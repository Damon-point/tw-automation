"use client";

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { cx } from "@/components/ui";
import { Tabs, inputCls } from "./kit";

export type HtmlEditorHandle = { getHtml: () => string };

/**
 * 문서 본문 편집 — [보면서 고치기]는 원본 서식 그대로 띄운 문서를 직접 고치고,
 * [HTML]은 소스를 고친다. 문서 전체(머리·스타일 포함)를 iframe 에 그대로 띄워
 * 실제 페이지와 같은 모양으로 보이게 한다. 스크립트는 돌리지 않는다.
 */
const TOOLS: { cmd: string; label: string; title: string; arg?: string }[] = [
  { cmd: "bold", label: "B", title: "굵게" },
  { cmd: "italic", label: "I", title: "기울임" },
  { cmd: "underline", label: "U", title: "밑줄" },
  { cmd: "insertUnorderedList", label: "•", title: "글머리 기호" },
  { cmd: "insertOrderedList", label: "1.", title: "번호 목록" },
  { cmd: "createLink", label: "링크", title: "링크 걸기" },
  { cmd: "unlink", label: "링크 해제", title: "링크 해제" },
  { cmd: "removeFormat", label: "서식 지우기", title: "서식 지우기" },
];

function stripScripts(html: string) {
  return html.replace(/<script[\s\S]*?<\/script>/gi, "");
}

export const HtmlEditor = forwardRef<HtmlEditorHandle, { initial: string; onDirty?: () => void; minH?: string }>(
  function HtmlEditor({ initial, onDirty, minH = "64vh" }, ref) {
    const [mode, setMode] = useState<"visual" | "source">("visual");
    const [source, setSource] = useState(initial);
    const frame = useRef<HTMLIFrameElement>(null);
    const latest = useRef(initial);

    const readFrame = () => {
      const doc = frame.current?.contentDocument;
      if (!doc) return latest.current;
      const html = "<!DOCTYPE html>\n" + doc.documentElement.outerHTML;
      // 문서 조각(본문만)으로 받은 경우 조각으로 돌려준다 — 원래 모양을 지킨다
      return /<html[\s>]/i.test(latest.current) ? html : doc.body.innerHTML;
    };

    useImperativeHandle(ref, () => ({
      getHtml: () => (mode === "visual" ? readFrame() : source),
    }));

    useEffect(() => {
      latest.current = initial;
      setSource(initial);
    }, [initial]);

    useEffect(() => {
      if (mode !== "visual") return;
      const f = frame.current;
      if (!f) return;
      const doc = f.contentDocument;
      if (!doc) return;
      doc.open();
      doc.write(stripScripts(source));
      doc.close();
      doc.designMode = "on";
      const onInput = () => onDirty?.();
      doc.addEventListener("input", onInput);
      return () => doc.removeEventListener("input", onInput);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [mode, source]);

    function switchTo(m: "visual" | "source") {
      if (m === mode) return;
      if (m === "source") setSource(readFrame());
      setMode(m);
    }

    function exec(cmd: string) {
      const doc = frame.current?.contentDocument;
      if (!doc) return;
      if (cmd === "createLink") {
        const url = window.prompt("링크 주소");
        if (!url) return;
        doc.execCommand(cmd, false, url);
      } else {
        doc.execCommand(cmd, false);
      }
      onDirty?.();
      frame.current?.contentWindow?.focus();
    }

    return (
      <div className="overflow-hidden rounded-xl border border-line">
        <div className="flex flex-wrap items-center gap-1 border-b border-line bg-surface-2 px-2 py-1.5">
          <Tabs value={mode} onChange={switchTo} items={[{ key: "visual", label: "보면서 고치기" }, { key: "source", label: "HTML" }]} />
          {mode === "visual" && (
            <div className="ml-2 flex flex-wrap gap-0.5">
              {TOOLS.map((t) => (
                <button key={t.cmd} title={t.title} onMouseDown={(e) => { e.preventDefault(); exec(t.cmd); }}
                        className={cx("rounded-md px-2 py-1 text-[12.5px] text-ink-2 hover:bg-surface hover:text-ink", t.cmd === "bold" && "font-bold", t.cmd === "italic" && "italic", t.cmd === "underline" && "underline")}>
                  {t.label}
                </button>
              ))}
            </div>
          )}
        </div>
        {mode === "visual" ? (
          <iframe ref={frame} sandbox="allow-same-origin" className="block w-full bg-white" style={{ minHeight: minH }} />
        ) : (
          <textarea value={source} onChange={(e) => { setSource(e.target.value); onDirty?.(); }} spellCheck={false}
                    className={cx(inputCls, "block rounded-none border-0 font-mono text-[12.5px] leading-relaxed focus:ring-0")} style={{ minHeight: minH }} />
        )}
      </div>
    );
  },
);
