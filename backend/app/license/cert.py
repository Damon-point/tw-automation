"""라이선스 인증서 PDF 생성.

흐름(원본 Apps Script 와 동일 개념):
  .docx 템플릿({{company}} 등 태그) → docxtpl 로 채움 → PDF 변환.

- 템플릿 채움: docxtpl (기존 office/rn_render 와 동일 방식, 지연 import)
- PDF 변환: 기존 app.office.rn_render.docx_to_pdf_bytes 재사용 (Word COM). 신규 의존성 없음.
- 템플릿 경로: env LICENSE_CERT_TEMPLATE, 없으면 work/licenses/license_cert_template.docx
- 모든 실패는 (None, error) 로 반환 → 승인 흐름이 죽지 않고 관리자에게 사유 표시.
"""
from __future__ import annotations

import io
import os
from datetime import datetime
from pathlib import Path

from app.atomic_io import atomic_write_bytes
from app.license import service
from app.license.models import LicenseRequest


def _template_path() -> Path:
    env = os.getenv("LICENSE_CERT_TEMPLATE", "").strip()
    if env:
        return Path(env)
    return service.STORAGE_DIR / "license_cert_template.docx"


def _fmt_date(s: str) -> str:
    """YYYY-MM-DD → 'October 20, 2025'(템플릿 스타일). 파싱 실패 시 원문."""
    try:
        return datetime.strptime((s or "").strip(), "%Y-%m-%d").strftime("%B %d, %Y")
    except (ValueError, TypeError):
        return s or ""


def _fmt_qty(q) -> str:
    """수량 → '1,000 EA'(천단위 콤마)."""
    s = str(q or "").strip()
    try:
        return f"{int(float(s)):,} EA"
    except (ValueError, TypeError):
        return f"{s} EA" if s else ""


def build_context(req: LicenseRequest) -> dict:
    """템플릿 placeholder 컨텍스트. Word 템플릿엔 {{ company }} 등으로 넣는다.

    Part Number / Description 은 PMDMCE 고정이라 템플릿에 리터럴로 둔다(여기 값은 예비).
    """
    return {
        "dateIssued": _fmt_date(req.date_issued),
        "description": "PMDM Cloud Enterprise",
        "quantity": _fmt_qty(req.quantity),
        "licenseKey": req.license_key or "(License Key Pending)",
        "company": req.company_name,
        "contactName": req.contact_name,
        "email": req.contact_email,
        "region": req.region,
        "docId": req.doc_id,
    }


def render_certificate(req: LicenseRequest) -> tuple[bytes | None, str]:
    """인증서 PDF 바이트 반환. (pdf_bytes, error)."""
    tpl = _template_path()
    if not tpl.exists():
        return None, (
            f"인증서 템플릿이 없습니다: {tpl}. "
            "LICENSE_CERT_TEMPLATE 로 경로를 지정하거나 해당 위치에 .docx 템플릿을 두세요."
        )
    try:
        from docxtpl import DocxTemplate  # 지연 import
    except Exception as e:  # noqa: BLE001
        return None, f"docxtpl 미설치: {e} (pip install docxtpl)"

    try:
        doc = DocxTemplate(str(tpl))
        doc.render(build_context(req))
        buf = io.BytesIO()
        doc.save(buf)
        docx_bytes = buf.getvalue()
    except Exception as e:  # noqa: BLE001
        return None, f"인증서 템플릿 렌더 실패: {e}"

    # PDF 변환은 기존 헬퍼 재사용(Word COM). 신규 의존성 없음.
    try:
        from app.office.rn_render import docx_to_pdf_bytes
    except Exception as e:  # noqa: BLE001
        return None, f"PDF 변환 모듈 로드 실패: {e}"

    pdf_bytes, err = docx_to_pdf_bytes(docx_bytes)
    if pdf_bytes is None:
        return None, err
    return pdf_bytes, ""


def render_and_save(req: LicenseRequest) -> tuple[str | None, bytes | None, str]:
    """인증서 생성 후 로컬 저장까지. (saved_path, pdf_bytes, error)."""
    pdf_bytes, err = render_certificate(req)
    if pdf_bytes is None:
        return None, None, err
    dest = service.cert_dest(req.doc_id)
    try:
        atomic_write_bytes(dest, pdf_bytes)
    except Exception as e:  # noqa: BLE001
        return None, pdf_bytes, f"인증서 저장 실패: {e}"
    service.save_cert_path(req.doc_id, str(dest))
    return str(dest), pdf_bytes, ""
