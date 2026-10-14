"use client";

import { cx } from "@/components/ui";

const ST: Record<string, { cls: string; dot: string }> = {
  대기중: { cls: "bg-warn-soft text-warn", dot: "bg-warn" },
  승인: { cls: "bg-ok-soft text-ok", dot: "bg-ok" },
  반려: { cls: "bg-err-soft text-err", dot: "bg-err" },
};

export function LicStatusChip({ status }: { status: string }) {
  const s = ST[status] ?? { cls: "bg-muted-soft text-ink-2", dot: "bg-ink-3" };
  return (
    <span className={cx("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-semibold", s.cls)}>
      <span className={cx("h-1.5 w-1.5 rounded-full", s.dot)} />
      {status}
    </span>
  );
}

export function Field({ label, children, mono }: { label: string; children: React.ReactNode; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="text-[11.5px] font-semibold text-ink-3">{label}</div>
      <div className={cx("mt-0.5 break-words text-[13.5px]", mono && "font-mono text-[12.5px]")}>{children || "—"}</div>
    </div>
  );
}

export const inputCls =
  "w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-[14px] outline-none placeholder:text-ink-3 focus:border-brand";

export function Btn({ children, onClick, kind = "ghost", disabled, busy, type = "button", className }: {
  children: React.ReactNode; onClick?: () => void; kind?: "primary" | "ghost" | "danger" | "danger-ghost";
  disabled?: boolean; busy?: boolean; type?: "button" | "submit"; className?: string;
}) {
  const k = {
    primary: "bg-brand text-white hover:bg-brand-ink border-transparent",
    ghost: "border-line-strong bg-surface text-ink hover:bg-surface-2",
    danger: "bg-err text-white hover:opacity-90 border-transparent",
    "danger-ghost": "border-line-strong bg-surface text-err hover:bg-err-soft",
  }[kind];
  return (
    <button type={type} onClick={onClick} disabled={disabled || busy}
            className={cx("inline-flex items-center justify-center gap-1.5 rounded-lg border px-3.5 py-2 text-[13.5px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50", k, className)}>
      {busy && <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />}
      {children}
    </button>
  );
}

/** 처리 흐름 — 신청 → 키 → 승인 → 인증서 → 결과 메일 */
export function Pipeline({ steps }: { steps: { label: string; state: "done" | "now" | "todo" | "fail" | "skip"; sub?: string }[] }) {
  return (
    <ol className="flex flex-wrap items-start gap-y-3">
      {steps.map((s, i) => (
        <li key={i} className="flex min-w-[96px] flex-1 items-start">
          <div className="flex flex-1 flex-col items-center text-center">
            <span className={cx("grid h-7 w-7 place-items-center rounded-full border-2 text-[12px] font-bold",
              s.state === "done" && "border-ok bg-ok text-white",
              s.state === "now" && "border-brand bg-brand-soft text-brand-ink",
              s.state === "fail" && "border-err bg-err-soft text-err",
              s.state === "todo" && "border-line-strong bg-surface text-ink-3",
              s.state === "skip" && "border-line bg-muted-soft text-ink-3")}>
              {s.state === "done" ? "✓" : s.state === "fail" ? "!" : s.state === "skip" ? "–" : i + 1}
            </span>
            <span className={cx("mt-1.5 text-[12.5px] font-semibold", s.state === "todo" || s.state === "skip" ? "text-ink-3" : "text-ink")}>{s.label}</span>
            {s.sub && <span className="mt-0.5 px-1 text-[11px] leading-tight text-ink-3">{s.sub}</span>}
          </div>
          {i < steps.length - 1 && <span className={cx("mt-3.5 h-0.5 w-4 shrink-0 rounded sm:w-6", s.state === "done" ? "bg-ok" : "bg-line-strong")} />}
        </li>
      ))}
    </ol>
  );
}
