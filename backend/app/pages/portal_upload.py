"""⑤ Service Portal · ⑥ Knowledge Base '검수 완료 = 실제 업로드'.

검수 작업공간(review_workspace)의 편집 사본으로 **실제 원본 페이지를 교체**한다.
되돌리기 어려운 고객 대면 production 쓰기라, 확인 다이얼로그에서 명시 승인해야만 실행된다
(각 저장 함수에 allow_write=True 는 이 흐름에서만 전달). 성공 시 해당 단계를 완료 처리한다.
"""
from nicegui import run, ui

from app.auth import current_user_id
from app.pipeline import history as H
from app.pipeline import review_workspace as WS
from app.stages import DONE, set_stage_status

_SRC = {
    "service_portal": ("service_portal", "Service Portal"),
    "release_note_edit": ("release_notes", "Knowledge Base (Confluence)"),
}


#: 검수 사본 source → 그 사본을 실제로 올리는 Approval 단계
_UPLOAD_STAGE = {"service_portal": "service_portal", "release_notes": "release_note_edit"}


def _still_open(item: dict) -> bool:
    """그 사본의 작업이 **아직 올리지 않았는가**. 업로드했거나 Skip 으로 끝낸 작업이면 False.

    끝난 작업의 사본이 남아 있으면 다음 회차를 '같은 페이지를 고치는 다른 작업' 으로 막았다
    (실측 2026-10-13: PM95 95.52 · 95.07C1 사본이 95.08 KB 업로드를 막음). 사본은 지우지 않는다 —
    그 단계를 되돌리면 다시 올릴 수 있어야 한다.
    """
    from app.task_store import approval_stage_status

    stage = _UPLOAD_STAGE.get(str(item.get("source", "")))
    if not stage:
        return True
    try:
        return approval_stage_status(item.get("mail_id"), stage) != DONE
    except Exception:  # noqa: BLE001  (판단 못 하면 예전처럼 충돌로 본다)
        return True


def confirm_upload(stage_key, mail_id, me, stage_id, on_done=None, set_done=None) -> None:
    """set_done: 업로드가 모두 성공하면 부를 함수(그 단계를 완료로). 작업 보드가 넘긴다 —
    OS Update 작업 단계와 메일 작업 단계는 표가 달라 여기서 번호만으로는 고를 수 없다."""
    source, label = _SRC.get(stage_key, (None, None))
    if source is None:
        ui.notify("업로드 대상 단계가 아닙니다.", color="negative")
        return
    items = [it for it in WS.list_items(mail_id) if it.get("source") == source]
    if not items:
        ui.notify(
            f"업로드할 {label} 검수 사본이 없습니다. 먼저 이 단계를 실행해 사본을 만드세요.",
            color="negative",
        )
        return
    # 같은 문서를 겨냥한 다른 작업의 사본 — 업로드는 본문을 통째로 교체하므로 나중에
    # 올린 쪽이 먼저 올린 내용을 지운다.
    #
    # **막는 것은 Knowledge Base 뿐이다.** KB 는 회차마다 새 버전 블록을 얹는 구조라
    # 하나가 지워지면 그 회차가 통째로 사라진다(실측 job-18·job-21 이 같은 PM95 A13
    # 페이지를 겨냥했다). Service Portal 의 4C '플래싱 가이드' 는 사정이 다르다 —
    # 제품마다 자기 행만 규칙으로 갈아 끼우는 **공용 문서**라, 열려 있는 작업이 둘만
    # 돼도 아무도 못 올리게 된다(실측 2026-09-09: 18·21·23·28 네 작업이 4C 를 함께
    # 물고 있어 TR54 업로드가 막혔다). 그래서 여기서는 막지 않고 **알리기만** 한다.
    _clash = [(it, o) for it in items for o in WS.others_on_doc(it) if _still_open(o)]
    _clash_lines = [
        f"· #{it.get('doc_id')} {it.get('title', '')} ← {o.get('mail_id')}"
        for it, o in _clash]
    if _clash and source == "release_notes":
        ui.notify(
            "같은 페이지를 고치는 다른 작업이 있습니다. 하나를 먼저 올린 뒤 나머지는 "
            "KB 편집을 다시 돌려 최신 내용 위에 쌓으세요." + chr(10)
            + chr(10).join(_clash_lines),
            color="negative", multi_line=True, timeout=12000)
        return
    user_id = current_user_id()

    with ui.dialog() as dlg, ui.card().classes("w-[38rem] max-w-full"):
        ui.label(f"{label} · 실제 업로드 확인").classes("text-h6")
        # ui.label 은 마크다운을 해석하지 않는다 — '**' 는 화면에 그대로 찍힌다.
        _n_new = sum(1 for it in items if it.get("kb_new") or it.get("sp_new"))
        ui.label(
            (f"검수 사본으로 실제 원본 페이지 {len(items) - _n_new}건을 교체하고 "
             f"{_n_new}건을 새로 만듭니다. " if _n_new else
             f"검수 사본으로 실제 원본 페이지 {len(items)}건을 교체합니다. ")
            + "고객 대면 페이지이며 되돌리기 어렵습니다."
        ).classes("text-negative")
        for it in items:
            _nw = it.get("kb_new") or it.get("sp_new")
            ui.label(f"· [신규 생성] {_nw['title']}"
                     + (f" ({_nw['space']} 스페이스)" if _nw.get("space") else "")
                     if _nw else
                     f"· #{it.get('doc_id')} {it.get('title','')}"
                     ).classes("text-caption text-grey-8")

        if _clash_lines:
            with ui.column().classes("w-full gap-1").style(
                "background:#fdf6e3; border-left:5px solid #d99a3a;"
                " border-radius:8px; padding:10px 14px"
            ):
                ui.label("같은 문서를 고치는 다른 작업이 있습니다").classes(
                    "text-subtitle2").style("color:#8a5a12")
                for _l in _clash_lines:
                    ui.label(_l).classes("text-caption").style("color:#8a5a12")
                ui.label(
                    "이 사본은 그 작업이 손대기 **전**의 내용입니다. 지금 올리면 "
                    "그쪽 변경이 되돌아갑니다. 그 작업은 단계를 다시 돌린 뒤 올리세요."
                ).classes("text-caption").style("color:#8a5a12")

        result_box = ui.column().classes("w-full")

        async def do_upload():
            from app.integrations import confluence_reader as RN
            from app.integrations import service_portal as SP

            n = ui.notification("실제 페이지 업로드 중...", spinner=True, timeout=None)
            results = []
            for it in items:
                did = str(it.get("doc_id"))
                content = it.get("edited_html", "") or it.get("original_html", "")
                # 올리기 직전에 한 번 더 — 편집기가 삼킨 <meta>·<style> 을 되살린다.
                # 이미 그렇게 저장된 사본에도 듣는다(실측 2026-09-09 TR54: style 이 빠진
                # 채 올라가 표 디자인이 통째로 깨졌다).
                if source == "service_portal":
                    from app.integrations.service_portal import keep_head

                    content = keep_head(it.get("original_html", ""), content)
                # KB — 올리기 직전에 최신 블록의 OS Download Link 를 이번 회차 sdcard
                # FULL zip 으로 맞춘다(편집안은 Dropbox 업로드 전에 만들어져 비어 있다).
                if source == "release_notes":
                    from app.pipeline.kb_rn import set_os_download_link, sdcard_full_url
                    from app.pipeline.state import get_state as _gs_dl

                    content, _ = set_os_download_link(content, sdcard_full_url(_gs_dl(mail_id) or {}))
                _new = it.get("kb_new")
                _spnew = it.get("sp_new")
                if _spnew:
                    # 최초 출시 — 교체할 문서가 없다. 최근 Resources 문서를 본떠 만든다.
                    nid, err2 = await run.io_bound(
                        SP.create_doc, user_id, _spnew["src_detail"],
                        _spnew["title"], content, True)
                    ok = bool(nid)
                    msg = (f"'{_spnew['title']}' 문서를 새로 만들었습니다 (#{nid}) — "
                           "제품 관리의 Service Portal 주소에 이 번호를 등록하세요."
                           if ok else err2)
                    did = nid or _spnew["title"]
                elif source == "service_portal":
                    ok, msg = await run.io_bound(SP.save_real_cont, user_id, did, content, True)
                elif _new:
                    # 최초 출시 — 교체할 원본이 없다. 페이지를 **새로 만든다**.
                    from app.pipeline import kb_new_page as KNEW

                    pid, url, err = await run.io_bound(
                        KNEW.create_page, user_id, _new["title"], content,
                        _new.get("space") or KNEW.DEFAULT_SPACE, True)
                    ok = bool(pid)
                    msg = (f"'{_new['title']}' 페이지를 새로 만들었습니다 — {url}"
                           if ok else err)
                    did = pid or _new["title"]
                else:
                    ok, msg = await run.io_bound(RN.save_real_release_note, user_id, did, content, True)
                results.append((did, ok, msg))
            n.dismiss()

            result_box.clear()
            with result_box:
                for did, ok, msg in results:
                    ui.label(f"{'✔' if ok else '✘'} #{did}: {msg}").classes(
                        "text-caption " + ("text-green-10" if ok else "text-red-10")
                    )
            if all(ok for _, ok, _ in results):
                # 예전에는 set_stage_status(메일 작업 단계 표)를 불러 OS Update 작업 단계가
                # 검수 대기로 남았다 — 업로드는 됐는데 완료 표시가 안 돼 Skip 으로 넘기게 됐다
                # (2026-10-13). 작업 보드가 넘긴 함수로 그 단계 표를 고친다.
                if set_done is not None:
                    set_done()
                else:
                    set_stage_status(stage_id, DONE, me)
                # 이력: 실제 원본 페이지 교체(업로드) — SP/KB 각 탭에 기록.
                _doc = {"service_portal": "sp", "release_note_edit": "kb"}.get(stage_key, "sp")
                for it in items:
                    content = it.get("edited_html", "") or it.get("original_html", "")
                    H.record(mail_id, _doc, "업로드(교체)", actor=me,
                             summary="원본 페이지 교체",
                             text=H.text_from_html(content),
                             doc_key=str(it.get("doc_id", "")),
                             title=it.get("title", ""))
                ui.notify(f"{label} 원본을 교체했습니다(업로드 완료).", color="positive")
                dlg.close()
                if on_done:
                    on_done()
            else:
                ui.notify("일부 업로드 실패 — 결과를 확인하세요.", color="negative")

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("취소", on_click=dlg.close).props("flat")
            ui.button("실제 업로드", icon="cloud_upload", on_click=do_upload).props("color=negative")
    dlg.open()
