"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Button, Field, Input, Section, Switch, useRunner } from "@/components/admin/kit";
import { Skeleton } from "@/components/ui";
import { useApi } from "@/lib/api";
import { send, type SettingsOverview } from "@/lib/admin";

export default function SbSettings() {
  const { data: s, loading, reload } = useApi<SettingsOverview>("/api/admin/settings");
  const [notify, setNotify] = useState(false);
  const [who, setWho] = useState("");
  const [ex, setEx] = useState("");
  const { busy, run } = useRunner();
  useEffect(() => { if (s) { setNotify(s.sb.sb_auto_notify); setWho(s.sb.sb_assignees); setEx(s.sb.exclude_keywords); } }, [s]);
  if (loading || !s) return <Skeleton rows={4} />;
  const save = (key: string, value: string | boolean, msg: string) =>
    run(key, async () => { const r = await send("/api/admin/settings/sb", { key, value }); reload(); return r; }, () => msg);
  const dirty = who !== s.sb.sb_assignees || ex !== s.sb.exclude_keywords;

  return (
    <Section title="Solution Buffer"
             desc="Redmine salesissue 를 5분마다 모으고, Resolved 된 이슈를 Solution Buffer 문서 초안으로 만듭니다. Confluence 업로드는 늘 사람이 [업로드]를 눌러야 합니다. 바꾼 값은 다음 수집(최대 5분)부터 적용됩니다.">
      <div className="space-y-5">
        <div className="rounded-xl border border-line p-4">
          <Switch checked={notify} disabled={busy === "sb_auto_notify"} label={<b>담당자에게 검토 요청 메일 자동 발송</b>}
                  onChange={(v) => { setNotify(v); save("sb_auto_notify", v, `자동 발송을 ${v ? "켰습니다" : "껐습니다"}`); }} />
          <p className="mt-2 pl-[50px] text-[12.5px] text-ink-3">
            초안이 만들어지면 그 자리에서 Redmine 담당자에게 메일이 나갑니다. 꺼 두면 Solution Buffer 화면에서 사람이 눌러 보냅니다. 소급분은 켜도 자동으로 나가지 않습니다.
          </p>
          {s.sb.missing_accounts.length > 0 && (
            <div className="mt-3 rounded-lg bg-warn-soft px-3 py-2 text-[13px] text-warn">
              담당 문서가 있는데 앱 계정이 없는 사람: {s.sb.missing_accounts.slice(0, 8).map((m) => `${m.name}(${m.count}건)`).join(", ")}
              {s.sb.missing_accounts.length > 8 && ` 외 ${s.sb.missing_accounts.length - 8}명`}
              {s.is_admin && <> · <Link href="/settings/accounts" className="font-semibold underline">계정 만들기</Link></>}
            </div>
          )}
        </div>
        <Field label="SB 대상 담당자" hint="여기 적힌 Redmine 담당자의 이슈만 문서를 만들고 메일을 보냅니다(쉼표 구분). 비우면 담당자를 가리지 않습니다. 빠진 문서는 지우지 않고 'SB 대상 아님' 으로 남깁니다.">
          <Input value={who} onChange={(e) => setWho(e.target.value)} placeholder="daniel.chae, jayden.lee" />
        </Field>
        <Field label="감시 제외 키워드" hint="제목에 이 단어가 들어간 이슈는 감시에서 뺍니다(쉼표 구분). 지우지 않고 표시만 하므로 규칙을 바꾸면 다음 수집 때 되살아납니다.">
          <Input value={ex} onChange={(e) => setEx(e.target.value)} placeholder="janam" />
        </Field>
        <div className="flex items-center justify-between gap-3 border-t border-line pt-4 text-[12.5px] text-ink-3">
          <span>초안 자동 생성 {s.sb.sb_auto_generate ? "켬" : "끔"} · 한 번에 {s.sb.sb_per_tick}건</span>
          <Button disabled={!dirty} busy={busy === "fields"}
                  onClick={() => run("fields", async () => {
                    await send("/api/admin/settings/sb", { key: "sb_assignees", value: who.trim() });
                    const r = await send("/api/admin/settings/sb", { key: "exclude_keywords", value: ex.trim() });
                    reload();
                    return r;
                  }, () => "저장했습니다")}>저장</Button>
        </div>
      </div>
    </Section>
  );
}
