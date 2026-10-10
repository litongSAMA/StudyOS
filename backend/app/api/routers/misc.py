"""系统设置 + 首页统计。"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infra.config import mask_key
from app.infra.db import get_db
from app.infra.models import AppSetting, Course, Material, Task, WrongQuestion
from app.api.schemas import SettingIn, Stats, TaskOut
from app.api.serializers import task_out
from app.domain.sync import mask_token
from app.infra.timeutil import utcnow

router = APIRouter(prefix="/api", tags=["misc"])

# 这些键对外只回掩码：密钥/同步令牌绝不通过通用设置接口整串回传
# （本机同步令牌 sync_token 等同于全部数据的读写权限，明文只走 /api/sync/token/reveal）
MASKED_SETTING_KEYS = ("llm_api_key", "embed_api_key")
TOKEN_SETTING_KEYS = ("sync_token", "sync_remote_token")


def _mask_setting(key: str, value: str) -> str:
    if key in TOKEN_SETTING_KEYS:
        return mask_token(value)   # 令牌只露末 4 位
    if key in MASKED_SETTING_KEYS:
        return mask_key(value)
    return value


@router.get("/stats", response_model=Stats)
def stats(db: Session = Depends(get_db)):
    now = utcnow()
    materials = db.execute(select(func.count(Material.id))).scalar_one()
    courses = db.execute(select(func.count(Course.id))).scalar_one()
    tasks_open = db.execute(
        select(func.count(Task.id)).where(Task.done == False)
    ).scalar_one()
    tasks_due_7d = db.execute(
        select(func.count(Task.id)).where(
            Task.done == False,
            Task.due_at >= now,
            Task.due_at <= now + timedelta(days=7),
        )
    ).scalar_one()
    tasks_overdue = db.execute(
        select(func.count(Task.id)).where(Task.done == False, Task.due_at < now)
    ).scalar_one()
    wrong_total = db.execute(select(func.count(WrongQuestion.id))).scalar_one()
    wrong_open = db.execute(
        select(func.count(WrongQuestion.id)).where(WrongQuestion.status == "open")
    ).scalar_one()
    upcoming = db.execute(
        select(Task)
        .where(Task.done == False, Task.due_at >= now)
        .order_by(Task.due_at.asc())
        .limit(6)
    ).scalars().all()
    from app.api.routers.focus import statistics
    from app.infra.models import Quiz
    focus=statistics(db)
    quizzes=list(db.scalars(select(Quiz)))
    return Stats(
        quizzes=len(quizzes),quizzes_completed=sum(bool(q.questions) and len(q.results)==len(q.questions) for q in quizzes),
        focus_count_today=focus['today']['count'],focus_minutes_today=focus['today']['minutes'],focus_streak=focus['streak'],
        materials=materials,
        courses=courses,
        tasks_open=tasks_open,
        tasks_due_7d=tasks_due_7d,
        tasks_overdue=tasks_overdue,
        wrong_questions=wrong_total,
        wrong_questions_open=wrong_open,
        upcoming=[task_out(db, t) for t in upcoming],
    )


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    return {
        s.key: _mask_setting(s.key, s.value or "")
        for s in db.execute(select(AppSetting)).scalars().all()
    }


@router.put("/settings")
def put_settings(body: SettingIn, db: Session = Depends(get_db)):
    row = db.get(AppSetting, body.key)
    if row:
        row.value = body.value
    else:
        db.add(AppSetting(key=body.key, value=body.value))
    db.commit()
    return {"ok": True}
