"use client";

import { useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/Icons";
import { Card, Empty, PageHead, Skeleton, cx } from "@/components/ui";
import Detail from "@/components/license/Detail";
import { Btn, LicStatusChip } from "@/components/license/LicBits";
import MailInbox from "@/components/license/MailInbox";
import NewRequest from "@/components/license/NewRequest";
import { useApi } from "@/lib/api";
import { qty, type LicOverview } from "@/lib/license";

const FILTERS = ["전체", "대기중", "승인", "반려"] as const;

export default function LicensePage() {
  const [scope, setScope] = useState<"auto" | "mine">("auto");
  const { data, loading, reload } = useApi<LicOverview>(`/api/license/overview?scope=${scope}`, 60_000);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("전체");
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [mailFor, setMailFor] = useState<number | null>(null);   // 메일에서 시작한 신청서
  const [inboxKey, setInboxKey] = useState(0);
  const [toast, setToast] = useState("");

  // 주소의 ?doc= 로 바로 열기(메일 링크 등) · 고른 요청을 주소에 남겨 새로고침에도 유지
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    if (p.get("doc")) setSel(p.get("doc"));
    if (p.get("new")) setCreating(true);
  }, []);
  useEffect(() => {
    const u = new URL(window.location.href);
    if (sel) u.searchParams.set("doc", sel); else u.searchParams.delete("doc");
    u.searchParams.delete("new");
    window.history.replaceState(null, "", u.toString());
  }, [sel]);

  // 관리자는 처음 열 때 결재 대기부터
  useEffect(() => {
    if (data?.is_admin && data.stats?.pending && filter === "전체" && !sel) setFilter("대기중");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.is_admin]);

  const rows = useMemo(() => {
    const ql = q.trim().toLowerCase();
    return (data?.requests ?? []).filter((r) =>
      (filter === "전체" || r.status === filter) &&
      (!ql || `${r.doc_id} ${r.company_name} ${r.license_user_id} ${r.contact_email}`.toLowerCase().includes(ql)));
  }, [data, filter, q]);

  const count = (f: string) => (data?.requests ?? []).filter((r) => f === "전체" || r.status === f).length;
  const s = data?.stats;

  return (
    <>
      <PageHead title="PMDM License"
                lead={data?.is_admin ? "신청 → License Key → 결재 → 인증서 → 결과 메일" : "License Key 발급 신청과 처리 상태"}
                right={<Btn kind="primary" onClick={() => { setCreating(true); setMailFor(null); setSel(null); }}><Icon name="key" className="h-4 w-4" /> 새 신청</Btn>} />

      {toast && (
        <div className="mb-4 flex items-center gap-2 rounded-xl bg-ok-soft px-4 py-2.5 text-[13.5px] text-ok">
          <Icon name="check" className="h-4 w-4" /><span className="flex-1">{toast}</span>
          <button onClick={() => setToast("")} className="text-[12px] opacity-70 hover:opacity-100">닫기</button>
        </div>
      )}
      {data?.is_admin && (!data.keys_ready || !data.cert_template) && (
        <div className="mb-4 rounded-xl bg-err-soft px-4 py-2.5 text-[13.5px] text-err">
          {!data.keys_ready && "발급용 RSA 키가 없어 License Key 를 만들 수 없음. "}
          {!data.cert_template && "인증서 템플릿(.docx)이 없어 인증서를 만들 수 없음."}
        </div>
      )}

      <MailInbox refreshKey={inboxKey}
                 onCreate={(id) => { setMailFor(id); setCreating(true); setSel(null); window.scrollTo({ top: 0, behavior: "smooth" }); }}
                 onOpenDoc={(doc) => { setCreating(false); setSel(doc); }} />

      {s && (
        <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <Kpi label="결재 대기" value={s.pending} hot={s.pending > 0} onClick={() => setFilter("대기중")} />
          <Kpi label="승인" value={s.approved} onClick={() => setFilter("승인")} />
          <Kpi label="반려" value={s.rejected} onClick={() => setFilter("반려")} />
          <Kpi label="전체 신청" value={s.total} onClick={() => setFilter("전체")} />
          <Kpi label="신청 수량" value={`${qty(s.total_qty)}대`} />
          <Kpi label="발급 수량" value={`${qty(s.approved_qty)}대`} />
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-[380px_1fr]">
        <Card pad={false} className="self-start lg:sticky lg:top-4">
          <div className="space-y-2.5 border-b border-line p-3">
            {data?.is_admin && (
              <div className="flex rounded-lg bg-muted-soft p-0.5 text-[12.5px] font-semibold">
                {([["auto", "전체 신청"], ["mine", "내 신청"]] as const).map(([k, lab]) => (
                  <button key={k} onClick={() => setScope(k)}
                          className={cx("flex-1 rounded-md py-1.5", scope === k ? "bg-surface text-ink shadow-sm" : "text-ink-3 hover:text-ink")}>{lab}</button>
                ))}
              </div>
            )}
            <div className="flex gap-1">
              {FILTERS.map((f) => (
                <button key={f} onClick={() => setFilter(f)}
                        className={cx("flex-1 rounded-lg px-2 py-1.5 text-[12.5px] font-semibold",
                          filter === f ? "bg-brand-soft text-brand-ink" : "text-ink-2 hover:bg-surface-2")}>
                  {f} <span className="opacity-60">{count(f)}</span>
                </button>
              ))}
            </div>
            <div className="relative">
              <Icon name="search" className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Doc ID · 회사 · ID · 이메일"
                     className="w-full rounded-lg border border-line-strong bg-surface py-2 pl-9 pr-3 text-[13.5px] outline-none focus:border-brand" />
            </div>
          </div>
          <div className="max-h-[calc(100vh-300px)] min-h-[200px] overflow-y-auto">
            {loading && !data ? <Skeleton rows={5} /> : rows.length ? rows.map((r) => (
              <button key={r.doc_id} onClick={() => { setSel(r.doc_id); setCreating(false); }}
                      className={cx("block w-full border-b border-line px-4 py-3 text-left last:border-b-0",
                        sel === r.doc_id && !creating ? "bg-brand-soft" : "hover:bg-surface-2")}>
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[11.5px] text-ink-3">{r.doc_id}</span>
                  <span className="ml-auto"><LicStatusChip status={r.status} /></span>
                </div>
                <div className="mt-1 truncate text-[14px] font-semibold">{r.company_name}</div>
                <div className="mt-0.5 flex items-center gap-2 text-[12px] text-ink-3">
                  <span>{qty(r.quantity)}대</span><span>·</span><span>{r.date_issued}</span>
                  {data?.scope === "all" && r.created_by_name && <><span>·</span><span className="truncate">{r.created_by_name}</span></>}
                  {r.status === "승인" && !r.has_cert && <span className="ml-auto text-err">인증서 없음</span>}
                </div>
              </button>
            )) : <Empty>{filter === "대기중" ? "결재 대기 중인 신청이 없습니다" : "신청이 없습니다"}</Empty>}
          </div>
        </Card>

        <div className="min-w-0">
          {creating ? (
            <NewRequest key={mailFor ?? "blank"} mailId={mailFor} onCancel={() => { setCreating(false); setMailFor(null); }}
                        onDone={(doc, m) => { setCreating(false); setMailFor(null); setToast(m); setFilter("전체"); reload(); setInboxKey((k) => k + 1); setSel(doc); }} />
          ) : sel ? (
            <Detail key={sel} docId={sel} onChanged={reload} onDeleted={() => { setSel(null); setToast("삭제했습니다"); reload(); }} />
          ) : (
            <Card>
              <div className="flex flex-col items-center gap-3 py-14 text-center">
                <span className="grid h-12 w-12 place-items-center rounded-2xl bg-brand-soft text-brand-ink"><Icon name="key" className="h-6 w-6" /></span>
                <div className="text-[15px] font-semibold">{data?.is_admin && s?.pending ? `결재 대기 ${s.pending}건` : "신청을 고르면 여기에 열린다"}</div>
                <div className="text-[13px] text-ink-3">왼쪽 목록에서 고르거나 새 신청을 만든다</div>
              </div>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}

function Kpi({ label, value, hot, onClick }: { label: string; value: React.ReactNode; hot?: boolean; onClick?: () => void }) {
  return (
    <button onClick={onClick} disabled={!onClick}
            className={cx("rounded-2xl border bg-surface px-4 py-3 text-left shadow-[0_1px_2px_rgba(16,24,40,.05)] transition-colors",
              hot ? "border-warn/50" : "border-line", onClick && "hover:border-line-strong")}>
      <div className="text-[12px] font-semibold text-ink-3">{label}</div>
      <div className={cx("mt-1 text-[22px] font-bold tabular-nums tracking-tight", hot && "text-warn")}>{value}</div>
    </button>
  );
}
