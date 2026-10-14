"""영역 라우터들이 같이 쓰는 것 — 로그인 사용자 확인 · 관리자 확인."""
from __future__ import annotations

from fastapi import HTTPException, Request


def user(request: Request) -> dict:
    u = request.session.get("user")
    if not u:
        raise HTTPException(401, "로그인이 필요합니다")
    return u


def admin(request: Request) -> dict:
    u = user(request)
    if u.get("role") != "admin":
        raise HTTPException(403, "관리자만 할 수 있습니다")
    return u
