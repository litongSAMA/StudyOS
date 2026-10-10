import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  BookOpen,
  BookX,
  BrainCircuit,
  CalendarClock,
  CircleAlert,
  Sparkles,
  Timer,
  TimerReset,
  TrendingUp,
} from "lucide-react";
import { apiAgent, apiCourses, apiForecast, apiMisc } from "../api";
import { useApp } from "../App";
import type { Stats, ExtractResult, Forecast } from "../types";
import { FORECAST_STATUS, KIND_COLORS, TASK_KINDS } from "../types";
import { countdown, dueLabel, fmtDT } from "../utils";
import { Modal, Spinner } from "../components/ui";
import { useAgentRequest } from "../components/useAgentRequest";
import ConfirmPanel from "../components/ConfirmPanel";

export default function Dashboard() {
  const { refreshKey } = useApp();
  const [stats, setStats] = useState<Stats | null>(null);
  const [courses, setCourses] = useState<string[]>([]);
  const [quickText, setQuickText] = useState("");
  const [extract, setExtract] = useState<ExtractResult | null>(null);
  const { busy: extracting, error: extractError, run, cancel } = useAgentRequest();
  const [applying, setApplying] = useState(false);
  const [forecast, setForecast] = useState<Forecast | null>(null);

  useEffect(() => {
    apiMisc.stats().then(setStats).catch(console.error);
    apiCourses.list().then((cs) => setCourses(cs.map((c) => c.name))).catch(console.error);
    apiForecast.get().then(setForecast).catch(console.error);
  }, [refreshKey]);

  const quickExtract = () => {
    if (!quickText.trim() || extracting || extract) return;
    void run((signal) => apiAgent.extract(quickText, undefined, signal), setExtract);
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-slate-800">早上好 👋</h1>
        <p className="mt-1 text-sm text-slate-500">
          资料入库 · DDL 提醒 · 复习计划，一个智能体全搞定
        </p>
      </div>

      {/* 快捷入库：聊天式 */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
          <Sparkles size={16} className="text-brand-500" />
          把消息粘贴进来，自动提取 DDL 和标签
        </div>
        <div className="flex gap-2">
          <textarea
            disabled={extracting || extract != null}
            rows={2}
            className="flex-1 resize-none rounded-xl border border-slate-200 px-3.5 py-2.5 text-sm outline-none transition focus:border-brand-500"
            placeholder='例：「操作系统第三次作业，实现银行家算法，下周五 23:59 截止，提交实验报告」或直接拖文件到入库页'
            value={quickText}
            onChange={(e) => setQuickText(e.target.value)}
            onKeyDown={(e) => {
              if (!e.nativeEvent.isComposing && e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); quickExtract(); }
            }}
          />
          <button
            onClick={quickExtract}
            disabled={extracting || extract != null || !quickText.trim()}
            className="flex items-center gap-1.5 rounded-xl bg-brand-600 px-5 text-sm font-medium text-white transition hover:bg-brand-700 disabled:opacity-50"
          >
            {extracting ? <Spinner /> : <Sparkles size={16} />}
            识别
          </button>
        </div>
        {extractError && <div role="alert" className="mt-2 text-xs text-red-600">{extractError}</div>}
        {extracting && <button onClick={cancel} className="mt-2 text-xs text-brand-600 underline">取消解析</button>}
        <div className="mt-1.5 text-[11px] text-slate-400">
          支持「下周五」「11.30」「第12周周三」「三天后」「晚上10点」等中文时间表达
          <Link to="/ingest" className="ml-2 text-brand-600 hover:underline">
            文件/截图入库 →
          </Link>
        </div>
      </div>

      {/* 统计卡片 */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {[
          { label: "资料总数", value: stats?.materials ?? "–", icon: BookOpen, cls: "text-blue-600 bg-blue-50" },
          { label: "进行中 DDL", value: stats?.tasks_open ?? "–", icon: CalendarClock, cls: "text-brand-600 bg-brand-50" },
          { label: "7 天内到期", value: stats?.tasks_due_7d ?? "–", icon: CalendarClock, cls: "text-amber-600 bg-amber-50" },
          { label: "已逾期", value: stats?.tasks_overdue ?? "–", icon: CircleAlert, cls: "text-red-600 bg-red-50" },
        ].map(({ label, value, icon: Icon, cls }) => (
          <div key={label} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className={`mb-2 inline-flex rounded-lg p-2 ${cls}`}>
              <Icon size={16} />
            </div>
            <div className="text-2xl font-bold text-slate-800">{value}</div>
            <div className="text-xs text-slate-400">{label}</div>
          </div>
        ))}
      </div>

      {/* 进度预测 / 负载预警入口 */}
      {(() => {
        const status = forecast?.overall.status ?? "idle";
        const st = FORECAST_STATUS[status] ?? FORECAST_STATUS.idle;
        const top = forecast?.warnings[0];
        const tone =
          status === "at_risk"
            ? "border-red-200 bg-red-50/70 hover:border-red-300"
            : status === "tight"
              ? "border-amber-200 bg-amber-50/70 hover:border-amber-300"
              : status === "on_track" || status === "done"
                ? "border-emerald-200 bg-emerald-50/70 hover:border-emerald-300"
                : "border-slate-200 bg-white hover:border-brand-300";
        return (
          <Link
            to="/forecast"
            className={`group flex items-center justify-between rounded-2xl border p-4 shadow-sm transition ${tone}`}
          >
            <div className="flex min-w-0 items-center gap-3">
              <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${st.badge}`}>
                <TrendingUp size={18} />
              </div>
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
                  进度预测 · 负载预警
                  {status !== "idle" && (
                    <span className={`rounded-md px-1.5 py-0.5 text-[10px] font-medium ${st.badge}`}>
                      {st.label}
                    </span>
                  )}
                </div>
                <div className="mt-0.5 truncate text-xs text-slate-500">
                  {top
                    ? `${top.date.slice(5)} · ${top.message}`
                    : forecast?.overall.message ?? "添加考试 DDL 后即可推演能否按时学完"}
                </div>
              </div>
            </div>
            <ArrowRight size={16} className="ml-3 shrink-0 text-slate-400 transition group-hover:translate-x-0.5" />
          </Link>
        );
      })()}

      {/* 考试倒计时入口 */}
      {(() => {
        const upcoming = stats?.upcoming ?? [];
        const nearestExam =
          upcoming.find(
            (t) => t.kind === "exam" || /考试|期末|期中|答辩|验收/.test(t.title)
          ) ?? upcoming[0];
        return (
          <Link
            to="/countdown"
            className="group flex items-center justify-between rounded-2xl border border-brand-100 bg-brand-50/60 p-4 shadow-sm transition hover:border-brand-300"
          >
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-600 text-white">
                <Timer size={18} />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-800">考试倒计时时间轴</div>
                <div className="mt-0.5 text-xs text-slate-500">
                  {nearestExam
                    ? `${nearestExam.title} · ${countdown(nearestExam.due_at).text}`
                    : "暂无临近考试，去「DDL 任务」添加一个"}
                </div>
              </div>
            </div>
            <ArrowRight size={16} className="text-brand-500 transition group-hover:translate-x-0.5" />
          </Link>
        );
      })()}

      {/* 番茄钟 / 专注计时入口 */}
      <Link
        to="/focus"
        className="group flex items-center justify-between rounded-2xl border border-orange-100 bg-orange-50/60 p-4 shadow-sm transition hover:border-orange-300"
      >
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-orange-500 text-white">
            <TimerReset size={18} />
          </div>
          <div>
            <div className="text-sm font-semibold text-slate-800">番茄钟 · 专注计时</div>
            <div className="mt-0.5 text-xs text-slate-500">
              {stats
                ? stats.focus_count_today > 0 || stats.focus_minutes_today > 0
                  ? `今日已专注 ${stats.focus_minutes_today} 分钟（${stats.focus_count_today} 🍅）· 连续 ${stats.focus_streak} 天，再来一个 →`
                  : stats.focus_streak > 0
                    ? `连续专注 ${stats.focus_streak} 天，今天开始第一个番茄 →`
                    : "开始一个番茄，可挂到复习计划的复习块上 →"
                : "开始一个番茄，可挂到复习计划的复习块上 →"}
            </div>
          </div>
        </div>
        <ArrowRight size={16} className="text-orange-500 transition group-hover:translate-x-0.5" />
      </Link>

      {/* 错题本入口 */}
      <Link
        to="/wrongbook"
        className="group flex items-center justify-between rounded-2xl border border-rose-100 bg-rose-50/60 p-4 shadow-sm transition hover:border-rose-300"
      >
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-rose-600 text-white">
            <BookX size={18} />
          </div>
          <div>
            <div className="text-sm font-semibold text-slate-800">错题本 · 错题归集</div>
            <div className="mt-0.5 text-xs text-slate-500">
              {stats
                ? stats.wrong_questions_open > 0
                  ? `${stats.wrong_questions_open} 题待攻克（共 ${stats.wrong_questions} 题），去重练 →`
                  : stats.wrong_questions > 0
                    ? "待攻克错题已清零，继续保持 🎉"
                    : "把做错的题归集起来，重练到掌握"
                : "把做错的题归集起来，重练到掌握"}
            </div>
          </div>
        </div>
        <ArrowRight size={16} className="text-rose-500 transition group-hover:translate-x-0.5" />
      </Link>

      {/* AI 自测 / 抽背入口 */}
      <Link
        to="/quiz"
        className="group flex items-center justify-between rounded-2xl border border-violet-100 bg-violet-50/60 p-4 shadow-sm transition hover:border-violet-300"
      >
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-600 text-white">
            <BrainCircuit size={18} />
          </div>
          <div>
            <div className="text-sm font-semibold text-slate-800">AI 自测 · 抽背</div>
            <div className="mt-0.5 text-xs text-slate-500">
              {stats
                ? stats.quizzes > 0
                  ? stats.quizzes_completed > 0
                    ? `${stats.quizzes_completed} 组已完成（共 ${stats.quizzes} 组自测），再来一组 →`
                    : `${stats.quizzes} 组自测待完成，继续作答 →`
                  : "基于笔记自动出选择题 / 问答题，考考自己 →"
                : "基于笔记自动出选择题 / 问答题，考考自己 →"}
            </div>
          </div>
        </div>
        <ArrowRight size={16} className="text-violet-500 transition group-hover:translate-x-0.5" />
      </Link>

      {/* 最近到期 */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-700">最近的 DDL</h2>
          <Link
            to="/tasks"
            className="flex items-center gap-1 text-xs text-brand-600 hover:underline"
          >
            全部任务 <ArrowRight size={13} />
          </Link>
        </div>
        {stats?.upcoming?.length ? (
          <div className="divide-y divide-slate-100">
            {stats.upcoming.map((t) => {
              const due = dueLabel(t.due_at);
              return (
                <div key={t.id} className="flex items-center gap-3 py-2.5">
                  <span className={`rounded-md px-2 py-0.5 text-[10px] font-medium ${KIND_COLORS[t.kind]}`}>
                    {TASK_KINDS[t.kind]}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-sm text-slate-700">{t.title}</span>
                  {t.course_name && (
                    <span className="hidden text-xs text-slate-400 md:block">{t.course_name}</span>
                  )}
                  <span className={`text-xs font-medium ${due.cls}`}>
                    {fmtDT(t.due_at)} · {due.text}
                  </span>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="py-6 text-center text-sm text-slate-400">
            暂无临近 DDL，去「智能入库」添加一个吧
          </div>
        )}
      </div>

      {extract && (
        <Modal title="AI 识别结果 · 请确认" onClose={() => { if (!applying) setExtract(null); }} wide>
          <ConfirmPanel
            onBusyChange={setApplying}
            textSource={quickText}
            extract={extract}
            courses={courses}
            onApplied={() => {
              setQuickText("");
            }}
          />
        </Modal>
      )}
    </div>
  );
}
