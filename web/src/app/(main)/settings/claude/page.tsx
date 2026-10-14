"use client";

import { useEffect, useState } from "react";
import { Button, Section, useRunner } from "@/components/admin/kit";
import { Skeleton, cx } from "@/components/ui";
import { useApi } from "@/lib/api";
import { send, type SettingsOverview } from "@/lib/admin";

type Claude = SettingsOverview["claude"];

const OPTIONS = [
  { value: "subscription", title: "구독 — 이 PC 의 Claude Code", desc: "API 요금이 들지 않습니다. 이 PC 에서만 되고, 연장(tools)을 쓰는 호출은 이 길로 가지 못합니다." },
  { value: "api", title: "API 열쇠 — api.anthropic.com", desc: "어디서나 되고 무엇이든 되지만 쓴 만큼 요금이 붙습니다." },
] as const;

export default function ClaudeSettings() {
  const { data: s, loading } = useApi<SettingsOverview>("/api/admin/settings");
  const [c, setC] = useState<Claude | null>(null);
  const [pick, setPick] = useState("");
  const [tried, setTried] = useState<{ ok: boolean; message: string } | null>(null);
  const { busy, run } = useRunner();
  useEffect(() => { if (s) { setC(s.claude); setPick(s.claude.backend); } }, [s]);
  if (loading || !s || !c) return <Skeleton rows={4} />;
  if (!s.is_admin) return <Section title="Claude 호출">관리자만 바꿀 수 있습니다.</Section>;

  const usable = (b: string) => (b === "subscription" ? !!c.claude : c.has_key);
  return (
    <Section title="Claude 호출 방식" desc="바꾸면 다음 호출부터 바로 적용됩니다.">
      <div className="grid gap-3 sm:grid-cols-2">
        {OPTIONS.map((o) => (
          <button key={o.value} onClick={() => setPick(o.value)}
                  className={cx("rounded-xl border-2 p-4 text-left transition-colors", pick === o.value ? "border-brand bg-brand-soft" : "border-line hover:border-line-strong")}>
            <div className="flex items-center gap-2">
              <span className={cx("h-4 w-4 rounded-full border-2", pick === o.value ? "border-brand bg-brand shadow-[inset_0_0_0_2px_var(--surface)]" : "border-line-strong")} />
              <span className="text-[14px] font-bold">{o.title}</span>
              {c.backend === o.value && <span className="ml-auto rounded-full bg-ok-soft px-2 py-0.5 text-[11px] font-bold text-ok">사용 중</span>}
            </div>
            <p className="mt-1.5 text-[12.5px] text-ink-2">{o.desc}</p>
            <p className={cx("mt-2 text-[12px] font-semibold", usable(o.value) ? "text-ok" : "text-err")}>
              {o.value === "subscription"
                ? (c.claude ? `✔ ${c.claude}` : "✘ claude 명령을 찾지 못했습니다")
                : (c.has_key ? "✔ .env 에 열쇠가 있습니다" : "✘ .env 에 ANTHROPIC_API_KEY 가 없습니다")}
            </p>
          </button>
        ))}
      </div>
      {!usable(pick) && <p className="mt-3 text-[13px] text-err">이대로 저장하면 Claude 를 쓰는 단계가 실패합니다.</p>}
      {tried && <p className={cx("mt-3 rounded-lg px-3 py-2 text-[13px]", tried.ok ? "bg-ok-soft text-ok" : "bg-err-soft text-err")}>{tried.message}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="outline" busy={busy === "try"}
                onClick={() => run("try", async () => { const r = await send<{ ok: boolean; message: string }>("/api/admin/settings/claude/try"); setTried(r); return r; })}>
          지금 불러보기
        </Button>
        <Button busy={busy === "save"} disabled={pick === c.backend}
                onClick={() => run("save", async () => { const r = await send<Claude>("/api/admin/settings/claude", { backend: pick }); setC(r); return r; },
                  () => `Claude 호출을 ${pick === "api" ? "API 열쇠" : "구독"}로 바꿨습니다`)}>저장</Button>
      </div>
    </Section>
  );
}
