"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Icon } from "@/components/Icons";
import { Card, Empty, LegacyLink, Progress, ResultSummary, Skeleton, StatusChip, Tag, cx } from "@/components/ui";
import { ago, skuText, useApi, type JobDetail, type JobSide, type JobStage } from "@/lib/api";

function StageCard({ s, side, isNext }: { s: JobStage; side: string; isNext: boolean }) {
  const n = {
    done: "bg-ok text-white", review: "bg-warn text-white", running: "bg-info text-white", pending: "bg-muted-soft text-ink-2",
  }[s.status] ?? "bg-muted-soft text-ink-2";
  return (
    <li id={`${side}-${s.key}`} className={cx("relative grid scroll-mt-6 grid-cols-[36px_1fr] gap-x-3 border-b border-line px-4 py-3.5 last:border-b-0",
      isNext && "bg-brand-soft/40")}>
      <span className={cx("mt-0.5 grid h-7 w-7 place-items-center rounded-full text-[12px] font-bold", n)}>
        {s.status === "done" ? <Icon name="check" className="h-3.5 w-3.5" /> : s.summary.tone === "error" ? "!" : "•"}
      </span>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className={cx("font-bold", s.status === "pending" && "text-ink-2")}>{s.label}</span>
          <StatusChip status={s.status} />
          {s.summary.tone === "error" && <Tag tone="error">오류</Tag>}
          {s.summary.tone === "warn" && s.status !== "pending" && <Tag tone="warn">경고</Tag>}
          {isNext && <Tag tone="ok">다음 할 일</Tag>}
          {s.updated_by && s.status !== "pending" && <span className="ml-auto text-[12px] text-ink-3">{s.updated_by} · {ago(s.updated_at)}</span>}
        </div>
        {s.blocked && <div className="mt-1 text-[12.5px] text-ink-3">⏸ {s.blocked}</div>}
        {s.summary.headline && s.status !== "pending" && <div className="mt-1 truncate text-[12.5px] text-ink-2" title={s.summary.headline}>{s.summary.headline}</div>}
        {s.status !== "pending" && <ResultSummary summary={s.summary} raw={s.result} />}
      </div>
    </li>
  );
}

function SideLane({ side }: { side: JobSide }) {
  return (
    <Card pad={false} title={side.title} right={
      <div className="flex w-[150px] items-center gap-2">
        <span className="whitespace-nowrap text-[12px] text-ink-3">{side.done}/{side.total}</span>
        <Progress done={side.done} total={side.total} />
      </div>
    }>
      <ol>{side.stages.map((s) => <StageCard key={s.key} s={s} side={side.side} isNext={side.next === s.key} />)}</ol>
    </Card>
  );
}

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
  const { id } = useParams<{ id: string }>();
  const { data, loading, error } = useApi<JobDetail>(`/api/jobs/${id}`, (d) =>
    d?.sides.some((s) => s.stages.some((x) => x.status === "running")) ? 4000 : 30000);

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
          <LegacyLink href={data.legacy.osupdate}>실행 · 검수는 기존 화면에서</LegacyLink>
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
          {data.sides.map((s) => <SideLane key={s.side} side={s} />)}
        </div>
        <div className="space-y-5">
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
