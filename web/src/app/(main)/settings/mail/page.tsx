"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { CredForm } from "@/components/admin/CredForm";
import { Button, Section, toast } from "@/components/admin/kit";
import { Skeleton, Tag } from "@/components/ui";
import { useApi } from "@/lib/api";
import { errText, upload, type SettingsOverview } from "@/lib/admin";

type ImportResult = { name: string; status: string; subject: string; message: string; mail_id?: number };
const TONE: Record<string, "ok" | "warn" | "error" | "none"> = { added: "ok", updated: "ok", duplicate: "none", error: "error" };
const LABEL: Record<string, string> = { added: "등록", updated: "갱신", duplicate: "이미 있음", error: "실패" };

export default function MailSettings() {
  const { data: s, loading, reload } = useApi<SettingsOverview>("/api/admin/settings");
  const [results, setResults] = useState<ImportResult[]>([]);
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  async function importFiles(files: File[]) {
    if (!files.length) return;
    setBusy(true);
    try {
      const r = await upload<{ results: ImportResult[] }>("/api/admin/settings/mail-import", files);
      setResults((v) => [...r.results, ...v]);
      const n = r.results.filter((x) => x.status === "added").length;
      toast(`${r.results.length}개 중 ${n}통 등록`);
    } catch (e) {
      toast(errText(e), "err");
    } finally {
      setBusy(false);
    }
  }

  if (loading || !s) return <Skeleton rows={5} />;
  return (
    <>
      <Section title="메일 수신 — 공용 계정" desc={`메일 동기화는 이 계정 하나로만 합니다. 서버 ${s.shared_mailbox.server} (SSL) 고정.`}>
        {s.is_admin ? (
          <CredForm title="공용 수신 계정" userLabel="메일 주소" username={s.shared_mailbox.email} hasPassword={s.shared_mailbox.has_password}
                    sub={s.shared_mailbox.updated_by ? `수정 ${s.shared_mailbox.updated_by}` : undefined}
                    savePath="/api/admin/settings/shared-mailbox" testPath="/api/admin/settings/shared-mailbox/test" onSaved={reload} />
        ) : (
          <div className="text-[14px]">{s.shared_mailbox.email || "등록 전"} <span className="text-ink-3">· 관리자가 등록·수정합니다</span></div>
        )}
      </Section>

      <Section title="메일 발송" desc="앱이 보내는 메일은 공용 발송 계정으로 로그인해 나갑니다. 메일 동기화(수신)에는 쓰지 않습니다.">
        <div className="mb-4 grid gap-3 sm:grid-cols-2">
          <div className="rounded-xl bg-surface-2 px-4 py-3">
            <div className="text-[12px] font-semibold text-ink-3">보내는 사람</div>
            <div className="mt-0.5 text-[14px]">{s.sender.system_from ? `${s.sender.system_from_name} <${s.sender.system_from}>` : "본인 계정"}</div>
          </div>
          <div className="rounded-xl bg-surface-2 px-4 py-3">
            <div className="text-[12px] font-semibold text-ink-3">내 회신 주소</div>
            <div className="mt-0.5 text-[14px]">{s.sender.my_address || <span className="text-err">찾지 못함 — 사내 인원 명부에 주소를 넣으면 잡힙니다</span>}</div>
          </div>
        </div>
        {s.sender.relay_owner ? (
          <CredForm title={s.sender.relay ? "공용 발송 계정 (SMTP 로그인)" : "내 발송 계정 (SMTP)"} userLabel="메일 주소"
                    username={s.sender.email} hasPassword={s.sender.has_password} updatedAt={s.sender.updated_at}
                    savePath="/api/admin/settings/smtp" testPath="/api/admin/settings/smtp/test" onSaved={reload} />
        ) : (
          <p className="text-[13px] text-ink-3">공용 발송 계정을 쓰고 있어 개인별로 등록할 것은 없습니다.</p>
        )}
      </Section>

      <Section title="메일 파일로 직접 등록 (.eml)"
               desc="공용 수신함으로 받지 못한 메일(개인 메일함에만 온 것, 정리된 옛 메일)을 넣습니다. 등록된 메일은 동기화로 받은 메일과 같게 다뤄집니다.">
        <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
             onDrop={(e) => { e.preventDefault(); setDrag(false); importFiles(Array.from(e.dataTransfer.files)); }}
             className={`flex flex-col items-center gap-2 rounded-xl border-2 border-dashed px-4 py-8 text-center ${drag ? "border-brand bg-brand-soft" : "border-line-strong"}`}>
          <div className="text-[14px] font-semibold">여기로 끌어 놓거나</div>
          <Button variant="outline" busy={busy} onClick={() => fileRef.current?.click()}>파일 고르기</Button>
          <div className="text-[12px] text-ink-3">.eml · .msg · .txt · 여러 개 가능 · Outlook 클래식은 [다른 이름으로 저장 → .eml], 웹메일은 [다운로드]</div>
          <input ref={fileRef} type="file" multiple accept=".eml,.msg,.txt" className="hidden"
                 onChange={(e) => { importFiles(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
        </div>
        {results.length > 0 && (
          <div className="mt-3 divide-y divide-line rounded-xl border border-line">
            {results.map((r, i) => (
              <div key={i} className="flex items-center gap-3 px-4 py-2 text-[13px]">
                <Tag tone={TONE[r.status] ?? "none"}>{LABEL[r.status] ?? r.status}</Tag>
                <span className="min-w-0 flex-1 truncate">
                  {r.mail_id ? <Link className="hover:underline" href={`/mail/${r.mail_id}`}>{r.subject || r.name}</Link> : (r.subject || r.name)}
                </span>
                <span className="shrink-0 text-ink-3">{r.message}</span>
              </div>
            ))}
          </div>
        )}
      </Section>
    </>
  );
}
