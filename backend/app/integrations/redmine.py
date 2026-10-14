"""Redmine(사내 이슈 관리, https://issue.pointmobile.co.kr) 읽기 — 로그인(ID/PW) 기반.

REST(.json)는 API 키가 필요해(세션 쿠키로는 401) 사용하지 않는다. 대신 **로그인 세션으로
이슈 HTML 페이지를 열어 구조화 파싱**한다. 읽기 전용 — 어떤 이슈도 만들거나 고치지 않는다.

관측된 구조(실제 #33814):
  - 제목에 `[GMS][PM95][A15]` 처럼 제품+Android 태그
  - 프로젝트 슬러그가 제품+Android 로 구분됨(예: `pm95-android-15`)
  - .issue .attributes(Status/Priority/Category/Detected in SW Version 등),
    .description .wiki(문제), #history .journal .wiki(논의/해결), #relations(연결 이슈)
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from urllib.parse import urlparse

from app.agent.browser import _new_context, session_path
from app.integrations.creds import get_web_config

TARGET = "redmine"

# 이슈 페이지에서 구조화 데이터를 뽑는 브라우저 스크립트
_EXTRACT_JS = r"""
() => {
  const t = el => el ? el.innerText.trim() : null;
  const out = {};
  out.h2 = t(document.querySelector('#content h2'));
  out.subject = t(document.querySelector('.issue .subject h3')) || t(document.querySelector('.subject h3'));
  out.author = t(document.querySelector('.issue .author')) || t(document.querySelector('p.author'));
  // 생성자(사용자 링크 = 이슈를 만든 사람). 날짜/앵커 링크가 아니라 /users/ 링크를 집는다.
  out.created_by = t(document.querySelector('.issue .author a[href*="/users/"], p.author a[href*="/users/"]'));
  const attrs = {};
  document.querySelectorAll('.issue .attributes .attribute').forEach(a => {
    const label = t(a.querySelector('.label'));
    const val = t(a.querySelector('.value'));
    if (label) attrs[label.replace(/:$/,'')] = val;
  });
  out.attributes = attrs;
  out.description = t(document.querySelector('.issue .description .wiki'));
  // relations (연결 이슈) — 관계 텍스트(타입)에 'Duplicated by #… / Related to #…' 등이 들어있음
  const rel = [];
  document.querySelectorAll('#relations tr').forEach(tr => {
    const a = tr.querySelector('a[href*="/issues/"]');
    if (a) { const m = a.getAttribute('href').match(/\/issues\/(\d+)/);
             if (m) rel.push({id: m[1], text: t(tr).replace(/\s+/g,' ').slice(0,140)}); }
  });
  out.relations = rel;
  // subtasks
  const sub = [];
  document.querySelectorAll('#issue_tree a[href*="/issues/"]').forEach(a=>{
    const m = a.getAttribute('href').match(/\/issues\/(\d+)/);
    if (m) sub.push({id: m[1], text: t(a)});
  });
  out.subtasks = sub;
  // journals (논의/해결 노트 + 상태변경 이력). details 에 'Status changed from … to Closed' 등이 있어
  // 누가 해결/완료 처리했는지 파악한다.
  const notes = [];
  document.querySelectorAll('#history .journal').forEach(j => {
    const n = t(j.querySelector('.wiki'));
    // 저널 작성자 = /users/ 링크(앵커 #4 등이 아니라)
    const who = t(j.querySelector('a[href*="/users/"]'));
    const details = [];
    j.querySelectorAll('.details li').forEach(li => { const d = t(li); if (d) details.push(d); });
    if (n || details.length) notes.push({who, note: n || '', details});
  });
  out.journal_notes = notes;
  // 본문 내 참조된 모든 이슈 번호
  const links = new Set();
  document.querySelectorAll('#content a[href*="/issues/"]').forEach(a=>{
    const m = a.getAttribute('href').match(/\/issues\/(\d+)/);
    if (m) links.add(m[1]);
  });
  out.referenced_issue_ids = [...links];
  // 프로젝트 슬러그(제품+Android)
  let slug = null;
  const pl = document.querySelector('#content a[href*="/projects/"], .breadcrumbs a[href*="/projects/"], #header a[href*="/projects/"]');
  if (pl) { const m = pl.getAttribute('href').match(/\/projects\/([^\/?#]+)/); if (m) slug = m[1]; }
  out.project_slug = slug;
  return out;
}
"""


def _base(cfg) -> str:
    u = urlparse((cfg.base_url or "").strip())
    if u.scheme and u.netloc:
        return f"{u.scheme}://{u.netloc}"
    return (cfg.base_url or "").strip().split("#")[0].split("?")[0].rstrip("/")


def _logged_in(page, base: str) -> bool:
    """HTML 기준 로그인 확인(REST 대신). 상단 'Logged in as' / 로그아웃 링크 존재로 판단."""
    try:
        page.goto(base + "/my/page", wait_until="domcontentloaded", timeout=20000)
    except Exception:  # noqa: BLE001
        return False
    return page.query_selector("#loggedas, .logged-as, a.logout, a[href='/logout']") is not None


def _do_login(page, cfg, base: str) -> None:
    """로그인 폼이 있으면 채워 제출. 이미 로그인 상태면(폼 없음) 그냥 통과."""
    login_url = (cfg.login_url or "").strip() or (base + "/login")
    page.goto(login_url, wait_until="domcontentloaded", timeout=25000)
    usel = cfg.username_sel or "#username"
    if page.query_selector(usel) is None:
        return  # 로그인 폼이 없음 = 이미 로그인됨
    page.fill(usel, cfg.username or "")
    page.fill(cfg.password_sel or "#password", cfg.password or "")
    page.click(cfg.submit_sel or "#login-submit")
    page.wait_for_load_state("domcontentloaded")


@contextmanager
def _session(user_id: int, headful: bool = False):
    """로그인된 Redmine page 를 제공. yield (page, base, err)."""
    cfg = get_web_config(user_id, TARGET)
    if cfg is None or not cfg.base_url:
        yield None, None, (
            "'redmine' 웹 편집 계정이 설정되지 않았습니다. [설정 → 웹 편집 계정]에서 "
            "Redmine URL/아이디/비밀번호를 등록하세요."
        )
        return

    from playwright.sync_api import sync_playwright

    base = _base(cfg)
    with sync_playwright() as p:
        browser, ctx = _new_context(p, user_id, TARGET, headless=not headful)
        try:
            page = ctx.new_page()
            if not _logged_in(page, base):
                try:
                    _do_login(page, cfg, base)
                except Exception as e:  # noqa: BLE001
                    yield None, None, f"자동 로그인 실패: {e}. 로그인 URL/셀렉터/계정을 확인하세요."
                    return
                ctx.storage_state(path=str(session_path(user_id, TARGET)))
                if not _logged_in(page, base):
                    yield None, None, "로그인했지만 세션이 확인되지 않습니다. 계정/셀렉터를 확인하세요."
                    return
            yield page, base, ""
        finally:
            browser.close()


def _scrape_issue(page, base: str, issue_id) -> dict | None:
    try:
        page.goto(base + f"/issues/{issue_id}", wait_until="domcontentloaded", timeout=25000)
    except Exception:  # noqa: BLE001
        return None
    # 존재하지 않거나 접근 불가면 subject 가 없음
    data = page.evaluate(_EXTRACT_JS)
    if not data or not data.get("subject"):
        return None
    data["id"] = str(issue_id)
    data["url"] = base + f"/issues/{issue_id}"
    attrs = data.get("attributes") or {}
    data["status"] = attrs.get("Status", "")
    data["category"] = attrs.get("Category", "")
    data["priority"] = attrs.get("Priority", "")
    data["assignee"] = attrs.get("Assignee", "")
    created_by = (data.get("created_by") or "").strip()
    if not created_by or created_by[:1].isdigit() or "/" in created_by:
        # 사용자 링크가 없거나 날짜를 잡은 경우 → author 블롭에서 name.surname 추출
        m = re.search(r"[A-Za-z]+\.[A-Za-z]+", data.get("author") or "")
        created_by = m.group(0) if m else created_by
    data["created_by"] = created_by
    # 해결자: 상태를 Resolved/Closed 로 바꾼 마지막 저널의 작성자
    resolved_by = ""
    for j in data.get("journal_notes") or []:
        for d in j.get("details") or []:
            dl = d.lower()
            if "status" in dl and any(k in dl for k in ("resolved", "closed", "완료", "해결", "종료")):
                resolved_by = (j.get("who") or "").strip() or resolved_by
    data["resolved_by"] = resolved_by
    return data


def get_issues(
    user_id: int, issue_ids: list[str | int], headful: bool = False
) -> tuple[dict[str, dict], list[str]]:
    """여러 이슈를 한 세션에서 스크래핑. ({id: issue}, [오류들])."""
    out: dict[str, dict] = {}
    errors: list[str] = []
    with _session(user_id, headful) as (page, base, err):
        if err:
            return {}, [err]
        for iid in issue_ids:
            issue = _scrape_issue(page, base, iid)
            if issue is None:
                errors.append(f"#{iid}: 읽기 실패")
            else:
                out[str(iid)] = issue
    return out, errors


def find_issue_refs(text: str) -> list[str]:
    """텍스트(메일 등)에서 Redmine 이슈 번호 참조를 뽑는다. '#33814' 또는 '/issues/33814'."""
    ids = re.findall(r"(?:#|/issues/)(\d{3,7})", text or "")
    seen, out = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out
