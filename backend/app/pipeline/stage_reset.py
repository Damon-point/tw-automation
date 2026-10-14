# -*- coding: utf-8 -*-
"""되돌리기 — **그 단계가 만든 것**을 상태에서 지운다.

예전에는 단계 상태만 '대기' 로 되돌리고 작업물은 그대로 두었다. 그래서 되돌린 뒤 다시
실행해도 앞선 실행이 남긴 값이 살아 있어, 새로 시작한 것처럼 보이지 않았다.
실측 2026-09-21 MF52 job-26: 사람이 파일서버에 RN 을 올려 두었는데도 8월에 시스템이
만든 빈 문서를 그대로 물고 있었고, `rn_full_skipped` 가 남아 있어 [건너뛰기] 는
"이미 건너뛴 단계입니다" 로 되돌아왔다.

**그 단계가 만든 것만** 지운다. 앞 단계가 정한 값(제품·버전·근거 메일 따위)은 건드리지
않는다 — 되돌리기는 '이 단계를 다시 한다' 는 뜻이지 작업을 처음으로 되감는 것이 아니다.
"""
from __future__ import annotations

#: 단계 → 그 단계가 만드는 상태 키. 여기 적힌 것만 지운다.
STAGE_OUTPUT_KEYS: dict[str, tuple[str, ...]] = {
    # 리서치·선별표·컴포넌트·이력본·1차 승인·건너뛰기 표시가 모두 이 단계의 산물이다.
    "release_note_full": (
        "rn_full_skipped", "rn_full_skip_reason", "rn_full_skipped_by",
        "rn_full_docx_path", "rn_full_docx_name", "rn_full_rev_count",
        "rn_full_pdf_path", "rn_full_pdf_name", "rn_full_pdf_error",
        "rn_full_archived", "rn_full_archive_note",
        "rn_initial_docx_path", "rn_initial_docx_name",
        "rn_mid_review", "rn_mid_reviewed_path", "rn_mid_reviewed_name",
        "rn_select_rows", "rn_select_edited_at", "rn_select_edited_by",
        "rn_revision_lines", "rn_revision_draft",
        "rn_changes", "rn_public_changes",
        "rn_components", "rn_component_src", "rn_component_vers",
        "rn_input_fields", "rn_prev_revisions", "rn_prev_source", "rn_prev_error",
        "rn_first_release", "rn_rev",
        "security_patch", "security_patch_src",
        "rn_existing_pdf_path", "rn_from_fileserver",
        "rn_doc_date", "version_full",
    ),
    # RN for Sales 는 공개본 문서와 2차 검수가 산물이다. 선별표·컴포넌트는 앞 단계 것이라
    # 그대로 둔다 — 여기서 지우면 되돌리기 한 번에 리서치까지 날아간다.
    "release_note": (
        "rn_docx_path", "rn_docx_name", "rn_pdf_path", "rn_pdf_name",
        "rn_final_pdf_path", "rn_sales_archived", "rn_sales_archive_note",
        "rn_review",
    ),
    "newsletter": ("newsletter_paths", "newsletter_review"),
    "release_note_edit": ("rn_confluence_link", "kb_uploads"),
}


def outputs_of(stage_key: str) -> tuple[str, ...]:
    return STAGE_OUTPUT_KEYS.get(stage_key, ())


def preview(st: dict, stage_key: str) -> list[str]:
    """지워질 값 중 **실제로 들어 있는** 키만. 확인 창에 몇 개인지 보여주려는 것."""
    st = st or {}
    return [k for k in outputs_of(stage_key)
            if st.get(k) not in (None, "", [], {}, False)]


def reset(task_key, stage_key) -> list[str]:
    """그 단계의 작업물을 지운다. 지운 키 목록을 돌려준다.

    값의 생김새대로 비운다 — 목록은 [], 사전은 {}, 그 밖은 "". 키 자체를 지우지 않는 건
    상태 파일을 읽는 쪽이 `.get(k)` 로 형을 가정하는 자리가 있어서다.
    """
    from app.pipeline.state import get_state, update_state

    if task_key is None or not stage_key:
        return []
    st = get_state(task_key) or {}
    wipe: dict = {}
    for k in outputs_of(stage_key):
        cur = st.get(k)
        if cur in (None, "", [], {}, False):
            continue
        wipe[k] = [] if isinstance(cur, list) else ({} if isinstance(cur, dict) else "")
    if wipe:
        update_state(task_key, **wipe)
    return sorted(wipe)
