"""中文/中英混合时间表达式归一化。

支持：绝对日期（2025-11-30 / 11.30 / 11月30日 / 11/30）、
相对时间（今天/明天/后天/大后天、下周/下下周/本周 + 周几、
N天后、今晚/明晚、第N周周几[需学期锚点]）、
时刻（晚上8点 / 20:00 / 24:00 跨天）。

「今晚 24:00」「下周五」这类表达锚定**设备本地时钟**（断网可用），
产出带本地时区的 aware datetime；上层 Schema（UTCDateTime）统一换算为
UTC 入库——入库前不碰 UTC，语义不错位。

返回候选列表，每项带置信度与依据说明；无法解析时给出原因，
供上层进入「人工确认」流程。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone

from app.infra.timeutil import localize

WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
NUM_CN = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


@dataclass
class ParsedTime:
    dt: datetime
    expr: str
    confidence: float
    reason: str


def _cn_num(s: str) -> int | None:
    """中文数字转 int：'十二' → 12；'二十三' → 23；'三十' → 30。"""
    s = s.strip()
    if s in NUM_CN and s != "十":
        return NUM_CN[s]
    if "十" in s:
        left, _, right = s.partition("十")
        tens = NUM_CN.get(left, 1) if left else 1
        units = NUM_CN.get(right, 0) if right else 0
        return tens * 10 + units
    return None


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _default_eod(d: date) -> datetime:
    return datetime.combine(d, dtime(23, 59))


def _cn_hour(h: int, prefix: str | None) -> int:
    """中文时段前缀 → 24 小时制小时数。"""
    if not prefix:
        return h
    if prefix in ("早上", "早晨", "上午"):
        return h
    if prefix == "凌晨":
        return 0 if h == 12 else h
    if prefix in ("下午", "傍晚"):
        return 12 if h == 12 else (h + 12 if h < 12 else h)
    if prefix in ("中午", "正午"):
        return 12 if h == 12 else (h + 12 if h <= 2 else h)
    if prefix == "晚上":
        return 0 if h == 12 else (h + 12 if h < 12 else h)
    if prefix in ("夜里", "半夜", "深夜"):
        return 0 if h == 12 else h
    return h


def _parse_time_of_day(text: str) -> tuple[dtime | None, float, str, bool]:
    """解析时刻，返回 (time, 置信度, 依据, 是否跨到次日)。

    冒号式交给模式 1；「N点」一律走中文模式（防止「下午2点」被解析成 02:00）。
    「24:00」表示次日零点，next_day=True。
    """
    # 模式 1：冒号式 23:59 / 8:30
    m = re.search(r"(?<![\d点])(\d{1,2})[:：](\d{1,2})?", text)
    if m:
        h = int(m.group(1))
        mi = int(m.group(2) or 0)
        if h == 24 and mi == 0:
            return dtime(0, 0), 0.9, "24:00（次日零点）", True
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return dtime(h, mi), 0.95, f"时刻 {h:02d}:{mi:02d}", False
        return None, 0.0, "", False
    # 模式 2：24点整
    m = re.search(r"24\s*点(?:整|钟)?", text)
    if m:
        return dtime(0, 0), 0.9, "24点（次日零点）", True
    # 模式 3：中文时刻：晚上8点 / 下午3点 / 中午12点半
    m = re.search(
        r"(凌晨|早上|早晨|上午|中午|正午|下午|傍晚|晚上|夜里|半夜|深夜)?\s*"
        r"([零一二两三四五六七八九十]+|1[0-9]|2[0-3]|[0-9])\s*点"
        r"(半|[零一二三四五六七八九十]+|[0-5]?[0-9]分?)?", text)
    if m and "小时" not in m.group(0):
        prefix, hour_s, minute_s = m.group(1), m.group(2), m.group(3)
        h = _cn_num(hour_s) if hour_s and not hour_s.isdigit() else int(hour_s or 0)
        if minute_s:
            ms = minute_s.strip("分")
            mi = 30 if ms == "半" else (_cn_num(ms) if ms and not ms.isdigit() else int(ms or 0))
        else:
            mi = 0
        h = _cn_hour(h, prefix)
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return dtime(h, mi), 0.9, f"中文时刻 {m.group(0)}", False
    return None, 0.0, "", False


def _parse_absolute(text: str) -> list[ParsedTime]:
    """绝对日期：2025-11-30、11月30日、11.30、11/30、2025/11/30。"""
    out: list[ParsedTime] = []
    today = date.today()

    pats = [
        (r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})日?", 0.95),
        (r"(\d{1,2})月(\d{1,2})日?", 0.9),
        (r"(\d{1,2})[./](\d{1,2})(?![./\d])", 0.85),
    ]
    for pat, conf in pats:
        for m in re.finditer(pat, text):
            try:
                if m.lastindex == 3:
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                else:
                    mo, d = int(m.group(1)), int(m.group(2))
                    y = today.year
                    # 明显是过去的日期 → 明年（跨年 DDL）
                    if date(y, mo, d) < today - timedelta(days=180):
                        y += 1
                dd = date(y, mo, d)
                t, tconf, reason, next_day = _parse_time_of_day(text)
                if t and next_day:
                    dd += timedelta(days=1)
                base = datetime.combine(dd, t) if t else _default_eod(dd)
                out.append(ParsedTime(localize(base), m.group(0), min(conf, tconf) if t else conf,
                                      reason or f"绝对日期 {m.group(0)}"))
            except ValueError:
                continue
    return out


def _parse_relative(text: str, semester_start: date | None) -> list[ParsedTime]:
    out: list[ParsedTime] = []
    now = datetime.now()
    today = now.date()
    t, tconf, t_reason, t_next_day = _parse_time_of_day(text)
    has_time = t is not None

    def at(d: date, conf: float, reason: str, end_of_day: bool = False):
        dd = d + timedelta(days=1) if (t and t_next_day) else d
        dt = datetime.combine(dd, t) if t and not end_of_day else _default_eod(dd)
        out.append(ParsedTime(localize(dt), reason, conf, t_reason or reason))

    # 今天/明天/后天/大后天（注意顺序：大后天含「后天」，需先判）
    if "大后天" in text:
        at(today + timedelta(days=3), 0.95, "大后天")
    elif "后天" in text:
        at(today + timedelta(days=2), 0.95, "后天")
    elif "明天" in text or "明早" in text or "明晚" in text:
        at(today + timedelta(days=1), 0.95, "明天")
    elif "今天" in text or "今晚" in text or "今早" in text:
        at(today, 0.95, "今天")
    # N天后
    m = re.search(r"([0-9一二两三四五六七八九十]+)\s*个?\s*天(?:之|以)?后", text)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else (_cn_num(m.group(1)) or 0)
        if 0 < n <= 60:
            at(today + timedelta(days=n), 0.9, f"{m.group(0)}")
    # 第N周周几（需要学期锚点）——命中后不得再由「下周/本周周几」规则抢答
    has_week_expr = bool(re.search(r"第\s*[0-9一二两三四五六七八九十]+\s*周", text))
    # 下周/下下周 + 周几
    m = re.search(r"(下下|下|本|这)?\s*(?:个)?(?:周|星期|礼拜)([一二三四五六日天])", text)
    if m and not has_week_expr:
        prefix, wd = m.group(1), WEEKDAYS[m.group(2)]
        if prefix in ("下下",):
            base_mon = _monday_of(today) + timedelta(days=14)
        elif prefix in ("下",):
            base_mon = _monday_of(today) + timedelta(days=7)
        else:
            base_mon = _monday_of(today)
        d = base_mon + timedelta(days=wd)
        conf = 0.9 if prefix in ("下", "下下") else 0.8
        # 「本周三」若已过，仍是本周（低置信提醒）
        at(d, conf, f"{m.group(0)}")
    # 第N周周几（需要学期锚点）
    m = re.search(r"第\s*([0-9一二两三四五六七八九十]+)\s*周\s*(?:周|星期|礼拜)?([一二三四五六日天])?", text)
    if m:
        week = int(m.group(1)) if m.group(1).isdigit() else (_cn_num(m.group(1)) or 0)
        wd = WEEKDAYS[m.group(2)] if m.group(2) else 0
        if week > 0:
            if semester_start:
                d = _monday_of(semester_start) + timedelta(weeks=week - 1, days=wd)
                at(d, 0.85, f"第{week}周周{m.group(2) or '一'}（按学期 {semester_start} 计算）")
            else:
                # 无法定位 → 不产出候选，交给人工
                # year=1 的哨兵值（上层按 year>1900 过滤）；直接挂 UTC，
                # 不能走 localize()——astimezone 对远古年份会溢出
                out.append(ParsedTime(datetime(1, 1, 1, tzinfo=timezone.utc), m.group(0), 0.0,
                                      "缺少学期起始日期，无法定位周次"))
    # 单独的「周几」（最近的未来那天）
    m = re.search(r"(?:周|星期|礼拜)([一二三四五六日天])", text)
    if m and not has_week_expr and not any(x in text for x in ("下周", "这周", "本周")):
        wd = WEEKDAYS[m.group(1)]
        delta = (wd - today.weekday()) % 7
        d = today + timedelta(days=delta if delta else 0)
        at(d, 0.7, f"最近{m.group(0)}")
    return out


def parse_time_expr(text: str, semester_start: date | None = None) -> list[ParsedTime]:
    """解析文本中所有时间表达。返回按置信度降序的候选列表。"""
    if not text:
        return []
    cands = _parse_absolute(text) + _parse_relative(text, semester_start)
    valid = [c for c in cands if c.dt.year > 1900]
    unresolved = [c for c in cands if c.dt.year <= 1900]
    valid.sort(key=lambda c: c.confidence, reverse=True)
    return valid + unresolved


def parse_single(text: str, semester_start: date | None = None) -> ParsedTime | None:
    """取最高置信度候选；无候选返回 None。"""
    cands = [c for c in parse_time_expr(text, semester_start) if c.dt.year > 1900]
    return cands[0] if cands else None
