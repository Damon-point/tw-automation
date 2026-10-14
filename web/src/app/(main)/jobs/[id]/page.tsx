"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Icon } from "@/components/Icons";
import { Card, Empty, Progress, Skeleton, Tag, cx } from "@/components/ui";
import { ago, skuText, useApi } from "@/lib/api";
import { rnLocked, type JobX, type ProgressInfo } from "@/lib/job";
import { Lane } from "@/components/job/Lane";
import { ReviewsCard } from "@/components/job/Reviews";
import { InputsCard } from "@/components/job/Inputs";
import { ToastHost } from "@/components/job/kit";

function Check({ ok, label, note }: { ok: boolean; label: string; note?: string }) {
  return (
    <li className="flex items-center gap-2.5 py-1.5 text-[13.5px]">
      <span className={cx("grid h-5 w-5 place-items-center rounded-full", ok ? "bg-ok text-white" : "border border-line-strong text-transparent")}>
        <Icon name="check" className="h-3 w-3" />
      </span>
      <span className={ok ? "" : "text-ink-2"}>{label}</span>
      {note && <span className="ml-auto text-[12px] text-ink-3">{note}</span>}
    </li>
  );
}

const DECISION: Record<string, string> = { requested: "요청됨", approved: "승인", rejected: "반려" };

export default function JobPage() {
  return <ToastHost><JobView /></ToastHost>;
}

const isRunning = (d: JobX | null) => !!d?.sides.some((s) => s.stages.some((x) => x.status === "running"))
  || Object.values(d?.run_all ?? {}).some((r) => !r.stopped);

function JobView() {
  const { id } = useParams<{ id: string }>();
  const { data, loading, error, reload } = useApi<JobX>(`/api/jobs/${id}`, (d) => isRunning(d) ? 3000 : 20000);
  const running = isRunning(data);
  const prog = useApi<Record<string, ProgressInfo>>(running ? `/api/jobs/${id}/progress` : null, running ? 1500 : undefined);
  const progress = (running && prog.data) || data?.progress || {};

  if (error) return <div className="rounded-lg bg-err-soft px-4 py-3 text-sm text-err">{error}</div>;
  if (loading || !data) return <Skeleton rows={6} />;
  const j = data.job;
  const done = data.sides.reduce((a, s) => a + s.done, 0);
  const total = data.sides.reduce((a, s) => a + s.total, 0);

  return (
    <>
      <div className="mb-1 text-[13px] text-ink-3"><Link href="/rounds" className="hover:underline">OS Update</Link> / {skuText(j)}</div>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[24px] font-bold tracking-tight">{skuText(j)} <span className="font-mono text-ink-2">{j.version}</span></h1>
          <p className="mt-1 text-[13px] text-ink-3">작업 #{j.id} · {j.created_by} 시작 · {ago(j.created_at)}</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="w-[200px]">
            <div className="mb-1 flex justify-between text-[12px] text-ink-2"><span>{done} / {total} 단계</span><span>{total ? Math.round((done * 100) / total) : 0}%</span></div>
            <Progress done={done} total={total} />
          </div>
        </div>
      </div>

      {data.facts.length > 0 && (
        <div className="mb-5 grid grid-cols-2 gap-px overflow-hidden rounded-2xl border border-line bg-line md:grid-cols-4">
          {data.facts.map((f) => (
            <div key={f.label} className="min-w-0 bg-surface px-4 py-3">
              <div className="text-[11.5px] font-semibold text-ink-3">{f.label}</div>
              {f.value.startsWith("http")
                ? <a href={f.value} target="_blank" rel="noreferrer" className="block truncate text-[13.5px] font-semibold text-brand-ink hover:underline" title={f.value}>링크 열기</a>
                : <div className="truncate text-[14px] font-semibold" title={f.value}>{f.value}</div>}
            </div>
          ))}
          {Array.from({ length: (4 - (data.facts.length % 4)) % 4 }).map((_, i) => (
            <div key={`pad${i}`} className="hidden bg-surface md:block" />
          ))}
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="space-y-5">
          {[...data.sides].sort((a, b) => (a.done === a.total ? 1 : 0) - (b.done === b.total ? 1 : 0)).map((s) => (
            <Lane key={s.side} side={s} ctx={{ job: data, reload, progress, locked: rnLocked(data) }} />
          ))}
        </div>
        <div className="space-y-5">
          <ReviewsCard job={data} reload={reload} />
          <InputsCard jobId={data.job.id} onSaved={reload} />
          <Card title="Release Note 체크" icon={<Icon name="file" />}>
            <ul>
              <Check ok={data.rn.mid === "approved"} label="1차 RN 승인" note={DECISION[data.rn.mid] ?? (data.rn.mid || "아직")} />
              <Check ok={data.rn.full_pdf} label="전체 이력 RN PDF" />
              <Check ok={data.rn.final === "approved"} label="2차 최종 검수" note={DECISION[data.rn.final] ?? (data.rn.final || "아직")} />
              <Check ok={data.rn.sales_pdf} label="RN for Sales PDF" />
              <Check ok={data.rn.sales_archived} label="RN for Sales 파일서버 보관" />
            </ul>
            {!data.rn.sales_archived && data.rn.mid === "approved" && (
              <p className="mt-2 rounded-lg bg-warn-soft px-3 py-2 text-[12.5px] text-warn">RN for Sales 가 아직 파일서버에 없습니다.</p>
            )}
          </Card>

          <Card title="Dropbox sdcard" icon={<Icon name="link" />} pad={false}>
            {data.uploads.length ? data.uploads.map((u) => (
              <a key={u.name} href={u.url} target="_blank" rel="noreferrer" className="flex items-center gap-2 border-b border-line px-5 py-2.5 last:border-b-0 hover:bg-surface-2">
                <Tag tone={u.type === "FULL" ? "ok" : "none"}>{u.type}</Tag>
                <span className="truncate font-mono text-[12.5px]">{u.name}</span>
              </a>
            )) : <Empty>아직 올리지 않음</Empty>}
          </Card>

          <Card title="결과물" icon={<Icon name="file" />} pad={false}>
            {data.artifacts.length ? data.artifacts.map((a) => (
              <a key={a.path} href={a.url || undefined} target="_blank" rel="noreferrer"
                 className={cx("block border-b border-line px-5 py-2.5 last:border-b-0", a.url && "hover:bg-surface-2")}>
                <div className="text-[11.5px] text-ink-3">{a.group}</div>
                <div className="truncate text-[13px] font-medium">{a.name}</div>
              </a>
            )) : <Empty>결과물이 없습니다</Empty>}
          </Card>

          <Card title="근거 메일" icon={<Icon name="mail" />} pad={false}>
            {data.basis.length ? data.basis.map((m) => (
              <div key={m.mail_id} className="border-b border-line px-5 py-2.5 last:border-b-0">
                <div className="truncate text-[13px] font-medium" title={m.subject}>{m.subject}</div>
                <div className="text-[11.5px] text-ink-3">#{m.mail_id} · {m.date}</div>
              </div>
            )) : <Empty>연결된 메일이 없습니다</Empty>}
          </Card>
        </div>
      </div>
    </>
  );
}
