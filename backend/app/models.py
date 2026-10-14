"""데이터 모델.

User            : 로그인 계정 (관리자가 CLI로 생성)
MailCredential  : 사용자별 Outlook(POP3) 접속 정보 (비밀번호는 암호화 저장)
CachedMail      : 동기화해 온 메일 (UIDL 로 중복 방지)
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="user")  # "admin" | "user"
    #: 첫 로그인에서 비밀번호를 바꿔야 하는가. 관리자가 계정을 만들면 초기 비밀번호가
    #: 공통값(1234)이라 그대로 두면 누구나 아는 비밀번호로 남는다. 그래서 새 계정은
    #: 이 표시를 달고 나가고, 본인이 바꾸기 전까지 다른 화면으로 갈 수 없다.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    credential: Mapped["MailCredential | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    mails: Mapped[list["CachedMail"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class MailCredential(Base):
    __tablename__ = "mail_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    email_address: Mapped[str] = mapped_column(String(200))
    # Fernet 로 암호화된 앱 비밀번호 (평문 저장 금지)
    encrypted_password: Mapped[str] = mapped_column(Text)
    pop_server: Mapped[str] = mapped_column(String(200), default="outlook.office365.com")
    pop_port: Mapped[int] = mapped_column(Integer, default=995)
    use_ssl: Mapped[bool] = mapped_column(Boolean, default=True)
    #: 이 사람의 메일 서명(Outlook 에서 복사해 붙인 HTML 그대로).
    #: 사람마다 직함·연락처가 달라 양식을 만들어 주는 대신 쓰던 것을 그대로 쓴다
    #: (사용자 확정 2026-08-19).
    signature_html: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    user: Mapped["User"] = relationship(back_populates="credential")


class WebCredential(Base):
    """브라우저 자동화용 웹 편집 계정 (Confluence 페이지 / 네이버 등).

    세션이 만료되면 저장된 username/password 로 자동 재로그인한다.
    로그인/편집 셀렉터는 사이트마다 달라 컬럼으로 저장(설정에서 튜닝 가능).
    """

    __tablename__ = "web_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "target", name="uq_web_user_target"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    target: Mapped[str] = mapped_column(String(30))  # confluence / naver
    base_url: Mapped[str] = mapped_column(String(500), default="")
    login_url: Mapped[str] = mapped_column(String(500), default="")  # 비우면 base_url 사용
    username: Mapped[str] = mapped_column(String(200), default="")
    encrypted_password: Mapped[str] = mapped_column(Text, default="")
    # 편집 흐름 셀렉터 (기본값 = Confluence 클래식)
    edit_url_template: Mapped[str] = mapped_column(
        String(500), default="{base}/pages/editpage.action?pageId={page_id}"
    )
    editor_iframe_sel: Mapped[str] = mapped_column(String(200), default="#wysiwygTextarea_ifr")
    save_button_sel: Mapped[str] = mapped_column(String(200), default="#rte-button-publish")
    # 로그인 폼 셀렉터
    username_sel: Mapped[str] = mapped_column(String(200), default="#os_username")
    password_sel: Mapped[str] = mapped_column(String(200), default="#os_password")
    submit_sel: Mapped[str] = mapped_column(String(200), default="#loginButton")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class SftpCredential(Base):
    """내부 파일서버(SMB) 접속 정보 — **서버별(target)**로 계정 저장(비밀번호 암호화).

    파일서버는 SMB(Windows 공유)이며, 서버마다 계정이 다르다. target 으로 구분한다:
      - "fs104" : 192.168.123.104 (OS 이미지 파일)
      - "fs160" : 192.168.123.160 (FOTA-DB 등)
    host 는 target 별 고정값이고, 사용자는 아이디/비밀번호만 등록한다.
    인증은 win32wnet(WNetAddConnection2)로 계정 기반 접속.
    """

    __tablename__ = "sftp_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "target", name="uq_fs_user_target"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    target: Mapped[str] = mapped_column(String(20), default="fs104")   # fs104 | fs160
    protocol: Mapped[str] = mapped_column(String(10), default="smb")   # smb 고정
    host: Mapped[str] = mapped_column(String(200), default="")
    port: Mapped[int] = mapped_column(Integer, default=445)
    username: Mapped[str] = mapped_column(String(200), default="")
    encrypted_password: Mapped[str] = mapped_column(Text, default="")
    base_path: Mapped[str] = mapped_column(String(500), default="")    # 미사용(공유는 파이프라인 root)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class DropboxCredential(Base):
    """사용자별 Dropbox 자격증명 (암호화 저장).

    두 방식 지원:
      - refresh 방식(권장): app_key + app_secret + refresh_token → SDK 가 access token 자동 갱신
      - 레거시: 단기 access token 하나(encrypted_token). 몇 시간 뒤 만료됨.
    """

    __tablename__ = "dropbox_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    # 레거시 단기 액세스 토큰(선택)
    encrypted_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    # refresh 방식(권장): 셋 다 있으면 자동 갱신
    encrypted_app_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_app_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class MailchimpCredential(Base):
    """⑦ Newsletter 발송용 Mailchimp 자격증명/설정 (API 키는 암호화 저장).

    server_prefix(us21 등)는 비워두면 API 키 접미(-usXX)에서 자동 추출한다.
    list_id = 발송 대상 오디언스(리스트) ID. from_name/reply_to/subject 는 캠페인 설정.
    """

    __tablename__ = "mailchimp_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    encrypted_api_key: Mapped[str] = mapped_column(Text, default="")
    server_prefix: Mapped[str] = mapped_column(String(20), default="")   # us21 등
    list_id: Mapped[str] = mapped_column(String(40), default="")          # 오디언스(컨테이너) ID
    # 발송 대상 = 오디언스 전체가 아니라 이 태그(static 세그먼트) 구독자. 비우면 오디언스 전체.
    tag: Mapped[str] = mapped_column(String(120), default="")             # 태그(세그먼트) 이름
    from_name: Mapped[str] = mapped_column(String(120), default="")
    reply_to: Mapped[str] = mapped_column(String(200), default="")
    subject_default: Mapped[str] = mapped_column(String(300), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class MailStage(Base):
    """메일(작업) 하나에 대한 처리 단계별 상태.

    stage_key: os_download / release_note / fota_update /
               service_portal / kb_update / newsletter
    status:    pending(처리 전) / in_progress(처리 중) / done(처리 후)
    산출물(PDF/Excel 등)이 만들어지면 artifact_path 에 로컬 경로를 저장해 열람 제공.
    """

    __tablename__ = "mail_stages"
    __table_args__ = (
        UniqueConstraint("mail_id", "stage_key", name="uq_mail_stage"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    mail_id: Mapped[int] = mapped_column(
        ForeignKey("cached_mails.id", ondelete="CASCADE"), index=True
    )
    stage_key: Mapped[str] = mapped_column(String(50))
    # pending(대기) / running(실행 중) / review(검수 대기) / done(완료)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # 공용 보드: 이 단계를 마지막으로 조작한 사람(username). 표시용(잠금 아님).
    updated_by: Mapped[str] = mapped_column(String(80), default="")
    input_params: Mapped[str] = mapped_column(Text, default="")  # 단계 실행 입력(예: SFTP 경로)
    result_text: Mapped[str] = mapped_column(Text, default="")  # 실행 결과(검수용)
    artifact_type: Mapped[str] = mapped_column(String(20), default="")  # pdf/excel/...
    artifact_path: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class SeenUidl(Base):
    """이미 평가(다운로드/확인)한 메일의 UIDL 기록.

    필터에 걸러져 저장하지 않은 메일도 여기에는 남겨, 다음 동기화 때
    같은 메일을 다시 내려받지 않도록 한다.
    """

    __tablename__ = "seen_uidls"
    __table_args__ = (
        UniqueConstraint("user_id", "uidl", name="uq_seen_user_uidl"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 외래키를 걸지 않는다 — 공용 수신 메일함은 실제 계정이 아닌 0(mail.service.SHARED_UIDL_OWNER)을
    # 주인으로 쓴다. SQLite 는 외래키를 검사하지 않아 통과했지만 PostgreSQL 은 거부한다.
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    uidl: Mapped[str] = mapped_column(String(255), index=True)


class CachedMail(Base):
    """동기화해 온 메일. **팀 공용 보드**: user_id 는 '이 메일을 처음 받은 사람'(생성자)일 뿐,
    조회 필터가 아니다. 모든 계정이 같은 pm_ta@ 배포주소 메일을 각자 받으므로,
    같은 메일은 메일함마다 uidl 이 달라진다. 팀 전체에서 하나로 묶는 키는 **message_id**.
    """

    __tablename__ = "cached_mails"
    __table_args__ = (
        UniqueConstraint("user_id", "uidl", name="uq_user_uidl"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 이 메일을 처음 동기화한 사람(생성자). 공용 보드라 조회 필터로는 쓰지 않는다.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    uidl: Mapped[str] = mapped_column(String(255), index=True)  # 생성자 메일함 기준 고유 ID
    # 메일 헤더 Message-ID. 수신자(메일함)와 무관하게 동일 → 팀 공용 중복제거 키.
    message_id: Mapped[str] = mapped_column(String(500), default="", index=True)
    # 스레드 연결용 헤더(WhiteList 메일 묶기). 없으면 제목 규칙으로 폴백한다.
    in_reply_to: Mapped[str] = mapped_column(String(500), default="")
    refs: Mapped[str] = mapped_column(Text, default="")   # References 헤더(예약어라 이름만 변경)
    # 원본 수신자(전체 회신 시 참조인 보존). 헤더 문자열 그대로 저장.
    to_recipients: Mapped[str] = mapped_column(Text, default="")
    cc_recipients: Mapped[str] = mapped_column(Text, default="")
    # 앱이 보낸 메일 표식(X-TW-Automation). 있으면 신규 요청 후보에서 제외한다.
    app_marker: Mapped[str] = mapped_column(String(50), default="")
    # 첨부(엑셀/CSV) — WhiteList 대량 요청은 SN 을 첨부로 보낸다.
    attachment_names: Mapped[str] = mapped_column(Text, default="")
    attachment_serials: Mapped[str] = mapped_column(Text, default="")   # 첨부에서 뽑은 SN(줄바꿈)
    attachment_dir: Mapped[str] = mapped_column(Text, default="")       # 저장 폴더(원본 열람용)
    sender: Mapped[str] = mapped_column(String(400), default="")
    subject: Mapped[str] = mapped_column(String(1000), default="")
    received_at: Mapped[str] = mapped_column(String(100), default="")  # 원문 Date 헤더
    body_text: Mapped[str] = mapped_column(Text, default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    user: Mapped["User"] = relationship(back_populates="mails")


class FotaEntry(Base):
    """④ FOTA-DB Excel 에 신규로 추가한 항목 기록(서비스 DB 관리).

    누적(외부) 파일은 전체 이력을 갖고, 이 테이블은 '이번에 신규로 추가된 것'만
    추적해 중복 append 방지 + 신규분 식별에 쓴다. (OS_Type, version) 유일.
    """

    __tablename__ = "fota_entries"
    __table_args__ = (
        UniqueConstraint("os_type", "version", name="uq_fota_ostype_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # 메일 번호 또는 작업 키('job-18') — SQLite 는 정수 칸에 글자도 받아 줘서 둘 다 들어 있었다
    mail_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    os_type: Mapped[str] = mapped_column(String(60), index=True)   # 예: PM95GMS13
    version: Mapped[str] = mapped_column(String(80))               # 예: 95.07 (Jul 02 2026)
    os_version: Mapped[int] = mapped_column(Integer, default=0)    # FOTA 카운터
    osinfo_added: Mapped[int] = mapped_column(Integer, default=0)      # OsInfo 추가 행 수
    downloadpath_added: Mapped[int] = mapped_column(Integer, default=0)  # DownloadPath 추가 행 수
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class OsProduct(Base):
    """OS Update 대상 제품(SKU) — **제품명 + Android 버전 + Type** 조합 하나가 한 행.

    메일함이 '새로 온 배포 메일'을 한 줄로 나열하는 것과 달리, 이 표는 **우리가 관리하는
    제품 전체**를 기준으로 목록을 두고 그 위에서 OS Update 작업을 한다.

    Type 은 같은 모델·같은 Android 라도 배포 계열이 갈리는 구분(GMS · AOSP · Russia …)이며,
    목록에 없는 계열은 직접 입력한다. 세 값의 조합이 유일하다.

    경로 8개는 단계마다 다시 찾지 않도록 제품 등록 시 한 번에 적어 둔다.
    """

    __tablename__ = "os_products"
    __table_args__ = (
        UniqueConstraint("product", "android", "os_type", name="uq_os_product_sku"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product: Mapped[str] = mapped_column(String(40), index=True)    # PM95
    android: Mapped[str] = mapped_column(String(10), default="")    # 15
    os_type: Mapped[str] = mapped_column(String(30), default="")    # GMS | AOSP | Russia | 직접입력
    # 이 SKU 로 처음 출시한 OS 버전. 버전 판별의 기준선(이 앞으로는 이력이 없다).
    initial_os_version: Mapped[str] = mapped_column(String(20), default="")   # 예: 95.01
    # 제품군 — 목록을 접었다 펼치기 위한 **보기 구분**이다(파이프라인 동작에는 쓰지 않는다).
    category: Mapped[str] = mapped_column(String(30), default="")   # EDA · Tablet …

    # 사내 경로
    dev_rn_path: Mapped[str] = mapped_column(Text, default="")            # 개발 Release Note 파일
    # RN for Sales 는 전체 이력본과 **다른 폴더**에서 관리한다(영업 배포용 발췌본).
    sales_rn_path: Mapped[str] = mapped_column(Text, default="")          # RN for Sales 저장 폴더
    os_file_path: Mapped[str] = mapped_column(Text, default="")           # OS file (sdcard·flash 공통)
    # 게시 경로
    sp_rn_path: Mapped[str] = mapped_column(Text, default="")             # Service Portal RN
    kb_rn_path: Mapped[str] = mapped_column(Text, default="")             # Knowledge Base RN
    rn_url: Mapped[str] = mapped_column(Text, default="")                 # FOTA Release_Note_URL
    # Dropbox 경로 — 지금 ③이 올리는 목적지 네 곳과 1:1로 맞춘다.
    dropbox_os_sdcard_path: Mapped[str] = mapped_column(Text, default="")      # 개별 파일(NEW_FOTA)
    dropbox_os_sdcard_zip_path: Mapped[str] = mapped_column(Text, default="")  # sdcard zip
    dropbox_os_flash_path: Mapped[str] = mapped_column(Text, default="")       # flash zip
    dropbox_rn_path: Mapped[str] = mapped_column(Text, default="")             # Release Note

    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_by: Mapped[str] = mapped_column(String(80), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class OsProductRelease(Base):
    """제품(SKU)의 **개발 Release Note 출시 버전** 하나.

    등록한 '개발 Release Note 파일 경로'를 훑어 만들어진다(app/pipeline/dev_rn.py).
    문서는 로컬(work/dev_rn/{product_id}/)에 내려두고 그 경로를 여기 적어,
    목록에서 docx 다운로드 · pdf 열람이 되게 한다.

    목록 = 그 폴더의 현재 상태. 폴더에서 사라진 버전은 수집 때 함께 지워진다.
    """

    __tablename__ = "os_product_releases"
    __table_args__ = (
        UniqueConstraint("product_id", "version", name="uq_os_release_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("os_products.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[str] = mapped_column(String(40))        # 95.52 · 40.03C1 · TR54G15.03C1
    major: Mapped[str] = mapped_column(String(20), default="")   # 95 · TR54G15
    minor: Mapped[int] = mapped_column(Integer, default=0)       # 52  (정렬·범위 판정용)
    suffix: Mapped[str] = mapped_column(String(10), default="")  # '' · C1

    docx_name: Mapped[str] = mapped_column(String(300), default="")
    docx_path: Mapped[str] = mapped_column(Text, default="")
    pdf_name: Mapped[str] = mapped_column(String(300), default="")
    pdf_path: Mapped[str] = mapped_column(Text, default="")

    src_dir: Mapped[str] = mapped_column(Text, default="")      # 근거: 파일서버 폴더
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class OsUpdateJob(Base):
    """OS Update 작업 하나 = **제품(SKU) + OS 버전**.

    메일 짝(Submission↔Approval)에 매여 있던 작업 단위를 제품 기준으로 바꾼 것이다.
    메일은 '트리거·근거'로 나중에 붙는다(submission_mail_id / approval_mail_id).
    그래서 배포 메일이 없는 계열(AOSP·Russia)도 작업을 만들 수 있다 —
    [FIH] 트리거 메일은 전부 Google 승인 건이라 GMS 밖에는 오지 않는다(실측).

    파이프라인 공유 상태는 `job-{id}` 키로 저장한다(메일 작업의 mail_id 자리).
    """

    __tablename__ = "os_update_jobs"
    __table_args__ = (
        UniqueConstraint("product_id", "version", name="uq_os_job_product_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("os_products.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[str] = mapped_column(String(40))          # 이번에 배포할 버전 (95.53)
    note: Mapped[str] = mapped_column(Text, default="")       # 메모(선택)
    # 나중에 메일이 오면 연결한다(3단계). 없어도 작업은 진행된다.
    submission_mail_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approval_mail_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    # 실제로 돌리지 않고 '이미 끝난 회차'로 닫은 경우의 시각. 출시 이력을 근거로 소급
    # 완료할 때 찍는다 — 우리가 처리한 건과 과거 이력을 구분하기 위한 것이다.
    backfilled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 이 회차를 감지한 근거 메일의 **스냅샷**(JSON: mail_id·subject·version·date·not_re).
    # 배포 공지로 잡힌 회차는 붙일 Submission/Approval 이 없어, 시작하는 순간 근거가
    # 화면에서 사라졌다. 메일 id 만 두면 메일이 지워졌을 때 무엇이었는지도 남지 않으므로
    # 제목·날짜까지 함께 박아 둔다.
    basis_mails: Mapped[str] = mapped_column(Text, default="")


class OsUpdateJobStage(Base):
    """작업의 단계 상태 — 메일 작업의 MailStage 에 해당한다.

    갈래(side)를 키에 넣는다. ⑥⑦ 은 Submission·Approval 양쪽에 있고 서로 다른 행이라
    (job_id, stage_key) 만으로는 구분되지 않는다.
    """

    __tablename__ = "os_update_job_stages"
    __table_args__ = (
        UniqueConstraint("job_id", "side", "stage_key", name="uq_os_job_stage"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("os_update_jobs.id", ondelete="CASCADE"), index=True
    )
    side: Mapped[str] = mapped_column(String(20))             # submission | approval
    stage_key: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    input_params: Mapped[str] = mapped_column(Text, default="")
    result_text: Mapped[str] = mapped_column(Text, default="")
    artifact_type: Mapped[str] = mapped_column(String(20), default="")
    artifact_path: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(80), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Staff(Base):
    """사내 인원 명부 — **메일을 보내오는 사람**을 관리자가 직접 관리한다.

    로그인 계정(User)과 별개다. 대부분의 직원은 앱 계정이 없고, 우리는 그 사람이
    보낸 메일을 어떻게 다룰지만 알면 된다.

    이 명부가 예전에 흩어져 있던 두 목록을 대신한다:
      · is_dqa      → DQA FOTA 테스트 전용 처리 대상(예전: work/whitelist_dqa_senders.json)
      · is_handler  → 화이트리스트 처리 담당. 이 사람이 보낸 메일은 **완료 안내·회신**이라
                      신규 요청 목록에서 뺀다(예전: mail_filter.EXCLUDED_SENDERS 하드코딩)

    status 는 표시용이다. 퇴사했다고 역할을 자동으로 끄지 않는다 — 메일함에는 과거
    메일이 남아 있어서, 재직 중 보낸 요청은 계속 맞게 분류돼야 한다. 끄거나 지우는
    것은 관리자가 판단한다(사용자 확정 2026-08-06).
    """

    __tablename__ = "staff"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), default="")
    email: Mapped[str] = mapped_column(String(200), default="", unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="재직")   # 재직 · 퇴사
    is_dqa: Mapped[bool] = mapped_column(Boolean, default=False)
    is_handler: Mapped[bool] = mapped_column(Boolean, default=False)
    team: Mapped[str] = mapped_column(String(80), default="")
    memo: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(80), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class SharedMailbox(Base):
    """팀 **공용 수신 메일함** — 메일 동기화는 항상 이 계정 하나로만 한다.

    각 사용자가 [계정 관리]에 등록하는 개인 계정은 **발송 전용**이다(개인 받은편지함이
    공용 보드로 올라오지 않도록). 관리자가 [설정]에서 이 공용 수신 계정을 등록한다.
    행은 하나만 쓴다(id=1).
    """

    __tablename__ = "shared_mailbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_address: Mapped[str] = mapped_column(String(200), default="")
    encrypted_password: Mapped[str] = mapped_column(Text, default="")
    pop_server: Mapped[str] = mapped_column(String(120), default="")
    pop_port: Mapped[int] = mapped_column(Integer, default=995)
    use_ssl: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str] = mapped_column(String(80), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class MailTemplate(Base):
    """앱이 보내는 메일의 **문구**. 사람이 고쳐 쓰는 자리다.

    기본 문구는 코드(app/mail_templates.py)에 있고 여기에는 **고친 것만** 남는다.
    행이 없으면 기본 문구를 쓴다 — 그래서 '기본값으로 되돌리기'는 행을 지우는 일이다.
    """

    __tablename__ = "mail_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(80), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class AppSetting(Base):
    """앱 전체에 걸리는 **사람이 내린 결정**. 열쇠가 아니라 '어떻게 돌 것인가' 다.

    자격증명은 저마다 전용 표가 있고, 회차별 작업물은 work/ 에 있다. 여기 남는 것은
    그 둘 어디에도 속하지 않는 한 줄짜리 설정이다 — 지금은 Claude 를 구독으로 부를지
    API 열쇠로 부를지 하나뿐이다.

    env 는 '처음 켤 때의 기본', 이 표는 '사람이 화면에서 내린 결정' 으로 본다.
    한 번 화면에서 바꾸면 env 를 고쳐도 바뀌지 않는다(app/app_settings.py).
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(80), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class PipelineState(Base):
    """작업(메일 · 'job-N')별 파이프라인 공유 상태 — 예전 work/pipeline_state/<키>.json.

    단계마다 결과(OS 파일 경로, 버전, Dropbox 링크, 결재 상태…)를 쌓아 다음 단계가 읽는다.
    PostgreSQL 에서는 JSONB 로 둬서 목록 화면이 파일 수십 개를 여는 대신 한 번에 읽는다.
    """

    __tablename__ = "pipeline_state"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class PipelineStateAlias(Base):
    """Submission/Approval 짝 — Approval 키의 상태는 Submission 키의 상태를 쓴다(예전 _alias.json)."""

    __tablename__ = "pipeline_state_alias"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    canonical: Mapped[str] = mapped_column(String(60))
