"""Confluence(OS/FW Release Notes) 페이지 읽기.

대상: http://post.solution-pointmobile.co.kr:8040 (스페이스 키 RN) — 로그인 필요.
흐름:
  1) 저장된 세션(쿠키)으로 접속. 미로그인/만료면 저장된 ID/PW 로 자동 로그인 후 세션 갱신.
  2) Confluence REST(`/rest/api/content/search`)로 제품(예: PM95) 제목의 페이지를 찾는다.
     (검색은 브라우저 컨텍스트의 쿠키를 그대로 쓰는 page.request 로 호출 → 권한 반영)
  3) 찾은 페이지로 이동해 본문(#main-content)에 렌더된 HTML 을 추출해 반환.

편집이 아니라 '읽기'이므로 Naver 편집기 iframe 을 열 필요 없이, 뷰 모드에 렌더된
본문 HTML(= 편집기로 작성된 내용이 표시된 결과)을 가져온다.
자격증명(ID/PW)은 서버가 복호화해 사용하며 화면/로그에 노출하지 않는다.
"""
from __future__ import annotations
from app import shadow as _shadow

import json
import re
from dataclasses import dataclass, field
from urllib.parse import quote, urlparse

from app.agent.browser import SESSIONS_DIR, _login, _new_context, session_path
from app.integrations.creds import get_web_config

TARGET = "release_notes"
DEFAULT_SPACE = "RN"
# REST 는 반드시 JSON 으로 받도록 명시 (미지정 시 Confluence 가 HTML 로그인 페이지를 줄 수 있음)
_JSON_HEADERS = {"Accept": "application/json"}


@dataclass
class ReleaseNote:
    product: str
    found: bool = False
    title: str = ""
    url: str = ""
    page_id: str = ""          # Confluence 콘텐츠 ID (편집 시 PUT 대상)
    version: int = 0           # 현재 버전 번호 (편집 시 +1 하여 낙관적 잠금)
    storage: str = ""          # 편집 원본 = Confluence storage format (매크로 포함)
    html: str = ""             # 렌더된 뷰 HTML (미리보기용)
    candidates: list = field(default_factory=list)  # [{title, url}] 검색 후보들
    error: str = ""
    android_version: str = ""  # 특정에 사용한 Android 버전(있으면)
    ambiguous: bool = False    # Android 특정이 애매해 사람 확인이 필요한지
    latest_os_version: str = ""  # RN 에서 추출한 '가장 최근 OS 버전'
    latest_os_date: str = ""
    os_versions: list = field(default_factory=list)  # 전체 버전 이력 [{version,date}] (문서순)


def _base(cfg) -> str:
    """기본 URL 에서 origin(scheme://host:port)만 뽑는다.

    사용자가 주소창 값(`/display/RN#...` 등 경로·조각 포함)을 붙여넣어도 REST/페이지 URL 이
    깨지지 않도록 정규화한다.
    """
    u = urlparse(cfg.base_url.strip())
    if u.scheme and u.netloc:
        return f"{u.scheme}://{u.netloc}"
    # scheme 이 없으면 원문에서 경로/조각만 제거
    return cfg.base_url.strip().split("#")[0].split("?")[0].rstrip("/")


def _login_url(cfg) -> str:
    """로그인은 항상 /login.action 로 (logout=true 등 잘못된 값 무시)."""
    return _base(cfg) + "/login.action"


def _get_json(page, url: str):
    """REST GET 후 JSON 파싱. JSON 이 아니면 (None, 진단메시지) 반환."""
    resp = page.request.get(url, headers=_JSON_HEADERS)
    ctype = (resp.headers or {}).get("content-type", "")
    body = resp.text()
    if "json" not in ctype.lower() or not body.strip().startswith(("{", "[")):
        # HTML 로그인 페이지 등 → 인증 안 된 상태로 판단
        return None, f"status={resp.status}, content-type={ctype or '없음'}"
    try:
        return resp.json(), ""
    except Exception as e:  # noqa: BLE001
        return None, f"JSON 파싱 실패: {e}"


def _logged_in(page, cfg) -> bool:
    """현재 세션이 로그인 상태인지 REST 로 확인 (JSON 으로 username 확인)."""
    data, _ = _get_json(page, _base(cfg) + "/rest/api/user/current")
    return bool(data and (data.get("username") or data.get("userKey") or data.get("accountId")))


def _search_pages(page, cfg, space: str, product: str) -> tuple[list[dict], str]:
    """CQL 로 space 내 제목이 product 를 포함하는 페이지 검색. (결과, 오류메시지)."""
    cql = f'space={space} and title ~ "{product}"'
    url = _base(cfg) + "/rest/api/content/search?limit=15&cql=" + quote(cql)
    data, err = _get_json(page, url)
    if data is None:
        return [], err
    out = []
    for r in data.get("results", []):
        webui = (r.get("_links") or {}).get("webui", "")
        out.append({"id": r.get("id"), "title": r.get("title", ""), "webui": webui})
    return out, ""


def _pick(cands: list[dict], product: str) -> dict | None:
    """가장 적합한 후보 선택: 정확일치 > 접두일치 > 부분일치 > 첫번째."""
    p = product.strip().lower()
    for pred in (
        lambda t: t == p,
        lambda t: t.startswith(p),
        lambda t: p in t,
    ):
        for c in cands:
            if pred(c["title"].strip().lower()):
                return c
    return cands[0] if cands else None


def _android_in_title(title: str) -> str | None:
    """RN 제목에서 Android 버전을 뽑는다.

    예: 'PM95 (Android 13) Release Notes' → '13', 'PM90 A13 Release Note' → '13'.
    없으면 None.
    """
    m = re.search(r"android\s*\.?\s*(\d{1,2})", title, re.I)
    if m:
        return m.group(1)
    m = re.search(r"\bA\s*(\d{1,2})\b", title, re.I)  # 'A13' 축약형
    return m.group(1) if m else None


def pick_for_android(
    cands: list[dict], product: str, android_version: str | None
) -> tuple[dict | None, list[dict], bool]:
    """제품 + Android 버전으로 정확한 RN 후보를 고른다(동명 제품 혼동 방지).

    반환: (선택된 후보, 같은 Android 로 매칭된 후보들, 애매함 여부).
      - android_version 이 주어지면 제목의 Android 버전이 일치하는 후보만 남긴다.
      - 일치가 정확히 1개면 확정. 여러 개면 ambiguous=True(사람 확인 필요).
      - Android 일치가 없으면 제품명 기준 _pick 으로 폴백하되 ambiguous=True.
    """
    av = str(android_version or "").strip()
    if not av:
        return _pick(cands, product), cands, False

    matched = [c for c in cands if _android_in_title(c.get("title", "")) == av]
    if len(matched) == 1:
        return matched[0], matched, False
    if len(matched) > 1:
        # 여러 개면 제품명 정밀 매칭으로 한 번 더 좁히되, 확신 없으면 애매로 표시
        best = _pick(matched, product)
        return best, matched, True
    # Android 일치가 하나도 없다. 여기서 제품명만으로 아무 페이지나 고르면 **다른 Android
    # 페이지를 고친다** — 실측 2026-09-08: PM95 A15 작업이 'PM95 (Android 13) Release
    # Notes' 를 골라 검수까지 올라왔다. 후보들이 제목에 Android 를 달고 있는데 그중 맞는
    # 것이 없다는 건 그 Android 의 페이지가 아직 없다는 뜻이므로, 고르지 않고 비운다
    # (부르는 쪽이 새 페이지를 만든다).
    if any(_android_in_title(c.get("title", "")) for c in cands):
        return None, [], True
    # 제목에 Android 가 아예 없는 제품(PM3+ · PM5 · RF88 …) 은 예전대로 제품명으로 고른다.
    return _pick(cands, product), cands, True


def read_release_note(
    user_id: int,
    product: str,
    space: str = DEFAULT_SPACE,
    headful: bool = True,
    android_version: str | None = None,
    page_title: str = "",
) -> ReleaseNote:
    """제품(+Android)의 OS/FW Release Note 를 읽는다.

    page_title 을 주면 **제목으로 정확히 그 페이지**를 집는다(제품 등록에 적어 둔 KB 링크).
    검색·추정으로 고르지 않으므로 동명 제품·Android 혼동이 아예 없다.
    """
    cfg = get_web_config(user_id, TARGET)
    if cfg is None or not cfg.base_url:
        return ReleaseNote(
            product,
            error="'release_notes' 웹 편집 계정이 설정되지 않았습니다. "
            "[설정 → 설정 → 웹 편집 계정]에서 기본 URL/아이디/비밀번호를 등록하세요.",
        )

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, ctx = _new_context(p, user_id, TARGET, headless=not headful)
        try:
            page = ctx.new_page()

            # 1) 로그인 상태 확인 → 아니면 자동 로그인 후 세션 저장
            if not _logged_in(page, cfg):
                # 로그인 URL 을 항상 올바른 /login.action 으로 강제 (입력 실수 방지)
                cfg.login_url = _login_url(cfg)
                try:
                    _login(page, cfg)
                except Exception as e:  # noqa: BLE001
                    return ReleaseNote(
                        product,
                        error=f"자동 로그인 시도 실패: {e}. 로그인 URL/셀렉터 또는 아이디/비밀번호를 확인하세요.",
                    )
                ctx.storage_state(path=str(session_path(user_id, TARGET)))
                if not _logged_in(page, cfg):
                    return ReleaseNote(
                        product,
                        error="로그인했지만 인증 세션이 확인되지 않습니다. "
                        "아이디/비밀번호가 맞는지, 로그인 URL/셀렉터가 올바른지 확인하세요.",
                    )

            # 2) 페이지 찾기 — 등록된 제목이 있으면 그것으로, 없으면 제품명으로 검색.
            term = page_title or product
            cands, err = _search_pages(page, cfg, space, term)
            if err:
                return ReleaseNote(
                    product,
                    error=f"REST 검색 응답을 JSON 으로 받지 못했습니다({err}). "
                    "세션 인증이 풀렸거나 base URL 이 잘못되었을 수 있습니다.",
                )
            if not cands:
                return ReleaseNote(
                    product,
                    error=f"'{space}' 스페이스에서 제목에 '{term}' 를 포함하는 페이지를 찾지 못했습니다.",
                )
            if page_title:
                # 등록된 제목과 **정확히 같은** 페이지만 인정한다(추정하지 않는다).
                exact = [c for c in cands
                         if c.get("title", "").strip().lower() == page_title.strip().lower()]
                if not exact:
                    return ReleaseNote(
                        product,
                        android_version=str(android_version or ""),
                        error=f"제품에 등록된 Knowledge Base 페이지 '{page_title}' 를 "
                              f"'{space}' 스페이스에서 찾지 못했습니다. 등록 링크를 확인하세요.",
                        candidates=[{"title": c["title"], "url": _base(cfg) + c["webui"]}
                                    for c in cands],
                    )
                hit, matched, ambiguous = exact[0], exact, False
            else:
                # Android 버전이 주어지면 그것으로 정확한 RN 을 특정(동명 제품 혼동 방지)
                hit, matched, ambiguous = pick_for_android(cands, product, android_version)
            if hit is None:
                return ReleaseNote(
                    product,
                    android_version=str(android_version or ""),
                    error=f"'{product}' (Android {android_version}) 에 맞는 Release Note 를 찾지 못했습니다.",
                    candidates=[
                        {"title": c["title"], "url": _base(cfg) + c["webui"]} for c in cands
                    ],
                )
            url = _base(cfg) + hit["webui"]

            # 3) REST 로 storage(편집 원본) + view(미리보기) + version 한 번에 조회
            #    storage format 은 매크로(펼치기/표/목차/레이아웃)·첨부 이미지 참조를 보존한다.
            pid = hit["id"]
            data, err = _get_json(
                page,
                _base(cfg) + f"/rest/api/content/{pid}?expand=body.storage,body.view,version",
            )
            if data is None:
                return ReleaseNote(
                    product,
                    error=f"페이지 본문(storage) 조회 실패: {err}.",
                )
            body = data.get("body", {}) or {}
            storage = ((body.get("storage") or {}).get("value")) or ""
            view = ((body.get("view") or {}).get("value")) or ""
            version = ((data.get("version") or {}).get("number")) or 0

            # RN 에서 '가장 최근 OS 버전' 추출(= 이 작업의 새 OS 버전 결정에 사용)
            all_versions = extract_versions(storage)
            latest = latest_version(all_versions)

            return ReleaseNote(
                product=product,
                found=True,
                title=data.get("title") or hit["title"],
                url=url,
                page_id=str(pid),
                version=int(version),
                storage=storage,
                html=view,
                candidates=[
                    {"title": c["title"], "url": _base(cfg) + c["webui"]} for c in cands
                ],
                android_version=str(android_version or ""),
                ambiguous=ambiguous,
                latest_os_version=(latest or {}).get("version", ""),
                latest_os_date=(latest or {}).get("date", ""),
                os_versions=all_versions,
            )
        finally:
            browser.close()


# ── 버전 파싱 ─────────────────────────────────────────────────────────
_VER_RE = re.compile(r"(\d{1,3}\.\d{1,3}(?:\.\d{1,3})?)\s*(?:\(([^)]*)\))?")


def extract_versions(storage: str) -> list[dict]:
    """storage 의 <h2> 헤더에서 버전(예: 95.03)과 날짜를 추출한다.

    Release Note 페이지는 버전마다 `<h2><strong>95.03 (Jan 12 2025)</strong></h2>` 형식이라
    <h2> 안의 텍스트에서 `숫자.숫자 (날짜)` 패턴을 뽑는다. 문서 등장 순서대로 반환.
    """
    out: list[dict] = []
    for inner in re.findall(r"<h2\b[^>]*>(.*?)</h2>", storage, re.S | re.I):
        text = re.sub(r"<[^>]+>", " ", inner)
        text = text.replace("&nbsp;", " ").strip()
        m = _VER_RE.search(text)
        if m:
            out.append({"version": m.group(1), "date": (m.group(2) or "").strip()})
    return out


def _ver_key(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def latest_version(versions: list[dict]) -> dict | None:
    """버전 목록에서 숫자상 최신을 고른다."""
    if not versions:
        return None
    return max(versions, key=lambda d: _ver_key(d["version"]))


# ── 안전한 미리보기 (storage → view 변환, 저장 안 함) ────────────────
def _convert_to_view(page, cfg, storage: str, content_id: str) -> tuple[str, str]:
    """storage 를 Confluence 실제 렌더러로 view HTML 로 변환한다. **페이지를 저장하지 않는다.**

    Confluence 앞단(nginx)이 same-origin Referer/Origin 을 요구하고, POST 는 XSRF 보호가
    걸려 있어 `X-Atlassian-Token: no-check` + Referer/Origin 헤더가 모두 필요하다.
    반환: (view_html, error).
    """
    base = _base(cfg)
    url = (
        base
        + f"/rest/api/contentbody/convert/view?contentIdContext={content_id}"
        + f"&spaceKeyContext={DEFAULT_SPACE}"
    )
    resp = page.request.post(
        url,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Atlassian-Token": "no-check",
            "Origin": base,
            "Referer": base + "/",
        },
        data=json.dumps({"value": storage, "representation": "storage"}),
    )
    body = resp.text()
    if resp.status == 200 and body.strip().startswith("{"):
        try:
            return json.loads(body).get("value", ""), ""
        except Exception as e:  # noqa: BLE001
            return "", f"변환 JSON 파싱 실패: {e}"
    return "", f"변환 실패 status={resp.status}: {body[:200]}"


@dataclass
class EditPreview:
    product: str
    found: bool = False
    title: str = ""
    url: str = ""
    page_id: str = ""
    version: int = 0
    versions: list = field(default_factory=list)   # [{version, date}]
    latest: dict | None = None
    original_storage: str = ""
    edited_storage: str = ""
    changed: bool = False
    replaced_count: int = 0
    rendered_html: str = ""    # 편집본을 Confluence 로 렌더한 미리보기 (저장 안 함)
    candidates: list = field(default_factory=list)
    error: str = ""


# ── 로컬 전용 편집 (프로덕션 무접촉) ──────────────────────────────────
# 실제 Release Note 페이지는 읽기만, 편집본은 로컬 파일에만 저장한다. Confluence 에는
# 어떤 페이지도 만들지/고치지 않는다(스페이스 읽기 권한자에게 트리·검색으로 노출되므로 금지).
# 미리보기는 convert/view(상태 비저장: 아무 페이지도 만들지 않고 렌더 HTML 만 반환)로 렌더한다.
_LOCAL_FILE = SESSIONS_DIR / "rn_local.json"


def storage_to_view(
    user_id: int, content_id: str, storage: str, headful: bool = False
) -> tuple[str, str]:
    """임의의 storage 를 Confluence 실제 렌더러로 view HTML 로 변환(저장 안 함). (html, err).

    ⑥ 검수 미리보기용 — 편집한 storage 를 실제 페이지처럼 렌더해 보여준다.
    """
    if not (storage or "").strip():
        return "", "변환할 storage 가 비어 있습니다."
    cfg = get_web_config(user_id, TARGET)
    if cfg is None or not cfg.base_url:
        return "", "release_notes(Knowledge Base) 설정이 없습니다."

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, ctx = _new_context(p, user_id, TARGET, headless=not headful)
        try:
            page = ctx.new_page()
            if not _logged_in(page, cfg):
                cfg.login_url = _login_url(cfg)
                try:
                    _login(page, cfg)
                except Exception as e:  # noqa: BLE001
                    return "", f"자동 로그인 실패: {e}"
                ctx.storage_state(path=str(session_path(user_id, TARGET)))
            return _convert_to_view(page, cfg, storage, content_id or "")
        finally:
            browser.close()


# ── 프로덕션 쓰기 (영구 금지) ─────────────────────────────────────────
# 사용자 지시(2026-07-07): 읽기만 허용, 쓰기(PUT/POST/DELETE)는 영구 금지.
# 이 상수는 절대 True 로 바꾸지 말 것. 편집/저장은 전부 로컬에서만 한다.
_ALLOW_PROD_WRITES = False  # DO NOT CHANGE — 프로덕션 쓰기 영구 금지
_PROD_WRITE_BLOCKED = "프로덕션 쓰기는 영구 금지입니다. 편집/저장은 로컬 전용 기능을 사용하세요."


# ── 실제 저장(PUT) — 개발만 해둠. 테스트 UI 에서는 절대 호출하지 않는다 ──
def save_release_note(
    user_id: int,
    page_id: str,
    new_storage: str,
    expected_version: int,
    title: str,
    allow_write: bool = False,
) -> tuple[bool, str]:
    """페이지 본문을 새 storage 로 교체 저장한다(버전 +1, 낙관적 잠금).

    ⚠️ 실제 Confluence 페이지를 **변경**한다. ⑥ '검수 완료 = 실제 업로드' 전용으로,
    사용자 확인 후 allow_write=True 일 때만 실제 PUT 한다. optimistic lock 을 위해
    expected_version(직전에 읽은 버전)을 받아 version=expected_version+1 로 올린다.
    """
    if (allow_write or _ALLOW_PROD_WRITES) and _shadow.blocked(
            "confluence", "save_release_note", page_id=page_id, title=title, storage=new_storage):
        return True, ""
    if not (allow_write or _ALLOW_PROD_WRITES):
        return False, _PROD_WRITE_BLOCKED
    cfg = get_web_config(user_id, TARGET)
    if cfg is None or not cfg.base_url:
        return False, "release_notes 설정이 없습니다."

    from playwright.sync_api import sync_playwright

    payload = {
        "id": page_id,
        "type": "page",
        "title": title,
        "version": {"number": expected_version + 1},
        "body": {"storage": {"value": new_storage, "representation": "storage"}},
    }
    with sync_playwright() as p:
        browser, ctx = _new_context(p, user_id, TARGET, headless=True)
        try:
            page = ctx.new_page()
            if not _logged_in(page, cfg):
                cfg.login_url = _login_url(cfg)
                _login(page, cfg)
                ctx.storage_state(path=str(session_path(user_id, TARGET)))
            base = _base(cfg)
            resp = page.request.put(
                base + f"/rest/api/content/{page_id}",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Atlassian-Token": "no-check",
                    "Origin": base,
                    "Referer": base + "/",
                },
                data=json.dumps(payload),
            )
            if resp.status == 200:
                return True, "저장 완료"
            return False, f"저장 실패 status={resp.status}: {resp.text()[:200]}"
        finally:
            browser.close()


def read_page_raw(user_id: int, page_id: str) -> tuple[dict | None, str]:
    """페이지의 현재 version·title 을 읽는다. **읽기 전용.**

    이 함수가 없어서 save_real_release_note 가 NameError 로 죽고 있었다 — 즉 KB
    '검수 완료 = 실제 업로드' 가 한 번도 성공한 적이 없다(발견 2026-09-08). 낙관적 잠금은
    저장 직전의 version 을 알아야 성립하므로 저장 경로가 반드시 이걸 부른다.

    반환 {"version": int, "title": str, "storage": str} 또는 (None, 오류).
    """
    cfg = get_web_config(user_id, TARGET)
    if cfg is None or not cfg.base_url:
        return None, "release_notes 설정이 없습니다."
    if not page_id:
        return None, "페이지 ID가 없습니다."

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, ctx = _new_context(p, user_id, TARGET, headless=True)
        try:
            page = ctx.new_page()
            if not _logged_in(page, cfg):
                cfg.login_url = _login_url(cfg)
                _login(page, cfg)
                ctx.storage_state(path=str(session_path(user_id, TARGET)))
            data, err = _get_json(
                page,
                _base(cfg) + f"/rest/api/content/{page_id}?expand=body.storage,version",
            )
            if data is None:
                return None, err
            body = (data.get("body") or {}).get("storage") or {}
            return {
                "version": int(((data.get("version") or {}).get("number")) or 0),
                "title": data.get("title", ""),
                "storage": body.get("value") or "",
            }, ""
        finally:
            browser.close()


def save_real_release_note(
    user_id: int, page_id: str, new_storage: str, allow_write: bool = False,
) -> tuple[bool, str]:
    """⑥ '검수 완료 = 실제 업로드' — 페이지의 현재 버전/제목을 읽어 새 storage 로 교체 저장.

    낙관적 잠금(version+1)을 위해 저장 직전에 최신 version 을 다시 읽는다.
    allow_write=True 일 때만 실제 저장한다.
    """
    if not page_id:
        return False, "대상 페이지 ID가 없습니다."
    data, err = read_page_raw(user_id, str(page_id))
    if err or not data:
        return False, f"현재 페이지 정보 조회 실패: {err}"
    return save_release_note(
        user_id, str(page_id), new_storage,
        int(data.get("version") or 1), data.get("title", ""), allow_write=allow_write,
    )


# ══════════════════════════════════════════════════════════════════════
#  샌드박스: 실제 제품 페이지 대신 '전용 테스트 페이지'로만 저장(PUT)을 검증한다.
#  저장은 항상 저장된 샌드박스 page_id 로만 나가도록 가드한다.
# ══════════════════════════════════════════════════════════════════════
_SANDBOX_FILE = SESSIONS_DIR / "rn_sandbox.json"

# 샌드박스 최초 본문(매크로/표 포함 → 왕복 보존 검증용)


def _ensure_login(page, ctx, cfg, user_id) -> str | None:
    """로그인 보장. 실패 시 오류 메시지, 성공 시 None."""
    if _logged_in(page, cfg):
        return None
    cfg.login_url = _login_url(cfg)
    try:
        _login(page, cfg)
    except Exception as e:  # noqa: BLE001
        return f"자동 로그인 실패: {e}"
    ctx.storage_state(path=str(session_path(user_id, TARGET)))
    return None if _logged_in(page, cfg) else "로그인 후에도 인증 세션 확인 실패."
