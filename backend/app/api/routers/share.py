"""公开分享接口：任何设备打开分享链接即可只读查看清单。

GET 无副作用，不要求登录（本项目单机免登录）；响应带 CORS `*`，
让「分享页 → 本机 StudyOS 一键导入」的跨源读取可行（分享页本身
就可能来自局域网内另一台设备的主机名）。写操作（导入到本机）仍走
POST /api/checklists/import，受同源 Origin 校验保护。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.infra.db import get_db
from app.infra.models import Checklist, ChecklistShare, Course
from app.api.schemas import ChecklistItem, SharedChecklistOut
from app.infra.timeutil import utcnow

router = APIRouter(prefix="/api/share", tags=["share"])

# loopback 来源视为清单所有者本人（分享者在自己的机器上打开链接预览）
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


@router.get("/{token}", response_model=SharedChecklistOut)
def view_share(token: str, request: Request, db: Session = Depends(get_db)):
    """公开只读查看：返回清单快照与分享元信息，并累加查看次数。

    撤销/过期/清单已删除统一按 404/410 处理，不泄露是否存在。
    """
    s = db.get(ChecklistShare, token)
    if not s or s.revoked:
        raise HTTPException(404, "分享链接不存在或已被撤销")
    c = db.get(Checklist, s.checklist_id)
    if not c:
        raise HTTPException(410, "分享的清单已被删除")
    if s.expires_at and utcnow() > s.expires_at:
        raise HTTPException(410, "分享链接已过期")
    s.view_count = (s.view_count or 0) + 1
    db.commit()

    course_name = ""
    if c.course_id:
        course = db.get(Course, c.course_id)
        course_name = course.name if course else ""
    items = [
        ChecklistItem(text=i.get("text", ""), done=bool(i.get("done")), note=i.get("note", ""))
        for i in (c.items or [])
        if (i or {}).get("text", "").strip()
    ]
    is_owner = (request.client.host or "") in _LOOPBACK_HOSTS
    payload = SharedChecklistOut(
        token=token,
        title=c.title,
        description=c.description or "",
        course_name=course_name,
        tags=list(c.tags or []),
        items=items,
        due_at=c.due_at,
        created_at=c.created_at,
        expires_at=s.expires_at,
        view_count=s.view_count or 0,
        is_owner=is_owner,
    )
    return JSONResponse(
        content=payload.model_dump(mode="json"),
        headers={"Access-Control-Allow-Origin": "*"},
    )
