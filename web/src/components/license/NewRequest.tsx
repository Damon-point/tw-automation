"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "@/components/Icons";
import { Card, Empty, Skeleton, cx } from "@/components/ui";
import { api, ago, useApi } from "@/lib/api";
import { fileToB64, type LicInboxItem, type LicMail } from "@/lib/license";
import { Btn, inputCls } from "./LicBits";

type Key = "company_name" | "quantity" | "contact_name" | "contact_email" | "license_user_id" | "note";
type Form = Record<Key, string>;
const EMPTY: Form = { company_name: "", quantity: "", contact_name: "", contact_email: "", license_user_id: "", note: "" };
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const ACCEPT = ".pdf,.doc,.docx,.xls,.xlsx,.jpg,.jpeg,.png";
const REQUIRED: [Key, string][] = [
  ["company_name", "Company"], ["quantity", "Quantity"], ["license_user_id", "PMDM ID"], ["contact_name", "Contact"], ["contact_email", "E-mail"],
];

type FromMail = LicInboxItem & { pick: string };

export default function NewRequest({ mailId, onDone, onCancel }: {
  mailId?: number | null; onDone: (docId: string, msg: string) => void; onCancel: () => void;
}) {
  const [f, setF] = useState<Form>(EMPTY);
  const [src, setSrc] = useState<Partial<Form>>({});          // 메일에서 채운 값 — 그대로면 '메일에서' 표시
  const [file, setFile] = useState<File | null>(null);
  const [mail, setMail] = useState<FromMail | null>(null);
  const [loadingMail, setLoadingMail] = useState(false);
  const [picker, setPicker] = useState(false);
  const [showBody, setShowBody] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [drag, setDrag] = useState(false);
  const inp = useRef<HTMLInputElement>(null);
  const refs = useRef<Partial<Record<Key, HTMLInputElement | HTMLTextAreaElement | null>>>({});

  const apply = useCallback((d: LicInboxItem) => {
    const v: Form = {
      company_name: d.fields.company_name || "", quantity: d.fields.quantity ? String(d.fields.quantity) : "",
      contact_name: d.fields.contact_name || "", contact_email: d.fields.contact_email || "",
      license_user_id: d.fields.license_user_id || "", note: d.note || "",
    };
    setF(v);
    setSrc(Object.fromEntries(Object.entries(v).filter(([k, x]) => x && k !== "note")) as Partial<Form>);
    setMail({ ...d, pick: d.po_guess });
    setFile(null);
    // 비어 있는 첫 칸으로
    const first = REQUIRED.find(([k]) => !v[k])?.[0];
    if (first) setTimeout(() => refs.current[first]?.focus(), 50);
  }, []);

  const loadMail = useCallback(async (id: number, rescan = false) => {
    setLoadingMail(true);
    setErr("");
    setPicker(false);
    try {
      const d = await api<LicInboxItem>(`/api/license/inbox/${id}${rescan ? "/rescan" : ""}`, rescan ? { method: "POST" } : undefined);
      apply(d);
      if (d.error) setErr(`메일을 읽지 못한 칸이 있습니다 — ${d.error}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingMail(false);
    }
  }, [apply]);

  useEffect(() => { if (mailId) loadMail(mailId); }, [mailId, loadMail]);

  const set = (k: Key) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setF({ ...f, [k]: k === "quantity" ? e.target.value.replace(/[^\d]/g, "") : e.target.value });

  function takeFile(fl: File | undefined | null) {
    if (!fl) return;
    if (fl.size > 20 * 1024 * 1024) return setErr("PO 파일은 20MB 까지입니다");
    setErr("");
    setFile(fl);
    setMail((m) => (m ? { ...m, pick: "" } : m));
  }

  const q = parseInt(f.quantity, 10);
  const empty = (k: Key) => (k === "quantity" ? !(q > 0) : !f[k].trim());
  const needPo = !(file || mail?.pick);
  const missing = [...REQUIRED.filter(([k]) => empty(k)).map(([, l]) => l), ...(needPo ? ["PO 파일"] : [])];
  const badEmail = !!f.contact_email.trim() && !EMAIL.test(f.contact_email.trim());
  const fromMailTag = (k: Key) => !!mail && !!src[k] && src[k] === f[k];

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (missing.length || badEmail) return;
    setBusy(true);
    setErr("");
    try {
      const body: Record<string, unknown> = { ...f, quantity: q };
      if (mail) body.source_mail_id = mail.mail_id;
      if (file) Object.assign(body, { po_name: file.name, po_b64: await fileToB64(file) });
      else if (mail) Object.assign(body, { mail_id: mail.mail_id, mail_attachment: mail.pick });
      const r = await api<{ doc_id: string; request_mail: { ok: boolean; error: string; to?: string[] } }>("/api/license/requests", { method: "POST", body: JSON.stringify(body) });
      onDone(r.doc_id, r.request_mail.ok ? `${r.doc_id} 등록 · 승인 요청 메일 → ${(r.request_mail.to ?? []).join(", ")}` : `${r.doc_id} 등록 · 승인 요청 메일 못 보냄: ${r.request_mail.error}`);
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    } finally {
      setBusy(false);
    }
  }

  const field = (k: Key, label: string, opts: { hint?: string; mono?: boolean; numeric?: boolean; area?: boolean; err?: string } = {}) => {
    const req = REQUIRED.some(([r]) => r === k);
    const need = req && empty(k);
    const cls = cx(inputCls, opts.mono && "font-mono", need && "border-warn/70 bg-warn-soft/40 focus:border-warn", opts.area && "min-h-[40px]");
    return (
      <label className="block">
        <span className="mb-1.5 flex items-baseline gap-2 text-[12.5px] font-semibold text-ink-2">
          {label}
          {opts.hint && <span className="font-normal text-ink-3">{opts.hint}</span>}
          {need && <span className="ml-auto text-[11.5px] font-semibold text-warn">입력 필요</span>}
          {!need && fromMailTag(k) && <span className="ml-auto inline-flex items-center gap-1 text-[11.5px] font-normal text-ok"><Icon name="mail" className="h-3 w-3" />메일에서</span>}
        </span>
        {opts.area ? (
          <textarea ref={(el) => { refs.current[k] = el; }} rows={2} className={cls} value={f[k]} onChange={set(k)} />
        ) : (
          <input ref={(el) => { refs.current[k] = el; }} className={cls} inputMode={opts.numeric ? "numeric" : undefined} value={f[k]} onChange={set(k)} />
        )}
        {opts.err && <span className="mt-1 block text-[12px] text-err">{opts.err}</span>}
      </label>
    );
  };

  return (
    <Card title="새 License 신청" icon={<Icon name="key" />}
          right={<button onClick={onCancel} className="rounded-md px-2 py-1 text-[13px] text-ink-3 hover:bg-surface-2 hover:text-ink">닫기</button>}>
      <form onSubmit={submit} className="space-y-5">
        {/* 근거 메일 */}
        <div className="overflow-hidden rounded-xl border border-line bg-surface-2">
          <div className="flex flex-wrap items-center gap-2 px-4 py-3">
            <Icon name="mail" className="h-4 w-4 text-ink-3" />
            {loadingMail ? (
              <span className="flex flex-1 items-center gap-2 text-[13px] text-ink-2">
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />메일과 첨부를 읽는 중
              </span>
            ) : mail ? (
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13.5px] font-semibold">{mail.subject}</span>
                <span className="block truncate text-[12px] text-ink-3">{mail.requester || mail.sender} · {ago(mail.received_at) || mail.received_at}</span>
              </span>
            ) : (
              <span className="flex-1 text-[13px] text-ink-2">신청 메일을 고르면 본문·첨부에서 찾은 값으로 칸을 채운다</span>
            )}
            {mail && !loadingMail && <Btn onClick={() => setShowBody((v) => !v)}>{showBody ? "원문 접기" : "원문 보기"}</Btn>}
            {mail && !loadingMail && <Btn onClick={() => loadMail(mail.mail_id, true)}>다시 읽기</Btn>}
            <Btn onClick={() => setPicker((v) => !v)}>{picker ? "접기" : mail ? "다른 메일" : "메일에서 가져오기"}</Btn>
            {mail && <Btn onClick={() => { setMail(null); setF(EMPTY); setSrc({}); }}>비우기</Btn>}
          </div>
          {mail?.reason && !loadingMail && (
            <div className="border-t border-line px-4 py-2 text-[12.5px] text-ink-2">
              <span className="font-semibold text-ink-3">읽은 내용 </span>{mail.reason}
            </div>
          )}
          {showBody && mail?.body && (
            <pre className="max-h-[320px] overflow-y-auto whitespace-pre-wrap border-t border-line bg-surface px-4 py-3 font-sans text-[12.5px] leading-relaxed text-ink-2">{mail.body}</pre>
          )}
        </div>
        {picker && <MailPicker onPick={(m) => loadMail(m.id)} />}

        <div className="grid gap-4 sm:grid-cols-2">
          {field("company_name", "Company Name")}
          {field("quantity", "Quantity (대)", { numeric: true })}
          {field("license_user_id", "PMDM ID", { hint: "License Key 에 들어가는 계정 ID", mono: true })}
          {field("contact_name", "Contact Name", { hint: "고객측 담당자" })}
          {field("contact_email", "Contact E-mail", { err: badEmail ? "이메일 형식이 아닙니다" : "" })}
          {field("note", "비고", { area: true })}
        </div>

        <div>
          <div className="mb-1.5 flex items-baseline gap-2 text-[12.5px] font-semibold text-ink-2">
            PO 파일{needPo && <span className="ml-auto text-[11.5px] font-semibold text-warn">입력 필요</span>}
          </div>
          {mail && mail.attachments.length > 0 && !file && (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {mail.attachments.map((a) => (
                <button type="button" key={a} onClick={() => setMail({ ...mail, pick: a })}
                        className={cx("inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[12.5px]",
                          mail.pick === a ? "border-brand bg-brand-soft font-semibold text-brand-ink" : "border-line-strong bg-surface text-ink-2 hover:bg-surface-2")}>
                  <Icon name="file" className="h-3.5 w-3.5" />{a}
                </button>
              ))}
            </div>
          )}
          <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
               onDrop={(e) => { e.preventDefault(); setDrag(false); takeFile(e.dataTransfer.files?.[0]); }}
               onClick={() => inp.current?.click()}
               className={cx("flex cursor-pointer items-center gap-3 rounded-xl border-2 border-dashed px-4 py-4 transition-colors",
                 drag ? "border-brand bg-brand-soft" : needPo ? "border-warn/60 hover:bg-surface-2" : "border-line-strong hover:bg-surface-2")}>
            <Icon name="file" className="h-5 w-5 text-ink-3" />
            {file ? (
              <span className="flex-1 text-[13.5px]"><b>{file.name}</b> <span className="text-ink-3">· {Math.max(1, Math.round(file.size / 1024)).toLocaleString()} KB</span></span>
            ) : (
              <span className="flex-1 text-[13.5px] text-ink-2">
                {mail?.pick ? <>메일 첨부 <b>{mail.pick}</b> 를 씁니다 · 다른 파일은 여기로 끌어 놓기</>
                  : mail ? "메일에 PO 첨부가 없습니다 · 끌어 놓거나 눌러서 고르기"
                  : "끌어 놓거나 눌러서 고르기 · PDF·Word·Excel·이미지, 20MB 까지"}
              </span>
            )}
            {file && <button type="button" onClick={(e) => { e.stopPropagation(); setFile(null); setMail((m) => (m ? { ...m, pick: m.po_guess } : m)); }} className="rounded-md px-2 py-1 text-[12.5px] text-err hover:bg-err-soft">빼기</button>}
            <input ref={inp} type="file" accept={ACCEPT} className="hidden" onChange={(e) => { takeFile(e.target.files?.[0]); e.target.value = ""; }} />
          </div>
        </div>

        {err && <div className="rounded-lg bg-err-soft px-3 py-2 text-[13px] text-err">{err}</div>}
        <div className="flex flex-wrap items-center justify-end gap-3">
          {missing.length > 0 && <span className="text-[12.5px] text-warn">남은 칸: {missing.join(" · ")}</span>}
          <Btn kind="primary" type="submit" busy={busy} disabled={missing.length > 0 || badEmail || loadingMail}>등록하고 승인 요청</Btn>
        </div>
      </form>
    </Card>
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
        )) : <Empty>찾은 메일이 없습니다</Empty>}
      </div>
    </div>
  );
}
