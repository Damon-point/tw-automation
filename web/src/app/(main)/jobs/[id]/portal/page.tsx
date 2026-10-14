"use client";

import { use, useEffect, useRef, useState } from "react";
import { Card, Skeleton, cx } from "@/components/ui";
import { HtmlEditor, type HtmlEditorHandle } from "@/components/osu/HtmlEditor";
import { Banner, Btn, EditorHead, HistoryList, Tabs, ToastHost, useToast } from "@/components/osu/kit";
import { useApi } from "@/lib/api";
import { errText, post, type HistoryEntry, type PortalItem } from "@/lib/osu";

type Resp = { items: PortalItem[]; history: HistoryEntry[] };

export default function PortalPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <ToastHost><Editor id={id} /></ToastHost>;
}

function Editor({ id }: { id: string }) {
  const toast = useToast();
  const res = useApi<Resp>(`/api/jobs/${id}/portal`);
  const [sel, setSel] = useState("");
  const [view, setView] = useState<"edit" | "changes" | "history">("edit");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const ed = useRef<HtmlEditorHandle>(null);

  const items = res.data?.items ?? [];
  useEffect(() => { if (!sel && items.length) setSel(items[0].key); }, [items, sel]);
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);
  const it = items.find((x) => x.key === sel);

  function pick(k: string) {
    if (k === sel) return;
    if (dirty && !window.confirm("저장하지 않은 편집이 있습니다. 버리고 다른 문서를 열까요?")) return;
    setDirty(false);
    setSel(k);
  }

  async function save() {
    if (!it || !ed.current) return;
    setSaving(true);
    try {
      const r = await post<{ message: string }>(`/api/jobs/${id}/portal`, { key: it.key, html: ed.current.getHtml() });
      toast("ok", r.message);
      setDirty(false);
      await res.reload();
    } catch (e) {
      toast("err", errText(e));
    } finally {
      setSaving(false);
    }
  }

  if (res.loading) return <Skeleton rows={6} />;
  return (
    <>
      <EditorHead jobId={id} title="Service Portal 편집" sub="실제 Portal 문서는 Approval 의 Service Portal 단계 [검수 완료] 때 이 편집본으로 바뀝니다."
                  right={it && view === "edit" && <Btn kind="primary" icon="check" busy={saving} disabled={!dirty} onClick={save}>{dirty ? "저장" : "저장됨"}</Btn>} />
      {!items.length ? (
        <Banner tone="info" title="편집안이 없습니다">Submission 의 Service Portal 단계를 실행하면 대상 문서마다 편집안이 만들어집니다.</Banner>
      ) : (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-[260px_minmax(0,1fr)]">
          <Card title={`문서 ${items.length}`} pad={false} className="self-start">
            {items.map((x) => (
              <button key={x.key} onClick={() => pick(x.key)}
                      className={cx("block w-full border-b border-line px-4 py-3 text-left last:border-b-0", x.key === sel ? "bg-brand-soft" : "hover:bg-surface-2")}>
                <div className="text-[13.5px] font-semibold leading-snug">{x.title || "(제목 없음)"}</div>
                <div className="mt-1 flex flex-wrap gap-1.5 text-[11.5px] text-ink-3">
                  <span>{x.new_doc ? "새 문서" : `#${x.doc_id}`}</span>
                  <span className="text-ok">적용 {x.applied.length}</span>
                  {x.failed.length > 0 && <span className="text-warn">미적용 {x.failed.length}</span>}
                  {x.error && <span className="text-err">오류</span>}
                </div>
              </button>
            ))}
          </Card>

          {it && (
            <div className="min-w-0 space-y-4">
              {it.error && <Banner tone="err" title="편집 오류">{it.error}</Banner>}
              {it.others.length > 0 && (
                <Banner tone="warn" title="같은 문서를 고치는 다른 작업이 있습니다">
                  {it.others.join(" · ")} — 업로드는 본문을 통째로 바꾸므로 하나를 올린 뒤 나머지는 다시 돌려 최신 내용 위에 쌓아야 합니다.
                </Banner>
              )}
              <div className="flex flex-wrap items-center gap-3">
                <Tabs value={view} onChange={(v) => { if (view === "edit" && dirty && v !== "edit" && !window.confirm("저장하지 않은 편집이 있습니다. 버리고 넘어갈까요?")) return; if (v !== "edit") setDirty(false); setView(v); }}
                      items={[{ key: "edit", label: "본문" }, { key: "changes", label: `바뀐 곳 ${it.applied.length + it.failed.length}` }, { key: "history", label: "이력" }]} />
                <span className="text-[12px] text-ink-3">{it.updated_at.replace("T", " ")}</span>
              </div>
              {view === "edit" && <HtmlEditor key={it.key + it.updated_at} ref={ed} initial={it.html} onDirty={() => setDirty(true)} />}
              {view === "changes" && <Changes it={it} />}
              {view === "history" && <Card><HistoryList entries={(res.data?.history ?? []).filter((h) => !h.title || h.title === it.title)} /></Card>}
            </div>
          )}
        </div>
      )}
    </>
  );
}

function Changes({ it }: { it: PortalItem }) {
  if (!it.applied.length && !it.failed.length) return <Card><div className="text-[13px] text-ink-3">자동 편집으로 바뀐 곳이 없습니다</div></Card>;
  return (
    <Card pad={false}>
      {it.applied.map((c, i) => (
        <div key={`a${i}`} className="border-b border-line px-5 py-3 last:border-b-0">
          <div className="text-[13px] font-semibold text-ok">✔ {c.why}</div>
          {c.old && <div className="mt-1 whitespace-pre-wrap break-all rounded-md bg-err-soft px-2 py-1 font-mono text-[12px] text-err">− {c.old}</div>}
          {c.new && <div className="mt-1 whitespace-pre-wrap break-all rounded-md bg-ok-soft px-2 py-1 font-mono text-[12px] text-ok">+ {c.new}</div>}
        </div>
      ))}
      {it.failed.map((c, i) => (
        <div key={`f${i}`} className="border-b border-line px-5 py-3 last:border-b-0">
          <div className="text-[13px] font-semibold text-warn">⚠ 적용하지 못함 · {c.reason}</div>
          {c.old && <div className="mt-1 whitespace-pre-wrap break-all font-mono text-[12px] text-ink-2">{c.old}</div>}
        </div>
      ))}
    </Card>
  );
}
