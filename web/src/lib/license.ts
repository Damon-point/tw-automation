// PMDM License — /api/license 응답 타입과 작은 도구들

export type LicStatus = "대기중" | "승인" | "반려" | string;

export type LicRow = {
  id: number; doc_id: string; status: LicStatus; date_issued: string; company_name: string; quantity: number;
  contact_name: string; contact_email: string; license_user_id: string; created_by_name: string;
  has_key: boolean; has_cert: boolean; confirm_date: string; confirm_by: string;
};

export type LicStats = { total: number; pending: number; approved: number; rejected: number; total_qty: number; approved_qty: number };

export type LicOverview = {
  is_admin: boolean; scope: "mine" | "all"; keys_ready: boolean; cert_template: boolean; live: boolean;
  stats: LicStats | null; requests: LicRow[];
};

export type LicVerify = { ok: boolean; id?: string; count?: string; id_ok?: boolean; count_ok?: boolean; issued_at?: string; error?: string };
export type LicMailLog = { at: string; kind: "approval" | "rejection"; to: string; by: string; ok: boolean; error: string; resend: boolean; attached: boolean; shadow: boolean };

export type LicDetail = LicRow & {
  note: string; admin_note: string; part_number: string; type: string; recovery_email: string; recipient_email: string;
  license_key: string; verify: LicVerify | null;
  po: { name: string; url: string } | null; cert: { name: string; url: string } | null;
  mails: LicMailLog[];
  mail_preview: { to: string; subject: string; body: string; attachment: string } | null;
  created_at: string; can_admin: boolean;
};

export type LicStep = { step: string; ok: boolean; msg: string };

export type LicMail = { id: number; subject: string; sender: string; received_at: string; attachments: string[] };
export type LicPrefill = {
  contact_name: string; contact_email: string; company_name: string; quantity: number | null;
  license_user_id: string; note: string; attachments: string[]; po_guess: string;
};

export function qty(n: number | string) {
  const v = typeof n === "number" ? n : parseInt(String(n), 10);
  return isNaN(v) ? String(n || "—") : v.toLocaleString("ko-KR");
}

export function fileToB64(f: File): Promise<string> {
  return new Promise((ok, bad) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result).split(",", 2)[1] ?? "");
    r.onerror = () => bad(r.error);
    r.readAsDataURL(f);
  });
}

// 메일로 들어온 신청 — /api/license/inbox
export type LicInboxStatus = "new" | "answered" | "linked" | "created" | "dismissed";
export type LicFields = { company_name: string; quantity: number | null; contact_name: string; contact_email: string; license_user_id: string };
export type LicInboxItem = {
  mail_id: number; subject: string; sender: string; sender_email: string; received_at: string;
  status: LicInboxStatus; doc_id: string; answered_by?: string;
  scanned: boolean; error: string; is_request: boolean; confidence: number; reason: string;
  fields: LicFields; missing: string[]; requester: string; country: string; sku: string; po_number: string;
  note: string; attachments: string[]; po_guess: string; body?: string;
};
export type LicInbox = { items: LicInboxItem[]; pending: number; counts: Record<LicInboxStatus, number> };
