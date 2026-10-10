from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import copy
import json
import sqlite3
import unittest

from agent_core import AgentError, AgentService
from tmphelper import TempDir


NOW = datetime(2026, 10, 3, 4, 0, tzinfo=timezone.utc)


def task(**changes):
    value = dict(title="实验报告", kind="lab", course="操作系统", due_at="2026-10-10T23:59:00+08:00", confidence=0.95, note="保留备注", source_text="原通知")
    value.update(changes)
    return value


def material(**changes):
    value = dict(title="课件", type="courseware", course="操作系统", chapter="第3章", difficulty="medium", exam_focus=False, tags=["内存"], summary="内存管理", confidence=0.95)
    value.update(changes)
    return value


class CoreCase(unittest.TestCase):
    def setUp(self):
        # TempDir（makedirs 建目录）代替 TemporaryDirectory：部分 Windows 环境下
        # mkdtemp 目录里连 SQLite 都建不了库（详见 tmphelper 模块说明）
        self.temp = TempDir()
        self.path = Path(self.temp.name) / "test.sqlite"
        self.files = Path(self.temp.name) / "files"
        self.files.mkdir()
        self.service = AgentService(self.path, bootstrap=True, file_root=self.files, clock=lambda: NOW)

    def tearDown(self):
        self.temp.cleanup()

    def count(self, table):
        with self.service.db.connect() as con:
            return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def apply(self, tasks=None, materials=None, key="test", **changes):
        return self.service.apply(dict(tasks=tasks or [], materials=materials or [], **changes), request_key=key)

    def error(self, status, callback):
        with self.assertRaises(AgentError) as caught:
            callback()
        self.assertEqual(caught.exception.status, status)


class IdempotencyTests(CoreCase):
    def test_postponement_notice_cannot_create_a_second_task_via_ingest(self):
        self.apply([task()])
        self.error(409, lambda: self.apply([task(due_at="2026-10-15T23:59:00+08:00")], key="change", text="实验报告延期到10月15日 23:59"))
        self.assertEqual(self.count("tasks"), 1)
        self.assertEqual(self.count("agent_operations"), 1)

    def test_lost_response_retry_replays_original_receipt(self):
        first = self.apply([task()], [material()])
        self.service = AgentService(self.path, clock=lambda: NOW)
        second = self.apply([task()], [material()])
        self.assertEqual(first["operation_id"], second["operation_id"])
        self.assertTrue(second["replayed"])
        self.assertEqual(self.count("tasks"), 1)
        self.assertEqual(self.count("materials"), 1)

    def test_same_key_different_content_conflicts(self):
        self.apply([task()])
        modified = task()
        modified["title"] = "另一个任务"
        self.error(409, lambda: self.apply([modified]))
        self.assertEqual(self.count("tasks"), 1)

    def test_new_key_same_batch_reuses_existing_objects(self):
        self.apply([task()], [material()])
        again = self.apply([task()], [material()], key="next")
        self.assertEqual(again["created_tasks"], [])
        self.assertEqual(len(again["reused_tasks"]), 1)
        self.assertEqual(len(again["reused_materials"]), 1)

    def test_equivalent_timezone_and_unicode_match(self):
        self.apply([task()])
        item = task()
        item.update(title=" 实验报告 ", course=" 操作系统 ", due_at="2026-10-10T15:59:00Z")
        self.assertEqual(len(self.apply([item], key="next")["reused_tasks"]), 1)

    def test_different_deadline_keeps_distinct_task(self):
        self.apply([task()])
        item = task()
        item["due_at"] = "2026-10-11T15:59:00Z"
        self.assertEqual(len(self.apply([item], key="next")["created_tasks"]), 1)

    def test_same_titles_in_different_courses_remain_distinct(self):
        self.apply([task()], [material()])
        item = task()
        item["course"] = "数据库"
        self.apply([item], [material(course="数据库")], key="next")
        self.assertEqual(self.count("tasks"), 2)
        self.assertEqual(self.count("materials"), 2)

    def test_file_content_hash_deduplicates_renamed_upload(self):
        first = self.files / "first.txt"
        second = self.files / "second.txt"
        first.write_text("same file bytes")
        second.write_bytes(first.read_bytes())
        self.apply(materials=[material()], file_path=str(first), file_name=first.name)
        again = self.apply(materials=[material(title="重命名课件")], key="next", file_path=str(second), file_name=second.name)
        self.assertEqual(len(again["reused_materials"]), 1)
        self.assertEqual(self.count("materials"), 1)

    def test_file_path_escape_rejected(self):
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_text("outside")
        self.error(422, lambda: self.apply(materials=[material()], file_path=str(outside)))
        self.assertEqual(self.count("materials"), 0)

    def test_symlink_escape_rejected(self):
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_text("outside")
        link = self.files / "link.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            # Windows 普通账户默认没有创建符号链接的权限（WinError 1314），
            # 这是环境能力缺失而不是被测行为：跳过并说明，不让整套回归误报失败。
            self.skipTest(f"当前环境无法创建符号链接，跳过软链接逃逸用例：{exc}")
        self.error(422, lambda: self.apply(materials=[material()], file_path=str(link)))

    def test_invalid_last_item_rolls_back_entire_batch_and_course(self):
        item = task()
        item["due_at"] = "bad date"
        self.error(422, lambda: self.apply([item], [material()]))
        for table in ["courses", "materials", "tasks", "agent_operations"]:
            self.assertEqual(self.count(table), 0)

    def test_in_batch_duplicate_is_reused(self):
        result = self.apply([task(), task()], [material(), material()])
        self.assertEqual(len(result["created_tasks"]), 1)
        self.assertEqual(len(result["reused_tasks"]), 1)
        self.assertEqual(self.count("materials"), 1)

    def test_concurrent_same_key_creates_once(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.apply([task()], [material()]), range(6)))
        self.assertEqual(len({row["operation_id"] for row in results}), 1)
        self.assertEqual(sum(not row["replayed"] for row in results), 1)
        self.assertEqual(self.count("tasks"), 1)

    def test_concurrent_distinct_keys_cross_batch_deduplicate(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda key: self.apply([task()], [material()], key=str(key)), range(6)))
        self.assertEqual(self.count("tasks"), 1)
        self.assertEqual(self.count("materials"), 1)

    def test_empty_payload_is_rejected(self):
        self.error(422, lambda: self.apply())

    def test_naive_input_date_is_rejected(self):
        item = task()
        item["due_at"] = "2026-10-10T23:59"
        self.error(422, lambda: self.apply([item]))

    def test_missing_database_needs_explicit_bootstrap(self):
        self.error(503, lambda: AgentService(Path(self.temp.name) / "missing.db"))


class UndoTests(CoreCase):
    def test_undo_new_batch_in_reverse_dependency_order(self):
        result = self.apply([task()], [material()])
        undo = self.service.undo(result["operation_id"], request_key="undo")
        self.assertEqual(undo["restored_count"], 3)
        for table in ["tasks", "materials", "courses"]:
            self.assertEqual(self.count(table), 0)
        self.assertEqual(self.count("tombstones"), 3)

    def test_undo_retry_is_idempotent(self):
        result = self.apply([task()])
        first = self.service.undo(result["operation_id"], request_key="undo")
        second = self.service.undo(result["operation_id"], request_key="undo")
        self.assertEqual(first["operation_id"], second["operation_id"])
        self.assertTrue(second["replayed"])

    def test_undo_does_not_delete_reused_records(self):
        self.apply([task()], [material()])
        result = self.apply([task()], [material()], key="reuse")
        self.service.undo(result["operation_id"], request_key="undo")
        self.assertEqual(self.count("tasks"), 1)
        self.assertEqual(self.count("materials"), 1)

    def test_later_edit_blocks_undo(self):
        result = self.apply([task()])
        with self.service.db.transaction() as con:
            con.execute("UPDATE tasks SET note='后续人工编辑' WHERE id=1")
        self.error(409, lambda: self.service.undo(result["operation_id"], request_key="undo"))
        self.assertEqual(self.count("tasks"), 1)

    def test_reference_blocks_undo_and_rolls_back(self):
        result = self.apply(materials=[material()])
        item = task()
        item["title"] = "后来任务"
        self.apply([item], [material()], key="new")
        self.error(409, lambda: self.service.undo(result["operation_id"], request_key="undo"))
        self.assertEqual(self.count("materials"), 1)
        self.assertEqual(self.count("courses"), 1)

    def test_scope_cannot_read_or_undo_other_operation(self):
        result = self.apply([task()])
        self.assertEqual(self.service.operations(scope="other"), [])
        self.error(404, lambda: self.service.undo(result["operation_id"], request_key="undo", scope="other"))

    def test_retry_undone_write_is_rejected(self):
        result = self.apply([task()])
        self.service.undo(result["operation_id"], request_key="undo")
        self.error(409, lambda: self.apply([task()]))


if __name__ == "__main__":
    unittest.main()
