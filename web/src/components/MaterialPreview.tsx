import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Download, FileText } from "lucide-react";
import { apiMaterials } from "../api";
import type { Material } from "../types";
import { Spinner } from "./ui";

/**
 * 资料原文件内嵌预览：
 * - PDF    → iframe + 浏览器原生 PDF 查看器（分段加载、可缩放/翻页/搜索）；
 * - PPTX   → pptx-preview 纯前端渲染（还原版式/图片/表格/图表，带翻页）；
 * - 图片   → 直接展示；
 * - .ppt / 其它 → 回落到已抽取的文本摘录 + 下载原文件。
 *
 * 文件统一经 `fetchFileBlob` 拉取为 Blob（自动携带登录令牌），
 * 再转 objectURL / ArrayBuffer——登录保护开启时预览同样可用。
 */
type PreviewKind = "pdf" | "pptx" | "ppt" | "image" | "text";

/** pptx-preview 渲染器实例的最小接口（动态加载，避免静态依赖 echarts） */
interface PptxPreviewer {
  preview: (file: ArrayBuffer) => Promise<unknown>;
  destroy: () => void;
}

const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "webp", "gif", "bmp"]);

function kindOf(fileName: string): PreviewKind {
  const ext = (fileName.split(".").pop() ?? "").toLowerCase();
  if (ext === "pdf") return "pdf";
  if (ext === "pptx") return "pptx";
  if (ext === "ppt") return "ppt";
  if (IMAGE_EXTS.has(ext)) return "image";
  return "text";
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function MaterialPreview({ material }: { material: Material }) {
  const kind = kindOf(material.file_name);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [blobSize, setBlobSize] = useState(0);
  const [pptBuffer, setPptBuffer] = useState<ArrayBuffer | null>(null);
  const [loading, setLoading] = useState(true);
  const [rendering, setRendering] = useState(false);
  const [error, setError] = useState("");
  /** PPTX 幻灯片自然尺寸（EMU→px）与适配缩放 */
  const [pptxSize, setPptxSize] = useState<{ w: number; h: number } | null>(null);
  const [pptxZoom, setPptxZoom] = useState(1);
  const pptxFitRef = useRef<HTMLDivElement | null>(null);
  const pptxHostRef = useRef<HTMLDivElement | null>(null);
  const previewerRef = useRef<PptxPreviewer | null>(null);

  // 拉取附件：Blob → objectURL（PDF/图片/下载）；PPTX 另取 ArrayBuffer 交给渲染库
  useEffect(() => {
    let cancelled = false;
    let url: string | null = null;
    (async () => {
      try {
        const blob = await apiMaterials.fetchFileBlob(material.id);
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        setBlobUrl(url);
        setBlobSize(blob.size);
        if (kind === "pptx") setPptBuffer(await blob.arrayBuffer());
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "附件加载失败，请稍后重试");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [material.id, kind]);

  // PPTX：动态加载渲染库（pptx-preview + echarts + jszip 只在预览 PPTX 时进包）。
  //
  // 关键：让库以「幻灯片自然尺寸」初始化（scale=1），其内部 transform 为恒等变换，
  // 避免 scale(0.97x) 分数缩放触发浏览器对 CJK 大字号字形的栅格化缺陷
  // （44px 标题在分数缩放 transform 下会渲染成部件错位的乱码，实测复现）。
  // 适配容器改用外层 CSS zoom 等比缩放——页面缩放路径的栅格化是安全的。
  useEffect(() => {
    if (kind !== "pptx" || !pptBuffer || !pptxFitRef.current) return;
    let disposed = false;
    let previewer: PptxPreviewer | null = null;
    const fit = pptxFitRef.current;
    (async () => {
      try {
        const [{ init }, { default: JSZip }] = await Promise.all([
          import("pptx-preview"),
          import("jszip"),
        ]);
        if (disposed || !pptxFitRef.current) return;

        // 1) 从 ppt/presentation.xml 读取幻灯片逻辑尺寸（EMU → px，96dpi）
        const zip = await JSZip.loadAsync(pptBuffer);
        const pres = zip.file("ppt/presentation.xml");
        if (!pres) throw new Error("missing presentation.xml");
        const presXml = await pres.async("string");
        const m = presXml.match(/<p:sldSz[^>]*cx="(\d+)"[^>]*cy="(\d+)"/);
        const slideW = m ? Math.round((+m[1] / 914400) * 96) : 960;
        const slideH = m ? Math.round((+m[2] / 914400) * 96) : 720;
        if (disposed || !pptxFitRef.current) return;

        // 2) 适配容器：只缩小不放大，保证整页可见且文字不模糊
        const cw = fit.clientWidth || 900;
        const ch = fit.clientHeight || 540;
        setPptxSize({ w: slideW, h: slideH });
        setPptxZoom(Math.min(cw / slideW, ch / slideH, 1));
        setRendering(true);

        // 3) 自然尺寸初始化 → scale=1，无分数缩放 transform
        previewer = init(pptxHostRef.current!, {
          width: slideW,
          height: slideH,
          mode: "slide",
        });
        previewerRef.current = previewer;
        await previewer.preview(pptBuffer);
      } catch {
        if (!disposed) {
          setError("PPTX 解析失败：文件可能已损坏，或包含暂不支持的版式");
        }
      } finally {
        if (!disposed) setRendering(false);
      }
    })();
    return () => {
      disposed = true;
      previewerRef.current = null;
      try {
        previewer?.destroy();
      } catch {
        /* 忽略清理异常 */
      }
      if (pptxHostRef.current) pptxHostRef.current.innerHTML = "";
      setPptxSize(null);
      setPptxZoom(1);
    };
  }, [kind, pptBuffer]);

  const download = () => {
    if (!blobUrl) return;
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = material.file_name || `material-${material.id}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  return (
    <div className="flex h-full flex-col gap-3">
      {/* 工具栏：文件名 + 大小 + 下载 */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2 text-xs text-slate-500">
          <FileText size={14} className="shrink-0 text-brand-500" />
          <span className="truncate font-medium text-slate-700">{material.file_name}</span>
          {blobSize > 0 && <span className="shrink-0 text-slate-400">{fmtSize(blobSize)}</span>}
        </div>
        <button
          onClick={download}
          disabled={!blobUrl}
          className="flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:border-brand-400 hover:text-brand-600 disabled:opacity-40"
        >
          <Download size={13} />
          下载原文件
        </button>
      </div>

      {/* 预览画布 */}
      <div className="h-[62vh] overflow-hidden rounded-xl border border-slate-200 bg-slate-100">
        {loading && (
          <div className="flex h-full items-center justify-center gap-2 text-sm text-slate-400">
            <Spinner /> 正在加载原文件…
          </div>
        )}
        {!loading && error && (
          <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
            <AlertTriangle size={22} className="text-amber-500" />
            <p className="text-sm text-slate-600">{error}</p>
            <p className="text-xs text-slate-400">
              可点击右上角「下载原文件」用本地软件打开
            </p>
          </div>
        )}
        {!loading && !error && kind === "pdf" && blobUrl && (
          <iframe
            src={blobUrl}
            title={`预览：${material.file_name}`}
            className="h-full w-full"
          />
        )}
        {!loading && !error && kind === "pptx" && (
          <div ref={pptxFitRef} className="relative flex h-full w-full items-center justify-center overflow-hidden bg-slate-900">
            {rendering && (
              <div className="absolute inset-0 flex items-center justify-center gap-2 text-sm text-slate-300">
                <Spinner /> 正在渲染幻灯片…
              </div>
            )}
            <div
              className="shrink-0"
              style={{
                zoom: pptxZoom,
                width: pptxSize?.w ?? 0,
                height: pptxSize?.h ?? 0,
              }}
            >
              <div ref={pptxHostRef} />
            </div>
          </div>
        )}
        {!loading && !error && kind === "image" && blobUrl && (
          <div className="flex h-full items-center justify-center overflow-auto p-4">
            <img
              src={blobUrl}
              alt={material.title}
              className="max-h-full max-w-full rounded-lg object-contain shadow"
            />
          </div>
        )}
        {!loading && !error && (kind === "ppt" || kind === "text") && (
          <div className="flex h-full flex-col overflow-hidden">
            <div className="flex items-center gap-2 border-b border-slate-200 bg-amber-50 px-4 py-2 text-xs text-amber-700">
              <AlertTriangle size={13} className="shrink-0" />
              {kind === "ppt"
                ? "旧版 .ppt 不支持内嵌预览，已展示抽取的文本摘录，可下载后用 PowerPoint 打开"
                : "该格式不支持内嵌预览，已展示抽取的文本摘录"}
            </div>
            <pre className="flex-1 overflow-auto whitespace-pre-wrap bg-white p-4 font-sans text-sm leading-6 text-slate-600">
              {material.text || "（没有可展示的文本内容）"}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
