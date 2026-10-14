"""메일 분석 — Claude 가 수신 메일을 읽고 '작업지시서 초안'의 근거를 판단한다.

룰베이스(제목 정규식) 대신 **본문까지** 읽고 의도로 판단한다:
  - 이 메일이 OS 배포 트리거인가?
  - 정확히 어느 제품군 + Android 버전인가?  (동명 제품 혼동 방지의 핵심)
  - (메일에 있으면) 새 OS 버전은?  없으면 비워둔다(추측 금지 — RN/파일서버에서 확정).

FIH-GMS 발신 메일은 형식이 다양하다(Google approval / DQA 배포허가 / MR 승인 등).
형식이 아니라 의도로 분류하고, 처음 보는/애매한 형식은 needs_human_review 로 표시한다.

읽기 전용. 프로덕션 무접촉.
"""
from __future__ import annotations

import os
import re

import anthropic

from app.database import get_session
from app.models import CachedMail
from app.pipeline.ai_edit import _extract_json
from app.integrations.claude_cli import have_auth  # noqa: E402

# Opus 4.8 은 조직 rate limit 이 빡빡해, 이 분류 작업은 모델별 별도 한도인 Sonnet 5 로 수행한다.
_MODEL = os.getenv("RN_RESEARCH_MODEL", "claude-sonnet-5")
_MAX_TOKENS = 2000
_GMS_SENDER = "fih-gms@fih-foxconn.com"

_SYSTEM = (
    "당신은 포인트모바일 TW 팀의 수신 메일 분석기입니다. 들어온 메일이 'OS 이미지를 FOTA 서버에 "
    "올려야 하는 배포 트리거'인지 판단하고, 정확한 제품(모델 코드)과 Android 버전, (있으면) 새 OS 버전을 뽑습니다. "
    "이 메일들은 FIH-GMS(폭스콘) 시스템에서 오며 형식이 다양합니다(Google 배포 승인, DQA 배포허가, MR 승인 등). "
    "형식이 아니라 '의도'로 판단하세요. "
    "**product_family 는 내부 문서(Service Portal·Redmine·배포 메일)에서 쓰는 '모델 코드'만** 넣으세요. "
    "브랜드/제조사 접두어는 제거합니다. 예: 'POINTMOBILE PM95'→'PM95', 'Janam XG5'→'XG5', "
    "'DT-X400'→'DT-X400', 'PR22'→'PR22', 'RF85x'→'RF85x'. 포인트모바일은 PM 시리즈 외에도 "
    "여러 브랜드/모델(Janam, DT-X, PR, RF, EZDS Z 등)을 개발하므로 특정 접두사를 가정하지 말고 메일의 실제 모델 코드를 쓰세요. "
    "같은 모델이라도 Android 버전이 다르면 다른 SKU 이므로 Android 버전을 반드시 정확히 뽑으세요"
    "(본문의 'Android OS' 값, Build fingerprint 의 '<모델>:<버전>' 등). "
    "메일에 OS 버전 번호가 명시돼 있지 않으면 os_version_new 를 빈 문자열로 두세요(절대 지어내지 마세요). "
    "처음 보는 형식이거나 확신이 낮으면 needs_human_review=true. 반드시 지정한 JSON 으로만 답하세요."
)

_SCHEMA_HINT = (
    '{\n'
    '  "is_os_deploy_trigger": true/false,\n'
    '  "mail_type": "Google 배포 승인 | DQA 배포허가 | MR 승인 | 기타(설명)",\n'
    '  "product_family": "모델 코드만(브랜드 접두 제거). 예: PM95, XG5, DT-X400 (없으면 \\"\\")",\n'
    '  "android_version": "예: 13 (없으면 \\"\\")",\n'
    '  "os_version_new": "예: 95.06 (메일에 없으면 \\"\\")",\n'
    '  "build_fingerprint": "있으면 그대로, 없으면 \\"\\"",\n'
    '  "approval_source": "예: Google / DQA / MR / 기타",\n'
    '  "confidence": 0.0~1.0,\n'
    '  "is_novel_pattern": true/false,\n'
    '  "needs_human_review": true/false,\n'
    '  "reason": "판단 이유 한두 줄",\n'
    '  "evidence": "메일에서 근거가 된 문구"\n'
    '}'
)


def _strip_html(h: str) -> str:
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    h = re.sub(r"&nbsp;", " ", h)
    h = re.sub(r"[ \t]+", " ", h)
    h = re.sub(r"\n\s*\n+", "\n", h)
    return h.strip()


def _mail_body(m: CachedMail) -> str:
    body = (m.body_text or "").strip()
    if not body and m.body_html:
        body = _strip_html(m.body_html)
    return body


def analyze_mail(mail_id: int, user_id: int | None = None) -> dict:
    """CachedMail(id=mail_id) 를 Claude 로 분석해 구조화 결과(dict)를 반환.

    실패 시 {"error": ...}. 성공 시 스키마 필드 + coarse gate 판단(from_gms) 포함.
    """
    if not have_auth():
        return {"error": "Claude 를 부를 수 없습니다 — Claude Code(구독) 로그인이나 .env 의 ANTHROPIC_API_KEY 중 하나가 있어야 합니다."}

    with get_session() as db:
        # 팀 공용 보드: 어느 계정이 받은 메일이든 분석 대상(user_id 필터 안 함).
        m = db.query(CachedMail).filter_by(id=mail_id).first()
        if m is None:
            return {"error": f"메일(id={mail_id})을 찾을 수 없습니다."}
        sender, subject = m.sender or "", m.subject or ""
        body = _mail_body(m)

    from_gms = _GMS_SENDER in sender.lower()
    prompt = (
        "다음 수신 메일을 분석해 지정한 JSON 으로만 답하세요.\n\n"
        f"[발신] {sender}\n[제목] {subject}\n[본문]\n{body[:6000]}\n\n"
        "출력 JSON 형식:\n" + _SCHEMA_HINT
    )

    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError:
        return {"error": "Claude 인증 실패: .env 의 ANTHROPIC_API_KEY 를 확인하세요."}
    except anthropic.APIStatusError as e:
        return {"error": f"Claude API 오류({e.status_code}): {e.message}"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"분석 실행 오류: {e}"}

    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    parsed = _extract_json(text)
    if parsed is None:
        return {"error": "Claude 응답에서 JSON 을 해석하지 못했습니다.\n" + text[:500]}

    parsed["from_gms"] = from_gms
    parsed["subject"] = subject
    parsed["sender"] = sender
    return parsed
