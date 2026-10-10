"""事务化课表导入（agent 核心 /api/agent/courses/preview）的验收用例。

覆盖：新建 vs 按名合并的判定、slots 落库形态（含宿主 ORM 兼容）、
同幂等键重放、不同键冲突、过期、外部改动保护、撤销还原、
非法输入的中文 422、能力标记与 API 层剥离内部计划字段。
"""
from datetime import datetime, timedelta
from uuid import uuid4
import io
import json
import re
import unittest

from agent_core import AgentError, AgentService
from agent_core.api import AgentAPI
from agent_core.common import canonical
from agent_core.courses import preview_import
from test_agent_core import NOW, CoreCase

# 预览夹具的「未传参」哨兵：None 是**非法输入**，必须能真实传给核心做校验
_MISSING = object()


def slot(**changes):
    value = dict(weekday=1, start="08:00", end="09:40", weeks=[1, 2, 3], location="", teacher="")
    value.update(changes)
    return value


def entry(**changes):
    value = dict(name="操作系统", code="CS301", teacher="张老师", location="教一101", slots=[slot()])
    value.update(changes)
    return value


class CourseCase(CoreCase):
    """课表导入用例的公共夹具：预览 / 执行 / 读库 / 直接种一门已有课程。"""

    def preview(self, entries=_MISSING, **changes):
        return preview_import(self.service, [entry()] if entries is _MISSING else entries, **changes)

    def execute(self, proposal, key="import"):
        return self.service.execute_plan(proposal["plan_id"], request_key=key)

    def rows(self):
        with self.service.db.snapshot() as con:
            return [dict(row) for row in con.execute("SELECT * FROM courses ORDER BY id")]

    def stored_slots(self, course_id=1):
        with self.service.db.snapshot() as con:
            return json.loads(self.service.db.row(con, "courses", course_id)["slots"])

    def seed_course(self, name="操作系统", slots=None, **fields):
        """直接落一门课，模拟「用户已有课表」，避免用例依赖导入自身。"""
        now = self.service.now()
        values = dict(
            name=name, code="", semester="", start_date=None, color="#6366f1", teacher="",
            location="", slots=canonical(slots or []), sync_uuid=str(uuid4()), created_at=now, updated_at=now,
        )
        values.update(fields)
        with self.service.db.transaction() as con:
            return self.service.db.write(con, "courses", values)

    def assert_rejected(self, callback, status=422):
        """断言状态码 + 中文提示（错误信息不得泄漏 SQL / 路径）。"""
        with self.assertRaises(AgentError) as caught:
            callback()
        self.assertEqual(caught.exception.status, status)
        self.assertTrue(re.search(r"[\u4e00-\u9fff]", caught.exception.detail), caught.exception.detail)
        return caught.exception.detail


class PreviewVerdictTests(CourseCase):
    def test_new_course_preview_creates_plan_without_writing(self):
        proposal = self.preview([entry()], term_start_date="2026-09-28", semester="2026秋")
        self.assertEqual(proposal["intent"], "course_import")
        self.assertTrue(proposal["plan_id"] and proposal["expires_at"])
        self.assertIn("新建 1 门、合并 0 门", proposal["summary"])
        verdict = proposal["entries"][0]
        self.assertFalse(verdict["exists"])
        self.assertIsNone(verdict["existing_id"])
        self.assertEqual(verdict["added_slots"], 1)
        self.assertTrue(verdict["changed"])
        self.assertEqual(len(proposal["actions"]), 1)
        # 新建课程没有旧行可做快照，但要有「同名仍然不存在」的并发保护
        # （两个预览都判为「新建」时，后执行的必须 409 而不是插重复课程）
        self.assertEqual(
            proposal["checks"], [{"kind": "absent", "table": "courses", "name": "操作系统"}]
        )
        self.assertNotIn("id", proposal["actions"][0])
        self.assertEqual(proposal["actions"][0]["table"], "courses")
        self.assertEqual(proposal["actions"][0]["op"], "upsert")
        self.assertEqual(proposal["actions"][0]["values"]["semester"], "2026秋")
        self.assertEqual(proposal["actions"][0]["values"]["start_date"], "2026-09-28")
        self.assertEqual(self.count("courses"), 0)
        self.assertEqual(self.count("agent_plans"), 1)

    def test_same_name_merges_and_other_name_creates(self):
        self.seed_course(slots=[slot(weeks=[1, 2])])
        proposal = self.preview([
            entry(slots=[slot(weeks=[2, 3]), slot(weekday=3, start="10:00", end="11:40", weeks=[5])]),
            entry(name="数据库", code="", teacher="", location="", slots=[slot(weekday=2)]),
        ])
        verdicts = {item["name"]: item for item in proposal["entries"]}
        self.assertTrue(verdicts["操作系统"]["exists"])
        self.assertEqual(verdicts["操作系统"]["existing_id"], 1)
        self.assertEqual(verdicts["操作系统"]["added_slots"], 1)
        self.assertEqual(verdicts["操作系统"]["merged_slots"], 1)
        self.assertFalse(verdicts["数据库"]["exists"])
        self.assertEqual(proposal["result"]["new_courses"], ["数据库"])
        self.assertEqual([item["id"] for item in proposal["result"]["merged_courses"]], [1])
        # 要合并的课程用行快照保护；新建课程用「同名不存在」保护
        self.assertEqual([check.get("id") for check in proposal["checks"]], [1, None])
        self.assertEqual(proposal["checks"][0]["before"]["slots"], canonical([slot(weeks=[1, 2])]))
        self.assertEqual(proposal["checks"][1], {"kind": "absent", "table": "courses", "name": "数据库"})
        self.assertEqual([action.get("id") for action in proposal["actions"]], [1, None])

    def test_same_batch_duplicate_names_merge_into_one_entry(self):
        proposal = self.preview([
            entry(slots=[slot(weeks=[1])]),
            entry(code="", teacher="", location="", slots=[slot(weeks=[2])]),
        ])
        self.assertEqual(len(proposal["entries"]), 1)
        self.assertEqual(len(proposal["actions"]), 1)
        self.assertEqual(proposal["actions"][0]["values"]["slots"], canonical([slot(weeks=[1, 2])]))

    def test_unchanged_import_produces_no_actions(self):
        self.seed_course(slots=[slot()], code="CS301", teacher="张老师", location="教一101")
        proposal = self.preview()
        self.assertEqual(proposal["actions"], [])
        self.assertFalse(proposal["entries"][0]["changed"])
        self.assertIn("无需写入", proposal["summary"])

    def test_existing_values_are_never_overwritten(self):
        self.seed_course(slots=[slot()], code="OLD", teacher="张老师", location="教一101",
                         semester="2026春", start_date="2026-02-23")
        proposal = self.preview([entry()], term_start_date="2026-09-28", semester="2026秋")
        # 时段与 code/teacher/location 都已存在 → 没有可写字段（学期/锚点也不覆盖）
        self.assertEqual(proposal["actions"], [])
        self.assertEqual(proposal["entries"][0]["current"]["code"], "OLD")
        self.assertEqual(proposal["entries"][0]["current"]["semester"], "2026春")
        self.assertEqual(proposal["entries"][0]["current"]["start_date"], "2026-02-23")

    def test_conflict_is_reported_but_does_not_block(self):
        proposal = self.preview([
            entry(slots=[slot(weekday=1, start="08:00", end="09:40")]),
            entry(name="数据库", code="", teacher="", location="", slots=[slot(weekday=1, start="09:00", end="10:40")]),
        ])
        self.assertEqual(len(proposal["conflicts"]), 1)
        conflict = proposal["conflicts"][0]
        self.assertEqual((conflict["weekday"], conflict["start"], conflict["end"]), (1, "09:00", "09:40"))
        self.assertEqual(conflict["courses"], ["操作系统", "数据库"])
        self.assertIn("时间重叠", conflict["message"])
        self.assertTrue(proposal["warnings"])
        self.assertEqual(proposal["result"]["conflict_count"], 1)
        self.assertEqual(self.execute(proposal)["changed_count"], 2)

    def test_conflict_detects_clash_with_untouched_course(self):
        self.seed_course(name="数据库", slots=[slot(weekday=1, start="09:00", end="10:40")])
        proposal = self.preview()
        self.assertEqual(proposal["conflicts"][0]["courses"], ["操作系统", "数据库"])
        self.assertEqual(proposal["entries"][0]["added_slots"], 1)

    def test_huge_timetable_degrades_conflict_scan(self):
        """现有课表超过扫描上限时退化为只查导入范围，并明确告知（预览响应时间可控）。"""
        repeated = [slot(weekday=(index % 5) + 1, weeks=[1]) for index in range(41)]
        for index in range(100):
            self.seed_course(name=f"课程{index}", slots=repeated)
        proposal = self.preview()
        self.assertEqual(proposal["conflicts"], [])
        self.assertTrue(any("导入范围" in item for item in proposal["warnings"]))


class ExecuteTests(CourseCase):
    def test_execute_writes_course_and_json_slots(self):
        proposal = self.preview(
            [entry(slots=[slot(weekday=3, start="10:00", end="11:40", weeks=[5, 2, 2]), slot()])],
            term_start_date="2026-09-28", semester="2026秋",
        )
        result = self.execute(proposal)
        self.assertFalse(result["replayed"])
        self.assertEqual(result["changed_count"], 1)
        self.assertEqual(result["changed"], [{"entity": "courses", "id": 1}])
        self.assertEqual(result["result"]["new_courses"], ["操作系统"])
        self.assertEqual(self.count("courses"), 1)
        row = self.rows()[0]
        self.assertEqual(row["name"], "操作系统")
        self.assertEqual((row["code"], row["semester"], row["start_date"]), ("CS301", "2026秋", "2026-09-28"))
        self.assertEqual((row["color"], row["teacher"], row["location"]), ("#6366f1", "张老师", "教一101"))
        self.assertTrue(row["sync_uuid"] and row["created_at"] and row["updated_at"])
        # slots 必须是可 json.loads 的 JSON 数组字符串，且按 weekday/start 稳定排序
        slots = json.loads(row["slots"])
        self.assertEqual([item["weekday"] for item in slots], [1, 3])
        self.assertEqual(slots[0], dict(weekday=1, start="08:00", end="09:40", weeks=[1, 2, 3], location="", teacher=""))
        self.assertEqual(slots[1]["weeks"], [2, 5])

    def test_merge_unions_weeks_and_keeps_first_non_empty_values(self):
        self.seed_course(slots=[slot(weeks=[1, 2], location="教一101", teacher="张老师")], teacher="张老师", location="教一101")
        proposal = self.preview([entry(teacher="李老师", location="教二202", slots=[
            slot(weeks=[2, 3]), slot(weekday=5, start="14:00", end="15:40", weeks=[1]),
        ])])
        self.assertEqual(proposal["entries"][0]["added_slots"], 1)
        self.assertEqual(proposal["entries"][0]["merged_slots"], 1)
        self.execute(proposal)
        self.assertEqual(self.count("courses"), 1)
        slots = self.stored_slots()
        self.assertEqual([item["weeks"] for item in slots], [[1, 2, 3], [1]])
        self.assertEqual(slots[0]["location"], "教一101")
        row = self.rows()[0]
        self.assertEqual((row["teacher"], row["location"]), ("张老师", "教一101"))

    def test_course_without_slots_is_allowed(self):
        """只有名称/老师的条目（解析器可能给不出时段）也要能导入，slots 落 '[]'。"""
        proposal = self.preview([entry(slots=[])])
        self.assertEqual(proposal["actions"][0]["values"]["slots"], "[]")
        self.assertEqual(proposal["entries"][0]["added_slots"], 0)
        self.execute(proposal)
        self.assertEqual(self.rows()[0]["slots"], "[]")
        self.assertEqual(json.loads(self.rows()[0]["slots"]), [])

    def test_slot_defaults_fill_from_entry_and_slot_override_wins(self):
        proposal = self.preview([entry(slots=[slot(weekday=2, start="14:00", end="15:40", location="", teacher=""),
                                              slot(weekday=4, start="16:00", end="17:40", location="教三303", teacher="李老师")])])
        self.execute(proposal)
        slots = self.stored_slots()
        self.assertEqual((slots[0]["location"], slots[0]["teacher"]), ("", ""))
        self.assertEqual((slots[1]["location"], slots[1]["teacher"]), ("教三303", "李老师"))
        # 课程级 location 不因时段覆盖而改变（与宿主 CourseSlot 语义一致）
        self.assertEqual(self.rows()[0]["location"], "教一101")

    def test_importing_the_same_schedule_twice_does_not_duplicate(self):
        self.execute(self.preview())
        again = self.preview()
        self.assertEqual(again["actions"], [])
        self.assertTrue(again["entries"][0]["exists"])
        self.execute(again, key="again")
        self.assertEqual(self.count("courses"), 1)
        self.assertEqual(len(self.stored_slots()), 1)

    def test_created_course_sync_uuid_is_stable_for_the_same_name(self):
        self.execute(self.preview())
        first = self.rows()[0]["sync_uuid"]
        self.service.undo(self.service.operations()[0]["id"], request_key="undo")
        self.execute(self.preview(), key="second")
        self.assertEqual(self.rows()[0]["sync_uuid"], first)


class IdempotencyTests(CourseCase):
    def test_replay_with_same_key_applies_once(self):
        proposal = self.preview()
        first = self.execute(proposal, key="import-1")
        second = self.execute(proposal, key="import-1")
        self.assertEqual(first["operation_id"], second["operation_id"])
        self.assertTrue(second["replayed"])
        self.assertEqual(self.count("courses"), 1)
        self.assertEqual(self.count("agent_operations"), 1)

    def test_second_key_after_execution_conflicts(self):
        proposal = self.preview()
        self.execute(proposal, key="import-1")
        self.assert_rejected(lambda: self.execute(proposal, key="import-2"), status=409)
        self.assertEqual(self.count("courses"), 1)

    def test_expired_plan_is_rejected(self):
        proposal = self.preview()
        self.service.clock = lambda: NOW + timedelta(hours=1)
        self.assert_rejected(lambda: self.execute(proposal), status=409)
        self.assertEqual(self.count("courses"), 0)

    def test_external_slot_change_after_preview_blocks_execution(self):
        self.seed_course(slots=[slot(weeks=[1])])
        proposal = self.preview([entry(slots=[slot(weeks=[2])])])
        with self.service.db.transaction() as con:
            con.execute("UPDATE courses SET slots=? WHERE id=1", (canonical([slot(weeks=[9])]),))
        self.assert_rejected(lambda: self.execute(proposal), status=409)
        self.assertEqual(self.stored_slots(), [slot(weeks=[9])])

    def test_external_rename_after_preview_blocks_execution(self):
        self.seed_course(slots=[slot(weeks=[1])])
        proposal = self.preview([entry(slots=[slot(weeks=[2])])])
        with self.service.db.transaction() as con:
            con.execute("UPDATE courses SET name='操作系统（改）' WHERE id=1")
        self.assert_rejected(lambda: self.execute(proposal), status=409)

    def test_plan_is_scoped_to_the_requesting_scope(self):
        proposal = self.preview()
        self.assert_rejected(lambda: self.service.execute_plan(proposal["plan_id"], request_key="import", scope="other"), status=404)
        self.assertEqual(self.count("courses"), 0)
        self.assertEqual(self.service.operations(scope="other"), [])

    def test_two_new_course_plans_cannot_both_create_the_same_course(self):
        """两个预览都判定「新建」同一门课时，后执行的必须 409 而不是插重复课程。

        宿主 courses.name 有唯一索引，这里同时覆盖「有索引」与「无索引」两种环境：
        前置的 absent 检查保证语义正确，IntegrityError 兜底保证不会变成 500。
        """
        first = self.preview()
        second = self.preview()
        self.assertNotEqual(first["plan_id"], second["plan_id"])
        self.execute(first, key="import-1")
        self.assert_rejected(lambda: self.execute(second, key="import-2"), status=409)
        self.assertEqual(self.count("courses"), 1)
        self.assertEqual(self.stored_slots(), [slot()])

    def test_absent_check_detects_course_created_outside_the_plan(self):
        """预览之后别处建了同名课程（含大小写/空格差异）→ 执行时 409。"""
        proposal = self.preview()
        self.seed_course(name=" 操作系统 ", slots=[slot(weeks=[3])])
        self.assert_rejected(lambda: self.execute(proposal), status=409)
        self.assertEqual(self.count("courses"), 1)
        self.assertEqual(self.stored_slots(), [slot(weeks=[3])])


class UndoTests(CourseCase):
    def test_undo_deletes_created_and_restores_merged_slots(self):
        course_id = self.seed_course(slots=[slot(weeks=[1])], teacher="张老师")
        before = self.rows()[0]
        proposal = self.preview([
            entry(slots=[slot(weeks=[2])]),
            entry(name="数据库", code="", teacher="", location="", slots=[slot(weekday=2)]),
        ])
        result = self.execute(proposal)
        self.assertEqual(self.count("courses"), 2)
        self.assertEqual(self.stored_slots(course_id)[0]["weeks"], [1, 2])
        undo = self.service.undo(result["operation_id"], request_key="undo")
        self.assertEqual(undo["restored_count"], 2)
        rows = self.rows()
        self.assertEqual([row["id"] for row in rows], [course_id])
        self.assertEqual(json.loads(rows[0]["slots"]), json.loads(before["slots"]))
        self.assertEqual(rows[0]["teacher"], "张老师")
        # 新建课程删除时写墓碑，供多端同步传播删除
        self.assertEqual(self.count("tombstones"), 1)
        self.assertEqual(self.count("courses"), 1)

    def test_undo_retry_is_idempotent(self):
        result = self.execute(self.preview())
        first = self.service.undo(result["operation_id"], request_key="undo")
        second = self.service.undo(result["operation_id"], request_key="undo")
        self.assertEqual(first["operation_id"], second["operation_id"])
        self.assertTrue(second["replayed"])
        self.assertEqual(self.count("courses"), 0)

    def test_undo_is_blocked_by_later_edit(self):
        result = self.execute(self.preview())
        with self.service.db.transaction() as con:
            con.execute("UPDATE courses SET name='被手改过' WHERE id=1")
        self.assert_rejected(lambda: self.service.undo(result["operation_id"], request_key="undo"), status=409)
        self.assertEqual(self.rows()[0]["name"], "被手改过")

    def test_undo_created_course_referenced_by_material_is_blocked(self):
        result = self.execute(self.preview())
        with self.service.db.transaction() as con:
            self.service.db.write(con, "materials", dict(
                course_id=1, title="课件", type="courseware", chapter="", difficulty="", exam_focus=False,
                file_path="", file_name="", file_hash="", source="text", text="正文", tags="[]",
                confidence=1, created_at=self.service.now(), updated_at=self.service.now(), sync_uuid=str(uuid4()),
            ))
        self.assert_rejected(lambda: self.service.undo(result["operation_id"], request_key="undo"), status=409)
        self.assertEqual(self.count("courses"), 1)

    def test_retry_after_undo_creates_a_new_operation(self):
        proposal = self.preview()
        result = self.execute(proposal, key="import-1")
        self.service.undo(result["operation_id"], request_key="undo")
        self.assert_rejected(lambda: self.execute(proposal, key="import-1"), status=409)
        self.assertEqual(self.count("courses"), 0)


class ValidationTests(CourseCase):
    def test_entries_container_is_validated(self):
        for bad in (None, [], "操作系统", [1], [entry()] * 101, {"name": "操作系统"}):
            self.assert_rejected(lambda value=bad: self.preview(value))

    def test_name_is_required_and_bounded(self):
        for bad in ("", "   ", None, 5, "课" * 121, ["操作系统"]):
            self.assert_rejected(lambda value=bad: self.preview([entry(name=value)]))

    def test_slots_must_be_a_bounded_array_of_objects(self):
        for bad in ("08:00", {"weekday": 1}, [slot()] * 51, ["08:00"]):
            self.assert_rejected(lambda value=bad: self.preview([entry(slots=value)]))

    def test_weekday_must_be_1_to_7(self):
        for bad in (0, 8, 9, -1, True, "1", None, 1.5):
            self.assert_rejected(lambda value=bad: self.preview([entry(slots=[slot(weekday=value)])]))

    def test_weeks_must_be_integers_in_range(self):
        for bad in ([0], [61], [1, 62], [1.5], [True], "1-16", [1] * 61, 3):
            self.assert_rejected(lambda value=bad: self.preview([entry(slots=[slot(weeks=value)])]))

    def test_clock_must_be_hh_mm(self):
        for bad in ("8:00", "25:00", "08:60", "", "0800", "08:00:00", "abc", None, 800):
            self.assert_rejected(lambda value=bad: self.preview([entry(slots=[slot(start=value)])]))
        # end 必须晚于 start
        self.assert_rejected(lambda: self.preview([entry(slots=[slot(start="09:40", end="08:00")])]))
        self.assert_rejected(lambda: self.preview([entry(slots=[slot(start="08:00", end="08:00")])]))

    def test_duration_limits(self):
        self.assert_rejected(lambda: self.preview([entry(slots=[slot(start="08:00", end="22:00")])]))
        # 100 门 × 12 小时 = 1200 小时 > 单次导入上限 1000 小时
        many = [entry(name=f"课程{index}", slots=[slot(start="08:00", end="20:00")]) for index in range(100)]
        detail = self.assert_rejected(lambda: self.preview(many))
        self.assertIn("总时长", detail)

    def test_field_length_limits(self):
        self.assert_rejected(lambda: self.preview([entry(code="C" * 41)]))
        self.assert_rejected(lambda: self.preview([entry(teacher="张" * 121)]))
        self.assert_rejected(lambda: self.preview([entry(location="教" * 201)]))
        self.assert_rejected(lambda: self.preview([entry(slots=[slot(location="教" * 201)])]))
        self.assert_rejected(lambda: self.preview([entry(slots=[slot(teacher="张" * 121)])]))
        self.assert_rejected(lambda: self.preview([entry()], semester="秋" * 41))

    def test_term_start_date_format(self):
        for bad in ("2026/09/01", "2026-02-30", "1999-09-01", "20260901", 20260901, "2026-9-1"):
            self.assert_rejected(lambda value=bad: self.preview([entry()], term_start_date=value))

    def test_invalid_input_never_writes(self):
        self.assert_rejected(lambda: self.preview([entry(slots=[slot(weekday=9)])]))
        self.assertEqual(self.count("courses"), 0)
        self.assertEqual(self.count("agent_plans"), 0)
        self.assertEqual(self.count("agent_operations"), 0)

    def test_error_messages_do_not_leak_sql_or_paths(self):
        detail = self.assert_rejected(lambda: self.preview([entry(slots=[slot(weeks=[99])])]))
        for leak in ("SELECT", "INSERT", "sqlite", "/Users", ".db"):
            self.assertNotIn(leak, detail)


class ApiTests(CourseCase):
    def setUp(self):
        super().setUp()
        self.api = AgentAPI(self.service, authorize=lambda environ: "local" if environ.get("HTTP_AUTHORIZATION") == "Bearer test" else None)

    def request(self, path, data=None, key="request", auth=True):
        body = json.dumps(data or {}).encode()
        environ = {
            "REQUEST_METHOD": "POST" if data is not None else "GET", "PATH_INFO": path,
            "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(body)),
            "wsgi.input": io.BytesIO(body), "HTTP_IDEMPOTENCY_KEY": key,
        }
        if auth:
            environ["HTTP_AUTHORIZATION"] = "Bearer test"
        status = []
        payload = b"".join(self.api(environ, lambda value, headers: status.append(int(value.split()[0]))))
        return status[0], json.loads(payload)

    def test_capabilities_include_course_import(self):
        status, body = self.request("/api/agent/capabilities")
        self.assertEqual(status, 200)
        self.assertTrue(body["course_import"])

    def test_preview_through_api_hides_internal_plan_fields(self):
        self.seed_course(slots=[slot(weeks=[1])])
        status, proposal = self.request("/api/agent/courses/preview", {
            "entries": [entry(slots=[slot(weeks=[2])])], "term_start_date": "2026-09-28", "semester": "2026秋",
        })
        self.assertEqual(status, 200)
        self.assertEqual(proposal["intent"], "course_import")
        self.assertTrue(proposal["expires_at"] and proposal["summary"])
        self.assertEqual(len(proposal["entries"]), 1)
        self.assertEqual(proposal["conflicts"], [])
        for hidden in ("checks", "actions", "dependencies"):
            self.assertNotIn(hidden, proposal)

    def test_preview_apply_and_undo_through_api(self):
        status, proposal = self.request("/api/agent/courses/preview", {"entries": [entry()]})
        self.assertEqual(status, 200)
        status, receipt = self.request(f"/api/agent/plans/{proposal['plan_id']}/apply", {}, key="apply-1")
        self.assertEqual(status, 200)
        self.assertEqual(receipt["changed_count"], 1)
        self.assertEqual(self.count("courses"), 1)
        status, undo = self.request(f"/api/agent/operations/{receipt['operation_id']}/undo", {}, key="undo-1")
        self.assertEqual(status, 200)
        self.assertEqual(self.count("courses"), 0)

    def test_body_scope_and_fake_actions_are_ignored(self):
        status, proposal = self.request("/api/agent/courses/preview", {
            "entries": [entry()], "scope": "other",
            "actions": [{"table": "tasks", "op": "delete", "id": 1}], "table": "sqlite_master",
        })
        self.assertEqual(status, 200)
        self.assertEqual(len(self.service.operations(scope="local")), 0)
        self.request(f"/api/agent/plans/{proposal['plan_id']}/apply", {}, key="apply-1")
        self.assertEqual(len(self.service.operations(scope="local")), 1)
        self.assertEqual(self.service.operations(scope="other"), [])

    def test_invalid_entry_returns_chinese_422(self):
        status, body = self.request("/api/agent/courses/preview", {"entries": [entry(slots=[slot(weekday=9)])]})
        self.assertEqual(status, 422)
        self.assertTrue(re.search(r"[\u4e00-\u9fff]", body["detail"]))
        self.assertEqual(self.count("courses"), 0)


def test_host_route_imports_course_through_session_local(client):
    """端到端：宿主 /api/agent/{path} 透传 → HostDatabase → 宿主 SessionLocal/ORM。

    这条用例不 mock 任何宿主层：App 的 lifespan 建表、真实路由分派、
    真实 ORM 读取与撤销，覆盖「前端最终会走的路径」。
    """
    from app.infra.db import SessionLocal
    from app.infra.models import Course

    preview = client.post("/api/agent/courses/preview", json={
        "entries": [entry(slots=[slot(weeks=[1, 2]), slot(weekday=4, start="16:00", end="17:40", weeks=[3])])],
        "term_start_date": "2026-09-28", "semester": "2026秋",
    })
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert plan["entries"][0]["exists"] is False
    applied = client.post(f"/api/agent/plans/{plan['plan_id']}/apply", json={}, headers={"Idempotency-Key": "course-import-1"})
    assert applied.status_code == 200, applied.text
    assert applied.json()["changed_count"] == 1
    with SessionLocal() as db:
        course = db.query(Course).one()
        assert course.name == "操作系统" and course.semester == "2026秋"
        assert course.start_date == "2026-09-28" and course.slots[0]["weeks"] == [1, 2]
        assert course.slots[1]["weekday"] == 4
        course_id = course.id
    # 同名再导入：合并（existing_id 指向同一行），不新建课程
    again = client.post("/api/agent/courses/preview", json={"entries": [entry(slots=[slot(weeks=[2, 5])])]})
    assert again.status_code == 200, again.text
    assert again.json()["entries"][0]["existing_id"] == course_id
    # 撤销：课程被删除，宿主 ORM 读不到
    undone = client.post(f"/api/agent/operations/{applied.json()['operation_id']}/undo", json={}, headers={"Idempotency-Key": "course-undo-1"})
    assert undone.status_code == 200, undone.text
    with SessionLocal() as db:
        assert db.query(Course).count() == 0


def test_host_orm_reads_imported_courses_and_slots(tmp_path):
    """真实宿主桥接（HostDatabase + app/models.py 的 JSON 列）能读回导入结果。

    这里自建一份宿主表结构的临时库：既不碰共享测试库，也不依赖宿主路由，
    但日期转换、JSON 列读取、撤销墓碑都走宿主真实实现。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.domain.core_bridge import HostDatabase
    from app.infra.db import Base
    from app.infra.models import Course

    path = tmp_path / "host.sqlite"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    service = AgentService(path, database=HostDatabase(path), clock=lambda: NOW)

    proposal = preview_import(service, [entry(slots=[
        slot(weekday=1, start="08:00", end="09:40", weeks=[1, 3]),
        slot(weekday=3, start="10:00", end="11:40", weeks=[2], location="教三303", teacher="李老师"),
    ])], term_start_date="2026-09-28", semester="2026秋")
    receipt = service.execute_plan(proposal["plan_id"], request_key="host-orm")
    assert receipt["changed_count"] == 1
    with sessions() as db:
        course = db.query(Course).one()
        assert course.name == "操作系统" and course.code == "CS301"
        assert course.semester == "2026秋" and course.start_date == "2026-09-28"
        assert course.teacher == "张老师" and course.location == "教一101"
        assert isinstance(course.slots, list) and len(course.slots) == 2
        assert course.slots[0] == {"weekday": 1, "start": "08:00", "end": "09:40", "weeks": [1, 3], "location": "", "teacher": ""}
        assert course.slots[1]["location"] == "教三303" and course.slots[1]["teacher"] == "李老师"
        assert isinstance(course.created_at, datetime) and isinstance(course.updated_at, datetime)
        course_id = course.id

    # 撤销：新建的课程被删除，宿主 ORM 读不到
    service.undo(receipt["operation_id"], request_key="host-orm-undo")
    with sessions() as db:
        assert db.query(Course).count() == 0

    # 重新导入后再导入同一门课：按名合并，不产生第二门课
    first = preview_import(service, [entry(slots=[slot(weekday=1, start="08:00", end="09:40", weeks=[3, 5])])])
    service.execute_plan(first["plan_id"], request_key="host-orm-2")
    second = preview_import(service, [entry(slots=[slot(weekday=1, start="08:00", end="09:40", weeks=[5, 7])])])
    assert second["entries"][0]["exists"] and second["entries"][0]["existing_id"] == course_id
    assert second["entries"][0]["merged_slots"] == 1 and second["entries"][0]["added_slots"] == 0
    service.execute_plan(second["plan_id"], request_key="host-orm-3")
    with sessions() as db:
        assert db.query(Course).count() == 1
        assert db.query(Course).one().slots[0]["weeks"] == [3, 5, 7]


if __name__ == "__main__":
    unittest.main()
