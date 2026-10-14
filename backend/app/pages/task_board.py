"""작업 보드 — 한 작업의 갈래(Submission / Approval) 하나를 카드로 그린다.

작업의 출발점은 이제 **제품(SKU) + OS 버전**이다(/osupdate). 예전에는 메일 짝
([FIH][Submission] ↔ [FIH][Approval])이 곧 작업이어서 이 코드가 메일함 안에 있었는데,
작업 주체가 제품으로 바뀌면서 메일함에서 떼어냈다.

  · Submission = 준비 단계 — ②⑥⑦ 산출물을 만들어 **로컬에만** 저장(외부 업로드 없음).
  · Approval  = 실행 단계 — ①~⑧ 전 과정(실제 업로드·게시·발송). ②⑥⑦ 는 Submission
    승인본을 그대로 쓰고 다시 만들지 않는다. '전체 실행'으로 ①→⑧ 순차 실행도 가능.

단계를 읽고 쓰는 일은 저장소(app.task_store)에 맡긴다 — 그래서 이 화면은 단계가
어느 테이블에 있는지 몰라도 된다. 버튼을 눌러도 목록이 접히지 않도록 본문만
부분 갱신(refreshable)한다.
"""
import os
import time
from email.utils import parsedate_to_datetime
from urllib.parse import quote

from nicegui import run, ui

from app.auth import current_username
from app.stage_runners import execute_stage, rework_stage
from app.task_store import progress_key, result_files
from app.stages import (
    DONE,
    OV_DONE,
    OVERALL_COLORS,
    OVERALL_LABELS,
    PENDING,
    REVIEW,
    RUNNING,
    SIDE_APPROVAL,
    SIDE_LABELS,
    SIDE_SUBMISSION,
    STAGE_LABELS,
    stage_label,
    STAGE_STATUS_COLORS,
    STAGE_STATUS_LABELS,
    overall_status,
)

def _short_date(received_at: str) -> str:
    """메일 원문 Date 헤더(RFC-2822)를 'YYYY-MM-DD' 로. 파싱 실패 시 원문 앞부분."""
    if not received_at:
        return ""
    try:
        dt = parsedate_to_datetime(received_at)
        if dt is not None:
            return dt.strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        pass
    return received_at[:16]


# ②/⑦ 는 관리자 검수 상태를 pipeline state 에 저장한다. 재실행·되돌리기 시 초기화해야
# '이전 승인'이 남아있지 않는다.
#  ② Release Note 는 결재가 **두 번**이다 — RN 승인(1차, rn_mid_review)과
#     최종 승인자 검수(2차, rn_review). 재실행하면 둘 다 지워야 이전 승인이 안 남는다.
#  단계를 가른 뒤(2026-08-27)에도 재실행 초기화 범위는 그대로다 — 전체 이력 단계를
#  다시 돌리면 선별표부터 새로 만들어지므로 **1차·2차 결재를 모두** 지운다. RN for Sales
#  단계만 다시 돌릴 때는 2차만 지운다(1차 승인본은 이미 확정·저장돼 건드리지 않는다).
_REVIEW_STATE_KEYS = {"release_note_full": ("rn_mid_review", "rn_review"),
                      "release_note": ("rn_review",),
                      "newsletter": ("newsletter_review",)}


def _reset_review_state(task_key, stage_key) -> None:
    """Release Note·Newsletter 재실행·되돌리기 시 검수 상태(요청/승인/반려)를 '요청 전'으로 초기화."""
    keys = _REVIEW_STATE_KEYS.get(stage_key)
    if not keys or task_key is None:
        return
    from app.pipeline.state import get_state, update_state
    st = get_state(task_key) or {}
    wipe = {sk: {} for sk in keys if st.get(sk)}
    if wipe:
        update_state(task_key, **wipe)


def _rn_review_locked(task_key) -> bool:
    """② RN 의 **'추가 지시로 재작업'** 을 막아야 하는가(선별표 편집기와는 무관하다).

    1차·2차 중 하나라도 요청됨/승인됨이면 막는다. 재작업은 Claude 로 문서를 처음부터
    다시 만드는 동작이라, **1차 승인 뒤에도 막아야 한다** — 그 시점엔 History 관리용
    이력본이 이미 확정돼 파일서버에 저장된 뒤다.
    RN for Sales 를 만드는 '고르고 편집' 은 선별표 편집기(render_selection_editor)로
    하며 이 잠금을 타지 않는다. 반려됨·요청 전이면 작성자가 다시 고칠 수 있어야 한다.
    """
    from app.pages.mid_review import MID_STATE_KEY
    from app.pipeline.state import get_state

    st = get_state(task_key) or {}
    for sk in (MID_STATE_KEY, "rn_review"):
        if (st.get(sk) or {}).get("status") in ("requested", "approved"):
            return True
    return False


def render_side(
    store, subject: str, sender: str, received_at: str, user_id: int, side: str,
    on_status_change=None, mail_id: int | None = None,
) -> None:
    """작업 한 갈래(Submission ②⑥⑦ / Approval ①③④⑤⑧)를 카드로 렌더링.

    store: 단계 저장소(app.task_store) — 메일 작업이면 MailStageStore,
           /osupdate 의 제품 작업이면 JobStageStore. 화면 코드는 둘을 구분하지 않는다.
    mail_id: 원본 메일이 있는 작업만 준다('메일 내용 보기' 버튼용). 제품 작업은 None.
    on_status_change: 단계 상태가 바뀌었음을 알리는 콜백. **이 카드를 품고 있는 요소를
           다시 그리는 함수를 주면 안 된다** — 아래 body() 가 그릴 때마다 호출하므로
           '그리기 → 알림 → 다시 그리기' 가 끝없이 돌아 서버가 멈춘다.
           진행 배지처럼 이 카드 바깥의 **작은 요약**만 갱신하는 함수를 줄 것.
    """
    me = current_username() or ""  # 공용 보드: 이 조작을 한 사람(수정자 표시용)
    key = store.key                # 파이프라인 공유 상태 키(메일 id 또는 'job-N')

    # 갈래 머리말: 이름 + 날짜 + 이 갈래의 진행 배지
    with ui.row().classes("w-full items-center gap-2 no-wrap"):
        ui.label(SIDE_LABELS[side]).classes("text-subtitle2").style(
            f"color: {'#3C6553' if side == SIDE_SUBMISSION else '#8a5a12'}; font-weight:700"
        )
        date_str = _short_date(received_at)
        if date_str:
            ui.label(date_str).classes("text-caption text-grey-6")
        ui.space()
        badge = ui.badge("", color="grey-6")

    async def run_stage(stage_id: int) -> bool:
        """단계를 실행: 선점 → 실제 작업 → 검수 대기. 선점 실패면 False."""
        ok, why = store.claim(stage_id, me)
        if not ok:
            body.refresh()          # 남이 잡은 최신 상태를 화면에 반영
            ui.notify(why, color="warning")
            return False
        body.refresh()
        st = store.get(stage_id)
        pk = progress_key(store, stage_id)
        # 재실행이면 이전 관리자 검수 상태(승인 흔적)를 초기화한다(②/⑦).
        _reset_review_state(key, st.stage_key)
        out = await run.io_bound(
            execute_stage, st.stage_key, subject, user_id, st.input_params or "",
            key, "", pk, side,
        )
        from app import progress
        progress.clear(pk)
        if out.cancelled:
            store.set_status(stage_id, PENDING, me)
            body.refresh()
            ui.notify("작업을 중단했습니다. 이 단계를 대기 상태로 되돌렸습니다.", color="warning")
            return True
        store.save_result(stage_id, out.result_text, out.artifact_type, out.artifact_path, me)
        if out.auto_done:
            # 검수할 것이 없는 단계 — '검수 대기' 로 세워 두면 아무도 누를 것이 없는데도
            # 작업이 안 끝난 것처럼 보인다(예: 이번 버전 RN 이 이미 있어 이력본을 만들지
            # 않는 경우). 실행이 곧 완료다.
            store.set_status(stage_id, DONE, me)
        body.refresh()
        return True

    async def start_processing():
        nxt = store.next_pending(side)
        if nxt is None:
            ui.notify("모든 단계가 완료되었습니다.", color="info")
            return
        await run_stage(nxt.id)

    async def run_all():
        """Approval 전용 — ①~⑧ 을 한꺼번에(순차) 실행한다.

        각 단계는 실행 후 '검수 대기'가 되므로, 여기서는 검수를 자동 통과시키지 않고
        **실행만** 연속으로 돌린다. 실패하면 그 단계에서 멈춘다.
        """
        ran = 0
        while True:
            nxt = store.next_pending(side)
            if nxt is None:
                break
            if nxt.status in (REVIEW, RUNNING):
                ui.notify(
                    f"'{STAGE_LABELS.get(nxt.stage_key, nxt.stage_key)}' 이(가) 검수 대기 상태입니다. "
                    "검수를 완료한 뒤 다시 실행하세요.",
                    color="warning", multi_line=True,
                )
                break
            if not await run_stage(nxt.id):
                break                       # 다른 사람이 실행 중 → 전체 실행 중단
            ran += 1
            st_now = store.get(nxt.id)
            if st_now is None or st_now.status == PENDING:   # 중단·실패 → 멈춤
                ui.notify("실행이 중단되었습니다. 결과를 확인하세요.", color="warning")
                break
        if ran:
            ui.notify(f"{ran}개 단계를 실행했습니다. 각 단계의 '검수하기'로 결과를 확인하세요.",
                      color="positive", multi_line=True)

    def open_review(stage_id: int):
        st = store.get(stage_id)
        label = stage_label(st.stage_key, side)
        files = result_files(st.result_text or "", st.artifact_path or "")
        # 팝업(카드) 전체가 스크롤된다 → 내부의 실행결과/표는 자체 스크롤을 두지 않고 전체를 펼쳐 보여준다.
        # 팝업 너비 = 메일함(받은 메일 영역) 폭(max-w-[104rem])과 맞춘다.
        with ui.dialog() as dlg, ui.card().classes("w-[104rem] max-w-full").style(
            "max-height: 92vh; overflow-y: auto;"
        ):
            ui.label(f"{label} · 검수").classes("text-h6")

            # 1) 전체 실행 결과(내부 스크롤 없이 전체 표시 — 길면 팝업 전체가 스크롤됨)
            ui.separator()
            ui.label("실행 결과").classes("text-subtitle2")
            ui.label(st.result_text or "(결과 내용이 없습니다)").classes(
                "whitespace-pre-wrap text-body2 font-mono w-full"
            )

            # 2) 산출물 다운로드/열람 — ⑦ Newsletter 는 미리보기가 있어 숨기고,
            #    ② Release Note 는 표와 검수 칸 사이(render_rn_review 안)에서 보여주므로 여기선 뺀다.
            if files and st.stage_key not in ("newsletter", "release_note",
                                              "release_note_full"):
                ui.separator()
                ui.label("산출물").classes("text-subtitle2")
                for p in files:
                    name = os.path.basename(p)
                    with ui.row().classes("items-center gap-2 no-wrap"):
                        ui.icon("insert_drive_file", size="sm").classes("text-grey-7")
                        ui.label(name).classes("text-body2 flex-grow")
                        if p.lower().endswith((".pdf", ".html", ".htm")):
                            # 다운로드 없이 새 브라우저 탭에서 내용만 바로 열람(inline).
                            _view_url = "/artifact/view?path=" + quote(p)
                            ui.button(
                                "열람", icon="visibility",
                                on_click=lambda url=_view_url: ui.navigate.to(url, new_tab=True),
                            ).props("flat dense size=sm")
                        ui.button(
                            "다운로드", icon="download",
                            on_click=lambda sp=p: ui.download(sp),
                        ).props("flat dense size=sm")

            # 2-1) Release Note 두 단계: 공개 선별표 인라인 + 검수(요청/승인/반려 메일).
            #       RN 문구는 선별표의 '수정 내용'에서 직접 고친다.
            #       · 전체 이력 RN 제작 → 선별표 + **RN 승인(1차)**
            #       · RN for Sales 제작 → 확정본 + **최종 승인자 검수(2차)**
            #      두 카드가 같은 컴포넌트를 part 만 달리해 쓴다(승인자 화면은 건드리지 않는다).
            if st.stage_key in ("release_note_full", "release_note"):
                from app.pages.rn_review import render_rn_review

                def _rn_on_approve():
                    store.set_status(stage_id, DONE, me)
                    dlg.close()
                    body.refresh()
                    _advance_notice()

                def _rn_on_close():
                    dlg.close()
                    body.refresh()

                render_rn_review(key, stage_id, me,
                                 on_approve=_rn_on_approve, on_close=_rn_on_close,
                                 part=("full" if st.stage_key == "release_note_full"
                                       else "sales"))

                # (Revision History 직접 편집란은 제거했다 — 선별표의 '수정 내용'이
                #  그대로 RN 문구가 되므로 같은 내용을 두 곳에서 고치게 되어 혼란스럽다.)

            # 2-1a) ⑥ Knowledge Base 편집: 검수를 **카드 안에서** 끝낸다. 예전에는
            #        /portaledit 로 건너가 건을 고르고 탭을 바꾸고 문서를 눌러야 했다
            #        (사용자 지적 2026-09-08).
            if st.stage_key == "release_note_edit":
                from app.pages.kb_review import render_kb_review

                render_kb_review(key, uploads=(side != SIDE_SUBMISSION),
                                 on_saved=body.refresh)

            # 2-1b) ④ FOTA 업로드: OsInfo·DownloadPath 내용 확인 + 실제 FOTA 등록
            if st.stage_key == "excel_docs":
                ui.separator()
                ui.label("FOTA 업로드 · 내용 확인 및 실제 등록").classes("text-subtitle2")
                from app.pages.fota_editor import render_fota_review

                render_fota_review(key, on_done=body.refresh)

            # 2-2) ⑦ Newsletter: Submission = 편집·제작, Approval = 완성본 발송만.
            if st.stage_key == "newsletter":
                ui.separator()
                _is_sub = side == SIDE_SUBMISSION
                ui.label("Newsletter 편집·제작" if _is_sub else "Newsletter 발송") \
                    .classes("text-subtitle2")
                from app.pages.newsletter import render_newsletter_editor

                # Submission 은 제작(초안)만, Approval 은 완성본을 편집 없이 발송만 한다.
                render_newsletter_editor(key, on_sent=body.refresh,
                                         allow_send=(side == SIDE_APPROVAL),
                                         edit=_is_sub)

                # 관리자 검수(요청/승인/반려)는 Submission 에서만. Approval 에선 이미 Submission 에서
                # 최종안 제작·검수를 끝냈으므로 검수 요청 없이 발송만 하면 된다.
                if _is_sub:
                    from app.pages.rn_review import render_review_controls

                    def _nl_on_approve():
                        store.set_status(stage_id, DONE, me)
                        dlg.close()
                        body.refresh()
                        _advance_notice()

                    def _nl_on_close():
                        dlg.close()
                        body.refresh()

                    render_review_controls(
                        key, stage_id, me, state_key="newsletter_review",
                        label="Newsletter", stage_hint="Newsletter",
                        on_approve=_nl_on_approve, on_close=_nl_on_close,
                    )

            # 2-3) ⑧ Dropbox 정리: 구버전 파일 실제 삭제(확인 게이트)
            if st.stage_key == "dropbox_cleanup":
                ui.separator()
                ui.label("Dropbox 구버전 정리").classes("text-subtitle2")
                from app.pages.dropbox_cleanup_editor import render_cleanup

                render_cleanup(key, on_done=body.refresh)

            # 3) 추가 지시로 재작업 — Claude 기반 '내용' 단계(②⑤⑥⑦)에만 노출.
            #    나머지(①③④⑧)는 규칙 실행이라 지시 재작업 기능을 제공하지 않는다.
            #    단 ② RN 은 검수 요청(중간/최종)이 들어가면 재작업도 막는다.
            #    또 Approval 의 ⑥ KB·⑦ Newsletter 는 Submission 승인본을 업로드·발송만 하므로
            #    (제작·편집은 Submission 에서 끝났다) 재작업 UI 를 아예 빼서 편집을 막는다.
            #    RN 은 **전체 이력 단계에만** 재작업을 둔다 — 고칠 대상인 Revision History 가
            #    거기서 만들어진다. RN for Sales 는 선별표에서 골라 만드는 문서라 대상이 없다.
            _REWORK_STAGES = {"release_note_full", "service_portal", "release_note_edit",
                              "newsletter"}
            _reuse_on_approval = (side == SIDE_APPROVAL
                                  and st.stage_key in ("release_note_edit", "newsletter"))
            _rework_locked = (st.stage_key == "release_note_full"
                              and _rn_review_locked(key))
            if st.stage_key in _REWORK_STAGES and not _rework_locked and not _reuse_on_approval:
                ui.separator()
                with ui.expansion("추가 지시로 재작업 (결과가 잘못됐을 때)",
                                  icon="auto_fix_high").classes("w-full"):
                    rev_in = ui.textarea(
                        "수정 지시",
                        placeholder="예: 변경 설명을 더 간결하게 / #33127 은 제외 / 보안 패치 항목 문구 수정 …",
                    ).classes("w-full")

                    async def do_rework():
                        rev = (rev_in.value or "").strip()
                        if not rev:
                            ui.notify("수정 지시를 입력하세요.", color="warning")
                            return
                        dlg.close()
                        store.set_status(stage_id, RUNNING, me)
                        body.refresh()
                        st2 = store.get(stage_id)
                        pk2 = progress_key(store, stage_id)
                        n = ui.notification("추가 지시로 재작업 중... (완료된 결과에 지시 반영)",
                                            spinner=True, timeout=None)
                        try:
                            # 전체 재실행이 아니라 완료 산출물에 지시만 반영(rework_stage).
                            out = await run.io_bound(
                                rework_stage, st2.stage_key, subject, user_id,
                                st2.input_params or "", key, rev, pk2,
                            )
                        finally:
                            n.dismiss()
                        from app import progress
                        progress.clear(pk2)
                        if out.cancelled:
                            store.set_status(stage_id, REVIEW, me)   # 재작업 중단 → 이전 결과 유지
                            body.refresh()
                            ui.notify("재작업을 중단했습니다. 이전 결과가 유지됩니다.", color="warning")
                            return
                        store.save_result(stage_id, out.result_text, out.artifact_type,
                                          out.artifact_path, me)
                        # 내용이 바뀌었으니 이전 관리자 검수 상태를 초기화(②/⑦).
                        _reset_review_state(key, st2.stage_key)
                        body.refresh()
                        ui.notify("재작업 완료. '검수하기'로 다시 확인하세요.", color="positive")

                    ui.button("이 지시로 재작업", icon="refresh", on_click=do_rework).props("color=primary")

            def _advance_notice():
                if store.next_pending(side) is None:
                    ui.notify("모든 단계가 완료되었습니다. 처리 완료 상태입니다.", color="positive")
                else:
                    ui.notify("다음 단계는 '실행 승인'을 눌러 진행하세요.", color="info")

            def confirm():
                # ⑧ Dropbox 정리는 '검수 완료'가 **아무것도 지우지 않는다** — 단계 상태만
                # 완료로 바꾼다. 삭제는 팝업 안의 경로별 버튼으로만 한다.
                # ⑤ Service Portal · ⑥ Knowledge Base: '검수 완료' = 사본으로 실제 원본 페이지 교체.
                # 단 Submission 갈래는 편집만 하고 실제 업로드는 Approval 에서 하므로,
                # 여기서는 업로드 없이 단계만 완료 처리한다(예전 Skip 과 동일 동작).
                if (st.stage_key in ("service_portal", "release_note_edit")
                        and side != SIDE_SUBMISSION):
                    from app.pages.portal_upload import confirm_upload

                    def _after():
                        dlg.close()
                        body.refresh()
                        _advance_notice()

                    confirm_upload(st.stage_key, key, me, stage_id, on_done=_after,
                                   set_done=lambda: store.set_status(stage_id, DONE, me))
                    return
                store.set_status(stage_id, DONE, me)
                dlg.close()
                body.refresh()
                _advance_notice()

            def skip():
                # 그냥 건너뛰기 — 실제 업로드 없이 이 단계를 완료 처리.
                store.set_status(stage_id, DONE, me)
                dlg.close()
                body.refresh()
                ui.notify("이 단계를 건너뛰었습니다(실제 페이지에 업로드하지 않음).", color="info")
                _advance_notice()

            # Submission 은 편집만 하므로 '검수 완료' 가 곧 완료 처리(Skip 불필요).
            # 실제 업로드가 있는 Approval 갈래에서만 Skip(업로드 생략) 버튼을 둔다.
            _is_upload_stage = st.stage_key in ("service_portal", "release_note_edit")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("닫기", on_click=dlg.close).props("flat")
                if _is_upload_stage and side != SIDE_SUBMISSION:
                    ui.button("Skip", on_click=skip).props("flat color=grey-7")
                ui.button("검수 완료", on_click=confirm).props("color=positive")
        dlg.open()

    @ui.refreshable
    def body():
        stages = store.stages(side)
        statuses = [s.status for s in stages]
        ov = overall_status(statuses)
        badge.set_text(OVERALL_LABELS[ov])
        badge.props(f"color={OVERALL_COLORS[ov]}")
        # 헤더의 단계 상태 칩도 함께 갱신(펼치지 않아도 진행 상황이 보이도록).
        if on_status_change is not None:
            on_status_change()

        any_active = any(s in (RUNNING, REVIEW) for s in statuses)

        def _bulk_status(status):
            # 이 갈래의 모든 단계 상태를 한 번에 수동 지정(이력 회차 마감·초기화용).
            for s in stages:
                store.set_status(s.id, status, me)
                if status == PENDING:
                    _reset_review_state(key, s.stage_key)
            body.refresh()
            ui.notify(
                f"이 작업의 모든 단계를 수동으로 '{STAGE_STATUS_LABELS[status]}'(으)로 "
                "변경했습니다.", color="info",
            )

        # 상단: 처리 시작/이어서 진행 + 메일 내용 보기
        with ui.row().classes("w-full items-center gap-2"):
            ui.label(sender or "").classes("text-caption text-grey-8")
            ui.label(received_at or "").classes("text-caption text-grey-6")
            ui.space()
            if ov != OV_DONE and not any_active:
                start_label = (
                    "처리 시작 (1단계 실행 승인)"
                    if all(s == PENDING for s in statuses)
                    else "다음 단계 실행 승인"
                )
                ui.button(start_label, on_click=start_processing).props("size=sm").tooltip(
                    "승인하면 이 단계를 Claude가 도구를 사용해 수행합니다."
                )
                # Approval 은 ①~⑧ 전 과정을 여기서 진행하므로 '전체 실행'도 제공한다.
                if side == SIDE_APPROVAL:
                    ui.button("전체 실행", icon="playlist_play", on_click=run_all).props(
                        "size=sm outline color=primary"
                    ).tooltip("모든 단계를 순차로 실행합니다(각 단계는 검수 대기로 남습니다)")
            if mail_id is not None:
                ui.button(
                    "메일 내용 보기",
                    on_click=lambda: ui.navigate.to(f"/mail/{mail_id}"),
                ).props("outline size=sm")
            # 작업 전체를 사람이 손으로 마감 — 예: Submission 만 있고 대체된 회차(MX450 R14)를
            # 실제 실행 없이 '처리 완료'로 닫는다. 이런 이력 정리가 잦아 버튼으로 둔다.
            if ov != OV_DONE:
                ui.button(
                    "처리 완료", icon="done_all", on_click=lambda: _bulk_status(DONE),
                ).props("size=sm outline color=positive").tooltip(
                    "이 작업의 모든 단계를 완료로 표시합니다(실제 실행 없이 전체 마감)."
                )
            # 이미 손댄 작업은 '대기로 초기화'(전체 되돌리기)도 제공한다.
            if any(s != PENDING for s in statuses):
                with ui.button(icon="more_vert").props(
                    "flat dense size=sm color=grey-7"
                ).tooltip("처리 상태 초기화"):
                    with ui.menu():
                        ui.menu_item("전체 대기로 초기화",
                                     on_click=lambda: _bulk_status(PENDING))

        # ① OS Download: 받을 정식 OS 버전 직접 지정(선택). 비우면 SP/RN 이력으로 자동 판별.
        os_stage = next((s for s in stages if s.stage_key == "os_download"), None)
        if os_stage is not None and os_stage.status != DONE:
            from app.pipeline.os_download import _override_version

            _cur_ver = _override_version(os_stage.input_params or "")
            # Submission 에서 지정한 버전을 ①이 물려받는다 — 비어 보이면 헷갈리므로 함께 보여준다.
            from app.pipeline.state import get_state as _gs_ov

            _inherited = str((_gs_ov(key) or {}).get("version_override") or "").strip()
            _src = "작업" if store.kind == "job" else "Submission"
            _title = "OS Download 버전 지정 (선택 — 비우면 자동 판별)"
            if not _cur_ver and _inherited:
                _title = f"OS Download 버전 지정 — {_src} 지정값 {_inherited} 사용"
            with ui.expansion(_title).classes("w-full").props("dense"):
                ui.label(
                    "비우면 출시 이력으로 자동 판별합니다."
                ).classes("text-caption text-grey-7")
                if not _cur_ver and _inherited:
                    ui.label(
                        f"※ {_src} 에서 지정한 {_inherited} 을(를) 그대로 사용합니다. "
                        "여기에 값을 넣으면 그 값이 우선합니다."
                    ).classes("text-caption").style("color:#3C6553")
                _ver_in = ui.input(
                    "OS 버전", value=_cur_ver,
                    placeholder=(_inherited or "예: 95.07"),
                ).classes("w-full")

                def save_os_ver(sid=os_stage.id, inp=_ver_in):
                    v = (inp.value or "").strip()
                    store.set_input(sid, f"version: {v}" if v else "")
                    ui.notify(
                        f"버전 {v} 로 지정했습니다. OS Download 를 실행/재실행하면 이 버전으로 받습니다."
                        if v else "버전 지정을 지웠습니다(자동 판별).",
                        color="positive",
                    )

                ui.button("저장", on_click=save_os_ver).props("size=sm outline")

        # Submission 갈래: ①이 아직 안 돌아 파일 기준 버전이 없으므로 여기서 OS 버전을 지정한다.
        # 이 값은 ②(RN 제작)가 정식 버전으로 쓰고, 나중에 Approval 의 ①도 이어받아 그 버전을 받는다.
        # 제품 작업(/osupdate)은 버전이 곧 작업의 정체다 — 여기서 못 바꾸게 하고 안내만 한다.
        if side == SIDE_SUBMISSION and store.kind == "job":
            from app.pipeline.state import get_state as _gs_v

            _job_v = str((_gs_v(key) or {}).get("version_override") or "")
            ui.label(f"OS 버전 {_job_v} — 작업에서 지정한 값입니다(변경하려면 새 작업을 만드세요).") \
                .classes("text-caption").style("color:#3C6553")
        elif side == SIDE_SUBMISSION:
            from app.pipeline.state import get_state as _gs_v, update_state as _us_v

            _cur_v = str((_gs_v(key) or {}).get("version_override") or "")
            with ui.expansion("OS 버전 지정 (Release Note 제작 · OS Download 공통 — 비우면 자동 판별)").classes(
                "w-full"
            ).props("dense"):
                ui.label(
                    "Submission 단계에서는 OS 파일이 없어 버전을 자동 판별할 수 없습니다."
                ).classes("text-caption text-grey-7")
                _v_in = ui.input("OS 버전", value=_cur_v, placeholder="예: 95.07").classes("w-full")

                def save_task_ver(inp=_v_in, mid=key):
                    v = (inp.value or "").strip()
                    _us_v(mid, version_override=v)
                    ui.notify(
                        f"버전 {v} 로 지정했습니다. Release Note 제작을 실행하면 이 버전으로 만들어집니다."
                        if v else "버전 지정을 지웠습니다(자동 판별).",
                        color="positive",
                    )

                ui.button("저장", on_click=save_task_ver).props("size=sm outline")

        # ① OS Download 경로 수동 지정 — OS Download 는 Approval 단계이므로 Approval 카드에 둔다.
        # 자동 탐색이 실패했을 때(폴더명이 다르거나 접근 불가) 채워 재실행한다.
        if side == SIDE_APPROVAL:
            from app.pipeline.state import get_state as _gs_p, update_state as _us_p

            _ov = (_gs_p(key) or {}).get("os_path_overrides") or {}
            with ui.expansion("다운로드 경로 수동 지정 (자동 탐색 실패 시)").classes(
                "w-full"
            ).props("dense"):
                ui.label(
                    "폴더 경로를 넣으면 자동 탐색 대신 그 경로를 씁니다. 비우면 자동. "
                    "전체 UNC(\\\\192.168.123.104\\Project\\…) 또는 공유 기준(Project/…) 둘 다 됩니다."
                ).classes("text-caption text-grey-7")
                _sd_in = ui.input("sdcard 폴더 경로", value=_ov.get("sdcard", "")).classes("w-full")
                _fl_in = ui.input("flash/fastboot 폴더 경로",
                                  value=_ov.get("flash", "")).classes("w-full")
                _sk_in = ui.input("EmKit SDK 폴더 경로 (…/sdk)",
                                  value=_ov.get("sdk", "")).classes("w-full")

                def save_paths(sd=_sd_in, fl=_fl_in, sk=_sk_in, mid=key):
                    # 탐색기 '경로로 복사'가 붙이는 따옴표를 걷어내고 저장한다 —
                    # 그대로 두면 첫 조각이 공유 이름으로 오인돼 67 로 죽는다.
                    from app.integrations.fileserver import clean_unc

                    _us_p(mid, os_path_overrides={
                        "sdcard": clean_unc(sd.value),
                        "flash": clean_unc(fl.value),
                        "sdk": clean_unc(sk.value),
                    })
                    ui.notify("경로를 저장했습니다. OS Download 를 재실행하세요.", color="positive")

                ui.button("저장", on_click=save_paths).props("size=sm outline")

        # ② Release Note 아래부분: 컴포넌트 버전(수동, 항목별 입력). 보안패치는 PFU에서 자동.
        # RN 은 Submission 에서 전부 제작하므로, Approval 에서는 이 입력 칸을 두지 않는다
        # (Approval 의 ② 는 Submission 승인본을 그대로 재사용).
        # 컴포넌트 입력값은 **리서치 단계**(전체 이력 RN 제작)가 쓴다 — 그 단계가 문서를
        # 만들 때 읽어 가므로, 그 단계가 끝나기 전까지만 입력 칸을 보여 준다.
        rn_stage = next((s for s in stages if s.stage_key == "release_note_full"), None)
        if side == SIDE_SUBMISSION and rn_stage is not None and rn_stage.status != DONE:
            from app.pipeline.rn_research import _parse_components

            # 자동 추출(보안패치·SDK·GMS = PFU에서 자동) 제외, 단말 런타임값만 수동 입력.
            # (표시명, 저장 라벨, 내부 키)
            _ver_fields = [
                ("Audio parameter", "Audio", "audio"),
                ("Touch panel", "Touch", "touch"),
                ("Wi-Fi Driver", "WiFi Driver", "wifi_driver"),
                ("Wi-Fi Firmware", "WiFi Firmware", "wifi_firmware"),
                ("Wi-Fi Config", "WiFi Config", "wifi_config"),
            ]
            # Revision History 초안(선택): 채우면 ②가 이 초안을 그대로 Revision History 로 사용.
            from app.pipeline.state import get_state as _gs, update_state as _us

            _draft0 = (_gs(key) or {}).get("rn_revision_draft_manual", "")
            with ui.expansion("Revision History 초안 입력 (선택 — 채우면 그대로 사용)").classes(
                "w-full"
            ).props("dense"):
                ui.label(
                    "한 줄에 한 항목. 비우면 Release Note 제작이 자동으로 정리합니다."
                ).classes("text-caption text-grey-7")
                _rev_draft_in = ui.textarea(value=_draft0).classes("w-full").props("autogrow")

                def save_draft(inp=_rev_draft_in):
                    _us(key, rn_revision_draft_manual=(inp.value or "").strip())
                    ui.notify("Revision History 초안을 저장했습니다. Release Note 제작을 실행하면 반영됩니다.", color="positive")

                ui.button("초안 저장", on_click=save_draft).props("size=sm outline")

            with ui.expansion("Release Note 컴포넌트 입력 (단말에서만 확인 가능한 값)").classes(
                "w-full"
            ).props("dense"):
                ui.label(
                    "단말에서만 확인 가능한 값입니다. 비우면 RN에 빈칸으로 남습니다."
                ).classes("text-caption text-grey-7")
                _existing = _parse_components(rn_stage.input_params or "")
                _inputs: dict = {}
                with ui.grid(columns=2).classes("w-full gap-2"):
                    # 변수명 주의: 바깥 `key`(작업 상태 키)를 가리지 않도록 fkey 로 쓴다.
                    for label, _lab, fkey in _ver_fields:
                        _inputs[fkey] = ui.input(
                            label, value=_existing.get(fkey, "")
                        ).classes("w-full")
                _inputs["known_issues"] = ui.textarea(
                    "Known Issues", value=_existing.get("known_issues", "")
                ).classes("w-full").props("autogrow")

                _save_map = [(lab, fk) for _n, lab, fk in _ver_fields] + [("Known Issues", "known_issues")]

                def save_comps(sid=rn_stage.id, inputs=_inputs, smap=_save_map):
                    lines = [f"{lab}: {inputs[fk].value.strip()}"
                             for lab, fk in smap if (inputs[fk].value or "").strip()]
                    store.set_input(sid, "\n".join(lines))
                    ui.notify("컴포넌트 값을 저장했습니다. Release Note 제작을 실행/재실행하면 RN에 반영됩니다.", color="positive")

                ui.button("저장", on_click=save_comps).props("size=sm outline")

        # Newsletter 의 Release Note 링크는 제작 단계가 RN for Sales 를 Dropbox 에 올려
        # 얻는다(2026-09-22 확정). 여기는 **다른 주소를 쓰고 싶을 때만** 손으로 지정하는
        # 자리다. 비워 두는 것이 정상이다.
        if any(s_.stage_key == "newsletter" for s_ in stages):
            from app.pipeline.state import get_state as _gs_l, update_state as _us_l

            _link0 = str((_gs_l(key) or {}).get("rn_confluence_link") or "")
            _auto0 = str((_gs_l(key) or {}).get("rn_link") or "")
            with ui.expansion(
                "Release Note 링크 직접 지정 (선택)"
                + (" — 지정함" if _link0 else ""), value=False
            ).classes("w-full").props("dense"):
                ui.label("비워 두면 Newsletter 제작 때 RN for Sales 를 Dropbox 에 올리고 "
                         "그 링크를 씁니다. 다른 주소를 쓰려면 여기에 넣으세요.") \
                    .classes("text-caption text-grey-7")
                if _auto0:
                    ui.label(f"지금 Dropbox 링크: {_auto0}") \
                        .classes("text-caption text-grey-7").style("word-break:break-all")
                _link_in = ui.input(
                    value=_link0,
                    placeholder="비우면 RN for Sales Dropbox 링크",
                ).classes("w-full").props("dense outlined")

                def save_rn_link(inp=_link_in):
                    _v = (inp.value or "").strip()
                    _us_l(key, rn_confluence_link=_v)
                    # 이미 만들어 둔 Newsletter 초안의 버튼 주소도 함께 바꾼다 — 그러지
                    # 않으면 초안을 다시 만들기 전까지 버튼이 옛 주소를 가리킨다.
                    # 지정을 지우면 Dropbox 링크로 되돌린다.
                    from app.pipeline import newsletter_ws as _NLWS

                    _to = _v or str((_gs_l(key) or {}).get("rn_link") or "")
                    _hit = _NLWS.update_fields(key, rn_link=_to) if _to else False
                    ui.notify(
                        ("Release Note 링크를 저장했습니다." if _v
                         else "직접 지정을 지웠습니다 — Dropbox 링크를 씁니다.")
                        + (" Newsletter 버튼 주소도 바꿨습니다." if _hit else ""),
                        color="positive")
                    body.refresh()

                ui.button("저장", on_click=save_rn_link).props("size=sm outline")

        ui.separator()
        # 되돌리기는 '직전 작업'(완료 접두부의 맨 끝 = 가장 마지막 비-대기 스텝)에만 허용.
        # → 뒤 스텝이 이미 처리됐으면 앞 스텝은 되돌릴 수 없어 파이프라인이 일관되게 유지된다.
        last_active_idx = -1
        for i, s in enumerate(stages):
            if s.status != PENDING:
                last_active_idx = i
        for i, s in enumerate(stages):
            _render_stage_row(s, open_review, body.refresh, me,
                              revertable=(i == last_active_idx), side=side, store=store)

    body()


def _render_stage_row(stage, open_review, refresh, me: str = "", revertable: bool = False,
                      side: str | None = None, store=None) -> None:
    with ui.row().classes("w-full items-center gap-2 no-wrap py-1"):
        ui.label(stage_label(stage.stage_key, side)).classes("text-body2").style(
            "min-width: 190px"
        )
        ui.badge(
            STAGE_STATUS_LABELS[stage.status], color=STAGE_STATUS_COLORS[stage.status]
        )

        async def _skip_full(sid=stage.id):
            """[전체 이력 RN 제작] 을 건너뛴다 — 이미 있는 RN 을 기준으로 이어간다.

            **둘 다 작업 스레드로 넘긴다.** 이벤트 루프에서 바로 부르면 안쪽 Service
            Portal 조회가 sync Playwright 라 거부되고(실측 2026-09-21), 파일서버
            조회도 그동안 모든 사용자의 화면을 멈춘다.
            """
            from app.auth import current_user_id
            from app.pipeline.rn_research import skip_full_rn, skip_target

            uid = current_user_id()
            n = ui.notification("기준으로 삼을 RN 을 찾는 중…", spinner=True, timeout=None)
            try:
                name, where, why = await run.io_bound(skip_target, store.key, uid)
            finally:
                n.dismiss()
            with ui.dialog() as dlg, ui.card().classes("w-[34rem] max-w-full"):
                ui.label("전체 이력 RN 제작을 건너뜁니다").classes("text-h6")
                ui.label(
                    "이력본을 새로 만들지 않습니다. 다음 [RN for Sales 제작] 이 "
                    "이미 만들어져 있는 RN 의 내용으로 공개본을 만듭니다."
                ).classes("text-body2").style("white-space:normal")
                if name:
                    ui.label(f"기준 RN: {name}").classes("text-caption")
                    if where:
                        ui.label(f"위치: {where}").classes("text-caption text-grey-7")
                elif why:
                    ui.label(why).classes("text-caption text-orange-9") \
                        .style("white-space:normal")

                async def _go():
                    dlg.close()
                    n2 = ui.notification("건너뛰는 중… (이미 있는 RN 을 읽습니다)",
                                         spinner=True, timeout=None)
                    try:
                        ok, msg = await run.io_bound(skip_full_rn, store.key, uid, me)
                    finally:
                        n2.dismiss()
                    ui.notify(msg, color="positive" if ok else "negative",
                              multi_line=True, timeout=15000)
                    if ok:
                        store.set_status(sid, DONE, me)
                        refresh()

                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("취소", on_click=dlg.close).props("flat")
                    ui.button("건너뛰기", icon="skip_next", on_click=_go) \
                        .props("color=primary")
            dlg.open()

        # 전체 이력 RN 은 건너뛸 수 있다 — 이미 만들어져 있는 RN 을 그대로 기준으로
        # 삼는다(사용자 요청 2026-09-21). 다른 단계에는 두지 않는다.
        if (stage.stage_key == "release_note_full"
                and stage.status in (PENDING, REVIEW)):
            ui.button("건너뛰기", icon="skip_next", on_click=_skip_full) \
                .props("flat dense size=sm color=grey-8") \
                .tooltip("이력본을 새로 만들지 않고, 이미 있는 RN 을 기준으로 다음 단계로 간다")

        if stage.status == RUNNING:
            # 실행 중: 스피너 + '지금 하는 일 · 경과시간'을 1초마다 폴링해 라이브 표시.
            from app import progress as _P

            ui.spinner(size="sm")
            live = ui.label("실행 중…").classes(
                "text-caption text-grey-7 ellipsis flex-grow"
            ).style("min-width: 0")

            def _tick(sid=progress_key(store, stage.id), lbl=live):
                info = _P.get(sid)
                if not info:
                    return
                elapsed = int(time.time() - info["started_at"])
                mm, ss = divmod(elapsed, 60)
                et = f"{mm}분 {ss:02d}초" if mm else f"{ss}초"
                lbl.set_text(f"{info['message']}  ·  {et}")

            _tick()
            ui.timer(1.0, _tick)

        # 공용 보드: 이 단계를 마지막으로 조작한 사람 표시(실행 중이 아닐 때)
        if stage.updated_by and stage.status != RUNNING:
            ui.label(f"· {stage.updated_by}").classes("text-caption text-grey-6")
        ui.space()

        def revert(sid=stage.id):
            """이 단계를 대기로 되돌린다 — **그 단계가 만든 것도 함께 지운다**.

            예전에는 상태만 되돌리고 작업물은 두어서, 다시 실행해도 앞선 실행이 남긴
            값이 살아 있었다(실측 2026-09-21 MF52 job-26: 사람이 올려 둔 RN 이 있는데도
            8월에 만든 빈 문서를 물고 있었고 [건너뛰기] 는 '이미 건너뛴 단계' 로 되돌아왔다).
            지우는 일이라 확인 창을 거친다.
            """
            from app.pipeline import stage_reset as _SR
            from app.pipeline.state import get_state as _gs

            gone = _SR.preview(_gs(store.key) or {}, stage.stage_key)

            # 확인 창은 **지울 것이 있을 때만** 만든다. 그래서 여기서 미리 비워 둔다 —
            # 없는데도 _do 가 닫으려 들면 그 자리에서 죽어 단추가 먹통이 된다
            # (실측 2026-09-22 job-31: Newsletter 가 실패해 남긴 값이 없었고,
            #  되돌리기를 눌러도 아무 일도 일어나지 않았다).
            dlg = None

            def _do():
                if dlg is not None:
                    dlg.close()
                store.set_status(sid, PENDING, me)
                _reset_review_state(store.key, stage.stage_key)
                wiped = _SR.reset(store.key, stage.stage_key)
                ui.notify(
                    f"[{stage_label(stage.stage_key, side)}] 을(를) 대기로 되돌렸습니다."
                    + (f" 이 단계의 작업물 {len(wiped)}가지를 지웠습니다." if wiped
                       else " 지울 작업물은 없었습니다."),
                    color="info", multi_line=True)
                refresh()

            if not gone:
                _do()
                return
            with ui.dialog() as dlg, ui.card().classes("w-[34rem] max-w-full"):
                ui.label("이 단계를 되돌립니다").classes("text-h6")
                ui.label(
                    f"[{stage_label(stage.stage_key, side)}] 을(를) 대기로 두고, "
                    "이 단계가 만든 값을 지웁니다. 다시 실행하면 처음부터 새로 만듭니다."
                ).classes("text-body2").style("white-space:normal")
                ui.label("지워지는 것 — " + ", ".join(gone[:12])
                         + (f" 외 {len(gone) - 12}가지" if len(gone) > 12 else "")) \
                    .classes("text-caption text-grey-7").style("white-space:normal")
                ui.label("앞 단계가 정한 값(제품·버전·근거 메일)은 그대로 둡니다.") \
                    .classes("text-caption text-grey-7")
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("취소", on_click=dlg.close).props("flat")
                    ui.button("되돌리기", icon="undo", on_click=_do) \
                        .props("color=negative")
            dlg.open()

        if stage.status == RUNNING:
            # 실행 중 중단(협조적) — 다음 체크포인트에서 멈춘다.
            def _cancel(sid=progress_key(store, stage.id)):
                from app import progress as _P

                if _P.request_cancel(sid):
                    ui.notify("중단을 요청했습니다. 현재 작업을 마치면 멈춥니다.", color="warning")
                else:
                    ui.notify("이미 마무리 단계라 중단할 수 없습니다.", color="info")

            ui.button("중단", icon="stop", on_click=_cancel).props(
                "flat dense size=sm color=negative"
            )
        elif stage.status == REVIEW:
            ui.button(
                "검수하기",
                on_click=lambda sid=stage.id: open_review(sid),
            ).props("color=orange-8 size=sm")
            # 검수 단계에서 바로 되돌리기(검수완료까지 안 가도 됨) — 직전 작업일 때만.
            if revertable:
                ui.button("되돌리기", on_click=revert).props("flat size=sm")
        elif stage.status == DONE:
            # 되돌리기는 '직전 작업'(다음 스텝이 대기)일 때만 노출.
            if revertable:
                ui.button("되돌리기", on_click=revert).props("flat size=sm")
