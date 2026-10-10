"""数据模型：课程 / 资料 / 任务(DDL) / 复习计划 / 导出记录 / 系统设置。

所有 datetime 列统一存 **naive UTC**（见 timeutil.py），
API/快照边界再转带时区的 ISO 输出——跨设备 LWW 比较口径一致。
"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base
from app.infra.timeutil import utcnow


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    code: Mapped[str] = mapped_column(String(40), default="")
    semester: Mapped[str] = mapped_column(String(40), default="")
    start_date: Mapped[str | None] = mapped_column(String(20), nullable=True)  # 学期锚点 YYYY-MM-DD
    color: Mapped[str] = mapped_column(String(16), default="#6366f1")
    # ---- 课表（导入课表功能）：任课老师 / 上课地点 / 每周上课时段 ----
    teacher: Mapped[str] = mapped_column(String(120), default="", server_default="")
    location: Mapped[str] = mapped_column(String(200), default="", server_default="")
    slots: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    # slots: [{weekday: 1~7(周一~周日), start: "HH:MM", end: "HH:MM",
    #          weeks: [1,2,...](上课周次), location: str, teacher: str}]
    # 时间存**本地墙上时间**（课表语义，不随设备时区换算，与 naive UTC 存储无关）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # 多端同步：跨设备唯一标识 + 最后修改时间（LWW 冲突解决）
    sync_uuid: Mapped[str] = mapped_column(String(36), default="", index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300), index=True)
    type: Mapped[str] = mapped_column(String(40), default="other", index=True)
    # type: courseware(课件) / homework(作业) / lab(实验) / exam(试卷) / notes(笔记) / handout(讲义) / other
    chapter: Mapped[str] = mapped_column(String(120), default="", index=True)
    # chapter: 独立章节字段（如「第3章 内存管理」），入库时自动抽取、可手动修正，支持过滤与检索
    difficulty: Mapped[str] = mapped_column(String(20), default="", index=True)
    # difficulty: 空=未标注 / easy(简单) / medium(中等) / hard(困难)，参与复习时长加权
    exam_focus: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # exam_focus: 考试重点（老师强调的考点/必考内容），复习计划优先分配
    file_path: Mapped[str] = mapped_column(String(600), default="")
    file_name: Mapped[str] = mapped_column(String(300), default="")
    file_hash: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(20), default="text")
    # source: file / screenshot / text
    text: Mapped[str] = mapped_column(Text, default="")  # 抽取的全文，供 FTS 检索
    tags: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sync_uuid: Mapped[str] = mapped_column(String(36), default="", index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="homework", index=True)
    # kind: homework / exam / report / lab / review / other
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    remind_offsets_h: Mapped[list] = mapped_column(JSON, default=lambda: [72, 24, 2])
    # 提前 N 小时提醒，默认 3 天 / 1 天 / 2 小时
    note: Mapped[str] = mapped_column(Text, default="")
    source_text: Mapped[str] = mapped_column(Text, default="")
    source_material_id: Mapped[int | None] = mapped_column(
        ForeignKey("materials.id"), nullable=True
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sync_uuid: Mapped[str] = mapped_column(String(36), default="", index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Note(Base):
    """AI 提炼笔记：从资料全文提炼的「重点/考点笔记」。

    由 refine 引擎生成（LLM 优先、离线规则降级），可人工编辑；
    points 为 [{point, importance}] 结构化要点，importance: high/medium/low。
    """

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_id: Mapped[int | None] = mapped_column(
        ForeignKey("materials.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(300), index=True)
    mode: Mapped[str] = mapped_column(String(20), default="keypoints", index=True)
    # mode: keypoints（重点笔记）/ exam（考点笔记）
    summary: Mapped[str] = mapped_column(Text, default="")
    points: Mapped[list] = mapped_column(JSON, default=list)
    # points: [{point: str, importance: high|medium|low}]
    engine: Mapped[str] = mapped_column(String(20), default="rule")
    # engine: rule（离线规则）/ llm（AI 模型）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sync_uuid: Mapped[str] = mapped_column(String(36), default="", index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WrongQuestion(Base):
    """错题归集（错题本）：从试卷/作业/练习中收集的错题，支持分类归档、重练与掌握判定。

    重练（practice）按「连续答对 2 次 = 已掌握（done）」判定：答对 streak+1，
    答错 streak 清零并回到待攻克（open），手动 PATCH status 可随时修正；
    章节/难度入库时按标题+题干走规则引擎自动抽取（与资料归档同一套规则），可手动修正。
    """

    __tablename__ = "wrong_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300), index=True)
    # title: 题干摘要/标题（列表展示）
    question: Mapped[str] = mapped_column(Text, default="")      # 题目原文
    my_answer: Mapped[str] = mapped_column(Text, default="")     # 我的（错误）答案
    answer: Mapped[str] = mapped_column(Text, default="")        # 正确答案/解析
    reason: Mapped[str] = mapped_column(Text, default="")        # 错因分析
    source: Mapped[str] = mapped_column(String(300), default="")  # 来源（试卷/作业/练习名）
    chapter: Mapped[str] = mapped_column(String(120), default="", index=True)
    difficulty: Mapped[str] = mapped_column(String(20), default="", index=True)
    # difficulty: ""(未标注) / easy / medium / hard
    tags: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    # status: open（待攻克）/ done（已掌握）
    review_count: Mapped[int] = mapped_column(Integer, default=0)  # 累计重练次数
    wrong_count: Mapped[int] = mapped_column(Integer, default=1)   # 累计答错次数（首次归集计 1）
    streak: Mapped[int] = mapped_column(Integer, default=0)        # 连续答对次数（≥2 → done）
    last_result: Mapped[str] = mapped_column(String(10), default="")
    # last_result: "" / correct / wrong
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sync_uuid: Mapped[str] = mapped_column(String(36), default="", index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ReviewPlan(Base):
    __tablename__ = "review_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    exam_task_ids: Mapped[list] = mapped_column(JSON, default=list)
    sessions: Mapped[list] = mapped_column(JSON, default=list)
    # sessions: [{date, course, scope, material_ids[], hours, mastery,
    #             study_ids[], due_ids[](到期间隔复习), spaced_ids[](计划间隔复习)}]
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sync_uuid: Mapped[str] = mapped_column(String(36), default="", index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MaterialReview(Base):
    """单份资料的间隔重复状态（SM-2 变体，见 spacedrep.py）。

    每次评分（again/hard/good/easy）更新：掌握程度 mastery(0~1)、连续成功次数、
    遗忘次数、难度系数 ease、当前间隔与下次到期时刻。复习计划按 mastery 加权
    分配学习时长，并按 due_at 安排间隔复习。
    """

    __tablename__ = "material_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_id: Mapped[int] = mapped_column(
        ForeignKey("materials.id"), nullable=False, unique=True, index=True
    )
    mastery: Mapped[float] = mapped_column(Float, default=0.0)
    # mastery: 0~1 连续掌握程度（0 未掌握，1 完全掌握）
    repetitions: Mapped[int] = mapped_column(Integer, default=0)
    # repetitions: 连续成功复习次数（决定间隔阶梯 1/2/4/7/14/30 天）
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    # lapses: 遗忘（again）次数
    interval_days: Mapped[float] = mapped_column(Float, default=0.0)
    # interval_days: 当前复习间隔（天）
    ease: Mapped[float] = mapped_column(Float, default=2.5)
    # ease: SM-2 难度系数（下限 1.3）
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # due_at: 下次应复习时刻（间隔重复到期点，naive UTC）
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_rating: Mapped[str] = mapped_column(String(10), default="")
    # last_rating: again / hard / good / easy
    history: Mapped[list] = mapped_column(JSON, default=list)
    # history: 最近评分记录 [{at, rating, mastery}]（最多 20 条）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sync_uuid: Mapped[str] = mapped_column(String(36), default="", index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Checklist(Base):
    """通用勾选清单：标题/描述/课程/标签/截止日 + 可勾选项列表。

    items 为 [{text, done, note}]；可一键生成分享链接（checklist_shares，只读公开查看），
    或存为模版进模版库（checklist_templates）。勾选进度只影响本机。
    """

    __tablename__ = "checklists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    items: Mapped[list] = mapped_column(JSON, default=list)
    # items: [{text: str, done: bool, note: str}]
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ChecklistTemplate(Base):
    """清单模版（模版库）：内置模版（key 唯一、builtin=True，随版本升级自动更新内容）
    与用户自存模版（可编辑、可删除）。内置模版受保护，只能「使用」或另存为新模版。"""

    __tablename__ = "checklist_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str | None] = mapped_column(String(80), nullable=True, unique=True, index=True)
    # key: 内置模版稳定标识（如 final-exam-sprint），用户模版为 NULL
    title: Mapped[str] = mapped_column(String(300), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(40), default="日常学习", index=True)
    # category: 复习备考 / 英语考试 / 论文写作 / 校园生活 / 日常学习
    tags: Mapped[list] = mapped_column(JSON, default=list)
    items: Mapped[list] = mapped_column(JSON, default=list)
    # items: [{text, done, note}]（模版内 done 一律为 False）
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    source_checklist_id: Mapped[int | None] = mapped_column(
        ForeignKey("checklists.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ChecklistShare(Base):
    """清单分享链接：token 即链接凭证；可选过期（expires_at）、可撤销（revoked），
    记录查看次数（view_count）。公开只读视图由 /api/share/{token} 提供。"""

    __tablename__ = "checklist_shares"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    checklist_id: Mapped[int] = mapped_column(ForeignKey("checklists.id"), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class ExportRecord(Base):
    """导出记录：既是导出流水（可回看导了什么），也是**增量导出的基线**。

    每次导出写一行；`until_at` 是本次导出覆盖到的时刻，下一次同 (kind, format) 的
    增量导出即以它为 `since` 起点，只导出这之后有变更（updated_at/created_at 更新）的条目。
    空的增量导出不记录，避免历史里堆满「0 条」噪声，基线也不必前移。
    """

    __tablename__ = "export_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    format: Mapped[str] = mapped_column(String(12))
    kind: Mapped[str] = mapped_column(String(20), default="tasks", index=True)
    # kind: tasks（DDL 任务）/ materials（资料目录）/ review（复习清单）
    file_path: Mapped[str] = mapped_column(String(600))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # 增量导出元信息
    since_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    until_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    incremental: Mapped[bool] = mapped_column(Boolean, default=False)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PushSubscription(Base):
    """Web Push 订阅：浏览器把 PushSubscription（endpoint + p256dh/auth 公钥）交给后端，
    后端经浏览器厂商的推送服务（FCM/APNs/Mozilla）送达——**页面关闭也能收到**。

    设备本地概念：订阅端点是「浏览器 × 本机」的注册，跨设备同步不应搬运
    （另一端后端用它推送会被 VAPID audience 校验拒绝），因此不进同步快照。
    """

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(800), unique=True, index=True)
    p256dh: Mapped[str] = mapped_column(Text, default="")
    auth: Mapped[str] = mapped_column(Text, default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PushDelivery(Base):
    """推送送达去重标记：任务 × 提醒点 × 订阅 各发一次，重启不重发、不轰炸。

    提醒点（remind_offsets_h 里的 N 小时）被跨过后只发一条；新订阅创建时会把
    「已经过期的提醒点」预先标记为已送达（静默基线），避免一开推送就收到历史轰炸。
    """

    __tablename__ = "push_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    subscription_id: Mapped[int] = mapped_column(Integer, index=True)
    offset_h: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Tombstone(Base):
    """多端同步删除墓碑：实体被删除后留下 (entity, sync_uuid) → deleted_at 记录，
    供其他设备合并删除；比实体 updated_at 新的墓碑才生效（防止误删重建数据）。"""

    __tablename__ = "tombstones"

    sync_uuid: Mapped[str] = mapped_column(String(60), primary_key=True)
    entity: Mapped[str] = mapped_column(String(20), primary_key=True)
    # entity: course / material / task / review / setting
    deleted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FocusSession(Base):
    __tablename__ = "focus_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sync_uuid: Mapped[str] = mapped_column(String(36), default="", index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    mode: Mapped[str] = mapped_column(String(10), default="focus")
    planned_min: Mapped[int] = mapped_column(Integer, default=25)
    duration_min: Mapped[float] = mapped_column(Float, default=0)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    interrupt_count: Mapped[int] = mapped_column(Integer, default=0)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"), nullable=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("review_plans.id", ondelete="SET NULL"), nullable=True)
    plan_date: Mapped[str] = mapped_column(String(20), default="")
    material_ids: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Quiz(Base):
    __tablename__ = "quizzes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sync_uuid: Mapped[str] = mapped_column(String(36), default="", index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"), nullable=True)
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id", ondelete="SET NULL"), nullable=True)
    note_ids: Mapped[list] = mapped_column(JSON, default=list)
    title: Mapped[str] = mapped_column(String(300), default="自测")
    qtype: Mapped[str] = mapped_column(String(10), default="mixed")
    questions: Mapped[list] = mapped_column(JSON, default=list)
    results: Mapped[list] = mapped_column(JSON, default=list)
    engine: Mapped[str] = mapped_column(String(10), default="rule")
