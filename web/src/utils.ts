/** 日期时间格式化与相对时间。 */
export function fmtDT(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate()
  ).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(
    d.getMinutes()
  ).padStart(2, "0")}`;
}

export function fmtDate(iso: string): string {
  const d = new Date(iso);
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

export function dueLabel(dueIso: string): { text: string; cls: string } {
  const due = new Date(dueIso).getTime();
  const now = Date.now();
  const diffH = (due - now) / 3_600_000;
  if (diffH < 0) {
    const days = Math.floor(-diffH / 24);
    return {
      text: days > 0 ? `已逾期 ${days} 天` : `已逾期 ${Math.floor(-diffH)} 小时`,
      cls: "text-red-600",
    };
  }
  if (diffH < 24) return { text: `剩 ${Math.floor(diffH)} 小时`, cls: "text-red-600" };
  const days = Math.floor(diffH / 24);
  if (days <= 3) return { text: `剩 ${days} 天`, cls: "text-amber-600" };
  if (days <= 14) return { text: `剩 ${days} 天`, cls: "text-slate-500" };
  return { text: `剩 ${days} 天`, cls: "text-slate-400" };
}

const MIN_MS = 60_000;
const HOUR_MS = 60 * MIN_MS;
const DAY_MS = 24 * HOUR_MS;

/** 精确倒计时（精确到分钟），用于时间轴：返回剩余/逾期文案与紧迫度配色。 */
export function countdown(
  dueIso: string,
  now: number = Date.now()
): { text: string; cls: string; overdue: boolean } {
  const diff = new Date(dueIso).getTime() - now;
  const abs = Math.abs(diff);

  const cls = diff < 0
    ? "text-red-600"
    : diff < DAY_MS
      ? "text-red-600"
      : diff <= 3 * DAY_MS
        ? "text-amber-600"
        : diff <= 14 * DAY_MS
          ? "text-slate-600"
          : "text-slate-400";

  if (diff < 0) {
    const d = Math.floor(abs / DAY_MS);
    const h = Math.floor((abs % DAY_MS) / HOUR_MS);
    if (d > 0) return { text: `已逾期 ${d} 天 ${h} 小时`, cls, overdue: true };
    if (h > 0) return { text: `已逾期 ${h} 小时`, cls, overdue: true };
    return { text: `已逾期 ${Math.max(Math.floor(abs / MIN_MS), 1)} 分钟`, cls, overdue: true };
  }

  const d = Math.floor(diff / DAY_MS);
  const h = Math.floor((diff % DAY_MS) / HOUR_MS);
  const m = Math.floor((diff % HOUR_MS) / MIN_MS);
  if (d > 0) return { text: `剩 ${d} 天 ${h} 小时`, cls, overdue: false };
  if (h > 0) return { text: `剩 ${h} 小时 ${m} 分`, cls, overdue: false };
  return { text: `剩 ${Math.max(m, 1)} 分钟`, cls, overdue: false };
}

export function courseColor(seed: string): string {
  const palette = [
    "#6366f1", "#0ea5e9", "#10b981", "#f59e0b",
    "#ef4444", "#8b5cf6", "#ec4899", "#14b8a6",
  ];
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  return palette[h % palette.length];
}
