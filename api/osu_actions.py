"""
OS Update 쓰기 동작 — 기존 작업 보드(app/pages/task_board.py)의 순서를 그대로 옮긴다.

단계 실행은 **이 API 프로세스 안의 스레드** 에서 돈다. 진행률(app.progress)도 이 프로세스
메모리에 있으므로, 실행은 반드시 이 API 를 거쳐야 한다(밖에서 돌리면 화면이 굳는다).
"""
from __future__ import annotations

import threading
from datetime import datetime
import time

from app import os_update_jobs as J
from app import progress
from app import stages as S
from app.pipeline import stage_reset as SR
from app.pipeline.state import get_state, update_state
from app.stage_runners import execute_stage, rework_stage
from app.task_store import JobStageStore, progress_key

PENDING, RUNNING, REVIEW, DONE = S.PENDING, S.RUNNING, S.REVIEW, S.DONE

# 재실행 · 되돌리기 때 결재 상태를 지우는 범위(task_board._REVIEW_STATE_KEYS 와 같다)
_REVIEW_STATE_KEYS = {"release_note_full": ("rn_mid_review", "rn_review"),
                      "release_note": ("rn_review",),
                      "newsletter": ("newsletter_review",)}
REWORK_STAGES = {"release_note_full", "service_portal", "release_note_edit", "newsletter"}
UPLOAD_STAGES = {"service_portal", "release_note_edit"}

_lock = threading.Lock()
_running: dict[int, threading.Thread] = {}


class ActionError(Exception):
    pass


def _reset_review_state(key, stage_key) -> None:
    keys = _REVIEW_STATE_KEYS.get(stage_key)
    if not keys:
        return
    st = get_state(key) or {}
    wipe = {k: {} for k in keys if st.get(k)}
    if wipe:
        update_state(key, **wipe)


def _store_and_stage(job_id: int, stage_id: int):
    store = JobStageStore(job_id)
    st = store.get(stage_id)
    if st is None or st.job_id != job_id:
        raise ActionError("단계를 찾지 못했습니다")
    return store, st


def _subject(job: dict) -> str:
    return f"{job.get('product', '')} A{job.get('android', '')} {job.get('os_type', '')} {job.get('version', '')}".strip()


def _rn_review_locked(key) -> bool:
    st = get_state(key) or {}
    return any((st.get(k) or {}).get("status") in ("requested", "approved") for k in ("rn_mid_review", "rn_review"))


# ── 실행 · 중단 ───────────────────────────────────────────────────────────────

def run_next(job_id: int, side: str, user: dict) -> dict:
    """그 갈래의 **첫 미완료 단계** 를 돌린다(기존 규칙). 검수 대기 · 실행 중이면 거부."""
    store = JobStageStore(job_id)
    store.stages(side)                      # 단계 행이 없으면 만든다(기존 보드와 같다)
    nxt = store.next_pending(side)
    if nxt is None:
        raise ActionError("모든 단계가 완료되었습니다")
    if nxt.status == RUNNING:
        raise ActionError(f"'{S.stage_label(nxt.stage_key, side)}' 이(가) 이미 실행 중입니다")
    return _start(job_id, nxt.id, user)


def run_stage(job_id: int, stage_id: int, user: dict) -> dict:
    """특정 단계 재실행(검수 대기 · 완료 상태에서 다시 돌리기). 앞 단계가 끝나 있어야 한다."""
    store, st = _store_and_stage(job_id, stage_id)
    order = S.stage_keys_for(st.side)
    rows = {r.stage_key: r for r in store.stages(st.side)}
    for k in order[: order.index(st.stage_key)]:
        if rows.get(k) is None or rows[k].status != DONE:
            raise ActionError(f"앞 단계 '{S.stage_label(k, st.side)}' 가 아직 완료되지 않았습니다")
    return _start(job_id, stage_id, user)


def _start(job_id: int, stage_id: int, user: dict) -> dict:
    me = user["username"]
    store, st = _store_and_stage(job_id, stage_id)
    ok, why = store.claim(stage_id, me)
    if not ok:
        raise ActionError(why)
    job = J.get(job_id) or {}
    pk = progress_key(store, stage_id)
    _reset_review_state(store.key, st.stage_key)

    def work():
        try:
            out = execute_stage(st.stage_key, _subject(job), user["id"], st.input_params or "",
                                store.key, "", pk, st.side)
            if out.cancelled:
                store.set_status(stage_id, PENDING, me)
            else:
                store.save_result(stage_id, out.result_text, out.artifact_type, out.artifact_path, me)
                if out.auto_done:
                    store.set_status(stage_id, DONE, me)
        except Exception as e:  # noqa: BLE001  기존 보드는 여기서 '실행 중' 으로 굳었다 — 결과로 남긴다
            store.save_result(stage_id, f"✘ 실행 중 오류: {type(e).__name__}: {e}", "", "", me)
        finally:
            progress.clear(pk)
            with _lock:
                _running.pop(stage_id, None)

    t = threading.Thread(target=work, name=f"stage-{stage_id}", daemon=True)
    with _lock:
        _running[stage_id] = t
    t.start()
    return {"ok": True, "stage_id": stage_id, "message": f"'{S.stage_label(st.stage_key, st.side)}' 실행을 시작했습니다"}


def cancel(job_id: int, stage_id: int) -> dict:
    store, _ = _store_and_stage(job_id, stage_id)
    ok = progress.request_cancel(progress_key(store, stage_id))
    return {"ok": ok, "message": "중단을 요청했습니다. 현재 작업을 마치면 멈춥니다." if ok
            else "이미 마무리 단계라 중단할 수 없습니다."}


def progress_of(job_id: int) -> dict:
    """이 작업의 실행 중 단계 진행 문구 · 경과 시간."""
    store = JobStageStore(job_id)
    out = {}
    for side in (S.SIDE_SUBMISSION, S.SIDE_APPROVAL):
        for r in store.stages(side):
            if r.status == RUNNING:
                info = progress.get(progress_key(store, r.id)) or {}
                started = info.get("started_at") or time.time()
                out[str(r.id)] = {"message": info.get("message", "실행 중…"), "elapsed": int(time.time() - started),
                                  "here": r.id in _running}
    return out


# ── 검수 · 되돌리기 ──────────────────────────────────────────────────────────

def revert_preview(job_id: int, stage_id: int) -> dict:
    store, st = _store_and_stage(job_id, stage_id)
    _check_revertable(store, st)
    return {"wipe": SR.preview(get_state(store.key) or {}, st.stage_key)}


def _check_revertable(store, st) -> None:
    rows = store.stages(st.side)
    active = [r for r in rows if r.status != PENDING]
    if not active or active[-1].id != st.id:
        raise ActionError("갈래에서 마지막으로 손댄 단계만 되돌릴 수 있습니다")
    if st.status == RUNNING:
        raise ActionError("실행 중인 단계는 먼저 중단하세요")


def revert(job_id: int, stage_id: int, user: dict) -> dict:
    store, st = _store_and_stage(job_id, stage_id)
    _check_revertable(store, st)
    store.set_status(stage_id, PENDING, user["username"])
    _reset_review_state(store.key, st.stage_key)
    wiped = SR.reset(store.key, st.stage_key)
    return {"ok": True, "wiped": wiped,
            "message": f"'{S.stage_label(st.stage_key, st.side)}' 을(를) 대기로 되돌렸습니다."
                       + (f" 작업물 {len(wiped)}가지를 지웠습니다." if wiped else " 지울 작업물은 없었습니다.")}


def review_done(job_id: int, stage_id: int, user: dict) -> dict:
    """'검수 완료' — 업로드가 없는 단계용. Approval 의 SP · KB 는 upload() 로 올린 뒤 끝난다."""
    store, st = _store_and_stage(job_id, stage_id)
    if st.status != REVIEW:
        raise ActionError("검수 대기 상태가 아닙니다")
    if st.stage_key in ("release_note_full", "release_note"):
        raise ActionError("Release Note 단계는 RN 결재(1차 · 2차)로 끝납니다")
    if st.stage_key in UPLOAD_STAGES and st.side == S.SIDE_APPROVAL:
        raise ActionError("실제 업로드로 끝내거나 Skip 하세요")
    store.set_status(stage_id, DONE, user["username"])
    return {"ok": True, "message": "검수를 완료했습니다"}


def skip(job_id: int, stage_id: int, user: dict) -> dict:
    """Approval 의 SP · KB 를 업로드 없이 완료로."""
    store, st = _store_and_stage(job_id, stage_id)
    if not (st.stage_key in UPLOAD_STAGES and st.side == S.SIDE_APPROVAL):
        raise ActionError("Skip 은 Service Portal · Knowledge Base 업로드에만 있습니다")
    store.set_status(stage_id, DONE, user["username"])
    return {"ok": True, "message": "업로드 없이 완료로 넘겼습니다"}


def rework(job_id: int, stage_id: int, instruction: str, user: dict) -> dict:
    store, st = _store_and_stage(job_id, stage_id)
    if st.stage_key not in REWORK_STAGES:
        raise ActionError("이 단계는 추가 지시 재작업을 지원하지 않습니다")
    if st.stage_key == "release_note_full" and _rn_review_locked(store.key):
        raise ActionError("RN 결재가 요청 · 승인된 뒤에는 재작업할 수 없습니다")
    if not instruction.strip():
        raise ActionError("수정 지시를 적으세요")
    me = user["username"]
    ok, why = store.claim(stage_id, me)
    if not ok:
        raise ActionError(why)
    job = J.get(job_id) or {}
    pk = progress_key(store, stage_id)

    def work():
        try:
            out = rework_stage(st.stage_key, _subject(job), user["id"], st.input_params or "",
                               store.key, instruction.strip(), pk)
            if out.cancelled:
                store.set_status(stage_id, REVIEW, me)
            else:
                store.save_result(stage_id, out.result_text, out.artifact_type, out.artifact_path, me)
                _reset_review_state(store.key, st.stage_key)
        except Exception as e:  # noqa: BLE001
            store.save_result(stage_id, f"✘ 재작업 중 오류: {type(e).__name__}: {e}", "", "", me)
        finally:
            progress.clear(pk)

    threading.Thread(target=work, name=f"rework-{stage_id}", daemon=True).start()
    return {"ok": True, "message": "재작업을 시작했습니다"}


# ── 단계 입력값 ───────────────────────────────────────────────────────────────

def set_input(job_id: int, stage_id: int, text: str) -> dict:
    store, _ = _store_and_stage(job_id, stage_id)
    store.set_input(stage_id, text)
    return {"ok": True}


def set_state_value(job_id: int, key: str, value: str) -> dict:
    """작업 상태 값 직접 지정 — 허용된 키만(RN 링크 직접 지정)."""
    allowed = {"rn_confluence_link"}
    if key not in allowed:
        raise ActionError("바꿀 수 없는 값입니다")
    sk = J.state_key(job_id)
    update_state(sk, **{key: value.strip()})
    if key == "rn_confluence_link":
        from app.pipeline import newsletter_ws as NLWS

        to = value.strip() or str((get_state(sk) or {}).get("rn_link") or "")
        if to:
            NLWS.update_fields(sk, rn_link=to)
    return {"ok": True}


# ── 실제 업로드 (Approval 의 Service Portal · Knowledge Base) ─────────────────

_SRC = {"service_portal": "service_portal", "release_note_edit": "release_notes"}
_UPLOAD_STAGE = {"service_portal": "service_portal", "release_notes": "release_note_edit"}


def _still_open(item: dict) -> bool:
    """사본 작업이 아직 올리지 않았는가 — 업로드 · Skip 으로 끝난 작업의 사본은 막지 않는다."""
    from app.task_store import approval_stage_status

    stage = _UPLOAD_STAGE.get(str(item.get("source", "")))
    if not stage:
        return True
    try:
        return approval_stage_status(item.get("mail_id"), stage) != DONE
    except Exception:  # noqa: BLE001
        return True


def upload_plan(job_id: int, stage_id: int) -> dict:
    """올릴 사본 목록과 충돌 — 확인 창에 보여 줄 것."""
    from app.pipeline import review_workspace as WS

    store, st = _store_and_stage(job_id, stage_id)
    source = _SRC.get(st.stage_key)
    if not source or st.side != S.SIDE_APPROVAL:
        raise ActionError("업로드 단계가 아닙니다")
    items = [it for it in WS.list_items(store.key) if it.get("source") == source]
    clash = [{"doc_id": it.get("doc_id"), "title": it.get("title", ""), "other": o.get("mail_id")}
             for it in items for o in WS.others_on_doc(it) if _still_open(o)]
    return {
        "source": source,
        "items": [{"doc_id": it.get("doc_id"), "title": it.get("title", ""),
                   "new": bool(it.get("kb_new") or it.get("sp_new"))} for it in items],
        "clash": clash,
        "blocked": bool(clash) and source == "release_notes",
    }


def upload(job_id: int, stage_id: int, user: dict) -> dict:
    """검수 사본으로 실제 페이지를 교체(또는 새로 만듦). 모두 성공하면 단계를 완료로."""
    from app.integrations import confluence_reader as RN
    from app.integrations import service_portal as SP
    from app.pipeline import history as H
    from app.pipeline import review_workspace as WS

    plan = upload_plan(job_id, stage_id)
    if not plan["items"]:
        raise ActionError("올릴 검수 사본이 없습니다. 먼저 Submission 쪽 편집 단계를 실행하세요")
    if plan["blocked"]:
        raise ActionError("같은 페이지를 고치는 다른 작업이 있습니다: "
                          + ", ".join(f"#{c['doc_id']} ← {c['other']}" for c in plan["clash"]))
    store, st = _store_and_stage(job_id, stage_id)
    source = plan["source"]
    uid = user["id"]
    results = []
    items = [it for it in WS.list_items(store.key) if it.get("source") == source]
    for it in items:
        did = str(it.get("doc_id"))
        content = it.get("edited_html", "") or it.get("original_html", "")
        if source == "service_portal":
            content = SP.keep_head(it.get("original_html", ""), content)
        if source == "release_notes":
            from app.pipeline.kb_rn import sdcard_full_url, set_os_download_link

            content, _ = set_os_download_link(content, sdcard_full_url(get_state(store.key) or {}))
        _new, _spnew = it.get("kb_new"), it.get("sp_new")
        if _spnew:
            nid, err = SP.create_doc(uid, _spnew["src_detail"], _spnew["title"], content, True)
            ok, msg, did = bool(nid), (f"'{_spnew['title']}' 문서를 새로 만들었습니다 (#{nid})" if nid else err), nid or _spnew["title"]
        elif source == "service_portal":
            ok, msg = SP.save_real_cont(uid, did, content, True)
        elif _new:
            from app.pipeline import kb_new_page as KNEW

            pid, url, err = KNEW.create_page(uid, _new["title"], content, _new.get("space") or KNEW.DEFAULT_SPACE, True)
            ok, msg, did = bool(pid), (f"'{_new['title']}' 페이지를 새로 만들었습니다 — {url}" if pid else err), pid or _new["title"]
        else:
            ok, msg = RN.save_real_release_note(uid, did, content, True)
        results.append({"doc_id": did, "ok": bool(ok), "message": msg})
    if results and all(r["ok"] for r in results):
        store.set_status(stage_id, DONE, user["username"])
        kind = "sp" if source == "service_portal" else "kb"
        for it in items:
            c = it.get("edited_html", "") or it.get("original_html", "")
            H.record(store.key, kind, "업로드(교체)", actor=user["username"], summary="원본 페이지 교체",
                     text=H.text_from_html(c), doc_key=str(it.get("doc_id", "")), title=it.get("title", ""))
    return {"ok": all(r["ok"] for r in results), "results": results}


# ── 선별표 (RN 반영 항목 · 수정 내용 · 비고) ─────────────────────────────────

EXCLUDE_NOTES = ("내부 이슈", "특정 고객 전용", "이슈 확인 안됨", "SW 요청으로 제외")


def selection(job_id: int) -> dict:
    """선별표 — 기존 편집기와 같은 규칙으로 정규화하고 시스템 행(보안패치·뭉침)을 붙인다."""
    from app.pipeline.rn_research import with_system_rows
    from app.pipeline.rn_select import normalize_rows

    key = J.state_key(job_id)
    st = get_state(key) or {}
    rows = normalize_rows(st.get("rn_select_rows"), edited=bool(st.get("rn_select_edited_at")))
    rows = with_system_rows(st, rows)
    mid = (st.get("rn_mid_review") or {}).get("status") or "none"
    fin = (st.get("rn_review") or {}).get("status") or "none"
    return {
        "rows": rows,
        "edited_at": st.get("rn_select_edited_at", ""), "edited_by": st.get("rn_select_edited_by", ""),
        "mid": mid, "final": fin,
        # 1차 승인 뒤에는 RN for Sales 만 다시 만든다(이력본은 확정 · 저장된 문서)
        "with_full": mid != "approved",
        # 결재가 요청됐거나 2차가 끝났으면 잠근다
        "locked": mid == "requested" or fin in ("requested", "approved"),
        "exclude_notes": list(EXCLUDE_NOTES),
    }


def save_selection(job_id: int, rows: list[dict], user: dict) -> dict:
    """반영/미반영 · 수정 내용 · 직접 추가한 행을 저장하고 RN 을 다시 만든다(기존 '저장 · RN 재생성')."""
    from app.pipeline.rn_research import korean_lines, rebuild_revision_from_selection, regenerate_rn
    from app.pipeline.rn_select import normalize_rows
    from app.pipeline import history as H

    info = selection(job_id)
    if info["locked"]:
        raise ActionError("결재가 요청 · 확정돼 선별표를 고칠 수 없습니다")
    key = J.state_key(job_id)
    new_rows = []
    for r in rows:
        r2 = dict(r)
        r2["apply_note"] = str(r2.get("apply_note") or "").strip()
        if r2.get("manual"):
            r2["include"] = "Y"
            r2["issue"] = r2["apply_note"]
            if not r2["apply_note"]:
                continue
        else:
            r2["include"] = "Y" if str(r2.get("include")) == "Y" else "X"
        new_rows.append(r2)
    new_rows = normalize_rows(new_rows)
    update_state(key, rn_select_rows=new_rows, rn_select_edited_at=datetime.now().isoformat(),
                 rn_select_edited_by=user["username"])
    lines, note = rebuild_revision_from_selection(key)
    if note:
        lines = list((get_state(key) or {}).get("rn_revision_lines") or [])
    regenerate_rn(key, "\n".join(f"- {ln}" for ln in lines), with_full=info["with_full"], with_sales=True)
    H.record(key, "rn", "선별표·입력값 수정", actor=user["username"],
             summary="반영 항목·수정 내용 수정 후 재생성", text=H.text_from_rn(get_state(key) or {}))
    ko = korean_lines(lines)
    return {"ok": True, "lines": len(lines), "korean": ko[:5],
            "message": "저장하고 RN 을 다시 만들었습니다" + (f" — 한국어 줄 {len(ko)}개" if ko else "")}


def save_exclude_note(job_id: int, no: str, value: str) -> dict:
    """비고(미반영 사유) — 그 자리에서 저장, 문서는 다시 만들지 않는다."""
    if value and value not in EXCLUDE_NOTES:
        raise ActionError("고를 수 있는 비고가 아닙니다")
    key = J.state_key(job_id)
    saved = [dict(x) for x in ((get_state(key) or {}).get("rn_select_rows") or [])]
    hit = False
    for rr in saved:
        if str(rr.get("no")) == str(no):
            rr["exclude_note"] = value or ""
            hit = True
    if not hit:
        raise ActionError("그 번호의 행이 없습니다")
    update_state(key, rn_select_rows=saved)
    return {"ok": True}
