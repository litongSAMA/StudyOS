import { useEffect, useState } from "react";
import { Outlet, NavLink, useLocation } from "react-router-dom";
import {
  AlertTriangle,
  BookOpen,
  BookX,
  BrainCircuit,
  CalendarClock,
  CalendarDays,
  ChartColumn,
  ClipboardList,
  GraduationCap,
  HelpCircle,
  Home,
  ListChecks,
  Menu,
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
import ReminderBell from "./ReminderBell";
import { ErrorBoundary } from "./ErrorBoundary";
import WallpaperLayer from "./WallpaperLayer";

const NAV = [
  { to: "/", label: "首页", icon: Home },
  { to: "/ingest", label: "智能入库", icon: Sparkles },
  { to: "/assistant", label: "对话助手", icon: MessageSquare },
  { to: "/materials", label: "资料库", icon: BookOpen },
  { to: "/notes", label: "AI 笔记提炼", icon: NotebookPen },
  { to: "/quiz", label: "AI 自测抽背", icon: BrainCircuit },
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
];

/** 品牌区 + 导航 + 底部状态区：桌面侧边栏与移动端抽屉共用 */
function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const { llmStatus, refreshHealth, openOnboarding, user } = useApp();
  const backendDown = llmStatus === "backend_down";
  return (
    <>
      <div className="flex items-center gap-2.5 px-5 py-5">
        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-500 text-white">
          <GraduationCap size={20} />
        </div>
        <div>
          <div className="text-base font-bold tracking-tight text-slate-800">StudyOS</div>
          <div className="text-[10px] text-slate-400">个人学习管理智能体</div>
        </div>
      </div>
      <nav className="flex-1 space-y-1 overflow-y-auto px-3">
        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            onClick={onNavigate}
            className={({ isActive }) =>
              `flex items-center gap-2.5 rounded-xl px-3 py-2.5 text-sm font-medium transition ${
                isActive
                  ? "bg-brand-50 text-brand-700"
                  : "text-slate-500 hover:bg-slate-100 hover:text-slate-700"
              }`
            }
          >
            <Icon size={17} />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-slate-100 px-5 py-4">
        {user && (
          <div
            className="mb-2 flex items-center gap-1.5 text-[11px] text-slate-400"
            title="当前登录账号（登录保护开启时显示）"
          >
            <UserRound size={12} className="text-brand-400" />
            <span className="truncate">{user.username}</span>
          </div>
        )}
        <div className="flex items-center justify-between">
          <button
            onClick={refreshHealth}
            title={
              backendDown
                ? "后端未连接——点击重试。若后端未启动，请先运行 ./start.sh"
                : "点击重新检测后端与 AI 状态"
            }
            className={`flex items-center gap-2 text-[11px] ${
              backendDown
                ? "text-red-600"
                : llmStatus.startsWith("offline")
                  ? "text-slate-400"
                  : "text-emerald-600"
            }`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                backendDown
                  ? "bg-red-500"
                  : llmStatus.startsWith("offline")
                    ? "bg-slate-300"
                    : "bg-emerald-500"
              }`}
            />
            {backendDown
              ? "后端未连接 · 点此重试"
              : `AI Agent：${llmStatus.startsWith("offline") ? "规则引擎（离线）" : "已连接"}`}
          </button>
          <div className="flex items-center gap-3">
            <button
              onClick={openOnboarding}
              title="重新查看新手引导"
              className="rounded-md text-slate-400 transition hover:text-brand-600"
            >
              <HelpCircle size={16} />
            </button>
            <ReminderBell />
          </div>
        </div>
      </div>
    </>
  );
}

export default function Layout() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [offlineServed, setOfflineServed] = useState(isOfflineServed());
  const location = useLocation();

  // 路由切换自动收起抽屉
  useEffect(() => {
    setDrawerOpen(false);
  }, [location.pathname]);

  // 抽屉打开时锁定背景滚动 + Esc 关闭
  useEffect(() => {
    if (!drawerOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDrawerOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [drawerOpen]);

  // 监听「离线缓存模式」（Service Worker 兜底返回缓存数据）切换
  useEffect(() => onOfflineChange(setOfflineServed), []);

  // 监听「下载失败」（后端未连接 / 401 / 导出出错）：横幅提示 8 秒后自动消失，
  // 避免点了导出什么都不发生、让人以为按钮坏了
  const [downloadError, setDownloadError] = useState(getDownloadError());
  useEffect(() => onDownloadError(setDownloadError), []);
  useEffect(() => {
    if (!downloadError) return;
    const timer = window.setTimeout(() => setDownloadError(null), 8000);
    return () => window.clearTimeout(timer);
  }, [downloadError]);

  return (
    <div className="min-h-screen md:flex">
      {/* 动态壁纸层：固定在视口最底层，侧边栏/内容层在其上方半透明展示 */}
      <WallpaperLayer />

      {/* 窄屏顶栏（<md）：汉堡菜单 + 品牌 + 铃铛 */}
      <header className="sticky top-0 z-30 flex items-center gap-2 border-b border-slate-200 bg-white/70 px-3 py-2.5 backdrop-blur-xl md:hidden">
        <button
          onClick={() => setDrawerOpen(true)}
          aria-label="打开导航菜单"
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-100 hover:text-slate-700"
        >
          <Menu size={20} />
        </button>
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-500 text-white">
          <GraduationCap size={18} />
        </div>
        <div className="truncate text-base font-bold tracking-tight text-slate-800">
          StudyOS
        </div>
        <div className="ml-auto">
          <ReminderBell />
        </div>
      </header>

      {/* 窄屏抽屉导航（<md） */}
      {drawerOpen && (
        <div
          className="fixed inset-0 z-40 md:hidden"
          role="dialog"
          aria-modal="true"
          aria-label="导航菜单"
        >
          <div
            className="absolute inset-0 bg-slate-900/40"
            onClick={() => setDrawerOpen(false)}
          />
          <aside className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col bg-white/90 shadow-xl backdrop-blur-xl">
            <button
              onClick={() => setDrawerOpen(false)}
              aria-label="关闭菜单"
              className="absolute right-3 top-4 z-10 flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
            >
              <X size={18} />
            </button>
            <SidebarContent onNavigate={() => setDrawerOpen(false)} />
          </aside>
        </div>
      )}

      {/* 桌面侧边栏（≥md）：固定左侧 */}
      <aside className="fixed inset-y-0 left-0 z-10 hidden w-52 flex-col border-r border-slate-200 bg-white/85 backdrop-blur-xl md:flex">
        <SidebarContent />
      </aside>

      {/* 主内容 */}
      <main className="relative z-10 min-w-0 flex-1 md:ml-52">
        {offlineServed && (
          <div className="flex items-center gap-2 border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-700 md:px-8">
            <WifiOff size={13} className="shrink-0" />
            <span>离线模式：当前展示的是本地缓存数据，联网后自动更新。</span>
          </div>
        )}
        {downloadError && (
          <div
            role="alert"
            className="flex items-start gap-2 border-b border-red-200 bg-red-50 px-4 py-2 text-xs text-red-700 md:px-8"
          >
            <AlertTriangle size={13} className="mt-0.5 shrink-0" />
            <span className="min-w-0 break-words">{downloadError}</span>
            <button
              onClick={() => setDownloadError(null)}
              className="ml-auto shrink-0 rounded p-0.5 hover:bg-red-100"
              title="关闭提示"
            >
              <X size={13} />
            </button>
          </div>
        )}
        {/* 内容容器：宽屏下用满可用宽度（max-w-6xl），减少长文本与工具条折行 */}
        <div className="mx-auto max-w-6xl px-4 py-6 md:px-8 md:py-8">
          {/* key=路由：某页渲染出错仅影响该页，切到其他页面自动恢复 */}
          <ErrorBoundary key={location.pathname} homePath="/">
            <Outlet />
          </ErrorBoundary>
        </div>
      </main>
    </div>
  );
}
