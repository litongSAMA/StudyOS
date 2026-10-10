import test from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_SETTINGS, normalizeSettings, loadSettings } from "../src/focusSettings.ts";

test("invalid persisted settings recover to usable defaults", () => {
  for (const value of [null, [], "bad", 123, { focus: "abc", short: NaN, long: Infinity, longEvery: null }]) {
    assert.deepEqual(normalizeSettings(value), DEFAULT_SETTINGS);
  }
});
test("custom durations and preferences survive normalization", () => {
  const custom = { ...DEFAULT_SETTINGS, focus: 37, short: 7, long: 20, longEvery: 3, sound: false, autoStartBreak: true };
  assert.deepEqual(normalizeSettings(custom), custom);
});
test("durations stay within API-compatible whole-minute limits", () => {
  const result = normalizeSettings({ focus: 10000, short: -5, long: 19.8, longEvery: 100 });
  assert.deepEqual([result.focus, result.short, result.long, result.longEvery], [240, 1, 20, 12]);
});
test("malformed boolean preferences never enable auto start or notifications", () => {
  assert.deepEqual(normalizeSettings({ autoStartFocus: "false", autoStartBreak: 1, notify: "true", sound: null }), DEFAULT_SETTINGS);
});
test("normalizing does not mutate saved objects or shared defaults", () => {
  const saved = { focus: 999 }; const result = normalizeSettings(saved); result.short = 10;
  assert.equal(saved.focus, 999); assert.equal(DEFAULT_SETTINGS.short, 5);
});
test("loading malformed or unavailable browser storage falls back safely", () => {
  const previous = globalThis.localStorage;
  try {
    globalThis.localStorage = { getItem: () => '{broken' };
    assert.deepEqual(loadSettings(), DEFAULT_SETTINGS);
    globalThis.localStorage = { getItem: () => { throw new Error("storage denied"); } };
    assert.deepEqual(loadSettings(), DEFAULT_SETTINGS);
    globalThis.localStorage = { getItem: () => JSON.stringify({ focus: 37, short: 7 }) };
    assert.equal(loadSettings().focus, 37); assert.equal(loadSettings().short, 7);
  } finally { if (previous === undefined) delete globalThis.localStorage; else globalThis.localStorage = previous; }
});
