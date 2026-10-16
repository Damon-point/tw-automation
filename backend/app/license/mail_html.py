"""License 승인 · 반려 메일의 HTML 판 — 실제 서비스가 보내는 안내 메일처럼.

평문(mail_templates 의 license_approval · license_rejection)은 그대로 함께 실린다 — HTML 을 못 그리는
메일 프로그램에서는 그것이 보인다. 개인 서명은 붙이지 않는다(시스템이 알리는 결과라서, 2026-10-16).

메일 프로그램은 CSS 를 제대로 그리지 않는 것이 많아 겉틀은 표로 짜고 칸마다 style 을 직접 적는다.
아웃룩은 표 칸에 글꼴을 물려주지 않고 margin 도 무시한다 — 칸마다 글꼴, 간격은 빈 줄로(vmi-web 과 같은 방식).
색은 TW Automation(검수 웹 · 3080)의 파랑.
"""
from __future__ import annotations

from datetime import datetime
from html import escape

from app.license.models import LicenseRequest

BRAND = "#2f6bff"
BRAND_DARK = "#1f4fd6"
INK = "#14213d"
INK2 = "#4a5873"
INK3 = "#8592a8"
LINE = "#e3e8f2"
SOFT = "#f4f7fd"
FAMILY = "font-family:'Malgun Gothic','Apple SD Gothic Neo','Segoe UI',Arial,sans-serif"
FONT = f"{FAMILY};font-size:13.5px;color:{INK}"


def _e(v) -> str:
    return escape(str(v or ""), quote=True)


def gap(px: int) -> str:
    return (f'<div style="line-height:{px}px;mso-line-height-rule:exactly;font-size:{px}px;height:{px}px">'
            "&nbsp;</div>")


def _qty(q) -> str:
    try:
        return f"{int(float(str(q).strip())):,}"
    except (TypeError, ValueError):
        return _e(q)


def _strip(cells: list[tuple[str, str]]) -> str:
    """눈에 먼저 걸려야 하는 값 한 줄 — Doc ID · 회사 · 수량."""
    td = "".join(
        f'<td valign="top" style="{FAMILY};padding:12px 16px;border:1px solid {LINE};background:{SOFT}">'
        f'<div style="{FAMILY};color:{INK3};font-size:11px;line-height:14px;mso-line-height-rule:exactly;'
        f'letter-spacing:.06em;text-transform:uppercase">{_e(k)}</div>'
        f'<div style="{FAMILY};color:{INK};font-size:17px;line-height:23px;mso-line-height-rule:exactly;'
        f'font-weight:700">{v}</div></td>'
        for k, v in cells)
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
            f'style="border-collapse:collapse;width:100%"><tr>{td}</tr></table>{gap(18)}')


def _rows(rows: list[tuple[str, str]]) -> str:
    """상세 — 왼쪽 항목 · 오른쪽 값."""
    tr = "".join(
        f'<tr><td valign="top" width="150" style="{FAMILY};font-size:12.5px;color:{INK3};padding:9px 14px;'
        f'border-bottom:1px solid {LINE};white-space:nowrap">{_e(k)}</td>'
        f'<td valign="top" style="{FAMILY};font-size:13.5px;color:{INK};padding:9px 14px;'
        f'border-bottom:1px solid {LINE}">{v}</td></tr>'
        for k, v in rows if v)
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
            f'style="border-collapse:collapse;width:100%;border-top:1px solid {LINE}">{tr}</table>{gap(18)}')


def _banner(ok: bool, title: str, sub: str) -> str:
    """결과 띠 — 승인은 초록, 반려는 빨강."""
    color, bg, mark = ("#13804b", "#e9f7ef", "&#10003;") if ok else ("#c62828", "#fdecec", "&#10005;")
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
            f'style="border-collapse:collapse;width:100%"><tr>'
            f'<td style="{FAMILY};background:{bg};border-left:4px solid {color};padding:14px 18px">'
            f'<div style="{FAMILY};color:{color};font-size:16px;line-height:22px;mso-line-height-rule:exactly;'
            f'font-weight:700">{mark}&nbsp; {_e(title)}</div>'
            f'<div style="{FAMILY};color:{INK2};font-size:12.5px;line-height:18px;mso-line-height-rule:exactly">'
            f'{_e(sub)}</div></td></tr></table>{gap(18)}')


def _box(label: str, text: str, color: str, bg: str) -> str:
    """메모 · 반려 사유 — 받는 사람이 꼭 읽어야 하는 글."""
    body = _e(text).replace("\n", "<br>")
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
            f'style="border-collapse:collapse;width:100%"><tr>'
            f'<td style="{FAMILY};background:{bg};border:1px solid {LINE};padding:14px 16px">'
            f'<div style="{FAMILY};color:{color};font-size:12px;line-height:16px;mso-line-height-rule:exactly;'
            f'font-weight:700">{_e(label)}</div>{gap(6)}'
            f'<div style="{FAMILY};color:{INK};font-size:13.5px;line-height:21px;mso-line-height-rule:exactly">'
            f'{body}</div></td></tr></table>{gap(18)}')


def _shell(title: str, inner: str) -> str:
    """겉틀 — 파란 머리(로고) · 본문 · 꼬리. 640px 표."""
    year = datetime.now().year
    return f"""<div style="margin:0;padding:28px 12px;background:#eef2f9;{FONT}">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" width="640" style="width:100%;max-width:640px;margin:0 auto;border-collapse:collapse;background:#ffffff;border:1px solid {LINE}">
  <tr>
    <td bgcolor="{BRAND}" style="{FAMILY};padding:20px 28px;background:{BRAND};border-bottom:3px solid {BRAND_DARK}">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse"><tr>
        <td width="36" height="36" align="center" valign="middle" bgcolor="#ffffff" style="{FAMILY};width:36px;height:36px;background:#ffffff;border-radius:9px;color:{BRAND};font-size:14px;font-weight:800;line-height:36px">tw</td>
        <td style="{FAMILY};padding-left:12px">
          <div style="{FAMILY};color:#ffffff;font-size:16px;line-height:20px;mso-line-height-rule:exactly;font-weight:700">TW Automation</div>
          <div style="{FAMILY};color:#d6e2ff;font-size:12px;line-height:16px;mso-line-height-rule:exactly">{_e(title)}</div>
        </td>
      </tr></table>
    </td>
  </tr>
  <tr><td style="padding:26px 28px 8px;{FONT};line-height:21px;mso-line-height-rule:exactly">{inner}</td></tr>
  <tr>
    <td style="{FAMILY};padding:14px 28px 22px;border-top:1px solid {LINE};color:{INK3};font-size:11.5px;line-height:1.7">
      TW Automation · PMDM License 에서 자동으로 보낸 메일입니다.<br>&copy; {year} Point Mobile Co., Ltd.
    </td>
  </tr>
</table>
</div>"""


def _common(req: LicenseRequest) -> tuple[str, list[tuple[str, str]]]:
    strip = _strip([("Doc ID", _e(req.doc_id)), ("Company", _e(req.company_name)),
                    ("Quantity", f"{_qty(req.quantity)} <span style=\"font-size:12px;color:{INK3}\">EA</span>")])
    contact = _e(req.contact_name) + (f' &lt;<a href="mailto:{_e(req.contact_email)}" style="color:{BRAND};'
                                      f'text-decoration:none">{_e(req.contact_email)}</a>&gt;' if req.contact_email else "")
    rows = [
        ("PMDM ID", f'<span style="font-family:Consolas,\'Courier New\',monospace;font-size:13px">{_e(req.license_user_id)}</span>'),
        ("Contact", contact),
        ("Product", f"{_e(req.part_number or 'PMDMCE')} · PMDM Cloud Enterprise"),
        ("신청일", _e(req.date_issued)),
        ("신청자", _e(req.created_by_name)),
    ]
    return strip, rows


def approval_html(req: LicenseRequest, has_pdf: bool) -> str:
    strip, rows = _common(req)
    rows += [("승인", f"{_e(req.confirm_by)} · {_e(req.confirm_date)}")]
    inner = (
        f'<p style="{FONT};margin:0 0 6px">안녕하세요.</p>'
        f'<p style="{FONT};margin:0">요청하신 PMDM License 발급이 승인되었습니다.</p>{gap(18)}'
        + _banner(True, "License 발급 승인", f"{req.company_name} · {_qty(req.quantity)} EA")
        + strip + _rows(rows)
        + (_box("메모", req.admin_note, BRAND_DARK, SOFT) if (req.admin_note or "").strip() else "")
        + (_box("첨부", f"{req.doc_id}_License_Certificate.pdf — License Key 가 들어 있는 인증서입니다.",
                "#13804b", "#f3fbf6") if has_pdf else _box("첨부", "라이선스 문서는 별도로 안내드립니다.", INK2, SOFT))
    )
    return _shell("PMDM License 발급 결과", inner)


def rejection_html(req: LicenseRequest) -> str:
    strip, rows = _common(req)
    rows += [("반려", f"{_e(req.confirm_by)} · {_e(req.confirm_date)}")]
    inner = (
        f'<p style="{FONT};margin:0 0 6px">안녕하세요.</p>'
        f'<p style="{FONT};margin:0">요청하신 PMDM License 발급이 반려되었습니다.</p>{gap(18)}'
        + _banner(False, "License 발급 반려", f"{req.company_name} · {_qty(req.quantity)} EA")
        + _box("반려 사유", req.admin_note or "-", "#c62828", "#fff6f6")
        + strip + _rows(rows)
    )
    return _shell("PMDM License 발급 결과", inner)
