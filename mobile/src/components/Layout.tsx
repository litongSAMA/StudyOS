import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  BookX,
  BrainCircuit,
  CalendarClock,
  CalendarDays,
  ChartColumn,
  ChevronRight,
  ClipboardList,
  GraduationCap,
  Grid2X2,
  HelpCircle,
  Home,
  ListChecks,
  NotebookPen,
  MessageSquare,
  Settings,
  Sparkles,
  Timer,
  TimerReset,
  TrendingUp,
  UserRound,
  WifiOff,
  X,
} from "lucide-react";
import { useApp } from "../App";
import { getDownloadError, isOfflineServed, onDownloadError, onOfflineChange } from "../api";
import { ErrorBoundary } from "./ErrorBoundary";
import ReminderBell from "./ReminderBell";
import WallpaperLayer from "./WallpaperLayer";
import { useDialog } from "./useDialog";
import type { FocusStatus } from "../pages/Focus";
const Focus = lazy(() => import("../pages/Focus"));

const ROUTES = [
  { to: "/", label: "首页", icon: Home },
  { to: "/ingest", label: "智能入库", icon: Sparkles },
  { to: "/assistant", label: "对话助手", icon: MessageSquare },
  { to: "/materials", label: "资料库", icon: BookOpen },
  { to: "/notes", label: "AI 笔记", icon: NotebookPen },
  { to: "/quiz", label: "AI 自测", icon: BrainCircuit },
  { to: "/wrongbook", label: "错题本", icon: BookX },
  { to: "/tasks", label: "DDL 任务", icon: CalendarClock },
  { to: "/checklists", label: "清单", icon: ClipboardList },
  { to: "/countdown", label: "考试倒计时", icon: Timer },
  { to: "/focus", label: "番茄钟", icon: TimerReset },
  { to: "/timetable", label: "周课表", icon: CalendarDays },
  { to: "/review", label: "复习计划", icon: ListChecks },
  { to: "/forecast", label: "进度预测", icon: TrendingUp },
  { to: "/report", label: "学习报告", icon: ChartColumn },
  { to: "/settings", label: "设置", icon: Settings },
] as const;

const BOTTOM_ROUTES = ["/", "/materials", "/ingest", "/tasks"];
const GROUPS = [
  { label: "资料与录入", routes: ["/materials", "/ingest", "/assistant", "/notes"] },
  { label: "计划与专注", routes: ["/tasks", "/focus", "/review", "/checklists", "/timetable", "/countdown"] },
  { label: "练习与复盘", routes: ["/quiz", "/wrongbook", "/forecast", "/report"] },
  { label: "账号与偏好", routes: ["/settings"] },
];

function MobileHeader({ title, focusStatus }: { title: string; focusStatus: FocusStatus | null }) {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <header className="mobile-header">
      {location.pathname === "/" ? (
        <Link to="/" aria-label="首页" className="mobile-brand-mark"><GraduationCap size={19} /></Link>
      ) : (
        <button className="mobile-icon-button" aria-label="返回上一页" onClick={() => {
          if (window.history.state?.idx > 0) navigate(-1); else navigate("/");
        }}><ArrowLeft size={21} /></button>
      )}
      <div className="min-w-0 flex-1">
        <div className="truncate text-[11px] font-medium tracking-[0.16em] text-brand-500">
          STUDYOS
        </div>
        <div className="truncate text-base font-bold text-slate-900">{title}</div>
      </div>
      {focusStatus && location.pathname !== "/focus" && <Link to="/focus" aria-label="返回进行中的番茄钟" className="mobile-focus-status">
        <TimerReset size={15} /><span>{focusStatus.remaining}<small>{focusStatus.running ? focusStatus.label + "中" : "已暂停"}</small></span>
      </Link>}
      <ReminderBell />
    </header>
  );
}

function MoreSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { llmStatus, refreshHealth, openOnboarding, user } = useApp();
  const backendDown = llmStatus === "backend_down";

  const location = useLocation();
  const [query, setQuery] = useState("");
  const dialogRef = useRef<HTMLElement>(null);
  useDialog(dialogRef, onClose, open);
  useEffect(() => { if (open) setQuery(""); }, [open]);
  const matches = ROUTES.filter((route) => route.label.toLowerCase().includes(query.trim().toLowerCase()));

  if (!open) return null;

  return (
    <div className="mobile-sheet-layer" role="dialog" aria-modal="true" aria-label="全部功能">
      <button className="mobile-sheet-backdrop" aria-label="关闭全部功能" onClick={onClose} />
      <section ref={dialogRef} tabIndex={-1} className="mobile-sheet">
        <div className="mobile-sheet-handle" />
        <div className="flex items-center justify-between px-5 pb-4 pt-1">
          <div>
            <h2 className="text-lg font-bold text-slate-900">全部功能</h2>
            <p className="mt-0.5 text-xs text-slate-400">学习资料、计划与复盘都在这里</p>
          </div>
          <button onClick={onClose} className="mobile-icon-button" aria-label="关闭">
            <X size={19} />
          </button>
        </div>

        <div className="px-5 pb-3">
          <input aria-label="搜索功能" placeholder="搜索功能，如番茄钟、复习计划" type="search"
            value={query} onChange={(event) => setQuery(event.target.value)}
            className="min-h-11 w-full rounded-xl border border-slate-200 bg-slate-50 px-3 text-sm outline-none focus:border-brand-400" />
        </div>
        {GROUPS.map((group) => {
          const routes = group.routes.map((to) => matches.find((route) => route.to === to)).filter((route): route is typeof ROUTES[number] => route !== undefined);
          if (!routes.length) return null;
          return (
            <nav key={group.label} aria-label={group.label} className="px-4 pb-3">
              <h3 className="px-2 pb-1 text-xs font-semibold text-slate-400">{group.label}</h3>
              <div className="mobile-feature-grid">
                {routes.map(({ to, label, icon: Icon }) => (
                  <Link key={to} to={to} onClick={onClose} aria-current={location.pathname === to ? "page" : undefined}
                    className={`mobile-feature-link ${location.pathname === to ? "is-current" : ""}`}>
                    <span className="mobile-feature-icon"><Icon size={19} /></span><span>{label}</span>
                  </Link>
                ))}
              </div>
            </nav>
          );
        })}
        {!matches.some((route) => route.to !== "/") && <p role="status" className="px-5 py-5 text-center text-sm text-slate-400">没有找到功能，试试其他关键词。</p>}

        <div className="mx-4 mt-4 rounded-2xl bg-slate-50 px-4 py-3">
          {user && (
            <div className="mb-2 flex items-center gap-2 border-b border-slate-200/70 pb-2 text-xs text-slate-500">
              <UserRound size={14} className="text-brand-500" />
              <span className="min-w-0 flex-1 truncate">{user.username}</span>
              <Link to="/settings" onClick={onClose} className="flex items-center gap-0.5 text-brand-600">
                账号 <ChevronRight size={13} />
              </Link>
            </div>
          )}
          <div className="flex items-center justify-between gap-3">
            <button onClick={refreshHealth} className="flex min-w-0 items-center gap-2 text-left text-xs text-slate-500">
              <span className={`h-2 w-2 shrink-0 rounded-full ${backendDown ? "bg-red-500" : llmStatus.startsWith("offline") ? "bg-slate-300" : "bg-emerald-500"}`} />
              <span className="truncate">
                {backendDown ? "后端未连接 · 点此重试" : llmStatus.startsWith("offline") ? "AI 规则引擎" : "AI 已连接"}
              </span>
            </button>
            <button onClick={() => { onClose(); openOnboarding(); }} className="flex shrink-0 items-center gap-1 text-xs text-brand-600">
              <HelpCircle size={14} /> 使用帮助
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

export default function Layout() {
  const location = useLocation();
  const [moreOpen, setMoreOpen] = useState(false);
  const [focusVisited, setFocusVisited] = useState(false);
  const [focusStatus, setFocusStatus] = useState<FocusStatus | null>(null);
  const relatedRef = useRef<HTMLElement>(null);
  const [offlineServed, setOfflineServed] = useState(isOfflineServed());
  const current = useMemo(
    () => ROUTES.find((route) => route.to === location.pathname),
    [location.pathname],
  );

  useEffect(() => {
    setMoreOpen(false);
    if (location.pathname === "/focus") setFocusVisited(true);
    window.scrollTo({ top: 0, behavior: "instant" });
  }, [location.pathname]);

  useEffect(() => onOfflineChange(setOfflineServed), []);

  // 下载失败提示（后端未连接 / 401 / 导出出错）：8 秒后自动消失，
  // 避免点了导出什么都不发生、让人以为按钮坏了
  const [downloadError, setDownloadError] = useState(getDownloadError());
  useEffect(() => onDownloadError(setDownloadError), []);
  useEffect(() => {
    if (!downloadError) return;
    const timer = window.setTimeout(() => setDownloadError(null), 8000);
    return () => window.clearTimeout(timer);
  }, [downloadError]);
  useEffect(() => { relatedRef.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: "nearest", inline: "center" }); }, [location.pathname]);

  const bottomItems = BOTTOM_ROUTES.map((to) => ROUTES.find((route) => route.to === to)!);
  const moreActive = !BOTTOM_ROUTES.includes(location.pathname);
  const currentGroup = GROUPS.find((group) => group.routes.includes(location.pathname));

  return (
    <div className="mobile-app-shell">
      <WallpaperLayer />
      <MobileHeader title={current?.label ?? "StudyOS"} focusStatus={focusStatus} />

      {offlineServed && (
        <div className="mobile-offline-banner">
          <WifiOff size={14} />
          <span>离线模式 · 正在展示本地缓存</span>
        </div>
      )}

      {downloadError && (
        <div className="mobile-download-error-banner" role="alert">
          <AlertTriangle size={14} />
          <span>{downloadError}</span>
          <button type="button" onClick={() => setDownloadError(null)} aria-label="关闭提示">
            <X size={14} />
          </button>
        </div>
      )}

      <main className="mobile-main">
        {currentGroup && currentGroup.routes.length > 1 && (
          <nav ref={relatedRef} className="mobile-related-nav" aria-label={currentGroup.label}>
            {currentGroup.routes.map((to) => {
              const route = ROUTES.find((item) => item.to === to)!;
              return <Link key={to} to={to} aria-current={location.pathname === to ? "page" : undefined}
                className={location.pathname === to ? "is-active" : ""}>{route.label.replace("DDL ", "")}</Link>;
            })}
          </nav>
        )}
        {(focusVisited || location.pathname === "/focus") && (
          <div hidden={location.pathname !== "/focus"}>
            <ErrorBoundary homePath="/"><Suspense fallback={<p role="status" className="p-4 text-sm text-slate-500">正在加载番茄钟…</p>}><Focus onStatusChange={setFocusStatus} /></Suspense></ErrorBoundary>
          </div>
        )}
        {location.pathname !== "/focus" && <ErrorBoundary key={location.pathname} homePath="/"><Outlet /></ErrorBoundary>}
      </main>

      <nav className="mobile-bottom-nav" aria-label="主导航">
        {bottomItems.map(({ to, label, icon: Icon }) => {
          const active = location.pathname === to;
          const prominent = to === "/ingest";
          return (
            <Link
              key={to}
              to={to}
              aria-current={active ? "page" : undefined}
              className={`mobile-tab ${active ? "is-active" : ""} ${prominent ? "is-prominent" : ""}`}
            >
              <span className="mobile-tab-icon"><Icon size={prominent ? 22 : 20} strokeWidth={active ? 2.4 : 2} /></span>
              <span>{label === "智能入库" ? "录入" : label.replace("DDL ", "")}</span>
            </Link>
          );
        })}
        <button
          type="button"
          className={`mobile-tab ${moreActive || moreOpen ? "is-active" : ""}`}
          onClick={() => setMoreOpen(true)}
          aria-expanded={moreOpen}
        >
          <span className="mobile-tab-icon"><Grid2X2 size={20} /></span>
          <span>全部功能</span>
        </button>
      </nav>

      <MoreSheet open={moreOpen} onClose={() => setMoreOpen(false)} />
    </div>
  );
}
