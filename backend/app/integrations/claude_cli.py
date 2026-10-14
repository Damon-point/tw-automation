# -*- coding: utf-8 -*-
"""Claude 호출을 **구독 인증**으로 — 이 PC 의 Claude Code 를 불러 쓴다.

Claude.ai 구독은 `api.anthropic.com` 을 열어 주지 않는다. 대신 이 PC 에 깔린 Claude
Code 가 구독으로 인증돼 있고(`~/.claude/.credentials.json`), 화면 없이 부르는 길이
있다. 같은 사내 도구인 gs-automation 이 2026-09-01 부터 그렇게 쓰고 있다.

**화면도 절차도 바뀌지 않는다.** 앱의 호출부 열여섯 군데는 모두

    client = anthropic.Anthropic()
    resp = client.messages.create(model=…, system=…, messages=[…], max_tokens=…)

모양이다. 그래서 `anthropic.Anthropic` 자리에 **같은 모양의 물건**을 끼워 넣는다.
호출부는 한 줄도 고치지 않는다.

**둘 다 쓴다. 고르는 것은 사람이다**(사용자 지시 2026-09-22).
[설정 → Claude] 에서 고르고, 그 값은 DB 에 남는다(app/app_settings.py).

    subscription  이 PC 의 Claude Code 를 부른다. API 요금이 들지 않는다 — 기본값
    api           예전처럼 api.anthropic.com 에 열쇠로 붙는다

**어느 쪽인지는 부를 때마다 본다.** 그래서 화면에서 바꾸면 그 다음 호출부터 바로
듣는다 — 서버를 다시 띄우지 않아도 된다. 바꿔 끼우기(install)는 어느 쪽이든 늘 한다.

처음 켤 때의 기본값은 TW_CLAUDE_BACKEND(또는 옛 이름 TW_CLAUDE_VIA_CLI)로 줄 수 있다.
한 번 화면에서 고르면 그때부터는 DB 값이 이긴다.

claude 명령이 없어도 **서버는 뜬다**. Claude 를 쓰는 단계만 까닭을 말하며 실패한다 —
배포 스크립트가 5초마다 되살리는 구조라 여기서 죽으면 되살아나기만 반복한다.

연장(tools)을 쓰는 호출은 이 길로 흉내 낼 수 없다. 쓰는 곳은 `app/agent/runner.py`
하나인데, 거기로 가는 길은 `stage_runners.execute_stage` 의 **맨 끝 되내림**이다 —
전용 파이프라인이 없는 단계 이름만 그리로 간다. 지금 도는 단계는 모두 전용
파이프라인을 갖고 있다. 그래서 전용 모드에서는 그 호출을 막고, 아니면 진짜 SDK 로
넘긴다.

**열쇠를 Claude Code 에 물려주지 않는다.** 이 앱은 호출부 열여섯 군데가 "열쇠가
있는가"로 문지기를 삼아 환경에 열쇠를 얹어 둔다. 그대로 넘기면 Claude Code 가 그
열쇠로 붙어 구독이 아니라 API 로 계산된다(실측 2026-09-21: 붙지도 못하고 멈췄다).
`call()` 이 벗겨서 부른다.

붙이는 인자에는 저마다 까닭이 있다(gs-automation 실측 2026-09-01).

    --system-prompt      Claude Code 의 기본 프롬프트를 우리 것으로 갈아 끼운다.
                         그냥 부르면 들어가는 말만 21,000 토큰이다
    --tools ""           연장을 주지 않는다. 이 호출은 글만 쓴다
    --safe-mode          CLAUDE.md · 스킬 · 훅 · MCP · 사용자 설정 전부 끔
    --strict-mcp-config  MCP 서버도 건너뜀

일하는 자리(cwd)는 **빈 폴더**로 못박는다 — 소스도 자격증명도 보이지 않게.
요청문은 **표준입력**으로 넘긴다. 윈도우 명령줄은 32,767자에서 끊긴다.
"""
from __future__ import annotations

import json
import os
import base64
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

#: 일하는 자리. 빈 폴더다.
#: **실제 경로로 푼다.** 클론(8081)의 work 는 프로덕션을 가리키는 폴더 링크
#: (junction)라, 링크 경로로 파일을 건네면 Claude Code 가 제 작업 폴더 밖이라며
#: 읽기를 막는다. 그림을 떨궈 읽히려면 양쪽이 같은 실제 경로여야 한다.
_BOX = Path(__file__).resolve().parent.parent.parent / "work" / "claude_box"
_BOX.mkdir(parents=True, exist_ok=True)
BOX = _BOX.resolve()

#: 기다릴 시간(초). RN 큐레이션처럼 긴 것도 있어 넉넉히 둔다.
TIMEOUT = int(os.getenv("TW_CLAUDE_CLI_TIMEOUT", "600"))

#: 한 번에 몇 개까지 동시에 부를지. 프로세스를 띄우는 일이라 무한정은 곤란하다.
_GATE = threading.Semaphore(int(os.getenv("TW_CLAUDE_CLI_PARALLEL", "3")))

#: 모델 이름 → Claude Code 가 받는 별칭. 모르는 이름은 그대로 넘긴다.
_ALIAS = {"opus": "opus", "sonnet": "sonnet", "haiku": "haiku"}


def exe() -> str:
    """claude 명령이 어디 있는가. 없으면 빈 문자열."""
    return shutil.which("claude") or ""


def ready() -> bool:
    return bool(exe())


SUBSCRIPTION = "subscription"
API = "api"


def backend() -> str:
    """지금 어느 쪽으로 부르는가 — "subscription" 또는 "api".

    **부를 때마다** 본다. 사람이 화면에서 바꾸면 그 다음 호출부터 바로 듣는다.
    DB 를 못 읽는 상황(표가 아직 없는 첫 기동 등)에서도 예외를 올리지 않는다.
    """
    try:
        from app import app_settings

        return app_settings.get("claude_backend")
    except Exception:  # noqa: BLE001
        return SUBSCRIPTION


def enabled() -> bool:
    """구독 경유로 도는가."""
    return backend() == SUBSCRIPTION


def have_auth() -> bool:
    """Claude 를 부를 수 있는가 — **구독이든 열쇠든**.

    앱 곳곳의 단계가 시작 전에 "Claude 를 부를 수 있나" 를 묻는다. 예전에는 그 물음이
    곧 "환경에 ANTHROPIC_API_KEY 가 있나" 였다. 구독으로 도는 지금은 열쇠가 없어도
    부를 수 있으므로, 열쇠만 보면 멀쩡히 돌 수 있는 단계를 거절한다.
    """
    if backend() == SUBSCRIPTION:
        # 구독을 고른 동안에는 열쇠가 있어도 쓰지 않는다. claude 가 있어야 부를 수 있다.
        return ready()
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def model_alias(model: str) -> str:
    """'claude-sonnet-5' → 'sonnet'. 등급만 넘기고 판 선택은 CLI 에 맡긴다."""
    m = (model or "").lower()
    for key, alias in _ALIAS.items():
        if key in m:
            return alias
    return model or ""


# ── anthropic 응답 흉내 ────────────────────────────────────────────────────
@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class _Block:
    type: str = "text"
    text: str = ""


@dataclass
class _Resp:
    """`messages.create()` 가 돌려주는 것과 **같은 자리**만 갖춘다.

    호출부가 쓰는 것은 `resp.content[*].text` · `resp.model` · `resp.usage` 뿐이다
    (app/usage.py 의 계측도 model·usage 만 본다).
    """
    content: list = field(default_factory=list)
    model: str = ""
    usage: _Usage = field(default_factory=_Usage)
    stop_reason: str = "end_turn"
    role: str = "assistant"
    type: str = "message"


def _text_of(content) -> str:
    """messages 의 content — 글자일 수도, 블록 목록일 수도 있다."""
    if isinstance(content, str):
        return content
    got = []
    for b in content or []:
        if isinstance(b, str):
            got.append(b)
        elif isinstance(b, dict) and b.get("type") == "text":
            got.append(str(b.get("text") or ""))
        else:
            t = getattr(b, "text", None)
            if t:
                got.append(str(t))
    return "\n".join(got)


#: 그림 파일 확장자. Claude Code 의 Read 가 열 수 있는 것들이다.
_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
        "image/gif": ".gif", "image/webp": ".webp"}


def _images_of(content) -> list[dict]:
    """messages 의 content 에서 그림 블록만 꺼낸다 → [{media_type, data(bytes)}]."""
    got = []
    for b in content or []:
        if not isinstance(b, dict) or b.get("type") != "image":
            continue
        src = b.get("source") or {}
        if src.get("type") != "base64":
            continue
        try:
            got.append({"media_type": str(src.get("media_type") or "image/png"),
                        "data": base64.standard_b64decode(str(src.get("data") or ""))})
        except Exception:  # noqa: BLE001
            continue
    return got


def _prompt_of(messages) -> str:
    """주고받은 말 → 한 덩이 글. 단발 호출이라 역할 표시를 글로 남긴다."""
    parts = []
    for m in messages or []:
        role = (m.get("role") if isinstance(m, dict) else getattr(m, "role", "")) or "user"
        body = _text_of(m.get("content") if isinstance(m, dict)
                        else getattr(m, "content", ""))
        if not body.strip():
            continue
        parts.append(body if role == "user" and len(messages) == 1
                     else f"[{role}]\n{body}")
    return "\n\n".join(parts)


def call(system: str, prompt: str, model: str = "",
         images: list[dict] | None = None) -> tuple[str, _Usage, str]:
    """한 번 묻는다. (답 글, 사용량, 오류). 예외를 올리지 않는다.

    images 가 있으면 **파일로 떨궈 놓고 Read 로 열어 보게** 한다. API 는 그림을
    메시지에 실어 보내지만 Claude Code 에는 그런 입구가 없다. 대신 일하는 자리에
    파일로 두고 경로를 일러 주면 읽는다. 이 호출에만 Read 를 열어 준다 — 일하는
    자리는 빈 폴더라 읽을 것이라곤 방금 떨군 그림뿐이다.
    """
    path = exe()
    if not path:
        return "", _Usage(), "claude 명령을 찾지 못했습니다"
    BOX.mkdir(parents=True, exist_ok=True)

    shed = None
    if images:
        shed = Path(tempfile.mkdtemp(prefix="img_", dir=str(BOX)))
        drops = []
        for i, im in enumerate(images):
            try:
                f = shed / f"image_{i + 1}{_EXT.get(str(im.get('media_type') or '').lower(), '.png')}"
                f.write_bytes(im.get("data") or b"")
                drops.append(str(f))
            except Exception:  # noqa: BLE001
                continue
        if drops:
            prompt = (prompt + "\n\n[딸린 그림] 아래 파일을 Read 로 **먼저 열어** 보고 "
                      "답하세요. 본문 표·스크린샷입니다.\n" + "\n".join(drops))
        else:
            shutil.rmtree(shed, ignore_errors=True)
            shed = None

    cmd = [path, "-p", "--output-format", "stream-json", "--verbose",
           "--tools", ("Read" if shed else ""), "--safe-mode", "--strict-mcp-config"]
    if system:
        cmd += ["--system-prompt", system]
    alias = model_alias(model)
    if alias:
        cmd += ["--model", alias]

    env = dict(os.environ)
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    # **열쇠를 물려주지 않는다.** Claude Code 는 ANTHROPIC_API_KEY 가 보이면 그것으로
    # 붙는다 — 구독이 아니라 API 로 계산된다. 이 앱은 열쇠를 .env 에서 읽어 환경에
    # 얹어 두기 때문에(호출부 아홉 군데가 "열쇠가 있는가"로 문지기를 삼는다) 그대로
    # 넘기면 구독을 쓰려고 만든 이 길이 도로 API 가 된다.
    for _k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
               "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_MODEL"):
        env.pop(_k, None)
    with _GATE:
        try:
            pr = subprocess.run(
                cmd, cwd=str(BOX), input=prompt, capture_output=True, text=True,
                encoding="utf-8", errors="replace", env=env, timeout=TIMEOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return "", _Usage(), f"{TIMEOUT}초 안에 답하지 않았습니다"
        except Exception as e:  # noqa: BLE001
            return "", _Usage(), f"{type(e).__name__}: {e}"
        finally:
            if shed:
                shutil.rmtree(shed, ignore_errors=True)

    said, use, err = [], _Usage(), ""
    got_model = ""
    for line in (pr.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        kind = d.get("type")
        if kind == "system" and d.get("subtype") == "init":
            got_model = str(d.get("model") or "")
        elif kind == "assistant":
            for c in ((d.get("message") or {}).get("content") or []):
                if c.get("type") == "text":
                    said.append(str(c.get("text") or ""))
        elif kind == "result":
            u = d.get("usage") or {}
            use = _Usage(int(u.get("input_tokens") or 0),
                         int(u.get("output_tokens") or 0))
            if d.get("is_error"):
                err = str(d.get("result") or "오류")
            # result 의 글이 더 온전하면 그것을 쓴다(assistant 줄이 쪼개져 올 때가 있다).
            _r = str(d.get("result") or "")
            if _r and len("".join(said)) < len(_r):
                said = [_r]
    text = "".join(said).strip()
    if not text and not err:
        err = (pr.stderr or "").strip()[:300] or "빈 응답"
    return text, use, err


class _Messages:
    """`client.messages` 자리. create() 만 있으면 된다."""

    def create(self, **kw):
        if backend() == API:                   # 사람이 API 를 골랐다 — 진짜 SDK 로
            return _real_anthropic()().messages.create(**kw)

        # 연장(tools)을 쓰는 호출은 이 길로 흉내 낼 수 없다. 몰래 API 로 보내면
        # 요금이 붙으므로, 고른 쪽을 지키고 무엇을 해야 하는지만 알린다.
        if kw.get("tools"):
            raise RuntimeError(
                "지금은 Claude 를 구독(Claude Code)으로 부르고 있습니다 — 연장(tools)을 "
                "쓰는 호출은 그 길로 할 수 없습니다. [설정 → Claude] 에서 'API 열쇠' 로 "
                "바꾼 뒤 다시 실행하세요.")

        system = kw.get("system") or ""
        if not isinstance(system, str):
            system = _text_of(system)
        msgs = kw.get("messages") or []
        prompt = _prompt_of(msgs)
        model = str(kw.get("model") or "")
        imgs = []
        for m in msgs:
            imgs += _images_of(m.get("content") if isinstance(m, dict)
                               else getattr(m, "content", None))

        text, use, err = call(system, prompt, model, imgs)
        if err and not text:
            raise RuntimeError(f"Claude Code 호출 실패: {err}")

        resp = _Resp(content=[_Block("text", text)], model=model, usage=use)
        try:                                   # 사용량 기록은 실패해도 답을 버리지 않는다
            from app import usage as _U

            _U.record(model, use)
        except Exception:  # noqa: BLE001
            pass
        return resp

    def parse(self, **kw):
        """구조화 출력(`output_format=<pydantic 모델>`) 흉내.

        진짜 SDK 는 스키마를 따로 넘겨 모델이 그 모양으로만 답하게 한다. 여기서는
        스키마를 **시스템 프롬프트에 실어** 같은 결과를 얻고, 받은 JSON 을 그 모델로
        검증해 `parsed_output` 에 담는다. 호출부(issuetrack/sb.py)는 차이를 모른다.
        """
        if backend() == API:                   # 사람이 API 를 골랐다 — 진짜 SDK 로
            return _real_anthropic()().messages.parse(**kw)
        fmt = kw.pop("output_format", None)
        if fmt is None:
            return self.create(**kw)
        try:
            schema = json.dumps(fmt.model_json_schema(), ensure_ascii=False)
        except Exception:  # noqa: BLE001
            schema = ""
        sys_txt = kw.get("system") or ""
        if not isinstance(sys_txt, str):
            sys_txt = _text_of(sys_txt)
        kw["system"] = (sys_txt + "\n\n[출력 형식] 아래 JSON 스키마에 맞는 **JSON 하나만** "
                        "답하세요. 설명·코드 울타리 없이 JSON 만.\n" + schema)

        resp = self.create(**kw)
        text = "".join(getattr(b, "text", "") for b in resp.content)
        obj = _json_of(text)
        if not obj:
            raise RuntimeError("구조화 출력을 읽지 못했습니다(JSON 이 아닙니다)")
        resp.parsed_output = fmt.model_validate(obj)
        resp.stop_details = ""
        return resp


def _json_of(text: str) -> dict:
    """답에서 JSON 한 덩이를 꺼낸다. ```json 울타리도 벗긴다."""
    s = (text or "").strip()
    if "```" in s:
        for one in s.split("```"):
            one = one.strip()
            if one.lower().startswith("json"):
                one = one[4:].strip()
            if one.startswith("{"):
                s = one
                break
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        return {}
    try:
        return json.loads(s[a:b + 1])
    except ValueError:
        return {}


class Client:
    """`anthropic.Anthropic()` 자리에 끼우는 물건."""

    def __init__(self, *a, **kw) -> None:
        self.messages = _Messages()


_REAL: dict = {}


def _real_anthropic():
    return _REAL.get("cls")


def status() -> dict:
    """화면에 보여 줄 지금 상태 — 어느 쪽인지, 양쪽이 쓸 수 있는지."""
    return {
        "backend": backend(),
        "claude": exe(),
        "has_key": bool(os.getenv("ANTHROPIC_API_KEY")
                        or os.getenv("ANTHROPIC_AUTH_TOKEN")),
    }


def install() -> bool:
    """`anthropic.Anthropic` 을 이 물건으로 바꿔 끼운다. 이미 했으면 False.

    호출부는 `anthropic.Anthropic()` 을 그대로 부른다 — 무엇이 끼워졌는지 모른다.
    """
    # **어느 쪽을 골랐든 끼운다.** 끼워 둬야 화면에서 바꿨을 때 다음 호출부터 바로
    # 듣는다. API 를 고른 동안에는 이 물건이 진짜 SDK 로 그대로 넘긴다.
    try:
        import anthropic
    except Exception:  # noqa: BLE001
        return False
    if getattr(anthropic.Anthropic, "_via_cli", False):
        return False
    _REAL["cls"] = anthropic.Anthropic
    Client._via_cli = True
    anthropic.Anthropic = Client
    return True
