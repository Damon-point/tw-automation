"use client";

import { useEffect, useState } from "react";
import { Tag } from "@/components/ui";
import { errText, post, type Draft, type UploadRow, type VerifyItem, type VerifyResult } from "@/lib/whitelist";
import { Btn, Field, Input, Modal, Note, SubHead, Table, Textarea, useToast } from "./kit";

/** 업로드 엑셀에 실제로 들어갈 행 — 열 이름·순서가 업로드 파일 머리와 같다 */
/** pick 을 주면 맨 앞에 체크 — 체크를 푼 S/N 은 보내지 않는다(excluded 로 흐리게). */
export function UploadTable({ rows, max = 300, pick }: {
  rows: UploadRow[]; max?: number;
  pick?: { on: (sn: string) => boolean; set: (sn: string, v: boolean) => void; all: boolean; setAll: (v: boolean) => void };
}) {
  return (
    <Table<UploadRow> max={max} rows={rows} empty="대상 없음"
      rowTone={(r) => (r.status.startsWith("⚠") && !r.excluded ? "warn" : undefined)}
      cols={[
        ...(pick ? [{
          key: "pick", w: "28px",
          label: <input type="checkbox" aria-label="전체 선택" checked={pick.all} onChange={(e) => pick.setAll(e.target.checked)} className="accent-[var(--brand)]" />,
          render: (r: UploadRow) => <input type="checkbox" checked={pick.on(r.sn)} onChange={(e) => pick.set(r.sn, e.target.checked)} className="accent-[var(--brand)]" />,
        }] : []),
        { key: "sn", label: "SN", mono: true, render: (r) => <span className={r.excluded ? "text-ink-3 line-through" : ""}>{r.sn}</span> },
        { key: "api", label: "Android API" },
        { key: "end", label: "Support end date" },
        { key: "pn", label: "Partnumber" },
        { key: "ostype", label: "ostype", mono: true },
        { key: "status", label: "상태", render: (r) => r.excluded ? <span className="text-ink-3">제외 — 보내지 않음</span>
          : <span className={r.status.startsWith("⚠") ? "text-warn" : "text-ok"}>{r.status}</span> },
      ]} />
  );
}

export function DateTable({ rows }: { rows: { sn: string; reg: string; end: string }[] }) {
  return (
    <Table rows={rows} cols={[
      { key: "sn", label: "sn", mono: true },
      { key: "reg", label: "update_date_time (등록일)" },
      { key: "end", label: "support_end_date (등록일+1년)" },
    ]} />
  );
}

/** FOTA 에서 다시 읽어 기대값과 대조 — 보냈다는 응답만으로 회신하지 않는다 */
export function VerifyPanel({ items, kind, result, onResult }: {
  items: VerifyItem[]; kind: string; result: VerifyResult | null; onResult: (r: VerifyResult) => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  async function run() {
    setBusy(true);
    try {
      const r = await post<VerifyResult>("/api/wl/verify", { items });
      onResult(r);
      if (r.error) toast("error", r.error);
      else toast(r.ok ? "ok" : "warn", `일치 ${r.matched}/${r.total}`);
    } catch (e) {
      toast("error", errText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div>
      <SubHead right={<Btn kind="primary" small busy={busy} disabled={!items.length} onClick={run}>FOTA 에서 확인</Btn>}>
        {kind || "처리"} · 대상 {items.length.toLocaleString()}대
      </SubHead>
      {!items.length ? <Note>처리를 실행하면 확인할 대상이 생깁니다.</Note>
        : !result ? <Note>아직 확인하지 않았습니다.</Note>
        : result.error ? <Note tone="error">✘ {result.error}</Note>
        : (
          <div className="space-y-2">
            <div className={result.ok ? "text-[14px] font-bold text-ok" : "text-[14px] font-bold text-err"}>
              일치 {result.matched}/{result.total}
              {result.missing ? ` · 미등록 ${result.missing}` : ""}{result.mismatched ? ` · 값 불일치 ${result.mismatched}` : ""}
            </div>
            {result.hidden > 0 && <div className="text-[12px] text-ink-3">{(result.rows.length + result.hidden).toLocaleString()}대 중 {result.rows.length}줄 — 안 맞는 것부터</div>}
            <Table rows={result.rows} rowTone={(r) => (r.ok ? undefined : "error")}
              cols={[
                { key: "sn", label: "sn", mono: true },
                { key: "date", label: "supportEndDate", render: (r) => r.date || "-" },
                { key: "osType", label: "osType", mono: true, render: (r) => r.osType || "-" },
                { key: "androidApi", label: "api", render: (r) => r.androidApi || "-" },
                { key: "ok", label: "", render: (r) => r.ok ? <span className="text-ok">✔</span> : <span className="text-err">{r.diffs.join(" · ") || "✘"}</span> },
              ]} />
          </div>
        )}
    </div>
  );
}

/** 회신 — 초안을 받아 다듬고, 미리보기로 실제 서식(인용·서명)을 본 뒤 보낸다 */
export function ReplyComposer({ tid, draftReq, withCc, requestId, note, canSend, onSent, onSkip }: {
  tid: string; draftReq: { kind: string; new_date?: string; sns?: string[] }; withCc?: boolean; requestId?: string;
  note?: string; canSend: boolean; onSent: () => void; onSkip?: () => void;
}) {
  const toast = useToast();
  const [d, setD] = useState<Draft | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [prev, setPrev] = useState<{ from: string; html: string } | null>(null);
  const key = JSON.stringify(draftReq);

  useEffect(() => {
    let off = false;
    post<Draft>(`/api/wl/threads/${tid}/reply-draft`, JSON.parse(key)).then((x) => {
      if (off) return;
      setD(x); setSubject(x.subject); setBody(x.body);
    }).catch((e) => toast("error", errText(e)));
    return () => { off = true; };
  }, [tid, key, toast]);

  async function preview() {
    try {
      setPrev(await post(`/api/wl/threads/${tid}/reply-preview`, { subject, body, with_cc: !!withCc }));
    } catch (e) { toast("error", errText(e)); }
  }
  async function send() {
    setBusy(true);
    try {
      await post(`/api/wl/threads/${tid}/reply`, { subject, body, with_cc: !!withCc, request_id: requestId ?? "", note: note ?? "회신 완료" });
      toast("ok", "회신을 보냈습니다");
      onSent();
    } catch (e) {
      toast("error", errText(e));
    } finally {
      setBusy(false);
    }
  }
  if (!d) return <div className="h-40 animate-pulse rounded-xl bg-muted-soft" />;
  return (
    <div className="space-y-3">
      <div className="grid gap-1 rounded-lg bg-surface-2 px-3 py-2 text-[12.5px] text-ink-2">
        <div><span className="inline-block w-16 text-ink-3">받는 사람</span>{d.to || <span className="text-err">주소 없음</span>}</div>
        {withCc && <div><span className="inline-block w-16 text-ink-3">참조</span>{d.cc || "원본에 참조 없음"} <Tag>전체 회신</Tag></div>}
      </div>
      <Field label="제목"><Input value={subject} onChange={(e) => setSubject(e.target.value)} /></Field>
      <Field label="본문"><Textarea rows={11} value={body} onChange={(e) => setBody(e.target.value)} /></Field>
      <div className="flex flex-wrap items-center gap-2">
        <Btn kind="ghost" onClick={preview}>미리보기</Btn>
        <div className="flex-1" />
        {onSkip && <Btn kind="ghost" onClick={onSkip}>회신 없이 닫기</Btn>}
        <Btn kind="primary" busy={busy} disabled={!canSend || !d.to} onClick={send}>회신 보내기</Btn>
      </div>
      <Modal open={!!prev} onClose={() => setPrev(null)} title="보낼 메일 미리보기" wide>
        <div className="mb-3 grid gap-1 text-[12.5px] text-ink-2">
          <div><span className="inline-block w-20 text-ink-3">보내는 사람</span>{prev?.from || "발송 계정 미등록"}</div>
          <div><span className="inline-block w-20 text-ink-3">받는 사람</span>{d.to}</div>
          {withCc && <div><span className="inline-block w-20 text-ink-3">참조</span>{d.cc || "-"}</div>}
          <div><span className="inline-block w-20 text-ink-3">제목</span>{subject}</div>
        </div>
        <iframe sandbox="" srcDoc={prev?.html} className="h-[60vh] w-full rounded-lg border border-line bg-white" />
      </Modal>
    </div>
  );
}

export function DryBadge({ dry }: { dry: boolean }) {
  return dry ? <Tag tone="warn">미리보기 · 반영 안 됨</Tag> : <Tag tone="ok">실제 반영</Tag>;
}
