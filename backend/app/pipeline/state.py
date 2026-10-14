"""작업(메일)별 파이프라인 공유 상태 — 단계 간 산출물 전달용.

각 단계가 만든 결과(OS 파일 경로, 버전, Dropbox 링크, 결재 상태 등)를 작업 키(mail_id 또는
'job-N')로 쌓아, 다음 단계(③ Excel, ④ Service Portal, ⑤ Release Note …)가 읽어 쓸 수 있게 한다.

저장소
  · PostgreSQL(3080) — public.pipeline_state(JSONB) · public.pipeline_state_alias
  · 그 밖(SQLite) — 예전처럼 work/pipeline_state/<키>.json · _alias.json
어느 쪽이든 부르는 쪽은 get_state / update_state / set_alias / canonical_id 만 쓴다.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from app.atomic_io import atomic_write_text, file_lock
from app.dbconf import is_pg

_DIR = Path(__file__).resolve().parent.parent.parent / "work" / "pipeline_state"
# Submission/Approval 짝은 하나의 작업이라 상태를 공유한다.
# 별칭: {Approval mail_id: Submission mail_id}. 짝이 지어질 때 app.pairing 이 등록한다.
_ALIAS = _DIR / "_alias.json"

_alias_cache: dict | None = None        # PostgreSQL 일 때 — 이 프로세스만 쓰므로 고칠 때 같이 고친다
_alias_lock = threading.Lock()


# ── 짝(별칭) ──────────────────────────────────────────────────────────────────

def _alias_map() -> dict:
    global _alias_cache
    if is_pg():
        with _alias_lock:
            if _alias_cache is None:
                from app.database import get_session
                from app.models import PipelineStateAlias

                with get_session() as db:
                    _alias_cache = {a.key: _as_id(a.canonical) for a in db.query(PipelineStateAlias).all()}
            return _alias_cache
    if _ALIAS.exists():
        try:
            return json.loads(_ALIAS.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _as_id(v):
    """'123' → 123, 'job-5' → 'job-5' — 예전 _alias.json 이 정수로 담던 것을 그대로 돌려준다."""
    s = str(v)
    return int(s) if s.isdigit() else s


def set_alias(mail_id: int, canonical_id: int) -> None:
    """mail_id 의 상태를 canonical_id 의 상태로 연결(짝 공유). 이미 같으면 아무 것도 안 함."""
    global _alias_cache
    if mail_id is None or canonical_id is None or str(mail_id) == str(canonical_id):
        return
    m = _alias_map()
    if str(m.get(str(mail_id))) == str(canonical_id):
        return
    if is_pg():
        from app.database import get_session
        from app.models import PipelineStateAlias

        with get_session() as db:
            row = db.get(PipelineStateAlias, str(mail_id))
            if row:
                row.canonical = str(canonical_id)
            else:
                db.add(PipelineStateAlias(key=str(mail_id), canonical=str(canonical_id)))
            db.commit()
        with _alias_lock:
            _alias_cache = None
        return
    m[str(mail_id)] = int(canonical_id)
    _DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_text(_ALIAS, json.dumps(m, ensure_ascii=False, indent=2))


def canonical_id(mail_id: int):
    """짝이 있으면 작업 기준 id(Submission), 없으면 그대로."""
    if mail_id is None:
        return mail_id
    return _alias_map().get(str(mail_id), mail_id)


# ── 상태 ─────────────────────────────────────────────────────────────────────

def _path(mail_id: int) -> Path:
    return _DIR / f"{canonical_id(mail_id)}.json"


def get_state(mail_id: int) -> dict:
    if is_pg():
        from app.database import get_session
        from app.models import PipelineState

        with get_session() as db:
            row = db.get(PipelineState, str(canonical_id(mail_id)))
            return dict(row.data or {}) if row else {}
    p = _path(mail_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def get_states(mail_ids) -> dict:
    """여러 작업의 상태를 한 번에 — {원래 키: 상태}. 목록 화면용(작업마다 따로 읽지 않게)."""
    ids = list(mail_ids)
    if not is_pg():
        return {m: get_state(m) for m in ids}
    from app.database import get_session
    from app.models import PipelineState

    canon = {m: str(canonical_id(m)) for m in ids}
    with get_session() as db:
        rows = db.query(PipelineState).filter(PipelineState.key.in_(set(canon.values()))).all() if canon else []
        got = {r.key: dict(r.data or {}) for r in rows}
    return {m: dict(got.get(k, {})) for m, k in canon.items()}


def update_state(mail_id: int, **fields) -> dict:
    """상태를 부분 갱신한다.

    두 사람이 같은 작업의 다른 단계를 동시에 돌리면 '읽고 → 고치고 → 통째로 쓰기' 가 겹쳐
    앞사람의 결과가 조용히 사라진다. PostgreSQL 은 행 잠금(SELECT … FOR UPDATE), 파일은 파일 락으로 막는다.
    """
    # 저장 전에 JSON 으로 한 번 돌려 본다 — 예전 파일 저장과 같은 값만 받는다(튜플은 리스트로)
    fields = json.loads(json.dumps(fields, ensure_ascii=False))
    if is_pg():
        from sqlalchemy.dialects.postgresql import insert

        from app.database import get_session
        from app.models import PipelineState

        key = str(canonical_id(mail_id))
        with get_session() as db:
            db.execute(insert(PipelineState).values(key=key, data={}, updated_at=datetime.now())
                       .on_conflict_do_nothing(index_elements=["key"]))
            row = db.query(PipelineState).filter_by(key=key).with_for_update().one()
            st = dict(row.data or {})
            st.update(fields)
            row.data = st
            row.updated_at = datetime.now()
            db.commit()
        return st
    _DIR.mkdir(parents=True, exist_ok=True)
    p = _path(mail_id)
    with file_lock(p):
        st = get_state(mail_id)
        st.update(fields)
        atomic_write_text(p, json.dumps(st, ensure_ascii=False, indent=2))
    return st


def import_from_files(overwrite: bool = False) -> dict:
    """work/pipeline_state 의 JSON 들을 PostgreSQL 로 옮긴다(파일은 그대로 둔다). 몇 번을 돌려도 같다.

    overwrite=False 면 이미 DB 에 있는 키는 건드리지 않는다.
    """
    global _alias_cache
    if not is_pg():
        raise RuntimeError("PostgreSQL 이 아닙니다")
    from app.database import get_session
    from app.models import PipelineState, PipelineStateAlias

    out = {"states": 0, "skipped": 0, "aliases": 0, "bad": []}
    with get_session() as db:
        if _ALIAS.exists():
            for k, v in json.loads(_ALIAS.read_text(encoding="utf-8")).items():
                if db.get(PipelineStateAlias, str(k)) is None:
                    db.add(PipelineStateAlias(key=str(k), canonical=str(v)))
                    out["aliases"] += 1
        for f in sorted(_DIR.glob("*.json")):
            if f.name == _ALIAS.name:
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                out["bad"].append(f"{f.name}: {e}")
                continue
            row = db.get(PipelineState, f.stem)
            if row is not None and not overwrite:
                out["skipped"] += 1
                continue
            if row is None:
                db.add(PipelineState(key=f.stem, data=data, updated_at=datetime.fromtimestamp(f.stat().st_mtime)))
            else:
                row.data = data
            out["states"] += 1
        db.commit()
    with _alias_lock:
        _alias_cache = None
    return out
