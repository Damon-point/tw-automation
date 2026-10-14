"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Toaster } from "@/components/admin/kit";
import { cx } from "@/components/ui";
import { useApi, type Me } from "@/lib/api";

type Item = { href: string; label: string; admin?: boolean };
const GROUPS: { title: string; items: Item[] }[] = [
  { title: "", items: [{ href: "/settings", label: "한눈에 보기" }] },
  { title: "운영", items: [{ href: "/settings/shadow", label: "검증 모드 · 운영 전환" }] },
  {
    title: "연결",
    items: [
      { href: "/settings/mail", label: "메일 계정" },
      { href: "/settings/fileserver", label: "파일 서버" },
      { href: "/settings/dropbox", label: "Dropbox" },
      { href: "/settings/web", label: "웹 로그인" },
      { href: "/settings/mailchimp", label: "Mailchimp" },
    ],
  },
  {
    title: "자동화",
    items: [
      { href: "/settings/claude", label: "Claude 호출", admin: true },
      { href: "/settings/sb", label: "Solution Buffer" },
    ],
  },
  {
    title: "사람",
    items: [
      { href: "/settings/accounts", label: "계정", admin: true },
      { href: "/settings/staff", label: "사내 인원", admin: true },
      { href: "/settings/credentials", label: "자격증명 현황" },
    ],
  },
  {
    title: "문서",
    items: [
      { href: "/settings/mail-templates", label: "메일 문구", admin: true },
      { href: "/settings/templates", label: "RN 템플릿", admin: true },
    ],
  },
  {
    title: "기록 · 조회",
    items: [
      { href: "/usage", label: "Claude 사용량" },
      { href: "/excel", label: "FOTA-DB 열람" },
    ],
  },
  { title: "내 계정", items: [{ href: "/settings/password", label: "비밀번호 변경" }] },
];

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const me = useApi<Me>("/api/me");
  const isAdmin = me.data?.role === "admin";
  return (
    <div className="grid gap-6 lg:grid-cols-[210px_minmax(0,1fr)]">
      <nav className="lg:sticky lg:top-6 lg:self-start">
        <h1 className="mb-3 px-2 text-[20px] font-bold tracking-tight">설정</h1>
        <div className="flex gap-1 overflow-x-auto pb-2 lg:flex-col lg:gap-0 lg:overflow-visible lg:pb-0">
          {GROUPS.map((g) => {
            const items = g.items.filter((i) => !i.admin || isAdmin);
            if (!items.length) return null;
            return (
              <div key={g.title || "top"} className="flex shrink-0 gap-1 lg:mb-3 lg:flex-col lg:gap-0.5">
                {g.title && <div className="hidden px-2 pb-1 text-[11px] font-bold uppercase tracking-wide text-ink-3 lg:block">{g.title}</div>}
                {items.map((i) => {
                  const active = i.href === "/settings" ? path === "/settings" : path.startsWith(i.href);
                  return (
                    <Link key={i.href} href={i.href}
                          className={cx("whitespace-nowrap rounded-lg px-2.5 py-1.5 text-[13.5px] font-medium transition-colors",
                            active ? "bg-brand-soft text-brand-ink" : "text-ink-2 hover:bg-surface-2 hover:text-ink")}>
                      {i.label}
                    </Link>
                  );
                })}
              </div>
            );
          })}
        </div>
      </nav>
      <div className="min-w-0 space-y-5">{children}</div>
      <Toaster />
    </div>
  );
}
