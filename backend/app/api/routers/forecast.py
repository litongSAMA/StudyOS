"""学习进度预测 / 负载预警 API。

GET /api/forecast?hours_per_day=3&days=21 一次性返回：
- overall：总体状态（进度良好 / 偏紧 / 预警）与一句话结论；
- courses：每场未完成考试的剩余学时、所需节奏、预计完成日期、
  考试日预计掌握程度（与复习计划共用 spacedrep.material_hours 公式）；
- load_days：未来 N 天逐日负载（DDL 截止权重 + 复习计划学时 + 到期复习）；
- warnings：结构化告警（单日超载 / 连续高负载 / 考试撞车）；
- curve：预计掌握度曲线（顺序仿真：容量先分给最早到期的课程）；
- pace：近 7 天实际复习节奏 vs 所需节奏。

全部计算为纯函数（app/forecast.py），本路由只负责读库、组数据、输出。
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infra.db import get_db
from app.domain.forecast import OVERDUE_EXAM_GRACE, compute_forecast
from app.infra.models import Material, MaterialReview, ReviewPlan, Task
from ..schemas import (
    CourseForecast,
    ForecastOut,
    ForecastOverall,
    ForecastPoint,
    LoadDay,
    LoadWarning,
    PaceInfo,
)
from app.api.serializers import course_names
from app.infra.timeutil import local_date, utcnow

router = APIRouter(prefix="/api/forecast", tags=["forecast"])

# 与复习页「选考试」口径一致：考试/大作业类型，或标题命中这些词
_EXAM_TITLE_RE = re.compile(r"考试|期末|期中|答辩|验收")

DEFAULT_HOURS_PER_DAY = 3.0
DEFAULT_WINDOW_DAYS = 21


@router.get("", response_model=ForecastOut)
def get_forecast(
    hours_per_day: float = Query(DEFAULT_HOURS_PER_DAY, ge=0.5, le=12.0),
    days: int = Query(DEFAULT_WINDOW_DAYS, ge=7, le=60),
    db: Session = Depends(get_db),
):
    now = utcnow()
    today = local_date(now)
    names = course_names(db)

    # ---- 未完成任务（一次性取出，同时供「考试预测」与「负载截止」用）----
    tasks = db.execute(select(Task).where(Task.done == False)).scalars().all()

    # ---- 考试/大作业 → 按课程分组（同一门课只取最早一场）----
    def is_exam_like(t: Task) -> bool:
        return t.kind in ("exam", "report") or bool(_EXAM_TITLE_RE.search(t.title or ""))

    exams_by_course: dict[int, Task] = {}
    for t in tasks:
        if not is_exam_like(t):
            continue
        due = local_date(t.due_at)
        if due < today - timedelta(days=OVERDUE_EXAM_GRACE):
            continue  # 过期太久的历史考试不再参与预测
        bucket = t.course_id or 0
        cur = exams_by_course.get(bucket)
        if cur is None or due < local_date(cur.due_at):
            exams_by_course[bucket] = t

    # ---- 资料按课程分组 + 掌握程度 ----
    mats = db.execute(select(Material)).scalars().all()
    states = {
        r.material_id: r
        for r in db.execute(select(MaterialReview)).scalars().all()
    }
    mats_by_course: dict[int, list[dict]] = {}
    for m in mats:
        bucket = m.course_id or 0
        st = states.get(m.id)
        mats_by_course.setdefault(bucket, []).append({
            "difficulty": m.difficulty or "",
            "exam_focus": bool(m.exam_focus),
            "mastery": (st.mastery if st else 0.0) or 0.0,
        })

    exams = [
        {
            "task_id": t.id,
            "course_id": t.course_id,
            "course_name": names.get(t.course_id or 0, "未分类"),
            "exam_title": t.title,
            "due_at": t.due_at,
            "due_date": local_date(t.due_at),
            "materials": mats_by_course.get(t.course_id or 0, []),
        }
        for t in exams_by_course.values()
    ]

    # ---- 最新一份复习计划的每日学时（未来部分才计入负载）----
    plan_sessions: list[tuple] = []
    latest_plan = db.execute(
        select(ReviewPlan).order_by(ReviewPlan.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if latest_plan:
        for s in latest_plan.sessions or []:
            try:
                plan_sessions.append(
                    (date.fromisoformat(s["date"]), float(s.get("hours") or 0))
                )
            except (KeyError, TypeError, ValueError):
                continue  # 脏数据不炸接口

    # ---- 到期的间隔复习（逾期按今天补）----
    due_review_dates = [
        local_date(r.due_at)
        for r in db.execute(
            select(MaterialReview).where(
                MaterialReview.repetitions > 0, MaterialReview.due_at.isnot(None)
            )
        ).scalars().all()
    ]

    # ---- 全部未完成任务的截止日（负载用，逾期按今天计）----
    deadlines = [(local_date(t.due_at), t.kind) for t in tasks]

    # ---- 近 7 天实际节奏（间隔复习评分记录）----
    recent_reviews = [
        r.last_reviewed_at
        for r in db.execute(
            select(MaterialReview).where(MaterialReview.last_reviewed_at.isnot(None))
        ).scalars().all()
    ]

    res = compute_forecast(
        exams=exams,
        plan_sessions=plan_sessions,
        due_review_dates=due_review_dates,
        deadlines=deadlines,
        hours_per_day=hours_per_day,
        window_days=days,
        today=today,
        now=now,
        recent_review_dates=recent_reviews,
    )

    return ForecastOut(
        now=now,
        hours_per_day=hours_per_day,
        window_days=days,
        overall=ForecastOverall(**res["overall"]),
        courses=[
            CourseForecast(
                task_id=c["task_id"], course_id=c["course_id"],
                course_name=c["course_name"], exam_title=c["exam_title"],
                due_at=c["due_at"], due_date=c["due_date"].isoformat(),
                days_left=c["days_left"],
                materials_total=c["materials_total"],
                materials_studied=c["materials_studied"],
                materials_mastered=c["materials_mastered"],
                current_mastery=c["current_mastery"],
                total_hours=round(c["total_hours"], 1),
                remaining_hours=round(c["remaining_hours"], 1),
                progress_pct=c["progress_pct"],
                required_pace_h=c["required_pace_h"],
                predicted_finish_date=c["predicted_finish_date"],
                predicted_mastery=c["predicted_mastery"],
                status=c["status"], message=c["message"],
            )
            for c in res["courses"]
        ],
        load_days=[LoadDay(**d) for d in res["load_days"]],
        warnings=[LoadWarning(**w) for w in res["warnings"]],
        curve=[ForecastPoint(**p) for p in res["curve"]],
        pace=PaceInfo(**res["pace"]),
    )
