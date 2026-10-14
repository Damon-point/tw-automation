"""고객 포털 데모 진입점 — `python -m app.portal.main` → http://localhost:8082

사내 앱(app.main, 8080/8081)과 **다른 프로세스**로 뜬다. 8082 를 외부 서버라고 보면 된다.
  · 사내 화면 라우트를 하나도 등록하지 않는다
  · 세션 쿠키가 사내 앱과 섞이지 않도록 storage_secret 을 따로 쓴다
  · 데이터는 whitelist.db 를 직접 읽는다 — 실제로는 클라우드로 동기화된 사본이 될 자리다
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from nicegui import app as ng_app
from nicegui import ui

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

import app.portal.pages  # noqa: E402,F401  (@ui.page 라우트 등록)
from app.whitelist.db import init_whitelist_db  # noqa: E402


def _startup() -> None:
    init_whitelist_db()


def run() -> None:
    ng_app.on_startup(_startup)
    ui.run(
        title="Point Mobile · WhiteList",
        # 사내 앱과 다른 값 — 같은 브라우저에서 두 화면을 함께 열어도 세션이 섞이지 않는다.
        storage_secret=os.getenv("WL_PORTAL_STORAGE_SECRET", "wl-portal-demo-secret"),
        reload=False,
        host=os.getenv("WL_PORTAL_HOST", "0.0.0.0"),
        port=int(os.getenv("WL_PORTAL_PORT", "8082")),
        show=False,
        favicon="🔒",
    )


if __name__ in {"__main__", "__mp_main__"}:
    run()
