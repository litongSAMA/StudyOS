"""出站 HTTP 的共用判定：本机/内网地址不走代理。

背景（真实缺陷）：httpx 默认 `trust_env=True`，会读 `HTTP_PROXY` / `HTTPS_PROXY` /
`ALL_PROXY` 环境变量。用户机器上只要设过代理（公司代理、抓包工具、某些 IDE 注入），
指向**本机**的调用就会被代理截走——典型后果：

- 本地 Ollama（`http://localhost:11434/v1`）连不上，AI 连通性测试报
  「服务返回 503」而不是「无法连接」；
- 内置离线 embedding / 本机语义服务被绕上一圈，慢或直接失败；
- 局域网内的自建 OpenAI 兼容网关同样受影响。

因此：目标是 loopback／私有网段／`.local` 时显式 `trust_env=False`，让这类请求
**直连**；公网服务仍沿用用户的代理设置（企业环境里那才是必需的）。
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

LOCAL_HOSTNAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}


def is_local_url(url: str) -> bool:
    """URL 是否指向本机/私有网段/`.local`（这类地址不应走代理）。"""
    try:
        host = (urlparse(url).hostname or "").strip().lower()
    except ValueError:
        return False
    if not host:
        return False
    if host in LOCAL_HOSTNAMES or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # 普通域名（可能是公网服务）→ 交给用户代理设置
    return ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified


def trust_env_for_url(url: str) -> bool:
    """该 URL 是否允许使用环境变量里的代理：本机/内网一律 False（直连）。"""
    return not is_local_url(url)
