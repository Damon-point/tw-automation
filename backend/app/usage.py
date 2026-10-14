"""Claude API 토큰 사용량·비용 추적.

- 이 앱이 하는 모든 anthropic `messages.create` 호출을 자동 계측(install())해 토큰·비용을 기록.
- 비용은 응답의 토큰 수 × **공개 단가**로 계산한 **추정치**(Anthropic 은 호출당 금액을 주지 않음).
  실제 청구액(계약 할인 등)과 다를 수 있고, '이 앱이 한 호출'만 집계한다(계정 전체 아님).
- 저장: 로컬 PostgreSQL 의 usage 스키마(설정이 없으면 전용 SQLite `usage.db`). 작업(operation) 라벨은 contextvar 로 스테이지가 설정.
"""
from __future__ import annotations

import contextvars
from datetime import datetime
from pathlib import Path

from sqlalchemy import Column, Float, Integer, MetaData, Table, Text, text

_DB = Path(__file__).resolve().parent.parent / "usage.db"

# 공개 단가 ($ per 1M tokens) = (input, output). 2026-07 기준.
_PRICING = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
}
_DEFAULT_PRICE = (5.0, 25.0)

_ctx_op = contextvars.ContextVar("usage_op", default="")
_ctx_mail = contextvars.ContextVar("usage_mail", default=None)


def set_operation(label: str, mail_id=None) -> None:
    """이후 이 컨텍스트(스레드)에서 발생하는 Claude 호출에 붙일 작업 라벨을 설정."""
    _ctx_op.set(label or "")
    _ctx_mail.set(mail_id)


def _price(model: str) -> tuple[float, float]:
    m = (model or "").lower()
    for k, v in _PRICING.items():
        if k in m:
            return v
    return _DEFAULT_PRICE


def _cost(model, inp, out, cache_read=0, cache_write=0) -> float:
    pin, pout = _price(model)
    # cache read ≈ 0.1×, cache write ≈ 1.25× (입력 단가 기준)
    return (inp * pin + out * pout + cache_read * pin * 0.1 + cache_write * pin * 1.25) / 1_000_000


_meta = MetaData()
_usage = Table(
    "token_usage", _meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", Text), Column("day", Text, index=True), Column("month", Text, index=True),
    Column("model", Text), Column("operation", Text), Column("mail_id", Text),   # 메일 번호 또는 작업 키('job-18')
    Column("input_tokens", Integer), Column("output_tokens", Integer),
    Column("cache_read", Integer), Column("cache_write", Integer), Column("cost_usd", Float),
)
_engine = None


def _eng():
    """엔진은 처음 쓸 때 만든다 — import 만으로 DB 에 붙지 않게."""
    global _engine
    if _engine is None:
        from app.dbconf import make_engine

        _engine = make_engine("usage", _DB, wal=False)
        _meta.create_all(_engine)
    return _engine


def record(model, usage, operation=None, mail_id=None) -> None:
    """한 번의 Claude 호출 사용량을 기록. usage = 응답의 .usage 객체."""
    try:
        inp = int(getattr(usage, "input_tokens", 0) or 0)
        out = int(getattr(usage, "output_tokens", 0) or 0)
        cr = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        cw = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    except Exception:  # noqa: BLE001
        return
    op = operation if operation is not None else _ctx_op.get()
    mid = mail_id if mail_id is not None else _ctx_mail.get()
    now = datetime.now()
    cost = _cost(model, inp, out, cr, cw)
    try:
        with _eng().begin() as c:
            c.execute(_usage.insert().values(
                ts=now.isoformat(timespec="seconds"), day=now.strftime("%Y-%m-%d"),
                month=now.strftime("%Y-%m"), model=model or "?", operation=op or "기타",
                mail_id=None if mid is None else str(mid),
                input_tokens=inp, output_tokens=out, cache_read=cr, cache_write=cw, cost_usd=cost))
    except Exception:  # noqa: BLE001
        pass   # 사용량 기록 실패가 본 작업을 막지 않는다.


def install() -> None:
    """anthropic Messages 호출을 감싸 사용량을 자동 기록(1회만).

    `create` 와 `parse` 를 **둘 다** 감싼다. 구조화 출력용 `parse()` 는 내부에서
    `create()` 를 부르지 않고 `_post()` 로 직접 가기 때문에, create 만 감싸면
    parse 로 쓴 비용이 대시보드에서 통째로 빠진다.
    """
    try:
        from anthropic.resources.messages import Messages
    except Exception:  # noqa: BLE001
        return

    def _wrap(name: str) -> None:
        orig = getattr(Messages, name, None)
        if orig is None or getattr(orig, "_usage_wrapped", False):
            return

        def _wrapped(self, *args, __orig=orig, **kwargs):
            resp = __orig(self, *args, **kwargs)
            try:
                model = getattr(resp, "model", None) or kwargs.get("model")
                usage = getattr(resp, "usage", None)
                if usage is not None:
                    record(model, usage)
            except Exception:  # noqa: BLE001
                pass
            return resp

        _wrapped._usage_wrapped = True
        setattr(Messages, name, _wrapped)

    _wrap("create")
    _wrap("parse")


# ── 조회(대시보드용) ─────────────────────────────────────────────
def _rows(sql: str, args: dict | None = None) -> list[dict]:
    with _eng().connect() as c:
        return [dict(r._mapping) for r in c.execute(text(sql), args or {})]


_AGG = "COALESCE(SUM(cost_usd),0) cost, COALESCE(SUM(input_tokens+output_tokens),0) tokens, COUNT(*) calls"


def totals() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    month = datetime.now().strftime("%Y-%m")

    def agg(where, arg):
        return _rows(f"SELECT {_AGG} FROM token_usage WHERE {where}", arg)[0]

    return {"today": agg("day=:v", {"v": today}),
            "month": agg("month=:v", {"v": month}),
            "all": agg("1=1", {})}


def by_day(n: int = 30) -> list[dict]:
    return list(reversed(_rows(
        f"SELECT day, {_AGG} FROM token_usage GROUP BY day ORDER BY day DESC LIMIT :n", {"n": n})))


def by_month(n: int = 12) -> list[dict]:
    return list(reversed(_rows(
        f"SELECT month, {_AGG} FROM token_usage GROUP BY month ORDER BY month DESC LIMIT :n", {"n": n})))


def by_operation(scope: str = "month") -> list[dict]:
    where, arg = "1=1", {}
    if scope == "today":
        where, arg = "day=:v", {"v": datetime.now().strftime("%Y-%m-%d")}
    elif scope == "month":
        where, arg = "month=:v", {"v": datetime.now().strftime("%Y-%m")}
    return _rows(
        f"SELECT operation, {_AGG} FROM token_usage WHERE {where} "
        "GROUP BY operation ORDER BY cost DESC", arg)


def recent(n: int = 50) -> list[dict]:
    return _rows(
        "SELECT ts, model, operation, mail_id, input_tokens, output_tokens, "
        "cache_read, cost_usd FROM token_usage ORDER BY id DESC LIMIT :n", {"n": n})
