"""WhiteList 모듈 ORM 모델 (whitelist.db 전용).

Apps Script 스프레드시트 스키마를 그대로 옮김:
  Requests(WLRequest) 1 : N Devices(WLDevice), 그리고 Rules(WLRule).
  Settings 시트(admin 계정)는 앱 로그인/역할로 대체 예정 → 별도 모델 없음.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.whitelist.db import WhitelistBase


def _now() -> datetime:
    return datetime.now()


class WLRule(WhitelistBase):
    """Model + (FromOS→ToOS) + Category → OS Type 매핑 규칙.

    category 로 같은 모델·버전 전환에 여러 OS Type 을 병행 등록한다.
    GMS(일반 업데이트)·DQA(DQA팀 요청)·KT·LG·기타. 요청 구분에 따라 알맞은 ostype 을 고른다.
    """

    __tablename__ = "wl_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model: Mapped[str] = mapped_column(String(40), index=True)   # 예: PM95
    from_os: Mapped[int] = mapped_column(Integer)               # Android From (예: 13)
    to_os: Mapped[int] = mapped_column(Integer)                 # Upgrade To (예: 15)
    ostype: Mapped[str] = mapped_column(String(60))            # 예: White95TtoV
    # 요청 구분: GMS·DQA·KT·LG·기타(기본). 같은 (model,from,to)에 카테고리별로 공존.
    category: Mapped[str] = mapped_column(String(20), default="기타", index=True)


class WLCompany(WhitelistBase):
    """고객사·파트너사 — 고객 포털 계정이 소속되는 회사.

    코드와 구분(최종고객/파트너사)은 두지 않는다 — 실제로 쓰이지 않고, 요청 귀속은
    '요청한 주체' 하나로 정해지므로 이름만 있으면 된다.
    email_domain 은 선택이다. 개인 메일로 요청하는 고객이 있어 비워 둘 수 있다.
    """

    __tablename__ = "wl_companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    email_domain: Mapped[str] = mapped_column(String(120), default="", index=True)
    active: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class WLPortalAccount(WhitelistBase):
    """고객 포털(외부) 로그인 계정. 사내 계정(User)과 완전히 별개다."""

    __tablename__ = "wl_portal_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("wl_companies.id", ondelete="CASCADE"), index=True
    )
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(160), default="")
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    # active(활성) / invited(최초 로그인 전) / suspended(중지)
    status: Mapped[str] = mapped_column(String(20), default="invited", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 초기 비밀번호가 고정값(1234)이라, 발급·초기화 직후에는 반드시 바꾸게 한다.
    # 1 이면 로그인해도 비밀번호 변경 화면 밖으로 나갈 수 없다.
    must_change: Mapped[int] = mapped_column(Integer, default=1)
    # 비밀번호 재설정 링크. 한 번에 하나만 살아 있고, 쓰면 즉시 지운다.
    reset_token: Mapped[str] = mapped_column(String(80), default="", index=True)
    reset_expires: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WLRequest(WhitelistBase):
    """화이트리스트 등록 신청 1건(단말 N대 묶음)."""

    __tablename__ = "wl_requests"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # req-xxxx (클라 생성)
    # 어느 고객사 건인지. 비어 있으면 고객 화면에 나타나지 않는다(README 의 전제).
    company_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # 신청자 — 앱 로그인 사용자 기준(격리 auth 결정 시 조정)
    requester_username: Mapped[str] = mapped_column(String(80), default="", index=True)
    requester_email: Mapped[str] = mapped_column(String(160), default="")
    requester_name: Mapped[str] = mapped_column(String(120), default="")
    team: Mapped[str] = mapped_column(String(80), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    # pending / confirmed / rejected / deleted
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    memo: Mapped[str] = mapped_column(Text, default="")

    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    processed_by: Mapped[str] = mapped_column(String(120), default="")
    reject_comment: Mapped[str] = mapped_column(Text, default="")
    fota_status: Mapped[str] = mapped_column(String(30), default="")  # fota_sent/failed/nodevice

    # 요청 구분(어느 규칙 카테고리로 등록했는지): GMS / DQA / KT / LG / 기타
    category: Mapped[str] = mapped_column(String(20), default="")

    # 유/무상
    charge_type: Mapped[str] = mapped_column(String(10), default="")   # 유상 / 무상
    amount: Mapped[str] = mapped_column(String(40), default="")
    po_number: Mapped[str] = mapped_column(String(80), default="")
    po_file_path: Mapped[str] = mapped_column(Text, default="")        # 로컬 저장 경로(드라이브 대체)
    free_reason: Mapped[str] = mapped_column(Text, default="")

    # 메일함에서 처리한 건이면 원본 메일 정보(회신·중복추적용).
    source_mail_id: Mapped[str] = mapped_column(String(30), default="")     # CachedMail.id
    requester_addr: Mapped[str] = mapped_column(String(200), default="")    # 회신 받을 주소
    mail_lang: Mapped[str] = mapped_column(String(4), default="")           # ko / en
    mail_message_id: Mapped[str] = mapped_column(String(500), default="")   # 회신 스레드 연결
    reply_status: Mapped[str] = mapped_column(String(20), default="")       # sent / failed / ""

    # 삭제 기록(soft delete)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_by: Mapped[str] = mapped_column(String(120), default="")
    delete_reason: Mapped[str] = mapped_column(Text, default="")

    # 넣은 순서대로 — PostgreSQL 은 정렬을 주지 않으면 순서를 보장하지 않는다(SQLite 는 대개 id 순이었다)
    devices: Mapped[list["WLDevice"]] = relationship(
        back_populates="request", cascade="all, delete-orphan", order_by="WLDevice.id"
    )


class WLDevice(WhitelistBase):
    """신청에 포함된 단말 1대."""

    __tablename__ = "wl_devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("wl_requests.id", ondelete="CASCADE"), index=True
    )
    sn: Mapped[str] = mapped_column(String(40), index=True)
    android_api: Mapped[str] = mapped_column(String(10), default="")
    support_end_date: Mapped[str] = mapped_column(String(20), default="")  # YYYY-MM-DD
    partnumber: Mapped[str] = mapped_column(String(60), default="")
    ostype: Mapped[str] = mapped_column(String(60), default="")
    model: Mapped[str] = mapped_column(String(40), default="")
    cur_os: Mapped[str] = mapped_column(String(10), default="")
    upg_os: Mapped[str] = mapped_column(String(10), default="")

    request: Mapped["WLRequest"] = relationship(back_populates="devices")
