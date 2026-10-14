"""License 발급 요청 모델 — 전용 DB(pmdm_license.db)에만 생성된다.

app.license.db.LicenseBase 에 등록 → 기존 tw_app.db 와 완전 분리.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.license.db import LicenseBase


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LicenseRequest(LicenseBase):
    """PMDM License Key 발급 요청 1건.

    status: 대기중 / 승인 / 반려 / deleted(소프트 삭제, 감사기록 유지)
    """

    __tablename__ = "license_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    date_issued: Mapped[str] = mapped_column(String(20), default="")
    status: Mapped[str] = mapped_column(String(10), default="대기중")

    part_number: Mapped[str] = mapped_column(String(20), default="")
    type: Mapped[str] = mapped_column(String(20), default="")
    quantity: Mapped[str] = mapped_column(String(20), default="")

    company_name: Mapped[str] = mapped_column(String(200), default="")
    contact_name: Mapped[str] = mapped_column(String(120), default="")
    contact_email: Mapped[str] = mapped_column(String(200), default="")
    region: Mapped[str] = mapped_column(String(120), default="")   # 폐지(폼/템플릿에서 제거, 컬럼은 보존)
    note: Mapped[str] = mapped_column(Text, default="")            # 신청자 비고(폼 입력 → 관리자 상세 표시)

    license_user_id: Mapped[str] = mapped_column(String(120), default="")
    recovery_email: Mapped[str] = mapped_column(String(200), default="")
    recipient_email: Mapped[str] = mapped_column(String(200), default="")

    license_key: Mapped[str] = mapped_column(Text, default="")  # RSA 2048 키는 344자 → Text
    admin_note: Mapped[str] = mapped_column(Text, default="")
    confirm_date: Mapped[str] = mapped_column(String(30), default="")
    confirm_by: Mapped[str] = mapped_column(String(120), default="")

    cert_path: Mapped[str] = mapped_column(Text, default="")
    po_path: Mapped[str] = mapped_column(Text, default="")
    po_filename: Mapped[str] = mapped_column(String(300), default="")

    # 신청자(로그인 사용자) 스냅샷
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_by_name: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    # 소프트 삭제 감사(원본 Apps Script 와 동일 개념)
    deleted_at: Mapped[str] = mapped_column(String(30), default="")
    deleted_by: Mapped[str] = mapped_column(String(120), default="")
    delete_reason: Mapped[str] = mapped_column(Text, default="")
