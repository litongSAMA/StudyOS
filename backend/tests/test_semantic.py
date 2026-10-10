"""语义检索：向量召回（离线 TF-IDF）、同义词扩展、混合 RRF 融合、状态与失效。

全部用例离线运行（内置 TF-IDF 后端 + 同义词表），不依赖网络与 embedding API。
"""


def _seed(client):
    c = client.post("/api/courses", json={"name": "操作系统"}).json()
    client.post("/api/materials", json={
        "course_id": c["id"], "title": "死锁与进程同步课件",
        "type": "courseware",
        "text": "死锁产生的四个必要条件，银行家算法可以避免死锁。",
        "tags": ["死锁", "操作系统"],
    })
    client.post("/api/materials", json={
        "course_id": c["id"], "title": "银行家算法实验",
        "type": "lab",
        "text": "实现资源分配算法，判断系统是否处于安全状态，避免进程因竞争资源而阻塞。",
        "tags": ["算法", "实验"],
    })
    client.post("/api/materials", json={
        "course_id": c["id"], "title": "傅里叶变换笔记",
        "type": "notes",
        "text": "傅里叶变换将信号从时域转换到频域，方便分析频谱。",
        "tags": ["信号"],
    })
    return c


def _by_title(body, title):
    return next(m for m in body["materials"] if m["title"] == title)


def test_semantic_synonym_recall(client):
    """查询词未出现在资料原文，靠同义词/相关词仍能召回（FTS 则命中不了）。"""
    _seed(client)
    fts = client.get("/api/search", params={"q": "死锁"}).json()
    assert fts["total"] == 1  # 只有含「死锁」二字的课件

    sem = client.get("/api/search", params={"q": "死锁", "mode": "semantic"}).json()
    assert sem["total"] == 2
    assert _by_title(sem, "银行家算法实验") is not None
    assert sem["engine"].startswith("semantic:")
    # 相似度与结果一一对应，且都在 (0, 1] 区间
    assert len(sem["scores"]) == len(sem["materials"])
    assert all(isinstance(s, float) and 0 < s <= 1 for s in sem["scores"])
    # 语义摘录非空
    assert all(snip for snip in sem["snippet"])


def test_semantic_paraphrase_query(client):
    """自然语言 + 口语词查询，语义模式排最前的仍是相关内容。"""
    _seed(client)
    body = client.get(
        "/api/search", params={"q": "进程竞争资源卡死怎么办", "mode": "semantic"}
    ).json()
    assert body["total"] >= 1
    assert body["materials"][0]["title"] == "银行家算法实验"
    # 无关资料（傅里叶笔记）不应出现在结果里
    assert all(m["title"] != "傅里叶变换笔记" for m in body["materials"])


def test_hybrid_fusion(client):
    """混合模式：精确命中（全文+语义双通道）排第一，且保留 FTS 高亮。"""
    _seed(client)
    body = client.get("/api/search", params={"q": "死锁", "mode": "hybrid"}).json()
    assert body["engine"].startswith("hybrid:")
    assert body["total"] == 2
    assert body["materials"][0]["title"] == "死锁与进程同步课件"
    assert "银行家算法实验" in [m["title"] for m in body["materials"]]
    # 首位来自 FTS：带【】高亮摘录；其余语义结果附相似度
    assert "【死锁】" in body["snippet"][0]
    assert len(body["scores"]) == len(body["materials"])
    assert body["scores"][0] is not None


def test_semantic_filters(client):
    _seed(client)
    # 类型过滤
    body = client.get(
        "/api/search", params={"q": "死锁", "mode": "semantic", "type": "lab"}
    ).json()
    assert body["total"] == 1
    assert body["materials"][0]["type"] == "lab"
    # 课程过滤（无资料的课程 → 空）
    other = client.post("/api/courses", json={"name": "数据库"}).json()
    body = client.get(
        "/api/search", params={"q": "死锁", "mode": "semantic", "course_id": other["id"]}
    ).json()
    assert body["total"] == 0
    # 标签过滤
    body = client.get(
        "/api/search", params={"q": "死锁", "mode": "semantic", "tag": "算法"}
    ).json()
    assert body["total"] == 1
    assert body["materials"][0]["title"] == "银行家算法实验"


def test_semantic_single_char(client):
    """单字查询（分词困难场景）也能语义召回。"""
    _seed(client)
    body = client.get("/api/search", params={"q": "锁", "mode": "semantic"}).json()
    assert body["total"] == 1
    assert body["materials"][0]["title"] == "死锁与进程同步课件"


def test_semantic_empty_query_returns_all(client):
    _seed(client)
    body = client.get("/api/search", params={"q": "", "mode": "semantic"}).json()
    assert body["total"] == 3
    assert body["snippet"] == ["", "", ""]


def test_semantic_status_and_rebuild(client):
    _seed(client)
    st = client.get("/api/search/semantic/status").json()
    assert st["backend"] == "tfidf"          # 测试环境离线 → 内置向量后端
    assert st["llm_expansion"] is False      # 未配置 LLM → 仅同义词表
    assert st["materials"] == 3
    rb = client.post("/api/search/semantic/rebuild").json()
    assert rb["backend"] == "tfidf"


def test_semantic_updates_after_edit(client):
    """资料文本被修改后，语义检索即时反映新内容。"""
    _seed(client)
    assert client.get(
        "/api/search", params={"q": "量子计算", "mode": "semantic"}
    ).json()["total"] == 0
    cw = _by_title(
        client.get("/api/search", params={"q": "死锁与进程同步课件"}).json(),
        "死锁与进程同步课件",
    )
    client.patch(
        f"/api/materials/{cw['id']}",
        json={"text": "量子计算：量子比特与叠加态，量子纠缠。"},
    )
    body = client.get(
        "/api/search", params={"q": "量子计算", "mode": "semantic"}
    ).json()
    assert body["total"] == 1
    assert body["materials"][0]["id"] == cw["id"]


def test_semantic_deleted_material_excluded(client):
    """删除的资料立即退出语义检索。"""
    _seed(client)
    lab = _by_title(
        client.get("/api/search", params={"q": "银行家算法实验"}).json(),
        "银行家算法实验",
    )
    client.delete(f"/api/materials/{lab['id']}")
    body = client.get(
        "/api/search", params={"q": "资源分配", "mode": "semantic"}
    ).json()
    assert all(m["title"] != "银行家算法实验" for m in body["materials"])
