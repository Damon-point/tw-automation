"""이번 작업의 '정식 OS 버전' 통합 판별 — ①·②·⑤·⑥ 공용.

정식 Release 는 **[FIH][Approval] 기준**. 버전은 빌드지문(R###)이 아니라
**출시 이력(Service Portal / OS·FW Release Notes) + FIH 승인 날짜**로 판별한다.

규칙(사용자 확정):
  · SP/RN 에서 현재까지 출시된 버전 이력을 확인(각 버전엔 날짜가 있다).
  · **이미 출시된(=날짜 ≤ FIH 승인일) 버전 중 최고**를 기준(base)으로 잡는다.
    → 같은 달에 OS 가 2회 이상 나가도 날짜로 시퀀싱해 정확히 구분한다.
  · FIH 승인일 > base 출시일 → **새 업데이트** → 새 버전 = base +1 증분(95.06→95.07).
  · FIH 승인일 ≤ base 출시일 → 그 버전이 이번 승인 대상(증분 안 함).
  · 출시 이력이 아무 것도 없으면 빈 값 + 사유(지어내지 않는다).

[GMS MR] 배포 메일·빌드지문은 버전 소스로 쓰지 않는다(GMS MR 은 이슈 참고용).
Android 는 반드시 함께 넘겨야 SKU(A13/A15)를 정확히 구분한다.
"""
from __future__ import annotations

import re
from datetime import datetime

from app.integrations import confluence_reader as RN
from app.integrations import service_portal as SP

_DATE_FMTS = ("%b %d %Y", "%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y", "%d %b %Y")


# 버전을 (마지막 숫자 앞부분, 마지막 숫자, 리비전 접미) 로 가른다.
#   '95.06'          → ('95.',       '06', '')      형식 1
#   '95.07C1'        → ('95.',       '07', 'C1')
#   '68.3.1'         → ('68.3.',     '1',  '')      SP/KB 가 주는 세 자리 표기
#   'TR54G15.03C1'   → ('TR54G15.',  '03', 'C1')    형식 2(제품명+Android 가 major)
# dev_rn 의 문법은 파일명에서 버전을 '찾는' 용도라 세 자리 표기를 못 받는다. 출시 이력은
# 외부(SP·Confluence)에서 오는 문자열이라 여기서는 더 너그럽게 가른다.
# major 형태와 접미 구분자('' · . · _ · 공백)는 dev_rn 과 같은 폭으로 받는다
# ('2x.05' · 'MF52EG15.00_B14' 같은 실측 표기가 빠지지 않게).
_SPLIT_RE = re.compile(
    r"^(?P<head>(?:\d+(?:\.\d+)*|\d[xX]|[A-Za-z][A-Za-z0-9]*)\.)?"
    r"(?P<last>\d+)(?:[._ ]?(?P<suffix>[A-Z]{1,2}\d*)(?![a-z]))?$"
)
_SFX_RE = re.compile(r"^([A-Za-z]{1,2})(\d*)$")


def _split_version(v: str) -> tuple[str, str, str] | None:
    """버전 → (head, last, suffix). 못 가르면 None. last 는 자릿수 보존을 위해 문자열.

    접미는 **대문자**만 본다 — dev_rn 과 같은 기준이다('40.00_r2' 의 소문자 r2 는 문서
    리비전이지 빌드 접미가 아니다). 꼬리가 붙어 통째로 안 읽히면 dev_rn 문법으로
    버전 부분만 집어내 다시 가른다('s_84.06C1_WWE_0226' → 84.06 + C1).
    """
    s = re.sub(r"\s+", "", v or "")
    m = _SPLIT_RE.match(s)
    if m is None:
        from app.pipeline.dev_rn import parse_version

        p = parse_version(s)
        m = _SPLIT_RE.match(p.text) if p else None
    if m is None:
        return None
    return m.group("head") or "", m.group("last"), (m.group("suffix") or "").upper()


def _ver_key(v: str) -> tuple:
    """정렬·비교 키 — **리비전 접미(C1 등)까지 반영**한다.

    같은 자리에서 접미 없는 판이 가장 낮다: 95.07 < 95.07C1 < 95.07C2 < 95.08.
    자리마다 타입을 고정해(계열, major숫자, major문자, 뒷자리들, 접미문자, 접미숫자)
    형식 1 과 형식 2 가 섞여 들어와도 비교가 터지지 않는다.
    예전에는 int 변환만 해서 '95.07C1' 이 통째로 (0,) 이 됐다 — 출시 이력에 C 판이 섞이면
    그게 최저로 밀려 base 판별이 틀어졌다.
    """
    parts = _split_version(v)
    if parts is None:
        return (0, 0, "", (), "", 0)         # 못 읽은 표기는 언제나 최저
    head, last, suffix = parts
    ms = _SFX_RE.match(suffix)
    sl, sn = (ms.group(1), int(ms.group(2) or 0)) if ms else ("", 0)
    nums = head.rstrip(".")
    segs = [s for s in nums.split(".") if s]
    # 앞부분이 전부 숫자일 때만 숫자 계열이다. '2x'·'TR54G15' 은 문자 계열로 둔다
    # ('2x' 를 숫자로 읽으려다 int() 에서 터지던 자리다).
    if segs and not all(s.isdigit() for s in segs):
        return (2, 0, nums.upper(), (int(last),), sl, sn)
    seq = tuple(int(s) for s in segs) + (int(last),)
    return (1, seq[0], "", seq[1:], sl, sn)


def _increment_version(v: str) -> str:
    """마지막 숫자 세그먼트 +1, 자릿수(zero-pad) 보존.

    95.06→95.07 · 95.51→95.52 · 68.3→68.4 · TR54G15.03→TR54G15.04
    **리비전 접미는 뗀다** — 95.07C1 의 다음 판은 95.08 이지 95.08C1 이 아니다.
    """
    parts = _split_version(v)
    if parts is None:
        return v
    head, last, _sfx = parts
    return head + str(int(last) + 1).zfill(len(last))


def _parse_date(s: str) -> datetime | None:
    """SP/RN 의 'Jan 27 2026' 및 메일의 RFC 형식('Thu, 2 Jul 2026 …')을 폭넓게 파싱."""
    s = (s or "").strip()
    if not s:
        return None
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:  # RFC 2822 메일 날짜
        from email.utils import parsedate_to_datetime

        d = parsedate_to_datetime(s)
        if d:
            return d.replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        pass
    for fmt in ("%d %b %Y", "%b %d %Y"):  # 앞 3토큰만
        try:
            return datetime.strptime(" ".join(s.split()[:3]), fmt)
        except (ValueError, IndexError):
            continue
    return None


def collect_history(user_id: int, product: str, android: str) -> dict:
    """**출시된** 버전 이력을 모은다 — {versions: [{version, date, src}], notes: [...]}.

    Service Portal(현재/직전) + Confluence OS·FW Release Notes(전체 이력)를 합치고,
    같은 버전이 양쪽에 있으면 날짜가 있는 쪽을 남긴다. 버전 내림차순.

    여기 실린 버전은 **이미 고객에게 나간 것**이다 — 다음 버전 판별의 기준이자,
    '이미 끝난 회차' 를 가려내는 근거이기도 하다(/osupdate 소급 완료).
    """
    notes: list[str] = []
    cands: list[dict] = []

    # 1) Service Portal Resources — 현재/직전 출시 버전(신뢰)
    sp = SP.resolve_version_from_sp(user_id, product, android)
    if sp.get("ok"):
        src = f"Service Portal ({sp.get('doc_id')} {sp.get('doc_title')})"
        if sp.get("current"):
            cands.append({"version": sp["current"], "date": sp.get("current_date", ""), "src": src})
        if sp.get("prev"):
            cands.append({"version": sp["prev"], "date": sp.get("prev_date", ""), "src": src + "(직전)"})
    else:
        notes.append(f"Service Portal 조회 실패: {sp.get('error')}")

    # 2) Confluence OS/FW Release Notes — 버전 이력 전체(다중 출시 시퀀싱용)
    try:
        rn = RN.read_release_note(user_id, product, android_version=android, headful=False)
        if rn.found:
            for v in (rn.os_versions or []):
                if v.get("version"):
                    cands.append({"version": v["version"], "date": v.get("date", ""),
                                  "src": "OS/FW Release Notes"})
    except Exception as e:  # noqa: BLE001
        notes.append(f"OS/FW Release Notes 조회 실패: {e}")

    if not cands:
        notes.append("출시 이력(Service Portal / OS·FW Release Notes)을 찾지 못했습니다.")
        return {"versions": [], "notes": notes}

    by_ver: dict[str, dict] = {}
    for c in cands:
        ex = by_ver.get(c["version"])
        if ex is None or (not ex.get("date") and c.get("date")):
            by_ver[c["version"]] = c
    uniq = sorted(by_ver.values(), key=lambda c: _ver_key(c["version"]), reverse=True)
    return {"versions": uniq, "notes": notes}


def resolve_release_version(
    user_id: int, product: str, android: str, trigger_date: str = "",
    history: dict | None = None,
) -> dict:
    """정식 OS 버전(이번에 배포할 새 버전)을 결정한다.

    trigger_date: [FIH][Approval] 승인 메일 날짜(RFC 형식 허용). 있으면 출시 이력과 비교해
      증분 여부를 판단하고, 없으면 최신 출시본 +1 로 가정한다.

    반환: {version, date, base_version, base_date, source, is_beta_seen, note}.
      version 이 "" 이면 어느 소스에서도 못 찾은 것(정직).
    """
    # history 를 주면 그걸 쓴다 — /osupdate 는 받아 둔 캐시를 넘겨 **외부 조회 없이**
    # 이 판별을 그대로 돌린다(같은 규칙을 두 벌 만들지 않기 위해서다).
    hist = history if history is not None else collect_history(user_id, product, android)
    notes: list[str] = list(hist.get("notes") or [])
    uniq = hist.get("versions") or []

    if not uniq:
        return {"version": "", "date": "", "base_version": "", "base_date": "",
                "source": "", "is_beta_seen": False, "note": "; ".join(notes)}

    td = _parse_date(trigger_date)

    # 이미 출시된(날짜 ≤ FIH) 것만 base 후보. 날짜 미상은 보수적으로 포함.
    if td:
        published = [c for c in uniq
                     if (_parse_date(c["date"]) is None) or (_parse_date(c["date"]) <= td)]
        if not published:
            published = uniq
            notes.append("주의: 모든 출시 이력이 FIH 승인일 이후 — 날짜 확인 필요")
    else:
        published = uniq
        notes.append("FIH 승인 날짜 미제공 — 최신 출시본 +1 로 가정")

    base = max(published, key=lambda c: _ver_key(c["version"]))
    base_v, base_d = base["version"], base.get("date", "")
    bd = _parse_date(base_d)

    if td and bd and td <= bd:
        new_v = base_v  # FIH 가 최신 출시본 이내 → 새 버전 아님
        notes.append(f"FIH 승인일이 최신 출시본 {base_v}({base_d}) 이내 → 기존 버전으로 간주")
    else:
        new_v = _increment_version(base_v)
        notes.append(f"최신 출시본 {base_v}({base_d or '날짜?'}) → 새 업데이트 → {new_v}")

    return {
        "version": new_v, "date": "",            # 새 버전은 아직 미출시 → 출시일 미정
        "base_version": base_v, "base_date": base_d,
        "source": base.get("src", ""),
        "is_beta_seen": False,
        "note": "; ".join(notes),
    }
