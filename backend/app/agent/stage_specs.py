"""단계별 에이전트 사양: 목표(goal), 사용 가능한 도구, 검수 기준(rubric).

os_download 는 실제 도구(SFTP→Dropbox→Excel)를 사용해 완전 자동화.
나머지 단계는 아직 실제 도구가 없어 record_note 로 수행 내용을 기록(추후 확장).
"""
from __future__ import annotations

from app.stages import STAGE_LABELS

# 단계별 사용 가능한 도구
STAGE_TOOLS: dict[str, list[str]] = {
    "os_download": ["fileserver_list", "fileserver_download", "dropbox_upload", "make_excel", "record_note"],
    "release_note": [
        "release_notes_get_page",
        "release_notes_edit_page",
        "make_pdf",
        "record_note",
    ],
    "fota_update": ["record_note"],
    "service_portal": ["service_portal_get_page", "service_portal_edit_page", "record_note"],
    "kb_update": ["record_note"],
    "newsletter": ["make_pdf", "record_note"],
}

# 단계별 목표/체크리스트
_GOALS: dict[str, str] = {
    "os_download": (
        "내부 파일 서버에서 OS 파일을 찾아 내려받아 Dropbox 에 올리고, "
        "각 파일명과 Dropbox 다운로드 링크를 담은 엑셀을 만드세요.\n"
        "경로가 정확히 주어지지 않았으면 fileserver_list 로 폴더를 탐색해 대상 OS 파일을 먼저 찾으세요.\n"
        "체크리스트: (1) 대상 파일을 찾아 fileserver_download 로 받았는가 "
        "(2) 받은 파일을 dropbox_upload 로 모두 올려 링크를 얻었는가 "
        "(3) make_excel 로 파일명+링크 표를 만들었는가. "
        "실패 시 원인을 파악해 재시도하고, 끝까지 안 되면 명확히 보고하세요.\n"
        "참고 정보(대상 경로/파일 힌트):\n{input_params}"
    ),
    "release_note": (
        "이 제출 건의 Release Note 내용을 작성하고, 회사의 'OS/FW Release Notes' 페이지에 반영하세요. "
        "먼저 release_notes_get_page 로 현재 페이지 HTML 을 확인한 뒤, 내용을 갱신해 "
        "release_notes_edit_page 로 저장하세요. (원하면 make_pdf 로 사본도 생성 가능) "
        "대상 pageId 가 지시에 없으면 record_note 로 남기고 멈추세요.\n대상 정보:\n{input_params}"
    ),
    "fota_update": (
        "FOTA 업데이트 작업을 수행하세요. 현재는 실제 연동 도구가 없으므로, "
        "수행해야 할 작업 절차와 상태를 record_note 로 구체적으로 기록하세요."
    ),
    "service_portal": (
        "Service Portal 의 'Download' 페이지를 업데이트하세요. "
        "먼저 service_portal_get_page 로 현재 페이지 HTML 을 확인한 뒤, 필요한 내용을 반영해 "
        "service_portal_edit_page 로 저장하세요. "
        "대상 pageId 가 지시에 없으면 record_note 로 남기고 멈추세요.\n대상 정보:\n{input_params}"
    ),
    "kb_update": (
        "KB(지식베이스) 업데이트 작업을 수행하세요. 현재는 실제 연동 도구가 없으므로, "
        "수행 절차와 상태를 record_note 로 기록하세요."
    ),
    "newsletter": (
        "Newsletter 를 제작하세요. 초안 내용을 make_pdf 로 문서화하고, "
        "발송 절차/대상은 record_note 로 기록하세요. (실제 발송 연동은 추후)"
    ),
}


def goal_for(stage_key: str, subject: str, input_params: str) -> str:
    label = STAGE_LABELS.get(stage_key, stage_key)
    body = _GOALS.get(stage_key, f"'{label}' 단계를 수행하고 결과를 record_note 로 기록하세요.")
    body = body.format(input_params=input_params or "(경로 미지정)")
    return f"작업 대상 메일 제목: {subject}\n단계: {label}\n\n{body}"


def tools_for(stage_key: str) -> list[str]:
    return STAGE_TOOLS.get(stage_key, ["record_note"])
