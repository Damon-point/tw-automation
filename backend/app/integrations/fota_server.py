"""192.168.123.104 파일서버의 OS 파일(PFU/md5) 경로 해석 — 퍼지 매칭.

실제 경로 구조(사용자 확인):
  /Project/[제품]/Software/[Android 버전]/Formal Release/[상세버전]/sdcard/  → *.PFU, *.md5

경로상의 모든 영문 대소문자·띄어쓰기는 유동적이다. 다음을 유동 처리한다:
  · 대소문자 무시, 공백 무시
  · 제품의 'P'(Plus)는 '+' / 'Plus' 로도 표기될 수 있음 → 상호 인식
  · Android 폴더는 'Android 15' 형태이나, 제품 기본(A13)이면 그냥 'Android' 일 수도 있음
  · 버전 폴더는 상세 버전 문자열을 '포함'(예: XXX95.07XXX)

`list_dir(path) -> list[str]` 를 주입받아 서버 접근 방식(SFTP 등)과 분리한다(테스트 용이).
"""
from __future__ import annotations

import re
from typing import Callable

_DATE_RE = re.compile(r"(20\d{2})[.\-_]?(\d{2})[.\-_]?(\d{2})")


def parse_os_build(name: str) -> dict:
    """OS 파일/폴더명에서 버전·빌드등급·날짜를 파싱. (날짜_버전세부 형식)

    반환 {version, tier, date, is_release}.
      version    접미를 뗀 버전 표기 그대로 — '86.07' · 'TR54G15.04'
      tier       빌드 등급 접미 — '' · 'C8' · 'B14' · 'RC1' · 'S1'
      is_release **정식 배포로 내보내는 판인가** — 접미가 없거나 **C 리비전**

    C 리비전을 정식으로 보는 것이 이 앱 전체의 규칙이다. C 판은 같은 회차를 대체하는
    정식 배포이고, 개발 빌드는 A/B/S/RC 뿐이다(app/pipeline/dev_rn.Version.tracked ·
    rn_research.is_release_version · os_update_jobs 가 모두 그 규칙을 쓴다).
    여기만 "접미가 있으면 비배포" 였던 탓에 86.07C8 처럼 공식 배포된 C 판이
    '배포용 아님' 으로 표시됐다(실측 2026-10-07 job-24).

    버전 문법도 **dev_rn 한 벌로 통일**한다. 예전 정규식은 제품명이 붙는 형식 2 를
    읽지 못해 'TR54G15.04' 를 '15.04' 로 잘라 버렸고, 그 탓에 버전 교차검증이
    '버전 불일치' 로 틀리게 경고했다.

      '86.07C8_20260824.PFU'      → 86.07 · C8  · 2026-08-24 · 정식
      'TR54G15.04_20260901.PFU'   → TR54G15.04  · 2026-09-01 · 정식
      '95.07B14_20260701.PFU'     → 95.07 · B14 · 2026-07-01 · 비배포
    """
    from app.pipeline.dev_rn import parse_version

    s = name or ""
    dm = _DATE_RE.search(s)
    date = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}" if dm else ""
    if dm:  # 날짜를 먼저 떼어 버전으로 오인하지 않게
        s = s.replace(dm.group(0), " ")

    pv = parse_version(s)
    if pv is None:
        # 버전을 못 읽었으면 등급도 알 수 없다. 비배포라고 단정할 근거가 없으므로
        # 경고를 띄우지 않는다 — 버전 교차검증이 따로 불일치를 알린다.
        return {"version": "", "tier": "", "date": date, "is_release": True}

    tier = pv.suffix or ""
    version = pv.text[:-len(tier)].rstrip("._ ") if tier else pv.text
    return {"version": version, "tier": tier, "date": date,
            "is_release": pv.tracked}


def _norm(s: str) -> str:
    """소문자 + 공백 제거 + plus/'+' → 'p' 통일."""
    s = (s or "").lower().replace(" ", "")
    return s.replace("plus", "p").replace("+", "p")


def resolve_sdcard(
    list_dir: Callable[[str], list[str]],
    product: str,
    android: str,
    version: str,
    root: str = "/Project",
) -> dict:
    """PFU/md5 이 있는 sdcard 폴더 경로와 파일 목록을 찾는다(퍼지).

    반환: {ok, path, files:{pfu[], md5[], all[]}, error, trail[]}.
    """
    trail: list[str] = []

    def step(base: str, want: str, label: str, contains: bool = False):
        try:
            children = list_dir(base)
        except Exception as e:  # noqa: BLE001
            return None, f"'{base}' 목록 조회 실패: {e}"
        w = _norm(want)
        hit = None
        for name in children:
            n = _norm(name)
            if (w in n) if contains else (n == w):
                hit = name
                break
        if hit is None:
            return None, f"{label} 폴더를 찾지 못함 (in {base}); 후보: {children[:10]}"
        p = base.rstrip("/") + "/" + hit
        trail.append(p)
        return p, ""

    cur, err = step(root, product, f"제품 '{product}'")
    if err:
        return {"ok": False, "path": "", "files": {}, "error": err, "trail": trail}
    cur, err = step(cur, "Software", "Software")
    if err:
        return {"ok": False, "path": "", "files": {}, "error": err, "trail": trail}

    # Android {n} → 없으면 'Android'(기본 A13 케이스)
    a = str(android or "").strip()
    nxt, err = step(cur, f"Android{a}", f"Android{a}")
    if err:
        nxt, err2 = step(cur, "Android", "Android(기본)")
        if err2:
            return {"ok": False, "path": "", "files": {},
                    "error": f"{err} / {err2}", "trail": trail}
    cur = nxt

    cur, err = step(cur, "FormalRelease", "Formal Release")
    if err:
        return {"ok": False, "path": "", "files": {}, "error": err, "trail": trail}

    # 버전 폴더 = **정식(approved) 빌드만**. 폴더명에 'approved'(예: _MR_approved/_IR_approved/
    # -LR_approved)가 있어야 정식 배포로 인정한다. Beta({버전}B\d)·'rejected' 폴더는 제외.
    try:
        vchildren = list_dir(cur)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "path": "", "files": {},
                "error": f"'{cur}' 목록 조회 실패: {e}", "trail": trail}
    vnorm = _norm(version)
    # 정식 배포 폴더 선택 규칙:
    #  · Beta({버전}B\d)·'rejected' 폴더는 제외.
    #  · 남은 정식 후보 중 'approved' 표기가 있으면 우선. (단, approved 가 폴더명에 없을 수도 있음)
    #  · approved 가 없으면 정식 후보 중 최신(폴더명 접두 날짜 YYYY.MM.DD).
    _beta_re = re.compile(re.escape(version) + r"\s*B\d", re.I)
    cands = [
        n for n in vchildren
        if vnorm in _norm(n) and "reject" not in n.lower() and not _beta_re.search(n)
    ]
    if not cands:
        others = [n for n in vchildren if vnorm in _norm(n)]
        return {"ok": False, "path": "", "files": {}, "trail": trail,
                "error": (f"버전 '{version}' 의 정식 빌드 폴더를 찾지 못했습니다 "
                          f"(Beta·rejected 제외 후 없음). 후보: {others[:10] or vchildren[:10]}")}
    _approved = [n for n in cands if "approved" in n.lower()]
    pick = sorted(_approved or cands, reverse=True)   # approved 우선, 없으면 정식 후보 최신
    cur = cur.rstrip("/") + "/" + pick[0]
    trail.append(cur)

    cur, err = step(cur, "sdcard", "sdcard")
    if err:
        return {"ok": False, "path": "", "files": {}, "error": err, "trail": trail}

    try:
        files = list_dir(cur)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "path": cur, "files": {}, "error": f"sdcard 목록 실패: {e}", "trail": trail}
    pfu = [f for f in files if f.lower().endswith(".pfu")]
    md5 = [f for f in files if f.lower().endswith(".md5")]
    return {"ok": True, "path": cur, "files": {"pfu": pfu, "md5": md5, "all": files},
            "error": "", "trail": trail}
