"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { cx } from "@/components/ui";
import { statusTone } from "@/lib/issues";

export function Btn({ children, onClick, kind = "ghost", disabled, busy, title, type = "button", className }: {
  children: React.ReactNode; onClick?: () => void; kind?: "primary" | "ghost" | "danger" | "soft";
  disabled?: boolean; busy?: boolean; title?: string; type?: "button" | "submit"; className?: string;
}) {
  const k = {
    primary: "bg-brand text-white hover:bg-brand-ink border-transparent",
    ghost: "border-line-strong bg-surface text-ink hover:bg-surface-2",
    soft: "border-transparent bg-brand-soft text-brand-ink hover:brightness-95",
    danger: "border-line-strong bg-surface text-err hover:bg-err-soft",
  }[kind];
  return (
    <button type={type} onClick={onClick} disabled={disabled || busy} title={title}
            className={cx("inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border px-3 py-1.5 text-[13px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50", k, className)}>
      {busy && <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />}
      {children}
    </button>
  );
}

export function IssueStatus({ status }: { status: string }) {
  return <span className={cx("inline-flex whitespace-nowrap rounded-full px-2 py-0.5 text-[11.5px] font-semibold", statusTone(status))}>{status || "?"}</span>;
}

/** 칩 — 상태별 건수. 누르면 그 상태만 본다. */
export function FilterChip({ active, onClick, children, tone }: { active: boolean; onClick: () => void; children: React.ReactNode; tone?: string }) {
  return (
    <button onClick={onClick}
            className={cx("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12.5px] font-semibold transition-colors",
              active ? "border-brand bg-brand text-white" : cx("border-line bg-surface text-ink-2 hover:border-line-strong", tone))}>
      {children}
    </button>
  );
}

export function SearchBox({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <div className="relative w-full max-w-[340px]">
      <svg viewBox="0 0 24 24" className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" fill="none" stroke="currentColor" strokeWidth={1.9}><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
             className="w-full rounded-lg border border-line-strong bg-surface py-2 pl-9 pr-8 text-[14px] outline-none focus:border-brand" />
      {value && <button onClick={() => onChange("")} className="absolute right-2 top-1/2 -translate-y-1/2 rounded px-1 text-ink-3 hover:text-ink" aria-label="지우기">×</button>}
    </div>
  );
}

/** 입력이 멈춘 뒤에만 바뀌는 값(검색 디바운스) */
export function useDebounced<T>(v: T, ms = 300) {
  const [d, setD] = useState(v);
  useEffect(() => {
    const t = setTimeout(() => setD(v), ms);
    return () => clearTimeout(t);
  }, [v, ms]);
  return d;
}

export function Modal({ open, onClose, title, children, actions, wide }: {
  open: boolean; onClose: () => void; title: React.ReactNode; children: React.ReactNode; actions?: React.ReactNode; wide?: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onMouseDown={onClose}>
      <div className={cx("max-h-[90vh] w-full overflow-auto rounded-2xl border border-line bg-surface shadow-2xl", wide ? "max-w-[860px]" : "max-w-[480px]")}
           onMouseDown={(e) => e.stopPropagation()}>
        <div className="border-b border-line px-5 py-3.5 text-[15px] font-bold">{title}</div>
        <div className="px-5 py-4 text-[14px]">{children}</div>
        {actions && <div className="flex justify-end gap-2 border-t border-line px-5 py-3">{actions}</div>}
      </div>
    </div>
  );
}

// ── 알림(토스트) ─────────────────────────────────────────────────────────────

type Toast = { id: number; text: string; tone: "ok" | "err" | "info" };
const ToastCtx = createContext<(text: string, tone?: Toast["tone"]) => void>(() => {});

export function ToastHost({ children }: { children: React.ReactNode }) {
  const [list, setList] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: Toast["tone"] = "ok") => {
    const id = Date.now() + Math.random();
    setList((l) => [...l, { id, text, tone }]);
    setTimeout(() => setList((l) => l.filter((t) => t.id !== id)), tone === "err" ? 8000 : 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-[60] flex w-[min(420px,calc(100vw-40px))] flex-col gap-2">
        {list.map((t) => (
          <div key={t.id} className={cx("pointer-events-auto break-all rounded-xl border px-4 py-2.5 text-[13.5px] font-medium shadow-lg",
            t.tone === "ok" ? "border-ok/30 bg-ok-soft text-ok" : t.tone === "err" ? "border-err/30 bg-err-soft text-err" : "border-line bg-surface text-ink")}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);
