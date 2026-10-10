"""检索：FTS5 全文 + 高亮摘录、LIKE 兜底、组合过滤。"""


def _seed(client):
    c = client.post("/api/courses", json={"name": "操作系统"}).json()
    client.post("/api/materials", json={
        "course_id": c["id"], "title": "操作系统课件：进程调度与死锁",
        "type": "courseware", "text": "进程调度与死锁，08月22日课堂上讲",
        "tags": ["操作系统", "进程", "死锁"],
    })
    client.post("/api/materials", json={
        "course_id": c["id"], "title": "操作系统实验三",
        "type": "lab", "text": "实现银行家算法", "tags": ["算法"],
    })
    return c


def test_fts_search_with_highlight(client):
    _seed(client)
    r = client.get("/api/search", params={"q": "死锁"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["materials"][0]["title"].startswith("操作系统课件")
    # 高亮摘录带【】标记
    assert "【死锁】" in body["snippet"][0]


def test_like_fallback_for_short_terms(client):
    """单字「锁」FTS 无 token 命中 → LIKE 兜底仍能召回。"""
    _seed(client)
    body = client.get("/api/search", params={"q": "锁"}).json()
    assert body["total"] >= 1
    assert all(s == "" for s in body["snippet"])  # LIKE 兜底无摘录


def test_search_no_result(client):
    _seed(client)
    assert client.get("/api/search", params={"q": "傅里叶"}).json()["total"] == 0


def test_search_special_chars_no_500(client):
    """FTS5 语法字符（引号等）出现在查询词中不得 500。"""
    _seed(client)
    for q in ['"', '死"锁', 'a"b', "OR", "NEAR", "(-)"]:
        r = client.get("/api/search", params={"q": q})
        assert r.status_code == 200, f"q={q!r} 返回 {r.status_code}"
        assert isinstance(r.json()["total"], int)


def test_search_filters(client):
    c = _seed(client)
    # 课程过滤
    assert client.get("/api/search", params={"q": "", "course_id": c["id"]}).json()["total"] == 2
    # 类型过滤
    assert client.get("/api/search", params={"q": "", "type": "lab"}).json()["total"] == 1
    # 标签过滤（空查询时生效）
    assert client.get("/api/search", params={"q": "", "tag": "进程"}).json()["total"] == 1
    # 关键词 + 课程组合
    assert client.get("/api/search", params={"q": "算法", "course_id": c["id"]}).json()["total"] == 1


def test_empty_query_returns_all(client):
    _seed(client)
    body = client.get("/api/search", params={"q": ""}).json()
    assert body["total"] == 2
    assert body["snippet"] == ["", ""]
