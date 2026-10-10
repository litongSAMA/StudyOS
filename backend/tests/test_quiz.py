"""AI 自测/抽背：出题引擎（离线规则确定性 / LLM 通道与降级 / 题型兜底）、
生成落库（资料/笔记/课程/自由文本四来源）、答题判分（选择题自动判分、
问答题抽背自评）、重做/统计/Markdown 导出、多端同步往返与引用重映射。"""
from __future__ import annotations

DEADLOCK_TEXT = (
    "死锁是指多个进程因竞争资源而相互等待，无法继续推进的现象。"
    "产生死锁必须同时满足四个必要条件：互斥条件、请求与保持条件、不可剥夺条件、循环等待条件。"
    "破坏其中任意一个条件即可预防死锁。"
    "银行家算法是一种避免死锁的经典算法，核心思想是在分配资源前判断系统是否处于安全状态。"
    "考试重点：银行家算法的安全性检测步骤是必考内容，常考计算题。"
    "死锁检测通过资源分配图化简实现。"
    "进程是操作系统进行资源分配和调度的基本单位。"
    "线程是处理机调度的基本单位。"
)


def _material(client, text=None, title="第3章 死锁"):
    c = client.post("/api/courses", json={"name": "操作系统"}).json()
    m = client.post("/api/materials", json={
        "course_id": c["id"], "title": title, "type": "courseware",
        "text": text or DEADLOCK_TEXT,
    }).json()
    return m


def _note(client, material_id, points=None):
    r = client.post("/api/notes", json={
        "material_id": material_id, "title": "死锁考点笔记", "mode": "exam",
        "summary": "死锁的四个必要条件与银行家算法。",
        "points": points or [
            {"point": "四个必要条件：互斥、请求保持、不可剥夺、循环等待", "importance": "high"},
            {"point": "银行家算法安全性检测是必考计算题", "importance": "high"},
        ],
        "engine": "rule",
    })
    return r.json()


# ---------------------------------------------------------------------------
# 出题引擎（纯函数）
# ---------------------------------------------------------------------------


def test_quiz_rules_engine_valid_questions():
    """离线规则引擎：选择题 4 选项且答案字母正确、问答题有参考答案、可追溯原文。"""
    from app.quizgen import generate_quiz

    r = generate_quiz(DEADLOCK_TEXT, "mixed", 5, "死锁")
    assert r["engine"] == "rule"
    assert r["title"]
    qs = r["questions"]
    assert qs, "离线规则也应出题"
    assert len(qs) <= 5
    letters = "ABCDEF"
    for q in qs:
        assert q["type"] in ("choice", "qa")
        assert q["stem"]
        if q["type"] == "choice":
            assert len(q["options"]) == 4
            assert q["answer"] == letters[q["answer_index"]]
            assert q["options"][q["answer_index"]]
            assert len(set(q["options"])) == 4, "选项不得重复"
            assert q["explanation"]
        else:
            assert not q["options"]
            assert q["answer_index"] == -1
            assert q["answer"]


def test_quiz_rules_deterministic_and_types():
    """同输入同输出（确定性）；题型要求被满足（充足素材时）。"""
    from app.quizgen import generate_quiz

    a = generate_quiz(DEADLOCK_TEXT, "mixed", 4, "死锁")
    b = generate_quiz(DEADLOCK_TEXT, "mixed", 4, "死锁")
    assert a == b

    c = generate_quiz(DEADLOCK_TEXT, "choice", 4, "死锁")
    assert all(q["type"] == "choice" for q in c["questions"])
    qa = generate_quiz(DEADLOCK_TEXT, "qa", 4, "死锁")
    assert all(q["type"] == "qa" for q in qa["questions"])

    mixed = generate_quiz(DEADLOCK_TEXT, "mixed", 6, "死锁")
    assert any(q["type"] == "choice" for q in mixed["questions"])
    assert any(q["type"] == "qa" for q in mixed["questions"])


def test_quiz_rules_fallback_and_bounds():
    """素材不足时题型兜底（题量优先）；题数钳制 1~20；空文本不崩。"""
    from app.quizgen import MAX_QUESTIONS, generate_quiz

    # 短文本凑不齐 3 个干扰项 → 选择题模式回落问答题，仍保证有题可用
    r = generate_quiz("死锁是指进程相互等待无法推进的现象。", "choice", 3)
    assert r["questions"], "短文本也不应得到空试卷"
    assert all(q["type"] == "qa" for q in r["questions"])

    assert len(generate_quiz(DEADLOCK_TEXT, "mixed", 99)["questions"]) <= MAX_QUESTIONS
    assert len(generate_quiz(DEADLOCK_TEXT, "mixed", 0)["questions"]) == 1
    assert generate_quiz("", "mixed", 3)["questions"] == []


def test_quiz_llm_channel_cleanup_and_fallback(monkeypatch):
    """LLM 可用时走模型输出并清洗；失败/输出全非法静默降级离线规则。"""
    from app import quizgen
    from app.infra.config import settings

    settings.llm_api_key = "test-key"

    mock = {"title": "AI 出题", "questions": [
        {"type": "choice", "stem": "AI 选择题", "options": ["甲", "乙", "丙", "丁"],
         "answer": "C", "explanation": "解析"},
        {"type": "qa", "stem": "AI 问答题", "answer": "参考答案要点", "explanation": ""},
        {"type": "bogus", "stem": "非法题型应被丢弃", "answer": "x"},
        {"type": "choice", "stem": "答案超范围应被丢弃", "options": ["甲", "乙"],
         "answer": "Z"},
        {"type": "qa", "stem": "无答案应被丢弃", "answer": ""},
    ]}
    monkeypatch.setattr(quizgen.llm, "chat_json", lambda *a, **k: mock)
    r = quizgen.generate_quiz(DEADLOCK_TEXT, "mixed", 5, "死锁")
    assert r["engine"] == "llm"
    assert r["title"] == "AI 出题"
    assert [q["stem"] for q in r["questions"]] == ["AI 选择题", "AI 问答题"]
    choice = r["questions"][0]
    assert choice["answer"] == "C" and choice["answer_index"] == 2

    # 题型过滤：只选选择题时 LLM 的问答输出被丢弃
    monkeypatch.setattr(quizgen.llm, "chat_json", lambda *a, **k: mock)
    r = quizgen.generate_quiz(DEADLOCK_TEXT, "choice", 5, "死锁")
    assert all(q["type"] == "choice" for q in r["questions"])

    # LLM 输出全非法 → 降级规则引擎
    monkeypatch.setattr(quizgen.llm, "chat_json", lambda *a, **k: {"questions": [
        {"type": "bogus", "stem": "x"},
        {"type": "qa", "stem": "y"},
    ]})
    r = quizgen.generate_quiz(DEADLOCK_TEXT, "mixed", 3, "死锁")
    assert r["engine"] == "rule"
    assert r["questions"]

    # 调用失败 → 降级
    monkeypatch.setattr(quizgen.llm, "chat_json", lambda *a, **k: None)
    r = quizgen.generate_quiz(DEADLOCK_TEXT, "mixed", 3, "死锁")
    assert r["engine"] == "rule"


# ---------------------------------------------------------------------------
# 生成落库：四来源
# ---------------------------------------------------------------------------


def test_quiz_create_from_material(client):
    m = _material(client)
    r = client.post("/api/quiz", json={"material_id": m["id"], "qtype": "choice", "n": 4})
    assert r.status_code == 201, r.text
    q = r.json()
    assert q["questions"], "离线规则也应出题"
    assert q["engine"] == "rule"
    assert q["qtype"] == "choice"
    assert q["material_id"] == m["id"]
    assert q["material_title"] == m["title"]
    assert q["course_id"] == m["course_id"]
    assert q["course_name"] == "操作系统"
    assert q["answered"] == 0 and q["total"] == len(q["questions"])
    assert all(x["type"] == "choice" for x in q["questions"])

    # 资料不存在 → 404；没有全文的资料 → 422 明确指引
    assert client.post("/api/quiz", json={"material_id": 9999}).status_code == 404
    empty = client.post("/api/materials", json={"title": "扫描件", "text": ""}).json()
    r = client.post("/api/quiz", json={"material_id": empty["id"]})
    assert r.status_code == 422


def test_quiz_create_from_notes_and_text(client):
    m = _material(client)
    n = _note(client, m["id"])
    r = client.post("/api/quiz", json={"note_ids": [n["id"]], "qtype": "qa", "n": 3})
    assert r.status_code == 201, r.text
    q = r.json()
    assert q["note_ids"] == [n["id"]]
    assert q["course_id"] == m["course_id"]
    assert all(x["type"] == "qa" for x in q["questions"])

    # 自由文本优先于资料/笔记；标题提示来自 title_hint
    r = client.post("/api/quiz", json={
        "text": "分页式存储管理把作业分成固定大小的页框。",
        "qtype": "qa", "n": 1, "title_hint": "内存管理",
    })
    assert r.status_code == 201, r.text
    q = r.json()
    assert q["questions"]
    assert q["note_ids"] == [] and q["material_id"] is None

    # 笔记不存在 → 404
    assert client.post("/api/quiz", json={"note_ids": [9999]}).status_code == 404


def test_quiz_create_from_course_falls_back(client):
    """只给课程：优先课程笔记，无笔记时回落课程资料全文。"""
    m = _material(client)
    r = client.post("/api/quiz", json={"course_id": m["course_id"], "n": 3})
    assert r.status_code == 201, r.text
    assert r.json()["questions"]

    _note(client, m["id"])
    r = client.post("/api/quiz", json={"course_id": m["course_id"], "n": 3})
    assert r.status_code == 201, r.text
    assert r.json()["note_ids"], "有笔记时优先用笔记出题"

    # 课程不存在 → 404；课程无笔记无资料全文 → 422
    assert client.post("/api/quiz", json={"course_id": 9999}).status_code == 404
    c = client.post("/api/courses", json={"name": "空课"}).json()
    assert client.post("/api/quiz", json={"course_id": c["id"]}).status_code == 422


def test_quiz_validation(client):
    assert client.post("/api/quiz", json={}).status_code == 422
    assert client.post("/api/quiz", json={"text": "内容", "qtype": "bogus"}).status_code == 422
    assert client.post("/api/quiz", json={"text": "内容", "n": 0}).status_code == 422
    assert client.post("/api/quiz", json={"text": "内容", "n": 21}).status_code == 422
    assert client.post("/api/quiz", json={"text": "   "}).status_code == 422


# ---------------------------------------------------------------------------
# 答题判分 / 重做 / 统计 / 列表 / 导出
# ---------------------------------------------------------------------------


def _quiz(client):
    r = client.post("/api/quiz", json={"text": DEADLOCK_TEXT, "qtype": "mixed", "n": 4})
    return r.json()


def test_quiz_answer_choice_auto_grade(client):
    q = _quiz(client)
    choice_qs = [i for i, x in enumerate(q["questions"]) if x["type"] == "choice"]
    assert choice_qs, "混合出题应含选择题"
    idx = choice_qs[0]
    item = q["questions"][idx]
    good = item["answer"]
    bad = [l for l in "ABCD" if l != good][0]

    # 答对 → correct
    r = client.post(f"/api/quiz/{q['id']}/answer", json={"index": idx, "choice": good})
    assert r.status_code == 200, r.text
    out = r.json()
    res = out["results"][0]
    assert res["verdict"] == "correct" and res["chosen"] == good
    assert out["answered"] == 1 and out["correct"] == 1

    # 重答答错 → 覆盖（Last-Write-Wins）
    r = client.post(f"/api/quiz/{q['id']}/answer", json={"index": idx, "choice": bad})
    out = r.json()
    assert len(out["results"]) == 1
    assert out["results"][0]["verdict"] == "wrong"
    assert out["wrong"] == 1 and out["correct"] == 0

    # 非法作答：越界 / 缺字母 / 字母超范围
    assert client.post(f"/api/quiz/{q['id']}/answer", json={"index": 99, "choice": "A"}).status_code == 422
    assert client.post(f"/api/quiz/{q['id']}/answer", json={"index": idx}).status_code == 422
    assert client.post(f"/api/quiz/{q['id']}/answer", json={"index": idx, "choice": "Z"}).status_code == 422
    assert client.post(f"/api/quiz/{q['id']}/answer", json={"index": idx, "choice": "a"}).status_code == 200


def test_quiz_answer_qa_self_assess(client):
    q = _quiz(client)
    qa_idx = [i for i, x in enumerate(q["questions"]) if x["type"] == "qa"][0]

    # 问答题必须自评 ok
    r = client.post(f"/api/quiz/{q['id']}/answer", json={"index": qa_idx})
    assert r.status_code == 422

    r = client.post(f"/api/quiz/{q['id']}/answer", json={
        "index": qa_idx, "ok": True, "answer_text": "我回忆的答案",
    })
    assert r.status_code == 200, r.text
    res = r.json()["results"][0]
    assert res["verdict"] == "self_ok"
    assert res["answer_text"] == "我回忆的答案"

    r = client.post(f"/api/quiz/{q['id']}/answer", json={"index": qa_idx, "ok": False})
    assert r.json()["results"][0]["verdict"] == "self_wrong"


def test_quiz_reset_stats_list_delete(client):
    q = _quiz(client)
    qid = q["id"]
    choice_qs = [i for i, x in enumerate(q["questions"]) if x["type"] == "choice"]
    client.post(f"/api/quiz/{qid}/answer",
               json={"index": choice_qs[0], "choice": q["questions"][choice_qs[0]]["answer"]})
    qa_idx = [i for i, x in enumerate(q["questions"]) if x["type"] == "qa"][0]
    client.post(f"/api/quiz/{qid}/answer", json={"index": qa_idx, "ok": False})

    s = client.get("/api/quiz/stats").json()
    assert s["total"] == 1
    assert s["answered"] == 2 and s["correct"] == 1
    assert s["choice_answered"] == 1 and s["choice_correct"] == 1
    assert s["choice_pct"] == 100
    assert s["qa_answered"] == 1 and s["qa_ok"] == 0 and s["qa_pct"] == 0
    # 自由文本自测无课程 → 归入 course_id=None 桶
    assert len(s["by_course"]) == 1
    assert s["by_course"][0]["course_id"] is None
    assert s["by_course"][0]["answered"] == 2 and s["by_course"][0]["correct"] == 1

    # 列表 + 分页头
    r = client.get("/api/quiz")
    assert r.status_code == 200
    assert r.headers["X-Total-Count"] == "1"
    assert len(r.json()) == 1

    # 重做：清空作答
    r = client.post(f"/api/quiz/{qid}/reset")
    assert r.json()["answered"] == 0 and r.json()["results"] == []

    # 详情 / 单条导出 / 全量导出
    assert client.get(f"/api/quiz/{qid}").status_code == 200
    md = client.get(f"/api/quiz/{qid}/md")
    assert md.status_code == 200
    assert "text/markdown" in md.headers["content-type"]
    assert "正确答案" in md.text
    md_all = client.get("/api/quiz/export/md")
    assert md_all.status_code == 200 and "AI 自测导出" in md_all.text

    # 删除 → 404
    assert client.delete(f"/api/quiz/{qid}").status_code == 204
    assert client.get(f"/api/quiz/{qid}").status_code == 404
    assert client.get("/api/quiz/stats").json()["total"] == 0


def test_quiz_stats_by_course(client):
    m = _material(client)
    r = client.post("/api/quiz", json={"material_id": m["id"], "n": 3})
    q = r.json()
    idx = next(i for i, x in enumerate(q["questions"]) if x["type"] == "choice")
    client.post(f"/api/quiz/{q['id']}/answer",
               json={"index": idx, "choice": q["questions"][idx]["answer"]})
    s = client.get("/api/quiz/stats").json()
    assert len(s["by_course"]) == 1
    row = s["by_course"][0]
    assert row["course_name"] == "操作系统"
    assert row["total"] == 1 and row["answered"] == 1 and row["correct"] == 1


# ---------------------------------------------------------------------------
# 多端同步往返与引用重映射
# ---------------------------------------------------------------------------


def test_quiz_sync_roundtrip_and_remap(client):
    """自测组进同步快照；replace 恢复后课程/资料/笔记引用按 uuid 重映射正确。"""
    from app.infra.db import SessionLocal
    from app.infra.models import Material, Note, Quiz
    from app.domain.sync import build_snapshot, apply_snapshot
    from sqlalchemy import select

    m = _material(client)
    n = _note(client, m["id"])
    q1 = client.post("/api/quiz", json={
        "material_id": m["id"], "note_ids": [n["id"]], "n": 3,
    }).json()
    q2 = client.post("/api/quiz", json={
        "text": "进程是操作系统进行资源分配的基本单位。", "n": 2,
    }).json()
    idx = next(i for i, x in enumerate(q1["questions"]) if x["type"] == "choice")
    client.post(f"/api/quiz/{q1['id']}/answer",
               json={"index": idx, "choice": q1["questions"][idx]["answer"]})

    with SessionLocal() as db:
        snap = build_snapshot(db)
        assert len(snap["quizzes"]) == 2
        by_title = {it["title"]: it for it in snap["quizzes"]}
        item = by_title[q1["title"]]
        assert item["questions"], "题目随快照传输"
        assert item["results"], "作答记录随快照传输"
        # 引用已是 uuid 而非本地 id
        mat = db.get(Material, m["id"])
        note = db.get(Note, n["id"])
        assert item["material_uuid"] == mat.sync_uuid
        assert item["note_uuids"] == [note.sync_uuid]

        # replace 模式整体恢复：引用重映射到新的本地 id
        apply_snapshot(db, snap, mode="replace")
        quizzes = db.execute(select(Quiz)).scalars().all()
        assert len(quizzes) == 2
        restored = next(x for x in quizzes if x.title == q1["title"])
        restored_q2 = next(x for x in quizzes if x.title == q2["title"])
        mat_new = db.execute(select(Material)).scalars().one()
        note_new = db.execute(select(Note)).scalars().one()
        assert restored.material_id == mat_new.id
        assert restored.note_ids == [note_new.id]
        assert len(restored.results) == 1

    # 删除 → 墓碑 entity=quiz（供对端合并删除）
    client.delete(f"/api/quiz/{restored_q2.id}")
    with SessionLocal() as db:
        snap = build_snapshot(db)
        entities = {t["entity"] for t in snap["tombstones"]}
        assert "quiz" in entities
