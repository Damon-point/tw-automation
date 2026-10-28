"use client";

import { useEffect, useMemo, useState } from "react";
import { Tag, cx } from "@/components/ui";
import { useApi } from "@/lib/api";
import {
  STATE_LABEL, dateRows, errText, post, today, upload,
  type FotaBrief, type Options, type ThreadDetail, type UploadRow, type VerifyItem, type VerifyResult,
} from "@/lib/whitelist";
import { Btn, Check, Field, Input, Note, Seg, Select, Steps, SubHead, useConfirm, useToast } from "./kit";
import { DateTable, DryBadge, ReplyComposer, UploadTable, VerifyPanel } from "./parts";

type Step = "proc" | "check" | "reply";

export default function SimpleProcess({ t, onChanged }: { t: ThreadDetail; onChanged: () => void }) {
  const toast = useToast();
  const { ask, node: confirmNode } = useConfirm();
  const opts = useApi<Options>("/api/wl/options").data;

  const [step, setStep] = useState<Step>("proc");
  const [picked, setPicked] = useState<Record<string, boolean>>(() => Object.fromEntries(t.serials.map((s) => [s.sn, !s.dup])));
  const [status, setStatus] = useState<Record<string, boolean> | null>(null);
  const [model, setModel] = useState(t.model);
  const [cur, setCur] = useState(t.from_os);
  const [upg, setUpg] = useState(t.to_os);
  const [category, setCategory] = useState(t.auto_category);
  const [override, setOverride] = useState("");
  const [rows, setRows] = useState<UploadRow[]>([]);
  const [validN, setValidN] = useState(0);
  const [charge, setCharge] = useState<"무상" | "유상">("무상");
  const [po, setPo] = useState<File | null>(null);
  const [freeReason, setFreeReason] = useState("");
  const [memo, setMemo] = useState("");
  const [dry, setDry] = useState(false);
  const [newDate, setNewDate] = useState(today());
  const [busy, setBusy] = useState("");
  const [lastFota, setLastFota] = useState<(FotaBrief & { label: string }) | null>(null);
  const [dbLines, setDbLines] = useState<string[]>([]);
  const [reqId, setReqId] = useState("");
  const [verifyKind, setVerifyKind] = useState("");
  const [verifyItems, setVerifyItems] = useState<VerifyItem[]>([]);
  const [verifyRes, setVerifyRes] = useState<VerifyResult | null>(null);
  const [doneSns, setDoneSns] = useState<string[]>([]);
  const [skipped, setSkipped] = useState(false);

  const sel = useMemo(() => t.serials.map((s) => s.sn).filter((s) => picked[s]), [t.serials, picked]);
  const newSns = status ? sel.filter((s) => !status[s]) : [];
  const regSns = status ? sel.filter((s) => status[s]) : [];
  const leadChange = sel.length > 0 && status !== null && newSns.length === 0;

  useEffect(() => {
    setStatus(null);
    post<{ status: Record<string, boolean> }>("/api/wl/fota-status", { sns: t.serials.map((s) => s.sn) })
      .then((r) => setStatus(r.status)).catch((e) => toast("error", errText(e)));
  }, [t.serials, toast]);

  const specKey = JSON.stringify({ sel, model, cur, upg, category, override, regSns });
  useEffect(() => {
    if (!sel.length) { setRows([]); setValidN(0); return; }
    const h = setTimeout(() => {
      post<{ rows: UploadRow[]; valid: number }>(`/api/wl/threads/${t.tid}/devices`,
        { sns: sel, model, cur, upg, category, ostype_override: override, registered: regSns })
        .then((r) => { setRows(r.rows); setValidN(r.valid); }).catch(() => {});
    }, 200);
    return () => clearTimeout(h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [specKey, t.tid]);

  const spec = { sns: sel, model, cur, upg, category, ostype_override: override, registered: regSns };

  async function register() {
    setBusy("reg");
    try {
      const r = await post<{ dry: boolean; fota: FotaBrief; request: { id: string } | null; verify_items: VerifyItem[]; devices?: { sn: string }[] }>(
        `/api/wl/threads/${t.tid}/register`, { ...spec, charge_type: charge, free_reason: freeReason, memo, write: !dry });
      setLastFota({ ...r.fota, label: "등록" });
      if (r.dry) { toast("warn", `미리보기 · 반영 안 됨 · 대상 ${r.fota.total}대`); return; }
      if (r.request) {
        setReqId(r.request.id);
        if (charge === "유상" && po) {
          try { await upload(`/api/wl/requests/${r.request.id}/po`, po); } catch (e) { toast("error", `PO 파일 저장 실패: ${errText(e)}`); }
        }
      }
      setDoneSns((r.devices ?? []).map((d) => d.sn));
      setVerifyKind("등록"); setVerifyItems(r.verify_items); setVerifyRes(null);
      toast("ok", `등록 완료 · FOTA ${r.fota.success}/${r.fota.total}`);
      setStep("check");
      onChanged();
    } catch (e) {
      toast("error", errText(e));
    } finally { setBusy(""); }
  }

  async function changeDate() {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(newDate)) { toast("warn", "날짜는 YYYY-MM-DD 형식입니다"); return; }
    setBusy("date");
    try {
      const r = await post<{ ok: boolean; dry: boolean; lines: string[]; request: { id: string } | null; verify_items: VerifyItem[] }>(
        `/api/wl/threads/${t.tid}/change-date`, { sns: regSns, date: newDate, write: !dry });
      setDbLines(r.lines);
      if (!r.ok) { toast("error", r.lines[0] ?? "변경 실패"); return; }
      if (r.dry) { toast("warn", `미리보기 · DB 미변경 · 대상 ${regSns.length}대`); return; }
      if (r.request) setReqId(r.request.id);
      setDoneSns(regSns);
      setVerifyKind("등록일 변경"); setVerifyItems(r.verify_items); setVerifyRes(null);
      toast("ok", `등록일 변경 완료 · ${regSns.length}대`);
      setStep("check");
      onChanged();
    } catch (e) {
      toast("error", errText(e));
    } finally { setBusy(""); }
  }

  const echo = dateRows(["x"], newDate)[0];

  const registerBlock = (
    <section className="space-y-3">
      <SubHead right={<span className="text-[12.5px] text-ink-3">전송 가능 {validN}대{regSns.length ? ` · 기존 등록 ${regSns.length}대(갱신)` : ""}</span>}>
        등록 · {sel.length}대
      </SubHead>
      <UploadTable rows={rows} max={260} />
      <div className="grid gap-3 sm:grid-cols-[auto_1fr]">
        <Seg value={charge} onChange={setCharge} options={[{ value: "무상", label: "무상" }, { value: "유상", label: "유상" }]} />
        {charge === "무상"
          ? <Input placeholder="무상 사유" value={freeReason} onChange={(e) => setFreeReason(e.target.value)} />
          : <label className="flex items-center gap-2 rounded-lg border border-dashed border-line-strong px-3 py-2 text-[13px] text-ink-2">
              <span className="font-semibold">PO 파일</span>
              <input type="file" onChange={(e) => setPo(e.target.files?.[0] ?? null)} className="min-w-0 flex-1 text-[12.5px]" />
            </label>}
      </div>
      <Input placeholder="메모(선택)" value={memo} onChange={(e) => setMemo(e.target.value)} />
      <div className="flex flex-wrap items-center gap-3">
        <Btn kind="primary" busy={busy === "reg"} disabled={!validN || !t.is_admin}
             onClick={() => dry ? register() : ask("FOTA 등록", <>표의 {validN}대를 FOTA 에 올립니다. 이미 등록된 단말은 osType·API·지원 종료일을 덮어씁니다.</>, register, "등록")}>
          등록 + FOTA 전송
        </Btn>
        {lastFota && lastFota.label === "등록" && (
          <span className="flex items-center gap-2 text-[12.5px] text-ink-2"><DryBadge dry={!!lastFota.dry} /> {lastFota.msg}</span>
        )}
      </div>
    </section>
  );

  const changeBlock = (
    <section className="space-y-3">
      <SubHead>등록일 변경 · {regSns.length}대 (이미 등록됨)</SubHead>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="변경할 등록일"><Input type="date" value={newDate} onChange={(e) => setNewDate(e.target.value)} className="w-44" /></Field>
        <div className="pb-2 text-[13px] font-semibold text-ink-2">→ 등록일 {echo.reg} · 지원 종료일 {echo.end}</div>
      </div>
      <p className="text-[12.5px] text-ink-3">FOTA DB 에 한 트랜잭션으로 반영합니다. androidApi·osType·partnumber 는 바꾸지 않습니다.</p>
      <DateTable rows={dateRows(regSns, newDate)} />
      <div className="flex flex-wrap items-center gap-3">
        <Btn kind="primary" busy={busy === "date"} disabled={!regSns.length || !t.is_admin}
             onClick={() => dry ? changeDate() : ask("등록일 변경", <>{regSns.length}대의 등록일을 {newDate} 로 FOTA DB 에 바로 반영합니다.</>, changeDate, "변경")}>
          등록일 변경
        </Btn>
      </div>
      {dbLines.length > 0 && (
        <div className="space-y-0.5 rounded-lg bg-surface-2 p-2.5 font-[family-name:var(--font-mono)] text-[12px]">
          {dbLines.map((l, i) => <div key={i} className={l.trim().startsWith("✘") ? "text-err" : "text-ink-2"}>{l}</div>)}
        </div>
      )}
    </section>
  );

  return (
    <div className="space-y-5">
      {confirmNode}
      <Steps current={step} onPick={(k) => setStep(k as Step)} steps={[
        { key: "proc", label: "처리", done: verifyItems.length > 0 || skipped },
        { key: "check", label: "확인", done: !!verifyRes?.ok },
        { key: "reply", label: "회신" },
      ]} />

      {step === "proc" && (
        <div className="space-y-5">
          {/* 일련번호 고르기 */}
          <section>
            <SubHead right={
              <span className="flex items-center gap-2 text-[12.5px] text-ink-3">
                {sel.length}/{t.serials.length} 선택
                <button onClick={() => setPicked(Object.fromEntries(t.serials.map((s) => [s.sn, true])))} disabled={sel.length === t.serials.length}
                        className="rounded-md border border-line px-2 py-0.5 font-semibold text-ink-2 hover:bg-surface-2 disabled:opacity-40">전체 선택</button>
                <button onClick={() => setPicked({})} disabled={!sel.length}
                        className="rounded-md border border-line px-2 py-0.5 font-semibold text-ink-2 hover:bg-surface-2 disabled:opacity-40">전체 해제</button>
              </span>
            }>일련번호</SubHead>
            <div className="flex flex-wrap gap-1.5">
              {t.serials.map((s) => {
                const on = !!picked[s.sn];
                const reg = status?.[s.sn];
                return (
                  <button key={s.sn} onClick={() => setPicked((p) => ({ ...p, [s.sn]: !on }))}
                          title={s.dup ? `이미 신청됨 · ${s.dup.request_id} (${s.dup.created_at})` : ""}
                          className={cx("inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 font-[family-name:var(--font-mono)] text-[12.5px] transition",
                            on ? "border-brand bg-brand-soft text-brand-ink" : "border-line bg-surface text-ink-3 line-through")}>
                    {s.sn}
                    {status === null ? <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-ink-3" />
                      : reg ? <span className="rounded bg-info-soft px-1 text-[10.5px] font-sans font-semibold text-info no-underline">등록됨</span>
                      : <span className="rounded bg-muted-soft px-1 text-[10.5px] font-sans font-semibold text-ink-2">신규</span>}
                    {s.dup && <span className="rounded bg-warn-soft px-1 text-[10.5px] font-sans font-semibold text-warn">신청됨</span>}
                  </button>
                );
              })}
            </div>
          </section>

          {/* 기준값 */}
          <section className="grid gap-3 rounded-xl border border-line bg-surface-2 p-3 sm:grid-cols-5">
            <Field label="요청 구분" hint={`자동: ${t.auto_category}`}>
              <Select value={category} onChange={setCategory} options={opts?.categories ?? [category]} />
            </Field>
            <Field label="모델"><Select value={model} onChange={setModel} placeholder="선택" options={Array.from(new Set([...(opts?.models ?? []), ...(model ? [model] : [])]))} /></Field>
            <Field label="Current OS"><Select value={cur} onChange={setCur} placeholder="-" options={(opts?.os_levels ?? []).map(String)} /></Field>
            <Field label="Upgrade To"><Select value={upg} onChange={setUpg} placeholder="-" options={(opts?.os_levels ?? []).map(String)} /></Field>
            <Field label="OS Type 직접 지정" hint="규칙 밖일 때만"><Input value={override} onChange={(e) => setOverride(e.target.value)} placeholder="White…" /></Field>
          </section>

          {status === null ? <div className="h-24 animate-pulse rounded-xl bg-muted-soft" /> : !sel.length ? <Note>선택한 일련번호가 없습니다.</Note> : (
            <>
              {leadChange ? <Tag tone="none">이미 전부 등록된 단말 {regSns.length}대 — 등록일 변경 요청</Tag>
                : regSns.length ? <Tag tone="none">신규 {newSns.length}대 · 이미 등록 {regSns.length}대</Tag> : null}
              {leadChange ? (
                <>
                  {changeBlock}
                  <details className="rounded-xl border border-line p-3">
                    <summary className="cursor-pointer text-[13px] font-semibold text-ink-2">이미 등록된 단말을 다시 등록(엑셀 업로드)</summary>
                    <div className="mt-3">{registerBlock}</div>
                  </details>
                </>
              ) : (
                <>
                  {registerBlock}
                  {regSns.length > 0 && <div className="border-t border-line pt-4">{changeBlock}</div>}
                </>
              )}
            </>
          )}

          <div className="flex flex-wrap items-center gap-4 border-t border-line pt-3">
            <Check checked={dry} onChange={setDry}>미리보기로만(dry-run)</Check>
            <div className="flex-1" />
            <Btn kind="ghost" onClick={() => { setSkipped(true); setStep("reply"); }}>처리 건너뛰고 회신만</Btn>
          </div>
        </div>
      )}

      {step === "check" && (
        <div className="space-y-3">
          <VerifyPanel items={verifyItems} kind={verifyKind} result={verifyRes} onResult={setVerifyRes} />
          {verifyRes && !verifyRes.error && (
            <div className="flex justify-end"><Btn kind={verifyRes.ok ? "primary" : "outline"} onClick={() => setStep("reply")}>회신 단계로</Btn></div>
          )}
        </div>
      )}

      {step === "reply" && (
        <ReplyComposer tid={t.tid} withCc canSend={t.is_admin && (verifyItems.length > 0 || skipped)} requestId={reqId}
          draftReq={{ kind: verifyKind, new_date: verifyKind === "등록일 변경" ? newDate : "", sns: doneSns.length ? doneSns : sel }}
          onSent={onChanged} />
      )}
      {t.state !== "open" && <div className="text-[12px] text-ink-3">현재 상태: {STATE_LABEL[t.state]}</div>}
    </div>
  );
}
