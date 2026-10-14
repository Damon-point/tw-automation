"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/Icons";
import { Card, Progress, ResultSummary, StatusChip, Tag, cx } from "@/components/ui";
import { ago } from "@/lib/api";
import {
  APP, REVIEW_META, canRework, editorOf, elapsedText, finishModeOf, jobApi, reviewKindOf, revertableId,
  type JobX, type ProgressInfo, type SideX, type StageX, type UploadOut, type UploadPlan,
} from "@/lib/job";
import { Btn, Field, Modal, inputCls, useAction } from "./kit";

type Ctx = { job: JobX; reload: () => void; progress: Record<string, ProgressInfo>; locked: boolean };

// ── 갈래 ─────────────────────────────────────────────────────────────────────

export function Lane({ side, ctx }: { side: SideX; ctx: Ctx }) {
  const revId = revertableId(side);
  const sub = side.side !== APP;
  return (
    <Card pad={false}
      title={<span className="flex items-center gap-2">
        <span className={cx("rounded-md px-1.5 py-0.5 text-[11px] font-bold", sub ? "bg-info-soft text-info" : "bg-brand-soft text-brand-ink")}>
          {sub ? "SUBMISSION" : "APPROVAL"}
        </span>
        <span>{sub ? "준비 — 외부에 쓰지 않음" : "실제 업로드 · 발송"}</span>
      </span>}
      right={<div className="flex items-center gap-2">
        <div className="flex w-[150px] items-center gap-2">
          <span className="whitespace-nowrap text-[12px] text-ink-3">{side.done}/{side.total}</span>
          <Progress done={side.done} total={side.total} />
        </div>
        <LaneMenu side={side} ctx={ctx} />
      </div>}>
      <RunAllNote side={side} ctx={ctx} />
      <NextAction side={side} ctx={ctx} />
      <ol>
        {side.stages.map((s, i) => (
          <StageRow key={s.key} s={s} n={i + 1} side={side} ctx={ctx} isNext={side.next === s.key} revertable={s.id != null && s.id === revId} />
        ))}
      </ol>
    </Card>
  );
}

// ── 갈래 한꺼번에: 전체 실행 · 전체 완료 · 전체 대기로 ─────────────────────────────

type Bulk = "run_all" | "done" | "pending";
const BULK: Record<Bulk, { title: string; body: string; ok: string; kind: "primary" | "ok" | "danger" }> = {
  run_all: { title: "남은 단계 전체 실행", ok: "전체 실행", kind: "primary",
    body: "남은 단계를 차례로 실행합니다. 각 단계는 끝나면 검수 대기로 남고, 검수 대기 · 실패 · 중단을 만나면 거기서 멈춥니다." },
  done: { title: "모든 단계를 완료로", ok: "전체 완료", kind: "ok",
    body: "실제로 실행하지 않고 이 갈래의 모든 단계를 완료로 표시합니다. 대체된 회차처럼 이력만 마감할 때 씁니다." },
  pending: { title: "모든 단계를 대기로", ok: "전체 대기로 초기화", kind: "danger",
    body: "이 갈래의 모든 단계를 대기로 되돌리고, 단계에 걸린 결재 상태(RN 승인 · 최종 검수 · 뉴스레터)도 지웁니다. 만든 파일은 그대로 남습니다." },
};

function LaneMenu({ side, ctx }: { side: SideX; ctx: Ctx }) {
  const [open, setOpen] = useState(false);
  const [ask, setAsk] = useState<Bulk | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const api = jobApi(ctx.job.job.id);
  const { busy, run } = useAction(ctx.reload);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);

  const running = side.stages.some((s) => s.status === "running");
  const next = side.stages.find((s) => s.key === side.next);
  const ra = ctx.job.run_all?.[side.side];
  const raActive = !!ra && !ra.stopped;
  const items: { k: Bulk; label: string; icon: string; off: string }[] = [
    ...(side.side === APP ? [{ k: "run_all" as Bulk, label: "전체 실행", icon: "play",
      off: raActive ? "전체 실행 중" : running ? "실행 중인 단계가 있음" : !next ? "남은 단계 없음" : next.status !== "pending" ? `${next.label} 검수 대기` : "" }] : []),
    { k: "done", label: "전체 완료", icon: "check", off: running || raActive ? "실행 중인 단계가 있음" : side.done === side.total ? "이미 모두 완료" : "" },
    { k: "pending", label: "전체 대기로 초기화", icon: "rounds", off: running || raActive ? "실행 중인 단계가 있음" : side.stages.every((s) => s.status === "pending") ? "이미 모두 대기" : "" },
  ];

  async function go(k: Bulk) {
    const r = await run(k, () => (k === "run_all" ? api.runAll(side.side) : api.bulkStatus(side.side, k)));
    if (r !== undefined) setAsk(null);
  }

  return (
    <div ref={box} className="relative">
      <button onClick={() => setOpen((v) => !v)} aria-label="갈래 전체 동작" aria-expanded={open}
              className="grid h-8 w-8 place-items-center rounded-lg text-[18px] leading-none text-ink-3 hover:bg-surface-2 hover:text-ink">⋯</button>
      {open && (
        <div className="absolute right-0 top-9 z-30 w-[230px] overflow-hidden rounded-xl border border-line bg-surface py-1 shadow-xl">
          {items.map((it) => (
            <button key={it.k} disabled={!!it.off} onClick={() => { setOpen(false); setAsk(it.k); }}
                    className={cx("flex w-full items-start gap-2.5 px-3.5 py-2.5 text-left hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-50",
                      it.k === "pending" && "text-err")}>
              <Icon name={it.icon} className="mt-0.5 h-4 w-4 shrink-0" />
              <span>
                <span className="block text-[13.5px] font-semibold">{it.label}</span>
                {it.off && <span className="block text-[11.5px] text-ink-3">{it.off}</span>}
              </span>
            </button>
          ))}
        </div>
      )}
      <Modal open={!!ask} onClose={() => setAsk(null)} title={ask ? `${side.side === APP ? "Approval" : "Submission"} — ${BULK[ask].title}` : ""}
             footer={ask && <>
               <Btn kind="outline" onClick={() => setAsk(null)}>취소</Btn>
               <Btn kind={BULK[ask].kind} busy={busy === ask} onClick={() => go(ask)}>{BULK[ask].ok}</Btn>
             </>}>
        {ask && (
          <div className="space-y-3">
            <p className="text-ink-2">{BULK[ask].body}</p>
            {ask !== "run_all" && (
              <ul className="max-h-[260px] overflow-auto rounded-lg border border-line text-[13px]">
                {side.stages.map((s) => (
                  <li key={s.key} className="flex items-center gap-2 border-b border-line px-3 py-1.5 last:border-b-0">
                    <span className="flex-1">{s.label}</span>
                    <StatusChip status={s.status} />
                    <span className="text-ink-3">→</span>
                    <StatusChip status={ask} />
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}

function RunAllNote({ side, ctx }: { side: SideX; ctx: Ctx }) {
  const ra = ctx.job.run_all?.[side.side];
  if (!ra) return null;
  const active = !ra.stopped;
  return (
    <div className={cx("mx-3 mt-3 flex flex-wrap items-center gap-2 rounded-lg px-3 py-2 text-[12.5px]",
      active ? "bg-info-soft text-info" : "bg-surface-2 text-ink-2")}>
      {active && <span className="h-2 w-2 rounded-full bg-info pulse-dot" />}
      <b>전체 실행</b>
      <span>{active ? `${ra.current || "다음 단계"} 실행 중 · ${ra.ran}개 끝남` : `${ra.ran}개 실행 — ${ra.stopped}`}</span>
      <span className="ml-auto text-ink-3">{ra.by} · {ago(ra.started_at)}</span>
    </div>
  );
}

// ── 다음 할 일 ────────────────────────────────────────────────────────────────

function NextAction({ side, ctx }: { side: SideX; ctx: Ctx }) {
  const running = side.stages.find((s) => s.status === "running");
  const next = side.stages.find((s) => s.key === side.next);
  const api = jobApi(ctx.job.job.id);
  const { busy, run } = useAction(ctx.reload);

  let tone = "bg-brand-soft/50 border-brand/20";
  let body: React.ReactNode;
  if (running) {
    const p = running.id != null ? ctx.progress[String(running.id)] : undefined;
    tone = "bg-info-soft/60 border-info/20";
    body = (
      <>
        <Lead icon={<span className="h-2.5 w-2.5 rounded-full bg-info pulse-dot" />} kicker="실행 중" title={running.label}
          sub={<>{p?.message ?? "시작하는 중…"}{p ? <span className="ml-2 text-ink-3">· {elapsedText(p.elapsed)}</span> : null}</>} />
        <Btn kind="outline" busy={busy === "cancel"} onClick={() => running.id != null && run("cancel", () => api.cancel(running.id!))}>중단</Btn>
      </>
    );
  } else if (!next) {
    tone = "bg-ok-soft/60 border-ok/20";
    body = <Lead icon={<Icon name="check" className="h-4 w-4 text-ok" />} kicker="완료" title="이 갈래의 모든 단계를 마쳤습니다" />;
  } else if (next.status === "pending") {
    body = (
      <>
        <Lead icon={<Icon name="play" className="h-4 w-4 text-brand" />} kicker="다음 할 일" title={`${next.label} 실행`}
          sub={next.blocked || undefined} />
        <Btn kind="primary" busy={busy === "run"} onClick={() => run("run", () => api.runNext(side.side))}>
          <Icon name="play" className="h-3.5 w-3.5" />실행
        </Btn>
      </>
    );
  } else {
    tone = next.summary.tone === "error" ? "bg-err-soft/60 border-err/20" : "bg-warn-soft/60 border-warn/20";
    body = (
      <>
        <Lead icon={<Icon name={next.summary.tone === "error" ? "alert" : "eye"} className={cx("h-4 w-4", next.summary.tone === "error" ? "text-err" : "text-warn")} />}
          kicker={next.summary.tone === "error" ? "오류 — 다시 실행하거나 되돌리기" : "검수 대기"} title={next.label}
          sub={finishHint(side.side, next, ctx.job)} />
        <FinishButtons s={next} side={side.side} ctx={ctx} primary />
      </>
    );
  }
  return <div className={cx("m-3 flex flex-wrap items-center gap-3 rounded-xl border px-4 py-3", tone)}>{body}</div>;
}

function Lead({ icon, kicker, title, sub }: { icon: React.ReactNode; kicker: string; title: string; sub?: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-1 items-start gap-3">
      <span className="mt-1 grid h-7 w-7 shrink-0 place-items-center rounded-full bg-surface shadow-sm">{icon}</span>
      <div className="min-w-0">
        <div className="text-[11.5px] font-bold uppercase tracking-wide text-ink-3">{kicker}</div>
        <div className="text-[15px] font-bold">{title}</div>
        {sub && <div className="mt-0.5 text-[12.5px] text-ink-2">{sub}</div>}
      </div>
    </div>
  );
}

function finishHint(side: string, s: StageX, job: JobX): string | undefined {
  const mode = finishModeOf(side, s.key);
  if (mode === "upload") return "검수 사본으로 실제 페이지를 교체하면 완료됩니다. 올리지 않을 거면 Skip.";
  if (mode === "rn") {
    const k = reviewKindOf(side, s.key)!;
    const r = job.reviews[k];
    const m = REVIEW_META[k];
    if (r.status === "requested") return `${m.who} 결정 대기 — ${r.requester} 요청 · ${ago(r.requested_at)}`;
    if (r.status === "rejected") return `반려됨${r.note ? ` — ${r.note}` : ""}. 선별표를 고친 뒤 다시 요청합니다.`;
    if (k === "final" && job.reviews.mid.status !== "approved") return "1차 RN 승인이 먼저 끝나야 요청할 수 있습니다.";
    return `선별표를 확인하고 ${m.who}에게 ${m.verb}을 요청하면 승인 시 완료됩니다.`;
  }
  const k = reviewKindOf(side, s.key);
  if (k === "newsletter") {
    const r = job.reviews.newsletter;
    if (r.status === "requested") return `최종 승인자 결정 대기 — ${r.requester} 요청`;
    return "편집을 마치면 결재를 요청하거나 바로 검수 완료합니다.";
  }
  return undefined;
}

// ── 검수 대기 단계를 끝내는 버튼들 ───────────────────────────────────────────────

function FinishButtons({ s, side, ctx, primary }: { s: StageX; side: string; ctx: Ctx; primary?: boolean }) {
  const api = jobApi(ctx.job.job.id);
  const { busy, run } = useAction(ctx.reload);
  const [upload, setUpload] = useState(false);
  const mode = finishModeOf(side, s.key);
  const ed = editorOf(ctx.job.job.id, side, s.key);
  const kind = reviewKindOf(side, s.key);
  const rv = kind ? ctx.job.reviews[kind] : null;
  if (s.id == null) return null;
  const sid = s.id;

  const editor = ed && (
    <Link href={ed.href} className="inline-flex items-center gap-1 rounded-lg border border-line-strong bg-surface px-3 py-2 text-[13.5px] font-semibold hover:bg-surface-2">
      {ed.label}<Icon name="chevron" className="h-3.5 w-3.5" />
    </Link>
  );
  const request = kind && rv && (rv.status === "none" || rv.status === "rejected") && (
    <Btn kind={primary ? "primary" : "outline"} busy={busy === "req"}
      disabled={kind === "final" && ctx.job.reviews.mid.status !== "approved"}
      onClick={() => run("req", () => api.reviewRequest(kind))}>
      <Icon name="stamp" className="h-3.5 w-3.5" />{REVIEW_META[kind].title} 요청
    </Btn>
  );

  return (
    <div className="flex flex-wrap items-center gap-2">
      {editor}
      {mode === "rn" && request}
      {mode === "rn" && rv?.status === "requested" && <Tag tone="warn">결정 대기</Tag>}
      {mode === "upload" && (
        <>
          <Btn kind="ghost" busy={busy === "skip"} onClick={() => run("skip", () => api.skip(sid))}>Skip</Btn>
          <Btn kind="primary" onClick={() => setUpload(true)}>업로드</Btn>
          <UploadDialog open={upload} onClose={() => setUpload(false)} jobId={ctx.job.job.id} stageId={sid} reload={ctx.reload} />
        </>
      )}
      {mode === "review" && (
        <>
          {kind === "newsletter" && request}
          {rv?.status === "requested" ? <Tag tone="warn">결정 대기</Tag> : (
            <Btn kind={primary && kind !== "newsletter" ? "ok" : "outline"} busy={busy === "done"} disabled={s.summary.tone === "error"}
              title={s.summary.tone === "error" ? "오류가 난 결과는 완료로 넘길 수 없습니다" : undefined}
              onClick={() => run("done", () => api.reviewDone(sid))}>
              <Icon name="check" className="h-3.5 w-3.5" />검수 완료
            </Btn>
          )}
        </>
      )}
    </div>
  );
}

// ── 단계 한 줄 ────────────────────────────────────────────────────────────────

function StageRow({ s, n, side, ctx, isNext, revertable }: {
  s: StageX; n: number; side: SideX; ctx: Ctx; isNext: boolean; revertable: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [dlg, setDlg] = useState<"" | "rerun" | "revert" | "rework" | "upload">("");
  const api = jobApi(ctx.job.job.id);
  const { busy, run } = useAction(ctx.reload);
  const p = s.id != null ? ctx.progress[String(s.id)] : undefined;
  const ed = editorOf(ctx.job.job.id, side.side, s.key);
  const rework = s.status === "review" && canRework(side.side, s.key, ctx.locked);
  const prevDone = side.stages.slice(0, n - 1).every((x) => x.status === "done");
  const rerun = (s.status === "review" || s.status === "done") && prevDone;
  const hasMore = s.status !== "pending" || rerun || revertable;

  const dot = { done: "bg-ok text-white", review: s.summary.tone === "error" ? "bg-err text-white" : "bg-warn text-white", running: "bg-info text-white" }[s.status]
    ?? "border border-line-strong bg-surface text-ink-3";

  return (
    <li className={cx("border-t border-line", isNext && s.status !== "running" && "bg-brand-soft/25")}>
      <div className="grid grid-cols-[30px_1fr_auto] items-center gap-x-3 px-4 py-3">
        <span className={cx("grid h-[26px] w-[26px] place-items-center rounded-full text-[11.5px] font-bold", dot)}>
          {s.status === "done" ? <Icon name="check" className="h-3.5 w-3.5" /> : s.status === "running" ? <span className="h-2 w-2 rounded-full bg-white pulse-dot" /> : s.summary.tone === "error" && s.status === "review" ? "!" : n}
        </span>
        <button onClick={() => hasMore && setOpen((v) => !v)} className={cx("min-w-0 text-left", hasMore && "cursor-pointer")}>
          <div className="flex flex-wrap items-center gap-2">
            <span className={cx("font-semibold", s.status === "pending" && "text-ink-2")}>{s.label}</span>
            <StatusChip status={s.status} />
            {s.summary.tone === "warn" && s.status !== "pending" && <Tag tone="warn">경고 {s.summary.warnings.length}</Tag>}
            {s.summary.tone === "error" && s.status !== "pending" && <Tag tone="error">오류</Tag>}
          </div>
          <div className="mt-0.5 truncate text-[12.5px] text-ink-3">
            {s.status === "running" ? <span className="text-info">{p?.message ?? "실행 중…"}{p ? ` · ${elapsedText(p.elapsed)}` : ""}</span>
              : s.status !== "pending" ? <>{s.updated_by} · {ago(s.updated_at)}{s.summary.headline ? ` — ${s.summary.headline}` : ""}</>
              : s.blocked || "대기"}
          </div>
        </button>
        <div className="flex items-center gap-1">
          {s.status === "running" && s.id != null && <Btn size="sm" busy={busy === "cancel"} onClick={() => run("cancel", () => api.cancel(s.id!))}>중단</Btn>}
          {ed && s.status !== "pending" && s.status !== "running" && (
            <Link href={ed.href} className="rounded-lg px-2 py-1 text-[12.5px] font-semibold text-brand-ink hover:bg-surface-2">{ed.label}</Link>
          )}
          {hasMore && (
            <button onClick={() => setOpen((v) => !v)} className="rounded-md p-1.5 text-ink-3 hover:bg-surface-2" aria-label="자세히">
              <Icon name="chevron" className={cx("h-4 w-4 transition-transform", open && "rotate-90")} />
            </button>
          )}
        </div>
      </div>

      {open && (
        <div className="border-t border-dashed border-line bg-surface-2/50 px-4 pb-4 pl-[58px] pt-3">
          {s.status === "review" && !isNext && <div className="mb-3"><FinishButtons s={s} side={side.side} ctx={ctx} /></div>}
          {s.status !== "pending" && <ResultSummary summary={s.summary} raw={s.result} />}
          {s.input && <div className="mt-2 text-[12px] text-ink-3">입력값: <span className="font-mono text-ink-2">{s.input.split("\n").join(" · ")}</span></div>}
          <div className="mt-3 flex flex-wrap gap-2">
            {rerun && <Btn size="sm" onClick={() => setDlg("rerun")}><Icon name="play" className="h-3 w-3" />다시 실행</Btn>}
            {rework && <Btn size="sm" onClick={() => setDlg("rework")}><Icon name="sparkle" className="h-3 w-3" />추가 지시로 재작업</Btn>}
            {revertable && <Btn size="sm" kind="ghost" className="text-err" onClick={() => setDlg("revert")}>대기로 되돌리기</Btn>}
          </div>
        </div>
      )}

      {s.id != null && <>
        <ConfirmRerun open={dlg === "rerun"} onClose={() => setDlg("")} label={s.label} status={s.status}
          onOk={() => run("rerun", () => api.run(s.id!)).then(() => setDlg(""))} busy={busy === "rerun"} />
        <RevertDialog open={dlg === "revert"} onClose={() => setDlg("")} jobId={ctx.job.job.id} stageId={s.id} label={s.label} reload={ctx.reload} />
        <ReworkDialog open={dlg === "rework"} onClose={() => setDlg("")} label={s.label}
          onOk={(t) => run("rework", () => api.rework(s.id!, t)).then((r) => r && setDlg(""))} busy={busy === "rework"} />
      </>}
    </li>
  );
}

// ── 대화상자 ──────────────────────────────────────────────────────────────────

function ConfirmRerun({ open, onClose, label, status, onOk, busy }: {
  open: boolean; onClose: () => void; label: string; status: string; onOk: () => void; busy: boolean;
}) {
  return (
    <Modal open={open} onClose={onClose} title={`${label} 다시 실행`}
      footer={<><Btn kind="ghost" onClick={onClose}>취소</Btn><Btn kind="primary" busy={busy} onClick={onOk}>다시 실행</Btn></>}>
      <p>지금 결과({status === "done" ? "완료" : "검수 대기"})를 새 실행 결과로 바꿉니다. 이 단계에 걸린 결재 상태도 처음으로 돌아갑니다.</p>
    </Modal>
  );
}

function ReworkDialog({ open, onClose, label, onOk, busy }: {
  open: boolean; onClose: () => void; label: string; onOk: (t: string) => void; busy: boolean;
}) {
  const [t, setT] = useState("");
  return (
    <Modal open={open} onClose={onClose} title={`${label} — 추가 지시로 재작업`}
      footer={<><Btn kind="ghost" onClick={onClose}>취소</Btn><Btn kind="primary" busy={busy} disabled={!t.trim()} onClick={() => onOk(t)}>이 지시로 재작업</Btn></>}>
      <p className="mb-3 text-ink-2">처음부터 다시 만들지 않고, 지금 결과에 지시만 반영합니다.</p>
      <Field label="수정 지시">
        <textarea autoFocus rows={5} value={t} onChange={(e) => setT(e.target.value)} className={inputCls}
          placeholder="예: 변경 설명을 더 간결하게 / #33127 은 제외 / 보안 패치 항목 문구 수정" />
      </Field>
    </Modal>
  );
}

function RevertDialog({ open, onClose, jobId, stageId, label, reload }: {
  open: boolean; onClose: () => void; jobId: number; stageId: number; label: string; reload: () => void;
}) {
  const api = jobApi(jobId);
  const [wipe, setWipe] = useState<string[] | null>(null);
  const [err, setErr] = useState("");
  const { busy, run } = useAction(reload);
  useEffect(() => {
    if (!open) return;
    setWipe(null); setErr("");
    jobApi(jobId).revertPreview(stageId).then((r) => setWipe(r.wipe)).catch((e) => setErr(e.message));
  }, [open, jobId, stageId]);
  return (
    <Modal open={open} onClose={onClose} title={`${label} — 대기로 되돌리기`}
      footer={<><Btn kind="ghost" onClick={onClose}>취소</Btn>
        <Btn kind="danger" busy={busy === "rv"} disabled={!!err || wipe === null}
          onClick={() => run("rv", () => api.revert(stageId)).then((r) => r && onClose())}>되돌리기</Btn></>}>
      {err ? <div className="rounded-lg bg-err-soft px-3 py-2 text-err">{err}</div>
        : wipe === null ? <div className="h-10 animate-pulse rounded-lg bg-muted-soft" />
        : (
          <>
            <p>단계를 대기로 돌리고, 이 단계가 만든 작업물을 지웁니다. 외부(Dropbox · FOTA · Portal)에 이미 올린 것은 그대로 남습니다.</p>
            <div className="mt-3 rounded-lg border border-line bg-surface-2 px-3 py-2">
              <div className="mb-1 text-[12px] font-semibold text-ink-2">지워지는 작업물 {wipe.length}가지</div>
              {wipe.length ? <div className="flex flex-wrap gap-1">{wipe.map((w) => <span key={w} className="rounded bg-surface px-1.5 py-0.5 font-mono text-[11.5px] text-ink-2">{w}</span>)}</div>
                : <div className="text-[12.5px] text-ink-3">지울 작업물이 없습니다 — 상태만 대기로 돌아갑니다.</div>}
            </div>
          </>
        )}
    </Modal>
  );
}

function UploadDialog({ open, onClose, jobId, stageId, reload }: {
  open: boolean; onClose: () => void; jobId: number; stageId: number; reload: () => void;
}) {
  const api = jobApi(jobId);
  const [plan, setPlan] = useState<UploadPlan | null>(null);
  const [out, setOut] = useState<UploadOut | null>(null);
  const [err, setErr] = useState("");
  const { busy, run } = useAction(reload);
  useEffect(() => {
    if (!open) return;
    setPlan(null); setOut(null); setErr("");
    jobApi(jobId).uploadPlan(stageId).then(setPlan).catch((e) => setErr(e.message));
  }, [open, jobId, stageId]);
  const target = plan?.source === "service_portal" ? "Service Portal" : "Knowledge Base";
  return (
    <Modal open={open} onClose={onClose} title={`${plan ? target : ""} 실제 업로드`} wide
      footer={out ? <Btn kind="primary" onClick={onClose}>닫기</Btn> : <>
        <Btn kind="ghost" onClick={onClose}>취소</Btn>
        <Btn kind="primary" busy={busy === "up"} disabled={!plan || plan.blocked || !plan.items.length}
          onClick={() => run("up", () => api.upload(stageId), (r) => r.ok ? "업로드를 마쳤습니다" : undefined).then((r) => r && setOut(r))}>
          {plan ? `${plan.items.length}건 업로드` : "업로드"}
        </Btn></>}>
      {err && <div className="rounded-lg bg-err-soft px-3 py-2 text-err">{err}</div>}
      {!err && !plan && <div className="h-16 animate-pulse rounded-lg bg-muted-soft" />}
      {plan && !out && (
        <>
          <p className="mb-3 text-ink-2">검수한 사본으로 실제 페이지 본문을 교체합니다{plan.items.some((i) => i.new) ? " · 새 문서는 만듭니다" : ""}. 모두 성공하면 이 단계가 완료됩니다.</p>
          {plan.items.length ? (
            <ul className="divide-y divide-line rounded-xl border border-line">
              {plan.items.map((it) => (
                <li key={String(it.doc_id)} className="flex items-center gap-2 px-3 py-2">
                  <Tag tone={it.new ? "ok" : "none"}>{it.new ? "새 문서" : "교체"}</Tag>
                  <span className="min-w-0 flex-1 truncate">{it.title}</span>
                  <span className="font-mono text-[12px] text-ink-3">#{it.doc_id}</span>
                </li>
              ))}
            </ul>
          ) : <div className="rounded-lg bg-warn-soft px-3 py-2 text-warn">올릴 검수 사본이 없습니다. Submission 쪽 편집 단계 결과가 필요합니다.</div>}
          {plan.clash.length > 0 && (
            <div className={cx("mt-3 rounded-lg px-3 py-2 text-[13px]", plan.blocked ? "bg-err-soft text-err" : "bg-warn-soft text-warn")}>
              <div className="font-semibold">{plan.blocked ? "같은 페이지를 고치는 다른 작업이 있어 올릴 수 없습니다" : "같은 문서를 고치는 다른 작업이 있습니다"}</div>
              {plan.clash.map((c, i) => <div key={i}>· {c.title || `#${c.doc_id}`} ← 작업 {String(c.other)}</div>)}
            </div>
          )}
        </>
      )}
      {out && (
        <ul className="space-y-1.5">
          {out.results.map((r, i) => (
            <li key={i} className={cx("rounded-lg px-3 py-2 text-[13px]", r.ok ? "bg-ok-soft text-ok" : "bg-err-soft text-err")}>
              <b>{r.ok ? "✔" : "✘"} #{r.doc_id}</b> {r.message}
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}
