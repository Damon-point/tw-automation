"""메일 DB 정리(prune) — 너무 커지지 않게 오래된 메일을 안전하게 삭제.

규칙(안전 버전):
  - 작업 = MailStage 가 있는 트리거 메일. 모든 단계가 DONE 이면 '완료 작업'.
  - **완료 작업은 최근 KEEP_COMPLETED(기본 5)개만 유지**한다(그보다 오래된 완료 작업은 정리 대상).
  - 삭제 기준일 = (유지할 작업들 = 최근 완료 5개 + 진행 중 작업 전체) 중 **가장 오래된 트리거 메일 날짜**.
  - **안전 버퍼**: 최근 BUFFER_DAYS(기본 90)일 이내 메일은 무조건 보존(배포 메일 등 생존 보장).
    → 실제 삭제 경계 = min(작업기준일, now - 버퍼).
  - 보존: 유지 작업들의 트리거 메일은 절대 삭제하지 않는다. 날짜 파싱 실패 메일도 보존.
  - **보존: OS Update 작업(/osupdate)에 근거 메일로 연결된 것도 절대 삭제하지 않는다.**
    작업의 주체가 제품으로 바뀌면서 MailStage 가 없는 메일도 작업에 쓰인다 — 지우면
    승인일 근거가 사라지고 작업 화면에 '메일 없음'만 남는다.
  - **SeenUidl 은 유지** → 지운 메일이 재동기화로 다시 들어오지 않는다(영구 정리).
  - 완료 작업 수가 KEEP_COMPLETED 이하이면 아무 것도 지우지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from app.database import get_session
from app.models import CachedMail, MailStage
from app.stages import DONE

KEEP_COMPLETED = 5
BUFFER_DAYS = 90


def _mail_dt(received_at: str) -> datetime | None:
    try:
        dt = parsedate_to_datetime(received_at)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:  # noqa: BLE001
        return None


def prune_mails(
    user_id: int, keep_completed: int = KEEP_COMPLETED, buffer_days: int = BUFFER_DAYS
) -> int:
    """오래된 메일을 규칙에 따라 삭제하고, 삭제한 건수를 반환한다."""
    # 팀 공용 보드: 특정 계정이 아니라 전체 메일/단계를 대상으로 정리한다.
    # (user_id 는 호출 호환을 위해 남겨두지만 필터로는 쓰지 않는다.)
    with get_session() as db:
        stage_rows = db.query(MailStage).all()
        if not stage_rows:
            return 0
        statuses: dict[int, list[str]] = {}
        for s in stage_rows:
            statuses.setdefault(s.mail_id, []).append(s.status)

        mails = {m.id: m for m in db.query(CachedMail).all()}

        def mdate(mid: int) -> datetime | None:
            m = mails.get(mid)
            return _mail_dt(m.received_at) if m else None

        completed = [mid for mid, sts in statuses.items() if sts and all(x == DONE for x in sts)]
        not_completed = [mid for mid in statuses if mid not in completed]

        if len(completed) <= keep_completed:
            return 0  # 아직 정리할 만큼 완료 작업이 쌓이지 않음

        _oldest = datetime.min.replace(tzinfo=timezone.utc)
        completed.sort(key=lambda mid: (mdate(mid) or _oldest), reverse=True)
        kept = completed[:keep_completed]

        from app import os_update_jobs as _JOBS

        # 제품 작업에 붙은 근거 메일도 보호한다(MailStage 가 없어도).
        protected = set(kept) | set(not_completed) | set(_JOBS.jobs_by_mail())
        prot_dates = [d for d in (mdate(mid) for mid in protected) if d]
        if not prot_dates:
            return 0
        task_cutoff = min(prot_dates)
        boundary = min(task_cutoff, datetime.now(timezone.utc) - timedelta(days=buffer_days))

        to_delete = [
            mid for mid, m in mails.items()
            if mid not in protected
            and (dt := _mail_dt(m.received_at)) is not None
            and dt < boundary
        ]
        if not to_delete:
            return 0

        # 삭제 대상 메일의 MailStage(있으면) 정리 + 메일 삭제. SeenUidl 은 유지.
        db.query(MailStage).filter(MailStage.mail_id.in_(to_delete)).delete(synchronize_session=False)
        db.query(CachedMail).filter(CachedMail.id.in_(to_delete)).delete(synchronize_session=False)
        db.commit()
        return len(to_delete)
