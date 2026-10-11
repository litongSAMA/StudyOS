"""资料查询过滤：把「课程 / 类型 / 标签」过滤统一下推到 SQL。

**为什么不在内存里过滤标签**：标签存在 JSON 列里，早期实现是「先 `LIMIT` 取一页、
再在 Python 里按标签筛」——资料量一大就出两种错：页内不含该标签的行被丢掉（返回条数
莫名变少）、页外命中该标签的行永远取不到（结果不完整），`total` 也不可信。

现在的做法：用 SQLite `json_each()` 把标签数组展开，做 `EXISTS` 子查询判断，
**过滤、计数、排序、分页全部在数据库内完成**，`LIMIT` 永远作用在「过滤之后」。
标签聚合（标签云）同样走 SQL `GROUP BY`，因此标签云覆盖全库而不是当前这一页。

极端环境兜底：自编译 SQLite 若缺少 JSON1 扩展，自动降级为「按标量条件取回 → 内存
过滤 → 过滤后切页」，慢一些但结果依旧完整正确（绝不再出现「先 limit 后过滤」）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from .models import Material

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
MAX_TAGS = 20            # 单次查询最多接受的标签数（防超长 URL / 病态 SQL）
MAX_FACETS = 200         # 标签云最多返回的标签数
_IN_CHUNK = 500          # id IN (...) 分批大小，避免触碰 SQLite 变量上限

TAG_MODES = ("any", "all")

_json1_ready: bool | None = None  # JSON1 可用性探测结果（进程级缓存）


# ---------------------------------------------------------------------------
# 过滤条件
# ---------------------------------------------------------------------------


def parse_tags(tag: str | None = None, tags: Sequence[str] | None = None) -> tuple[str, ...]:
    """归一化标签入参：支持 `tag=a`、`tags=a&tags=b`、`tags=a,b` 混用，去重保序。"""
    raw: list[str] = []
    if tag:
        raw.append(tag)
    for item in tags or []:
        raw.extend(str(item).split(","))
    out: list[str] = []
    for t in raw:
        t = t.strip()
        if t and t not in out:
            out.append(t)
    return tuple(out[:MAX_TAGS])


def normalize_tag_mode(mode: str | None) -> str:
    mode = (mode or "any").strip().lower()
    return mode if mode in TAG_MODES else "any"


@dataclass(frozen=True)
class MaterialFilter:
    """资料过滤条件（课程 / 类型 / 章节 / 难度 / 考试重点 / 标签集合 + 标签匹配模式）。"""

    course_id: int | None = None
    type: str | None = None
    chapter: str | None = None
    difficulty: str | None = None
    exam_focus: bool | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    tag_mode: str = "any"

    @classmethod
    def build(
        cls,
        course_id: int | None = None,
        type: str | None = None,
        chapter: str | None = None,
        difficulty: str | None = None,
        exam_focus: bool | None = None,
        tag: str | None = None,
        tags: Sequence[str] | None = None,
        tag_mode: str | None = "any",
    ) -> MaterialFilter:
        return cls(
            course_id=course_id,
            type=(type or "").strip() or None,
            chapter=(chapter or "").strip() or None,
            difficulty=(difficulty or "").strip() or None,
            exam_focus=True if exam_focus else None,
            tags=parse_tags(tag, tags),
            tag_mode=normalize_tag_mode(tag_mode),
        )

    @property
    def has_tags(self) -> bool:
        return bool(self.tags)


# ---------------------------------------------------------------------------
# SQL 条件构造
# ---------------------------------------------------------------------------


def json1_ready(db: Session) -> bool:
    """探测 SQLite 是否带 JSON1（json_each/json_valid）——结果进程级缓存。"""
    global _json1_ready
    if _json1_ready is None:
        try:
            db.execute(select(func.json_valid("[]"))).scalar_one()
            je = func.json_each(func.json('["x"]')).table_valued("value")
            db.execute(select(je.c.value)).all()
            _json1_ready = True
        except DatabaseError:
            _json1_ready = False
        except Exception:  # 驱动差异导致的其它异常同样视为不可用
            _json1_ready = False
    return _json1_ready


def tag_condition(tag: str) -> ColumnElement[bool]:
    """单个标签的 SQL 条件：json_each 展开后 EXISTS 精确匹配（非子串匹配）。

    `json_valid` 前置守卫：历史脏数据（非法 JSON）不会让整条查询报 malformed JSON。
    """
    values = func.json_each(Material.tags).table_valued("value")
    return and_(
        func.json_valid(Material.tags),
        select(1).select_from(values).where(values.c.value == tag).exists(),
    )


def scalar_conditions(f: MaterialFilter) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = []
    if f.course_id is not None:
        conds.append(Material.course_id == f.course_id)
    if f.type:
        conds.append(Material.type == f.type)
    if f.chapter:
        # 章节过滤用包含匹配（「第3章」「3」都能筛到），SQL LIKE 下推
        conds.append(Material.chapter.contains(f.chapter))
    if f.difficulty:
        conds.append(Material.difficulty == f.difficulty)
    if f.exam_focus:
        conds.append(Material.exam_focus == True)  # noqa: E712  显式布尔列比较
    return conds


def tag_sql_condition(f: MaterialFilter) -> ColumnElement[bool] | None:
    if not f.has_tags:
        return None
    parts = [tag_condition(t) for t in f.tags]
    return and_(*parts) if f.tag_mode == "all" else or_(*parts)


def conditions(db: Session, f: MaterialFilter) -> tuple[list[ColumnElement[bool]], bool]:
    """返回 (SQL 条件列表, 标签是否已下推到 SQL)。"""
    conds = scalar_conditions(f)
    if not f.has_tags:
        return conds, True
    if not json1_ready(db):
        return conds, False  # 调用方需在内存里补做标签过滤
    tag_cond = tag_sql_condition(f)
    if tag_cond is not None:
        conds.append(tag_cond)
    return conds, True


def matches_tags(m: Material, f: MaterialFilter) -> bool:
    """内存兜底的标签判断（仅 JSON1 缺失时使用）。"""
    if not f.has_tags:
        return True
    owned = set(m.tags or [])
    if f.tag_mode == "all":
        return all(t in owned for t in f.tags)
    return any(t in owned for t in f.tags)


def order_recent(stmt: Select) -> Select:
    """稳定排序：创建时间倒序 + id 倒序（时间相同也有确定顺序，分页不重不漏）。"""
    return stmt.order_by(Material.created_at.desc(), Material.id.desc())


# ---------------------------------------------------------------------------
# 查询入口
# ---------------------------------------------------------------------------


@dataclass
class MaterialPage:
    items: list[Material]
    total: int                    # 过滤后的总条数（与 limit 无关）
    limit: int
    offset: int
    pushed_down: bool = True      # 标签过滤是否在 SQL 内完成

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


def clamp_limit(limit: int | None, default: int = DEFAULT_LIMIT) -> int:
    if limit is None:
        return default
    return max(1, min(int(limit), MAX_LIMIT))


def page_materials(
    db: Session, f: MaterialFilter, limit: int | None = None, offset: int = 0
) -> MaterialPage:
    """过滤 → 计数 → 排序 → 切页，全部在 SQL 内完成（JSON1 缺失时内存兜底）。"""
    limit = clamp_limit(limit)
    offset = max(0, int(offset or 0))
    conds, pushed = conditions(db, f)
    if pushed:
        total = int(db.execute(select(func.count(Material.id)).where(*conds)).scalar_one())
        stmt = order_recent(select(Material).where(*conds)).limit(limit).offset(offset)
        items = list(db.execute(stmt).scalars().all())
        return MaterialPage(items=items, total=total, limit=limit, offset=offset, pushed_down=True)
    # 兜底：先按标量条件取回 → 内存过滤 → **过滤之后**再切页（顺序不能颠倒）
    rows = db.execute(order_recent(select(Material).where(*conds))).scalars().all()
    filtered = [m for m in rows if matches_tags(m, f)]
    return MaterialPage(
        items=filtered[offset: offset + limit],
        total=len(filtered),
        limit=limit,
        offset=offset,
        pushed_down=False,
    )


def page_headers(page: MaterialPage) -> dict[str, str]:
    """分页元信息走响应头：既不破坏「返回数组」的既有约定，又让前端拿到真实总数。"""
    return {
        "X-Total-Count": str(page.total),
        "X-Limit": str(page.limit),
        "X-Offset": str(page.offset),
        "X-Has-More": "1" if page.has_more else "0",
        "X-Tag-Filter": "sql" if page.pushed_down else "memory",
    }


def filter_ids(db: Session, f: MaterialFilter, ids: Iterable[int]) -> set[int]:
    """把候选 id 集合按过滤条件收窄（检索链路用：FTS/语义先排序，过滤在 SQL 内做）。"""
    id_list = [int(i) for i in ids]
    if not id_list:
        return set()
    conds, pushed = conditions(db, f)
    keep: set[int] = set()
    for i in range(0, len(id_list), _IN_CHUNK):
        chunk = id_list[i: i + _IN_CHUNK]
        if pushed:
            rows = db.execute(
                select(Material.id).where(Material.id.in_(chunk), *conds)
            ).scalars().all()
            keep.update(int(x) for x in rows)
        else:
            rows = db.execute(
                select(Material).where(Material.id.in_(chunk), *conds)
            ).scalars().all()
            keep.update(m.id for m in rows if matches_tags(m, f))
    return keep


def candidates(db: Session, f: MaterialFilter, limit: int | None = None) -> list[Material]:
    """语义检索候选集：SQL 过滤后按时间倒序取回。

    limit 由调用方决定（用于兜住「超大资料库」的内存与算力），None 表示不限。
    """
    conds, pushed = conditions(db, f)
    stmt = order_recent(select(Material).where(*conds))
    if pushed:
        if limit is not None:
            stmt = stmt.limit(max(1, int(limit)))
        return list(db.execute(stmt).scalars().all())
    rows = db.execute(stmt).scalars().all()
    out = [m for m in rows if matches_tags(m, f)]
    return out[: max(1, int(limit))] if limit is not None else out


def tag_facets(
    db: Session, f: MaterialFilter, limit: int = MAX_FACETS
) -> list[tuple[str, int]]:
    """标签聚合（标签云）：`[(标签, 资料数)]`，按资料数倒序。

    覆盖**全库**（受课程/类型过滤约束），不再只统计当前页命中的标签。
    """
    limit = max(1, min(int(limit or MAX_FACETS), MAX_FACETS))
    conds = scalar_conditions(f)
    if json1_ready(db):
        values = func.json_each(Material.tags).table_valued("value", joins_implicitly=True)
        n = func.count(Material.id)
        stmt = (
            select(values.c.value.label("tag"), n.label("n"))
            .where(func.json_valid(Material.tags), *conds)
            .group_by(values.c.value)
            .order_by(n.desc(), values.c.value.asc())
            .limit(limit)
        )
        return [(str(row.tag), int(row.n)) for row in db.execute(stmt).all() if row.tag]
    counts: dict[str, int] = {}
    for m in db.execute(select(Material).where(*conds)).scalars().all():
        for t in m.tags or []:
            counts[str(t)] = counts.get(str(t), 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ordered[:limit]


def _natural_key(s: str) -> tuple:
    """章节自然排序键：按「章节号数字 → 原文」比较，第2章 < 第10章 < 第3.2节。"""
    nums = [int(x) for x in re.findall(r"\d+", s or "")]
    return (nums if nums else (10**9,), s)


def chapter_facets(
    db: Session, f: MaterialFilter, limit: int = MAX_FACETS
) -> list[tuple[str, int]]:
    """章节聚合：`[(章节, 资料数)]`，按章节号**自然排序**（第1章 < 第2章 < 第10章）。

    统计覆盖全库非空章节（受课程/类型/难度等过滤约束），SQL GROUP BY 计数，
    自然排序在 Python 完成（章节号解析的 SQL 表达式复杂且可读性差）。
    """
    limit = max(1, min(int(limit or MAX_FACETS), MAX_FACETS))
    conds = scalar_conditions(f)
    n = func.count(Material.id)
    stmt = (
        select(Material.chapter.label("chapter"), n.label("n"))
        .where(Material.chapter != "", *conds)
        .group_by(Material.chapter)
        .limit(limit)
    )
    rows = [(str(r.chapter), int(r.n)) for r in db.execute(stmt).all() if r.chapter]
    return sorted(rows, key=lambda kv: _natural_key(kv[0]))
