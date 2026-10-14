"use client";

import { useRef, useState } from "react";
import { Icon } from "@/components/Icons";
import { Card, Empty, Skeleton, cx } from "@/components/ui";
import { api, ago, useApi } from "@/lib/api";
import { fileToB64, type LicMail, type LicPrefill } from "@/lib/license";
import { Btn, inputCls } from "./LicBits";

type Form = { company_name: string; quantity: string; contact_name: string; contact_email: string; license_user_id: string; note: string };
const EMPTY: Form = { company_name: "", quantity: "", contact_name: "", contact_email: "", license_user_id: "", note: "" };
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const ACCEPT = ".pdf,.doc,.docx,.xls,.xlsx,.jpg,.jpeg,.png";

export default function NewRequest({ onDone, onCancel }: { onDone: (docId: string, msg: string) => void; onCancel: () => void }) {
  const [f, setF] = useState<Form>(EMPTY);
  const [file, setFile] = useState<File | null>(null);
  const [fromMail, setFromMail] = useState<{ id: number; subject: string; attachments: string[]; pick: string } | null>(null);
  const [picker, setPicker] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [drag, setDrag] = useState(false);
  const inp = useRef<HTMLInputElement>(null);

  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.value });

  function takeFile(fl: File | undefined | null) {
    if (!fl) return;
    if (fl.size > 20 * 1024 * 1024) return setErr("PO 파일은 20MB 까지입니다");
    setErr("");
    setFile(fl);
    setFromMail((m) => (m ? { ...m, pick: "" } : m));
  }

  async function applyMail(m: LicMail) {
    setPicker(false);
    try {
      const p = await api<LicPrefill>(`/api/license/mail/${m.id}/prefill`);
      setF({
        company_name: p.company_name || f.company_name, quantity: p.quantity ? String(p.quantity) : f.quantity,
        contact_name: p.contact_name || f.contact_name, contact_email: p.contact_email || f.contact_email,
        license_user_id: p.license_user_id || f.license_user_id, note: p.note,
      });
      setFromMail({ id: m.id, subject: m.subject, attachments: p.attachments, pick: file ? "" : p.po_guess });
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  const q = parseInt(f.quantity, 10);
  const missing = [
    !f.company_name.trim() && "Company", !(q > 0) && "Quantity", !f.contact_name.trim() && "Contact",
    !f.contact_email.trim() && "E-mail", !f.license_user_id.trim() && "ID", !(file || fromMail?.pick) && "PO 파일",
  ].filter(Boolean) as string[];
  const badEmail = !!f.contact_email.trim() && !EMAIL.test(f.contact_email.trim());

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (missing.length || badEmail) return;
    setBusy(true);
    setErr("");
    try {
      const body: Record<string, unknown> = { ...f, quantity: q };
      if (file) Object.assign(body, { po_name: file.name, po_b64: await fileToB64(file) });
      else if (fromMail) Object.assign(body, { mail_id: fromMail.id, mail_attachment: fromMail.pick });
      const r = await api<{ doc_id: string; request_mail: { ok: boolean; error: string; to?: string[] } }>("/api/license/requests", { method: "POST", body: JSON.stringify(body) });
      onDone(r.doc_id, r.request_mail.ok ? `${r.doc_id} 등록 · 승인 요청 메일 → ${(r.request_mail.to ?? []).join(", ")}` : `${r.doc_id} 등록 · 승인 요청 메일 못 보냄: ${r.request_mail.error}`);
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="새 License 신청" icon={<Icon name="key" />}
          right={<button onClick={onCancel} className="rounded-md px-2 py-1 text-[13px] text-ink-3 hover:bg-surface-2 hover:text-ink">닫기</button>}>
      <form onSubmit={submit} className="space-y-5">
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-dashed border-line-strong bg-surface-2 px-4 py-3">
          <Icon name="mail" className="h-4 w-4 text-ink-3" />
          {fromMail ? (
            <span className="min-w-0 flex-1 truncate text-[13px]">
              <span className="text-ink-3">메일에서 채움 · </span><b>{fromMail.subject}</b>
            </span>
          ) : (
            <span className="flex-1 text-[13px] text-ink-2">고객 메일이 있으면 보낸 사람·본문·첨부 PO 로 칸을 채운다</span>
          )}
          <Btn onClick={() => setPicker((v) => !v)}>{picker ? "접기" : fromMail ? "다른 메일" : "메일에서 가져오기"}</Btn>
          {fromMail && <Btn onClick={() => { setFromMail(null); setF(EMPTY); }}>비우기</Btn>}
        </div>
        {picker && <MailPicker onPick={applyMail} />}

        <div className="grid gap-4 sm:grid-cols-2">
          <L label="Company Name"><input className={inputCls} value={f.company_name} onChange={set("company_name")} /></L>
          <L label="Quantity (대)"><input className={inputCls} inputMode="numeric" value={f.quantity} onChange={(e) => setF({ ...f, quantity: e.target.value.replace(/[^\d]/g, "") })} /></L>
          <L label="Contact Name"><input className={inputCls} value={f.contact_name} onChange={set("contact_name")} /></L>
          <L label="Contact E-mail" err={badEmail ? "이메일 형식이 아닙니다" : ""}><input className={inputCls} value={f.contact_email} onChange={set("contact_email")} /></L>
          <L label="PMDM ID" hint="License Key 에 들어가는 계정 ID"><input className={cx(inputCls, "font-mono")} value={f.license_user_id} onChange={set("license_user_id")} /></L>
          <L label="비고"><textarea rows={1} className={cx(inputCls, "min-h-[40px]")} value={f.note} onChange={set("note")} /></L>
        </div>

        <div>
          <div className="mb-1.5 text-[12.5px] font-semibold text-ink-2">PO 파일</div>
          {fromMail && fromMail.attachments.length > 0 && !file && (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {fromMail.attachments.map((a) => (
                <button type="button" key={a} onClick={() => setFromMail({ ...fromMail, pick: a })}
                        className={cx("inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[12.5px]",
                          fromMail.pick === a ? "border-brand bg-brand-soft font-semibold text-brand-ink" : "border-line-strong bg-surface text-ink-2 hover:bg-surface-2")}>
                  <Icon name="file" className="h-3.5 w-3.5" />{a}
                </button>
              ))}
            </div>
          )}
          <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
               onDrop={(e) => { e.preventDefault(); setDrag(false); takeFile(e.dataTransfer.files?.[0]); }}
               onClick={() => inp.current?.click()}
               className={cx("flex cursor-pointer items-center gap-3 rounded-xl border-2 border-dashed px-4 py-4 transition-colors",
                 drag ? "border-brand bg-brand-soft" : "border-line-strong hover:bg-surface-2")}>
            <Icon name="file" className="h-5 w-5 text-ink-3" />
            {file ? (
              <span className="flex-1 text-[13.5px]"><b>{file.name}</b> <span className="text-ink-3">· {Math.max(1, Math.round(file.size / 1024)).toLocaleString()} KB</span></span>
            ) : (
              <span className="flex-1 text-[13.5px] text-ink-2">{fromMail?.pick ? <>메일 첨부 <b>{fromMail.pick}</b> 를 씁니다 · 다른 파일은 여기로 끌어 놓기</> : "끌어 놓거나 눌러서 고르기 · PDF·Word·Excel·이미지, 20MB 까지"}</span>
            )}
            {file && <button type="button" onClick={(e) => { e.stopPropagation(); setFile(null); }} className="rounded-md px-2 py-1 text-[12.5px] text-err hover:bg-err-soft">빼기</button>}
            <input ref={inp} type="file" accept={ACCEPT} className="hidden" onChange={(e) => { takeFile(e.target.files?.[0]); e.target.value = ""; }} />
          </div>
        </div>

        {err && <div className="rounded-lg bg-err-soft px-3 py-2 text-[13px] text-err">{err}</div>}
        <div className="flex flex-wrap items-center justify-end gap-3">
          {missing.length > 0 && <span className="text-[12.5px] text-ink-3">남은 칸: {missing.join(" · ")}</span>}
          <Btn kind="primary" type="submit" busy={busy} disabled={missing.length > 0 || badEmail}>등록하고 승인 요청</Btn>
        </div>
      </form>
    </Card>
  );
}

function L({ label, hint, err, children }: { label: string; hint?: string; err?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 flex items-baseline gap-2 text-[12.5px] font-semibold text-ink-2">
        {label}{hint && <span className="font-normal text-ink-3">{hint}</span>}
      </span>
      {children}
      {err && <span className="mt-1 block text-[12px] text-err">{err}</span>}
    </label>
  );
}

function MailPicker({ onPick }: { onPick: (m: LicMail) => void }) {
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const { data, loading } = useApi<{ mail: LicMail[] }>(`/api/license/mail-candidates?q=${encodeURIComponent(term)}`);
  return (
    <div className="overflow-hidden rounded-xl border border-line">
      <div className="flex gap-2 border-b border-line bg-surface-2 px-3 py-2">
        <div className="relative flex-1">
          <Icon name="search" className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" />
          <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); setTerm(q); } }}
                 placeholder="제목 · 보낸 사람 (비우면 License·PMDM 메일)" className={cx(inputCls, "py-1.5 pl-9")} />
        </div>
        <Btn onClick={() => setTerm(q)}>찾기</Btn>
      </div>
      <div className="max-h-[300px] overflow-y-auto">
        {loading ? <Skeleton rows={3} /> : data?.mail.length ? data.mail.map((m) => (
          <button type="button" key={m.id} onClick={() => onPick(m)} className="block w-full border-b border-line px-4 py-2.5 text-left last:border-b-0 hover:bg-surface-2">
            <div className="flex items-baseline gap-2">
              <span className="min-w-0 flex-1 truncate text-[13.5px] font-semibold">{m.subject || "(제목 없음)"}</span>
              <span className="shrink-0 text-[11.5px] text-ink-3">{ago(m.received_at) || m.received_at}</span>
            </div>
            <div className="mt-0.5 flex items-center gap-2 text-[12px] text-ink-3">
              <span className="truncate">{m.sender}</span>
              {m.attachments.length > 0 && <span className="shrink-0">· 첨부 {m.attachments.length}</span>}
            </div>
          </button>
        )) : <Empty>첨부가 있는 메일이 없습니다</Empty>}
      </div>
    </div>
  );
}
