"""pytest 全局配置。

关键设计：
1. 隔离临时数据库——必须在导入 app.db（创建引擎）之前替换 settings，
   测试绝不触碰真实的 data/studyos.db 与 data/files。
2. 强制离线规则引擎（llm_api_key=""）、关闭 OCR 模型加载——测试快且确定。
3. 每个用例结束后清空全部表，用例可任意顺序、任意组合执行。
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# ---- 必须先于 app.db / app.main 导入完成隔离配置 ----
from app.infra.config import settings  # noqa: E402


def _temp_root() -> Path:
    """临时库根目录。

    默认用系统临时目录（`tempfile.mkdtemp`）。可用 `STUDYOS_TEST_TMP` 指定一个
    已存在的目录作为根（CI 希望把临时库固定在构建目录、或系统临时目录不可写的
    受限环境）；指定时用 uuid 子目录 + makedirs，权限宽松（0o755），便于
    SQLite 与外部工具读写，路径可控可审计。
    """
    override = os.environ.get("STUDYOS_TEST_TMP", "").strip()
    if override:
        root = Path(override)
        root.mkdir(parents=True, exist_ok=True)
        run_dir = root / f"studyos-test-{uuid.uuid4().hex[:12]}"
        run_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
        return run_dir
    return Path(tempfile.mkdtemp(prefix="studyos-test-"))


_TMP = _temp_root()
settings.data_dir = _TMP
settings.db_path = _TMP / "test.db"
settings.vapid_keys_file = _TMP / "vapid_keys.json"
settings.ocr_enabled = False  # 测试不加载 OCR 模型（快 + 离线）
settings.llm_api_key = ""  # 强制规则引擎，保证离线确定性
settings.embed_base_url = ""  # 语义检索强制内置 TF-IDF，绝不联网
settings.embed_auto_ollama = False

from app.infra.db import SessionLocal  # noqa: E402
from app.api.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402


@pytest.fixture(scope="session")
def client():
    """会话级客户端：触发 lifespan → 在临时库上建表。

    client=("127.0.0.1", …)：模拟「本机浏览器」——同步令牌披露等敏感接口只允许 loopback，
    需要模拟局域网其它设备时用下面的 lan_client。
    """
    with TestClient(app, raise_server_exceptions=False, client=("127.0.0.1", 54321)) as c:
        yield c


@pytest.fixture(scope="session")
def lan_client(client):
    """模拟局域网内另一台设备（非 loopback 来源），用于验证敏感接口的收口。"""
    with TestClient(app, raise_server_exceptions=False, client=("192.168.1.50", 54322)) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_db(client):
    """每个用例后清空全部表（含 FTS 虚表），并复位运行态 AI 配置。"""
    yield
    with SessionLocal() as db:
        for t in (
            "focus_sessions", "quizzes", "notes", "material_reviews", "tasks", "review_plans", "materials",
            "wrong_questions",
            "checklist_shares", "checklist_templates", "checklists",
            "courses", "export_records", "app_settings", "tombstones",
            "push_deliveries", "push_subscriptions",
        ):
            db.execute(text(f"DELETE FROM {t}"))
        db.execute(text("DELETE FROM materials_fts"))
        db.execute(text("DELETE FROM semantic_vectors"))
        for table in ["agent_turns", "agent_conversations", "agent_plans", "agent_operations", "agent_remote_receipts", "auth_sessions", "users", "auth_flags", "auth_attempts", "auth_events"]:
            db.execute(text(f"DELETE FROM {table}"))
        db.commit()
    # AI 配置为运行态单例：用例写入后必须复位，保证测试顺序无关
    settings.llm_api_key = ""
    settings.llm_base_url = "https://api.deepseek.com/v1"
    settings.llm_model = "deepseek-chat"
    settings.embed_base_url = ""
    settings.embed_api_key = ""
    settings.embed_model = "BAAI/bge-m3"
