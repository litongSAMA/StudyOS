"""统一时间规范：存储一律 UTC，对外一律带时区。

时间流转约定（跨时区双设备同步不再互相偏移的关键）：

1. **DB 层**：所有 datetime 列存 **naive UTC**（无时区、按 UTC 解释的墙上时间）。
   SQLAlchemy 的 SQLite DateTime 类型不保留 tzinfo，naive UTC 是唯一能在
   「SQL 比较 / LWW 冲突解决 / 增量导出基线」三处都保持一致口径的存储形式；
2. **API / 快照边界**：一律带时区。输出为 `...Z`（UTC），输入接受
   `Z` / `±HH:MM` / 无后缀 ISO（无后缀按 UTC 解释——新规范写入的全部是 UTC，
   老前端直发本地时间的行为已随前端同步修正）；
4. **离线可用**：时间来源只有设备本地时钟（`datetime.now()`）+ 系统时区偏移换算，
   不依赖任何网络时间服务（NTP/API）；断网时 UTC 与本地换算照常工作；
5. **面向用户展示**（导出文件、日志、复习计划的「今天」）：按**设备本地时区**
   换算，仅发生在展示层，绝不写回存储。

历史数据迁移见 `db._migrate_utc_timestamps()`：旧库 naive 值按「本机本地时间」
一次性折算为 UTC（跨设备同步来的旧行无法还原原始时区，按本机时区 best-effort，
两端各自升级后经一轮 LWW 同步收敛）。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated

from pydantic import BeforeValidator, PlainSerializer

UTC = timezone.utc


# ---------------------------------------------------------------------------
# 基础换算
# ---------------------------------------------------------------------------


def utcnow() -> datetime:
    """当前 UTC 时刻（naive UTC，用于入库与比较）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def localnow() -> datetime:
    """当前本地时刻（aware，仅用于展示文案 / 本地日期语义）。"""
    return datetime.now().astimezone()


def to_utc(dt: datetime) -> datetime:
    """任何 datetime → naive UTC。带时区按偏移换算；无时区视为 UTC。"""
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def parse_iso(s: str) -> datetime:
    """ISO 字符串 → naive UTC。支持 `Z` 与 `±HH:MM`；无后缀视为 UTC。"""
    return to_utc(datetime.fromisoformat(str(s).strip().replace("Z", "+00:00")))


def as_aware_utc(dt: datetime | None) -> datetime | None:
    """naive UTC → aware UTC（供 API / 快照输出）。"""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def iso_utc(dt: datetime | None) -> str | None:
    """→ `YYYY-MM-DDTHH:MM:SS.ffffffZ`（跨设备可无损比较的规范输出）。"""
    dt = as_aware_utc(dt)
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def localize(dt: datetime) -> datetime:
    """naive → 视为**设备本地时间**并附加本地时区（时间表达解析「今晚 24:00」用）。"""
    return dt if dt.tzinfo is not None else dt.astimezone()


def local_datetime(dt: datetime) -> datetime:
    """naive UTC → 本地时区 aware（展示 / 按用户本地日期计算）。"""
    return dt.replace(tzinfo=UTC).astimezone()


def local_date(dt: datetime) -> date:
    """naive UTC → 用户本地日期（复习计划「今天/考前一天」这类日期语义用）。"""
    return local_datetime(dt).date()


def legacy_naive_to_utc(dt: datetime) -> datetime:
    """迁移专用：旧库 naive 值原为「本机本地时间」→ naive UTC。

    naive.astimezone(UTC) 会把 naive 视为系统本地时间并正确换算
    （含历史 DST 规则）；带时区的值同样按偏移换算。两者可共用同一表达式。
    """
    return dt.astimezone(UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Pydantic 类型：输入归一化为 naive UTC，JSON 输出统一 `...Z`
# ---------------------------------------------------------------------------


def _normalize_utc(v) -> datetime:
    """API 输入 → naive UTC：字符串走 parse_iso，datetime 走 to_utc。"""
    if isinstance(v, str):
        return parse_iso(v)
    return to_utc(v)


def _serialize_utc(dt: datetime) -> str:
    return iso_utc(dt)


UTCDateTime = Annotated[
    datetime,
    BeforeValidator(_normalize_utc),
    PlainSerializer(_serialize_utc, return_type=str, when_used="json-unless-none"),
]
