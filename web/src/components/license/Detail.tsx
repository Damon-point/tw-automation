"use client";

import { useEffect, useState } from "react";
import { Icon } from "@/components/Icons";
import { Card, Skeleton, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { qty, type LicDetail, type LicStep } from "@/lib/license";
import { Btn, Field, LicStatusChip, Pipeline, inputCls } from "./LicBits";

export default function Detail({ docId, onChanged, onDeleted }: { docId: string; onChanged: () => void; onDeleted: () => void }) {
  const [d, setD] = useState<LicDetail | null>(null);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState("");
  const [steps, setSteps] = useState<LicStep[] | null>(null);
  const [msg, setMsg] = useState("");
  const [confirmDel, setConfirmDel] = useState(false);
  const [delReason, setDelReason] = useState("");
  const [preview, setPreview] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let alive = true;
    setD(null); setErr(""); setSteps(null); setMsg(""); setConfirmDel(false); setPreview(false);
    api<LicDetail>(`/api/license/requests/${encodeURIComponent(docId)}`)
      .then((x) => { if (alive) { setD(x); setNote(x.admin_note || ""); } })
      .catch((e) => alive && setErr(e.message));
    return () => { alive = false; };
  }, [docId]);

  async function run(name: string, path: string, body?: unknown, after?: (r: unknown) => void) {
    setBusy(name); setErr(""); setMsg("");
    try {
      const r = await api<unknown>(`/api/license/requests/${encodeURIComponent(docId)}/${path}`, { method: "POST", body: JSON.stringify(body ?? {}) });
      after?.(r);
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }
  const take = (r: unknown) => { const x = r as LicDetail; setD(x); setNote(x.admin_note || ""); };
  const takeSteps = (r: unknown) => { const x = r as { steps: LicStep[]; detail: LicDetail }; setSteps(x.steps); take(x.detail); };

  if (err && !d) return <Card><div className="text-[13.5px] text-err">{err}</div></Card>;
  if (!d) return <Card pad={false}><Skeleton rows={5} /></Card>;

  const pending = d.status === "대기중";
  const approved = d.status === "승인";
  const rejected = d.status === "반려";
  const lastMail = d.mails[d.mails.length - 1];
  const mailOk = d.mails.some((m) => m.ok);

  const flow = [
    { label: "신청", state: "done" as const, sub: `${d.date_issued} · ${d.created_by_name || "—"}` },
    { label: "License Key", state: rejected ? (d.has_key ? "done" as const : "skip" as const) : d.has_key ? (d.verify && !d.verify.ok ? "fail" as const : "done" as const) : pending ? "now" as const : "todo" as const,
      sub: d.has_key ? (d.verify ? (d.verify.ok ? "검증 일치" : "검증 불일치") : "발급됨") : pending ? "승인 때 자동 생성" : "" },
    { label: rejected ? "반려" : "결재", state: pending ? "now" as const : rejected ? "fail" as const : "done" as const,
      sub: pending ? "결재 대기" : `${d.confirm_date} · ${d.confirm_by}` },
    { label: "인증서", state: rejected ? "skip" as const : d.has_cert ? "done" as const : approved ? "fail" as const : "todo" as const,
      sub: approved && !d.has_cert ? "생성 안 됨" : "" },
    { label: "결과 메일", state: pending ? "todo" as const : mailOk ? "done" as const : lastMail ? "fail" as const : d.can_admin ? "now" as const : "todo" as const,
      sub: lastMail ? `${lastMail.at.slice(5, 16).replace("T", " ")}${lastMail.shadow ? " · 검증 모드" : ""}` : "" },
  ];

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="font-mono text-[12px] text-ink-3">{d.doc_id}</div>
            <h2 className="mt-0.5 truncate text-[19px] font-bold">{d.company_name}</h2>
            <div className="mt-1 text-[13px] text-ink-2">{qty(d.quantity)}대 · {d.part_number} {d.type && `(${d.type})`}</div>
          </div>
          <LicStatusChip status={d.status} />
        </div>
        <div className="mt-5 rounded-xl bg-surface-2 px-3 py-4"><Pipeline steps={flow} /></div>

        {steps && (
          <ul className="mt-4 space-y-1.5">
            {steps.map((s, i) => (
              <li key={i} className={cx("flex gap-2 rounded-lg px-3 py-1.5 text-[13px]", s.ok ? "bg-ok-soft text-ok" : "bg-err-soft text-err")}>
                <span className="font-bold">{s.ok ? "✔" : "✘"}</span><span className="break-all">{s.msg}</span>
              </li>
            ))}
          </ul>
        )}
        {msg && <div className="mt-3 rounded-lg bg-ok-soft px-3 py-2 text-[13px] text-ok">{msg}</div>}
        {err && <div className="mt-3 rounded-lg bg-err-soft px-3 py-2 text-[13px] text-err">{err}</div>}

        <div className="mt-5 grid gap-x-6 gap-y-3.5 sm:grid-cols-2">
          <Field label="담당자">{d.contact_name}{d.contact_email && <span className="text-ink-3"> · {d.contact_email}</span>}</Field>
          <Field label="PMDM ID" mono>{d.license_user_id}</Field>
          <Field label="신청일">{d.date_issued}</Field>
          <Field label="신청자">{d.created_by_name}</Field>
          {d.note && <div className="sm:col-span-2"><Field label="비고"><span className="whitespace-pre-wrap">{d.note}</span></Field></div>}
          {rejected && !d.can_admin && <div className="sm:col-span-2"><Field label="반려 사유"><span className="text-err">{d.admin_note}</span></Field></div>}
        </div>

        <div className="mt-5 flex flex-wrap gap-2">
          {d.po && <FileLink href={d.po.url} label={`PO · ${d.po.name}`} />}
          {d.cert && <FileLink href={d.cert.url} label="License 인증서 PDF" strong />}
        </div>
      </Card>

      {(d.license_key || (d.can_admin && pending)) && (
        <Card title="License Key" icon={<Icon name="key" />}
              right={d.can_admin && pending && (
                <Btn busy={busy === "key"} onClick={() => run("key", "key", {}, take)}>{d.license_key ? "다시 만들기" : "만들기"}</Btn>
              )}>
          {d.license_key ? (
            <>
              <div className="flex items-start gap-2">
                <code className="block max-h-[92px] min-w-0 flex-1 overflow-y-auto break-all rounded-lg border border-line bg-surface-2 px-3 py-2 font-mono text-[12px] leading-relaxed">{d.license_key}</code>
                <Btn onClick={() => { navigator.clipboard?.writeText(d.license_key); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>{copied ? "복사됨" : "복사"}</Btn>
              </div>
              {d.verify && (
                <div className={cx("mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg px-3 py-2 text-[12.5px]", d.verify.ok ? "bg-ok-soft text-ok" : "bg-err-soft text-err")}>
                  <b>{d.verify.ok ? "공개키 검증 일치" : d.verify.error ? d.verify.error : "공개키 검증 불일치"}</b>
                  {d.verify.id !== undefined && <span>ID {d.verify.id} {d.verify.id_ok ? "✔" : `✘ 신청 ${d.license_user_id}`}</span>}
                  {d.verify.count !== undefined && <span>수량 {d.verify.count} {d.verify.count_ok ? "✔" : `✘ 신청 ${d.quantity}`}</span>}
                  {d.verify.issued_at && <span className="opacity-80">발급 {d.verify.issued_at}</span>}
                </div>
              )}
            </>
          ) : <div className="text-[13px] text-ink-3">아직 없음 · 승인하면 자동으로 만든다</div>}
        </Card>
      )}

      {d.can_admin && (
        <Card title={pending ? "결재" : "처리"} icon={<Icon name="stamp" />}>
          <label className="block">
            <span className="mb-1.5 block text-[12.5px] font-semibold text-ink-2">{pending ? "메모 · 반려 사유" : "관리자 메모"}</span>
            <textarea rows={3} className={inputCls} value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          {pending ? (
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <Btn kind="primary" busy={busy === "approve"} onClick={() => run("approve", "approve", { note }, takeSteps)}>
                <Icon name="check" className="h-4 w-4" /> 승인 · 인증서 · 결과 메일
              </Btn>
              <Btn kind="danger-ghost" busy={busy === "reject"} disabled={!note.trim()} onClick={() => run("reject", "reject", { note }, takeSteps)}>반려</Btn>
              {!note.trim() && <span className="text-[12px] text-ink-3">반려는 사유를 적어야 누를 수 있다</span>}
            </div>
          ) : (
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <Btn busy={busy === "note"} disabled={note === (d.admin_note || "")} onClick={() => run("note", "note", { note }, (r) => { take(r); setMsg("메모 저장"); })}>메모 저장</Btn>
              {approved && (
                <Btn busy={busy === "cert"} onClick={() => run("cert", "cert", {}, (r) => { take(r); setMsg("인증서를 새로 만들었습니다"); })}>
                  인증서 {d.has_cert ? "다시 만들기" : "만들기"}
                </Btn>
              )}
              <Btn kind={mailOk ? "ghost" : "primary"} busy={busy === "send"} disabled={approved && !d.has_cert}
                   onClick={() => run("send", "send", {}, (r) => { take(r); setMsg("결과 메일을 보냈습니다"); })}>
                <Icon name="mail" className="h-4 w-4" /> 결과 메일 {mailOk ? "다시 보내기" : "보내기"}
              </Btn>
              {d.mail_preview && <button onClick={() => setPreview((v) => !v)} className="ml-auto text-[12.5px] font-semibold text-brand-ink hover:underline">{preview ? "메일 미리보기 접기" : "메일 미리보기"}</button>}
            </div>
          )}
          {preview && d.mail_preview && (
            <div className="mt-3 overflow-hidden rounded-xl border border-line">
              <div className="space-y-0.5 border-b border-line bg-surface-2 px-4 py-2.5 text-[12.5px]">
                <div><span className="text-ink-3">받는 사람 </span>{d.mail_preview.to}</div>
                <div><span className="text-ink-3">제목 </span><b>{d.mail_preview.subject}</b></div>
                {d.mail_preview.attachment && <div><span className="text-ink-3">첨부 </span>{d.mail_preview.attachment}</div>}
              </div>
              <pre className="max-h-[260px] overflow-auto whitespace-pre-wrap px-4 py-3 font-[family-name:var(--font-sans)] text-[13px] leading-relaxed">{d.mail_preview.body}</pre>
            </div>
          )}
          {d.mails.length > 0 && (
            <div className="mt-4">
              <div className="mb-1.5 text-[12px] font-semibold text-ink-3">보낸 기록</div>
              <ul className="space-y-1">
                {[...d.mails].reverse().map((m, i) => (
                  <li key={i} className="flex flex-wrap items-center gap-x-2 text-[12.5px]">
                    <span className={m.ok ? "text-ok" : "text-err"}>{m.ok ? "✔" : "✘"}</span>
                    <span className="font-mono text-[11.5px] text-ink-3">{m.at.replace("T", " ").slice(0, 16)}</span>
                    <span>{m.kind === "approval" ? "승인" : "반려"}{m.resend && " 재발송"} → {m.to}</span>
                    <span className="text-ink-3">· {m.by}{m.attached && " · 인증서 첨부"}{m.shadow && " · 검증 모드 기록"}</span>
                    {m.error && <span className="text-err">{m.error}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="mt-5 border-t border-line pt-4">
            {confirmDel ? (
              <div className="rounded-xl border border-err/40 bg-err-soft/50 p-3">
                <div className="text-[13px] font-semibold text-err">{d.doc_id} 삭제 · 삭제일·삭제자·사유는 기록으로 남는다</div>
                <input className={cx(inputCls, "mt-2")} placeholder="삭제 사유" value={delReason} onChange={(e) => setDelReason(e.target.value)} />
                <div className="mt-2 flex justify-end gap-2">
                  <Btn onClick={() => setConfirmDel(false)}>그만두기</Btn>
                  <Btn kind="danger" busy={busy === "del"} onClick={() => run("del", "delete", { reason: delReason }, () => onDeleted())}>삭제</Btn>
                </div>
              </div>
            ) : (
              <button onClick={() => setConfirmDel(true)} className="text-[12.5px] font-semibold text-err hover:underline">이 요청 삭제</button>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}

function FileLink({ href, label, strong }: { href: string; label: string; strong?: boolean }) {
  return (
    <a href={href} target="_blank" rel="noreferrer"
       className={cx("inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[13px] font-semibold",
         strong ? "border-brand/40 bg-brand-soft text-brand-ink hover:bg-brand-soft/70" : "border-line-strong bg-surface text-ink hover:bg-surface-2")}>
      <Icon name="file" className="h-4 w-4" />{label}
    </a>
  );
}
