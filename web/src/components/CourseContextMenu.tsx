import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Pencil, Trash2 } from "lucide-react";

export default function CourseContextMenu({ x, y, name, onEdit, onDelete, onClose }: {
  x: number;
  y: number;
  name: string;
  onEdit: () => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const [position, setPosition] = useState({ left: x, top: y });

  useLayoutEffect(() => {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return;
    setPosition({
      left: Math.max(8, Math.min(x, window.innerWidth - rect.width - 8)),
      top: Math.max(8, Math.min(y, window.innerHeight - rect.height - 8)),
    });
    ref.current?.querySelector<HTMLButtonElement>("button")?.focus({ preventScroll: true });
  }, [x, y]);

  useEffect(() => {
    const dismiss = () => closeRef.current();
    const outside = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) dismiss();
    };
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape" || event.key === "Tab") dismiss();
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", keydown);
    document.addEventListener("scroll", dismiss, true);
    window.addEventListener("resize", dismiss);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", keydown);
      document.removeEventListener("scroll", dismiss, true);
      window.removeEventListener("resize", dismiss);
    };
  }, []);

  return createPortal(
    <div
      ref={ref}
      role="menu"
      aria-label={`课程「${name}」操作`}
      style={position}
      className="fixed z-50 w-52 max-w-[calc(100vw-1rem)] rounded-xl border border-slate-200 bg-white p-1.5 shadow-xl"
      onContextMenu={(e) => e.preventDefault()}
      onKeyDown={(e) => {
        if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
        e.preventDefault();
        const items = Array.from(ref.current?.querySelectorAll<HTMLButtonElement>("button") ?? []);
        const current = items.indexOf(document.activeElement as HTMLButtonElement);
        const next = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1
          : (current + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        items[next]?.focus();
      }}
    >
      <p className="truncate border-b border-slate-100 px-2.5 py-2 text-xs font-medium text-slate-500">{name}</p>
      <button type="button" role="menuitem" onClick={onEdit} className="mt-1 flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm text-slate-700 outline-none hover:bg-brand-50 focus:bg-brand-50">
        <Pencil size={15} /> 修改课程
      </button>
      <button type="button" role="menuitem" onClick={onDelete} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm text-red-500 outline-none hover:bg-red-50 focus:bg-red-50">
        <Trash2 size={15} /> 删除课程
      </button>
    </div>,
    document.body,
  );
}
