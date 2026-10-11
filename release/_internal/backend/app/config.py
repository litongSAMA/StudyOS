"""StudyOS 配置：全部可通过环境变量覆盖。"""
import os
from pathlib import Path

from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/


class Settings(BaseModel):
    app_name: str = "StudyOS"
    # 数据存储（本地优先；DATA_DIR 换成云盘/网络路径即实现云端存储）
    data_dir: Path = Path(
        (BASE_DIR / "data").as_posix()
    )
    db_path: Path = Path((BASE_DIR / "data" / "studyos.db").as_posix())

    # LLM 适配层：OpenAI 兼容协议（DeepSeek / OpenAI / Ollama 均可）
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_timeout: float = 30.0

    # 语义检索 embedding 后端（OpenAI 兼容 /embeddings 接口，如 SiliconFlow BGE、
    # 本地 Ollama nomic-embed-text 等）。留空则自动使用内置离线 TF-IDF 向量。
    embed_base_url: str = ""
    embed_api_key: str = ""
    embed_model: str = "BAAI/bge-m3"
    embed_timeout: float = 30.0
    embed_auto_ollama: bool = True  # LLM 指向本地 Ollama 时自动复用其 embedding
    embed_model_explicit: bool = False  # 用户显式指定过 EMBED_MODEL

    # 解析管线开关
    ocr_enabled: bool = True  # 无 OCR 依赖时自动降级

    # Web Push：VAPID 密钥对（持久化到 data 目录，重启后订阅仍有效）
    vapid_keys_file: Path = Path((BASE_DIR / "data" / "vapid_keys.json").as_posix())
    vapid_subject: str = "mailto:studyos@localhost"  # VAPID 声明里的联系邮箱
    push_tick_seconds: int = 60  # DDL 提醒调度间隔（检查是否到了提前 N 小时的提醒点）

    # 同步令牌等敏感信息只允许在本机（loopback）显式披露；
    # 反向代理/远程访问场景可用 ALLOW_REMOTE_ADMIN=1 放行（自行承担风险）。
    allow_remote_admin: bool = False

    # CORS（开发期前端）
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


settings = Settings()

# AI 相关配置项：支持「数据库运行时配置 > 环境变量 > 默认值」三级来源
AI_KEYS: tuple[str, ...] = (
    "llm_base_url", "llm_api_key", "llm_model",
    "embed_base_url", "embed_api_key", "embed_model",
)

# 常见服务商预设（前端选择后自动填 base_url / model）
PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "deepseek": {
        "label": "DeepSeek",
        "llm_base_url": "https://api.deepseek.com/v1",
        "llm_model": "deepseek-chat",
        "embed_base_url": "",
        "embed_model": "BAAI/bge-m3",
    },
    "openai": {
        "label": "OpenAI",
        "llm_base_url": "https://api.openai.com/v1",
        "llm_model": "gpt-4o-mini",
        "embed_base_url": "https://api.openai.com/v1",
        "embed_model": "text-embedding-3-small",
    },
    "siliconflow": {
        "label": "硅基流动 SiliconFlow",
        "llm_base_url": "https://api.siliconflow.cn/v1",
        "llm_model": "deepseek-ai/DeepSeek-V3",
        "embed_base_url": "https://api.siliconflow.cn/v1",
        "embed_model": "BAAI/bge-m3",
    },
    "moonshot": {
        "label": "月之暗面 Kimi",
        "llm_base_url": "https://api.moonshot.cn/v1",
        "llm_model": "moonshot-v1-8k",
        "embed_base_url": "",
        "embed_model": "BAAI/bge-m3",
    },
    "zhipu": {
        "label": "智谱 GLM",
        "llm_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "llm_model": "glm-4-flash",
        "embed_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "embed_model": "embedding-3",
    },
    "ollama": {
        "label": "本地 Ollama（免 Key）",
        "llm_base_url": "http://localhost:11434/v1",
        "llm_model": "qwen2.5:7b",
        "embed_base_url": "http://localhost:11434/v1",
        "embed_model": "nomic-embed-text",
    },
    "custom": {
        "label": "自定义（OpenAI 兼容）",
        "llm_base_url": "",
        "llm_model": "",
        "embed_base_url": "",
        "embed_model": "BAAI/bge-m3",
    },
}


def mask_key(key: str) -> str:
    """密钥掩码：仅返回前后缀，避免整串回传前端。"""
    key = (key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:3]}••••••{key[-4:]}"


def apply_ai_overrides(values: dict) -> None:
    """把 AI 配置写入运行态 settings（立即生效，无需重启）。"""
    for k, v in values.items():
        if k in AI_KEYS and v is not None:
            setattr(settings, k, str(v).strip())


def reload_ai_from_db(db) -> None:
    """把数据库里保存的 AI 配置重载进运行态（启动时 / 同步合并后调用）。"""
    from .models import AppSetting  # 延迟导入避免循环依赖

    changes: dict = {}
    for attr in AI_KEYS:
        row = db.get(AppSetting, attr)
        if row and row.value:
            changes[attr] = row.value
    apply_ai_overrides(changes)


def _apply_env() -> None:
    """环境变量覆盖（README 配置表与实现保持一致）。"""
    _str = {
        "LLM_API_KEY": "llm_api_key",
        "LLM_BASE_URL": "llm_base_url",
        "LLM_MODEL": "llm_model",
        "EMBED_BASE_URL": "embed_base_url",
        "EMBED_API_KEY": "embed_api_key",
        "EMBED_MODEL": "embed_model",
        "DATA_DIR": "data_dir",
    }
    for env_key, attr in _str.items():
        v = os.environ.get(env_key, "").strip()
        if v:
            setattr(settings, attr, Path(v) if attr == "data_dir" else v)
    if os.environ.get("EMBED_MODEL", "").strip():
        settings.embed_model_explicit = True
    for attr in ("llm_timeout", "embed_timeout"):
        v = os.environ.get(attr.upper(), "").strip()
        if v:
            try:
                setattr(settings, attr, float(v))
            except ValueError:
                pass
    for attr in ("ocr_enabled", "embed_auto_ollama", "allow_remote_admin"):
        v = os.environ.get(attr.upper(), "").strip()
        if v:
            setattr(settings, attr, v.lower() in ("1", "true", "yes", "on"))
    for attr in ("push_tick_seconds",):
        v = os.environ.get(attr.upper(), "").strip()
        if v:
            try:
                setattr(settings, attr, max(5, int(v)))
            except ValueError:
                pass
    if os.environ.get("VAPID_SUBJECT", "").strip():
        settings.vapid_subject = os.environ["VAPID_SUBJECT"].strip()
    if os.environ.get('DB_PATH','').strip(): settings.db_path=Path(os.environ['DB_PATH'])
    settings.vapid_keys_file=Path(os.environ.get('VAPID_KEYS_FILE') or settings.data_dir/'vapid_keys.json')
    # db_path 跟随 data_dir（除非显式覆盖）
    if "DATA_DIR" in os.environ and "DB_PATH" not in os.environ:
        settings.db_path = Path(settings.data_dir) / "studyos.db"


_apply_env()
settings.data_dir.mkdir(parents=True, exist_ok=True)

# 「环境变量/默认值」基准快照：清空数据库配置时回落到此值
AI_BASE_VALUES: dict[str, str] = {k: getattr(settings, k) for k in AI_KEYS}
# 哪些 AI 项来自环境变量（而非内置默认值），供状态展示
AI_ENV_SET: frozenset[str] = frozenset(
    k for k in AI_KEYS if os.environ.get(k.upper(), "").strip()
)
