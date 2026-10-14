"""FOTA Postgres 직접 연결 — 갱신·등록일 변경.

웹 화면(EmSync)이 하던 일을 DB 로 직접 한다. 웹 API 로는 **건별 원자성**을 만들 수 없었다
— 없는 SN 을 빼고 나머지만 반영되는 부분 성공이 났다. DB 는 한 트랜잭션으로 묶어
하나라도 어긋나면 전부 되돌린다(사용자 확정 2026-08-07).

표 구조(실측 2026-08-07, emsync_v02):
    terminal        122,965행  id · sn · android_api · support_end_date
                               created_date_time · update_date_time · partnumber · input_kind · token
    white_list       72,826행  id · terminal_id → terminal.id
                               os_type_master_id → os_type_master.id (NOT NULL)
                               created_date_time · update_date_time · partnumber
    os_type_master      197행  id · os_type · descr

  · support_end_date 는 **terminal 에만** 있다(화면의 'support end date' 가 이것).
  · osType 은 white_list → os_type_master 참조다. 문자열이 아니라 id 를 바꾼다.
  · terminal 에 있어도 white_list 행이 없으면 **등록된 것이 아니다**(5만 대가 그렇다).
    그래서 존재 확인은 반드시 inner join 이다.
  · terminal_history 는 단말이 실제로 업데이트를 받아간 기록이라 우리가 남기지 않는다.

**여기서 하는 일은 등록일 변경 하나뿐이다.** 신규 등록과 갱신은 FOTA 화면과 같은
엑셀 업로드로 처리한다(사용자 확정 2026-08-07).
"""
from __future__ import annotations
from app import shadow as _shadow

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

# 등록일 변경 시각 — 날짜는 요청값이고 시간은 이 값으로 고정한다.
# 요청이 오늘이 아닌 경우가 많아 now() 의 시각을 쓰면 의미가 없다(사용자 확정 2026-08-07).
REG_TIME = "01:01:01"

_LOOKUP = """
    select t.sn, t.id as terminal_id, t.android_api, t.support_end_date,
           w.id as white_list_id, w.update_date_time, m.id as os_type_id, m.os_type
      from terminal t
      join white_list w     on w.terminal_id = t.id
      join os_type_master m on m.id = w.os_type_master_id
     where t.sn = any(%s)
"""


def dsn() -> str:
    d = (os.getenv("FOTA_DB_DSN") or "").strip()
    if d:
        return d
    host = (os.getenv("FOTA_DB_HOST") or "").strip()
    if not host:
        raise RuntimeError("FOTA DB 접속 정보가 없습니다(.env 의 FOTA_DB_*).")
    return (f"postgresql://{os.getenv('FOTA_DB_USER','')}:{os.getenv('FOTA_DB_PASSWORD','')}"
            f"@{host}:{os.getenv('FOTA_DB_PORT','5432')}/{os.getenv('FOTA_DB_NAME','')}")


def _connect(*, read_only: bool):
    import psycopg

    con = psycopg.connect(dsn(), connect_timeout=20)
    con.read_only = read_only
    return con


# ── 읽기 전용 연결 재사용 ──────────────────────────────────────────────────
# 조회는 화면이 목록을 그릴 때마다 들어오는데, 매번 새로 접속하면 **연결에만 1.3초**가
# 든다(실측 2026-08-14). 연결 하나를 붙들고 돌려 쓴다.
#   · 오래 놀린 연결은 서버가 이미 끊었을 수 있으므로 버리고 새로 잡는다.
#   · autocommit — 트랜잭션을 열어 둔 채 놀면(idle in transaction) 서버 쪽 스냅샷을
#     붙잡아 두게 된다. 읽기 전용이라 열어 둘 이유가 없다.
#   · 쓰기 경로(등록일·osType 변경)는 예전처럼 매번 새 연결을 쓴다 — 손대지 않는다.
_RO_IDLE_MAX_SEC = 240.0
_ro_lock = threading.RLock()
_ro_con = None
_ro_used_at = 0.0


def _ro_drop() -> None:
    global _ro_con
    con, _ro_con = _ro_con, None
    if con is not None:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass


def _ro_connection():
    """재사용 중인 읽기 전용 연결. 호출자가 _ro_lock 을 잡고 있어야 한다."""
    global _ro_con, _ro_used_at
    con = _ro_con
    if con is not None and (con.closed or time.monotonic() - _ro_used_at > _RO_IDLE_MAX_SEC):
        _ro_drop()
        con = None
    if con is None:
        con = _connect(read_only=True)
        con.autocommit = True
        _ro_con = con
    _ro_used_at = time.monotonic()
    return con


def close_pool() -> None:
    """붙들고 있던 읽기 연결을 닫는다(종료·설정 변경 시)."""
    with _ro_lock:
        _ro_drop()


@dataclass
class Row:
    sn: str
    terminal_id: int
    android_api: str
    support_end_date: date | None
    white_list_id: int
    update_date_time: datetime | None
    os_type_id: int
    os_type: str


@dataclass
class DbReport:
    ok: bool = False
    dry_run: bool = True
    rows: list[Row] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    error: str = ""

    def lines(self) -> list[str]:
        head = ("미리보기(dry-run)" if self.dry_run else "실제 반영")
        out = [f"[{head}] 대상 {len(self.rows)}대"
               + (f" · 등록 안 된 SN {len(self.missing)}개" if self.missing else "")]
        if self.error:
            out.append("✘ " + self.error)
        out += [f"  · {c}" for c in self.changes[:40]]
        if self.missing:
            out.append("  ✘ 등록 안 됨: " + ", ".join(self.missing[:12])
                       + (f" 외 {len(self.missing) - 12}개" if len(self.missing) > 12 else ""))
        return out


def _lookup_once(sns: list[str]) -> list[Row]:
    con = _ro_connection()
    with con.cursor() as cur:
        cur.execute(_LOOKUP, (sns,))
        cols = [d.name for d in cur.description]
        return [Row(**dict(zip(cols, r))) for r in cur.fetchall()]


def lookup(sns: list[str]) -> tuple[list[Row], list[str]]:
    """등록된 SN 의 현재 값과, 등록 안 된 SN 목록. 읽기 전용."""
    sns = [s.strip() for s in sns if s and s.strip()]
    if not sns:
        return [], []
    with _ro_lock:
        try:
            rows = _lookup_once(sns)
        except Exception:  # noqa: BLE001
            # 재사용하던 연결이 그새 끊겼을 수 있다 — 버리고 딱 한 번 다시 잡는다.
            # 두 번째도 실패하면 진짜 장애이므로 그대로 올린다(호출부가 예비 경로로 간다).
            _ro_drop()
            rows = _lookup_once(sns)
    found = {r.sn for r in rows}
    return rows, [s for s in sns if s not in found]


def _inspect_name() -> str:
    import inspect

    return inspect.stack()[1].function


def _guard(sns: list[str], dry_run: bool) -> tuple[DbReport, list[Row]]:
    """공통 사전 검사 — 하나라도 등록 안 됐으면 **아무것도 하지 않는다**."""
    rep = DbReport(dry_run=dry_run)
    try:
        rows, missing = lookup(sns)
    except Exception as e:  # noqa: BLE001
        rep.error = f"{type(e).__name__}: {e}"
        return rep, []
    rep.rows, rep.missing = rows, missing
    if missing:
        rep.error = (f"등록되지 않은 SN 이 {len(missing)}개 있어 이 건은 처리하지 않습니다"
                     " — 전부 등록된 뒤에 다시 실행하세요.")
    return rep, rows


def change_registration_date(sns: list[str], reg_date: date, *, dry_run: bool = True) -> DbReport:
    """등록일 변경 — white_list.update_date_time 과 terminal.support_end_date 를 함께 바꾼다.

    update_date_time = 요청 등록일 + REG_TIME
    support_end_date = 요청 등록일 + 1년
    created_date_time 과 terminal.update_date_time 은 건드리지 않는다.
    """
    if not dry_run and _shadow.blocked("fota_db", _inspect_name(), sns=sns):
        dry_run = True
    rep, rows = _guard(sns, dry_run)
    if rep.error:
        return rep

    new_update = datetime.combine(reg_date, datetime.strptime(REG_TIME, "%H:%M:%S").time())
    new_end = reg_date.replace(year=reg_date.year + 1) if (reg_date.month, reg_date.day) != (2, 29) \
        else date(reg_date.year + 1, 3, 1)
    for r in rows:
        rep.changes.append(
            f"{r.sn}  update_date_time {r.update_date_time} → {new_update}"
            f"  ·  support_end_date {r.support_end_date} → {new_end}")
    if dry_run:
        rep.ok = True
        return rep

    ids = [r.terminal_id for r in rows]
    try:
        with _connect(read_only=False) as con, con.cursor() as cur:
            cur.execute("update white_list set update_date_time = %s where terminal_id = any(%s)",
                        (new_update, ids))
            n_wl = cur.rowcount
            cur.execute("update terminal set support_end_date = %s where id = any(%s)",
                        (new_end, ids))
            n_tm = cur.rowcount
            if n_wl != len(ids) or n_tm != len(ids):
                con.rollback()
                rep.error = (f"갱신 행 수가 맞지 않아 되돌렸습니다 "
                             f"(white_list {n_wl}/{len(ids)} · terminal {n_tm}/{len(ids)})")
                return rep
            con.commit()
        rep.ok = True
    except Exception as e:  # noqa: BLE001
        rep.error = f"{type(e).__name__}: {e}"
    return rep


def os_type_id(os_type: str) -> int | None:
    """osType 문자열 → os_type_master.id. 없으면 None."""
    name = (os_type or "").strip()
    if not name:
        return None
    with _connect(read_only=True) as con, con.cursor() as cur:
        cur.execute("select id from os_type_master where os_type = %s", (name,))
        r = cur.fetchone()
    return r[0] if r else None


def change_os_type(sns: list[str], os_type: str, *, android_api: str = "",
                   end_date: date | None = None, dry_run: bool = True) -> DbReport:
    """이미 등록된 단말의 **osType 을 바꾼다**.

    엑셀 업로드로는 안 되는 일이다 — white_list 행이 이미 있으면 새로 만들지도, 기존 것을
    갈아치우지도 않는다(실측 2026-08-10: PM451 10대가 2023년 White451PtoR 그대로 남았다).
    그래서 등록일 변경과 같은 방식으로 DB 에서 직접 바꾼다.

    android_api · end_date 를 주면 terminal 쪽도 함께 맞춘다(9→11 이 11→13 으로 바뀌면
    현재 OS 가 달라져 API 도 바뀐다).

    한 트랜잭션이다. 갱신된 행 수가 대상 수와 다르면 전부 되돌린다.
    """
    if not dry_run and _shadow.blocked("fota_db", _inspect_name(), sns=sns):
        dry_run = True
    rep, rows = _guard(sns, dry_run)
    if rep.error:
        return rep

    new_id = os_type_id(os_type)
    if new_id is None:
        rep.error = f"FOTA 에 '{os_type}' osType 이 없습니다 — 이름을 확인하세요."
        return rep

    api = (android_api or "").strip()
    for r in rows:
        parts = [f"{r.sn}  osType {r.os_type} → {os_type}"]
        if api and str(r.android_api) != api:
            parts.append(f"android_api {r.android_api} → {api}")
        if end_date and r.support_end_date != end_date:
            parts.append(f"support_end_date {r.support_end_date} → {end_date}")
        rep.changes.append("  ·  ".join(parts))
    if dry_run:
        rep.ok = True
        return rep

    ids = [r.terminal_id for r in rows]
    try:
        with _connect(read_only=False) as con, con.cursor() as cur:
            cur.execute("update white_list set os_type_master_id = %s "
                        "where terminal_id = any(%s)", (new_id, ids))
            n_wl = cur.rowcount
            if n_wl != len(ids):
                con.rollback()
                rep.error = f"white_list 갱신 {n_wl}/{len(ids)} — 되돌렸습니다"
                return rep
            if api:
                cur.execute("update terminal set android_api = %s where id = any(%s)",
                            (api, ids))
                if cur.rowcount != len(ids):
                    con.rollback()
                    rep.error = f"terminal.android_api 갱신 {cur.rowcount}/{len(ids)} — 되돌렸습니다"
                    return rep
            if end_date:
                cur.execute("update terminal set support_end_date = %s where id = any(%s)",
                            (end_date, ids))
                if cur.rowcount != len(ids):
                    con.rollback()
                    rep.error = f"terminal.support_end_date 갱신 {cur.rowcount}/{len(ids)} — 되돌렸습니다"
                    return rep
            con.commit()
        rep.ok = True
    except Exception as e:  # noqa: BLE001
        rep.error = f"{type(e).__name__}: {e}"
    return rep
