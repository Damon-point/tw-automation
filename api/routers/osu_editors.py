"""
OS Update 편집기 · 작업 생애 — 8080 화면(app/pages/*)이 하던 일을 같은 순서로 옮긴 것.

  회차   : 시작(감지된 회차 / 버전 직접 추가) · 완료로 기록 · 다시 열기 · 삭제
  뉴스레터: 초안 열람 · 카드 편집 → 렌더·저장 · 발송 그룹(태그) · Mailchimp 발송
  Service Portal / Knowledge Base: 검수 편집본 열람 · 저장
  FOTA   : 등록할 OsInfo·DownloadPath 행 열람·수정 · 미리보기(dry-run) · 대조 · 실제 등록
  Dropbox: 구버전 그룹 열람 · 그룹 삭제

외부 쓰기는 app.shadow 가 마지막 함수에서 막는다(검증 모드).
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app import os_products as P
from app import os_update_jobs as J
from app.pipeline import history as H
from app.pipeline import newsletter_ws as NLWS
from app.pipeline import review_workspace as WS
from app.pipeline.state import get_state, update_state

from common import user as _user

router = APIRouter()

WORK = (Path(J.__file__).resolve().parent.parent / "work").resolve()


def _file_url(path: str) -> str:
    if not path:
        return ""
    try:
        Path(path).resolve().relative_to(WORK)
    except (ValueError, OSError):
        return ""
    from urllib.parse import quote

    return f"/api/files?path={quote(str(path))}"


def _job_or_404(job_id: int) -> dict:
    j = J.get(job_id)
    if not j:
        raise HTTPException(404, "작업이 없습니다")
    return j


def _fail(ok: bool, msg: str) -> dict:
    if not ok:
        raise HTTPException(409, msg)
    return {"ok": True, "message": msg}


# ── 회차: 시작 · 직접 추가 · 완료로 기록 · 다시 열기 · 삭제 ─────────────────────────

class RoundIn(BaseModel):
    product_id: int
    version: str
    version_input: str = ""


def _round_of(product_id: int, version: str) -> tuple[dict, dict]:
    p = P.get(product_id)
    if not p:
        raise HTTPException(404, "제품이 없습니다")
    for d in J.version_rows(p):
        if d["version"] == version:
            return p, d
    raise HTTPException(404, "회차를 찾지 못했습니다 — 목록을 새로 읽으세요")


def _unknown(v: str) -> bool:
    return not v or v.startswith("?")


@router.get("/api/rounds/plan")
def round_plan(product_id: int, version: str, request: Request):
    """시작 전 — 이 회차가 왜 잡혔는지, 시작하면 무엇을 하게 되는지."""
    _user(request)
    from app import stages as S

    p, d = _round_of(product_id, version)
    upd = d.get("update")
    if upd is not None:
        if upd.get("source") == "deploy":
            why = "배포 공지로 감지된 새 회차입니다(트리거 메일 없음)."
        elif upd.get("approved"):
            why = "Google 승인까지 끝난 새 회차입니다."
        else:
            why = "승인 요청이 접수된 새 회차입니다(승인 대기)."
    elif d.get("published_at"):
        why = f"이미 출시된 버전입니다 (출시 {d['published_at']})."
    else:
        why = "개발 Release Note 만 있는 버전입니다."
    mails = J.basis_from_update(upd) if upd else []
    sides = [{"side": side, "label": S.SIDE_LABELS.get(side, side),
              "stages": [S.stage_label(k, side) for k in S.stage_keys_for(side)]}
             for side in (S.SIDE_SUBMISSION, S.SIDE_APPROVAL)]
    return {
        "product": {"id": p["id"], "product": p.get("product"), "android": str(p.get("android") or ""),
                    "os_type": p.get("os_type", "")},
        "version": d["version"], "state": d["state"], "unknown": _unknown(d["version"]),
        "basis": (upd or {}).get("basis", "") if _unknown(d["version"]) else "",
        "why": why, "published_at": d.get("published_at", ""),
        "mails": [{"mail_id": m.get("mail_id"), "subject": m.get("subject", ""), "date": m.get("date", ""),
                   "version": m.get("version", ""), "origin": bool(m.get("not_re"))} for m in mails],
        "sides": sides, "job_id": (d.get("job") or {}).get("id"),
    }


@router.post("/api/rounds/start")
def round_start(body: RoundIn, request: Request):
    u = _user(request)
    p, d = _round_of(body.product_id, body.version)
    if d.get("job"):
        raise HTTPException(409, "이미 시작한 회차입니다")
    version = (body.version_input or "").strip() if _unknown(d["version"]) else d["version"]
    if not version or _unknown(version):
        raise HTTPException(409, "OS 버전을 입력하세요")
    if d.get("update") is not None:
        ok, msg, job_id = J.start_from_update(p, version, d["update"], actor=u["username"])
    else:
        ok, msg, job_id = J.create(p["id"], version, actor=u["username"])
    _fail(ok, msg)
    return {"ok": True, "message": msg, "job_id": job_id}


@router.post("/api/rounds/create")
def round_create(body: RoundIn, request: Request):
    """버전 직접 추가 — 배포 공지·트리거 메일 없이 나가는 예외 회차."""
    u = _user(request)
    ok, msg, job_id = J.create(body.product_id, (body.version or "").strip(), actor=u["username"])
    _fail(ok, msg)
    return {"ok": True, "message": msg, "job_id": job_id}


@router.post("/api/rounds/close")
def round_close(body: RoundIn, request: Request):
    """실행 없이 '이미 끝난 회차'로 기록."""
    u = _user(request)
    if _unknown(body.version):
        raise HTTPException(409, "버전을 정하지 못한 회차는 완료로 기록할 수 없습니다")
    ok, msg = J.close_as_done(body.product_id, body.version, actor=u["username"])
    return _fail(ok, msg)


@router.post("/api/jobs/{job_id}/reopen")
def job_reopen(job_id: int, request: Request):
    u = _user(request)
    _job_or_404(job_id)
    ok, msg = J.reopen(job_id, actor=u["username"])
    return _fail(ok, msg)


@router.delete("/api/jobs/{job_id}")
def job_delete(job_id: int, request: Request):
    _user(request)
    _job_or_404(job_id)
    ok, msg = J.delete(job_id)
    return _fail(ok, msg)


# ── 뉴스레터 ────────────────────────────────────────────────────────────────────

TOPICS = {"security": "보안", "network": "네트워크", "pmdm": "PMDM/ZeroTouch", "common": "일반"}


class Card(BaseModel):
    topic: str = "common"
    title_ko: str = ""
    body_ko: str = ""
    title_en: str = ""
    body_en: str = ""


class NewsletterIn(BaseModel):
    subject: str
    os_version_disp: str
    cards: list[Card]


class SendRow(BaseModel):
    lang: str = "kr"
    tag: str


class SendIn(BaseModel):
    subject: str = ""
    rows: list[SendRow]


def _nl_paths(key: str, draft: dict) -> tuple[str, str]:
    """미리보기 HTML 저장 경로(KR/EN) — 8080 newsletter._preview_paths 와 같은 규칙."""
    from app.pages.newsletter import _en_path
    from app.work_paths import anchor

    kr = anchor(draft.get("html_path", "") or "")
    if not kr:
        base = WORK / "newsletter_preview"
        base.mkdir(parents=True, exist_ok=True)
        kr = str(base / f"nl_{key}_KR.html")
    en = _en_path(kr) or (kr[:-5] + "_EN.html" if kr.endswith(".html") else kr + "_EN.html")
    return kr, en


def _nl_write_previews(key: str, draft: dict, cards: list[dict]) -> tuple[str, str]:
    from app.atomic_io import atomic_write_text
    from app.pages.newsletter import _render_en, _render_kr

    kr, en = _nl_paths(key, draft)
    try:
        atomic_write_text(kr, _render_kr(draft, cards))
        atomic_write_text(en, _render_en(draft, cards))
    except Exception:  # noqa: BLE001
        pass
    return kr, en


def _nl_view(job_id: int, key: str) -> dict:
    draft = NLWS.get(key)
    if not draft:
        return {"draft": None}
    cards = [dict(c) for c in draft.get("cards", [])]
    kr, en = _nl_write_previews(key, draft, cards)
    st = get_state(key) or {}
    return {
        "draft": {
            "subject": draft.get("subject", ""), "product_disp": draft.get("product_disp", ""),
            "os_version_disp": draft.get("os_version_disp", ""), "sdk": draft.get("sdk", ""),
            "rn_link": draft.get("rn_link", ""), "cards": cards,
            "sent": bool(draft.get("sent")), "sent_at": draft.get("sent_at", ""),
            "campaign_id": draft.get("campaign_id", ""), "updated_at": draft.get("updated_at", ""),
        },
        "preview": {"kr": _file_url(kr), "en": _file_url(en)},
        "topics": TOPICS,
        "review": (st.get("newsletter_review") or {}).get("status", "none"),
    }


@router.get("/api/jobs/{job_id}/newsletter")
def nl_get(job_id: int, request: Request):
    _user(request)
    _job_or_404(job_id)
    return _nl_view(job_id, J.state_key(job_id))


@router.post("/api/jobs/{job_id}/newsletter")
def nl_save(job_id: int, body: NewsletterIn, request: Request):
    """카드·제목 편집 → KR/EN 렌더 → 저장 → 이력(8080 '미리보기 재렌더'와 같다)."""
    u = _user(request)
    _job_or_404(job_id)
    key = J.state_key(job_id)
    draft = NLWS.get(key)
    if not draft:
        raise HTTPException(409, "Newsletter 초안이 없습니다 — Newsletter 제작을 먼저 실행하세요")
    st = get_state(key) or {}
    if (st.get("newsletter_review") or {}).get("status") in ("requested", "approved"):
        raise HTTPException(409, "결재가 요청됐거나 끝난 초안은 고칠 수 없습니다")
    cards = [c.model_dump() for c in body.cards]
    for c in cards:
        c["topic"] = c["topic"] if c["topic"] in TOPICS else "common"
        for f in ("title_ko", "body_ko", "title_en", "body_en"):
            c[f] = (c[f] or "").strip()
    draft["os_version_disp"] = body.os_version_disp.strip()
    from app.work_paths import anchor

    path = anchor(draft.get("html_path", ""))
    kr, _en = _nl_write_previews(key, draft, cards)
    NLWS.update_fields(key, subject=body.subject.strip(), cards=cards, html_path=path or None,
                       os_version_disp=draft["os_version_disp"])
    H.record(key, "newsletter", "편집", actor=u["username"], summary="카드·제목 편집 저장",
             text=H.text_from_newsletter({**draft, "subject": body.subject.strip(), "cards": cards}))
    return {"ok": True, "message": "편집본(KR/EN)을 저장했습니다", **_nl_view(job_id, key)}


@router.get("/api/jobs/{job_id}/newsletter/tags")
def nl_tags(job_id: int, request: Request):
    """발송 그룹(태그) — 8080 SEND_TAGS 에 적힌 것만, 적힌 순서대로."""
    u = _user(request)
    _job_or_404(job_id)
    from app.integrations import mailchimp as MC
    from app.integrations.creds import get_mailchimp_config
    from app.pages.newsletter import _pick_send_tags

    cfg = get_mailchimp_config(u["id"])
    if cfg is None or not cfg.usable:
        return {"tags": [], "error": "Mailchimp 설정이 없습니다 — [설정 → Mailchimp]"}
    tags, err = MC.list_all_tags(cfg)
    if err:
        return {"tags": [], "error": f"태그 목록을 읽지 못했습니다: {err}"}
    return {"tags": [{"key": f"{t['list_id']}|{t['id']}", "name": t.get("name", ""),
                      "members": int(t.get("member_count") or 0), "list_name": t.get("list_name", "")}
                     for t in _pick_send_tags(tags)], "error": ""}


@router.post("/api/jobs/{job_id}/newsletter/send")
def nl_send(job_id: int, body: SendIn, request: Request):
    """줄마다 캠페인 하나 — 생성 → 본문 → 발송(8080 newsletter.do_send 와 같은 순서)."""
    u = _user(request)
    _job_or_404(job_id)
    key = J.state_key(job_id)
    from app.integrations import mailchimp as MC
    from app.integrations.creds import get_mailchimp_config
    from app.pages.newsletter import _pick_send_tags, _render_en, _render_kr, _send_link
    from app.pipeline import newsletter_images as NLI

    draft = NLWS.get(key)
    if not draft:
        raise HTTPException(409, "Newsletter 초안이 없습니다")
    if not _send_link(key):
        raise HTTPException(409, "초안에 Release Note 링크가 없습니다 — Newsletter 제작을 다시 실행하세요")
    cfg = get_mailchimp_config(u["id"])
    if cfg is None or not cfg.usable:
        raise HTTPException(409, "Mailchimp 설정이 없습니다 — [설정 → Mailchimp]")
    tags, err = MC.list_all_tags(cfg)
    if err:
        raise HTTPException(409, f"태그 목록을 읽지 못했습니다: {err}")
    tmap = {f"{t['list_id']}|{t['id']}": t for t in _pick_send_tags(tags)}
    plan = [(r.lang if r.lang in ("kr", "en") else "kr", tmap[r.tag]) for r in body.rows if r.tag in tmap]
    if not plan:
        raise HTTPException(409, "보낼 줄이 없습니다 — 줄마다 보낼 그룹을 고르세요")

    subject = (body.subject or "").strip() or draft.get("subject", "")
    img_urls = NLI.ensure_hosted_images(cfg)
    cards = [dict(c) for c in draft.get("cards", [])]
    done, fail, ids, lines = 0, 0, [], []
    for lg, t in plan:
        who = f"{t.get('name', '')}({'EN' if lg == 'en' else 'KR'})"
        cid, err = MC.create_campaign(cfg, subject, subject, list_id=t.get("list_id", ""), segment_id=int(t["id"]))
        if not cid:
            fail += 1
            lines.append(f"✘ {who} — 캠페인 생성 실패: {err}")
            continue
        render = _render_en if lg == "en" else _render_kr
        ok, err = MC.set_content(cfg, cid, render(draft, cards, image_urls=img_urls))
        if not ok:
            fail += 1
            lines.append(f"✘ {who} — 콘텐츠 설정 실패: {err}")
            continue
        ok, msg = MC.send_campaign(cfg, cid, True)
        if not ok:
            fail += 1
            lines.append(f"✘ {who} — 발송 실패: {msg}")
            continue
        done += 1
        ids.append(cid)
        real = ""
        if cid != "shadow":
            try:
                info2, _ = MC.get_campaign(cfg, cid)
                if info2 and info2.get("recipient_count"):
                    real = f" · 수신자 {info2['recipient_count']}명"
            except Exception:  # noqa: BLE001
                pass
        lines.append(f"✔ {who}{real} · campaign {cid}")
    if done:
        NLWS.mark_sent(key, ", ".join(ids))
        H.record(key, "newsletter", "발송", actor=u["username"],
                 summary=(f"Mailchimp 발송 — 성공 {done}건" + (f" · 실패 {fail}건" if fail else "") + "\n"
                          + "\n".join(lines)),
                 text=H.text_from_newsletter({**draft, "subject": subject, "cards": cards}))
    return {"ok": not fail, "done": done, "fail": fail, "lines": lines}


# ── Service Portal · Knowledge Base 검수 편집본 ─────────────────────────────────────

class PortalSaveIn(BaseModel):
    key: str
    html: str


class KbSection(BaseModel):
    title: str = "Others"
    new: list[str] = []
    improved: list[str] = []
    fixed: list[str] = []


class KbApplyIn(BaseModel):
    key: str
    vfull: str = ""
    sdk: str = ""
    security: str = ""
    sections: list[KbSection]


class KbSourceIn(BaseModel):
    key: str
    storage: str


def _item_of(job_id: int, item_key: str, source: str) -> dict:
    it = WS.get_item(item_key)
    if not it or str(it.get("mail_id", "")) != J.state_key(job_id) or it.get("source") != source:
        raise HTTPException(404, "검수 항목이 없습니다")
    return it


def _others(it: dict) -> list[str]:
    return [str(o.get("mail_id")) for o in WS.others_on_doc(it)]


@router.get("/api/jobs/{job_id}/portal")
def portal_get(job_id: int, request: Request):
    _user(request)
    _job_or_404(job_id)
    key = J.state_key(job_id)
    items = []
    for it in WS.list_items(key):
        if it.get("source") != "service_portal":
            continue
        items.append({
            "key": it["key"], "title": it.get("title", ""), "doc_id": str(it.get("doc_id", "")),
            "applied": it.get("applied") or [], "failed": it.get("failed") or [], "error": it.get("error") or "",
            "html": it.get("edited_html") or it.get("original_html") or "",
            "original": it.get("original_html") or "", "updated_at": it.get("updated_at", ""),
            "new_doc": bool(it.get("sp_new") or it.get("new_doc")), "others": _others(it),
        })
    return {"items": items, "history": _history(key, "sp")}


@router.post("/api/jobs/{job_id}/portal")
def portal_save(job_id: int, body: PortalSaveIn, request: Request):
    u = _user(request)
    _job_or_404(job_id)
    it = _item_of(job_id, body.key, "service_portal")
    if not WS.update_edited(body.key, body.html):
        raise HTTPException(409, "저장하지 못했습니다(항목 없음)")
    H.record(J.state_key(job_id), "sp", "편집", actor=u["username"], summary="에디터 저장",
             text=H.text_from_html(body.html), doc_key=str(it.get("doc_id", "")), title=it.get("title", ""))
    return {"ok": True, "message": "편집본을 저장했습니다"}


def _kb_sections(item: dict) -> list[dict]:
    secs = [dict(s) for s in (item.get("kb_sections") or [])]
    if not secs and item.get("kb_cats"):
        c = item["kb_cats"]
        secs = [{"title": "VoC", "new": list(c.get("new") or []), "improved": list(c.get("improved") or []),
                 "fixed": list(c.get("fixed") or [])}]
    for s in secs:
        for b in ("new", "improved", "fixed"):
            s[b] = list(s.get(b) or [])
    return secs


def _kb_preview_file(key: str, item: dict) -> str:
    html = item.get("preview_html", "") or ""
    if not html.strip():
        return ""
    from app.atomic_io import atomic_write_text

    d = WORK / "kb_preview"
    d.mkdir(parents=True, exist_ok=True)
    did = re.sub(r"[^A-Za-z0-9_.-]", "_", str(item.get("doc_id", "x")))
    mid = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
    fp = d / f"kb_{mid}_{did}.html"
    try:
        atomic_write_text(str(fp), html)
    except Exception:  # noqa: BLE001
        return ""
    return _file_url(str(fp))


@router.get("/api/jobs/{job_id}/kb")
def kb_get(job_id: int, request: Request):
    _user(request)
    _job_or_404(job_id)
    from app.pipeline import kb_rn as KB

    key = J.state_key(job_id)
    items = []
    for it in WS.list_items(key):
        if it.get("source") != "release_notes":
            continue
        new_page = it.get("kb_new") or None
        items.append({
            "key": it["key"], "title": (new_page or {}).get("title") or it.get("title", ""),
            "doc_id": str(it.get("doc_id", "")), "new_page": bool(new_page),
            "ambiguous": it.get("ambiguous_why", "") if it.get("ambiguous") else "",
            "others": _others(it), "error": it.get("error") or "",
            "vfull": it.get("kb_vfull", ""), "sdk": it.get("kb_sdk", ""), "security": it.get("kb_security", ""),
            "security_date": KB.secpatch_date(it.get("kb_security") or "") if it.get("kb_security") else "",
            "sections": _kb_sections(it),
            "storage": it.get("edited_html", "") or it.get("original_html", ""),
            "preview": _kb_preview_file(key, it), "updated_at": it.get("updated_at", ""),
        })
    return {"items": items, "section_names": list(KB.SECTIONS), "history": _history(key, "kb")}


@router.post("/api/jobs/{job_id}/kb/apply")
def kb_apply(job_id: int, body: KbApplyIn, request: Request):
    """섹션 편집 → 엔트리 재생성 → 원본 맨 위에 삽입 → 미리보기 렌더(8080 kb_review._apply)."""
    u = _user(request)
    _job_or_404(job_id)
    from app.integrations import confluence_reader as RN
    from app.pipeline import kb_rn as KB

    it0 = _item_of(job_id, body.key, "release_notes")
    out = []
    for s in body.sections:
        t = {"title": (s.title or "Others").strip()}
        for b in ("new", "improved", "fixed"):
            t[b] = [x.strip() for x in getattr(s, b) if str(x).strip()]
        if any(t[b] for b in ("new", "improved", "fixed")):
            out.append(t)
    vfull, sdk, sec = body.vfull.strip(), body.sdk.strip(), body.security.strip()
    entry = KB.build_entry_html(vfull, sdk, out, security_patch=sec)
    new_storage, ins_ok = KB.insert_entry(it0.get("original_html", "") or "", entry)
    html, _e = RN.storage_to_view(u["id"], str(it0.get("doc_id", "")), new_storage)
    cnt = sum(len(s[b]) for s in out for b in ("new", "improved", "fixed"))
    it0.update(
        edited_html=new_storage, preview_html=html or "", kb_sections=out, kb_vfull=vfull, kb_sdk=sdk,
        kb_security=sec, error="" if ins_ok else "삽입 위치(최신 버전 섹션)를 찾지 못했습니다.",
        applied=[{"why": f"편집 반영 · 섹션 {len(out)}개 · 항목 {cnt}건", "old": "", "new": f"{vfull} 엔트리"}]
        if ins_ok else it0.get("applied"),
    )
    WS.put(it0)
    H.record(J.state_key(job_id), "kb", "편집", actor=u["username"],
             summary=f"섹션 {len(out)}개 · 항목 {cnt}건 편집 반영", text=H.text_from_kb_sections(out),
             doc_key=str(it0.get("doc_id", "")), title=it0.get("title", ""))
    if not ins_ok:
        raise HTTPException(409, "엔트리는 다시 만들었지만 삽입 위치(최신 버전 섹션)를 찾지 못했습니다")
    return {"ok": True, "message": f"편집을 반영했습니다 · 섹션 {len(out)}개 · 항목 {cnt}건"}


@router.post("/api/jobs/{job_id}/kb/source")
def kb_source(job_id: int, body: KbSourceIn, request: Request):
    u = _user(request)
    _job_or_404(job_id)
    from app.integrations import confluence_reader as RN

    it2 = _item_of(job_id, body.key, "release_notes")
    html2, _e = RN.storage_to_view(u["id"], str(it2.get("doc_id", "")), body.storage)
    it2["edited_html"] = body.storage
    it2["preview_html"] = html2 or ""
    WS.put(it2)
    H.record(J.state_key(job_id), "kb", "편집(소스)", actor=u["username"], summary="storage 소스 직접 편집",
             text=H.text_from_html(body.storage), doc_key=str(it2.get("doc_id", "")), title=it2.get("title", ""))
    return {"ok": True, "message": "소스를 저장했습니다"}


def _history(key: str, doc: str) -> list[dict]:
    """편집 이력 — 오래된 것부터, 같은 문서의 직전 판과 줄 단위 차이."""
    out, last = [], {}
    for e in H.history(key, doc):
        dk = e.get("doc_key", "")
        first = dk not in last
        base, cur = last.get(dk, ""), e.get("text", "")
        add, dele = H.diff_stat(base, cur)
        out.append({"version": e.get("version"), "event": e.get("event", ""), "title": e.get("title", ""),
                    "actor": e.get("actor", ""), "summary": e.get("summary", ""), "ts": str(e.get("ts", "")),
                    "add": add, "del": dele, "first": first,
                    "diff": [[t, ln] for t, ln in H.diff_lines(base, cur)][:400]})
        last[dk] = cur
    return out


# ── FOTA 등록 ───────────────────────────────────────────────────────────────────

class FotaRowsIn(BaseModel):
    osinfo: list[list]
    dp: list[list]


class FotaRegisterIn(BaseModel):
    force: bool = False


def _fota_rows(key: str) -> tuple[list, list]:
    st = get_state(key) or {}
    ois = st.get("fota_osinfo_rows") or ([st["fota_osinfo_row"]] if st.get("fota_osinfo_row") else [])
    return [list(r) for r in ois], [list(r) for r in (st.get("fota_dp_rows") or [])]


def _cell(v) -> str:
    return "" if v is None else str(v)


@router.get("/api/jobs/{job_id}/fota")
def fota_get(job_id: int, request: Request):
    _user(request)
    _job_or_404(job_id)
    from app.pipeline import excel_repo

    ois, dp = _fota_rows(J.state_key(job_id))
    return {
        "headers": {"osinfo": excel_repo.HEADERS["OsInfo"], "dp": excel_repo.HEADERS["DownloadPath"]},
        "osinfo": [[_cell(v) for v in r] for r in ois], "dp": [[_cell(v) for v in r] for r in dp],
    }


@router.post("/api/jobs/{job_id}/fota/rows")
def fota_save_rows(job_id: int, body: FotaRowsIn, request: Request):
    """등록할 행을 고친다 — 표에 보이는 값과 등록이 보내는 값이 같은 상태값이다."""
    u = _user(request)
    _job_or_404(job_id)
    from app.pipeline import excel_repo

    key = J.state_key(job_id)
    n_oi, n_dp = len(excel_repo.HEADERS["OsInfo"]), len(excel_repo.HEADERS["DownloadPath"])
    old_oi, old_dp = _fota_rows(key)

    def keep_types(new_rows, old_rows, width):
        out = []
        for i, r in enumerate(new_rows):
            r = (list(r) + [""] * width)[:width]
            old = old_rows[i] if i < len(old_rows) else []
            row = []
            for j, v in enumerate(r):
                ov = old[j] if j < len(old) else None
                if isinstance(ov, int) and str(v).strip().lstrip("-").isdigit():
                    row.append(int(str(v).strip()))
                elif ov is None and str(v) == "":
                    row.append(None)
                else:
                    row.append(v)
            out.append(row)
        return out

    ois = keep_types(body.osinfo, old_oi, n_oi)
    dp = keep_types(body.dp, old_dp, n_dp)
    update_state(key, fota_osinfo_rows=ois, fota_dp_rows=dp)
    H.record(key, "fota", "편집", actor=u["username"], summary=f"OsInfo {len(ois)}행 · DownloadPath {len(dp)}행",
             text="\n".join("\t".join(_cell(v) for v in r) for r in ois + dp))
    return {"ok": True, "message": f"저장했습니다 — OsInfo {len(ois)}행 · DownloadPath {len(dp)}행"}


@router.post("/api/jobs/{job_id}/fota/dryrun")
def fota_dryrun(job_id: int, request: Request):
    u = _user(request)
    _job_or_404(job_id)
    from app.integrations import fota_system

    ois, dp = _fota_rows(J.state_key(job_id))
    if not ois:
        raise HTTPException(409, "등록할 OsInfo 행이 없습니다")
    rep = fota_system.register_rows(u["id"], ois, dp, False)
    return {"ok": bool(rep.get("ok")), "mode": rep.get("mode", "DRY"), "report": fota_system.format_report(rep)}


@router.get("/api/jobs/{job_id}/fota/plan")
def fota_plan(job_id: int, request: Request):
    """실제 등록 전 대조 — 새로 보낼 행과 이미 있는 행."""
    u = _user(request)
    _job_or_404(job_id)
    from app.integrations import fota_system

    ois, dp = _fota_rows(J.state_key(job_id))
    plan = fota_system.plan_registration(u["id"], ois, dp)
    return {
        "ok": plan["ok"], "error": plan.get("error", ""),
        "new_osinfo": [{"os_version": _cell(r[2]) if len(r) > 2 else "", "version": _cell(r[1]) if len(r) > 1 else ""}
                       for r in plan["new_osinfo"]],
        "new_dp": len(plan["new_dp"]),
        "dup_osinfo": [{"id": _cell(m.get("id", "?")), "version": _cell(m.get("version", "")),
                        "os_version": _cell(m.get("os_version", ""))} for _r, m in plan["dup_osinfo"]],
        "dup_dp": [{"id": _cell(m.get("id", "?")), "os_version": _cell(m.get("os_version", "")),
                    "type": _cell(m.get("download_type", ""))} for _r, m in plan["dup_dp"]],
        "total_osinfo": len(ois), "total_dp": len(dp),
    }


@router.post("/api/jobs/{job_id}/fota/register")
def fota_register(job_id: int, body: FotaRegisterIn, request: Request):
    """실제 등록 — 이미 올라간 행은 빼고 보낸다(force 면 전부). 되읽어 확인된 것만 기록."""
    u = _user(request)
    _job_or_404(job_id)
    from app.integrations import fota_system
    from app.pipeline.excel_docs import record_registered

    key = J.state_key(job_id)
    ois, dp = _fota_rows(key)
    if body.force:
        send_oi, send_dp = ois, dp
    else:
        plan = fota_system.plan_registration(u["id"], ois, dp)
        send_oi, send_dp = plan["new_osinfo"], plan["new_dp"]
    if not send_oi and not send_dp:
        raise HTTPException(409, "보낼 행이 없습니다 — 이미 모두 등록되어 있습니다")
    rep = fota_system.register_rows(u["id"], send_oi, send_dp, True)
    n = record_registered(u["id"], key, rep)
    return {"ok": bool(rep.get("ok")), "mode": rep.get("mode", ""), "report": fota_system.format_report(rep),
            "recorded_osinfo": n, "recorded_dp": len(rep.get("dp_ok") or [])}


# ── Dropbox 구버전 정리 ─────────────────────────────────────────────────────────

class GroupIn(BaseModel):
    key: str


@router.get("/api/jobs/{job_id}/dropbox")
def dbx_groups(job_id: int, request: Request):
    u = _user(request)
    _job_or_404(job_id)
    from app.pipeline.dropbox_release import cleanup_groups

    groups, err = cleanup_groups(u["id"], J.state_key(job_id))
    return {"error": err, "groups": [{"key": g["key"], "label": g["label"], "base": g["base"],
                                      "files": g["files"], "skipped": g["skipped"], "unsafe": bool(g.get("unsafe"))}
                                     for g in groups]}


@router.post("/api/jobs/{job_id}/dropbox/delete")
def dbx_delete(job_id: int, body: GroupIn, request: Request):
    u = _user(request)
    _job_or_404(job_id)
    from app.pipeline.dropbox_release import delete_group

    res = delete_group(u["id"], J.state_key(job_id), body.key)
    return {"ok": not res.startswith("✘"), "result": res}
