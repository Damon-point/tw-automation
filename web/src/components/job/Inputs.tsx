"use client";

import { useEffect, useState } from "react";
import { Icon } from "@/components/Icons";
import { Card, Tag, cx } from "@/components/ui";
import { useApi } from "@/lib/api";
import { jobApi, type Inputs } from "@/lib/job";
import { Btn, Field, inputCls, useAction } from "./kit";

/** 단계가 읽어 가는 입력값 — 비워 두면 자동. 채운 것만 표시가 붙는다 */
export function InputsCard({ jobId, onSaved }: { jobId: number; onSaved: () => void }) {
  const { data, reload } = useApi<Inputs>(`/api/jobs/${jobId}/inputs`);
  const [open, setOpen] = useState<string>("");
  if (!data) return null;
  const reloadAll = () => { reload(); onSaved(); };
  const filledComps = data.components.fields.filter((f) => f.value).length;
  const sections = [
    { id: "ver", title: "OS Download 버전", state: data.os_version.value || (data.os_version.inherited ? `지정값 ${data.os_version.inherited}` : "자동"),
      on: !!data.os_version.value, show: data.os_version.editable || !!data.os_version.value },
    { id: "link", title: "Release Note 링크", state: data.rn_link.manual ? "직접 지정" : "자동", on: !!data.rn_link.manual, show: true },
    { id: "comp", title: "RN 컴포넌트 값", state: filledComps ? `${filledComps}개 입력` : "비어 있음", on: filledComps > 0, show: data.components.editable || filledComps > 0 },
    { id: "draft", title: "Revision History 초안", state: data.revision_draft.value ? "입력됨" : "자동", on: !!data.revision_draft.value, show: data.revision_draft.editable || !!data.revision_draft.value },
  ].filter((s) => s.show);

  return (
    <Card title="입력값" icon={<Icon name="file" />} pad={false}>
      {sections.map((s) => (
        <div key={s.id} className="border-b border-line last:border-b-0">
          <button onClick={() => setOpen(open === s.id ? "" : s.id)} className="flex w-full items-center gap-2 px-5 py-3 text-left hover:bg-surface-2">
            <span className="flex-1 text-[13.5px] font-semibold">{s.title}</span>
            <Tag tone={s.on ? "ok" : "none"}>{s.state}</Tag>
            <Icon name="chevron" className={cx("h-4 w-4 text-ink-3 transition-transform", open === s.id && "rotate-90")} />
          </button>
          {open === s.id && (
            <div className="px-5 pb-4">
              {s.id === "ver" && <VersionForm jobId={jobId} d={data} done={reloadAll} />}
              {s.id === "link" && <LinkForm jobId={jobId} d={data} done={reloadAll} />}
              {s.id === "comp" && <ComponentsForm jobId={jobId} d={data} done={reloadAll} />}
              {s.id === "draft" && <DraftForm jobId={jobId} d={data} done={reloadAll} />}
            </div>
          )}
        </div>
      ))}
    </Card>
  );
}

type F = { jobId: number; d: Inputs; done: () => void };

function VersionForm({ jobId, d, done }: F) {
  const [v, setV] = useState(d.os_version.value);
  const { busy, run } = useAction(done);
  return (
    <div className="space-y-2">
      <Field label="받을 정식 OS 버전" hint={d.os_version.inherited ? `비우면 ${d.os_version.inherited} 을(를) 받습니다` : "비우면 출시 이력으로 자동 판별합니다"}>
        <input value={v} onChange={(e) => setV(e.target.value)} disabled={!d.os_version.editable} className={cx(inputCls, "font-mono")} placeholder={d.os_version.inherited || "예: 95.07"} />
      </Field>
      {d.os_version.editable && <Btn size="sm" kind="primary" busy={!!busy} onClick={() => run("s", () => jobApi(jobId).setOsVersion(v))}>저장</Btn>}
    </div>
  );
}

function LinkForm({ jobId, d, done }: F) {
  const [v, setV] = useState(d.rn_link.manual);
  const { busy, run } = useAction(done);
  return (
    <div className="space-y-2">
      <div className="text-[12px] text-ink-3">자동: Newsletter 제작이 RN for Sales 를 Dropbox 에 올려 얻는 링크</div>
      {d.rn_link.auto && <a href={d.rn_link.auto} target="_blank" rel="noreferrer" className="block truncate text-[12.5px] text-brand-ink hover:underline">{d.rn_link.auto}</a>}
      <Field label="다른 주소를 쓸 때만">
        <input value={v} onChange={(e) => setV(e.target.value)} className={inputCls} placeholder="https://…" />
      </Field>
      <div className="flex gap-2">
        <Btn size="sm" kind="primary" busy={busy === "s"} onClick={() => run("s", () => jobApi(jobId).setState("rn_confluence_link", v), () => v ? "링크를 지정했습니다" : "자동 링크로 되돌렸습니다")}>저장</Btn>
        {d.rn_link.manual && <Btn size="sm" kind="ghost" busy={busy === "c"} onClick={() => { setV(""); run("c", () => jobApi(jobId).setState("rn_confluence_link", ""), () => "자동 링크로 되돌렸습니다"); }}>지정 지우기</Btn>}
      </div>
    </div>
  );
}

function ComponentsForm({ jobId, d, done }: F) {
  const [vals, setVals] = useState<Record<string, string>>({});
  useEffect(() => setVals(Object.fromEntries(d.components.fields.map((f) => [f.key, f.value]))), [d]);
  const { busy, run } = useAction(done);
  const ed = d.components.editable;
  return (
    <div className="space-y-2">
      <div className="text-[12px] text-ink-3">단말에서만 확인할 수 있는 값. 비우면 RN 에 빈칸으로 남습니다.</div>
      <div className="grid grid-cols-2 gap-2">
        {d.components.fields.filter((f) => f.key !== "known_issues").map((f) => (
          <Field key={f.key} label={f.label}>
            <input value={vals[f.key] ?? ""} disabled={!ed} onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} className={inputCls} />
          </Field>
        ))}
      </div>
      <Field label="Known Issues">
        <textarea rows={3} value={vals.known_issues ?? ""} disabled={!ed} onChange={(e) => setVals({ ...vals, known_issues: e.target.value })} className={inputCls} />
      </Field>
      {ed && <Btn size="sm" kind="primary" busy={!!busy} onClick={() => run("s", () => jobApi(jobId).setComponents(vals))}>저장</Btn>}
    </div>
  );
}

function DraftForm({ jobId, d, done }: F) {
  const [v, setV] = useState(d.revision_draft.value);
  const { busy, run } = useAction(done);
  return (
    <div className="space-y-2">
      <Field label="한 줄에 한 항목" hint="채우면 전체 이력 RN 제작이 이 초안을 그대로 Revision History 로 씁니다">
        <textarea rows={6} value={v} disabled={!d.revision_draft.editable} onChange={(e) => setV(e.target.value)} className={cx(inputCls, "font-mono text-[12.5px]")} />
      </Field>
      {d.revision_draft.editable && <Btn size="sm" kind="primary" busy={!!busy} onClick={() => run("s", () => jobApi(jobId).setRevisionDraft(v))}>저장</Btn>}
    </div>
  );
}
