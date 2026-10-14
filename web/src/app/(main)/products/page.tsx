"use client";

import { Fragment, useEffect, useState } from "react";
import { Button, Confirm, Field, Input, Modal, SearchBox, Select, Toaster, toast, useRunner } from "@/components/admin/kit";
import { Card, Empty, PageHead, Progress, Skeleton, Tag, cx } from "@/components/ui";
import { useApi } from "@/lib/api";
import { errText, pval, send, type ProductFull, type ProductGroups, type ProductMeta } from "@/lib/admin";

const TYPE_TONE: Record<string, string> = {
  GMS: "bg-ok-soft text-ok", AOSP: "bg-info-soft text-info", Russia: "bg-[#fde7da] text-[#b4441b] dark:bg-[#3a2014] dark:text-[#f3a37c]",
};

function Copy({ value }: { value: string }) {
  return (
    <button onClick={() => { navigator.clipboard?.writeText(value); toast("복사했습니다", "info"); }}
            className="shrink-0 rounded px-1.5 py-0.5 text-[11.5px] font-semibold text-ink-3 hover:bg-surface-2 hover:text-ink">복사</button>
  );
}

function Detail({ p, meta, onChanged }: { p: ProductFull; meta: ProductMeta; onChanged: () => void }) {
  const { busy, run } = useRunner();
  const [rels, setRels] = useState(p.releases);
  const [count, setCount] = useState(p.release_count);
  useEffect(() => { setRels(p.releases); setCount(p.release_count); }, [p]);
  return (
    <div className="grid gap-5 bg-surface-2/60 px-5 py-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
      <div className="space-y-4">
        {meta.path_groups.map((g) => (
          <div key={g.group}>
            <div className="mb-1.5 text-[12px] font-bold text-ink-3">{g.group}</div>
            <div className="space-y-1">
              {g.fields.map((f) => {
                const v = pval(p, f.key);
                return (
                  <div key={f.key} className="grid grid-cols-[180px_minmax(0,1fr)] items-start gap-2 text-[13px]">
                    <span className="text-ink-2">{f.label}</span>
                    {v ? (
                      <span className="flex min-w-0 items-start gap-1">
                        <span className="min-w-0 break-all font-mono text-[12.5px]">{v}</span><Copy value={v} />
                      </span>
                    ) : <span className="text-ink-3">미등록</span>}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
        <div className="text-[12px] text-ink-3">
          등록 {p.created_by || "?"} {p.created_at}{p.updated_by && ` · 최근 수정 ${p.updated_by} ${p.updated_at}`}
        </div>
      </div>
      <div>
        <div className="mb-1.5 flex items-center gap-2">
          <span className="text-[12px] font-bold text-ink-3">개발 Release Note</span>
          <span className="text-[12px] text-ink-3">{count ? `${count}판` : "수집 안 됨"}</span>
          <Button variant="outline" size="sm" className="ml-auto" busy={busy === "scan"} disabled={!pval(p, "dev_rn_path")}
                  title={pval(p, "dev_rn_path") ? "등록한 경로를 다시 훑습니다(읽기만)" : "개발 Release Note 경로가 비어 있습니다"}
                  onClick={() => run("scan", async () => {
                    const r = await send<{ total: number; downloaded: number; skipped: string[]; releases: ProductFull["releases"] }>(`/api/admin/products/${p.id}/rescan`);
                    setRels(r.releases); setCount(r.total); onChanged();
                    return r;
                  }, (r) => `${r.total}판 수집${r.downloaded ? ` · 파일 ${r.downloaded}개 저장` : ""}${r.skipped.length ? ` · 제외 ${r.skipped.length}건` : ""}`)}>
            다시 가져오기
          </Button>
        </div>
        {rels.length ? (
          <div className="divide-y divide-line rounded-xl border border-line bg-surface">
            {rels.slice(0, 15).map((r) => (
              <div key={r.version} className="flex items-center gap-2 px-3 py-1.5 text-[13px]">
                <span className="w-[120px] shrink-0 font-mono">{r.version}</span>
                <span className="min-w-0 flex-1 truncate text-[12px] text-ink-3">{r.pdf_name || r.docx_name}</span>
                {r.has_docx && <a className="rounded px-1.5 text-[12px] font-semibold text-brand-ink hover:bg-brand-soft" href={`/api/admin/products/${p.id}/releases/${encodeURIComponent(r.version)}/docx`}>Word</a>}
                {r.has_pdf && <a className="rounded px-1.5 text-[12px] font-semibold text-brand-ink hover:bg-brand-soft" href={`/api/admin/products/${p.id}/releases/${encodeURIComponent(r.version)}/pdf`}>PDF</a>}
              </div>
            ))}
            {count > 15 && <div className="px-3 py-1.5 text-[12px] text-ink-3">외 {count - 15}판</div>}
          </div>
        ) : <div className="rounded-xl border border-dashed border-line-strong px-3 py-4 text-[13px] text-ink-3">아직 수집하지 않았습니다</div>}
      </div>
    </div>
  );
}

function Form({ meta, row, onClose, onSaved }: { meta: ProductMeta; row: ProductFull | null; onClose: () => void; onSaved: () => void }) {
  const init = () => {
    const f: Record<string, string> = {};
    for (const x of [...meta.key_fields, ...meta.info_fields, ...meta.path_groups.flatMap((g) => g.fields)]) f[x.key] = row ? pval(row, x.key) : "";
    return f;
  };
  const [f, setF] = useState<Record<string, string>>(init);
  const { busy, run } = useRunner();
  const others = f.category === "Others";
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setF((v) => ({ ...v, [k]: e.target.value }));
  const types = Array.from(new Set([...meta.types, f.os_type].filter(Boolean)));
  return (
    <Modal open onClose={onClose} wide title={row ? `제품 수정 — ${row.product}` : "제품 등록"}
           footer={<>
             <Button variant="ghost" onClick={onClose}>취소</Button>
             <Button busy={busy === "save"} onClick={() => run("save", async () => {
               if (row) {
                 const r = await send(`/api/admin/products/${row.id}`, { fields: f }, "PUT");
                 onSaved(); onClose(); return r;
               }
               const r = await send<{ ok: boolean; message: string; collected: string[] }>("/api/admin/products", { fields: f });
               onSaved(); onClose();
               return r;
             }, (r) => {
               const c = (r as { collected?: string[] }).collected;
               const m = (r as { message?: string }).message ?? "저장했습니다";
               return c ? `${m}${c.length ? ` · 가져옴: ${c.join(" · ")}` : ""}` : m;
             })}>{row ? "저장" : "등록"}</Button>
           </>}>
      <div className="space-y-5">
        <div className="grid gap-3 sm:grid-cols-4">
          <Field label="제품군 *" hint={meta.category_hint}>
            <Select value={f.category} onChange={(v) => setF((x) => ({ ...x, category: v }))}
                    options={[{ value: "", label: "고르기" }, ...meta.categories.map((c) => ({ value: c, label: c }))]} />
          </Field>
          <Field label="제품명 *"><Input value={f.product} onChange={set("product")} placeholder="PM95" /></Field>
          {!others && (
            <>
              <Field label="Android 버전 *"><Input value={f.android} onChange={set("android")} placeholder="15" /></Field>
              <Field label="Type *" hint="목록에 없으면 직접 입력">
                <Input list="twn-types" value={f.os_type} onChange={set("os_type")} placeholder="GMS" />
                <datalist id="twn-types">{types.map((t) => <option key={t} value={t} />)}</datalist>
              </Field>
            </>
          )}
        </div>
        <Field label="최초 OS 버전" className="max-w-[240px]"><Input value={f.initial_os_version} onChange={set("initial_os_version")} placeholder="95.01" mono /></Field>
        <div className="text-[12px] text-ink-3">구분 키: {others ? "제품명" : "제품명 · Android 버전 · Type"}</div>
        {meta.path_groups.map((g) => (
          <div key={g.group}>
            <div className="mb-2 border-b border-line pb-1 text-[13px] font-bold">{g.group}</div>
            <div className="space-y-2.5">
              {g.fields.map((x) => (
                <Field key={x.key} label={x.label}><Input value={f[x.key]} onChange={set(x.key)} placeholder={x.placeholder} mono /></Field>
              ))}
            </div>
          </div>
        ))}
        {!row && <p className="text-[12.5px] text-ink-3">등록하면 그 경로에서 개발 Release Note 와 출시 이력(Service Portal · Knowledge Base)을 한 번 읽어 둡니다.</p>}
      </div>
    </Modal>
  );
}

export default function ProductsPage() {
  const [q, setQ] = useState("");
  const [dq, setDq] = useState("");
  useEffect(() => { const t = setTimeout(() => setDq(q), 250); return () => clearTimeout(t); }, [q]);
  const meta = useApi<ProductMeta>("/api/admin/products/meta");
  const { data, loading, reload } = useApi<ProductGroups>(`/api/admin/products?q=${encodeURIComponent(dq)}`);
  const [onlyMissing, setOnlyMissing] = useState(false);
  const [open, setOpen] = useState<Record<number, boolean>>({});
  const [closedCat, setClosedCat] = useState<Record<string, boolean>>({});
  const [form, setForm] = useState<ProductFull | "new" | null>(null);
  const [del, setDel] = useState<ProductFull | null>(null);

  const groups = (data?.groups ?? []).map((g) => ({ ...g, items: g.items.filter((p) => !onlyMissing || p.missing.length) }));
  const total = (data?.groups ?? []).reduce((n, g) => n + g.items.length, 0);
  const incomplete = (data?.groups ?? []).reduce((n, g) => n + g.items.filter((p) => p.missing.length).length, 0);

  return (
    <>
      <PageHead title="제품 관리" lead="OS Update 작업의 기준 목록. 제품명 + Android + Type 하나가 한 줄이고, 각 단계가 쓰는 경로를 여기 등록해 둡니다."
                right={<Button onClick={() => setForm("new")} disabled={!meta.data}>제품 등록</Button>} />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <button onClick={() => setOnlyMissing((v) => !v)}
                className={cx("rounded-lg border px-3 py-1.5 text-[13px] font-semibold", onlyMissing ? "border-warn bg-warn-soft text-warn" : "border-line-strong bg-surface text-ink-2")}>
          빈 칸 있는 제품만 {incomplete ? `(${incomplete})` : ""}
        </button>
        <span className="text-[12.5px] text-ink-3">{total}개</span>
        <SearchBox value={q} onChange={setQ} placeholder="제품명 · Android · Type · 경로" className="ml-auto" />
      </div>
      {loading || !data || !meta.data ? <Card pad={false}><Skeleton rows={8} /></Card> : (
        <div className="space-y-4">
          {groups.filter((g) => g.items.length).map((g) => {
            const closed = closedCat[g.category] && !dq;
            return (
              <Card key={g.category} pad={false}
                    title={<button className="flex items-center gap-2" onClick={() => setClosedCat((c) => ({ ...c, [g.category]: !c[g.category] }))}>
                      <span className={cx("inline-block transition-transform", closed ? "" : "rotate-90")}>›</span>{g.category}
                      <span className="text-[12.5px] font-normal text-ink-3">{g.items.length}개</span>
                    </button>}>
                {!closed && (
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[880px] text-[13.5px]">
                      <thead>
                        <tr className="border-b border-line bg-surface-2 text-left text-[11.5px] font-semibold text-ink-3">
                          <th className="px-5 py-2">제품</th><th className="px-2 py-2">최초 OS</th><th className="px-2 py-2">최신 개발 RN</th>
                          <th className="w-[230px] px-2 py-2">등록 항목</th><th className="px-2 py-2">수정</th><th className="px-5 py-2" />
                        </tr>
                      </thead>
                      <tbody>
                        {g.items.map((p) => {
                          const isOpen = open[p.id];
                          return (
                            <Fragment key={p.id}>
                              <tr className={cx("cursor-pointer border-b border-line hover:bg-surface-2", isOpen && "bg-surface-2")}
                                  onClick={() => setOpen((o) => ({ ...o, [p.id]: !o[p.id] }))}>
                                <td className="px-5 py-2.5">
                                  <div className="flex items-center gap-2">
                                    <span className={cx("inline-block text-ink-3 transition-transform", isOpen && "rotate-90")}>›</span>
                                    <span className="font-bold">{p.product}</span>
                                    {p.android && <Tag>A{p.android}</Tag>}
                                    {p.os_type && <span className={cx("rounded-md px-1.5 py-0.5 text-[11px] font-semibold", TYPE_TONE[p.os_type] ?? "bg-muted-soft text-ink-2")}>{p.os_type}</span>}
                                  </div>
                                </td>
                                <td className="px-2 py-2.5 font-mono text-[12.5px]">{p.initial_os_version || <span className="text-ink-3">—</span>}</td>
                                <td className="px-2 py-2.5">
                                  {p.releases[0] ? (
                                    <span className="font-mono text-[12.5px]">{p.releases[0].version}{p.release_count > 1 && <span className="ml-1.5 text-[11.5px] text-ink-3">+{p.release_count - 1}</span>}</span>
                                  ) : <span className="text-[12.5px] text-ink-3">수집 안 됨</span>}
                                </td>
                                <td className="px-2 py-2.5">
                                  <div className="flex items-center gap-2">
                                    <div className="w-16"><Progress done={p.filled} total={p.total} /></div>
                                    <span className={cx("text-[12px] tabular-nums", p.missing.length ? "text-ink-3" : "font-semibold text-ok")}>{p.filled}/{p.total}</span>
                                    {p.missing.length > 0 && <span className="min-w-0 truncate text-[11.5px] text-ink-3" title={p.missing.join("\n")}>빈 칸 {p.missing.length}</span>}
                                  </div>
                                </td>
                                <td className="px-2 py-2.5 text-[12px] text-ink-3">{p.updated_at.slice(0, 10)}</td>
                                <td className="px-5 py-2.5" onClick={(e) => e.stopPropagation()}>
                                  <div className="flex justify-end gap-1">
                                    <Button variant="ghost" size="sm" onClick={() => setForm(p)}>수정</Button>
                                    <Button variant="ghost" size="sm" className="text-err hover:bg-err-soft hover:text-err" onClick={() => setDel(p)}>삭제</Button>
                                  </div>
                                </td>
                              </tr>
                              {isOpen && (
                                <tr className="border-b border-line"><td colSpan={6} className="p-0"><Detail p={p} meta={meta.data!} onChanged={reload} /></td></tr>
                              )}
                            </Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            );
          })}
          {!groups.some((g) => g.items.length) && <Card><Empty>{dq ? `'${dq}' 에 맞는 제품이 없습니다` : "등록된 제품이 없습니다"}</Empty></Card>}
        </div>
      )}
      {form && meta.data && <Form meta={meta.data} row={form === "new" ? null : form} onClose={() => setForm(null)} onSaved={reload} />}
      <Confirm open={!!del} onClose={() => setDel(null)} danger confirmText="삭제" title={`'${del?.product}${del?.android ? ` A${del.android}` : ""} ${del?.os_type ?? ""}' 삭제`}
               onConfirm={async () => { try { const r = await send(`/api/admin/products/${del!.id}`, undefined, "DELETE"); toast(r.message ?? "삭제했습니다"); reload(); } catch (e) { toast(errText(e), "err"); } }}>
        등록한 경로도 함께 지워집니다.
      </Confirm>
      <Toaster />
    </>
  );
}
