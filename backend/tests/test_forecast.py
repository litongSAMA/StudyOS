"""学习进度预测 / 负载预警：状态判定、顺序仿真、负载告警、考试撞车、异常路径。"""
from datetime import date, timedelta

import helpers


def _seed_course(client, name="操作系统", n_materials=2, difficulty="", exam_focus=False):
    c = client.post("/api/courses", json={"name": name}).json()
    mats = []
    for i in range(n_materials):
        mats.append(client.post("/api/materials", json={
            "course_id": c["id"], "title": f"{name}资料{i + 1}",
            "type": "courseware", "text": f"内容{i + 1}",
            "difficulty": difficulty, "exam_focus": exam_focus,
        }).json())
    return c, mats


def _seed_exam(client, course_id, title="期末考试", days=10, kind="exam"):
    # 用 expect_utc 换算成「本地 N 天后 23:59」的正确 UTC 时刻，
    # 保证任何时区的机器上 local_date(due_at) 都落在期望的那天
    return client.post("/api/tasks", json={
        "course_id": course_id, "title": title, "kind": kind,
        "due_at": helpers.expect_utc(helpers.expect_n_days(days)).isoformat(),
    }).json()


def test_forecast_idle_empty(client):
    r = client.get("/api/forecast")
    assert r.status_code == 200
    body = r.json()
    assert body["overall"]["status"] == "idle"
    assert body["courses"] == []
    assert body["curve"] == []
    assert len(body["load_days"]) == 21  # 默认窗口 21 天
    assert body["hours_per_day"] == 3.0


def test_forecast_on_track(client):
    c, mats = _seed_course(client)
    exam = _seed_exam(client, c["id"], days=10)

    body = client.get("/api/forecast").json()
    assert body["overall"]["status"] == "on_track"
    cf = body["courses"][0]
    assert cf["task_id"] == exam["id"]
    assert cf["course_name"] == "操作系统"
    assert cf["days_left"] == 10
    assert cf["materials_total"] == 2
    assert cf["progress_pct"] == 0.0
    # 两门课…两门资料未掌握：1.6h × 2 = 3.2h，容量 3h/天 → 2 天学完
    assert cf["predicted_finish_date"] < cf["due_date"]
    assert cf["predicted_mastery"] == 1.0
    # 曲线以 1.0 收尾且掌握度单调不减
    mastery = [p["mastery"] for p in body["curve"]]
    assert mastery == sorted(mastery)
    assert mastery[-1] == 1.0


def test_forecast_at_risk(client):
    c, _ = _seed_course(client, n_materials=5, difficulty="hard")
    _seed_exam(client, c["id"], days=1)

    body = client.get("/api/forecast").json()
    assert body["overall"]["status"] == "at_risk"
    assert body["overall"]["courses_at_risk"] == 1
    cf = body["courses"][0]
    assert cf["status"] == "at_risk"
    # 5 × 1.5 × 1.6 = 12h > 1 天 × 3h 容量 → 无法按时完成
    assert cf["predicted_finish_date"] is None
    assert cf["predicted_mastery"] < 1.0


def test_forecast_capacity_param(client):
    c, _ = _seed_course(client, n_materials=8, difficulty="hard")  # 8×1.5×1.6=19.2h
    _seed_exam(client, c["id"], days=5)

    # 容量 3h/天 → 5 天只有 15h < 19.2h → 预警；容量 6h/天 → 4 天学完 → 正常
    assert client.get("/api/forecast?hours_per_day=3").json()["courses"][0]["status"] == "at_risk"
    assert client.get("/api/forecast?hours_per_day=6").json()["courses"][0]["status"] == "on_track"

    # 参数越界 → 422
    assert client.get("/api/forecast?hours_per_day=99").status_code == 422
    assert client.get("/api/forecast?days=365").status_code == 422


def test_forecast_deadline_overload(client):
    # 同一天 4 个作业截止 → 当日负载 overload + 告警
    c, _ = _seed_course(client)
    due = helpers.expect_utc(helpers.expect_n_days(0)).isoformat()
    for i in range(4):
        client.post("/api/tasks", json={
            "course_id": c["id"], "title": f"作业{i}",
            "kind": "homework", "due_at": due,
        })

    body = client.get("/api/forecast").json()
    d0 = body["load_days"][0]
    assert d0["is_today"] is True
    assert d0["deadline_count"] == 4
    assert d0["level"] == "overload"
    assert any("DDL" in w["message"] for w in body["warnings"])
    assert body["overall"]["busy_days"] >= 1


def test_forecast_plan_and_review_load(client):
    c, mats = _seed_course(client, n_materials=2)
    exam = _seed_exam(client, c["id"], days=10)

    # 生成复习计划（今天/明天有学习块）→ 负载计入 plan_hours
    client.post("/api/review/generate", json={"exam_task_ids": [exam["id"]]})
    # 评分 → 1 天后到期间隔复习 → 负载计入 review_hours
    client.post("/api/review/rate", json={"material_id": mats[0]["id"], "rating": "good"})

    body = client.get("/api/forecast").json()
    day0 = body["load_days"][0]
    day1 = body["load_days"][1]
    assert day0["plan_hours"] > 0  # 计划从今天开始排
    assert day1["review_hours"] == 0.5  # 间隔复习 1 天后到期
    # 今日负载 = 计划学时 → busy（3h 容量内则为 ok，按实际断言合理区间）
    assert day0["level"] in ("ok", "busy")
    assert day0["total_hours"] == round(day0["plan_hours"] + day0["review_hours"], 1)


def test_forecast_busy_level(client):
    # 复习计划每天排 4h（>容量 3h、<1.5×容量 4.5h）→ busy
    c, mats = _seed_course(client, n_materials=10)
    exam = _seed_exam(client, c["id"], days=7)
    client.post("/api/review/generate", json={"exam_task_ids": [exam["id"]], "hours_per_day": 4})

    body = client.get("/api/forecast?hours_per_day=3").json()
    levels = {d["level"] for d in body["load_days"]}
    assert "busy" in levels
    assert any(w["level"] == "busy" for w in body["warnings"])


def test_forecast_exam_collision(client):
    c1, _ = _seed_course(client, name="操作系统")
    c2, _ = _seed_course(client, name="计算机网络")
    _seed_exam(client, c1["id"], title="操作系统期末", days=10)
    _seed_exam(client, c2["id"], title="计算机网络期末", days=11)

    body = client.get("/api/forecast").json()
    assert any("考试撞车" in w["message"] for w in body["warnings"])


def test_forecast_mastered_course(client):
    c, mats = _seed_course(client)
    _seed_exam(client, c["id"], days=10)
    # 连续 4 次 easy（+0.25/次）→ 掌握度 1.0 → 已掌握
    for _ in range(4):
        client.post("/api/review/rate", json={"material_id": mats[0]["id"], "rating": "easy"})
        client.post("/api/review/rate", json={"material_id": mats[1]["id"], "rating": "easy"})

    body = client.get("/api/forecast").json()
    cf = body["courses"][0]
    assert cf["status"] == "done"
    assert cf["progress_pct"] == 100.0
    assert cf["materials_mastered"] == 2
    assert body["overall"]["status"] == "done"


def test_forecast_overdue_exam_included(client):
    # 昨天过期的考试（宽限期内）→ 仍预测且预警
    c, _ = _seed_course(client, n_materials=4)
    _seed_exam(client, c["id"], days=-1)

    body = client.get("/api/forecast").json()
    assert body["courses"], "宽限期内的过期考试应参与预测"
    assert body["courses"][0]["status"] == "at_risk"
    assert body["overall"]["status"] == "at_risk"


def test_forecast_done_tasks_excluded(client):
    c, _ = _seed_course(client)
    exam = _seed_exam(client, c["id"], days=10)
    client.patch(f"/api/tasks/{exam['id']}", json={"done": True})

    body = client.get("/api/forecast").json()
    assert body["overall"]["status"] == "idle"
    assert body["courses"] == []


def test_forecast_title_regex_exam(client):
    # 标题带「期中」的普通任务也当作考试预测对象
    c, _ = _seed_course(client)
    _seed_exam(client, c["id"], title="高数期中测验", days=5, kind="other")

    body = client.get("/api/forecast").json()
    assert len(body["courses"]) == 1
