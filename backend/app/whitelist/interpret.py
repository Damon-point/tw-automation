"""WhiteList 요청 **유기적 해석** — Claude 가 틀 없는 요청을 읽고 처리 계획을 만든다.

WhiteList 등록 요청은 정해진 양식이 없다(본문 표·엑셀·첨부된 예전 메일이 뒤섞임).
룰로 상황마다 커버하는 대신, 요청을 **소스 블록**(본문/첨부메일/엑셀)으로 나눠
각 블록의 텍스트를 Claude 가 읽고 **무엇을 해달라는지**(신규 등록 / 등록일 변경 /
해당 없음)와 모델·OS·날짜를 판단하게 한다. **SN 목록 자체는 결정론적으로** 뽑아
그대로 쓰고(환각 방지), Claude 는 '해석'만 한다. 결과는 관리자가 표에서 확인·수정한다.

비용: 블록 텍스트만(수천 SN 제외) 넘기므로 한 번의 짧은 호출로 끝난다.
"""
from __future__ import annotations

import json
import os
from app.integrations.claude_cli import have_auth  # noqa: E402

_MODEL = os.getenv("WHITELIST_INTERPRET_MODEL", "claude-haiku-4-5")
_MAX_BLOCK_CHARS = 900
_MAX_BLOCKS = 40

_SYS = """당신은 Point Mobile 사내 FOTA 화이트리스트 요청을 해석합니다.
요청은 **정해진 양식이 없습니다** — 본문 표, 엑셀 첨부, 첨부된 예전 메일이 섞여 옵니다.
입력은 요청을 '블록'(본문 / 첨부메일 / 엑셀)으로 나눈 것이며, 각 블록의 텍스트와 SN 개수가 주어집니다.
**일련번호(SN)는 이미 규칙으로 정확히 뽑아 두었으니 당신은 SN 을 나열하지 마세요.**
각 블록이 무엇을 요청하는지만 판단하세요.

먼저 **요청 전체의 성격**을 판단해 overall 을 만드세요:
  overall: {request_type, date, model, from_os, to_os, reason}
    · request_type = "date"(전체가 등록일/기간 변경) | "new"(전체가 신규 등록)
                     | "mixed"(둘 섞임) | "unknown"(판단 불가)
    · date/model/from_os/to_os = 전체에 적용할 대표값(알면 채우고 모르면 "")
    · reason = 한국어 한 줄 근거
예: "등록일을 7/21로 변경" 요청이면 overall.request_type="date", date="2026-07-21".
예: 엑셀 SN 목록을 "등록 부탁" 하면 overall.request_type="new", 제목/이미지에서 model·OS.
**overall 은 본문(최상단 요청자의 핵심 지시)을 우선한다.** 본문이 '등록일 변경'을 요청하면,
첨부·인용된 예전 메일이 신규 등록처럼 보여도 그것들은 '변경 대상 목록'일 뿐이므로
overall.request_type="date" 로 판단하라(mixed 로 두지 말 것).

그다음 각 블록마다 JSON 객체를 만들어 답하세요. 최종 형식:
{"overall": {...}, "blocks": [...]}
블록 필드:
  index    : 입력에 준 블록 번호 그대로
  type     : "new"(신규 등록) | "date"(등록일/기간 변경) | "skip"(등록 대상 아님 · 단순 안내/확인/인용)
  model    : 단말 모델(PM95 등). 모르면 ""
  from_os  : 현재 Android 숫자(9·11·13·15…). 모르면 ""
  to_os    : 업그레이드할 Android 숫자. 모르면 ""
  date     : 등록일/기간 변경 대상 날짜(YYYY-MM-DD). 없으면 ""
  note     : 한국어 한 줄 근거

판단 지침:
  · **요청을 전체로 보라.** 의도(등록/변경)는 본문에 있고 SN 은 엑셀·다른 블록에 있을 수 있다.
    본문이 "이 SN 들 FOTA 등록 부탁" 이고 엑셀에 SN 목록이 있으면, **그 엑셀 블록이 type=new** 다.
  · 본문이 "등록일을 …로 변경" 처럼 날짜 변경을 말하면 관련 블록들은 type=date, date 를 채운다.
  · "아래 SN 등록 완료했습니다" 처럼 **이미 처리한 결과를 알리는** 인용/회신 블록은 type=skip.
  · model 은 제목·본문에서 끌어와 SN 이 있는 블록에도 채운다(예: 제목 'PM95' → 엑셀 블록 model=PM95).
  · from_os/to_os 는 본문/제목에 **명시된 경우에만** 채운다(예 'A13→A15', '13에서 15로').
    '무상 업그레이드'처럼 버전이 없으면 **빈 문자열로 두고** 관리자가 채우게 한다(지어내지 말 것).
  · SN 이 하나도 없는 안내용 블록은 type=skip 으로 둬도 된다(어차피 처리 대상이 없다).
  · **본문에 이미지(표·스크린샷)가 첨부되면 그 안의 글자도 읽어라** — 모델·OS 버전(예 A13→A15)이
    본문 텍스트가 아니라 이미지 표에 들어오는 경우가 많다. 이미지에서 읽은 값으로 model/from_os/to_os 를 채운다."""


def _client():
    import anthropic
    return anthropic.Anthropic()


def _prompt(blocks: list[dict], subject: str, instruction: str = "") -> str:
    parts = []
    if instruction.strip():
        parts += ["[관리자 지시 — 최우선으로 따르세요]", instruction.strip(), ""]
    parts += [f"메일 제목: {subject}", ""]
    for i, b in enumerate(blocks):
        txt = " ".join((b.get("text") or "").split())[:_MAX_BLOCK_CHARS]
        parts.append(
            f"--- 블록 {i}\n"
            f"출처: {b.get('source', '')}\n"
            f"SN 개수: {len(b.get('serials') or [])}\n"
            f"텍스트: {txt or '(텍스트 없음 — 엑셀 등)'}\n"
        )
    parts.append("\n위 블록들을 해석해 JSON 으로 답하세요.")
    return "\n".join(parts)


def interpret_blocks(blocks: list[dict], subject: str = "",
                     images: list[dict] | None = None, instruction: str = "") -> dict:
    """블록들을 Claude 로 해석. 반환 {ok, error, overall:{...}, blocks:[{...}]}.

    images: 본문 인라인 이미지 [{media_type, data(bytes)}] — 표·스크린샷 속 OS 버전을 읽게 넘긴다.
    instruction: 관리자가 직접 준 지시(오판 교정용) — 최우선으로 반영한다.
    실패해도 예외를 던지지 않는다(호출부가 규칙 기반 기본값으로 폴백).
    """
    import base64

    use = blocks[:_MAX_BLOCKS]
    if not have_auth():
        return {"ok": False, "error": "Claude 를 부를 수 없습니다 — 자동 해석 건너뜀(수동 지정 가능).",
                "overall": {}, "blocks": []}
    try:
        from app import usage
        from app.pipeline.ai_edit import _extract_json
    except Exception:  # noqa: BLE001
        _extract_json = None  # type: ignore
    try:
        from app import usage as _usage
        _usage.set_operation("WhiteList 요청 해석", None)
    except Exception:  # noqa: BLE001
        pass
    content: list[dict] = [{"type": "text", "text": _prompt(use, subject, instruction)}]
    for img in (images or [])[:6]:
        try:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": img["media_type"],
                           "data": base64.standard_b64encode(img["data"]).decode("ascii")},
            })
        except Exception:  # noqa: BLE001
            continue
    if len(content) > 1:
        content.append({"type": "text",
                        "text": "위 이미지(본문 표·스크린샷)의 모델·OS 버전도 읽어 반영하세요."})
    try:
        resp = _client().messages.create(
            model=_MODEL, max_tokens=2000, system=_SYS,
            messages=[{"role": "user", "content": content}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        parsed = (_extract_json(text) if _extract_json else json.loads(text)) or {}
        ov = parsed.get("overall") or {}
        overall = {
            "request_type": str(ov.get("request_type") or "").strip().lower(),
            "date": str(ov.get("date") or "").strip(),
            "model": str(ov.get("model") or "").strip(),
            "from_os": str(ov.get("from_os") or "").strip(),
            "to_os": str(ov.get("to_os") or "").strip(),
            "reason": str(ov.get("reason") or "").strip(),
        }
        out = []
        for r in (parsed.get("blocks") or []):
            try:
                out.append({
                    "index": int(r.get("index")),
                    "type": str(r.get("type") or "").strip().lower() or "skip",
                    "model": str(r.get("model") or "").strip(),
                    "from_os": str(r.get("from_os") or "").strip(),
                    "to_os": str(r.get("to_os") or "").strip(),
                    "date": str(r.get("date") or "").strip(),
                    "note": str(r.get("note") or "").strip(),
                })
            except (TypeError, ValueError):
                continue
        return {"ok": True, "error": "", "overall": overall, "blocks": out}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "overall": {}, "blocks": []}
