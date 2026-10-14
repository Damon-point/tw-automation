"use client";

import { useState } from "react";
import { Icon } from "@/components/Icons";
import { Card, Tag, cx } from "@/components/ui";
import { ago } from "@/lib/api";
import { REVIEW_META, SUB, jobApi, type JobX, type ReviewKind } from "@/lib/job";
import { Btn, inputCls, useAction } from "./kit";

const STATUS: Record<string, { label: string; tone: "ok" | "warn" | "error" | "none" }> = {
  none: { label: "요청 전", tone: "none" },
  requested: { label: "결정 대기", tone: "warn" },
  approved: { label: "승인", tone: "ok" },
  rejected: { label: "반려", tone: "error" },
};

/** 결재 세 가지 — 요청 · 결정(권한자) · 이력을 한 카드에서 */
export function ReviewsCard({ job, reload }: { job: JobX; reload: () => void }) {
  const stageOf = (k: ReviewKind) => {
    const key = k === "mid" ? "release_note_full" : k === "final" ? "release_note" : "newsletter";
    return job.sides.find((s) => s.side === SUB)?.stages.find((s) => s.key === key);
  };
  return (
    <Card title="결재" icon={<Icon name="stamp" />} pad={false}>
      {(["mid", "final", "newsletter"] as ReviewKind[]).map((k) => (
        <ReviewRow key={k} kind={k} job={job} stageStatus={stageOf(k)?.status ?? "pending"} reload={reload} />
      ))}
    </Card>
  );
}

function ReviewRow({ kind, job, stageStatus, reload }: { kind: ReviewKind; job: JobX; stageStatus: string; reload: () => void }) {
  const r = job.reviews[kind];
  const meta = REVIEW_META[kind];
  const st = STATUS[r.status] ?? { label: r.status, tone: "none" as const };
  const api = jobApi(job.job.id);
  const { busy, run } = useAction(reload);
  const [note, setNote] = useState("");
  const [deciding, setDeciding] = useState(false);

  const blockedByMid = kind === "final" && job.reviews.mid.status !== "approved";
  const canRequest = (r.status === "none" || r.status === "rejected") && stageStatus === "review" && !blockedByMid;
  const why = r.status === "none" || r.status === "rejected"
    ? stageStatus === "done" ? "결재 없이 완료됨"
      : stageStatus !== "review" ? "단계 결과가 나오면 요청할 수 있습니다"
      : blockedByMid ? "1차 RN 승인 뒤에 요청합니다" : ""
    : "";

  return (
    <div className="border-b border-line px-5 py-3.5 last:border-b-0">
      <div className="flex items-center gap-2">
        <span className={cx("h-2 w-2 rounded-full", { ok: "bg-ok", warn: "bg-warn", error: "bg-err", none: "bg-line-strong" }[st.tone])} />
        <span className="flex-1 font-semibold">{meta.title}</span>
        <Tag tone={st.tone}>{st.label}</Tag>
      </div>
      <div className="mt-0.5 pl-4 text-[12px] text-ink-3">{meta.target} · {meta.who}</div>

      {(r.requester || r.decided_by) && (
        <div className="mt-2 space-y-0.5 pl-4 text-[12.5px] text-ink-2">
          {r.requester && <div>요청 {r.requester} · {ago(r.requested_at)}</div>}
          {r.decided_by && <div>{r.status === "approved" ? "승인" : "반려"} {r.decided_by} · {ago(r.decided_at)}</div>}
          {r.note && <div className={cx("mt-1 rounded-md px-2 py-1", r.status === "rejected" ? "bg-err-soft text-err" : "bg-surface-2")}>“{r.note}”</div>}
        </div>
      )}

      <div className="mt-2.5 pl-4">
        {canRequest && (
          <Btn size="sm" kind="primary" busy={busy === "req"} onClick={() => run("req", () => api.reviewRequest(kind))}>
            {r.status === "rejected" ? "다시 요청" : `${meta.who}에게 ${meta.verb} 요청`}
          </Btn>
        )}
        {why && <div className="text-[12px] text-ink-3">{why}</div>}
        {r.status === "requested" && job.can_decide[kind] && (
          deciding ? (
            <div className="space-y-2">
              <textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} className={inputCls}
                placeholder="메모 (반려할 때는 사유 필수)" />
              <div className="flex gap-2">
                <Btn size="sm" kind="ok" busy={busy === "ok"} onClick={() => run("ok", () => api.reviewDecide(kind, true, note)).then((x) => x && setDeciding(false))}>승인</Btn>
                <Btn size="sm" kind="danger" busy={busy === "no"} disabled={!note.trim()} onClick={() => run("no", () => api.reviewDecide(kind, false, note)).then((x) => x && setDeciding(false))}>반려</Btn>
                <Btn size="sm" kind="ghost" onClick={() => setDeciding(false)}>취소</Btn>
              </div>
            </div>
          ) : <Btn size="sm" kind="primary" onClick={() => setDeciding(true)}>결정하기</Btn>
        )}
        {r.status === "requested" && !job.can_decide[kind] && <div className="text-[12px] text-ink-3">{meta.who}의 결정을 기다리는 중</div>}
      </div>
    </div>
  );
}
