import { useEffect, useState, lazy, Suspense } from "react";
import { Route, Routes, useLocation } from "react-router-dom";
import Layout from "./components/Layout";
import { ErrorBoundary } from "./components/ErrorBoundary";
import Onboarding, { ONBOARDING_DONE_KEY } from "./components/Onboarding";


















import { ApiNetworkError, apiAuth, apiMisc, onAuthExpired, getKnownAuthProtection, type AuthUser } from "./api";

const Assistant = lazy(() => import("./pages/Assistant"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Checklists = lazy(() => import("./pages/Checklists"));
const Countdown = lazy(() => import("./pages/Countdown"));
const ForecastPage = lazy(() => import("./pages/Forecast"));
const Ingest = lazy(() => import("./pages/Ingest"));
const Login = lazy(() => import("./pages/Login"));
const Materials = lazy(() => import("./pages/Materials"));
const Notes = lazy(() => import("./pages/Notes"));
const QuizPage = lazy(() => import("./pages/Quiz"));
const ReportPage = lazy(() => import("./pages/Report"));
const SharedChecklist = lazy(() => import("./pages/SharedChecklist"));
const Tasks = lazy(() => import("./pages/Tasks"));
const Timetable = lazy(() => import("./pages/Timetable"));
const Review = lazy(() => import("./pages/Review"));
const Settings = lazy(() => import("./pages/Settings"));
const Wrongbook = lazy(() => import("./pages/Wrongbook"));

/** 全局上下文：LLM/OCR 状态 + 数据变更触发器 + 新手引导 + 登录会话 */
import { createContext, useContext } from "react";

interface AppCtx {
  /** llm 运行态：connected=已连接 / offline=规则引擎 / backend_down=后端不可达 */
  llmStatus: string;
  refreshKey: number;
  bump: () => void;
  refreshHealth: () => void;
  openOnboarding: () => void;
  /** 登录保护是否开启（默认 false = 免登录） */
  authEnabled: boolean;
  /** 当前登录用户（未登录为 null） */
  user: AuthUser | null;
  /** 重新拉取登录状态（登录/登出/开关保护后调用） */
  refreshAuth: () => Promise<void>;
}
const Ctx = createContext<AppCtx>({
  llmStatus: "unknown",
  refreshKey: 0,
  bump: () => {},
  refreshHealth: () => {},
  openOnboarding: () => {},
  authEnabled: false,
  user: null,
  refreshAuth: async () => {},
});
export const useApp = () => useContext(Ctx);

export default function App() {
  const [llmStatus, setLlmStatus] = useState("unknown");
  const [refreshKey, setRefreshKey] = useState(0);
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  // 登录会话状态：authChecked = 已向后端确认过（未确认前显示启动屏，避免闪出登录页/主界面）
  const [authChecked, setAuthChecked] = useState(false);
  const [authEnabled, setAuthEnabled] = useState(getKnownAuthProtection);
  const isShared = /^\/s\/[^/]+$/.test(useLocation().pathname);
  const [user, setUser] = useState<AuthUser | null>(null);

  const refreshHealth = () => {
    apiMisc
      .health()
      .then((h) => setLlmStatus(h.llm))
      .catch((e) =>
        setLlmStatus(e instanceof ApiNetworkError ? "backend_down" : "offline"),
      );
  };

  const refreshAuth = async () => {
    try {
      const s = await apiAuth.status();
      setAuthEnabled(s.enabled);
      setUser(s.user);
    } catch {
      // 后端不可达：保持当前状态（启动屏提示会由健康检查兜底）
    } finally {
      setAuthChecked(true);
    }
  };

  useEffect(() => {
    refreshHealth();
    void refreshAuth();
  }, []);

  // 登录会话失效（401）：切回登录门禁
  useEffect(
    () =>
      onAuthExpired(() => {
        setUser(null);
        setAuthEnabled(true);
      }),
    [],
  );

  // 后端状态轮询：后端重启后侧边栏状态自动恢复，无需手动刷新页面
  useEffect(() => {
    const t = setInterval(refreshHealth, 20000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 首次访问自动弹出新手引导（每个浏览器仅一次，localStorage 记忆）；
  // 登录保护开启且未登录时不弹（先登录再说）
  useEffect(() => {
    if (isShared || (authEnabled && !user)) return;
    try {
      if (localStorage.getItem(ONBOARDING_DONE_KEY) !== "1") {
        setOnboardingOpen(true);
      }
    } catch {
      /* localStorage 不可用时忽略，直接展示引导 */
      setOnboardingOpen(true);
    }
  }, [authEnabled, user, isShared]);

  const finishOnboarding = () => {
    try {
      localStorage.setItem(ONBOARDING_DONE_KEY, "1");
    } catch {
      /* ignore */
    }
    setOnboardingOpen(false);
  };

  // 登录门禁：保护开启且未登录 → 全屏登录页（无侧边栏、无任何数据请求）
  if (authChecked && authEnabled && !user && !isShared) {
    return <Suspense fallback={<div role="status" className="p-8 text-sm text-slate-500">正在加载登录页…</div>}><Login onAuthed={(u) => setUser(u)} /></Suspense>;
  }

  // 启动中：极简占位，避免闪屏
  if (!authChecked) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50">
        <div className="flex items-center gap-3 text-sm text-slate-400">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-brand-500 border-t-transparent" />
          StudyOS 启动中…
        </div>
      </div>
    );
  }

  return (
    <Ctx.Provider
      value={{
        llmStatus,
        refreshKey,
        bump: () => setRefreshKey((k) => k + 1),
        refreshHealth,
        openOnboarding: () => setOnboardingOpen(true),
        authEnabled,
        user,
        refreshAuth,
      }}
    >
      {/* 全局错误边界：Layout 本身或分享页崩溃时，全屏可恢复错误页兜底，杜绝白屏 */}
      <ErrorBoundary full homePath="/" label="应用遇到了一点问题，你的数据没有丢失。">
        <Suspense fallback={<div role="status" className="p-8 text-sm text-slate-500">正在加载页面…</div>}><Routes>
          <Route element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="ingest" element={<Ingest />} />
            <Route path="assistant" element={<Assistant />} />
            <Route path="materials" element={<Materials />} />
            <Route path="notes" element={<Notes />} />
            <Route path="quiz" element={<QuizPage />} />
            <Route path="wrongbook" element={<Wrongbook />} />
            <Route path="tasks" element={<Tasks />} />
            <Route path="checklists" element={<Checklists />} />
            <Route path="countdown" element={<Countdown />} />
            <Route path="focus" element={null} />
            <Route path="timetable" element={<Timetable />} />
            <Route path="review" element={<Review />} />
            <Route path="forecast" element={<ForecastPage />} />
            <Route path="report" element={<ReportPage />} />
            <Route path="settings" element={<Settings />} />
          </Route>
          {/* 分享链接只读页：独立于主布局（无侧边栏，任何人可打开） */}
          <Route path="s/:token" element={<SharedChecklist />} />
        </Routes></Suspense>
      </ErrorBoundary>
      <Onboarding open={onboardingOpen} onFinish={finishOnboarding} />
    </Ctx.Provider>
  );
}
