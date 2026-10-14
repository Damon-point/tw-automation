"""WhiteList 대량 요청 — 요약 + 일괄 처리 팝업.

SN 이 많으면(>30) 하나씩 체크/조회하지 않고 **요약**만 보여주고 유형별로 **일괄 처리**한다.
  · 신규 등록   → 엑셀 업로드 (요약에서 지정한 단일 모델/OS/구분을 전 SN 에 적용)
  · 등록일 변경 → FOTA DB 직접   (osType·api 는 그대로 · 등록일과 support_end_date 만)
처리는 청크로 나눠 진행률을 보여주고, 끝나면 성공/실패 요약을 남긴다.
"""
from __future__ import annotations

from datetime import date


BULK_THRESHOLD = 30          # SN 이 이보다 많으면 요약 모드
_CHUNK = 200                 # 한 번에 서버로 보낼 SN 수


def _dedup(sns) -> list[str]:
    return list(dict.fromkeys(s.strip().upper() for s in sns if s and s.strip()))


def _plus_year(d: date) -> str:
    try:
        return d.replace(year=d.year + 1).isoformat()
    except ValueError:
        return d.replace(year=d.year + 1, day=28).isoformat()


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ── 무엇이 올라가는지 보여주는 표 ─────────────────────────────
# 화면 입력값이 아니라 **서버에 실제로 들어갈 값**을 그대로 만든다.
# 처리 화면이 여러 개(메일 팝업·AI 해석 팝업·일괄)라 여기 한 곳에서 만든다.

UPLOAD_COLS = [
    {"name": "sn", "label": "SN", "field": "sn", "align": "left"},
    {"name": "api", "label": "Android API", "field": "api", "align": "left"},
    {"name": "end", "label": "Support end date", "field": "end", "align": "left"},
    {"name": "pn", "label": "Partnumber", "field": "pn", "align": "left"},
    {"name": "ostype", "label": "ostype", "field": "ostype", "align": "left"},
    {"name": "status", "label": "상태", "field": "status", "align": "left"},
]

DATE_COLS = [
    {"name": "sn", "label": "sn", "field": "sn", "align": "left"},
    {"name": "update_date_time", "label": "update_date_time (등록일)",
     "field": "update_date_time", "align": "left"},
    {"name": "support_end_date", "label": "support_end_date (등록일+1년)",
     "field": "support_end_date", "align": "left"},
]


def upload_rows(devices: list[dict], registered: dict | None = None) -> list[dict]:
    """업로드 엑셀에 들어갈 행. registered={sn: True} 면 '기존 등록(갱신)' 을 덧붙인다."""
    reg = registered or {}
    out = []
    for d in devices:
        known = " · 기존 등록(갱신)" if reg.get(d.get("sn", "")) else ""
        out.append({
            "sn": d.get("sn", ""), "api": str(d.get("api") or ""),
            "end": d.get("endDate") or "", "pn": d.get("partnumber") or "",
            "ostype": d.get("ostype") or "",
            "status": ("✅ 업로드 대상" + known) if d.get("valid", True)
                      else "⚠ " + " / ".join(d.get("errors") or []),
        })
    return out


def date_rows(sns: list[str], new_date: str) -> list[dict]:
    """등록일 변경으로 DB 에 들어갈 행(update_date_time · support_end_date)."""
    import re

    from app.whitelist.fota_db import REG_TIME

    nd = (new_date or "").strip()
    ok = bool(re.match(r"^\d{4}-\d{2}-\d{2}$", nd))
    bad = "(날짜 형식 오류)"
    end = f"{int(nd[:4]) + 1}{nd[4:]}" if ok else bad
    return [{"sn": s, "update_date_time": f"{nd} {REG_TIME}" if ok else bad,
             "support_end_date": end} for s in sns]
