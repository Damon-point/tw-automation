"use client";

import { api, type JobDetail, type JobSide, type JobStage } from "./api";

// ── 응답 타입 (작업 상세의 쓰기용 필드) ─────────────────────────────────────────

export type ReviewKind = "mid" | "final" | "newsletter";
export type ReviewState = {
  status: "none" | "requested" | "approved" | "rejected" | string;
  requester: string; requested_at: string; decided_by: string; decided_at: string; note: string;
};
export type StageX = JobStage & { id: number | null; input: string };
export type SideX = Omit<JobSide, "stages"> & { stages: StageX[] };
export type ProgressInfo = { message: string; elapsed: number; here: boolean };
export type JobX = Omit<JobDetail, "sides"> & {
  sides: SideX[];
  reviews: Record<ReviewKind, ReviewState>;
  progress: Record<string, ProgressInfo>;
  can_decide: Record<ReviewKind, boolean>;
  run_all: Record<string, RunAllState>;
};

export type ActResult = { ok?: boolean; message?: string; notes?: string[] };

export type UploadPlan = {
  source: "service_portal" | "release_notes";
  items: { doc_id: string | number; title: string; new: boolean }[];
  clash: { doc_id: string | number; title: string; other: string | number }[];
  blocked: boolean;
};
export type UploadOut = { ok: boolean; results: { doc_id: string; ok: boolean; message: string }[] };

export type SelRow = {
  no: number | string; issue: string; team?: string; customer?: string; gubun?: string;
  include: "Y" | "X"; reason?: string; public?: string; redmine?: string; sensitive?: boolean;
  apply_note: string; exclude_note?: string; kind?: string; manual?: boolean;
};
export type Selection = {
  rows: SelRow[]; edited_at: string; edited_by: string; mid: string; final: string;
  with_full: boolean; locked: boolean; exclude_notes: string[];
  inputs: { fields: InputField[]; editable: boolean; now_version: string };
};
/** 선별표의 입력 값 칸 — key 가 빈 것은 최초 릴리즈에서 새로 더한 항목 */
export type InputField = { key: string; label: string; required: boolean; value: string; manual: boolean; version: string };
export type RunAllState = { by: string; started_at: string; ran: number; stopped: string; current: string };

export type Inputs = {
  os_version: { value: string; inherited: string; editable: boolean };
  components: { fields: { key: string; label: string; value: string }[]; editable: boolean };
  revision_draft: { value: string; editable: boolean };
  rn_link: { manual: string; auto: string };
};

// ── 단계별 규칙 (api/osu_actions.py · 기존 작업 보드와 같다) ─────────────────────

export const SUB = "submission";
export const APP = "approval";

const RN_STAGES = new Set(["release_note_full", "release_note"]);
const UPLOAD_STAGES = new Set(["service_portal", "release_note_edit"]);
const REWORK_STAGES = new Set(["release_note_full", "service_portal", "release_note_edit", "newsletter"]);

/** 이 단계의 결과를 손보는 화면(편집기). 없으면 null */
export function editorOf(jobId: number | string, side: string, key: string): { href: string; label: string } | null {
  const base = `/jobs/${jobId}`;
  switch (key) {
    case "release_note_full":
    case "release_note":
      return { href: `${base}/selection`, label: "선별표 편집" };
    case "release_note_edit":
      return { href: `${base}/kb`, label: side === APP ? "KB 사본 보기" : "KB 편집" };
    case "service_portal":
      return { href: `${base}/portal`, label: "Service Portal 편집" };
    case "excel_docs":
      return { href: `${base}/fota`, label: "FOTA 등록" };
    case "newsletter":
      return { href: `${base}/newsletter`, label: side === APP ? "Newsletter 발송" : "Newsletter 편집" };
    case "dropbox_cleanup":
      return { href: `${base}/dropbox`, label: "구버전 정리" };
    default:
      return null;
  }
}

/** 검수 대기 단계를 끝내는 방법 */
export function finishModeOf(side: string, key: string): "rn" | "upload" | "review" {
  if (RN_STAGES.has(key)) return "rn";
  if (side === APP && UPLOAD_STAGES.has(key)) return "upload";
  return "review";
}

export function canRework(side: string, key: string, rnLocked: boolean): boolean {
  if (!REWORK_STAGES.has(key)) return false;
  if (side === APP && (key === "release_note_edit" || key === "newsletter")) return false; // Submission 승인본을 그대로 쓴다
  if (key === "release_note_full" && rnLocked) return false;
  return true;
}

/** 이 단계에 걸린 결재 */
export function reviewKindOf(side: string, key: string): ReviewKind | null {
  if (key === "release_note_full") return "mid";
  if (key === "release_note") return "final";
  if (key === "newsletter" && side === SUB) return "newsletter";
  return null;
}

export const REVIEW_META: Record<ReviewKind, { title: string; target: string; who: string; verb: string }> = {
  mid: { title: "RN 승인 (1차)", target: "전체 이력 Release Note", who: "RN 승인자", verb: "승인" },
  final: { title: "최종 검수 (2차)", target: "Release Note for Sales", who: "최종 승인자", verb: "검수" },
  newsletter: { title: "Newsletter 결재", target: "Newsletter", who: "최종 승인자", verb: "검수" },
};

/** 갈래에서 되돌릴 수 있는 단계 — 순서상 마지막으로 손댄(대기 아닌) 단계 하나 */
export function revertableId(side: SideX): number | null {
  const touched = side.stages.filter((s) => s.status !== "pending" && s.id != null);
  const last = touched[touched.length - 1];
  return last && last.status !== "running" ? last.id : null;
}

export function rnLocked(j: JobX): boolean {
  return ["requested", "approved"].includes(j.reviews.mid.status) || ["requested", "approved"].includes(j.reviews.final.status);
}

// ── 호출 ────────────────────────────────────────────────────────────────────

export function post<T = ActResult>(path: string, body?: unknown): Promise<T> {
  return api<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });
}

export const jobApi = (id: number | string) => ({
  runNext: (side: string) => post(`/api/jobs/${id}/run-next`, { side }),
  run: (stage_id: number) => post(`/api/jobs/${id}/run`, { stage_id }),
  cancel: (stage_id: number) => post(`/api/jobs/${id}/cancel`, { stage_id }),
  revertPreview: (stage_id: number) => api<{ wipe: string[] }>(`/api/jobs/${id}/revert-preview?stage_id=${stage_id}`),
  revert: (stage_id: number) => post<ActResult & { wiped: string[] }>(`/api/jobs/${id}/revert`, { stage_id }),
  reviewDone: (stage_id: number) => post(`/api/jobs/${id}/review-done`, { stage_id }),
  skip: (stage_id: number) => post(`/api/jobs/${id}/skip`, { stage_id }),
  rework: (stage_id: number, text: string) => post(`/api/jobs/${id}/rework`, { stage_id, text }),
  uploadPlan: (stage_id: number) => api<UploadPlan>(`/api/jobs/${id}/upload-plan?stage_id=${stage_id}`),
  upload: (stage_id: number) => post<UploadOut>(`/api/jobs/${id}/upload`, { stage_id }),
  setState: (key: string, value: string) => post(`/api/jobs/${id}/state`, { key, value }),
  selection: () => api<Selection>(`/api/jobs/${id}/selection`),
  saveSelection: (rows: SelRow[], inputs?: { key: string; label: string; value: string }[]) =>
    post<ActResult & { lines: number; korean: string[] }>(`/api/jobs/${id}/selection`, { rows, inputs }),
  runAll: (side: string) => post(`/api/jobs/${id}/run-all`, { side }),
  bulkStatus: (side: string, status: "done" | "pending") => post<ActResult & { changed: number }>(`/api/jobs/${id}/bulk-status`, { side, status }),
  excludeNote: (no: string | number, value: string) => post(`/api/jobs/${id}/exclude-note`, { no: String(no), value }),
  reviewRequest: (kind: ReviewKind) => post(`/api/jobs/${id}/reviews/${kind}/request`),
  reviewDecide: (kind: ReviewKind, approve: boolean, note: string) =>
    post(`/api/jobs/${id}/reviews/${kind}/decide`, { approve, note }),
  inputs: () => api<Inputs>(`/api/jobs/${id}/inputs`),
  setOsVersion: (version: string) => post(`/api/jobs/${id}/inputs/os-version`, { version }),
  setComponents: (fields: Record<string, string>) => post(`/api/jobs/${id}/inputs/components`, { fields }),
  setRevisionDraft: (text: string) => post(`/api/jobs/${id}/inputs/revision-draft`, { text }),
});

export function elapsedText(sec: number) {
  if (sec < 60) return `${sec}초`;
  const m = Math.floor(sec / 60);
  return m < 60 ? `${m}분 ${sec % 60}초` : `${Math.floor(m / 60)}시간 ${m % 60}분`;
}
