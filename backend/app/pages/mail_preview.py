"""보낼 메일 미리보기 — 실제로 나갈 모양 그대로 보여 준다.

메일은 되돌릴 수 없다. 특히 회신은 고객이 보낸 요청 스레드에 붙어 나가므로,
누르기 전에 인용부·서명·글꼴이 어떻게 실리는지 확인할 수 있어야 한다
(사용자 요청 2026-08-19).

미리보기는 **iframe 안에서** 그린다. 메일 서식(MsoNormal·맑은 고딕)을 화면에
그대로 풀면 앱 CSS 와 섞여 실제와 다르게 보인다.
"""
from __future__ import annotations

import html as _html

from nicegui import ui


def _iframe(doc: str, height: str = "60vh") -> None:
    # sanitize=False 필수 — 기본값이면 DOMPurify 가 <iframe> 을 지운다.
    src = _html.escape(doc, quote=True)
    ui.html(
        f'<iframe srcdoc="{src}" sandbox="" '
        f'style="width:100%;height:{height};border:1px solid #dcdcdc;'
        f'border-radius:6px;background:#fff"></iframe>',
        sanitize=False,
    ).classes("w-full")


def open_mail_preview(user_id: int, subject: str, body: str, *,
                      to: str = "", quote_html: str = "",
                      cc: str = "") -> None:
    """보낼 메일 한 통을 그대로 그려 보여 준다."""
    from app.user_mailer import build_html_mail, get_send_identity

    ident = get_send_identity(user_id)
    doc = build_html_mail(body, quote=quote_html,
                          signature=(ident.signature if ident else ""))

    # 폭은 **inline style** 로 준다 — Tailwind 클래스(w-[60rem])만 쓰면 Quasar 의
    # `.q-dialog__inner--minimized > div {max-width:560px}` 가 이겨 좁게 눌린다
    # (실측 2026-08-19). 앱의 다른 팝업도 같은 방식이다.
    with ui.dialog() as dlg, ui.card().classes("w-full") \
            .style("max-width:1000px; max-height:92vh"):
        ui.label("보낼 메일 미리보기").classes("text-subtitle1")
        rows = [("보내는 사람", ident.email if ident else "(발송 계정 미등록)"),
                ("받는 사람", to or "(지정 안 됨)"),
                ("참조", cc or "-"),
                ("제목", subject or "(제목 없음)")]
        for k, v in rows:
            with ui.row().classes("items-start gap-2 no-wrap w-full"):
                ui.label(k).classes("text-caption text-grey-7") \
                    .style("min-width:5.5rem;flex:0 0 auto")
                ui.label(v).classes("text-caption text-grey-9 grow") \
                    .style("word-break:break-all")
        ui.separator()
        _iframe(doc)
        ui.label(
            "실제 발송 화면과 같은 서식입니다. 인용된 원본은 받는 사람 메일함의 "
            "원문 서식을 그대로 따릅니다."
        ).classes("text-caption text-grey-6")
        with ui.row().classes("w-full justify-end"):
            ui.button("닫기", on_click=dlg.close).props("flat")
    dlg.open()


def preview_button(user_id: int, subject_fn, body_fn, *, to: str = "",
                   quote_html_fn=None, cc: str = "", label: str = "미리보기"):
    """[미리보기] 버튼. 값은 누를 때 읽는다(입력칸을 고친 뒤 눌러도 반영되게)."""
    def _open() -> None:
        open_mail_preview(
            user_id, subject_fn() or "", body_fn() or "",
            to=to, cc=cc,
            quote_html=(quote_html_fn() if quote_html_fn else ""))

    return ui.button(label, icon="visibility", on_click=_open) \
        .props("flat dense color=primary")
