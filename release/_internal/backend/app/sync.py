"""多端同步引擎：本地优先、点对点、无中心服务器。

设计：
1. 每台设备是一个完整的 StudyOS 实例（SQLite + 文件目录）。
2. 每行数据带全局唯一 sync_uuid 与 updated_at；删除留下墓碑(Tombstone)。
3. 同步 = 交换快照：本端把「课程/资料/任务/复习计划/设置/墓碑」全量快照
   发给对端，对端按 **Last-Write-Wins（时间戳）** 合并后回传它的快照，
   本端再合并一次 → 一轮往返两端收敛。
4. 快照内引用一律用 sync_uuid（本地自增 id 各端不同，导入时按 uuid 重映射）。
5. 文件以 base64 随快照传输（超上限跳过），文本检索与文件在两端都可用。
6. 备份 = 快照 + 原始文件打包 zip，恢复 = 全量替换（可搬新设备）。

安全：同步接口必须携带设备令牌（X-Sync-Token），令牌只存在本机数据库。
令牌本身等同于「本机数据的完整读写权限」，因此**不会**在 /api/sync/status 这类被动查询里
明文返回——只回掩码 + 指纹；配对需要明文时，由用户在本机页面上显式点击「显示令牌」
（POST /api/sync/token/reveal，默认仅 loopback 可调用）。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import secrets
import uuid as uuid_mod
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from .agent.common import normalize_difficulty
from .config import settings
from .db import (
    MATERIAL_ARCHIVE_BACKFILL_MARKER,
    UTC_MIGRATION_MARKER,
    fts_sync_add,
    fts_sync_delete,
    fts_sync_update,
)
from .filestore import safe_unlink
from .models import (
    AppSetting, Course, Material, MaterialReview, Note, ReviewPlan, Task, Tombstone,
    WrongQuestion, FocusSession, PushDelivery, Quiz,
)
from .semantic import invalidate_material
from .timeutil import iso_utc, parse_iso, utcnow

SNAPSHOT_VERSION = 2  # v2：快照内所有时间戳带 UTC 时区后缀（Z）
MAX_FILE_BYTES = 32 * 1024 * 1024  # 单文件同步上限 32MB，超限跳过（保留文本）
EXCHANGE_TIMEOUT = 180.0
MAX_BACKUP_BYTES = 2 * 1024 * 1024 * 1024   # 备份 zip 上传上限 2GB
MAX_BACKUP_ENTRIES = 100_000               # 备份 zip 条目数上限
MAX_MANIFEST_BYTES = 10 * 1024 * 1024      # manifest.json 大小上限

# 本机专属设置：不同步、不备份（令牌/远端地址/设备 id 各端自己持有；
# UTC 迁移标记与归档回填标记都是**每台设备自己数据库**的一次性开关，
# 同步过去会让对端误跳过迁移/回填）
LOCAL_SETTING_KEYS = (
    "sync_token", "sync_device_id", "sync_remote_url", "sync_remote_token",
    UTC_MIGRATION_MARKER,
    MATERIAL_ARCHIVE_BACKFILL_MARKER,
)

ENTITY_MODELS = {
    "course": Course, "material": Material, "task": Task, "review": ReviewPlan,
    "note": Note, "materialreview": MaterialReview, "wrongquestion": WrongQuestion, "focussession": FocusSession, "quiz": Quiz,
}

# 复习计划 session 内引用资料 id 的字段 → 快照里的 uuid 字段（跨端重映射）
SESSION_ID_FIELDS = (
    ("material_ids", "material_uuids"),
    ("study_ids", "study_uuids"),
    ("due_ids", "due_uuids"),
    ("spaced_ids", "spaced_uuids"),
)


class SyncError(Exception):
    """同步/恢复失败（含远端错误），信息面向用户。"""


def _iso(dt: datetime | None) -> str | None:
    """存储为 naive UTC → 输出带 `Z` 的 UTC ISO（v2 快照，跨时区可直接比较）。"""
    return iso_utc(dt)


# 非法时间戳的兜底值：远古时间永远输掉 LWW，绝不因解析失败而「now() 胜出」
_EPOCH = datetime(1970, 1, 1)


def _parse_dt(s) -> datetime:
    """快照时间 → naive UTC。带时区按偏移换算；无后缀视为 UTC。

    v1 旧快照的无后缀时间原为「对端本地时间」，无法还原其原始时区，
    按 UTC 解释为 best-effort——两端都升级到 v2 后即不再出现。
    """
    if isinstance(s, datetime):
        if s.tzinfo is not None:
            return s.astimezone(timezone.utc).replace(tzinfo=None)
        return s
    try:
        return parse_iso(str(s))
    except (ValueError, TypeError):
        return _EPOCH


# ---------------------------------------------------------------------------
# 设置与令牌
# ---------------------------------------------------------------------------


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(AppSetting, key)
    return row.value if row and row.value is not None else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))


def get_device_id(db: Session) -> str:
    did = get_setting(db, "sync_device_id")
    if not did:
        did = uuid_mod.uuid4().hex[:12]
        set_setting(db, "sync_device_id", did)
        db.commit()
    return did


def get_sync_token(db: Session) -> str:
    """设备令牌：环境变量 SYNC_TOKEN 可覆盖（部署场景），否则自动生成。"""
    env = os.environ.get("SYNC_TOKEN", "").strip()
    if env:
        return env
    tok = get_setting(db, "sync_token")
    if not tok:
        tok = secrets.token_urlsafe(24)
        set_setting(db, "sync_token", tok)
        db.commit()
    return tok


def token_ok(db: Session, token: str | None) -> bool:
    if not token:
        return False
    return secrets.compare_digest(get_sync_token(db), token)


# ---- 令牌披露：默认只回掩码与指纹，明文需显式调用 reveal 接口 ----------------


def mask_token(token: str) -> str:
    """令牌掩码：只露末 4 位，用于「这是哪一个令牌」的辨识，不足以配对。"""
    token = (token or "").strip()
    if not token:
        return ""
    if len(token) <= 4:
        return "•" * len(token)
    return f"{'•' * 8}{token[-4:]}"


def token_fingerprint(token: str) -> str:
    """令牌指纹（sha256 前 8 位）：两台设备可核对「是不是同一个令牌」，且不可反推明文。"""
    token = (token or "").strip()
    if not token:
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


def token_source(db: Session | None = None) -> str:
    """令牌来源：env（SYNC_TOKEN 环境变量固定）/ device（本机数据库随机生成）。"""
    return "env" if os.environ.get("SYNC_TOKEN", "").strip() else "device"


def token_public_info(db: Session) -> dict:
    """对外可安全返回的令牌信息（不含明文）。"""
    tok = get_sync_token(db)
    return {
        "token_masked": mask_token(tok),
        "token_fingerprint": token_fingerprint(tok),
        "token_length": len(tok),
        "token_source": token_source(),
    }


# ---------------------------------------------------------------------------
# 快照构建（本端 → dict）
# ---------------------------------------------------------------------------


def _material_file_payload(m: Material, include_files: bool) -> dict:
    item: dict = {"file_name": m.file_name or ""}
    if include_files and m.file_path:
        p = Path(m.file_path)
        try:
            if p.exists() and p.is_file():
                size = p.stat().st_size
                if size <= MAX_FILE_BYTES:
                    item["file_b64"] = base64.b64encode(p.read_bytes()).decode()
                    item["file_size"] = size
                elif m.file_name:
                    item["file_skipped"] = True
                    item["file_size"] = size
        except OSError:
            pass
    elif include_files and m.file_name:
        item["file_skipped"] = True
    return item


def build_snapshot(db: Session, include_files: bool = True, include_local_ids: bool = False) -> dict:
    """本端全量快照。include_local_ids 仅用于备份（恢复时保持原 id）。"""
    courses = db.execute(select(Course)).scalars().all()
    materials = db.execute(select(Material)).scalars().all()
    tasks = db.execute(select(Task)).scalars().all()
    reviews = db.execute(select(ReviewPlan)).scalars().all()
    notes = db.execute(select(Note)).scalars().all()
    material_reviews = db.execute(select(MaterialReview)).scalars().all()
    wrong_questions = db.execute(select(WrongQuestion)).scalars().all()

    course_uuid_by_id = {c.id: c.sync_uuid for c in courses}
    mat_uuid_by_id = {m.id: m.sync_uuid for m in materials}
    task_uuid_by_id = {t.id: t.sync_uuid for t in tasks}

    snap: dict = {
        "version": SNAPSHOT_VERSION,
        "generated_at": _iso(utcnow()),
        "device_id": get_device_id(db),
        "courses": [],
        "materials": [],
        "tasks": [],
        "reviews": [],
        "notes": [],
        "material_reviews": [],
        "wrong_questions": [],
        "settings": [],
        "tombstones": [],
    }
    from .quiz_sync import export_quizzes
    snap["quizzes"] = export_quizzes(db, include_local_ids)
    from .focus_sync import export_focus
    snap["focus_sessions"] = export_focus(db, include_local_ids)
    from .agent_receipts import export_receipts
    snap["agent_receipts"] = export_receipts(db)
    for c in courses:
        item = {
            "sync_uuid": c.sync_uuid,
            "updated_at": _iso(c.updated_at),
            "created_at": _iso(c.created_at),
            "name": c.name, "code": c.code, "semester": c.semester,
            "start_date": c.start_date, "color": c.color,
            "teacher": c.teacher or "", "location": c.location or "",
            "slots": c.slots or [],
        }
        if include_local_ids:
            item["id"] = c.id
        snap["courses"].append(item)

    for m in materials:
        item = {
            "sync_uuid": m.sync_uuid,
            "updated_at": _iso(m.updated_at),
            "created_at": _iso(m.created_at),
            "course_uuid": course_uuid_by_id.get(m.course_id or 0),
            "title": m.title, "type": m.type,
            "chapter": m.chapter or "", "difficulty": m.difficulty or "",
            "exam_focus": bool(m.exam_focus),
            "file_hash": m.file_hash or "",
            "source": m.source, "text": m.text or "",
            "tags": m.tags or [], "confidence": m.confidence,
            **_material_file_payload(m, include_files),
        }
        if include_local_ids:
            item["id"] = m.id
        snap["materials"].append(item)

    for t in tasks:
        item = {
            "sync_uuid": t.sync_uuid,
            "updated_at": _iso(t.updated_at),
            "created_at": _iso(t.created_at),
            "course_uuid": course_uuid_by_id.get(t.course_id or 0),
            "title": t.title, "kind": t.kind, "due_at": _iso(t.due_at),
            "done": t.done, "remind_offsets_h": t.remind_offsets_h or [],
            "note": t.note or "", "source_text": t.source_text or "",
            "source_material_uuid": mat_uuid_by_id.get(t.source_material_id or 0),
            "confidence": t.confidence,
        }
        if include_local_ids:
            item["id"] = t.id
        snap["tasks"].append(item)

    for r in reviews:
        sessions = []
        for s in r.sessions or []:
            ns = dict(s)
            if "course_id" in ns:
                ns["course_uuid"] = course_uuid_by_id.get(ns.pop("course_id"))
            for src, dst in SESSION_ID_FIELDS:
                if src in ns:
                    ns[dst] = [mat_uuid_by_id.get(i, "") for i in ns.pop(src)]
            sessions.append(ns)
        item = {
            "sync_uuid": r.sync_uuid,
            "updated_at": _iso(r.updated_at),
            "created_at": _iso(r.created_at),
            "title": r.title,
            "exam_task_uuids": [task_uuid_by_id.get(i, "") for i in r.exam_task_ids or []],
            "sessions": sessions,
        }
        if include_local_ids:
            item["id"] = r.id
        snap["reviews"].append(item)

    for nt in notes:
        item = {
            "sync_uuid": nt.sync_uuid,
            "updated_at": _iso(nt.updated_at),
            "created_at": _iso(nt.created_at),
            "material_uuid": mat_uuid_by_id.get(nt.material_id or 0),
            "title": nt.title, "mode": nt.mode or "keypoints",
            "summary": nt.summary or "", "points": nt.points or [],
            "engine": nt.engine or "rule",
        }
        if include_local_ids:
            item["id"] = nt.id
        snap["notes"].append(item)

    for mr in material_reviews:
        item = {
            "sync_uuid": mr.sync_uuid,
            "updated_at": _iso(mr.updated_at),
            "created_at": _iso(mr.created_at),
            "material_uuid": mat_uuid_by_id.get(mr.material_id or 0),
            "mastery": mr.mastery or 0.0,
            "repetitions": mr.repetitions or 0,
            "lapses": mr.lapses or 0,
            "interval_days": mr.interval_days or 0.0,
            "ease": mr.ease if mr.ease is not None else 2.5,
            "due_at": _iso(mr.due_at),
            "last_reviewed_at": _iso(mr.last_reviewed_at),
            "last_rating": mr.last_rating or "",
            "history": mr.history or [],
        }
        if include_local_ids:
            item["id"] = mr.id
        snap["material_reviews"].append(item)

    for wq in wrong_questions:
        item = {
            "sync_uuid": wq.sync_uuid,
            "updated_at": _iso(wq.updated_at),
            "created_at": _iso(wq.created_at),
            "course_uuid": course_uuid_by_id.get(wq.course_id or 0),
            "title": wq.title,
            "question": wq.question or "",
            "my_answer": wq.my_answer or "",
            "answer": wq.answer or "",
            "reason": wq.reason or "",
            "source": wq.source or "",
            "chapter": wq.chapter or "",
            "difficulty": wq.difficulty or "",
            "tags": wq.tags or [],
            "status": wq.status or "open",
            "review_count": wq.review_count or 0,
            "wrong_count": wq.wrong_count if wq.wrong_count is not None else 1,
            "streak": wq.streak or 0,
            "last_result": wq.last_result or "",
            "last_reviewed_at": _iso(wq.last_reviewed_at),
        }
        if include_local_ids:
            item["id"] = wq.id
        snap["wrong_questions"].append(item)

    for s in db.execute(select(AppSetting)).scalars().all():
        if s.key in LOCAL_SETTING_KEYS or s.key.startswith('focus_legacy_imported_'):
            continue
        snap["settings"].append({
            "key": s.key, "value": s.value or "",
            "updated_at": _iso(s.updated_at),
        })

    for t in db.execute(select(Tombstone)).scalars().all():
        snap["tombstones"].append({
            "entity": t.entity, "sync_uuid": t.sync_uuid,
            "deleted_at": _iso(t.deleted_at),
        })
    return snap


# ---------------------------------------------------------------------------
# 快照合并（dict → 本端），LWW 冲突解决
# ---------------------------------------------------------------------------

_EMPTY_STATS = {
    "created": 0, "updated": 0, "deleted": 0, "skipped": 0,
    "files_written": 0, "files_skipped": 0, "settings_updated": 0,
}


def _write_material_file(item: dict, db=None) -> str | None:
    """快照文件 base64 → 本端文件，返回新 file_path；失败返回 None。"""
    raw = base64.b64decode(item["file_b64"])
    if not raw or len(raw) > MAX_FILE_BYTES:
        return None
    suffix = Path(item.get("file_name") or "").suffix.lower() or ""
    store_dir = settings.data_dir / "files"
    store_dir.mkdir(parents=True, exist_ok=True)
    p = store_dir / f"{uuid_mod.uuid4().hex}{suffix}"
    p.write_bytes(raw)
    if db is not None: db.info.setdefault("sync_new_files",[]).append(str(p))
    return str(p)


def _delete_material_file(path: str | None, db=None) -> None:
    # 只删除 data/files 内的附件，防误删任意路径
    if db is not None: db.info.setdefault("sync_delete_files",[]).append(path)
    else: safe_unlink(path)


def apply_snapshot(db: Session, snap: dict, mode: str = "merge") -> dict:
    """把远端快照合并进本端。

    mode="merge"：按 sync_uuid 匹配 + updated_at 最后写入胜出；
    mode="replace"：先清空本端全部数据，再整体导入（用于备份恢复）。
    """
    if not isinstance(snap, dict) or "version" not in snap:
        raise SyncError("快照格式无效（缺少 version 字段）")
    stats = dict(_EMPTY_STATS)
    if mode not in ("merge", "replace"):
        raise SyncError(f"未知合并模式: {mode}")
    was_syncing = db.info.get("syncing")
    db.info["syncing"] = True
    db.info["sync_new_files"]=[];db.info["sync_delete_files"]=[]
    committed=False
    try:
        if mode == "replace":
            _wipe_all(db)
        _merge(db, snap, stats)
        db.commit()
        committed=True
        for path in db.info['sync_delete_files']: safe_unlink(path)
        # 设置合并可能带入了 AI 配置（llm_*/embed_*）→ 重载运行态立即生效
        if mode == "replace" or stats["settings_updated"]:
            from .config import reload_ai_from_db

            reload_ai_from_db(db)
    except SyncError:
        db.rollback()
        raise
    except Exception as e:  # 任何异常都不得留下半合并状态
        db.rollback()
        raise SyncError(f"合并快照失败: {e}") from e
    finally:
        if not committed:
            for path in db.info['sync_new_files']: safe_unlink(path)
        db.info.pop('sync_new_files',None);db.info.pop('sync_delete_files',None)
        db.info["syncing"] = was_syncing
    return stats


def _wipe_all(db: Session) -> None:
    """清空本端业务数据（保留同步令牌/远端配置等本机设置）。"""
    for m in db.execute(select(Material)).scalars().all():
        _delete_material_file(m.file_path,db)
        fts_sync_delete(db, m.id)
    from .models import FocusSession
    db.execute(delete(FocusSession))
    db.execute(delete(Quiz))
    for table in ["agent_turns","agent_conversations","agent_plans","agent_operations","agent_remote_receipts"]:
        if db.execute(text("SELECT 1 FROM sqlite_master WHERE name=:name"),{"name":table}).scalar():
            db.execute(text(f"DELETE FROM {table}"))
    db.execute(delete(ReviewPlan))
    db.execute(delete(Note))
    db.execute(delete(MaterialReview))
    db.execute(delete(WrongQuestion))
    db.execute(delete(Task))
    db.execute(delete(Material))
    db.execute(delete(Course))
    db.execute(delete(Tombstone))
    db.execute(delete(AppSetting).where(AppSetting.key.notin_(LOCAL_SETTING_KEYS)))
    db.execute(text("DELETE FROM materials_fts"))
    db.execute(text("DELETE FROM semantic_vectors"))
    db.flush()


def _merge(db: Session, snap: dict, stats: dict) -> None:
    tomb_map: dict[tuple[str, str], datetime] = {
        (t.entity, t.sync_uuid): t.deleted_at
        for t in db.execute(select(Tombstone)).scalars().all()
    }
    settings_map: dict[str, AppSetting] = {
        s.key: s for s in db.execute(select(AppSetting)).scalars().all()
    }

    # 1) 墓碑：合并进来的删除事件，并删除本端更旧的实体
    for t in snap.get("tombstones", []):
        if not isinstance(t, dict) or not t.get("sync_uuid"):
            continue
        entity, suid = str(t.get("entity", "")), str(t["sync_uuid"])
        deleted_at = _parse_dt(t.get("deleted_at"))
        local_dt = tomb_map.get((entity, suid))
        if local_dt is not None:
            if local_dt >= deleted_at:
                continue
            row = db.get(Tombstone, {"sync_uuid": suid, "entity": entity})
            if row:
                row.deleted_at = deleted_at
        else:
            db.add(Tombstone(sync_uuid=suid, entity=entity, deleted_at=deleted_at))
            tomb_map[(entity, suid)] = deleted_at
        _delete_local_if_older(db, entity, suid, deleted_at, stats)

    # 2) 课程 → 3) 资料 → 4) 任务 → 5) 复习计划 → 6) 笔记 → 7) 复习状态 → 8) 错题 → 9) 设置
    course_ids = _merge_courses(db, snap.get("courses", []), tomb_map, stats)
    mat_ids = _merge_materials(db, snap.get("materials", []), tomb_map, course_ids, stats)
    _merge_tasks(db, snap.get("tasks", []), tomb_map, course_ids, mat_ids, stats)
    _merge_reviews(db, snap.get("reviews", []), tomb_map, mat_ids, stats)
    _merge_notes(db, snap.get("notes", []), tomb_map, mat_ids, stats)
    _merge_material_reviews(db, snap.get("material_reviews", []), tomb_map, mat_ids, stats)
    _merge_wrong_questions(db, snap.get("wrong_questions", []), tomb_map, course_ids, stats)
    from .quiz_sync import merge_quizzes
    merge_quizzes(db, snap.get("quizzes", []), tomb_map, stats)
    from .focus_sync import merge_focus
    merge_focus(db, snap.get("focus_sessions", []), tomb_map, stats)
    from .agent_receipts import merge_receipts
    merge_receipts(db, snap.get("agent_receipts", []))

    incoming_settings: dict[str, dict] = {}
    for s in snap.get("settings", []):
        if isinstance(s, dict) and s.get("key") and s["key"] not in LOCAL_SETTING_KEYS and not str(s["key"]).startswith("focus_legacy_imported_"):
            incoming_settings[str(s["key"])] = s
    for key, s in incoming_settings.items():
        value = str(s.get("value", ""))
        updated_at = _parse_dt(s.get("updated_at"))
        local = settings_map.get(key)
        if local is not None:
            if (local.updated_at or datetime.min) >= updated_at:
                continue
            local.value = value
            local.updated_at = updated_at
        else:
            db.add(AppSetting(key=key, value=value, updated_at=updated_at))
        stats["settings_updated"] += 1


def _tombstone_newer(tomb_map: dict, entity: str, suid: str, updated_at: datetime) -> bool:
    dt = tomb_map.get((entity, suid))
    return dt is not None and dt >= updated_at


def _delete_local_if_older(db: Session, entity: str, suid: str, deleted_at: datetime, stats: dict) -> None:
    """本端存在该 uuid 且最后修改早于远端删除时间 → 删除本端实体。"""
    model = ENTITY_MODELS.get(entity)
    if model is None:
        return
    obj = db.execute(select(model).where(model.sync_uuid == suid)).scalar_one_or_none()
    if obj is None or (obj.updated_at or datetime.min) >= deleted_at:
        return
    if isinstance(obj, Material):
        _delete_material_file(obj.file_path,db)
        fts_sync_delete(db, obj.id)
        invalidate_material(db, obj.id)
        # 资料的间隔重复状态一并清理（避免孤儿状态被同步复活）
        for st in db.execute(
            select(MaterialReview).where(MaterialReview.material_id == obj.id)
        ).scalars().all():
            db.delete(st)
    if isinstance(obj, Task):
        db.execute(PushDelivery.__table__.delete().where(PushDelivery.task_id==obj.id))
    db.delete(obj)
    stats["deleted"] += 1


def _merge_courses(db: Session, items: list, tomb_map: dict, stats: dict) -> dict[str, int]:
    local = {c.sync_uuid: c for c in db.execute(select(Course)).scalars().all()}
    id_map: dict[str, int] = {c.sync_uuid: c.id for c in local.values()}
    for it in items:
        if not isinstance(it, dict) or not it.get("sync_uuid"):
            continue
        suid, updated_at = str(it["sync_uuid"]), _parse_dt(it.get("updated_at"))
        if _tombstone_newer(tomb_map, "course", suid, updated_at):
            stats["skipped"] += 1
            continue
        c = local.get(suid)
        name = str(it.get("name", c.name if c else ""))[:120] or "未命名课程"
        # 名称唯一约束：两端各自创建了同名课程（不同 uuid）时，保留本端、跳过远端
        clash = db.execute(
            select(Course).where(Course.name == name, Course.sync_uuid != suid)
        ).scalar_one_or_none()
        if clash is not None:
            stats["skipped"] += 1
            continue
        if c is not None:
            if (c.updated_at or datetime.min) >= updated_at:
                stats["skipped"] += 1
                continue
            stats["updated"] += 1
        else:
            c = Course(sync_uuid=suid, name=name)
            db.add(c)
            local[suid] = c
            stats["created"] += 1
        c.name = name
        c.code = str(it.get("code", ""))[:40]
        c.semester = str(it.get("semester", ""))[:40]
        c.start_date = it.get("start_date") or None
        c.color = str(it.get("color", "#6366f1"))[:16]
        c.teacher = str(it.get("teacher", ""))[:120]
        c.location = str(it.get("location", ""))[:200]
        slots = it.get("slots")
        c.slots = slots if isinstance(slots, list) else (c.slots or [])
        c.updated_at = updated_at
        if it.get("created_at"):
            c.created_at = _parse_dt(it["created_at"])
        db.flush()
        id_map[suid] = c.id
    return id_map


def _merge_materials(db: Session, items: list, tomb_map: dict, course_ids: dict, stats: dict) -> dict[str, int]:
    local = {m.sync_uuid: m for m in db.execute(select(Material)).scalars().all()}
    id_map: dict[str, int] = {m.sync_uuid: m.id for m in local.values()}
    touched: set[int] = set()
    for it in items:
        if not isinstance(it, dict) or not it.get("sync_uuid"):
            continue
        suid, updated_at = str(it["sync_uuid"]), _parse_dt(it.get("updated_at"))
        if _tombstone_newer(tomb_map, "material", suid, updated_at):
            stats["skipped"] += 1
            continue
        m = local.get(suid)
        is_new = m is None
        if m is not None:
            if (m.updated_at or datetime.min) >= updated_at:
                stats["skipped"] += 1
                continue
            stats["updated"] += 1
        else:
            m = Material(sync_uuid=suid, title=str(it.get("title", ""))[:300])
            db.add(m)
            local[suid] = m
            stats["created"] += 1
        m.title = str(it.get("title", m.title or ""))[:300]
        m.type = str(it.get("type", "other"))[:40]
        m.chapter = str(it.get("chapter", "") or "")[:120]
        m.difficulty = normalize_difficulty(str(it.get("difficulty", "") or ""))
        m.exam_focus = bool(it.get("exam_focus", False))
        m.source = str(it.get("source", "text"))[:20]
        m.text = str(it.get("text", ""))
        m.tags = [str(x) for x in (it.get("tags") or [])][:30]
        m.confidence = float(it.get("confidence", 1.0) or 1.0)
        m.course_id = course_ids.get(it.get("course_uuid") or "")
        m.file_name = str(it.get("file_name", "") or m.file_name or "")[:300]
        m.file_hash = str(it.get("file_hash", "") or "")[:64]
        if it.get("file_b64"):
            old_path = None if is_new else m.file_path
            new_path = _write_material_file(it,db)
            if new_path:
                m.file_path = new_path
                if old_path and old_path != new_path:
                    _delete_material_file(old_path,db)
                stats["files_written"] += 1
            else:
                stats["files_skipped"] += 1
        elif it.get("file_skipped"):
            stats["files_skipped"] += 1
        m.updated_at = updated_at
        if it.get("created_at"):
            m.created_at = _parse_dt(it["created_at"])
        db.flush()
        id_map[suid] = m.id
        touched.add(m.id)
    for mid in touched:
        m = db.get(Material, mid)
        if m:
            fts_sync_update(db, m)
            invalidate_material(db, mid)
    return id_map


def _merge_tasks(db: Session, items: list, tomb_map: dict, course_ids: dict, mat_ids: dict, stats: dict) -> None:
    local = {t.sync_uuid: t for t in db.execute(select(Task)).scalars().all()}
    for it in items:
        if not isinstance(it, dict) or not it.get("sync_uuid"):
            continue
        suid, updated_at = str(it["sync_uuid"]), _parse_dt(it.get("updated_at"))
        if _tombstone_newer(tomb_map, "task", suid, updated_at):
            stats["skipped"] += 1
            continue
        t = local.get(suid)
        if t is not None:
            if (t.updated_at or datetime.min) >= updated_at:
                stats["skipped"] += 1
                continue
            stats["updated"] += 1
        else:
            t = Task(sync_uuid=suid, title=str(it.get("title", ""))[:300])
            db.add(t)
            local[suid] = t
            stats["created"] += 1
        incoming_due=_parse_dt(it['due_at']) if it.get('due_at') else utcnow()
        if t.id and (t.due_at!=incoming_due or t.done!=bool(it.get('done',False)) or t.remind_offsets_h!=it.get('remind_offsets_h')):
            db.execute(PushDelivery.__table__.delete().where(PushDelivery.task_id==t.id))
        t.title = str(it.get("title", t.title or ""))[:300]
        t.kind = str(it.get("kind", "homework"))[:20]
        due = it.get("due_at")
        t.due_at = _parse_dt(due) if due else utcnow()
        t.done = bool(it.get("done", False))
        t.remind_offsets_h = [int(x) for x in (it.get("remind_offsets_h") if it.get("remind_offsets_h") is not None else [72, 24, 2])][:20]
        t.note = str(it.get("note", ""))
        t.source_text = str(it.get("source_text", ""))
        t.confidence = float(it.get("confidence", 1.0) or 1.0)
        t.course_id = course_ids.get(it.get("course_uuid") or "")
        t.source_material_id = mat_ids.get(it.get("source_material_uuid") or "")
        t.updated_at = updated_at
        if it.get("created_at"):
            t.created_at = _parse_dt(it["created_at"])
        db.flush()


def _merge_reviews(db: Session, items: list, tomb_map: dict, mat_ids: dict, stats: dict) -> None:
    local = {r.sync_uuid: r for r in db.execute(select(ReviewPlan)).scalars().all()}
    task_ids: dict[str, int] = {
        t.sync_uuid: t.id for t in db.execute(select(Task)).scalars().all()
    }
    for it in items:
        if not isinstance(it, dict) or not it.get("sync_uuid"):
            continue
        suid, updated_at = str(it["sync_uuid"]), _parse_dt(it.get("updated_at"))
        if _tombstone_newer(tomb_map, "review", suid, updated_at):
            stats["skipped"] += 1
            continue
        r = local.get(suid)
        if r is not None:
            if (r.updated_at or datetime.min) >= updated_at:
                stats["skipped"] += 1
                continue
            stats["updated"] += 1
        else:
            r = ReviewPlan(sync_uuid=suid, title=str(it.get("title", ""))[:300])
            db.add(r)
            local[suid] = r
            stats["created"] += 1
        r.title = str(it.get("title", r.title or ""))[:300]
        r.exam_task_ids = [
            tid for uid in (it.get("exam_task_uuids") or [])
            if (tid := task_ids.get(uid)) is not None
        ]
        sessions = []
        for s in it.get("sessions") or []:
            if not isinstance(s, dict):
                continue
            ns = dict(s)
            course_uid = ns.pop("course_uuid", None)
            ns.pop("course_id", None)
            if course_uid:
                c = db.execute(select(Course).where(Course.sync_uuid == course_uid)).scalar_one_or_none()
                if c:
                    ns["course_id"] = c.id
                    ns["course"] = c.name
            for src, dst in SESSION_ID_FIELDS:
                if dst in ns:
                    ns[src] = [
                        mid for uid in ns.pop(dst)
                        if (mid := mat_ids.get(uid)) is not None
                    ]
            sessions.append(ns)
        r.sessions = sessions
        r.updated_at = updated_at
        if it.get("created_at"):
            r.created_at = _parse_dt(it["created_at"])
        db.flush()


def _merge_notes(db: Session, items: list, tomb_map: dict, mat_ids: dict, stats: dict) -> None:
    local = {n.sync_uuid: n for n in db.execute(select(Note)).scalars().all()}
    for it in items:
        if not isinstance(it, dict) or not it.get("sync_uuid"):
            continue
        suid, updated_at = str(it["sync_uuid"]), _parse_dt(it.get("updated_at"))
        if _tombstone_newer(tomb_map, "note", suid, updated_at):
            stats["skipped"] += 1
            continue
        n = local.get(suid)
        if n is not None:
            if (n.updated_at or datetime.min) >= updated_at:
                stats["skipped"] += 1
                continue
            stats["updated"] += 1
        else:
            n = Note(sync_uuid=suid, title=str(it.get("title", ""))[:300])
            db.add(n)
            local[suid] = n
            stats["created"] += 1
        n.title = str(it.get("title", n.title or ""))[:300]
        n.mode = str(it.get("mode", "keypoints"))[:20]
        n.summary = str(it.get("summary", "") or "")
        n.points = [
            {"point": str(p.get("point", ""))[:300],
             "importance": str(p.get("importance", "medium"))[:10]}
            for p in (it.get("points") or [])
            if isinstance(p, dict) and str(p.get("point", "")).strip()
        ][:20]
        n.engine = str(it.get("engine", "rule"))[:20]
        n.material_id = mat_ids.get(it.get("material_uuid") or "")
        n.updated_at = updated_at
        if it.get("created_at"):
            n.created_at = _parse_dt(it["created_at"])
        db.flush()


def _merge_material_reviews(db: Session, items: list, tomb_map: dict, mat_ids: dict, stats: dict) -> None:
    """合并间隔重复状态（掌握程度/间隔/到期时刻）：按资料 uuid 重映射，LWW 收敛。"""
    local = {r.sync_uuid: r for r in db.execute(select(MaterialReview)).scalars().all()}
    for it in items:
        if not isinstance(it, dict) or not it.get("sync_uuid"):
            continue
        suid, updated_at = str(it["sync_uuid"]), _parse_dt(it.get("updated_at"))
        if _tombstone_newer(tomb_map, "materialreview", suid, updated_at):
            stats["skipped"] += 1
            continue
        material_id = mat_ids.get(it.get("material_uuid") or "")
        if material_id is None:
            continue  # 来源资料未同步/已删除 → 状态无从挂靠，丢弃
        r = local.get(suid)
        if r is not None:
            if (r.updated_at or datetime.min) >= updated_at:
                stats["skipped"] += 1
                continue
            stats["updated"] += 1
        else:
            r = MaterialReview(sync_uuid=suid, material_id=material_id)
            db.add(r)
            local[suid] = r
            stats["created"] += 1
        r.material_id = material_id
        r.mastery = float(it.get("mastery", 0.0) or 0.0)
        r.repetitions = int(it.get("repetitions", 0) or 0)
        r.lapses = int(it.get("lapses", 0) or 0)
        r.interval_days = float(it.get("interval_days", 0.0) or 0.0)
        r.ease = float(it.get("ease", 2.5) or 2.5)
        r.due_at = _parse_dt(it["due_at"]) if it.get("due_at") else None
        r.last_reviewed_at = (
            _parse_dt(it["last_reviewed_at"]) if it.get("last_reviewed_at") else None
        )
        r.last_rating = str(it.get("last_rating", ""))[:10]
        r.history = it.get("history") or []
        r.updated_at = updated_at
        if it.get("created_at"):
            r.created_at = _parse_dt(it["created_at"])
        db.flush()


def _merge_wrong_questions(db: Session, items: list, tomb_map: dict, course_ids: dict, stats: dict) -> None:
    """合并错题本（题目/答案/错因/状态/重练记录）：按课程 uuid 重映射，LWW 收敛。"""
    local = {w.sync_uuid: w for w in db.execute(select(WrongQuestion)).scalars().all()}
    for it in items:
        if not isinstance(it, dict) or not it.get("sync_uuid"):
            continue
        suid, updated_at = str(it["sync_uuid"]), _parse_dt(it.get("updated_at"))
        if _tombstone_newer(tomb_map, "wrongquestion", suid, updated_at):
            stats["skipped"] += 1
            continue
        w = local.get(suid)
        if w is not None:
            if (w.updated_at or datetime.min) >= updated_at:
                stats["skipped"] += 1
                continue
            stats["updated"] += 1
        else:
            w = WrongQuestion(sync_uuid=suid, title=str(it.get("title", ""))[:300])
            db.add(w)
            local[suid] = w
            stats["created"] += 1
        w.title = str(it.get("title", w.title or ""))[:300]
        w.question = str(it.get("question", "") or "")
        w.my_answer = str(it.get("my_answer", "") or "")
        w.answer = str(it.get("answer", "") or "")
        w.reason = str(it.get("reason", "") or "")
        w.source = str(it.get("source", "") or "")[:300]
        w.chapter = str(it.get("chapter", "") or "")[:120]
        w.difficulty = normalize_difficulty(str(it.get("difficulty", "") or ""))
        w.tags = [str(x) for x in (it.get("tags") or [])][:20]
        w.status = str(it.get("status", "open"))[:20]
        if w.status not in ("open", "done"):
            w.status = "open"
        w.review_count = int(it.get("review_count", 0) or 0)
        w.wrong_count = int(it.get("wrong_count", 1) or 1)
        w.streak = int(it.get("streak", 0) or 0)
        w.last_result = str(it.get("last_result", "") or "")[:10]
        w.last_reviewed_at = (
            _parse_dt(it["last_reviewed_at"]) if it.get("last_reviewed_at") else None
        )
        w.course_id = course_ids.get(it.get("course_uuid") or "")
        w.updated_at = updated_at
        if it.get("created_at"):
            w.created_at = _parse_dt(it["created_at"])
        db.flush()


# ---------------------------------------------------------------------------
# 远端交换（本端作为客户端）
# ---------------------------------------------------------------------------


def sync_now(db: Session, remote_url: str, remote_token: str, include_files: bool = True) -> dict:
    """与远端 StudyOS 做一轮快照交换，返回双向合并统计。"""
    remote_url = (remote_url or "").strip().rstrip("/")
    if not remote_url or not remote_token:
        raise SyncError("请先填写远端设备地址与同步令牌")
    if not remote_url.startswith(("http://", "https://")):
        raise SyncError("远端地址需以 http:// 或 https:// 开头")
    snap = build_snapshot(db, include_files=include_files)
    try:
        resp = httpx.post(
            f"{remote_url}/api/sync/exchange",
            json={"snapshot": snap, "include_files": include_files},
            headers={"X-Sync-Token": remote_token},
            timeout=EXCHANGE_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise SyncError(f"无法连接远端设备（{e.__class__.__name__}），请确认地址可达且 StudyOS 已启动") from e
    if resp.status_code == 401:
        raise SyncError("远端拒绝：同步令牌不正确")
    if resp.status_code >= 400:
        detail = ""
        try:
            detail = resp.json().get("detail", "")
        except Exception:
            detail = resp.text[:200]
        raise SyncError(f"远端返回错误 {resp.status_code}：{detail}")
    try:
        data = resp.json()
    except Exception as e:
        raise SyncError(f"远端响应格式无效: {e}") from e
    pulled = apply_snapshot(db, data.get("snapshot") or {})
    return {"pushed": data.get("applied") or dict(_EMPTY_STATS), "pulled": pulled}


# ---------------------------------------------------------------------------
# 完整备份（zip）与恢复
# ---------------------------------------------------------------------------


def make_backup(db: Session) -> bytes:
    """打包完整备份 zip：manifest.json（快照）+ files/（原始文件）。"""
    snap = build_snapshot(db, include_files=False, include_local_ids=True)
    file_map: dict[str, str] = {}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for m in db.execute(select(Material)).scalars().all():
            if not m.file_path:
                continue
            p = Path(m.file_path)
            if not p.exists() or not p.is_file():
                continue
            arc = f"files/{m.sync_uuid or uuid_mod.uuid4().hex}/{Path(m.file_name or 'file').name}"
            zf.write(p, arc)
            file_map[m.sync_uuid] = arc
        manifest = {
            "studyos_backup": 1,
            "created_at": _iso(utcnow()),
            "files": file_map,
            "snapshot": snap,
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
    return buf.getvalue()


def restore_backup(db: Session, data: bytes) -> dict:
    """从备份 zip 恢复：解析 manifest + 文件 → 全量替换本端。"""
    if len(data) > MAX_BACKUP_BYTES:
        raise SyncError(f"备份文件过大（上限 {MAX_BACKUP_BYTES // (1024 ** 3)}GB）")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise SyncError(f"备份文件无效（{e}），请使用「完整备份」导出的 zip") from e
    try:
        infos = zf.infolist()
        if len(infos) > MAX_BACKUP_ENTRIES:
            raise SyncError("备份文件条目过多，已拒绝恢复")
        try:
            m_info = zf.getinfo("manifest.json")
        except KeyError as e:
            raise SyncError("备份文件缺少 manifest.json，请使用「完整备份」导出的 zip") from e
        if m_info.file_size > MAX_MANIFEST_BYTES:
            raise SyncError("备份 manifest 异常过大，已拒绝恢复")
        try:
            manifest = json.loads(zf.read(m_info).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise SyncError(f"备份文件无效（{e}），请使用「完整备份」导出的 zip") from e
        snap = manifest.get("snapshot") or {}
        file_map = manifest.get("files") or {}
        for it in snap.get("materials", []):
            if not isinstance(it, dict):
                continue
            arc = file_map.get(it.get("sync_uuid") or "")
            if not arc:
                continue
            try:
                info = zf.getinfo(arc)
            except KeyError:
                it["file_skipped"] = True
                continue
            if info.file_size > MAX_FILE_BYTES:  # 先查元数据再解压，防 zip 炸弹
                it["file_skipped"] = True
                continue
            it["file_b64"] = base64.b64encode(zf.read(info)).decode()
    finally:
        zf.close()
    return apply_snapshot(db, snap, mode="replace")
