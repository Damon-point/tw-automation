"""설정 페이지: Outlook(POP3) / SFTP / Dropbox 자격증명 등록 + 연결 테스트."""
from nicegui import run, ui

from app.auth import current_user_id, is_admin, logout
from app.database import get_session
from app.integrations.creds import get_dropbox_auth
from app.integrations.dropbox_client import (
    build_authorize_url as dropbox_build_authorize_url,
    exchange_code as dropbox_exchange_code,
    test_token as dropbox_test_token,
)
from app.integrations.creds import get_mailchimp_config
from app.integrations.fileserver import FS_TARGETS, make_smb_backend
from app.mail.pop3_client import ConnectionConfig, test_connection as pop3_test
from app.models import (
    DropboxCredential,
    MailchimpCredential,
    MailCredential,
    SftpCredential,
    WebCredential,
)
from app.pages.credentials import render_credentials_content
from app.security import decrypt_secret, encrypt_secret
from app.theme import apply_theme, page_header


@ui.page("/settings")
def settings_page():
    apply_theme()
    user_id = current_user_id()

    with page_header("설정"):
        with ui.tabs().classes("w-full") as tabs:
            ui.tab("설정", icon="settings")
            ui.tab("자격증명", icon="key")
            ui.tab("로그아웃", icon="logout")
        with ui.tab_panels(tabs, value="설정").classes("w-full"):
            with ui.tab_panel("설정"):
                # 자격증명별 하위 탭 (설정/자격증명/로그아웃 상위 탭과 별개)
                with ui.tabs().props("dense inline-label active-color=primary").classes("w-full") as sub:
                    ui.tab("Outlook 메일", icon="mail")
                    ui.tab("파일 서버", icon="dns")
                    ui.tab("Dropbox", icon="cloud")
                    ui.tab("Service Portal", icon="language")
                    ui.tab("Release Notes", icon="description")
                    ui.tab("Redmine", icon="bug_report")
                    ui.tab("Solution Buffer", icon="insights")
                    ui.tab("FOTA", icon="system_update")
                    ui.tab("Mailchimp", icon="campaign")
                    # 사내 인원·템플릿 관리 — 관리자 전용
                    if is_admin():
                        ui.tab("Claude", icon="smart_toy")
                        ui.tab("사내 인원", icon="groups")
                        ui.tab("템플릿", icon="description")
                        ui.tab("메일 문구", icon="drafts")
                with ui.tab_panels(sub, value="Outlook 메일").classes("w-full"):
                    with ui.tab_panel("Outlook 메일"):
                        shared_mailbox_section()
                        _outlook_section(user_id)
                        _mail_import_section(user_id)
                    with ui.tab_panel("파일 서버"):
                        _sftp_section(user_id)
                    with ui.tab_panel("Dropbox"):
                        _dropbox_section(user_id)
                    with ui.tab_panel("Service Portal"):
                        _web_cred_simple(user_id, "service_portal")
                    with ui.tab_panel("Release Notes"):
                        _web_cred_simple(user_id, "release_notes")
                    with ui.tab_panel("Redmine"):
                        _web_cred_simple(user_id, "redmine")
                    with ui.tab_panel("Solution Buffer"):
                        _issuetrack_section()
                    with ui.tab_panel("FOTA"):
                        _web_cred_simple(user_id, "fota")
                    with ui.tab_panel("Mailchimp"):
                        _mailchimp_section(user_id)
                    if is_admin():
                        with ui.tab_panel("Claude"):
                            _claude_section()
                        with ui.tab_panel("사내 인원"):
                            from app.pages.staff_page import render_staff_content

                            render_staff_content()
                        with ui.tab_panel("템플릿"):
                            from app.pages.templates_page import (
                                render_templates_content,
                            )
                            render_templates_content()
                        with ui.tab_panel("메일 문구"):
                            from app.pages.mail_templates_page import (
                                render_mail_templates_content,
                            )
                            render_mail_templates_content()
            with ui.tab_panel("자격증명"):
                render_credentials_content()
            with ui.tab_panel("로그아웃"):
                _logout_panel()


def _shared_uid() -> int:
    """공용 자격증명(웹 편집·Mailchimp·파일서버·Dropbox)이 사는 계정 id = admin.

    읽는 쪽(creds.get_* · fileserver.get_backend)은 늘 admin 행을 본다. 그런데 저장은
    **로그인한 사용자** 행에 하고 있었다 — admin 이 아닌 사람이 저장하면 값이 들어가긴
    하는데 앱은 그 값을 영영 읽지 않는다(실측 2026-09-09: damon.park 이 저장한 Mailchimp
    API 키가 user_id=2 행에 남고, 발송은 계속 옛 키를 썼다).

    개인 메일 계정(MailCredential)은 사람마다 달라야 하므로 여기 해당하지 않는다.
    """
    from app.integrations.creds import credential_user_id

    return credential_user_id()


def _mail_import_section(user_id: int):
    """메일 파일(.eml) 직접 등록 — 드물게 쓰는 기능이라 설정 안에 접어 둔다.

    공용 수신함으로는 못 받는 메일(개인 메일함에만 온 것, 이미 정리된 오래된 것)을
    사람이 직접 넣는 통로. 등록되면 동기화로 들어온 메일과 완전히 같게 취급된다.
    """
    from app.mail.import_mail import import_file

    with ui.card().classes("w-full"):
        with ui.expansion("메일 파일로 직접 등록 (.eml)", icon="upload_file").classes(
            "w-full"
        ).props("dense"):
            ui.label(
                "공용 수신함으로 못 받는 메일을 직접 넣을 때만 씁니다 — 개인 메일함에만 온 메일, "
                "이미 자동 정리된 오래된 메일 등. 평소에는 메일함의 '동기화'로 충분합니다."
            ).classes("text-caption text-grey-7")
            ui.label(
                "올린 메일은 동기화로 받은 메일과 똑같이 저장됩니다. 작업 트리거"
                "([FIH][Submission]/[Approval]) 메일이면 메일함 작업 목록에 바로 나타납니다."
            ).classes("text-caption text-grey-7")
            ui.label(
                "※ Outlook 클래식은 '파일 → 다른 이름으로 저장'에서 형식을 텍스트(.eml)로, "
                "웹메일(OWA)은 '다운로드'를 누르면 .eml 로 받아집니다."
            ).classes("text-caption text-grey-6")

            result = ui.column().classes("w-full gap-1")
            added = {"n": 0}

            async def on_upload(e):
                data = await e.file.read()
                res = await run.io_bound(import_file, data, e.file.name, user_id)
                icon, color = {
                    "added": ("check_circle", "text-green-8"),
                    "updated": ("autorenew", "text-teal-7"),
                    "duplicate": ("info", "text-grey-7"),
                    "error": ("error", "text-red-6"),
                }.get(res["status"], ("info", "text-grey-7"))
                with result:
                    with ui.row().classes("items-center gap-2 no-wrap w-full"):
                        ui.icon(icon, size="xs").classes(color)
                        ui.label(
                            f"{e.file.name} · {res.get('subject') or '(제목 없음)'}"
                        ).classes("text-caption").style("flex:1; word-break:break-all")
                        ui.label(res["message"]).classes(f"text-caption {color}")
                if res["status"] == "added":
                    added["n"] += 1
                    ui.notify(f"메일을 등록했습니다 · 총 {added['n']}통", color="positive")

            ui.upload(on_upload=on_upload, auto_upload=True, multiple=True,
                      label="메일 파일 선택 (.eml · 여러 개 가능)").props(
                "accept=.eml,.msg,.txt flat bordered"
            ).classes("w-full")


def _logout_panel():
    with ui.card().classes("w-full"):
        ui.label("로그아웃").classes("text-h6")
        ui.label("현재 계정에서 로그아웃합니다.").classes("text-caption text-grey-7")

        def _do_logout():
            logout()
            ui.navigate.to("/login")

        ui.button("로그아웃", icon="logout", on_click=_do_logout).props("color=negative")


# 고정값(바뀌지 않는 접속 정보) — 사용자는 아이디/비밀번호만 입력한다.
# PointMobile 사내 메일서버(POP3/SMTP 겸용). O365 아님. env 로 override 가능.
import os as _os

_MAIL_SERVER = _os.getenv("MAIL_SERVER", "mail.pointmobile.com")
_MAIL_PORT = int(_os.getenv("MAIL_POP_PORT", "995") or "995")

# 웹 로그인 대상별 고정 설정(주소·셀렉터). 사용자 입력은 아이디/비밀번호뿐.
_WEB_FIXED: dict[str, dict] = {
    "service_portal": {
        "label": "Service Portal", "base_url": "https://serviceportal.pointmobile.com/",
        "login_url": "https://serviceportal.pointmobile.com/#/main/login",
        "user_sel": "#os_username", "pw_sel": "#os_password", "submit_sel": "#loginButton",
        "edit_tpl": "{base}/pages/editpage.action?pageId={page_id}",
        "iframe_sel": "#wysiwygTextarea_ifr", "save_sel": "#rte-button-publish",
    },
    "release_notes": {
        "label": "OS/FW Release Notes (Confluence)",
        "base_url": "http://post.solution-pointmobile.co.kr:8040", "login_url": "",
        "user_sel": "#os_username", "pw_sel": "#os_password", "submit_sel": "#loginButton",
        "edit_tpl": "{base}/pages/editpage.action?pageId={page_id}",
        "iframe_sel": "#wysiwygTextarea_ifr", "save_sel": "#rte-button-publish",
    },
    "redmine": {
        "label": "Redmine (사내 이슈)", "base_url": "https://issue.pointmobile.co.kr",
        "login_url": "https://issue.pointmobile.co.kr/login",
        "user_sel": "#username", "pw_sel": "#password", "submit_sel": "#login-submit",
        "edit_tpl": "", "iframe_sel": "", "save_sel": "",
    },
    "fota": {
        "label": "FOTA System (OS 등록)", "base_url": "https://fota.solution-pointmobile.co.kr",
        "login_url": "https://fota.solution-pointmobile.co.kr/login",
        "user_sel": "input[name='username']", "pw_sel": "input[name='password']",
        "submit_sel": "button[type='submit']", "edit_tpl": "", "iframe_sel": "", "save_sel": "",
    },
}


# ── Outlook (POP3) — 메일 주소 + 앱 비밀번호만 ─────────────────────────
def shared_mailbox_section():
    """공용 수신 메일 계정 — 메일 동기화는 이 계정 하나로만 한다(관리자 전용)."""
    from app.auth import current_username, is_admin
    from app.models import SharedMailbox

    with get_session() as db:
        box = db.query(SharedMailbox).filter_by(id=1).first()

    with ui.card().classes("w-full").style("border-left:4px solid #4A7C67"):
        ui.label("메일 수신 계정 (공용)").classes("text-h6")
        ui.label(
            "메일 수신은 이 공용 계정 하나로만 합니다."
        ).classes("text-caption text-grey-7")
        if box and box.email_address:
            ui.label(
                f"현재: {box.email_address} · 수정자 {box.updated_by or '-'}"
            ).classes("text-caption text-grey-8")
        else:
            ui.label("아직 등록되지 않았습니다 — 등록 전에는 동기화할 수 없습니다.").classes(
                "text-caption text-red-6")

        if not is_admin():
            ui.label("관리자만 등록·수정할 수 있습니다.").classes("text-caption text-grey-6")
            return

        email_in = ui.input("메일 주소", value=(box.email_address if box else "")).classes("w-full")
        pw_in = ui.input(
            "앱 비밀번호", password=True, password_toggle_button=True,
            placeholder="변경할 때만 입력 (비우면 기존 값 유지)",
        ).classes("w-full")

        def _cfg(require_pw: bool):
            password = pw_in.value
            if not password:
                if box and box.encrypted_password:
                    password = decrypt_secret(box.encrypted_password)
                elif require_pw:
                    ui.notify("앱 비밀번호를 입력하세요.", color="negative")
                    return None
            if not email_in.value:
                ui.notify("메일 주소를 입력하세요.", color="negative")
                return None
            return ConnectionConfig(
                email_address=email_in.value, password=password or "",
                server=_MAIL_SERVER, port=_MAIL_PORT, use_ssl=True,
            )

        async def on_test():
            cfg = _cfg(require_pw=True)
            if not cfg:
                return
            n = ui.notification("접속 테스트 중...", spinner=True, timeout=None)
            ok, msg = await run.io_bound(pop3_test, cfg)
            n.dismiss()
            ui.notify(msg, color="positive" if ok else "negative")

        def on_save():
            if not email_in.value:
                ui.notify("메일 주소를 입력하세요.", color="negative")
                return
            with get_session() as db:
                row = db.query(SharedMailbox).filter_by(id=1).first()
                if row is None:
                    if not pw_in.value:
                        ui.notify("최초 등록 시 앱 비밀번호가 필요합니다.", color="negative")
                        return
                    row = SharedMailbox(id=1)
                    db.add(row)
                row.email_address = email_in.value
                row.pop_server = _MAIL_SERVER
                row.pop_port = _MAIL_PORT
                row.use_ssl = True
                row.updated_by = current_username() or ""
                if pw_in.value:
                    row.encrypted_password = encrypt_secret(pw_in.value)
                db.commit()
            pw_in.value = ""
            ui.notify("공용 수신 계정을 저장했습니다.", color="positive")

        with ui.row().classes("gap-2"):
            ui.button("연결 테스트", on_click=on_test).props("outline")
            ui.button("저장", on_click=on_save)


def _outlook_section(user_id: int):
    """발송 계정 설정.

    공용 발송 계정(TW_MAIL_ACCOUNT)을 쓰는 지금은 **그 계정 주인만** 여기를 손대면
    된다. 나머지 사람은 등록할 것이 없다 — 아이디가 곧 회사 메일 주소이고, 없으면
    사내 인원 명부에서 찾는다. 그래서 다른 사람에게는 찾아낸 주소만 보여 준다
    (예전에는 모두에게 입력란을 띄워, 등록해도 아무 효과가 없는 일을 시켰다).
    """
    from app.user_mailer import sender_profile, system_sender_user_id

    with get_session() as db:
        cred = db.query(MailCredential).filter_by(user_id=user_id).first()

    _relay = _os.getenv("TW_MAIL_ACCOUNT", "").strip()
    if _relay and system_sender_user_id() != user_id:
        addr, _name, _sig = sender_profile(user_id)
        with ui.card().classes("w-full"):
            ui.label("내 발송 주소").classes("text-h6")
            ui.label(
                (f"보내는 주소: {addr}" if addr else "보낼 주소를 찾지 못했습니다.")
                + "  —  등록하실 것은 없습니다."
            ).classes("text-body2")
            ui.label(
                "앱이 보내는 메일은 공용 계정으로 나가고, 보내는 주소만 본인 것으로 "
                "찍힙니다. 주소는 로그인 아이디에서, 없으면 사내 인원 명부에서 찾습니다. "
                + ("" if addr else
                   "주소를 못 찾으면 시스템 명의로 나갑니다 — [인원 명부]에 주소를 "
                   "추가하거나 관리자에게 문의하세요.")
            ).classes("text-caption text-grey-7")
        return

    with ui.card().classes("w-full"):
        ui.label("공용 발송 계정 (SMTP)" if _relay else "내 발송 계정 (SMTP) — 발송 전용"
                 ).classes("text-h6")
        ui.label(
            f"서버 {_MAIL_SERVER}:{_MAIL_PORT} (SSL) 는 고정입니다. "
            + ("**앱이 보내는 모든 메일이 이 계정으로 로그인합니다.** 비밀번호가 바뀌면 "
               "여기서 고쳐 주세요 — 안 고치면 앱 메일이 전부 나가지 않습니다. "
               if _relay else "메일 주소와 앱 비밀번호를 입력하세요. ")
            + "**메일 동기화(수신)에는 사용되지 않습니다.**"
        ).classes("text-caption text-grey-7")
        email_in = ui.input("메일 주소", value=cred.email_address if cred else "").classes("w-full")
        pw_in = ui.input(
            "앱 비밀번호", password=True, password_toggle_button=True,
            placeholder="변경할 때만 입력 (비우면 기존 값 유지)",
        ).classes("w-full")

        def build_cfg(require_pw: bool):
            password = pw_in.value
            if not password:
                if cred and cred.encrypted_password:
                    password = decrypt_secret(cred.encrypted_password)
                elif require_pw:
                    ui.notify("앱 비밀번호를 입력하세요.", color="negative")
                    return None
            if not email_in.value:
                ui.notify("메일 주소를 입력하세요.", color="negative")
                return None
            return ConnectionConfig(
                email_address=email_in.value, password=password or "",
                server=_MAIL_SERVER, port=_MAIL_PORT, use_ssl=True,
            )

        async def on_test():
            cfg = build_cfg(require_pw=True)
            if not cfg:
                return
            n = ui.notification("접속 테스트 중...", spinner=True, timeout=None)
            ok, msg = await run.io_bound(pop3_test, cfg)
            n.dismiss()
            ui.notify(msg, color="positive" if ok else "negative")

        def on_save():
            if not email_in.value:
                ui.notify("메일 주소를 입력하세요.", color="negative")
                return
            with get_session() as db:
                row = db.query(MailCredential).filter_by(user_id=user_id).first()
                if row is None:
                    # 여기까지 오는 사람은 실제로 로그인할 계정을 등록하는 것이라
                    # 앱 비밀번호가 반드시 있어야 한다.
                    if not pw_in.value:
                        ui.notify("최초 등록 시 앱 비밀번호가 필요합니다.", color="negative")
                        return
                    row = MailCredential(user_id=user_id, encrypted_password="")
                    db.add(row)
                row.email_address = email_in.value
                row.pop_server = _MAIL_SERVER
                row.pop_port = _MAIL_PORT
                row.use_ssl = True
                if pw_in.value:
                    row.encrypted_password = encrypt_secret(pw_in.value)
                db.commit()
            pw_in.value = ""
            ui.notify("Outlook 설정을 저장했습니다.", color="positive")

        with ui.row().classes("gap-2"):
            ui.button("연결 테스트", on_click=on_test).props("outline")
            ui.button("저장", on_click=on_save)


# ── 웹 로그인 계정 — 아이디 + 비밀번호만 (주소·셀렉터는 고정) ──────────
# ── Solution Buffer(Redmine salesissue 감시 → SB 문서화) 동작 설정 ─────────
def _claude_section():
    """Claude 를 **어디로 부를지** 고른다. 고른 값은 DB 에 남고, 다음 호출부터 듣는다.

    구독(이 PC 의 Claude Code)은 API 요금이 들지 않는다. 대신 이 PC 에서만 되고,
    연장(tools)을 쓰는 호출은 그 길로 못 간다. API 열쇠는 그 반대다 — 어디서나 되고
    무엇이든 되지만 쓴 만큼 요금이 붙는다. 그래서 둘 다 두고 고르게 한다
    (사용자 지시 2026-09-22 "뭔가 api로 돌아갈 수도 있겠다는 생각이 드네요").
    """
    from app import app_settings
    from app.integrations import claude_cli as CC

    st = CC.status()

    with ui.card().classes("w-full"):
        ui.label("Claude 호출 방식").classes("text-h6")
        ui.label("바꾸면 **다음 호출부터** 바로 적용됩니다. 서버를 다시 띄우지 "
                 "않아도 됩니다.").classes("text-caption text-grey-7")

        pick = ui.radio(
            {CC.SUBSCRIPTION: "구독 (이 PC 의 Claude Code) — API 요금 없음",
             CC.API: "API 열쇠 (api.anthropic.com) — 쓴 만큼 요금"},
            value=st["backend"],
        ).props("dense")

        # 지금 양쪽이 쓸 수 있는 상태인지 그대로 보여 준다.
        with ui.column().classes("gap-0 q-mt-sm"):
            ui.label(("✔ Claude Code: " + st["claude"]) if st["claude"]
                     else "✘ Claude Code: claude 명령을 찾지 못했습니다 "
                          "(설치·로그인 필요)").classes("text-caption")
            ui.label("✔ API 열쇠: .env 에 등록됨" if st["has_key"]
                     else "✘ API 열쇠: .env 에 ANTHROPIC_API_KEY 가 없습니다") \
                .classes("text-caption")

        note = ui.label("").classes("text-caption").style("white-space:normal")

        def _save() -> None:
            app_settings.set("claude_backend", pick.value,
                             by=str(current_user_id() or ""))
            now = CC.status()
            ui.notify("Claude 호출 방식을 "
                      + ("구독" if now["backend"] == CC.SUBSCRIPTION else "API 열쇠")
                      + " 로 바꿨습니다.", color="positive")
            _warn(now)

        def _warn(now: dict) -> None:
            if now["backend"] == CC.SUBSCRIPTION and not now["claude"]:
                note.set_text("claude 명령이 없어 지금은 Claude 를 쓰는 단계가 "
                              "실패합니다.")
            elif now["backend"] == CC.API and not now["has_key"]:
                note.set_text("열쇠가 없어 지금은 Claude 를 쓰는 단계가 실패합니다.")
            else:
                note.set_text("")

        _warn(st)

        async def _try() -> None:
            """정말 불러 본다 — 고른 길로 한 번 왕복한다."""
            import time

            import anthropic

            n = ui.notification("Claude 를 불러 보는 중…", spinner=True, timeout=None)

            def _go():
                t = time.time()
                r = anthropic.Anthropic().messages.create(
                    model="claude-haiku-4-5", max_tokens=32,
                    messages=[{"role": "user", "content": "1+1 은? 숫자만 답하세요."}])
                said = "".join(getattr(b, "text", "") for b in r.content).strip()
                return time.time() - t, said, r.usage

            try:
                took, said, use = await run.io_bound(_go)
            except Exception as e:  # noqa: BLE001
                n.dismiss()
                ui.notify(f"실패: {e}", color="negative", multi_line=True)
                return
            n.dismiss()
            ui.notify(f"됐습니다 — {took:.1f}초 · 답 {said[:20]!r} · 토큰 "
                      f"{getattr(use, 'input_tokens', 0)}/"
                      f"{getattr(use, 'output_tokens', 0)}",
                      color="positive", multi_line=True)

        with ui.row().classes("gap-2 q-mt-sm"):
            ui.button("저장", icon="save", on_click=_save).props("size=sm")
            ui.button("지금 불러보기", icon="play_arrow", on_click=_try) \
                .props("size=sm outline")

        ui.separator()
        ui.label("연장(tools)을 쓰는 단계는 구독으로 갈 수 없습니다 — 그때는 API 로 "
                 "바꿔 실행하세요. 지금 도는 단계 중에는 해당하는 것이 없습니다.") \
            .classes("text-caption text-grey-7").style("white-space:normal")


def _issuetrack_section():
    """자동으로 도는 동작을 켜고 끈다. 값은 issuetrack.db 에 저장돼 재시작해도 남는다.

    **자주 바꿀 것만 둔다.** 초안 자동 생성(켬)·틱당 처리 건수(3)·소급 기준일은
    한 번 정하면 손댈 일이 없어 화면에서 뺐다. 값 자체는 그대로 동작하며 필요하면
    env(TW_SB_AUTO_GENERATE / TW_SB_PER_TICK / TW_SB_CUTOFF)로 덮어쓸 수 있다.
    """
    from app.issuetrack import settings as it_settings

    cfg = it_settings.load()

    with ui.card().classes("w-full"):
        ui.label("Solution Buffer 동작").classes("text-h6")
        ui.label(
            "Redmine salesissue 를 5분마다 수집하고, Resolved 된 이슈는 "
            "Solution Buffer 업로드용 문서 초안으로 만듭니다. "
            "Confluence 업로드는 이 설정과 무관하게 항상 사람이 [업로드]를 눌러야 합니다."
        ).classes("text-caption text-grey-7")

        def _save(key: str, value, msg: str) -> None:
            it_settings.set_(key, value)
            ui.notify(msg, color="positive")

        ui.switch(
            "담당자에게 검토 요청 메일 자동 발송",
            value=cfg.sb_auto_notify,
            on_change=lambda e: _save(
                "sb_auto_notify", bool(e.value),
                f"자동 발송을 {'켰습니다' if e.value else '껐습니다'}."),
        ).tooltip("이슈가 Resolved 되어 초안이 만들어지면 그 자리에서 Redmine 담당자에게 "
                  "메일이 나갑니다. 담당자는 로그인해 본인 문서를 고치거나 올립니다. "
                  "꺼 두면 [Solution Buffer] 화면에서 사람이 눌러 보냅니다. "
                  "소급분은 켜도 자동 발송되지 않습니다.")

        # 메일은 '로그인해서 확인하세요'라고 안내한다. 계정이 없으면 들어올 수가 없으니
        # 켜기 전에 여기서 보이게 한다.
        from app.issuetrack import sb as _sb

        missing = _sb.assignees_without_account()
        if missing:
            who = ", ".join(f"{n}({c}건)" for n, c in missing[:6])
            more = f" 외 {len(missing) - 6}명" if len(missing) > 6 else ""
            ui.label(
                f"⚠ 담당 문서가 있는데 앱 계정이 없는 사람: {who}{more}. "
                "메일은 받아도 로그인해서 확인할 수 없습니다 — "
                "[계정 관리]에서 계정을 만들어 주세요."
            ).classes("text-caption text-warning")

        ui.input(
            "SB 대상 담당자", value=cfg.sb_assignees,
            on_change=lambda e: _save(
                "sb_assignees", (e.value or "").strip(), "저장했습니다."),
        ).props("dense outlined").classes("w-full") \
            .tooltip("Solution Buffer 는 TA 팀 전용입니다. 여기 적힌 Redmine 담당자의 "
                     "이슈만 문서를 만들고 메일을 보냅니다(쉼표 구분). "
                     "비우면 담당자를 가리지 않습니다. 대상에서 빠진 문서는 지워지지 "
                     "않고 'SB 대상 아님'으로 남으며, 다시 넣으면 되살아납니다.")

        ui.input(
            "감시 제외 키워드", value=cfg.exclude_keywords,
            on_change=lambda e: _save(
                "exclude_keywords", (e.value or "").strip(), "저장했습니다."),
        ).props("dense outlined").classes("w-full") \
            .tooltip("제목에 이 단어가 들어간 이슈는 감시 대상에서 뺍니다(쉼표 구분). "
                     "삭제가 아니라 표시만 하므로, 규칙을 바꾸면 다음 수집 때 되살아납니다.")

        ui.label(
            "설정을 바꾸면 다음 수집 주기(최대 5분)부터 적용됩니다. "
            "서버를 다시 시작할 필요는 없습니다."
        ).classes("text-caption text-grey-6")


def _web_cred_simple(user_id: int, target: str):
    cfg = _WEB_FIXED[target]
    with get_session() as db:
        c = db.query(WebCredential).filter_by(user_id=_shared_uid(), target=target).first()

    with ui.card().classes("w-full"):
        ui.label(f"{cfg['label']} 로그인").classes("text-h6")
        ui.label(
            f"주소: {cfg['base_url']} (고정). 아이디와 비밀번호만 입력하면 됩니다. "
            "비밀번호는 암호화 저장됩니다."
        ).classes("text-caption text-grey-7")
        user_in = ui.input("아이디", value=c.username if c else "").classes("w-full")
        pw_in = ui.input(
            "비밀번호", password=True, password_toggle_button=True,
            placeholder="변경할 때만 입력 (비우면 기존 값 유지)",
        ).classes("w-full")

        def on_save():
            if not user_in.value.strip():
                ui.notify("아이디를 입력하세요.", color="negative")
                return
            with get_session() as db:
                row = db.query(WebCredential).filter_by(user_id=_shared_uid(), target=target).first()
                if row is None:
                    if not pw_in.value:
                        ui.notify("최초 등록 시 비밀번호가 필요합니다.", color="negative")
                        return
                    row = WebCredential(user_id=_shared_uid(), target=target)
                    db.add(row)
                row.base_url = cfg["base_url"]
                row.login_url = cfg["login_url"]
                row.username = user_in.value.strip()
                row.username_sel = cfg["user_sel"]
                row.password_sel = cfg["pw_sel"]
                row.submit_sel = cfg["submit_sel"]
                row.edit_url_template = cfg["edit_tpl"]
                row.editor_iframe_sel = cfg["iframe_sel"]
                row.save_button_sel = cfg["save_sel"]
                if pw_in.value:
                    row.encrypted_password = encrypt_secret(pw_in.value)
                db.commit()
            pw_in.value = ""
            ui.notify(f"{cfg['label']} 계정을 저장했습니다.", color="positive")

        ui.button("저장", on_click=on_save)


# ── Mailchimp (⑦ Newsletter 발송) ────────────────────────────────────
def _mailchimp_section(user_id: int):
    with get_session() as db:
        c = db.query(MailchimpCredential).filter_by(user_id=_shared_uid()).first()

    with ui.card().classes("w-full"):
        ui.label("Mailchimp (Newsletter 발송)").classes("text-h6")
        ui.label(
            "Newsletter 발송에 씁니다. API 키는 암호화 저장됩니다. API 키 형식은 "
            "'...-us21' 처럼 끝에 서버 프리픽스가 붙어 있어, 프리픽스는 비워두면 자동 인식합니다. "
            "보낼 대상(오디언스·태그)은 여기서 정하지 않습니다 — 발송할 때 "
            "[Newsletter 검수] 화면에서 그룹(태그)을 골라 보냅니다."
        ).classes("text-caption text-grey-7")

        key_in = ui.input(
            "API 키", password=True, password_toggle_button=True,
            placeholder="변경할 때만 입력 (비우면 기존 값 유지)",
        ).classes("w-full")
        prefix_in = ui.input(
            "서버 프리픽스(선택)", value=(c.server_prefix if c else ""),
            placeholder="us21 (비우면 API 키에서 자동)",
        ).classes("w-64")
        from_in = ui.input(
            "발신자 표시명(From name)", value=(c.from_name if c else ""),
            placeholder="표시 이름 (이메일 아님! 예: Point Mobile). @ 등 특수문자 불가",
        ).classes("w-full")
        reply_in = ui.input(
            "회신 주소(Reply-to)", value=(c.reply_to if c else ""),
            placeholder="이메일 주소 (예: pm_ta@pointmobile.com)",
        ).classes("w-full")
        subj_in = ui.input(
            "기본 제목", value=(c.subject_default if c else ""),
            placeholder="예: [PointMobile] OS Update Newsletter",
        ).classes("w-full")

        def on_save():
            with get_session() as db:
                row = db.query(MailchimpCredential).filter_by(user_id=_shared_uid()).first()
                if row is None:
                    if not key_in.value:
                        ui.notify("최초 등록 시 API 키가 필요합니다.", color="negative")
                        return
                    row = MailchimpCredential(user_id=_shared_uid())
                    db.add(row)
                if key_in.value:
                    row.encrypted_api_key = encrypt_secret(key_in.value.strip())
                row.server_prefix = (prefix_in.value or "").strip()
                row.from_name = (from_in.value or "").strip()
                row.reply_to = (reply_in.value or "").strip()
                row.subject_default = (subj_in.value or "").strip()
                db.commit()
            key_in.value = ""
            ui.notify("Mailchimp 설정을 저장했습니다.", color="positive")

        async def on_test():
            from app.integrations import mailchimp as MC

            cfg = get_mailchimp_config(user_id)
            if cfg is None or not cfg.usable:
                ui.notify("먼저 API 키를 저장하세요.", color="negative")
                return
            n = ui.notification("Mailchimp 연결 테스트 중...", spinner=True, timeout=None)
            ok, msg = await run.io_bound(MC.ping, cfg)
            n.dismiss()
            ui.notify(msg, color="positive" if ok else "negative")

        with ui.row().classes("gap-2 mt-2"):
            ui.button("저장", on_click=on_save)
            ui.button("연결 테스트", on_click=on_test).props("outline")


# ── 파일 서버 (SMB) — 서버별(.104/.160) 계정 등록 ─────────────────────
def _sftp_section(user_id: int):
    ui.label(
        "내부 파일서버는 모두 SMB(Windows 공유)입니다. 서버(.104/.160/.26)마다 계정이 다르니 "
        "각각 아이디/비밀번호를 등록하세요. 호스트·포트는 고정이라 입력할 필요 없습니다. "
        "(.26 은 NightlyBuild 서버 — DQA FOTA 테스트의 NB 빌드 다운로드에 사용)"
    ).classes("text-caption text-grey-7")
    for target in ("fs104", "fs160", "fs26"):
        _smb_cred(user_id, target)


def _smb_cred(user_id: int, target: str):
    info = FS_TARGETS[target]
    with get_session() as db:
        c = db.query(SftpCredential).filter_by(user_id=_shared_uid(), target=target).first()

    with ui.card().classes("w-full"):
        ui.label(f"{info['label']} · {info['host']}").classes("text-h6")
        ui.label("SMB(포트 445) · 아이디와 비밀번호만 입력하면 됩니다. 비밀번호는 암호화 저장됩니다.").classes(
            "text-caption text-grey-7"
        )
        user_in = ui.input("아이디", value=(c.username if c else "")).classes("w-full")
        pw_in = ui.input(
            "비밀번호", password=True, password_toggle_button=True,
            placeholder="변경할 때만 입력 (비우면 기존 값 유지)",
        ).classes("w-full")

        def stored_pw() -> str:
            return decrypt_secret(c.encrypted_password) if (c and c.encrypted_password) else ""

        async def on_test():
            if not user_in.value.strip():
                ui.notify("아이디를 입력하세요.", color="negative")
                return
            pw = pw_in.value or stored_pw()
            if not pw:
                ui.notify("비밀번호를 입력하세요.", color="negative")
                return
            b = make_smb_backend(target, user_in.value.strip(), pw)
            n = ui.notification("SMB 인증 테스트 중...", spinner=True, timeout=None)
            ok, msg = await run.io_bound(b.test)
            n.dismiss()
            ui.notify(msg, color="positive" if ok else "negative")

        def on_save():
            if not user_in.value.strip():
                ui.notify("아이디를 입력하세요.", color="negative")
                return
            with get_session() as db:
                row = db.query(SftpCredential).filter_by(user_id=_shared_uid(), target=target).first()
                if row is None:
                    if not pw_in.value:
                        ui.notify("최초 등록 시 비밀번호가 필요합니다.", color="negative")
                        return
                    row = SftpCredential(user_id=_shared_uid(), target=target)
                    db.add(row)
                row.protocol = "smb"
                row.host = info["host"]
                row.port = 445
                row.username = user_in.value.strip()
                row.base_path = ""
                if pw_in.value:
                    row.encrypted_password = encrypt_secret(pw_in.value)
                db.commit()
            pw_in.value = ""
            ui.notify(f"{info['label']} 계정을 저장했습니다.", color="positive")

        with ui.row().classes("gap-2"):
            ui.button("연결 테스트", on_click=on_test).props("outline")
            ui.button("저장", on_click=on_save)


# ── Dropbox ───────────────────────────────────────────────────────────
def _dropbox_section(user_id: int):
    with get_session() as db:
        cred = db.query(DropboxCredential).filter_by(user_id=_shared_uid()).first()
    has_refresh = bool(
        cred and cred.encrypted_refresh_token and cred.encrypted_app_key and cred.encrypted_app_secret
    )
    has_legacy = bool(cred and cred.encrypted_token)

    def _status_text() -> str:
        if has_refresh:
            return "현재 상태: refresh 방식 등록됨 ✓ (access token 자동 갱신 — 만료 걱정 없음)"
        if has_legacy:
            return "현재 상태: 레거시 access token 만 등록됨 (몇 시간 뒤 만료) — 아래에서 refresh 방식 등록 권장"
        return "현재 상태: 미등록"

    with ui.card().classes("w-full"):
        ui.label("Dropbox").classes("text-h6")
        ui.label(
            "refresh token 방식으로 등록하면 access token 이 자동 갱신되어 다시 만료되지 않습니다. "
            "Dropbox App Console(developers.dropbox.com/apps → 해당 앱 → Settings)에서 "
            "App key / App secret 을 복사해 아래에 입력하세요. 값은 모두 암호화 저장됩니다."
        ).classes("text-caption text-grey-7")
        ui.label(_status_text()).classes("text-caption")

        key_in = ui.input("App key").classes("w-full")
        secret_in = ui.input(
            "App secret", password=True, password_toggle_button=True
        ).classes("w-full")

        def _resolve_keys(require: bool = True):
            ak = (key_in.value or "").strip()
            asec = (secret_in.value or "").strip()
            if not ak and cred and cred.encrypted_app_key:
                ak = decrypt_secret(cred.encrypted_app_key)
            if not asec and cred and cred.encrypted_app_secret:
                asec = decrypt_secret(cred.encrypted_app_secret)
            if require and (not ak or not asec):
                ui.notify("App key 와 App secret 을 입력하세요.", color="negative")
                return None, None
            return ak, asec

        def _save_keys(ak: str, asec: str, refresh_token: str | None = None):
            with get_session() as db:
                row = db.query(DropboxCredential).filter_by(user_id=_shared_uid()).first()
                if row is None:
                    row = DropboxCredential(user_id=_shared_uid())
                    db.add(row)
                row.encrypted_app_key = encrypt_secret(ak)
                row.encrypted_app_secret = encrypt_secret(asec)
                if refresh_token:
                    row.encrypted_refresh_token = encrypt_secret(refresh_token)
                db.commit()

        def on_gen_url():
            ak, asec = _resolve_keys()
            if not ak:
                return
            try:
                url = dropbox_build_authorize_url(ak, asec)
            except Exception as e:  # noqa: BLE001
                ui.notify(f"인증 URL 생성 실패: {e}", color="negative")
                return
            _save_keys(ak, asec)  # 코드 교환 단계에서 재사용하도록 저장
            auth_url_box.clear()
            with auth_url_box:
                ui.label(
                    "① 아래 링크를 열어 Dropbox 로그인 후 [허용] → 화면에 표시되는 코드를 복사하세요."
                ).classes("text-caption text-primary")
                ui.link("Dropbox 인증 페이지 열기", url, new_tab=True).classes("text-primary")
            ui.notify("인증 URL 을 만들었습니다. 링크에서 코드를 받아 아래에 붙여넣으세요.", color="positive")

        # 인증 URL 생성 버튼 → 그 아래 링크 표시 영역
        ui.button("① 인증 URL 생성", icon="link", on_click=on_gen_url).props("outline")
        auth_url_box = ui.column().classes("w-full gap-1")

        code_in = ui.input(
            "② 인증 코드", placeholder="위 링크에서 받은 코드 붙여넣기"
        ).classes("w-full")

        async def on_finish():
            ak, asec = _resolve_keys()
            if not ak:
                return
            code = (code_in.value or "").strip()
            if not code:
                ui.notify("인증 코드를 입력하세요.", color="negative")
                return
            n = ui.notification("코드 교환 중...", spinner=True, timeout=None)
            ok, res = await run.io_bound(dropbox_exchange_code, ak, asec, code)
            n.dismiss()
            if not ok:
                ui.notify(f"인증 실패: {res}", color="negative", multi_line=True)
                return
            _save_keys(ak, asec, refresh_token=res)
            code_in.value = ""
            ui.notify("Dropbox refresh 방식 등록 완료! 이제 자동 갱신됩니다.", color="positive")
            ui.navigate.reload()

        async def on_test():
            auth = get_dropbox_auth(user_id)
            if auth is None:
                ui.notify(
                    "등록된 Dropbox 자격증명이 없습니다. 먼저 인증을 완료하세요.", color="negative"
                )
                return
            n = ui.notification("Dropbox 연결 테스트 중...", spinner=True, timeout=None)
            ok, msg = await run.io_bound(dropbox_test_token, auth)
            n.dismiss()
            ui.notify(msg, color="positive" if ok else "negative")

        with ui.row().classes("gap-2 mt-2"):
            ui.button("② 인증 완료 & 저장", icon="check", on_click=on_finish)
            ui.button("연결 테스트", on_click=on_test).props("outline")
