# -*- coding: utf-8 -*-
"""복사해 온 데이터 안의 '원본 tw-automation 의 work 폴더' 경로를 이 백엔드의 work 로 바꾼다.

3080 이 8080 의 파일을 읽거나 덮어쓰지 않게 하는 것이 목적이다. 바꾸는 것은 경로 앞부분
'<드라이브>:\\…\\tw-automation\\work' 뿐이고, 그 뒤 경로와 다른 글자는 그대로 둔다.

    python tools/relocate_paths.py          # 건수만 센다
    python tools/relocate_paths.py --apply  # 실제로 바꾼다
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
NEW_WORK = str((HERE / "work").resolve())
# '<드라이브>:\ … \tw-automation\work' (슬래시 · 역슬래시 모두) — 뒤에 구분자가 오거나 끝일 때만
OLD_WORK_RE = re.compile(r"[A-Za-z]:[\\/](?:[^\\/\"\n<>|?*]+[\\/])*?tw-automation[\\/]work(?=[\\/]|$|[\s\"'),])")


def fix(s: str) -> tuple[str, int]:
    return OLD_WORK_RE.subn(NEW_WORK.replace("\\", "\\\\"), s)


def walk(v):
    """JSON 값을 돌며 문자열을 고친다. (새 값, 바꾼 수)"""
    if isinstance(v, str):
        return fix(v)
    if isinstance(v, list):
        out, n = [], 0
        for x in v:
            y, k = walk(x); out.append(y); n += k
        return out, n
    if isinstance(v, dict):
        out, n = {}, 0
        for k_, x in v.items():
            y, k = walk(x); out[k_] = y; n += k
        return out, n
    return v, 0


def main(apply: bool) -> None:
    total_json = files_json = 0
    for root, _, files in os.walk(HERE / "work"):
        for f in files:
            if not f.endswith(".json"):
                continue
            p = Path(root) / f
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            new, n = walk(data)
            if n:
                files_json += 1
                total_json += n
                if apply:
                    p.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON: 파일 {files_json}개 · 경로 {total_json}곳")

    for db in ("tw_app.db", "whitelist.db", "issuetrack.db", "pmdm_license.db", "usage.db"):
        c = sqlite3.connect(HERE / db)
        n_db = 0
        detail = []
        for (t,) in c.execute("select name from sqlite_master where type='table'").fetchall():
            info = c.execute(f"pragma table_info('{t}')").fetchall()
            pk = next((r[1] for r in info if r[5]), None)
            if not pk:
                continue
            cols = [r[1] for r in info if "CHAR" in (r[2] or "").upper() or (r[2] or "").upper() in ("TEXT", "")]
            for col in cols:
                rows = c.execute(f"select \"{pk}\", \"{col}\" from '{t}' where \"{col}\" like '%tw-automation%'").fetchall()
                k_col = 0
                for rid, val in rows:
                    if not isinstance(val, str):
                        continue
                    new, k = OLD_WORK_RE.subn(NEW_WORK.replace("\\", "\\\\"), val)
                    if k:
                        k_col += k
                        if apply:
                            c.execute(f"update '{t}' set \"{col}\"=? where \"{pk}\"=?", (new, rid))
                if k_col:
                    detail.append(f"{t}.{col}={k_col}")
                    n_db += k_col
        if apply:
            c.commit()
        print(f"{db}: 경로 {n_db}곳 {detail}")


if __name__ == "__main__":
    main("--apply" in sys.argv)
