"""사내 인원 명부 — 관리자가 추가·수정·삭제한다([설정 → 사내 인원]).

여기서 켠 체크가 곧 동작이다:
  · DQA팀     — 그 사람이 보낸 FOTA 테스트 요청이 'DQA FOTA Test 처리' 전용 팝업으로 간다.
  · 처리담당  — 그 사람이 보낸 메일은 완료 안내·회신으로 보고 신규 요청 목록에서 뺀다.

'퇴사'는 표시일 뿐 역할을 자동으로 끄지 않는다. 메일함에 과거 메일이 남아 있어서,
재직 중 보낸 요청은 계속 맞게 분류돼야 한다. 끄거나 지우는 판단은 관리자가 한다.
"""
from nicegui import ui

from app import staff as ST
from app.auth import current_username, is_admin


def render_staff_content() -> None:
    if not is_admin():
        ui.label("관리자만 볼 수 있습니다.").classes("text-caption text-grey-7")
        return
    me = current_username() or ""

    @ui.refreshable
    def table() -> None:
        rows = ST.list_staff()
        n_dqa = sum(1 for r in rows if r["is_dqa"])
        n_hd = sum(1 for r in rows if r["is_handler"])
        n_left = sum(1 for r in rows if r["status"] == ST.LEFT)
        with ui.row().classes("w-full items-center gap-3"):
            ui.label(f"{len(rows)}명 · DQA팀 {n_dqa} · 처리담당 {n_hd} · 퇴사 {n_left}") \
                .classes("text-body2")
            ui.space()
            ui.button("인원 추가", icon="person_add",
                      on_click=lambda: _edit(None)).props("unelevated dense color=primary")

        if not rows:
            ui.label("등록된 인원이 없습니다.").classes("text-caption text-grey-6")
            return

        # 열 정렬은 grid 로 한다(flex + width 는 라벨이 밀려 행마다 어긋난다).
        # 메일 열은 minmax(0,1fr) — 0 을 줘야 좁은 화면에서 줄어든다. 14rem 처럼 최소를
        # 두면 합이 화면보다 커져 표가 밖으로 삐져나간다.
        grid = ("display:grid; align-items:center; column-gap:.5rem; "
                "grid-template-columns:minmax(5rem,9rem) minmax(0,1fr) "
                "3.5rem 3.5rem 4.5rem 4.5rem;")
        ell = "overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0;"

        # 팀별로 묶는다. 팀 없는 사람은 맨 아래.
        teams: dict[str, list[dict]] = {}
        for r in rows:
            teams.setdefault(r["team"] or "", []).append(r)
        order = sorted(k for k in teams if k) + ([""] if "" in teams else [])

        with ui.column().classes("w-full gap-0").style("max-width:100%; min-width:0"):
            with ui.element("div").classes("w-full text-caption text-grey-7").style(
                grid + "padding:0 .25rem .25rem;"
            ):
                for h, extra in (("이름", ""), ("메일 주소", ""),
                                 ("상태", "text-align:center;"),
                                 ("DQA", "text-align:center;"),
                                 ("처리담당", "text-align:center;"), ("", "")):
                    ui.label(h).style(extra)

            for team in order:
                members = sorted(teams[team],
                                 key=lambda r: (r["status"] != ST.ACTIVE, r["name"]))
                n_left = sum(1 for m in members if m["status"] == ST.LEFT)
                with ui.row().classes("w-full items-center gap-2 no-wrap").style(
                    "background:#f1f3f2; padding:.2rem .35rem; margin-top:.4rem;"
                ):
                    ui.label(team or "(팀 없음)").classes("text-body2") \
                        .style("font-weight:700")
                    ui.label(f"{len(members)}명"
                             + (f" · 퇴사 {n_left}" if n_left else "")) \
                        .classes("text-caption text-grey-7")
                for r in members:
                    left = r["status"] == ST.LEFT
                    with ui.element("div").classes("w-full").style(
                        grid + "border-top:1px solid #e0e0e0; padding:.2rem .25rem;"
                        + ("opacity:.55;" if left else "")
                    ):
                        ui.label(r["name"] or "-").classes("text-body2").style(ell)
                        ui.label(r["email"]).classes("text-caption font-mono").style(ell)
                        ui.label(r["status"]).classes("text-caption").style(
                            "text-align:center;" + ("color:#b3261e;" if left else ""))
                        for on in (r["is_dqa"], r["is_handler"]):
                            with ui.element("div").style("text-align:center;"):
                                ui.icon("check" if on else "remove").props("size=xs") \
                                    .classes("text-green-8" if on else "text-grey-4")
                        with ui.row().classes("gap-0 no-wrap justify-end"):
                            ui.button(icon="edit",
                                      on_click=lambda _=None, rr=r: _edit(rr)) \
                                .props("flat dense size=sm")
                            ui.button(icon="delete",
                                      on_click=lambda _=None, rr=r: _confirm_del(rr)) \
                                .props("flat dense size=sm color=negative")

    def _edit(r: dict | None) -> None:
        with ui.dialog() as dlg, ui.card().classes("w-[34rem] max-w-full"):
            ui.label("인원 추가" if r is None else "인원 수정").classes("text-h6")
            name_in = ui.input("이름", value=(r or {}).get("name", "")) \
                .props("outlined dense").classes("w-full")
            mail_in = ui.input("메일 주소", value=(r or {}).get("email", "")) \
                .props("outlined dense").classes("w-full")
            team_in = ui.input("팀", value=(r or {}).get("team", "")) \
                .props("outlined dense").classes("w-full")
            st_sel = ui.select(list(ST.STATUSES), label="상태",
                               value=(r or {}).get("status", ST.ACTIVE)) \
                .props("outlined dense").classes("w-full")
            dqa_cb = ui.checkbox("DQA팀 — FOTA 테스트 요청을 전용 처리로 보낸다",
                                 value=bool((r or {}).get("is_dqa")))
            hd_cb = ui.checkbox("처리담당 — 이 사람이 보낸 메일은 신규 요청에서 뺀다",
                                value=bool((r or {}).get("is_handler")))
            memo_in = ui.input("메모(선택)", value=(r or {}).get("memo", "")) \
                .props("outlined dense").classes("w-full")

            def save() -> None:
                ok, msg = ST.save_staff(
                    id=(r or {}).get("id"), name=name_in.value or "",
                    email=mail_in.value or "", status=st_sel.value or ST.ACTIVE,
                    is_dqa=bool(dqa_cb.value), is_handler=bool(hd_cb.value),
                    team=team_in.value or "", memo=memo_in.value or "", by=me)
                ui.notify(msg, color="positive" if ok else "negative")
                if ok:
                    dlg.close()
                    table.refresh()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("취소", on_click=dlg.close).props("flat")
                ui.button("저장", on_click=save).props("unelevated color=primary")
        dlg.open()

    def _confirm_del(r: dict) -> None:
        with ui.dialog() as dlg, ui.card().classes("w-[30rem] max-w-full"):
            ui.label("명부에서 삭제").classes("text-h6")
            ui.label(f"{r['name'] or '(이름 없음)'} · {r['email']}").classes("text-body2")
            if r["is_dqa"] or r["is_handler"]:
                roles = " · ".join(
                    x for x in ("DQA팀" if r["is_dqa"] else "",
                                "처리담당" if r["is_handler"] else "") if x)
                ui.label(f"이 사람이 보낸 과거 메일의 {roles} 판정이 사라집니다.") \
                    .classes("text-negative text-body2")

            def go() -> None:
                ok, msg = ST.delete_staff(r["id"])
                ui.notify(msg, color="positive" if ok else "negative")
                dlg.close()
                table.refresh()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("취소", on_click=dlg.close).props("flat")
                ui.button("삭제", on_click=go).props("color=negative")
        dlg.open()

    # 카드가 부모보다 넓어지지 않게 못박는다 — 안 그러면 표가 아니라 카드가 밖으로 나간다.
    with ui.card().classes("w-full").style("max-width:100%; min-width:0; overflow-x:hidden"):
        table()
