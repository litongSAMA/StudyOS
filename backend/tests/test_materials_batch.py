"""资料批量操作：批量删除 / 批量改标签 / 批量换课程。

覆盖：FTS 与语义向量同步、复习状态清理、笔记等来源引用置空（不炸 FK）、
幂等删除、入参校验（空 ids / 超限 / 非法模式 / 非法课程）。
"""


def _make(client, title, course_id=None, tags=None, text="正文内容"):
    return client.post("/api/materials", json={
        "course_id": course_id, "title": title, "type": "notes",
        "text": text, "tags": tags or [],
    }).json()


def test_batch_delete_and_fts_sync(client):
    c = client.post("/api/courses", json={"name": "数据库"}).json()
    a = _make(client, "事务与锁", c["id"], tags=["数据库"])
    b = _make(client, "索引优化", c["id"], tags=["数据库"])
    keep = _make(client, "MVCC 笔记", c["id"], tags=["数据库"])
    assert client.get("/api/search", params={"q": "事务"}).json()["total"] == 1

    r = client.post("/api/materials/batch/delete", json={"ids": [a["id"], b["id"]]})
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 0, "deleted": 2, "missing": 0}

    assert [m["id"] for m in client.get("/api/materials").json()] == [keep["id"]]
    # FTS 同步删除：旧词搜不到，保留的资料仍可搜
    assert client.get("/api/search", params={"q": "事务"}).json()["total"] == 0
    assert client.get("/api/search", params={"q": "MVCC"}).json()["total"] == 1
    assert client.get(f"/api/materials/{a['id']}").status_code == 404


def test_batch_delete_idempotent_and_dedupe(client):
    a = _make(client, "资料 A")
    # 重复 id 去重：只删一次；不存在的 id 计入 missing
    r = client.post("/api/materials/batch/delete", json={"ids": [a["id"], a["id"]]})
    assert r.json() == {"updated": 0, "deleted": 1, "missing": 0}
    r = client.post("/api/materials/batch/delete", json={"ids": [a["id"], 9999]})
    assert r.json() == {"updated": 0, "deleted": 0, "missing": 2}


def test_batch_delete_keeps_notes_and_cleans_review(client):
    """资料删了，笔记保留（material_id 置空）、复习状态清理、任务来源引用置空。"""
    c = client.post("/api/courses", json={"name": "操作系统"}).json()
    m = _make(client, "死锁课件", c["id"], text="死锁的四个必要条件与银行家算法")
    note = client.post("/api/notes", json={
        "material_id": m["id"], "title": "死锁笔记", "mode": "keypoints",
        "summary": "四个必要条件", "points": [{"point": "互斥条件", "importance": "high"}],
        "engine": "rule",
    }).json()
    client.post("/api/review/rate", json={"material_id": m["id"], "rating": "good"})

    r = client.post("/api/materials/batch/delete", json={"ids": [m["id"]]})
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 1

    # 笔记保留，来源置空；复习状态随资料删除
    n = client.get(f"/api/notes/{note['id']}").json()
    assert n["material_id"] is None and n["title"] == "死锁笔记"
    assert client.get("/api/review/states").json() == []


def test_batch_tags_add_remove_set(client):
    a = _make(client, "资料 A", tags=["数据库"])
    b = _make(client, "资料 B", tags=["数据库"])
    keep = _make(client, "资料 C", tags=["其他"])

    # 追加：A/B 加上「期末」，已有标签不重复
    r = client.post("/api/materials/batch/tags", json={
        "ids": [a["id"], b["id"]], "tags": ["期末", "数据库"], "mode": "add",
    })
    assert r.json() == {"updated": 2, "deleted": 0, "missing": 0}
    tags_a = client.get(f"/api/materials/{a['id']}").json()["tags"]
    assert set(tags_a) == {"数据库", "期末"}
    # 标签进 FTS：按新标签能搜到
    assert client.get("/api/search", params={"q": "期末"}).json()["total"] == 2

    # 移除：A/B 去掉「数据库」
    client.post("/api/materials/batch/tags", json={
        "ids": [a["id"], b["id"]], "tags": ["数据库"], "mode": "remove",
    })
    assert client.get(f"/api/materials/{a['id']}").json()["tags"] == ["期末"]
    assert client.get("/api/search", params={"q": "期末"}).json()["total"] == 2

    # 覆盖：B 标签整体换成「实验报告」，A 清空
    client.post("/api/materials/batch/tags", json={
        "ids": [b["id"]], "tags": ["实验报告"], "mode": "set",
    })
    client.post("/api/materials/batch/tags", json={
        "ids": [a["id"]], "tags": [], "mode": "set",
    })
    assert client.get(f"/api/materials/{b['id']}").json()["tags"] == ["实验报告"]
    assert client.get(f"/api/materials/{a['id']}").json()["tags"] == []
    # 未选中的资料不受影响
    assert client.get(f"/api/materials/{keep['id']}").json()["tags"] == ["其他"]


def test_batch_course_move_and_uncategorized(client):
    c1 = client.post("/api/courses", json={"name": "操作系统"}).json()
    c2 = client.post("/api/courses", json={"name": "机器学习"}).json()
    a = _make(client, "OS 课件", c1["id"])
    b = _make(client, "OS 实验", c1["id"])
    keep = _make(client, "OS 作业", c1["id"])

    r = client.post("/api/materials/batch/course", json={
        "ids": [a["id"], b["id"]], "course_id": c2["id"],
    })
    assert r.json() == {"updated": 2, "deleted": 0, "missing": 0}
    assert client.get(f"/api/materials/{a['id']}").json()["course_name"] == "机器学习"
    assert client.get(f"/api/materials/{keep['id']}").json()["course_name"] == "操作系统"

    # 移到未分类（course_id=None）
    client.post("/api/materials/batch/course", json={
        "ids": [a["id"]], "course_id": None,
    })
    out = client.get(f"/api/materials/{a['id']}").json()
    assert out["course_id"] is None and out["course_name"] is None

    # 不存在的课程 → 404；已归属同课程 → updated 不计
    assert client.post("/api/materials/batch/course", json={
        "ids": [b["id"]], "course_id": 9999,
    }).status_code == 404
    r = client.post("/api/materials/batch/course", json={
        "ids": [b["id"]], "course_id": c2["id"],
    })
    assert r.json()["updated"] == 0


def test_batch_validation(client):
    # 空 ids / 超限 → 422
    assert client.post("/api/materials/batch/delete", json={"ids": []}).status_code == 422
    too_many = list(range(501))
    assert client.post("/api/materials/batch/delete", json={"ids": too_many}).status_code == 422
    # 非法标签模式 → 422；add/remove 空 tags → 422（set 空 tags = 清空，合法）
    assert client.post("/api/materials/batch/tags", json={
        "ids": [1], "mode": "bogus", "tags": ["x"],
    }).status_code == 422
    assert client.post("/api/materials/batch/tags", json={
        "ids": [1], "mode": "add", "tags": [],
    }).status_code == 422


def test_batch_fields_set_chapter_difficulty_examfocus_type(client):
    """批量归档设置：章节/难度/考试重点/类型一次生效，并同步进全文索引。"""
    a = _make(client, "资料 A", tags=["数据库"])
    b = _make(client, "资料 B", tags=["数据库"])
    keep = _make(client, "资料 C")

    r = client.post("/api/materials/batch/fields", json={
        "ids": [a["id"], b["id"]],
        "chapter": "第3章 内存管理",
        "difficulty": "hard",
        "exam_focus": True,
        "type": "courseware",
    })
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 2, "deleted": 0, "missing": 0}

    for mid in (a["id"], b["id"]):
        out = client.get(f"/api/materials/{mid}").json()
        assert out["chapter"] == "第3章 内存管理"
        assert out["difficulty"] == "hard"
        assert out["exam_focus"] is True
        assert out["type"] == "courseware"
    # 未选中的资料不受影响
    k = client.get(f"/api/materials/{keep['id']}").json()
    assert k["chapter"] == "" and k["difficulty"] == "" and k["exam_focus"] is False and k["type"] == "notes"
    # 章节进全文索引（章节是 FTS meta 字段）
    assert client.get("/api/search", params={"q": "内存管理"}).json()["total"] == 2
    # 难度/考试重点过滤生效（总数在响应头 X-Total-Count）
    assert client.get("/api/materials", params={"difficulty": "hard"}).headers["X-Total-Count"] == "2"
    assert client.get("/api/materials", params={"exam_focus": "true"}).headers["X-Total-Count"] == "2"


def test_batch_fields_clear_and_partial(client):
    """部分字段不传 = 不改；空字符串 = 清空章节/难度；无实际变化不计 updated。"""
    a = _make(client, "资料 A")
    client.post("/api/materials/batch/fields", json={
        "ids": [a["id"]], "chapter": "第2章 进程", "difficulty": "medium", "exam_focus": True,
    })
    # 只清空难度，其它字段保持
    r = client.post("/api/materials/batch/fields", json={
        "ids": [a["id"]], "difficulty": "",
    })
    assert r.json()["updated"] == 1
    out = client.get(f"/api/materials/{a['id']}").json()
    assert out["difficulty"] == "" and out["chapter"] == "第2章 进程" and out["exam_focus"] is True

    # 全部字段 None（不改任何字段）且无实际变化 → updated 0
    r = client.post("/api/materials/batch/fields", json={"ids": [a["id"]]})
    assert r.json() == {"updated": 0, "deleted": 0, "missing": 0}
    # 清空章节
    client.post("/api/materials/batch/fields", json={"ids": [a["id"]], "chapter": ""})
    assert client.get(f"/api/materials/{a['id']}").json()["chapter"] == ""
    # 不存在的 id 计入 missing
    r = client.post("/api/materials/batch/fields", json={"ids": [a["id"], 9999], "exam_focus": False})
    assert r.json()["updated"] == 1 and r.json()["missing"] == 1
    assert client.get(f"/api/materials/{a['id']}").json()["exam_focus"] is False
    # 非法难度 → 422（与单条 PATCH 同一套校验）
    assert client.post("/api/materials/batch/fields", json={
        "ids": [a["id"]], "difficulty": "insane",
    }).status_code == 422
