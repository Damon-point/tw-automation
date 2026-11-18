"""OS Update 결재 요청 메일의 HTML 판 — PMDM License 승인 요청 메일(license.mail_html.request_html)과 같은 틀.

파란 머리 · 요청 띠 · 숫자 한 줄 · 상세 표 · 검수 화면 버튼(2026-11-18 사용자 요청). 평문(mail_templates 의
review_request)은 그대로 함께 실린다 — HTML 을 못 그리는 메일 프로그램에서는 그것이 보인다.
"""
from __future__ import annotations

from datetime import datetime

from app.license.mail_html import (BRAND_DARK, FONT, INK3, SOFT, _banner_info, _box, _button, _e, _rows, _shell,
                                   _strip, gap)


def review_request_html(*, kind_label: str, verb: str, josa: str, product: str, version: str, requester: str,
                        role: str, reviewers: list[str], docs: list[str], url: str, url_label: str) -> str:
    """결재자에게 — '{요청자} 님이 {제품} {버전} {대상} {승인/검수}을 요청했습니다'."""
    title = f"{kind_label} {verb} 요청"
    strip = _strip([("Product", _e(product) or "-"), ("Version", _e(version) or "-"), ("Type", _e(kind_label))])
    rows = [
        ("요청자", _e(requester)),
        ("요청일", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("결재자", f"{_e(role)}" + (f' <span style="color:{INK3}">· {_e(", ".join(reviewers))}</span>' if reviewers else "")),
    ]
    inner = (
        f'<p style="{FONT};margin:0 0 6px">안녕하세요.</p>'
        f'<p style="{FONT};margin:0">{_e(requester)} 님이 {_e(product)} {_e(version)} {_e(kind_label)} '
        f'{_e(verb)}{_e(josa)} 요청했습니다.</p>{gap(18)}'
        + _banner_info(title, f"{product} · {version}")
        + strip + _rows(rows)
        + (_box("검수할 문서", "\n".join(docs), BRAND_DARK, SOFT) if docs else "")
        + _button(url_label, url)
    )
    return _shell(f"OS Update {title}", inner, source="OS Update")
