"""자격증명 현황 페이지 (/credentials).

계정별로 어떤 자격증명이 등록되어 있는지 '메타정보'만 보여준다.
※ 실제 비밀번호/토큰 값은 절대 표시하지 않는다(설정됨/미설정으로만).
"""
from nicegui import ui

from app.database import get_session
from app.models import (
    DropboxCredential,
    MailchimpCredential,
    MailCredential,
    SftpCredential,
    User,
    WebCredential,
)
from app.theme import apply_theme, page_header


def _yn(v) -> str:
    return "설정됨" if v else "미설정"


def _ts(dt) -> str:
    return dt.strftime("%Y-%m-%d") if dt else "-"


def _role_color(role: str) -> str:
    return {"admin": "deep-orange-8", "mid_reviewer": "indigo-7"}.get(role, "grey-6")


_ROLE_LABEL = {"admin": "관리자(최종)", "mid_reviewer": "RN 승인자", "user": "일반 사용자"}


def _create_account_form():
    """admin 전용 — 새 계정(admin/user)을 만든다."""
    import re

    from app.auth import INITIAL_PASSWORD
    from app.security import hash_password

    _EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

    with ui.card().classes("w-full").style("border-left:3px solid #4A7C67;"):
        ui.label("새 계정 만들기 (admin 전용)").classes("text-subtitle1")
        ui.label("아이디와 역할만 정하면 됩니다. 비밀번호는 초기값으로 만들어집니다.").classes(
            "text-caption text-grey-7"
        )
        user_in = ui.input("아이디 (회사 이메일)",
                           placeholder="hong.gildong@pointmobile.com").classes("w-full")
        with ui.row().classes("items-center gap-2"):
            ui.icon("key").classes("text-grey-7")
            ui.label(f"초기 비밀번호 {INITIAL_PASSWORD} — 만들면 그 이메일로 서비스 주소와 "
                     "함께 안내가 발송되고, 첫 로그인에서 바꾸게 됩니다.")                 .classes("text-caption text-grey-8")
        role_sel = ui.select(
            {"user": "일반 사용자 (user)", "mid_reviewer": "RN 승인자 (RN 1차 승인)",
             "admin": "관리자 (최종 승인)"},
            value="user", label="역할",
        ).classes("w-64")

        def create():
            uname = (user_in.value or "").strip()
            if not uname:
                ui.notify("아이디를 입력하세요.", color="negative")
                return
            # 아이디가 곧 안내 메일을 받을 주소다 — 형식이 아니면 안내가 갈 곳이 없다.
            if not _EMAIL_RE.match(uname):
                ui.notify("아이디는 회사 이메일 주소 형식이어야 합니다.", color="negative")
                return
            with get_session() as db:
                if db.query(User).filter_by(username=uname).first():
                    ui.notify(f"'{uname}' 은 이미 존재하는 아이디입니다.", color="negative")
                    return
                db.add(User(
                    username=uname, password_hash=hash_password(INITIAL_PASSWORD),
                    role=role_sel.value, must_change_password=True,
                ))
                db.commit()
            from app.account_mail import send_welcome
            from app.auth import current_user_id

            sent, why = send_welcome(current_user_id(), uname)
            note = ("안내 메일을 보냈습니다." if sent
                    else f"안내 메일은 보내지 못했습니다 — {why}")
            ui.notify(
                f"'{uname}' 계정({_ROLE_LABEL.get(role_sel.value, role_sel.value)})을 만들었습니다. "
                f"초기 비밀번호는 {INITIAL_PASSWORD} 이며 첫 로그인에서 변경하게 됩니다. {note}",
                color="positive" if sent else "warning", multi_line=True,
            )
            ui.navigate.reload()

        ui.button("계정 만들기", icon="person_add", on_click=create).props("color=primary")


@ui.page("/credentials")
def credentials_page():
    apply_theme()
    with page_header("자격증명 현황"):
        render_credentials_content()


def render_credentials_content():
    """자격증명 현황 본문. 독립 페이지와 설정 탭에서 함께 재사용한다."""
    if True:
        ui.label("계정별 자격증명 등록 현황").classes("text-h6")
        ui.label(
            "실제 비밀번호·토큰 값은 표시하지 않습니다."
        ).classes("text-caption text-grey-7")

        from app.auth import is_admin

        if is_admin():
            _create_account_form()

        with get_session() as db:
            users = db.query(User).order_by(User.id).all()
            data = []
            for u in users:
                mail = db.query(MailCredential).filter_by(user_id=u.id).first()
                fs = {
                    s.target: s
                    for s in db.query(SftpCredential).filter_by(user_id=u.id).all()
                }
                dbx = db.query(DropboxCredential).filter_by(user_id=u.id).first()
                webs = {
                    w.target: w
                    for w in db.query(WebCredential).filter_by(user_id=u.id).all()
                }
                mc = db.query(MailchimpCredential).filter_by(user_id=u.id).first()
                data.append((u, mail, fs, dbx, webs, mc))

        if not users:
            ui.label("계정이 없습니다.").classes("text-grey-7")
            return

        for u, mail, fs, dbx, webs, mc in data:
            with ui.card().classes("w-full"):
                _role = getattr(u, "role", "user") or "user"
                with ui.row().classes("items-center gap-2"):
                    ui.label(f"👤 {u.username}").classes("text-subtitle1").tooltip(
                        f"계정 생성: {_ts(u.created_at)}"
                    )
                    ui.badge(
                        _ROLE_LABEL.get(_role, _role),
                        color=_role_color(_role),
                    )
                ui.separator()

                _row(
                    "📧 Outlook (POP3)",
                    f"{mail.email_address} @ {mail.pop_server}:{mail.pop_port} · "
                    f"SSL {_yn(mail.use_ssl)} · 비번 {_yn(mail.encrypted_password)} · 갱신 {_ts(mail.updated_at)}"
                    if mail
                    else "미설정",
                    bool(mail),
                )

                from app.integrations.fileserver import FS_TARGETS

                for tgt, tinfo in FS_TARGETS.items():
                    s = fs.get(tgt)
                    if s:
                        detail = (
                            f"SMB · \\\\{s.host} · 아이디 {s.username or '-'} · "
                            f"비번 {_yn(s.encrypted_password)} · 갱신 {_ts(s.updated_at)}"
                        )
                    else:
                        detail = "미설정"
                    _row(f"🗄 파일서버 {tinfo['label']}", detail, bool(s))

                _dbx_refresh = bool(
                    dbx
                    and dbx.encrypted_refresh_token
                    and dbx.encrypted_app_key
                    and dbx.encrypted_app_secret
                )
                _dbx_set = _dbx_refresh or bool(dbx and dbx.encrypted_token)
                if _dbx_refresh:
                    _dbx_detail = f"refresh 방식(자동 갱신) · 갱신 {_ts(dbx.updated_at)}"
                elif dbx and dbx.encrypted_token:
                    _dbx_detail = f"레거시 토큰(만료 가능) · 갱신 {_ts(dbx.updated_at)}"
                else:
                    _dbx_detail = "미설정"
                _row("☁ Dropbox", _dbx_detail, _dbx_set)

                for target, label in (
                    ("service_portal", "🌐 Service Portal (Download)"),
                    ("release_notes", "🌐 OS/FW Release Notes"),
                    ("redmine", "🌐 Redmine (사내 이슈)"),
                    ("fota", "🌐 FOTA System (OS 등록)"),
                ):
                    w = webs.get(target)
                    if w:
                        detail = (
                            f"{w.base_url or '-'} · 아이디 {w.username or '-'} · "
                            f"비번 {_yn(w.encrypted_password)} · 갱신 {_ts(w.updated_at)}"
                        )
                    else:
                        detail = "미설정"
                    _row(label, detail, bool(w))

                if mc and mc.encrypted_api_key:
                    mc_detail = (
                        f"API키 설정됨 · 서버 {mc.server_prefix or '(자동)'} · "
                        f"발신 {mc.from_name or '-'} · 회신 {mc.reply_to or '-'} · "
                        f"갱신 {_ts(mc.updated_at)}"
                    )
                else:
                    mc_detail = "미설정"
                _row("📣 Mailchimp (발송)", mc_detail, bool(mc and mc.encrypted_api_key))


def _row(label: str, value: str, ok: bool):
    with ui.row().classes("w-full items-center gap-3 no-wrap py-1"):
        ui.label(label).classes("text-body2").style("min-width: 210px")
        ui.badge("✓" if ok else "—", color="green-8" if ok else "grey-5")
        ui.label(value).classes("text-caption text-grey-8" if ok else "text-caption text-grey-6")
