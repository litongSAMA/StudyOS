import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AlarmClock, ArrowRight, Flame, Hourglass, Timer } from "lucide-react";
import { apiTasks } from "../api";
import { useApp } from "../App";
import type { Task } from "../types";
import { KIND_COLORS, TASK_KINDS } from "../types";
import { countdown, fmtDT } from "../utils";
import { Empty } from "../components/ui";

const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];

/** 考试类任务：kind=exam，或标题命中考试/期末/期中/答辩/验收 */
const isExam = (t: Task) =>
  t.kind === "exam" || /考试|期末|期中|答辩|验收/.test(t.title);

interface TimelineGroup {
  key: string;
  label: string;
  sub: string;
  isToday: boolean;
  items: Task[];
}

function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

function groupByDate(tasks: Task[]): TimelineGroup[] {
  const today = new Date();
  const tomorrow = new Date(today.getTime() + 86_400_000);
  const groups: TimelineGroup[] = [];
  for (const t of tasks) {
    const d = new Date(t.due_at);
    const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
    let g = groups.find((x) => x.key === key);
    if (!g) {
      const isToday = sameDay(d, today);
      const isTomorrow = sameDay(d, tomorrow);
      g = {
        key,
        label: `${d.getMonth() + 1}月${d.getDate()}日`,
        sub: isToday ? "今天" : isTomorrow ? "明天" : `周${WEEKDAYS[d.getDay()]}`,
        isToday,
        items: [],
      };
      groups.push(g);
    }
    g.items.push(t);
  }
  return groups;
}

function dotCls(t: Task, now: number): string {
  const diff = new Date(t.due_at).getTime() - now;
  if (isExam(t)) return "bg-red-500";
  if (diff < 86_400_000) return "bg-red-400";
  if (diff <= 3 * 86_400_000) return "bg-amber-400";
  return "bg-brand-400";
}

const pad = (n: number) => String(n).padStart(2, "0");

export default function Countdown() {
  const { refreshKey } = useApp();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [onlyExam, setOnlyExam] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    apiTasks.list({ done: false }).then(setTasks).catch(console.error);
  }, [refreshKey]);

  // 秒级 tick：大号倒计时实时跳动
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const { upcoming, overdue } = useMemo(() => {
    const up: Task[] = [];
    const od: Task[] = [];
    for (const t of tasks) {
      if (new Date(t.due_at).getTime() >= now) up.push(t);
      else od.push(t);
    }
    up.sort((a, b) => new Date(a.due_at).getTime() - new Date(b.due_at).getTime());
    od.sort((a, b) => new Date(b.due_at).getTime() - new Date(a.due_at).getTime());
    return { upcoming: up, overdue: od };
    // now 变化时重新切分「未来/逾期」
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tasks, now]);

  const nearestExam = useMemo(() => upcoming.find(isExam) ?? upcoming[0], [upcoming]);
  const visible = onlyExam ? upcoming.filter(isExam) : upcoming;
  const groups = useMemo(() => groupByDate(visible), [visible]);

  const today = new Date(now);
  const todayLabel = `${today.getMonth() + 1}月${today.getDate()}日 周${WEEKDAYS[today.getDay()]}`;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-800">考试倒计时</h1>
          <p className="mt-1 text-sm text-slate-500">
            按时间轴看每场考试 / DDL，倒计时一目了然
          </p>
        </div>
      </div>

      {/* 距离最近考试的大号倒计时 */}
      {nearestExam ? (
        <div className="rounded-2xl bg-gradient-to-br from-brand-600 to-indigo-600 p-5 text-white shadow-sm">
          <div className="flex items-center gap-2 text-sm font-medium text-white/85">
            <Flame size={16} />
            {isExam(nearestExam) ? "距离最近考试" : "下一个截止"}
          </div>
          <div className="mt-1 truncate text-lg font-semibold">{nearestExam.title}</div>
          <div className="mt-4 flex flex-wrap items-end gap-1.5 font-mono tabular-nums">
            <HeroCountdown dueIso={nearestExam.due_at} now={now} />
          </div>
          <div className="mt-3 text-xs text-white/80">
            {fmtDT(nearestExam.due_at)}
            {nearestExam.course_name ? ` · ${nearestExam.course_name}` : ""}
          </div>
        </div>
      ) : (
        <Empty text="还没有未完成的 DDL。先去「智能入库」或「DDL 任务」添加一个吧。" />
      )}

      {overdue.length > 0 && (
        <Link
          to="/tasks"
          className="flex items-center justify-between rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-600 transition hover:border-red-200"
        >
          <span className="flex items-center gap-2">
            <AlarmClock size={15} />
            有 {overdue.length} 个已逾期任务，点击去处理
          </span>
          <ArrowRight size={15} />
        </Link>
      )}

      {/* 过滤切换 */}
      <div className="flex items-center gap-2">
        <div className="flex rounded-xl bg-slate-100 p-1">
          {[
            { key: false, label: "全部 DDL" },
            { key: true, label: "只看考试" },
          ].map((f) => (
            <button
              key={String(f.key)}
              onClick={() => setOnlyExam(f.key)}
              className={`rounded-lg px-4 py-1.5 text-sm font-medium transition ${
                onlyExam === f.key ? "bg-white text-slate-800 shadow-sm" : "text-slate-500"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <span className="ml-auto text-xs text-slate-400">
          {visible.length} 个未完成
        </span>
      </div>

      {/* 时间轴 */}
      {visible.length === 0 ? (
        <Empty
          text={
            onlyExam
              ? "没有未完成的考试类 DDL。可在「DDL 任务」里添加期末考试。"
              : "暂无未完成 DDL。"
          }
        />
      ) : (
        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="relative">
            {/* 竖向主线 */}
            <div className="absolute bottom-2 left-2 top-2 w-px bg-slate-200" aria-hidden />

            <div className="space-y-1">
              {/* 今天标记 */}
              <div className="relative flex items-center gap-3">
                <div className="flex w-4 justify-center">
                  <span className="z-10 mt-0.5 h-2.5 w-2.5 rounded-full bg-brand-500 ring-4 ring-white" />
                </div>
                <span className="text-xs font-semibold text-brand-600">今天 · {todayLabel}</span>
              </div>

              {groups.map((g) => (
                <div key={g.key}>
                  {/* 日期节点 */}
                  <div className="relative flex items-center gap-3 pt-2">
                    <div className="flex w-4 justify-center">
                      <span
                        className={`z-10 h-3 w-3 rounded-full ring-4 ring-white ${
                          g.isToday ? "bg-brand-500" : "bg-slate-300"
                        }`}
                      />
                    </div>
                    <div className="text-sm font-semibold text-slate-700">
                      {g.label}{" "}
                      <span className="font-normal text-slate-400">· {g.sub}</span>
                    </div>
                  </div>

                  {/* 该日任务 */}
                  {g.items.map((t) => {
                    const c = countdown(t.due_at, now);
                    return (
                      <div key={t.id} className="relative flex gap-3 py-1.5">
                        <div className="flex w-4 justify-center">
                          <span
                            className={`z-10 mt-4 h-2.5 w-2.5 rounded-full ring-4 ring-white ${dotCls(t, now)}`}
                          />
                        </div>
                        <div className="flex-1 rounded-xl border border-slate-200 bg-white p-3.5 shadow-sm transition hover:border-brand-300">
                          <div className="flex items-center gap-2">
                            <span
                              className={`rounded-md px-2 py-0.5 text-[10px] font-medium ${KIND_COLORS[t.kind] ?? "bg-slate-100 text-slate-600"}`}
                            >
                              {TASK_KINDS[t.kind] ?? t.kind}
                            </span>
                            {isExam(t) && (
                              <span className="inline-flex items-center gap-1 rounded-md bg-red-50 px-1.5 py-0.5 text-[10px] font-medium text-red-600">
                                <Timer size={10} /> 考试倒计时
                              </span>
                            )}
                            <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-700">
                              {t.title}
                            </span>
                          </div>
                          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                            {t.course_name && (
                              <span className="text-slate-400">{t.course_name}</span>
                            )}
                            <span className="text-slate-400">{fmtDT(t.due_at)}</span>
                            <span className={`font-semibold tabular-nums ${c.cls}`}>{c.text}</span>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      <div className="flex items-center gap-1.5 text-xs text-slate-400">
        <Hourglass size={13} />
        提示：在「复习计划」页选中考试，可一键按倒计时生成每日复习清单
        <Link to="/review" className="font-medium text-brand-600 hover:underline">
          去生成 →
        </Link>
      </div>
    </div>
  );
}

function HeroCountdown({ dueIso, now }: { dueIso: string; now: number }) {
  const diff = new Date(dueIso).getTime() - now;
  const abs = Math.abs(diff);
  const d = Math.floor(abs / 86_400_000);
  const h = Math.floor((abs % 86_400_000) / 3_600_000);
  const m = Math.floor((abs % 3_600_000) / 60_000);
  const s = Math.floor((abs % 60_000) / 1000);
  const overdue = diff < 0;
  const parts: [string, string][] = [
    [String(d), "天"],
    [pad(h), "时"],
    [pad(m), "分"],
    [pad(s), "秒"],
  ];
  return (
    <>
      <span className="mr-1 self-end text-sm text-white/85">{overdue ? "已逾期" : "剩"}</span>
      {parts.map(([v, u]) => (
        <span key={u} className="flex items-baseline gap-1">
          <span className="text-3xl font-bold">{v}</span>
          <span className="text-sm text-white/85">{u}</span>
        </span>
      ))}
    </>
  );
}
