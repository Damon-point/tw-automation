"""WhiteList 메일 선별 — **룰베이스 1차 필터**(Claude 미사용).

동기화는 OS Update 와 같은 공용 수신함 하나(app.mail.service)가 이미 **모든 메일**을
CachedMail 에 저장한다. 그래서 여기서는 새로 받아오지 않고 **저장된 메일을 걸러내기만** 한다.

거르는 기준은 **둘 다** 통과해야 한다(사용자 확정 2026-08-06):
  ① 일련번호가 있는가 — 10자리 영숫자 토큰. **숫자만 10자리** 또는 **숫자+알파벳 조합
     10자리**(알파벳만 10자리는 제외 — 'PROCESSING' 같은 평범한 단어가 걸린다).
     첨부(엑셀/CSV)에서 뽑은 SN 도 인정한다. 등록할 단말이 없으면 등록 요청이 아니다.
  ② 의도 단어가 있는가 — FOTA·화이트리스트·등록·요청·업그레이드 계열.
     SN 만으로는 부족하다. 단말 반출·RMA·주간 업무 보고에도 SN 은 들어있다.

이 둘을 통과한 것만 Claude 판별(mail_triage)로 넘어간다. 실측 회귀: 이미 '요청'으로
판정된 스레드 39개는 ①②에서 한 건도 탈락하지 않고, '요청 아님' 48개 중 17개가
Claude 를 거치기 전에 걸러진다.

여기는 **등급을 매기지 않는다**(2026-08-06 사용자 확정). 등록 요청인지 아닌지는
2차 판별(mail_triage)의 이진 판정 하나로만 정한다. 예전에는 여기서 높음/보통/낮음을
나눠 '낮음'을 판정 대상에서 뺐는데, 그 때문에 진짜 요청 13건이 화면에도 판정에도
오지 못했다(실측: `PM451 OS Upgrade S/N 등록 요청의 건` 이 OS 버전 표기가 없다는
이유로 낮음). 전량 판정 비용이 스레드 87개 기준 0.12달러라 아낄 이유가 없다.

읽기 전용 — 메일을 고치거나 지우지 않는다.
"""
from __future__ import annotations

import re

from app.database import get_session
from app.models import CachedMail

# ── 키워드 ────────────────────────────────────────────────────────────────
# 'SN' 은 흔한 두 글자라 단독으로는 오탐이 많다. 구분기호(S/N·S.N)나
# 'SN:' 처럼 뒤에 값이 오는 형태, 또는 Serial 계열 단어만 인정한다.
_KEYWORD_RE = re.compile(
    r"""(?ix)
    \b s\s*[/.\-]\s*n \b            # S/N · S.N · S-N
  | \b sn \s* [:：=]                # SN: · SN =
  | \b serial (?: \s* (?: number | no\.? | \# ) )?   # Serial / Serial Number / Serial No. / Serial #
  | 시리얼 \s* (?: 넘버 | 번호 )?
  | 일련 \s* 번호
""",
)

# ── 일련번호 후보 토큰 ────────────────────────────────────────────────────
# 앞뒤가 영숫자가 아니어야 한다(긴 해시·문서번호의 일부를 잘라오지 않도록).
_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{10})(?![A-Za-z0-9])")


# 10자리 영숫자 중 숫자가 최소 몇 개여야 일련번호로 볼지.
# 실제 SN 은 '2232710498'(숫자 10) · '24094A0109'(숫자 9) 처럼 숫자가 대부분이다.
# 이 값이 없으면 'SCAN2STAGE'·'DAISY16KIM' 같은 평범한 단어가 그대로 후보로 올라온다.
MIN_DIGITS = 5


# 토큰 안에 모델명이 들어있으면 일련번호가 아니다.
# 실측: 'OSA13PM451'(= OS A13 PM451 이 붙은 것)이 SN 후보로 올라왔다.
_MODEL_IN_TOKEN_RE = re.compile(r"(?:PM|RF|TR|MF|PR)\d{2,3}[A-Za-z]?", re.I)


def is_serial_like(token: str) -> bool:
    """10자리 영숫자이면서 숫자가 MIN_DIGITS 개 이상인가(모델명 섞인 토큰은 제외)."""
    t = (token or "").strip()
    if len(t) != 10 or not t.isalnum() or not t.isascii():
        return False
    if sum(c.isdigit() for c in t) < MIN_DIGITS:
        return False
    return not _MODEL_IN_TOKEN_RE.search(t)


def find_serials(text: str) -> list[str]:
    """본문·제목에서 일련번호 후보를 순서대로(중복 제거) 뽑는다. 대문자로 정규화."""
    out: list[str] = []
    seen: set[str] = set()
    for m in _TOKEN_RE.finditer(text or ""):
        tok = m.group(1)
        if not is_serial_like(tok):
            continue
        up = tok.upper()
        if up not in seen:
            seen.add(up)
            out.append(up)
    return out


def has_keyword(text: str) -> bool:
    return bool(_KEYWORD_RE.search(text or ""))


# ── ② 의도 단어 ───────────────────────────────────────────────────────────
# SN 만으로는 등록 요청이 아니다. 이 계열 단어가 하나도 없으면 Claude 에게 묻지 않는다.
# 넓게 잡는다 — 여기서 거르는 것은 '명백히 아닌 것'뿐이고, 판별은 Claude 가 한다.
_INTENT_RE = re.compile(
    r"""(?ix)
    fota | 화이트\s*리스트 | white\s*list
  | 등록 | register | registration | enroll
  | 요청 | request | 부탁 | please
  | 업그레이드 | 업데이트 | upgrade | update
""",
)


def has_intent(text: str) -> bool:
    """FOTA·등록·요청·업그레이드 계열 단어가 있는가(② 필터)."""
    return bool(_INTENT_RE.search(text or ""))


# ── 발신자 제외를 뒤집는 단어 ─────────────────────────────────────────────
# 처리담당이 보낸 메일이라도 이 단어가 있으면 목록에서 빼지 않는다
# (사용자 확정 2026-08-10). 처리담당이 요청 메일을 **FW 로 전달**해 오는 일이 있어
# 발신자만으로 빼면 진짜 요청을 놓친다.
#
# 실측: [1947] 'FW: [결재 완료] … 터키 SETSIS_OS 무상 업그레이드의 건_TGS (PM86, PM95,
# PM451)' — 첨부 TGS_Upgrade.xlsx 에서 SN 75건이 이미 뽑혀 있었는데, 전달한 사람이
# 처리담당이라는 이유만으로 후보에서 빠졌다.
#
# 앱이 보낸 회신(app_marker)은 이것과 무관하게 계속 제외한다 — 그쪽이 진짜 우리 회신이다.
#
# ⓐ 'OS 업그레이드' 계열은 그것만으로 인정한다.
_STRONG_INTENT_RE = re.compile(
    r"""(?ix)
    os \s* 업그레이드 | os \s* up\s*grade | os \s* 업데이트 | os \s* update
""",
)

# ⓑ '유상·무상' 은 짧아서 통관·견적 메일에도 걸린다. **OS·FOTA·업그레이드와 같은 줄에서
#    40자 안에 붙어 있을 때만** 인정한다(사용자 확정 2026-08-10).
#    걸러야 하는 것: 'RE: DHL 4117127711 … 수입 화물 통관 정보 제출 요청'
#    인정해야 하는 것: '터키 SETSIS_OS 무상 업그레이드의 건' · 'PM95 OS 무상 upgrade 문의'
_NEAR = r"(?: \b (?: os | fota ) \b | 업\s*그레이드 | 업데이트 | \b up\s*grade \b | \b update \b )"
_PAID_NEAR_RE = re.compile(
    rf"""(?ix)
    {_NEAR} [^\n]{{0,40}}? (?: 유상 | 무상 )
  | (?: 유상 | 무상 ) [^\n]{{0,40}}? {_NEAR}
""",
)


def has_strong_intent(text: str) -> bool:
    """발신자 제외를 뒤집을 만큼 뚜렷한 단어가 있는가."""
    t = text or ""
    return bool(_STRONG_INTENT_RE.search(t) or _PAID_NEAR_RE.search(t))


# ── 우리가 보낸 안내 ───────────────────────────────────────────────────────
# 앱(mailer)이 만드는 안내 제목은 전부 이 머리말로 **시작**한다.
#   [White List] 등록 완료 안내 — N대 / 등록일 변경 완료 안내 — N대
#   [White List] Registration completed — N device(s) / Registration date changed
#   [WhiteList] 새 Request 접수 — id
# 본문에 '잠시 후 OS 업데이트를 받으실 수 있습니다' 가 들어 있어 위 단어 규칙에 걸린다.
# 우리 안내이므로 신규 요청이 아니다(사용자 확정 2026-08-10).
#
# 머리말이 **맨 앞**일 때만 본다. 'RE: [White List] …' 는 상대가 보낸 회신이라 남긴다 —
# 우리 안내에 답장하면서 새 요청을 붙여 오는 경우가 있다.
_OWN_NOTICE_RE = re.compile(r"^\s*\[\s*white\s*list\s*\]", re.I)


def is_own_notice(subject: str) -> bool:
    """앱이 보낸 WhiteList 안내 메일인가(제목 머리말로 판단)."""
    return bool(_OWN_NOTICE_RE.match(subject or ""))


# 자동발송 — Redmine 이슈 알림·뉴스레터의 랜덤 ID 가 일련번호처럼 걸린다.
# 지우지 않고 '숨김' 으로만 둔다(Redmine 이슈에 진짜 SN 이 들어있는 경우가 실제로 있었다).
_BOT_SENDER_RE = re.compile(
    r"redmine|newsletter|no-?reply|noreply|atlassian|jira|mailer-daemon|notification", re.I
)

# 제외 발신자 — **등록을 처리한 쪽이 보낸 회신**이라 신규 요청이 아니다.
# 목록은 [설정 → 사내 인원]의 '처리담당' 체크에서 온다(2026-08-06, 하드코딩 폐지).
# ※ 발신자로 구분하는 건 한시적이다. 앱이 직접 회신하면 app_marker 로 판별한다.
def excluded_senders() -> set[str]:
    from app import staff

    return staff.handler_addresses()


def is_bot_sender(sender: str) -> bool:
    """사람이 아니라 시스템이 보낸 메일인가(알림·뉴스레터)."""
    return bool(_BOT_SENDER_RE.search(sender or ""))


def _addresses(sender: str) -> set[str]:
    """'이름 <a@b.com>' 형태에서 메일 주소만 뽑는다(소문자)."""
    s = (sender or "").lower()
    return set(re.findall(r"[\w.+-]+@[\w.-]+\.\w+", s))


def is_excluded_sender(sender: str) -> bool:
    """등록 처리 회신을 보내는 계정인가 → 신규 요청이 아니므로 목록에서 뺀다."""
    return bool(_addresses(sender) & excluded_senders())


def classify(subject: str, body: str, sender: str = "") -> dict:
    """메일 하나를 걸러낸다. ① SN 있음 **그리고** ② 의도 단어 있음 → 후보.

    반환: {matched, by_keyword, has_intent, serials, reason, is_bot, is_excluded}.
    '등록 요청인가'는 여기서 판단하지 않는다 — mail_triage 의 이진 판정이 정한다.
    """
    text = f"{subject or ''}\n{body or ''}"
    kw = has_keyword(text)
    serials = find_serials(text)
    intent = has_intent(text)
    strong = has_strong_intent(text)
    by_sender = is_excluded_sender(sender)
    own = is_own_notice(subject)
    reasons = []
    if serials:
        reasons.append(f"일련번호 {len(serials)}건")
    if kw:
        reasons.append("SN 키워드")
    if own:
        reasons.append("우리가 보낸 안내")
    elif by_sender and strong:
        reasons.append("처리담당 발신이지만 OS 업그레이드·유무상 단어 있음")
    return {
        "matched": bool(serials) and intent,
        "by_keyword": kw,
        "has_intent": intent,
        "strong_intent": strong,
        "serials": serials,
        "reason": " · ".join(reasons) or "해당 없음",
        "is_own_notice": own,
        "is_bot": is_bot_sender(sender),
        # 처리담당이 보냈어도 뚜렷한 단어가 있으면 빼지 않는다(사용자 확정 2026-08-10).
        # 다만 우리가 보낸 안내는 뚜렷한 단어가 있어도 뺀다.
        "is_excluded": own or (by_sender and not strong),
    }


# classify 는 본문 전체를 정규식으로 훑어 전량(5M자)이면 1.3초쯤 걸린다.
# 저장된 메일은 바뀌지 않으므로 **메일 id 별 판정 결과를 캐시**한다(프로세스 메모리).
_CLASSIFY_CACHE: dict[int, dict] = {}


def _classify_cached(m) -> dict:
    hit = _CLASSIFY_CACHE.get(m.id)
    if hit is None:
        hit = classify(m.subject, m.body_text, m.sender)
        # 첨부(엑셀/CSV)에서 뽑은 SN 을 본문 SN 과 합친다 — 대량 요청은 SN 이 첨부에만 있다.
        att = [s.strip().upper() for s in
               (getattr(m, "attachment_serials", "") or "").splitlines() if s.strip()]
        if att:
            merged = list(dict.fromkeys(list(hit.get("serials") or []) + att))
            base = hit.get("reason", "")
            if base == "해당 없음":       # 본문엔 아무 신호도 없고 첨부에만 SN 이 있는 경우
                base = ""
            reason = (base + f" · 첨부 SN {len(att)}건").strip(" ·")
            # 첨부에 SN 이 있어도 ② 의도 단어는 그대로 요구한다(본문 기준).
            hit = {**hit, "serials": merged, "reason": reason,
                   "matched": bool(hit.get("has_intent"))}
        # 앱이 보낸 메일(X-TW-Automation 표식)은 우리 회신이므로 신규 요청에서 제외한다.
        if getattr(m, "app_marker", ""):
            hit = {**hit, "is_excluded": True,
                   "reason": (hit.get("reason", "") + " · 앱 발송(회신)").strip(" ·")}
        _CLASSIFY_CACHE[m.id] = hit
    return hit


def list_candidates(limit: int = 200, *, include_bot: bool = False) -> list[dict]:
    """저장된 메일 중 WhiteList 후보(최신 순).

    SN 키워드나 일련번호 후보가 하나라도 있으면 전부 후보다 — 여기서 더 좁히지 않고
    2차 판별(mail_triage)로 넘긴다(사용자 확정 2026-08-06).

    include_bot: 자동발송(Redmine 알림·뉴스레터)도 포함. 기본은 뺀다.
    """
    with get_session() as db:
        rows = db.query(CachedMail).order_by(CachedMail.id.desc()).all()

    out: list[dict] = []
    for m in rows:
        res = _classify_cached(m)
        if not res["matched"]:
            continue
        if res["is_excluded"]:
            continue                      # 등록 처리 회신 — 신규 요청 아님
        if res["is_bot"] and not include_bot:
            continue
        out.append({
            "id": m.id,
            "subject": m.subject or "(제목 없음)",
            "sender": m.sender or "",
            "received_at": m.received_at or "",
            "body_text": m.body_text or "",
            "message_id": m.message_id or "",
            "in_reply_to": getattr(m, "in_reply_to", "") or "",
            "refs": getattr(m, "refs", "") or "",
            "to_recipients": getattr(m, "to_recipients", "") or "",
            "cc_recipients": getattr(m, "cc_recipients", "") or "",
            "attachment_names": getattr(m, "attachment_names", "") or "",
            "attachment_dir": getattr(m, "attachment_dir", "") or "",
            **res,
        })
    out.sort(key=lambda r: -r["id"])
    return out[:max(limit, 1)]


def counts() -> dict:
    """후보·자동발송·회신 제외 건수."""
    with get_session() as db:
        rows = db.query(CachedMail).all()
    c = {"candidate": 0, "bot": 0, "excluded": 0}
    for m in rows:
        res = _classify_cached(m)
        if not res["matched"]:
            continue
        if res["is_excluded"]:
            c["excluded"] += 1
        elif res["is_bot"]:
            c["bot"] += 1
        else:
            c["candidate"] += 1
    return c
