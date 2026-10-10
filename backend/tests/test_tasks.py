"""任务(DDL) CRUD、看板过滤、提醒轮询(upcoming)、逾期(overdue)。"""
from datetime import timedelta

import helpers
from app.infra.timeutil import utcnow


def _make_course(client, name="操作系统"):
    return client.post("/api/courses", json={"name": name}).json()


def test_task_crud(client):
    c = _make_course(client)
    r = client.post("/api/tasks", json={
        "course_id": c["id"], "title": "实验三报告",
        "kind": "report", "due_at": helpers.expect_n_days(5).isoformat(),
        "note": "记得提交 PDF",
    })
    assert r.status_code == 201
    t = r.json()
    assert t["done"] is False
    assert t["remind_offsets_h"] == [72, 24, 2]  # 默认提前 3天/1天/2小时
    assert t["course_name"] == "操作系统"

    # 修改：标题 + 完成状态
    r = client.patch(f"/api/tasks/{t['id']}", json={"title": "实验三报告(终版)", "done": True})
    assert r.status_code == 200
    assert r.json()["title"] == "实验三报告(终版)"
    assert r.json()["done"] is True

    # 删除
    assert client.delete(f"/api/tasks/{t['id']}").status_code == 204
    assert client.get("/api/tasks").json() == []
    assert client.delete(f"/api/tasks/{t['id']}").status_code == 404


def test_task_filters(client):
    c1 = _make_course(client, "操作系统")
    c2 = _make_course(client, "数据库")
    client.post("/api/tasks", json={
        "course_id": c1["id"], "title": "OS 作业", "kind": "homework",
        "due_at": helpers.expect_n_days(3).isoformat(),
    })
    client.post("/api/tasks", json={
        "course_id": c2["id"], "title": "DB 考试", "kind": "exam",
        "due_at": helpers.expect_n_days(6).isoformat(),
    })

    assert len(client.get("/api/tasks").json()) == 2
    assert len(client.get("/api/tasks", params={"course_id": c1["id"]}).json()) == 1
    assert len(client.get("/api/tasks", params={"kind": "exam"}).json()) == 1
    assert len(client.get("/api/tasks", params={"done": False}).json()) == 2
    assert len(client.get("/api/tasks", params={"done": True}).json()) == 0


def test_upcoming_and_overdue(client):
    c = _make_course(client)
    soon = client.post("/api/tasks", json={
        "course_id": c["id"], "title": "3小时后交",
        "due_at": (utcnow() + timedelta(hours=3)).isoformat(),
    }).json()
    later = client.post("/api/tasks", json={
        "course_id": c["id"], "title": "30天后交",
        "due_at": helpers.expect_n_days(30).isoformat(),
    }).json()
    past = client.post("/api/tasks", json={
        "course_id": c["id"], "title": "昨天该交的",
        "due_at": (utcnow() - timedelta(hours=5)).isoformat(),
    }).json()

    # 提醒轮询：未来 N 小时内到期的未完成任务
    ids = {t["id"] for t in client.get("/api/tasks/upcoming", params={"hours": 4}).json()}
    assert soon["id"] in ids
    assert later["id"] not in ids
    assert past["id"] not in ids

    # 逾期看板
    overdue = client.get("/api/tasks/overdue").json()
    assert [t["id"] for t in overdue] == [past["id"]]

    # 标记完成后从逾期与提醒中消失
    client.patch(f"/api/tasks/{past['id']}", json={"done": True})
    assert client.get("/api/tasks/overdue").json() == []


def test_task_with_unknown_course_404(client):
    r = client.post("/api/tasks", json={
        "course_id": 999, "title": "作业",
        "due_at": helpers.expect_n_days(1).isoformat(),
    })
    assert r.status_code == 404


def test_task_patch_with_unknown_course_404(client):
    t = client.post("/api/tasks", json={
        "title": "无课程任务", "due_at": helpers.expect_n_days(1).isoformat(),
    }).json()
    r = client.patch(f"/api/tasks/{t['id']}", json={"course_id": 999})
    assert r.status_code == 404
    # 原有字段未被破坏
    assert client.get(f"/api/tasks").json()[0]["course_id"] is None
