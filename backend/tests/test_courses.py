"""课程 CRUD 与异常路径。"""


def test_course_crud(client):
    # 创建
    r = client.post("/api/courses", json={
        "name": "操作系统", "code": "CS301",
        "semester": "2026-2027-1", "start_date": "2026-08-31",
        "color": "#6366f1",
    })
    assert r.status_code == 201
    c = r.json()
    assert c["name"] == "操作系统"
    assert c["code"] == "CS301"
    assert c["start_date"] == "2026-08-31"
    assert "id" in c

    # 列表
    courses = client.get("/api/courses").json()
    assert len(courses) == 1

    # 修改
    r = client.patch(f"/api/courses/{c['id']}", json={"color": "#22c55e"})
    assert r.status_code == 200
    assert r.json()["color"] == "#22c55e"

    # 删除
    assert client.delete(f"/api/courses/{c['id']}").status_code == 204
    assert client.get("/api/courses").json() == []
    assert client.delete(f"/api/courses/{c['id']}").status_code == 404


def test_course_not_found_errors(client):
    assert client.patch("/api/courses/999", json={"color": "#000000"}).status_code == 404
    assert client.delete("/api/courses/999").status_code == 404


def test_custom_course_weeks_roundtrip(client):
    """每个时段独立保存离散周次，更新和重新读取后保持一致。"""
    slots = [
        {"weekday": 1, "start": "08:00", "end": "09:40", "weeks": [1, 3, 5, 6, 7, 8, 12]},
        {"weekday": 3, "start": "10:00", "end": "11:40", "weeks": [2, 6, 60]},
    ]
    response = client.post("/api/courses", json={"name": "自定义周次课程", "slots": slots})
    assert response.status_code == 201
    course = response.json()
    assert [slot["weeks"] for slot in course["slots"]] == [s["weeks"] for s in slots]
    slots[0]["weeks"] = [1, 5, 9]
    updated = client.patch(f"/api/courses/{course['id']}", json={"slots": slots})
    assert updated.status_code == 200
    saved = client.get("/api/courses").json()[0]
    assert [slot["weeks"] for slot in saved["slots"]] == [[1, 5, 9], [2, 6, 60]]


def test_duplicate_course_name(client):
    client.post("/api/courses", json={"name": "操作系统"})
    r = client.post("/api/courses", json={"name": "操作系统"})
    assert r.status_code == 409


def test_delete_course_keeps_materials_and_tasks(client):
    """删除课程不级联删除资料/任务，只解除关联（外键约束不得 500）。"""
    c = client.post("/api/courses", json={"name": "编译原理"}).json()
    m = client.post("/api/materials", json={
        "course_id": c["id"], "title": "词法分析", "type": "courseware",
        "text": "正则与自动机",
    }).json()
    t = client.post("/api/tasks", json={
        "course_id": c["id"], "title": "编译原理大作业",
        "due_at": "2026-12-20T23:59:00",
    }).json()

    r = client.delete(f"/api/courses/{c['id']}")
    assert r.status_code == 204, r.text
    assert client.get("/api/courses").json() == []

    # 资料与任务仍在，且课程关联被解除
    mat = client.get(f"/api/materials/{m['id']}").json()
    assert mat["course_id"] is None
    assert mat["course_name"] is None
    task = client.get(f"/api/tasks").json()[0]
    assert task["id"] == t["id"]
    assert task["course_id"] is None

    # 资料全文检索不受影响
    assert client.get("/api/search", params={"q": "词法"}).json()["total"] >= 1


def test_update_course_duplicate_name_409(client):
    client.post("/api/courses", json={"name": "高等数学"})
    c2 = client.post("/api/courses", json={"name": "线性代数"}).json()
    r = client.patch(f"/api/courses/{c2['id']}", json={"name": "高等数学"})
    assert r.status_code == 409


def test_edit_course_replaces_schedule_and_preserves_identity(client):
    """右键编辑保存原课程：时段/周次替换，原课程编号与课程代码保留。"""
    created = client.post("/api/courses", json={
        "name": "原课程", "code": "CS301", "teacher": "默认老师", "location": "旧地点",
        "slots": [
            {"weekday": 1, "start": "08:00", "end": "09:40", "weeks": [1, 2, 3]},
            {"weekday": 3, "start": "10:00", "end": "11:40", "weeks": [2, 4]},
        ],
    }).json()
    slot = {"weekday": 5, "start": "14:00", "end": "15:40", "weeks": [1, 5, 9],
            "teacher": "时段老师", "location": "新地点"}
    response = client.patch(f"/api/courses/{created['id']}", json={
        "name": "修改后课程", "teacher": "", "location": "", "semester": "",
        "start_date": None, "color": "#ec4899", "slots": [slot],
    })
    assert response.status_code == 200
    saved = client.get("/api/courses").json()
    assert len(saved) == 1
    assert saved[0]["id"] == created["id"]
    assert saved[0]["code"] == "CS301"
    assert saved[0]["name"] == "修改后课程"
    assert saved[0]["slots"] == [slot]
    assert saved[0]["teacher"] == saved[0]["location"] == saved[0]["semester"] == ""
    assert saved[0]["color"] == "#ec4899"
    assert saved[0]["start_date"] is None
