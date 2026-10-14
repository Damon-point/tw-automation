# -*- coding: utf-8 -*-
"""RN for Sales 를 Dropbox 에 올리고 **내려받기 링크**를 얻는다.

이 링크가 곧 **Release Note URL** 이다 — Newsletter 의 'Release Note' 단추,
FOTA OsInfo 의 Release_Note_URL 칸, Service Portal 의 RN 링크가 모두 이것을 쓴다.

예전에는 이 업로드가 ③ Dropbox 업로드 단계 안에 묻혀 있었다. 그런데 Newsletter 제작은
Submission 갈래에서 도는데 그 갈래에는 ③ 단계가 아예 없다. 그래서 링크가 생길 길이
없었고, Newsletter 는 '링크가 없습니다' 로 계속 멈췄다(사용자 확인 2026-09-22).

그래서 업로드를 **단계에서 떼어내 이 함수로** 옮겼다. 필요한 단계가 필요할 때 부른다.

    · Newsletter 제작   — 링크가 곧 본문에 들어가므로 여기서 만든다
    · FOTA 업로드       — OsInfo 의 Release_Note_URL 칸에 넣는다
    · Service Portal    — 문서의 RN 링크를 이 주소로 바꾼다

**두 번 올리지 않는다.** 이번 회차에 이미 올렸고 파일 이름도 그대로면 그 링크를 준다.
그래서 어느 단계가 먼저 돌든 결과가 같고, 다시 실행해도 사본이 쌓이지 않는다.
"""
from __future__ import annotations

import os

from app.integrations import dropbox_client as D
from app.integrations.creds import get_dropbox_auth
from app.pipeline.state import get_state, update_state


def rn_folder_for(product: str, android: str = "") -> str:
    """이 제품의 Release Note 폴더. 제품 관리에 등록한 경로가 있으면 그것을 쓴다.

    Release Notes 는 NEW_FOTA 안이 아니라 **제품 폴더 바로 아래**에 있다.
    """
    from app import os_products as _OP
    from app.pipeline.dropbox_release import _dest, _FOTA_TPL

    reg = _OP.paths_for(product, android) or {}
    return reg.get("dbx_rn") or f"{_dest(_FOTA_TPL, product)}/Release Notes"


def _pdf_from_done_docx(mail_id, st: dict) -> tuple[str, str]:
    """RN for Sales 단계가 **완료** 이고 docx 가 있으면 그 docx 를 PDF 로 굳힌다. (pdf, 알림글)."""
    docx = str(st.get("rn_docx_path") or "").strip()
    if not docx or not os.path.exists(docx):
        return "", "RN for Sales 문서가 없습니다 — 'RN for Sales 제작' 을 먼저 끝내세요."
    from app.task_store import stage_status

    if stage_status(mail_id, "release_note") != "done":
        return "", ("RN for Sales PDF 가 없습니다 — 'RN for Sales 제작' 의 최종 검수가 끝나야 "
                    "PDF 가 생깁니다.")
    from app.pipeline.rn_research import finalize_rn_pdf

    pdf, err = finalize_rn_pdf(mail_id)
    if not pdf:
        return "", f"RN for Sales docx 를 PDF 로 바꾸지 못했습니다 — {err}"
    return pdf, ""


def ensure_rn_link(user_id: int, mail_id: int | None,
                   progress=None) -> tuple[str, str, str]:
    """RN for Sales 를 올리고 (링크, 파일 이름, 알림글) 을 돌려준다.

    이미 올렸으면 올리지 않고 그 링크를 준다. 실패해도 예외를 올리지 않는다 —
    링크가 빈 문자열이면 실패고, 까닭은 세 번째 값에 담긴다.
    """
    if mail_id is None:
        return "", "", "회차 정보가 없어 Release Note 를 올릴 수 없습니다."

    st = get_state(mail_id) or {}
    pdf = str(st.get("rn_pdf_path") or "").strip()
    name = str(st.get("rn_pdf_name") or "").strip() or (os.path.basename(pdf) if pdf else "")
    link = str(st.get("rn_link") or "").strip()

    # 이미 이번 회차 것을 올렸는가. 이름까지 같아야 한다 — RN 을 다시 만들어 파일
    # 이름이 바뀌었으면 옛 링크는 다른 문서를 가리킨다.
    if link and str(st.get("rn_name") or "").strip() == name:
        return link, name, f"이미 올린 Release Note 를 씁니다 — {name}"

    if not pdf or not os.path.exists(pdf):
        # PDF 는 2차 최종 검수가 docx 를 굳힐 때 생긴다. 그런데 'RN for Sales 제작' 이 검수
        # 없이 완료로 닫힌 작업이 있다 — 그 docx 가 사람이 받아들인 최종본이다.
        # 그럴 때는 지금 docx 를 PDF 로 굳혀 올린다(실측 job-33 PM352 352.09, 2026-10-13:
        # docx 만 있어 Service Portal 의 Release Note 링크가 바뀌지 않았다).
        pdf, note = _pdf_from_done_docx(mail_id, st)
        if not pdf:
            return "", "", note
        st = get_state(mail_id) or {}
        name = str(st.get("rn_pdf_name") or "").strip() or os.path.basename(pdf)

    token = get_dropbox_auth(user_id)
    if not token:
        return "", "", "Dropbox 자격증명이 없습니다. [설정 → Dropbox] 에서 등록하세요."

    folder = rn_folder_for(st.get("product") or "(미상)", st.get("android", ""))
    if progress is not None:
        try:
            progress.step("Release Note PDF 업로드 중…")
        except Exception:  # noqa: BLE001
            pass

    try:
        with open(pdf, "rb") as f:
            data = f.read()
        res = D.upload_bytes(token, name, data, dest_folder=folder)
    except Exception as e:  # noqa: BLE001
        return "", "", f"Release Note 업로드 오류: {e}"

    if not res.ok:
        return "", "", f"Release Note 업로드 실패: {res.error}"

    link = res.link
    # ⑧ Dropbox 정리가 **방금 올린 이 파일을 구버전으로 보고 지우지 않도록** 이번 회차
    # 목록에 넣어 둔다. ③ 단계가 만들어 둔 목록이 있으면 그 위에 얹는다.
    fresh = dict(st.get("dropbox_new") or {})
    fresh["rn"] = [name]
    update_state(mail_id, rn_link=link, rn_name=name, dropbox_new=fresh)
    return link, name, f"Release Note 업로드({folder}/{name})"
