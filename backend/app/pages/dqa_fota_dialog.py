"""DQA FOTA Test 처리 팝업 — 파싱 → 다운로드/업로드 → FOTA 등록 → SN 등록 → 확인 → 회신.

WhiteList 메일함에서 DQA FOTA 테스트 요청 스레드에 대해 열린다. 모든 쓰기(FOTA·화이트리스트)는
관리자만, 미리보기(dry-run) 확인 뒤에만 실행한다. 읽기 전용 조회는 누구나 가능.

① 다운로드+업로드만은 이 팝업이 직접 돌리지 않는다 — `pipeline.dqa_jobs` 의 백그라운드
작업으로 보내고, 여기서는 진행률을 폴링해 그리기만 한다. 수 GB 를 받는 동안 팝업을 닫거나
화면을 새로 열어도 작업이 끊기지 않게 하려는 것이다(그 사고 경위는 dqa_jobs 머리말에).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from nicegui import run, ui

from app.pages import whitelist_verify as WV
from app.pipeline import dqa_fota as D
from app.pipeline import dqa_jobs as DJ

_WORK = Path(__file__).resolve().parent.parent.parent / "work" / "dqa_fota"

#: ① 작업 상태별 표시(색은 앱의 상태 색 규칙과 같다).
_JOB_KO = {DJ.RUNNING: "① 진행 중", DJ.DONE: "① 다운로드 + Dropbox 업로드 완료",
           DJ.CANCELLED: "① 중단됨", DJ.ERROR: "① 오류로 멈춤"}
_JOB_COLOR = {DJ.RUNNING: "#3b6ea5", DJ.DONE: "#4A7C67",
              DJ.CANCELLED: "#8a8a8a", DJ.ERROR: "#e05252"}


def open_dqa_fota_dialog(g: dict, me: str, refresh) -> None:
    from app.auth import current_user_id, is_admin

    uid = current_user_id()
    rep = g["rep"]
    body = rep.get("body_text", "") or ""
    subject = rep.get("subject", "") or ""
    serials = list(g.get("serials") or [])

    # 1) 파싱 + Plan(FOTA 조회) — 팝업 열 때 한 번. os_type 교정 시 재계산.
    parsed = D.parse_fota_test(subject, body, serials)
    # osinfo_ok = ③-1 이 끝나 FOTA 에서 확인된 상태인가. ③-2 는 이게 참일 때만 열린다.
    ctx = {"plan": D.build_plan(uid, parsed), "downloaded": None, "uploaded": False,
           "osinfo_ok": False}

    # 돌고 있거나 이미 끝난 ① 작업이 있으면 그 결과를 plan 에 되살린다 — 팝업을 다시
    # 열 때마다 plan 을 새로 계산하므로, 안 그러면 다 해 놓고도 ③ 이 막힌다.
    job_key = DJ.key_for(rep["id"])

    def _adopt(job: dict | None) -> None:
        if not job:
            return
        DJ.apply_to_plan(job, ctx["plan"])
        if job.get("downloaded"):
            ctx["downloaded"] = job["downloaded"]
        ctx["uploaded"] = bool(job.get("uploaded"))

    _adopt(DJ.snapshot(job_key))

    # 팝업은 페이지 바닥에 만든다 — 메일 목록 패널 안에 만들면 그 패널이 다시 그려질 때
    # 팝업이 통째로 사라진다(whitelist_organic 과 같은 이유, 실측 2026-08-10).
    with ui.context.client.content:
        dialog = ui.dialog()

    with dialog, ui.card().classes("w-full").style(
        "max-width:960px; max-height:92vh; overflow-y:auto"
    ):
        ui.label(f"DQA FOTA Test 처리 · {subject}").classes("text-subtitle1 text-weight-bold")
        ui.label(f"발신: {rep.get('sender','')}").classes("text-caption text-grey-7")

        if ctx["plan"].errors and not ctx["plan"].os_type:
            for e in ctx["plan"].errors:
                ui.label("✘ " + e).classes("text-negative")
            # OS_Type 을 못 잡아도 관리자가 수동 지정해 재계산할 수 있게 입력만 남긴다.

        # ── 파싱 값 직접 수정(안전장치) ──
        # 회신·표 형식 등으로 자동 파싱이 어긋나면 관리자가 경로·버전·SN·OS_Type 를 고쳐 다시 계산한다.
        _has_warn = bool(ctx["plan"].warnings or not ctx["plan"].os_type)
        with ui.expansion("파싱 값 직접 수정 (경로·버전·SN·OS_Type)", icon="edit",
                          value=_has_warn).classes("w-full"):
            ui.label("자동 파싱이 틀리면 여기서 고친 뒤 [적용·다시 계산]을 누르세요. "
                     "경로는 sdcard 폴더(또는 버전 폴더) UNC 전체를 넣습니다.").classes(
                "text-caption text-grey-7")
            with ui.row().classes("w-full gap-2 no-wrap"):
                prod_in = ui.input("제품(PMxx)", value=parsed.product).props(
                    "dense outlined").classes("w-40")
                and_in = ui.input("Android", value=parsed.android).props(
                    "dense outlined").classes("w-40")
                ot_in = ui.input("OS_Type", value=ctx["plan"].os_type).props(
                    "dense outlined").classes("flex-1")
            sn_in = ui.input("SN (쉼표로 여러 개)", value=", ".join(parsed.sns)).props(
                "dense outlined").classes("w-full")

            from_inputs = []
            for r in parsed.froms:
                ui.label(f"FROM [{r.dtype}]").classes("text-caption text-weight-bold q-mt-xs")
                with ui.row().classes("w-full gap-2 no-wrap"):
                    v = ui.input("버전", value=r.version).props("dense outlined").classes("w-64")
                    pth = ui.input("경로(UNC)", value=r.path).props(
                        "dense outlined").classes("flex-1")
                from_inputs.append((r, v, pth))

            to_inputs = None
            if parsed.to:
                ui.label("TO").classes("text-caption text-weight-bold q-mt-xs")
                with ui.row().classes("w-full gap-2 no-wrap"):
                    tv = ui.input("버전", value=parsed.to.version).props(
                        "dense outlined").classes("w-64")
                    tp = ui.input("경로(UNC)", value=parsed.to.path).props(
                        "dense outlined").classes("flex-1")
                with ui.row().classes("w-full gap-2 no-wrap"):
                    tf = ui.input("Full PFU(선택)", value=parsed.to.full_pfu).props(
                        "dense outlined").classes("flex-1")
                    td = ui.input("Diff PFU(선택)", value=parsed.to.diff_pfu).props(
                        "dense outlined").classes("flex-1")
                to_inputs = (tv, tp, tf, td)

            def _recalc():
                import re as _re
                parsed.product = (prod_in.value or "").strip()
                pm = _re.search(r"(\d{2,3})", parsed.product)
                parsed.product_num = pm.group(1) if pm else parsed.product_num
                parsed.android = (and_in.value or "").strip()
                parsed.sns = [s.strip() for s in (sn_in.value or "").replace("\n", ",").split(",")
                              if s.strip()]
                for r, v, pth in from_inputs:
                    r.version = (v.value or "").strip()
                    r.path = (pth.value or "").strip()
                if to_inputs and parsed.to:
                    parsed.to.version = (to_inputs[0].value or "").strip()
                    parsed.to.path = (to_inputs[1].value or "").strip()
                    parsed.to.full_pfu = (to_inputs[2].value or "").strip()
                    parsed.to.diff_pfu = (to_inputs[3].value or "").strip()
                ctx["plan"] = D.build_plan(uid, parsed, os_type_override=(ot_in.value or "").strip())
                ctx["downloaded"] = None
                ctx["uploaded"] = False
                # os_version 이 다시 배정되므로 앞서 확인한 OsInfo 는 무효다.
                ctx["osinfo_ok"] = False
                # 받아 둔 빌드·Dropbox URL 은 OS_Type 과 무관하다 — 다시 붙여 준다.
                _adopt(DJ.snapshot(job_key))
                preview.refresh()
                fota_panel.refresh()
                ui.notify(f"OS_Type={ctx['plan'].os_type or '(미해결)'} 로 다시 계산", color="info")

            ui.button("적용 · 다시 계산", icon="refresh", on_click=_recalc).props(
                "color=primary dense").classes("q-mt-sm")

        @ui.refreshable
        def preview():
            plan = ctx["plan"]
            # 파싱 요약
            with ui.card().classes("w-full").style("background:#f6f8f7"):
                ui.label("파싱 결과").classes("text-subtitle2")
                ui.label(f"제품 {plan.product} · Android {plan.android} · OS_Type "
                         f"{plan.os_type or '(미해결)'} (FOTA 최신 os_version={plan.latest})").classes(
                    "text-body2")
                ui.label(f"SN: {', '.join(parsed.sns) or '(없음)'}  ·  "
                         f"api={plan.api} · endDate={plan.end_date} · PN(빈값)").classes("text-body2")
                if plan.note:
                    ui.label("· " + plan.note).classes("text-caption text-orange-9")
                # 버전 표기 접두어는 제품마다 다르다 — 무엇을 근거로 붙였는지 보여 준다.
                _vp = plan.version_prefix
                ui.label(
                    "버전 표기: " + (f"'{_vp}' 접두어" if _vp else "숫자만(접두어 없음)")
                    + (f"  ←  {plan.version_prefix_src}" if plan.version_prefix_src else "")
                ).classes("text-caption").style(
                    "color:" + ("#4A7C67" if plan.version_prefix_src else "#c07a2c")
                ).tooltip("FOTA OsInfo 의 Version 칸 표기입니다. 제품관리의 "
                          "'최초 OS 버전'에서 가져옵니다(TR54G15.00 → TR54G).")
                for r in parsed.froms:
                    ui.label(f"FROM [{r.dtype}] {r.version}  →  {r.path}").classes(
                        "text-caption text-grey-8")
                if parsed.to:
                    ui.label(f"TO {parsed.to.version}  →  {parsed.to.path}").classes(
                        "text-caption text-grey-8")
                for w in plan.warnings:
                    ui.label("⚠ " + w).classes("text-caption text-orange-9")

            # OsInfo
            ui.label("OsInfo (등록될 행)").classes("text-subtitle2 q-mt-sm")
            oi_cols = [{"name": k, "label": k, "field": k, "align": "left"}
                       for k in ("osType", "version", "osVersion", "description")]
            oi_data = [{"osType": r.os_type, "version": r.version, "osVersion": r.os_version,
                        "description": r.description} for r in plan.oi_rows]
            ui.table(columns=oi_cols, rows=oi_data).props("dense flat bordered").classes("w-full")

            # DownloadPath (다운로드/업로드 후 갱신)
            ui.label("DownloadPath (from=dummy · to=실파일)").classes("text-subtitle2 q-mt-sm")
            dp_cols = [{"name": k, "label": k, "field": k, "align": "left"}
                       for k in ("osVersion", "downloadType", "fileNm", "size", "downloadUrl")]
            dp_data = []
            for r in plan.dp_rows:
                url = r.download_url or ("(업로드 대기)" if not r.is_dummy else "")
                dp_data.append({"osVersion": r.os_version, "downloadType": r.dtype,
                                "fileNm": r.filename or "(다운로드 후 확정)",
                                "size": r.size or "-",
                                "downloadUrl": (url[:64] + "…") if len(url) > 64 else url})
            ui.table(columns=dp_cols, rows=dp_data).props("dense flat bordered").classes("w-full")

        preview()

        # ── ① 진행 상황 ────────────────────────────────────────────
        # 작업 자체는 dqa_jobs 의 스레드가 한다. 여기 있는 것은 그 상태를 1초마다
        # 읽어 그리는 판때기뿐이라, 팝업을 닫았다 다시 열면 이어서 보인다.
        job_card = ui.card().classes("w-full q-mt-sm")
        with job_card:
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                head_lbl = ui.label("").classes("text-subtitle2")
                ui.space()
                elapsed_lbl = ui.label("").classes("text-caption text-grey-7")
                cancel_btn = ui.button("중단", icon="stop").props(
                    "flat dense color=negative")
                clear_btn = ui.button("기록 지우기", icon="close").props(
                    "flat dense color=grey-7")
            bar = ui.linear_progress(value=0.0, show_value=False).props(
                "instant-feedback rounded size=12px").classes("w-full")
            detail_lbl = ui.label("").classes("text-caption")
            rate_lbl = ui.label("").classes("text-caption text-grey-7")
            job_log = ui.column().classes("w-full")
        job_card.set_visibility(False)

        log_box = ui.column().classes("w-full q-mt-sm")

        def _log(lines, title=""):
            with log_box:
                if title:
                    ui.label(title).classes("text-subtitle2")
                with ui.scroll_area().classes("w-full").style("max-height:24vh"):
                    ui.label("\n".join(lines)).classes(
                        "whitespace-pre-wrap text-caption font-mono")

        # 그린 상태를 기억해 둔다 — 로그는 줄 수가 바뀔 때만, 결과 반영은 한 번만.
        seen = {"logs": -1, "state": ""}

        def _tick() -> None:
            job = DJ.snapshot(job_key)
            if not job:
                job_card.set_visibility(False)
                return
            job_card.set_visibility(True)
            d = DJ.describe(job)
            st = job["state"]
            running = (st == DJ.RUNNING)
            job_card.style("border-left:4px solid " + _JOB_COLOR.get(st, "#8a8a8a"))
            head_lbl.text = (d["headline"] or _JOB_KO.get(st, "")) + (
                " · 이전 실행 기록" if job.get("restored") else "")
            elapsed_lbl.text = "경과 " + d["elapsed"]
            bar.set_visibility(running)
            bar.value = d["ratio"]
            detail_lbl.text = d["detail"] if running else (job.get("message") or "")
            rate_lbl.text = f"속도 {d['speed']} · 남은 시간 {d['eta']}"
            rate_lbl.set_visibility(running)
            cancel_btn.set_visibility(running)
            clear_btn.set_visibility(not running)
            if len(job["logs"]) != seen["logs"]:
                seen["logs"] = len(job["logs"])
                job_log.clear()
                if job["logs"]:
                    with job_log, ui.expansion("실행 로그", icon="list").classes("w-full"):
                        with ui.scroll_area().classes("w-full").style("max-height:24vh"):
                            ui.label(chr(10).join(job["logs"])).classes(
                                "whitespace-pre-wrap text-caption font-mono")
            if seen["state"] != st:
                # 방금 끝났다면 결과를 plan 에 붙이고 미리보기를 다시 그린다.
                if not running:
                    _adopt(job)
                    preview.refresh()
                seen["state"] = st

        def _start_job() -> None:
            workdir = str(_WORK / f"{rep['id']}_{datetime.now():%Y%m%d-%H%M%S}")
            DJ.clear(job_key)
            seen["logs"], seen["state"] = -1, ""
            ok, msg = DJ.start(job_key, uid, ctx["plan"], workdir, label=subject)
            if not ok:
                ui.notify(msg, color="warning")
                return
            log_box.clear()
            ui.notify("백그라운드에서 시작했습니다 — 팝업을 닫아도 계속 진행됩니다.",
                      color="positive")
            _tick()

        # ── 단계 버튼 ──
        async def step_download():
            if DJ.is_running(job_key):
                ui.notify("이미 돌고 있습니다 — 위 진행률에서 확인하세요.", color="warning")
                return
            j = DJ.snapshot(job_key)
            if j and j["state"] == DJ.DONE:
                # 오늘 아침처럼 같은 빌드를 두 번 받는 일을 막는다(3GB × 2).
                with ui.dialog() as c, ui.card():
                    ui.label("다시 받기").classes("text-h6")
                    ui.label(
                        "이 요청은 이미 다운로드+업로드가 끝났습니다. "
                        "같은 파일을 다시 받아 다시 올릴까요? (수 GB · 수 분)"
                    ).classes("text-caption")
                    with ui.row().classes("justify-end gap-2"):
                        ui.button("취소", on_click=c.close).props("flat")
                        ui.button(
                            "다시 받기",
                            on_click=lambda: (c.close(), _start_job()),
                        ).props("color=primary")
                c.open()
                return
            _start_job()

        def _cancel_job() -> None:
            if DJ.cancel(job_key):
                ui.notify("중단을 요청했습니다 — 지금 보내는 덩어리가 끝나면 멈춥니다.",
                          color="warning")
                _tick()

        def _clear_job() -> None:
            DJ.clear(job_key)
            seen["logs"], seen["state"] = -1, ""
            job_card.set_visibility(False)

        cancel_btn.on_click(_cancel_job)
        clear_btn.on_click(_clear_job)

        async def step_dryrun():
            log_box.clear()
            from app.integrations import fota_system
            n = ui.notification("FOTA dry-run 조회 중…", spinner=True, timeout=None)
            try:
                rep0 = await run.io_bound(D.register_fota, uid, ctx["plan"], False)
            finally:
                n.dismiss()
            _log([fota_system.format_report(rep0)], "② FOTA 등록 미리보기(dry-run)")

        # ── ③ FOTA 실제 등록 — OsInfo 와 DownloadPath 를 나눠서 ────────────
        # DownloadPath 는 OsInfo 의 osType+osVersion 에 매달리는 행이라, OsInfo 가
        # 안 들어가면 붙을 데가 없다. 한 번에 보내던 예전 방식은 OsInfo 가 실패해도
        # DownloadPath 를 계속 보내서 '판은 없는데 경로만 있는' 상태를 만들 수 있었다.
        # 그래서 ③-1 을 끝내고 **FOTA 에서 되읽어 확인한 뒤에만** ③-2 로 넘어간다.
        def _guard_admin_ostype(plan) -> bool:
            if not is_admin():
                ui.notify("FOTA 등록은 관리자만 가능합니다.", color="warning")
                return False
            if not plan.os_type:
                ui.notify("OS_Type 이 확정되지 않았습니다 — 위에서 교정 후 다시 계산하세요.",
                          color="warning")
                return False
            return True

        def _confirm(title: str, body: str, on_go) -> None:
            with ui.dialog() as c, ui.card().style("min-width:520px"):
                ui.label(title).classes("text-h6")
                ui.label(body).classes("text-negative").style(
                    "white-space:normal; word-break:break-word")

                async def _go():
                    c.close()
                    await on_go()

                with ui.row().classes("justify-end gap-2"):
                    ui.button("취소", on_click=c.close).props("flat")
                    ui.button("실제 등록", on_click=_go).props("color=negative")
            c.open()

        async def step_register_osinfo():
            plan = ctx["plan"]
            if not _guard_admin_ostype(plan):
                return
            vers = " · ".join(f"osv {r.os_version} = {r.version}" for r in plan.oi_rows)

            async def go():
                from app.integrations import fota_system
                n = ui.notification("OsInfo 등록·확인 중…", spinner=True, timeout=None)
                try:
                    rr = await run.io_bound(D.register_osinfo, uid, plan, True)
                finally:
                    n.dismiss()
                ctx["osinfo_ok"] = bool(rr.get("osinfo_ok"))
                log_box.clear()
                _log([fota_system.format_report(rr)],
                     "③-1 OsInfo 등록 " + ("완료 · 확인됨" if rr.get("osinfo_ok") else "실패"))
                fota_panel.refresh()
                ui.notify("OsInfo 등록·확인 완료 — 이어서 ③-2 를 진행하세요."
                          if rr.get("osinfo_ok") else "OsInfo 등록 실패 — 로그를 확인하세요.",
                          color="positive" if rr.get("osinfo_ok") else "negative",
                          multi_line=True)

            _confirm("③-1 OsInfo 실제 등록",
                     f"OsInfo {len(plan.oi_rows)}행을 실제 FOTA(production)에 등록합니다.\n"
                     f"{vers}", go)

        async def step_register_dp():
            plan = ctx["plan"]
            if not _guard_admin_ostype(plan):
                return
            if not any(r.download_url for r in plan.dp_rows if not r.is_dummy):
                ui.notify("먼저 ① 다운로드+업로드를 완료하세요(to-OS 실 URL 필요).",
                          color="warning")
                return

            async def go():
                from app.integrations import fota_system
                n = ui.notification("OsInfo 확인 → DownloadPath 등록 중…",
                                    spinner=True, timeout=None)
                try:
                    rr = await run.io_bound(D.register_downloadpath, uid, plan, True)
                finally:
                    n.dismiss()
                ctx["osinfo_ok"] = bool(rr.get("osinfo_ok"))
                log_box.clear()
                _log([fota_system.format_report(rr)],
                     "③-2 DownloadPath 등록 " + ("성공" if rr.get("ok") else "오류"))
                fota_panel.refresh()
                if rr.get("skipped"):
                    ui.notify(rr["skipped"], color="negative", multi_line=True)
                else:
                    ui.notify("DownloadPath 등록 완료" if rr.get("ok") else "등록 오류",
                              color="positive" if rr.get("ok") else "negative")

            _confirm("③-2 DownloadPath 실제 등록",
                     f"DownloadPath {len(plan.dp_rows)}행을 실제 FOTA(production)에 "
                     "등록합니다. 보내기 전에 OsInfo 가 실제로 있는지 다시 확인합니다.", go)

        async def check_osinfo():
            """OsInfo 가 FOTA 에 들어가 있는지만 읽어 본다(쓰기 없음)."""
            from app.integrations import fota_system

            plan = ctx["plan"]
            n = ui.notification("OsInfo 확인 중…", spinner=True, timeout=None)
            try:
                rr = await run.io_bound(
                    fota_system.register_downloadpath_only,
                    uid, D.osinfo_lists(plan), [], False)
            finally:
                n.dismiss()
            ctx["osinfo_ok"] = bool(rr.get("osinfo_ok"))
            log_box.clear()
            _log(rr.get("lines") or [], "③ OsInfo 확인")
            fota_panel.refresh()
            ui.notify("OsInfo 확인됨" if ctx["osinfo_ok"] else "OsInfo 가 확인되지 않습니다",
                      color="positive" if ctx["osinfo_ok"] else "warning")

        async def step_whitelist():
            plan = ctx["plan"]
            if not is_admin():
                ui.notify("화이트리스트 등록은 관리자만 가능합니다.", color="warning")
                return
            if not plan.os_type or not parsed.sns:
                ui.notify("OS_Type 또는 SN 이 없습니다 — 확인 후 진행하세요.", color="warning")
                return
            with ui.dialog() as c, ui.card().style("min-width:620px"):
                ui.label("SN 화이트리스트 등록").classes("text-h6")
                # 올라갈 엑셀을 그대로 보여준다(열 이름·순서 = 업로드 파일 헤더).
                from app.pages.whitelist_bulk import UPLOAD_COLS, upload_rows

                ui.table(columns=UPLOAD_COLS,
                         rows=upload_rows(D.whitelist_devices(plan)),
                         row_key="sn").classes("w-full").props("dense flat bordered")

                async def go():
                    c.close()
                    n = ui.notification("화이트리스트 등록 중…", spinner=True, timeout=None)
                    try:
                        wl = await run.io_bound(D.register_whitelist, uid, plan, True)
                    finally:
                        n.dismiss()
                    log_box.clear()
                    _log([str(d) for d in wl.get("details", [])],
                         "④ 화이트리스트 " + ("성공" if wl.get("ok") else "오류"))
                    ui.notify("SN 등록 완료" if wl.get("ok") else "SN 등록 오류",
                              color="positive" if wl.get("ok") else "negative")
                    # 보낸 값을 기대값으로 남긴다 — ⑤ 확인이 FOTA 에서 다시 읽어 대조한다.
                    ctx["verify_items"] = D.whitelist_devices(plan)
                    ctx["verify_result"] = None
                    verify_panel.refresh()
                    # 실제로 올라간 건만 등록 내역에 남긴다.
                    if wl.get("ok"):
                        from app.pages.whitelist_mail import _sender_addr_name
                        from app.whitelist import service as WLS

                        _a, _n = _sender_addr_name(rep.get("sender", ""))
                        rec = await run.io_bound(lambda: WLS.record_registration(
                            devices=D.whitelist_devices(plan),
                            mail={"mail_id": rep["id"], "requester_addr": _a,
                                  "requester_name": _n,
                                  "message_id": rep.get("message_id", ""),
                                  "lang": rep.get("lang", "")},
                            processed_by=me, category="DQA",
                            memo=f"[DQA FOTA Test · {plan.os_type} · api={plan.api}]"))
                        if rec.get("ok"):
                            ui.notify(f"등록 내역 기록 · {rec['request']['id']}",
                                      color="positive")

                with ui.row().classes("justify-end gap-2"):
                    ui.button("취소", on_click=c.close).props("flat")
                    ui.button("등록", on_click=go).props("color=negative")
            c.open()

        # ⑤ 확인 — 보냈다는 응답만 믿지 않고 FOTA 에서 다시 읽어 대조한다.
        async def step_verify():
            items = ctx.get("verify_items") or []
            if not items:
                ui.notify("④ SN 화이트리스트 등록을 먼저 실행하세요.", color="warning")
                return
            res = await WV.run_verify(uid, items, "DQA 화이트리스트")
            ctx["verify_result"] = res
            verify_panel.refresh()
            if res.get("error"):
                ui.notify(res["error"], color="negative")
            else:
                ui.notify(f"일치 {res['matched']}/{res['total']}",
                          color="positive" if res["ok"] else "negative")

        @ui.refreshable
        def verify_panel() -> None:
            # ④ 전에도 ⑤·⑥ 자리를 보여준다 — 아무것도 안 그리면 단계가 없는 화면으로 보인다.
            items = ctx.get("verify_items") or []
            with ui.card().classes("w-full q-mt-sm"):
                ui.label("⑤ 확인" + (f" · SN {len(items)}대 "
                                     f"(osType={ctx['plan'].os_type} · api={ctx['plan'].api} · "
                                     f"endDate={ctx['plan'].end_date})" if items else "")) \
                    .classes("text-subtitle2")
                res = ctx.get("verify_result")
                if not items:
                    ui.label("④ SN 화이트리스트 등록을 먼저 실행하세요.") \
                        .classes("text-caption text-grey-7")
                elif res is None:
                    ui.label("아직 확인하지 않았습니다.").classes("text-caption text-grey-7")
                else:
                    WV.verify_table(res)

            _reply_card(items)

        def _reply_card(items: list) -> None:
            """⑥ 메일 회신 — 사내 회신 형식(mailer.build_dqa_reply_draft).

            **전체 회신이다.** DQA 요청 메일은 SQA·TA 그룹 주소를 참조에 달고 오고,
            실제 회신도 그 참조를 그대로 살려 보냈다(메일함 [933]·[1343] 실측). 참조를
            떨어뜨리면 완료 통보가 요청자 한 사람에게만 가서 SQA·TA 는 모른 채로 남는다.
            """
            from app.pages.whitelist_mail import _sender_addr_name
            from app.user_mailer import display_name_for, reply_context
            from app.whitelist import mailer as WM

            addr, to_name = _sender_addr_name(rep.get("sender", ""))
            # 사내 발신자는 표시 이름 없이 주소만 오는 경우가 많다(daisy.kim@… ).
            # 그때는 인원 명부의 이름을 쓴다 — 호칭 없는 회신을 내보내지 않으려는 것.
            if not to_name and addr:
                to_name = display_name_for(addr)
            rctx = reply_context(rep.get("id"), with_cc=True) if rep.get("id") else {}
            cc = rctx.get("cc", "")
            with ui.card().classes("w-full"):
                ui.label("⑥ 메일 회신").classes("text-subtitle2")
                if not items:
                    ui.label("④ SN 화이트리스트 등록을 먼저 실행하세요.") \
                        .classes("text-caption text-grey-7")
                    return
                ui.label(f"받는 사람: {addr or '(주소 없음)'}") \
                    .classes("text-caption text-grey-7")
                ui.label(f"참조: {cc or '(원본에 참조 없음)'}") \
                    .classes("text-caption text-grey-7") \
                    .tooltip("원본 메일의 받는 사람·참조를 그대로 살립니다(전체 회신). "
                             "본인 주소와 받는 사람은 자동으로 빠집니다.")
                draft = WM.build_dqa_reply_draft(to_name=to_name, subject_orig=subject)
                subj_in = ui.input("제목", value=draft["subject"]) \
                    .props("outlined dense").classes("w-full")
                body_in = ui.textarea("본문", value=draft["body"]) \
                    .props("outlined").classes("w-full").style("min-height:180px")

                async def _send():
                    if not addr:
                        ui.notify("회신 받을 주소가 없습니다.", color="negative")
                        return
                    send_btn.props("loading")
                    try:
                        ok, msg = await run.io_bound(
                            WM.send_reply, uid, addr, subj_in.value, body_in.value,
                            orig_mail_id=rep.get("id"), with_cc=True)
                    finally:
                        send_btn.props(remove="loading")
                    if ok:
                        ui.notify("회신을 보냈습니다.", color="positive")
                        _mark_done(g, me)
                        dialog.close()
                        ui.timer(0.01, refresh, once=True)
                    else:
                        ui.notify(f"회신 발송 실패: {msg}", color="negative", multi_line=True)

                with ui.row().classes("items-center gap-2"):
                    # 회신은 요청 스레드에 인용부·서명까지 얹혀 나간다 — 누르기 전에
                    # 실제로 어떻게 실리는지 볼 수 있어야 한다(고객 회신 화면과 동일).
                    from app.pages.mail_preview import preview_button

                    preview_button(uid, lambda: subj_in.value, lambda: body_in.value,
                                   to=addr, cc=cc,
                                   quote_html_fn=lambda: rctx.get("quote_html", ""))
                    send_btn = ui.button("회신 보내기", icon="send", on_click=_send) \
                        .props("unelevated color=primary")

        verify_panel()

        with ui.row().classes("w-full gap-2 q-mt-md"):
            ui.button(
                "① 빌드 다운로드 + Dropbox 업로드", icon="cloud_download",
                on_click=step_download,
            ).props("color=primary").tooltip(
                "백그라운드에서 돕니다 — 팝업을 닫아도 계속되고, "
                "다시 열면 진행률이 이어서 보입니다.")
            ui.button("② FOTA 미리보기", icon="visibility",
                      on_click=step_dryrun).props("outline")
            ui.button("④ SN 화이트리스트 등록", icon="playlist_add_check",
                      on_click=step_whitelist).props("color=deep-orange")
            ui.button("⑤ 확인", icon="fact_check",
                      on_click=step_verify).props("color=primary")

        # ③ 은 순서를 지켜야 해서 따로 묶는다 — OsInfo 가 확인돼야 DownloadPath 가 열린다.
        @ui.refreshable
        def fota_panel() -> None:
            ok = bool(ctx.get("osinfo_ok"))
            with ui.card().classes("w-full q-mt-sm").style(
                "border-left:4px solid " + ("#4A7C67" if ok else "#d0a24c")
            ):
                with ui.row().classes("w-full items-center gap-2 no-wrap"):
                    ui.label("③ FOTA 실제 등록").classes("text-subtitle2")
                    ui.badge("OsInfo 확인됨" if ok else "OsInfo 미확인",
                             color="green-7" if ok else "orange-8").props("outline")
                    ui.space()
                    ui.button("OsInfo 확인만", icon="fact_check",
                              on_click=check_osinfo).props("flat dense color=primary") \
                        .tooltip("쓰기 없이 FOTA 목록만 읽어 대조합니다")
                ui.label(
                    "DownloadPath 는 OsInfo 의 osType+osVersion 에 붙는 행입니다. "
                    "OsInfo 가 먼저 들어가 확인돼야 ③-2 가 열립니다."
                ).classes("text-caption text-grey-7").style("white-space:normal")
                with ui.row().classes("gap-2 q-mt-xs"):
                    ui.button("③-1 OsInfo 등록", icon="playlist_add",
                              on_click=step_register_osinfo).props("color=deep-orange")
                    _dp = ui.button("③-2 DownloadPath 등록", icon="cloud_upload",
                                    on_click=step_register_dp).props("color=deep-orange")
                    _dp.set_enabled(ok)
                    if not ok:
                        _dp.tooltip("③-1 을 끝내거나 [OsInfo 확인만] 을 눌러 확인하세요")

        fota_panel()

        with ui.row().classes("w-full justify-between q-mt-sm"):
            ui.button("처리 완료(숨김)", icon="check_circle",
                      on_click=lambda: (_mark_done(g, me), dialog.close(),
                                        ui.timer(0.01, refresh, once=True))) \
                .props("flat color=grey-8")
            ui.button("닫기", on_click=dialog.close).props("flat")

        # 진행률은 1초마다 다시 읽는다. 하는 일이 사본 한 번 뜨는 것뿐이라 부담이 없고,
        # 팝업을 닫았다 다시 열면 곧바로 최신 상태가 보인다.
        # **타이머는 반드시 팝업 안에 만든다** — 팝업 바깥(메일 목록 패널)에 만들면
        # 그 패널이 다시 그려질 때 타이머까지 지워져 진행률이 그 자리에서 멈춘다.
        _tick()
        ui.timer(1.0, _tick)

    dialog.open()


def _mark_done(g: dict, me: str) -> None:
    from app.whitelist import mail_state as MS
    from app.whitelist import mail_triage as MT
    try:
        MS.set_status(g["key"], MS.DONE, by=me, note="DQA FOTA Test 처리",
                      rep_ts=MT.newest_ts(g))
    except Exception:  # noqa: BLE001
        pass
