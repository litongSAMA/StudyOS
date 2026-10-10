"""番茄钟 / 专注计时：会话落库与校验、时间线、统计聚合、删除、旧统计迁移、首页联动。"""
from datetime import datetime, timedelta, timezone


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _days_ago(n: int) -> str:
    return _iso(_now() - timedelta(days=n))


def _local_day(dt: datetime) -> str:
    """aware UTC 时刻 → 设备本地日期（与服务端 local_date 同一口径）。"""
    return dt.astimezone().date().isoformat()


def _seed_course_task(client, name="操作系统"):
    c = client.post("/api/courses", json={"name": name}).json()
    t = client.post("/api/tasks", json={
        "course_id": c["id"], "title": f"{name}期中考试",
        "kind": "exam", "due_at": _days_ago(-10),
    }).json()
    m = client.post("/api/materials", json={
        "course_id": c["id"], "title": f"{name}课件",
        "type": "courseware", "text": "进程调度",
    }).json()
    return c, t, m


def _focus(mode="focus", **kw):
    body = {
        "mode": mode, "planned_min": 25, "duration_min": 25.0,
        "completed": True, "started_at": _now().isoformat(),
    }
    body.update(kw)
    return body


def test_create_and_list_session(client):
    c, t, m = _seed_course_task(client)
    plan = client.post("/api/review/generate", json={"exam_task_ids": [t["id"]]}).json()
    plan_date = plan["sessions"][0]["date"]

    r = client.post("/api/focus/sessions", json=_focus(
        task_id=t["id"], course_id=c["id"], plan_id=plan["id"], plan_date=plan_date,
        material_ids=[m["id"], 99999], note="死锁部分", interrupt_count=1,
    ))
    assert r.status_code == 201
    s = r.json()
    assert s["mode"] == "focus"
    assert s["task_title"] == "操作系统期中考试"
    assert s["course_name"] == "操作系统"
    assert s["plan_title"].startswith("复习计划（")
    assert s["plan_date"] == plan_date
    assert s["material_ids"] == [m["id"]], "不存在的资料应被静默过滤"
    assert s["note"] == "死锁部分"
    assert s["interrupt_count"] == 1

    lst = client.get("/api/focus/sessions")
    assert lst.status_code == 200
    assert lst.headers["X-Total-Count"] == "1"
    assert len(lst.json()) == 1


def test_session_validation(client):
    # 非法 mode / 非法日期 / 未来开始时间
    assert client.post("/api/focus/sessions", json=_focus(mode="nap")).status_code == 422
    assert client.post("/api/focus/sessions", json=_focus(plan_date="2025-13-99")).status_code == 422
    future = _iso(_now() + timedelta(hours=2))
    assert client.post("/api/focus/sessions", json=_focus(started_at=future)).status_code == 422
    # 时长边界（schema ge/le）
    assert client.post("/api/focus/sessions", json=_focus(duration_min=0.1)).status_code == 422
    assert client.post("/api/focus/sessions", json=_focus(duration_min=300)).status_code == 422
    assert client.post("/api/focus/sessions", json=_focus(planned_min=0)).status_code == 422
    # 不存在的绑定对象
    assert client.post("/api/focus/sessions", json=_focus(task_id=999)).status_code == 404
    assert client.post("/api/focus/sessions", json=_focus(course_id=999)).status_code == 404
    assert client.post("/api/focus/sessions", json=_focus(plan_id=999)).status_code == 404


def test_stats_aggregation(client):
    c, t, m = _seed_course_task(client)
    plan = client.post("/api/review/generate", json={"exam_task_ids": [t["id"]]}).json()
    pd = plan["sessions"][0]["date"]

    # 今天：完成 25 + 放弃 10；昨天完成 25；前天完成 25；大前天休息 5
    y1, y2, y3 = _now() - timedelta(days=1), _now() - timedelta(days=2), _now() - timedelta(days=3)
    k1, k2, k3 = _local_day(y1), _local_day(y2), _local_day(y3)
    client.post("/api/focus/sessions", json=_focus(course_id=c["id"], plan_id=plan["id"], plan_date=pd))
    client.post("/api/focus/sessions", json=_focus(
        course_id=c["id"], plan_id=plan["id"], plan_date=pd,
        duration_min=10.0, completed=False,
    ))
    client.post("/api/focus/sessions", json=_focus(course_id=c["id"], started_at=_iso(y1)))
    client.post("/api/focus/sessions", json=_focus(started_at=_iso(y2)))
    client.post("/api/focus/sessions", json=_focus(mode="short", planned_min=5, duration_min=5.0, started_at=_iso(y3)))

    st = client.get("/api/focus/stats").json()
    assert st["today"]["count"] == 1, "放弃的番茄不计入番茄数"
    assert st["today"]["minutes"] == 35.0, "放弃的专注时间仍计入分钟"
    assert st["streak"] == 3, "今天/昨天/前天连续专注"
    assert st["total_count"] == 3
    assert st["total_minutes"] == 85.0

    week_by_date = {d["date"]: d for d in st["week"]}
    assert len(st["week"]) == 7
    assert week_by_date[k1]["count"] == 1
    assert week_by_date[k2]["count"] == 1
    # 休息不计入任何统计
    assert week_by_date[k3]["count"] == 0
    assert week_by_date[k3]["minutes"] == 0.0

    # 课程分布：三天专注都挂在课程上（25+10+25=60 分钟，2 个完成番茄）
    assert st["by_course"][0]["course_id"] == c["id"]
    assert st["by_course"][0]["course_name"] == "操作系统"
    assert st["by_course"][0]["minutes"] == 60.0
    assert st["by_course"][0]["count"] == 2

    # 复习块投入：plan_id + plan_date 分组（今天完成 25 + 放弃 10）
    assert st["by_plan_date"] == [{
        "plan_id": plan["id"], "plan_date": pd, "count": 1, "minutes": 35.0,
    }]

    # days 参数：钳制与窗口
    assert len(client.get("/api/focus/stats?days=3").json()["week"]) == 3
    assert len(client.get("/api/focus/stats?days=0").json()["week"]) == 1
    assert len(client.get("/api/focus/stats?days=999").json()["week"]) == 90


def test_delete_session(client):
    s = client.post("/api/focus/sessions", json=_focus()).json()
    assert client.delete(f"/api/focus/sessions/{s['id']}").status_code == 204
    assert client.delete(f"/api/focus/sessions/{s['id']}").status_code == 404
    assert client.get("/api/focus/stats").json()["total_count"] == 0


def test_import_legacy(client):
    d = _local_day(_now() - timedelta(days=1))
    r = client.post("/api/focus/import-legacy", json={
        "days": [{"date": d, "count": 2, "minutes": 50}, {"date": "bad", "count": 1, "minutes": 25}],
    })
    assert r.status_code == 422, "非法日期应整体拒绝"
    assert client.get("/api/focus/stats").json()["total_count"] == 0, "不应部分写入"

    r = client.post("/api/focus/import-legacy", json={
        "days": [{"date": d, "count": 2, "minutes": 50}],
    })
    assert r.status_code == 200
    assert r.json() == {"imported": 2, "days": 1}

    rows = client.get("/api/focus/sessions").json()
    assert len(rows) == 2
    assert all(x["note"] == "旧版番茄钟统计导入" for x in rows)
    assert all(x["duration_min"] == 25.0 for x in rows)
    assert all(x["completed"] for x in rows)

    st = client.get("/api/focus/stats").json()
    assert st["total_count"] == 2
    assert st["total_minutes"] == 50.0
    assert st["week"][-1]["count"] == 0, "昨天导入不影响今天"

    # 超过上限拒绝
    r = client.post("/api/focus/import-legacy", json={
        "days": [{"date": d, "count": 1001, "minutes": 1001}],
    })
    assert r.status_code == 422


def test_misc_stats_focus_fields(client):
    client.post("/api/focus/sessions", json=_focus())
    client.post("/api/focus/sessions", json=_focus(started_at=_days_ago(1)))
    s = client.get("/api/stats").json()
    assert s["focus_count_today"] == 1
    assert s["focus_minutes_today"] == 25.0
    assert s["focus_streak"] == 2
