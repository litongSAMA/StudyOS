export interface FocusSettings {
  focus: number;
  short: number;
  long: number;
  longEvery: number;
  autoStartBreak: boolean;
  autoStartFocus: boolean;
  sound: boolean;
  notify: boolean;
}

export const SETTINGS_KEY = "studyos.pomodoro.settings.v1";
export const DEFAULT_SETTINGS: FocusSettings = {
  focus: 25, short: 5, long: 15, longEvery: 4,
  autoStartBreak: false, autoStartFocus: false, sound: true, notify: false,
};
export const DURATION_FIELDS = [
  ["focus", "专注时长", 1, 240, "分钟"],
  ["short", "短休息", 1, 60, "分钟"],
  ["long", "长休息", 1, 120, "分钟"],
  ["longEvery", "长休息间隔", 1, 12, "个专注"],
] as const;
export const PRESETS = [
  { label: "轻量 15/5", focus: 15, short: 5, long: 15 },
  { label: "经典 25/5", focus: 25, short: 5, long: 15 },
  { label: "学习 45/5", focus: 45, short: 5, long: 15 },
  { label: "冲刺 50/10", focus: 50, short: 10, long: 30 },
  { label: "长跑 60/10", focus: 60, short: 10, long: 30 },
];

// Storage is untrusted: invalid values must never produce NaN timers or unlimited cycles.
export function normalizeSettings(value: unknown): FocusSettings {
  const result = { ...DEFAULT_SETTINGS };
  if (!value || typeof value !== "object" || Array.isArray(value)) return result;
  const source = value as Record<string, unknown>;
  for (const [key, , min, max] of DURATION_FIELDS) {
    const n = source[key];
    if (typeof n === "number" && Number.isFinite(n)) result[key] = Math.min(max, Math.max(min, Math.round(n)));
  }
  for (const key of ["autoStartBreak", "autoStartFocus", "sound", "notify"] as const) {
    if (typeof source[key] === "boolean") result[key] = source[key];
  }
  return result;
}

export function loadSettings(): FocusSettings {
  try { return normalizeSettings(JSON.parse(localStorage.getItem(SETTINGS_KEY) || "null")); }
  catch { return { ...DEFAULT_SETTINGS }; }
}
