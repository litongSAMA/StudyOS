"""错题 AI 提炼引擎：粘贴错题原文 → 结构化字段（供表单预填），无副作用。

输入：试卷/作业里的错题原文（题干、当时的作答、参考答案/解析可混在一起粘贴）。
输出：title / question / my_answer / answer / reason / chapter / difficulty / tags。

与笔记提炼（refine.py）同一设计原则：
- LLM 未配置或调用失败时降级为**离线规则引擎**——章节/难度/标签按词典抽取，
  标题取首句，内容字段留空由用户填写（绝不编造答案）；
- 引擎标识 engine=llm/rule 回传前端展示。
"""
from __future__ import annotations

from . import llm
from .common import (
    classify_difficulty,
    extract_chapter,
    extract_tags,
    normalize_difficulty,
)

_MAX_TEXT = 20000       # 输入截断（与笔记提炼一致）
MAX_TAGS = 20
MAX_TAG_LEN = 40

# 字段长度上限（与 WrongQuestionIn/路由层的口径对齐）
_LEN_CAP = {
    "title": 300,
    "question": 8000,
    "my_answer": 4000,
    "answer": 8000,
    "reason": 4000,
    "chapter": 120,
}


def _first_line_title(text: str) -> str:
    """规则引擎标题：取首个非空行（去序号前缀），截断到 300 字。"""
    for line in (text or "").splitlines():
        s = line.strip()
        if s:
            return s[:300]
    return "未命名错题"


def _norm_tags(raw: list | None) -> list[str]:
    out: list[str] = []
    for t in raw or []:
        t = str(t).strip()[:MAX_TAG_LEN]
        if t and t not in out:
            out.append(t)
    return out[:MAX_TAGS]


def _clean(data: dict) -> dict:
    """LLM 输出清洗：字段截断、难度/标签归一化，非字符串字段丢弃。"""
    out: dict = {}
    for field, cap in _LEN_CAP.items():
        v = data.get(field)
        out[field] = str(v).strip()[:cap] if isinstance(v, str) else ""
    out["difficulty"] = normalize_difficulty(str(data.get("difficulty", "")))
    out["tags"] = _norm_tags(data.get("tags") if isinstance(data.get("tags"), list) else [])
    return out


def refine_wrong_by_rules(text: str) -> dict:
    """离线规则提炼：只做确定性的规则抽取，内容字段留空由用户补。"""
    return {
        "title": _first_line_title(text),
        "question": text.strip()[:8000],
        "my_answer": "",
        "answer": "",
        "reason": "",
        "chapter": extract_chapter(text),
        "difficulty": classify_difficulty(text),
        "tags": extract_tags(text),
    }


def refine_wrong_by_llm(text: str, title_hint: str = "") -> dict | None:
    """LLM 结构化提炼。未配置/失败返回 None（上层降级离线规则）。"""
    if not llm.llm_available():
        return None
    system = (
        "你是学生学习助手。用户会把一道做错的题粘贴给你（可能包含题目、他当时的错误答案、"
        "参考答案与解析，全部混在一起）。请把这道错题整理成结构化字段，严格输出 JSON："
        '{"title":"一句话概括这道题","question":"题目原文（忠实原文，不要改写）",'
        '"my_answer":"用户当时的错误答案（原文没有就留空字符串）",'
        '"answer":"正确答案与解析（原文没有且你不确定时留空字符串，绝不编造）",'
        '"reason":"错因分析（一句话，可推断常见错因；不确定就留空）",'
        '"chapter":"章节（如「第3章 内存管理」，没有就留空字符串）",'
        '"difficulty":"easy|medium|hard 三选一（拿不准就 medium）",'
        '"tags":["知识点标签1","标签2"]}。'
        "规则：忠实原文、不编造原文没有的内容；tags 最多 6 个，用简短的知识点名词。"
    )
    user = f"标题提示：{title_hint or '无'}\n错题原文：\n{text[:_MAX_TEXT]}"
    data = llm.chat_json(system, user, max_tokens=2000)
    if not isinstance(data, dict):
        return None
    return data


def refine_wrong(text: str, title_hint: str = "") -> dict:
    """主入口：LLM 优先，失败/输出不完整时降级离线规则；输出永远可用。

    返回 dict 结构：{title, question, my_answer, answer, reason, chapter,
    difficulty, tags, engine}——engine 标识实际使用的引擎。
    """
    data = refine_wrong_by_llm(text, title_hint)
    if data is None:
        cleaned = refine_wrong_by_rules(text)
        cleaned["engine"] = "rule"
        return cleaned
    cleaned = _clean(data)
    # LLM 没提炼出任何实质内容（题干都为空）→ 规则兜底
    if not cleaned["question"] and not cleaned["title"]:
        fallback = refine_wrong_by_rules(text)
        fallback["engine"] = "rule"
        return fallback
    if not cleaned["title"]:
        cleaned["title"] = _first_line_title(text) or (title_hint or "").strip()[:300]
    if not cleaned["question"]:
        cleaned["question"] = text.strip()[:8000]
    cleaned["engine"] = "llm"
    return cleaned
