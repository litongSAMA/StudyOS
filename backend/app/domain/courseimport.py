"""导入课表解析：iCalendar(.ics) 与 JSON 两种来源 → 课程条目（纯函数、可测）。

- **iCal**：教务系统（正方/强智等）、课表 App 导出的 .ics。循环事件
  （RRULE FREQ=WEEKLY + BYDAY + UNTIL/COUNT）按天展开 → 相对学期锚点（最早上课日
  所在周的周一）换算成周次列表；任课老师/上课地点从 LOCATION 与 DESCRIPTION
  （「教师：张三」「地点：教3-101」等写法）抽取；支持 EXDATE 排除（法定节假日）。
- **JSON**：本应用约定结构 `{courses:[{name, slots:[{weekday,start,end,weeks}]}]}`
  或扁平课表 App 风格 `{course_list:[{name,day,start,end,weeks}]}`；
  周次支持 `[1,3,5]` / `"1-16"` / `"1-16单|双"` / `"1,3,5-8"`；
  节次（start_period/end_period）按标准 12 节时间表换算成 "HH:MM"。

所有时间都是**本地墙上时间**（课表的语义是「周一 08:00 上课」，与设备时区无关）。
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")

# 标准 12 节时间表（国内高校通用节次边界，用于「第N节」→ 时刻换算）
PERIOD_START: dict[int, str] = {
    1: "08:00", 2: "08:55", 3: "10:00", 4: "10:55", 5: "14:00", 6: "14:55",
    7: "16:00", 8: "16:55", 9: "19:00", 10: "19:55", 11: "20:50", 12: "21:45",
}
PERIOD_MINUTES = 45  # 每节时长（用于 end_period → 结束时刻）

# 新课程自动配色（按导入顺序循环取色）
COURSE_PALETTE = [
    "#6366f1", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444",
    "#8b5cf6", "#ec4899", "#14b8a6", "#f97316", "#84cc16",
]

# 解析规模护栏（防恶意/异常文件拖垮服务）
MAX_EVENTS = 500
MAX_ENTRIES = 100
MAX_SLOTS = 50
MAX_WEEKS = 60
MAX_SCAN_DAYS = 420

_WEEKDAY_NAMES = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7,
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
}


class ImportParseError(ValueError):
    """课表内容无法解析（用户输入问题，接口返回 422 并带中文说明）。"""


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _minutes(t: str) -> int:
    """'HH:MM' → 当天分钟数。"""
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _add_minutes(t: str, delta: int) -> str:
    """'HH:MM' + 分钟数 → 'HH:MM'（跨天截断到 23:59）。"""
    total = min(_minutes(t) + delta, 24 * 60 - 1)
    return f"{total // 60:02d}:{total % 60:02d}"


def _parse_time(value: str) -> str:
    """'8:00' / '08:00' / '08:00:00' → 'HH:MM'。"""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*$", value or "")
    if not m:
        raise ImportParseError(f"时间格式无法识别：{value!r}（应为 HH:MM，如 08:00）")
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        raise ImportParseError(f"时间超出范围：{value!r}")
    return f"{h:02d}:{mi:02d}"


def period_to_time(period: int) -> str:
    """第 N 节 → 开始时刻（标准 12 节时间表）。"""
    if period not in PERIOD_START:
        raise ImportParseError(
            f"节次 {period} 不在 1~12 范围内（请在 1~12 之间取值或直接用 start/end 时间）"
        )
    return PERIOD_START[period]


def period_end_time(period: int) -> str:
    """第 N 节 → 结束时刻（开始 + 45 分钟）。"""
    return _add_minutes(period_to_time(period), PERIOD_MINUTES)


def _parse_weekday(value) -> int:
    """1~7（周一~周日）/ '周一' / '星期一' / 'mon' 等 → 1~7。"""
    if isinstance(value, bool):
        raise ImportParseError("星期取值无效")
    if isinstance(value, int):
        if 1 <= value <= 7:
            return value
        raise ImportParseError(f"星期 {value} 超出 1~7 范围（1=周一 … 7=周日）")
    s = str(value or "").strip().lower()
    if s in ("mo", "mon", "monday"):
        return 1
    if s in ("tu", "tue", "tuesday"):
        return 2
    if s in ("we", "wed", "wednesday"):
        return 3
    if s in ("th", "thu", "thursday"):
        return 4
    if s in ("fr", "fri", "friday"):
        return 5
    if s in ("sa", "sat", "saturday"):
        return 6
    if s in ("su", "sun", "sunday"):
        return 7
    m = re.match(r"^(?:周|星期|礼拜)?([一二三四五六日天1-7])$", s)
    if m:
        return _WEEKDAY_NAMES[m.group(1)]
    raise ImportParseError(f"星期无法识别：{value!r}（应为 1~7 或「周一」等写法）")


def parse_weeks(value) -> list[int]:
    """周次归一化：int / [1,3,5] / '1-16' / '1-16单' / '1-16双' / '1,3,5-8' → 升序去重列表。"""
    if value is None or value == "" or value == []:
        return []
    out: set[int] = set()

    def add(n):
        if not (1 <= n <= MAX_WEEKS):
            raise ImportParseError(f"周次 {n} 超出 1~{MAX_WEEKS} 范围")
        out.add(n)

    def add_range(a: int, b: int, step: int = 1):
        if a > b:
            a, b = b, a
        for n in range(a, b + 1, step):
            add(n)

    if isinstance(value, bool):
        raise ImportParseError("周次取值无效")
    if isinstance(value, (int, float)):
        if int(value) != value:
            raise ImportParseError(f"周次 {value!r} 不是整数")
        add(int(value))
        return sorted(out)
    if isinstance(value, (list, tuple, set)):
        for v in value:
            out.update(parse_weeks(v))
        return sorted(out)
    s = str(value).strip().replace("到", "-").replace("至", "-")
    if not s or s.lower() in ("全周", "每周", "all"):
        return []
    # '1-16周' / '第1-16周' / '1-16(单)' / '1-16单' / '1-16双'
    odd = even = False
    if re.search(r"[（(]?单[周）)]?$", s):
        odd, s = True, re.sub(r"[（(]?单[周）)]?$", "", s)
    elif re.search(r"[（(]?双[周）)]?$", s):
        even, s = True, re.sub(r"[（(]?双[周）)]?$", "", s)
    s = s.replace("第", "").replace("周", "")
    for token in s.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            a, b = token.split("-", 1)
            if not (a.strip().isdigit() and b.strip().isdigit()):
                raise ImportParseError(f"周次写法无法识别：{value!r}")
            a, b = int(a), int(b)
            step = 2 if odd or even else 1
            if odd and a % 2 == 0:
                a += 1  # 单周：从第一个奇数周开始
            if even and a % 2 == 1:
                a += 1  # 双周：从第一个偶数周开始
            add_range(a, b, step)
        else:
            if not token.isdigit():
                raise ImportParseError(f"周次写法无法识别：{value!r}")
            add(int(token))
    if odd:
        out = {n for n in out if n % 2 == 1}
    if even:
        out = {n for n in out if n % 2 == 0}
    return sorted(out)


def guess_semester(start: date | None) -> str:
    """按学期起始日期猜学期名（中国学年惯例：8 月后开学 = 秋季第 1 学期）。"""
    if not start:
        return ""
    y = start.year
    return f"{y}-{y+1}-1" if start.month >= 8 else f"{y-1}-{y}-2"


def pick_color(index: int) -> str:
    return COURSE_PALETTE[index % len(COURSE_PALETTE)]


# ---------------------------------------------------------------------------
# iCalendar 解析
# ---------------------------------------------------------------------------

def _unfold(text: str) -> list[str]:
    """RFC 5545 折行还原：把以空格/TAB 开头的续行拼回上一行。"""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    for line in lines:
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _parse_line(line: str) -> tuple[str, dict, str]:
    """'NAME;PARAM=V:VALUE' → (NAME, {param: [values]}, VALUE)。"""
    name, sep, rest = line.partition(":")
    if not sep:
        return line.strip().upper(), {}, ""
    params: dict = {}
    if ";" in name:
        name, _, param_str = name.partition(";")
        for chunk in param_str.split(";"):
            k, _, v = chunk.partition("=")
            params.setdefault(k.strip().upper(), []).append(v.strip())
    return name.strip().upper(), params, rest


def _unescape(s: str) -> str:
    return (
        s.replace("\\n", "\n").replace("\\N", "\n")
        .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    )


def _decode_quoted_printable(s: str) -> str:
    s = re.sub(r"=\r?\n", "", s)  # 软换行
    return re.sub(r"=([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), s)


def _decode_value(value: str, params: dict) -> str:
    encoding = (params.get("ENCODING") or [""])[0].upper()
    if encoding == "QUOTED-PRINTABLE":
        value = _decode_quoted_printable(value)
    charset = (params.get("CHARSET") or ["utf-8"])[0].lower()
    if charset in ("gb2312", "gbk", "gb18030"):
        try:
            # 线路文本被当作 latin-1 保存时，还原原始字节再按 gb18030 解码
            value = value.encode("latin-1").decode("gb18030", errors="replace")
        except Exception:
            pass
    return _unescape(value)


def _parse_datetime(value: str, params: dict) -> dict | None:
    """DTSTART/DTEND/EXDATE 值 → {date, time|None, all_day}。

    - TZID=Asia/Shanghai → 直接取墙上时间（教务系统本来就按本地时间写）；
    - 结尾 Z → UTC 时刻，转 Asia/Shanghai 取墙上时间；
    - VALUE=DATE / 纯日期 → 全天事件（节假日），time=None。
    """
    if not value:
        return None
    if (params.get("VALUE") or [""])[0].upper() == "DATE" or len(value) == 8:
        try:
            return {"date": date.fromisoformat(value), "time": None, "all_day": True}
        except ValueError:
            return None
    tzid = (params.get("TZID") or [""])[0]
    if value.endswith("Z"):
        try:
            dt = datetime.fromisoformat(value[:-1]).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        if tzid:
            try:
                dt = dt.astimezone(ZoneInfo(tzid))
            except Exception:
                dt = dt.astimezone(SHANGHAI)
        else:
            dt = dt.astimezone(SHANGHAI)
    else:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    return {"date": dt.date(), "time": dt.strftime("%H:%M"), "all_day": False}


def _parse_duration(value: str) -> int | None:
    """ISO-8601 时长 PT1H40M / PT45M / P1D → 分钟数。"""
    m = re.fullmatch(
        r"[+-]?P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value or "", re.I
    )
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 24 * 60 + h * 60 + mi + (1 if s else 0)


def _parse_rrule(value: str) -> dict:
    rule: dict = {"freq": "", "interval": 1, "byday": [], "until": None, "count": None}
    for part in value.split(";"):
        k, _, v = part.partition("=")
        k, v = k.strip().upper(), v.strip()
        if k == "FREQ":
            rule["freq"] = v
        elif k == "INTERVAL":
            try:
                rule["interval"] = max(1, int(v))
            except ValueError:
                pass
        elif k == "BYDAY":
            rule["byday"] = [
                _parse_weekday(x) for x in v.split(",") if x.strip()
            ]
        elif k == "UNTIL":
            rule["until"] = _parse_datetime(v, {})
        elif k == "COUNT":
            try:
                rule["count"] = int(v)
            except ValueError:
                pass
    return rule


def _parse_exdates(values: list[str], params: dict) -> set[date]:
    out: set[date] = set()
    for value in values:
        for piece in value.split(","):
            piece = piece.strip()
            if not piece:
                continue
            dt = _parse_datetime(piece, params)
            if dt:
                out.add(dt["date"])
    return out


def _expand_occurrences(start_d: date, rrule: dict | None, exdates: set[date]) -> list[date]:
    """把循环事件展开成上课日期列表（FREQ=WEEKLY 为主，兼容 DAILY）。"""
    if not rrule or not rrule.get("freq"):
        return [start_d] if start_d not in exdates else []
    freq = rrule["freq"]
    interval = rrule.get("interval", 1) or 1
    count = rrule.get("count")
    until: date | None = None
    if rrule.get("until"):
        until = rrule["until"]["date"]
    byday = rrule.get("byday") or [start_d.isoweekday()]
    if freq == "DAILY":
        byday = None
    elif freq != "WEEKLY":
        # 学期课表几乎不用其它频率；无法可靠展开时保守返回首日
        return [start_d] if start_d not in exdates else []

    out: list[date] = []
    if freq == "WEEKLY":
        week_anchor = start_d - timedelta(days=start_d.weekday())
        d = start_d
        scan_end = until or (start_d + timedelta(days=MAX_SCAN_DAYS))
        while d <= scan_end and len(out) < (count or 10**9):
            if (d - start_d).days > MAX_SCAN_DAYS:
                break
            week_idx = (d - week_anchor).days // 7
            if d.isoweekday() in byday and week_idx % interval == 0:
                if d not in exdates:
                    out.append(d)
            d += timedelta(days=1)
    else:  # DAILY
        d = start_d
        scan_end = until or (start_d + timedelta(days=MAX_SCAN_DAYS))
        while d <= scan_end and len(out) < (count or 10**9):
            if (d - start_d).days % interval == 0 and d not in exdates:
                out.append(d)
            d += timedelta(days=1)
    return out


_TEACHER_RE = re.compile(r"(?:任课教师|授课教师|主讲教师|教师|老师)[:：]\s*([^\n\r,;，；|〔【]+)")
_LOCATION_RE = re.compile(r"(?:上课地点|授课地点|地点|教室)[:：]\s*([^\n\r,;，；|〔【]+)")
_BRACKET_RE = re.compile(r"[【\[〔].*?[】\]〕]")


def _clean_name(name: str) -> str:
    s = _BRACKET_RE.sub("", name or "")
    s = re.sub(r"\s+", " ", s).strip(" -–—:：,，")
    return s[:120]


def _extract_teacher_location(description: str, location: str) -> tuple[str, str]:
    teacher, loc = "", (location or "").strip()
    desc = description or ""
    m = _TEACHER_RE.search(desc)
    if m:
        teacher = m.group(1).strip().strip(" -–—:：,，")
    if not loc:
        m = _LOCATION_RE.search(desc)
        if m:
            loc = m.group(1).strip().strip(" -–—:：,，")
    return teacher, loc


def parse_ics(text: str) -> dict:
    """iCalendar 文本 → {format, term_start_date, semester, entries}。

    同一 SUMMARY（课程名）的多个 VEVENT 合并为一个课程条目，同一
    (星期, 开始, 结束) 的时段并集周次；周次相对「全表最早上课日所在周的周一」。
    """
    events: list[dict] = []
    cur: dict | None = None
    for raw in _unfold(text):
        line = raw.strip()
        if not line:
            continue
        name, params, value = _parse_line(line)
        if name == "BEGIN" and value.upper() == "VEVENT":
            cur = {"summary": "", "location": "", "description": "",
                   "dtstart": None, "dtend": None, "duration": None,
                   "rrule": None, "exdates": set()}
            if len(events) >= MAX_EVENTS:
                break
        elif cur is not None:
            if name == "END" and value.upper() == "VEVENT":
                events.append(cur)
                cur = None
            elif name == "SUMMARY":
                cur["summary"] = _decode_value(value, params)
            elif name == "LOCATION":
                cur["location"] = _decode_value(value, params)
            elif name == "DESCRIPTION":
                cur["description"] = _decode_value(value, params)
            elif name == "DTSTART":
                cur["dtstart"] = _parse_datetime(value, params)
            elif name == "DTEND":
                cur["dtend"] = _parse_datetime(value, params)
            elif name == "DURATION":
                cur["duration"] = _parse_duration(value)
            elif name == "RRULE":
                cur["rrule"] = _parse_rrule(value)
            elif name == "EXDATE":
                cur["exdates"] |= _parse_exdates([value], params)
    if cur is not None:
        events.append(cur)

    # 展开每个事件的上课日期；统计全部日期求学期锚点（最早上课周的周一）
    dated: list[tuple[dict, list[date]]] = []
    for ev in events:
        dtstart = ev.get("dtstart")
        if not dtstart or dtstart["all_day"] or not dtstart.get("time"):
            continue  # 无开始时刻 / 全天事件（节假日等）不进课表
        occurrences = _expand_occurrences(
            dtstart["date"], ev.get("rrule"), ev.get("exdates")
        )
        if occurrences:
            dated.append((ev, occurrences))
    if not dated:
        raise ImportParseError(
            "未在 .ics 内容中找到带时间的课程事件（DTSTART 时刻缺失或全部为全天事件）"
        )

    all_dates = {d for _, occs in dated for d in occs}
    term_anchor = min(all_dates) - timedelta(days=min(all_dates).weekday())

    # 按课程名归并
    by_name: dict[str, dict] = {}
    for ev, occurrences in dated:
        name = _clean_name(ev.get("summary") or "")
        if not name:
            name = "未命名课程"
        entry = by_name.setdefault(name, {"name": name, "code": "", "teacher": "",
                                          "location": "", "slots": []})
        teacher, loc = _extract_teacher_location(
            ev.get("description") or "", ev.get("location") or ""
        )
        if teacher and not entry["teacher"]:
            entry["teacher"] = teacher[:120]
        if loc and not entry["location"]:
            entry["location"] = loc[:200]
        start_t = ev["dtstart"]["time"]
        if ev.get("dtend") and not ev["dtend"]["all_day"] and ev["dtend"].get("time"):
            end_t = ev["dtend"]["time"]
        elif ev.get("duration"):
            end_t = _add_minutes(start_t, ev["duration"])
        else:
            end_t = _add_minutes(start_t, PERIOD_MINUTES)
        # 同一 (weekday,start,end) 合并周次；但同一事件跨多天（BYDAY 多值）时按天拆分
        day_times: dict[int, tuple[str, str]] = {}
        for d in occurrences:
            day_times.setdefault(d.isoweekday(), (start_t, end_t))
        for weekday, (s_t, e_t) in day_times.items():
            weeks = sorted({
                (d - term_anchor).days // 7 + 1
                for d in occurrences if d.isoweekday() == weekday
            })
            slot = {"weekday": weekday, "start": s_t, "end": e_t,
                    "weeks": [w for w in weeks if 1 <= w <= MAX_WEEKS],
                    "location": loc, "teacher": teacher}
            merged = next(
                (s for s in entry["slots"]
                 if s["weekday"] == weekday and s["start"] == s_t and s["end"] == e_t),
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

    entries = list(by_name.values())
    for e in entries:
        e["slots"] = sorted(e["slots"], key=lambda s: (s["weekday"], s["start"]))[:MAX_SLOTS]
    return {
        "format": "ics",
        "term_start_date": term_anchor.isoformat(),
        "semester": guess_semester(term_anchor),
        "entries": entries[:MAX_ENTRIES],
    }


# ---------------------------------------------------------------------------
# JSON 解析
# ---------------------------------------------------------------------------

def _slot_from_dict(d: dict, defaults: dict | None = None) -> dict:
    """{weekday, start|start_period, end|end_period, weeks, location, teacher} → 归一化时段。"""
    if not isinstance(d, dict):
        raise ImportParseError("slots 里每一项必须是对象")
    defaults = defaults or {}
    weekday = _parse_weekday(d.get("weekday", d.get("day", defaults.get("weekday"))))
    start = d.get("start", d.get("start_time"))
    if start is None and d.get("start_period") is not None:
        start = period_to_time(int(d["start_period"]))
    if not start:
        raise ImportParseError(f"星期{weekday} 的时段缺少开始时间（start 或 start_period）")
    start = _parse_time(str(start))
    end = d.get("end", d.get("end_time"))
    if end is None and d.get("end_period") is not None:
        end = period_end_time(int(d["end_period"]))
    if not end:
        end = _add_minutes(start, PERIOD_MINUTES)
    end = _parse_time(str(end))
    if _minutes(end) <= _minutes(start):
        raise ImportParseError(f"结束时间 {end} 必须晚于开始时间 {start}")
    return {
        "weekday": weekday,
        "start": start,
        "end": end,
        "weeks": parse_weeks(d.get("weeks")),
        "location": str(d.get("location") or defaults.get("location") or "")[:200],
        "teacher": str(d.get("teacher") or defaults.get("teacher") or "")[:120],
    }


def _merge_entry(by_name: dict[str, dict], raw: dict) -> None:
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ImportParseError("课程条目缺少 name（课程名）字段")
    name = _clean_name(name)
    entry = by_name.setdefault(name, {"name": name, "code": "", "teacher": "",
                                      "location": "", "slots": []})
    for field, limit in (("code", 40), ("teacher", 120), ("location", 200)):
        if raw.get(field) and not entry[field]:
            entry[field] = str(raw[field])[:limit]
    defaults = {"location": entry["location"], "teacher": entry["teacher"]}
    slots = raw.get("slots")
    if isinstance(slots, list) and slots:
        for s in slots:
            slot = _slot_from_dict(s, defaults)
            merged = next(
                (x for x in entry["slots"]
                 if x["weekday"] == slot["weekday"] and x["start"] == slot["start"]
                 and x["end"] == slot["end"]),
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
    elif raw.get("weekday") is not None or raw.get("day") is not None:
        entry["slots"].append(_slot_from_dict(raw, defaults))


def parse_json(text: str) -> dict:
    """JSON 文本 → 条目。支持 `courses`（对象数组）与 `course_list`（扁平、逐条即一个时段）。"""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise ImportParseError(f"JSON 解析失败：{e}") from e
    if not isinstance(obj, dict):
        raise ImportParseError("JSON 顶层必须是对象 { courses: [...] } 或 { course_list: [...] }")

    start_date = obj.get("start_date") or None
    if start_date:
        try:
            date.fromisoformat(str(start_date))
        except ValueError as e:
            raise ImportParseError(f"start_date 格式应为 YYYY-MM-DD：{start_date!r}") from e

    by_name: dict[str, dict] = {}
    if "courses" in obj:
        courses = obj["courses"]
        if not isinstance(courses, list):
            raise ImportParseError("courses 必须是数组")
        for raw in courses[:MAX_ENTRIES]:
            _merge_entry(by_name, raw if isinstance(raw, dict) else {})
    elif "course_list" in obj:
        courses = obj["course_list"]
        if not isinstance(courses, list):
            raise ImportParseError("course_list 必须是数组")
        for raw in courses[:MAX_ENTRIES]:
            _merge_entry(by_name, raw if isinstance(raw, dict) else {})
    else:
        raise ImportParseError(
            "JSON 里没有 courses / course_list 字段。"
            "示例：{\"courses\":[{\"name\":\"操作系统\",\"slots\":[{\"weekday\":1,"
            "\"start\":\"08:00\",\"end\":\"09:40\",\"weeks\":\"1-16\"}]}]}"
        )

    if not by_name:
        raise ImportParseError("JSON 里没有可导入的课程条目")
    entries = list(by_name.values())
    for e in entries:
        e["slots"] = sorted(e["slots"], key=lambda s: (s["weekday"], s["start"]))[:MAX_SLOTS]
    return {
        "format": "json",
        "term_start_date": start_date,
        "semester": str(obj.get("semester") or "") or guess_semester(
            date.fromisoformat(start_date) if start_date else None
        ),
        "entries": entries[:MAX_ENTRIES],
    }


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def parse_import_source(text: str) -> dict:
    """入口：自动识别 iCal / JSON 并解析（解析失败抛 ImportParseError）。"""
    stripped = (text or "").lstrip()
    if not stripped:
        raise ImportParseError("内容为空：请上传 .ics 文件或粘贴课表内容")
    if stripped.startswith("{"):
        return parse_json(text)
    return parse_ics(text)


def merge_slots(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """把新时段并入已有课表：同 (weekday,start,end) 并集周次，其余追加。"""
    merged = [dict(s) for s in (existing or [])]
    for s in incoming:
        hit = next(
            (m for m in merged
             if m.get("weekday") == s.get("weekday")
             and m.get("start") == s.get("start")
             and m.get("end") == s.get("end")),
            None,
        )
        if hit:
            hit["weeks"] = sorted(set(hit.get("weeks") or []) | set(s.get("weeks") or []))
            if not hit.get("location") and s.get("location"):
                hit["location"] = s["location"]
            if not hit.get("teacher") and s.get("teacher"):
                hit["teacher"] = s["teacher"]
        else:
            merged.append(dict(s))
    return merged[:MAX_SLOTS]
