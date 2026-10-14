"""[FIH] 트리거 메일 파서 — 제품·Android·빌드지문을 **규칙으로** 뽑는다.

FIH/Foxconn GMS tracking system 이 자동 발송하는 메일이라 본문이 항상 같은 표 구조다:

    Brand \n POINTMOBILE \n Device \n MF52e \n Product \n MF52e
    Android OS \n 15
    Build fingerprint \n POINTMOBILE/MF52e/MF52e:15/TP1A.220624.014/RA4:user/release-keys

그래서 제품·Android 판별에 Claude 를 부를 이유가 없다(mail_analysis 는 자유형식 메일용).
캐시된 트리거 메일 19통 전부에서 Device·Android·빌드지문이 이 규칙으로 잡힌다.

**제목에도 본문에도 OS 버전(95.53 등)은 없다.** 메일이 알려주는 건 SKU(제품+Android)와
승인 시점까지다. 그래서 메일만으로는 어느 작업인지 특정할 수 없고, 후보가 하나일 때만
자동 연결하고 여럿이면 사람이 고르게 한다(app/os_update_jobs.py).
"""
from __future__ import annotations

import re

_LABEL = r"[ \t]*[\r\n]+[ \t]*"          # 'Label' 다음 줄에 값이 온다


def _field(body: str, label: str, value_re: str = r"[^\r\n]+") -> str:
    m = re.search(re.escape(label) + _LABEL + f"({value_re})", body or "", re.I)
    return m.group(1).strip() if m else ""


# 빌드지문: Brand/Device/Product:Android/빌드ID/증분:user/release-keys
# 증분 빌드값에 하이픈이 들어가는 제품이 있다(PM45: 'vAQAK-0') — 문자군에 '-' 를 포함한다.
_FP_RE = re.compile(
    r"(?P<brand>[A-Za-z0-9_.-]+)/(?P<device>[A-Za-z0-9_.-]+)/(?P<product>[A-Za-z0-9_.-]+)"
    r":(?P<android>\d{1,2})/(?P<build_id>[A-Za-z0-9_.-]+)/(?P<incremental>[A-Za-z0-9_.-]+)"
    r":(?P<type>[a-z]+)/(?P<tags>[a-z-]+)"
)


def parse(subject: str, body: str) -> dict:
    """트리거 메일 → {side, product, android, fingerprint, …}. 못 읽은 값은 빈 문자열.

    product 는 제품 등록(OsProduct.product)과 맞추기 위해 대문자로 통일한다
    ('MF52e' → 'MF52E'). android 는 숫자만.
    """
    from app.mail.service import extract_model, is_approval_mail, is_submission_mail

    body = body or ""
    side = ("approval" if is_approval_mail(subject)
            else "submission" if is_submission_mail(subject) else "")

    fp = _field(body, "Build fingerprint", r"\S+")
    m = _FP_RE.match(fp)

    # 본문 표 → 없으면 빌드지문 → 없으면 제목에서. 셋 다 같은 값을 가리킨다(실측 19/19).
    device = _field(body, "Device", r"\S+") or (m.group("device") if m else "")
    product = device or extract_model(subject)
    android = _field(body, "Android OS", r"\d{1,2}") or (m.group("android") if m else "")

    return {
        "side": side,
        "product": re.sub(r"\s+", "", product).upper(),
        "android": android,
        "brand": _field(body, "Brand", r"\S+") or (m.group("brand") if m else ""),
        "fingerprint": fp,
        "build_id": m.group("build_id") if m else "",
        "incremental": m.group("incremental") if m else "",
        "submission_type": _field(body, "Submission type", r"\S+"),
        "submission_date": _field(body, "Submission date", r"\d{4}-\d{2}-\d{2}"),
        "estimated_approval_date": _field(
            body, "Estimated approval date", r"\d{4}-\d{2}-\d{2}"),
    }


def parse_mail(mail) -> dict:
    """CachedMail 행 하나를 파싱. 본문은 text 가 없으면 html 에서 뽑는다."""
    from app.mail.search import _body

    d = parse(mail.subject or "", _body(mail))
    d["mail_id"] = mail.id
    d["subject"] = " ".join((mail.subject or "").split())
    d["received_at"] = mail.received_at or ""
    return d


def trigger_mails() -> list[dict]:
    """캐시된 [FIH] 트리거 메일 전부를 파싱해 반환(최신순)."""
    from app.database import get_session
    from app.mail.service import is_trigger_mail
    from app.models import CachedMail

    with get_session() as db:
        rows = [m for m in db.query(CachedMail).all()
                # 앱이 보낸 메일은 트리거가 아니다 — 표식(X-TW-Automation)으로 가른다.
                if not (getattr(m, "app_marker", "") or "").strip()
                and is_trigger_mail(m.subject)]
        out = [parse_mail(m) for m in rows]
    out.sort(key=lambda d: _ts(d["received_at"]), reverse=True)
    return out


def _ts(received_at: str) -> float:
    from email.utils import parsedate_to_datetime

    if not received_at:
        return float("-inf")
    try:
        dt = parsedate_to_datetime(received_at)
        if dt is not None:
            return dt.timestamp()
    except Exception:  # noqa: BLE001
        pass
    return float("-inf")


def received_date(received_at: str) -> str:
    """메일 Date 헤더 → 'YYYY-MM-DD'. 승인일로 쓴다(본문에 승인일 항목이 없다)."""
    from email.utils import parsedate_to_datetime

    if not received_at:
        return ""
    try:
        dt = parsedate_to_datetime(received_at)
        if dt is not None:
            return dt.strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        pass
    return received_at[:10]
