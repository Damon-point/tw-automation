"""OS Update 건별 문서 편집 이력(History) — append-only.

Approval 단계에서 발행하는 제작/수정 문서(포털=Service Portal+KB, RN=② Release Note,
Newsletter=⑦)의 **initial → 최종** 변경을 시각·작성자·이벤트·요약·내용 스냅샷으로 쌓는다.
최종 승인 반려가 반복되면 재편집분이 계속 append 되어 이력이 늘어난다.

파이프라인 상태(state.py)는 최신값만 덮어써 이력이 남지 않으므로 별도 저장한다.
키는 작업 기준 id(canonical_id) — Submission/Approval 짝은 한 작업으로 묶인다.

저장: work/pipeline_history/{canonical_id}.json
  { "portal": [entry...], "rn": [entry...], "newsletter": [entry...] }
entry = {ts, actor, event, summary, text, doc_key, title, meta}
  · text : 그 시점 내용의 사람이 읽을 수 있는 표현(줄 단위 diff 용)
  · doc_key/title : 한 종류 안에서 여러 문서를 구분(포털의 SP 여러 개, KB 등)
"""
from __future__ import annotations

import difflib
import json
from datetime import datetime
from pathlib import Path

from app.atomic_io import atomic_write_text, file_lock
from app.pipeline.state import canonical_id

_DIR = Path(__file__).resolve().parent.parent.parent / "work" / "pipeline_history"

# 문서 종류(탭) — 순서 = 화면 탭 순서. SP·KB 는 각각 따로 관리한다.
DOCS = ("sp", "kb", "rn", "newsletter")


def _path(mail_id) -> Path:
    return _DIR / f"{canonical_id(mail_id)}.json"


def _load(mail_id) -> dict:
    p = _path(mail_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def record(mail_id, doc: str, event: str, *, actor: str = "",
           summary: str = "", text: str = "", doc_key: str = "",
           title: str = "", meta: dict | None = None) -> None:
    """이력 1건을 append 한다. 예외를 던지지 않는다(기록 실패가 본 작업을 막지 않게).

    doc: 'portal' | 'rn' | 'newsletter'
    같은 문서(doc_key)의 직전 내용과 text 가 완전히 같으면 중복이라 기록하지 않는다
    (같은 저장을 두 번 눌러도 이력이 부풀지 않게). event 만 다르면(예: 편집→승인) 남긴다.
    """
    try:
        if doc not in DOCS:
            return
        _DIR.mkdir(parents=True, exist_ok=True)
        p = _path(mail_id)
        with file_lock(p):
            data = _load(mail_id)
            lst = data.setdefault(doc, [])
            # 같은 문서의 직전 엔트리와 event·text 가 모두 같으면 스킵(무의미한 중복 방지).
            for prev in reversed(lst):
                if prev.get("doc_key", "") == doc_key:
                    if prev.get("event") == event and prev.get("text", "") == text:
                        return
                    break
            lst.append({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "actor": actor or "",
                "event": event,
                "summary": summary or "",
                "text": text or "",
                "doc_key": doc_key or "",
                "title": title or "",
                "meta": meta or {},
            })
            atomic_write_text(p, json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:  # noqa: BLE001
        pass


def history(mail_id, doc: str) -> list[dict]:
    """해당 작업·문서종류의 이력(오래된→최신). 각 엔트리에 version(1부터) 부여."""
    lst = list(_load(mail_id).get(doc, []))
    for i, e in enumerate(lst, 1):
        e["version"] = i
    return lst


def counts(mail_id) -> dict:
    """탭 배지용 — 종류별 이력 건수."""
    d = _load(mail_id)
    return {k: len(d.get(k, [])) for k in DOCS}


def list_cases() -> list[dict]:
    """이력이 있는 OS Update 건 목록(최근 수정순).
    [{mail_id, product, version, counts}] — 화면 상단 선택기에 쓴다.
    """
    from app.pipeline.state import get_state

    if not _DIR.exists():
        return []
    out: list[dict] = []
    for p in sorted(_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            cid = int(p.stem)
        except ValueError:
            continue
        st = get_state(cid) or {}
        out.append({
            "mail_id": cid,
            "product": st.get("product", "") or "",
            "version": st.get("version_full") or st.get("os_version", "") or "",
            "counts": counts(cid),
        })
    return out


def diff_lines(old_text: str, new_text: str) -> list[tuple[str, str]]:
    """직전 버전 대비 줄 단위 diff. [(tag, line)] — tag ∈ {'same','add','del'}.

    화면에서 add=초록(+), del=빨강(−), same=회색으로 그린다.
    """
    old = (old_text or "").splitlines()
    new = (new_text or "").splitlines()
    sm = difflib.SequenceMatcher(a=old, b=new)
    out: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for ln in old[i1:i2]:
                out.append(("same", ln))
        elif tag == "delete":
            for ln in old[i1:i2]:
                out.append(("del", ln))
        elif tag == "insert":
            for ln in new[j1:j2]:
                out.append(("add", ln))
        elif tag == "replace":
            for ln in old[i1:i2]:
                out.append(("del", ln))
            for ln in new[j1:j2]:
                out.append(("add", ln))
    return out


# RN 입력 값(RN 승인자가 채우는 단말/빌드 확인 값) — mid_review.INPUT_FIELDS 와 같은 키.
# security_patch 는 상태 최상위(rn_components 밖)에 저장된다.
_RN_INPUT_FIELDS = [
    ("known_issues", "Known Issues"),
    ("gms_package", "GMS Package"),
    ("sdk", "SDK"),
    ("audio", "Audio parameter"),
    ("touch", "Touch panel"),
    ("wifi_driver", "Wi-Fi Driver"),
    ("wifi_firmware", "Wi-Fi Firmware"),
    ("wifi_config", "Wi-Fi Configuration"),
]


def text_from_rn(state) -> str:
    """RN 스냅샷 — 공개 선별 문구(Revision History) + 입력 값 전부를 diff 용 텍스트로.

    선별표의 포함(Y) 결과뿐 아니라 RN 승인자가 채우는 입력 값(GMS/SDK/Audio/Touch/
    Wi-Fi/보안패치 등)까지 담아, 어느 값이 언제 바뀌었는지 이력에 남긴다.
    """
    st = state or {}
    out = ["[공개 Revision History]"]
    out += [f"- {x}" for x in (st.get("rn_revision_lines") or []) if str(x).strip()]
    comps = st.get("rn_components") or {}
    out += ["", "[입력 값]"]
    for key, label in _RN_INPUT_FIELDS:
        val = str(comps.get(key, "") or "").strip()
        out.append(f"{label}: {val or '-'}")
    sp = str(st.get("security_patch", "") or "").strip()
    out.append(f"Android security patch level: {sp or '-'}")
    return "\n".join(out)


def text_from_kb_sections(secs) -> str:
    """KB 섹션 구조 → diff 용 텍스트(섹션·버킷별로 한 줄씩)."""
    out: list[str] = []
    for s in secs or []:
        out.append(f"## {(s.get('title') or 'Others').strip()}")
        for b, lab in (("new", "NEW"), ("improved", "IMPROVED"), ("fixed", "FIXED")):
            for it in s.get(b, []) or []:
                if str(it).strip():
                    out.append(f"[{lab}] {str(it).strip()}")
    return "\n".join(out)


def text_from_newsletter(draft) -> str:
    """Newsletter 초안(draft) → diff 용 텍스트(제목 + 카드별 KR/EN)."""
    d = draft or {}
    out = [f"제목: {d.get('subject', '')}", f"OS: {d.get('os_version_disp', '')}"]
    for i, c in enumerate(d.get("cards", []) or [], 1):
        out.append(f"[카드 {i}] ({c.get('topic', 'common')})")
        out.append(f"  KR 제목: {c.get('title_ko', '')}")
        out.append(f"  KR 본문: {c.get('body_ko', '')}")
        out.append(f"  EN 제목: {c.get('title_en', '')}")
        out.append(f"  EN 본문: {c.get('body_en', '')}")
    return "\n".join(out)


def text_from_html(html: str) -> str:
    """HTML → 보이는 텍스트만(포털 SP·Newsletter diff 용). 태그/과다 공백 제거."""
    import re

    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html or "")
    t = re.sub(r"(?is)<br\s*/?>", "\n", t)
    t = re.sub(r"(?is)</(p|div|li|tr|h[1-6])>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    lines = [ln.strip() for ln in t.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def diff_stat(old_text: str, new_text: str) -> tuple[int, int]:
    """(추가 줄 수, 삭제 줄 수) — 요약 배지용."""
    add = dele = 0
    for tag, _ in diff_lines(old_text, new_text):
        if tag == "add":
            add += 1
        elif tag == "del":
            dele += 1
    return add, dele
