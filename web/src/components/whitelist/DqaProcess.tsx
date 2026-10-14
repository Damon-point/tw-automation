"use client";

import { useEffect, useState } from "react";
import { Tag, cx } from "@/components/ui";
import { useApi } from "@/lib/api";
import { errText, post, type DqaView, type ThreadDetail, type VerifyResult } from "@/lib/whitelist";
import { Btn, Field, Input, Note, Pre, SubHead, Table, useConfirm, useToast } from "./kit";
import { ReplyComposer, UploadTable, VerifyPanel } from "./parts";

function StepRow({ n, title, state, children, right }: {
  n: string; title: string; state: "todo" | "done" | "active" | "locked"; children?: React.ReactNode; right?: React.ReactNode;
}) {
  return (
    <div className={cx("relative rounded-xl border p-4", state === "done" ? "border-ok/40" : state === "active" ? "border-brand/50" : "border-line", state === "locked" && "opacity-60")}>
      <div className="flex flex-wrap items-center gap-2.5">
        <span className={cx("grid h-7 min-w-7 place-items-center rounded-full px-1.5 text-[12px] font-bold",
          state === "done" ? "bg-ok text-white" : state === "active" ? "bg-brand text-white" : "bg-muted-soft text-ink-3")}>{state === "done" ? "✓" : n}</span>
        <span className="flex-1 text-[14px] font-bold">{title}</span>
        {right}
      </div>
      {children && <div className="mt-3">{children}</div>}
    </div>
  );
}

export default function DqaProcess({ t, onChanged }: { t: ThreadDetail; onChanged: () => void }) {
  const toast = useToast();
  const { ask, node: confirmNode } = useConfirm();
  const { data: v, reload, error } = useApi<DqaView>(`/api/wl/dqa/${t.tid}`, (d) => (d?.job?.running ? 1000 : undefined));
  const [busy, setBusy] = useState("");
  const [report, setReport] = useState<{ title: string; text: string } | null>(null);
  const [verifyRes, setVerifyRes] = useState<VerifyResult | null>(null);
  const [fix, setFix] = useState<{ product: string; android: string; os_type: string; sns: string; froms: { version: string; path: string }[]; to: { version: string; path: string; full_pfu: string; diff_pfu: string } | null } | null>(null);

  useEffect(() => {
    if (v && !fix) {
      setFix({ product: v.parsed.product, android: v.parsed.android, os_type: v.plan.os_type, sns: v.parsed.sns.join(", "),
        froms: v.parsed.froms.map((f) => ({ version: f.version, path: f.path })), to: v.parsed.to ? { ...v.parsed.to } : null });
    }
  }, [v, fix]);

  if (error) return <Note tone="error">{error}</Note>;
  if (!v || !fix) return <div className="h-60 animate-pulse rounded-xl bg-muted-soft" />;
  const plan = v.plan;
  const job = v.job;

  async function act<T>(key: string, path: string, body?: unknown): Promise<T | null> {
    setBusy(key);
    try { return await post<T>(path, body); } catch (e) { toast("error", errText(e)); return null; } finally { setBusy(""); }
  }

  async function recalc() {
    const r = await act<DqaView>("recalc", `/api/wl/dqa/${t.tid}/recalc`, {
      product: fix!.product, android: fix!.android, os_type: fix!.os_type,
      sns: fix!.sns.split(/[,\s]+/).filter(Boolean), froms: fix!.froms, to: fix!.to,
    });
    if (r) { toast("info", `OS_Type=${r.plan.os_type || "(미해결)"} 로 다시 계산`); reload(); }
  }
  async function download(force = false) {
    const r = await act<{ ok: boolean; need_force?: boolean; message: string }>("dl", `/api/wl/dqa/${t.tid}/download`, { force });
    if (!r) return;
    if (r.need_force) { ask("다시 받기", <>{r.message}. 같은 빌드를 다시 받아 다시 올립니다(수 GB · 수 분).</>, () => download(true), "다시 받기"); return; }
    toast("ok", r.message); reload();
  }
  async function osinfo() {
    const r = await act<{ ok: boolean; dry: boolean; report: string }>("oi", `/api/wl/dqa/${t.tid}/osinfo`);
    if (r) { setReport({ title: `③-1 OsInfo 등록 ${r.ok ? "· 확인됨" : "· 실패"}`, text: r.report }); toast(r.ok ? "ok" : "error", r.ok ? "OsInfo 등록·확인 완료" : "OsInfo 등록 실패"); reload(); }
  }
  async function osinfoCheck() {
    const r = await act<{ ok: boolean; report: string }>("oic", `/api/wl/dqa/${t.tid}/osinfo-check`);
    if (r) { setReport({ title: "③ OsInfo 확인", text: r.report }); toast(r.ok ? "ok" : "warn", r.ok ? "OsInfo 확인됨" : "OsInfo 가 확인되지 않습니다"); reload(); }
  }
  async function dp() {
    const r = await act<{ ok: boolean; skipped: string; report: string }>("dp", `/api/wl/dqa/${t.tid}/downloadpath`);
    if (r) { setReport({ title: `③-2 DownloadPath 등록 ${r.ok ? "성공" : "오류"}`, text: r.report }); toast(r.skipped ? "error" : r.ok ? "ok" : "error", r.skipped || (r.ok ? "DownloadPath 등록 완료" : "등록 오류")); reload(); }
  }
  async function whitelist() {
    const r = await act<{ ok: boolean; dry: boolean; msg: string; details: string[]; request?: { id: string } }>("wl", `/api/wl/dqa/${t.tid}/whitelist`);
    if (r) {
      setReport({ title: `④ 화이트리스트 ${r.dry ? "(미리보기 · 반영 안 됨)" : r.ok ? "성공" : "오류"}`, text: [r.msg, ...r.details].join("\n") });
      toast(r.dry ? "warn" : r.ok ? "ok" : "error", r.dry ? "미리보기 · 반영 안 됨" : r.ok ? `SN 등록 완료${r.request ? ` · ${r.request.id}` : ""}` : "SN 등록 오류");
      setVerifyRes(null); reload(); onChanged();
    }
  }

  const uploaded = plan.uploaded;
  const wlDone = v.verify_items.length > 0;
  const finish = async () => { await post(`/api/wl/threads/${t.tid}/close?note=${encodeURIComponent("DQA FOTA Test 처리")}`); toast("ok", "처리 완료로 표시했습니다"); onChanged(); };

  return (
    <div className="space-y-5">
      {confirmNode}
      {/* 파싱 결과 */}
      <section className="rounded-xl border border-line bg-surface-2 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[15px] font-bold">{plan.product || "?"} · Android {plan.android || "?"}</span>
          <Tag tone={plan.os_type ? "ok" : "error"}>OS_Type {plan.os_type || "미해결"}</Tag>
          <Tag>FOTA 최신 os_version {plan.latest}</Tag>
          <Tag>api {plan.api ?? "-"}</Tag><Tag>endDate {plan.end_date}</Tag>
        </div>
        <div className="mt-2 font-[family-name:var(--font-mono)] text-[12.5px] text-ink-2">SN: {v.parsed.sns.join(", ") || "(없음)"}</div>
        <div className="mt-1 text-[12.5px] text-ink-3">버전 표기: {plan.version_prefix ? `'${plan.version_prefix}' 접두어` : "숫자만(접두어 없음)"}{plan.version_prefix_src ? ` ← ${plan.version_prefix_src}` : ""}</div>
        <div className="mt-2 space-y-0.5 text-[12.5px] text-ink-2">
          {v.parsed.froms.map((f, i) => <div key={i}>FROM [{f.dtype}] <b>{f.version}</b> → <span className="break-all text-ink-3">{f.path}</span></div>)}
          {v.parsed.to && <div>TO <b>{v.parsed.to.version}</b> → <span className="break-all text-ink-3">{v.parsed.to.path}</span></div>}
        </div>
        {plan.note && <div className="mt-2 text-[12.5px] text-warn">· {plan.note}</div>}
        {plan.errors.map((e, i) => <div key={`e${i}`} className="mt-1 text-[12.5px] text-err">✘ {e}</div>)}
        {plan.warnings.map((w, i) => <div key={`w${i}`} className="mt-1 text-[12.5px] text-warn">⚠ {w}</div>)}
      </section>

      <details className="rounded-xl border border-line p-3" open={!plan.os_type || plan.warnings.length > 0}>
        <summary className="cursor-pointer text-[13px] font-semibold text-ink-2">파싱 값 직접 수정 (경로 · 버전 · SN · OS_Type)</summary>
        <div className="mt-3 space-y-3">
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="제품(PMxx)"><Input value={fix.product} onChange={(e) => setFix({ ...fix, product: e.target.value })} /></Field>
            <Field label="Android"><Input value={fix.android} onChange={(e) => setFix({ ...fix, android: e.target.value })} /></Field>
            <Field label="OS_Type"><Input value={fix.os_type} onChange={(e) => setFix({ ...fix, os_type: e.target.value })} /></Field>
          </div>
          <Field label="SN (쉼표로 여러 개)"><Input value={fix.sns} onChange={(e) => setFix({ ...fix, sns: e.target.value })} /></Field>
          {fix.froms.map((f, i) => (
            <div key={i} className="grid gap-3 sm:grid-cols-[14rem_1fr]">
              <Field label={`FROM [${v.parsed.froms[i]?.dtype}] 버전`}><Input value={f.version} onChange={(e) => { const fr = [...fix.froms]; fr[i] = { ...f, version: e.target.value }; setFix({ ...fix, froms: fr }); }} /></Field>
              <Field label="경로(UNC)"><Input value={f.path} onChange={(e) => { const fr = [...fix.froms]; fr[i] = { ...f, path: e.target.value }; setFix({ ...fix, froms: fr }); }} /></Field>
            </div>
          ))}
          {fix.to && (
            <>
              <div className="grid gap-3 sm:grid-cols-[14rem_1fr]">
                <Field label="TO 버전"><Input value={fix.to.version} onChange={(e) => setFix({ ...fix, to: { ...fix.to!, version: e.target.value } })} /></Field>
                <Field label="경로(UNC)"><Input value={fix.to.path} onChange={(e) => setFix({ ...fix, to: { ...fix.to!, path: e.target.value } })} /></Field>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Full PFU(선택)"><Input value={fix.to.full_pfu} onChange={(e) => setFix({ ...fix, to: { ...fix.to!, full_pfu: e.target.value } })} /></Field>
                <Field label="Diff PFU(선택)"><Input value={fix.to.diff_pfu} onChange={(e) => setFix({ ...fix, to: { ...fix.to!, diff_pfu: e.target.value } })} /></Field>
              </div>
            </>
          )}
          <Btn kind="primary" busy={busy === "recalc"} onClick={recalc}>적용 · 다시 계산</Btn>
        </div>
      </details>

      <div className="grid gap-4 xl:grid-cols-2">
        <div>
          <SubHead>OsInfo (등록될 행)</SubHead>
          <Table rows={plan.oi_rows} max={200} cols={[
            { key: "osType", label: "osType", mono: true }, { key: "version", label: "version" },
            { key: "osVersion", label: "osVersion" }, { key: "description", label: "description" },
          ]} />
        </div>
        <div>
          <SubHead>DownloadPath (from=dummy · to=실파일)</SubHead>
          <Table rows={plan.dp_rows} max={200} cols={[
            { key: "osVersion", label: "osVersion" }, { key: "downloadType", label: "type" },
            { key: "fileNm", label: "fileNm", mono: true }, { key: "size", label: "size" },
            { key: "downloadUrl", label: "downloadUrl", render: (r) => <span className="block max-w-[260px] truncate" title={r.downloadUrl}>{r.downloadUrl}</span> },
          ]} />
        </div>
      </div>

      <div className="space-y-3">
        <StepRow n="1" title="빌드 다운로드 + Dropbox 업로드" state={job?.running ? "active" : uploaded ? "done" : "todo"}
          right={<>
            {job?.running && <Btn small kind="danger" onClick={async () => { await post(`/api/wl/dqa/${t.tid}/cancel`); toast("warn", "중단을 요청했습니다"); reload(); }}>중단</Btn>}
            {job && !job.running && <Btn small kind="ghost" onClick={async () => { await post(`/api/wl/dqa/${t.tid}/clear`); reload(); }}>기록 지우기</Btn>}
            <Btn small kind="primary" busy={busy === "dl"} disabled={job?.running || !t.is_admin} onClick={() => download()}>{uploaded ? "다시 받기" : "시작"}</Btn>
          </>}>
          {job && (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2 text-[13px]">
                <b>{job.headline || job.state}</b>{job.restored && <Tag>이전 실행 기록</Tag>}
                <span className="text-ink-3">경과 {job.elapsed}</span>
              </div>
              {job.running && (
                <>
                  <div className="h-2.5 overflow-hidden rounded-full bg-muted-soft"><div className="h-full rounded-full bg-brand transition-all" style={{ width: `${Math.round(job.ratio * 100)}%` }} /></div>
                  <div className="text-[12.5px] text-ink-2">{job.detail}</div>
                  <div className="text-[12px] text-ink-3">속도 {job.speed} · 남은 시간 {job.eta}</div>
                </>
              )}
              {!job.running && job.message && <div className="text-[12.5px] text-ink-2">{job.message}</div>}
              {job.logs.length > 0 && (
                <details><summary className="cursor-pointer text-[12.5px] font-semibold text-ink-3">실행 로그 {job.logs.length}줄</summary><Pre>{job.logs.join("\n")}</Pre></details>
              )}
            </div>
          )}
          {!job && <div className="text-[12.5px] text-ink-3">백그라운드에서 돕니다 — 화면을 닫아도 계속되고, 다시 열면 진행률이 이어집니다.</div>}
        </StepRow>

        <StepRow n="2" title="FOTA 등록 미리보기" state="todo"
          right={<Btn small busy={busy === "pv"} onClick={async () => { const r = await act<{ report: string }>("pv", `/api/wl/dqa/${t.tid}/fota-preview`); if (r) setReport({ title: "② FOTA 등록 미리보기(dry-run)", text: r.report }); }}>미리보기</Btn>} />

        <StepRow n="3" title="FOTA 실제 등록 — OsInfo → DownloadPath" state={v.osinfo_ok ? "done" : "todo"}
          right={<Tag tone={v.osinfo_ok ? "ok" : "warn"}>{v.osinfo_ok ? "OsInfo 확인됨" : "OsInfo 미확인"}</Tag>}>
          <div className="flex flex-wrap gap-2">
            <Btn small kind="ghost" busy={busy === "oic"} onClick={osinfoCheck}>OsInfo 확인만</Btn>
            <Btn small kind="danger" busy={busy === "oi"} disabled={!plan.os_type || !t.is_admin}
                 onClick={() => ask("③-1 OsInfo 실제 등록", <>OsInfo {plan.oi_rows.length}행을 FOTA(production)에 등록합니다.<br />{plan.oi_rows.map((r) => `osv ${r.osVersion} = ${r.version}`).join(" · ")}</>, osinfo, "실제 등록")}>③-1 OsInfo 등록</Btn>
            <Btn small kind="danger" busy={busy === "dp"} disabled={!v.osinfo_ok || !uploaded || !t.is_admin}
                 title={!v.osinfo_ok ? "OsInfo 가 확인돼야 열립니다" : !uploaded ? "① 업로드가 먼저입니다" : ""}
                 onClick={() => ask("③-2 DownloadPath 실제 등록", <>DownloadPath {plan.dp_rows.length}행을 FOTA(production)에 등록합니다. 보내기 전에 OsInfo 가 있는지 다시 확인합니다.</>, dp, "실제 등록")}>③-2 DownloadPath 등록</Btn>
          </div>
        </StepRow>

        <StepRow n="4" title="SN 화이트리스트 등록" state={wlDone ? "done" : "todo"}
          right={<Btn small kind="danger" busy={busy === "wl"} disabled={!plan.os_type || !v.parsed.sns.length || !t.is_admin}
            onClick={() => ask("SN 화이트리스트 등록", <div className="space-y-2"><div>아래 {v.whitelist_rows.length}행을 올립니다. 이미 등록된 SN 은 덮어씁니다.</div><UploadTable rows={v.whitelist_rows} max={240} /></div>, whitelist, "등록")}>등록</Btn>}>
          <UploadTable rows={v.whitelist_rows} max={180} />
        </StepRow>

        <StepRow n="5" title="확인" state={verifyRes?.ok ? "done" : wlDone ? "active" : "locked"}>
          <VerifyPanel items={v.verify_items} kind={`DQA 화이트리스트 · osType=${plan.os_type} · api=${plan.api}`} result={verifyRes} onResult={setVerifyRes} />
        </StepRow>

        <StepRow n="6" title="메일 회신 (전체 회신)" state={wlDone ? "active" : "locked"}>
          {wlDone ? <ReplyComposer tid={t.tid} withCc canSend={t.is_admin} draftReq={{ kind: "dqa" }} note="DQA FOTA Test 처리" onSent={onChanged} />
            : <Note>④ SN 화이트리스트 등록 뒤에 열립니다.</Note>}
        </StepRow>
      </div>

      {report && (
        <div>
          <SubHead right={<Btn small kind="ghost" onClick={() => setReport(null)}>닫기</Btn>}>{report.title}</SubHead>
          <Pre max={360}>{report.text}</Pre>
        </div>
      )}

      <div className="flex justify-end border-t border-line pt-3"><Btn kind="ghost" onClick={finish}>처리 완료(숨김)</Btn></div>
    </div>
  );
}
