# -*- coding: utf-8 -*-
"""이 회차의 **공식 표기**를 한 군데서 정한다.

회차 버전이 상태에 세 군데로 들어 있다.

    release_version   회차 표기 — 보통 'MF52EG15.00 C1' 처럼 C 리비전을 품는다
    version_full      빌드명 — '86.07C8 (Aug 24 2026)'. 날짜가 붙어 있다
    os_version        예전 메일 작업이 쓰던 자리. 회차를 다시 잡아도 바뀌지 않는다

셋이 어긋날 수 있다. 회차를 뒤늦게 다른 C 판으로 다시 잡으면 version_full 만 바뀌고
나머지는 base 로 남는다(실측 2026-10-07 job-24: release_version·os_version 은 '86.07',
version_full 은 '86.07C8 (Aug 24 2026)').

그런데 고르는 **순서가 자리마다 달랐다**. 그래서 같은 회차를 어떤 단계는 86.07 로,
어떤 단계는 86.07C8 로 보았다.

    portal_edit      os_version → version_full → release_version   (가장 낡은 것이 이긴다)
    newsletter       release_version → os_version
    final/mid_review version_full → os_version
    rn_research      자리마다 섞여 있다

그래서 Service Portal 문서에 86.07 이 들어갔고, FOTA 에는 기존 osv 107 과 같은 표기가
올라가 등록이 거부됐다. 고르는 규칙을 하나로 모은다.

**규칙: 같은 회차 안에서 C 리비전이 가장 높은 표기를 쓴다.** base 가 다른 값은 서로
다른 회차이므로 섞지 않는다(86.07 과 86.08, MF52 와 MF52E 는 다른 것이다).

주의 — **폴더를 찾는 자리에는 쓰지 않는다.** 파일서버·Dropbox 경로는 base 버전으로
된 폴더를 쓰는 곳이 있어, 거기서는 release_version 그대로가 맞다.
"""
from __future__ import annotations

#: 보는 자리 — 값이 있으면 모두 후보로 삼는다.
KEYS = ("version_full", "release_version", "os_version")


def _split(v: str) -> tuple[str, str]:
    """'86.07C8 (Aug 24 2026)' → ('86.07C8', 'Aug 24 2026')."""
    text, _, rest = str(v or "").partition("(")
    return text.strip(), rest.rstrip(")").strip()


def _crev(v: str) -> int:
    from app.pipeline.rn_research import _c_revision

    c = _c_revision(v)
    try:
        return int(c[1:] or 0) if c else 0
    except ValueError:
        return 0


def round_version(st: dict) -> tuple[str, str, str]:
    """(회차 표기, 날짜, 알림글). 알림글은 고른 값이 release_version 과 다를 때만.

    날짜는 **고른 표기에 붙어 있던 것**을 쓴다. 빌드명에 붙은 날짜는 그 판의 것이므로
    임의로 다른 판의 날짜를 섞지 않는다. 고른 표기에 날짜가 없으면 release_date.
    """
    from app.pipeline.rn_research import _base_ver

    st = st or {}
    cands: list[tuple[str, str, str]] = []          # (표기, 날짜, 출처)
    for k in KEYS:
        text, date = _split(st.get(k) or "")
        if text:
            cands.append((text, date, k))
    if not cands:
        return "", str(st.get("release_date") or "").strip(), ""

    # 기준 회차 — release_version 이 있으면 그것, 없으면 첫 후보.
    base_src = next((t for t, _d, k in cands if k == "release_version"), cands[0][0])
    base = _base_ver(base_src)

    same = [c for c in cands if _base_ver(c[0]) == base]
    if not same:
        same = [cands[0]]
    best = max(same, key=lambda c: _crev(c[0]))

    date = best[1] or str(st.get("release_date") or "").strip()
    note = ""
    rv = next((t for t, _d, k in cands if k == "release_version"), "")
    if rv and best[0].replace(" ", "").upper() != rv.replace(" ", "").upper():
        note = f"회차 표기를 {best[2]} 에서 가져왔습니다: {rv} → {best[0]}"
    return best[0], date, note
