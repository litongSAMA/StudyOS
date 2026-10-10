"""LLM 适配层：OpenAI 兼容协议（DeepSeek / OpenAI / 本地 Ollama 均可）。

未配置 API Key 或调用失败时返回 None——上层自动降级到规则引擎，
保证离线可用（答辩演示不依赖网络）。
"""
from __future__ import annotations

import json
import re

import httpx

from app.infra.config import settings
from app.infra.netutil import trust_env_for_url


def llm_available() -> bool:
    """是否启用 LLM 通道：配置了 Key，或指向本地无 Key 服务（Ollama/LM Studio）。"""
    if settings.llm_api_key:
        return True
    base = settings.llm_base_url.lower()
    return base.startswith(("http://localhost", "http://127.0.0.1", "http://0.0.0.0")) or "11434" in base


def _strip_code_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s


def chat_json(
    system: str, user: str, max_tokens: int = 2000, timeout: float | None = None
) -> dict | None:
    """请求结构化 JSON 输出。失败/未配置返回 None。"""
    if not llm_available():
        return None
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
        "temperature": 0.1,
    }
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    try:
        # 本机/内网端点（如本地 Ollama）显式直连，不走 HTTP_PROXY，
        # 否则代理会把 127.0.0.1 的请求截走、报出与真实原因无关的错误
        with httpx.Client(
            timeout=timeout or settings.llm_timeout,
            trust_env=trust_env_for_url(settings.llm_base_url),
        ) as client:
            resp = client.post(
                f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                json=payload, headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return json.loads(_strip_code_fence(content))
    except Exception:
        return None
