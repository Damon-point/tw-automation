"""WhiteList 메일 **2차 판별** — Claude 가 본문을 읽고 등록 요청인지 판단한다.

1차(mail_filter)는 일련번호·키워드로 후보를 좁히는 룰이고, 여기서는
"이 메일이 실제로 FOTA 화이트리스트 등록 요청인가"를 문맥으로 판단한다.

비용을 낮추려고 세 가지를 한다(실측 근거):
  · 인용문 제거   — 회신에 붙는 이전 대화를 버린다(본문 중앙값 4,084자 → 575자)
  · 스레드 묶기   — 같은 대화는 한 번만 판정한다(후보 64건 → 스레드 32개)
  · 결과 캐시     — 한 번 판정한 스레드는 다시 묻지 않는다(work/whitelist_triage.json)

**일련번호는 Claude 에게 맡기지 않는다.** 룰이 원문 전체에서 뽑은 SN 목록을 같이 넘기고,
Claude 는 '요청인지 여부 · 모델 · OS' 만 판단한다(인용부에만 SN 이 있는 메일이 실제로 38건 있었다).
읽기 전용 — 메일을 고치거나 지우지 않는다.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from app.atomic_io import atomic_write_text, file_lock
from app.integrations.claude_cli import have_auth  # noqa: E402

# 단순 분류라 작은 모델로 충분하다. 필요하면 env 로 올린다.
_MODEL = os.getenv("WHITELIST_TRIAGE_MODEL", "claude-haiku-4-5")
_BODY_CHARS = 4000          # 인용 제거 후에도 이 길이로 자른다(중앙값 575자라 대부분 안 잘림)
_THREADS_PER_CALL = 10      # 한 요청에 스레드 여러 개 → 시스템 프롬프트 분산

# 판정 프롬프트 판(版). 프롬프트나 본문 구성 방식을 바꾸면 **반드시 올린다** —
# 캐시 키에 들어가 있어 올리면 전량 재판정된다(스레드 70개 기준 약 0.1달러).
#   v1 → v2 : 완료 안내·확인 회신을 요청 아님으로, 최신 메일과 인용을 분리해 제시(2026-08-06)
#   v2 → v3 : DQA·SQA FOTA 테스트 협조 요청을 요청으로 고정 + '첨부 참조' 회신은
#             결과 회신이 아님(사용자 확정 2026-08-06)
_PROMPT_VERSION = 3

_CACHE = Path(__file__).resolve().parent.parent.parent / "work" / "whitelist_triage.json"

# ── 실제 API 호출 게이트 ──────────────────────────────────────────────────
# **비용이 발생하는 작업이라 기본은 꺼 둔다.** False 인 동안 triage() 는 호출하지 않고
# '판정 대기 N건 · 예상 비용' 만 계산해 돌려준다(dry-run).
# 켜는 것은 사용자 판단 — Dropbox 삭제(_ALLOW_DROPBOX_DELETE)·FOTA 쓰기(_ALLOW_FOTA_WRITES)와 같은 규약.
_ALLOW_TRIAGE_CALLS = True

# 비용 추정용(공개 단가 $/1M, Haiku 기준). 실측: 스레드당 입력 ~590 · 출력 ~150 토큰.
_EST_IN_PER_THREAD = 590
_EST_OUT_PER_THREAD = 150
_EST_PRICE_IN, _EST_PRICE_OUT = 1.0, 5.0


def estimate_cost(n_threads: int) -> float:
    """스레드 n 개를 판정할 때의 예상 비용($)."""
    return (n_threads * _EST_IN_PER_THREAD / 1e6 * _EST_PRICE_IN
            + n_threads * _EST_OUT_PER_THREAD / 1e6 * _EST_PRICE_OUT)

# 인용 시작 지점 — 여기부터는 이전 대화라 버린다.
_QUOTE_RE = re.compile(
    r"^\s*(>|\|)"
    r"|^-{2,}\s*(original message|원본 메시지)"
    r"|^\s*(from|보낸사람|sent|보낸\s*날짜)\s*:",
    re.I,
)
_RE_PREFIX_RE = re.compile(r"^\s*(re|fw|fwd|답장|전달)\s*:\s*", re.I)

_SYS = """당신은 Point Mobile 사내 메일을 분류합니다.
각 메일이 **FOTA 화이트리스트 등록 요청**인지 판단하세요.

화이트리스트 등록 요청 = 특정 단말(일련번호)을 지정해 OS 업그레이드/업데이트를 받게 해달라는 요청.
표현은 다양합니다("FOTA 등록", "화이트리스트 추가", "OS 무상 upgrade 문의", "A13 upgrade for PM451" 등).

**DQA·SQA 의 FOTA 테스트 협조 요청도 등록 요청입니다**(사용자 확정). 배포된 RC 를
검증하려고 테스트 단말 SN 과 현재/목표 OS 를 보내오면, 그 단말을 화이트리스트에
등록해주는 실제 작업입니다. '테스트용이라 정규 등록이 아니다'라고 판단하지 마세요.
제목이 `... FOTA Test 요청 // RE: [PM45][A9] 45.81 배포` 처럼 배포 공지에 달린
회신이어도, 단말 SN 을 주며 확인을 요청하면 is_request=true 입니다.

요청이 **아닌** 것:
  · 배포 공지·릴리즈 알림 자체, 그리고 단말 SN 없는 단순 회신
  · 이슈/버그 리포트, 기술 문의
  · 단말 반출·재고·구매 요청
  · **이미 처리된 건에 대한 결과 회신**

결과 회신 판단이 가장 중요합니다. 메일에 SN 과 '등록' 이 같이 나와도, **가장 최근
메일이 무엇을 말하는지**로 판단하세요. 처리자가 보낸 완료 안내("등록되었습니다",
"등록 완료했습니다", "has been registered", "added to the whitelist")나 요청자가 보낸
확인·감사 회신("확인 완료했습니다", "정상 동작 확인", "감사합니다")이면 is_request=false
입니다. 앞선 요청 내용이 인용문으로 딸려와도 마찬가지입니다 — **이미 끝난 건**입니다.

반대로 완료 안내 뒤에 **새 단말을 추가로 요청**하거나 **등록을 취소·수정**해달라고 하면
그건 새 작업이므로 is_request=true 입니다.

짧은 회신이라고 다 결과 회신은 아닙니다. "Please find attached" · "첨부 확인 부탁드립니다"
처럼 **요청에 필요한 자료(단말 목록)를 보내는** 메일은 요청의 일부입니다. 이전 대화가
등록 요청이고 완료 안내가 아직 없으면 is_request=true 입니다.

각 메일마다 JSON 객체를 만들어 {"results":[...]} 형태로만 답하세요. 필드:
  id        : 입력에 주어진 id 그대로
  is_request: true/false
  confidence: 0.0~1.0
  model     : 단말 모델명(PM95 등). 모르면 ""
  from_os   : 현재 OS 숫자(9,11,13...). 모르면 ""
  to_os     : 업그레이드할 OS 숫자. 모르면 ""
  reason    : 한국어 한 문장 근거

본문에 없는 값을 지어내지 마세요. 모르면 빈 문자열로 두세요."""


def strip_quotes(body: str) -> str:
    """회신에 붙은 이전 대화를 잘라낸다(첫 인용 표시 지점까지만 남김)."""
    out: list[str] = []
    for line in (body or "").splitlines():
        if _QUOTE_RE.match(line):
            break
        out.append(line)
    return "\n".join(out).strip()


def thread_key(subject: str) -> str:
    """RE:/FW: 를 떼어 같은 대화를 하나로 묶는 키."""
    s = subject or ""
    for _ in range(5):
        s2 = _RE_PREFIX_RE.sub("", s)
        if s2 == s:
            break
        s = s2
    return re.sub(r"\s+", " ", s).strip().lower()


def received_key(r: dict) -> tuple:
    """정렬용 수신 시각. id 는 저장 순서일 뿐 도착 순서가 아니다(실측: 늦게 온 회신의 id 가 더 작았다)."""
    from email.utils import parsedate_to_datetime

    raw = r.get("received_at") or ""
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            return (0, dt.timestamp(), r["id"])
    except Exception:  # noqa: BLE001
        pass
    return (1, 0.0, r["id"])          # 날짜를 못 읽으면 뒤로


def newest_ts(g: dict) -> float:
    """스레드에서 **가장 늦게 도착한** 메일의 수신 시각(epoch 초). 못 읽으면 0.0.

    처리 완료 표시가 아직 유효한지 판단하는 기준이다(mail_state 참고).
    rep 을 그냥 쓰지 않는 이유는, 날짜를 못 읽은 메일이 정렬상 rep 이 될 수 있어서다.
    """
    rows = g.get("members") or ([g["rep"]] if g.get("rep") else [])
    return max((received_key(r)[1] for r in rows), default=0.0)


def is_reply(subject: str) -> bool:
    """제목에 RE:/FW: 가 붙었는가 = 앞선 메일의 회신·전달."""
    return bool(_RE_PREFIX_RE.match(subject or ""))


def group_threads(rows: list[dict]) -> list[dict]:
    """후보 메일들을 **요청 단위**로 묶는다. 대표 = 가장 최근 메일, SN 은 묶음 안 합집합.

    제목만으로 묶으면 안 된다 — 같은 사람이 **같은 제목으로 새 요청을 반복해서** 보낸다
    (실측: 'FOTA 등록 요청 - Carema DE' 가 2주간 원본 4통, 각각 다른 단말 요청이었다).
    그래서:
      · `In-Reply-To`/`References` 헤더가 있으면 그걸로 정확히 잇는다(신규 수신분).
      · 헤더가 없으면(기존 저장분) 제목 접두사로 판단한다 —
        RE:/FW: 가 붙은 것만 **가장 최근의 같은 제목 묶음**에 붙이고,
        접두사 없는 원본은 **새 요청**으로 분리한다.
    """
    ordered = sorted(rows, key=received_key)          # 실제 수신 시각 순(오래된 것부터)
    groups: list[dict] = []
    by_msgid: dict[str, dict] = {}                     # Message-ID → 그 메일이 속한 묶음
    latest_by_subject: dict[str, dict] = {}            # 제목 → 가장 최근 묶음(폴백용)

    def _new_group(r: dict) -> dict:
        g = {
            # 키는 '제목 + 최초 메일 id' — 같은 제목의 다른 요청과 구분되고, 실행할 때마다 같다.
            "key": f"{thread_key(r['subject'])}#{r['id']}",
            "rep": r, "mail_ids": [], "serials": [], "count": 0, "members": [],
        }
        groups.append(g)
        return g

    for r in ordered:
        parent = None
        for mid in _parent_ids(r):                     # 헤더로 부모 찾기(가장 정확)
            parent = by_msgid.get(mid)
            if parent is not None:
                break
        if parent is None and is_reply(r["subject"]):   # 헤더 없으면 제목 접두사로
            parent = latest_by_subject.get(thread_key(r["subject"]))
        g = parent if parent is not None else _new_group(r)

        g["count"] += 1
        g["members"].append(r)
        g["mail_ids"].append(r["id"])
        for s in r["serials"]:
            if s not in g["serials"]:
                g["serials"].append(s)
        if received_key(r) > received_key(g["rep"]):
            g["rep"] = r
        if r.get("message_id"):
            by_msgid[r["message_id"]] = g
        latest_by_subject[thread_key(r["subject"])] = g
    return groups


def _parent_ids(r: dict) -> list[str]:
    """이 메일이 답장한 대상 Message-ID 목록(In-Reply-To 우선, 그다음 References 역순)."""
    out: list[str] = []
    for tok in re.findall(r"<[^>]+>", r.get("in_reply_to") or ""):
        out.append(tok)
    for tok in reversed(re.findall(r"<[^>]+>", r.get("refs") or "")):
        if tok not in out:
            out.append(tok)
    return out


# ── 판정 결과 캐시 ────────────────────────────────────────────────────────
def _load_cache() -> dict:
    if _CACHE.exists():
        try:
            return json.loads(_CACHE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _cache_id(g: dict) -> str:
    """스레드 + 구성 메일 + 프롬프트 판(版)을 반영한 캐시 키.

    새 회신이 오면 다시 판정한다. 프롬프트를 고치면 예전 판정이 그대로 남아 고친
    효과가 안 나므로 _PROMPT_VERSION 도 키에 넣는다 — 올리면 전량 재판정된다.
    """
    return f"{g['key']}|{max(g['mail_ids'])}|v{_PROMPT_VERSION}"


def _is_answered(v) -> bool:
    """실제 Claude 판정이 담긴 verdict 인가.

    answered=False 는 배치 응답에서 그 스레드에 대한 결과가 빠져(=Claude 가 답하지 못해)
    기본값으로 채워진 것이다. 확정 '요청 아님' 이 아니므로 판정으로 치면 안 된다.
    (answered 키가 없는 옛 항목은 정상 판정으로 간주.)
    """
    return bool(v) and v.get("answered", True)


def cached_verdicts() -> dict:
    # 미응답(answered=False)은 캐시로 치지 않는다 → UI 에선 '판별 전'으로 보이고
    # 다음 triage 가 다시 묻는다(거짓 '요청 아님' 으로 영구히 숨는 것을 막는다).
    return {k: v for k, v in _load_cache().items() if _is_answered(v)}


def _save_verdicts(new: dict) -> None:
    with file_lock(_CACHE):
        data = _load_cache()
        data.update(new)
        atomic_write_text(_CACHE, json.dumps(data, ensure_ascii=False, indent=2))


# 인용을 걷어낸 본문이 이보다 짧으면 판단 근거가 부족하다고 보고 원문을 쓴다.
# (실제 사례: 최신 메일이 "Please find attached." 한 줄이고 요청과 SN 은 인용부에 있었다.
#  이때 인용을 자르면 Claude 가 '요청 아님' 으로 잘못 판정한다.)
_MIN_BODY_CHARS = 300


def prompt_bodies(group: dict) -> tuple[str, str]:
    """(최신 메일 본문, 참고용 이전 대화). **판정 기준은 최신 메일**이다.

    예전에는 스레드에서 가장 내용 많은 본문 하나만 보냈다. 그러면 완료 안내 회신이
    앞선 요청 본문으로 판정돼 '요청'으로 잘못 살아난다(실측: `Re: FOTA 등록 요청의 건`
    = 등록 완료 안내가 요청으로 판정됨). 그래서 최신 메일을 따로 세우고, 근거가
    모자랄 때만 이전 대화를 **참고로** 덧붙인다.
    """
    rep = group["rep"]
    latest = strip_quotes(rep["body_text"])[:_BODY_CHARS]
    if len(latest) >= _MIN_BODY_CHARS:
        return latest, ""
    members = group.get("members") or [rep]
    best = max(members, key=lambda r: len(strip_quotes(r["body_text"])))
    ctx = strip_quotes(best["body_text"]) if best is not rep else ""
    if len(ctx) < _MIN_BODY_CHARS:
        ctx = rep["body_text"] or ""          # 인용 포함 원문 — 비용보다 오판이 비싸다
    return latest, ctx[:_BODY_CHARS]


def _prompt_for(batch: list[dict]) -> str:
    parts = []
    for g in batch:
        r = g["rep"]
        latest, ctx = prompt_bodies(g)
        # id 는 대표 메일 id(정수) 하나로 준다 — 예전엔 긴 한글 캐시키를 줘서
        # Claude 가 정확히 되돌려주지 못해(특히 비슷한 제목 배치) 매칭이 빠지는 일이 있었다.
        block = (
            f"--- id: {r['id']}\n"
            f"제목: {r['subject']}\n"
            f"보낸사람: {r['sender']}\n"
            f"스레드 메일 수: {g.get('count', 1)}\n"
            f"규칙이 찾은 일련번호({len(g['serials'])}건): {', '.join(g['serials'][:40]) or '없음'}\n"
            f"[판정 기준] 최신 메일 본문:\n{latest or '(본문 없음)'}\n"
        )
        if ctx:
            block += ("[참고용] 이전 대화 — 이것만 보고 요청이라고 판단하지 마세요:\n"
                      f"{ctx}\n")
        parts.append(block)
    return "\n".join(parts) + "\n\n위 메일들을 판정해 JSON 으로 답하세요."


def triage(rows: list[dict], *, force: bool = False, max_threads: int = 60,
           allow_calls: bool | None = None) -> dict:
    """후보 메일을 스레드로 묶어 Claude 로 판정. 캐시된 스레드는 건너뛴다.

    allow_calls: 실제 API 호출 허용 여부. None 이면 모듈 게이트(_ALLOW_TRIAGE_CALLS).
                 꺼져 있으면 호출하지 않고 대기 건수·예상 비용만 돌려준다.

    반환: {verdicts, asked, cached, pending, est_cost, blocked, error}
    """
    groups = group_threads(rows)
    cache = _load_cache()
    # 아직 확정 판정이 없는 스레드 = 캐시에 없거나, 있어도 미응답(answered=False)인 것.
    todo = [g for g in groups if force or not _is_answered(cache.get(_cache_id(g)))]
    todo = todo[:max_threads]
    if not todo:
        return {"verdicts": cache, "asked": 0, "cached": len(groups), "error": "",
                "pending": 0, "est_cost": 0.0, "blocked": False}

    if not (_ALLOW_TRIAGE_CALLS if allow_calls is None else allow_calls):
        # 게이트가 닫혀 있으면 **호출하지 않는다**. 몇 건이 대기 중이고 얼마 드는지만 알린다.
        return {
            "verdicts": cache, "asked": 0, "cached": len(groups) - len(todo),
            "pending": len(todo), "est_cost": estimate_cost(len(todo)),
            "blocked": True,
            "error": (f"판정 대기 {len(todo)}건 (예상 ${estimate_cost(len(todo)):.3f}). "
                      "실제 판정은 허용 후 실행됩니다."),
        }

    if not have_auth():
        return {"verdicts": cache, "asked": 0, "cached": 0, "pending": len(todo),
                "est_cost": 0.0, "blocked": True,
                "error": "Claude 를 부를 수 없습니다 — Claude Code(구독) 로그인이나 .env 의 ANTHROPIC_API_KEY 중 하나가 있어야 합니다."}

    import anthropic

    from app import usage
    from app.pipeline.ai_edit import _extract_json

    usage.set_operation("WhiteList 메일 판별", None)
    client = anthropic.Anthropic()
    new: dict = {}
    err = ""
    for i in range(0, len(todo), _THREADS_PER_CALL):
        batch = todo[i:i + _THREADS_PER_CALL]
        try:
            resp = client.messages.create(
                model=_MODEL, max_tokens=2000, system=_SYS,
                messages=[{"role": "user", "content": _prompt_for(batch)}],
            )
            text = "".join(
                b.text for b in resp.content if getattr(b, "type", None) == "text"
            )
            parsed = _extract_json(text) or {}
            by_id = {str(r.get("id")): r for r in (parsed.get("results") or [])}
            for g in batch:
                v = by_id.get(str(g["rep"]["id"]))
                if not v:
                    # Claude 가 이 스레드를 답하지 않았다 → 캐시하지 않는다(다음 triage 가 다시 물음).
                    # 예전처럼 거짓 '요청 아님' 으로 저장해 영구히 숨기지 않는다.
                    continue
                new[_cache_id(g)] = {
                    "is_request": bool(v.get("is_request")),
                    "confidence": float(v.get("confidence") or 0),
                    "model": str(v.get("model") or ""),
                    "from_os": str(v.get("from_os") or ""),
                    "to_os": str(v.get("to_os") or ""),
                    "reason": str(v.get("reason") or "").strip() or "판정 결과 없음",
                    "serials": g["serials"],
                    "subject": g["rep"]["subject"],
                    "mail_ids": g["mail_ids"],
                    "answered": True,
                }
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            break
    if new:
        _save_verdicts(new)
    cache.update(new)
    return {"verdicts": cache, "asked": len(new),
            "cached": len(groups) - len(todo), "pending": 0,
            "est_cost": 0.0, "blocked": False, "error": err}
