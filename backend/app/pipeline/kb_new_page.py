"""⑥ Knowledge Base — 아직 페이지가 없는 제품(최초 출시)의 Release Note 페이지를 만든다.

새 제품은 Confluence 에 Release Notes 페이지 자체가 없다. 뼈대를 손으로 짜는 대신
**가장 최근에 손댄 RN 페이지를 복사**해 만든다(사용자 확정 2026-08-13).

  · 버전 블록(single 레이아웃 섹션)은 **전부 걷어낸다** — 남의 제품 이력이 딸려 오면 안 된다
  · 상단 제품 이미지는 **비워 둔다** — 사람이 나중에 넣는다(제품 사진이 아직 없다)
  · 목차(toc 매크로)는 그대로 둔다 — Confluence 가 알아서 채운다

읽기·쓰기 모두 REST v1(Basic 인증)으로 한다. 이 서버는 REST 로 조회·생성이 다 되므로
브라우저 세션(Playwright)을 띄울 이유가 없다. 응답이 느려(실측 TCP 연결만 4.4초) 재시도를 둔다.
"""
from __future__ import annotations
from app import shadow as _shadow

import base64
import json
import re
import time
import urllib.parse
import urllib.request
import uuid

TARGET = "release_notes"
DEFAULT_SPACE = "RN"

#: 버전 블록 = 'single' 레이아웃 섹션. 뼈대에서는 이걸 전부 지운다.
_SINGLE_SEC_RE = re.compile(
    r"<ac:layout-section\s+ac:type=\"single\">.*?</ac:layout-section>", re.I | re.S)
#: 상단 제품 이미지 — 태그만 지우고 자리(문단)는 남긴다.
_IMAGE_RE = re.compile(r"<ac:image\b.*?</ac:image>|<ac:image\b[^>]*/>", re.I | re.S)
#: 복사해 온 매크로 id — 문서마다 새로 발급한다.
_MACRO_ID_RE = re.compile(r'ac:macro-id="[^"]*"', re.I)


def _cfg(user_id: int):
    from app.integrations.creds import get_web_config

    return get_web_config(user_id, TARGET)


def _conn(user_id: int) -> tuple[str, dict, str]:
    """(base_url, 요청 헤더, error)."""
    cfg = _cfg(user_id)
    if cfg is None:
        return "", {}, "Knowledge Base 자격증명이 없습니다([설정 → 웹 자격증명])."
    from app.integrations.confluence_reader import _base

    tok = base64.b64encode(f"{cfg.username}:{cfg.password}".encode()).decode()
    return _base(cfg), {"Authorization": "Basic " + tok,
                        "Content-Type": "application/json"}, ""


def _open(req, timeout: int = 40, tries: int = 3):
    """이 Confluence 는 느리고 가끔 끊긴다 — 그대로 두면 어디까지 갔는지 알 수 없다."""
    last = None
    for i in range(tries):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            last = e
            if i < tries - 1:
                time.sleep(3 * (i + 1))
    raise last


def _get(user_id: int, path: str, params: dict) -> tuple[dict | None, str]:
    base, hdr, err = _conn(user_id)
    if err:
        return None, err
    url = f"{base}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url)
    for k, v in hdr.items():
        req.add_header(k, v)
    try:
        with _open(req) as r:
            return json.load(r), ""
    except Exception as e:  # noqa: BLE001
        return None, f"Confluence 조회 실패: {e}"


def find_page(user_id: int, title: str, space: str = DEFAULT_SPACE) -> tuple[dict | None, str]:
    """제목이 정확히 일치하는 페이지. 없으면 (None, "")."""
    d, err = _get(user_id, "/rest/api/content",
                  {"title": title, "spaceKey": space, "limit": "1",
                   "expand": "body.storage,version"})
    if err:
        return None, err
    res = (d or {}).get("results") or []
    return (res[0] if res else None), ""


def latest_rn_page(user_id: int, space: str = DEFAULT_SPACE) -> tuple[dict | None, str]:
    """그 스페이스에서 **가장 최근에 수정된** Release Notes 페이지 하나.

    뼈대를 뜰 원본이다. 제목에 'Release Note' 가 들어간 것만 고른다 — 스페이스에 회의록 같은
    다른 문서가 섞여 있어도 엉뚱한 뼈대를 뜨지 않게.
    """
    cql = f'space="{space}" and type=page and title~"Release Notes" order by lastmodified desc'
    d, err = _get(user_id, "/rest/api/content/search",
                  {"cql": cql, "limit": "5", "expand": "body.storage,version"})
    if err:
        return None, err
    for it in (d or {}).get("results") or []:
        if "release note" in (it.get("title") or "").lower():
            return it, ""
    return None, f"'{space}' 스페이스에서 복사할 Release Notes 페이지를 찾지 못했습니다."


def page_title(product: str, android: str) -> str:
    """등록 관행에 맞춘 페이지 제목 — 'PM84P (Android 15) Release Notes'."""
    a = str(android or "").strip()
    return f"{product} (Android {a}) Release Notes" if a else f"{product} Release Notes"


def build_skeleton(storage: str) -> tuple[str, dict]:
    """원본 페이지 storage → 새 페이지 뼈대. (뼈대, 무엇을 뺐는지).

    버전 블록을 전부 지우고 이미지 태그를 걷어낸다. 머리 레이아웃(이미지 자리 + 목차)은
    구조를 그대로 두므로, 나중에 사람이 이미지만 끼워 넣으면 된다.
    """
    s = storage or ""
    n_sec = len(_SINGLE_SEC_RE.findall(s))
    s = _SINGLE_SEC_RE.sub("", s)
    n_img = len(_IMAGE_RE.findall(s))
    s = _IMAGE_RE.sub("", s)
    # 매크로 id 는 원본에서 그대로 딸려 온다 — 문서마다 새로 발급해 겹치지 않게 한다.
    n_mid = 0

    def _new_mid(m):
        nonlocal n_mid
        n_mid += 1
        return f'ac:macro-id="{uuid.uuid4()}"'

    s = _MACRO_ID_RE.sub(_new_mid, s)
    return s, {"버전블록 제거": n_sec, "이미지 제거": n_img,
               "목차 유지": s.count('ac:name="toc"'), "매크로id 재발급": n_mid}


def create_page(user_id: int, title: str, storage: str, space: str = DEFAULT_SPACE,
                allow_write: bool = False) -> tuple[str, str, str]:
    """새 페이지 생성. (page_id, url, error).

    allow_write=False 면 **아무것도 만들지 않는다** — ⑥의 미리보기 단계에서 실수로
    페이지가 생기지 않도록, 실제 생성은 검수 완료(업로드) 때만 하도록 게이트를 둔다.
    """
    if allow_write and _shadow.blocked("confluence", "create_page", title=title, space=space, storage=storage):
        return "shadow", "", ""
    if not allow_write:
        return "", "", "생성 게이트가 닫혀 있습니다(allow_write=False)."
    base, hdr, err = _conn(user_id)
    if err:
        return "", "", err
    exist, e2 = find_page(user_id, title, space)
    if e2:
        return "", "", e2
    if exist:
        return "", "", f"같은 제목의 페이지가 이미 있습니다(#{exist.get('id')}) — 생성하지 않았습니다."
    body = {
        "type": "page", "title": title, "space": {"key": space},
        "body": {"storage": {"value": storage, "representation": "storage"}},
    }
    req = urllib.request.Request(f"{base}/rest/api/content", method="POST",
                                 data=json.dumps(body).encode("utf-8"))
    for k, v in hdr.items():
        req.add_header(k, v)
    try:
        with _open(req, 60) as r:
            d = json.load(r)
    except Exception as e:  # noqa: BLE001
        return "", "", f"페이지 생성 실패: {e}"
    pid = str(d.get("id") or "")
    url = base + ((d.get("_links") or {}).get("webui") or "")
    return pid, url, ""
