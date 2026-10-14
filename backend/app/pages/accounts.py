"""계정 관리 (/accounts) — 관리자 계정 생성 + 내 발송 계정(이메일) 등록.

- **관리자만** 계정(ID/PW/역할)을 만든다(이메일은 계정 생성 시 받지 않는다).
- 각 사용자는 본인 '발송 계정(이메일 자격증명)'을 등록한다 — 이 이메일이 검수 요청/
  승인/반려 메일의 **보내는 사람**이 되고, POP3 메일 수신에도 함께 쓰인다.
"""
from nicegui import ui

from app.auth import current_user_id, is_admin
from app.database import get_session
from app.models import User
from app.theme import apply_theme, page_header

def _role_color(role: str) -> str:
    return {"admin": "deep-orange-8", "final_reviewer": "green-8",
            "mid_reviewer": "indigo-7"}.get(role, "grey-6")


_ROLE_LABEL = {"admin": "시스템 관리자", "final_reviewer": "최종 승인자",
               "mid_reviewer": "RN 승인자", "user": "일반 사용자"}


@ui.page("/accounts")
def accounts_page():
    apply_theme()
    # 계정 목록이 이름·권한·이메일·권한변경·삭제 5열이라 기본 폭(3xl)으론 좁다.
    with page_header("계정 관리", max_w="max-w-5xl"):
        _render()


def _render():
    from app.pages.credentials import _create_account_form
    from app.pages.settings import _outlook_section

    ui.label("계정 관리").classes("text-h6")
    # 발신 주소는 **고정**이다(TW_MAIL_FROM). 사람 주소는 답장 받을 곳과 수신자
    # 목록에만 쓰인다. 예전 문구는 '등록한 이메일이 발신 주소가 된다' 였는데, 공용
    # 발송 계정으로 바뀐 뒤로는 사실이 아니다(사용자 지적 2026-09-07).
    from app.user_mailer import system_from as _sysfrom

    _from_addr, _from_name, _ = _sysfrom()
    ui.label(
        (f"앱이 보내는 메일은 {_from_name} <{_from_addr}> 로 나갑니다. "
         if _from_addr else "앱이 보내는 메일은 공용 계정으로 나갑니다. ")
        + "계정의 이메일 주소는 답장 받을 곳과 수신자 목록에 쓰입니다 — "
          "아이디가 회사 메일 주소이면 따로 등록할 것이 없습니다."
    ).classes("text-caption text-grey-7")

    # 비밀번호 변경 화면으로 가는 유일한 입구다. 예전에는 첫 로그인 강제 이동으로만
    # 열려서, 스스로 바꾸려면 주소를 직접 쳐야 했다(2026-08-19).
    from app.auth import CHANGE_PW_PAGE

    ui.button("비밀번호 변경", icon="password",
              on_click=lambda: ui.navigate.to(CHANGE_PW_PAGE)).props(
        "flat dense color=primary").classes("self-start")

    if is_admin():
        _create_account_form()
        _account_list()
        ui.separator()
        from app.pages.settings import shared_mailbox_section

        shared_mailbox_section()

    ui.separator()
    # 이 구획의 성격이 사람마다 다르다. 공용 발송 계정(TW_MAIL_ACCOUNT) 주인에게는
    # **앱 전체가 로그인에 쓰는 계정**이고, 나머지에게는 '등록할 것이 없다'는 안내다.
    # _outlook_section 이 그 갈래를 이미 나누므로 제목만 맞춰 준다.
    from app.user_mailer import system_sender_user_id as _sysuid

    # 릴레이 주인에게만 제목을 붙인다. 나머지에게는 _outlook_section 이 '내 발송 주소'
    # 카드로 스스로 말하므로, 여기서 또 제목을 두면 같은 말이 두 번 나온다.
    if _sysuid() == current_user_id():
        ui.label("공용 발송 계정 (SMTP 로그인)").classes("text-subtitle1")
        ui.label(
            "앱이 보내는 모든 메일이 이 계정으로 로그인합니다. "
            "메일 수신(동기화)은 위의 공용 수신 계정이 따로 맡습니다."
        ).classes("text-caption text-grey-7")
    _outlook_section(current_user_id())


@ui.refreshable
def _account_list():
    from app.accounts_admin import (
        ROLE_LABEL,
        delete_user,
        reset_password,
        set_user_role,
    )

    me_id = current_user_id()
    # 보여 줄 것은 '등록했는가' 가 아니라 **실제로 쓰이는 주소**다. 공용 발송 계정으로
    # 바뀐 뒤로 개인 등록은 필요 없고(아이디가 곧 회사 주소, 없으면 사내 인원 명부에서
    # 찾는다), 등록하지 않은 사람에게 빨간 '이메일 미등록' 을 띄우는 것은 사실과 다르다
    # (사용자 지적 2026-09-07 — 4명이 멀쩡한데 전부 빨갛게 떠 있었다).
    from app.user_mailer import user_email as _uemail

    with get_session() as db:
        _users = [(u.id, u.username, getattr(u, "role", "user") or "user")
                  for u in db.query(User).order_by(User.id).all()]
    rows = [(uid, uname, role, _uemail(uid) or "") for uid, uname, role in _users]
    with ui.card().classes("w-full"):
        ui.label("계정 목록").classes("text-subtitle1")
        ui.label(
            "계정을 삭제해도 팀 공용 메일·작업 기록은 관리자 앞으로 이관됩니다."
        ).classes("text-caption text-grey-7")
        # 열 머리글 — 아래 행들과 같은 고정 너비로 맞춘다.
        with ui.row().classes("w-full items-center gap-3 no-wrap px-1").style(
            "border-bottom:1px solid #e0e2de; padding-bottom:4px"
        ):
            ui.label("계정").classes("text-caption text-grey-6").style("width:150px; flex:none")
            ui.label("권한").classes("text-caption text-grey-6").style("width:104px; flex:none")
            ui.label("이메일").classes("text-caption text-grey-6").style("flex:1 1 0; min-width:0")
            ui.label("권한 변경").classes("text-caption text-grey-6").style("width:170px; flex:none")
            ui.label("").style("width:40px; flex:none")

        for uid, uname, role, email in rows:
            with ui.row().classes("w-full items-center gap-3 no-wrap px-1 py-1"):
                ui.label(f"👤 {uname}" + (" (나)" if uid == me_id else "")).classes(
                    "text-body2 ellipsis").style("width:150px; flex:none")
                with ui.element("div").style("width:104px; flex:none"):
                    ui.badge(_ROLE_LABEL.get(role, role), color=_role_color(role))
                # 주소를 **정말로 못 찾은 경우만** 빨갛게. 그때는 이 사람 앞으로
                # 나가는 메일이 시스템 명의로 대체되므로 손볼 일이 맞다.
                ui.label(f"✉ {email or '주소를 찾지 못함'}").classes(
                    "text-caption ellipsis " + ("text-grey-8" if email else "text-red-6")
                ).style("flex:1 1 0; min-width:0").tooltip(
                    email or "아이디가 메일 주소가 아니고 사내 인원 명부에도 없습니다 — "
                             "명부에 추가하거나 이 계정에 주소를 등록하세요.")

                def _change(e, target=uid):
                    ok, msg = set_user_role(target, e.value, me_id)
                    ui.notify(msg, color="positive" if ok else "negative", multi_line=True)
                    _account_list.refresh()

                ui.select(ROLE_LABEL, value=role, on_change=_change).props(
                    "dense outlined").style("width:170px; flex:none").tooltip("권한 변경")

                def _confirm_reset(target=uid, name=uname):
                    with ui.dialog() as dlg, ui.card():
                        ui.label(f"'{name}' 비밀번호를 초기화할까요?").classes("text-subtitle1")
                        ui.label(
                            "초기 비밀번호로 되돌아가고, 본인이 다음 로그인에서 다시 정하게 "
                            "됩니다. 아이디(이메일)로 안내 메일이 함께 발송됩니다."
                        ).classes("text-caption text-grey-7").style("max-width:26rem")

                        def _do():
                            ok, msg, who = reset_password(target)
                            if ok:
                                from app.account_mail import send_reset

                                sent, why = send_reset(me_id, who)
                                msg += (" 안내 메일을 보냈습니다." if sent
                                        else f" 안내 메일은 보내지 못했습니다 — {why}")
                            ui.notify(msg, color="positive" if ok else "negative",
                                      multi_line=True)
                            dlg.close()
                            _account_list.refresh()

                        with ui.row().classes("justify-end gap-2"):
                            ui.button("취소", on_click=dlg.close).props("flat")
                            ui.button("초기화", on_click=_do).props("color=primary")
                    dlg.open()

                ui.button(icon="lock_reset", on_click=_confirm_reset)                     .props("flat dense color=grey-7").tooltip("비밀번호 초기화")

                def _confirm_delete(target=uid, name=uname):
                    with ui.dialog() as dlg, ui.card():
                        ui.label(f"'{name}' 계정을 삭제할까요?").classes("text-subtitle1")
                        ui.label(
                            "이 계정의 이메일·자격증명은 삭제됩니다. 팀 공용 메일·작업 기록은 "
                            "삭제되지 않고 내 계정으로 이관됩니다."
                        ).classes("text-caption text-grey-7").style("max-width:26rem")

                        def _do():
                            ok, msg = delete_user(target, me_id)
                            ui.notify(msg, color="positive" if ok else "negative",
                                      multi_line=True)
                            dlg.close()
                            _account_list.refresh()

                        with ui.row().classes("justify-end gap-2"):
                            ui.button("취소", on_click=dlg.close).props("flat")
                            ui.button("삭제", on_click=_do).props("color=negative")
                    dlg.open()

                ui.button(icon="delete", on_click=_confirm_delete).props(
                    "flat dense color=negative"
                ).style("width:40px; flex:none").tooltip(
                    "본인 계정은 삭제할 수 없습니다" if uid == me_id else "계정 삭제"
                ).set_enabled(uid != me_id)
