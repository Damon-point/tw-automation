"use client";

import { Section, SetBadge } from "@/components/admin/kit";
import { Skeleton, Tag } from "@/components/ui";
import { useApi } from "@/lib/api";

type Item = { label: string; set: boolean; detail: string };
type Resp = { users: { id: number; username: string; role: string; created_at: string; items: Item[] }[]; shared_uid: number };
const ROLE: Record<string, string> = { admin: "시스템 관리자", final_reviewer: "최종 승인자", mid_reviewer: "RN 승인자", user: "일반 사용자" };

export default function CredentialsPage() {
  const { data, loading } = useApi<Resp>("/api/admin/credentials");
  if (loading || !data) return <Skeleton rows={6} />;
  return (
    <Section title="계정별 자격증명 현황" desc="등록 여부와 갱신 시각만 보여 줍니다. 비밀번호·토큰 값은 내보내지 않습니다. 팀 공용 자격증명은 공용 계정 한 곳에 모입니다.">
      <div className="space-y-4">
        {data.users.map((u) => {
          const n = u.items.filter((i) => i.set).length;
          return (
            <div key={u.id} className="rounded-xl border border-line">
              <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
                <span className="font-bold">{u.username}</span>
                <Tag>{ROLE[u.role] ?? u.role}</Tag>
                {u.id === data.shared_uid && <Tag tone="ok">공용 자격증명</Tag>}
                <span className="ml-auto text-[12px] text-ink-3">{n}/{u.items.length} 등록{u.created_at && ` · 생성 ${u.created_at}`}</span>
              </div>
              {n === 0 ? (
                <div className="px-4 py-3 text-[13px] text-ink-3">등록된 자격증명이 없습니다</div>
              ) : (
                <div className="divide-y divide-line">
                  {u.items.map((i) => (
                    <div key={i.label} className="grid grid-cols-[minmax(150px,220px)_auto_minmax(0,1fr)] items-center gap-3 px-4 py-2 text-[13px]">
                      <span className="text-ink-2">{i.label}</span>
                      <SetBadge ok={i.set} noText="없음" />
                      <span className="truncate text-ink-3" title={i.detail}>{i.detail}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Section>
  );
}
