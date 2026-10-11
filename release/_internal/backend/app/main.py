"""StudyOS 后端主应用。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import SessionLocal, init_db
from .routers import (
    agent, ai, checklists, courses, exports, forecast, ingest, materials, misc,
    notes, push, review, search, share, sync, tasks, wrongbook,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    from .auth import init_auth
    init_auth()
    from .core_bridge import get_core
    get_core()
    with SessionLocal() as db:
        ai.load_db_ai_settings(db)  # 数据库里保存的 AI 配置 → 运行态（热加载）
    from .push import start_push_scheduler, stop_push_scheduler

    start_push_scheduler()  # DDL 提醒调度：到点经推送服务发 Web Push（关页也能收）
    try:
        yield
    finally:
        await stop_push_scheduler()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # 分页与导出元信息走响应头，跨源（开发前端直连后端）时也要能读到
    expose_headers=[
        "Content-Disposition",
        "X-Total-Count", "X-Limit", "X-Offset", "X-Has-More", "X-Tag-Filter",
        "X-Export-Kind", "X-Export-Format", "X-Export-Incremental",
        "X-Export-Since", "X-Export-Until", "X-Export-Count", "X-Export-Record-Id",
    ],
)

# 本机访问锁使用 Bearer 会话，同时保留写操作的同源校验（恶意网页可诱导浏览器向
# 127.0.0.1 发「简单请求」——multipart 上传/恢复等）。凡写操作一律校验
# Origin 与 Host 同源；开发前端（vite 代理会改写 Host）走白名单放行。
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_SAFE_ORIGINS = {*(settings.cors_origins)}


@app.middleware("http")
async def csrf_origin_guard(request, call_next):
    if request.method in _UNSAFE_METHODS:
        origin = request.headers.get("origin")
        if origin is not None:
            host = request.headers.get("host", "")
            if origin not in _SAFE_ORIGINS and urlparse(origin).netloc != host:
                # 中间件里抛 HTTPException 会被 ServerErrorMiddleware 吞成 500，直接返回响应
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    status_code=403,
                    content={"detail": "跨站请求被拒绝：请在 StudyOS 页面内操作"},
                )
    return await call_next(request)

from .routers import auth, agent_core, focus, quiz, report
app.include_router(auth.router)
app.include_router(agent.router)
app.include_router(agent_core.router)
app.include_router(focus.router)
app.include_router(quiz.router)
app.include_router(report.router)

@app.middleware("http")
async def login_guard(request, call_next):
    if request.url.path.startswith('/api/') and request.method != 'OPTIONS' and request.url.path != '/api/health' and not request.url.path.startswith(('/api/auth/', '/api/share/')):
        from .auth import authorize_request
        from starlette.concurrency import run_in_threadpool
        from fastapi.responses import JSONResponse
        try:
            request.state.agent_scope = await run_in_threadpool(authorize_request, request)
        except HTTPException as error:
            return JSONResponse(status_code=error.status_code,content={'detail':error.detail},headers=error.headers)
    return await call_next(request)


app.include_router(courses.router)
app.include_router(checklists.router)
app.include_router(checklists.templates_router)
app.include_router(checklists.shares_router)
app.include_router(share.router)
app.include_router(ingest.router)
app.include_router(materials.router)
app.include_router(tasks.router)
app.include_router(search.router)
app.include_router(notes.router)
app.include_router(wrongbook.router)
app.include_router(review.router)
app.include_router(forecast.router)
app.include_router(exports.router)
app.include_router(misc.router)
app.include_router(ai.router)
app.include_router(sync.router)
app.include_router(push.router)


@app.get("/api/health")
def health():
    from .agent.llm import llm_available
    from .ingest.ocr import ocr_available
    from .semantic import backend_name

    return {
        "status": "ok",
        "app": settings.app_name,
        "llm": "configured" if llm_available() else "offline(rule engine)",
        "ocr": "available" if ocr_available() else "unavailable",
        "semantic": backend_name(),
        "ai": {
            "llm_key_set": bool(settings.llm_api_key),
            "llm_base_url": settings.llm_base_url,
            "llm_model": settings.llm_model,
            "embed_key_set": bool(settings.embed_api_key),
            "embed_base_url": settings.embed_base_url,
        },
    }


# 生产模式：托管前端构建产物（web/dist）+ SPA 回退
import os
_web_dist = Path(os.environ.get("WEB_DIST") or Path(__file__).resolve().parent.parent.parent / "web" / "dist").resolve()
if _web_dist.exists():
    from fastapi.responses import FileResponse

    app.mount("/assets", StaticFiles(directory=str(_web_dist / "assets")), name="web-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404, "Not Found")
        candidate = (_web_dist / full_path).resolve()
        if (
            full_path
            and candidate.is_file()
            and candidate.is_relative_to(_web_dist)
        ):
            return FileResponse(candidate)
        return FileResponse(_web_dist / "index.html")
