"""포털 편집 검수 페이지 (/portaledit).

메일 구동 ④ 오케스트레이터가 담은 문서들(Service Portal 문서 + OS/FW Release Notes 페이지)을
문서별로 알맞은 로컬 에디터(SmartEditor2 / TinyMCE)로 검수·수정·로컬 저장한다.
**프로덕션 무접촉** — 여기서 저장은 검수 작업공간(로컬 JSON)에만 반영된다.
"""
from nicegui import run, ui

from app.pages._editors import (
    preload_smarteditor2,
    preload_tinymce,
    smarteditor2_editor,
    smarteditor2_get_content,
    tinymce_editor,
    tinymce_get_content,
)
from app.auth import current_username
from app.pipeline import history as H
from app.pipeline import review_workspace as WS
from app.theme import apply_theme, page_header

_SOURCE_LABEL = {
    "service_portal": "Service Portal",
    "release_notes": "Knowledge Base",
}
_seq = {"n": 0}


def _change_report(item: dict):
    applied = item.get("applied") or []
    failed = item.get("failed") or []
    err = item.get("error")
    if err:
        with ui.card().classes("w-full bg-red-1"):
            ui.label(f"편집 오류: {err}").classes("text-caption text-red-10")
        return
    color = "bg-blue-1" if applied else "bg-grey-2"
    with ui.card().classes(f"w-full {color}"):
        ui.label(
            f"편집 결과 — 적용 {len(applied)}건 · 미적용 {len(failed)}건 (아래 에디터에 반영됨)"
        ).classes("text-subtitle2")
        for c in applied:
            with ui.column().classes("gap-0 q-mb-xs"):
                ui.label(f'✔ {c.get("why","")}').classes("text-caption text-green-10")
                ui.label(f'− {str(c.get("old",""))[:160]}').classes(
                    "text-caption text-red-9"
                ).style("white-space:pre-wrap;word-break:break-all")
                ui.label(f'+ {str(c.get("new",""))[:160]}').classes(
                    "text-caption text-green-9"
                ).style("white-space:pre-wrap;word-break:break-all")
        for c in failed:
            ui.label(
                f'⚠ 미적용({c.get("reason","")}): {str(c.get("old",""))[:120]}'
            ).classes("text-caption text-orange-10").style("white-space:pre-wrap")


_EVENT_COLOR = {
    "생성": "blue-6", "재생성": "indigo-6", "편집": "teal-6", "편집(소스)": "teal-7",
    "KB 편집": "teal-6", "Word 업로드": "deep-orange-6", "업로드(교체)": "purple-6",
    "발송": "green-7", "RN 승인 승인": "green-6", "RN 승인 반려": "red-6",
    "RN 승인 재생성": "indigo-6",
    # Newsletter 는 지금도 최종 승인자 결재를 거친다.
    "최종 승인": "green-8", "최종 반려": "red-7",
}


def _render_diff(base: str, cur: str, first: bool) -> None:
    lines = H.diff_lines(base, cur)
    if not any(ln.strip() for _, ln in lines):
        ui.label("(내용 없음)").classes("text-caption text-grey-6")
        return
    with ui.column().classes("w-full gap-0").style(
        "font-family:monospace; font-size:12px; max-height:24rem; overflow:auto"
    ):
        for tag, ln in lines:
            if first:
                ui.label(ln or " ").style("white-space:pre-wrap; color:#333")
                continue
            pre = {"same": " ", "add": "+", "del": "−"}[tag]
            bg = {"same": "", "add": "background:#e6f4ea;", "del": "background:#fde8e8;"}[tag]
            col = {"same": "#555", "add": "#1b7a3d", "del": "#b3261e"}[tag]
            ui.label(f"{pre} {ln}").style(
                f"white-space:pre-wrap; padding:0 4px; {bg} color:{col}")


def _timeline_card(e: dict, base: str, cur: str, first: bool) -> None:
    add, dele = H.diff_stat(base, cur)
    with ui.card().classes("w-full"):
        with ui.row().classes("items-center gap-2 w-full no-wrap"):
            ui.badge(f"v{e['version']}", color="grey-7")
            ui.badge(e.get("event", ""),
                     color=_EVENT_COLOR.get(e.get("event", ""), "grey-6"))
            if e.get("title"):
                ui.label(e["title"]).classes(
                    "text-caption text-grey-7 ellipsis").style("max-width:26rem")
            ui.space()
            if add or dele:
                ui.label(f"+{add} −{dele}").classes("text-caption").style(
                    "color:#4A7C67; font-family:monospace")
            ui.label(str(e.get("ts", "")).replace("T", " ")).classes(
                "text-caption text-grey-6")
        meta = " · ".join(x for x in [e.get("actor", ""), e.get("summary", "")] if x)
        if meta:
            ui.label(meta).classes("text-caption text-grey-8")
        with ui.expansion("최초 내용" if first else "변경 내용 보기",
                          icon="difference").classes("w-full").props("dense"):
            _render_diff(base, cur, first)


def _render_timeline(container, mail_id, doc: str) -> None:
    """해당 작업·문서종류의 이력을 오래된→최신 타임라인으로. doc_key 별로 직전 버전과 diff."""
    container.clear()
    with container:
        if mail_id is None:
            return
        entries = H.history(mail_id, doc)
        if not entries:
            ui.label("이 문서 종류의 이력이 아직 없습니다.").classes(
                "text-caption text-grey-6")
            return
        ui.label(f"총 {len(entries)}개 버전 (오래된 → 최신)").classes(
            "text-caption text-grey-7")
        last_text: dict = {}
        for e in entries:
            dk = e.get("doc_key", "")
            first = dk not in last_text
            base = last_text.get(dk, "")
            cur = e.get("text", "")
            _timeline_card(e, base, cur, first)
            last_text[dk] = cur


@ui.page("/portaledit")
def portal_edit_page():
    apply_theme()
    preload_tinymce()          # 라이브러리는 페이지 생성 시점에 로드해야 함
    preload_smarteditor2()

    with page_header("문서 History", max_w="max-w-[104rem]"):
        ui.label("OS Update 건별 문서 이력 — Service Portal · Knowledge Base · RN · Newsletter").classes("text-h6")
        ui.label(
            "각 문서의 최초 생성부터 승인까지 언제·누가·무엇을 바꿨는지 버전으로 남깁니다. "
            "(승인이 반려되면 재편집분이 계속 쌓입니다.)"
        ).classes("text-caption text-grey-7")

        # OS Update(=작업) 별로 이력을 분리한다. 상단에서 건을 골라 해당 이력·편집만 본다.
        sel = {"mail_id": None}
        selector_row = ui.row().classes("items-center gap-2 w-full q-mt-sm")
        # 포털 탭 안에서 생성한다(아래 tab_panel 참고). 클로저로 참조하므로 여기서 미리 선언.
        lists: dict = {}     # 소스별(sp/kb) 검수 문서 목록 박스
        editors: dict = {}   # 소스별(sp/kb) 에디터 박스
        tl: dict = {}        # 문서종류별 타임라인 컨테이너

        def _edit_area(doc: str, title: str) -> None:
            """탭 안의 편집 영역(이력 아래) — 해당 소스 문서만 다룬다."""
            ui.separator().classes("q-mt-sm")
            ui.label(title).classes("text-subtitle1")
            with ui.card().classes("w-full bg-green-1"):
                ui.label(
                    "✔ 편집·저장은 로컬에만 반영됩니다. 실제 문서는 '검수 완료' 시에만 교체됩니다."
                ).classes("text-caption text-green-10")
            lists[doc] = ui.column().classes("w-full gap-2")
            editors[doc] = ui.column().classes("w-full gap-2")

        with ui.tabs().props("no-caps").classes("w-full") as _htabs:
            t_sp = ui.tab("Service Portal", icon="language")
            t_kb = ui.tab("Knowledge Base", icon="menu_book")
            t_rn = ui.tab("RN", icon="description")
            t_nl = ui.tab("Newsletter", icon="campaign")
        with ui.tab_panels(_htabs, value=t_sp).classes("w-full"):
            with ui.tab_panel(t_sp):
                tl["sp"] = ui.column().classes("w-full gap-2")
                _edit_area("sp", "Service Portal 편집")
            with ui.tab_panel(t_kb):
                tl["kb"] = ui.column().classes("w-full gap-2")
                _edit_area("kb", "Knowledge Base 편집")
            with ui.tab_panel(t_rn):
                tl["rn"] = ui.column().classes("w-full gap-2")
                ui.label("RN(Release Note) 편집은 [RN 승인]·작업 카드에서 이뤄지고, 여기서는 이력만 봅니다.") \
                    .classes("text-caption text-grey-6")
            with ui.tab_panel(t_nl):
                tl["newsletter"] = ui.column().classes("w-full gap-2")
                ui.label("Newsletter 편집·발송은 작업 카드에서 이뤄지고, 여기서는 이력만 봅니다.") \
                    .classes("text-caption text-grey-6")

        def _clear_editors() -> None:
            for _e in editors.values():
                _e.clear()

        def _render_timelines():
            for doc, box in tl.items():
                _render_timeline(box, sel["mail_id"], doc)

        def _case_label(c: dict) -> str:
            pv = " ".join(x for x in (c.get("product", ""), c.get("version", "")) if x)
            tot = sum((c.get("counts") or {}).values())
            return f"#{c['mail_id']} · {pv or '제품?'}" + (f" (이력 {tot})" if tot else "")

        def _render_selector():
            selector_row.clear()
            # 이력이 있는 건 + 편집 대상(WS) 건을 합쳐 보여준다(최근순은 list_cases 기준).
            cases = {c["mail_id"]: c for c in H.list_cases()}
            for u in WS.list_os_updates():
                cases.setdefault(u["mail_id"], {
                    "mail_id": u["mail_id"], "product": u.get("product", ""),
                    "version": u.get("version", ""), "counts": {}})
            with selector_row:
                if not cases:
                    ui.label("이력·검수 대상 OS Update 가 없습니다.").classes(
                        "text-caption text-grey-7")
                    return
                opts = {mid: _case_label(c) for mid, c in cases.items()}
                if sel["mail_id"] not in opts:
                    sel["mail_id"] = next(iter(opts))
                ui.label("OS Update").classes("text-caption text-grey-7")
                ui.select(opts, value=sel["mail_id"],
                          on_change=lambda e: _pick(e.value)).props(
                    "dense outlined").classes("w-[30rem]")
                ui.button(icon="refresh", on_click=_refresh_all).props(
                    "flat dense").tooltip("새로고침")

        def _pick(mid):
            sel["mail_id"] = mid
            _clear_editors()
            _render_timelines()
            _render_list()

        def _refresh_all():
            _render_selector()
            _clear_editors()
            _render_timelines()
            _render_list()

        def open_item(key: str):
            item = WS.get_item(key)
            if not item:
                ui.notify("항목을 찾을 수 없습니다.", color="negative")
                return
            # 문서 소스에 맞는 탭의 편집기에 연다(SP→Service Portal 탭 / KB→Knowledge Base 탭).
            doc = "sp" if item.get("source") == "service_portal" else "kb"
            editor_box = editors.get(doc)
            if editor_box is None:
                return
            editor_box.clear()
            _seq["n"] += 1
            with editor_box:
                ui.separator()
                ui.label(
                    f'[{_SOURCE_LABEL.get(item.get("source",""), item.get("source",""))}] '
                    f'{item.get("title","")} · #{item.get("doc_id","")}'
                ).classes("text-subtitle1")
                _change_report(item)

                editor = item.get("editor", "tinymce")
                content = item.get("edited_html", "") or item.get("original_html", "")
                result_box = ui.column().classes("w-full")

                if editor == "storage":
                    # ⑥ Knowledge Base — 검수 화면은 작업 카드와 **같은 것**을 쓴다
                    #    (app/pages/kb_review.py). 두 벌로 두면 한쪽만 고쳐진다.
                    from app.pages.kb_review import render_kb_doc

                    render_kb_doc(key, sel["mail_id"], on_saved=_render_list)
                    return

                is_se2 = editor == "se2"
                el_id = f"pe_{'se2' if is_se2 else 'tm'}_{_seq['n']}"
                editor_name = "Naver SmartEditor2" if is_se2 else "TinyMCE"
                ui.label(f"{editor_name} (실제 에디터 · 로컬 탑재)").classes("text-caption text-grey-7")
                if is_se2:
                    smarteditor2_editor(el_id, content)
                else:
                    tinymce_editor(el_id, content)

                async def on_save(k=key, eid=el_id, se2=is_se2):
                    html = await (
                        smarteditor2_get_content(eid) if se2 else tinymce_get_content(eid)
                    )
                    ok = WS.update_edited(k, html)
                    if ok:
                        _doc = "sp" if item.get("source") == "service_portal" else "kb"
                        H.record(sel["mail_id"], _doc, "편집", actor=current_username(),
                                 summary="에디터 저장",
                                 text=H.text_from_html(html),
                                 doc_key=str(item.get("doc_id", "")),
                                 title=item.get("title", ""))
                        ui.notify("로컬 검수 작업공간에 저장했습니다. (프로덕션 무접촉)", color="positive")
                        _render_list()  # 갱신시각 반영
                    else:
                        ui.notify("저장 실패(항목 없음).", color="negative")

                with ui.row().classes("gap-2 mt-2"):
                    ui.button("로컬에 저장", icon="save", on_click=on_save).props("color=primary")
                result_box

        def _render_list():
            all_items = WS.list_items(sel["mail_id"]) if sel["mail_id"] is not None else []
            for doc, src, lab in (("sp", "service_portal", "Service Portal"),
                                  ("kb", "release_notes", "Knowledge Base")):
                lb = lists.get(doc)
                if lb is None:
                    continue
                lb.clear()
                items = [it for it in all_items if it.get("source") == src]
                with lb:
                    if not items:
                        ui.label(
                            f"이 OS Update 에 {lab} 검수 문서가 없습니다. "
                            "메일함에서 해당 편집 단계를 실행하면 여기에 담깁니다."
                        ).classes("text-caption text-grey-7")
                        continue
                    ui.label(f"검수 대상 {len(items)}개").classes("text-caption text-grey-7")
                    for it in items:
                        with ui.card().classes("w-full"):
                            with ui.row().classes("items-center justify-between w-full no-wrap"):
                                with ui.row().classes("items-center gap-2 no-wrap"):
                                    ui.label(it.get("title", "")).classes("text-body2")
                                    ui.label(f'#{it.get("doc_id","")}').classes(
                                        "text-caption text-grey-5")
                                with ui.row().classes("items-center gap-2 no-wrap"):
                                    if it.get("error"):
                                        ui.badge("편집 오류", color="red")
                                    else:
                                        ui.label(
                                            f'적용 {len(it.get("applied") or [])} · '
                                            f'미적용 {len(it.get("failed") or [])}'
                                        ).classes("text-caption text-grey-6")
                                    ui.button(
                                        "검수", icon="rate_review",
                                        on_click=lambda k=it["key"]: open_item(k),
                                    ).props("flat dense")

        _render_selector()
        _render_timelines()
        _render_list()
