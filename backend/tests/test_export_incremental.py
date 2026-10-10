"""导出记录与增量导出：ExportRecord 既是流水也是增量基线（不再「写了从不读」）。"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

import helpers
from app.infra.timeutil import utcnow


@pytest.fixture
def seeded(client):
    c = client.post("/api/courses", json={"name": "操作系统", "code": "CS301"}).json()
    m = client.post("/api/materials", json={
        "course_id": c["id"], "title": "操作系统课件：进程调度与死锁",
        "type": "courseware", "text": "进程调度与死锁", "tags": ["死锁"],
    }).json()
    t = client.post("/api/tasks", json={
        "course_id": c["id"], "title": "操作系统期末考", "kind": "exam",
        "due_at": helpers.expect_n_days(9).isoformat(), "note": "重点：死锁",
    }).json()
    plan = client.post("/api/review/generate", json={"exam_task_ids": [t["id"]]}).json()
    return {"course": c, "material": m, "task": t, "plan": plan}


def _export(client, kind, fmt, **params):
    r = client.get(f"/api/export/{kind}/{fmt}", params=params)
    assert r.status_code == 200, r.text
    return r


def test_full_export_writes_readable_record(client, seeded):
    r = _export(client, "tasks", "csv")
    assert r.headers["X-Export-Count"] == "1"
    assert r.headers["X-Export-Incremental"] == "0"
    assert r.headers["X-Export-Since"] == ""
    assert r.headers["X-Export-Record-Id"]

    records = client.get("/api/export/records").json()
    assert len(records) == 1
    rec = records[0]
    assert rec["kind"] == "tasks" and rec["format"] == "csv"
    assert rec["item_count"] == 1 and rec["incremental"] is False
    assert rec["since_at"] is None and rec["until_at"]
    assert rec["file_path"] == "studyos-tasks.csv"

    # 记录可按类型/格式筛选
    assert client.get("/api/export/records", params={"kind": "materials"}).json() == []
    assert len(client.get("/api/export/records", params={"fmt": "csv"}).json()) == 1


def test_incremental_export_only_changed_items(client, seeded):
    _export(client, "tasks", "csv")  # 建立基线

    # 没有任何变更 → 增量导出 0 条，且**不写记录**（基线不前移、历史不被噪声污染）
    r = _export(client, "tasks", "csv", incremental="true")
    assert r.headers["X-Export-Count"] == "0"
    assert r.headers["X-Export-Incremental"] == "1"
    assert r.headers["X-Export-Since"]
    assert r.headers["X-Export-Record-Id"] == ""
    assert seeded["task"]["title"] not in r.text
    assert "ID,课程,任务" in r.text  # 依然是结构完整的 CSV
    assert len(client.get("/api/export/records").json()) == 1

    # 新增一条任务 → 增量只带新任务
    client.post("/api/tasks", json={
        "title": "编译原理实验二", "due_at": helpers.expect_n_days(3).isoformat(),
    })
    r = _export(client, "tasks", "csv", incremental="true")
    assert r.headers["X-Export-Count"] == "1"
    assert "编译原理实验二" in r.text
    assert seeded["task"]["title"] not in r.text
    records = client.get("/api/export/records").json()
    assert len(records) == 2
    assert records[0]["incremental"] is True and records[0]["item_count"] == 1
    assert records[0]["since_at"] and records[0]["file_path"].startswith("studyos-tasks-incremental-")

    # 修改既有任务（updated_at 变新）→ 也算变更
    client.patch(f"/api/tasks/{seeded['task']['id']}", json={"note": "改了备注"})
    r = _export(client, "tasks", "csv", incremental="true")
    assert r.headers["X-Export-Count"] == "1"
    assert seeded["task"]["title"] in r.text
    assert "编译原理实验二" not in r.text


def test_incremental_baseline_is_per_format(client, seeded):
    """不同格式各自维护基线：CSV 导过不影响 iCal 的首次增量导出。"""
    _export(client, "tasks", "csv")
    r = _export(client, "tasks", "ical", incremental="true")
    # iCal 没有历史基线 → 首次增量退化为全量，并落基线
    assert r.headers["X-Export-Count"] == "1"
    assert r.headers["X-Export-Since"] == ""
    assert "BEGIN:VTODO" in r.text
    r = _export(client, "tasks", "ical", incremental="true")
    assert r.headers["X-Export-Count"] == "0"
    assert "BEGIN:VTODO" not in r.text  # 空日历依然是合法 VCALENDAR
    assert "BEGIN:VCALENDAR" in r.text


def test_incremental_materials_and_review(client, seeded):
    _export(client, "materials", "md")
    _export(client, "review", "md")
    assert _export(client, "materials", "md", incremental="true").headers["X-Export-Count"] == "0"
    assert _export(client, "review", "md", incremental="true").headers["X-Export-Count"] == "0"

    client.post("/api/materials", json={"title": "新笔记", "type": "notes", "text": "x"})
    r = _export(client, "materials", "md", incremental="true")
    assert r.headers["X-Export-Count"] == "1"
    assert "新笔记" in r.text
    assert seeded["material"]["title"] not in r.text

    client.post("/api/review/generate", json={"exam_task_ids": [seeded["task"]["id"]]})
    r = _export(client, "review", "md", incremental="true")
    assert r.headers["X-Export-Count"] == "1"


def test_incremental_materials_ical_is_real(client, seeded):
    """资料目录 iCal 走真 iCal 渲染，同样参与增量导出（基线按「类型 × 格式」）。"""
    r = _export(client, "materials", "ical")
    assert "BEGIN:VEVENT" in r.text and "BEGIN:VCALENDAR" in r.text

    # 无变更 → 0 条且不写记录；空日历依然是合法 VCALENDAR
    r = _export(client, "materials", "ical", incremental="true")
    assert r.headers["X-Export-Count"] == "0"
    assert "BEGIN:VEVENT" not in r.text
    assert "BEGIN:VCALENDAR" in r.text
    assert r.headers["X-Export-Record-Id"] == ""
    assert len(client.get("/api/export/records").json()) == 1

    # 新增资料 → 增量只带新条目（全天事件）
    client.post("/api/materials", json={"title": "编译原理新课件", "type": "courseware", "text": "x"})
    r = _export(client, "materials", "ical", incremental="true")
    assert r.headers["X-Export-Count"] == "1"
    assert "编译原理新课件" in r.text
    assert seeded["material"]["title"] not in r.text


def test_explicit_since_overrides_baseline(client, seeded):
    future = (utcnow() + timedelta(hours=1)).isoformat()
    r = _export(client, "tasks", "md", since=future)
    assert r.headers["X-Export-Count"] == "0"
    assert r.headers["X-Export-Incremental"] == "1"

    past = (utcnow() - timedelta(days=1)).isoformat()
    r = _export(client, "tasks", "md", since=past)
    assert r.headers["X-Export-Count"] == "1"
    assert seeded["task"]["title"] in r.text

    assert client.get("/api/export/tasks/md", params={"since": "不是时间"}).status_code == 422


def test_export_status_reports_pending_changes(client, seeded):
    st = client.get("/api/export/status").json()
    by_kind = {k["kind"]: k for k in st["kinds"]}
    assert by_kind["tasks"]["total"] == 1
    assert by_kind["tasks"]["last_export"] is None
    # 无基线时「待导出变更数」= 全部条目
    csv_status = next(f for f in by_kind["tasks"]["formats"] if f["format"] == "csv")
    assert csv_status["changed"] == 1 and csv_status["last"] is None

    _export(client, "tasks", "csv")
    st = client.get("/api/export/status").json()
    by_kind = {k["kind"]: k for k in st["kinds"]}
    csv_status = next(f for f in by_kind["tasks"]["formats"] if f["format"] == "csv")
    assert csv_status["changed"] == 0
    assert csv_status["last"]["item_count"] == 1
    assert by_kind["tasks"]["last_export"]["format"] == "csv"
    # 其它格式仍是「全部待导出」
    md_status = next(f for f in by_kind["tasks"]["formats"] if f["format"] == "md")
    assert md_status["changed"] == 1

    client.post("/api/tasks", json={
        "title": "又一个作业", "due_at": helpers.expect_n_days(2).isoformat(),
    })
    st = client.get("/api/export/status").json()
    by_kind = {k["kind"]: k for k in st["kinds"]}
    csv_status = next(f for f in by_kind["tasks"]["formats"] if f["format"] == "csv")
    assert csv_status["changed"] == 1
    assert by_kind["tasks"]["total"] == 2
    assert st["records"] and st["records"][0]["kind"] == "tasks"


def test_clear_records_resets_baseline(client, seeded):
    _export(client, "tasks", "csv")
    _export(client, "materials", "csv")
    assert len(client.get("/api/export/records").json()) == 2

    # 只清一类
    assert client.request("DELETE", "/api/export/records", params={"kind": "tasks"}).json() == {"deleted": 1}
    kinds = [r["kind"] for r in client.get("/api/export/records").json()]
    assert kinds == ["materials"]
    # 基线被清 → 增量退化为全量
    r = _export(client, "tasks", "csv", incremental="true")
    assert r.headers["X-Export-Count"] == "1"

    assert client.request("DELETE", "/api/export/records").json()["deleted"] >= 1
    assert client.get("/api/export/records").json() == []
    assert client.request("DELETE", "/api/export/records", params={"kind": "xml"}).status_code == 404


def test_record_false_does_not_touch_baseline(client, seeded):
    """预览/自动化拉取可用 record=false，不污染增量基线。"""
    r = _export(client, "tasks", "csv", record="false")
    assert r.headers["X-Export-Record-Id"] == ""
    assert client.get("/api/export/records").json() == []
    assert _export(client, "tasks", "csv", incremental="true").headers["X-Export-Count"] == "1"


def test_unknown_kind_and_format(client):
    assert client.get("/api/export/unknown/csv").status_code == 404
    assert client.get("/api/export/tasks/xml").status_code == 404


def test_legacy_records_migrated_to_new_shape(client, seeded):
    """旧库遗留记录（kind='studyos-tasks'、无时间窗口）迁移后可直接作增量基线。"""
    from app.infra.db import SessionLocal, _migrate_export_records

    stamp = utcnow() - timedelta(minutes=5)  # 新规范：库内时间统一 naive UTC
    with SessionLocal() as db:
        db.execute(
            text(
                "INSERT INTO export_records"
                "(format, kind, file_path, created_at, item_count, incremental) "
                "VALUES ('csv', 'studyos-tasks', 'studyos-tasks.csv', :ts, 0, 0)"
            ),
            {"ts": stamp.strftime("%Y-%m-%d %H:%M:%S.%f")},  # 字符串写入，避开 3.12 适配器弃用
        )
        db.commit()

    _migrate_export_records()
    rec = client.get("/api/export/records").json()[0]
    assert rec["kind"] == "tasks"
    assert rec["until_at"] is not None  # 回填 created_at → 基线可用
    assert rec["incremental"] is False and rec["item_count"] == 0

    # 迁移后的基线立即生效：since 取回填的 until_at（= 旧记录 created_at）
    r = _export(client, "tasks", "csv", incremental="true")
    assert r.headers["X-Export-Since"][:19] == stamp.isoformat()[:19]
    assert r.headers["X-Export-Count"] == "1"  # 种子任务晚于该基线 → 计入增量
