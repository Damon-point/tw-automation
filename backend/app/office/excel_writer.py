"""엑셀(.xlsx) 생성: 헤더 + 행 데이터를 워크북으로 만든다."""
from __future__ import annotations

import io
from typing import Sequence

from openpyxl import Workbook


def build_workbook_bytes(
    headers: Sequence[str],
    rows: Sequence[Sequence],
    sheet_name: str = "Sheet1",
) -> bytes:
    """헤더/행으로 워크북을 만들어 xlsx 바이트로 반환 (다운로드용)."""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(list(headers))
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
