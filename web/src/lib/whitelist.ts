"use client";

import { ApiError, api } from "./api";

// ── 메일함 ──────────────────────────────────────────────────────────────────
export type ThreadKind = "simple" | "organic" | "dqa" | "none";
export type ThreadState = "open" | "done" | "notwl" | "reopened";

export type ThreadSummary = {
  tid: string; subject: string; sender: string; when: string; count: number;
  serial_count: number; dup_count: number; attachments: string[];
  judged: boolean; model: string; from_os: string; to_os: string; reason: string;
  state: ThreadState; processed: boolean; kind: ThreadKind; snippet: string;
};
export type Dup = { request_id: string; created_at: string; status: string } | null;
export type ThreadDetail = ThreadSummary & {
  is_admin: boolean; addr: string; name: string; lang: "ko" | "en"; auto_category: string;
  serials: { sn: string; dup: Dup }[];
  members: { id: number; subject: string; sender: string; when: string; body: string; attachments: string }[];
};

export type UploadRow = { sn: string; api: string; end: string; pn: string; ostype: string; status: string; excluded?: boolean };
export type FotaBrief = { ok: boolean; total: number; success: number; msg: string; dry: boolean; fails: { sn?: string; msg?: string }[] };
export type VerifyItem = { sn: string; endDate?: string; ostype?: string; api?: string };
export type VerifyRow = { sn: string; found: boolean; date: string; osType: string; androidApi: string; diffs: string[]; ok: boolean };
export type VerifyResult = {
  ok: boolean; total: number; matched: number; missing: number; mismatched: number; error: string;
  rows: VerifyRow[]; hidden: number;
};
/** 회신 문구에 들어간 두 사람 — source: roster(사내 인원 명부) · learned(지난 메일) · fallback(영문 이름) · none */
export type ReplyPerson = { email: string; name_ko: string; source: string; in_roster: boolean };
export type Draft = {
  to: string; cc: string; subject: string; body: string;
  people?: {
    to: ReplyPerson & { name: string; greeting: string; title: string };
    me: ReplyPerson & { intro: string; team: string };
  };
};

// ── AI 해석 ─────────────────────────────────────────────────────────────────
export type OrgView = {
  type: "" | "date" | "new" | "mixed" | "unknown"; type_label: string; reason: string; date: string; note: string;
  all_sns: string[]; new_sns: string[]; auto_category: string;
  blocks: { index: number; source: string; serials: number; plan: { type?: string; model?: string; from_os?: string; to_os?: string; date?: string; note?: string } }[];
};
/** count · valid 는 체크한(보낼) 것만, all · sns 는 체크를 푼 것까지 */
export type OrgModel = { model: string; count: number; valid: number; all: number; sns: string[]; rules: { from: number; to: number; ostype: string }[]; rows: UploadRow[] };
export type OrgPreview = {
  category: string; total: number; looked_up: number; error: string; models: OrgModel[];
  unknown: { sn: string; model: string; os: string; ostype: string; status: string; excluded?: boolean }[]; unknown_total: number;
  ready: number; rule_models: string[]; excluded: number;
};
export type OrgRun = {
  dry: boolean; ok: boolean; success: number; fails: { sn?: string; msg?: string }[]; fail_count?: number;
  invalid: number; reasons: string[]; label: string; total?: number; verify_items: VerifyItem[]; message?: string;
  request?: { id: string };
};

// ── DQA ─────────────────────────────────────────────────────────────────────
export type DqaJob = {
  state: string; running: boolean; ratio: number; frac: number; headline: string; detail: string;
  speed: string; eta: string; elapsed: string; message: string; logs: string[]; restored: boolean;
};
export type DqaView = {
  parsed: {
    product: string; android: string; sns: string[];
    froms: { dtype: string; version: string; path: string }[];
    to: { version: string; path: string; full_pfu: string; diff_pfu: string } | null;
  };
  plan: {
    os_type: string; latest: number; product: string; android: string; api: number | null; end_date: string; note: string;
    errors: string[]; warnings: string[]; version_prefix: string; version_prefix_src: string;
    oi_rows: { osType: string; version: string; osVersion: number; description: string }[];
    dp_rows: { osVersion: number; downloadType: string; fileNm: string; size: string; downloadUrl: string; dummy: boolean }[];
    uploaded: boolean;
  };
  osinfo_ok: boolean; verify_items: VerifyItem[]; whitelist_rows: UploadRow[]; job: DqaJob | null;
};

// ── 신청 · 내역 · 규칙 · 포털 ──────────────────────────────────────────────────
export type Rule = { id: number; category: string; model: string; from: number; to: number; ostype: string; api?: number | null };
export type Device = {
  sn: string; cur: string; upg: string; model: string; api: number | null; ostype: string | null;
  category: string; endDate: string; errors: string[]; valid: boolean;
};
export type Options = {
  models: string[]; os_levels: number[]; categories: string[]; default_category: string; end_date: string;
  api: Record<string, number>; rules: Rule[]; is_admin: boolean; email: string;
};
export type WLRequest = {
  id: string; userName: string; userId: string; team: string; createdAt: string; status: string; statusLabel: string;
  memo: string; processedAt: string; processedBy: string; fotaStatus: string; chargeType: string; category: string;
  amount: string; poNumber: string; poFilePath: string; poFileName: string; freeReason: string; companyId: number | null;
  devices: { sn: string; api: string; endDate: string; ostype: string; model: string }[];
};
export type Company = { id: number; name: string; email_domain: string; active: boolean; accounts: number; requests: number };
export type Account = {
  id: number; company_id: number; username: string; email: string; status: string; status_label: string;
  must_change: boolean; last_login_at: string;
};

// ── 도구 ────────────────────────────────────────────────────────────────────
export function post<T>(path: string, body?: unknown) {
  return api<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });
}
export function del<T>(path: string) {
  return api<T>(path, { method: "DELETE" });
}

/** 파일 업로드(multipart) — api() 는 JSON 머리를 붙이므로 따로 둔다 */
export async function upload<T>(path: string, file: File): Promise<T> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(path, { method: "POST", body: fd, credentials: "same-origin" });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).error ?? msg; } catch {}
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export function errText(e: unknown) {
  return e instanceof Error ? e.message : String(e);
}

/** 등록일 변경으로 DB 에 들어갈 값 — 등록일 01:01:01 · 지원 종료일 = 등록일 + 1년 */
export function dateRows(sns: string[], d: string) {
  const ok = /^\d{4}-\d{2}-\d{2}$/.test(d.trim());
  const end = ok ? `${Number(d.slice(0, 4)) + 1}${d.slice(4)}` : "(날짜 형식 오류)";
  return sns.map((sn) => ({ sn, reg: ok ? `${d} 01:01:01` : "(날짜 형식 오류)", end }));
}

export const STATE_LABEL: Record<ThreadState, string> = { open: "처리 전", done: "처리 후", notwl: "작업 아님", reopened: "새 회신" };
export const KIND_LABEL: Record<ThreadKind, string> = { simple: "등록", organic: "AI 해석", dqa: "DQA FOTA", none: "SN 없음" };
export const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
