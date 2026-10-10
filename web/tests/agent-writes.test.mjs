import test from "node:test";
import assert from "node:assert/strict";
import { beginWrite, finishWrite, loadPendingWrite } from "../src/agentWrites.ts";
import { apiAgent, ApiNetworkError } from "../src/api.ts";

test.beforeEach(() => { const pending = loadPendingWrite(); if (pending) finishWrite(pending.key); });
test.afterEach(() => { const pending = loadPendingWrite(); if (pending) finishWrite(pending.key); });

test("pending payload is a deep snapshot of original input", () => {
  const input = { tasks: [{ title: "报告" }], materials: [] };
  const write = beginWrite("/agent/apply", input);
  input.tasks[0].title = "被修改的标题";
  assert.equal(write.body.tasks[0].title, "报告");
});
test("another write cannot replace a pending request", () => {
  beginWrite("/agent/apply", { tasks: [], materials: [] });
  assert.throws(() => beginWrite("/agent/apply", { tasks: [{ title: "另一个" }] }), /待确认/);
});
test("only the matching receipt can clear a pending write", () => {
  const write = beginWrite("/agent/apply", {});
  finishWrite("wrong-key");
  assert.equal(loadPendingWrite().key, write.key);
  finishWrite(write.key);
  assert.equal(loadPendingWrite(), null);
});
test("lost response retries exactly the original key and body", async (t) => {
  const requests = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    if(url.endsWith("/capabilities")) return Response.json({});
    requests.push({ url, key: new Headers(init.headers).get("Idempotency-Key"), body: init.body });
    if (requests.length === 1) throw new TypeError("Response lost after commit");
    return Response.json({ operation_id: "original", replayed: true });
  });
  await assert.rejects(apiAgent.apply({ tasks: [], materials: [] }), ApiNetworkError);
  assert.equal(requests.length, 1);
  assert.ok(loadPendingWrite());
  const receipt = await apiAgent.retryPending();
  assert.deepEqual(requests[0], requests[1]);
  assert.equal(receipt.operation_id, "original");
  assert.equal(loadPendingWrite(), null);
});
test("receipt lookup clears original pending request", async (t) => {
  const write = beginWrite("/agent/apply", {});
  t.mock.method(globalThis, "fetch", async (url) => {
    assert.equal(url, `/api/agent/receipts/${write.key}`);
    return Response.json({ operation_id: "original" });
  });
  await apiAgent.receipt(write.key);
  assert.equal(loadPendingWrite(), null);
});
test("failed receipt lookup preserves pending request", async (t) => {
  const write = beginWrite("/agent/apply", {});
  t.mock.method(globalThis, "fetch", async () => Response.json({ detail: "Not found" }, { status: 404 }));
  await assert.rejects(apiAgent.receipt(write.key));
  assert.equal(loadPendingWrite().key, write.key);
});
test("known validation rejection clears pending request", async (t) => {
  t.mock.method(globalThis, "fetch", async url => url.endsWith("/capabilities") ? Response.json({}) : Response.json({ detail: "Invalid" }, { status: 422 }));
  await assert.rejects(apiAgent.apply({ tasks: [], materials: [] }));
  assert.equal(loadPendingWrite(), null);
});
test("server error preserves pending request for safe recovery", async (t) => {
  t.mock.method(globalThis, "fetch", async url => url.endsWith("/capabilities") ? Response.json({}) : Response.json({ detail: "Unavailable" }, { status: 503 }));
  await assert.rejects(apiAgent.apply({ tasks: [], materials: [] }));
  assert.ok(loadPendingWrite());
});
test("write survives a refreshed module through session storage", async (t) => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage");
  const store = new Map();
  Object.defineProperty(globalThis, "sessionStorage", { configurable: true, value: { getItem: (key) => store.get(key) ?? null, setItem: (key, value) => store.set(key, value), removeItem: (key) => store.delete(key) } });
  t.after(() => { if (original) Object.defineProperty(globalThis, "sessionStorage", original); else delete globalThis.sessionStorage; });
  const write = beginWrite("/agent/apply", { tasks: [{ title: "报告" }] });
  const refreshed = await import(`../src/agentWrites.ts?refresh=${Date.now()}`);
  assert.equal(refreshed.loadPendingWrite().key, write.key);
  assert.deepEqual(refreshed.loadPendingWrite().body, write.body);
  finishWrite(write.key);
});
