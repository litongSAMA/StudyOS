import test from "node:test";
import { spawnSync } from "node:child_process";
import assert from "node:assert/strict";
import { isDeadlineChangeNotice, needsReview, toIso, toLocalInput, parseTags, updateMaterialTags, prepareAgentApply, validateIngestFile } from "../src/agent.ts";

test("deadline change notices enter the original task update flow", () => {
  for (const value of ["实验报告延期到下周三", "作业改为10月15日", "考试提前到明天", "截止时间需要调整"]) assert.equal(isDeadlineChangeNotice(value), true);
  assert.equal(isDeadlineChangeNotice("实验报告10月15日提交"), false);
});

const task = (changes = {}) => ({ title: "实验报告", kind: "lab", due_at: "2026-12-01T23:59:00+08:00", due_expr: "12月1日晚", course: "操作系统", confidence: 0.95, note: "", source_text: "提交实验报告", keep: true, reviewed: false, ...changes });
const material = (changes = {}) => ({ title: "课件", type: "courseware", course: "操作系统", chapter: "第3章", difficulty: "medium", exam_focus: false, tags: ["内存"], summary: "内存管理", confidence: 0.95, keep: true, reviewed: false, ...changes });

test("editing tags changes only the target material, without mutating originals", () => {
  const rows = [material(), material({ title: "讲义", tags: ["进程"] })];
  const edited = updateMaterialTags(rows, 0, " 内存，考点，内存 ");
  assert.deepEqual(edited[0].tags, ["内存", "考点"]);
  assert.equal(edited[1], rows[1]);
  assert.deepEqual(rows[0].tags, ["内存"]);
});
test("tags support Chinese/English separators, trim and deduplicate", () => assert.deepEqual(parseTags(" 内存,考点；复习;内存\n标签， "), ["内存", "考点", "复习", "标签"]));
test("blank tags can be cleared", () => assert.deepEqual(parseTags("， ;\n"), []));
test("timezone offset becomes the same UTC instant", () => assert.equal(toIso("2026-12-01T23:59:00+08:00"), "2026-12-01T15:59:00.000Z"));
test("local input roundtrips through UTC without timezone drift", () => assert.equal(toIso(toLocalInput("2026-12-01T15:59:00.000Z")), "2026-12-01T15:59:00.000Z"));
test("invalid/empty dates never render NaN", () => { for (const value of [null, "", "invalid", "123", "2026-02-30T12:00", "2026-13-01T12:00", "2026-01-00T12:00", "2026-01-01T24:00", "2026-01-01T12:60"]) { assert.equal(toIso(value), null, value); assert.equal(toLocalInput(value), "", value); } });
test("leap days are validated", () => { assert.ok(toIso("2028-02-29T12:00")); assert.equal(toIso("2026-02-29T12:00"), null); });
test("confidence boundary and malformed confidence need explicit review", () => { assert.equal(needsReview(0.85), false); for (const confidence of [0.8499, 0, -1, NaN, Infinity, 1.1]) assert.equal(needsReview(confidence), true); });
test("no selection is blocked", () => assert.match(prepareAgentApply([task({ keep: false })], [material({ keep: false })]).error, /至少/));
test("blank selected titles are blocked", () => assert.match(prepareAgentApply([task({ title: "  " })], []).error, /标题不能为空/));
test("invalid selected deadline is blocked", () => assert.match(prepareAgentApply([task({ due_at: "invalid" })], []).error, /有效截止时间/));
test("unselected invalid suggestions do not block valid ones", () => assert.ok(prepareAgentApply([task({ keep: false, due_at: null, title: "" })], [material()]).payload));
test("low confidence task is blocked before review", () => assert.match(prepareAgentApply([task({ confidence: 0.4 })], []).error, /已核对/));
test("low confidence material is blocked before review", () => assert.match(prepareAgentApply([], [material({ confidence: 0.4 })]).error, /已核对/));
test("reviewed low confidence suggestions are accepted", () => assert.ok(prepareAgentApply([task({ confidence: 0.4, reviewed: true })], [material({ confidence: 0.4, reviewed: true })]).payload));
test("exact duplicate tasks are blocked even with different timestamp notation", () => assert.match(prepareAgentApply([task(), task({ due_at: "2026-12-01T15:59:00Z" })], []).error, /重复/));
test("tasks with distinct deadlines or courses remain separate", () => assert.equal(prepareAgentApply([task(), task({ due_at: "2026-12-02T23:59:00+08:00" }), task({ course: "数据库" })], []).payload.tasks.length, 3));
test("exact duplicate materials are blocked", () => assert.match(prepareAgentApply([], [material(), material()]).error, /重复/));
test("different chapters remain separate materials", () => assert.equal(prepareAgentApply([], [material(), material({ chapter: "第4章" })]).payload.materials.length, 2));
test("normalized payload omits UI fields and preserves source data", () => {
  const result = prepareAgentApply([task({ title: " 实验报告 ", course: " 操作系统 " })], [material({ chapter: " 第3章 ", tags: [" 内存 ", "内存", ""] })]).payload;
  assert.equal(result.tasks[0].title, "实验报告");
  assert.equal(result.tasks[0].course, "操作系统");
  assert.equal(result.tasks[0].source_text, "提交实验报告");
  assert.equal(result.materials[0].chapter, "第3章");
  assert.deepEqual(result.materials[0].tags, ["内存"]);
  for (const row of [...result.tasks, ...result.materials]) { assert.equal("keep" in row, false); assert.equal("reviewed" in row, false); }
});
test("empty and unsupported files are rejected", () => { assert.match(validateIngestFile({ name: "notes.txt", size: 0 }), /为空/); assert.match(validateIngestFile({ name: "notes.exe", size: 10 }), /不支持/); });
test("supported extensions are case insensitive", () => { for (const name of ["课件.PDF", "notes.md", "截图.PNG", "课件.pptx", "报告.docx"]) assert.equal(validateIngestFile({ name, size: 10 }), ""); });

test("DST gap deadlines are rejected rather than silently shifted", () => {
  const helperUrl = new URL("../src/agent.ts", import.meta.url).href;
  const script = `import { toIso } from ${JSON.stringify(helperUrl)}; console.log(JSON.stringify([toIso("2026-03-08T02:30"), toIso("2026-03-08T03:30")]));`;
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", script], { env: { ...process.env, TZ: "America/Los_Angeles" }, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), [null, "2026-03-08T10:30:00.000Z"]);
});
