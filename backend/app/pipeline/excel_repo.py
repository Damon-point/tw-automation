"""④ FOTA 등록용 Excel 저장소 — **신규분만** 만든다.

  · `work/excel_repo/OsInfo.xlsx` / `DownloadPath.xlsx`
        이번 실행에서 추가된 행만(FOTA 등록용, 매 실행 덮어쓰기).

누적(01_OsInfo·03_DownloadPath) 파일은 **더 이상 다루지 않는다**(사용자 확정 2026-08-05).
누적 이력은 FOTA 시스템 DB 조회가 대신한다 — 열람은 [FOTA-DB 열람](/excelrepo)이
`/view/osinfo/list`·`/view/downloadpath/list` 에서 직접 읽는다. 파일로도 들고 있으면
둘이 어긋났을 때 무엇이 참인지 알 수 없다.

신규로 추가된 항목 자체는 서비스 DB(FotaEntry 테이블)에도 기록한다.
실제 배포 스키마(9열/8열)를 그대로 따른다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from openpyxl import Workbook

from app.atomic_io import atomic_save_workbook

# 신규만(repo) 고정 경로 -------------------------------------------------------
REPO_DIR = Path(__file__).resolve().parent.parent.parent / "work" / "excel_repo"

NEW_FILES = {"OsInfo": "OsInfo.xlsx", "DownloadPath": "DownloadPath.xlsx"}

# 실제 배포 스키마
HEADERS = {
    "OsInfo": [
        "OS_Type", "Version", "OS_Version", "Description", "Release_Note",
        "Release_Note_URL", "Update_Code", "OSReset_Code", "ScannerReset_Code",
    ],
    "DownloadPath": [
        "OS_Type", "Azure_Info", "OS_Version", "Download_URL",
        "Download_URL_MD5", "DOWNLOADTYPE", "파일이름", "size",
    ],
}
# 공통 열 인덱스
_SHEET = "Sheet1"


def _write_new(path: Path, headers: Sequence, rows: Sequence[Sequence]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = _SHEET
    ws.append(list(headers))
    for r in rows:
        ws.append(list(r))
    atomic_save_workbook(wb, path)


def save_new(kind: str, rows: Sequence[Sequence]) -> Path:
    """신규만 파일(repo)을 헤더+이번 행으로 덮어쓰기 저장."""
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    path = REPO_DIR / NEW_FILES[kind]
    _write_new(path, HEADERS[kind], rows)
    return path


    # (파일 목록 조회 API 는 누적 파일 열람용이었다 — 열람이 FOTA DB 조회로 바뀌면서
    #  쓰는 곳이 없어져 함께 걷어냈다.)
