"""
PMDM License — 8080 의 /licenses(app/pages/license.py) 를 옮긴 것.

  신청(직접 입력 · 메일에서 가져오기) → 승인 요청 메일(최종 승인자)
  관리자: 키 생성·공개키 재검증 → 승인(인증서 PDF → 결과 메일) / 반려(사유 메일) · 메모 · 재발송 · 삭제(감사기록)

8080 과 달라진 점
  · 승인을 단계로 나눠 돌려준다(키 → 검증 → 상태 → 인증서 → 메일). 인증서가 실패해도
    승인은 남고, 같은 화면에서 [인증서 다시 만들기] → [결과 메일 보내기] 로 이어 간다.
  · 결과 메일을 언제 누구에게 보냈는지 남긴다(work/licenses/result_mail_log.json).
  · 메일에서 가져오기 — 받은 메일의 보낸 사람·본문·첨부(PO)로 신청서를 채운다.

RSA 키(LICENSE_RSA_*)는 응답·로그 어디에도 내보내지 않는다. 내보내는 것은 발급된 License Key 뿐이다.
메일은 app.shadow 의 검증 모드에서 보내지 않고 기록만 한다.
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import shadow, user_mailer
from app.database import BASE_DIR, get_session
from app.license import cert, licensing, mailer, service
from app.license.db import init_license_db
from app.license.models import LicenseRequest
from app.models import CachedMail
from common import user

import license_inbox as LI

router = APIRouter(prefix="/api/license")
init_license_db()

MAX_PO = 20 * 1024 * 1024
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PO_EXT = {".pdf", ".doc", ".docx", ".jpg", ".jpeg", ".png", ".xlsx", ".xls"}
WORK = (BASE_DIR / "work").resolve()
MAIL_LOG = service.STORAGE_DIR / "result_mail_log.json"
_log_lock = threading.Lock()


# ── 공통 ───────────────────────────────────────────────────────────────────────

def _admin_ok(u: dict) -> bool:
    return service.is_admin(u.get("username"))


def _need_admin(request: Request) -> dict:
    u = user(request)
    if not _admin_ok(u):
        raise HTTPException(403, "License 결재 권한이 없습니다")
    return u


def _get(doc_id: str) -> LicenseRequest:
    r = service.get(doc_id)
    if not r or r.status == service.STATUS_DELETED:
        raise HTTPException(404, "요청이 없습니다")
    return r


def _qty_int(v) -> int:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return 0


def _mail_log() -> dict:
    try:
        return json.loads(MAIL_LOG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _log_mail(doc_id: str, entry: dict) -> None:
    with _log_lock:
        d = _mail_log()
        d.setdefault(doc_id, []).append(entry)
        MAIL_LOG.parent.mkdir(parents=True, exist_ok=True)
        MAIL_LOG.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def _row(r: LicenseRequest) -> dict:
    return {
        "id": r.id, "doc_id": r.doc_id, "status": r.status, "date_issued": r.date_issued,
        "company_name": r.company_name, "quantity": _qty_int(r.quantity),
        "contact_name": r.contact_name, "contact_email": r.contact_email,
        "license_user_id": r.license_user_id, "created_by_name": r.created_by_name,
        "has_key": bool(r.license_key), "has_cert": bool(r.cert_path and os.path.exists(r.cert_path)),
        "confirm_date": r.confirm_date, "confirm_by": r.confirm_by,
    }


def _verify(r: LicenseRequest) -> dict | None:
    if not r.license_key:
        return None
    chk = licensing.verify_license_key(r.license_key)
    if not chk.get("ok"):
        return {"ok": False, "error": "공개키로 복호화하지 못했습니다"}
    id_ok = chk["id"] == (r.license_user_id or "")
    cnt_ok = str(chk["count"]) == service.norm_qty(r.quantity)
    issued = ""
    try:
        issued = datetime.fromtimestamp(int(chk["time_ms"]) / 1000).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        pass
    return {"ok": id_ok and cnt_ok, "id": chk["id"], "count": chk["count"], "id_ok": id_ok,
            "count_ok": cnt_ok, "issued_at": issued}


def _recipient(r: LicenseRequest) -> str:
    return ((user_mailer.user_email(r.created_by) if r.created_by else "")
            or r.recipient_email or r.contact_email or "")


def _detail(r: LicenseRequest, u: dict) -> dict:
    admin = _admin_ok(u)
    preview = None
    if admin and r.status in (service.STATUS_APPROVED, service.STATUS_REJECTED):
        m = (mailer.build_approval(r, bool(r.cert_path)) if r.status == service.STATUS_APPROVED
             else mailer.build_rejection(r))
        preview = {"to": _recipient(r), "subject": m["subject"].strip(), "body": m["body"],
                   "attachment": f"{r.doc_id}_License_Certificate.pdf"
                   if r.status == service.STATUS_APPROVED and r.cert_path else ""}
    return {
        **_row(r),
        "note": r.note, "admin_note": r.admin_note if admin or r.status == service.STATUS_REJECTED else "",
        "part_number": r.part_number, "type": r.type,
        "recovery_email": r.recovery_email, "recipient_email": r.recipient_email,
        "license_key": r.license_key if (admin or r.status == service.STATUS_APPROVED) else "",
        "verify": _verify(r) if admin else None,
        "po": {"name": r.po_filename or "PO", "url": f"/api/license/files/po/{r.id}"} if r.po_path else None,
        "cert": {"name": os.path.basename(r.cert_path), "url": f"/api/license/files/cert/{r.id}"}
        if r.cert_path and os.path.exists(r.cert_path) else None,
        "mails": _mail_log().get(r.doc_id, []) if admin else [],
        "mail_preview": preview,
        "created_at": r.created_at.isoformat(timespec="seconds") if r.created_at else "",
        "can_admin": admin,
    }


# ── 조회 ───────────────────────────────────────────────────────────────────────

@router.get("/overview")
def overview(request: Request, scope: str = "auto"):
    u = user(request)
    admin = _admin_ok(u)
    mine = scope == "mine" or not admin
    rows = service.list_mine(u.get("id"), u.get("username", "")) if mine else service.list_all()
    s = service.stats() if admin else None
    if s:
        s = {"total": s["total"], "pending": s["counts"].get(service.STATUS_PENDING, 0),
             "approved": s["counts"].get(service.STATUS_APPROVED, 0),
             "rejected": s["counts"].get(service.STATUS_REJECTED, 0),
             "total_qty": s["total_qty"], "approved_qty": s["approved_qty"]}
    return {"is_admin": admin, "scope": "mine" if mine else "all", "keys_ready": licensing.keys_configured(),
            "cert_template": cert._template_path().exists(), "stats": s, "requests": [_row(r) for r in rows],
            "live": shadow.area_live("license")}


@router.get("/requests/{doc_id}")
def detail(doc_id: str, request: Request):
    u = user(request)
    r = _get(doc_id)
    if not (_admin_ok(u) or r.created_by == u.get("id") or r.created_by_name == u.get("username")):
        raise HTTPException(403, "권한이 없습니다")
    return _detail(r, u)


# ── 신청 ───────────────────────────────────────────────────────────────────────

class NewIn(BaseModel):
    company_name: str
    quantity: int
    contact_name: str
    contact_email: str
    license_user_id: str
    note: str = ""
    po_name: str = ""
    po_b64: str = ""             # 직접 올린 PO
    mail_id: int | None = None   # 메일에서 가져온 PO
    mail_attachment: str = ""
    source_mail_id: int | None = None   # 신청서를 만든 근거 메일(메일함에서 처리됨으로)


def _mail_attachment_path(mail_id: int, name: str) -> Path:
    with get_session() as db:
        m = db.get(CachedMail, mail_id)
        if m is None or not m.attachment_dir:
            raise HTTPException(404, "메일 첨부를 찾지 못했습니다")
        folder = Path(m.attachment_dir)
    p = (folder / os.path.basename(name)).resolve()
    try:
        p.relative_to(WORK)
    except ValueError:
        raise HTTPException(404, "메일 첨부를 찾지 못했습니다")
    if not p.is_file():
        raise HTTPException(404, "메일 첨부 파일이 없습니다")
    return p


@router.post("/requests")
def create(body: NewIn, request: Request):
    u = user(request)
    f = {k: (getattr(body, k) or "").strip() for k in ("company_name", "contact_name", "contact_email", "license_user_id")}
    missing = [lab for k, lab in (("company_name", "Company"), ("contact_name", "Contact"),
                                  ("contact_email", "E-mail"), ("license_user_id", "ID")) if not f[k]]
    if body.quantity < 1:
        missing.append("Quantity")
    if missing:
        raise HTTPException(400, f"비어 있는 항목: {', '.join(missing)}")
    if not _EMAIL_RE.match(f["contact_email"]):
        raise HTTPException(400, f"이메일 형식이 아닙니다: {f['contact_email']}")
    if body.po_b64:
        try:
            data = base64.b64decode(body.po_b64, validate=True)
        except ValueError:
            raise HTTPException(400, "PO 파일을 읽지 못했습니다")
        name = os.path.basename(body.po_name or "PO")
    elif body.mail_id and body.mail_attachment:
        p = _mail_attachment_path(body.mail_id, body.mail_attachment)
        data, name = p.read_bytes(), p.name
    else:
        raise HTTPException(400, "PO 파일이 없습니다")
    if len(data) > MAX_PO:
        raise HTTPException(400, "PO 파일은 20MB 까지입니다")
    if os.path.splitext(name)[1].lower() not in _PO_EXT:
        raise HTTPException(400, "PO 는 PDF·Word·Excel·이미지 파일만 받습니다")

    tmp_id = datetime.now().strftime("%y%m%d.%H%M%S")
    po_path, po_name = service.save_po_file(tmp_id, data, name)
    req = service.create_request({**f, "quantity": body.quantity, "note": (body.note or "").strip(),
                                  "date_issued": datetime.now().strftime("%Y-%m-%d"),
                                  "po_path": po_path, "po_filename": po_name}, u.get("id"), u.get("username", ""))

    to_admins = user_mailer.final_reviewer_emails()
    mail = {"ok": False, "error": "최종 승인자 이메일이 등록돼 있지 않습니다"}
    if to_admins:
        from app import mail_templates as MT

        subject, text = MT.render(
            "license_request", 신청자=u.get("username", ""), 문서번호=req.doc_id, 회사=req.company_name,
            수량=req.quantity, 담당자=f"{req.contact_name} ({req.contact_email})",
            라이선스아이디=req.license_user_id, 비고=req.note or "-")
        ok, err = user_mailer.send_as_user(u["id"], to_admins, subject.strip(), text)
        mail = {"ok": ok, "error": err, "to": to_admins}
    src = body.source_mail_id or body.mail_id
    if src:
        LI.set_status(src, "created", req.doc_id, u.get("username", ""))
    return {"ok": True, "doc_id": req.doc_id, "request_mail": mail}


@router.get("/mail-candidates")
def mail_candidates(request: Request, q: str = ""):
    """신청서로 옮길 메일 고르기 — 비우면 규칙에 걸린 License 메일, 검색어를 주면 제목·보낸 사람."""
    user(request)
    if q.strip():
        like = f"%{q.strip()}%"
        with get_session() as db:
            rows = (db.query(CachedMail).filter(CachedMail.subject.like(like) | CachedMail.sender.like(like))
                    .order_by(CachedMail.id.desc()).limit(40).all())
            db.expunge_all()
    else:
        rows = LI.candidates()[:40]
    return {"mail": [{"id": m.id, "subject": m.subject, "sender": m.sender, "received_at": m.received_at,
                      "attachments": LI.attachments(m)[:8]} for m in rows]}


@router.get("/mail/{mail_id}/prefill")
def mail_prefill(mail_id: int, request: Request):
    """메일 → 신청서 초안. Claude 가 본문·첨부를 읽어 찾은 칸만 채운다(처음이면 이 자리에서 읽는다)."""
    user(request)
    try:
        d = LI.detail(mail_id)
    except KeyError:
        raise HTTPException(404, "메일이 없습니다")
    return {**d["fields"], "note": d["note"], "attachments": d["attachments"], "po_guess": d["po_guess"],
            "missing": d["missing"], "is_request": d["is_request"], "reason": d["reason"], "error": d["error"]}


# ── 메일로 들어온 신청 ─────────────────────────────────────────────────────────────

@router.get("/inbox")
def inbox(request: Request):
    user(request)
    out = LI.inbox()
    if out["pending"]:
        LI.scan_async()        # 아직 안 읽은 후보는 뒤에서 읽고, 화면은 다시 물어 온다
    return out


@router.get("/inbox/{mail_id}")
def inbox_detail(mail_id: int, request: Request):
    user(request)
    try:
        return LI.detail(mail_id)
    except KeyError:
        raise HTTPException(404, "메일이 없습니다")


@router.post("/inbox/{mail_id}/rescan")
def inbox_rescan(mail_id: int, request: Request):
    user(request)
    try:
        LI.interpret(mail_id)
        return LI.detail(mail_id)
    except KeyError:
        raise HTTPException(404, "메일이 없습니다")


@router.post("/inbox/{mail_id}/dismiss")
def inbox_dismiss(mail_id: int, request: Request):
    u = user(request)
    LI.set_status(mail_id, "dismissed", by=u.get("username", ""))
    return {"ok": True}


@router.post("/inbox/{mail_id}/restore")
def inbox_restore(mail_id: int, request: Request):
    u = user(request)
    LI.set_status(mail_id, "new", by=u.get("username", ""))
    return {"ok": True}


# ── 관리자: 키 · 승인 · 반려 ─────────────────────────────────────────────────────

class NoteIn(BaseModel):
    note: str = ""


def _pending(r: LicenseRequest) -> None:
    if r.status != service.STATUS_PENDING:
        raise HTTPException(409, f"이미 {r.status} 처리된 요청입니다")


def _make_key(r: LicenseRequest) -> str:
    if not licensing.keys_configured():
        raise HTTPException(409, "발급용 RSA 키가 설정돼 있지 않습니다")
    if not (r.license_user_id and service.norm_qty(r.quantity)):
        raise HTTPException(409, "ID·Quantity 가 있어야 키를 만듭니다")
    try:
        key, _ = licensing.generate_license_key(r.license_user_id, service.norm_qty(r.quantity))
    except Exception:  # noqa: BLE001  (사유에 키 정보가 섞일 수 있어 내보내지 않는다)
        raise HTTPException(500, "키 생성에 실패했습니다")
    service.save_license_key(r.doc_id, key)
    return key


@router.post("/requests/{doc_id}/key")
def gen_key(doc_id: str, request: Request):
    u = _need_admin(request)
    r = _get(doc_id)
    _pending(r)
    _make_key(r)
    return _detail(service.get(doc_id), u)


def _send_result(r: LicenseRequest, kind: str, pdf: bytes | None, u: dict, *, resend: bool = False) -> dict:
    to = _recipient(r)
    if not to:
        return {"ok": False, "error": "받는 사람 이메일이 없습니다"}
    m = mailer.build_approval(r, pdf is not None) if kind == "approval" else mailer.build_rejection(r)
    att = [(f"{r.doc_id}_License_Certificate.pdf", pdf)] if (kind == "approval" and pdf) else None
    ok, err = user_mailer.send_as_user(u["id"], to, m["subject"].strip(), m["body"], att)
    _log_mail(r.doc_id, {"at": datetime.now().isoformat(timespec="seconds"), "kind": kind, "to": to,
                         "by": u.get("username", ""), "ok": ok, "error": err, "resend": resend,
                         "attached": bool(att), "shadow": not shadow.area_live("license")})
    return {"ok": ok, "error": err, "to": to}


def _cert_bytes(r: LicenseRequest) -> tuple[bytes | None, str]:
    _, pdf, err = cert.render_and_save(r)
    return pdf, err


@router.post("/requests/{doc_id}/approve")
def approve(doc_id: str, body: NoteIn, request: Request):
    """승인 — 키(없으면 생성) → 공개키 재검증(불일치면 멈춤) → 승인 → 인증서 → 결과 메일."""
    u = _need_admin(request)
    return {**decide_approve(doc_id, body.note or "", u), "detail": _detail(service.get(doc_id), u)}


def decide_approve(doc_id: str, note: str, u: dict) -> dict:
    """승인 본문 — 화면(위)과 검수 웹 동기화(confirm_sync)가 같이 쓴다. u: {id, username} 결재 권한자."""
    if not _admin_ok(u):
        raise HTTPException(403, "License 결재 권한이 없습니다")
    r = _get(doc_id)
    _pending(r)
    steps: list[dict] = []
    if not r.license_key:
        _make_key(r)
        r = service.get(doc_id)
        steps.append({"step": "key", "ok": True, "msg": "License Key 생성"})
    v = _verify(r)
    if not v or not v["ok"]:
        raise HTTPException(409, "License Key 의 ID·수량이 신청과 다릅니다 — 키를 다시 만드세요")
    steps.append({"step": "verify", "ok": True, "msg": f"공개키 검증 · ID {v['id']} · {v['count']}대"})
    r = service.set_status(doc_id, service.STATUS_APPROVED, note or "", u.get("username", ""))
    steps.append({"step": "status", "ok": True, "msg": "승인"})
    pdf, err = _cert_bytes(r)
    if pdf is None:
        steps.append({"step": "cert", "ok": False, "msg": f"인증서 생성 실패 — {err}"})
        steps.append({"step": "mail", "ok": False, "msg": "인증서가 없어 결과 메일을 보내지 않았습니다"})
    else:
        steps.append({"step": "cert", "ok": True, "msg": "인증서 PDF 생성"})
        res = _send_result(service.get(doc_id), "approval", pdf, u)
        steps.append({"step": "mail", "ok": res["ok"],
                      "msg": (f"결과 메일 → {res.get('to', '')}" if res["ok"] else f"결과 메일 실패 — {res['error']}")})
    return {"steps": steps}


@router.post("/requests/{doc_id}/reject")
def reject(doc_id: str, body: NoteIn, request: Request):
    u = _need_admin(request)
    return {**decide_reject(doc_id, body.note or "", u), "detail": _detail(service.get(doc_id), u)}


def decide_reject(doc_id: str, note: str, u: dict) -> dict:
    """반려 본문 — 화면(위)과 검수 웹 동기화(confirm_sync)가 같이 쓴다."""
    if not _admin_ok(u):
        raise HTTPException(403, "License 결재 권한이 없습니다")
    r = _get(doc_id)
    _pending(r)
    if not (note or "").strip():
        raise HTTPException(400, "반려 사유를 적으세요")
    r = service.set_status(doc_id, service.STATUS_REJECTED, note.strip(), u.get("username", ""))
    res = _send_result(r, "rejection", None, u)
    steps = [{"step": "status", "ok": True, "msg": "반려"},
             {"step": "mail", "ok": res["ok"],
              "msg": (f"반려 메일 → {res.get('to', '')}" if res["ok"] else f"반려 메일 실패 — {res['error']}")}]
    return {"steps": steps}


@router.post("/requests/{doc_id}/cert")
def regen_cert(doc_id: str, request: Request):
    u = _need_admin(request)
    r = _get(doc_id)
    if r.status != service.STATUS_APPROVED:
        raise HTTPException(409, "승인된 요청만 인증서를 만듭니다")
    pdf, err = _cert_bytes(r)
    if pdf is None:
        raise HTTPException(500, f"인증서 생성 실패 — {err}")
    return _detail(service.get(doc_id), u)


@router.post("/requests/{doc_id}/send")
def send_result(doc_id: str, request: Request):
    """결과 메일 보내기 · 다시 보내기 — 승인이면 인증서 첨부(없으면 보내지 않는다)."""
    u = _need_admin(request)
    r = _get(doc_id)
    if r.status not in (service.STATUS_APPROVED, service.STATUS_REJECTED):
        raise HTTPException(409, "처리되지 않은 요청입니다")
    pdf = None
    if r.status == service.STATUS_APPROVED:
        if not (r.cert_path and os.path.exists(r.cert_path)):
            raise HTTPException(409, "인증서가 없습니다 — 인증서를 먼저 만드세요")
        pdf = Path(r.cert_path).read_bytes()
    resend = bool(_mail_log().get(doc_id))
    res = _send_result(r, "approval" if pdf else "rejection", pdf, u, resend=resend)
    if not res["ok"]:
        raise HTTPException(502, f"메일 발송 실패 — {res['error']}")
    return _detail(service.get(doc_id), u)


@router.post("/requests/{doc_id}/note")
def save_note(doc_id: str, body: NoteIn, request: Request):
    u = _need_admin(request)
    _get(doc_id)
    service.save_note(doc_id, body.note or "")
    return _detail(service.get(doc_id), u)


class DeleteIn(BaseModel):
    reason: str = ""


@router.post("/requests/{doc_id}/delete")
def delete(doc_id: str, body: DeleteIn, request: Request):
    u = _need_admin(request)
    _get(doc_id)
    service.delete_request(doc_id, body.reason or "", u.get("username", ""))
    return {"ok": True}


# ── 파일(본인·관리자만) ─────────────────────────────────────────────────────────

@router.get("/files/{kind}/{req_id}")
def files(kind: str, req_id: int, request: Request):
    u = user(request)
    r = service.get_by_id(req_id)
    if not r or kind not in ("po", "cert"):
        raise HTTPException(404, "파일이 없습니다")
    if not (_admin_ok(u) or (r.created_by is not None and r.created_by == u.get("id"))):
        raise HTTPException(403, "권한이 없습니다")
    path = r.po_path if kind == "po" else r.cert_path
    if not path or not os.path.exists(path):
        raise HTTPException(404, "파일이 없습니다")
    ext = os.path.splitext(path)[1].lower()
    media = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".png": "image/png"}.get(ext, "application/octet-stream")
    name = os.path.basename(path)
    from urllib.parse import quote

    return FileResponse(path, media_type=media,
                        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}"})
