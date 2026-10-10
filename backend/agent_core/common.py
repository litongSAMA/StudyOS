from datetime import datetime, timezone
import hashlib
import json
import math
import re
import unicodedata


class AgentError(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status, self.detail = status, detail


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def normalize(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip().casefold()


def utc(value):
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError("timezone required")
        return date.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (AttributeError, TypeError, ValueError):
        raise AgentError(422, "时间必须是有效且带时区的 ISO 日期时间") from None


def text(value, field, limit=300, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise AgentError(422, f"{field} 格式或长度无效")
    return value.strip()


def number(value, field, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise AgentError(422, f"{field} 必须在 {low} 到 {high} 之间")
    return value


def identifier(value, field="id"):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AgentError(422, f"{field} 必须是正整数")
    return value
