"""메일 동기화 서비스: DB 자격증명 → POP3 조회 → **전체 저장** → CachedMail.

정책(2026-07-08 변경): 더 이상 제목으로 필터링하지 않고 **모든 신규 메일을 동기화**한다.
이유: 작업 트리거는 [FIH][Approval] 이지만, 그 OS update 의 Redmine 이슈 번호/링크는
관련된 다른 메일에 들어있을 수 있어 **Claude 가 전체 메일에 접근**해야 하기 때문이다.
[FIH][Approval] 메일은 '작업 트리거'로만 구분한다(동기화 필터가 아님).
"""
from __future__ import annotations

import hashlib
import os
import re

from app.database import get_session
from app.mail.pop3_client import ConnectionConfig, ParsedMail, fetch_new_matching_mails
from app.models import CachedMail, MailCredential, SeenUidl, SharedMailbox
from app.security import decrypt_secret

SHARED_UIDL_OWNER = 0    # 공용 수신 메일함의 SeenUidl 소유자(고정 센티널 — 실제 계정 아님)

APPROVAL_PREFIX = "[FIH][APPROVAL]"
SUBMISSION_PREFIX = "[FIH][SUBMISSION]"

# 제목에서 모델명 추출 — Submission/Approval 짝을 맞추는 키.
#   [FIH][Submission] POINTMOBILE PM95 has been submitted to Google
#   [FIH][Approval] The shipment of POINTMOBILE MF52e has been granted by Google
# 'The shipment of' 는 Approval 에만 있고, 모델명은 벤더명 뒤 마지막 토큰이다.
_MODEL_RE = re.compile(
    r"\[FIH\]\[(?:Submission|Approval)\]\s*(?:The shipment of\s+)?(.*?)\s+has been\s+",
    re.IGNORECASE,
)


def _dedup_key(m: ParsedMail) -> str:
    """팀 전체에서 같은 메일을 하나로 묶는 키.

    Message-ID 헤더가 있으면 그대로 쓰고(수신자와 무관하게 동일), 없으면
    제목+날짜+발신자로 안정적인 대체 키를 만든다. 항상 비어있지 않게 보장한다.
    """
    if m.message_id:
        return m.message_id
    raw = f"{m.subject}\x00{m.received_at}\x00{m.sender_addr}".encode("utf-8", "replace")
    return "fallback:" + hashlib.sha1(raw).hexdigest()


class NoCredentialError(Exception):
    """자격증명이 등록되지 않은 경우."""


def _norm(subject: str) -> str:
    """제목 정규화: 줄바꿈/연속 공백을 한 칸으로(제목에 \\r\\n 이 섞여 오는 경우가 있다)."""
    return re.sub(r"\s+", " ", (subject or "")).strip()


def is_approval_mail(subject: str) -> bool:
    """Approval 트리거: 제목이 [FIH][APPROVAL] 로 시작(대소문자 무시).

    'RE:' / '전달:' 이 붙은 회신·전달본은 시작이 달라 자동으로 제외된다(원본만).
    """
    return _norm(subject).upper().startswith(APPROVAL_PREFIX)


def is_submission_mail(subject: str) -> bool:
    """Submission 트리거: 제목이 [FIH][SUBMISSION] 로 시작(대소문자 무시)."""
    return _norm(subject).upper().startswith(SUBMISSION_PREFIX)


def is_trigger_mail(subject: str) -> bool:
    """작업 트리거 메일(Submission 또는 Approval)인지 판정."""
    return is_submission_mail(subject) or is_approval_mail(subject)


def extract_model(subject: str) -> str:
    """제목에서 모델명(PM95 · MF52E · XG5 · MX450 …)을 뽑는다. 없으면 "".

    벤더명(POINTMOBILE / Janam …) 뒤 마지막 토큰을 모델로 본다. 짝 맞춤 키라 대문자로 통일.
    """
    m = _MODEL_RE.search(_norm(subject))
    if not m:
        return ""
    name = m.group(1).strip()
    return name.split()[-1].upper() if name else ""


# 제목에 이 말이 들어간 메일은 **본문을 받지 않는다**(헤더만 보고 건너뛴다).
# 사내 회람이라 이 앱에서 쓸 일이 없는데 첨부가 커서 동기화 세션을 오래 잡는다 —
# 실측: '주간업무보고' 1통(1.8MB) 을 받는 데 805초가 걸렸고, 그 뒤 서버가 연결을 끊어
# 그때까지 받은 21통이 전부 버려졌다. 환경변수로 늘릴 수 있다(쉼표 구분).
SKIP_SUBJECT_KEYWORDS = tuple(
    k.strip() for k in
    os.getenv("TW_MAIL_SKIP_SUBJECTS", "주간업무보고,주간보고").split(",")
    if k.strip()
)


def _accept_all(subject: str, from_addr: str) -> bool:
    """전체 동기화: 건너뛸 제목만 빼고 모두 받는다.

    공백을 무시하고 맞춘다 — 같은 회람이 '주간업무보고'·'주간 업무 보고' 로 섞여 온다.
    """
    s = re.sub(r"\s+", "", subject or "")
    return not any(re.sub(r"\s+", "", k) in s for k in SKIP_SUBJECT_KEYWORDS)


def sync_mails(user_id: int) -> int:
    """동기화 안 된 **모든** 신규 메일을 받아 **팀 공용 보드**에 저장한다(필터 없음).

    같은 pm_ta@ 배포주소 메일을 팀원 각자 받으므로, 이미 다른 계정이 올려둔
    메일(message_id 동일)은 새로 만들지 않고 건너뛴다. 이번 동기화로 **새로 추가된**
    공용 메일 수를 반환한다.
    UI 스레드를 막지 않도록 run.io_bound 로 호출할 것.
    (첫 동기화는 받은편지함 전체를 받으므로 시간이 걸릴 수 있다.)
    """
    with get_session() as db:
        # 수신은 **공용 수신 계정** 하나로만 한다. 개인 계정(MailCredential)은 발송 전용이라
        # 개인 받은편지함이 팀 공용 보드로 올라오지 않는다.
        box = db.query(SharedMailbox).filter_by(id=1).first()
        if box is None or not box.email_address or not box.encrypted_password:
            raise NoCredentialError(
                "공용 수신 메일 계정이 등록되지 않았습니다. "
                "관리자가 [설정 → 메일 수신 계정(공용)]에서 등록해야 동기화할 수 있습니다."
            )
        cfg = ConnectionConfig(
            email_address=box.email_address,
            password=decrypt_secret(box.encrypted_password),
            server=box.pop_server,
            port=box.pop_port,
            use_ssl=box.use_ssl,
        )
        # UIDL 은 메일함별 고유값 → 공용 메일함 하나이므로 모두가 같은 '확인됨' 집합을 쓴다.
        seen = {
            u for (u,) in db.query(SeenUidl.uidl).filter_by(user_id=SHARED_UIDL_OWNER).all()
        }
        if not seen:
            # 공용 수신으로 바꾸기 전, **같은 주소**를 개인 계정으로 동기화하던 사람의 수신
            # 이력을 승계한다(같은 메일함 = 같은 UIDL). 없으면 첫 동기화가 전체를 다시 받는다.
            prev_ids = [
                uid for (uid,) in db.query(MailCredential.user_id)
                .filter(MailCredential.email_address == box.email_address).all()
            ]
            if prev_ids:
                inherited = {
                    u for (u,) in db.query(SeenUidl.uidl)
                    .filter(SeenUidl.user_id.in_(prev_ids)).all() if u
                }
                # 승계분을 공용 소유자로 **저장**해 둔다. 저장하지 않으면 다음 동기화 때
                # seen 이 다시 비어 보여 예전 메일 전체를 또 받게 된다.
                for u in inherited:
                    db.add(SeenUidl(user_id=SHARED_UIDL_OWNER, uidl=u))
                db.commit()
                seen = inherited

    matched, processed, failed = fetch_new_matching_mails(cfg, seen, _accept_all)
    if failed:
        # 건너뛴 메일은 '받음' 으로 기록하지 않는다 — 다음 주기에 다시 시도한다.
        print(f"[mail] 받지 못한 메일 {len(failed)}건 — 다음 동기화에서 재시도", flush=True)

    if not processed:
        return 0

    added = 0
    with get_session() as db:
        # 팀 공용: 이미 보드에 있는 메일(message_id)은 다시 만들지 않는다.
        existing_keys = {
            k for (k,) in db.query(CachedMail.message_id).all() if k
        }
        # message_id 컬럼은 나중에 추가돼 **예전 행은 빈 값**이라 위 집합으로 못 거른다.
        # 저장 시 (user_id, uidl) 유일 제약이 있으므로 그 조합도 함께 확인해 중복 INSERT 를 막는다.
        existing_uidls = {
            u for (u,) in db.query(CachedMail.uidl).filter_by(user_id=user_id).all() if u
        }
        for m in matched:
            key = _dedup_key(m)
            if key in existing_keys or m.uidl in existing_uidls:
                continue  # 이미 보드에 있는 메일 → 건너뜀
            existing_keys.add(key)
            existing_uidls.add(m.uidl)
            db.add(
                CachedMail(
                    user_id=user_id,   # 생성자(처음 받은 사람) 기록용
                    uidl=m.uidl,
                    message_id=key,
                    sender=m.sender,
                    subject=m.subject,
                    received_at=m.received_at,
                    body_text=m.body_text,
                    body_html=m.body_html,
                    in_reply_to=m.in_reply_to,   # 스레드 연결용(WhiteList 메일 묶기)
                    refs=m.references,
                    to_recipients=getattr(m, "to_recipients", ""),
                    cc_recipients=getattr(m, "cc_recipients", ""),
                    app_marker=getattr(m, "app_marker", ""),
                    attachment_names=getattr(m, "attachment_names", ""),
                    attachment_serials=getattr(m, "attachment_serials", ""),
                    attachment_dir=getattr(m, "attachment_dir", ""),
                )
            )
            added += 1
        # 이번에 공용 메일함에서 평가한 UIDL 을 확인됨으로 기록(재다운로드 방지).
        # 이미 기록된 것은 (user_id, uidl) 유일 제약에 걸리므로 제외한다(재시도 안전).
        already = {
            u for (u,) in db.query(SeenUidl.uidl)
            .filter_by(user_id=SHARED_UIDL_OWNER).all()
        }
        for u in dict.fromkeys(processed):        # 순서 유지 + 배치 내 중복 제거
            if u not in already:
                db.add(SeenUidl(user_id=SHARED_UIDL_OWNER, uidl=u))
                already.add(u)
        db.commit()
    return added


def fetch_raw_by_uidl(uidl: str) -> bytes | None:
    """공용 수신함에서 uidl 로 원문(raw)을 다시 받아온다. 없으면 None.

    유기적 처리(블록 분해·AI 해석)는 첨부메일 본문까지 필요해 원문 전체가 있어야 한다.
    """
    from app.mail import pop3_client as P

    if not uidl or uidl.startswith(("manual:", "fallback:")):
        return None
    with get_session() as db:
        box = db.query(SharedMailbox).filter_by(id=1).first()
        if box is None or not box.email_address or not box.encrypted_password:
            return None
        cfg = ConnectionConfig(
            email_address=box.email_address, password=decrypt_secret(box.encrypted_password),
            server=box.pop_server, port=box.pop_port, use_ssl=box.use_ssl,
        )
    try:
        client = P._connect(cfg)
    except Exception:  # noqa: BLE001
        return None
    try:
        num = {u: n for n, u in P._server_uidls(client)}.get(uidl)
        if not num:
            return None
        return P._retr_full(client, num)
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            client.quit()
        except Exception:  # noqa: BLE001
            pass
