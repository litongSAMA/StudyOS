import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, FileUp, Image as ImageIcon, Sparkles, Upload, CheckCircle2 } from "lucide-react";
import { apiAgent, apiCourses, apiIngest, apiMaterials } from "../api";
import { validateIngestFile } from "../agent";
import { acceptFromCatalog, formatBadge, fallbackCatalog, normalizeCatalog, summaryText, textCharsText, unknownNoteOf } from "../lib/formats";
import FormatCatalog from "../components/FormatCatalog";
import { useAgentRequest } from "../components/useAgentRequest";
import { useApp } from "../App";
import { Link } from "react-router-dom";
import ConfirmPanel from "../components/ConfirmPanel";
import { Empty, Spinner } from "../components/ui";
import type { ApplyResult, ExtractResult, FormatCatalog as FormatCatalogData, SuggestionBundle } from "../types";

type Pending =
  | { kind: "extract"; result: ExtractResult; preview: string }
  | { kind: "upload"; bundle: SuggestionBundle }
  | null;

/** 徽标配色：mismatch=琥珀（按实际内容解析）、本机不支持=灰、正常=品牌色 */
const badgeToneClass = (tone: "ok" | "muted" | "warn") =>
  tone === "warn"
    ? "bg-amber-50 text-amber-700 ring-amber-200"
    : tone === "muted"
      ? "bg-slate-100 text-slate-400 ring-slate-200"
      : "bg-brand-50 text-brand-700 ring-brand-200";

export default function Ingest() {
  const { refreshKey } = useApp();
  const [courses, setCourses] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [courseHint, setCourseHint] = useState("");
  const { busy, error, setError, run, cancel } = useAgentRequest();
  const [saving, setSaving] = useState(false);
  const [receipt, setReceipt] = useState<ApplyResult | null>(null);
  const previewLock = useRef(false);
  const [pending, setPending] = useState<Pending>(null);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  // 格式目录：接口失败静默降级到硬编码兜底列表，上传功能不受影响
  const [catalog, setCatalog] = useState<FormatCatalogData>(() => fallbackCatalog());
  const [catalogDegraded, setCatalogDegraded] = useState(false);

  useEffect(() => {
    apiCourses.list().then((cs) => setCourses(cs.map((c) => c.name))).catch(console.error);
  }, [refreshKey]);

  useEffect(() => {
    let cancelled = false;
    apiIngest
      .formats()
      .then((raw) => {
        if (cancelled) return;
        setCatalog(normalizeCatalog(raw));
        setCatalogDegraded(false);
      })
      .catch(() => {
        // 静默降级：目录接口不可用时仍可上传（accept 用兜底列表）
        if (cancelled) return;
        setCatalog(fallbackCatalog());
        setCatalogDegraded(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const accept = useMemo(() => acceptFromCatalog(catalog), [catalog]);
  const uploadBadge = pending?.kind === "upload" ? formatBadge(pending.bundle.format, unknownNoteOf(catalog)) : null;

  const blocked = busy || saving || pending != null;
  const runText = () => {
    if (!text.trim() || previewLock.current || saving) return;
    const input = text;
    setReceipt(null);
    void run((signal) => apiAgent.extract(input, courseHint, signal), (result) => {
      previewLock.current = true;
      setPending({ kind: "extract", result, preview: input });
    });
  };

  const runFile = (file: File) => {
    if (busy || previewLock.current || saving) return;
    const validation = validateIngestFile(file, accept);
    if (validation) { setError(validation); return; }
    setReceipt(null);
    void run((signal) => apiMaterials.upload(file, courseHint, signal), (bundle) => {
      previewLock.current = true;
      setPending({ kind: "upload", bundle });
    });
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-slate-800">智能入库</h1>
        <p className="mt-1 text-sm text-slate-500">
          文本、文件、截图，一次解析出 DDL 任务与资料标签，确认后入库
        </p>
      </div>

      {error && <div role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
      {busy && <div role="status" className="flex items-center gap-3 rounded-xl bg-brand-50 px-4 py-3 text-sm text-brand-700"><Spinner />正在解析，复杂文件可能需要一些时间<button className="ml-auto underline" onClick={cancel}>取消解析</button></div>}
      {receipt && <div role="status" className="flex flex-wrap items-center gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
        <CheckCircle2 size={18} />已入库：{receipt.created_tasks.length} 条任务、{receipt.created_materials.length} 份资料
        {((receipt.reused_tasks?.length ?? 0) + (receipt.reused_materials?.length ?? 0)) > 0 && <span>复用 {(receipt.reused_tasks?.length ?? 0) + (receipt.reused_materials?.length ?? 0)} 项已有记录</span>}
        {(receipt.created_tasks.length + (receipt.reused_tasks?.length ?? 0)) > 0 && <Link className="underline" to="/tasks">查看任务与提醒</Link>}
        {(receipt.created_materials.length + (receipt.reused_materials?.length ?? 0)) > 0 && <Link className="underline" to="/materials">查看资料</Link>}
      </div>}
      <div className="grid gap-4 lg:grid-cols-5">
        {/* 输入区 */}
        <div className="space-y-4 lg:col-span-2">
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-700">
              <Sparkles size={16} className="text-brand-500" />
              文本入库
            </div>
            <textarea
              disabled={blocked}
              aria-label="待识别文本"
              rows={7}
              className="w-full resize-none rounded-xl border border-slate-200 px-3.5 py-3 text-sm outline-none transition focus:border-brand-500"
              placeholder={"粘贴老师通知 / 群消息，例如：\n\n操作系统课程设计答辩定在12月15日下午2点，地点计院302，记得带实验报告\n\n可多条消息一起粘贴"}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (!e.nativeEvent.isComposing && e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); runText(); }
              }}
            />
            <input
              disabled={blocked}
              aria-label="课程线索"
              list="ingest-courses"
              placeholder="课程线索（可选，帮助归类）"
              className="mt-2 w-full rounded-xl border border-slate-200 px-3.5 py-2 text-xs outline-none focus:border-brand-500"
              value={courseHint}
              onChange={(e) => setCourseHint(e.target.value)}
            />
            <datalist id="ingest-courses">
              {courses.map((c) => (
                <option key={c} value={c} />
              ))}
            </datalist>
            <button
              onClick={runText}
              disabled={blocked || !text.trim()}
              className="mt-3 flex w-full items-center justify-center gap-2 rounded-xl bg-brand-600 py-2.5 text-sm font-medium text-white transition hover:bg-brand-700 disabled:opacity-50"
            >
              {busy ? <Spinner /> : <Sparkles size={16} />}
              AI 识别
            </button>
          </div>

          {/* 文件区 */}
          <div
            className={`rounded-2xl border-2 border-dashed p-6 text-center transition ${
              dragOver ? "border-brand-500 bg-brand-50" : "border-slate-200 bg-white"
            }`}
            onDragOver={(e) => {
              e.preventDefault();
              if (blocked) return;
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              if (blocked) return;
              if (e.dataTransfer.files.length > 1) { setError("请一次上传一个文件，确认入库后再上传下一份"); return; }
              const f = e.dataTransfer.files?.[0];
              if (f) runFile(f);
            }}
          >
            <FileUp size={28} className="mx-auto text-slate-300" />
            <p className="mt-2 text-sm font-medium text-slate-600">
              拖拽文件到这里，或
              <button
                disabled={blocked}
                className="mx-1 text-brand-600 underline"
                onClick={() => fileRef.current?.click()}
              >
                选择文件
              </button>
            </p>
            <p className="mt-1 text-xs text-slate-400">
              {summaryText(catalog.groups)}
              <br />
              <span className="inline-flex items-center gap-1">
                <ImageIcon size={12} /> 截图 / 扫描件会先 OCR 再提取
              </span>
            </p>
            <div className="mt-2">
              <FormatCatalog
                groups={catalog.groups}
                unknownNote={unknownNoteOf(catalog)}
                ocrAvailable={catalog.ocr_available}
                degraded={catalogDegraded}
              />
            </div>
            <input
              ref={fileRef}
              type="file"
              accept={accept}
              disabled={blocked}
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) runFile(f);
                e.target.value = "";
              }}
            />
          </div>
        </div>

        {/* 解析结果区 */}
        <div className="lg:col-span-3">
          {pending ? (
            <div className="space-y-4">
              <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-700">
                  <Upload size={15} className="text-brand-500" />
                  {pending.kind === "upload"
                    ? `已解析文件：${pending.bundle.file_name}`
                    : "已解析文本"}
                  <span className="ml-auto rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500">
                    {pending.kind === "upload"
                      ? pending.bundle.extract.engine
                      : pending.result.engine}{" "}
                    引擎
                  </span>
                </div>
                {pending.kind === "upload" && (
                  <div className="mb-3 flex flex-wrap items-center gap-2">
                    <span
                      title={uploadBadge?.note || uploadBadge?.label}
                      className={`rounded-lg px-2 py-0.5 text-[11px] font-medium ring-1 ${badgeToneClass(uploadBadge?.tone ?? "muted")}`}
                    >
                      {uploadBadge?.label}
                    </span>
                    {uploadBadge?.groupLabel && <span className="text-[10px] text-slate-400">{uploadBadge.groupLabel}</span>}
                    {textCharsText(pending.bundle.text_chars) && (
                      <span className="text-[10px] text-slate-400">抽取 {textCharsText(pending.bundle.text_chars)}</span>
                    )}
                    {uploadBadge && !uploadBadge.supported && uploadBadge.note && (
                      <span className="text-[10px] text-amber-600">{uploadBadge.note}</span>
                    )}
                  </div>
                )}
                {pending.kind === "upload" && uploadBadge?.mismatchNote && (
                  <div
                    role="alert"
                    className="mb-3 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-700"
                  >
                    <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                    <span>{uploadBadge.mismatchNote}</span>
                  </div>
                )}
                {pending.kind === "upload" && pending.bundle.extracted_text_preview && (
                  <details className="mb-3 rounded-xl bg-slate-50 p-3">
                    <summary className="cursor-pointer text-xs text-slate-500">
                      查看抽取的文本（{textCharsText(pending.bundle.text_chars) || `${pending.bundle.extracted_text_preview.length} 字`}）
                    </summary>
                    <pre className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap text-xs text-slate-600">
                      {pending.bundle.extracted_text_preview}
                    </pre>
                  </details>
                )}
                {pending.kind === "extract" && <details className="mb-3 rounded-xl bg-slate-50 p-3"><summary className="cursor-pointer text-xs text-slate-500">查看识别原文（{pending.preview.length} 字）</summary><pre className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap break-words text-xs text-slate-600">{pending.preview}</pre></details>}
                <button disabled={saving} className="mb-3 text-xs text-slate-500 underline disabled:opacity-50" onClick={() => { previewLock.current = false; setPending(null); setError(""); }}>放弃本次结果，重新识别</button>
                <ConfirmPanel
                  extract={
                    pending.kind === "upload" ? pending.bundle.extract : pending.result
                  }
                  filePath={pending.kind === "upload" ? pending.bundle.stored_path : ""}
                  fileName={pending.kind === "upload" ? pending.bundle.file_name : ""}
                  source={pending.kind === "upload" ? "file" : "text"}
                  textSource={pending.kind === "extract" ? pending.preview : pending.bundle.extracted_text_preview}
                  courses={courses}
                  onBusyChange={setSaving}
                  onApplied={(result) => {
                    setReceipt(result);
                    previewLock.current = false;
                    setText("");
                    setPending(null);
                  }}
                />
              </div>
            </div>
          ) : (
            <Empty text="解析结果会显示在这里，低置信的内容会标黄提示你核对" />
          )}
        </div>
      </div>
    </div>
  );
}
