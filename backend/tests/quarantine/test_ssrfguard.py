"""出站请求安全校验：链接本地 / 云元数据等危险地址必须被拒绝，合法目标放行。

说明：对「主机名」目标的校验会做 DNS 解析，为避免测试依赖真实网络（且环境 DNS
可能极慢/不可达导致挂起），这里统一用 monkeypatch 的 getaddrinfo 返回确定性结果，
保证用例离线、可重复。
"""
from __future__ import annotations

import socket

import pytest

from app.ssrfguard import UnsafeOutboundError, check_outbound_url


def _fake_getaddrinfo(resolved_ip: str):
    """构造 getaddrinfo 桩：任何主机名都解析到给定 IP。"""
    def fake(host, port, *a, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (resolved_ip, port or 0))]
    return fake


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",  # 云元数据
    "http://169.254.1.1:8800/api/share/x",       # 链接本地
    "http://169.254.2.2/x",                      # 链接本地
    "http://0.0.0.0:8800/x",                     # 未指定地址
    "http://224.0.0.1/x",                        # 多播
    "ftp://192.168.1.10:8800",                   # 非 http/https
    "file:///etc/passwd",
])
def test_rejects_unsafe_outbound(client, url):
    with pytest.raises(UnsafeOutboundError):
        check_outbound_url(url)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:11434/v1",          # 本地 Ollama（loopback 合法）
    "http://localhost:8800",
    "http://192.168.1.10:8800/api/share/x",   # 局域网 StudyOS（合法）
    "http://10.0.0.5:8800/api/share/x",       # 局域网 StudyOS（合法）
    "https://api.deepseek.com/v1",
    "https://example.com/api/share/x",
])
def test_allows_safe_outbound(client, monkeypatch, url):
    # 主机名类目标：mock 解析到公网 IP，避免依赖真实 DNS
    monkeypatch.setattr("app.ssrfguard.socket.getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    check_outbound_url(url)  # 不抛异常即通过


def test_rejects_hostname_resolving_to_link_local(client, monkeypatch):
    """主机名解析到链接本地/云元数据地址同样必须拒绝（DNS 重绑定缓解）。"""
    monkeypatch.setattr("app.ssrfguard.socket.getaddrinfo", _fake_getaddrinfo("169.254.169.254"))
    with pytest.raises(UnsafeOutboundError):
        check_outbound_url("http://metadata.internal/api/share/x")


def test_allows_hostname_resolving_to_safe(client, monkeypatch):
    """主机名解析到公网地址放行。"""
    monkeypatch.setattr("app.ssrfguard.socket.getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    check_outbound_url("http://sync.example.com/api/share/x")
