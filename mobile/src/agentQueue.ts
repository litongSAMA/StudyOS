/** Portable text ingestion and conversation drafts; never contains login tokens. */
import { requestKey, getWriteIdentity } from './agentWrites.ts';
export type QueueEntry = { key: string; kind: 'ingest' | 'draft'; createdAt: string; actorId?: string; body: Record<string, unknown> };
const KEY = 'studyos.agent.queue.v1';
const MAX = 100;
const MAX_BYTES = 2_000_000;
function checked(value: unknown): QueueEntry {
  const item = value as QueueEntry;
  if (!item || !/^[A-Za-z0-9_.:-]{1,128}$/.test(item.key) || !['ingest','draft'].includes(item.kind) || typeof item.createdAt !== 'string' || !Number.isFinite(Date.parse(item.createdAt)) || !item.body || typeof item.body !== 'object' || Array.isArray(item.body)) throw new Error('离线队列格式无效');
  if(item.actorId!==undefined && (typeof item.actorId!=='string'||item.actorId.length>128)) throw new Error('队列账号格式无效');
  if (item.kind === 'draft') {
    if (typeof item.body.message !== 'string' || !item.body.message.trim() || item.body.message.length > 10000) throw new Error('助手草稿内容无效');
  } else if (!Array.isArray(item.body.tasks) || !Array.isArray(item.body.materials) || item.body.tasks.length + item.body.materials.length < 1 || item.body.tasks.length + item.body.materials.length > 100) throw new Error('入库队列格式无效');
  return structuredClone(item);
}
export function queued(): QueueEntry[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    if (new TextEncoder().encode(raw).length > MAX_BYTES) throw new Error();
    const value = JSON.parse(raw);
    if (!Array.isArray(value) || value.length > MAX) throw new Error();
    return value.map(checked);
  } catch { throw new Error('无法读取离线队列，请先检查本机浏览器存储'); }
}
function save(value: QueueEntry[]) {
  const raw = JSON.stringify(value);
  if (value.length > MAX || new TextEncoder().encode(raw).length > MAX_BYTES) throw new Error('离线队列已满，请先处理已有条目');
  localStorage.setItem(KEY, raw); // Never report success when durable storage failed.
}
export function stage(kind: QueueEntry['kind'], body: Record<string, unknown>, key = requestKey()): QueueEntry {
  const entry = checked({key,kind,body,createdAt:new Date().toISOString(),actorId:getWriteIdentity()?.actorId});
  const values = queued();
  const prior = values.find(item => item.key === key);
  if (prior) {
    if (prior.kind !== kind || JSON.stringify(prior.body) !== JSON.stringify(body)) throw new Error('相同请求编号的队列内容不能改变');
    return prior;
  }
  save([...values,entry]);
  return entry;
}
export function removeQueued(key: string) { save(queued().filter(item => item.key !== key)); }
export function exportQueue(pending?: {key:string;body:Record<string,unknown>;path:string;actorId?:string}|null): string {
  const items = queued();
  if (pending?.path === '/agent/apply' && !items.some(item => item.key === pending.key)) items.push(checked({...pending,kind:'ingest',createdAt:new Date().toISOString()}));
  if (items.some(item => item.kind === 'ingest' && item.body.file_path)) throw new Error('附件需在目标设备重新上传；跨设备迁移只支持纯文本入库和助手草稿');
  return JSON.stringify({version:1,entries:items},null,2);
}
export function importQueue(raw:string): number {
  if (new TextEncoder().encode(raw).length > MAX_BYTES) throw new Error('队列文件超过 2 MB');
  const value = JSON.parse(raw);
  if (value.version !== 1 || !Array.isArray(value.entries) || value.entries.length > MAX) throw new Error('队列文件格式无效');
  const incoming = value.entries.map(checked) as QueueEntry[];
  if (incoming.some(item => item.kind === 'ingest' && item.body.file_path)) throw new Error('队列中含有另一台设备的附件路径，请在本机重新上传');
  const all = queued();
  let added = 0;
  for (const item of incoming) {
    const previous = all.find(row => row.key === item.key);
    if (previous && (previous.kind !== item.kind || JSON.stringify(previous.body) !== JSON.stringify(item.body))) throw new Error('队列请求编号冲突，未导入任何条目');
    if (!previous) { all.push(item); added++; }
  }
  save(all);
  return added;
}
