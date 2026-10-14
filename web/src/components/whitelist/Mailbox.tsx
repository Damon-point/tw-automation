"use client";

import { useEffect, useState } from "react";
import { Icon } from "@/components/Icons";
import { Card, Empty, Skeleton, Tag, cx } from "@/components/ui";
import { useApi } from "@/lib/api";
import {
  KIND_LABEL, STATE_LABEL, errText, post,
  type ThreadDetail, type ThreadState, type ThreadSummary,
} from "@/lib/whitelist";
import { Btn, Check, Note, useToast } from "./kit";
import DqaProcess from "./DqaProcess";
import OrganicProcess from "./OrganicProcess";
import SimpleProcess from "./SimpleProcess";

const STATE_CLS: Record<ThreadState, string> = {
  open: "bg-warn-soft text-warn", reopened: "bg-err-soft text-err", done: "bg-ok-soft text-ok", notwl: "bg-muted-soft text-ink-3",
};

function StateChip({ s }: { s: ThreadState }) {
  return <span className={cx("inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-bold", STATE_CLS[s])}>{STATE_LABEL[s]}</span>;
}

export default function Mailbox({ selected, onSelect, onCount }: { selected: string; onSelect: (tid: string) => void; onCount?: (n: number) => void }) {
  const toast = useToast();
  const [showDone, setShowDone] = useState(false);
  const [showNotwl, setShowNotwl] = useState(false);
  const [q, setQ] = useState("");
  const [dq, setDq] = useState("");
  const [syncing, setSyncing] = useState(false);
  useEffect(() => { const h = setTimeout(() => setDq(q), 250); return () => clearTimeout(h); }, [q]);
  const list = useApi<{ open: number; threads: ThreadSummary[] }>(
    `/api/wl/threads?show_done=${showDone}&show_notwl=${showNotwl}&q=${encodeURIComponent(dq)}`, 30000);
  useEffect(() => { if (list.data) onCount?.(list.data.open); }, [list.data, onCount]);

  async function sync() {
    setSyncing(true);
    try {
      const r = await post<{ added: number; triage: { asked?: number; error?: string; blocked?: boolean; pending?: number } }>("/api/wl/sync");
      toast(r.added ? "ok" : "info", (r.added ? `새 메일 ${r.added}통` : "새 메일이 없습니다")
        + (r.triage.asked ? ` · ${r.triage.asked}건 판별` : "") + (r.triage.error ? ` · 판별 실패: ${r.triage.error}` : ""));
      list.reload();
    } catch (e) { toast("error", errText(e)); } finally { setSyncing(false); }
  }

  const threads = list.data?.threads ?? [];
  return (
    <div className={cx("grid gap-4", selected ? "xl:grid-cols-[400px_1fr]" : "")}>
      <div className={cx(selected && "hidden xl:block")}>
        <Card pad={false}>
          <div className="space-y-2.5 border-b border-line p-3">
            <div className="flex items-center gap-2">
              <div className="relative flex-1">
                <Icon name="search" className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" />
                <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="제목 · 발신자 · 일련번호"
                       className="w-full rounded-lg border border-line-strong bg-surface py-2 pl-9 pr-3 text-[13.5px] outline-none focus:border-brand" />
              </div>
              <Btn kind="soft" busy={syncing} onClick={sync}>동기화</Btn>
            </div>
            <div className="flex flex-wrap items-center gap-4">
              <Check checked={showDone} onChange={setShowDone}>처리 완료 포함</Check>
              <Check checked={showNotwl} onChange={setShowNotwl}>작업 아님 포함</Check>
              <span className="ml-auto text-[12px] text-ink-3">{threads.length}건</span>
            </div>
          </div>
          <div className="max-h-[calc(100vh-260px)] overflow-auto">
            {list.loading && !list.data ? <Skeleton rows={7} /> : list.error ? <div className="p-4"><Note tone="error">{list.error}</Note></div>
              : !threads.length ? <Empty>처리할 메일이 없습니다</Empty>
              : threads.map((t) => (
                <button key={t.tid} onClick={() => onSelect(t.tid)}
                        className={cx("block w-full border-b border-line px-4 py-3 text-left last:border-b-0 hover:bg-surface-2",
                          selected === t.tid && "bg-brand-soft hover:bg-brand-soft")}>
                  <div className="flex items-center gap-2">
                    <StateChip s={t.state} />
                    {!t.judged ? <Tag>AI 판별 중</Tag> : <Tag tone={t.kind === "dqa" ? "warn" : "none"}>{KIND_LABEL[t.kind]}</Tag>}
                    <span className="ml-auto whitespace-nowrap text-[11.5px] text-ink-3">{t.when.slice(5)}</span>
                  </div>
                  <div className={cx("mt-1 line-clamp-2 text-[13.5px] font-semibold", !t.judged && "text-ink-3")}>{t.subject}</div>
                  <div className="mt-0.5 flex items-center gap-2 text-[12px] text-ink-3">
                    <span className="min-w-0 flex-1 truncate">{t.sender}</span>
                    {t.serial_count > 0 && <span className="whitespace-nowrap font-semibold text-info">SN {t.serial_count.toLocaleString()}</span>}
                    {t.count > 1 && <span className="whitespace-nowrap">메일 {t.count}</span>}
                    {t.attachments.length > 0 && <span>📎</span>}
                  </div>
                </button>
              ))}
          </div>
        </Card>
      </div>
      {selected && <ThreadPane tid={selected} onBack={() => onSelect("")} onChanged={() => list.reload()} />}
    </div>
  );
}

function ThreadPane({ tid, onBack, onChanged }: { tid: string; onBack: () => void; onChanged: () => void }) {
  const toast = useToast();
  const { data: t, error, reload } = useApi<ThreadDetail>(`/api/wl/threads/${tid}`);
  const [openMail, setOpenMail] = useState<number | null>(null);
  const [nonce, setNonce] = useState(0);

  async function flag(path: "done" | "notwl", on: boolean) {
    try {
      const r = await post<{ message: string }>(`/api/wl/threads/${tid}/${path}`, { on });
      toast("ok", r.message); reload(); onChanged();
    } catch (e) { toast("error", errText(e)); }
  }
  const changed = () => { reload(); onChanged(); };

  if (error) return <Card><Note tone="error">{error}</Note></Card>;
  if (!t || t.tid !== tid) return <Card pad={false}><Skeleton rows={8} /></Card>;

  return (
    <div className="min-w-0 space-y-4">
      <Card pad={false}>
        <div className="border-b border-line px-5 py-4">
          <div className="mb-2 flex items-center gap-2">
            <button onClick={onBack} className="rounded-md px-1.5 py-0.5 text-[13px] text-ink-3 hover:bg-muted-soft xl:hidden">← 목록</button>
            <StateChip s={t.state} />
            {t.judged && <Tag tone={t.kind === "dqa" ? "warn" : "none"}>{KIND_LABEL[t.kind]}</Tag>}
            <Tag>{t.auto_category}</Tag>
            <span className="ml-auto text-[12px] text-ink-3">{t.when}</span>
          </div>
          <h2 className="text-[17px] font-bold leading-snug">{t.subject}</h2>
          <div className="mt-1 text-[13px] text-ink-2">{t.name || t.addr} <span className="text-ink-3">&lt;{t.addr || "주소 없음"}&gt;</span> · {t.lang === "ko" ? "한국어" : "영어"}</div>
          {t.judged && (
            <div className="mt-3 rounded-lg bg-surface-2 px-3 py-2 text-[13px]">
              <span className="font-semibold">AI 판정</span>
              <span className="text-ink-2"> · {t.model ? `모델 ${t.model} · ` : ""}OS {t.from_os || "?"} → {t.to_os || "?"}</span>
              {t.reason && <div className="mt-0.5 text-[12.5px] text-ink-3">{t.reason}</div>}
            </div>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            {t.state === "done" ? <Btn small kind="ghost" onClick={() => flag("done", false)}>다시 열기</Btn>
              : t.state !== "notwl" && <Btn small kind="ghost" onClick={() => flag("done", true)}>처리 완료(수동)</Btn>}
            {t.state === "notwl" ? <Btn small kind="ghost" onClick={() => flag("notwl", false)}>되돌리기</Btn>
              : <Btn small kind="ghost" onClick={() => flag("notwl", true)}>작업 아님</Btn>}
          </div>
        </div>
        <div className="divide-y divide-line">
          {t.members.map((m, i) => {
            const open = openMail === null ? i === 0 : openMail === m.id;
            return (
              <div key={m.id} className="px-5 py-3">
                <button onClick={() => setOpenMail(open ? -1 : m.id)} className="flex w-full items-center gap-2 text-left">
                  <span className="min-w-0 flex-1 truncate text-[13px] font-semibold">{m.sender}</span>
                  {m.attachments && <span className="truncate text-[11.5px] text-ink-3" title={m.attachments}>📎 {m.attachments.split(/[\n,]/)[0]}</span>}
                  <span className="whitespace-nowrap text-[11.5px] text-ink-3">{m.when}</span>
                </button>
                {open && <pre className="mt-2 max-h-[320px] overflow-auto whitespace-pre-wrap font-[family-name:var(--font-sans)] text-[13px] leading-relaxed text-ink-2">{m.body || "(본문 없음)"}</pre>}
              </div>
            );
          })}
        </div>
      </Card>

      <Card title="처리" right={!t.is_admin ? <Tag>관리자만 처리할 수 있습니다</Tag> : <Btn small kind="ghost" onClick={() => setNonce((n) => n + 1)}>처음부터</Btn>}>
        {!t.judged ? <Note>AI 판별 중 — 끝나면 처리 단계가 열립니다.</Note>
          : t.kind === "dqa" ? <DqaProcess key={nonce} t={t} onChanged={changed} />
          : t.kind === "organic" ? <OrganicProcess key={nonce} t={t} onChanged={changed} />
          : t.kind === "simple" ? <SimpleProcess key={nonce} t={t} onChanged={changed} />
          : <Note>이 메일에서 일련번호를 찾지 못했습니다.</Note>}
      </Card>
    </div>
  );
}
