"""Redmine → issuetrack.db 수집. 전수 백필 + 증분 폴링.

동기화 전략
- **백필**: 목록 API 로 이슈 골격을 전부 넣고(빠름), 그 다음 이슈별 상세를 받아
  journals 를 채운다. 상세는 이슈당 1콜(~0.15초)이라 1,600여 건이면 4~5분.
  detail_fetched 플래그로 진행 상황을 DB 에 남기므로 중간에 죽어도 이어서 받는다.
- **폴링**: `updated_on >= (내가 본 가장 최신 updated_on − OVERLAP)` 로 목록을 받아
  updated_on 이 달라진 이슈만 상세를 다시 받는다. OVERLAP 을 두는 이유는 Redmine
  서버 시각과 로컬 시각이 어긋나도 이벤트를 흘리지 않기 위함이다. 중복은 journal id
  가 PK 라 자동으로 흡수된다.
- 이슈 상세를 다시 받을 때는 그 이슈의 journal 을 지우고 다시 넣는다. Redmine 에서
  코멘트가 수정/삭제될 수 있어 병합보다 교체가 정확하다.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import func, select

from app.issuetrack.client import PROJECT, RedmineClient, RedmineError, parse_dt
from app.issuetrack.db import init_issuetrack_db, issuetrack_session
from app.issuetrack.models import (
    Attachment,
    Issue,
    Journal,
    JournalDetail,
    Lookup,
    SyncState,
)

log = logging.getLogger(__name__)

# 제외 키워드는 [설정 → Solution Buffer]에서 바꿀 수 있다(app/issuetrack/settings.py).
# 제외는 삭제가 아니라 플래그라, 규칙을 바꾸고 다시 훑으면 그대로 되살아난다.

#: 폴링 시 되짚어보는 여유 구간. 서버-로컬 시계 오차 + 경계 이벤트 누락 방지.
POLL_OVERLAP = timedelta(minutes=int(os.getenv("TW_REDMINE_POLL_OVERLAP_MIN", "15")))

#: 폴링 1회에서 상세를 다시 받을 이슈 수 상한. 대량 일괄 수정이 있어도 한 틱이
#: 몇 분씩 붙잡히지 않게 한다. 남은 건 detail_fetched=False 로 남아 다음 틱에 처리된다.
POLL_DETAIL_CAP = int(os.getenv("TW_REDMINE_POLL_DETAIL_CAP", "80"))

_sync_lock = threading.Lock()

#: 이슈 payload 에서 주워담은 커스텀 필드 id→이름. journal detail 의 property='cf' 는
#: 이름 없이 id 만 주는데, Redmine 의 /custom_fields.json 은 관리자 전용이라 막히는
#: 경우가 있다. 이슈마다 딸려오는 custom_fields 로 매핑을 채워 두면 그럴 필요가 없다.
_cf_seen: dict[int, str] = {}

K_LAST_SEEN = "last_seen_updated_on"   # 목록에서 관측한 가장 최신 updated_on
K_BACKFILL = "backfill_state"          # none | running | done
K_LAST_POLL = "last_poll_at"
K_LAST_ERROR = "last_error"


# ---------------------------------------------------------------- state ---
def get_state(key: str, default: str | None = None) -> str | None:
    with issuetrack_session() as db:
        row = db.get(SyncState, key)
        return row.value if row and row.value is not None else default


def set_state(key: str, value: str | None) -> None:
    with issuetrack_session() as db:
        row = db.get(SyncState, key)
        if row is None:
            row = SyncState(key=key)
            db.add(row)
        row.value = value
        row.updated_at = datetime.utcnow()
        db.commit()


def data_version() -> tuple[int, int, int]:
    """이슈 데이터의 '판번호'. 값이 바뀌었을 때만 화면을 다시 그리려고 쓴다.

    (이슈 수, 이벤트 수, 최대 journal id) — 전부 인덱스만 보므로 싸다.
    """
    try:
        with issuetrack_session() as db:
            ni = db.scalar(select(func.count()).select_from(Issue)) or 0
            nj = db.scalar(select(func.count()).select_from(Journal)) or 0
            mj = db.scalar(select(func.max(Journal.id))) or 0
        return int(ni), int(nj), int(mj)
    except Exception:  # noqa: BLE001
        return (-1, -1, -1)


def status() -> dict:
    """화면·로그용 요약."""
    with issuetrack_session() as db:
        issues = db.scalar(select(func.count()).select_from(Issue)) or 0
        pending = db.scalar(
            select(func.count()).select_from(Issue).where(
                Issue.detail_fetched.is_(False), Issue.excluded.is_(False)
            )
        ) or 0
        journals = db.scalar(select(func.count()).select_from(Journal)) or 0
    return {
        "issues": issues,
        "detail_pending": pending,
        "journals": journals,
        "backfill": get_state(K_BACKFILL, "none"),
        "last_seen": get_state(K_LAST_SEEN),
        "last_poll": get_state(K_LAST_POLL),
        "last_error": get_state(K_LAST_ERROR),
        "running": _sync_lock.locked(),
    }


# ----------------------------------------------------------------- rules ---
def is_test_subject(subject: str | None) -> bool:
    """제목이 [Test] 로 시작하면 기능 검증용 이슈.

    수집·SB 생성·이력은 평소대로 하고 **통계에서만 뺀다**. 테스트 몇 건이
    소요시간 중앙값이나 월별 건수를 흔들면 통계를 믿을 수 없게 된다.
    """
    return (subject or "").lstrip().lower().startswith("[test]")


def exclusion_for(subject: str | None) -> str | None:
    """제외 사유 문자열, 대상이 아니면 None."""
    from app.issuetrack import settings

    s = (subject or "").lower()
    for kw in settings.exclude_list():
        if kw in s:
            return f"제외 키워드: {kw}"
    return None


# ----------------------------------------------------------------- upsert --
def _name(obj: Any) -> str | None:
    return (obj or {}).get("name") if isinstance(obj, dict) else None


def _rid(obj: Any) -> int | None:
    v = (obj or {}).get("id") if isinstance(obj, dict) else None
    return int(v) if v is not None else None


def _apply_summary(row: Issue, src: dict) -> None:
    """목록/상세 공통 필드를 이슈 행에 반영."""
    row.project_id = _rid(src.get("project"))
    row.project_name = _name(src.get("project"))
    row.tracker = _name(src.get("tracker"))
    row.status = _name(src.get("status"))
    row.status_id = _rid(src.get("status"))
    row.priority = _name(src.get("priority"))
    row.priority_id = _rid(src.get("priority"))
    row.category = _name(src.get("category"))
    row.author_id = _rid(src.get("author"))
    row.author_name = _name(src.get("author"))
    row.assigned_to_id = _rid(src.get("assigned_to"))
    row.assigned_to_name = _name(src.get("assigned_to"))
    row.subject = (src.get("subject") or "")[:500]
    if src.get("description") is not None:
        row.description = src.get("description")
    row.done_ratio = src.get("done_ratio")
    row.start_date = src.get("start_date")
    row.due_date = src.get("due_date")
    row.created_on = parse_dt(src.get("created_on"))
    row.updated_on = parse_dt(src.get("updated_on"))
    row.closed_on = parse_dt(src.get("closed_on"))
    cf = src.get("custom_fields")
    if cf:
        row.custom_fields_json = json.dumps(cf, ensure_ascii=False)
        for f in cf:
            if f.get("id") is not None and f.get("name"):
                _cf_seen.setdefault(int(f["id"]), str(f["name"]))

    reason = exclusion_for(row.subject)
    row.excluded = reason is not None
    row.excluded_reason = reason
    row.is_test = is_test_subject(row.subject)
    row.synced_at = datetime.utcnow()


def _upsert_summary(db, src: dict) -> tuple[Issue, bool]:
    """목록 행 반영. (row, 상세 재수집 필요 여부) 반환."""
    iid = int(src["id"])
    row = db.get(Issue, iid)
    created = row is None
    if created:
        row = Issue(id=iid)
        db.add(row)
    _apply_summary(row, src)
    stale = (
        created
        or not row.detail_fetched
        or row.detail_synced_updated_on != row.updated_on
    )
    return row, stale


def _replace_journals(db, issue_id: int, journals: list[dict]) -> int:
    """이슈의 이벤트를 통째로 교체. Redmine 에서 코멘트가 수정/삭제될 수 있어 병합보다 안전."""
    for j in db.query(Journal).filter(Journal.issue_id == issue_id).all():
        db.delete(j)
    db.flush()
    n = 0
    for j in journals or []:
        jr = Journal(
            id=int(j["id"]),
            issue_id=issue_id,
            user_id=_rid(j.get("user")),
            user_name=_name(j.get("user")),
            created_on=parse_dt(j.get("created_on")),
            notes=j.get("notes") or None,
            private_notes=bool(j.get("private_notes")),
        )
        db.add(jr)
        for seq, d in enumerate(j.get("details") or []):
            db.add(
                JournalDetail(
                    journal_id=jr.id,
                    seq=seq,
                    property=d.get("property"),
                    name=d.get("name"),
                    old_value=d.get("old_value"),
                    new_value=d.get("new_value"),
                )
            )
        n += 1
    return n


def _replace_attachments(db, issue_id: int, atts: list[dict]) -> None:
    for a in db.query(Attachment).filter(Attachment.issue_id == issue_id).all():
        db.delete(a)
    db.flush()
    for a in atts or []:
        db.add(
            Attachment(
                id=int(a["id"]),
                issue_id=issue_id,
                filename=(a.get("filename") or "")[:300],
                filesize=a.get("filesize"),
                content_type=(a.get("content_type") or "")[:120] or None,
                author_name=_name(a.get("author")),
                created_on=parse_dt(a.get("created_on")),
            )
        )


def _fetch_detail(db, client: RedmineClient, issue_id: int) -> int:
    """이슈 상세를 받아 journals/attachments 까지 반영. 저장한 journal 수 반환."""
    src = client.issue_detail(issue_id)
    if not src:
        return 0
    row = db.get(Issue, issue_id)
    if row is None:
        row = Issue(id=issue_id)
        db.add(row)
    _apply_summary(row, src)
    n = _replace_journals(db, issue_id, src.get("journals") or [])
    _replace_attachments(db, issue_id, src.get("attachments") or [])
    row.detail_fetched = True
    row.detail_synced_updated_on = row.updated_on
    return n


# ---------------------------------------------------------------- lookups --
def sync_lookups(client: RedmineClient) -> None:
    """id→이름 매핑 갱신. journal detail 이 '97 → 207' 처럼 숫자만 주기 때문에 필요."""
    pairs: list[tuple[str, int, str]] = []
    for kind, rows in client.enumerations().items():
        for r in rows:
            if r.get("id") is not None and r.get("name"):
                pairs.append((kind, int(r["id"]), str(r["name"])))
    for m in client.memberships():
        u = m.get("user") or {}
        if u.get("id") and u.get("name"):
            pairs.append(("user", int(u["id"]), str(u["name"])))

    if not pairs:
        return
    with issuetrack_session() as db:
        existing = {(x.kind, x.ref_id): x for x in db.query(Lookup).all()}
        for kind, rid, name in pairs:
            cur = existing.get((kind, rid))
            if cur is None:
                db.add(Lookup(kind=kind, ref_id=rid, name=name))
            elif cur.name != name:
                cur.name = name
        db.commit()


def _flush_cf_lookups() -> None:
    """수집 중에 주워담은 커스텀 필드 이름을 Lookup 에 반영."""
    if not _cf_seen:
        return
    with issuetrack_session() as db:
        have = {
            x.ref_id for x in db.query(Lookup).filter(Lookup.kind == "cf").all()
        }
        for rid, name in _cf_seen.items():
            if rid not in have:
                db.add(Lookup(kind="cf", ref_id=rid, name=name))
        db.commit()


def lookup_map(kind: str) -> dict[str, str]:
    """{'207': '박다몬'} — journal detail 값이 문자열이라 키도 문자열로 준다."""
    with issuetrack_session() as db:
        return {
            str(x.ref_id): x.name
            for x in db.query(Lookup).filter(Lookup.kind == kind).all()
        }


# --------------------------------------------------------------- backfill --
ProgressCb = Callable[[str, int, int], None]


def backfill(*, progress: ProgressCb | None = None, project: str = PROJECT) -> dict:
    """전수 수집. 이미 받은 이슈는 건너뛰므로 여러 번 불러도 안전(이어받기)."""
    if not _sync_lock.acquire(blocking=False):
        return {"skipped": True, "reason": "이미 동기화가 진행 중입니다."}
    try:
        init_issuetrack_db()
        client = RedmineClient()
        set_state(K_BACKFILL, "running")
        set_state(K_LAST_ERROR, None)
        sync_lookups(client)

        # 1단계 — 목록으로 골격 채우기
        listed = 0
        newest: datetime | None = None
        with issuetrack_session() as db:
            for src in client.iter_issues(project=project):
                row, _ = _upsert_summary(db, src)
                if row.updated_on and (newest is None or row.updated_on > newest):
                    newest = row.updated_on
                listed += 1
                if listed % 200 == 0:
                    db.commit()
                    db.expunge_all()
                    if progress:
                        progress("list", listed, 0)
            db.commit()
        if progress:
            progress("list", listed, listed)

        # 2단계 — 상세(journals) 채우기. 제외 대상은 건너뛴다.
        with issuetrack_session() as db:
            todo = [
                i for (i,) in db.execute(
                    select(Issue.id).where(
                        Issue.excluded.is_(False),
                        (Issue.detail_fetched.is_(False))
                        | (Issue.detail_synced_updated_on != Issue.updated_on),
                    ).order_by(Issue.id)
                ).all()
            ]

        done = journals = failed = 0
        with issuetrack_session() as db:
            for iid in todo:
                try:
                    journals += _fetch_detail(db, client, iid)
                except RedmineError as e:
                    failed += 1
                    log.warning("이슈 #%s 상세 수집 실패: %s", iid, e)
                    db.rollback()
                done += 1
                if done % 50 == 0:
                    db.commit()
                    # 1,600여 건을 한 세션으로 처리하므로 identity map 을 비워
                    # 메모리가 계속 불어나지 않게 한다.
                    db.expunge_all()
                    if progress:
                        progress("detail", done, len(todo))
            db.commit()
        if progress:
            progress("detail", done, len(todo))

        _flush_cf_lookups()
        if newest:
            set_state(K_LAST_SEEN, newest.isoformat())
        set_state(K_BACKFILL, "done")
        set_state(K_LAST_POLL, datetime.utcnow().isoformat())
        result = {
            "listed": listed,
            "detail_done": done,
            "detail_failed": failed,
            "journals": journals,
        }
        log.info("Redmine 백필 완료: %s", result)
        return result
    except RedmineError as e:
        set_state(K_BACKFILL, "none")
        set_state(K_LAST_ERROR, str(e))
        log.error("Redmine 백필 실패: %s", e)
        return {"error": str(e)}
    finally:
        _sync_lock.release()


# ------------------------------------------------------------------- poll --
def poll(*, project: str = PROJECT) -> dict:
    """증분 수집. 백필이 끝나지 않았으면 백필로 넘긴다."""
    if get_state(K_BACKFILL) != "done":
        return backfill(project=project)

    if not _sync_lock.acquire(blocking=False):
        return {"skipped": True, "reason": "이미 동기화가 진행 중입니다."}
    try:
        client = RedmineClient()
        last_seen = _parse_iso(get_state(K_LAST_SEEN))
        since = (last_seen - POLL_OVERLAP) if last_seen else None

        changed = 0
        newest = last_seen
        seen = 0
        with issuetrack_session() as db:
            for src in client.iter_issues(project=project, updated_since=since):
                _, stale = _upsert_summary(db, src)
                seen += 1
                row_updated = parse_dt(src.get("updated_on"))
                if row_updated and (newest is None or row_updated > newest):
                    newest = row_updated
                if stale:
                    changed += 1
            db.commit()

        # 상세 대상은 방금 훑은 목록이 아니라 **DB 의 stale 플래그**에서 뽑는다.
        # 상한에 걸려 밀린 이슈가 다음 폴링의 시간창을 벗어나 영영 누락되는 걸 막는다.
        stale_where = (
            Issue.excluded.is_(False),
            (Issue.detail_fetched.is_(False))
            | (Issue.detail_synced_updated_on != Issue.updated_on),
        )
        with issuetrack_session() as db:
            total_stale = db.scalar(
                select(func.count()).select_from(Issue).where(*stale_where)
            ) or 0
            todo = [
                i for (i,) in db.execute(
                    select(Issue.id).where(*stale_where)
                    .order_by(Issue.updated_on.desc())
                    .limit(POLL_DETAIL_CAP)
                ).all()
            ]
        deferred = max(0, total_stale - len(todo))

        journals = failed = 0
        with issuetrack_session() as db:
            for iid in todo:
                try:
                    journals += _fetch_detail(db, client, iid)
                except RedmineError as e:
                    failed += 1
                    log.warning("이슈 #%s 상세 수집 실패: %s", iid, e)
                    db.rollback()
            db.commit()

        _flush_cf_lookups()
        if newest:
            set_state(K_LAST_SEEN, newest.isoformat())
        set_state(K_LAST_POLL, datetime.utcnow().isoformat())
        set_state(K_LAST_ERROR, None)

        result = {
            "scanned": seen,
            "changed": changed,
            "detail_done": len(todo),
            "detail_failed": failed,
            "journals": journals,
        }
        if deferred:
            result["deferred"] = deferred
        if seen:
            log.info("Redmine 폴링: %s", result)
        return result
    except RedmineError as e:
        set_state(K_LAST_ERROR, str(e))
        log.error("Redmine 폴링 실패: %s", e)
        return {"error": str(e)}
    finally:
        _sync_lock.release()


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None
