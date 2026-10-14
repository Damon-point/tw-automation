"use client";

import Link from "next/link";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { cx } from "@/components/ui";
import { Icon } from "@/components/Icons";
import type { HistoryEntry } from "@/lib/osu";

// ── 버튼 ─────────────────────────────────────────────────────────────────────

type BtnKind = "primary" | "danger" | "ghost" | "outline";
const BTN: Record<BtnKind, string> = {
  primary: "bg-brand text-white hover:brightness-110 disabled:opacity-50",
  danger: "bg-err text-white hover:brightness-110 disabled:opacity-50",
  outline: "border border-line-strong bg-surface text-ink hover:bg-surface-2 disabled:opacity-50",
  ghost: "text-ink-2 hover:bg-surface-2 hover:text-ink disabled:opacity-40",
};

export function Btn({ kind = "outline", busy, icon, children, className, ...rest }:
  React.ButtonHTMLAttributes<HTMLButtonElement> & { kind?: BtnKind; busy?: boolean; icon?: string }) {
  return (
    <button {...rest} disabled={rest.disabled || busy}
            className={cx("inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-[13px] font-semibold transition-[filter,background] disabled:cursor-not-allowed", BTN[kind], className)}>
      {busy ? <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-r-transparent" /> : icon ? <Icon name={icon} className="h-3.5 w-3.5" /> : null}
      {children}
    </button>
  );
}

// ── 입력 ─────────────────────────────────────────────────────────────────────

export const inputCls = "w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-[14px] outline-none focus:border-brand focus:ring-2 focus:ring-brand-soft disabled:bg-surface-2 disabled:text-ink-3";

export function Field({ label, hint, children, className }: { label: string; hint?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <label className={cx("block", className)}>
      <span className="mb-1 block text-[12px] font-semibold text-ink-2">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11.5px] text-ink-3">{hint}</span>}
    </label>
  );
}

export function AutoTextarea({ value, onChange, className, minRows = 2, ...rest }:
  Omit<React.TextareaHTMLAttributes<HTMLTextAreaElement>, "onChange"> & { value: string; onChange: (v: string) => void; minRows?: number }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + 2}px`;
  }, [value]);
  return <textarea ref={ref} rows={minRows} value={value} onChange={(e) => onChange(e.target.value)} className={cx(inputCls, "resize-none leading-relaxed", className)} {...rest} />;
}

// ── 모달 ─────────────────────────────────────────────────────────────────────

export function Modal({ open, onClose, title, children, footer, wide }: {
  open: boolean; onClose: () => void; title: React.ReactNode; children: React.ReactNode; footer?: React.ReactNode; wide?: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 px-4 py-[8vh]" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={cx("w-full rounded-2xl border border-line bg-surface shadow-2xl", wide ? "max-w-[760px]" : "max-w-[520px]")}>
        <header className="flex items-center gap-2 border-b border-line px-5 py-3.5">
          <h3 className="flex-1 text-[15px] font-bold">{title}</h3>
          <button onClick={onClose} className="rounded-md p-1 text-ink-3 hover:bg-surface-2 hover:text-ink" aria-label="닫기">✕</button>
        </header>
        <div className="max-h-[64vh] overflow-y-auto px-5 py-4 text-[14px]">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</footer>}
      </div>
    </div>
  );
}

/** 되돌릴 수 없는 일 앞의 확인창 — 무엇을 하는지 본문에 적고, 버튼에 동사를 쓴다 */
export function Confirm({ open, title, children, confirmLabel, danger, busy, onConfirm, onClose }: {
  open: boolean; title: string; children: React.ReactNode; confirmLabel: string; danger?: boolean; busy?: boolean;
  onConfirm: () => void; onClose: () => void;
}) {
  return (
    <Modal open={open} onClose={onClose} title={title}
           footer={<><Btn kind="ghost" onClick={onClose}>취소</Btn><Btn kind={danger ? "danger" : "primary"} busy={busy} onClick={onConfirm}>{confirmLabel}</Btn></>}>
      {children}
    </Modal>
  );
}

// ── 알림(토스트) ──────────────────────────────────────────────────────────────

type ToastT = { id: number; tone: "ok" | "err" | "info"; text: string };
const ToastCtx = createContext<(tone: ToastT["tone"], text: string) => void>(() => {});

export function ToastHost({ children }: { children: React.ReactNode }) {
  const [list, setList] = useState<ToastT[]>([]);
  const push = useCallback((tone: ToastT["tone"], text: string) => {
    const id = Date.now() + Math.random();
    setList((l) => [...l, { id, tone, text }]);
    setTimeout(() => setList((l) => l.filter((t) => t.id !== id)), tone === "err" ? 9000 : 4500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-[60] flex w-[360px] max-w-[calc(100vw-2.5rem)] flex-col gap-2">
        {list.map((t) => (
          <div key={t.id} className={cx("pointer-events-auto whitespace-pre-wrap rounded-xl border px-4 py-3 text-[13px] shadow-lg",
            t.tone === "ok" ? "border-ok/30 bg-ok-soft text-ok" : t.tone === "err" ? "border-err/30 bg-err-soft text-err" : "border-line bg-surface text-ink")}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

// ── 편집기 머리 ───────────────────────────────────────────────────────────────

export function EditorHead({ jobId, title, sub, right }: { jobId: string | number; title: string; sub?: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="mb-5">
      <Link href={`/jobs/${jobId}`} className="mb-2 inline-flex items-center gap-1 text-[13px] font-semibold text-ink-2 hover:text-brand-ink">
        <Icon name="chevron" className="h-3.5 w-3.5 rotate-180" /> 작업으로
      </Link>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-[22px] font-bold tracking-tight">{title}</h1>
          {sub && <div className="mt-1 text-[14px] text-ink-2">{sub}</div>}
        </div>
        {right && <div className="flex flex-wrap items-center gap-2">{right}</div>}
      </div>
    </div>
  );
}

export function Banner({ tone, title, children }: { tone: "err" | "warn" | "ok" | "info"; title: string; children?: React.ReactNode }) {
  const cls = { err: "border-err/40 bg-err-soft text-err", warn: "border-warn/40 bg-warn-soft text-warn", ok: "border-ok/40 bg-ok-soft text-ok", info: "border-line bg-surface-2 text-ink-2" }[tone];
  return (
    <div className={cx("rounded-xl border-l-4 px-4 py-3", cls)}>
      <div className="text-[13.5px] font-bold">{title}</div>
      {children && <div className="mt-0.5 text-[13px] opacity-90">{children}</div>}
    </div>
  );
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { key: T; label: React.ReactNode }[] }) {
  return (
    <div className="flex rounded-lg border border-line bg-surface p-0.5">
      {items.map((t) => (
        <button key={t.key} onClick={() => onChange(t.key)}
                className={cx("rounded-md px-3 py-1.5 text-[13px] font-semibold", value === t.key ? "bg-brand-soft text-brand-ink" : "text-ink-2 hover:text-ink")}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

/** 저장된 HTML 파일 미리보기 — 열 때마다 새로 받는다(캐시로 옛 판이 보이지 않게) */
export function FramePreview({ src, stamp, minH = "62vh" }: { src: string; stamp?: string | number; minH?: string }) {
  if (!src) return <div className="rounded-xl border border-dashed border-line px-4 py-10 text-center text-[13px] text-ink-3">미리보기가 없습니다</div>;
  return <iframe sandbox="" src={`${src}&v=${stamp ?? ""}`} className="w-full rounded-xl border border-line bg-white" style={{ minHeight: minH }} />;
}

// ── 편집 이력 ─────────────────────────────────────────────────────────────────

export function HistoryList({ entries }: { entries: HistoryEntry[] }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!entries.length) return <div className="px-1 py-4 text-[13px] text-ink-3">아직 이력이 없습니다</div>;
  const list = [...entries].reverse();
  return (
    <ol className="space-y-2">
      {list.map((e, i) => (
        <li key={i} className="rounded-xl border border-line">
          <button onClick={() => setOpen(open === i ? null : i)} className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-surface-2">
            <span className="rounded-md bg-muted-soft px-1.5 py-0.5 font-mono text-[11px] text-ink-2">v{e.version}</span>
            <span className="text-[13px] font-semibold">{e.event}</span>
            <span className="min-w-0 flex-1 truncate text-[12px] text-ink-3">{[e.actor, e.summary.split("\n")[0]].filter(Boolean).join(" · ")}</span>
            {(e.add > 0 || e.del > 0) && <span className="font-mono text-[11px]"><span className="text-ok">+{e.add}</span> <span className="text-err">−{e.del}</span></span>}
            <span className="text-[11px] text-ink-3">{e.ts.replace("T", " ").slice(0, 16)}</span>
          </button>
          {open === i && (
            <div className="max-h-[360px] overflow-auto border-t border-line px-3 py-2 font-mono text-[12px] leading-relaxed">
              {e.diff.filter(([, ln]) => ln.trim()).map(([t, ln], k) => (
                <div key={k} className={cx("whitespace-pre-wrap px-1", e.first ? "text-ink-2" : t === "add" ? "bg-ok-soft text-ok" : t === "del" ? "bg-err-soft text-err" : "text-ink-3")}>
                  {e.first ? "" : t === "add" ? "+ " : t === "del" ? "− " : "  "}{ln}
                </div>
              ))}
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}
