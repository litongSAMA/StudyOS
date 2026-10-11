"""中文分词与检索辅助（jieba + FTS5）。"""
from __future__ import annotations

import re

import jieba

jieba.setLogLevel(60)  # 静默构建日志

# 常用学习场景词，保证不被切碎
for _w in ["操作系统", "机器学习", "深度学习", "神经网络", "数据结构", "数据库",
           "编译原理", "计算机组成", "计算机网络", "大学物理", "高等数学",
           "线性代数", "概率论", "傅里叶", "实验报告", "课件", "考试", "作业",
           "死锁", "银行家算法", "资源分配", "进程同步", "进程调度", "调度算法",
           "时间片", "傅里叶变换", "词法分析", "语法分析", "内存管理", "文件系统",
           "反向传播", "量子计算", "微积分", "特征值", "监督学习"]:
    jieba.add_word(_w)


def seg(text: str) -> str:
    """分词并以空格连接，供 FTS 索引。"""
    if not text:
        return ""
    return " ".join(t for t in jieba.cut(text) if t and not t.isspace())


def seg_query(q: str) -> list[str]:
    """查询分词：保留所有非空白 token。"""
    return [t for t in jieba.cut(q) if t and not t.isspace()]


def clean_snippet(s: str) -> str:
    """去掉分词产生的空格，让摘录自然可读（覆盖 CJK 与中文标点相邻处）。"""
    if not s:
        return ""
    cjk = r"\u4e00-\u9fff\u3000-\u303f\uff00-\uffef"
    s = re.sub(rf"(?<=[{cjk}])\s+(?=[{cjk}])", "", s)
    return s.strip()
