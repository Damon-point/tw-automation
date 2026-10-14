"""FOTA-DB 열람 페이지 (/excelrepo) — FOTA 시스템 실데이터(OsInfo/DownloadPath)를 표로 조회.

기존 '누적 엑셀' 열람을 대체한다. FOTA API(/view/osinfo/list, /view/downloadpath/list)에서
직접 읽어 표시하므로 항상 현재 DB 상태를 본다. 로그인 사용자의 FOTA 자격증명을 사용한다.
"""
from nicegui import run, ui

from app.auth import current_user_id
from app.integrations import fota_system
from app.theme import apply_theme, page_header

_OSINFO_COLS = [
    ("id", "ID"), ("os_type", "OS_Type"), ("version", "Version"),
    ("os_version", "OS_Version"), ("description", "Description"),
    ("release_note", "Release_Note"), ("release_url", "Release_URL"),
    ("update_code", "Update"), ("osreset_code", "OS_Reset"), ("scannerreset_code", "Scanner_Reset"),
]
_DP_COLS = [
    ("id", "ID"), ("os_type", "OS_Type"), ("azure", "Azure"), ("os_version", "OS_Version"),
    ("download_url", "Download_URL"), ("md5", "MD5"), ("download_type", "Type"),
    ("filename", "File"), ("size", "Size"),
]
_WL_COLS = [
    ("id", "ID"), ("sn", "SN"), ("androidApi", "Android API"),
    ("osType", "OS_Type"), ("supportEndDate", "Support End Date"),
]


def _section(fetch_fn, colmap) -> None:
    cols = [{"name": k, "label": lab, "field": k, "align": "left", "sortable": True}
            for k, lab in colmap]
    with ui.row().classes("items-center gap-3 w-full"):
        btn = ui.button("불러오기 / 새로고침", icon="refresh")
        search = ui.input(placeholder="검색 (전체 열)").props("dense clearable").classes("w-64")
        count_lbl = ui.label("").classes("text-caption text-grey-7")
    table = ui.table(columns=cols, rows=[], row_key="id", pagination=20).props(
        "dense flat bordered wrap-cells"
    ).classes("w-full")
    search.bind_value_to(table, "filter")

    async def load():
        uid = current_user_id()
        btn.disable()
        n = ui.notification("FOTA 조회 중...", spinner=True, timeout=None)
        try:
            rows, err = await run.io_bound(fetch_fn, uid)
        finally:
            n.dismiss()
            btn.enable()
        if err:
            count_lbl.set_text("")
            table.rows = []
            table.update()
            ui.notify(f"조회 실패: {err}", color="negative", multi_line=True)
            return
        table.rows = rows
        table.update()
        count_lbl.set_text(f"{len(rows)}건")

    btn.on_click(load)


@ui.page("/excelrepo")
def excel_repo_page():
    apply_theme()
    with page_header("FOTA-DB 열람"):
        ui.label("FOTA 시스템 실데이터 열람 (OsInfo · DownloadPath · WhiteList)").classes("text-h6")
        ui.label(
            "FOTA 시스템에서 직접 읽어옵니다(누적 엑셀 대체). '불러오기'를 눌러 조회하세요."
        ).classes("text-caption text-grey-7")
        with ui.tabs().classes("w-full") as tabs:
            ui.tab("OsInfo", icon="list_alt")
            ui.tab("DownloadPath", icon="cloud_download")
            ui.tab("WhiteList", icon="playlist_add_check")
        with ui.tab_panels(tabs, value="OsInfo").classes("w-full"):
            with ui.tab_panel("OsInfo"):
                _section(fota_system.fetch_osinfo_list, _OSINFO_COLS)
            with ui.tab_panel("DownloadPath"):
                _section(fota_system.fetch_downloadpath_list, _DP_COLS)
            with ui.tab_panel("WhiteList"):
                from app.whitelist import fota as whitelist_fota
                _section(whitelist_fota.fetch_whitelist_list, _WL_COLS)
