"""공개 RN 선별표 편집기 — RN 승인(/reviews)와 ② 검수하기(작성자) 화면 공용.

반영/미반영 토글 + '수정 내용' 입력 + 입력값(GMS/SDK/…) 수정 + 저장·재생성.
저장하면 반영 항목과 수정 내용으로 RN 을 다시 만들고(rebuild+regenerate) 버전 이력을 남긴다.
(메일 알림·검수 상태 변경 같은 역할별 동작은 호출부에서 별도로 처리한다.)
"""
from __future__ import annotations

from datetime import datetime
from functools import partial as _partial

from nicegui import run, ui

from app.pipeline import history as H
from app.theme import AUTOFIT_CLASS
from app.pipeline.state import get_state, update_state

# 입력 항목 정의·칸 그리기는 RN 승인 화면과 동일한 것을 재사용한다
# (최초 릴리즈의 항목 삭제·추가도 그 컴포넌트가 함께 처리한다).
from app.pages.mid_review import (
    _TOP_LEVEL_KEYS,
    note_min_height,
    render_input_fields,
)

_HDR = (("번호", "3rem"), ("이슈 제목", "14rem"), ("반영", "9rem"),
        ("작성자", "5rem"), ("고객", "6rem"), ("redmine", "5rem"), ("수정 내용", ""),
        ("", "2.5rem"))   # 맨 끝 = 직접 추가한 행의 삭제 버튼 자리

#: RN for Sales 에서 **미반영으로 뺀 이유**. 고를 수 있는 값은 넷뿐이다
#: (사용자 확정 2026-09-07). 자유 입력을 두지 않는 이유는 나중에 세어 보기 위해서다.
EXCLUDE_NOTES = ("내부 이슈", "특정 고객 전용", "이슈 확인 안됨", "SW 요청으로 제외")

#: 비고 열은 **RN for Sales 에만** 붙는다. 전체 이력 RN·RN 승인 화면은 그대로다.
_NOTE_HDR = ("비고", "11rem")
_NOTE_AT = 7          # '수정 내용' 다음, 삭제 버튼 앞


def _w(i: int) -> str:
    w = _HDR[i][1]
    if not w:
        return "flex:1 1 20rem; min-width:20rem; white-space:normal; word-break:break-word"
    return f"width:{w}; flex:0 0 {w}; white-space:normal; word-break:break-word"


def _row_style(active: bool) -> str:
    # min-width:max-content — 표가 넘칠 때 행이 내용 폭까지 늘어나
    # 하이라이트 배경이 오른쪽 끝(마지막 셀)까지 칠해지도록 한다.
    s = "border-top:1px solid #eceeea; min-width:max-content;"
    if active:
        s += "background:#fff7d6;"
    return s


def render_selection_editor(mail_id, *, on_saved=None, editable: bool = True,
                            save_label: str = "저장 · RN 재생성",
                            with_full: bool = True, with_sales: bool = True,
                            exclude_note: bool = False) -> None:
    """선별표(+입력값) 편집기. editable=False 면 읽기 전용으로만 보여준다.

    exclude_note=True 면 **비고 열**을 붙인다 — 미반영으로 뺀 항목의 사유를 고른다.
    RN for Sales 에서만 쓴다(사용자 확정 2026-09-07). 고르는 즉시 저장한다 —
    '저장 · 재생성' 은 문서를 다시 만드는 무거운 일이라, 사유 하나 적자고 누르게 하면
    안 된다.

    with_full=False 면 저장할 때 **RN for Sales 만** 다시 만든다. RN 승인(1차)이 끝나면
    전체 이력 RN 은 확정·저장된 문서라, 그 뒤에 공개 항목을 고른다고 이력본까지 바뀌면
    승인받은 문서와 파일서버에 올라간 문서가 달라진다.
    """
    from app.auth import current_username

    from app.pipeline.rn_select import normalize_rows as _norm_sel_rows

    st = get_state(mail_id) or {}
    # 중간 검수와 같은 규칙을 통과시킨다 — 기본값은 반영, '수정 내용'은 빈칸 금지.
    rows = _norm_sel_rows(st.get("rn_select_rows"),
                          edited=bool(st.get("rn_select_edited_at")))
    # 시스템 항목(보안패치·뭉침)도 같은 표에서 다룬다(RN 승인 화면과 같다).
    from app.pipeline.rn_research import with_system_rows as _with_sys

    rows = _with_sys(st, rows)
    comps = st.get("rn_components", {}) or {}
    applies: dict = {}
    notes: dict = {}
    locked = not editable

    # 비고 열이 붙으면 뒤 열의 자리가 한 칸씩 밀린다. 모듈의 _w 를 **지역으로 가려**
    # 부르는 자리를 그대로 두고 열 정의만 바꾼다.
    _hdr = (_HDR[:_NOTE_AT] + ((_NOTE_HDR,) if exclude_note else ()) + _HDR[_NOTE_AT:])
    _i_del = len(_hdr) - 1

    def _w(i: int) -> str:      # noqa: F811  (모듈 것과 같은 규칙, 열만 늘어난다)
        w = _hdr[i][1]
        if not w:
            return ("flex:1 1 20rem; min-width:20rem; white-space:normal; "
                    "word-break:break-word")
        return f"width:{w}; flex:0 0 {w}; white-space:normal; word-break:break-word"

    def _save_note(row: dict, value: str) -> None:
        """비고를 **그 자리에서** 저장한다 — 다른 칸의 편집을 건드리지 않는다."""
        row["exclude_note"] = value or ""
        st_now = get_state(mail_id) or {}
        saved = [dict(x) for x in (st_now.get("rn_select_rows") or [])]
        hit = False
        for rr in saved:
            if str(rr.get("no")) == str(row.get("no")):
                rr["exclude_note"] = value or ""
                hit = True
                break
        if hit:
            update_state(mail_id, rn_select_rows=saved)
            ui.notify(f"비고 저장 — {value or '(지움)'}", color="positive")

    ui.label("공개 RN 선별표 — '반영'한 항목(노란색)이 RN 에 들어갑니다").classes("text-subtitle2")
    # live_rows = 화면에 실제로 놓인 행(저장 대상). '행 추가'로 늘어난다.
    live_rows: list[dict] = list(rows)

    count_lbl = ui.label("").classes("text-caption text-grey-7")

    def _sync_count() -> None:
        n_y = sum(1 for rr in live_rows if rr.get("include") == "Y")
        count_lbl.set_text(
            f"총 {len(live_rows)}건 · 반영 {n_y}건 / 미반영 {len(live_rows) - n_y}건")

    # flex-shrink:0 — 팝업(flex 컬럼) 안에서 표가 세로로 접혀 사라지는 것을 막는다.
    # 세로 스크롤은 팝업 카드가 처리하고, 넓은 표는 가로로만 스크롤한다.
    with ui.element("div").classes("w-full").style(
        "overflow-x:auto; flex-shrink:0; border:1px solid #d5d8d2; border-radius:8px"
    ):
        with ui.row().classes("w-full items-center gap-2 no-wrap px-2 py-1").style(
            "background:#f0f1ef; position:sticky; top:0; z-index:1; font-weight:600; "
            "min-width:max-content"
        ):
            for i, (txt, _w0) in enumerate(_hdr):
                ui.label(txt).classes("text-caption").style(_w(i))
        body = ui.element("div").classes("w-full")

    def _add_row(r: dict, manual: bool = False) -> None:
        """행 하나를 표에 놓는다.

        · 자동 수집 행 = 반영/미반영 토글 + 읽기용 이슈 제목 + 수정 내용
        · 직접 추가한 행 = **수정 내용만** (항상 반영) + 삭제 버튼
        """
        no = str(r.get("no"))
        inc = (r.get("include") == "Y")
        with body:
            row_el = ui.row().classes("w-full items-start gap-2 no-wrap px-2 py-1").style(
                _row_style(inc))
        with row_el:
            ui.label(no).classes("text-caption q-pt-xs").style(_w(0))
            if manual:
                # 이슈가 없는 항목이라 제목·판정 칸을 두지 않는다(정렬만 맞춘다).
                ui.label("직접 추가").classes("text-caption text-grey-6 q-pt-xs").style(_w(1))
                for _i in (2, 3, 4, 5):
                    ui.label("").style(_w(_i))
            else:
                ui.label(str(r.get("issue", ""))).classes("text-body2 q-pt-xs").style(_w(1))
                tg = ui.toggle({"Y": "반영", "X": "미반영"}, value=("Y" if inc else "X")) \
                    .props("no-caps dense unelevated color=grey-5 toggle-color=primary") \
                    .style(_w(2))
                ui.label(str(r.get("team", ""))).classes("text-caption q-pt-xs").style(_w(3))
                ui.label(str(r.get("customer", ""))).classes(
                    "text-caption q-pt-xs").style(_w(4))
                _rm = str(r.get("redmine", "") or "")
                if _rm.startswith("http"):
                    ui.link("#" + _rm.rsplit("/", 1)[-1], _rm, new_tab=True) \
                        .classes("text-caption q-pt-xs").style(_w(5))
                else:
                    ui.label(_rm).classes("text-caption text-grey-7 q-pt-xs").style(_w(5))
            _v = str(r.get("apply_note", "") or "")
            ne = ui.textarea(value=_v, placeholder="수정 필요 시 입력") \
                .classes(AUTOFIT_CLASS) \
                .props('dense outlined autogrow '
                       f'input-style="{note_min_height(_v)}"').style(_w(6))
            ne.set_enabled((manual or inc) and not locked)

            # 비고 — **미반영일 때만** 고를 수 있다. 반영으로 돌리면 비우고 잠근다
            # (실린 항목에 '뺀 이유'가 남아 있으면 다음에 보는 사람이 헷갈린다).
            nsel = None
            if exclude_note:
                nsel = ui.select(list(EXCLUDE_NOTES), value=(
                    str(r.get("exclude_note") or "") or None),
                    with_input=False, clearable=True) \
                    .props("dense outlined").style(_w(_NOTE_AT))
                nsel.set_enabled((not inc) and not locked)

                def _on_note(e, row=r) -> None:
                    _save_note(row, str(e.value or ""))

                nsel.on_value_change(_on_note)

            if manual:
                def _remove(el=row_el, row=r, key=no):
                    live_rows[:] = [x for x in live_rows if x is not row]
                    notes.pop(key, None)
                    el.delete()
                    _sync_count()

                ui.button(icon="delete", on_click=_remove) \
                    .props("flat dense size=sm color=negative").style(_w(_i_del)) \
                    .set_enabled(not locked)
            else:
                ui.label("").style(_w(_i_del))

                def _on_toggle(e, target=ne, el=row_el, row=r, sel=nsel):
                    active = (e.value == "Y")
                    # 반영으로 바꾸면 '수정 내용'이 비어 있지 않게 공개 문구로 채운다
                    # (반영 항목은 RN 에 실릴 문구가 항상 보여야 한다).
                    if active and not (target.value or "").strip():
                        target.value = str(row.get("public", "") or "")
                    target.set_enabled(active and not locked)
                    el.style(replace=_row_style(active))
                    row["include"] = "Y" if active else "X"
                    if sel is not None:
                        sel.set_enabled((not active) and not locked)
                        if active and sel.value:
                            sel.value = None          # 반영으로 돌리면 사유를 지운다
                    _sync_count()

                tg.on_value_change(_on_toggle)
                tg.set_enabled(not locked)
                applies[no] = tg
        notes[no] = ne

    for _r in rows:
        _add_row(_r, manual=bool(_r.get("manual")))
    if not rows:
        ui.label(
            "선별 데이터가 없습니다. Release Note 제작을 실행하면 채워지고, 아래 '행 추가'로 직접 넣을 수도 있습니다."
        ).classes("text-caption text-grey-6")
    _sync_count()

    if not locked:
        def _new_row() -> None:
            """이슈에 없는 항목을 RN 에 넣을 때 — 빈 행을 하나 더 만든다."""
            nums = [int(x.get("no")) for x in live_rows
                    if str(x.get("no", "")).isdigit()]
            r = {"no": (max(nums) + 1) if nums else 1, "issue": "", "team": "",
                 "customer": "", "gubun": "", "include": "Y", "reason": "직접 추가",
                 "public": "", "redmine": "", "sensitive": False, "apply_note": "",
                 "manual": True}
            live_rows.append(r)
            _add_row(r, manual=True)
            _sync_count()

        ui.button("행 추가", icon="add", on_click=_new_row) \
            .props("outline dense size=sm no-caps").classes("q-mt-xs") \
            .tooltip("Redmine 이슈에 없는 항목을 RN 에 직접 넣습니다")

    # ── 입력값(RN 승인자가 채운 값 확인·수정) ─────────────────────────
    ui.separator()
    ui.label("입력 값 (GMS/SDK/Audio/Touch/Wi-Fi/보안패치)").classes("text-subtitle2")
    box = render_input_fields(mail_id, st, enabled=not locked)

    if locked:
        return

    def _collect():
        # 3~8 항목이 비어 있어도 **저장과 RN 재생성을 막지 않는다** — 단말에서 확인해야
        # 알 수 있는 값이라 나중에 채우는 경우가 많다. 대신 무엇이 비었는지 알려 준다
        # (비운 채로 만들면 RN 의 해당 칸이 빈칸으로 나간다).
        missing = box.missing()
        if missing:
            ui.notify("빈 항목이 있는 채로 저장합니다 — " + ", ".join(missing),
                      color="warning", multi_line=True)
        new_rows = []
        for r in live_rows:
            r2 = dict(r)
            no = str(r.get("no"))
            ne = notes.get(no)
            if ne is not None:
                # 사람이 친 문구는 **그대로** 저장한다 — [모듈] 태그를 붙이지 않는다
                # (사용자 확정 2026-08-27).
                r2["apply_note"] = (ne.value or "").strip()
            if r2.get("manual"):
                r2["include"] = "Y"                 # 직접 추가한 행은 항상 반영
                r2["issue"] = r2.get("apply_note", "")   # 표시용(제목 칸이 따로 없다)
                if not r2["apply_note"]:
                    continue                        # 빈 행은 저장하지 않는다
            else:
                tg = applies.get(no)
                r2["include"] = "Y" if (tg and tg.value == "Y") else "X"
            new_rows.append(r2)
        # 저장 직전에도 불변식을 건다(중간 검수와 같다) — '수정 내용'을 지운 채 저장해도
        # 빈칸으로 남지 않는다.
        new_rows = _norm_sel_rows(new_rows)
        vals = box.values()
        comp_vals = {k: v for k, v in vals.items() if k not in _TOP_LEVEL_KEYS}
        top_vals = {k: v for k, v in vals.items() if k in _TOP_LEVEL_KEYS}
        # 화면에서 뺀 항목의 값은 같이 걷어낸다(지운 섹션이 문서에 남지 않게).
        merged = {k: v for k, v in {**comps, **comp_vals}.items()
                  if k not in box.dropped()}
        # 사람이 반영/미반영을 손댔다는 표시 — ②를 다시 돌려도 이 선택을 덮지 않는다
        # (표시가 없으면 ②는 기본값인 '전부 반영'으로 다시 깐다).
        # 사람이 **값을 바꾼** 항목만 수동으로 표시한다 — 표시가 붙으면 ②를 다시 돌려도
        # 그 값은 덮이지 않는다(자동으로 채운 값은 더 나은 출처가 생기면 갱신된다).
        from app.pipeline.rn_research import mark_manual_components

        mark_manual_components(mail_id, merged, top_vals)
        update_state(mail_id, rn_select_rows=new_rows, rn_components=merged,
                     rn_select_edited_at=datetime.now().isoformat(),
                     rn_select_edited_by=current_username() or "", **top_vals)
        box.save_fields(mail_id)
        return True

    async def do_save():
        if _collect() is None:
            return
        n = ui.notification("저장 · RN 재생성 중…", spinner=True, timeout=None)
        _ko: list[str] = []
        try:
            from app.pipeline.rn_research import (
                korean_lines,
                rebuild_revision_from_selection,
                regenerate_rn,
            )
            new_lines, note = await run.io_bound(rebuild_revision_from_selection, mail_id)
            if note:
                new_lines = list((get_state(mail_id) or {}).get("rn_revision_lines") or [])
            draft = "\n".join(f"- {ln}" for ln in new_lines)
            await run.io_bound(
                _partial(regenerate_rn, with_full=with_full, with_sales=with_sales),
                mail_id, draft)
            H.record(mail_id, "rn", "선별표·입력값 수정", actor=current_username(),
                     summary="반영 항목·수정 내용·입력값 수정 후 재생성",
                     text=H.text_from_rn(get_state(mail_id) or {}))
            _ko = korean_lines(new_lines)
        finally:
            n.dismiss()
        ui.notify("저장 완료", color="positive")
        if _ko:
            # RN 본문은 항상 영어다. 문구를 그대로 쓰므로 여기서 잡아야 한다.
            ui.notify(
                f"RN 본문에 한국어가 {len(_ko)}줄 있습니다 — 영어로 고쳐 주세요.\n"
                + "\n".join(f"· {ln[:70]}" for ln in _ko[:3]),
                color="warning", multi_line=True, timeout=12000,
            )
        if on_saved:
            on_saved()

    ui.button(save_label, icon="save", on_click=do_save).props("color=primary").classes("q-mt-sm")
