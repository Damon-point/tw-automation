"""OS Update 작업 (/osupdate) — 등록된 제품이 그대로 작업 항목이 된다.

[제품 관리](/products) 에 등록한 제품(제품명 + Android + Type) 하나가 여기서 **항목 하나**다.
배포 메일이 와야 줄이 생기는 메일함과 달리, 이 목록은 **제품을 등록하는 순간** 만들어진다.

항목 하나 안에서:
  · 최신 Release Note 바로 열람·다운로드
  · **Release 버전 목록 = 작업 목록** — 버전 하나가 곧 작업 하나다.

등록해 둔 경로는 여기서 다루지 않는다 — [제품 관리] 한 곳에서만 본다.

버전 줄에 그 회차의 모든 것이 모인다: 개발 Release Note 문서, 트리거 메일(근거·승인일),
단계 진행 상황, 만들어진 결과물. 펼치면 [할 일] · [결과물] 두 탭이고,
아직 시작하지 않은 버전은 [할 일] 에 무엇을 하게 되는지와 시작 버튼이 나온다.

갈래(Submission/Approval) 구성과 검수 흐름은 예전 메일함 보드와 똑같다 — 같은 화면 코드
(task_board.render_side)를 단계 저장소만 바꿔 쓴다. 다른 점은 작업의 출발점이
메일이 아니라 제품·버전이라는 것뿐이다.
"""
from __future__ import annotations

from urllib.parse import quote

from nicegui import run, ui

from app import os_products as P
from app import os_update_jobs as JOBS
from app.auth import current_user_id, current_username
from app.pages import mail_detail as MD
from app.pages.mail_sync import sync_mailbox
from app.stages import SIDE_APPROVAL, SIDE_SUBMISSION, stage_label
from app.task_store import JobStageStore
from app.theme import apply_theme, page_header


# 페이지 함수 안에서는 무거운 일을 하지 않는다 — whitelist_page 위의 설명과 같은 이유다.
# 틀만 짓고, 목록은 화면이 뜬 뒤 스레드에서 읽어 채운다.
@ui.page("/osupdate")
def os_update_page():
    apply_theme()
    with page_header("작업 History", max_w="max-w-[104rem]"):
        _render()


def _load(q: str) -> tuple[list[dict], dict]:
    """목록과 제품별 '신규 회차'를 **한 번에** 구한다. 스레드에서 부를 것.

    제품 하나당 감지 조회가 붙어 37개면 1초에 가깝다(실측 2026-08-14). 화면을 그리며
    하나씩 부르면 그 시간 내내 이벤트 루프가 막힌다.
    version_rows 도 여기서 미리 불러 캐시를 채운다 — 아래 _versions 는 그 캐시를 쓴다.
    """
    rows = P.list_products(q)
    pend = {r["id"]: JOBS.pending_updates(r) for r in rows}
    for r in rows:
        JOBS.version_rows(r)
    return rows, pend


def _render() -> None:
    ui.label("작업 History").classes("text-h6")

    state = {"q": ""}

    # items 는 **틀만** 만든다. 목록 읽기는 아래 load 가 스레드에서 한다 —
    # 여기서 기다리면 화면 짓는 자리를 붙잡아 NiceGUI 가 3초 만에 오류 페이지를 낸다.
    @ui.refreshable
    def items() -> None:
        holder = ui.column().classes("w-full")
        with holder:
            with ui.row().classes("items-center gap-2 q-mt-sm"):
                ui.spinner(size="sm")
                ui.label("목록을 읽는 중…").classes("text-caption text-grey-7")

        async def load() -> None:
            try:
                rows, pend = await run.io_bound(_load, state["q"])
            except Exception as e:  # noqa: BLE001
                holder.clear()
                with holder:
                    ui.label(f"목록을 읽지 못했습니다: {e}").classes("text-negative")
                return
            holder.clear()
            with holder:
                if not rows:
                    _empty(bool(state["q"]))
                    return
                ui.label(f"항목 {len(rows)}개").classes("text-caption text-grey-7")
                # 제품군으로 묶어 접었다 편다(제품 관리와 같은 순서·같은 구분).
                # 손볼 게 있는 제품군(신규 회차 있음)은 펼쳐 둔다 — 접혀 있으면 못 보고 지나친다.
                for cat, group in P.group_by_category(rows):
                    n_new = sum(1 for x in group if pend.get(x["id"]))
                    with ui.expansion(value=bool(n_new) or bool(state["q"])) \
                            .classes("w-full").props("dense expand-separator") as exp:
                        with exp.add_slot("header"):
                            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                                ui.label(cat).classes("text-subtitle2") \
                                    .style("font-weight:700")
                                ui.label(f"{len(group)}개") \
                                    .classes("text-caption text-grey-7")
                                if n_new:
                                    ui.badge(f"신규 {n_new}", color="red-6").tooltip(
                                        "아직 시작하지 않은 회차가 있는 제품 수")
                        if not group:
                            ui.label("이 제품군에 등록된 제품이 없습니다.") \
                                .classes("text-caption text-grey-5")
                            continue
                        for r in group:
                            _item(r, pend.get(r["id"]))

        # 화면이 브라우저에 뜬 **뒤** 채운다(ui.timer 는 접속이 붙은 다음 돈다).
        ui.timer(0.01, load, once=True)

    def refresh_all() -> None:
        items.refresh()

    with ui.row().classes("w-full items-center no-wrap gap-2"):
        def _on_search(e) -> None:
            state["q"] = (e.value or "").strip()
            items.refresh()

        ui.input(placeholder="제품명 · Android · Type 검색", on_change=_on_search) \
            .props("dense outlined clearable").classes("w-80")

        ui.space()
        ui.button("제품 관리", icon="inventory_2",
                  on_click=lambda: ui.navigate.to("/products")) \
            .props("outline no-caps")
        _sync_btn = ui.button("동기화", icon="sync").props("color=primary no-caps")
        _sync_btn.tooltip("공용 수신함에서 새 메일 받기")
        _sync_btn.on_click(lambda: sync_mailbox(_sync_btn, refresh_all))
        ui.button(icon="refresh", on_click=refresh_all) \
            .props("flat dense").tooltip("목록 새로 읽기")

    items()         # 틀을 지금 만들어 둔다 — 한 번도 부르지 않으면 refresh 가 안 먹는다


def _empty(filtered: bool) -> None:
    with ui.card().classes("w-full items-start"):
        if filtered:
            ui.label("조건에 맞는 항목이 없습니다.").classes("text-grey-7")
            return
        ui.label("작업 항목이 없습니다.").classes("text-grey-7")
        ui.label(
            "[제품 관리]에서 제품을 등록하면 이 목록에 항목으로 나타납니다."
        ).classes("text-caption text-grey-6")
        ui.button("제품 등록하러 가기", icon="add",
                  on_click=lambda: ui.navigate.to("/products")).props("color=primary no-caps")


def sku_text(r: dict) -> str:
    """'PM95 A15 GMS' — 제품을 이루는 세 값을 한 덩어리로 쓴다.

    Others 처럼 OS 입력이 없는 제품은 제품명만 남는다(빈 칸이 붙지 않는다).
    신규 작업 화면도 같은 표기를 써야 해서 여기 한 곳에 둔다.
    """
    return " ".join(x for x in (
        r["product"],
        f"A{r['android']}" if r.get("android") else "",
        r.get("os_type") or "",
    ) if x)


def _item(r: dict, ups0: list | None = None) -> None:
    """제품 하나 = 작업 항목 하나.

    ups0: 목록이 스레드에서 미리 구해 둔 '신규 회차'. 없으면 여기서 구한다.
    """
    # 손볼 게 있는 제품(신규 회차)만 펼쳐 둔다 — 나머지는 접어서 목록을 짧게 유지한다.
    if ups0 is None:
        ups0 = JOBS.pending_updates(r)
    open_first = bool(ups0)

    with ui.card().classes("w-full nicegui-card-tight").style(
        "border-left:5px solid #4A7C67"
    ):
        card = ui.expansion(value=open_first).classes("w-full").props(
            "dense hide-expand-icon")
        with card.add_slot("header"):
            # 제품명·Android·Type 은 SKU 를 이루는 한 덩어리라 같은 서식으로 나란히 쓴다.
            with ui.row().classes("w-full items-center gap-3 no-wrap"):
                ui.icon("expand_more", size="sm").classes("text-grey-6").style(
                    "width:20px; flex:none")

                # 아직 시작하지 않은 신규 버전이 있으면 제품명 왼쪽에 점 하나로만 알린다.
                @ui.refreshable
                def alert_badge(first: list | None = ups0) -> None:
                    # 첫 렌더는 위에서 이미 계산한 값을 쓴다(같은 조회를 두 번 하지 않는다).
                    ups = first if first is not None else JOBS.pending_updates(r)
                    if not ups:
                        ui.element("div").style("width:8px; height:8px; flex:none")
                        return
                    # 배포 공지로만 잡힌 건은 승인 여부를 알 수 없어 따로 센다.
                    _dep = [u for u in ups if u.get("source") == "deploy"]
                    _fih = [u for u in ups if u.get("source") != "deploy"]
                    n_ok = sum(1 for u in _fih if u["approved"])
                    _tip = [f"신규 {len(ups)}건 — 아직 시작하지 않았습니다"]
                    if _fih:
                        _tip.append(f"트리거 메일: 승인 완료 {n_ok} · 승인 대기 "
                                    f"{len(_fih) - n_ok}")
                    if _dep:
                        _tip.append(f"배포 공지만 있음(트리거 메일 없음): {len(_dep)}건")
                    ui.element("div").style(
                        "width:8px; height:8px; flex:none; border-radius:50%;"
                        " background:#b3261e"
                    ).tooltip(" · ".join(_tip))

                alert_badge()
                ui.label(sku_text(r)).classes("text-subtitle1").style("font-weight:700")
                ui.space()
                ui.button(icon="edit",
                          on_click=lambda pid=r["id"]: ui.navigate.to("/products")) \
                    .props("flat dense size=sm").tooltip("제품 관리에서 수정")

        with card:
            # ── 버전 = 작업 ─────────────────────────────────────────
            # (등록 경로는 [제품 관리]에서만 다룬다 — 여기선 안 보여준다.)
            #
            # 접혀 있는 제품은 **펼칠 때** 만든다. 예전에는 37개 제품의 버전·단계 표를
            # 전부 미리 지어 브라우저로 보냈다 — 화면 하나가 2.5 MB 였고, 그걸 짓는
            # 동안 서버가 멈췄다(실측 2026-08-14). 보이지도 않는 것을 짓느라 그랬다.
            if open_first:
                _versions(r, alert_badge.refresh)
            else:
                holder = ui.column().classes("w-full gap-0")
                built = {"done": False}

                def _fill(e, _h=holder, _b=built) -> None:
                    if _b["done"] or not e.value:
                        return
                    _b["done"] = True
                    with _h:
                        _versions(r, alert_badge.refresh)

                card.on_value_change(_fill)


def _add_version(r: dict, on_change) -> None:
    """버전을 직접 넣어 작업을 만든다 — **예외 회차용**(회의 2026-08-04 결정).

    배포 공지도 트리거 메일도 없이 나가는 건(비GMS·인증 미대상·긴급 생산 투입 등)은
    자동 감지에 걸리지 않는다. 그런 회차를 사람이 시작할 수 있어야 목록에서 빠지지 않는다.
    """
    sku = " ".join(x for x in (r["product"], f"A{r['android']}" if r["android"] else "",
                               r.get("os_type") or "") if x)
    with ui.dialog() as dlg, ui.card().classes("w-[26rem]"):
        ui.label(f"{sku} — 버전 직접 추가").classes("text-subtitle1")
        ui.label(
            "배포 공지·트리거 메일 없이 나가는 회차를 시작합니다. "
            "버전은 문서·업로드 표기에 그대로 쓰입니다."
        ).classes("text-caption text-grey-7")
        ver = ui.input("OS 버전", placeholder="예: 95.08 · 95.08C1") \
            .props("dense outlined autofocus").classes("w-full")

        def go() -> None:
            v = (ver.value or "").strip()
            if not v:
                ui.notify("버전을 입력하세요.", color="warning")
                return
            ok, msg, _jid = JOBS.create(r["id"], v, actor=current_username() or "")
            ui.notify(msg, color="positive" if ok else "negative")
            if ok:
                dlg.close()
                on_change()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("취소", on_click=dlg.close).props("flat")
            ui.button("작업 만들기", icon="play_arrow", on_click=go).props("color=primary")
    dlg.open()


def _versions(r: dict, on_alert=None) -> None:
    """이 제품의 **버전 목록 = 작업 목록**. 버전 하나가 곧 작업 하나다."""
    @ui.refreshable
    def body() -> None:
        rows = JOBS.version_rows(r)

        def changed() -> None:
            body.refresh()
            if on_alert is not None:
                on_alert()

        _latest_rn(rows)

        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            ui.label("Release 버전").classes("text-subtitle2")
            ui.label(f"{len(rows)}개").classes("text-caption text-grey-7")
            ui.space()
            ui.button("버전 직접 추가", icon="add",
                      on_click=lambda rr=r, ch=changed: _add_version(rr, ch)) \
                .props("flat dense size=sm no-caps") \
                .tooltip("배포 공지도 트리거 메일도 없는 회차를 직접 시작합니다")

        if not rows:
            ui.label(
                # Others 는 회차를 감지하지 않는다 — 오지 않을 메일을 기다리라고
                # 안내하면 안 된다.
                "버전 없음 (Others — 회차 감지 안 함)"
                if P.is_others(r.get("category")) else
                "버전 없음"
            ).classes("text-caption text-grey-5")
            return
        with ui.element("div").classes("w-full").style(
            "border:1px solid #d5d8d2; border-radius:8px; overflow:hidden"
        ):
            for i, d in enumerate(rows):
                _version_item(r, d, changed, first=(i == 0))

    body()


_RN_GROUPS = ("공개 Release Note", "개발 Release Note")


def _latest_rn(rows: list[dict]) -> None:
    """가장 마지막 버전의 Release Note **최종본 PDF 한 개**.

    카드에서 찾는 건 '지금 최신 RN' 하나뿐이다. 초안·docx·개발 원본까지 늘어놓으면
    어느 게 최종인지 되레 헷갈린다. 그래서 **PDF 만**, 공개본을 먼저 골라 하나만 건다
    (전체 파일은 그 버전을 펼쳐 [결과물] 탭에서 본다).
    """
    for d in rows:                       # rows 는 최신 버전이 위
        pdfs = [a for a in JOBS.artifacts_for(d)
                if a["group"] in _RN_GROUPS and a["name"].lower().endswith(".pdf")]
        if not pdfs:
            continue
        pdfs.sort(key=lambda a: _RN_GROUPS.index(a["group"]))   # 공개본 우선
        f = pdfs[0]
        _url = "/artifact/view?path=" + quote(f["path"])
        with ui.row().classes("w-full items-center gap-2 no-wrap").style(
            "border:1px solid #e3e5e1; border-radius:8px; padding:4px 10px; background:#fafbfa"
        ):
            ui.icon("picture_as_pdf", size="sm").classes("text-grey-6")
            ui.label(f["name"]).classes("text-body2 ellipsis").style(
                "flex:1 1 0; min-width:0").tooltip(f"{d['version']} · {f['group']}")
            ui.button("열람", icon="visibility",
                      on_click=lambda u=_url: ui.navigate.to(u, new_tab=True)) \
                .props("flat dense size=sm no-caps")
            ui.button("다운로드", icon="download",
                      on_click=lambda p=f["path"]: ui.download(p)) \
                .props("flat dense size=sm no-caps")
        return
    ui.label("아직 Release Note 가 없습니다.").classes("text-caption text-grey-5")


_STATE_BORDER = {
    JOBS.NEW: "#b3261e", JOBS.IDLE: "#c7cac4", JOBS.PUBLISHED: "#7a8b98",
    JOBS.RUNNING_: "#d99a3a", JOBS.FINISHED: "#4A7C67",
}


def _version_item(r: dict, d: dict, on_change, first: bool) -> None:
    """버전 하나 = 작업 하나. 펼치면 [할 일] · [결과물] 두 탭.

    on_change 는 목록 전체를 다시 그린다 — **버튼 이벤트에서만** 쓴다.
    단계 상태가 바뀔 때(render_side 가 매 렌더마다 알린다)는 아래 head_status /
    out_panel 만 갱신한다. 목록 전체를 걸면 '그리기 → 알림 → 다시 그리기' 가
    끝없이 돌아 화면이 멈춘다.
    """
    job, upd, state = d["job"], d["update"], d["state"]

    exp = ui.expansion().classes("w-full").props("dense hide-expand-icon")
    exp.style(f"border-left:6px solid {_STATE_BORDER[state]}"
              + ("" if first else "; border-top:1px solid #eceeea"))

    # 상태는 **왼쪽 색 바 하나로만** 나타낸다. 줄마다 배지를 달면 정작 봐야 할
    # 버전 번호가 묻힌다 — 자세한 상태는 펼쳐서 본다.
    _sub = (job or {}).get("submission_mail_id") or (upd or {}).get("submission")
    _app = (job or {}).get("approval_mail_id") or (upd or {}).get("approval")
    tip = JOBS.VERSION_LABELS[state]
    if d.get("published_at"):
        tip += f" · 출시 {d['published_at']}"
    if d.get("backfilled"):
        tip += " (소급 기록 — 실제 실행 안 함)"
    if _sub or _app:
        tip += " · 메일 " + ("승인 완료" if _app else "승인 대기")
    if not d.get("release"):
        tip += " · 개발 RN 없음"

    # 줄에는 버전 번호만. 진행 표시·완료·삭제는 펼친 안쪽에 둔다.
    with exp.add_slot("header"):
        with ui.row().classes("w-full items-center gap-3 no-wrap"):
            ui.icon("expand_more", size="sm").classes("text-grey-6").style(
                "width:20px; flex:none")
            ui.label(d["version"]).classes("text-body2 font-mono").style(
                "font-weight:700").tooltip(tip)

    with exp:
        with ui.tabs().props("dense no-caps align=left").classes("w-full") as tabs:
            t_todo = ui.tab("할 일")
            t_out = ui.tab("결과물")

        @ui.refreshable
        def out_panel() -> None:
            _artifacts_panel(JOBS.artifacts_for(d))

        @ui.refreshable
        def head_status() -> None:
            _actions(r, d, on_change)

        def on_stage_change() -> None:
            """단계 상태가 바뀌었을 때 — 목록을 다시 그리지 않고 요약만 갱신한다."""
            head_status.refresh()
            out_panel.refresh()

        with ui.tab_panels(tabs, value=(t_out if state == JOBS.FINISHED else t_todo)) \
                .classes("w-full").props("keep-alive"):
            with ui.tab_panel(t_todo).classes("q-pa-sm"):
                head_status()
                _todo_panel(r, d, on_change, on_stage_change)
            with ui.tab_panel(t_out).classes("q-pa-sm"):
                out_panel()


def _actions(r: dict, d: dict, on_change) -> None:
    """이 버전에 대한 조작 — 진행 표시 + 완료로 기록 / 다시 열기 / 작업 삭제."""
    job, state = d["job"], d["state"]
    with ui.row().classes("w-full items-center gap-2 no-wrap"):
        ui.label(JOBS.VERSION_LABELS[state]).classes("text-caption text-grey-7")
        if job is not None:
            done, total = JOBS.job_progress(job["id"])
            ui.label(f"{done}/{total}").classes("text-caption text-grey-7 font-mono")
        if d.get("published_at"):
            ui.label(f"출시 {d['published_at']}").classes("text-caption text-grey-6")
        ui.space()
        if state == JOBS.FINISHED:
            ui.button("다시 열기", icon="lock_open",
                      on_click=lambda jj=job: _confirm_reopen(jj, on_change)) \
                .props("flat dense size=sm no-caps color=grey-8")
        elif not _is_unknown(d["version"]):
            ui.button("완료로 기록", icon="task_alt",
                      on_click=lambda: _confirm_close(r, d, on_change)) \
                .props("flat dense size=sm no-caps color=grey-8").tooltip(
                "실행 없이 '이미 끝난 회차'로 표시합니다")
        if job is not None:
            ui.button("작업 삭제", icon="delete_outline",
                      on_click=lambda jj=job: _confirm_delete(jj, on_change)) \
                .props("flat dense size=sm no-caps color=grey-7")
    ui.separator()


def _todo_panel(r: dict, d: dict, on_change, on_stage_change) -> None:
    """할 일 — 작업이 있으면 두 갈래 보드, 없으면 시작 안내."""
    from app.pages.task_board import render_side

    job = d["job"]
    if job is None:
        _start_panel(r, d, on_change)
        return

    # 시작한 뒤에도 근거는 남는다 — 작업에 박아 둔 스냅샷을 그대로 그린다.
    _basis_mails({"mails": JOBS.basis_for_job(job)})

    user_id = current_user_id()
    subject = f"{r['product']} A{r['android']} {r['os_type']} {d['version']}".strip()
    with ui.row().classes("w-full no-wrap items-stretch gap-4"):
        for side, color in ((SIDE_SUBMISSION, "#4A7C67"), (SIDE_APPROVAL, "#d99a3a")):
            with ui.column().classes("flex-1 min-w-0"):
                with ui.card().classes("w-full h-full").style(f"border-left:5px solid {color}"):
                    render_side(JobStageStore(job["id"]), subject, "", "",
                                user_id, side, on_status_change=on_stage_change)


def _start_panel(r: dict, d: dict, on_change) -> None:
    """아직 시작하지 않은 버전 — 무엇을 하게 되는지 보여주고 시작시킨다."""
    from app.stages import SIDE_LABELS, stage_keys_for

    upd = d["update"]
    # 메일은 '새 회차가 왔다'는 신호로만 쓴다 — 메일 내용 자체는 여기서 보여주지 않는다.
    if upd is not None:
        if upd.get("source") == "deploy":
            # 트리거 메일([FIH])이 없어 배포 공지로만 잡힌 회차 — 비GMS·긴급 배포 등.
            _msg = "배포 공지로 감지된 새 회차입니다(트리거 메일 없음)."
        elif upd["approved"]:
            _msg = "Google 승인까지 끝난 새 회차입니다."
        else:
            _msg = "승인 요청이 접수된 새 회차입니다(승인 대기)."
        ui.label(_msg).classes("text-body2").style("color:#b3261e; font-weight:700")
        _basis_mails(upd)
    elif d.get("published_at"):
        ui.label(f"이미 출시된 버전입니다 (출시 {d['published_at']}).").classes("text-body2")
    else:
        ui.label(
            "개발 Release Note 만 있는 버전입니다. 시작하면 이 버전으로 작업을 만듭니다."
        ).classes("text-caption text-grey-7")

    ui.label("시작하면 할 일").classes("text-subtitle2")
    with ui.row().classes("w-full no-wrap items-start gap-6"):
        for side in (SIDE_SUBMISSION, SIDE_APPROVAL):
            with ui.column().classes("gap-0 flex-1 min-w-0"):
                ui.label(SIDE_LABELS[side]).classes("text-caption").style(
                    f"font-weight:700; "
                    f"color:{'#3C6553' if side == SIDE_SUBMISSION else '#8a5a12'}")
                for k in stage_keys_for(side):
                    ui.label("· " + stage_label(k, side)).classes(
                        "text-caption text-grey-7")

    # 버전 판별은 기존 판별기가 한다. 못 정했으면(출시 이력 미조회 등) 직접 넣게 한다.
    unknown = _is_unknown(d["version"])
    ver_in = None
    if unknown:
        ui.separator()
        ui.label("이 회차의 OS 버전").classes("text-subtitle2")
        ui.label((d["update"] or {}).get("basis")
                 or "버전을 정하지 못했습니다 — 직접 입력하세요.") \
            .classes("text-caption").style("color:#8a5a12")
        ver_in = ui.input("OS 버전", placeholder="예: 45.81").classes("w-full") \
            .props("dense outlined")

    def go() -> None:
        version = (ver_in.value or "").strip() if ver_in is not None else d["version"]
        if not version or _is_unknown(version):
            ui.notify("OS 버전을 입력하세요.", color="warning")
            return
        if upd is not None:
            ok, msg, _ = JOBS.start_from_update(r, version, upd,
                                                actor=current_username() or "")
        else:
            ok, msg, _ = JOBS.create(r["id"], version, actor=current_username() or "")
        ui.notify(msg, color="positive" if ok else "negative", multi_line=True)
        if ok:
            on_change()

    with ui.row().classes("w-full justify-end"):
        ui.button("작업 시작" if unknown else f"{d['version']} 작업 시작",
                  icon="play_arrow", on_click=go).props("color=primary no-caps")


def _basis_mails(upd: dict) -> None:
    """이 회차를 '신규'라고 판단한 근거 메일.

    한 회차가 95.07 · C1 · C2 처럼 여러 공지로 나뉘어 오므로 전부 받아 두고, 화면에는
    원문만 건다. 본문은 새 탭이 아니라 미리보기 창으로 띄운다 — 탭을 넘기면 보던
    작업 화면을 잃는다.
    """
    mails = JOBS.basis_from_update(upd)
    if not mails:
        return

    # 한 공지에 회신이 줄줄이 달려 24건까지 나온다(실측 PM95 95.52). 회신은 원문과 같은
    # 스레드라 열어 봐야 같은 내용이므로 **원문만** 건다 — 대신 몇 건을 뺐는지 밝힌다.
    origin = [m for m in mails if m["not_re"]]
    shown, replies = (origin, len(mails) - len(origin)) if origin else (mails, 0)

    with ui.expansion(value=False).classes("w-full").props("dense expand-separator") as e:
        with e.add_slot("header"):
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.icon("mail_outline", size="sm").classes("text-grey-6")
                ui.label(f"근거 메일 {len(shown)}건").classes("text-caption").style(
                    "font-weight:700")
                if replies:
                    ui.label(f"회신 {replies}건 제외") \
                        .classes("text-caption text-grey-6")
        for m in shown:
            with ui.row().classes("w-full items-center gap-2 no-wrap").style(
                "padding:2px 6px"
            ):
                # 원 공지와 회신(Re:)을 구분해 준다 — 회신만 남은 회차인지 바로 보인다.
                ui.badge("원문" if m["not_re"] else "회신",
                         color="green-8" if m["not_re"] else "grey-6").props("rounded")
                if m.get("version"):
                    ui.label(m["version"]).classes("text-caption font-mono text-grey-8")
                ui.label(m["subject"] or f"메일 #{m['mail_id']}") \
                    .classes("text-caption ellipsis").style("flex:1 1 0; min-width:0") \
                    .tooltip(m["subject"])
                if m.get("date"):
                    ui.label(m["date"]).classes("text-caption text-grey-6")
                ui.button("미리보기", icon="visibility",
                          on_click=lambda i=m["mail_id"]: MD.preview(i)) \
                    .props("flat dense size=sm no-caps")


def _is_unknown(version: str) -> bool:
    """version_rows 가 버전을 못 정했을 때 쓰는 자리표시('? (R142)')인가."""
    return not version or version.startswith("?")


def _confirm_close(r: dict, d: dict, on_done) -> None:
    """실행 없이 '이미 끝난 회차'로 기록."""
    with ui.dialog() as dlg, ui.card().classes("w-[34rem] max-w-full"):
        ui.label(f"{r['product']} {d['version']} 을(를) 완료로 기록할까요?").classes(
            "text-subtitle2")
        ui.label(
            "단계를 실제로 실행하지 않고 이력만 남깁니다. 이미 처리가 끝난 과거 회차를 "
            "정리할 때 씁니다."
        ).classes("text-caption text-grey-7")
        if d.get("published_at"):
            ui.label(f"출시 이력: {d['published_at']}").classes(
                "text-caption text-grey-6 font-mono")
        else:
            ui.label("※ 출시 이력에 없는 버전입니다 — 정말 나간 회차인지 확인하세요.") \
                .classes("text-caption").style("color:#8a5a12")
        ui.label("나중에 [다시 열기] 로 되돌릴 수 있습니다.").classes(
            "text-caption text-grey-6")

        def go() -> None:
            ok, msg = JOBS.close_as_done(r["id"], d["version"],
                                         actor=current_username() or "")
            ui.notify(msg, color="positive" if ok else "negative", multi_line=True)
            dlg.close()
            if ok:
                on_done()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("취소", on_click=dlg.close).props("flat")
            ui.button("완료로 기록", on_click=go).props("color=primary")
    dlg.open()


def _confirm_reopen(j: dict, on_done) -> None:
    with ui.dialog() as dlg, ui.card().classes("w-[34rem] max-w-full"):
        ui.label(f"{j.get('product', '')} {j['version']} 작업을 다시 열까요?").classes(
            "text-subtitle2")
        ui.label(
            "모든 단계가 '대기' 로 돌아갑니다. 만들어 둔 산출물 파일은 그대로 남습니다."
        ).classes("text-caption text-grey-7")

        def go() -> None:
            ok, msg = JOBS.reopen(j["id"], actor=current_username() or "")
            ui.notify(msg, color="positive" if ok else "negative", multi_line=True)
            dlg.close()
            if ok:
                on_done()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("취소", on_click=dlg.close).props("flat")
            ui.button("다시 열기", on_click=go).props("color=primary")
    dlg.open()


def _artifacts_panel(arts: list[dict]) -> None:
    """결과물 — 이 버전에서 나온 파일을 만든 곳별로 묶어 보여준다."""
    if not arts:
        ui.label("아직 만들어진 결과물이 없습니다.").classes("text-caption text-grey-5")
        return
    groups: dict[str, list[dict]] = {}
    for a in arts:
        groups.setdefault(a["group"], []).append(a)
    for group, files in groups.items():
        ui.label(group).classes("text-caption text-grey-6")
        for f in files:
            _file_line(f["name"], f["path"])


def _file_line(name: str, path: str) -> None:
    viewable = path.lower().endswith((".pdf", ".html", ".htm", ".png", ".txt"))
    with ui.row().classes("w-full items-center gap-2 no-wrap"):
        ui.icon("insert_drive_file", size="sm").classes("text-grey-6")
        ui.label(name).classes("text-body2 ellipsis").style(
            "flex:1 1 0; min-width:0").tooltip(path)
        if viewable:
            _url = "/artifact/view?path=" + quote(path)
            ui.button("열람", icon="visibility",
                      on_click=lambda u=_url: ui.navigate.to(u, new_tab=True)) \
                .props("flat dense size=sm no-caps")
        ui.button("다운로드", icon="download",
                  on_click=lambda p=path: ui.download(p)) \
            .props("flat dense size=sm no-caps")


def _confirm_delete(j: dict, on_done) -> None:
    with ui.dialog() as dlg, ui.card():
        ui.label(f"{j.get('product', '')} {j['version']} 작업을 삭제할까요?").classes(
            "text-subtitle2")
        ui.label(
            "단계 진행 상태가 함께 지워집니다. 만들어 둔 산출물 파일은 남습니다."
        ).classes("text-caption text-grey-7")

        def go() -> None:
            ok, msg = JOBS.delete(j["id"])
            ui.notify(msg, color="positive" if ok else "negative")
            dlg.close()
            if ok:
                on_done()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("취소", on_click=dlg.close).props("flat")
            ui.button("삭제", on_click=go).props("color=negative")
    dlg.open()
