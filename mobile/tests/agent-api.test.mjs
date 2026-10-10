import test from "node:test";
import assert from "node:assert/strict";
import { finishWrite, loadPendingWrite } from "../src/agentWrites.ts";
import { apiAgent, apiMaterials, ApiHttpError, ApiNetworkError } from "../src/api.ts";

test.beforeEach(() => { const pending = loadPendingWrite(); if (pending) finishWrite(pending.key); });
test.afterEach(() => { const pending = loadPendingWrite(); if (pending) finishWrite(pending.key); });

test("extract forwards cancellation signal and request input", async (t) => {
  const controller = new AbortController();
  t.mock.method(globalThis, "fetch", async (url, init) => {
    assert.equal(url, "/api/agent/extract");
    assert.equal(init.signal, controller.signal);
    assert.deepEqual(JSON.parse(init.body), { text: "明天交报告", course_hint: "操作系统" });
    return Response.json({ tasks: [], materials: [], warnings: [], engine: "rule" });
  });
  assert.equal((await apiAgent.extract("明天交报告", "操作系统", controller.signal)).engine, "rule");
});
test("upload forwards signal, file and course hint", async (t) => {
  const controller = new AbortController();
  t.mock.method(globalThis, "fetch", async (url, init) => {
    assert.equal(url, "/api/materials/upload");
    assert.equal(init.signal, controller.signal);
    assert.equal(init.body.get("file").name, "课件.txt");
    assert.equal(init.body.get("course_hint"), "操作系统");
    return Response.json({ extract: { tasks: [], materials: [], warnings: [], engine: "rule" } });
  });
  await apiMaterials.upload(new File(["内存管理"], "课件.txt"), "操作系统", controller.signal);
});
test("cancellation is preserved rather than reported as connection failure", async (t) => {
  t.mock.method(globalThis, "fetch", async () => { throw new DOMException("Cancelled", "AbortError"); });
  await assert.rejects(apiAgent.extract("通知"), { name: "AbortError" });
});
test("network failures are classified and never automatically retried", async (t) => {
  const mock = t.mock.method(globalThis, "fetch", async () => { throw new TypeError("Connection reset"); });
  await assert.rejects(apiAgent.apply({ tasks: [], materials: [] }), ApiNetworkError);
  assert.equal(mock.mock.callCount(), 1);
});
test("validation errors show field path and message", async (t) => {
  t.mock.method(globalThis, "fetch", async () => Response.json({ detail: [{ loc: ["body", "tasks", 0, "due_at"], msg: "截止时间无效" }] }, { status: 422 }));
  await assert.rejects(apiAgent.apply({ tasks: [], materials: [] }), (error) => error instanceof ApiHttpError && error.status === 422 && error.message === "body.tasks.0.due_at: 截止时间无效");
});
test("rate limit response retains Retry-After and sends only one request", async (t) => {
  const mock = t.mock.method(globalThis, "fetch", async () => Response.json({ detail: "稍后再试" }, { status: 429, headers: { "Retry-After": "30" } }));
  await assert.rejects(apiAgent.extract("通知"), (error) => error instanceof ApiHttpError && error.retryAfter === 30 && error.message === "稍后再试");
  assert.equal(mock.mock.callCount(), 1);
});
