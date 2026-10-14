"""비밀번호 변경 (/change-password).

두 가지 경우에 열린다.
  · 관리자가 만들어 준 계정으로 **처음 로그인**했을 때 — 초기 비밀번호가 공통값이라
    본인 것으로 바꾸기 전에는 다른 화면으로 갈 수 없다(app.auth 의 미들웨어가 막는다).
  · 그냥 바꾸고 싶을 때 — [계정 관리] 에서 들어온다. 이때는 상단 메뉴가 그대로 있다.

역할과 무관하게 로그인한 사람이면 누구나 들어올 수 있다.
"""
from __future__ import annotations

from nicegui import ui

from app.auth import (
    INITIAL_PASSWORD,
    change_password,
    current_username,
    must_change_password,
)
from app.theme import apply_theme, page_header


@ui.page("/change-password")
def change_password_page() -> None:
    apply_theme()
    forced = must_change_password()
    # 강제로 온 경우에는 네비를 감춘다 — 다른 화면으로 가도 미들웨어가 되돌려 보낸다.
    with page_header("비밀번호 변경", show_nav=not forced):
        with ui.card().classes("w-full max-w-md mx-auto"):
            ui.label("비밀번호 변경").classes("text-h6")
            if forced:
                with ui.card().classes("w-full").style(
                        "background:#FBEAE8; border-left:4px solid #b3261e; box-shadow:none"):
                    ui.label(
                        f"처음 로그인하셨습니다. 초기 비밀번호({INITIAL_PASSWORD})는 계정을 "
                        "만든 관리자도 알고 있으므로, 본인만 아는 비밀번호로 바꾼 뒤에 "
                        "이용하실 수 있습니다."
                    ).classes("text-caption").style("color:#b3261e; white-space:normal")
            else:
                ui.label("새 비밀번호를 정해 주세요.").classes("text-caption text-grey-7")

            ui.label(f"계정: {current_username() or '-'}").classes("text-caption text-grey-7")

            pw1 = ui.input("새 비밀번호", password=True, password_toggle_button=True) \
                .classes("w-full")
            pw2 = ui.input("새 비밀번호 확인", password=True, password_toggle_button=True) \
                .classes("w-full")
            ui.label("4자 이상. 초기 비밀번호와 같은 값은 쓸 수 없습니다.") \
                .classes("text-caption text-grey-6")

            def save() -> None:
                a, b = (pw1.value or ""), (pw2.value or "")
                if a != b:
                    ui.notify("두 입력이 서로 다릅니다.", color="negative")
                    return
                ok, msg = change_password(a)
                if not ok:
                    ui.notify(msg, color="negative")
                    return
                ui.notify(msg + " 다시 로그인하지 않으셔도 됩니다.", color="positive",
                          multi_line=True)
                # 역할별 시작 화면은 미들웨어가 정한다 — 여기서는 기본 경로로만 보낸다.
                ui.navigate.to("/osupdate")

            pw2.on("keydown.enter", save)
            ui.button("변경", icon="lock_reset", on_click=save) \
                .props("color=primary").classes("w-full q-mt-sm")
