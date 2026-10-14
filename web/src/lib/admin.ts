"use client";

import { ApiError, api } from "./api";

/** JSON 으로 보내고 받는다 — 실패하면 서버가 준 사유를 그대로 던진다 */
export function send<T = { ok: boolean; message?: string }>(path: string, body?: unknown, method = "POST") {
  return api<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
}

/** 파일 올리기 — Content-Type 은 브라우저가 경계값과 함께 정한다 */
export async function upload<T>(path: string, files: File[] | File, field = "files"): Promise<T> {
  const fd = new FormData();
  for (const f of Array.isArray(files) ? files : [files]) fd.append(field, f);
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

// ── 응답 타입 ────────────────────────────────────────────────────────────────

export type CredRow = { target: string; label: string; username: string; has_password: boolean; updated_at: string; base_url?: string; host?: string };

export type SettingsOverview = {
  is_admin: boolean;
  shared_mailbox: { email: string; has_password: boolean; updated_by: string; server: string };
  sender: {
    relay_owner: boolean; relay: boolean; my_address: string; system_from: string; system_from_name: string;
    email: string; has_password: boolean; updated_at: string;
  };
  web: CredRow[];
  smb: CredRow[];
  dropbox: { mode: "refresh" | "legacy" | "none"; has_app_key: boolean; updated_at: string };
  mailchimp: { has_key: boolean; server_prefix: string; from_name: string; reply_to: string; subject_default: string; updated_at: string };
  claude: { backend: "subscription" | "api"; claude: string; has_key: boolean };
};

export type ShadowWrite = { at: string; system: string; action: string; [k: string]: unknown };
export type ShadowResp = {
  live: boolean;
  writes: ShadowWrite[];
  runtime: { started_at: string; loops: Record<string, { ok: boolean; at: string; error?: string }> };
};

export type Account = { id: number; username: string; role: string; must_change: boolean; email: string; me: boolean };
export type AccountsResp = { roles: Record<string, string>; users: Account[]; system_from: string; system_from_name: string; initial_password: string };

export type StaffRow = {
  id: number; name: string; email: string; status: string; is_dqa: boolean; is_handler: boolean; team: string; memo: string; updated_by?: string;
};

export type MailTpl = {
  key: string; title: string; group: string; when: string; to: string; subject: string; body: string;
  vars: Record<string, string>; auto: Record<string, string>; default_subject: string; default_body: string;
  edited: boolean; updated_by: string; updated_at: string;
};

export type FileTpl = { key: string; name: string; label: string; ext: string; exists: boolean; size: number; mtime: string };

export type UsageBucket = { cost: number; tokens: number; calls: number };
export type UsageResp = {
  totals: { today: UsageBucket; month: UsageBucket; all: UsageBucket };
  by_day: ({ day: string } & UsageBucket)[];
  by_month: ({ month: string } & UsageBucket)[];
  by_operation: ({ operation: string } & UsageBucket)[];
  recent: { ts: string; model: string; operation: string; input_tokens: number; output_tokens: number; cost_usd: number }[];
  scope: string;
};

export type Field = { key: string; label: string; placeholder: string };
export type ProductMeta = {
  categories: string[]; category_hint: string; types: string[];
  key_fields: Field[]; info_fields: Field[]; path_groups: { group: string; fields: Field[] }[];
};
export type Release = { version: string; docx_name: string; pdf_name: string; has_docx: boolean; has_pdf: boolean };
export type ProductFull = {
  id: number; product: string; android: string; os_type: string; category: string; initial_os_version: string;
  created_by: string; updated_by: string; created_at: string; updated_at: string;
  filled: number; total: number; missing: string[]; releases: Release[]; release_count: number;
  [k: string]: unknown;
};

export function pval(p: ProductFull, key: string): string {
  const v = p[key];
  return typeof v === "string" ? v : v == null ? "" : String(v);
}
export type ProductGroups = { groups: { category: string; items: ProductFull[] }[] };

export type MailDetail = {
  id: number; subject: string; sender: string; received_at: string; to: string; cc: string;
  html: string; text: string; attachments: { name: string; available: boolean; size: number }[]; serials: string[];
};

export function usd(v: number) {
  v = Number(v || 0);
  return v >= 1 ? `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : `$${v.toFixed(4)}`;
}

export function num(v: number) {
  return Math.round(Number(v || 0)).toLocaleString();
}

export function bytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
