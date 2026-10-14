"use client";

import { useState } from "react";
import { Button } from "@/components/admin/kit";
import { Card, Empty, PageHead, Skeleton, cx } from "@/components/ui";
import { useApi } from "@/lib/api";
import { num, usd, type UsageBucket, type UsageResp } from "@/lib/admin";

function Stat({ title, d, strong }: { title: string; d: UsageBucket; strong?: boolean }) {
  return (
    <div className={cx("rounded-2xl border bg-surface p-5", strong ? "border-brand/40" : "border-line")}>
      <div className="text-[12.5px] font-semibold text-ink-3">{title}</div>
      <div className="mt-1 text-[26px] font-bold tracking-tight tabular-nums">{usd(d.cost)}</div>
      <div className="mt-0.5 text-[12.5px] text-ink-3 tabular-nums">{num(d.tokens)} tokens · {num(d.calls)}회</div>
    </div>
  );
}

/** 단일 계열 막대 — y 축 하나, 막대 위 툴팁, 맨 끝 값만 직접 표시 */
function Bars({ rows, labelKey }: { rows: ({ [k: string]: unknown } & UsageBucket)[]; labelKey: string }) {
  if (!rows.length) return <Empty>기록이 없습니다</Empty>;
  const max = Math.max(...rows.map((r) => r.cost), 0.0001);
  const ticks = [0, 0.5, 1].map((f) => max * f);
  return (
    <div className="flex gap-2">
      <div className="flex h-[200px] w-12 flex-col-reverse justify-between text-right text-[11px] tabular-nums text-ink-3">
        {ticks.map((t, i) => <span key={i} className="-translate-y-1/2 first:translate-y-0">{usd(t)}</span>)}
      </div>
      <div className="min-w-0 flex-1">
        <div className="relative h-[200px] border-b border-line-strong">
          {[0.5, 1].map((f) => <div key={f} className="absolute inset-x-0 border-t border-dashed border-line" style={{ bottom: `${f * 100}%` }} />)}
          <div className="absolute inset-0 flex items-end gap-[2px]">
            {rows.map((r, i) => {
              const h = (r.cost / max) * 100;
              return (
                <div key={i} className="group relative flex h-full min-w-0 flex-1 items-end">
                  <div className="w-full rounded-t-[4px] bg-brand transition-opacity group-hover:opacity-80" style={{ height: `${Math.max(h, r.cost > 0 ? 1 : 0)}%` }} />
                  <div className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1 hidden -translate-x-1/2 whitespace-nowrap rounded-lg border border-line bg-surface px-2.5 py-1.5 text-[12px] shadow-lg group-hover:block">
                    <div className="font-semibold">{String(r[labelKey])}</div>
                    <div className="tabular-nums text-ink-2">{usd(r.cost)} · {num(r.tokens)} tokens · {num(r.calls)}회</div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
        <div className="mt-1 flex gap-[2px] text-[10.5px] text-ink-3">
          {rows.map((r, i) => (
            <span key={i} className="min-w-0 flex-1 truncate text-center">
              {i === 0 || i === rows.length - 1 || i % Math.ceil(rows.length / 8) === 0 ? String(r[labelKey]).slice(5) || String(r[labelKey]) : ""}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

const SCOPES = [{ v: "today", l: "오늘" }, { v: "month", l: "이번 달" }, { v: "all", l: "전체" }];

export default function UsagePage() {
  const [scope, setScope] = useState("month");
  const { data, loading, reload } = useApi<UsageResp>(`/api/admin/usage?scope=${scope}`, 60_000);
  const [table, setTable] = useState(false);

  return (
    <>
      <PageHead title="Claude 사용량" lead="이 앱이 부른 Claude 호출만 셉니다. 비용은 토큰 × 공개 단가로 계산한 추정치입니다."
                right={<Button variant="outline" size="sm" onClick={reload}>새로고침</Button>} />
      {loading || !data ? <Skeleton rows={6} /> : (
        <div className="space-y-5">
          <div className="grid gap-3 sm:grid-cols-3">
            <Stat title="오늘" d={data.totals.today} />
            <Stat title="이번 달" d={data.totals.month} strong />
            <Stat title="전체" d={data.totals.all} />
          </div>

          <div className="grid gap-5 xl:grid-cols-2">
            <Card title="일별 비용 · 최근 30일" right={<button onClick={() => setTable((v) => !v)} className="text-[12.5px] font-semibold text-brand-ink hover:underline">{table ? "막대로 보기" : "표로 보기"}</button>}>
              {table ? (
                <div className="max-h-[240px] overflow-y-auto text-[13px]">
                  {data.by_day.map((r) => (
                    <div key={r.day} className="flex justify-between border-b border-line py-1 tabular-nums last:border-b-0">
                      <span>{r.day}</span><span>{usd(r.cost)} · {num(r.calls)}회</span>
                    </div>
                  ))}
                </div>
              ) : <Bars rows={data.by_day} labelKey="day" />}
            </Card>
            <Card title="월별 비용">
              <Bars rows={data.by_month} labelKey="month" />
            </Card>
          </div>

          <Card title="작업별" pad={false}
                right={<div className="flex rounded-lg border border-line-strong p-0.5">
                  {SCOPES.map((s) => (
                    <button key={s.v} onClick={() => setScope(s.v)}
                            className={cx("rounded-md px-2.5 py-1 text-[12.5px] font-semibold", scope === s.v ? "bg-brand text-white" : "text-ink-2")}>{s.l}</button>
                  ))}
                </div>}>
            {data.by_operation.length ? (
              <table className="w-full text-[13.5px]">
                <thead>
                  <tr className="border-b border-line bg-surface-2 text-left text-[11.5px] font-semibold text-ink-3">
                    <th className="px-5 py-2">작업</th><th className="px-3 py-2 text-right">호출</th><th className="px-3 py-2 text-right">토큰</th>
                    <th className="px-3 py-2 text-right">비용</th><th className="w-[28%] px-5 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {(() => {
                    const top = Math.max(...data.by_operation.map((o) => o.cost), 0.0001);
                    return data.by_operation.map((o) => (
                      <tr key={o.operation} className="border-b border-line last:border-b-0">
                        <td className="px-5 py-2">{o.operation || "(이름 없음)"}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{num(o.calls)}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{num(o.tokens)}</td>
                        <td className="px-3 py-2 text-right font-semibold tabular-nums">{usd(o.cost)}</td>
                        <td className="px-5 py-2"><div className="h-1.5 rounded-full bg-muted-soft"><div className="h-full rounded-full bg-brand" style={{ width: `${(o.cost / top) * 100}%` }} /></div></td>
                      </tr>
                    ));
                  })()}
                </tbody>
              </table>
            ) : <Empty>기록이 없습니다</Empty>}
          </Card>

          <Card title="최근 호출" pad={false}>
            <div className="max-h-[480px] overflow-auto">
              <table className="w-full min-w-[640px] text-[13px]">
                <thead className="sticky top-0 bg-surface-2">
                  <tr className="border-b border-line text-left text-[11.5px] font-semibold text-ink-3">
                    <th className="px-5 py-2">시각</th><th className="px-3 py-2">작업</th><th className="px-3 py-2">모델</th>
                    <th className="px-3 py-2 text-right">in / out</th><th className="px-5 py-2 text-right">비용</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent.map((r, i) => (
                    <tr key={i} className="border-b border-line last:border-b-0">
                      <td className="px-5 py-1.5 font-mono text-[12px] text-ink-3">{r.ts.slice(0, 19).replace("T", " ")}</td>
                      <td className="px-3 py-1.5">{r.operation}</td>
                      <td className="px-3 py-1.5 font-mono text-[12px] text-ink-2">{r.model}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{num(r.input_tokens)} / {num(r.output_tokens)}</td>
                      <td className="px-5 py-1.5 text-right tabular-nums">{usd(r.cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}
    </>
  );
}
