"""이슈 트래킹 **전용** DB — tw_app.db 와 물리적으로 분리(issuetrack.db).

license/whitelist 모듈과 같은 방식이다. 별도 엔진·메타데이터를 쓰므로 이 테이블들이
tw_app.db 에 생기지 않고, 반대도 마찬가지다.

수집량이 큰 편이라(이슈 1,600여 건 + 이벤트 6,000여 건, 이후 계속 증가) 메일·작업
DB 와 섞지 않는 편이 백업·초기화 모두 안전하다.
"""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.database import BASE_DIR
from app.dbconf import make_engine

ISSUETRACK_DB_PATH = BASE_DIR / "issuetrack.db"

# SQLite 일 때는 백필이 수천 건을 연속으로 쓰는 동안 조회가 막히지 않도록 WAL + busy_timeout.
issuetrack_engine = make_engine("issuetrack", ISSUETRACK_DB_PATH)


IssuetrackSessionLocal = sessionmaker(
    bind=issuetrack_engine, autoflush=False, expire_on_commit=False
)


class IssuetrackBase(DeclarativeBase):
    """이슈 트래킹 전용 declarative base (app.database.Base 와 분리)."""


def issuetrack_session():
    """with issuetrack_session() as db: ... 형태로 사용."""
    return IssuetrackSessionLocal()


def init_issuetrack_db() -> None:
    from app.issuetrack import models  # noqa: F401  (IssuetrackBase 에 모델 등록)

    IssuetrackBase.metadata.create_all(bind=issuetrack_engine)
    _ensure_columns()


#: 나중에 늘어난 컬럼. create_all 은 **테이블만** 만들고 컬럼 추가는 안 해서,
#: 이미 만들어진 DB 에는 ALTER TABLE 로 따로 붙여야 한다.
#: {테이블: [(컬럼명, DDL 조각), ...]}
_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "rm_sb_docs": [
        ("origin", "VARCHAR(20) DEFAULT 'backlog'"),
    ],
    "rm_issues": [
        ("is_test", "BOOLEAN DEFAULT 0"),
    ],
}


def _ensure_columns() -> None:
    from sqlalchemy import inspect, text

    insp = inspect(issuetrack_engine)
    have = set(insp.get_table_names())
    stmts: list[str] = []
    for table, cols in _ADDED_COLUMNS.items():
        if table not in have:
            continue        # create_all 이 방금 만들었다면 컬럼도 이미 다 있다
        existing = {c["name"] for c in insp.get_columns(table)}
        for name, ddl in cols:
            if name not in existing:
                stmts.append(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
    if stmts:
        with issuetrack_engine.begin() as conn:
            for sql in stmts:
                conn.execute(text(sql))


# import 시 즉시 스키마 보장(IssuetrackBase 는 위에서 이미 정의됨 → 순환 import 안전)
init_issuetrack_db()
