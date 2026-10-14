"""PMDM License 발급 페이지 (/licenses) — 내부 로그인 전용.

지금은 별개 페이지지만, 본문을 render_license_content() 하나로 묶어
나중에 통합 페이지의 탭 패널에 그대로 꽂을 수 있게 구성한다(credentials.py 와 동일 패턴).

탭: 신청 / 내 신청 내역 / (관리자 — 'admin' 계정 항상, LICENSE_ADMIN_USERS 로 추가 지정).
admin 로그인 시 관리자 탭이 기본 화면. 관리자 = 목록/필터/상세/승인/반려/키/메모/삭제(감사)/통계.
DB 는 전용 pmdm_license.db(기존 tw_app.db 와 분리). 파일은 /license/po|cert/{id}(로그인+본인/관리자).
"""
from __future__ import annotations

import os
from datetime import datetime

from fastapi import HTTPException
from fastapi.responses import FileResponse
from nicegui import app as ng_app, run, ui

from app import user_mailer
from app.auth import current_user_id, current_username
from app.license import cert, licensing, mailer, service
from app.license.models import LicenseRequest  # noqa: F401  (테이블 등록 목적)
from app.theme import apply_theme, page_header

MAX_PO = 20 * 1024 * 1024  # 20MB
_EMAIL_RE = __import__("re").compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _qd(v) -> str:
    """수량 표시: 정수 + 천단위 콤마 ('1000.0' → '1,000')."""
    try:
        return f"{int(float(str(v).strip())):,}"
    except (TypeError, ValueError):
        return str(v or "-")


@ui.page("/licenses")
def license_page():
    apply_theme()
    with page_header("License 발급"):
        render_license_content()


def render_license_content():
    """탭 재사용 진입점 — 통합 페이지에서 이 함수만 탭 패널에 넣으면 된다."""
    admin = service.is_admin(current_username())
    with ui.tabs().classes("w-full") as tabs:
        t_new = ui.tab("신청")
        t_my = ui.tab("내 신청 내역")
        t_admin = ui.tab("관리자") if admin else None
    # admin 으로 로그인하면 관리자 화면이 기본으로 열린다.
    with ui.tab_panels(tabs, value=(t_admin if admin else t_new)).classes("w-full"):
        with ui.tab_panel(t_new):
            _render_request_form()
        with ui.tab_panel(t_my):
            _render_my_requests()
        if admin:
            with ui.tab_panel(t_admin):
                _render_admin()


# ── 신청 폼 ────────────────────────────────────────────────────────────────
def _render_request_form():
    ui.label("License Key 발급 요청").classes("text-h6")
    ui.label("PMDM License Purchase Certificate 등록").classes("text-caption text-grey-7")

    po: dict = {"data": None, "name": ""}

    with ui.column().classes("w-full gap-3 q-mt-md"):
        with ui.row().classes("w-full gap-3 no-wrap"):
            f_company = ui.input("Company Name").classes("flex-1")
            f_qty = ui.number("Quantity", min=1, format="%d").classes("flex-1")
        with ui.row().classes("w-full gap-3 no-wrap"):
            f_cname = ui.input("Contact Name").classes("flex-1")
            f_cemail = ui.input("Contact E-mail").classes("flex-1")
        with ui.row().classes("w-full gap-3 no-wrap"):
            f_uid = ui.input("ID").classes("flex-1")
        f_note = ui.textarea("비고 (선택)").classes("w-full").props("autogrow")

        ui.label("PO 파일").classes("text-caption text-grey-7 q-mt-sm")

        # 원래의 드래그앤드롭 업로더를 그대로 쓰되, 헷갈리던 '파일 목록'(체크표시·용량·업로드%·
        # 제거버튼)만 CSS로 숨긴다. 첨부 확인은 아래 깔끔한 칩으로 보여준다.
        ui.add_css(".po-uploader .q-uploader__list { display: none; }")

        @ui.refreshable
        def po_status():
            if po["data"]:
                with ui.row().classes("items-center gap-2 q-mt-xs"):
                    ui.icon("description").classes("text-green-7")
                    ui.label(f"{po['name']} ({len(po['data']) // 1024:,} KB)").classes("text-body2")
                    ui.button(icon="close", on_click=_clear_po) \
                        .props("flat dense round size=sm color=negative").tooltip("첨부 제거")
            else:
                ui.label("첨부된 파일 없음").classes("text-caption text-grey-5 q-mt-xs")

        def _clear_po():
            po["data"], po["name"] = None, ""
            po_upload.reset()
            po_status.refresh()

        async def _on_upload(e):
            # NiceGUI 3.14: 업로드 이벤트는 e.file(FileUpload), read() 는 async.
            data = await e.file.read()
            if len(data) > MAX_PO:
                ui.notify("PO 파일은 20MB 이하만 첨부할 수 있습니다.", type="negative")
                po_upload.reset()
                return
            po["data"], po["name"] = data, e.file.name
            po_upload.reset()   # 내부 목록 비움(어차피 CSS로 숨겨져 있음)
            po_status.refresh()
            ui.notify(f"PO 첨부됨: {e.file.name}", type="positive")

        po_upload = ui.upload(on_upload=_on_upload, auto_upload=True, max_file_size=MAX_PO) \
            .props('accept=".pdf,.doc,.docx,.jpg,.jpeg,.png"').classes("w-full po-uploader")
        po_status()

        async def _submit():
            fields = {
                "company_name": (f_company.value or "").strip(),
                "quantity": f_qty.value,
                "contact_name": (f_cname.value or "").strip(),
                "contact_email": (f_cemail.value or "").strip(),
                "license_user_id": (f_uid.value or "").strip(),
            }
            missing = [k for k, v in fields.items() if not v]
            if missing:
                ui.notify("필수 항목(Company·Quantity·Contact·Email·ID)을 모두 입력해 주세요.", type="negative")
                return
            if not _EMAIL_RE.match(fields["contact_email"]):
                ui.notify(f"이메일 형식이 올바르지 않습니다: {fields['contact_email']}", type="negative")
                return
            if not po["data"]:
                ui.notify("PO 파일을 첨부해 주세요.", type="negative")
                return

            btn.disable()
            try:
                uid = current_user_id()
                uname = current_username() or ""
                tmp_id = datetime.now().strftime("%y%m%d.%H%M%S")
                po_path, po_name = service.save_po_file(tmp_id, po["data"], po["name"])
                data = {
                    **fields,
                    "note": (f_note.value or "").strip(),
                    "date_issued": _today(),
                    "po_path": po_path,
                    "po_filename": po_name,
                }
                req = service.create_request(data, uid, uname)
                # 승인 요청 메일 — 본인(신청자) 계정에서 관리자 전원에게(② RN 검수요청과 동일 방식).
                # License 결재자는 최종 승인자다(없으면 시스템 관리자로 폴백).
                to_admins = user_mailer.final_reviewer_emails()
                if to_admins:
                    from app import mail_templates as MT

                    subject, body = MT.render(
                        "license_request", 신청자=uname, 문서번호=req.doc_id,
                        회사=req.company_name, 수량=req.quantity,
                        담당자=f"{req.contact_name} ({req.contact_email})",
                        라이선스아이디=req.license_user_id, 비고=req.note or "-")
                    ok, err = await run.io_bound(user_mailer.send_as_user, uid, to_admins, subject, body)
                else:
                    ok, err = False, "관리자 이메일 미등록([계정 관리]에서 등록 필요)"
                msg = f"등록 완료 — Doc ID {req.doc_id}"
                if not ok:
                    msg += f" (관리자 승인요청 메일 스킵: {err})"
                ui.notify(msg, type="positive")
                # 폼 초기화
                for w in (f_company, f_qty, f_cname, f_cemail, f_uid, f_note):
                    w.value = None
                po["data"], po["name"] = None, ""
                po_status.refresh()
                po_upload.reset()
            except Exception as e:  # noqa: BLE001
                ui.notify(f"등록 실패: {e}", type="negative")
            finally:
                btn.enable()

        btn = ui.button("등록 요청", on_click=_submit).props("color=primary").classes("w-full q-mt-md")


# ── 내 신청 내역 ───────────────────────────────────────────────────────────
def _status_badge(status: str):
    color = {"승인": "green-7", "반려": "red-6", "대기중": "orange-7"}.get(status, "grey-6")
    ui.badge(status, color=color)


def _render_my_requests():
    @ui.refreshable
    def _list():
        rows = service.list_mine(current_user_id(), current_username() or "")
        if not rows:
            ui.label("등록한 요청이 없습니다.").classes("text-grey-7")
            return
        for r in rows:
            with ui.card().classes("w-full"):
                with ui.row().classes("w-full items-center justify-between no-wrap"):
                    ui.label(r.doc_id).classes("text-caption text-grey-7")
                    _status_badge(r.status)
                ui.label(r.company_name).classes("text-subtitle1")
                ui.label(f"수량 {_qd(r.quantity)} · {r.date_issued}").classes(
                    "text-caption text-grey-7"
                )
                if r.status == "승인":
                    if r.license_key:
                        ui.label(f"License Key: {r.license_key}").classes("text-body2")
                    if r.cert_path:
                        ui.link("📄 인증서 열기", f"/license/cert/{r.id}", new_tab=True)
                elif r.status == "반려":
                    ui.label(f"반려 사유: {r.admin_note or '-'}").classes("text-body2 text-red-7")
                else:
                    ui.label("검토 중입니다.").classes("text-caption text-grey-7")

    ui.button("↻ 새로고침", on_click=lambda: _list.refresh()).props("flat color=primary")
    _list()


# ── 관리자 ─────────────────────────────────────────────────────────────────
def _render_admin():
    state = {"filter": "전체", "sel": None, "q": ""}

    @ui.refreshable
    def _stats_panel():
        s = service.stats()
        with ui.row().classes("gap-3 items-stretch").style("flex-wrap: wrap"):
            for val, lbl in [
                (s["total"], "전체"),
                (s["counts"]["대기중"], "대기중"),
                (s["counts"]["승인"], "승인"),
                (s["counts"]["반려"], "반려"),
                (f'{s["total_qty"]} EA', "총 요청수량"),
                (f'{s["approved_qty"]} EA', "승인 수량"),
            ]:
                with ui.card().classes("items-center").style("min-width:96px"):
                    ui.label(str(val)).classes("text-h6")
                    ui.label(lbl).classes("text-caption text-grey-7")

    with ui.expansion("📊 통계 대시보드", icon="analytics").classes("w-full"):
        _stats_panel()

    with ui.row().classes("w-full no-wrap gap-4"):
        # 좌측: 목록
        with ui.column().classes("gap-2").style("min-width: 320px; max-width: 380px"):
            with ui.row().classes("gap-1"):
                for f in ("전체", "대기중", "승인", "반려"):
                    ui.button(
                        f, on_click=lambda f=f: (_set_filter(f))
                    ).props("flat dense color=primary")

            def _on_search(v):
                state["q"] = (v or "").strip()
                _render_list.refresh()

            ui.input(placeholder="Doc ID · 회사명 검색") \
                .props("dense clearable outlined") \
                .on_value_change(lambda e: _on_search(e.value)) \
                .classes("w-full")
            # 승인 요청이 쌓여도 페이지가 늘어나지 않게, 목록은 고정 높이 내부 스크롤 영역에 둔다.
            with ui.scroll_area().classes("w-full").style("height: 72vh"):
                list_box = ui.column().classes("w-full gap-2")
        # 우측: 상세 (테두리 있는 패널로 구분 · 목록보다 약간 아래에서 시작)
        detail_box = ui.column().classes("flex-1 gap-2").style(
            "border:1px solid #d8d8d8; border-radius:8px; padding:14px; "
            "background:#fafafa; min-height:220px; margin-top:40px"
        )

    @ui.refreshable
    def _render_list():
        rows = service.list_all()
        if state["filter"] != "전체":
            rows = [r for r in rows if r.status == state["filter"]]
        q = (state.get("q") or "").lower()
        if q:
            rows = [r for r in rows
                    if q in (r.doc_id or "").lower() or q in (r.company_name or "").lower()]
        list_box.clear()
        with list_box:
            if not rows:
                ui.label("요청이 없습니다.").classes("text-grey-7")
                return
            for r in rows:
                _sel = (state["sel"] == r.doc_id)
                with ui.card().classes("w-full cursor-pointer").on(
                    "click", lambda r=r: _select(r.doc_id)
                ).style(
                    "border:1px solid " + ("#4A7C67" if _sel else "#e0e0e0") + ";"
                    + ("background:#eef4f1;" if _sel else "")
                ):
                    with ui.row().classes("w-full items-center justify-between no-wrap"):
                        ui.label(r.doc_id).classes("text-caption text-grey-7")
                        _status_badge(r.status)
                    ui.label(r.company_name).classes("text-subtitle2")
                    ui.label(f"수량 {_qd(r.quantity)} · {r.date_issued}").classes(
                        "text-caption text-grey-7"
                    )

    @ui.refreshable
    def _render_detail():
        detail_box.clear()
        with detail_box:
            doc_id = state["sel"]
            if not doc_id:
                ui.label("← 요청을 선택하세요").classes("text-grey-7")
                return
            r = service.get(doc_id)
            if not r:
                ui.label("요청을 찾을 수 없습니다.").classes("text-grey-7")
                return
            with ui.row().classes("w-full items-center justify-between no-wrap"):
                ui.label(f"{r.doc_id} · {r.company_name}").classes("text-h6")
                _status_badge(r.status)

            grid = [
                ("발급일자", r.date_issued),
                ("Quantity", _qd(r.quantity)),
                ("Contact", f"{r.contact_name} ({r.contact_email})"),
                ("아이디", r.license_user_id),
                ("비고", getattr(r, "note", "")),
            ]
            with ui.grid(columns=1).classes("w-full gap-1"):
                for label, val in grid:
                    with ui.column().classes("gap-0"):
                        ui.label(label).classes("text-caption text-grey-6")
                        ui.label(str(val or "-")).classes("text-body2")
            if r.po_path:
                ui.link(f"📎 PO 파일: {r.po_filename or 'PO'}", f"/license/po/{r.id}", new_tab=True)

            # ── 라이선스 키: RSA 개인키로 자동 생성 + 공개키로 복호화 재검증 ──
            key_in = ui.input("라이선스 키 (자동 생성)", value=r.license_key or "") \
                .classes("w-full").props("readonly")
            verify_box = ui.column().classes("w-full gap-0 q-mb-sm")

            def _show_verify(key: str):
                verify_box.clear()
                if not key:
                    return
                chk = licensing.verify_license_key(key)
                with verify_box:
                    if not chk["ok"]:
                        ui.label(f"⚠ 키 복호화 실패: {chk.get('error', '')}") \
                            .classes("text-caption text-red-7")
                        return
                    id_ok = chk["id"] == (r.license_user_id or "")
                    cnt_ok = str(chk["count"]) == str(r.quantity or "")
                    ok_all = id_ok and cnt_ok
                    with ui.row().classes("items-center gap-1"):
                        ui.icon("verified" if ok_all else "error").style(
                            "color:" + ("#4A7C67" if ok_all else "#c0392b"))
                        ui.label("복호화 검증 (공개키로 정보 재확인)").classes("text-caption text-grey-7")
                    ui.label(f"· ID: {chk['id']} "
                             + ("✔ 일치" if id_ok else f"✘ 불일치 (신청 ID: {r.license_user_id})")) \
                        .classes("text-caption").style("color:" + ("#4A7C67" if id_ok else "#c0392b"))
                    ui.label(f"· Count: {chk['count']} "
                             + ("✔ 일치" if cnt_ok else f"✘ 불일치 (신청 수량: {r.quantity})")) \
                        .classes("text-caption").style("color:" + ("#4A7C67" if cnt_ok else "#c0392b"))

            def _gen_key():
                if not licensing.keys_configured():
                    ui.notify("RSA 키 미설정(.env LICENSE_RSA_*) — 앱 재시작 필요할 수 있음.", type="negative")
                    return
                if not (r.license_user_id and str(r.quantity or "").strip()):
                    ui.notify("ID·Quantity 가 있어야 키를 생성합니다.", type="negative")
                    return
                try:
                    key, _info = licensing.generate_license_key(r.license_user_id, r.quantity)
                except Exception as e:  # noqa: BLE001
                    ui.notify(f"키 생성 실패: {e}", type="negative")
                    return
                key_in.value = key
                service.save_license_key(doc_id, key)
                _show_verify(key)
                ui.notify("라이선스 키 생성 + 복호화 검증 완료", type="positive")

            if r.status == "대기중":
                ui.button("🔑 라이선스 키 생성 / 재생성", on_click=_gen_key) \
                    .props("color=primary size=sm").classes("q-mb-xs")
            _show_verify(r.license_key or "")

            note_in = ui.textarea("관리자 메모", value=r.admin_note or "").classes("w-full")

            def _send_result_mail(mreq, kind: str, pdf, admin_uid) -> tuple[bool, str]:
                """승인/반려 결과 메일을 **검수 없이 즉시** 발송(관리자 본인 계정 → 신청 사용자).

                kind='approval'|'rejection'. 승인이면 인증서 PDF 첨부. (sync — run.io_bound 로 호출)
                """
                to = (user_mailer.user_email(mreq.created_by)
                      if getattr(mreq, "created_by", None) else "") \
                    or mreq.recipient_email or mreq.contact_email
                m = (mailer.build_approval(mreq, pdf is not None) if kind == "approval"
                     else mailer.build_rejection(mreq))
                attachments = ([(f"{mreq.doc_id}_License_Certificate.pdf", pdf)]
                               if (kind == "approval" and pdf) else None)
                return user_mailer.send_as_user(admin_uid, to, m["subject"], m["body"], attachments)

            async def _approve():
                key = (key_in.value or r.license_key or "").strip()
                # 키가 없으면 승인 시 자동 생성
                if not key:
                    if licensing.keys_configured() and r.license_user_id and str(r.quantity or "").strip():
                        try:
                            key, _ = licensing.generate_license_key(r.license_user_id, r.quantity)
                        except Exception as e:  # noqa: BLE001
                            ui.notify(f"키 생성 실패: {e}", type="negative")
                            return
                        key_in.value = key
                    else:
                        ui.notify("라이선스 키가 없습니다. '라이선스 키 생성'을 먼저 누르세요.", type="negative")
                        return
                # 승인 전 공개키 복호화로 정보 일치 최종 확인(불일치면 승인 차단)
                chk = licensing.verify_license_key(key)
                if not chk["ok"]:
                    ui.notify(f"키 검증 실패: {chk.get('error', '')}", type="negative")
                    return
                if chk["id"] != (r.license_user_id or "") or str(chk["count"]) != str(r.quantity or ""):
                    ui.notify(f"⚠ 키 정보 불일치 — ID {chk['id']} / Count {chk['count']}. 재생성 필요.",
                              type="negative")
                    return
                service.save_license_key(doc_id, key)
                req = service.set_status(doc_id, service.STATUS_APPROVED, note_in.value or "",
                                         current_username() or "")
                if not req:
                    ui.notify("요청을 찾을 수 없습니다.", type="negative")
                    return
                # 인증서(PDF) 생성 후 승인 메일을 **즉시** 발송(검수 없이).
                prog = ui.notification("인증서 생성·메일 발송 중…", spinner=True, timeout=None)
                admin_uid = current_user_id()
                try:
                    _, pdf, cert_err = await run.io_bound(cert.render_and_save, req)
                    if cert_err:
                        mok, merr = False, ""
                    else:
                        mok, merr = await run.io_bound(
                            _send_result_mail, service.get(doc_id), "approval", pdf, admin_uid)
                finally:
                    prog.dismiss()
                if cert_err:
                    ui.notify(f"승인 완료 · ⚠ 인증서 생성 실패(메일 미발송): {cert_err}", type="warning")
                elif mok:
                    ui.notify("승인 완료 · 인증서 첨부 메일을 신청자에게 발송했습니다.", type="positive")
                else:
                    ui.notify(f"승인·인증서 완료 · ⚠ 메일 발송 실패: {merr}", type="warning")
                _render_list.refresh()
                _render_detail.refresh()
                _stats_panel.refresh()

            async def _reject():
                if not (note_in.value or "").strip():
                    ui.notify("반려 사유를 메모에 입력해 주세요.", type="negative")
                    return
                req = service.set_status(doc_id, service.STATUS_REJECTED, note_in.value or "",
                                         current_username() or "")
                admin_uid = current_user_id()
                sn = ui.notification("반려 메일 발송 중…", spinner=True, timeout=None)
                try:
                    mok, merr = await run.io_bound(_send_result_mail, req, "rejection", None, admin_uid)
                finally:
                    sn.dismiss()
                ui.notify("반려 처리 · 신청자에게 메일 발송 완료" if mok
                          else f"반려 처리됨 · ⚠ 메일 발송 실패: {merr}",
                          type="warning")
                _render_list.refresh()
                _render_detail.refresh()
                _stats_panel.refresh()

            if r.status == "대기중":
                with ui.row().classes("gap-2 q-mt-sm"):
                    ui.button("✓ 승인", on_click=_approve).props("color=primary")
                    ui.button("✕ 반려", on_click=_reject).props("color=negative outline")
            else:
                ui.label(f"처리: {r.status} · {r.confirm_date} · {r.confirm_by}").classes(
                    "text-caption text-grey-7 q-mt-sm"
                )
                ui.button(
                    "메모 저장",
                    on_click=lambda: (service.save_note(doc_id, note_in.value or ""),
                                      ui.notify("메모 저장됨", type="positive")),
                ).props("flat dense color=primary")
                if r.cert_path:
                    ui.link("📄 인증서 열기", f"/license/cert/{r.id}", new_tab=True)

                async def _resend():
                    pdf = None
                    if r.status == "승인" and r.cert_path and os.path.exists(r.cert_path):
                        try:
                            pdf = open(r.cert_path, "rb").read()
                        except OSError:
                            pdf = None
                    kind = "approval" if r.status == "승인" else "rejection"
                    admin_uid = current_user_id()
                    sn = ui.notification("메일 재발송 중…", spinner=True, timeout=None)
                    try:
                        mok, merr = await run.io_bound(_send_result_mail, r, kind, pdf, admin_uid)
                    finally:
                        sn.dismiss()
                    ui.notify("메일 재발송 완료" if mok else f"재발송 실패: {merr}",
                              type="positive" if mok else "negative")

                ui.button("📧 메일 재발송", on_click=_resend) \
                    .props("flat dense color=primary").classes("q-mt-xs")

            # 삭제(소프트 삭제 · 감사기록 유지) — 대기중/처리완료 모두 가능
            def _open_delete():
                with ui.dialog() as dlg, ui.card().classes("gap-2"):
                    ui.label(f"{doc_id} 삭제").classes("text-subtitle1")
                    ui.label("삭제해도 감사기록(삭제일·삭제자·사유)은 남습니다.").classes(
                        "text-caption text-grey-7"
                    )
                    reason = ui.textarea("삭제 사유(선택)").classes("w-full")
                    with ui.row().classes("justify-end w-full"):
                        ui.button("취소", on_click=dlg.close).props("flat")

                        def _go():
                            service.delete_request(
                                doc_id, reason.value or "", current_username() or ""
                            )
                            dlg.close()
                            state["sel"] = None
                            ui.notify("삭제됨(감사기록 유지)", type="positive")
                            _render_list.refresh()
                            _render_detail.refresh()
                            _stats_panel.refresh()

                        ui.button("삭제", on_click=_go).props("color=negative")
                dlg.open()

            ui.button("🗑 삭제", on_click=_open_delete).props(
                "flat dense color=negative"
            ).classes("q-mt-sm")

    def _set_filter(f: str):
        state["filter"] = f
        _render_list.refresh()

    def _select(doc_id: str):
        state["sel"] = doc_id
        _render_detail.refresh()
        _render_list.refresh()   # 선택 카드 강조 갱신

    _render_list()
    _render_detail()


# ── 파일 서빙 (로그인 필수 · 본인/관리자만) ────────────────────────────────
def _authz(req: LicenseRequest) -> bool:
    uid = current_user_id()
    if req.created_by is not None and req.created_by == uid:
        return True
    return service.is_admin(current_username())


@ng_app.get("/license/po/{req_id}")
def serve_po(req_id: int):
    req = service.get_by_id(req_id)
    if not req or not req.po_path:
        raise HTTPException(404, "PO 파일이 없습니다.")
    if not _authz(req):
        raise HTTPException(403, "권한이 없습니다.")
    path = req.po_path
    if not os.path.exists(path):
        raise HTTPException(404, "PO 파일을 찾을 수 없습니다.")
    # 다운로드 대신 새 탭에서 바로 열람(inline). PDF·이미지는 브라우저가 바로 표시.
    ext = os.path.splitext(path)[1].lower()
    media = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".png": "image/png"}.get(ext, "application/octet-stream")
    return FileResponse(path, media_type=media, headers={"Content-Disposition": "inline"})


@ng_app.get("/license/cert/{req_id}")
def serve_cert(req_id: int):
    req = service.get_by_id(req_id)
    if not req or not req.cert_path:
        raise HTTPException(404, "인증서가 없습니다.")
    if not _authz(req):
        raise HTTPException(403, "권한이 없습니다.")
    path = req.cert_path
    if not os.path.exists(path):
        raise HTTPException(404, "인증서 파일을 찾을 수 없습니다.")
    filename = os.path.basename(path)
    return FileResponse(
        path, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
