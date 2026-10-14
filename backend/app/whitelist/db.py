"""WhiteList 모듈 **전용** DB — 기존 tw_app.db 와 물리적으로 분리(whitelist.db).

License 모듈(pmdm_license.db)과 동일한 격리 패턴. 기존 앱 DB(app.database)와 다른
엔진·메타데이터를 쓴다 → WhiteList 테이블은 tw_app.db 에 절대 생성되지 않는다.
import 시 자동으로 스키마를 생성한다.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.database import BASE_DIR

WHITELIST_DB_PATH = BASE_DIR / "whitelist.db"

whitelist_engine = create_engine(
    f"sqlite:///{WHITELIST_DB_PATH}",
    echo=False,
    connect_args={"check_same_thread": False},
)

WhitelistSessionLocal = sessionmaker(
    bind=whitelist_engine, autoflush=False, expire_on_commit=False
)


class WhitelistBase(DeclarativeBase):
    """WhiteList 모듈 전용 declarative base (app.database.Base 와 분리)."""


def whitelist_session():
    """with whitelist_session() as db: ... 형태로 사용."""
    return WhitelistSessionLocal()


# 기존 whitelist.db 에 나중에 추가된 컬럼(create_all 로는 안 붙는다).
_ADDED_COLUMNS = [
    ("wl_requests", "source_mail_id", "TEXT DEFAULT ''"),
    ("wl_requests", "requester_addr", "TEXT DEFAULT ''"),
    ("wl_requests", "mail_lang", "TEXT DEFAULT ''"),
    ("wl_requests", "mail_message_id", "TEXT DEFAULT ''"),
    ("wl_requests", "reply_status", "TEXT DEFAULT ''"),
    # 규칙 카테고리(GMS/DQA/KT/LG/기타). 기존 White… 규칙은 GMS 가 아니라 '기타' 로 채운다
    # (GMS 는 '제품명GMS버전' 형식으로 별도 등록).
    ("wl_rules", "category", "TEXT DEFAULT '기타'"),
    ("wl_requests", "category", "TEXT DEFAULT ''"),   # 신청이 어느 구분으로 처리됐는지 기록
    # 고객 포털 — 이 요청이 어느 고객사 것인지. 비어 있으면 고객 화면에 나오지 않는다.
    ("wl_requests", "company_id", "INTEGER"),
    # 초기 비밀번호 변경 강제 플래그. 기존 계정도 1 로 시작해 한 번은 바꾸게 한다.
    ("wl_portal_accounts", "must_change", "INTEGER DEFAULT 1"),
    # 비밀번호 재설정 링크(한 번에 하나).
    ("wl_portal_accounts", "reset_token", "TEXT DEFAULT ''"),
    ("wl_portal_accounts", "reset_expires", "DATETIME"),
]


def _migrate() -> None:
    from sqlalchemy import inspect, text

    insp = inspect(whitelist_engine)
    tables = set(insp.get_table_names())
    with whitelist_engine.begin() as conn:
        for table, col, sqltype in _ADDED_COLUMNS:
            if table not in tables:
                continue
            if col not in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {sqltype}"))


def init_whitelist_db() -> None:
    from app.whitelist import models  # noqa: F401  (WhitelistBase 에 모델 등록)

    WhitelistBase.metadata.create_all(bind=whitelist_engine)
    _migrate()
    _seed_default_rules()


def _seed_default_rules() -> None:
    """규칙이 하나도 없으면 Apps Script 기본 10개를 시드.

    이 White… 형식(예: White45OtoP)은 GMS 가 아니라 '기타' 구분이다.
    GMS 는 '제품명GMS버전'(PM95GMS15 …) 형식으로 별도 등록한다.
    """
    from app.whitelist.models import WLRule

    defaults = [
        ("PM451", 11, 13, "White451RtoT"), ("PM451", 9, 11, "White451PtoR"),
        ("PM45", 8, 9, "White45OtoP"),     ("PM560", 11, 13, "White560RtoT"),
        ("PM67", 11, 13, "White67RtoT"),   ("PM85", 8, 9, "White85OtoP_1"),
        ("PM86", 11, 13, "White86RtoT"),   ("PM90", 9, 11, "White90PtoR"),
        ("PM90", 11, 13, "White90RtoT"),   ("PM95", 13, 15, "White95TtoV"),
    ]
    with whitelist_session() as db:
        if db.query(WLRule).count() > 0:
            return
        for model, frm, to, ostype in defaults:
            db.add(WLRule(model=model, from_os=frm, to_os=to, ostype=ostype,
                          category="기타"))
        db.commit()


# import 시 즉시 스키마 보장
init_whitelist_db()
