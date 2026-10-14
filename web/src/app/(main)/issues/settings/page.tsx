"use client";

import { useEffect, useState } from "react";
import { api, useApi } from "@/lib/api";
import type { IssueOverview, SbSettings } from "@/lib/issues";
import { Card, cx, Skeleton } from "@/components/ui";
import { Btn, useToast } from "@/components/issues/kit";

const input = "w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-[14px] outline-none focus:border-brand disabled:bg-surface-2 disabled:text-ink-2";

/** Solution Buffer 동작 설정 — 값은 issuetrack.db 에 남고 다음 수집 주기부터 듣는다. 고치기는 관리자만. */
export default function SbSettingsPage() {
  const st = useApi<SbSettings>("/api/issues/settings");
  const ov = useApi<IssueOverview>("/api/issues/overview");
  const toast = useToast();
  const admin = !!ov.data?.admin;
  const [form, setForm] = useState({ sb_auto_notify: false, sb_assignees: "", exclude_keywords: "" });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (st.data) setForm({ sb_auto_notify: st.data.sb_auto_notify, sb_assignees: st.data.sb_assignees, exclude_keywords: st.data.exclude_keywords });
  }, [st.data]);

  const dirty = !!st.data && (form.sb_auto_notify !== st.data.sb_auto_notify || form.sb_assignees !== st.data.sb_assignees || form.exclude_keywords !== st.data.exclude_keywords);

  async function save() {
    setBusy(true);
    try {
      const r = await api<{ message: string }>("/api/issues/settings", { method: "POST", body: JSON.stringify(form) });
      toast(r.message);
      st.reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  if (!st.data) return <Card>{st.error ? <p className="text-[13px] text-err">{st.error}</p> : <Skeleton rows={4} />}</Card>;
  const d = st.data;

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
      <Card title="Solution Buffer 동작" right={!admin && <span className="text-[12px] text-ink-3">관리자만 바꿀 수 있음</span>}>
        <div className="space-y-5">
          <div className="flex items-start gap-3">
            <button type="button" role="switch" aria-checked={form.sb_auto_notify} disabled={!admin}
                    onClick={() => setForm({ ...form, sb_auto_notify: !form.sb_auto_notify })}
                    className={cx("relative mt-0.5 h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-60", form.sb_auto_notify ? "bg-brand" : "bg-line-strong")}>
              <span className={cx("absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all", form.sb_auto_notify ? "left-[22px]" : "left-0.5")} />
            </button>
            <div>
              <div className="text-[14px] font-semibold">담당자에게 검토 요청 메일 자동 발송</div>
              <div className="mt-0.5 text-[12.5px] text-ink-3">
                Resolved 로 초안이 만들어지는 즉시 Redmine 담당자에게 메일이 나간다 · 끄면 문서 화면에서 사람이 보낸다 · 소급분은 자동 발송하지 않는다
              </div>
            </div>
          </div>

          <label className="block">
            <span className="mb-1 block text-[13px] font-semibold">SB 대상 담당자</span>
            <input className={input} disabled={!admin} value={form.sb_assignees} onChange={(e) => setForm({ ...form, sb_assignees: e.target.value })} placeholder="쉼표로 구분 · 비우면 모든 담당자" />
            <span className="mt-1 block text-[12px] text-ink-3">여기 적힌 담당자의 이슈만 문서를 만들고 메일을 보낸다 · 빠진 문서는 &lsquo;SB 대상 아님&rsquo;으로 남고 다시 넣으면 되살아난다</span>
          </label>

          <label className="block">
            <span className="mb-1 block text-[13px] font-semibold">감시 제외 키워드</span>
            <input className={input} disabled={!admin} value={form.exclude_keywords} onChange={(e) => setForm({ ...form, exclude_keywords: e.target.value })} placeholder="쉼표로 구분" />
            <span className="mt-1 block text-[12px] text-ink-3">제목에 이 단어가 든 이슈는 감시에서 뺀다 · 삭제가 아니라 표시라 규칙을 바꾸면 다음 수집 때 되살아난다</span>
          </label>

          {admin && (
            <div className="flex items-center gap-2 border-t border-line pt-4">
              <Btn kind="primary" onClick={save} busy={busy} disabled={!dirty}>저장</Btn>
              {dirty && <Btn onClick={() => setForm({ sb_auto_notify: d.sb_auto_notify, sb_assignees: d.sb_assignees, exclude_keywords: d.exclude_keywords })}>되돌리기</Btn>}
              <span className="text-[12px] text-ink-3">다음 수집 주기(최대 5분)부터 적용</span>
            </div>
          )}
        </div>
      </Card>

      <div className="space-y-4">
        <Card title="고정 값">
          <dl className="grid grid-cols-[1fr_auto] gap-y-2 text-[13px]">
            <dt className="text-ink-3">초안 자동 생성</dt><dd className="font-semibold">{d.sb_auto_generate ? "켬" : "끔"}</dd>
            <dt className="text-ink-3">주기당 생성 건수</dt><dd className="font-semibold tabular-nums">{d.sb_per_tick}</dd>
            <dt className="text-ink-3">소급 기준일</dt><dd className="font-semibold tabular-nums">{d.sb_cutoff || "—"}</dd>
          </dl>
          <p className="mt-3 text-[12px] text-ink-3">env TW_SB_AUTO_GENERATE · TW_SB_PER_TICK · TW_SB_CUTOFF 로 바꾼다</p>
        </Card>
        {d.without_account.length > 0 && (
          <Card title="앱 계정이 없는 담당자">
            <p className="mb-2 text-[12px] text-ink-3">담당 문서가 있지만 로그인할 계정이 없다</p>
            <ul className="space-y-1 text-[13px]">
              {d.without_account.map((w) => (
                <li key={w.name} className="flex justify-between"><span>{w.name}</span><span className="tabular-nums text-ink-3">{w.count}건</span></li>
              ))}
            </ul>
          </Card>
        )}
      </div>
    </div>
  );
}
