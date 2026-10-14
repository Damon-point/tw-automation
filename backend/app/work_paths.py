# -*- coding: utf-8 -*-
"""저장된 산출물 경로를 **지금의 work/ 안으로** 되돌린다.

앱은 산출물 경로를 절대 경로 문자열로 적어 둔다(회차 상태 JSON · 단계 행의
artifact_path · 결과 글). 편한 대신 두 가지가 따라온다.

  · 저장소를 옮기거나 다른 폴더에서 한 번 돌리면 그때의 접두가 그대로 박힌다
  · 열람 라우트는 보안상 work/ 하위만 허용하므로, 그런 경로는 403 으로 막힌다
    — 화면에는 {"detail":"허용되지 않은 경로입니다."} 만 보인다

더 고약한 쪽은 **쓰기** 다. 저장된 경로를 그대로 믿고 쓰면 없어진 폴더를 되살려
거기에 파일을 만든다. 그러면 앱은 자기가 쓴 것을 자기가 못 읽는다.

    실측 2026-10-07 — 시험용 클론(tw-automation-cli)에서 돌린 Newsletter 세 건의
    경로가 클론 접두를 달고 있었다. 클론을 지운 뒤 뉴스레터를 편집하자 그 경로로
    다시 써서 폴더가 되살아났고, 열람은 403 이었다.

그래서 **읽을 때도 쓸 때도 이 함수를 지나게 한다.** 옮기는 방향은 한쪽뿐이다 —
work/ **안으로만** 당긴다. 밖으로 내보내는 일이 없으니 열람 가드가 느슨해지지 않는다.
"""
from __future__ import annotations

from pathlib import Path, PurePath

#: 지금 저장소의 work/. 산출물은 모두 이 아래에 있다.
WORK = (Path(__file__).resolve().parent.parent / "work").resolve()


def inside(path: str | Path) -> bool:
    """이 경로가 지금의 work/ 안인가."""
    try:
        p = Path(path).resolve()
    except OSError:
        return False
    return p == WORK or WORK in p.parents


def anchor(path: str | Path) -> str:
    """work/ 안의 경로로 되돌린다. 되돌릴 수 없으면 받은 것을 그대로 돌려준다.

    규칙은 하나다 — 경로에 work 칸이 있으면 **그 뒤쪽을 지금의 work/ 에 붙인다**.
    폴더 구조가 같아야 하므로, 이름만 같은 엉뚱한 파일을 끌어오지 않는다.
    work 칸이 없으면 손대지 않는다 — 파일서버 보관 경로(`\\\\192.168...`)처럼
    **일부러 work/ 밖에 두는** 것이 있다.
    """
    raw = str(path or "")
    if not raw or inside(raw):
        return raw
    parts = PurePath(raw).parts
    low = [s.lower() for s in parts]
    if "work" not in low:
        return raw
    i = len(low) - 1 - low[::-1].index("work")        # 마지막 work 칸
    tail = parts[i + 1:]
    return str(WORK.joinpath(*tail)) if tail else raw
