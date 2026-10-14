"""Service Portal(serviceportal.pointmobile.com) Download 문서 - 읽기 + 샌드박스 저장 테스트.

AngularJS 1.x + Tomcat. 인증 = JSESSIONID 쿠키(로그인 폼 제출 후 유지).
API = 같은 도메인 상대경로 POST, 응답 봉투 {responseVO:{header:{status},body:{docs:[...]}}}.
문서 본문(Cont)은 Naver SmartEditor로 작성된 HTML.

샌드박스 안전 규칙:
  - 숨김([UseYN='N']) [SANDBOX] Download 항목 1개에만 insert/update 한다(실제 제품 문서는 읽기만).
  - 저장/삭제는 저장된 샌드박스 DownloadId 로만 나가도록 가드한다.
자격증명(ID/PW)은 서버가 복호화해 사용하며 화면/로그에 노출하지 않는다.
"""
from __future__ import annotations
from app import shadow as _shadow

import json
import re
from contextlib import contextmanager
from datetime import datetime

from app.agent.browser import SESSIONS_DIR, _new_context, session_path
from app.integrations.creds import get_web_config

TARGET = "service_portal"
DEFAULT_BASE = "https://serviceportal.pointmobile.com"
_HDR = {"Accept": "application/json", "Content-Type": "application/json"}
_SANDBOX_FILE = SESSIONS_DIR / "sp_sandbox.json"

# 샌드박스 최초 본문(간단한 표 포함). Naver 편집기 본문은 평범한 HTML.


def _base(cfg) -> str:
    return (cfg.base_url or DEFAULT_BASE).strip().split("#")[0].rstrip("/")


# ── 저수준 API 호출 ───────────────────────────────────────────────────
def _api(page, base: str, path: str, body: dict) -> tuple[dict | None, str]:
    """POST 후 {responseVO.body} 반환. (body, error). status!=200 이면 error."""
    r = page.request.post(base + path, headers=_HDR, data=json.dumps(body))
    t = r.text()
    try:
        j = json.loads(t)
    except Exception:  # noqa: BLE001
        return None, f"응답이 JSON 아님(status={r.status}). 세션 만료 가능."
    vo = j.get("responseVO") or {}
    if (vo.get("header") or {}).get("status") != 200:
        return None, f"API 오류 status={(vo.get('header') or {}).get('status')}"
    return vo.get("body") or {}, ""


def _logged_in(page, base: str) -> bool:
    _, err = _api(page, base, "/select/Download.getDownloadListCount/action.json", {})
    return err == ""


def _do_login(page, base: str, cfg) -> bool:
    """AngularJS 로그인 폼을 채워 제출(앱이 userGbn/Tenant 처리). 성공 시 JSESSIONID 쿠키 확보."""
    page.goto(base + "/#/main/login", wait_until="networkidle", timeout=30000)
    page.wait_for_selector('input[ng-model="vc.user.loginId"]', timeout=15000)
    page.fill('input[ng-model="vc.user.loginId"]', cfg.username)
    page.fill('input[ng-model="vc.user.loginPw"]', cfg.password)
    page.click('button[type="submit"]')
    page.wait_for_timeout(3500)
    return page.evaluate("() => window.sessionStorage.getItem('isLoggedIn')") == "Y"


def _ensure_login(page, ctx, base: str, cfg, user_id: int, force_form: bool = False) -> str | None:
    """로그인 보장. force_form=True 면 폼 로그인을 강제한다.

    SPA 화면(상세 페이지 등)은 라우트 가드가 sessionStorage 를 보는데, 저장된 세션(쿠키)만
    복원되면 sessionStorage 가 비어 화면 접근이 막힌다. 그래서 스크린샷 등 UI 조작 시엔
    폼 로그인을 강제해 sessionStorage 를 채운다. (순수 API 호출은 쿠키만으로 충분.)
    """
    if not force_form and _logged_in(page, base):
        return None
    if not cfg.username or not cfg.password:
        return "아이디/비밀번호가 설정되지 않았습니다. [설정 → 웹 편집 계정 → Service Portal]에서 등록하세요."
    if not _do_login(page, base, cfg):
        return "로그인 실패. 아이디/비밀번호를 확인하세요."
    ctx.storage_state(path=str(session_path(user_id, TARGET)))
    return None if _logged_in(page, base) else "로그인 후에도 세션이 확인되지 않습니다."


@contextmanager
def _session(user_id: int, headful: bool = False, ui_login: bool = False):
    """로그인된 (page, base) 를 제공. 실패 시 (None, None, error).

    ui_login=True 면 SPA 화면 접근을 위해 폼 로그인을 강제(sessionStorage 채움).
    """
    cfg = get_web_config(user_id, TARGET)
    if cfg is None or not cfg.base_url:
        yield None, None, "service_portal 설정이 없습니다. [설정]에서 등록하세요."
        return
    from playwright.sync_api import sync_playwright

    base = _base(cfg)
    with sync_playwright() as p:
        browser, ctx = _new_context(p, user_id, TARGET, headless=not headful)
        try:
            page = ctx.new_page()
            err = _ensure_login(page, ctx, base, cfg, user_id, force_form=ui_login)
            if err:
                yield None, None, err
            else:
                yield page, base, ""
        finally:
            browser.close()


# ── 읽기 (실제 문서) ──────────────────────────────────────────────────
def list_downloads(user_id: int, size: int = 500, headful: bool = False) -> tuple[list, str]:
    """전체 Download 문서 목록. size 로 한 번에 가져올 최대 건수 지정(기본 500, 전량)."""
    with _session(user_id, headful) as (page, base, err):
        if err:
            return [], err
        body, e = _api(
            page, base, "/select/Download.getDownloadList/action.json",
            {"viewSize": size, "page": 1},
        )
        return (body.get("docs", []), "") if not e else ([], e)


def _detail(page, base, download_id: str):
    srch = {
        "DownloadId": str(download_id), "TenantId": "1", "TableId": str(download_id),
        "TableNm": "download", "IdName": "DownloadId",
    }
    body, e = _api(page, base, "/select/Download.getDownloadDetail/action.json", srch)
    if e:
        return None, e
    docs = body.get("docs", [])
    return (docs[0] if docs else None), ("" if docs else "문서를 찾지 못했습니다.")


def get_detail(user_id: int, download_id: str, headful: bool = False):
    with _session(user_id, headful) as (page, base, err):
        if err:
            return None, err
        return _detail(page, base, download_id)


# ── OS 버전 소스 = 제품별 Resources 문서 (신뢰) ───────────────────────
# 제목: "{제품} A{안드로이드} Resources (OS, SDK) & Document"
# Confluence RN 이 밀릴 수 있어, 현재 OS 버전은 이 문서에서 뽑는다(A13=95.06, A15=95.51 처럼 SKU별).
#: 버전 표기 — 제품 접두사와 리비전 표시를 **함께** 읽는다.
#    95.06 · 95.07C1 · TR54G15.04 · TR54G15.03 C1 · PM84PG15.03
#  예전에는 `\d+\.\d+` 만 봐서 'TR54G15.03 C1' 에서 숫자 '15.03' 만 집었다. 그래서
#  Incremental 행의 직전 버전이 'TR54G15.03 C1 (May 15 2026)' 대신 '15.03 (Apr 28 2026)'
#  으로 나갔다(실측 2026-09-09 TR54).
_VER_CORE = r"[A-Za-z][A-Za-z0-9+]*?(?=\d{1,3}\.)|"      # 접두사(있으면)
_VER_TXT = r"(?:[A-Za-z][A-Za-z0-9+]*)?\d{1,3}\.\d{1,3}(?:\s*[A-Za-z]\d{1,2})?"
_VER_PAREN_RE = re.compile(r"(" + _VER_TXT + r")\s*\(([^)]*)\)")
# 상단 현재 버전 마커: <b>95.51 (Mar 31 2026)</b> (가장 신뢰; OS Image 표는 stale 일 수 있음)
_BOLD_VER_RE = re.compile(r"<b>\s*(" + _VER_TXT + r")\s*\(([^)]*)\)\s*</b>", re.I)
#: 정렬용 — 접두사를 떼고 숫자와 리비전만 본다. 'TR54G15.03 C1' → (15, 3, 'C', 1)
_VNUM_RE = re.compile(r"(\d{1,3})\.(\d{1,3})(?:\s*([A-Za-z])(\d{1,2}))?")


def _vkey(v: str):
    m = _VNUM_RE.search(str(v or ""))
    if not m:
        return (0, 0, "", 0)
    return (int(m.group(1)), int(m.group(2)),
            (m.group(3) or "").upper(), int(m.group(4) or 0))


def extract_os_version(cont: str) -> dict:
    """Resources 문서 Cont(HTML)에서 현재/직전 OS 버전을 추출.

    현재 버전 = 상단 `<b>버전 (날짜)</b>` 마커(가장 신뢰). 없으면 등장 버전 중 숫자상 최댓값.
    (주의: OS Image 표의 'X → Y' 는 다른 SKU 에서 복사돼 stale 할 수 있어 신뢰하지 않는다.)
    직전 버전 = 현재보다 낮은 버전 중 최고(best-effort; stale 표 영향 가능 → 경계는 Redmine 로 교차확인).
    반환: {current, current_date, prev, prev_date}
    """
    text = cont or ""
    vers = _VER_PAREN_RE.findall(text)
    if not vers:
        return {"current": "", "current_date": "", "prev": "", "prev_date": ""}

    bold = _BOLD_VER_RE.search(text)
    if bold:
        current, current_date = bold.group(1), bold.group(2).strip()
    else:
        cur = max(vers, key=lambda v: _vkey(v[0]))
        current, current_date = cur[0], cur[1].strip()

    ck = _vkey(current)
    lowers = sorted({(v, d.strip()) for v, d in vers if _vkey(v) < ck},
                    key=lambda v: _vkey(v[0]), reverse=True)
    prev, prev_date = (lowers[0] if lowers else ("", ""))
    return {"current": current, "current_date": current_date, "prev": prev, "prev_date": prev_date}


# 제목의 Android 표기: 'A13' · 'Android 13' 둘 다 쓰인다(PM80 Android 6 등).
_TITLE_ANDROID_RE = re.compile(r"\ba\s*(\d{1,2})\b|\bandroid\s*(\d{1,2})\b", re.I)


def title_android(title: str) -> str:
    """제목에서 Android 버전 표기를 뽑는다. 표기가 없으면 "" (= 제품 기본 Android 문서)."""
    m = _TITLE_ANDROID_RE.search(title or "")
    if not m:
        return ""
    return m.group(1) or m.group(2) or ""


def is_resources_title(title: str, product: str) -> bool:
    """그 제품의 Resources 문서인가(Android 표기는 보지 않는다).

    제목 앞머리에서 Android 표기를 걷어낸 나머지가 **제품명과 정확히 같아야** 한다.
    부분 포함으로 보면 'PM84' 가 'PM84P' 에, 'PM95' 가 'PM95 Dual SAM' 에 잘못 걸린다.
    """
    t = title or ""
    if "resources" not in t.lower():
        return False
    head = re.split(r"\bresources\b", t, flags=re.I)[0]
    # 'A13' · 'Android 13' · 표기만 있는 'Android' 를 모두 제거
    head = re.sub(r"\b(a\s*\d{1,2}|android(\s*\d{1,2})?)\b", "", head, flags=re.I)
    return _norm_model(head) == _norm_model(product)


# ── 신규 문서 생성(최초 출시) ─────────────────────────────────────────
# 새 제품은 Download 문서 자체가 없다. 편집 화면의 '추가' 는 같은 폼을 쓰고, 저장만
# DownloadId==0 일 때 다른 주소로 나간다(프런트 download-edit-ctrl.js 실측 2026-08-13):
#     등록: POST /api/insertDownloadDetail.json
#     수정: POST /update/Download.updateDownloadDetail/action.json
# 응답 docs[0].DownloadId 로 새 id 가 돌아온다.
_INSERT_PATH = "/api/insertDownloadDetail.json"
#: 제품 OS 문서 제목 관행 — 'PM95 A15 Resources (OS, SDK) & Document'
_RES_TITLE_RE = re.compile(r"resources\s*\(.*?\)\s*&\s*document", re.I)


def resources_title(product: str, android: str) -> str:
    """새 문서 제목 — 최근 등록분의 표기를 따른다(PM95 A15 · PM68 A15 · PM86 A13)."""
    a = "".join(c for c in str(android or "") if c.isdigit())
    return (f"{product} A{a} Resources (OS, SDK) & Document" if a
            else f"{product} Resources (OS, SDK) & Document")


def latest_resources_doc(user_id: int) -> tuple[dict | None, str]:
    """가장 최근에 손댄 **제품 Resources 문서**의 상세. 새 문서를 뜰 원본이다.

    'AS Partner material package' 같은 비제품 문서를 뜨면 안 되므로 제목으로 거른다.
    최신 판단은 목록의 EdtDate 를 쓴다(상세에는 날짜 필드가 없다).
    """
    with _session(user_id) as (page, base, err):
        if err:
            return None, err
        body, e = _api(page, base, "/select/Download.getDownloadList/action.json",
                       {"viewSize": 500, "page": 1})
        if e:
            return None, e
        docs = [d for d in (body.get("docs") or [])
                if _RES_TITLE_RE.search(str(d.get("Title") or ""))]
        if not docs:
            return None, "복사할 Resources 문서를 찾지 못했습니다."
        docs.sort(key=lambda d: str(d.get("EdtDate") or ""), reverse=True)
        det, e2 = _detail(page, base, str(docs[0].get("DownloadId")))
        if det is None:
            return None, f"원본 문서 조회 실패: {e2}"
        return det, ""


def create_doc(user_id: int, src_detail: dict, title: str, cont: str,
               allow_write: bool = False, headful: bool = False) -> tuple[str, str]:
    """원본 문서를 본떠 **새 Download 문서**를 만든다. (새 DownloadId, error).

    분류(Cate)·공개여부(UseYN)·권한 플래그는 원본 값을 그대로 물려받는다 — 새 제품도
    같은 분류·같은 공개 범위로 나가야 하고, 우리가 지어낼 값이 아니다.
    첨부 목록은 비운다(파일은 사람이 올린다). allow_write 없이는 아무것도 만들지 않는다.
    """
    if (allow_write or _ALLOW_PROD_WRITES) and _shadow.blocked(
            "service_portal", "create_doc", user_id=user_id, title=title, cont=cont):
        return "shadow", ""
    if not (allow_write or _ALLOW_PROD_WRITES):
        return "", _PROD_WRITE_BLOCKED
    if not (title or "").strip():
        return "", "새 문서 제목이 비었습니다."
    data = {k: v for k, v in (src_detail or {}).items()
            if k not in ("DownloadId", "TableId", "uploadFileList", "uploadFileList2")}
    data.update({
        "DownloadId": 0, "TableId": 0, "TableNm": "Download", "IdName": "TableId",
        "Title": title, "Cont": cont,
        "uploadFileList": [], "uploadFileList2": [],
    })
    with _session(user_id, headful) as (page, base, err):
        if err:
            return "", err
        body, e = _api(page, base, _INSERT_PATH, data)
        if e:
            return "", e
        docs = (body or {}).get("docs") or []
        new_id = str((docs[0] if docs else {}).get("DownloadId") or "").strip()
        if not new_id or new_id in ("0", "None"):
            return "", "생성 응답에 새 문서 id 가 없습니다."
        return new_id, ""


def _norm_model(s: str) -> str:
    """모델명 비교용 정규화 — 소문자 · 공백 제거 · '+'/'plus' → 'p'."""
    s = (s or "").strip().lower().replace(" ", "")
    return s.replace("plus", "p").replace("+", "p")


def select_resources_doc(
    docs: list[dict], product: str, android: str
) -> tuple[dict | None, str, bool]:
    """제품의 Resources 문서 후보 중 이번 작업에 맞는 것 하나를 고른다.

    **후보 개수를 먼저 본다** — 대부분의 제품은 Resources 문서가 하나뿐이라
    Android 를 따질 필요가 없다. 기본 Android 설정이 필요한 경우는
    '표기 없는 문서 + A{n} 문서가 함께 있는' 제품뿐이다.

    반환: (문서 | None, 판단 근거, 사람 확인 필요 여부)
    """
    cands = [d for d in docs if is_resources_title(d.get("Title", ""), product)]
    if not cands:
        return None, "Resources 문서를 찾지 못했습니다.", False
    if len(cands) == 1:
        # 후보가 하나뿐 — Android 표기가 있든 없든 이 문서가 답이다(설정 불필요).
        return cands[0], "제품의 Resources 문서가 하나뿐", False

    want = str(android or "").strip()
    exact = [d for d in cands if title_android(d.get("Title", "")) == want] if want else []
    if len(exact) == 1:
        return exact[0], f"제목의 A{want} 정확 일치", False
    if len(exact) > 1:
        return exact[0], f"A{want} 일치 문서가 여러 건 — 첫 번째 사용", True

    bare = [d for d in cands if not title_android(d.get("Title", ""))]
    if not bare:
        titles = ", ".join(d.get("Title", "") for d in cands)
        return None, f"A{want} 문서가 없습니다(있는 문서: {titles}).", False

    # 표기 없는 문서 = 제품 기본 Android 문서. 기본값을 알면 요청과 대조한다.
    from app.pipeline.product_registry import get_default_android

    default = get_default_android(product)
    if default and want and default != want:
        return None, f"기본 Android 는 A{default} 인데 A{want} 문서가 없습니다.", False
    if default and want:
        return bare[0], f"기본 Android(A{default}) 문서", False
    return bare[0], f"표기 없는 문서로 폴백 — 기본 Android 미확인(제품 {product})", True


def find_resources_doc(
    user_id: int, product: str, android: str, headful: bool = False
) -> tuple[dict | None, str]:
    """제품+Android 의 'Resources (OS, SDK) & Document' 문서를 찾아 상세(Cont 포함)를 반환.

    (detail dict, error). detail 에는 Title/Cont/DownloadId 포함.
    """
    docs, err = list_downloads(user_id, headful=headful)
    if err:
        return None, err
    d, why, needs_confirm = select_resources_doc(docs, product, android)
    if d is None:
        return None, f"{product} A{android} Resources 문서를 특정하지 못했습니다 — {why}"
    detail, e = get_detail(user_id, str(d.get("DownloadId")), headful=headful)
    if detail is None:
        return None, f"Resources 문서(#{d.get('DownloadId')}) 조회 실패: {e}"
    detail.setdefault("DownloadId", d.get("DownloadId"))
    detail["_match_why"] = why              # 어떤 근거로 이 문서를 골랐는지(로그·검수 표시용)
    detail["_needs_confirm"] = needs_confirm
    return detail, ""


def resolve_version_from_sp(
    user_id: int, product: str, android: str, headful: bool = False
) -> dict:
    """Resources 문서에서 현재/직전 OS 버전을 뽑아 반환(견고한 버전 소스).

    반환: {ok, current, current_date, prev, prev_date, doc_id, doc_title, error}
    """
    detail, err = find_resources_doc(user_id, product, android, headful=headful)
    if detail is None:
        return {"ok": False, "error": err}
    v = extract_os_version(detail.get("Cont", ""))
    return {
        "ok": bool(v.get("current")),
        "current": v.get("current", ""),
        "current_date": v.get("current_date", ""),
        "prev": v.get("prev", ""),
        "prev_date": v.get("prev_date", ""),
        "doc_id": str(detail.get("DownloadId", "")),
        "doc_title": detail.get("Title", ""),
        "error": "" if v.get("current") else "Resources 문서에서 OS 버전을 추출하지 못했습니다.",
    }


# ── 로컬 전용 편집 (프로덕션 무접촉) ──────────────────────────────────
# 실제 문서는 읽기만, 편집본은 로컬 파일에만 저장한다. 프로덕션 Service Portal DB 에는
# 어떤 행도 만들지/고치지/지우지 않는다(숨겨도 URL 로 직접 접근될 위험이 있어 금지).
_LOCAL_FILE = SESSIONS_DIR / "sp_local.json"


# ── 프로덕션 쓰기 ─────────────────────────────────────────────────────
# 기본 게이트는 False(안전). ⑤ '검수 완료 = 실제 업로드'는 사용자 확인 후
# allow_write=True 를 명시적으로 넘겨서만 실행한다(save_real_cont).
_ALLOW_PROD_WRITES = False
_PROD_WRITE_BLOCKED = "프로덕션 쓰기가 잠겨 있습니다(검수 팝업의 '검수 완료'로만 실행)."


def fmt_portal_date(d) -> str:
    """포털 표기용 날짜 — 'Jan 27 2026'(일자 앞 0 없음). d: date/datetime."""
    return f"{d:%b} {d.day} {d:%Y}"


# Resources 문서의 OS Image 표 행 라벨(1열)
_FULL_LABEL_RE = re.compile(r"full\s*update", re.I)
_DIFF_LABEL_RE = re.compile(r"incremental\s*update", re.I)


#: 문서에 섞여 있는 머리 요소 — <meta> 와 <style>. 본문 편집기가 건드리는 자리다.
_HEAD_RE = re.compile(r"<meta[^>]*>|<style[^>]*>.*?</style>", re.S | re.I)
_STYLE_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.S | re.I)


def keep_head(original: str, edited: str) -> str:
    """편집본이 잃어버린 <meta>·<style> 을 원본에서 되살린다.

    Service Portal 문서는 <style> 로 표 디자인을 정의해 둔다(#5L 의 .tg 계열, #4C 의
    .flashOS_table). 본문 편집기(SmartEditor2)는 이걸 두 가지로 망가뜨린다:

      ① 통째로 삼킨다        → 원본 위치를 알 수 없으니 맨 앞에 되붙인다.
      ② **여는/닫는 태그만 벗기고 CSS 본문은 문서에 남긴다** — 실측 2026-09-09 TR54.
         이때는 CSS 가 화면에 글자로 그대로 보이고(`.tg { border-collapse: collapse; }`),
         디자인은 디자인대로 깨진다. 남아 있는 그 자리에 **태그를 다시 씌워** 원래
         모양으로 되돌린다. 맨 앞에 사본을 붙이면 글자는 그대로 남는다.

    이미 성한 것은 건드리지 않고, 원본에 없던 것을 만들어 넣지도 않는다.
    """
    o, e = original or "", edited or ""
    if not o or not e:
        return e
    for h in _HEAD_RE.findall(o):
        if h in e:
            continue                      # 성하게 남아 있다
        m = _STYLE_RE.match(h)
        inner = (m.group(1) if m else "").strip("\n")
        # ② 태그만 벗겨진 경우 — 남은 CSS 를 찾아 그 자리에 태그를 되씌운다.
        if inner.strip() and inner in _STYLE_RE.sub("", e):
            e = e.replace(inner, h, 1)
            continue
        # ① 통째로 사라진 경우 — 맨 앞에 되붙인다.
        e = h + "\n" + e
    return e


def edit_resources_links(
    cont: str, *, version_full: str, release_date: str,
    full_url: str = "", diff_url: str = "", rn_url: str = "",
    sdk_url: str = "", sdk_version: str = "",
) -> tuple[str, list, list, str]:
    """Resources (OS, SDK) & Document 문서의 링크·버전 표기를 규칙으로 교체.

    고치는 곳(실문서 #51 PM95 A13 구조 기준):
      ① 상단 현재버전 마커  <b>95.06 (Jan 27 2026)</b>
         ※ extract_os_version() 이 읽는 값 = 다음 회차 버전 판별 기준. 반드시 갱신.
      ② OS Image 표 'Full Update' 행        — href + 3열 'Other versions →{새버전} ({날짜})'
      ③ OS Image 표 'Incremental Update' 행 — href + 3열 '{직전} ({직전날짜}) → {새버전} ({날짜})'
         직전 버전/날짜는 **문서에 현재 적혀 있는 값**을 그대로 밀어서 쓴다.
      ④ Release Note 링크(앵커 텍스트 'Release Note')  — href 만
      ⑤ SDK 링크(앵커 텍스트 'SDK') — href + 앵커 **바깥**의 ' - v3.41.08' 텍스트

    순수 함수(네트워크 없음). 반환: (new_cont, applied, failed, error).
    """
    applied: list[dict] = []
    failed: list[dict] = []
    if not (version_full and release_date):
        return cont, [], [{"why": "버전/날짜 없음", "old": "", "new": "",
                           "reason": "version_full·release_date 가 비어 있습니다."}], \
            "새 버전 또는 배포일이 비어 있어 Resources 편집을 건너뜁니다."

    # 직전 버전 = 편집 전 문서에 적힌 '현재 버전'(밀어내기)
    cur = extract_os_version(cont)
    prev_v, prev_d = cur.get("current", ""), cur.get("current_date", "")
    # version_full 이 이미 '(날짜)' 를 달고 있으면 또 붙이지 않는다 — 예전에는
    # 'TR54G15.04 (Aug 22 2026) (Aug 28 2026)' 처럼 두 번 찍혔다(실측 2026-09-09).
    _vf = (version_full or "").strip()
    new_label = _vf if _vf.endswith(")") and "(" in _vf else f"{_vf} ({release_date})"

    def _text(html: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()

    out = cont

    # ① 상단 굵은 현재버전 마커
    m = _BOLD_VER_RE.search(out)
    if m:
        out = out[:m.start()] + f"<b>{new_label}</b>" + out[m.end():]
        applied.append({"why": "현재버전 마커 교체(다음 회차 버전 판별 기준)",
                        "old": _text(m.group(0)), "new": new_label})
    else:
        failed.append({"why": "현재버전 마커 못 찾음", "old": "", "new": "",
                       "reason": "<b>버전 (날짜)</b> 패턴이 없습니다."})

    # ②③ OS Image 표 두 행
    for label_re, url, kind in ((_FULL_LABEL_RE, full_url, "Full"),
                                (_DIFF_LABEL_RE, diff_url, "Incremental")):
        if not url:
            failed.append({"why": f"{kind} Update 링크 없음", "old": "", "new": "",
                           "reason": "Dropbox 업로드 결과에 해당 zip 이 없습니다."})
            continue
        hit = None
        for mtr in re.finditer(r"<tr\b[^>]*>(.*?)</tr>", out, re.S | re.I):
            tds = list(re.finditer(r"<td\b[^>]*>(.*?)</td>", mtr.group(1), re.S | re.I))
            if len(tds) >= 3 and label_re.search(_text(tds[0].group(1))):
                hit = (mtr, tds)
                break
        if hit is None:
            failed.append({"why": f"{kind} Update 행 못 찾음", "old": "", "new": "",
                           "reason": "OS Image 표에서 해당 행을 찾지 못했습니다."})
            continue
        mtr, tds = hit
        tr = mtr.group(1)
        dl_td, ver_td = tds[1], tds[2]
        new_dl = re.sub(r'href=["\'][^"\']*["\']', lambda _m: f'href="{url}"',
                        dl_td.group(1), count=1)
        if kind == "Full":
            new_ver = f"Other versions →{new_label}"
        else:
            new_ver = (f"{prev_v} ({prev_d}) → {new_label}" if prev_v else new_label)
        old_ver_text = _text(ver_td.group(1))
        # tr 안에서 뒤(버전열)부터 앞(다운로드열)으로 치환해 오프셋을 보존한다.
        repls = sorted([(dl_td.start(1), dl_td.end(1), new_dl),
                        (ver_td.start(1), ver_td.end(1), new_ver)], reverse=True)
        new_tr = tr
        for s, e, val in repls:
            new_tr = new_tr[:s] + val + new_tr[e:]
        out = out[:mtr.start(1)] + new_tr + out[mtr.end(1):]
        applied.append({"why": f"{kind} Update 링크 교체", "old": "(기존 dropbox 링크)", "new": url})
        applied.append({"why": f"{kind} Update 버전 표기 교체",
                        "old": old_ver_text, "new": new_ver})

    # ④ Release Note — 앵커 텍스트로 찾아 href 만 교체
    if rn_url:
        m = re.search(r'<a\b([^>]*)>(\s*Release\s*Note\s*)</a>', out, re.I)
        if m and "href=" in m.group(1):
            attrs = re.sub(r'href=["\'][^"\']*["\']', f'href="{rn_url}"', m.group(1), count=1)
            out = out[:m.start()] + f"<a{attrs}>{m.group(2)}</a>" + out[m.end():]
            applied.append({"why": "Release Note 링크 교체",
                            "old": "(기존 dropbox 링크)", "new": rn_url})
        else:
            failed.append({"why": "Release Note 링크 못 찾음", "old": "", "new": "",
                           "reason": "앵커 텍스트 'Release Note' 를 찾지 못했습니다."})

    # ⑤ SDK — href + 앵커 바깥의 ' - v3.41.08'
    if sdk_url:
        m = re.search(r'<a\b([^>]*)>(\s*SDK\s*)</a>(\s*-\s*v?[\d.]+)?', out, re.I)
        if m and "href=" in m.group(1):
            attrs = re.sub(r'href=["\'][^"\']*["\']', f'href="{sdk_url}"', m.group(1), count=1)
            tail = m.group(3) or ""
            if sdk_version:
                v = sdk_version if sdk_version.lower().startswith("v") else f"v{sdk_version}"
                tail = f" - {v}"
            out = out[:m.start()] + f"<a{attrs}>{m.group(2)}</a>{tail}" + out[m.end():]
            applied.append({"why": "SDK 링크 교체", "old": "(기존 dropbox 링크)", "new": sdk_url})
            if sdk_version:
                applied.append({"why": "SDK 버전 표기 교체",
                                "old": (m.group(3) or "").strip(), "new": tail.strip()})
        else:
            failed.append({"why": "SDK 링크 못 찾음", "old": "", "new": "",
                           "reason": "앵커 텍스트 'SDK' 를 찾지 못했습니다."})

    err = "" if applied else "Resources 문서에서 교체할 위치를 찾지 못했습니다."
    return out, applied, failed, err


def edit_flash_row(cont: str, product: str, android: str, version_full: str,
                   url: str, size_bytes: int) -> tuple[str, list, list, str]:
    """4C(플래싱 다운로드) 표에서 Model+Android 행을 찾아 3곳 교체.

    표: Model | Android Version | OS Version | Download(<a href>Download (X.XXGB)</a>).
    일부 행은 Model 이 rowspan(셀 3개)이라 상위 Model 을 이어받아 매칭한다.
    반환: (new_cont, applied[list], failed[list], error). 순수 함수(네트워크 없음).
    """
    size_gb = f"{size_bytes / (1024 ** 3):.2f}GB" if size_bytes else ""
    dl_text = f"Download ({size_gb})" if size_gb else "Download"
    android_want = f"android {android}".strip().lower()
    product_l = (product or "").strip().lower()

    def _text(html: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()

    current_model = ""
    for mtr in re.finditer(r"<tr\b[^>]*>(.*?)</tr>", cont, re.S | re.I):
        tr = mtr.group(1)
        tds = list(re.finditer(r"<td\b[^>]*>(.*?)</td>", tr, re.S | re.I))
        if not tds:
            continue
        texts = [_text(td.group(1)) for td in tds]
        if len(tds) >= 4:
            current_model = texts[0]
            a_idx, os_idx, dl_idx = 1, 2, 3
        elif len(tds) == 3:
            a_idx, os_idx, dl_idx = 0, 1, 2  # rowspan Model 이어받음
        else:
            continue
        if current_model.strip().lower() != product_l:
            continue
        if texts[a_idx].strip().lower() != android_want:
            continue

        # 매칭 행 — OS Version 셀 inner 교체 + Download 셀 href/텍스트 교체
        os_td, dl_td = tds[os_idx], tds[dl_idx]
        dl_inner = dl_td.group(1)
        new_dl = re.sub(r'href=["\'][^"\']*["\']', lambda _m: f'href="{url}"', dl_inner, count=1)
        new_dl = re.sub(r'(<a\b[^>]*>)(.*?)(</a>)',
                        lambda _m: _m.group(1) + dl_text + _m.group(3),
                        new_dl, count=1, flags=re.S | re.I)
        # tr 내에서 뒤(dl)부터 앞(os) 순으로 치환(오프셋 보존)
        repls = sorted([(os_td.start(1), os_td.end(1), version_full),
                        (dl_td.start(1), dl_td.end(1), new_dl)], reverse=True)
        new_tr = tr
        for s, e, val in repls:
            new_tr = new_tr[:s] + val + new_tr[e:]
        new_cont = cont[:mtr.start(1)] + new_tr + cont[mtr.end(1):]
        applied = [
            {"why": "OS Version 교체", "old": texts[os_idx], "new": version_full},
            {"why": "다운로드 링크 교체", "old": "(기존 dropbox 링크)", "new": url},
            {"why": "링크 텍스트 교체", "old": texts[dl_idx], "new": dl_text},
        ]
        return new_cont, applied, [], ""

    return cont, [], [{"why": "행 못 찾음", "old": "", "new": "",
                       "reason": f"{product} + Android {android} 행을 4C 표에서 찾지 못함"}], \
        f"{product} Android {android} 행을 4C 표에서 찾지 못했습니다."


def save_real_cont(
    user_id: int, download_id: str, new_cont: str,
    allow_write: bool = False, headful: bool = False,
) -> tuple[bool, str]:
    """실제 Service Portal Download 문서(download_id)의 본문(Cont)을 새 내용으로 교체 저장.

    ⑤ '검수 완료 = 실제 업로드' 전용. allow_write=True 일 때만 실제 POST 한다.
    기존 문서를 읽어 Cont 만 바꿔 update 엔드포인트로 저장(다른 필드/파일 목록 보존).
    """
    if (allow_write or _ALLOW_PROD_WRITES) and _shadow.blocked(
            "service_portal", "save_cont", user_id=user_id, download_id=download_id, cont=new_cont):
        return True, ""
    if not (allow_write or _ALLOW_PROD_WRITES):
        return False, _PROD_WRITE_BLOCKED
    sid = str(download_id)
    if not sid or sid in ("0", "None"):
        return False, "대상 문서 ID가 유효하지 않습니다."
    with _session(user_id, headful) as (page, base, err):
        if err:
            return False, err
        d, e = _detail(page, base, sid)
        if d is None:
            return False, f"원본 문서(#{sid}) 조회 실패: {e}"
        d["Cont"] = new_cont
        d["TableId"] = sid
        d["TableNm"] = "Download"
        d["IdName"] = "TableId"
        d["uploadFileList"] = d.get("uploadFileList") or []
        d["uploadFileList2"] = d.get("uploadFileList2") or []
        _, e = _api(page, base, "/update/Download.updateDownloadDetail/action.json", d)
        return (False, e) if e else (True, f"Service Portal #{sid} 원본을 교체 저장했습니다.")
