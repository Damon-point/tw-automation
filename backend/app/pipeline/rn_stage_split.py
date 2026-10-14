"""Release Note 단계를 둘로 가른 뒤, **이미 있던 작업**을 알맞은 단계에 앉힌다.

2026-08-27 부터 Release Note 는 단계가 둘이다.
    전체 이력 RN 제작 (release_note_full) — RN 승인(1차)으로 끝난다
    RN for Sales 제작 (release_note)      — 최종 검수(2차)로 끝난다

`release_note` 키는 **RN for Sales 가 물려받았다**. 그래서 예전 작업들은 새로 생긴
`release_note_full` 행이 비어 있는 채로 남고, 그대로 두면 다 끝난 작업에도 '전체 이력
RN 제작 = 대기' 가 붙어 **완료된 일이 미완료로 되살아난다**(실측: 운영 41건 중 15건).

그래서 한 번만 도는 이관을 둔다. 규칙은 순서대로 본다.

  R0  기존 단계가 이미 완료      → 두 단계 모두 완료. 되살리지 않는다.
  R1  rn_mid_review = approved  → 전체 이력 = 완료, Sales = 기존 상태 그대로.
                                  (1차 승인이 곧 전체 이력 단계의 끝이다)
  R2  그 밖(요청중·반려·기록없음) → 전체 이력 = 기존 상태를 그대로 물려받고,
                                  Sales = 대기. 아직 앞 단계에 있는 작업이다.

**한 번만 돈다.** 표식 파일이 있으면 통째로 건너뛴다 — 새로 생기는 작업은 처음부터
두 단계로 만들어지므로 이관할 것이 없고, 두 번 돌면 그 사이 사람이 진행한 상태를
되돌려 버린다.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.atomic_io import atomic_write_text

#: 이관을 끝냈다는 표식. work/ 에 둔다(DB 를 옮겨도 상태 폴더와 함께 따라다닌다).
_MARKER = Path(__file__).resolve().parent.parent.parent / "work" / "rn_stage_split.json"

OLD_KEY = "release_note"          # 이제 'RN for Sales 제작'
NEW_KEY = "release_note_full"     # 새로 생긴 '전체 이력 RN 제작'


def _mid_status(state_key) -> str:
    from app.pipeline.state import get_state

    st = get_state(state_key) or {}
    return (st.get("rn_mid_review") or {}).get("status") or "none"


def _decide(old_status: str, mid: str) -> tuple[str, str]:
    """(전체 이력 단계 상태, RN for Sales 단계 상태)."""
    from app.stages import DONE, PENDING

    if old_status == DONE:
        return DONE, DONE                 # R0 — 이미 끝난 작업은 되살리지 않는다
    if mid == "approved":
        return DONE, old_status           # R1 — 1차 통과. Sales 단계로 넘어가 있다
    return old_status, PENDING            # R2 — 아직 전체 이력 단계


def plan() -> list[dict]:
    """무엇을 어떻게 바꿀지 **계산만** 한다(dry-run 에서 그대로 쓴다)."""
    from app.database import get_session
    from app.models import MailStage, OsUpdateJobStage
    from app.stages import SIDE_SUBMISSION

    out: list[dict] = []
    with get_session() as db:
        for row in db.query(OsUpdateJobStage).filter_by(stage_key=OLD_KEY).all():
            key = f"job-{row.job_id}"
            mid = _mid_status(key)
            full_st, sales_st = _decide(row.status, mid)
            out.append({"kind": "job", "id": row.job_id, "side": row.side, "key": key,
                        "old": row.status, "mid": mid,
                        "full": full_st, "sales": sales_st})
        for row in db.query(MailStage).filter_by(stage_key=OLD_KEY).all():
            mid = _mid_status(row.mail_id)
            full_st, sales_st = _decide(row.status, mid)
            out.append({"kind": "mail", "id": row.mail_id, "side": SIDE_SUBMISSION,
                        "key": row.mail_id, "old": row.status, "mid": mid,
                        "full": full_st, "sales": sales_st})
    return out


def migrate_once(*, force: bool = False) -> dict:
    """이관 실행. 이미 했으면 아무 것도 하지 않는다."""
    from app.database import get_session
    from app.models import MailStage, OsUpdateJobStage
    from app.stages import PENDING

    if _MARKER.exists() and not force:
        return {"skipped": True, "reason": "이미 이관했습니다"}

    steps = plan()
    moved = {"job": 0, "mail": 0}
    counts = {"R0_done": 0, "R1_sales": 0, "R2_full": 0}

    with get_session() as db:
        for s in steps:
            if s["kind"] == "job":
                old = (db.query(OsUpdateJobStage)
                       .filter_by(job_id=s["id"], side=s["side"], stage_key=OLD_KEY)
                       .first())
                new = (db.query(OsUpdateJobStage)
                       .filter_by(job_id=s["id"], side=s["side"], stage_key=NEW_KEY)
                       .first())
                if new is None:
                    new = OsUpdateJobStage(job_id=s["id"], side=s["side"],
                                           stage_key=NEW_KEY, status=PENDING)
                    db.add(new)
            else:
                old = (db.query(MailStage)
                       .filter_by(mail_id=s["id"], stage_key=OLD_KEY).first())
                new = (db.query(MailStage)
                       .filter_by(mail_id=s["id"], stage_key=NEW_KEY).first())
                if new is None:
                    new = MailStage(user_id=old.user_id, mail_id=s["id"],
                                    stage_key=NEW_KEY, status=PENDING)
                    db.add(new)
            if old is None:
                continue

            # 새 단계는 **기존 행의 내용을 그대로 물려받는다** — 실행 결과·산출물이
            # 리서치와 전체 이력본 얘기라, 그쪽에 있어야 화면이 지금과 같아 보인다.
            new.status = s["full"]
            new.result_text = old.result_text
            new.artifact_type = old.artifact_type
            new.artifact_path = old.artifact_path
            new.input_params = old.input_params
            new.updated_by = old.updated_by

            if s["sales"] == PENDING and old.status != PENDING:
                # R2 — 아직 앞 단계다. Sales 행은 처음 상태로 비운다(결과가 없는 게 맞다).
                old.status = PENDING
                old.result_text = ""
                old.artifact_type = ""
                old.artifact_path = ""
            else:
                old.status = s["sales"]

            moved[s["kind"]] += 1
            if s["old"] == "done":
                counts["R0_done"] += 1
            elif s["mid"] == "approved":
                counts["R1_sales"] += 1
            else:
                counts["R2_full"] += 1
        db.commit()

    res = {"skipped": False, "moved": moved, "rules": counts,
           "at": datetime.now().isoformat(timespec="seconds")}
    _MARKER.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(_MARKER, json.dumps(res, ensure_ascii=False, indent=2))
    return res
