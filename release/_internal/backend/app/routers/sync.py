"""多端同步路由：状态/令牌、快照交换（远端调用）、主动同步、完整备份/恢复。

令牌披露策略（知情同意）：
- `GET /api/sync/status` 只回**掩码 + 指纹**，不再明文返回令牌——局域网内任何设备
  一条 curl 就能抓走配对凭据的问题由此消除；
- 需要明文配对时，由用户在本机页面显式调用 `POST /api/sync/token/reveal`，
  该接口默认仅允许 loopback（本机）访问，并在服务端日志留下披露记录；
  反向代理/远程访问场景可用 `ALLOW_REMOTE_ADMIN=1` 放行（自行承担风险）。
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import (
    AppSetting, Course, Material, MaterialReview, Note, ReviewPlan, Task, Tombstone,
    WrongQuestion,
)
from ..sync import (
    MAX_BACKUP_BYTES,
    SyncError,
    build_snapshot,
    apply_snapshot,
    get_device_id,
    get_setting,
    get_sync_token,
    make_backup,
    restore_backup,
    set_setting,
    sync_now,
    token_fingerprint,
    token_ok,
    token_public_info,
    token_source,
)
from ..timeutil import iso_utc, localnow, utcnow

router = APIRouter(prefix="/api/sync", tags=["sync"])

TOKEN_HEADER = "X-Sync-Token"

# 令牌明文披露时给用户的风险提示（前端原样展示，确保「知情」）
TOKEN_WARNING = (
    "令牌等同于本机数据的完整读写权限：持有它的设备可以读取并覆盖你的全部课程、资料、"
    "任务与复习计划。只在配对时向你自己的设备展示，不要截图外发或贴进聊天群；"
    "怀疑泄露时立即「重新生成」。"
)

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}


class ExchangeIn(BaseModel):
    snapshot: dict
    include_files: bool = True


class SyncNowIn(BaseModel):
    remote_url: str
    remote_token: str
    include_files: bool = True


def _require_token(db: Session, token: str | None) -> None:
    if not token_ok(db, token):
        raise HTTPException(401, "同步令牌无效")


def _client_host(request: Request) -> str:
    return (request.client.host if request.client else "") or ""


def _is_local(request: Request) -> bool:
    host = _client_host(request)
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def _require_local(request: Request) -> None:
    """敏感披露只允许本机（loopback）——除非显式放开 ALLOW_REMOTE_ADMIN。"""
    if _is_local(request) or settings.allow_remote_admin:
        return
    raise HTTPException(
        403,
        "为避免配对令牌被局域网内其它设备抓取，明文令牌只允许在运行 StudyOS 的这台设备上"
        f"查看（当前来源 {_client_host(request) or '未知'}）。"
        "如确需远程查看，请以 ALLOW_REMOTE_ADMIN=1 启动后端。",
    )


def _counts(db: Session) -> dict:
    return {
        "courses": db.execute(select(func.count(Course.id))).scalar_one(),
        "materials": db.execute(select(func.count(Material.id))).scalar_one(),
        "tasks": db.execute(select(func.count(Task.id))).scalar_one(),
        "reviews": db.execute(select(func.count(ReviewPlan.id))).scalar_one(),
        "notes": db.execute(select(func.count(Note.id))).scalar_one(),
        "material_reviews": db.execute(select(func.count(MaterialReview.id))).scalar_one(),
        "wrong_questions": db.execute(select(func.count(WrongQuestion.id))).scalar_one(),
        "tombstones": db.execute(select(func.count(Tombstone.sync_uuid))).scalar_one(),
    }


@router.get("/status")
def sync_status(request: Request, db: Session = Depends(get_db)):
    """本机同步状态：令牌**掩码 + 指纹**（不含明文）、远端配置、数据规模。"""
    remote_url = get_setting(db, "sync_remote_url")
    return {
        "device_id": get_device_id(db),
        **token_public_info(db),
        # 明文令牌需显式调用 POST /api/sync/token/reveal（默认仅本机）
        "token_reveal_allowed": _is_local(request) or settings.allow_remote_admin,
        "client_local": _is_local(request),
        "remote_admin_allowed": settings.allow_remote_admin,
        "token_warning": TOKEN_WARNING,
        "remote_url": remote_url,
        "remote_token_set": bool(get_setting(db, "sync_remote_token")),
        "counts": _counts(db),
        "now": iso_utc(utcnow()),
    }


@router.post("/token/reveal")
def reveal_token(request: Request, db: Session = Depends(get_db)):
    """显式披露明文令牌（配对用）：仅本机可调用，并在服务端留下披露记录。"""
    _require_local(request)
    token = get_sync_token(db)
    print(
        f"[sync] 同步令牌被显式查看（指纹 {token_fingerprint(token)}，"
        f"来源 {_client_host(request) or '本机'}，{localnow():%Y-%m-%d %H:%M:%S}）"
    )
    return {
        "token": token,
        "fingerprint": token_fingerprint(token),
        "warning": TOKEN_WARNING,
        "revealed_at": iso_utc(utcnow()),
    }


@router.post("/token/regenerate")
def regenerate_token(request: Request, db: Session = Depends(get_db)):
    """重新生成本机令牌（旧令牌立即失效）：仅本机可调用，返回掩码信息。"""
    _require_local(request)
    if token_source() == "env":
        # 令牌被 SYNC_TOKEN 环境变量固定，写库不会生效 → 明确拒绝而不是静默无效
        raise HTTPException(
            422,
            "当前令牌由 SYNC_TOKEN 环境变量固定，重新生成不会生效；"
            "请修改环境变量后重启后端。",
        )
    set_setting(db, "sync_token", secrets.token_urlsafe(24))
    db.commit()
    return {**token_public_info(db), "warning": TOKEN_WARNING}


@router.get("/snapshot")
def snapshot_endpoint(
    token: str | None = None,
    x_sync_token: str | None = Header(default=None),
    include_files: bool = True,
    db: Session = Depends(get_db),
):
    """远端拉取本端快照（需令牌；exchange 接口已内建，此接口供诊断）。"""
    _require_token(db, token or x_sync_token)
    return build_snapshot(db, include_files=include_files)


@router.post("/exchange")
def exchange(
    body: ExchangeIn,
    x_sync_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """被远端调用：合并远端快照 → 回传本端（合并后）快照。一轮往返即双向同步。"""
    _require_token(db, x_sync_token)
    applied = apply_snapshot(db, body.snapshot)
    return {
        "applied": applied,
        "snapshot": build_snapshot(db, include_files=body.include_files),
    }


@router.post("/now")
def sync_now_endpoint(body: SyncNowIn, db: Session = Depends(get_db)):
    """本端发起一轮同步：推本端快照到远端 → 拉远端快照合并回来。"""
    try:
        result = sync_now(db, body.remote_url, body.remote_token, body.include_files)
    except SyncError as e:
        raise HTTPException(422, str(e)) from e
    set_setting(db, "sync_remote_url", body.remote_url.strip().rstrip("/"))
    set_setting(db, "sync_remote_token", body.remote_token.strip())
    db.commit()
    return {**result, "remote_url": body.remote_url.strip().rstrip("/")}


@router.post("/remote/clear")
def clear_remote(db: Session = Depends(get_db)):
    for key in ("sync_remote_url", "sync_remote_token"):
        row = db.get(AppSetting, key)
        if row:
            db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/backup")
def backup(
    x_sync_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """完整备份下载：manifest.json（全部数据）+ files/（原始文件），zip 格式。"""
    _require_token(db, x_sync_token)
    data = make_backup(db)
    fname = f"studyos-backup-{localnow().strftime('%Y%m%d-%H%M%S')}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/restore")
def restore(
    file: UploadFile,
    x_sync_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """从备份 zip 恢复：全量替换当前数据（保留本机同步令牌与远端配置）。"""
    _require_token(db, x_sync_token)
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(422, "请上传「完整备份」导出的 .zip 文件")
    data = file.file.read(MAX_BACKUP_BYTES + 1)
    if len(data) > MAX_BACKUP_BYTES:
        raise HTTPException(413, f"备份文件过大（上限 {MAX_BACKUP_BYTES // (1024 ** 3)}GB）")
    try:
        stats = restore_backup(db, data)
    except SyncError as e:
        raise HTTPException(422, str(e)) from e
    return {"ok": True, "stats": stats}
