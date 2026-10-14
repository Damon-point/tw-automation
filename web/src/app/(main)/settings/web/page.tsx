"use client";

import { CredForm } from "@/components/admin/CredForm";
import { Section } from "@/components/admin/kit";
import { Skeleton } from "@/components/ui";
import { useApi } from "@/lib/api";
import type { SettingsOverview } from "@/lib/admin";

export default function WebLoginSettings() {
  const { data: s, loading, reload } = useApi<SettingsOverview>("/api/admin/settings");
  if (loading || !s) return <Skeleton rows={4} />;
  return (
    <Section title="웹 로그인" desc="주소·화면 요소는 고정입니다. 아이디와 비밀번호만 등록합니다. 팀 공용으로 하나씩 씁니다.">
      <div className="space-y-3">
        {s.web.map((x) => (
          <CredForm key={x.target} title={x.label} sub={x.base_url} username={x.username} hasPassword={x.has_password}
                    updatedAt={x.updated_at} savePath={`/api/admin/settings/web/${x.target}`} onSaved={reload} />
        ))}
      </div>
    </Section>
  );
}
