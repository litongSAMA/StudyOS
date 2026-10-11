"""清单 / 模版库 / 分享链接路由。

- 清单 CRUD：通用勾选清单（标题/描述/课程/标签/截止日 + 可勾选项），
  勾选进度随时可改，Markdown 可导出；
- 模版库：内置模版（只读，随版本升级）+ 用户自存模版（可编辑/删除），
  已有清单可一键「存为模版」；
- 分享链接：token 凭证 + 可选过期 + 可撤销 + 查看计数，公开只读视图见
  routers/share.py；同学打开链接即可查看，StudyOS 用户可一键导入。
"""
from __future__ import annotations

import secrets
from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..checklistlib import TEMPLATE_CATEGORIES
from ..db import get_db
from ..models import Checklist, ChecklistShare, ChecklistTemplate, Course
from ..schemas import (
    ChecklistImportIn,
    ChecklistIn,
    ChecklistOut,
    ChecklistPatch,
    ChecklistTemplateIn,
    ChecklistTemplateOut,
    ChecklistTemplatePatch,
    SaveAsTemplateIn,
    ShareCreate,
    ShareOut,
    SharedChecklistOut,
)
from ..serializers import checklist_out, course_names, share_out
from ..timeutil import local_datetime, utcnow

router = APIRouter(prefix="/api/checklists", tags=["checklists"])
templates_router = APIRouter(prefix="/api/checklist-templates", tags=["checklists"])
shares_router = APIRouter(prefix="/api/shares", tags=["checklists"])

# 分享链接最长有效期：1 年（8760 小时）
SHARE_MAX_HOURS = 8760


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------

def _norm_items(items) -> list[dict]:
    """清单项归一化：去掉空文本、截断超长文本，最多 500 项。"""
    out: list[dict] = []
    for it in items or []:
        if isinstance(it, BaseModel):
            text, done, note = it.text, it.done, it.note
        elif isinstance(it, dict):
            text = it.get("text", "")
            done = bool(it.get("done"))
            note = it.get("note", "")
        else:
            continue
        text = str(text or "").strip()
        if not text:
            continue
        out.append({"text": text[:300], "done": bool(done), "note": str(note or "").strip()[:300]})
    return out[:500]


def _norm_tags(tags) -> list[str]:
    """标签归一化：去空白、去重保序，最多 50 个。"""
    seen: set[str] = set()
    out: list[str] = []
    for t in tags or []:
        t = str(t or "").strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t[:40])
    return out[:50]


def _check_category(category: str | None) -> str:
    category = (category or "日常学习").strip()
    if category not in TEMPLATE_CATEGORIES:
        raise HTTPException(
            422,
            f"模版分类非法：{category}（可选 {'/'.join(TEMPLATE_CATEGORIES)}）",
        )
    return category


# ---------------------------------------------------------------------------
# 清单 CRUD
# ---------------------------------------------------------------------------

@router.get("", response_model=list[ChecklistOut])
def list_checklists(
    course_id: int | None = None,
    status: str = "all",
    db: Session = Depends(get_db),
):
    """清单列表：可按课程过滤；status=open（未全部勾完）/ done（全部勾完）。"""
    q = select(Checklist)
    if course_id is not None:
        q = q.where(Checklist.course_id == course_id)
    q = q.order_by(Checklist.created_at.desc()).limit(500)
    names = course_names(db)
    out = [checklist_out(db, c, names) for c in db.execute(q).scalars().all()]
    if status == "open":
        out = [c for c in out if c.done_count < c.total_count or c.total_count == 0]
    elif status == "done":
        out = [c for c in out if c.total_count > 0 and c.done_count >= c.total_count]
    return out


@router.post("", response_model=ChecklistOut, status_code=201)
def create_checklist(body: ChecklistIn, db: Session = Depends(get_db)):
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(422, "清单标题不能为空")
    if body.course_id is not None and not db.get(Course, body.course_id):
        raise HTTPException(404, "课程不存在")
    c = Checklist(
        course_id=body.course_id,
        title=title[:300],
        description=(body.description or "").strip(),
        tags=_norm_tags(body.tags),
        due_at=body.due_at,
        items=_norm_items(body.items),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return checklist_out(db, c)


@router.post("/import", response_model=ChecklistOut, status_code=201)
def import_checklist(body: ChecklistImportIn, db: Session = Depends(get_db)):
    """一键导入分享清单：url = 分享链接（服务端抓取），payload = 下载的分享 JSON。

    导入的清单归导入者所有：勾选进度清零（重新开始）、课程按名字在本机
    匹配（匹配不到则不绑课程）。
    """
    payload = body.payload
    if body.url:
        payload = _fetch_share(body.url)
    if not isinstance(payload, dict):
        raise HTTPException(422, "分享内容格式不正确")
    try:
        shared = SharedChecklistOut.model_validate(payload)
    except ValidationError:
        raise HTTPException(422, "分享内容字段缺失或格式不正确")
    title = (shared.title or "").strip()
    if not title:
        raise HTTPException(422, "分享内容缺少标题")
    course_id = None
    if shared.course_name:
        row = db.execute(
            select(Course).where(Course.name == shared.course_name)
        ).scalar_one_or_none()
        course_id = row.id if row else None
    c = Checklist(
        course_id=course_id,
        title=title[:300],
        description=(shared.description or "").strip(),
        tags=_norm_tags(shared.tags),
        due_at=shared.due_at,
        items=[{**i, "done": False} for i in _norm_items(shared.items)],
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return checklist_out(db, c)


def _fetch_share(url: str) -> dict:
    """分享链接 → 公开只读 JSON。页面链接（/#/s/…）自动转 API 地址。"""
    u = (url or "").strip()
    if not u:
        raise HTTPException(422, "分享链接为空")
    if "/#/s/" in u:
        head, tail = u.split("/#/s/", 1)
        token = tail.split("?")[0].split("#")[0].strip("/")
        if not token:
            raise HTTPException(422, "不是有效的 StudyOS 分享链接")
        u = f"{head.rstrip('/')}/api/share/{token}"
    if not u.startswith(("http://", "https://")) or "/api/share/" not in u:
        raise HTTPException(422, "不是有效的 StudyOS 分享链接")
    if not u.rsplit("/api/share/", 1)[1].split("?")[0].strip("/"):
        raise HTTPException(422, "不是有效的 StudyOS 分享链接")
    try:
        r = httpx.get(u, timeout=10.0, follow_redirects=True)
    except httpx.HTTPError:
        raise HTTPException(502, "无法访问分享链接（目标不可达）")
    if r.status_code != 200:
        raise HTTPException(502, "分享链接无效：可能已过期、被撤销或清单已删除")
    try:
        return r.json()
    except ValueError:
        raise HTTPException(502, "分享内容不是有效的 JSON")


@router.get("/{checklist_id}", response_model=ChecklistOut)
def get_checklist(checklist_id: int, db: Session = Depends(get_db)):
    c = db.get(Checklist, checklist_id)
    if not c:
        raise HTTPException(404, "清单不存在")
    return checklist_out(db, c)


@router.get("/{checklist_id}/md")
def export_checklist_md(checklist_id: int, db: Session = Depends(get_db)):
    """单条清单导出为 Markdown（- [ ] 勾选格式，数据可带走）。"""
    c = db.get(Checklist, checklist_id)
    if not c:
        raise HTTPException(404, "清单不存在")
    out = checklist_out(db, c)
    meta: list[str] = []
    if out.course_name:
        meta.append(f"课程：{out.course_name}")
    if out.tags:
        meta.append(f"标签：{'、'.join(out.tags)}")
    meta.append(
        f"截止：{local_datetime(out.due_at):%Y-%m-%d %H:%M}" if out.due_at else "截止：无"
    )
    lines = [
        f"# {out.title}",
        "",
        f"> {' ｜ '.join(meta)}",
        f"> 进度：{out.done_count}/{out.total_count} ｜ 导出时间：{local_datetime(utcnow()):%Y-%m-%d %H:%M}",
        "",
    ]
    if out.description:
        lines += [out.description, ""]
    lines += ["## 清单项", ""]
    for it in out.items:
        mark = "x" if it.done else " "
        lines.append(f"- [{mark}] {it.text}" + (f"（{it.note}）" if it.note else ""))
    body = "\n".join(lines)
    fname = f"studyos-checklist-{checklist_id}.md"
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.patch("/{checklist_id}", response_model=ChecklistOut)
def patch_checklist(checklist_id: int, body: ChecklistPatch, db: Session = Depends(get_db)):
    c = db.get(Checklist, checklist_id)
    if not c:
        raise HTTPException(404, "清单不存在")
    data = body.model_dump(exclude_unset=True)
    if "course_id" in data and data["course_id"] is not None and not db.get(Course, data["course_id"]):
        raise HTTPException(404, "课程不存在")
    if "title" in data:
        title = (data["title"] or "").strip()
        if not title:
            raise HTTPException(422, "清单标题不能为空")
        data["title"] = title[:300]
    if "description" in data:
        data["description"] = (data["description"] or "").strip()
    if "tags" in data:
        data["tags"] = _norm_tags(data["tags"])
    if "items" in data:
        data["items"] = _norm_items(data["items"])
    for k, v in data.items():
        setattr(c, k, v)
    c.updated_at = utcnow()
    db.commit()
    db.refresh(c)
    return checklist_out(db, c)


@router.delete("/{checklist_id}", status_code=204)
def delete_checklist(checklist_id: int, db: Session = Depends(get_db)):
    c = db.get(Checklist, checklist_id)
    if not c:
        raise HTTPException(404, "清单不存在")
    db.execute(delete(ChecklistShare).where(ChecklistShare.checklist_id == checklist_id))
    db.delete(c)
    db.commit()


# ---------------------------------------------------------------------------
# 分享链接（管理侧）
# ---------------------------------------------------------------------------

@router.post("/{checklist_id}/share", response_model=ShareOut, status_code=201)
def create_share(
    checklist_id: int, body: ShareCreate, request: Request, db: Session = Depends(get_db)
):
    """为清单生成分享链接。expires_h 留空 = 永久；1~8760 小时可选过期。"""
    c = db.get(Checklist, checklist_id)
    if not c:
        raise HTTPException(404, "清单不存在")
    if body.expires_h is not None and not 1 <= body.expires_h <= SHARE_MAX_HOURS:
        raise HTTPException(
            422, f"有效期需在 1~{SHARE_MAX_HOURS} 小时之间（留空 = 永久有效）"
        )
    s = ChecklistShare(
        token=secrets.token_urlsafe(24),
        checklist_id=c.id,
        expires_at=utcnow() + timedelta(hours=body.expires_h) if body.expires_h else None,
    )
    db.add(s)
    db.commit()
    return share_out(db, s, str(request.base_url))


@router.get("/{checklist_id}/shares", response_model=list[ShareOut])
def list_checklist_shares(checklist_id: int, request: Request, db: Session = Depends(get_db)):
    """某条清单的全部分享链接（含已撤销的，便于查看历史）。"""
    if not db.get(Checklist, checklist_id):
        raise HTTPException(404, "清单不存在")
    rows = db.execute(
        select(ChecklistShare)
        .where(ChecklistShare.checklist_id == checklist_id)
        .order_by(ChecklistShare.created_at.desc())
    ).scalars().all()
    return [share_out(db, s, str(request.base_url)) for s in rows]


@shares_router.get("", response_model=list[ShareOut])
def list_all_shares(request: Request, db: Session = Depends(get_db)):
    """全部分享链接总览（管理/撤销用）。"""
    rows = db.execute(
        select(ChecklistShare).order_by(ChecklistShare.created_at.desc()).limit(200)
    ).scalars().all()
    return [share_out(db, s, str(request.base_url)) for s in rows]


@shares_router.delete("/{token}")
def revoke_share(token: str, db: Session = Depends(get_db)):
    """撤销分享链接：立即失效（公开视图 404）。"""
    s = db.get(ChecklistShare, token)
    if not s:
        raise HTTPException(404, "分享链接不存在")
    s.revoked = True
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# 模版库
# ---------------------------------------------------------------------------

@templates_router.get("/categories")
def template_categories():
    return TEMPLATE_CATEGORIES


@templates_router.get("", response_model=list[ChecklistTemplateOut])
def list_templates(category: str | None = None, db: Session = Depends(get_db)):
    """模版库列表（内置在前，可按分类过滤）。"""
    q = select(ChecklistTemplate)
    if category:
        q = q.where(ChecklistTemplate.category == category)
    q = q.order_by(ChecklistTemplate.builtin.desc(), ChecklistTemplate.id)
    return db.execute(q).scalars().all()


@templates_router.post("", response_model=ChecklistTemplateOut, status_code=201)
def create_template(body: ChecklistTemplateIn, db: Session = Depends(get_db)):
    """自建模版（勾选状态统一清零）。"""
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(422, "模版标题不能为空")
    t = ChecklistTemplate(
        title=title[:300],
        description=(body.description or "").strip(),
        category=_check_category(body.category),
        tags=_norm_tags(body.tags),
        items=[{**i, "done": False} for i in _norm_items(body.items)],
        builtin=False,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@router.post("/{checklist_id}/template", response_model=ChecklistTemplateOut, status_code=201)
def save_checklist_as_template(
    checklist_id: int, body: SaveAsTemplateIn, db: Session = Depends(get_db)
):
    """把已有清单存为模版（标题可改、分类可选，勾选状态清零）。"""
    c = db.get(Checklist, checklist_id)
    if not c:
        raise HTTPException(404, "清单不存在")
    t = ChecklistTemplate(
        title=((body.title or "").strip() or c.title)[:300],
        description=c.description or "",
        category=_check_category(body.category),
        tags=_norm_tags(c.tags),
        items=[{**i, "done": False} for i in _norm_items(c.items)],
        builtin=False,
        source_checklist_id=c.id,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@templates_router.get("/{template_id}", response_model=ChecklistTemplateOut)
def get_template(template_id: int, db: Session = Depends(get_db)):
    t = db.get(ChecklistTemplate, template_id)
    if not t:
        raise HTTPException(404, "模版不存在")
    return t


@templates_router.patch("/{template_id}", response_model=ChecklistTemplateOut)
def patch_template(template_id: int, body: ChecklistTemplatePatch, db: Session = Depends(get_db)):
    t = db.get(ChecklistTemplate, template_id)
    if not t:
        raise HTTPException(404, "模版不存在")
    if t.builtin:
        raise HTTPException(403, "内置模版受保护：请「使用」该模版后另存为新模版")
    data = body.model_dump(exclude_unset=True)
    if "title" in data:
        title = (data["title"] or "").strip()
        if not title:
            raise HTTPException(422, "模版标题不能为空")
        data["title"] = title[:300]
    if "description" in data:
        data["description"] = (data["description"] or "").strip()
    if "category" in data:
        data["category"] = _check_category(data["category"])
    if "tags" in data:
        data["tags"] = _norm_tags(data["tags"])
    if "items" in data:
        data["items"] = [{**i, "done": False} for i in _norm_items(data["items"])]
    for k, v in data.items():
        setattr(t, k, v)
    t.updated_at = utcnow()
    db.commit()
    db.refresh(t)
    return t


@templates_router.delete("/{template_id}", status_code=204)
def delete_template(template_id: int, db: Session = Depends(get_db)):
    t = db.get(ChecklistTemplate, template_id)
    if not t:
        raise HTTPException(404, "模版不存在")
    if t.builtin:
        raise HTTPException(403, "内置模版受保护，不可删除")
    db.delete(t)
    db.commit()
