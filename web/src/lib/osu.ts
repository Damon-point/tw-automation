"use client";

import { api } from "./api";

// ── OS Update 편집기 API ──────────────────────────────────────────────────────

export const post = <T = { ok: boolean; message?: string }>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const del = <T = { ok: boolean; message?: string }>(path: string) => api<T>(path, { method: "DELETE" });

export type RoundPlan = {
  product: { id: number; product: string; android: string; os_type: string };
  version: string; state: string; unknown: boolean; basis: string; why: string; published_at: string;
  mails: { mail_id: number; subject: string; date: string; version: string; origin: boolean }[];
  sides: { side: string; label: string; stages: string[] }[];
  job_id: number | null;
};

export type NlCard = { topic: string; title_ko: string; body_ko: string; title_en: string; body_en: string };
export type Newsletter = {
  draft: null | {
    subject: string; product_disp: string; os_version_disp: string; sdk: string; rn_link: string;
    cards: NlCard[]; sent: boolean; sent_at: string; campaign_id: string; updated_at: string;
  };
  preview?: { kr: string; en: string };
  topics?: Record<string, string>;
  review?: string;
};
export type NlTag = { key: string; name: string; members: number; list_name: string };

export type HistoryEntry = {
  version: number; event: string; title: string; actor: string; summary: string; ts: string;
  add: number; del: number; first: boolean; diff: [string, string][];
};

export type PortalItem = {
  key: string; title: string; doc_id: string; html: string; original: string; updated_at: string;
  applied: { why?: string; old?: string; new?: string }[]; failed: { reason?: string; old?: string }[];
  error: string; new_doc: boolean; others: string[];
};

export type KbSection = { title: string; new: string[]; improved: string[]; fixed: string[] };
export type KbItem = {
  key: string; title: string; doc_id: string; new_page: boolean; ambiguous: string; others: string[]; error: string;
  vfull: string; sdk: string; security: string; security_date: string; sections: KbSection[];
  storage: string; preview: string; updated_at: string;
};

export type Fota = { headers: { osinfo: string[]; dp: string[] }; osinfo: string[][]; dp: string[][] };
export type FotaPlan = {
  ok: boolean; error: string; new_osinfo: { os_version: string; version: string }[]; new_dp: number;
  dup_osinfo: { id: string; version: string; os_version: string }[];
  dup_dp: { id: string; os_version: string; type: string }[];
  total_osinfo: number; total_dp: number;
};
export type FotaReport = { ok: boolean; mode: string; report: string; recorded_osinfo?: number; recorded_dp?: number };

export type DbxGroup = { key: string; label: string; base: string; files: string[]; skipped: string; unsafe: boolean };

export const KB_BUCKETS = [
  { key: "new", label: "NEW", color: "rgb(255,102,0)" },
  { key: "improved", label: "IMPROVED", color: "rgb(51,102,255)" },
  { key: "fixed", label: "FIXED", color: "rgb(51,153,102)" },
] as const;

export function errText(e: unknown) {
  return e instanceof Error ? e.message : String(e);
}
