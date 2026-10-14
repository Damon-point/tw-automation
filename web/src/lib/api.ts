"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    ...init,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      msg = (await res.json()).error ?? msg;
    } catch {}
    if (res.status === 401 && typeof window !== "undefined" && !location.pathname.startsWith("/login")) {
      location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
    }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

/** 데이터를 가져오고, refreshMs 를 주면 그 간격으로 다시 가져온다(창이 보일 때만). */
export function useApi<T>(path: string | null, refreshMs?: number | ((d: T | null) => number | undefined)) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const dataRef = useRef<T | null>(null);

  const load = useCallback(async () => {
    if (!path) return;
    try {
      const d = await api<T>(path);
      dataRef.current = d;
      setData(d);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = () => {
      const ms = typeof refreshMs === "function" ? refreshMs(dataRef.current) : refreshMs;
      if (!ms) return;
      timer = setTimeout(async () => {
        if (document.visibilityState === "visible") await load();
        tick();
      }, ms);
    };
    tick();
    return () => clearTimeout(timer);
  }, [load, refreshMs]);

  return { data, error, loading, reload: load };
}

// ── 응답 타입 ────────────────────────────────────────────────────────────────

export type Tone = "ok" | "warn" | "error" | "none";
export type Summary = { tone: Tone; headline: string; errors: string[]; warnings: string[]; ok_count: number };
export type Me = { id: number; username: string; role: string; legacy_url: string };

export type StageItem = {
  job_id: number; product: string; android: string; os_type: string; version: string;
  side: string; stage_key: string; label: string; updated_by: string; updated_at: string; summary?: Summary;
};
export type Approval = {
  job_id: number; product: string; android: string; version: string; kind: string;
  requested_by: string; requested_at: string;
};
export type RoundCard = {
  product: string; android: string; os_type: string; product_id?: number; version: string; state: string;
  done: number; total: number; job_id: number | null; why?: string; approved?: boolean;
  mails?: { mail_id: number; subject: string; date: string }[]; created_at?: string;
};
export type Home = {
  user: Me;
  counts: { review: number; failed: number; running: number; approvals: number; new_rounds: number };
  review: StageItem[]; failed: StageItem[]; running: StageItem[]; approvals: Approval[]; new_rounds: RoundCard[];
};
export type JobStage = {
  key: string; label: string; status: string; updated_by: string; updated_at: string;
  result: string; summary: Summary; artifact: { type: string; path: string } | null; blocked: string;
};
export type JobSide = { side: string; title: string; done: number; total: number; stages: JobStage[]; next: string | null };
export type JobDetail = {
  job: { id: number; product: string; android: string; os_type: string; version: string; created_by: string; created_at: string };
  sides: JobSide[];
  facts: { label: string; value: string }[];
  uploads: { type: string; name: string; url: string }[];
  rn: { mid: string; final: string; full_pdf: boolean; sales_pdf: boolean; sales_archived: boolean };
  artifacts: { group: string; name: string; path: string; url: string }[];
  basis: { mail_id: number; subject: string; date: string }[];
  legacy: Record<string, string>;
};
export type Product = {
  id: number; product: string; android: string; os_type: string; category: string;
  initial_os_version: string; filled: string[]; missing: string[];
};
export type Mail = { id: number; subject: string; sender: string; received_at: string; attachments: string[]; snippet: string };

export function skuText(p: { product: string; android?: string; os_type?: string }) {
  return [p.product, p.android ? `A${p.android}` : "", p.os_type].filter(Boolean).join(" ");
}

export function ago(iso: string) {
  if (!iso) return "";
  const t = new Date(iso.includes("T") ? iso : iso.replace(" ", "T"));
  if (isNaN(t.getTime())) return iso;
  const s = (Date.now() - t.getTime()) / 1000;
  if (s < 60) return "방금";
  if (s < 3600) return `${Math.floor(s / 60)}분 전`;
  if (s < 86400) return `${Math.floor(s / 3600)}시간 전`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)}일 전`;
  return `${t.getMonth() + 1}/${t.getDate()}`;
}
