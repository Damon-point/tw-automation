"""단계 실행 진입점.

이제 각 단계는 Claude 주도 에이전트가 수행한다(app/agent/runner.py).
UI(task_board)는 여전히 execute_stage(...) 를 run.io_bound 로 호출한다.
"""
from __future__ import annotations

from app.agent.runner import StageOutput, run_stage_agent

__all__ = ["StageOutput", "execute_stage", "rework_stage"]


# 아직 자동화 로직이 없는 단계 — Claude 에이전트로 넘기면 '상상 계획'을 만들어내므로,
# 실제 스펙·다음 Phase 를 안내하는 정직한 자리표시로 고정한다(환각 방지).
_NOT_IMPLEMENTED: dict[str, str] = {}


def _reuse_output(stage_key: str, mail_id: int | None) -> StageOutput:
    """Approval 에서 Release Note 제작·Knowledge Base·Newsletter 를 만났을 때 — Submission 승인본을 그대로 쓴다(재생성 없음)."""
    from app.pipeline.state import get_state
    from app.stages import STAGE_LABELS

    st = get_state(mail_id) or {} if mail_id is not None else {}
    label = STAGE_LABELS.get(stage_key, stage_key)
    if stage_key == "release_note":
        path = st.get("rn_pdf_path") or st.get("rn_docx_path") or ""
        kind = "PDF" if st.get("rn_pdf_path") else "docx"
        if not path:
            return StageOutput(result_text=(
                f"[{label}] ✘ Submission 에서 승인된 Release Note 가 없습니다.\n"
                "Submission 쪽에서 Release Note 제작을 먼저 완료하세요."))
        return StageOutput(
            result_text=(f"[{label}] Submission 승인본을 그대로 사용합니다(재생성 없음).\n"
                         f"· RN({kind}): {path}"),
            artifact_type=("pdf" if kind == "PDF" else "docx"), artifact_path=path)
    if stage_key == "release_note_edit":
        # 편집안은 Dropbox 업로드 전에 만들어져 OS Download Link 가 비어 있다 — 지금 채운다.
        from app.pipeline.kb_rn import apply_os_download_link

        _n, _url = apply_os_download_link(mail_id) if mail_id is not None else (0, "")
        _dl = (f"· 최신 버전 블록의 OS Download Link = sdcard FULL zip: {_url}" if _url else
               "· ✘ sdcard FULL zip 링크가 없어 OS Download Link 를 넣지 못했습니다 — "
               "Dropbox 업로드를 먼저 끝내세요.")
        return StageOutput(result_text=(
            f"[{label}] Submission 에서 만든 편집안을 그대로 사용합니다(재생성 없음).\n"
            f"{_dl}\n"
            "· '검수하기'에서 확인 후 [검수 완료]를 누르면 실제 페이지에 반영됩니다."))
    if stage_key == "newsletter":
        return StageOutput(result_text=(
            f"[{label}] Submission 에서 승인된 뉴스레터 초안을 그대로 사용합니다(재생성 없음).\n"
            "· '검수하기'에서 미리보기 확인 후 Mailchimp 발송을 진행하세요."))
    return StageOutput(result_text=f"[{label}] 승인본 재사용.")


def execute_stage(
    stage_key: str,
    subject: str,
    user_id: int,
    input_params: str = "",
    mail_id: int | None = None,
    revision: str = "",
    stage_id: int | None = None,
    side: str | None = None,
) -> StageOutput:
    """단계를 실행하고 결과를 반환.

    revision: 사람이 검수 후 준 '추가 수정 지시'. Claude 기반 단계(② RN·④ Portal)가 반영해 재작업한다.
    결정적 단계는 revision 을 무시하고 재실행한다.
    stage_id: 진행상황(app.progress) 연결용. 주면 실행 중 '지금 하는 일' + 경과시간을 UI 가 표시한다.
    """
    from app import usage
    from app.stages import REUSE_ON_APPROVAL, SIDE_APPROVAL, STAGE_LABELS

    # Approval 의 ②⑥⑦ 는 Submission 승인본을 그대로 쓴다 — 다시 만들지 않는다.
    if side == SIDE_APPROVAL and stage_key in REUSE_ON_APPROVAL:
        return _reuse_output(stage_key, mail_id)

    usage.set_operation(STAGE_LABELS.get(stage_key, stage_key), mail_id)   # Claude 사용량 라벨
    if stage_id is None:
        return _dispatch_stage(stage_key, subject, user_id, input_params, mail_id, revision)
    from app import progress

    progress.begin(stage_id, f"{STAGE_LABELS.get(stage_key, stage_key)} 준비 중…")
    try:
        return _dispatch_stage(stage_key, subject, user_id, input_params, mail_id, revision)
    except progress.StageCancelled:
        return StageOutput(result_text="사용자가 작업을 중단했습니다.", cancelled=True)
    finally:
        progress.end(stage_id)


def _dispatch_stage(
    stage_key: str,
    subject: str,
    user_id: int,
    input_params: str = "",
    mail_id: int | None = None,
    revision: str = "",
) -> StageOutput:
    if stage_key == "os_download":
        from app.pipeline.os_download import run_os_download

        return run_os_download(user_id, subject, input_params, mail_id)
    if stage_key == "release_note_full":
        # 리서치·선별표·컴포넌트 + 전체 이력 RN. 승인자 미리보기용 RN 초안도 여기서 만든다
        # (RN 승인 화면이 rn_docx_path 를 띄운다 — 이 산출물이 없으면 승인 화면이 빈다).
        from app.pipeline.rn_research import run_rn_research

        return run_rn_research(user_id, subject, input_params, mail_id, revision=revision)
    if stage_key == "release_note":
        # RN for Sales — 앞 단계에서 확정된 선별로 공개본만 다시 만든다(리서치 없음).
        from app.pipeline.rn_research import run_rn_sales

        return run_rn_sales(mail_id)
    if stage_key == "dropbox_release":
        from app.pipeline.dropbox_release import run_dropbox_release

        return run_dropbox_release(user_id, subject, input_params, mail_id)
    if stage_key == "excel_docs":
        from app.pipeline.excel_docs import run_excel_docs

        return run_excel_docs(user_id, subject, input_params, mail_id)
    if stage_key == "service_portal":
        from app.pipeline.portal_edit import run_portal_edit

        return run_portal_edit(
            user_id, subject, input_params, mail_id, revision=revision,
            targets=("service_portal",),
        )
    if stage_key == "release_note_edit":
        from app.pipeline.portal_edit import run_portal_edit

        return run_portal_edit(
            user_id, subject, input_params, mail_id, revision=revision,
            targets=("release_notes",),
        )
    if stage_key == "newsletter":
        # ⑦ 근거 기반 KR/EN 초안 생성(발송 X). Mailchimp 연동/발송은 이후 단계.
        from app.pipeline.newsletter import run_newsletter

        return run_newsletter(user_id, subject, input_params, mail_id, revision=revision)
    if stage_key == "dropbox_cleanup":
        # ⑧ 구버전 파일 정리. **이 단계는 아무것도 지우지 않는다** — 등록된 Dropbox 경로
        # 4개를 훑어 무엇이 남아 있는지 보여줄 뿐이고, 삭제는 '검수하기' 팝업에서 사람이
        # 경로별 버튼으로 한다(사용자 확정 2026-08-05).
        from app.pipeline.dropbox_release import cleanup_groups

        groups, err = cleanup_groups(user_id, mail_id)
        if err:
            return StageOutput(result_text=f"[Dropbox 정리]\n✘ {err}")
        lines = []
        for g in groups:
            if g["skipped"]:
                lines.append(f"· {g['label']}: {g['skipped']}")
            else:
                lines.append(f"· {g['label']}: 구버전 {len(g['files'])}개  ({g['base']})")
        total = sum(len(g["files"]) for g in groups)
        return StageOutput(result_text=(
            "[Dropbox 정리 · 구버전 확인]\n"
            f"등록된 Dropbox 경로 4개에서 구버전 파일 {total}개를 찾았습니다.\n"
            "삭제는 '검수하기' 팝업에서 경로별로 직접 실행하세요.\n\n" + "\n".join(lines)
        ))
    if stage_key in _NOT_IMPLEMENTED:
        return StageOutput(result_text=_NOT_IMPLEMENTED[stage_key])
    return run_stage_agent(stage_key, subject, user_id, input_params)


def rework_stage(
    stage_key: str,
    subject: str,
    user_id: int,
    input_params: str = "",
    mail_id: int | None = None,
    revision: str = "",
    stage_id: int | None = None,
) -> StageOutput:
    """추가 지시 재작업 — **완료된 산출물에 지시만 반영**(전체 프로세스 재실행 아님).

    지원 단계(②⑤⑥⑦)는 저장된 결과/검수 작업공간을 재사용해 지시만 반영한다.
    데이터(Redmine/SP/Confluence) 재수집을 하지 않는다.
    """
    from app import usage
    from app.stages import STAGE_LABELS

    usage.set_operation("재작업: " + STAGE_LABELS.get(stage_key, stage_key), mail_id)
    if stage_id is None:
        return _dispatch_rework(stage_key, subject, user_id, input_params, mail_id, revision)
    from app import progress

    progress.begin(stage_id, f"{STAGE_LABELS.get(stage_key, stage_key)} 재작업 준비 중…")
    try:
        return _dispatch_rework(stage_key, subject, user_id, input_params, mail_id, revision)
    except progress.StageCancelled:
        return StageOutput(result_text="사용자가 작업을 중단했습니다.", cancelled=True)
    finally:
        progress.end(stage_id)


def _dispatch_rework(
    stage_key: str,
    subject: str,
    user_id: int,
    input_params: str = "",
    mail_id: int | None = None,
    revision: str = "",
) -> StageOutput:
    if stage_key == "release_note_full":
        # 추가 지시 재작업은 리서치 결과(Revision History)를 고치는 일이라 앞 단계에만 둔다.
        # RN for Sales 는 선별표에서 골라 만드는 문서라 '지시로 재작업' 이 없다.
        from app.pipeline.rn_research import rework_rn

        return rework_rn(mail_id, revision)
    if stage_key == "service_portal":
        from app.pipeline.portal_edit import rework_portal

        return rework_portal(user_id, mail_id, revision, targets=("service_portal",))
    if stage_key == "release_note_edit":
        from app.pipeline.portal_edit import rework_portal

        return rework_portal(user_id, mail_id, revision, targets=("release_notes",))
    if stage_key == "newsletter":
        from app.pipeline.newsletter import rework_newsletter

        return rework_newsletter(mail_id, revision)
    return StageOutput(result_text="이 단계는 추가 지시 재작업을 지원하지 않습니다.")
