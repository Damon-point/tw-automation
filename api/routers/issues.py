"""
Solution Buffer — Redmine salesissue 감시 · SB 문서 검토/업로드 · 통계 · 동작 설정.

8080 의 /issues(pages/issues.py · sb_docs.py · issue_stats.py)와 설정의 Solution Buffer 칸을 옮겼다.
  · SB 문서는 **내 담당 건만** 보이고 고칠 수 있다(관리자는 전체).
  · Confluence 업로드·검토 요청 메일은 사람이 눌렀을 때만. 검증 모드면 app.shadow 가 막고 기록만 한다.
  · Redmine 시각은 UTC 로 들어와 있어 KST(+09:00) ISO 로 내보낸다.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.issuetrack import markup
from app.issuetrack import sb
from app.issuetrack import settings as it_settings
from app.issuetrack import stats as ST
from app.issuetrack import sync as it_sync
from app.issuetrack.client import issue_url
from app.issuetrack.db import issuetrack_session
from app.issuetrack.events import ACTION_KINDS, Naming, timeline, timings
from app.issuetrack.models import Issue, SBDoc

from common import admin as _admin
from common import user as _user

router = APIRouter(prefix="/api/issues", tags=["issues"])

ISSUE_PAGE = 50
SB_PAGE = 30

#: SB 상태 → 한글 이름. 화면 색은 web 쪽에서 정한다.
SB_STATES = {
    "backlog": "소급 대기", "not_needed": "소급 불필요", "not_target": "SB 대상 아님",
    "queued": "생성 대기", "draft": "초안", "notified": "검토 요청됨", "approved": "승인됨",
    "uploaded": "업로드 완료", "discarded": "폐기", "error": "생성 실패",
}
#: 기본으로 보여 줄 상태 — 사람이 할 일이 남은 것들.
SB_ACTIVE = ["queued", "draft", "notified", "approved", "error"]
#: 칩으로 만들지 않는 상태(감시 시작 때 한 번 정리하고 끝난 소급분).
SB_HIDDEN = ["backlog", "not_needed", "not_target"]
KNOWN_LABELS = ["sb", "confidential", "partner", "ascp", "posted"]

ACTION_WHAT = {
    "resolve": "상태를 Resolved · Closed · Rejected 로 바꿨다 (첫 손길이 곧 종결)",
    "analyze": "상태를 InProgress 로 바꿨다 (분석 착수)",
    "status": "그 밖의 상태 변경 (ReOpen 등)",
    "comment": "코멘트를 남겼다",
    "assign": "담당자만 지정했다",
    "attach": "첨부만 올렸다",
    "field": "진행률·기한·우선순위·연관 이슈 등 필드만 고쳤다",
    "empty": "코멘트도 변경 내역도 없는 이벤트",
}


def _kst(dt: datetime | None) -> str:
    """UTC naive → KST ISO(+09:00)."""
    if dt is None:
        return ""
    return (dt + timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%S+09:00")


def _iso_kst(s: str | None) -> str:
    """sync 상태 값(UTC ISO 문자열) → KST ISO."""
    if not s:
        return ""
    try:
        return _kst(datetime.fromisoformat(s).replace(tzinfo=None))
    except ValueError:
        return ""


def _is_admin(u: dict) -> bool:
    return u.get("role") == "admin"


# ── 머리말 · 동기화 ──────────────────────────────────────────────────────────

def _open_doc_count(u: dict) -> int:
    conds = [SBDoc.superseded.is_(False), SBDoc.state.in_(("draft", "notified", "error"))]
    if not _is_admin(u):
        conds.append(SBDoc.assignee_name == (u.get("username") or ""))
    try:
        with issuetrack_session() as db:
            return db.scalar(select(func.count()).select_from(SBDoc).where(*conds)) or 0
    except Exception:  # noqa: BLE001
        return 0


@router.get("/overview")
def overview(request: Request):
    u = _user(request)
    try:
        st = it_sync.status()
    except Exception as e:  # noqa: BLE001
        st = {"last_error": str(e)}
    return {
        "me": u.get("username"), "admin": _is_admin(u),
        "sb_open": _open_doc_count(u),
        "sync": {
            "issues": st.get("issues", 0), "journals": st.get("journals", 0),
            "detail_pending": st.get("detail_pending", 0), "backfill": st.get("backfill", ""),
            "last_poll": _iso_kst(st.get("last_poll")), "last_error": st.get("last_error") or "",
            "running": bool(st.get("running")) or bool(_sync_thread and _sync_thread.is_alive()),
        },
        "sync_result": _sync_result(),
        "version": list(it_sync.data_version()),
    }


_sync_thread: threading.Thread | None = None
_sync_last: dict = {}


@router.post("/sync")
def sync_now(request: Request):
    """Redmine 에서 새 이벤트를 지금 가져온다(평소 5분마다 자동). 읽기만 한다.

    최초 전수 수집은 몇 분 걸려 요청 안에서 기다리지 않는다 — 스레드로 돌리고 overview 로 따라간다.
    """
    global _sync_thread
    _user(request)
    if (_sync_thread and _sync_thread.is_alive()) or it_sync.status().get("running"):
        return {"ok": True, "started": False, "message": "이미 수집 중입니다"}

    def run() -> None:
        try:
            res = it_sync.poll()
        except Exception as e:  # noqa: BLE001
            res = {"error": str(e)}
        _sync_last.clear()
        _sync_last.update(res or {}, at=time.time())

    _sync_thread = threading.Thread(target=run, name="twn-issues-sync", daemon=True)
    _sync_thread.start()
    return {"ok": True, "started": True, "message": "Redmine 에서 가져오는 중"}


def _sync_result() -> dict:
    if not _sync_last:
        return {}
    r = _sync_last
    if r.get("error"):
        msg, ok = f"동기화 실패: {r['error']}", False
    elif r.get("skipped"):
        msg, ok = r.get("reason") or "이미 동기화가 진행 중입니다", True
    elif r.get("detail_done"):
        msg, ok = f"이슈 {r['detail_done']}건 갱신 (이벤트 {r.get('journals', 0)}건)", True
    else:
        msg, ok = "새 이벤트가 없습니다", True
    return {"ok": ok, "message": msg, "at": r.get("at", 0)}


# ── 이슈 목록 · 타임라인 ─────────────────────────────────────────────────────

@router.get("/list")
def issue_list(request: Request, q: str = "", status: str = "", limit: int = ISSUE_PAGE):
    _user(request)
    limit = max(1, min(limit, 1000))
    q = q.strip()
    with issuetrack_session() as db:
        counts = {s or "(없음)": n for s, n in db.execute(
            select(Issue.status, func.count()).where(Issue.excluded.is_(False))
            .group_by(Issue.status)).all()}
        # 이슈 번호를 그대로 치면 다른 조건은 무시한다 — 그 이슈를 보러 온 것이다.
        if q.lstrip("#").isdigit() and len(q.lstrip("#")) >= 4:
            row = db.get(Issue, int(q.lstrip("#")))
            rows, total = ([row], 1) if row else ([], 0)
        else:
            conds = [Issue.excluded.is_(False)]
            if status:
                conds.append(Issue.status.in_([s for s in status.split(",") if s]))
            if q:
                like = f"%{q}%"
                conds.append(or_(Issue.subject.ilike(like), Issue.assigned_to_name.ilike(like),
                                 Issue.author_name.ilike(like)))
            total = db.scalar(select(func.count()).select_from(Issue).where(*conds)) or 0
            rows = db.execute(select(Issue).where(*conds).order_by(Issue.updated_on.desc())
                              .limit(limit)).scalars().all()
        out = [{
            "id": r.id, "status": r.status or "", "subject": r.subject or "",
            "assignee": r.assigned_to_name or "", "author": r.author_name or "",
            "category": r.category or "", "tracker": r.tracker or "", "priority": r.priority or "",
            "created": _kst(r.created_on), "updated": _kst(r.updated_on),
            "is_test": bool(r.is_test), "excluded": bool(r.excluded),
            "excluded_reason": r.excluded_reason or "", "url": issue_url(r.id),
        } for r in rows]
    return {"total": total, "rows": out,
            "counts": sorted(([k, v] for k, v in counts.items()), key=lambda x: -x[1])}


def _days(v: float | None) -> float | None:
    return round(v, 2) if v is not None else None


@router.get("/{issue_id}/timeline")
def issue_timeline(issue_id: int, request: Request):
    _user(request)
    with issuetrack_session() as db:
        issue = db.execute(select(Issue).options(selectinload(Issue.journals))
                           .where(Issue.id == issue_id)).scalars().first()
        if issue is None:
            raise HTTPException(404, "이슈가 없습니다")
        naming = Naming()
        evs = timeline(issue, naming)
        t = timings(issue, naming)
        events = [{
            "at": _kst(e.at), "who": e.who or "", "kind": e.kind, "creation": e.is_creation,
            "new_status": e.new_status or "", "summary": e.summary,
            "note_html": markup.render(e.note) if e.note else "",
        } for e in evs]
    return {
        "events": events,
        "metrics": {
            "first_action_days": _days(t.first_action_workdays),
            "first_action_kind": ACTION_KINDS.get(t.first_action_kind, ""),
            "first_action_by": t.first_action_by or "",
            "lead_days": _days(t.lead_workdays), "reopened": t.reopened,
            "participants": t.participants, "comments": t.comment_count,
        },
    }


# ── SB 문서 ──────────────────────────────────────────────────────────────────

def _doc_dict(d: SBDoc, subject: str, issue_status: str) -> dict:
    return {
        "id": d.id, "issue_id": d.issue_id, "revision": d.revision, "state": d.state,
        "state_label": SB_STATES.get(d.state, d.state), "origin": d.origin,
        "superseded": bool(d.superseded), "title": d.title or "", "disclosure": d.disclosure,
        "reason": d.disclosure_reason or "", "applies_to": d.applies_to or "",
        "description_html": d.description_html or "",
        "partners": json.loads(d.partners_json or "[]"),
        "labels": json.loads(d.labels_json or "[]"),
        "assignee": d.assignee_name or "", "email": d.assignee_email or "",
        "error": d.error or "", "url": d.confluence_url or "", "page_id": d.confluence_page_id or "",
        "resolved_at": _kst(d.source_resolved_at), "generated_at": _kst(d.generated_at),
        "notified_at": _kst(d.notified_at), "uploaded_at": _kst(d.uploaded_at),
        "decided_by": d.decided_by or "",
        "in_tok": d.input_tokens or 0, "out_tok": d.output_tokens or 0,
        "subject": subject or "", "issue_status": issue_status or "", "issue_url": issue_url(d.issue_id),
        "generating": d.id in _gen_running,
        "gen_result": _gen_result.get(d.id) or None,
    }


@router.get("/sb")
def sb_list(request: Request, q: str = "", states: str = "active", mine: int = 0, limit: int = SB_PAGE):
    """SB 문서 목록. 일반 사용자는 본인 담당만, 관리자는 전체(또는 mine=1 로 본인 것만)."""
    u = _user(request)
    me = u.get("username") or ""
    admin = _is_admin(u)
    scope = me if (not admin or mine) else ""
    limit = max(1, min(limit, 500))

    conds = [SBDoc.superseded.is_(False)]
    if not admin or mine:
        # 이름이 비어 있어도 조건을 건다 — 빈 문자열은 아무것과도 안 맞아 남의 문서가 새지 않는다.
        conds.append(SBDoc.assignee_name == scope)
    st_list = SB_ACTIVE if states == "active" else [s for s in states.split(",") if s and s != "all"]
    if st_list:
        conds.append(SBDoc.state.in_(st_list))
    q = q.strip()
    if q:
        if q.lstrip("#").isdigit():
            conds.append(SBDoc.issue_id == int(q.lstrip("#")))
        else:
            conds.append(SBDoc.title.ilike(f"%{q}%"))

    with issuetrack_session() as db:
        total = db.scalar(select(func.count()).select_from(SBDoc).where(*conds)) or 0
        rows = db.execute(
            select(SBDoc, Issue.subject, Issue.status).join(Issue, Issue.id == SBDoc.issue_id)
            .where(*conds).order_by(SBDoc.source_resolved_at.desc(), SBDoc.id.desc()).limit(limit)
        ).all()
        docs = [_doc_dict(d, s, st) for d, s, st in rows]

    summ = sb.summary(scope)
    return {
        "total": total, "docs": docs, "me": me, "admin": admin, "scope": scope,
        "counts": summ["counts"], "spent_usd": summ["spent_usd"], "generating": summ["running"],
        "states": SB_STATES, "active": SB_ACTIVE, "hidden": SB_HIDDEN,
        "templates": {k: {"ko": v["ko"], "name": v["name"], "labels": v["labels"]} for k, v in sb.TEMPLATES.items()},
        "default_labels": {k: _default_labels(k) for k in sb.TEMPLATES},
        "partners": sb.PARTNER_CHOICES, "known_labels": KNOWN_LABELS,
    }


def _default_labels(disclosure: str) -> list[str]:
    from app.issuetrack.confluence import default_labels_for

    return default_labels_for(disclosure)


def _load_doc_for(u: dict, doc_id: int) -> dict:
    """본인 담당(관리자는 전체) 문서만 다룬다."""
    with issuetrack_session() as db:
        row = db.execute(select(SBDoc, Issue.subject, Issue.status).join(Issue, Issue.id == SBDoc.issue_id)
                         .where(SBDoc.id == doc_id)).first()
        if row is None:
            raise HTTPException(404, "문서가 없습니다")
        d = _doc_dict(*row)
    if not _is_admin(u) and d["assignee"] != (u.get("username") or ""):
        raise HTTPException(403, "본인 담당 문서만 다룰 수 있습니다")
    return d


@router.get("/sb/{doc_id}")
def sb_get(doc_id: int, request: Request):
    return _load_doc_for(_user(request), doc_id)


class DraftIn(BaseModel):
    title: str = ""
    disclosure: str = "internal"
    applies_to: str = ""
    description_html: str = ""
    partners: list[str] = []
    labels: list[str] = []


def _save(doc_id: int, body: DraftIn) -> None:
    res = sb.update_draft(doc_id, title=body.title, disclosure=body.disclosure, applies_to=body.applies_to,
                          description_html=body.description_html, partners=body.partners,
                          labels=body.labels or None)
    if not res.get("ok"):
        raise HTTPException(409, res.get("error") or "저장 실패")


@router.post("/sb/{doc_id}/save")
def sb_save(doc_id: int, body: DraftIn, request: Request):
    u = _user(request)
    _load_doc_for(u, doc_id)
    _save(doc_id, body)
    return {"ok": True, "message": "저장했습니다", "doc": _load_doc_for(u, doc_id)}


@router.post("/sb/{doc_id}/preview")
def sb_preview(doc_id: int, body: DraftIn, request: Request):
    """저장 → Confluence 변환 API 로 문법 검증 → 렌더 결과. 읽기만 한다."""
    from app.issuetrack import confluence as C

    u = _user(request)
    _load_doc_for(u, doc_id)
    _save(doc_id, body)
    res = C.preview(doc_id)
    if not res.get("ok"):
        raise HTTPException(502, f"검증 실패: {res.get('error')}")
    return {"ok": True, "title": res["title"], "labels": res["labels"], "html": res["html"],
            "exists": res.get("exists")}


@router.post("/sb/{doc_id}/notify")
def sb_notify(doc_id: int, body: DraftIn, request: Request):
    u = _user(request)
    _load_doc_for(u, doc_id)
    _save(doc_id, body)
    res = sb.notify(doc_id)
    if not res.get("sent"):
        raise HTTPException(409, f"발송 실패: {res.get('error')}")
    return {"ok": True, "message": f"검토 요청 메일을 보냈습니다 → {', '.join(res.get('to') or [])}"}


@router.post("/sb/{doc_id}/upload")
def sb_upload(doc_id: int, body: DraftIn, request: Request):
    from app import shadow
    from app.issuetrack import confluence as C

    u = _user(request)
    _load_doc_for(u, doc_id)
    _save(doc_id, body)
    res = C.publish(doc_id, by=u.get("username") or "")
    if not res.get("ok"):
        raise HTTPException(409, f"업로드 실패: {res.get('error')}")
    kind = "갱신" if res.get("action") == "updated" else "신규"
    msg = f"업로드 완료({kind}) — {res.get('url')}"
    if not shadow.live():
        msg = f"검증 모드 — Confluence 에 쓰지 않고 기록만 했습니다({kind})"
    return {"ok": True, "message": msg, "url": res.get("url")}


_gen_running: set[int] = set()
_gen_lock = threading.Lock()
_gen_result: dict[int, dict] = {}


@router.post("/sb/{doc_id}/generate")
def sb_generate(doc_id: int, request: Request):
    """Claude 로 초안 생성(30초 안팎). 스레드로 돌리고, 화면은 문서의 generating 표시로 따라간다."""
    u = _user(request)
    _load_doc_for(u, doc_id)
    with _gen_lock:
        if doc_id in _gen_running:
            raise HTTPException(409, "이미 생성 중입니다")
        _gen_running.add(doc_id)
    _gen_result.pop(doc_id, None)

    def run() -> None:
        try:
            res = sb.generate(doc_id)
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "error": str(e)}
        finally:
            with _gen_lock:
                _gen_running.discard(doc_id)
        _gen_result[doc_id] = res

    threading.Thread(target=run, name=f"twn-sb-gen-{doc_id}", daemon=True).start()
    return {"ok": True, "message": "Claude 로 초안을 만드는 중"}


class DiscardIn(BaseModel):
    reason: str = ""


@router.post("/sb/{doc_id}/discard")
def sb_discard(doc_id: int, body: DiscardIn, request: Request):
    u = _user(request)
    _load_doc_for(u, doc_id)
    res = sb.discard(doc_id, by=u.get("username") or "", reason=body.reason.strip())
    if not res.get("ok"):
        raise HTTPException(409, res.get("error") or "실패")
    return {"ok": True, "message": "폐기했습니다"}


@router.post("/sb/{doc_id}/reopen")
def sb_reopen(doc_id: int, request: Request):
    u = _user(request)
    _load_doc_for(u, doc_id)
    res = sb.reopen_draft(doc_id)
    if not res.get("ok"):
        raise HTTPException(409, res.get("error") or "실패")
    return {"ok": True, "message": "되돌렸습니다"}


# ── 통계 ──────────────────────────────────────────────────────────────────────

_stats_cache: dict[bool, tuple[tuple, list]] = {}


def _rows(include_excluded: bool, fresh: bool) -> list:
    """통계용 이슈 묶음 — Redmine 데이터 판번호가 그대로면 직전 계산을 쓴다(한 번에 3초).

    예전에는 60초마다 무조건 다시 셌다. 수집이 끝나면 runtime 이 미리 데워 둔다(warm_stats).
    """
    ver = _stats_version()
    hit = _stats_cache.get(include_excluded)
    if hit and not fresh and hit[0] == ver:
        return hit[1]
    rows = ST.collect(include_excluded=include_excluded)
    _stats_cache[include_excluded] = (ver, rows)
    return rows


def _stats_version() -> tuple:
    """통계가 바뀌었는지 — 이슈·이벤트 판번호에 더해, 수를 바꾸지 않고 바뀌는 것(제외 · 테스트 표시 ·
    수집 시각)까지 본다. 제외 키워드를 고치면 excluded 만 바뀌기 때문이다."""
    from sqlalchemy import func, select

    from app.issuetrack.db import issuetrack_session
    from app.issuetrack.models import Issue

    with issuetrack_session() as db:
        extra = db.execute(select(
            func.count().filter(Issue.excluded.is_(True)), func.count().filter(Issue.is_test.is_(True)),
            func.max(Issue.updated_on), func.max(Issue.synced_at))).one()
    return tuple(it_sync.data_version()) + tuple(str(x) for x in extra)


def warm_stats() -> None:
    """Redmine 수집 직후 runtime 이 부른다 — 처음 통계를 여는 사람이 3초를 기다리지 않게."""
    _rows(False, False)


def _r1(v: float | None) -> float | None:
    return round(v, 2) if v is not None else None


@router.get("/stats")
def stats(request: Request, include_excluded: int = 0, basis: str = "target", months: int = 24,
          top: int = 10, fresh: int = 0):
    _user(request)
    rows = _rows(bool(include_excluded), bool(fresh))
    s = ST.summary(rows)
    if basis not in ST.GROUP_BASES:
        basis = "target"
    comp = ST.composition(rows, basis=basis, months=max(1, min(months, 240)), top=max(1, min(top, 30)))

    from collections import Counter

    comp_rows = []
    for g, n in comp.totals:
        mem = comp.members.get(g, [])
        lead = [m.lead_days for m in mem if m.lead_days is not None and m.lead_days >= 0]
        inside = ""
        if g == "기타":
            c = Counter(m.target for m in mem)
            inside = ", ".join(f"{k}({v})" for k, v in c.most_common(12))
            if len(c) > 12:
                inside += f" 외 {len(c) - 12}종"
        comp_rows.append({"name": g, "total": n, "open": sum(1 for m in mem if m.is_open),
                          "lead": round(sum(lead) / len(lead), 1) if lead else None, "inside": inside})

    acts = ST.by_first_action(rows)
    return {
        "summary": {k: (_r1(v) if isinstance(v, float) else v) for k, v in s.items() if k != "aging"},
        "aging": [{"days": d, "id": r.id, "status": r.status, "subject": r.subject, "assignee": r.assignee,
                   "url": issue_url(r.id)} for d, r in s["aging"]],
        "monthly": ST.monthly(rows, 24),
        "lead_hist": ST.duration_hist(rows, "lead_days"),
        "first_hist": ST.duration_hist(rows, "first_action_days"),
        "by_status": ST.by_status(rows),
        "by_tag": ST.by_tag(rows, 18),
        "by_assignee": [{**a, "mean_lead": _r1(a["mean_lead"]), "mean_action": _r1(a["mean_action"])}
                        for a in ST.by_assignee(rows, 20)],
        "first_action": [{"key": a["key"], "name": a["name"], "value": a["value"],
                          "mean_days": _r1(a["mean_days"]), "median_days": _r1(a["median_days"]),
                          "what": ACTION_WHAT.get(a["key"], "")} for a in acts],
        "no_action": len(ST.no_action(rows)),
        "composition": {"labels": comp.labels, "groups": comp.groups, "series": comp.series,
                        "n": comp.n, "rows": comp_rows, "basis": basis, "months": months, "top": top},
        "bases": ST.GROUP_BASES,
    }


# ── 동작 설정 ─────────────────────────────────────────────────────────────────

@router.get("/settings")
def get_settings(request: Request):
    _user(request)
    cfg = it_settings.load()
    return {
        "sb_auto_notify": cfg.sb_auto_notify, "sb_auto_generate": cfg.sb_auto_generate,
        "sb_per_tick": cfg.sb_per_tick, "sb_cutoff": cfg.sb_cutoff,
        "sb_assignees": cfg.sb_assignees, "exclude_keywords": cfg.exclude_keywords,
        "without_account": [{"name": n, "count": c} for n, c in sb.assignees_without_account()],
    }


class SettingsIn(BaseModel):
    sb_auto_notify: bool | None = None
    sb_assignees: str | None = None
    exclude_keywords: str | None = None


@router.post("/settings")
def set_settings(body: SettingsIn, request: Request):
    _admin(request)
    for k, v in body.model_dump(exclude_none=True).items():
        it_settings.set_(k, v.strip() if isinstance(v, str) else v)
    return {"ok": True, "message": "저장했습니다 — 다음 수집 주기부터 적용됩니다"}
