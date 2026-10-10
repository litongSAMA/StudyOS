import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  AlarmClock,
  CalendarDays,
  Coffee,
  Flame,
  ListTodo,
  Pause,
  Play,
  RotateCcw,
  Settings2,
  SkipForward,
  TimerReset,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { apiCourses, apiFocus, apiReview, apiTasks } from "../api";
import type { FocusSessionIn } from "../api";
import type { Course, FocusSession, FocusStats, ReviewPlan, ReviewSession, Task } from "../types";
import { RATINGS } from "../types";
import FocusSettingsDialog from "../components/FocusSettingsDialog";
import { PageHeader } from "../components/ui";
import { loadSettings, SETTINGS_KEY } from "../focusSettings";
import type { FocusSettings as Settings } from "../focusSettings";

/* ------------------------------------------------------------------ */
/* 类型与常量                                                          */
/* ------------------------------------------------------------------ */

type Mode = "focus" | "short" | "long";
type BindKind = "free" | "task" | "plan";

interface ModeTheme {
  label: string;
  /** 主色（SVG 环 / 内联样式） */
  hex: string;
  hexSoft: string;
  /** Tailwind 静态类（不能动态拼接，全部写全） */
  segActive: string;
  btn: string;
  glow: string;
  text: string;
  chipBg: string;
  bar: string;
}

const MODE_THEME: Record<Mode, ModeTheme> = {
  focus: {
    label: "专注",
    hex: "#6366f1",
    hexSoft: "#a5b4fc",
    segActive: "bg-brand-600 text-white shadow-sm",
    btn: "bg-brand-600 hover:bg-brand-700 shadow-lg shadow-brand-500/30",
    glow: "bg-brand-400/30",
    text: "text-brand-600",
    chipBg: "bg-brand-50 text-brand-700",
    bar: "bg-brand-500",
  },
  short: {
    label: "短休息",
    hex: "#10b981",
    hexSoft: "#6ee7b7",
    segActive: "bg-emerald-500 text-white shadow-sm",
    btn: "bg-emerald-500 hover:bg-emerald-600 shadow-lg shadow-emerald-500/30",
    glow: "bg-emerald-400/30",
    text: "text-emerald-600",
    chipBg: "bg-emerald-50 text-emerald-700",
    bar: "bg-emerald-500",
  },
  long: {
    label: "长休息",
    hex: "#0ea5e9",
    hexSoft: "#7dd3fc",
    segActive: "bg-sky-500 text-white shadow-sm",
    btn: "bg-sky-500 hover:bg-sky-600 shadow-lg shadow-sky-500/30",
    glow: "bg-sky-400/30",
    text: "text-sky-600",
    chipBg: "bg-sky-50 text-sky-700",
    bar: "bg-sky-500",
  },
};

const MODE_ORDER: Mode[] = ["focus", "short", "long"];
const MODE_LABEL: Record<Mode, string> = { focus: "专注", short: "短休息", long: "长休息" };
const STATS_KEY = "studyos.pomodoro.stats.v1";

/** 旧版本地统计（迁移源）：{日期: {count, minutes}} */
type LegacyMap = Record<string, { count: number; minutes: number }>;

const pad = (n: number) => String(n).padStart(2, "0");
const dateKey = (d: Date) =>
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const fmtHM = (iso: string) => {
  const d = new Date(iso);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

interface PlanOption {
  planId: number;
  date: string;
  course: string;
  scope: string;
  hours: number;
  examDate: string;
  materialIds: number[];
  materials: string[];
}

/* ------------------------------------------------------------------ */
/* 组件                                                                */
/* ------------------------------------------------------------------ */

export default function Focus() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [settings, setSettings] = useState<Settings>(loadSettings);
  const [mode, setMode] = useState<Mode>("focus");
  const [remaining, setRemaining] = useState(settings.focus * 60);
  const [running, setRunning] = useState(false);
  const [sessionStarted, setSessionStarted] = useState(false);
  /** 距离长休息还差几个专注（1..longEvery） */
  const [cyclePos, setCyclePos] = useState(0);
  /** 本次专注期间「记录打断」次数（番茄工作法：被打断应重置） */
  const [interrupts, setInterrupts] = useState(0);
  const [showSettings, setShowSettings] = useState(false);

  /* ---- 绑定：自由 / DDL 任务 / 复习计划复习块 ---- */
  const [bindKind, setBindKind] = useState<BindKind>("free");
  const [taskId, setTaskId] = useState<number | "">("");
  const [planSel, setPlanSel] = useState<{ planId: number; date: string } | null>(null);
  const [onlyToday, setOnlyToday] = useState(true);

  /* ---- 数据 ---- */
  const [tasks, setTasks] = useState<Task[]>([]);
  const [plans, setPlans] = useState<ReviewPlan[]>([]);
  const [courses, setCourses] = useState<Course[]>([]);
  const [stats, setStats] = useState<FocusStats | null>(null);
  const [sessions, setSessions] = useState<FocusSession[]>([]);
  /** 上一个完成的专注（带资料时提示评分，喂回间隔重复） */
  const [ratingPanel, setRatingPanel] = useState<{
    sessionId: number;
    materials: { id: number; title: string }[];
  } | null>(null);
  const [rated, setRated] = useState<Record<number, string>>({});
  /** 旧版本地统计待迁移 */
  const [legacyDays, setLegacyDays] = useState<{ date: string; count: number; minutes: number }[] | null>(null);
  const [importedMsg, setImportedMsg] = useState("");

  const endAtRef = useRef<number | null>(null);
  const startAtRef = useRef<number | null>(null);
  /** 本阶段开始时的计划分钟（设置中途修改不影响本阶段口径） */
  const plannedMinRef = useRef<number>(settings.focus);
  const timerRef = useRef<number | null>(null);
  const audioRef = useRef<AudioContext | null>(null);
  /** 始终指向最新一帧的完成回调，避免 interval 闭包拿到旧 mode/设置 */
  const completeRef = useRef<() => void>(() => {});
  /** 上传失败的记录：下次成功时重试 */
  const pendingRef = useRef<FocusSessionIn[]>([]);

  const theme = MODE_THEME[mode];
  const total = plannedMinRef.current * 60;

  /* ---------------- 持久化设置 ---------------- */
  useEffect(() => {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    } catch {
      /* ignore */
    }
  }, [settings]);

  /* ---------------- 数据加载 ---------------- */
  const refresh = useCallback(() => {
    apiFocus.stats(7).then(setStats).catch(console.error);
    apiFocus
      .list({ limit: 100 })
      .then(({ items }) => setSessions(items))
      .catch(console.error);
  }, []);

  useEffect(() => {
    apiTasks.list({ done: false }).then((ts) => setTasks(ts.slice(0, 50))).catch(console.error);
    apiReview.list().then(setPlans).catch(console.error);
    apiCourses.list().then(setCourses).catch(console.error);
    refresh();
  }, [refresh]);

  /* ---------------- URL 参数：从复习计划页 / 任务页直达绑定 ---------------- */
  useEffect(() => {
    const planId = Number(searchParams.get("plan") || 0);
    const date = searchParams.get("date") || "";
    const task = Number(searchParams.get("task") || 0);
    let changed = false;
    if (planId && date) {
      setBindKind("plan");
      setPlanSel({ planId, date });
      setOnlyToday(false);
      changed = true;
    } else if (task) {
      setBindKind("task");
      setTaskId(task);
      changed = true;
    }
    if (changed) setSearchParams({}, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* ---------------- 旧版本地统计检测（仅当服务端还没有任何记录） ---------------- */
  useEffect(() => {
    try {
      const raw = localStorage.getItem(STATS_KEY);
      if (!raw) return;
      const old = JSON.parse(raw) as LegacyMap;
      const days = Object.entries(old)
        .filter(([, v]) => v && v.count > 0)
        .map(([date, v]) => ({ date, count: v.count, minutes: v.minutes }));
      if (days.length) setLegacyDays(days);
    } catch {
      /* ignore */
    }
  }, []);

  /* ---------------- 提示音（WebAudio，无外部资源） ---------------- */
  const chime = useCallback(() => {
    if (!settings.sound) return;
    try {
      const Ctx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      if (!Ctx) return;
      if (!audioRef.current) audioRef.current = new Ctx();
      const ctx = audioRef.current;
      if (ctx.state === "suspended") void ctx.resume();
      const notes = mode === "focus" ? [523.25, 659.25, 783.99] : [783.99, 659.25];
      notes.forEach((freq, i) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.value = freq;
        const t0 = ctx.currentTime + i * 0.16;
        gain.gain.setValueAtTime(0.0001, t0);
        gain.gain.exponentialRampToValueAtTime(0.18, t0 + 0.03);
        gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.32);
        osc.connect(gain).connect(ctx.destination);
        osc.start(t0);
        osc.stop(t0 + 0.34);
      });
    } catch {
      /* 音频不可用时静默 */
    }
  }, [mode, settings.sound]);

  /* ---------------- 会话记录（后端持久化 + 失败重试） ---------------- */
  const recordSession = useCallback(async (body: FocusSessionIn) => {
    try {
      const s = await apiFocus.create(body);
      // 顺带补传之前的失败记录
      while (pendingRef.current.length) {
        const prev = pendingRef.current.shift()!;
        try {
          await apiFocus.create(prev);
        } catch {
          pendingRef.current.unshift(prev);
          break;
        }
      }
      refresh();
      return s;
    } catch (e) {
      pendingRef.current.push(body);
      console.error("专注记录上传失败，稍后自动重试", e);
      return null;
    }
  }, [refresh]);

  /* ---------------- 计时核心：基于结束时间戳，切后台也准确 ---------------- */
  const tick = useCallback(() => {
    if (endAtRef.current == null) return;
    const left = Math.max(0, Math.ceil((endAtRef.current - Date.now()) / 1000));
    setRemaining(left);
    if (left <= 0) completeRef.current();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (running) {
      timerRef.current = window.setInterval(tick, 250);
    } else if (timerRef.current) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
  }, [running, tick]);

  /* ---------------- 复习块查找（当前选中绑定） ---------------- */
  const findPlanSession = useCallback(
    (sel: { planId: number; date: string } | null): ReviewSession | undefined => {
      if (!sel) return undefined;
      for (const p of plans) {
        if (p.id !== sel.planId) continue;
        return p.sessions.find((s) => s.date === sel.date);
      }
      return undefined;
    },
    [plans],
  );
  const currentPlanSession = findPlanSession(planSel);

  /** 当前绑定 → 会话落库字段（任务 / 复习块 / 自由） */
  const bindBody = useCallback((): Partial<FocusSessionIn> => {
    if (bindKind === "task" && taskId !== "") {
      const t = tasks.find((x) => x.id === taskId);
      return { task_id: Number(taskId), course_id: t?.course_id ?? null };
    }
    if (bindKind === "plan" && currentPlanSession) {
      const cid = courses.find((c) => c.name === currentPlanSession.course)?.id ?? null;
      return {
        plan_id: planSel!.planId,
        plan_date: currentPlanSession.date,
        course_id: cid,
        material_ids: currentPlanSession.material_ids ?? [],
      };
    }
    return {};
  }, [bindKind, taskId, tasks, currentPlanSession, courses, planSel]);

  const boundLabel = useMemo(() => {
    if (bindKind === "task") {
      const t = tasks.find((x) => x.id === taskId);
      return t ? `任务 · ${t.title}` : "任务（未选中）";
    }
    if (bindKind === "plan") {
      if (!currentPlanSession) return "复习计划 · 复习块";
      return `复习 · ${currentPlanSession.course}（${currentPlanSession.date.slice(5)}）`;
    }
    return "";
  }, [bindKind, taskId, tasks, currentPlanSession]);

  /* ---------------- 模式切换 / 阶段完成 ---------------- */
  const gotoMode = useCallback(
    (next: Mode, autoRun = false) => {
      setMode(next);
      const mins = next === "focus" ? settings.focus : next === "short" ? settings.short : settings.long;
      plannedMinRef.current = mins;
      setRemaining(mins * 60);
      endAtRef.current = null;
      startAtRef.current = null;
      setRunning(autoRun);
      setSessionStarted(autoRun);
      if (autoRun) {
        endAtRef.current = Date.now() + mins * 60_000;
        startAtRef.current = Date.now();
      }
    },
    [settings],
  );

  /** 当前阶段自然结束 */
  const completeSession = useCallback(() => {
    chime();
    const mins = plannedMinRef.current;
    const startedAt = new Date(startAtRef.current ?? Date.now() - mins * 60_000).toISOString();
    if (mode === "focus") {
      if (settings.notify && "Notification" in window && Notification.permission === "granted") {
        try {
          new Notification("专注结束 🍅", {
            body: `已完成 ${mins} 分钟专注，起来活动一下、喝口水吧`,
          });
        } catch {
          /* ignore */
        }
      }
      void recordSession({
        mode: "focus",
        planned_min: mins,
        duration_min: mins,
        completed: true,
        interrupt_count: interrupts,
        started_at: startedAt,
        ...bindBody(),
      }).then((s) => {
        if (s && s.material_ids.length && currentPlanSession) {
          const names = currentPlanSession.materials ?? [];
          setRatingPanel({
            sessionId: s.id,
            materials: s.material_ids.map((id, i) => ({
              id,
              title: names[i] || `资料 #${id}`,
            })),
          });
          setRated({});
        }
      });
      setInterrupts(0);
      const nextPos = cyclePos + 1;
      const isLong = nextPos % settings.longEvery === 0;
      setCyclePos(nextPos);
      gotoMode(isLong ? "long" : "short", settings.autoStartBreak);
    } else {
      // 休息自然结束 → 记录进时间线（不计入专注统计）
      void recordSession({
        mode,
        planned_min: mins,
        duration_min: mins,
        completed: true,
        started_at: startedAt,
      });
      gotoMode("focus", settings.autoStartFocus);
    }
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, cyclePos, settings, interrupts, chime, gotoMode, recordSession, bindBody, currentPlanSession, refresh]);
  // 每次渲染后同步最新完成回调，供稳定的 tick 调用
  useEffect(() => {
    completeRef.current = completeSession;
  });

  /* ---------------- 控制按钮 ---------------- */
  const toggle = () => {
    if (running) {
      // 暂停：冻结剩余
      if (endAtRef.current != null) setRemaining(Math.max(0, Math.ceil((endAtRef.current - Date.now()) / 1000)));
      endAtRef.current = null;
      setRunning(false);
    } else {
      if (startAtRef.current == null) startAtRef.current = Date.now();
      setSessionStarted(true);
      endAtRef.current = Date.now() + remaining * 1000;
      setRunning(true);
    }
  };
  const reset = () => {
    endAtRef.current = null;
    startAtRef.current = null;
    setSessionStarted(false);
    setRunning(false);
    plannedMinRef.current = settings[mode];
    setRemaining(settings[mode] * 60);
    setInterrupts(0);
  };
  /** 结束本阶段：专注=放弃（记录未完成），休息=跳过（不记录） */
  const endPhase = () => {
    if (mode === "focus") {
      const mins = plannedMinRef.current;
      const elapsedMin = (mins * 60 - remaining) / 60;
      if (elapsedMin >= 0.5 && startAtRef.current != null) {
        void recordSession({
          mode: "focus",
          planned_min: mins,
          duration_min: Math.min(elapsedMin, mins),
          completed: false,
          interrupt_count: interrupts,
          started_at: new Date(startAtRef.current).toISOString(),
          ...bindBody(),
        });
      }
      setInterrupts(0);
      gotoMode("short", false);
      refresh();
    } else {
      gotoMode("focus", false);
    }
  };
  const pickMode = (m: Mode) => {
    if (m === mode) return;
    endAtRef.current = null;
    setInterrupts(0);
    gotoMode(m);
  };

  /* 调整时长设置时，若当前模式未在运行则同步剩余时间 */
  useEffect(() => {
    if (startAtRef.current == null) {
      const mins = settings[mode];
      plannedMinRef.current = mins;
      setRemaining(mins * 60);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings.focus, settings.short, settings.long]);

  /* ---------------- 标签页标题同步倒计时 ---------------- */
  const mmss = `${pad(Math.floor(remaining / 60))}:${pad(remaining % 60)}`;
  useEffect(() => {
    const prev = document.title;
    document.title = running
      ? `${mmss} · ${theme.label}中 · StudyOS`
      : "番茄钟 · StudyOS";
    return () => {
      document.title = prev;
    };
  }, [mmss, running, theme.label]);

  /* ---------------- 统计派生 ---------------- */
  const todayKey = dateKey(new Date());
  const todayStat = stats?.today ?? { date: todayKey, count: 0, minutes: 0 };
  const week = stats?.week ?? [];
  const weekMax = Math.max(1, ...week.map((d) => d.count));
  const courseMax = Math.max(1, ...(stats?.by_course ?? []).map((c) => c.minutes));

  /* ---------------- 复习块候选（今天在前，其次未来，其次过去） ---------------- */
  const planOptions: PlanOption[] = useMemo(() => {
    const out: PlanOption[] = [];
    for (const p of plans) {
      for (const s of p.sessions) {
        out.push({
          planId: p.id,
          date: s.date,
          course: s.course,
          scope: s.scope,
          hours: s.hours,
          examDate: s.exam_date,
          materialIds: s.material_ids ?? [],
          materials: s.materials ?? [],
        });
      }
    }
    const rank = (d: string) => (d === todayKey ? 0 : d > todayKey ? 1 : 2);
    out.sort((a, b) => rank(a.date) - rank(b.date) || (a.date < b.date ? -1 : 1));
    return out;
  }, [plans, todayKey]);
  const visiblePlanOptions = onlyToday
    ? planOptions.filter((o) => o.date === todayKey)
    : planOptions;

  /* ---------------- 环形进度几何 ---------------- */
  const SIZE = 288;
  const STROKE = 16;
  const R = (SIZE - STROKE) / 2;
  const C = 2 * Math.PI * R;
  const fraction = total > 0 ? remaining / total : 0;
  const dashOffset = C * (1 - fraction);

  /* ---------------- 操作：评分 / 删除 / 迁移 ---------------- */
  const rateMaterial = async (materialId: number, rating: string) => {
    try {
      await apiReview.rate(materialId, rating);
      setRated((prev) => ({ ...prev, [materialId]: rating }));
    } catch (e) {
      alert((e as Error).message);
    }
  };

  const removeSession = async (id: number) => {
    try {
      await apiFocus.remove(id);
      refresh();
    } catch (e) {
      alert((e as Error).message);
    }
  };

  const importLegacy = async () => {
    if (!legacyDays) return;
    try {
      const r = await apiFocus.importLegacy(legacyDays);
      localStorage.removeItem(STATS_KEY);
      setLegacyDays(null);
      setImportedMsg(`已导入 ${r.imported} 条历史专注记录`);
      refresh();
    } catch (e) {
      alert((e as Error).message);
    }
  };

  const saveSettings = (next: Settings) => {
    setSettings(next);
    setShowSettings(false);
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={
          <>
            <TimerReset size={20} className="text-brand-500" />
            番茄钟 · 专注计时
          </>
        }
        desc="自定义专注节奏，绑定任务或复习计划，完成后记录学习进度。"
        actions={
          <button
            onClick={() => setShowSettings(true)}
            className={`inline-flex shrink-0 items-center gap-1.5 rounded-xl border px-3 py-2 text-xs font-medium transition ${
              showSettings
                ? "border-brand-300 bg-brand-50 text-brand-700"
                : "border-slate-200 bg-white text-slate-600 hover:border-brand-300"
            }`}
          >
            <Settings2 size={14} />
            自定义
          </button>
        }
      />

      {/* 旧版本地统计迁移 */}
      {legacyDays && stats && stats.total_count === 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-amber-200 bg-amber-50/70 px-4 py-3 shadow-sm">
          <Upload size={16} className="shrink-0 text-amber-500" />
          <div className="min-w-0 flex-1 text-xs text-amber-800">
            检测到旧版番茄钟的本地统计（{legacyDays.length} 天），点击导入合并到新的服务端统计，之后清除本地旧数据
          </div>
          <button
            onClick={importLegacy}
            className="rounded-lg bg-amber-500 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-amber-600"
          >
            一键导入
          </button>
          <button
            onClick={() => {
              localStorage.removeItem(STATS_KEY);
              setLegacyDays(null);
            }}
            className="rounded-lg px-2 py-1.5 text-xs text-amber-600 hover:bg-amber-100"
          >
            不需要
          </button>
        </div>
      )}
      {importedMsg && !legacyDays && (
        <div className="rounded-xl bg-emerald-50 px-4 py-2.5 text-xs text-emerald-700">
          ✅ {importedMsg}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
        {/* ============ 左：计时主卡 ============ */}
        <div className="relative overflow-hidden rounded-3xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
          {/* 模式分段控件 */}
          <div className="mx-auto flex w-fit rounded-2xl bg-slate-100 p-1">
            {MODE_ORDER.map((m) => (
              <button
                key={m}
                onClick={() => pickMode(m)}
                disabled={sessionStarted}
                title={sessionStarted ? "重置或结束当前阶段后可切换模式" : undefined}
                aria-pressed={mode === m}
                className={`rounded-xl px-4 py-1.5 text-sm font-medium transition sm:px-6 ${
                  mode === m ? MODE_THEME[m].segActive : "text-slate-500 hover:text-slate-700"
                }`}
              >
                {MODE_THEME[m].label}
              </button>
            ))}
          </div>

          <button onClick={() => setShowSettings(true)}
            className="mx-auto mt-4 flex min-h-11 items-center gap-2 rounded-xl bg-slate-50 px-4 text-sm text-slate-600">
            <Settings2 size={15} />
            {plannedMinRef.current} 分钟 · 自定义时长
          </button>

          {/* 环形计时器 */}
          <div className="relative mx-auto mt-8 flex w-fit items-center justify-center">
            <div
              className={`pointer-events-none absolute h-64 w-64 rounded-full blur-3xl transition-colors duration-700 ${theme.glow}`}
              aria-hidden
            />
            <svg width={SIZE} height={SIZE} className="-rotate-90">
              <defs>
                <linearGradient id="pomodoro-ring" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" stopColor={theme.hexSoft} />
                  <stop offset="100%" stopColor={theme.hex} />
                </linearGradient>
              </defs>
              <circle
                cx={SIZE / 2}
                cy={SIZE / 2}
                r={R}
                fill="none"
                stroke="#f1f5f9"
                strokeWidth={STROKE}
              />
              <circle
                cx={SIZE / 2}
                cy={SIZE / 2}
                r={R}
                fill="none"
                stroke="url(#pomodoro-ring)"
                strokeWidth={STROKE}
                strokeLinecap="round"
                strokeDasharray={C}
                strokeDashoffset={dashOffset}
                style={{ transition: running ? "stroke-dashoffset 0.4s linear" : "none" }}
              />
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <span className={`text-xs font-medium tracking-widest ${theme.text}`}>
                {running ? `${theme.label}中` : sessionStarted ? "已暂停" : mode === "focus" ? "准备专注" : "放松一下"}
              </span>
              <span className="mt-1 font-mono text-6xl font-bold tabular-nums tracking-tight text-slate-800">
                {mmss}
              </span>
              {boundLabel ? (
                <span className="mt-2 inline-flex max-w-[240px] items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-[11px] text-slate-600">
                  <ListTodo size={11} className="shrink-0" />
                  <span className="truncate">{boundLabel}</span>
                </span>
              ) : (
                <span className="mt-2 text-[11px] text-slate-400">
                  {mode === "focus"
                    ? bindKind === "free"
                      ? "可在「专注对象」中关联任务或复习块"
                      : "已选择绑定对象"
                    : "站起来活动一下、喝口水"}
                </span>
              )}
            </div>
          </div>

          {/* 控制按钮 */}
          <div className="mt-8 flex items-center justify-center gap-6">
            <button
              onClick={reset}
              aria-label="重置计时"
              title="重置"
              className="flex h-11 w-11 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 transition hover:border-slate-300 hover:text-slate-700 active:scale-95"
            >
              <RotateCcw size={18} />
            </button>
            <button
              onClick={toggle}
              className={`flex h-16 w-16 items-center justify-center rounded-full text-white transition active:scale-95 ${theme.btn}`}
              aria-label={`${running ? "暂停" : "开始"}${theme.label}计时`}
              title={running ? "暂停" : "开始"}
            >
              {running ? <Pause size={26} /> : <Play size={26} className="ml-0.5" />}
            </button>
            <button
              onClick={endPhase}
              aria-label={mode === "focus" ? "结束本阶段" : "跳过休息"}
              title={mode === "focus" ? "结束本个番茄（不计入番茄数，时间仍留痕）" : "跳过休息"}
              className="flex h-11 w-11 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 transition hover:border-slate-300 hover:text-slate-700 active:scale-95"
            >
              <SkipForward size={18} />
            </button>
          </div>

          {/* 打断记录（番茄工作法：被打断就重置） */}
          {mode === "focus" && (
            <div className="mt-5 flex items-center justify-center gap-2">
              <button
                onClick={() => setInterrupts((n) => n + 1)}
                title="被打断时点一下：番茄工作法建议被打断就重置本个番茄"
                className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-[11px] text-slate-500 transition hover:border-red-200 hover:text-red-500"
              >
                <AlarmClock size={12} />
                记录一次打断
              </button>
              {interrupts > 0 && (
                <span className="rounded-full bg-red-50 px-2.5 py-1 text-[11px] font-medium text-red-500">
                  已打断 {interrupts} 次
                </span>
              )}
            </div>
          )}

          {/* 长休息轮次点 */}
          <div className="mt-6 flex items-center justify-center gap-2">
            {Array.from({ length: settings.longEvery }, (_, i) => (
              <span
                key={i}
                className={`h-2 w-2 rounded-full transition-colors ${
                  i < cyclePos % settings.longEvery ||
                  (cyclePos > 0 && cyclePos % settings.longEvery === 0 && i === 0)
                    ? theme.bar
                    : "bg-slate-200"
                }`}
                title={`第 ${i + 1} 个专注后${i + 1 === settings.longEvery ? "长休息" : ""}`}
              />
            ))}
            <span className="ml-1 text-[11px] text-slate-400">
              每 {settings.longEvery} 个专注后长休息
            </span>
          </div>
        </div>

        {/* ============ 右：绑定 / 统计 / 时间线 / 设置 ============ */}
        <div className="space-y-4">
          {/* 专注完成 → 资料评分（喂回间隔重复） */}
          {ratingPanel && (
            <div className="rounded-2xl border border-emerald-200 bg-emerald-50/70 p-4 shadow-sm">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-semibold text-emerald-800">
                  🎉 本个番茄完成，给复习的资料打个分
                </span>
                <button
                  onClick={() => setRatingPanel(null)}
                  className="rounded-md p-1 text-emerald-500 hover:bg-emerald-100"
                >
                  <X size={14} />
                </button>
              </div>
              <div className="mb-2 text-[11px] text-emerald-600">
                评分会更新掌握程度与间隔重复安排，复习计划随之优化
              </div>
              <div className="space-y-2">
                {ratingPanel.materials.map((m) => (
                  <div key={m.id} className="flex flex-wrap items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-xs text-slate-700">{m.title}</span>
                    {rated[m.id] ? (
                      <span className="rounded-md bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
                        {RATINGS[rated[m.id]].label} ✓
                      </span>
                    ) : (
                      <div className="flex items-center gap-1">
                        {Object.entries(RATINGS).map(([key, cfg]) => (
                          <button
                            key={key}
                            onClick={() => rateMaterial(m.id, key)}
                            className={`rounded-md border px-1.5 py-0.5 text-[11px] font-medium transition ${cfg.cls}`}
                          >
                            {cfg.label}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
              <Link
                to="/review"
                className="mt-3 inline-flex items-center gap-1 text-[11px] font-medium text-emerald-700 hover:underline"
              >
                去复习计划页看掌握度 →
              </Link>
            </div>
          )}

          {/* 绑定专注对象 */}
          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-2.5 flex items-center gap-2 text-sm font-semibold text-slate-700">
              <ListTodo size={15} className="text-brand-500" />
              本次专注对象
            </div>
            {sessionStarted && <p className="mb-2 text-xs text-slate-400">当前阶段已开始，重置或结束后可更换专注对象。</p>}
            <div className="mb-3 flex rounded-xl bg-slate-100 p-1 text-xs">
              {(
                [
                  ["free", "自由专注"],
                  ["task", "待办任务"],
                  ["plan", "复习计划"],
                ] as [BindKind, string][]
              ).map(([k, label]) => (
                <button
                  key={k}
                  onClick={() => setBindKind(k)}
                  disabled={sessionStarted}
                  aria-pressed={bindKind === k}
                  className={`flex-1 rounded-lg px-2 py-1.5 font-medium transition ${
                    bindKind === k ? "bg-white text-brand-700 shadow-sm" : "text-slate-500"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            {bindKind === "task" && (
              <>
                <select
                  aria-label="选择专注任务"
                  disabled={sessionStarted}
                  value={taskId}
                  onChange={(e) => setTaskId(e.target.value === "" ? "" : Number(e.target.value))}
                  className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none transition focus:border-brand-500"
                >
                  <option value="">选择要专注完成的待办…</option>
                  {tasks.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.title}
                    </option>
                  ))}
                </select>
                {tasks.length === 0 && (
                  <div className="mt-2 text-[11px] text-slate-400">
                    没有未完成任务，
                    <Link to="/tasks" className="text-brand-600 hover:underline">
                      去添加
                    </Link>
                  </div>
                )}
              </>
            )}

            {bindKind === "plan" && (
              <>
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-[11px] text-slate-400">
                    {onlyToday
                      ? `今日复习块 ${visiblePlanOptions.length} 个`
                      : `全部复习块 ${visiblePlanOptions.length} 个（按日期排序）`}
                  </span>
                  <button
                    onClick={() => setOnlyToday((v) => !v)}
                    className="text-[11px] font-medium text-brand-600 hover:underline"
                  >
                    {onlyToday ? "显示全部日期" : "只看今天"}
                  </button>
                </div>
                <div className="max-h-64 space-y-1.5 overflow-y-auto pr-1">
                  {visiblePlanOptions.map((o) => {
                    const key = `${o.planId}:${o.date}`;
                    const sel = planSel && planSel.planId === o.planId && planSel.date === o.date;
                    return (
                      <button
                        key={key}
                        disabled={sessionStarted}
                        onClick={() => setPlanSel({ planId: o.planId, date: o.date })}
                        className={`w-full rounded-xl border p-2.5 text-left transition ${
                          sel
                            ? "border-brand-400 bg-brand-50/70"
                            : "border-slate-200 hover:border-brand-300"
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2 text-xs">
                          <span className="flex min-w-0 items-center gap-1.5 font-medium text-slate-700">
                            <CalendarDays size={11} className="shrink-0 text-brand-500" />
                            <span className="truncate">{o.course}</span>
                          </span>
                          <span className="shrink-0 text-[10px] text-slate-400">
                            {o.date === todayKey ? "今天" : o.date.slice(5)} · {o.hours}h
                          </span>
                        </div>
                        <div className="mt-1 truncate text-[11px] text-slate-500">{o.scope}</div>
                        <div className="mt-0.5 text-[10px] text-slate-400">
                          {o.materialIds.length} 份资料 · {o.examDate} 考试
                        </div>
                      </button>
                    );
                  })}
                  {visiblePlanOptions.length === 0 && (
                    <div className="py-4 text-center text-[11px] text-slate-400">
                      {plans.length === 0 ? (
                        <>
                          还没有复习计划，
                          <Link to="/review" className="text-brand-600 hover:underline">
                            去生成
                          </Link>
                        </>
                      ) : (
                        "该日期没有复习块"
                      )}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>

          {/* 今日统计 */}
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className={`text-2xl font-bold ${theme.text}`}>{todayStat.count}</div>
              <div className="mt-0.5 text-xs text-slate-400">今日番茄（个）</div>
            </div>
            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="text-2xl font-bold text-slate-800">{todayStat.minutes}</div>
              <div className="mt-0.5 text-xs text-slate-400">今日专注（分钟）</div>
            </div>
            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex items-center gap-1 text-2xl font-bold text-orange-500">
                <Flame size={18} />
                {stats?.streak ?? 0}
              </div>
              <div className="mt-0.5 text-xs text-slate-400">连续专注（天）</div>
            </div>
            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="text-2xl font-bold text-slate-800">{stats?.total_minutes ?? 0}</div>
              <div className="mt-0.5 text-xs text-slate-400">
                累计专注（分钟 · {stats?.total_count ?? 0} 番茄）
              </div>
            </div>
          </div>

          {/* 近 7 天柱状图 */}
          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between">
              <span className="text-sm font-semibold text-slate-700">近 7 天专注</span>
              <span className="text-[11px] text-slate-400">
                共 {week.reduce((n, d) => n + d.count, 0)} 个番茄
              </span>
            </div>
            <div className="flex h-24 items-end justify-between gap-1.5">
              {week.map((d) => (
                <div key={d.date} className="flex h-full min-w-0 flex-1 flex-col items-center gap-1">
                  <span className="text-[10px] font-medium text-slate-500">
                    {d.count > 0 ? d.count : ""}
                  </span>
                  <div className="flex w-full flex-1 items-end">
                    <div
                      className={`w-full rounded-md transition-all ${
                        d.date === todayKey ? theme.bar : "bg-slate-200"
                      }`}
                      style={{
                        height: `${Math.max(4, (d.count / weekMax) * 100)}%`,
                        minHeight: d.count > 0 ? 8 : 4,
                      }}
                      title={`${d.date.slice(5)}：${d.count} 个番茄 · ${d.minutes} 分钟`}
                    />
                  </div>
                  <span
                    className={`text-[9px] ${d.date === todayKey ? "font-semibold " + theme.text : "text-slate-400"}`}
                  >
                    {d.date.slice(5).replace("-", "/")}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* 课程分布 */}
          {(stats?.by_course.length ?? 0) > 0 && (
            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="mb-3 text-sm font-semibold text-slate-700">专注课程分布</div>
              <div className="space-y-2.5">
                {stats!.by_course.slice(0, 6).map((c) => (
                  <div key={c.course_id ?? 0}>
                    <div className="mb-1 flex items-center justify-between text-xs">
                      <span className="min-w-0 truncate text-slate-600">
                        {c.course_name || "未分课程"}
                      </span>
                      <span className="shrink-0 text-[10px] text-slate-400">
                        {c.minutes} 分钟 · {c.count} 番茄
                      </span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
                      <div
                        className="h-full rounded-full bg-brand-400"
                        style={{ width: `${Math.max(4, (c.minutes / courseMax) * 100)}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 时间线 */}
          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-2.5 flex items-center justify-between">
              <span className="text-sm font-semibold text-slate-700">专注时间线</span>
              <span className="text-[11px] text-slate-400">最近 {sessions.length} 条</span>
            </div>
            {sessions.length === 0 ? (
              <div className="py-3 text-center text-[11px] text-slate-400">
                完成第一个番茄后，这里会出现今天的专注时间线
              </div>
            ) : (
              <div className="max-h-72 space-y-0.5 overflow-y-auto pr-1">
                {sessions.map((s) => {
                  const isToday = dateKey(new Date(s.started_at)) === todayKey;
                  return (
                    <div key={s.id} className="flex items-center gap-2 rounded-lg px-1 py-1.5 hover:bg-slate-50">
                      <span
                        className={`w-14 shrink-0 text-[10px] tabular-nums ${
                          isToday ? "font-semibold text-brand-600" : "text-slate-400"
                        }`}
                        title={new Date(s.started_at).toLocaleString()}
                      >
                        {isToday ? fmtHM(s.started_at) : new Date(s.started_at).getMonth() + 1 + "/" + new Date(s.started_at).getDate()}
                      </span>
                      <span
                        className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium ${
                          s.mode === "focus" ? MODE_THEME.focus.chipBg : "bg-slate-100 text-slate-500"
                        }`}
                      >
                        {MODE_LABEL[s.mode as Mode] ?? s.mode}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-xs text-slate-600" title={contextTitle(s)}>
                        {contextLabel(s)}
                      </span>
                      {s.interrupt_count > 0 && (
                        <span
                          className="shrink-0 rounded-md bg-red-50 px-1.5 py-0.5 text-[10px] text-red-500"
                          title={`被打断 ${s.interrupt_count} 次`}
                        >
                          打断×{s.interrupt_count}
                        </span>
                      )}
                      {s.mode === "focus" && !s.completed && (
                        <span className="shrink-0 text-[10px] text-amber-500">未完成</span>
                      )}
                      <span className="w-12 shrink-0 text-right text-[11px] tabular-nums text-slate-400">
                        {s.duration_min}分
                      </span>
                      <button
                        onClick={() => removeSession(s.id)}
                        title="删除这条记录"
                        className="shrink-0 rounded-md p-1 text-slate-300 transition hover:bg-red-50 hover:text-red-500"
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <div className="flex items-start gap-2 rounded-xl bg-slate-50 px-3.5 py-2.5 text-[11px] leading-5 text-slate-500">
            <Coffee size={14} className="mt-0.5 shrink-0 text-brand-500" />
            <span>
              专注期间减少分心，被打断时可以记录并重新开始。关联复习计划后，完成专注即可给资料评分，更新掌握进度。
            </span>
          </div>
        </div>
      </div>
      {showSettings && <FocusSettingsDialog settings={settings} inProgress={sessionStarted}
        onSave={saveSettings} onClose={() => setShowSettings(false)} />}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 时间线文案                                                          */
/* ------------------------------------------------------------------ */

function contextLabel(s: FocusSession): string {
  if (s.mode !== "focus") return s.note || "休息";
  if (s.plan_title) return `${s.plan_title} · ${s.plan_date.slice(5)}`;
  if (s.task_title) return s.task_title;
  if (s.course_name) return s.course_name;
  return s.note || "自由专注";
}

function contextTitle(s: FocusSession): string {
  if (s.plan_title) return `${s.plan_title}（${s.plan_date}）`;
  if (s.task_title) return `任务：${s.task_title}`;
  if (s.course_name) return `课程：${s.course_name}`;
  return s.note || "自由专注";
}
