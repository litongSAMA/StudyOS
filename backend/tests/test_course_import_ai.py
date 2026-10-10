"""课表图片 AI 智能兜底：LLM 输出归一化（不联网、不配置 Key，用替身覆盖 chat_json）。

LLM 的输出一律不可信：字段别名、中文星期、字符串节次、坏时段、重复课程都要能
归一化；完全不可用时返回 None（让调用方给出明确提示），绝不返回空课表冒充成功。
"""
from __future__ import annotations

import pytest

from app.domain import courseimport_ai
from app.domain.courseimport import ImportParseError


@pytest.fixture
def fake_llm(monkeypatch):
    """把 LLM 通道换成替身：可用 + 固定返回值；返回安装函数。"""
    monkeypatch.setattr(courseimport_ai, "llm_available", lambda: True)

    def install(payload):
        calls = {}

        def _chat(system, user, max_tokens=2000, timeout=None):
            calls["system"] = system
            calls["user"] = user
            if isinstance(payload, Exception):
                raise payload
            return payload

        monkeypatch.setattr(courseimport_ai, "chat_json", _chat)
        return calls

    return install


def names(entries) -> list[str]:
    return [e["name"] for e in entries]


def slots_of(entries, name) -> list[tuple]:
    entry = next(e for e in entries if e["name"] == name)
    return [(s["weekday"], s["start"], s["end"], s["weeks"]) for s in entry["slots"]]


def test_messy_payload_is_normalised(fake_llm):
    """字符串节次 / 中文星期 / 单双周 / 重复课程 / 坏时段 / 空名 全都要收口。"""
    fake_llm({"courses": [
        {"name": "高等数学", "teacher": "李四", "location": "教1-202",
         "slots": [{"weekday": "周一", "start_period": 1, "end_period": 2, "weeks": "1-16单"}]},
        # 同名重复：应合并为一条，周次取并集
        {"name": "高等数学", "slots": [{"weekday": 1, "start": "08:00", "end": "09:40", "weeks": "1-16"}]},
        # end < start：应修成 start + 45 分钟，而不是丢弃
        {"name": "数据结构", "slots": [{"day": "周三", "start": "14:00", "end": "13:00", "weeks": [1, 2, 3]}]},
        # 没有课程名：整条丢弃
        {"name": "", "slots": [{"weekday": 2, "start": "08:00"}]},
        # weekday 是列表：一个时段展开成两天
        {"name": "大学英语", "slots": [{"weekday": [1, 3], "start_period": 3, "end_period": 4}]},
    ]})
    entries = courseimport_ai.llm_extract_entries("随便一段 OCR 文本")

    assert names(entries) == ["高等数学", "数据结构", "大学英语"]  # 空名被丢掉
    math = next(e for e in entries if e["name"] == "高等数学")
    assert math["teacher"] == "李四" and math["location"] == "教1-202"
    assert math["slots"] == [{
        "weekday": 1, "start": "08:00", "end": "09:40",
        "weeks": list(range(1, 17)), "location": "教1-202", "teacher": "李四",
    }]  # 单周 ∪ 全周 = 全周，且时段继承课程的老师/地点

    assert slots_of(entries, "数据结构") == [(3, "14:00", "14:45", [1, 2, 3])]
    assert slots_of(entries, "大学英语") == [
        (1, "10:00", "11:40", []), (3, "10:00", "11:40", []),
    ]  # 第3-4节 = 10:00-11:40，列表展开成两个时段


def test_flat_course_list_and_field_aliases(fake_llm):
    """兼容 course_list / 顶层数组 与 start_time/begin/period 一类别名。"""
    fake_llm({"course_list": [
        {"course": "线性代数", "teacher_name": "王五",
         "slots": [{"week": "周二", "begin": "08:00", "finish": "09:40"}]},
    ]})
    entries = courseimport_ai.llm_extract_entries("x")
    assert names(entries) == ["线性代数"] or names(entries) == ["线性代数"]  # 别名归一化
    assert slots_of(entries, "线性代数")[0][:3] == (2, "08:00", "09:40")


def test_weeks_beyond_range_are_clamped_or_dropped(fake_llm):
    """周次超出 1~60 不炸：坏值丢弃，好值保留。"""
    fake_llm({"courses": [{
        "name": "操作系统", "slots": [{"weekday": 4, "start": "08:00", "end": "09:40", "weeks": "1-3,99"}],
    }]})
    entries = courseimport_ai.llm_extract_entries("x")
    assert slots_of(entries, "操作系统")[0][3] == [1, 2, 3]


def test_unusable_payloads_return_none(fake_llm):
    """LLM 返回垃圾时必须 None（调用方据此给中文提示），不能返回空课表。"""
    for payload in (
        {"foo": "bar"},
        {"courses": []},
        ["not", "a", "dict"],
        {"courses": [{"name": "只有名字没有时段"}]},
        None,
    ):
        fake_llm(payload)
        assert courseimport_ai.llm_extract_entries("x") is None


def test_llm_failure_returns_none(fake_llm):
    """调用抛异常 / 未配置 Key 都返回 None，不向上抛。"""
    fake_llm(RuntimeError("network down"))
    assert courseimport_ai.llm_extract_entries("x") is None
    assert courseimport_ai.llm_extract_source("x") is None

    import app.domain.courseimport_ai as mod

    original = mod.llm_available
    try:
        mod.llm_available = lambda: False
        mod.chat_json = lambda *a, **k: pytest.fail("未配置 Key 时不该发起调用")
        assert mod.llm_extract_entries("x") is None
    finally:
        mod.llm_available = original


def test_empty_text_skips_llm(fake_llm):
    calls = fake_llm({"courses": [{"name": "x", "slots": [{"weekday": 1, "start": "08:00"}]}]})
    assert courseimport_ai.llm_extract_entries("   ") is None
    assert "system" not in calls  # 空文本不该浪费一次模型调用


def test_extract_source_shape_and_term_start(fake_llm):
    """完整结果：format=image、ai_assisted=True、学期由开学日推断。"""
    fake_llm({
        "courses": [{"name": "大学物理", "slots": [{"weekday": 5, "start": "14:00", "end": "15:40"}]}],
        "term_start_date": "2025-09-01",
    })
    out = courseimport_ai.llm_extract_source("x")
    assert out["format"] == "image" and out["ai_assisted"] is True
    assert out["term_start_date"] == "2025-09-01"
    assert out["semester"] == "2025-2026-1"
    assert names(out["entries"]) == ["大学物理"]


def test_extract_source_ignores_bad_term_start(fake_llm):
    fake_llm({
        "courses": [{"name": "大学物理", "slots": [{"weekday": 5, "start": "14:00", "end": "15:40"}]}],
        "term_start_date": "不是日期",
    })
    out = courseimport_ai.llm_extract_source("x")
    assert out["term_start_date"] is None and out["semester"] == ""


def test_ocr_text_is_truncated_in_prompt(fake_llm):
    """超长 OCR 文本要截断后再进提示词，避免 token 爆炸。"""
    calls = fake_llm({"courses": [{"name": "x", "slots": [{"weekday": 1, "start": "08:00"}]}]})
    courseimport_ai.llm_extract_entries("课" * 50000)
    assert len(calls["user"]) < 20000


def test_prompt_states_no_guessing_rule(fake_llm):
    """提示词必须明确「绝不编造」，这是兜底通道的质量前提。"""
    calls = fake_llm({"courses": [{"name": "x", "slots": [{"weekday": 1, "start": "08:00"}]}]})
    courseimport_ai.llm_extract_entries("周一 高等数学")
    assert "绝不编造" in calls["system"]
    assert "第N-M节" in calls["system"] or "第N节" in calls["system"]
