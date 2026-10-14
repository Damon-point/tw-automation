"""/whitelist — WhiteList(FOTA 화이트리스트 등록) UI.

기준(OS Update) 테마/레이아웃(theme.page_header, #EDEDED/#4A7C67)을 그대로 쓴다.
역할 기반: 모든 사용자 = 신청·내 신청 / admin = 신청 관리·규칙 관리.
로직은 app.whitelist.service 에 위임(격리 DB whitelist.db).
"""
from __future__ import annotations

from datetime import datetime
from io import BytesIO

from nicegui import run, ui

from app.auth import current_user_id, current_username, is_admin
from app.theme import apply_theme, page_header
from app.user_mailer import user_email
from app.whitelist import mailer as MAIL
from app.whitelist import service as S
from app.whitelist.db import whitelist_session
from app.whitelist.models import WLRequest

MODELS = ["PM451", "PM45", "PM560", "PM67", "PM85", "PM86", "PM90", "PM95"]
OS_LEVELS = [6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]

_STATUS_KO = {"pending": "대기 중", "confirmed": "승인됨", "rejected": "거절됨",
              "deleted": "삭제됨", "handled": "수동 처리(등록 아님)"}


# ── Excel 헬퍼 (openpyxl, 밝은 테마) ────────────────────────
def _template_bytes() -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "단말기_일괄등록"
    hdr = ["SN (10자리)", "Current OS", "Upgrade To", "Model"]
    ws.append(hdr)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="4A7C67")
    ws.append(["1234567890", 9, 11, "PM90"])
    ws.append(["2234567890", "", "", "PM45"])  # 단일 rule 모델 = OS 생략 가능
    for i, w in enumerate([22, 14, 14, 14], start=1):
        ws.column_dimensions[chr(64 + i)].width = w

    ws2 = wb.create_sheet("등록가능_규칙")
    ws2.append(["Model", "From OS", "To OS", "OS Type", "비고"])
    for c in ws2[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="4A7C67")
    from collections import Counter
    rules = S.list_rules()
    cnt = Counter(r["model"] for r in rules)
    for r in rules:
        ws2.append([r["model"], r["from"], r["to"], r["ostype"],
                    "단일 Rule — OS 생략 가능" if cnt[r["model"]] == 1 else ""])
    for i, w in enumerate([14, 12, 12, 22, 24], start=1):
        ws2.column_dimensions[chr(64 + i)].width = w

    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


def _export_bytes(reqs: list[dict]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Requests"
    hdr = ["Request ID", "요청자", "팀", "제출일", "SN", "Android API",
           "Support End Date", "OS Type", "유/무상", "상태", "처리일"]
    ws.append(hdr)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="4A7C67")
    for r in reqs:
        for d in r["devices"]:
            ws.append([r["id"], r["userName"], r["team"], str(r["createdAt"])[:10],
                       d["sn"], d["api"], d["endDate"], d["ostype"],
                       r["chargeType"], _STATUS_KO.get(r["status"], r["status"]),
                       str(r["processedAt"])[:10]])
    for i, w in enumerate([18, 14, 10, 12, 16, 12, 16, 18, 8, 10, 12], start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


def _rules_template_bytes() -> bytes:
    """규칙 일괄 등록 양식 — Category/Model/From/To/OS Type. 현재 규칙을 예시로 채운다."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "규칙"
    hdr = ["Category", "Model", "From OS", "To OS", "OS Type"]
    ws.append(hdr)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="4A7C67")
    # 현재 등록된 규칙(기타)을 예시로. GMS·DQA 예시 행을 위에 얹어 형식을 보여준다.
    #  GMS = '제품명GMS버전'(예: PM95GMS15) · DQA = 'WhiteDQA…' 형식.
    ws.append(["GMS", "PM95", 13, 15, "PM95GMS15"])     # GMS 예시
    ws.append(["DQA", "PM95", 13, 15, "WhiteDQA95V"])   # DQA 예시
    for r in S.list_rules():
        ws.append([r["category"], r["model"], r["from"], r["to"], r["ostype"]])
    for i, w in enumerate([12, 12, 10, 10, 22], start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


def _parse_rules_xlsx(content: bytes) -> list[dict]:
    """규칙 양식 → [{category, model, from, to, ostype}]. 헤더/빈 행은 건너뛴다."""
    from openpyxl import load_workbook

    wb = load_workbook(BytesIO(content), data_only=True, read_only=True)
    ws = wb.active
    out: list[dict] = []
    for row in ws.iter_rows(values_only=True):
        cat = str((row[0] if len(row) > 0 else "") or "").strip()
        model = str((row[1] if len(row) > 1 else "") or "").strip()
        if not model or model.lower() in ("model",) or cat.lower() in ("category",):
            continue
        out.append({
            "category": cat, "model": model,
            "from": row[2] if len(row) > 2 else None,
            "to": row[3] if len(row) > 3 else None,
            "ostype": str((row[4] if len(row) > 4 else "") or "").strip(),
        })
    wb.close()
    return out


def _parse_bulk(content: bytes) -> list[dict]:
    from openpyxl import load_workbook

    wb = load_workbook(BytesIO(content), data_only=True, read_only=True)
    ws = wb.active
    out: list[dict] = []
    for row in ws.iter_rows(values_only=True):
        sn = str((row[0] if len(row) > 0 else "") or "").strip()
        if not sn:
            continue
        low = sn.lower()
        if low.startswith("sn") or "10자리" in sn or "serial" in low:
            continue
        if sn.startswith("※") or sn.startswith("▼") or "WhiteList" in sn or "양식" in sn:
            continue
        cur = row[1] if len(row) > 1 else ""
        upg = row[2] if len(row) > 2 else ""
        model = str((row[3] if len(row) > 3 else "") or "").strip()
        out.append(S.build_device(sn, cur, upg, model))
    wb.close()
    return out


# ── 페이지 ──────────────────────────────────────────────────
# 페이지 함수 안에서는 **무거운 일을 하지 않는다**. 두 가지가 걸린다.
#   · NiceGUI 는 동기 페이지 함수를 이벤트 루프에서 그대로 실행한다(nicegui/page.py).
#     여기서 몇 초를 쓰면 그 시간 내내 접속한 **모든 사람의 화면**이 함께 멈춘다.
#     실측 2026-08-14: 이 페이지가 8.6초 동안 서버를 붙잡아, 평소 21ms 에 답하던 요청이
#     8.6초를 기다렸다.
#   · async 로 바꿔 await 하는 것도 답이 아니다 — 3초(response_timeout) 안에 못 지으면
#     NiceGUI 가 오류 페이지를 내보낸다(실측: 콜드 6초 → HTTP 500).
# 그래서 여기서는 **틀만 짓고**, 내용은 화면이 뜬 뒤 각 패널이 스레드에서 채운다.
@ui.page("/whitelist")
def whitelist_page():
    apply_theme()
    username = current_username() or ""
    uid = current_user_id()
    admin = is_admin()
    # 이메일은 로그인 아이디가 아니라 [계정 관리]에 등록한 발송 이메일에서 가져온다.
    # (아이디는 'admin' 처럼 메일 주소가 아니라, 예전 방식은 항상 빈 값이 됐다.)
    requester = {"username": username, "email": user_email(uid) if uid else "",
                 "name": username, "team": ""}
    staged: list[dict] = []          # 등록 예정 단말
    bulk_results: list[dict] = []    # 엑셀 검증 결과

    from app.pages.whitelist_mail import render_whitelist_mailbox

    panels: dict = {}                # 탭 본문의 refresh 함수(동기화 후 갱신용)

    # WhiteList 는 넓은 폭이 필요 없지만, 메일함 탭이 스레드 본문을 펼치므로 여유를 준다.
    with page_header("WhiteList", max_w="max-w-[96rem]"):
        # 탭 줄 + 동기화 버튼(탭 바깥) — 동기화는 어느 탭에서든 누를 수 있어야 한다.
        with ui.row().classes("w-full items-center no-wrap gap-2"):
            with ui.tabs().props("shrink no-caps") as tabs:
                # 메일함 — 예전에는 /mail 의 한 탭이었다. SN 언급 메일을 스레드로 묶어
                # 등록 요청인지 판별하는 화면이라 WhiteList 안에 있는 게 맞다.
                with ui.tab("메일함", icon="mail") as t_mail:
                    # 숫자는 메일함 본문이 목록을 다 읽은 뒤 알려 준다(아래 set_badge).
                    mail_badge = ui.badge("0", color="red-6").props("floating rounded")
                    mail_badge.set_visibility(False)
                t_reg = ui.tab("신청", icon="add_box")
                # 등록 내역: 대기/승인/거절 절차 없이 '처리(등록) 히스토리'만 관리한다.
                #            모든 사용자가 열람하고, 누가 신청했는지만 표기한다.
                t_list = ui.tab("등록 내역", icon="history")
                t_rules = ui.tab("규칙 관리", icon="rule") if admin else None
                # 고객 포털(외부) 운영용 — 고객사와 그 회사 계정. 관리자 전용.
                t_portal = ui.tab("고객 포털", icon="apartment") if admin else None
            ui.space()
            sync_btn = ui.button("동기화", icon="sync").props("color=primary no-caps")
            sync_btn.tooltip("공용 수신함에서 새 메일 받기")

        def set_badge(n: int) -> None:
            """메일함이 목록을 다 읽으면 그 수를 여기로 알려 준다.

            배지가 목록을 따로 세지 않게 하려는 것이다 — 예전에는 배지와 본문이 같은
            4초짜리 계산을 각각 해서 페이지 한 번 여는 데 8초를 썼다(2026-08-14).
            """
            mail_badge.set_text(str(n))
            mail_badge.set_visibility(bool(n))

        # 탭 본문은 **그 탭을 처음 열 때** 만든다. 예전에는 다섯 탭을 전부 미리 지어
        # 브라우저로 보냈다 — 안 보는 탭까지 짓느라 첫 화면이 그만큼 늦었다.
        lazy: dict[str, object] = {}

        with ui.tab_panels(tabs, value=t_mail).classes("w-full") as tab_panels:
            # ── 메일함(SN 언급 메일 판별) ──────────────────
            # 처음 보이는 탭이라 틀은 지금 짓는다. 목록은 화면이 뜬 뒤 채워진다.
            with ui.tab_panel(t_mail):
                panels["mail"] = render_whitelist_mailbox(on_count=set_badge)
            # ── 신청 ──────────────────────────────────────
            p_reg = ui.tab_panel(t_reg)
            lazy["신청"] = (p_reg, lambda: _render_register(staged, bulk_results, requester, uid))
            # ── 등록 내역(처리 히스토리 · 모든 사용자) ────
            p_list = ui.tab_panel(t_list)
            lazy["등록 내역"] = (p_list, _render_requests)
            if admin:
                p_rules = ui.tab_panel(t_rules)
                lazy["규칙 관리"] = (p_rules, _render_rules)
                p_portal = ui.tab_panel(t_portal)
                lazy["고객 포털"] = (p_portal, _render_portal)

        def _build_tab(e) -> None:
            # 값은 탭 이름(문자열)으로 오지만, 처음 지정한 값은 Tab 객체 그대로일 수 있다.
            v = e.value
            name = v if isinstance(v, str) else (getattr(v, "props", {}) or {}).get("name", "")
            got = lazy.pop(str(name or ""), None)
            if got is None:
                return                       # 이미 지었거나 메일함 탭
            panel, build = got
            with panel:
                build()

        tab_panels.on_value_change(_build_tab)

        _wire_mail_tab(sync_btn, panels)


def _wire_mail_tab(sync_btn, panels: dict) -> None:
    """메일함 탭의 동기화·자동 판별을 붙인다.

    동기화하면 새 메일 받기 → 오래된 메일 정리 → 아직 판정 안 된 스레드 판별(Claude)
    까지 이어서 돈다. 페이지를 열 때도 미판정이 남아 있으면 한 번 돌린다 —
    동기화 버튼을 누르지 않아도, 다른 경로로 들어온 메일도 판별되도록.
    """
    from app.pages.mail_sync import sync_mailbox
    from app.pages.whitelist_mail import auto_triage_new

    async def run_triage(*, notify_none: bool = False) -> None:
        n = ui.notification("WhiteList 판별 중…", spinner=True, timeout=None)
        try:
            res = await run.io_bound(auto_triage_new)
        except Exception as e:  # noqa: BLE001
            n.dismiss()
            ui.notify(f"WhiteList 판별 오류: {e}", color="negative")
            return
        n.dismiss()
        if res.get("blocked"):
            if res.get("pending"):
                ui.notify(
                    f"WhiteList 판별 대기 {res['pending']}건 "
                    f"(예상 ${res['est_cost']:.3f}) — 판별이 꺼져 있습니다.",
                    color="warning", multi_line=True)
        elif res.get("error"):
            ui.notify(f"WhiteList 판별 실패: {res['error']}",
                      color="negative", multi_line=True)
        elif res.get("asked"):
            ui.notify(f"WhiteList {res['asked']}건 판별 완료", color="positive")
        elif notify_none:
            ui.notify("판별할 새 메일이 없습니다.", color="info")
        _refresh_mail(panels)

    async def on_sync() -> None:
        await sync_mailbox(sync_btn, lambda: _refresh_mail(panels))
        await run_triage()

    sync_btn.on_click(on_sync)

    def _pending_count() -> int:
        from app.whitelist import mail_filter as _F
        from app.whitelist import mail_triage as _T

        rows = _F.list_candidates(limit=400)
        return sum(1 for g in _T.group_threads(rows)
                   if _T._cache_id(g) not in _T.cached_verdicts())

    async def _triage_on_load() -> None:
        # 메일 전량을 훑는 계산이다 — 스레드로 넘긴다(이벤트 루프에서 하면 화면이 멈춘다).
        if await run.io_bound(_pending_count):
            await run_triage()

    ui.timer(0.1, _triage_on_load, once=True)


def _refresh_mail(panels: dict) -> None:
    """메일함 본문을 다시 읽는다. 탭 배지는 본문이 다 읽은 뒤 스스로 갱신한다."""
    fn = panels.get("mail")
    if fn is not None:
        fn()


# ── 신청 화면 ───────────────────────────────────────────────
def _take_staged_devices() -> list[dict]:
    """WhiteList 메일함이 넘긴 단말 정보를 **꺼내면서 비운다**(한 번만 채우도록).

    반환: [{"sn", "model", "cur", "upg"}] — 옛 형식(문자열 목록)도 받아준다.
    """
    from nicegui import app as ng_app

    from app.pages.whitelist_mail import STAGED_SN_KEY

    raw = list(ng_app.storage.user.get(STAGED_SN_KEY) or [])
    if raw:
        ng_app.storage.user[STAGED_SN_KEY] = []
    out = []
    for x in raw:
        if isinstance(x, dict):
            out.append({"sn": x.get("sn", ""), "model": x.get("model", ""),
                        "cur": x.get("cur", ""), "upg": x.get("upg", "")})
        elif x:
            out.append({"sn": str(x), "model": "", "cur": "", "upg": ""})
    return [d for d in out if d["sn"]]


def _render_register(staged: list, bulk_results: list, requester: dict,
                     uid: int | None) -> None:
    @ui.refreshable
    def staged_table() -> None:
        ui.label(f"등록 예정 단말기 · {len(staged)}대").classes("text-subtitle2")
        if not staged:
            ui.label("단말기를 추가해주세요.").classes("text-caption").style("color:#888")
            return
        with ui.column().classes("w-full gap-1"):
            for i, d in enumerate(list(staged)):
                with ui.row().classes("items-center w-full").style(
                    "border-bottom:1px solid #e0e0e0;padding:2px 0"
                ):
                    ui.label(d["sn"]).classes("font-mono").style("width:120px")
                    ui.label(f"API {d['api']}").style("width:70px")
                    ui.label(d["endDate"]).style("width:110px")
                    ui.label(d["ostype"]).style("flex:1")
                    ui.button(icon="close", on_click=lambda _, idx=i: (_remove(idx))) \
                        .props("flat dense round color=red")

    def _remove(idx: int) -> None:
        if 0 <= idx < len(staged):
            staged.pop(idx)
        staged_table.refresh()

    def _add_valid(devs: list, note: str = "") -> None:
        existing = {d["sn"] for d in staged}
        added = dupes = 0
        for d in devs:
            if not d["valid"]:
                continue
            if d["sn"] in existing:
                dupes += 1
                continue
            staged.append(d)
            existing.add(d["sn"])
            added += 1
        staged_table.refresh()
        msg = f"{added}대 추가"
        if dupes:
            msg += f" · {dupes}대 중복 제외"
        ui.notify(note + msg, type="positive" if added else "warning")

    # 입력 카드
    with ui.card().classes("w-full").style("background:#fff"):
        ui.label("단말기 입력").classes("text-subtitle1")
        with ui.tabs() as in_tabs:
            it_single = ui.tab("단일 입력")
            it_bulk = ui.tab("엑셀 일괄")
        with ui.tab_panels(in_tabs, value=it_single).classes("w-full"):
            # 단일 입력
            with ui.tab_panel(it_single):
                with ui.row().classes("w-full items-end gap-3"):
                    sn_in = ui.textarea("Serial Number (여러 개 붙여넣기 가능)") \
                        .props("outlined dense").classes("flex-1").style("min-width:220px")
                    # WhiteList 메일함에서 [신청으로 보내기] 한 단말을 채워 둔다.
                    # Claude 가 읽은 모델·OS 를 규칙표로 확정해 규칙에 맞으면 '등록 예정'에 바로 담고,
                    # 확정 못 한 것만 SN 입력란에 남겨 사람이 고르게 한다.
                    _incoming = _take_staged_devices()
                    if _incoming:
                        _ok, _left = [], []
                        for _d in _incoming:
                            _r = S.resolve_os(_d["model"], _d["cur"], _d["upg"])
                            _cur = str(_r["from"]) if _r else _d["cur"]
                            _upg = str(_r["to"]) if _r else _d["upg"]
                            _dev = S.build_device(_d["sn"], _cur, _upg, _d["model"])
                            (_ok if _dev["valid"] else _left).append(
                                _dev if _dev["valid"] else _d["sn"])
                        if _ok:
                            _add_valid(_ok, "메일함에서 ")
                        if _left:
                            sn_in.set_value("\n".join(_left))
                            if _ok:
                                ui.notify(
                                    f"{len(_left)}건은 모델·OS 를 확정하지 못해 직접 선택이 필요합니다.",
                                    type="warning")
                    cur_sel = ui.select(OS_LEVELS, label="Current OS").props("outlined dense") \
                        .style("width:110px")
                    upg_sel = ui.select(OS_LEVELS, label="Upgrade To").props("outlined dense") \
                        .style("width:110px")
                    model_sel = ui.select(MODELS, label="Model").props("outlined dense") \
                        .style("width:120px")
                preview = ui.label().classes("text-caption").style("color:#4A7C67;min-height:18px")

                def update_preview() -> None:
                    m, c, u = model_sel.value, cur_sel.value, upg_sel.value
                    if m and c and u:
                        ot = S.get_ostype(m, c, u)
                        if ot:
                            preview.set_text(f"→ API {S.get_api(c)} · End Date {S.get_end_date()} · OS Type {ot}")
                            preview.style("color:#4A7C67")
                        else:
                            preview.set_text(f"⚠ 규칙 없음 ({m} {c}→{u})")
                            preview.style("color:#c0392b")
                    else:
                        preview.set_text("")

                def on_model() -> None:
                    sr = S.single_rule_for(model_sel.value) if model_sel.value else None
                    if sr:
                        cur_sel.set_value(sr["from"])
                        upg_sel.set_value(sr["to"])
                    update_preview()

                model_sel.on_value_change(lambda _: on_model())
                cur_sel.on_value_change(lambda _: update_preview())
                upg_sel.on_value_change(lambda _: update_preview())

                def add_single() -> None:
                    raw = sn_in.value or ""
                    sns = [s.strip() for s in raw.replace(",", " ").split() if s.strip()]
                    if not sns:
                        ui.notify("SN 을 입력하세요.", type="warning"); return
                    devs = [S.build_device(sn, cur_sel.value, upg_sel.value, model_sel.value) for sn in sns]
                    invalid = [d for d in devs if not d["valid"]]
                    _add_valid(devs)
                    if invalid:
                        ui.notify(f"오류 {len(invalid)}건 제외: "
                                  + ", ".join(f"{d['sn']}({d['errors'][0]})" for d in invalid[:3]),
                                  type="negative")
                    sn_in.set_value("")

                ui.button("목록에 추가", icon="add", on_click=add_single).props("color=primary").classes("mt-2")

            # 엑셀 일괄
            with ui.tab_panel(it_bulk):
                with ui.row().classes("items-center gap-3"):
                    ui.label("열 순서: SN / Current OS / Upgrade To / Model "
                             "(단일 rule 모델은 OS 생략 가능)").classes("text-caption").style("color:#666")
                    ui.button("양식 다운로드", icon="download",
                              on_click=lambda: ui.download(_template_bytes(),
                                                           "WhiteList_일괄등록_양식.xlsx")) \
                        .props("outline color=primary size=sm")

                @ui.refreshable
                def bulk_table() -> None:
                    if not bulk_results:
                        return
                    valid = [r for r in bulk_results if r["valid"]]
                    invalid = [r for r in bulk_results if not r["valid"]]
                    with ui.row().classes("items-center gap-3 mt-2"):
                        ui.label(f"유효 {len(valid)} / 오류 {len(invalid)} (총 {len(bulk_results)})") \
                            .classes("text-subtitle2")
                        ui.button("유효 항목 등록 목록 추가", icon="playlist_add",
                                  on_click=lambda: (_add_valid(bulk_results), bulk_results.clear(),
                                                    bulk_table.refresh())).props("color=primary size=sm")
                        ui.button("초기화", on_click=lambda: (bulk_results.clear(), bulk_table.refresh())) \
                            .props("flat size=sm")
                    rows = [{"sn": r["sn"], "cur": r["cur"], "upg": r["upg"], "model": r["model"],
                             "api": r["api"], "ostype": r["ostype"] or "-",
                             "st": "OK" if r["valid"] else (r["errors"][0] if r["errors"] else "오류")}
                            for r in bulk_results]
                    ui.table(columns=[
                        {"name": "sn", "label": "SN", "field": "sn", "align": "left"},
                        {"name": "cur", "label": "Cur", "field": "cur"},
                        {"name": "upg", "label": "Upg", "field": "upg"},
                        {"name": "model", "label": "Model", "field": "model", "align": "left"},
                        {"name": "api", "label": "API", "field": "api"},
                        {"name": "ostype", "label": "OS Type", "field": "ostype", "align": "left"},
                        {"name": "st", "label": "상태", "field": "st", "align": "left"},
                    ], rows=rows, row_key="sn").classes("w-full").props("dense")

                async def on_upload(e) -> None:
                    try:
                        data = await e.file.read()
                        bulk_results.clear()
                        bulk_results.extend(_parse_bulk(data))
                        if not bulk_results:
                            ui.notify("데이터를 찾을 수 없습니다. 양식을 확인하세요.", type="warning")
                        bulk_table.refresh()
                    except Exception as ex:  # noqa: BLE001
                        ui.notify(f"파일 읽기 오류: {ex}", type="negative")

                ui.upload(on_upload=on_upload, auto_upload=True, max_files=1) \
                    .props("accept=.xlsx flat").classes("w-full mt-2")
                bulk_table()

    # 등록 목록 + 제출
    with ui.card().classes("w-full").style("background:#fff"):
        staged_table()

    with ui.card().classes("w-full").style("background:#fff"):
        ui.label("유/무상 구분").classes("text-subtitle2")
        charge = ui.radio({"유상": "유상", "무상": "무상"}, value="무상").props("inline")
        po_hold = {"name": "", "data": None}

        with ui.column().classes("w-full gap-2") as po_section:
            ui.label("PO 파일 등록 (유상)").classes("text-caption").style("color:#666")

            async def on_po_upload(e) -> None:
                po_hold["name"] = e.file.name
                po_hold["data"] = await e.file.read()
                ui.notify(f"PO 파일 첨부: {e.file.name}", type="positive")

            ui.upload(on_upload=on_po_upload, auto_upload=True, max_files=1) \
                .props("flat").classes("w-full")
        free_in = ui.textarea("무상 사유").props("outlined dense").classes("w-full")

        memo_in = ui.textarea("메모 (선택)").props("outlined dense").classes("w-full")

        def apply_charge() -> None:
            paid = charge.value == "유상"
            po_section.set_visibility(paid)
            free_in.set_visibility(not paid)

        charge.on_value_change(lambda _: apply_charge())
        apply_charge()

        async def submit() -> None:
            if not staged:
                ui.notify("등록할 단말기를 추가하세요.", type="warning"); return
            if not charge.value:
                ui.notify("유/무상을 선택하세요.", type="warning"); return
            paid = charge.value == "유상"
            try:
                req = S.submit_request(
                    requester=requester, devices=list(staged),
                    memo=(memo_in.value or "").strip(), charge_type=charge.value,
                    free_reason="" if paid else (free_in.value or "").strip(),
                )
            except S.DuplicateSerialError as ex:
                # 같은 단말을 두 번 등록하는 것을 막는다(메일함을 거치지 않은 직접 신청 포함).
                lines = [f"· {sn} — {v['request_id']} ({v['created_at']}, "
                         f"{_STATUS_KO.get(v['status'], v['status'])})"
                         for sn, v in list(ex.duplicates.items())[:8]]
                ui.notify(
                    "이미 신청된 단말이 있어 저장하지 않았습니다.\n" + "\n".join(lines),
                    type="negative", multi_line=True, timeout=0, close_button="닫기",
                )
                return
            if paid and po_hold["data"]:
                try:
                    path = S.save_po_file(req["id"], po_hold["name"], po_hold["data"])
                    S.attach_po(req["id"], path)
                except Exception as ex:  # noqa: BLE001
                    ui.notify(f"PO 파일 저장 실패: {ex}", type="negative")
            # 관리자 알림은 SMTP(수 초) 이므로 UI 를 막지 않도록 io_bound 로.
            try:
                mail_ok, mail_msg = await run.io_bound(MAIL.send_new_request, req, uid)
            except Exception as ex:  # noqa: BLE001
                mail_ok, mail_msg = False, str(ex)
            staged.clear()
            staged_table.refresh()
            memo_in.set_value("")
            free_in.set_value("")
            po_hold["name"] = ""; po_hold["data"] = None
            note = f"신청 완료 · {req['id']} ({len(req['devices'])}대)"
            if not mail_ok:
                note += f" · 관리자 알림 메일 스킵({mail_msg})"
            ui.notify(note, type="positive")

        ui.button("Request 제출", icon="send", on_click=submit).props("color=primary")


# ── 등록 내역(처리 히스토리 · 모든 사용자 열람) ──────────────
def _render_requests() -> None:
    # 대기/승인/거절 절차가 없으므로 상태 필터·통계 없이 '등록 히스토리'만 보여준다.
    state = {"kw": ""}

    @ui.refreshable
    def body() -> None:
        # 삭제(soft delete) 건은 히스토리에서 뺀다.
        allr = [r for r in S.list_requests() if r["status"] != "deleted"]
        f = allr
        if state["kw"]:
            k = state["kw"].lower()
            f = [r for r in f if k in (r["userName"] or "").lower() or k in r["id"].lower()
                 or any(k in d["sn"].lower() for d in r["devices"])]

        with ui.row().classes("items-center gap-3 w-full"):
            with ui.card().style("background:#fff;min-width:120px"):
                ui.label(str(len(allr))).classes("text-h6").style("color:#4A7C67")
                ui.label("총 등록").classes("text-caption")
            ui.input("검색 (SN·요청자·ID)", value=state["kw"],
                     on_change=lambda e: state.update(kw=e.value)) \
                .props("outlined dense clearable").classes("flex-1") \
                .on("keydown.enter", body.refresh)
            ui.button("검색", icon="search", on_click=body.refresh).props("color=primary size=sm")
            ui.button("내보내기 Excel", icon="download",
                      on_click=lambda: ui.download(_export_bytes(f),
                                                   f"WhiteList_등록내역_{datetime.now():%Y%m%d}.xlsx")) \
                .props("outline color=primary size=sm")

        ui.label(f"등록 {len(f)}건").classes("text-caption").style("color:#666")
        if not f:
            ui.label("등록 내역이 없습니다.").classes("text-caption").style("color:#888")
            return
        comp_opts, comp_cur = _company_context()
        for r in f:
            _request_expansion(r, comp_opts, comp_cur)

    body()


# ── 등록 1건 = 펼침 카드 (처리 히스토리 · 열람 전용) ──────────
def _request_expansion(r: dict, comp_opts: dict | None = None,
                       comp_cur: dict | None = None) -> None:
    title = f"{r['id']}  ·  {r['userName'] or '-'}  ·  {str(r['createdAt'])[:10]}  ·  {len(r['devices'])}대"
    with ui.expansion(title).classes("w-full").style(
        "background:#fff;border:1px solid #e0e0e0;border-radius:6px"
    ):
        with ui.row().classes("items-center gap-2"):
            # 수동 처리(등록 아님) 건은 실제 등록과 헷갈리지 않게 회색 배지로 구분.
            if r["status"] == "handled":
                ui.badge("수동 처리 · 등록 아님", color="grey-6") \
                    .tooltip("메일함에서 등록 없이 '처리 완료(수동)'로 표시한 건입니다.")
            if r.get("category"):
                ui.badge(r["category"], color="blue-grey-6")
            if r["chargeType"]:
                ui.badge(r["chargeType"], color="amber" if r["chargeType"] == "유상" else "teal")
            if r["fotaStatus"]:
                ui.label(f"FOTA: {r['fotaStatus']}").classes("text-caption").style("color:#666")
        _company_picker(r["id"], comp_opts or {}, comp_cur or {})
        if r["memo"]:
            ui.label(f"메모: {r['memo']}").classes("text-caption").style("color:#666")
        # 유/무상 상세
        if r["chargeType"] == "유상":
            with ui.row().classes("items-center gap-3 text-caption").style("color:#666"):
                if r["amount"]:
                    ui.label(f"금액: {r['amount']}")
                if r["poNumber"]:
                    ui.label(f"PO 번호: {r['poNumber']}")
                if r.get("poFilePath"):
                    import os as _os
                    ui.button("PO 파일", icon="description",
                              on_click=lambda p=r["poFilePath"]: ui.download.file(p)) \
                        .props("outline color=primary size=sm") \
                        .tooltip(_os.path.basename(r["poFilePath"]))
        elif r["chargeType"] == "무상" and r["freeReason"]:
            ui.label(f"무상 사유: {r['freeReason']}").classes("text-caption").style("color:#666")

        rows = [{"sn": d["sn"], "api": d["api"], "endDate": d["endDate"],
                 "ostype": d["ostype"], "model": d["model"]} for d in r["devices"]]
        ui.table(columns=[
            {"name": "sn", "label": "SN", "field": "sn", "align": "left"},
            {"name": "api", "label": "Android API", "field": "api"},
            {"name": "endDate", "label": "Support End Date", "field": "endDate"},
            {"name": "ostype", "label": "OS Type", "field": "ostype", "align": "left"},
            {"name": "model", "label": "Model", "field": "model", "align": "left"},
        ], rows=rows, row_key="sn").classes("w-full").props("dense")


def _company_context() -> tuple[dict, dict]:
    """(선택 옵션, {요청 id: 고객사 id}) — 목록을 그리기 전에 **한 번만** 읽는다.

    행마다 조회하면 등록 내역 19건에 40번 가까이 DB 를 친다.
    """
    from app.whitelist import portal as PT

    comps = PT.list_companies()
    if not comps:
        return {}, {}
    opts = {0: "— 지정 안 함 —", **{c["id"]: c["name"] for c in comps}}
    with whitelist_session() as db:
        cur = dict(db.query(WLRequest.id, WLRequest.company_id)
                   .filter(WLRequest.company_id.isnot(None)).all())
    return opts, cur


def _company_picker(request_id: str, opts: dict, cur: dict) -> None:
    """이 요청이 어느 고객사 건인지 지정한다.

    지정하지 않으면 고객 화면에 나타나지 않는다 — 고객 입장에선 '요청했는데 아무것도
    없다' 가 되므로, 처리 이력을 보는 이 자리에서 바로 정할 수 있게 둔다.
    """
    from app.whitelist import portal as PT

    if not opts:
        return
    with ui.row().classes("items-center gap-2 no-wrap"):
        ui.label("고객사").classes("text-caption").style("color:#666")
        sel = ui.select(opts, value=cur.get(request_id, 0)) \
            .props("outlined dense options-dense").style("min-width:14rem")

        def save(e) -> None:
            ok, msg = PT.set_request_company(request_id, int(e.value or 0) or None)
            ui.notify(msg, color="positive" if ok else "negative")

        sel.on_value_change(save)


# ── 관리자: 고객 포털(고객사 + 그 회사 계정) ────────────────
# 회사와 그 회사 계정은 따로 볼 일이 없다. 한 화면에서 회사를 펼치면 그 안에 계정이
# 있고, 발급할 때 소속을 다시 고르지 않아도 된다.
# 코드와 구분(최종고객/파트너사)은 두지 않는다 — 요청 귀속은 '요청한 주체' 하나로 정해진다.
_ACCT_COLOR = {"active": "green-7", "invited": "blue-grey-5", "suspended": "grey-6"}


def _show_password(title: str, username: str, pw: str) -> None:
    """발급·초기화 결과 안내. 초기 비밀번호는 고정값이라 이 창을 닫아도 확인할 수 있다."""
    with ui.dialog() as dlg, ui.card().classes("w-[28rem]"):
        ui.label(title).classes("text-subtitle1")
        ui.label("아이디와 초기 비밀번호를 고객에게 전달해 주세요.") \
            .classes("text-caption").style("color:#666")
        with ui.row().classes("items-center gap-2 w-full no-wrap"):
            ui.label(f"{username} /").classes("text-body2")
            ui.label(pw).classes("text-h6").style("font-family:monospace")
            ui.button(icon="content_copy",
                      on_click=lambda: (ui.clipboard.write(pw),
                                        ui.notify("복사했습니다.", color="positive"))) \
                .props("flat dense round size=sm")
        ui.button("닫기", on_click=dlg.close).props("flat")
    dlg.open()


def _render_portal() -> None:
    from app.whitelist import portal as PT

    @ui.refreshable
    def body() -> None:
        comps = PT.list_companies()
        accounts = PT.list_accounts()
        by_comp: dict = {}
        for a in accounts:
            by_comp.setdefault(a["company_id"], []).append(a)

        with ui.card().classes("w-full").style("background:#fff"):
            ui.label("고객사 추가").classes("text-subtitle2")
            with ui.row().classes("items-end gap-3 w-full no-wrap"):
                name = ui.input("이름", placeholder="Guard1") \
                    .props("outlined dense").classes("flex-1")
                dom = ui.input("이메일 도메인 (선택)", placeholder="guard1.com") \
                    .props("outlined dense").classes("flex-1")

                def add() -> None:
                    ok, msg = PT.add_company(name.value or "", dom.value or "")
                    ui.notify(msg, color="positive" if ok else "negative")
                    if ok:
                        body.refresh()

                ui.button("추가", icon="add", on_click=add).props("color=primary")
            ui.label("도메인은 비워 둘 수 있습니다 — 개인 메일로 요청하는 고객이 있습니다.") \
                .classes("text-caption").style("color:#888")

        ui.label(f"고객사 {len(comps)}곳 · 계정 {len(accounts)}개") \
            .classes("text-caption").style("color:#666")
        if not comps:
            ui.label("등록된 고객사가 없습니다.").classes("text-caption").style("color:#888")
            return

        for c in comps:
            _company_card(c, by_comp.get(c["id"], []), body.refresh)

    body()


def _company_card(c: dict, accts: list[dict], refresh) -> None:
    """고객사 1곳 = 펼침 카드. 펼치면 그 회사 계정과 편집이 들어 있다."""
    from app.whitelist import portal as PT

    exp = ui.expansion().classes("w-full").style(
        "background:#fff;border:1px solid #e0e0e0;border-radius:6px"
    )
    with exp.add_slot("header"):
        with ui.row().classes("w-full items-center gap-3 no-wrap"):
            ui.label(c["name"]).classes("text-subtitle2").style("min-width:11rem")
            ui.label(c["email_domain"] or "— 도메인 없음").classes("text-caption") \
                .style("color:#666;min-width:11rem;font-family:monospace")
            ui.label(f"계정 {c['accounts']}").classes("text-caption").style("color:#666")
            ui.label(f"연결된 요청 {c['requests']}").classes("text-caption") \
                .style("color:#666")
            ui.space()
            if not c["active"]:
                ui.badge("비활성", color="grey-6")

    with exp:
        # ── 계정 ────────────────────────────────────────────
        ui.label("계정").classes("text-subtitle2")
        if not accts:
            ui.label("발급된 계정이 없습니다.").classes("text-caption").style("color:#888")
        for a in accts:
            with ui.row().classes("w-full items-center gap-3 no-wrap").style(
                "border-top:1px solid #eceeea;padding:6px 0"
            ):
                ui.label(a["username"]).classes("text-body2") \
                    .style("min-width:9rem;font-family:monospace")
                ui.label(a["email"] or "—").classes("text-caption") \
                    .style("color:#666;min-width:14rem;font-family:monospace")
                _last = str(a["last_login_at"])[:16] if a["last_login_at"] else "없음"
                ui.label(f"최근 로그인 {_last}").classes("text-caption").style("color:#666")
                ui.space()
                if a["must_change"]:
                    # 초기 비밀번호(1234) 그대로다 — 담당자도 아는 값이라 눈에 띄게 표시한다.
                    ui.badge("초기 비밀번호", color="orange-8") \
                        .tooltip("고객이 아직 비밀번호를 바꾸지 않았습니다")
                ui.badge(PT.status_label(a["status"]),
                         color=_ACCT_COLOR.get(a["status"], "grey-6"))

                def reset(aid=a["id"], nm=a["username"]) -> None:
                    ok, msg, pw = PT.reset_password(aid)
                    ui.notify(msg, color="positive" if ok else "negative")
                    if ok:
                        _show_password("비밀번호 초기화", nm, pw)
                        refresh()

                ui.button("비밀번호 초기화", on_click=reset) \
                    .props("flat dense size=sm no-caps")

                def flip(aid=a["id"], cur=a["status"]) -> None:
                    ok, msg = PT.set_account_status(
                        aid, "active" if cur == "suspended" else "suspended")
                    ui.notify(msg, color="positive" if ok else "negative")
                    refresh()

                ui.button("다시 활성화" if a["status"] == "suspended" else "중지",
                          on_click=flip).props("flat dense size=sm no-caps")

                def drop_acct(aid=a["id"]) -> None:
                    ok, msg = PT.delete_account(aid)
                    ui.notify(msg, color="positive" if ok else "negative")
                    if ok:
                        refresh()

                ui.button(icon="delete_outline", on_click=drop_acct) \
                    .props("flat dense round size=sm color=grey-7").tooltip("계정 삭제")

        # ── 계정 발급 (소속은 이 회사로 고정) ────────────────
        with ui.row().classes("items-end gap-3 w-full no-wrap q-mt-sm"):
            uid_in = ui.input("아이디", placeholder="guard1") \
                .props("outlined dense").classes("flex-1")
            mail_in = ui.input("이메일 (선택)", placeholder="whitelist@guard1.com") \
                .props("outlined dense").classes("flex-1")

            def issue(cid=c["id"], uid_in=uid_in, mail_in=mail_in) -> None:
                ok, msg, pw = PT.add_account(cid, uid_in.value or "", mail_in.value or "")
                ui.notify(msg, color="positive" if ok else "negative")
                if ok:
                    _show_password("계정 발급 완료", (uid_in.value or "").lower(), pw)
                    refresh()

            ui.button("계정 발급", icon="person_add", on_click=issue).props("color=primary")
        from app.whitelist import portal as _PT
        ui.label(f"초기 비밀번호는 '{_PT.INITIAL_PASSWORD}' 입니다. "
                 "고객이 로그인한 뒤 바꾸도록 안내해 주세요.") \
            .classes("text-caption").style("color:#888")

        # ── 고객사 편집 ─────────────────────────────────────
        ui.separator()
        with ui.row().classes("items-end gap-3 w-full no-wrap"):
            en = ui.input("이름", value=c["name"]).props("outlined dense").classes("flex-1")
            ed = ui.input("이메일 도메인", value=c["email_domain"]) \
                .props("outlined dense").classes("flex-1")

            def save(cid=c["id"], en=en, ed=ed) -> None:
                ok, msg = PT.update_company(cid, name=en.value, email_domain=ed.value or "")
                ui.notify(msg, color="positive" if ok else "negative")
                refresh()

            ui.button("저장", icon="save", on_click=save).props("color=primary")

            def toggle(cid=c["id"], cur=c["active"]) -> None:
                ok, msg = PT.update_company(cid, active=not cur)
                ui.notify(msg, color="positive" if ok else "negative")
                refresh()

            ui.button("비활성으로" if c["active"] else "활성으로", on_click=toggle) \
                .props("outline dense no-caps")

            def drop(cid=c["id"]) -> None:
                ok, msg = PT.delete_company(cid)
                ui.notify(msg, color="positive" if ok else "negative", multi_line=True)
                if ok:
                    refresh()

            ui.button(icon="delete_outline", on_click=drop) \
                .props("flat dense round color=grey-7") \
                .tooltip("고객사 삭제 (소속 계정이 있으면 지워지지 않습니다)")


# ── 관리자: 규칙 관리 ───────────────────────────────────────
def _render_rules() -> None:
    state = {"cat": ""}
    _CAT_COLOR = {"GMS": "teal", "DQA": "deep-orange", "KT": "indigo",
                  "LG": "pink", "기타": "grey"}

    @ui.refreshable
    def body() -> None:
        rules = S.list_rules(state["cat"])
        with ui.card().classes("w-full").style("background:#fff"):
            ui.label("새 규칙 추가").classes("text-subtitle2")
            with ui.row().classes("items-end gap-3"):
                c = ui.select(S.CATEGORIES, value="기타", label="구분") \
                    .props("outlined dense").style("width:110px")
                m = ui.select(MODELS, label="Model", new_value_mode="add-unique") \
                    .props("outlined dense").style("width:120px")
                f = ui.select(OS_LEVELS, label="From OS").props("outlined dense").style("width:100px")
                t = ui.select(OS_LEVELS, label="To OS").props("outlined dense").style("width:100px")
                o = ui.input("OS Type").props("outlined dense").style("width:180px")

                def add() -> None:
                    ok, msg = S.add_rule(m.value, f.value, t.value, o.value, c.value)
                    ui.notify("추가됨" if ok else msg, type="positive" if ok else "negative")
                    if ok:
                        body.refresh()

                ui.button("추가", icon="add", on_click=add).props("color=primary")

        # 표 일괄 등록(양식 다운로드 → 채워서 업로드) — GMS/DQA 규칙을 한 번에.
        with ui.card().classes("w-full").style("background:#fff"):
            with ui.row().classes("items-center gap-3"):
                ui.label("표 일괄 등록").classes("text-subtitle2")
                ui.button("양식 다운로드", icon="download",
                          on_click=lambda: ui.download(_rules_template_bytes(),
                                                       "WhiteList_규칙_양식.xlsx")) \
                    .props("outline color=primary size=sm")
            ui.label("열 순서: Category / Model / From OS / To OS / OS Type "
                     "(같은 Model·버전·구분이면 OS Type 갱신)").classes(
                "text-caption").style("color:#666")

            async def on_rules_upload(e) -> None:
                try:
                    rows = _parse_rules_xlsx(await e.file.read())
                except Exception as ex:  # noqa: BLE001
                    ui.notify(f"파일 읽기 오류: {ex}", type="negative"); return
                res = S.bulk_import_rules(rows)
                msg = f"일괄 등록 · 추가 {res['added']} · 갱신 {res['updated']} · 유지 {res['skipped']}"
                if res["errors"]:
                    msg += f" · 오류 {len(res['errors'])}건"
                ui.notify(msg, type="positive" if not res["errors"] else "warning",
                          multi_line=True)
                body.refresh()

            ui.upload(on_upload=on_rules_upload, auto_upload=True, max_files=1) \
                .props("accept=.xlsx flat").classes("w-full")

        with ui.card().classes("w-full").style("background:#fff"):
            with ui.row().classes("items-center gap-3 w-full"):
                ui.label(f"등록된 규칙 · {len(rules)}개").classes("text-subtitle2")
                ui.select({"": "전체", **{c: c for c in S.CATEGORIES}}, value=state["cat"],
                          label="구분 필터",
                          on_change=lambda e: (state.update(cat=e.value), body.refresh())) \
                    .props("outlined dense").style("width:130px")
            for r in rules:
                with ui.row().classes("items-center w-full").style(
                    "border-bottom:1px solid #eee;padding:3px 0"
                ):
                    ui.badge(r["category"],
                             color=_CAT_COLOR.get(r["category"], "grey")).style("width:52px")
                    ui.label(r["model"]).style("width:80px")
                    ui.label(f"Android {r['from']} → {r['to']}").style("width:160px")
                    ui.label(f"API {S.get_api(r['from'])}").style("width:70px")
                    ui.label(r["ostype"]).style("flex:1;color:#4A7C67")
                    ui.button(icon="delete",
                              on_click=lambda _, rid=r["id"]: (
                                  S.delete_rule(rid), ui.notify("삭제됨"), body.refresh())) \
                        .props("flat dense round color=red")

    body()
