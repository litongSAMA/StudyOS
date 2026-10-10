import { useEffect, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import {
  Check,
  Eye,
  EyeOff,
  KeyRound,
  LogOut,
  MonitorSmartphone,
  ScrollText,
  ShieldCheck,
  ShieldOff,
  Trash2,
  UserPlus,
  Users,
  X,
} from "lucide-react";
import {
  apiAuth,
  ApiHttpError,
  setAuthToken,
  type AuthEventItem,
  type AuthRegistrationContacts,
  type AuthSessionInfo,
  type AuthUser,
  type AuthVerificationChallenge,
  type ContactKind,
} from "../api";
import { useApp } from "../App";
import { Spinner } from "../components/ui";
import AccountContacts from "./AccountContacts";
import ContactCodeField from "./ContactCodeField";
import {
  CapsLockHint,
  PasswordMeter,
  USERNAME_HINT,
  capsLockOn,
  passwordEqualsUsername,
  validPassword,
  validUsername,
} from "../components/PasswordStrength";

/**
 * 账号安全 · 登录保护（设置页卡片）。
 *
 * - 默认（未开启保护）：免登录即用；可在此注册首个账号（注册即自动登录），
 *   登录后可一键开启「登录保护」；
 * - 开启保护后：打开应用需先登录；已登录状态下可添加成员、修改密码、
 *   删除成员、关闭保护或退出登录；可查看并撤销登录会话（设备）、
 *   查看安全事件日志（登录成败/注册/改密/开关保护等审计留痕）；
 * - 全部账号共享同一份学习数据（账号只是本机访问锁，数据不同步、不上云）。
 *
 * 体验增强：注册/改密表单实时校验（用户名规则、密码强度条、确认匹配指示、
 * CapsLock 提示），输入框按校验状态高亮。
 */

const EVENT_LABELS: Record<string, string> = {
  register: "注册账号",
  user_add: "添加账号",
  login_ok: "登录成功",
  login_fail: "登录失败",
  logout: "退出登录",
  password_change: "修改密码",
  reset_password: "重置密码（恢复工具）",
  enable: "开启登录保护",
  disable: "关闭登录保护",
  user_delete: "删除账号",
  session_revoke: "撤销会话",
  session_revoke_all: "退出其它设备",
  verification_requested: "申请联系方式验证码",
  contact_bind: "绑定/换绑联系方式",
  contact_unbind: "解绑联系方式",
};

/** 表单区块小标题 */
function Section({ icon, title, extra }: { icon: ReactNode; title: string; extra?: ReactNode }) {
  return (
    <div className="mb-2 flex items-center justify-between">
      <span className="flex items-center gap-1.5 text-xs font-semibold text-slate-500">
        {icon}
        {title}
      </span>
      {extra}
    </div>
  );
}

/** 输入框按校验状态着色：bad = 红，good = 绿，缺省 = 默认 */
const fieldCls = (bad?: boolean, good?: boolean) =>
  `rounded-xl border px-3 py-2 text-sm outline-none transition focus:ring-2 ${
    bad
      ? "border-red-300 bg-red-50/40 focus:border-red-400 focus:ring-red-100"
      : good
        ? "border-emerald-300 focus:border-emerald-400 focus:ring-emerald-100"
        : "border-slate-200 focus:border-brand-400 focus:ring-brand-100"
  }`;

/** 行内校验提示：ok = 绿色对勾，否则红色警示文字 */
function FieldHint({ ok, text }: { ok: boolean; text: string }) {
  return (
    <div className={`mt-1 flex items-center gap-1 text-[10px] ${ok ? "text-emerald-600" : "text-red-500"}`}>
      {ok ? <Check size={11} className="shrink-0" /> : <X size={11} className="shrink-0" />}
      <span className="truncate">{text}</span>
    </div>
  );
}

export default function AuthCard() {
  const { authEnabled, user, refreshAuth } = useApp();
  const [users, setUsers] = useState<AuthUser[] | null>(null);
  const [sessions, setSessions] = useState<AuthSessionInfo[] | null>(null);
  const [events, setEvents] = useState<AuthEventItem[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [showAdd, setShowAdd] = useState(false);
  const [showEvents, setShowEvents] = useState(false);

  // 注册 / 添加成员
  const [regUsername, setRegUsername] = useState("");
  const [regPassword, setRegPassword] = useState("");
  const [regConfirm, setRegConfirm] = useState("");
  const [regCaps, setRegCaps] = useState(false);
  // 开放注册触发 429 时，跟随 Retry-After 倒计时，避免用户重复提交同一请求。
  const [regRetryUntil, setRegRetryUntil] = useState<number | null>(null);
  const [regNowMs, setRegNowMs] = useState(() => Date.now());
  const [regContacts, setRegContacts] = useState<Record<ContactKind, string>>({
    phone: "",
    wechat: "",
    qq: "",
  });
  const [regCodes, setRegCodes] = useState<Record<ContactKind, string>>({
    phone: "",
    wechat: "",
    qq: "",
  });
  const [regChallenges, setRegChallenges] = useState<Record<ContactKind, AuthVerificationChallenge | null>>({
    phone: null,
    wechat: null,
    qq: null,
  });

  // 登录（未开启保护、已有账号、未登录时）
  const [loginUsername, setLoginUsername] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [showLoginPassword, setShowLoginPassword] = useState(false);
  const [loginCaps, setLoginCaps] = useState(false);

  // 修改密码
  const [curPassword, setCurPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newConfirm, setNewConfirm] = useState("");
  const [showNewPassword, setShowNewPassword] = useState(false);
  const [newCaps, setNewCaps] = useState(false);

  // ---- 实时校验（与后端规则一致）----
  const regUserTyped = regUsername.trim() !== "";
  const regUserOk = validUsername(regUsername.trim());
  const regPwOk = validPassword(regPassword) && !passwordEqualsUsername(regPassword, regUsername.trim());
  const regConfirmOk = regConfirm.length > 0 && regConfirm === regPassword;
  const regFormOk = regUserOk && regPwOk && regConfirmOk;
  const regRetrySeconds = regRetryUntil == null
    ? 0
    : Math.max(0, Math.ceil((regRetryUntil - regNowMs) / 1000));
  const regLocked = regRetrySeconds > 0;
  const regContactKinds: ContactKind[] = ["phone", "wechat", "qq"];
  const regContactsOk = regContactKinds.every((kind) => {
    if (!regContacts[kind].trim()) return true;
    return !!regChallenges[kind] && regCodes[kind].length === 6;
  });

  useEffect(() => {
    if (regRetryUntil == null) return;
    const timer = window.setInterval(() => {
      const remaining = regRetryUntil - Date.now();
      if (remaining <= 0) setRegRetryUntil(null);
      setRegNowMs(Date.now());
    }, 500);
    return () => window.clearInterval(timer);
  }, [regRetryUntil]);

  const newPwOk = validPassword(newPassword) && !passwordEqualsUsername(newPassword, user?.username ?? "");
  const newConfirmOk = newConfirm.length > 0 && newConfirm === newPassword;
  const changePwOk = !!curPassword && newPwOk && newConfirmOk;

  const keyCaps = (set: (v: boolean) => void) => (e: KeyboardEvent) => set(capsLockOn(e));

  const reloadAccounts = async () => {
    if (user) {
      const [u, s, e] = await Promise.all([
        apiAuth.listUsers().catch(() => null),
        apiAuth.sessions().catch(() => null),
        apiAuth.events(30).catch(() => null),
      ]);
      setUsers(u);
      setSessions(s);
      setEvents(e);
    } else {
      setUsers(null);
      setSessions(null);
      setEvents(null);
    }
  };

  useEffect(() => {
    // 已登录时拉取账号/会话/安全事件
    void reloadAccounts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, authEnabled]);

  const fail = (err: unknown) => {
    setError(err instanceof ApiHttpError ? String(err.message) : "操作失败，请稍后重试");
    setBusy(false);
  };

  const register = async (e: FormEvent) => {
    e.preventDefault();
    if (!regFormOk || !regContactsOk || regLocked) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const verifiedContacts: AuthRegistrationContacts = {};
      for (const kind of regContactKinds) {
        const value = regContacts[kind].trim();
        if (value && regChallenges[kind] && regCodes[kind]) {
          verifiedContacts[kind] = {
            value,
            challenge_id: regChallenges[kind]!.challenge_id,
            code: regCodes[kind],
          };
        }
      }
      const r = await apiAuth.register(
        regUsername.trim(),
        regPassword,
        regConfirm,
        Object.keys(verifiedContacts).length ? verifiedContacts : undefined,
      );
      setRegUsername("");
      setRegPassword("");
      setRegConfirm("");
      setRegContacts({ phone: "", wechat: "", qq: "" });
      setRegCodes({ phone: "", wechat: "", qq: "" });
      setRegChallenges({ phone: null, wechat: null, qq: null });
      setShowAdd(false);
      if (r.token) {
        // 未开启保护时注册即自动登录
        setAuthToken(r.token);
        setMessage(`账号「${r.user.username}」已创建并登录，可开启登录保护`);
      } else {
        setMessage(`账号「${r.user.username}」已创建`);
      }
      await refreshAuth();
      await reloadAccounts();
    } catch (err) {
      if (err instanceof ApiHttpError && err.status === 429 && err.retryAfter) {
        setRegNowMs(Date.now());
        setRegRetryUntil(Date.now() + err.retryAfter * 1000);
      }
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const login = async (e: FormEvent) => {
    e.preventDefault();
    if (!loginUsername.trim() || !loginPassword) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const r = await apiAuth.login(loginUsername.trim(), loginPassword);
      setAuthToken(r.token);
      setLoginUsername("");
      setLoginPassword("");
      setMessage(`已登录：${r.user.username}`);
      await refreshAuth();
      await reloadAccounts();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const logout = async () => {
    setBusy(true);
    setError("");
    try {
      await apiAuth.logout();
      setAuthToken(null);
      setMessage("已退出登录");
      await refreshAuth();
      await reloadAccounts();
    } catch {
      // 后端不可达也要清本端令牌（下次 status 会按新状态处理）
      setAuthToken(null);
      await refreshAuth();
    } finally {
      setBusy(false);
    }
  };

  const toggleProtection = async () => {
    const enabling = !authEnabled;
    if (
      enabling &&
      !confirm(
        "开启登录保护后，每次打开 StudyOS 都需要登录（数据仍保存在本机）。确定开启？",
      )
    )
      return;
    if (!enabling && !confirm("确定关闭登录保护？关闭后打开应用不再需要登录。")) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await apiAuth.setEnabled(enabling);
      setMessage(enabling ? "登录保护已开启：重新打开页面需要登录" : "登录保护已关闭：恢复免登录使用");
      await refreshAuth();
      await reloadAccounts();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const changePassword = async (e: FormEvent) => {
    e.preventDefault();
    if (!changePwOk) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await apiAuth.changePassword(curPassword, newPassword);
      setCurPassword("");
      setNewPassword("");
      setNewConfirm("");
      setMessage("密码已修改（其它设备的登录已失效）");
      await reloadAccounts();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const removeUser = async (u: AuthUser) => {
    if (!confirm(`删除账号「${u.username}」？该账号将无法再登录（学习数据不受影响）。`)) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await apiAuth.removeUser(u.id);
      setMessage(`账号「${u.username}」已删除`);
      await reloadAccounts();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const revokeSession = async (s: AuthSessionInfo) => {
    if (!confirm(s.current ? "撤销当前会话？你将立即退出登录。" : "撤销该会话？该设备上的登录将立即失效。"))
      return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await apiAuth.revokeSession(s.id);
      if (s.current) {
        setAuthToken(null);
        setMessage("当前会话已撤销，你已退出登录");
        await refreshAuth();
      } else {
        setMessage("会话已撤销，该设备的登录已失效");
      }
      await reloadAccounts();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const revokeOthers = async () => {
    if (!confirm("退出其它设备？其它设备上的登录将立即失效（本机不受影响）。")) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await apiAuth.revokeOtherSessions();
      setMessage("已退出其它设备");
      await reloadAccounts();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const btnPrimary =
    "flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-brand-600 to-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm shadow-brand-500/20 transition hover:from-brand-700 hover:to-indigo-700 disabled:opacity-50";

  const registrationContactFields = (
    <div className="space-y-1.5 border-t border-slate-200/70 pt-2 md:col-span-4">
      <div className="text-[11px] font-medium text-slate-500">绑定联系方式（可选，需验证码）</div>
      <div className="grid gap-2 md:grid-cols-3">
        {regContactKinds.map((kind) => (
          <ContactCodeField
            key={kind}
            kind={kind}
            value={regContacts[kind]}
            code={regCodes[kind]}
            challenge={regChallenges[kind]}
            purpose="register"
            disabled={busy || regLocked}
            onValueChange={(value) => setRegContacts((prev) => ({ ...prev, [kind]: value }))}
            onCodeChange={(value) => setRegCodes((prev) => ({ ...prev, [kind]: value }))}
            onChallenge={(challenge) => setRegChallenges((prev) => ({ ...prev, [kind]: challenge }))}
          />
        ))}
      </div>
      <div className="text-[10px] leading-relaxed text-slate-400">
        联系方式会经过验证码确认并唯一绑定，可在登录后设置中验证换绑。
      </div>
    </div>
  );

  const meMeta = (users ?? []).find((u) => u.id === user?.id);

  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      {/* 卡片头部 */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 bg-gradient-to-r from-brand-50/80 via-white to-indigo-50/60 px-5 py-4">
        <div className="flex items-center gap-3">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-brand-500 to-indigo-600 text-white shadow-sm shadow-brand-500/30">
            <ShieldCheck size={17} />
          </span>
          <div>
            <div className="text-sm font-semibold text-slate-700">账号安全 · 登录保护</div>
            <div className="text-[11px] text-slate-400">
              {authEnabled ? "已开启：打开 StudyOS 需先登录，失败尝试与安全事件全程留痕" : "当前免登录使用，注册账号后可开启"}
            </div>
          </div>
        </div>
        <button
          onClick={toggleProtection}
          disabled={busy || !user}
          title={user ? "" : "请先注册并登录一个账号，再开启登录保护"}
          className={`flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
            authEnabled
              ? "border border-red-200 bg-red-50 text-red-600 hover:border-red-400"
              : "border border-brand-200 bg-brand-50 text-brand-700 hover:border-brand-400"
          }`}
        >
          {authEnabled ? <ShieldOff size={14} /> : <ShieldCheck size={14} />}
          {authEnabled ? "关闭登录保护" : "开启登录保护"}
        </button>
      </div>

      <div className="p-5">
        {error && (
          <div className="mb-3 rounded-xl bg-red-50 px-3 py-2 text-xs text-red-600">{error}</div>
        )}
        {message && (
          <div className="mb-3 rounded-xl bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
            {message}
          </div>
        )}

        {user ? (
          /* ---- 已登录：账号信息 + 密码 + 成员管理 + 会话/事件 ---- */
          <div className="space-y-6">
            {/* 当前账号 */}
            <div className="flex flex-wrap items-center gap-3">
              <span className="flex h-9 w-9 items-center justify-center rounded-full bg-gradient-to-br from-brand-500 to-indigo-600 text-sm font-bold text-white shadow-sm shadow-brand-500/30">
                {(user.username[0] ?? "?").toUpperCase()}
              </span>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2 text-sm font-medium text-slate-700">
                  {user.username}
                  <span className="rounded-full bg-brand-50 px-2 py-0.5 text-[10px] font-medium text-brand-700">
                    当前账号
                  </span>
                </div>
                <div className="text-[11px] text-slate-400">
                  {meMeta?.last_login_at
                    ? `最近登录 ${new Date(meMeta.last_login_at).toLocaleString()}`
                    : "本机访问锁账号"}
                </div>
              </div>
              <div className="ml-auto">
                <button
                  onClick={logout}
                  disabled={busy}
                  className="flex items-center gap-1.5 rounded-xl border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-500 transition hover:border-red-300 hover:text-red-600 disabled:opacity-50"
                >
                  {busy ? <Spinner /> : <LogOut size={13} />} 退出登录
                </button>
              </div>
            </div>

            <AccountContacts />

            {/* 修改密码 */}
            <div>
              <Section
                icon={<KeyRound size={13} />}
                title="修改密码"
                extra={<span className="text-[10px] text-slate-400">修改后其它设备全部下线</span>}
              />
              <form onSubmit={changePassword} className="grid gap-2 md:grid-cols-4">
                <input
                  type="password"
                  placeholder="当前密码"
                  value={curPassword}
                  onChange={(e) => setCurPassword(e.target.value)}
                  autoComplete="current-password"
                  className={fieldCls()}
                />
                <div>
                  <div className="relative">
                    <input
                      type={showNewPassword ? "text" : "password"}
                      placeholder="新密码（至少 6 位）"
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      onKeyDown={keyCaps(setNewCaps)}
                      onKeyUp={keyCaps(setNewCaps)}
                      autoComplete="new-password"
                      className={`${fieldCls(newPassword !== "" && !newPwOk, newPwOk)} w-full pr-9`}
                    />
                    <button
                      type="button"
                      onClick={() => setShowNewPassword((v) => !v)}
                      title={showNewPassword ? "隐藏" : "显示"}
                      className="absolute inset-y-0 right-0 flex w-8 items-center justify-center text-slate-400 hover:text-slate-600"
                    >
                      {showNewPassword ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  </div>
                  <PasswordMeter value={newPassword} />
                  {newCaps && <CapsLockHint />}
                  {newPassword !== "" && passwordEqualsUsername(newPassword, user?.username ?? "") && (
                    <FieldHint ok={false} text="新密码不能与用户名相同" />
                  )}
                </div>
                <div>
                  <input
                    type="password"
                    placeholder="确认新密码"
                    value={newConfirm}
                    onChange={(e) => setNewConfirm(e.target.value)}
                    autoComplete="new-password"
                    className={`${fieldCls(newConfirm !== "" && !newConfirmOk, newConfirmOk)} w-full`}
                  />
                  {newConfirm !== "" &&
                    (newConfirmOk ? (
                      <FieldHint ok text="两次输入一致" />
                    ) : (
                      <FieldHint ok={false} text="两次输入的新密码不一致" />
                    ))}
                </div>
                <button
                  type="submit"
                  disabled={busy || !changePwOk}
                  className={btnPrimary}
                >
                  {busy ? <Spinner /> : <KeyRound size={14} />} 修改密码
                </button>
              </form>
            </div>

            {/* 成员账号 */}
            <div>
              <Section
                icon={<Users size={13} />}
                title={`本机账号（${users?.length ?? "…"} 个，共享同一份学习数据）`}
                extra={
                  <button
                    onClick={() => setShowAdd((v) => !v)}
                    className="flex items-center gap-1 rounded-lg border border-slate-200 px-2 py-1 text-[11px] font-medium text-slate-500 transition hover:border-brand-300 hover:text-brand-600"
                  >
                    <UserPlus size={12} /> 添加账号
                  </button>
                }
              />
              {showAdd && (
                <form onSubmit={register} className="mb-3 space-y-2 rounded-xl border border-dashed border-brand-200 bg-brand-50/40 p-3">
                  <div className="text-[11px] font-medium text-brand-700">添加新成员</div>
                  <div className="grid gap-2 md:grid-cols-4">
                    <div>
                      <input
                        placeholder="新用户名"
                        value={regUsername}
                        onChange={(e) => setRegUsername(e.target.value)}
                        autoComplete="off"
                        className={`${fieldCls(regUserTyped && !regUserOk, regUserOk)} w-full`}
                      />
                      {regUserTyped &&
                        (regUserOk ? (
                          <FieldHint ok text="用户名可用" />
                        ) : (
                          <FieldHint ok={false} text={USERNAME_HINT} />
                        ))}
                    </div>
                    <div>
                      <input
                        type="password"
                        placeholder="密码（至少 6 位）"
                        value={regPassword}
                        onChange={(e) => setRegPassword(e.target.value)}
                        onKeyDown={keyCaps(setRegCaps)}
                        onKeyUp={keyCaps(setRegCaps)}
                        autoComplete="new-password"
                        className={`${fieldCls(regPassword !== "" && !regPwOk, regPwOk)} w-full`}
                      />
                      <PasswordMeter value={regPassword} />
                      {regCaps && <CapsLockHint />}
                      {regPassword !== "" && passwordEqualsUsername(regPassword, regUsername.trim()) && (
                        <FieldHint ok={false} text="密码不能与用户名相同" />
                      )}
                    </div>
                    <div>
                      <input
                        type="password"
                        placeholder="确认密码"
                        value={regConfirm}
                        onChange={(e) => setRegConfirm(e.target.value)}
                        autoComplete="new-password"
                        className={`${fieldCls(regConfirm !== "" && !regConfirmOk, regConfirmOk)} w-full`}
                      />
                      {regConfirm !== "" &&
                        (regConfirmOk ? (
                          <FieldHint ok text="两次输入一致" />
                        ) : (
                          <FieldHint ok={false} text="两次输入的密码不一致" />
                        ))}
                    </div>
                    {registrationContactFields}
                    <button
                      type="submit"
                      disabled={busy || !regFormOk || !regContactsOk || regLocked}
                      className={btnPrimary}
                    >
                      {busy ? <Spinner /> : <UserPlus size={14} />} {regLocked ? `${regRetrySeconds} 秒后可重试` : "创建"}
                    </button>
                  </div>
                </form>
              )}
              <ul className="space-y-1">
                {(users ?? []).map((u) => (
                  <li
                    key={u.id}
                    className="flex items-center justify-between rounded-xl bg-slate-50 px-3 py-2 text-sm text-slate-600"
                  >
                    <span className="flex min-w-0 flex-wrap items-center gap-2">
                      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white text-[10px] font-bold text-brand-600 ring-1 ring-slate-200">
                        {(u.username[0] ?? "?").toUpperCase()}
                      </span>
                      <span className="truncate">{u.username}</span>
                      {user.id === u.id && (
                        <span className="rounded-full bg-brand-100 px-2 py-0.5 text-[10px] font-medium text-brand-700">
                          当前
                        </span>
                      )}
                      {u.last_login_at && (
                        <span className="text-[10px] text-slate-400">
                          最近登录 {new Date(u.last_login_at).toLocaleString()}
                        </span>
                      )}
                    </span>
                    {user.id !== u.id && (
                      <button
                        onClick={() => removeUser(u)}
                        disabled={busy}
                        className="text-slate-300 transition hover:text-red-500 disabled:opacity-50"
                        title="删除账号"
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </div>

            {/* 登录设备 / 会话 */}
            <div>
              <Section
                icon={<MonitorSmartphone size={13} />}
                title={`登录设备（${sessions?.length ?? "…"} 个会话）`}
                extra={
                  (sessions?.length ?? 0) > 1 ? (
                    <button
                      onClick={revokeOthers}
                      disabled={busy}
                      className="flex items-center gap-1 rounded-lg border border-slate-200 px-2 py-1 text-[11px] font-medium text-slate-500 transition hover:border-red-300 hover:text-red-600 disabled:opacity-50"
                    >
                      退出其它设备
                    </button>
                  ) : undefined
                }
              />
              <ul className="space-y-1">
                {(sessions ?? []).map((s) => (
                  <li
                    key={s.id}
                    className="flex items-center justify-between rounded-xl bg-slate-50 px-3 py-2 text-sm text-slate-600"
                  >
                    <span className="flex min-w-0 flex-wrap items-center gap-2">
                      <code className="text-[11px] text-slate-400">{s.id.slice(0, 12)}…</code>
                      {s.current && (
                        <span className="rounded-full bg-brand-100 px-2 py-0.5 text-[10px] font-medium text-brand-700">
                          当前会话
                        </span>
                      )}
                      <span className="text-[11px] text-slate-400">
                        {s.last_seen_at
                          ? `最近活跃 ${new Date(s.last_seen_at).toLocaleString()}`
                          : "从未活跃"}
                      </span>
                    </span>
                    <button
                      onClick={() => revokeSession(s)}
                      disabled={busy}
                      className="text-slate-300 transition hover:text-red-500 disabled:opacity-50"
                      title={s.current ? "撤销当前会话（立即登出）" : "撤销该会话"}
                    >
                      <Trash2 size={14} />
                    </button>
                  </li>
                ))}
              </ul>
            </div>

            {/* 安全日志 */}
            <div>
              <Section
                icon={<ScrollText size={13} />}
                title={`安全日志（最近 ${events?.length ?? "…"} 条）`}
                extra={
                  <button
                    onClick={() => setShowEvents((v) => !v)}
                    className="rounded-lg border border-slate-200 px-2 py-1 text-[11px] font-medium text-slate-500 transition hover:border-brand-300 hover:text-brand-600"
                  >
                    {showEvents ? "收起" : "展开"}
                  </button>
                }
              />
              {showEvents && (
                <ul className="max-h-56 space-y-1 overflow-auto pr-1">
                  {(events ?? []).map((ev) => (
                    <li
                      key={ev.id}
                      className="flex items-center justify-between gap-2 rounded-lg bg-slate-50 px-3 py-1.5 text-[11px] text-slate-500"
                    >
                      <span className="flex min-w-0 flex-wrap items-center gap-1.5">
                        <span
                          className={`rounded px-1.5 py-0.5 font-medium ${
                            ev.kind === "login_fail"
                              ? "bg-red-50 text-red-600"
                              : "bg-slate-100 text-slate-600"
                          }`}
                        >
                          {EVENT_LABELS[ev.kind] ?? ev.kind}
                        </span>
                        {ev.username && <span className="text-slate-600">{ev.username}</span>}
                        {ev.detail && <span className="truncate text-slate-400">{ev.detail}</span>}
                      </span>
                      <span className="shrink-0 text-slate-400">
                        {new Date(ev.created_at).toLocaleString()}
                        {ev.source_ip && ev.source_ip !== "cli" ? ` · ${ev.source_ip}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        ) : (
          /* ---- 未登录：注册首账号 +（已有账号时）登录 ---- */
          <div className="grid gap-4 md:grid-cols-2">
            <form onSubmit={register} className="space-y-2 rounded-xl border border-slate-100 bg-slate-50/60 p-3.5">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-600">
                <UserPlus size={13} className="text-brand-500" />
                注册账号{users && users.length > 0 ? "" : "（首次设置）"}
              </div>
              <div>
                <input
                  placeholder="用户名（2~32 位）"
                  value={regUsername}
                  onChange={(e) => setRegUsername(e.target.value)}
                  autoComplete="off"
                  className={`${fieldCls(regUserTyped && !regUserOk, regUserOk)} w-full`}
                />
                {regUserTyped &&
                  (regUserOk ? (
                    <FieldHint ok text="用户名可用" />
                  ) : (
                    <FieldHint ok={false} text={USERNAME_HINT} />
                  ))}
              </div>
              <div>
                <input
                  type="password"
                  placeholder="密码（至少 6 位）"
                  value={regPassword}
                  onChange={(e) => setRegPassword(e.target.value)}
                  onKeyDown={keyCaps(setRegCaps)}
                  onKeyUp={keyCaps(setRegCaps)}
                  autoComplete="new-password"
                  className={`${fieldCls(regPassword !== "" && !regPwOk, regPwOk)} w-full`}
                />
                <PasswordMeter value={regPassword} />
                {regCaps && <CapsLockHint />}
                {regPassword !== "" && passwordEqualsUsername(regPassword, regUsername.trim()) && (
                  <FieldHint ok={false} text="密码不能与用户名相同" />
                )}
              </div>
              <div>
                <input
                  type="password"
                  placeholder="确认密码"
                  value={regConfirm}
                  onChange={(e) => setRegConfirm(e.target.value)}
                  autoComplete="new-password"
                  className={`${fieldCls(regConfirm !== "" && !regConfirmOk, regConfirmOk)} w-full`}
                />
                {regConfirm !== "" &&
                  (regConfirmOk ? (
                    <FieldHint ok text="两次输入一致" />
                  ) : (
                    <FieldHint ok={false} text="两次输入的密码不一致" />
                  ))}
              </div>
              {registrationContactFields}
              <button
                type="submit"
                disabled={busy || !regFormOk || !regContactsOk || regLocked}
                className={`${btnPrimary} w-full justify-center`}
              >
                {busy ? <Spinner /> : <UserPlus size={14} />} {regLocked ? `${regRetrySeconds} 秒后可重试` : "注册并登录"}
              </button>
            </form>

            {users && users.length > 0 && (
              <form onSubmit={login} className="space-y-2 rounded-xl border border-slate-100 bg-slate-50/60 p-3.5">
                <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-600">
                  <ShieldCheck size={13} className="text-brand-500" />
                  登录已有账号
                </div>
                <input
                  placeholder="用户名 / 手机号 / 微信号 / QQ号"
                  value={loginUsername}
                  onChange={(e) => setLoginUsername(e.target.value)}
                  autoComplete="username"
                  className={`${fieldCls()} w-full`}
                />
                <div className="relative">
                  <input
                    type={showLoginPassword ? "text" : "password"}
                    placeholder="密码"
                    value={loginPassword}
                    onChange={(e) => setLoginPassword(e.target.value)}
                    onKeyDown={keyCaps(setLoginCaps)}
                    onKeyUp={keyCaps(setLoginCaps)}
                    autoComplete="current-password"
                    className={`${fieldCls()} w-full pr-9`}
                  />
                  <button
                    type="button"
                    onClick={() => setShowLoginPassword((v) => !v)}
                    title={showLoginPassword ? "隐藏密码" : "显示密码"}
                    className="absolute inset-y-0 right-0 flex w-8 items-center justify-center text-slate-400 hover:text-slate-600"
                  >
                    {showLoginPassword ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </div>
                {loginCaps && <CapsLockHint />}
                <button
                  type="submit"
                  disabled={busy || !loginUsername.trim() || !loginPassword}
                  className={`${btnPrimary} w-full justify-center`}
                >
                  {busy ? <Spinner /> : <ShieldCheck size={14} />} 登录
                </button>
              </form>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
