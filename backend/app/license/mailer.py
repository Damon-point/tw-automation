"""License 승인/반려 메일의 **본문 문구**를 만든다.

발송 자체는 하지 않는다 — 앱의 모든 메일은 user_mailer 한 곳으로 나간다(발신 신원도
거기서 정한다). 예전에는 이 파일이 별도 SMTP 계정(.env 의 LICENSE_SMTP_*)으로 직접
보냈는데, 부르는 곳이 없어진 지 오래고 설정도 없어서 걷어냈다(2026-08-25).
"""
from __future__ import annotations

from app.license.models import LicenseRequest


def build_approval(req: LicenseRequest, has_pdf: bool = True) -> dict:
    """승인 메일 내용. {to, subject, body} — License Key 는 첨부 PDF 에만 있고 본문엔 넣지 않는다."""
    from app import mail_templates as MT

    subject, body = MT.render(
        "license_approval", 문서번호=req.doc_id, 회사=req.company_name,
        수량=req.quantity, 승인자=req.confirm_by, 메모=req.admin_note or "-",
        첨부안내=("라이선스 인증서(License Key 포함)가 PDF 로 첨부되어 있습니다."
                if has_pdf else "라이선스 문서는 별도 안내드립니다."))
    return {"to": req.recipient_email or req.contact_email,
            "subject": subject, "body": body}


def build_rejection(req: LicenseRequest) -> dict:
    """반려 메일 내용(검수용). {to, subject, body}."""
    from app import mail_templates as MT

    subject, body = MT.render(
        "license_rejection", 문서번호=req.doc_id, 회사=req.company_name,
        반려자=req.confirm_by, 사유=req.admin_note or "-")
    return {"to": req.recipient_email or req.contact_email,
            "subject": subject, "body": body}
