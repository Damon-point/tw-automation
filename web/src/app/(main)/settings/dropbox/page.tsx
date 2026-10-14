"use client";

import { useState } from "react";
import { Button, Field, Input, SecretInput, Section, SetBadge, useRunner } from "@/components/admin/kit";
import { Skeleton, cx } from "@/components/ui";
import { useApi } from "@/lib/api";
import { send, type SettingsOverview } from "@/lib/admin";

function Step({ n, title, done, children }: { n: number; title: string; done?: boolean; children: React.ReactNode }) {
  return (
    <div className="flex gap-3">
      <span className={cx("grid h-7 w-7 shrink-0 place-items-center rounded-full text-[13px] font-bold", done ? "bg-ok text-white" : "bg-brand-soft text-brand-ink")}>
        {done ? "✓" : n}
      </span>
      <div className="min-w-0 flex-1 pb-5">
        <div className="mb-2 text-[14px] font-bold">{title}</div>
        {children}
      </div>
    </div>
  );
}

export default function DropboxSettings() {
  const { data: s, loading, reload } = useApi<SettingsOverview>("/api/admin/settings");
  const [ak, setAk] = useState("");
  const [as, setAs] = useState("");
  const [url, setUrl] = useState("");
  const [code, setCode] = useState("");
  const { busy, run } = useRunner();
  if (loading || !s) return <Skeleton rows={4} />;
  const d = s.dropbox;

  return (
    <Section title="Dropbox"
             desc="refresh 방식으로 등록하면 접속 토큰이 저절로 갱신되어 만료되지 않습니다. App key · App secret 은 Dropbox App Console(developers.dropbox.com/apps → 해당 앱 → Settings)에 있습니다."
             right={<div className="flex items-center gap-2">
               <SetBadge ok={d.mode === "refresh"} okText="자동 갱신" noText={d.mode === "legacy" ? "만료형 토큰" : "미등록"} />
               <Button variant="outline" size="sm" busy={busy === "test"} onClick={() => run("test", () => send("/api/admin/settings/dropbox/test"))}>연결 테스트</Button>
             </div>}>
      {d.updated_at && <div className="mb-4 text-[12.5px] text-ink-3">마지막 등록 {d.updated_at}</div>}
      <Step n={1} title="앱 열쇠" done={d.has_app_key && !ak && !as}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="App key"><Input value={ak} onChange={(e) => setAk(e.target.value)} placeholder={d.has_app_key ? "저장됨 · 바꿀 때만 입력" : ""} /></Field>
          <Field label="App secret"><SecretInput value={as} onChange={setAs} saved={d.has_app_key} /></Field>
        </div>
      </Step>
      <Step n={2} title="인증 페이지에서 허용" done={!!url}>
        <Button variant="outline" busy={busy === "url"}
                onClick={() => run("url", async () => {
                  const r = await send<{ url: string }>("/api/admin/settings/dropbox/authorize-url", { app_key: ak, app_secret: as });
                  setUrl(r.url);
                  window.open(r.url, "_blank", "noopener");
                  return r;
                }, () => "인증 페이지를 열었습니다 — [허용] 후 나오는 코드를 아래에 붙여 넣으세요")}>
          인증 페이지 열기
        </Button>
        {url && <a href={url} target="_blank" rel="noreferrer" className="ml-3 text-[13px] text-brand-ink hover:underline">다시 열기 ↗</a>}
      </Step>
      <Step n={3} title="받은 코드로 등록 마치기" done={d.mode === "refresh" && !code}>
        <div className="flex flex-wrap gap-2">
          <Input value={code} onChange={(e) => setCode(e.target.value)} placeholder="인증 코드 붙여 넣기" className="max-w-[420px]" mono />
          <Button busy={busy === "finish"} disabled={!code.trim()}
                  onClick={() => run("finish", async () => {
                    const r = await send("/api/admin/settings/dropbox/finish", { app_key: ak, app_secret: as, code });
                    setCode(""); setAk(""); setAs(""); setUrl(""); reload();
                    return r;
                  })}>등록</Button>
        </div>
      </Step>
    </Section>
  );
}
