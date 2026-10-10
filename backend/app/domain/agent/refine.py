"""笔记提炼引擎：LLM 结构化提炼（重点/考点）+ 离线抽取式降级。

输入：资料全文（或任意文本）+ 模式（keypoints 重点 / exam 考点）。
输出：RefineResult（标题、概要、结构化要点 + 引擎标识），无副作用——
用户确认后才由 notes 接口保存为笔记。

与抽取 Agent 同一设计原则：LLM 未配置或调用失败时降级为**离线抽取式摘要**——
句子按「词频覆盖 × 位置权重 × 重点/考点信号词」打分，取高分句作为要点，
断网、无 Key 时依然可提炼；LLM 通道失败静默回落，绝不报错。
"""
from __future__ import annotations

import math
import re

from app.api.schemas import RefinePoint, RefineResult
from app.domain.semantic import _tokens
from app.domain.agent import llm
from app.domain.agent.common import EXAM_FOCUS_KEYWORDS

NOTE_MODES = ("keypoints", "exam")  # keypoints=重点笔记 / exam=考点笔记

# 重点信号词：定义/概念/原理类句子通常是重点
KEY_SIGNALS = (
    "核心", "关键", "重要", "重点", "难点", "定义", "概念", "原理", "特点",
    "特征", "结论", "总结", "公式", "定理", "定律", "注意", "作用", "意义",
    "目的", "本质", "分类", "步骤", "方法",
)
# 考点信号词：考试可能考查的内容
EXAM_SIGNALS = EXAM_FOCUS_KEYWORDS + (
    "易错", "辨析", "对比", "区别", "联系", "应用", "考察", "掌握", "理解",
    "计算", "例题", "举例",
)

_SENT_SPLIT = re.compile(r"[。！？!?；;\n]+")
_TITLE_NOISE = re.compile(r"^[\s【\[（(·•\-—:：]+|[\s】\])）·•\-—:：]+$")
_MAX_POINTS = 8
_MAX_POINT_LEN = 300


def _sentences(text: str) -> list[str]:
    """按中文标点/换行切句，滤掉过短/过长的噪声行。"""
    parts = _SENT_SPLIT.split(text or "")
    out: list[str] = []
    for p in parts:
        s = re.sub(r"\s+", " ", p).strip()
        if 6 <= len(s) <= 400:
            out.append(s)
    return out


def _sig_bonus(sent: str, mode: str) -> float:
    """重点/考点信号词加成。"""
    bonus = 0.0
    if mode == "exam":
        if any(k in sent for k in EXAM_FOCUS_KEYWORDS):
            bonus += 1.8
        if any(k in sent for k in EXAM_SIGNALS):
            bonus += 0.4
    else:
        if any(k in sent for k in KEY_SIGNALS):
            bonus += 0.7
        if any(k in sent for k in EXAM_SIGNALS):
            bonus += 0.3
    return bonus


def refine_by_rules(text: str, mode: str, title_hint: str = "") -> dict:
    """离线抽取式提炼：词频 + 位置 + 信号词打分，高分句即要点。"""
    sents = _sentences(text)
    if not sents:
        return {"title": "", "summary": "", "points": []}

    # 全文词频：jieba 词 + 二元组 + 单字（与语义检索同一套分词）
    freq: dict[str, float] = {}
    for s in sents:
        for tok in _tokens(s):
            freq[tok] = freq.get(tok, 0.0) + 1.0
    # 词频做 IDF 式压制：出现 1 次的词信息量低
    n = max(len(sents), 1)
    idf = {tok: math.log((n + 1) / (1 + c)) + 1.0 for tok, c in freq.items()}

    scored: list[tuple[int, float, float]] = []  # (index, score, signal)
    for i, s in enumerate(sents):
        score = sum(w * idf.get(tok, 1.0) for tok, w in _tokens(s).items())
        if i == 0:                     # 首句通常是主题句
            score += 1.2
        elif i == len(sents) - 1:      # 末句常是总结
            score += 0.5
        sig = _sig_bonus(s, mode)
        score += sig
        scored.append((i, score, sig))

    # 考点模式：含考点信号词的句子优先入选（保证「必考/常考」句不丢）
    def rank_key(item):
        i, score, sig = item
        return (sig > 0 if mode == "exam" else 0, score)

    top = sorted(scored, key=rank_key, reverse=True)[:_MAX_POINTS]
    top = sorted(top, key=lambda x: x[0])  # 按原文顺序展示

    high_bar = sorted((s for _, s, _ in scored), reverse=True)[: max(1, len(scored) // 3)][-1]
    top_idx = [i for i, _, _ in top]
    points = []
    for i in top_idx:
        s = sents[i]
        _, score, sig = scored[i]
        if sig > 0 or score >= high_bar:
            imp = "high"
        elif score >= high_bar * 0.7:
            imp = "medium"
        else:
            imp = "low"
        points.append({"point": s[:_MAX_POINT_LEN], "importance": imp})

    summary_sents = [sents[i] for i in top_idx[:3]]
    summary = "。".join(summary_sents)[:500]
    title = (title_hint or "").strip()[:40] or (sents[0][:40])
    return {"title": title, "summary": summary, "points": points}


def refine_by_llm(text: str, mode: str, title_hint: str = "") -> dict | None:
    """LLM 结构化提炼。未配置/失败返回 None（上层降级离线规则）。"""
    if not llm.llm_available():
        return None
    if mode == "exam":
        goal = (
            "把资料文本提炼成**考点笔记**：考试可能考查的内容——必考/常考/高频考点、"
            "易错易混淆点、常见考法。"
        )
    else:
        goal = (
            "把资料文本提炼成**重点笔记**：核心概念、定义、原理、公式、结论与方法，"
            "去掉铺垫与口水话。"
        )
    system = (
        "你是学生学习助手。" + goal + "严格输出 JSON："
        '{"title":"简洁标题","summary":"2-3句话总览",'
        '"points":[{"point":"一条要点（具体、可直接复习，公式/定理/定义原样保留）",'
        '"importance":"high|medium|low"}]}。'
        "规则：忠实原文、不编造原文没有的内容；要点 4-10 条按重要程度排序，"
        "importance=high 的是最可能考/最核心的内容；原文太短时要点可以少于 4 条。"
    )
    user = f"资料标题：{title_hint or '无'}\n资料全文：\n{text[:8000]}"
    data = llm.chat_json(system, user, max_tokens=2000)
    if not isinstance(data, dict):
        return None
    return data


def _clean_points(raw: list) -> list[dict]:
    """LLM 输出清洗：只保留合法要点，importance 归一化。"""
    out: list[dict] = []
    for p in raw or []:
        if not isinstance(p, dict):
            continue
        text = str(p.get("point", "")).strip()
        if not text:
            continue
        imp = str(p.get("importance", "medium")).strip().lower()
        if imp not in ("high", "medium", "low"):
            imp = "medium"
        out.append({"point": text[:_MAX_POINT_LEN], "importance": imp})
    return out[: _MAX_POINTS + 4]


def refine(text: str, mode: str = "keypoints", title_hint: str = "") -> RefineResult:
    """主入口：LLM 优先，失败降级离线规则；输出永远可用。"""
    mode = mode if mode in NOTE_MODES else "keypoints"
    data = refine_by_llm(text, mode, title_hint)
    engine = "llm"
    if not data or not isinstance(data.get("points"), list) or not data["points"]:
        data = refine_by_rules(text, mode, title_hint)
        engine = "rule"
    points = _clean_points(data.get("points"))
    if not points and engine == "llm":  # LLM 输出全部非法 → 规则兜底
        data = refine_by_rules(text, mode, title_hint)
        engine = "rule"
        points = _clean_points(data.get("points"))
    summary = str(data.get("summary", "") or "").strip()[:600]
    title = str(data.get("title", "") or "").strip()[:120]
    if not title:
        title = title_hint.strip()[:40] or (points[0]["point"][:40] if points else "提炼笔记")
    return RefineResult(
        title=title,
        summary=summary,
        points=[RefinePoint(**p) for p in points],
        mode=mode,
        engine=engine,
    )
