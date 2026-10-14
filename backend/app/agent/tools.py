"""Claude 에이전트가 사용할 도구(tool) 정의 + 실제 실행.

기존 기능(SFTP/Dropbox/Excel/PDF)을 Claude 가 호출할 수 있는 도구로 래핑한다.
도구는 우리 앱(서버)에서 실행되며, 결과 문자열을 Claude 에게 돌려준다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from app.atomic_io import atomic_write_bytes
from app.integrations.creds import get_dropbox_auth
from app.integrations.dropbox_client import upload_files
from app.integrations.fileserver import get_backend
from app.office.excel_writer import build_workbook_bytes
from app.office.pdf_tool import build_pdf


@dataclass
class ToolContext:
    """한 단계 실행 동안 공유되는 상태."""

    user_id: int
    subject: str
    workdir: str
    downloaded: dict[str, str] = field(default_factory=dict)  # name -> local path
    links: dict[str, str] = field(default_factory=dict)       # name -> download link
    artifact_type: str = ""   # 최종 산출물 (검수 화면 열람용)
    artifact_path: str = ""
    notes: list[str] = field(default_factory=list)


# --- 개별 도구 구현 (ctx, input) -> 결과 문자열 -----------------------
def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024.0
    return f"{n:.1f}GB"


def _t_fileserver_list(ctx: ToolContext, args: dict) -> str:
    path = str(args.get("path", "")).strip()
    backend = get_backend(ctx.user_id)
    if backend is None:
        return "오류: 파일 서버가 설정되지 않았습니다. 설정에서 등록이 필요합니다."
    try:
        items = backend.list_dir(path)
    except Exception as e:  # noqa: BLE001
        return f"목록 조회 실패('{path or '기준경로'}'): {e}"
    if not items:
        return f"'{path or '기준경로'}' 는 비어 있습니다."
    lines = [f"'{path or '기준경로'}' 내용 ({len(items)}개):"]
    for it in items:
        if it.is_dir:
            lines.append(f"  [폴더] {it.name}/")
        else:
            lines.append(f"        {it.name}  ({_fmt_size(it.size)})")
    return "\n".join(lines)


def _t_fileserver_download(ctx: ToolContext, args: dict) -> str:
    paths = [p.strip() for p in args.get("paths", []) if str(p).strip()]
    if not paths:
        return "오류: paths(기준경로 기준 상대경로) 가 비어 있습니다."
    backend = get_backend(ctx.user_id)
    if backend is None:
        return "오류: 파일 서버가 설정되지 않았습니다. 설정에서 등록이 필요합니다."
    lines = []
    for rel in paths:
        try:
            dst, size = backend.download(rel, ctx.workdir)
            name = os.path.basename(dst)
            ctx.downloaded[name] = dst
            lines.append(f"성공: {rel} -> {name} ({size} bytes)")
        except Exception as e:  # noqa: BLE001
            lines.append(f"실패: {rel} — {e}")
    return "\n".join(lines) if lines else "다운로드 결과 없음"


def _t_dropbox_upload(ctx: ToolContext, args: dict) -> str:
    names = args.get("filenames")
    if names:
        locals_ = [ctx.downloaded[n] for n in names if n in ctx.downloaded]
    else:
        locals_ = list(ctx.downloaded.values())
    if not locals_:
        return "오류: 업로드할 다운로드 파일이 없습니다. 먼저 sftp_download 를 사용하세요."
    token = get_dropbox_auth(ctx.user_id)
    if not token:
        return "오류: Dropbox 자격증명이 없습니다. 설정에서 등록이 필요합니다."
    results = upload_files(token, locals_)
    lines = []
    for r in results:
        if r.ok:
            ctx.links[r.name] = r.link
            lines.append(f"성공: {r.name} -> {r.link}")
        else:
            lines.append(f"실패: {r.name} — {r.error}")
    return "\n".join(lines) if lines else "업로드 결과 없음"


def _t_make_excel(ctx: ToolContext, args: dict) -> str:
    rows_in = args.get("rows", [])
    headers = args.get("headers") or ["파일명", "다운로드 링크"]
    rows = [[str(c) for c in row] for row in rows_in]
    data = build_workbook_bytes(headers, rows, sheet_name=args.get("sheet_name", "Sheet1"))
    filename = args.get("filename", "output.xlsx")
    if not filename.endswith(".xlsx"):
        filename += ".xlsx"
    path = os.path.join(ctx.workdir, filename)
    atomic_write_bytes(path, data)
    ctx.artifact_type = "excel"
    ctx.artifact_path = path
    return f"엑셀 생성 완료: {filename} ({len(rows)}행)"


def _t_make_pdf(ctx: ToolContext, args: dict) -> str:
    text = args.get("text", "")
    title = args.get("title", "")
    data = build_pdf(text, title=title)
    filename = args.get("filename", "output.pdf")
    if not filename.endswith(".pdf"):
        filename += ".pdf"
    path = os.path.join(ctx.workdir, filename)
    atomic_write_bytes(path, data)
    ctx.artifact_type = "pdf"
    ctx.artifact_path = path
    return f"PDF 생성 완료: {filename}"


def _t_record_note(ctx: ToolContext, args: dict) -> str:
    note = args.get("summary", "")
    ctx.notes.append(note)
    return "기록했습니다."


def _web_get(ctx: ToolContext, target: str, args: dict) -> str:
    from app.agent.browser import get_content

    page_id = str(args.get("page_id", "")).strip()
    if not page_id:
        return "오류: page_id 가 필요합니다."
    return f"현재 본문 HTML:\n{get_content(ctx.user_id, target, page_id)}"


def _web_edit(ctx: ToolContext, target: str, args: dict) -> str:
    # 프로덕션 쓰기 영구 금지(안전 정책). 편집·저장은 로컬 에디터에서 사람이 수행한다.
    return (
        "편집 저장은 비활성화되어 있습니다(프로덕션 쓰기 금지). "
        "Service Portal/Release Note 편집은 [도구 → 포털 편집 검수](/portaledit)에서 "
        "실제 데이터를 불러와 사람이 로컬 에디터로 편집·확인합니다."
    )


# Service Portal(Download 페이지) — Confluence 형 편집기
def _t_service_portal_get(ctx: ToolContext, args: dict) -> str:
    return _web_get(ctx, "service_portal", args)


def _t_service_portal_edit(ctx: ToolContext, args: dict) -> str:
    return _web_edit(ctx, "service_portal", args)


# OS/FW Release Notes 페이지(회사 페이지, Naver 편집기 임베드)
def _t_release_notes_get(ctx: ToolContext, args: dict) -> str:
    return _web_get(ctx, "release_notes", args)


def _t_release_notes_edit(ctx: ToolContext, args: dict) -> str:
    return _web_edit(ctx, "release_notes", args)


# --- 도구 스키마 (Claude 에게 전달) ----------------------------------
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "fileserver_list",
        "description": "내부 파일 서버(SFTP 또는 SMB)의 디렉토리 내용(폴더/파일과 크기)을 본다. "
        "OS 파일을 찾기 위해 폴더를 탐색할 때 사용. path 를 바꿔가며 하위 폴더를 살펴볼 수 있다. "
        "경로는 설정된 기준경로(base) 기준 상대경로다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "볼 디렉토리(기준경로 기준 상대). 생략 시 기준경로 최상위.",
                }
            },
        },
    },
    {
        "name": "fileserver_download",
        "description": "내부 파일 서버에서 지정한 파일들을 내려받는다. "
        "경로는 기준경로(base) 기준 상대경로 목록.",
        "input_schema": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "내려받을 파일 경로(기준경로 기준 상대) 목록",
                }
            },
            "required": ["paths"],
        },
    },
    {
        "name": "dropbox_upload",
        "description": "이전에 sftp_download 로 받은 파일을 Dropbox 에 올리고 다운로드 링크를 만든다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filenames": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "올릴 파일명 목록. 생략하면 받은 파일 전체를 올린다.",
                }
            },
        },
    },
    {
        "name": "make_excel",
        "description": "표(헤더+행)를 엑셀(.xlsx) 파일로 생성한다. 이 단계의 산출물이 된다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "headers": {"type": "array", "items": {"type": "string"}},
                "rows": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "string"}},
                },
                "filename": {"type": "string"},
                "sheet_name": {"type": "string"},
            },
            "required": ["rows"],
        },
    },
    {
        "name": "make_pdf",
        "description": "텍스트로 PDF 문서를 생성한다(한글 지원). 이 단계의 산출물이 된다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "title": {"type": "string"},
                "filename": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "record_note",
        "description": "수행한 작업 내용/결과를 기록한다. 실제 자동화 도구가 아직 없는 단계에서 사용.",
        "input_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    },
    {
        "name": "service_portal_get_page",
        "description": "Service Portal 의 Download 페이지 현재 본문 HTML 을 가져온다(수정 전 확인용).",
        "input_schema": {
            "type": "object",
            "properties": {"page_id": {"type": "string", "description": "페이지 ID(pageId)"}},
            "required": ["page_id"],
        },
    },
    {
        "name": "service_portal_edit_page",
        "description": "Service Portal 의 Download 페이지 본문을 새 HTML 로 교체·저장한다. "
        "먼저 service_portal_get_page 로 현재 내용을 확인한 뒤 수정본을 넣는 것을 권장.",
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string"},
                "new_html": {"type": "string", "description": "저장할 본문 HTML 전체"},
            },
            "required": ["page_id", "new_html"],
        },
    },
    {
        "name": "release_notes_get_page",
        "description": "OS/FW Release Notes 페이지(회사 Solution/Resources 페이지, Naver 편집기)의 "
        "현재 본문 HTML 을 가져온다(수정 전 확인용).",
        "input_schema": {
            "type": "object",
            "properties": {"page_id": {"type": "string", "description": "페이지/글 ID"}},
            "required": ["page_id"],
        },
    },
    {
        "name": "release_notes_edit_page",
        "description": "OS/FW Release Notes 페이지 본문을 새 HTML 로 교체·저장한다. "
        "먼저 release_notes_get_page 로 현재 내용을 확인한 뒤 수정본을 넣는 것을 권장.",
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string"},
                "new_html": {"type": "string", "description": "저장할 본문 HTML 전체"},
            },
            "required": ["page_id", "new_html"],
        },
    },
]

_TOOL_IMPLS = {
    "fileserver_list": _t_fileserver_list,
    "fileserver_download": _t_fileserver_download,
    "dropbox_upload": _t_dropbox_upload,
    "make_excel": _t_make_excel,
    "make_pdf": _t_make_pdf,
    "record_note": _t_record_note,
    "service_portal_get_page": _t_service_portal_get,
    "service_portal_edit_page": _t_service_portal_edit,
    "release_notes_get_page": _t_release_notes_get,
    "release_notes_edit_page": _t_release_notes_edit,
}


def dispatch(ctx: ToolContext, name: str, args: dict) -> str:
    """도구 이름으로 실제 구현을 호출. 예외는 문자열로 반환(루프가 계속되도록)."""
    impl = _TOOL_IMPLS.get(name)
    if impl is None:
        return f"오류: 알 수 없는 도구 '{name}'"
    try:
        return impl(ctx, args or {})
    except Exception as e:  # noqa: BLE001
        return f"도구 실행 오류({name}): {e}"


def schemas_for(tool_names: list[str]) -> list[dict]:
    return [s for s in TOOL_SCHEMAS if s["name"] in tool_names]
