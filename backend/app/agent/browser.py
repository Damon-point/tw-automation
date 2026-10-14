"""브라우저 자동화 (Playwright): 웹 편집기(Confluence 페이지/네이버 등) 편집.

자격증명(ID/PW)은 설정에 암호화 저장되며, **세션 만료 시 자동 재로그인**한다:
  1) 저장된 세션(쿠키)이 있으면 그걸로 편집 페이지 접속(빠름)
  2) 편집기가 안 뜨면(=세션 만료/미로그인) 저장된 ID/PW로 자동 로그인 → 세션 갱신 → 재시도
  3) 그래도 안 되면(캡차/2FA 등) 명확한 오류 반환

셀렉터·URL 은 설정(WebCredential)에 저장되어 사이트별로 튜닝 가능하다.
"""
from __future__ import annotations

from pathlib import Path

from app.integrations.creds import WebConfig, get_web_config

# 세션 저장 위치 (프로젝트 루트/sessions/)
SESSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "sessions"
_EDITOR_TIMEOUT = 8000  # 편집기 로딩 대기(ms). 넘으면 미로그인으로 간주.


def session_path(user_id: int, target: str) -> Path:
    SESSIONS_DIR.mkdir(exist_ok=True)
    return SESSIONS_DIR / f"{target}_{user_id}.json"


def _edit_url(cfg: WebConfig, page_id: str) -> str:
    return cfg.edit_url_template.format(base=cfg.base_url.rstrip("/"), page_id=page_id)


def capture_session(user_id: int, target: str) -> str:
    """브라우저 창을 띄워 사람이 직접 로그인 → 세션 저장(수동/최초용, 캡차 대응).
    headful + 콘솔 입력이 필요하므로 CLI(scripts/capture_session.py)에서 호출한다.
    """
    cfg = get_web_config(user_id, target)
    if cfg is None:
        raise RuntimeError(f"'{target}' 웹 편집 계정이 설정되지 않았습니다. 설정에서 먼저 등록하세요.")
    from playwright.sync_api import sync_playwright

    path = session_path(user_id, target)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(cfg.login_url)
        print(f"[{target}] 브라우저에서 로그인/필요한 페이지 접근 후 이 창에서 Enter 를 누르세요.")
        input("완료했으면 Enter... ")
        ctx.storage_state(path=str(path))
        browser.close()
    return str(path)


def _new_context(p, user_id: int, target: str, headless: bool = True):
    """저장된 세션이 있으면 실어서 컨텍스트 생성.

    headless=False 면 브라우저 창이 화면에 보인다(테스트 관찰용).
    """
    browser = p.chromium.launch(headless=headless)
    sp = session_path(user_id, target)
    if sp.exists():
        ctx = browser.new_context(storage_state=str(sp))
    else:
        ctx = browser.new_context()
    return browser, ctx


def _login(page, cfg: WebConfig) -> None:
    """저장된 ID/PW 로 로그인 폼을 채워 로그인."""
    if not cfg.username or not cfg.password:
        raise RuntimeError(
            "자동 재로그인 실패: 저장된 아이디/비밀번호가 없습니다. 설정에서 등록하세요."
        )
    page.goto(cfg.login_url)
    page.wait_for_selector(cfg.username_sel, timeout=_EDITOR_TIMEOUT)
    page.fill(cfg.username_sel, cfg.username)
    page.fill(cfg.password_sel, cfg.password)
    page.click(cfg.submit_sel)
    page.wait_for_load_state("networkidle", timeout=15000)


def _open_editor(page, ctx, cfg: WebConfig, page_id: str, user_id: int):
    """편집 페이지를 열고 편집기 iframe 프레임을 반환. 세션 만료 시 자동 재로그인 후 재시도."""
    page.goto(_edit_url(cfg, page_id))
    handle = _wait_editor(page, cfg)
    if handle is None:
        # 미로그인/만료로 판단 → 자동 로그인 → 세션 저장 → 재시도
        _login(page, cfg)
        ctx.storage_state(path=str(session_path(user_id, cfg.target)))
        page.goto(_edit_url(cfg, page_id))
        handle = _wait_editor(page, cfg)
        if handle is None:
            raise RuntimeError(
                "로그인 후에도 편집기를 찾지 못했습니다. 셀렉터/URL 설정 또는 캡차 여부를 확인하세요."
            )
    return handle.content_frame()


def _wait_editor(page, cfg: WebConfig):
    try:
        return page.wait_for_selector(cfg.editor_iframe_sel, timeout=_EDITOR_TIMEOUT)
    except Exception:  # noqa: BLE001
        return None


# --- 공개 API (에이전트 도구가 호출) ----------------------------------
def get_content(user_id: int, target: str, page_id: str) -> str:
    cfg = get_web_config(user_id, target)
    if cfg is None:
        raise RuntimeError(f"'{target}' 웹 편집 계정이 설정되지 않았습니다. 설정에서 등록하세요.")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, ctx = _new_context(p, user_id, target)
        try:
            page = ctx.new_page()
            frame = _open_editor(page, ctx, cfg, page_id, user_id)
            return frame.evaluate("() => document.body.innerHTML")
        finally:
            browser.close()
