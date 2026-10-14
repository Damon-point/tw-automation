# -*- coding: utf-8 -*-
"""프로젝트 폴더를 옮길 때 — 데이터 안의 절대 경로 앞부분(예전 프로젝트 폴더)을 새 폴더로 바꾼다.

작업 상태 · 단계 결과 · 첨부 · 산출물 경로가 '<드라이브>:\\…\\<옛 폴더>\\backend\\work\\…' 처럼 절대 경로로
들어 있어서, 폴더 이름만 바꾸면 링크가 전부 깨진다. 바꾸는 것은 '<옛 폴더>' 앞부분뿐이고 뒤는 그대로 둔다.
구분자 표기(역슬래시 · JSON 안의 두 겹 역슬래시 · 슬래시)는 원래 것을 따른다.

    python tools/relocate_root.py <옛 폴더> <새 폴더>            # 건수만
    python tools/relocate_root.py <옛 폴더> <새 폴더> --apply    # 실제로(PostgreSQL 모든 스키마 + work/**/*.json)

API 를 멈춘 상태에서 돌린다. 폴더를 옮기기 **전에**, 옛 위치에서 돌린다.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))


def make_fixer(old: str, new: str):
    """old 경로(어느 구분자로 적혀 있든)를 new 로 — 구분자는 찾은 것과 같게."""
    parts = [p for p in re.split(r"[\\/]+", old.strip().rstrip("\\/")) if p]
    new_parts = [p for p in re.split(r"[\\/]+", new.strip().rstrip("\\/")) if p]
    sep = r"(\\\\|\\|/)"
    pat = re.escape(parts[0]) + sep + r"\1".join(re.escape(p) for p in parts[1:])
    rx = re.compile(pat + r"(?=\1|$|[\"'\s),\]}])", re.I)

    def fix(s: str) -> tuple[str, int]:
        return rx.subn(lambda m: m.group(1).join(new_parts), s)

    return fix


def walk(v, fix):
    if isinstance(v, str):
        return fix(v)
    if isinstance(v, list):
        out, n = [], 0
        for x in v:
            y, k = walk(x, fix)
            out.append(y)
            n += k
        return out, n
    if isinstance(v, dict):
        out, n = {}, 0
        for key, x in v.items():
            y, k = walk(x, fix)
            out[key] = y
            n += k
        return out, n
    return v, 0


def run(old: str, new: str, apply: bool) -> int:
    fix = make_fixer(old, new)
    total = 0

    # 1) work/**/*.json
    files = hits = 0
    for root, _d, names in os.walk(HERE / "work"):
        for f in names:
            if not f.endswith(".json"):
                continue
            p = Path(root) / f
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            nd, k = walk(data, fix)
            if k:
                files += 1
                hits += k
                if apply:
                    p.write_text(json.dumps(nd, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"work JSON: 파일 {files}개 · 경로 {hits}곳")
    total += hits

    # 2) PostgreSQL — 글자 칸 · JSON 칸 전부
    from sqlalchemy import text

    from app.database import engine

    like = "%" + re.split(r"[\\/]+", old.rstrip("\\/"))[-1] + "%"
    with engine.begin() as c:
        cols = c.execute(text("""
            select c.table_schema, c.table_name, c.column_name, c.data_type
            from information_schema.columns c join information_schema.tables t
              on t.table_schema = c.table_schema and t.table_name = c.table_name
            where t.table_type = 'BASE TABLE' and c.table_schema not in ('pg_catalog','information_schema')
              and c.data_type in ('text','character varying','json','jsonb')""")).all()
        for schema, table, col, dtype in cols:
            pk = c.execute(text("""
                select a.attname from pg_index i join pg_attribute a on a.attrelid = i.indrelid and a.attnum = any(i.indkey)
                where i.indrelid = cast(:t as regclass) and i.indisprimary"""), {"t": f'"{schema}"."{table}"'}).scalars().all()
            if len(pk) != 1:
                continue
            q = f'select "{pk[0]}", "{col}" from "{schema}"."{table}" where "{col}"::text ilike :l'
            n_col = 0
            for rid, val in c.execute(text(q), {"l": like}).all():
                if dtype in ("json", "jsonb"):
                    nv, k = walk(val, fix)
                    if k and apply:
                        c.execute(text(f'update "{schema}"."{table}" set "{col}" = cast(:v as {dtype}) where "{pk[0]}" = :id'),
                                  {"v": json.dumps(nv, ensure_ascii=False), "id": rid})
                else:
                    nv, k = fix(val or "")
                    if k and apply:
                        c.execute(text(f'update "{schema}"."{table}" set "{col}" = :v where "{pk[0]}" = :id'), {"v": nv, "id": rid})
                n_col += k
            if n_col:
                print(f"   {schema}.{table}.{col}: {n_col}곳")
                total += n_col
        if not apply:
            c.rollback()
    print(f"합계 {total}곳" + (" — 바꿨다" if apply else " — 건수만(--apply 로 실제로)"))
    return total


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    if len(a) != 2:
        raise SystemExit(__doc__)
    run(a[0], a[1], "--apply" in sys.argv)
