"""Submission ↔ Approval 짝 맞추기 — 하나의 OS Update 작업 = 메일 두 통.

[FIH][Submission] 이 먼저 오고, 나중에 같은 모델의 [FIH][Approval] 이 온다.
둘을 1:1 로 묶어 하나의 '작업(Task)'으로 다룬다.

짝 규칙(사용자 확정): **모델명 + 시간순**
  · 제목에서 모델명(PM95 · MF52E · XG5 …)을 뽑아 같은 모델끼리 후보로 묶는다.
  · 각 Approval 은 '그보다 앞선 것 중 아직 짝 없는 가장 최근 Submission' 과 연결한다.
  · 남는 Submission = 아직 Approval 대기, 남는 Approval = Submission 없음(단독).

작업 상태(파이프라인 state)는 두 메일이 **공유**해야 하므로, 짝을 만들 때
state 별칭(Approval mail_id → Submission mail_id)을 등록한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime

from app.mail.service import extract_model, is_approval_mail, is_submission_mail

SIDE_SUBMISSION = "submission"
SIDE_APPROVAL = "approval"


def _ts(received_at: str) -> float:
    """메일 Date 헤더 → 정렬용 타임스탬프. 파싱 실패는 맨 뒤(-inf)."""
    if not received_at:
        return float("-inf")
    try:
        dt = parsedate_to_datetime(received_at)
        if dt is not None:
            return dt.timestamp()
    except Exception:  # noqa: BLE001
        pass
    return float("-inf")


@dataclass
class Task:
    """Submission/Approval 한 쌍 = 작업 하나. 둘 중 하나만 있을 수 있다."""

    model: str
    submission: object | None = None   # CachedMail | None
    approval: object | None = None     # CachedMail | None

    @property
    def task_id(self) -> int:
        """작업 식별자 = Submission 우선(먼저 오는 쪽). 상태 공유의 기준 id."""
        m = self.submission or self.approval
        return m.id

    @property
    def sort_ts(self) -> float:
        """목록 정렬용 — 짝에서 가장 최근 메일 시각."""
        return max(
            _ts(getattr(self.submission, "received_at", "") or ""),
            _ts(getattr(self.approval, "received_at", "") or ""),
        )


def build_tasks(mails: list) -> list[Task]:
    """CachedMail 목록 → Submission/Approval 짝(Task) 목록. 최신순 정렬.

    부수효과: 짝이 생기면 state 별칭을 등록해 두 메일이 상태를 공유하게 한다.
    """
    subs: dict[str, list] = {}
    apps: dict[str, list] = {}
    for m in mails:
        model = extract_model(m.subject)
        if not model:
            continue
        if is_submission_mail(m.subject):
            subs.setdefault(model, []).append(m)
        elif is_approval_mail(m.subject):
            apps.setdefault(model, []).append(m)

    tasks: list[Task] = []
    for model in set(subs) | set(apps):
        s_list = sorted(subs.get(model, []), key=lambda m: _ts(m.received_at))
        a_list = sorted(apps.get(model, []), key=lambda m: _ts(m.received_at))
        used: set[int] = set()
        for a in a_list:
            a_ts = _ts(a.received_at)
            # 이 Approval 보다 앞선 것 중 아직 안 쓴 가장 최근 Submission
            cand = [s for s in s_list if s.id not in used and _ts(s.received_at) <= a_ts]
            partner = cand[-1] if cand else None
            if partner is not None:
                used.add(partner.id)
            tasks.append(Task(model=model, submission=partner, approval=a))
        for s in s_list:                      # 아직 Approval 이 안 온 Submission
            if s.id not in used:
                tasks.append(Task(model=model, submission=s, approval=None))

    tasks.sort(key=lambda t: t.sort_ts, reverse=True)

    # 짝이 지어진 작업은 두 메일이 파이프라인 상태를 공유하도록 별칭 등록.
    from app.pipeline.state import set_alias

    for t in tasks:
        if t.submission is not None and t.approval is not None:
            set_alias(t.approval.id, t.submission.id)
    return tasks
