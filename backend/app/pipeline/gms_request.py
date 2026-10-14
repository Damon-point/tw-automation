# -*- coding: utf-8 -*-
"""`Request to start the GMS * approval test` 메일 파서 — **회차의 공식 버전**을 얻는다.

우리 GMS 담당이 FIH 로 보내는 메일이다. 본문이 정형이라 규칙으로 읽힌다.

    Please start the GMS MR approval test for MF52e.
    *   Build Version: MF52EG15.00C1
    *   Fingerprint:
    *  User: POINTMOBILE/MF52e/MF52e:15/TP1A.220624.014/R25:user/release-keys

**왜 이 메일인가**(사용자 확정 2026-09-07). 회차를 알려 주는 근거가 둘 있었는데 둘 다
구멍이 있었다.

  · [FIH] 트리거 — 본문에 버전이 **아예 없다**(실측 38건 전부). SKU 와 빌드지문뿐이라
    '어느 버전 건인지' 를 알 수 없다.
  · 배포 공지 제목 — 공지가 안 나가면 아무것도 모른다. 실측 MF52e: 8/12 RC 공지 뒤로
    공지가 없는데 8/28 에 MF52EG15.00C1 로 승인 요청이 나갔고 9/4 에 승인까지 났다.

이 메일은 **우리가 보내는 것**이라 누락이 없고, 정식 버전으로 Google 승인을 요청하는
시점이라 배포 공지보다 먼저 온다. 실측 원 요청 14건이 전부 정식 버전이었다(베타 없음).

빌드지문이 [FIH] 트리거와 이 메일을 잇는 다리다 — 지문이 같으면 같은 회차다.
실측: 트리거 32건 중 30건(93%)이 이 방식으로 자기 버전을 찾았다.
"""
from __future__ import annotations

import re

#: 제목 — 'Request to start the GMS MR approval test for MF52e.'
#: 회신(RE:/回覆:)도 같은 본문을 인용해 오므로 함께 받는다. 어차피 지문으로 합쳐진다.
_SUBJ_RE = re.compile(r"request\s+to\s+start\s+the\s+GMS\s+([A-Z]{2})\s+approval\s+test",
                      re.I)

#: 'Build Version: 86.07C8'. **사람이 손으로 쓰는 줄이라 오타가 난다** — 실측
#: 메일 #2765 는 'Build Verson:' 이었고, 그 한 글자 때문에 86.07C8 회차가 통째로
#: 이름을 잃었다(감지는 됐는데 배포 공지로 폴백해 '86.07' 로 붙었다 —
#: 사용자 지적 2026-09-21). 'Ver…on' 으로 느슨하게 받는다. 뒤의 지문이 같은지까지
#: 봐야 한 회차로 묶이므로, 조금 느슨해도 남의 회차를 물어 오지 않는다.
_BUILD_VER_RE = re.compile(r"Build\s*Ver\w*on\s*[:：]\s*([A-Za-z0-9._\-]+)", re.I)

#: 빌드지문 — fih_mail 과 **같은 문법**을 쓴다(두 벌로 갈라지면 반드시 어긋난다).
from app.pipeline.fih_mail import _FP_RE  # noqa: E402

# 버전 문법은 **dev_rn 한 벌만** 쓴다. 여기서 정규식을 새로 만들면 같은 문자열을
# 두 곳이 다르게 읽는 순간 조용히 어긋난다(rn_research._parsed_ver 도 그쪽을 쓴다).


def is_request_subject(subject: str) -> str:
    """이 메일이 승인 요청인가 — 맞으면 시험 종류(MR·IR·LR), 아니면 ''."""
    m = _SUBJ_RE.search(subject or "")
    return m.group(1).upper() if m else ""


def valid_version(v: str) -> str:
    """받아들일 수 있는 버전 표기면 정규화해 돌려주고, 아니면 ''.

    형식이 낯설면 **버리는 쪽**을 고른다. 잘못 받아들이면 작업 버전이 그대로 굳어
    문서 이름·파일서버 경로까지 그 표기로 나간다(배포 공지 쪽으로 폴백하면 그만이다).

    거르는 것 둘.
      · 파서가 **일부만** 읽어 낸 것 — '15.00_20260812'(MF52 8/17 요청)는 '15.00' 으로
        읽히는데 날짜 꼬리표가 남는다. 읽어 낸 글자가 원문과 같을 때만 받는다.
      · 정식 배포가 아닌 판 — 접미가 없거나 C 리비전만 받는다(A/B/S/RC 는 개발 빌드).
        os_update_jobs._deploy_rounds 의 _is_release 와 같은 기준이다.
    """
    from app.pipeline.dev_rn import parse_version

    s = re.sub(r"\s+", "", str(v or ""))
    if not s:
        return ""
    pv = parse_version(s)
    if pv is None or pv.text != s:
        return ""
    sfx = (pv.suffix or "").upper()
    if sfx and not sfx.startswith("C"):
        return ""
    return s


def parse(subject: str, body: str) -> dict | None:
    """요청 메일 → {kind, version, device, android, build_id, incremental, fp_key}.

    버전과 지문이 **둘 다** 있어야 쓸모가 있다 — 하나라도 없으면 None.
    """
    kind = is_request_subject(subject)
    if not kind:
        return None
    vm = _BUILD_VER_RE.search(body or "")
    fm = _FP_RE.search(body or "")
    if not (vm and fm):
        return None
    ver = valid_version(vm.group(1))
    if not ver:
        return None
    return {
        "kind": kind,
        "version": ver,
        "device": fm.group("device").upper(),
        "android": fm.group("android"),
        "build_id": fm.group("build_id"),
        "incremental": fm.group("incremental"),
        "fp_key": fp_key_of(fm.group("build_id"), fm.group("incremental")),
    }


def fp_key_of(build_id: str, incremental: str) -> str:
    """회차를 가르는 열쇠 — 빌드ID/증분.

    실측 MF52e: 같은 base(MF52EG15.00)인데 증분이 RA3·RA4·R23·R25 로 갈린다.
    base 로 묶으면 네 회차가 한 덩어리가 되어 C 리비전을 새 회차로 볼 수 없다.
    """
    b, i = (build_id or "").strip(), (incremental or "").strip()
    return f"{b}/{i}" if b and i else ""


def fp_key(fingerprint: str) -> str:
    """빌드지문 문자열에서 열쇠를 뽑는다. 못 읽으면 ''."""
    m = _FP_RE.search(fingerprint or "")
    return fp_key_of(m.group("build_id"), m.group("incremental")) if m else ""


#: '[Change list]' · 'change list:' · 'Here is the change list:' — 줄 **어디에 있어도** 잡는다
#: (실측 #3764 는 문장 끝에 붙어 있었다). 같은 줄의 나머지 글은 항목으로 치지 않는다.
_CL_HEAD_RE = re.compile(r"\[?\s*change\s*list\s*\]?\s*:?", re.I)

#: 글머리. 메일 클라이언트마다 다르게 온다(*, -, •, 그리고 Wingdings 가 깨진 l·n).
_CL_BULLET_RE = re.compile(r"^\s*(?:[*•·▪●–—-]|[ln]\s{2,})\s*(.+?)\s*$")

#: 다음 구획 머리 — '[SW]' · '[Test Suites]'. 여기서 목록이 끝난다.
_CL_SECTION_RE = re.compile(r"^\s*\[[^\]]{1,40}\]\s*:?\s*$")

#: 맺음말·인용 시작 — 여기서도 끝난다.
_CL_END_RE = re.compile(
    r"^\s*(thank|thanks|best regards|regards|sincerely|from:|sent:|to:|cc:|"
    r"보낸 사람|寄件者|-----\s*original)", re.I)

#: 목록처럼 생겼지만 **내용이 아닌** 줄 — 빌드 정보·시험 스위트.
_CL_META_RE = re.compile(
    r"^\s*(sw|build\s*ver\w*on|fingerprint|user|debug|build\s*image|gsi\s*image|"
    r"test\s*suites?|cts|ctsv|vts|gts|sts)\b\s*:?", re.I)


def change_list(body: str) -> list[str]:
    """승인 요청 메일 본문의 **[Change list] 항목**. 없으면 빈 목록.

    이 목록이 곧 그 회차의 변경 내용이다 — 실측 PM86 A13 86.53(#3764)의 두 줄이
    발행된 RN 의 Revision History 와 그대로 같았다.

    형태가 몇 가지다(실측).
      · '[Change list]' 다음 줄부터 '*' 글머리로 나열        (#1739 · PM86 86.07C2)
      · 'change list:' 다음에 빈 줄을 끼고 '*' 로 나열        (#3764 · PM86 86.53)
      · '[Change list]: We will send this one ASAP.' 자리표시  (#1766 · 목록 없음)
        → 같은 줄의 글은 항목으로 치지 않고, 이어지는 '[SW]' 구획에서 끊는다.

    인용된 원문에 같은 목록이 또 실려 오므로(회신) **처음 만난 것**만 읽는다.
    """
    lines = (body or "").splitlines()
    for i, raw in enumerate(lines):
        m = _CL_HEAD_RE.search(raw)
        if not m:
            continue
        out: list[str] = []
        blanks = 0
        for ln in lines[i + 1:]:
            s = ln.strip()
            if not s:
                blanks += 1
                if out and blanks >= 3:       # 목록이 끝나고 여백이 길어지면 그만
                    break
                continue
            if _CL_SECTION_RE.match(ln) or _CL_END_RE.match(ln):
                break
            b = _CL_BULLET_RE.match(ln)
            if not b:
                if out:                        # 글머리 없는 줄이 나오면 목록이 끝난 것
                    break
                blanks = 0
                continue
            blanks = 0
            item = b.group(1).strip()
            if not item or _CL_META_RE.match(item):
                continue
            out.append(item)
        if out:
            return out
    return []


def change_lines_for(version: str) -> tuple[list[str], str]:
    """그 버전의 **변경 내용**을 승인 요청 메일에서 읽는다. (줄 목록, 근거).

    같은 회차의 메일이 여럿이다(원 요청·회신). 원 요청에는 대개 '곧 보내겠다' 만 있고
    **나중 회신**에 실제 목록이 온다(실측 PM86 86.07C2: 8/5 요청에는 없고 8/6 회신에
    두 줄). 그래서 **늦게 온 것부터** 보고 처음 찾은 목록을 쓴다.
    """
    from app.database import get_session
    from app.models import CachedMail

    want = valid_version(version) or (version or "").strip()
    if not want:
        return [], "버전이 없습니다."
    with get_session() as db:
        rows = (db.query(CachedMail.id, CachedMail.subject, CachedMail.body_text)
                .filter(CachedMail.subject.like("%approval test%"))
                .order_by(CachedMail.id.desc()).all())
    seen = 0
    for mid, subject, body in rows:
        d = parse(subject or "", body or "")
        if not d or not _same_version(d["version"], want):
            continue
        seen += 1
        items = change_list(body or "")
        if items:
            return items, f"승인 요청 메일 #{mid} 의 [Change list] {len(items)}줄"
    if seen:
        return [], f"{want} 승인 요청 메일은 있으나 [Change list] 가 없습니다."
    return [], f"{want} 의 승인 요청 메일을 찾지 못했습니다."


def _same_version(a: str, b: str) -> bool:
    """버전 표기 비교 — 공백·대소문자만 무시한다('86.07C2' == '86.07 c2')."""
    return re.sub(r"\s+", "", (a or "")).upper() == re.sub(r"\s+", "", (b or "")).upper()



def request_versions() -> dict:
    """{지문열쇠: {version, mail_id, device, android, kind}} — 메일함 전체에서.

    같은 지문이 여러 번 나오면(회신) **가장 이른 것**을 남긴다 — 원 요청이다.
    """
    from app.database import get_session
    from app.models import CachedMail

    out: dict = {}
    with get_session() as db:
        rows = (db.query(CachedMail.id, CachedMail.subject, CachedMail.body_text,
                         CachedMail.received_at)
                .filter(CachedMail.subject.like("%approval test%"))
                .order_by(CachedMail.id).all())
    for mid, subject, body, received in rows:
        d = parse(subject or "", body or "")
        if not d or not d["fp_key"]:
            continue
        cur = out.get(d["fp_key"])
        if cur is None or mid < cur["mail_id"]:
            out[d["fp_key"]] = {**d, "mail_id": mid, "received_at": received}
    return out


def our_gms_product(device: str, android: str) -> bool:
    """제품 관리에 등록된 **우리 GMS 제품**인가 (사용자 확정 2026-09-07).

    판별을 코드에 박지 않는다 — 제품 관리 화면이 곧 기준이다. PS32 처럼 우리 제품인데
    아직 등록하지 않은 것은 등록하는 순간부터 잡히고, HT2·Z7_JP 같은 OEM/ODM 공급분은
    등록하지 않는 한 조용히 빠진다.

    실측(2026-09-07): 메일에서 본 지문 device 13종 중 8종이 등록돼 있고 이름이 전부
    정확히 일치했다. 미등록 5종은 MX450·Z7_JP·XG5·PS32·HT2.
    """
    from app.database import get_session
    from app.models import OsProduct

    dev = re.sub(r"[\s_]+", "", (device or "")).upper()
    if not dev:
        return False
    with get_session() as db:
        rows = db.query(OsProduct.product, OsProduct.android, OsProduct.os_type).all()
    for prod, andr, os_type in rows:
        if re.sub(r"[\s_]+", "", (prod or "")).upper() != dev:
            continue
        if str(os_type or "").strip().upper() != "GMS":
            continue                       # 지금은 GMS 만 다룬다
        if android and str(andr or "") and str(andr) != str(android):
            continue                       # PM95 A13 / A15 구분
        return True
    return False
