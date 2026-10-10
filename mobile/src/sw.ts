/// <reference lib="webworker" />
/**
 * StudyOS Service Worker：
 * 1. Workbox 预缓存（PWA 离线可用，与旧 generateSW 行为一致）；
 * 2. API 数据离线缓存：GET /api/* 网络优先、成功后写入 IndexedDB，
 *    断网/后端不可达时回退本地缓存（响应带 X-StudyOS-Offline 头，页面据此提示「离线模式」）；
 *    POST/PUT/PATCH/DELETE 成功后会作废对应资源的离线缓存，避免离线时看到过期数据；
 * 3. Web Push：监听浏览器推送服务发来的 `push` 事件弹系统通知——
 *    **页面关闭也能收到**，这正是「真正的推送」与旧版页面内 Notification 的区别。
 *
 * 后端（FastAPI + pywebpush）经 VAPID 签名把 JSON 消息推到浏览器推送服务，
 * 浏览器进程唤醒本 SW 执行 push 事件。点击通知回 /tasks 看板。
 */
import { clientsClaim } from "workbox-core";
import {
  cleanupOutdatedCaches,
  createHandlerBoundToURL,
  precacheAndRoute,
} from "workbox-precaching";
import { NavigationRoute, registerRoute } from "workbox-routing";

declare let self: ServiceWorkerGlobalScope & { __WB_MANIFEST: unknown[] };

self.skipWaiting();
clientsClaim();
cleanupOutdatedCaches();

precacheAndRoute(self.__WB_MANIFEST);

// SPA 回退：所有导航请求回 index.html
// 但必须放行 /api/*：导出的文件（/api/export/*.ics/.csv/.md）、报告/笔记 Markdown
// 都是「点链接就是一次导航」的下载地址。若被回退成 index.html，浏览器只会把页面
// 换成一个 App 空壳，文件永远下不来（现象：点「数据导出」没有任何反应，后端日志里
// 也看不到这次请求）。denylist 让这些请求照常打到后端，由后端返回附件。
registerRoute(
  new NavigationRoute(createHandlerBoundToURL("index.html"), {
    denylist: [/^\/api\//],
  }),
);

// ---------------------------------------------------------------------------
// API 数据离线缓存（IndexedDB）
// 只缓存 GET + JSON 响应；排除依赖后端服务/含敏感信息的接口。
// ---------------------------------------------------------------------------

const IDB_NAME = "studyos-offline-v1";
const IDB_STORE = "api";
/** 最多保留的缓存条目数（超出按写入时间淘汰最旧的） */
const MAX_ENTRIES = 300;
/** 不参与离线缓存的 API（健康检查/密钥配置/推送/同步/语义检索等必须在线） */
const API_DENY = /^\/api\/(auth|health|ai|agent|push|sync|search)(\/|$)/;

interface ApiCacheEntry {
  url: string;
  body: string;
  contentType: string;
  cachedAt: number;
}

let dbPromise: Promise<IDBDatabase> | null = null;

function openDb(): Promise<IDBDatabase> {
  if (!dbPromise) {
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(IDB_NAME, 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(IDB_STORE)) {
          db.createObjectStore(IDB_STORE, { keyPath: "url" });
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }
  return dbPromise;
}

async function idbGet(key: string): Promise<ApiCacheEntry | null> {
  try {
    const db = await openDb();
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(IDB_STORE, "readonly");
      const req = tx.objectStore(IDB_STORE).get(key);
      req.onsuccess = () => resolve((req.result as ApiCacheEntry) ?? null);
      req.onerror = () => reject(req.error);
    });
  } catch {
    return null;
  }
}

async function idbPut(entry: ApiCacheEntry): Promise<void> {
  try {
    const db = await openDb();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(IDB_STORE, "readwrite");
      tx.objectStore(IDB_STORE).put(entry);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
    void prune();
  } catch {
    /* 缓存写入失败不影响在线响应 */
  }
}

/** 删除所有以 prefix 开头的缓存条目（变更后作废旧数据） */
async function idbDeletePrefix(prefix: string): Promise<void> {
  try {
    const db = await openDb();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(IDB_STORE, "readwrite");
      const req = tx.objectStore(IDB_STORE).openCursor();
      req.onsuccess = () => {
        const cursor = req.result;
        if (cursor) {
          if (String(cursor.key).startsWith(prefix)) cursor.delete();
          cursor.continue();
        }
      };
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } catch {
    /* ignore */
  }
}

/** 条目数超过上限时淘汰最旧的，防止无限膨胀 */
async function prune(): Promise<void> {
  try {
    const db = await openDb();
    const all: ApiCacheEntry[] = await new Promise((resolve, reject) => {
      const tx = db.transaction(IDB_STORE, "readonly");
      const req = tx.objectStore(IDB_STORE).getAll();
      req.onsuccess = () => resolve(req.result as ApiCacheEntry[]);
      req.onerror = () => reject(req.error);
    });
    if (all.length <= MAX_ENTRIES) return;
    all.sort((a, b) => a.cachedAt - b.cachedAt);
    const drop = all.slice(0, all.length - MAX_ENTRIES).map((e) => e.url);
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(IDB_STORE, "readwrite");
      const store = tx.objectStore(IDB_STORE);
      for (const url of drop) store.delete(url);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } catch {
    /* ignore */
  }
}

async function cacheKey(url:URL,request:Request){
  const credential=request.headers.get('Authorization')||'guest';
  const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(credential));
  return url.pathname+url.search+'::'+Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,'0')).join('');
}

// GET：网络优先 → 成功写入 IndexedDB；失败回退缓存（带 X-StudyOS-Offline 头）
registerRoute(
  ({ url, request }) =>
    request.method === "GET" &&
    url.pathname.startsWith("/api/") &&
    !API_DENY.test(url.pathname),
  async ({ request, url }) => {
    const key=await cacheKey(url,request);
    try {
      const resp = await fetch(request);
      if (
        resp.ok &&
        (resp.headers.get("content-type") ?? "").includes("application/json")
      ) {
        const cloned = resp.clone();
        try {
          const body = await cloned.text();
          await idbPut({
            url: key,
            body,
            contentType:
              resp.headers.get("content-type") ?? "application/json; charset=utf-8",
            cachedAt: Date.now(),
          });
        } catch {
          /* 缓存写入失败不影响本次响应 */
        }
      }
      return resp;
    } catch (err) {
      const hit = await idbGet(key);
      if (hit) {
        return new Response(hit.body, {
          status: 200,
          headers: {
            "Content-Type": hit.contentType,
            "X-StudyOS-Offline": "1",
            "X-StudyOS-Cached-At": String(hit.cachedAt),
          },
        });
      }
      // 无缓存：原样抛出网络错误（页面显示「后端未连接」）
      throw err;
    }
  },
);

// 写操作：直通后端；成功后作废受影响资源的离线缓存
// （如 PATCH /api/tasks/5 → 删除所有 /api/tasks* 与 /api/stats 缓存；
//   学习报告是跨实体聚合，任何写操作都可能改变它，一并作废）
async function invalidateAfterMutation(url: URL): Promise<void> {
  const segs = url.pathname.split("/").filter(Boolean); // e.g. ["api","tasks","5"]
  const resource = segs.length > 1 ? `/${segs[0]}/${segs[1]}` : `/${segs[0]}`;
  if (resource === '/api/agent' || resource === '/api/sync' || resource === '/api/auth') {
    await idbDeletePrefix('/api/');
  } else {
    await idbDeletePrefix(resource);
  }
  await idbDeletePrefix("/api/stats");
  await idbDeletePrefix("/api/report");
}

for (const method of ["POST", "PUT", "PATCH", "DELETE"] as const) {
  registerRoute(
    ({ url, request }) =>
      request.method === method && url.pathname.startsWith("/api/"),
    async ({ request, url, event }) => {
      const resp = await fetch(request);
      if (resp.ok) {
        event.waitUntil(invalidateAfterMutation(url));
      }
      return resp;
    },
    method,
  );
}

// ---------------------------------------------------------------------------
// Web Push
// ---------------------------------------------------------------------------

interface PushPayload {
  title?: string;
  body?: string;
  tag?: string;
  url?: string;
  task_id?: number;
}

self.addEventListener("push", (event: PushEvent) => {
  let data: PushPayload = {};
  if (event.data) {
    try {
      data = event.data.json() as PushPayload;
    } catch {
      data = { body: event.data.text() };
    }
  }
  event.waitUntil(
    self.registration.showNotification(data.title ?? "StudyOS · DDL 提醒", {
      body: data.body ?? "",
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      tag: data.tag ?? "studyos-ddl",
      data: {
        url: data.url ?? "/",
        task_id: data.task_id,
      },
      renotify: true,
      vibrate: [120, 60, 120],
    }),
  );
});

self.addEventListener("notificationclick", (event: NotificationEvent) => {
  event.notification.close();
  const url: string =
    (event.notification.data && (event.notification.data as { url?: string }).url) || "/";
  event.waitUntil(
    (async () => {
      // 已有窗口 → 聚焦并导航；否则新开窗口
      const clients = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      for (const client of clients) {
        if ("focus" in client) {
          await client.focus();
          if ("navigate" in client) {
            await (client as WindowClient).navigate(url);
          }
          return;
        }
      }
      await self.clients.openWindow(url);
    })(),
  );
});
