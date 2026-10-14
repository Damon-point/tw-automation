"""3080 의 SQLite 파일 → 로컬 PostgreSQL(twnext) 로 옮긴다. 몇 번을 돌려도 같다(대상 표를 비우고 다시 채운다).

    backend\\.venv\\Scripts\\python.exe -X utf8 tools\\sqlite_to_pg.py            # 길이 검사만
    backend\\.venv\\Scripts\\python.exe -X utf8 tools\\sqlite_to_pg.py --load     # 실제로 옮긴다

API(8180)를 멈춘 상태에서 돌린다 — 도는 중이면 옮기는 사이에 SQLite 가 바뀐다.
SQLite 파일은 지우지 않는다(되돌릴 때 쓴다: backend/.env 의 TWN_PG_URL 을 지우면 SQLite 로 돈다).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import String, create_engine, select, text  # noqa: E402

from app.dbconf import is_pg  # noqa: E402

if not is_pg():
    raise SystemExit("TWN_PG_URL 이 없습니다 (backend/.env)")

from app import database  # noqa: E402
from app import models as _m  # noqa: E402,F401
from app import usage  # noqa: E402
from app.issuetrack import db as itdb  # noqa: E402
from app.issuetrack import models as _im  # noqa: E402,F401
from app.license import db as ldb  # noqa: E402
from app.license import models as _lm  # noqa: E402,F401
from app.whitelist import db as wdb  # noqa: E402
from app.whitelist import models as _wm  # noqa: E402,F401

B = database.BASE_DIR
MODULES = [
    # (이름, SQLite 파일, 메타데이터, PostgreSQL 엔진, 스키마)
    ("본체", B / "tw_app.db", database.Base.metadata, database.engine, "public"),
    ("License", B / "pmdm_license.db", ldb.LicenseBase.metadata, ldb.license_engine, "license"),
    ("Issue/SB", B / "issuetrack.db", itdb.IssuetrackBase.metadata, itdb.issuetrack_engine, "issuetrack"),
    ("WhiteList", B / "whitelist.db", wdb.WhitelistBase.metadata, wdb.whitelist_engine, "whitelist"),
    ("사용량", B / "usage.db", usage._meta, usage._eng(), "usage"),
]
BATCH = 500


def _clean(v):
    if isinstance(v, str) and "\x00" in v:
        return v.replace("\x00", "")
    return v


def run(load: bool) -> int:
    problems = 0
    for name, path, meta, pg, schema in MODULES:
        if not path.exists():
            print(f"[{name}] SQLite 파일 없음 — 건너뜀")
            continue
        lite = create_engine(f"sqlite:///{path}")
        lite_tables = set(lite.dialect.get_table_names(lite.connect()))
        meta.create_all(pg)
        print(f"[{name}] {path.name} → {schema}")
        for table in meta.sorted_tables:
            if table.name not in lite_tables:
                print(f"   {table.name}: SQLite 에 없음 — 빈 표로 둔다")
                continue
            have = {c["name"] for c in lite.dialect.get_columns(lite.connect(), table.name)}
            cols = [c for c in table.columns if c.name in have]
            with lite.connect() as lc:
                rows = [dict(r._mapping) for r in lc.execute(select(*cols))]
            # PostgreSQL 은 VARCHAR(n) 길이를 지킨다(SQLite 는 안 지켰다) — 넘치는 값을 먼저 찾는다
            for c in cols:
                if isinstance(c.type, String) and c.type.length:
                    over = [r[c.name] for r in rows if isinstance(r[c.name], str) and len(r[c.name]) > c.type.length]
                    if over:
                        problems += 1
                        print(f"   ✘ {table.name}.{c.name} VARCHAR({c.type.length}) 넘침 {len(over)}건 — 최대 {max(map(len, over))}자")
            # SQLite 는 정수 칸에 글자도 받아 준다(타입 친화성) — PostgreSQL 은 거부한다
            for c in cols:
                try:
                    is_int = c.type.python_type is int
                except NotImplementedError:
                    is_int = False
                if is_int:
                    bad = [r[c.name] for r in rows if r[c.name] is not None and not isinstance(r[c.name], (int, bool))]
                    if bad:
                        problems += 1
                        print(f"   ✘ {table.name}.{c.name} 정수 칸에 글자 {len(bad)}건 — 예: {bad[:3]}")
            # SQLite 는 외래키를 검사하지 않았다 — 가리키는 행이 없는 값을 찾는다
            for fk in table.foreign_keys:
                col, ref = fk.parent.name, fk.column
                if col not in have or ref.table.name not in lite_tables:
                    continue
                with lite.connect() as lc:
                    ids = {r[0] for r in lc.execute(text(f'SELECT "{ref.name}" FROM "{ref.table.name}"'))}
                bad = [r[col] for r in rows if r[col] is not None and r[col] not in ids]
                if bad:
                    problems += 1
                    print(f"   ✘ {table.name}.{col} → {ref.table.name}.{ref.name} 없는 값 {len(bad)}건 — 예: {sorted(set(bad))[:5]}")
            if not load:
                print(f"   {table.name}: {len(rows)}행")
                continue
            with pg.begin() as pc:
                pc.execute(text(f'TRUNCATE TABLE "{schema}"."{table.name}" RESTART IDENTITY CASCADE'))
                for i in range(0, len(rows), BATCH):
                    chunk = [{k: _clean(v) for k, v in r.items()} for r in rows[i:i + BATCH]]
                    pc.execute(table.insert(), chunk)
                pk = list(table.primary_key.columns)
                if len(pk) == 1 and pk[0].autoincrement in (True, "auto") and pk[0].type.python_type is int:
                    pc.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('\"{schema}\".\"{table.name}\"', '{pk[0].name}'), "
                        f"COALESCE((SELECT MAX(\"{pk[0].name}\") FROM \"{schema}\".\"{table.name}\"), 1), "
                        f"(SELECT MAX(\"{pk[0].name}\") FROM \"{schema}\".\"{table.name}\") IS NOT NULL)"))
                n = pc.execute(text(f'SELECT COUNT(*) FROM "{schema}"."{table.name}"')).scalar()
            mark = "" if n == len(rows) else f"  ✘ 원본 {len(rows)}행과 다름"
            print(f"   {table.name}: {n}행{mark}")
            problems += 0 if n == len(rows) else 1
    return problems


if __name__ == "__main__":
    bad = run("--load" in sys.argv)
    print("문제 없음" if not bad else f"문제 {bad}건")
    sys.exit(1 if bad else 0)
