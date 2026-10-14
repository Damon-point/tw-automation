"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { DISCLOSURE_TONE, kst, SB_STATE_TONE, type SbDoc, type SbList, type SbPreview } from "@/lib/issues";
import { cx } from "@/components/ui";
import { Btn, Modal, useToast } from "./kit";
import { RichEditor } from "./RichEditor";

type Form = { title: string; disclosure: string; applies_to: string; description_html: string; partners: string[]; labels: string[] };

function formOf(d: SbDoc): Form {
  return { title: d.title, disclosure: d.disclosure, applies_to: d.applies_to, description_html: d.description_html,
           partners: [...d.partners], labels: [...d.labels] };
}

export function SbStateChip({ d }: { d: Pick<SbDoc, "state" | "state_label"> }) {
  return <span className={cx("inline-flex whitespace-nowrap rounded-full px-2 py-0.5 text-[11.5px] font-semibold", SB_STATE_TONE[d.state] ?? "bg-muted-soft text-ink-2")}>{d.state_label}</span>;
}

export function DisclosureTag({ v }: { v: string }) {
  return <span className={cx("inline-flex rounded-md border px-1.5 py-[1px] text-[11px] font-semibold", DISCLOSURE_TONE[v] ?? "border-line text-ink-2")}>{v}</span>;
}

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[12px] font-semibold text-ink-3">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11.5px] text-ink-3">{hint}</span>}
    </label>
  );
}

const input = "w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-[14px] outline-none focus:border-brand";

/** SB 문서 한 건 — 상태에 맞는 화면(생성 대기 · 실패 · 편집 · 업로드 완료 · 폐기). */
export function SbDocPanel({ doc, meta, onChanged }: { doc: SbDoc; meta: SbList; onChanged: (d?: SbDoc) => void }) {
  const toast = useToast();
  const [form, setForm] = useState<Form>(formOf(doc));
  const [busy, setBusy] = useState<string>("");
  const [preview, setPreview] = useState<SbPreview | null>(null);
  const [askUpload, setAskUpload] = useState(false);
  const [askDiscard, setAskDiscard] = useState(false);
  const [discardReason, setDiscardReason] = useState("");

  useEffect(() => {
    setForm(formOf(doc));
    setPreview(null);
  }, [doc.id, doc.state, doc.generated_at]); // eslint-disable-line react-hooks/exhaustive-deps

  const dirty = useMemo(() => JSON.stringify(form) !== JSON.stringify(formOf(doc)), [form, doc]);
  const editable = ["draft", "notified", "approved"].includes(doc.state);

  async function call<T>(name: string, path: string, body?: unknown): Promise<T | null> {
    setBusy(name);
    try {
      return await api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "err");
      return null;
    } finally {
      setBusy("");
    }
  }

  async function save() {
    const r = await call<{ message: string; doc: SbDoc }>("save", `/api/issues/sb/${doc.id}/save`, form);
    if (r) { toast(r.message); onChanged(r.doc); }
  }
  async function doPreview() {
    const r = await call<SbPreview>("preview", `/api/issues/sb/${doc.id}/preview`, form);
    if (r) setPreview(r);
  }
  async function notify() {
    const r = await call<{ message: string }>("notify", `/api/issues/sb/${doc.id}/notify`, form);
    if (r) { toast(r.message); onChanged(); }
  }
  async function upload() {
    setAskUpload(false);
    const r = await call<{ message: string }>("upload", `/api/issues/sb/${doc.id}/upload`, form);
    if (r) { toast(r.message); onChanged(); }
  }
  async function generate() {
    const r = await call<{ message: string }>("generate", `/api/issues/sb/${doc.id}/generate`);
    if (r) { toast(r.message, "info"); onChanged(); }
  }

  // 생성은 서버 스레드에서 돈다 — 끝날 때까지 3초마다 문서를 다시 읽는다.
  useEffect(() => {
    if (!doc.generating) return;
    const t = setInterval(async () => {
      try {
        const d = await api<SbDoc>(`/api/issues/sb/${doc.id}`);
        if (!d.generating) {
          clearInterval(t);
          if (d.gen_result && !d.gen_result.ok) toast(`생성 실패: ${d.gen_result.error ?? ""}`, "err");
          else toast(`초안 생성 완료 — ${(d.title || "").slice(0, 50)}`);
          onChanged(d);
        }
      } catch { /* 다음 주기에 다시 */ }
    }, 3000);
    return () => clearInterval(t);
  }, [doc.generating, doc.id]); // eslint-disable-line react-hooks/exhaustive-deps
  async function discard() {
    setAskDiscard(false);
    const r = await call<{ message: string }>("discard", `/api/issues/sb/${doc.id}/discard`, { reason: discardReason });
    if (r) { toast(r.message, "info"); setDiscardReason(""); onChanged(); }
  }
  async function reopen() {
    const r = await call<{ message: string }>("reopen", `/api/issues/sb/${doc.id}/reopen`);
    if (r) { toast(r.message, "info"); onChanged(); }
  }

  function setDisclosure(v: string) {
    // 등급을 바꾸면 라벨도 그 등급 기본값으로 바로 바꾼다 — 이전 등급 라벨이 남지 않게.
    setForm((f) => ({ ...f, disclosure: v, labels: meta.default_labels[v] ?? f.labels, partners: v === "partner" ? f.partners : f.partners }));
  }
  function toggle(list: "partners" | "labels", key: string) {
    setForm((f) => ({ ...f, [list]: f[list].includes(key) ? f[list].filter((x) => x !== key) : [...f[list], key] }));
  }

  const times = [
    ["해결", doc.resolved_at], ["생성", doc.generated_at], ["검토 요청", doc.notified_at], ["업로드", doc.uploaded_at],
  ].filter(([, v]) => v) as [string, string][];

  return (
    <div className="flex h-full flex-col">
      {/* 머리 */}
      <div className="border-b border-line px-5 py-4">
        <div className="flex flex-wrap items-center gap-1.5">
          <SbStateChip d={doc} />
          {!["backlog", "queued"].includes(doc.state) && <DisclosureTag v={doc.disclosure} />}
          {doc.revision > 1 && <span className="rounded-md border border-warn/40 px-1.5 py-[1px] text-[11px] font-semibold text-warn" title="이슈가 다시 열렸다가 해결되어 새로 작성된 문서">{doc.revision}판</span>}
          <a href={doc.issue_url} target="_blank" rel="noreferrer" className="ml-1 text-[12.5px] font-semibold text-brand-ink hover:underline">#{doc.issue_id} ↗</a>
          <span className="ml-auto text-[12px] text-ink-3">{doc.assignee || "담당자 없음"}</span>
        </div>
        <div className="mt-1.5 text-[16px] font-bold leading-snug">{doc.title || doc.subject}</div>
        <div className="mt-1 text-[12.5px] text-ink-3">
          [{doc.issue_status}] {doc.subject}
        </div>
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-0.5 text-[12px] text-ink-3">
          {times.map(([k, v]) => <span key={k}>{k} {kst(v)}</span>)}
          {doc.in_tok > 0 && <span>토큰 {doc.in_tok.toLocaleString()}/{doc.out_tok.toLocaleString()}</span>}
        </div>
      </div>

      <div className="flex-1 space-y-4 overflow-auto px-5 py-4">
        {["backlog", "queued"].includes(doc.state) && (
          <div className="rounded-xl border border-dashed border-line-strong px-4 py-6 text-center">
            <div className="text-[14px] font-semibold">초안 미생성</div>
            <div className="mt-1 text-[12.5px] text-ink-3">5분 주기 작업이 차례대로 만든다 · Claude 로 30초 안팎</div>
            <Btn kind="primary" className="mt-3" busy={busy === "generate" || doc.generating} onClick={generate}>{doc.generating ? "Claude 가 작성 중" : "지금 초안 생성"}</Btn>
          </div>
        )}

        {doc.state === "error" && (
          <div className="rounded-xl bg-err-soft px-4 py-3">
            <div className="text-[13px] font-semibold text-err">생성 실패</div>
            <div className="mt-1 whitespace-pre-wrap break-all text-[12.5px] text-err">{doc.error || "알 수 없는 오류"}</div>
            <Btn kind="primary" className="mt-3" busy={busy === "generate" || doc.generating} onClick={generate}>{doc.generating ? "Claude 가 작성 중" : "다시 생성"}</Btn>
          </div>
        )}

        {doc.state === "uploaded" && (
          <div className="rounded-xl bg-ok-soft px-4 py-3">
            <div className="flex flex-wrap items-center gap-2 text-[13px] font-semibold text-ok">
              업로드 완료
              {doc.url && <a href={doc.url} target="_blank" rel="noreferrer" className="underline">Confluence 에서 열기 ↗</a>}
              {doc.decided_by && <span className="font-normal">· {doc.decided_by}</span>}
            </div>
            <div className="mt-2 flex flex-wrap gap-1">{doc.labels.map((l) => <span key={l} className="rounded-md border border-line bg-surface px-1.5 text-[11.5px]">{l}</span>)}</div>
            {doc.applies_to && <div className="mt-2 text-[12.5px] text-ink-2">Applies to: {doc.applies_to}</div>}
          </div>
        )}

        {doc.state === "discarded" && (
          <div className="rounded-xl bg-muted-soft px-4 py-3">
            <div className="text-[13px] font-semibold">폐기됨{doc.decided_by ? ` · ${doc.decided_by}` : ""}</div>
            {doc.error && <div className="mt-1 text-[12.5px] text-ink-2">{doc.error}</div>}
            <Btn className="mt-3" busy={busy === "reopen"} onClick={reopen}>되돌리기</Btn>
          </div>
        )}

        {["not_needed", "not_target"].includes(doc.state) && (
          <div className="rounded-xl bg-muted-soft px-4 py-3 text-[13px] text-ink-2">{doc.state_label} — 문서를 만들지 않는 건</div>
        )}

        {editable && (
          <>
            {doc.reason && (
              <div className="rounded-xl bg-surface-2 px-3.5 py-2.5 text-[12.5px] text-ink-2">
                <span className="font-semibold text-ink">Claude 판단 근거</span> · {doc.reason}
              </div>
            )}
            <Field label="제목" hint="Troubleshooting 문서는 '[TR] ' 로 시작">
              <input className={input} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </Field>
            <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
              <Field label="공개 등급">
                <div className="grid grid-cols-2 gap-1.5">
                  {Object.entries(meta.templates).map(([k, t]) => (
                    <button key={k} type="button" onClick={() => setDisclosure(k)}
                            className={cx("rounded-lg border px-2.5 py-1.5 text-left text-[12.5px] transition-colors",
                              form.disclosure === k ? "border-brand bg-brand-soft text-brand-ink" : "border-line-strong hover:bg-surface-2")}>
                      <span className="font-semibold">{k}</span> <span className="text-ink-3">{t.ko}</span>
                    </button>
                  ))}
                </div>
              </Field>
              <Field label="라벨" hint="등급을 바꾸면 그 등급의 기본 라벨로 바뀐다 · 'posted' 는 외부에 실제 게시한 문서용">
                <div className="flex flex-wrap gap-1.5">
                  {meta.known_labels.map((l) => (
                    <button key={l} type="button" onClick={() => toggle("labels", l)}
                            className={cx("rounded-full border px-2.5 py-1 text-[12.5px] font-semibold",
                              form.labels.includes(l) ? "border-brand bg-brand text-white" : "border-line-strong text-ink-2 hover:bg-surface-2")}>
                      {l}
                    </button>
                  ))}
                </div>
              </Field>
            </div>
            {form.disclosure === "partner" && (
              <Field label="공개 파트너">
                <div className="flex flex-wrap gap-3">
                  {Object.entries(meta.partners).map(([k, label]) => (
                    <label key={k} className="inline-flex items-center gap-2 text-[13px]">
                      <input type="checkbox" className="h-4 w-4 accent-[var(--brand)]" checked={form.partners.includes(k)} onChange={() => toggle("partners", k)} />
                      {label}
                    </label>
                  ))}
                </div>
              </Field>
            )}
            <Field label="Applies to">
              <input className={input} value={form.applies_to} onChange={(e) => setForm({ ...form, applies_to: e.target.value })} />
            </Field>
            <div>
              <span className="mb-1 block text-[12px] font-semibold text-ink-3">Description</span>
              <RichEditor value={form.description_html} onChange={(h) => setForm((f) => ({ ...f, description_html: h }))}
                          placeholder="배경 → 증상 → 원인 → 해결 순서로 작성" />
            </div>

            {preview && (
              <div className="rounded-xl border border-line">
                <div className="flex flex-wrap items-center gap-2 border-b border-line bg-surface-2 px-4 py-2.5 text-[12.5px]">
                  <span className="font-semibold text-ok">✔ 문법 정상</span>
                  <span className="text-ink-2">제목 &lsquo;{preview.title}&rsquo;</span>
                  {preview.labels.map((l) => <span key={l} className="rounded-md border border-line bg-surface px-1.5 text-[11px]">{l}</span>)}
                  <button onClick={() => setPreview(null)} className="ml-auto text-ink-3 hover:text-ink">닫기</button>
                </div>
                {preview.exists && (
                  <div className="border-b border-line bg-warn-soft px-4 py-2 text-[12.5px] text-warn">
                    같은 제목의 페이지가 이미 있음 (id {preview.exists.id}) — 이 제목으로는 업로드가 거부된다
                    {preview.exists.url && <> · <a className="underline" href={preview.exists.url} target="_blank" rel="noreferrer">열기</a></>}
                  </div>
                )}
                <div className="rich px-4 py-3" dangerouslySetInnerHTML={{ __html: preview.html }} />
              </div>
            )}
          </>
        )}
      </div>

      {editable && (
        <div className="flex flex-wrap items-center gap-2 border-t border-line bg-surface px-5 py-3">
          <Btn onClick={save} busy={busy === "save"} disabled={!!busy && busy !== "save"}>{dirty ? "저장 •" : "저장"}</Btn>
          <Btn onClick={doPreview} busy={busy === "preview"} disabled={!!busy && busy !== "preview"} title="저장하고 Confluence 변환 API 로 문법을 검증한 뒤 렌더 결과를 본다">미리보기·검증</Btn>
          {doc.state === "draft" && (
            <Btn kind="soft" onClick={notify} busy={busy === "notify"} disabled={!!busy && busy !== "notify"}
                 title={`수신: ${doc.email || "담당자 주소를 못 만들어 관리자에게"}`}>검토 요청 메일</Btn>
          )}
          <Btn kind="primary" onClick={() => setAskUpload(true)} busy={busy === "upload"} disabled={!!busy && busy !== "upload"}>Confluence 업로드</Btn>
          <span className="flex-1" />
          <Btn kind="danger" onClick={() => setAskDiscard(true)} disabled={!!busy}>폐기</Btn>
        </div>
      )}

      <Modal open={askUpload} onClose={() => setAskUpload(false)} title="Confluence 에 업로드"
             actions={<><Btn onClick={() => setAskUpload(false)}>취소</Btn><Btn kind="primary" onClick={upload}>업로드</Btn></>}>
        <dl className="grid grid-cols-[80px_1fr] gap-y-1.5 text-[13.5px]">
          <dt className="text-ink-3">스페이스</dt><dd>SB</dd>
          <dt className="text-ink-3">제목</dt><dd className="break-all font-semibold">{form.title}</dd>
          <dt className="text-ink-3">공개 등급</dt><dd><DisclosureTag v={form.disclosure} /> {meta.templates[form.disclosure]?.ko}</dd>
          <dt className="text-ink-3">라벨</dt><dd>{form.labels.join(", ") || "등급 기본값"}</dd>
          <dt className="text-ink-3">방식</dt><dd>{doc.page_id ? `기존 페이지 갱신 (id ${doc.page_id})` : "새 페이지"}</dd>
        </dl>
        <p className="mt-3 text-[12.5px] text-ink-3">올린 페이지는 Confluence 에서 직접 지워야 없어진다.</p>
      </Modal>

      <Modal open={askDiscard} onClose={() => setAskDiscard(false)} title="이 초안을 폐기"
             actions={<><Btn onClick={() => setAskDiscard(false)}>취소</Btn><Btn kind="danger" onClick={discard}>폐기</Btn></>}>
        <p className="mb-2 text-[13px] text-ink-2">초안은 이력으로 남고, 언제든 되돌릴 수 있다.</p>
        <input className={input} placeholder="사유 (선택)" value={discardReason} onChange={(e) => setDiscardReason(e.target.value)} />
      </Modal>
    </div>
  );
}
