"""WhiteList 메일 **처리 상태** — 어떤 요청을 이미 등록했는지 기록한다.

같은 요청을 두 번 등록하는 걸 막기 위한 것이다. 단위는 메일이 아니라 **스레드**다
(한 요청이 회신으로 여러 통이 되므로, 하나를 처리하면 그 대화는 끝난 것으로 본다).

다만 '처리 완료'는 **그 시점까지**를 뜻한다. 같은 제목으로 계속 오가는 대화에서
나중에 새 요청이 회신으로 들어오면 그건 아직 처리 전이므로 다시 목록에 떠야 한다.
그래서 기록에 기준 시각을 남기고, 그보다 늦게 온 메일이 있으면 다시 열린 것으로 본다.
(실제 사례: 2026-07-29 완료 표시한 'FOTA 등록 요청 - Carema DE' 스레드에 2026-08-18
 등록일 변경 요청이 회신으로 왔는데 완료 표시에 가려 목록에 뜨지 않았다.)

로컬 JSON(work/whitelist_mail_state.json). 메일 자체는 건드리지 않는다.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.atomic_io import atomic_write_text, file_lock

_FILE = Path(__file__).resolve().parent.parent.parent / "work" / "whitelist_mail_state.json"

DONE = "done"           # 등록 완료 — 목록에서 기본 숨김
OPEN = "open"           # 미처리
NOTWL = "notwl"         # WhiteList 작업이 아니다 — 잘못 들어온 것


def _load() -> dict:
    if _FILE.exists():
        try:
            return json.loads(_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def all_records() -> dict:
    """기록 전체를 한 번에 읽는다.

    목록을 그릴 때 스레드마다 is_done() 을 부르면 같은 파일을 수십 번 다시 읽는다.
    한 번 읽어 두고 is_done_rec() 로 판단하라는 용도다.
    """
    return _load()


def _cutoff(rec: dict) -> float:
    """이 기록이 '여기까지 처리했다'고 말하는 시각(epoch 초). 못 읽으면 0.0.

    기준은 표시 당시 스레드의 가장 늦은 수신 시각(rep_ts)과 표시한 시각(at) 중 나중 것이다.
    at 만으로도 대개 맞지만(사람이 누르는 시점은 늘 메일 수신 뒤다), 발신 측 시계가
    앞서 있는 메일이 있어 rep_ts 를 같이 본다. 옛 기록에는 rep_ts 가 없어 at 만 쓴다.
    """
    ts = 0.0
    try:
        ts = float(rec.get("rep_ts") or 0.0)
    except (TypeError, ValueError):
        ts = 0.0
    try:
        ts = max(ts, datetime.fromisoformat(rec.get("at") or "").timestamp())
    except (TypeError, ValueError):
        pass
    return ts


def is_done_rec(rec: dict | None, newest_ts: float | None = None) -> bool:
    """기록 하나로 판단한다. newest_ts = 그 스레드에서 가장 늦게 온 메일의 수신 시각.

    newest_ts 를 주면 **완료 표시 이후에 새 메일이 온 스레드는 다시 열린 것으로 본다.**
    주지 않으면 예전처럼 표시 여부만 본다(표시가 남아 있는지 확인할 때 쓴다).
    """
    if not rec or rec.get("status") != DONE:
        return False
    if not newest_ts:
        return True
    cut = _cutoff(rec)
    if cut <= 0.0:
        return True            # 기준 시각을 못 읽으면 함부로 되살리지 않는다
    return newest_ts <= cut


def is_notwl_rec(rec: dict | None) -> bool:
    """이 스레드는 **WhiteList 작업이 아니다**라고 표시해 두었는가.

    '처리 완료'와 다르다. 완료는 *그 시점까지* 처리했다는 뜻이라 새 회신이 오면 다시
    열리는데, 작업이 아닌 메일은 회신이 와도 여전히 작업이 아니다. 그래서 시각을
    따지지 않는다(사용자 요청 2026-09-14 — WhiteList 아닌 메일이 목록에 섞인다).
    """
    return bool(rec) and rec.get("status") == NOTWL


def status_of(thread_key: str) -> str:
    return (_load().get(thread_key) or {}).get("status", OPEN)


def is_done(thread_key: str, newest_ts: float | None = None) -> bool:
    return is_done_rec(_load().get(thread_key), newest_ts)


def set_status(thread_key: str, status: str, by: str = "", note: str = "",
               rep_ts: float = 0.0) -> None:
    """스레드 하나의 처리 상태를 바꾼다.

    rep_ts: 표시 당시 그 스레드에서 가장 늦게 온 메일의 수신 시각(epoch 초).
            이후 이보다 늦게 온 메일이 있으면 다시 열린 것으로 본다.
            생략하면 표시한 시각(at)을 기준으로 삼는다.
    """
    with file_lock(_FILE):
        data = _load()
        if status == OPEN:
            data.pop(thread_key, None)
        else:
            data[thread_key] = {
                "status": status,
                "by": by or "",
                "note": note or "",
                "rep_ts": float(rep_ts or 0.0),
                "at": datetime.now().isoformat(timespec="seconds"),
            }
        atomic_write_text(_FILE, json.dumps(data, ensure_ascii=False, indent=2))
