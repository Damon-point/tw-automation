"""WhiteList 요청 **유기적 처리** 팝업 — AI 해석 + 유형 잠금 실행 + Claude 직접 지시.

정해진 양식이 없는 요청을 소스 블록(본문/첨부메일/엑셀)으로 나누고, Claude 가 **요청 전체가
등록일 변경인지 신규 등록인지**를 판정한다. 다이얼로그는 그 유형의 **실행만** 노출한다:
  · 등록일 변경 → 날짜만 지정해 FOTA DB 직접 반영 (한 트랜잭션 · 미등록 섞이면 전체 중단)
  · 신규 등록   → 모델·OS 지정해 엑셀 업로드 (등록 대상 블록의 SN)
AI 가 잘못 판별하면 **Claude 에게 직접 지시**(OS Update 재작업 지시처럼)해 다시 해석한다.

SN 은 결정론적으로 뽑은 값을 그대로 쓰고, 원문(.eml)은 수집 때 저장해 둔 것을 로컬에서
읽는다(POP3 동시접속 제약 회피). 모든 실제 쓰기는 확인 다이얼로그 + dry-run 뒤.
"""
from __future__ import annotations

import email
from datetime import date

from nicegui import run, ui

from app.pages import whitelist_verify as WV
from app.pages.whitelist_bulk import (DATE_COLS, UPLOAD_COLS, _CHUNK, _chunks,
                                      _plus_year, date_rows, upload_rows)
from app.whitelist import fota as WF
from app.whitelist import service as S

_TYPE_KO = {"date": "등록일 변경 요청", "new": "신규 등록 요청",
            "mixed": "혼합(판단 필요)", "unknown": "판별 실패"}


def _dedup(sns) -> list[str]:
    return list(dict.fromkeys(s.strip().upper() for s in sns if s and s.strip()))


_UNKNOWN_COLS = [
    {"name": "sn", "label": "S/N", "field": "sn", "align": "left"},
    {"name": "model", "label": "모델", "field": "model", "align": "left"},
    {"name": "os", "label": "From→To", "field": "os", "align": "left"},
    {"name": "ostype", "label": "ostype", "field": "ostype", "align": "left"},
    {"name": "status", "label": "상태", "field": "status", "align": "left"},
]


def unknown_rows(failed: list, manual: dict, category: str) -> list[dict]:
    """모델을 못 받은 S/N 의 표 행. 지정된 것은 어떤 ostype 이 되는지까지 보여준다.

    build_device 를 행마다 부르지 않는다 — 601대까지 오는데 그때마다 규칙을 DB 에서
    찾으면 표를 그릴 때마다 천 번 넘게 조회한다. 같은 지정끼리 ostype 을 한 번만 찾고,
    행마다 다른 것은 S/N 검사뿐이다.
    """
    seen: dict = {}
    out: list[dict] = []
    for sn in failed:
        a = manual.get(sn)
        if not a:
            out.append({"sn": sn, "model": "", "os": "", "ostype": "",
                        "status": "미지정 — 등록에서 빠집니다"})
            continue
        key = (a["model"], a["from"], a["to"])
        if key not in seen:
            seen[key] = S.get_ostype(a["model"], a["from"], a["to"], category)
        ostype = seen[key]
        errs = []
        if not S.is_valid_sn(sn):
            errs.append("SN 10자리 아님")
        if not ostype:
            errs.append(f"규칙 없음 ({a['model']} {a['from']}→{a['to']} · {category})")
        out.append({"sn": sn, "model": a["model"], "os": f"{a['from']}→{a['to']}",
                    "ostype": ostype or "",
                    "status": "✅ 등록 대상" if not errs else "⚠ " + " / ".join(errs)})
    return out


def render_unknown_models(failed: list, manual: dict, category: str, on_change) -> None:
    """포털에서 모델을 못 받은 S/N — **사람이 모델·OS 를 지정해 등록에 넣는다**.

    포털 단말 마스터에 없는 단말이 섞여 온다(출하 전이라 아직 대장에 없음 · 고객 파일의
    오타 · 애초에 S/N 이 아닌 값). 예전에는 목록만 보여 주고 전부 버렸는데, 모델을 아는
    사람이 직접 지정하면 등록할 수 있어야 한다(사용자 확정 2026-09-03).

    행마다 셀렉트를 두지 않는다 — 실측 601대짜리 요청이 있고, 그러면 셀렉트만 1,200개다.
    대신 **표에서 행을 고르고 위쪽 셀렉트로 적용**한다. 개별 지정과 일괄 지정이 같은
    조작이 되고, 아무것도 고르지 않으면 전체에 적용한다.

    모델 목록은 [설정 → WhiteList 규칙] 에 등록된 것만 준다. 규칙이 없는 모델은 ostype 을
    정할 수 없어 어차피 등록되지 않는다.

    지정하지 않은 행은 예전과 똑같이 등록에서 빠진다 — 그래서 'V260902023'(출하요청
    번호가 S/N 으로 잘못 뽑힌 값) 같은 것을 따로 빼는 조작이 필요 없다. 잘못 지정했으면
    그 행만 골라 [지정 취소] 한다.
    """
    done = [s for s in failed if s in manual]
    with ui.card().classes("w-full q-mt-xs").style("border-left:4px solid #b3261e"):
        ui.label(f"모델을 못 받은 S/N · {len(failed):,}대"
                 + (f"  (지정 {len(done):,}대)" if done else "")) \
            .classes("text-subtitle2 text-red-9")
        ui.label("포털 단말 마스터에 없거나 조회가 실패했습니다. "
                 "모델과 OS 를 지정한 행만 등록에 들어갑니다.") \
            .classes("text-caption text-grey-8")

        models = sorted({r["model"] for r in S.list_rules()})

        def _rules_of(model: str) -> dict:
            rs = [r for r in S.rules_for(model) if r["category"] == category] \
                or S.rules_for(model)
            return {f"{r['from']}→{r['to']}": r for r in rs}

        with ui.row().classes("items-end gap-2 q-mt-xs"):
            m_sel = ui.select(models, label="모델", with_input=True) \
                .props("outlined dense").classes("w-40")
            o_sel = ui.select([], label="From→To").props("outlined dense").classes("w-44")

            def _on_model(e) -> None:
                opts = _rules_of(e.value)
                o_sel.set_options(list(opts))
                # 규칙이 하나뿐인 모델(PM95 13→15 등)은 고를 것이 없다 — 바로 채운다.
                o_sel.value = list(opts)[0] if len(opts) == 1 else None
            m_sel.on_value_change(_on_model)

            def _apply() -> None:
                model, pick = m_sel.value, o_sel.value
                if not model or not pick:
                    ui.notify("모델과 From→To 를 모두 고르세요.", color="warning")
                    return
                r = _rules_of(model).get(pick) or {}
                targets = [x["sn"] for x in (tb.selected or [])] or list(failed)
                for sn in targets:
                    manual[sn] = {"model": model, "from": str(r.get("from", "")),
                                  "to": str(r.get("to", ""))}
                ui.notify(f"{len(targets):,}대에 {model} {pick} 을(를) 지정했습니다.",
                          color="positive")
                on_change()

            def _clear() -> None:
                targets = [x["sn"] for x in (tb.selected or [])] or list(failed)
                for sn in targets:
                    manual.pop(sn, None)
                ui.notify(f"{len(targets):,}대의 지정을 취소했습니다.", color="warning")
                on_change()

            ui.button("지정", icon="done_all", on_click=_apply).props("unelevated color=primary")
            ui.button("지정 취소", icon="backspace", on_click=_clear).props("flat color=grey-8")

        ui.label("행을 고르면 고른 것에만, 아무것도 고르지 않으면 전체에 적용합니다.") \
            .classes("text-caption text-grey-6")

        tb = ui.table(columns=_UNKNOWN_COLS,
                      rows=unknown_rows(failed, manual, category),
                      row_key="sn", selection="multiple") \
            .classes("w-full").props("dense flat bordered")
        if len(failed) > 8:
            tb.style("max-height:300px")


def open_organic_dialog(g: dict, me: str, refresh) -> None:
    from app.auth import current_user_id, is_admin

    if not is_admin():
        ui.notify("요청 처리는 관리자만 가능합니다.", color="warning")
        return
    uid = current_user_id()
    rep = g["rep"]
    ctx: dict = {"blocks": [], "plan_by_idx": {}, "overall": {}, "note": ""}

    # 팝업을 **페이지 바닥**에 만든다. 이 함수는 메일 목록(@ui.refreshable) 안의 버튼에서
    # 불리는데, 거기서 그대로 만들면 팝업이 그 패널의 자식이 된다. 그러면 판별·동기화가
    # 끝나며 패널을 다시 그릴 때 팝업이 통째로 사라진다 — 등록 직후 창이 닫히고 재판별이
    # 도는 것처럼 보였던 원인이다(실측 2026-08-10).
    with ui.context.client.content:
        dialog = ui.dialog()

    with dialog, ui.card().classes("w-full").style(
        "max-width:960px; max-height:92vh; overflow-y:auto"
    ):
        ui.label(f"요청 처리 (AI 해석) · {rep.get('subject', '')[:52]}").classes(
            "text-subtitle1 text-weight-bold")
        ui.label(f"발신: {rep.get('sender', '')}").classes("text-caption text-grey-7")

        body_area = ui.column().classes("w-full")
        with body_area:
            with ui.row().classes("items-center gap-2"):
                ui.spinner()
                ui.label("저장 원문 로드 · 블록 분해 · AI 해석 중…").classes("text-grey-7")

        # ── 데이터 로드 + 해석 ─────────────────────────────────────────
        async def load(reinterpret: bool = False, instruction: str = ""):
            from app.mail import attachments as ATT
            from app.mail import service as MS
            from app.whitelist import interpret as INT

            att_dir = rep.get("attachment_dir", "")
            raw = ATT.load_raw(att_dir)
            src = "저장 원문"
            if raw is None:
                raw = await run.io_bound(MS.fetch_raw_by_uidl, rep.get("uidl", ""))
                src = "서버 재조회"
            if raw:
                msg = email.message_from_bytes(raw)
                blocks = ATT.extract_blocks(msg)
                images = ATT.collect_body_images(msg)
                cached = None if (reinterpret or instruction) else ATT.load_plan(att_dir)
                if isinstance(cached, dict) and cached.get("blocks") is not None:
                    interp = cached
                    note = f"{src} · 저장된 AI 해석"
                else:
                    interp = await run.io_bound(
                        INT.interpret_blocks, blocks, rep.get("subject", ""), images, instruction)
                    if interp.get("blocks") or interp.get("overall"):
                        ATT.save_plan(att_dir, {"overall": interp.get("overall", {}),
                                                "blocks": interp.get("blocks", [])})
                    note = (f"{src} · AI 해석"
                            + (f" · 본문 이미지 {len(images)}장" if images else "")
                            + (" · 관리자 지시 반영" if instruction else ""))
                ctx["blocks"] = blocks
                ctx["plan_by_idx"] = {b["index"]: b for b in interp.get("blocks", [])}
                ctx["overall"] = dict(interp.get("overall", {}) or {})
                ctx["note"] = (interp.get("error", "") + " · " + note).strip(" ·")
            else:
                ctx["blocks"] = [{"source": "본문(원문 없음 — 저장 SN 사용)", "text": "",
                                  "serials": _dedup(g.get("serials") or [])}]
                ctx["plan_by_idx"], ctx["overall"] = {}, {}
                ctx["note"] = "원문을 찾지 못했습니다 — 아래 지시로 유형을 알려주세요."
            _render()

        def _mail_ctx() -> dict:
            from app.pages.whitelist_mail import _sender_addr_name

            addr, nm = _sender_addr_name(rep.get("sender", ""))
            return {"mail_id": rep["id"], "user_id": uid, "requester_addr": addr,
                    "requester_name": nm, "message_id": rep.get("message_id", ""),
                    "lang": rep.get("lang", "")}

        # ── SN 대상 ───────────────────────────────────────────────────
        def _all_sns() -> list[str]:
            return _dedup([s for b in ctx["blocks"] for s in b["serials"]])

        def _new_sns() -> list[str]:
            out: list[str] = []
            for i, b in enumerate(ctx["blocks"]):
                if (ctx["plan_by_idx"].get(i, {}).get("type") or "skip") != "skip":
                    out += b["serials"]
            return _dedup(out) or _all_sns()

        # ── 공통 실행(청크 + 진행률) ───────────────────────────────────
        async def _run_items(targets, fn, label, write, invalid=0, invalid_reasons=None):
            ctx["result"].clear()
            if not targets:
                msg = "처리할 SN 이 없습니다."
                if invalid:
                    msg += f" · 신규 무효 {invalid:,}건 — {'; '.join(invalid_reasons or [])}"
                ui.notify(msg, color="warning", multi_line=True)
                return
            ctx["prog"].set_visibility(True)
            ctx["prog"].set_value(0)
            total, done, ok, fails, dry = len(targets), 0, 0, [], None
            for chunk in _chunks(targets, _CHUNK):
                res = await run.io_bound(fn, chunk)
                ok += res.get("success", 0)
                dry = res.get("dryRun") if dry is None else dry
                fails += [d for d in res.get("details", []) if not d.get("ok")]
                done += len(chunk)
                ctx["prog"].set_value(done / total)
                ctx["status"].set_text(
                    f"{label} · 진행 {done:,}/{total:,} · 성공 {ok:,} · 실패/미등록 {len(fails):,}")
            ctx["prog"].set_visibility(False)
            mode = "미리보기(dry-run)" if dry else "실제 처리"
            with ctx["result"]:
                color = "text-green-9" if not fails else "text-orange-9"
                extra = f" · 신규 무효 {invalid:,}" if invalid else ""
                ui.label(f"[{mode}] {label} — 성공 {ok:,} / 실패·미등록 {len(fails):,}{extra}").classes(
                    f"text-subtitle2 {color}")
                if invalid and invalid_reasons:
                    ui.label("무효 사유: " + "; ".join(invalid_reasons)
                             + " → 모델/OS 확인 또는 규칙 등록").classes("text-caption text-red-9")
                if fails:
                    ui.label("실패·미등록 예시: " + ", ".join(
                        str(f.get("sn", "")) for f in fails[:15])).classes("text-caption text-red-9")
            if not dry:
                # 보낸 값을 기대값으로 남겨 ② 확인이 FOTA 에서 다시 읽어 대조한다.
                # 실패가 섞여 있어도 확인은 띄운다 — 무엇이 안 들어갔는지 봐야 한다.
                ctx["verify_items"] = list(targets)
                ctx["verify_kind"] = label
                ctx["verify_result"] = None
                after_panel.refresh()
            if not dry and not fails:
                # 여기서 창을 닫으면 ② 확인·③ 회신을 볼 수 없다 — 닫는 것은 회신 단계의 몫.
                ui.notify("처리 완료 · ② 확인으로 진행하세요.", color="positive")
            return {"dry": bool(dry), "ok": not fails, "fails": fails}

        # ── ② 확인 · ③ 메일 회신 ───────────────────────────────────────
        # 전송 응답만 믿지 않는다. FOTA 에서 다시 읽어 대조한 뒤 회신한다.
        async def _do_verify():
            items = ctx.get("verify_items") or []
            if not items:
                ui.notify("확인할 대상이 없습니다.", color="warning")
                return
            res = await WV.run_verify(uid, items, ctx.get("verify_kind", ""))
            ctx["verify_result"] = res
            after_panel.refresh()
            if res.get("error"):
                ui.notify(res["error"], color="negative")
            else:
                ui.notify(f"일치 {res['matched']}/{res['total']}",
                          color="positive" if res["ok"] else "negative")

        @ui.refreshable
        def after_panel() -> None:
            # 처리 전에도 두 단계를 **자리부터** 보여준다 — 아무것도 안 그리면
            # 확인·회신 단계가 없는 화면으로 보인다.
            items = ctx.get("verify_items") or []
            from app.whitelist import mailer as WM

            from app.pages.whitelist_mail import _sender_addr_name

            addr, to_name = _sender_addr_name(rep.get("sender", ""))
            lang = rep.get("lang") or ("ko" if any(
                "가" <= c <= "힣" for c in (rep.get("subject") or "")) else "en")

            with ui.card().classes("w-full q-mt-sm"):
                with ui.row().classes("w-full items-center gap-3"):
                    ui.label("② 확인"
                             + (f" · {ctx.get('verify_kind', '')} · 대상 {len(items):,}대"
                                if items else "")).classes("text-subtitle2")
                    ui.space()
                    ui.button("FOTA 에서 확인", icon="fact_check", on_click=_do_verify) \
                        .props("unelevated color=primary dense").set_enabled(bool(items))
                res = ctx.get("verify_result")
                if not items:
                    ui.label("① 처리를 먼저 실행하세요.").classes("text-caption text-grey-7")
                elif res is None:
                    ui.label("아직 확인하지 않았습니다.").classes("text-caption text-grey-7")
                else:
                    WV.verify_table(res)

            if not items:
                with ui.card().classes("w-full"):
                    ui.label("③ 메일 회신").classes("text-subtitle2")
                    ui.label("① 처리를 먼저 실행하세요.").classes("text-caption text-grey-7")
                return

            with ui.card().classes("w-full"):
                ui.label("③ 메일 회신").classes("text-subtitle2")
                ui.label(f"받는 사람: {addr or '(주소 없음)'}").classes(
                    "text-caption text-grey-7")
                draft = WM.build_reply_draft(
                    to_name=to_name,
                    devices=[{"sn": i["sn"]} for i in items], lang=lang,
                    kind=ctx.get("verify_kind", ""), new_date=ctx.get("new_date", ""))
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
                                orig_mail_id=rep.get("id"))
                    finally:
                        send_btn.props(remove="loading")
                    if ok:
                        ui.notify("회신을 보냈습니다.", color="positive")
                        _mark_done()
                    else:
                        ui.notify(f"회신 발송 실패: {msg}", color="negative", multi_line=True)

                with ui.row().classes("items-center gap-2"):
                    send_btn = ui.button("회신 보내기", icon="send", on_click=_send) \
                        .props("unelevated color=primary")
                    ui.button("회신 건너뛰기", icon="skip_next",
                              on_click=_mark_done).props("flat color=grey-8")

        def _confirm(coro_factory, label):
            with ui.dialog() as c, ui.card():
                ui.label("실제 처리 확인").classes("text-h6")
                ui.label(f"{label} 을(를) 실제 FOTA 에 반영합니다.").classes("text-negative")

                async def _go():
                    c.close()
                    await coro_factory()
                with ui.row().classes("justify-end gap-2"):
                    ui.button("취소", on_click=c.close).props("flat")
                    ui.button("실행", on_click=_go).props("color=negative")
            c.open()

        # ── 유형별 실행 UI ─────────────────────────────────────────────
        def _date_mode():
            ov = ctx["overall"]
            sns = _all_sns()
            with ui.row().classes("items-end gap-2"):
                d_in = ui.input("변경할 등록일(YYYY-MM-DD)", value=ov.get("date", "")).props(
                    "outlined dense").classes("w-48")
                ui.button("현재 등록일 조회(샘플)", icon="search",
                          on_click=lambda: _query_dates()).props("outline dense")
            cur_box = ui.column().classes("w-full gap-0")

            async def _query_dates():
                sample = sns[:10]
                cur_box.clear()
                with cur_box:
                    ui.label("조회 중…").classes("text-caption text-grey-6")

                def _work():
                    sess = WF._session_from_user(uid)
                    out = []
                    for sn in sample:
                        try:
                            recs = WF.find_records(sess, sn) if sess else []
                        except Exception:  # noqa: BLE001
                            recs = []
                        if recs:
                            r0 = recs[0]
                            out.append((sn, r0.get("date", ""), r0.get("osType", "")))
                        else:
                            out.append((sn, "", ""))
                    return out
                res = await run.io_bound(_work)
                cur_box.clear()
                with cur_box:
                    ui.label(f"현재 등록일 (샘플 {len(res)} / 전체 {len(sns):,}건)").classes(
                        "text-caption text-weight-bold")
                    for sn, d, ot in res:
                        txt = f"  {sn} · 현재 {d or '(미등록)'}" + (f" · {ot}" if ot else "")
                        ui.label(txt).classes(
                            "text-caption " + ("text-grey-8" if d else "text-orange-9"))

            ui.label("표의 값 그대로 FOTA DB 에 반영합니다. "
                     "androidApi·osType·partnumber 는 변경하지 않습니다.") \
                .classes("text-caption text-grey-8")
            _dtbl = ui.table(columns=DATE_COLS,
                             rows=date_rows(sns, (d_in.value or "").strip()),
                             row_key="sn").classes("w-full").props("dense flat bordered")
            if len(sns) > 8:
                _dtbl.style("max-height:280px")
            d_in.on_value_change(lambda e: (
                _dtbl.__setattr__("rows", date_rows(sns, (d_in.value or "").strip())),
                _dtbl.update()))

            async def _do(write):
                nd = (d_in.value or "").strip()
                try:
                    end = _plus_year(date.fromisoformat(nd))
                except ValueError:
                    ui.notify("등록일 형식을 확인하세요(YYYY-MM-DD).", color="warning")
                    return
                # 웹 API 가 아니라 FOTA DB 직접 — 한 트랜잭션이라 하나라도 어긋나면
                # 전부 되돌아간다(메일 팝업과 같은 경로).
                res = await run.io_bound(lambda: S.change_dates_db(
                    sns=sns, new_date=nd, processed_by=me, mail=_mail_ctx(),
                    dry_run=not write))
                ctx["result"].clear()
                with ctx["result"]:
                    for ln in res.get("lines", []):
                        ui.label(ln).classes("text-caption font-mono") \
                            .style("color:#b3261e" if ln.lstrip().startswith("✘") else "")
                if not res.get("ok"):
                    ui.notify(res["lines"][0] if res.get("lines") else "변경 실패",
                              color="negative", multi_line=True)
                    return
                if res.get("dry"):
                    ui.notify(f"dry-run · DB 미변경 · 대상 {len(sns):,}대", color="warning")
                    return
                ui.notify(f"등록일 변경 완료 · {len(sns):,}대 (DB 반영)", color="positive")
                ctx["verify_items"] = [{"sn": s, "endDate": end} for s in sns]
                ctx["verify_kind"] = "등록일 변경"
                ctx["verify_result"] = None
                ctx["new_date"] = nd
                after_panel.refresh()
            with ui.row().classes("gap-2 q-mt-xs"):
                ui.button("미리보기(dry-run)", icon="visibility",
                          on_click=lambda: _do(False)).props("outline")
                ui.button("등록일 변경 실행", icon="event",
                          on_click=lambda: _confirm(lambda: _do(True),
                                                    f"등록일 변경 {len(sns):,}건")).props(
                    "color=deep-orange")

        def _new_mode():
            """신규 등록 — 모델별로 표를 나누고, 올리는 것은 한 번에.

            모델은 메일에서 읽지 않고 **포털 단말 마스터에서 S/N 으로 받아온다**
            (사용자 확정 2026-08-10). 첨부 서식이 파트너마다 달라 거기서 뽑으면 계속 깨진다.
            ostype 이 모델마다 달라 표는 나누지만, FOTA 엑셀은 행마다 ostype 을 따로 담을
            수 있어 업로드는 한 번이면 된다.
            """
            from app.whitelist import portal_lookup as PLK

            # 포털이 모델을 못 준 S/N 에 사람이 지정한 값. {S/N: {model, from, to}}
            # 구분(category)을 바꾸면 화면을 통째로 다시 그리므로 ctx 에 둬야 남는다.
            manual: dict = ctx.setdefault("manual", {})
            sns = _new_sns()
            look = ctx.get("lookup")
            if look is None:
                async def _go():
                    ctx["lookup"] = await run.io_bound(PLK.lookup, sns)
                    _render()
                with ui.row().classes("items-center gap-2"):
                    ui.spinner(size="sm")
                    ui.label(f"포털에서 모델 조회 중 — S/N {len(sns):,}건") \
                        .classes("text-caption text-grey-8")
                ui.timer(0.05, _go, once=True)
                return

            cat = ui.select(S.CATEGORIES,
                            value=S.category_for_sender(rep.get("sender", "")),
                            label="구분").props("outlined dense").classes("w-32")
            ok_n = len(sns) - len(look.failed)
            ui.label(f"포털 조회 {ok_n:,}/{len(sns):,} · 모델별로 나눠 보여줍니다. "
                     "‘상태 ⚠’ 행은 제외되고, 올리는 것은 한 번에 합니다.") \
                .classes("text-caption text-grey-8")
            if look.error:
                ui.label("⚠ " + look.error).classes("text-caption text-orange-9")

            groups = look.by_model(sns)          # {모델: [S/N …]}  순서는 입력 순
            picks: dict[str, dict] = {}          # 모델 → {from, to, ui}

            def _build_model(m: str) -> list[dict]:
                p = picks.get(m) or {}
                return [S.build_device(sn, p.get("from", ""), p.get("to", ""), m,
                                       cat.value or S.DEFAULT_CATEGORY)
                        for sn in groups.get(m, [])]

            def _manual_devices() -> list[dict]:
                """사람이 모델·OS 를 지정한 행 — 포털이 모델을 준 것과 똑같이 취급한다."""
                return [S.build_device(sn, a["from"], a["to"], a["model"],
                                       cat.value or S.DEFAULT_CATEGORY)
                        for sn, a in manual.items() if sn in look.failed]

            def _build_all() -> list[dict]:
                out: list[dict] = []
                for m in groups:
                    out.extend(_build_model(m))
                out.extend(_manual_devices())
                return out

            tables: dict[str, object] = {}

            def _redraw():
                for m, tb in tables.items():
                    tb.rows = upload_rows(_build_model(m))
                    tb.update()

            for m, lst in groups.items():
                rules = [r for r in S.rules_for(m)
                         if r["category"] == (cat.value or S.DEFAULT_CATEGORY)] or S.rules_for(m)
                with ui.card().classes("w-full q-mt-xs"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label(f"{m} · {len(lst):,}대").classes("text-subtitle2")
                        if len(rules) == 1:
                            r = rules[0]
                            picks[m] = {"from": str(r["from"]), "to": str(r["to"])}
                            ui.label(f"{r['from']} → {r['to']}  ostype {r['ostype']}") \
                                .classes("text-caption text-grey-8")
                            ui.label("규칙 1개 · 자동").classes("text-caption text-green-9")
                        elif not rules:
                            picks[m] = {"from": "", "to": ""}
                            ui.label("규칙 없음 — [설정 → WhiteList 규칙]에 추가하세요") \
                                .classes("text-caption text-orange-9")
                        else:
                            picks[m] = {"from": "", "to": ""}
                            opts = {f"{r['from']}→{r['to']}": r for r in rules}
                            ui.label(f"규칙 {len(rules)}개 — OS 를 고르세요") \
                                .classes("text-caption text-orange-9")

                            def _pick(e, mm=m, oo=opts):
                                r = oo.get(e.value) or {}
                                picks[mm] = {"from": str(r.get("from", "")),
                                             "to": str(r.get("to", ""))}
                                _redraw()
                            ui.select(list(opts), label="From→To",
                                      on_change=_pick).props("outlined dense").classes("w-40")
                    tb = ui.table(columns=UPLOAD_COLS, rows=upload_rows(_build_model(m)),
                                  row_key="sn").classes("w-full").props("dense flat bordered")
                    if len(lst) > 8:
                        tb.style("max-height:240px")
                    tables[m] = tb

            if look.failed:
                render_unknown_models(look.failed, manual,
                                      cat.value or S.DEFAULT_CATEGORY, _render)

            cat.on_value_change(lambda e: _render())

            async def _do(write):
                devs, invalid, reasons = [], 0, []
                for d in _build_all():
                    if d["valid"]:
                        devs.append({"sn": d["sn"], "api": d["api"], "endDate": d["endDate"],
                                     "ostype": d["ostype"], "partnumber": "",
                                     "model": d["model"], "cur": d["cur"], "upg": d["upg"]})
                    else:
                        invalid += 1
                        rr = ", ".join(d["errors"])
                        if rr not in reasons and len(reasons) < 3:
                            reasons.append(rr)
                # 지정한 행은 _build_all 이 이미 세었다 — 남은 것만 실패로 친다.
                invalid += len([s for s in look.failed if s not in manual])
                parts = [f"{m} {picks.get(m, {}).get('from', '')}"
                         f"→{picks.get(m, {}).get('to', '')}" for m in groups]
                parts += sorted({f"{a['model']} {a['from']}→{a['to']}(수동)"
                                 for sn, a in manual.items() if sn in look.failed})
                label = " · ".join(parts)
                # 신규는 엑셀 업로드, 이미 등록된 SN 은 덮어쓴다(사용자 확정 2026-08-10).
                # ostype 은 행마다 다르므로 모델이 섞여도 한 번에 보낸다.
                res = await _run_items(
                    devs, lambda ch: S.register_devices(uid, ch, allow_write=write),
                    f"신규 등록 · {label}", write, invalid, reasons)
                # 실제로 올라간 건만 등록 내역에 남긴다 — 없으면 중복 검사도 이 SN 을 모른다.
                if res and not res["dry"] and res["ok"]:
                    rec = await run.io_bound(lambda: S.record_registration(
                        devices=devs, mail=_mail_ctx(), processed_by=me,
                        category=cat.value or S.DEFAULT_CATEGORY,
                        memo=f"[AI 해석 · 신규 등록 {label}]"))
                    if rec.get("ok"):
                        ui.notify(f"등록 내역 기록 · {rec['request']['id']}", color="positive")

            n_manual = len([s for s in look.failed if s in manual])
            n_ready = sum(len(v) for v in groups.values()) + n_manual
            with ui.row().classes("gap-2 q-mt-sm"):
                ui.button("미리보기(dry-run)", icon="visibility",
                          on_click=lambda: _do(False)).props("outline")
                ui.button("신규 등록 실행", icon="playlist_add_check",
                          on_click=lambda: _confirm(lambda: _do(True),
                                                    f"신규 등록 {n_ready:,}건"
                                                    + (f" · 수동 지정 {n_manual:,}건"
                                                       if n_manual else ""))).props(
                    "color=deep-orange")
                ui.button("모델 다시 조회", icon="refresh",
                          on_click=lambda: (ctx.update(lookup=None), _render())).props("flat")

        def _force_type(t):
            ctx["overall"]["request_type"] = t
            _render()

        def _instruction_box():
            with ui.expansion("Claude 에게 직접 지시 (잘못 판별했을 때)", icon="chat").classes(
                    "w-full").props("dense"):
                ui.label("예: '이건 전부 등록일 변경, 7/21로' · 'PM95 13→15 신규 등록으로 처리' · "
                         "'첨부메일들도 등록일 변경 대상이야'").classes("text-caption text-grey-6")
                ins = ui.textarea(placeholder="지시를 입력하고 '다시 해석'을 누르세요").props(
                    "outlined autogrow").classes("w-full")

                async def _reask():
                    if not (ins.value or "").strip():
                        ui.notify("지시를 입력하세요.", color="warning")
                        return
                    body_area.clear()
                    with body_area:
                        with ui.row().classes("items-center gap-2"):
                            ui.spinner()
                            ui.label("지시 반영해 다시 해석 중…").classes("text-grey-7")
                    await load(reinterpret=True, instruction=ins.value.strip())
                ui.button("이 지시로 다시 해석", icon="autorenew", on_click=_reask).props(
                    "color=primary")

        def _detail_expander():
            with ui.expansion("세부 (블록별 AI 판정 · 참고용)", icon="list").classes(
                    "w-full").props("dense"):
                for i, b in enumerate(ctx["blocks"]):
                    pl = ctx["plan_by_idx"].get(i, {})
                    t = _TYPE_KO_SHORT.get(pl.get("type", ""), pl.get("type", "-"))
                    line = (f"[{t}] {b['source'][:46]} · SN {len(b['serials']):,}"
                            + (f" · {pl.get('model', '')} {pl.get('from_os', '')}"
                               f"→{pl.get('to_os', '')}" if pl.get("model") else "")
                            + (f" · {pl.get('date', '')}" if pl.get("date") else ""))
                    ui.label(line).classes("text-caption text-grey-8")
                    if pl.get("note"):
                        ui.label("    " + pl["note"]).classes("text-caption text-grey-6")

        # ── 화면 ───────────────────────────────────────────────────────
        def _render():
            body_area.clear()
            with body_area:
                overall = ctx["overall"]
                rtype = overall.get("request_type", "")
                ui.label(f"소스 블록 {len(ctx['blocks'])}개 · 고유 SN {len(_all_sns()):,}개").classes(
                    "text-subtitle2")
                if ctx.get("note"):
                    _hard = any(k in ctx["note"] for k in ("실패", "없음", "못 "))
                    ui.label(("⚠ " if _hard else "· ") + ctx["note"]).classes(
                        "text-caption " + ("text-orange-9" if _hard else "text-grey-6"))

                # AI 판정
                _c = {"date": "#2e7d32", "new": "#1565c0"}.get(rtype, "#b26a00")
                with ui.card().classes("w-full").style(f"background:#f6f8f7; border-left:4px solid {_c}"):
                    ui.label("AI 판정: " + _TYPE_KO.get(rtype, "미판별")).classes(
                        "text-subtitle1 text-weight-bold")
                    if overall.get("reason"):
                        ui.label("· " + overall["reason"]).classes("text-caption text-grey-7")

                # 진행/결과 위젯(실행 UI 가 참조)
                ctx["prog"] = ui.linear_progress(value=0).props("instant-feedback").classes("w-full")
                ctx["prog"].set_visibility(False)
                ctx["status"] = ui.label().classes("text-caption text-grey-8")
                ctx["result"] = ui.column().classes("w-full")

                # ① 처리 — 유형 잠금 실행 UI
                with ui.card().classes("w-full"):
                    ui.label("① 처리").classes("text-subtitle2")
                    if rtype == "date":
                        _date_mode()
                    elif rtype == "new":
                        _new_mode()
                    else:
                        ui.label("유형을 확정하지 못했습니다(혼합/불명). 아래 지시로 알려주시거나 "
                                 "유형을 직접 고르세요.").classes("text-orange-9")
                        with ui.row().classes("gap-2"):
                            ui.button("등록일 변경으로 처리", icon="event",
                                      on_click=lambda: _force_type("date")).props("outline")
                            ui.button("신규 등록으로 처리", icon="playlist_add_check",
                                      on_click=lambda: _force_type("new")).props("outline")

                after_panel()          # ② 확인 · ③ 메일 회신

                _instruction_box()
                _detail_expander()

                with ui.row().classes("w-full justify-end gap-2 q-mt-sm"):
                    ui.button("처리 완료(숨김)", icon="check_circle",
                              on_click=_mark_done).props("flat color=grey-8")
                    ui.button("닫기", on_click=dialog.close).props("flat")

        def _mark_done():
            try:
                from app.whitelist import mail_state as MST
                from app.whitelist import mail_triage as MTR
                MST.set_status(g["key"], MST.DONE, by=me, note="유기적 처리",
                               rep_ts=MTR.newest_ts(g))
            except Exception:  # noqa: BLE001
                pass
            dialog.close()
            ui.timer(0.01, refresh, once=True)

    dialog.open()
    ui.timer(0.05, load, once=True)


_TYPE_KO_SHORT = {"date": "날짜변경", "new": "신규", "skip": "제외"}
