"use client";

import { use, useEffect, useState } from "react";
import { Card, Skeleton, cx } from "@/components/ui";
import { Banner, Btn, EditorHead, Modal, ToastHost, useToast } from "@/components/osu/kit";
import { useApi } from "@/lib/api";
import { errText, post, type Fota, type FotaPlan, type FotaReport } from "@/lib/osu";

export default function FotaPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <ToastHost><Editor id={id} /></ToastHost>;
}

function Editor({ id }: { id: string }) {
  const toast = useToast();
  const res = useApi<Fota>(`/api/jobs/${id}/fota`);
  const [oi, setOi] = useState<string[][]>([]);
  const [dp, setDp] = useState<string[][]>([]);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<"" | "save" | "dry" | "plan" | "reg">("");
  const [report, setReport] = useState<{ title: string; ok: boolean; text: string } | null>(null);
  const [plan, setPlan] = useState<FotaPlan | null>(null);
  const [force, setForce] = useState(false);

  useEffect(() => {
    if (!res.data) return;
    setOi(res.data.osinfo.map((r) => [...r]));
    setDp(res.data.dp.map((r) => [...r]));
    setDirty(false);
  }, [res.data]);

  async function save() {
    setBusy("save");
    try {
      const r = await post<{ message: string }>(`/api/jobs/${id}/fota/rows`, { osinfo: oi, dp });
      toast("ok", r.message);
      await res.reload();
    } catch (e) {
      toast("err", errText(e));
    } finally {
      setBusy("");
    }
  }
  async function dryrun() {
    setBusy("dry");
    try {
      const r = await post<FotaReport>(`/api/jobs/${id}/fota/dryrun`);
      setReport({ title: "등록 미리보기 (dry-run)", ok: r.ok, text: r.report });
    } catch (e) {
      toast("err", errText(e));
    } finally {
      setBusy("");
    }
  }
  async function openPlan() {
    setBusy("plan");
    setForce(false);
    try {
      setPlan(await fetch(`/api/jobs/${id}/fota/plan`, { cache: "no-store" }).then(async (x) => {
        if (!x.ok) throw new Error((await x.json()).error ?? x.statusText);
        return x.json();
      }));
    } catch (e) {
      toast("err", errText(e));
    } finally {
      setBusy("");
    }
  }
  async function register() {
    setBusy("reg");
    try {
      const r = await post<FotaReport>(`/api/jobs/${id}/fota/register`, { force });
      setPlan(null);
      setReport({ title: `실제 등록 · ${r.mode} · ${r.ok ? "성공" : "오류"}`, ok: r.ok, text: r.report });
      if (r.ok) toast("ok", `FOTA 등록을 마쳤습니다 — 확인된 OsInfo ${r.recorded_osinfo ?? 0}건 · DownloadPath ${r.recorded_dp ?? 0}건을 기록했습니다`);
      else toast("err", "FOTA 등록 오류");
    } catch (e) {
      toast("err", errText(e));
    } finally {
      setBusy("");
    }
  }

  if (res.loading) return <Skeleton rows={6} />;
  const h = res.data?.headers;
  const nothing = !!plan && plan.ok && !plan.new_osinfo.length && !plan.new_dp;
  return (
    <>
      <EditorHead jobId={id} title="FOTA 등록" sub="표의 값이 그대로 FOTA 시스템에 등록됩니다."
                  right={<>
                    <Btn kind="primary" icon="check" busy={busy === "save"} disabled={!dirty} onClick={save}>{dirty ? "표 저장" : "저장됨"}</Btn>
                    <Btn icon="eye" busy={busy === "dry"} disabled={dirty || !oi.length} onClick={dryrun}>등록 미리보기</Btn>
                    <Btn kind="danger" icon="play" busy={busy === "plan"} disabled={dirty || !oi.length} onClick={openPlan}>FOTA에 등록</Btn>
                  </>} />
      {!oi.length && <div className="mb-4"><Banner tone="info" title="등록할 OsInfo 가 없습니다">FOTA 업로드 단계를 실행하면 행이 만들어집니다.</Banner></div>}
      <div className="space-y-5">
        <RowsTable title="OsInfo" headers={h?.osinfo ?? []} rows={oi} onChange={(r) => { setOi(r); setDirty(true); }} />
        <RowsTable title="DownloadPath" headers={h?.dp ?? []} rows={dp} onChange={(r) => { setDp(r); setDirty(true); }} canAdd />
        {report && (
          <Card title={report.title} right={<Btn kind="ghost" onClick={() => setReport(null)}>닫기</Btn>}>
            <pre className={cx("max-h-[420px] overflow-auto whitespace-pre-wrap font-mono text-[12.5px] leading-relaxed", report.ok ? "text-ink" : "text-err")}>{report.text}</pre>
          </Card>
        )}
      </div>

      <Modal open={!!plan} onClose={() => setPlan(null)} title="FOTA 실제 등록" wide
             footer={<><Btn kind="ghost" onClick={() => setPlan(null)}>취소</Btn><Btn kind="danger" busy={busy === "reg"} disabled={nothing && !force} onClick={register}>실제 등록</Btn></>}>
        {plan && (
          <div className="space-y-4">
            {!plan.ok && <Banner tone="warn" title="이미 등록됐는지 확인하지 못했습니다">{plan.error} — 이대로 보내면 중복이 생길 수 있습니다.</Banner>}
            <section>
              <h4 className="mb-1.5 text-[13px] font-bold">보낼 것</h4>
              {nothing ? <div className="text-[13px] text-ink-3">없음 — 모두 등록되어 있습니다</div> : (
                <ul className="space-y-1 font-mono text-[12.5px]">
                  {plan.new_osinfo.map((r, i) => <li key={i} className="rounded-md bg-surface-2 px-2.5 py-1">OsInfo · osv {r.os_version} · {r.version}</li>)}
                  {plan.new_dp > 0 && <li className="rounded-md bg-surface-2 px-2.5 py-1">DownloadPath {plan.new_dp}행</li>}
                </ul>
              )}
            </section>
            {(plan.dup_osinfo.length > 0 || plan.dup_dp.length > 0) && (
              <section>
                <h4 className="mb-1.5 text-[13px] font-bold text-ink-2">건너뛸 것 (이미 등록됨)</h4>
                <ul className="space-y-1 font-mono text-[12px] text-ink-3">
                  {plan.dup_osinfo.map((m, i) => <li key={i}>OsInfo · ID {m.id} · {m.version} · osv {m.os_version}</li>)}
                  {plan.dup_dp.slice(0, 6).map((m, i) => <li key={`d${i}`}>DownloadPath · ID {m.id} · osv {m.os_version} · {m.type}</li>)}
                  {plan.dup_dp.length > 6 && <li>… 외 {plan.dup_dp.length - 6}행</li>}
                </ul>
              </section>
            )}
            <label className="flex items-center gap-2 text-[13px]">
              <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
              이미 등록된 것도 다시 보내기 (OsInfo {plan.total_osinfo}행 · DownloadPath {plan.total_dp}행 전부)
            </label>
          </div>
        )}
      </Modal>
    </>
  );
}

function RowsTable({ title, headers, rows, onChange, canAdd }: { title: string; headers: string[]; rows: string[][]; onChange: (r: string[][]) => void; canAdd?: boolean }) {
  const set = (i: number, j: number, v: string) => onChange(rows.map((r, k) => (k === i ? r.map((c, l) => (l === j ? v : c)) : r)));
  return (
    <Card title={`${title} · ${rows.length}행`} pad={false}
          right={canAdd && rows.length > 0 && <Btn kind="ghost" onClick={() => onChange([...rows, [...rows[rows.length - 1]]])}>마지막 행 복제</Btn>}>
      {!rows.length ? <div className="px-5 py-6 text-[13px] text-ink-3">없음</div> : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-[12.5px]">
            <thead>
              <tr className="bg-surface-2 text-left text-ink-2">
                <th className="w-8 px-2 py-2" />
                {headers.map((hd) => <th key={hd} className="whitespace-nowrap px-2 py-2 font-semibold">{hd}</th>)}
                {canAdd && <th className="w-8" />}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className="border-t border-line align-top">
                  <td className="px-2 py-1.5 text-center text-ink-3">{i + 1}</td>
                  {headers.map((hd, j) => {
                    const v = r[j] ?? "";
                    const long = v.length > 40 || /url|note|description/i.test(hd);
                    return (
                      <td key={hd} className="px-1 py-1">
                        {long ? (
                          <textarea value={v} rows={Math.min(4, Math.max(1, Math.ceil(v.length / 48)))} onChange={(e) => set(i, j, e.target.value)}
                                    className="w-[300px] resize-y rounded-md border border-transparent bg-transparent px-1.5 py-1 font-mono text-[12px] outline-none hover:border-line focus:border-brand focus:bg-surface" />
                        ) : (
                          <input value={v} onChange={(e) => set(i, j, e.target.value)} size={Math.max(6, Math.min(24, v.length + 1))}
                                 className="rounded-md border border-transparent bg-transparent px-1.5 py-1 font-mono text-[12px] outline-none hover:border-line focus:border-brand focus:bg-surface" />
                        )}
                      </td>
                    );
                  })}
                  {canAdd && <td className="px-1 py-1"><Btn kind="ghost" className="text-err" onClick={() => onChange(rows.filter((_, k) => k !== i))}>✕</Btn></td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
