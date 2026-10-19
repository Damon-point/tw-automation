"""공개 Release Note 항목 선별 — 사람 draft 형식(번호/이슈/작성자/고객/구분/포함여부/비고/redmine).

선별 규칙(사용자 확정):
  · 포함 = (a) 신규 기능 (b) 기본값 변경·동작 로직 개선 (c) 고객 요청 중 일반 OS 에 들어가는 개선
           → (a)(b) 는 **적용 모델 범위**를 함께 확인한다(대상 제품이 범위 밖이면 제외).
  · 제외 = (1) **작성자 팀이 DQA**(사내 검증 발견) (2) VoC 중 **특정 고객 전용 OS 한정 기능**
           (3) 내부·개발·QA 도구/테스트/인증(EmSettings·EmHealthReport·Emlog·DirectClone·VTS 등)
           (4) 타제품 전용 (5) 동작 규격은 그대로 두고 결함만 바로잡은 수정

  ※ VoC 는 그것만으로 제외하지 않는다 — 특정 고객 전용 OS 에만 있는 기능일 때만 제외한다.
  ※ '결함 수정'과 '기본값·로직 변경'의 경계: 원래 의도대로 되게 고친 것은 제외,
     동작 규격이나 기본값 자체가 바뀐 것은 포함.

작성자(팀)=사람→팀 명단(결정적), 구분=제목 접두어([VoC]/[ISSUE]), 고객/포함여부/비고/공개문구=Claude.
"""
from __future__ import annotations

import re
import time

import anthropic

from app import progress
from app.pipeline.ai_edit import _extract_json

# 한 번에 판정할 이슈 수. 12건이면 응답 JSON(항목마다 영문 public·apply_note)이 길어져
# max_tokens 에 걸려 잘리고, 잘린 JSON 은 파싱 실패 → **그 청크 전체가 미판정**이 됐다
# (실측: 16건 작업에서 앞 12건이 통째로 판정 없이 '미반영'으로 남음). 그래서 8건으로 줄인다.
_CHUNK = 8
_MAX_TOKENS = 4000
_RATE_RETRIES = 4


def _create_with_retry(client, **kwargs):
    """rate limit(429)·일시적 서버 오류는 대기 후 재시도. 끝까지 실패하면 예외를 올린다.

    예전에는 재시도 없이 한 번만 호출하고 실패를 조용히 삼켜서, 청크 하나가 실패하면
    그 안의 이슈가 모두 '미반영'으로 보였다(실패와 전부제외가 구분되지 않았다).
    """
    last: Exception | None = None
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
        except anthropic.APIStatusError as e:
            last = e
            if e.status_code < 500 or attempt >= _RATE_RETRIES:
                raise
            time.sleep(5 * (attempt + 1))
    raise last


#: 태그가 붙어 있는가 — '[WLAN] ...' · '[A][B] ...'
_TAG_RE = re.compile(r"^\s*(?:\[[A-Za-z0-9 /&+._-]+\]\s*)+")


def strip_tag(line: str) -> str:
    """앞의 [Module] 태그를 떼어낸다 — 뉴스레터처럼 태그가 어울리지 않는 곳에서 쓴다."""
    return _TAG_RE.sub("", (line or "").strip()).strip()


_REDMINE = "https://issue.pointmobile.co.kr/issues/"
_GUBUN_RE = re.compile(r"\[\s*(voc|issue)\s*\]", re.I)

_SELECT_SYS = (
    "당신은 포인트모바일 공개 Release Note 선별기입니다. 각 이슈를 고객용 RN 에 실을지 판정합니다.\n"
    "■ 먼저 제외를 본다(하나라도 걸리면 N):\n"
    "  (1) **작성자 팀이 DQA** — 사내 검증 과정에서 발견한 건이다. 다른 조건과 무관하게 N(사유 'DQA').\n"
    "  (2) VoC 중 **특정 고객 전용 OS(커스텀 빌드)에만 해당하는 기능** — 일반 고객 단말에는 없는 기능이라 N"
    "(사유 '특정고객OS'). ※ **VoC 라는 것만으로는 제외하지 마세요.** 고객이 제기했더라도 일반 OS 에 "
    "들어가는 변경이면 아래 포함 조건으로 판단합니다.\n"
    "  (3) 내부·개발·QA 도구/테스트/인증(EmSettings·EmHealthReport·Emlog·DirectClone·VTS·GMS 인증 등) "
    "→ N(사유 '내부(도구)').\n"
    "  (4) 다른 제품 전용(제목·본문의 적용 모델에 대상 제품이 없음) → N(사유 '타제품').\n"
    "  (5) **동작 규격은 그대로 두고 결함만 바로잡은 수정**(원래 의도대로 되게 고친 것) → N(사유 '결함수정'). "
    "개별 항목화하지 않습니다. 이런 건들을 뭉치는 'Improved system stability and bug fixed' 한 줄은 "
    "**시스템이 따로** 붙이니 당신은 그 문구를 만들지 마세요.\n"
    "■ 위에 걸리지 않으면 포함(Y):\n"
    "  (a) **신규 기능** — 고객이 새로 쓸 수 있게 된 기능.\n"
    "  (b) **기본값이 바뀌었거나 동작 로직이 개선된 것** — 고객이 체감하는 동작 변화. "
    "(5)와의 경계는 '규격이 바뀌었는가'다. 기본값·정책·동작 방식 자체가 바뀌면 (b) 로 **포함**한다.\n"
    "  (c) 고객이 요청·보고해 반영된 개선 중 **일반 OS 에 들어가는** 것.\n"
    "■ (a)(b) 는 **적용 모델 범위를 반드시 함께 확인**하세요. 대상 제품이 적용 범위에 없으면 (4)로 N, "
    "범위에 있으나 일부 모델·조건에만 해당하면 Y 로 두고 reason 에 그 범위를 적으세요"
    "(예: 'PM95/PM67 한정').\n"
    "각 이슈에 include(Y/N), customer(특정 고객명 있으면; 없으면 ''), reason(한국어 간단 사유 — 제외면 "
    "'DQA'/'특정고객OS'/'내부(도구)'/'타제품'/'결함수정', 포함이면 근거와 적용 모델 범위), "
    "public 을 판정하세요.\n"
    "추가로 **sensitive(Y/N)** = 원문 표현이 고객에게 그대로 나가면 안 되는 민감 정보인가"
    "(내부 코드명·모듈명, 보안 취약점 상세, 특정 고객명, 사내 용어/비속어 등). "
    "**apply_note** = 민감하지만 **표현을 다듬으면 공개 가능한** 경우, 고객 공개용으로 순화한 "
    "영문 한 줄(그대로 공개해도 되면 '' 로 두고, 다듬어도 공개 불가한 민감 정보면 '' 로 두고 include=N). "
    "즉 민감하더라도 순화해 실을 수 있으면 include=Y 로 하고 apply_note 에 순화 문구를 넣으세요.\n"
    "**문구(public·apply_note) 앞에 '[WLAN]' · '[System]' 같은 [모듈] 태그를 붙이지 "
    "마세요.** 실제 배포 Release Note 도 대부분 태그 없이 씁니다. 문장으로 바로 "
    "시작하세요(사용자 확정 2026-08-27).\n"
    "**public = '이 항목이 포함될 경우 RN 에 실릴, 그 이슈만의 구체적인 영문 한 줄'** 입니다. "
    "포함/제외와 무관하게 **항상 그 이슈의 실제 내용**을 쓰세요(사람이 나중에 포함으로 바꿀 수 있으므로). "
    "'Improved system stability and bug fixed' 처럼 **여러 항목에 똑같이 쓰이는 일반·뭉뚱그린 문구는 "
    "public 에 절대 쓰지 마세요** — 어떤 이슈인지 알 수 있게 항목마다 다른 문구여야 합니다. "
    "지어내지 말고 주어진 제목/설명 근거로만. JSON 으로만 답하세요."
)


def team_of(person: str) -> str:
    """Redmine 작성자 → 팀. 명단은 [설정 → 사내 인원]에서 관리한다.

    예전에는 이 파일에 `_ROSTER_RAW` 로 하드코딩돼 있었다(2026-08-06 명부로 이관).
    선별 프롬프트가 '작성자 팀이 DQA 면 제외'를 첫 규칙으로 쓰므로 값이 결과를 바꾼다.
    """
    from app import staff

    return staff.team_of(person)


def gubun_of(title: str) -> str:
    m = _GUBUN_RE.search(title or "")
    return m.group(1).upper() if m else ""


def _compact(iss: dict, team: str, gubun: str) -> str:
    return (
        f"#{iss['id']} | 팀:{team} | 구분:{gubun or '?'} | cat:{iss.get('category', '')}\n"
        f"  제목: {iss.get('subject', '')}\n"
        f"  설명: {(iss.get('description') or '')[:150]}"
    )


def classify_issues(client, product: str, android: str, ordered: list[dict],
                    model: str, chunk: int = _CHUNK,
                    errors: list | None = None) -> dict:
    """이슈들을 Claude 로 판정. 반환 {id: {include(bool), customer, reason, public}}.

    errors 를 주면 **판정을 못 받은 이슈**의 사유를 append 한다. 호출부는 이 목록으로
    '판정 실패'와 '전부 제외'를 구분해 사람에게 알려야 한다(조용히 미반영으로 두면 안 된다).
    """
    metas = [(iss, team_of(iss.get("created_by", "")), gubun_of(iss.get("subject", "")))
             for iss in ordered]
    results: dict[str, dict] = {}
    for k in range(0, len(metas), chunk):
        # 청크마다 몇 건까지 왔는지 알린다 — 이슈가 많으면 몇 분이 걸려,
        # 표시가 안 바뀌면 멈춘 것으로 보인다.
        progress.step(f"Claude 이슈 판정 중… ({min(k + chunk, len(metas))}/{len(metas)}건)")
        part = metas[k:k + chunk]
        blocks = "\n\n".join(_compact(i, t, g) for i, t, g in part)
        prompt = (
            f"대상 제품: {product} A{android}\n아래 이슈들을 판정하세요.\n\n{blocks}\n\n"
            '반드시 JSON: {"items":[{"id":"번호","include":"Y|N","sensitive":"Y|N",'
            '"customer":"","reason":"..","public":"..","apply_note":".."}]}'
        )
        ids = [str(i["id"]) for i, _t, _g in part]
        # 태그를 못 받았을 때 이슈 제목에서 모듈을 추론하려고 제목을 들고 있는다.
        titles = {str(i["id"]): str(i.get("subject") or "") for i, _t, _g in part}
        try:
            resp = _create_with_retry(
                client, model=model, max_tokens=_MAX_TOKENS, system=_SELECT_SYS,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
            parsed = _extract_json(text)
            if parsed is None:
                if errors is not None:
                    errors.append(
                        f"이슈 {', '.join('#' + i for i in ids)} — 응답 JSON 해석 실패"
                        "(응답이 잘렸을 수 있음)"
                    )
                continue
            for it in parsed.get("items") or []:
                iid = str(it.get("id", "")).lstrip("#").strip()
                if iid:
                    results[iid] = {
                        "include": str(it.get("include", "")).upper().startswith("Y"),
                        "sensitive": str(it.get("sensitive", "")).upper().startswith("Y"),
                        "customer": str(it.get("customer", "")).strip(),
                        "reason": str(it.get("reason", "")).strip(),
                        "public": strip_tag(str(it.get("public", ""))),
                        "apply_note": strip_tag(str(it.get("apply_note", ""))),
                    }
            # 보낸 이슈 중 응답에 빠진 것도 '미판정'이다(부분 응답).
            missing = [i for i in ids if i not in results]
            if missing and errors is not None:
                errors.append(
                    f"이슈 {', '.join('#' + i for i in missing)} — 응답에 판정이 없음")
        except Exception as e:  # noqa: BLE001
            if errors is not None:
                errors.append(
                    f"이슈 {', '.join('#' + i for i in ids)} — 판정 호출 실패: "
                    f"{type(e).__name__}: {e}"
                )
    return results


def classify_lines(client, product: str, android: str, lines: list[str],
                   model: str) -> list[dict]:
    """이미 공개된 RN 변경 문구들의 **민감도만** 판정(기존 RN 마지막 Revision 표 정리용).

    반환: lines 순서의 [{sensitive(bool), apply_note(str)}].
      · sensitive = 고객에게 그대로 나가면 안 되는 표현(내부 코드명·보안취약점 상세·특정 고객명 등)
      · apply_note = 민감해 순화가 필요하면 순화한 영문 한 줄(아니면 '')
    """
    out = [{"sensitive": False, "apply_note": ""} for _ in lines]
    if not lines:
        return out
    blocks = "\n".join(f"{i + 1}. {ln}" for i, ln in enumerate(lines))
    prompt = (
        f"대상 제품: {product} A{android}\n"
        "아래는 이미 공개된 Release Note 변경 문구입니다. 각 문구가 고객에게 그대로 나가도 되는지 "
        "민감도를 판정하세요. sensitive(Y/N) = 내부 코드명·모듈명, 보안 취약점 상세, 특정 고객명, "
        "사내 용어 등 민감 표현이 있는가. apply_note = 민감해서 순화가 필요하면 고객 공개용으로 순화한 "
        "영문 한 줄(그대로 공개해도 되면 '').\n\n"
        f"{blocks}\n\n"
        '반드시 JSON: {"items":[{"no":"번호","sensitive":"Y|N","apply_note":".."}]}'
    )
    try:
        resp = _create_with_retry(
            client,
            model=model, max_tokens=1500, system=_SELECT_SYS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        for it in (_extract_json(text) or {}).get("items") or []:
            try:
                idx = int(str(it.get("no", "")).strip()) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(out):
                out[idx] = {
                    "sensitive": str(it.get("sensitive", "")).upper().startswith("Y"),
                    "apply_note": strip_tag(str(it.get("apply_note", ""))),
                }
    except Exception:  # noqa: BLE001
        pass
    return out


def match_lines_to_issues(client, product: str, lines: list[str],
                          issues: list[dict], model: str) -> list[str]:
    """RN 변경 문구를 근거 이슈(제목)와 매칭 — 기존 RN 표에 redmine 번호를 채우기 위함.

    반환: lines 순서의 [issue_id or ''] (확실하지 않으면 '' — 억지 매칭 금지).
    """
    out = ["" for _ in lines]
    if not lines or not issues:
        return out
    iss_blocks = "\n".join(f"#{i.get('id')}: {i.get('subject', '')}" for i in issues)
    line_blocks = "\n".join(f"{k + 1}. {ln}" for k, ln in enumerate(lines))
    prompt = (
        f"제품 {product}. 아래 '변경 문구'가 어떤 '이슈'에서 비롯된 것인지 매칭하세요. "
        "문구와 이슈 내용이 분명히 대응할 때만 그 이슈 번호를 쓰고, 확실하지 않으면 빈 문자열로 두세요"
        "(억지로 매칭하지 마세요).\n\n"
        f"[이슈 목록]\n{iss_blocks}\n\n[변경 문구]\n{line_blocks}\n\n"
        '반드시 JSON: {"items":[{"no":"번호","id":"이슈번호 또는 빈문자열"}]}'
    )
    try:
        resp = _create_with_retry(
            client,
            model=model, max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        valid = {str(i.get("id")) for i in issues}
        for it in (_extract_json(text) or {}).get("items") or []:
            try:
                idx = int(str(it.get("no", "")).strip()) - 1
            except (TypeError, ValueError):
                continue
            iid = str(it.get("id", "")).lstrip("#").strip()
            if 0 <= idx < len(out) and iid and iid in valid:
                out[idx] = iid
    except Exception:  # noqa: BLE001
        pass
    return out


def public_lines_for(client, product: str, android: str, items: list[dict],
                     model: str) -> dict:
    """제외였다가 새로 포함된 항목의 **고객용 영문 1줄**을 만든다.

    선별 시 public 을 못 받은 예전 데이터를 위한 보정. items=[{"no","issue"}] → {no: line}.
    """
    if not items:
        return {}
    blocks = "\n".join(f'{it["no"]}. {it["issue"]}' for it in items)
    _issue_by_no = {str(it["no"]): str(it.get("issue") or "") for it in items}
    prompt = (
        f"대상 제품: {product} A{android}\n"
        "아래는 고객용 Release Note 에 새로 포함하기로 한 변경 항목(사내 이슈 제목)입니다.\n"
        "각 항목을 고객이 읽을 **영문 릴리스노트 한 줄**로 바꾸세요. "
        "**'[모듈]' 태그는 붙이지 마세요** — 문장으로 바로 시작합니다. "
        "사내 용어·이슈번호·담당자는 빼고, 제품 기능 관점에서 간결하게. "
        "원문에 없는 내용은 지어내지 마세요.\n\n"
        f"{blocks}\n\n"
        '반드시 JSON: {"items":[{"no":"번호","public":"영문 한 줄"}]}'
    )
    out: dict = {}
    try:
        resp = _create_with_retry(
            client,
            model=model, max_tokens=1500, system=_SELECT_SYS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        for it in (_extract_json(text) or {}).get("items") or []:
            no = str(it.get("no", "")).strip()
            line = strip_tag(str(it.get("public", "")))
            if no and line:
                out[no] = line
    except Exception:  # noqa: BLE001
        pass
    return out


# ── 최초 출시(신규 OS) ──────────────────────────────────────────────────────
#: 최초 출시 RN 의 공개 항목은 **이 한 줄뿐**이다. 지어낸 규칙이 아니라 실제 사내
#: RN 이력에서 확인한 것이다(실측 2026-08-24, Revision History V1:
#: PM45 45.70 · PM95 95.51 · PM84P PM84PG15.01 · MF52E MF52EG15.00 · TR54 TR54G15.01
#: — 다섯 제품 모두 이 한 줄만 있고 보안패치 줄조차 없다).
#: 최초 출시는 '무엇이 달라졌나'가 성립하지 않으므로 선별할 것도 없다.
FIRST_RELEASE_LINE = "1st official release"
FIRST_RELEASE_REASON = "최초 출시 — 이전 판이 없어 선별 대상이 없습니다(한 줄 고정)"


def first_release_rows() -> list[dict]:
    """최초 출시 선별표 — 한 줄. rn_select_rows 와 같은 모양으로 돌려준다."""
    return [{
        "no": 1, "issue": FIRST_RELEASE_LINE, "team": "", "customer": "",
        "gubun": "", "include": "Y", "reason": FIRST_RELEASE_REASON,
        "public": FIRST_RELEASE_LINE, "redmine": "",
        "sensitive": False, "apply_note": FIRST_RELEASE_LINE,
        # 재실행 때 Redmine 이슈로 복원되지 않는 항목이라는 표시(번호 없는 항목과 같다).
        "from_mail": True,
    }]


# 판정을 못 받은 행의 사유 — 빈칸으로 두면 '검토해서 제외한 것'과 구분되지 않는다.
#: 비고가 이것으로 시작하면 미판정 행이다(사유 문구가 바뀌어도 판정이 깨지지 않게 분리).
UNJUDGED_PREFIX = "⚠ 미판정"
UNJUDGED_REASON = UNJUDGED_PREFIX + "(판정 실패) — 사람 확인 필요"


# ── 선별표 불변식 ────────────────────────────────────────────────────────────
# 아래 두 가지는 **사용자 확정 규칙**이다(2026-08-04 · 2026-08-24 재확인).
#
#   ① 기본값은 반영(Y) — 전 항목을 실어 두고 사람이 빼는 방향으로 검수한다.
#   ② '수정 내용'(apply_note)은 비워 두지 않는다 — 반영이면 RN 에 그대로 나갈 문구,
#      미반영이면 '반영하면 이렇게 들어갑니다' 미리보기가 항상 보여야 한다.
#
# 이 규칙이 여러 군데(②생성 · 중간 검수 저장 · RN 승인 저장)에 흩어져 있어서 한 곳만
# 고치다 다시 어긋났다. 그래서 판정은 normalize_rows() 하나로 모으고, 행을 만들거나
# 저장하는 경로는 **전부 이 함수를 지나게** 한다.

#: 여러 항목을 하나로 뭉치는 일반 문구. 개별 항목의 '그 이슈만의 문구'로는 쓰지 않는다
#: (어떤 이슈인지 알 수 없고, 여러 행이 같은 줄이 된다 — 실측 4회 중복).
#: 다만 그 행이 실제로 뭉쳐 나가는 행이면 '수정 내용'에 이 문구를 그대로 보여 준다.
GENERIC_RE = re.compile(
    r"improved\s+(?:overall\s+)?system\s+stability|fixed\s+minor\s+bugs?|bugs?\s+fixed",
    re.I,
)


def is_generic_line(s: str) -> bool:
    """'Improved system stability and bug fixed' 류의 뭉뚱그린 일반 문구인가."""
    return bool(GENERIC_RE.search(s or ""))


# ── 시스템 항목 ─────────────────────────────────────────────────────────────
# 이슈에서 온 것이 아니라 **규칙으로 붙던 두 줄**이다. 예전에는 코드가 문서에 직접 끼워
# 넣어서 사람이 뺄 수도, 문구를 고칠 수도 없었다. 선별표에 행으로 올려 다른 항목과 똑같이
# 반영/미반영을 정하고 문구를 고칠 수 있게 한다(사용자 확정 2026-08-26).
#
#   · security  = '[Security] Updated security patches to …' — 값은 [입력 요청]에서 온다
#   · stability = '[System] Improved overall system stability …' — 뺀 항목을 뭉쳐 알리는 줄
SYSTEM_KINDS = ("security", "stability")

STABILITY_LINE = "Improved overall system stability and fixed minor bugs."

#: 보안패치 줄인가 — 표기가 흔들려도('patch'/'patches', ISO/영문 날짜) 같은 줄로 본다.
#: 앞에 '[Security] ' 같은 모듈 태그가 붙어도 같은 줄이다(2026-08-19 태그 상시화).
SECPATCH_RE = re.compile(
    r"^\s*(?:\[[A-Za-z0-9 /&+._-]+\]\s*)*updated\s+security\s+patch(es)?\s+to\b", re.I)

_SYS_ISSUE = {
    "security": "보안 패치 안내 (시스템 항목)",
    "stability": "그 밖의 수정 뭉침 (시스템 항목)",
}
_SYS_REASON = {
    "security": "이슈가 아니라 규칙으로 붙는 줄 — 문구의 날짜는 [입력 요청]의 "
                "Android security patch level 값에서 옵니다",
    "stability": "이슈가 아니라 규칙으로 붙는 줄 — 미반영으로 뺀 항목이 있다는 것을 "
                 "한 줄로 알립니다",
}


def system_rows(rows) -> dict:
    """선별표의 시스템 항목만 {kind: row}."""
    return {r.get("kind"): r for r in (rows or []) if r.get("kind") in SYSTEM_KINDS}


def ensure_system_rows(rows, *, security_line: str = "", prev=None) -> list[dict]:
    """선별표 끝에 시스템 항목 두 줄을 놓는다. 이미 있으면 **사람이 정한 값을 지킨다**.

    · security  — security_line 이 있을 때만 둔다(보안패치 값이 없으면 할 말이 없다).
      사람이 문구를 고치지 않았으면(= 아직 보안패치 줄 모양이면) 최신 값으로 새로 쓴다.
      고쳐 썼으면 그대로 둔다.
    · stability — 늘 둔다. 처음 만들 때의 기본값은 **오늘까지의 동작 그대로** —
      미반영이 하나라도 있으면 반영(Y), 하나도 없으면 미반영(X). 그 뒤로는 사람 몫이다.

    prev 를 주면(②를 다시 돌릴 때) 거기 있던 사람의 선택·문구를 물려받는다.
    반환은 새 목록이다(원본을 건드리지 않는다).
    """
    out = [dict(r) for r in (rows or []) if r.get("kind") not in SYSTEM_KINDS]
    cur = system_rows(rows)
    old = system_rows(prev) if prev else {}

    def _mk(kind: str, line: str, include: str) -> dict:
        return {"no": 0, "issue": _SYS_ISSUE[kind], "team": "", "customer": "",
                "gubun": "", "include": include, "reason": _SYS_REASON[kind],
                "public": line, "redmine": "", "sensitive": False,
                "apply_note": line, "kind": kind}

    # ── 보안 패치 ──
    if security_line:
        r = dict(cur.get("security") or old.get("security") or {})
        if r:
            note = str(r.get("apply_note") or "").strip()
            # **사람이 고쳐 쓴 문구는 지킨다.** 예전에는 '보안패치처럼 생겼으면 자동
            # 문구' 로 보고 덮어써서, 표에 원하는 표기를 적어 넣어도 다음 갱신에
            # 시스템 표기로 되돌아갔다(사용자 지적 2026-09-21 · 86.53).
            # 무엇이 자동 문구였는지는 auto 에 적어 둔다 — 그것과 같을 때만 갱신한다.
            _prev_auto = str(r.get("auto") or "").strip()
            _untouched = (not note) or (note == _prev_auto if _prev_auto
                                        else bool(SECPATCH_RE.match(note)))
            if _untouched:
                r["apply_note"] = security_line
            r["public"] = str(r.get("apply_note") or security_line)
            r["issue"] = _SYS_ISSUE["security"]
            r["reason"] = _SYS_REASON["security"]
            r["kind"] = "security"
            r["include"] = "X" if str(r.get("include") or "").upper() == "X" else "Y"
        else:
            # 표에 이미 보안패치 줄이 **일반 행**으로 있으면 그 문구를 물려받는다.
            # 사람이 만들어 둔 RN 을 가져다 쓰는 경우가 그렇다 — 그 문서의 표기를
            # 시스템 표기로 바꿔 버리면 안 된다(실측 86.53: 문서는
            # 'Updated security patches to 2026-08-05.' 인데 시스템이
            # 'Updated security patches to August 5, 2026.' 로 바꿔 내보냈다).
            _borrowed = ""
            for _x in (rows or []):
                if _x.get("kind") in SYSTEM_KINDS:
                    continue
                _t = str(_x.get("apply_note") or _x.get("public") or "").strip()
                if _t and SECPATCH_RE.match(_t):
                    _borrowed = _t
                    break
            r = _mk("security", _borrowed or security_line, "Y")
        r["auto"] = security_line
        out.append(r)


    # ── 그 밖의 수정 뭉침 ──
    r = dict(cur.get("stability") or old.get("stability") or {})
    if r:
        note = str(r.get("apply_note") or "").strip() or STABILITY_LINE
        r.update({"apply_note": note, "public": STABILITY_LINE, "kind": "stability",
                  "issue": _SYS_ISSUE["stability"], "reason": _SYS_REASON["stability"]})
        r["include"] = "X" if str(r.get("include") or "").upper() == "X" else "Y"
    else:
        # 처음 놓는 순간의 기본값 — 오늘까지의 규칙을 그대로 옮긴다.
        has_excluded = any(x.get("include") == "X" for x in out)
        r = _mk("stability", STABILITY_LINE, "Y" if has_excluded else "X")
    out.append(r)

    # 번호는 **시스템 항목에만** 새로 준다 — 이슈 행의 번호는 사람이 눈으로 좇는 값이라
    # 건드리지 않는다(화면의 토글·입력칸도 이 번호를 열쇠로 쓴다).
    used = {int(x["no"]) for x in out if str(x.get("no", "")).strip().lstrip("-").isdigit()
            and x.get("kind") not in SYSTEM_KINDS}
    nxt = (max(used) if used else 0) + 1
    for x in out:
        if x.get("kind") in SYSTEM_KINDS:
            x["no"] = nxt
            nxt += 1
    return out


def default_note(*, apply_note: str = "", public: str = "", issue: str = "") -> str:
    """선별표 '수정 내용' 의 값. 셋 다 비었을 때만 빈 문자열이 된다.

    우선순위(사용자 확정): 순화 문구(apply_note) → 그 항목의 공개 문구(public)
    → 뭉뚱그린 일반 문구 → 이슈 제목.

    일반 문구를 **맨 뒤가 아니라 이슈 제목 앞**에 두는 이유: 결함 수정처럼 한 줄로
    뭉쳐 나가는 항목은 실제로 그 문구로 RN 에 실린다. 여기에 이슈 제목을 넣어 버리면
    표에는 항목별 문구가 보이는데 문서에는 뭉친 한 줄이 나가 표와 문서가 어긋난다.
    사람이 그 항목만의 문구를 원하면 표에서 고쳐 쓰면 된다(고치는 순간 그 행만
    따로 나간다).
    """
    for cand in ((apply_note or "").strip(), (public or "").strip()):
        if cand and not is_generic_line(cand):
            return strip_tag(cand)
    for cand in ((apply_note or "").strip(), (public or "").strip()):
        if cand:
            return cand                    # 일반 문구도 그대로(뭉침 판정 유지)
    return (issue or "").strip()


def normalize_rows(rows: "list[dict] | None", *, edited: bool = True) -> list[dict]:
    """선별표 행에 위 불변식을 강제한다. 원본을 건드리지 않고 새 목록을 돌려준다.

    · include 는 'Y'/'X' 둘 중 하나. **값이 없거나 알 수 없으면 반영('Y')** 이다.
    · apply_note 는 default_note() 로 채운다(빈칸으로 남지 않는다).
    · public·apply_note 앞의 [모듈] 태그는 **뗀다**(사용자 확정 2026-08-31).
      이 함수는 선별표를 **화면에 펼 때도, 저장할 때도** 지나는 유일한 길목이라
      여기서 막으면 옛 표가 화면에서부터 태그 없이 보이고, 그 화면을 저장해도
      태그가 되살아나지 않는다(실측 job-21: 태그째 띄워 둔 화면을 저장해 되돌아왔다).

    edited=False (=사람이 이 표를 아직 손대지 않았다) 면 **예전 규칙으로 미반영이 된
    미판정 행을 기본값(반영)으로 되돌린다**. 미판정을 X 로 깔던 시절에 만들어진 표가
    남아 있는데(실측 job-28: 39행 중 16행), 그건 사람의 판단이 아니라 그때의 기본값이다.
    사람이 한 번이라도 저장했으면(edited=True) 그 값이 이긴다 — 사람 판단을 되돌리는
    일은 없어야 한다. 그래서 **기본값은 edited=True**(안전한 쪽)이고, 부르는 쪽이
    '아직 아무도 안 건드렸다'를 알 때만 False 를 준다.
    """
    out: list[dict] = []
    for r in rows or []:
        r2 = dict(r)
        inc = str(r2.get("include") or "").strip().upper()
        if (not edited and inc == "X"
                and str(r2.get("reason") or "").startswith(UNJUDGED_PREFIX)):
            inc = "Y"
        r2["include"] = "X" if inc == "X" else "Y"
        r2["public"] = strip_tag(str(r2.get("public") or ""))
        r2["apply_note"] = strip_tag(default_note(
            apply_note=str(r2.get("apply_note") or ""),
            public=str(r2.get("public") or ""),
            issue=str(r2.get("issue") or ""),
        ))
        out.append(r2)
    return out


def related_groups(ordered: list[dict]) -> list[list[dict]]:
    """이번 목록 안에서 Redmine 으로 서로 연결된 이슈를 한 덩어리로 — 사슬(A–B–C)도 하나로 묶는다.

    같은 문제를 영업 이슈(Sales_Issues)와 개발 이슈로 따로 올리고 서로 '관련' 으로 이어 두는 일이 많다.
    둘 다 선별표에 오르면 검수자가 같은 내용을 두 번 판단해야 했다(2026-10-19 사용자 지적, 실측 job-34:
    #34294↔#34369 · #33729↔#33554). 관계 종류(관련 · 중복 · 복사 · 선후)는 가리지 않는다.
    덩어리 순서와 덩어리 안 순서는 원래 목록 순서 — 첫 이슈가 대표다.
    """
    ids = [str(i["id"]) for i in ordered]
    inset = set(ids)
    parent = {i: i for i in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for iss in ordered:
        a = str(iss["id"])
        for r in iss.get("relations") or []:
            b = str(r.get("id") or "")
            if b in inset and b != a:
                ra, rb = find(a), find(b)
                if ra != rb:
                    # 목록에서 먼저 나온 쪽을 뿌리로 — 대표가 늘 첫 이슈가 되게
                    if ids.index(ra) < ids.index(rb):
                        parent[rb] = ra
                    else:
                        parent[ra] = rb
    groups: dict[str, list[dict]] = {}
    for iss in ordered:
        groups.setdefault(find(str(iss["id"])), []).append(iss)
    return list(groups.values())


def build_selection(ordered: list[dict], classified: dict,
                    include_all: bool = False,
                    carry: dict | None = None) -> tuple[list[list], list[str]]:
    """draft 형식 행 + 포함 공개문구 목록. 반환 (draft_rows, public_changes).

    include_all=True 면 **전 항목을 반영(Y)으로 깔아 둔다**(2026-08-04, 사용자 지시:
    "처음 RN 제작 작업을 할 때는 모든 항목에 대해 기본값이 반영"). 제외 규칙으로 걸러
    내고 사람이 다시 넣게 하는 것보다, 전부 실어 두고 사람이 빼는 편이 맞다는 것이다.
    Claude 의 판정 사유는 비고에 남겨 근거를 잃지 않는다.

    판정을 못 받은 이슈도 **반영(Y)** 이다. 예전에는 미판정만 X 로 뺐는데(공개 문구가
    없어 빈 항목이 섞인다는 이유), 그러면 '기본값은 전부 반영' 이라는 규칙이 조용히
    깨진다. 빈 문구 쪽은 rn_select.default_note / rn_research 의 문구 보정으로 막고,
    미판정이라는 사실은 **비고(UNJUDGED_REASON)로 남겨** 검수 화면이 경고하게 한다.

    carry {이슈번호: 'Y'|'X'} 를 주면 그 값이 **가장 세다**. 사람이 선별표에서 정한
    반영/미반영을 재제작이 덮지 않게 하기 위한 것이다(carry 에 없는 새 이슈는 기본값).
    """
    rows: list[list] = []
    public: list[str] = []
    carry = carry or {}
    for n, iss in enumerate(ordered, 1):
        iid = str(iss["id"])
        judged = iid in classified
        c = classified.get(iid, {})
        inc = (True if include_all else bool(c.get("include")))
        if not judged:
            reason = UNJUDGED_REASON
        elif include_all:
            _r = (c.get("reason", "") or "").strip()
            reason = "기본 반영" + (f" (판정: {_r})" if _r else "")
        else:
            reason = c.get("reason", "")
        if iid in carry:                      # 사람이 정한 값 — 판정보다 우선
            was = carry[iid] == "Y"
            if was != inc:
                _raw = (c.get("reason", "") or "").strip() if judged else ""
                reason = (f"사람이 {'반영' if was else '미반영'}으로 지정"
                          + (f" (판정: {_raw})" if _raw else ""))
            inc = was
        rows.append([
            n, iss.get("subject", ""), team_of(iss.get("created_by", "")),
            c.get("customer", ""), gubun_of(iss.get("subject", "")),
            "Y" if inc else "X", reason, _REDMINE + iid,
        ])
        if inc and c.get("public"):
            public.append(c["public"])
    return rows, public
