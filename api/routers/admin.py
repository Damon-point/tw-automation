"""
관리·설정 — 8080 의 설정(settings) · 자격증명 현황(credentials) · 계정(accounts) · 사내 인원(staff) ·
메일 문구(mail_templates_page) · RN 템플릿(templates_page) · 사용량(usage) · FOTA-DB 열람(excel_files) ·
제품 관리(products) · 메일 보기(mail_detail) 를 옮긴 것.

비밀번호·토큰 값은 화면으로 내보내지 않는다 — '설정됨' 여부와 갱신 시각만 준다. 바꿀 때만 새 값을 받는다.
공용 자격증명(웹 로그인·파일서버·Dropbox·Mailchimp)은 8080 과 같이 공용 계정(admin) 행에 저장한다.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.database import get_session
from app.models import (
    CachedMail,
    DropboxCredential,
    MailchimpCredential,
    MailCredential,
    SftpCredential,
    User,
    WebCredential,
)
from app.security import decrypt_secret, encrypt_secret, hash_password, verify_password
from common import admin, user

router = APIRouter(prefix="/api/admin")

_MAIL_SERVER = os.getenv("MAIL_SERVER", "mail.pointmobile.com")
_MAIL_PORT = int(os.getenv("MAIL_POP_PORT", "995") or "995")

# 웹 로그인 대상별 고정 설정 — 8080 pages/settings._WEB_FIXED 와 같은 값
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


def _ts(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def _shared_uid() -> int:
    from app.integrations.creds import credential_user_id

    return credential_user_id()


def _bad(msg: str, code: int = 400):
    raise HTTPException(code, msg)


# ── 설정: 전체 현황 ────────────────────────────────────────────────────────────────

@router.get("/settings")
def settings_overview(request: Request):
    u = user(request)
    from app.integrations.fileserver import FS_TARGETS
    from app.models import SharedMailbox
    from app.user_mailer import sender_profile, system_from, system_sender_user_id

    sid = _shared_uid()
    relay = os.getenv("TW_MAIL_ACCOUNT", "").strip()
    with get_session() as db:
        box = db.query(SharedMailbox).filter_by(id=1).first()
        mine = db.query(MailCredential).filter_by(user_id=u["id"]).first()
        webs = {w.target: w for w in db.query(WebCredential).filter_by(user_id=sid).all()}
        smbs = {s.target: s for s in db.query(SftpCredential).filter_by(user_id=sid).all()}
        dbx = db.query(DropboxCredential).filter_by(user_id=sid).first()
        mc = db.query(MailchimpCredential).filter_by(user_id=sid).first()

        out: dict = {"is_admin": u.get("role") == "admin"}
        out["shared_mailbox"] = {
            "email": box.email_address if box else "", "has_password": bool(box and box.encrypted_password),
            "updated_by": (box.updated_by if box else "") or "",
            "server": f"{_MAIL_SERVER}:{_MAIL_PORT}",
        }
        relay_owner = (not relay) or system_sender_user_id() == u["id"]
        addr, _n, _s = sender_profile(u["id"])
        f_addr, f_name, _r = system_from()
        out["sender"] = {
            "relay_owner": relay_owner, "relay": bool(relay), "my_address": addr or "",
            "system_from": f_addr or "", "system_from_name": f_name or "",
            "email": mine.email_address if mine else "", "has_password": bool(mine and mine.encrypted_password),
            "updated_at": _ts(mine.updated_at) if mine and hasattr(mine, "updated_at") else "",
        }
        out["web"] = [{
            "target": t, "label": cfg["label"], "base_url": cfg["base_url"],
            "username": webs[t].username if t in webs else "",
            "has_password": bool(t in webs and webs[t].encrypted_password),
            "updated_at": _ts(getattr(webs.get(t), "updated_at", None)),
        } for t, cfg in _WEB_FIXED.items()]
        out["smb"] = [{
            "target": t, "label": info["label"], "host": info["host"],
            "username": smbs[t].username if t in smbs else "",
            "has_password": bool(t in smbs and smbs[t].encrypted_password),
            "updated_at": _ts(getattr(smbs.get(t), "updated_at", None)),
        } for t, info in FS_TARGETS.items()]
        has_refresh = bool(dbx and dbx.encrypted_refresh_token and dbx.encrypted_app_key and dbx.encrypted_app_secret)
        out["dropbox"] = {
            "mode": "refresh" if has_refresh else "legacy" if (dbx and dbx.encrypted_token) else "none",
            "has_app_key": bool(dbx and dbx.encrypted_app_key), "updated_at": _ts(getattr(dbx, "updated_at", None)),
        }
        out["mailchimp"] = {
            "has_key": bool(mc and mc.encrypted_api_key),
            "server_prefix": (mc.server_prefix if mc else "") or "", "from_name": (mc.from_name if mc else "") or "",
            "reply_to": (mc.reply_to if mc else "") or "", "subject_default": (mc.subject_default if mc else "") or "",
            "updated_at": _ts(getattr(mc, "updated_at", None)),
        }

    from app.integrations import claude_cli as CC

    st = CC.status()
    out["claude"] = {"backend": st["backend"], "claude": st["claude"] or "", "has_key": bool(st["has_key"])}

    from app.issuetrack import settings as it_settings

    cfg = it_settings.load()
    missing = []
    try:
        from app.issuetrack import sb as _sb

        missing = [{"name": n, "count": c} for n, c in _sb.assignees_without_account()]
    except Exception:  # noqa: BLE001
        pass
    out["sb"] = {"sb_auto_notify": cfg.sb_auto_notify, "sb_assignees": cfg.sb_assignees,
                 "exclude_keywords": cfg.exclude_keywords, "sb_auto_generate": cfg.sb_auto_generate,
                 "sb_per_tick": cfg.sb_per_tick, "missing_accounts": missing}
    return out


# ── 메일 계정 ──────────────────────────────────────────────────────────────────

class MailAccountIn(BaseModel):
    email: str
    password: str = ""


def _pop_cfg(email: str, password: str):
    from app.mail.pop3_client import ConnectionConfig

    return ConnectionConfig(email_address=email, password=password, server=_MAIL_SERVER, port=_MAIL_PORT, use_ssl=True)


@router.post("/settings/shared-mailbox")
def save_shared_mailbox(body: MailAccountIn, request: Request):
    u = admin(request)
    from app.models import SharedMailbox

    email = body.email.strip()
    if not email:
        _bad("메일 주소를 입력하세요")
    with get_session() as db:
        row = db.query(SharedMailbox).filter_by(id=1).first()
        if row is None:
            if not body.password:
                _bad("최초 등록 시 앱 비밀번호가 필요합니다")
            row = SharedMailbox(id=1)
            db.add(row)
        row.email_address = email
        row.pop_server, row.pop_port, row.use_ssl = _MAIL_SERVER, _MAIL_PORT, True
        row.updated_by = u["username"]
        if body.password:
            row.encrypted_password = encrypt_secret(body.password)
        db.commit()
    return {"ok": True, "message": "공용 수신 계정을 저장했습니다"}


@router.post("/settings/shared-mailbox/test")
def test_shared_mailbox(body: MailAccountIn, request: Request):
    admin(request)
    from app.mail.pop3_client import test_connection
    from app.models import SharedMailbox

    pw = body.password
    if not pw:
        with get_session() as db:
            row = db.query(SharedMailbox).filter_by(id=1).first()
            pw = decrypt_secret(row.encrypted_password) if row and row.encrypted_password else ""
    if not body.email.strip() or not pw:
        _bad("메일 주소와 앱 비밀번호가 필요합니다")
    ok, msg = test_connection(_pop_cfg(body.email.strip(), pw))
    return {"ok": ok, "message": msg}


def _relay_guard(u: dict) -> None:
    from app.user_mailer import system_sender_user_id

    relay = os.getenv("TW_MAIL_ACCOUNT", "").strip()
    if relay and system_sender_user_id() != u["id"]:
        _bad("공용 발송 계정을 쓰고 있어 등록할 것이 없습니다", 409)


@router.post("/settings/smtp")
def save_smtp(body: MailAccountIn, request: Request):
    u = user(request)
    _relay_guard(u)
    email = body.email.strip()
    if not email:
        _bad("메일 주소를 입력하세요")
    with get_session() as db:
        row = db.query(MailCredential).filter_by(user_id=u["id"]).first()
        if row is None:
            if not body.password:
                _bad("최초 등록 시 앱 비밀번호가 필요합니다")
            row = MailCredential(user_id=u["id"], encrypted_password="")
            db.add(row)
        row.email_address = email
        row.pop_server, row.pop_port, row.use_ssl = _MAIL_SERVER, _MAIL_PORT, True
        if body.password:
            row.encrypted_password = encrypt_secret(body.password)
        db.commit()
    return {"ok": True, "message": "발송 계정을 저장했습니다"}


@router.post("/settings/smtp/test")
def test_smtp(body: MailAccountIn, request: Request):
    u = user(request)
    _relay_guard(u)
    from app.mail.pop3_client import test_connection

    pw = body.password
    if not pw:
        with get_session() as db:
            row = db.query(MailCredential).filter_by(user_id=u["id"]).first()
            pw = decrypt_secret(row.encrypted_password) if row and row.encrypted_password else ""
    if not body.email.strip() or not pw:
        _bad("메일 주소와 앱 비밀번호가 필요합니다")
    ok, msg = test_connection(_pop_cfg(body.email.strip(), pw))
    return {"ok": ok, "message": msg}


@router.post("/settings/mail-import")
async def mail_import(request: Request, files: list[UploadFile] = File(...)):
    u = user(request)
    from app.mail.import_mail import import_file

    out = []
    for f in files:
        data = await f.read()
        res = import_file(data, f.filename or "mail.eml", u["id"])
        out.append({"name": f.filename, "status": res.get("status"), "subject": res.get("subject") or "",
                    "message": res.get("message") or "", "mail_id": res.get("mail_id")})
    return {"results": out}


# ── 웹 로그인 · 파일서버 · Dropbox · Mailchimp ───────────────────────────────────────

class LoginCredIn(BaseModel):
    username: str
    password: str = ""


@router.post("/settings/web/{target}")
def save_web(target: str, body: LoginCredIn, request: Request):
    user(request)
    cfg = _WEB_FIXED.get(target)
    if not cfg:
        _bad("모르는 대상입니다", 404)
    if not body.username.strip():
        _bad("아이디를 입력하세요")
    sid = _shared_uid()
    with get_session() as db:
        row = db.query(WebCredential).filter_by(user_id=sid, target=target).first()
        if row is None:
            if not body.password:
                _bad("최초 등록 시 비밀번호가 필요합니다")
            row = WebCredential(user_id=sid, target=target)
            db.add(row)
        row.base_url, row.login_url = cfg["base_url"], cfg["login_url"]
        row.username = body.username.strip()
        row.username_sel, row.password_sel, row.submit_sel = cfg["user_sel"], cfg["pw_sel"], cfg["submit_sel"]
        row.edit_url_template, row.editor_iframe_sel, row.save_button_sel = cfg["edit_tpl"], cfg["iframe_sel"], cfg["save_sel"]
        if body.password:
            row.encrypted_password = encrypt_secret(body.password)
        db.commit()
    return {"ok": True, "message": f"{cfg['label']} 계정을 저장했습니다"}


@router.post("/settings/smb/{target}")
def save_smb(target: str, body: LoginCredIn, request: Request):
    user(request)
    from app.integrations.fileserver import FS_TARGETS

    info = FS_TARGETS.get(target)
    if not info:
        _bad("모르는 서버입니다", 404)
    if not body.username.strip():
        _bad("아이디를 입력하세요")
    sid = _shared_uid()
    with get_session() as db:
        row = db.query(SftpCredential).filter_by(user_id=sid, target=target).first()
        if row is None:
            if not body.password:
                _bad("최초 등록 시 비밀번호가 필요합니다")
            row = SftpCredential(user_id=sid, target=target)
            db.add(row)
        row.protocol, row.host, row.port = "smb", info["host"], 445
        row.username, row.base_path = body.username.strip(), ""
        if body.password:
            row.encrypted_password = encrypt_secret(body.password)
        db.commit()
    return {"ok": True, "message": f"{info['label']} 계정을 저장했습니다"}


@router.post("/settings/smb/{target}/test")
def test_smb(target: str, body: LoginCredIn, request: Request):
    user(request)
    from app.integrations.fileserver import FS_TARGETS, make_smb_backend

    if target not in FS_TARGETS:
        _bad("모르는 서버입니다", 404)
    pw = body.password
    if not pw:
        with get_session() as db:
            row = db.query(SftpCredential).filter_by(user_id=_shared_uid(), target=target).first()
            pw = decrypt_secret(row.encrypted_password) if row and row.encrypted_password else ""
    if not body.username.strip() or not pw:
        _bad("아이디와 비밀번호가 필요합니다")
    ok, msg = make_smb_backend(target, body.username.strip(), pw).test()
    return {"ok": ok, "message": msg}


class DropboxKeysIn(BaseModel):
    app_key: str = ""
    app_secret: str = ""
    code: str = ""


def _dbx_keys(body: DropboxKeysIn) -> tuple[str, str]:
    ak, asec = body.app_key.strip(), body.app_secret.strip()
    with get_session() as db:
        c = db.query(DropboxCredential).filter_by(user_id=_shared_uid()).first()
        if not ak and c and c.encrypted_app_key:
            ak = decrypt_secret(c.encrypted_app_key)
        if not asec and c and c.encrypted_app_secret:
            asec = decrypt_secret(c.encrypted_app_secret)
    if not ak or not asec:
        _bad("App key 와 App secret 을 입력하세요")
    return ak, asec


def _dbx_save(ak: str, asec: str, refresh: str = "") -> None:
    sid = _shared_uid()
    with get_session() as db:
        row = db.query(DropboxCredential).filter_by(user_id=sid).first()
        if row is None:
            row = DropboxCredential(user_id=sid)
            db.add(row)
        row.encrypted_app_key, row.encrypted_app_secret = encrypt_secret(ak), encrypt_secret(asec)
        if refresh:
            row.encrypted_refresh_token = encrypt_secret(refresh)
        db.commit()


@router.post("/settings/dropbox/authorize-url")
def dropbox_authorize_url(body: DropboxKeysIn, request: Request):
    user(request)
    from app.integrations.dropbox_client import build_authorize_url

    ak, asec = _dbx_keys(body)
    try:
        url = build_authorize_url(ak, asec)
    except Exception as e:  # noqa: BLE001
        _bad(f"인증 URL 생성 실패: {e}")
    _dbx_save(ak, asec)
    return {"url": url}


@router.post("/settings/dropbox/finish")
def dropbox_finish(body: DropboxKeysIn, request: Request):
    user(request)
    from app.integrations.dropbox_client import exchange_code

    ak, asec = _dbx_keys(body)
    if not body.code.strip():
        _bad("인증 코드를 입력하세요")
    ok, res = exchange_code(ak, asec, body.code.strip())
    if not ok:
        _bad(f"인증 실패: {res}")
    _dbx_save(ak, asec, refresh=res)
    return {"ok": True, "message": "Dropbox 를 refresh 방식으로 등록했습니다"}


@router.post("/settings/dropbox/test")
def dropbox_test(request: Request):
    u = user(request)
    from app.integrations.creds import get_dropbox_auth
    from app.integrations.dropbox_client import test_token

    auth = get_dropbox_auth(u["id"])
    if auth is None:
        _bad("등록된 Dropbox 자격증명이 없습니다")
    ok, msg = test_token(auth)
    return {"ok": ok, "message": msg}


class MailchimpIn(BaseModel):
    api_key: str = ""
    server_prefix: str = ""
    from_name: str = ""
    reply_to: str = ""
    subject_default: str = ""


@router.post("/settings/mailchimp")
def save_mailchimp(body: MailchimpIn, request: Request):
    user(request)
    sid = _shared_uid()
    with get_session() as db:
        row = db.query(MailchimpCredential).filter_by(user_id=sid).first()
        if row is None:
            if not body.api_key.strip():
                _bad("최초 등록 시 API 키가 필요합니다")
            row = MailchimpCredential(user_id=sid)
            db.add(row)
        if body.api_key.strip():
            row.encrypted_api_key = encrypt_secret(body.api_key.strip())
        row.server_prefix = body.server_prefix.strip()
        row.from_name = body.from_name.strip()
        row.reply_to = body.reply_to.strip()
        row.subject_default = body.subject_default.strip()
        db.commit()
    return {"ok": True, "message": "Mailchimp 설정을 저장했습니다"}


@router.post("/settings/mailchimp/test")
def test_mailchimp(request: Request):
    u = user(request)
    from app.integrations import mailchimp as MC
    from app.integrations.creds import get_mailchimp_config

    cfg = get_mailchimp_config(u["id"])
    if cfg is None or not cfg.usable:
        _bad("먼저 API 키를 저장하세요")
    ok, msg = MC.ping(cfg)
    return {"ok": ok, "message": msg}


# ── Claude · Solution Buffer ────────────────────────────────────────────────────

class ClaudeIn(BaseModel):
    backend: str


@router.post("/settings/claude")
def save_claude(body: ClaudeIn, request: Request):
    u = admin(request)
    from app import app_settings
    from app.integrations import claude_cli as CC

    try:
        app_settings.set("claude_backend", body.backend, by=str(u["id"]))
    except ValueError as e:
        _bad(str(e))
    st = CC.status()
    return {"backend": st["backend"], "claude": st["claude"] or "", "has_key": bool(st["has_key"])}


@router.post("/settings/claude/try")
def try_claude(request: Request):
    admin(request)
    import anthropic

    t = time.time()
    try:
        r = anthropic.Anthropic().messages.create(
            model="claude-haiku-4-5", max_tokens=32,
            messages=[{"role": "user", "content": "1+1 은? 숫자만 답하세요."}])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"실패: {e}"}
    said = "".join(getattr(b, "text", "") for b in r.content).strip()
    use = r.usage
    return {"ok": True, "message": f"{time.time() - t:.1f}초 · 답 {said[:20]!r} · 토큰 "
                                   f"{getattr(use, 'input_tokens', 0)}/{getattr(use, 'output_tokens', 0)}"}


class SbIn(BaseModel):
    key: str
    value: str | bool | int


_SB_KEYS = {"sb_auto_notify": bool, "sb_assignees": str, "exclude_keywords": str}


@router.post("/settings/sb")
def save_sb(body: SbIn, request: Request):
    user(request)
    from app.issuetrack import settings as it_settings

    typ = _SB_KEYS.get(body.key)
    if typ is None:
        _bad("바꿀 수 없는 항목입니다")
    val = bool(body.value) if typ is bool else str(body.value or "").strip()
    it_settings.set_(body.key, val)
    return {"ok": True}


# ── 자격증명 현황(계정별 · 값은 내보내지 않는다) ───────────────────────────────────────

@router.get("/credentials")
def credentials(request: Request):
    user(request)
    from app.integrations.fileserver import FS_TARGETS

    out = []
    with get_session() as db:
        for u in db.query(User).order_by(User.id).all():
            mail = db.query(MailCredential).filter_by(user_id=u.id).first()
            fs = {s.target: s for s in db.query(SftpCredential).filter_by(user_id=u.id).all()}
            dbx = db.query(DropboxCredential).filter_by(user_id=u.id).first()
            webs = {w.target: w for w in db.query(WebCredential).filter_by(user_id=u.id).all()}
            mc = db.query(MailchimpCredential).filter_by(user_id=u.id).first()
            items = [{"label": "메일 계정", "set": bool(mail),
                      "detail": f"{mail.email_address} · 비밀번호 {'있음' if mail.encrypted_password else '없음'}" if mail else ""}]
            for t, info in FS_TARGETS.items():
                s = fs.get(t)
                items.append({"label": f"파일서버 {info['label']}", "set": bool(s),
                              "detail": f"\\\\{s.host} · {s.username or '-'} · 갱신 {_ts(s.updated_at)}" if s else ""})
            ref = bool(dbx and dbx.encrypted_refresh_token and dbx.encrypted_app_key and dbx.encrypted_app_secret)
            items.append({"label": "Dropbox", "set": ref or bool(dbx and dbx.encrypted_token),
                          "detail": ("refresh 방식(자동 갱신)" if ref else "레거시 토큰(만료됨)" if dbx and dbx.encrypted_token else "")
                          + (f" · 갱신 {_ts(dbx.updated_at)}" if dbx else "")})
            for t, cfg in _WEB_FIXED.items():
                w = webs.get(t)
                items.append({"label": cfg["label"], "set": bool(w),
                              "detail": f"{w.username or '-'} · 비밀번호 {'있음' if w.encrypted_password else '없음'} · 갱신 {_ts(w.updated_at)}" if w else ""})
            items.append({"label": "Mailchimp", "set": bool(mc and mc.encrypted_api_key),
                          "detail": f"서버 {mc.server_prefix or '자동'} · 발신 {mc.from_name or '-'} · 회신 {mc.reply_to or '-'}"
                          if mc and mc.encrypted_api_key else ""})
            out.append({"id": u.id, "username": u.username, "role": u.role or "user",
                        "created_at": _ts(u.created_at) if hasattr(u, "created_at") else "", "items": items})
    return {"users": out, "shared_uid": _shared_uid()}


# ── 계정 ─────────────────────────────────────────────────────────────────────

@router.get("/accounts")
def accounts(request: Request):
    u = user(request)
    from app.accounts_admin import ROLE_LABEL
    from app.auth import INITIAL_PASSWORD
    from app.user_mailer import system_from, user_email

    f_addr, f_name, _ = system_from()
    rows = []
    if u.get("role") == "admin":
        with get_session() as db:
            users = [(x.id, x.username, x.role or "user", bool(getattr(x, "must_change_password", False)))
                     for x in db.query(User).order_by(User.id).all()]
        rows = [{"id": i, "username": n, "role": r, "must_change": m, "email": user_email(i) or "", "me": i == u["id"]}
                for i, n, r, m in users]
    return {"roles": ROLE_LABEL, "users": rows, "system_from": f_addr or "", "system_from_name": f_name or "",
            "initial_password": INITIAL_PASSWORD if u.get("role") == "admin" else ""}


class NewAccountIn(BaseModel):
    username: str
    role: str = "user"


@router.post("/accounts")
def create_account(body: NewAccountIn, request: Request):
    u = admin(request)
    import re

    from app.accounts_admin import VALID_ROLES
    from app.auth import INITIAL_PASSWORD

    uname = body.username.strip()
    if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", uname):
        _bad("아이디는 회사 이메일 주소 형식이어야 합니다")
    if body.role not in VALID_ROLES:
        _bad("모르는 역할입니다")
    with get_session() as db:
        if db.query(User).filter_by(username=uname).first():
            _bad(f"'{uname}' 은 이미 있는 아이디입니다", 409)
        db.add(User(username=uname, password_hash=hash_password(INITIAL_PASSWORD), role=body.role,
                    must_change_password=True))
        db.commit()
    from app.account_mail import send_welcome

    sent, why = send_welcome(u["id"], uname)
    return {"ok": True, "message": f"'{uname}' 계정을 만들었습니다" + (" · 안내 메일 발송" if sent else f" · 안내 메일 실패: {why}")}


class RoleIn(BaseModel):
    role: str


@router.post("/accounts/{uid}/role")
def set_role(uid: int, body: RoleIn, request: Request):
    u = admin(request)
    from app.accounts_admin import set_user_role

    ok, msg = set_user_role(uid, body.role, u["id"])
    if not ok:
        _bad(msg, 409)
    return {"ok": True, "message": msg}


@router.post("/accounts/{uid}/reset-password")
def reset_pw(uid: int, request: Request):
    u = admin(request)
    from app.account_mail import send_reset
    from app.accounts_admin import reset_password

    ok, msg, who = reset_password(uid)
    if not ok:
        _bad(msg, 404)
    sent, why = send_reset(u["id"], who)
    return {"ok": True, "message": msg + (" · 안내 메일 발송" if sent else f" · 안내 메일 실패: {why}")}


@router.delete("/accounts/{uid}")
def delete_account(uid: int, request: Request):
    u = admin(request)
    from app.accounts_admin import delete_user

    ok, msg = delete_user(uid, u["id"])
    if not ok:
        _bad(msg, 409)
    return {"ok": True, "message": msg}


class PasswordIn(BaseModel):
    current: str
    new: str


@router.post("/me/password")
def change_my_password(body: PasswordIn, request: Request):
    u = user(request)
    from app.auth import INITIAL_PASSWORD

    pw = (body.new or "").strip()
    if len(pw) < 4:
        _bad("비밀번호는 4자 이상이어야 합니다")
    if pw == INITIAL_PASSWORD:
        _bad("초기 비밀번호와 다른 값으로 정하세요")
    with get_session() as db:
        row = db.query(User).filter_by(id=u["id"]).first()
        if row is None or not verify_password(body.current, row.password_hash):
            _bad("지금 비밀번호가 맞지 않습니다", 403)
        row.password_hash = hash_password(pw)
        row.must_change_password = False
        db.commit()
    return {"ok": True, "message": "비밀번호를 바꿨습니다"}


# ── 사내 인원 ──────────────────────────────────────────────────────────────────

@router.get("/staff")
def staff_list(request: Request):
    admin(request)
    from app import staff as ST

    return {"staff": ST.list_staff(), "statuses": list(ST.STATUSES), "active": ST.ACTIVE, "left": ST.LEFT}


class StaffIn(BaseModel):
    id: int | None = None
    name: str = ""
    email: str = ""
    team: str = ""
    status: str = ""
    is_dqa: bool = False
    is_handler: bool = False
    memo: str = ""


@router.post("/staff")
def staff_save(body: StaffIn, request: Request):
    u = admin(request)
    from app import staff as ST

    ok, msg = ST.save_staff(id=body.id, name=body.name, email=body.email, status=body.status or ST.ACTIVE,
                            is_dqa=body.is_dqa, is_handler=body.is_handler, team=body.team, memo=body.memo,
                            by=u["username"])
    if not ok:
        _bad(msg)
    return {"ok": True, "message": msg}


@router.delete("/staff/{sid}")
def staff_delete(sid: int, request: Request):
    admin(request)
    from app import staff as ST

    ok, msg = ST.delete_staff(sid)
    if not ok:
        _bad(msg)
    return {"ok": True, "message": msg}


# ── 메일 문구 ──────────────────────────────────────────────────────────────────

def _tpl_out(d: dict) -> dict:
    return {**d, "updated_at": _ts(d.get("updated_at"))}


@router.get("/mail-templates")
def mail_templates(request: Request):
    admin(request)
    from app import mail_templates as MT

    return {"groups": [{"group": g, "items": [_tpl_out(MT.get(k)) for k, t in MT.TEMPLATES.items() if t.group == g]}
                       for g in MT.groups()]}


class MailTplIn(BaseModel):
    subject: str
    body: str


@router.post("/mail-templates/{key}/preview")
def mail_template_preview(key: str, body: MailTplIn, request: Request):
    admin(request)
    from app import mail_templates as MT

    if key not in MT.TEMPLATES:
        _bad("없는 문구입니다", 404)
    vals = MT.sample(key)
    return {"subject": MT.fill(body.subject, vals), "body": MT.fill(body.body, vals),
            "unknown": MT.unknown_vars(key, body.subject, body.body),
            "missing": MT.missing_vars(key, body.subject, body.body)}


@router.post("/mail-templates/{key}")
def mail_template_save(key: str, body: MailTplIn, request: Request):
    u = admin(request)
    from app import mail_templates as MT

    bad = MT.unknown_vars(key, body.subject, body.body)
    if bad:
        _bad("값이 채워지지 않는 자리가 있습니다: " + ", ".join("{{" + b + "}}" for b in bad))
    gone = MT.missing_vars(key, body.subject, body.body)
    ok, msg = MT.save(key, body.subject, body.body, u["username"])
    if not ok:
        _bad(msg)
    return {"ok": True, "message": msg, "missing": gone, "item": _tpl_out(MT.get(key))}


@router.post("/mail-templates/{key}/reset")
def mail_template_reset(key: str, request: Request):
    admin(request)
    from app import mail_templates as MT

    ok, msg = MT.reset(key)
    if not ok:
        _bad(msg)
    return {"ok": True, "message": msg, "item": _tpl_out(MT.get(key))}


# ── RN 템플릿 파일 ───────────────────────────────────────────────────────────────

@router.get("/templates")
def templates(request: Request):
    admin(request)
    from app.office import templates as T

    return {"templates": [{k: v for k, v in it.items() if k != "path"} for it in T.status()]}


@router.post("/templates/{key}")
async def template_upload(key: str, request: Request, file: UploadFile = File(...)):
    admin(request)
    from app.office import templates as T

    spec = T.TEMPLATES.get(key)
    if not spec:
        _bad("없는 템플릿입니다", 404)
    if not (file.filename or "").lower().endswith(spec[2]):
        _bad(f"{spec[2]} 파일만 올릴 수 있습니다")
    ok, msg = T.save(key, await file.read())
    if not ok:
        _bad(msg)
    return {"ok": True, "message": msg}


@router.get("/templates/{key}/file")
def template_download(key: str, request: Request):
    admin(request)
    from app.office import templates as T

    p = T.path_for(key)
    if not p or not p.exists():
        _bad("파일이 없습니다", 404)
    return FileResponse(str(p), filename=p.name)


@router.delete("/templates/{key}")
def template_delete(key: str, request: Request):
    admin(request)
    from app.office import templates as T

    ok, msg = T.delete(key)
    if not ok:
        _bad(msg)
    return {"ok": True, "message": msg}


# ── Claude 사용량 ──────────────────────────────────────────────────────────────

@router.get("/usage")
def usage(request: Request, scope: str = "month"):
    user(request)
    from app import usage as US

    if scope not in ("today", "month", "all"):
        scope = "month"
    return {"totals": US.totals(), "by_day": US.by_day(30), "by_month": US.by_month(12),
            "by_operation": US.by_operation(scope), "recent": US.recent(80), "scope": scope}


# ── FOTA-DB 열람(읽기 전용) ─────────────────────────────────────────────────────────

@router.get("/fota/{kind}")
def fota_list(kind: str, request: Request):
    u = user(request)
    from app.integrations import fota_system

    if kind == "osinfo":
        rows, err = fota_system.fetch_osinfo_list(u["id"])
    elif kind == "downloadpath":
        rows, err = fota_system.fetch_downloadpath_list(u["id"])
    elif kind == "whitelist":
        from app.whitelist import fota as WF

        rows, err = WF.fetch_whitelist_list(u["id"])
    else:
        _bad("모르는 표입니다", 404)
    if err:
        _bad(f"조회 실패: {err}", 502)
    return {"rows": rows, "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


# ── 제품 관리 ──────────────────────────────────────────────────────────────────

def _releases(pid: int) -> list[dict]:
    from app.pipeline import dev_rn as RN

    try:
        rels = RN.list_releases(pid)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rels:
        out.append({"version": r.get("version", ""),
                    "docx_name": r.get("docx_name") or "", "pdf_name": r.get("pdf_name") or "",
                    "has_docx": bool(r.get("docx_name") and RN.exists(r.get("docx_path"))),
                    "has_pdf": bool(r.get("pdf_name") and RN.exists(r.get("pdf_path")))})
    return out


def _product_out(r: dict) -> dict:
    from app import os_products as P

    d = {k: r.get(k, "") or "" for k in P.ALL_KEYS}
    rels = _releases(r["id"])
    d.update(id=r["id"], created_by=r.get("created_by", ""), updated_by=r.get("updated_by", ""),
             created_at=_ts(r.get("created_at")), updated_at=_ts(r.get("updated_at")),
             filled=P.filled_items(r), total=len(P.COUNTED_KEYS), missing=P.missing_labels(r),
             category=P.category_of(r), releases=rels[:30], release_count=len(rels))
    return d


@router.get("/products/meta")
def products_meta(request: Request):
    user(request)
    from app import os_products as P

    return {"categories": P.CATEGORIES, "category_hint": P.CATEGORY_HINT, "types": P.known_types(),
            "key_fields": [{"key": k, "label": lab, "placeholder": ph} for k, lab, ph in P.KEY_FIELDS],
            "info_fields": [{"key": k, "label": lab, "placeholder": ph} for k, lab, ph in P.INFO_FIELDS],
            "path_groups": [{"group": g, "fields": [{"key": k, "label": lab, "placeholder": ph} for k, lab, ph in fs]}
                            for g, fs in P.PATH_GROUPS]}


@router.get("/products")
def products(request: Request, q: str = ""):
    user(request)
    from app import os_products as P

    return {"groups": [{"category": cat, "items": [_product_out(r) for r in items]}
                       for cat, items in P.group_by_category(P.list_products(q))]}


class ProductIn(BaseModel):
    fields: dict[str, str]


def _collect(pid: int, uid: int) -> list[str]:
    """신규 등록 직후 — 개발 RN 수집 + 출시 이력 조회(읽기만)."""
    from app import os_products as P
    from app import release_history as RH
    from app.pipeline import dev_rn as RN

    done = []
    p = P.get(pid) or {}
    if p.get("dev_rn_path"):
        try:
            res = RN.scan_product(pid)
            if res.get("ok"):
                done.append(f"개발 RN {res['total']}판")
        except Exception:  # noqa: BLE001
            pass
    try:
        hist = RH.refresh(pid, uid)
        if hist.get("ok"):
            done.append(f"출시 이력 {len(hist['versions'])}개")
    except Exception:  # noqa: BLE001
        pass
    return done


@router.post("/products")
def product_create(body: ProductIn, request: Request):
    u = user(request)
    from app import os_products as P

    ok, msg = P.create(body.fields, u["username"])
    if not ok:
        _bad(msg, 409)
    f = body.fields
    key = (f.get("product", "").strip().upper().replace(" ", ""),
           "".join(c for c in f.get("android", "") if c.isdigit()), f.get("os_type", "").strip())
    saved = next((r for r in P.list_products() if (r["product"], r["android"], r["os_type"]) == key), None)
    collected = _collect(saved["id"], u["id"]) if saved else []
    return {"ok": True, "message": msg, "id": saved["id"] if saved else None, "collected": collected}


@router.put("/products/{pid}")
def product_update(pid: int, body: ProductIn, request: Request):
    u = user(request)
    from app import os_products as P

    ok, msg = P.update(pid, body.fields, u["username"])
    if not ok:
        _bad(msg, 409)
    return {"ok": True, "message": msg}


@router.delete("/products/{pid}")
def product_delete(pid: int, request: Request):
    user(request)
    from app import os_products as P

    ok, msg = P.delete(pid)
    if not ok:
        _bad(msg, 409)
    return {"ok": True, "message": msg}


@router.post("/products/{pid}/rescan")
def product_rescan(pid: int, request: Request):
    user(request)
    from app import os_products as P
    from app.pipeline import dev_rn as RN

    p = P.get(pid)
    if not p:
        _bad("제품이 없습니다", 404)
    if not (p.get("dev_rn_path") or "").strip():
        _bad("'개발 Release Note 파일 경로' 가 비어 있습니다")
    try:
        res = RN.scan_product(pid)
    except Exception as e:  # noqa: BLE001
        _bad(f"수집 실패: {type(e).__name__}: {e}", 502)
    if not res.get("ok"):
        _bad(f"수집 실패: {res.get('error')}", 502)
    return {"ok": True, "total": res.get("total", 0), "downloaded": res.get("downloaded", 0),
            "skipped": (res.get("skipped") or [])[:20], "releases": _releases(pid)}


@router.get("/products/{pid}/releases/{version}/{kind}")
def product_release_file(pid: int, version: str, kind: str, request: Request):
    user(request)
    from app.pipeline import dev_rn as RN

    rel = next((r for r in RN.list_releases(pid) if r.get("version") == version), None)
    path = (rel or {}).get("docx_path" if kind == "docx" else "pdf_path")
    if not rel or not path or not RN.exists(path):
        _bad("파일이 없습니다", 404)
    return FileResponse(path, filename=Path(path).name)


# ── 메일 보기 ──────────────────────────────────────────────────────────────────

def _attachments(m: CachedMail) -> list[dict]:
    names = [a for a in (m.attachment_names or "").splitlines() if a.strip()]
    d = Path(m.attachment_dir) if m.attachment_dir else None
    out = []
    for n in names:
        p = (d / n) if d else None
        ok = bool(p and p.is_file())
        out.append({"name": n, "available": ok, "size": p.stat().st_size if ok else 0})
    return out


@router.get("/mail/{mail_id}")
def mail_detail(mail_id: int, request: Request):
    user(request)
    with get_session() as db:
        m = db.query(CachedMail).filter_by(id=mail_id).first()
        if m is None:
            _bad("메일을 찾을 수 없습니다", 404)
        return {"id": m.id, "subject": m.subject or "", "sender": m.sender or "", "received_at": str(m.received_at or ""),
                "to": m.to_recipients or "", "cc": m.cc_recipients or "",
                "html": m.body_html or "", "text": m.body_text or "", "attachments": _attachments(m),
                "serials": [s for s in (m.attachment_serials or "").splitlines() if s.strip()][:200]}


@router.get("/mail/{mail_id}/attachment")
def mail_attachment(mail_id: int, name: str, request: Request):
    user(request)
    with get_session() as db:
        m = db.query(CachedMail).filter_by(id=mail_id).first()
        if m is None or not m.attachment_dir:
            _bad("첨부가 없습니다", 404)
        names = [a for a in (m.attachment_names or "").splitlines() if a.strip()]
        base = Path(m.attachment_dir).resolve()
    if name not in names:
        _bad("첨부가 없습니다", 404)
    p = (base / name).resolve()
    try:
        p.relative_to(base)
    except ValueError:
        _bad("허용되지 않은 경로", 404)
    if not p.is_file():
        _bad("파일이 없습니다", 404)
    return FileResponse(str(p), filename=p.name)
