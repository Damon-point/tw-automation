"""⑥ Knowledge Base 편집 검수 — **작업 카드 안에서** 끝낸다.

예전에는 검수하려면 작업 보드를 떠나 /portaledit 로 가서, 건을 고르고, 탭을 바꾸고,
문서를 눌러야 그제서야 내용이 보였다(사용자 지적 2026-09-08: "다른 페이지에 가서 봐야되고
그 마저도 불편합니다"). 이제 단계 카드가 이 화면을 그대로 품는다.

검수자가 알아야 하는 것부터 위에 둔다:
  ① 어느 페이지를 고치는가 — 제목·번호, 새로 만드는 것인지, 다른 Android 페이지는 아닌지
  ② **이번에 무엇이 추가되는가** — 문서 전체(2만 자)가 아니라 새로 얹히는 엔트리만
  ③ 고치기 — 섹션 × NEW/IMPROVED/FIXED
  ④ 검수 완료 = 실제 업로드
"""
from __future__ import annotations

from nicegui import run, ui

from app.auth import current_user_id, current_username
from app.pipeline import history as H
from app.pipeline import kb_rn as KB
from app.pipeline import review_workspace as WS

_PREVIEW_SEQ = {"n": 0}


def _preview_iframe(path: str, min_h: str = "55vh") -> None:
    """미리보기 HTML 을 파일 URL(/artifact/view)로 iframe 에 띄운다(큰 문서·<script> 안전).

    URL 이 매번 같으면 브라우저 캐시로 '수정해도 반영 안 됨'이 생기므로 v= 로 무력화한다.
    """
    from urllib.parse import quote

    _PREVIEW_SEQ["n"] += 1
    src = f"/artifact/view?path={quote(path)}&v={_PREVIEW_SEQ['n']}"
    ui.html(
        f'<iframe sandbox src="{src}" '
        f'style="width:100%;min-height:{min_h};border:1px solid #ddd;background:white;">'
        "</iframe>",
        sanitize=False,
    ).classes("w-full")


def _warn(title: str, body: str) -> None:
    with ui.column().classes("w-full gap-1").style(
        "background:#fdece9; border-left:5px solid #c0392b; border-radius:8px;"
        " padding:10px 14px"
    ):
        ui.label(title).classes("text-subtitle2").style("color:#922b21")
        ui.label(body).classes("text-caption").style("color:#922b21")


def _sections_of(item: dict) -> list[dict]:
    """항목에 담긴 섹션 목록(편집용 사본). 예전 단일(VoC) 구조도 받아 준다."""
    secs = [dict(s) for s in (item.get("kb_sections") or [])]
    if not secs and item.get("kb_cats"):
        c = item["kb_cats"]
        secs = [{"title": "VoC", "new": list(c.get("new") or []),
                 "improved": list(c.get("improved") or []),
                 "fixed": list(c.get("fixed") or [])}]
    for s in secs:
        for b in ("new", "improved", "fixed"):
            s[b] = list(s.get(b) or [])
    return secs


def _entry_summary(item: dict, secs: list[dict]) -> None:
    """이번에 **추가되는 엔트리**만 보여 준다 — 문서 전체를 읽게 하지 않는다."""
    n = sum(len(s[b]) for s in secs for b in ("new", "improved", "fixed"))
    with ui.card().classes("w-full").style("background:#f7faf8;border:1px solid #d9e6de"):
        with ui.row().classes("items-baseline gap-2 w-full no-wrap"):
            ui.label(item.get("kb_vfull") or "(버전 없음)").classes("text-subtitle1")
            ui.label("이번에 이 페이지 맨 위에 얹히는 내용입니다").classes(
                "text-caption text-grey-7")
        meta = []
        if (item.get("kb_sdk") or "").strip():
            meta.append(f"SDK v{item['kb_sdk']}")
        if (item.get("kb_security") or "").strip():
            meta.append(f"Security Patch {KB.secpatch_date(item['kb_security'])}")
        if meta:
            ui.label(" · ".join(meta)).classes("text-caption text-grey-8")
        if not n:
            _warn("Release Notes 내용이 비어 있습니다",
                  "공개할 항목이 하나도 없습니다. 이대로 올리면 고객에게 제목과 SDK·보안패치만 "
                  "보입니다. 아래에서 항목을 넣거나, 이 단계를 다시 실행하세요.")
            return
        for s in secs:
            ui.label(s.get("title") or "Others").classes("text-body2 q-mt-sm") \
                .style("font-weight:600")
            for b in ("new", "improved", "fixed"):
                for txt in s[b]:
                    with ui.row().classes("items-start gap-2 w-full no-wrap"):
                        ui.badge(KB._LABEL[b]).style(
                            f"background: rgb({KB._COLOR[b]}) !important; color:#fff")
                        ui.label(txt).classes("text-body2").style(
                            "white-space:pre-wrap")


def render_kb_doc(key: str, mail_id, *, on_saved=None) -> None:
    """KB 문서 한 건 — 대상 페이지 · 추가되는 엔트리 · 섹션 편집 · 문서 전체 미리보기."""
    item = WS.get_item(key)
    if not item:
        ui.label("검수 항목을 찾을 수 없습니다.").classes("text-caption text-grey-7")
        return

    new_page = item.get("kb_new") or None
    with ui.row().classes("items-baseline gap-2 w-full no-wrap"):
        ui.label("Knowledge Base").classes("text-caption text-grey-7")
        ui.label(
            (f"{new_page['title']} · 새로 만듭니다" if new_page
             else f"{item.get('title', '')} · #{item.get('doc_id', '')}")
        ).classes("text-subtitle1")

    if item.get("ambiguous"):
        _warn("다른 Android 버전의 페이지일 수 있습니다",
              item.get("ambiguous_why", ""))
    others = WS.others_on_doc(item)
    if others:
        _warn("같은 페이지를 고치는 다른 작업이 있습니다",
              "· ".join(str(o.get("mail_id")) for o in others)
              + " — 업로드는 본문을 통째로 바꾸므로, 하나를 올린 뒤 나머지는 KB 편집을 "
                "다시 돌려 최신 내용 위에 쌓아야 합니다.")
    if item.get("error"):
        _warn("편집 오류", str(item["error"]))

    secs = _sections_of(item)
    _entry_summary(item, secs)

    # ── 고치기 ────────────────────────────────────────────────────────
    with ui.expansion("이 엔트리 고치기 — 섹션 · NEW/IMPROVED/FIXED", icon="edit") \
            .classes("w-full").props("dense"):
        with ui.row().classes("w-full gap-3 no-wrap"):
            ver_in = ui.input("버전", value=item.get("kb_vfull", "")).props(
                "dense outlined").classes("flex-grow")
            sdk_in = ui.input("SDK", value=item.get("kb_sdk", "")).props(
                "dense outlined").classes("w-40")
            sec_in = ui.input("Security Patch", value=item.get("kb_security", "")).props(
                "dense outlined").classes("w-40")

        def _move(i: int, d: int) -> None:
            j = i + d
            if 0 <= i < len(secs) and 0 <= j < len(secs):
                secs[i], secs[j] = secs[j], secs[i]
            sections_ui.refresh()

        def _del_sec(i: int) -> None:
            if 0 <= i < len(secs):
                secs.pop(i)
            sections_ui.refresh()

        def _add_sec() -> None:
            used = {s.get("title") for s in secs}
            nxt = next((t for t in KB.SECTIONS if t not in used), "Others")
            secs.append({"title": nxt, "new": [], "improved": [], "fixed": []})
            sections_ui.refresh()

        def _add_item(i: int, b: str) -> None:
            secs[i][b].append("")
            sections_ui.refresh()

        def _del_item(i: int, b: str, j: int) -> None:
            if 0 <= j < len(secs[i][b]):
                secs[i][b].pop(j)
            sections_ui.refresh()

        @ui.refreshable
        def sections_ui() -> None:
            if not secs:
                ui.label("섹션이 없습니다. 아래 '섹션 추가'로 만드세요.").classes(
                    "text-caption text-grey-6")
            for i, s in enumerate(secs):
                with ui.card().classes("w-full").style(
                    "background:#fafafa;border:1px solid #e6e6e6"
                ):
                    with ui.row().classes("items-center gap-2 w-full no-wrap"):
                        ui.select(
                            KB.SECTIONS, value=s.get("title") or "Others",
                            label="섹션", new_value_mode="add-unique",
                            on_change=lambda e, k=i: secs[k].update(
                                title=e.value or "Others"),
                        ).props("dense outlined").classes("w-64")
                        ui.space()
                        ui.button(icon="arrow_upward",
                                  on_click=lambda _=None, k=i: _move(k, -1)) \
                            .props("flat dense").tooltip("위로").set_enabled(i > 0)
                        ui.button(icon="arrow_downward",
                                  on_click=lambda _=None, k=i: _move(k, 1)) \
                            .props("flat dense").tooltip("아래로") \
                            .set_enabled(i < len(secs) - 1)
                        ui.button(icon="delete",
                                  on_click=lambda _=None, k=i: _del_sec(k)) \
                            .props("flat dense color=negative").tooltip("섹션 삭제")
                    for b in ("new", "improved", "fixed"):
                        with ui.row().classes("items-center gap-2 w-full no-wrap"):
                            # 실제 RN 마커 색(NEW 주황·IMPROVED 파랑·FIXED 초록).
                            # Quasar 기본 배지 배경을 덮어야 해서 !important.
                            ui.badge(KB._LABEL[b]).style(
                                f"background: rgb({KB._COLOR[b]}) !important; color:#fff")
                            ui.label(f"{len(s[b])}건").classes("text-caption text-grey-6")
                            ui.space()
                            ui.button("항목 추가", icon="add",
                                      on_click=lambda _=None, k=i, bb=b: _add_item(k, bb)) \
                                .props("flat dense size=sm")
                        for j, txt in enumerate(s[b]):
                            with ui.row().classes("items-center gap-1 w-full no-wrap"):
                                ui.input(
                                    value=txt,
                                    on_change=lambda e, k=i, bb=b, jj=j:
                                        secs[k][bb].__setitem__(jj, e.value or ""),
                                ).props("dense outlined").classes("flex-grow")
                                ui.button(
                                    icon="close",
                                    on_click=lambda _=None, k=i, bb=b, jj=j:
                                        _del_item(k, bb, jj),
                                ).props("flat dense color=negative")

        sections_ui()
        with ui.row().classes("items-center gap-2 q-mt-sm"):
            ui.button("섹션 추가", icon="playlist_add", on_click=_add_sec) \
                .props("outline size=sm")

        async def _apply(k=key) -> None:
            from app.integrations import confluence_reader as RN

            # 빈 항목은 버린다(편집 중 추가한 빈 줄).
            out = []
            for s in secs:
                t = {"title": (s.get("title") or "Others").strip()}
                for b in ("new", "improved", "fixed"):
                    t[b] = [x.strip() for x in s.get(b, []) if str(x).strip()]
                if any(t[b] for b in ("new", "improved", "fixed")):
                    out.append(t)
            vfull = (ver_in.value or "").strip()
            sdk = (sdk_in.value or "").strip()
            sec = (sec_in.value or "").strip()
            it0 = WS.get_item(k)
            if not it0:
                ui.notify("항목 없음", color="negative")
                return
            entry = KB.build_entry_html(vfull, sdk, out, security_patch=sec)
            new_storage, ins_ok = KB.insert_entry(it0.get("original_html", "") or "", entry)
            n = ui.notification("반영 + 미리보기 렌더 중...", spinner=True, timeout=None)
            html, _e = await run.io_bound(
                RN.storage_to_view, current_user_id(), str(it0.get("doc_id", "")),
                new_storage)
            n.dismiss()
            cnt = sum(len(s[b]) for s in out for b in ("new", "improved", "fixed"))
            it0.update(
                edited_html=new_storage, preview_html=html or "",
                kb_sections=out, kb_vfull=vfull, kb_sdk=sdk, kb_security=sec,
                error="" if ins_ok else "삽입 위치(최신 버전 섹션)를 찾지 못했습니다.",
                applied=[{"why": f"편집 반영 · 섹션 {len(out)}개 · 항목 {cnt}건",
                          "old": "", "new": f"{vfull} 엔트리"}] if ins_ok
                else it0.get("applied"),
            )
            WS.put(it0)
            H.record(mail_id, "kb", "편집", actor=current_username(),
                     summary=f"섹션 {len(out)}개 · 항목 {cnt}건 편집 반영",
                     text=H.text_from_kb_sections(out),
                     doc_key=str(it0.get("doc_id", "")), title=it0.get("title", ""))
            ui.notify("편집을 반영했습니다." if ins_ok
                      else "엔트리는 재생성했지만 삽입 위치를 못 찾았습니다.",
                      color="positive" if ins_ok else "warning")
            if on_saved:
                on_saved()

        ui.button("이 내용으로 반영", icon="save", on_click=_apply).props("color=primary")

    # ── 문서 전체 미리보기(접어 둔다 — 판단은 위의 엔트리로 한다) ──────────
    with ui.expansion("문서 전체 미리보기 (올라갈 페이지 그대로)", icon="menu_book") \
            .classes("w-full").props("dense"):
        html = item.get("preview_html", "")
        if not (html or "").strip():
            ui.label("미리보기를 렌더하지 못했습니다 — 위 엔트리 내용으로 검수하세요.") \
                .classes("text-caption text-grey-7")
        else:
            import re as _re
            from pathlib import Path as _P

            from app.atomic_io import atomic_write_text as _awt

            _dir = _P(__file__).resolve().parent.parent.parent / "work" / "kb_preview"
            _dir.mkdir(parents=True, exist_ok=True)
            _did = _re.sub(r"[^A-Za-z0-9_.-]", "_", str(item.get("doc_id", "x")))
            _mid = _re.sub(r"[^A-Za-z0-9_.-]", "_", str(mail_id))
            _fp = _dir / f"kb_{_mid}_{_did}.html"
            try:
                _awt(str(_fp), html)
            except Exception:  # noqa: BLE001
                pass
            _preview_iframe(str(_fp))

    with ui.expansion("고급: 소스(storage) HTML 직접 편집", icon="code") \
            .classes("w-full").props("dense"):
        ta = ui.textarea(value=item.get("edited_html", "") or item.get("original_html", "")) \
            .classes("w-full").props("autogrow outlined")

        async def _save_src(k=key) -> None:
            from app.integrations import confluence_reader as RN

            it2 = WS.get_item(k)
            if not it2:
                ui.notify("저장 실패(항목 없음).", color="negative")
                return
            new_storage = ta.value or ""
            n = ui.notification("저장 + 미리보기 렌더 중...", spinner=True, timeout=None)
            html2, _e = await run.io_bound(
                RN.storage_to_view, current_user_id(), str(it2.get("doc_id", "")),
                new_storage)
            n.dismiss()
            it2["edited_html"] = new_storage
            it2["preview_html"] = html2 or ""
            WS.put(it2)
            H.record(mail_id, "kb", "편집(소스)", actor=current_username(),
                     summary="storage 소스 직접 편집",
                     text=H.text_from_html(new_storage),
                     doc_key=str(it2.get("doc_id", "")), title=it2.get("title", ""))
            ui.notify("저장했습니다.", color="positive")
            if on_saved:
                on_saved()

        ui.button("저장", icon="save", on_click=_save_src).props("color=primary")


def render_kb_review(mail_id, *, uploads: bool, on_saved=None) -> None:
    """작업 카드의 [Knowledge Base 편집] 단계에 붙는 검수 화면.

    업로드 버튼은 두지 않는다 — 카드의 [검수 완료]가 그 일을 한다(Approval 갈래에서만
    실제 교체, Submission 갈래는 단계 완료만). 버튼이 둘이면 어느 쪽이 진짜인지 헷갈린다.
    """
    items = [it for it in WS.list_items(mail_id) if it.get("source") == "release_notes"]
    ui.separator()
    ui.label("Knowledge Base 편집 검수").classes("text-subtitle2")
    if not items:
        ui.label("검수할 편집안이 없습니다 — 이 단계를 실행하면 만들어집니다.")             .classes("text-caption text-grey-7")
        return

    for i, it in enumerate(items):
        if i:
            ui.separator()
        render_kb_doc(it["key"], mail_id, on_saved=on_saved)

    with ui.column().classes("w-full gap-1").style(
        "background:#eaf3ee; border-left:5px solid #3C6553; border-radius:8px;"
        " padding:10px 14px"
    ):
        ui.label("여기까지는 로컬 검수입니다 — 실제 페이지는 아직 그대로입니다").classes(
            "text-subtitle2").style("color:#2f5244")
        ui.label(
            "아래 [검수 완료]를 누르면 위 내용으로 Confluence 페이지를 교체합니다."
            if uploads else
            "실제 교체는 Approval 갈래의 같은 단계에서 합니다."
        ).classes("text-caption").style("color:#2f5244")
