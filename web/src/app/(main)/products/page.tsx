"use client";

import { useState } from "react";
import { Icon } from "@/components/Icons";
import { Card, Empty, LegacyLink, PageHead, Skeleton, Tag, cx } from "@/components/ui";
import { useApi, type Me, type Product } from "@/lib/api";

const FIELDS = ["OS 파일", "개발 RN", "RN for Sales", "Service Portal", "Knowledge Base", "Dropbox FOTA", "Dropbox RN"];

export default function ProductsPage() {
  const [q, setQ] = useState("");
  const [onlyMissing, setOnlyMissing] = useState(false);
  const { data, loading } = useApi<{ products: Product[] }>(`/api/products?q=${encodeURIComponent(q)}`);
  const me = useApi<Me>("/api/me");
  const rows = (data?.products ?? []).filter((p) => !onlyMissing || p.missing.length);
  const incomplete = (data?.products ?? []).filter((p) => p.missing.length).length;

  return (
    <>
      <PageHead title="제품 관리" lead="각 단계가 쓰는 경로·문서 주소. 비어 있는 칸은 그 단계가 추측하거나 멈추는 자리다."
                right={me.data && <LegacyLink href={`${me.data.legacy_url}/products`}>고치기는 기존 화면에서</LegacyLink>} />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <button onClick={() => setOnlyMissing((v) => !v)}
                className={cx("rounded-lg border px-3 py-1.5 text-[13px] font-semibold", onlyMissing ? "border-warn bg-warn-soft text-warn" : "border-line-strong bg-surface text-ink-2")}>
          빈 칸 있는 제품만 {incomplete ? `(${incomplete})` : ""}
        </button>
        <div className="relative ml-auto w-full max-w-[280px]">
          <Icon name="search" className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="제품명 · Android"
                 className="w-full rounded-lg border border-line-strong bg-surface py-2 pl-9 pr-3 text-[14px] outline-none focus:border-brand" />
        </div>
      </div>
      <Card pad={false}>
        {loading ? <Skeleton rows={6} /> : rows.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] text-[13.5px]">
              <thead>
                <tr className="border-b border-line bg-surface-2 text-left text-[11.5px] font-semibold text-ink-3">
                  <th className="px-5 py-2.5">제품</th><th className="px-2 py-2.5">최초 OS</th>
                  {FIELDS.map((f) => <th key={f} className="px-2 py-2.5 text-center">{f}</th>)}
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.id} className="border-b border-line last:border-b-0">
                    <td className="px-5 py-2.5">
                      <div className="font-semibold">{p.product} <span className="font-normal text-ink-3">A{p.android} {p.os_type}</span></div>
                      <div className="text-[11.5px] text-ink-3">{p.category}</div>
                    </td>
                    <td className="px-2 py-2.5 font-mono text-[12.5px]">{p.initial_os_version || "—"}</td>
                    {FIELDS.map((f) => (
                      <td key={f} className="px-2 py-2.5 text-center">
                        <span className={cx("inline-block h-2.5 w-2.5 rounded-full", p.filled.includes(f) ? "bg-ok" : "bg-err/70")} title={p.filled.includes(f) ? "채워짐" : "비어 있음"} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <Empty>맞는 제품이 없습니다</Empty>}
      </Card>
      <p className="mt-3 flex items-center gap-3 text-[12px] text-ink-3">
        <span className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full bg-ok" /> 채워짐</span>
        <span className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full bg-err/70" /> 비어 있음</span>
        <Tag>Others 등 회차 감지 대상이 아닌 제품도 함께 보인다</Tag>
      </p>
    </>
  );
}
