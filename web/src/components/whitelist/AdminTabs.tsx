"use client";

import { useState } from "react";
import { Card, Empty, Skeleton, Tag, cx } from "@/components/ui";
import { useApi } from "@/lib/api";
import { del, errText, post, upload, type Account, type Company, type Rule } from "@/lib/whitelist";
import { Btn, Field, Input, Modal, Select, useConfirm, useToast } from "./kit";

// ── 규칙 ────────────────────────────────────────────────────────────────────

const CAT_TONE: Record<string, string> = {
  GMS: "bg-ok-soft text-ok", DQA: "bg-warn-soft text-warn", KT: "bg-info-soft text-info", LG: "bg-err-soft text-err",
};

export function RulesTab() {
  const toast = useToast();
  const [cat, setCat] = useState("");
  const { data, loading, reload } = useApi<{ rules: Rule[]; categories: string[]; models: string[]; os_levels: number[] }>(`/api/wl/rules?cat=${encodeURIComponent(cat)}`);
  const [f, setF] = useState({ category: "기타", model: "", from_os: "", to_os: "", ostype: "" });
  const [busy, setBusy] = useState(false);

  async function add() {
    if (!f.model || !f.from_os || !f.to_os || !f.ostype) { toast("warn", "모델 · From · To · OS Type 을 모두 채우세요"); return; }
    setBusy(true);
    try { await post("/api/wl/rules", { ...f, from_os: Number(f.from_os), to_os: Number(f.to_os) }); toast("ok", "추가했습니다"); setF({ ...f, ostype: "" }); reload(); }
    catch (e) { toast("error", errText(e)); } finally { setBusy(false); }
  }
  async function remove(r: Rule) {
    try { await del(`/api/wl/rules/${r.id}`); toast("ok", `${r.model} ${r.from}→${r.to} 삭제`); reload(); } catch (e) { toast("error", errText(e)); }
  }
  async function importFile(file?: File) {
    if (!file) return;
    try {
      const r = await upload<{ added: number; updated: number; skipped: number; errors: string[] }>("/api/wl/rules/import", file);
      toast(r.errors.length ? "warn" : "ok", `추가 ${r.added} · 갱신 ${r.updated} · 유지 ${r.skipped}${r.errors.length ? ` · 오류 ${r.errors.length}건: ${r.errors[0]}` : ""}`);
      reload();
    } catch (e) { toast("error", errText(e)); }
  }

  const os = (data?.os_levels ?? []).map(String);
  return (
    <div className="space-y-4">
      <Card title="새 규칙">
        <div className="grid items-end gap-3 sm:grid-cols-[110px_140px_100px_100px_1fr_auto]">
          <Field label="구분"><Select value={f.category} onChange={(v) => setF({ ...f, category: v })} options={data?.categories ?? ["기타"]} /></Field>
          <Field label="Model">
            <Input list="wl-models" value={f.model} onChange={(e) => setF({ ...f, model: e.target.value.toUpperCase() })} placeholder="PM95" />
            <datalist id="wl-models">{(data?.models ?? []).map((m) => <option key={m} value={m} />)}</datalist>
          </Field>
          <Field label="From OS"><Select value={f.from_os} onChange={(v) => setF({ ...f, from_os: v })} placeholder="-" options={os} /></Field>
          <Field label="To OS"><Select value={f.to_os} onChange={(v) => setF({ ...f, to_os: v })} placeholder="-" options={os} /></Field>
          <Field label="OS Type"><Input value={f.ostype} onChange={(e) => setF({ ...f, ostype: e.target.value })} placeholder="White95TtoV" className="font-[family-name:var(--font-mono)]" /></Field>
          <Btn kind="primary" busy={busy} onClick={add}>추가</Btn>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-line pt-4 text-[13px] text-ink-2">
          <span className="font-semibold">표 일괄 등록</span>
          <span className="text-ink-3">열 순서: Category / Model / From OS / To OS / OS Type — 같은 Model·버전·구분이면 OS Type 을 갱신합니다</span>
          <div className="flex-1" />
          <a href="/api/wl/rules/template.xlsx" className="font-semibold text-brand-ink hover:underline">양식 내려받기</a>
          <label className="cursor-pointer rounded-lg border border-line-strong bg-surface px-3 py-1.5 font-semibold hover:bg-surface-2">
            <input type="file" accept=".xlsx" className="hidden" onChange={(e) => { importFile(e.target.files?.[0]); e.target.value = ""; }} />
            .xlsx 올리기
          </label>
        </div>
      </Card>

      <Card pad={false} title={<>등록된 규칙 <Tag>{data?.rules.length ?? 0}</Tag></>}
            right={<Select className="w-32 py-1.5" value={cat} onChange={setCat} placeholder="전체" options={data?.categories ?? []} />}>
        {loading && !data ? <Skeleton rows={6} /> : !data?.rules.length ? <Empty>규칙이 없습니다</Empty> : (
          <table className="w-full text-[13px]">
            <thead className="bg-surface-2 text-left text-[12px] text-ink-3">
              <tr><th className="px-5 py-2">구분</th><th className="px-3 py-2">Model</th><th className="px-3 py-2">Android</th><th className="px-3 py-2">API</th><th className="px-3 py-2">OS Type</th><th /></tr>
            </thead>
            <tbody>
              {data.rules.map((r) => (
                <tr key={r.id} className="border-t border-line hover:bg-surface-2">
                  <td className="px-5 py-2"><span className={cx("rounded-md px-2 py-0.5 text-[11.5px] font-bold", CAT_TONE[r.category] ?? "bg-muted-soft text-ink-2")}>{r.category}</span></td>
                  <td className="px-3 py-2 font-semibold">{r.model}</td>
                  <td className="px-3 py-2">{r.from} → {r.to}</td>
                  <td className="px-3 py-2 text-ink-2">{r.api ?? "-"}</td>
                  <td className="px-3 py-2 font-[family-name:var(--font-mono)] text-[12.5px] text-ok">{r.ostype}</td>
                  <td className="px-3 py-2 text-right"><button onClick={() => remove(r)} className="rounded-md px-2 py-1 text-ink-3 hover:bg-err-soft hover:text-err">삭제</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

// ── 고객 포털 ────────────────────────────────────────────────────────────────

type PortalResp = { companies: Company[]; accounts: Account[]; initial_password: string; portal_url: string };
const ACCT_TONE: Record<string, string> = { active: "bg-ok-soft text-ok", invited: "bg-muted-soft text-ink-2", suspended: "bg-err-soft text-err" };

export function PortalTab() {
  const toast = useToast();
  const { ask, node: confirmNode } = useConfirm();
  const { data, loading, reload } = useApi<PortalResp>("/api/wl/portal");
  const [nc, setNc] = useState({ name: "", email_domain: "" });
  const [open, setOpen] = useState<number | null>(null);
  const [pw, setPw] = useState<{ title: string; user: string; pw: string } | null>(null);

  async function run<T extends { message?: string; password?: string }>(p: Promise<T>, after?: (r: T) => void) {
    try { const r = await p; if (r.message) toast("ok", r.message); after?.(r); reload(); } catch (e) { toast("error", errText(e)); }
  }

  if (loading && !data) return <Card pad={false}><Skeleton rows={5} /></Card>;
  if (!data) return null;
  const byComp: Record<number, Account[]> = {};
  data.accounts.forEach((a) => { (byComp[a.company_id] ??= []).push(a); });

  return (
    <div className="space-y-4">
      {confirmNode}
      <Card title="고객사 추가">
        <div className="grid items-end gap-3 sm:grid-cols-[1fr_1fr_auto]">
          <Field label="이름"><Input value={nc.name} onChange={(e) => setNc({ ...nc, name: e.target.value })} placeholder="Guard1" /></Field>
          <Field label="이메일 도메인(선택)" ><Input value={nc.email_domain} onChange={(e) => setNc({ ...nc, email_domain: e.target.value })} placeholder="guard1.com" /></Field>
          <Btn kind="primary" onClick={() => run(post<{ message?: string }>("/api/wl/portal/companies", nc), () => setNc({ name: "", email_domain: "" }))}>추가</Btn>
        </div>
        <div className="mt-2 text-[12px] text-ink-3">도메인은 비워 둘 수 있습니다 — 개인 메일로 요청하는 고객이 있습니다. 포털 주소 {data.portal_url}</div>
      </Card>

      <div className="text-[12.5px] text-ink-3">고객사 {data.companies.length}곳 · 계정 {data.accounts.length}개</div>
      {data.companies.length === 0 ? <Card><Empty>등록된 고객사가 없습니다</Empty></Card> : data.companies.map((c) => (
        <CompanyCard key={c.id} c={c} accts={byComp[c.id] ?? []} open={open === c.id} onToggle={() => setOpen(open === c.id ? null : c.id)}
          initial={data.initial_password} run={run} ask={ask}
          onPw={(title, user, p) => setPw({ title, user, pw: p })} />
      ))}

      <Modal open={!!pw} onClose={() => setPw(null)} title={pw?.title}
             footer={<Btn kind="primary" onClick={() => setPw(null)}>닫기</Btn>}>
        <div className="space-y-3 text-[13.5px]">
          <div className="text-ink-2">아이디와 초기 비밀번호를 고객에게 전달합니다.</div>
          <div className="flex items-center gap-3 rounded-xl bg-surface-2 px-4 py-3">
            <span className="font-[family-name:var(--font-mono)]">{pw?.user}</span><span className="text-ink-3">/</span>
            <span className="font-[family-name:var(--font-mono)] text-[18px] font-bold">{pw?.pw}</span>
            <div className="flex-1" />
            <Btn small kind="ghost" onClick={() => { navigator.clipboard?.writeText(pw?.pw ?? ""); toast("ok", "복사했습니다"); }}>복사</Btn>
          </div>
        </div>
      </Modal>
    </div>
  );
}

function CompanyCard({ c, accts, open, onToggle, initial, run, ask, onPw }: {
  c: Company; accts: Account[]; open: boolean; onToggle: () => void; initial: string;
  run: <T extends { message?: string; password?: string }>(p: Promise<T>, after?: (r: T) => void) => Promise<void>;
  ask: (t: string, b: React.ReactNode, go: () => void, label?: string) => void;
  onPw: (title: string, user: string, pw: string) => void;
}) {
  const [edit, setEdit] = useState({ name: c.name, email_domain: c.email_domain });
  const [na, setNa] = useState({ username: "", email: "" });
  type R = { message?: string; password?: string };
  return (
    <Card pad={false}>
      <button onClick={onToggle} className="flex w-full flex-wrap items-center gap-3 px-5 py-3.5 text-left hover:bg-surface-2">
        <span className="min-w-[10rem] text-[14.5px] font-bold">{c.name}</span>
        <span className="font-[family-name:var(--font-mono)] text-[12.5px] text-ink-3">{c.email_domain || "— 도메인 없음"}</span>
        <Tag>계정 {c.accounts}</Tag><Tag>연결된 요청 {c.requests}</Tag>
        {!c.active && <Tag tone="error">비활성</Tag>}
        <span className={cx("ml-auto text-ink-3 transition", open && "rotate-90")}>›</span>
      </button>
      {open && (
        <div className="space-y-4 border-t border-line px-5 py-4">
          <div>
            <div className="mb-2 text-[13px] font-bold">계정</div>
            {!accts.length && <div className="text-[12.5px] text-ink-3">발급된 계정이 없습니다</div>}
            <div className="divide-y divide-line rounded-xl border border-line">
              {accts.map((a) => (
                <div key={a.id} className="flex flex-wrap items-center gap-3 px-3 py-2 text-[13px]">
                  <span className="min-w-[8rem] font-[family-name:var(--font-mono)]">{a.username}</span>
                  <span className="min-w-[12rem] font-[family-name:var(--font-mono)] text-[12px] text-ink-3">{a.email || "—"}</span>
                  <span className="text-[12px] text-ink-3">최근 로그인 {a.last_login_at || "없음"}</span>
                  <div className="flex-1" />
                  {a.must_change && <Tag tone="warn">초기 비밀번호</Tag>}
                  <span className={cx("rounded-full px-2 py-0.5 text-[11px] font-bold", ACCT_TONE[a.status] ?? "bg-muted-soft text-ink-2")}>{a.status_label}</span>
                  <Btn small kind="ghost" onClick={() => run<R>(post(`/api/wl/portal/accounts/${a.id}/reset`), (r) => r.password && onPw("비밀번호 초기화", a.username, r.password))}>비밀번호 초기화</Btn>
                  <Btn small kind="ghost" onClick={() => run<R>(post(`/api/wl/portal/accounts/${a.id}/status`, { status: a.status === "suspended" ? "active" : "suspended" }))}>{a.status === "suspended" ? "다시 활성화" : "중지"}</Btn>
                  <Btn small kind="ghost" onClick={() => ask("계정 삭제", <>{a.username} 계정을 지웁니다.</>, () => run<R>(del(`/api/wl/portal/accounts/${a.id}`)), "삭제")}>삭제</Btn>
                </div>
              ))}
            </div>
            <div className="mt-3 grid items-end gap-3 sm:grid-cols-[1fr_1fr_auto]">
              <Field label="아이디"><Input value={na.username} onChange={(e) => setNa({ ...na, username: e.target.value })} placeholder="guard1" /></Field>
              <Field label="이메일(선택)"><Input value={na.email} onChange={(e) => setNa({ ...na, email: e.target.value })} placeholder="whitelist@guard1.com" /></Field>
              <Btn kind="primary" onClick={() => run<R>(post(`/api/wl/portal/companies/${c.id}/accounts`, na), (r) => { if (r.password) onPw("계정 발급 완료", na.username.toLowerCase(), r.password); setNa({ username: "", email: "" }); })}>계정 발급</Btn>
            </div>
            <div className="mt-1.5 text-[12px] text-ink-3">초기 비밀번호 {initial} · 고객은 첫 로그인 때 바꿉니다</div>
          </div>
          <div className="border-t border-line pt-4">
            <div className="mb-2 text-[13px] font-bold">고객사 정보</div>
            <div className="grid items-end gap-3 sm:grid-cols-[1fr_1fr_auto_auto_auto]">
              <Field label="이름"><Input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
              <Field label="이메일 도메인"><Input value={edit.email_domain} onChange={(e) => setEdit({ ...edit, email_domain: e.target.value })} /></Field>
              <Btn kind="primary" onClick={() => run<R>(post(`/api/wl/portal/companies/${c.id}`, edit))}>저장</Btn>
              <Btn onClick={() => run<R>(post(`/api/wl/portal/companies/${c.id}`, { active: !c.active }))}>{c.active ? "비활성으로" : "활성으로"}</Btn>
              <Btn kind="ghost" onClick={() => ask("고객사 삭제", <>{c.name} 을(를) 지웁니다. 소속 계정이 있으면 지워지지 않습니다.</>, () => run<R>(del(`/api/wl/portal/companies/${c.id}`)), "삭제")}>삭제</Btn>
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}

// ── DQA 발신자 ────────────────────────────────────────────────────────────────

type Staff = { id: number; name: string; email: string; status: string; is_dqa: boolean; team: string };

export function DqaSendersTab() {
  const toast = useToast();
  const { data, loading, reload } = useApi<{ staff: Staff[]; domains: string[] }>("/api/wl/dqa-senders");
  const [q, setQ] = useState("");
  async function flip(s: Staff) {
    try { await post(`/api/wl/dqa-senders/${s.id}`, { is_dqa: !s.is_dqa }); toast("ok", `${s.name || s.email} · DQA ${!s.is_dqa ? "지정" : "해제"}`); reload(); }
    catch (e) { toast("error", errText(e)); }
  }
  if (loading && !data) return <Card pad={false}><Skeleton rows={6} /></Card>;
  const list = (data?.staff ?? []).filter((s) => !q || `${s.name} ${s.email} ${s.team}`.toLowerCase().includes(q.toLowerCase()));
  const dqa = (data?.staff ?? []).filter((s) => s.is_dqa);
  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
      <Card pad={false} title="사내 인원" right={<Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="이름 · 메일 · 팀" className="w-56 py-1.5" />}>
        {!list.length ? <Empty>없음</Empty> : list.map((s) => (
          <div key={s.id} className="flex items-center gap-3 border-b border-line px-5 py-2.5 last:border-b-0">
            <span className="min-w-[6rem] font-semibold">{s.name || "-"}</span>
            <span className="min-w-0 flex-1 truncate font-[family-name:var(--font-mono)] text-[12.5px] text-ink-3">{s.email}</span>
            {s.team && <Tag>{s.team}</Tag>}
            {s.status !== "active" && <Tag>{s.status}</Tag>}
            <button onClick={() => flip(s)} className={cx("rounded-full px-3 py-1 text-[12px] font-bold transition",
              s.is_dqa ? "bg-warn text-white" : "bg-muted-soft text-ink-3 hover:text-ink-2")}>DQA</button>
          </div>
        ))}
      </Card>
      <Card title={<>DQA 발신자 <Tag>{dqa.length}</Tag></>}>
        <div className="space-y-1 text-[13px]">
          {dqa.map((s) => <div key={s.id} className="flex gap-2"><span className="font-semibold">{s.name}</span><span className="truncate text-ink-3">{s.email}</span></div>)}
          {!dqa.length && <div className="text-ink-3">지정된 사람이 없습니다</div>}
        </div>
        {(data?.domains.length ?? 0) > 0 && (
          <div className="mt-4 border-t border-line pt-3 text-[12.5px] text-ink-2">도메인 전체 DQA: {data!.domains.join(", ")}</div>
        )}
        <div className="mt-4 border-t border-line pt-3 text-[12.5px] text-ink-3">DQA 발신자가 보낸 요청은 구분이 DQA 로 잡히고, FOTA 테스트 요청이면 DQA FOTA Test 처리로 열립니다.</div>
      </Card>
    </div>
  );
}
