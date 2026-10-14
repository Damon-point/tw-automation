"use client";

import { useEffect, useState } from "react";
import { ago, useApi } from "@/lib/api";
import { dur, kst, type IssueList, type IssueRow, type Timeline } from "@/lib/issues";
import { Card, cx, Empty, Skeleton } from "@/components/ui";
import { FilterChip, IssueStatus, SearchBox, useDebounced } from "@/components/issues/kit";
import { RichStyle } from "@/components/issues/RichEditor";

const PAGE = 50;

const KIND: Record<string, { label: string; cls: string }> = {
  created: { label: "생성", cls: "bg-brand" },
  status: { label: "상태", cls: "bg-ok" },
  comment: { label: "코멘트", cls: "bg-ink-3" },
  attachment: { label: "첨부", cls: "bg-[#7c3aed]" },
  field: { label: "변경", cls: "bg-line-strong" },
};

/** 이슈 이력 — 1,600여 건을 상태·검색으로 좁혀 보고, 누르면 오른쪽에 타임라인이 열린다. */
export default function IssueListPage() {
  const [q, setQ] = useState("");
  const dq = useDebounced(q);
  const [status, setStatus] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const [open, setOpen] = useState<IssueRow | null>(null);

  const qs = new URLSearchParams({ q: dq, status, limit: String(limit) }).toString();
  const list = useApi<IssueList>(`/api/issues/list?${qs}`);
  const rows = list.data?.rows ?? [];

  useEffect(() => {
    const f = () => list.reload();
    window.addEventListener("issues:changed", f);
    return () => window.removeEventListener("issues:changed", f);
  }, [list.reload]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <RichStyle />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <SearchBox value={q} onChange={(v) => { setQ(v); setLimit(PAGE); }} placeholder="제목 · 담당자 · 작성자 · 이슈번호" />
        <span className="ml-auto text-[12px] text-ink-3">
          {list.data && `${list.data.total.toLocaleString()}건 중 ${rows.length.toLocaleString()}건 · 감시 제외 이슈는 보이지 않음`}
        </span>
      </div>
      <div className="mb-4 flex flex-wrap gap-1.5">
        <FilterChip active={!status} onClick={() => { setStatus(""); setLimit(PAGE); }}>전체</FilterChip>
        {(list.data?.counts ?? []).map(([s, n]) => (
          <FilterChip key={s} active={status === s} onClick={() => { setStatus(status === s ? "" : s); setLimit(PAGE); }}>
            {s} <span className="opacity-70">{n.toLocaleString()}</span>
          </FilterChip>
        ))}
      </div>

      <Card pad={false}>
        {list.loading && !list.data ? <Skeleton rows={8} /> : rows.length ? (
          <>
            {rows.map((r) => (
              <button key={r.id} onClick={() => setOpen(r)}
                      className={cx("flex w-full items-center gap-3 border-b border-line px-4 py-2.5 text-left last:border-b-0",
                        open?.id === r.id ? "bg-brand-soft" : "hover:bg-surface-2")}>
                <span className="w-[92px] shrink-0"><IssueStatus status={r.status} /></span>
                <span className="w-[54px] shrink-0 font-mono text-[12px] text-ink-3">#{r.id}</span>
                <span className="min-w-0 flex-1 truncate text-[13.5px] font-medium">{r.subject || "(제목 없음)"}</span>
                {r.is_test && <span className="rounded-md border border-warn/40 px-1.5 text-[11px] font-semibold text-warn" title="기능 검증용 — 통계에서만 뺀다">Test</span>}
                <span className="hidden w-[120px] shrink-0 truncate text-right text-[12.5px] text-ink-2 md:block">{r.assignee}</span>
                <span className="w-[72px] shrink-0 text-right text-[12px] text-ink-3" title={`최종 갱신 ${kst(r.updated)}`}>{ago(r.updated)}</span>
              </button>
            ))}
            {rows.length < (list.data?.total ?? 0) && (
              <button onClick={() => setLimit((l) => l + PAGE)} className="block w-full px-4 py-3 text-center text-[13px] font-semibold text-brand-ink hover:bg-surface-2">
                더 보기 (남은 {((list.data?.total ?? 0) - rows.length).toLocaleString()}건)
              </button>
            )}
          </>
        ) : <Empty>조건에 맞는 이슈가 없습니다</Empty>}
      </Card>
      {list.error && <p className="mt-3 text-[13px] text-err">{list.error}</p>}

      {open && <IssueDrawer row={open} onClose={() => setOpen(null)} />}
    </>
  );
}

function IssueDrawer({ row, onClose }: { row: IssueRow; onClose: () => void }) {
  const tl = useApi<Timeline>(`/api/issues/${row.id}/timeline`);
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  const m = tl.data?.metrics;

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/30" onMouseDown={onClose}>
      <aside className="flex h-full w-full max-w-[760px] flex-col border-l border-line bg-surface shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <header className="border-b border-line px-5 py-4">
          <div className="flex items-center gap-2">
            <IssueStatus status={row.status} />
            <a href={row.url} target="_blank" rel="noreferrer" className="text-[13px] font-semibold text-brand-ink hover:underline">#{row.id} Redmine ↗</a>
            {row.is_test && <span className="rounded-md border border-warn/40 px-1.5 text-[11px] font-semibold text-warn">Test</span>}
            <button onClick={onClose} className="ml-auto rounded-md px-2 py-1 text-[18px] leading-none text-ink-3 hover:bg-surface-2 hover:text-ink" aria-label="닫기">×</button>
          </div>
          <h2 className="mt-2 text-[17px] font-bold leading-snug">{row.subject}</h2>
          <div className="mt-1.5 flex flex-wrap gap-x-3 text-[12.5px] text-ink-3">
            <span>작성 {row.author}</span><span>담당 {row.assignee || "—"}</span>
            {row.category && <span>{row.category}</span>}{row.priority && <span>{row.priority}</span>}
            <span>생성 {kst(row.created)}</span>
          </div>
        </header>

        {m && (
          <div className="grid grid-cols-2 gap-px border-b border-line bg-line sm:grid-cols-5">
            {[
              ["첫 반응", dur(m.first_action_days), `작성자 외 사람이 처음 손댄 시점까지(업무일)${m.first_action_kind ? ` — ${m.first_action_kind}${m.first_action_by ? ` · ${m.first_action_by}` : ""}` : ""}`],
              ["Lead time", dur(m.lead_days), "생성 → Resolved 최초 시점, 주말·한국 공휴일을 뺀 업무일"],
              ["재오픈", `${m.reopened}회`, "Resolved/Closed 이후 다시 열린 횟수"],
              ["참여자", `${m.participants.length}명`, m.participants.join(", ")],
              ["코멘트", `${m.comments}건`, ""],
            ].map(([k, v, tip]) => (
              <div key={k} className="bg-surface px-4 py-2.5" title={tip}>
                <div className="text-[11.5px] text-ink-3">{k}</div>
                <div className="text-[15px] font-bold tabular-nums">{v}</div>
              </div>
            ))}
          </div>
        )}

        <div className="flex-1 overflow-auto px-5 py-4">
          {tl.loading ? <Skeleton rows={5} /> : tl.error ? <p className="text-[13px] text-err">{tl.error}</p> : (
            <ol className="relative ml-1.5 border-l border-line">
              {(tl.data?.events ?? []).map((e, i) => {
                const k = KIND[e.kind] ?? KIND.field;
                return (
                  <li key={i} className="relative pb-4 pl-5 last:pb-0">
                    <span className={cx("absolute -left-[5px] top-1.5 h-2.5 w-2.5 rounded-full ring-4 ring-surface", k.cls)} />
                    <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
                      <span className="font-semibold">{e.who}</span>
                      <span className="text-ink-3">{kst(e.at)}</span>
                      {e.creation ? <span className="rounded-md bg-brand-soft px-1.5 text-[11px] font-semibold text-brand-ink">이슈 생성</span>
                        : e.new_status ? <IssueStatus status={e.new_status} /> : null}
                    </div>
                    {e.summary && <div className="mt-0.5 text-[12.5px] text-ink-2">{e.summary}</div>}
                    {e.note_html && (
                      <div className="rich mt-1.5 max-h-[26rem] overflow-auto rounded-lg border border-line bg-surface-2 px-3 py-2"
                           dangerouslySetInnerHTML={{ __html: e.note_html }} />
                    )}
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      </aside>
    </div>
  );
}
