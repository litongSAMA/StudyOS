"""三格式导出：iCal(RFC5545·UID 稳定) / CSV(UTF-8 BOM) / Markdown。"""
import re

import pytest

import helpers


@pytest.fixture
def seeded(client):
    """一份完整数据：课程 + 资料 + 考试任务 + 复习计划。"""
    c = client.post("/api/courses", json={"name": "操作系统", "code": "CS301"}).json()
    m = client.post("/api/materials", json={
        "course_id": c["id"], "title": "操作系统课件：进程调度与死锁",
        "type": "courseware", "text": "进程调度与死锁", "tags": ["死锁"],
    }).json()
    t = client.post("/api/tasks", json={
        "course_id": c["id"], "title": "操作系统期末考",
        "kind": "exam", "due_at": helpers.expect_n_days(9).isoformat(),
        "note": "重点：死锁", "remind_offsets_h": [72, 24, 2],
    }).json()
    plan = client.post("/api/review/generate", json={"exam_task_ids": [t["id"]]}).json()
    return {"course": c, "material": m, "task": t, "plan": plan}


def _uids(text: str) -> list[str]:
    return re.findall(r"^UID:(.+)$", text, re.M)


def test_tasks_ical_valid_and_stable_uid(client, seeded):
    r1 = client.get("/api/export/tasks/ical")
    r2 = client.get("/api/export/tasks/ical")
    assert r1.status_code == 200
    assert "text/calendar" in r1.headers["content-type"]
    text = r1.text
    assert "BEGIN:VCALENDAR" in text
    assert "BEGIN:VTODO" in text
    assert "BEGIN:VALARM" in text  # 提前提醒
    assert seeded["task"]["title"] in text

    # UID 稳定：重复导出不产生重复日历事件
    uids = _uids(text)
    assert len(uids) == 1
    assert uids == _uids(r2.text)

    # 用 ics 库严格解析，确认格式合法
    from ics import Calendar

    cal = Calendar(text)
    assert len(cal.todos) == 1


def test_review_ical_has_vevent(client, seeded):
    r = client.get("/api/export/review/ical")
    assert r.status_code == 200
    text = r.text
    assert "BEGIN:VEVENT" in text
    assert "复习" in text


def test_csv_has_utf8_bom(client, seeded):
    r = client.get("/api/export/tasks/csv")
    assert r.status_code == 200
    assert r.content.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM，Excel 不乱码
    assert "ID,课程,任务" in r.text
    assert seeded["task"]["title"] in r.text

    r = client.get("/api/export/materials/csv")
    assert r.content.startswith(b"\xef\xbb\xbf")
    assert "ID,课程,标题" in r.text


def test_markdown_exports(client, seeded):
    r = client.get("/api/export/tasks/md")
    assert r.status_code == 200
    assert "# StudyOS" in r.text
    assert seeded["task"]["title"] in r.text

    r = client.get("/api/export/review/md")
    assert "# StudyOS 复习清单" in r.text
    assert "- [ ]" in r.text  # 勾选格式
    assert seeded["material"]["title"] in r.text  # 复习块附资料

    r = client.get("/api/export/materials/md")
    assert "# StudyOS 资料目录" in r.text


def test_materials_ical_is_real_calendar(client, seeded):
    """资料目录 iCal 是真 iCal（不再是 Markdown 兜底）：全天事件锚定入库日期。"""
    r1 = client.get("/api/export/materials/ical")
    r2 = client.get("/api/export/materials/ical")
    assert r1.status_code == 200
    assert "text/calendar" in r1.headers["content-type"]
    text = r1.text
    assert "BEGIN:VCALENDAR" in text
    assert "BEGIN:VEVENT" in text
    assert "DTSTART;VALUE=DATE" in text  # 全天事件
    assert seeded["material"]["title"] in text
    assert "难度" in text  # 元数据进 DESCRIPTION
    assert "# StudyOS" not in text  # 不再是 Markdown 内容

    # UID 稳定：重复导出不产生重复日历事件
    uids = _uids(text)
    assert len(uids) == 1
    assert uids == _uids(r2.text)

    # 用 ics 库严格解析：全天事件落在资料入库的本地日期
    from ics import Calendar

    from app.infra.timeutil import local_date, parse_iso

    cal = Calendar(text)
    assert len(cal.events) == 1
    ev = next(iter(cal.events))
    assert ev.all_day is True
    expected = local_date(parse_iso(seeded["material"]["created_at"])).isoformat()
    assert ev.begin.date().isoformat() == expected


def test_invalid_format_404(client):
    assert client.get("/api/export/tasks/xml").status_code == 404
