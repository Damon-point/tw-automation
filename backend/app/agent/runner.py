"""Claude 주도 에이전트 실행기 (수동 에이전트 루프).

한 단계를 Claude(claude-opus-4-8)가 도구를 호출하며 수행한다:
  목표 제시 → Claude 가 도구 호출 → 우리가 실행 → 결과 전달 → 반복 →
  Claude 가 스스로 검증/수정 후 종료. 최종 요약 + 산출물을 반환한다.

블로킹(수 분 소요 가능)이므로 UI 에서는 run.io_bound 로 호출할 것.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass

import anthropic

from app.agent.stage_specs import goal_for, tools_for
from app.agent.tools import ToolContext, dispatch, schemas_for
from app.integrations.claude_cli import have_auth  # noqa: E402

MODEL = "claude-opus-4-8"
MAX_TURNS = 12          # 도구 호출 왕복 상한(비용/무한루프 보호)
MAX_TOKENS = 8000

SYSTEM = (
    "당신은 포인트모바일 TW 팀의 자동화 운영 에이전트입니다. "
    "주어진 도구를 사용해 단계 목표를 끝까지 완수하세요. "
    "각 도구 결과를 확인하고, 실패하거나 결과가 부실하면 원인을 진단해 스스로 재시도/수정하세요. "
    "요청되지 않은 작업은 하지 마세요. "
    "작업이 끝나면 한국어로 무엇을 했는지, 산출물은 무엇인지, 남은 이슈가 있는지 간결히 요약하세요. "
    "검증 없이 '완료'라고 말하지 말고, 실제 도구 결과에 근거해 보고하세요."
)


@dataclass
class StageOutput:
    result_text: str = ""
    artifact_type: str = ""   # "pdf" / "excel" / ""
    artifact_path: str = ""
    cancelled: bool = False    # 사용자가 실행 중 중단한 경우 True
    #: 검수할 것이 없어 **실행과 동시에 끝나는** 단계. 보드가 '검수 대기' 대신 '완료' 로
    #: 놓는다(예: 이번 버전 RN 이 파일서버에 이미 있어 전체 이력 RN 을 만들지 않는 경우).
    auto_done: bool = False


def _text_blocks(content) -> list[str]:
    out = []
    for b in content:
        if getattr(b, "type", None) == "text":
            out.append(b.text)
    return out


def run_stage_agent(
    stage_key: str, subject: str, user_id: int, input_params: str = ""
) -> StageOutput:
    if not have_auth():
        return StageOutput(
            result_text="Claude 인증 정보가 없습니다. 프로젝트 .env 에 "
            "ANTHROPIC_API_KEY=sk-ant-... 를 넣은 뒤 서버를 재시작하세요."
        )

    workdir = tempfile.mkdtemp(prefix=f"tw_agent_{user_id}_")
    ctx = ToolContext(user_id=user_id, subject=subject, workdir=workdir)
    tools = schemas_for(tools_for(stage_key))
    messages = [{"role": "user", "content": goal_for(stage_key, subject, input_params)}]
    transcript: list[str] = []

    try:
        client = anthropic.Anthropic()
        for _ in range(MAX_TURNS):
            resp = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                thinking={"type": "adaptive"},
                system=SYSTEM,
                tools=tools,
                messages=messages,
            )

            if resp.stop_reason == "refusal":
                return StageOutput(result_text="Claude가 요청을 거부했습니다(안전 정책).")

            transcript.extend(_text_blocks(resp.content))

            if resp.stop_reason != "tool_use":
                break

            # 도구 실행 후 결과를 돌려준다
            messages.append({"role": "assistant", "content": resp.content})
            tool_results = []
            for b in resp.content:
                if getattr(b, "type", None) == "tool_use":
                    result = dispatch(ctx, b.name, dict(b.input))
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": b.id,
                            "content": result,
                        }
                    )
            messages.append({"role": "user", "content": tool_results})
        else:
            transcript.append("\n(도구 호출 한도에 도달해 중단했습니다.)")

        # 기록된 노트도 요약에 포함
        if ctx.notes:
            transcript.append("\n[기록]\n" + "\n".join(f"- {n}" for n in ctx.notes))

        summary = "\n".join(t for t in transcript if t.strip()).strip() or "(요약 없음)"
        return StageOutput(
            result_text=summary,
            artifact_type=ctx.artifact_type,
            artifact_path=ctx.artifact_path,
        )

    except anthropic.AuthenticationError:
        return StageOutput(
            result_text="Claude 인증 실패: .env 의 ANTHROPIC_API_KEY 가 없거나 유효하지 않습니다. "
            "console.anthropic.com 에서 발급한 키를 넣어주세요."
        )
    except anthropic.APIStatusError as e:
        return StageOutput(result_text=f"Claude API 오류({e.status_code}): {e.message}")
    except Exception as e:  # noqa: BLE001
        return StageOutput(result_text=f"에이전트 실행 오류: {e}")
