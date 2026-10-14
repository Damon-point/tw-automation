"use client";

import { useEffect, useRef, useState } from "react";
import { cx } from "@/components/ui";

/** Redmine 본문 · SB 본문 · Confluence 렌더 결과에 쓰는 글 서식(표·목록·코드) */
export function RichStyle() {
  return (
    <style>{`
.rich { word-break: break-word; line-height: 1.6; font-size: 13.5px; }
.rich p { margin: 0 0 .55em; }
.rich ul, .rich ol { margin: .25em 0 .6em; padding-left: 1.4em; }
.rich ul { list-style: disc; } .rich ul ul { list-style: circle; } .rich ol { list-style: decimal; }
.rich li { margin: .1em 0; }
.rich h1, .rich h2, .rich h3, .rich h4, .rich h5, .rich h6 { font-weight: 700; margin: .8em 0 .35em; line-height: 1.3; }
.rich h1 { font-size: 1.3em; } .rich h2 { font-size: 1.18em; } .rich h3 { font-size: 1.06em; }
.rich table { border-collapse: collapse; margin: .5em 0; font-size: .95em; display: block; overflow-x: auto; max-width: 100%; }
.rich th, .rich td { border: 1px solid var(--line-strong); padding: 4px 8px; vertical-align: top; }
.rich th { background: var(--surface-2); font-weight: 600; text-align: left; }
.rich pre { background: var(--surface-2); border: 1px solid var(--line); border-radius: 6px; padding: 8px 10px; margin: .5em 0; overflow-x: auto; white-space: pre-wrap; word-break: break-all; font-size: .9em; }
.rich code { background: var(--muted-soft); padding: 0 4px; border-radius: 4px; font-family: var(--font-mono); font-size: .92em; }
.rich pre code { background: none; padding: 0; }
.rich blockquote { border-left: 3px solid var(--line-strong); margin: .5em 0; padding-left: .8em; color: var(--text-2); }
.rich a { color: var(--brand-ink); text-decoration: underline; }
.rich img { max-width: 100%; }
`}</style>
  );
}

const TOOLS: { cmd: string; arg?: string; label: React.ReactNode; title: string }[] = [
  { cmd: "bold", label: <b>B</b>, title: "굵게" },
  { cmd: "italic", label: <i>I</i>, title: "기울임" },
  { cmd: "underline", label: <u>U</u>, title: "밑줄" },
  { cmd: "strikeThrough", label: <s>S</s>, title: "취소선" },
  { cmd: "formatBlock", arg: "h2", label: "H2", title: "제목 2" },
  { cmd: "formatBlock", arg: "h3", label: "H3", title: "제목 3" },
  { cmd: "formatBlock", arg: "p", label: "¶", title: "본문" },
  { cmd: "insertUnorderedList", label: "•", title: "글머리 목록" },
  { cmd: "insertOrderedList", label: "1.", title: "번호 목록" },
  { cmd: "createLink", label: "🔗", title: "링크" },
  { cmd: "removeFormat", label: "⌫", title: "서식 지우기" },
  { cmd: "undo", label: "↶", title: "되돌리기" },
  { cmd: "redo", label: "↷", title: "다시" },
];

/**
 * 서식 편집기. 버튼으로 서식을 넣고, </> 로 HTML 원문을 직접 고친다.
 * XHTML 문법은 맞추지 않아도 된다 — 업로드할 때 Confluence 변환 API 가 정규화한다.
 */
export function RichEditor({ value, onChange, placeholder, minHeight = 340 }: {
  value: string; onChange: (html: string) => void; placeholder?: string; minHeight?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [source, setSource] = useState(false);
  const last = useRef(value);

  // 밖에서 값이 바뀌었을 때만(다른 문서를 열었을 때) 내용을 갈아 끼운다 — 입력 중 커서가 튀지 않게.
  useEffect(() => {
    if (!source && ref.current && value !== last.current) {
      ref.current.innerHTML = value;
      last.current = value;
    }
  }, [value, source]);
  useEffect(() => {
    if (ref.current) ref.current.innerHTML = value;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source]);

  function emit() {
    const html = ref.current?.innerHTML ?? "";
    last.current = html;
    onChange(html);
  }

  function run(cmd: string, arg?: string) {
    if (source) return;
    ref.current?.focus();
    if (cmd === "createLink") {
      const url = window.prompt("링크 주소");
      if (!url) return;
      document.execCommand(cmd, false, url);
    } else {
      document.execCommand(cmd, false, arg);
    }
    emit();
  }

  return (
    <div className="overflow-hidden rounded-xl border border-line-strong bg-surface focus-within:border-brand">
      <div className="flex flex-wrap items-center gap-0.5 border-b border-line bg-surface-2 px-1.5 py-1">
        {TOOLS.map((t, i) => (
          <button key={i} type="button" title={t.title} disabled={source}
                  onMouseDown={(e) => { e.preventDefault(); run(t.cmd, t.arg); }}
                  className="min-w-[30px] rounded-md px-2 py-1 text-[13px] text-ink-2 hover:bg-muted-soft hover:text-ink disabled:opacity-40">
            {t.label}
          </button>
        ))}
        <span className="flex-1" />
        <button type="button" onClick={() => setSource((v) => !v)} title="HTML 원문 보기·고치기"
                className={cx("rounded-md px-2 py-1 font-mono text-[12.5px]", source ? "bg-brand text-white" : "text-ink-2 hover:bg-muted-soft")}>
          {"</>"}
        </button>
      </div>
      {source ? (
        <textarea value={value} onChange={(e) => { last.current = e.target.value; onChange(e.target.value); }}
                  spellCheck={false} style={{ minHeight }}
                  className="block w-full resize-y bg-surface p-3 font-mono text-[12.5px] leading-relaxed text-ink outline-none" />
      ) : (
        <div ref={ref} contentEditable suppressContentEditableWarning onInput={emit} onBlur={emit}
             data-placeholder={placeholder}
             style={{ minHeight }}
             className="rich max-h-[70vh] overflow-auto px-4 py-3 outline-none empty:before:text-ink-3 empty:before:content-[attr(data-placeholder)]" />
      )}
    </div>
  );
}
