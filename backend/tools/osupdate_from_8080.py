"""OS Update 데이터만 8080 → 3080 으로 다시 옮긴다(2026-10-19 OS Update 를 3080 으로 옮길 때).

area_from_8080.py 는 스키마를 통째로 다룬다. OS Update 는 public 안의 표 몇 개라 그 표만 고른다 —
받은 메일(cached_mails) · 설정(app_settings) · 계정 · WhiteList/License 스키마는 건드리지 않는다.

    backend\\.venv\\Scripts\\python.exe -X utf8 tools\\osupdate_from_8080.py           # 비교만
    backend\\.venv\\Scripts\\python.exe -X utf8 tools\\osupdate_from_8080.py --load    # 실제로

3080 API(8180)를 멈추고, 8080 에서 OS Update 일을 멈춘 뒤 돌린다. 넣기 전에 대상 표 · 작업 상태는 pg_dump,
바뀌는 JSON 은 work/backups/ 에 남긴다. os_download(OS 원본, 수십 GB)는 옮기지 않는다 — 새 작업이 다시 받는다.

  DB   os_products · os_product_releases · os_update_jobs · os_update_job_stages · fota_entries · mail_stages
       (TRUNCATE 는 이 표들만 한 번에 — CASCADE 없이)
  상태 work/pipeline_state/*.json → PostgreSQL pipeline_state · alias (import_from_files overwrite)
  파일 pipeline_history/ · rn_research/ · newsletter/ · kb_preview/ · excel_repo/ ·
       newsletter_review.json · newsletter_image_urls.json · portal_review.json

데이터 안의 예전 경로(…\\tw-automation\\work · …\\Damon(박훈일)\\pointmobile\\dev\\tw-automation\\work)는
3080 의 backend\\work 로 바꾸고, 받은 메일 번호는 message_id 로 3080 번호에 맞춘다.
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
from app import models as _m  # noqa: E402,F401
from area_from_8080 import _mail_id_map  # noqa: E402
from relocate_root import make_fixer  # noqa: E402

SRC = Path(os.getenv("TW8080_DIR") or r"C:\Users\POINTMOBILE\dev\tw-automation")
SRC_WORK = SRC / "work"
DST_WORK = database.BASE_DIR / "work"
OLD_ROOTS = [str(SRC_WORK), r"C:\Users\Damon(박훈일)\pointmobile\dev\tw-automation\work"]
FIXERS = [make_fixer(r, str(DST_WORK)) for r in OLD_ROOTS]
TABLES = ["os_products", "os_product_releases", "os_update_jobs", "os_update_job_stages", "fota_entries", "mail_stages"]
DIRS = ["pipeline_history", "rn_research", "newsletter", "kb_preview", "excel_repo"]
FILES = ["newsletter_review.json", "newsletter_image_urls.json", "portal_review.json"]
MAIL_COLS = {("os_update_jobs", "submission_mail_id"), ("os_update_jobs", "approval_mail_id"),
             ("mail_stages", "mail_id"), ("fota_entries", "mail_id")}
BATCH = 500


def fix_text(s: str) -> str:
    if "tw-automation" not in s:
        return s
    for f in FIXERS:
        s = f(s)[0]
    return s


def _val(table: str, col: str, v, idmap: dict[int, int]):
    if isinstance(v, str):
        v = fix_text(v.replace("\x00", ""))
    if (table, col) in MAIL_COLS and v not in (None, ""):
        s = str(v)
        if s.isdigit() and int(s) in idmap:
            v = idmap[int(s)] if isinstance(v, int) else str(idmap[int(s)])
        if table == "fota_entries":
            v = str(v)                      # 3080 은 글자 칸(8080 은 숫자 · 'job-N' 이 섞였다)
    if table == "os_update_jobs" and col == "basis_mails" and v:
        try:
            lst = json.loads(v)
            for b in lst if isinstance(lst, list) else []:
                mid = b.get("mail_id")
                if str(mid).isdigit() and int(mid) in idmap:
                    b["mail_id"] = idmap[int(mid)]
            v = json.dumps(lst, ensure_ascii=False)
        except (ValueError, AttributeError):
            pass
    return v


def _pg_dump(out: Path) -> None:
    exe = Path(os.getenv("TWN_PG_BIN") or r"C:\Users\POINTMOBILE\pgsql\bin") / "pg_dump.exe"
    url = re.sub(r"^postgresql\+\w+://", "postgresql://", pg_url())
    args = [str(exe), "--dbname", url, "-f", str(out), "--data-only"]
    for t in TABLES + ["pipeline_state", "pipeline_state_alias"]:
        args += ["-t", f"public.{t}"]
    subprocess.run(args, check=True)


def copy_db(load: bool, idmap: dict[int, int]) -> int:
    lite = create_engine(f"sqlite:///{SRC / 'tw_app.db'}")
    meta = database.Base.metadata
    problems = 0
    data: dict[str, list[dict]] = {}
    for name in TABLES:
        table = meta.tables[name]
        have = {c["name"] for c in lite.dialect.get_columns(lite.connect(), name)}
        cols = [c for c in table.columns if c.name in have]
        with lite.connect() as lc:
            data[name] = [{k: _val(name, k, v, idmap) for k, v in dict(r._mapping).items()}
                          for r in lc.execute(select(*cols))]
        with database.engine.connect() as c:
            before = c.execute(text(f'SELECT COUNT(*) FROM public."{name}"')).scalar()
        print(f"   {name}: 8080 {len(data[name])}행 · 3080 {before}행")
    if not load:
        return 0
    with database.engine.begin() as pc:
        pc.execute(text("TRUNCATE TABLE " + ", ".join(f'public."{t}"' for t in TABLES) + " RESTART IDENTITY"))
        for name in TABLES:                       # TABLES 는 부모 → 자식 순서
            table = meta.tables[name]
            rows = data[name]
            for i in range(0, len(rows), BATCH):
                pc.execute(table.insert(), rows[i:i + BATCH])
            pk = list(table.primary_key.columns)
            if len(pk) == 1 and pk[0].autoincrement in (True, "auto") and pk[0].type.python_type is int:
                pc.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('public.\"{name}\"', '{pk[0].name}'), "
                    f"COALESCE((SELECT MAX(\"{pk[0].name}\") FROM public.\"{name}\"), 1), "
                    f"(SELECT MAX(\"{pk[0].name}\") FROM public.\"{name}\") IS NOT NULL)"))
            n = pc.execute(text(f'SELECT COUNT(*) FROM public."{name}"')).scalar()
            mark = "" if n == len(rows) else f"  ✘ 8080 {len(rows)}행과 다름"
            problems += 0 if n == len(rows) else 1
            print(f"   {name}: → {n}행{mark}")
    return problems


def _fix_json_file(p: Path) -> None:
    try:
        s = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    t = fix_text(s)
    if t != s:
        p.write_text(t, encoding="utf-8")


def copy_state(load: bool, backup: Path, idmap: dict[int, int]) -> None:
    src, dst = SRC_WORK / "pipeline_state", DST_WORK / "pipeline_state"
    files = sorted(src.glob("*.json"))
    print(f"   pipeline_state: 8080 파일 {len(files)}개")
    if not load:
        return
    if dst.exists():
        shutil.copytree(dst, backup / "pipeline_state", dirs_exist_ok=True)
    dst.mkdir(parents=True, exist_ok=True)
    for f in files:
        name = f.name
        stem = f.stem
        if stem.isdigit() and int(stem) in idmap:          # 메일 번호로 된 작업 상태 파일
            name = f"{idmap[int(stem)]}.json"
        (dst / name).write_text(fix_text(f.read_text(encoding="utf-8")), encoding="utf-8")
    from app.pipeline.state import import_from_files

    r = import_from_files(overwrite=True)
    print(f"   pipeline_state → PostgreSQL: 상태 {r['states']} · 별칭 추가 {r['aliases']}" + (f" · 못 읽음 {r['bad']}" if r["bad"] else ""))


def copy_files(load: bool, backup: Path) -> None:
    for d in DIRS:
        src, dst = SRC_WORK / d, DST_WORK / d
        if not src.exists():
            print(f"   {d}/: 8080 에 없음")
            continue
        n = sum(1 for p in src.rglob("*") if p.is_file())
        print(f"   {d}/: 파일 {n}개")
        if load:
            shutil.copytree(src, dst, dirs_exist_ok=True)
            for p in dst.rglob("*.json"):
                _fix_json_file(p)
    for f in FILES:
        src, dst = SRC_WORK / f, DST_WORK / f
        if not src.exists():
            print(f"   {f}: 8080 에 없음")
            continue
        print(f"   {f}")
        if load:
            if dst.exists():
                shutil.copy2(dst, backup / f)
            dst.write_text(fix_text(src.read_text(encoding="utf-8")), encoding="utf-8")


def main() -> int:
    load = "--load" in sys.argv
    backup = DST_WORK / "backups" / f"osupdate_from_8080-{datetime.now():%Y%m%d-%H%M%S}"
    if load:
        backup.mkdir(parents=True, exist_ok=True)
        _pg_dump(backup / "osupdate_tables.sql")
        print(f"백업: {backup}")
    idmap = _mail_id_map()
    print(f"받은 메일 번호가 다른 것: {len(idmap)}통")
    print("[DB]")
    bad = copy_db(load, idmap)
    print("[작업 상태]")
    copy_state(load, backup, idmap)
    print("[파일]")
    copy_files(load, backup)
    print("문제 없음" if not bad else f"문제 {bad}건")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
