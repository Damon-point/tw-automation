"use client";

import { useState } from "react";
import { useApi } from "@/lib/api";
import { dur, type IssueStats } from "@/lib/issues";
import { Card, cx, Empty, Skeleton } from "@/components/ui";
import { BarChart, HBars, Kpi, Legend, OTHER, SERIES, VizStyle } from "@/components/issues/Charts";
import { Btn, IssueStatus } from "@/components/issues/kit";

const MONTHS: [number, string][] = [[12, "최근 1년"], [24, "최근 2년"], [36, "최근 3년"], [120, "전체"]];
const TOPS = [5, 8];

function Select<T extends string | number>({ value, onChange, options, label }: {
  value: T; onChange: (v: T) => void; options: [T, string][]; label: string;
}) {
  return (
    <label className="inline-flex items-center gap-1.5 text-[12.5px] text-ink-3">
      {label}
      <select value={String(value)} onChange={(e) => onChange((typeof value === "number" ? Number(e.target.value) : e.target.value) as T)}
              className="rounded-lg border border-line-strong bg-surface px-2 py-1.5 text-[13px] text-ink outline-none focus:border-brand">
        {options.map(([v, l]) => <option key={String(v)} value={String(v)}>{l}</option>)}
      </select>
    </label>
  );
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return <th className={cx("px-3 py-2 text-[11.5px] font-semibold text-ink-3", right ? "text-right" : "text-left")}>{children}</th>;
}

/**
 * 이슈 통계 — 모든 소요시간은 journal 이력에서 계산하고 주말·한국 공휴일을 뺀 업무일로 잰다.
 * Redmine 의 closed_on 은 결측·역전이 많아 쓰지 않는다. [Test] 이슈는 집계에서 뺀다.
 */
export default function StatsPage() {
  const [inc, setInc] = useState(false);
  const [basis, setBasis] = useState("target");
  const [months, setMonths] = useState(24);
  const [top, setTop] = useState(8);
  const [fresh, setFresh] = useState(0);
  const qs = new URLSearchParams({ include_excluded: inc ? "1" : "0", basis, months: String(months), top: String(top), fresh: fresh ? "1" : "0", _: String(fresh) }).toString();
  const st = useApi<IssueStats>(`/api/issues/stats?${qs}`);
  const d = st.data;
  const s = d?.summary;

  const comp = d?.composition;
  const compSeries = comp ? comp.groups.map((g, i) => ({ name: g, color: g === "기타" ? OTHER : SERIES[i] ?? OTHER, data: comp.series[g] ?? [] })) : [];

  return (
    <div className="viz-root space-y-4">
      <VizStyle />
      <div className="flex flex-wrap items-center gap-3">
        <label className="inline-flex items-center gap-2 text-[13px] text-ink-2" title="Janam 등 감시 대상에서 뺀 이슈도 함께 집계">
          <input type="checkbox" className="h-4 w-4 accent-[var(--brand)]" checked={inc} onChange={(e) => setInc(e.target.checked)} />
          제외 이슈 포함
        </label>
        <span className="text-[12px] text-ink-3">소요시간은 이력에서 계산한 업무일(주말·한국 공휴일 제외) · [Test] 이슈 제외</span>
        <Btn className="ml-auto" onClick={() => setFresh((n) => n + 1)} busy={st.loading && !!d}>다시 계산</Btn>
      </div>

      {!d ? (st.error ? <p className="text-[13px] text-err">{st.error}</p> : <Card><Skeleton rows={6} /></Card>) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
            <Kpi title="전체 이슈" value={s!.total.toLocaleString()} sub={`미해결 ${s!.open.toLocaleString()}건`} />
            <Kpi title="최근 30일" value={`신규 ${s!.new_30d}`} sub={`해결 ${s!.resolved_30d}건`} />
            <Kpi title="Lead time 평균" value={dur(s!.mean_lead)} sub={`중앙값 ${dur(s!.median_lead)} · 측정 ${s!.lead_measured.toLocaleString()}건`} hint="생성 → Resolved, 업무일" />
            <Kpi title="첫 반응 평균" value={dur(s!.mean_first)} sub={`중앙값 ${dur(s!.median_first)} · 측정 ${s!.action_measured.toLocaleString()}건`} hint="작성자 외 사람이 처음 손댄 시점 — 코멘트·상태·담당·첨부 변경 포함" />
            <Kpi title="재오픈율" value={`${s!.reopen_rate.toFixed(1)}%`} sub="한 번이라도 다시 열린 비율" />
          </div>

          <Card title="[Issue] 월별 구성" right={
            <div className="flex flex-wrap items-center gap-2">
              <Select label="기준" value={basis} onChange={setBasis} options={Object.entries(d.bases) as [string, string][]} />
              <Select label="기간" value={months} onChange={setMonths} options={MONTHS} />
              <Select label="묶음" value={top} onChange={setTop} options={TOPS.map((t) => [t, `상위 ${t}`] as [number, string])} />
            </div>
          }>
            <p className="mb-3 text-[12px] text-ink-3">
              제목이 [Issue][PM85_A9] 처럼 오면 두 번째 태그를 대상으로 본다 · PM85_A9 · PM85 PIE · PM85_O 는 PM85 한 묶음 · n={comp!.n.toLocaleString()}
            </p>
            <Legend items={compSeries.map((x) => ({ name: x.name, color: x.color }))} />
            <div className="mt-2"><BarChart labels={comp!.labels} series={compSeries} stacked height={260} /></div>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full min-w-[560px] text-[13px]">
                <thead><tr className="border-b border-line bg-surface-2"><Th>묶음</Th><Th right>건수</Th><Th right>미해결</Th><Th right>Lead time 평균</Th><Th>포함</Th></tr></thead>
                <tbody>
                  {comp!.rows.map((r, i) => (
                    <tr key={r.name} className="border-b border-line last:border-b-0">
                      <td className="px-3 py-2"><span className="mr-2 inline-block h-2.5 w-2.5 rounded-[3px] align-middle" style={{ background: r.name === "기타" ? OTHER : SERIES[i] ?? OTHER }} />{r.name}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{r.total}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{r.open}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{dur(r.lead)}</td>
                      <td className="px-3 py-2 text-[12px] text-ink-3">{r.inside}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title="월별 생성 · 해결">
            <Legend items={[{ name: "생성", color: SERIES[0] }, { name: "해결", color: SERIES[1] }]} />
            <div className="mt-2">
              <BarChart labels={d.monthly.labels} series={[{ name: "생성", color: SERIES[0], data: d.monthly.created }, { name: "해결", color: SERIES[1], data: d.monthly.resolved }]} />
            </div>
          </Card>

          <Card title="첫 반응">
            <p className="mb-3 text-[12px] text-ink-3">작성자 말고 처음 손댄 사람이 무엇을 했는지로 나눈다 · 코멘트만 세면 담당만 배정하고 넘어간 건이 &lsquo;반응 없음&rsquo;이 된다</p>
            <div className="grid gap-6 lg:grid-cols-2">
              <HBars rows={d.first_action.map((a) => ({ name: a.name, value: a.value, hint: a.what }))} />
              <div>
                <div className="mb-1 text-[12.5px] font-semibold text-ink-2">첫 반응까지 (업무일, n={d.first_hist.n.toLocaleString()})</div>
                <BarChart labels={d.first_hist.labels} series={[{ name: "이슈", color: SERIES[0], data: d.first_hist.counts }]} height={200} labelEvery={1} />
              </div>
            </div>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full min-w-[560px] text-[13px]">
                <thead><tr className="border-b border-line bg-surface-2"><Th>첫 반응</Th><Th right>건수</Th><Th right>평균</Th><Th right>중앙값</Th><Th>무엇을 했나</Th></tr></thead>
                <tbody>
                  {d.first_action.map((a) => (
                    <tr key={a.key} className="border-b border-line last:border-b-0">
                      <td className="px-3 py-2 font-medium">{a.name}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{a.value}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{dur(a.mean_days)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{dur(a.median_days)}</td>
                      <td className="px-3 py-2 text-[12px] text-ink-3">{a.what}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {d.no_action > 0 && <p className="mt-2 text-[12px] text-ink-3">작성자 말고는 아무도 손대지 않은 이슈 {d.no_action.toLocaleString()}건은 위 분모에서 빠져 있다</p>}
            </div>
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title={`Lead time (업무일, n=${d.lead_hist.n.toLocaleString()})`}>
              <BarChart labels={d.lead_hist.labels} series={[{ name: "이슈", color: SERIES[2], data: d.lead_hist.counts }]} height={220} labelEvery={1} />
            </Card>
            <Card title="상태 분포">
              <HBars rows={d.by_status} />
            </Card>
          </div>

          <Card title="제목 태그 분포">
            <HBars rows={d.by_tag} />
          </Card>

          <Card title="담당자별" pad={false}>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[620px] text-[13px]">
                <thead><tr className="border-b border-line bg-surface-2"><Th>담당자</Th><Th right>전체</Th><Th right>미해결</Th><Th right>해결</Th><Th right>Lead time 평균</Th><Th right>첫 반응 평균</Th></tr></thead>
                <tbody>
                  {d.by_assignee.map((a) => (
                    <tr key={a.name} className="border-b border-line last:border-b-0">
                      <td className="px-3 py-2 font-medium">{a.name || "(없음)"}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{a.total}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{a.open}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{a.resolved}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{dur(a.mean_lead)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{dur(a.mean_action)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title="가장 오래 열려 있는 미해결 이슈" pad={false}>
            {d.aging.length ? d.aging.map((a) => (
              <a key={a.id} href={a.url} target="_blank" rel="noreferrer" className="flex items-center gap-3 border-b border-line px-5 py-2.5 last:border-b-0 hover:bg-surface-2">
                <span className="w-[64px] shrink-0 text-right text-[13px] font-bold tabular-nums text-err">{a.days.toLocaleString()}일</span>
                <span className="w-[92px] shrink-0"><IssueStatus status={a.status} /></span>
                <span className="w-[54px] shrink-0 font-mono text-[12px] text-ink-3">#{a.id}</span>
                <span className="min-w-0 flex-1 truncate text-[13.5px]">{a.subject}</span>
                <span className="hidden shrink-0 text-[12.5px] text-ink-3 md:block">{a.assignee}</span>
              </a>
            )) : <Empty>미해결 이슈가 없습니다</Empty>}
          </Card>
        </>
      )}
    </div>
  );
}
