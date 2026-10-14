"""메일(작업)의 처리 단계 정의 및 상태 로직.

[FIH][Approval] 메일 한 통 = 하나의 '작업'. 처리 파이프라인(개발보고서 단계 ①~⑥)의
per-작업 단계들로 처리한다. (메일 동기화·확인은 작업 시작 신호로, 메일함 자체이므로 번호 단계에 포함하지 않는다.)
진행 방식: 단계 실행 승인 → 실행 → '검수 대기' → 사람이 검수 확인 → (다음 단계는 다시 승인).
"""
from __future__ import annotations

from app.database import get_session
from app.models import MailStage

# (stage_key, 표시 이름) — 순서 유지.
# 이름 앞에 번호(①②③…)를 붙이지 않는다. 갈래마다 수행하는 단계가 달라(Submission 3개 /
# Approval 7개) 번호가 화면에서 건너뛰어 보였고, 부를 때도 'Approval 의 Release Note 제작'
# 처럼 갈래+이름으로 말하는 편이 정확하다. 순서는 이 목록의 나열 순서 그대로다.
STAGES: list[tuple[str, str]] = [
    ("os_download", "OS Download"),
    # Release Note 는 문서가 둘이고 결재도 둘이라 **단계를 갈라 둔다**(2026-08-27).
    #   · 전체 이력 RN 제작 — 리서치·선별표·이력본. RN 승인(1차)으로 끝난다.
    #   · RN for Sales 제작 — 그 확정본에서 공개할 것만 골라 만든다. 최종 검수로 끝난다.
    # 순서가 곧 의존이다. 전체 이력이 확정돼야 그 내용으로 Sales 를 만든다.
    # release_note 키는 **RN for Sales 쪽이 물려받는다** — 이미 쌓인 이력·완료 기록이
    # 이 키에 달려 있고, 그 키의 완료 시점(최종 검수)도 그대로이기 때문이다.
    ("release_note_full", "전체 이력 RN 제작"),
    ("release_note", "RN for Sales 제작"),
    ("dropbox_release", "Dropbox 업로드"),
    ("excel_docs", "FOTA 업로드"),
    ("service_portal", "Service Portal 편집·업로드"),
    ("release_note_edit", "Knowledge Base 편집"),
    ("newsletter", "Newsletter 제작"),
    ("dropbox_cleanup", "Dropbox 정리 (구버전 삭제)"),
]
STAGE_LABELS: dict[str, str] = dict(STAGES)
STAGE_KEYS: list[str] = [k for k, _ in STAGES]

# ── Submission = 준비 / Approval = 실행 ────────────────────────────────────
# Submission(먼저 오는 메일): Approval 때 배포·업로드할 것을 **미리 만들어 두는 준비 단계**.
#   ②⑥⑦(승인 절차가 필요한 문서·문안)만 수행하고, 산출물은 **로컬에만 저장**한다.
#   외부(Dropbox·FOTA·서비스포털·지식베이스·Mailchimp)에는 아무것도 쓰지 않는다.
# Approval(나중에 오는 메일): **①~⑧ 전 과정**을 여기서 진행한다(실제 다운로드·업로드·게시·발송).
#   ②⑥⑦ 은 Submission 에서 승인받은 산출물을 **그대로 사용**하고 다시 만들지 않는다.
SIDE_SUBMISSION = "submission"
SIDE_APPROVAL = "approval"

SUBMISSION_STAGE_KEYS: list[str] = ["release_note_full", "release_note",
                                    "release_note_edit", "newsletter"]
# Approval: ② RN 제작은 하지 않는다 — Submission 에서 제작을 끝냈고, Approval 은 그 산출물을
# 바로 ③ Dropbox 에 올리기만 한다. 그래서 Approval 단계에서 RN 제작 두 단계는 아예 뺀다.
RN_STAGE_KEYS: tuple[str, str] = ("release_note_full", "release_note")
APPROVAL_STAGE_KEYS: list[str] = [k for k in STAGE_KEYS if k not in RN_STAGE_KEYS]

# Approval 에서 '승인본 재사용'으로 처리하는 단계(다시 생성하지 않는다).
# ② 는 Approval 에 없으므로 제외. ⑥ KB 편집·⑦ Newsletter 는 Submission 승인본을 그대로 쓴다.
REUSE_ON_APPROVAL: set[str] = {"release_note_edit", "newsletter"}
SIDE_STAGE_KEYS: dict[str, list[str]] = {
    SIDE_SUBMISSION: SUBMISSION_STAGE_KEYS,
    SIDE_APPROVAL: APPROVAL_STAGE_KEYS,
}
SIDE_LABELS = {SIDE_SUBMISSION: "Submission", SIDE_APPROVAL: "Approval"}

# Approval 갈래에서만 다르게 보이는 단계 이름(제작은 Submission 에서 끝났으므로 업로드·발송만).
_APPROVAL_LABEL_OVERRIDE = {
    "release_note_edit": "Knowledge Base 업로드",
    "newsletter": "Newsletter 발송",
}


def stage_label(key: str, side: str | None = None) -> str:
    """단계 표시 이름. Approval 에서는 일부 단계가 다른 이름으로 보인다."""
    if side == SIDE_APPROVAL and key in _APPROVAL_LABEL_OVERRIDE:
        return _APPROVAL_LABEL_OVERRIDE[key]
    return STAGE_LABELS.get(key, key)


def stage_keys_for(side: str | None) -> list[str]:
    """해당 갈래가 수행하는 단계 키 목록. side 가 없으면 전체(하위호환)."""
    return SIDE_STAGE_KEYS.get(side or "", STAGE_KEYS)

# 단계 상태값
PENDING = "pending"          # 대기
RUNNING = "running"          # 실행 중
REVIEW = "review"            # 검수 대기 (실행 완료, 사람 확인 필요)
DONE = "done"                # 완료 (검수됨)

STAGE_STATUS_LABELS = {
    PENDING: "대기",
    RUNNING: "실행 중",
    REVIEW: "검수 대기",
    DONE: "완료",
}
STAGE_STATUS_COLORS = {
    PENDING: "grey-6",
    RUNNING: "blue-7",
    REVIEW: "orange-8",
    DONE: "green-8",
}

# 메일 전체 상태 (배지)
OV_PENDING = "pending"
OV_IN_PROGRESS = "in_progress"
OV_DONE = "done"
OVERALL_LABELS = {
    OV_PENDING: "처리 전",
    OV_IN_PROGRESS: "처리 중",
    OV_DONE: "처리 완료",
}
OVERALL_COLORS = {
    OV_PENDING: "grey-6",
    OV_IN_PROGRESS: "orange-8",
    OV_DONE: "green-8",
}


def overall_status(statuses: list[str]) -> str:
    if statuses and all(s == DONE for s in statuses):
        return OV_DONE
    if statuses and all(s == PENDING for s in statuses):
        return OV_PENDING
    return OV_IN_PROGRESS


def get_or_create_stages(
    mail_id: int, user_id: int, side: str | None = None
) -> list[MailStage]:
    """해당 메일이 맡은 단계 레코드를 (없으면 생성해) 순서대로 반환.

    side 를 주면 그 갈래(Submission ②⑥⑦ / Approval ①③④⑤⑧)의 단계만 다룬다.
    (예전에 8단계가 모두 생성된 메일은 남은 행을 그냥 무시하고 해당 갈래만 보여준다.)
    """
    keys = stage_keys_for(side)
    with get_session() as db:
        existing = {
            s.stage_key: s
            for s in db.query(MailStage).filter_by(mail_id=mail_id).all()
        }
        created = False
        for key in keys:
            if key not in existing:
                db.add(
                    MailStage(
                        user_id=user_id, mail_id=mail_id, stage_key=key, status=PENDING
                    )
                )
                created = True
        if created:
            db.commit()
        rows = {
            s.stage_key: s
            for s in db.query(MailStage).filter_by(mail_id=mail_id).all()
        }
        return [rows[k] for k in keys if k in rows]


def get_stage(stage_id: int) -> MailStage | None:
    with get_session() as db:
        return db.query(MailStage).filter_by(id=stage_id).first()


def set_stage_status(stage_id: int, status: str, updated_by: str = "") -> None:
    with get_session() as db:
        row = db.query(MailStage).filter_by(id=stage_id).first()
        if row:
            row.status = status
            if updated_by:
                row.updated_by = updated_by
            db.commit()


def try_claim_stage(stage_id: int, updated_by: str = "") -> tuple[bool, str]:
    """단계를 '실행 중'으로 선점한다. 이미 누가 돌리고 있으면 (False, 사유).

    여러 사람이 같은 작업 보드를 보고 있어서, 남의 화면이 갱신되기 전에 같은 단계를
    누르면 ③ 업로드나 ⑧ 삭제가 두 번 실행될 수 있다. 그래서 DB 에서 한 번에
    '대기중일 때만 RUNNING 으로' 바꾸고(UPDATE ... WHERE status != running),
    바뀐 행이 없으면 이미 남이 잡은 것으로 본다.
    """
    with get_session() as db:
        changed = (
            db.query(MailStage)
            .filter(MailStage.id == stage_id, MailStage.status != RUNNING)
            .update({"status": RUNNING, "updated_by": updated_by or ""},
                    synchronize_session=False)
        )
        db.commit()
        if changed:
            return True, ""
        row = db.query(MailStage).filter_by(id=stage_id).first()
        who = (row.updated_by if row else "") or "다른 사용자"
        return False, f"{who} 님이 이미 실행 중입니다."


def set_stage_input(stage_id: int, input_params: str) -> None:
    with get_session() as db:
        row = db.query(MailStage).filter_by(id=stage_id).first()
        if row:
            row.input_params = input_params
            db.commit()


def save_stage_result(
    stage_id: int, result_text: str, artifact_type: str = "", artifact_path: str = "",
    updated_by: str = "",
) -> None:
    """실행 결과를 저장하고 상태를 '검수 대기'로 만든다."""
    with get_session() as db:
        row = db.query(MailStage).filter_by(id=stage_id).first()
        if row:
            row.result_text = result_text
            row.artifact_type = artifact_type
            row.artifact_path = artifact_path
            row.status = REVIEW
            if updated_by:
                row.updated_by = updated_by
            db.commit()


def next_pending_stage(
    mail_id: int, user_id: int, side: str | None = None
) -> MailStage | None:
    """해당 갈래에서 아직 완료되지 않은 첫 단계를 반환 (없으면 None)."""
    for s in get_or_create_stages(mail_id, user_id, side):
        if s.status != DONE:
            return s
    return None
