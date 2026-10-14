"use client";

import { useEffect, useState } from "react";
import { Icon } from "@/components/Icons";
import { Card, Empty, Skeleton, Tag, cx } from "@/components/ui";
import { useApi } from "@/lib/api";
import { errText, post, type WLRequest } from "@/lib/whitelist";
import { Btn, Input, Modal, Select, Table, useToast } from "./kit";

type Resp = { total: number; count: number; requests: WLRequest[]; companies: { id: number; name: string }[]; is_admin: boolean };

export default function RequestsTab() {
  const toast = useToast();
  const [q, setQ] = useState("");
  const [dq, setDq] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [del, setDel] = useState<WLRequest | null>(null);
  const [reason, setReason] = useState("");
  useEffect(() => { const h = setTimeout(() => setDq(q), 300); return () => clearTimeout(h); }, [q]);
  const { data, loading, reload } = useApi<Resp>(`/api/wl/requests?q=${encodeURIComponent(dq)}`);

  async function setCompany(id: string, cid: string) {
    try { const r = await post<{ message: string }>(`/api/wl/requests/${id}/company`, { company_id: cid ? Number(cid) : null }); toast("ok", r.message); reload(); }
    catch (e) { toast("error", errText(e)); }
  }
  async function doDelete() {
    if (!del) return;
    try { await post(`/api/wl/requests/${del.id}/delete`, { reason }); toast("ok", `${del.id} 삭제`); setDel(null); setReason(""); reload(); }
    catch (e) { toast("error", errText(e)); }
  }

  const devTotal = (data?.requests ?? []).reduce((a, r) => a + r.devices.length, 0);
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="총 등록" value={data?.total ?? "-"} />
        <Stat label="검색 결과" value={data?.count ?? "-"} />
        <Stat label="단말(표시 중)" value={devTotal.toLocaleString()} />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-full max-w-[360px]">
          <Icon name="search" className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="SN · 요청자 · ID"
                 className="w-full rounded-lg border border-line-strong bg-surface py-2 pl-9 pr-3 text-[13.5px] outline-none focus:border-brand" />
        </div>
        <div className="flex-1" />
        <a href={`/api/wl/requests/export.xlsx?q=${encodeURIComponent(dq)}`}
           className="inline-flex items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-3.5 py-2 text-[13.5px] font-semibold hover:bg-surface-2">Excel 내보내기</a>
      </div>
      <Card pad={false}>
        {loading && !data ? <Skeleton rows={8} /> : !data?.requests.length ? <Empty>등록 내역이 없습니다</Empty> : data.requests.map((r) => {
          const on = open === r.id;
          return (
            <div key={r.id} className="border-b border-line last:border-b-0">
              <button onClick={() => setOpen(on ? null : r.id)} className="flex w-full flex-wrap items-center gap-2 px-5 py-3 text-left hover:bg-surface-2">
                <span className="font-[family-name:var(--font-mono)] text-[12.5px] text-ink-3">{r.id}</span>
                <span className="font-semibold">{r.userName || "-"}</span>
                {r.status === "handled" && <Tag>수동 처리 · 등록 아님</Tag>}
                {r.category && <Tag tone="none">{r.category}</Tag>}
                {r.chargeType && <Tag tone={r.chargeType === "유상" ? "warn" : "ok"}>{r.chargeType}</Tag>}
                {r.fotaStatus === "fota_failed" && <Tag tone="error">FOTA 실패</Tag>}
                <span className="ml-auto text-[12.5px] text-ink-3">{r.createdAt.slice(0, 10)} · {r.devices.length}대</span>
                <span className={cx("text-ink-3 transition", on && "rotate-90")}>›</span>
              </button>
              {on && (
                <div className="space-y-3 bg-surface-2 px-5 py-4">
                  <div className="flex flex-wrap gap-x-6 gap-y-1 text-[12.5px] text-ink-2">
                    <span>상태 {r.statusLabel}</span>
                    {r.processedBy && <span>처리 {r.processedBy} · {r.processedAt.slice(0, 16).replace("T", " ")}</span>}
                    {r.fotaStatus && <span>FOTA {r.fotaStatus}</span>}
                    {r.amount && <span>금액 {r.amount}</span>}{r.poNumber && <span>PO 번호 {r.poNumber}</span>}
                    {r.freeReason && <span>무상 사유 {r.freeReason}</span>}
                  </div>
                  {r.memo && <div className="text-[12.5px] text-ink-2">메모: {r.memo}</div>}
                  <div className="flex flex-wrap items-center gap-2">
                    {r.poFileName && <a href={`/api/wl/requests/${r.id}/po`} className="rounded-lg border border-line-strong bg-surface px-3 py-1.5 text-[12.5px] font-semibold hover:bg-surface-2">PO 파일 · {r.poFileName}</a>}
                    {data.is_admin && data.companies.length > 0 && (
                      <span className="flex items-center gap-2 text-[12.5px] text-ink-2">고객사
                        <Select className="w-48 py-1.5" value={r.companyId ?? ""} placeholder="— 지정 안 함 —"
                                options={data.companies.map((c) => ({ value: c.id, label: c.name }))} onChange={(v) => setCompany(r.id, v)} />
                      </span>
                    )}
                    <div className="flex-1" />
                    {data.is_admin && <Btn small kind="ghost" onClick={() => setDel(r)}>삭제</Btn>}
                  </div>
                  <Table rows={r.devices} max={300} cols={[
                    { key: "sn", label: "SN", mono: true }, { key: "api", label: "Android API" }, { key: "endDate", label: "Support End Date" },
                    { key: "ostype", label: "OS Type", mono: true }, { key: "model", label: "Model" },
                  ]} />
                </div>
              )}
            </div>
          );
        })}
      </Card>
      <Modal open={!!del} onClose={() => setDel(null)} title={`${del?.id} 삭제`}
             footer={<><Btn kind="ghost" onClick={() => setDel(null)}>취소</Btn><Btn kind="danger" onClick={doDelete}>삭제</Btn></>}>
        <div className="space-y-2 text-[13.5px] text-ink-2">
          <div>등록 내역에서 내립니다(기록은 삭제됨으로 남습니다). FOTA 등록 자체는 바뀌지 않습니다.</div>
          <Input placeholder="삭제 사유" value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
      </Modal>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-line bg-surface px-5 py-4">
      <div className="text-[12px] font-semibold text-ink-3">{label}</div>
      <div className="mt-1 text-[22px] font-bold">{value}</div>
    </div>
  );
}
