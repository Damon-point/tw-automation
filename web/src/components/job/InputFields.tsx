"use client";

import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/Icons";
import { Tag, cx } from "@/components/ui";
import type { InputField } from "@/lib/job";
import { Btn, inputCls } from "./kit";

export type LiveField = InputField & { uid: string };

/** 입력 값 칸(GMS/SDK/Audio/Touch/Wi-Fi/보안패치…) — 선별표와 함께 저장된다.
 *  값을 고치면 문서에서 그 항목 위에 찍히는 판(◼)이 이번 판으로 바뀐다. */
export default function InputFields({ fields, orig, editable, nowVersion, locked, onChange }: {
  fields: LiveField[]; orig: Record<string, string>; editable: boolean; nowVersion: string; locked: boolean;
  onChange: (f: LiveField[]) => void;
}) {
  const [name, setName] = useState("");
  const empty = fields.filter((f) => f.required && !f.value.trim());

  function set(uid: string, value: string) {
    onChange(fields.map((f) => (f.uid === uid ? { ...f, value } : f)));
  }
  function add() {
    const label = name.replace(/^\s*\d{1,2}\.\s*/, "").trim();
    if (!label) return;
    onChange([...fields, { uid: `new-${Date.now()}`, key: "", label, required: false, value: "", manual: true, version: "" }]);
    setName("");
  }

  return (
    <section className="mb-5 overflow-hidden rounded-2xl border border-line bg-surface">
      <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
        <h2 className="text-[15px] font-bold">입력 값</h2>
        <span className="text-[12.5px] text-ink-3">GMS · SDK · Audio · Touch · Wi-Fi · 보안패치 — 단말에서 확인한 값</span>
        {empty.length > 0 && <span className="ml-auto text-[12.5px] text-warn">빈 칸 {empty.length} — 비운 채 저장하면 RN 의 그 칸이 비어 나갑니다</span>}
      </header>
      {editable && !locked && (
        <div className="border-b border-line bg-info-soft/50 px-4 py-2 text-[12.5px] text-info">
          최초 버전이라 참고할 이전 RN 이 없습니다. 이 제품에 없는 항목은 빼고, 빠진 항목은 더할 수 있습니다.
        </div>
      )}
      <div className="divide-y divide-line">
        {fields.map((f) => {
          const was = orig[f.uid];
          const changed = was !== undefined ? was !== f.value : !!f.value.trim();
          const ver = !f.value.trim() ? "—" : changed ? nowVersion : (f.version || nowVersion);
          return (
            <div key={f.uid} className="grid gap-2 px-4 py-3 md:grid-cols-[200px_minmax(0,1fr)_150px] md:items-start">
              <div className="pt-1.5">
                <div className="text-[13px] font-semibold">{f.label}</div>
                <div className="mt-0.5 flex flex-wrap gap-1">
                  {f.manual && !changed && <Tag>직접 입력</Tag>}
                  {changed && <Tag tone="warn">수정</Tag>}
                  {!f.key && <Tag tone="ok">새 항목</Tag>}
                </div>
              </div>
              <Grow value={f.value} disabled={locked} warn={f.required && !f.value.trim()} onChange={(v) => set(f.uid, v)} />
              <div className="flex items-start justify-between gap-2 pt-2 md:justify-end">
                <span className="whitespace-nowrap font-mono text-[12px] text-ink-3" title="문서에서 이 항목 위에 찍히는 판">◼ {ver}</span>
                {editable && !locked && (
                  <button onClick={() => onChange(fields.filter((x) => x.uid !== f.uid))} aria-label={`${f.label} 빼기`}
                          className="rounded-md p-1 text-ink-3 hover:bg-err-soft hover:text-err">✕</button>
                )}
              </div>
            </div>
          );
        })}
      </div>
      {editable && !locked && (
        <div className="flex gap-2 border-t border-line bg-surface-2 px-4 py-3">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="항목 추가 — 예: Scanner version information"
                 onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }} className={cx(inputCls, "flex-1")} />
          <Btn onClick={add} disabled={!name.trim()}><Icon name="check" className="h-4 w-4" />추가</Btn>
        </div>
      )}
    </section>
  );
}

function Grow({ value, onChange, disabled, warn }: { value: string; onChange: (v: string) => void; disabled?: boolean; warn?: boolean }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (el) { el.style.height = "auto"; el.style.height = `${el.scrollHeight + 2}px`; }
  }, [value]);
  return (
    <textarea ref={ref} rows={1} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}
              placeholder={warn ? "입력 필요" : "여러 줄이면 줄마다 글머리가 붙습니다"}
              className={cx(inputCls, "resize-none overflow-hidden py-1.5 font-mono text-[13px] leading-relaxed",
                warn && "border-warn/70 bg-warn-soft/40")} />
  );
}
