"use client";

import { useMemo, useState } from "react";
import { Button, SearchBox, Toaster, toast } from "@/components/admin/kit";
import { Card, Empty, PageHead, Skeleton, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { errText } from "@/lib/admin";

type Kind = "osinfo" | "downloadpath" | "whitelist";
const TABS: { kind: Kind; label: string; cols: [string, string][] }[] = [
  { kind: "osinfo", label: "OsInfo", cols: [["id", "ID"], ["os_type", "OS_Type"], ["version", "Version"], ["os_version", "OS_Version"], ["description", "Description"], ["release_note", "Release_Note"], ["release_url", "Release_URL"], ["update_code", "Update"], ["osreset_code", "OS_Reset"], ["scannerreset_code", "Scanner_Reset"]] },
  { kind: "downloadpath", label: "DownloadPath", cols: [["id", "ID"], ["os_type", "OS_Type"], ["azure", "Azure"], ["os_version", "OS_Version"], ["download_url", "Download_URL"], ["md5", "MD5"], ["download_type", "Type"], ["filename", "File"], ["size", "Size"]] },
  { kind: "whitelist", label: "WhiteList", cols: [["id", "ID"], ["sn", "SN"], ["androidApi", "Android API"], ["osType", "OS_Type"], ["supportEndDate", "Support End Date"]] },
];
const PAGE = 100;

type Rows = Record<string, unknown>[];

export default function ExcelPage() {
  const [kind, setKind] = useState<Kind>("osinfo");
  const [data, setData] = useState<Partial<Record<Kind, { rows: Rows; at: string }>>>({});
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<{ k: string; desc: boolean }>({ k: "id", desc: true });
  const [page, setPage] = useState(0);
  const tab = TABS.find((t) => t.kind === kind)!;
  const cur = data[kind];

  async function load() {
    setBusy(true);
    try {
      const r = await api<{ rows: Rows; fetched_at: string }>(`/api/admin/fota/${kind}`);
      setData((d) => ({ ...d, [kind]: { rows: r.rows, at: r.fetched_at } }));
      setPage(0);
    } catch (e) {
      toast(errText(e), "err");
    } finally {
      setBusy(false);
    }
  }

  const rows = useMemo(() => {
    let rs = cur?.rows ?? [];
    if (q) {
      const ql = q.toLowerCase();
      rs = rs.filter((r) => tab.cols.some(([k]) => String(r[k] ?? "").toLowerCase().includes(ql)));
    }
    const k = sort.k;
    return [...rs].sort((a, b) => {
      const x = a[k], y = b[k];
      const c = typeof x === "number" && typeof y === "number" ? x - y : String(x ?? "").localeCompare(String(y ?? ""), undefined, { numeric: true });
      return sort.desc ? -c : c;
    });
  }, [cur, q, sort, tab]);
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));

  return (
    <>
      <PageHead title="FOTA-DB 열람" lead="FOTA 시스템에서 바로 읽어 옵니다(읽기 전용). 표마다 [불러오기] 를 누르면 지금 상태를 가져옵니다." />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="flex rounded-lg border border-line-strong bg-surface p-0.5">
          {TABS.map((t) => (
            <button key={t.kind} onClick={() => { setKind(t.kind); setPage(0); setSort({ k: "id", desc: true }); }}
                    className={cx("rounded-md px-3 py-1.5 text-[13px] font-semibold", kind === t.kind ? "bg-brand text-white" : "text-ink-2")}>
              {t.label}{data[t.kind] && <span className="ml-1 opacity-75">{data[t.kind]!.rows.length.toLocaleString()}</span>}
            </button>
          ))}
        </div>
        <Button busy={busy} onClick={load}>{cur ? "새로 불러오기" : "불러오기"}</Button>
        {cur && <span className="text-[12px] text-ink-3">{cur.at} 기준</span>}
        <SearchBox value={q} onChange={(v) => { setQ(v); setPage(0); }} placeholder="모든 열에서 찾기" className="ml-auto" />
      </div>
      <Card pad={false}>
        {busy && !cur ? <Skeleton rows={8} /> : !cur ? <Empty>[불러오기] 를 누르면 {tab.label} 표를 가져옵니다</Empty> : rows.length ? (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-[12.5px]">
                <thead>
                  <tr className="border-b border-line bg-surface-2 text-left text-[11.5px] font-semibold text-ink-3">
                    {tab.cols.map(([k, l]) => (
                      <th key={k} className="cursor-pointer whitespace-nowrap px-3 py-2 hover:text-ink" onClick={() => setSort((s) => ({ k, desc: s.k === k ? !s.desc : false }))}>
                        {l}{sort.k === k && (sort.desc ? " ↓" : " ↑")}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.slice(page * PAGE, page * PAGE + PAGE).map((r, i) => (
                    <tr key={i} className="border-b border-line align-top last:border-b-0 hover:bg-surface-2">
                      {tab.cols.map(([k]) => {
                        const v = String(r[k] ?? "");
                        const link = /^https?:\/\//.test(v);
                        return (
                          <td key={k} className="max-w-[360px] break-all px-3 py-1.5">
                            {link ? <a href={v} target="_blank" rel="noreferrer" className="text-brand-ink hover:underline">{v}</a> : v}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between border-t border-line px-4 py-2 text-[12.5px] text-ink-3">
              <span>{rows.length.toLocaleString()}건{q && ` (전체 ${cur.rows.length.toLocaleString()})`}</span>
              <span className="flex items-center gap-2">
                <Button variant="ghost" size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>이전</Button>
                {page + 1} / {pages}
                <Button variant="ghost" size="sm" disabled={page + 1 >= pages} onClick={() => setPage((p) => p + 1)}>다음</Button>
              </span>
            </div>
          </>
        ) : <Empty>맞는 행이 없습니다</Empty>}
      </Card>
      <Toaster />
    </>
  );
}
