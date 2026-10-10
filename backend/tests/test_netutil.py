"""出站请求的代理判定：本机/内网端点必须直连，不被 HTTP_PROXY 截走。

回归背景（真实缺陷）：httpx 默认 `trust_env=True`，会读 `HTTP_PROXY` / `ALL_PROXY`。
用户机器上装过代理（公司代理、抓包工具、某些 IDE 注入）时，指向本机的调用会被代理
截走——表现为「本地 Ollama 连不上」，AI 连通性测试报「服务返回 503」而不是
「无法连接」，让人误以为服务坏了。修复：loopback / 私有网段 / `.local` 一律
`trust_env=False` 直连，公网服务仍沿用用户代理设置。
"""
from __future__ import annotations

import pytest

from app.infra.netutil import is_local_url, trust_env_for_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434/v1",
        "http://localhost:11434/v1",
        "http://LocalHost/v1",
        "http://[::1]:8800/v1",
        "http://0.0.0.0:8800/v1",
        "http://192.168.1.20:8800/api",
        "http://10.0.0.5:8800/api",
        "http://172.16.3.9:8800/api",
        "http://169.254.10.10/api",
        "http://studyos.local/api",
    ],
)
def test_local_and_lan_urls_bypass_proxy(url):
    assert is_local_url(url) is True
    assert trust_env_for_url(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "https://api.deepseek.com/v1",
        "https://api.openai.com/v1",
        "http://example.com/api/share/abc",
        "http://ollama.internal.corp/v1",  # 普通域名（不是 .local）→ 交给用户代理设置
    ],
)
def test_public_urls_still_use_user_proxy(url):
    """公网服务走用户代理设置（企业/校园网里这往往是必需的），不能被一刀切关掉。"""
    assert is_local_url(url) is False
    assert trust_env_for_url(url) is True


def test_malformed_url_is_not_treated_as_local():
    assert is_local_url("") is False
    assert is_local_url("not a url") is False
    assert trust_env_for_url("") is True


def test_llm_and_embedding_clients_use_the_helper():
    """防止回退：LLM / embedding / 连通性测试 / 同步 / 分享抓取都必须带上代理判定。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app"
    for rel in (
        "domain/agent/llm.py",
        "domain/semantic.py",
        "api/routers/ai.py",
        "domain/sync.py",
        "api/routers/checklists.py",
    ):
        source = (root / rel).read_text(encoding="utf-8")
        assert "trust_env_for_url" in source, f"{rel} 缺少代理判定（本机请求会被代理截走）"
