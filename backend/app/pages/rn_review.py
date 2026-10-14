"""검수 워크플로 컴포넌트(관리자 메일 검수) — 검수하기 팝업 안에서 사용.

- 작업자가 '검수 요청' → role=admin 전원에게 메일(From=작업자 본인 계정).
- 관리자가 같은 팝업에서 '승인'/'반려' → 요청 작업자에게 메일(From=관리자 본인 계정).
  승인 시 on_approve() 로 단계 완료, 반려/요청 시 on_close() 로 팝업 갱신.

`render_review_controls` = 단계 무관 공통 컨트롤(Release Note·Newsletter 등이 재사용).
`render_rn_review` = Release Note 전용(공개 선별표 + 공통 컨트롤).
메일은 각 사용자가 [계정 관리]에 등록한 본인 이메일 자격증명으로 발송된다(app.user_mailer).
"""
from __future__ import annotations

import inspect
import os
from datetime import datetime

from nicegui import run, ui

from app import user_mailer
from app.auth import can_final_decide, current_user_id, current_username
from app.pipeline import history as H
from app.pipeline.state import get_state, update_state

_STATUS_LABEL = {
    "none": "요청 전", "requested": "요청됨", "approved": "승인됨", "rejected": "반려됨",
}
_STATUS_COLOR = {
    "requested": "orange-8", "approved": "green-8", "rejected": "red-7", "none": "grey-6",
}


def _eul(word: str) -> str:
    """받침을 보고 '을/를' 을 고른다 — '승인를 요청했습니다' 가 나가고 있었다."""
    w = (word or "").strip()
    if not w:
        return "를"
    last = w[-1]
    if "가" <= last <= "힣":
        return "을" if (ord(last) - 0xAC00) % 28 else "를"
    return "를"


def render_review_controls(
    mail_id, stage_id, me, *, state_key: str, label: str, stage_hint: str,
    on_approve, on_close,
    recipients_fn=None, can_decide_fn=None, role_label: str = "관리자",
    request_hint: str = "", request_verb: str = "검수",
) -> None:
    """검수 컨트롤(요청/승인/반려). state_key 로 단계별 상태를 분리 저장.

    label: 메일 제목/본문에 쓸 대상 이름(예: 'Release Note', 'Newsletter').
    stage_hint: 요청 메일 안내에 쓸 단계 이름(예: 'Release Note', 'Newsletter').
    recipients_fn: 수신자 목록 함수(기본 최종 승인자). RN 승인은 다른 함수를 준다.
    can_decide_fn: 승인/반려 버튼을 볼 수 있는지 판정(기본 최종 승인자).
    role_label: 화면·안내 문구에 쓸 결재자 명칭.
    request_verb: 요청의 성격('승인'·'검수'). 절차 이름이 문구 전체를 좌우한다 —
      RN 은 'RN 승인', 최종 승인자는 '검수'다. 최초 버전도 '승인' 으로 간다 —
      승인자에게 문서를 써 달라고 하는 절차가 아니다(사용자 확정 2026-08-19).
    """
    # 기본 결재자는 **최종 승인자**다(2026-08-04 admin 과 역할 분리).
    # admin 도 결재할 수 있다 — can_final_decide 참조.
    recipients_fn = recipients_fn or user_mailer.final_reviewer_emails
    can_decide_fn = can_decide_fn or can_final_decide
    st = get_state(mail_id) or {}
    product = st.get("product") or ""
    version = st.get("version_full") or st.get("os_version") or ""
    review = st.get(state_key) or {}
    status = review.get("status") or "none"

    ui.separator()
    ui.label(f"{role_label} {request_verb} (메일 알림)").classes("text-subtitle2")
    with ui.row().classes("items-center gap-2"):
        _sl = _STATUS_LABEL.get(status, status)
        ui.badge(f"{request_verb} {_sl}" if status == "requested" else _sl,
                 color=_STATUS_COLOR.get(status, "grey-6"))
        if review.get("requester_email"):
            _when = str(review.get("requested_at", ""))[:16].replace("T", " ")
            ui.label(
                f"요청자 {review.get('requester_name', '')} <{review['requester_email']}> · {_when}"
            ).classes("text-caption text-grey-7")
    if status in ("approved", "rejected"):
        _dw = str(review.get("decided_at", ""))[:16].replace("T", " ")
        ui.label(
            f"처리자 {review.get('decided_by', '')} · {_dw}"
            + (f" · 메모: {review['note']}" if review.get("note") else "")
        ).classes("text-caption text-grey-8")

    admin_view = can_decide_fn()

    async def do_request():
        to = recipients_fn()
        if not to:
            ui.notify(
                f"{role_label} 이메일이 등록되어 있지 않습니다. [계정 관리]에서 이메일을 등록해야 합니다.",
                color="negative", multi_line=True,
            )
            return
        uid = current_user_id()
        from app import mail_templates as MT

        subject, body = MT.render(
            "review_request", 요청종류=request_verb, 제품=product, 버전=version,
            대상=label, 요청자=current_username(), 조사=_eul(request_verb),
            안내=(request_hint
                or f"▶ 앱에 로그인해 해당 메일의 {stage_hint} '검수하기'에서 "
                   "확인 후 승인을 부탁드립니다."))
        subject = subject.strip()
        n = ui.notification(f"{request_verb} 요청 메일 발송 중...", spinner=True, timeout=None)
        ok, err = await run.io_bound(user_mailer.send_as_user, uid, to, subject, body)
        n.dismiss()
        if not ok:
            ui.notify(f"발송 실패: {err}", color="negative", multi_line=True)
            return
        update_state(mail_id, **{state_key: {
            "status": "requested", "requester_id": uid,
            "requester_email": user_mailer.user_email(uid),
            "requester_name": current_username(),
            "requested_at": datetime.now().isoformat(), "admins": to,
        }})
        ui.notify(f"{request_verb} 요청 메일을 {role_label} {len(to)}명에게 보냈습니다.",
                  color="positive")
        on_close()

    note_in = None

    async def do_decision(approve: bool):
        req_email = review.get("requester_email") or ""
        if not req_email:
            ui.notify("요청자 이메일이 없어 결과를 보낼 수 없습니다.", color="negative")
            return
        uid = current_user_id()
        note = (note_in.value or "").strip() if note_in else ""
        kind = "승인" if approve else "반려"
        from app import mail_templates as MT

        # 요청 성격과 결과가 같은 낱말이면 겹쳐 적지 않는다('승인 건이 승인되었습니다').
        첫줄 = (f"요청하신 {product} {version} {label} "
               f"{'' if request_verb == kind else request_verb + ' '}건이 "
               f"{kind}되었습니다.").replace("  ", " ").strip()
        subject, body = MT.render(
            "review_result", 요청종류=request_verb, 결과=kind, 제품=product,
            버전=version, 대상=label, 첫줄=첫줄,
            처리자=current_username(), 메모=note or "-")
        subject = subject.strip()
        n = ui.notification(f"{kind} 메일 발송 중...", spinner=True, timeout=None)
        ok, err = await run.io_bound(user_mailer.send_as_user, uid, req_email, subject, body)
        n.dismiss()
        if not ok:
            ui.notify(f"발송 실패: {err}", color="negative", multi_line=True)
            return
        update_state(mail_id, **{state_key: {
            **review, "status": "approved" if approve else "rejected",
            "decided_by": current_username(), "decided_at": datetime.now().isoformat(),
            "note": note,
        }})
        ui.notify(f"{kind} 처리 및 메일 발송 완료.", color="positive")
        # 이력: 최종 승인/반려 (반려가 반복되면 이력이 계속 쌓인다).
        if state_key == "newsletter_review":
            from app.pipeline import newsletter_ws as _NLWS
            H.record(mail_id, "newsletter", f"최종 {kind}", actor=current_username(),
                     summary=note or "",
                     text=H.text_from_newsletter(_NLWS.get(mail_id) or {}))
        elif state_key == "rn_review":
            H.record(mail_id, "rn", f"최종 {kind}", actor=current_username(),
                     summary=note or "",
                     text=H.text_from_rn(get_state(mail_id) or {}))
        # 승인 뒤처리(PDF 확정·파일서버 저장·단계 완료)는 오래 걸려 코루틴으로 온다.
        _cb = (on_approve if approve else on_close)
        _r = _cb()
        if inspect.isawaitable(_r):
            await _r

    if status in ("none", "rejected"):
        ui.button(f"{role_label}에게 {request_verb} 요청", icon="mark_email_read",
                  on_click=do_request).props("color=primary")
        ui.label(f"본인 계정(등록 이메일)에서 {role_label} 전원에게 발송됩니다.").classes(
            "text-caption text-grey-5"
        )
    elif status == "requested" and admin_view:
        note_in = ui.textarea("메모(선택) — 승인/반려 사유").classes("w-full")
        with ui.row().classes("gap-2"):
            ui.button("승인", icon="check",
                      on_click=lambda: do_decision(True)).props("color=positive")
            ui.button("반려", icon="close",
                      on_click=lambda: do_decision(False)).props("color=negative")
    elif status == "requested":
        ui.label(f"{role_label} 승인 대기 중입니다.").classes("text-caption text-grey-7")


def _rn_file_row(path: str) -> None:
    """파일 한 줄(이름 + 열람/다운로드)."""
    from urllib.parse import quote

    with ui.row().classes("items-center gap-2 no-wrap w-full"):
        ui.icon("insert_drive_file", size="sm").classes("text-grey-7")
        ui.label(os.path.basename(path)).classes("text-body2 flex-grow")
        if path.lower().endswith((".pdf", ".html", ".htm")):
            _url = "/artifact/view?path=" + quote(path)
            ui.button("열람", icon="visibility",
                      on_click=lambda u=_url: ui.navigate.to(u, new_tab=True)).props(
                "flat dense size=sm")
        ui.button("다운로드", icon="download",
                  on_click=lambda sp=path: ui.download(sp)).props("flat dense size=sm")


# 각 검수 단계의 산출물은 '기록'으로 계속 남긴다(누적). 중간 산출물은 최종본이 아니다.
def _rn_existing(st: dict) -> None:
    """기존 RN(파일서버 원본) — Case A. RN 승인 '위', 항상 유지."""
    ep = st.get("rn_existing_pdf_path") or ""
    if not (st.get("rn_from_fileserver") and ep and os.path.exists(ep)):
        return
    ui.separator()
    ui.label("기존 RN (파일서버 원본)").classes("text-subtitle2")
    _rn_file_row(ep)


# ── ②가 처음 만든 판(고정본)은 화면에 두지 않는다 ─────────────────────────
# 「RN 제작본 (최초 생성)」(rn_initial_docx_path · Case B)와
# 「RN for Sales (마지막 Revision 반영)」(rn_sales_initial_path · Case A) 두 패널을 뺐다
# (사용자 확정 2026-08-26).
#
# 둘 다 ②가 처음 만든 판을 굳혀 둔 파일이라 저장·재생성에 **갱신되지 않는다**. 그런데
# 실제 RN 바로 옆에 이름까지 비슷하게 놓여 있어서, 승인자가 미반영으로 바꾸고 저장한 뒤
# 그 파일을 열어 "뺀 항목이 아직 문서에 있다"고 읽는 일이 실제로 났다(실측 job-21 —
# 공개 RN·전체 이력 RN 은 13:55 에 정상 갱신됐는데 이 파일만 8/19 판 그대로였다).
# 작업을 다시 하면 RN 도 다시 만들어지므로, 갱신되지 않는 판은 화면에 두지 않는다.
#
# 파일 자체는 작업 폴더에 그대로 남는다(기록) — 화면·결과물 목록에만 내보내지 않는다.
# 이전 **버전**의 실제 RN(rn_existing_pdf_path)은 참고 대상이 맞으므로 그대로 둔다.


def _rn_full(st: dict) -> None:
    """전체 이력 RN — 이전 판 전부 + 이번 판(반영 항목만). RN for Sales 와 함께 갱신된다."""
    p = st.get("rn_full_pdf_path") or st.get("rn_full_docx_path") or ""
    if not (p and os.path.exists(p)):
        return
    ui.separator()
    n = int(st.get("rn_full_rev_count", 0) or 0)
    ui.label("전체 이력 Release Note" + (f" · 판 {n}개" if n else "")).classes("text-subtitle2")
    # ui.label 은 마크다운을 해석하지 않는다 — '**' 를 쓰면 그대로 화면에 찍힌다.
    ui.label(
        "이전 버전 Revision History 전부 + 이번 버전의 반영 항목입니다. 이번 판 내용은 "
        "RN for Sales 와 같습니다(미반영은 빠집니다). "
        "RN for Sales 를 다시 만들 때마다 같이 갱신됩니다."
    ).classes("text-caption text-grey-6")
    _rn_file_row(p)
    if st.get("rn_full_pdf_error"):
        ui.label(f"PDF 변환 실패: {st['rn_full_pdf_error']}").classes("text-caption text-orange-9")
    # 승인 때 사내 경로에 남긴 결과(있으면).
    for saved in st.get("rn_full_archived") or []:
        ui.label(f"사내 경로 저장됨 · {saved}").classes("text-caption text-green-9").style(
            "white-space:normal; word-break:break-all")
    if st.get("rn_full_archive_note"):
        ui.label(f"사내 경로 미저장 · {st['rn_full_archive_note']}").classes(
            "text-caption text-orange-9").style("white-space:normal; word-break:break-all")


def _rn_mid_reviewed(st: dict) -> None:
    """RN 승인 반영본 — RN 승인~최종 검수 '사이'(RN 승인 후). 기록 고정본."""
    p = st.get("rn_mid_reviewed_path") or st.get("rn_docx_path") or ""
    if not (p and os.path.exists(p)):
        return
    ui.separator()
    ui.label("RN 승인 반영본").classes("text-subtitle2")
    ui.label("RN 승인자가 확인·반영한 판입니다(기록 · 최종본 아님).").classes(
        "text-caption text-grey-6")
    _rn_file_row(p)


def _rn_final(st: dict) -> None:
    """RN for Sales(PDF) — RN 승인으로 확정된 정본. 파일명에는 '최종'을 넣지 않는다."""
    p = st.get("rn_final_pdf_path") or ""
    if not (p and os.path.exists(p)):
        return
    ui.separator()
    ui.label("RN for Sales (PDF · 승인 완료)").classes("text-subtitle2 text-weight-bold")
    _rn_file_row(p)


def _render_missing_inputs(st: dict) -> None:
    """승인 전 확인 — 값이 비어 있는 입력 항목과 미판정 이슈를 눈에 띄게 보여준다.

    RN 승인 화면에서 빈 값을 막지 않도록 바꿨기 때문에(단말에서 나중에 확인하는 값이 많다),
    문서가 굳기 직전인 여기서 한 번 드러내 준다. 승인을 막지는 않는다.
    """
    from app.pages.mid_review import _TOP_LEVEL_KEYS, input_fields_for

    comps = st.get("rn_components", {}) or {}
    missing = [
        disp for disp, key, _m, _r in input_fields_for(st)
        if not str((st.get(key) if key in _TOP_LEVEL_KEYS else comps.get(key)) or "").strip()
    ]
    from app.pipeline.rn_select import UNJUDGED_PREFIX

    unjudged = [r for r in (st.get("rn_select_rows") or [])
                if str(r.get("reason", "")).startswith(UNJUDGED_PREFIX)]
    if not missing and not unjudged:
        return

    ui.separator()
    with ui.column().classes("w-full gap-1").style(
        "background:#f6e8cf; border-left:5px solid #d99a3a; border-radius:8px; padding:10px 14px"
    ):
        ui.label("승인 전 확인").classes("text-subtitle2").style("color:#8a5a12")
        if missing:
            ui.label(f"값이 비어 있는 입력 항목 {len(missing)}개 — RN 에 빈칸으로 나갑니다.") \
                .classes("text-caption").style("color:#8a5a12")
            ui.label(" · ".join(missing)).classes("text-caption").style(
                "color:#8a5a12; white-space:normal; word-break:break-word")
        if unjudged:
            # 기본값이 '전부 반영'이라 미판정도 실려 나간다 — 빼는 건 사람 몫이라
            # 몇 건이 반영 상태인지까지 알려 준다.
            _u_inc = sum(1 for r in unjudged if r.get("include") == "Y")
            ui.label(
                f"선별 판정을 못 받은 이슈 {len(unjudged)}건 — 그중 {_u_inc}건이 "
                "기본값대로 반영 상태입니다. 문구를 확인하고 뺄 것은 빼 주세요."
            ).classes("text-caption").style("color:#8a5a12")


def _final_finalize(mail_id, user_id: int, actor: str) -> dict:
    """2차 승인 뒤처리 — 스레드에서 돈다(Word 변환·파일서버 접근)."""
    from app.pages.mid_review import _finalize_after_final_approval

    return _finalize_after_final_approval(mail_id, user_id, actor)


def _rn_adopted_rows(st: dict) -> None:
    """가져다 쓴 RN 에서 뽑은 마지막 Revision — 다음 단계가 이 줄들을 쓴다.

    문서를 새로 만들지 않았어도 **무엇을 읽어 왔는지** 는 여기서 볼 수 있어야 한다.
    고치는 것은 다음 단계 [RN for Sales 제작] 에서 한다(사용자 지적 2026-09-11).
    """
    lines = [str(x) for x in (st.get("rn_revision_lines") or []) if str(x).strip()]
    if not lines:
        return
    ui.separator()
    ui.label(f"이 문서에서 읽어 온 마지막 Revision · {len(lines)}건") \
        .classes("text-subtitle2")
    with ui.column().classes("w-full gap-0"):
        for ln in lines:
            ui.label(f"· {ln}").classes("text-caption") \
                .style("white-space:normal")


def render_rn_review(mail_id, stage_id, me, *, on_approve, on_close,
                     part: str = "full") -> None:
    """Release Note 검수 — 공개 선별표 인라인 + 공통 관리자 검수 컨트롤.

    단계를 가른 뒤(2026-08-27) 이 컴포넌트도 둘로 쓰인다.
      · part="full"  — 전체 이력 RN 제작 카드. 선별표 + **RN 승인(1차)** 요청까지.
      · part="sales" — RN for Sales 제작 카드. 1차 승인이 끝난 뒤에만 열리며,
        공개 항목을 골라 저장하고 **최종 승인자 검수(2차)** 를 요청한다.
    RN 승인자 화면(/reviews)은 이 컴포넌트를 쓰지 않는다 — 그쪽은 그대로다.
    """
    st = get_state(mail_id) or {}
    rows = st.get("rn_select_rows") or []
    n_inc = sum(1 for r in rows if r.get("include") == "Y")
    from app.pages.mid_review import MID_STATE_KEY as _MID_KEY

    _mid_status = (st.get(_MID_KEY) or {}).get("status") or "none"

    if part == "sales":
        _render_rn_sales(mail_id, stage_id, me, st, _mid_status,
                         on_approve=on_approve, on_close=on_close)
        return

    ui.separator()
    if st.get("rn_full_skipped"):
        # 이번 버전 RN 이 파일서버에 이미 있으면 이력본을 만들지 않는다 — 결재할 새 문서가
        # 없으므로 선별표·승인 컨트롤도 두지 않는다(2026-08-27). 선별표는 다음 단계에서
        # 고른다. 여기서는 '이미 있는 그 문서' 만 확인할 수 있게 한다.
        with ui.column().classes("w-full gap-1").style(
            "background:#eaf3ee; border-left:5px solid #3C6553; border-radius:8px;"
            " padding:10px 14px"
        ):
            ui.label("전체 이력 RN 을 만들지 않았습니다 — 가져다 쓴 문서를 확인하세요") \
                .classes("text-subtitle2").style("color:#2f5244")
            ui.label(str(st.get("rn_full_skip_reason") or
                         "이번 버전 RN 이 파일서버에 이미 있습니다.")) \
                .classes("text-caption").style("color:#2f5244; white-space:normal")
            ui.label("공개할 항목을 고르고 편집하는 일은 다음 단계 "
                     "[RN for Sales 제작] 에서 진행하세요.") \
                .classes("text-caption").style("color:#2f5244")
        _rn_existing(st)
        _rn_adopted_rows(st)
        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            if on_close:
                ui.button("닫기", on_click=on_close).props("flat")
            ui.button("확인했습니다 — 이 단계 완료", icon="check",
                      on_click=on_approve).props("color=primary")
        return

    if st.get("rn_first_release"):
        with ui.column().classes("w-full gap-1").style(
            "background:#f6e8cf; border-left:5px solid #d99a3a; border-radius:8px;"
            " padding:10px 14px"
        ):
            ui.label("신규 OS — 참고할 Release Note 가 없습니다").classes(
                "text-subtitle2").style("color:#8a5a12")
            ui.label(
                "이 제품의 첫 RN 입니다. 이번 버전 RN 도, 직전 버전 RN 도 파일서버에 없어 "
                "기본 항목만 깔아 두었습니다. 아래에서 값을 채우거나, "
                "[RN 승인자에게 승인 요청] 으로 넘기세요(비어 있는 값은 승인자가 채웁니다)."
            ).classes("text-caption").style("color:#8a5a12")

    # 만들어진 RN 을 **화면에서 바로** 본다 — RN 승인 화면과 같은 컴포넌트.
    # 파일명만 보고 판단할 수 없으니, 선별표 위에 문서 자체를 띄운다.
    # 이 단계의 산출물은 **전체 이력본**이라 그 문서를 띄운다(2026-08-27).
    from app.pages.mid_review import render_rn_preview

    render_rn_preview(mail_id, height="78vh", which="full")

    ui.separator()
    # 선별표 + 수정 내용 + 입력값 편집기(RN 승인 화면과 동일 컴포넌트).
    # RN 승인자가 입력한 수정 내용·입력값을 여기서 확인하고, 참고해 수정·재생성할 수 있다.
    from app.pages.rn_edit_table import render_selection_editor

    # 이 단계의 산출물은 이력본 하나다 — 저장해도 RN for Sales 는 만들지 않는다.
    render_selection_editor(mail_id, on_saved=on_close,
                            save_label="저장 · 전체 이력 RN 재생성", with_sales=False)

    # ── 위(항상 유지) ──────────────────────────────────────────────────
    # ① 참고할 **이전 버전** RN + 전체 이력 RN.
    # ②가 처음 만든 고정본 두 가지는 뺐다 — 갱신되지 않아 오해를 부른다(위 주석 참조).
    _rn_existing(st)
    _rn_full(st)

    # ── 1차: RN 승인 ──────────────────────────────────────────────────────
    from app.auth import can_mid_decide
    from app.pages.mid_review import MID_STATE_KEY

    mid = st.get(MID_STATE_KEY) or {}
    mid_status = mid.get("status") or "none"

    # 최초 버전이라도 요청은 **승인 요청**이다(사용자 확정 2026-08-19). 예전에는
    # request_verb 를 '작성' 으로 바꿔 「[작성 요청]」 메일이 나갔는데, 승인자에게
    # 문서를 써 달라고 떠넘기는 느낌이라 절차와 맞지 않았다.
    render_review_controls(
        mail_id, stage_id, me, state_key=MID_STATE_KEY,
        label="Release Note",
        stage_hint="Release Note", on_approve=on_close, on_close=on_close,
        recipients_fn=user_mailer.mid_reviewer_emails,
        can_decide_fn=lambda: False,          # 승인/반려는 /reviews 페이지에서 처리
        role_label="RN 승인자",
        request_verb="승인",
        # 본문은 순수 텍스트로 나간다 — 마크다운(**강조**)을 쓰면 별표가 그대로 찍힌다.
        # 접속 주소도 함께 준다(예전에는 '앱에 로그인해' 라고만 하고 주소가 없었다).
        # 최초 버전이라는 사정은 적지 않는다 — 승인 요청 문구는 언제나 같다
        # (사용자 확정 2026-08-19).
        request_hint=(
            "▶ 아래 주소로 로그인해 [RN 승인] 페이지에서 내용을 확인하시고 "
            "승인을 부탁드립니다." + chr(10) +
            f"   {user_mailer.app_url()}"
        ),
    )
    if mid_status == "requested" and can_mid_decide():
        ui.button("RN 승인 페이지로 이동", icon="open_in_new",
                  on_click=lambda: ui.navigate.to("/reviews", new_tab=True)).props("outline size=sm")

    if mid_status == "approved":
        # 1차가 끝났으면 여기서는 안내만 한다 — 공개본 제작·2차 검수는 **다음 단계**
        # [RN for Sales 제작] 카드에서 한다(2026-08-27 단계 분리).
        with ui.column().classes("w-full gap-1").style(
            "background:#eaf3ee; border-left:5px solid #3C6553; border-radius:8px;"
            " padding:10px 14px"
        ):
            ui.label("RN 승인 완료 — History 관리용 RN 이 확정·저장되었습니다").classes(
                "text-subtitle2").style("color:#2f5244")
            ui.label(
                "이 단계는 끝났습니다. 공개할 항목을 고르고 편집해 RN for Sales 를 만드는 "
                "일은 다음 단계 [RN for Sales 제작] 에서 진행하세요."
            ).classes("text-caption").style("color:#2f5244")
        _rn_mid_reviewed(st)
    else:
        _render_rn_mid_notice(st, mid, mid_status)


def _render_rn_sales(mail_id, stage_id, me, st: dict, mid_status: str, *,
                     on_approve, on_close) -> None:
    """**RN for Sales 제작** 카드 — 1차 승인이 끝난 뒤 공개본을 만들고 2차 검수를 받는다.

    전체 이력 RN 은 여기서 건드리지 않는다(1차 승인으로 확정·저장이 끝난 문서다).
    그래서 선별표 저장도 공개본만 다시 만든다(with_full=False).
    """
    from app.pipeline.rn_research import full_stage_done

    ui.separator()
    _ok, _what = full_stage_done(st)
    if not _ok:
        with ui.column().classes("w-full gap-1").style(
            "background:#f6e8cf; border-left:5px solid #d99a3a; border-radius:8px;"
            " padding:10px 14px"
        ):
            ui.label("앞 단계가 끝나지 않았습니다").classes("text-subtitle2") \
                .style("color:#8a5a12")
            ui.label(
                f"{_what}. 전체 이력 RN 이 확정된 뒤에 그 내용으로 공개본을 만듭니다 — "
                "[전체 이력 RN 제작] 단계에서 승인을 받아 주세요."
            ).classes("text-caption").style("color:#8a5a12")
        return

    from app.pages.mid_review import render_rn_preview

    render_rn_preview(mail_id, height="78vh")

    ui.separator()
    # 공개할 항목만 골라 편집한다. 저장하면 **RN for Sales 만** 다시 만들어진다.
    from app.pages.rn_edit_table import render_selection_editor

    # 비고 열은 **여기에만** 둔다 — 미반영으로 뺀 이유를 고르는 칸이다
    # (사용자 확정 2026-09-07). 고르는 즉시 저장된다.
    render_selection_editor(mail_id, on_saved=on_close,
                            save_label="저장 · RN for Sales 재생성", with_full=False,
                            exclude_note=True)

    _rn_mid_reviewed(st)
    _rn_full(st)

    with ui.column().classes("w-full gap-1").style(
        "background:#eaf3ee; border-left:5px solid #3C6553; border-radius:8px;"
        " padding:10px 14px"
    ):
        ui.label("RN 승인 완료 — History 관리용 RN 이 확정·저장되었습니다").classes(
            "text-subtitle2").style("color:#2f5244")
        ui.label(
            "위 선별표에서 **공개할 항목만 골라 편집**하고 저장하면 RN for Sales 가 "
            "다시 만들어집니다. 그 문서로 최종 승인자 검수를 받으세요."
        ).classes("text-caption").style("color:#2f5244")

    async def _final_done() -> None:
        n = ui.notification("RN for Sales 확정·저장 중…", spinner=True, timeout=None)
        try:
            res = await run.io_bound(
                _final_finalize, mail_id, current_user_id(), current_username() or "")
        except Exception as e:  # noqa: BLE001
            n.dismiss()
            ui.notify(f"확정 실패: {type(e).__name__}: {e}", color="negative",
                      multi_line=True)
            on_close()
            return
        n.dismiss()
        for msg, color in res.get("notes", []):
            ui.notify(msg, color=color, multi_line=True, timeout=8000)
        on_approve()

    render_review_controls(
        mail_id, stage_id, me, state_key="rn_review",
        label="Release Note for Sales", stage_hint="Release Note",
        on_approve=_final_done, on_close=on_close,
        role_label="최종 승인자", request_verb="검수",
        request_hint=(
            "▶ RN 승인자 확인이 끝난 뒤, 공개할 항목만 선별·편집한 "
            "**RN for Sales** 입니다.\n"
            "  앱에 로그인해 이 작업의 [검수하기]에서 문서를 확인하시고 검수를 "
            "부탁드립니다."
        ),
    )
    _rn_final(st)


def _render_rn_mid_notice(st: dict, mid: dict, mid_status: str) -> None:
    """[전체 이력 RN 제작] 카드에서 1차 승인 **전**에 보여 주는 안내."""
    ui.separator()
    if mid_status == "rejected":
        # 반려는 '대기'가 아니라 **작성자가 할 일이 생긴 상태**라 눈에 띄게 알린다.
        with ui.column().classes("w-full gap-1").style(
            "background:#fdecea; border-left:5px solid #c0392b; border-radius:8px; "
            "padding:10px 14px"
        ):
            ui.label("RN 승인 반려됨 — 수정 후 다시 요청해 주세요").classes(
                "text-subtitle2").style("color:#922b21")
            _n = (mid.get("note") or "").strip()
            ui.label(
                f"처리자 {mid.get('decided_by', '')} · 사유: {_n or '(사유 없음)'}"
            ).classes("text-caption").style(
                "color:#922b21; white-space:normal; word-break:break-word")
            ui.label(
                "위 선별표·입력값은 RN 승인자가 저장한 상태 그대로입니다. "
                "고치신 뒤 'RN 승인자에게 승인 요청'을 다시 누르세요."
            ).classes("text-caption").style("color:#922b21")
    else:
        # 승인 전 확인 — 비어 있는 항목은 RN 의 그 칸이 빈칸으로 굳는다.
        # RN 승인자가 채우는 값이라, 요청하기 전에 작성자도 볼 수 있게 여기 둔다.
        _render_missing_inputs(st)
