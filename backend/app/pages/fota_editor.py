"""④ FOTA 업로드 검수 팝업 — 이번에 올릴 내용 열람 + FOTA 실제 등록.

표에 그리는 것과 등록 버튼이 보내는 것은 **같은 값**이어야 한다. 예전에는 표가
work/excel_repo/*.xlsx 파일을, 등록은 상태(fota_osinfo_row)를 각각 읽어서, 엑셀을
고쳐도 등록에는 반영되지 않았다(사용자 확정 2026-08-05: 상태값으로 통일).
xlsx 는 FOTA 등록용 산출물로 계속 만들되, 이 화면에서는 보지 않는다.

한 회차가 95.07 · 95.07C1 · 95.07C2 로 나뉘면 OsInfo 도 그만큼 여러 행이다.
등록은 **이미 올라간 행을 빼고 새 행만** 보낸다 — 되돌릴 수 없는 쓰기이고, OsInfo 는
이미 올렸는데 Dropbox 업로드가 늦어 DownloadPath 만 나중에 추가하는 경우가 있다.
"""
from nicegui import run, ui

from app.auth import current_user_id
from app.integrations import fota_system
from app.pipeline import excel_repo
from app.pipeline.state import get_state


def _row_view(label: str, headers: list[str], rows: list) -> None:
    """등록에 실제로 보낼 행을 그대로 표로 — 스키마 순서가 곧 열 순서다."""
    ui.label(f"{label} · {len(rows)}행").classes("text-subtitle2")
    if not rows:
        ui.label("(없음)").classes("text-caption text-grey-7")
        return
    cols = [{"name": h, "label": h, "field": h, "align": "left"} for h in headers]
    data = [{h: ("" if v is None else str(v)) for h, v in zip(headers, r)} for r in rows]
    with ui.scroll_area().classes("w-full").style("max-height: 26vh;"):
        ui.table(columns=cols, rows=data).classes("w-full").props(
            "dense flat bordered wrap-cells")


def render_fota_review(mail_id, on_done=None) -> None:
    user_id = current_user_id()
    st = get_state(mail_id) or {}
    # 예전 상태(fota_osinfo_row 단일)도 그대로 열린다.
    ois = st.get("fota_osinfo_rows") or (
        [st["fota_osinfo_row"]] if st.get("fota_osinfo_row") else [])
    dp = st.get("fota_dp_rows") or []

    ui.label("이번 등록분").classes("text-subtitle1")
    _row_view("OsInfo", excel_repo.HEADERS["OsInfo"], ois)
    _row_view("DownloadPath", excel_repo.HEADERS["DownloadPath"], dp)

    if not ois:
        return

    ui.separator()
    ui.label("FOTA 시스템 실제 등록").classes("text-subtitle2")
    result_box = ui.column().classes("w-full")

    def _show(rep: dict, title: str) -> None:
        result_box.clear()
        with result_box:
            color = "text-green-10" if rep.get("ok") else "text-red-10"
            ui.label(title).classes(f"text-subtitle2 {color}")
            with ui.scroll_area().classes("w-full").style("max-height: 26vh;"):
                ui.label(fota_system.format_report(rep)).classes(
                    "whitespace-pre-wrap text-body2 font-mono")

    async def do_dryrun():
        n = ui.notification("FOTA 미리보기(dry-run) 조회 중...", spinner=True, timeout=None)
        rep = await run.io_bound(fota_system.register_rows, user_id, ois, dp, False)
        n.dismiss()
        _show(rep, "[DRY-RUN 미리보기]")

    async def do_register():
        n = ui.notification("FOTA 등록 여부 확인 중…", spinner=True, timeout=None)
        plan = await run.io_bound(fota_system.plan_registration, user_id, ois, dp)
        n.dismiss()

        n_oi, n_dp = plan["new_osinfo"], plan["new_dp"]
        d_oi, d_dp = plan["dup_osinfo"], plan["dup_dp"]
        nothing = plan["ok"] and not n_oi and not n_dp

        with ui.dialog() as dlg, ui.card().classes("w-[44rem] max-w-full").style(
            "max-height:88vh; overflow-y:auto"
        ):
            ui.label("FOTA 실제 등록 확인").classes("text-h6")
            if not plan["ok"]:
                ui.label(f"⚠ 등록 여부를 확인하지 못했습니다 — {plan['error']}").classes(
                    "text-orange-9")
                ui.label("확인 없이 보내면 중복이 생길 수 있습니다.").classes(
                    "text-caption text-orange-9")

            ui.label("보낼 것").classes("text-subtitle2")
            if nothing:
                ui.label("없음 — 이미 모두 등록되어 있습니다.").classes(
                    "text-caption text-grey-7")
            for row in n_oi:
                ui.label(f"· OsInfo — osv {row[2]} · {row[1]}").classes(
                    "text-caption font-mono")
            if n_dp:
                ui.label(f"· DownloadPath {len(n_dp)}행").classes("text-caption font-mono")

            if d_oi or d_dp:
                ui.label("건너뛸 것 (이미 등록됨)").classes("text-subtitle2 text-grey-8")
                for _row, m in d_oi:
                    ui.label(f"· OsInfo — ID {m.get('id', '?')} · {m.get('version', '')} "
                             f"· osv {m.get('os_version', '')}").classes(
                        "text-caption font-mono text-grey-7")
                for _row, m in d_dp[:5]:
                    ui.label(f"· DownloadPath — ID {m.get('id', '?')} · osv "
                             f"{m.get('os_version', '')} · {m.get('download_type', '')}") \
                        .classes("text-caption font-mono text-grey-7")
                if len(d_dp) > 5:
                    ui.label(f"  … 외 {len(d_dp) - 5}행").classes(
                        "text-caption text-grey-7")

            ui.separator()
            force = ui.checkbox("이미 등록된 것도 다시 보내기").props("dense")
            ui.label("실제 FOTA 시스템(production)에 등록합니다. 되돌리기 어렵습니다.") \
                .classes("text-caption text-negative")

            async def confirm():
                dlg.close()
                send_oi = ois if force.value else n_oi
                send_dp = dp if force.value else n_dp
                if not send_oi and not send_dp:
                    ui.notify("보낼 행이 없습니다.", color="warning")
                    return
                n2 = ui.notification("FOTA 등록 중...", spinner=True, timeout=None)
                rep = await run.io_bound(
                    fota_system.register_rows, user_id, send_oi, send_dp, True)
                n2.dismiss()
                # 기록은 **들어간 것만** 남긴다(계획이 아니라 되읽어 확인된 행).
                from app.pipeline.excel_docs import record_registered

                _n = record_registered(user_id, mail_id, rep)
                _show(rep, f"[{rep.get('mode')}] {'성공' if rep.get('ok') else '오류'}")
                if rep.get("ok"):
                    ui.notify(f"FOTA 등록을 마쳤습니다 — 확인된 OsInfo {_n}건 · "
                              f"DownloadPath {len(rep.get('dp_ok') or [])}건을 "
                              "기록했습니다.", color="positive", multi_line=True)
                    if on_done:
                        on_done()
                else:
                    ui.notify("FOTA 등록 오류 — 결과를 확인하세요.", color="negative")

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("취소", on_click=dlg.close).props("flat")
                btn = ui.button("실제 등록", icon="cloud_upload", on_click=confirm) \
                    .props("color=negative")
                # 보낼 게 없으면 잠근다 — 실수로 두 번 누르는 사고가 여기서 끊긴다.
                # (강제 옵션을 켜면 풀린다.)
                if nothing:
                    btn.disable()
                    force.on_value_change(
                        lambda e, b=btn: b.enable() if e.value else b.disable())
        dlg.open()

    with ui.row().classes("gap-2 mt-2"):
        ui.button("등록 미리보기(dry-run)", icon="visibility", on_click=do_dryrun) \
            .props("outline")
        ui.button("FOTA에 실제 등록", icon="cloud_upload", on_click=do_register) \
            .props("color=negative")
