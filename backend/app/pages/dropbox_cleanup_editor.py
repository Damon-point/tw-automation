"""⑧ Dropbox 정리 — 등록된 Dropbox 경로 4개별로 구버전 파일을 보여주고, 사람이 지운다.

**자동 삭제는 없다.** 단계 실행도, 검수 완료도 아무것도 지우지 않는다. 이 화면에서
경로마다 있는 [이 경로 구버전 삭제] 버튼을 누르고 확인창을 통과해야만 삭제된다
(사용자 확정 2026-08-05).

경로 4개는 제품 관리에 등록한 그 네 칸이다 — OS Image(sdcard)·sdcard zip·flash·
Release Note. 등록이 없으면 그 그룹은 조회하지 않는다.
"""
import os

from nicegui import run, ui

from app.auth import current_user_id

_MAX_LIST = 200


def render_cleanup(mail_id, on_done=None) -> None:
    user_id = current_user_id()
    from app.pipeline.dropbox_release import cleanup_groups

    box = ui.column().classes("w-full")

    @ui.refreshable
    def body() -> None:
        groups, err = cleanup_groups(user_id, mail_id)
        if err:
            ui.label(f"✘ {err}").classes("text-red-10")
            return
        total = sum(len(g["files"]) for g in groups)
        ui.label(f"구버전 파일 {total}개 · Dropbox 경로 4개").classes("text-subtitle2")
        unsafe = any(g.get("unsafe") for g in groups)
        if unsafe:
            ui.label(
                "⚠ Dropbox 업로드(③) 기록이 없습니다 — 아래 목록에 이번 회차 파일이 "
                "섞여 있을 수 있어 삭제를 막았습니다."
            ).classes("text-body2").style("color:#b3261e; font-weight:700")

        for g in groups:
            with ui.card().classes("w-full nicegui-card-tight").style(
                "border-left:4px solid " + ("#b3261e" if g["files"] else "#c7cac4")
            ):
                with ui.row().classes("w-full items-center gap-2 no-wrap"):
                    ui.label(g["label"]).classes("text-body2").style("font-weight:700")
                    ui.label(f"{len(g['files'])}개").classes("text-caption text-grey-7")
                    ui.space()
                    if g["files"] and not g.get("unsafe"):
                        ui.button("이 경로 구버전 삭제", icon="delete",
                                  on_click=lambda gg=g: _confirm(gg)) \
                            .props("dense size=sm no-caps color=negative")
                    elif g["files"]:
                        ui.label("삭제 잠김").classes("text-caption text-grey-6")
                ui.label(g["base"] or "(경로 미등록)") \
                    .classes("text-caption font-mono text-grey-7 ellipsis") \
                    .style("min-width:0")
                if g["skipped"]:
                    ui.label(g["skipped"]).classes("text-caption text-grey-6")
                elif not g["files"]:
                    ui.label("구버전 없음").classes("text-caption text-grey-6")
                else:
                    with ui.scroll_area().classes("w-full").style("max-height:18vh"):
                        for p in g["files"][:_MAX_LIST]:
                            ui.label(p).classes("text-caption font-mono text-grey-8")
                        if len(g["files"]) > _MAX_LIST:
                            ui.label(f"… 외 {len(g['files']) - _MAX_LIST}개") \
                                .classes("text-caption text-grey-6")

    def _confirm(g: dict) -> None:
        with ui.dialog() as dlg, ui.card().classes("w-[40rem] max-w-full").style(
            "max-height:82vh; overflow-y:auto"
        ):
            ui.label(f"{g['label']} — 구버전 삭제").classes("text-h6")
            ui.label(f"{g['base']}").classes("text-caption font-mono text-grey-7")
            ui.label(f"아래 {len(g['files'])}개를 Dropbox 에서 **실제 삭제**합니다. "
                     "되돌릴 수 없습니다.").classes("text-negative")
            with ui.scroll_area().classes("w-full").style("max-height:34vh"):
                for p in g["files"]:
                    ui.label(os.path.basename(p)).classes("text-caption font-mono")

            async def go():
                dlg.close()
                from app.pipeline.dropbox_release import delete_group

                n = ui.notification(f"{g['label']} 삭제 중…", spinner=True, timeout=None)
                res = await run.io_bound(delete_group, user_id, mail_id, g["key"])
                n.dismiss()
                box.clear()
                with box:
                    ui.label(res).classes("whitespace-pre-wrap text-body2")
                ui.notify(res.splitlines()[0], color="positive", multi_line=True)
                body.refresh()
                if on_done:
                    on_done()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("취소", on_click=dlg.close).props("flat")
                ui.button("실제 삭제", icon="delete", on_click=go).props("color=negative")
        dlg.open()

    body()
    box
