"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api } from "@/lib/api";
import { ThemeCycle } from "@/components/ThemeToggle";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [username, setU] = useState("");
  const [password, setP] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      await api("/api/login", { method: "POST", body: JSON.stringify({ username, password }) });
      router.replace(params.get("next") || "/");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "로그인 실패");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="w-[380px] max-w-[92vw] rounded-2xl border border-line bg-surface p-7 shadow-sm">
      <div className="mb-6 flex items-center gap-2.5">
        <span className="grid h-9 w-9 place-items-center rounded-lg bg-gradient-to-br from-[#5b8cff] to-[#2f6bff] text-[13px] font-extrabold text-white">tw</span>
        <div>
          <div className="text-[16px] font-bold">TW Automation</div>
          <div className="text-[12px] text-ink-3">기존 계정으로 로그인</div>
        </div>
      </div>
      {err && <div className="mb-4 rounded-lg bg-err-soft px-3 py-2 text-[13px] text-err">{err}</div>}
      <label className="mb-1.5 block text-[13px] font-semibold text-ink-2" htmlFor="u">아이디</label>
      <input id="u" autoFocus autoComplete="username" value={username} onChange={(e) => setU(e.target.value)}
             className="mb-4 w-full rounded-lg border border-line-strong bg-surface px-3 py-2 outline-none focus:border-brand focus:ring-2 focus:ring-brand-soft" />
      <label className="mb-1.5 block text-[13px] font-semibold text-ink-2" htmlFor="p">비밀번호</label>
      <input id="p" type="password" autoComplete="current-password" value={password} onChange={(e) => setP(e.target.value)}
             className="mb-5 w-full rounded-lg border border-line-strong bg-surface px-3 py-2 outline-none focus:border-brand focus:ring-2 focus:ring-brand-soft" />
      <button disabled={busy || !username || !password}
              className="w-full rounded-lg bg-brand py-2.5 text-[14px] font-semibold text-white hover:bg-brand-ink disabled:opacity-50">
        {busy ? "확인 중…" : "로그인"}
      </button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <div className="relative grid min-h-screen place-items-center bg-[radial-gradient(1200px_600px_at_20%_-10%,#dbe6ff_0%,transparent_60%)] dark:bg-[radial-gradient(1200px_600px_at_20%_-10%,#1b2742_0%,transparent_60%)]">
      <ThemeCycle className="absolute right-4 top-4 text-ink-3 hover:bg-surface-2 hover:text-ink" />
      <Suspense>
        <LoginForm />
      </Suspense>
    </div>
  );
}
