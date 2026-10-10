"""AI 配置路由：热更新、密钥掩码、清空回落、连通性测试。"""
from sqlalchemy import select

from app.infra.config import settings
from app.infra.db import SessionLocal
from app.infra.models import AppSetting

KEY = "sk-test1234567890abcd"


def test_ai_config_defaults(client):
    r = client.get("/api/ai/config")
    assert r.status_code == 200
    body = r.json()
    assert "deepseek" in body["presets"] and "ollama" in body["presets"]
    cfg = body["config"]
    assert cfg["llm_api_key"]["value"] == "" and cfg["llm_api_key"]["set"] is False
    assert cfg["llm_base_url"]["value"] == "https://api.deepseek.com/v1"
    assert cfg["llm_base_url"]["source"] == "default"
    assert body["status"]["llm"] == "offline"
    assert body["status"]["semantic"] == "tfidf"


def test_ai_put_hot_reload_and_masking(client):
    r = client.put("/api/ai/config", json={
        "llm_base_url": "https://api.deepseek.com/v1",
        "llm_api_key": KEY,
        "llm_model": "deepseek-chat",
    })
    assert r.status_code == 200
    cfg = r.json()["config"]
    # 密钥只回掩码，不回原文
    assert cfg["llm_api_key"]["value"] == "sk-••••••abcd"
    assert cfg["llm_api_key"]["set"] is True
    assert cfg["llm_api_key"]["source"] == "db"
    assert cfg["llm_model"]["source"] == "db"  # 请求里带上了 model，落库生效
    # 热更新：运行态立即生效，无需重启
    assert settings.llm_api_key == KEY
    assert r.json()["status"]["llm"] == "configured"
    assert client.get("/api/health").json()["llm"] == "configured"
    assert client.get("/api/health").json()["ai"]["llm_key_set"] is True
    # 数据库中保存的是原文
    with SessionLocal() as db:
        row = db.get(AppSetting, "llm_api_key")
        assert row.value == KEY


def test_ai_masked_placeholder_keeps_key(client):
    client.put("/api/ai/config", json={"llm_api_key": KEY})
    # 前端把掩码值原样提交 → 后端视为「保持不变」
    r = client.put("/api/ai/config", json={"llm_api_key": "sk-••••••abcd"})
    assert r.status_code == 200
    assert settings.llm_api_key == KEY
    with SessionLocal() as db:
        assert db.get(AppSetting, "llm_api_key").value == KEY


def test_ai_clear_key_and_url_fallback(client):
    client.put("/api/ai/config", json={
        "llm_base_url": "http://localhost:11434/v1",
        "llm_api_key": KEY,
        "llm_model": "qwen2.5:7b",
    })
    r = client.put("/api/ai/config", json={"clear_llm_key": True, "llm_base_url": ""})
    cfg = r.json()["config"]
    assert cfg["llm_api_key"]["set"] is False
    # 清空 base_url 回落环境变量/默认值
    assert cfg["llm_base_url"]["value"] == "https://api.deepseek.com/v1"
    assert cfg["llm_base_url"]["source"] == "default"
    with SessionLocal() as db:
        assert db.get(AppSetting, "llm_api_key") is None
        assert db.get(AppSetting, "llm_base_url") is None


def test_ai_embed_config(client):
    r = client.put("/api/ai/config", json={
        "embed_base_url": "https://api.siliconflow.cn/v1",
        "embed_api_key": "sk-embed-secret-1234",
        "embed_model": "BAAI/bge-m3",
    })
    cfg = r.json()["config"]
    assert cfg["embed_api_key"]["value"] == "sk-••••••1234"
    assert r.json()["status"]["semantic"] == "api:BAAI/bge-m3"
    # 清空 embedding key
    r = client.put("/api/ai/config", json={"clear_embed_key": True})
    assert r.json()["status"]["semantic_key_set"] is False


def test_ai_test_unreachable_and_missing(client):
    # 未配置 embedding → 提示使用离线 TF-IDF
    r = client.post("/api/ai/test", json={"use_saved": True})
    assert r.status_code == 200
    assert r.json()["embed"]["ok"] is False
    assert "TF-IDF" in r.json()["embed"]["error"]

    # 不可达地址：明确报错而非抛异常（端口 1 几乎必拒连）
    r = client.post("/api/ai/test", json={
        "use_saved": False,
        "llm_base_url": "http://127.0.0.1:1/v1",
        "llm_api_key": "sk-x",
        "llm_model": "test",
    })
    chat = r.json()["chat"]
    assert chat["ok"] is False
    assert "无法连接" in chat["error"] or "Connect" in chat["error"]
    assert isinstance(chat["latency_ms"], int)
