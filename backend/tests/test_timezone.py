"""UTC 时间规范回归：Z 后缀输出、偏移输入归一化、跨时区 LWW、旧库迁移、iCal 本地时区。

核心约定（见 app/timeutil.py）：存储一律 naive UTC，API/快照一律带时区，
展示层按设备本地时区换算。时间来源仅为设备本地时钟——断网同样成立。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, text
from ics import Calendar

import helpers
from app.infra.db import SessionLocal, UTC_MIGRATION_MARKER, _migrate_utc_timestamps
from app.infra.models import AppSetting, Course, Task
from app.domain.sync import apply_snapshot, build_snapshot
from app.infra.timeutil import legacy_naive_to_utc, utcnow

UTC = timezone.utc


def _make_task(client, due_iso: str) -> dict:
    r = client.post("/api/tasks", json={"title": "跨时区任务", "due_at": due_iso})
    assert r.status_code == 201, r.text
    return r.json()


def test_api_emits_z_and_normalizes_offset_input(client):
    """+08:00 输入 → 按 UTC 瞬时存储 → 输出 Z 后缀的等价 UTC。"""
    t = _make_task(client, "2030-01-01T00:00:00+08:00")
    assert t["due_at"] == "2029-12-31T16:00:00Z"
    assert t["created_at"].endswith("Z")
    got = client.get("/api/tasks").json()
    assert got[0]["due_at"] == "2029-12-31T16:00:00Z"


def test_naive_input_treated_as_utc(client):
    """无后缀 ISO 一律按 UTC 解释（新规范下前端只发 Z 或带偏移的 ISO）。"""
    t = _make_task(client, "2030-01-01T00:00:00")
    assert t["due_at"] == "2030-01-01T00:00:00Z"
    with SessionLocal() as db:
        row = db.execute(select(Task)).scalar_one()
        assert row.due_at == datetime(2030, 1, 1, 0, 0)  # naive UTC 入库


def test_lww_compares_by_utc_instant_across_offsets(client):
    """带偏移的 updated_at 按其 UTC 瞬时参与 LWW，而不是按墙上数字比较。"""
    t = _make_task(client, (utcnow() + timedelta(days=2)).isoformat())
    with SessionLocal() as db:
        task = db.execute(select(Task)).scalar_one()
        suid = task.sync_uuid
        base = build_snapshot(db)
    item = next(x for x in base["tasks"] if x["sync_uuid"] == suid)

    # 远端 UTC+9 的 updated_at 墙上时间比本端大 9 小时，但实际瞬时早 1 小时 → 输
    loser = dict(item)
    loser["title"] = "更晚但实际更早的修改"
    loser["updated_at"] = (utcnow() - timedelta(hours=1)).replace(tzinfo=UTC).astimezone(
        timezone(timedelta(hours=9))
    ).isoformat()
    snap = {"version": 2, "generated_at": None, "device_id": "b",
            "courses": [], "materials": [], "reviews": [], "settings": [],
            "tombstones": [], "tasks": [loser]}
    with SessionLocal() as db:
        assert apply_snapshot(db, snap)["skipped"] == 1
        assert db.execute(select(Task)).scalar_one().title == "跨时区任务"

    # UTC+8 的 updated_at 实际瞬时更新 5 分钟 → 胜
    winner = dict(item)
    winner["title"] = "更新且更晚的修改"
    winner["updated_at"] = (utcnow() + timedelta(minutes=5)).replace(tzinfo=UTC).astimezone(
        timezone(timedelta(hours=8))
    ).isoformat()
    snap = {"version": 2, "generated_at": None, "device_id": "c",
            "courses": [], "materials": [], "reviews": [], "settings": [],
            "tombstones": [], "tasks": [winner]}
    with SessionLocal() as db:
        assert apply_snapshot(db, snap)["updated"] == 1
        assert db.execute(select(Task)).scalar_one().title == "更新且更晚的修改"


def test_snapshot_timestamps_carry_z(client):
    _make_task(client, "2030-06-01T12:00:00+02:00")
    with SessionLocal() as db:
        snap = build_snapshot(db)
    assert snap["version"] == 2
    task = snap["tasks"][0]
    assert task["due_at"] == "2030-06-01T10:00:00Z"
    assert task["updated_at"].endswith("Z")
    assert datetime.fromisoformat(task["updated_at"]).tzinfo is not None
    # 迁移标记属于本机设置，绝不进入快照
    assert not any(s["key"] == UTC_MIGRATION_MARKER for s in snap["settings"])


def test_legacy_naive_rows_migrate_local_to_utc(client):
    """旧库 naive 本地时间一次性折算 UTC；标记幂等防重复折算。"""
    local_stamp = datetime.now() - timedelta(days=1)
    local_due = datetime.now() + timedelta(days=3)
    with SessionLocal() as db:
        db.execute(text(
            "INSERT INTO courses (name, code, semester, start_date, color,"
            " created_at, updated_at, sync_uuid)"
            " VALUES ('旧课程', '', '', NULL, '#6366f1', :c1, :c2, 'legacy0001')"
        ), {"c1": local_stamp, "c2": local_stamp})
        db.execute(text(
            "INSERT INTO tasks (course_id, title, kind, due_at, done,"
            " remind_offsets_h, note, source_text, source_material_id, confidence,"
            " created_at, updated_at, sync_uuid)"
            " VALUES (NULL, '旧任务', 'homework', :due, 0, '[72,24,2]', '', '',"
            " NULL, 1.0, :c1, :c1, 'legacy0002')"
        ), {"due": local_due, "c1": local_stamp})
        db.commit()

    _migrate_utc_timestamps()

    with SessionLocal() as db:
        course = db.execute(select(Course)).scalar_one()
        task = db.execute(select(Task)).scalar_one()
        assert course.created_at == legacy_naive_to_utc(local_stamp)
        assert course.updated_at == legacy_naive_to_utc(local_stamp)
        assert task.due_at == legacy_naive_to_utc(local_due)
        assert task.updated_at == legacy_naive_to_utc(local_stamp)
        assert db.get(AppSetting, UTC_MIGRATION_MARKER) is not None

    # 幂等：再跑一次不改变任何值（标记存在即跳过）
    with SessionLocal() as db:
        before = db.execute(select(Course)).scalar_one().updated_at
    _migrate_utc_timestamps()
    with SessionLocal() as db:
        assert db.execute(select(Course)).scalar_one().updated_at == before


def test_migration_marker_not_synced(client):
    """迁移标记是本机一次性开关：出现在快照里会让对端误跳过自己的迁移。"""
    _make_task(client, (utcnow() + timedelta(days=1)).isoformat())
    with SessionLocal() as db:
        snap = build_snapshot(db)
    assert not any(s["key"] == UTC_MIGRATION_MARKER for s in snap["settings"])


def test_ical_due_matches_utc_instant(client):
    """iCal 导出带本地时区，但其表示的瞬时与存储的 UTC 一致。"""
    _make_task(client, "2030-01-01T20:00:00+08:00")  # = 12:00Z
    r = client.get("/api/export/tasks/ical")
    assert r.status_code == 200
    cal = Calendar(r.text)
    assert len(cal.todos) == 1
    todo = next(iter(cal.todos))  # ics 0.7.x 的 todos/events 是 set
    due = todo.due
    dt = due.datetime if hasattr(due, "datetime") else due
    assert dt.astimezone(UTC) == datetime(2030, 1, 1, 12, 0, tzinfo=UTC)


def test_review_today_uses_local_date(client):
    """复习计划的「今天」按设备本地日期（due_at 虽存 UTC，日期语义不错位）。"""
    client.post("/api/courses", json={"name": "操作系统"})
    t = _make_task(client, helpers.expect_n_days(9).isoformat())
    r = client.post("/api/review/generate", json={"exam_task_ids": [t["id"]]})
    assert r.status_code == 201, r.text
    plan = r.json()
    assert plan["sessions"][0]["date"] == date.today().isoformat()
    assert plan["created_at"].endswith("Z")


def test_sync_status_now_and_export_headers_carry_z(client):
    st = client.get("/api/sync/status").json()
    assert st["now"].endswith("Z")
    _make_task(client, "2030-02-01T10:00:00Z")
    r = client.get("/api/export/tasks/md")
    assert r.headers["X-Export-Until"].endswith("Z")
    r2 = client.get("/api/export/tasks/md", params={"since": "2020-01-01T00:00:00Z"})
    assert r2.headers["X-Export-Since"] == "2020-01-01T00:00:00Z"
    assert r2.headers["X-Export-Count"] == "1"
