"use client";

import { useEffect, useState } from "react";
import { Button, Field, Input, SecretInput, Section, SetBadge, useRunner } from "@/components/admin/kit";
import { Skeleton } from "@/components/ui";
import { useApi } from "@/lib/api";
import { send, type SettingsOverview } from "@/lib/admin";

export default function MailchimpSettings() {
  const { data: s, loading, reload } = useApi<SettingsOverview>("/api/admin/settings");
  const [key, setKey] = useState("");
  const [f, setF] = useState({ server_prefix: "", from_name: "", reply_to: "", subject_default: "" });
  const { busy, run } = useRunner();
  useEffect(() => {
    if (s) setF({ server_prefix: s.mailchimp.server_prefix, from_name: s.mailchimp.from_name, reply_to: s.mailchimp.reply_to, subject_default: s.mailchimp.subject_default });
  }, [s]);
  if (loading || !s) return <Skeleton rows={4} />;
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF((v) => ({ ...v, [k]: e.target.value }));
  const badFrom = /[@<>]/.test(f.from_name);

  return (
    <Section title="Mailchimp" desc="Newsletter 발송에 씁니다. 받을 그룹(태그)은 여기서 정하지 않고 Newsletter 결재 화면에서 고릅니다."
             right={<div className="flex items-center gap-2">
               {s.mailchimp.updated_at && <span className="text-[12px] text-ink-3">갱신 {s.mailchimp.updated_at}</span>}
               <SetBadge ok={s.mailchimp.has_key} noText="미등록" />
             </div>}>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="API 키" hint="끝의 '-us21' 같은 서버 표시로 접속 서버를 알아냅니다">
          <SecretInput value={key} onChange={setKey} saved={s.mailchimp.has_key} />
        </Field>
        <Field label="서버 프리픽스" hint="비우면 API 키에서 알아냅니다">
          <Input value={f.server_prefix} onChange={set("server_prefix")} placeholder="us21" className="max-w-[160px]" />
        </Field>
        <Field label="발신자 표시명" hint={badFrom ? <span className="text-err">이메일이 아니라 이름입니다 — @ 같은 기호는 쓸 수 없습니다</span> : "받는 사람에게 보이는 이름"}>
          <Input value={f.from_name} onChange={set("from_name")} placeholder="Point Mobile" />
        </Field>
        <Field label="회신 주소"><Input value={f.reply_to} onChange={set("reply_to")} placeholder="pm_ta@pointmobile.com" /></Field>
        <Field label="기본 제목" className="sm:col-span-2"><Input value={f.subject_default} onChange={set("subject_default")} placeholder="[PointMobile] OS Update Newsletter" /></Field>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="outline" busy={busy === "test"} onClick={() => run("test", () => send("/api/admin/settings/mailchimp/test"))}>연결 테스트</Button>
        <Button busy={busy === "save"} disabled={badFrom}
                onClick={() => run("save", async () => { const r = await send("/api/admin/settings/mailchimp", { api_key: key, ...f }); setKey(""); reload(); return r; })}>
          저장
        </Button>
      </div>
    </Section>
  );
}
