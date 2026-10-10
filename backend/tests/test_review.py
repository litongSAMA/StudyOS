"""复习清单：生成（DDL 倒排 + 挂资料）、列表/详情/删除、异常路径。"""
from datetime import date

import helpers


def _seed_exam(client, days=10, name="操作系统"):
    c = client.post("/api/courses", json={"name": name}).json()
    m1 = client.post("/api/materials", json={
        "course_id": c["id"], "title": f"{name}课件：进程调度",
        "type": "courseware", "text": "进程调度", "tags": ["进程"],
    }).json()
    m2 = client.post("/api/materials", json={
        "course_id": c["id"], "title": f"{name}复习笔记",
        "type": "notes", "text": "内存管理", "tags": ["内存"],
    }).json()
    exam = client.post("/api/tasks", json={
        "course_id": c["id"], "title": f"{name}期中考试",
        "kind": "exam", "due_at": helpers.expect_n_days(days).isoformat(),
    }).json()
    return c, m1, m2, exam


def test_generate_plan_attaches_materials(client):
    c, m1, m2, exam = _seed_exam(client)

    r = client.post("/api/review/generate", json={"exam_task_ids": [exam["id"]]})
    assert r.status_code == 201
    plan = r.json()
    assert plan["title"].startswith("复习计划（")
    assert plan["exam_task_ids"] == [exam["id"]]

    sessions = plan["sessions"]
    assert sessions, "应生成至少一个复习块"
    first = sessions[0]
    # 从今天开始，按 DDL 倒排
    assert first["date"] == date.today().isoformat()
    assert first["course"] == "操作系统"
    # 每块附对应资料（可直接打开）
    assert set(first["material_ids"]) == {m1["id"], m2["id"]}
    assert first["hours"] > 0
    # 日期单调递增
    dates = [s["date"] for s in sessions]
    assert dates == sorted(dates)


def test_plan_lifecycle(client):
    _, _, _, exam = _seed_exam(client)
    plan = client.post("/api/review/generate", json={"exam_task_ids": [exam["id"]]}).json()

    plans = client.get("/api/review").json()
    assert len(plans) == 1
    assert client.get(f"/api/review/{plan['id']}").status_code == 200
    assert client.delete(f"/api/review/{plan['id']}").status_code == 204
    assert client.get("/api/review").json() == []
    assert client.delete(f"/api/review/{plan['id']}").status_code == 404


def test_generate_errors(client):
    # 空选择 / 不存在的任务
    assert client.post("/api/review/generate", json={"exam_task_ids": []}).status_code == 422
    assert client.post("/api/review/generate", json={"exam_task_ids": [999]}).status_code == 422

    # 考试已过期 → 拒绝生成
    _, _, _, exam = _seed_exam(client, days=-1)
    r = client.post("/api/review/generate", json={"exam_task_ids": [exam["id"]]})
    assert r.status_code == 422
