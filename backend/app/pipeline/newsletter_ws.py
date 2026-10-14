"""⑦ Newsletter 편집·발송 작업공간 — 로컬 전용.

run_newsletter 가 생성한 KR 초안(카드 + 메타)을 mail_id 키로 담아, /newsletter 페이지에서
카드 제목·본문을 수정하고 재렌더한 뒤 Mailchimp 로 발송한다.

draft(항목) 구조:
  mail_id     : 작업 메일 id (키)
  subject     : 발송 제목(사람이 수정 가능)
  product_disp / os_version_disp / sdk / rn_link : 렌더 메타
  cards       : [{topic, title_ko, body_ko, title_en, body_en}]  (편집 대상은 _ko)
  html_path   : 마지막 렌더된 KR HTML 파일 경로
  updated_at  : 갱신 시각
  sent        : 발송 완료 여부 / sent_at / campaign_id
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.atomic_io import atomic_write_text

_FILE = Path(__file__).resolve().parent.parent.parent / "work" / "newsletter_review.json"


def _load() -> dict:
    if _FILE.exists():
        try:
            return json.loads(_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {"drafts": {}}
    return {"drafts": {}}


def _save(data: dict) -> None:
    atomic_write_text(_FILE, json.dumps(data, ensure_ascii=False, indent=2))


def _key(mail_id) -> str:
    """Submission/Approval 짝은 하나의 작업 → 초안도 canonical(=Submission) id 로 공유한다.

    파이프라인 상태(state.py)와 동일한 규칙. 이렇게 해야 Submission 에서 만든 뉴스레터 초안을
    Approval 발송 단계에서도 같은 키로 찾는다(예전엔 raw mail_id 라 Approval 에서 '초안 없음'이 났다).
    """
    from app.pipeline.state import canonical_id

    return str(canonical_id(mail_id))


def put(draft: dict) -> None:
    """초안을 저장(작업 canonical id 기준 덮어쓰기)."""
    data = _load()
    draft = dict(draft)
    draft.setdefault("updated_at", datetime.now().isoformat(timespec="seconds"))
    data.setdefault("drafts", {})[_key(draft["mail_id"])] = draft
    _save(data)


def get(mail_id) -> dict | None:
    return _load().get("drafts", {}).get(_key(mail_id))


def update_fields(mail_id, *, subject=None, cards=None, html_path=None,
                  os_version_disp=None, rn_link=None) -> bool:
    data = _load()
    d = data.get("drafts", {}).get(_key(mail_id))
    if not d:
        return False
    if subject is not None:
        d["subject"] = subject
    if cards is not None:
        d["cards"] = cards
    if html_path is not None:
        d["html_path"] = html_path
    if os_version_disp is not None:
        d["os_version_disp"] = os_version_disp
    if rn_link is not None:
        # '더 많은 개선 사항 확인' 버튼의 주소({{RN_LINK}}). 초안을 만들 때 한 번 박히고
        # 끝이라, 링크를 나중에 고쳐도 버튼은 옛 주소를 가리켰다(사용자 지적 2026-09-09).
        d["rn_link"] = rn_link
    d["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _save(data)
    return True


def mark_sent(mail_id, campaign_id: str) -> None:
    data = _load()
    d = data.get("drafts", {}).get(_key(mail_id))
    if d:
        d["sent"] = True
        d["campaign_id"] = campaign_id
        d["sent_at"] = datetime.now().isoformat(timespec="seconds")
        _save(data)
