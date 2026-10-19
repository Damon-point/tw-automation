# -*- coding: utf-8 -*-
"""앱 전체 설정 — 화면에서 바꾸고 DB 에 남는다.

env 로만 바꿀 수 있으면 값을 고칠 때마다 서버를 재시작해야 한다. "Claude 를 구독으로
부를까, API 열쇠로 부를까" 처럼 상황에 따라 오가는 항목은 화면에서 즉시 바뀌어야 한다.

우선순위: **DB 에 저장된 값 > env 기본값 > 코드 기본값**. env 는 '처음 켤 때의 기본',
DB 는 '사람이 내린 결정' 으로 본다. 한 번 화면에서 바꾸면 env 를 고쳐도 안 바뀐다.
(같은 규칙을 Solution Buffer 설정이 먼저 쓰고 있다 — app/issuetrack/settings.py)

읽는 자리가 Claude 호출 **한 번마다** 이므로 값을 한 번 읽어 두고 쓴다. 화면에서
바꾸면 그 자리에서 잊는다.
"""
from __future__ import annotations

import os
import threading

#: 키 → (env 이름, 코드 기본값, 고를 수 있는 값들)
_DEFS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    # Claude 를 어디로 부를 것인가.
    #   subscription — 이 PC 의 Claude Code(구독 인증)를 부른다. API 요금이 들지 않는다
    #   api          — 예전처럼 api.anthropic.com 에 열쇠로 붙는다
    "claude_backend": ("TW_CLAUDE_BACKEND", "subscription", ("subscription", "api")),
    # 외부 시스템에 실제로 쓸 것인가(3080 전용).
    #   0 — 검증 모드: 메일·Dropbox·파일서버·FOTA·Portal·Confluence·Mailchimp 에 보내지 않고 기록만 한다
    #   1 — 운영: 실제로 쓴다
    "live_writes": ("TWN_LIVE_WRITES", "0", ("0", "1")),
    # live_writes=0 이어도 실제로 쓰는 영역(2026-10-16~). OS Update 는 8080 에 남아 있어 계속 막는다.
    #   whitelist — WhiteList · DQA 화면과 그 자동 작업 / license — PMDM License
    "live_areas": ("TWN_LIVE_AREAS", "none", ("none", "whitelist", "license", "whitelist,license")),
    # 테스트 모드(관리자, 2026-10-19) — 켜면 메일을 하나도 보내지 않는다(운영 중인 영역이어도). 보내려던 것은 기록만.
    "test_mode": ("TWN_TEST_MODE", "0", ("0", "1")),
}

_lock = threading.Lock()
_cache: dict[str, str] = {}


def _env_default(key: str) -> str:
    env_name, fallback, choices = _DEFS[key]
    raw = (os.getenv(env_name) or "").strip().lower()
    if raw in choices:
        return raw
    # 옛 이름도 받아 준다 — 구독을 붙일 때 쓰던 스위치다.
    if key == "claude_backend":
        via = (os.getenv("TW_CLAUDE_VIA_CLI") or "").strip()
        if via == "0":
            return "api"
        if via == "1":
            return "subscription"
    return fallback


def get(key: str) -> str:
    """설정 하나. DB 를 못 읽어도 예외를 올리지 않는다 — 기본값으로 돈다."""
    if key not in _DEFS:
        raise KeyError(key)
    with _lock:
        if key in _cache:
            return _cache[key]
    val = ""
    try:
        from app.database import get_session
        from app.models import AppSetting

        with get_session() as db:
            row = db.get(AppSetting, key)
            val = (row.value if row else "") or ""
    except Exception:  # noqa: BLE001  (표가 아직 없거나 DB 를 못 여는 때)
        val = ""
    if val not in _DEFS[key][2]:
        val = _env_default(key)
    with _lock:
        _cache[key] = val
    return val


def set(key: str, value: str, by: str = "") -> None:  # noqa: A001
    """설정 하나를 바꾼다. 다음 호출부터 바로 듣는다(재시작 없음)."""
    if key not in _DEFS:
        raise KeyError(key)
    if value not in _DEFS[key][2]:
        raise ValueError(f"{key} 에 쓸 수 없는 값입니다: {value!r}")

    from app.database import get_session
    from app.models import AppSetting

    with get_session() as db:
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value=value, updated_by=by or ""))
        else:
            row.value, row.updated_by = value, (by or "")
        db.commit()
    forget()


def choices(key: str) -> tuple[str, ...]:
    return _DEFS[key][2]


def forget() -> None:
    """읽어 둔 값을 잊는다. 바꾼 직후에 부른다."""
    with _lock:
        _cache.clear()
