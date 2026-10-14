"""템플릿 관리 (/templates + 설정 '템플릿' 탭) — RN 제작용 Word/Excel 템플릿 관리.

RN 은 종류가 여러 개(전체 이슈 RN=Dropbox용 / 2~3개 요약 RN 등)이고 템플릿 구조가 다르다.
지금은 전체 이슈 RN 템플릿을 관리한다. 프로그램 내부(data/templates/) 저장, 로컬 전용.
Word 템플릿은 docxtpl(Jinja2) 태그: {{ product }} · {{ version_full }} · {% for c in changes %}{{ c }}{% endfor %} 등.

관리자 전용 기능이다 — 상단 Service 메뉴가 아니라 [설정] 안에 두고, /templates 직접 접근도
관리자만 허용한다.
"""
from nicegui import events, ui

from app.office import templates as T
from app.theme import apply_theme, page_header


def render_templates_content() -> None:
    """템플릿 관리 본문 — /templates 페이지와 설정(관리자) 탭에서 공용으로 그린다."""
    with ui.row().classes("items-center gap-2"):
        ui.icon("description", size="sm").classes("text-primary")
        ui.label("RN 제작용 템플릿").classes("text-h6")
    ui.label(
        "Release Note 제작이 이 템플릿을 채워 PDF 를 만듭니다. "
        "Word 는 docxtpl 태그로 작성: {{ product }} · {{ version_full }} · "
        "{% for c in changes %}{{ c }}{% endfor %}. (프로그램 내부 저장, 로컬 전용)"
    ).classes("text-caption text-grey-7")

    box = ui.column().classes("w-full gap-2 mt-2")

    def _upload_dialog(key: str, label: str, ext: str):
        with ui.dialog() as dlg, ui.card().classes("w-96"):
            ui.label(f"{label}").classes("text-subtitle2")
            ui.label(f"{ext} 파일을 선택하면 바로 저장(교체)됩니다.").classes(
                "text-caption text-grey-6"
            )

            async def _on(e: events.UploadEventArguments):
                ok, msg = T.save(key, await e.file.read())
                ui.notify(msg, color="positive" if ok else "negative")
                dlg.close()
                render()

            ui.upload(on_upload=_on, auto_upload=True, max_files=1).props(
                f'accept={ext} flat bordered'
            ).classes("w-full")
            ui.button("닫기", on_click=dlg.close).props("flat").classes("self-end")
        dlg.open()

    def _delete_dialog(key: str, label: str):
        with ui.dialog() as dlg, ui.card():
            ui.label(f"'{label}' 템플릿을 삭제할까요?")
            with ui.row().classes("justify-end w-full"):
                ui.button("취소", on_click=dlg.close).props("flat")

                def _do():
                    ok, msg = T.delete(key)
                    ui.notify(msg, color="positive" if ok else "negative")
                    dlg.close()
                    render()

                ui.button("삭제", color="negative", on_click=_do)
        dlg.open()

    def render():
        box.clear()
        with box:
            for it in T.status():
                with ui.card().classes("w-full"):
                    with ui.row().classes("items-center justify-between w-full no-wrap"):
                        with ui.row().classes("items-center gap-3 no-wrap"):
                            ui.icon(
                                "check_circle" if it["exists"] else "radio_button_unchecked",
                                color="positive" if it["exists"] else "grey-5",
                            )
                            with ui.column().classes("gap-0"):
                                ui.label(it["label"]).classes("text-subtitle2")
                                detail = (
                                    f'{it["name"]} · {it["mtime"]} · {it["size"]/1024:.1f} KB'
                                    if it["exists"] else f'{it["name"]} · 미등록'
                                )
                                ui.label(detail).classes("text-caption text-grey-6")
                        with ui.row().classes("items-center gap-1 no-wrap"):
                            ui.button(
                                "업로드" if not it["exists"] else "교체",
                                icon="upload",
                                on_click=lambda k=it["key"], l=it["label"], x=it["ext"]:
                                    _upload_dialog(k, l, x),
                            ).props("outline size=sm")
                            if it["exists"]:
                                ui.button(
                                    icon="download",
                                    on_click=lambda p=it["path"]: ui.download(p),
                                ).props("flat round size=sm").tooltip("다운로드")
                                ui.button(
                                    icon="delete", color="negative",
                                    on_click=lambda k=it["key"], l=it["label"]:
                                        _delete_dialog(k, l),
                                ).props("flat round size=sm").tooltip("삭제")

    render()


@ui.page("/templates")
def templates_page():
    apply_theme()
    from app.auth import is_admin

    with page_header("템플릿 관리"):
        if not is_admin():
            # 관리자 전용 — 일반 사용자는 접근 차단.
            with ui.card().classes("w-full items-center q-pa-xl"):
                ui.icon("lock", size="42px").classes("text-grey-5")
                ui.label("템플릿 관리는 관리자만 접근할 수 있습니다.").classes("text-grey-7")
                ui.button("설정으로", icon="settings",
                          on_click=lambda: ui.navigate.to("/settings")).props(
                    "flat color=primary")
            return
        render_templates_content()
