"""OS Update 작업 — 단계가 읽어 가는 입력값(기존 보드의 펼침 칸 셋 + RN 링크).

  · OS Download 버전 지정 — os_download 단계 입력 'version: 95.07'. 비우면 자동 판별
  · RN 컴포넌트 — release_note_full 단계 입력 'Audio: …' 줄들. 단말에서만 확인 가능한 값
  · Revision History 초안 — 상태 rn_revision_draft_manual. 채우면 RN 제작이 그대로 쓴다
  · RN 링크 직접 지정 — 상태 rn_confluence_link (쓰기는 main.py 의 /state)
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app import os_update_jobs as J
from app import stages as S
from app.pipeline.state import get_state, update_state
from app.task_store import JobStageStore
from common import user

router = APIRouter(prefix="/api/jobs/{job_id}/inputs")

# (화면 이름, 저장 라벨, 내부 키) — 기존 보드와 같은 순서 · 같은 라벨
COMPONENTS = [
    ("Audio parameter", "Audio", "audio"),
    ("Touch panel", "Touch", "touch"),
    ("Wi-Fi Driver", "WiFi Driver", "wifi_driver"),
    ("Wi-Fi Firmware", "WiFi Firmware", "wifi_firmware"),
    ("Wi-Fi Config", "WiFi Config", "wifi_config"),
    ("Known Issues", "Known Issues", "known_issues"),
]


def _stage(job_id: int, side: str, key: str):
    store = JobStageStore(job_id)
    for r in store.stages(side):
        if r.stage_key == key:
            return store, r
    return store, None


@router.get("")
def inputs(job_id: int, request: Request):
    user(request)
    if not J.get(job_id):
        raise HTTPException(404, "작업이 없습니다")
    from app.pipeline.os_download import _override_version
    from app.pipeline.rn_research import _parse_components

    st = get_state(J.state_key(job_id)) or {}
    _, osr = _stage(job_id, S.SIDE_APPROVAL, "os_download")
    _, rnr = _stage(job_id, S.SIDE_SUBMISSION, "release_note_full")
    comps = _parse_components(rnr.input_params or "") if rnr else {}
    return {
        "os_version": {
            "value": _override_version(osr.input_params or "") if osr else "",
            "inherited": str(st.get("version_override") or "").strip(),
            "editable": bool(osr and osr.status != S.DONE),
        },
        "components": {
            "fields": [{"key": k, "label": lab, "value": comps.get(k, "")} for lab, _s, k in COMPONENTS],
            "editable": bool(rnr and rnr.status != S.DONE),
        },
        "revision_draft": {
            "value": str(st.get("rn_revision_draft_manual") or ""),
            "editable": bool(rnr and rnr.status != S.DONE),
        },
        "rn_link": {"manual": str(st.get("rn_confluence_link") or ""), "auto": str(st.get("rn_link") or "")},
    }


class VersionIn(BaseModel):
    version: str = ""


@router.post("/os-version")
def set_os_version(job_id: int, body: VersionIn, request: Request):
    user(request)
    store, r = _stage(job_id, S.SIDE_APPROVAL, "os_download")
    if r is None:
        raise HTTPException(409, "OS Download 단계가 없습니다")
    if r.status == S.DONE:
        raise HTTPException(409, "OS Download 가 이미 끝났습니다")
    v = body.version.strip()
    if v and not re.fullmatch(r"[A-Za-z0-9._-]+", v):
        raise HTTPException(409, "버전 형식이 아닙니다 (예: 95.07)")
    store.set_input(r.id, f"version: {v}" if v else "")
    return {"ok": True, "message": f"버전 {v} 로 받습니다" if v else "자동 판별로 되돌렸습니다"}


class ComponentsIn(BaseModel):
    fields: dict[str, str]


@router.post("/components")
def set_components(job_id: int, body: ComponentsIn, request: Request):
    user(request)
    store, r = _stage(job_id, S.SIDE_SUBMISSION, "release_note_full")
    if r is None:
        raise HTTPException(409, "전체 이력 RN 제작 단계가 없습니다")
    if r.status == S.DONE:
        raise HTTPException(409, "RN 제작이 이미 끝났습니다")
    lines = [f"{lab}: {str(body.fields.get(k) or '').strip()}"
             for _n, lab, k in COMPONENTS if str(body.fields.get(k) or "").strip()]
    store.set_input(r.id, "\n".join(lines))
    return {"ok": True, "message": "컴포넌트 값을 저장했습니다"}


class DraftIn(BaseModel):
    text: str = ""


@router.post("/revision-draft")
def set_revision_draft(job_id: int, body: DraftIn, request: Request):
    user(request)
    update_state(J.state_key(job_id), rn_revision_draft_manual=body.text.strip())
    return {"ok": True, "message": "Revision History 초안을 저장했습니다"}
