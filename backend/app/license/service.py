"""License 요청 비즈니스 로직 (CRUD · Doc ID · 상태전환 · 삭제감사 · 통계 · 파일저장).

DB 는 전용 pmdm_license.db(app.license.db) — 기존 tw_app.db 와 완전 분리.
관리자 판별은 env(LICENSE_ADMIN_USERS), 미설정이면 기본 {admin}.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from app.atomic_io import atomic_write_bytes
from app.database import BASE_DIR  # 파일 저장 위치 계산용(엔진과 무관)
from app.license.db import license_session
from app.license.models import LicenseRequest

STORAGE_DIR = BASE_DIR / "work" / "licenses"
PO_DIR = STORAGE_DIR / "po"
CERT_DIR = STORAGE_DIR / "cert"

TYPE_MAP = {"PMDMCE": "Cloud", "PMDMPE": "Onpremise", "PMDMME": "Onpremise"}

STATUS_PENDING = "대기중"
STATUS_APPROVED = "승인"
STATUS_REJECTED = "반려"
STATUS_DELETED = "deleted"


def _ensure_dirs() -> None:
    for d in (PO_DIR, CERT_DIR):
        d.mkdir(parents=True, exist_ok=True)


def is_admin(username: str | None) -> bool:
    """License 결재 권한이 있는가.

    셋 중 하나면 관리자다:
      · 'admin' 계정(내장 — env 와 무관)
      · env LICENSE_ADMIN_USERS(쉼표구분)에 적힌 계정
      · **역할이 시스템 관리자(admin) 또는 최종 승인자(final_reviewer)**
    역할을 함께 보는 이유: License 승인은 최종 승인자의 결재 업무다(2026-08-05 확정).
    계정 이름만 보던 예전 방식으로는 역할을 새로 만들어도 권한이 따라오지 않았다.
    """
    if not username:
        return False
    name = username.strip().lower()
    admins = {"admin"}  # 내장 관리자 계정 — env 와 무관하게 항상 관리자
    raw = os.getenv("LICENSE_ADMIN_USERS", "").strip()
    admins |= {u.strip().lower() for u in raw.split(",") if u.strip()}
    if name in admins:
        return True
    try:
        from app.auth import FINAL_REVIEWER_ROLE
        from app.database import get_session
        from app.models import User

        with get_session() as db:
            u = db.query(User).filter(User.username == username).first()
        return bool(u) and (u.role or "") in ("admin", FINAL_REVIEWER_ROLE)
    except Exception:  # noqa: BLE001  (조회 실패가 화면을 막지 않게)
        return False


def generate_doc_id(db) -> str:
    """yyMMdd.NNNN (삭제분 포함 전체 순번 기반 — Doc ID 재사용 방지)."""
    seq = db.query(LicenseRequest).count() + 1
    today = datetime.now(timezone.utc).astimezone().strftime("%y%m%d")
    return f"{today}.{seq:04d}"


def save_po_file(doc_id: str, data: bytes, filename: str) -> tuple[str, str]:
    _ensure_dirs()
    safe = os.path.basename(filename) or "PO"
    dest = PO_DIR / f"{doc_id}_{safe}"
    atomic_write_bytes(dest, data)
    return str(dest), safe


def create_request(data: dict, user_id: int | None, username: str) -> LicenseRequest:
    with license_session() as db:
        doc_id = generate_doc_id(db)
        req = LicenseRequest(
            doc_id=doc_id,
            date_issued=data.get("date_issued")
            or datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d"),
            status=STATUS_PENDING,
            part_number=data.get("part_number") or "PMDMCE",  # PMDMCE 고정
            type=TYPE_MAP.get(data.get("part_number") or "PMDMCE", "-"),
            quantity=norm_qty(data.get("quantity", "")),
            company_name=data.get("company_name", "").strip(),
            contact_name=data.get("contact_name", "").strip(),
            contact_email=data.get("contact_email", "").strip(),
            region=data.get("region", "").strip(),
            note=data.get("note", "").strip(),
            license_user_id=data.get("license_user_id", "").strip(),
            recovery_email=data.get("recovery_email", "").strip(),
            recipient_email=data.get("recipient_email", "").strip(),
            po_path=data.get("po_path", ""),
            po_filename=data.get("po_filename", ""),
            created_by=user_id,
            created_by_name=username or "",
        )
        db.add(req)
        db.commit()
        db.refresh(req)
        return req


def list_all(include_deleted: bool = False) -> list[LicenseRequest]:
    with license_session() as db:
        q = db.query(LicenseRequest)
        if not include_deleted:
            q = q.filter(LicenseRequest.status != STATUS_DELETED)
        return q.order_by(LicenseRequest.id.desc()).all()


def list_mine(user_id: int | None, username: str) -> list[LicenseRequest]:
    with license_session() as db:
        q = db.query(LicenseRequest).filter(LicenseRequest.status != STATUS_DELETED)
        if user_id is not None:
            q = q.filter(
                (LicenseRequest.created_by == user_id)
                | (LicenseRequest.created_by_name == (username or ""))
            )
        else:
            q = q.filter(LicenseRequest.created_by_name == (username or ""))
        return q.order_by(LicenseRequest.id.desc()).all()


def get(doc_id: str) -> LicenseRequest | None:
    with license_session() as db:
        return db.query(LicenseRequest).filter_by(doc_id=doc_id).first()


def get_by_id(req_id: int) -> LicenseRequest | None:
    with license_session() as db:
        return db.query(LicenseRequest).filter_by(id=req_id).first()


def _now_str() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


def set_status(doc_id: str, status: str, admin_note: str, admin_name: str) -> LicenseRequest | None:
    with license_session() as db:
        req = db.query(LicenseRequest).filter_by(doc_id=doc_id).first()
        if not req:
            return None
        req.status = status
        req.admin_note = admin_note or ""
        req.confirm_date = _now_str()
        req.confirm_by = admin_name or ""
        db.commit()
        db.refresh(req)
        return req


def save_license_key(doc_id: str, license_key: str) -> bool:
    with license_session() as db:
        req = db.query(LicenseRequest).filter_by(doc_id=doc_id).first()
        if not req:
            return False
        req.license_key = license_key or ""
        db.commit()
        return True


def save_note(doc_id: str, admin_note: str) -> bool:
    with license_session() as db:
        req = db.query(LicenseRequest).filter_by(doc_id=doc_id).first()
        if not req:
            return False
        req.admin_note = admin_note or ""
        db.commit()
        return True


def delete_request(doc_id: str, reason: str, admin_name: str) -> bool:
    """소프트 삭제(감사기록 유지) — status=deleted + 삭제일/자/사유 기록."""
    with license_session() as db:
        req = db.query(LicenseRequest).filter_by(doc_id=doc_id).first()
        if not req:
            return False
        req.status = STATUS_DELETED
        req.deleted_at = _now_str()
        req.deleted_by = admin_name or ""
        req.delete_reason = reason or ""
        db.commit()
        return True


def save_cert_path(doc_id: str, path: str) -> None:
    with license_session() as db:
        req = db.query(LicenseRequest).filter_by(doc_id=doc_id).first()
        if req:
            req.cert_path = path
            db.commit()


def cert_dest(doc_id: str) -> Path:
    _ensure_dirs()
    return CERT_DIR / f"{doc_id}_License_Certificate.pdf"


def _qty(v) -> int:
    try:
        return int(float(str(v).strip()))   # "1000.0" 같은 값도 처리
    except (TypeError, ValueError):
        return 0


def norm_qty(v) -> str:
    """수량을 정수 문자열로 정규화('1000.0' → '1000')."""
    s = str(v if v is not None else "").strip()
    try:
        return str(int(float(s)))
    except (TypeError, ValueError):
        return s


def stats() -> dict:
    """관리자 통계 대시보드용 집계(삭제분 제외)."""
    rows = list_all()
    counts = {STATUS_PENDING: 0, STATUS_APPROVED: 0, STATUS_REJECTED: 0}
    total_qty = approved_qty = 0
    by_part: dict[str, dict] = {}
    by_region: dict[str, int] = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
        q = _qty(r.quantity)
        total_qty += q
        if r.status == STATUS_APPROVED:
            approved_qty += q
        p = r.part_number or "-"
        b = by_part.setdefault(p, {"count": 0, "qty": 0})
        b["count"] += 1
        b["qty"] += q
        rg = r.region or "-"
        by_region[rg] = by_region.get(rg, 0) + 1
    return {
        "total": len(rows),
        "counts": counts,
        "total_qty": total_qty,
        "approved_qty": approved_qty,
        "by_part": by_part,
        "by_region": by_region,
    }
