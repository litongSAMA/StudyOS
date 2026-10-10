"""资料 CRUD 与 FTS 索引同步（新增/修改/删除后检索结果一致）。"""
import helpers


def test_material_crud_and_fts_sync(client):
    c = client.post("/api/courses", json={"name": "数据库"}).json()

    # 创建 → FTS 立即可检索
    m = client.post("/api/materials", json={
        "course_id": c["id"], "title": "事务与锁",
        "type": "notes", "text": "事务隔离级别与死锁分析",
        "tags": ["数据库", "事务"],
    }).json()
    assert client.get("/api/search", params={"q": "事务"}).json()["total"] >= 1

    # 详情
    r = client.get(f"/api/materials/{m['id']}")
    assert r.status_code == 200
    assert r.json()["course_name"] == "数据库"

    # 修改 → 旧词消失、新词可检索（FTS 同步更新）
    client.patch(f"/api/materials/{m['id']}", json={
        "title": "并发控制笔记", "text": "MVCC 与乐观锁", "tags": ["并发"],
    })
    s = client.get("/api/search", params={"q": "MVCC"}).json()
    assert s["total"] >= 1
    assert client.get("/api/search", params={"q": "事务"}).json()["total"] == 0

    # 删除 → FTS 行同步删除，检索不到
    assert client.delete(f"/api/materials/{m['id']}").status_code == 204
    assert client.get("/api/search", params={"q": "MVCC"}).json()["total"] == 0
    assert client.get(f"/api/materials/{m['id']}").status_code == 404


def test_material_list_filters(client):
    c1 = client.post("/api/courses", json={"name": "操作系统"}).json()
    c2 = client.post("/api/courses", json={"name": "机器学习"}).json()
    client.post("/api/materials", json={
        "course_id": c1["id"], "title": "OS 课件", "type": "courseware",
        "text": "进程调度", "tags": ["进程"],
    })
    client.post("/api/materials", json={
        "course_id": c2["id"], "title": "ML 笔记", "type": "notes",
        "text": "梯度下降", "tags": ["机器学习"],
    })

    assert len(client.get("/api/materials").json()) == 2
    assert len(client.get("/api/materials", params={"course_id": c1["id"]}).json()) == 1
    assert len(client.get("/api/materials", params={"type": "notes"}).json()) == 1
    assert len(client.get("/api/materials", params={"tag": "进程"}).json()) == 1


def test_material_without_course(client):
    m = client.post("/api/materials", json={
        "title": "未分类笔记", "type": "notes", "text": "随手记",
    }).json()
    assert m["course_id"] is None
    assert m["course_name"] is None


def test_material_unknown_course_404(client):
    """外键校验：不存在的课程返回 404 而非数据库 500。"""
    r = client.post("/api/materials", json={
        "course_id": 999, "title": "悬空资料", "type": "notes", "text": "x",
    })
    assert r.status_code == 404

    m = client.post("/api/materials", json={
        "title": "未分类", "type": "notes", "text": "x",
    }).json()
    r = client.patch(f"/api/materials/{m['id']}", json={"course_id": 999})
    assert r.status_code == 404
