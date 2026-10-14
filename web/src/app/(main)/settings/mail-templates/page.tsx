"use client";

import { useEffect, useRef, useState } from "react";
import { Button, Confirm, Field, Input, SearchBox, Section, Textarea, toast, useRunner } from "@/components/admin/kit";
import { Skeleton, Tag, cx } from "@/components/ui";
import { useApi } from "@/lib/api";
import { errText, send, type MailTpl } from "@/lib/admin";

type Resp = { groups: { group: string; items: MailTpl[] }[] };
type Preview = { subject: string; body: string; unknown: string[]; missing: string[] };

function Editor({ t, onSaved }: { t: MailTpl; onSaved: (t: MailTpl) => void }) {
  const [subject, setSubject] = useState(t.subject);
  const [body, setBody] = useState(t.body);
  const [pv, setPv] = useState<Preview | null>(null);
  const [resetOpen, setResetOpen] = useState(false);
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const lastFocus = useRef<"subject" | "body">("body");
  const subjRef = useRef<HTMLInputElement>(null);
  const { busy, run } = useRunner();
  useEffect(() => { setSubject(t.subject); setBody(t.body); }, [t]);

  useEffect(() => {
    const h = setTimeout(async () => {
      try { setPv(await send<Preview>(`/api/admin/mail-templates/${t.key}/preview`, { subject, body })); } catch (e) { setPv(null); toast(errText(e), "err"); }
    }, 350);
    return () => clearTimeout(h);
  }, [subject, body, t.key]);

  function insert(name: string) {
    const tag = `{{${name}}}`;
    const el = lastFocus.current === "subject" ? subjRef.current : bodyRef.current;
    const val = lastFocus.current === "subject" ? subject : body;
    const s = el?.selectionStart ?? val.length;
    const e = el?.selectionEnd ?? val.length;
    const next = val.slice(0, s) + tag + val.slice(e);
    (lastFocus.current === "subject" ? setSubject : setBody)(next);
    requestAnimationFrame(() => { el?.focus(); el?.setSelectionRange(s + tag.length, s + tag.length); });
  }

  const dirty = subject !== t.subject || body !== t.body;
  const vars = Object.entries(t.vars);
  const autos = Object.entries(t.auto);
  return (
    <div className="space-y-4">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-[16px] font-bold">{t.title}</h2>
          {t.edited && <Tag tone="warn">고침 · {t.updated_by} {t.updated_at}</Tag>}
        </div>
        <div className="mt-1 text-[12.5px] text-ink-3">언제 — {t.when} · 받는 사람 — {t.to}</div>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <div className="space-y-3">
          <Field label="제목"><Input ref={subjRef} value={subject} onFocus={() => (lastFocus.current = "subject")} onChange={(e) => setSubject(e.target.value)} /></Field>
          <Field label="본문" hint="보내는 사람 서명은 보낼 때 저절로 붙습니다">
            <Textarea ref={bodyRef} mono rows={16} value={body} onFocus={() => (lastFocus.current = "body")} onChange={(e) => setBody(e.target.value)} />
          </Field>
          <div>
            <div className="mb-1.5 text-[12.5px] font-semibold text-ink-2">바뀌는 자리 — 누르면 커서 자리에 들어갑니다</div>
            <div className="flex flex-wrap gap-1.5">
              {[...vars, ...autos].map(([k, d]) => (
                <button key={k} title={d} onClick={() => insert(k)}
                        className={cx("rounded-md border px-2 py-0.5 font-mono text-[12px]", vars.some(([v]) => v === k) ? "border-brand/40 bg-brand-soft text-brand-ink" : "border-line-strong text-ink-2")}>
                  {`{{${k}}}`}
                </button>
              ))}
            </div>
          </div>
        </div>
        <div>
          <div className="mb-1 text-[12.5px] font-semibold text-ink-2">미리보기 — 예시 값을 넣은 모습</div>
          <div className="rounded-xl border border-line bg-surface-2">
            <div className="border-b border-line px-4 py-2.5 text-[13.5px] font-semibold">{pv?.subject ?? "…"}</div>
            <pre className="max-h-[440px] overflow-auto whitespace-pre-wrap px-4 py-3 font-[family-name:var(--font-sans)] text-[13px] leading-relaxed">{pv?.body ?? ""}</pre>
          </div>
          {!!pv?.unknown.length && (
            <div className="mt-2 rounded-lg bg-err-soft px-3 py-2 text-[12.5px] text-err">
              채워지지 않는 자리: {pv.unknown.map((u) => `{{${u}}}`).join(", ")} — 이대로는 저장되지 않습니다
            </div>
          )}
          {!!pv?.missing.length && (
            <div className="mt-2 rounded-lg bg-warn-soft px-3 py-2 text-[12.5px] text-warn">
              빠진 자리: {pv.missing.map((u) => `{{${u}}}`).join(", ")}
            </div>
          )}
        </div>
      </div>
      <div className="flex flex-wrap justify-end gap-2 border-t border-line pt-4">
        {dirty && <Button variant="ghost" onClick={() => { setSubject(t.subject); setBody(t.body); }}>고친 것 버리기</Button>}
        <Button variant="outline" disabled={!t.edited} onClick={() => setResetOpen(true)}>기본 문구로 되돌리기</Button>
        <Button busy={busy === "save"} disabled={!dirty || !!pv?.unknown.length}
                onClick={() => run("save", async () => {
                  const r = await send<{ ok: boolean; message: string; item: MailTpl }>(`/api/admin/mail-templates/${t.key}`, { subject, body });
                  onSaved(r.item);
                  return r;
                })}>저장</Button>
      </div>
      <Confirm open={resetOpen} onClose={() => setResetOpen(false)} title="기본 문구로 되돌리기" confirmText="되돌리기"
               onConfirm={async () => {
                 const r = await run("reset", () => send<{ ok: boolean; message: string; item: MailTpl }>(`/api/admin/mail-templates/${t.key}/reset`));
                 if (r) onSaved(r.item);
               }}>
        고친 제목·본문을 지우고 처음 문구로 돌아갑니다.
      </Confirm>
    </div>
  );
}

export default function MailTemplatesPage() {
  const { data, loading, reload } = useApi<Resp>("/api/admin/mail-templates");
  const [sel, setSel] = useState("");
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Record<string, MailTpl>>({});
  useEffect(() => {
    if (!data) return;
    const m: Record<string, MailTpl> = {};
    for (const g of data.groups) for (const t of g.items) m[t.key] = t;
    setItems(m);
    setSel((s) => s || data.groups[0]?.items[0]?.key || "");
  }, [data]);
  if (loading || !data) return <Skeleton rows={6} />;
  const cur = items[sel];

  return (
    <Section title="메일 문구" desc="앱이 보내는 메일의 제목과 본문입니다. 고치면 다음 발송부터 쓰입니다. 누구에게 언제 나가는지는 바뀌지 않고 문구만 바뀝니다.">
      <div className="grid gap-5 lg:grid-cols-[240px_minmax(0,1fr)]">
        <div className="lg:border-r lg:border-line lg:pr-4">
          <SearchBox value={q} onChange={setQ} placeholder="문구 찾기" className="mb-3 max-w-none" />
          <div className="max-h-[70vh] space-y-3 overflow-y-auto">
            {data.groups.map((g) => {
              const list = g.items.filter((t) => !q || `${t.title} ${t.when}`.toLowerCase().includes(q.toLowerCase()));
              if (!list.length) return null;
              return (
                <div key={g.group}>
                  <div className="mb-1 px-2 text-[11px] font-bold uppercase tracking-wide text-ink-3">{g.group}</div>
                  {list.map((t) => {
                    const it = items[t.key] ?? t;
                    return (
                      <button key={t.key} onClick={() => setSel(t.key)}
                              className={cx("flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px]", sel === t.key ? "bg-brand-soft font-semibold text-brand-ink" : "text-ink-2 hover:bg-surface-2")}>
                        <span className="min-w-0 flex-1 truncate">{it.title}</span>
                        {it.edited && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-warn" title="고친 문구" />}
                      </button>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </div>
        <div className="min-w-0">
          {cur ? <Editor key={cur.key} t={cur} onSaved={(t) => { setItems((m) => ({ ...m, [t.key]: t })); reload(); }} /> : null}
        </div>
      </div>
    </Section>
  );
}
