"""OS Update 결재 요청 메일의 HTML 판 — PMDM License 승인 요청 메일(license.mail_html.request_html)과 같은 틀.

파란 머리 · 요청 띠 · 숫자 한 줄 · 상세 표 · 검수 화면 버튼(2026-11-18 사용자 요청). 평문(mail_templates 의
review_request)은 그대로 함께 실린다 — HTML 을 못 그리는 메일 프로그램에서는 그것이 보인다.
"""
from __future__ import annotations

from datetime import datetime

from app.license.mail_html import (BRAND_DARK, FONT, INK2, INK3, SOFT, _banner, _banner_info, _box, _button, _e,
                                   _rows, _shell, _strip, gap)


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


def review_result_html(*, kind_label: str, verb: str, approved: bool, product: str, version: str, decider: str,
                       note: str, next_line: str, url: str) -> str:
    """요청자에게 — 결재 결과. PMDM License 승인 · 반려 메일(license.mail_html)과 같은 틀(2026-11-25).

    승인은 초록 띠, 반려는 빨간 띠와 '반려 사유' 상자. 버튼은 앱의 그 작업 화면.
    """
    verdict = "승인" if approved else "반려"
    title = f"{kind_label} {verdict}" if verb == "승인" else f"{kind_label} {verb} {verdict}"
    strip = _strip([("Product", _e(product) or "-"), ("Version", _e(version) or "-"), ("Result", verdict)])
    rows = [("결재자", _e(decider)), ("결정일", datetime.now().strftime("%Y-%m-%d %H:%M"))]
    inner = (
        f'<p style="{FONT};margin:0 0 6px">안녕하세요.</p>'
        f'<p style="{FONT};margin:0">요청하신 {_e(product)} {_e(version)} {_e(kind_label)}'
        f'{" 가" if verb == "승인" else " " + _e(verb) + "가"} {verdict}되었습니다.</p>{gap(18)}'
        + _banner(approved, title, f"{product} · {version}")
        + (_box("반려 사유", note or "-", "#c62828", "#fff6f6") if not approved else "")
        + strip + _rows(rows)
        + (_box("메모", note, BRAND_DARK, SOFT) if approved and note and note != "-" else "")
        + (f'<p style="{FONT};margin:0;color:{INK2}">{_e(next_line)}</p>{gap(18)}' if next_line else "")
        + _button("앱에서 작업 보기", url)
    )
    return _shell(f"OS Update {kind_label} 결재 결과", inner, source="OS Update")
