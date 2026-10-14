"use client";

import { useMemo, useRef, useState } from "react";

/*
 * 통계 탭의 작은 차트들 — 외부 라이브러리 없이 SVG/HTML 로 그린다.
 * 색은 검증된 범주 팔레트를 순서대로 쓴다(돌려 쓰지 않는다). 8개를 넘는 묶음은 '기타'로 접는다.
 * 다크 모드는 같은 색상의 다크용 단계를 쓴다.
 */

export const SERIES = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)", "var(--s6)", "var(--s7)", "var(--s8)"];
export const OTHER = "var(--s-other)";

export function VizStyle() {
  return (
    <style>{`
.viz-root {
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100; --s5:#e87ba4; --s6:#008300; --s7:#6250d6; --s8:#e34948;
  --s-other:#b8bcc6; --grid:#eceef2; --axis:#8a90a0;
}
@media (prefers-color-scheme: dark) {
  .viz-root {
    --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --s5:#d55181; --s6:#008300; --s7:#9085e9; --s8:#e66767;
    --s-other:#4a5160; --grid:#232936; --axis:#7d8494;
  }
}`}</style>
  );
}

export function Legend({ items }: { items: { name: string; color: string }[] }) {
  return (
    <div className="flex flex-wrap gap-x-3.5 gap-y-1 text-[12px] text-ink-2">
      {items.map((it) => (
        <span key={it.name} className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-[3px]" style={{ background: it.color }} />
          {it.name}
        </span>
      ))}
    </div>
  );
}

function niceMax(v: number) {
  if (v <= 4) return 4;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
  return v;
}

type Series = { name: string; color: string; data: number[] };

/** 세로 막대 — 묶음(grouped) 또는 쌓기(stacked). 열에 마우스를 올리면 값이 뜬다. */
export function BarChart({ labels, series, stacked = false, height = 240, labelEvery }: {
  labels: string[]; series: Series[]; stacked?: boolean; height?: number; labelEvery?: number;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const W = 720, H = height, L = 34, R = 8, T = 10, B = 34;
  const n = labels.length || 1;
  const totals = labels.map((_, i) => (stacked ? series.reduce((a, s) => a + (s.data[i] || 0), 0) : Math.max(0, ...series.map((s) => s.data[i] || 0))));
  const max = niceMax(Math.max(1, ...totals));
  const cw = (W - L - R) / n;
  const y = (v: number) => T + (H - T - B) * (1 - v / max);
  const every = labelEvery ?? Math.max(1, Math.ceil(n / 12));
  const ticks = [0, max / 4, max / 2, (max * 3) / 4, max];
  const gap = 2;

  const bars = useMemo(() => {
    const out: { x: number; y: number; w: number; h: number; c: string; key: string }[] = [];
    labels.forEach((_, i) => {
      const x0 = L + i * cw;
      if (stacked) {
        let acc = 0;
        const bw = Math.max(2, cw * 0.66);
        series.forEach((s, si) => {
          const v = s.data[i] || 0;
          if (!v) return;
          const top = y(acc + v), bot = y(acc);
          out.push({ x: x0 + (cw - bw) / 2, y: top, w: bw, h: Math.max(0, bot - top - (acc ? gap : 0)), c: s.color, key: `${i}-${si}` });
          acc += v;
        });
      } else {
        const inner = cw * 0.72;
        const bw = Math.max(2, (inner - gap * (series.length - 1)) / series.length);
        series.forEach((s, si) => {
          const v = s.data[i] || 0;
          out.push({ x: x0 + (cw - inner) / 2 + si * (bw + gap), y: y(v), w: bw, h: Math.max(0, y(0) - y(v)), c: s.color, key: `${i}-${si}` });
        });
      }
    });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [labels, series, stacked, max, cw]);

  const tipLeft = hover === null ? 0 : ((L + (hover + 0.5) * cw) / W) * 100;
  return (
    <div ref={wrap} className="relative" onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} className="block w-full" style={{ height: "auto" }} role="img">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="var(--grid)" strokeWidth={1} />
            <text x={L - 6} y={y(t) + 3.5} textAnchor="end" fontSize={10} fill="var(--axis)">{Math.round(t)}</text>
          </g>
        ))}
        {hover !== null && <rect x={L + hover * cw} y={T} width={cw} height={H - T - B} fill="var(--grid)" opacity={0.7} />}
        {bars.map((b) => <rect key={b.key} x={b.x} y={b.y} width={b.w} height={b.h} rx={Math.min(3, b.w / 2)} fill={b.c} />)}
        {labels.map((lb, i) => (i % every === 0 || i === n - 1) && (
          <text key={lb + i} x={L + (i + 0.5) * cw} y={H - B + 14} textAnchor="middle" fontSize={10} fill="var(--axis)">{lb}</text>
        ))}
        {labels.map((lb, i) => (
          <rect key={`h${i}`} x={L + i * cw} y={T} width={cw} height={H - T - B + 20} fill="transparent" onMouseEnter={() => setHover(i)} />
        ))}
      </svg>
      {hover !== null && (
        <div className="pointer-events-none absolute top-1 z-10 min-w-[140px] -translate-x-1/2 rounded-lg border border-line bg-surface px-3 py-2 text-[12px] shadow-lg"
             style={{ left: `${Math.min(88, Math.max(12, tipLeft))}%` }}>
          <div className="mb-1 font-semibold text-ink">{labels[hover]}</div>
          {series.filter((s) => !stacked || s.data[hover]).map((s) => (
            <div key={s.name} className="flex items-center gap-2 text-ink-2">
              <span className="h-2 w-2 rounded-[2px]" style={{ background: s.color }} />
              <span className="flex-1">{s.name}</span>
              <span className="font-semibold tabular-nums text-ink">{s.data[hover] || 0}</span>
            </div>
          ))}
          {stacked && <div className="mt-1 border-t border-line pt-1 text-right font-semibold text-ink">합계 {totals[hover]}</div>}
        </div>
      )}
    </div>
  );
}

/** 가로 막대 목록 — 분포를 이름과 함께 읽기 좋다. 값은 막대 끝에 적는다. */
export function HBars({ rows, color = "var(--s1)", colorOf, unit = "건" }: {
  rows: { name: string; value: number; hint?: string }[]; color?: string; colorOf?: (name: string) => string; unit?: string;
}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  const total = rows.reduce((a, r) => a + r.value, 0) || 1;
  return (
    <div className="space-y-1.5">
      {rows.map((r) => (
        <div key={r.name} className="group grid grid-cols-[minmax(84px,30%)_1fr] items-center gap-3 text-[12.5px]" title={r.hint ?? `${r.name}: ${r.value}${unit} (${((r.value * 100) / total).toFixed(1)}%)`}>
          <span className="truncate text-ink-2">{r.name}</span>
          <span className="flex items-center gap-2">
            <span className="h-3.5 rounded-r-[4px] transition-opacity group-hover:opacity-80" style={{ width: `${Math.max(1.5, (r.value * 100) / max)}%`, background: colorOf?.(r.name) ?? color }} />
            <span className="whitespace-nowrap tabular-nums text-ink">{r.value.toLocaleString()}<span className="ml-1 text-ink-3">{((r.value * 100) / total).toFixed(0)}%</span></span>
          </span>
        </div>
      ))}
    </div>
  );
}

/** 큰 숫자 한 칸 */
export function Kpi({ title, value, sub, hint }: { title: string; value: string; sub?: string; hint?: string }) {
  return (
    <div className="rounded-2xl border border-line bg-surface px-4 py-3.5 shadow-[0_1px_2px_rgba(16,24,40,.05)]" title={hint}>
      <div className="text-[12px] font-medium text-ink-3">{title}</div>
      <div className="mt-1 text-[24px] font-bold tracking-tight tabular-nums">{value}</div>
      {sub && <div className="mt-0.5 text-[12px] text-ink-3">{sub}</div>}
    </div>
  );
}
