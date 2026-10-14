"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { cx } from "@/components/ui";

// ── 버튼 ─────────────────────────────────────────────────────────────────────

type BtnKind = "primary" | "ok" | "danger" | "ghost" | "outline";
const BTN: Record<BtnKind, string> = {
  primary: "bg-brand text-white hover:brightness-110 shadow-[0_1px_2px_rgba(16,24,40,.12)]",
  ok: "bg-ok text-white hover:brightness-110 shadow-[0_1px_2px_rgba(16,24,40,.12)]",
  danger: "bg-err text-white hover:brightness-110",
  outline: "border border-line-strong bg-surface text-ink hover:bg-surface-2",
  ghost: "text-ink-2 hover:bg-surface-2 hover:text-ink",
};

export function Btn({ kind = "outline", size = "md", busy, className, children, ...rest }:
  React.ButtonHTMLAttributes<HTMLButtonElement> & { kind?: BtnKind; size?: "sm" | "md"; busy?: boolean }) {
  return (
    <button {...rest} disabled={rest.disabled || busy}
      className={cx("inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg font-semibold transition disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "px-2.5 py-1 text-[12.5px]" : "px-3.5 py-2 text-[13.5px]", BTN[kind], className)}>
      {busy && <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />}
      {children}
    </button>
  );
}

// ── 모달 ─────────────────────────────────────────────────────────────────────

export function Modal({ open, onClose, title, children, footer, wide }: {
  open: boolean; onClose: () => void; title: React.ReactNode; children: React.ReactNode; footer?: React.ReactNode; wide?: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-[rgba(15,20,35,.45)] p-4 backdrop-blur-[2px]" onMouseDown={onClose}>
      <div onMouseDown={(e) => e.stopPropagation()}
        className={cx("flex max-h-[88vh] w-full flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-2xl", wide ? "max-w-[880px]" : "max-w-[520px]")}>
        <header className="flex items-center gap-2 border-b border-line px-5 py-3.5">
          <h3 className="flex-1 text-[15.5px] font-bold">{title}</h3>
          <button onClick={onClose} className="rounded-md px-2 py-1 text-ink-3 hover:bg-surface-2" aria-label="닫기">✕</button>
        </header>
        <div className="min-h-0 flex-1 overflow-auto px-5 py-4 text-[14px]">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-line bg-surface-2 px-5 py-3">{footer}</footer>}
      </div>
    </div>
  );
}

// ── 알림(토스트) ──────────────────────────────────────────────────────────────

type Toast = { id: number; tone: "ok" | "err" | "info"; text: string; notes?: string[] };
const ToastCtx = createContext<(t: Omit<Toast, "id">) => void>(() => {});

export function ToastHost({ children }: { children: React.ReactNode }) {
  const [list, setList] = useState<Toast[]>([]);
  const seq = useRef(0);
  const push = useCallback((t: Omit<Toast, "id">) => {
    const id = ++seq.current;
    setList((l) => [...l, { ...t, id }]);
    setTimeout(() => setList((l) => l.filter((x) => x.id !== id)), t.tone === "err" || t.notes?.length ? 9000 : 4500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-[60] flex w-[360px] max-w-[calc(100vw-40px)] flex-col gap-2">
        {list.map((t) => (
          <div key={t.id} className={cx("pointer-events-auto rounded-xl border px-4 py-3 text-[13.5px] shadow-lg",
            t.tone === "ok" && "border-ok/30 bg-ok-soft text-ok",
            t.tone === "err" && "border-err/30 bg-err-soft text-err",
            t.tone === "info" && "border-line bg-surface text-ink")}>
            <div className="font-semibold">{t.text}</div>
            {t.notes?.map((n, i) => <div key={i} className="mt-1 text-[12.5px] opacity-90">· {n}</div>)}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

/** 버튼 하나의 비동기 동작 — 진행 표시 · 오류 토스트 · 끝나면 새로고침 */
export function useAction(reload: () => void) {
  const toast = useToast();
  const [busy, setBusy] = useState<string>("");
  const run = useCallback(async <T,>(tag: string, fn: () => Promise<T>, okText?: (r: T) => string | undefined) => {
    setBusy(tag);
    try {
      const r = await fn();
      const rr = r as { message?: string; notes?: string[] } | undefined;
      const text = okText?.(r) ?? rr?.message;
      if (text) toast({ tone: "ok", text, notes: rr?.notes });
      return r;
    } catch (e) {
      toast({ tone: "err", text: e instanceof Error ? e.message : String(e) });
      return undefined;
    } finally {
      setBusy("");
      reload();
    }
  }, [reload, toast]);
  return { busy, run };
}

// ── 입력 ─────────────────────────────────────────────────────────────────────

export const inputCls = "w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-[13.5px] outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20 disabled:bg-surface-2 disabled:text-ink-3";

export function Field({ label, hint, children }: { label: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[12px] font-semibold text-ink-2">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11.5px] text-ink-3">{hint}</span>}
    </label>
  );
}
