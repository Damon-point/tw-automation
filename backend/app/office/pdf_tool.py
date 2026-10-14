"""PDF 열기(텍스트 추출) + PDF 내보내기(텍스트 → PDF).

- 추출: PyMuPDF(fitz) 로 페이지 텍스트를 읽는다.
- 생성: fpdf2 로 한글 폰트(맑은 고딕)를 임베드해 PDF 를 만든다.
※ 이번 단계는 '텍스트' 수준의 열기/수정/내보내기다. 원본 레이아웃·이미지·표는
  보존되지 않는다(추후 확장 여지).
"""
from __future__ import annotations

import os

import fitz  # PyMuPDF
from fpdf import FPDF

# 한글 지원 폰트 후보 (Windows 기본)
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\malgun.ttf",
    r"C:\Windows\Fonts\gulim.ttc",
    r"C:\Windows\Fonts\batang.ttc",
]


def _korean_font() -> str | None:
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def page_count(data: bytes) -> int:
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        return doc.page_count
    finally:
        doc.close()


def build_pdf(text: str, title: str = "") -> bytes:
    """텍스트를 A4 PDF 로 생성해 바이트로 반환 (한글 폰트 임베드)."""
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    font_path = _korean_font()
    if font_path:
        pdf.add_font("Body", "", font_path)
        pdf.set_font("Body", size=11)
    else:
        # 한글 폰트를 못 찾으면 기본 폰트(한글 깨질 수 있음)
        pdf.set_font("Helvetica", size=11)

    if title:
        pdf.set_font_size(14)
        pdf.multi_cell(0, 9, title, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        pdf.set_font_size(11)

    # 빈 문자열이면 최소 한 줄
    body = text if text.strip() else "(내용 없음)"
    pdf.multi_cell(0, 7, body, new_x="LMARGIN", new_y="NEXT")

    out = pdf.output()  # fpdf2 2.8: bytearray 반환
    return bytes(out)
