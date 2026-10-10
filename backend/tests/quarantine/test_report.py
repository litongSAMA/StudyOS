"""学习报告 / 学期统计：周聚合引擎 + 学期分组 API + Markdown 导出。"""
from datetime import date, datetime, timezone

import pytest

from app.report import UNSET_SEMESTER, build_series, week_buckets


def _d(iso: str) -> date:
    return date.fromisoformat(iso)


# ---------------------------------------------------------------------------
# 纯函数引擎
# ---------------------------------------------------------------------------


def test_week_buckets_monday_aligned():
    # 2024-09-03（周二）→ 2024-09-23（周一）：首尾都对齐到周一
    bs = week_buckets(_d("2024-09-03"), _d("2024-09-23"))
    assert bs[0] == _d("2024-09-02")
    assert bs[-1] == _d("2024-09-23")
    assert len(bs) == 4


def test_week_buckets_empty_and_cap():
    assert week_buckets(_d("2024-09-10"), _d("2024-09-09")) == []
    # 跨度超过 max_points → 向左截断，终点不变
    bs = week_buckets(_d("2020-01-01"), _d("2024-01-01"), max_points=10)
    assert len(bs) == 10
    assert bs[-1] == _d("2024-01-01")


def test_build_series_mastery_and_reviews():
    buckets = week_buckets(_d("2024-09-02"), _d("2024-09-16"))
    events = [
        (_d("2024-09-03"), 1, 0.5),
        (_d("2024-09-04"), 2, 0.8),
        (_d("2024-09-10"), 1, 0.9),
        (_d("2024-09-12"), 3, 0.3),
    ]
    pts = build_series(buckets, events, [1, 2, 3], [], [])
    assert [p["date"] for p in pts] == ["2024-09-02", "2024-09-09", "2024-09-16"]
    # 首周：资料 1→0.5、资料 2→0.8、资料 3 未复习 → 平均 (0.5+0.8+0)/3
    assert pts[0]["mastery"] == pytest.approx((0.5 + 0.8) / 3, abs=1e-4)
    assert pts[0]["reviews"] == 2
    # 次周：资料 1 更新 0.9、资料 3 开始 0.3 → (0.9+0.8+0.3)/3
    assert pts[1]["mastery"] == pytest.approx((0.9 + 0.8 + 0.3) / 3, abs=1e-4)
    assert pts[1]["reviews"] == 2
    # 末周无新事件：掌握度保持、计数为 0、累计 4
    assert pts[2]["mastery"] == pts[1]["mastery"]
    assert pts[2]["reviews"] == 0
    assert pts[2]["reviews_cum"] == 4


def test_build_series_wrong_timeline():
    buckets = week_buckets(_d("2024-09-02"), _d("2024-09-16"))
    created = [_d("2024-09-02"), _d("2024-09-08"), _d("2024-09-15")]
    conquered = [_d("2024-09-10"), _d("2024-09-14")]
    pts = build_series(buckets, [], [], created, conquered)
    assert pts[0]["new_wrong"] == 2
    assert pts[0]["conquered"] == 0
    assert pts[0]["wrong_rate"] == 0.0
    assert pts[1]["new_wrong"] == 1  # 09-15 落在 [09-09, 09-16) 这一周
    assert pts[1]["conquered"] == 2
    assert pts[1]["wrong_rate"] == pytest.approx(2 / 3 * 100, abs=0.05)
    assert pts[2]["wrong_total"] == 3
    assert pts[2]["conquered_cum"] == 2
    assert pts[2]["wrong_rate"] == pytest.approx(2 / 3 * 100, abs=0.05)


def test_build_series_clamps_early_events():
    # 首周之前的事件并入首周（截断口径：掌握度与计数都生效）
    buckets = week_buckets(_d("2024-09-02"), _d("2024-09-02"))
    pts = build_series(buckets, [(_d("2024-08-20"), 1, 0.7)], [1], [], [])
    assert pts[0]["mastery"] == pytest.approx(0.7)
    assert pts[0]["reviews"] == 1


def test_build_series_no_materials_mastery_zero():
    buckets = week_buckets(_d("2024-09-02"), _d("2024-09-02"))
    pts = build_series(buckets, [], [], [_d("2024-09-02")], [])
    assert pts[0]["mastery"] == 0.0
    assert pts[0]["wrong_total"] == 1


# ---------------------------------------------------------------------------
# API：学期分组 + 指标 + 时间线
# ---------------------------------------------------------------------------


def _mk_course(client, name, semester=""):
    return client.post("/api/courses", json={"name": name, "semester": semester}).json()


def _mk_material(client, course_id, title):
    return client.post("/api/materials", json={
        "course_id": course_id, "title": title, "type": "courseware", "text": "x",
    }).json()


def _mk_task(client, course_id, title, done=False):
    t = client.post("/api/tasks", json={
        "course_id": course_id, "title": title, "kind": "homework",
        "due_at": datetime(2099, 1, 1, tzinfo=timezone.utc).isoformat(),
    }).json()
    if done:
        t = client.patch(f"/api/tasks/{t['id']}", json={"done": True}).json()
    return t


def test_report_empty(client):
    body = client.get("/api/report").json()
    assert body["semesters"] == []
    assert body["overview"]["label"] == "全部学期"
    assert body["overview"]["stats"]["courses"] == 0
    assert body["overview"]["series"] == []


def test_report_semester_grouping_and_series(client):
    c1 = _mk_course(client, "操作系统", "2024-2025-1")
    c2 = _mk_course(client, "计算机网络", "2024-2025-1")
    m1 = _mk_material(client, c1["id"], "进程调度")
    m2 = _mk_material(client, c2["id"], "TCP 三次握手")
    _mk_task(client, c1["id"], "实验报告", done=True)
    _mk_task(client, c2["id"], "平时作业")

    # 三次评分：good(+0.15) → easy(+0.25)=0.40；hard(+0.05)
    client.post("/api/review/rate", json={"material_id": m1["id"], "rating": "good"})
    client.post("/api/review/rate", json={"material_id": m1["id"], "rating": "easy"})
    client.post("/api/review/rate", json={"material_id": m2["id"], "rating": "hard"})

    # 错题：1 条连续答对 2 次 → 已掌握；1 条待攻克
    w1 = client.post("/api/wrongbook", json={
        "course_id": c1["id"], "title": "题1", "question": "q",
    }).json()
    client.post(f"/api/wrongbook/{w1['id']}/practice", json={"ok": True})
    client.post(f"/api/wrongbook/{w1['id']}/practice", json={"ok": True})
    client.post("/api/wrongbook", json={"course_id": c2["id"], "title": "题2"})

    body = client.get("/api/report").json()
    assert len(body["semesters"]) == 1
    s = body["semesters"][0]
    assert s["semester"] == "2024-2025-1"
    assert s["label"] == "2024-2025-1"
    assert s["course_names"] == ["操作系统", "计算机网络"]

    st = s["stats"]
    assert st["courses"] == 2
    assert st["materials"] == 2
    assert st["materials_reviewed"] == 2
    assert st["materials_mastered"] == 0
    assert st["mastery"] == pytest.approx((0.40 + 0.05) / 2)
    assert st["reviews"] == 3
    assert st["wrong_total"] == 2
    assert st["wrong_done"] == 1
    assert st["wrong_rate"] == pytest.approx(50.0)
    assert st["tasks_total"] == 2
    assert st["tasks_done"] == 1
    assert st["tasks_rate"] == pytest.approx(50.0)

    # 时间线：末周（今天）掌握度=当前平均，复习次数=3，攻克率=50%
    assert s["series"]
    last = s["series"][-1]
    assert last["mastery"] == pytest.approx((0.40 + 0.05) / 2, abs=1e-4)
    assert last["reviews"] == 3
    assert last["reviews_cum"] == 3
    assert last["wrong_rate"] == pytest.approx(50.0)
    assert last["wrong_total"] == 2


def test_report_multiple_semesters_and_overview(client):
    c1 = _mk_course(client, "操作系统", "2024-2025-1")
    c2 = _mk_course(client, "编译原理", "2023-2024-2")
    m1 = _mk_material(client, c1["id"], "进程调度")
    m2 = _mk_material(client, c2["id"], "文法分析")
    client.post("/api/review/rate", json={"material_id": m1["id"], "rating": "good"})
    client.post("/api/review/rate", json={"material_id": m2["id"], "rating": "good"})
    client.post("/api/wrongbook", json={"course_id": c2["id"], "title": "旧学期错题"})

    body = client.get("/api/report").json()
    # 活跃时间相同时按学期名排序（确定口径）
    assert [s["semester"] for s in body["semesters"]] == ["2023-2024-2", "2024-2025-1"]
    by_sem = {s["semester"]: s for s in body["semesters"]}
    assert by_sem["2024-2025-1"]["stats"]["reviews"] == 1
    assert by_sem["2023-2024-2"]["stats"]["reviews"] == 1
    assert by_sem["2023-2024-2"]["stats"]["wrong_total"] == 1

    ov = body["overview"]
    assert ov["stats"]["courses"] == 2
    assert ov["stats"]["materials"] == 2
    assert ov["stats"]["reviews"] == 2
    assert ov["stats"]["wrong_total"] == 1
    assert ov["series"][-1]["reviews_cum"] == 2


def test_report_ordering_recent_active_first(client):
    # 最近活跃的学期排在最前（旧学期活动回拨 100 天）
    from datetime import timedelta

    from app.db import SessionLocal
    from app.models import MaterialReview
    from app.timeutil import iso_utc, utcnow

    c1 = _mk_course(client, "操作系统", "2024-2025-1")
    c2 = _mk_course(client, "编译原理", "2023-2024-2")
    m1 = _mk_material(client, c1["id"], "进程调度")
    m2 = _mk_material(client, c2["id"], "文法分析")

    old = utcnow() - timedelta(days=100)
    with SessionLocal() as db:
        db.add(MaterialReview(
            material_id=m2["id"], mastery=0.5, repetitions=1,
            history=[{"at": iso_utc(old), "rating": "good", "mastery": 0.5}],
            last_reviewed_at=old, created_at=old, updated_at=old,
        ))
        db.commit()
    client.post("/api/review/rate", json={"material_id": m1["id"], "rating": "good"})

    body = client.get("/api/report").json()
    assert [s["semester"] for s in body["semesters"]] == ["2024-2025-1", "2023-2024-2"]


def test_report_unset_semester_bucket(client):
    # 无课程的资料/错题 → 「未分学期」
    m = client.post("/api/materials", json={
        "course_id": None, "title": "散落笔记", "type": "notes", "text": "x",
    }).json()
    client.post("/api/review/rate", json={"material_id": m["id"], "rating": "good"})
    client.post("/api/wrongbook", json={"course_id": None, "title": "无课程错题"})

    body = client.get("/api/report").json()
    assert len(body["semesters"]) == 1
    s = body["semesters"][0]
    assert s["semester"] == UNSET_SEMESTER
    assert s["stats"]["materials"] == 1
    assert s["stats"]["reviews"] == 1
    assert s["stats"]["wrong_total"] == 1


def test_report_legacy_state_without_history(client):
    # 跨端同步/旧库：有掌握度但没有 history 的状态也要进趋势（合成事件）
    c = _mk_course(client, "操作系统", "2024-2025-1")
    m = _mk_material(client, c["id"], "进程调度")

    from app.db import SessionLocal
    from app.models import MaterialReview

    with SessionLocal() as db:
        db.add(MaterialReview(material_id=m["id"], mastery=0.9, repetitions=3))
        db.commit()

    body = client.get("/api/report").json()
    s = body["semesters"][0]
    assert s["stats"]["materials_mastered"] == 1
    assert s["stats"]["mastery"] == pytest.approx(0.9)
    assert s["stats"]["reviews"] == 1  # 合成了一条事件
    assert s["series"][-1]["mastery"] == pytest.approx(0.9)


def test_report_markdown_export(client):
    c = _mk_course(client, "操作系统", "2024-2025-1")
    m = _mk_material(client, c["id"], "进程调度")
    client.post("/api/review/rate", json={"material_id": m["id"], "rating": "good"})

    r = client.get("/api/report/md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "studyos-report.md" in r.headers["content-disposition"]
    text = r.text
    assert "# StudyOS 学习报告" in text
    assert "## 总览" in text
    assert "## 学期：2024-2025-1" in text
    assert "平均掌握度" in text
    assert "逐周时间线" in text
