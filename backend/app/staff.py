"""사내 인원 명부 — 메일을 보내오는 사람과 그 역할을 관리자가 한 곳에서 관리한다.

예전에는 사람 목록이 세 군데에 흩어져 있었다:
  · DQA 발신자 9명 → work/whitelist_dqa_senders.json (화면 없음, 파일 직접 수정)
  · 회신 제외 2명   → mail_filter.EXCLUDED_SENDERS (코드 하드코딩)
  · 로그인 계정     → users 테이블 (앱 사용자만, 사내 전원이 아님)

앞의 둘을 이 명부가 대신한다. 로그인 계정은 성격이 달라 그대로 둔다(사용자 확정).

주소 비교는 소문자로 한다. 판정 결과 캐시(mail_filter._CLASSIFY_CACHE)가 is_excluded
를 들고 있으므로, 명부를 고치면 반드시 캐시를 비운다 — 안 그러면 화면이 안 바뀐다.
"""
from __future__ import annotations

from app.database import get_session
from app.models import Staff

ACTIVE, LEFT = "재직", "퇴사"
STATUSES = (ACTIVE, LEFT)


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def _to_dict(s: Staff) -> dict:
    return {
        "id": s.id, "name": s.name or "", "email": s.email or "",
        "status": s.status or ACTIVE, "is_dqa": bool(s.is_dqa),
        "is_handler": bool(s.is_handler), "team": s.team or "",
        "memo": s.memo or "", "updated_by": s.updated_by or "",
    }


def list_staff() -> list[dict]:
    """명부 전체. 재직 먼저, 그다음 이름순."""
    with get_session() as db:
        rows = db.query(Staff).all()
        out = [_to_dict(s) for s in rows]
    out.sort(key=lambda r: (r["status"] != ACTIVE, r["name"] or r["email"]))
    return out


def save_staff(*, id: int | None, name: str, email: str, status: str = ACTIVE,
               is_dqa: bool = False, is_handler: bool = False, team: str = "",
               memo: str = "", by: str = "") -> tuple[bool, str]:
    """추가(id=None) 또는 수정. (성공여부, 메시지).

    메일 주소는 명부에서 유일해야 한다 — 같은 사람이 두 행이면 역할 판정이 갈린다.
    """
    addr = _norm(email)
    if not addr or "@" not in addr:
        return False, "메일 주소를 정확히 입력하세요."
    with get_session() as db:
        dup = db.query(Staff).filter(Staff.email == addr).first()
        if dup and (id is None or dup.id != id):
            return False, f"이미 등록된 주소입니다: {addr}"
        s = db.get(Staff, id) if id is not None else None
        if id is not None and s is None:
            return False, "대상을 찾을 수 없습니다(이미 삭제된 듯합니다)."
        if s is None:
            s = Staff()
            db.add(s)
        s.name = (name or "").strip()
        s.email = addr
        s.status = status if status in STATUSES else ACTIVE
        s.is_dqa = bool(is_dqa)
        s.is_handler = bool(is_handler)
        s.team = (team or "").strip()
        s.memo = (memo or "").strip()
        s.updated_by = by or ""
        db.commit()
    _invalidate()
    return True, "저장했습니다."


def delete_staff(id: int) -> tuple[bool, str]:
    with get_session() as db:
        s = db.get(Staff, id)
        if s is None:
            return False, "대상을 찾을 수 없습니다."
        db.delete(s)
        db.commit()
    _invalidate()
    return True, "삭제했습니다."


# ── 역할 조회 ──────────────────────────────────────────────────────────────
def _emails_where(**flags) -> set[str]:
    with get_session() as db:
        q = db.query(Staff.email)
        for k, v in flags.items():
            q = q.filter(getattr(Staff, k) == v)
        return {(e or "").lower() for (e,) in q.all() if e}


def dqa_addresses() -> set[str]:
    """DQA FOTA 테스트 전용 처리 대상 발신자."""
    return _emails_where(is_dqa=True)


def handler_addresses() -> set[str]:
    """화이트리스트 처리 담당 — 이 사람이 보낸 메일은 신규 요청이 아니다."""
    return _emails_where(is_handler=True)


def _invalidate() -> None:
    """명부가 바뀌면 파생 캐시를 버린다(메일 판정에 is_excluded 가 들어있다)."""
    _TEAM_MAP.clear()
    try:
        from app.whitelist import mail_filter as F

        F._CLASSIFY_CACHE.clear()
    except Exception:  # noqa: BLE001
        pass


# ── 사람 → 팀 ──────────────────────────────────────────────────────────────
# Redmine 작성자는 'samuel.seo' 처럼 **메일 로컬파트**로 온다(주소가 아니다).
# 그래서 명부의 주소 앞부분으로 찾는다. 이슈마다 부르므로 한 번 만들고 캐시한다.
_TEAM_MAP: dict[str, str] = {}
UNKNOWN_TEAM = "?"


def _norm_person(p: str) -> str:
    return (p or "").strip().lower().replace(" ", ".")


def _team_map() -> dict[str, str]:
    if not _TEAM_MAP:
        with get_session() as db:
            for email, team in db.query(Staff.email, Staff.team).all():
                if email and team:
                    _TEAM_MAP[(email or "").lower().split("@", 1)[0]] = team
    return _TEAM_MAP


def team_of(person: str) -> str:
    """사람(로컬파트 또는 메일 주소) → 팀. 모르면 '?'.

    Release Note 이슈 선별이 이 값으로 'DQA 가 올린 사내 검증 건'을 걸러낸다.
    """
    key = _norm_person(person)
    if "@" in key:
        key = key.split("@", 1)[0]
    return _team_map().get(key, UNKNOWN_TEAM)


# ── 최초 1회 이관 ───────────────────────────────────────────────────────────
_LEGACY_HANDLERS = ("jayden.lee@pointmobile.com", "damon.park@pointmobile.com")

# 팀 명단 — 예전에는 rn_select.py 에 `_ROSTER_RAW` 로 하드코딩돼 있었다(사용자 제공).
# 명부로 한 번 옮긴 뒤에는 화면에서 관리한다. 원본 표기가 제각각이라(공백/점 혼용)
# 여기서 정규화해 넣는다. daisy.kim 은 2026-07 말 퇴사 — 역할은 유지하고 상태만 퇴사로
# 둔다(과거 메일·이슈가 계속 맞게 분류돼야 한다).
_LEGACY_TEAMS: dict[str, list[str]] = {
    "TA": ["damon.park", "jayden.lee", "samuel.seo", "daniel.chae"],
    "DQA": ["sally.yun", "luna.lee", "daisy.kim", "mark baek", "jamie.choi",
            "jinny ko", "thomas.jo", "alex.park", "jane.kim", "ivan.oh"],
    "SW1": ["jonas.kim", "roy.kim", "bruno.jeong", "jack.hong", "robin.cho",
            "nathan.yang", "andy.kim", "jason.kim"],
    "SW2": ["abel.kang", "mik.lee", "ryan.kim", "nomar.kwon", "tommy.kim",
            "jin.lee", "sijoo", "brandon.ha"],
    "Solution": ["olivia.park", "sheen.lee"],   # SW1/SW2 와 별개
    "Branding": ["steve.ahn"],
}
_LEGACY_LEFT = ("daisy.kim",)                  # 퇴사자(명단에는 남아 있던 사람)
_MERGE_FLAG = "staff_roster_merged.flag"


def _work_path(name: str):
    from pathlib import Path

    p = Path(__file__).resolve().parent.parent / "work"
    p.mkdir(parents=True, exist_ok=True)
    return p / name


def merge_team_roster() -> tuple[int, int]:
    """팀 명단을 명부에 **한 번만** 합친다. (추가, 갱신) 반환.

    이미 있는 사람은 team 이 비어 있을 때만 채운다 — 관리자가 화면에서 바꾼 값을
    덮어쓰면 안 된다. 완료 표시를 파일로 남겨, 관리자가 지운 사람이 되살아나지 않게 한다.
    """
    flag = _work_path(_MERGE_FLAG)
    if flag.exists():
        return 0, 0

    added = updated = 0
    with get_session() as db:
        existing = {(s.email or "").lower(): s for s in db.query(Staff).all()}
        for team, members in _LEGACY_TEAMS.items():
            for m in members:
                local = _norm_person(m)
                addr = f"{local}@pointmobile.com"
                left = local in _LEGACY_LEFT
                s = existing.get(addr)
                if s is None:
                    db.add(Staff(
                        name=local.replace(".", " ").title(), email=addr,
                        status=LEFT if left else ACTIVE, team=team,
                        is_dqa=(team == "DQA"), is_handler=False,
                        updated_by="(이관)"))
                    added += 1
                    continue
                changed = False
                if not (s.team or "").strip():
                    s.team = team
                    changed = True
                if team == "DQA" and not s.is_dqa:
                    s.is_dqa = True
                    changed = True
                if left and s.status != LEFT:
                    s.status = LEFT
                    changed = True
                updated += 1 if changed else 0
        db.commit()

    from app.atomic_io import atomic_write_text

    atomic_write_text(flag, "merged\n")
    _invalidate()
    return added, updated


def seed_from_legacy() -> int:
    """예전 목록(DQA json · 하드코딩 회신 제외)을 명부로 옮긴다. 옮긴 건수 반환.

    명부에 이미 행이 있으면 아무것도 하지 않는다 — 관리자가 지운 사람을 되살리면 안 된다.
    """
    with get_session() as db:
        if db.query(Staff).first() is not None:
            return 0

    from app.whitelist import service as WLS

    seeded: dict[str, dict] = {}
    for addr in WLS.dqa_senders().get("addresses", []):
        a = _norm(addr)
        if a:
            seeded[a] = {"is_dqa": True, "is_handler": False}
    for addr in _LEGACY_HANDLERS:
        a = _norm(addr)
        seeded.setdefault(a, {"is_dqa": False, "is_handler": False})["is_handler"] = True

    if not seeded:
        return 0
    with get_session() as db:
        for addr, flags in seeded.items():
            # 이름은 주소 앞부분에서 만든다(관리자가 화면에서 고친다).
            local = addr.split("@", 1)[0].replace(".", " ").title()
            db.add(Staff(name=local, email=addr, status=ACTIVE,
                         is_dqa=flags["is_dqa"], is_handler=flags["is_handler"],
                         team="DQA" if flags["is_dqa"] else "",
                         updated_by="(이관)"))
        db.commit()
    _invalidate()
    return len(seeded)
