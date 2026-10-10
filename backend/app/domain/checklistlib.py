"""清单模版库：内置学生常用清单模版 + 分类体系。

内置模版按 `key` 幂等播种/升级（每次启动 upsert，新增模版与内容修订随版本
自动进入老用户的模版库）；用户自存模版保存在数据库（key 为 NULL），
可编辑可删除。新增内置模版 = 向 BUILTIN_TEMPLATES 追加一项即可。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

# 模版分类（固定枚举：前端按此展示分类页签，用户模版也必须落在其中）
TEMPLATE_CATEGORIES: list[str] = [
    "复习备考",
    "英语考试",
    "论文写作",
    "校园生活",
    "日常学习",
]


def _items(*texts: str) -> list[dict]:
    """快捷构造清单项：全部未勾选、无备注。"""
    return [{"text": t, "done": False, "note": ""} for t in texts]


BUILTIN_TEMPLATES: list[dict] = [
    {
        "key": "final-exam-sprint",
        "title": "期末复习清单",
        "description": "考前 2 周冲刺节奏：盘点 → 排期 → 吃透重点 → 真题演练，把时间花在分值最高的地方。",
        "category": "复习备考",
        "tags": ["期末", "冲刺", "复习"],
        "items": _items(
            "列出所有考试科目与考试时间，按先后排序",
            "盘点每门课的资料、笔记与复习时长缺口",
            "用「复习计划」按 DDL 倒排生成每日复习块",
            "先吃透考试重点章节与考点笔记（★标记）",
            "每天先完成到期的间隔复习，再学新内容",
            "每门课完整做一套往年真题并计时",
            "错题与易混淆点整理成「考前速览页」",
            "考前 1 天只看速览页与公式/定义卡",
            "确认文具、证件、考场位置与闹钟",
        ),
    },
    {
        "key": "exam-week-prep",
        "title": "考前冲刺清单",
        "description": "考试周前一天一个科目的最后检查动作，防止临场缺东少西。",
        "category": "复习备考",
        "tags": ["考试周", "检查"],
        "items": _items(
            "确认考试时间表与教室（截图存档）",
            "每科准备一张 A4 速览页（公式/定义/易错点）",
            "提前打印/整理开卷允许携带的资料",
            "把证件、学生证、文具收进考试袋",
            "考前夜 23:30 前睡觉，定两个闹钟",
            "提前 30 分钟到考场，路上看速览页",
            "考后立刻记录「下次要注意的点」",
        ),
    },
    {
        "key": "kaoyan-monthly",
        "title": "考研月度复习清单",
        "description": "考研备考的月度例行框架：节奏检查 + 四科推进 + 模考复盘。",
        "category": "复习备考",
        "tags": ["考研", "月度", "规划"],
        "items": _items(
            "回顾上月进度，标注薄弱科目",
            "制定本月各科目标与每日时间分配",
            "数学：按章节刷题，错题本每周清零",
            "英语：每天背单词 + 每周两篇精读",
            "政治：每天 1 小时，后期加刷选择题",
            "专业课：以真题为纲整理考点笔记",
            "月末做一次全真模考并复盘时间分配",
            "调整作息，保证每天 7 小时睡眠",
        ),
    },
    {
        "key": "cet30",
        "title": "四六级备考清单",
        "description": "四六级最后 30 天：单词保温 + 听力磨耳 + 真题套卷 + 作文模版。",
        "category": "英语考试",
        "tags": ["四六级", "英语", "30天"],
        "items": _items(
            "高频核心词过一轮（每天 50 个）",
            "每天 20 分钟听力真题磨耳朵",
            "每周 2 套真题套卷（计时）",
            "整理作文万能句与三段式模版",
            "摘抄翻译常用表达（文化/教育/科技类）",
            "阅读错题归类：词汇/定位/长难句",
            "考前一周每天模考 1 次听力+阅读",
            "考前一天检查耳机电池与准考证",
        ),
    },
    {
        "key": "english-final",
        "title": "英语期末备考清单",
        "description": "英语期末：课文 + 语法 + 题型专项，严格按老师给的范围复习。",
        "category": "英语考试",
        "tags": ["英语", "期末"],
        "items": _items(
            "确认考试范围、题型与分值比例",
            "过一遍课文重点词汇与短语",
            "整理语法易错点清单",
            "听力材料精听 2 遍",
            "按题型做专项练习（阅读/完形/翻译）",
            "准备 3 篇作文骨架（通用开头结尾）",
            "错题重做一遍",
        ),
    },
    {
        "key": "thesis",
        "title": "论文写作任务清单",
        "description": "毕业论文从开题到答辩的关键节点 checklist，按时间线逐项打勾。",
        "category": "论文写作",
        "tags": ["毕业论文", "流程"],
        "items": _items(
            "确定选题并与导师确认方向",
            "完成开题报告（背景/意义/方法）",
            "文献综述：精读 20+ 篇核心文献并做笔记",
            "搭建论文大纲与章节结构",
            "完成实验/调研与数据整理",
            "完成初稿（先求完整再求完美）",
            "按格式要求排版：封面/目录/参考文献",
            "查重与降重，重复率达标",
            "准备答辩 PPT 与自述稿",
            "提交终稿与归档材料",
        ),
    },
    {
        "key": "course-paper",
        "title": "课程论文/报告写作清单",
        "description": "课程论文、综述与调研报告写作通用流程，适用于大部分课程作业。",
        "category": "论文写作",
        "tags": ["课程论文", "报告"],
        "items": _items(
            "确认题目要求、字数与截止日期",
            "收集资料并提炼要点笔记",
            "列大纲：问题 → 分析 → 结论",
            "写初稿（引用标出处）",
            "图表编号与标题统一",
            "检查格式：字体/行距/参考文献规范",
            "查重并降重",
            "命名规范后按时提交（提前 1 天）",
        ),
    },
    {
        "key": "lab-report",
        "title": "实验报告撰写清单",
        "description": "实验报告从原始记录到提交的标准动作，理工科通用。",
        "category": "论文写作",
        "tags": ["实验", "报告"],
        "items": _items(
            "整理实验原始数据与截图",
            "明确实验目的与原理",
            "记录实验步骤与仪器参数",
            "处理数据：表格/图表/误差分析",
            "结果分析并回答思考题",
            "结论与讨论（与理论对比）",
            "检查格式与页码，按时提交",
        ),
    },
    {
        "key": "new-semester",
        "title": "开学准备清单",
        "description": "开学第一周琐事一次清空：选课、教材、环境与节奏。",
        "category": "校园生活",
        "tags": ["开学", "准备"],
        "items": _items(
            "选课/补退选，核对课表与学分",
            "领取或购买教材，加入课程群",
            "整理宿舍与学习桌面",
            "把课表导入日历并设置提醒",
            "为每门课建 StudyOS 课程档案",
            "确认奖学金/助学金等申报时间",
            "制定本学期目标（成绩/GPA/技能）",
            "恢复作息，开学第一周不熬夜",
        ),
    },
    {
        "key": "scholarship",
        "title": "奖学金申请材料清单",
        "description": "奖学金/评优申报材料 checklist，避免临期缺件。",
        "category": "校园生活",
        "tags": ["奖学金", "材料"],
        "items": _items(
            "确认申报条件与截止时间",
            "填写申请表并请辅导员审核",
            "成绩单打印/下载",
            "获奖证书扫描件整理",
            "个人陈述/申请理由撰写",
            "推荐信（如需要）联系老师",
            "佐证材料按清单顺序打包",
            "提交前逐项核对材料清单",
        ),
    },
    {
        "key": "daily-study",
        "title": "每日高效学习清单",
        "description": "一天的学习节奏模板：计划 → 专注 → 复习 → 复盘，可设为每日例行。",
        "category": "日常学习",
        "tags": ["每日", "高效", "习惯"],
        "items": _items(
            "早上写下今天 3 件最重要的事",
            "用番茄钟完成 2 小时专注学习",
            "中午整理上午的疑问点",
            "下午完成 2 小时新内容学习",
            "傍晚 30 分钟运动或散步",
            "晚上用间隔重复复习到期内容",
            "睡前 5 分钟复盘并写明日计划",
        ),
    },
    {
        "key": "weekly-plan",
        "title": "每周学习计划",
        "description": "每周例行：回顾上周 → 定本周目标 → 分天排块，周日复盘闭环。",
        "category": "日常学习",
        "tags": ["每周", "计划", "复盘"],
        "items": _items(
            "回顾上周完成情况与未完成事项",
            "确定本周 3 个最重要的学习目标",
            "列出本周全部 DDL 与作业节点",
            "按课程把每天的学习时间块排好",
            "预留间隔复习与错题复盘时间",
            "中途检查进度，动态调整计划",
            "周日复盘并生成下周计划",
        ),
    },
    {
        "key": "final-daily",
        "title": "期末周每日作息清单",
        "description": "期末周每天的时间块安排，保证状态稳定不崩。",
        "category": "日常学习",
        "tags": ["期末周", "作息"],
        "items": _items(
            "7:30 起床，列今日复习任务",
            "上午 3 小时主攻最难科目",
            "午休 30 分钟",
            "下午 3 小时第二优先科目",
            "傍晚运动 20 分钟换脑子",
            "晚上 2 小时刷题与错题复盘",
            "23:00 前睡觉，保证 7.5 小时睡眠",
        ),
    },
]


def seed_builtin_templates(db: Session) -> int:
    """内置模版幂等播种/升级：按 key upsert——新增模版补插、已有内置模版
    同步最新内容（老用户升级后也能看到扩增后的模版库），返回内置模版总数。"""
    from app.infra.models import ChecklistTemplate

    existing = {
        t.key: t
        for t in db.execute(
            select(ChecklistTemplate).where(ChecklistTemplate.builtin.is_(True))
        ).scalars().all()
    }
    changed = False
    for spec in BUILTIN_TEMPLATES:
        t = existing.get(spec["key"])
        if t is None:
            db.add(ChecklistTemplate(
                key=spec["key"],
                title=spec["title"],
                description=spec["description"],
                category=spec["category"],
                tags=list(spec["tags"]),
                items=[dict(i) for i in spec["items"]],
                builtin=True,
            ))
            changed = True
        else:
            for field in ("title", "description", "category"):
                if getattr(t, field) != spec[field]:
                    setattr(t, field, spec[field])
                    changed = True
            if list(t.tags or []) != list(spec["tags"]):
                t.tags = list(spec["tags"])
                changed = True
            if [dict(i) for i in (t.items or [])] != [dict(i) for i in spec["items"]]:
                t.items = [dict(i) for i in spec["items"]]
                changed = True
    if changed:
        db.commit()
    return len(BUILTIN_TEMPLATES)
