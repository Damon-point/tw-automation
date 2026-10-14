"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Icon } from "@/components/Icons";
import { Card, Empty, PageHead, Skeleton, Tag } from "@/components/ui";
import { useApi, type Mail } from "@/lib/api";

function kindOf(subject: string): { label: string; tone: "ok" | "warn" | "error" | "none" } | null {
  const s = subject.toUpperCase();
  if (s.includes("[FIH][APPROVAL]")) return { label: "Google 승인", tone: "ok" };
  if (s.includes("[FIH][SUBMISSION]")) return { label: "Google 제출", tone: "none" };
  if (/GMS .*APPROVAL TEST|BUILD VERSION/.test(s)) return { label: "GMS 승인 요청", tone: "warn" };
  if (/배포/.test(subject)) return { label: "배포 공지", tone: "none" };
  if (/WHITE ?LIST|화이트/.test(s)) return { label: "화이트리스트", tone: "none" };
  return null;
}

export default function MailPage() {
  const [q, setQ] = useState("");
  const [d, setD] = useState("");
  useEffect(() => { const t = setTimeout(() => setD(q), 300); return () => clearTimeout(t); }, [q]);
  const { data, loading } = useApi<{ mail: Mail[] }>(`/api/mail?limit=120&q=${encodeURIComponent(d)}`, 60000);
  return (
    <>
      <PageHead title="메일" lead="공용 메일함에서 동기화된 최근 메일. 회차 감지의 근거가 되는 메일에는 종류 표시가 붙는다." />
      <div className="mb-4 flex justify-end">
        <div className="relative w-full max-w-[320px]">
          <Icon name="search" className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="제목 · 보낸이"
                 className="w-full rounded-lg border border-line-strong bg-surface py-2 pl-9 pr-3 text-[14px] outline-none focus:border-brand" />
        </div>
      </div>
      <Card pad={false}>
        {loading ? <Skeleton rows={8} /> : data?.mail.length ? data.mail.map((m) => {
          const k = kindOf(m.subject);
          return (
            <Link key={m.id} href={`/mail/${m.id}`} className="block border-b border-line px-5 py-3 last:border-b-0 hover:bg-surface-2">
              <div className="flex flex-wrap items-center gap-2">
                {k && <Tag tone={k.tone}>{k.label}</Tag>}
                <span className="min-w-0 flex-1 truncate font-semibold" title={m.subject}>{m.subject || "(제목 없음)"}</span>
                <span className="text-[12px] text-ink-3">{m.received_at.replace(/\s\+\d{4}.*$/, "")}</span>
              </div>
              <div className="mt-0.5 flex gap-2 text-[12.5px] text-ink-3">
                <span className="truncate">{m.sender}</span>
                {m.attachments.length > 0 && <span>· 첨부 {m.attachments.length}</span>}
              </div>
              {m.snippet && <div className="mt-1 line-clamp-1 text-[12.5px] text-ink-2">{m.snippet}</div>}
            </Link>
          );
        }) : <Empty>{q ? `'${q}' 에 맞는 메일이 없습니다` : "메일이 없습니다"}</Empty>}
      </Card>
    </>
  );
}
