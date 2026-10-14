"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { api, useApi, type Home, type Me } from "@/lib/api";
import { Icon } from "./Icons";
import { ThemeCycle, ThemeSwitch } from "./ThemeToggle";
import { cx } from "./ui";

const NAV = [
  { href: "/", label: "오늘 할 일", icon: "home", count: (h?: Home | null) => h ? h.counts.approvals + h.counts.failed + h.counts.review : 0, hot: true },
  { href: "/rounds", label: "OS Update", icon: "rounds", count: (h?: Home | null) => h?.counts.new_rounds ?? 0, hot: true },
  { href: "/whitelist", label: "WhiteList · DQA", icon: "shield" },
  { href: "/license", label: "PMDM License", icon: "key", count: (h?: Home | null) => h?.counts.license_mail ?? 0, hot: true },
  { href: "/issues", label: "Solution Buffer", icon: "bug" },
  { href: "/mail", label: "메일", icon: "inbox" },
  { href: "/products", label: "제품 관리", icon: "box" },
  { href: "/settings", label: "설정", icon: "gear" },
];

const ROLE: Record<string, string> = { admin: "관리자", user: "작성자", mid_reviewer: "RN 승인자", final_reviewer: "최종 승인자" };

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const me = useApi<Me>("/api/me");
  const home = useApi<Home>("/api/home", 30_000);

  async function logout() {
    await api("/api/logout", { method: "POST" });
    router.replace("/login");
  }

  const name = me.data?.username ?? "";
  return (
    <div className="grid min-h-screen grid-cols-1 lg:grid-cols-[240px_1fr]">
      <aside className="sticky top-0 z-10 flex flex-col gap-1 border-b border-side-line bg-side px-3 py-4 text-side-text lg:h-screen lg:border-b-0 lg:border-r">
        <div className="mb-3 flex items-center">
        <Link href="/" className="flex flex-1 items-center gap-2.5 px-2 text-side-strong">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-[#5b8cff] to-[#2f6bff] text-[13px] font-extrabold text-white">tw</span>
          <span>
            <span className="block text-[15px] font-bold leading-tight">TW Automation</span>
            <span className="block text-[11px] text-side-dim">OS 배포 · FOTA · 문서</span>
          </span>
        </Link>
        <ThemeCycle className="text-side-dim hover:bg-side-hover hover:text-side-strong lg:hidden" />
        </div>
        <nav className="flex flex-row flex-wrap gap-1 lg:flex-col">
          {NAV.map((n) => {
            const active = n.href === "/" ? path === "/" : path.startsWith(n.href) || (n.href === "/rounds" && path.startsWith("/jobs"));
            const c = n.count?.(home.data) ?? 0;
            return (
              <Link key={n.href} href={n.href}
                    className={cx("flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[14px] font-medium transition-colors",
                      active ? "bg-side-active font-semibold text-side-active-text" : "hover:bg-side-hover hover:text-side-strong")}>
                <Icon name={n.icon} className="h-[18px] w-[18px] opacity-85" />
                <span className="flex-1">{n.label}</span>
                {c > 0 && <span className={cx("rounded-full px-2 text-[11px] font-bold", n.hot ? "bg-[#d97706] text-white" : "text-side-dim")}>{c}</span>}
              </Link>
            );
          })}
        </nav>
        <div className="mt-auto hidden border-t border-side-line pt-3 lg:block">
          <ThemeSwitch className="mb-2" />
          {me.data?.legacy_url && (
            <a href={me.data.legacy_url} target="_blank" rel="noreferrer" className="mb-2 block rounded-lg px-2.5 py-2 text-[12.5px] text-side-dim hover:bg-side-hover hover:text-side-strong">
              기존 화면(8080) 열기 ↗
            </a>
          )}
          <div className="flex items-center gap-2.5 px-2 py-1.5">
            <span className="grid h-8 w-8 place-items-center rounded-full bg-side-chip text-[12px] font-bold text-side-chip-text">{name.slice(0, 2).toUpperCase()}</span>
            <span className="flex-1 leading-tight">
              <span className="block truncate text-[13px] text-side-strong">{name}</span>
              <span className="block text-[11px] text-side-dim">{ROLE[me.data?.role ?? ""] ?? me.data?.role}</span>
            </span>
            <button onClick={logout} title="로그아웃" className="rounded-md p-1.5 text-side-dim hover:bg-side-hover hover:text-side-strong">
              <Icon name="logout" />
            </button>
          </div>
        </div>
      </aside>
      <main className="min-w-0">
        {me.data && !me.data.live && (
          <Link href="/settings/shadow" className="block border-b border-[#f2d49b] bg-[#fff7e6] px-4 py-2 text-[13px] text-[#8a5a00] lg:px-8 dark:border-[#5a4310] dark:bg-[#2a2210] dark:text-[#f0c36a]">
            <b>검증 모드</b> — 메일·Dropbox·FOTA·Portal·Confluence 에 실제로 쓰지 않고 기록만 합니다. 기록 보기 →
          </Link>
        )}
        <div className="mx-auto w-full max-w-[1280px] px-4 py-7 lg:px-8">{children}</div>
      </main>
    </div>
  );
}
