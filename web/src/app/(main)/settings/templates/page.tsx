"use client";

import { useRef, useState } from "react";
import { Button, Confirm, Section, SetBadge, toast } from "@/components/admin/kit";
import { Skeleton } from "@/components/ui";
import { useApi } from "@/lib/api";
import { bytes, errText, send, upload, type FileTpl } from "@/lib/admin";

function Row({ t, onChanged }: { t: FileTpl; onChanged: () => void }) {
  const ref = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [del, setDel] = useState(false);
  async function put(f?: File) {
    if (!f) return;
    setBusy(true);
    try {
      const r = await upload<{ message: string }>(`/api/admin/templates/${t.key}`, f, "file");
      toast(r.message);
      onChanged();
    } catch (e) {
      toast(errText(e), "err");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border border-line px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[14px] font-bold">{t.label}</span>
          <SetBadge ok={t.exists} okText="등록됨" noText="미등록" />
        </div>
        <div className="mt-0.5 font-mono text-[12px] text-ink-3">{t.name}{t.exists && ` · ${t.mtime} · ${bytes(t.size)}`}</div>
      </div>
      <input ref={ref} type="file" accept={t.ext} className="hidden" onChange={(e) => { put(e.target.files?.[0]); e.target.value = ""; }} />
      <Button variant="outline" size="sm" busy={busy} onClick={() => ref.current?.click()}>{t.exists ? "교체" : "올리기"}</Button>
      {t.exists && (
        <>
          <a href={`/api/admin/templates/${t.key}/file`} className="rounded-lg px-2.5 py-1 text-[12.5px] font-semibold text-ink-2 hover:bg-surface-2">내려받기</a>
          <Button variant="ghost" size="sm" className="text-err hover:bg-err-soft hover:text-err" onClick={() => setDel(true)}>삭제</Button>
        </>
      )}
      <Confirm open={del} onClose={() => setDel(false)} title={`'${t.label}' 삭제`} confirmText="삭제" danger
               onConfirm={async () => { try { const r = await send(`/api/admin/templates/${t.key}`, undefined, "DELETE"); toast(r.message ?? "삭제했습니다"); onChanged(); } catch (e) { toast(errText(e), "err"); } }}>
        이 템플릿을 쓰는 단계(Release Note · Newsletter)는 다시 올릴 때까지 문서를 만들지 못합니다.
      </Confirm>
    </div>
  );
}

export default function TemplatesPage() {
  const { data, loading, reload } = useApi<{ templates: FileTpl[] }>("/api/admin/templates");
  if (loading || !data) return <Skeleton rows={4} />;
  return (
    <Section title="RN · Newsletter 템플릿"
             desc={<>Release Note 제작이 이 템플릿을 채워 문서를 만듭니다. Word 는 docxtpl 태그로 씁니다 — <code className="font-mono">{"{{ product }}"}</code> · <code className="font-mono">{"{{ version_full }}"}</code> · <code className="font-mono">{"{% for c in changes %}{{ c }}{% endfor %}"}</code></>}>
      <div className="space-y-2.5">
        {data.templates.map((t) => <Row key={t.key} t={t} onChanged={reload} />)}
      </div>
    </Section>
  );
}
