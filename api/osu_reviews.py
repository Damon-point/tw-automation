"""
OS Update 결재 — RN 승인(1차) · 최종 검수(2차) · 뉴스레터 결재. 기존 화면의 순서를 그대로 옮긴다.

  요청: 결재자 전원에게 요청 메일 → 상태 'requested'
  결정: (1차) 승인본 사본 고정 → 상태 저장 → 전체 이력 PDF 확정·.104 보관·단계 완료 → 요청자에게 결과 메일
        (2차) 결과 메일 → 상태 저장 → for Sales PDF 확정·.160 보관·단계 완료
        (뉴스레터) 결과 메일 → 상태 저장 → Submission 뉴스레터 단계 완료
메일은 3080 의 검증 모드에서는 보내지 않고 기록만 한다(app.shadow).
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime

from app import mail_templates as MT
from app import os_update_jobs as J
from app import stages as S
from app import user_mailer
from app.pipeline import history as H
from app.pipeline.state import get_state, update_state
from app.task_store import JobStageStore

KINDS = {
    "mid": {"state_key": "rn_mid_review", "label": "Release Note", "verb": "승인", "role": "RN 승인자",
            "roles": ("mid_reviewer", "admin"), "stage": "release_note_full"},
    "final": {"state_key": "rn_review", "label": "Release Note for Sales", "verb": "검수", "role": "최종 승인자",
              "roles": ("final_reviewer", "admin"), "stage": "release_note"},
    "newsletter": {"state_key": "newsletter_review", "label": "Newsletter", "verb": "검수", "role": "최종 승인자",
                   "roles": ("final_reviewer", "admin"), "stage": "newsletter"},
}


class ReviewError(Exception):
    pass


def _eul(word: str) -> str:
    ch = word[-1]
    if "가" <= ch <= "힣":
        return "을" if (ord(ch) - 0xAC00) % 28 else "를"
    return "을(를)"


def state_of(job_id: int) -> dict:
    st = get_state(J.state_key(job_id)) or {}
    out = {}
    for k, spec in KINDS.items():
        r = st.get(spec["state_key"]) or {}
        out[k] = {"status": r.get("status") or "none", "requester": r.get("requester_name", ""),
                  "requested_at": r.get("requested_at", ""), "decided_by": r.get("decided_by", ""),
                  "decided_at": r.get("decided_at", ""), "note": r.get("note", "")}
    return out


def _recipients(kind: str) -> list[str]:
    return user_mailer.mid_reviewer_emails() if kind == "mid" else user_mailer.final_reviewer_emails()


def request(job_id: int, kind: str, user: dict) -> dict:
    spec = KINDS.get(kind)
    if not spec:
        raise ReviewError("모르는 결재 종류")
    key = J.state_key(job_id)
    st = get_state(key) or {}
    cur = (st.get(spec["state_key"]) or {}).get("status") or "none"
    if cur not in ("none", "rejected"):
        raise ReviewError("이미 요청됐거나 끝난 결재입니다")
    if kind == "final" and (st.get("rn_mid_review") or {}).get("status") != "approved" and not st.get("rn_full_skipped"):
        raise ReviewError("1차 RN 승인이 먼저입니다")
    to = _recipients(kind)
    if not to:
        raise ReviewError(f"{spec['role']} 이메일이 등록돼 있지 않습니다")
    product = st.get("product") or ""
    version = st.get("version_full") or st.get("os_version") or ""
    subject, body = MT.render(
        "review_request", 요청종류=spec["verb"], 제품=product, 버전=version, 대상=spec["label"],
        요청자=user["username"], 조사=_eul(spec["verb"]),
        안내=f"▶ 앱에 로그인해 해당 작업의 {spec['label']} 결재 화면에서 확인 후 결정을 부탁드립니다.")
    ok, err = user_mailer.send_as_user(user["id"], to, subject.strip(), body)
    if not ok:
        raise ReviewError(f"요청 메일 발송 실패: {err}")
    update_state(key, **{spec["state_key"]: {
        "status": "requested", "requester_id": user["id"], "requester_email": user_mailer.user_email(user["id"]),
        "requester_name": user["username"], "requested_at": datetime.now().isoformat(), "admins": to}})
    return {"ok": True, "message": f"{spec['role']} {len(to)}명에게 {spec['verb']} 요청을 보냈습니다"}


def can_decide(kind: str, user: dict) -> bool:
    return user.get("role") in KINDS[kind]["roles"]


def decide(job_id: int, kind: str, approve: bool, note: str, user: dict) -> dict:
    spec = KINDS.get(kind)
    if not spec:
        raise ReviewError("모르는 결재 종류")
    if not can_decide(kind, user):
        raise ReviewError(f"{spec['role']} 만 결정할 수 있습니다")
    key = J.state_key(job_id)
    st = get_state(key) or {}
    review = st.get(spec["state_key"]) or {}
    if review.get("status") != "requested":
        raise ReviewError("요청된 결재가 아닙니다")
    note = (note or "").strip()
    if not approve and not note:
        raise ReviewError("반려 사유를 적으세요")
    verdict = "승인" if approve else "반려"
    product = st.get("product") or ""
    version = st.get("version_full") or st.get("os_version") or ""
    notes: list[str] = []
    decided = {**review, "status": "approved" if approve else "rejected",
               "decided_by": user["username"], "decided_at": datetime.now().isoformat(), "note": note}

    if kind == "mid":
        H.record(key, "rn", f"RN 승인 {verdict}", actor=user["username"], summary=note or "승인 결과 기록",
                 text=H.text_from_rn(st))
        if approve:
            src = st.get("rn_full_docx_path") or st.get("rn_docx_path") or ""
            if src and os.path.exists(src):
                dst = os.path.join(os.path.dirname(src), f"midreviewed_{os.path.basename(src)}")
                shutil.copyfile(src, dst)
                update_state(key, rn_mid_reviewed_path=dst, rn_mid_reviewed_name=os.path.basename(dst))
        update_state(key, **{spec["state_key"]: decided})
        if approve:
            from app.pages.mid_review import _finalize_after_approval

            notes += [m for m, _c in _finalize_after_approval(key, user["id"], user["username"]).get("notes", [])]
        req = (review.get("requester_email") or "").strip()
        if req:
            subject, body = MT.render(
                "mid_review_result", 제품=product, 버전=version, 결과=verdict, 처리자=user["username"],
                메모이름="메모" if approve else "반려 사유", 메모=note or "-",
                다음안내=("▶ History 관리용 RN(전체 이력본)을 PDF 로 확정하고 파일서버에 저장했습니다."
                      if approve else "▶ 위 사유를 확인해 수정한 뒤 다시 RN 승인을 요청해 주세요."))
            ok, err = user_mailer.send_as_user(user["id"], req, subject.strip(), body, cc=user_mailer.mid_reviewer_emails())
            if not ok:
                notes.append(f"결정은 저장했지만 알림 메일 발송 실패 — {err}")
        return {"ok": True, "message": f"RN 승인 {verdict}", "notes": notes}

    # 2차 · 뉴스레터
    req = review.get("requester_email") or ""
    if not req:
        raise ReviewError("요청자 이메일이 없어 결과를 보낼 수 없습니다")
    first = (f"요청하신 {product} {version} {spec['label']} "
             f"{'' if spec['verb'] == verdict else spec['verb'] + ' '}건이 {verdict}되었습니다.").replace("  ", " ")
    subject, body = MT.render("review_result", 요청종류=spec["verb"], 결과=verdict, 제품=product, 버전=version,
                              대상=spec["label"], 첫줄=first, 처리자=user["username"], 메모=note or "-")
    ok, err = user_mailer.send_as_user(user["id"], req, subject.strip(), body)
    if not ok:
        raise ReviewError(f"결과 메일 발송 실패: {err}")
    update_state(key, **{spec["state_key"]: decided})
    if kind == "newsletter":
        from app.pipeline import newsletter_ws as NLWS

        H.record(key, "newsletter", f"최종 {verdict}", actor=user["username"], summary=note,
                 text=H.text_from_newsletter(NLWS.get(key) or {}))
        if approve:
            store = JobStageStore(job_id)
            for r in store.stages(S.SIDE_SUBMISSION):
                if r.stage_key == "newsletter":
                    store.set_status(r.id, S.DONE, user["username"])
    else:
        H.record(key, "rn", f"최종 {verdict}", actor=user["username"], summary=note, text=H.text_from_rn(get_state(key) or {}))
        if approve:
            from app.pages.mid_review import _finalize_after_final_approval

            notes += [m for m, _c in _finalize_after_final_approval(key, user["id"], user["username"]).get("notes", [])]
    return {"ok": True, "message": f"{spec['label']} {verdict}", "notes": notes}
