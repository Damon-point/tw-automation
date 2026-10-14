"use client";

import { useMemo, useState } from "react";
import { Button, Input, Modal, Section, SearchBox, toast } from "@/components/admin/kit";
import { Empty, Skeleton, Tag, cx } from "@/components/ui";
import { useApi, type Me } from "@/lib/api";
import { errText, send, type ShadowResp, type ShadowWrite } from "@/lib/admin";

const SYSTEM: Record<string, string> = {
  mail: "메일", dropbox: "Dropbox", fileserver: "파일 서버", fota_web: "FOTA 웹", fota_whitelist: "FOTA WhiteList",
  fota_db: "FOTA DB", service_portal: "Service Portal", confluence: "Confluence", sb_confluence: "SB Confluence",
  mailchimp: "Mailchimp", system: "운영 전환",
};

const LOOP: Record<string, string> = { mail_sync: "메일 동기화 · 알림", whitelist_poll: "WhiteList 메일 판별", redmine_sb: "Redmine · Solution Buffer" };

const PHRASE = "운영 전환";

function headline(w: ShadowWrite): string {
  const v = (k: string) => (w[k] == null ? "" : String(w[k]));
  if (w.system === "mail") {
    const to = Array.isArray(w.to) ? (w.to as string[]).join(", ") : v("to");
    return `${v("subject")} → ${to}`;
  }
  return v("title") || v("subject") || v("path") || v("rel") || v("name") || v("page_id") || v("download_id") || v("campaign_id") || v("by") || "";
}

export default function ShadowPage() {
  const me = useApi<Me>("/api/me");
  const { data, loading, reload } = useApi<ShadowResp>("/api/shadow?limit=1000", 20_000);
  const [sys, setSys] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<ShadowWrite | null>(null);
  const [toggle, setToggle] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const isAdmin = me.data?.role === "admin";

  const writes = useMemo(() => data?.writes ?? [], [data]);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const w of writes) c[w.system] = (c[w.system] ?? 0) + 1;
    return c;
  }, [writes]);
  const rows = writes.filter((w) => (!sys || w.system === sys) && (!q || JSON.stringify(w).toLowerCase().includes(q.toLowerCase())));

  async function switchMode(live: boolean) {
    setBusy(true);
    try {
      await send("/api/shadow/live", { live });
      toast(live ? "운영으로 전환했습니다 — 지금부터 실제로 씁니다" : "검증 모드로 돌렸습니다");
      setToggle(false);
      setTyped("");
      reload();
      me.reload();
    } catch (e) {
      toast(errText(e), "err");
    } finally {
      setBusy(false);
    }
  }

  if (loading || !data) return <Skeleton rows={6} />;
  const live = data.live;
  return (
    <>
      <Section title="운영 상태"
               desc="8080 과 같은 Dropbox·FOTA·Portal·Confluence·메일을 함께 쓰므로, 검증 모드에서는 외부로 나가는 쓰기를 마지막 단계에서 막고 무엇을 보내려 했는지만 남깁니다."
               right={isAdmin && (live
                 ? <Button variant="outline" busy={busy} onClick={() => switchMode(false)}>검증 모드로 돌리기</Button>
                 : <Button variant="danger" onClick={() => setToggle(true)}>운영 전환…</Button>)}>
        <div className="flex flex-wrap items-center gap-4">
          <span className={cx("inline-flex items-center gap-2 rounded-full px-3 py-1 text-[13.5px] font-bold",
            live ? "bg-ok-soft text-ok" : "bg-[#fff1d6] text-[#8a5a00] dark:bg-[#3a2d10] dark:text-[#f0c36a]")}>
            <span className={cx("h-2 w-2 rounded-full", live ? "bg-ok" : "bg-[#d97706]")} />
            {live ? "운영 — 실제로 씁니다" : "검증 모드 — 기록만 합니다"}
          </span>
          <span className="text-[13px] text-ink-3">막아 둔 쓰기 {writes.length}건</span>
        </div>
        <div className="mt-4 grid gap-2 sm:grid-cols-3">
          {Object.entries(LOOP).map(([k, label]) => {
            const l = data.runtime.loops[k];
            return (
              <div key={k} className="rounded-xl border border-line bg-surface-2 px-3 py-2.5">
                <div className="flex items-center gap-2 text-[13px] font-semibold">
                  <span className={cx("h-2 w-2 rounded-full", !l ? "bg-ink-3" : l.ok ? "bg-ok" : "bg-err")} />
                  {label}
                </div>
                <div className="mt-0.5 truncate text-[12px] text-ink-3" title={l?.error}>
                  {!l ? "첫 실행 전" : l.ok ? `마지막 ${l.at}` : `오류 ${l.at} · ${l.error}`}
                </div>
              </div>
            );
          })}
        </div>
        {data.runtime.started_at && <div className="mt-2 text-[12px] text-ink-3">API 기동 {data.runtime.started_at}</div>}
      </Section>

      <Section title="막아 둔 쓰기" desc="최근 것부터. 줄을 누르면 보내려던 내용 전체가 보입니다."
               right={<Button variant="ghost" size="sm" onClick={reload}>새로고침</Button>}>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <button onClick={() => setSys("")}
                  className={cx("rounded-full border px-3 py-1 text-[12.5px] font-semibold", !sys ? "border-brand bg-brand-soft text-brand-ink" : "border-line-strong text-ink-2")}>
            전체 {writes.length}
          </button>
          {Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([k, n]) => (
            <button key={k} onClick={() => setSys(k === sys ? "" : k)}
                    className={cx("rounded-full border px-3 py-1 text-[12.5px] font-semibold", sys === k ? "border-brand bg-brand-soft text-brand-ink" : "border-line-strong text-ink-2")}>
              {SYSTEM[k] ?? k} {n}
            </button>
          ))}
          <SearchBox value={q} onChange={setQ} placeholder="내용 검색" className="ml-auto" />
        </div>
        {rows.length ? (
          <div className="overflow-hidden rounded-xl border border-line">
            {rows.slice(0, 400).map((w, i) => (
              <button key={i} onClick={() => setOpen(w)}
                      className="flex w-full items-center gap-3 border-b border-line px-4 py-2.5 text-left last:border-b-0 hover:bg-surface-2">
                <span className="w-[132px] shrink-0 font-mono text-[12px] text-ink-3">{w.at.replace("T", " ")}</span>
                <Tag>{SYSTEM[w.system] ?? w.system}</Tag>
                <span className="shrink-0 text-[12.5px] font-semibold text-ink-2">{w.action}</span>
                <span className="min-w-0 flex-1 truncate text-[13px]">{headline(w)}</span>
              </button>
            ))}
          </div>
        ) : <Empty>{writes.length ? "조건에 맞는 기록이 없습니다" : "아직 막은 쓰기가 없습니다"}</Empty>}
      </Section>

      <Modal open={!!open} onClose={() => setOpen(null)} wide
             title={open ? `${SYSTEM[open.system] ?? open.system} · ${open.action}` : ""}>
        {open && (
          <dl className="space-y-3">
            {Object.entries(open).filter(([k]) => !["system", "action"].includes(k)).map(([k, v]) => (
              <div key={k}>
                <dt className="text-[12px] font-semibold text-ink-3">{k}</dt>
                <dd className="mt-0.5 whitespace-pre-wrap break-all rounded-lg bg-surface-2 px-3 py-2 font-mono text-[12.5px]">
                  {typeof v === "string" ? v : JSON.stringify(v, null, 2)}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </Modal>

      <Modal open={toggle} onClose={() => { setToggle(false); setTyped(""); }} title="운영으로 전환"
             footer={<>
               <Button variant="ghost" onClick={() => { setToggle(false); setTyped(""); }}>취소</Button>
               <Button variant="danger" busy={busy} disabled={typed.trim() !== PHRASE} onClick={() => switchMode(true)}>전환</Button>
             </>}>
        <div className="space-y-3 text-[14px] text-ink-2">
          <p>전환하면 3080 이 메일을 실제로 보내고, Dropbox·파일 서버·FOTA·Service Portal·Confluence·Mailchimp 에 실제로 씁니다.
            8080 에서 같은 작업을 돌리고 있으면 두 번 올라가거나 메일이 두 번 나갑니다.</p>
          <p>메일 동기화 · WhiteList 판별 · Solution Buffer 알림처럼 사람이 누르지 않아도 도는 작업도 그 순간부터 실제로 보냅니다.</p>
          <label className="block">
            <span className="mb-1 block text-[12.5px] font-semibold">아래 칸에 <b className="text-err">{PHRASE}</b> 를 입력하세요</span>
            <Input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={PHRASE} autoFocus />
          </label>
        </div>
      </Modal>
    </>
  );
}

