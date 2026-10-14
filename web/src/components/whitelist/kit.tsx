"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { cx } from "@/components/ui";

// ── 버튼 · 입력 ──────────────────────────────────────────────────────────────

type BtnKind = "primary" | "danger" | "outline" | "ghost" | "soft";
const BTN: Record<BtnKind, string> = {
  primary: "bg-brand text-white hover:brightness-110 border-transparent",
  danger: "bg-err text-white hover:brightness-110 border-transparent",
  outline: "bg-surface text-ink border-line-strong hover:bg-surface-2",
  ghost: "bg-transparent text-ink-2 border-transparent hover:bg-muted-soft",
  soft: "bg-brand-soft text-brand-ink border-transparent hover:brightness-95",
};

export function Btn({ kind = "outline", busy, small, className, children, disabled, ...rest }:
  React.ButtonHTMLAttributes<HTMLButtonElement> & { kind?: BtnKind; busy?: boolean; small?: boolean }) {
  return (
    <button {...rest} disabled={disabled || busy}
            className={cx("inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border font-semibold transition disabled:cursor-not-allowed disabled:opacity-50",
              small ? "px-2.5 py-1 text-[12.5px]" : "px-3.5 py-2 text-[13.5px]", BTN[kind], className)}>
      {busy && <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />}
      {children}
    </button>
  );
}

const FIELD = "w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-[13.5px] outline-none focus:border-brand disabled:bg-surface-2";

export function Field({ label, hint, children, className }: { label?: React.ReactNode; hint?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <label className={cx("block", className)}>
      {label && <span className="mb-1 block text-[12px] font-semibold text-ink-2">{label}</span>}
      {children}
      {hint && <span className="mt-1 block text-[12px] text-ink-3">{hint}</span>}
    </label>
  );
}

export function Input(p: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...p} className={cx(FIELD, p.className)} />;
}
export function Textarea(p: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...p} className={cx(FIELD, "leading-relaxed", p.className)} />;
}
export function Select({ options, value, onChange, placeholder, className, disabled }: {
  options: (string | number | { value: string | number; label: string })[]; value: string | number | "";
  onChange: (v: string) => void; placeholder?: string; className?: string; disabled?: boolean;
}) {
  return (
    <select value={String(value ?? "")} disabled={disabled} onChange={(e) => onChange(e.target.value)} className={cx(FIELD, "pr-8", className)}>
      {placeholder !== undefined && <option value="">{placeholder}</option>}
      {options.map((o) => {
        const v = typeof o === "object" ? o.value : o;
        const l = typeof o === "object" ? o.label : String(o);
        return <option key={String(v)} value={String(v)}>{l}</option>;
      })}
    </select>
  );
}

/** 둘~넷 중 하나 고르기 */
export function Seg<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: React.ReactNode }[]; onChange: (v: T) => void }) {
  return (
    <div className="inline-flex rounded-lg border border-line-strong bg-surface-2 p-0.5">
      {options.map((o) => (
        <button key={o.value} type="button" onClick={() => onChange(o.value)}
                className={cx("rounded-md px-3 py-1.5 text-[13px] font-semibold transition",
                  value === o.value ? "bg-surface text-ink shadow-sm" : "text-ink-3 hover:text-ink-2")}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Check({ checked, onChange, children }: { checked: boolean; onChange: (v: boolean) => void; children: React.ReactNode }) {
  return (
    <label className="inline-flex cursor-pointer select-none items-center gap-2 text-[13px] text-ink-2">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="h-4 w-4 accent-[var(--brand)]" />
      {children}
    </label>
  );
}

// ── 단계 표시 ────────────────────────────────────────────────────────────────

export function Steps({ steps, current, onPick }: { steps: { key: string; label: string; done?: boolean }[]; current: string; onPick: (k: string) => void }) {
  return (
    <ol className="flex flex-wrap items-center gap-1.5">
      {steps.map((s, i) => {
        const on = s.key === current;
        return (
          <li key={s.key} className="flex items-center gap-1.5">
            {i > 0 && <span className="h-px w-5 bg-line-strong" />}
            <button onClick={() => onPick(s.key)}
                    className={cx("inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-[13px] font-semibold transition",
                      on ? "border-brand bg-brand-soft text-brand-ink" : s.done ? "border-transparent bg-ok-soft text-ok" : "border-line bg-surface text-ink-3 hover:text-ink-2")}>
              <span className={cx("grid h-5 w-5 place-items-center rounded-full text-[11px]",
                on ? "bg-brand text-white" : s.done ? "bg-ok text-white" : "bg-muted-soft text-ink-3")}>{s.done ? "✓" : i + 1}</span>
              {s.label}
            </button>
          </li>
        );
      })}
    </ol>
  );
}

// ── 알림 ─────────────────────────────────────────────────────────────────────

type Toast = { id: number; tone: "ok" | "warn" | "error" | "info"; text: string };
const ToastCtx = createContext<(tone: Toast["tone"], text: string) => void>(() => {});

export function ToastHost({ children }: { children: React.ReactNode }) {
  const [list, setList] = useState<Toast[]>([]);
  const seq = useRef(0);
  const push = useCallback((tone: Toast["tone"], text: string) => {
    const id = ++seq.current;
    setList((l) => [...l, { id, tone, text }]);
    setTimeout(() => setList((l) => l.filter((t) => t.id !== id)), tone === "error" ? 9000 : 4500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-[60] flex w-[min(420px,calc(100vw-2.5rem))] flex-col gap-2">
        {list.map((t) => (
          <div key={t.id} className={cx("pointer-events-auto rounded-xl border px-4 py-3 text-[13.5px] shadow-lg",
            t.tone === "ok" ? "border-ok/30 bg-ok-soft text-ok" : t.tone === "warn" ? "border-warn/30 bg-warn-soft text-warn"
              : t.tone === "error" ? "border-err/30 bg-err-soft text-err" : "border-line bg-surface text-ink")}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

// ── 모달 ─────────────────────────────────────────────────────────────────────

export function Modal({ open, onClose, title, children, wide, footer }: {
  open: boolean; onClose: () => void; title: React.ReactNode; children: React.ReactNode; wide?: boolean; footer?: React.ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" onMouseDown={onClose}>
      <div onMouseDown={(e) => e.stopPropagation()}
           className={cx("flex max-h-[90vh] w-full flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-2xl", wide ? "max-w-[1000px]" : "max-w-[520px]")}>
        <header className="flex items-center gap-2 border-b border-line px-5 py-3.5">
          <h3 className="flex-1 text-[15px] font-bold">{title}</h3>
          <button onClick={onClose} className="rounded-md px-2 py-1 text-ink-3 hover:bg-muted-soft">✕</button>
        </header>
        <div className="min-h-0 flex-1 overflow-auto p-5">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</footer>}
      </div>
    </div>
  );
}

/** 실제로 쓰기 전 확인 — 무엇이 어디에 들어가는지 한 줄로 */
export function useConfirm() {
  const [st, setSt] = useState<{ title: string; body: React.ReactNode; go: () => void; label: string } | null>(null);
  const ask = (title: string, body: React.ReactNode, go: () => void, label = "실행") => setSt({ title, body, go, label });
  const node = (
    <Modal open={!!st} onClose={() => setSt(null)} title={st?.title}
           footer={<><Btn kind="ghost" onClick={() => setSt(null)}>취소</Btn><Btn kind="danger" onClick={() => { const g = st?.go; setSt(null); g?.(); }}>{st?.label}</Btn></>}>
      <div className="text-[14px] leading-relaxed text-ink-2">{st?.body}</div>
    </Modal>
  );
  return { ask, node };
}

// ── 표 ──────────────────────────────────────────────────────────────────────

export function Table<R>({ cols, rows, max = 320, empty = "없음", rowTone }: {
  cols: { key: string; label: string; render?: (r: R) => React.ReactNode; mono?: boolean; w?: string }[];
  rows: R[]; max?: number; empty?: string; rowTone?: (r: R) => "error" | "warn" | "ok" | undefined;
}) {
  return (
    <div className="overflow-auto rounded-xl border border-line" style={{ maxHeight: max }}>
      <table className="w-full border-collapse text-[12.5px]">
        <thead className="sticky top-0 z-[1] bg-surface-2 text-left text-ink-3">
          <tr>{cols.map((c) => <th key={c.key} className="whitespace-nowrap border-b border-line px-3 py-2 font-semibold" style={{ width: c.w }}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.length ? rows.map((r, i) => {
            const t = rowTone?.(r);
            return (
              <tr key={i} className={cx("border-b border-line last:border-b-0", t === "error" && "bg-err-soft/60", t === "warn" && "bg-warn-soft/60")}>
                {cols.map((c) => (
                  <td key={c.key} className={cx("px-3 py-1.5 align-top", c.mono && "font-[family-name:var(--font-mono)] text-[12px]")}>
                    {c.render ? c.render(r) : String((r as Record<string, unknown>)[c.key] ?? "")}
                  </td>
                ))}
              </tr>
            );
          }) : <tr><td colSpan={cols.length} className="px-3 py-6 text-center text-ink-3">{empty}</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

export function Pre({ children, max = 260 }: { children: React.ReactNode; max?: number }) {
  return (
    <pre className="overflow-auto whitespace-pre-wrap rounded-xl border border-line bg-surface-2 p-3 font-[family-name:var(--font-mono)] text-[12px] leading-relaxed text-ink-2" style={{ maxHeight: max }}>
      {children}
    </pre>
  );
}

export function Note({ tone = "info", children }: { tone?: "info" | "warn" | "error" | "ok"; children: React.ReactNode }) {
  return (
    <div className={cx("rounded-lg px-3 py-2 text-[13px]",
      tone === "ok" ? "bg-ok-soft text-ok" : tone === "warn" ? "bg-warn-soft text-warn" : tone === "error" ? "bg-err-soft text-err" : "bg-surface-2 text-ink-2")}>
      {children}
    </div>
  );
}

export function SubHead({ children, right }: { children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="mb-2 flex items-center gap-2">
      <h4 className="flex-1 text-[13.5px] font-bold">{children}</h4>
      {right}
    </div>
  );
}
