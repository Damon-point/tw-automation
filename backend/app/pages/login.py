"""로그인 페이지."""
from nicegui import app, ui

from app.auth import authenticate
from app.theme import apply_theme, page_header


@ui.page("/login")
def login_page():
    apply_theme()

    # 이미 로그인되어 있으면 원래 가려던 곳(또는 /osupdate)으로
    if app.storage.user.get("authenticated", False):
        ui.navigate.to(app.storage.user.get("referrer_path", "/osupdate"))
        return

    def forgot():
        """관리자에게 초기화를 요청한다.

        화면에는 계정이 있는지 없는지 알리지 않는다 — 여기서 알려 주면 아이디를
        하나씩 넣어 보며 등록 여부를 떠볼 수 있다. 관리자에게 가는 메일에만 적는다.
        """
        with ui.dialog() as dlg, ui.card().classes("w-full max-w-sm"):
            ui.label("비밀번호 초기화 요청").classes("text-subtitle1")
            ui.label(
                "아이디(회사 이메일)를 남겨 주시면 시스템 관리자에게 전달됩니다. "
                "관리자가 초기화하면 그 주소로 안내 메일이 갑니다."
            ).classes("text-caption text-grey-7").style("white-space:normal")
            who = ui.input("아이디 (회사 이메일)").classes("w-full")

            def send():
                lid = (who.value or "").strip()
                if not lid:
                    ui.notify("아이디를 입력하세요.", color="negative")
                    return
                from app.account_mail import send_forgot_request
                from app.database import get_session
                from app.models import User

                with get_session() as db:
                    exists = db.query(User).filter_by(username=lid).first() is not None
                ok, why = send_forgot_request(lid, exists)
                dlg.close()
                if ok:
                    # 계정 존재 여부와 무관하게 같은 문구를 쓴다.
                    ui.notify("요청을 접수했습니다. 관리자가 확인 후 안내 드립니다.",
                              color="positive", multi_line=True)
                else:
                    ui.notify(f"요청을 전달하지 못했습니다 — {why}",
                              color="negative", multi_line=True)

            who.on("keydown.enter", send)
            with ui.row().classes("justify-end gap-2 w-full"):
                ui.button("취소", on_click=dlg.close).props("flat")
                ui.button("요청", on_click=send).props("color=primary")
        dlg.open()

    def try_login():
        if authenticate(username.value, password.value):
            target = app.storage.user.get("referrer_path", "/osupdate")
            ui.navigate.to(target)
        else:
            ui.notify("아이디 또는 비밀번호가 올바르지 않습니다.", color="negative")

    with page_header("로그인", show_nav=False):
        with ui.card().classes("w-full max-w-sm mx-auto"):
            ui.label("로그인").classes("text-h6")
            username = ui.input("아이디 (회사 이메일)").classes("w-full").props("autofocus")
            password = (
                ui.input("비밀번호", password=True, password_toggle_button=True)
                .classes("w-full")
                .on("keydown.enter", lambda: try_login())
            )
            ui.button("로그인", on_click=try_login).classes("w-full")
            with ui.row().classes("w-full items-center justify-between no-wrap"):
                ui.label("계정은 관리자에게 문의하세요.").classes("text-caption text-grey-7")
                ui.button("비밀번호를 잊으셨나요?", on_click=forgot)                     .props("flat dense no-caps size=sm color=primary")
