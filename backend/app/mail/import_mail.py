"""메일 파일(.eml) 직접 등록 — 공용 수신함으로 못 받는 메일을 사람이 직접 넣는다.

왜 필요한가: 동기화는 공용 수신함(pm_ta@) 하나만 본다. 그래서
  · 개인 메일함에만 온 메일
  · 이미 prune 으로 지워진 오래된 메일(seen 기록이 남아 재동기화로 안 들어옴)
  · 서버에서 이미 사라진 메일
은 자동으로는 절대 들어오지 않는다. 이때 Outlook 에서 내려받은 파일을 올려 등록한다.

동기화로 들어온 메일과 **같은 테이블(CachedMail)** 에 저장하므로, 등록 즉시 작업 짝
맞춤(Submission↔Approval)·Claude 검색·WhiteList 판별에 그대로 쓰인다.

seen_uidls 에는 넣지 않는다 — 나중에 같은 메일이 서버에서 들어와도 막지 않기 위해서다
(그때는 Message-ID 로 중복 제거된다).
"""
from __future__ import annotations

import hashlib

from app.database import get_session
from app.mail.pop3_client import _parse_message
from app.models import CachedMail

# 사람이 직접 올린 메일의 uidl 접두어 — 서버 UIDL 과 절대 겹치지 않게.
MANUAL_PREFIX = "manual:"


def _dedup_key(m) -> str:
    """동기화(app.mail.service._dedup_key)와 **같은 규칙**이어야 중복이 걸린다."""
    if m.message_id:
        return m.message_id
    raw = f"{m.subject}\x00{m.received_at}\x00{m.sender_addr}".encode("utf-8", "replace")
    return "fallback:" + hashlib.sha1(raw).hexdigest()


def parse_file(raw: bytes, filename: str):
    """파일 바이트 → ParsedMail. 지원하지 않는 형식이면 (None, 사유)."""
    name = (filename or "").lower()
    if name.endswith(".msg"):
        # Outlook 독점 형식(OLE) — 표준 email 모듈로는 못 읽는다.
        try:
            import extract_msg  # noqa: F401
        except ImportError:
            return None, (
                ".msg 는 Outlook 전용 형식이라 그대로는 읽지 못합니다. "
                "Outlook 에서 '다른 이름으로 저장 → 형식: 텍스트(.eml)' 로 받거나, "
                "웹메일에서 내려받으면 .eml 로 저장됩니다. "
                "(.msg 를 계속 쓰시려면 `pip install extract-msg` 후 다시 시도)"
            )
        return _parse_msg(raw), ""
    # .eml / .txt(원문 소스) — 둘 다 RFC822 원문이라 같은 파서로 읽힌다.
    uidl = MANUAL_PREFIX + hashlib.sha1(raw).hexdigest()
    return _parse_message(uidl, raw), ""


def _parse_msg(raw: bytes):
    """extract_msg 가 있으면 .msg → RFC822 로 바꿔 같은 파서를 태운다."""
    import io

    import extract_msg

    msg = extract_msg.Message(io.BytesIO(raw))
    eml = msg.asEmailMessage().as_bytes()
    return _parse_message(MANUAL_PREFIX + hashlib.sha1(raw).hexdigest(), eml)


def import_file(raw: bytes, filename: str, user_id: int) -> dict:
    """메일 파일 하나를 CachedMail 에 등록한다.

    반환 {status: added|duplicate|error, subject, mail_id, message}
    """
    if not raw:
        return {"status": "error", "subject": filename, "message": "빈 파일입니다."}
    try:
        parsed, err = parse_file(raw, filename)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "subject": filename, "message": f"파싱 실패: {e}"}
    if parsed is None:
        return {"status": "error", "subject": filename, "message": err}
    # 메일 원문인지 확인 — 헤더가 하나도 없으면 그냥 텍스트 파일이다.
    # (email 모듈은 아무 텍스트나 '본문만 있는 메시지'로 읽어주기 때문에 이 검사가 없으면
    #  메일이 아닌 파일이 조용히 메일로 등록된다.)
    if not (parsed.subject or parsed.sender_addr or parsed.message_id or parsed.received_at):
        return {"status": "error", "subject": filename,
                "message": "메일 원문이 아닙니다(제목·발신자·날짜 헤더가 없음). "
                           "Outlook 에서 저장한 .eml 파일이 맞는지 확인해 주세요."}
    if not (parsed.body_text or parsed.body_html):
        return {"status": "error", "subject": parsed.subject or filename,
                "message": "본문을 읽지 못했습니다."}

    key = _dedup_key(parsed)
    with get_session() as db:
        dup = (
            db.query(CachedMail)
            .filter((CachedMail.message_id == key) | (CachedMail.uidl == parsed.uidl))
            .first()
        )
        if dup is not None:
            # 이미 있는 메일이라도 **첨부/수신자 정보가 비어 있으면 채워 준다**(백필).
            # 첨부(엑셀 SN)를 나중에 읽도록 기능이 붙기 전에 받은 메일을 재등록해 갱신하는 통로.
            filled = []
            for fld in ("attachment_names", "attachment_serials", "attachment_dir",
                        "to_recipients", "cc_recipients"):
                val = getattr(parsed, fld, "")
                if val and not getattr(dup, fld, ""):
                    setattr(dup, fld, val)
                    filled.append(fld)
            if filled:
                db.commit()
                from app.whitelist import mail_filter as _MF
                _MF._CLASSIFY_CACHE.pop(dup.id, None)   # 판정 캐시 무효화(첨부 SN 반영)
                return {"status": "updated", "subject": dup.subject, "mail_id": dup.id,
                        "message": f"기존 메일에 첨부/수신자 정보를 채웠습니다({', '.join(filled)})."}
            return {"status": "duplicate", "subject": dup.subject, "mail_id": dup.id,
                    "message": "이미 등록된 메일입니다."}
        row = CachedMail(
            user_id=user_id,            # 등록한 사람(생성자 기록용)
            uidl=parsed.uidl,
            message_id=key,
            sender=parsed.sender,
            subject=parsed.subject,
            received_at=parsed.received_at,
            body_text=parsed.body_text,
            body_html=parsed.body_html,
            in_reply_to=parsed.in_reply_to,
            refs=parsed.references,
            to_recipients=getattr(parsed, "to_recipients", ""),
            cc_recipients=getattr(parsed, "cc_recipients", ""),
            app_marker=getattr(parsed, "app_marker", ""),
            attachment_names=getattr(parsed, "attachment_names", ""),
            attachment_serials=getattr(parsed, "attachment_serials", ""),
            attachment_dir=getattr(parsed, "attachment_dir", ""),
        )
        db.add(row)
        db.commit()
        return {"status": "added", "subject": row.subject, "mail_id": row.id,
                "message": "등록했습니다."}
