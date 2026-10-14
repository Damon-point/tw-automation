"""등록 확인 — FOTA 에서 **다시 읽어** 기대값과 대조한 결과를 그린다.

전송 응답만 믿고 회신하지 않는다. 신규 등록도 등록일 변경도 이 화면을 거친다
(사용자 확정 2026-08-06). 일반 등록 팝업(whitelist_mail)과 AI 해석 팝업
(whitelist_organic)이 같은 표를 쓴다.
"""
from nicegui import run, ui

_GRID = ("display:grid; align-items:center; column-gap:.5rem; "
         "grid-template-columns:minmax(0,1fr) 6.5rem 7rem 4rem 1.5rem;")
_ELL = "overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0;"

#: 표에 그릴 최대 줄 수. 2,652대 건을 전부 그리면 화면이 멎는다(사용자 신고
#: 2026-09-16). 넘치면 **안 맞는 것부터** 보여주고 나머지는 수만 알려 준다.
_MAX_ROWS = 200


async def run_verify(uid: int, items: list[dict], label: str = "") -> dict:
    """확인 실행(스피너 포함). 반환은 fota.verify_registration 결과 그대로."""
    from app.whitelist import fota as WF

    n = ui.notification(f"확인 중… {len(items):,}대" + (f" · {label}" if label else ""),
                        spinner=True, timeout=None)
    try:
        return await run.io_bound(WF.verify_registration, uid, items)
    finally:
        n.dismiss()


def verify_table(res: dict) -> None:
    """대조 결과표. res 가 None 이면 아무것도 그리지 않는다."""
    if not res:
        return
    if res.get("error"):
        ui.label(f"✘ {res['error']}").classes("text-red-10")
        return

    ok = res["ok"]
    ui.label(
        f"일치 {res['matched']}/{res['total']}"
        + (f" · 미등록 {res['missing']}" if res["missing"] else "")
        + (f" · 값 불일치 {res['mismatched']}" if res["mismatched"] else "")
    ).classes("text-body2").style(
        "font-weight:700; color:" + ("#1b5e20" if ok else "#b3261e"))

    with ui.element("div").classes("w-full text-caption text-grey-7").style(
        _GRID + "padding:.25rem 0 0;"
    ):
        for h in ("sn", "supportEndDate", "osType", "api", ""):
            ui.label(h)
    rows = res["rows"]
    bad = [r for r in rows if not r["ok"]]
    shown = rows if len(rows) <= _MAX_ROWS else (bad or rows)[:_MAX_ROWS]
    if len(shown) < len(rows):
        ui.label(f"{len(rows):,}대 중 {len(shown):,}줄만 보여줍니다"
                 + (" — 안 맞는 것부터입니다." if bad else " — 모두 일치합니다.")) \
            .classes("text-caption text-grey-7")
    with ui.scroll_area().classes("w-full").style("max-height:34vh"):
        for r in shown:
            with ui.element("div").classes("w-full").style(
                _GRID + "border-top:1px solid #e0e0e0; padding:.15rem 0;"
            ):
                ui.label(r["sn"]).classes("text-caption font-mono").style(_ELL)
                ui.label(r["date"] or "-").classes("text-caption").style(_ELL)
                ui.label(r["osType"] or "-").classes("text-caption").style(_ELL)
                ui.label(r["androidApi"] or "-").classes("text-caption")
                ui.icon("check_circle" if r["ok"] else "error").props("size=xs") \
                    .classes("text-green-8" if r["ok"] else "text-red-10")
            if r["diffs"]:
                ui.label(" · ".join(r["diffs"])).classes(
                    "text-caption text-red-10").style("padding-left:.5rem")

    if not ok:
        ui.label("일치하지 않는 건이 있습니다 — 다시 보내거나 FOTA 를 직접 확인하세요.") \
            .classes("text-caption text-red-10")
