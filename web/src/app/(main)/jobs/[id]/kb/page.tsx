"use client";

import { use, useEffect, useState } from "react";
import { Card, Skeleton, cx } from "@/components/ui";
import { Banner, Btn, EditorHead, Field, FramePreview, HistoryList, Tabs, ToastHost, inputCls, useToast } from "@/components/osu/kit";
import { useApi } from "@/lib/api";
import { KB_BUCKETS, errText, post, type HistoryEntry, type KbItem, type KbSection } from "@/lib/osu";

type Resp = { items: KbItem[]; section_names: string[]; history: HistoryEntry[] };
type Bucket = (typeof KB_BUCKETS)[number]["key"];

export default function KbPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <ToastHost><Editor id={id} /></ToastHost>;
}

function Editor({ id }: { id: string }) {
  const res = useApi<Resp>(`/api/jobs/${id}/kb`);
  const [sel, setSel] = useState("");
  const items = res.data?.items ?? [];
  useEffect(() => { if (!sel && items.length) setSel(items[0].key); }, [items, sel]);
  const it = items.find((x) => x.key === sel);

  if (res.loading) return <Skeleton rows={6} />;
  return (
    <>
      <EditorHead jobId={id} title="Knowledge Base 편집" sub="이번에 페이지 맨 위에 얹히는 엔트리를 고칩니다. 실제 페이지는 Approval 의 KB 단계 [검수 완료] 때 바뀝니다." />
      {!items.length ? (
        <Banner tone="info" title="편집안이 없습니다">Knowledge Base 편집 단계를 실행하면 만들어집니다.</Banner>
      ) : (
        <>
          {items.length > 1 && (
            <div className="mb-4"><Tabs value={sel} onChange={setSel} items={items.map((x) => ({ key: x.key, label: x.title || x.doc_id }))} /></div>
          )}
          {it && <Doc key={it.key + it.updated_at} id={id} it={it} names={res.data?.section_names ?? []} history={res.data?.history ?? []} onSaved={res.reload} />}
        </>
      )}
    </>
  );
}

function Doc({ id, it, names, history, onSaved }: { id: string; it: KbItem; names: string[]; history: HistoryEntry[]; onSaved: () => void }) {
  const toast = useToast();
  const [view, setView] = useState<"entry" | "page" | "source" | "history">("entry");
  const [vfull, setVfull] = useState(it.vfull);
  const [sdk, setSdk] = useState(it.sdk);
  const [sec, setSec] = useState(it.security);
  const [secs, setSecs] = useState<KbSection[]>(it.sections);
  const [src, setSrc] = useState(it.storage);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

  const count = secs.reduce((n, s) => n + s.new.length + s.improved.length + s.fixed.length, 0);
  const mut = (f: (s: KbSection[]) => KbSection[]) => { setSecs((x) => f(x.map((s) => ({ ...s, new: [...s.new], improved: [...s.improved], fixed: [...s.fixed] })))); setDirty(true); };

  async function apply() {
    setBusy(true);
    try {
      const r = await post<{ message: string }>(`/api/jobs/${id}/kb/apply`, { key: it.key, vfull, sdk, security: sec, sections: secs });
      toast("ok", r.message);
      setDirty(false);
      onSaved();
    } catch (e) {
      toast("err", errText(e));
    } finally {
      setBusy(false);
    }
  }
  async function saveSource() {
    setBusy(true);
    try {
      const r = await post<{ message: string }>(`/api/jobs/${id}/kb/source`, { key: it.key, storage: src });
      toast("ok", r.message);
      setDirty(false);
      onSaved();
    } catch (e) {
      toast("err", errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-[16px] font-bold">{it.title}</span>
        <span className="text-[13px] text-ink-3">{it.new_page ? "새로 만드는 페이지" : `#${it.doc_id}`}</span>
      </div>
      {it.ambiguous && <Banner tone="warn" title="다른 Android 버전의 페이지일 수 있습니다">{it.ambiguous}</Banner>}
      {it.others.length > 0 && <Banner tone="warn" title="같은 페이지를 고치는 다른 작업이 있습니다">{it.others.join(" · ")} — 하나를 올린 뒤 나머지는 KB 편집을 다시 돌려 최신 내용 위에 쌓아야 합니다.</Banner>}
      {it.error && <Banner tone="err" title="편집 오류">{it.error}</Banner>}

      <div className="flex flex-wrap items-center gap-3">
        <Tabs value={view} onChange={(v) => { if (dirty && !window.confirm("저장하지 않은 편집이 있습니다. 버리고 넘어갈까요?")) return; if (dirty) { setVfull(it.vfull); setSdk(it.sdk); setSec(it.security); setSecs(it.sections); setSrc(it.storage); setDirty(false); } setView(v); }}
              items={[{ key: "entry", label: `이번 엔트리 · ${count}건` }, { key: "page", label: "페이지 전체" }, { key: "source", label: "소스" }, { key: "history", label: "이력" }]} />
        {view === "entry" && <Btn kind="primary" icon="check" busy={busy} disabled={!dirty} onClick={apply} className="ml-auto">{dirty ? "이 내용으로 반영" : "반영됨"}</Btn>}
        {view === "source" && <Btn kind="primary" icon="check" busy={busy} disabled={!dirty} onClick={saveSource} className="ml-auto">{dirty ? "소스 저장" : "저장됨"}</Btn>}
      </div>

      {view === "entry" && (
        <>
          <Card>
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_160px_200px]">
              <Field label="버전"><input className={inputCls} value={vfull} onChange={(e) => { setVfull(e.target.value); setDirty(true); }} /></Field>
              <Field label="SDK"><input className={inputCls} value={sdk} onChange={(e) => { setSdk(e.target.value); setDirty(true); }} /></Field>
              <Field label="Security Patch" hint={it.security_date && `표시: ${it.security_date}`}><input className={inputCls} value={sec} onChange={(e) => { setSec(e.target.value); setDirty(true); }} /></Field>
            </div>
          </Card>
          {count === 0 && <Banner tone="err" title="공개할 항목이 하나도 없습니다">이대로 올리면 제목과 SDK·보안패치만 보입니다.</Banner>}
          {secs.map((s, i) => (
            <Card key={i} pad={false}
                  title={
                    <span className="flex items-center gap-2">
                      <input list="kb-sections" className={cx(inputCls, "w-[260px] py-1 text-[14px] font-bold")} value={s.title}
                             onChange={(e) => mut((x) => { x[i].title = e.target.value; return x; })} />
                    </span>
                  }
                  right={
                    <div className="flex gap-0.5">
                      <Btn kind="ghost" disabled={i === 0} onClick={() => mut((x) => { [x[i - 1], x[i]] = [x[i], x[i - 1]]; return x; })}>↑</Btn>
                      <Btn kind="ghost" disabled={i === secs.length - 1} onClick={() => mut((x) => { [x[i + 1], x[i]] = [x[i], x[i + 1]]; return x; })}>↓</Btn>
                      <Btn kind="ghost" className="text-err" onClick={() => mut((x) => x.filter((_, k) => k !== i))}>섹션 삭제</Btn>
                    </div>
                  }>
              {KB_BUCKETS.map((b) => (
                <div key={b.key} className="border-b border-line px-5 py-3 last:border-b-0">
                  <div className="mb-2 flex items-center gap-2">
                    <span className="rounded px-1.5 py-0.5 text-[11px] font-bold text-white" style={{ background: b.color }}>{b.label}</span>
                    <span className="text-[12px] text-ink-3">{s[b.key as Bucket].length}건</span>
                    <Btn kind="ghost" className="ml-auto" onClick={() => mut((x) => { x[i][b.key as Bucket].push(""); return x; })}>항목 추가</Btn>
                  </div>
                  <div className="space-y-1.5">
                    {s[b.key as Bucket].map((txt, j) => (
                      <div key={j} className="flex gap-1.5">
                        <input className={inputCls} value={txt} onChange={(e) => mut((x) => { x[i][b.key as Bucket][j] = e.target.value; return x; })} />
                        <Btn kind="ghost" onClick={() => mut((x) => { x[i][b.key as Bucket].splice(j, 1); return x; })}>✕</Btn>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </Card>
          ))}
          <datalist id="kb-sections">{names.map((n) => <option key={n} value={n} />)}</datalist>
          <Btn icon="sparkle" onClick={() => mut((x) => [...x, { title: names.find((n) => !x.some((s) => s.title === n)) ?? "Others", new: [], improved: [], fixed: [] }])}>섹션 추가</Btn>
        </>
      )}
      {view === "page" && <FramePreview src={it.preview} stamp={it.updated_at} minH="72vh" />}
      {view === "source" && (
        <textarea value={src} spellCheck={false} onChange={(e) => { setSrc(e.target.value); setDirty(true); }}
                  className={cx(inputCls, "min-h-[64vh] font-mono text-[12.5px] leading-relaxed")} />
      )}
      {view === "history" && <Card><HistoryList entries={history} /></Card>}
    </div>
  );
}
