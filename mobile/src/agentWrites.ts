/** Preserve the key and body when a response is lost, including across tab refreshes. */
export interface AgentWrite {
  key: string;
  path: string;
  body: Record<string, unknown>;
  createdAt: string;
  actorId?: string;
  deviceId?: string;
}
const STORAGE_KEY = "studyos.agent.pending-write.v1";
let pending: AgentWrite | null = null;
let identity: {actorId:string;deviceId:string} | null = null;
export function setWriteIdentity(value: typeof identity) { identity = value; }
export function getWriteIdentity() { return identity; }

export function requestKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function loadPendingWrite(): AgentWrite | null {
  try {
    let raw: string | null = null;
    let durableAvailable=false;
    try { raw = localStorage.getItem(STORAGE_KEY); durableAvailable=true; } catch { /* Try older session storage. */ }
    if (!raw) { try { raw = sessionStorage.getItem(STORAGE_KEY); } catch { /* memory fallback */ } }
    if(durableAvailable && !raw) pending=null;
    if (raw) {
      const value = JSON.parse(raw);
      if (typeof value.key === "string" && /^[A-Za-z0-9_.:-]{1,128}$/.test(value.key) && /^\/agent\/(apply|plans\/[a-f0-9-]+\/apply|operations\/[a-f0-9-]+\/undo)$/.test(value.path) && value.body && typeof value.body === "object" && !Array.isArray(value.body)) pending = value;
    }
  } catch { /* Memory fallback when browser storage is unavailable. */ }
  return pending;
}

export function beginWrite(path: string, body: Record<string, unknown>, key = requestKey()): AgentWrite {
  const current = loadPendingWrite();
  if (current) throw new Error("还有一笔提交结果待确认，请先在对话助手中查询回执或使用原请求重试");
  const write = { key, path, body: structuredClone(body), createdAt: new Date().toISOString(), ...(identity || {}) };
  pending = write;
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(write)); } catch {
    if(typeof window!=="undefined") {pending=null;throw new Error("浏览器持久存储不可用，尚未发送请求，请检查存储后重试");}
    try {sessionStorage.setItem(STORAGE_KEY,JSON.stringify(write));}catch{ /* Node/test-only memory fallback. */ }
  }
  return write;
}

export function finishWrite(key: string): void {
  if (loadPendingWrite()?.key !== key) return;
  pending = null;
  try { localStorage.removeItem(STORAGE_KEY); } catch { /* memory fallback */ }
  try { sessionStorage.removeItem(STORAGE_KEY); } catch { /* memory fallback */ }
}
