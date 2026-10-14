"use client";

import { useEffect, useRef, useState } from "react";
import { cx } from "@/components/ui";

// ── 알림(토스트) — 어디서든 toast() 를 부르면 화면에 놓인 <Toaster/> 가 띄운다 ──────────────

type ToastItem = { id: number; text: string; tone: "ok" | "err" | "info" };

export function toast(text: string, tone: ToastItem["tone"] = "ok") {
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("twn-toast", { detail: { text, tone } }));
}

export function Toaster() {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);
  useEffect(() => {
    const on = (e: Event) => {
      const d = (e as CustomEvent).detail as { text: string; tone: ToastItem["tone"] };
      const id = ++seq.current;
      setItems((v) => [...v, { id, ...d }]);
      setTimeout(() => setItems((v) => v.filter((x) => x.id !== id)), d.tone === "err" ? 7000 : 3500);
    };
    window.addEventListener("twn-toast", on);
    return () => window.removeEventListener("twn-toast", on);
  }, []);
  return (
    <div className="pointer-events-none fixed bottom-5 right-5 z-[60] flex w-[min(380px,calc(100vw-2rem))] flex-col gap-2">
      {items.map((t) => (
        <div key={t.id}
             className={cx("pointer-events-auto rounded-xl border px-4 py-3 text-[13.5px] shadow-lg",
               t.tone === "err" ? "border-err/30 bg-err-soft text-err" : t.tone === "info" ? "border-line bg-surface text-ink" : "border-ok/30 bg-ok-soft text-ok")}>
          {t.text}
        </div>
      ))}
    </div>
  );
}

// ── 버튼 · 입력 ─────────────────────────────────────────────────────────────────

export function Button({ children, onClick, variant = "primary", busy, disabled, type = "button", className, title, size = "md" }: {
  children: React.ReactNode; onClick?: () => void; variant?: "primary" | "ghost" | "outline" | "danger";
  busy?: boolean; disabled?: boolean; type?: "button" | "submit"; className?: string; title?: string; size?: "sm" | "md";
}) {
  return (
    <button type={type} onClick={onClick} disabled={disabled || busy} title={title}
            className={cx("inline-flex items-center justify-center gap-1.5 rounded-lg font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50",
              size === "sm" ? "px-2.5 py-1 text-[12.5px]" : "px-3.5 py-2 text-[13.5px]",
              variant === "primary" && "bg-brand text-white hover:brightness-110",
              variant === "outline" && "border border-line-strong bg-surface text-ink hover:bg-surface-2",
              variant === "ghost" && "text-ink-2 hover:bg-surface-2 hover:text-ink",
              variant === "danger" && "bg-err text-white hover:brightness-110",
              className)}>
      {busy && <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-r-transparent" />}
      {children}
    </button>
  );
}

const INPUT = "w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-[14px] text-ink outline-none placeholder:text-ink-3 focus:border-brand disabled:bg-surface-2 disabled:text-ink-3";

export function Input(props: React.ComponentProps<"input"> & { mono?: boolean }) {
  const { mono, className, ...rest } = props;
  return <input {...rest} className={cx(INPUT, mono && "font-mono text-[13px]", className)} />;
}

export function Textarea(props: React.ComponentProps<"textarea"> & { mono?: boolean }) {
  const { mono, className, ...rest } = props;
  return <textarea {...rest} className={cx(INPUT, "leading-relaxed", mono && "font-mono text-[12.5px]", className)} />;
}

export function Select({ value, onChange, options, className, disabled }: {
  value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; className?: string; disabled?: boolean;
}) {
  return (
    <select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} className={cx(INPUT, "pr-8", className)}>
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

export function Field({ label, hint, children, className }: { label: string; hint?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <label className={cx("block", className)}>
      <span className="mb-1 block text-[12.5px] font-semibold text-ink-2">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[12px] text-ink-3">{hint}</span>}
    </label>
  );
}

/** 비밀번호·키 입력 — 저장된 값은 보여 주지 않고, 있으면 '저장됨' 으로만 표시한다 */
export function SecretInput({ value, onChange, saved, placeholder }: { value: string; onChange: (v: string) => void; saved: boolean; placeholder?: string }) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <input type={show ? "text" : "password"} value={value} onChange={(e) => onChange(e.target.value)} autoComplete="new-password"
             placeholder={saved ? "저장됨 · 바꿀 때만 입력" : placeholder ?? "입력"}
             className={cx(INPUT, "pr-16")} />
      <button type="button" onClick={() => setShow((v) => !v)} className="absolute right-2 top-1/2 -translate-y-1/2 rounded px-1.5 py-0.5 text-[11.5px] font-semibold text-ink-3 hover:bg-surface-2">
        {show ? "숨기기" : "보기"}
      </button>
    </div>
  );
}

export function Switch({ checked, onChange, disabled, label }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label?: React.ReactNode }) {
  return (
    <button type="button" role="switch" aria-checked={checked} disabled={disabled} onClick={() => onChange(!checked)}
            className="inline-flex items-center gap-2.5 text-left disabled:opacity-50">
      <span className={cx("relative h-[22px] w-[40px] shrink-0 rounded-full transition-colors", checked ? "bg-brand" : "bg-line-strong")}>
        <span className={cx("absolute top-[3px] h-4 w-4 rounded-full bg-white shadow transition-all", checked ? "left-[21px]" : "left-[3px]")} />
      </span>
      {label && <span className="text-[14px]">{label}</span>}
    </button>
  );
}

/** 설정됨 / 없음 표시 */
export function SetBadge({ ok, okText = "설정됨", noText = "없음" }: { ok: boolean; okText?: string; noText?: string }) {
  return (
    <span className={cx("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11.5px] font-semibold", ok ? "bg-ok-soft text-ok" : "bg-muted-soft text-ink-3")}>
      <span className={cx("h-1.5 w-1.5 rounded-full", ok ? "bg-ok" : "bg-ink-3")} />
      {ok ? okText : noText}
    </span>
  );
}

// ── 창 ────────────────────────────────────────────────────────────────────

export function Modal({ open, onClose, title, children, footer, wide }: {
  open: boolean; onClose: () => void; title: React.ReactNode; children: React.ReactNode; footer?: React.ReactNode; wide?: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 sm:items-center" onMouseDown={onClose}>
      <div onMouseDown={(e) => e.stopPropagation()}
           className={cx("my-8 w-full rounded-2xl border border-line bg-surface shadow-2xl", wide ? "max-w-[960px]" : "max-w-[520px]")}>
        <header className="flex items-center gap-2 border-b border-line px-5 py-3.5">
          <h3 className="flex-1 text-[15.5px] font-bold">{title}</h3>
          <button onClick={onClose} className="rounded-md px-2 py-1 text-ink-3 hover:bg-surface-2" aria-label="닫기">✕</button>
        </header>
        <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</footer>}
      </div>
    </div>
  );
}

/** 되돌릴 수 없는 일 확인 */
export function Confirm({ open, onClose, title, children, confirmText = "확인", danger, onConfirm }: {
  open: boolean; onClose: () => void; title: string; children?: React.ReactNode; confirmText?: string; danger?: boolean; onConfirm: () => Promise<void> | void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Modal open={open} onClose={onClose} title={title}
           footer={<>
             <Button variant="ghost" onClick={onClose}>취소</Button>
             <Button variant={danger ? "danger" : "primary"} busy={busy}
                     onClick={async () => { setBusy(true); try { await onConfirm(); onClose(); } finally { setBusy(false); } }}>{confirmText}</Button>
           </>}>
      <div className="text-[14px] text-ink-2">{children}</div>
    </Modal>
  );
}

/** 설정 화면 한 구획 */
export function Section({ title, desc, right, children }: { title: React.ReactNode; desc?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-line bg-surface shadow-[0_1px_2px_rgba(16,24,40,.05)]">
      <header className="flex flex-wrap items-start gap-3 border-b border-line px-5 py-4">
        <div className="min-w-0 flex-1">
          <h2 className="text-[15px] font-bold">{title}</h2>
          {desc && <p className="mt-0.5 text-[13px] text-ink-3">{desc}</p>}
        </div>
        {right}
      </header>
      <div className="p-5">{children}</div>
    </section>
  );
}

/** 비동기 작업을 돌리고 결과를 알림으로 띄운다 */
export function useRunner() {
  const [busy, setBusy] = useState<string>("");
  async function run<T>(key: string, fn: () => Promise<T>, ok?: (r: T) => string | void) {
    setBusy(key);
    try {
      const r = await fn();
      const msg = ok?.(r);
      const rr = r as unknown as { ok?: boolean; message?: string };
      if (msg) toast(msg);
      else if (rr && typeof rr === "object" && rr.message) toast(rr.message, rr.ok === false ? "err" : "ok");
      return r;
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "err");
      return undefined;
    } finally {
      setBusy("");
    }
  }
  return { busy, run };
}

export function SearchBox({ value, onChange, placeholder, className }: { value: string; onChange: (v: string) => void; placeholder?: string; className?: string }) {
  return (
    <div className={cx("relative w-full max-w-[300px]", className)}>
      <svg viewBox="0 0 24 24" className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" fill="none" stroke="currentColor" strokeWidth="1.9"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
             className="w-full rounded-lg border border-line-strong bg-surface py-2 pl-9 pr-3 text-[14px] outline-none focus:border-brand" />
    </div>
  );
}
