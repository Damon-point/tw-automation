"""신규 작업 (/newtasks) — 아직 끝나지 않은 회차만 **평평하게** 모아 건별로 처리한다.

작업 History(/osupdate)는 '제품이 지금 어디까지 왔나'를 보는 화면이라 제품군 → 제품 →
버전 세 겹으로 접힌다. 그 구조는 훑어보기엔 좋지만, 정작 **오늘 손대야 할 회차**는
겹겹이 묻힌다. 이 화면은 그 겹을 걷어내고 회차 하나를 한 줄로 놓는다 — 예전 메일함
보드에서 메일 짝 하나가 카드 하나였던 것과 같은 모양이다.

담는 것은 os_update_jobs.OPEN_STATES(신규 · 진행 중)뿐이다. **시작해도 목록에서
빠지지 않는다** — 시작하자마자 사라지면 같은 자리에서 이어 처리할 수가 없다. 모든 단계가
끝나면 그때 빠지고, 작업 History 에만 기록으로 남는다.

카드 안쪽([할 일] · [결과물])은 작업 History 와 **같은 화면 코드**를 그대로 쓴다.
두 화면이 갈라져 한쪽만 고쳐지는 일이 없도록, 여기서 새로 그리지 않고 불러다 쓴다.
"""
from __future__ import annotations

from nicegui import ui

from app import os_update_jobs as JOBS
from app.pages import os_update as OU
from app.pages.mail_sync import sync_mailbox
from app.theme import apply_theme, page_header


@ui.page("/newtasks")
def new_tasks_page():
    apply_theme()
    with page_header("신규 작업", max_w="max-w-[104rem]"):
        _render()


def _render() -> None:
    ui.label("신규 작업").classes("text-h6")

    state = {"q": ""}

    @ui.refreshable
    def items() -> None:
        rows = JOBS.open_rounds(state["q"])
        if not rows:
            _empty(bool(state["q"]))
            return
        n_new = sum(1 for d in rows if d["state"] == JOBS.NEW)
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            ui.label(f"신규 {n_new}건").classes("text-subtitle2").style(
                "font-weight:700; color:#b3261e" if n_new else "font-weight:700")
            ui.label(f"· 진행 중 {len(rows) - n_new}건").classes(
                "text-caption text-grey-7")
        for d in rows:
            _round_card(d, items.refresh)

    with ui.row().classes("w-full items-center no-wrap gap-2"):
        def _on_search(e) -> None:
            state["q"] = (e.value or "").strip()
            items.refresh()

        ui.input(placeholder="제품명 · Android · Type 검색", on_change=_on_search) \
            .props("dense outlined clearable").classes("w-80")

        ui.space()
        ui.button("작업 History", icon="history",
                  on_click=lambda: ui.navigate.to("/osupdate")) \
            .props("outline no-caps").tooltip("제품별 전 회차 보기")
        _sync = ui.button("동기화", icon="sync").props("color=primary no-caps")
        _sync.tooltip("공용 수신함에서 새 메일 받기")
        _sync.on_click(lambda: sync_mailbox(_sync, items.refresh))
        ui.button(icon="refresh", on_click=items.refresh) \
            .props("flat dense").tooltip("목록 새로 읽기")

    items()


def _empty(filtered: bool) -> None:
    with ui.card().classes("w-full items-start"):
        if filtered:
            ui.label("조건에 맞는 회차가 없습니다.").classes("text-grey-7")
            return
        ui.label("처리할 신규 작업이 없습니다.").classes("text-grey-7")
        ui.button("작업 History 열기", icon="history",
                  on_click=lambda: ui.navigate.to("/osupdate")) \
            .props("outline no-caps")


def _round_card(d: dict, on_change) -> None:
    """회차 하나 = 카드 하나. 펼치면 [할 일] · [결과물] — 작업 History 와 같은 내용."""
    r, state = d["product"], d["state"]
    # 상태는 왼쪽 색 바로만 나타낸다(작업 History 와 같은 색 규칙).
    with ui.card().classes("w-full nicegui-card-tight").style(
        f"border-left:5px solid {OU._STATE_BORDER[state]}"
    ):
        exp = ui.expansion().classes("w-full").props("dense hide-expand-icon")
        with exp.add_slot("header"):
            with ui.row().classes("w-full items-center gap-3 no-wrap"):
                ui.icon("expand_more", size="sm").classes("text-grey-6").style(
                    "width:20px; flex:none")
                ui.label(OU.sku_text(r)).classes("text-subtitle1").style(
                    "font-weight:700")
                ui.label(d["version"]).classes("text-body2 font-mono").style(
                    "font-weight:700")
                ui.badge(JOBS.VERSION_LABELS[state],
                         color=JOBS.VERSION_COLORS[state]).props("rounded")
                ui.label(_why(d)).classes("text-caption text-grey-7 ellipsis").style(
                    "flex:1 1 0; min-width:0")
                if d["job"] is not None:
                    done, total = JOBS.job_progress(d["job"]["id"])
                    ui.label(f"{done}/{total}").classes(
                        "text-caption text-grey-7 font-mono")

        with exp:
            with ui.tabs().props("dense no-caps align=left").classes("w-full") as tabs:
                t_todo = ui.tab("할 일")
                t_out = ui.tab("결과물")

            @ui.refreshable
            def out_panel() -> None:
                OU._artifacts_panel(JOBS.artifacts_for(d))

            @ui.refreshable
            def head_status() -> None:
                OU._actions(r, d, on_change)

            def on_stage_change() -> None:
                """단계가 바뀌면 요약만 갱신한다 — 목록 전체를 다시 그리면 화면이 멈춘다."""
                head_status.refresh()
                out_panel.refresh()

            with ui.tab_panels(tabs, value=t_todo).classes("w-full").props("keep-alive"):
                with ui.tab_panel(t_todo).classes("q-pa-sm"):
                    head_status()
                    OU._todo_panel(r, d, on_change, on_stage_change)
                with ui.tab_panel(t_out).classes("q-pa-sm"):
                    out_panel()


def _why(d: dict) -> str:
    """이 회차가 왜 목록에 있는지 한 줄로 — 카드를 펼치지 않고도 고를 수 있게."""
    u = d.get("update") or {}
    if d["state"] == JOBS.RUNNING_:
        return "진행 중"
    if u.get("source") == "deploy":
        return "배포 공지로 감지 (트리거 메일 없음)"
    if u:
        got = (u.get("approval") or u.get("submission") or {}).get("received_at", "")
        tail = f" · {got[:10]}" if got else ""
        return ("Google 승인 완료" if u.get("approved") else "승인 대기") + tail
    return ""
