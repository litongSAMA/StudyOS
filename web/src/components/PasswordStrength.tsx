import { AlertTriangle } from "lucide-react";

/**
 * 登录 / 注册 / 改密共用的密码与用户名校验工具。
 *
 * 规则与后端（backend/app/auth.py）保持一致：
 * - 用户名 2~32 位，首字符为字母/数字/下划线/中文，其后可含空格、点、短横线；
 * - 密码 6~128 位，且不能与用户名相同（大小写不敏感）。
 */

/** 与后端一致的用户名校验 */
export function validUsername(name: string): boolean {
  return /^[A-Za-z0-9_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff .\-]{1,31}$/.test(
    name,
  );
}

export const USERNAME_HINT = "2~32 位，以字母/数字/下划线/中文开头，可含空格、点、短横线";

/** 密码是否满足后端最低要求（6~128 位） */
export function validPassword(pw: string): boolean {
  return pw.length >= 6 && pw.length <= 128;
}

/** 密码与用户名相同（大小写不敏感）——后端会直接拒绝 */
export function passwordEqualsUsername(pw: string, username: string): boolean {
  return pw.toLowerCase() === (username || "").trim().toLowerCase();
}

/**
 * 密码强度评分 0~4：
 * 1 长度达标 → +1；10 位以上 → +1；含大小写 → +1；含数字或符号 → +1。
 */
export function strengthOf(pw: string): number {
  if (!pw) return 0;
  let s = 0;
  if (pw.length >= 6) s += 1;
  if (pw.length >= 10) s += 1;
  if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) s += 1;
  if (/\d/.test(pw) || /[^A-Za-z0-9]/.test(pw)) s += 1;
  return Math.min(s, 4);
}

const LEVELS: Record<number, { label: string; bar: string; text: string; hint: string }> = {
  1: { label: "弱", bar: "bg-red-400", text: "text-red-500", hint: "建议 10 位以上更安全" },
  2: { label: "较弱", bar: "bg-orange-400", text: "text-orange-500", hint: "建议加入大小写字母或数字" },
  3: { label: "中", bar: "bg-amber-400", text: "text-amber-600", hint: "大小写 + 数字/符号会更稳" },
  4: { label: "强", bar: "bg-emerald-500", text: "text-emerald-600", hint: "密码强度良好" },
};

/** 密码强度条：输入非空时展示 4 段进度 + 强度标签与建议 */
export function PasswordMeter({ value }: { value: string }) {
  const s = strengthOf(value);
  if (!value) return null;
  const lv = LEVELS[s] ?? LEVELS[1];
  return (
    <div className="mt-1.5" aria-live="polite">
      <div className="flex gap-1">
        {[1, 2, 3, 4].map((i) => (
          <span
            key={i}
            className={`h-1 flex-1 rounded-full transition-colors duration-300 ${i <= s ? lv.bar : "bg-slate-200"}`}
          />
        ))}
      </div>
      <div className={`mt-1 flex items-center gap-1 text-[10px] ${lv.text}`}>
        <span className="font-medium">强度：{lv.label}</span>
        <span className="text-slate-400">· {lv.hint}</span>
      </div>
    </div>
  );
}

/** CapsLock 检测（键盘事件上调用；不支持的环境返回 false） */
export function capsLockOn(e: {
  getModifierState?: (key: "CapsLock") => boolean;
}): boolean {
  try {
    return e.getModifierState?.("CapsLock") ?? false;
  } catch {
    return false;
  }
}

/** CapsLock 提示条（密码框内常驻监听时展示） */
export function CapsLockHint() {
  return (
    <div className="mt-1 flex items-center gap-1 text-[10px] text-amber-600">
      <AlertTriangle size={11} className="shrink-0" />
      大写锁定已开启（CapsLock），注意密码大小写
    </div>
  );
}
