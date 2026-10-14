"""최종 승인자 검수 (/final-reviews) — RN for Sales 를 보고 승인/반려만 한다.

RN 승인(1차)은 History 관리용 RN(전체 이력본)을 확정하는 결재이고, 그 내용을 작성자가
고르고 편집해 만든 **RN for Sales** 가 여기로 온다(사용자 확정 2026-08-04).

화면에 두는 것은 넷이다 — 문서 미리보기 · 공개 선별표 · 입력 값 · 승인/반려.
선별표와 입력 값은 **읽기 전용**이다(사용자 확정 2026-09-08). 문서만 보아서는 무엇을
빼기로 했는지 알 수 없어 결재 판단에 근거가 모자랐다. 고치는 일은 여전히 작성자 몫이라
편집은 열지 않는다.
"""
from __future__ import annotations

from nicegui import run, ui

from app.auth import (
    can_final_decide,
    current_user_id,
    current_username,
)
from app.database import get_session
from app.models import CachedMail
from app.pairing import build_tasks
from app.pages.mid_review import _JobTask, render_rn_preview
from app.pages.rn_review import render_review_controls
from app.pipeline.state import get_state
from app.theme import apply_theme, page_header

FINAL_STATE_KEY = "rn_review"          # RN for Sales 검수 상태(1차 rn_mid_review 와 분리)
NEWSLETTER_STATE_KEY = "newsletter_review"


@ui.page("/final-reviews")
def final_reviews_page():
    apply_theme()
    with page_header("최종 검수", max_w="max-w-[92rem]"):
        _render()


def _pending_items(state_key: str) -> list[dict]:
    """그 결재가 요청된(또는 끝난) 작업 목록.

    /reviews 와 같은 방식으로 메일 짝과 /osupdate 작업 양쪽에서 모은다.
    """
    from app import os_update_jobs as JOBS

    out: list[dict] = []
    with get_session() as db:
        mails = db.query(CachedMail).all()
    tasks = list(build_tasks(mails)) + [_JobTask(j) for j in JOBS.list_jobs()]
    for t in tasks:
        st = get_state(t.task_id) or {}
        review = st.get(state_key) or {}
        if review.get("status") in ("requested", "approved"):
            out.append({"task": t, "state": st, "review": review})
    # 대기 중인 건이 위로, 그다음은 **제품 · OS 버전 순**이다. 요청 시각 순으로 늘어놓으면
    # 같은 제품이 목록 여기저기 흩어져 무엇이 남았는지 한눈에 안 들어온다.
    def _k(x):
        _s = x["state"]
        return (x["review"].get("status") != "requested",
                str(_s.get("product") or ""),
                str(_s.get("version_full") or _s.get("os_version") or ""))

    out.sort(key=_k)
    return out


def _render() -> None:
    ui.label("최종 검수").classes("text-h6")
    ui.label(
        "최종 승인자가 결재하는 두 가지입니다 — 고객 배포용 RN for Sales 와 Newsletter. "
        "내용을 확인하시고 승인 또는 반려해 주세요."
    ).classes("text-caption text-grey-7")

    if not can_final_decide():
        ui.label("최종 승인자 전용 페이지입니다.").classes("text-red-7")
        return

    @ui.refreshable
    def items():
        with ui.tabs().classes("w-full") as tabs:
            rn_tab = ui.tab("Release Note for Sales")
            nl_tab = ui.tab("Newsletter")
        rn_items = _pending_items(FINAL_STATE_KEY)
        nl_items = _pending_items(NEWSLETTER_STATE_KEY)
        # 대기 중인 쪽을 먼저 띄운다 — 처리할 것이 있는 탭이 열려 있어야 한다.
        _wait_rn = any(x["review"].get("status") == "requested" for x in rn_items)
        with ui.tab_panels(tabs, value=(rn_tab if _wait_rn or not nl_items else nl_tab)) \
                .classes("w-full"):
            with ui.tab_panel(rn_tab):
                ui.label(
                    "승인하시면 PDF 가 확정되고 파일서버에 저장되며 "
                    "Release Note 제작 단계가 완료됩니다."
                ).classes("text-caption text-grey-7")
                if not rn_items:
                    ui.label("검수 요청된 항목이 없습니다.").classes("text-grey-7")
                else:
                    _list_summary(rn_items)
                for it in rn_items:
                    _render_rn_item(it, on_change=items.refresh)
            with ui.tab_panel(nl_tab):
                ui.label("승인하시면 Newsletter 제작 단계가 완료됩니다.") \
                    .classes("text-caption text-grey-7")
                if not nl_items:
                    ui.label("검수 요청된 항목이 없습니다.").classes("text-grey-7")
                else:
                    _list_summary(nl_items)
                for it in nl_items:
                    _render_nl_item(it, on_change=items.refresh)

    items()


def _list_summary(items: list[dict]) -> None:
    """목록 머리 — 몇 건이고 그중 몇 건이 대기인지."""
    wait = sum(1 for x in items if x["review"].get("status") == "requested")
    ui.label(f"총 {len(items)}건 · 검수 대기 {wait}건").classes(
        "text-caption").style("color:#3C6553; font-weight:600")


def _item_header(exp, st, review, wait_label: str, done_label: str) -> None:
    """목록 한 줄의 머리 — 제품/버전 · 상태 · 요청자."""
    status = review.get("status") or "none"
    color = {"requested": "orange-8", "approved": "green-8"}.get(status, "grey-6")
    label = {"requested": wait_label, "approved": done_label}.get(status, status)
    product = st.get("product") or ""
    version = st.get("version_full") or st.get("os_version") or ""
    with exp.add_slot("header"):
        with ui.row().classes("w-full items-center gap-3 no-wrap"):
            ui.label(f"{product} {version}".strip()).classes("text-subtitle1").style(
                "font-weight:700; min-width:14rem")
            ui.badge(label, color=color)
            ui.label(
                f"요청자 {review.get('requester_name', '')} · "
                f"{str(review.get('requested_at', ''))[:16].replace('T', ' ')}"
            ).classes("text-caption text-grey-7")


def _render_nl_item(it: dict, on_change) -> None:
    """Newsletter 최종 승인 — 완성본을 보고 승인/반려만 한다."""
    t, st, review = it["task"], it["state"], it["review"]
    mail_id = t.task_id
    # 항상 접어 둔다 — RN 승인 화면과 같다. 대기 건을 자동으로 펼치면 6건이 한꺼번에
    # 열려 목록이 아니라 벽이 되고, 하나를 승인·반려해 목록을 다시 그릴 때마다 나머지가
    # 도로 펼쳐진다(사용자 지적 2026-09-08).
    exp = ui.expansion().classes(
        "w-full").props("expand-separator")
    _item_header(exp, st, review, "승인 대기", "승인 완료")
    with exp:
        with ui.column().classes("w-full gap-3 px-2 pb-2"):
            from app.pages.newsletter import render_newsletter_editor

            # 결재자는 내용만 본다 — 편집·발송은 작성자 몫이라 둘 다 끈다.
            render_newsletter_editor(mail_id, on_sent=on_change,
                                     allow_send=False, edit=False)

            def _nl_done(_id=mail_id) -> None:
                from app.task_store import complete_stage

                complete_stage(_id, "newsletter", current_username() or "")
                on_change()

            render_review_controls(
                mail_id, None, current_username(), state_key=NEWSLETTER_STATE_KEY,
                label="Newsletter", stage_hint="Newsletter",
                on_approve=_nl_done, on_close=on_change,
                role_label="최종 승인자", request_verb="검수",
            )


def _render_rn_item(it: dict, on_change) -> None:
    t, st, review = it["task"], it["state"], it["review"]
    mail_id = t.task_id

    # 대기 중인 건만 펼쳐 둔다 — 끝난 건까지 열려 있으면 목록이 길어진다.
    # 항상 접어 둔다 — RN 승인 화면과 같다. 대기 건을 자동으로 펼치면 6건이 한꺼번에
    # 열려 목록이 아니라 벽이 되고, 하나를 승인·반려해 목록을 다시 그릴 때마다 나머지가
    # 도로 펼쳐진다(사용자 지적 2026-09-08).
    exp = ui.expansion().classes(
        "w-full").props("expand-separator")
    _item_header(exp, st, review, "검수 대기", "검수 완료")

    with exp:
        with ui.column().classes("w-full gap-3 px-2 pb-2"):
            # ① 문서 미리보기 — 판단 근거는 문서 자체다. 이 화면은 문서를 보는 것이
            #    전부라 세로를 넉넉히 준다(작은 창에서 한 쪽이 통째로 보이도록).
            render_rn_preview(mail_id, height="88vh")

            # ② 무엇을 싣고 뺐는지 · 어떤 값이 들어갔는지 **읽기 전용**으로 보여준다
            #    (사용자 확정 2026-09-08). 문서만으로는 '무엇을 뺐는지' 를 알 수 없어
            #    결재 판단에 근거가 모자랐다. 고치는 일은 작성자 몫이라 여기서는 잠근다
            #    — editable=False 면 편집기가 저장 경로를 아예 만들지 않는다.
            with ui.expansion("공개 선별표 · 입력 값 (읽기 전용)", icon="fact_check")                     .classes("w-full").props("dense"):
                from app.pages.rn_edit_table import render_selection_editor

                render_selection_editor(mail_id, editable=False, exclude_note=True)

            # ③ 승인 / 반려. 요청 버튼은 이 화면에 나오지 않는다
            #    (status 가 항상 requested·approved 라 render_review_controls 가 결정 버튼만 그린다).
            async def _final_done(_id=mail_id) -> None:
                from app.pages.rn_review import _final_finalize

                n = ui.notification("RN for Sales 확정·저장 중…", spinner=True, timeout=None)
                try:
                    res = await run.io_bound(
                        _final_finalize, _id, current_user_id(),
                        current_username() or "")
                except Exception as e:  # noqa: BLE001
                    n.dismiss()
                    ui.notify(f"확정 실패: {type(e).__name__}: {e}", color="negative",
                              multi_line=True)
                    on_change()
                    return
                n.dismiss()
                for msg, c in res.get("notes", []):
                    ui.notify(msg, color=c, multi_line=True, timeout=8000)
                on_change()

            render_review_controls(
                mail_id, None, current_username(), state_key=FINAL_STATE_KEY,
                label="Release Note for Sales", stage_hint="Release Note",
                on_approve=_final_done, on_close=on_change,
                role_label="최종 승인자", request_verb="검수",
            )
