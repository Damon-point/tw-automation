"""영역 하나씩 8080 → 3080 으로 다시 옮긴다(2026-10-16 WhiteList · DQA · License 를 3080 에서 실제 업무로).

sqlite_to_pg.py 는 다섯 스키마를 통째로 비우고 3080 안의 옛 복사본(10-14)에서 다시 채운다 — public 의
3080 전용 데이터(설정 · 작업 상태 · 받은 메일)까지 지운다. 이것은 고른 영역의 스키마와 작업 파일만,
8080 의 **지금** 파일에서 가져온다.

    backend\\.venv\\Scripts\\python.exe -X utf8 tools\\area_from_8080.py                    # 비교만
    backend\\.venv\\Scripts\\python.exe -X utf8 tools\\area_from_8080.py --load             # 실제로
    ... --areas whitelist        (기본: whitelist,license)

3080 API(8180)를 멈추고, 8080 에서 그 영역 일을 멈춘 뒤 돌린다. 넣기 전에 대상 스키마는 pg_dump,
작업 파일은 work/backups/ 에 남긴다.

  whitelist — 스키마 whitelist + work/whitelist_mail_state.json · whitelist_triage.json ·
              whitelist_dqa_senders.json · dqa_fota/
  license   — 스키마 license + work/licenses/po · cert, result_mail_log.json(두 쪽을 합친다)

데이터 안의 8080 경로(…\\tw-automation\\work\\…)는 3080 경로(…\\tw-automation-v2\\backend\\work\\…)로 바꾼다.
WhiteList 기록 키에 든 받은 메일 번호(…#<id>)는 두 앱이 메일을 따로 받아 다를 수 있어 message_id 로 맞춘다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import create_engine, select, text  # noqa: E402

from app.dbconf import is_pg, pg_url  # noqa: E402

if not is_pg():
    raise SystemExit("TWN_PG_URL 이 없습니다 (backend/.env)")

from app import database  # noqa: E402
from app.license import db as ldb  # noqa: E402
from app.license import models as _lm  # noqa: E402,F401
from app.whitelist import db as wdb  # noqa: E402
from app.whitelist import models as _wm  # noqa: E402,F401
from relocate_root import make_fixer  # noqa: E402

SRC = Path(os.getenv("TW8080_DIR") or r"C:\Users\POINTMOBILE\dev\tw-automation")
DST_WORK = database.BASE_DIR / "work"
SRC_WORK = SRC / "work"
AREAS = {
    "whitelist": {"db": "whitelist.db", "meta": wdb.WhitelistBase.metadata, "engine": wdb.whitelist_engine,
                  "schema": "whitelist",
                  "files": ["whitelist_mail_state.json", "whitelist_triage.json", "whitelist_dqa_senders.json"],
                  "dirs": ["dqa_fota"]},
    "license": {"db": "pmdm_license.db", "meta": ldb.LicenseBase.metadata, "engine": ldb.license_engine,
                "schema": "license", "files": [], "dirs": ["licenses/po", "licenses/cert"],
                "merge": ["licenses/result_mail_log.json"]},
}
BATCH = 500
fix = make_fixer(str(SRC_WORK), str(DST_WORK))


def _fix(v):
    if isinstance(v, str):
        v = v.replace("\x00", "")
        return fix(v)[0] if "tw-automation" in v else v
    return v


def _mail_id_map() -> dict[int, int]:
    """8080 받은 메일 번호 → 3080 번호(message_id 가 같은 것). 같으면 넣지 않는다."""
    c8 = sqlite3.connect(SRC / "tw_app.db")
    m8 = {r[0]: r[1] for r in c8.execute("select id, message_id from cached_mails") if r[1]}
    with database.engine.connect() as c:
        by3: dict[str, int] = {}
        for i, mid in c.execute(text("select id, message_id from cached_mails order by id")):
            if mid:
                by3.setdefault(mid, i)
    return {i: by3[mid] for i, mid in m8.items() if mid in by3 and by3[mid] != i}


def _remap_key(k: str, idmap: dict[int, int]) -> str:
    """'제목#586' · '제목#594|907' 의 메일 번호를 3080 것으로."""
    if "#" not in k:
        return k
    base, tail = k.rsplit("#", 1)
    parts = [str(idmap.get(int(p), int(p))) if p.isdigit() else p for p in tail.split("|")]
    return base + "#" + "|".join(parts)


def _copy_db(name: str, a: dict, load: bool) -> int:
    path = SRC / a["db"]
    lite = create_engine(f"sqlite:///{path}")
    lite_tables = set(lite.dialect.get_table_names(lite.connect()))
    meta, pg, schema = a["meta"], a["engine"], a["schema"]
    problems = 0
    print(f"[{name}] {path} → {schema}")
    for table in meta.sorted_tables:
        if table.name not in lite_tables:
            print(f"   {table.name}: 8080 에 없음 — 그대로 둔다")
            continue
        have = {c["name"] for c in lite.dialect.get_columns(lite.connect(), table.name)}
        cols = [c for c in table.columns if c.name in have]
        with lite.connect() as lc:
            rows = [{k: _fix(v) for k, v in dict(r._mapping).items()} for r in lc.execute(select(*cols))]
        with pg.connect() as c:
            before = c.execute(text(f'SELECT COUNT(*) FROM "{schema}"."{table.name}"')).scalar()
        if not load:
            print(f"   {table.name}: 8080 {len(rows)}행 · 3080 {before}행")
            continue
        with pg.begin() as pc:
            pc.execute(text(f'TRUNCATE TABLE "{schema}"."{table.name}" RESTART IDENTITY CASCADE'))
            for i in range(0, len(rows), BATCH):
                pc.execute(table.insert(), rows[i:i + BATCH])
            pk = list(table.primary_key.columns)
            if len(pk) == 1 and pk[0].autoincrement in (True, "auto") and pk[0].type.python_type is int:
                pc.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('\"{schema}\".\"{table.name}\"', '{pk[0].name}'), "
                    f"COALESCE((SELECT MAX(\"{pk[0].name}\") FROM \"{schema}\".\"{table.name}\"), 1), "
                    f"(SELECT MAX(\"{pk[0].name}\") FROM \"{schema}\".\"{table.name}\") IS NOT NULL)"))
            n = pc.execute(text(f'SELECT COUNT(*) FROM "{schema}"."{table.name}"')).scalar()
        mark = "" if n == len(rows) else f"  ✘ 8080 {len(rows)}행과 다름"
        problems += 0 if n == len(rows) else 1
        print(f"   {table.name}: {before} → {n}행{mark}")
    return problems


def _write_json(dst: Path, data) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    tmp.write_text(fix(json.dumps(data, ensure_ascii=False, indent=1))[0], encoding="utf-8")
    os.replace(tmp, dst)


def _copy_files(name: str, a: dict, load: bool, backup: Path, idmap: dict[int, int]) -> None:
    for f in a.get("files", []):
        src, dst = SRC_WORK / f, DST_WORK / f
        if not src.exists():
            print(f"   {f}: 8080 에 없음")
            continue
        data = json.loads(src.read_text(encoding="utf-8"))
        moved = 0
        if isinstance(data, dict) and f in ("whitelist_mail_state.json", "whitelist_triage.json"):
            new = {}
            for k, v in data.items():
                k2 = _remap_key(k, idmap)
                moved += k2 != k
                new[k2] = v
            data = new
        print(f"   {f}: {len(data) if hasattr(data, '__len__') else '-'}건" + (f" · 메일 번호 맞춤 {moved}" if moved else ""))
        if load:
            if dst.exists():
                (backup / f).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, backup / f)
            _write_json(dst, data)
    for d in a.get("dirs", []):
        src, dst = SRC_WORK / d, DST_WORK / d
        if not src.exists():
            print(f"   {d}/: 8080 에 없음")
            continue
        n = sum(1 for p in src.rglob("*") if p.is_file())
        print(f"   {d}/: 파일 {n}개")
        if load:
            shutil.copytree(src, dst, dirs_exist_ok=True)
    for f in a.get("merge", []):
        src, dst = SRC_WORK / f, DST_WORK / f
        a8 = json.loads(src.read_text(encoding="utf-8")) if src.exists() else {}
        a3 = json.loads(dst.read_text(encoding="utf-8")) if dst.exists() else {}
        merged = {k: list(v) for k, v in a8.items()}
        for k, v in a3.items():
            seen = {json.dumps(e, sort_keys=True, ensure_ascii=False) for e in merged.get(k, [])}
            merged.setdefault(k, []).extend(e for e in v if json.dumps(e, sort_keys=True, ensure_ascii=False) not in seen)
        print(f"   {f}: 8080 {len(a8)} · 3080 {len(a3)} → {len(merged)}건(합침)")
        if load:
            if dst.exists():
                (backup / f).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, backup / f)
            _write_json(dst, merged)


def _pg_dump(schemas: list[str], out: Path) -> None:
    exe = Path(os.getenv("TWN_PG_BIN") or r"C:\Users\POINTMOBILE\pgsql\bin") / "pg_dump.exe"
    url = re.sub(r"^postgresql\+\w+://", "postgresql://", pg_url())
    args = [str(exe), "--dbname", url, "-f", str(out)] + [x for s in schemas for x in ("-n", s)]
    subprocess.run(args, check=True)


def main() -> int:
    load = "--load" in sys.argv
    areas = ["whitelist", "license"]
    if "--areas" in sys.argv:
        areas = [x for x in sys.argv[sys.argv.index("--areas") + 1].split(",") if x in AREAS]
    backup = DST_WORK / "backups" / f"area_from_8080-{datetime.now():%Y%m%d-%H%M%S}"
    if load:
        backup.mkdir(parents=True, exist_ok=True)
        _pg_dump([AREAS[a]["schema"] for a in areas], backup / "schemas.sql")
        print(f"백업: {backup}")
    idmap = _mail_id_map()
    print(f"받은 메일 번호가 다른 것: {len(idmap)}통")
    bad = 0
    for a in areas:
        bad += _copy_db(a, AREAS[a], load)
        _copy_files(a, AREAS[a], load, backup, idmap)
    print("문제 없음" if not bad else f"문제 {bad}건")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
