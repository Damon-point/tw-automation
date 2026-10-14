"""Solution Buffer 동작 설정 — 화면에서 바꾸고 DB(issuetrack.db)에 저장한다.

env 로만 바꿀 수 있으면 값을 고칠 때마다 서버를 재시작해야 한다. 자동 메일 발송처럼
"켜고 끄기"를 자주 판단할 항목은 화면에서 즉시 바꿀 수 있어야 한다.

우선순위: **DB 에 저장된 값 > env 기본값 > 코드 기본값**. env 는 '처음 켤 때의 기본',
DB 는 '사람이 내린 결정'으로 본다. 한 번 화면에서 바꾸면 env 를 고쳐도 안 바뀐다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

from app.issuetrack.db import issuetrack_session
from app.issuetrack.models import SyncState

_PREFIX = "cfg."

#: 키 → (env 이름, 코드 기본값, 형)
_DEFS: dict[str, tuple[str, object, type]] = {
    "sb_auto_generate": ("TW_SB_AUTO_GENERATE", True, bool),
    "sb_auto_notify": ("TW_SB_AUTO_NOTIFY", False, bool),
    "sb_per_tick": ("TW_SB_PER_TICK", 3, int),
    "exclude_keywords": ("TW_REDMINE_EXCLUDE", "janam", str),
    # 이 날짜보다 전에 해결된 이슈는 SB 에 이미 정리돼 있다고 보고 소급 대상에서 뺀다.
    # SB 스페이스의 최신 문서가 2026-04-22 자라 그 앞은 다 완료된 것으로 간주한다.
    # 빈 값이면 컷오프 없이 전부 소급 대상.
    "sb_cutoff": ("TW_SB_CUTOFF", "2026-04-22", str),
    # Solution Buffer 는 TA 팀 전용이다. 여기 적힌 Redmine 담당자의 이슈만
    # 문서를 만들고 메일을 보낸다. 빈 값이면 담당자를 가리지 않는다.
    "sb_assignees": ("TW_SB_ASSIGNEES",
                     "daniel.chae,samuel.seo,jayden.lee,damon.park", str),
}


def _env_default(key: str):
    env_name, fallback, typ = _DEFS[key]
    raw = os.getenv(env_name)
    if raw is None:
        return fallback
    return _cast(raw, typ, fallback)


def _cast(raw: str, typ: type, fallback):
    raw = (raw or "").strip()
    if typ is bool:
        return raw.lower() not in ("0", "false", "no", "off", "")
    if typ is int:
        try:
            return int(raw)
        except ValueError:
            return fallback
    return raw


#: 아주 짧은 캐시. 제외 키워드는 이슈 1,600여 건을 훑는 동안 건당 한 번씩 조회되는데,
#: 그때마다 SQLite 를 열면 백필이 눈에 띄게 느려진다. 화면에서 바꾼 값은 몇 초 안에 반영된다.
_TTL_SEC = 5.0
_cache: dict[str, tuple[float, object]] = {}


def get(key: str):
    """설정 1개. DB 에 값이 있으면 그것, 없으면 env, 그것도 없으면 코드 기본값."""
    if key not in _DEFS:
        raise KeyError(key)
    import time

    hit = _cache.get(key)
    now = time.monotonic()
    if hit is not None and now - hit[0] < _TTL_SEC:
        return hit[1]

    _, fallback, typ = _DEFS[key]
    value = None
    try:
        with issuetrack_session() as db:
            row = db.get(SyncState, _PREFIX + key)
            if row is not None and row.value is not None:
                value = _cast(row.value, typ, fallback)
    except Exception:  # noqa: BLE001
        pass          # DB 를 못 읽어도 동작은 이어져야 한다
    if value is None:
        value = _env_default(key)
    _cache[key] = (now, value)
    return value


def set_(key: str, value) -> None:
    if key not in _DEFS:
        raise KeyError(key)
    typ = _DEFS[key][2]
    raw = ("1" if value else "0") if typ is bool else str(value)
    with issuetrack_session() as db:
        row = db.get(SyncState, _PREFIX + key)
        if row is None:
            row = SyncState(key=_PREFIX + key)
            db.add(row)
        row.value = raw
        row.updated_at = datetime.utcnow()
        db.commit()
    _cache.pop(key, None)       # 바꾼 즉시 반영


def reset(key: str) -> None:
    """저장된 값을 지워 env/코드 기본값으로 되돌린다."""
    with issuetrack_session() as db:
        row = db.get(SyncState, _PREFIX + key)
        if row is not None:
            db.delete(row)
            db.commit()
    _cache.pop(key, None)


@dataclass
class Config:
    sb_auto_generate: bool
    sb_auto_notify: bool
    sb_per_tick: int
    exclude_keywords: str
    sb_cutoff: str
    sb_assignees: str


def load() -> Config:
    return Config(
        sb_auto_generate=bool(get("sb_auto_generate")),
        sb_auto_notify=bool(get("sb_auto_notify")),
        sb_per_tick=max(1, int(get("sb_per_tick"))),
        exclude_keywords=str(get("exclude_keywords")),
        sb_cutoff=str(get("sb_cutoff")),
        sb_assignees=str(get("sb_assignees")),
    )


def sb_assignee_set() -> set[str]:
    """SB 대상 담당자(소문자). 빈 집합이면 담당자를 가리지 않는다."""
    return {n.strip().lower()
            for n in str(get("sb_assignees") or "").split(",") if n.strip()}


def cutoff_date() -> datetime | None:
    """소급 기준일. 이 날짜보다 전에 해결된 건은 SB 문서를 만들지 않는다."""
    raw = str(get("sb_cutoff") or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def exclude_list() -> tuple[str, ...]:
    return tuple(k.strip().lower()
                 for k in str(get("exclude_keywords")).split(",") if k.strip())
