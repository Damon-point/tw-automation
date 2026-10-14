"use client";

import { use, useEffect, useMemo, useState } from "react";
import { Card, Skeleton, StatusChip, cx } from "@/components/ui";
import { AutoTextarea, Banner, Btn, Confirm, EditorHead, Field, FramePreview, Tabs, ToastHost, inputCls, useToast } from "@/components/osu/kit";
import { useApi } from "@/lib/api";
import { errText, post, type NlCard, type NlTag, type Newsletter } from "@/lib/osu";

const EMPTY: NlCard = { topic: "common", title_ko: "", body_ko: "", title_en: "", body_en: "" };
const REVIEW: Record<string, { status: string; label: string }> = {
  requested: { status: "review", label: "결재 요청됨" },
  approved: { status: "done", label: "결재 승인" },
  rejected: { status: "new", label: "반려" },
};

export default function NewsletterPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <ToastHost><Editor id={id} /></ToastHost>;
}

function Editor({ id }: { id: string }) {
  const toast = useToast();
  const nl = useApi<Newsletter>(`/api/jobs/${id}/newsletter`);
  const [subject, setSubject] = useState("");
  const [osv, setOsv] = useState("");
  const [cards, setCards] = useState<NlCard[]>([]);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [lang, setLang] = useState<"kr" | "en">("kr");
  const [stamp, setStamp] = useState(0);
  const [focus, setFocus] = useState(0);

  const d = nl.data?.draft;
  useEffect(() => {
    if (!d) return;
    setSubject(d.subject);
    setOsv(d.os_version_disp);
    setCards(d.cards.map((c) => ({ ...EMPTY, ...c })));
    setDirty(false);
    setStamp(Date.now());
  }, [d]);

  // 저장하지 않고 떠나려 하면 붙잡는다
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

  const locked = nl.data?.review === "requested" || nl.data?.review === "approved";
  const topics = nl.data?.topics ?? {};

  function edit(i: number, patch: Partial<NlCard>) {
    setCards((cs) => cs.map((c, k) => (k === i ? { ...c, ...patch } : c)));
    setDirty(true);
  }
  function move(i: number, d2: number) {
    setCards((cs) => {
      const j = i + d2;
      if (j < 0 || j >= cs.length) return cs;
      const n = [...cs];
      [n[i], n[j]] = [n[j], n[i]];
      return n;
    });
    setFocus(i + d2);
    setDirty(true);
  }

  async function save() {
    setSaving(true);
    try {
      const r = await post<{ message: string }>(`/api/jobs/${id}/newsletter`, { subject, os_version_disp: osv, cards });
      toast("ok", r.message);
      await nl.reload();
      setStamp(Date.now());
      setDirty(false);
    } catch (e) {
      toast("err", errText(e));
    } finally {
      setSaving(false);
    }
  }

  if (nl.loading) return <Skeleton rows={6} />;
  if (!d) {
    return (
      <>
        <EditorHead jobId={id} title="Newsletter" />
        <Banner tone="info" title="Newsletter 초안이 없습니다">Submission 의 Newsletter 제작 단계를 실행하면 초안이 만들어집니다.</Banner>
      </>
    );
  }

  const rv = REVIEW[nl.data?.review ?? ""];
  return (
    <>
      <EditorHead
        jobId={id}
        title="Newsletter"
        sub={<span className="inline-flex flex-wrap items-center gap-2">{d.product_disp} · {d.os_version_disp}
          {rv && <StatusChip status={rv.status} label={rv.label} />}
          {d.sent && <StatusChip status="done" label={`발송 완료 ${d.sent_at.slice(0, 10)}`} />}</span>}
        right={!locked && <Btn kind="primary" icon="check" busy={saving} disabled={!dirty} onClick={save}>{dirty ? "저장하고 미리보기 갱신" : "저장됨"}</Btn>}
      />
      {locked && <div className="mb-4"><Banner tone="info" title="결재 중이거나 승인된 초안이라 편집을 잠갔습니다">반려되면 다시 고칠 수 있습니다.</Banner></div>}
      {!d.rn_link && <div className="mb-4"><Banner tone="err" title="초안에 Release Note 링크가 없습니다">Newsletter 제작을 다시 실행하면 RN for Sales 링크가 들어갑니다.</Banner></div>}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="space-y-4">
          <Card title="머리">
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_220px]">
              <Field label="메일 제목"><input className={inputCls} value={subject} disabled={locked} onChange={(e) => { setSubject(e.target.value); setDirty(true); }} /></Field>
              <Field label="OS 버전 표기" hint="예: 68.04 (Jul 16 2026)"><input className={inputCls} value={osv} disabled={locked} onChange={(e) => { setOsv(e.target.value); setDirty(true); }} /></Field>
            </div>
            <div className="mt-3 grid gap-1 text-[12.5px] text-ink-2 sm:grid-cols-2">
              <div>SDK <span className="font-mono">{d.sdk || "—"}</span></div>
              <div className="min-w-0 truncate">Release Note {d.rn_link ? <a href={d.rn_link} target="_blank" rel="noreferrer" className="text-brand-ink hover:underline">{d.rn_link}</a> : "—"}</div>
            </div>
          </Card>

          <Card title={`카드 ${cards.length}`} right={!locked && <Btn kind="ghost" icon="sparkle" onClick={() => { setCards((c) => [...c, { ...EMPTY }]); setFocus(cards.length); setDirty(true); }}>카드 추가</Btn>} pad={false}>
            {cards.length === 0 && <div className="px-5 py-8 text-center text-[13px] text-ink-3">카드가 없습니다</div>}
            {cards.map((c, i) => (
              <div key={i} className={cx("border-b border-line px-5 py-4 last:border-b-0", focus === i && "bg-surface-2/60")} onFocus={() => setFocus(i)}>
                <div className="mb-3 flex items-center gap-2">
                  <span className="grid h-6 w-6 place-items-center rounded-full bg-brand-soft text-[12px] font-bold text-brand-ink">{i + 1}</span>
                  <select className={cx(inputCls, "w-auto py-1 text-[13px]")} value={topics[c.topic] ? c.topic : "common"} disabled={locked} onChange={(e) => edit(i, { topic: e.target.value })}>
                    {Object.entries(topics).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                  <span className="text-[11.5px] text-ink-3">이미지 토픽</span>
                  {!locked && (
                    <div className="ml-auto flex gap-0.5">
                      <Btn kind="ghost" disabled={i === 0} onClick={() => move(i, -1)} title="위로">↑</Btn>
                      <Btn kind="ghost" disabled={i === cards.length - 1} onClick={() => move(i, 1)} title="아래로">↓</Btn>
                      <Btn kind="ghost" className="text-err" onClick={() => { setCards((cs) => cs.filter((_, k) => k !== i)); setDirty(true); }}>삭제</Btn>
                    </div>
                  )}
                </div>
                <div className="grid gap-3 lg:grid-cols-2">
                  <div className="space-y-2">
                    <div className="text-[11px] font-bold uppercase tracking-wide text-ink-3">KR</div>
                    <input className={inputCls} placeholder="제목" value={c.title_ko} disabled={locked} onChange={(e) => edit(i, { title_ko: e.target.value })} />
                    <AutoTextarea placeholder="본문" value={c.body_ko} disabled={locked} onChange={(v) => edit(i, { body_ko: v })} />
                  </div>
                  <div className="space-y-2">
                    <div className="text-[11px] font-bold uppercase tracking-wide text-ink-3">EN</div>
                    <input className={inputCls} placeholder="Title" value={c.title_en} disabled={locked} onChange={(e) => edit(i, { title_en: e.target.value })} />
                    <AutoTextarea placeholder="Body" value={c.body_en} disabled={locked} onChange={(v) => edit(i, { body_en: v })} />
                  </div>
                </div>
              </div>
            ))}
          </Card>
        </div>

        <div className="space-y-4 xl:sticky xl:top-4 xl:self-start">
          <Card title="미리보기" right={<Tabs value={lang} onChange={setLang} items={[{ key: "kr", label: "한글본" }, { key: "en", label: "영문본" }]} />}>
            {dirty && <div className="mb-2 text-[12px] text-warn">고친 내용은 저장하면 미리보기에 반영됩니다</div>}
            <FramePreview src={(lang === "kr" ? nl.data?.preview?.kr : nl.data?.preview?.en) ?? ""} stamp={stamp} minH="70vh" />
          </Card>
          <SendPanel id={id} subject={subject} dirty={dirty} canSend={!!d.rn_link && nl.data?.review === "approved"} sent={d.sent} onSent={nl.reload} />
        </div>
      </div>
    </>
  );
}

function SendPanel({ id, subject, dirty, canSend, sent, onSent }: { id: string; subject: string; dirty: boolean; canSend: boolean; sent: boolean; onSent: () => void }) {
  const toast = useToast();
  const [tags, setTags] = useState<NlTag[] | null>(null);
  const [tagErr, setTagErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [rows, setRows] = useState<{ lang: "kr" | "en"; tag: string }[]>([{ lang: "kr", tag: "" }]);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string[] | null>(null);

  async function loadTags() {
    setLoading(true);
    try {
      const r = await fetch(`/api/jobs/${id}/newsletter/tags`, { cache: "no-store" }).then((x) => x.json());
      setTags(r.tags ?? []);
      setTagErr(r.error ?? "");
      setRows((rs) => rs.map((x) => ((r.tags ?? []).some((t: NlTag) => t.key === x.tag) ? x : { ...x, tag: "" })));
    } catch (e) {
      setTagErr(errText(e));
    } finally {
      setLoading(false);
    }
  }

  const byKey = useMemo(() => Object.fromEntries((tags ?? []).map((t) => [t.key, t])), [tags]);
  const plan = rows.filter((r) => byKey[r.tag]);
  const total = plan.reduce((s, r) => s + byKey[r.tag].members, 0);

  async function send() {
    setBusy(true);
    try {
      const r = await post<{ ok: boolean; done: number; fail: number; lines: string[] }>(`/api/jobs/${id}/newsletter/send`, { subject, rows: plan });
      setResult(r.lines);
      toast(r.fail ? "err" : "ok", r.fail ? `발송 ${r.done}건 성공 · ${r.fail}건 실패` : `발송 ${r.done}건 완료`);
      setConfirm(false);
      onSent();
    } catch (e) {
      toast("err", errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Mailchimp 발송" right={sent && <StatusChip status="done" label="발송한 적 있음" />}>
      {tags === null ? (
        <div className="flex items-center gap-3">
          <span className="text-[13px] text-ink-2">줄마다 본문(한글·영문)과 받을 그룹을 골라 캠페인을 하나씩 만듭니다.</span>
          <Btn icon="mail" busy={loading} onClick={loadTags} className="ml-auto">발송 그룹 불러오기</Btn>
        </div>
      ) : (
        <>
          {tagErr && <div className="mb-3 text-[13px] text-err">{tagErr}</div>}
          <div className="space-y-2">
            {rows.map((r, i) => (
              <div key={i} className="flex items-center gap-2">
                <select className={cx(inputCls, "w-[130px]")} value={r.lang} onChange={(e) => setRows((rs) => rs.map((x, k) => (k === i ? { ...x, lang: e.target.value as "kr" | "en" } : x)))}>
                  <option value="kr">한글본</option>
                  <option value="en">영문본</option>
                </select>
                <select className={inputCls} value={r.tag} onChange={(e) => setRows((rs) => rs.map((x, k) => (k === i ? { ...x, tag: e.target.value } : x)))}>
                  <option value="">받을 그룹 고르기</option>
                  {(tags ?? []).map((t) => <option key={t.key} value={t.key}>{t.name} · {t.members.toLocaleString()}명</option>)}
                </select>
                <Btn kind="ghost" disabled={rows.length < 2} onClick={() => setRows((rs) => rs.filter((_, k) => k !== i))}>✕</Btn>
              </div>
            ))}
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Btn kind="ghost" onClick={() => setRows((rs) => [...rs, { lang: "kr", tag: "" }])}>줄 추가</Btn>
            <Btn kind="ghost" busy={loading} onClick={loadTags}>그룹 다시 읽기</Btn>
            <span className="ml-auto text-[12.5px] text-ink-2">{plan.length}개 줄 · {total.toLocaleString()}명</span>
            <Btn kind="danger" icon="mail" disabled={!plan.length || dirty || !canSend} onClick={() => setConfirm(true)}>발송</Btn>
          </div>
          {dirty && <div className="mt-2 text-[12px] text-warn">저장하지 않은 편집이 있어 발송을 잠갔습니다</div>}
          {!canSend && <div className="mt-2 text-[12px] text-ink-3">Newsletter 결재가 승인되면 발송할 수 있습니다</div>}
          {result && (
            <pre className="mt-3 whitespace-pre-wrap rounded-lg border border-line bg-surface-2 p-3 text-[12.5px]">{result.join("\n")}</pre>
          )}
        </>
      )}
      <Confirm open={confirm} title="실제 발송" danger busy={busy} confirmLabel={`${plan.length}개 줄 발송`} onConfirm={send} onClose={() => setConfirm(false)}>
        <p className="mb-3 font-semibold text-err">{total.toLocaleString()}명에게 보냅니다. 보낸 메일은 거둘 수 없습니다.</p>
        <ul className="space-y-1 text-[13px]">
          {plan.map((r, i) => (
            <li key={i} className="flex justify-between gap-3 rounded-lg bg-surface-2 px-3 py-1.5">
              <span>{r.lang === "kr" ? "한글본" : "영문본"} → <b>{byKey[r.tag].name}</b></span>
              <span className="text-ink-3">{byKey[r.tag].members.toLocaleString()}명 · {byKey[r.tag].list_name}</span>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-[13px] text-ink-2">제목 · {subject}</p>
      </Confirm>
    </Card>
  );
}
