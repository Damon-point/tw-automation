"use client";

import { useMemo, useState } from "react";
import { Card, Tag } from "@/components/ui";
import { useApi } from "@/lib/api";
import { errText, post, upload, type Device, type Options } from "@/lib/whitelist";
import { Btn, Field, Note, Seg, Select, Table, Textarea, useToast } from "./kit";

export default function RegisterTab() {
  const toast = useToast();
  const opts = useApi<Options>("/api/wl/options").data;
  const [mode, setMode] = useState<"single" | "bulk">("single");
  const [snText, setSnText] = useState("");
  const [model, setModel] = useState("");
  const [cur, setCur] = useState("");
  const [upg, setUpg] = useState("");
  const [bulk, setBulk] = useState<Device[]>([]);
  const [staged, setStaged] = useState<Device[]>([]);
  const [charge, setCharge] = useState<"무상" | "유상">("무상");
  const [freeReason, setFreeReason] = useState("");
  const [memo, setMemo] = useState("");
  const [po, setPo] = useState<File | null>(null);
  const [busy, setBusy] = useState("");

  const preview = useMemo(() => {
    if (!opts || !model || !cur || !upg) return null;
    const r = opts.rules.find((x) => x.model === model && String(x.from) === cur && String(x.to) === upg && x.category === opts.default_category);
    return r ? { ok: true, text: `API ${opts.api[cur] ?? "-"} · End Date ${opts.end_date} · OS Type ${r.ostype}` }
      : { ok: false, text: `규칙 없음 (${model} ${cur}→${upg})` };
  }, [opts, model, cur, upg]);

  function onModel(m: string) {
    setModel(m);
    const rs = (opts?.rules ?? []).filter((r) => r.model === m && r.category === opts?.default_category);
    if (rs.length === 1) { setCur(String(rs[0].from)); setUpg(String(rs[0].to)); }
  }

  function addValid(devs: Device[], prefix = "") {
    const have = new Set(staged.map((d) => d.sn));
    let added = 0, dupes = 0;
    const next = [...staged];
    for (const d of devs) {
      if (!d.valid) continue;
      if (have.has(d.sn)) { dupes++; continue; }
      next.push(d); have.add(d.sn); added++;
    }
    setStaged(next);
    toast(added ? "ok" : "warn", `${prefix}${added}대 추가${dupes ? ` · ${dupes}대 중복 제외` : ""}`);
  }

  async function addSingle() {
    const sns = snText.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
    if (!sns.length) { toast("warn", "SN 을 입력하세요"); return; }
    try {
      const r = await post<{ devices: Device[] }>("/api/wl/build-devices", { sns, model, cur, upg });
      const bad = r.devices.filter((d) => !d.valid);
      addValid(r.devices);
      if (bad.length) toast("error", `오류 ${bad.length}건 제외: ${bad.slice(0, 3).map((d) => `${d.sn}(${d.errors[0]})`).join(", ")}`);
      setSnText(bad.map((d) => d.sn).join("\n"));
    } catch (e) { toast("error", errText(e)); }
  }

  async function onBulk(f: File | undefined) {
    if (!f) return;
    try {
      const r = await upload<{ devices: Device[] }>("/api/wl/bulk-parse", f);
      setBulk(r.devices);
      if (!r.devices.length) toast("warn", "데이터를 찾지 못했습니다");
    } catch (e) { toast("error", errText(e)); }
  }

  async function submit() {
    if (!staged.length) { toast("warn", "등록할 단말기를 추가하세요"); return; }
    setBusy("submit");
    try {
      const r = await post<{ request: { id: string; devices: unknown[] }; fota_ok: boolean; success: number; total: number;
                             fails: { sn?: string; msg?: string }[]; msg: string }>("/api/wl/requests",
        { devices: staged, charge_type: charge, free_reason: freeReason, memo });
      if (charge === "유상" && po) {
        try { await upload(`/api/wl/requests/${r.request.id}/po`, po); } catch (e) { toast("error", `PO 파일 저장 실패: ${errText(e)}`); }
      }
      if (r.fota_ok) {
        toast("ok", `FOTA 등록 완료 · ${r.success}/${r.total}대 · ${r.request.id}`);
        setStaged([]); setMemo(""); setFreeReason(""); setPo(null);
      } else {
        // 등록이 다 되지 않았다 — 신청은 '대기 중' 으로 남는다. 입력은 지우지 않는다.
        const why = r.fails.slice(0, 3).map((f) => `${f.sn ?? ""} ${f.msg ?? ""}`.trim()).join(", ") || r.msg;
        toast("error", `FOTA 등록 실패 · 성공 ${r.success}/${r.total}대 — ${why}`);
      }
    } catch (e) { toast("error", errText(e)); } finally { setBusy(""); }
  }

  const bulkValid = bulk.filter((d) => d.valid).length;
  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_420px]">
      <div className="space-y-4">
        <Card title="단말기 입력" right={<Seg value={mode} onChange={setMode} options={[{ value: "single", label: "직접 입력" }, { value: "bulk", label: "엑셀 일괄" }]} />}>
          {mode === "single" ? (
            <div className="space-y-3">
              <Field label="Serial Number" hint="여러 개를 줄바꿈·쉼표·공백으로 붙여넣을 수 있습니다">
                <Textarea rows={4} value={snText} onChange={(e) => setSnText(e.target.value)} className="font-[family-name:var(--font-mono)]" />
              </Field>
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Model"><Select value={model} onChange={onModel} placeholder="선택" options={opts?.models ?? []} /></Field>
                <Field label="Current OS"><Select value={cur} onChange={setCur} placeholder="-" options={(opts?.os_levels ?? []).map(String)} /></Field>
                <Field label="Upgrade To"><Select value={upg} onChange={setUpg} placeholder="-" options={(opts?.os_levels ?? []).map(String)} /></Field>
              </div>
              {preview && <div className={preview.ok ? "text-[13px] font-semibold text-ok" : "text-[13px] font-semibold text-err"}>{preview.ok ? "→ " : "⚠ "}{preview.text}</div>}
              <Btn kind="primary" onClick={addSingle}>목록에 추가</Btn>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-3 text-[13px] text-ink-2">
                열 순서: SN / Current OS / Upgrade To / Model (규칙이 하나뿐인 모델은 OS 생략 가능)
                <a href="/api/wl/template.xlsx" className="font-semibold text-brand-ink hover:underline">양식 내려받기</a>
              </div>
              <label className="flex cursor-pointer items-center justify-center rounded-xl border-2 border-dashed border-line-strong px-4 py-6 text-[13.5px] text-ink-2 hover:bg-surface-2">
                <input type="file" accept=".xlsx" className="hidden" onChange={(e) => { onBulk(e.target.files?.[0]); e.target.value = ""; }} />
                .xlsx 파일 선택
              </label>
              {bulk.length > 0 && (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[13.5px] font-semibold">유효 {bulkValid} / 오류 {bulk.length - bulkValid} (총 {bulk.length})</span>
                    <div className="flex-1" />
                    <Btn small kind="primary" disabled={!bulkValid} onClick={() => { addValid(bulk); setBulk([]); }}>유효 항목 추가</Btn>
                    <Btn small kind="ghost" onClick={() => setBulk([])}>초기화</Btn>
                  </div>
                  <Table rows={bulk} rowTone={(d) => (d.valid ? undefined : "warn")} cols={[
                    { key: "sn", label: "SN", mono: true }, { key: "cur", label: "Cur" }, { key: "upg", label: "Upg" },
                    { key: "model", label: "Model" }, { key: "api", label: "API" },
                    { key: "ostype", label: "OS Type", mono: true, render: (d) => d.ostype || "-" },
                    { key: "st", label: "상태", render: (d) => d.valid ? <span className="text-ok">OK</span> : <span className="text-warn">{d.errors[0]}</span> },
                  ]} />
                </>
              )}
            </div>
          )}
        </Card>

        <Card title={<>등록 예정 단말기 <Tag>{staged.length}대</Tag></>} pad={false}>
          {staged.length ? (
            <div className="p-3">
              <Table rows={staged} max={420} cols={[
                { key: "sn", label: "SN", mono: true }, { key: "api", label: "API" }, { key: "endDate", label: "End Date" },
                { key: "ostype", label: "OS Type", mono: true }, { key: "model", label: "Model" },
                { key: "x", label: "", w: "40px", render: (d) => <button onClick={() => setStaged((s) => s.filter((x) => x.sn !== d.sn))} className="text-ink-3 hover:text-err">✕</button> },
              ]} />
            </div>
          ) : <div className="px-5 py-8 text-center text-[13px] text-ink-3">아직 담은 단말기가 없습니다</div>}
        </Card>
      </div>

      <div>
        <Card title="신청 정보" className="xl:sticky xl:top-6">
          <div className="space-y-3">
            <Field label="유/무상"><Seg value={charge} onChange={setCharge} options={[{ value: "무상", label: "무상" }, { value: "유상", label: "유상" }]} /></Field>
            {charge === "무상" ? <Field label="무상 사유"><Textarea rows={2} value={freeReason} onChange={(e) => setFreeReason(e.target.value)} /></Field>
              : <Field label="PO 파일"><input type="file" onChange={(e) => setPo(e.target.files?.[0] ?? null)} className="block w-full text-[13px]" /></Field>}
            <Field label="메모(선택)"><Textarea rows={2} value={memo} onChange={(e) => setMemo(e.target.value)} /></Field>
            {opts && !opts.email && <Note tone="warn">발송 이메일이 등록돼 있지 않아 관리자 알림 메일은 나가지 않습니다.</Note>}
            <Btn kind="primary" className="w-full" busy={busy === "submit"} disabled={!staged.length} onClick={submit}>
              {staged.length ? `${staged.length}대 신청 제출` : "신청 제출"}
            </Btn>
            <div className="text-center text-[12px] text-ink-3">요청자 {opts?.email || "-"}</div>
          </div>
        </Card>
      </div>
    </div>
  );
}
