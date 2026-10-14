"""Claude 판단 기반 문서 편집 엔진 (Service Portal · OS/FW Release Notes 공용) — 로컬 전용.

Claude 가 실제 문서(HTML) + 새 OS 릴리스 데이터를 보고 '무엇을 바꿔야 하는지'를 판단해
**정확한 문자열 치환 목록**(old_string → new_string + 이유)으로 돌려준다.
문서 전체를 다시 생성하지 않으므로(훼손/잘림 위험 제거) 안전하고 저렴하다.

각 치환은 'old_string 이 문서에 정확히 1번 있을 때만' 적용한다(Edit 도구와 동일한 안전장치).
0번/2번 이상은 조용히 넘기지 않고 실패로 보고한다.

**프로덕션에는 절대 쓰지 않는다.** 결과 HTML 은 호출자가 로컬 편집본/에디터에만 반영하고 사람이 검수한다.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

import anthropic

from app.pipeline.state import get_state
from app.integrations.claude_cli import have_auth  # noqa: E402

_MODEL = "claude-opus-4-8"
_MAX_TOKENS = 8000

_SYSTEM = (
    "당신은 포인트모바일의 제품 문서(HTML)를 새 OS 릴리스에 맞춰 갱신하는 편집 보조자입니다. "
    "새 OS 버전이 나왔을 때 **바뀌어야 하는 부분만** 판단해 고칩니다: "
    "다운로드 링크(href), 버전 번호, 날짜, 버전 전이(예: '95.04 → 95.06') 같은 것. "
    "버전과 무관한 문단·구조·스타일·다른 제품 링크는 절대 건드리지 마세요. "
    "제공되지 않은 링크나 값을 지어내지 마세요. 확실치 않으면 그 항목은 바꾸지 마세요.\n"
    "**중요 — old_string 은 반드시 '실제로 바뀌는 최소 단위'만 잡으세요**: 링크 href URL 하나, "
    "버전 문자열 하나, 날짜 하나처럼 짧게. div/table 같은 큰 구조 블록을 통째로 old_string 에 넣지 "
    "마세요(원문과 공백·줄바꿈까지 똑같아야 적용되므로 큰 블록은 거의 실패합니다). "
    "문서에 있는 텍스트를 **글자 그대로**(따옴표·&amp; 등 엔티티 포함) 복사해 쓰세요. "
    "반드시 지정한 JSON 형식으로만 답하세요."
)

# 문서 종류별 편집 가이드
_GUIDANCE = {
    "service_portal": (
        "이 문서는 Service Portal 의 'Download' 문서(Naver SmartEditor HTML, __se_ 클래스)입니다.\n"
        "바꿀 대상(모두 '짧은 앵커'로만):\n"
        "- 현재 버전 표기: '68.03 (Jan 08 2026)' 같은 '버전 (날짜)' 문자열 → 'new_version (new_version_date)'. "
        "old_string 은 그 '버전 (날짜)' 텍스트만 잡으세요(감싼 <b>/<span> 태그까지 넣지 말 것).\n"
        "- 'OS Image Package Download' 표(class=\"tg\")의 다운로드 링크 href: Dropbox URL 그 자체를 old_string 으로. "
        "파일명으로 Full/Diff 를 구분(예: '..._YYYYMMDD.zip'=Full, '..._02_YYYYMMDD.zip'=Diff) → os_image_links 의 "
        "FULL/DIFF URL 로 교체. (href 값 전체를 &amp; 포함 글자 그대로 복사)\n"
        "- 'Release Note' PDF 링크 href(예: '..._Release_Note_68.03_for-sales.pdf?...') → release_note_link.\n"
        "각 교체의 old_string 은 바뀌는 '값 그 자체'(버전 문자열/ href URL)만. 표·div·행 전체를 넣지 마세요. "
        "어떤 URL이 Full/Diff인지 확신이 없으면 그 항목은 건너뛰세요."
    ),
    "release_notes": (
        "이 문서는 Confluence 로 작성된 제품의 OS/FW Release Note 페이지(뷰 HTML, columnLayout/cell 구조)입니다. "
        "**교체 + 새 항목 추가** 둘 다 하되, old_string 은 항상 '짧은 앵커'로:\n"
        "1) 교체: 기존 버전/날짜/다운로드·RN 링크(href)/구성요소 버전 표기 중 이번 릴리스로 갱신할 '값 그 자체'만 교체.\n"
        "2) 새 항목 추가: 기존 최신 버전 항목과 같은 형식으로 new_version 항목(헤더=new_version + new_version_date, "
        "본문=changes 각 항목)을 만들어 이력 맨 위에 끼워넣습니다. 이때 **큰 블록을 old_string 으로 쓰지 말고**, "
        "삽입 지점 바로 뒤에 있는 '짧고 유일한 텍스트 한 조각'(예: 현재 최신 버전 헤더 텍스트 '95.06 (...)' )을 "
        "old_string 으로 잡고, new_string = '새 항목 HTML' + 그 텍스트 로 하세요(그 텍스트 앞에 새 항목이 삽입됨). "
        "changes 에 없는 내용은 지어내지 마세요.\n"
        "⚠ 모든 편집은 **로컬 검수 사본에만** 반영됩니다 — 실제 Confluence 페이지엔 절대 쓰지 않습니다."
    ),
}


@dataclass
class EditResult:
    edited_html: str = ""
    applied: list[dict] = field(default_factory=list)   # {old,new,why}
    failed: list[dict] = field(default_factory=list)     # {old,new,why,reason}
    raw_changes: list[dict] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def release_data(mail_id: int | None) -> dict:
    """파이프라인 공유 상태에서 새 릴리스 정보를 모은다(⑤⑥ 편집 근거).

    new_version(신규) + prev_version(직전, 문서에서 교체 대상) + Full/Diff 다운로드 링크(③ fota_downloads)
    + 보안패치/SDK 를 넘겨 Claude 가 '이전값 → 새값' 교체를 확실히 하도록 한다.
    """
    st = get_state(mail_id) if mail_id is not None else {}
    # ③ fota_downloads(폴더별 Full/Diff) → 대표 Full/Diff 링크
    links: dict[str, str] = {}
    for d in st.get("fota_downloads") or []:
        t = str(d.get("type", "")).upper()
        if t and t not in links and d.get("url"):
            links[t] = d["url"]
    return {
        "product": st.get("product"),
        "new_version": st.get("release_version") or st.get("os_version"),
        "new_version_date": st.get("release_date") or "",
        # 버전+날짜 한 문자열(예: '95.07 (Jul 10 2026)') — OS Version 필드 통짜 교체용(날짜 유실 방지).
        "new_version_full": st.get("version_full")
        or st.get("release_version") or st.get("os_version") or "",
        "prev_version": st.get("prev_version") or "",
        "os_image_links": links,                       # {"FULL": url, "DIFF": url}
        "release_note_link": st.get("rn_link"),
        "release_note_name": st.get("rn_name"),
        "security_patch": st.get("security_patch") or "",
        "sdk": (st.get("rn_components") or {}).get("sdk", ""),
        "changes": st.get("rn_changes") or [],   # ⑥ RN 새 항목 본문(② 산출)
    }


def _build_prompt(current_html: str, data: dict, kind: str, extra: str = "") -> str:
    extra_line = (
        f"0) [사람의 추가 수정 지시 — 최우선 반영]: {extra}\n" if extra.strip() else ""
    )
    return (
        "아래는 현재 문서의 실제 HTML 과, 이번에 반영할 새 OS 릴리스 데이터입니다.\n"
        "새 릴리스를 반영하기 위해 바꿔야 할 부분을 **정확한 문자열 치환**으로만 제안하세요.\n\n"
        "규칙:\n" + extra_line
        + "1) 각 치환의 old_string 은 문서에서 **정확히 한 번만** 나타나는 **가장 짧은 스니펫**이어야 합니다. "
        "실제로 바뀌는 값(버전 문자열/날짜/href URL) 그 자체만 잡고, 큰 구조 블록(div/table/행 전체)은 넣지 마세요. "
        "그 값이 여러 곳에 나와 모호하면, 유일해질 만큼만 앞뒤로 아주 짧게 덧붙이세요.\n"
        "2) " + _GUIDANCE.get(kind, "") + "\n"
        "3) 제공된 링크/값만 사용하세요(추측 금지).\n"
        "4) 버전·무관 부분은 그대로 두세요.\n"
        "5) 문서에 **prev_version**(직전 버전)이 보이면 **new_version** 으로 교체하세요. "
        "단, **버전과 날짜가 한 문자열로 붙어있는 곳**(예: 'OS Version: 95.06 (Jan 27 2026)')은 "
        "반드시 **new_version_full** 로 통째 교체해 **날짜를 반드시 포함**하세요(날짜 유실 금지). "
        "버전과 분리된 별도 날짜 표기만 있으면 **new_version_date** 로 교체하세요. "
        "os_image_links 의 FULL/DIFF URL 이 제공되면 해당 다운로드 "
        "링크(href)를 그 값으로 교체하세요. security_patch/sdk 가 제공되고 문서에 해당 표기가 있으면 갱신하세요.\n\n"
        "출력은 **오직 아래 JSON** 한 개:\n"
        '{ "changes": [ { "old_string": "...", "new_string": "...", "why": "왜 바꾸는지 한 줄" } ] }\n'
        "바꿀 게 없으면 changes 를 빈 배열로 두세요.\n\n"
        "=== 새 OS 릴리스 데이터(JSON) ===\n"
        + json.dumps(data, ensure_ascii=False, indent=2)
        + "\n\n=== 현재 문서 HTML ===\n"
        + current_html
    )


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    i, j = text.find("{"), text.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(text[i : j + 1])
        except Exception:  # noqa: BLE001
            return None
    return None


def _fuzzy_find_unique(html: str, old: str) -> tuple[int, int] | None:
    """공백(줄바꿈·스페이스·탭) 차이를 무시하고 old 가 문서에 '정확히 한 번' 나오는 구간을 찾는다.

    Claude 가 돌려준 old_string 과 원문이 공백/줄바꿈만 다를 때(HTML 에서 흔함) 매칭시킨다.
    토큰 사이 공백을 \\s+ 로 바꾼 정규식으로 검색해 유일하면 (start, end) 반환, 아니면 None.
    """
    tokens = [t for t in re.split(r"\s+", old.strip()) if t]
    if not tokens:
        return None
    pattern = r"\s+".join(re.escape(t) for t in tokens)
    try:
        matches = list(re.finditer(pattern, html))
    except re.error:
        return None
    if len(matches) == 1:
        return matches[0].span()
    return None


def _apply(current_html: str, changes: list[dict]) -> tuple[str, list[dict], list[dict]]:
    html = current_html
    applied: list[dict] = []
    failed: list[dict] = []
    for ch in changes:
        old = ch.get("old_string", "")
        new = ch.get("new_string", "")
        why = ch.get("why", "")
        if not old:
            failed.append({"old": old, "new": new, "why": why, "reason": "old_string 이 비어 있음"})
            continue
        cnt = html.count(old)
        if cnt == 1:
            html = html.replace(old, new, 1)
            applied.append({"old": old, "new": new, "why": why})
        elif cnt > 1:
            failed.append({"old": old, "new": new, "why": why, "reason": f"{cnt}곳 중복(모호)"})
        else:
            # 정확 일치 실패 → 공백 관용(fuzzy) 매칭 폴백
            span = _fuzzy_find_unique(html, old)
            if span:
                s, e = span
                html = html[:s] + new + html[e:]
                applied.append({"old": old, "new": new, "why": (why + " (공백 관용 매칭)").strip()})
            else:
                failed.append({"old": old, "new": new, "why": why, "reason": "문서에서 못 찾음"})
    return html, applied, failed


def propose_edit(current_html: str, data: dict, kind: str, extra: str = "") -> EditResult:
    """Claude 에게 편집을 판단시키고 안전하게 적용한 결과를 반환(로컬 전용).

    kind: "service_portal" | "release_notes"
    extra: 사람이 준 추가 수정 지시(있으면 최우선 반영).
    """
    if not have_auth():
        return EditResult(error="Claude 를 부를 수 없습니다 — Claude Code(구독) 로그인이나 .env 의 ANTHROPIC_API_KEY 중 하나가 있어야 합니다.")
    if not (current_html or "").strip():
        return EditResult(error="편집할 문서 내용이 비어 있습니다.")
    if not data.get("new_version"):
        return EditResult(
            error="파이프라인에 새 버전 정보가 없습니다. 먼저 OS Download · Release Note 제작을 실행해 데이터를 만드세요."
        )

    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=_SYSTEM,
            messages=[{"role": "user", "content": _build_prompt(current_html, data, kind, extra)}],
        )
    except anthropic.AuthenticationError:
        return EditResult(error="Claude 인증 실패: .env 의 ANTHROPIC_API_KEY 를 확인하세요.")
    except anthropic.APIStatusError as e:
        return EditResult(error=f"Claude API 오류({e.status_code}): {e.message}")
    except Exception as e:  # noqa: BLE001
        return EditResult(error=f"에이전트 실행 오류: {e}")

    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    parsed = _extract_json(text)
    if parsed is None:
        return EditResult(error="Claude 응답에서 JSON 을 해석하지 못했습니다.\n" + text[:500])

    changes = parsed.get("changes") or []
    edited, applied, failed = _apply(current_html, changes)
    return EditResult(edited_html=edited, applied=applied, failed=failed, raw_changes=changes)
