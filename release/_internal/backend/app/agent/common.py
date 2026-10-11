"""公共工具：课程名匹配、资料类型/标签识别词典。"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Course

# 资料类型关键词 → 类型
# 顺序即优先级：课件/讲义/笔记排在考试词**前面**——
# 「课件：第8章…（考试重点）」应归课件而非试卷（考试重点只是考点标记）
TYPE_KEYWORDS: list[tuple[str, str]] = [
    ("课件", "courseware"), ("讲义", "handout"), ("笔记", "notes"),
    ("实验", "lab"), ("报告", "report"),
    ("试卷", "exam"), ("真题", "exam"), ("期末", "exam"),
    ("期中", "exam"), ("考试", "exam"),
    ("作业", "homework"), ("习题", "homework"), ("答案", "homework"),
    ("ppt", "courseware"), ("pdf", "courseware"), ("slides", "courseware"),
    ("lecture", "courseware"),
]

# 常见主题标签词典（规则引擎用，LLM 可补充）
TAG_KEYWORDS = [
    "机器学习", "深度学习", "神经网络", "操作系统", "进程", "线程", "死锁",
    "内存管理", "文件系统", "数据库", "SQL", "事务", "网络", "TCP", "HTTP",
    "算法", "数据结构", "图论", "动态规划", "概率论", "线性代数", "微积分",
    "傅里叶", "信号", "电路", "编译原理", "分布式", "并发", "多线程",
    "前端", "后端", "Java", "Python", "C++", "英语", "四六级",
]

# 章节表达：第N章/节/讲/课、Chapter N（可带章节名，如「第3章 内存管理」；
# 章节名在括号/标点处截断——「第8章 IO系统（考试重点）」只取到「第8章 IO系统」）
CHAPTER_RE = re.compile(
    r"(?:第\s*[0-9一二三四五六七八九十百零两]+\s*(?:章|节|讲|课)"
    r"|[Cc]hapter\s*\d+(?:\.\d+)?)"
    r"(?:[·•:：\s]+[^，,。;；、！!？?\n（(【\[]{1,24})?"
)

# 难度分类词典：**先难后易**（「基础」「简单」太泛，放最后判定）
DIFFICULTY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("hard", ("难点", "较难", "很难", "偏难", "难度较大", "难度大", "难题",
              "进阶", "提高", "挑战", "hard")),
    ("medium", ("中等", "适中", "常规", "medium")),
    ("easy", ("入门", "基础", "简单", "初级", "易学", "easy")),
]

DIFFICULTIES: tuple[str, ...] = ("", "easy", "medium", "hard")

# 考试重点信号词：老师强调的考点/必考/常考内容
EXAM_FOCUS_KEYWORDS = (
    "考试重点", "考点", "必考", "常考", "高频考点", "高频", "重点", "易错", "押题",
)


def normalize_difficulty(raw: str) -> str:
    """难度归一化：只接受 ""/easy/medium/hard，其余回落未标注。"""
    v = (raw or "").strip().lower()
    return v if v in DIFFICULTIES else ""


def extract_chapter(text: str) -> str:
    """从文本/标题抽取章节号与章节名（如「第3章 内存管理」），取第一处。"""
    m = CHAPTER_RE.search(text or "")
    if not m:
        return ""
    return m.group(0).strip()[:120]


def classify_difficulty(text: str) -> str:
    """按词典判定难度；命中多条时按「先难后易」优先级取第一处。"""
    low = (text or "").lower()
    for level, kws in DIFFICULTY_RULES:
        if any(k in low for k in kws):
            return level
    return ""


def has_exam_focus(text: str) -> bool:
    """是否含考试重点信号词。"""
    low = (text or "").lower()
    return any(k in low for k in EXAM_FOCUS_KEYWORDS)


def normalize_course_name(raw: str) -> str:
    """去掉常见的课程前后缀噪声：'《操作系统》第3章' → '操作系统'。"""
    s = raw.strip()
    s = re.sub(r"^[《「\[【\"']+|[》」\]】\"']+$", "", s)
    s = re.sub(r"(第[0-9一二三四五六七八九十百]+[章节讲课时])[^，,、]*$", "", s)
    s = re.sub(r"[（(][^）)]*[）)]$", "", s)
    return s.strip()


def match_course(db: Session, text: str) -> tuple[int | None, str]:
    """在已有课程中做模糊匹配：精确 > 包含 > 关键词覆盖。"""
    courses = db.execute(select(Course)).scalars().all()
    if not courses:
        return None, ""
    norm = normalize_course_name(text)
    for c in courses:
        if norm and (c.name == norm or norm in c.name or c.name in norm):
            return c.id, c.name
    # 关键词覆盖：课程名去掉「学/原理/导论」等虚词后若出现在文本中
    for c in courses:
        core = re.sub(r"(学|原理|导论|基础|课程)$", "", c.name)
        if len(core) >= 2 and core in text:
            return c.id, c.name
    return None, ""


def classify_material_type(text: str) -> tuple[str, float]:
    for kw, typ in TYPE_KEYWORDS:
        if kw.lower() in text.lower():
            return typ, 0.9
    return "other", 0.5


def extract_tags(text: str) -> list[str]:
    tags: list[str] = []
    for kw in TAG_KEYWORDS:
        if kw.lower() in text.lower() and kw not in tags:
            tags.append(kw)
    return tags[:8]
