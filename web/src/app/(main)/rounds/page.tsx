"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/Icons";
import { Card, Empty, PageHead, Progress, Skeleton, StatusChip, cx } from "@/components/ui";
import { Btn, Confirm, Field, Modal, ToastHost, inputCls, useToast } from "@/components/osu/kit";
import { ago, skuText, useApi, type Product, type RoundCard } from "@/lib/api";
import { del, errText, post, type RoundPlan } from "@/lib/osu";

type Rounds = { open: RoundCard[]; finished: RoundCard[] };
type Ask = { kind: "close" | "reopen" | "delete"; r: RoundCard } | null;

export default function RoundsPage() {
  return <ToastHost><Rounds /></ToastHost>;
}

function Rounds() {
  const [q, setQ] = useState("");
  const [tab, setTab] = useState<"open" | "finished">("open");
  const [debounced, setDebounced] = useState("");
  const [openKey, setOpenKey] = useState("");
  const [ask, setAsk] = useState<Ask>(null);
  const [adding, setAdding] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { const t = setTimeout(() => setDebounced(q), 250); return () => clearTimeout(t); }, [q]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName ?? "")) { e.preventDefault(); input.current?.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const { data, loading, reload } = useApi<Rounds>(`/api/rounds?q=${encodeURIComponent(debounced)}`, 30000);
  const rows = tab === "open" ? data?.open ?? [] : data?.finished ?? [];
  const newCount = data?.open.filter((r) => r.state === "new").length ?? 0;
  const keyOf = (r: RoundCard) => `${r.product_id ?? r.product}-${r.android}-${r.version}-${r.job_id ?? ""}`;

  return (
    <>
      <PageHead
        title="OS Update"
        lead="신규로 감지됐거나 진행 중인 회차, 그리고 최근에 끝난 회차."
        right={<Btn icon="sparkle" onClick={() => setAdding(true)}>버전 직접 추가</Btn>}
      />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex rounded-lg border border-line bg-surface p-0.5">
          {(["open", "finished"] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)}
                    className={cx("rounded-md px-3.5 py-1.5 text-[13px] font-semibold", tab === t ? "bg-brand-soft text-brand-ink" : "text-ink-2 hover:text-ink")}>
              {t === "open" ? `진행 중 ${data?.open.length ?? ""}` : `최근 완료 ${data?.finished.length ?? ""}`}
              {t === "open" && newCount > 0 && <span className="ml-1.5 rounded-full bg-err px-1.5 text-[11px] text-white">신규 {newCount}</span>}
            </button>
          ))}
        </div>
        <div className="relative ml-auto w-full max-w-[320px]">
          <Icon name="search" className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" />
          <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder="제품 · 버전으로 찾기   ( / )"
                 className="w-full rounded-lg border border-line-strong bg-surface py-2 pl-9 pr-3 text-[14px] outline-none focus:border-brand focus:ring-2 focus:ring-brand-soft" />
        </div>
      </div>
      <Card pad={false}>
        {loading ? <Skeleton rows={5} /> : rows.length ? rows.map((r) => {
          const k = keyOf(r);
          return <RoundRow key={k} r={r} open={openKey === k} onToggle={() => setOpenKey(openKey === k ? "" : k)} onAsk={(kind) => setAsk({ kind, r })} onStarted={reload} />;
        }) : <Empty>{q ? `'${q}' 에 맞는 회차가 없습니다` : tab === "open" ? "진행 중인 회차가 없습니다" : "최근 완료된 회차가 없습니다"}</Empty>}
      </Card>
      {tab === "finished" && <p className="mt-3 text-[12px] text-ink-3">최근 작업 60건 중 모든 단계가 끝난 것. {data?.finished[0]?.created_at && `가장 최근 ${ago(data.finished[0].created_at!)}`}</p>}
      <AskDialog ask={ask} onClose={() => setAsk(null)} onDone={() => { setAsk(null); setOpenKey(""); reload(); }} />
      <AddVersion open={adding} onClose={() => setAdding(false)} />
    </>
  );
}

function RoundRow({ r, open, onToggle, onAsk, onStarted }: { r: RoundCard; open: boolean; onToggle: () => void; onAsk: (k: "close" | "reopen" | "delete") => void; onStarted: () => void }) {
  const finished = r.state === "finished";
  const head = (
    <div className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1.5 md:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_120px_auto]">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-semibold">{skuText(r)}</span>
          <span className="font-mono text-[13px] text-ink-2">{r.version}</span>
        </div>
        {r.why && <div className="mt-0.5 truncate text-[12px] text-ink-3" title={r.why}>{r.why}</div>}
      </div>
      <div className="hidden min-w-0 md:block">
        {r.total ? (
          <>
            <div className="mb-1 flex justify-between text-[11px] text-ink-3"><span>{r.done} / {r.total} 단계</span></div>
            <Progress done={r.done} total={r.total} />
          </>
        ) : <span className="text-[12px] text-ink-3">아직 시작하지 않음</span>}
      </div>
      <div className="hidden md:block"><StatusChip status={r.state === "running" ? "review" : r.state} label={r.state === "running" ? "진행 중" : undefined} /></div>
      <Icon name="chevron" className={cx("h-4 w-4 text-ink-3 transition-transform", !r.job_id && open && "rotate-90")} />
    </div>
  );
  return (
    <div className="group relative border-b border-line last:border-b-0">
      {r.job_id ? (
        <Link href={`/jobs/${r.job_id}`} className="block px-5 py-3 pr-[150px] hover:bg-surface-2">{head}</Link>
      ) : (
        <button onClick={onToggle} className={cx("block w-full px-5 py-3 text-left hover:bg-surface-2", open && "bg-surface-2")}>{head}</button>
      )}
      {r.job_id && (
        <div className="absolute right-12 top-1/2 flex -translate-y-1/2 gap-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
          {finished && <Btn kind="ghost" onClick={() => onAsk("reopen")}>다시 열기</Btn>}
          <Btn kind="ghost" className="text-err" onClick={() => onAsk("delete")}>삭제</Btn>
        </div>
      )}
      {!r.job_id && open && r.product_id && <StartPanel r={r} onAsk={onAsk} onStarted={onStarted} />}
    </div>
  );
}

function StartPanel({ r, onAsk, onStarted }: { r: RoundCard; onAsk: (k: "close") => void; onStarted: () => void }) {
  const toast = useToast();
  const router = useRouter();
  const plan = useApi<RoundPlan>(`/api/rounds/plan?product_id=${r.product_id}&version=${encodeURIComponent(r.version)}`);
  const [ver, setVer] = useState("");
  const [busy, setBusy] = useState(false);
  const [showReplies, setShowReplies] = useState(false);
  const p = plan.data;

  async function start() {
    setBusy(true);
    try {
      const res = await post<{ message: string; job_id: number }>("/api/rounds/start", { product_id: r.product_id, version: r.version, version_input: ver });
      toast("ok", res.message);
      onStarted();
      router.push(`/jobs/${res.job_id}`);
    } catch (e) {
      toast("err", errText(e));
      setBusy(false);
    }
  }

  if (plan.loading) return <div className="px-5 pb-4"><Skeleton rows={2} /></div>;
  if (!p) return <div className="px-5 pb-4 text-[13px] text-err">{plan.error}</div>;
  const origins = p.mails.filter((m) => m.origin);
  const shown = showReplies || !origins.length ? p.mails : origins;
  return (
    <div className="border-t border-line bg-surface-2/60 px-5 py-4">
      <div className="mb-3 text-[13.5px] font-semibold text-err">{p.why}</div>
      {p.mails.length > 0 && (
        <div className="mb-4">
          <div className="mb-1.5 flex items-center gap-2 text-[12px] font-semibold text-ink-2">
            근거 메일 {shown.length}건
            {origins.length > 0 && origins.length < p.mails.length && (
              <button onClick={() => setShowReplies((v) => !v)} className="font-normal text-brand-ink hover:underline">
                {showReplies ? "원문만" : `회신 ${p.mails.length - origins.length}건 포함`}
              </button>
            )}
          </div>
          <ul className="space-y-1">
            {shown.slice(0, 12).map((m) => (
              <li key={m.mail_id} className="flex items-center gap-2 text-[12.5px]">
                <span className={cx("rounded px-1.5 py-0.5 text-[10.5px] font-bold", m.origin ? "bg-ok-soft text-ok" : "bg-muted-soft text-ink-3")}>{m.origin ? "원문" : "회신"}</span>
                {m.version && <span className="font-mono text-ink-2">{m.version}</span>}
                <Link href={`/mail?q=${encodeURIComponent(m.subject.slice(0, 40))}`} className="min-w-0 flex-1 truncate hover:text-brand-ink" title={m.subject}>{m.subject || `메일 #${m.mail_id}`}</Link>
                <span className="text-ink-3">{m.date}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="mb-4 grid gap-4 sm:grid-cols-2">
        {p.sides.map((s) => (
          <div key={s.side}>
            <div className={cx("mb-1 text-[12px] font-bold", s.side === "submission" ? "text-ok" : "text-warn")}>{s.label}</div>
            <ol className="space-y-0.5 text-[12.5px] text-ink-2">
              {s.stages.map((st, i) => <li key={i}><span className="mr-1.5 text-ink-3">{i + 1}.</span>{st}</li>)}
            </ol>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap items-end gap-3">
        {p.unknown && (
          <Field label="이 회차의 OS 버전" hint={p.basis || "버전을 정하지 못했습니다"} className="w-[260px]">
            <input className={inputCls} value={ver} onChange={(e) => setVer(e.target.value)} placeholder="예: 45.81" autoFocus />
          </Field>
        )}
        <div className="ml-auto flex gap-2">
          {!p.unknown && <Btn kind="ghost" onClick={() => onAsk("close")}>완료로 기록</Btn>}
          <Btn kind="primary" icon="play" busy={busy} disabled={p.unknown && !ver.trim()} onClick={start}>{p.unknown ? "작업 시작" : `${p.version} 작업 시작`}</Btn>
        </div>
      </div>
    </div>
  );
}

function AskDialog({ ask, onClose, onDone }: { ask: Ask; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  if (!ask) return null;
  const { kind, r } = ask;
  const name = `${skuText(r)} ${r.version}`;

  async function go() {
    setBusy(true);
    try {
      const res = kind === "close" ? await post("/api/rounds/close", { product_id: r.product_id, version: r.version })
        : kind === "reopen" ? await post(`/api/jobs/${r.job_id}/reopen`)
        : await del(`/api/jobs/${r.job_id}`);
      toast("ok", res.message ?? "처리했습니다");
      onDone();
    } catch (e) {
      toast("err", errText(e));
    } finally {
      setBusy(false);
    }
  }

  const spec = {
    close: { title: `${name} 을(를) 완료로 기록`, label: "완료로 기록", danger: false,
      body: "단계를 실행하지 않고 이미 끝난 회차로 남깁니다. [다시 열기] 로 되돌릴 수 있습니다." },
    reopen: { title: `${name} 다시 열기`, label: "다시 열기", danger: false,
      body: "모든 단계가 '대기' 로 돌아갑니다. 만들어 둔 산출물 파일은 그대로 남습니다." },
    delete: { title: `${name} 작업 삭제`, label: "삭제", danger: true,
      body: "단계 진행 상태가 함께 지워집니다. 만들어 둔 산출물 파일은 남습니다." },
  }[kind];
  return (
    <Confirm open title={spec.title} danger={spec.danger} busy={busy} confirmLabel={spec.label} onConfirm={go} onClose={onClose}>
      <p className="text-[13.5px] text-ink-2">{spec.body}</p>
    </Confirm>
  );
}

function AddVersion({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast();
  const router = useRouter();
  const prods = useApi<{ products: Product[] }>(open ? "/api/products" : null);
  const [pid, setPid] = useState("");
  const [ver, setVer] = useState("");
  const [busy, setBusy] = useState(false);

  async function go() {
    setBusy(true);
    try {
      const res = await post<{ message: string; job_id: number }>("/api/rounds/create", { product_id: Number(pid), version: ver });
      toast("ok", res.message);
      onClose();
      router.push(`/jobs/${res.job_id}`);
    } catch (e) {
      toast("err", errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="버전 직접 추가"
           footer={<><Btn kind="ghost" onClick={onClose}>취소</Btn><Btn kind="primary" icon="play" busy={busy} disabled={!pid || !ver.trim()} onClick={go}>작업 만들기</Btn></>}>
      <p className="mb-4 text-[13px] text-ink-2">배포 공지·트리거 메일 없이 나가는 회차를 시작합니다. 버전은 문서·업로드 표기에 그대로 쓰입니다.</p>
      <div className="space-y-3">
        <Field label="제품">
          <select className={inputCls} value={pid} onChange={(e) => setPid(e.target.value)}>
            <option value="">고르기</option>
            {(prods.data?.products ?? []).map((p) => <option key={p.id} value={p.id}>{skuText(p)}</option>)}
          </select>
        </Field>
        <Field label="OS 버전"><input className={inputCls} value={ver} onChange={(e) => setVer(e.target.value)} placeholder="예: 95.08 · 95.08C1" onKeyDown={(e) => { if (e.key === "Enter" && pid && ver.trim()) go(); }} /></Field>
      </div>
    </Modal>
  );
}
