"""단계 ② Release Note 제작 — 결정적 수집 + Claude 요약 → 이슈 Excel + RN PDF.

[FIH][Approval] 트리거 시:
  1) (사전계산) 트리거 메일 분석으로 제품(모델 코드)+Android 확정.
  2) 그 제품+Android 의 `[GMS MR] {버전} 배포` 메일을 역추적 → 본문 '개선사항'의 이슈 #목록이
     곧 이번 버전 이슈(근거). 없으면 SP Resource 버전 + '이슈 수동 지정 필요' 로 정직 처리.
  3) 이슈 상세를 Redmine 에서 읽고(무토큰), Claude 로 청크 요약(내부 문제/해결 + 고객용 change).
  4) 이슈 Excel(내부) + RN 템플릿 채운 PDF(전체 이슈 RN) 생성. RN PDF 경로는 상태에 저장(③ 업로드용).

모든 원본(메일·Redmine·SP·RN)은 **읽기 전용**. 산출물은 로컬 저장.
Rate limit(모든 모델 분당 10k input) 대응: 수집은 코드로, Claude 는 압축 입력 청크 요약만.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path

import anthropic

from app import progress
from app.agent.runner import StageOutput
from app.atomic_io import atomic_write_bytes
from app.integrations import redmine as RM
from app.mail import search as MAILSEARCH
from app.pipeline.mail_analysis import analyze_mail
# 표준 [모듈] 태그 어휘는 rn_select 이 단일 출처다(실제 배포 RN 에서 뽑았다).

# 요약 모델. Opus 4.8 은 조직 rate limit(분당 10k input)이 빡빡해 모델별 별도 한도인 Sonnet 5 로 수행.
_MODEL = os.getenv("RN_RESEARCH_MODEL", "claude-sonnet-5")
_RATE_RETRIES = 5  # 429(rate limit) 시 대기 후 재시도 횟수
_WORK = Path(__file__).resolve().parent.parent.parent / "work" / "rn_research"


def _precompute(user_id: int, mail_id: int | None) -> tuple[dict, str]:
    """제품군 + Android 확정. (context, error).

    /osupdate 의 작업(제품 SKU + 버전)에서 시작하면 제품·Android 가 **작업에 이미 있다** —
    상태에 심어져 있으므로 트리거 메일을 Claude 로 분석할 필요가 없다(메일이 아예 없을 수도
    있다: AOSP·Russia 계열엔 [FIH] 메일이 오지 않는다).
    메일함에서 시작한 기존 작업은 예전처럼 트리거 메일을 분석한다.

    버전·이슈목록은 여기서 정하지 않는다 — 아래에서 [GMS MR] 배포 메일을 역추적해 근거로 삼는다.
    """
    if mail_id is None:
        return {}, "mail_id 가 없습니다(트리거 메일 필요)."

    from app.pipeline.state import get_state

    st = get_state(mail_id) or {}
    if st.get("product"):
        return {
            "product": st["product"],
            "android": st.get("android", ""),
            "mail_type": "job",
            "build_fingerprint": st.get("build_fingerprint", ""),
            "trigger_mail_id": st.get("approval_mail_id") or st.get("trigger_mail_id"),
        }, ""

    a = analyze_mail(mail_id, user_id)
    if a.get("error"):
        return {}, f"메일 분석 실패: {a['error']}"
    product = a.get("product_family") or ""
    android = a.get("android_version") or ""
    if not product:
        return {}, "메일에서 제품을 식별하지 못했습니다."
    return {
        "product": product,
        "android": android,
        "mail_type": a.get("mail_type"),
        "build_fingerprint": a.get("build_fingerprint"),
        "trigger_mail_id": mail_id,
    }, ""


def _create_with_retry(client, **kwargs):
    """rate_limit(429) 시 retry-after 만큼 대기 후 재시도. 그래도 실패면 예외 전파."""
    last = None
    for attempt in range(_RATE_RETRIES + 1):
        try:
            return client.messages.create(**kwargs)
        except anthropic.RateLimitError as e:  # noqa: PERF203
            last = e
            wait = 30.0
            try:
                ra = e.response.headers.get("retry-after")
                if ra:
                    wait = float(ra)
            except Exception:  # noqa: BLE001
                pass
            if attempt < _RATE_RETRIES:
                time.sleep(min(wait + 2, 65))
    raise last


# ── ② 재설계: [GMS MR] 배포 메일이 '버전 + 개선사항(이슈목록)'의 근거 ──────
# 트리거([FIH][Approval])의 제품+Android → 전체 메일에서 그 제품의 [GMS MR] {버전} 배포 메일을
# 역추적 → 본문 '개선사항'의 이슈 #목록이 곧 이번 버전 이슈(근거). 없으면(입사 이전 등) SP 버전 +
# "Request to start the GMS MR approval test" 를 근거 후보로 제시하고 사람 확인으로 넘긴다.
# 룰은 최소화하고, 모든 산출은 실제 메일/이슈에 근거한다.
# 배포 메일 개선사항 이슈 상한. 실측(2026-08-02, 캐시 메일 1,578통)에서 PM95 A15 95.52 가
# 합집합 **정확히 40건** — 예전 상한 40 에 딱 붙어 있었다. 한 건만 늘어도 잘렸을 자리라
# 여유를 3배로 두고, 그래도 넘치면 아래에서 **버린 건수를 결과에 적는다**(조용히 자르지 않는다).
# 이슈가 많으면 Claude 호출도 그만큼 늘어난다(요약 6건·선별 8건 단위) — 환경변수로 조절 가능.
_MAX_ISSUES = int(os.getenv("RN_MAX_ISSUES", "120") or "120")
_MAX_RELATED = int(os.getenv("RN_MAX_RELATED", "120") or "120")   # 생성자≠해결자 추적용 관계 이슈
_CHUNK = 6                # Claude 요약 청크(관계정보 포함으로 블록이 커져 6으로 축소, 토큰 여유)
_JUDGE_MAX_TOKENS = 5000

# 배포 유형 태그: MR(Maintenance)·IR·LR 모두 인식. 제목에서 [GMS XX] 를 잡는다.
_DEPLOY_TAG_RE = re.compile(r"\[GMS\s*(MR|IR|LR)\]", re.I)
# [GMS] 태그가 없는 관례: '[제품][A안드] Release {버전} 배포' — 'Release' 키워드 뒤에서 버전을 잡는다.
_RELEASE_KW_RE = re.compile(r"\bRelease\b", re.I)
# 버전(느슨): '450.50' / '95.07' + 선택 리비전('B12'·'C1'·'V3' 등 공백 허용). '95.07 C1' 같이 띄어써도 잡는다.
# 형식 1('95.52 B3')과 형식 2('PM84PG15.03')를 모두 잡는다. 형식 2 는 제품명+Android 가
# major 라, 숫자만 보면 'PM84PG15.03' 이 '15.03' 으로 잘려 다른 제품 버전처럼 보인다.
_DEPLOY_VER_RE = re.compile(
    r"((?:[A-Za-z][A-Za-z0-9]*)?[0-9]+\.[0-9]+(?:\s*[A-Za-z]{1,3}\s*\d+)?)")
_DEPLOY_DATE_RE = re.compile(r"\(([A-Za-z]{3}\s+\d{1,2}\s+\d{4})\)")
_SUBJ_ANDROID_RE = re.compile(r"\bA(\d{1,2})\b")


def RNL_STD(product: str, android, version: str, suffix: str = "") -> str:
    """생성 문서의 표준 파일명(확장자 없음) — rn_lookup.standard_name 의 짧은 이름."""
    from app.pipeline.rn_lookup import standard_name

    return standard_name(product, android, version, suffix)


def _norm_ver(v: str) -> str:
    """버전 비교용 정규화 — 공백 제거 + 대문자('95.07 C1' == '95.07c1' == '95.07C1')."""
    return re.sub(r"\s+", "", (v or "")).upper()


def _parsed_ver(v: str):
    """버전 문자열 파싱 — 문법은 dev_rn 한 벌만 쓴다(두 벌로 갈라지면 반드시 어긋난다).

    형식 1 '95.07C1' 과 형식 2 'TR54G15.03C1'(제품명+Android 가 major)을 모두 다룬다.
    공백만 걷어내고 대소문자는 건드리지 않는다 — '2x.05' 같은 표기를 임의로 바꾸지 않기 위해서다.
    """
    from app.pipeline.dev_rn import parse_version

    return parse_version(re.sub(r"\s+", "", v or ""))


def _base_ver(v: str) -> str:
    """문서·업로드 표기용 base 버전 — 리비전(C1·B12 등)을 뗀 부분.

    ('95.07 C1' · '95.07C1' → '95.07',  '95.06 B12' → '95.06',
     'TR54G15.03C1' → 'TR54G15.03')
    """
    p = _parsed_ver(v)
    if p is None:
        return (v or "").strip()
    return p.text[:len(p.text) - len(p.suffix)] if p.suffix else p.text


def _exact_ver(v: str) -> str:
    """표기에서 **버전 부분만** 뽑는다(리비전 포함). 못 뽑으면 원문 그대로.

    ('95.52 (Jul 18 2026)' → '95.52',  '67.00 B2 (Jul 26 2021)' → '67.00B2',
     'PM84PG15.02 C1 (May 18 2026)' → 'PM84PG15.02C1')
    `_base_ver` 는 리비전을 떼지만 이쪽은 **남긴다** — 같은 판인지 가릴 때 쓴다.
    """
    p = _parsed_ver(v)
    return p.text if p else (v or "").strip()


def _c_revision(v: str) -> str:
    """버전 표기에서 **C 리비전 접미**만 뽑는다('95.07 C1' → 'C1'). 없으면 ''.

    형식 2('TR54G15.03C1' → 'C1')도 같다. C 외의 접미(B3 = Beta, RC 등)는 비배포
    빌드라 공식 버전명에 들어가지 않는다 — dev_rn 의 '목록에 남기는 버전' 규칙과 같은 기준이다.
    """
    p = _parsed_ver(v)
    sfx = (p.suffix if p else "").upper()
    return sfx if sfx.startswith("C") else ""


def _same_release(a: str, b: str) -> bool:
    """두 버전 표기가 **같은 회차**인가.

    핵심 규칙(사용자 확정 2026-08-18): **C 리비전이 있으면 그것이 그 회차의 버전이다.**
    민짜 판은 C 판으로 대체되며, 문서에 두 줄로 남지 않는다 — 실측 PM84P 개발 RN 에
    'PM84PG15.02 C1' 만 있고 민짜 'PM84PG15.02' 행은 없다. 배포 공지도 마찬가지라
    PM95 A13 은 '95.07 C1' 공지만 있고 민짜 '95.07' 배포는 아예 없었다.
    (excel_docs 의 회차 번호도 같은 기준이다 — 'C 리비전은 base 로 묶어 하나로 센다'.)

    Beta(B판)는 다르다 — 회차마다 따로 남는다. 실측 PM67 67.09 문서에 67.00 B1~B5 와
    민짜 67.00 이 **각각 한 행**으로 실려 있다.

      '95.07'   vs '95.07C1'  → True   (C 판이 그 회차의 정식 판)
      '95.07C1' vs '95.07C2'  → True
      '67.00'   vs '67.00 B3' → False  (Beta 는 별개 행)
      '95.07'   vs '95.08'    → False
    """
    if _base_ver(a) != _base_ver(b):
        return False
    pa, pb = _parsed_ver(a), _parsed_ver(b)
    if pa is None or pb is None:
        return _norm_ver(a) == _norm_ver(b)
    sa = (pa.suffix or "").upper()
    sb = (pb.suffix or "").upper()
    beta_a = bool(sa) and not sa.startswith("C")
    beta_b = bool(sb) and not sb.startswith("C")
    if beta_a or beta_b:
        return sa == sb          # Beta 는 판마다 별개
    return True                  # 민짜와 C 판은 같은 회차


def _c_rev_no(rev: str) -> int:
    """'C2' → 2, '' → 0. C 리비전의 높낮이를 견주는 유일한 자리."""
    m = re.match(r"^[Cc](\d+)$", str(rev or "").strip())
    return int(m.group(1)) if m else 0


def _official_version(base_v: str, cands: list[dict]) -> tuple[str, str]:
    """공식 릴리즈 버전명 = base + **배포 공지에 있는 가장 높은 C 리비전**. (버전, 사유).

    사용자 확정(2026-08-04): 95.07 · 95.07C1 · 95.07C2 배포 공지가 있으면 공식 릴리즈
    버전명은 95.07C2 다(95.06 다음 회차가 95.07C2 가 된다). 그 세 공지의 개선사항 이슈는
    합집합으로 모은다 — 그쪽은 호출부의 same 루프가 이미 하고 있다.

    C 가 하나도 없으면 base 를 그대로 돌려준다(사유 '').
    """
    revs = [r for c in cands if not c.get("is_beta")
            if (r := _c_revision(c.get("version", "")))]
    if not revs:
        return base_v, ""
    top = max(revs, key=_c_rev_no)
    return base_v + top, f"배포 공지의 최고 리비전 {top} 반영 → 공식 {base_v}{top}"


#: 지정 버전과 배포 공지가 어긋났다는 표식. 결과 문구·검수 화면이 이걸 보고 경고한다.
VER_CONFLICT_MARK = "⚠ 버전 확인 필요"


def official_version_keep(current: str, base_v: str,
                          cands: list[dict]) -> tuple[str, str, bool]:
    """공식 버전을 정한다 — **C 는 올리기만 한다**. (버전, 사유, 어긋남).

    _official_version 은 배포 공지에서 **찾은 것만** 돌려준다. 그 회차의 공지가 메일함에
    없으면 base 를 주는데, 그 값으로 덮으면 지정한 버전이 조용히 낮아진다.

    실측 job-24(PM86): 작업은 86.07C2 인데 PM86 배포 공지는 6월의 86.07 하나뿐이라
    (C2 공지가 메일함에 없다) 문서·파일명·파일서버 발행본이 전부 86.07 로 나갔다.
    TA팀이 정리한 'OS 버전' 표(#2669)에는 PM86 A11 = 86.07C2 로 적혀 있다 — 같은 표의
    PM95 A13 = 95.07C1 은 우리 작업과 일치하므로, 그 칸이 곧 RN 에 쓰는 정식 버전이다.

    그래서 둘 중 **C 가 높은 쪽**을 쓴다. 공지가 더 높으면 올리고(C1→C2), 공지가 낮거나
    없으면 지정값을 지킨다. base 는 호출부가 정한 것이라 건드리지 않는다.

    ※ 지킬 때는 **어긋남을 반드시 알린다**(세 번째 반환값 · VER_CONFLICT_MARK).
      조용히 지나가면 이번처럼 승인·발행까지 간 뒤에야 드러난다. 둘 중 어느 쪽이 맞든
      사람이 한 번 보고 넘어가야 하는 자리다.
    """
    off, note = _official_version(base_v, cands)
    if _c_rev_no(_c_revision(current)) > _c_rev_no(_c_revision(off)):
        return current, (f"{VER_CONFLICT_MARK} — 작업에 적힌 {current} 로 만듭니다. "
                         f"배포 공지에서 확인된 버전은 {off} 입니다"
                         f"({current} 배포 공지를 못 찾았습니다). "
                         "둘 중 무엇이 맞는지 확인하세요."), True
    return off, note, False

_SUMMARY_SYSTEM = (
    "당신은 포인트모바일 Release Note 작성 보조자입니다. 아래 Redmine 이슈들은 이번 OS 버전 "
    "배포 메일의 '개선사항'에 명시된 것(=이번 버전 포함)입니다. 각 이슈에 대해: "
    "(1) 내부 검토용 '문제'와 '해결'을 한국어로 아주 간결히(각 40자 이내), "
    "(2) 고객용 Release Note 에 실을 **한 줄 변경 설명(change)** 을 **반드시 영어로**(이슈 원문이 "
    "한국어여도 영어로 번역해서), 릴리스노트 문체로 작성 — change 필드에 한국어를 절대 쓰지 마세요, "
    "(3) **created_by**=이 이슈를 만든 사람, **resolved_in**=실제 수정/구현이 이뤄진 이슈 번호"
    "(보통 이 이슈에 'Related to'로 연결된 개발/구현 이슈; 연결이 없으면 이 이슈 자신의 번호), "
    "**resolved_by**=그 구현/해결 이슈를 만들거나 해결한 사람. "
    "created_by/resolved_in/resolved_by 는 **주어진 이슈·관계이슈 정보만** 근거로 하고 모르면 빈 값으로 두세요(추측 금지). "
    "지어내지 말고 주어진 내용에 근거하세요. 반드시 지정한 JSON 으로만 답하세요."
)


# RN 아래부분(컴포넌트 버전) 수동 입력 파서 — 'Key: Value' 줄. 보안패치는 PFU 자동이라 제외.
_COMP_KEYS = {
    "gms": "gms_package", "gms package": "gms_package",
    "sdk": "sdk", "emkit": "sdk",
    "audio": "audio",
    "touch": "touch", "touch panel": "touch",
    "wifi driver": "wifi_driver", "driver": "wifi_driver",
    "wifi firmware": "wifi_firmware", "firmware": "wifi_firmware",
    "wifi config": "wifi_config", "wifi configuration": "wifi_config",
    "config": "wifi_config", "configuration": "wifi_config",
    "known issues": "known_issues", "known issue": "known_issues",
}


def _parse_components(text: str) -> dict:
    """'Key: Value' 형식 텍스트 → {gms_package, sdk, audio, touch, wifi_*, known_issues}."""
    out: dict = {}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        key = _COMP_KEYS.get(k.strip().lower())
        if key and v.strip():
            out[key] = v.strip()
    return out


def _resolution_of(iss: dict) -> str:
    notes = iss.get("journal_notes") or []
    if not notes:
        return ""
    txt = notes[-1].get("note", "") or ""
    # Redmine 액션 링크(Quote/Edit/Delete) 잔재 제거
    txt = re.sub(r"\s*Quote\s*Edit\s*Delete\s*", " ", txt).strip()
    return txt


def _compact_issue(iss: dict, related_map: dict | None = None) -> str:
    related_map = related_map or {}
    rels = []
    for r in iss.get("relations") or []:
        rid = str(r.get("id") or "")
        rl = related_map.get(rid)
        if rl:
            rels.append(
                f"#{rid}(생성 {rl.get('created_by') or '?'}/해결 {rl.get('resolved_by') or '?'}"
                f"/{rl.get('status','')}): {(rl.get('subject','') or '')[:36]}"
            )
        else:
            rels.append(f"#{rid}: {(r.get('text','') or '')[:60]}")
    return (
        f"#{iss['id']} [{iss.get('status','')}] {iss.get('subject','')}\n"
        f"  cat={iss.get('category','')} | 생성 {iss.get('created_by') or '?'} "
        f"| 담당 {iss.get('assignee') or '?'} | 상태해결자 {iss.get('resolved_by') or '-'}\n"
        f"  문제: {(iss.get('description') or '')[:250]}\n"
        f"  최근노트: {_resolution_of(iss)[:200] or '(없음)'}\n"
        f"  관계이슈: {'; '.join(rels) if rels else '없음'}"
    )


def _not_ours():
    """우리 앱이 보낸 메일을 걸러내는 조건(X-TW-Automation 표식이 없는 것만).

    앱이 보낸 메일도 같은 메일함으로 돌아온다. 배포 공지에 **회신**하기 시작하면
    제목이 'RE: [PM95][GMS MR] 95.52 … 배포' 라 배포 공지로 잡히고, 그 회신 날짜가
    최신이라 회차 판정까지 흔든다(실측 2026-08-19: 테스트 메일 한 통이 PM95 A13 의
    회차를 95.52 로 만들었다).
    """
    from sqlalchemy import or_

    from app.models import CachedMail as _CM

    return or_(_CM.app_marker.is_(None), _CM.app_marker == "")


def _deploy_subject_rows() -> list[dict]:
    """제목에 '배포' 가 든 메일의 **제목 수준** 목록(최신 먼저). 본문은 읽지 않는다.

    배포 공지 판별은 전부 제목 기준이라 본문을 훑을 이유가 없다. 메일 본문은 통째로
    커서 전 메일을 훑으면 질의 하나에 수 초가 걸린다.
    """
    from app.database import get_session
    from app.models import CachedMail

    with get_session() as db:
        rows = (db.query(CachedMail.id, CachedMail.subject, CachedMail.received_at)
                .filter(CachedMail.subject.like("%배포%"))
                .filter(_not_ours())
                .order_by(CachedMail.id.desc()).all())
    return [{"id": r[0], "subject": r[1] or "", "received_at": r[2] or ""} for r in rows]


def _deploy_candidates(user_id: int, product: str, android: str,
                       want_version: str = "") -> list[dict]:
    """제품(+Android)의 배포 공지 메일 후보를 **모두** 반환(정렬: 버전일치→clean→non-RE→최신).

    두 관례를 모두 인식한다(룰 최소화):
      (A) `[제품][GMS MR|IR|LR] {버전} 배포`
      (B) `[제품][A{안드}] Release {버전} 배포`  (예: '[PM95][A13] Release 95.07 C1 (Jul 10 2026) 배포')
    body 는 싣지 않는다(가볍게 — 필요할 때만 읽는다).
    각 dict: {mail_id, subject, version, gms_type, date, is_beta, is_rc, not_re, clean}.
    """
    # 후보 수집: **제목에 '배포' 가 든 메일 전부**를 한 번에 가져온다.
    #  아래 판별(제품·Android·버전·회신 여부)이 전부 제목만 보므로 본문은 볼 이유가 없다.
    #  예전에는 타겟 쿼리 3~4개를 mail.search 로 돌렸는데, 그쪽은 질의마다 전 메일의
    #  **본문까지** 훑어 한 번에 수 초가 걸렸다 — 제품 수에 비례해 늘어나 28개 SKU 의
    #  /osupdate 예열이 72초였다(실측 2026-08-04). 제목 색인 한 번이면 충분하다.
    #  200건 상한도 없앤다(예전엔 상한에 걸려 오래된 배포 메일이 잘렸다 — 실측 id=50 유실).
    rows = _deploy_subject_rows()

    want_n = _norm_ver(want_version)
    ranked = []
    for r in rows:
        s = r.get("subject") or ""
        low = s.lower()
        if "배포" not in s:
            continue
        # 제품명은 **경계까지** 봐야 한다. 부분일치로 두면 PM84 가 PM84P 를,
        # PM45 가 PM451·PM452 를 통째로 빨아들인다(실측 2026-08-04: PM84 A13 목록에
        # PM84P 의 PM84PG15.03 회차가 '신규'로 올라왔다).
        if product and not re.search(
                rf"{re.escape(product)}(?![A-Za-z0-9])", s, re.I):
            continue
        # 제목에 A{n} 태그가 있으면 요청 Android 와 반드시 일치해야 한다(A13↔A15 오인 방지).
        am = _SUBJ_ANDROID_RE.search(s)
        if am and (not android or am.group(1) != str(android)):
            continue
        # 버전 추출 위치: [GMS XX] 뒤 → 'Release' 뒤 → 제목 전체 순.
        tagm = _DEPLOY_TAG_RE.search(s)
        relm = _RELEASE_KW_RE.search(s)
        if tagm:
            mv = _DEPLOY_VER_RE.search(s[tagm.end():])
            gms_type = tagm.group(1).upper()          # MR / IR / LR
        elif relm:
            mv = _DEPLOY_VER_RE.search(s[relm.end():])
            gms_type = "Release"
        else:
            mv = _DEPLOY_VER_RE.search(s)
            gms_type = ""
        if not mv:
            continue
        ver = re.sub(r"\s+", " ", mv.group(1).strip())
        ver_match = 1 if (want_n and _norm_ver(ver) == want_n) else 0
        clean = 1 if s.rstrip().endswith("배포") else 0   # 협조요청/결과보고 아닌 순수 공지
        not_re = 0 if low.strip().startswith("re:") else 1
        dm = _DEPLOY_DATE_RE.search(s)
        # '원본 공지'와 '회신·전달'을 가르는 두 번째 신호. not_re 는 제목 **맨 앞**이 'RE:'
        # 일 때만 0 이라, 'PM45 FOTA Test 요청 // RE: … 배포' 같은 회신이 원 공지보다 위로
        # 올라와 대표로 뽑혔다(실측 job-15·job-23). 근거 합집합에서 빼지는 않는다 —
        # PM86 86.07 은 'FW:' 사본이 유일본이라 빼면 이슈 7건을 통째로 잃는다.
        pure = 0 if re.search(r"//|\bRE:|\bFW:", s, re.I) else 1
        ranked.append((ver_match, clean, not_re, pure, r["id"], {
            "mail_id": r["id"], "subject": s, "version": ver, "gms_type": gms_type,
            "date": dm.group(1) if dm else "",
            "is_beta": bool(re.search(r"B\d", ver)),
            "is_rc": bool(re.search(r"\bRC\b", s, re.I)),
            "not_re": not_re, "clean": clean, "pure": pure,
        }))
    # 버전일치 → clean → non-RE → 순수공지 → 최신(id)
    ranked.sort(key=lambda x: x[:5], reverse=True)
    return [d for *_, d in ranked]


# 배포 공지 본문의 블록 머리. 항목과 머리 모두 '*' 로 시작해 구분이 안 되므로
# **글머리를 뗀 텍스트가 이 낱말 자체인 줄**을 머리로 본다(들여쓰기·탭에 기대지 않는다).
_SECTION_HEADS = {"배포", "개선사항", "참고사항", "기타", "비고", "특이사항"}
_ITEM_SECTIONS = {"개선사항", "참고사항"}      # 여기 담긴 항목만 RN 후보다
# 인용된 회신 사슬이 시작되는 자리 — 여기서 끊지 않으면 남의 메일까지 긁어온다.
_QUOTE_HEAD_RE = re.compile(
    r"^(best regards|from:|sent:|to:|cc:|subject:|보낸 사람:|받는 사람:|제목:|감사합니다)",
    re.I)
_BULLET_RE = re.compile(r"^[*\-·•o]\s*(.*)$")
# 항목으로 볼 수 없는 줄 — 경로·링크·표 머리.
_SKIP_ITEM_RE = re.compile(r"^(\\\\|<file:|https?://|<https?://)", re.I)


def _deploy_text_items(body: str) -> list[str]:
    """배포 공지의 개선사항·참고사항에서 **이슈 번호가 없는** 항목 문구를 뽑는다.

    실측(2026-08-04) 배포 공지는 이슈 번호가 붙은 줄과 안 붙은 줄이 섞여 있다.
        *  BTS 1 warning 패치 적용 (#33779)     ← 이슈로 잡힌다(_deploy_issue_ids)
        *  EDLA (ENTERPRISE_DEVICE) flag 추가    ← 번호가 없어 통째로 버려지던 줄
        *  SPL: Update SPL to 2026-06-05         ← 참고사항에 있는 보안패치 갱신
    번호가 없다고 RN 에 안 실을 이유가 없으므로 여기서 문구 그대로 건져 선별표에 올린다.

    본문 전체를 훑지 않는다 — 배포 공지는 회신 사슬이 길게 붙어 있어, 섹션 밖까지
    긁으면 남의 메일 문장이 RN 후보로 들어온다.
    """
    out: list[str] = []
    section = ""
    open_item = False        # 직전 글머리를 **채택했는가** — 접힌 줄을 이어붙일지 판단
    for raw in (body or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if _QUOTE_HEAD_RE.match(line):
            break                                  # 인용 사슬 시작 — 여기까지만
        m = _BULLET_RE.match(line)
        text = (m.group(1) if m else line).strip(" \t:：")
        if text in _SECTION_HEADS:                 # 블록 머리
            section, open_item = text, False
            continue
        if section not in _ITEM_SECTIONS:
            open_item = False
            continue
        if not m:
            # 글머리 없는 줄 = 앞 항목이 줄바꿈된 것(한글 제목이 자주 접힌다).
            # **직전 글머리를 버렸으면 이어붙이지 않는다** — 버린 줄(이슈·경로)의 접힌
            # 부분이 엉뚱한 항목 뒤에 달라붙는다(실측 #372).
            if open_item and not _SKIP_ITEM_RE.match(text) and len(text) > 1:
                out[-1] = (out[-1] + " " + text).strip()
            continue
        open_item = False
        if _SKIP_ITEM_RE.match(text) or len(text) < 4:
            continue
        if RM.find_issue_refs(text):               # 번호가 있으면 이슈 쪽에서 다룬다
            continue
        out.append(text)
        open_item = True
    # 순서 유지 + 중복 제거(같은 항목이 B판·RC판 공지에 반복해서 실린다).
    seen: set[str] = set()
    uniq: list[str] = []
    for t in out:
        k = re.sub(r"\s+", " ", t).strip().lower()
        if k not in seen:
            seen.add(k)
            uniq.append(re.sub(r"\s+", " ", t).strip())
    return uniq


def _deploy_issue_ids(body: str) -> list[str]:
    """배포 메일 본문의 '개선사항' 섹션에서 이슈 #번호 목록을 뽑는다(없으면 전체)."""
    idx = body.find("개선사항")
    seg = body[idx:] if idx >= 0 else body
    return RM.find_issue_refs(seg)


def _raw_item(iss: dict) -> dict:
    return {
        "row": [
            f"#{iss['id']}", (iss.get("subject", "") or "")[:80], iss.get("category", ""),
            iss.get("created_by", ""), "", iss.get("resolved_by", ""),
            (iss.get("description") or "")[:120], _resolution_of(iss)[:120],
            iss.get("status", ""), "",
        ],
        "change": (iss.get("subject", "") or "")[:120],
    }


def _summarize_chunk(
    client, product, android, version, chunk: list[dict],
    revision: str = "", related_map: dict | None = None,
) -> list[dict]:
    """이슈 청크를 Claude 로 요약 → [{row, change}]. 실패 시 raw 폴백.

    revision: 사람이 준 추가 수정 지시. related_map: 관계 이슈 상세(생성자/해결자 매핑용).
    """
    from app.pipeline.ai_edit import _extract_json

    blocks = "\n\n".join(_compact_issue(i, related_map) for i in chunk)
    rev_line = (
        f"\n[사람의 추가 수정 지시 — 반드시 반영]: {revision}\n" if revision.strip() else ""
    )
    prompt = (
        f"제품 {product} A{android} · 이번 버전 {version}.\n" + rev_line
        + f"=== 이슈({len(chunk)}건) ===\n{blocks}\n\n"
        "아래 JSON 으로만 답하세요:\n"
        '{ "items": [ {"issue":"#번호","title":"제목","category":"카테고리",'
        '"created_by":"생성자","resolved_in":"#해결이슈번호","resolved_by":"해결자",'
        '"problem":"문제요약","resolution":"해결요약","status":"상태",'
        '"change":"고객용 한 줄 변경 설명(영어)","note":"비고"}, ... ] }'
    )
    _cols = ("issue", "title", "category", "created_by", "resolved_in", "resolved_by",
             "problem", "resolution", "status", "note")
    try:
        resp = _create_with_retry(
            client, model=_MODEL, max_tokens=_JUDGE_MAX_TOKENS,
            system=_SUMMARY_SYSTEM, messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        parsed = _extract_json(text)
        items = (parsed or {}).get("items")
        if items:
            return [
                {"row": [str(it.get(k, "")) for k in _cols],
                 "change": str(it.get("change", "")).strip()}
                for it in items
            ]
    except Exception:  # noqa: BLE001
        pass
    return [_raw_item(i) for i in chunk]


# ── Revision History 정리(배포본 스타일) ──────────────────────────────
# 배포된 RN(예: PM68 68.03 V3) 분석 결과:
#  · Description 머리 = "{버전} ({빌드날짜})", Update = "MMM DD, YYYY"
#  · 조사/QA검증/내부전용 잡음 제거 + 동사 통일(Added/Fixed/Improved/Updated…), 평면 리스트
#  · 보안패치는 첫 줄("Updated security patches to <날짜>.")
# 실제 배포 RN 155건·불릿 4,285개 분석으로 도출한 'PointMobile Revision History 문체' 규칙.
_CURATE_SYS = (
    "당신은 PointMobile Android 단말 Release Note 의 Revision History 를 '실제 배포본 문체'로 다시 씁니다. "
    "입력받은 고객용 변경 문구 목록을, PointMobile 이 실제로 쓰는 짧은 항목 리스트로 정리하세요.\n"
    "형식 규칙(실제 배포 RN 155건 분석 기반):\n"
    "0) **출력은 반드시 100% 영어입니다. 입력에 한국어가 섞여 있어도 모두 영어로 번역해 작성하고, "
    "어떤 항목도 한국어로 출력하지 마세요.** (PointMobile Release Note 는 항상 영어입니다.)\n"
    "1) 각 항목은 한 줄, **간결하게(대개 6~12단어)**. 장황한 설명·중복 수식 금지.\n"
    "2) **과거형 동사로 시작**: Fixed / Added / Improved / Supported / Updated / Removed / Changed / "
    "Modified / Applied / Implemented / Enhanced / Disabled 등.\n"
    "3) **'[WLAN]' · '[System]' 같은 [모듈] 태그는 붙이지 않는다** — 문장으로 바로 시작한다. "
    "실제 배포 Release Note 도 대부분 태그 없이 쓴다(사용자 확정 2026-08-27).\n"
    "4) 보안패치 줄과 'Improved system stability / fixed minor bugs' 같은 **뭉뚱그린 일반 문구**는 "
    "**시스템이 따로 붙이니 여기서 만들지 마세요**(입력에 있으면 그대로 한 번만 통과시키고, "
    "새로 만들거나 여러 번 반복하지 마세요).\n"
    "5) 입력 목록은 **사람(RN 승인자)이 공개 대상으로 직접 고른** 항목입니다. 임의로 버리거나 "
    "일반 문구로 뭉치지 말고 **항목마다 그 내용에 맞는 구체적인 한 줄**을 쓰세요. 정말 고객에게 "
    "의미 없는 항목이라면 **그 줄을 아예 만들지 말고 빼세요** — 일반 문구로 대체하면 안 됩니다.\n"
    "6) 원문에 없는 내용은 지어내지 말 것. 항목 순서는 대체로 입력 순서를 유지. "
    "**같은 문구를 두 번 이상 출력하지 마세요.**\n"
    "실제 배포본 문체 예시:\n"
    "- [WLAN] Fixed the connection problem that Wi-Fi took a long time to connect after turning it on.\n"
    "- [Scanner] Added the HW Scan Key Mode.\n"
    "- [Direct Clone] Fixed an issue that restore erased all saved Wi-Fi networks.\n"
    "- [Battery] Improved battery safety by limiting volume and brightness at low battery.\n"
    "- [Settings] Added an option to disable the Power key action.\n"
    "- [Phone] Fixed missed-call display when using a specific carrier SIM.\n"
    "- [EmLogTool] Enabled kernel message logging by default.\n"
    "- [LogTrace] Added QXDM battery log collection.\n"
    '반드시 JSON 으로만: {"lines": ["...", "..."]}'
)



def _curate_changes(client, product: str, version: str, changes: list[str]) -> list[str]:
    """전체 change 목록 → 배포용 Revision History 스타일 큐레이션(잡음 제거·문체 통일).

    모든 줄에 모듈 태그를 보장한다 — 모델이 빠뜨려도 코드에서 채운다(2026-08-19).
    """
    if not changes:
        return []
    from app.pipeline.ai_edit import _extract_json

    body = "\n".join(f"- {c}" for c in changes)
    prompt = (f"제품 {product} · 버전 {version}\n원본 변경 목록:\n{body}\n\n"
              "위 규칙대로 큐레이션한 JSON 을 반환하세요.")
    try:
        resp = _create_with_retry(
            client, model=_MODEL, max_tokens=_JUDGE_MAX_TOKENS,
            system=_CURATE_SYS, messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        lines = (_extract_json(text) or {}).get("lines")
        if lines:
            return _dedup_lines([_RSEL_strip(str(x)) for x in lines])
    except Exception:  # noqa: BLE001
        pass
    # 실패해도 태그는 보장한다 — 원본을 그대로 쓰더라도 문서 안에서 모양이 섞이면 안 된다.
    return [_RSEL_strip(c) for c in changes]


def _fmt_secpatch(sp: str) -> str:
    """보안패치 날짜 → 'Month D, YYYY'. 파싱 실패 시 원문.

    **날짜에 0 을 채우지 않는다** — 발행된 RN 이 'April 5, 2020' 이지 'April 05, 2020'
    이 아니다(실측 PM45 45.81 문서). %d 로 찍으면 05 가 되므로 직접 넣는다.
    """
    sp = (sp or "").strip()
    if not sp:
        return ""
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%b %d %Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            d = datetime.strptime(sp, fmt)
            return f"{d.strftime('%B')} {d.day}, {d.year}"
        except ValueError:
            continue
    return sp


def _fmt_secpatch_iso(sp: str) -> str:
    """보안패치 날짜 → 'YYYY-MM-DD'. 파싱 실패 시 원문.

    **Revision History 줄 전용**이다. 발행된 RN 이 두 자리를 다르게 쓴다(실측 PM86
    86.53): 줄은 'Updated security patches to 2026-02-05.', 문서의
    '8. Android security patch level' 값은 'May 5, 2026'.
    """
    sp = (sp or "").strip()
    if not sp:
        return ""
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%b %d %Y", "%b %d, %Y", "%B %d, %Y",
                "%B %d %Y"):
        try:
            return datetime.strptime(sp, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return sp


def secpatch_line(sp: str) -> str:
    """Revision History 에 실을 보안패치 한 줄. 값이 없으면 빈 문자열."""
    iso = _fmt_secpatch_iso(sp)
    return f"Updated security patches to {iso}." if iso else ""



def _fmt_build_date(s: str) -> str:
    """빌드 날짜(YYYYMMDD·ISO·이미 'MMM DD YYYY') → 'MMM DD YYYY'.

    ISO 도 받는다 — 출시 이력(Service Portal·Confluence)이 주는 날짜가 ISO 라, 배포 공지가
    없는 회차에서는 머리글이 '95.52 (2026-06-26)' 처럼 배포본과 다른 꼴로 나갔다.

    **머리글 꼴('Jun 19, 2026')도 받는다** — 쉼표가 있다. 이 자리에는 rn_doc_date 가
    그대로 들어올 때가 있는데(버전이 바뀌어 version_full 을 다시 만들 때), 예전에는 못 읽고
    통째로 흘려보내 Description 머리가 '86.07C2 (Jun 19, 2026)' 로 나갔다. 배포본은 모두
    쉼표가 없다(실측 job-24, 2026-08-31).
    """
    s = (s or "").strip()
    if not s:
        return s
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d",
                "%b %d, %Y", "%b %d %Y", "%B %d, %Y", "%B %d %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%b %d %Y")
        except ValueError:
            continue
    return s


def is_release_version(ver: str) -> bool:
    """**그 회차의 정식 빌드** 표기인가 — 접미가 없거나 C 리비전.

    A/B/S/RC 는 비배포 빌드다(개발 RN 수집 기준 dev_rn.Version.tracked 와 같다).
    버전 표기에 붙는 **빌드 날짜는 정식 빌드의 것이어야 한다** — 베타 공지 날짜를 쓰면
    베타가 나올 때마다 빌드명이 바뀐다(실측 2026-09-21 MF52G15.00: B20 Aug 21 →
    B25 Sep 02 으로 흔들렸고, 정식 빌드는 2026.09.13 이었다).
    """
    pv = _parsed_ver(ver)
    sfx = (pv.suffix if pv else "").upper()
    return (not sfx) or sfx.startswith("C")



def _version_full(version: str, date: str = "") -> str:
    """Revision History Description 머리 — '95.52 (Jul 20 2026)'.

    날짜가 없다고 버전만 찍으면 배포본과 형식이 달라진다(실측 배포본은 모두 괄호 날짜 포함).
    머리글 Date 는 이미 오늘로 폴백하고 있으므로 여기도 같은 기준으로 채운다 — 아직 출시 전
    버전이면 '문서를 만든 날'이 들어가고, 출시일이 정해지면 재생성 때 그 날짜로 바뀐다.
    """
    v = (version or "").strip()
    if not v:
        return ""
    d = _fmt_build_date(date) or datetime.now().strftime("%b %d %Y")
    return f"{v} ({d})"


def _keep_version_full(stored: str, version: str, date: str = "") -> str:
    """저장된 version_full 을 쓰되, 날짜가 빠졌거나 **버전이 달라졌으면** 다시 만든다.

    이 폴백이 생기기 전에 만들어진 작업은 version_full 이 '12.00' 처럼 버전만 들어 있다.
    그대로 재사용하면 재생성해도 영영 날짜 없이 나간다.

    버전 비교도 반드시 해야 한다 — 공식 버전이 95.07 → 95.07C1 로 확정돼도 저장된
    '95.07 (Jul 10 2026)' 이 그대로 살아남아 **문서에 옛 버전이 찍혔다**(실측 2026-08-04).
    """
    s = (stored or "").strip()
    if "(" not in s:
        return _version_full(version, date)
    v = (version or "").strip()
    if v and _norm_ver(s.split("(", 1)[0]) != _norm_ver(v):
        return _version_full(version, date)
    # 날짜가 달라도 다시 만든다 — 이 자리 날짜는 **OS 빌드 이름의 날짜**이고, 그것이
    # 정본이다. 버전만 보고 지키면 한 번 잘못 박힌 날짜가 재생성해도 영영 남는다
    # (실측 2026-09-09 PM67 67.09 — 반려본 날짜 Jul 03 이 그대로 살아 있었다).
    want = _fmt_build_date(date)
    if want and s.split("(", 1)[1].rstrip(")").strip() != want:
        return _version_full(version, date)
    return s


def _fmt_doc_date(s: str) -> str:
    """출시/빌드 날짜 문자열 → 머리글/Update 칸용 'MMM DD, YYYY'(예: Jun 26, 2026).

    RN 머리글 Date 와 Revision History Update 는 **출시 날짜**여야 한다(RN 만든 오늘이 아님).
    파싱 실패/빈값이면 오늘로 폴백(문서가 날짜 없이 나가지 않게)."""
    s = (s or "").strip()
    if s:
        for fmt in ("%b %d %Y", "%b %d, %Y", "%B %d, %Y", "%B %d %Y",
                    "%Y-%m-%d", "%Y%m%d", "%Y.%m.%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(s, fmt).strftime("%b %d, %Y")
            except ValueError:
                continue
    return datetime.now().strftime("%b %d, %Y")


def _draft_to_lines(text: str) -> list[str]:
    """Revision History 초안 텍스트 → 줄 목록(**글머리 기호 완전 제거**).

    예전에는 `lstrip("-•*")` 로 앞의 기호를 한 번만 걷어서, '- - 문구' 처럼 기호가
    두 번 붙으면 하나가 남았다. 그 상태로 저장되면 문서 목록의 기호와 겹쳐
    '- - 문구' 로 찍힌다(실측). 앞쪽 기호·공백을 통째로 없앤다.
    """
    out: list[str] = []
    for ln in (text or "").splitlines():
        t = re.sub(r"^[\s\-•*·]+", "", ln).strip()
        if t:
            out.append(t)
    return out


# 템플릿에 **전용 자리**가 있는 항목 = 표준 항목 전부(2026-08-04 표준화).
# 표준 항목은 문서에서 번호가 고정이라 extra 로 내려가면 안 된다. 표준에 없는 항목만
# extra_components 반복 블록으로 **표준 항목들 뒤에** 붙는다(그래야 1~12번이 안 흔들린다).
def _tpl_keys() -> set[str]:
    from app.pipeline.rn_lookup import STANDARD_KEYS

    return set(STANDARD_KEYS)


_TPL_KEYS = _tpl_keys()


def _same_component_value(a, b) -> bool:
    """두 값이 '같은 값'인가 — 줄 양끝 공백·빈 줄 차이는 무시한다."""
    def _n(x) -> str:
        return "\n".join(ln.strip() for ln in str(x or "").splitlines() if ln.strip())
    return _n(a) == _n(b)


def seed_component_versions(mail_id, pdf_path: str) -> dict:
    """참고 RN 에서 항목별 **판 + 그때 값**을 읽어 기준선으로 둔다.

    문서의 각 섹션은 `◼ 판` + `✓ 값…` 이라, 값을 물려받을 때 판도 함께 물려받아야
    '언제 정해진 값인지'가 맞는다(사용자 확정 2026-09-03). 기준선은 뒤에 오는
    stamp_component_versions 가 '값이 그대로인가'를 판단하는 잣대가 된다.

    읽어 온 문서가 곧 기준이므로 **덮어쓴다**. 사람이 이미 고쳐 둔 값이 있으면 기준선과
    달라지고, 그건 stamp 가 이번 판으로 올려 준다.
    """
    from app.pipeline import rn_lookup as RNL
    from app.pipeline.state import get_state, update_state

    try:
        vers = RNL.extract_section_versions(pdf_path) or {}
        vals = {f["key"]: f.get("value", "")
                for f in (RNL.extract_sections(pdf_path) or [])}
    except Exception:  # noqa: BLE001
        return {}
    base = {k: {"version": v, "value": str(vals.get(k, "") or "").strip()}
            for k, v in vers.items() if v and k != "known_issues"}
    if base and mail_id is not None:
        cur = dict((get_state(mail_id) or {}).get("rn_component_vers") or {})
        cur.update(base)
        update_state(mail_id, rn_component_vers=cur)
    return base


def component_version(base: dict, key: str, value, version_full: str) -> dict:
    """이 값이 달고 나갈 판 — {"version", "value"}.

    기준선과 값이 같으면 그때 판, 다르면 이번 판. 화면(입력 칸 옆 표시)과 문서 생성이
    **같은 함수**를 써야 보이는 것과 나가는 것이 어긋나지 않는다.
    """
    old = (base or {}).get(key) or {}
    if old.get("version") and _same_component_value(old.get("value"), value):
        # **같은 회차면 우리 표기를 쓴다.** 판단은 _same_release 하나로 한다 —
        # Revision History 가 쓰는 기준과 같아야 문서 안에서 표기가 갈라지지 않는다.
        #   · C 리비전은 민짜 판을 대체한다. 예전에는 글자 그대로 비교해서
        #     'MF52EG15.00C1' ≠ 'MF52EG15.00' 으로 보고, Revision History 에는
        #     없는 판(MF52EG15.00)이 아래 항목들에만 남았다(사용자 지적 2026-09-17).
        #   · 참고본이 이번 회차 자신인 경우(파일서버에 이미 이번 버전 RN 이 있던 경우)도
        #     같은 자리에서 걸린다 — 그 문서의 날짜가 우리가 아는 출시일과 달라
        #     ◼ 만 옛 날짜로 남았다(실측 job-18: 머리는 '95.52 (Jul 20 2026)' 인데
        #     ◼ 만 'Jul 18 2026').
        #   · Beta(B판)는 회차가 다르다 — _same_release 가 False 라 옛 표기가 남는다.
        if _same_release(str(old["version"]).split(" (")[0],
                         str(version_full).split(" (")[0]):
            return {"version": version_full, "value": old.get("value", "")}
        return {"version": old["version"], "value": old.get("value", "")}
    return {"version": version_full, "value": str(value or "").strip()}



def stamp_component_versions(mail_id, version_full: str, merged: dict,
                             security_patch: str = "") -> dict:
    """항목마다 **그 값이 정해진 판**을 매긴다. 반환 {키: 판 표시}.

    규칙은 하나다 — 값이 기준선과 같으면 그때 판을 그대로 쓰고, 달라졌으면 이번 판으로
    올린다. 사람이 입력 칸에서 고쳤든 OS 파일에서 자동으로 채워졌든 구분하지 않는다.
    판단이 값 하나에만 걸려 있어야 재생성할 때마다 같은 답이 나온다.

    판을 올린 뒤에는 기준선도 그 값으로 바꿔 둔다. 그렇지 않으면 다음 재생성에서 또
    '달라졌다'고 보고 판을 계속 올린다.
    """
    from app.pipeline.state import get_state, update_state

    st = (get_state(mail_id) or {}) if mail_id is not None else {}
    base = dict(st.get("rn_component_vers") or {})
    cur = dict(merged or {})
    cur["security_patch"] = (security_patch
                             or cur.get("security_patch")
                             or st.get("security_patch", ""))

    out: dict = {}
    for key, val in cur.items():
        if key == "known_issues" or not str(val or "").strip():
            continue
        out[key] = component_version(base, key, val, version_full)
    if mail_id is not None and out != base:
        update_state(mail_id, rn_component_vers=out)
    return {k: v["version"] for k, v in out.items()}


def extra_components(st: dict) -> list[dict]:
    """템플릿 전용 자리가 없는 항목 → [{"title", "value"}] (RN 에 적힌 순서 그대로).

    제품마다 섹션 구성이 달라(MX450 은 Camera tuning·Scanner 까지 10번) 고정 자리로는
    담을 수 없다. ②가 RN 에서 읽어 둔 rn_input_fields 순서를 따르고, 그 목록이 없으면
    rn_components 에 남은 미지 키를 살린다(값을 문서에서 빠뜨리지 않도록).
    """
    comps = (st or {}).get("rn_components") or {}
    fields = (st or {}).get("rn_input_fields") or []
    out: list[dict] = []
    seen: set[str] = set()
    for f in fields:
        key = str(f.get("key") or "")
        if not key or key in _TPL_KEYS or key in seen:
            continue
        seen.add(key)
        title = re.sub(r"^\s*\d{1,2}\.\s*", "", str(f.get("label") or key)).strip()
        out.append({"title": title, "key": key,
                    "value": comps.get(key, "") or f.get("value", "")})
    if not fields:
        for key, val in comps.items():
            if key in _TPL_KEYS or key in seen:
                continue
            seen.add(key)
            out.append({"title": key.replace("_", " ").strip().title(),
                        "key": key, "value": val})
    # 값이 여러 개인 항목(예: Scanner = API/Decoder/ScanDriver)은 **줄마다 하나씩** 찍는다.
    # 줄바꿈으로 저장돼 있고, 옛 데이터는 ' / ' 로 이어져 있어 그것도 펼친다.
    # 키 이름은 'lines' — 'values'/'items' 는 dict 의 메서드라 템플릿(Jinja2)에서
    # c.values 가 메서드로 잡혀 렌더가 깨진다.
    _vers = (st or {}).get("rn_component_vers") or {}
    for d in out:
        raw = str(d.get("value") or "")
        parts = [p.strip() for chunk in raw.splitlines() for p in chunk.split(" / ")]
        d["lines"] = [p for p in parts if p]
        # 표준 섹션과 같은 규칙 — 값이 정해진 판을 문서에 찍는다. 모르면 빈 값을 두고,
        # 템플릿 쪽에서 이번 판으로 메운다.
        d["ver"] = str((_vers.get(d.get("key") or "") or {}).get("version") or "")
    return [d for d in out if d.get("lines")]


def merge_components(st: dict, fetched: dict, *, refresh: bool,
                     manual_now: dict | None = None) -> tuple[dict, dict, list[str]]:
    """컴포넌트 값 병합 — **사람이 고친 값만 지키고, 자동으로 채운 값은 갱신한다**.

    예전에는 저장된 값이 무조건 이겼다(`{**새값, **저장값}`). 그래서 첫 실행이 직전 버전에서
    물려받은 값이, 나중에 **이번 버전 문서를 찾은 뒤에도** 그대로 남았다 — 실측 job-23:
    파일서버의 PM84PG15.03 문서에 GMS 15_202605 · 보안패치 July 5 2026 이 있는데도 상태는
    직전 15.01 의 15_202512 · Aug 5 2025 를 유지했고, Revision History 에는 '15_202605 로
    올렸다'고 적혀 문서가 스스로 모순됐다.

    자동/수동은 `rn_component_src` 에 남긴다(화면 저장 때 mark_manual_components 가 찍는다).

    refresh: 이번 값이 **이번 버전 문서**에서 온 것인가.
      · True (기존 RN 승계) — 자동으로 채운 값을 덮어쓴다. 이 문서가 이 버전의 정본이다.
      · False (직전 버전 RN) — 빈 칸만 채운다. 직전 값은 이번 버전의 추정치일 뿐이다.
    manual_now: 실행 전 입력값(사람이 준 것) — 항상 수동으로 표시한다.

    반환 (merged, src, changed_notes).
    """
    prev = (st or {}).get("rn_components") or {}
    src = dict((st or {}).get("rn_component_src") or {})
    out = dict(prev)
    notes: list[str] = []
    for k, v in (fetched or {}).items():
        v = str(v or "").strip()
        if not v:
            continue                       # 빈 값으로 기존 값을 지우지 않는다
        cur = str(out.get(k, "") or "").strip()
        if src.get(k) == "manual" and cur:
            continue                       # 사람이 고친 값은 건드리지 않는다
        if not cur:
            out[k], src[k] = v, "auto"
        elif refresh and cur != v:
            notes.append(f"{k}: {cur} → {v}")
            out[k], src[k] = v, "auto"
    for k, v in (manual_now or {}).items():
        out[k], src[k] = v, "manual"
    return out, src, notes


def merge_security_patch(st: dict, fetched: str, *, refresh: bool) -> tuple[str, str, str]:
    """보안패치 병합. 규칙은 merge_components 와 같다. 반환 (값, 출처, 변경메모)."""
    cur = str((st or {}).get("security_patch", "") or "").strip()
    src = str((st or {}).get("security_patch_src", "") or "")
    f = str(fetched or "").strip()
    if not f:
        return cur, src, ""
    if src == "manual" and cur:
        return cur, src, ""
    if not cur:
        return f, "auto", ""
    if refresh and cur != f:
        return f, "auto", f"보안패치: {cur} → {f}"
    return cur, src or "auto", ""


def apply_os_props(user_id, product: str, android: str, version: str,
                   merged: dict, security_patch: str,
                   mail_id=None) -> tuple[dict, str, list[str]]:
    """이번 회차 **Beta OS 에서 읽은 값으로 덮어쓴다**. 반환 (merged, 보안패치, 메모).

    보안패치·SDK·GMS 세 항목만 다룬다(os_props 참고 — 나머지는 모델마다 자리가 달라
    규칙이 서지 않는다). **OS 값이 언제나 이긴다**(사용자 확정 2026-09-01): 이 자리는
    사람이 입력 칸을 만지기 **전**이라 지켜야 할 수동 입력이 아직 없고, OS 파일이 이
    회차의 정본이기 때문이다.

    실측으로 필요성이 확인된 규칙이다 — job-15(PM45 45.81)은 직전 RN 에서 물려받은
    보안패치 'April 5, 2020' 이 그대로 문서로 나갔는데 실제 OS 는 2026-06-05 였다.

    OS 를 못 읽으면 있던 값을 그대로 두고 메모만 남긴다(제작은 계속된다).
    """
    from app.pipeline import os_props as _OP

    notes: list[str] = []
    merged = dict(merged or {})
    sec = str(security_patch or "")
    try:
        res = _OP.props_for(user_id, product, android, version)
    except Exception as e:  # noqa: BLE001
        return merged, sec, [f"OS 값 읽기 실패: {e}"]
    vals = res.get("values") or {}
    if res.get("error") and not vals:
        return merged, sec, [f"OS 값 없음: {res['error']}"]

    got = []
    for key in _OP.FILL_KEYS:
        raw = str(vals.get(key) or "").strip()
        if not raw:
            continue
        if key == "security_patch":
            new = _fmt_secpatch(raw)      # '2026-06-05' → 'June 5, 2026'
            if new != sec:
                notes.append(f"보안패치: {sec or '(없음)'} → {new}")
            sec = new
        else:
            new = raw
            if merged.get(key, "") != new:
                notes.append(f"{key}: {merged.get(key) or '(없음)'} → {new}")
            merged[key] = new
        got.append(f"{key}={new}")
    # OS 빌드 이름의 날짜를 **그대로** 남긴다 — RN 머리 '67.09 (Jul 08 2026)' 이 이 값이다.
    # 예전에는 이 자리에서 폴더를 열어 놓고도 날짜를 버려, 출시이력에서 온 날짜가 쓰였다.
    # 실측 2026-09-09: PM67 67.09 가 반려본 날짜(Jul 03)로 나갔다(정본은 Jul 08).
    _bd = str(res.get("build_date") or "")
    if _bd and mail_id is not None:
        from app.pipeline.state import get_state as _gs, update_state as _us

        if str((_gs(mail_id) or {}).get("os_build_date") or "") != _bd:
            _us(mail_id, os_build_date=_bd)
            notes.append(f"OS 빌드 날짜: {_bd} ({res.get('folder', '')})")
    if got:
        notes.append(f"OS 에서 읽음({res.get('folder', '')}): {', '.join(got)}")
    return merged, sec, notes


def mark_manual_components(mail_id, comp_vals: dict, top_vals: dict | None = None) -> None:
    """사람이 **실제로 값을 바꾼** 항목만 '수동'으로 표시한다(화면 저장 직전에 부른다).

    저장 버튼만 눌러도 전부 수동이 되면 예전과 같아져(자동 갱신이 영영 막힌다), 저장된
    값과 **달라진 항목만** 찍는다. 표시가 붙은 값은 ②를 다시 돌려도 덮이지 않는다.
    """
    from app.pipeline.state import get_state, update_state

    st = get_state(mail_id) or {}
    prev = st.get("rn_components") or {}
    src = dict(st.get("rn_component_src") or {})
    fields, changed = {}, False
    for k, v in (comp_vals or {}).items():
        if str(v or "") != str(prev.get(k, "") or ""):
            src[k], changed = "manual", True
    for k, v in (top_vals or {}).items():
        if str(v or "") != str(st.get(k, "") or ""):
            fields[f"{k}_src"], changed = "manual", True
    if changed:
        update_state(mail_id, rn_component_src=src, **fields)


def _build_rn_ctx(product, version_full, rev_num, revision_lines, security_patch, merged,
                  release_date="", extra: list | None = None, android: str = "",
                  vers: dict | None = None):
    """RN 템플릿 컨텍스트. Description 머리(version)·컴포넌트 ◼ 라벨 모두 version_full 사용.

    파일서버 실제 배포본 대조(2026-07-23): 최근 RN 4건(PM95 A15 95.51 · PM67 A13 67.50 ·
    MX450 450.50 · PM68 68.04)이 모두 아래 규칙이라 이쪽을 따른다.
      · 머리글 Date / Revision History Update 칸 = 'Jul 24, 2025' (MMM D, YYYY)
      · Revision History Description 머리 = '67.50 (Jul 01 2025)' (버전 + 날짜)
    (PM95 A13 95.04·95.06 만 'MM/DD/YYYY' + 버전만 쓰는 예외였다 — 소수라 따르지 않는다.)
    날짜는 RN 을 만든 오늘이 아니라 그 버전의 **출시 날짜**(release_date)를 쓴다."""
    # 머리글 Date · Revision History Update 칸 = **문서를 최종 업데이트한 날짜**
    # (사용자 확정 2026-08-04). 예전에는 그 버전의 출시 날짜를 썼는데, 모델마다
    # 빌드 날짜본과 작성 날짜본이 섞여 기준이 흔들렸다. 문서를 만들거나 다시 만든
    # 오늘로 일원화한다. Description 머리의 '버전 (날짜)' 는 출시 날짜 그대로 둔다.
    doc_date = _fmt_doc_date(datetime.now().strftime("%Y-%m-%d"))
    # 세대는 **제품 관리에 등록돼 있다** — 상태에 없다고 '모른다'로 두지 않는다.
    # version_full 은 '95.52 (Jul 18 2026)' 꼴이라 버전 부분만 넘긴다.
    from app import os_products as _OP0

    android = _OP0.android_for_version(
        product, str(version_full or "").split(" (")[0], android)

    def _mv(key: str, override=None) -> str:
        """컴포넌트 값 — **양끝 공백을 떼서** 넘긴다.

        템플릿은 값이 있으면 섹션을, 없으면 제목까지 통째로 빼는데, Jinja 는 공백만 든
        문자열(' ')을 **참**으로 본다. 이전 RN 에서 물려받은 값에 공백이 섞여 들어오면
        빈 섹션이 제목만 달고 남는다 — 여기서 한 번에 막는다.
        """
        raw = override if override is not None else merged.get(key, "")
        return str(raw or "").strip()

    def _mvl(key: str, override=None) -> list[str]:
        """컴포넌트 값 → **줄 목록**. 문서에서 줄마다 글머리 하나씩 찍힌다.

        글머리는 Word 목록 서식이라 글자가 아니다. 한 문단에 줄바꿈을 넣으면 글머리는
        하나뿐이고 둘째 줄부터는 글머리 없이 들여쓰기만 된다 — 값이 여러 개인 항목은
        값마다 글머리가 붙어야 한다(사용자 확정 2026-09-01). 그래서 템플릿이 이 목록을
        돌며 문단을 반복한다.

        옛 저장분은 값을 ' / ' 로 이어 두었으므로 그것도 펼친다(extra_components 와 같다).
        """
        parts = [p.strip() for chunk in _mv(key, override).splitlines()
                 for p in chunk.split(" / ")]
        return [p for p in parts if p]

    # 템플릿이 `{%p for v in <키>_lines %}` 로 도는 항목들. 값이 하나뿐이면 글머리도
    # 하나라 예전 문서와 똑같이 나온다. Known Issues 는 글머리 문단이 아니라 뺀다.
    _line_keys = ("gms_package", "sdk", "audio", "touch",
                  "wifi_driver", "wifi_firmware", "wifi_config",
                  "camera_tuning", "scanner", "msr_firmware",
                  "bluetooth_profiles")
    _lines_ctx = {f"{k}_lines": _mvl(k) for k in _line_keys}
    _lines_ctx["security_patch_lines"] = _mvl("security_patch", security_patch)

    # 섹션마다 **그 값이 정해진 판**(문서의 ◼ 줄). 값이 이번에 바뀌지 않았으면 옛 판이
    # 그대로 찍힌다(사용자 확정 2026-09-03). 모르는 항목만 이번 판으로 메운다.
    _vers = dict(vers or {})

    def _ver(key: str) -> str:
        return str(_vers.get(key) or "").strip() or version_full

    _ver_ctx = {f"{k}_ver": _ver(k) for k in (*_line_keys, "security_patch")}
    # Wi-Fi 는 세 값이 한 섹션에 들어가므로 판도 하나다 — 셋 중 아는 것을 쓴다.
    _ver_ctx["wifi_ver"] = next(
        (str(_vers[k]) for k in ("wifi_driver", "wifi_firmware", "wifi_config")
         if _vers.get(k)), version_full)
    # 직접 추가한 항목은 자기 판을 들고 온다(extra_components 가 붙여 둔다).
    _extra = [dict(c) for c in (extra or [])]
    for c in _extra:
        c["ver"] = str(c.get("ver") or "").strip() or version_full

    return {
        **_lines_ctx,
        **_ver_ctx,
        # 표지 제목 · 쪽 머리말 · 머리말 표의 Model 칸이 모두 이 값을 쓴다.
        # 'PM84P Android' 로만 두면 어느 세대 문서인지 표지에서 알 수 없다 —
        # Android 버전까지 적는다(사용자 확정 2026-08-13).
        "product": f"{product} Android {android}".strip(),
        "version": version_full,        # Description 머리 = "68.04 (Jun 26 2026)"
        "version_full": version_full,   # 컴포넌트 ◼ 라벨
        "rev": f"V{rev_num}",
        "doc_date": doc_date,           # 머리글 Date = 최종 업데이트 날짜
        "rev_date": doc_date,           # Update 칸: 최종 업데이트 날짜(MMM DD, YYYY)
        # 두 문서(전체 이력본·RN for Sales)가 **모두** 이 자리를 지난다. 태그를 여기서
        # 떼면 어느 쪽으로 만들어도, 상태에 태그가 남아 있어도 문서에는 실리지 않는다.
        "changes": [_RSEL_strip(str(x)) for x in (revision_lines or [])],
        "security_patch": _mv("security_patch", security_patch),
        # Known Issues 는 **비어도 문서에 자리를 지킨다**(사용자 확정 2026-08-26) —
        # 템플릿에서 이 항목만 제목이 조건문 밖에 있다.
        "known_issues": _mv("known_issues"),
        "gms_package": _mv("gms_package"),
        "sdk": _mv("sdk"),
        "audio": _mv("audio"),
        "touch": _mv("touch"),
        "wifi_driver": _mv("wifi_driver"),
        "wifi_firmware": _mv("wifi_firmware"),
        "wifi_config": _mv("wifi_config"),
        # Wi-Fi 는 한 섹션에 값이 셋이라 '하나라도 있는가' 로 판단한다. 템플릿이 이 값으로
        # 섹션을 **제목까지 통째로** 넣거나 뺀다.
        "has_wifi": bool(_mv("wifi_driver") or _mv("wifi_firmware") or _mv("wifi_config")),
        # 표준 항목(2026-08-04). 값이 없으면 섹션이 통째로 빠지고 뒷 번호가 당겨진다
        # (번호는 Word 자동 번호라 문단이 빠지면 저절로 다시 매겨진다).
        "camera_tuning": _mv("camera_tuning"),
        "scanner": _mv("scanner"),
        "msr_firmware": _mv("msr_firmware"),
        "bluetooth_profiles": _mv("bluetooth_profiles"),
        # 표준에 없는 항목만 여기로. 표준 섹션 **뒤에** 붙는다.
        "extra_components": _extra,
    }


def current_public_changes(st: dict) -> list[str]:
    """② 편집 반영본(rn_revision_lines)에서 **공개 변경 항목만** 반환.

    보안패치·뭉침 줄(선별표의 **시스템 항목**)은 제외한다 — ⑥ KB·⑦ Newsletter 가 그 둘은
    따로 붙이므로 여기서 또 주면 두 번 실린다. ②의 재작업(rework)·수작업(regenerate)
    편집이 rn_revision_lines 에 반영되므로 downstream 이 항상 최신 선별 내용을 쓴다.
    revision_lines 가 없으면 rn_public_changes(원본 선별) 로 폴백.

    판정은 문구 모양(_SECPATCH_RE · 일반 문구)과 **시스템 항목의 실제 문구** 둘 다로 한다.
    예전에는 `startswith("Updated security patches to")` 로만 봤는데, 태그가 상시로 붙은
    뒤(2026-08-19) 실제 줄은 '[Security] Updated …' 라 **한 번도 걸러지지 않았다**.
    사람이 시스템 항목 문구를 고쳐 쓰면 모양만으로는 알 수 없으므로 표의 값도 함께 본다.
    """
    from app.pipeline import rn_select

    rev = st.get("rn_revision_lines") or []
    if not rev:
        return st.get("rn_public_changes") or []
    sysline = {_norm_line(r.get("apply_note"))
               for r in rn_select.system_rows(st.get("rn_select_rows") or []).values()
               if str(r.get("apply_note") or "").strip()}
    return [
        ln for ln in rev
        if not _SECPATCH_RE.match(ln)
        and not _is_generic_line(ln)
        and _norm_line(ln) not in sysline
    ]


def _norm_line(s: str) -> str:
    """문구 비교용 정규화 — 대소문자·구두점·공백 차이를 무시한다."""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def with_system_rows(st: dict, rows=None) -> list[dict]:
    """선별표 + **시스템 항목 두 줄**(보안패치 · 뭉침). 화면과 재생성이 같은 목록을 본다.

    보안패치 문구는 [입력 요청]의 값에서 만든다 — 값이 없으면 그 줄 자체를 두지 않는다
    (할 말이 없다). 최초 출시는 '1st official release' 한 줄로 끝나는 회차라 붙이지 않는다.

    **다만 C 리비전은 그 예외에서 뺀다**(사용자 확정 2026-09-07). C 판은 최초 출시본을
    고쳐 다시 낸 회차라 실제로 바뀐 내용이 있다. 실측 job-29(MF52EG15.00C1): 표는
    '1st official release' 한 줄뿐인데 문서에는 'Updated security patches to July 5,
    2026.' 이 붙어 나갔다 — 표에 없는 줄이라 **빼고 싶어도 뺄 수가 없었다**.
    """
    from app.pipeline import rn_select

    src = rows if rows is not None else ((st or {}).get("rn_select_rows") or [])
    if not src:
        return list(src or [])
    _ver = str((st or {}).get("version_full") or (st or {}).get("release_version")
               or (st or {}).get("os_version") or "")
    _pv = _parsed_ver(_ver.split(" (")[0])
    _is_c = bool(_pv and (_pv.suffix or "").upper().startswith("C"))
    _real = [r for r in src if r.get("kind") not in rn_select.SYSTEM_KINDS]
    if (not _is_c) and len(_real) == 1 and _norm_line(
            _real[0].get("apply_note")) == _norm_line(rn_select.FIRST_RELEASE_LINE):
        return list(src)
    sec = str((st or {}).get("security_patch", "") or "")
    line = secpatch_line(sec)
    return rn_select.ensure_system_rows(src, security_line=line)


# '여러 항목을 하나로 뭉치는 일반 문구' 판정은 rn_select 에 한 벌만 둔다 — 선별표의
# 불변식(기본 반영·수정 내용 채움)과 같은 규칙을 쓰므로 두 곳에 두면 또 어긋난다.
from app.pipeline.rn_select import is_generic_line as _is_generic_line
from app.pipeline.rn_select import strip_tag as _RSEL_strip  # noqa: E402


def _is_first_release_for(product: str, android: str, version: str) -> bool:
    """이 회차가 그 제품의 **최초 출시**인가 — 제품관리의 '최초 OS 버전'으로 판단한다.

    파이프라인 상태의 rn_first_release 는 ②의 실행 이력(참고 RN 을 찾았는지)에 따라
    켜졌다 꺼졌다 해서 기준으로 삼을 수 없다. 등록값은 사람이 정해 둔 사실이라 흔들리지
    않는다(excel_docs 의 baseline 판단도 같은 근거를 쓴다).
    """
    from app.os_products import find_sku
    from app.os_update_jobs import is_first_release

    try:
        row = find_sku(product, android) or {}
    except Exception:  # noqa: BLE001  (제품 조회 실패로 RN 제작을 막지는 않는다)
        return False
    return is_first_release(row.get("initial_os_version") or "", version)


def _dedup_lines(lines: list[str]) -> list[str]:
    """같은 문구 중복 제거 + 뭉뚱그린 일반 문구는 **최대 1줄만** 남긴다(순서 유지)."""
    out: list[str] = []
    seen: set = set()
    generic_kept = False
    for ln in lines or []:
        s = str(ln or "").strip()
        if not s:
            continue
        if _is_generic_line(s):
            if generic_kept:
                continue
            generic_kept = True
        k = _norm_line(s)
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out


def korean_lines(lines) -> list[str]:
    """RN 본문에 한국어가 섞인 줄.

    RN 은 항상 영어다. ②가 만들 때는 검사가 있었지만, 사람이 '수정 내용'에 직접 적는
    저장·재생성 경로에는 없어서 한국어가 그대로 문서에 실릴 수 있었다. 저장 후 경고용.
    """
    return [str(ln) for ln in (lines or []) if re.search(r"[가-힣]", str(ln))]


def rebuild_revision_from_selection(mail_id: int, *, curate: bool = False
                                    ) -> tuple[list[str], str]:
    """선별표(rn_select_rows)의 포함(Y) 항목으로 Revision History 를 **통째로** 다시 만든다.

    RN 승인 화면에서 체크를 바꾸면 문서에도 반영되어야 한다. '직전 반영분과의 차이만 적용'
    하는 방식은 기준선이 없는 첫 저장이 통째로 무시되고, 다듬어진 문구와 원본 공개 문구가
    달라 해제가 안 먹는 문제가 있었다. 그래서 **매번 선별표에서 새로 만든다**.

    · 포함(Y) 행의 **'수정 내용'(apply_note)을 그대로** 쓴다 — 표에 보이는 문구가 곧 RN 문구다.
      (반영으로 판정되면 '수정 내용'은 항상 채워져 있고, 사람이 고쳐 쓴 문구도 그대로 나간다.)
    · curate=True 를 주면 ②와 같은 큐레이션으로 문체·[모듈] 태그를 다시 맞춘다.
      **기본값은 False** — 사람이 적은 '수정 내용'을 Claude 가 다시 쓰면 표와 문서가 달라진다.
    · 보안패치 줄과 '뭉침' 줄도 **선별표의 시스템 항목**에서 온다(2026-08-26).
      코드가 문서에 직접 끼워 넣지 않는다 — 사람이 표에서 빼거나 문구를 고칠 수 있다.

    반환: (revision_lines, note). 만들지 못했으면 빈 목록 + 사유(기존 문서 유지).
    """
    from app.pipeline import rn_select
    from app.pipeline.state import get_state, update_state

    st = get_state(mail_id) or {}
    rows = st.get("rn_select_rows") or []
    if not rows:
        return [], "선별표가 없습니다."

    # 시스템 항목이 아직 없는 예전 작업도 여기서 갖춰 준다(②를 다시 돌리지 않아도 된다).
    _sec_raw = st.get("security_patch", "")
    _sec_auto = secpatch_line(_sec_raw)
    _fixed = with_system_rows(st, rows)
    if _fixed != rows:
        rows = _fixed
        update_state(mail_id, rn_select_rows=rows)

    included = [r for r in rows if r.get("include") == "Y"]

    # 포함 항목의 공개 문구가 비었거나 **일반 문구**('Improved system stability…')면,
    # 그 이슈 제목으로 **그 항목만의** 문구를 새로 만든다. 안 그러면 포함할 때마다 같은 줄이
    # 반복된다(실측: 4개 항목이 모두 같은 일반 문구로 들어감).
    # '반영 내용'(apply_note)이 있으면 그 문구를 그대로 쓰므로 public 생성이 필요 없다.
    # **한국어가 섞인 '수정 내용'도 여기서 다시 만든다** — 선별표는 빈칸을 남기지 않으려고
    # 문구가 없으면 이슈 제목으로 채우는데(rn_select.default_note), 사내 이슈 제목은
    # 한국어라 그대로 두면 고객 RN 에 한국어가 실린다.
    need = [r for r in included
            if (not str(r.get("apply_note") or "").strip()
                and (not str(r.get("public") or "").strip()
                     or _is_generic_line(r.get("public"))))
            or re.search(r"[가-힣]", str(r.get("apply_note") or ""))]
    if need:
        from app.pipeline import rn_select

        items = [{"no": str(r.get("no")), "issue": str(r.get("issue") or "")}
                 for r in need if str(r.get("issue") or "").strip()]
        if items:
            by_no = {str(r.get("no")): r for r in rows}
            made = rn_select.public_lines_for(
                anthropic.Anthropic(), st.get("product", ""), st.get("android", ""),
                items, _MODEL,
            )
            changed = False
            for k, line in made.items():
                r0 = by_no.get(k)
                if not (r0 and line) or _is_generic_line(line):
                    continue
                r0["public"] = line
                # '수정 내용'이 비었거나 한국어(=이슈 제목으로 채워진 것)면 그것도 바꾼다.
                # apply_note 가 public 보다 세므로, 여기서 안 바꾸면 한국어가 그대로 나간다.
                _cur = str(r0.get("apply_note") or "")
                if not _cur.strip() or re.search(r"[가-힣]", _cur):
                    r0["apply_note"] = line
                changed = True
            if changed:
                update_state(mail_id, rn_select_rows=rows)

    # 포함분 문구 수집 — 일반 문구는 아래에서 **한 줄만** 뭉쳐 붙이므로 여기선 제외하고,
    # 같은 문구가 여러 행에 있으면 중복 제거한다(순서 유지).
    # 시스템 항목(보안패치·뭉침)은 여기서 빼고 아래에서 제자리에 놓는다.
    lines, _seen = [], set()
    generic_included = False
    for r in [x for x in included if x.get("kind") not in rn_select.SYSTEM_KINDS]:
        # 반영 내용(메모)이 있으면 그 항목의 공개 문구를 그것으로 대체한다.
        ln = str(r.get("apply_note") or "").strip() or str(r.get("public") or "").strip()
        if not ln:
            continue
        if _is_generic_line(ln):
            generic_included = True
            continue
        k = _norm_line(ln)
        if k in _seen:
            continue
        _seen.add(k)
        # 표의 문구를 그대로 쓰되 태그만 채운다 — 판정(_is_generic_line·중복)은 원문 기준.
        lines.append(ln)

    # **시스템 항목은 세지 않는다.** 이 가드는 '이슈를 반영해 놓고 그 문구를 못 만든'
    # 경우를 막으려는 것인데, included 를 통째로 세면 보안패치·뭉침만 반영된 표에서도
    # 걸린다 — 그 둘은 아래에서 따로 놓으므로 lines 가 비는 것이 정상이다.
    # 그러면 재계산이 실패하고 호출부가 **옛 이력을 그대로 되쓴다**. 실측 job-15
    # (PM45 45.81, 2026-09-08): 이슈 3건을 전부 미반영으로 바꿨는데 문서에는 그중
    # 한 줄이 남고 반영해 둔 '뭉침' 줄은 빠졌다.
    _real_included = [x for x in included if x.get("kind") not in rn_select.SYSTEM_KINDS]
    if _real_included and not lines and not generic_included:
        # 체크는 했는데 그 항목들의 공개 문구를 끝내 만들지 못한 경우(Claude 실패 등)
        # — 기존 Revision History 를 덮어쓰지 않는다.
        # (반대로 **전부 제외**는 정상 상태다 → 아래에서 안정성 한 줄 + 보안패치로 재구성한다.)
        return [], "포함 항목에 고객 공개 문구가 없어 Revision History 를 만들지 못했습니다."

    # ② 와 같은 큐레이션(문체 통일 + [모듈] 태그). 재생성 결과가 ②가 만든 문서와
    # 같은 문체가 되도록 여기서도 태운다(실패하면 원본 문구 그대로 — _curate_changes 폴백).
    if curate and lines:
        lines = _curate_changes(anthropic.Anthropic(), st.get("product", ""),
                                st.get("release_version", "") or st.get("os_version", ""), lines)

    # ── 시스템 항목 두 줄 — **표에서 정한 대로** 놓는다 ──────────────────────
    # 자리는 그대로다(보안패치 맨 위 · 뭉침 맨 아래). 실을지 말지와 문구만 표가 정한다.
    _sys = rn_select.system_rows(rows)

    _stab = _sys.get("stability")
    if _stab and _stab.get("include") == "Y":
        _sl = str(_stab.get("apply_note") or "").strip() or rn_select.STABILITY_LINE
        if not any(_norm_line(_sl) == _norm_line(ln) for ln in lines):
            lines.append(_sl)
    elif not _stab:
        # 시스템 항목이 없는 예전 데이터 — 예전 규칙 그대로(뺀 항목이 있으면 한 줄 뭉침).
        if ((len(rows) - len(included)) > 0 or generic_included) and not any(
            "system stability" in ln.lower() for ln in lines
        ):
            lines.append(rn_select.STABILITY_LINE)

    _secr = _sys.get("security")
    # 보안패치 표기가 흔들린다 — 'Updated security patch to 2026-06-05.'(단수·ISO) 와
    # 'Updated security patches to June 05, 2026.'(복수·영문) 가 섞여 들어온다. 형태를
    # 가리지 않고 걷어낸 뒤 **한 줄만** 놓는다(실측 job-18·job-23 에서 두 줄이 실렸다).
    lines = [ln for ln in lines if not _SECPATCH_RE.match(ln)]
    if _secr:
        if _secr.get("include") == "Y":
            _sec_line = str(_secr.get("apply_note") or "").strip() or _sec_auto
            if _sec_line:
                lines = [_sec_line] + lines
    elif _sec_auto:                       # 예전 데이터 폴백
        lines = [_sec_auto] + lines

    update_state(mail_id, rn_revision_lines=lines,
                 rn_revision_draft="\n".join(f"- {ln}" for ln in lines))
    return lines, ""


# ── 전체 이력 RN ───────────────────────────────────────────────────────────
# RN for Sales = 이번 판 한 개 + 공개 선별(Y)만. 파일서버에 쌓이는 정식 RN 은 그것과 달리
# **모든 버전의 Revision History** 를 담는다(실측: PM67 67.09 문서에 V1~V24). 그래서 같은
# 시점에 전체 이력본을 하나 더 만든다. 두 문서의 차이는 **담는 버전 수**뿐이다 —
# 이번 판의 내용은 양쪽이 같고, 미반영은 어느 쪽에도 실리지 않는다(사용자 확정 2026-08-26).
# 보안패치 줄 판정은 선별표(rn_select)와 **같은 한 벌**을 쓴다 — 이제 그 줄도 선별표의
# 시스템 항목이라, 판정이 두 곳으로 갈리면 표와 문서가 어긋난다.
from app.pipeline.rn_select import SECPATCH_RE as _SECPATCH_RE  # noqa: E402
from app.integrations.claude_cli import have_auth  # noqa: E402


def full_change_lines(st: dict) -> list[str]:
    """이번 버전의 변경 문구 — **미반영은 뺀다**(RN for Sales 와 같은 내용).

    예전에는 반영·미반영을 가리지 않고 선별표 전 행을 실었다. 그러면 승인자가 선별표에서
    미반영으로 바꾸고 재생성해도 이 문서에는 그대로 남아 두 문서가 어긋났다
    (실측 job-21 PM95 95.07C1 — 미반영 2건이 전체 이력본 V2 행에 그대로 남아 있었다).
    반영/미반영은 **무엇을 남길지 정하려고** 두는 것이니 이 문서도 그대로 따른다
    (사용자 확정 2026-08-26).

    기준은 ②·재생성이 확정해 둔 **rn_revision_lines** 다. 표에서 다시 계산하지 않는 이유:
    '재작업'(사람 지시로 문구 편집)은 rn_revision_lines 만 고치므로, 표 기준으로 만들면
    그 편집이 이 문서에서만 사라진다. 두 문서가 갈라질 자리를 아예 없앤다.
    그 값이 없을 때만 선별표의 **반영(Y)** 행으로 되만든다.

    ※ 이전 버전들의 이력은 여기가 아니라 rn_prev_revisions 로 들어간다 — 그쪽도 태그는
      떼고 싣는다(build_full_rn 참조).
    """
    rows = (st or {}).get("rn_select_rows") or []
    out: list[str] = [str(x) for x in ((st or {}).get("rn_revision_lines") or [])
                      if str(x).strip()]
    from app.pipeline import rn_select

    sysrows = rn_select.system_rows(rows)
    if not out and rows:
        # 폴백 — 확정된 문구가 아직 없을 때. RN for Sales 와 같은 규칙으로 만든다
        # (반영 행만 · 중복 제거 · 뭉침 문구는 한 줄로).
        seen: set = set()
        included = [r for r in rows if r.get("include") == "Y"]
        generic_included = False
        for r in included:
            if r.get("kind") in rn_select.SYSTEM_KINDS:
                continue                      # 시스템 항목은 아래에서 제자리에 놓는다
            ln = (str(r.get("apply_note") or "").strip()
                  or str(r.get("public") or "").strip()
                  or str(r.get("issue") or "").strip())
            if not ln:
                continue
            if _is_generic_line(ln):
                generic_included = True
                continue
            k = _norm_line(ln)
            if k in seen:
                continue
            seen.add(k)
            out.append(ln)
        _stab = sysrows.get("stability")
        if _stab:
            if _stab.get("include") == "Y" and not any(
                    "system stability" in x.lower() for x in out):
                out.append(str(_stab.get("apply_note") or "").strip()
                           or rn_select.STABILITY_LINE)
        elif ((len(rows) - len(included)) > 0 or generic_included) and not any(
            "system stability" in x.lower() for x in out
        ):
            out.append(rn_select.STABILITY_LINE)

    # 보안패치 줄은 맨 위 한 줄로 통일한다. 표기가 흔들려('Updated security patch to
    # 2026-06-05.' vs 'Updated security patches to June 05, 2026.') 두 줄이 같이 실리는
    # 일이 있어(실측 job-18), 형태를 가리지 않고 걷어낸 뒤 하나만 붙인다.
    #
    # **선별표에서 미반영으로 뺐으면 붙이지 않는다**(2026-08-26). 예전에는 상태의
    # security_patch 값만 보고 무조건 앞에 끼워서, 표에서 뺀 줄이 이 문서에서만 되살아났다.
    _secr = sysrows.get("security")
    sec = (st or {}).get("security_patch", "")
    if _secr:
        out = [x for x in out if not _SECPATCH_RE.match(x)]
        if _secr.get("include") == "Y":
            _line = str(_secr.get("apply_note") or "").strip()
            if not _line and sec:
                _line = secpatch_line(sec)
            if _line:
                out = [_line] + out
    # 시스템 항목이 없으면 **붙이지 않는다**(사용자 확정 2026-09-07).
    # 예전에는 상태의 security_patch 값만 보고 무조건 끼워 넣었는데, 그러면 선별표에
    # 없는 줄이 문서에만 나가 사람이 뺄 수가 없다(실측 job-29). 표에 행이 없다는 것은
    # with_system_rows 가 아직 안 돈 옛 데이터라는 뜻이고, 다시 만들면 행이 생긴다.
    # 태그는 여기서도 뗀다. 이 함수는 **상태에 저장된 rn_revision_lines 를 그대로** 쓰는데,
    # 예전 작업에는 태그가 붙은 채로 남아 있다. 상태를 고치지 않아도 다시 만들면 사라지게
    # 한다 — 과거 판(build_full_rn)만 막고 이번 판을 안 막으면 한 표에 두 모양이 섞인다.
    return [_RSEL_strip(str(x)) for x in out]


def revision_rows_from(entries: dict) -> list[dict]:
    """extract_revision_entries_full 결과 → 템플릿용 행 목록(문서 순서 유지).

    **옛 판 문구에도 태그를 붙여 채우지 않는다**(사용자 확정 2026-08-27). 예전에는 [WLAN]
    같은 태그를 추정해 넣었는데, 그러면 과거 문서에 없던 글자가 생긴다.

    거꾸로 **읽어 온 문구에 태그가 있으면 뗀다**(사용자 확정 2026-08-31) — Revision History
    에는 태그를 넣지 않는다. 떼는 일은 입구(extract_revision_entries_full)에서 이미 끝나지만,
    예전에 태그째 저장해 둔 이력이 이 자리로 들어올 수도 있어 여기서 한 번 더 막는다.
    """
    return [
        {"rev": d.get("rev", ""), "date": d.get("date", ""),
         "version": d.get("header", "") or ver,
         "changes": [_RSEL_strip(str(x)) for x in (d.get("lines") or [])]}
        for ver, d in (entries or {}).items()
    ]


def _next_rev_no(prev: list[dict]) -> int:
    """이번 판의 V 번호 — 이전 이력의 최대 V + 1. 못 읽으면 개수 + 1."""
    nums = []
    for r in prev or []:
        m = re.match(r"[Vv](\d+)$", str(r.get("rev") or "").strip())
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) if nums else len(prev or [])) + 1


def _next_rev_label(prev: list[dict]) -> str:
    """이번 판의 V 표기 — **이전 이력의 자릿수를 따른다**.

    문서마다 표기 폭이 다르다(실측: PM45 는 V01~V06, PM67 은 V1~V24). 폭을 안 맞추면
    같은 표 안에서 'V06' 다음이 'V7' 로 찍혀 한 판만 모양이 다르다.
    """
    n = _next_rev_no(prev)
    for r in reversed(prev or []):
        m = re.match(r"[Vv](\d+)$", str(r.get("rev") or "").strip())
        if m:
            return f"V{n:0{len(m.group(1))}d}"
    return f"V{n}"


def build_full_rn(mail_id, workdir=None) -> tuple[str, str]:
    """전체 이력 RN(docx) 생성. (path, error).

    이전 이력(`rn_prev_revisions`, ②가 읽어 저장해 둔 것) + 이번 버전 한 판을 이어 붙인다.
    이전 이력에 이번 버전이 이미 있으면(파일서버에 이번 버전 RN 이 있던 경우) 그 행은 빼고
    **우리가 편집한 내용**으로 다시 넣는다 — 표에서 고친 문구가 이쪽에도 반영되어야 한다.
    """
    import os as _os

    from app.office import rn_render
    from app.pipeline.state import get_state, update_state

    st = get_state(mail_id) or {}
    product = st.get("product", "")
    version = st.get("release_version") or st.get("os_version") or ""
    if not (product and version):
        return "", "제품·버전이 없어 전체 이력 RN 을 만들지 못했습니다."

    doc_date = st.get("rn_doc_date", "") or st.get("release_date", "")
    version_full = _keep_version_full(st.get("version_full", ""), version, doc_date)
    # 예전 작업 상태에는 태그가 붙은 채로 저장된 이력이 남아 있다. 문서를 그릴 때 뗀다 —
    # 상태를 고치지 않아도 다시 만들면 태그가 사라진다(사용자 확정 2026-08-31).
    prev = [dict(r, changes=[_RSEL_strip(str(x)) for x in (r.get("changes") or [])])
            for r in (st.get("rn_prev_revisions") or [])]
    # 이번 판과 **같은 회차**인 행을 걷어낸다(그 자리에 우리가 편집한 내용을 넣는다).
    #   · C 리비전은 민짜 판을 **대체**한다 — 95.07C1 을 만들면 이력의 95.07 행은 지운다.
    #     같은 회차가 두 줄이 되면 안 된다(사용자 확정 2026-08-18, _same_release 참조).
    #   · Beta(B판)는 **각각 남긴다** — 예전에 base 로만 비교해 67.00 을 만들 때
    #     67.00 B1~B4 가 통째로 지워졌다(실측 PM67 67.09 문서에 그 판들이 따로 실려 있다).
    prev = [r for r in prev
            if not _same_release(str(r.get("version") or ""), version)]

    revisions = prev + [{
        "rev": _next_rev_label(prev),
        # 이번 판의 Update 칸 = **문서 최종 업데이트 날짜**(오늘). 이전 판들(prev)의
        # 날짜는 그때의 것이라 그대로 둔다 — 과거 이력을 오늘로 덮으면 안 된다.
        "date": _fmt_doc_date(datetime.now().strftime("%Y-%m-%d")),
        "version": version_full,
        "changes": full_change_lines(st),
    }]

    _vers = stamp_component_versions(
        mail_id, version_full, st.get("rn_components", {}) or {},
        st.get("security_patch", ""))
    st = get_state(mail_id) or st          # 판이 바뀌었으면 extra 도 새 값을 봐야 한다
    ctx = _build_rn_ctx(product, version_full, _next_rev_no(prev),
                        revisions[-1]["changes"], st.get("security_patch", ""),
                        st.get("rn_components", {}) or {},
                        release_date=doc_date, extra=extra_components(st),
                        android=str(st.get("android", "") or ""), vers=_vers)
    ctx["revisions"] = revisions
    docx_bytes, err = rn_render.render_rn_docx(ctx, template_key="rn_docx_full")
    if not docx_bytes:
        return "", err

    # RN for Sales 와 같은 폴더에 둔다. 이름은 파일서버 관례(‘for Sales’ 없는 이름).
    # 이 단계가 먼저라 Sales 문서가 아직 없을 수 있다 — 그때는 실행이 만든 작업 폴더를 쓴다.
    sales = st.get("rn_docx_path", "")
    _cur = st.get("rn_full_docx_path", "")
    if workdir is not None:
        wd = Path(workdir)
    elif sales:
        wd = Path(_os.path.dirname(sales))
    elif _cur:
        wd = Path(_os.path.dirname(_cur))     # 재생성 — 만들어 뒀던 자리를 그대로 쓴다
    else:
        wd = _WORK / f"{mail_id}_full"
    wd.mkdir(parents=True, exist_ok=True)
    path = str(wd / (RNL_STD(product, st.get("android", ""), version.replace(" ", "")) + ".docx"))
    atomic_write_bytes(path, docx_bytes)
    update_state(mail_id, rn_full_docx_path=path,
                 rn_full_docx_name=_os.path.basename(path),
                 rn_full_rev_count=len(revisions))
    return path, ""


def finalize_full_pdf(mail_id: int) -> tuple[str, str]:
    """**1차(RN 승인)** 에서 호출 — 전체 이력본 docx 를 PDF 로 굳힌다. (pdf_path, error).

    RN 승인은 'History 관리용 RN' 을 확정하는 결재다(사용자 확정 2026-08-04).
    RN for Sales 는 이 뒤에 작성자가 선별·편집해 만들고 최종 승인자 검수를 따로 받는다.
    """
    import os as _os

    from app.office import rn_render
    from app.pipeline.state import get_state, update_state

    st = get_state(mail_id) or {}
    full_docx = st.get("rn_full_docx_path", "")
    if not full_docx or not _os.path.exists(full_docx):
        return "", "전체 이력 RN docx 가 없습니다. Release Note 제작을 먼저 실행하세요."
    try:
        fbytes, ferr = rn_render.docx_to_pdf_bytes(open(full_docx, "rb").read())
    except Exception as e:  # noqa: BLE001
        fbytes, ferr = None, f"{type(e).__name__}: {e}"
    if not fbytes:
        update_state(mail_id, rn_full_pdf_error=ferr or "전체 이력 RN PDF 변환 실패")
        return "", ferr or "전체 이력 RN PDF 변환 실패"
    fpdf = _os.path.splitext(full_docx)[0] + ".pdf"
    atomic_write_bytes(fpdf, fbytes)
    update_state(mail_id, rn_full_pdf_path=fpdf,
                 rn_full_pdf_name=_os.path.basename(fpdf), rn_full_pdf_error="")
    return fpdf, ""


def finalize_rn_pdf(mail_id: int) -> tuple[str, str]:
    """**2차(최종 승인자 검수)** 에서 호출 — RN for Sales docx 를 PDF 로 굳힌다.

    반환 (pdf_path, error). 승인 전까지는 docx 로만 관리하고(작성자가 선별·편집·재생성),
    승인 시점의 docx 를 그대로 PDF 로 굳힌다.
    전체 이력본은 1차에서 이미 굳혔으므로 여기서는 건드리지 않는다.
    """
    import os as _os

    from app.office import rn_render
    from app.pipeline.state import get_state, update_state

    st = get_state(mail_id) or {}
    docx_path = st.get("rn_docx_path", "")
    if not docx_path or not _os.path.exists(docx_path):
        return "", "RN docx 가 없습니다. Release Note 제작을 먼저 실행하세요."
    try:
        docx_bytes = open(docx_path, "rb").read()
    except Exception as e:  # noqa: BLE001
        return "", f"RN docx 읽기 실패: {e}"

    pdf_bytes, err = rn_render.docx_to_pdf_bytes(docx_bytes)
    if not pdf_bytes:
        return "", err
    pdf_path = _os.path.splitext(docx_path)[0] + ".pdf"
    atomic_write_bytes(pdf_path, pdf_bytes)
    update_state(mail_id, rn_pdf_path=pdf_path, rn_pdf_name=_os.path.basename(pdf_path),
                 rn_final_pdf_path=pdf_path)
    return pdf_path, ""


def _archive_to(user_id: int, mail_id, rel_dir: str, dir_label: str,
                src_keys: tuple[tuple[str, str], ...], default_suffix: str = "") -> dict:
    """상태의 문서들을 파일서버 rel_dir 에 올린다. 전체 이력본·RN for Sales 공용.

    반환 {saved: [경로…], skipped: [사유…], dir: 경로, name: 파일명(확장자 없음)}.

    · 파일명은 **그 폴더의** 기존 관례를 따른다(rn_lookup.name_for_version).
      폴더마다 관례가 다르므로 두 저장소가 서로의 이름을 흉내내지 않게 각자 훑는다.
    · **같은 이름이 있으면 덮어쓰지 않는다** — 손대지 않고 사유만 남긴다(backend.upload).
    · 실패해도 승인 자체는 막지 않는다(호출부가 결과만 알린다).
    """
    import os as _os

    from app.pipeline import rn_lookup as RNL
    from app.pipeline.state import get_state

    st = get_state(mail_id) or {}
    out: dict = {"saved": [], "skipped": [], "dir": rel_dir, "name": ""}
    product = st.get("product", "")
    version = st.get("release_version") or st.get("os_version") or ""
    if not (product and version):
        out["skipped"].append("제품·버전이 없어 저장하지 않았습니다.")
        return out
    if not rel_dir:
        out["skipped"].append(f"이 제품에 '{dir_label}'가 등록돼 있지 않습니다.")
        return out

    # 등록 경로의 **호스트에 맞는** 백엔드를 쓴다(개발 RN 은 .104, RN for Sales 는 .160).
    b = RNL.backend_for_path(user_id, rel_dir)
    if b is None:
        out["skipped"].append("파일서버 자격증명이 없습니다.")
        return out
    try:
        existing = [f.name for f in b.list_dir(rel_dir) if not f.is_dir]
    except Exception as e:  # noqa: BLE001
        out["skipped"].append(f"등록 경로를 열지 못했습니다 — {type(e).__name__}: {e}")
        return out

    # 파일명은 **표준 규칙 고정**({제품}_Android{N}_Release_Note_{버전}, 2026-08-04 확정).
    # 폴더 관례 추종은 그만둔다 — 관례가 폴더마다 달라 같은 제품이 A13·A15 에서 다른
    # 이름으로 쌓였다. 기존 파일을 '찾는' 쪽은 두 규칙을 다 보므로 과거분도 그대로 잡힌다.
    android = str(st.get("android", "") or "")
    name = RNL.standard_name(product, android, version, suffix=default_suffix)
    out["name"] = name
    for src_key, ext in src_keys:
        src = st.get(src_key, "")
        if not (src and _os.path.exists(src)):
            out["skipped"].append(f"{ext} — 만들어진 파일이 없습니다.")
            continue
        written, err = b.upload(src, f"{rel_dir}\\{name}{ext}")
        if written:
            out["saved"].append(written)
        else:
            out["skipped"].append(f"{name}{ext} — {err}")
    return out


def archive_full_rn(user_id: int, mail_id) -> dict:
    """RN 승인 시 — 전체 이력 RN(docx + pdf)을 제품의 **개발 RN 경로**에 남긴다."""
    from app.pipeline import rn_lookup as RNL
    from app.pipeline.state import get_state, update_state

    st = get_state(mail_id) or {}
    out = _archive_to(
        user_id, mail_id,
        RNL.dev_rn_dir(st.get("product", ""), str(st.get("android", "") or "")),
        "개발 Release Note 파일 경로",
        (("rn_full_docx_path", ".docx"), ("rn_full_pdf_path", ".pdf")),
    )
    update_state(mail_id, rn_full_archived=out["saved"],
                 rn_full_archive_note="; ".join(out["skipped"]))
    return out


def archive_sales_rn(user_id: int, mail_id) -> dict:
    """RN 승인 시 — **RN for Sales**(docx + 확정 pdf)를 제품의 RN for Sales 경로에 남긴다.

    전체 이력본과 **다른 폴더**다. 같은 폴더에 두면 이름이 겹쳐 하나가 저장되지 않는다
    (backend.upload 는 덮어쓰지 않는다).
    """
    from app.pipeline import rn_lookup as RNL
    from app.pipeline.state import get_state, update_state

    st = get_state(mail_id) or {}
    out = _archive_to(
        user_id, mail_id,
        RNL.sales_rn_dir(st.get("product", ""), str(st.get("android", "") or "")),
        "RN for Sales 저장 경로",
        (("rn_docx_path", ".docx"), ("rn_pdf_path", ".pdf")),
        default_suffix=" for Sales",
    )
    update_state(mail_id, rn_sales_archived=out["saved"],
                 rn_sales_archive_note="; ".join(out["skipped"]))
    return out


def preview_pdf(mail_id: int, which: str = "sales") -> tuple[str, str]:
    """현재 RN docx 를 **화면 미리보기용 PDF** 로 변환한다. (pdf_path, error).

    확정본 PDF(finalize_rn_pdf)와 다르다 — 그쪽은 승인 시점의 정본을 굳히는 것이고,
    이건 검수 중에 문서를 화면에서 바로 보기 위한 임시본이다. docx 가 바뀌었을 때만
    다시 변환한다(수정→저장 때마다 Word 를 띄우면 느리다).

    which="full" 이면 **전체 이력 RN** 을 띄운다. 1차 승인이 확정하는 문서가 이력본이라,
    그 단계의 화면은 승인 대상 문서를 그대로 보여 준다(2026-08-27).
    """
    import os as _os

    from app.office import rn_render
    from app.pipeline.state import get_state

    st = get_state(mail_id) or {}
    _full = which == "full"
    docx_path = st.get("rn_full_docx_path" if _full else "rn_docx_path", "")
    if _full and (not docx_path or not _os.path.exists(docx_path)):
        return "", ("전체 이력 RN 이 아직 없습니다 — [전체 이력 RN 제작] 을 실행하세요."
                    if not st.get("rn_first_release") else
                    "최초 버전 — 참고할 이전 문서가 없어 확인 항목이 비어 있습니다. "
                    "값을 채우면 문서가 만들어집니다.")
    if not docx_path or not _os.path.exists(docx_path):
        # 최초 버전이면 '문서가 없다'가 아니라 **아직 값이 안 채워진** 것이다.
        # 참고할 이전 RN 이 없어 빈손으로 시작하지만, 절차는 그대로 승인이다
        # ('작성해 주세요' 는 승인자에게 문서를 떠넘기는 어감이었다 — 2026-08-19).
        if st.get("rn_first_release"):
            _ver = str(st.get("release_version") or st.get("version_override") or "").strip()
            _t = f"최초 버전({_ver})" if _ver else "최초 버전"
            return "", (f"{_t} — 참고할 이전 문서가 없어 확인 항목이 비어 있습니다. "
                        "값을 채우면 문서가 만들어집니다.")
        return "", "RN 문서가 아직 없습니다."

    pdf_path = _os.path.splitext(docx_path)[0] + "_preview.pdf"
    try:
        if (_os.path.exists(pdf_path)
                and _os.path.getmtime(pdf_path) >= _os.path.getmtime(docx_path)):
            return pdf_path, ""            # 문서가 그대로면 이전 변환본을 쓴다
    except OSError:
        pass

    try:
        docx_bytes = open(docx_path, "rb").read()
    except Exception as e:  # noqa: BLE001
        return "", f"RN 문서를 읽지 못했습니다: {e}"
    pdf_bytes, err = rn_render.docx_to_pdf_bytes(docx_bytes)
    if not pdf_bytes:
        return "", f"미리보기 변환 실패: {err}"
    atomic_write_bytes(pdf_path, pdf_bytes)
    return pdf_path, ""


def regenerate_rn(mail_id: int, revision_text: str, *,
                  with_full: bool = True, with_sales: bool = True) -> tuple[str, str]:
    """RN 문서(docx) 재생성 — 사람이 Revision History 를 편집한 뒤 다시 렌더. (path, error).

    파이프라인 상태(제품·버전·컴포넌트·보안패치·리비전)를 재사용하고, Revision History 만
    편집본으로 교체해 다시 렌더한다. Redmine/Claude 재수집은 하지 않는다(빠름).

    단계마다 **자기 문서만** 다시 만든다(2026-08-27).
      · 전체 이력 RN 제작 — with_sales=False. 이 단계의 산출물은 이력본 하나다.
      · RN for Sales 제작 — with_full=False. 이력본은 1차 승인으로 확정·저장이 끝난
        문서라 그 뒤에 건드리면 승인받은 판과 달라진다.
    돌려주는 path 는 만든 문서다(Sales 를 만들었으면 Sales, 아니면 이력본).
    """
    import os as _os

    from app.office import rn_render
    from app.pipeline.state import get_state, update_state

    st = get_state(mail_id) or {}
    product = st.get("product", "")
    version = st.get("release_version") or st.get("os_version") or ""
    rev_num = int(st.get("rn_rev", 1) or 1)
    merged = st.get("rn_components", {}) or {}
    security_patch = st.get("security_patch", "")
    # 재생성도 머리글/Update = 출시 날짜(원 실행에서 저장한 값 재사용).
    doc_date = st.get("rn_doc_date", "") or st.get("release_date", "")
    version_full = _keep_version_full(st.get("version_full", ""), version, doc_date)

    revision_lines = _draft_to_lines(revision_text)
    # 안전장치: 빈 초안으로 재생성하면 Revision History 가 통째로 날아간다.
    # 초안이 비어 있으면 **기존 항목을 그대로 유지**한다(수정·추가분만 반영되도록).
    if not revision_lines:
        revision_lines = list(st.get("rn_revision_lines") or [])
        update_state(mail_id, rn_revision_lines=revision_lines)
    else:
        update_state(mail_id, rn_revision_lines=revision_lines,
                     rn_revision_draft=revision_text)

    path = ""
    if with_sales:
        _vers = stamp_component_versions(mail_id, version_full, merged, security_patch)
        st = get_state(mail_id) or st      # 판이 바뀌었으면 extra 도 새 값을 봐야 한다
        ctx = _build_rn_ctx(product, version_full, rev_num, revision_lines, security_patch,
                            merged, release_date=doc_date, extra=extra_components(st),
                            android=str(st.get("android", "") or ""), vers=_vers)
        docx_bytes, err = rn_render.render_rn_docx(ctx)
        if not docx_bytes:
            return "", err
        _want = RNL_STD(product, st.get("android", ""),
                        version.replace(" ", ""), " for Sales") + ".docx"
        path = st.get("rn_docx_path", "")
        if path and _os.path.basename(path) != _want:
            # **버전이 바뀌면 이름도 따라간다.** 예전에는 저장된 자리를 그대로 써서,
            # 작업이 86.07C2 → 86.07C8 로 갈아탄 뒤에도 공개본이
            # '…86.07C2 for Sales.docx' 로 나왔다(실측 2026-09-21). 폴더는 그대로 둔다.
            path = str(Path(_os.path.dirname(path)) / _want)
        if not path:
            # 이력본과 **같은 폴더**에 둔다(파일서버 관례). 이력본이 아직 없으면 작업 폴더.
            _full = st.get("rn_full_docx_path", "")
            wd = Path(_os.path.dirname(_full)) if _full else (_WORK / f"{mail_id}_regen")
            wd.mkdir(parents=True, exist_ok=True)
            path = str(wd / _want)
        atomic_write_bytes(path, docx_bytes)
        # version_full 은 여기서 보정됐을 수 있으므로 함께 저장한다 — ⑤⑥⑦ 이 같은 표기를 쓴다.
        update_state(mail_id, rn_docx_path=path, rn_docx_name=_os.path.basename(path),
                     version_full=version_full)
    else:
        update_state(mail_id, version_full=version_full)
    if with_full:
        _fp, _fe = build_full_rn(mail_id)
        if not with_sales:
            if not _fp:
                return "", _fe
            path = _fp
    return path, ""


def full_stage_done(st: dict) -> tuple[bool, str]:
    """앞 단계(전체 이력 RN 제작)가 끝났는가. (끝남, 아직이면 사유).

    끝나는 길이 둘이다.
      · RN 승인(1차)을 받았다 — 이력본을 만들어 확정한 보통의 경우.
      · 만들지 않고 건너뛰었다 — 이번 버전 RN 이 파일서버에 이미 있는 경우
        (파일서버의 그 문서가 곧 전체 이력본이라 결재할 새 문서가 없다).
    """
    st = st or {}
    if st.get("rn_full_skipped"):
        return True, ""
    mid = (st.get("rn_mid_review") or {}).get("status") or "none"
    if mid == "approved":
        return True, ""
    return False, {"none": "아직 RN 승인을 요청하지 않았습니다",
                   "requested": "RN 승인자의 승인을 기다리는 중입니다",
                   "rejected": "RN 승인에서 반려됐습니다"}.get(mid, mid)


def skip_target(mail_id, user_id: int = 0) -> tuple[str, str, str]:
    """건너뛸 때 **기준으로 삼을 RN** — (무엇, 어디, 사유). 못 찾으면 ("", "", 사유).

    둘 중 앞의 것을 쓴다.
      · 파일서버에 **이미 있는** 이번 회차 RN. C 리비전은 기준 판이 곧 그 회차라
        민짜 표기로도 찾아본다(86.07C8 → 86.07).
      · 그것이 없으면 이 작업이 이미 만든 전체 이력본(rn_full_docx_path).

    **파일서버가 먼저다**(사용자 지적 2026-09-21). 사람이 만들어 올려 둔 정식 RN 이
    '이미 만들어져 있는 RN' 이다. 예전에는 작업이 만든 문서를 먼저 집어서, 내용이 빈
    옛 초안을 기준으로 삼는 일이 있었다(실측 MF52 job-26: 8월에 만든 문서가
    '1st official release' 한 줄에 컴포넌트가 전부 비어 있었다).
    """
    import os as _os

    from app.pipeline import c_revision as _CR
    from app.pipeline import rn_lookup as _RNL
    from app.pipeline.state import get_state

    st = get_state(mail_id) or {}
    product = str(st.get("product") or "")
    android = str(st.get("android") or "")
    version = str(st.get("release_version") or st.get("os_version") or "")
    why = ""
    if product and version:
        _base, _rev = _CR.split_c(version)
        for _v in ([version, _base] if _rev else [version]):
            try:
                hit = _RNL.find_existing_rn(user_id, product, android, _v)
            except Exception as e:  # noqa: BLE001  (파일서버가 막혀도 아래로 내려간다)
                why = f"파일서버를 읽지 못했습니다: {e}"
                break
            if hit:
                return hit["name"], hit.get("where", ""), ""

    have = str(st.get("rn_full_docx_path") or "")
    if have and _os.path.exists(have):
        return _os.path.basename(have), have, ""
    if not (product and version):
        return "", "", "제품·버전이 없어 기준 RN 을 찾을 수 없습니다."
    return "", "", (why or f"{product} {version} 의 RN 을 파일서버에서 찾지 못했습니다 — "
                    "건너뛰면 RN for Sales 가 쓸 내용이 없습니다.")



def skip_full_rn(mail_id, user_id: int = 0, by: str = "") -> tuple[bool, str]:
    """**[전체 이력 RN 제작] 을 건너뛴다** — 이미 만들어져 있는 RN 을 그대로 기준으로 삼는다.
    (된다, 알림글)

    이력본을 새로 만들지 않고, 다음 단계(RN for Sales 제작)가 그 RN 의 내용으로 공개본을
    만든다(사용자 요청 2026-09-21).

    두 갈래다.
      · 이 작업이 이미 리서치를 돌려 **선별표·Revision 문구가 있으면** 그대로 쓴다.
      · 아직 아무것도 없으면 **파일서버의 그 버전 RN 을 읽어** 채운다 — 사람이 만들어
        올려 둔 RN 으로 RN for Sales 만 만들고 싶은 경우다(실측 PM86 A13 86.53:
        9/21 에 사람이 올린 PM86_Android13_Release_Note_86.53.pdf).
        읽는 일은 run_rn_research(adopt_only=True) 가 한다 — 이미 있는 길이라
        같은 값을 같은 방식으로 채운다. 못 찾으면 건너뛰지 않는다.

    자동으로 건너뛰는 길(이번 버전 RN 이 파일서버에 이미 있는 경우)과 **같은 표시**를
    쓴다 — full_stage_done 이 그 표시 하나만 보므로 뒤 단계가 그대로 이어진다.
    """
    from app.pipeline import history as _H
    from app.pipeline.state import get_state, update_state

    st = get_state(mail_id) or {}
    if st.get("rn_full_skipped"):
        return True, "이미 건너뛴 단계입니다."

    def _lines(s: dict) -> list:
        return [str(x) for x in (s.get("rn_revision_lines") or []) if str(x).strip()]

    lines = _lines(st)
    read_note = ""
    if not lines:
        # 아직 아무것도 없다 — 이미 만들어져 있는 RN 을 읽어 온다.
        out = run_rn_research(user_id, "", "", mail_id, adopt_only=True)
        st = get_state(mail_id) or {}
        lines = _lines(st)
        if not lines:
            return False, (out.result_text or
                           "건너뛸 수 없습니다 — 기준으로 삼을 RN 을 찾지 못했습니다.")
        read_note = (st.get("rn_pdf_name") or st.get("rn_from_fileserver") or "")

    name, where, _why = skip_target(mail_id, user_id)
    name = read_note or name
    reason = ("사람이 건너뛴 단계입니다 — 이력본을 새로 만들지 않고 "
              + (f"이미 만들어져 있는 RN({name}) 을 " if name else "지금 상태를 ")
              + "기준으로 RN for Sales 를 만듭니다.")
    update_state(mail_id, rn_full_skipped=True, rn_full_skip_reason=reason,
                 rn_full_skipped_by=(by or ""))
    try:
        _H.record(mail_id, "rn", "건너뜀", actor=(by or ""),
                  summary=reason + (f" · 위치 {where}" if where else ""),
                  text=_H.text_from_rn(get_state(mail_id) or {}))
    except Exception:  # noqa: BLE001  (이력 남기기 실패가 건너뛰기를 막지 않는다)
        pass
    return True, (reason + f"\n이번 판에 실을 항목 {len(lines)}건은 그대로 씁니다."
                  + (f"\n기준 RN: {name}" if name else ""))



def run_rn_sales(mail_id) -> StageOutput:
    """**RN for Sales 제작** 단계 실행 — 확정된 선별로 공개용 문서만 다시 만든다.

    앞 단계(전체 이력 RN 제작)에서 리서치·선별표·컴포넌트가 모두 정해졌으므로 여기서는
    Redmine·Claude 를 다시 부르지 않는다. 전체 이력 RN 도 건드리지 않는다 — 1차 승인으로
    확정·저장이 끝난 문서다.

    1차 승인 전에는 실행하지 않는다. 전체 이력이 확정돼야 그 내용으로 공개본을 고르는
    순서이고, 승인 전에 만들면 승인 뒤 바뀐 내용과 어긋난 문서가 남는다.
    """
    import os as _os

    from app.pipeline.state import get_state

    st = get_state(mail_id) or {}
    _ok, _what = full_stage_done(st)
    if not _ok:
        return StageOutput(result_text=(
            "[RN for Sales 제작] ✘ 앞 단계가 끝나지 않았습니다 — " + _what + ".\n"
            "전체 이력 RN 이 확정된 뒤에 그 내용으로 공개본을 만듭니다. "
            "[전체 이력 RN 제작] 단계에서 승인을 받아 주세요."))

    lines = [str(x) for x in (st.get("rn_revision_lines") or []) if str(x).strip()]
    if not lines:
        return StageOutput(result_text=(
            "[RN for Sales 제작] ✘ 이번 판에 실을 내용이 없습니다.\n"
            "[전체 이력 RN 제작] 의 공개 선별표에서 반영할 항목을 골라 주세요."))

    path, err = regenerate_rn(mail_id, "", with_full=False)
    if not path:
        return StageOutput(result_text=f"[RN for Sales 제작] ✘ 문서 생성 실패: {err}")

    body = "\n".join(f"  - {x}" for x in lines)
    return StageOutput(
        result_text=("[RN for Sales 제작] 확정된 선별로 공개용 문서를 만들었습니다"
                     "(리서치·전체 이력본은 건드리지 않았습니다).\n"
                     f"· RN for Sales: {path}\n\n"
                     f"[이번 판에 실린 항목 {len(lines)}건]\n{body}\n\n"
                     "다음: [검수하기] 에서 최종 승인자 검수를 요청하세요."),
        artifact_type="docx", artifact_path=path)


_REWORK_SYS = (
    "당신은 이미 완성된 고객용 Release Note(영문 불릿 목록)를 사람의 추가 지시대로 수정하는 편집기입니다.\n"
    "- 전체를 새로 쓰지 말고, 지시가 요구하는 부분만 바꾸세요(항목 삭제/추가/문구 수정 등).\n"
    "- 지시와 무관한 줄은 원문 그대로 유지합니다.\n"
    "- 결과는 항상 영문 RN 불릿 스타일. 원문에 없는 내용은 지어내지 말 것.\n"
    "- 보안 패치 줄('Updated security patches to ...')은 시스템이 따로 관리하므로 결과에 포함하지 마세요.\n"
    '반드시 JSON 으로만: {"lines": ["...", "..."]}'
)


def _rework_lines(client, product: str, version: str, lines: list[str], revision: str) -> list[str]:
    """기존 RN 라인에 사람 지시(revision)를 반영해 새 라인 목록 반환. 실패 시 원본 유지."""
    from app.pipeline.ai_edit import _extract_json

    body = "\n".join(f"- {ln}" for ln in lines)
    prompt = (f"제품 {product} · 버전 {version}\n[현재 RN 항목]\n{body}\n\n"
              f"[사람의 추가 수정 지시 — 반드시 반영]\n{revision}\n\n"
              "지시를 반영한 최종 RN 항목 JSON 을 반환하세요.")
    try:
        resp = _create_with_retry(client, model=_MODEL, max_tokens=_JUDGE_MAX_TOKENS,
                                  system=_REWORK_SYS, messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        out = (_extract_json(text) or {}).get("lines")
        if out:
            return [str(x).strip() for x in out if str(x).strip()]
    except Exception:  # noqa: BLE001
        pass
    return list(lines)


def rework_rn(mail_id: int, revision: str) -> StageOutput:
    """② 재작업 — **완료된 RN 내용(revision_lines)에 지시만 반영**해 PDF 재생성.

    Redmine/전체 리서치 재수집 없이 저장된 상태만 재사용한다(빠름)."""
    import anthropic

    from app.pipeline.state import get_state, update_state

    st = get_state(mail_id) or {}
    lines = st.get("rn_revision_lines") or []
    if not lines:
        return StageOutput(result_text="✘ 재작업할 RN 내용이 없습니다. Release Note 제작을 먼저 실행하세요.")
    if not (revision or "").strip():
        return StageOutput(result_text="✘ 수정 지시가 비어 있습니다.")

    product = st.get("product", "")
    version = st.get("release_version") or st.get("os_version") or ""
    doc_date = st.get("rn_doc_date", "") or st.get("release_date", "")
    version_full = _keep_version_full(st.get("version_full", ""), version, doc_date)

    # 보안 패치 줄은 시스템이 별도 관리 → 편집 대상에서 분리 후 다시 맨 앞에 붙인다.
    _sec_prefix = "Updated security patches to"
    editable = [ln for ln in lines if not ln.startswith(_sec_prefix)]
    sec_lines = [ln for ln in lines if ln.startswith(_sec_prefix)]

    progress.step("추가 지시 반영(RN 편집) 중…")
    client = anthropic.Anthropic()
    new_lines = sec_lines + _rework_lines(client, product, version, editable, revision)

    rev_num = int(st.get("rn_rev", 1) or 1) + 1
    update_state(mail_id, rn_rev=rev_num, rn_revision_lines=new_lines,
                 rn_revision_draft="\n".join(f"- {ln}" for ln in new_lines))

    # 재작업은 **전체 이력 RN 제작** 단계의 기능이라 이력본만 다시 만든다(2026-08-27).
    # RN for Sales 는 다음 단계가 이 편집 결과(rn_revision_lines)로 만든다.
    update_state(mail_id, version_full=version_full)
    progress.step("전체 이력 RN 재생성 중…")
    _fp, _fe = build_full_rn(mail_id)
    if not _fp:
        return StageOutput(result_text=f"✘ 전체 이력 RN 재생성 실패: {_fe}")
    path = _fp

    body = "\n".join(f"  - {ln}" for ln in new_lines)
    summary = (f"[RN 재작업 · 완료 산출물 편집]\n제품 {product} · {version_full} (Rev V{rev_num})\n"
               f"지시: {revision}\n\n[반영된 RN 항목 {len(new_lines)}줄]\n{body}\n\n"
               + (f"전체 이력 RN: {_fp}\n" if _fp else f"전체 이력 RN 실패: {_fe}\n")
               + "※ 전체 리서치 재수집 없이 저장된 결과에 지시만 반영했습니다.")
    return StageOutput(result_text=summary, artifact_type="docx", artifact_path=path)


def _components_from_prev_rn(
    user_id: int, product: str, android: str, prev_version: str, mail_id,
    cur_version: str = "",
) -> tuple[dict, str, list, str, list]:
    """이번 버전 RN 이 파일서버에 아직 없을 때 — **직전 버전 RN** 에서 항목을 가져온다.

    항목 구성은 제품마다 다르다(개수·이름·순서). 그래서 고정 목록이 아니라 그 RN 의
    번호 섹션을 그대로 읽어 **입력 칸 목록**과 값을 만든다. 값은 버전이 바뀌어도 대개
    그대로라 초기값으로 채워 준다(사람이 고치면 그 값이 이긴다).

    **직전 버전 RN 이 없으면 그 제품이 가지고 있는 가장 최신 RN 으로 폴백한다**
    (2026-08-04). 값(GMS·SDK·Audio·Touch·Wi-Fi·보안패치)은 버전이 몇 판 떨어져도 대개
    그대로라, 빈 칸을 주는 것보다 최신 값을 초기값으로 주는 편이 사람이 덜 채운다.
    어느 문서에서 가져왔는지는 note 로 돌려주어 결과 화면에 그대로 표시한다.

    반환 (components, security_patch, input_fields, note, prev_revisions, lookup_error).
    못 가져오면 ({}, "", [], 사유, [], 조회오류).

    lookup_error 는 **조회 자체가 실패**했을 때만 찬다(자격증명 없음·파일서버 불가 등).
    비어 있으면서 아무것도 못 가져왔으면 '참고할 RN 이 정말 없다'(최초 출시)는 뜻이다.
    호출부는 이 둘을 갈라야 한다 — 뭉치면 파일서버가 끊긴 것을 최초 출시로 오해한다.

    prev_revisions 는 **전체 이력 RN** 이 이어 붙일 이전 판들이다. 어차피 같은 문서를
    내려받으므로 여기서 함께 뽑는다(추가 조회 없음).
    """
    from app.pipeline import rn_lookup as RNL

    why = ""            # 직전 버전 RN 을 못 쓴 사유(폴백했을 때만 채워진다)
    hit = None
    # 증분이 없는 회차(FIH 승인일 ≤ 출시일)에는 base_version 이 **이번 버전과 같다**.
    # 그대로 찾으면 '이번 버전 RN 을 직전 버전으로 찾는' 셈인데, 그건 바로 위(1-b)에서
    # 없다고 확인한 문서라 반드시 실패한다 — 폴백이 확정되고 사유도 '45.81 의 RN 이
    # 없음'으로 나와 사람을 헷갈리게 한다(실측 job-15: prev_version == release_version).
    if prev_version and cur_version and _norm_ver(prev_version) == _norm_ver(cur_version):
        why = "직전 버전이 따로 없음(이번 버전이 곧 기준 회차)"
        prev_version = ""
    if not prev_version:
        why = why or "직전 버전을 알 수 없음"
    else:
        try:
            hit = RNL.find_existing_rn(user_id, product, android, prev_version)
        except Exception as e:  # noqa: BLE001
            why = f"직전 버전 RN 조회 실패: {e}"
        if hit is None and not why:
            why = f"직전 버전 {prev_version} 의 RN 이 파일서버에 없음"

    # 폴백 — 직전 버전 RN 이 없으면 **가지고 있는 가장 최신 RN** 에서 값을 가져온다.
    fallback = False
    if hit is None:
        try:
            hit, _lerr = RNL.find_latest_rn(
                user_id, product, android, exclude_version=cur_version)
        except Exception as e:  # noqa: BLE001
            return {}, "", [], f"{why} · 최신 RN 조회도 실패: {e}", [], f"최신 RN 조회 실패: {e}"
        if hit is None:
            # _lerr 가 비어 있으면 후보가 0건 — 참고할 RN 이 정말 없다(최초 출시).
            return ({}, "", [],
                    f"{why} · " + (_lerr or "참고할 RN 이 파일서버에 없습니다."), [], _lerr)
        fallback = True

    # 어디서 가져왔는지만 **사실로** 남긴다. 사람에게 확인을 떠넘기는 안내 문구는
    # 붙이지 않는다(사용자 확정 2026-09-01).
    _src = (f"최신 RN({hit['name']}) 에서 가져옴"
            if fallback else f"직전 버전 RN({hit['name']}) 에서 가져옴")

    wd = _WORK / f"{mail_id}_prev"
    wd.mkdir(parents=True, exist_ok=True)
    local, err = RNL.download(user_id, hit["rel"], str(wd))
    if not local:
        _m = f"참고 RN({hit['name']}) 내려받기 실패: {err}"
        return {}, "", [], _m, [], _m
    try:
        fields = RNL.extract_sections(local) or []
        comps = RNL.extract_components(local) or {}
        prev_revs = revision_rows_from(RNL.extract_revision_entries_full(local))
        # 값을 물려받을 때 그 값이 정해진 판도 함께 물려받는다(2026-09-03).
        seed_component_versions(mail_id, local)
    except Exception as e:  # noqa: BLE001
        _m = f"참고 RN({hit['name']}) 에서 값 추출 실패: {e}"
        return {}, "", [], _m, [], _m
    # 섹션에서 뽑은 값도 컴포넌트에 합친다(구 추출기가 모르는 항목까지 살린다).
    for f in fields:
        if f.get("value"):
            comps.setdefault(f["key"], f["value"])
    sec = comps.pop("security_patch", "")
    comps.pop("known_issues", None)   # Known Issues 는 버전마다 달라 물려받지 않는다.
    if fallback:
        # 폴백본의 Revision History 는 **이 SKU 의 것이라고 증명될 때만** 이어받는다.
        # 폴더 탐색 마지막 순서가 다른 Android 세대 폴더까지 훑기 때문에
        # (PM95\Android13 …) 아무 문서나 붙이면 세대가 섞인다.
        #
        # 예전에는 폴백이면 **무조건** 버렸다. 그 뒤 find_latest_rn 에 SKU 버전 범위
        # 가드가 붙어(version_window, 2026-08-05) 세대 오염은 그쪽에서 막히는데도 폐기가
        # 남아 있어, 멀쩡한 이력까지 통째로 날아갔다 — 실측 job-15: PM45 45.81 이
        # 6판(V01~V06)짜리 45.76 문서를 받아 놓고 이력 0판으로 '전체 이력 RN' 을 만들었다.
        if RNL.in_sku_window(product, android, hit.get("version", "")):
            _src += f" · 이력 {len(prev_revs)}판"
        else:
            prev_revs = []
    if not comps and not sec and not fields and not prev_revs:
        # 문서는 찾았는데 못 읽은 것 — 최초 출시가 아니다. 오류로 올려 화면에서 구분한다.
        _m = f"참고 RN({hit['name']}) 에서 항목을 뽑지 못했습니다(문서 형식 확인 필요)."
        return {}, "", [], _m, [], _m
    return comps, sec, fields, _src, prev_revs, ""


def _prepend_c_revision_rows(mail_id, client, product: str, android: str,
                            base_version: str, version: str,
                            rows: list, prev_revs: list) -> list:
    """C 리비전의 선별표 — **기준 판 이력 + 그 뒤 수정 사항**을 앞에 붙인다.

    기준 판 이력은 참고본(기준 판 RN)에서 이미 읽어 둔 prev_revs 를 쓴다 — 문서를 다시
    내려받지 않는다. 수정 사항은 배포 공지 본문에서 Claude 가 읽는다(형식이 제각각이라
    규칙으로 잡으면 곧 깨진다 — 사용자 확정 2026-09-07).

    이미 표에 있는 문구는 건너뛴다(Redmine 이슈로 같은 내용이 올라온 경우).
    실패해도 예외를 올리지 않는다 — 못 얹으면 기존 표 그대로 간다.
    """
    from app.pipeline import c_revision as CR
    from app.pipeline import rn_select

    def _norm(x) -> str:
        return re.sub(r"\s+", " ", str(x or "")).strip().lower().rstrip(".")

    have = {_norm(r.get(k)) for r in (rows or [])
            for k in ("apply_note", "public", "issue") if r.get(k)}

    # ① 기준 판 이력 — 그 판의 줄들
    base_lines: list[str] = []
    for e in (prev_revs or []):
        if not isinstance(e, dict):
            continue
        _hdr = str(e.get("version") or e.get("header") or "")
        if _norm_ver(_hdr.split(" (")[0]) == _norm_ver(base_version):
            base_lines = [str(x) for x in (e.get("changes") or e.get("lines") or [])
                          if str(x).strip()]
            break
    if not base_lines and prev_revs:
        _last = prev_revs[-1]
        if isinstance(_last, dict):
            base_lines = [str(x) for x in (_last.get("changes") or _last.get("lines")
                                           or []) if str(x).strip()]

    # ② 기준 판 이후 수정 사항
    #
    # **GMS 승인 요청 메일의 [Change list] 가 먼저다**(사용자 지시 2026-09-21).
    # 우리 GMS 담당이 FIH 로 보내는 메일이라 누락이 없고, 그 목록이 곧 그 회차의 변경
    # 내용이다 — 실측 PM86 A13 86.53(#3764)의 두 줄이 발행된 RN 의 Revision History 와
    # 그대로 같았다. 규칙으로 읽으므로 Claude 를 부르지 않는다(크레딧과 무관하게 된다).
    # 배포 공지는 그 목록이 없을 때만 본다 — C 회차는 공지 자체가 없는 경우가 잦다.
    change_lines: list[str] = []
    src_note = ""
    try:
        from app.pipeline import gms_request as _GR

        progress.step("승인 요청 메일에서 변경 목록 읽는 중…")
        change_lines, src_note = _GR.change_lines_for(version)
    except Exception:  # noqa: BLE001  (못 읽어도 아래 배포 공지로 간다)
        change_lines = []
    if not change_lines:
        try:
            _cands = _deploy_candidates(0, product, android, want_version=version)
            _ids = [c.get("mail_id") for c in _cands[:3] if c.get("mail_id")]
            _mails = _mail_bodies(_ids)
            if _mails:
                progress.step("배포 공지에서 기준 판 이후 수정 사항 읽는 중…")
                change_lines, src_note = CR.changes_since_base(
                    client, _MODEL, product, base_version, version, _mails)
        except Exception:  # noqa: BLE001  (못 읽어도 기준 판 이력만으로 간다)
            change_lines = []

    _src = "승인 요청 메일" if "승인 요청 메일" in (src_note or "") else "배포 공지"
    add = [r for r in CR.rows_from(base_lines, change_lines, base_version, _src)
           if _norm(r.get("apply_note")) not in have]
    if not add:
        return rows
    out = add + list(rows or [])
    for i, r in enumerate(out, 1):
        r["no"] = i
    return rn_select.normalize_rows(out)


def _mail_bodies(ids: list) -> list[dict]:
    """메일 id 목록 → [{mail_id, subject, body}]. 본문이 있는 것만."""
    from app.database import get_session
    from app.models import CachedMail

    if not ids:
        return []
    with get_session() as db:
        rows = db.query(CachedMail).filter(CachedMail.id.in_(list(ids))).all()
        return [{"mail_id": m.id, "subject": m.subject or "",
                 "body": m.body_text or ""} for m in rows if (m.body_text or "").strip()]


def run_rn_research(
    user_id: int, subject: str, input_params: str = "", mail_id: int | None = None,
    revision: str = "", *, adopt_only: bool = False,
) -> StageOutput:
    if not have_auth():
        return StageOutput(result_text="Claude 를 부를 수 없습니다 — Claude Code(구독) 로그인이나 .env 의 ANTHROPIC_API_KEY 중 하나가 있어야 합니다.")

    progress.step("트리거 메일 분석 중 (제품·Android)…")
    context, err = _precompute(user_id, mail_id)
    if err:
        return StageOutput(result_text=f"[RN 리서치] 사전계산 실패: {err}")
    product, android = context["product"], context["android"]
    # 이후 단계(⑤ SP·⑥ RN 편집)가 Android 를 정확히 쓰도록 상태에 저장
    if mail_id is not None:
        from app.pipeline.state import update_state

        update_state(mail_id, product=product, android=android)
    header = f"[RN 리서치 · {product} A{android}]\n"

    # 0) 정식 버전 = 출시 이력(SP/RN) + FIH 승인 날짜 기준 증분. (GMS MR·빌드지문 아님)
    #    작업(job-N)에서 왔으면 승인 메일이 붙어 있을 때만 그 날짜를 쓴다.
    trig_date = ""
    _trig = context.get("trigger_mail_id") if context.get("mail_type") == "job" else mail_id
    if isinstance(_trig, int):
        trig_date = (MAILSEARCH.read_mail(user_id, _trig) or {}).get("received_at", "") or ""
    from app.pipeline.version_resolve import resolve_release_version

    rel = resolve_release_version(user_id, product, android, trigger_date=trig_date)
    version = rel.get("version", "")          # 문서·업로드용 정식 버전(base, 리비전 없음)
    release_date = rel.get("date", "") or ""
    ver_note = rel.get("note", "")

    # 리비전(C1 등)은 '근거 배포 메일 찾기'에만 쓰고, 문서 버전(version)은 base 로 유지한다.
    search_hint = version

    # Submission 단계에서는 ①(OS Download)이 아직 안 돌아 파일 기준 버전이 없다.
    # 사용자가 지정한 버전(version_override)이 있으면 그것을 쓴다 — **C 리비전까지 그대로**.
    has_override = False
    if mail_id is not None:
        from app.pipeline.state import get_state as _gs0

        _ov = str((_gs0(mail_id) or {}).get("version_override") or "").strip()
        if _ov:
            has_override = True
            search_hint = _ov                  # '95.07C1' → 근거 메일 검색에 사용
            _auto = version
            # 공식 릴리즈 버전명은 C 리비전을 포함한다('95.07 C1' → '95.07C1').
            # 배포 공지에 더 높은 C 가 있으면 아래에서 그쪽으로 다시 올라간다.
            version = _base_ver(_ov) + _c_revision(_ov)
            ver_note = ("사용자 지정 버전 " + version
                        + (f" (근거 검색 {_ov})" if _norm_ver(_ov) != _norm_ver(version) else "")
                        + (f" · 자동판별 {_auto}"
                           if _auto and _norm_ver(_auto) != _norm_ver(version) else ""))

    # 1) 배포 메일 역추적 → '개선사항' 이슈목록. 리비전 포함 검색힌트로 그 버전 메일들을 찾는다.
    progress.step("배포 메일에서 개선사항 이슈 목록 찾는 중…")
    cands = _deploy_candidates(user_id, product, android, want_version=search_hint)
    deploy = cands[0] if cands else None
    deploy_date = ""
    cap_warn = ""            # 상한에 걸려 버린 이슈가 있으면 여기에 담아 결과에 싣는다
    if deploy:
        # 버전 자동판별이 아예 실패(입력도 없음)했을 때만 배포 메일의 **base** 버전을 승계한다.
        #  (리비전 C1 등은 문서 버전에 넣지 않는다 — 찾기용일 뿐, 표기는 base '95.07'. Beta 제외.)
        if not version and deploy.get("version") and not deploy.get("is_beta"):
            version = _base_ver(deploy["version"])
            ver_note = f"배포 메일 #{deploy['mail_id']} 에서 확인"
        # 근거 = **같은 base 버전(예: 95.07 + 95.07 C1 …) 배포 공지들의 개선사항 합집합**.
        #  수정버전(C1)이 있으면 base 배포와 수정버전 배포의 이슈를 모두 합쳐 근거로 삼는다.
        base_v = _base_ver(version) or _base_ver(deploy["version"])
        same = [c for c in cands
                if _base_ver(c["version"]) == base_v and c.get("not_re")]
        # 대상 버전의 배포 공지가 하나도 없으면 **다른 버전 공지로 대체하지 않는다**.
        # 예전에는 `or [deploy]` 로 최신 후보를 그냥 썼다 — 실측 864: 67.10 RN 을 만들면서
        # 67.09 공지의 이슈 16건을 근거로 삼아 RN 내용 전체가 다른 회차의 것이 됐다.
        # 근거가 없으면 만들지 않고 멈추는 것이 이 단계의 원칙이다(아래 '근거 없음' 분기).
        _ver_gap = not same
        # 공식 릴리즈 버전명에 **C 리비전을 붙인다**(사용자 확정 2026-08-04).
        # 리비전 탐색은 RE: 회신까지 본다 — 회신 제목에도 원 공지의 버전이 그대로 남아 있어
        # 원 공지가 메일함에서 밀려 잘려도 C 판이 있었다는 사실은 놓치지 않는다.
        if version:
            _same_all = [c for c in cands if _base_ver(c["version"]) == base_v]
            version, _rev_note, _ver_conflict = official_version_keep(
                version, base_v, _same_all or same)
            if _ver_conflict:
                # 지정 버전이 공지보다 높다 — 지키되 **반드시 알린다**(아래 결과 문구).
                ver_note = (ver_note + " · " if ver_note else "") + _rev_note
            if _rev_note and not _ver_conflict:
                ver_note = (ver_note + " · " if ver_note else "") + _rev_note
                # 작업(목록)의 버전도 같은 표기로 맞춘다 — 안 맞추면 같은 회차가
                # '95.07'(작업)과 '95.07C1'(감지) 두 줄로 갈라져 보인다.
                if mail_id is not None:
                    from app.pipeline.state import get_state as _gs_job
                    from app.pipeline.state import update_state as _us_job

                    _stj = _gs_job(mail_id) or {}
                    if _stj.get("job_id"):
                        from app.os_update_jobs import sync_job_version

                        _ok, _note = sync_job_version(int(_stj["job_id"]), version)
                        if _note:
                            ver_note += f" · {_note}"
                    if str(_stj.get("version_override") or "").strip():
                        _us_job(mail_id, version_override=version)
        issue_ids, used = [], []
        # 이슈 번호가 없는 항목('SPL: Update SPL to 2026-06-05' 등)도 같이 건진다.
        # B1·B2… 판 공지에만 적혀 있다가 RC 판에서 빠지는 항목이 있어, 이슈와 마찬가지로
        # **같은 base 의 모든 공지에서 합집합**으로 모은다.
        text_items: list[str] = []
        for c in same:
            body = (MAILSEARCH.read_mail(user_id, c["mail_id"]) or {}).get("body", "")
            ids = _deploy_issue_ids(body)
            if ids:
                used.append(c)
            for iid in ids:                       # 순서 유지 + 중복 제거(합집합)
                if iid not in issue_ids:
                    issue_ids.append(iid)
            for t in _deploy_text_items(body):
                if t not in text_items:
                    text_items.append(t)
        # 상한을 넘으면 **몇 건을 버렸는지 남긴다**. 예전엔 말없이 잘라서, RN 에 빠진 항목이
        # 있어도 결과만 보고는 알 수 없었다(상한에 걸린 줄도 모른 채 '이게 전부'로 읽힌다).
        if len(issue_ids) > _MAX_ISSUES:
            cap_warn = (
                f"⚠ 개선사항 이슈 {len(issue_ids)}건 중 **{len(issue_ids) - _MAX_ISSUES}건을 "
                f"상한({_MAX_ISSUES})으로 제외**했습니다 — 뒤쪽 이슈가 RN 에서 빠집니다. "
                "환경변수 RN_MAX_ISSUES 로 상한을 올릴 수 있습니다.\n"
            )
            issue_ids = issue_ids[:_MAX_ISSUES]
        # 버전이 어긋났으면 그 공지의 날짜도 물려받지 않는다(출시일이 다른 회차 것이 된다).
        # **날짜는 정식 배포 공지에서만 가져온다.** 같은 회차라도 B판·RC 공지의 날짜는
        # 그 회차 빌드의 날짜가 아니다 — 베타가 새로 나올 때마다 빌드명이 바뀐다
        # (실측 2026-09-21 MF52G15.00: B20 Aug 21 → B25 Sep 02, 정식 빌드는 Sep 13).
        # 이슈 목록은 베타 공지에서도 그대로 모은다 — 같은 회차의 일이라 근거가 된다.
        _date_src = next((c for c in same
                          if is_release_version(c.get("version", ""))), None)
        _dep_date = (_date_src or {}).get("date", "")
        deploy_date = "" if _ver_gap else _dep_date
        if not _ver_gap and not release_date and _dep_date:
            release_date = _fmt_build_date(_dep_date)
        if _ver_gap:
            _near = ", ".join(f"#{c['mail_id']} {c['version']}" for c in cands[:3])
            source = (f"**{version} 의 배포 공지를 찾지 못했습니다.** "
                      f"가장 가까운 후보: {_near or '없음'} — 버전이 달라 근거로 쓰지 "
                      "않았습니다(다른 회차의 이슈가 RN 에 실리는 것을 막기 위해서입니다).")
        else:
            _src = used or [deploy]
            if len(_src) > 1:
                source = ("배포 메일 합산(" + " + ".join(
                    f"#{c['mail_id']} {c['version']}" for c in _src) + ")")
            else:
                _dt = deploy.get("gms_type") or "배포"
                source = f"배포 메일 #{deploy['mail_id']} ({_dt}) — {deploy['subject']}"
    else:
        issue_ids = []
        text_items = []
        reqs = MAILSEARCH.search_mails(user_id, f"approval test for {product}", 5)
        req_ids = ", ".join(f"#{r['id']}" for r in reqs)
        source = f"해당 Android 의 배포 메일 없음. 근거 후보 메일: {req_ids or '없음'}."

    # 정식 버전을 상태에 저장 → ⑤ SP·⑥ RN 편집이 이 버전으로 문서를 갱신한다.
    if mail_id is not None and version:
        from app.pipeline.state import update_state

        update_state(mail_id, release_version=version, release_date=release_date,
                     prev_version=rel.get("base_version", ""))   # ⑤⑥ 편집이 교체할 직전 버전

    if not version:
        return StageOutput(result_text=(
            header + "정식 OS 버전을 확정하지 못했습니다.\n"
            f"사유: {ver_note or '출시 이력 소스 없음'}\n"
            "→ Service Portal / OS·FW Release Notes 에서 현재 버전을 확인해 수동 지정해야 합니다."
        ))

    # 1-b) **이미 만들어진 RN 이 파일서버에 있으면 그것을 쓴다**(새로 제작하지 않는다).
    #      \\192.168.123.104\Project\SQA\Documents\ReleaseNotes\{제품}\[Android{N}\]{파일}.pdf
    #      찾으면 그 PDF 를 산출물로 삼고, Revision History·컴포넌트를 뽑아 상태에 넣어
    #      ③ Dropbox 업로드·⑤⑥⑦ 편집이 그대로 이어서 쓰게 한다.
    progress.step("파일서버에 이미 만든 RN 이 있는지 확인 중…")
    try:
        from app.pipeline import rn_lookup as RNL

        _hit = RNL.find_existing_rn(user_id, product, android, version)
    except Exception:  # noqa: BLE001
        _hit = None
    # 건너뛰기(adopt_only)는 **이미 만들어져 있는 RN 을 쓰자**는 뜻이다. 없으면 여기서
    # 끝낸다 — 무거운 리서치로 넘어가면 '건너뛰기' 가 아니게 된다(사용자 지적
    # 2026-09-21: 사람이 올려 둔 86.53 RN 으로 RN for Sales 만 만들고 싶다).
    if adopt_only and not _hit:
        return StageOutput(result_text=(
            header + "✘ 건너뛸 수 없습니다 — 파일서버에서 "
            f"{product} {version} 의 RN 을 찾지 못했습니다.\n"
            "이미 만들어 둔 RN 이 있다면 파일서버의 제품 RN 폴더에 두고 다시 "
            "누르세요. 없다면 [전체 이력 RN 제작] 을 실행해 만들어야 합니다."))

    if _hit:
        _wd = _WORK / f"{mail_id}_existing"
        _wd.mkdir(parents=True, exist_ok=True)
        progress.step(f"기존 RN 내려받는 중… ({_hit['name']})")
        _local, _derr = RNL.download(user_id, _hit["rel"], str(_wd))
        if adopt_only and not _local:
            return StageOutput(result_text=(
                header + f"✘ 건너뛸 수 없습니다 — 기존 RN({_hit['name']}) 을 "
                f"내려받지 못했습니다: {_derr}"))
        if _local:
            from app.pipeline.state import get_state as _gs2
            from app.pipeline.state import update_state as _us2

            progress.step("기존 RN 읽는 중 (Revision History · 컴포넌트 · 입력 항목)…")
            # 전체 이력본이 이 문서의 **모든 판**을 이어받는다 — V번호·Update 날짜까지 살린다.
            _ents_full = RNL.extract_revision_entries_full(_local)
            _ents = {v: d["lines"] for v, d in _ents_full.items()}
            _comps = RNL.extract_components(_local)
            # 입력 항목은 제품마다 다르다(개수·이름·순서). 이 RN 의 섹션을 그대로 입력 칸으로 쓴다.
            _fields = RNL.extract_sections(_local)
            # 값과 함께 **그 값이 정해진 판**도 읽어 기준선으로 둔다(2026-09-03).
            seed_component_versions(mail_id, _local)
            if mail_id is not None:
                # 항목 목록은 **표준표**로 고정하고(번호가 흔들리지 않게), 값만 이 문서에서
                # 물려받는다. 표준에 없는 항목은 값만 남아 extra 로 문서 뒤에 붙는다.
                _us2(mail_id, rn_input_fields=[dict(s) for s in RNL.STANDARD_SECTIONS])
                # 이번 버전 RN 을 **찾았다** — 값을 물려받을 문서가 있으므로 '참고할 RN 이
                # 없다'는 최초출시 표시는 더 이상 맞지 않다. 여기서 안 내리면 검수 화면이
                # 문서를 정상으로 읽어 왔는데도 "참고할 Release Note 가 없습니다" 를 띄운다
                # (rn_review.py 가 이 값만 보고 판단한다).
                _us2(mail_id, rn_first_release=False)
                for _f in _fields:
                    if _f.get("value") and _f["key"] != "security_patch":
                        _comps.setdefault(_f["key"], _f["value"])
            # 마지막 Revision = 대상 버전의 항목(이 버전용으로 이미 만든 RN 이므로 그게 최신 판).
            # 같은 버전이 여러 판으로 실려 있으면 **마지막 판**이 최신이다
            # (이 버전용으로 이미 만든 RN 이므로 뒤에 실린 판이 최종본).
            _key = next((k for k in reversed(list(_ents))
                         if _base_ver(k) == _base_ver(version)), "")
            # 기존 문서의 문구에는 태그가 없을 수 있다(실제 배포 RN 은 38% 만 태그가
            # 있었다). 그대로 실으면 이번 판만 태그가 빠지므로 여기서 채운다.
            # 이번 판과 과거 판을 **한 번에** 태깅한다 — 문구가 겹쳐 호출이 줄고,
            # 같은 문구가 판마다 다른 태그를 받는 일이 없다.
            _changes = [str(x) for x in (_ents.get(_key) or []) if str(x).strip()]
            _sec = _comps.pop("security_patch", "") if _comps else ""
            # 버전 표기의 **빌드 날짜는 그 문서에 적힌 것**을 쓴다. 그 값은 OS 빌드
            # 이름에서 온 정본이라 우리가 다시 계산하면 안 된다(사용자 확정 2026-09-21).
            # 예전에는 release_date 를 썼는데, 그 값이 베타 공지에서 와서 빌드명이
            # 흔들렸다(실측 MF52G15.00: 문서는 Sep 13 인데 Sep 02 로 나갔다).
            _hdr = str((_ents_full.get(_key) or {}).get("header") or "").strip()
            _doc_bdate = ""
            if "(" in _hdr and _norm_ver(_hdr.split(" (")[0]) == _norm_ver(version):
                _doc_bdate = _hdr.split("(", 1)[1].rstrip(")").strip()
            _vfull = _version_full(version, _doc_bdate or release_date)
            _st_a = (_gs2(mail_id) or {}) if mail_id is not None else {}
            # 이 문서가 **이 버전의 정본**이다 — 자동으로 채워 둔 값은 여기 값으로 갱신하고,
            # 사람이 고친 값(수동 표시)만 지킨다.
            _merged, _msrc, _mnotes = merge_components(_st_a, _comps, refresh=True)
            _secv, _ssrc, _snote = merge_security_patch(_st_a, _sec, refresh=True)
            if _snote:
                _mnotes.append(_snote)
            # 이번 회차 Beta OS 의 값이 **마지막에 덮어쓴다** — 문서보다 OS 가 정본이다.
            _merged, _secv, _onotes = apply_os_props(
                user_id, product, android, version, _merged, _secv, mail_id=mail_id)
            _mnotes.extend(_onotes)

            if _changes and mail_id is not None:
                # ── 마지막 Revision 을 **선별표**로 정리(민감 판정·하이라이트) ──────────
                from app.pipeline import rn_select as _RSEL
                progress.step(f"Claude 공개 문구 판정 중… ({len(_changes)}건)")
                try:
                    _cls = _RSEL.classify_lines(
                        anthropic.Anthropic(), product, android, _changes, _MODEL)
                except Exception:  # noqa: BLE001
                    _cls = []
                _sel = []
                for _i, _ln in enumerate(_changes):
                    _c = _cls[_i] if _i < len(_cls) else {}
                    # 전부 반영(Y)이므로 '수정 내용'을 항상 채운다(순화 문구 > 원문 순).
                    _sel.append({
                        "no": _i + 1, "issue": _ln, "team": "", "customer": "",
                        "gubun": "", "include": "Y", "reason": "",
                        "public": _ln, "redmine": "",
                        "sensitive": bool(_c.get("sensitive")),
                        "apply_note": (_c.get("apply_note", "") or "").strip() or _ln,
                    })
                # ── 근거 배포 메일의 Redmine 이슈와 매칭해 번호를 채운다(best-effort) ──────
                if issue_ids:
                    try:
                        from app.integrations import redmine as _RM
                        progress.step(f"Redmine 이슈 읽는 중… ({len(issue_ids)}건)")
                        _imap, _ = _RM.get_issues(user_id, issue_ids)
                        _iss = [_imap[i] for i in issue_ids if i in _imap]
                        _cli = anthropic.Anthropic()
                        progress.step(f"Claude 이슈 매칭 중… ({len(_iss)}건)")
                        _match = _RSEL.match_lines_to_issues(
                            _cli, product, _changes, _iss, _MODEL)
                        # 매칭된 이슈의 고객명 추출(이슈 원문에 명시된 경우만; 없으면 공백).
                        progress.step("Claude 고객·팀 정보 추출 중…")
                        try:
                            _cls_iss = _RSEL.classify_issues(_cli, product, android, _iss, _MODEL)
                        except Exception:  # noqa: BLE001
                            _cls_iss = {}
                        for _i, _iid in enumerate(_match):
                            if _iid and _i < len(_sel):
                                _sel[_i]["redmine"] = _RSEL._REDMINE + _iid
                                # 매칭된 이슈에서 작성자(팀)·고객도 채운다(리비전 문구엔 없는 정보).
                                _io = _imap.get(_iid)
                                if _io:
                                    _sel[_i]["team"] = _RSEL.team_of(_io.get("created_by", ""))
                                _cst = (_cls_iss.get(_iid, {}) or {}).get("customer", "")
                                if _cst:
                                    _sel[_i]["customer"] = _cst
                    except Exception:  # noqa: BLE001
                        pass
                # ── 이 단계의 문서는 **전체 이력 RN 하나**다(2026-08-27 단계 분리) ──────
                # 예전에는 여기서 '마지막 Revision 만' 담은 RN for Sales 초안도 함께
                # 만들었는데, 그 문서는 다음 단계가 선별 결과로 다시 만든다.
                _sec_use = _sec or (_gs2(mail_id) or {}).get("security_patch", "")
                _wd2 = _WORK / f"{mail_id}_{datetime.now():%Y%m%d-%H%M%S}"
                _wd2.mkdir(parents=True, exist_ok=True)
                # 이번 버전 RN 이 파일서버에 **이미 있으면 전체 이력 RN 을 만들지 않는다**
                # (사용자 확정 2026-08-27). 파일서버의 그 문서가 곧 전체 이력본이라
                # 같은 것을 하나 더 만들 이유가 없다. 선별표·입력값은 그대로 읽어 둔다
                # — 다음 단계(RN for Sales 제작)가 그 값을 쓴다.
                _us2(mail_id, rn_full_skipped=True,
                     rn_full_skip_reason=(f"이번 버전 RN 이 파일서버에 이미 있습니다 "
                                          f"({_hit['name']}). 전체 이력 RN 을 새로 "
                                          "만들지 않습니다."))
                _us2(mail_id,
                     rn_pdf_path=_local, rn_pdf_name=os.path.basename(_local),
                     rn_existing_pdf_path=_local,
                     rn_revision_lines=_changes, rn_public_changes=_changes,
                     rn_revision_draft="\n".join(f"- {c}" for c in _changes),
                     rn_select_rows=_sel, rn_components=_merged,
                     rn_component_src=_msrc, rn_rev=1,
                     version_full=_vfull, rn_from_fileserver=_hit["rel"],
                     rn_doc_date=_fmt_doc_date(_doc_bdate or release_date))
                if _secv:
                    _us2(mail_id, security_patch=_secv, security_patch_src=_ssrc)
                # 이력: 기존 RN 을 찾아 선별표만 정리(문서는 만들지 않는다)
                from app.pipeline import history as _H2
                _H2.record(mail_id, "rn", "생성",
                           summary=f"기존 RN 마지막 Revision({_key or version}) 으로 초안 생성",
                           text=_H2.text_from_rn(_gs2(mail_id) or {}))
                _body = "\n".join(f"  - {c}" for c in _changes)
                return StageOutput(
                    result_text=(
                        header + cap_warn
                        + f"버전 {version} — **기존 RN 을 찾아 마지막 Revision 을 선별표로 정리했습니다.**\n"
                        f"· 파일: {_hit['name']}\n· 위치: {_hit['where']}\n\n"
                        f"[마지막 Revision {len(_changes)}건 → 선별표]\n{_body}\n\n"
                        f"[컴포넌트] {_comps or '(추출 없음)'}"
                        + (f"\n[보안 패치] {_secv}" if _secv else "")
                        + ("\n[값 갱신] " + " · ".join(_mnotes) if _mnotes else "")
                        + "\n\n[전체 이력 RN] **만들지 않습니다** — 이번 버전 RN 이 "
                          f"파일서버에 이미 있습니다({_hit['name']}). 그 문서가 곧 "
                          "전체 이력본이라 같은 것을 하나 더 만들지 않습니다.\n"
                          "[검수하기] 에서 그 문서와 선별표를 확인하고 이 단계를 "
                          "완료하세요."
                    ),
                    artifact_type="pdf",
                    artifact_path=_local,
                    # 새로 만든 문서는 없지만 **무엇을 가져다 썼는지** 는 봐야 한다.
                    # 예전에는 여기서 바로 완료로 넘겼는데, 그러면 보드에 [검수하기]
                    # 가 아예 안 뜨고 까닭도 안 보인 채 완료가 됐다(사용자 지적
                    # 2026-09-11). 검수 대기로 세우고, 검수 화면에서 확인 후 끝낸다.
                )

            # 폴백: 해당 버전 항목을 못 뽑았으면 기존처럼 PDF 를 그대로 산출물로 쓴다.
            if mail_id is not None:
                _us2(mail_id,
                     rn_pdf_path=_local, rn_pdf_name=os.path.basename(_local),
                     rn_existing_pdf_path=_local,
                     rn_revision_lines=_changes, rn_public_changes=_changes,
                     rn_revision_draft="\n".join(f"- {c}" for c in _changes),
                     rn_components=_merged, rn_component_src=_msrc,
                     version_full=_vfull, rn_from_fileserver=_hit["rel"])
                if _secv:
                    _us2(mail_id, security_patch=_secv, security_patch_src=_ssrc)
            _body = "\n".join(f"  - {c}" for c in _changes) or "  (해당 버전 항목을 못 뽑았습니다)"
            return StageOutput(
                result_text=(
                    header + cap_warn
                    + f"버전 {version} — **이미 만들어진 RN 을 파일서버에서 찾았습니다.**\n"
                    f"· 파일: {_hit['name']}\n· 위치: {_hit['where']}\n\n"
                    f"[문서에서 뽑은 Revision History {len(_changes)}건]\n{_body}\n\n"
                    f"[컴포넌트] {_comps or '(추출 없음)'}"
                    + (f"\n[보안 패치] {_sec}" if _sec else "")
                    + "\n\n※ 해당 버전 항목을 못 뽑아 이 문서를 그대로 산출물로 씁니다."
                ),
                artifact_type="pdf", artifact_path=_local,
            )

    # 최초 출시(신규 OS)는 여기서 갈라진다 — 이전 판이 없어 '무엇이 달라졌나'가 성립하지
    # 않고, 실제 사내 RN 도 '[System] 1st official release' 한 줄뿐이다. 이슈를 읽고
    # Claude 로 요약해 봐야 아래에서 전부 버려지므로, 그 일을 **아예 하지 않는다**.
    # (판정 근거는 제품관리의 '최초 OS 버전' — _is_first_release_for 참조.)
    first_release = _is_first_release_for(product, android, version)
    if first_release:
        progress.step("최초 출시 — 이슈 선별 없이 '1st official release' 한 줄로 만듭니다")
        issue_ids, text_items = [], []
        source = "최초 출시 — 이전 판이 없어 선별 대상이 없습니다"

    # 최초 출시는 근거 이슈가 없는 게 정상이라 여기서 멈추지 않는다(멈추면 신규 제품은
    # 배포 공지가 오기 전까지 RN 을 아예 못 만든다).
    if not issue_ids and not text_items and not first_release:
        return StageOutput(result_text=(
            header + f"버전: {version}" + (f"  (근거: {ver_note})" if ver_note else "")
            + f"\n이슈 근거: {source}\n\n"
            "→ 개선사항 이슈 목록을 자동 확보하지 못했습니다. "
            "해당 버전의 배포 공지가 오면 다시 실행하시거나, 버전을 직접 지정해 주세요."
        ))

    # 2) 개선사항 이슈 상세 읽기(무토큰)
    ordered: list[dict] = []
    errs: list = []
    if issue_ids:
        progress.step(f"Redmine 이슈 상세 읽는 중… ({len(issue_ids)}건)")
        issues, errs = RM.get_issues(user_id, issue_ids)
        ordered = [issues[i] for i in issue_ids if i in issues]
    # 이슈를 하나도 못 읽었더라도 **번호 없는 항목이 있으면 진행**한다 — 그 항목만으로도
    # 선별표를 만들 수 있고, 여기서 멈추면 그 내용이 통째로 사라진다.
    if not ordered and not text_items and not first_release:
        return StageOutput(result_text=(
            header + cap_warn + f"버전 {version}\n근거: {source}\n"
            f"이슈 {len(issue_ids)}건을 Redmine 에서 읽지 못했습니다: {errs[:3]}"
        ))
    if issue_ids and not ordered:
        cap_warn += (f"⚠ 이슈 {len(issue_ids)}건을 Redmine 에서 읽지 못했습니다({errs[:2]}) — "
                     "번호 없는 항목만으로 선별표를 만듭니다.\n")

    # 2-1) 관계(연결) 이슈 추가 로드 — 생성자≠해결자 추적용(예: #25999 → #25929 david.kim)
    rel_ids: list[str] = []
    for iss in ordered:
        for r in iss.get("relations") or []:
            if r.get("id"):
                rel_ids.append(str(r["id"]))
    already = set(str(i) for i in issue_ids)
    rel_ids = [r for r in dict.fromkeys(rel_ids) if r not in already]
    if len(rel_ids) > _MAX_RELATED:
        cap_warn += (
            f"⚠ 관계 이슈 {len(rel_ids)}건 중 {len(rel_ids) - _MAX_RELATED}건을 "
            f"상한({_MAX_RELATED})으로 제외했습니다 — 그 이슈들의 생성자/해결자는 비어 있을 수 "
            "있습니다(RN 본문에는 영향 없음).\n"
        )
        rel_ids = rel_ids[:_MAX_RELATED]
    related_map: dict = {}
    if rel_ids:
        progress.step(f"관계 이슈 추가 읽는 중… ({len(rel_ids)}건)")
        related_map, _ = RM.get_issues(user_id, rel_ids)

    # 2-2) 이번 목록 안에서 서로 연결된 이슈는 한 행으로 묶는다(대표 = 먼저 나온 이슈).
    #      나머지는 대표 행의 merged 로 남겨 검수 화면이 링크로 보여 준다(2026-10-19).
    from app.pipeline import rn_select as _RSEL_G

    merged_of: dict[str, list[dict]] = {}
    _groups = _RSEL_G.related_groups(ordered)
    if any(len(g) > 1 for g in _groups):
        merged_of = {str(g[0]["id"]): g[1:] for g in _groups if len(g) > 1}
        _n_m = sum(len(v) for v in merged_of.values())
        progress.step(f"서로 연결된 이슈 {_n_m}건을 대표 이슈 {len(merged_of)}건에 묶음")
        cap_warn += (f"연결된 이슈 {_n_m}건을 대표 이슈 {len(merged_of)}건에 묶었습니다 — "
                     + ", ".join(f"#{k}←" + "·".join(f"#{x['id']}" for x in v) for k, v in merged_of.items()) + "\n")
        ordered = [g[0] for g in _groups]

    # 3) 청크 요약 → items(rows + 고객용 change) (rate limit 대응, 실패 시 raw 폴백)
    # 최초 출시면 Claude 를 한 번도 부르지 않는다 — 클라이언트도 만들지 않는다.
    client = None if first_release else anthropic.Anthropic()
    items: list[dict] = []
    _n = len(ordered)
    for k in range(0, _n, _CHUNK):
        progress.step(f"Claude 이슈 요약 중… ({min(k + _CHUNK, _n)}/{_n}건)")
        items += _summarize_chunk(
            client, product, android, version, ordered[k:k + _CHUNK], revision, related_map
        )
    rows = [it["row"] for it in items]
    changes = [it["change"] for it in items if it.get("change")]

    # 3-1) 공개 RN 선별: 신규기능·기본값/로직 개선·고객요청 일반건을 항목화(규칙은 rn_select 참조).
    #      사람 draft 형식(번호/이슈/작성자/고객/구분/포함여부/비고/redmine) 표 + 포함(Y) 공개문구.
    from app.pipeline import rn_select

    # first_release 는 위(이슈 읽기 직전)에서 이미 정했다 — 여기서 다시 부르면 판정 기준이
    # 두 벌이 된다. 최초 출시면 ordered 도 text_items 도 비어 있다.
    sel_errors: list[str] = []
    if first_release:
        progress.step("최초 출시 — 공개 선별표를 '1st official release' 한 줄로 고정합니다")
        classified, sel_rows = {}, []
        public_changes = [rn_select.FIRST_RELEASE_LINE]
        n_inc, n_unjudged = 1, 0
        _carry = {}
    else:
        progress.step("공개 Release Note 항목 선별 중…")
        classified = rn_select.classify_issues(
            client, product, android, ordered, _MODEL, errors=sel_errors)
        # 선별표 기본값은 **전부 반영(Y)** 이다 — 사람이 빼는 방향으로 검수한다.
        # 단 사람이 이미 선별표에서 반영/미반영을 정했으면(rn_select_edited_at) 그 값이
        # 이긴다. 재제작이 사람 판단을 조용히 되돌리면 안 되기 때문이다.
        from app.pipeline.state import get_state as _gs_first

        _st_sel = (_gs_first(mail_id) or {}) if mail_id is not None else {}
        _carry = {}
        if _st_sel.get("rn_select_edited_at"):
            for _r in _st_sel.get("rn_select_rows") or []:
                _rid = str(_r.get("redmine") or "").rstrip("/").rsplit("/", 1)[-1]
                if _rid.isdigit():
                    _carry[_rid] = _r.get("include")
                elif _r.get("issue"):
                    # 이슈 번호가 없는 항목(배포 공지 문구)은 문구 자체가 키다.
                    _carry[str(_r["issue"]).strip()] = _r.get("include")
        sel_rows, public_changes = rn_select.build_selection(
            ordered, classified, include_all=True, carry=_carry)
        n_inc = sum(1 for r in sel_rows if r[5] == "Y")
        n_unjudged = len(ordered) - len(classified)

    # ④ Excel(Release_Note)용: 전체 변경요약 + 공개 선별문구를 상태에 저장
    if mail_id is not None:
        from app.pipeline.state import update_state

        update_state(mail_id, rn_changes=changes, rn_public_changes=public_changes)

    # 4) 저장(로컬): RN(PDF)만. issues.xlsx·select.xlsx 는 더 이상 만들지 않는다.
    #    선별 데이터(이슈·포함 Y/N·공개문구)는 파일 대신 **검수 팝업 인라인 표시용**으로 state 에 저장.
    workdir = _WORK / f"{mail_id}_{datetime.now():%Y%m%d-%H%M%S}"
    workdir.mkdir(parents=True, exist_ok=True)
    vtag = (version or "RN").replace(" ", "")
    # 최초 출시면 한 줄로 끝. 이슈 행도, 배포 공지의 번호 없는 항목도 올리지 않는다.
    sel_display = rn_select.first_release_rows() if first_release else []
    for _row, _iss in zip(sel_rows, ordered):
        _inc = _row[5] == "Y"
        _c = classified.get(str(_iss["id"]), {})
        _public = _c.get("public", "")
        # '수정 내용'(apply_note) = RN 에 실릴 문구. **반영·미반영을 가리지 않고 채운다** —
        #   · 반영(Y): 그대로 RN 에 들어가는 문구
        #   · 미반영(X): '반영하면 이렇게 들어갑니다' 미리보기(문서에는 들어가지 않는다)
        # 규칙은 rn_select.default_note 한 곳에 있다(빈칸을 남기지 않는다).
        _note = rn_select.default_note(
            apply_note=_c.get("apply_note", "") or "", public=_public,
            issue=str(_row[1] or ""))
        sel_display.append({
            # _select.xlsx(사람 draft) 양식 그대로: 포함여부(=반영/미반영)는 Y(반영)/X(미반영).
            "no": _row[0], "issue": _row[1], "team": _row[2], "customer": _row[3],
            "gubun": _row[4], "include": _row[5], "reason": _row[6],
            # public 은 **모든 행**에 저장한다. 나중에 RN 승인자가 제외→포함으로 바꾸면
            # 그 문구를 문서에 넣어야 하는데, 포함 행에만 저장하면 넣을 문구가 없다.
            "public": _public, "redmine": _row[7],
            # 민감 판정(하이라이트) + 수정 내용(RN 에 실릴 문구; 사람이 고쳐 쓸 수 있다).
            "sensitive": bool(_c.get("sensitive")), "apply_note": _note,
            # 이 행에 묶인 연결 이슈(같은 문제를 따로 올린 것) — 검수 화면이 링크로 보여 준다
            "merged": [{"id": str(m["id"]), "subject": m.get("subject", ""),
                        "redmine": _row[7].rsplit("/", 1)[0] + "/" + str(m["id"])}
                       for m in merged_of.get(str(_iss["id"]), [])],
        })
    # ── 이슈 번호가 없는 배포 공지 항목도 선별표에 올린다 ────────────────────
    # 'SPL: Update SPL to 2026-06-05' 처럼 번호 없이 문구만 적힌 것들이다. Redmine 이
    # 없으니 팀·고객·구분은 비우고, 고객 공개용 영문 한 줄만 Claude 로 만든다.
    if text_items and not first_release:
        progress.step(f"배포 공지의 번호 없는 항목 정리 중… ({len(text_items)}건)")
        _tno = [{"no": str(i), "issue": t} for i, t in enumerate(text_items, 1)]
        try:
            _tpub = rn_select.public_lines_for(client, product, android, _tno, _MODEL)
        except Exception:  # noqa: BLE001  (문구 생성 실패해도 항목 자체는 살린다)
            _tpub = {}
        for _i, _t in enumerate(text_items, 1):
            _line = (_tpub.get(str(_i)) or "").strip()
            _inc = _carry.get(_t, "Y") == "Y"      # 사람이 정한 값이 있으면 그것이 이긴다
            sel_display.append({
                "no": len(sel_display) + 1, "issue": _t, "team": "", "customer": "",
                "gubun": "", "include": "Y" if _inc else "X",
                "reason": ("배포 공지 항목(이슈 번호 없음)"
                           + ("" if _line else " · 공개 문구 생성 실패 — 직접 적어 주세요")),
                "public": _line, "redmine": "",
                "sensitive": False, "apply_note": (_line or _t),
                # 재실행 때 Redmine 이슈로 복원되지 않는 항목이라는 표시.
                "from_mail": True,
            })
            if _inc and _line:
                public_changes.append(_line)

    if mail_id is not None:
        from app.pipeline.state import get_state as _gs_sel
        from app.pipeline.state import update_state as _us_sel

        # 사람이 '행 추가'로 직접 넣은 항목은 ②를 다시 실행해도 남긴다
        # (Redmine 이슈에 없는 항목이라 재수집으로는 복원되지 않는다).
        _manual = [r for r in ((_gs_sel(mail_id) or {}).get("rn_select_rows") or [])
                   if r.get("manual")]
        for _i, _m in enumerate(_manual, start=len(sel_display) + 1):
            _m = dict(_m)
            _m["no"] = _i
            sel_display.append(_m)
        # 그 항목만의 공개 문구를 끝내 못 받은 행(판정 실패 등)은 여기서 한 번에 만든다.
        # 기본값이 '전부 반영'이라 문구 없는 행도 그대로 RN 후보가 되는데, 그 자리에
        # 사내 이슈 제목(한국어)이 실리면 안 된다. 실패하면 아래 normalize_rows 가
        # 이슈 제목으로라도 채운다 — 빈칸으로 두지는 않는다.
        _blank = [r for r in sel_display
                  if not str(r.get("public") or "").strip()
                  and str(r.get("issue") or "").strip()
                  and not r.get("manual")]
        if _blank:
            progress.step(f"공개 문구가 없는 항목의 문구 생성 중… ({len(_blank)}건)")
            try:
                _made = rn_select.public_lines_for(
                    client, product, android,
                    [{"no": str(r["no"]), "issue": str(r["issue"])} for r in _blank],
                    _MODEL)
            except Exception:  # noqa: BLE001  (문구 생성 실패해도 항목 자체는 살린다)
                _made = {}
            for _r in _blank:
                _ln = (_made.get(str(_r["no"])) or "").strip()
                if _ln:
                    _r["public"] = _ln
                    _r["apply_note"] = _ln

        # 저장 직전에 불변식을 강제한다(기본 반영 · 수정 내용 채움). 위 경로 중 하나가
        # 빠뜨려도 여기서 잡힌다 — 규칙을 한 곳에 모아 둔 이유다.
        sel_display = rn_select.normalize_rows(sel_display)
        # 시스템 항목(보안패치·뭉침)도 표에 올린다 — 사람이 빼거나 문구를 고칠 수 있게
        # (2026-08-26). 최초 출시는 '1st official release.' 한 줄뿐이라 붙이지 않는다.
        if not first_release:
            _prev_sel = (_gs_sel(mail_id) or {}).get("rn_select_rows") or []
            # 이 자리에서 쓸 수 있는 건 **저장된 값**뿐이다. 예전에는 뒤에
            # `or security_patch` 가 붙어 있었는데 그 이름은 한참 아래(문서 만들기
            # 직전)에서야 생겨서, 저장된 값이 비면 UnboundLocalError 로 죽었다.
            _sp = (_gs_sel(mail_id) or {}).get("security_patch") or ""
            sel_display = rn_select.ensure_system_rows(
                sel_display,
                security_line=secpatch_line(_sp),
                prev=_prev_sel,
            )
        # 번호 없는 항목의 공개 문구가 뒤에 붙었으므로 여기서 다시 저장한다
        # (앞에서 저장한 rn_public_changes 는 이슈 분만 담고 있다 — ④ Excel·⑦ 이 그걸 쓴다).
        _us_sel(mail_id, rn_select_rows=sel_display, rn_public_changes=public_changes)

    # RN 문서 리비전(Revision History 표): 최초 V1, 재작업(revision 지시)마다 +1, 현재 판 1행만.
    from app.pipeline.state import get_state, update_state

    _st = get_state(mail_id) or {} if mail_id is not None else {}
    # RN 컴포넌트(보안패치·GMS·SDK·audio/touch/wifi·Known Issues)는 전부 **RN 승인자가 RN 에서 입력**한다.
    # ① OS Download 는 PFU 에서 아무것도 추출하지 않는다(SDK 버전은 여기 입력값을 ①이 되받아 씀).
    _comps = _parse_components(input_params)
    # 이번 버전 RN 이 없으므로(_hit 없음) **직전 버전 RN** 에서 항목 목록·값을 가져온다.
    progress.step("직전 버전 RN 에서 입력 항목·이력 가져오는 중…")
    # C 리비전은 **기준 판이 곧 직전 판**이다(사용자 확정 2026-09-07). 출시 이력에서
    # 오는 base_version 은 기준 판이 아직 이력에 없으면 한 판 더 과거를 집는다 —
    # 실측 job-21: 95.07C1 인데 95.04 를 잡아 95.07 의 이력이 문서에서 통째로 빠졌다.
    from app.pipeline import c_revision as _CR

    _c_base, _c_rev = _CR.split_c(version)
    _prev_want = _c_base if _c_rev else rel.get("base_version", "")
    (_prev_seed, _prev_sec, _prev_fields, _prev_note, _prev_revs,
     _prev_err) = _components_from_prev_rn(
        user_id, product, android, _prev_want, mail_id,
        cur_version=version)
    # C 리비전이면 **기준 판 이력 + 그 뒤 수정 사항**을 선별표 앞에 올린다
    # (사용자 확정 2026-09-07). C 판은 기준 판을 대체하므로 그 한 행이 둘을 함께
    # 담아야 한다. 둘 다 행으로 두어야 사람이 빼거나 문구를 고칠 수 있다.
    #
    # **직전 RN 을 읽은 뒤에 한다** — 기준 판 이력(_prev_revs)이 있어야 하기 때문이다.
    # 예전에는 이 블록이 위 선별표 자리에 있어서, 아직 만들어지지 않은 _prev_revs 와
    # _CR 을 붙들고 C 리비전 건이 통째로 죽었다(UnboundLocalError — 실측 2026-09-21
    # PM86 86.07C8: 작업 폴더만 만들어지고 단계가 '실행 중' 으로 굳었다).
    if mail_id is not None and _c_rev:
        _sel_now = (get_state(mail_id) or {}).get("rn_select_rows") or []
        _sel_now = _prepend_c_revision_rows(
            mail_id, client, product, android, _c_base, version,
            _sel_now, _prev_revs)
        update_state(mail_id, rn_select_rows=_sel_now)

    # 우선순위: 실행 전 입력값 > 이미 저장된 값(RN 승인 입력) > 직전 버전 RN.
    # (예전에는 재실행 때 저장된 값을 통째로 덮어써 RN 승인 입력이 사라졌다.)
    # 직전 버전 RN 값은 이번 버전의 **추정치**다 — 빈 칸만 채우고 이미 있는 값은 두다.
    # (이번 버전 문서를 찾은 경우는 위 branch A 에서 refresh=True 로 갱신한다.)
    _merged, _msrc, _mnotes = merge_components(
        _st, _prev_seed, refresh=False, manual_now=_comps)
    _secv, _ssrc = "", ""          # 아래 mail_id 분기 밖에서도 쓰인다(미정의 방지)
    # 이번 회차 Beta OS 의 값이 **직전 버전 RN 에서 물려받은 추정치를 덮어쓴다**.
    # 보안패치는 아래 merge_security_patch 뒤에 한 번 더 적용한다(그쪽이 값을 다시 잡는다).
    _merged, _os_sec, _onotes = apply_os_props(
        user_id, product, android, version, _merged, "", mail_id=mail_id)
    _mnotes.extend(_onotes)
    if mail_id is not None:
        # ⑦ Newsletter·⑤⑥ 편집이 SDK 등에 사용
        update_state(mail_id, rn_components=_merged, rn_component_src=_msrc)
        # 전체 이력 RN 이 이어 붙일 이전 판들. 못 가져왔으면 이번 판만 있는 문서가 된다
        # (최초 버전이면 그게 정상이다).
        # **원문 그대로 넣는다** — 옛 판 문구에 태그를 추정해 채우던 것을 그만뒀다
        # (사용자 확정 2026-08-27). 이미 발행된 문서와 글자가 달라지면 안 된다.
        update_state(mail_id, rn_prev_revisions=_prev_revs,
                     rn_prev_source=_prev_note, rn_prev_error=_prev_err)
        # 입력 항목은 **전 모델 공통 표준표**로 고정한다(사용자 확정 2026-08-04).
        # 예전에는 그 제품 RN 의 섹션을 그대로 칸으로 썼는데, 제품마다 개수·이름·번호가
        # 달라 문서의 섹션 번호가 흔들렸다. 이제 목록은 표준이 정하고, **값만** 참고 RN 에서
        # 물려받는다(미해당 항목은 공백으로 남는다).
        from app.pipeline import rn_lookup as _RNL0

        update_state(mail_id, rn_input_fields=[dict(s) for s in _RNL0.STANDARD_SECTIONS])
        if _prev_fields:
            # 참고할 RN 을 찾았다는 것은 최초 출시가 아니라는 뜻이다 — 예전 실행이 남긴
            # rn_first_release 표시를 여기서 걷어낸다(한 번 켜지면 안 꺼져 계속 첫 RN 화면이 떴다).
            update_state(mail_id, rn_first_release=False)
        elif not _prev_err:
            # **최초 출시 표시는 조회가 정상이었을 때만** 켠다. 파일서버를 못 봤을 뿐인데
            # 최초 출시로 찍으면, 값이 있는 제품인데도 첫 RN 화면이 뜨고 사람이 전부
            # 직접 채우게 된다. 조회 오류면 그대로 두고 결과 화면에 사유를 띄운다.
            update_state(mail_id, rn_first_release=True)
        _secv, _ssrc, _snote = merge_security_patch(_st, _prev_sec, refresh=False)
        if _snote:
            _mnotes.append(_snote)
        # OS 에서 읽은 보안패치가 이긴다 — 직전 RN 값은 이번 회차의 추정치일 뿐이다.
        if _os_sec:
            if _os_sec != _secv:
                _mnotes.append(f"보안패치: {_secv or '(없음)'} → {_os_sec} (OS)")
            _secv, _ssrc = _os_sec, "auto"
        if _secv:
            update_state(mail_id, security_patch=_secv, security_patch_src=_ssrc)
    prev_rev = int(_st.get("rn_rev", 0) or 0)
    if prev_rev == 0:
        rev_num = 1                       # 최초 생성
    elif revision.strip():
        rev_num = prev_rev + 1            # 재작업 지시 있음 → 판 올림
    else:
        rev_num = prev_rev               # 단순 재실행 → 판 유지
    if mail_id is not None:
        update_state(mail_id, rn_rev=rev_num)

    # 버전+빌드날짜: "68.04 (Jun 26 2026)" (배포본 형식)
    # OS 빌드 이름의 날짜가 **가장 앞선다**. 배포 공지 날짜(deploy_date)는 공지한 날이라
    # 빌드 날짜와 다를 수 있고, 출시이력 날짜는 더 멀다(사용자 확정 2026-09-09).
    _st = get_state(mail_id) or _st        # 방금 apply_os_props 가 적은 값을 읽는다
    build_date = _fmt_build_date(_st.get("os_build_date", "")) or deploy_date
    version_full = _version_full(version, build_date or release_date)
    # 머리글 Date·Revision History Update = 출시 날짜(빌드날짜 우선, 없으면 출시버전 날짜).
    doc_date = _fmt_doc_date(build_date or release_date)
    # 직전 버전 RN 에서 가져온 보안패치도 이번 문서에 쓴다(_st 는 그 전에 읽은 값이라 폴백).
    security_patch = _secv or _st.get("security_patch", "") or _prev_sec

    # Revision History 라인: 사용자 초안이 있으면 그대로, 없으면 자동 큐레이션(+보안패치 첫 줄).
    # **사람이 직접 적어 둔 초안**만 그대로 쓴다(rn_revision_draft_manual).
    # rn_revision_draft 는 저장·재생성이 남기는 파생물이라, 그걸 '사용자 초안'으로 보면
    # 한 번 만들어진 문구가 계속 재사용돼 새 선별 결과가 영영 반영되지 않는다(실측).
    _draft = (_st.get("rn_revision_draft_manual") or "").strip()
    if _draft:
        revision_lines = _draft_to_lines(_draft)
        rev_source = "사용자 초안"
    elif first_release:
        # 최초 출시는 한 줄이다. 큐레이션도 보안패치 줄도 붙이지 않는다 — 실제 사내 RN 의
        # V1 항목이 다섯 제품 모두 이 한 줄뿐이었다(PM45·PM95·PM84P·MF52E·TR54).
        revision_lines = [rn_select.FIRST_RELEASE_LINE]
        rev_source = "최초 출시(고정 한 줄)"
    else:
        progress.step("Revision History 정리(큐레이션) 중…")
        # 규칙 A: RN(Sales)에는 **공개 선별 항목(public_changes)만** 넣는다.
        # 전체 issue(changes)가 아니라, 검수용 선별에서 공개(Y)로 뽑힌 것만 대상.
        curated = _curate_changes(client, product, version, public_changes)
        # 제외(사내발견 버그 등)가 있으면 개별 항목화하지 않고 안정성 한 줄로 뭉친다.
        n_excluded = len(sel_rows) - n_inc
        if n_excluded > 0 and not any("system stability" in c.lower() for c in curated):
            curated.append(
                "[System] Improved overall system stability and fixed minor bugs.")
        _sec_line = secpatch_line(security_patch)
        revision_lines = ([_sec_line] if _sec_line else []) + curated
        rev_source = "자동 큐레이션(공개 선별)"
    # RN 본문은 항상 영어여야 한다. 한국어가 섞이면(대개 Claude 번역 실패 = API 크레딧 소진 폴백으로
    # 원본 한국어가 그대로 남은 것) 조용히 넘어가지 말고 경고한다.
    _ko_lines = [ln for ln in revision_lines if re.search(r"[가-힣]", ln)]
    # 지정 버전과 배포 공지가 어긋났으면 **맨 앞에** 띄운다. 실측 job-24(PM86)는 작업이
    # 86.07C2 인데 공지가 86.07 뿐이라 조용히 86.07 로 나갔고, 승인·파일서버 발행까지
    # 지난 뒤에야 드러났다. 어느 쪽이 맞든 사람이 한 번은 보고 넘어가야 한다.
    _ver_warn = ""
    if ver_note and VER_CONFLICT_MARK in ver_note:
        _ver_warn = ver_note.split(" · ")[-1].strip() + "\n"

    _ko_warn = ""
    if _ko_lines:
        _ko_warn = (f"⚠ RN 본문에 한국어 {len(_ko_lines)}줄 감지 — Claude 영어 번역이 실패한 것으로 보입니다"
                    "(Anthropic API 크레딧 잔액을 확인하세요). 크레딧 충전 후 RN 을 재생성하면 영어로 나옵니다.\n")
    # 선별 판정을 못 받은 이슈가 있으면 **반드시** 알린다. 조용히 두면 '전부 제외'와
    # 구분되지 않아 공개 항목이 통째로 비어도 정상처럼 보인다(실측 사례).
    _sel_warn = ""
    if n_unjudged:
        _sel_warn = (
            f"⚠ 이슈 {n_unjudged}건이 선별 판정을 받지 못했습니다(미판정) — 선별표 비고에 표시됩니다. "
            "기본값대로 **반영** 상태이니 '검수하기'에서 문구를 확인하고 뺄 것은 빼 주세요"
            "(또는 Release Note 제작을 재실행하세요).\n"
            + "".join(f"   · {m}\n" for m in sel_errors[:5])
        )
    if mail_id is not None:
        update_state(mail_id, rn_revision_lines=revision_lines, version_full=version_full,
                     rn_doc_date=doc_date)
        # 이력: ② Release Note 최초 생성(또는 재실행) — initial 스냅샷(공개 문구 + 입력 값).
        from app.pipeline import history as _H
        from app.pipeline.state import get_state as _gs
        _H.record(mail_id, "rn", "생성", summary="Release Note 생성",
                  text=_H.text_from_rn(_gs(mail_id) or {}))

    # RN = 템플릿(rn_docx_full)을 revision_lines 로 채워 **docx** 생성(build_full_rn 이 한다).
    # PDF 는 RN 승인 시에만 만든다(finalize_full_pdf) — 그 전까지는 docx 로 관리·수정한다.
    # 이 단계의 산출물은 **전체 이력 RN 하나**다(2026-08-27 단계 분리).
    # RN for Sales 는 이 뒤 [RN for Sales 제작] 단계가 만든다 — 여기서 같이 만들면
    # 승인 전 판이 남아, 화면에서 그것을 보고 판단하게 된다.
    #
    # 전체 이력 RN — 직전 버전 RN 의 판들 + 이번 판(반영 항목만).
    # 이번 판의 내용은 RN for Sales 와 같다 — 미반영은 어느 쪽에도 실리지 않는다.
    progress.step("전체 이력 Release Note 만드는 중…")
    _full_path, _full_err = build_full_rn(mail_id, workdir)
    rn_line = ""
    if _full_path:
        artifact_type, artifact_path = "docx", str(_full_path)
        # 최초 제작본을 **별도 파일로 고정**(재생성에 안 덮이게) — 작업 폴더에 남기는 기록이다.
        # **화면·결과물 목록에는 내보내지 않는다**(사용자 확정 2026-08-26). 갱신되지 않아
        # 선별 전 내용이 그대로 남는 판이라, 실제 RN 옆에 두면 그것을 보고 "미반영이 아직
        # 문서에 있다"고 읽게 된다(실측 job-21). 작업을 다시 하면 RN 도 다시 만들어진다.
        from app.pipeline.state import update_state

        _init = workdir / (RNL_STD(product, android, vtag) + "_initial.docx")
        try:
            atomic_write_bytes(_init, Path(_full_path).read_bytes())
            update_state(mail_id, rn_initial_docx_path=str(_init),
                         rn_initial_docx_name=_init.name)
        except OSError:
            pass                       # 기록용 사본이라 실패해도 단계는 진행한다
    else:
        artifact_type, artifact_path = "", ""
        rn_line = f"RN(docx) 생성 실패: {_full_err}"
    full_line = (f"전체 이력 RN: {_full_path} (판 {len(_prev_revs) + 1}개)"
                 if _full_path else f"전체 이력 RN 생성 실패: {_full_err}")
    # 이전 판을 하나도 못 이어받았으면 그건 **전체 이력이 아니다**. 예전에는 '판 1개'로만
    # 적혀 최초 출시와 구분되지 않아, 이력이 통째로 빠진 문서가 정상처럼 보였다.
    _first_rel = not _prev_fields and not _prev_err
    if _full_path and not _prev_revs and not _first_rel:
        _why_empty = (_prev_err or _prev_note or "").replace("\n", " ")
        full_line += ("\n  ⚠ **이전 판을 하나도 이어받지 못했습니다 — "
                      "이 문서는 이번 판만 담고 있습니다.** " + _why_empty)

    # 근거(evidence): 어떤 메일을 봤는지 + 어떤 이슈를 봤는지 명시
    issue_list = ", ".join("#" + str(i) for i in issue_ids[:30])
    if len(issue_ids) > 30:
        issue_list += f" … 외 {len(issue_ids) - 30}건"
    evidence = ["[근거]"]
    if rel.get("base_version"):
        evidence.append(
            f"· 버전 판별: 출시본 {rel['base_version']}({rel.get('base_date') or '날짜?'}, "
            f"{rel.get('source') or '?'}) + FIH 승인일 → {version}"
        )
    if deploy:
        evidence.append(
            f"· 참조 메일(개선사항 출처): #{deploy['mail_id']} \"{deploy['subject']}\""
            + (f" ({deploy_date})" if deploy_date else "")
        )
    else:
        evidence.append(f"· 참조 메일: {source}")
    if not first_release:
        evidence.append(f"· 확인한 개선사항 이슈 {len(ordered)}건 "
                        f"(상한 {_MAX_ISSUES}): {issue_list}")
    if related_map:
        evidence.append(
            f"· 생성자/해결자 추적을 위해 관계 이슈 {len(related_map)}건 추가 확인"
        )
    if first_release:
        evidence.append(
            f"· 공개 RN 선별: **최초 출시** — '{rn_select.FIRST_RELEASE_LINE}' 한 줄로 "
            "고정했습니다(이슈 선별을 하지 않습니다)."
            "\n  ※ 이전 판이 없어 '무엇이 달라졌나'가 성립하지 않습니다. 실제 사내 RN 의 "
            "V1 항목도 이 한 줄뿐입니다."
        )
    else:
        evidence.append(
            # 미판정도 기본값이 반영이라 n_inc 에 들어 있다 — 제외는 그냥 나머지다
            # (예전처럼 미판정을 또 빼면 음수가 나온다).
            f"· 공개 RN 선별: 포함(Y) {n_inc}건 / 제외 {len(sel_rows) - n_inc}건"
            + (f" · 배포 공지의 **번호 없는 항목 {len(text_items)}건** 추가"
               if text_items else "")
            + (f" / **미판정 {n_unjudged}건**" if n_unjudged else "")
            + " (결함 수정은 'stability'로 뭉침)"
            + ("\n  ※ **기본값은 전부 반영(Y)** 입니다 — 빼실 항목은 선별표에서 미반영으로 "
               "바꾸세요(각 행 비고에 판정 사유가 남아 있습니다)."
               if not _carry else
               f"\n  ※ 선별표에서 사람이 정한 값 {len(_carry)}건을 그대로 유지했습니다.")
        )
    if _mnotes:
        evidence.append("· 값 갱신(자동으로 채웠던 항목): " + " · ".join(_mnotes))
    if _prev_fields or _prev_seed or _prev_sec:
        evidence.append(
            f"· 입력 항목: {_prev_note} — {len(_prev_fields)}개 "
            + "(" + ", ".join(f.get("label", "") for f in _prev_fields[:12]) + ")"
            + " · 실행 전 입력값·RN 승인 입력이 있으면 그쪽이 우선"
        )
    elif _prev_err:
        # 조회 실패 — 최초 출시와 구분해서 보여야 사람이 '값이 원래 없는 것'으로 넘기지 않는다.
        evidence.append(
            f"· ⚠ 입력 항목: **파일서버에서 입력값을 못 가져왔습니다(최초 출시 아님)** — {_prev_err}"
            " · 값을 직접 채우거나, 파일서버 확인 후 ②를 다시 실행하세요."
        )
    elif _prev_note:
        evidence.append(f"· 입력 항목: 이전 RN 에서 못 가져옴 — {_prev_note}")

    summary = (
        header + _ver_warn + cap_warn + _ko_warn + _sel_warn
        + f"버전: {version}\n근거요약: {source}\n\n"
        + "\n".join(evidence) + "\n\n"
        + ("최초 출시 — Redmine 이슈를 읽지 않았습니다. 공개 항목 1건(고정).\n"
           if first_release else
           f"개선사항 이슈 {len(issue_ids)}건 → Redmine 상세 {len(ordered)}건 "
           f"→ 변경 {len(changes)}건 → 공개 항목 {n_inc}건.\n")
        + (f"{rn_line}\n" if rn_line else "") + f"{full_line}\n"
        f"※ 공개 RN 선별표(이슈·포함·공개문구)는 '검수하기' 팝업에 표로 표시됩니다.\n"
        f"Revision History: {rev_source} · {len(revision_lines)}줄 (버전표기 {version_full})\n"
        "※ Revision History 는 '검수하기' 팝업에서 편집 후 RN 재생성할 수 있습니다. "
        "컴포넌트 버전(Audio/Touch/Wi-Fi/Known Issues)은 실행 전 입력값을 사용합니다."
    )
    return StageOutput(result_text=summary, artifact_type=artifact_type, artifact_path=artifact_path)
