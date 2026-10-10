"""导入课表：iCal/JSON 解析、预览/确认导入接口、旧库迁移、同步往返。"""
from datetime import datetime, timedelta

from sqlalchemy import select, text

from app.domain.courseimport import (
    ImportParseError,
    guess_semester,
    parse_ics,
    parse_import_source,
    parse_weeks,
)
from app.infra.db import SessionLocal, init_db
from app.infra.models import Course

ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//ZhengFang//Schedule//CN
BEGIN:VEVENT
UID:1
DTSTART;TZID=Asia/Shanghai:20250901T080000
DTEND;TZID=Asia/Shanghai:20250901T094000
RRULE:FREQ=WEEKLY;UNTIL=20260112T155959Z;BYDAY=MO
SUMMARY:操作系统
LOCATION:教3-101
DESCRIPTION:教师:张三\\n周一第1-2节
EXDATE;TZID=Asia/Shanghai:20251006T080000
END:VEVENT
BEGIN:VEVENT
UID:2
DTSTART;TZID=Asia/Shanghai:20250903T140000
DTEND;TZID=Asia/Shanghai:20250903T154000
RRULE:FREQ=WEEKLY;UNTIL=20260112T155959Z;BYDAY=WE
SUMMARY:操作系统
LOCATION:实验楼A201
END:VEVENT
BEGIN:VEVENT
UID:3
DTSTART;TZID=Asia/Shanghai:20250902T101000
DURATION:PT45M
RRULE:FREQ=WEEKLY;COUNT=8;BYDAY=TU,TH
SUMMARY:【高数】高等数学A
DESCRIPTION:教师：李四\\n地点：教1-202
END:VEVENT
BEGIN:VEVENT
UID:4
DTSTART;TZID=Asia/Shanghai:20250901T235000
DTEND;TZID=Asia/Shanghai:20250902T003000
SUMMARY:答疑
LOCATION:线上
END:VEVENT
END:VCALENDAR"""


# ---------------------------------------------------------------------------
# 纯解析器（无数据库；client 仅用于触发建表，保证 clean_db 清理可用）
# ---------------------------------------------------------------------------

def test_parse_weeks_forms(client):
    assert parse_weeks("1-16") == list(range(1, 17))
    assert parse_weeks("1-16周") == list(range(1, 17))
    assert parse_weeks("第1-16周") == list(range(1, 17))
    assert parse_weeks("1-16单") == [1, 3, 5, 7, 9, 11, 13, 15]
    assert parse_weeks("1-16双") == [2, 4, 6, 8, 10, 12, 14, 16]
    assert parse_weeks("1,3,5-8") == [1, 3, 5, 6, 7, 8]
    assert parse_weeks([2, 4, 2]) == [2, 4]
    assert parse_weeks("1到3") == [1, 2, 3]
    assert parse_weeks(None) == []
    assert parse_weeks("") == []
    assert parse_weeks("全周") == []


def test_guess_semester(client):
    from datetime import date

    assert guess_semester(date(2025, 9, 1)) == "2025-2026-1"
    assert guess_semester(date(2026, 3, 2)) == "2025-2026-2"
    assert guess_semester(None) == ""


def test_parse_ics_weekly_weeks_teacher_location_exdate(client):
    r = parse_import_source(ICS)
    assert r["format"] == "ics"
    # 学期锚点 = 最早上课日 2025-09-01（周一）
    assert r["term_start_date"] == "2025-09-01"
    assert r["semester"] == "2025-2026-1"

    by_name = {e["name"]: e for e in r["entries"]}
    os_ = by_name["操作系统"]
    assert os_["teacher"] == "张三" and os_["location"] == "教3-101"
    assert len(os_["slots"]) == 2
    mon = next(s for s in os_["slots"] if s["weekday"] == 1)
    wed = next(s for s in os_["slots"] if s["weekday"] == 3)
    assert mon["start"] == "08:00" and mon["end"] == "09:40"
    # 2025-09-01 起共 20 周，EXDATE 排除了第 6 周（2025-10-06 国庆）
    assert mon["weeks"] == [w for w in range(1, 21) if w != 6]
    assert wed["location"] == "实验楼A201"
    assert wed["weeks"][0] == 1

    math_ = by_name["高等数学A"]  # 【高数】前缀被清理
    assert math_["teacher"] == "李四" and math_["location"] == "教1-202"
    assert sorted(s["weekday"] for s in math_["slots"]) == [2, 4]
    # COUNT=8 → 周二/周四各 4 次
    for s in math_["slots"]:
        assert s["weeks"] == [1, 2, 3, 4]
        assert s["start"] == "10:10" and s["end"] == "10:55"  # DURATION:PT45M

    # 单次事件（无 RRULE）也进课表
    assert "答疑" in by_name


def test_parse_ics_interval_skips_weeks(client):
    text = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//X//Y//CN
BEGIN:VEVENT
UID:1
DTSTART;TZID=Asia/Shanghai:20250901T080000
DTEND;TZID=Asia/Shanghai:20250901T090000
RRULE:FREQ=WEEKLY;INTERVAL=2;UNTIL=20251101T235959Z;BYDAY=MO
SUMMARY:体育
END:VEVENT
END:VCALENDAR"""
    r = parse_import_source(text)
    slot = r["entries"][0]["slots"][0]
    # 双周上课：9/1 起隔周（1、3、5、7、9 周）
    assert slot["weeks"] == [1, 3, 5, 7, 9]


def test_parse_ics_utc_times_converted_to_shanghai_wall_time(client):
    # 教务系统按 UTC 写 DTSTART（结尾 Z）→ 应换算成北京墙上时间
    text = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//X//Y//CN
BEGIN:VEVENT
UID:1
DTSTART:20250901T000000Z
DTEND:20250901T014000Z
RRULE:FREQ=WEEKLY;COUNT=2;BYDAY=MO
SUMMARY:晨课
END:VEVENT
END:VCALENDAR"""
    slot = parse_import_source(text)["entries"][0]["slots"][0]
    assert slot["start"] == "08:00" and slot["end"] == "09:40"


def test_parse_ics_skips_all_day_and_no_dtstart_events(client):
    """只有全天事件/无 DTSTART 的日历 → 没有可上课条目，报可读错误。"""
    import pytest

    text = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//X//Y//CN
BEGIN:VEVENT
UID:1
DTSTART;VALUE=DATE:20251001
DTEND;VALUE=DATE:20251008
SUMMARY:国庆假期
END:VEVENT
BEGIN:VEVENT
UID:2
SUMMARY:没有时间的会议
END:VEVENT
END:VCALENDAR"""
    with pytest.raises(ImportParseError, match="带时间"):
        parse_import_source(text)


def test_parse_invalid_raises(client):
    import pytest

    with pytest.raises(ImportParseError):
        parse_import_source("BEGIN:VCALENDAR\nEND:VCALENDAR")
    with pytest.raises(ImportParseError):
        parse_import_source("   ")
    with pytest.raises(ImportParseError):
        parse_import_source("{not json")
    with pytest.raises(ImportParseError):
        parse_import_source('{"courses": [{"name": "x", "slots": [{"weekday": 9, "start": "08:00", "end": "09:00"}]}]}')
    with pytest.raises(ImportParseError):
        parse_import_source('{"courses": [{"name": "x", "slots": [{"weekday": 1, "start": "08:00", "end": "07:00"}]}]}')


def test_parse_json_courses_and_flat_course_list(client):
    r = parse_import_source(
        '{"semester":"2026-2027-1","start_date":"2026-08-31","courses":['
        '{"name":"操作系统","teacher":"张三",'
        '"slots":[{"weekday":1,"start":"08:00","end":"09:40","weeks":"1-16单"},'
        '{"weekday":3,"start_period":5,"end_period":6,"weeks":[1,2,3]}]}]}'
    )
    assert r["format"] == "json"
    assert r["term_start_date"] == "2026-08-31" and r["semester"] == "2026-2027-1"
    e = r["entries"][0]
    assert e["teacher"] == "张三"
    s1 = next(s for s in e["slots"] if s["weekday"] == 1)
    s3 = next(s for s in e["slots"] if s["weekday"] == 3)
    assert s1["weeks"] == [1, 3, 5, 7, 9, 11, 13, 15]
    # 第5-6节 → 14:00~15:40（标准节次表）
    assert s3["start"] == "14:00" and s3["end"] == "15:40"

    flat = parse_import_source(
        '{"course_list":[{"name":"英语","day":2,"start":"08:00","end":"09:40",'
        '"weeks":"1-8","teacher":"王五"},'
        '{"name":"英语","day":4,"start":"08:00","end":"09:40","weeks":"1-8"}]}'
    )
    e = flat["entries"][0]
    assert e["name"] == "英语" and e["teacher"] == "王五"
    assert sorted(s["weekday"] for s in e["slots"]) == [2, 4]


# ---------------------------------------------------------------------------
# 接口：预览 + 确认导入
# ---------------------------------------------------------------------------

def test_preview_endpoint_file_and_text(client):
    # 粘贴文本通道
    r = client.post("/api/courses/import/preview", data={"text": ICS})
    assert r.status_code == 200
    pv = r.json()
    assert pv["format"] == "ics"
    assert pv["term_start_date"] == "2025-09-01"
    assert pv["semester"] == "2025-2026-1"
    names = [e["name"] for e in pv["entries"]]
    assert "操作系统" in names and "高等数学A" in names
    assert all(e["exists"] is False for e in pv["entries"])
    assert all(e["color"].startswith("#") for e in pv["entries"])

    # 文件上传通道
    r = client.post(
        "/api/courses/import/preview",
        files={"file": ("schedule.ics", ICS.encode("utf-8"), "text/calendar")},
    )
    assert r.status_code == 200
    assert r.json()["entries"] == pv["entries"]

    # 空内容
    assert client.post("/api/courses/import/preview").status_code == 422
    assert client.post("/api/courses/import/preview", json={"text": "随便写点什么"}).status_code == 422
    # 超大内容 → 413（JSON 通道不受 multipart 单字段 1MB 限制）
    big = "a" * (2 * 1024 * 1024 + 100)
    assert client.post("/api/courses/import/preview", json={"text": big}).status_code == 413


def test_preview_marks_existing_course(client):
    client.post("/api/courses", json={"name": "操作系统"})
    pv = client.post("/api/courses/import/preview", data={"text": ICS}).json()
    os_ = next(e for e in pv["entries"] if e["name"] == "操作系统")
    assert os_["exists"] is True and os_["existing_id"] is not None
    other = next(e for e in pv["entries"] if e["name"] != "操作系统")
    assert other["exists"] is False


def _entries(payload, keep=None):
    entries = [
        {"name": e["name"], "teacher": e["teacher"], "location": e["location"],
         "slots": e["slots"]}
        for e in payload["entries"]
    ]
    return entries if keep is None else [e for e in entries if e["name"] in keep]


def test_apply_creates_courses_with_slots_semester_and_color(client):
    pv = client.post("/api/courses/import/preview", data={"text": ICS}).json()
    r = client.post("/api/courses/import", json={
        "entries": _entries(pv), "start_date": pv["term_start_date"],
    })
    assert r.status_code == 200
    res = r.json()
    assert set(x["name"] for x in res["created"]) == {"答疑", "操作系统", "高等数学A"}
    assert res["updated"] == []

    courses = client.get("/api/courses").json()
    os_ = next(c for c in courses if c["name"] == "操作系统")
    assert os_["teacher"] == "张三" and os_["location"] == "教3-101"
    assert len(os_["slots"]) == 2
    assert os_["start_date"] == "2025-09-01"
    assert os_["semester"] == "2025-2026-1"  # 未传 semester → 按开学日自动猜
    assert os_["color"].startswith("#")
    # 自动配色按导入顺序取色，且互不重复
    colors = {c["name"]: c["color"] for c in courses}
    assert len(set(colors.values())) == len(colors)


def test_apply_merges_existing_course_without_duplicate(client):
    client.post("/api/courses", json={"name": "操作系统"})
    pv = client.post("/api/courses/import/preview", data={"text": ICS}).json()
    r = client.post("/api/courses/import", json={"entries": _entries(pv)})
    res = r.json()
    assert set(x["name"] for x in res["created"]) == {"答疑", "高等数学A"}
    assert [x["name"] for x in res["updated"]] == ["操作系统"]

    courses = client.get("/api/courses").json()
    assert len([c for c in courses if c["name"] == "操作系统"]) == 1
    os_ = next(c for c in courses if c["name"] == "操作系统")
    assert os_["teacher"] == "张三" and len(os_["slots"]) == 2

    # 再次导入同一份：已合并 → 全部无变化，不再出现 created/updated
    res2 = client.post("/api/courses/import", json={"entries": _entries(pv)}).json()
    assert res2["created"] == [] and res2["updated"] == []

    # 导入补一个新时段（周三 19:00）→ 周次/时段合并进已有课表
    extra = _entries(pv)
    extra[0]["slots"].append({"weekday": 5, "start": "19:00", "end": "20:40", "weeks": [2, 4]})
    res3 = client.post("/api/courses/import", json={"entries": [extra[0]]}).json()
    assert [x["name"] for x in res3["updated"]] == ["操作系统"]
    os_ = client.get("/api/courses").json()
    os_ = next(c for c in os_ if c["name"] == "操作系统")
    assert len(os_["slots"]) == 3


def test_apply_dedupes_same_name_entries_in_batch(client):
    # 同名条目（两个不同时段）一次提交 → 只建一门课、两时段都保留
    r = client.post("/api/courses/import", json={
        "entries": [
            {"name": "数据结构", "slots": [{"weekday": 1, "start": "08:00", "end": "09:40", "weeks": [1]}]},
            {"name": "数据结构", "slots": [{"weekday": 3, "start": "10:00", "end": "11:40", "weeks": [2]}]},
        ],
    })
    res = r.json()
    assert len(res["created"]) == 1
    assert len(res["created"][0]["slots"]) == 2

    # 空条目 / 空名 → 422
    assert client.post("/api/courses/import", json={"entries": []}).status_code == 422
    assert client.post(
        "/api/courses/import", json={"entries": [{"name": "  ", "slots": []}]}
    ).status_code == 422


def test_json_import_flow(client):
    payload = ('{"semester":"2026-2027-1","start_date":"2026-08-31","courses":['
               '{"name":"英语","teacher":"王五","slots":[{"weekday":2,"start":"08:00",'
               '"end":"09:40","weeks":"1-8双","location":"外语楼301"}]}]}')
    pv = client.post("/api/courses/import/preview", data={"text": payload}).json()
    assert pv["format"] == "json" and pv["semester"] == "2026-2027-1"
    assert pv["entries"][0]["slots"][0]["weeks"] == [2, 4, 6, 8]

    r = client.post("/api/courses/import", json={
        "entries": _entries(pv),
        "semester": pv["semester"], "start_date": pv["term_start_date"],
    }).json()
    c = r["created"][0]
    assert c["name"] == "英语" and c["semester"] == "2026-2027-1"
    assert c["start_date"] == "2026-08-31"
    assert c["slots"][0]["location"] == "外语楼301"


def test_course_crud_carries_schedule_fields(client):
    # POST/PATCH 都支持课表字段（课后手动编辑）
    r = client.post("/api/courses", json={
        "name": "编译原理", "teacher": "赵老师",
        "slots": [{"weekday": 4, "start": "08:00", "end": "09:40", "weeks": [1, 2]}],
    })
    assert r.status_code == 201
    c = r.json()
    assert c["teacher"] == "赵老师" and c["slots"][0]["weekday"] == 4

    r = client.patch(f"/api/courses/{c['id']}", json={
        "location": "教2-105",
        "slots": [{"weekday": 5, "start": "10:00", "end": "11:40", "weeks": [3]}],
    })
    assert r.status_code == 200
    assert r.json()["location"] == "教2-105"
    assert r.json()["slots"][0]["weekday"] == 5


# ---------------------------------------------------------------------------
# 旧库迁移 + 多端同步
# ---------------------------------------------------------------------------

def test_legacy_db_migrates_course_schedule_columns(client):
    """旧库（无 teacher/location/slots 列）启动时自动补列，旧数据不丢。"""
    client.post("/api/courses", json={"name": "遗留课程"})
    with SessionLocal() as db:
        db.execute(text("ALTER TABLE courses DROP COLUMN teacher"))
        db.execute(text("ALTER TABLE courses DROP COLUMN location"))
        db.execute(text("ALTER TABLE courses DROP COLUMN slots"))
        db.commit()

    init_db()  # 完整启动序列（与 lifespan 一致）

    with SessionLocal() as db:
        cols = {row[1] for row in db.execute(text("PRAGMA table_info(courses)"))}
        assert {"teacher", "location", "slots"} <= cols
        c = db.execute(select(Course).where(Course.name == "遗留课程")).scalar_one()
        assert c.teacher == "" and c.location == "" and c.slots == []
    # 迁移后正常读写
    r = client.get("/api/courses").json()
    assert r[0]["slots"] == []
    r = client.patch(f"/api/courses/{r[0]['id']}", json={"teacher": "钱老师"})
    assert r.json()["teacher"] == "钱老师"


def test_sync_roundtrip_course_schedule_fields(client):
    """课表字段（teacher/location/slots）随同步快照跨端迁移。"""
    from app.infra.db import Base, SessionLocal
    from app.domain.sync import apply_snapshot, build_snapshot
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.infra.config import settings

    c = client.post("/api/courses", json={
        "name": "操作系统", "teacher": "张三", "location": "教3-101",
        "slots": [{"weekday": 1, "start": "08:00", "end": "09:40", "weeks": [1, 2, 3]}],
    }).json()

    with SessionLocal() as dbA:
        snap = build_snapshot(dbA)
    course_item = snap["courses"][0]
    assert course_item["teacher"] == "张三"
    assert course_item["slots"][0]["weeks"] == [1, 2, 3]

    # 第二台设备合并快照
    path = settings.data_dir / "device-b-course-import.db"
    eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    B = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    with B() as dbB:
        stats = apply_snapshot(dbB, snap)
        assert stats["created"] == 1
        c2 = dbB.execute(select(Course)).scalar_one()
        assert c2.name == "操作系统" and c2.teacher == "张三"
        assert c2.location == "教3-101"
        assert c2.slots[0]["start"] == "08:00"
        assert c2.slots[0]["weeks"] == [1, 2, 3]

    # B 端修改课表 → 回传 → A 合并更新（JSON 列需整体赋值才会被 ORM 跟踪）
    with B() as dbB:
        c2 = dbB.execute(select(Course)).scalar_one()
        c2.slots = [{**c2.slots[0], "weeks": [1, 2, 3, 4]}]
        dbB.commit()
        snapB = build_snapshot(dbB)
    with SessionLocal() as dbA:
        stats = apply_snapshot(dbA, snapB)
        assert stats["updated"] == 1
    r = client.get("/api/courses").json()[0]
    assert r["slots"][0]["weeks"] == [1, 2, 3, 4]
