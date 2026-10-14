"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ago, api, useApi } from "@/lib/api";
import type { IssueOverview } from "@/lib/issues";
import { cx, PageHead } from "@/components/ui";
import { Btn, ToastHost, useToast } from "@/components/issues/kit";

const TABS = [
  { href: "/issues", label: "Solution Buffer", badge: true },
  { href: "/issues/list", label: "이슈" },
  { href: "/issues/stats", label: "통계" },
  { href: "/issues/settings", label: "동작 설정" },
];

function Head() {
  const path = usePathname();
  const ov = useApi<IssueOverview>("/api/issues/overview", 30_000);
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  async function sync() {
    setBusy(true);
    try {
      const r = await api<{ message: string; started: boolean }>("/api/issues/sync", { method: "POST" });
      toast(r.message, "info");
      setWaiting(Date.now() / 1000);
      ov.reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  // 수집은 서버 스레드에서 돈다 — 끝나면 결과를 알리고 각 탭에 새로 읽으라고 알린다.
  const [waiting, setWaiting] = useState(0);
  useEffect(() => {
    if (!waiting) return;
    const t = setInterval(async () => {
      const d = await api<IssueOverview>("/api/issues/overview").catch(() => null);
      if (!d) return;
      const r = d.sync_result;
      if (!d.sync.running && r?.at && r.at >= waiting - 1) {
        clearInterval(t);
        setWaiting(0);
        toast(r.message ?? "", r.ok ? "ok" : "err");
        ov.reload();
        window.dispatchEvent(new Event("issues:changed"));
      }
    }, 2500);
    return () => clearInterval(t);
  }, [waiting]); // eslint-disable-line react-hooks/exhaustive-deps

  // 주기 수집(5분)이 새 이벤트를 넣으면 화면도 따라간다 — 데이터 판번호가 바뀐 때만.
  const ver = useRef("");
  useEffect(() => {
    const v = ov.data?.version?.join(",") ?? "";
    if (!v || v.startsWith("-1")) return;
    if (ver.current && ver.current !== v) window.dispatchEvent(new Event("issues:changed"));
    ver.current = v;
  }, [ov.data?.version]);

  const s = ov.data?.sync;
  return (
    <>
      <PageHead
        title="Solution Buffer"
        lead="Redmine salesissue 를 5분마다 수집하고, Resolved 된 이슈를 SB 문서 초안으로 만든다. Confluence 업로드는 사람이 누를 때만."
        right={
          <div className="flex items-center gap-3">
            {s && (
              <span className="text-right text-[12px] leading-tight text-ink-3">
                {s.last_poll ? `마지막 수집 ${ago(s.last_poll)}` : "수집 기록 없음"}
                <span className="block">이슈 {s.issues.toLocaleString()} · 이벤트 {s.journals.toLocaleString()}</span>
              </span>
            )}
            <Btn kind="primary" onClick={sync} busy={busy || s?.running || !!waiting} title="Redmine 에서 새 이벤트를 지금 가져온다">
              {s?.running ? "수집 중" : "지금 동기화"}
            </Btn>
          </div>
        }
      />
      {s?.last_error && <div className="mb-3 rounded-lg bg-err-soft px-3 py-2 text-[13px] text-err">수집 오류: {s.last_error}</div>}
      {s && s.backfill && s.backfill !== "done" && (
        <div className="mb-3 rounded-lg bg-warn-soft px-3 py-2 text-[13px] text-warn">최초 전수 수집 진행 중 — 상세 대기 {s.detail_pending.toLocaleString()}건</div>
      )}
      <nav className="mb-5 flex gap-1 overflow-x-auto border-b border-line">
        {TABS.map((t) => {
          const active = t.href === "/issues" ? path === "/issues" : path.startsWith(t.href);
          const n = t.badge ? ov.data?.sb_open ?? 0 : 0;
          return (
            <Link key={t.href} href={t.href}
                  className={cx("-mb-px inline-flex items-center gap-2 whitespace-nowrap border-b-2 px-3.5 py-2.5 text-[14px] font-semibold transition-colors",
                    active ? "border-brand text-ink" : "border-transparent text-ink-3 hover:text-ink")}>
              {t.label}
              {n > 0 && <span className="rounded-full bg-err px-1.5 text-[11px] font-bold leading-[18px] text-white">{n}</span>}
            </Link>
          );
        })}
      </nav>
    </>
  );
}

export default function IssuesLayout({ children }: { children: React.ReactNode }) {
  return (
    <ToastHost>
      <Head />
      {children}
    </ToastHost>
  );
}
