"use client";

import Link from "next/link";
import { Icon } from "@/components/Icons";
import { Card, Empty, PageHead, ResultSummary, RowLink, Skeleton, StatusChip, Tag, cx } from "@/components/ui";
import { ago, skuText, useApi, type Home, type StageItem } from "@/lib/api";

function Stat({ label, value, tone, href }: { label: string; value: number; tone: "err" | "warn" | "info" | "brand" | "muted"; href: string }) {
  const color = { err: "text-err", warn: "text-warn", info: "text-info", brand: "text-brand-ink", muted: "text-ink" }[tone];
  return (
    <a href={href} className="rounded-2xl border border-line bg-surface p-4 shadow-[0_1px_2px_rgba(16,24,40,.05)] hover:shadow-md">
      <div className={cx("text-[28px] font-bold leading-none tracking-tight", value ? color : "text-ink-3")}>{value}</div>
      <div className="mt-1.5 text-[13px] text-ink-2">{label}</div>
    </a>
  );
}

function StageRow({ s, showSummary }: { s: StageItem; showSummary?: boolean }) {
  return (
    <RowLink href={`/jobs/${s.job_id}#${s.side}-${s.stage_key}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{skuText(s)} · {s.version}</span>
        <Tag>{s.side === "submission" ? "Submission" : "Approval"}</Tag>
        <span className="text-[13px] text-ink-2">{s.label}</span>
        <span className="ml-auto text-[12px] text-ink-3">{s.updated_by} · {ago(s.updated_at)}</span>
      </div>
      {showSummary && s.summary && <ResultSummary summary={s.summary} compact />}
    </RowLink>
  );
}

export default function HomePage() {
  const { data, loading, error } = useApi<Home>("/api/home", (d) => (d?.counts.running ? 5000 : 30000));
  const c = data?.counts;
  return (
    <>
      <PageHead title="오늘 할 일" lead="내 손이 필요한 것부터. 실행 중인 단계가 있으면 5초마다 새로 읽는다." />
      {error && <div className="mb-4 rounded-lg bg-err-soft px-4 py-3 text-sm text-err">{error}</div>}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="결재 대기" value={c?.approvals ?? 0} tone="err" href="#approvals" />
        <Stat label="실패한 단계" value={c?.failed ?? 0} tone="err" href="#failed" />
        <Stat label="검수 대기" value={c?.review ?? 0} tone="warn" href="#review" />
        <Stat label="실행 중" value={c?.running ?? 0} tone="info" href="#running" />
        <Stat label="신규 회차" value={c?.new_rounds ?? 0} tone="brand" href="/rounds" />
      </div>

      <div className="mt-6 grid gap-5 xl:grid-cols-2">
        <Card title="결재 대기" icon={<Icon name="stamp" />} pad={false} className="scroll-mt-6" right={<span className="text-xs text-ink-3">RN 1차 · 2차 · 뉴스레터</span>}>
          <div id="approvals" />
          {loading ? <Skeleton /> : data?.approvals.length ? data.approvals.map((a, i) => (
            <RowLink key={i} href={`/jobs/${a.job_id}`}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold">{skuText(a)} · {a.version}</span>
                <Tag tone="warn">{a.kind}</Tag>
                <span className="ml-auto text-[12px] text-ink-3">{a.requested_by} 요청 · {ago(a.requested_at)}</span>
              </div>
            </RowLink>
          )) : <Empty>결재를 기다리는 것이 없습니다</Empty>}
        </Card>

        <Card title="실패한 단계" icon={<Icon name="alert" />} pad={false} right={<span className="text-xs text-ink-3">결과에 ✘ 가 있는 검수 대기</span>}>
          <div id="failed" />
          {loading ? <Skeleton /> : data?.failed.length ? data.failed.map((s, i) => <StageRow key={i} s={s} showSummary />)
            : <Empty>실패한 단계가 없습니다</Empty>}
        </Card>

        <Card title="검수 대기" icon={<Icon name="eye" />} pad={false}>
          <div id="review" />
          {loading ? <Skeleton /> : data?.review.length ? data.review.map((s, i) => <StageRow key={i} s={s} showSummary />)
            : <Empty>검수를 기다리는 단계가 없습니다</Empty>}
        </Card>

        <div className="space-y-5">
          <Card title="실행 중" icon={<Icon name="play" />} pad={false}>
            <div id="running" />
            {loading ? <Skeleton rows={1} /> : data?.running.length ? data.running.map((s, i) => (
              <RowLink key={i} href={`/jobs/${s.job_id}`}>
                <div className="flex items-center gap-2">
                  <StatusChip status="running" />
                  <span className="font-semibold">{skuText(s)} · {s.version}</span>
                  <span className="text-[13px] text-ink-2">{s.label}</span>
                  <span className="ml-auto text-[12px] text-ink-3">{s.updated_by}</span>
                </div>
              </RowLink>
            )) : <Empty>지금 실행 중인 단계가 없습니다</Empty>}
          </Card>
          <Card title="신규 회차" icon={<Icon name="sparkle" />} pad={false} right={<Link href="/rounds" className="text-xs font-semibold text-brand-ink">전체 보기</Link>}>
            {loading ? <Skeleton rows={1} /> : data?.new_rounds.length ? data.new_rounds.map((r, i) => (
              <RowLink key={i} href="/rounds">
                <div className="flex items-center gap-2">
                  <StatusChip status="new" />
                  <span className="font-semibold">{skuText(r)} · {r.version}</span>
                  <span className="ml-auto truncate text-[12px] text-ink-3">{r.why}</span>
                </div>
              </RowLink>
            )) : <Empty>새로 감지된 회차가 없습니다</Empty>}
          </Card>
        </div>
      </div>
    </>
  );
}
