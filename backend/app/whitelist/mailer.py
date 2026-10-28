"""WhiteList 알림 메일. Apps Script sendRequestEmail/sendResultEmail 이식.

발송 방식 = **앱 표준(user_mailer)**: 공용 SMTP 계정이 아니라 로그인 사용자가
[계정 관리]에 등록한 본인 메일 자격증명으로 보낸다(From = 그 사람).
  · 신규 신청 알림 : 신청자 계정 → role=admin 사용자들의 등록 이메일
  · 승인/거절 결과 : 처리한 관리자 계정 → 신청자 이메일
자격증명 미등록이면 (False, 사유) 반환 → 승인/거절 자체는 진행되고 발송만 스킵.
"""
from __future__ import annotations

import re

from app.user_mailer import admin_emails, reply_context, send_as_user


def detect_lang(text: str) -> str:
    """한글이 있으면 'ko', 없으면 'en'. (요청 메일은 한/영 둘 중 하나)"""
    return "ko" if re.search(r"[가-힣]", text or "") else "en"


def _reply_device_lines(devices: list[dict], by_model: bool = False) -> str:
    """회신에 넣는 S/N — 실제로 보낸 회신(2026-09~10)과 같은 꼴.

    등록: 'SN: a, b, c' 한 줄(많으면 10개씩 줄을 나눈다).
    등록일 변경: 모델을 알면 '1. PM95 : a, b' 처럼 모델별 번호 목록.
    """
    sns = [str(d.get("sn", "")).strip() for d in devices if str(d.get("sn", "")).strip()]
    if by_model and any(d.get("model") for d in devices):
        groups: dict[str, list[str]] = {}
        for d in devices:
            sn = str(d.get("sn", "")).strip()
            if sn:
                groups.setdefault(str(d.get("model") or "-"), []).append(sn)
        return "\n".join(f"{i+1}. {m} : {', '.join(v)}" for i, (m, v) in enumerate(groups.items()))
    rows = [", ".join(sns[i:i + 10]) for i in range(0, len(sns), 10)]
    return "SN: " + ",\n".join(rows)


_GREET = re.compile(r"^([가-힣]{2,4}\s*(?:[가-힣]{1,4}\s*)?님)\s*[.,]?$")
_QUOTE_SPLIT = re.compile(r"\n\s*(?:From:|보낸 사람:|-----Original|-----\s*원본|발신:)")


def learned_greeting(to_addr: str, sender_addr: str) -> str:
    """보내는 사람이 그 받는 사람에게 마지막으로 쓴 호칭 — '염서연 대리님'.

    메일 머리의 이름은 영문(Rosie Yeom)이고 직원 명부에는 직함이 없다. 직접 보낸 회신에는
    인사 다음 줄에 늘 '이름 직함님' 을 쓰므로 그것을 배운다. 못 찾으면 ''.
    """
    to_addr, sender_addr = (to_addr or "").strip().lower(), (sender_addr or "").strip().lower()
    if not (to_addr and sender_addr):
        return ""
    from sqlalchemy import text

    from app.database import engine

    with engine.connect() as c:
        rows = c.execute(text(
            "select body_text from cached_mails where lower(sender) like :s and lower(to_recipients) like :t "
            "order by id desc limit 30"), {"s": f"%{sender_addr}%", "t": f"%{to_addr}%"}).fetchall()
    for (body,) in rows:
        own = _QUOTE_SPLIT.split(body or "")[0]
        lines = [ln.strip() for ln in own.splitlines() if ln.strip()]
        for i, ln in enumerate(lines[:6]):
            if ln.startswith("안녕하세요") and i + 1 < len(lines):
                m = _GREET.match(lines[i + 1])
                if m:
                    return re.sub(r"\s+", " ", m.group(1))
    return ""


def _own_part(body: str) -> str:
    return _QUOTE_SPLIT.split(body or "")[0]


_INTRO = re.compile(r"^안녕하세요[.,!]?\s*(\S.{0,30}?입니다)[.!]?\s*$")


def learned_intro(sender_addr: str) -> str:
    """보내는 사람이 인사 줄에 쓰는 자기소개 — 'TA팀 박훈일입니다.' 못 찾으면 ''.

    실제 회신(2026-10, 메일함 #5161~#5953)은 모두 '안녕하세요. TA팀 박훈일입니다.' 로 시작한다.
    로그인한 사람마다 다르므로 문구에 박지 않고 그 사람이 최근에 보낸 메일에서 배운다.
    """
    sender_addr = (sender_addr or "").strip().lower()
    if not sender_addr:
        return ""
    from sqlalchemy import text

    from app.database import engine

    with engine.connect() as c:
        rows = c.execute(text(
            "select body_text from cached_mails where lower(sender) like :s and body_text like '안녕하세요%' "
            "order by id desc limit 30"), {"s": f"%{sender_addr}%"}).fetchall()
    for (body,) in rows:
        for ln in [x.strip() for x in _own_part(body).splitlines() if x.strip()][:2]:
            m = _INTRO.match(ln)
            if m:
                return m.group(1) + "."
    return ""


# 서명 첫 줄 — Outlook 서명은 이 줄을 감싼 <div> 하나에 통째로 들어 있다(실측 #5953).
_SIG_START = re.compile(r"Best\s+Regards|Kind\s+Regards|Regards,", re.I)
_SIG_CACHE: dict[str, tuple[float, dict]] = {}


def learned_signature(sender_addr: str) -> dict:
    """보내는 사람이 최근에 보낸 메일의 Outlook 서명 그대로 — {html, images, mail_id}. 못 찾으면 {}.

    서명은 각자 PC 의 Outlook 에 있어 앱이 따로 가지고 있지 않다. 그 사람이 보낸 메일이 메일함에
    남으므로(참조로 받은 것) 가장 최근 것에서 서명 블록(Best Regards · 이름 · 연락처 · 배너)을 떼어 온다.
    배너는 메일 안에 넣은 그림(cid)이라 받은 원문(_raw.eml)에서 그림까지 꺼낸다 — 행사마다 바뀌는
    배너도 최근 메일을 따라간다. images: [(cid, bytes, maintype, subtype)].
    """
    import time

    sender_addr = (sender_addr or "").strip().lower()
    if not sender_addr:
        return {}
    hit = _SIG_CACHE.get(sender_addr)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    from sqlalchemy import text

    from app.database import engine

    with engine.connect() as c:
        rows = c.execute(text(
            "select id, body_html, attachment_dir from cached_mails where lower(sender) like :s "
            "and body_html ilike '%regards%' order by id desc limit 15"), {"s": f"%{sender_addr}%"}).fetchall()
    out: dict = {}
    for mid, html, adir in rows:
        m = _SIG_START.search(html or "")
        if not m:
            continue
        start = html.rfind("<div", 0, m.start())
        end = html.find("</div>", m.start())
        if start < 0 or end < 0 or "</div>" in html[start:m.start()]:
            continue
        seg = html[start:end + len("</div>")]
        if re.search(r"From:|보낸 사람:|-----Original", seg):
            continue
        cids = re.findall(r'src="cid:([^"]+)"', seg)
        images = _cid_images(adir, cids) if cids else []
        for cid in set(cids) - {i[0] for i in images}:      # 그림을 못 꺼냈으면 그 자리는 뺀다
            seg = re.sub(r'<img[^>]*src="cid:' + re.escape(cid) + r'"[^>]*>', "", seg)
        out = {"html": seg, "images": images, "mail_id": mid}
        break
    _SIG_CACHE[sender_addr] = (time.time(), out)
    return out


def _cid_images(adir: str, cids: list[str]) -> list[tuple[str, bytes, str, str]]:
    import email
    from email import policy
    from pathlib import Path

    raw = Path(adir or "") / "_raw.eml"
    if not adir or not raw.is_file():
        return []
    try:
        with raw.open("rb") as f:
            msg = email.message_from_binary_file(f, policy=policy.default)
    except Exception:  # noqa: BLE001
        return []
    want = set(cids)
    out = []
    for part in msg.walk():
        cid = (part.get("Content-ID") or "").strip().strip("<>")
        if cid in want and part.get_content_maintype() == "image":
            data = part.get_payload(decode=True) or b""
            if data:
                out.append((cid, data, "image", part.get_content_subtype()))
    return out


def signature_preview_html(sig: dict) -> str:
    """미리보기용 — 메일 안 그림(cid:)을 data: 로 바꿔 브라우저에서도 배너가 보이게."""
    import base64

    html = (sig or {}).get("html", "")
    for cid, data, mt, st in (sig or {}).get("images", []):
        html = html.replace(f"cid:{cid}", f"data:{mt}/{st};base64," + base64.b64encode(data).decode())
    return html


def _strip_re(subject: str) -> str:
    s = (subject or "").strip()
    while s.lower().startswith(("re:", "회신:", "답장:")):
        s = s.split(":", 1)[1].strip()
    return s


def build_reply_draft(
    *, to_name: str, devices: list[dict], lang: str, subject_orig: str = "",
    orig_mail: dict | None = None, kind: str = "", new_date: str = "", greeting: str = "",
    intro: str = "",
) -> dict:
    """처리 완료 안내 **초안**(관리자가 다듬어 발송). {subject, body}.

    여기서 만드는 것은 **본문 문구뿐**이다. 스레드 연결(In-Reply-To)과 원문 인용은
    보낼 때 send_reply(orig_mail_id=...) 가 붙인다(2026-08-19). subject_orig/orig_mail
    은 하위호환용으로 받기만 하고 쓰지 않는다.

    kind 가 '등록일 변경' 이면 등록이 아니라 날짜 변경으로 문구를 바꾼다 —
    등록일만 바꾼 건에 '등록되었습니다' 라고 회신하면 사실과 다르다.
    """
    from app import mail_templates as MT

    en = lang == "en"
    date_only = kind == "등록일 변경"
    tbl = _reply_device_lines(devices, by_model=date_only)
    n = len(devices)
    key = ("wl_reply_date_" if date_only else "wl_reply_done_") + ("en" if en else "ko")
    if en:
        호칭 = (" " + to_name) if to_name else ""
        변경일 = (" to " + new_date) if new_date else ""
        호칭줄 = (to_name + ",\n\n") if to_name else ""
    else:
        호칭 = (", " + to_name + "님") if to_name else ""
        변경일 = ("(" + new_date + ")") if new_date else ""
        # 직접 보낸 회신에서 배운 호칭('염서연 대리님')이 먼저, 없으면 이름님. 실제 회신처럼 끝에 쉼표.
        who = greeting or ((to_name + "님") if to_name else "")
        호칭줄 = (who + ",\n\n") if who else ""
    subject, body = MT.render(key, 호칭=호칭, 호칭줄=호칭줄, 대수=n, 단말목록=tbl, 변경일=변경일,
                              소개=(" " + intro) if intro and not en else "",
                              원본제목=_strip_re(subject_orig) or "FOTA 등록")
    return {"subject": subject, "body": body}


def build_dqa_reply_draft(*, to_name: str, subject_orig: str, intro: str = "") -> dict:
    """DQA FOTA Test 등록 완료 회신 **초안**. {subject, body}.

    사내 회신이라 고객 회신(build_reply_draft)과 형식이 다르다. 실제로 보낸 메일
    (메일함 [933]·[1343], jayden.lee → DQA 요청자)을 그대로 따랐다:
      · 제목은 원본에 RE: 만 붙인다(스레드 유지) — [White List] 머리말을 쓰지 않는다.
      · **SN 목록을 넣지 않는다.** 인용된 원본에 이미 있어서 실제 회신도 넣지 않는다.
    서명은 넣지 않는다 — 보내는 사람이 로그인 계정마다 달라 지어낼 수 없다.
    """
    from app import mail_templates as MT

    subj = (subject_orig or "").strip() or "FOTA Test 등록 완료"
    # 이미 RE: 로 시작하면 문구의 'RE: ' 가 겹치지 않게 떼고 넘긴다.
    while subj.lower().startswith(("re:", "회신:", "답장:")):
        subj = subj.split(":", 1)[1].strip()
    subject, body = MT.render(
        "wl_reply_dqa", 원본제목=subj, 소개=(" " + intro) if intro else "",
        호칭줄=(f"{to_name}님.\n\n" if to_name else ""))
    return {"subject": subject, "body": body}


def send_reply(sender_user_id: int | None, to_addr: str, subject: str,
               body: str, in_reply_to: str = "", *, references: str = "",
               cc: "list[str] | str | None" = None,
               quote: str = "", orig_mail_id=None,
               with_cc: bool = False) -> tuple[bool, str]:
    """관리자 본인 계정으로 요청자에게 회신 발송(원본 스레드 + 참조인 보존).

    in_reply_to: 원본 요청 메일의 Message-ID — 주면 원본 스레드로 회신된다.
    references : 원본 References 헤더(스레드 체인). cc: 원본 To/Cc(참조인 보존).
    """
    if sender_user_id is None:
        return False, "로그인 사용자 정보 없음"
    to = (to_addr or "").strip()
    if not to:
        return False, "회신 받을 주소가 없습니다."
    # 원본 메일 id 를 주면 스레드 헤더·인용을 한 번에 갖춘다. WhiteList 회신은
    # 고객이 보낸 **요청 메일에 대한 답**이라 원문이 함께 실려야 한다.
    ctx = reply_context(orig_mail_id, with_cc=with_cc) if orig_mail_id else {}
    # 서명은 보내는 사람의 Outlook 서명 그대로(배너 그림까지) — 못 찾으면 앱 기본 서명(이름 한 줄)
    from app.user_mailer import user_email

    sig = learned_signature(user_email(sender_user_id))
    return send_as_user(
        sender_user_id, to, subject, body,
        in_reply_to=in_reply_to or ctx.get("in_reply_to", ""),
        references=references or ctx.get("references", ""),
        cc=cc if cc is not None else ctx.get("cc"),
        quote=quote or ctx.get("quote", ""),
        quote_html=ctx.get("quote_html", ""),
        signature=sig.get("html", ""), inline_images=sig.get("images") or None)


def _device_lines(devices: list[dict]) -> str:
    return "\n".join(
        f"{i+1}. SN: {d.get('sn','')} | API: {d.get('api','')} | "
        f"End Date: {d.get('endDate','')} | OS Type: {d.get('ostype','')}"
        for i, d in enumerate(devices)
    )


def send_new_request(req: dict, sender_user_id: int | None) -> tuple[bool, str]:
    """신규 신청 → 관리자 알림(신청자 본인 계정으로 발송)."""
    if sender_user_id is None:
        return False, "로그인 사용자 정보 없음"
    tos = admin_emails()
    if not tos:
        return False, "관리자 이메일 미등록([계정 관리]에서 admin 계정의 발송 이메일 등록 필요)"

    from app import mail_templates as MT

    devices = req.get("devices", [])
    subject, body = MT.render(
        "wl_new_request",
        요청번호=req.get("id", ""),
        요청자=f"{req.get('userName','')} ({req.get('userId','')})",
        팀=req.get("team") or "-",
        제출일=str(req.get("createdAt", ""))[:10],
        대수=len(devices),
        유무상=req.get("chargeType") or "-",
        메모=req.get("memo") or "(없음)",
        단말목록=_device_lines(devices),
    )
    return send_as_user(sender_user_id, tos, subject, body)
