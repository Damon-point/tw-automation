"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Tag, cx } from "@/components/ui";
import { useApi } from "@/lib/api";
import {
  dateRows, errText, post,
  type Options, type OrgPreview, type OrgRun, type OrgView, type ThreadDetail, type VerifyItem, type VerifyResult,
} from "@/lib/whitelist";
import { Btn, Field, Input, Note, Select, Steps, SubHead, Table, Textarea, useConfirm, useToast } from "./kit";
import { DateTable, DryBadge, ReplyComposer, UploadTable, VerifyPanel } from "./parts";

type Step = "proc" | "check" | "reply";
type Pick = { from: string; to: string };

export default function OrganicProcess({ t, onChanged }: { t: ThreadDetail; onChanged: () => void }) {
  const toast = useToast();
  const { ask, node: confirmNode } = useConfirm();
  const opts = useApi<Options>("/api/wl/options").data;
  const [view, setView] = useState<OrgView | null>(null);
  const [loadErr, setLoadErr] = useState("");
  const [busy, setBusy] = useState("");
  const [step, setStep] = useState<Step>("proc");
  const [instruction, setInstruction] = useState("");
  const [verifyKind, setVerifyKind] = useState("");
  const [verifyItems, setVerifyItems] = useState<VerifyItem[]>([]);
  const [verifyRes, setVerifyRes] = useState<VerifyResult | null>(null);
  const [newDate, setNewDate] = useState("");
  const [result, setResult] = useState<React.ReactNode>(null);

  useEffect(() => {
    let off = false;
    setView(null); setLoadErr("");
    fetch(`/api/wl/organic/${t.tid}`, { credentials: "same-origin" }).then(async (r) => {
      const j = await r.json();
      if (off) return;
      if (!r.ok) setLoadErr(j.error ?? r.statusText);
      else { setView(j); setNewDate(j.date || ""); }
    }).catch((e) => !off && setLoadErr(errText(e)));
    return () => { off = true; };
  }, [t.tid]);

  async function reinterpret(force_type = "") {
    if (!force_type && !instruction.trim()) { toast("warn", "지시를 입력하세요"); return; }
    setBusy(force_type ? "force" : "ask");
    try {
      const v = await post<OrgView>(`/api/wl/organic/${t.tid}/interpret`, { instruction: instruction.trim(), force_type });
      setView(v);
      if (v.date) setNewDate(v.date);
    } catch (e) { toast("error", errText(e)); } finally { setBusy(""); }
  }

  function afterRun(kind: string, items: VerifyItem[]) {
    setVerifyKind(kind); setVerifyItems(items); setVerifyRes(null);
    setStep("check");
    onChanged();
  }

  if (loadErr) return <Note tone="error">{loadErr}</Note>;
  if (!view) return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-[13px] text-ink-3"><span className="h-4 w-4 animate-spin rounded-full border-2 border-brand border-t-transparent" />원문 블록을 나누고 AI 로 해석하는 중…</div>
      <div className="h-40 animate-pulse rounded-xl bg-muted-soft" />
    </div>
  );

  const toneBar = view.type === "date" ? "border-ok" : view.type === "new" ? "border-brand" : "border-warn";

  return (
    <div className="space-y-5">
      {confirmNode}
      <Steps current={step} onPick={(k) => setStep(k as Step)} steps={[
        { key: "proc", label: "처리", done: verifyItems.length > 0 },
        { key: "check", label: "확인", done: !!verifyRes?.ok },
        { key: "reply", label: "회신" },
      ]} />

      {step === "proc" && (
        <div className="space-y-5">
          <section className={cx("rounded-xl border-l-4 bg-surface-2 p-4", toneBar)}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[15px] font-bold">AI 판정: {view.type_label}</span>
              <Tag>블록 {view.blocks.length}</Tag><Tag>고유 SN {view.all_sns.length.toLocaleString()}</Tag>
            </div>
            {view.reason && <p className="mt-1 text-[13px] text-ink-2">{view.reason}</p>}
            {view.note && <p className="mt-1 text-[12px] text-ink-3">{view.note}</p>}
          </section>

          {view.type === "date" ? (
            <DateMode t={t} sns={view.all_sns} date={newDate} setDate={setNewDate} ask={ask} busy={busy} setBusy={setBusy}
              onResult={setResult} onDone={(items) => afterRun("등록일 변경", items)} />
          ) : view.type === "new" ? (
            <NewMode t={t} view={view} opts={opts} ask={ask} busy={busy} setBusy={setBusy} onResult={setResult}
              onDone={(label, items) => afterRun(label, items)} />
          ) : (
            <Note tone="warn">
              <div className="mb-2">유형을 확정하지 못했습니다(혼합/불명). 지시로 다시 해석하거나 유형을 직접 고르세요.</div>
              <div className="flex gap-2">
                <Btn small busy={busy === "force"} onClick={() => reinterpret("date")}>등록일 변경으로 처리</Btn>
                <Btn small busy={busy === "force"} onClick={() => reinterpret("new")}>신규 등록으로 처리</Btn>
              </div>
            </Note>
          )}
          {result}

          <details className="rounded-xl border border-line p-3">
            <summary className="cursor-pointer text-[13px] font-semibold text-ink-2">Claude 에게 직접 지시 (잘못 판별했을 때)</summary>
            <div className="mt-3 space-y-2">
              <Textarea rows={3} value={instruction} onChange={(e) => setInstruction(e.target.value)}
                        placeholder="예: 이건 전부 등록일 변경, 7/21로 · PM95 13→15 신규 등록으로 처리 · 첨부메일들도 등록일 변경 대상" />
              <Btn kind="soft" busy={busy === "ask"} onClick={() => reinterpret()}>이 지시로 다시 해석</Btn>
            </div>
          </details>
          <details className="rounded-xl border border-line p-3">
            <summary className="cursor-pointer text-[13px] font-semibold text-ink-2">블록별 AI 판정</summary>
            <div className="mt-2 space-y-1">
              {view.blocks.map((b) => (
                <div key={b.index} className="text-[12.5px] text-ink-2">
                  <Tag>{({ date: "날짜변경", new: "신규", skip: "제외" } as Record<string, string>)[b.plan.type ?? ""] ?? (b.plan.type || "-")}</Tag>{" "}
                  {b.source.slice(0, 60)} · SN {b.serials.toLocaleString()}
                  {b.plan.model ? ` · ${b.plan.model} ${b.plan.from_os ?? ""}→${b.plan.to_os ?? ""}` : ""}{b.plan.date ? ` · ${b.plan.date}` : ""}
                  {b.plan.note && <div className="pl-4 text-ink-3">{b.plan.note}</div>}
                </div>
              ))}
            </div>
          </details>
          <div className="flex justify-end border-t border-line pt-3">
            <Btn kind="ghost" onClick={async () => { await post(`/api/wl/threads/${t.tid}/close?note=${encodeURIComponent("유기적 처리")}`); toast("ok", "처리 완료로 표시했습니다"); onChanged(); }}>처리 완료(숨김)</Btn>
          </div>
        </div>
      )}

      {step === "check" && (
        <div className="space-y-3">
          <VerifyPanel items={verifyItems} kind={verifyKind} result={verifyRes} onResult={setVerifyRes} />
          {verifyRes && !verifyRes.error && <div className="flex justify-end"><Btn kind="primary" onClick={() => setStep("reply")}>회신 단계로</Btn></div>}
        </div>
      )}

      {step === "reply" && (
        <ReplyComposer tid={t.tid} canSend={t.is_admin && verifyItems.length > 0} note="유기적 처리"
          draftReq={{ kind: verifyKind, new_date: verifyKind === "등록일 변경" ? newDate : "", sns: verifyItems.map((i) => i.sn) }}
          onSent={onChanged} onSkip={async () => { await post(`/api/wl/threads/${t.tid}/close?note=${encodeURIComponent("유기적 처리")}`); onChanged(); }} />
      )}
    </div>
  );
}

type Ask = (title: string, body: React.ReactNode, go: () => void, label?: string) => void;

function DateMode({ t, sns, date, setDate, ask, busy, setBusy, onResult, onDone }: {
  t: ThreadDetail; sns: string[]; date: string; setDate: (v: string) => void; ask: Ask; busy: string; setBusy: (v: string) => void;
  onResult: (n: React.ReactNode) => void; onDone: (items: VerifyItem[]) => void;
}) {
  const toast = useToast();
  const [cur, setCur] = useState<{ total: number; rows: { sn: string; date: string; osType: string }[]; login: boolean } | null>(null);
  async function sample() {
    setBusy("sample");
    try { setCur(await post(`/api/wl/organic/${t.tid}/current-dates`)); } catch (e) { toast("error", errText(e)); } finally { setBusy(""); }
  }
  async function run(write: boolean) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) { toast("warn", "등록일은 YYYY-MM-DD 형식입니다"); return; }
    setBusy(write ? "run" : "dry");
    try {
      const r = await post<{ ok: boolean; dry: boolean; lines: string[]; verify_items: VerifyItem[] }>(
        `/api/wl/threads/${t.tid}/change-date`, { sns, date, write });
      onResult(
        <div className="space-y-1 rounded-xl border border-line p-3">
          <div className="flex items-center gap-2 text-[13px] font-semibold"><DryBadge dry={r.dry} /> 등록일 변경 · {sns.length.toLocaleString()}대</div>
          <div className="space-y-0.5 font-[family-name:var(--font-mono)] text-[12px]">
            {r.lines.map((l, i) => <div key={i} className={l.trim().startsWith("✘") ? "text-err" : "text-ink-2"}>{l}</div>)}
          </div>
        </div>);
      if (!r.ok) { toast("error", r.lines[0] ?? "변경 실패"); return; }
      if (r.dry) { toast("warn", `미리보기 · DB 미변경 · ${sns.length.toLocaleString()}대`); return; }
      toast("ok", `등록일 변경 완료 · ${sns.length.toLocaleString()}대`);
      onDone(r.verify_items);
    } catch (e) { toast("error", errText(e)); } finally { setBusy(""); }
  }
  const echo = dateRows(["x"], date)[0];
  return (
    <section className="space-y-3">
      <SubHead>등록일 변경 · {sns.length.toLocaleString()}대</SubHead>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="변경할 등록일"><Input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="w-44" /></Field>
        <div className="pb-2 text-[13px] font-semibold text-ink-2">→ 등록일 {echo.reg} · 지원 종료일 {echo.end}</div>
        <div className="flex-1" />
        <Btn small kind="ghost" busy={busy === "sample"} onClick={sample}>현재 등록일 조회(샘플 10대)</Btn>
      </div>
      {cur && (
        <Table rows={cur.rows} max={220} cols={[
          { key: "sn", label: "SN", mono: true },
          { key: "date", label: "현재 등록일", render: (r) => r.date || <span className="text-warn">미등록</span> },
          { key: "osType", label: "osType", mono: true },
        ]} />
      )}
      <p className="text-[12.5px] text-ink-3">FOTA DB 에 한 트랜잭션으로 반영합니다. 미등록 SN 이 섞이면 아무것도 바꾸지 않습니다.</p>
      <DateTable rows={dateRows(sns.slice(0, 500), date)} />
      <div className="flex gap-2">
        <Btn busy={busy === "dry"} onClick={() => run(false)}>미리보기(dry-run)</Btn>
        <Btn kind="danger" busy={busy === "run"} disabled={!t.is_admin}
             onClick={() => ask("등록일 변경 실행", <>{sns.length.toLocaleString()}대의 등록일을 {date} 로 FOTA DB 에 반영합니다.</>, () => run(true), "실행")}>등록일 변경 실행</Btn>
      </div>
    </section>
  );
}

function NewMode({ t, view, opts, ask, busy, setBusy, onResult, onDone }: {
  t: ThreadDetail; view: OrgView; opts: Options | null; ask: Ask; busy: string; setBusy: (v: string) => void;
  onResult: (n: React.ReactNode) => void; onDone: (label: string, items: VerifyItem[]) => void;
}) {
  const toast = useToast();
  const [category, setCategory] = useState(view.auto_category);
  const [picks, setPicks] = useState<Record<string, Pick>>({});
  const [manual, setManual] = useState<Record<string, { model: string; from: string; to: string }>>({});
  const [pv, setPv] = useState<OrgPreview | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [mModel, setMModel] = useState("");
  const [mRule, setMRule] = useState("");
  // 체크를 푼 S/N — 미리보기 숫자에서도, 실제 등록에서도 빠진다
  const [excl, setExcl] = useState<Record<string, boolean>>({});
  const exclude = useMemo(() => Object.keys(excl).filter((k) => excl[k]), [excl]);
  const setOn = (sns: string[], on: boolean) => setExcl((x) => ({ ...x, ...Object.fromEntries(sns.map((s) => [s, !on])) }));

  const load = useCallback(async (refreshLookup = false) => {
    try {
      setPv(await post<OrgPreview>(`/api/wl/organic/${t.tid}/new-preview`, { category, picks, manual, exclude, refresh_lookup: refreshLookup }));
    } catch (e) { toast("error", errText(e)); }
  }, [t.tid, category, picks, manual, exclude, toast]);

  const relook = useRef(0);
  useEffect(() => {
    const again = refresh > relook.current;
    relook.current = refresh;
    load(again);
  }, [load, refresh]);

  const rulesOf = (model: string) => {
    const all = (opts?.rules ?? []).filter((r) => r.model === model);
    const inCat = all.filter((r) => r.category === category);
    return inCat.length ? inCat : all;
  };

  async function run(write: boolean) {
    setBusy(write ? "run" : "dry");
    try {
      const r = await post<OrgRun>(`/api/wl/organic/${t.tid}/new-run`, { category, picks, manual, exclude, write });
      onResult(
        <div className="space-y-1 rounded-xl border border-line p-3 text-[13px]">
          <div className={cx("flex flex-wrap items-center gap-2 font-semibold", r.fail_count ? "text-warn" : "text-ok")}>
            <DryBadge dry={r.dry} /> {r.label} — 성공 {r.success.toLocaleString()} / 실패·미등록 {(r.fail_count ?? r.fails.length).toLocaleString()}
            {r.invalid ? ` · 무효 ${r.invalid.toLocaleString()}` : ""}
          </div>
          {r.message && <div className="text-ink-2">{r.message}</div>}
          {r.reasons.length > 0 && <div className="text-err">무효 사유: {r.reasons.join("; ")}</div>}
          {r.fails.length > 0 && <div className="text-err">실패 예: {r.fails.slice(0, 15).map((f) => f.sn).join(", ")}</div>}
          {r.request && <div className="text-ink-3">등록 내역 기록 · {r.request.id}</div>}
        </div>);
      if (!r.dry && r.verify_items.length) {
        toast(r.ok ? "ok" : "warn", r.ok ? "등록 완료" : "일부 실패 — 확인 단계에서 대조합니다");
        onDone(r.label, r.verify_items);
      }
    } catch (e) { toast("error", errText(e)); } finally { setBusy(""); }
  }

  function applyManual(clear: boolean) {
    // 체크한(보낼) 모델 미확인 S/N 에 지정한다
    const targets = pv ? pv.unknown.map((u) => u.sn).filter((sn) => !excl[sn]) : [];
    if (!clear) {
      const r = rulesOf(mModel).find((x) => `${x.from}→${x.to}` === mRule);
      if (!mModel || !r) { toast("warn", "모델과 From→To 를 모두 고르세요"); return; }
      setManual((m) => ({ ...m, ...Object.fromEntries(targets.map((sn) => [sn, { model: mModel, from: String(r.from), to: String(r.to) }])) }));
      toast("ok", `${targets.length.toLocaleString()}대에 ${mModel} ${mRule} 지정`);
    } else {
      setManual((m) => { const n = { ...m }; targets.forEach((sn) => delete n[sn]); return n; });
    }
  }

  if (!pv) return <div className="flex items-center gap-2 text-[13px] text-ink-3"><span className="h-4 w-4 animate-spin rounded-full border-2 border-brand border-t-transparent" />포털에서 S/N {view.new_sns.length.toLocaleString()}건의 모델을 조회하는 중…</div>;

  const mRules = rulesOf(mModel);
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="구분"><Select value={category} onChange={setCategory} options={opts?.categories ?? [category]} className="w-32" /></Field>
        <div className="pb-2 text-[12.5px] text-ink-3">포털 조회 {pv.looked_up.toLocaleString()}/{pv.total.toLocaleString()} · 모델별로 나눠 보여주고, 올리는 것은 한 번에 합니다</div>
        <div className="flex-1" />
        <Btn small kind="ghost" onClick={() => { setPv(null); setRefresh((n) => n + 1); }}>모델 다시 조회</Btn>
      </div>
      {pv.error && <Note tone="warn">⚠ {pv.error}</Note>}

      {pv.models.map((m) => {
        const rs = m.rules;
        const p = picks[m.model];
        return (
          <div key={m.model} className="space-y-2 rounded-xl border border-line p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[14px] font-bold">{m.model}</span><Tag>{m.count.toLocaleString()}대</Tag>
              {m.all > m.count && <Tag tone="none">제외 {(m.all - m.count).toLocaleString()}</Tag>}
              <Tag tone={m.valid === m.count ? "ok" : "warn"}>유효 {m.valid.toLocaleString()}</Tag>
              <div className="flex-1" />
              {rs.length === 1 ? <span className="text-[12.5px] text-ink-2">{rs[0].from} → {rs[0].to} · <span className="font-[family-name:var(--font-mono)]">{rs[0].ostype}</span> <Tag tone="ok">규칙 1개 · 자동</Tag></span>
                : !rs.length ? <Tag tone="warn">규칙 없음 — 규칙 탭에서 추가</Tag>
                : <Select className="w-40" value={p ? `${p.from}→${p.to}` : ""} placeholder="From→To 선택"
                    onChange={(v) => { const r = rs.find((x) => `${x.from}→${x.to}` === v); setPicks((s) => ({ ...s, [m.model]: { from: String(r?.from ?? ""), to: String(r?.to ?? "") } })); }}
                    options={rs.map((r) => ({ value: `${r.from}→${r.to}`, label: `${r.from}→${r.to} · ${r.ostype}` }))} />}
            </div>
            <UploadTable rows={m.rows} max={220} pick={{
              on: (sn) => !excl[sn], set: (sn, v) => setOn([sn], v),
              all: m.sns.every((sn) => !excl[sn]), setAll: (v) => setOn(m.sns, v),
            }} />
            {m.count > m.rows.length && <div className="text-[12px] text-ink-3">{m.count.toLocaleString()}대 중 {m.rows.length}줄 표시</div>}
          </div>
        );
      })}

      {pv.unknown_total > 0 && (
        <div className="space-y-2 rounded-xl border border-err/30 border-l-4 border-l-err p-3">
          <div className="text-[14px] font-bold text-err">모델을 못 받은 S/N · {pv.unknown_total.toLocaleString()}대
            {Object.keys(manual).length > 0 && <span className="font-normal text-ink-2"> (지정 {Object.keys(manual).length.toLocaleString()}대)</span>}</div>
          <div className="text-[12.5px] text-ink-2">포털 단말 마스터에 없거나 조회가 실패했습니다. 모델과 OS 를 지정한 행만 등록에 들어갑니다. 지정은 체크한 행에 적용되고, 체크를 푼 행은 보내지 않습니다.</div>
          <div className="flex flex-wrap items-end gap-2">
            <Field label="모델"><Select className="w-36" value={mModel} placeholder="선택" options={pv.rule_models} onChange={(v) => { setMModel(v); const rs = rulesOf(v); setMRule(rs.length === 1 ? `${rs[0].from}→${rs[0].to}` : ""); }} /></Field>
            <Field label="From→To"><Select className="w-36" value={mRule} placeholder="선택" options={mRules.map((r) => `${r.from}→${r.to}`)} onChange={setMRule} /></Field>
            <Btn kind="primary" small onClick={() => applyManual(false)}>지정</Btn>
            <Btn kind="ghost" small onClick={() => applyManual(true)}>지정 취소</Btn>
          </div>
          <Table rows={pv.unknown} max={260} rowTone={(r) => (r.status.startsWith("✅") ? undefined : "warn")} cols={[
            { key: "pick", w: "28px",
              label: (
                <input type="checkbox" aria-label="전체 선택" className="accent-[var(--brand)]"
                       checked={pv.unknown.every((u) => !excl[u.sn])}
                       onChange={(e) => setOn(pv.unknown.map((u) => u.sn), e.target.checked)} />
              ),
              render: (r) => <input type="checkbox" checked={!excl[r.sn]} onChange={(e) => setOn([r.sn], e.target.checked)} className="accent-[var(--brand)]" /> },
            { key: "sn", label: "S/N", mono: true, render: (r) => <span className={r.excluded ? "text-ink-3 line-through" : ""}>{r.sn}</span> }, { key: "model", label: "모델" }, { key: "os", label: "From→To" },
            { key: "ostype", label: "ostype", mono: true }, { key: "status", label: "상태" },
          ]} />
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <Btn busy={busy === "dry"} onClick={() => run(false)}>미리보기(dry-run)</Btn>
        <Btn kind="danger" busy={busy === "run"} disabled={!t.is_admin || !pv.ready}
             onClick={() => ask("신규 등록 실행", <>{pv.ready.toLocaleString()}대를 FOTA 에 등록합니다. 미등록은 엑셀 업로드, 이미 등록된 단말은 FOTA DB 에서 덮어씁니다.</>, () => run(true), "실행")}>
          신규 등록 실행 · {pv.ready.toLocaleString()}대
        </Btn>
      </div>
    </section>
  );
}
