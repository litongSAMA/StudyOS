from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4, uuid5, NAMESPACE_URL
import hashlib
import json
import sqlite3

from .common import AgentError, canonical, digest, identifier, normalize, number, text, utc
from .database import Database

TASK_KINDS = {"homework", "exam", "report", "lab", "review", "other"}
MATERIAL_TYPES = {"courseware", "homework", "lab", "exam", "notes", "handout", "report", "other"}


class AgentService:
    def __init__(self, db_path, *, bootstrap=False, file_root=None, clock=None, timezone_name="Asia/Shanghai", database=None, change_hook=None, search=None, answer=None, file_text=None):
        self.db = database or Database(db_path, bootstrap=bootstrap)
        self.change_hook = change_hook
        self.search = search
        self.answer = answer
        self.file_text = file_text
        self.file_root = Path(file_root).resolve() if file_root else None
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.timezone_name = timezone_name

    def now(self):
        return self.clock().astimezone(timezone.utc).isoformat(timespec="microseconds")

    @staticmethod
    def scope(value):
        return text(value, "授权数据范围", 128, True)

    @staticmethod
    def key(value):
        import re
        value = text(value, "幂等键", 128, True)
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
            raise AgentError(422, "幂等键只能包含字母、数字、点、冒号、下划线和连字符")
        return value

    def _course(self, con, name, changes, now):
        name = text(name or "", "课程", 120)
        if not name:
            return None
        for row in con.execute("SELECT id,name FROM courses"):
            if normalize(row["name"]) == normalize(name):
                return row["id"]
        row_id = self.db.write(con, "courses", dict(name=name, code="", semester="", start_date=None, color="#6366f1", teacher="", location="", slots="[]", sync_uuid=str(uuid4()), created_at=now, updated_at=now))
        changes.append(self.db.change("courses", None, self.db.row(con, "courses", row_id)))
        return row_id

    def _file_hash(self, payload):
        raw = payload.get("file_path") or ""
        if not raw:
            return ""
        if not self.file_root:
            raise AgentError(422, "附件目录尚未配置，不能确认文件入库")
        path = Path(raw)
        path = (path if path.is_absolute() else self.file_root / path).resolve()
        if not path.is_relative_to(self.file_root) or not path.is_file():
            raise AgentError(422, "附件路径无效")
        if path.stat().st_size > 32 * 1024 * 1024:
            raise AgentError(413, "附件超过 32 MB")
        checksum = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                checksum.update(chunk)
        return checksum.hexdigest()

    @staticmethod
    def _stored_time(value):
        # Historical schema stores UTC datetimes without a suffix.
        return utc(value if value.endswith("Z") or "+" in value[10:] or "-" in value[10:] else value + "+00:00")

    def apply(self, payload, *, request_key, scope="local"):
        scope, key = self.scope(scope), self.key(request_key)
        if not isinstance(payload, dict):
            raise AgentError(422, "入库内容必须是对象")
        tasks, materials = payload.get("tasks", []), payload.get("materials", [])
        if not isinstance(tasks, list) or not isinstance(materials, list) or not 1 <= len(tasks) + len(materials) <= 100:
            raise AgentError(422, "每批入库需要 1 到 100 项建议")
        text(payload.get("file_path", ""), "附件路径", 600)
        text(payload.get("file_name", ""), "附件名", 300)
        source = text(payload.get("source", "text"), "入库来源", 20)
        if source not in {"text", "file", "image"}:
            raise AgentError(422, "入库来源无效")
        try:
            if len(canonical(payload).encode()) > 2 * 1024 * 1024:
                raise AgentError(413, "请求超过 2 MB")
            request_hash = digest(payload)
        except (ValueError, TypeError):
            raise AgentError(422, "入库内容必须是有效的 JSON 数据") from None
        with self.db.transaction() as con:
            replay = self.db.replay(con, scope, key, request_hash)
            if replay is not None:
                return replay
            from .deadlines import CHANGE_MARKER
            notice = text(payload.get("text", ""), "通知原文", 500_000)
            if tasks and (CHANGE_MARKER.search(notice) or any(isinstance(item, dict) and isinstance(item.get("source_text"), str) and CHANGE_MARKER.search(item["source_text"]) for item in tasks)):
                raise AgentError(409, "识别到截止日期变更通知，请通过对话助手选择原任务并预览更新，避免创建重复任务")
            checksum = self._file_hash(payload)
            full_file_text = self.file_text(payload) if checksum and self.file_text else None
            now, changes = self.now(), []
            created_tasks, created_materials, reused_tasks, reused_materials, matched_courses = [], [], [], [], {}
            associated_material_ids = []
            for item_index, item in enumerate(materials):
                if not isinstance(item, dict):
                    raise AgentError(422, "资料建议格式无效")
                title = text(item.get("title"), "资料标题", required=True)
                category = item.get("type", "other")
                if not isinstance(category, str) or category not in MATERIAL_TYPES:
                    raise AgentError(422, "资料类型无效")
                course_id = self._course(con, item.get("course", ""), changes, now)
                if course_id:
                    matched_courses[item["course"]] = course_id
                chapter = text(item.get("chapter", ""), "章节", 120)
                difficulty = item.get("difficulty", "")
                if not isinstance(difficulty, str) or difficulty not in {"", "easy", "medium", "hard"}:
                    raise AgentError(422, "难度无效")
                tags = item.get("tags", [])
                if not isinstance(tags, list) or len(tags) > 100 or any(not isinstance(tag, str) or len(tag) > 100 for tag in tags):
                    raise AgentError(422, "标签格式无效")
                content = text(item.get("text", full_file_text if full_file_text else payload.get("text", item.get("summary", ""))), "资料文本", 500_000)
                confidence = number(item.get("confidence", 1), "置信度", 0, 1)
                if not isinstance(item.get("exam_focus", False), bool):
                    raise AgentError(422, "考试重点标记必须是布尔值")
                existing = None
                for row in con.execute("SELECT * FROM materials WHERE course_id IS ? ORDER BY id", (course_id,)):
                    same_file = checksum and row["file_hash"] == checksum and normalize(row["chapter"]) == normalize(chapter)
                    same_text = not checksum and not row["file_hash"] and normalize(row["title"]) == normalize(title) and row["type"] == category and normalize(row["chapter"]) == normalize(chapter) and normalize(row["text"]) == normalize(content)
                    if same_file or same_text:
                        existing = row["id"]
                        break
                if existing:
                    reused_materials.append(self.db.public(con, "materials", existing))
                    associated_material_ids.append(existing)
                    continue
                row_id = self.db.write(con, "materials", dict(course_id=course_id, title=title, type=category, chapter=chapter, difficulty=difficulty, exam_focus=bool(item.get("exam_focus", False)), file_path=payload.get("file_path", ""), file_name=payload.get("file_name", ""), file_hash=checksum, source=payload.get("source", "text"), text=content, tags=canonical(list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))), confidence=confidence, created_at=now, updated_at=now, sync_uuid=str(uuid5(NAMESPACE_URL, canonical(["studyos-agent-ingest",scope,key,"material",item_index])))))
                changes.append(self.db.change("materials", None, self.db.row(con, "materials", row_id)))
                created_materials.append(self.db.public(con, "materials", row_id))
                associated_material_ids.append(row_id)
            for item_index, item in enumerate(tasks):
                if not isinstance(item, dict):
                    raise AgentError(422, "任务建议格式无效")
                title = text(item.get("title"), "任务标题", required=True)
                category = item.get("kind", "other")
                if not isinstance(category, str) or category not in TASK_KINDS:
                    raise AgentError(422, "任务类型无效")
                course_id = self._course(con, item.get("course", ""), changes, now)
                if course_id:
                    matched_courses[item["course"]] = course_id
                due = utc(item.get("due_at"))
                confidence = number(item.get("confidence", 1), "置信度", 0, 1)
                existing = None
                for row in con.execute("SELECT id,title,kind,due_at FROM tasks WHERE course_id IS ? ORDER BY id", (course_id,)):
                    if normalize(row["title"]) == normalize(title) and row["kind"] == category and self._stored_time(row["due_at"]) == due:
                        existing = row["id"]
                        break
                if existing:
                    reused_tasks.append(self.db.public(con, "tasks", existing))
                    continue
                offsets = item.get("remind_offsets_h", [72, 24, 2])
                if not isinstance(offsets, list) or len(offsets) > 20:
                    raise AgentError(422, "提醒设置无效")
                for offset in offsets:
                    number(offset, "提醒提前小时数", 0, 8760)
                row_id = self.db.write(con, "tasks", dict(course_id=course_id, title=title, kind=category, due_at=due, done=False, remind_offsets_h=canonical(sorted(set(offsets), reverse=True)), note=text(item.get("note", ""), "备注", 10000), source_text=text(item.get("source_text", ""), "来源", 500_000), source_material_id=associated_material_ids[0] if associated_material_ids else None, confidence=confidence, created_at=now, updated_at=now, sync_uuid=str(uuid5(NAMESPACE_URL, canonical(["studyos-agent-ingest",scope,key,"task",item_index])))))
                changes.append(self.db.change("tasks", None, self.db.row(con, "tasks", row_id)))
                created_tasks.append(self.db.public(con, "tasks", row_id))
            if self.change_hook:
                self.change_hook(con, changes)
            return self.db.operation(con, str(uuid4()), scope, key, request_hash, "ingest", changes, dict(created_tasks=created_tasks, created_materials=created_materials, reused_tasks=reused_tasks, reused_materials=reused_materials, matched_courses=matched_courses), now)

    def operations(self, *, scope="local", limit=50):
        scope = self.scope(scope)
        identifier(limit, "limit")
        if limit > 100:
            raise AgentError(422, "最多读取 100 条记录")
        with self.db.snapshot() as con:
            rows = con.execute("SELECT id,kind,created_at,undone_at,response FROM agent_operations WHERE scope=? ORDER BY created_at DESC,id DESC LIMIT ?", (scope, limit))
            return [{**dict(row), "response": json.loads(row["response"])} for row in rows]

    def undo(self, operation_id, *, request_key, scope="local"):
        scope, key = self.scope(scope), self.key(request_key)
        request_hash = digest({"undo": operation_id})
        with self.db.transaction() as con:
            replay = self.db.replay(con, scope, key, request_hash)
            if replay:
                return replay
            record = con.execute("SELECT * FROM agent_operations WHERE id=? AND scope=?", (operation_id, scope)).fetchone()
            if not record:
                raise AgentError(404, "操作不存在")
            if record["kind"] == "undo":
                raise AgentError(409, "撤销记录不能再次撤销")
            if record["undone_at"]:
                raise AgentError(409, "操作已撤销")
            changes = json.loads(record["changes"])
            for guard in json.loads(record["guards"]):
                if self._dependency_digest(con, guard["kind"], guard["id"]) != guard["digest"]:
                    raise AgentError(409, "提醒或学习进度已有后续变化，无法安全撤销")
            for change in changes:
                if self.db.row(con, change["table"], change["id"]) != change["after"]:
                    raise AgentError(409, "对象已被后续编辑，无法安全撤销；请先处理后续操作")
                focus_ref = {"tasks":"task_id","courses":"course_id","review_plans":"plan_id"}.get(change["table"])
                if change["before"] is None and focus_ref and con.execute(f"SELECT 1 FROM focus_sessions WHERE {focus_ref}=?", (change["id"],)).fetchone():
                    raise AgentError(409, "对象已有专注记录，无法安全撤销")
                if change["table"] == "tasks" and change["before"] is None and con.execute("SELECT 1 FROM push_deliveries WHERE task_id=?", (change["id"],)).fetchone():
                    raise AgentError(409, "该任务已发送提醒，不能直接撤销创建")
            now = self.now()
            reversed_changes = []
            try:
                for change in reversed(changes):
                    table, row_id, before = change["table"], change["id"], change["before"]
                    after = self.db.row(con, table, row_id)
                    if before is None:
                        con.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))
                        if "sync_uuid" in after:
                            con.execute("INSERT OR REPLACE INTO tombstones (sync_uuid,entity,deleted_at) VALUES (?,?,?)", (after["sync_uuid"], self.db.entity_name(table), self.db.storage_time(now) if hasattr(self.db, "storage_time") else now))
                    else:
                        if "updated_at" in before:
                            before = {**before, "updated_at": now}
                        if after is None:
                            self.db.write(con, table, before)
                        else:
                            self.db.write(con, table, before, row_id)
                    reversed_changes.append(self.db.change(table, after, self.db.row(con, table, row_id)))
            except Exception as error:
                import sqlite3
                if isinstance(error, sqlite3.IntegrityError):
                    raise AgentError(409, "对象已被其他学习记录引用，无法安全撤销") from None
                raise
            if self.change_hook:
                self.change_hook(con, reversed_changes)
            con.execute("UPDATE agent_operations SET undone_at=? WHERE id=?", (now, operation_id))
            return self.db.operation(con, str(uuid4()), scope, key, request_hash, "undo", reversed_changes, {"undone_operation_id": operation_id, "restored_count": len(changes)}, now)

    def save_plan(self, intent, proposal, *, scope="local"):
        scope = self.scope(scope)
        plan_id, now = str(uuid4()), self.now()
        expires = (self.clock() + timedelta(minutes=30)).astimezone(timezone.utc).isoformat(timespec="microseconds")
        with self.db.transaction() as con:
            con.execute("INSERT INTO agent_plans (id,scope,intent,proposal,expires_at,created_at) VALUES (?,?,?,?,?,?)", (plan_id, scope, intent, canonical(proposal), expires, now))
        return {"plan_id": plan_id, "intent": intent, "expires_at": expires, **proposal}

    def _load_plan(self, con, plan_id, scope):
        row = con.execute("SELECT * FROM agent_plans WHERE id=? AND scope=?", (plan_id, scope)).fetchone()
        if not row:
            raise AgentError(404, "建议不存在")
        if row["operation_id"]:
            raise AgentError(409, "建议已执行，请读取原执行回执")
        if row["expires_at"] <= self.now():
            raise AgentError(409, "建议已过期，请重新生成")
        return row, json.loads(row["proposal"])

    def execute_plan(self, plan_id, *, request_key, scope="local"):
        scope, key = self.scope(scope), self.key(request_key)
        request_hash = digest({"plan_id": plan_id})
        with self.db.transaction() as con:
            replay = self.db.replay(con, scope, key, request_hash)
            if replay:
                return replay
            record, proposal = self._load_plan(con, plan_id, scope)
            if proposal.get("blocked_reason"):
                raise AgentError(422, proposal["blocked_reason"])
            for check in proposal.get("checks", []):
                if check.get("kind") == "absent":
                    # 新建类动作的并发保护：预览时该对象不存在，执行时必须仍然不存在
                    # （两个预览都判定「新建」同一门课时，后执行的要重新生成而不是插重复行）
                    if self._find_by_name(con, check["table"], check["name"]) is not None:
                        raise AgentError(409, "建议生成后学习数据已变化，请重新生成")
                    continue
                if self.db.row(con, check["table"], check["id"]) != check["before"]:
                    raise AgentError(409, "建议生成后学习数据已变化，请重新生成")
            for query_check in proposal.get("dependencies", []):
                if self._dependency_digest(con, query_check["kind"], query_check["id"]) != query_check["digest"]:
                    raise AgentError(409, "学习进度或提醒记录已变化，请重新生成建议")
            changes, now = [], self.now()
            try:
                for action in proposal.get("actions", []):
                    table = action["table"]
                    before = self.db.row(con, table, action["id"]) if action.get("id") else None
                    if action["op"] == "delete":
                        con.execute(f"DELETE FROM {table} WHERE id=?", (action["id"],))
                        changes.append(self.db.change(table, before, None))
                    else:
                        fields = {**action["values"]}
                        if "updated_at" in fields:
                            fields["updated_at"] = now
                        row_id = self.db.write(con, table, fields, action.get("id"))
                        changes.append(self.db.change(table, before, self.db.row(con, table, row_id)))
            except sqlite3.IntegrityError:
                # 唯一索引/外键冲突（典型：两个预览都判定「新建」同一门课，先后执行）：
                # 语义上是「建议生成后数据已变化」，给 409 让客户端重新生成，
                # 而不是以 500 冒出去——事务已被回滚，不会留下半截数据。
                raise AgentError(409, "建议生成后学习数据已变化，请重新生成") from None
            operation_id = str(uuid4())
            followups = []
            if record["intent"] == "deadline_change":
                changed_task_id = proposal["result"]["task_id"]
                for plan in con.execute("SELECT id,title,exam_task_ids FROM review_plans"):
                    if changed_task_id in json.loads(plan["exam_task_ids"]):
                        followups.append({"intent": "review_replan", "review_plan_id": plan["id"], "title": plan["title"], "message": "截止日期已变化，建议预览此计划的关联重排"})
            if self.change_hook:
                self.change_hook(con, changes)
            guards = [{"kind": check["kind"], "id": check["id"], "digest": self._dependency_digest(con, check["kind"], check["id"])} for check in proposal.get("dependencies", [])]
            response = self.db.operation(con, operation_id, scope, key, request_hash, record["intent"], changes, {"plan_id": plan_id, "summary": proposal.get("summary", ""), "result": proposal.get("result", {}), "followups": followups, "changed_count": len(changes), "changed": [{"entity": change["table"], "id": change["id"]} for change in changes]}, now, guards=guards)
            con.execute("UPDATE agent_plans SET operation_id=? WHERE id=?", (operation_id, plan_id))
            return response

    @staticmethod
    def _find_by_name(con, table, name):
        """按归一化名称查一行（与 `_course` 的同名合并口径一致）。"""
        for row in con.execute(f"SELECT id,name FROM {table}"):
            if normalize(row["name"]) == normalize(name):
                return row["id"]
        return None

    @staticmethod
    def _dependency_digest(con, kind, row_id):
        if kind == "deliveries":
            rows = con.execute("SELECT * FROM push_deliveries WHERE task_id=? ORDER BY id", (row_id,)).fetchall()
        elif kind == "progress":
            rows = [*con.execute("SELECT * FROM material_reviews ORDER BY id"), *con.execute("SELECT * FROM focus_sessions ORDER BY id"), *con.execute("SELECT * FROM review_plans ORDER BY id"), *con.execute("SELECT * FROM courses ORDER BY id")]
        else:
            raise ValueError("unknown dependency")
        return digest([dict(row) for row in rows])
