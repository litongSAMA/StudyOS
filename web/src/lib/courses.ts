import type { CourseSlot } from "../types";

/**
 * 课表领域的纯函数：周次解析、周次回显、时段合并。
 * 口径与后端 `courseimport.parse_weeks` / `merge_slots` 保持一致——
 * 手动新增（课表页「新增课程」）与文件导入走同一套周次/合并规则。
 * 单独成文件（不含 JSX）便于 `node --test` 直接跑用例。
 */

/** 与后端 courseimport.COURSE_PALETTE 同一套配色：手动新增的课程与导入的观感一致 */
export const COURSE_PALETTE = [
  "#6366f1", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444",
  "#8b5cf6", "#ec4899", "#14b8a6", "#f97316", "#84cc16",
];

/** 周次上限（与后端 courseimport.MAX_WEEKS 一致） */
export const MAX_WEEKS = 60;

/**
 * 周次文本 → 升序去重列表：
 * 支持「1-16」「1-16单」「1-16双」「1,3,5-8」「第1-16周」「1到3」；
 * 空 /「每周」/「全周」= 每周都上（空数组）；写法读不懂时抛错（消息可直接展示给用户）。
 */
export function parseWeeks(text: string): number[] {
  let s = (text || "").trim().replace(/[到至]/g, "-");
  if (!s || ["全周", "每周", "all"].includes(s.toLowerCase())) return [];
  let odd = false;
  let even = false;
  if (/[（(]?单(?:周)?[）)]?$/.test(s)) {
    odd = true;
    s = s.replace(/[（(]?单(?:周)?[）)]?$/, "");
  } else if (/[（(]?双(?:周)?[）)]?$/.test(s)) {
    even = true;
    s = s.replace(/[（(]?双(?:周)?[）)]?$/, "");
  }
  s = s.replace(/[第周]/g, "");
  const out = new Set<number>();
  const add = (n: number) => {
    if (!Number.isInteger(n) || n < 1 || n > MAX_WEEKS) {
      throw new Error(`周次 ${n} 超出 1~${MAX_WEEKS} 范围`);
    }
    out.add(n);
  };
  for (const raw of s.split(/[,，、]/)) {
    const token = raw.trim();
    if (!token) throw new Error("请填写有效周次，如 1,3,5-8");
    const range = /^(\d+)\s*-\s*(\d+)$/.exec(token);
    if (range) {
      let a = Number(range[1]);
      let b = Number(range[2]);
      // 在展开范围前验证两端，避免超大范围循环或奇偶过滤掩盖越界。
      for (const n of [a, b]) {
        if (!Number.isInteger(n) || n < 1 || n > MAX_WEEKS) {
          throw new Error(`周次 ${n} 超出 1~${MAX_WEEKS} 范围`);
        }
      }
      if (a > b) [a, b] = [b, a];
      // 单/双周：从第一个符合奇偶的周开始（与后端一致）
      if (odd && a % 2 === 0) a += 1;
      if (even && a % 2 === 1) a += 1;
      for (let n = a; n <= b; n += odd || even ? 2 : 1) add(n);
      continue;
    }
    if (!/^\d+$/.test(token)) throw new Error(`周次写法无法识别：${(text || "").trim()}`);
    add(Number(token));
  }
  let weeks = [...out];
  if (odd) weeks = weeks.filter((n) => n % 2 === 1);
  if (even) weeks = weeks.filter((n) => n % 2 === 0);
  if (!weeks.length) throw new Error("请至少选择一个上课周次");
  return weeks.sort((a, b) => a - b);
}

/** 只将连续的隔周序列称为单双周，离散的自定义周次保留准确列表。 */
export function isAlternatingWeeks(weeks: number[]): boolean {
  return weeks.length > 1 && weeks.every((w, i) => i === 0 || w === weeks[i - 1] + 2);
}

/** 周次列表 → 「每周 / 单周 / 双周 / 第1-16周」人话（表单里的解析回显用） */
export function weeksHint(weeks: number[]): string {
  weeks = [...new Set(weeks)].sort((a, b) => a - b);
  if (!weeks.length) return "每周";
  const last = weeks[weeks.length - 1];
  if (isAlternatingWeeks(weeks)) return `${weeks[0] % 2 ? "单" : "双"}周（第${weeks[0]}-${last}周）`;
  const parts: string[] = [];
  let i = 0;
  while (i < weeks.length) {
    let j = i;
    while (j + 1 < weeks.length && weeks[j + 1] === weeks[j] + 1) j++;
    parts.push(j > i ? `${weeks[i]}-${weeks[j]}` : `${weeks[i]}`);
    i = j + 1;
  }
  return `第${parts.join(",")}周`;
}

/** 把新时段并入已有课表（与后端 merge_slots 同一口径）：同「星期+起止」并集周次，其余追加 */
export function mergeSlots(existing: CourseSlot[], incoming: CourseSlot[]): CourseSlot[] {
  const merged: CourseSlot[] = (existing || []).map((s) => ({ ...s }));
  for (const s of incoming) {
    const hit = merged.find(
      (m) => m.weekday === s.weekday && m.start === s.start && m.end === s.end,
    );
    if (!hit) {
      merged.push({ ...s });
      continue;
    }
    hit.weeks = [...new Set([...(hit.weeks || []), ...(s.weeks || [])])].sort((a, b) => a - b);
    if (!hit.location) hit.location = s.location;
    if (!hit.teacher) hit.teacher = s.teacher;
  }
  return merged;
}
