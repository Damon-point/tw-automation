"""단계 산출물(PDF/Excel 등) 열람용 라우트.

/artifact/{stage_id} — 로그인한 사용자 본인의 단계 산출물 파일을 반환.
PDF 는 브라우저 내 열람(inline), 그 외는 다운로드.
인증 미들웨어가 보호하므로 미로그인 시 /login 으로 리다이렉트된다.
"""
import os
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from nicegui import app as ng_app

from app.auth import current_user_id
from app.database import get_session
from app.models import MailStage

# 산출물이 저장되는 루트(work/). 임의 경로 서빙을 막기 위한 화이트리스트 기준.
from app.work_paths import WORK as _WORK_BASE, anchor as _anchor

_INLINE_MEDIA = {".pdf": "application/pdf", ".html": "text/html", ".htm": "text/html"}


@ng_app.get("/artifact/view")
def serve_artifact_view(path: str):
    """산출물 파일을 **브라우저 새 탭에서 inline 열람**(다운로드 아님).

    로그인 필수 + work/ 하위 경로만 허용(임의 경로 접근 차단)."""
    if not current_user_id():
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    # 옛 기록은 그때의 폴더 접두를 달고 있다(저장소를 옮겼거나 다른 폴더에서 돌린 것).
    # 그런 경로를 **work/ 안으로 당겨** 열어 준다 — 밖으로 내보내는 일은 없으므로
    # 아래 가드는 그대로 유효하다.
    try:
        p = Path(_anchor(path)).resolve()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="잘못된 경로입니다.")
    if p != _WORK_BASE and _WORK_BASE not in p.parents:
        raise HTTPException(status_code=403, detail="허용되지 않은 경로입니다.")
    if not p.is_file():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
    media = _INLINE_MEDIA.get(p.suffix.lower(), "application/octet-stream")
    # 파일명에 한글이 있어도 헤더 인코딩 오류가 없도록 filename 은 생략하고 inline 만 지정.
    return FileResponse(str(p), media_type=media, headers={"Content-Disposition": "inline"})


@ng_app.get("/rn/preview/{task_key}")
def serve_rn_preview(task_key: str, doc: str = "sales"):
    """작업 중인 RN 문서를 **PDF 로 변환해 그대로 반환**(검수 화면 iframe 용).

    화면에서 나중에 채우는 방식(ui.timer)은 목록이 갱신되면 부모 슬롯이 먼저 사라져
    콜백이 깨진다. 그래서 iframe 이 이 주소를 직접 불러가게 하고, 변환은 여기서 한다.
    (sync 핸들러라 FastAPI 가 스레드풀에서 돌린다 — 이벤트 루프를 막지 않는다.)

    task_key 는 파이프라인 상태 키다 — 메일 작업이면 메일 id, /osupdate 의 제품 작업이면
    'job-N'. 그래서 int 로 못 박지 않는다.

    doc=full 이면 **전체 이력 RN** 을 띄운다(전체 이력 RN 제작 단계·RN 승인 화면).
    """
    mail_id = task_key
    if not current_user_id():
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    from app.pipeline.rn_research import preview_pdf

    path, err = preview_pdf(mail_id, "full" if doc == "full" else "sales")
    if err or not path:
        # iframe 안에서 읽히도록 오류도 화면으로 돌려준다(브라우저 기본 오류 페이지 대신).
        return HTMLResponse(
            "<body style=\"margin:0;padding:16px;font-family:sans-serif;color:#8a5a12;"
            "background:#f6e8cf\">" + (err or "미리보기를 만들지 못했습니다.") + "</body>",
            status_code=200,
        )
    return FileResponse(path, media_type="application/pdf",
                        headers={"Content-Disposition": "inline"})


@ng_app.get("/artifact/{stage_id}")
def serve_artifact(stage_id: int):
    user_id = current_user_id()
    with get_session() as db:
        stage = (
            db.query(MailStage).filter_by(id=stage_id, user_id=user_id).first()
        )
        if not stage or not stage.artifact_path:
            raise HTTPException(status_code=404, detail="산출물이 없습니다.")
        path = stage.artifact_path
        atype = stage.artifact_type

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="산출물 파일을 찾을 수 없습니다.")

    filename = os.path.basename(path)
    if atype == "pdf":
        return FileResponse(
            path,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )
    return FileResponse(path, filename=filename)
