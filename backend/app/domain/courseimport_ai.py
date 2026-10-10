"""课表截图导入的 AI 智能兜底通道：OCR 文本 → LLM 抽取课程条目（纯函数、可测）。

定位：当 `courseimport_ocr` 的 OCR + 网格重建无法产出可用课表（表格线丢失、串列、
倾斜、合并单元格被拆散等）时，把整段 OCR 文本交给已配置的 LLM 做最后一道语义抽取。

设计要点：
- LLM 输出**一律不可信**：字段别名、时间、星期、周次全部穷举归一化；
  坏时段静默跳过，绝不把异常抛给调用方。
- 返回结构与 `courseimport.parse_ics` 的 entries 同构：
  `{"name","code","teacher","location","slots":[{"weekday","start","end","weeks",
  "location","teacher"}]}`，可直接喂给导入接口 / `courseimport._normalize_entries`。
- 去重规则（与 parse_ics / parse_json 保持一致）：**去掉【】标注后同名即同一门课**，
  合并为一条；同 (weekday, start, end) 的时段**并集周次**，其余时段追加保留。
- LLM 未配置 / 调用失败 / 结果完全不可用 → 返回 None，由调用方给出「识别失败，
  请手动录入或换更清晰的截图」一类中文提示，**绝不返回空课表冒充成功**。

注：写本文件时 `courseimport` 里还没有 `_normalize_entries`（那边仍在并行改动），
所以归一化由本模块自己完成；若之后 `courseimport` 补上了 `_normalize_entries`，
`_pass_through_course_normalize()` 会自动把结果再交给它收口。
"""
from __future__ import annotations

import re
from datetime import date

from app.domain import courseimport
from app.domain.agent.llm import chat_json, llm_available
from .courseimport import (
    MAX_ENTRIES,
    MAX_SLOTS,
    MAX_WEEKS,
    _clean_name,
    _parse_time,
    _parse_weekday,
    guess_semester,
    parse_weeks,
    period_end_time,
    period_to_time,
)

# 护栏：OCR 文本截断长度、要求 LLM 输出的 token 上限、单次归一化的原始条目上限
_MAX_OCR_CHARS = 12000
_MAX_TOKENS = 4000
_MAX_RAW_COURSES = MAX_ENTRIES * 5
# 时段缺少结束时间（或 end <= start）时的兜底时长：与 courseimport 的每节 45 分钟一致
_DEFAULT_DURATION = 45

# ---------------------------------------------------------------------------
# 提示词：system 讲死规则（含标准 12 节时间表），user 只放 OCR 原文
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """你是课表 OCR 文本的结构化抽取器，只做「忠实抽取」，绝不做任何推测。

输出要求：
1. 只输出一个 JSON 对象，不要解释、不要 Markdown 代码块，结构固定为：
{"courses":[{"name":"课程名","teacher":"任课教师","location":"上课地点",
"slots":[{"weekday":1,"start":"08:00","end":"09:40","weeks":"1-16"}]}],
"term_start_date":"YYYY-MM-DD" 或 null}
2. weekday 用 1~7 表示周一~周日；start/end 必须是真实时刻 "HH:MM"（24 小时制）。
3. 「第N节」「第N-M节」按标准 12 节时间表换算成时刻：
1:08:00-08:45, 2:08:55-09:40, 3:10:00-10:45, 4:10:55-11:40, 5:14:00-14:45,
6:14:55-15:40, 7:16:00-16:45, 8:16:55-17:40, 9:19:00-19:45, 10:19:55-20:40,
11:20:50-21:35, 12:21:45-22:30。
4. 一个格子里写「第3-4节」时算**一个**时段：start 取第 3 节开始 10:00、end 取第 4 节结束 11:40，
不要拆成两个时段；跨节次同理。
5. weeks 用 "1-16" / "1-16单" / "1-16双" / "1,3,5-8" 这类字符串；文本没写周次就省略该字段。
6. 同一门课（课程名相同，忽略空格与【】等标注）只输出一条，它的多个时段全部放进 slots。
7. 绝不编造：文本里没有的课程、教师、地点、时间、周次一律省略该字段（宁可缺字段，也不许猜）。
8. term_start_date 只在文本明确写出开学/第 1 周日期时填写，否则填 null。"""


def _build_user_prompt(text: str) -> str:
    """拼 user 提示词：OCR 原文截断到 12000 字，并提醒噪声特征。"""
    clipped = text[:_MAX_OCR_CHARS]
    tail = ""
    if len(text) > _MAX_OCR_CHARS:
        tail = f"\n（OCR 原文共 {len(text)} 字，超出部分已截断，仅提供前 {_MAX_OCR_CHARS} 字）"
    return (
        "下面是课表截图的 OCR 文本。它来自图像识别，经常有多余空格、换行错位、形近字/数字"
        "误识（如 0/O、1/l、8/B）、列串位、顺序打乱，还会混入页面标题、页码、水印、按钮文字"
        "等与课程无关的噪声。请按语义还原课表，忽略这些噪声；看不清或不确定的内容按「省略字段」"
        "处理，不要猜。" + tail + "\n"
        "===== OCR 文本开始 =====\n" + clipped + "\n===== OCR 文本结束 =====\n"
        "请严格按 system 中约定的 JSON 结构输出。"
    )


# ---------------------------------------------------------------------------
# 字段别名：LLM 每次回答用的键名都可能不一样，这里穷举常见写法
# ---------------------------------------------------------------------------

_COURSE_LIST_KEYS = ("courses", "course_list", "courses_list", "courseList", "课程列表")
_WRAPPER_KEYS = ("data", "result", "items", "list", "payload", "课程")
_NAME_KEYS = ("name", "course_name", "courseName", "title", "课程名", "课程名称", "course", "课程")
_CODE_KEYS = ("code", "course_code", "courseCode", "课程号", "课程代码")
_TEACHER_KEYS = ("teacher", "teachers", "instructor", "tutor", "教师", "老师", "任课教师")
_LOCATION_KEYS = ("location", "place", "room", "classroom", "地点", "教室", "上课地点")
_SLOT_LIST_KEYS = ("slots", "slot_list", "slotList", "times", "periods", "schedule", "时段")
_WEEKDAY_KEYS = ("weekday", "week_day", "day", "week", "星期", "周几")
_TIME_TEXT_KEYS = ("time", "period", "periods", "section", "sections", "when", "节次", "时间", "上课时间")
_WEEKS_KEYS = ("weeks", "week_range", "weekRange", "week_list", "周次", "week")
_START_KEYS = (
    "start", "start_time", "startTime", "begin", "begin_time",
    "start_period", "startPeriod", "period_start", "periodStart", "开始时间", "开始节次",
)
_END_KEYS = (
    "end", "end_time", "endTime", "finish", "finish_time",
    "end_period", "endPeriod", "period_end", "periodEnd", "结束时间", "结束节次",
)
# LLM 偶尔把「无 / 未知」当课程名填进来，这些一律视为没有名字
_BOGUS_NAMES = {
    "", "-", "--", "—", "无", "暂无", "未知", "未命名", "未命名课程", "待定",
    "unknown", "none", "null", "n/a", "na",
}

_CN_WEEKDAY_RE = re.compile(r"(?:周|星期|礼拜)\s*([一二三四五六日天1-7])")
_EN_WEEKDAY_RE = re.compile(
    r"\b(mon|monday|tue|tues|tuesday|wed|wednesday|thu|thur|thurs|thursday|"
    r"fri|friday|sat|saturday|sun|sunday)\b",
    re.I,
)
_PERIOD_RE = re.compile(r"第?\s*(\d{1,2})\s*(?:[-~—–至到]\s*(\d{1,2})\s*)?节")
_CLOCK_RANGE_RE = re.compile(r"(\d{1,2}:\d{1,2})\s*[-~—–至到]\s*(\d{1,2}:\d{1,2})")
_SINGLE_CLOCK_RE = re.compile(r"\d{1,2}:\d{1,2}")
_FULLWIDTH_TRANS = str.maketrans("：．０１２３４５６７８９", ":." + "0123456789")


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _dedupe(seq) -> list[int]:
    """保序去重。"""
    return list(dict.fromkeys(seq))


def _minutes(t: str) -> int:
    """'HH:MM' → 当天分钟数。"""
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _plus_minutes(t: str, delta: int) -> str:
    """'HH:MM' + 分钟 → 'HH:MM'（跨天截断到 23:59）。"""
    total = min(_minutes(t) + delta, 24 * 60 - 1)
    return f"{total // 60:02d}:{total % 60:02d}"


def _clean_time_str(value) -> str:
    """把 '8：00' / '8点' / '12点半' / '9:5' / '08:00:00' 洗成 _parse_time 能吃的写法。"""
    s = str(value if value is not None else "").strip().translate(_FULLWIDTH_TRANS)
    s = s.replace("点", ":").replace("时", ":").replace("半", "30")
    s = re.sub(
        r"(\d{1,2}):(\d{1,2})",
        lambda m: f"{int(m.group(1)):02d}:{int(m.group(2)):02d}",
        s,
    )
    s = re.sub(r"\b(\d{1,2}):(?!\d)", lambda m: f"{int(m.group(1)):02d}:00", s)
    return s


def _safe_time(value) -> str | None:
    """任意时间写法 → 'HH:MM'（统一走 courseimport._parse_time 校验）；无法识别返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return _parse_time(_clean_time_str(value))
    except Exception:
        return None


def _as_period(value) -> int | None:
    """节次取值 → 1~12 的 int（'3' / 3 / '第3节' 都认）；否则 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if int(value) != value:
            return None
        n = int(value)
    else:
        m = re.search(r"\d{1,2}", str(value))
        if not m:
            return None
        n = int(m.group(0))
    return n if 1 <= n <= 12 else None


def _period_time(value, *, end: bool) -> str | None:
    """节次 → 时刻（统一走 period_to_time / period_end_time）；失败返回 None。"""
    n = _as_period(value)
    if n is None:
        return None
    try:
        return period_end_time(n) if end else period_to_time(n)
    except Exception:
        return None


def _first_text(raw: dict, keys) -> str:
    """按 keys 顺序取第一个非空字符串字段。"""
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return ""


def _first_present(raw: dict, keys):
    """按 keys 顺序取第一个「非空」字段值（保留原类型，交给下游解析）。"""
    for key in keys:
        value = raw.get(key)
        if value is None or value == "" or value == [] or value == {}:
            continue
        return value
    return None


def _safe_weeks(value) -> list[int]:
    """周次归一化：parse_weeks 为主，越界/怪写法走兜底解析，最后夹取到 1..MAX_WEEKS。"""
    if value is None or value == "" or value == [] or value == {}:
        return []
    weeks: list[int] = []
    try:
        weeks = parse_weeks(value)
    except Exception:
        weeks = []
    if not weeks:
        weeks = _weeks_best_effort(value)
    return sorted({w for w in weeks if 1 <= w <= MAX_WEEKS})


def _weeks_best_effort(value) -> list[int]:
    """parse_weeks 抛错（如 '1-99' 越界）时的兜底：抠数字、夹取范围、尊重单/双周。"""
    out: set[int] = set()
    texts = [str(v) for v in value] if isinstance(value, (list, tuple, set)) else [str(value)]
    for text in texts:
        t = text.replace("到", "-").replace("至", "-")
        odd, even = "单" in t, "双" in t
        for m in re.finditer(r"(\d{1,3})\s*-\s*(\d{1,3})", t):
            a, b = sorted((int(m.group(1)), int(m.group(2))))
            out.update(n for n in range(a, b + 1) if 1 <= n <= MAX_WEEKS)
        for m in re.finditer(r"\d{1,3}", t):
            n = int(m.group(0))
            if 1 <= n <= MAX_WEEKS:
                out.add(n)
        if odd:
            out = {n for n in out if n % 2 == 1}
        if even:
            out = {n for n in out if n % 2 == 0}
    return sorted(out)


def _weekdays_from_text(text) -> list[int]:
    """从任意文本里抠星期：「周一」「星期三」「礼拜天」「Mon」等。"""
    out: list[int] = []
    s = str(text or "")
    for m in _CN_WEEKDAY_RE.finditer(s):
        try:
            out.append(_parse_weekday("周" + m.group(1)))
        except Exception:
            continue
    for m in _EN_WEEKDAY_RE.finditer(s):
        try:
            out.append(_parse_weekday(m.group(1)))
        except Exception:
            continue
    return _dedupe(out)


def _as_weekdays(value) -> list[int]:
    """任意取值 → 星期列表（统一走 courseimport._parse_weekday）；无法识别返回 []。"""
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, (list, tuple, set)):
        out: list[int] = []
        for item in value:
            out.extend(_as_weekdays(item))
        return _dedupe(out)
    if isinstance(value, (int, float)):
        if int(value) != value:
            return []
        try:
            return [_parse_weekday(int(value))]
        except Exception:
            return []
    s = str(value).strip()
    if not s:
        return []
    parts = [p for p in re.split(r"[,，、/|;；\s]+", s) if p]
    if len(parts) > 1:
        out = []
        for p in parts:
            out.extend(_as_weekdays(p))
        if out:
            return _dedupe(out)
    found = _weekdays_from_text(s)
    if found:
        return found
    try:
        return [_parse_weekday(s)]
    except Exception:
        return []


def _weekdays_of(raw: dict) -> list[int]:
    """取时段的星期：weekday → day → week（仅非数字，数字的 week 更像周次）→ 时间文本里抠。"""
    for key in _WEEKDAY_KEYS:
        if key not in raw:
            continue
        value = raw.get(key)
        if key == "week" and isinstance(value, (int, float)) and not isinstance(value, bool):
            continue
        days = _as_weekdays(value)
        if days:
            return days
    return _weekdays_from_text(_time_text_of(raw))


def _time_text_of(raw: dict) -> str:
    """把「时间/节次」类字段拼成一段可解析的文本（只收标量，忽略嵌套对象）。"""
    parts: list[str] = []
    for key in _TIME_TEXT_KEYS:
        value = raw.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            parts.append(_clean_time_str(value))
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, (str, int, float)) and not isinstance(item, bool):
                    parts.append(_clean_time_str(item))
    return " ".join(p for p in parts if p)


def _period_numbers(raw: dict) -> list[int]:
    """「period: 5」「periods: [3,4]」「section: '第3节'」这类纯节次写法 → 节次列表。"""
    out: list[int] = []
    for key in ("period", "periods", "section", "sections", "节次"):
        if key not in raw:
            continue
        value = raw.get(key)
        items = value if isinstance(value, (list, tuple)) else [value]
        for item in items:
            n = _as_period(item)
            if n is not None and n not in out:
                out.append(n)
        if out:
            return out
    return out


def _range_from_text(raw: dict) -> tuple[str | None, str | None, int | None, int | None]:
    """从时段的时间文本里抠 (start时刻, end时刻, 开始节次, 结束节次)，抠不到给 None。

    优先认时刻（更明确），同时保留节次；两者都没有时再看 period/periods 的纯数字写法。
    """
    text = _time_text_of(raw)
    start_t = end_t = None
    start_p = end_p = None
    if text:
        m = _CLOCK_RANGE_RE.search(text)
        if m:
            start_t, end_t = _safe_time(m.group(1)), _safe_time(m.group(2))
        m = _PERIOD_RE.search(text)
        if m:
            start_p = _as_period(m.group(1))
            end_p = _as_period(m.group(2)) if m.group(2) else start_p
        if start_t is None:
            m = _SINGLE_CLOCK_RE.search(text)
            if m:
                start_t = _safe_time(m.group(0))
    if start_p is None:
        periods = _period_numbers(raw)
        if periods:
            start_p, end_p = periods[0], periods[-1]
    return start_t, end_t, start_p, end_p


def _first_clock(raw: dict, keys, *, end: bool) -> str | None:
    """按 keys 顺序取第一个可解析的时刻；写成节次（'start': 3）也容错换算。"""
    for key in keys:
        if key not in raw:
            continue
        value = raw[key]
        t = _safe_time(value)
        if t:
            return t
        t = _period_time(value, end=end)
        if t:
            return t
    return None


def _repair_end(start: str) -> str | None:
    """结束时刻缺失或 end <= start 时的兜底：开始 + 45 分钟（23:59 起上课则丢弃）。"""
    end = _plus_minutes(start, _DEFAULT_DURATION)
    if _minutes(end) > _minutes(start):
        return end
    end = _plus_minutes(start, 1)
    return end if _minutes(end) > _minutes(start) else None


# ---------------------------------------------------------------------------
# 时段 / 条目归一化
# ---------------------------------------------------------------------------

def _normalize_slot(raw, defaults: dict) -> dict | None:
    """单个时段 → 归一化 slot；无法凑出星期或开始时刻时返回 None（调用方静默跳过）。"""
    if not isinstance(raw, dict):
        return None
    weekdays = _weekdays_of(raw)
    if not weekdays:
        return None
    start_t, end_t, start_p, end_p = _range_from_text(raw)

    start = _first_clock(raw, _START_KEYS, end=False)
    if start is None:
        start = start_t
    if start is None and start_p is not None:
        start = _period_time(start_p, end=False)
    if start is None:
        return None

    end = _first_clock(raw, _END_KEYS, end=True)
    if end is None:
        end = end_t
    if end is None and end_p is not None:
        end = _period_time(end_p, end=True)
    if end is None or _minutes(end) <= _minutes(start):
        end = _repair_end(start)  # 缺失 / 颠倒 → 开始 + 45 分钟
    if end is None:
        return None

    weeks = _safe_weeks(_first_present(raw, _WEEKS_KEYS))
    location = (_first_text(raw, _LOCATION_KEYS) or defaults.get("location") or "")[:200]
    teacher = (_first_text(raw, _TEACHER_KEYS) or defaults.get("teacher") or "")[:120]
    return {
        "weekday": weekdays[0],  # 其余星期在下面展开成独立时段
        "start": start,
        "end": end,
        "weeks": weeks,
        "location": location,
        "teacher": teacher,
    }


def _expand_slot(raw, defaults: dict) -> list[dict]:
    """一个原始时段 → 归一化 slot 列表（weekday 写成数组时展开成多天）。"""
    base = _normalize_slot(raw, defaults)
    if base is None:
        return []
    if not isinstance(raw, dict):
        return [base]
    weekdays = _weekdays_of(raw)
    if len(weekdays) <= 1:
        return [base]
    return [{**base, "weekday": day} for day in weekdays]


def _raw_slots(raw: dict) -> list:
    """条目下的时段列表：兼容 slots 数组、单个 slot 对象、['周一 08:00-09:40'] 文本数组。"""
    if not isinstance(raw, dict):
        return []
    for key in _SLOT_LIST_KEYS:
        value = raw.get(key)
        if value is None:
            continue
        items = [value] if isinstance(value, dict) else (value if isinstance(value, list) else [])
        out: list = []
        for item in items:
            if isinstance(item, dict):
                out.append(item)
            elif isinstance(item, str) and item.strip():
                out.append({"time": item})
        # 全是标量的 periods/section（如 [3, 4]）不算时段列表，交给下面的扁平分支
        if out:
            return out
    # 完全没有 slots → 条目本身就是「扁平的一行」（课表 App 的 course_list 风格）
    if any(k in raw for k in ("weekday", "week_day", "day", "week")) or _time_text_of(raw):
        return [raw]
    return []


def _entry_name(raw) -> str:
    """条目的课程名（清洗【】等标注、清掉「无/未知」这类占位名）；没有可用名字返回 ''。"""
    if isinstance(raw, str):
        return _clean_name(raw)
    if not isinstance(raw, dict):
        return ""
    name = _clean_name(_first_text(raw, _NAME_KEYS))
    if name.lower() in _BOGUS_NAMES:
        return ""
    return name


def _unwrap_entry(raw):
    """兼容 {"course": {...}} / {"data": {...}} 这类多包一层的写法。"""
    if not isinstance(raw, dict) or _entry_name(raw):
        return raw
    for key in ("course", "data", "info", "detail"):
        inner = raw.get(key)
        if isinstance(inner, dict) and _entry_name(inner):
            return {**inner, **{k: v for k, v in raw.items() if k != key}}
    return raw


def _normalize_entry(by_name: dict[str, dict], raw) -> None:
    """把一条原始课程并入 by_name（去重规则见模块 docstring）；没有可用名字/时段的丢弃。"""
    raw = _unwrap_entry(raw)
    name = _entry_name(raw)
    if not name:
        return
    if not isinstance(raw, dict):
        return
    entry = by_name.setdefault(
        name, {"name": name, "code": "", "teacher": "", "location": "", "slots": []}
    )
    for field, keys, limit in (
        ("code", _CODE_KEYS, 40),
        ("teacher", _TEACHER_KEYS, 120),
        ("location", _LOCATION_KEYS, 200),
    ):
        if entry[field]:
            continue
        value = _first_text(raw, keys)
        if value:
            entry[field] = value[:limit]

    defaults = {"location": entry["location"], "teacher": entry["teacher"]}
    for raw_slot in _raw_slots(raw):
        for slot in _expand_slot(raw_slot, defaults):  # 坏时段在这里被静默跳过
            merged = next(
                (
                    x for x in entry["slots"]
                    if x["weekday"] == slot["weekday"]
                    and x["start"] == slot["start"]
                    and x["end"] == slot["end"]
                ),
                None,
            )
            if merged:
                merged["weeks"] = sorted(set(merged["weeks"]) | set(slot["weeks"]))
                if not merged["location"] and slot["location"]:
                    merged["location"] = slot["location"]
                if not merged["teacher"] and slot["teacher"]:
                    merged["teacher"] = slot["teacher"]
            else:
                entry["slots"].append(slot)


def _entries_from_payload(payload) -> list[dict]:
    """LLM 的原始 JSON → 归一化 entries（可能为空列表；绝不在坏数据上抛异常）。"""
    by_name: dict[str, dict] = {}
    for raw in _raw_courses(payload)[:_MAX_RAW_COURSES]:
        try:
            _normalize_entry(by_name, raw)
        except Exception:
            continue  # 单条坏数据不影响其它课程
    entries = [e for e in by_name.values() if e["slots"]]
    for entry in entries:
        entry["slots"] = sorted(entry["slots"], key=lambda s: (s["weekday"], s["start"]))[:MAX_SLOTS]
    return entries[:MAX_ENTRIES]


def _raw_courses(payload) -> list:
    """从 LLM 答案里取课程数组：courses / course_list / 包装字段 / 裸数组 / 名字→信息 映射。"""
    if isinstance(payload, list):
        return list(payload)
    if not isinstance(payload, dict):
        return []
    for key in _COURSE_LIST_KEYS + _WRAPPER_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, dict) and any(k in value for k in _COURSE_LIST_KEYS + _WRAPPER_KEYS):
            return _raw_courses(value)  # {"data": {"courses": [...]}}
        if isinstance(value, dict):
            return [_named(k, v) for k, v in value.items()]
        if isinstance(value, list):
            return value
        return []
    # 顶层直接写成 {课程名: 信息}（少见，但 LLM 偶尔这么答）
    if payload and all(isinstance(v, (dict, list)) for v in payload.values()):
        return [_named(k, v) for k, v in payload.items()]
    return []


def _named(name, info) -> dict:
    """{课程名: 信息} 映射的一项 → 条目 dict（键名当课程名，信息里同名键优先）。"""
    if isinstance(info, dict):
        return {"name": name, **info}
    return {"name": name, "slots": info if isinstance(info, list) else [info]}


def _term_start_of(payload) -> str | None:
    """取 LLM 报告的开学日期：必须是合法 ISO 日期，否则 None（绝不猜）。"""
    if not isinstance(payload, dict):
        return None
    for key in ("term_start_date", "start_date", "semester_start", "term_start"):
        value = payload.get(key)
        if value is None or value == "" or str(value).strip().lower() in ("null", "none", "无", "未知"):
            continue
        text = str(value).strip().replace("/", "-").replace(".", "-")[:10]
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            continue
    return None


def _pass_through_course_normalize(entries: list[dict]) -> list[dict]:
    """若 courseimport 提供了 _normalize_entries，则优先让它收口（失败/形状不对则用本模块结果）。

    写本文件时 courseimport 里还没有这个函数，因此归一化主要由上面的
    `_entries_from_payload` 完成；这里只是给并行改动留的兼容钩子。
    """
    normalize = getattr(courseimport, "_normalize_entries", None)
    if not callable(normalize):
        return entries
    try:
        out = normalize(entries)
    except Exception:
        return entries
    if isinstance(out, list) and all(
        isinstance(e, dict) and e.get("name") and isinstance(e.get("slots"), list) for e in out
    ):
        return out
    return entries


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------

def _extract(text: str, timeout: float) -> tuple[list[dict], str | None] | None:
    """调用 LLM 并归一化 → (entries, term_start_date)；任何一步不可用/失败返回 None。"""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    try:
        if not llm_available():  # 未配置 Key → 直接跳过，不做无谓的网络等待
            return None
        payload = chat_json(
            _SYSTEM_PROMPT, _build_user_prompt(cleaned), max_tokens=_MAX_TOKENS, timeout=timeout
        )
    except Exception:
        return None
    if payload is None:
        return None
    try:
        entries = _pass_through_course_normalize(_entries_from_payload(payload))
    except Exception:
        return None
    if not entries:
        return None
    return entries, _term_start_of(payload)


def llm_extract_entries(text: str, timeout: float = 60.0) -> list[dict] | None:
    """把课表图片的 OCR 文本交给 LLM 抽取课程条目。

    返回 [{"name","code","teacher","location","slots":[{"weekday","start","end","weeks":[...],
    "location","teacher"}]}]（与 courseimport.parse_ics 的 entries 同构，可直接喂给
    courseimport._normalize_entries）；LLM 不可用/调用失败/结果完全不可用时返回 None。
    """
    try:
        got = _extract(text, timeout)
    except Exception:
        return None
    return got[0] if got else None


def llm_extract_source(text: str, timeout: float = 60.0) -> dict | None:
    """AI 兜底通道的完整结果：{format:"image", ai_assisted:True, term_start_date, semester, entries}。

    term_start_date 只认 LLM 报告的合法 ISO 日期（否则 None），semester 由
    `courseimport.guess_semester` 按开学日期推断；无可用课程时返回 None。
    """
    try:
        got = _extract(text, timeout)
        if not got:
            return None
        entries, term_start = got
        start_date = date.fromisoformat(term_start) if term_start else None
        return {
            "format": "image",
            "ai_assisted": True,
            "term_start_date": term_start,
            "semester": guess_semester(start_date),
            "entries": entries,
        }
    except Exception:
        return None
