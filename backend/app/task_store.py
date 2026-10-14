"""작업 단계 저장소 — 메일 작업과 제품 작업(/osupdate)의 공통 인터페이스.

작업 보드 화면(task_board.render_side)은 단계를 읽고 쓰는 일만 하고, 그 단계가
`mail_stages` 에 있는지 `os_update_job_stages` 에 있는지는 신경 쓰지 않는다.
그래서 갈래(Submission/Approval)·검수 팝업·재작업 UI 를 두 경로가 그대로 공유한다.

`key` 는 파이프라인 공유 상태 키다 — 메일 작업은 메일 id, 제품 작업은 'job-N'.
파이프라인 함수들은 이 값을 불투명한 키로만 쓰므로 둘 다 그대로 통한다.
"""
from __future__ import annotations

import os
import re

from app import os_update_jobs as JOBS
from app import stages as S

# 단계 실행 결과 텍스트에 찍힌 산출물 경로. 단계마다 결과를 사람이 읽는 글로 남기는데,
# 그 안의 파일 경로를 뽑아 다운로드·결과물 모음에 쓴다.
_PATH_RE = re.compile(
    r"[A-Za-z]:\\[^\n:*?\"<>|]+?\.(?:pdf|xlsx|xls|docx|doc|csv|zip|dat|png|txt|html?)")


def result_files(result_text: str, artifact_path: str = "") -> list[str]:
    """실행 결과 글 + 지정 산출물에서 **실제로 존재하는** 파일 경로만 추려 반환."""
    found: list[str] = []
    for m in _PATH_RE.findall(result_text or ""):
        p = m.strip().rstrip(").,")
        if os.path.exists(p) and p not in found:
            found.append(p)
    if artifact_path and os.path.exists(artifact_path) and artifact_path not in found:
        found.append(artifact_path)
    return found


class MailStageStore:
    """메일 한 통(정확히는 Submission↔Approval 짝) = 작업. 기존 메일함 경로."""

    kind = "mail"

    def __init__(self, mail_id: int, user_id: int) -> None:
        self.key = mail_id
        self.user_id = user_id

    def stages(self, side: str):
        return S.get_or_create_stages(self.key, self.user_id, side)

    def next_pending(self, side: str):
        return S.next_pending_stage(self.key, self.user_id, side)

    get = staticmethod(S.get_stage)
    set_status = staticmethod(S.set_stage_status)
    set_input = staticmethod(S.set_stage_input)
    claim = staticmethod(S.try_claim_stage)
    save_result = staticmethod(S.save_stage_result)


class JobStageStore:
    """제품 작업(제품 SKU + 버전) = 작업. /osupdate 경로."""

    kind = "job"

    def __init__(self, job_id: int) -> None:
        self.job_id = job_id
        self.key = JOBS.state_key(job_id)

    def stages(self, side: str):
        return JOBS.get_or_create_stages(self.job_id, side)

    def next_pending(self, side: str):
        return JOBS.next_pending_stage(self.job_id, side)

    get = staticmethod(JOBS.get_stage)
    set_status = staticmethod(JOBS.set_stage_status)
    set_input = staticmethod(JOBS.set_stage_input)
    claim = staticmethod(JOBS.try_claim_stage)
    save_result = staticmethod(JOBS.save_stage_result)


def stage_status(key, stage_key: str) -> str:
    """상태 키(메일 id 또는 'job-N')로 Submission 단계의 상태를 읽는다. 없으면 ''."""
    from app.database import get_session
    from app.models import MailStage, OsUpdateJobStage

    is_job = isinstance(key, str) and str(key).startswith("job-")
    with get_session() as db:
        if is_job:
            try:
                job_id = int(str(key).split("-", 1)[1])
            except ValueError:
                return ""
            row = (db.query(OsUpdateJobStage)
                   .filter_by(job_id=job_id, stage_key=stage_key,
                              side=S.SIDE_SUBMISSION).first())
        else:
            try:
                mail_id = int(key)
            except (TypeError, ValueError):
                return ""
            row = db.query(MailStage).filter_by(mail_id=mail_id,
                                                stage_key=stage_key).first()
        return str(row.status) if row is not None else ""


def approval_stage_status(key, stage_key: str) -> str:
    """상태 키로 **Approval 갈래** 단계의 상태를 읽는다(메일 작업은 갈래 구분이 없어 그 단계). 없으면 ''."""
    from app.database import get_session
    from app.models import MailStage, OsUpdateJobStage

    is_job = isinstance(key, str) and str(key).startswith("job-")
    with get_session() as db:
        if is_job:
            try:
                job_id = int(str(key).split("-", 1)[1])
            except ValueError:
                return ""
            row = (db.query(OsUpdateJobStage)
                   .filter_by(job_id=job_id, stage_key=stage_key,
                              side=S.SIDE_APPROVAL).first())
        else:
            try:
                mail_id = int(key)
            except (TypeError, ValueError):
                return ""
            row = db.query(MailStage).filter_by(mail_id=mail_id,
                                                stage_key=stage_key).first()
        return str(row.status) if row is not None else ""


def complete_stage(key, stage_key: str, actor: str = "") -> bool:
    """상태 키만 알고 있을 때 그 작업의 특정 단계를 **완료**로 표시한다.

    RN 승인 화면(/reviews)은 작업 보드를 거치지 않고 열리므로 store·stage_id 를 들고
    있지 않다. 그런데 승인이 곧 마지막 결재라 단계도 그때 끝나야 한다. 그래서 키로
    단계를 직접 찾는다. key = 메일 id(int) 또는 'job-N'.
    """
    from app.database import get_session
    from app.models import MailStage, OsUpdateJobStage

    is_job = isinstance(key, str) and str(key).startswith("job-")
    with get_session() as db:
        if is_job:
            try:
                job_id = int(str(key).split("-", 1)[1])
            except ValueError:
                return False
            row = (db.query(OsUpdateJobStage)
                   .filter_by(job_id=job_id, stage_key=stage_key,
                              side=S.SIDE_SUBMISSION).first())
        else:
            try:
                mail_id = int(key)
            except (TypeError, ValueError):
                return False
            # 메일 단계는 갈래 열이 없다 — release_note 는 Submission 에만 있으므로
            # stage_key 하나로 특정된다.
            row = db.query(MailStage).filter_by(mail_id=mail_id,
                                                stage_key=stage_key).first()
        if row is None:
            return False
        row.status = S.DONE
        if actor:
            row.updated_by = actor
        db.commit()
    return True


def progress_key(store, stage_id: int):
    """진행상황 저장소 키.

    두 테이블의 id 는 서로 겹치므로(메일 단계 3번 · 작업 단계 3번), 제품 작업은
    문자열로 네임스페이스를 붙여 남의 진행률을 덮어쓰지 않게 한다.
    """
    return stage_id if store.kind == "mail" else f"job:{stage_id}"
