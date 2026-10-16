"""앱이 보내는 모든 메일의 발송구.

**로그인 계정과 보이는 발신자를 나눈다.**
  · 로그인(SMTP AUTH)  : 공용 계정 하나 — TW_MAIL_ACCOUNT. 사람마다 앱 비밀번호를
    등록하지 않아도 앱이 메일을 보낼 수 있다. 지정이 없으면 예전처럼 본인 계정.
  · 보이는 발신자(From): **언제나 시스템 주소 하나**(TW_MAIL_FROM). 사람 주소는 절대
    From 에 넣지 않는다.
  · 답장(Reply-To)     : 사람이 한 일이면 그 사람, 시스템이 알리는 것이면 시스템 관리자.
  · 반송(Return-Path)  : 항상 로그인 계정. 시스템 주소에 사서함이 없어도 배달 실패
    통지가 사라지지 않는다.
  · 항상 참조(Cc)      : TW_MAIL_ALWAYS_CC. 앱이 내보내는 모든 메일을 그 주소가 함께
    받는다(보낸 사람이거나 받는 사람이면 넣지 않는다).
사내 메일 서버가 From 을 검사하지 않아 가능한 구성이다(실측 2026-08-25). O365 였다면
인증 계정과 다른 From 은 5.7.60 으로 거부된다.

**왜 From 을 고정하는가** — 한동안 사람이 누른 메일은 그 사람 주소로 내보냈다. 받는
쪽은 그 사람이 직접 쓴 메일로 읽었고, 실제로는 앱이 보낸 것이라 혼선이 컸다
(2026-08-25 사용자 지적). 누가 처리했는지는 Reply-To 와 본문 끝 서명이 전한다.

시스템 주소는 **회사 도메인 안**이어야 한다. 다른 도메인(twautomation.com 등)을 쓰면
사외 수신 서버의 DMARC 정렬에 걸려 스팸 처리되거나 거부된다 — 그 도메인은 우리 것도
아니다(2026-08-25 DNS 확인: Afternic 매물 · null MX · SPF -all).

주소만 등록하고 앱 비밀번호는 비워 둘 수 있다 — 로그인은 공용 계정이 한다. 사람 주소는
Reply-To 와 수신자 목록(admin_emails 등)에 쓰인다.
자격증명이 하나도 없으면 (False, 사유) 반환 → 호출부에서 안내.

TW_MAIL_FROM 을 두지 않으면 동작은 예전 그대로다(본인 명의 발송).
"""
from __future__ import annotations
from app import shadow as _shadow

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from app.database import get_session
from app.models import MailCredential, User
from app.security import decrypt_secret


@dataclass
class SendIdentity:
    email: str
    password: str
    host: str
    port: int
    #: 이 계정의 메일 서명(HTML). 보내는 모든 메일 끝에 붙는다.
    signature: str = ""


def _smtp_host_for(pop_server: str) -> str:
    # 명시 override(USER_SMTP_HOST)가 있으면 최우선.
    override = os.getenv("USER_SMTP_HOST", "").strip()
    if override:
        return override
    p = (pop_server or "").strip()
    pl = p.lower()
    if "office365" in pl or "outlook" in pl:
        return "smtp.office365.com"
    for pre in ("pop3.", "pop.", "imap."):
        if pl.startswith(pre):
            return "smtp." + p[len(pre):]
    # 그 외(자체 메일서버 등): POP 과 동일 호스트를 SMTP 로 사용
    # (예: mail.pointmobile.com POP3/SMTP 겸용). 빈 값이면 O365 로 폴백.
    return p or "smtp.office365.com"


def get_send_identity(user_id: int) -> "SendIdentity | None":
    """사용자의 메일 자격증명 → SMTP 발송 신원. 미등록/복호화 실패 시 None."""
    with get_session() as db:
        c = db.query(MailCredential).filter_by(user_id=user_id).first()
        if c is None or not c.email_address or not c.encrypted_password:
            return None
        try:
            pw = decrypt_secret(c.encrypted_password)
        except Exception:  # noqa: BLE001
            return None
        if not pw:
            return None
        return SendIdentity(
            email=c.email_address.strip(), password=pw,
            host=_smtp_host_for(c.pop_server),
            port=int(os.getenv("USER_SMTP_PORT", "587") or "587"),
            # 등록해 둔 서명이 있으면 그것, 없으면 이름 한 줄.
            signature=((getattr(c, "signature_html", "") or "").strip()
                       or name_signature(c.email_address)),
        )


def system_from() -> tuple[str, str, str]:
    """시스템 명의 발신 설정 — (보이는 주소, 표시 이름, 답장 받을 주소).

    보이는 주소가 비어 있으면 예전 그대로 **보내는 사람 = 로그인 사용자**로 나간다.
    설정해 두면 시스템 안내 메일의 From 만 그 주소로 보이고, SMTP 로그인·반송 주소
    (Return-Path)는 여전히 실제 계정이다. 사내 메일 서버가 From 을 검사하지 않아
    가능한 방식이다 (O365 였다면 5.7.60 으로 거부된다 — 실측 2026-08-25).

    답장 받을 주소(Reply-To)를 비워 두면 시스템 관리자에게 간다. 시스템 주소는 사서함이
    없을 수도 있어서(2026-08-25 기준 tw.auto 는 아직 없다), 답장이 갈 곳을 사람 주소로
    두지 않으면 회신이 반송된다.
    """
    return (os.getenv("TW_MAIL_FROM", "").strip(),
            os.getenv("TW_MAIL_FROM_NAME", "").strip() or "TW Automation",
            os.getenv("TW_MAIL_REPLY_TO", "").strip())


def always_cc() -> list[str]:
    """앱이 보내는 **모든** 메일에 참조로 넣을 주소. TW_MAIL_ALWAYS_CC (쉼표 구분).

    보낸 사람이거나 받는 사람이면 넣지 않는다 — 같은 메일이 두 통 오는 일을 막는다.
    설정이 없으면 빈 목록이라 예전과 같다.
    """
    raw = os.getenv("TW_MAIL_ALWAYS_CC", "")
    out: list[str] = []
    for part in raw.replace(";", ",").split(","):
        a = part.strip()
        if a and "@" in a and a.lower() not in [x.lower() for x in out]:
            out.append(a)
    return out


def relay_identity(fallback_user_id: int | None = None) -> "SendIdentity | None":
    """**모든 메일이 실제로 로그인할 계정**. TW_MAIL_ACCOUNT 로 지정한다.

    계정 이름('damon.park')이든 메일 주소든 받는다. 이 값을 두면 사람마다 앱 비밀번호를
    등록하지 않아도 앱이 메일을 보낼 수 있다 — 보이는 발신자(From)는 send_as_user 가
    따로 정한다(언제나 시스템 주소 하나로 고정).

    지정이 없으면 fallback_user_id(=보내는 사람 본인) 계정을 쓴다 — 예전 그대로다.
    """
    key = os.getenv("TW_MAIL_ACCOUNT", "").strip()
    if not key:
        return get_send_identity(fallback_user_id) if fallback_user_id else None
    with get_session() as db:
        uid = (
            db.query(MailCredential.user_id)
            .filter(MailCredential.email_address == key).scalar()
            or db.query(User.id).filter(User.username == key).scalar()
        )
    return get_send_identity(uid) if uid else None


def _email_from_username(username: str) -> str:
    """로그인 아이디에서 메일 주소를 얻는다. 없으면 ''.

    ① 아이디 자체가 메일 주소인 계정이 많다(brandon.ha@pointmobile.com).
    ② 아니면 **사내 인원 명부**에서 아이디를 앞자리로 가진 주소를 찾는다
       (damon.park → damon.park@pointmobile.com). 규칙으로 지어내지 않고 등록된
       주소에서만 찾는다 — 없는 주소를 만들면 회신이 반송된다.
    """
    name = (username or "").strip()
    if not name:
        return ""
    if "@" in name:
        return name
    from app.staff import list_staff

    low = name.lower()
    for s in list_staff():
        addr = str(s.get("email") or "").strip()
        if addr and addr.split("@", 1)[0].lower() == low:
            return addr
    return ""


def sender_profile(user_id: int) -> tuple[str, str, str]:
    """그 사람 명의로 보낼 때 쓸 (주소, 표시 이름, 서명). 주소를 못 찾으면 ('', 이름, '').

    앱 비밀번호가 없어도 그 사람 명의로 나갈 수 있다 — 로그인은 공용 계정
    (relay_identity)이 하기 때문이다. 주소는 등록해 둔 것 → 아이디 순으로 찾는다.
    """
    with get_session() as db:
        c = db.query(MailCredential).filter_by(user_id=user_id).first()
        u = db.query(User).filter_by(id=user_id).first()
        addr = (c.email_address or "").strip() if c else ""
        sig = ((getattr(c, "signature_html", "") or "").strip() if c else "")
        uname = u.username if u else ""
    addr = addr or _email_from_username(uname)
    name = display_name_for(addr) or uname
    return addr, name, (sig or (name_signature(addr) if addr else ""))


def user_email(user_id: int) -> str:
    """사용자의 메일 주소 — 등록해 둔 것이 없으면 아이디에서 찾는다. 없으면 ''."""
    with get_session() as db:
        c = db.query(MailCredential).filter_by(user_id=user_id).first()
        if c and c.email_address:
            return c.email_address.strip()
        u = db.query(User).filter_by(id=user_id).first()
        uname = u.username if u else ""
    return _email_from_username(uname)


def _role_emails(*roles: str, exclude_username: str = "") -> list[str]:
    """해당 역할 사용자들의 메일 주소 — **등록해 둔 것이 없으면 아이디에서 찾는다**.

    예전에는 자격증명(MailCredential)이 있는 사람만 골라서, 아이디가 메일 주소인
    계정조차 수신자에서 빠졌다. 그 바람에 RN 1차 승인 요청이 '수신자 0명'으로 아예
    나가지 못했다. 중복 주소는 한 번만 남긴다.
    """
    with get_session() as db:
        q = db.query(User.id, User.username).filter(User.role.in_(roles))
        if exclude_username:
            q = q.filter(User.username != exclude_username)
        rows = q.order_by(User.id).all()
    out: list[str] = []
    seen: set[str] = set()
    for uid, uname in rows:
        addr = user_email(uid) or _email_from_username(uname)
        low = addr.lower()
        if addr and low not in seen:
            seen.add(low)
            out.append(addr)
    return out


def system_sender_user_id() -> int | None:
    """시스템 안내 메일을 어느 계정 앞으로 돌릴지. 없으면 None.

    보이는 발신자는 어차피 시스템 주소라 이 값은 로그인·기록용이다. 공용 발송 계정
    (TW_MAIL_ACCOUNT)을 먼저 찾고, 없으면 자격증명이 있는 관리자를 쓴다.
    예전에는 부르는 곳마다 '자격증명 있는 사용자'를 따로 뒤졌는데, 개인 등록이 필요
    없어진 지금은 그 조회들이 **보낼 수 있는데도 못 보낸다고** 막아설 뿐이다.
    """
    key = os.getenv("TW_MAIL_ACCOUNT", "").strip()
    with get_session() as db:
        if key:
            uid = (db.query(MailCredential.user_id)
                   .filter(MailCredential.email_address == key).scalar()
                   or db.query(User.id).filter(User.username == key).scalar())
            if uid:
                return uid
        row = (db.query(User.id)
               .join(MailCredential, MailCredential.user_id == User.id)
               .filter(MailCredential.encrypted_password != "")
               .order_by(User.role != "admin", User.id).first())
    return row[0] if row else None


def admin_emails() -> list[str]:
    """시스템 관리자(role=admin) 이메일 목록."""
    return _role_emails("admin")


def system_admin_emails() -> list[str]:
    """시스템 관리자 이메일 — **공용 'admin' 계정은 뺀다**.

    admin 은 여러 사람이 함께 쓰는 계정이라, 개인이 처리해야 할 안내가 묻힌다.
    비밀번호 초기화 요청처럼 사람이 손을 대야 하는 건은 실제 담당자에게만 보낸다
    (사용자 확정 2026-08-19).
    """
    return _role_emails("admin", exclude_username="admin")




def app_url() -> str:
    """안내 메일에 싣는 서비스 주소. 배포처가 바뀌면 TW_APP_URL 로 덮는다."""
    import os

    return os.getenv("TW_APP_URL", "http://192.168.121.35:8080").rstrip("/")


def final_reviewer_emails() -> list[str]:
    """최종 승인자(role=final_reviewer) 이메일 목록 — RN for Sales·Newsletter 결재 수신자.

    아직 최종 승인자를 만들지 않았으면 **admin 으로 폴백**한다. 수신자가 0명이면
    결재 요청 자체가 발송 실패로 막히므로, 역할을 나눈 직후에도 업무가 멈추지 않게 한다.
    """
    return _role_emails("final_reviewer") or _role_emails("admin")


def mid_reviewer_emails() -> list[str]:
    """RN 승인자(role=mid_reviewer) 이메일 목록 — RN 1차 승인 요청 수신자."""
    return _role_emails("mid_reviewer")


#: Outlook 이 쓰는 영문 요일·달 이름. 서버 로캘이 한국어면 strftime 이 '수요일' 을
#: 내놓아 사내 메일과 모양이 달라진다 — 그래서 직접 박아 둔다.
_WDAY_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday")
_MON_EN = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def _outlook_datetime(raw: str) -> str:
    """메일 날짜 → Outlook 인용부의 'Sent:' 표기.

    실측(사내 회신 60통): 'Wednesday, August 19, 2026 11:33 AM' — 요일·달은 영문
    전체 이름, 일·시는 0 을 채우지 않는다. 못 읽으면 원문을 그대로 둔다.
    """
    from email.utils import parsedate_to_datetime

    s = (raw or "").strip()
    if not s:
        return ""
    try:
        dt = parsedate_to_datetime(s)
    except Exception:  # noqa: BLE001
        return s
    if dt is None:
        return s
    h = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return (f"{_WDAY_EN[dt.weekday()]}, {_MON_EN[dt.month - 1]} {dt.day}, "
            f"{dt.year} {h}:{dt.minute:02d} {ampm}")


def _outlook_addrs(raw: str) -> str:
    """수신자 헤더 → Outlook 인용부 표기(세미콜론 구분).

    이름이 있으면 'Name <addr>', 없으면 주소만 쓴다. 저장된 값은 쉼표 구분에 줄바꿈이
    섞여 있어 그대로 실으면 인용부가 깨진다.
    """
    from email.utils import getaddresses

    out = []
    for name, addr in getaddresses([(raw or "").replace("\r\n", " ").replace("\n", " ")]):
        # 표시 이름의 작은따옴표는 그대로 둔다 — 사내 메일이 "'PM_SW' <...>" 로
        # 보이는 건 Outlook 이 아니라 주소록에 그렇게 들어 있기 때문이다.
        name, addr = (name or "").strip(), (addr or "").strip()
        if not addr:
            continue
        out.append(f"{name} <{addr}>" if name else addr)
    return "; ".join(out)


#: 사내 메일의 본문 서식 — Outlook 이 만든 회신 메일의 <style> 블록을 그대로 옮겼다.
#: 맑은 고딕 10pt 가 사내 표준 본문 서체다(사용자 확정 2026-08-19).
_MAIL_CSS = "\n".join([
    "p.MsoNormal, li.MsoNormal, div.MsoNormal",
    "   {margin:0cm; text-align:justify; text-justify:inter-ideograph;",
    "    text-autospace:none; word-break:break-hangul;",
    '    font-size:10.0pt; font-family:"맑은 고딕";}',
    "body, div.WordSection1, table, td, p, li, span",
    '   {font-size:10.0pt; font-family:"맑은 고딕";}'
    ,
    "a:link, span.MsoHyperlink",
    "   {color:#467886; text-decoration:underline;}",
])

#: 회신 본문과 인용부를 가르는 줄 — Outlook 이 쓰는 값 그대로.
_QUOTE_RULE = ("border:none;border-top:solid #E1E1E1 1.0pt;"
               "padding:3.0pt 0cm 0cm 0cm")


def _esc(s: str) -> str:
    from html import escape

    return escape(s or "", quote=False)


def _text_to_html(text: str) -> str:
    """평문 → <p class=MsoNormal> 줄들. 빈 줄은 Outlook 처럼 &nbsp; 한 줄로 둔다."""
    out = []
    for line in (text or "").split("\n"):
        s = line.rstrip()
        if not s.strip():
            out.append("<p class=MsoNormal><o:p>&nbsp;</o:p></p>")
            continue
        # 앞 공백은 화면에서 살려 준다(들여쓴 주소 줄 등).
        lead = len(s) - len(s.lstrip())
        out.append("<p class=MsoNormal>"
                   + "&nbsp;" * lead + _esc(s.strip())
                   + "</p>")
    return "\n".join(out)


def _inner_html(doc: str) -> str:
    """원본 HTML 에서 <body> 안쪽만 꺼낸다 — 통째로 넣으면 문서가 겹쳐 깨진다."""
    import re as _re

    s = doc or ""
    m = _re.search(r"<body[^>]*>(.*)</body>", s, _re.S | _re.I)
    if m:
        s = m.group(1)
    # 원본의 <style> 은 우리 서식을 덮어쓸 수 있어 걷어낸다.
    return _re.sub(r"<style[^>]*>.*?</style>", "", s, flags=_re.S | _re.I)


def quote_html(*, sender: str = "", sent: str = "", to: str = "", cc: str = "",
               subject: str = "", body_text: str = "", body_html: str = "") -> str:
    """원본 인용부를 **Outlook 과 같은 HTML** 로 만든다.

    실측한 구조(사내 회신 메일):
      <div style='border-top:solid #E1E1E1 1.0pt; …'>   ← 본문과 가르는 줄
        <p class=MsoNormal><b>From:</b> …<br><b>Sent:</b> …<br>…</p>
      </div>
      (그 아래 원본 본문)
    """
    rows = [("From", _outlook_addrs(sender) or (sender or "").strip()),
            ("Sent", _outlook_datetime(sent)),
            ("To", _outlook_addrs(to)),
            ("Cc", _outlook_addrs(cc)),
            ("Subject", (subject or "").strip())]
    head = "<br>".join("<b>" + k + ":</b> " + _esc(v) for k, v in rows if v)
    inner = (_inner_html(body_html) if (body_html or "").strip()
             else _text_to_html(body_text))
    if not head and not inner:
        return ""
    return ("<div><div style='" + _QUOTE_RULE + "'>"
            "<p class=MsoNormal>" + head + "<o:p></o:p></p></div>"
            + inner + "</div>")


def build_html_mail(body: str, *, quote: str = "", signature: str = "") -> str:
    """평문 본문 + (서명) + (인용 HTML) → 사내 서식의 HTML 메일 한 통."""
    parts = [_text_to_html(body)]
    if (signature or "").strip():
        parts += ["<p class=MsoNormal><o:p>&nbsp;</o:p></p>",
                  signature if "<" in signature else _text_to_html(signature)]
    if (quote or "").strip():
        parts += ["<p class=MsoNormal><o:p>&nbsp;</o:p></p>", quote]
    return ('<html><head><meta http-equiv="Content-Type" '
            'content="text/html; charset=utf-8"><style><!--\n'
            + _MAIL_CSS
            + '\n--></style></head><body lang=KO><div class=WordSection1>\n'
            + "\n".join(parts)
            + "\n</div></body></html>")


def quote_original(*, sender: str = "", sent: str = "", to: str = "",
                   cc: str = "", subject: str = "", body: str = "") -> str:
    """원본 메일을 회신 본문 아래에 붙일 **인용 블록**으로 만든다.

    사내에서 실제로 오가는 전체회신 메일 60통을 뜯어 그 모양에 맞췄다(2026-08-19):
      · '-----Original Message-----' 줄은 **쓰지 않는다** (58/60 이 그렇다).
        빈 줄 하나 뒤에 곧바로 'From:' 으로 시작한다.
      · 헤더 라벨은 영문 From/Sent/To/Cc/Subject (211/212).
      · Sent 는 'Wednesday, August 19, 2026 11:33 AM' 형식.
      · To/Cc 는 세미콜론 구분. Cc 가 없으면 줄째 뺀다.
    원본 본문에 이미 이전 인용이 들어 있으면 그대로 딸려와 체인이 이어진다
    (실측: 한 메일에 인용 블록이 최대 30개까지 쌓여 있다).
    """
    rows = [("From", _outlook_addrs(sender) or (sender or "").strip()),
            ("Sent", _outlook_datetime(sent)),
            ("To", _outlook_addrs(to)),
            ("Cc", _outlook_addrs(cc)),
            ("Subject", (subject or "").strip())]
    lines = [f"{k}: {v}" for k, v in rows if v]
    text = (body or "").strip()
    if not lines and not text:
        return ""
    out = ["", *lines]
    if text:
        out += ["", text]
    return "\n".join(out)


def quote_of_mail(mail) -> str:
    """CachedMail(또는 같은 필드를 가진 dict) → 인용 블록."""
    def g(k: str) -> str:
        v = mail.get(k) if isinstance(mail, dict) else getattr(mail, k, "")
        return str(v or "")
    return quote_original(
        sender=g("sender"), sent=g("received_at"), to=g("to_recipients"),
        cc=g("cc_recipients"), subject=g("subject"), body=g("body_text"))

def html_to_text(doc: str) -> str:
    """HTML 조각 → 평문. 평문판에도 서명이 남아야 해서 쓴다(정교할 필요는 없다)."""
    import re as _re
    from html import unescape

    nl = chr(10)
    s = _re.sub(r"<(br|/p|/div|/tr)[^>]*>", nl, doc or "", flags=_re.I)
    s = _re.sub(r"<[^>]+>", "", s)
    s = unescape(s).replace(chr(160), " ")
    return nl.join(x.rstrip() for x in s.splitlines()).strip()


def quote_html_of_mail(mail) -> str:
    """CachedMail(또는 같은 필드를 가진 dict) → HTML 인용부."""
    def g(k: str) -> str:
        v = mail.get(k) if isinstance(mail, dict) else getattr(mail, k, "")
        return str(v or "")
    return quote_html(
        sender=g("sender"), sent=g("received_at"), to=g("to_recipients"),
        cc=g("cc_recipients"), subject=g("subject"),
        body_text=g("body_text"), body_html=g("body_html"))


def display_name_for(email: str) -> str:
    """보내는 사람 표시 이름. 사내 인원 명부에 있으면 그 이름, 없으면 주소에서 만든다.

    damon.park@pointmobile.com -> Damon Park
    """
    addr = (email or "").strip()
    if not addr:
        return ""
    try:
        from app.models import Staff

        with get_session() as db:
            row = (db.query(Staff)
                   .filter(Staff.email.ilike(addr))
                   .first())
        if row and (row.name or "").strip():
            return row.name.strip()
    except Exception:  # noqa: BLE001  (명부가 없어도 서명은 나가야 한다)
        pass
    local = addr.split("@", 1)[0]
    return " ".join(w.capitalize() for w in local.replace("_", ".").split(".") if w)


def name_signature(email: str) -> str:
    """서명 — 이름 한 줄만 파란 글자로(사용자 확정 2026-08-19).

    사람마다 다른 Outlook 서명을 그대로 가져올 방법이 없고(각자 PC 에 있다),
    서명 자체가 중요한 요소는 아니라 이름만 남긴다. 색은 사내 서명이 쓰는 남색.
    """
    name = display_name_for(email)
    if not name:
        return ""
    return ("<p class=MsoNormal><b><span style='color:#376092'>"
            + name + "</span></b></p>")


def reply_context(mail_id, *, with_cc: bool = False) -> dict:
    """원본 메일 id → **회신에 필요한 것 한 벌**.

    반환한 dict 를 send_as_user 에 그대로 펼쳐 넘기면 스레드 연결(In-Reply-To·
    References)·인용(평문/HTML)이 한꺼번에 맞는다. 호출부마다 헤더를 따로 챙기다
    빠뜨리는 일을 막으려고 한 곳에 모았다(실측 2026-08-19: WhiteList 회신 세 곳이
    모두 헤더를 안 넘겨 스레드에 붙지 않고 있었다).

    with_cc=True 면 원본의 To/Cc 를 참조인으로 살린다(전체 회신). 외부 수신자가
    늘어나므로 **부르는 쪽에서 명시할 때만** 켠다.
    """
    from app.models import CachedMail

    if not mail_id:
        return {}
    with get_session() as db:
        m = db.query(CachedMail).filter(CachedMail.id == int(mail_id)).first()
        if m is None:
            return {}
        row = {k: (getattr(m, k, "") or "")
               for k in ("sender", "subject", "received_at", "body_text",
                         "body_html", "to_recipients", "cc_recipients",
                         "message_id", "refs")}
    out = {
        "in_reply_to": row["message_id"],
        "references": row["refs"] or row["message_id"],
        "quote": quote_of_mail(row),
        "quote_html": quote_html_of_mail(row),
    }
    if with_cc:
        out["cc"] = ", ".join(x for x in (row["to_recipients"],
                                          row["cc_recipients"]) if x)
    return out


def send_as_user(
    user_id: int, to: "list[str] | str", subject: str, body: str,
    attachments: "list[tuple[str, bytes]] | None" = None,
    *, in_reply_to: str = "", references: str = "", cc: "list[str] | str | None" = None,
    app_marker: str = "1", quote: str = "", quote_html: str = "",
    signature: str = "", as_system: bool = False, html: str = "",
) -> tuple[bool, str]:
    """메일 발송. 보내는 사람 표시는 as_system 이 가른다.

    as_system=False(기본) — **사람이 한 일**이라 그 사람 명의로 나간다(From = 본인 주소).
    as_system=True        — **시스템이 알리는 것**이라 시스템 명의로 나간다(TW_MAIL_FROM).
    SMTP 로그인은 둘 다 공용 계정(TW_MAIL_ACCOUNT) 하나로 한다 — 사람마다 앱 비밀번호를
    등록하지 않아도 되게. 설정이 없으면 예전처럼 본인 계정으로 로그인한다.

    in_reply_to: 원본 메일의 Message-ID 를 주면 In-Reply-To/References 헤더를 붙여
    **원본 메일 스레드에 회신**으로 붙는다(받은 편지함 필터가 원본 기준일 때 유용).
    cc: 참조인. 원본의 To/Cc 문자열을 그대로 넘겨도 된다 — 발신자 본인과 수신자(To)와
        중복되는 주소는 자동으로 걸러 준다(전체 회신 시 참조인 보존용).
    quote: 본문 아래에 붙일 원본 인용 블록(quote_original·quote_of_mail 로 만든다).
    반환 (성공, 오류사유). 자격증명 미등록이면 (False, 안내)."""
    if _shadow.blocked("mail", "send", user_id=user_id, to=to, cc=cc, subject=subject, body=body,
                       attachments=[n for n, _b in (attachments or [])], as_system=as_system):
        return True, ""
    from email.utils import formataddr, formatdate, getaddresses, make_msgid

    ident = relay_identity(user_id)
    if ident is None:
        return False, "발송 계정 미등록 — [계정 관리]에서 본인 이메일 자격증명을 등록하세요."
    tos = [to] if isinstance(to, str) else list(to)
    tos = [t.strip() for t in tos if t and t.strip()]
    if not tos:
        return False, "수신 주소가 비어 있습니다."

    # ── 보이는 발신자 ────────────────────────────────────────────────────────
    # **사람 주소는 From 에 넣지 않는다.** 앱이 보낸 메일을 그 사람이 직접 쓴 것으로
    # 읽어 혼선이 컸다(2026-08-25 사용자 지적). 표시 이름도 사람 이름을 쓰지 않는다 —
    # 이름만 봐도 같은 오해가 생긴다. 누가 처리했는지는 Reply-To 와 본문 끝 서명이 전한다.
    _sys_from, _sys_name, _sys_reply = system_from()
    _pf_addr, _pf_name, _pf_sig = sender_profile(user_id)
    _fallback_reply = _sys_reply or (system_admin_emails() or [ident.email])[0]
    if _sys_from:
        show_from, show_name = _sys_from, _sys_name
        if as_system:
            # 시스템이 알리는 메일 — 답장은 시스템 관리자가 받는다.
            reply_to = _fallback_reply
            _pf_sig = ""                   # 개인 서명을 붙이지 않는다
        else:
            # 사람이 누른 메일 — 답장은 **그 사람**에게 간다. 주소를 못 찾으면 관리자.
            reply_to = _pf_addr or _fallback_reply
    else:
        # 시스템 주소를 정하지 않았으면 예전 그대로 본인 명의로 나간다.
        show_from = _pf_addr or ident.email
        show_name = _pf_name
        reply_to = ""

    # 참조인 — 원본 수신자에서 **보이는 발신자**·수신자(To)와 겹치는 주소는 뺀다.
    # 로그인 계정(ident.email)은 여기서 빼지 않는다. 공용 계정으로 로그인해 남의 명의로
    # 보내게 된 뒤로는 그 주소가 '보낸 사람'이 아니라서, 빼 버리면 참조로 받아야 할
    # 사람이 조용히 사라진다(항상 참조 설정이 그 경우다).
    seen = {t.lower() for t in tos} | {show_from.lower()}
    cc_list: list[str] = []
    if cc:
        cands = cc if isinstance(cc, (list, tuple)) else [cc]
        raw = ", ".join(str(c) for c in cands if c)
        for _n, a in getaddresses([raw]):
            a = (a or "").strip()
            if a and "@" in a and a.lower() not in seen:
                seen.add(a.lower())
                cc_list.append(a)

    # 항상 참조 — 앱이 내보내는 모든 메일을 한 사람이 함께 받아 두게 한다.
    # 이미 보낸 사람이거나 받는 사람이면 넣지 않는다(같은 메일을 두 번 받게 된다).
    for a in always_cc():
        if a.lower() not in seen:
            seen.add(a.lower())
            cc_list.append(a)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((show_name, show_from)) if show_name else show_from
    msg["To"] = ", ".join(tos)
    # 보이는 주소가 그 사람 것이 아닐 때는 답장이 갈 곳을 따로 적어 준다 — 시스템
    # 주소에 사서함이 없으면 그리로 온 답장은 반송된다.
    if reply_to:
        msg["Reply-To"] = reply_to
    # Message-ID·Date 를 **직접** 단다. smtplib 도, 사내 메일 서버도 붙여 주지 않아
    # 우리가 보낸 메일에는 식별자가 아예 없었다(실측 2026-08-19, 수신함 원문 확인).
    # 그래서 (1) 받는 사람이 회신해도 우리 메일을 가리킬 수 없고, (2) 그 회신을 다시
    # 수집할 때 message_id 로 묶는 중복 제거가 헛돌고, (3) Date 가 없어 클라이언트가
    # 수신 시각을 서버 헤더로 추정한다.
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=(show_from.rpartition("@")[2] or None))
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)   # send_message 가 To+Cc 모두에게 보낸다
    # 앱이 보낸 메일 표식 — 우리 회신이 다시 '신규 요청'으로 오인되지 않게 필터가 이걸 본다.
    if app_marker:
        msg["X-TW-Automation"] = app_marker
    # 원본 Message-ID 가 있으면 회신 스레드로 이어붙인다.
    mid = (in_reply_to or "").strip()
    if mid:
        if not (mid.startswith("<") and mid.endswith(">")):
            mid = f"<{mid.strip('<>')}>"
        msg["In-Reply-To"] = mid
        # References = 원본 체인 + 원본 Message-ID (없으면 Message-ID 만).
        ref = (references or "").strip()
        msg["References"] = (f"{ref} {mid}".strip()) if ref else mid
    # 회신이면 원본을 본문 아래에 인용해 붙인다 — 새로 쓴 몇 줄만 가면 받는 사람이
    # 무엇에 대한 답인지 알 수 없다(사용자 지적 2026-08-19).
    # html: 다 만든 HTML 본문(안내 메일 양식). 주면 그대로 싣고 개인 서명은 붙이지 않는다.
    _sig = "" if html else (signature or _pf_sig)
    plain = body
    if _sig:
        plain = plain.rstrip() + chr(10) * 2 + html_to_text(_sig)
    if quote:
        plain = plain.rstrip() + chr(10) + quote
    msg.set_content(plain)
    # 사내 메일과 같은 서식(맑은 고딕 10pt · 인용부 구분선)으로 보이도록 HTML 판을
    # **모든 메일에** 함께 싣는다. 평문만 보내면 글꼴도 구분선도 없이 밋밋하게 뜬다
    # (사용자 지적 2026-08-19).
    msg.add_alternative(
        html or build_html_mail(body, quote=quote_html, signature=(signature or _pf_sig)),
        subtype="html")
    for fname, data in attachments or []:
        msg.add_attachment(data, maintype="application", subtype="octet-stream", filename=fname)

    try:
        with smtplib.SMTP(ident.host, ident.port, timeout=30) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(ident.email, ident.password)
            # 봉투 발신자(Return-Path)는 **실제 계정**으로 둔다. From 만 시스템 주소로
            # 바꾸고 반송까지 그리로 보내면, 사서함이 없을 때 배달 실패 통지가 사라진다.
            s.send_message(msg, from_addr=ident.email)
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, f"SMTP 발송 실패: {e}"
