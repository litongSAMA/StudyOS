"""语义检索：向量召回 + 查询扩展 + 混合排序（RRF 融合全文检索）。

后端优先级（自动选择，逐级降级）：
1. api   —— OpenAI 兼容 embedding 接口（EMBED_BASE_URL / EMBED_API_KEY / EMBED_MODEL），
           文档向量持久化到 SQLite 表 semantic_vectors，按文本哈希惰性重建；
2. tfidf —— 内置纯 Python TF-IDF 向量（jieba 分词 + 字符 n-gram），离线可用、零依赖，
           语料矩阵按「语料指纹」缓存，资料增删改后下次检索自动重建。

查询扩展（提升召回，离线在线都生效）：
- 内置课程领域同义词表（死锁→银行家算法 等）；
- 配置 LLM 时可选追加 LLM 扩展词（失败静默降级）。

设计原则与全文检索一致：断网、无 Key、无任何外部服务时依然可用。
"""
from __future__ import annotations

import hashlib
import math
import re
import struct

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domain.agent.llm import chat_json, llm_available
from app.infra.config import settings
from app.infra.models import Material
from app.infra.netutil import trust_env_for_url
from app.infra.searchutil import seg_query

# ---------------------------------------------------------------------------
# 配置解析：embedding 后端
# ---------------------------------------------------------------------------


def _resolve_api() -> tuple[str, str]:
    """返回 (base_url, model)。未配置 API 时 base_url 为空串。"""
    base = settings.embed_base_url.strip()
    model = settings.embed_model.strip()
    if base:
        return base, model
    if settings.embed_auto_ollama and "11434" in settings.llm_base_url:
        # Ollama 用户：复用 LLM 地址做 embedding，模型默认 nomic-embed-text
        base = settings.llm_base_url
        if "embed_model" not in settings.model_fields_set and not settings.embed_model_explicit:
            model = "nomic-embed-text"
    return base, model


_last_error: str = ""  # api 后端最近一次失败原因（供状态展示）


def backend_name() -> str:
    base, model = _resolve_api()
    return f"api:{model}" if base else "tfidf"


# ---------------------------------------------------------------------------
# 文本分块与哈希
# ---------------------------------------------------------------------------

_SENT_SEPS = "。！？!?\n；;"


def chunk_text(text: str, size: int = 300, overlap: int = 48) -> list[str]:
    """按句边界切块（带重叠），保证向量语义完整、摘录可读。"""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            for i in range(end, start + size // 2, -1):
                if text[i - 1] in _SENT_SEPS:
                    end = i
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _meta_text(m: Material) -> str:
    return f"{m.title or ''}。{' '.join(m.tags or [])}"


def _text_hash(m: Material) -> str:
    """文本哈希用 sha256：跨进程/重启稳定（内置 hash() 带随机盐，
    会导致每次重启后所有向量缓存失效、全量重嵌入）。"""
    h = hashlib.sha256()
    for part in (m.title or "", m.text or "", " ".join(m.tags or [])):
        h.update(part.encode("utf-8", errors="ignore"))
        h.update(b"\x00")
    return h.hexdigest()


def _doc_inputs(m: Material) -> tuple[list[str], str]:
    """返回 (待嵌入的文本块, 标题摘录)。文本块末尾追加「标题+标签」元块。"""
    chunks = chunk_text(m.text or "")
    return chunks + [_meta_text(m)], m.title or ""


# ---------------------------------------------------------------------------
# 查询扩展：同义词表（离线兜底）+ LLM（可选）
# ---------------------------------------------------------------------------

SYNONYMS: dict[str, list[str]] = {
    "死锁": ["银行家算法", "资源分配", "互斥", "进程同步", "阻塞"],
    "卡死": ["死锁", "阻塞", "资源分配"],
    "进程调度": ["调度算法", "时间片", "优先级"],
    "傅里叶": ["傅里叶变换", "频域", "频谱", "信号"],
    "编译原理": ["词法分析", "语法分析", "编译器"],
    "数据库": ["SQL", "事务", "索引"],
    "机器学习": ["模型", "训练", "神经网络", "监督学习"],
    "神经网络": ["深度学习", "反向传播", "模型"],
    "操作系统": ["进程", "内存管理", "文件系统"],
    "数据结构": ["链表", "栈", "队列", "树", "图"],
    "计算机网络": ["TCP", "IP", "协议", "网络层"],
    "考试": ["试卷", "期末", "复习", "真题"],
    "作业": ["习题", "练习"],
    "实验": ["实验报告", "实验课"],
    "高等数学": ["微积分", "极限", "导数"],
    "线性代数": ["矩阵", "特征值", "向量"],
    "概率论": ["期望", "方差", "分布"],
}

_llm_expand_cache: dict[str, list[str]] = {}


def _llm_expand(q: str) -> list[str]:
    """LLM 查询扩展（带缓存）。未配置/失败 → 空列表。"""
    if not llm_available():
        return []
    if q in _llm_expand_cache:
        return _llm_expand_cache[q]
    system = (
        "你是学习资料检索助手。把用户的自然语言查询扩展为检索关键词："
        "同义词、相关概念、课程术语。只输出 JSON：{\"terms\": [\"词1\", \"词2\"]}，"
        "最多 8 个，不要重复用户已给的词，不要编造无关内容。"
    )
    data = chat_json(system, q, max_tokens=300, timeout=min(settings.llm_timeout, 8.0))
    terms: list[str] = []
    if data and isinstance(data.get("terms"), list):
        terms = [str(t).strip() for t in data["terms"] if str(t).strip()][:8]
    _llm_expand_cache[q] = terms
    return terms


def expand_terms(q: str) -> list[str]:
    """查询扩展词：同义词表（子串匹配，不依赖分词）+ LLM（可选）。"""
    extra: list[str] = []
    for key, vals in SYNONYMS.items():
        if key in q:
            extra.extend(vals)
    extra.extend(t for t in _llm_expand(q) if t and t not in extra)
    return list(dict.fromkeys(extra))


# ---------------------------------------------------------------------------
# TF-IDF 后端（内置离线，零依赖）
# ---------------------------------------------------------------------------

_CJK = r"\u4e00-\u9fff"

# 权重：词 1.0 / 字二元组 0.7 / 单字 0.25（兜底单字查询）
_W_WORD, _W_BIGRAM, _W_UNI = 1.0, 0.7, 0.25
_EXPAND_WEIGHT = 0.6


def _tokens(t: str) -> dict[str, float]:
    """文本 → {token: weight}：jieba 词 + 字符二元组 + 单字。"""
    out: dict[str, float] = {}
    for w in seg_query(t):
        out[w] = out.get(w, 0.0) + _W_WORD
    for run in re.findall(rf"[{_CJK}]+", t):
        for i in range(len(run)):
            out[run[i]] = out.get(run[i], 0.0) + _W_UNI
            if i + 1 < len(run):
                bg = run[i:i + 2]
                out[bg] = out.get(bg, 0.0) + _W_BIGRAM
    return out


def _l2_normalize(vec: dict[str, float]) -> dict[str, float]:
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {k: v / norm for k, v in vec.items()}


class _TfidfCorpus:
    """语料矩阵：每份资料 → [(归一化块向量, 摘录), …]（含标题/标签元块）。"""

    def __init__(self) -> None:
        self.docs: dict[int, list[tuple[dict[str, float], str]]] = {}


_tfidf_cache: tuple[tuple, _TfidfCorpus] | None = None


def _fingerprint(mats: list[Material]) -> tuple:
    return tuple(sorted((m.id, m.title or "", m.text or "") for m in mats))


def _build_tfidf(mats: list[Material]) -> _TfidfCorpus:
    """全量重建 TF-IDF 语料：每份资料 = 正文块 + 标题/标签元块。"""
    corpus = _TfidfCorpus()
    df: dict[str, float] = {}
    raw: dict[int, list[tuple[dict[str, float], str]]] = {}
    for m in mats:
        chunks: list[tuple[dict[str, float], str]] = []
        for chunk in chunk_text(m.text or ""):
            toks = _tokens(chunk)
            if toks:
                chunks.append((toks, chunk))
        meta = _tokens(_meta_text(m))
        if meta:
            chunks.append((meta, m.title or ""))
        if chunks:
            raw[m.id] = chunks
            for toks, _ in chunks:
                for t in toks:
                    df[t] = df.get(t, 0.0) + 1.0
    n = max(len(raw), 1)
    idf = {t: math.log((1.0 + n) / (1.0 + df)) + 1.0 for t, df in df.items()}
    for mid, chunks in raw.items():
        corpus.docs[mid] = [
            (_l2_normalize({t: w * idf[t] for t, w in toks.items()}), snip)
            for toks, snip in chunks
        ]
    return corpus


def _get_tfidf(mats: list[Material]) -> _TfidfCorpus:
    global _tfidf_cache
    fp = _fingerprint(mats)
    if _tfidf_cache is None or _tfidf_cache[0] != fp:
        _tfidf_cache = (fp, _build_tfidf(mats))
    return _tfidf_cache[1]


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b.get(t, 0.0) for t, w in a.items())


def _tfidf_rank(mats: list[Material], q: str, top_k: int) -> list[tuple[Material, float, str]]:
    corpus = _get_tfidf(mats)
    qvec = dict(_tokens(q))
    for t in expand_terms(q):
        for tok, w in _tokens(t).items():
            qvec[tok] = qvec.get(tok, 0.0) + _EXPAND_WEIGHT * w
    qvec = _l2_normalize(qvec)
    scored: list[tuple[Material, float, str]] = []
    for m in mats:
        best, best_snip = 0.0, ""
        for vec, snip in corpus.docs.get(m.id, []):
            s = _cosine(qvec, vec)
            if s > best:
                best, best_snip = s, snip
        if best >= _MIN_SCORE:
            scored.append((m, best, best_snip))
    scored.sort(key=lambda x: -x[1])
    return scored[:top_k]


# ---------------------------------------------------------------------------
# Embedding API 后端（可选，OpenAI 兼容协议）
# ---------------------------------------------------------------------------

_EMBED_BATCH = 64
_MIN_SCORE = 0.05  # 相似度噪声阈值：过滤仅靠单字共现的弱匹配


class EmbedError(Exception):
    pass


def _embed_api(base: str, model: str, texts: list[str]) -> list[list[float]]:
    headers = {"Content-Type": "application/json"}
    if settings.embed_api_key:
        headers["Authorization"] = f"Bearer {settings.embed_api_key}"
    try:
        # 本机/内网 embedding（本地 Ollama、局域网服务）直连，不受 HTTP_PROXY 影响
        with httpx.Client(
            timeout=settings.embed_timeout, trust_env=trust_env_for_url(base)
        ) as client:
            resp = client.post(
                f"{base.rstrip('/')}/embeddings",
                json={"model": model, "input": texts},
                headers=headers,
            )
            resp.raise_for_status()
            rows = sorted(resp.json()["data"], key=lambda r: r["index"])
            return [r["embedding"] for r in rows]
    except Exception as e:  # 网络/格式错误 → 抛 EmbedError，上层降级
        raise EmbedError(f"embedding 接口失败: {e}") from e


def _pack_vecs(vecs: list[list[float]]) -> tuple[int, bytes]:
    dim = len(vecs[0]) if vecs else 0
    flat = [x for v in vecs for x in v]
    return dim, struct.pack(f"{len(flat)}f", *flat)


def _unpack_vecs(blob: bytes, dim: int) -> list[list[float]]:
    n = len(blob) // 4
    vals = struct.unpack(f"{n}f", blob)
    return [list(vals[i:i + dim]) for i in range(0, n, dim)]


def _cos_vec(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _stored_rows(db: Session, model: str) -> dict[int, tuple[list[list[float]], str]]:
    """material_id → (向量块列表, 文本哈希)。"""
    rows = db.execute(text(
        "SELECT material_id, dim, vecs, text_hash FROM semantic_vectors WHERE model = :m"
    ), {"m": model}).all()
    return {r.material_id: (_unpack_vecs(r.vecs, r.dim), r.text_hash) for r in rows}


def _api_rank(
    db: Session, mats: list[Material], q: str, top_k: int
) -> list[tuple[Material, float, str]]:
    base, model = _resolve_api()
    stored = _stored_rows(db, model)

    # 找出需要（重新）嵌入的资料：无向量或文本已变化
    pending: list[Material] = []
    for m in mats:
        row = stored.get(m.id)
        if row is None or row[1] != _text_hash(m):
            pending.append(m)
            stored.pop(m.id, None)

    for i in range(0, len(pending), _EMBED_BATCH):
        batch = pending[i:i + _EMBED_BATCH]
        inputs: list[str] = []
        for m in batch:
            inputs.extend(_doc_inputs(m)[0])
        vecs = _embed_api(base, model, inputs)
        pos = 0
        for m in batch:
            n_chunks = len(_doc_inputs(m)[0])
            chunk_vecs = vecs[pos:pos + n_chunks]
            pos += n_chunks
            dim, blob = _pack_vecs(chunk_vecs)
            db.execute(text(
                "INSERT OR REPLACE INTO semantic_vectors"
                "(material_id, model, dim, nchunks, vecs, text_hash) "
                "VALUES (:i, :m, :d, :n, :b, :h)"
            ), {"i": m.id, "m": model, "d": dim, "n": len(chunk_vecs),
                "b": blob, "h": _text_hash(m)})
            stored[m.id] = (chunk_vecs, _text_hash(m))
        db.commit()

    qvec = _embed_api(base, model, [q])[0]
    scored: list[tuple[Material, float, str]] = []
    for m in mats:
        vecs, _ = stored.get(m.id) or ([], "")
        if not vecs:
            continue
        text_chunks, title = _doc_inputs(m)
        best, best_idx = 0.0, 0
        for idx, v in enumerate(vecs):
            s = _cos_vec(qvec, v)
            if s > best:
                best, best_idx = s, idx
        if best < _MIN_SCORE:
            continue
        snip = text_chunks[best_idx] if best_idx < len(text_chunks) else title
        scored.append((m, best, snip))
    scored.sort(key=lambda x: -x[1])
    return scored[:top_k]


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------


def semantic_rank(
    db: Session, mats: list[Material], q: str, top_k: int = 50
) -> tuple[list[tuple[Material, float, str]], str]:
    """对候选资料做语义排序，返回 [(资料, 相似度, 摘录), …] 与实际引擎名。

    api 后端任何失败都静默降级到内置 tfidf，保证检索永远可用。
    """
    global _last_error
    if not mats or not q.strip():
        return [], backend_name()
    base, model = _resolve_api()
    if base:
        try:
            ranked = _api_rank(db, mats, q, top_k)
            _last_error = ""
            return ranked, f"api:{model}"
        except EmbedError as e:
            _last_error = str(e)
    return _tfidf_rank(mats, q, top_k), "tfidf"


def hybrid_fuse(fts_ids: list[int], sem_ids: list[int], limit: int) -> list[int]:
    """RRF 融合：全文排名与语义排名取倒数排名和，兼顾精确与语义。"""
    score: dict[int, float] = {}
    for rank, mid in enumerate(fts_ids):
        score[mid] = score.get(mid, 0.0) + 1.0 / (60 + rank + 1)
    for rank, mid in enumerate(sem_ids):
        score[mid] = score.get(mid, 0.0) + 1.0 / (60 + rank + 1)
    return [mid for mid, _ in sorted(score.items(), key=lambda kv: -kv[1])][:limit]


def invalidate_material(db: Session, material_id: int) -> None:
    """资料增删改时失效向量缓存（api 后端删行；tfidf 靠指纹自动失效）。"""
    db.execute(text("DELETE FROM semantic_vectors WHERE material_id = :i"),
               {"i": material_id})


def rebuild_all(db: Session) -> dict:
    """重建全量向量（api 后端预嵌入，供答辩前预热/手动刷新）。"""
    global _tfidf_cache
    base, model = _resolve_api()
    if not base:
        _tfidf_cache = None  # 指纹缓存作废，下次检索即时重建
        return {"backend": "tfidf", "note": "TF-IDF 语料在检索时即时构建，无需预计算"}

    from sqlalchemy import select

    mats = db.execute(select(Material)).scalars().all()
    db.execute(text("DELETE FROM semantic_vectors WHERE model = :m"), {"m": model})
    total, failed = 0, 0
    for i in range(0, len(mats), _EMBED_BATCH):
        batch = mats[i:i + _EMBED_BATCH]
        inputs: list[str] = []
        for m in batch:
            inputs.extend(_doc_inputs(m)[0])
        try:
            vecs = _embed_api(base, model, inputs)
        except EmbedError as e:
            global _last_error
            _last_error = str(e)
            failed += len(batch)
            continue
        pos = 0
        for m in batch:
            n_chunks = len(_doc_inputs(m)[0])
            chunk_vecs = vecs[pos:pos + n_chunks]
            pos += n_chunks
            dim, blob = _pack_vecs(chunk_vecs)
            db.execute(text(
                "INSERT OR REPLACE INTO semantic_vectors"
                "(material_id, model, dim, nchunks, vecs, text_hash) "
                "VALUES (:i, :m, :d, :n, :b, :h)"
            ), {"i": m.id, "m": model, "d": dim, "n": len(chunk_vecs),
                "b": blob, "h": _text_hash(m)})
            total += 1
        db.commit()
    return {"backend": f"api:{model}", "embedded": total, "failed": failed}


def semantic_status(db: Session) -> dict:
    base, model = _resolve_api()
    n_mats = db.execute(text("SELECT COUNT(*) FROM materials")).scalar() or 0
    if base:
        n_vecs = db.execute(text(
            "SELECT COUNT(*) FROM semantic_vectors WHERE model = :m"
        ), {"m": model}).scalar() or 0
        return {
            "backend": f"api:{model}", "model": model, "vectors": n_vecs,
            "materials": n_mats, "llm_expansion": llm_available(),
            "error": _last_error,
        }
    return {
        "backend": "tfidf", "model": "内置 TF-IDF（离线）", "vectors": n_mats,
        "materials": n_mats, "llm_expansion": llm_available(),
        "error": "",
    }
