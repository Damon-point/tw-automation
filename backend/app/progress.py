"""단계 실행 진행상황 — 워커 스레드가 쓰고 UI가 읽는다.

각 단계는 UI 에서 run.io_bound 로 별도 스레드에서 실행된다. execute_stage 가
begin(stage_id) 로 '현재 스레드'를 그 stage_id 에 묶고, 파이프라인 코드는
step("지금 하는 일") 로 진행 상황을 알린다. step 은 인자로 stage_id 를 받지 않고
현재 스레드에 묶인 값을 쓰므로, 파이프라인 함수 시그니처를 바꾸지 않아도 된다.

UI(task_board)는 get(stage_id) 를 타이머로 폴링해 '실행 중' 옆에 현재 작업 + 경과시간을
표시한다. 완료되면 execute_stage 가 end() 로 표시하고, UI 가 결과를 그린 뒤 clear() 한다.
"""
from __future__ import annotations

import threading
import time


class StageCancelled(BaseException):
    """사용자가 단계 실행을 중단했다.

    BaseException 을 상속한다 — 파이프라인 곳곳의 `except Exception` 에 걸려
    '작업 실패'로 둔갑하지 않고, execute_stage 까지 곧장 전파되게 하기 위함.
    """


_lock = threading.Lock()
# stage_id -> {"message", "started_at", "updated_at", "done", "cancel"}
_store: dict[int, dict] = {}
_local = threading.local()  # 현재 스레드가 담당하는 stage_id


def begin(stage_id: int, message: str = "시작하는 중…") -> None:
    """이 스레드를 stage_id 에 묶고 진행상황을 초기화한다."""
    _local.stage_id = stage_id
    now = time.time()
    with _lock:
        _store[stage_id] = {
            "message": message, "started_at": now, "updated_at": now,
            "done": False, "cancel": False,
        }


def step(message: str) -> None:
    """현재 단계의 '지금 하는 일'을 갱신한다. 중단 요청이 있으면 StageCancelled 를 던진다.

    파이프라인은 이미 주요 지점마다 step() 을 부르므로, 그 지점들이 곧 '중단 체크포인트'가 된다.
    (진행 중인 단일 작업 — 대용량 다운로드 한 개, Claude 호출 한 번 — 은 그게 끝난 다음 멈춘다.)
    """
    sid = getattr(_local, "stage_id", None)
    if sid is None:
        return
    now = time.time()
    cancelled = False
    with _lock:
        e = _store.get(sid)
        if e is not None:
            if e.get("cancel"):
                cancelled = True
            elif not e["done"]:
                e["message"] = message
                e["updated_at"] = now
    if cancelled:
        raise StageCancelled()


def request_cancel(stage_id: int) -> bool:
    """단계 중단을 요청한다(협조적). 실제 중단은 다음 체크포인트에서 일어난다.

    실행 중인 단계였으면 True, 이미 없거나 끝난 단계면 False.
    """
    with _lock:
        e = _store.get(stage_id)
        if e is None or e.get("done"):
            return False
        e["cancel"] = True
        e["message"] = "중단 요청됨 — 현재 작업을 마무리하는 중…"
        e["updated_at"] = time.time()
        return True


def end(stage_id: int | None = None) -> None:
    """단계 완료 표시. stage_id 를 안 주면 현재 스레드 담당분."""
    sid = stage_id if stage_id is not None else getattr(_local, "stage_id", None)
    _local.stage_id = None
    if sid is None:
        return
    with _lock:
        e = _store.get(sid)
        if e is not None:
            e["done"] = True


def get(stage_id: int) -> dict | None:
    """UI 폴링용. {message, started_at, updated_at, done} 사본 또는 None."""
    with _lock:
        e = _store.get(stage_id)
        return dict(e) if e is not None else None


def clear(stage_id: int) -> None:
    """저장소에서 제거(UI 가 결과를 그린 뒤 호출)."""
    with _lock:
        _store.pop(stage_id, None)
