import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  BellRing,
  BookX,
  BrainCircuit,
  CheckCircle2,
  ClipboardList,
  GraduationCap,
  ListChecks,
  NotebookPen,
  Rocket,
  Search,
  Settings,
  Sparkles,
  TrendingUp,
  X,
} from "lucide-react";

/** 本地存储键：是否已完成新手引导（每个浏览器一次） */
export const ONBOARDING_DONE_KEY = "studyos.onboarding.done";

interface OnboardingPoint {
  title: string;
  desc: string;
}

/** 最后一步的「三步上手」行动项：点击跳转到对应页面 */
interface QuickStartItem {
  icon: typeof Sparkles;
  title: string;
  desc: string;
  to: string;
}

interface OnboardingStep {
  icon: typeof Sparkles;
  iconCls: string;
  title: string;
  subtitle: string;
  points: OnboardingPoint[];
  /** 该步对应的功能页路由：有则显示「去体验」按钮（点击跳转并结束引导） */
  to?: string;
  /** 「去体验」按钮文案 */
  action?: string;
  /** 快速上手行动项（最后一步） */
  quickStart?: QuickStartItem[];
  /** 最后一步主按钮文案 */
  cta?: string;
}

const STEPS: OnboardingStep[] = [
  {
    icon: GraduationCap,
    iconCls: "bg-brand-100 text-brand-700",
    title: "欢迎使用 StudyOS",
    subtitle: "面向学生的个人学习管理智能体，把「资料 · 任务 · 复习」缝合成一个闭环。",
    points: [
      { title: "一次入库，全链路打通", desc: "粘贴文本 / 拖文件 / 传截图，自动抽 DDL 与标签，检索、提醒、复习随之就绪。" },
      { title: "零注册、本地优先", desc: "数据存在你自己的设备上，不上任何云，随时导出带走。" },
      { title: "像发微信一样管学习", desc: "聊天式录入，低置信的识别结果都会标黄请你确认，绝不静默写错。" },
      { title: "全程约 2 分钟", desc: "看完或跳过都行，左下角「?」可随时重新查看本引导。" },
    ],
  },
  {
    icon: Sparkles,
    iconCls: "bg-violet-100 text-violet-700",
    title: "智能入库",
    subtitle: "在「智能入库」页，把老师通知、课件文件或截图丢进来即可。",
    points: [
      { title: "文本 / 文件 / 截图三通道", desc: "支持 PDF、PPT、Word、TXT 与图片截图（截图先 OCR 再提取）。" },
      { title: "自动拆 DDL + 标签", desc: "一句「下周五 23:59 截止」也能解析成具体时间并自动挂提醒。" },
      { title: "中文时间表达全覆盖", desc: "支持「下周五」「11.30」「第12周周三」「三天后」「晚上10点」等。" },
    ],
    to: "/ingest",
    action: "去入库",
  },
  {
    icon: Search,
    iconCls: "bg-sky-100 text-sky-700",
    title: "资料库与检索",
    subtitle: "资料自动打标签归档，想找的时候一句话就能搜到。",
    points: [
      { title: "结构化标签", desc: "课程 · 类型 · 章节 · 难度 · 考试重点，自动打标、人工可改。" },
      { title: "全文 + 语义 + 混合检索", desc: "关键词高亮命中，还能按「讲死锁的那页 PPT」语义召回。" },
      { title: "组合过滤不丢结果", desc: "多标签、章节、难度、考点过滤全在数据库内完成，大资料库也精准。" },
    ],
    to: "/materials",
    action: "去资料库",
  },
  {
    icon: NotebookPen,
    iconCls: "bg-fuchsia-100 text-fuchsia-700",
    title: "AI 笔记提炼",
    subtitle: "把资料全文一键提炼成可直接复习的笔记。",
    points: [
      { title: "重点 / 考点两种模式", desc: "重点笔记抓核心概念与结论；考点笔记抓必考/常考/易错点。" },
      { title: "双引擎 + 离线兜底", desc: "LLM 结构化提炼，未配置或失败时自动降级离线抽取式摘要。" },
      { title: "可编辑可导出", desc: "笔记可改、可挂资料、可导出 Markdown，随多端同步。" },
    ],
    to: "/notes",
    action: "去提炼",
  },
  {
    icon: BrainCircuit,
    iconCls: "bg-violet-100 text-violet-700",
    title: "AI 自测 / 抽背",
    subtitle: "学没学会？基于笔记自动出题，当场考考自己。",
    points: [
      { title: "一键出题", desc: "按课程 / 资料 / 笔记 / 自由文本，自动生成选择题与问答题。" },
      { title: "选择题自动判分", desc: "即时反馈 + 解析；问答题先回忆再看参考答案自评。" },
      { title: "离线也能出题", desc: "未配置 AI 时规则引擎兜底（挖空题 + 定义题 + 回忆题），演示不崩。" },
    ],
    to: "/quiz",
    action: "去自测",
  },
  {
    icon: BellRing,
    iconCls: "bg-red-100 text-red-700",
    title: "DDL 提醒与考试倒计时",
    subtitle: "截止日期再也不会被课程群消息刷走。",
    points: [
      { title: "提前 3 天 / 1 天 / 2 小时", desc: "默认三档提醒，可在任务里自定义。" },
      { title: "Web Push 真推送", desc: "浏览器推送服务 → Service Worker，页面关闭也能收到；侧边栏铃铛看 24h 内到期。" },
      { title: "iCal 兜底 + 考试倒计时", desc: "导出 .ics 同步手机/电脑系统日历；「考试倒计时」页看时间轴。" },
    ],
    to: "/tasks",
    action: "去任务",
  },
  {
    icon: ListChecks,
    iconCls: "bg-emerald-100 text-emerald-700",
    title: "复习计划与间隔重复",
    subtitle: "期末多门考试挤在一起？交给 StudyOS 排优先级。",
    points: [
      { title: "选考试，自动生成每日复习块", desc: "按 DDL 倒排 + 资料量 × 难度权重 × 考试重点加权。" },
      { title: "掌握度加权", desc: "未掌握的资料多分配时间，已掌握只维持间隔复习，考点困难排前。" },
      { title: "间隔重复（SM-2）", desc: "四档评分，间隔 1→2→4→7→14→30 天阶梯，遗忘自动回退。" },
    ],
    to: "/review",
    action: "去复习",
  },
  {
    icon: BookX,
    iconCls: "bg-rose-100 text-rose-700",
    title: "错题本",
    subtitle: "做错的题散在试卷作业里？归集起来重练到掌握。",
    points: [
      { title: "归集错题", desc: "题目/我的答案/正确答案/错因 + 课程/章节/难度/标签/来源。" },
      { title: "章节难度自动抽取", desc: "与资料归档同一套规则引擎，可手动修正。" },
      { title: "重练到掌握", desc: "连续答对 2 次自动「已掌握」，答错清零回退「待攻克」。" },
    ],
    to: "/wrongbook",
    action: "去错题本",
  },
  {
    icon: ClipboardList,
    iconCls: "bg-cyan-100 text-cyan-700",
    title: "清单 · 模版库 · 分享",
    subtitle: "期末冲刺、论文写作、开学准备，都能沉淀成清单并分享给同学。",
    points: [
      { title: "通用勾选清单", desc: "标题/课程/标签/截止日 + 可勾选项，进度环展示。" },
      { title: "模版库", desc: "13 个内置学生模版 + 把清单存为模版，复制即用。" },
      { title: "分享链接", desc: "只读链接发给同学，可过期/撤销；本机用户可一键导入。" },
    ],
    to: "/checklists",
    action: "去清单",
  },
  {
    icon: TrendingUp,
    iconCls: "bg-indigo-100 text-indigo-700",
    title: "进度预测 · 负载预警",
    subtitle: "不止「每天学什么」，还回答「这样学来得及吗」。",
    points: [
      { title: "课程进度预测", desc: "剩余学时 ÷ 剩余天数 vs 每日容量，进度良好 / 偏紧 / 预警。" },
      { title: "顺序仿真", desc: "逐日推演预计完成日期与考试日掌握度，输出掌握度曲线。" },
      { title: "负载告警", desc: "未来 7/14/21/30 天逐日负载，识别连续高负载与考试撞车。" },
    ],
    to: "/forecast",
    action: "去预测",
  },
  {
    icon: Rocket,
    iconCls: "bg-amber-100 text-amber-700",
    title: "数据可带走 · 同步 · AI 可选",
    subtitle: "本地存储不锁数据，AI 能力按需开启，离线也能完整演示。",
    points: [
      { title: "多格式导出", desc: "iCal / CSV / Markdown，支持增量导出 + 完整备份 zip 迁移新设备。" },
      { title: "多端同步", desc: "两台设备点对点直连，令牌配对，数据只在你自己的设备之间流动。" },
      { title: "AI 模型接入", desc: "设置页可视化配置 DeepSeek / OpenAI / 硅基流动 / 本地 Ollama；未配置自动降级离线规则引擎。" },
    ],
    quickStart: [
      { icon: Settings, title: "添加课程与开学日期", desc: "启用「第12周周三」这类时间解析", to: "/settings" },
      { icon: Sparkles, title: "丢进第一条消息或文件", desc: "自动提取 DDL 和标签", to: "/ingest" },
      { icon: BellRing, title: "开启 Web Push 提醒", desc: "页面关闭也能收到 DDL 提醒", to: "/settings" },
    ],
    cta: "开始使用",
  },
];

export default function Onboarding({
  open,
  onFinish,
}: {
  open: boolean;
  onFinish: () => void;
}) {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [leaving, setLeaving] = useState(false);

  const finish = useCallback(() => {
    setLeaving(true);
    // 给退出动画一点时间，再回调（回调里做持久化 + 关闭）
    setTimeout(onFinish, 180);
  }, [onFinish]);

  const skip = useCallback(() => {
    finish();
  }, [finish]);

  /** 跳转到功能页并结束引导（标记完成，避免刷新后再次自动弹出） */
  const goTo = useCallback(
    (to: string) => {
      navigate(to);
      finish();
    },
    [navigate, finish],
  );

  useEffect(() => {
    if (!open) {
      setStep(0);
      setLeaving(false);
      return;
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") skip();
      else if (e.key === "ArrowRight" && step < STEPS.length - 1) setStep((s) => s + 1);
      else if (e.key === "ArrowLeft" && step > 0) setStep((s) => s - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, step, skip]);

  if (!open) return null;

  const s = STEPS[step];
  const last = step === STEPS.length - 1;
  const Icon = s.icon;

  return (
    <div
      className={`mobile-onboarding-layer fixed inset-0 z-[60] flex items-center justify-center bg-slate-900/60 p-4 backdrop-blur-sm transition-opacity duration-200 ${
        leaving ? "opacity-0" : "opacity-100"
      }`}
      onClick={skip}
    >
      <div
        className={`mobile-onboarding flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-3xl bg-white shadow-2xl transition-transform duration-200 ${
          leaving ? "scale-95" : "scale-100"
        }`}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 顶部渐变头 */}
        <div className="relative bg-gradient-to-r from-brand-600 to-violet-600 px-7 py-6 text-white">
          <button
            onClick={skip}
            className="absolute right-4 top-4 rounded-lg p-1.5 text-white/70 transition hover:bg-white/15 hover:text-white"
            aria-label="跳过引导"
          >
            <X size={18} />
          </button>
          <div className="text-[11px] font-medium uppercase tracking-widest text-white/70">
            StudyOS · 新手引导
          </div>
          <div className="mt-1 text-lg font-bold">
            {step + 1} / {STEPS.length} · {s.title}
          </div>
          {/* 进度条 */}
          <div className="mt-3 h-1 w-full overflow-hidden rounded-full bg-white/20">
            <div
              className="h-full rounded-full bg-white transition-all duration-300"
              style={{ width: `${((step + 1) / STEPS.length) * 100}%` }}
            />
          </div>
        </div>

        {/* 正文 */}
        <div className="flex-1 overflow-y-auto px-7 py-6">
          <div className={`inline-flex rounded-2xl p-3.5 ${s.iconCls}`}>
            <Icon size={26} />
          </div>
          <h2 className="mt-4 text-xl font-bold text-slate-800">{s.title}</h2>
          <p className="mt-1.5 text-sm leading-relaxed text-slate-500">{s.subtitle}</p>

          <ul className="mt-5 space-y-3">
            {s.points.map((p) => (
              <li
                key={p.title}
                className="flex gap-3 rounded-2xl border border-slate-100 bg-slate-50/60 px-4 py-3"
              >
                <CheckCircle2 size={18} className="mt-0.5 shrink-0 text-emerald-500" />
                <div>
                  <div className="text-sm font-semibold text-slate-700">{p.title}</div>
                  <div className="mt-0.5 text-xs leading-relaxed text-slate-500">{p.desc}</div>
                </div>
              </li>
            ))}
          </ul>

          {/* 去体验：跳转到对应功能页 */}
          {s.to && (
            <button
              onClick={() => goTo(s.to!)}
              className="mt-4 flex items-center gap-1.5 rounded-xl border border-brand-100 bg-brand-50/60 px-3.5 py-2 text-sm font-medium text-brand-700 transition hover:bg-brand-100"
            >
              {s.action ?? "去体验"} <ArrowRight size={15} />
            </button>
          )}

          {/* 最后一步：三步上手 */}
          {s.quickStart && (
            <div className="mt-5 rounded-2xl border border-brand-100 bg-brand-50/50 p-4">
              <div className="text-[11px] font-semibold uppercase tracking-wide text-brand-700">
                三步上手
              </div>
              <div className="mt-2 space-y-2">
                {s.quickStart.map((q, i) => {
                  const QIcon = q.icon;
                  return (
                    <button
                      key={q.title}
                      onClick={() => goTo(q.to)}
                      className="flex w-full items-center gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-left transition hover:border-brand-300 hover:shadow-sm"
                    >
                      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-600 text-[11px] font-bold text-white">
                        {i + 1}
                      </span>
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
                        <QIcon size={16} />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium text-slate-700">{q.title}</span>
                        <span className="block text-xs text-slate-400">{q.desc}</span>
                      </span>
                      <ArrowRight size={15} className="shrink-0 text-slate-300" />
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* 底部控制条 */}
        <div className="flex items-center justify-between border-t border-slate-100 bg-white px-7 py-4">
          <button
            onClick={skip}
            className="rounded-xl px-3 py-2 text-sm text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
          >
            跳过
          </button>

          <div className="flex items-center gap-1.5">
            {STEPS.map((_, i) => (
              <button
                key={i}
                onClick={() => setStep(i)}
                aria-label={`第 ${i + 1} 步`}
                className={`h-2 rounded-full transition-all ${
                  i === step ? "w-6 bg-brand-600" : "w-2 bg-slate-200 hover:bg-slate-300"
                }`}
              />
            ))}
          </div>

          <div className="flex items-center gap-2">
            {step > 0 && (
              <button
                onClick={() => setStep((s) => s - 1)}
                className="flex items-center gap-1 rounded-xl border border-slate-200 px-3.5 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-50"
              >
                <ArrowLeft size={15} /> 上一步
              </button>
            )}
            {last ? (
              <button
                onClick={finish}
                className="flex items-center gap-1.5 rounded-xl bg-brand-600 px-5 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-brand-700"
              >
                {s.cta ?? "开始使用"} <Rocket size={15} />
              </button>
            ) : (
              <button
                onClick={() => setStep((s) => s + 1)}
                className="flex items-center gap-1.5 rounded-xl bg-brand-600 px-5 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-brand-700"
              >
                下一步 <ArrowRight size={15} />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
