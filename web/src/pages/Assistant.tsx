import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { MessageSquare, RotateCcw, Send, ShieldCheck } from "lucide-react";
import { apiAgent } from "../api";
import { loadPendingWrite, requestKey, getWriteIdentity } from "../agentWrites";
import { queued, stage, removeQueued, exportQueue, importQueue, type QueueEntry } from "../agentQueue";
import { useApp } from "../App";
import { useAgentRequest } from "../components/useAgentRequest";
import { Spinner } from "../components/ui";
import { fmtDT } from "../utils";
import type { AgentCapabilities, AgentOperation, AgentReply } from "../types";

export default function Assistant() {
  const { refreshKey, bump } = useApp();
  const location = useLocation();
  const [capabilities, setCapabilities] = useState<AgentCapabilities | null>(null);
  const [connection, setConnection] = useState("正在连接核心服务…");
  const [message, setMessage] = useState(() => typeof location.state?.message === "string" ? location.state.message : "");
  const [sentMessage, setSentMessage] = useState("");
  const [course, setCourse] = useState("");
  const [hours, setHours] = useState(3);
  const [reply, setReply] = useState<AgentReply | null>(null);
  const [operations, setOperations] = useState<AgentOperation[]>([]);
  const [writeBusy, setWriteBusy] = useState(false);
  const [writeError, setWriteError] = useState("");
  const [receipt, setReceipt] = useState("");
  const [pending, setPending] = useState(loadPendingWrite);
  const writeLock = useRef(false);
  const [exportedQueue,setExportedQueue]=useState("");
  const [queue, setQueue] = useState<QueueEntry[]>([]);
  const [conversation, setConversation] = useState({id:requestKey(),revision:0});
  const [conversations, setConversations] = useState<{id:string;revision:number;updated_at:string}[]>([]);
  const [history, setHistory] = useState<{role:string;content:string}[]>([]);
  const [followups, setFollowups] = useState<{review_plan_id:number;title:string;message:string}[]>([]);
  const conversationOwner=useRef<string|undefined>(undefined);
  const turn = useRef<{signature:string;body:Parameters<typeof apiAgent.chat>[0]} | null>(null);
  const updateQueue = () => { try { setQueue(queued()); } catch (cause) { setWriteError((cause as Error).message); } };
  useEffect(updateQueue, []);
  useEffect(() => { if (capabilities?.conversations) void apiAgent.conversations().then(setConversations).catch(cause => setWriteError(cause.message)); }, [capabilities, refreshKey, conversation.revision]);
  const restoreConversation = async (id:string) => {
    if (!id) { setConversation({id:requestKey(),revision:0});setHistory([]);setReply(null);turn.current=null;return; }
    try { const value=await apiAgent.conversation(id);setConversation({id:value.id,revision:value.revision});setHistory(value.messages);setReply(null);turn.current=null;setWriteError(""); }
    catch (cause) { setWriteError((cause as Error).message); }
  };
  const saveDraft = () => { try { stage('draft',{message:message.trim(),course:course.trim()||undefined,hours_per_day:hours});updateQueue();setReceipt("草稿已保存在本机离线队列"); } catch (cause) { setWriteError((cause as Error).message); } };
  const downloadQueue = () => { try { const value=exportQueue(pending);setExportedQueue(value);const url=URL.createObjectURL(new Blob([value],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='studyos-agent-queue.json';document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000); } catch (cause) {setWriteError((cause as Error).message);} };
  const importFile = async (file?:File) => { if (!file) return;try {if(file.size>2_000_000) throw new Error('队列文件超过 2 MB');const count=importQueue(await file.text());updateQueue();setReceipt(`已导入 ${count} 条队列，发送前请核对账号与内容`);}catch(cause){setWriteError((cause as Error).message);} };

  const { busy, error, run, cancel } = useAgentRequest();

  useEffect(() => {
    let active = true;
    apiAgent.capabilities().then((value) => {
      if (active) {
        if(conversationOwner.current!==value.actor_id){setHistory([]);setConversation({id:requestKey(),revision:0});setConversations([]);setReply(null);turn.current=null;conversationOwner.current=value.actor_id;}
        setCapabilities(value);setConnection("");
      }
    }).catch(() => { if (active) { setCapabilities(null); setConnection("核心服务暂时不可用。可以先保存助手草稿或管理离线队列，恢复连接后再提交。"); } });
    return () => { active = false; };
  }, [refreshKey]);

  useEffect(() => {
    if (!capabilities) return;
    let active = true;
    apiAgent.operations().then((value) => { if (active) setOperations(value); }).catch((cause) => { if (active) setWriteError(cause.message); });
    setPending(loadPendingWrite());
    return () => { active = false; };
  }, [capabilities, refreshKey]);

  const ask = (selection?: { task_id?: number; plan_id?: number }) => {
    const input = selection ? sentMessage : message.trim();
    if (!input || busy || writeBusy || !capabilities?.chat) return;
    setWriteError("");
    setReceipt("");
    setSentMessage(input);
    const body = {message:input,course:course.trim()||undefined,hours_per_day:hours,...selection,conversation_id:conversation.id,expected_revision:conversation.revision};
    const signature = JSON.stringify(body);
    if (turn.current?.signature !== signature) turn.current={signature,body:{...body,turn_id:requestKey()}};
    const request = turn.current!.body;
    void run((signal) => apiAgent.chat(request, signal), (value) => {
      setReply(value);turn.current=null;
      if(value.conversation_id && value.revision) setConversation({id:value.conversation_id,revision:value.revision});
      setHistory(previous=>[...previous,{role:'user',content:input},{role:'assistant',content:value.reply}].slice(-40));
    });
  };

  const write = async (action: () => Promise<Record<string, unknown>>, success: string) => {
    if (writeLock.current) return;
    writeLock.current = true;
    setWriteBusy(true);
    setWriteError("");
    try {
      const result = await action();
      if(Array.isArray(result.followups)&&result.followups.length) setFollowups(result.followups as typeof followups);
      const changedPlan=(result.result as {review_plan_id?:number}|undefined)?.review_plan_id;
      if(changedPlan) setFollowups(previous=>previous.filter(item=>item.review_plan_id!==changedPlan));
      setReceipt(success);
      setReply(null);
      bump();
    } catch (cause) {
      setWriteError(cause instanceof Error ? cause.message : "提交结果尚未确认，原请求已保留。请查询回执或使用原请求重试。");
    } finally {
      writeLock.current = false;
      setWriteBusy(false);
      setPending(loadPendingWrite());
    }
  };

  const proposal = reply?.proposal;
  const candidates = proposal?.needs_selection ? proposal.candidates : reply?.candidates;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-bold text-slate-800"><MessageSquare size={22} />学习对话助手</h1>
        <p className="mt-1 text-sm text-slate-500">查资料、调整截止日期、生成与重排复习计划；每次变更先预览，再确认。</p>
      </div>
      {connection && <div role="status" className="rounded-xl bg-amber-50 p-4 text-sm text-amber-700">{connection}</div>}
      {pending && <div className="space-y-2 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
        <p>有一笔提交结果待确认，原请求已保存。查询回执或重试会沿用原请求编号。</p>
        <div className="flex flex-wrap gap-3">
          <button disabled={writeBusy || !capabilities?.idempotency} onClick={() => void write(() => apiAgent.receipt(pending.key), "已找到原提交回执，数据已刷新")} className="underline disabled:opacity-50">查询原回执</button>
          <button disabled={writeBusy || !capabilities?.idempotency} onClick={() => void write(apiAgent.retryPending, "原请求已确认完成，数据已刷新")} className="underline disabled:opacity-50">使用原请求重试</button>
        </div>
      </div>}
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <button disabled={busy||writeBusy} onClick={()=>void restoreConversation('')} className="text-brand-600 underline">新对话</button>
        <select aria-label="恢复历史对话" disabled={busy||writeBusy} value={conversations.some(row=>row.id===conversation.id)?conversation.id:''} onChange={event=>void restoreConversation(event.target.value)} className="max-w-full rounded-lg border border-slate-200 p-2"><option value="">恢复历史对话…</option>{conversations.map(row=><option key={row.id} value={row.id}>{fmtDT(row.updated_at)} · {row.revision} 轮</option>)}</select>
        {capabilities?.conversations && <button disabled={busy||writeBusy} onClick={()=>void restoreConversation(conversation.id)} className="text-brand-600 underline">重新读取对话</button>}
      </div>
      {history.length>0 && <details className="rounded-xl border border-slate-200 bg-white p-4"><summary className="cursor-pointer text-sm text-slate-600">对话记录 · {Math.ceil(history.length/2)} 轮</summary><div className="mt-3 max-h-72 space-y-2 overflow-auto">{history.map((row,index)=><p key={index} className="whitespace-pre-wrap text-sm text-slate-600"><strong>{row.role==='user'?'你':'助手'}：</strong>{row.content}</p>)}</div></details>}
      <fieldset disabled={busy || writeBusy} className="min-w-0 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <textarea aria-label="发给学习助手的消息" rows={4} value={message} onChange={(event) => setMessage(event.target.value)} placeholder="例如：操作系统实验报告延期到下周三晚上10点；帮我找内存管理资料；昨天的复习没完成，请重排" className="w-full rounded-xl border border-slate-200 p-3 text-sm outline-none focus:border-brand-500" />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <input aria-label="助手课程范围" value={course} onChange={(event) => setCourse(event.target.value)} placeholder="课程范围（可选）" className="rounded-lg border border-slate-200 px-3 py-2 text-sm" />
          <label className="text-xs text-slate-600">每日学习总时间（含课表）<input aria-label="每日学习总小时数" type="number" min={0.25} max={16} step={0.25} value={hours} onChange={(event) => setHours(Number(event.target.value))} className="ml-2 w-20 rounded-lg border border-slate-200 p-2" /></label>
          <button disabled={!message.trim()} onClick={saveDraft} className="text-sm text-brand-600 underline">保存离线草稿</button>
          <button disabled={!capabilities?.chat || !message.trim() || !Number.isFinite(hours) || hours < 0.25 || hours > 16} onClick={() => ask()} className="ml-auto flex items-center gap-2 rounded-xl bg-brand-600 px-4 py-2 text-sm text-white disabled:opacity-50"><Send size={15} />发送</button>
        </div>
      </fieldset>
      {busy && <div role="status" className="flex items-center gap-2 text-sm text-brand-600"><Spinner />正在查看学习数据<button onClick={cancel} className="ml-3 underline">取消</button></div>}
      {(error || writeError) && <div role="alert" className="rounded-xl bg-red-50 p-3 text-sm text-red-700">{error || writeError}</div>}
      {receipt && <div role="status" className="flex items-center gap-2 rounded-xl bg-emerald-50 p-3 text-sm text-emerald-700"><ShieldCheck size={17} />{receipt}</div>}
      {reply && <div className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <p className="whitespace-pre-wrap text-sm text-slate-700">{reply.reply}</p>
        {reply.context && <div className="grid gap-3 md:grid-cols-2">{reply.context.materials.map((item) => <Link key={item.id} to="/materials" className="rounded-xl bg-slate-50 p-3"><p className="text-sm font-medium text-slate-700">{item.title}</p><p className="mt-1 whitespace-pre-wrap break-words text-xs text-slate-500">{item.snippet}</p></Link>)}</div>}
        {reply.links && <div className="flex flex-wrap gap-3">{reply.links.map((link) => <Link key={link.href} to={link.href} className="text-sm text-brand-600 underline">{link.label}</Link>)}</div>}
        {candidates && <div className="space-y-2">{candidates.map((item) => <button key={item.id} disabled={busy || writeBusy} onClick={() => ask(reply.intent === "deadline_change" ? { task_id: item.id } : { plan_id: item.id })} className="block w-full rounded-xl border border-slate-200 p-3 text-left text-sm text-brand-700">选择「{item.title}」{'due_at' in item && typeof item.due_at === 'string' && item.due_at ? ` · ${fmtDT(item.due_at)}` : ""}</button>)}</div>}
        {proposal?.result?.old_due_at && proposal.result.new_due_at && <div className="rounded-xl bg-brand-50 p-3 text-sm text-brand-700">原截止：{fmtDT(proposal.result.old_due_at)}<br />新截止：{fmtDT(proposal.result.new_due_at)}</div>}
        {proposal?.result?.sessions && <div className="max-h-72 space-y-2 overflow-y-auto">{proposal.result.sessions.map((block, index) => <div key={index} className="rounded-lg bg-slate-50 p-3 text-sm text-slate-600">{block.date} · {block.course} · {block.scope} · {block.hours.toFixed(2)} 小时</div>)}</div>}
        {proposal?.warnings?.map((warning) => <p key={warning} className="text-sm text-amber-700">{warning}</p>)}
        {proposal?.plan_id && <button disabled={writeBusy || busy || !!pending || !!proposal.blocked_reason} onClick={() => void write(() => apiAgent.execute(proposal.plan_id!), "变更已完成，可在下方操作记录中撤销")} className="rounded-xl bg-brand-600 px-4 py-2 text-sm text-white disabled:opacity-50">{writeBusy ? "正在执行…" : "确认执行此建议"}</button>}
      </div>}
      {followups.length>0 && <div className="space-y-3 rounded-xl bg-amber-50 p-4 text-sm text-amber-800"><p>截止日期已更新，以下复习计划可以继续调整：</p>{followups.map(item=><div key={item.review_plan_id}><p>{item.message}</p><button disabled={busy||writeBusy||!!pending} onClick={()=>{setReceipt("");void run(()=>apiAgent.replan(item.review_plan_id,hours), proposal=>setReply({engine:'rule',intent:'review_replan',reply:proposal.summary||'请核对重排建议',proposal,requires_confirmation:true,tools:['review.replan.preview']}));}} className="mt-1 underline">预览「{item.title}」关联重排</button></div>)}</div>}
      <div className="space-y-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-700">离线队列 · {queue.length} 条</h2>
        <p className="text-xs text-slate-500">纯文本入库和草稿可以导出到另一台设备，保留原请求编号。附件需重新上传，任务调整需在目标设备重新预览。使用原账号同步数据后再重试。</p>
        <div className="flex flex-wrap gap-4 text-sm text-brand-600"><button onClick={downloadQueue} className="underline">导出队列</button><label className="cursor-pointer underline">导入队列<input aria-label="导入离线队列" type="file" accept="application/json,.json" className="hidden" onChange={event=>{void importFile(event.target.files?.[0]);event.target.value='';}} /></label><button onClick={updateQueue} className="underline">刷新队列</button></div>
        {exportedQueue && <div className="space-y-2"><p className="text-xs text-slate-500">若当前浏览器未保存文件，可复制下方内容到目标设备并保存为 JSON 文件。</p><textarea aria-label="离线队列导出内容" readOnly value={exportedQueue} rows={4} className="w-full rounded-lg border border-slate-200 p-2 font-mono text-xs" /><button onClick={()=>void navigator.clipboard.writeText(exportedQueue).then(()=>setReceipt("队列内容已复制，请在目标设备保存为 JSON 后导入")).catch(()=>setWriteError("无法写入剪贴板，请手动选中上方导出内容复制"))} className="text-sm text-brand-600 underline">复制队列内容</button></div>}
        {queue.map(item=><div key={item.key} className="space-y-2 rounded-xl bg-slate-50 p-3 text-sm"><p className="break-words text-slate-700">{item.kind==='draft'?String(item.body.message):`待入库：${(item.body.tasks as unknown[]).length} 条任务、${(item.body.materials as unknown[]).length} 份资料`}</p><p className="text-xs text-slate-500">{fmtDT(item.createdAt)}{item.actorId?` · ${item.actorId.replace(/^user:/,'')}`:''}</p><div className="flex gap-4 text-brand-600"><button disabled={busy||writeBusy||(item.kind==='ingest'&&(!capabilities?.idempotency||!!pending))} onClick={()=>{if(item.actorId&&item.actorId!==getWriteIdentity()?.actorId){setWriteError('队列属于另一账号，请切换到原账号后继续');return;}if(item.kind==='draft'){setMessage(String(item.body.message));setCourse(String(item.body.course||''));setHours(Number(item.body.hours_per_day||3));setReceipt('草稿已载入，发送前请核对');}else void write(async()=>{const result=await apiAgent.submitQueued(item.key,item.body,item.actorId);removeQueued(item.key);updateQueue();return result;},'队列已入库，重复请求已自动去重');}} className="underline disabled:opacity-50">{item.kind==='draft'?'载入草稿':'提交原请求'}</button><button disabled={writeBusy} onClick={()=>{try{removeQueued(item.key);updateQueue();}catch(cause){setWriteError((cause as Error).message);}}} className="text-slate-500 underline">移除</button></div></div>)}
      </div>
      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="mb-3 text-sm font-semibold text-slate-700">最近的助手操作</h2>
        {operations.length === 0 && <p className="text-xs text-slate-500">暂时没有操作记录</p>}
        <div className="space-y-2">{operations.map((operation) => <div key={operation.id} className="flex flex-wrap items-center gap-3 rounded-xl bg-slate-50 p-3 text-sm">
          <div className="min-w-0 flex-1"><p className="text-slate-700">{operation.response.summary || ({ ingest: "智能入库", undo: "撤销操作", deadline_change: "调整截止日期", review_replan: "重排复习", review_generate: "生成复习计划" } as Record<string, string>)[operation.kind] || operation.kind}</p><p className="mt-1 text-xs text-slate-400">{fmtDT(operation.created_at)}{operation.undone_at && " · 已撤销"}</p></div>
          {operation.kind !== "undo" && !operation.undone_at && <button disabled={writeBusy || busy || !!pending || !capabilities?.undo} onClick={() => void write(() => apiAgent.undo(operation.id), "该操作已撤销，学习数据已刷新")} className="flex items-center gap-1 text-xs text-brand-600 disabled:opacity-50"><RotateCcw size={13} />撤销此操作</button>}
        </div>)}</div>
      </div>
    </div>
  );
}
