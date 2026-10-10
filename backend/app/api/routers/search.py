"""检索路由：全文（jieba + FTS5 + LIKE 兜底）· 语义（向量召回）· 混合（RRF 融合）。

过滤（课程 / 类型 / 标签）一律**下推到 SQL**：
- 标签在 JSON 列里用 `json_each()` 展开做 EXISTS 匹配（见 materialquery.py）；
- FTS5 命中与过滤条件在同一条 SQL 里 JOIN，`ORDER BY bm25 + LIMIT` 作用于过滤之后，
  因此「按标签筛选后结果变少/丢失」的截断问题不会出现；
- `total` 是过滤后的真实命中总数，`returned`/`truncated` 说明本次返回是否被 limit 截断。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Float, Integer, String, func, or_, select, text
from sqlalchemy.orm import Session

from app.infra.db import get_db
from app.infra.materialquery import (
    MaterialFilter,
    conditions,
    filter_ids,
    order_recent,
    page_materials,
)
from app.infra.materialquery import candidates as filtered_candidates
from app.infra.models import Material
from app.api.schemas import SearchResult
from app.infra.searchutil import clean_snippet, seg_query
from app.domain.semantic import (
    hybrid_fuse,
    rebuild_all,
    semantic_rank,
    semantic_status,
)
from app.api.serializers import course_names, material_out

router = APIRouter(prefix="/api/search", tags=["search"])

# FTS5 命中子查询：id + 高亮摘录 + bm25 排序分。作为子查询与 materials JOIN，
# 过滤条件因此能进 SQL（snippet/bm25 在 MATCH 所在的子查询内求值，合法）。
FTS_SQL = """
    SELECT rowid AS id,
           snippet(materials_fts, 1, '【', '】', '…', 14) AS snip,
           bm25(materials_fts) AS rank
    FROM materials_fts
    WHERE materials_fts MATCH :q
"""

MODES = {"fts", "semantic", "hybrid"}

# 语义检索候选上限：向量召回需要把候选文本读进内存，超大资料库时兜住算力/内存。
# 命中该上限时 truncated=True，前端可提示「缩小筛选范围」。
SEMANTIC_SCAN_LIMIT = 3000
# JSON1 缺失的兜底路径下，FTS/LIKE 先取回的扫描窗口（之后在内存里补标签过滤）
FALLBACK_SCAN = 1000


@dataclass
class Hits:
    """检索命中：已过滤 + 已排序的 id、摘录、过滤后总命中数。"""

    ids: list[int] = field(default_factory=list)
    snips: dict[int, str] = field(default_factory=dict)
    total: int = 0


def _fts_subquery():
    return text(FTS_SQL).columns(id=Integer, snip=String, rank=Float).subquery("fts")


def _match_expr(q: str) -> tuple[str, list[str]]:
    """构造 FTS5 MATCH 表达式：分词 → 短语 OR。词内双引号需转义成 ""，否则语法错误。"""
    terms = [t.replace('"', '""') for t in seg_query(q)]
    return (" OR ".join(f'"{t}"' for t in terms) if terms else ""), terms


def _fts_matched_anything(db: Session, fts_q: str) -> bool:
    """未加过滤条件时 FTS 是否有命中——决定是否启用 LIKE 兜底（保持既有语义：
    FTS 本身命中但被过滤器排空时，不再退化成 LIKE 扫描）。"""
    row = db.execute(
        text("SELECT 1 FROM materials_fts WHERE materials_fts MATCH :q LIMIT 1"),
        {"q": fts_q},
    ).first()
    return row is not None


def _fts_hits(db: Session, q: str, f: MaterialFilter, limit: int) -> Hits:
    fts_q, terms = _match_expr(q)
    if not fts_q:
        return Hits()
    conds, pushed = conditions(db, f)
    needs_memory_tags = not pushed and f.has_tags
    fetch = FALLBACK_SCAN if needs_memory_tags else limit

    sub = _fts_subquery()
    base = (
        select(sub.c.id, sub.c.snip)
        .join(Material, Material.id == sub.c.id)
        .where(*conds)
    )
    rows = db.execute(base.order_by(sub.c.rank).limit(fetch), {"q": fts_q}).all()
    total = int(
        db.execute(select(func.count()).select_from(base.subquery()), {"q": fts_q}).scalar_one()
    )
    if needs_memory_tags:
        keep = filter_ids(db, f, [r.id for r in rows])
        rows = [r for r in rows if r.id in keep]
        total = len(rows)
    if rows:
        ids = [int(r.id) for r in rows][:limit]
        return Hits(ids=ids, snips={int(r.id): clean_snippet(r.snip or "") for r in rows}, total=total)
    if _fts_matched_anything(db, fts_q):
        return Hits(total=total)  # FTS 有命中、只是被过滤器排空 → 不启用 LIKE 兜底

    # FTS 无命中（短词/新词/未登录词）→ LIKE 兜底扫描，过滤条件同样在 SQL 内
    if not terms:
        return Hits()
    like = or_(*[
        Material.title.like(f"%{t}%") | Material.text.like(f"%{t}%")
        for t in terms[:8]
    ])
    stmt = order_recent(select(Material.id).where(like, *conds)).limit(fetch)
    ids = [int(x) for x in db.execute(stmt).scalars().all()]
    like_total = int(
        db.execute(select(func.count(Material.id)).where(like, *conds)).scalar_one()
    )
    if needs_memory_tags:
        keep = filter_ids(db, f, ids)
        ids = [i for i in ids if i in keep]
        like_total = len(ids)
    return Hits(ids=ids[:limit], snips={}, total=like_total)


def _materials_in_order(db: Session, ids: list[int]) -> list[Material]:
    if not ids:
        return []
    mats = db.execute(select(Material).where(Material.id.in_(ids))).scalars().all()
    order = {mid: i for i, mid in enumerate(ids)}
    return sorted(mats, key=lambda m: order.get(m.id, len(order)))


def _fts_result(
    db: Session, q: str, f: MaterialFilter, limit: int, names: dict[int, str]
) -> SearchResult:
    """全文检索路径（mode=fts 与空查询共用）。"""
    if not q:
        page = page_materials(db, f, limit=limit)
        return SearchResult(
            materials=[material_out(db, m, names) for m in page.items],
            total=page.total,
            snippet=[""] * len(page.items),
            returned=len(page.items),
            truncated=page.has_more,
            engine="fts",
        )

    hits = _fts_hits(db, q, f, limit)
    mats = _materials_in_order(db, hits.ids)
    return SearchResult(
        materials=[material_out(db, m, names) for m in mats],
        total=hits.total,
        snippet=[hits.snips.get(m.id, "") for m in mats],
        returned=len(mats),
        truncated=hits.total > len(mats),
        engine="fts",
    )


@router.get("", response_model=SearchResult)
def search(
    q: str,
    course_id: int | None = None,
    type: str | None = None,
    chapter: str | None = None,
    difficulty: str | None = None,
    exam_focus: bool | None = None,
    tag: str | None = None,
    tags: list[str] | None = Query(default=None, description="多标签过滤，可重复传或用逗号分隔"),
    tag_mode: str = Query(default="any", pattern="^(any|all)$", description="多标签：any=任一 / all=全部"),
    mode: str = "fts",
    limit: int = 50,
    db: Session = Depends(get_db),
):
    q = q.strip()
    if mode not in MODES:
        mode = "fts"
    limit = max(1, min(limit, 500))
    names = course_names(db)
    f = MaterialFilter.build(
        course_id=course_id, type=type, chapter=chapter, difficulty=difficulty,
        exam_focus=exam_focus, tag=tag, tags=tags, tag_mode=tag_mode,
    )

    # 空查询与全文模式走全文路径
    if not q or mode == "fts":
        return _fts_result(db, q, f, limit, names)

    cands = filtered_candidates(db, f, limit=SEMANTIC_SCAN_LIMIT + 1)
    scan_truncated = len(cands) > SEMANTIC_SCAN_LIMIT
    cands = cands[:SEMANTIC_SCAN_LIMIT]
    ranked, engine = semantic_rank(db, cands, q, top_k=limit)
    if mode == "semantic":
        return SearchResult(
            materials=[material_out(db, m, names) for m, _, _ in ranked],
            total=len(ranked),
            snippet=[_clip(s) for _, _, s in ranked],
            scores=[round(s, 4) for _, s, _ in ranked],
            returned=len(ranked),
            truncated=scan_truncated,
            engine=f"semantic:{engine}",
        )

    # 混合：RRF 融合全文排名与语义排名
    hits = _fts_hits(db, q, f, limit)
    fused = hybrid_fuse(hits.ids, [m.id for m, _, _ in ranked], limit)
    score_by_id = {m.id: round(s, 4) for m, s, _ in ranked}
    sem_snip = {m.id: _clip(s) for m, _, s in ranked}
    mats = _materials_in_order(db, fused)
    return SearchResult(
        materials=[material_out(db, m, names) for m in mats],
        total=len(mats),
        snippet=[hits.snips.get(m.id) or sem_snip.get(m.id, "") for m in mats],
        scores=[score_by_id.get(m.id) for m in mats],
        returned=len(mats),
        truncated=scan_truncated or hits.total > len(hits.ids),
        engine=f"hybrid:{engine}",
    )


def _clip(s: str, n: int = 200) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


@router.get("/semantic/status")
def status(db: Session = Depends(get_db)):
    """语义检索引擎状态：后端、模型、向量缓存数量。"""
    return semantic_status(db)


@router.post("/semantic/rebuild")
def rebuild(db: Session = Depends(get_db)):
    """重建语义向量索引（embedding API 后端预嵌入；内置 TF-IDF 即时构建）。"""
    return rebuild_all(db)
