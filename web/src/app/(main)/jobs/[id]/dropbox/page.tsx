"use client";

import { use, useState } from "react";
import { Card, Skeleton, cx } from "@/components/ui";
import { Banner, Btn, Confirm, EditorHead, ToastHost, useToast } from "@/components/osu/kit";
import { useApi } from "@/lib/api";
import { errText, post, type DbxGroup } from "@/lib/osu";

type Resp = { error: string; groups: DbxGroup[] };
const MAX = 200;

export default function DropboxPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <ToastHost><Cleanup id={id} /></ToastHost>;
}

function Cleanup({ id }: { id: string }) {
  const toast = useToast();
  const res = useApi<Resp>(`/api/jobs/${id}/dropbox`);
  const [target, setTarget] = useState<DbxGroup | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");

  async function remove() {
    if (!target) return;
    setBusy(true);
    try {
      const r = await post<{ ok: boolean; result: string }>(`/api/jobs/${id}/dropbox/delete`, { key: target.key });
      setResult(r.result);
      toast(r.ok ? "ok" : "err", r.result.split("\n")[0]);
      setTarget(null);
      await res.reload();
    } catch (e) {
      toast("err", errText(e));
    } finally {
      setBusy(false);
    }
  }

  const groups = res.data?.groups ?? [];
  const total = groups.reduce((n, g) => n + g.files.length, 0);
  const unsafe = groups.some((g) => g.unsafe);
  return (
    <>
      <EditorHead jobId={id} title="Dropbox 구버전 정리" sub="제품 관리에 등록한 Dropbox 경로마다, 이번 회차에 올리지 않은 파일을 모았습니다. 지우는 것은 경로별 버튼으로만 합니다."
                  right={<Btn kind="ghost" busy={res.loading} onClick={res.reload}>다시 읽기</Btn>} />
      {res.loading ? <Skeleton rows={4} /> : res.data?.error ? <Banner tone="err" title="목록을 읽지 못했습니다">{res.data.error}</Banner> : (
        <div className="space-y-4">
          <div className="text-[14px] font-semibold">구버전 파일 {total}개 · 경로 {groups.length}개</div>
          {unsafe && <Banner tone="err" title="삭제를 막았습니다">Dropbox 업로드 단계의 기록이 없어 아래 목록에 이번 회차 파일이 섞여 있을 수 있습니다.</Banner>}
          {result && <Card title="마지막 삭제 결과" right={<Btn kind="ghost" onClick={() => setResult("")}>닫기</Btn>}><pre className="whitespace-pre-wrap text-[12.5px]">{result}</pre></Card>}
          {groups.map((g) => (
            <Card key={g.key} pad={false} className={cx("border-l-4", g.files.length ? "border-l-err" : "border-l-line-strong")}
                  title={<span>{g.label} <span className="ml-1 text-[12.5px] font-normal text-ink-3">{g.files.length}개</span></span>}
                  right={g.files.length > 0 && (g.unsafe ? <span className="text-[12px] text-ink-3">삭제 잠김</span> : <Btn kind="danger" onClick={() => setTarget(g)}>이 경로 구버전 삭제</Btn>)}>
              <div className="border-b border-line px-5 py-2 font-mono text-[12px] text-ink-3">{g.base || "(경로 미등록)"}</div>
              {g.skipped ? <div className="px-5 py-3 text-[13px] text-ink-3">{g.skipped}</div>
                : !g.files.length ? <div className="px-5 py-3 text-[13px] text-ink-3">구버전 없음</div>
                : (
                  <ul className="max-h-[260px] overflow-auto px-5 py-2 font-mono text-[12px] text-ink-2">
                    {g.files.slice(0, MAX).map((f) => <li key={f} className="truncate py-0.5" title={f}>{f}</li>)}
                    {g.files.length > MAX && <li className="py-0.5 text-ink-3">… 외 {g.files.length - MAX}개</li>}
                  </ul>
                )}
            </Card>
          ))}
        </div>
      )}
      <Confirm open={!!target} title={`${target?.label ?? ""} — 구버전 삭제`} danger busy={busy} confirmLabel={`${target?.files.length ?? 0}개 삭제`} onConfirm={remove} onClose={() => setTarget(null)}>
        <p className="mb-2 font-mono text-[12px] text-ink-3">{target?.base}</p>
        <p className="mb-3 font-semibold text-err">아래 {target?.files.length}개를 Dropbox 에서 지웁니다. 되돌릴 수 없습니다.</p>
        <ul className="max-h-[34vh] overflow-auto rounded-lg bg-surface-2 px-3 py-2 font-mono text-[12px]">
          {target?.files.map((f) => <li key={f} className="truncate">{f.split("/").pop()}</li>)}
        </ul>
      </Confirm>
    </>
  );
}
