"use client";

import Link from "next/link";
import { use, useState } from "react";
import { Card, Empty, Skeleton, cx } from "@/components/ui";
import { useApi } from "@/lib/api";
import { bytes, type MailDetail } from "@/lib/admin";

function Addr({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <div className="grid grid-cols-[64px_minmax(0,1fr)] gap-2 text-[13px]">
      <span className="text-ink-3">{label}</span>
      <span className="break-all text-ink-2">{value}</span>
    </div>
  );
}

export default function MailDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: m, loading, error } = useApi<MailDetail>(`/api/admin/mail/${id}`);
  const [mode, setMode] = useState<"html" | "text">("html");

  if (loading) return <Skeleton rows={6} />;
  if (error || !m) return <Card><Empty>{error || "메일을 찾을 수 없습니다"}</Empty></Card>;
  const view = m.html && mode === "html" ? "html" : "text";
  return (
    <>
      <Link href="/mail" className="mb-3 inline-block text-[13px] font-semibold text-ink-3 hover:text-ink">← 메일</Link>
      <Card>
        <h1 className="text-[19px] font-bold leading-snug">{m.subject || "(제목 없음)"}</h1>
        <div className="mt-3 space-y-1">
          <Addr label="보낸 사람" value={m.sender} />
          <Addr label="받는 사람" value={m.to} />
          <Addr label="참조" value={m.cc} />
          <Addr label="받은 시각" value={m.received_at} />
        </div>
        {m.attachments.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2">
            {m.attachments.map((a) => a.available ? (
              <a key={a.name} href={`/api/admin/mail/${m.id}/attachment?name=${encodeURIComponent(a.name)}`}
                 className="inline-flex items-center gap-2 rounded-lg border border-line-strong bg-surface px-3 py-1.5 text-[12.5px] hover:bg-surface-2">
                <span className="font-semibold">{a.name}</span><span className="text-ink-3">{bytes(a.size)}</span>
              </a>
            ) : (
              <span key={a.name} className="inline-flex items-center gap-2 rounded-lg border border-dashed border-line-strong px-3 py-1.5 text-[12.5px] text-ink-3">
                {a.name} · 보관본 없음
              </span>
            ))}
          </div>
        )}
        {m.serials.length > 0 && (
          <details className="mt-3 text-[12.5px]">
            <summary className="cursor-pointer font-semibold text-ink-2">첨부에서 찾은 SN {m.serials.length}개</summary>
            <div className="mt-1.5 flex flex-wrap gap-1 font-mono">{m.serials.map((s) => <span key={s} className="rounded bg-surface-2 px-1.5 py-0.5">{s}</span>)}</div>
          </details>
        )}
      </Card>
      <Card className="mt-4" pad={false}
            title="본문"
            right={m.html && m.text ? (
              <div className="flex rounded-lg border border-line-strong p-0.5">
                {(["html", "text"] as const).map((k) => (
                  <button key={k} onClick={() => setMode(k)} className={cx("rounded-md px-2.5 py-1 text-[12px] font-semibold", mode === k ? "bg-brand text-white" : "text-ink-2")}>
                    {k === "html" ? "서식" : "글자만"}
                  </button>
                ))}
              </div>
            ) : undefined}>
        {view === "html" ? (
          // 스크립트는 sandbox 로 막는다 — 메일 본문은 외부에서 온 것이다
          <iframe sandbox="" srcDoc={m.html} title="메일 본문" className="h-[70vh] w-full rounded-b-2xl border-0 bg-white" />
        ) : m.text ? (
          <pre className="whitespace-pre-wrap break-words px-5 py-4 font-[family-name:var(--font-sans)] text-[13.5px] leading-relaxed">{m.text}</pre>
        ) : <Empty>본문이 없습니다</Empty>}
      </Card>
    </>
  );
}
