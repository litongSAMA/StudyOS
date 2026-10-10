import test from "node:test";
import assert from "node:assert/strict";
import {
  COURSE_PALETTE,
  MAX_WEEKS,
  mergeSlots,
  parseWeeks,
  weeksHint,
} from "../src/lib/courses.ts";

/** 与后端 courseimport.parse_weeks 的用例同形：手动新增课表的周次口径必须与导入一致 */
test("parseWeeks：连周/带「周」字/「到」字都归一化成升序去重列表", () => {
  assert.deepEqual(parseWeeks("1-16"), [...Array(16)].map((_, i) => i + 1));
  assert.deepEqual(parseWeeks("1-16周"), [...Array(16)].map((_, i) => i + 1));
  assert.deepEqual(parseWeeks("第1-16周"), [...Array(16)].map((_, i) => i + 1));
  assert.deepEqual(parseWeeks("1到3"), [1, 2, 3]);
});

test("parseWeeks：单双周取奇偶（起点自动对齐，与后端同规则）", () => {
  assert.deepEqual(parseWeeks("1-16单"), [1, 3, 5, 7, 9, 11, 13, 15]);
  assert.deepEqual(parseWeeks("1-16双"), [2, 4, 6, 8, 10, 12, 14, 16]);
  assert.deepEqual(parseWeeks("2-16单"), [3, 5, 7, 9, 11, 13, 15]);
  assert.deepEqual(parseWeeks("1-16（单）"), [1, 3, 5, 7, 9, 11, 13, 15]);
});

test("parseWeeks：逗号分段、乱序、重复与中文顿号都能合并去重", () => {
  assert.deepEqual(parseWeeks("1,3,5-8"), [1, 3, 5, 6, 7, 8]);
  assert.deepEqual(parseWeeks("8-6"), [6, 7, 8]);
  assert.deepEqual(parseWeeks("3,1,3、5"), [1, 3, 5]);
  assert.deepEqual(parseWeeks(" 1 - 3 , 5 "), [1, 2, 3, 5]);
});

test("parseWeeks：空/每周/全周 = 每周都上（空数组）", () => {
  for (const value of ["", "   ", "每周", "全周", "ALL", "all"]) {
    assert.deepEqual(parseWeeks(value), []);
  }
});

test("parseWeeks：读不懂的写法抛出可直接展示的中文提示", () => {
  assert.throws(() => parseWeeks("abc"), /周次写法无法识别/);
  assert.throws(() => parseWeeks("1-abc"), /周次写法无法识别/);
  assert.throws(() => parseWeeks("0-3"), /周次 0 超出/);
  assert.throws(() => parseWeeks(String(MAX_WEEKS + 1)), /超出 1~60 范围/);
});

test("weeksHint：每周/单周/双周/离散周次都给人话", () => {
  assert.equal(weeksHint([]), "每周");
  assert.equal(weeksHint([1, 3, 5, 7, 9, 11, 13, 15]), "单周（第1-15周）");
  assert.equal(weeksHint([2, 4, 6, 8]), "双周（第2-8周）");
  assert.equal(weeksHint([1, 2, 3, 4]), "第1-4周");
  assert.equal(weeksHint([1, 3, 5, 6, 7, 8]), "第1,3,5-8周");
});

const slot = (weekday, start, end, weeks = [], location = "", teacher = "") => ({
  weekday, start, end, weeks, location, teacher,
});

test("mergeSlots：同「星期+起止」并集周次，不重复堆时段（与后端同口径）", () => {
  const existing = [slot(1, "08:00", "09:40", [1, 3, 5], "N-A103", "张老师")];
  const merged = mergeSlots(existing, [slot(1, "08:00", "09:40", [2, 3, 4])]);
  assert.equal(merged.length, 1);
  assert.deepEqual(merged[0].weeks, [1, 2, 3, 4, 5]);
  assert.equal(merged[0].location, "N-A103");
  assert.equal(merged[0].teacher, "张老师");
});

test("mergeSlots：补空的老师/地点，但不覆盖已有值", () => {
  const merged = mergeSlots(
    [slot(2, "10:00", "11:40", [], "", "")],
    [slot(2, "10:00", "11:40", [], "I-101", "钱老师")],
  );
  assert.equal(merged[0].location, "I-101");
  assert.equal(merged[0].teacher, "钱老师");
  const kept = mergeSlots(
    [slot(2, "10:00", "11:40", [], "I-101", "钱老师")],
    [slot(2, "10:00", "11:40", [], "别处", "别人")],
  );
  assert.equal(kept[0].location, "I-101");
  assert.equal(kept[0].teacher, "钱老师");
});

test("mergeSlots：时间或星期不同则追加，且不改动传入的原数组", () => {
  const existing = [slot(1, "08:00", "09:40", [1])];
  const merged = mergeSlots(existing, [slot(1, "10:00", "11:40", [1]), slot(3, "08:00", "09:40", [])]);
  assert.equal(merged.length, 3);
  assert.equal(existing.length, 1);
  assert.deepEqual(existing[0].weeks, [1]);
  assert.deepEqual(merged.map((s) => s.weekday), [1, 1, 3]);
});

test("mergeSlots：空课表时等同新增，空周次（每周）不与具体周次互相污染", () => {
  assert.deepEqual(mergeSlots([], [slot(5, "14:00", "15:40", [1, 2])])[0].weeks, [1, 2]);
  const merged = mergeSlots([slot(5, "14:00", "15:40", [])], [slot(5, "14:00", "15:40", [7])]);
  assert.deepEqual(merged[0].weeks, [7]);
});

test("COURSE_PALETTE：与后端导入配色同一套 10 色，可直接用于调色板", () => {
  assert.equal(COURSE_PALETTE.length, 10);
  assert.equal(COURSE_PALETTE[0], "#6366f1");
  assert.equal(new Set(COURSE_PALETTE).size, 10);
  for (const color of COURSE_PALETTE) assert.match(color, /^#[0-9a-f]{6}$/);
});
