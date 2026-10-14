"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/Icons";
import { Card, Empty, LegacyLink, PageHead, Progress, Skeleton, StatusChip, cx } from "@/components/ui";
import { ago, skuText, useApi, type Me, type RoundCard } from "@/lib/api";

type Rounds = { open: RoundCard[]; finished: RoundCard[] };

function RoundRow({ r }: { r: RoundCard }) {
  const inner = (
    <div className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1.5 md:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_120px_auto]">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-semibold">{skuText(r)}</span>
          <span className="font-mono text-[13px] text-ink-2">{r.version}</span>
        </div>
        {r.why && <div className="mt-0.5 truncate text-[12px] text-ink-3" title={r.why}>{r.why}</div>}
      </div>
      <div className="hidden min-w-0 md:block">
        {r.total ? (
          <>
            <div className="mb-1 flex justify-between text-[11px] text-ink-3"><span>{r.done} / {r.total} 단계</span></div>
            <Progress done={r.done} total={r.total} />
          </>
        ) : <span className="text-[12px] text-ink-3">아직 시작하지 않음</span>}
      </div>
      <div className="hidden md:block"><StatusChip status={r.state === "running" ? "review" : r.state} label={r.state === "running" ? "진행 중" : undefined} /></div>
      <Icon name="chevron" className={cx("h-4 w-4", r.job_id ? "text-ink-3" : "text-transparent")} />
    </div>
  );
  return r.job_id ? (
    <Link href={`/jobs/${r.job_id}`} className="block border-b border-line px-5 py-3 last:border-b-0 hover:bg-surface-2">{inner}</Link>
  ) : (
    <div className="border-b border-line px-5 py-3 last:border-b-0">{inner}</div>
  );
}

export default function RoundsPage() {
  const [q, setQ] = useState("");
  const [tab, setTab] = useState<"open" | "finished">("open");
  const [debounced, setDebounced] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { const t = setTimeout(() => setDebounced(q), 250); return () => clearTimeout(t); }, [q]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "/" && document.activeElement?.tagName !== "INPUT") { e.preventDefault(); input.current?.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const { data, loading } = useApi<Rounds>(`/api/rounds?q=${encodeURIComponent(debounced)}`, 30000);
  const me = useApi<Me>("/api/me");
  const rows = tab === "open" ? data?.open ?? [] : data?.finished ?? [];
  const newCount = data?.open.filter((r) => r.state === "new").length ?? 0;

  return (
    <>
      <PageHead
        title="OS Update"
        lead="신규로 감지됐거나 진행 중인 회차, 그리고 최근에 끝난 회차."
        right={me.data && <LegacyLink href={`${me.data.legacy_url}/newtasks`}>새 작업 시작은 기존 화면에서</LegacyLink>}
      />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex rounded-lg border border-line bg-surface p-0.5">
          {(["open", "finished"] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)}
                    className={cx("rounded-md px-3.5 py-1.5 text-[13px] font-semibold", tab === t ? "bg-brand-soft text-brand-ink" : "text-ink-2 hover:text-ink")}>
              {t === "open" ? `진행 중 ${data?.open.length ?? ""}` : `최근 완료 ${data?.finished.length ?? ""}`}
              {t === "open" && newCount > 0 && <span className="ml-1.5 rounded-full bg-err px-1.5 text-[11px] text-white">신규 {newCount}</span>}
            </button>
          ))}
        </div>
        <div className="relative ml-auto w-full max-w-[320px]">
          <Icon name="search" className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" />
          <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder="제품 · 버전으로 찾기   ( / )"
                 className="w-full rounded-lg border border-line-strong bg-surface py-2 pl-9 pr-3 text-[14px] outline-none focus:border-brand focus:ring-2 focus:ring-brand-soft" />
        </div>
      </div>
      <Card pad={false}>
        {loading ? <Skeleton rows={5} /> : rows.length ? rows.map((r, i) => <RoundRow key={`${r.product}-${r.android}-${r.version}-${i}`} r={r} />)
          : <Empty>{q ? `'${q}' 에 맞는 회차가 없습니다` : tab === "open" ? "진행 중인 회차가 없습니다" : "최근 완료된 회차가 없습니다"}</Empty>}
      </Card>
      {tab === "finished" && <p className="mt-3 text-[12px] text-ink-3">최근 작업 60건 중 모든 단계가 끝난 것. {data?.finished[0]?.created_at && `가장 최근 ${ago(data.finished[0].created_at!)}`}</p>}
    </>
  );
}
