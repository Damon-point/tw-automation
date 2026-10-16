"""
API 프로세스의 기동 · 주기 작업 — 8080 의 app.main._startup 과 app.background.install 을 옮긴 것.

8080 은 NiceGUI 타이머로 돌렸다. 여기는 화면 서버가 따로라 데몬 스레드로 돌린다.
단계 진행률이 이 프로세스 메모리에 있으므로, 단계·주기 작업 모두 이 프로세스 안에서만 돈다.
외부로 나가는 쓰기는 app.shadow 가 막는다(검증 모드).
"""
from __future__ import annotations

import threading
import time
import traceback

_started = False
_lock = threading.Lock()
status: dict = {"started_at": "", "loops": {}}


def _log(msg: str) -> None:
    try:
        from app.background import _log as blog

        blog(msg)
    except Exception:  # noqa: BLE001
        print(msg, flush=True)


def _loop(name: str, every: float, first: float, fn) -> None:
    def run() -> None:
        time.sleep(first)
        while True:
            t0 = time.time()
            try:
                fn()
                status["loops"][name] = {"ok": True, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            except Exception as e:  # noqa: BLE001
                status["loops"][name] = {"ok": False, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "error": str(e)[:300]}
                _log(f"[3080] {name} 오류\n" + traceback.format_exc())
            time.sleep(max(5.0, every - (time.time() - t0)))

    threading.Thread(target=run, name=f"twn-{name}", daemon=True).start()


def _warm() -> None:
    from app import background as B

    for f in (B._warm_imports_sync, B._warm_os_update_sync, B._warm_whitelist_sync):
        try:
            f()
        except Exception:  # noqa: BLE001
            _log(f"[3080] 예열 오류 {f.__name__}\n" + traceback.format_exc())


def start() -> None:
    """한 번만. uvicorn 이 재시작해도 같은 프로세스에서 두 번 돌지 않게 막는다."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    status["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    from app.database import init_db

    init_db()
    from app.pipeline import rn_stage_split

    rn_stage_split.migrate_once()
    from app import staff

    staff.seed_from_legacy()
    staff.merge_team_roster()

    import app.usage

    app.usage.install()
    from app.integrations import claude_cli

    claude_cli.install()

    from app import background as B

    B._clear_stale_running()
    threading.Thread(target=_warm, name="twn-warm", daemon=True).start()

    def tick() -> None:
        B.sync_once()
        B._notify_if_due()
        B._warm_os_update_sync()

    _loop("mail_sync", B.SYNC_INTERVAL_SEC, B.SYNC_INTERVAL_SEC, tick)
    from app import shadow

    def wl_poll() -> None:
        with shadow.area("whitelist"):
            B.whitelist_poll_once()

    if getattr(B, "WL_POLL_ENABLED", False):
        _loop("whitelist_poll", B.WL_POLL_SEC, 25, wl_poll)
    # Redmine 수집 · Solution Buffer 는 별도 프로젝트(pointmobile-faq, 3070)로 옮겼다(2026-10-14) — 여기서는 돌리지 않는다.

    def license_scan() -> None:
        import license_inbox

        with shadow.area("license"):
            license_inbox.scan()

    _loop("license_inbox", 120, 40, license_scan)

    import confirm_sync

    if confirm_sync.enabled():
        # 검수 웹(tw-automation-confirm) — 이 PC 에서 바깥으로만 나간다
        _loop("confirm_sync", 30, 20, confirm_sync.sync_once)
    _log("[3080] 백그라운드 시작 — 메일 동기화·알림·WhiteList·License 메일")
