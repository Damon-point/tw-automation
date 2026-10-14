"""업무일 계산 — 주말과 한국 공휴일을 뺀다.

Lead time 을 달력일로 재면 설·추석이 낀 건과 안 낀 건을 나란히 놓을 수 없다. 그래서
한국 기준 업무일로 잰다(사용자 확정 2026-08-11).

공휴일은 ``holidays`` 패키지의 KR 자료를 쓴다. 직접 표를 박아 넣지 않는 이유는 설·추석·
부처님오신날이 음력이라 해마다 옮겨 다니고, 대체공휴일과 임시공휴일(2024 국군의날,
2025-01-27 등)이 그때그때 생기기 때문이다. 실측으로 2025년 19일·2026년 22일을 맞게
집어낸다.

**하루를 [00:00, 24:00) 한 칸으로 보고 시작일과 종료일은 걸친 만큼만 센다.** 업무시간을
따로 알 수 없어 근무 시각을 가정하지 않는다. 그래서 화요일 09시 → 수요일 09시는 1.0
업무일, 금요일 18시 → 월요일 06시는 0.25 + 0.25 = 0.5 업무일이 된다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

#: 공휴일 표를 만들어 둘 범위. 이슈가 2016년부터 있고 앞으로도 쓰므로 넉넉히 잡는다.
FIRST_YEAR = 2015
LAST_YEAR = 2035

_cum: dict[date, int] = {}      # 날짜 → 그 날까지(포함) 업무일 누적
_holidays: frozenset[date] = frozenset()
_first: date | None = None
_last: date | None = None


def _load() -> None:
    """공휴일 표와 누적 업무일을 한 번만 만든다."""
    global _cum, _holidays, _first, _last
    if _cum:
        return

    try:
        import holidays as _h

        hol = set(_h.country_holidays("KR", years=range(FIRST_YEAR, LAST_YEAR + 1)))
    except Exception:  # noqa: BLE001
        # 패키지가 없으면 주말만 뺀다. 숫자가 조금 커지지만 화면이 죽지는 않는다.
        hol = set()
    _holidays = frozenset(hol)

    _first = date(FIRST_YEAR, 1, 1)
    _last = date(LAST_YEAR, 12, 31)
    n = 0
    d = _first
    step = timedelta(days=1)
    while d <= _last:
        if d.weekday() < 5 and d not in _holidays:
            n += 1
        _cum[d] = n
        d += step


def is_business_day(d: date) -> bool:
    _load()
    return d.weekday() < 5 and d not in _holidays


def holidays_in(a: date, b: date) -> list[date]:
    """[a, b] 안의 공휴일(주말 제외). 화면에서 '무슨 날이 빠졌나' 보여줄 때 쓴다."""
    _load()
    return sorted(d for d in _holidays if a <= d <= b and d.weekday() < 5)


def count_days(a: date, b: date) -> int:
    """(a, b] 구간의 업무일 수. a 는 빼고 b 는 넣는다."""
    _load()
    if b <= a:
        return 0
    lo = max(a, _first)          # 표 밖은 셀 수 없다
    hi = min(b, _last)
    if hi <= lo:
        return 0
    return _cum[hi] - _cum[lo]


def _frac(t: datetime) -> float:
    """그 날 자정부터 흐른 비율."""
    return (t.hour * 3600 + t.minute * 60 + t.second) / 86400


def business_days(a: datetime | None, b: datetime | None) -> float | None:
    """a → b 사이의 업무일. 뒤집힌 구간은 None 이 아니라 음수로 두지 않고 None 을 준다.

    None 을 주는 경우는 값이 없거나 순서가 뒤집힌 때뿐이다 — 집계 쪽에서 그대로 빼면
    된다. 주말·공휴일에만 걸친 구간은 0.0 이다(하루도 일하지 않았다는 뜻).
    """
    if a is None or b is None or b < a:
        return None
    _load()
    da, db = a.date(), b.date()

    if da == db:
        if not is_business_day(da):
            return 0.0
        return (b - a).total_seconds() / 86400

    total = 0.0
    if is_business_day(da):
        total += 1.0 - _frac(a)
    if is_business_day(db):
        total += _frac(b)
    # 사이에 낀 온전한 날들 — (da, db) 열린 구간
    total += count_days(da, db - timedelta(days=1))
    return total


def calendar_days(a: datetime | None, b: datetime | None) -> float | None:
    """달력일. 업무일과 나란히 보여 주려고 남겨 둔다."""
    if a is None or b is None or b < a:
        return None
    return (b - a).total_seconds() / 86400
