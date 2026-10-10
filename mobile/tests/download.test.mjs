// 下载通道测试：导出文件必须走「带令牌的 fetch + Blob」，并且 URL 不能出现 /api/api/…
// （历史 bug：<a href="/api/export/..."> 被 Service Worker 的 SPA 回退吞成 index.html，点了没反应）
import test from "node:test";
import assert from "node:assert/strict";
import {
  ApiHttpError,
  downloadFile,
  exportUrl,
  filenameFromResponse,
} from "../src/api.ts";

/** 记录本次「落盘」的文件名与 URL */
function mockBrowser() {
  const saved = [];
  const store = new Map();
  globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };
  globalThis.URL.createObjectURL = () => "blob:studyos-mock";
  globalThis.URL.revokeObjectURL = () => {};
  globalThis.document = {
    body: { appendChild() {} },
    createElement() {
      const el = {
        click() { saved.push({ href: el.href, download: el.download }); },
        remove() {},
      };
      return el;
    },
  };
  return saved;
}

test.afterEach(() => {
  delete globalThis.localStorage;
  delete globalThis.document;
  delete globalThis.fetch;
});

test("filenameFromResponse：解析服务端 Content-Disposition 里的文件名", () => {
  const r = new Response("x", {
    headers: { "Content-Disposition": 'attachment; filename="studyos-tasks.csv"' },
  });
  assert.equal(filenameFromResponse(r, "fallback.csv"), "studyos-tasks.csv");
});

test("filenameFromResponse：支持 UTF-8 编码的中文文件名，缺头时回落兜底名", () => {
  const cn = new Response("x", {
    headers: { "Content-Disposition": "attachment; filename*=UTF-8''%E5%AF%BC%E5%87%BA.md" },
  });
  assert.equal(filenameFromResponse(cn, "fallback.md"), "导出.md");
  assert.equal(filenameFromResponse(new Response("x"), "fallback.md"), "fallback.md");
});

test("downloadFile：exportUrl() 的完整地址被归一化，不会打成 /api/api/…", async () => {
  const saved = mockBrowser();
  let requested = "";
  globalThis.fetch = async (url) => {
    requested = String(url);
    return new Response("ID,课程\n", {
      status: 200,
      headers: {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": 'attachment; filename="studyos-tasks.csv"',
      },
    });
  };
  const name = await downloadFile(exportUrl("tasks", "csv", { incremental: true }), "studyos-tasks.csv");
  assert.equal(requested, "/api/export/tasks/csv?incremental=true");
  assert.equal(name, "studyos-tasks.csv");
  assert.deepEqual(saved, [{ href: "blob:studyos-mock", download: "studyos-tasks.csv" }]);
});

test("downloadFile：失败时抛出可展示的错误（后端未启动 / 404 等）", async () => {
  const saved = mockBrowser();
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: "Not Found" }), {
    status: 404,
    headers: { "Content-Type": "application/json" },
  });
  await assert.rejects(
    () => downloadFile("/export/tasks/csv", "studyos-tasks.csv"),
    (err) => err instanceof ApiHttpError && err.status === 404,
  );
  assert.equal(saved.length, 0, "失败时不应触发落盘");
});

test("downloadFile：后端不可达时给出「启动后端」指引而不是静默失败", async () => {
  mockBrowser();
  globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
  await assert.rejects(
    () => downloadFile("/export/tasks/md", "studyos-tasks.md"),
    (err) => /无法连接后端 API/.test(err.message),
  );
});
