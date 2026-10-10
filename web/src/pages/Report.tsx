import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  BarChart3,
  BookOpen,
  CheckCircle2,
  Download,
  GraduationCap,
  History,
  LineChart,
  Minus,
  RotateCcw,
  Sparkles,
  Target,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { apiReport } from "../api";
import { useApp } from "../App";
import type { Report, ReportSemester, ReportSeriesPoint } from "../types";
import DownloadLink from "../components/DownloadLink";
import { Empty, PageHeader, Spinner } from "../components/ui";

/** "YYYY-MM-DD" → "MM-DD"（图表坐标轴用） */
const mmdd = (iso: string) => iso.slice(5);

/** 图表最多展示的周数（更早的截断，附提示） */
const MAX_CHART_WEEKS = 104;

/** 图表通用留白 */
const CHART = { W: 760, H: 240, l: 46, r: 14, t: 22, b: 24 };

const xAt = (i: number, n: number) => {
  const iw = CHART.W - CHART.l - CHART.r;
  return n <= 1 ? CHART.l + iw / 2 : CHART.l + (i * iw) / (n - 1);
};

/** X 轴日期标签（首/1/3/2/3/尾均匀取样；点少时去重，避免重复日期） */
function XLabels({ pts, y }: { pts: ReportSeriesPoint[]; y: number }) {
  if (pts.length === 0) return null;
  const idxs = [...new Set([0, 1, 2, 3].map((i) => Math.round((i * (pts.length - 1)) / 3)))];
  return (
    <>
      {idxs.map((i) => (
        <text
          key={i}
          x={xAt(i, pts.length)}
          y={y}
          textAnchor="middle"
          fontSize="10"
          fill="#94a3b8"
        >
          {mmdd(pts[i].date)}
        </text>
      ))}
    </>
  );
}

/** 图表图例（卡片标题行右侧） */
function Legend({ items }: { items: { color: string; label: string }[] }) {
  return (
    <span className="ml-auto hidden items-center gap-3 sm:flex">
      {items.map((it) => (
        <span key={it.label} className="flex items-center gap-1.5 text-[10px] text-slate-400">
          <span className="h-2 w-2 rounded-full" style={{ background: it.color }} />
          {it.label}
        </span>
      ))}
    </span>
  );
}

function ChartCard({
  icon: Icon,
  title,
  hint,
  legend,
  children,
  className = "",
}: {
  icon: typeof TrendingUp;
  title: string;
  hint?: string;
  legend?: { color: string; label: string }[];
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`rounded-2xl border border-slate-200 bg-white p-5 shadow-sm ${className}`}>
      <div className="mb-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-semibold text-slate-700">
        <Icon size={16} className="text-brand-500" />
        {title}
        {hint && <span className="text-xs font-normal text-slate-400">{hint}</span>}
        {legend && <Legend items={legend} />}
      </div>
      {children}
    </div>
  );
}

function ChartHint({ icon: Icon, children }: { icon: typeof TrendingUp; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-sm text-slate-500">
      <Icon size={16} className="shrink-0 text-slate-400" />
      {children}
    </div>
  );
}

/** 掌握度趋势：逐周平均掌握程度折线（0~100%） */
function MasteryChart({ s }: { s: ReportSemester }) {
  const { pts, trimmed } = useTrimmed(s);
  if (s.stats.materials === 0) {
    return (
      <ChartCard icon={TrendingUp} title="掌握度趋势">
        <ChartHint icon={BookOpen}>
          该学期还没有资料——先去「资料库」添加资料，在「复习计划」里评分后，这里会出现掌握度曲线。
        </ChartHint>
      </ChartCard>
    );
  }
  if (pts.length === 0) return null;
  const iw = CHART.W - CHART.l - CHART.r;
  const ih = CHART.H - CHART.t - CHART.b;
  const y = (m: number) => CHART.t + ih * (1 - m);
  const line = pts
    .map((p, i) => `${i === 0 ? "M" : "L"}${xAt(i, pts.length).toFixed(1)},${y(p.mastery).toFixed(1)}`)
    .join(" ");
  const area = `${line} L${xAt(pts.length - 1, pts.length).toFixed(1)},${(CHART.t + ih).toFixed(1)} L${xAt(0, pts.length).toFixed(1)},${(CHART.t + ih).toFixed(1)} Z`;
  const last = pts[pts.length - 1];
  const first = pts[0];
  const gain = last.mastery - first.mastery;
  return (
    <ChartCard
      icon={TrendingUp}
      title="掌握度趋势"
      hint={`（全 ${s.stats.materials} 份资料口径，未复习按 0 计）`}
      legend={[{ color: "#6366f1", label: "平均掌握度" }]}
      className="lg:col-span-2"
    >
      <svg viewBox={`0 0 ${CHART.W} ${CHART.H}`} className="w-full">
        <defs>
          <linearGradient id="reportMasteryFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#6366f1" stopOpacity="0.28" />
            <stop offset="100%" stopColor="#6366f1" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {[0, 0.25, 0.5, 0.75, 1].map((g) => (
          <g key={g}>
            <line
              x1={CHART.l} y1={y(g)} x2={CHART.W - CHART.r} y2={y(g)}
              stroke="#e2e8f0" strokeWidth="1" strokeDasharray={g === 0 ? "" : "3 4"}
            />
            <text x={CHART.l - 8} y={y(g) + 4} textAnchor="end" fontSize="10" fill="#94a3b8">
              {Math.round(g * 100)}%
            </text>
          </g>
        ))}
        <XLabels pts={pts} y={CHART.H - 8} />
        <path d={area} fill="url(#reportMasteryFill)" />
        <polyline
          points={pts.map((p, i) => `${xAt(i, pts.length).toFixed(1)},${y(p.mastery).toFixed(1)}`).join(" ")}
          fill="none" stroke="#6366f1" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
        />
        <circle cx={xAt(pts.length - 1, pts.length)} cy={y(last.mastery)} r="4" fill="#6366f1" />
        <text
          x={xAt(pts.length - 1, pts.length)} y={y(last.mastery) - 10}
          textAnchor="end" fontSize="10" fill="#6366f1" fontWeight="600"
        >
          {Math.round(last.mastery * 100)}%
        </text>
        {/* 较起点增量（有跨度且明显变化时显示） */}
        {gain !== 0 && pts.length > 1 && (
          <text
            x={CHART.l} y={CHART.t - 9} fontSize="10" fill={gain > 0 ? "#059669" : "#e11d48"} fontWeight="600"
          >
            {gain > 0 ? "↑" : "↓"} 较首周 {gain > 0 ? "+" : ""}
            {(gain * 100).toFixed(1)}%
          </text>
        )}
      </svg>
      {trimmed > 0 && <TrimNote n={trimmed} />}
    </ChartCard>
  );
}

/** 复习次数：逐周评分柱状图（tooltip 带累计） */
function ReviewChart({ s }: { s: ReportSemester }) {
  const { pts, trimmed } = useTrimmed(s);
  if (s.stats.reviews === 0) {
    return (
      <ChartCard icon={BarChart3} title="复习次数">
        <ChartHint icon={RotateCcw}>
          还没有复习记录——在「复习计划」页给资料评分（忘了/吃力/正常/轻松）后，这里会按周统计复习次数。
        </ChartHint>
      </ChartCard>
    );
  }
  if (pts.length === 0) return null;
  const iw = CHART.W - CHART.l - CHART.r;
  const ih = CHART.H - CHART.t - CHART.b;
  const maxV = Math.max(1, ...pts.map((p) => p.reviews));
  const bw = Math.max(2, iw / pts.length - 1);
  const barY = (v: number) => CHART.t + ih * (1 - v / maxV);
  const showLabels = pts.length <= 16;
  return (
    <ChartCard
      icon={BarChart3}
      title="复习次数"
      hint="（间隔重复评分次数，按周统计）"
      legend={[{ color: "#818cf8", label: "每周复习" }]}
    >
      <svg viewBox={`0 0 ${CHART.W} ${CHART.H}`} className="w-full">
        <defs>
          <linearGradient id="reportReviewBar" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#a5b4fc" />
            <stop offset="100%" stopColor="#818cf8" />
          </linearGradient>
        </defs>
        {[0, 0.5, 1].map((g) => (
          <g key={g}>
            <line
              x1={CHART.l} y1={barY(maxV * g)} x2={CHART.W - CHART.r} y2={barY(maxV * g)}
              stroke="#e2e8f0" strokeWidth="1" strokeDasharray={g === 0 ? "" : "3 4"}
            />
            <text x={CHART.l - 8} y={barY(maxV * g) + 4} textAnchor="end" fontSize="10" fill="#94a3b8">
              {Math.round(maxV * g)}
            </text>
          </g>
        ))}
        <XLabels pts={pts} y={CHART.H - 8} />
        {pts.map((p, i) => {
          const h = Math.max((p.reviews / maxV) * ih, p.reviews > 0 ? 3 : 0);
          return (
            <g key={p.date}>
              <rect
                x={xAt(i, pts.length) - bw / 2}
                y={p.reviews > 0 ? barY(p.reviews) : CHART.t + ih - 1.5}
                width={bw}
                height={p.reviews > 0 ? h : 1.5}
                rx="2"
                fill={p.reviews > 0 ? "url(#reportReviewBar)" : "#e2e8f0"}
              >
                <title>
                  {`${p.date} · 本周复习 ${p.reviews} 次\n累计复习 ${p.reviews_cum} 次`}
                </title>
              </rect>
              {showLabels && p.reviews > 0 && (
                <text
                  x={xAt(i, pts.length)} y={barY(p.reviews) - 5}
                  textAnchor="middle" fontSize="9" fill="#818cf8" fontWeight="600"
                >
                  {p.reviews}
                </text>
              )}
            </g>
          );
        })}
        <text x={CHART.W - CHART.r} y={CHART.t - 8} textAnchor="end" fontSize="10" fill="#6366f1" fontWeight="600">
          累计 {pts[pts.length - 1].reviews_cum} 次
        </text>
      </svg>
      {trimmed > 0 && <TrimNote n={trimmed} />}
    </ChartCard>
  );
}

/** 错题攻克率时间线：累计攻克率折线 + 每周攻克柱 */
function ConquerChart({ s }: { s: ReportSemester }) {
  const { pts, trimmed } = useTrimmed(s);
  if (s.stats.wrong_total === 0) {
    return (
      <ChartCard icon={Target} title="错题攻克率时间线">
        <ChartHint icon={Target}>
          该学期还没有错题——把做错的题收进「错题本」并重练到掌握，这里会出现攻克率曲线。
        </ChartHint>
      </ChartCard>
    );
  }
  if (pts.length === 0) return null;
  const iw = CHART.W - CHART.l - CHART.r;
  const ih = CHART.H - CHART.t - CHART.b;
  // 上 62% 画攻克率折线，下 38% 画每周攻克柱
  const rateH = ih * 0.62;
  const barTop = CHART.t + rateH + 10;
  const barH = CHART.H - CHART.b - barTop;
  const maxConq = Math.max(1, ...pts.map((p) => p.conquered));
  const yRate = (r: number) => CHART.t + rateH * (1 - r / 100);
  const line = pts
    .map((p, i) => `${i === 0 ? "M" : "L"}${xAt(i, pts.length).toFixed(1)},${yRate(p.wrong_rate).toFixed(1)}`)
    .join(" ");
  const bw = Math.max(2, iw / pts.length - 1);
  const last = pts[pts.length - 1];
  return (
    <ChartCard
      icon={Target}
      title="错题攻克率时间线"
      hint="（折线=累计攻克率，柱=每周攻克数）"
      legend={[
        { color: "#10b981", label: "累计攻克率" },
        { color: "#34d399", label: "每周攻克" },
      ]}
    >
      <svg viewBox={`0 0 ${CHART.W} ${CHART.H}`} className="w-full">
        {[0, 50, 100].map((g) => (
          <g key={g}>
            <line
              x1={CHART.l} y1={yRate(g)} x2={CHART.W - CHART.r} y2={yRate(g)}
              stroke="#e2e8f0" strokeWidth="1" strokeDasharray={g === 0 ? "" : "3 4"}
            />
            <text x={CHART.l - 8} y={yRate(g) + 4} textAnchor="end" fontSize="10" fill="#94a3b8">
              {g}%
            </text>
          </g>
        ))}
        <XLabels pts={pts} y={CHART.H - 8} />
        <polyline
          points={pts.map((p, i) => `${xAt(i, pts.length).toFixed(1)},${yRate(p.wrong_rate).toFixed(1)}`).join(" ")}
          fill="none" stroke="#10b981" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
        />
        <circle cx={xAt(pts.length - 1, pts.length)} cy={yRate(last.wrong_rate)} r="4" fill="#10b981" />
        <text
          x={xAt(pts.length - 1, pts.length)} y={yRate(last.wrong_rate) - 8}
          textAnchor="end" fontSize="10" fill="#059669" fontWeight="600"
        >
          {last.wrong_rate.toFixed(0)}%
        </text>
        {pts.map((p, i) => {
          const h = p.conquered > 0 ? Math.max((p.conquered / maxConq) * barH, 3) : 0;
          return (
            <rect
              key={p.date}
              x={xAt(i, pts.length) - bw / 2}
              y={barTop + barH - h}
              width={bw}
              height={h || 1.5}
              rx="2"
              fill={p.conquered > 0 ? "#34d399" : "#e2e8f0"}
            >
              <title>
                {`${p.date} · 新增错题 ${p.new_wrong} · 攻克 ${p.conquered}\n累计 ${p.conquered_cum}/${p.wrong_total} 已掌握（${p.wrong_rate.toFixed(0)}%）`}
              </title>
            </rect>
          );
        })}
        <text x={CHART.l} y={CHART.t - 8} fontSize="10" fill="#64748b">
          每周攻克（最多 {maxConq} 题/周）
        </text>
      </svg>
      {trimmed > 0 && <TrimNote n={trimmed} />}
    </ChartCard>
  );
}

function TrimNote({ n }: { n: number }) {
  return (
    <div className="mt-1 text-[11px] text-slate-400">
      时间跨度较长，仅显示最近 {MAX_CHART_WEEKS} 周（更早 {n} 周未显示）。
    </div>
  );
}

/** 截断到最近 MAX_CHART_WEEKS 周 */
function useTrimmed(s: ReportSemester): { pts: ReportSeriesPoint[]; trimmed: number } {
  return useMemo(() => {
    const series = s.series ?? [];
    const pts = series.length > MAX_CHART_WEEKS ? series.slice(series.length - MAX_CHART_WEEKS) : series;
    return { pts, trimmed: series.length - pts.length };
  }, [s]);
}

/** 增量徽章（掌握度 / 复习次数较上期） */
function DeltaChip({ tone, text }: { tone: "up" | "down" | "flat"; text: string }) {
  const cls =
    tone === "up"
      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
      : tone === "down"
        ? "border-rose-200 bg-rose-50 text-rose-600"
        : "border-slate-200 bg-slate-50 text-slate-500";
  const Icon = tone === "up" ? TrendingUp : tone === "down" ? TrendingDown : Minus;
  return (
    <span className={`inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-[11px] font-medium ${cls}`}>
      <Icon size={12} />
      {text}
    </span>
  );
}

/** 学习总览：综合活跃度评分环 + 一句话结论 + 较上期增量 + 里程碑 */
function SummaryHero({ s }: { s: ReportSemester }) {
  const st = s.stats;
  const series = s.series ?? [];
  const last = series.length > 0 ? series[series.length - 1] : null;
  const prev = series.length > 1 ? series[series.length - 2] : null;
  const hasData = st.materials > 0 || st.reviews > 0 || st.wrong_total > 0 || st.tasks_total > 0;

  // 综合活跃度（0~100）：掌握度 40% + 错题攻克率 25% + 任务完成率 25% + 复习活跃 10%
  const score = hasData
    ? Math.round(
        st.mastery * 40 +
          (st.wrong_total > 0 ? st.wrong_rate * 0.25 : 0) +
          (st.tasks_total > 0 ? st.tasks_rate * 0.25 : 0) +
          Math.min(st.reviews / 80, 1) * 10,
      )
    : 0;

  const masteryDelta = last && prev ? last.mastery - prev.mastery : null;
  const reviewDelta = last && prev ? last.reviews - prev.reviews : null;

  const verdict = hasData
    ? score >= 80
      ? "状态极佳——掌握扎实、节奏在线，保持下去就是冲刺满分。"
      : score >= 60
        ? "稳步推进——基础正在成型，按复习计划走，别松劲。"
        : score >= 40
          ? "蓄力阶段——积累需要时间，每天多复习几份，曲线就会抬升。"
          : "打基础——掌握度还在爬坡期，先让复习与错题重练形成习惯。"
    : "还没有学习数据——从添加资料、开始复习起，这里会生成你的学习画像。";

  const R = 40;
  const C = 2 * Math.PI * R;
  const pct = Math.min(Math.max(score, 0), 100);

  return (
    <div className="relative overflow-hidden rounded-2xl border border-brand-100 bg-gradient-to-br from-brand-50/80 via-white to-violet-50/60 p-5 shadow-sm">
      <div className="flex flex-wrap items-center gap-5">
        {/* 综合评分环 */}
        <div className="relative h-24 w-24 shrink-0">
          <svg viewBox="0 0 100 100" className="h-24 w-24 -rotate-90">
            <defs>
              <linearGradient id="heroScoreGrad" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stopColor="#a5b4fc" />
                <stop offset="100%" stopColor="#6366f1" />
              </linearGradient>
            </defs>
            <circle cx="50" cy="50" r={R} fill="none" stroke="#e4e7fb" strokeWidth="9" />
            <circle
              cx="50" cy="50" r={R} fill="none" stroke="url(#heroScoreGrad)" strokeWidth="9"
              strokeLinecap="round" strokeDasharray={`${(C * pct) / 100} ${C}`}
            />
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-2xl font-extrabold leading-none text-brand-700">{score}</span>
            <span className="mt-0.5 text-[9px] text-slate-400">活跃度</span>
          </div>
        </div>

        {/* 结论 + 增量 */}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-sm font-semibold text-slate-800">
            <Sparkles size={15} className="text-brand-500" />
            学习总览
            <span className="rounded-md bg-brand-100 px-1.5 py-0.5 text-[10px] font-medium text-brand-700">
              {s.label}
            </span>
          </div>
          <p className="mt-1.5 text-sm leading-relaxed text-slate-600">{verdict}</p>
          {(masteryDelta !== null || reviewDelta !== null) && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {masteryDelta !== null &&
                (masteryDelta > 0.005 ? (
                  <DeltaChip tone="up" text={`掌握度较上期 +${(masteryDelta * 100).toFixed(1)}%`} />
                ) : masteryDelta < -0.005 ? (
                  <DeltaChip tone="down" text={`掌握度较上期 ${(masteryDelta * 100).toFixed(1)}%`} />
                ) : (
                  <DeltaChip tone="flat" text="掌握度与上期持平" />
                ))}
              {reviewDelta !== null &&
                (reviewDelta > 0 ? (
                  <DeltaChip tone="up" text={`本周复习 +${reviewDelta} 次`} />
                ) : reviewDelta < 0 ? (
                  <DeltaChip tone="down" text={`复习较上周少 ${-reviewDelta} 次`} />
                ) : (
                  <DeltaChip tone="flat" text="本周复习次数与上期持平" />
                ))}
            </div>
          )}
        </div>

        {/* 里程碑 */}
        <div className="grid shrink-0 grid-cols-3 gap-2">
          {[
            { label: "掌握资料", value: `${st.materials_mastered}/${st.materials}`, valueCls: "text-brand-600", bg: "bg-brand-50" },
            { label: "累计复习", value: `${st.reviews} 次`, valueCls: "text-violet-600", bg: "bg-violet-50" },
            { label: "错题攻克", value: st.wrong_total > 0 ? `${st.wrong_rate.toFixed(0)}%` : "—", valueCls: "text-emerald-600", bg: "bg-emerald-50" },
          ].map(({ label, value, valueCls, bg }) => (
            <div key={label} className={`min-w-[72px] rounded-xl border border-slate-100 px-3 py-2 text-center ${bg}`}>
              <div className={`text-base font-bold ${valueCls}`}>{value}</div>
              <div className="mt-0.5 text-[10px] text-slate-500">{label}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function ReportPage() {
  const { refreshKey, bump } = useApp();
  const [data, setData] = useState<Report | null>(null);
  const [loading, setLoading] = useState(false);
  const [error,setError]=useState("");
  const [scope, setScope] = useState<string>("all");

  useEffect(() => {
    setLoading(true);setError("");
    apiReport
      .get()
      .then(setData)
      .catch(cause=>setError(cause instanceof Error?cause.message:"报告读取失败"))
      .finally(() => setLoading(false));
  }, [refreshKey]);

  const view: ReportSemester | null = useMemo(() => {
    if (!data) return null;
    if (scope === "all") return data.overview;
    return data.semesters.find((s) => s.semester === scope) ?? data.overview;
  }, [data, scope]);

  const hasData =
    data !== null &&
    (data.semesters.length > 0 || data.overview.stats.courses > 0);

  return (
    <div className="space-y-6">
      {/* 标题 + 导出 */}
      <PageHeader
        title="学习报告 · 数据大图"
        desc="按学期汇总掌握度趋势、复习次数与错题攻克进度——一张图讲清你的学习成长"
        actions={
          data ? (
            <DownloadLink
              path={apiReport.mdUrl}
              fallbackName="studyos-report.md"
              className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-600 shadow-sm transition hover:border-brand-300 hover:text-brand-600"
            >
              <Download size={15} />
              导出 Markdown 报告
            </DownloadLink>
          ) : null
        }
      />

      {error && <div role="alert" className="rounded-xl bg-red-50 p-3 text-sm text-red-700">{error}<button onClick={bump} className="ml-3 underline">重试</button></div>}
      {data?.history_note && <p className="text-xs text-slate-500">{data.history_note}</p>}
      {loading && !data && (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      )}

      {data && !hasData ? (
        <div>
          <Empty text="还没有任何学习数据，报告无从谈起" />
          <div className="mt-4 flex flex-wrap justify-center gap-3 text-sm">
            <Link to="/materials" className="text-brand-600 hover:underline">
              去「资料库」添加资料 →
            </Link>
            <Link to="/wrongbook" className="text-brand-600 hover:underline">
              去「错题本」记录错题 →
            </Link>
            <Link to="/review" className="text-brand-600 hover:underline">
              去「复习计划」开始复习 →
            </Link>
          </div>
        </div>
      ) : data && hasData && view ? (
        <>
          {/* 学期切换 */}
          <div className="flex flex-wrap items-center gap-1.5">
            {[{ semester: "all", label: "全部学期" }, ...data.semesters].map((s) => (
              <button
                key={s.semester}
                onClick={() => setScope(s.semester)}
                className={`rounded-full px-3.5 py-1.5 text-xs font-medium transition ${
                  scope === s.semester
                    ? "bg-brand-600 text-white shadow-sm"
                    : "border border-slate-200 bg-white text-slate-500 hover:border-brand-300 hover:text-brand-600"
                }`}
              >
                {s.label}
              </button>
            ))}
            {view.span_from && (
              <span className="ml-auto text-[11px] text-slate-400">
                时间线 {mmdd(view.span_from)} ~ {mmdd(view.span_to)}
              </span>
            )}
          </div>

          {/* 学习总览（视觉锚点） */}
          <SummaryHero s={view} />

          {/* 指标卡片 */}
          <StatCards s={view} />

          {/* 三张图：掌握度趋势 + 复习次数 / 错题攻克率 */}
          <div className="grid gap-4 lg:grid-cols-2">
            <MasteryChart s={view} />
            <ReviewChart s={view} />
            <ConquerChart s={view} />
            <SemesterNote s={view} className="lg:col-span-2" />
          </div>

          {/* 学期对比表 */}
          <SemesterTable data={data} />
        </>
      ) : null}
    </div>
  );
}

function StatCards({ s }: { s: ReportSemester }) {
  const st = s.stats;
  const cards = [
    {
      label: "平均掌握度",
      value: `${Math.round(st.mastery * 100)}%`,
      sub: `全 ${st.materials} 份资料口径`,
      icon: TrendingUp,
      grad: "from-indigo-500 to-brand-600",
      bar: st.mastery,
      barCls: "bg-gradient-to-r from-indigo-500 to-brand-600",
    },
    {
      label: "复习总次数",
      value: String(st.reviews),
      sub: "间隔重复评分次数",
      icon: RotateCcw,
      grad: "from-violet-500 to-purple-600",
      bar: Math.min(st.reviews / 50, 1),
      barCls: "bg-gradient-to-r from-violet-500 to-purple-600",
    },
    {
      label: "错题攻克率",
      value: st.wrong_total > 0 ? `${st.wrong_rate.toFixed(0)}%` : "—",
      sub: `已攻克 ${st.wrong_done}/${st.wrong_total}`,
      icon: Target,
      grad: "from-emerald-500 to-teal-600",
      bar: st.wrong_rate / 100,
      barCls: "bg-gradient-to-r from-emerald-500 to-teal-600",
    },
    {
      label: "已掌握资料",
      value: `${st.materials_mastered}/${st.materials}`,
      sub: `另 ${st.materials_reviewed - st.materials_mastered} 份复习中`,
      icon: CheckCircle2,
      grad: "from-sky-500 to-blue-600",
      bar: st.materials > 0 ? st.materials_mastered / st.materials : 0,
      barCls: "bg-gradient-to-r from-sky-500 to-blue-600",
    },
    {
      label: "任务完成",
      value: st.tasks_total > 0 ? `${st.tasks_rate.toFixed(0)}%` : "—",
      sub: `完成 ${st.tasks_done}/${st.tasks_total} 个 DDL`,
      icon: History,
      grad: "from-amber-500 to-orange-500",
      bar: st.tasks_rate / 100,
      barCls: "bg-gradient-to-r from-amber-500 to-orange-500",
    },
    {
      label: "学期课程",
      value: `${st.courses} 门`,
      sub: s.course_names.slice(0, 3).join("、") + (s.course_names.length > 3 ? "…" : "") || "本学期课程",
      icon: GraduationCap,
      grad: "from-rose-500 to-pink-600",
      bar: 0,
      barCls: "",
    },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      {cards.map(({ label, value, sub, icon: Icon, grad, bar, barCls }) => (
        <div
          key={label}
          className="group relative overflow-hidden rounded-2xl border border-slate-200 bg-white p-4 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md"
        >
          <div
            className={`mb-3 flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br ${grad} text-white shadow-sm`}
          >
            <Icon size={16} />
          </div>
          <div className="text-[22px] font-bold leading-none text-slate-800">{value}</div>
          <div className="mt-1 text-[11px] font-medium text-slate-500">{label}</div>
          <div className="mt-0.5 truncate text-[10px] text-slate-400" title={sub}>
            {sub}
          </div>
          {bar > 0 && (
            <div className="mt-3 h-1 overflow-hidden rounded-full bg-slate-100">
              <div
                className={`h-full rounded-full ${barCls}`}
                style={{ width: `${Math.min(bar * 100, 100)}%` }}
              />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

/** 口径说明卡片（占满第三个图旁边的空位） */
function SemesterNote({ s, className = "" }: { s: ReportSemester; className?: string }) {
  const st = s.stats;
  const lines: string[] = [];
  if (st.materials > 0) {
    lines.push(
      `掌握度 = 本学期全部 ${st.materials} 份资料的平均掌握程度（未复习按 0 计），逐周重放评分历史得到趋势。`,
    );
  }
  if (st.reviews > 0) {
    lines.push(`复习次数按「复习计划」页的间隔重复评分记录统计（每份资料保留最近 20 条历史）。`);
  }
  if (st.wrong_total > 0) {
    lines.push(
      `错题攻克率 = 累计「已掌握」错题 ÷ 错题总数；攻克时刻取该错题最近一次自评时间，连续答对 2 次即视为攻克。`,
    );
  }
  if (lines.length === 0) {
    lines.push("开始学习后，这里会解释每个指标的计算口径。");
  }
  return (
    <div className={`rounded-2xl border border-slate-200 bg-white p-5 shadow-sm ${className}`}>
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
        <LineChart size={16} className="text-brand-500" />
        指标口径
      </div>
      <ul className="space-y-2 text-xs leading-relaxed text-slate-500">
        {lines.map((l, i) => (
          <li key={i} className="flex gap-2">
            <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-brand-400" />
            {l}
          </li>
        ))}
      </ul>
      <div className="mt-3 border-t border-slate-100 pt-3 text-[11px] text-slate-400">
        数据随每次评分 / 错题自评实时更新；「导出 Markdown 报告」可把同一份数据带走。
      </div>
    </div>
  );
}

/** 学期对比表：跨学期一张总图 */
function SemesterTable({ data }: { data: Report }) {
  const rows = [
    { sem: data.overview, isAll: true },
    ...data.semesters.map((s) => ({ sem: s, isAll: false })),
  ];
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
        <BarChart3 size={16} className="text-brand-500" />
        学期对比
        <span className="text-xs font-normal text-slate-400">（跨学期一览）</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50/70 text-left text-[11px] text-slate-500">
              <th className="rounded-tl-lg py-2.5 pl-3 pr-3 font-medium">学期</th>
              <th className="py-2.5 pr-3 font-medium">课程</th>
              <th className="py-2.5 pr-3 font-medium">资料（已掌握）</th>
              <th className="py-2.5 pr-3 font-medium">复习次数</th>
              <th className="py-2.5 pr-3 font-medium">平均掌握度</th>
              <th className="py-2.5 pr-3 font-medium">错题攻克</th>
              <th className="rounded-tr-lg py-2.5 pr-3 font-medium">任务完成</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ sem: s, isAll }) => {
              const st = s.stats;
              return (
                <tr
                  key={s.label}
                  className={`border-b border-slate-100 transition last:border-0 hover:bg-brand-50/40 ${
                    isAll ? "bg-brand-50/50 font-semibold text-slate-800" : "text-slate-600"
                  }`}
                >
                  <td className="py-3 pl-3 pr-3">
                    <div className="flex items-center gap-2">
                      {isAll && (
                        <span className="rounded-md bg-brand-600 px-1.5 py-0.5 text-[10px] font-medium text-white">
                          汇总
                        </span>
                      )}
                      {s.label}
                    </div>
                    {!isAll && s.course_names.length > 0 && (
                      <div className="mt-0.5 font-normal text-slate-400">
                        {s.course_names.slice(0, 2).join("、")}
                        {s.course_names.length > 2 ? "…" : ""}
                      </div>
                    )}
                  </td>
                  <td className="py-3 pr-3">{st.courses}</td>
                  <td className="py-3 pr-3">
                    {st.materials}
                    <span className="text-slate-400">（{st.materials_mastered}）</span>
                  </td>
                  <td className="py-3 pr-3">{st.reviews}</td>
                  <td className="py-3 pr-3">
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-100">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-brand-600"
                          style={{ width: `${Math.round(st.mastery * 100)}%` }}
                        />
                      </div>
                      <span className="text-xs">{Math.round(st.mastery * 100)}%</span>
                    </div>
                  </td>
                  <td className="py-3 pr-3">
                    {st.wrong_total > 0 ? (
                      <span>
                        {st.wrong_done}/{st.wrong_total}
                        <span className="ml-1 text-xs text-slate-400">({st.wrong_rate.toFixed(0)}%)</span>
                      </span>
                    ) : (
                      <span className="text-slate-300">—</span>
                    )}
                  </td>
                  <td className="py-3 pr-3">
                    {st.tasks_total > 0 ? (
                      <span>
                        {st.tasks_done}/{st.tasks_total}
                        <span className="ml-1 text-xs text-slate-400">({st.tasks_rate.toFixed(0)}%)</span>
                      </span>
                    ) : (
                      <span className="text-slate-300">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
