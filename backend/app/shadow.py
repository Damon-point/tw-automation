# -*- coding: utf-8 -*-
"""검증 모드 — 3080 이 8080 과 나란히 도는 동안 외부 시스템에 쓰지 않는다.

같은 Dropbox·FOTA·Portal·Confluence·메일 계정을 두 시스템이 함께 쓴다. 3080 이 실제로
쓰면 8080 에서 진행 중인 작업과 겹치거나 같은 메일이 두 번 나간다. 그래서 외부에 쓰는
**마지막 함수 하나하나**에서 이 모듈에 묻고, 검증 모드면 무엇을 보내려 했는지만
work/shadow_writes.jsonl 에 남기고 성공한 것처럼 돌려준다. 호출하는 쪽이 allow_write=True 를
넘겨도 여기서 막는다.

운영 전환은 설정(app_settings 'live_writes')에서 한다. 영역별로는 'live_areas' —
요청 · 작업이 어느 영역인지(area())를 달고 들어오면 그 영역이 운영 중일 때만 통과한다.
WhiteList · DQA · License 는 3080 에서 실제 업무를 하고, OS Update 는 8080 에 남아 계속 막힌다(2026-10-16).
"""
from __future__ import annotations

import contextvars
import json
import threading
from contextlib import contextmanager
from datetime import datetime

from app.database import BASE_DIR

LOG_PATH = BASE_DIR / "work" / "shadow_writes.jsonl"
_lock = threading.Lock()


def live() -> bool:
    """외부에 실제로 써도 되는가. 설정을 못 읽으면 쓰지 않는다."""
    try:
        from app import app_settings

        return app_settings.get("live_writes") == "1"
    except Exception:  # noqa: BLE001
        return False


AREAS = ("whitelist", "license")
_area: contextvars.ContextVar[str] = contextvars.ContextVar("twn_area", default="")


def live_areas() -> set[str]:
    """live_writes=0 이어도 실제로 쓰는 영역."""
    try:
        from app import app_settings

        v = app_settings.get("live_areas")
    except Exception:  # noqa: BLE001
        return set()
    return {a for a in v.split(",") if a in AREAS}


def area_live(name: str) -> bool:
    return live() or name in live_areas()


def current_area() -> str:
    return _area.get()


@contextmanager
def area(name: str):
    """이 안에서 일어나는 외부 쓰기는 name 영역의 것이다. 스레드로 넘길 때는 carry() 로 감싼다."""
    tok = _area.set(name)
    try:
        yield
    finally:
        _area.reset(tok)


def carry(fn):
    """지금의 영역 표시를 새 스레드까지 들고 간다 — threading.Thread(target=carry(fn))."""
    ctx = contextvars.copy_context()
    return lambda *a, **k: ctx.run(fn, *a, **k)


def record(system: str, action: str, **detail) -> None:
    """보내지 않은 쓰기 하나를 남긴다. 기록이 실패해도 업무는 막지 않는다."""
    row = {"at": datetime.now().isoformat(timespec="seconds"), "system": system, "action": action}
    for k, v in detail.items():
        if isinstance(v, (bytes, bytearray)):
            v = f"<{len(v)} bytes>"
        elif not isinstance(v, (str, int, float, bool, type(None), list, dict)):
            v = str(v)
        if isinstance(v, str) and len(v) > 2000:
            v = v[:2000] + f"… (+{len(v) - 2000})"
        row[k] = v
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _lock, open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001
        pass


def test_mode() -> bool:
    """테스트 모드 — 켜져 있으면 메일은 어떤 경우에도 보내지 않는다(설정을 못 읽으면 꺼진 것으로)."""
    try:
        from app import app_settings

        return app_settings.get("test_mode") == "1"
    except Exception:  # noqa: BLE001
        return False


def blocked(system: str, action: str, **detail) -> bool:
    """검증 모드면 기록하고 True(=보내지 말 것). 운영 중인 영역(live_areas)의 쓰기는 통과.
    테스트 모드면 메일은 운영 · 영역과 상관없이 막는다."""
    if system == "mail" and test_mode():
        record(system, action, area=_area.get() or "-", test_mode=True, **detail)
        return True
    if live():
        return False
    a = _area.get()
    if a and a in live_areas():
        return False
    record(system, action, area=a or "-", **detail)
    return True


def recent(limit: int = 200) -> list[dict]:
    if not LOG_PATH.exists():
        return []
    with _lock:
        lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
    out = []
    for ln in reversed(lines[-limit:]):
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out
