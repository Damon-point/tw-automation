"""SB 초안 → Confluence Solution Buffer 스페이스 페이지.

여기가 이 기능에서 **유일하게 바깥에 쓰는 곳**이다. 그래서 규약을 둔다.
  · 쓰기는 사람이 화면에서 [업로드]를 눌렀을 때만 일어난다. 주기 작업은 절대 부르지 않는다.
  · 올리기 전에 storage format 을 Confluence 변환 API 로 검증한다(문법이 어긋나면
    저장이 통째로 실패하는데, 실패한 뒤에 알면 늦다). 변환 API 는 아무것도 만들지 않는다.
  · 같은 제목이 이미 있으면 만들지 않고 사람에게 되돌린다. Confluence 는 스페이스 안에서
    제목이 유일해야 하고, 억지로 이름을 바꿔 올리면 나중에 아무도 못 찾는다.

관측한 SB 스페이스 규칙(600건 조사):
  · 576건이 'Solution Buffer'(id 12779716, 스페이스 홈) 바로 아래에 있다 → 부모 고정
  · 라벨 = 공개 등급. confidential+sb 333건 / partner+posted+sb 174건 /
    posted+sb 86건 / ascp+posted+sb 6건
  · **'posted' 는 내부(confidential) 문서에는 하나도 없다.** 외부에 실제로 게시한
    문서에만 붙는 라벨이라, 기본값을 등급에 따라 다르게 준다.
  · Troubleshooting 문서는 제목이 '[TR] ' 로 시작(12건)
"""
from __future__ import annotations
from app import shadow as _shadow

import json
import logging
import os
import re
from urllib.parse import quote, urlparse

import requests

from app.integrations.creds import get_web_config
from app.issuetrack.sb import TEMPLATES, PARTNER_CHOICES

log = logging.getLogger(__name__)

TARGET = "release_notes"          # SB 와 RN 은 같은 Confluence 호스트
SPACE = os.getenv("TW_SB_SPACE", "SB")
#: 'Solution Buffer' 페이지. 새 문서는 전부 이 아래에 만든다.
PARENT_ID = os.getenv("TW_SB_PARENT_ID", "12779716")

_TIMEOUT = (10, 60)
_UA = "TW-Automation/1.0 (solution buffer)"


class ConfluenceError(RuntimeError):
    pass


def _cfg():
    cfg = get_web_config(0, TARGET)
    if cfg is None or not cfg.base_url:
        raise ConfluenceError(
            "'release_notes' 웹 편집 계정이 없습니다. [설정 → 웹 편집 계정]에서 "
            "Confluence URL/아이디/비밀번호를 등록하세요.")
    return cfg


def _origin(raw: str) -> str:
    u = urlparse((raw or "").strip())
    if u.scheme and u.netloc:
        return f"{u.scheme}://{u.netloc}"
    return (raw or "").strip().split("#")[0].split("?")[0].rstrip("/")


class SBConfluence:
    def __init__(self) -> None:
        cfg = _cfg()
        self.base = _origin(cfg.base_url)
        self._s = requests.Session()
        self._s.auth = (cfg.username or "", cfg.password or "")
        self._s.headers.update({"Accept": "application/json", "User-Agent": _UA})

    # ---- 저수준 ----------------------------------------------------------
    def _req(self, method: str, path: str, **kw) -> dict:
        if (method.upper() in ("PUT", "DELETE") or (method.upper() == "POST" and "/contentbody/convert" not in path)) \
                and _shadow.blocked("sb_confluence", method.upper(), path=path, body=kw.get("json")):
            return {"id": "shadow", "version": {"number": 0}, "_links": {}}
        r = self._s.request(method, self.base + path, timeout=_TIMEOUT, **kw)
        if r.status_code in (200, 201):
            try:
                return r.json()
            except ValueError:
                return {}
        if r.status_code in (401, 403):
            raise ConfluenceError(
                f"Confluence 권한 없음({r.status_code}). 등록된 계정에 SB 스페이스 "
                "쓰기 권한이 있는지 확인하세요.")
        # Confluence 는 오류 사유를 message 에 담아 준다 — 그대로 보여 주는 게 낫다.
        msg = ""
        try:
            msg = (r.json() or {}).get("message") or ""
        except ValueError:
            msg = r.text[:200]
        raise ConfluenceError(f"HTTP {r.status_code} {msg}")

    # ---- 조회 -------------------------------------------------------------
    def find_by_title(self, title: str) -> dict | None:
        d = self._req("GET", "/rest/api/content", params={
            "spaceKey": SPACE, "title": title, "expand": "version"})
        rows = d.get("results") or []
        return rows[0] if rows else None

    def get_page(self, page_id: str) -> dict:
        return self._req("GET", f"/rest/api/content/{page_id}",
                         params={"expand": "version,space,metadata.labels"})

    def validate_storage(self, storage: str) -> str:
        """storage format 을 view 로 변환해 문법을 검증한다. 아무것도 만들지 않는다.

        반환: 변환된 view HTML(미리보기로 써도 된다). 문법 오류면 예외.
        """
        d = self._req("POST", "/rest/api/contentbody/convert/view",
                      json={"value": storage, "representation": "storage"})
        return (d or {}).get("value", "")

    def to_storage(self, html: str) -> str:
        """사람이 쓴 HTML → Confluence storage format.

        **직접 손으로 맞추지 않고 Confluence 에게 시킨다.** storage format 은 엄격한
        XHTML 이라 사람이 쓴 글은 거의 항상 어긋난다(홑따옴 &, <tbody> 없는 표,
        안 닫힌 <br>). 편집기가 쓰는 'editor' 표현식으로 넘기면 Confluence 가 자기
        규칙대로 정규화해 준다 — 실측: '그냥 & 이렇게' → '그냥 &amp; 이렇게',
        <table><tr> → <table><tbody><tr>.
        """
        if not (html or "").strip():
            return ""
        d = self._req("POST", "/rest/api/contentbody/convert/storage",
                      json={"value": html, "representation": "editor"})
        return (d or {}).get("value", "")

    # ---- 쓰기 -------------------------------------------------------------
    def create_page(self, *, title: str, storage: str, parent_id: str = "") -> dict:
        return self._req("POST", "/rest/api/content", json={
            "type": "page",
            "title": title,
            "space": {"key": SPACE},
            "ancestors": [{"id": int(parent_id or PARENT_ID)}],
            "body": {"storage": {"value": storage, "representation": "storage"}},
        })

    def update_page(self, page_id: str, *, title: str, storage: str) -> dict:
        cur = self.get_page(page_id)
        ver = int(((cur.get("version") or {}).get("number")) or 0)
        return self._req("PUT", f"/rest/api/content/{page_id}", json={
            "id": str(page_id),
            "type": "page",
            "title": title,
            "space": {"key": SPACE},
            "version": {"number": ver + 1,
                        "message": "TW Automation — Redmine 이슈 갱신 반영"},
            "body": {"storage": {"value": storage, "representation": "storage"}},
        })

    def set_labels(self, page_id: str, labels: list[str]) -> list[str]:
        """라벨을 붙인다(기존 라벨은 지우지 않는다 — 사람이 손으로 단 것을 없애면 안 된다)."""
        if not labels:
            return []
        self._req("POST", f"/rest/api/content/{page_id}/label",
                  json=[{"prefix": "global", "name": n} for n in labels])
        d = self._req("GET", f"/rest/api/content/{page_id}/label")
        return [x.get("name") for x in (d.get("results") or [])]

    def page_url(self, page_id: str, title: str = "") -> str:
        if title:
            return f"{self.base}/display/{SPACE}/{quote(title.replace(' ', '+'), safe='+%')}"
        return f"{self.base}/pages/viewpage.action?pageId={page_id}"


# --------------------------------------------------------- storage 조립 ---
def _esc(s: str) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _partner_rows(selected: list[str]) -> str:
    """파트너 템플릿의 체크박스. 고른 것만 complete 로 표시한다."""
    items = []
    for i, (key, label) in enumerate(PARTNER_CHOICES.items(), start=1):
        status = "complete" if key in (selected or []) else "incomplete"
        items.append(
            f"<ac:task><ac:task-id>{i}</ac:task-id>"
            f"<ac:task-status>{status}</ac:task-status>"
            f"<ac:task-body>{_esc(label)}</ac:task-body></ac:task>")
    return "<ac:task-list>" + "".join(items) + "</ac:task-list>"


def build_storage(doc, client: "SBConfluence | None" = None) -> str:
    """SBDoc → Confluence storage format 페이지 본문.

    템플릿 4종이 공통으로 갖는 details 매크로(Author / [Partner] / [Summary] /
    creation date) + '1) Applies to' + '2) Description' 구성을 그대로 따른다.
    내부(Internal) 템플릿에만 Summary 행이 없어서 등급에 따라 표를 다르게 만든다.

    client 를 주면 **본문만** Confluence 에 맡겨 storage format 으로 정규화한다.
    머리말(details 매크로)은 여기서 만든 storage 라 이미 정확하므로 통과시키지 않는다
    — 통째로 변환에 넘기면 ac: 매크로가 뭉개진다.
    """
    disclosure = doc.disclosure or "internal"
    author = _esc(doc.assignee_name or "")
    rows = [f"<tr><th>Author</th><td>{author}</td></tr>"]
    if disclosure == "partner":
        partners = json.loads(doc.partners_json or "[]")
        rows.append(f"<tr><th>Partner</th><td>{_partner_rows(partners)}</td></tr>")
    if disclosure != "internal":
        summary = _esc((doc.title or "").removeprefix("[TR] "))
        rows.append(f"<tr><th>Summary</th><td>{summary}</td></tr>")
    rows.append(
        "<tr><th>creation date</th><td><div class=\"content-wrapper\"><p>"
        "<ac:structured-macro ac:name=\"creationdate\" ac:schema-version=\"1\" />"
        "</p></div></td></tr>")

    details = (
        '<ac:structured-macro ac:name="details" ac:schema-version="1">'
        '<ac:rich-text-body><table class="wrapped">'
        "<colgroup><col /><col /></colgroup><tbody>"
        + "".join(rows) +
        "</tbody></table></ac:rich-text-body></ac:structured-macro>"
    )
    desc = doc.description_html or ""
    if client is not None:
        desc = client.to_storage(desc)
    body = (
        f"<p><strong>1) Applies to</strong> : {_esc(doc.applies_to or '')}</p>"
        "<p><strong>2) Description :</strong></p>"
        f"{desc}"
    )
    return details + body


def default_labels_for(disclosure: str | None) -> list[str]:
    """등급별 기본 라벨.

    'posted' 는 외부에 실제로 게시한 문서에만 붙어 있어(내부 문서 333건 중 0건)
    internal 이 아닐 때만 기본값으로 넣는다. 최종 선택은 화면에서 사람이 한다.
    """
    d = disclosure or "internal"
    labels = list(TEMPLATES.get(d, {}).get("labels", ["sb"]))
    if d != "internal" and "posted" not in labels:
        labels.append("posted")
    return labels


def default_labels(doc) -> list[str]:
    return default_labels_for(doc.disclosure)


_TITLE_BAD = re.compile(r'[\x00-\x1f]')


def normalize_title(title: str) -> str:
    t = _TITLE_BAD.sub(" ", title or "").strip()
    t = re.sub(r"\s{2,}", " ", t)
    return t[:250]          # Confluence 제목 상한(255)에 여유를 둔다


# ------------------------------------------------------------------ 게시 ---
def preview(doc_id: int) -> dict:
    """올리기 전 미리보기. storage 를 만들어 Confluence 변환 API 로 검증까지 한다.

    반환 {ok, storage, html, title, labels, exists, error}.
    exists 는 같은 제목의 페이지가 이미 있는지 — 있으면 업로드가 막힌다.
    """
    from app.issuetrack.db import issuetrack_session
    from app.issuetrack.models import SBDoc

    with issuetrack_session() as db:
        doc = db.get(SBDoc, doc_id)
        if doc is None:
            return {"ok": False, "error": "문서를 찾을 수 없습니다."}
        title = normalize_title(doc.title)
        labels = json.loads(doc.labels_json or "null") or default_labels(doc)
        page_id = doc.confluence_page_id

    out = {"ok": False, "storage": "", "html": "", "title": title,
           "labels": labels, "exists": None, "error": ""}
    try:
        c = SBConfluence()
        with issuetrack_session() as db:
            storage = build_storage(db.get(SBDoc, doc_id), client=c)
        out["storage"] = storage
        out["html"] = c.validate_storage(storage)
        found = c.find_by_title(title)
        # 이 문서가 이미 만든 페이지라면 '중복'이 아니라 '갱신 대상'이다.
        if found and str(found.get("id")) != str(page_id or ""):
            out["exists"] = {"id": found.get("id"), "title": found.get("title"),
                             "url": c.page_url(found.get("id"), found.get("title"))}
        out["ok"] = True
    except ConfluenceError as e:
        out["error"] = str(e)
    return out


def publish(doc_id: int, *, by: str = "") -> dict:
    """실제 업로드. **사람이 화면에서 눌렀을 때만** 호출한다.

    이미 페이지가 있으면(재해결로 만든 개정판) 새로 만들지 않고 그 페이지를 갱신한다.
    사용자가 "갱신이 가능하면 갱신, 안 되면 신규 작성 후 기존 폐기"로 정했고,
    Confluence 는 버전 올리기로 갱신이 되므로 갱신 쪽을 쓴다.
    """
    from datetime import datetime

    from app.issuetrack.db import issuetrack_session
    from app.issuetrack.models import SBDoc

    with issuetrack_session() as db:
        doc = db.get(SBDoc, doc_id)
        if doc is None:
            return {"ok": False, "error": "문서를 찾을 수 없습니다."}
        if doc.state == "uploaded":
            return {"ok": False, "error": "이미 업로드된 문서입니다."}
        if doc.state in ("backlog", "queued"):
            return {"ok": False, "error": "초안이 아직 생성되지 않았습니다."}
        title = normalize_title(doc.title)
        labels = json.loads(doc.labels_json or "null") or default_labels(doc)
        page_id = doc.confluence_page_id
        issue_id = doc.issue_id

    if not title:
        return {"ok": False, "error": "제목이 비어 있습니다."}

    try:
        c = SBConfluence()
        with issuetrack_session() as db:
            storage = build_storage(db.get(SBDoc, doc_id), client=c)
        c.validate_storage(storage)          # 문법 오류면 여기서 멈춘다(아무것도 안 만든다)

        if page_id:
            res = c.update_page(page_id, title=title, storage=storage)
            action = "updated"
        else:
            dup = c.find_by_title(title)
            if dup:
                return {"ok": False, "error":
                        f"같은 제목의 페이지가 이미 있습니다(id {dup.get('id')}). "
                        "제목을 고쳐서 다시 시도하세요.",
                        "existing_url": c.page_url(dup.get("id"), dup.get("title"))}
            res = c.create_page(title=title, storage=storage)
            action = "created"

        new_id = str(res.get("id") or page_id or "")
        applied = c.set_labels(new_id, labels)
        url = c.page_url(new_id, title)
    except ConfluenceError as e:
        log.warning("SB 업로드 실패(doc %s): %s", doc_id, e)
        return {"ok": False, "error": str(e)}

    with issuetrack_session() as db:
        doc = db.get(SBDoc, doc_id)
        doc.state = "uploaded"
        doc.confluence_page_id = new_id
        doc.confluence_url = url
        doc.labels_json = json.dumps(labels, ensure_ascii=False)
        doc.uploaded_at = datetime.utcnow()
        doc.decided_at = doc.decided_at or datetime.utcnow()
        doc.decided_by = by or doc.decided_by
        db.commit()

    log.info("SB 업로드 %s: #%s → %s (labels=%s)", action, issue_id, url, applied)
    return {"ok": True, "action": action, "page_id": new_id, "url": url,
            "labels": applied}
