"""고객 포털 — 고객사·계정 관리와 고객에게 보여줄 요청 조회.

사내 화면(WhiteList 페이지의 [고객사]·[계정] 탭)과 외부 데모(8082)가 같이 쓴다.

**반출 범위**(기획 확정): 접수번호·접수일·모델·단말 개수·진행 단계·어느 고객사 건인지까지.
S/N, PO 파일·번호·금액, 유무상, 사내 담당자·주소, 원본 메일, OS Type 은 내보내지 않는다.
그래서 고객에게 나가는 값은 반드시 이 모듈의 `customer_requests` / `customer_detail`
을 거치게 한다 — 화면이 ORM 객체를 직접 만지면 그 경계가 쉽게 무너진다.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

from sqlalchemy import func

from app.security import hash_password, verify_password
from app.whitelist.db import whitelist_session
from app.whitelist.models import WLCompany, WLDevice, WLPortalAccount, WLRequest

# 고객에게 보여줄 진행 상태. 사내 상태값을 그대로 노출하지 않는다.
_CUSTOMER_STATE: dict[str, tuple[str, str]] = {
    "pending": ("접수", "접수되었습니다. 검토 중입니다."),
    "confirmed": ("완료", "등록이 완료되었습니다."),
    "handled": ("완료", "처리가 완료되었습니다."),
    "rejected": ("반려", "요청이 반려되었습니다."),
}
_DEFAULT_STATE = ("진행 중", "처리 중입니다.")


# ── 고객사 ────────────────────────────────────────────────────────────────
def list_companies() -> list[dict]:
    """고객사 목록 + 계정 수·연결된 요청 수."""
    with whitelist_session() as db:
        rows = db.query(WLCompany).order_by(WLCompany.name).all()
        acc = dict(
            db.query(WLPortalAccount.company_id, func.count(WLPortalAccount.id))
            .group_by(WLPortalAccount.company_id).all()
        )
        req = dict(
            db.query(WLRequest.company_id, func.count(WLRequest.id))
            .filter(WLRequest.company_id.isnot(None))
            .group_by(WLRequest.company_id).all()
        )
        return [{
            "id": c.id, "name": c.name, "email_domain": c.email_domain or "",
            "active": bool(c.active), "accounts": acc.get(c.id, 0),
            "requests": req.get(c.id, 0),
            "created_at": c.created_at,
        } for c in rows]


def add_company(name: str, email_domain: str = "") -> tuple[bool, str]:
    name = (name or "").strip()
    if not name:
        return False, "이름을 입력하세요."
    dom = (email_domain or "").strip().lstrip("@").lower()
    with whitelist_session() as db:
        if db.query(WLCompany).filter(func.lower(WLCompany.name) == name.lower()).first():
            return False, f"'{name}' 은(는) 이미 있습니다."
        db.add(WLCompany(name=name, email_domain=dom))
        db.commit()
    return True, f"'{name}' 을(를) 추가했습니다."


def update_company(company_id: int, *, name: str | None = None,
                   email_domain: str | None = None,
                   active: bool | None = None) -> tuple[bool, str]:
    with whitelist_session() as db:
        c = db.get(WLCompany, company_id)
        if c is None:
            return False, "고객사를 찾을 수 없습니다."
        if name is not None and name.strip():
            c.name = name.strip()
        if email_domain is not None:
            c.email_domain = email_domain.strip().lstrip("@").lower()
        if active is not None:
            c.active = 1 if active else 0
        db.commit()
    return True, "저장했습니다."


def delete_company(company_id: int) -> tuple[bool, str]:
    """계정이 남아 있으면 지우지 않는다 — 계정이 소속을 잃으면 로그인할 수 없다."""
    with whitelist_session() as db:
        c = db.get(WLCompany, company_id)
        if c is None:
            return False, "고객사를 찾을 수 없습니다."
        n = db.query(func.count(WLPortalAccount.id)).filter_by(company_id=company_id).scalar()
        if n:
            return False, f"계정 {n}개가 소속돼 있어 삭제할 수 없습니다."
        db.query(WLRequest).filter_by(company_id=company_id).update({"company_id": None})
        db.delete(c)
        db.commit()
    return True, "삭제했습니다."


# ── 포털 계정 ─────────────────────────────────────────────────────────────
_STATUS_LABEL = {"active": "활성", "invited": "최초 로그인 전", "suspended": "중지"}


def status_label(s: str) -> str:
    return _STATUS_LABEL.get(s, s)


def list_accounts() -> list[dict]:
    with whitelist_session() as db:
        rows = (db.query(WLPortalAccount, WLCompany)
                .join(WLCompany, WLCompany.id == WLPortalAccount.company_id)
                .order_by(WLCompany.name, WLPortalAccount.username).all())
        return [{
            "id": a.id, "username": a.username, "email": a.email or "",
            "company_id": a.company_id, "company": c.name, "status": a.status,
            "created_at": a.created_at, "last_login_at": a.last_login_at,
            "must_change": bool(a.must_change),
        } for a, c in rows]


# 발급·초기화 때 넣는 초기 비밀번호. **고정값**이다(사용자 결정).
# 아이디만 알면 누구나 로그인할 수 있다는 뜻이므로, 대외로 열기 전에는
# WL_PORTAL_INITIAL_PASSWORD 로 바꾸거나 최초 로그인 시 변경을 강제해야 한다.
INITIAL_PASSWORD = os.getenv("WL_PORTAL_INITIAL_PASSWORD", "1234")


def _new_password() -> str:
    """초기 비밀번호."""
    return INITIAL_PASSWORD


def add_account(company_id: int, username: str, email: str = "",
                password: str = "") -> tuple[bool, str, str]:
    """계정 발급. (ok, 메시지, 초기 비밀번호)."""
    username = (username or "").strip().lower()
    if not company_id:
        return False, "소속 고객사를 선택하세요.", ""
    if not re.fullmatch(r"[a-z0-9._-]{3,40}", username or ""):
        return False, "아이디는 영문 소문자·숫자·.-_ 3자 이상입니다.", ""
    pw = (password or "").strip() or _new_password()
    with whitelist_session() as db:
        if db.get(WLCompany, company_id) is None:
            return False, "고객사를 찾을 수 없습니다.", ""
        if db.query(WLPortalAccount).filter_by(username=username).first():
            return False, f"아이디 '{username}' 은(는) 이미 있습니다.", ""
        db.add(WLPortalAccount(company_id=company_id, username=username,
                               email=(email or "").strip(),
                               password_hash=hash_password(pw), status="invited"))
        db.commit()
    return True, f"'{username}' 계정을 발급했습니다.", pw


def reset_password(account_id: int) -> tuple[bool, str, str]:
    pw = _new_password()
    with whitelist_session() as db:
        a = db.get(WLPortalAccount, account_id)
        if a is None:
            return False, "계정을 찾을 수 없습니다.", ""
        a.password_hash = hash_password(pw)
        a.status = "invited"
        a.must_change = 1          # 초기값으로 돌아갔으니 다시 바꾸게 한다
        db.commit()
        name = a.username
    return True, f"'{name}' 비밀번호를 초기화했습니다.", pw


# 새 비밀번호 규칙. 길게 요구하면 고객이 못 외워 다시 문의가 오므로 최소만 건다.
MIN_PASSWORD_LEN = 8


def check_new_password(new: str, again: str) -> str:
    """새 비밀번호 검사. 문제가 없으면 빈 문자열."""
    new = new or ""
    if len(new) < MIN_PASSWORD_LEN:
        return f"비밀번호는 {MIN_PASSWORD_LEN}자 이상이어야 합니다."
    if new != (again or ""):
        return "새 비밀번호가 서로 다릅니다."
    if new == INITIAL_PASSWORD:
        return "초기 비밀번호와 다른 값으로 정해 주세요."
    return ""


# ── 비밀번호 재설정(고객이 잊었을 때) ────────────────────────────────────
# 링크 유효시간. 짧으면 메일을 늦게 확인한 고객이 다시 요청해야 하고, 길면 링크가
# 오래 살아 있는다. 한 시간이면 사내 담당자에게 다시 묻는 일이 거의 없다.
RESET_TTL_MIN = int(os.getenv("WL_PORTAL_RESET_TTL_MIN", "60"))
PORTAL_URL = os.getenv("WL_PORTAL_URL", "http://localhost:8082").rstrip("/")


def request_reset(who: str) -> dict:
    """재설정 링크를 만든다. 아이디 또는 이메일로 찾는다.

    **계정이 있는지는 화면에 알리지 않는다** — 남의 아이디를 넣어 보고 존재 여부를
    알아내는 것을 막기 위해서다. 호출부는 결과와 무관하게 같은 안내를 보여야 한다.
    반환 {found, username, email, url} — 메일 발송·로그용이다.
    """
    import secrets
    from datetime import timedelta

    key = (who or "").strip().lower()
    if not key:
        return {"found": False}
    with whitelist_session() as db:
        a = (db.query(WLPortalAccount)
             .filter((func.lower(WLPortalAccount.username) == key)
                     | (func.lower(WLPortalAccount.email) == key))
             .first())
        if a is None or a.status == "suspended":
            return {"found": False}
        token = secrets.token_urlsafe(32)
        a.reset_token = token
        a.reset_expires = datetime.now() + timedelta(minutes=RESET_TTL_MIN)
        db.commit()
        return {"found": True, "username": a.username, "email": a.email or "",
                "url": f"{PORTAL_URL}/reset?token={token}"}


def verify_reset(token: str) -> dict | None:
    """링크가 아직 쓸 수 있는지. 되면 {account_id, username}."""
    token = (token or "").strip()
    if not token:
        return None
    with whitelist_session() as db:
        a = db.query(WLPortalAccount).filter_by(reset_token=token).first()
        if a is None or not a.reset_expires or a.reset_expires < datetime.now():
            return None
        return {"account_id": a.id, "username": a.username}


def apply_reset(token: str, new: str, again: str) -> tuple[bool, str]:
    """링크로 새 비밀번호를 정한다. 성공하면 링크는 즉시 죽는다."""
    err = check_new_password(new, again)
    if err:
        return False, err
    with whitelist_session() as db:
        a = db.query(WLPortalAccount).filter_by(reset_token=(token or "").strip()).first()
        if a is None or not a.reset_expires or a.reset_expires < datetime.now():
            return False, "링크가 만료되었습니다. 다시 요청해 주세요."
        a.password_hash = hash_password(new)
        a.must_change = 0          # 본인이 정한 값이므로 강제 변경 대상이 아니다
        a.status = "active"
        a.reset_token = ""
        a.reset_expires = None
        db.commit()
    return True, "비밀번호를 다시 설정했습니다. 새 비밀번호로 로그인해 주세요."


def send_reset_mail(email: str, username: str, url: str) -> tuple[bool, str]:
    """재설정 안내 메일. 발송 계정이 없으면 (False, 사유) — 화면 안내는 바뀌지 않는다."""
    if not email:
        return False, "계정에 이메일이 등록돼 있지 않습니다."
    try:
        from app.user_mailer import send_as_user, system_sender_user_id
    except Exception as e:  # noqa: BLE001
        return False, f"메일 모듈 로드 실패: {e}"

    sender = system_sender_user_id()
    if sender is None:
        return False, "발송 계정이 설정되지 않았습니다."
    from app import mail_templates as MT

    subject, body = MT.render("portal_reset", 이름=username, 재설정주소=url,
                              유효시간=RESET_TTL_MIN)
    return send_as_user(sender, email, subject, body, as_system=True)


def change_password(account_id: int, current: str, new: str,
                    again: str) -> tuple[bool, str]:
    """고객이 스스로 바꾼다. 현재 비밀번호를 맞혀야 하고, 바꾸면 강제 플래그가 풀린다."""
    err = check_new_password(new, again)
    if err:
        return False, err
    with whitelist_session() as db:
        a = db.get(WLPortalAccount, account_id)
        if a is None:
            return False, "계정을 찾을 수 없습니다."
        if not (a.password_hash and verify_password(current or "", a.password_hash)):
            return False, "현재 비밀번호가 맞지 않습니다."
        a.password_hash = hash_password(new)
        a.must_change = 0
        a.status = "active"
        db.commit()
    return True, "비밀번호를 바꿨습니다."


def set_account_status(account_id: int, status: str) -> tuple[bool, str]:
    if status not in _STATUS_LABEL:
        return False, f"알 수 없는 상태: {status}"
    with whitelist_session() as db:
        a = db.get(WLPortalAccount, account_id)
        if a is None:
            return False, "계정을 찾을 수 없습니다."
        a.status = status
        db.commit()
    return True, f"상태를 '{status_label(status)}' 로 바꿨습니다."


def delete_account(account_id: int) -> tuple[bool, str]:
    with whitelist_session() as db:
        a = db.get(WLPortalAccount, account_id)
        if a is None:
            return False, "계정을 찾을 수 없습니다."
        db.delete(a)
        db.commit()
    return True, "계정을 삭제했습니다."


def authenticate(username: str, password: str) -> dict | None:
    """고객 포털 로그인. 성공하면 {account_id, username, company_id, company}."""
    username = (username or "").strip().lower()
    with whitelist_session() as db:
        a = db.query(WLPortalAccount).filter_by(username=username).first()
        if a is None or a.status == "suspended":
            return None
        if not (a.password_hash and verify_password(password or "", a.password_hash)):
            return None
        c = db.get(WLCompany, a.company_id)
        if c is None or not c.active:
            return None
        a.last_login_at = datetime.now()
        if not a.must_change:
            a.status = "active"
        db.commit()
        return {"account_id": a.id, "username": a.username,
                "company_id": c.id, "company": c.name,
                "must_change": bool(a.must_change)}


# ── 요청 연결 ─────────────────────────────────────────────────────────────
def set_request_company(request_id: str, company_id: int | None) -> tuple[bool, str]:
    with whitelist_session() as db:
        r = db.get(WLRequest, request_id)
        if r is None:
            return False, "요청을 찾을 수 없습니다."
        r.company_id = company_id or None
        db.commit()
    return True, "고객사를 지정했습니다." if company_id else "고객사 지정을 해제했습니다."


# ── 고객에게 보이는 것(반출 범위 통과분만) ────────────────────────────────
def _state_of(status: str) -> tuple[str, str]:
    return _CUSTOMER_STATE.get(status or "", _DEFAULT_STATE)


def customer_requests(company_id: int) -> list[dict]:
    """그 고객사 요청 목록. S/N·PO·금액은 담지 않는다."""
    with whitelist_session() as db:
        rows = (db.query(WLRequest)
                .filter(WLRequest.company_id == company_id,
                        WLRequest.status != "deleted")
                .order_by(WLRequest.created_at.desc()).all())
        counts = dict(
            db.query(WLDevice.request_id, func.count(WLDevice.id))
            .filter(WLDevice.request_id.in_([r.id for r in rows] or [""]))
            .group_by(WLDevice.request_id).all()
        )
        models: dict = {}
        for rid, model in (db.query(WLDevice.request_id, WLDevice.model)
                           .filter(WLDevice.request_id.in_([r.id for r in rows] or [""]))
                           .distinct().all()):
            if model:
                models.setdefault(rid, set()).add(model)
        out = []
        for r in rows:
            label, _ = _state_of(r.status)
            out.append({
                "id": r.id,
                "created_at": r.created_at,
                "models": ", ".join(sorted(models.get(r.id, []))) or "—",
                "devices": counts.get(r.id, 0),
                "state": label,
                "status": r.status,
            })
        return out


def customer_detail(company_id: int, request_id: str) -> dict | None:
    """요청 1건 상세. **소속 고객사가 다르면 None** — 남의 건은 조회 자체가 안 된다."""
    with whitelist_session() as db:
        r = db.get(WLRequest, request_id)
        if r is None or r.company_id != company_id or r.status == "deleted":
            return None
        n = db.query(func.count(WLDevice.id)).filter_by(request_id=r.id).scalar() or 0
        models = sorted({m for (m,) in db.query(WLDevice.model)
                         .filter_by(request_id=r.id).distinct().all() if m})
        # 'Android 13 → 15'. 한쪽만 있는 단말도 있어 짝이 맞을 때만 화살표를 쓴다.
        pairs = set()
        for a, b in (db.query(WLDevice.cur_os, WLDevice.upg_os)
                     .filter_by(request_id=r.id).distinct().all()):
            a, b = (a or "").strip(), (b or "").strip()
            if a and b:
                pairs.add((int(a) if a.isdigit() else 0, f"{a} → {b}"))
            elif a or b:
                pairs.add((int(a or b) if (a or b).isdigit() else 0, a or b))
        oses = [txt for _, txt in sorted(pairs)]
        label, desc = _state_of(r.status)
        # 단계는 **항상 같은 개수**로 보여 준다 — 어디까지 왔는지 한눈에 보이게 하기 위해서다.
        # 날짜가 없는 단계는 아직 오지 않은 것이다(화면에서 회색 점).
        did = r.processed_at
        done = r.status in ("confirmed", "handled")
        if r.status == "rejected":
            steps = [
                {"at": r.created_at, "title": "신청 접수", "desc": f"단말 {n:,}대"},
                {"at": did, "title": "검토", "desc": ""},
                {"at": did, "title": "반려", "desc": desc},
            ]
        else:
            steps = [
                {"at": r.created_at, "title": "신청 접수", "desc": f"단말 {n:,}대"},
                {"at": did if r.status != "pending" else None, "title": "검토",
                 "desc": "등록 가능 여부를 확인합니다" if r.status == "pending" else ""},
                {"at": did if done else None, "title": "등록 처리",
                 "desc": f"단말 {n:,}대 등록" if done else ""},
                {"at": did if done else None, "title": "완료",
                 "desc": desc if done else ""},
            ]
        return {
            "id": r.id, "created_at": r.created_at, "state": label, "status": r.status,
            "state_desc": desc, "devices": n,
            "models": ", ".join(models) or "—",
            "os": ", ".join(oses) or "—",
            "steps": steps,
        }
