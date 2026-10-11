"""错题归集（错题本）路由：CRUD + 过滤检索 + AI 提炼 + 重练模式 + Markdown 导出。

与资料归档同一套口径：
- 章节/难度入库时自动规则抽取（可手动修正），难度非法值 422；
- 标签过滤走 SQLite `json_each()` 精确匹配（JSON1 缺失时内存兜底，结果一致），
  计数/排序/分页都在过滤之后（X-Total-Count 给出真实总数）；
- AI 提炼 POST /refine：粘贴错题原文 → LLM 结构化补全标题/题目/答案/错因/
  章节/难度/标签（不落库，供表单预填），LLM 未配置或失败时降级离线规则引擎；
- 重练模式：随机抽取「待攻克」错题，自评「答对/又错」——
  连续答对 2 次自动标记已掌握（done），答错清零回退待攻克（open）；
- 全量/单条 Markdown 导出，数据可带走。
"""
from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from ..agent.common import classify_difficulty, extract_chapter, normalize_difficulty
from ..agent.wrongrefine import refine_wrong
from ..db import get_db
from ..materialquery import json1_ready, normalize_tag_mode, parse_tags
from ..models import Course, WrongQuestion
from ..schemas import (
    WrongBookFacets, WrongBookStats, WrongPracticeIn, WrongQuestionIn,
    WrongQuestionOut, WrongQuestionPatch, WrongRefineResult,
)
from ..serializers import course_names, wrong_out
from ..timeutil import utcnow

router = APIRouter(prefix="/api/wrongbook", tags=["wrongbook"])

DIFFICULTIES = ("", "easy", "medium", "hard")
STATUSES = ("open", "done")
STATUS_LABELS = {"open": "待攻克", "done": "已掌握"}
DIFF_LABELS = {"easy": "简单", "medium": "中等", "hard": "困难"}

MAX_LIMIT = 1000
MAX_TAGS = 20
MAX_FACETS = 200


def _check_difficulty(v: str | None) -> None:
    """难度只接受固定枚举（其余 422），与资料归档口径一致。"""
    if v is not None and (v or "").strip().lower() not in DIFFICULTIES:
        raise HTTPException(422, f"难度取值非法：{v}（可选 easy/medium/hard）")


def _check_status(v: str | None) -> None:
    if v is not None and v not in STATUSES:
        raise HTTPException(422, f"状态取值非法：{v}（可选 open/done）")


def _norm_tags(raw: list | None) -> list[str]:
    """标签归一化：去空白、去重保序、限长限量。"""
    out: list[str] = []
    for t in raw or []:
        t = str(t).strip()[:40]
        if t and t not in out:
            out.append(t)
    return out[:MAX_TAGS]


def _tag_cond(tag: str):
    """单个标签的 SQL 条件：json_each 展开后 EXISTS 精确匹配（非子串匹配）。"""
    values = func.json_each(WrongQuestion.tags).table_valued("value")
    return and_(
        func.json_valid(WrongQuestion.tags),
        select(1).select_from(values).where(values.c.value == tag).exists(),
    )


def _list_order():
    return (
        WrongQuestion.status.desc(),   # 待攻克（open）排在已掌握（done）前面
        WrongQuestion.created_at.desc(),
        WrongQuestion.id.desc(),
    )


def _natural_key(s: str) -> tuple:
    """章节自然排序键：按章节号数字比较，第2章 < 第10章 < 第3.2节。"""
    nums = [int(x) for x in re.findall(r"\d+", s or "")]
    return (nums if nums else (10**9,), s)


def render_wrong_md(w: WrongQuestionOut, index: int | None = None) -> str:
    """单条错题 → Markdown（题目/我的答案/正确答案/错因，元信息进引用块）。"""
    heading = f"# {w.title}" if index is None else f"## {index}. {w.title}"
    meta = " ｜ ".join(filter(None, [
        f"课程：{w.course_name}" if w.course_name else "",
        f"来源：{w.source}" if w.source else "",
        f"章节：{w.chapter}" if w.chapter else "",
        f"难度：{DIFF_LABELS.get(w.difficulty, '未标注')}" if w.difficulty else "",
        f"状态：{STATUS_LABELS.get(w.status, w.status)}",
        f"重练 {w.review_count} 次（错 {w.wrong_count} 次）",
    ]))
    lines = [heading, "", f"> {meta}", ""]
    if w.question:
        lines += ["## 题目", "", w.question, ""]
    if w.my_answer:
        lines += ["## 我的答案", "", w.my_answer, ""]
    if w.answer:
        lines += ["## 正确答案与解析", "", w.answer, ""]
    if w.reason:
        lines += ["## 错因分析", "", w.reason, ""]
    if w.tags:
        lines += [f"标签：{'、'.join(w.tags)}", ""]
    return "\n".join(lines)


@router.get("", response_model=list[WrongQuestionOut])
def list_wrong_questions(
    response: Response,
    course_id: int | None = None,
    chapter: str | None = None,
    difficulty: str | None = None,
    status: str | None = Query(default=None, pattern="^(open|done)$"),
    tag: str | None = None,
    tags: list[str] | None = Query(default=None, description="多标签过滤，可重复传或用逗号分隔"),
    tag_mode: str = Query(default="any", pattern="^(any|all)$"),
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """错题列表：课程/章节(包含)/难度/状态/标签(any|all)/关键词过滤，待攻克排前。

    分页元信息在响应头：X-Total-Count（过滤后总数）/ X-Limit / X-Offset / X-Has-More。
    """
    _check_difficulty(difficulty)
    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset or 0))
    conds = []
    if course_id is not None:
        conds.append(WrongQuestion.course_id == course_id)
    if chapter:
        conds.append(WrongQuestion.chapter.contains(chapter))
    if difficulty:
        conds.append(WrongQuestion.difficulty == difficulty)
    if status:
        conds.append(WrongQuestion.status == status)
    if q and q.strip():
        qs = q.strip().replace("\\", "\\\\")
        conds.append(or_(
            WrongQuestion.title.contains(qs, autoescape=True),
            WrongQuestion.question.contains(qs, autoescape=True),
            WrongQuestion.answer.contains(qs, autoescape=True),
            WrongQuestion.reason.contains(qs, autoescape=True),
            WrongQuestion.source.contains(qs, autoescape=True),
        ))

    tags_tuple = parse_tags(tag, tags)
    tag_mode_n = normalize_tag_mode(tag_mode)
    sql_tags = bool(tags_tuple) and json1_ready(db)
    if sql_tags:
        parts = [_tag_cond(t) for t in tags_tuple]
        conds.append(and_(*parts) if tag_mode_n == "all" else or_(*parts))

    stmt = select(WrongQuestion).where(*conds)
    if sql_tags or not tags_tuple:
        total = int(db.execute(
            select(func.count()).select_from(stmt.subquery())
        ).scalar_one())
        rows = db.execute(stmt.order_by(*_list_order()).limit(limit).offset(offset)).scalars().all()
        pushed = "sql"
    else:
        # JSON1 缺失兜底：先按标量条件取回 → 内存过滤 → 过滤后再切页（顺序不可颠倒）
        owned = lambda w: set(w.tags or [])  # noqa: E731
        rows_all = db.execute(stmt.order_by(*_list_order())).scalars().all()
        filtered = [
            w for w in rows_all
            if (all(t in owned(w) for t in tags_tuple) if tag_mode_n == "all"
                else any(t in owned(w) for t in tags_tuple))
        ]
        total = len(filtered)
        rows = filtered[offset: offset + limit]
        pushed = "memory"

    response.headers.update({
        "X-Total-Count": str(total),
        "X-Limit": str(limit),
        "X-Offset": str(offset),
        "X-Has-More": "1" if offset + len(rows) < total else "0",
        "X-Tag-Filter": pushed,
    })
    names = course_names(db)
    return [wrong_out(db, w, names) for w in rows]


@router.get("/stats", response_model=WrongBookStats)
def wrongbook_stats(db: Session = Depends(get_db)):
    """错题本统计：总数/待攻克/已掌握 + 按课程分布（首页卡片与页面头部用）。"""
    total = int(db.execute(select(func.count(WrongQuestion.id))).scalar_one())
    open_n = int(db.execute(
        select(func.count(WrongQuestion.id)).where(WrongQuestion.status == "open")
    ).scalar_one())
    done_n = total - open_n
    by_course: list[dict] = []
    if total:
        names = course_names(db)
        rows = db.execute(
            select(
                WrongQuestion.course_id,
                func.count(WrongQuestion.id),
                func.sum(case((WrongQuestion.status == "open", 1), else_=0)),
            ).group_by(WrongQuestion.course_id)
        ).all()
        by_course = [
            {
                "course_id": cid,
                "course_name": names.get(cid or 0, ""),
                "total": int(cnt),
                "open": int(op or 0),
                "done": int(cnt) - int(op or 0),
            }
            for cid, cnt, op in rows
        ]
        by_course.sort(key=lambda x: -x["open"])
    return WrongBookStats(
        total=total,
        open=open_n,
        done=done_n,
        mastered_pct=round(done_n * 100 / total) if total else 0,
        by_course=by_course,
    )


@router.get("/facets", response_model=WrongBookFacets)
def wrongbook_facets(
    course_id: int | None = None,
    limit: int = MAX_FACETS,
    db: Session = Depends(get_db),
):
    """过滤聚合：章节（自然排序）+ 标签云（热度倒序），SQL GROUP BY 覆盖全库。"""
    limit = max(1, min(int(limit or MAX_FACETS), MAX_FACETS))
    base_conds = [WrongQuestion.course_id == course_id] if course_id is not None else []

    rows = db.execute(
        select(WrongQuestion.chapter, func.count(WrongQuestion.id))
        .where(WrongQuestion.chapter != "", *base_conds)
        .group_by(WrongQuestion.chapter)
    ).all()
    chapters = sorted(
        ((str(c), int(n)) for c, n in rows if c),
        key=lambda kv: _natural_key(kv[0]),
    )[:limit]

    tag_pairs: list[tuple[str, int]] = []
    if json1_ready(db):
        values = func.json_each(WrongQuestion.tags).table_valued("value", joins_implicitly=True)
        n = func.count(WrongQuestion.id)
        stmt = (
            select(values.c.value.label("tag"), n.label("n"))
            .where(func.json_valid(WrongQuestion.tags), *base_conds)
            .group_by(values.c.value)
            .order_by(n.desc(), values.c.value.asc())
            .limit(limit)
        )
        tag_pairs = [(str(r.tag), int(r.n)) for r in db.execute(stmt).all() if r.tag]
    else:
        counts: dict[str, int] = {}
        for w in db.execute(select(WrongQuestion).where(*base_conds)).scalars().all():
            for t in w.tags or []:
                counts[str(t)] = counts.get(str(t), 0) + 1
        tag_pairs = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]

    return WrongBookFacets(
        chapters=[{"chapter": c, "count": n} for c, n in chapters],
        tags=[{"tag": t, "count": n} for t, n in tag_pairs],
    )


@router.get("/practice", response_model=list[WrongQuestionOut])
def practice_queue(
    course_id: int | None = None,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    """重练队列：随机抽取「待攻克」错题（可限课程），返回顺序即作答顺序。"""
    limit = max(1, min(int(limit), 50))
    q = select(WrongQuestion).where(WrongQuestion.status == "open")
    if course_id is not None:
        q = q.where(WrongQuestion.course_id == course_id)
    rows = db.execute(q.order_by(func.random()).limit(limit)).scalars().all()
    return [wrong_out(db, w) for w in rows]


@router.get("/export/md")
def export_all_md(db: Session = Depends(get_db)):
    """全部错题导出为单个 Markdown 文件（题目+答案+错因，数据可带走）。"""
    rows = db.execute(select(WrongQuestion).order_by(*_list_order())).scalars().all()
    outs = [wrong_out(db, w) for w in rows]
    open_n = sum(1 for w in outs if w.status == "open")
    head = (
        f"# 错题本导出\n\n"
        f"> 共 {len(outs)} 题 ｜ 待攻克 {open_n} ｜ 已掌握 {len(outs) - open_n} ｜ "
        f"导出时间：{date.today().isoformat()}\n"
    )
    body = head + "\n" + "\n\n".join(
        render_wrong_md(w, i + 1) for i, w in enumerate(outs)
    ) if outs else head + "\n暂无错题"
    fname = f"studyos-wrongbook-{date.today().isoformat()}.md"
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("", response_model=WrongQuestionOut, status_code=201)
def create_wrong_question(body: WrongQuestionIn, db: Session = Depends(get_db)):
    """归集一条错题。章节/难度留空时按「标题 + 题干 + 来源」自动规则抽取。"""
    if body.course_id is not None and not db.get(Course, body.course_id):
        raise HTTPException(404, "课程不存在")
    _check_difficulty(body.difficulty)
    data = body.model_dump()
    data["title"] = (body.title or "").strip()[:300] or "未命名错题"
    data["question"] = (body.question or "").strip()
    data["my_answer"] = (body.my_answer or "").strip()
    data["answer"] = (body.answer or "").strip()
    data["reason"] = (body.reason or "").strip()
    data["source"] = (body.source or "").strip()[:300]
    data["chapter"] = (body.chapter or "").strip()[:120]
    data["difficulty"] = normalize_difficulty(data["difficulty"])
    data["tags"] = _norm_tags(body.tags)
    haystack = " ".join(filter(None, [data["title"], data["question"], data["source"]]))
    if not data["chapter"]:
        data["chapter"] = extract_chapter(haystack)[:120]
    if not data["difficulty"]:
        data["difficulty"] = classify_difficulty(haystack)
    w = WrongQuestion(**data)
    db.add(w)
    db.commit()
    db.refresh(w)
    return wrong_out(db, w)


@router.post("/refine", response_model=WrongRefineResult)
def refine_wrong_question(body: dict):
    """AI 提炼错题：粘贴错题原文 → 结构化字段。不落库，供表单预填核对。

    body: { text: str, title_hint?: str }
    LLM 未配置/失败时降级离线规则引擎（章节/难度/标签规则抽取，内容字段留空）。
    """
    text = str(body.get("text", "") or "").strip()
    if not text:
        raise HTTPException(422, "文本为空：请粘贴错题原文（题干、你的作答、参考答案等）")
    title_hint = str(body.get("title_hint", "") or "").strip()
    return refine_wrong(text, title_hint)


@router.get("/{wrong_id}", response_model=WrongQuestionOut)
def get_wrong_question(wrong_id: int, db: Session = Depends(get_db)):
    w = db.get(WrongQuestion, wrong_id)
    if not w:
        raise HTTPException(404, "错题不存在")
    return wrong_out(db, w)


@router.get("/{wrong_id}/md")
def export_wrong_md(wrong_id: int, db: Session = Depends(get_db)):
    """单条错题导出为 Markdown 文件。"""
    w = db.get(WrongQuestion, wrong_id)
    if not w:
        raise HTTPException(404, "错题不存在")
    out = wrong_out(db, w)
    fname = f"studyos-wrong-{wrong_id}.md"
    return Response(
        content=render_wrong_md(out),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.patch("/{wrong_id}", response_model=WrongQuestionOut)
def patch_wrong_question(wrong_id: int, body: WrongQuestionPatch, db: Session = Depends(get_db)):
    w = db.get(WrongQuestion, wrong_id)
    if not w:
        raise HTTPException(404, "错题不存在")
    if body.course_id is not None and not db.get(Course, body.course_id):
        raise HTTPException(404, "课程不存在")
    _check_difficulty(body.difficulty)
    _check_status(body.status)
    data = body.model_dump(exclude_unset=True)
    if "title" in data:
        data["title"] = (data["title"] or "").strip()[:300] or w.title
    if "chapter" in data:
        data["chapter"] = (data["chapter"] or "").strip()[:120]
    if "difficulty" in data:
        data["difficulty"] = normalize_difficulty(data["difficulty"])
    if "tags" in data:
        data["tags"] = _norm_tags(body.tags)
    for field in ("question", "my_answer", "answer", "reason", "source"):
        if field in data:
            data[field] = (data[field] or "").strip()
            if field == "source":
                data[field] = data[field][:300]
    for k, v in data.items():
        setattr(w, k, v)
    db.commit()
    db.refresh(w)
    return wrong_out(db, w)


@router.post("/{wrong_id}/practice", response_model=WrongQuestionOut)
def practice_wrong_question(wrong_id: int, body: WrongPracticeIn, db: Session = Depends(get_db)):
    """重练自评：答对 streak+1（连续 2 次 → 已掌握 done）；答错 streak 清零、回到待攻克。"""
    w = db.get(WrongQuestion, wrong_id)
    if not w:
        raise HTTPException(404, "错题不存在")
    w.review_count = (w.review_count or 0) + 1
    w.last_reviewed_at = utcnow()
    if body.ok:
        w.streak = (w.streak or 0) + 1
        w.last_result = "correct"
        if w.streak >= 2:
            w.status = "done"
    else:
        w.streak = 0
        w.wrong_count = (w.wrong_count or 0) + 1
        w.last_result = "wrong"
        w.status = "open"
    db.commit()
    db.refresh(w)
    return wrong_out(db, w)


@router.delete("/{wrong_id}", status_code=204)
def delete_wrong_question(wrong_id: int, db: Session = Depends(get_db)):
    w = db.get(WrongQuestion, wrong_id)
    if not w:
        raise HTTPException(404, "错题不存在")
    db.delete(w)
    db.commit()
