import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  AlarmClock,
  BookOpen,
  CalendarClock,
  CircleCheck,
  Gauge,
  Sparkles,
  TrendingUp,
  TriangleAlert,
} from "lucide-react";
import { apiForecast } from "../api";
import { useApp } from "../App";
import type { Forecast } from "../types";
import { FORECAST_STATUS, LOAD_LEVELS } from "../types";
import { Empty, PageHeader, Spinner } from "../components/ui";

/** "YYYY-MM-DD" → "MM-DD"（图表坐标轴用） */
const mmdd = (iso: string) => iso.slice(5);

/** 小时数展示：整数不带小数 */
const fmtH = (h: number) => (Number.isInteger(h) ? String(h) : h.toFixed(1));

const CAPACITY_OPTIONS = [1, 2, 3, 4, 6, 8];
const WINDOW_OPTIONS = [7, 14, 21, 30];

export default function ForecastPage() {
  const { refreshKey } = useApp();
  const [hours, setHours] = useState(3);
  const [windowDays, setWindowDays] = useState(21);
  const [data, setData] = useState<Forecast | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    apiForecast
      .get(hours, windowDays)
      .then(setData)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [refreshKey, hours, windowDays]);

  const overall = data?.overall;
  const st = overall ? FORECAST_STATUS[overall.status] ?? FORECAST_STATUS.idle : null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="进度预测 · 负载预警"
        desc="按 DDL 倒排推演「能不能按时学完」，逐日检查「会不会学爆」"
        actions={
          /* 参数控制：每日学习容量 + 预警窗口 */
          <div className="flex flex-col items-end gap-2">
            <div className="flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white p-1 shadow-sm">
              <span className="pl-2 pr-1 text-[11px] text-slate-400">每日容量</span>
              {CAPACITY_OPTIONS.map((h) => (
                <button
                  key={h}
                  onClick={() => setHours(h)}
                  className={`rounded-lg px-2.5 py-1 text-xs font-medium transition ${
                    hours === h
                      ? "bg-brand-600 text-white"
                      : "text-slate-500 hover:bg-slate-100"
                  }`}
                >
                  {h}h
                </button>
              ))}
            </div>
            <div className="flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white p-1 shadow-sm">
              <span className="pl-2 pr-1 text-[11px] text-slate-400">预警窗口</span>
              {WINDOW_OPTIONS.map((d) => (
                <button
                  key={d}
                  onClick={() => setWindowDays(d)}
                  className={`rounded-lg px-2.5 py-1 text-xs font-medium transition ${
                    windowDays === d
                      ? "bg-brand-600 text-white"
                      : "text-slate-500 hover:bg-slate-100"
                  }`}
                >
                  {d}天
                </button>
              ))}
            </div>
          </div>
        }
      />

      {loading && !data && (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      )}

      {data && overall && overall.status === "idle" ? (
        <div>
          <Empty text="暂无未完成的考试/大作业，预测无从谈起" />
          <div className="mt-4 text-center text-sm">
            <Link to="/tasks" className="text-brand-600 hover:underline">
              去「DDL 任务」添加一场考试 →
            </Link>
          </div>
        </div>
      ) : data && overall && st ? (
        <>
          {/* 总体结论横幅 */}
          <div
            className={`rounded-2xl border p-5 shadow-sm ${
              overall.status === "at_risk"
                ? "border-red-200 bg-red-50"
                : overall.status === "tight"
                  ? "border-amber-200 bg-amber-50"
                  : "border-emerald-200 bg-emerald-50"
            }`}
          >
            <div className="flex flex-wrap items-center gap-x-8 gap-y-4">
              <div className="flex min-w-48 items-center gap-3">
                <div className={`flex h-11 w-11 items-center justify-center rounded-xl ${st.badge}`}>
                  {overall.status === "at_risk" ? (
                    <TriangleAlert size={22} />
                  ) : overall.status === "tight" ? (
                    <Gauge size={22} />
                  ) : (
                    <CircleCheck size={22} />
                  )}
                </div>
                <div>
                  <div className={`text-base font-bold ${st.cls}`}>{st.label}</div>
                  <div className="max-w-xl text-xs text-slate-600">{overall.message}</div>
                </div>
              </div>
              <div className="flex flex-wrap gap-6 text-center">
                {[
                  { label: "剩余总学时", value: `${fmtH(overall.remaining_hours)}h`, icon: BookOpen },
                  { label: "预警课程", value: overall.courses_at_risk, icon: TriangleAlert },
                  { label: "高负载天数", value: overall.busy_days, icon: AlarmClock },
                ].map(({ label, value, icon: Icon }) => (
                  <div key={label}>
                    <div className="flex items-center justify-center gap-1 text-lg font-bold text-slate-800">
                      <Icon size={15} className="text-slate-400" />
                      {value}
                    </div>
                    <div className="text-[11px] text-slate-500">{label}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* 每门课的预测卡片 */}
          <div className="grid gap-4 md:grid-cols-2">
            {data.courses.map((c) => {
              const cs = FORECAST_STATUS[c.status] ?? FORECAST_STATUS.on_track;
              return (
                <div key={c.task_id} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-sm font-semibold text-slate-800">
                        {c.course_name}
                        <span className="ml-2 text-xs font-normal text-slate-400">{c.exam_title}</span>
                      </div>
                      <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                        <CalendarClock size={13} />
                        {c.due_date} · 剩 {c.days_left} 天
                      </div>
                    </div>
                    <span className={`shrink-0 rounded-md px-2 py-0.5 text-[11px] font-medium ${cs.badge}`}>
                      {cs.label}
                    </span>
                  </div>

                  {/* 完成进度条 */}
                  <div className="mt-3">
                    <div className="mb-1 flex justify-between text-[11px] text-slate-400">
                      <span>预计进度</span>
                      <span>{c.progress_pct.toFixed(0)}%</span>
                    </div>
                    <div className="h-2 overflow-hidden rounded-full bg-slate-100">
                      <div
                        className={`h-full rounded-full transition-all ${cs.bar}`}
                        style={{ width: `${Math.min(c.progress_pct, 100)}%` }}
                      />
                    </div>
                  </div>

                  <div className="mt-3 grid grid-cols-4 gap-2 text-center">
                    {[
                      { label: "剩余学时", value: `${fmtH(c.remaining_hours)}h` },
                      { label: "需每日", value: `${fmtH(c.required_pace_h)}h` },
                      {
                        label: "预计完成",
                        value: c.predicted_finish_date ? mmdd(c.predicted_finish_date) : "来不及",
                        danger: !c.predicted_finish_date,
                      },
                      { label: "考试日掌握", value: `${Math.round(c.predicted_mastery * 100)}%` },
                    ].map((it) => (
                      <div key={it.label} className="rounded-xl bg-slate-50 px-1 py-2">
                        <div
                          className={`text-sm font-bold ${it.danger ? "text-red-600" : "text-slate-800"}`}
                        >
                          {it.value}
                        </div>
                        <div className="text-[10px] text-slate-400">{it.label}</div>
                      </div>
                    ))}
                  </div>

                  <div className="mt-3 text-xs text-slate-500">
                    {c.materials_mastered > 0 && (
                      <span className="mr-3">
                        已掌握 {c.materials_mastered}/{c.materials_total} 份资料
                      </span>
                    )}
                    {c.message}
                  </div>
                </div>
              );
            })}
          </div>

          {/* 预计掌握度曲线 */}
          {data.curve.length >= 2 && <MasteryChart data={data} />}

          {/* 节奏对比 + 负载图 */}
          <div className="grid gap-4 lg:grid-cols-5">
            <PaceCard data={data} />
            <LoadChart data={data} />
          </div>

          {/* 告警列表 */}
          {data.warnings.length > 0 && (
            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
                <TriangleAlert size={16} className="text-amber-500" />
                预警清单（{data.warnings.length} 条）
              </div>
              <div className="space-y-2">
                {data.warnings.map((w, i) => (
                  <div
                    key={i}
                    className={`flex items-start gap-2.5 rounded-xl border px-3.5 py-2.5 text-sm ${
                      w.level === "overload"
                        ? "border-red-100 bg-red-50 text-red-700"
                        : "border-amber-100 bg-amber-50 text-amber-700"
                    }`}
                  >
                    <TriangleAlert size={15} className="mt-0.5 shrink-0" />
                    <div className="min-w-0">
                      <span className="mr-2 rounded bg-white/70 px-1.5 py-0.5 text-[10px] font-medium">
                        {mmdd(w.date)}
                      </span>
                      {w.message}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}

/** SVG 掌握度曲线：顺序仿真逐日掌握度 + 考试日标记 */
function MasteryChart({ data }: { data: Forecast }) {
  const W = 760;
  const H = 230;
  const PAD = { l: 44, r: 14, t: 26, b: 26 };
  const iw = W - PAD.l - PAD.r;
  const ih = H - PAD.t - PAD.b;

  const pts = data.curve;
  const x = (i: number) => PAD.l + (i * iw) / (pts.length - 1);
  const y = (m: number) => PAD.t + ih * (1 - m);

  // 考试日标记（落在曲线时间范围内的）
  const exams = data.courses
    .map((c) => c.due_date)
    .filter((d) => d >= pts[0].date && d <= pts[pts.length - 1].date);

  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.mastery).toFixed(1)}`).join(" ");
  const area = `${line} L${x(pts.length - 1).toFixed(1)},${(PAD.t + ih).toFixed(1)} L${x(0).toFixed(1)},${(PAD.t + ih).toFixed(1)} Z`;

  const gridVals = [0, 0.25, 0.5, 0.75, 1];
  const xTick = (i: number) => x(Math.round((i * (pts.length - 1)) / 3));

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
        <TrendingUp size={16} className="text-brand-500" />
        预计掌握度曲线
        <span className="text-xs font-normal text-slate-400">
          （顺序仿真：每日容量先分给最早到期的课程）
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
        <defs>
          <linearGradient id="masteryFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#6366f1" stopOpacity="0.28" />
            <stop offset="100%" stopColor="#6366f1" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {/* 横向网格 + Y 轴标签 */}
        {gridVals.map((g) => (
          <g key={g}>
            <line
              x1={PAD.l} y1={y(g)} x2={W - PAD.r} y2={y(g)}
              stroke="#e2e8f0" strokeWidth="1" strokeDasharray={g === 0 ? "" : "3 4"}
            />
            <text x={PAD.l - 8} y={y(g) + 4} textAnchor="end" fontSize="10" fill="#94a3b8">
              {Math.round(g * 100)}%
            </text>
          </g>
        ))}
        {/* X 轴日期（首/中/尾） */}
        {[0, 1, 2, 3].map((i) => (
          <text
            key={i}
            x={xTick(i)} y={H - 8} textAnchor="middle" fontSize="10" fill="#94a3b8"
          >
            {mmdd(pts[Math.round((i * (pts.length - 1)) / 3)].date)}
          </text>
        ))}
        {/* 考试日竖线 */}
        {exams.slice(0, 4).map((d) => {
          const idx = pts.findIndex((p) => p.date >= d);
          if (idx < 0) return null;
          return (
            <line
              key={d}
              x1={x(idx)} y1={PAD.t} x2={x(idx)} y2={PAD.t + ih}
              stroke="#ef4444" strokeWidth="1" strokeDasharray="2 4" opacity="0.6"
            />
          );
        })}
        <path d={area} fill="url(#masteryFill)" />
        <polyline points={pts.map((p, i) => `${x(i).toFixed(1)},${y(p.mastery).toFixed(1)}`).join(" ")} fill="none" stroke="#6366f1" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
        {/* 终点标记 */}
        <circle cx={x(pts.length - 1)} cy={y(pts[pts.length - 1].mastery)} r="4" fill="#6366f1" />
        <text x={x(pts.length - 1)} y={y(pts[pts.length - 1].mastery) - 10} textAnchor="end" fontSize="10" fill="#6366f1" fontWeight="600">
          {Math.round(pts[pts.length - 1].mastery * 100)}%
        </text>
      </svg>
      {exams.length > 0 && (
        <div className="mt-1 text-[11px] text-slate-400">红色虚线 = 考试日</div>
      )}
    </div>
  );
}

/** 近 7 天实际节奏 vs 所需节奏 */
function PaceCard({ data }: { data: Forecast }) {
  const pace = data.pace;
  const gap = pace.required_hours_per_day > 0 && pace.study_hours_last_7d < pace.required_hours_per_day;
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:col-span-2">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
        <Activity size={16} className="text-brand-500" />
        近 7 天复习节奏
      </div>
      <div className="grid grid-cols-3 gap-2 text-center">
        {[
          { label: "复习评分次数", value: pace.reviews_last_7d, unit: "次" },
          { label: "复习学时（约）", value: fmtH(pace.study_hours_last_7d), unit: "h" },
          { label: "需要每日", value: fmtH(pace.required_hours_per_day), unit: "h/天" },
        ].map((it) => (
          <div key={it.label} className="rounded-xl bg-slate-50 px-1 py-3">
            <div className="text-xl font-bold text-slate-800">
              {it.value}
              <span className="ml-0.5 text-[10px] font-normal text-slate-400">{it.unit}</span>
            </div>
            <div className="mt-0.5 text-[10px] text-slate-400">{it.label}</div>
          </div>
        ))}
      </div>
      <div className="mt-3 text-xs leading-relaxed text-slate-500">
        {pace.required_hours_per_day <= 0 ? (
          <>暂无未完成的考试，节奏压力为 0。</>
        ) : gap ? (
          <span className="text-amber-700">
            实际复习节奏明显低于所需（{fmtH(pace.study_hours_last_7d)}h/周 ≈ 每天{" "}
            {fmtH(pace.study_hours_last_7d / 7)}h），建议提高每日学习时长。
          </span>
        ) : (
          <>当前复习节奏可以支撑所需的学习强度，继续保持。</>
        )}
      </div>
      <div className="mt-3 text-[11px] text-slate-400">
        实际节奏按间隔复习评分记录近似（每次按 0.5h 计）；在「复习计划」页评分越多，估计越准。
      </div>
    </div>
  );
}

/** 未来 N 天逐日负载柱状图 + 容量线 */
function LoadChart({ data }: { data: Forecast }) {
  const capacity = data.hours_per_day;
  const CH = 140;
  const maxH = capacity * 2; // 柱高按 2× 容量封顶

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:col-span-3">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
          <Sparkles size={16} className="text-brand-500" />
          未来 {data.load_days.length} 天负载
        </div>
        <div className="flex items-center gap-3 text-[10px] text-slate-400">
          <span className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-sm bg-emerald-400" /> 轻松
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-sm bg-amber-400" /> 偏满
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-sm bg-red-500" /> 超载
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 border-t border-dashed border-slate-400" /> 容量 {fmtH(capacity)}h
          </span>
        </div>
      </div>
      <div className="relative">
        {/* 容量虚线 */}
        <div
          className="pointer-events-none absolute inset-x-0 border-t border-dashed border-slate-300"
          style={{ bottom: `${(capacity / maxH) * CH}px` }}
        />
        <div className="flex items-end gap-[3px]" style={{ height: CH }}>
          {data.load_days.map((d) => {
            const lv = LOAD_LEVELS[d.level] ?? LOAD_LEVELS.ok;
            const h = Math.max((d.total_hours / maxH) * CH, d.deadline_count > 0 ? 4 : 2);
            return (
              <div
                key={d.date}
                className="group relative flex-1 cursor-default"
                title={`${d.date}（${d.is_today ? "今天 · " : ""}${lv.label}）\n计划 ${fmtH(d.plan_hours)}h · 到期复习 ${d.review_count} 份(${fmtH(d.review_hours)}h)\nDDL 截止 ${d.deadline_count} 个\n合计 ${fmtH(d.total_hours)}h / 容量 ${fmtH(capacity)}h`}
              >
                <div
                  className={`w-full rounded-t ${d.is_today ? "ring-2 ring-brand-500 ring-offset-1" : ""} ${lv.bar}`}
                  style={{ height: `${h}px` }}
                />
                {/* 当日有 DDL：柱顶红点提示 */}
                {d.deadline_count > 0 && (
                  <span
                    className="absolute -top-1.5 left-1/2 h-1.5 w-1.5 -translate-x-1/2 rounded-full bg-red-500"
                  />
                )}
              </div>
            );
          })}
        </div>
        {/* X 轴日期（隔几天标一个） */}
        <div className="mt-1 flex gap-[3px]">
          {data.load_days.map((d, i) => (
            <div key={d.date} className="flex-1 text-center text-[9px] text-slate-400">
              {i % Math.max(Math.floor(data.load_days.length / 8), 1) === 0 || d.is_today
                ? mmdd(d.date)
                : ""}
            </div>
          ))}
        </div>
      </div>
      <div className="mt-2 text-[11px] text-slate-400">
        柱高 = 当日「复习计划学时 + 到期间隔复习」合计（封顶 2× 容量）；红点 = 当日有 DDL 截止（负载权重按考试 2.0 / 报告 1.5 / 作业 1.0 计）。
      </div>
    </div>
  );
}
