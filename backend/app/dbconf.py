"""DB 엔진 만들기 — 로컬 PostgreSQL(있으면) 또는 SQLite(없으면).

3080 은 이 PC 의 PostgreSQL(127.0.0.1:5432, DB twnext)을 쓴다. 접속 주소는 backend/.env 의
TWN_PG_URL. 예전 SQLite 파일(tw_app.db 등)은 모듈마다 따로였으므로, PostgreSQL 에서는 같은
DB 안에 **모듈마다 스키마**를 둔다 — public(본체) · license · issuetrack · whitelist · usage.
엔진마다 search_path 를 자기 스키마로 잡아 두므로 모델·SQL 은 테이블 이름 그대로 쓴다.

외부 FOTA DB(FOTA_DB_*)는 이 모듈과 무관하다 — 그쪽은 app/whitelist/fota_db.py 가 따로 붙는다.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")          # 스크립트로 불러도 같은 DB 를 보게(이미 있으면 덮지 않는다)

_BUSY_TIMEOUT_MS = 8000


def pg_url() -> str:
    return (os.getenv("TWN_PG_URL") or "").strip()


def is_pg() -> bool:
    return bool(pg_url())


def make_engine(schema: str, sqlite_path: Path, *, wal: bool = True) -> Engine:
    """모듈 하나의 엔진. PostgreSQL 이면 그 모듈의 스키마에, 아니면 SQLite 파일에."""
    url = pg_url()
    if url:
        eng = create_engine(
            url, echo=False, pool_pre_ping=True, pool_size=8, max_overflow=16, pool_recycle=1800,
            connect_args={"options": f"-csearch_path={schema}"},
        )
        with eng.begin() as c:
            c.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        return eng

    eng = create_engine(
        f"sqlite:///{sqlite_path}", echo=False,
        connect_args={"check_same_thread": False, "timeout": _BUSY_TIMEOUT_MS / 1000},
    )
    if wal:
        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _rec):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()
    return eng
