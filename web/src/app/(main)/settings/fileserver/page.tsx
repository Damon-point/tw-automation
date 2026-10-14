"use client";

import { CredForm } from "@/components/admin/CredForm";
import { Section } from "@/components/admin/kit";
import { Skeleton } from "@/components/ui";
import { useApi } from "@/lib/api";
import type { SettingsOverview } from "@/lib/admin";

export default function FileServerSettings() {
  const { data: s, loading, reload } = useApi<SettingsOverview>("/api/admin/settings");
  if (loading || !s) return <Skeleton rows={4} />;
  return (
    <Section title="파일 서버 (SMB)" desc="서버마다 계정이 다릅니다. 호스트·포트(445)는 고정입니다. .26 은 DQA FOTA 테스트의 NightlyBuild 빌드를 받을 때 씁니다.">
      <div className="space-y-3">
        {s.smb.map((x) => (
          <CredForm key={x.target} title={x.label} sub={`\\\\${x.host}`} username={x.username} hasPassword={x.has_password}
                    updatedAt={x.updated_at} savePath={`/api/admin/settings/smb/${x.target}`}
                    testPath={`/api/admin/settings/smb/${x.target}/test`} onSaved={reload} />
        ))}
      </div>
    </Section>
  );
}
