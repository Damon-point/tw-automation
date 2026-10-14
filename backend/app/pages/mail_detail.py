"""메일 본문 페이지 (/mail/{mail_id}) + 다른 화면에서 띄우는 미리보기 창."""
import base64

from nicegui import ui

from app.auth import current_user_id
from app.database import get_session
from app.models import CachedMail
from app.theme import apply_theme, page_header


def _get_mail(user_id: int, mail_id: int) -> CachedMail | None:
    # 팀 공용 보드: 누가 받은 메일이든 열람 가능(user_id 필터 안 함).
    with get_session() as db:
        return db.query(CachedMail).filter_by(id=mail_id).first()


def _body(mail: CachedMail) -> None:
    """본문 렌더 — 페이지와 미리보기 창이 같은 것을 쓴다."""
    if mail.body_html:
        # HTML 본문을 base64 data URI 로 iframe 에 실어 안전하게 격리 표시.
        # (srcdoc 이스케이프 이슈를 피하려고 data URI 사용, 스크립트는 sandbox 로 차단)
        b64 = base64.b64encode(mail.body_html.encode("utf-8")).decode("ascii")
        # sanitize=False: DOMPurify 가 iframe 을 제거하지 못하게 함.
        # (본문 스크립트는 iframe 의 sandbox 로 차단하므로 안전)
        ui.html(
            '<iframe sandbox '
            'style="width:100%;min-height:60vh;border:0;background:white;" '
            f'src="data:text/html;charset=utf-8;base64,{b64}"></iframe>',
            sanitize=False,
        ).classes("w-full")
    elif mail.body_text:
        ui.label(mail.body_text).classes("whitespace-pre-wrap")
    else:
        ui.label("(본문이 없습니다)").classes("text-grey-7")


def preview(mail_id: int) -> None:
    """메일 본문을 그 자리에서 창으로 띄운다.

    다른 탭으로 넘기면 보던 작업 화면을 잃는다 — 근거 메일은 확인하고 곧바로 돌아오는
    용도라 창으로 연다.
    """
    mail = _get_mail(current_user_id(), mail_id)
    with ui.dialog() as dlg, ui.card().classes("w-[76rem] max-w-full").style(
        "max-height:90vh; overflow-y:auto"
    ):
        if mail is None:
            ui.label("메일을 찾을 수 없습니다.").classes("text-negative")
        else:
            with ui.row().classes("w-full items-start gap-2 no-wrap"):
                with ui.column().classes("gap-0").style("flex:1 1 0; min-width:0"):
                    ui.label(mail.subject or "(제목 없음)").classes("text-subtitle1") \
                        .style("font-weight:700")
                    ui.label(f"{mail.sender} · {mail.received_at}") \
                        .classes("text-caption text-grey-7")
                ui.button(icon="close", on_click=dlg.close).props("flat dense")
            ui.separator()
            _body(mail)
    dlg.open()


@ui.page("/mail/{mail_id}")
def mail_detail_page(mail_id: int):
    apply_theme()
    user_id = current_user_id()
    mail = _get_mail(user_id, mail_id)

    with page_header("메일 보기"):

        if mail is None:
            ui.label("메일을 찾을 수 없습니다.").classes("text-negative")
            return

        with ui.card().classes("w-full"):
            ui.label(mail.subject or "(제목 없음)").classes("text-h6")
            ui.label(f"보낸사람: {mail.sender}").classes("text-caption text-grey-8")
            ui.label(f"받은시각: {mail.received_at}").classes("text-caption text-grey-6")

        with ui.card().classes("w-full"):
            _body(mail)
