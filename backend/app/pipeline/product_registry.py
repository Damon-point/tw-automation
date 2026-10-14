"""제품 레지스트리 — (제품군 + Android 버전) → 편집 대상 문서를 결정적으로 묶는 기준표.

동명 제품(예: PM95 A13 vs A15) 혼동을 막기 위한 **학습되는 기준 데이터**.
빈 상태로 시작하고, 사람이 작업지시서를 승인할 때마다 한 행씩 채워진다(upsert).
이후 같은 제품+Android 조합이 오면 편집 대상 문서가 결정적으로 확정된다.

로컬 전용(JSON). 어떤 프로덕션도 건드리지 않는다.

행(row) 구조:
  product_family      : "PM95"
  android_version     : "13"                (문자열; 없으면 "")
  os_version_series   : "95"                (선택; 버전대 접두 힌트)
  service_portal_docs : [{"id":"51","title":"PM95 A13 Resources..."}]
  release_notes_page  : {"id":"139100390","title":"PM95 (Android 13) Release Notes"} | None
  last_os_version     : "95.06"             (마지막으로 승인된 새 버전)
  examples            : ["<메일 식별/제목>"] (학습 근거 누적)
  updated_at
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.atomic_io import atomic_write_text, file_lock

_FILE = Path(__file__).resolve().parent.parent.parent / "work" / "product_registry.json"


def _norm(product_family: str, android_version: str | None) -> tuple[str, str]:
    return (product_family or "").strip().upper(), str(android_version or "").strip()


def _load() -> dict:
    if _FILE.exists():
        try:
            return json.loads(_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {"rows": []}
    return {"rows": []}


def _save(data: dict) -> None:
    atomic_write_text(_FILE, json.dumps(data, ensure_ascii=False, indent=2))


# 제품별 **기본 Android 버전** — 이 버전의 문서/폴더는 이름에 Android 표기가 없다.
#   Service Portal: 'PM67 Resources ...'(기본 A11) vs 'PM67 A13 Resources ...'
#   파일서버      : 'Software/Android' (기본)      vs 'Software/Android13'
# 사람이 확인해 준 값만 넣는다(추측 금지). 모르는 제품은 "" → 폴백 시 사람 확인 표시.
_DEFAULT_ANDROID_FILE = Path(__file__).resolve().parent.parent.parent / "work" / "default_android.json"

def _load_defaults() -> dict:
    if _DEFAULT_ANDROID_FILE.exists():
        try:
            return json.loads(_DEFAULT_ANDROID_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def get_default_android(product_family: str) -> str:
    """제품의 기본 Android 버전. 모르면 "" (= 폴백해도 되는지 사람이 확인해야 함)."""
    return str(_load_defaults().get((product_family or "").strip().upper(), "") or "")


def find(product_family: str, android_version: str | None) -> dict | None:
    """(제품군, Android) 로 행을 찾는다. 없으면 None."""
    pf, av = _norm(product_family, android_version)
    for r in _load().get("rows", []):
        if _norm(r.get("product_family"), r.get("android_version")) == (pf, av):
            return r
    return None


def upsert(
    product_family: str,
    android_version: str | None,
    *,
    service_portal_docs: list[dict] | None = None,
    release_notes_page: dict | None = None,
    os_version_series: str | None = None,
    last_os_version: str | None = None,
    example: str | None = None,
) -> dict:
    """행을 만들거나 갱신한다(사람 승인 시 호출 = 학습). 갱신된 행을 반환."""
    pf, av = _norm(product_family, android_version)
    with file_lock(_FILE):
        return _upsert_locked(pf, av, service_portal_docs, release_notes_page,
                              os_version_series, last_os_version, example)


def _upsert_locked(pf, av, service_portal_docs, release_notes_page,
                   os_version_series, last_os_version, example) -> dict:
    data = _load()
    row = None
    for r in data.get("rows", []):
        if _norm(r.get("product_family"), r.get("android_version")) == (pf, av):
            row = r
            break
    if row is None:
        row = {
            "product_family": pf,
            "android_version": av,
            "service_portal_docs": [],
            "release_notes_page": None,
            "examples": [],
        }
        data.setdefault("rows", []).append(row)

    if service_portal_docs is not None:
        row["service_portal_docs"] = service_portal_docs
    if release_notes_page is not None:
        row["release_notes_page"] = release_notes_page
    if os_version_series:
        row["os_version_series"] = os_version_series
    if last_os_version:
        row["last_os_version"] = last_os_version
    if example and example not in row.get("examples", []):
        row.setdefault("examples", []).append(example)
    row["updated_at"] = datetime.now().isoformat(timespec="seconds")

    _save(data)
    return row
