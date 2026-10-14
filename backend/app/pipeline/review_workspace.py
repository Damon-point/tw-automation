"""포털 편집 검수 작업공간 — 여러 문서(Service Portal 문서 + OS/FW Release Notes 페이지)를
한 곳에 담아 사람이 로컬 에디터로 검수하게 한다. **로컬 전용, 프로덕션 무접촉.**

메일(작업) 구동 ④ 오케스트레이터가 각 대상 문서를 읽어(읽기 전용) Claude 편집 제안을 적용한 뒤
여기에 항목으로 넣는다. 검수 페이지(/portaledit)가 항목별로 알맞은 에디터(SE2/TinyMCE)를 띄운다.

항목(item) 구조:
  key           : "service_portal:51" / "release_notes:139100390" (source:doc_id)
  source        : "service_portal" | "release_notes"
  doc_id        : 원본 문서 ID(읽기용, 저장 안 함)
  title         : 문서 제목
  editor        : "se2"(Service Portal) | "tinymce"(Release Notes)
  original_html : 원본(Cont 또는 view HTML) — 읽기 전용 참고
  edited_html   : Claude 제안(+사람 검수 후 저장) 편집본
  applied/failed: Claude 치환 적용/미적용 목록
  error         : Claude 편집 오류(있으면)
  product/version, updated_at
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.atomic_io import atomic_write_text, file_lock

_FILE = Path(__file__).resolve().parent.parent.parent / "work" / "portal_review.json"


def _load() -> dict:
    if _FILE.exists():
        try:
            return json.loads(_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {"items": {}}
    return {"items": {}}


def _save(data: dict) -> None:
    atomic_write_text(_FILE, json.dumps(data, ensure_ascii=False, indent=2))


def clear() -> None:
    _save({"items": {}})


def clear_source(source: str, mail_id=None) -> None:
    """특정 OS Update(mail_id)의 특정 source 항목만 제거.

    mail_id 를 주면 그 OS Update 의 해당 source 만 지운다(다른 OS Update 검수는 보존).
    mail_id 가 None 이면 해당 source 전체를 지운다(레거시).
    """
    def _keep(v: dict) -> bool:
        if v.get("source") != source:
            return True
        if mail_id is None:
            return False
        return str(v.get("mail_id", "")) != str(mail_id)

    with file_lock(_FILE):
        data = _load()
        data["items"] = {k: v for k, v in data.get("items", {}).items() if _keep(v)}
        _save(data)


def put(item: dict) -> None:
    """항목을 저장(키 기준 덮어쓰기).

    락: 두 사람이 다른 문서를 동시에 저장해도 같은 파일이라 서로를 덮어쓴다.
    """
    with file_lock(_FILE):
        data = _load()
        item = dict(item)
        item.setdefault("updated_at", datetime.now().isoformat(timespec="seconds"))
        data["items"][item["key"]] = item
        _save(data)


def list_items(mail_id=None) -> list[dict]:
    """저장된 항목 목록(source, title 순 정렬). mail_id 를 주면 그 OS Update 것만."""
    items = list(_load().get("items", {}).values())
    if mail_id is not None:
        items = [it for it in items if str(it.get("mail_id", "")) == str(mail_id)]
    items.sort(key=lambda x: (x.get("source", ""), x.get("title", "")))
    return items


def others_on_doc(item: dict) -> list[dict]:
    """같은 원본 문서를 겨냥한 **다른 작업**의 검수 항목들.

    항목 열쇠가 `작업:source:doc_id` 라 작업마다 사본이 따로 산다. 그런데 업로드는 원본
    본문을 통째로 교체하므로, 같은 페이지를 겨냥한 사본이 둘이면 **나중에 올린 쪽이 먼저
    올린 블록을 지운다** (실측 2026-09-08: job-18·job-21 이 둘 다 PM95 A13 페이지를
    겨냥했다). 부르는 쪽에서 막거나 알리는 데 쓴다.
    """
    did = str(item.get("doc_id") or "")
    if not did:
        return []
    mid = str(item.get("mail_id", ""))
    return [it for it in _load().get("items", {}).values()
            if str(it.get("doc_id") or "") == did
            and it.get("source") == item.get("source")
            and str(it.get("mail_id", "")) != mid]


def list_os_updates() -> list[dict]:
    """검수 항목이 있는 OS Update(mail_id) 목록 — 검수 페이지 선택용.

    반환 [{mail_id, product, version, count, updated_at}] (최신 갱신순).
    """
    groups: dict[str, dict] = {}
    for it in _load().get("items", {}).values():
        mid = it.get("mail_id")
        if mid is None:
            continue
        g = groups.setdefault(str(mid), {
            "mail_id": mid, "product": "", "version": "", "count": 0, "updated_at": "",
        })
        g["count"] += 1
        if not g["product"] and it.get("product"):
            g["product"] = it["product"]
        if not g["version"] and it.get("version"):
            g["version"] = it["version"]
        ua = it.get("updated_at", "")
        if ua > g["updated_at"]:
            g["updated_at"] = ua
    return sorted(groups.values(), key=lambda x: x["updated_at"], reverse=True)


def get_item(key: str) -> dict | None:
    return _load().get("items", {}).get(key)


def update_edited(key: str, edited_html: str) -> bool:
    """사람이 에디터에서 검수·수정한 편집본을 저장."""
    data = _load()
    it = data.get("items", {}).get(key)
    if not it:
        return False
    # 본문 편집기(SmartEditor2)는 <meta>·<style> 을 삼킨다. 원본에 있던 것을 되살려
    # 둔다 — 그러지 않으면 표 디자인을 정의하던 CSS 가 사라진다(service_portal.keep_head).
    from app.integrations.service_portal import keep_head

    it["edited_html"] = keep_head(it.get("original_html") or "", edited_html)
    it["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _save(data)
    return True
