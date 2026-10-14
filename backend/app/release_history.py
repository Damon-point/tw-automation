"""제품별 **출시 이력** 캐시 — Service Portal · Knowledge Base 를 긁은 결과를 보관한다.

이력 조회는 브라우저로 외부 사이트를 훑는 느린 작업이라 화면을 그릴 때마다 부를 수 없다.
그래서 사람이 [출시 이력 조회] 를 누를 때만 긁고, 결과를 파일로 남겨 그 뒤로는 그걸 쓴다.

여기 실린 버전은 **이미 고객에게 나간 것**이다. /osupdate 는 이걸 두 가지로 쓴다:
  · 버전 판별의 예비 근거 — 배포 공지 메일이 없을 때 pipeline.version_resolve 에 넘긴다.
  · 지난 회차 표시('출시됨') — 완료로 닫을지는 **사람이 정한다**(자동으로 닫지 않는다).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent / "work" / "release_history"


def _path(product_id: int) -> Path:
    return _DIR / f"{product_id}.json"


def get(product_id: int) -> dict:
    """캐시된 이력. 없으면 {}. {fetched_at, versions:[{version,date,src}], note}."""
    p = _path(product_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def refresh(product_id: int, user_id: int = 0) -> dict:
    """외부 사이트를 조회해 이력을 새로 받아 저장한다. {ok, error, versions, note}."""
    from app.database import get_session
    from app.models import OsProduct
    from app.pipeline.version_resolve import collect_history

    with get_session() as db:
        p = db.query(OsProduct).filter_by(id=product_id).first()
        if p is None:
            return {"ok": False, "error": "제품을 찾을 수 없습니다.", "versions": []}
        product, android = p.product, p.android

    try:
        hist = collect_history(user_id, product, android)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"조회 실패: {e}", "versions": []}

    data = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "versions": hist["versions"],
        "note": "; ".join(hist["notes"]),
    }
    if not hist["versions"]:
        # 못 찾았으면 빈 이력을 덮어쓰지 않는다 — 이전에 받아 둔 값이 더 쓸모 있다.
        return {"ok": False, "error": data["note"] or "출시 이력을 찾지 못했습니다.",
                "versions": []}
    _DIR.mkdir(parents=True, exist_ok=True)
    _path(product_id).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    return {"ok": True, "error": "", "versions": hist["versions"], "note": data["note"]}


def published(product_id: int) -> dict:
    """{버전: 출시일 문자열} — 캐시된 이력에서."""
    return {v["version"]: (v.get("date") or "")
            for v in (get(product_id).get("versions") or []) if v.get("version")}


def fetched_at(product_id: int) -> str:
    return get(product_id).get("fetched_at", "")
