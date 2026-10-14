"""License 모듈 **전용** DB — 기존 tw_app.db 와 물리적으로 분리(pmdm_license.db).

기존 앱 DB(app.database.engine/Base)와 완전히 다른 엔진·메타데이터를 쓴다.
→ License 테이블은 tw_app.db 에 절대 생성되지 않고, 반대로 기존 테이블도 여기 안 생김.
import 시 자동으로 스키마를 생성한다(별도 startup 훅 불필요).
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.database import BASE_DIR

LICENSE_DB_PATH = BASE_DIR / "pmdm_license.db"

license_engine = create_engine(
    f"sqlite:///{LICENSE_DB_PATH}",
    echo=False,
    connect_args={"check_same_thread": False},
)

LicenseSessionLocal = sessionmaker(
    bind=license_engine, autoflush=False, expire_on_commit=False
)


class LicenseBase(DeclarativeBase):
    """License 모듈 전용 declarative base (app.database.Base 와 분리)."""


def license_session():
    """with license_session() as db: ... 형태로 사용."""
    return LicenseSessionLocal()


def init_license_db() -> None:
    from app.license import models  # noqa: F401  (LicenseBase 에 모델 등록)

    LicenseBase.metadata.create_all(bind=license_engine)
    _ensure_columns()


def _ensure_columns() -> None:
    """기존 DB 에 신규 컬럼을 추가한다(create_all 은 컬럼 추가를 안 함)."""
    from sqlalchemy import inspect, text

    insp = inspect(license_engine)
    if "license_requests" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("license_requests")}
    adds = []
    if "note" not in cols:
        adds.append("ALTER TABLE license_requests ADD COLUMN note TEXT DEFAULT ''")
    if adds:
        with license_engine.begin() as conn:
            for sql in adds:
                conn.execute(text(sql))


# import 시 즉시 스키마 보장(LicenseBase 는 위에서 이미 정의됨 → 순환 import 안전)
init_license_db()
