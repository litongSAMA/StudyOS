import { Component, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, Check, Copy, Home, RefreshCw } from "lucide-react";

interface ErrorBoundaryProps {
  children: ReactNode;
  /** 「返回首页」导航目标，默认 "/" */
  homePath?: string;
  /** 出错时的提示文案 */
  label?: string;
  /** true = 全屏兜底（无侧边栏的应用级错误）；false = 面板式（用于主内容区） */
  full?: boolean;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * 全局错误边界：任何子组件渲染/生命周期抛错时，展示可恢复的错误页而非整站白屏。
 * 使用方式：
 *  - 页面级：<ErrorBoundary key={location.pathname}><Outlet/></ErrorBoundary>
 *  - 应用级：<ErrorBoundary full>...</ErrorBoundary>
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // 保留完整错误堆栈便于排查（不影响用户体验）
    console.error("[ErrorBoundary]", error, info?.componentStack ?? "");
  }

  reset = () => this.setState({ error: null });

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <ErrorFallback
        error={this.state.error}
        onReset={this.reset}
        homePath={this.props.homePath ?? "/"}
        label={this.props.label}
        full={this.props.full}
      />
    );
  }
}

function ErrorFallback({
  error,
  onReset,
  homePath,
  label,
  full,
}: {
  error: Error;
  onReset: () => void;
  homePath: string;
  label?: string;
  full?: boolean;
}) {
  const navigate = useNavigate();
  const [copied, setCopied] = useState(false);

  const goHome = () => {
    onReset();
    navigate(homePath);
  };

  const copyError = async () => {
    try {
      await navigator.clipboard.writeText(
        `${error.name}: ${error.message}\n${error.stack ?? ""}`,
      );
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* 剪贴板不可用时静默忽略 */
    }
  };

  const content = (
    <div
      className={`flex flex-col items-center justify-center gap-4 px-6 py-16 text-center ${
        full ? "min-h-screen bg-slate-50" : "min-h-[55vh]"
      }`}
    >
      {full && (
        <div className="mb-2 flex items-center gap-2">
          <span className="text-lg font-bold text-slate-800">StudyOS</span>
          <span className="text-xs text-slate-400">个人学习管理智能体</span>
        </div>
      )}

      <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-amber-50 text-amber-500">
        <AlertTriangle size={24} />
      </div>

      <div className="max-w-md">
        <h2 className="text-base font-semibold text-slate-800">页面出错了</h2>
        <p className="mt-1.5 text-sm leading-6 text-slate-500">
          {label ?? "界面渲染时遇到一个问题，你的数据没有丢失。重新加载或返回首页即可继续使用。"}
        </p>
        <p
          className="mx-auto mt-3 max-w-md truncate rounded-lg bg-slate-50 px-3 py-1.5 text-xs text-slate-500"
          title={error.stack ?? error.message}
        >
          {error.name}: {error.message}
        </p>
      </div>

      <div className="mt-1 flex flex-wrap items-center justify-center gap-2">
        <button
          onClick={goHome}
          className="flex items-center gap-1.5 rounded-xl bg-brand-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-700"
        >
          <Home size={14} />
          返回首页
        </button>
        <button
          onClick={() => window.location.reload()}
          className="flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-600 transition hover:border-brand-300 hover:text-brand-600"
        >
          <RefreshCw size={14} />
          重新加载
        </button>
        <button
          onClick={copyError}
          className="flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-500 transition hover:border-slate-300 hover:text-slate-700"
          title="复制错误信息"
        >
          {copied ? <Check size={14} className="text-emerald-500" /> : <Copy size={14} />}
          {copied ? "已复制" : "复制错误信息"}
        </button>
      </div>

      {full && (
        <p className="mt-4 text-xs text-slate-400">
          若问题反复出现，可把「复制错误信息」的结果反馈给开发者
        </p>
      )}
    </div>
  );

  if (full) return content;
  return (
    <div className="rounded-2xl border border-dashed border-slate-200 bg-white/60">
      {content}
    </div>
  );
}
