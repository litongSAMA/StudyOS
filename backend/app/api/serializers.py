"""ORM → API 输出转换辅助。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infra.models import Checklist, ChecklistShare, Course, Material, Note, Task, WrongQuestion
from .schemas import (
    ChecklistOut, MaterialOut, NoteOut, ShareOut, TaskOut, WrongQuestionOut,
)


def course_names(db: Session) -> dict[int, str]:
    return {c.id: c.name for c in db.execute(select(Course)).scalars().all()}


def material_out(db: Session, m: Material, names: dict[int, str] | None = None) -> MaterialOut:
    names = names if names is not None else course_names(db)
    out = MaterialOut.model_validate(m)
    out.course_name = names.get(m.course_id or 0)
    return out


def task_out(db: Session, t: Task, names: dict[int, str] | None = None) -> TaskOut:
    names = names if names is not None else course_names(db)
    out = TaskOut.model_validate(t)
    out.course_name = names.get(t.course_id or 0)
    return out


def note_out(db: Session, n: Note) -> NoteOut:
    """笔记输出：补来源资料标题与课程名（资料/课程已删除时为空串）。"""
    out = NoteOut.model_validate(n)
    if n.material_id:
        m = db.get(Material, n.material_id)
        if m:
            out.material_title = m.title
            course = db.get(Course, m.course_id) if m.course_id else None
            out.course_name = course.name if course else ""
    return out


def wrong_out(db: Session, w: WrongQuestion, names: dict[int, str] | None = None) -> WrongQuestionOut:
    """错题输出：补课程名（课程已删除时为空）。"""
    names = names if names is not None else course_names(db)
    out = WrongQuestionOut.model_validate(w)
    out.course_name = names.get(w.course_id or 0)
    return out


def checklist_out(db: Session, c: Checklist, names: dict[int, str] | None = None) -> ChecklistOut:
    """清单输出：补课程名与勾选进度（done_count/total_count 由 items 现算）。"""
    names = names if names is not None else course_names(db)
    out = ChecklistOut.model_validate(c)
    out.course_name = names.get(c.course_id or 0)
    items = list(c.items or [])
    out.total_count = len(items)
    out.done_count = sum(1 for i in items if (i or {}).get("done"))
    return out


def share_out(db: Session, s: ChecklistShare, base_url: str) -> ShareOut:
    """分享链接输出：拼出页面链接与公开只读接口地址（用请求 Host，局域网可直达）。"""
    c = db.get(Checklist, s.checklist_id)
    base = str(base_url or "").rstrip("/")
    return ShareOut(
        token=s.token,
        checklist_id=s.checklist_id,
        checklist_title=c.title if c else "",
        url=f"{base}/#/s/{s.token}",
        api_url=f"{base}/api/share/{s.token}",
        expires_at=s.expires_at,
        created_at=s.created_at,
        view_count=s.view_count or 0,
        revoked=bool(s.revoked),
    )
