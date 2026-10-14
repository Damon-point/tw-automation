"use client";

import { Suspense, useCallback, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { PageHead, Skeleton, cx } from "@/components/ui";
import { useApi, type Me } from "@/lib/api";
import { ToastHost } from "@/components/whitelist/kit";
import Mailbox from "@/components/whitelist/Mailbox";
import RegisterTab from "@/components/whitelist/RegisterTab";
import RequestsTab from "@/components/whitelist/RequestsTab";
import { DqaSendersTab, PortalTab, RulesTab } from "@/components/whitelist/AdminTabs";

const TABS = [
  { key: "mail", label: "메일함", admin: false },
  { key: "register", label: "신청", admin: false },
  { key: "history", label: "등록 내역", admin: false },
  { key: "rules", label: "규칙", admin: true },
  { key: "portal", label: "고객 포털", admin: true },
  { key: "dqa", label: "DQA 발신자", admin: true },
] as const;

function WhitelistInner() {
  const sp = useSearchParams();
  const router = useRouter();
  const path = usePathname();
  const me = useApi<Me>("/api/me").data;
  const [openCount, setOpenCount] = useState<number | null>(null);
  const tab = sp.get("tab") ?? "mail";
  const tid = sp.get("t") ?? "";
  const isAdmin = me?.role === "admin";

  const nav = useCallback((next: Record<string, string>) => {
    const p = new URLSearchParams(sp.toString());
    Object.entries(next).forEach(([k, v]) => (v ? p.set(k, v) : p.delete(k)));
    router.replace(`${path}?${p.toString()}`, { scroll: false });
  }, [sp, router, path]);

  return (
    <>
      <PageHead title="WhiteList · DQA"
        lead="FOTA 화이트리스트 등록 요청을 메일에서 바로 처리한다 — 등록 · 등록일 변경 · DQA FOTA Test, 그리고 확인과 회신까지." />
      <div className="mb-5 flex flex-wrap gap-1 border-b border-line">
        {TABS.filter((t) => !t.admin || isAdmin).map((t) => (
          <button key={t.key} onClick={() => nav({ tab: t.key === "mail" ? "" : t.key, t: "" })}
                  className={cx("-mb-px inline-flex items-center gap-2 border-b-2 px-3.5 py-2.5 text-[14px] font-semibold transition",
                    tab === t.key ? "border-brand text-ink" : "border-transparent text-ink-3 hover:text-ink-2")}>
            {t.label}
            {t.key === "mail" && openCount ? <span className="rounded-full bg-[#d97706] px-2 text-[11px] font-bold text-white">{openCount}</span> : null}
          </button>
        ))}
      </div>
      {tab === "mail" && <Mailbox selected={tid} onSelect={(t) => nav({ t })} onCount={setOpenCount} />}
      {tab === "register" && <RegisterTab />}
      {tab === "history" && <RequestsTab />}
      {tab === "rules" && isAdmin && <RulesTab />}
      {tab === "portal" && isAdmin && <PortalTab />}
      {tab === "dqa" && isAdmin && <DqaSendersTab />}
    </>
  );
}

export default function WhitelistPage() {
  return (
    <ToastHost>
      <Suspense fallback={<Skeleton rows={6} />}>
        <WhitelistInner />
      </Suspense>
    </ToastHost>
  );
}
