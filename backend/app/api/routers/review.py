"""复习清单生成：DDL 倒排 + 掌握程度加权 + 间隔重复（SM-2 变体）。

复习计划不再只按「DDL + 资料量」分配：
1. 每份资料的学习时长 = 基础时长 × 难度权重 × 考试重点加权 × 掌握系数
   （未掌握 1.6× → 已掌握 1.0×），掌握程度从间隔重复评分实时更新；
2. 每天的复习块先排「到期的间隔复习」（复习状态 due_at ≤ 当天），再排新学，
   已掌握（mastery ≥ 0.85）的资料不再排初次学习；
3. 首次学习的资料按 1/3/7 天阶梯预排间隔复习检查点（考试日前），
   形成「学习 → 隔天 / 隔三天 / 隔一周再复习」的完整间隔重复闭环；
4. 单日间隔复习数量有上限（超出顺延），每天仍只排一门主复习课（避免切换成本）。

评分入口见 POST /api/review/rate（again/hard/good/easy），状态总览见
GET /api/review/states（含 is_due / overdue_days 到期标记）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infra.db import get_db
from app.infra.models import Material, MaterialReview, ReviewPlan, Task
from app.api.schemas import MaterialReviewOut, ReviewIn, ReviewPlanOut, ReviewRateIn
from app.api.serializers import course_names
from app.domain.spacedrep import (
    MASTERED,
    PROJECTED_OFFSETS,
    RATING_QUALITY,
    REVIEWS_PER_DAY,
    REVIEW_HOURS,
    apply_rating,
    material_hours,
)
from app.infra.timeutil import iso_utc, local_date, utcnow

router = APIRouter(prefix="/api/review", tags=["review"])

HISTORY_KEEP = 20  # 每份资料保留最近 20 条评分历史


# ---------------------------------------------------------------------------
# 复习状态（掌握程度 + 间隔重复调度状态）
# ---------------------------------------------------------------------------


def _load_states(db: Session, material_ids: list[int] | None = None) -> dict[int, MaterialReview]:
    q = select(MaterialReview)
    if material_ids:
        q = q.where(MaterialReview.material_id.in_(material_ids))
    return {r.material_id: r for r in db.execute(q).scalars().all()}


def _state_out(
    db: Session, st: MaterialReview | None, m: Material, names: dict[int, str]
) -> MaterialReviewOut:
    """复习状态输出：无状态记录的资料按 mastery=0「未复习」返回，便于前端渲染总览。"""
    now = utcnow()
    out = MaterialReviewOut(
        material_id=m.id,
        material_title=m.title,
        course_id=m.course_id,
        course_name=names.get(m.course_id or 0, ""),
    )
    if st:
        out.mastery = st.mastery or 0.0
        out.repetitions = st.repetitions or 0
        out.lapses = st.lapses or 0
        out.interval_days = st.interval_days or 0.0
        out.ease = st.ease or 2.5
        out.due_at = st.due_at
        out.last_reviewed_at = st.last_reviewed_at
        out.last_rating = st.last_rating or ""
        # 到期 = 已排过间隔（repetitions>0）且 due_at 已过：该再复习了
        if (st.repetitions or 0) > 0 and st.due_at and st.due_at <= now:
            out.is_due = True
            out.overdue_days = max(0, (local_date(now) - local_date(st.due_at)).days)
    return out


@router.get("/states", response_model=list[MaterialReviewOut])
def list_states(course_id: int | None = None, db: Session = Depends(get_db)):
    """全部资料的复习状态总览（掌握程度 / 间隔 / 到期标记），可按课程过滤。"""
    names = course_names(db)
    q = select(Material)
    if course_id is not None:
        q = q.where(Material.course_id == course_id)
    mats = db.execute(q.order_by(Material.course_id, Material.title)).scalars().all()
    states = _load_states(db, [m.id for m in mats])
    return [_state_out(db, states.get(m.id), m, names) for m in mats]


@router.post("/rate", response_model=MaterialReviewOut)
def rate_material(body: ReviewRateIn, db: Session = Depends(get_db)):
    """给一份资料的复习效果评分：again（忘了）/ hard（吃力）/ good（正常）/ easy（轻松）。

    按 SM-2 变体更新掌握程度 / 间隔 / 难度系数，并给出下次到期时刻——
    这是间隔重复闭环的反馈入口，评分后重新生成复习计划即体现掌握程度变化。
    """
    if body.rating not in RATING_QUALITY:
        raise HTTPException(422, "评分需为 again / hard / good / easy")
    m = db.get(Material, body.material_id)
    if not m:
        raise HTTPException(404, "资料不存在")
    st = db.execute(
        select(MaterialReview).where(MaterialReview.material_id == m.id)
    ).scalar_one_or_none()
    if st is None:
        st = MaterialReview(material_id=m.id)
        db.add(st)
    now = utcnow()
    upd = apply_rating(
        st.mastery or 0.0, st.repetitions or 0, st.lapses or 0, st.ease or 2.5,
        body.rating, now,
    )
    st.mastery = upd["mastery"]
    st.repetitions = upd["repetitions"]
    st.lapses = upd["lapses"]
    st.interval_days = upd["interval_days"]
    st.ease = upd["ease"]
    st.due_at = upd["due_at"]
    st.last_reviewed_at = now
    st.last_rating = body.rating
    st.history = (st.history or [])[-(HISTORY_KEEP - 1):] + [{
        "at": iso_utc(now), "rating": body.rating, "mastery": round(st.mastery, 2),
    }]
    db.commit()
    db.refresh(st)
    return _state_out(db, st, m, course_names(db))


@router.delete("/state/{material_id}", status_code=204)
def reset_state(material_id: int, db: Session = Depends(get_db)):
    """重置一份资料的复习状态（重新开始间隔重复）。"""
    if not db.get(Material, material_id):
        raise HTTPException(404, "资料不存在")
    st = db.execute(
        select(MaterialReview).where(MaterialReview.material_id == material_id)
    ).scalar_one_or_none()
    if st:
        db.delete(st)
        db.commit()


# ---------------------------------------------------------------------------
# 复习计划生成
# ---------------------------------------------------------------------------


def _material_hours(m: Material, mastery: float = 0.0) -> float:
    """单份资料的学习时长（共享公式见 spacedrep.material_hours）。"""
    return material_hours(m, mastery)


def _priority_order(mats: list[Material], states: dict[int, MaterialReview]) -> list[Material]:
    """复习资料排序：考试重点优先，其次困难→中等→简单，掌握程度低者先学（稳定序）。"""
    rank = {"hard": 0, "medium": 1, "easy": 2, "": 3}
    return sorted(
        mats,
        key=lambda m: (
            0 if m.exam_focus else 1,
            rank.get(m.difficulty or "", 3),
            states.get(m.id).mastery if m.id in states else 0.0,
        ),
    )


def _pick_study(
    studyable: list[Material],
    states: dict[int, MaterialReview],
    block_h: float,
    covered: dict[int, float],
) -> tuple[list[int], list[str]]:
    """从优先队列里挑出本学习块覆盖的资料：跳过已覆盖完的，累计剩余时长够 block_h 即停。"""
    picked: list[Material] = []
    acc = 0.0
    for m in studyable:
        need = _material_hours(m, states.get(m.id).mastery if m.id in states else 0.0)
        done = covered.get(m.id, 0.0)
        if done + 1e-6 >= need:
            continue
        picked.append(m)
        acc += need - done
        if acc + 1e-6 >= block_h:
            break
    return [m.id for m in picked], [m.title for m in picked]


@router.get("", response_model=list[ReviewPlanOut])
def list_plans(db: Session = Depends(get_db)):
    return db.execute(select(ReviewPlan).order_by(ReviewPlan.created_at.desc())).scalars().all()


@router.get("/{plan_id}", response_model=ReviewPlanOut)
def get_plan(plan_id: int, db: Session = Depends(get_db)):
    p = db.get(ReviewPlan, plan_id)
    if not p:
        raise HTTPException(404, "计划不存在")
    return p


@router.delete("/{plan_id}", status_code=204)
def delete_plan(plan_id: int, db: Session = Depends(get_db)):
    p = db.get(ReviewPlan, plan_id)
    if not p:
        raise HTTPException(404, "计划不存在")
    db.delete(p)
    db.commit()


@router.post("/generate", response_model=ReviewPlanOut, status_code=201)
def generate_plan(body: ReviewIn, db: Session = Depends(get_db)):
    """按考试 DDL 倒排生成复习计划（掌握程度加权 + 间隔重复），规则见模块 docstring。"""
    exams = db.execute(select(Task).where(Task.id.in_(body.exam_task_ids))).scalars().all()
    if not exams:
        raise HTTPException(422, "请选择至少一个考试/大作业任务")
    today = local_date(utcnow())  # 用户本地「今天」（due_at 存 UTC，日期语义按本地算）
    hours_per_day = max(0.5, min(body.hours_per_day, 12.0))
    names = course_names(db)
    now_utc = utcnow()

    # 同一门课选多个考试任务时只取最早的一个
    courses: dict[int, dict] = {}
    for exam in sorted((e for e in exams if e.due_at >= now_utc), key=lambda e: e.due_at):
        cid = exam.course_id or 0
        if cid in courses:
            continue
        mats = db.execute(
            select(Material).where(Material.course_id == exam.course_id)
        ).scalars().all()
        courses[cid] = {"exam": exam, "mats": mats}
    if not courses:
        raise HTTPException(422, "所选考试均已过期")

    all_mats = [m for c in courses.values() for m in c["mats"]]
    states = _load_states(db, [m.id for m in all_mats])
    for cid, c in courses.items():
        mat_mastery = [states.get(m.id).mastery if m.id in states else 0.0 for m in c["mats"]]
        c["mastery"] = (sum(mat_mastery) / len(mat_mastery)) if mat_mastery else 0.0
        # 每份资料按掌握程度加权后求和 = 该门课还需的新学总时长
        c["total_h"] = max(
            sum(_material_hours(m, states.get(m.id).mastery if m.id in states else 0.0)
                for m in c["mats"]),
            2.0,
        )
        c["due_date"] = local_date(c["exam"].due_at)
        # 已掌握（mastery ≥ 0.85）的资料不再排初次学习，靠间隔复习维持
        c["studyable"] = [
            m for m in _priority_order(c["mats"], states)
            if (states.get(m.id).mastery if m.id in states else 0.0) < MASTERED
        ]

    ordered = sorted(courses.items(), key=lambda kv: courses[kv[0]]["due_date"])
    max_exam_date = max(c["due_date"] for c in courses.values())

    # ---- Pass 1: 新学分配（每天一门主复习课，DDL 早的先排）----
    remaining = {cid: c["total_h"] for cid, c in courses.items()}
    study_blocks: dict[tuple[date, int], dict] = {}
    first_study: dict[int, tuple[date, int]] = {}  # 首次学习日（仅无复习状态的资料）
    covered: dict[int, float] = {}  # 每份资料已分配的新学时长（避免剩余小块重复吃同一份）
    mat_by_id = {m.id: m for m in all_mats}
    day = today
    while any(h > 0.01 for h in remaining.values()) and day < max_exam_date:
        for cid, c in ordered:
            if remaining[cid] <= 0.01 or c["due_date"] <= day:
                continue
            if not c["studyable"]:
                if c["mats"]:
                    remaining[cid] = 0.0  # 资料已全部掌握 → 只靠间隔复习维持
                    continue
                # 一门课完全没有资料：仍排一个「自由巩固」块（保持旧行为，
                # 前端会提示先补资料），避免 0 资料课程让整个计划 422
                block_h = min(hours_per_day, remaining[cid])
                study_blocks[(day, cid)] = {"ids": [], "titles": [], "hours": block_h}
                remaining[cid] -= block_h
                break
            block_h = min(hours_per_day, remaining[cid])
            ids, titles = _pick_study(c["studyable"], states, block_h, covered)
            if not ids:
                remaining[cid] = 0.0
                continue
            study_blocks[(day, cid)] = {"ids": ids, "titles": titles, "hours": block_h}
            remaining[cid] -= block_h
            # 更新覆盖进度：除最后一份整份覆盖，最后一份吃掉本块剩余时长
            acc = 0.0
            for i, mid in enumerate(ids):
                m = mat_by_id[mid]
                need = _material_hours(m, states.get(mid).mastery if mid in states else 0.0)
                done = covered.get(mid, 0.0)
                if i == len(ids) - 1:
                    covered[mid] = done + max(0.0, block_h - acc)
                else:
                    covered[mid] = need
                    acc += need - done
            for mid in ids:
                if mid not in states:  # 纯首次学习 → 预排间隔复习检查点
                    first_study.setdefault(mid, (day, cid))
            break  # 一天一门主复习课
        day += timedelta(days=1)

    # ---- Pass 2: 到期的间隔复习（复习状态 due_at 已到/逾期，考试日前）----
    due_items: dict[int, list[tuple[date, int, str]]] = {}
    for cid, c in courses.items():
        for m in c["mats"]:
            st = states.get(m.id)
            if not st or not st.due_at or (st.repetitions or 0) <= 0:
                continue
            d = max(local_date(st.due_at), today)
            if d >= c["due_date"]:
                continue
            due_items.setdefault(cid, []).append((d, m.id, m.title))

    # ---- Pass 3: 计划预排间隔复习检查点（首次学习后第 1/3/7 天）----
    spaced_items: dict[int, list[tuple[date, int, str]]] = {}
    title_by_id = {m.id: m.title for m in all_mats}
    for mid, (d, cid) in first_study.items():
        for off in PROJECTED_OFFSETS:
            rd = d + timedelta(days=off)
            if rd >= courses[cid]["due_date"]:
                break
            spaced_items.setdefault(cid, []).append((rd, mid, title_by_id.get(mid, "")))

    # ---- Pass 4: 每日间隔复习上限（超出顺延，排不进考试前则舍弃）----
    review_days: dict[tuple[date, int], dict] = {}
    for cid in set(due_items) | set(spaced_items):
        queue = (
            [(d, mid, t, "due") for d, mid, t in due_items.get(cid, [])]
            + [(d, mid, t, "spaced") for d, mid, t in spaced_items.get(cid, [])]
        )
        queue.sort(key=lambda x: x[0])
        for d0, mid, title, kind in queue:
            placed = None
            d = d0
            while d < courses[cid]["due_date"]:
                if len(review_days.get((d, cid), {}).get("items", [])) < REVIEWS_PER_DAY:
                    placed = (d, cid)
                    break
                d += timedelta(days=1)
            if placed is None:
                continue  # 考试前排不下，舍弃该次复习
            slot = review_days.setdefault(placed, {
                "items": [], "due_ids": [], "due_titles": [],
                "spaced_ids": [], "spaced_titles": [],
            })
            if kind == "due":
                slot["due_ids"].append(mid)
                slot["due_titles"].append(title)
            else:
                slot["spaced_ids"].append(mid)
                slot["spaced_titles"].append(title)
            slot["items"].append(mid)

    if not study_blocks and not review_days:
        raise HTTPException(422, "没有可安排的复习内容（资料已全部掌握或距离考试太近）")

    # ---- Pass 5: 组装每日块（到期复习在前 → 新学 → 计划检查点）----
    sessions: list[dict] = []
    order_index = {cid: i for i, (cid, _) in enumerate(ordered)}
    all_keys = set(study_blocks) | set(review_days)
    for d, cid in sorted(all_keys, key=lambda k: (k[0], order_index[k[1]])):
        c = courses[cid]
        study = study_blocks.get((d, cid))
        rev = review_days.get((d, cid), {})
        study_ids = study["ids"] if study else []
        study_titles = study["titles"] if study else []
        study_h = study["hours"] if study else 0.0
        due_ids = rev.get("due_ids", [])
        spaced_ids = rev.get("spaced_ids", [])
        n_review = len(due_ids) + len(spaced_ids)
        # 到期复习在前（检索练习先行）→ 新学 → 计划检查点；去重保持顺序
        mat_ids = list(dict.fromkeys(due_ids + study_ids + spaced_ids))
        scope = f"{_scope_base(c['mats'])}{_focus_hint(c['mats'])}· 掌握度 {round(c['mastery'] * 100)}%"
        if due_ids:
            scope += f"· 到期复习 {len(due_ids)} 份"
        if spaced_ids:
            scope += f"· 间隔复习 {len(spaced_ids)} 份"
        sessions.append({
            "date": d.isoformat(),
            "course": names.get(cid, "未分类"),
            "scope": scope,
            "material_ids": mat_ids,
            "materials": [title_by_id[i] for i in mat_ids],
            "hours": round(study_h + REVIEW_HOURS * n_review, 1),
            "mastery": round(c["mastery"], 2),
            "exam": c["exam"].title,
            "exam_date": c["due_date"].strftime("%m-%d"),
            "study_ids": study_ids,
            "study_titles": study_titles,
            "due_ids": due_ids,
            "due_titles": rev.get("due_titles", []),
            "spaced_ids": spaced_ids,
            "spaced_titles": rev.get("spaced_titles", []),
        })

    last_day = max(datetime.fromisoformat(s["date"]) for s in sessions)
    plan = ReviewPlan(
        title=f"复习计划（{today.strftime('%m-%d')} ~ {last_day.strftime('%m-%d')}）",
        exam_task_ids=body.exam_task_ids,
        sessions=sessions,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _scope_base(mats: list[Material]) -> str:
    """按资料类型给复习范围文案：先过课件/笔记，再过试卷。"""
    by_type: dict[str, list[Material]] = {}
    for m in mats:
        by_type.setdefault(m.type, []).append(m)
    if "exam" in by_type:
        return f"真题/试卷 {len(by_type['exam'])} 份 + 错题回顾"
    if "notes" in by_type:
        return f"复习笔记（{len(by_type['notes'])} 份）"
    if "courseware" in by_type:
        return f"课件回顾（{len(by_type['courseware'])} 份）"
    return f"资料梳理（{len(mats)} 份）"


def _focus_hint(mats: list[Material]) -> str:
    """复习范围补充提示：考试重点数量 / 困难资料数量（都没有则省略）。"""
    n_focus = sum(1 for m in mats if m.exam_focus)
    n_hard = sum(1 for m in mats if m.difficulty == "hard")
    parts = []
    if n_focus:
        parts.append(f"含 {n_focus} 份考试重点")
    if n_hard:
        parts.append(f"困难 {n_hard} 份")
    return f"（{' · '.join(parts)}）" if parts else ""
