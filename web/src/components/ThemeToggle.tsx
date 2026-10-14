"use client";

import { useEffect, useState } from "react";
import { Icon } from "./Icons";
import { cx } from "./ui";

export type Theme = "system" | "light" | "dark";
const KEY = "twn-theme";
const OPTS: { v: Theme; label: string; icon: string }[] = [
  { v: "light", label: "라이트", icon: "sun" },
  { v: "system", label: "시스템", icon: "monitor" },
  { v: "dark", label: "다크", icon: "moon" },
];

/** 첫 그림 전에 테마를 박아 둔다 — 다크로 둔 사람에게 흰 화면이 번쩍이지 않게(layout 의 <head> 에 넣는다). */
export const THEME_BOOT = `try{var t=localStorage.getItem("${KEY}");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

function read(): Theme {
  try {
    const t = localStorage.getItem(KEY);
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system";
  }
}

function apply(t: Theme) {
  const el = document.documentElement;
  if (t === "system") delete el.dataset.theme;
  else el.dataset.theme = t;
  try {
    if (t === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, t);
  } catch {
    /* 저장이 막혀도 이번 화면에는 적용된다 */
  }
}

function useTheme(): [Theme, (t: Theme) => void] {
  const [t, setT] = useState<Theme>("system");
  useEffect(() => setT(read()), []);
  return [t, (v: Theme) => { apply(v); setT(v); }];
}

/** 라이트 · 시스템 · 다크 세 칸 — 사이드바 아래 */
export function ThemeSwitch({ className }: { className?: string }) {
  const [t, set] = useTheme();
  return (
    <div role="radiogroup" aria-label="화면 테마" className={cx("flex rounded-lg bg-side-hover p-0.5", className)}>
      {OPTS.map((o) => (
        <button key={o.v} role="radio" aria-checked={t === o.v} title={o.label} onClick={() => set(o.v)}
                className={cx("flex flex-1 items-center justify-center gap-1.5 rounded-md py-1.5 text-[12px] font-medium transition-colors",
                  t === o.v ? "bg-side-seg font-semibold text-side-strong shadow-sm" : "text-side-dim hover:text-side-strong")}>
          <Icon name={o.icon} className="h-3.5 w-3.5" />{o.label}
        </button>
      ))}
    </div>
  );
}

/** 아이콘 하나 — 누를 때마다 라이트 → 다크 → 시스템. 좁은 화면·로그인 화면용 */
export function ThemeCycle({ className }: { className?: string }) {
  const [t, set] = useTheme();
  const next: Theme = t === "light" ? "dark" : t === "dark" ? "system" : "light";
  const cur = OPTS.find((o) => o.v === t)!;
  return (
    <button onClick={() => set(next)} title={`화면 테마: ${cur.label}`} aria-label={`화면 테마: ${cur.label}`}
            className={cx("grid h-8 w-8 place-items-center rounded-lg transition-colors", className)}>
      <Icon name={cur.icon} className="h-[18px] w-[18px]" />
    </button>
  );
}
