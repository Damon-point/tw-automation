# -*- coding: utf-8 -*-
"""C 리비전(95.07C1 · MF52EG15.00C1) 전용 — 기준 판 이력 + 그 뒤 수정 사항.

C 판은 **기준 판을 대체한다**. 그래서 문서의 그 한 행은 두 가지를 함께 담아야 한다
(사용자 확정 2026-09-07).

  ① 기준 판(95.07 · MF52EG15.00) RN 의 Revision History **전부**
  ② 기준 판 이후 바뀐 내용 — 배포 공지 본문의 '[95.07 이후 수정 사항]' 목록

둘 다 **공개 선별표의 행**으로 올린다. 문서는 표에서만 나오므로, 사람이 빼거나 문구를
고칠 수 있어야 한다(2026-09-07 '뺄 수가 없다' 지적의 연장).

**왜 기준 판을 따로 찾나.** 기존 경로는 '직전 버전'을 찾는데, 그 값은 출시 이력에서
오므로 C 판이 나온 시점에 기준 판이 아직 이력에 없으면 **한 판 더 과거**를 집는다
(실측 job-21: 95.07C1 인데 95.04 를 참고본으로 잡아, 95.07 의 이력이 문서에서 통째로
빠졌다). C 리비전에서는 기준 판이 곧 직전 판이라 표기에서 바로 얻을 수 있다.

**수정 사항은 Claude 로 읽는다**(사용자 확정 2026-09-07). 형식이 제각각이라
(`[95.07 이후 수정 사항]` · `95.07 대비 변경사항` · 목록 기호도 `*`·`n`·`-`) 규칙으로
잡으면 곧 깨진다. 실측으로도 이 형식의 메일이 3건뿐이라 규칙을 세울 근거가 얇다.
"""
from __future__ import annotations

import re

#: C 리비전 표기 — 95.07C1 · MF52EG15.00C1 · TR54G15.03C2
_C_RE = re.compile(r"^(?P<base>.+?)\s*(?P<rev>C\d+)\s*$", re.I)


def split_c(version: str) -> tuple[str, str]:
    """('기준 판', 'C1') 로 가른다. C 리비전이 아니면 (원본, '')."""
    s = re.sub(r"\s+", "", str(version or "")).split("(")[0]
    m = _C_RE.match(s)
    return (m.group("base"), m.group("rev").upper()) if m else (s, "")


def is_c_revision(version: str) -> bool:
    return bool(split_c(version)[1])


_SYS = (
    "당신은 OS 배포 공지 메일에서 **기준 판 이후 바뀐 내용**만 뽑는 도구입니다.\n"
    "- 메일에는 '[95.07 이후 수정 사항]' 처럼 기준 판 뒤의 변경을 적은 목록이 있습니다.\n"
    "- 그 목록의 항목만 뽑으세요. 파일 경로·인사말·서명·시험 일정은 제외합니다.\n"
    "- 원문 표기를 유지하되 목록 기호(*, -, n)와 앞뒤 공백은 떼세요.\n"
    "- 그런 목록이 없으면 빈 배열을 돌려주세요. **지어내지 마세요.**\n"
    '반드시 JSON 으로만: {"lines": ["...", "..."]}'
)


def changes_since_base(client, model: str, product: str, base_version: str,
                       version: str, mails: list[dict]) -> tuple[list[str], str]:
    """배포 공지에서 '기준 판 이후 수정 사항'을 뽑는다. (줄 목록, 사유).

    mails: [{mail_id, subject, body}] — 이 회차의 배포 공지 후보(본문 포함).
    못 뽑아도 예외를 올리지 않는다 — 없으면 기준 판 이력만으로 진행한다.
    """
    cand = [m for m in mails if str(m.get("body") or "").strip()]
    if not cand:
        return [], "배포 공지 본문이 없습니다."
    from app.pipeline.ai_edit import _extract_json

    body = "\n\n".join(
        f"[메일 #{m.get('mail_id')}] {m.get('subject') or ''}\n{str(m.get('body'))[:4000]}"
        for m in cand[:3])
    prompt = (f"제품 {product} · 기준 판 {base_version} · 이번 판 {version}\n\n"
              f"[배포 공지]\n{body}\n\n"
              f"{base_version} 이후 바뀐 내용의 목록만 JSON 으로 뽑으세요.")
    try:
        resp = client.messages.create(
            model=model, max_tokens=2000, system=_SYS,
            messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in resp.content
                       if getattr(b, "type", None) == "text")
        out = (_extract_json(text) or {}).get("lines") or []
    except Exception as e:  # noqa: BLE001
        return [], f"수정 사항 추출 실패: {type(e).__name__}: {e}"
    lines = [re.sub(r"\s+", " ", str(x)).strip() for x in out if str(x).strip()]
    return lines, ("" if lines else "배포 공지에 기준 판 이후 수정 사항이 없습니다.")


def rows_from(base_lines: list[str], change_lines: list[str],
              base_version: str, change_src: str = "배포 공지") -> list[dict]:
    """기준 판 이력 + 이후 수정 사항 → 선별표 행.

    둘 다 **반영(Y)** 으로 깔아 둔다 — 선별표의 불변식(기본 반영, 사람이 뺀다)과 같다.
    비고에 출처를 남겨 어느 쪽에서 온 줄인지 화면에서 구분되게 한다. change_src 는 그 출처 이름이다.
    """
    rows: list[dict] = []
    seen: set[str] = set()

    def _add(line: str, reason: str) -> None:
        s = re.sub(r"\s+", " ", str(line or "")).strip()
        key = s.lower().rstrip(".")
        if not s or key in seen:
            return
        seen.add(key)
        rows.append({
            "no": len(rows) + 1, "issue": s, "team": "", "customer": "", "gubun": "",
            "include": "Y", "reason": reason, "public": s, "redmine": "",
            "sensitive": False, "apply_note": s,
            # Redmine 이슈에서 복원되지 않는 행이라는 표시(최초 출시 행과 같다).
            "from_mail": True,
        })

    for ln in base_lines:
        _add(ln, f"{base_version} RN 이력에서 가져옴 — C 판이 그 판을 대체한다")
    for ln in change_lines:
        _add(ln, f"{base_version} 이후 수정 사항({change_src})")
    return rows


def base_rn_revision_lines(entries: dict, base_version: str) -> list[str]:
    """기준 판 RN 의 Revision History 에서 **그 판의 줄들**을 꺼낸다.

    entries: rn_lookup.extract_revision_entries_full 결과 {버전: {lines, ...}}.
    표기가 흔들리므로(95.07 · 95.07 (Jul 10 2026)) 버전 앞부분으로 맞춘다.
    기준 판을 못 찾으면 **문서의 마지막 판**을 쓴다 — 그 문서가 곧 기준 판의 RN 이다.
    """
    from app.pipeline.rn_research import _norm_ver

    want = _norm_ver(base_version)
    for ver, d in entries.items():
        if _norm_ver(str(ver).split(" (")[0]) == want:
            return [str(x) for x in (d.get("lines") or []) if str(x).strip()]
    if entries:
        last = list(entries.values())[-1]
        return [str(x) for x in (last.get("lines") or []) if str(x).strip()]
    return []
