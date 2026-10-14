"use client";

import { useCallback, useEffect, useState } from "react";
import { api, useApi } from "@/lib/api";
import { kst, type SbDoc, type SbList } from "@/lib/issues";
import { Card, cx, Empty, Skeleton } from "@/components/ui";
import { FilterChip, SearchBox, useDebounced } from "@/components/issues/kit";
import { DisclosureTag, SbDocPanel, SbStateChip } from "@/components/issues/SbDocPanel";
import { RichStyle } from "@/components/issues/RichEditor";

const PAGE = 30;

/**
 * Solution Buffer 문서 — 왼쪽 목록, 오른쪽 문서. 8080 은 펼침 목록 안에 편집기를 넣어
 * 길게 스크롤해야 했다. 여기서는 목록을 보며 오른쪽에서 고치고, 처리하면 다음 건으로 넘어간다.
 */
export default function SbPage() {
  const [q, setQ] = useState("");
  const dq = useDebounced(q);
  const [states, setStates] = useState("active");
  const [mine, setMine] = useState(false);
  const [limit, setLimit] = useState(PAGE);
  const [sel, setSel] = useState<number | null>(null);
  const [selDoc, setSelDoc] = useState<SbDoc | null>(null);

  const qs = new URLSearchParams({ q: dq, states, mine: mine ? "1" : "0", limit: String(limit) }).toString();
  const list = useApi<SbList>(`/api/issues/sb?${qs}`);
  const meta = list.data;
  const docs = meta?.docs ?? [];

  useEffect(() => {
    const f = () => list.reload();
    window.addEventListener("issues:changed", f);
    return () => window.removeEventListener("issues:changed", f);
  }, [list.reload]); // eslint-disable-line react-hooks/exhaustive-deps

  // 고른 문서는 목록에서 빠져도(상태가 바뀌어 필터 밖으로) 오른쪽에 그대로 둔다.
  useEffect(() => {
    if (sel === null && docs.length) setSel(docs[0].id);
  }, [docs, sel]);
  useEffect(() => {
    const d = docs.find((x) => x.id === sel);
    if (d) setSelDoc(d);
    else if (sel !== null) api<SbDoc>(`/api/issues/sb/${sel}`).then(setSelDoc).catch(() => setSelDoc(null));
  }, [sel, docs]);

  const onChanged = useCallback((d?: SbDoc) => {
    if (d) setSelDoc(d);
    else if (sel !== null) api<SbDoc>(`/api/issues/sb/${sel}`).then(setSelDoc).catch(() => {});
    list.reload();
  }, [sel, list.reload]); // eslint-disable-line react-hooks/exhaustive-deps

  const counts = meta?.counts ?? {};
  const chips = meta ? Object.entries(meta.states).filter(([k]) => counts[k] && !meta.hidden.includes(k)) : [];
  const activeTotal = meta ? meta.active.reduce((a, k) => a + (counts[k] ?? 0), 0) : 0;

  return (
    <>
      <RichStyle />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <SearchBox value={q} onChange={(v) => { setQ(v); setLimit(PAGE); }} placeholder="제목 · 이슈번호" />
        {meta?.admin && (
          <label className="inline-flex items-center gap-2 text-[13px] text-ink-2">
            <input type="checkbox" className="h-4 w-4 accent-[var(--brand)]" checked={mine} onChange={(e) => { setMine(e.target.checked); setLimit(PAGE); }} />
            내 담당만
          </label>
        )}
        <span className="ml-auto text-[12px] text-ink-3">
          {meta && (meta.scope ? `담당자 ${meta.scope} 기준` : "전체 담당자")}
          {meta ? ` · 누적 사용 $${meta.spent_usd.toFixed(2)}` : ""}
        </span>
      </div>
      <div className="mb-4 flex flex-wrap gap-1.5">
        <FilterChip active={states === "active"} onClick={() => { setStates("active"); setLimit(PAGE); }}>할 일 {activeTotal || ""}</FilterChip>
        {chips.map(([k, label]) => (
          <FilterChip key={k} active={states === k} onClick={() => { setStates(k); setLimit(PAGE); }}>{label} {counts[k]}</FilterChip>
        ))}
        <FilterChip active={states === "all"} onClick={() => { setStates("all"); setLimit(PAGE); }}>전체</FilterChip>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(320px,400px)_minmax(0,1fr)]">
        <Card pad={false} className="self-start xl:sticky xl:top-4">
          <div className="border-b border-line px-4 py-2.5 text-[12px] text-ink-3">
            {meta ? `${meta.total.toLocaleString()}건 중 ${docs.length.toLocaleString()}건` : " "}
          </div>
          {list.loading && !meta ? <Skeleton rows={6} /> : docs.length ? (
            <div className="max-h-[calc(100vh-260px)] overflow-auto">
              {docs.map((d) => (
                <button key={d.id} onClick={() => setSel(d.id)}
                        className={cx("block w-full border-b border-line px-4 py-3 text-left last:border-b-0",
                          sel === d.id ? "bg-brand-soft" : "hover:bg-surface-2")}>
                  <div className="flex items-center gap-1.5">
                    <SbStateChip d={d} />
                    {!["backlog", "queued"].includes(d.state) && <DisclosureTag v={d.disclosure} />}
                    {d.revision > 1 && <span className="text-[11px] font-semibold text-warn">{d.revision}판</span>}
                    {d.generating && <span className="h-1.5 w-1.5 rounded-full bg-info pulse-dot" title="생성 중" />}
                    <span className="ml-auto text-[11.5px] text-ink-3">#{d.issue_id}</span>
                  </div>
                  <div className="mt-1 line-clamp-2 text-[13.5px] font-semibold leading-snug">{d.title || <span className="text-ink-2">{d.subject}</span>}</div>
                  <div className="mt-1 flex gap-2 text-[11.5px] text-ink-3">
                    <span>{d.assignee || "담당자 없음"}</span>
                    {d.resolved_at && <span>해결 {kst(d.resolved_at).slice(0, 10)}</span>}
                  </div>
                </button>
              ))}
              {docs.length < (meta?.total ?? 0) && (
                <button onClick={() => setLimit((l) => l + PAGE)} className="block w-full px-4 py-3 text-center text-[13px] font-semibold text-brand-ink hover:bg-surface-2">
                  더 보기 (남은 {((meta?.total ?? 0) - docs.length).toLocaleString()}건)
                </button>
              )}
            </div>
          ) : (
            <Empty>{states === "active" ? "처리할 문서가 없습니다" : "조건에 맞는 문서가 없습니다"}</Empty>
          )}
        </Card>

        <Card pad={false} className="min-h-[480px] overflow-hidden">
          {selDoc && meta ? (
            <SbDocPanel key={selDoc.id} doc={selDoc} meta={meta} onChanged={onChanged} />
          ) : (
            <Empty>왼쪽에서 문서를 고르세요</Empty>
          )}
        </Card>
      </div>
      {list.error && <p className="mt-3 text-[13px] text-err">{list.error}</p>}
    </>
  );
}
