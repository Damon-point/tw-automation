"""메일 문구 관리 (/mail-templates + 설정 '메일 문구' 탭).

앱이 보내는 메일의 제목·본문을 여기서 고친다. 고치면 그 다음 발송부터 바로 쓰인다.
누구에게 언제 나가는지는 코드가 정하므로 여기서 바뀌지 않는다 — 문구만 바뀐다.

관리자 전용이다. 고객에게 나가는 문구가 섞여 있어 아무나 손대면 안 된다.
"""
from nicegui import ui

from app.auth import current_username, is_admin
from app import mail_templates as MT
from app.theme import apply_theme, page_header


def _editor(key: str, on_changed) -> None:
    """문구 하나 — 접힌 카드. 펼치면 제목·본문·변수 안내·미리보기가 나온다."""
    cur = MT.get(key)
    mark = "  ·  고침" if cur["edited"] else ""
    with ui.expansion(cur["title"] + mark).classes("w-full").props("dense"):
        ui.label(f"언제: {cur['when']}   |   받는 사람: {cur['to']}").classes(
            "text-caption text-grey-7")
        if cur["edited"]:
            _at = cur["updated_at"].strftime("%Y-%m-%d %H:%M") if cur["updated_at"] else ""
            ui.label(f"기본 문구를 고친 상태입니다 — {cur['updated_by'] or '?'} · {_at}"
                     ).classes("text-caption text-orange-9")

        subj = ui.input("제목", value=cur["subject"]).classes("w-full")
        body = ui.textarea("본문", value=cur["body"]).classes("w-full").props(
            "outlined autogrow input-style='font-family: Consolas, monospace'")

        def _var_rows(items) -> None:
            for name, desc in items:
                with ui.row().classes("items-start gap-2 no-wrap"):
                    ui.label("{{" + name + "}}").classes(
                        "text-caption").style("font-family: Consolas, monospace; "
                                              "white-space: nowrap; color: #1565c0")
                    ui.label(desc).classes("text-caption text-grey-8")

        with ui.expansion("바뀌는 자리 안내").classes("w-full").props("dense"):
            if not cur["vars"]:
                ui.label("이 문구에만 있는 자리는 없습니다.").classes("text-caption")
            _var_rows(cur["vars"].items())
            ui.label("어느 문구에서나 쓸 수 있는 값").classes(
                "text-caption text-grey-7 mt-2")
            _var_rows(cur["auto"].items())
            ui.label("보내는 사람 서명은 본문에 넣지 마세요 — 보낼 때 자동으로 붙습니다."
                     ).classes("text-caption text-grey-7 mt-1")

        preview = ui.column().classes("w-full")

        def show_preview() -> None:
            preview.clear()
            vals = MT.sample(key)
            with preview:
                ui.label("미리보기 — 예시 값을 넣은 모습입니다").classes(
                    "text-caption text-grey-7 mt-2")
                with ui.card().classes("w-full bg-grey-2"):
                    ui.label("제목: " + MT.fill(subj.value, vals)).classes("text-body2")
                    ui.separator()
                    ui.label(MT.fill(body.value, vals)).style(
                        "white-space: pre-wrap; font-family: Consolas, monospace; "
                        "font-size: 12px")

        def save() -> None:
            bad = MT.unknown_vars(key, subj.value, body.value)
            if bad:
                ui.notify("값이 채워지지 않는 자리가 있습니다: "
                          + ", ".join("{{" + b + "}}" for b in bad)
                          + " — 이대로 두면 메일에 그대로 나갑니다.",
                          color="negative", multi_line=True)
                return
            gone = MT.missing_vars(key, subj.value, body.value)
            ok, msg = MT.save(key, subj.value, body.value, current_username())
            if not ok:
                ui.notify(msg, color="negative")
                return
            if gone:
                ui.notify(msg + " (빠진 자리: "
                          + ", ".join("{{" + g + "}}" for g in gone) + ")",
                          color="warning", multi_line=True)
            else:
                ui.notify(msg, color="positive")
            on_changed()

        def back_to_default() -> None:
            ok, msg = MT.reset(key)
            ui.notify(msg, color="positive" if ok else "negative")
            on_changed()

        with ui.row().classes("gap-2 mt-2"):
            ui.button("저장", on_click=save)
            ui.button("미리보기", on_click=show_preview).props("outline")
            ui.button("기본 문구로 되돌리기", on_click=back_to_default).props(
                "flat color=grey-8").set_enabled(cur["edited"])


def render_mail_templates_content() -> None:
    """메일 문구 본문 — /mail-templates 페이지와 설정 탭이 함께 쓴다."""
    with ui.row().classes("items-center gap-2"):
        ui.icon("mail", size="sm").classes("text-primary")
        ui.label("메일 문구").classes("text-h6")
    ui.label(
        "앱이 보내는 메일의 제목과 본문입니다. 고치면 다음 발송부터 바로 적용됩니다. "
        "{{...}} 는 보낼 때 실제 값으로 바뀌는 자리이니 지우지 마세요 — "
        "누구에게 언제 나가는지는 바뀌지 않고 문구만 바뀝니다."
    ).classes("text-caption text-grey-7")

    box = ui.column().classes("w-full gap-1 mt-2")

    def draw() -> None:
        box.clear()
        with box:
            for g in MT.groups():
                ui.label(g).classes("text-subtitle2 text-primary mt-2")
                for key, t in MT.TEMPLATES.items():
                    if t.group == g:
                        _editor(key, draw)

    draw()


@ui.page("/mail-templates")
def mail_templates_page():
    apply_theme()
    with page_header("메일 문구"):
        if not is_admin():
            ui.label("관리자만 볼 수 있는 화면입니다.").classes("text-negative")
            return
        render_mail_templates_content()
