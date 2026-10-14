"use client";

import { useEffect, useState } from "react";
import { Icon } from "@/components/Icons";
import { cx } from "@/components/ui";
import { api, ago, useApi } from "@/lib/api";
import { qty, type LicInbox, type LicInboxItem } from "@/lib/license";
import { Btn } from "./LicBits";

const DONE_LABEL: Record<string, string> = {
  answered: "회신함", linked: "이미 신청됨", created: "신청서 만듦", dismissed: "신청 아님",
};

/** 받은 메일에서 찾은 License 신청 — 누르면 신청서가 채워진 채로 열린다. */
export default function MailInbox({ onCreate, onOpenDoc, refreshKey }: {
  onCreate: (mailId: number) => void; onOpenDoc: (docId: string) => void; refreshKey: number;
}) {
  const { data, reload } = useApi<LicInbox>("/api/license/inbox");
  const [showDone, setShowDone] = useState(false);
  const [busy, setBusy] = useState<number | null>(null);

  // 아직 읽는 중인 메일이 있으면 몇 초 뒤 다시 묻는다
  useEffect(() => {
    if (!data?.pending) return;
    const t = setTimeout(reload, 4000);
    return () => clearTimeout(t);
  }, [data, reload]);
  useEffect(() => { if (refreshKey) reload(); }, [refreshKey, reload]);

  if (!data) return null;
  const open = data.items.filter((i) => i.status === "new");
  const done = data.items.filter((i) => i.status !== "new");
  if (!open.length && !done.length && !data.pending) return null;

  async function mark(i: LicInboxItem, action: "dismiss" | "restore") {
    setBusy(i.mail_id);
    try {
      await api(`/api/license/inbox/${i.mail_id}/${action}`, { method: "POST" });
      reload();
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="mb-5 overflow-hidden rounded-2xl border border-line bg-surface shadow-[0_1px_2px_rgba(16,24,40,.05)]">
      <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
        <span className="grid h-7 w-7 place-items-center rounded-lg bg-brand-soft text-brand-ink"><Icon name="mail" className="h-4 w-4" /></span>
        <h2 className="text-[15px] font-bold">메일로 들어온 신청</h2>
        {open.length > 0 && <span className="rounded-full bg-[#d97706] px-2 text-[11.5px] font-bold text-white">{open.length}</span>}
        {data.pending > 0 && (
          <span className="inline-flex items-center gap-1.5 text-[12.5px] text-ink-3">
            <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />새 메일 {data.pending}건 읽는 중
          </span>
        )}
        {done.length > 0 && (
          <button onClick={() => setShowDone((v) => !v)} className="ml-auto rounded-md px-2 py-1 text-[12.5px] text-ink-3 hover:bg-surface-2 hover:text-ink">
            {showDone ? "처리된 메일 접기" : `처리된 메일 ${done.length}`}
          </button>
        )}
      </header>

      {open.length > 0 ? (
        <div className="divide-y divide-line">
          {open.map((i) => <Row key={i.mail_id} i={i} busy={busy === i.mail_id}
                                onCreate={() => onCreate(i.mail_id)} onDismiss={() => mark(i, "dismiss")} />)}
        </div>
      ) : (
        <div className="px-4 py-3 text-[13px] text-ink-3">새로 들어온 신청 메일이 없습니다</div>
      )}

      {showDone && done.length > 0 && (
        <div className="border-t border-line bg-surface-2/60">
          {done.map((i) => (
            <div key={i.mail_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-4 py-2 text-[13px] last:border-b-0">
              <span className="rounded-full bg-muted-soft px-2 py-0.5 text-[11.5px] font-semibold text-ink-2">{DONE_LABEL[i.status]}</span>
              <span className="min-w-0 flex-1 truncate">{i.subject}</span>
              <span className="text-[12px] text-ink-3">{i.fields.company_name}{i.fields.quantity ? ` · ${qty(i.fields.quantity)}대` : ""}</span>
              {i.status === "answered" && <span className="text-[12px] text-ink-3">{i.answered_by?.split("@")[0]} 회신</span>}
              {i.doc_id && <button onClick={() => onOpenDoc(i.doc_id)} className="font-mono text-[12px] text-brand-ink hover:underline">{i.doc_id}</button>}
              {i.status === "dismissed" || i.status === "answered" ? (
                <button onClick={() => onCreate(i.mail_id)} className="text-[12px] text-brand-ink hover:underline">신청서 만들기</button>
              ) : null}
              {i.status === "dismissed" && (
                <button onClick={() => mark(i, "restore")} className="text-[12px] text-ink-3 hover:text-ink">되돌리기</button>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function Row({ i, busy, onCreate, onDismiss }: { i: LicInboxItem; busy: boolean; onCreate: () => void; onDismiss: () => void }) {
  const f = i.fields;
  const chips: [string, string | null][] = [
    ["Company", f.company_name || null], ["수량", f.quantity ? `${qty(f.quantity)}대` : null], ["PMDM ID", f.license_user_id || null],
    ["Contact", f.contact_name || null], ["E-mail", f.contact_email || null], ["PO", i.po_guess || null],
  ];
  return (
    <div className="flex flex-col gap-3 px-4 py-3.5 lg:flex-row lg:items-center">
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="min-w-0 truncate text-[14px] font-semibold">{i.subject}</span>
          <span className="shrink-0 text-[11.5px] text-ink-3">{ago(i.received_at) || i.received_at}</span>
        </div>
        <div className="mt-0.5 truncate text-[12.5px] text-ink-3">
          {i.requester || i.sender}{i.country && ` · ${i.country}`}{i.sku && ` · ${i.sku}`}{i.po_number && ` · ${i.po_number}`}
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {chips.map(([k, v]) => (
            <span key={k} className={cx("inline-flex max-w-[260px] items-center gap-1 rounded-md px-2 py-0.5 text-[12px]",
              v ? "bg-ok-soft text-ok" : "border border-dashed border-warn/60 text-warn")}>
              <span className="font-semibold">{k}</span>
              <span className="truncate">{v ?? "입력 필요"}</span>
            </span>
          ))}
        </div>
      </div>
      <div className="flex shrink-0 gap-2">
        <Btn onClick={onDismiss} busy={busy}>신청 아님</Btn>
        <Btn kind="primary" onClick={onCreate}><Icon name="key" className="h-4 w-4" />신청서 만들기</Btn>
      </div>
    </div>
  );
}
