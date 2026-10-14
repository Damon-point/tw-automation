// Solution Buffer(/issues) 응답 타입과 표시 도우미

export type IssueOverview = {
  me: string; admin: boolean; sb_open: number;
  sync: { issues: number; journals: number; detail_pending: number; backfill: string; last_poll: string; last_error: string; running: boolean };
  version: number[];
  sync_result: { ok?: boolean; message?: string; at?: number };
};

export type IssueRow = {
  id: number; status: string; subject: string; assignee: string; author: string; category: string;
  tracker: string; priority: string; created: string; updated: string;
  is_test: boolean; excluded: boolean; excluded_reason: string; url: string;
};
export type IssueList = { total: number; rows: IssueRow[]; counts: [string, number][] };

export type TimelineEvent = { at: string; who: string; kind: string; creation: boolean; new_status: string; summary: string; note_html: string };
export type Timeline = {
  events: TimelineEvent[];
  metrics: {
    first_action_days: number | null; first_action_kind: string; first_action_by: string;
    lead_days: number | null; reopened: number; participants: string[]; comments: number;
  };
};

export type SbDoc = {
  id: number; issue_id: number; revision: number; state: string; state_label: string; origin: string;
  superseded: boolean; title: string; disclosure: string; reason: string; applies_to: string;
  description_html: string; partners: string[]; labels: string[]; assignee: string; email: string;
  error: string; url: string; page_id: string; resolved_at: string; generated_at: string;
  notified_at: string; uploaded_at: string; decided_by: string; in_tok: number; out_tok: number;
  subject: string; issue_status: string; issue_url: string;
  generating: boolean; gen_result: { ok: boolean; error?: string; title?: string } | null;
};
export type SbList = {
  total: number; docs: SbDoc[]; me: string; admin: boolean; scope: string;
  counts: Record<string, number>; spent_usd: number; generating: boolean;
  states: Record<string, string>; active: string[]; hidden: string[];
  templates: Record<string, { ko: string; name: string; labels: string[] }>;
  default_labels: Record<string, string[]>;
  partners: Record<string, string>; known_labels: string[];
};
export type SbPreview = { ok: boolean; title: string; labels: string[]; html: string; exists: { id: string; title: string; url: string } | null };

export type Hist = { labels: string[]; counts: number[]; n: number };
export type IssueStats = {
  summary: {
    total: number; open: number; new_30d: number; resolved_30d: number;
    mean_lead: number | null; median_lead: number | null; mean_first: number | null; median_first: number | null;
    reopen_rate: number; lead_measured: number; action_measured: number;
  };
  aging: { days: number; id: number; status: string; subject: string; assignee: string; url: string }[];
  monthly: { labels: string[]; created: number[]; resolved: number[] };
  lead_hist: Hist; first_hist: Hist;
  by_status: { name: string; value: number }[];
  by_tag: { name: string; value: number }[];
  by_assignee: { name: string; total: number; open: number; resolved: number; mean_lead: number | null; mean_action: number | null }[];
  first_action: { key: string; name: string; value: number; mean_days: number | null; median_days: number | null; what: string }[];
  no_action: number;
  composition: {
    labels: string[]; groups: string[]; series: Record<string, number[]>; n: number; basis: string; months: number; top: number;
    rows: { name: string; total: number; open: number; lead: number | null; inside: string }[];
  };
  bases: Record<string, string>;
};

export type SbSettings = {
  sb_auto_notify: boolean; sb_auto_generate: boolean; sb_per_tick: number; sb_cutoff: string;
  sb_assignees: string; exclude_keywords: string; without_account: { name: string; count: number }[];
};

/** Redmine 상태 → 화면 톤. 손이 필요한 상태일수록 진하다. */
export const ISSUE_STATUS_TONE: Record<string, string> = {
  open: "bg-err-soft text-err",
  reopen: "bg-warn-soft text-warn",
  inprogress: "bg-info-soft text-info",
  monitoring: "bg-[#efe9fb] text-[#6d3fc0] dark:bg-[#2a2140] dark:text-[#b9a2f0]",
  holding: "bg-muted-soft text-ink-2",
  resolved: "bg-ok-soft text-ok",
  closed: "bg-muted-soft text-ink-3",
  rejected: "bg-muted-soft text-ink-3",
};
export function statusTone(s: string) {
  return ISSUE_STATUS_TONE[(s || "").toLowerCase().replace(/\s/g, "")] ?? "bg-muted-soft text-ink-2";
}

/** SB 문서 상태 → 톤 */
export const SB_STATE_TONE: Record<string, string> = {
  queued: "bg-warn-soft text-warn",
  draft: "bg-info-soft text-info",
  notified: "bg-[#ebeafe] text-[#4f46e5] dark:bg-[#24223f] dark:text-[#a5a1f5]",
  approved: "bg-ok-soft text-ok",
  uploaded: "bg-ok-soft text-ok",
  error: "bg-err-soft text-err",
  discarded: "bg-muted-soft text-ink-3",
  backlog: "bg-muted-soft text-ink-3",
  not_needed: "bg-muted-soft text-ink-3",
  not_target: "bg-muted-soft text-ink-3",
};
export const DISCLOSURE_TONE: Record<string, string> = {
  internal: "border-err/40 text-err",
  public: "border-ok/40 text-ok",
  partner: "border-info/40 text-info",
  ascp: "border-[#8b5cf6]/40 text-[#7c3aed] dark:text-[#b9a2f0]",
};

/** 업무일 → 사람이 읽는 길이. 하루 미만은 시간으로. */
export function dur(days: number | null | undefined) {
  if (days === null || days === undefined) return "—";
  return days < 1 ? `${(days * 24).toFixed(1)}시간` : `${days.toFixed(1)}일`;
}

/** KST ISO → 'YYYY-MM-DD HH:MM' */
export function kst(iso: string) {
  if (!iso) return "";
  return iso.slice(0, 16).replace("T", " ");
}
