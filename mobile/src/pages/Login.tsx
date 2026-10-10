import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import {
  AlertCircle,
  Check,
  Copy,
  Eye,
  EyeOff,
  GraduationCap,
  History,
  KeyRound,
  Lock,
  LogIn,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import { apiAuth, ApiHttpError, ApiNetworkError, setAuthToken } from "../api";
import type { AuthUser } from "../api";
import { Spinner } from "../components/ui";
import { CapsLockHint, capsLockOn } from "../components/PasswordStrength";

/**
 * 登录页：登录保护开启后的全屏门禁。
 *
 * 说明：登录保护开启后，注册新账号只允许已登录的管理员在设置页操作，
 * 因此这里只提供登录入口——账号就是本机访问锁的钥匙。
 *
 * 体验细节：
 * - 记住用户名（本机 localStorage，默认开启；勾选状态随登录保存）；
 * - 连续输错密码触发锁定退避时，实时倒计时展示「N 秒后可重试」并禁用表单；
 * - CapsLock 提示、密码可见性切换、错误抖动反馈；
 * - 「忘记密码？」折叠面板给出本机恢复工具命令（一键复制）。
 */

const LAST_USER_KEY = "studyos.auth.last_username";
const REMEMBER_KEY = "studyos.auth.remember_username";

function readLocal(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function writeLocal(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* localStorage 不可用时静默忽略 */
  }
}

export default function Login({ onAuthed }: { onAuthed: (user: AuthUser) => void }) {
  const [username, setUsername] = useState(() => {
    // 勾选了记住用户名时，预填上次登录的用户名
    return readLocal(REMEMBER_KEY) === "1" ? (readLocal(LAST_USER_KEY) ?? "") : "";
  });
  const [lastUser] = useState(() => readLocal(LAST_USER_KEY));
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [remember, setRemember] = useState(() => readLocal(REMEMBER_KEY) !== "0");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [capsLock, setCapsLock] = useState(false);
  // 锁定退避倒计时：lockUntil = 允许重试的时间戳（毫秒）
  const [lockUntil, setLockUntil] = useState<number | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [shake, setShake] = useState(0);
  const [showRecover, setShowRecover] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const passwordRef = useRef<HTMLInputElement>(null);

  const locked = lockUntil != null;
  const remaining = locked ? Math.max(0, Math.ceil((lockUntil - nowMs) / 1000)) : 0;

  // 锁定期间每秒刷新剩余秒数；到期自动解除
  useEffect(() => {
    if (lockUntil == null) return;
    const t = setInterval(() => {
      if (lockUntil - Date.now() <= 0) setLockUntil(null);
      else setNowMs(Date.now());
    }, 500);
    return () => clearInterval(t);
  }, [lockUntil]);

  // 记住用户名时自动聚焦密码框
  useEffect(() => {
    if (lastUser && remember) passwordRef.current?.focus();
  }, [lastUser, remember]);

  const triggerShake = () => {
    // 先移除再重加类名，保证连续失败也能重新触发抖动
    setShake(0);
    requestAnimationFrame(() => requestAnimationFrame(() => setShake((k) => k + 1)));
  };

  const onKey = (e: KeyboardEvent) => setCapsLock(capsLockOn(e));

  const copy = async (text: string, key: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      /* 剪贴板不可用（非安全上下文等）时静默 */
      return;
    }
    setCopied(key);
    window.setTimeout(() => setCopied((c) => (c === key ? null : c)), 2000);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const u = username.trim();
    if (!u || !password || locked) return;
    setBusy(true);
    setError("");
    setCapsLock(false);
    try {
      const r = await apiAuth.login(u, password);
      if (remember) writeLocal(LAST_USER_KEY, u);
      else writeLocal(LAST_USER_KEY, null);
      writeLocal(REMEMBER_KEY, remember ? "1" : "0");
      setAuthToken(r.token);
      onAuthed(r.user);
    } catch (err) {
      triggerShake();
      if (err instanceof ApiHttpError) {
        if (err.status === 429 && err.retryAfter) {
          setNowMs(Date.now());
          setLockUntil(Date.now() + err.retryAfter * 1000);
          setError(`尝试次数过多，已暂时锁定，请 ${err.retryAfter} 秒后再试`);
        } else {
          setError(String(err.message));
        }
      } else if (err instanceof ApiNetworkError) {
        setError("无法连接后端：请确认已运行 start.bat / start.sh 启动 StudyOS，再刷新重试");
      } else {
        setError("登录失败，请稍后重试");
      }
    } finally {
      setBusy(false);
    }
  };

  const inputCls =
    "w-full rounded-xl border border-slate-200 bg-white/80 py-2.5 pl-9 pr-3 text-sm text-slate-700 outline-none transition placeholder:text-slate-300 focus:border-brand-400 focus:ring-2 focus:ring-brand-100 disabled:opacity-60";

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden px-4 py-10">
      {/* 动态极光背景：与设置页壁纸同源的 CSS 光斑 + 细网格 + 底部光晕 */}
      <div aria-hidden className="pointer-events-none fixed inset-0">
        <div className="absolute inset-0 bg-gradient-to-br from-brand-50 via-slate-50 to-indigo-100" />
        <span className="wpblob wp-aurora-blob wp-aurora-blob-0" />
        <span className="wpblob wp-aurora-blob wp-aurora-blob-1" />
        <span className="wpblob wp-aurora-blob wp-aurora-blob-2" />
        <span className="wpblob wp-aurora-blob wp-aurora-blob-3" />
        <div
          className="absolute inset-0 opacity-40"
          style={{
            backgroundImage: "radial-gradient(rgba(99,102,241,0.14) 1px, transparent 1px)",
            backgroundSize: "26px 26px",
          }}
        />
        <div className="absolute -bottom-28 left-1/2 h-64 w-[38rem] -translate-x-1/2 rounded-full bg-indigo-200/40 blur-3xl" />
      </div>

      <div className="auth-enter relative z-10 w-full max-w-sm">
        {/* 品牌 */}
        <div className="auth-enter-delay-1 mb-5 flex flex-col items-center gap-2.5 text-center">
          <div className="auth-float flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-500 to-indigo-600 text-white shadow-lg shadow-brand-500/40 ring-1 ring-white/60">
            <GraduationCap size={28} />
          </div>
          <div>
            <div className="text-2xl font-bold tracking-tight text-slate-800">StudyOS</div>
            <div className="mt-0.5 text-xs text-slate-400">个人学习管理智能体 · 本地优先</div>
          </div>
        </div>

        {/* 登录保护徽标 */}
        <div className="auth-enter-delay-2 mb-4 flex items-center justify-center">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-brand-200 bg-brand-50/80 px-3 py-1 text-[11px] font-medium text-brand-700">
            <ShieldCheck size={12} />
            已开启登录保护
          </span>
        </div>

        <form
          onSubmit={submit}
          className={`rounded-3xl border border-white/70 bg-white/75 p-6 shadow-2xl shadow-indigo-500/10 backdrop-blur-xl sm:p-7 ${
            shake > 0 ? "auth-shake" : ""
          }`}
        >
          <p className="mb-5 text-center text-xs leading-relaxed text-slate-400">
            请登录后继续使用。你的学习数据始终保存在本机，不上云。
          </p>

          {/* 用户名 */}
          <label className="mb-1.5 block text-xs font-medium text-slate-500">用户名</label>
          <div className="relative">
            <UserRound size={15} className="auth-input-icon text-slate-400" />
            <input
              autoFocus={!(lastUser && remember)}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="用户名 / 手机号 / 微信号 / QQ号"
              autoComplete="username"
              disabled={locked}
              className={inputCls}
            />
          </div>
          {lastUser && (
            <div className="mt-1.5 flex items-center gap-1 text-[11px] text-slate-400">
              <History size={11} className="shrink-0" />
              <span>上次登录：</span>
              <button
                type="button"
                onClick={() => setUsername(lastUser)}
                className="font-medium text-brand-600 transition hover:underline"
              >
                {lastUser}
              </button>
              {username.trim() === lastUser && <Check size={11} className="text-emerald-500" />}
            </div>
          )}

          {/* 密码 */}
          <label className="mb-1.5 mt-3.5 block text-xs font-medium text-slate-500">密码</label>
          <div className="relative">
            <Lock size={15} className="auth-input-icon text-slate-400" />
            <input
              ref={passwordRef}
              type={showPassword ? "text" : "password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={onKey}
              onKeyUp={onKey}
              placeholder="请输入密码"
              autoComplete="current-password"
              disabled={locked}
              className={`${inputCls} pr-10`}
            />
            <button
              type="button"
              onClick={() => setShowPassword((v) => !v)}
              title={showPassword ? "隐藏密码" : "显示密码"}
              className="absolute inset-y-0 right-0 flex w-10 items-center justify-center text-slate-400 transition hover:text-slate-600"
            >
              {showPassword ? <EyeOff size={15} /> : <Eye size={15} />}
            </button>
          </div>
          {capsLock && <CapsLockHint />}

          {/* 记住用户名 + 忘记密码 */}
          <div className="mt-3 flex items-center justify-between">
            <label className="flex cursor-pointer select-none items-center gap-1.5 text-[11px] text-slate-500">
              <input
                type="checkbox"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
                className="h-3.5 w-3.5 rounded accent-brand-600"
              />
              记住用户名
            </label>
            <button
              type="button"
              onClick={() => setShowRecover((v) => !v)}
              className="text-[11px] font-medium text-brand-600 transition hover:underline"
            >
              忘记密码？
            </button>
          </div>

          {/* 错误提示 */}
          {error && (
            <div
              role="alert"
              className="mt-3 flex items-start gap-1.5 rounded-xl border border-red-100 bg-red-50 px-3 py-2 text-xs leading-relaxed text-red-600"
            >
              <AlertCircle size={13} className="mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {/* 登录按钮（锁定期间倒计时） */}
          <button
            type="submit"
            disabled={busy || locked || !username.trim() || !password}
            className="mt-4 flex w-full items-center justify-center gap-1.5 rounded-xl bg-gradient-to-r from-brand-600 to-indigo-600 px-4 py-2.5 text-sm font-medium text-white shadow-lg shadow-brand-500/25 transition hover:from-brand-700 hover:to-indigo-700 active:scale-[0.99] disabled:opacity-50"
          >
            {busy ? <Spinner /> : locked ? <Lock size={14} /> : <LogIn size={15} />}
            {locked ? `${remaining} 秒后可重试` : busy ? "正在登录…" : "登录"}
          </button>

          {/* 本机找回工具（折叠） */}
          {showRecover && (
            <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50/80 p-3.5 text-[11px] leading-relaxed text-slate-500">
              <div className="mb-1.5 flex items-center gap-1 font-medium text-slate-600">
                <KeyRound size={12} />
                本机找回（无需改动数据库）
              </div>
              <p className="mb-1">
                忘记密码？在本机打开终端，进入{" "}
                <code className="rounded bg-slate-200/70 px-1">backend</code> 目录执行：
              </p>
              <div className="mb-2 flex items-center gap-1.5">
                <code className="min-w-0 flex-1 rounded-lg bg-white px-2 py-1.5 font-mono text-[10px] break-all ring-1 ring-slate-200">
                  python -m app.authadmin reset-password 用户名
                </code>
                <button
                  type="button"
                  onClick={() => copy("python -m app.authadmin reset-password 用户名", "reset")}
                  className="flex shrink-0 items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-[10px] font-medium text-slate-500 transition hover:border-brand-300 hover:text-brand-600"
                >
                  {copied === "reset" ? <Check size={11} className="text-emerald-500" /> : <Copy size={11} />}
                  {copied === "reset" ? "已复制" : "复制"}
                </button>
              </div>
              <p className="mb-1">误开登录保护？关闭保护执行：</p>
              <div className="flex items-center gap-1.5">
                <code className="min-w-0 flex-1 rounded-lg bg-white px-2 py-1.5 font-mono text-[10px] break-all ring-1 ring-slate-200">
                  python -m app.authadmin disable-protection
                </code>
                <button
                  type="button"
                  onClick={() => copy("python -m app.authadmin disable-protection", "disable")}
                  className="flex shrink-0 items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-[10px] font-medium text-slate-500 transition hover:border-brand-300 hover:text-brand-600"
                >
                  {copied === "disable" ? <Check size={11} className="text-emerald-500" /> : <Copy size={11} />}
                  {copied === "disable" ? "已复制" : "复制"}
                </button>
              </div>
            </div>
          )}

          {/* 数据安全说明 */}
          <div className="mt-4 flex items-start gap-1.5 text-[11px] leading-relaxed text-slate-400">
            <Lock size={12} className="mt-0.5 shrink-0" />
            <span>
              账号是本机访问锁，仅用于保护学习数据；全部账号共享同一份数据，不随多端同步、不上云。
            </span>
          </div>
        </form>
      </div>
    </div>
  );
}
