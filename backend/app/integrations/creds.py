"""사용자별 SFTP/Dropbox 자격증명을 복호화해 사용 가능한 형태로 반환."""
from __future__ import annotations

from dataclasses import dataclass

from app.database import get_session
from app.models import (
    DropboxCredential,
    MailchimpCredential,
    WebCredential,
)
from app.security import decrypt_secret

# 공용 자격증명(FOTA·Service Portal·Confluence·Dropbox·Mailchimp·파일서버)은
# **admin 계정 한 곳**에 등록해 팀 전체가 공유한다. 예외는 개인 메일 발송 계정뿐
# (그건 user_mailer 가 각 사용자 계정으로 보낸다 — 여기 getter 들은 그 계정을 다루지 않는다).
_ADMIN_CRED_UID: int | None = None


def credential_user_id() -> int:
    """공용 자격증명을 소유한 계정 id = admin. (username 'admin' 우선, 없으면 최소 id admin.)"""
    global _ADMIN_CRED_UID
    if _ADMIN_CRED_UID is not None:
        return _ADMIN_CRED_UID
    from app.models import User

    with get_session() as db:
        u = (db.query(User).filter_by(username="admin").first()
             or db.query(User).filter_by(role="admin").order_by(User.id).first())
        _ADMIN_CRED_UID = u.id if u else 1
    return _ADMIN_CRED_UID


@dataclass
class WebConfig:
    target: str
    base_url: str
    login_url: str
    username: str
    password: str
    edit_url_template: str
    editor_iframe_sel: str
    save_button_sel: str
    username_sel: str
    password_sel: str
    submit_sel: str


def get_web_config(user_id: int, target: str) -> "WebConfig | None":
    user_id = credential_user_id()   # 공용: admin 계정 자격증명 사용
    with get_session() as db:
        c = db.query(WebCredential).filter_by(user_id=user_id, target=target).first()
        if c is None or not c.base_url:
            return None
        return WebConfig(
            target=c.target,
            base_url=c.base_url,
            login_url=c.login_url or c.base_url,
            username=c.username,
            password=decrypt_secret(c.encrypted_password) if c.encrypted_password else "",
            edit_url_template=c.edit_url_template,
            editor_iframe_sel=c.editor_iframe_sel,
            save_button_sel=c.save_button_sel,
            username_sel=c.username_sel,
            password_sel=c.password_sel,
            submit_sel=c.submit_sel,
        )


@dataclass
class DropboxAuth:
    """Dropbox 접속 자격증명. refresh 3종이 있으면 자동 갱신, 없으면 레거시 access token."""

    refresh_token: str = ""
    app_key: str = ""
    app_secret: str = ""
    access_token: str = ""

    @property
    def uses_refresh(self) -> bool:
        return bool(self.refresh_token and self.app_key and self.app_secret)

    @property
    def usable(self) -> bool:
        return self.uses_refresh or bool(self.access_token)


@dataclass
class MailchimpConfig:
    api_key: str
    server_prefix: str
    list_id: str
    tag: str
    from_name: str
    reply_to: str
    subject_default: str

    @property
    def usable(self) -> bool:
        return bool(self.api_key and self.server_prefix)


def get_mailchimp_config(user_id: int) -> "MailchimpConfig | None":
    """Mailchimp 자격증명/설정을 복호화해 반환. API 키가 없으면 None.

    server_prefix 가 비어있으면 API 키 접미(...-usXX)에서 자동 추출한다.
    """
    user_id = credential_user_id()   # 공용: admin 계정 자격증명 사용
    with get_session() as db:
        c = db.query(MailchimpCredential).filter_by(user_id=user_id).first()
        if c is None or not c.encrypted_api_key:
            return None
        key = decrypt_secret(c.encrypted_api_key)
        prefix = (c.server_prefix or "").strip()
        if not prefix and "-" in key:
            prefix = key.rsplit("-", 1)[-1].strip()
        return MailchimpConfig(
            api_key=key, server_prefix=prefix, list_id=c.list_id or "",
            tag=(getattr(c, "tag", "") or ""),
            from_name=c.from_name or "", reply_to=c.reply_to or "",
            subject_default=c.subject_default or "",
        )


def get_dropbox_auth(user_id: int) -> "DropboxAuth | None":
    """Dropbox 자격증명을 복호화해 반환. 미등록이면 None.

    refresh 방식(app_key/secret/refresh_token)이 있으면 그것을 우선 사용(자동 갱신),
    없으면 레거시 access token 으로 폴백한다.
    """
    user_id = credential_user_id()   # 공용: admin 계정 자격증명 사용
    with get_session() as db:
        c = db.query(DropboxCredential).filter_by(user_id=user_id).first()
        if c is None:
            return None
        auth = DropboxAuth(
            refresh_token=decrypt_secret(c.encrypted_refresh_token)
            if c.encrypted_refresh_token
            else "",
            app_key=decrypt_secret(c.encrypted_app_key) if c.encrypted_app_key else "",
            app_secret=decrypt_secret(c.encrypted_app_secret) if c.encrypted_app_secret else "",
            access_token=decrypt_secret(c.encrypted_token) if c.encrypted_token else "",
        )
        return auth if auth.usable else None
