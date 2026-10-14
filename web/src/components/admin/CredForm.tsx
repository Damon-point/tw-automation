"use client";

import { useEffect, useState } from "react";
import { Button, Field, Input, SecretInput, SetBadge, useRunner } from "./kit";
import { send } from "@/lib/admin";

/** 아이디 + 비밀번호 한 벌. 비밀번호는 바꿀 때만 보낸다 */
export function CredForm({ title, sub, userLabel = "아이디", username, hasPassword, updatedAt, savePath, testPath, onSaved, userPlaceholder, disabled }: {
  title: string; sub?: string; userLabel?: string; username: string; hasPassword: boolean; updatedAt?: string;
  savePath: string; testPath?: string; onSaved: () => void; userPlaceholder?: string; disabled?: boolean;
}) {
  const [u, setU] = useState(username);
  const [pw, setPw] = useState("");
  const { busy, run } = useRunner();
  useEffect(() => setU(username), [username]);
  const isMail = userLabel.includes("메일");
  const body = () => (isMail ? { email: u, password: pw } : { username: u, password: pw });

  return (
    <div className="rounded-xl border border-line p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="text-[14px] font-bold">{title}</span>
        {sub && <span className="text-[12.5px] text-ink-3">{sub}</span>}
        <span className="ml-auto flex items-center gap-2">
          {updatedAt && <span className="text-[12px] text-ink-3">갱신 {updatedAt}</span>}
          <SetBadge ok={hasPassword} noText="미등록" />
        </span>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={userLabel}><Input value={u} onChange={(e) => setU(e.target.value)} placeholder={userPlaceholder} disabled={disabled} /></Field>
        <Field label={isMail ? "앱 비밀번호" : "비밀번호"}><SecretInput value={pw} onChange={setPw} saved={hasPassword} /></Field>
      </div>
      <div className="mt-3 flex justify-end gap-2">
        {testPath && <Button variant="outline" busy={busy === "test"} disabled={disabled}
                             onClick={() => run("test", () => send(testPath, body()))}>연결 테스트</Button>}
        <Button busy={busy === "save"} disabled={disabled}
                onClick={() => run("save", async () => { const r = await send(savePath, body()); setPw(""); onSaved(); return r; })}>저장</Button>
      </div>
    </div>
  );
}
