"use client";

import { useState } from "react";
import { Button, Field, SecretInput, Section, useRunner } from "@/components/admin/kit";
import { send } from "@/lib/admin";

export default function PasswordPage() {
  const [cur, setCur] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const { busy, run } = useRunner();
  const mismatch = pw2.length > 0 && pw !== pw2;
  const ok = cur && pw.length >= 4 && pw === pw2;
  return (
    <Section title="비밀번호 변경" desc="4자 이상, 초기 비밀번호와 다른 값으로 정합니다.">
      <form className="max-w-[420px] space-y-3"
            onSubmit={(e) => { e.preventDefault(); if (ok) run("pw", async () => { const r = await send("/api/admin/me/password", { current: cur, new: pw }); setCur(""); setPw(""); setPw2(""); return r; }); }}>
        <Field label="지금 비밀번호"><SecretInput value={cur} onChange={setCur} saved={false} placeholder="" /></Field>
        <Field label="새 비밀번호"><SecretInput value={pw} onChange={setPw} saved={false} placeholder="4자 이상" /></Field>
        <Field label="새 비밀번호 확인" hint={mismatch ? <span className="text-err">두 값이 다릅니다</span> : undefined}>
          <SecretInput value={pw2} onChange={setPw2} saved={false} placeholder="" />
        </Field>
        <div className="pt-1"><Button type="submit" busy={busy === "pw"} disabled={!ok}>바꾸기</Button></div>
      </form>
    </Section>
  );
}
