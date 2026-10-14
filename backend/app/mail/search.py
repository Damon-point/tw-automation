"""동기화된 전체 메일(CachedMail) 검색·읽기 — Claude 에이전트(② RN 제작)가 쓴다.

OS update 의 Redmine 이슈 번호/맥락이 관련 메일에 흩어져 있으므로, 키워드/이슈번호로
전체 메일을 뒤질 수 있어야 한다. 읽기 전용.
"""
from __future__ import annotations

import re

from app.database import get_session
from app.models import CachedMail


def _strip_html(h: str) -> str:
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    h = re.sub(r"&nbsp;", " ", h)
    h = re.sub(r"[ \t]+", " ", h)
    h = re.sub(r"\n\s*\n+", "\n", h)
    return h.strip()


def _body(m: CachedMail) -> str:
    b = (m.body_text or "").strip()
    if not b and m.body_html:
        b = _strip_html(m.body_html)
    return b


def _snippet(text: str, query: str, width: int = 160) -> str:
    i = text.lower().find(query.lower())
    if i < 0:
        return text[:width]
    s = max(0, i - width // 3)
    return ("…" if s > 0 else "") + text[s : s + width] + "…"


def search_mails(user_id: int, query: str, limit: int = 20) -> list[dict]:
    """제목/본문에 query 를 포함하는 메일을 최신순으로 검색. [{id,subject,sender,received_at,snippet}]."""
    q = (query or "").strip().lower()
    # 팀 공용 보드: 어느 계정이 받은 메일이든 전체를 검색한다.
    with get_session() as db:
        mails = (
            db.query(CachedMail)
            .order_by(CachedMail.id.desc())
            .all()
        )
    out: list[dict] = []
    for m in mails:
        subject = m.subject or ""
        body = _body(m)
        hay = (subject + "\n" + body).lower()
        if q and q not in hay:
            continue
        out.append(
            {
                "id": m.id,
                "subject": subject,
                "sender": m.sender or "",
                "received_at": m.received_at or "",
                "snippet": _snippet(body or subject, q) if q else (body or subject)[:160],
            }
        )
        if len(out) >= limit:
            break
    return out


def read_mail(user_id: int, mail_id: int) -> dict | None:
    """메일 한 건의 전체 내용. {id,subject,sender,received_at,body}."""
    with get_session() as db:
        m = db.query(CachedMail).filter_by(id=mail_id).first()
        if m is None:
            return None
        return {
            "id": m.id,
            "subject": m.subject or "",
            "sender": m.sender or "",
            "received_at": m.received_at or "",
            "body": _body(m),
        }
