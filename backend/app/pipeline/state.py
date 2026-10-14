"""작업(메일)별 파이프라인 공유 상태 — 단계 간 산출물 전달용.

각 단계가 만든 결과(OS 파일 경로, 버전, Dropbox 링크 등)를 mail_id 키로 로컬 JSON 에 쌓아,
다음 단계(③ Excel, ④ Service Portal, ⑤ Release Note)가 읽어 쓸 수 있게 한다. 로컬 전용.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.atomic_io import atomic_write_text, file_lock

_DIR = Path(__file__).resolve().parent.parent.parent / "work" / "pipeline_state"
# Submission/Approval 짝은 하나의 작업이라 상태를 공유한다.
# 별칭: {Approval mail_id: Submission mail_id}. 짝이 지어질 때 app.pairing 이 등록한다.
_ALIAS = _DIR / "_alias.json"


def _alias_map() -> dict:
    if _ALIAS.exists():
        try:
            return json.loads(_ALIAS.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def set_alias(mail_id: int, canonical_id: int) -> None:
    """mail_id 의 상태를 canonical_id 의 상태로 연결(짝 공유). 이미 같으면 아무 것도 안 함."""
    if mail_id is None or canonical_id is None or int(mail_id) == int(canonical_id):
        return
    m = _alias_map()
    if m.get(str(mail_id)) == int(canonical_id):
        return
    m[str(mail_id)] = int(canonical_id)
    _DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_text(_ALIAS, json.dumps(m, ensure_ascii=False, indent=2))


def canonical_id(mail_id: int):
    """짝이 있으면 작업 기준 id(Submission), 없으면 그대로."""
    if mail_id is None:
        return mail_id
    return _alias_map().get(str(mail_id), mail_id)


def _path(mail_id: int) -> Path:
    return _DIR / f"{canonical_id(mail_id)}.json"


def get_state(mail_id: int) -> dict:
    p = _path(mail_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def update_state(mail_id: int, **fields) -> dict:
    """상태를 부분 갱신한다.

    락으로 감싸는 이유: 두 사람이 같은 작업의 다른 단계를 동시에 돌리면
    '읽고 → 고치고 → 통째로 쓰기' 가 겹쳐 앞사람의 결과가 조용히 사라진다.
    """
    _DIR.mkdir(parents=True, exist_ok=True)
    p = _path(mail_id)
    with file_lock(p):
        st = get_state(mail_id)
        st.update(fields)
        atomic_write_text(p, json.dumps(st, ensure_ascii=False, indent=2))
    return st
