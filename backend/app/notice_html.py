"""정기 알림(11시 · 16시) 메일의 HTML 판 — 결재 요청 메일(license.mail_html.request_html)과 같은 틀.

파란 머리 · 요청 띠 · 숫자 한 줄 · 목록 표 · 화면으로 가는 버튼. 평문(mail_templates 의 notice_whitelist ·
notice_osupdate)은 그대로 함께 실린다 — HTML 을 못 그리는 메일 프로그램에서는 그것이 보인다(2026-10-28).
"""
from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime

from app.license.mail_html import FAMILY, FONT, INK, INK2, INK3, LINE, _banner_info, _button, _e, _shell, _strip, gap


def _when(raw: str) -> str:
    try:
        dt = parsedate_to_datetime(raw or "")
        if dt.tzinfo:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt.strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        return _e((raw or "")[:16])


def _sender(raw: str) -> str:
    """'Rosie Yeom <rosie.yeom@…>' → 'Rosie Yeom'(이름이 없으면 주소)."""
    from email.utils import parseaddr

    name, addr = parseaddr(raw or "")
    return (name or addr or raw or "").strip().strip("'\"")


def _table(head: list[str], rows: list[list[str]], widths: list[str]) -> str:
    """목록 — 머리 한 줄 + 줄마다 구분선. 칸 값은 이미 이스케이프된 HTML."""
    th = "".join(
        f'<td width="{w}" style="{FAMILY};font-size:11.5px;color:{INK3};padding:8px 10px;background:#f4f7fd;'
        f'border-bottom:1px solid {LINE};letter-spacing:.04em;white-space:nowrap">{_e(h)}</td>'
        for h, w in zip(head, widths))
    tr = "".join(
        "<tr>" + "".join(
            f'<td valign="top" style="{FAMILY};font-size:13px;line-height:19px;color:{INK};padding:9px 10px;'
            f'border-bottom:1px solid {LINE}">{v}</td>' for v in r) + "</tr>"
        for r in rows)
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
            f'style="border-collapse:collapse;width:100%;border:1px solid {LINE}"><tr>{th}</tr>{tr}</table>{gap(18)}')


def _sub(text: str) -> str:
    return f'<div style="{FAMILY};color:{INK3};font-size:12px;line-height:17px;padding-top:2px">{text}</div>'


def _now_label() -> str:
    n = datetime.now()
    return f"{n:%Y-%m-%d} {n.hour}시 기준"


def whitelist_notice_html(groups: list[dict], url: str, limit: int = 20) -> str:
    """처리를 기다리는 WhiteList 요청 스레드 목록."""
    n = len(groups)
    sns = sum(len(g.get("serials") or []) for g in groups)
    rows = []
    for i, g in enumerate(groups[:limit], 1):
        rep = g.get("rep") or {}
        s = list(g.get("serials") or [])
        sn = (_e(", ".join(s[:3])) + (f' <span style="color:{INK3}">외 {len(s) - 3}</span>' if len(s) > 3 else "")) if s else "-"
        rows.append([str(i),
                     _e((rep.get("subject") or "(제목 없음)")[:80]) + _sub(_e(_sender(rep.get("sender", "")))),
                     sn, _when(rep.get("received_at", ""))])
    more = (f'<p style="{FONT};margin:0;color:{INK2}">외 {n - limit}건</p>{gap(14)}' if n > limit else "")
    inner = (
        f'<p style="{FONT};margin:0 0 6px">안녕하세요.</p>'
        f'<p style="{FONT};margin:0">처리를 기다리는 FOTA WhiteList 등록 요청이 {n}건 있습니다.</p>{gap(18)}'
        + _banner_info(f"WhiteList 등록 요청 {n}건 대기", _now_label())
        + _strip([("대기 요청", f"{n}<span style=\"font-size:12px;color:{INK3}\"> 건</span>"),
                  ("S/N", f"{sns:,}<span style=\"font-size:12px;color:{INK3}\"> 대</span>")])
        + _table(["#", "요청", "S/N", "받은 때"], rows, ["24", "", "170", "80"])
        + more
        + _button("WhiteList 화면에서 처리하기", url)
    )
    return _shell("WhiteList 처리 대기 알림", inner, source="WhiteList · 매일 11시 · 16시")


def osupdate_notice_html(found: list[tuple[dict, dict]], url: str) -> str:
    """아직 시작하지 않은 OS Update 회차 목록. found = [(제품, 회차)]."""
    n = len(found)
    rows = []
    for i, (r, u) in enumerate(found, 1):
        sku = " ".join(x for x in (r.get("product", ""), f"A{r['android']}" if r.get("android") else "",
                                   r.get("os_type") or "") if x)
        src = ("배포 공지만 있음" if u.get("source") == "deploy" else
               "Google 승인 완료" if u.get("approved") else "승인 대기")
        color = "#13804b" if src == "Google 승인 완료" else ("#b26a00" if src == "승인 대기" else INK2)
        rows.append([str(i),
                     f"<b>{_e(sku)}</b>" + (_sub(_e(u["basis"][:90])) if u.get("basis") else ""),
                     _e(u.get("version") or "(버전 미정)"),
                     f'<span style="color:{color};font-weight:700">{_e(src)}</span>'])
    inner = (
        f'<p style="{FONT};margin:0 0 6px">안녕하세요.</p>'
        f'<p style="{FONT};margin:0">아직 시작하지 않은 OS Update 회차가 {n}건 있습니다.</p>{gap(18)}'
        + _banner_info(f"시작하지 않은 OS Update 회차 {n}건", _now_label())
        + _table(["#", "제품", "버전", "상태"], rows, ["24", "", "150", "120"])
        + _button("OS Update 화면에서 시작하기", url)
    )
    return _shell("OS Update 회차 알림", inner, source="OS Update · 매일 11시 · 16시")
