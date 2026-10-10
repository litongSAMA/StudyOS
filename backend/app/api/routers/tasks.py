"""任务(DDL)路由：CRUD + 看板过滤 + 提醒查询。"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infra.db import get_db
from app.infra.models import Course, Task
from app.api.schemas import TaskIn, TaskOut, TaskPatch
from app.api.serializers import course_names, task_out
from app.infra.timeutil import UTCDateTime, to_utc, utcnow

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("", response_model=list[TaskOut])
def list_tasks(
    done: bool | None = None,
    course_id: int | None = None,
    kind: str | None = None,
    due_before: UTCDateTime | None = None,
    due_after: UTCDateTime | None = None,
    limit: int = 300,
    db: Session = Depends(get_db),
):
    q = select(Task).order_by(Task.due_at.asc()).limit(min(limit, 1000))
    if done is not None:
        q = q.where(Task.done == done)
    if course_id is not None:
        q = q.where(Task.course_id == course_id)
    if kind:
        q = q.where(Task.kind == kind)
    if due_before:
        q = q.where(Task.due_at <= to_utc(due_before))
    if due_after:
        q = q.where(Task.due_at >= to_utc(due_after))
    names = course_names(db)
    return [task_out(db, t, names) for t in db.execute(q).scalars().all()]


@router.get("/upcoming", response_model=list[TaskOut])
def upcoming_tasks(hours: int = 168, db: Session = Depends(get_db)):
    """未来 N 小时内到期的未完成任务（提醒轮询用）。"""
    hours = min(max(hours, 1), 24 * 30)  # 钳制在 1 小时 ~ 30 天，防异常入参
    now = utcnow()
    q = (
        select(Task)
        .where(
            Task.done == False,
            Task.due_at >= now,  # 只返回未来任务；逾期任务走 /overdue
            Task.due_at <= now + timedelta(hours=hours),
        )
        .order_by(Task.due_at.asc())
    )
    names = course_names(db)
    return [task_out(db, t, names) for t in db.execute(q).scalars().all()]


@router.get("/overdue", response_model=list[TaskOut])
def overdue_tasks(db: Session = Depends(get_db)):
    now = utcnow()
    q = (
        select(Task)
        .where(Task.done == False, Task.due_at < now)
        .order_by(Task.due_at.asc())
    )
    names = course_names(db)
    return [task_out(db, t, names) for t in db.execute(q).scalars().all()]


@router.post("", response_model=TaskOut, status_code=201)
def create_task(body: TaskIn, db: Session = Depends(get_db)):
    if body.course_id is not None and not db.get(Course, body.course_id):
        raise HTTPException(404, "课程不存在")
    t = Task(**body.model_dump())
    db.add(t)
    db.commit()
    db.refresh(t)
    return task_out(db, t)


@router.patch("/{task_id}", response_model=TaskOut)
def patch_task(task_id: int, body: TaskPatch, db: Session = Depends(get_db)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    if body.course_id is not None and not db.get(Course, body.course_id):
        raise HTTPException(404, "课程不存在")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(t, k, v)
    db.commit()
    db.refresh(t)
    return task_out(db, t)


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: int, db: Session = Depends(get_db)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    db.delete(t)
    db.commit()
