import { X } from "lucide-react";
import { createPortal } from "react-dom";
import { useRef } from "react";
import type { ReactNode } from "react";
import { useDialog } from "./useDialog";

export function Modal({
  title,
  onClose,
  children,
  wide,
  xwide,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
  /** 更宽的弹窗（资料预览等需要大画布的场景） */
  xwide?: boolean;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialog(dialogRef, onClose);
  return createPortal(
    <div
      className="mobile-modal-layer fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      onClick={onClose}
    >
      <div
        ref={dialogRef} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1}
        className={`mobile-modal max-h-[85vh] w-full ${xwide ? "max-w-6xl" : wide ? "max-w-3xl" : "max-w-lg"} overflow-y-auto rounded-2xl bg-white shadow-xl`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mobile-modal-header sticky top-0 z-10 flex items-center justify-between border-b border-slate-100 bg-white px-5 py-4">
          <h3 className="text-base font-semibold text-slate-800">{title}</h3>
          <button
            onClick={onClose}
            aria-label="关闭弹窗"
            className="flex min-h-11 min-w-11 items-center justify-center rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
          >
            <X size={18} />
          </button>
        </div>
        <div className="mobile-modal-body px-5 py-4">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

export function Spinner() {
  return (
    <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-brand-500 border-t-transparent" />
  );
}

export function Empty({ text }: { text: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-slate-200 bg-white/60 py-14 text-center text-sm text-slate-400">
      {text}
    </div>
  );
}
