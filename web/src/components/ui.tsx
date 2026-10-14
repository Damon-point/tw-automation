"use client";

import Link from "next/link";
import { useState } from "react";
import type { Summary, Tone } from "@/lib/api";

export function cx(...a: (string | false | null | undefined)[]) {
  return a.filter(Boolean).join(" ");
}

export function Card({ title, icon, right, children, className, pad = true }: {
  title?: React.ReactNode; icon?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode; className?: string; pad?: boolean;
}) {
  return (
    <section className={cx("rounded-2xl border border-line bg-surface shadow-[0_1px_2px_rgba(16,24,40,.05)]", className)}>
      {title && (
        <header className="flex items-center gap-2 border-b border-line px-5 py-3.5">
          {icon && <span className="text-ink-3">{icon}</span>}
          <h2 className="flex-1 text-[15px] font-bold">{title}</h2>
          {right}
        </header>
      )}
      <div className={pad ? "p-5" : ""}>{children}</div>
    </section>
  );
}

const STATUS: Record<string, { label: string; cls: string; dot: string }> = {
  pending: { label: "대기", cls: "bg-muted-soft text-ink-2", dot: "bg-ink-3" },
  running: { label: "실행 중", cls: "bg-info-soft text-info", dot: "bg-info pulse-dot" },
  review: { label: "검수 대기", cls: "bg-warn-soft text-warn", dot: "bg-warn" },
  done: { label: "완료", cls: "bg-ok-soft text-ok", dot: "bg-ok" },
  new: { label: "신규", cls: "bg-err-soft text-err", dot: "bg-err" },
  finished: { label: "완료", cls: "bg-ok-soft text-ok", dot: "bg-ok" },
};

export function StatusChip({ status, label }: { status: string; label?: string }) {
  const s = STATUS[status] ?? { label: status, cls: "bg-muted-soft text-ink-2", dot: "bg-ink-3" };
  return (
    <span className={cx("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-semibold", s.cls)}>
      <span className={cx("h-1.5 w-1.5 rounded-full", s.dot)} />
      {label ?? s.label}
    </span>
  );
}

const TONE: Record<Tone, string> = {
  error: "bg-err-soft text-err",
  warn: "bg-warn-soft text-warn",
  ok: "bg-ok-soft text-ok",
  none: "bg-muted-soft text-ink-2",
};

export function Tag({ tone = "none", children }: { tone?: Tone; children: React.ReactNode }) {
  return <span className={cx("inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-semibold", TONE[tone])}>{children}</span>;
}

export function Progress({ done, total, tone }: { done: number; total: number; tone?: "ok" | "brand" }) {
  const pct = total ? Math.round((done * 100) / total) : 0;
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted-soft">
      <div className={cx("h-full rounded-full transition-all", pct === 100 || tone === "ok" ? "bg-ok" : "bg-brand")} style={{ width: `${pct}%` }} />
    </div>
  );
}

/** 단계 결과 글 요약 — 오류(✘)·경고(⚠) 줄을 위로, 원문은 접는다 */
export function ResultSummary({ summary, raw, compact }: { summary: Summary; raw?: string; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  if (!raw && !summary.headline) return null;
  return (
    <div className="mt-2 space-y-1.5">
      {summary.errors.map((e, i) => (
        <div key={`e${i}`} className="flex gap-2 rounded-lg bg-err-soft px-2.5 py-1.5 text-[13px] text-err">
          <span className="font-bold">✘</span>
          <span className="break-all">{e}</span>
        </div>
      ))}
      {summary.warnings.slice(0, compact ? 2 : 8).map((w, i) => (
        <div key={`w${i}`} className="flex gap-2 rounded-lg bg-warn-soft px-2.5 py-1.5 text-[13px] text-warn">
          <span className="font-bold">⚠</span>
          <span className="break-all">{w}</span>
        </div>
      ))}
      {raw && !compact && (
        <>
          <button onClick={() => setOpen((v) => !v)} className="text-xs font-semibold text-brand-ink hover:underline">
            {open ? "결과 글 접기" : `결과 글 펼치기${summary.ok_count ? ` · ✔ ${summary.ok_count}줄` : ""}`}
          </button>
          {open && (
            <pre className="max-h-[420px] overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-surface-2 p-3 font-[family-name:var(--font-sans)] text-[12.5px] leading-relaxed text-ink-2">
              {raw}
            </pre>
          )}
        </>
      )}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="px-5 py-10 text-center text-sm text-ink-3">{children}</div>;
}

export function PageHead({ title, lead, right }: { title: React.ReactNode; lead?: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-[22px] font-bold tracking-tight">{title}</h1>
        {lead && <p className="mt-1 text-[14px] text-ink-2">{lead}</p>}
      </div>
      {right}
    </div>
  );
}

export function LegacyLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer"
       className="inline-flex items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-3 py-1.5 text-[13px] font-semibold text-ink hover:bg-surface-2">
      {children}
      <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 4h6v6M20 4l-9 9" /><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5" /></svg>
    </a>
  );
}

export function RowLink({ href, children, className }: { href: string; children: React.ReactNode; className?: string }) {
  return (
    <Link href={href} className={cx("block border-b border-line px-5 py-3 last:border-b-0 hover:bg-surface-2", className)}>
      {children}
    </Link>
  );
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3 p-5">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-10 animate-pulse rounded-lg bg-muted-soft" />
      ))}
    </div>
  );
}
