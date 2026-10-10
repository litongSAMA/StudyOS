"""AI 配置路由：LLM / Embedding 接入管理。

- 配置持久化到 app_settings（随多端同步带到其他设备，改一次处处生效）；
- 运行态立即生效（热更新，无需重启）；
- 密钥只进不出：读取时一律掩码，前端永远拿不到完整 Key；
- 连通性测试：分别探测 chat/completions 与 /embeddings 两个端点。
"""
from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.domain.agent.llm import llm_available
from app.infra.config import (
    AI_BASE_VALUES,
    AI_ENV_SET,
    AI_KEYS,
    PROVIDER_PRESETS,
    apply_ai_overrides,
    mask_key,
    reload_ai_from_db,
    settings,
)
from app.infra.db import get_db
from app.infra.models import AppSetting
from app.infra.netutil import trust_env_for_url
from app.domain.semantic import backend_name

router = APIRouter(prefix="/api/ai", tags=["ai"])

_TEST_TIMEOUT = 12.0
_KEYS = ("llm_api_key", "embed_api_key")
_URL_ATTRS = ("llm_base_url", "llm_model", "embed_base_url", "embed_model")


class AIConfigIn(BaseModel):
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    embed_base_url: str | None = None
    embed_api_key: str | None = None
    embed_model: str | None = None
    clear_llm_key: bool = False
    clear_embed_key: bool = False


class AITestIn(BaseModel):
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    embed_base_url: str | None = None
    embed_api_key: str | None = None
    embed_model: str | None = None
    use_saved: bool = True


def _db_row(db: Session, attr: str) -> AppSetting | None:
    return db.get(AppSetting, attr)


def _source(db: Session, attr: str) -> str:
    if _db_row(db, attr) is not None:
        return "db"
    if attr in AI_ENV_SET:
        return "env"
    return "default"


def _clear(db: Session, attr: str, changes: dict) -> None:
    row = _db_row(db, attr)
    if row:
        db.delete(row)
    changes[attr] = AI_BASE_VALUES.get(attr, "")


def config_view(db: Session) -> dict:
    """当前生效配置（密钥掩码）+ 状态。"""
    cfg: dict[str, dict] = {}
    for attr in AI_KEYS:
        value = getattr(settings, attr)
        cfg[attr] = {
            "value": mask_key(value) if attr in _KEYS else value,
            "source": _source(db, attr),
            "set": bool(value),
        }
    return {
        "presets": PROVIDER_PRESETS,
        "config": cfg,
        "status": {
            "llm": "configured" if llm_available() else "offline",
            "llm_base_url": settings.llm_base_url,
            "llm_model": settings.llm_model,
            "semantic": backend_name(),
            "semantic_key_set": bool(settings.embed_api_key),
        },
    }


def load_db_ai_settings(db: Session) -> None:
    """启动时把数据库里保存的 AI 配置加载进运行态（数据库 > 环境变量 > 默认值）。"""
    reload_ai_from_db(db)


@router.get("/config")
def get_ai_config(db: Session = Depends(get_db)):
    return config_view(db)


@router.put("/config")
def put_ai_config(body: AIConfigIn, db: Session = Depends(get_db)):
    """保存 AI 配置：非空写入，空串清除回落（env → 默认值），密钥留空/掩码占位保持不变。"""
    changes: dict = {}
    for attr in _URL_ATTRS:
        v = getattr(body, attr)
        if v is None:
            continue
        v = v.strip()
        if v:
            row = _db_row(db, attr)
            if row:
                row.value = v
            else:
                db.add(AppSetting(key=attr, value=v))
            changes[attr] = v
        else:
            _clear(db, attr, changes)

    if body.clear_llm_key:
        _clear(db, "llm_api_key", changes)
    elif body.llm_api_key is not None and body.llm_api_key.strip():
        v = body.llm_api_key.strip()
        if "•" not in v:  # 前端掩码占位符 → 保持不变
            row = _db_row(db, "llm_api_key")
            if row:
                row.value = v
            else:
                db.add(AppSetting(key="llm_api_key", value=v))
            changes["llm_api_key"] = v

    if body.clear_embed_key:
        _clear(db, "embed_api_key", changes)
    elif body.embed_api_key is not None and body.embed_api_key.strip():
        v = body.embed_api_key.strip()
        if "•" not in v:
            row = _db_row(db, "embed_api_key")
            if row:
                row.value = v
            else:
                db.add(AppSetting(key="embed_api_key", value=v))
            changes["embed_api_key"] = v

    apply_ai_overrides(changes)
    db.commit()
    return config_view(db)


# ---------------------------------------------------------------------------
# 连通性测试
# ---------------------------------------------------------------------------


def _probe(base: str, key: str, model: str, kind: str) -> dict:
    """探测一个端点：kind ∈ chat / embed。永不抛异常。"""
    base = (base or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "error": "未配置接口地址", "latency_ms": 0}
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if kind == "chat":
        payload = {
            "model": model or "",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 5,
        }
        path = "/chat/completions"
    else:
        payload = {"model": model or "", "input": ["你好"]}
        path = "/embeddings"
    started = time.perf_counter()
    try:
        # 探测本机/内网端点（本地 Ollama、局域网网关）时直连，避免代理把请求截走，
        # 把「连不上」误报成「服务返回 503」；公网服务仍沿用环境里的代理设置
        with httpx.Client(
            timeout=_TEST_TIMEOUT, trust_env=trust_env_for_url(base)
        ) as client:
            resp = client.post(f"{base}{path}", json=payload, headers=headers)
        latency_ms = int((time.perf_counter() - started) * 1000)
        if resp.status_code == 401 or resp.status_code == 403:
            if not key:
                # 没带密钥时的 401 不能报成「Key 无效」：用户根本没配 Key
                return {"ok": False, "error": "未配置 API Key：本次请求没有携带密钥（留空即使用规则引擎，无需联网）", "latency_ms": latency_ms}
            return {"ok": False, "error": "鉴权失败：API Key 无效或无权限", "latency_ms": latency_ms}
        if resp.status_code == 404:
            return {"ok": False, "error": "接口不存在：请检查地址是否含 /v1 且服务支持 OpenAI 兼容协议", "latency_ms": latency_ms}
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = str(resp.json().get("error", {}).get("message", resp.json().get("error", "")))[:160]
            except Exception:
                detail = resp.text[:160]
            return {"ok": False, "error": f"服务返回 {resp.status_code}：{detail or resp.reason_phrase}", "latency_ms": latency_ms}
        data = resp.json()
        if kind == "chat":
            content = data.get("choices", [{}])[0].get("message", {}).get("content")
            return {"ok": True, "latency_ms": latency_ms, "reply": (content or "")[:60],
                    "model": data.get("model", model)}
        rows = data.get("data", [])
        dim = len(rows[0].get("embedding", [])) if rows else 0
        return {"ok": True, "latency_ms": latency_ms, "dim": dim, "model": data.get("model", model)}
    except httpx.HTTPError as e:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {"ok": False, "error": f"无法连接：{e.__class__.__name__}", "latency_ms": latency_ms}
    except Exception as e:  # 响应格式异常等
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {"ok": False, "error": f"响应解析失败：{e}", "latency_ms": latency_ms}


def _form_key(value: str | None, saved: str) -> str:
    """表单里的密钥 → 实际用于探测的密钥：留空或掩码占位时沿用已保存的值。"""
    v = (value or "").strip()
    if not v or "•" in v:
        return saved or ""
    return v


@router.post("/test")
def test_ai(body: AITestIn, db: Session = Depends(get_db)):
    """分别测试 chat 与 embedding 端点（优先使用已保存配置，也可用表单临时值试连）。"""
    if body.use_saved:
        llm_base, llm_key, llm_model = (
            settings.llm_base_url, settings.llm_api_key, settings.llm_model,
        )
        emb_base, emb_key, emb_model = (
            settings.embed_base_url, settings.embed_api_key, settings.embed_model,
        )
    else:
        llm_base = body.llm_base_url or ""
        # 前端「测试连接」直接用输入框当前值试连：密钥留空/回传掩码时沿用已保存的
        # Key，否则「改了地址没保存就测试」会变成一次不带密钥的请求。
        llm_key = _form_key(body.llm_api_key, settings.llm_api_key)
        llm_model = body.llm_model or settings.llm_model
        emb_base = body.embed_base_url or ""
        emb_key = _form_key(body.embed_api_key, settings.embed_api_key)
        emb_model = body.embed_model or settings.embed_model

    chat = _probe(llm_base, llm_key, llm_model, "chat") if llm_base else {
        "ok": False, "error": "未配置 LLM 接口地址", "latency_ms": 0}
    embed = _probe(emb_base, emb_key, emb_model, "embed") if emb_base else {
        "ok": False, "error": "未配置 embedding 接口地址（留空则使用内置离线 TF-IDF）", "latency_ms": 0}
    return {"chat": chat, "embed": embed, "llm_available": llm_available()}
