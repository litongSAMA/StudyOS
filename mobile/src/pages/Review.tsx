import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlarmClock,
  BookOpen,
  Brain,
  CalendarDays,
  Download,
  Play,
  RotateCcw,
  Sparkles,
  TimerReset,
  Trash2,
  Wand2,
} from "lucide-react";
import { apiFocus, apiReview, apiTasks, exportUrl } from "../api";
import { useApp } from "../App";
import type { FocusStats, ReviewPlan, ReviewState, Task } from "../types";
import { RATINGS, masteryLabel } from "../types";
import DownloadLink from "../components/DownloadLink";
import { Empty, Spinner } from "../components/ui";
import { dueLabel, fmtDate } from "../utils";

export default function Review() {
  const { refreshKey, bump } = useApp();
  const [allTasks, setAllTasks] = useState<Task[]>([]);
  const [plans, setPlans] = useState<ReviewPlan[]>([]);
  const [states, setStates] = useState<ReviewState[]>([]);
  const [focusStats, setFocusStats] = useState<FocusStats | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [hours, setHours] = useState(3);
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    apiTasks.list({ done: false }).then(setAllTasks).catch(console.error);
    apiReview.list().then(setPlans).catch(console.error);
    apiReview.states().then(setStates).catch(console.error);
    // 番茄钟投入（按复习块聚合，展示每个复习块已专注了多久）
    apiFocus.stats(90).then(setFocusStats).catch(console.error);
  }, [refreshKey]);

  const examCandidates = useMemo(
    () =>
      allTasks.filter(
        (t) => t.kind === "exam" || t.kind === "report" || /考试|期末|期中|答辩|验收/.test(t.title)
      ),
    [allTasks]
  );

  /** 选中考试涉及的课程（未选中时展示全部课程的掌握度） */
  const focusedCourseIds = useMemo(() => {
    if (!selected.length) return null;
    return new Set(
      allTasks
        .filter((t) => selected.includes(t.id))
        .map((t) => t.course_id)
        .filter((x): x is number => x != null)
    );
  }, [selected, allTasks]);

  const rate = async (materialId: number, rating: string) => {
    try {
      const updated = await apiReview.rate(materialId, rating);
      setStates((prev) => prev.map((s) => (s.material_id === materialId ? updated : s)));
    } catch (e) {
      alert((e as Error).message);
    }
  };

  const resetState = async (materialId: number) => {
    try {
      await apiReview.resetState(materialId);
      setStates((prev) =>
        prev.map((s) =>
          s.material_id === materialId
            ? { ...s, mastery: 0, repetitions: 0, lapses: 0, interval_days: 0, due_at: null, last_reviewed_at: null, last_rating: "", is_due: false, overdue_days: 0 }
            : s
        )
      );
    } catch (e) {
      alert((e as Error).message);
    }
  };

  const generate = async () => {
    if (!selected.length) return;
    setGenerating(true);
    try {
      await apiReview.generate(selected, hours);
      setSelected([]);
      bump();
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setGenerating(false);
    }
  };

  const removePlan = async (id: number) => {
    await apiReview.remove(id);
    bump();
  };

  /** 到期的间隔复习队列（逾期越久越靠前） */
  const dueQueue = useMemo(
    () => states.filter((s) => s.is_due).sort((a, b) => b.overdue_days - a.overdue_days),
    [states]
  );

  /** 掌握度看板：按课程分组 */
  const groups = useMemo(() => {
    const filtered = focusedCourseIds
      ? states.filter((s) => s.course_id != null && focusedCourseIds.has(s.course_id))
      : states;
    const map = new Map<string, ReviewState[]>();
    for (const s of filtered) {
      const key = s.course_name || "未分类";
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(s);
    }
    return Array.from(map.entries());
  }, [states, focusedCourseIds]);

  const plan = plans[0];

  return (
    <div className="mobile-page space-y-6">
      <div className="mobile-page-heading">
        <h1 className="text-xl font-bold text-slate-800">复习清单生成</h1>
        <p className="mt-1 text-sm text-slate-500">
          按 DDL 倒排 + 掌握程度加权 + 间隔重复自动排出每日复习计划
        </p>
      </div>

      {/* 选考试 */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-3 text-sm font-semibold text-slate-700">
          选择要复习的考试 / 大作业（{selected.length} 已选）
        </div>
        {examCandidates.length === 0 ? (
          <div className="text-sm text-slate-400">
            没有找到未完成的考试类任务。先在「DDL 任务」里添加期末考试吧。
          </div>
        ) : (
          <div className="grid gap-2 md:grid-cols-2">
            {examCandidates.map((t) => {
              const due = dueLabel(t.due_at);
              const checked = selected.includes(t.id);
              return (
                <button
                  key={t.id}
                  onClick={() =>
                    setSelected((prev) =>
                      checked ? prev.filter((x) => x !== t.id) : [...prev, t.id]
                    )
                  }
                  className={`flex items-center gap-3 rounded-xl border p-3 text-left transition ${
                    checked ? "border-brand-500 bg-brand-50" : "border-slate-200 hover:border-brand-300"
                  }`}
                >
                  <input type="checkbox" checked={checked} readOnly className="h-4 w-4 accent-brand-600" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm text-slate-700">{t.title}</div>
                    <div className="text-[11px] text-slate-400">
                      {t.course_name ?? ""} · {fmtDate(t.due_at)}
                    </div>
                  </div>
                  <span className={`text-xs font-medium ${due.cls}`}>{due.text}</span>
                </button>
              );
            })}
          </div>
        )}
        <div className="mt-4 flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-slate-500">
            每日复习时长
            <input
              type="number"
              min={0.5}
              max={12}
              step={0.5}
              value={hours}
              onChange={(e) => setHours(Number(e.target.value))}
              className="w-20 rounded-lg border border-slate-200 px-2 py-1.5 text-sm"
            />
            小时
          </label>
          <button
            onClick={generate}
            disabled={!selected.length || generating}
            className="flex items-center gap-2 rounded-xl bg-brand-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
          >
            {generating ? <Spinner /> : <Wand2 size={16} />}
            生成复习清单
          </button>
          <span className="text-[11px] text-slate-400">
            掌握度低的资料多分配时间；复习后点「忘了/吃力/正常/轻松」记录效果，计划随之优化
          </span>
        </div>
      </div>

      {/* 掌握程度 · 间隔重复 */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
          <Brain size={15} className="text-brand-500" />
          掌握程度 · 间隔重复
          <span className="text-[11px] font-normal text-slate-400">
            {focusedCourseIds ? "（只看所选考试课程）" : "（全部课程）"}
          </span>
        </div>

        {dueQueue.length > 0 && (
          <div className="mb-4 rounded-xl border border-red-100 bg-red-50/60 p-3">
            <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-red-600">
              <AlarmClock size={13} />
              今日到期复习（{dueQueue.length} 份）——按间隔重复该过一遍了
            </div>
            <div className="space-y-1.5">
              {dueQueue.map((s) => (
                <div key={s.material_id} className="flex flex-wrap items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-sm text-slate-700">
                    {s.material_title}
                    <span className="ml-1.5 text-[11px] text-slate-400">{s.course_name}</span>
                  </span>
                  {s.overdue_days > 0 && (
                    <span className="rounded-md bg-red-100 px-1.5 py-0.5 text-[10px] font-medium text-red-600">
                      逾期 {s.overdue_days} 天
                    </span>
                  )}
                  <RateButtons last={s.last_rating} onRate={(r) => rate(s.material_id, r)} />
                </div>
              ))}
            </div>
          </div>
        )}

        {groups.length === 0 ? (
          <div className="text-sm text-slate-400">
            还没有归档资料。先在「资料库」里导入课件/笔记，复习时点击评分即可记录掌握程度。
          </div>
        ) : (
          <div className="space-y-4">
            {groups.map(([course, items]) => (
              <CourseMastery key={course} course={course} items={items} onRate={rate} onReset={resetState} />
            ))}
          </div>
        )}
      </div>

      {/* 计划展示 */}
      {plan ? (
        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-700">{plan.title}</h2>
            <div className="flex items-center gap-1.5">
              <ExportBtn fmt="ical" label="iCal" />
              <ExportBtn fmt="csv" label="CSV" />
              <ExportBtn fmt="md" label="Markdown" />
              <button
                onClick={() => removePlan(plan.id)}
                className="ml-1 rounded-lg p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-500"
              >
                <Trash2 size={15} />
              </button>
            </div>
          </div>

          {/* 按日分组 */}
          <div className="space-y-2">
            {plan.sessions.map((s, i) => {
              const today = new Date();
              const todayKey = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
              const isToday = s.date === todayKey;
              const focus = focusStats?.by_plan_date.find(
                (f) => f.plan_id === plan.id && f.plan_date === s.date
              );
              return (
                <div
                  key={i}
                  className={`flex items-start gap-3 rounded-xl border p-3.5 ${
                    isToday
                      ? "border-brand-300 bg-brand-50/50"
                      : "border-slate-100 bg-slate-50/60"
                  }`}
                >
                  <div className="flex h-11 w-11 shrink-0 flex-col items-center justify-center rounded-xl bg-white shadow-sm ring-1 ring-slate-200">
                    <CalendarDays size={15} className="text-brand-500" />
                    <span className="mt-0.5 text-[10px] font-semibold text-slate-600">
                      {s.date.slice(5).replace("-", "/")}
                    </span>
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="font-medium text-slate-800">{s.course}</span>
                      <span className="text-slate-500">· {s.scope}</span>
                      <span className="rounded-md bg-brand-50 px-1.5 py-0.5 text-[10px] text-brand-700">
                        {s.hours} 小时
                      </span>
                      {typeof s.mastery === "number" && (
                        <span className="flex items-center gap-1 rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">
                          <Brain size={10} />
                          掌握度 {Math.round(s.mastery * 100)}%
                        </span>
                      )}
                      {isToday && (
                        <span className="rounded-md bg-brand-600 px-1.5 py-0.5 text-[10px] font-medium text-white">
                          今天
                        </span>
                      )}
                      {focus && (focus.count > 0 || focus.minutes > 0) && (
                        <span
                          className="flex items-center gap-1 rounded-md bg-orange-50 px-1.5 py-0.5 text-[10px] font-medium text-orange-600"
                          title={`已投入：${focus.count} 个番茄 · ${focus.minutes} 分钟`}
                        >
                          <TimerReset size={10} />
                          已专注 {focus.minutes} 分钟（{focus.count} 🍅）
                        </span>
                      )}
                    </div>
                    {s.due_titles?.length ? (
                      <div className="mt-1 flex items-center gap-1 text-[11px] text-red-500">
                        <AlarmClock size={11} />
                        到期复习：{s.due_titles.join("；")}
                      </div>
                    ) : null}
                    {s.spaced_titles?.length ? (
                      <div className="mt-1 flex items-center gap-1 text-[11px] text-blue-500">
                        <Sparkles size={11} />
                        间隔复习：{s.spaced_titles.join("；")}
                      </div>
                    ) : null}
                    <div className="mt-1 flex items-center gap-1 text-[11px] text-slate-400">
                      <BookOpen size={11} />
                      {(s.study_titles?.length ? s.study_titles : s.materials || []).join("；") ||
                        "（该课程暂无归档资料，先补资料再复习更高效）"}
                    </div>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-2">
                    <div className="text-right text-[11px] text-slate-400">
                      目标：{s.exam}
                      <br />
                      <span className="font-medium text-red-400">{s.exam_date} 考试</span>
                    </div>
                    <Link
                      to={`/focus?plan=${plan.id}&date=${s.date}`}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-2.5 py-1.5 text-[11px] font-medium text-white transition hover:bg-brand-700"
                      title="在番茄钟里专注完成这个复习块，结束后直接给资料评分"
                    >
                      <Play size={11} />
                      开始专注
                    </Link>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <Empty text="还没有复习计划。选中上方考试，一键生成按天排好的复习清单。" />
      )}

      {plans.length > 1 && (
        <div className="text-xs text-slate-400">
          历史计划 {plans.length - 1} 个（可在设置中导出全部）
        </div>
      )}
    </div>
  );
}

/** 一门课程的掌握度看板：平均进度条 + 每份资料的评分按钮 */
function CourseMastery({
  course,
  items,
  onRate,
  onReset,
}: {
  course: string;
  items: ReviewState[];
  onRate: (materialId: number, rating: string) => void;
  onReset: (materialId: number) => void;
}) {
  const avg = items.length ? items.reduce((a, s) => a + s.mastery, 0) / items.length : 0;
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-2">
        <span className="text-xs font-semibold text-slate-700">{course}</span>
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
          <div
            className={`h-full rounded-full transition-all ${barColor(avg)}`}
            style={{ width: `${Math.round(avg * 100)}%` }}
          />
        </div>
        <span className="text-[11px] text-slate-500">
          {masteryLabel(avg)} {Math.round(avg * 100)}%
        </span>
      </div>
      <div className="space-y-1.5 pl-1">
        {items.map((s) => (
          <div key={s.material_id} className="flex flex-wrap items-center gap-2">
            <span className="w-40 min-w-0 truncate text-sm text-slate-600" title={s.material_title}>
              {s.material_title}
            </span>
            <div className="h-1.5 w-24 overflow-hidden rounded-full bg-slate-100">
              <div
                className={`h-full rounded-full ${barColor(s.mastery)}`}
                style={{ width: `${Math.round(s.mastery * 100)}%` }}
              />
            </div>
            <span className="w-14 text-[11px] text-slate-500">
              {masteryLabel(s.mastery)}
              {s.repetitions > 0 ? ` ${Math.round(s.mastery * 100)}%` : ""}
            </span>
            {s.repetitions > 0 && (
              <span className="text-[10px] text-slate-400" title="连续成功次数 / 当前间隔">
                第{s.repetitions}次 · {s.interval_days}天
              </span>
            )}
            <RateButtons last={s.last_rating} onRate={(r) => onRate(s.material_id, r)} />
            <button
              onClick={() => onReset(s.material_id)}
              title="重置掌握程度（重新开始间隔重复）"
              className="rounded-md p-1 text-slate-300 transition hover:bg-slate-100 hover:text-slate-500"
            >
              <RotateCcw size={12} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function RateButtons({ last, onRate }: { last: string; onRate: (rating: string) => void }) {
  return (
    <div className="flex items-center gap-1">
      {Object.entries(RATINGS).map(([key, cfg]) => (
        <button
          key={key}
          onClick={() => onRate(key)}
          title={`${cfg.label}：${RATING_HINTS[key]}`}
          className={`rounded-md border px-1.5 py-0.5 text-[11px] font-medium transition ${
            last === key ? cfg.active : cfg.cls
          }`}
        >
          {cfg.label}
        </button>
      ))}
    </div>
  );
}

const RATING_HINTS: Record<string, string> = {
  again: "完全忘了 → 间隔回 1 天，掌握度下降",
  hard: "回想吃力 → 间隔缩短，掌握度微升",
  good: "正常想起 → 间隔按阶梯拉长",
  easy: "轻松答出 → 间隔拉得更长，掌握度大涨",
};

function barColor(m: number): string {
  if (m >= 0.85) return "bg-emerald-500";
  if (m >= 0.4) return "bg-amber-500";
  return m > 0 ? "bg-orange-400" : "bg-slate-200";
}

function ExportBtn({ fmt, label }: { fmt: "ical" | "csv" | "md"; label: string }) {
  return (
    <DownloadLink
      path={exportUrl("review", fmt)}
      fallbackName={`studyos-review.${fmt === "ical" ? "ics" : fmt}`}
      className="flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs text-slate-600 transition hover:bg-slate-50"
    >
      <Download size={13} /> {label}
    </DownloadLink>
  );
}
