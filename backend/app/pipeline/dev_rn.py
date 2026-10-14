"""개발 Release Note 수집 — 등록된 '개발 Release Note 파일 경로'에서 출시 버전을 훑어온다.

제품을 등록하면 이 수집이 돌아, 그 폴더에 이미 만들어져 있는 개발 RN 문서들에서
**버전을 뽑아 목록**으로 만들고 문서를 로컬에 내려둔다(docx 다운로드 · pdf 열람용).

파일서버는 **읽기만** 한다.

── 파일명에서 버전 뽑기 (파일서버 실측 1,880개 문서 기준) ───────────────────
두 가지 표기를 모두 받는다.
  형식 1  `PM95_Android15_Release_Note_95.52.pdf`        → 95.52
  형식 2  `TR54G15.03C1.pdf` (제품명+type+android.minor)  → TR54G15.03C1

suffix 는 버전 뒤에 **바로 붙은** 대문자+숫자만 본다. 그래서 문서 리비전·지역·날짜
표기는 suffix 로 오인하지 않는다.
  `95.52`              → suffix 없음
  `40.03C1`            → C1
  `84.04.B1`           → B1
  `MF52EG15.00_B14`    → B14
  `40.00_r2`           → 없음 (소문자 r2 = 문서 리비전)
  `40.05_10232019_R2`  → 없음 (버전 뒤가 숫자 → 날짜)
  `s_84.06C1_WWE_0226` → C1  (WWE·날짜는 무시)

── 목록에 남기는 버전 ────────────────────────────────────────────────────
  · suffix 가 없거나 **C 로 시작**하는 것만 (A/B/S/RC 등 비배포 빌드는 제외)
  · 제품에 등록한 **최초 OS 버전과 같은 major**, 그리고 **뒷자리(minor)가 그 이상**
    (다음 버전은 항상 뒷자리가 커진다 — 45.70 → 45.71 → 45.73 …)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_WORK = Path(__file__).resolve().parent.parent.parent / "work" / "dev_rn"

DOCX_EXT = (".doc", ".docx")
PDF_EXT = (".pdf",)

# major = '95' | '2x' | 'TR54G15'(형식 2)   ·   minor = 두 자리 이하 숫자
# suffix = 바로 뒤(구분자 없거나 . _ 공백 하나)에 붙은 **대문자**+숫자
_VER_RE = re.compile(
    r"(?P<major>\d{2,3}|\d[xX]|[A-Za-z][A-Za-z0-9]*\d{1,2})"
    r"\.(?P<minor>\d{1,2})"
    r"(?:[._ ]?(?P<suffix>[A-Z]{1,2}\d{0,2})(?![a-z]))?"
    r"(?![\d.])"
)


@dataclass
class Version:
    """파일명에서 뽑은 버전 하나."""

    text: str        # 표기 그대로 — '95.52' · '40.03C1' · 'TR54G15.03C1'
    major: str       # '95' · 'TR54G15'
    minor: int       # 52
    suffix: str      # '' · 'C1' · 'B14'

    @property
    def tracked(self) -> bool:
        """목록으로 관리하는 버전인가 — suffix 없음 또는 C 로 시작."""
        return not self.suffix or self.suffix.upper().startswith("C")

    @property
    def sort_key(self) -> tuple:
        return (self.minor, self.suffix)


def parse_version(name: str) -> Version | None:
    """파일명 또는 버전 문자열에서 버전을 뽑는다. 못 뽑으면 None.

    확장자를 떼고 먼저 본다. 다만 '95.51' 처럼 **버전만** 준 경우엔 stem 이 '95' 가
    되어(뒷자리를 확장자로 먹는다) 못 찾으므로, 그때는 원문 전체에서 다시 찾는다.
    """
    m = _VER_RE.search(Path(name or "").stem) or _VER_RE.search(name or "")
    if not m:
        return None
    major = m.group("major")
    minor = m.group("minor")
    suffix = (m.group("suffix") or "").strip()
    return Version(
        text=f"{major}.{minor}{suffix}", major=major, minor=int(minor), suffix=suffix
    )


def canonical_version(version: str, floor_raw: str) -> str:
    """제품 관리에 등록한 '최초 OS 버전'의 표기를 표준으로 삼아 버전을 맞춘다.

    같은 회차가 자료마다 다르게 적힌다 — 출시 이력(Confluence·SP)은 숫자 꼬리만 주고
    (`15.01`), 개발 RN 파일명은 제품 접두어까지 붙인다(`PM84PG15.01`). 그대로 두면
    한 회차가 두 줄이 된다(실측 2026-08-05: TR54 4회차가 7줄).

    **제품 접두어가 붙은 표기가 있으면 그게 표준이다**(사용자 확정 2026-08-05).
    무엇이 표준인지는 등록된 최초 OS 버전으로 판단한다 — `PM84PG15.00` 이면 접두어를
    붙이고, `95.00` 같은 레거시면 그대로 둔다.

    맞출 수 없으면(계열이 다르거나 파싱 실패) 원문을 그대로 돌려준다.
    """
    v = parse_version(version or "")
    floor = parse_version(str(floor_raw or "").strip())
    if v is None or floor is None:
        return version
    std, mine = floor.major, v.major
    if std == mine or std.isdigit():          # 이미 표준이거나, 레거시 제품
        return version
    # 숫자 꼬리만 온 값에만 접두어를 붙인다('15' → 'PM84PG15').
    if not (mine.isdigit() and std.upper().endswith(mine)):
        return version
    return f"{std}.{v.minor:02d}{v.suffix}"


def _same_series(v: Version, floor: Version | None) -> bool:
    """최초 OS 버전과 같은 계열(major 일치)이고 뒷자리가 그 이상인가.

    최초 OS 버전이 등록돼 있지 않으면(floor=None) 계열을 따지지 않는다.

    **형식 1과 형식 2가 같은 계열을 가리킬 수 있다.** 출시 이력(Confluence·SP)은 숫자
    꼬리만 주고(`15.00`), 등록값·파일명은 제품명까지 붙인 표기를 쓴다(`PM84PG15.00`).
    major 를 글자 그대로 비교하면 그 제품의 회차가 통째로 사라진다(실측 2026-08-04:
    PM84P A15 는 출시 이력 2건이 있는데 버전 목록이 0줄이었다).
    한쪽이 **순수 숫자**이고 다른 쪽이 그 숫자로 끝날 때만 같은 계열로 본다 —
    'PM84PG15' vs '15' 은 통과, '95' vs '5' 같은 우연한 꼬리 일치는 막는다.
    """
    if floor is None:
        return True
    a, b = v.major.upper(), floor.major.upper()
    if a != b:
        num, txt = (a, b) if a.isdigit() else (b, a)
        if not (num.isdigit() and not txt.isdigit() and txt.endswith(num)):
            return False
    return v.minor >= floor.minor


# ── 수집 ───────────────────────────────────────────────────────────────────
def local_dir(product_id: int, version: str = "") -> Path:
    """수집한 개발 RN 을 두는 곳. 버전을 주면 **버전별 하위 폴더**를 준다.

    한 폴더에 모든 버전을 쏟아 두면 파일명 규칙이 제품마다 달라 뒤섞인다.
    버전별로 나눠 두면 그 버전의 산출물이 무엇인지 폴더만 봐도 안다.
    """
    base = _WORK / str(product_id)
    return base / _safe(version) if version else base


def _safe(name: str) -> str:
    """버전 표기를 폴더 이름으로. 경로 구분자·금지문자를 막는다."""
    return re.sub(r'[\\/:*?"<>|]', "_", (name or "").strip()) or "_"


def collect(entries, floor: Version | None) -> tuple[dict, list[str]]:
    """파일 목록 → {버전표기: {"version": Version, "docx": 이름, "pdf": 이름}}.

    반환 (수집분, 제외 사유 로그). entries 는 FileEntry 목록(파일서버) 또는 이름 목록.
    """
    picked: dict[str, dict] = {}
    skipped: list[str] = []
    for e in entries:
        name = getattr(e, "name", e)
        if getattr(e, "is_dir", False):
            continue
        ext = Path(name).suffix.lower()
        if ext not in DOCX_EXT + PDF_EXT:
            continue
        v = parse_version(name)
        if v is None:
            skipped.append(f"{name} — 버전을 못 뽑음")
            continue
        if not v.tracked:
            skipped.append(f"{name} — 비배포 빌드(suffix {v.suffix})")
            continue
        if not _same_series(v, floor):
            skipped.append(f"{name} — 최초 OS 버전 계열/범위 밖({v.text})")
            continue
        slot = picked.setdefault(v.text, {"version": v, "docx": "", "pdf": ""})
        key = "pdf" if ext in PDF_EXT else "docx"
        # 같은 버전에 .doc 과 .docx 가 다 있으면 .docx 를 쓴다.
        if not slot[key] or (key == "docx" and ext == ".docx"):
            slot[key] = name
    return picked, skipped


def scan_product(product_id: int) -> dict:
    """제품 하나의 개발 RN 을 수집해 DB 에 반영한다.

    반환 {ok, error, total, downloaded, skipped(list), dir}
    """
    from app.database import get_session
    from app.integrations.fileserver import get_backend
    from app.models import OsProduct, OsProductRelease

    with get_session() as db:
        p = db.query(OsProduct).filter_by(id=product_id).first()
        if p is None:
            return {"ok": False, "error": "제품을 찾을 수 없습니다."}
        src = (p.dev_rn_path or "").strip()
        initial = (p.initial_os_version or "").strip()
    if not src:
        return {"ok": False, "error": "'개발 Release Note 파일 경로' 가 등록되지 않았습니다."}

    # 등록 경로의 **호스트에 맞는** 백엔드를 쓴다(제품마다 서버가 다를 수 있다).
    from app.pipeline.rn_lookup import backend_for_path

    backend = backend_for_path(0, src) or get_backend(0)
    if backend is None:
        return {"ok": False, "error": "파일 서버 자격증명이 없습니다. [설정 → 파일 서버]에서 등록하세요."}

    try:
        entries = backend.list_dir(src)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"경로를 열지 못했습니다({src}): {e}"}

    floor = parse_version(initial) if initial else None
    picked, skipped = collect(entries, floor)

    flat = local_dir(product_id)                 # 예전 저장 위치(버전 구분 없이 한 폴더)
    flat.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    now = datetime.now(timezone.utc)

    with get_session() as db:
        existing = {
            r.version: r
            for r in db.query(OsProductRelease).filter_by(product_id=product_id).all()
        }
        for text, slot in picked.items():
            v: Version = slot["version"]
            row = existing.get(text)
            if row is None:
                row = OsProductRelease(product_id=product_id, version=text)
                db.add(row)
            row.major, row.minor, row.suffix = v.major, v.minor, v.suffix
            row.src_dir = src
            row.fetched_at = now
            dest = local_dir(product_id, text)         # 버전별 폴더
            dest.mkdir(parents=True, exist_ok=True)
            for key, col_name, col_path in (
                ("docx", "docx_name", "docx_path"),
                ("pdf", "pdf_name", "pdf_path"),
            ):
                name = slot[key]
                if not name:
                    setattr(row, col_name, "")
                    setattr(row, col_path, "")
                    continue
                target = dest / name
                if not target.exists():
                    # 예전에 한 폴더에 받아 둔 게 있으면 다시 받지 않고 옮긴다.
                    old = flat / name
                    if old.exists() and old.is_file():
                        try:
                            old.replace(target)
                        except OSError:
                            pass
                if not target.exists():
                    try:
                        backend.download(f"{src}\\{name}", str(dest))
                        downloaded += 1
                    except Exception:  # noqa: BLE001
                        skipped.append(f"{name} — 내려받기 실패")
                        setattr(row, col_name, "")
                        setattr(row, col_path, "")
                        continue
                setattr(row, col_name, name)
                setattr(row, col_path, str(target))
        # 폴더에서 사라진 버전은 목록에서도 지운다(목록 = 폴더의 현재 상태).
        for text, row in existing.items():
            if text not in picked:
                db.delete(row)
        db.commit()

    return {
        "ok": True, "error": "", "total": len(picked), "downloaded": downloaded,
        "skipped": skipped, "dir": str(flat),
    }


def list_releases(product_id: int) -> list[dict]:
    """수집된 버전 목록 — 최신(뒷자리 큰 것) 먼저."""
    from app.database import get_session
    from app.models import OsProductRelease

    with get_session() as db:
        rows = db.query(OsProductRelease).filter_by(product_id=product_id).all()
        out = [{
            "version": r.version, "major": r.major, "minor": r.minor, "suffix": r.suffix,
            "docx_name": r.docx_name or "", "docx_path": r.docx_path or "",
            "pdf_name": r.pdf_name or "", "pdf_path": r.pdf_path or "",
            "fetched_at": r.fetched_at,
        } for r in rows]
    out.sort(key=lambda d: (d["minor"], d["suffix"]), reverse=True)
    return out


def exists(path: str) -> bool:
    return bool(path) and os.path.exists(path)
