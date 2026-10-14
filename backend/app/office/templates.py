"""RN 제작용 템플릿(공통 1벌) 저장소 — 프로그램 내부 관리.

고정 경로 `data/templates/` 에 활성 템플릿을 둔다:
  - rn_docx  : Release Note 브랜드 Word 템플릿(rn_template.docx) — docxtpl(Jinja2) 태그로 채움
  - rn_excel : RN 작성용 Excel 템플릿(rn_issues_template.xlsx) — 이슈 요약 채움

관리 페이지(/templates)에서 업로드·교체·다운로드한다. 로컬 전용.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.atomic_io import atomic_write_bytes

_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "templates"

# key -> (파일명, 라벨, 허용 확장자)
# 참고: RN 은 종류가 여러 개(전체 이슈 RN=Dropbox용 / 2~3개 요약 RN 등)이고 템플릿 구조가 다르다.
# 지금은 'rn_docx' = 전체 이슈 RN(Dropbox 업로드용). 요약 RN 템플릿은 추후 별도 키로 추가.
TEMPLATES: dict[str, tuple[str, str, str]] = {
    "rn_docx": ("rn_template.docx", "Release Note (RN for Sales) · Word 템플릿", ".docx"),
    # 전체 이력본 — Revision History 표에 {%tr for r in revisions %} 행 반복이 있는 판.
    # `python -m scripts.build_rn_full_template` 로 위 템플릿에서 만들어 낸다.
    "rn_docx_full": ("rn_full_template.docx",
                     "Release Note — 전체 버전 이력 · Word 템플릿", ".docx"),
    "rn_excel": ("rn_issues_template.xlsx", "RN 작성용 Excel 템플릿 (xlsx)", ".xlsx"),
    # Android 세대가 바뀐 출시 전용 뉴스레터(개선사항 카드 없이 업그레이드 안내가 본문).
    "newsletter_newos_kr": ("newsletter_newos_kr.html",
                            "Newsletter — Android 세대 변경 (KR)", ".html"),
    "newsletter_newos_en": ("newsletter_newos_en.html",
                            "Newsletter — Android 세대 변경 (EN)", ".html"),
}


def path_for(key: str) -> Path | None:
    meta = TEMPLATES.get(key)
    return (_DIR / meta[0]) if meta else None


def exists(key: str) -> bool:
    p = path_for(key)
    return bool(p and p.exists())


def save(key: str, data: bytes) -> tuple[bool, str]:
    """업로드된 템플릿 바이트를 저장(교체)."""
    p = path_for(key)
    if p is None:
        return False, f"알 수 없는 템플릿 키: {key}"
    atomic_write_bytes(p, data)
    return True, f"'{TEMPLATES[key][1]}' 저장 완료 ({len(data):,} bytes)."


def delete(key: str) -> tuple[bool, str]:
    """등록된 템플릿을 삭제한다."""
    p = path_for(key)
    if p is None:
        return False, f"알 수 없는 템플릿 키: {key}"
    if not p.exists():
        return False, "삭제할 템플릿이 없습니다."
    try:
        p.unlink()
        return True, f"'{TEMPLATES[key][1]}' 삭제했습니다."
    except OSError as e:
        return False, f"삭제 실패: {e}"


def status() -> list[dict]:
    """각 템플릿의 상태(존재/수정시각/크기/파일명)."""
    out: list[dict] = []
    for key, (name, label, ext) in TEMPLATES.items():
        p = _DIR / name
        if p.exists():
            st = p.stat()
            out.append({
                "key": key, "name": name, "label": label, "ext": ext, "exists": True,
                "path": str(p), "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
        else:
            out.append({"key": key, "name": name, "label": label, "ext": ext,
                        "exists": False, "path": str(p), "size": 0, "mtime": ""})
    return out
