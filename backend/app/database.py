"""DB 연결 / 세션 헬퍼.

3080 은 로컬 PostgreSQL 의 public 스키마(app.dbconf), 설정이 없으면 프로젝트 루트의 tw_app.db(SQLite).
"""
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# 프로젝트 루트 (app/ 의 부모)
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "tw_app.db"

# PostgreSQL 이면 public 스키마, 아니면 SQLite(WAL + busy_timeout — 여러 사람이 동시에 쓴다)
from app.dbconf import make_engine  # noqa: E402

engine = make_engine("public", DB_PATH)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# 기존 DB 에 나중에 추가된 컬럼들(테이블은 이미 있으니 create_all 로는 안 붙는다).
# (table, column, "SQL 타입") — 없으면 ALTER TABLE ADD COLUMN 으로 붙인다.
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    # 첫 로그인 비밀번호 변경 — 기존 계정은 0(변경 불필요)으로 남는다.
    ("users", "must_change_password", "INTEGER DEFAULT 0"),
    # 계정별 메일 서명(Outlook 에서 붙여넣은 HTML).
    ("mail_credentials", "signature_html", "TEXT DEFAULT ''"),
    ("dropbox_credentials", "encrypted_app_key", "TEXT"),
    ("dropbox_credentials", "encrypted_app_secret", "TEXT"),
    ("dropbox_credentials", "encrypted_refresh_token", "TEXT"),
    # 팀 공용 작업 보드
    ("cached_mails", "message_id", "TEXT DEFAULT ''"),   # 팀 공용 메일 중복제거 키
    # 스레드 연결용 헤더 — 제목만으로 묶으면 같은 제목의 새 요청이 한 덩어리가 된다.
    ("cached_mails", "in_reply_to", "TEXT DEFAULT ''"),
    ("cached_mails", "refs", "TEXT DEFAULT ''"),
    # 회신(전체 회신)용 원본 수신자 — 참조인(Cc) 보존.
    ("cached_mails", "to_recipients", "TEXT DEFAULT ''"),
    ("cached_mails", "cc_recipients", "TEXT DEFAULT ''"),
    # 앱이 보낸 메일 표식 — 우리 회신을 신규 요청으로 오인하지 않게.
    ("cached_mails", "app_marker", "TEXT DEFAULT ''"),
    # 첨부(엑셀/CSV) — 대량 SN 요청 처리용.
    ("cached_mails", "attachment_names", "TEXT DEFAULT ''"),
    ("cached_mails", "attachment_serials", "TEXT DEFAULT ''"),
    ("cached_mails", "attachment_dir", "TEXT DEFAULT ''"),
    ("mail_stages", "updated_by", "TEXT DEFAULT ''"),    # 단계 마지막 수정자(표시용)
    # ⑦ Mailchimp: 태그(세그먼트) 기준 발송
    ("mailchimp_credentials", "tag", "TEXT DEFAULT ''"),
    # 계정 역할 (admin | user)
    ("users", "role", "TEXT DEFAULT 'user'"),
    # 제품 관리: Dropbox sdcard **zip** 경로(개별 파일 경로와 별개 폴더)
    ("os_products", "dropbox_os_sdcard_zip_path", "TEXT NOT NULL DEFAULT ''"),
    # 제품 관리: 이 SKU 의 최초 OS 버전
    ("os_products", "initial_os_version", "TEXT NOT NULL DEFAULT ''"),
    # 제품 관리: RN for Sales 저장 폴더(전체 이력본과 다른 곳에 쌓는다)
    ("os_products", "sales_rn_path", "TEXT NOT NULL DEFAULT ''"),
    # 제품 관리: 제품군(보기 구분용 — EDA · PDT & Keypad · Tablet · POS · Others)
    ("os_products", "category", "TEXT NOT NULL DEFAULT ''"),
    # 제품 관리: FOTA OsInfo 의 Release_Note_URL 로 그대로 쓰는 주소
    ("os_products", "rn_url", "TEXT NOT NULL DEFAULT ''"),
    # OS Update 작업: 소급 완료로 닫은 시각(실제 실행 없이 이력만 남긴 회차 표시)
    ("os_update_jobs", "backfilled_at", "DATETIME"),
    # OS Update 작업: 감지 근거 메일 스냅샷(JSON) — 시작한 뒤에도 남는다
    ("os_update_jobs", "basis_mails", "TEXT NOT NULL DEFAULT ''"),
]


def _migrate() -> None:
    """기존 테이블에 누락된 컬럼을 안전하게 추가(예전 SQLite 파일용 — PostgreSQL 은 create_all 이 다 만든다)."""
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, column, sqltype in _ADDED_COLUMNS:
            if table not in existing_tables:
                continue  # create_all 이 새로 만들 테이블 → ALTER 불필요
            cols = {c["name"] for c in insp.get_columns(table)}
            if column not in cols:
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {sqltype}'))
        # 같은 메일(message_id)이 팀 전체에서 한 행만 존재하도록 유니크 인덱스.
        # (동기화 코드도 존재 검사로 막지만, 동시 동기화 레이스까지 방지)
        if "cached_mails" in existing_tables:
            # 유니크 인덱스 전에, message_id 가 비어있는 기존(레거시) 행을 행별 고유값으로 채운다.
            # (구버전 동기화로 저장돼 Message-ID 가 없는 행들이 '' 로 겹쳐 인덱스 생성이 막히는 것 방지)
            conn.execute(text(
                "UPDATE cached_mails SET message_id = 'legacy:' || id "
                "WHERE message_id IS NULL OR message_id = ''"
            ))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_mail_message_id "
                "ON cached_mails(message_id)"
            ))
        # 부트스트랩: 'admin' 계정은 admin 역할로. (다른 계정은 기본 user)
        if "users" in existing_tables:
            conn.execute(text("UPDATE users SET role='admin' WHERE username='admin'"))
    _merge_os_file_path()


def _merge_os_file_path() -> None:
    """os_products: OS file 경로를 sdcard/flash 두 칸에서 **한 칸**으로 합친다.

    sdcard 쪽 값을 os_file_path 로 이름만 바꿔 살리고(입력값 보존), flash 칸은 없앤다.
    flash 에만 값이 있던 행은 사라지지 않게 os_file_path 로 옮긴다.
    """
    insp = inspect(engine)
    if "os_products" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("os_products")}
    with engine.begin() as conn:
        if "os_sdcard_path" in cols and "os_file_path" not in cols:
            conn.execute(text(
                "ALTER TABLE os_products RENAME COLUMN os_sdcard_path TO os_file_path"
            ))
            cols = (cols - {"os_sdcard_path"}) | {"os_file_path"}
        if "os_flash_path" in cols:
            if "os_file_path" in cols:
                conn.execute(text(
                    "UPDATE os_products SET os_file_path = os_flash_path "
                    "WHERE (os_file_path IS NULL OR os_file_path = '') "
                    "AND os_flash_path IS NOT NULL AND os_flash_path != ''"
                ))
            conn.execute(text("ALTER TABLE os_products DROP COLUMN os_flash_path"))


def _pre_create_rebuilds() -> None:
    """유니크 제약 변경 등 컬럼 추가로 안 되는 스키마 변경은 테이블을 재생성한다.

    sftp_credentials: 단일(user_id 유니크) → 서버별(user_id,target 유니크)로 구조 변경.
    'target' 컬럼이 없으면 옛 스키마이므로 드롭 → create_all 이 새 스키마로 다시 만든다.
    (파일서버 자격증명은 재등록하면 되므로 데이터 손실 허용.)
    """
    insp = inspect(engine)
    if "sftp_credentials" in set(insp.get_table_names()):
        cols = {c["name"] for c in insp.get_columns("sftp_credentials")}
        if "target" not in cols:
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE sftp_credentials"))


def init_db() -> None:
    """모든 테이블을 생성하고(이미 있으면 무시) 누락 컬럼을 마이그레이션한다."""
    from app import models  # noqa: F401  (모델 등록 목적 import)

    _pre_create_rebuilds()
    Base.metadata.create_all(bind=engine)
    _migrate()


def get_session():
    """with get_session() as db: ... 형태로 사용."""
    return SessionLocal()
