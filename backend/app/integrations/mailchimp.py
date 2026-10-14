"""Mailchimp Marketing API 클라이언트 — ⑦ Newsletter 발송.

캠페인 생성 → HTML 콘텐츠 설정 → (오디언스 실제) 발송.
**발송(send)은 되돌릴 수 없는 외부 동작**이라 allow_send=True 가 명시될 때만 실행한다.
그 외(ping·오디언스 조회·캠페인 생성·콘텐츠 설정)는 읽기/준비 단계로 안전하다.
인증: HTTP Basic (username 아무거나 + password=API 키).
"""
from __future__ import annotations
from app import shadow as _shadow

import re

import requests

from app.integrations.creds import MailchimpConfig

_TIMEOUT = 30


def _clean_from_name(name: str) -> str:
    """Mailchimp from_name(표시 이름) 정리 — '@','<','>' 등 비허용 문자가 있으면 안전한 기본값.

    이메일을 넣은 경우(예: pm_ta@pointmobile.com)는 표시 이름으로 부적합하므로 'Point Mobile' 로.
    (이메일은 reply_to 로 따로 들어간다.)
    """
    name = (name or "").strip()
    if not name:
        return "Point Mobile"
    if "@" in name or "<" in name or ">" in name:
        return "Point Mobile"
    cleaned = re.sub(r"[<>@]", "", name).strip()
    return cleaned or "Point Mobile"


def _base(cfg: MailchimpConfig) -> str:
    return f"https://{cfg.server_prefix}.api.mailchimp.com/3.0"


def _auth(cfg: MailchimpConfig):
    return ("pm-tw-app", cfg.api_key)


def _err(resp: requests.Response) -> str:
    try:
        j = resp.json()
        detail = j.get("detail") or j.get("title") or ""
        errs = j.get("errors") or []
        if errs:
            detail += " / " + "; ".join(f"{e.get('field')}: {e.get('message')}" for e in errs)
        return f"{resp.status_code} {detail}".strip()
    except Exception:  # noqa: BLE001
        return f"{resp.status_code} {resp.text[:200]}"


def ping(cfg: MailchimpConfig) -> tuple[bool, str]:
    """연결/키 확인. (성공, 메시지)."""
    if not cfg.server_prefix:
        return False, "서버 프리픽스(us21 등)를 확인하지 못했습니다. API 키 형식을 확인하세요."
    try:
        r = requests.get(f"{_base(cfg)}/ping", auth=_auth(cfg), timeout=_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return False, f"접속 오류: {e}"
    if r.status_code == 200:
        return True, "Mailchimp 연결 성공 ✓"
    return False, f"연결 실패: {_err(r)}"


def list_audiences(cfg: MailchimpConfig) -> tuple[list[dict], str]:
    """오디언스 목록 [{id, name, member_count}] + 오류메시지. (설정에서 list_id 고르기용)."""
    try:
        r = requests.get(
            f"{_base(cfg)}/lists", auth=_auth(cfg),
            params={"count": 100, "fields": "lists.id,lists.name,lists.stats.member_count"},
            timeout=_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001
        return [], f"접속 오류: {e}"
    if r.status_code != 200:
        return [], _err(r)
    out = []
    for l in r.json().get("lists", []):
        out.append({
            "id": l.get("id"), "name": l.get("name", ""),
            "member_count": (l.get("stats") or {}).get("member_count", 0),
        })
    return out, ""


def list_tags(cfg: MailchimpConfig, list_id: str = "") -> tuple[list[dict], str]:
    """오디언스의 태그(static 세그먼트) 목록 [{id, name, member_count}] + 오류메시지."""
    lid = (list_id or cfg.list_id or "").strip()
    if not lid:
        return [], "오디언스(List) ID 가 설정되지 않았습니다."
    try:
        r = requests.get(
            f"{_base(cfg)}/lists/{lid}/segments", auth=_auth(cfg),
            params={"type": "static", "count": 200,
                    "fields": "segments.id,segments.name,segments.member_count"},
            timeout=_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001
        return [], f"접속 오류: {e}"
    if r.status_code != 200:
        return [], _err(r)
    out = [
        {"id": s.get("id"), "name": s.get("name", ""), "member_count": s.get("member_count", 0)}
        for s in r.json().get("segments", [])
    ]
    return out, ""


def list_campaigns(cfg: MailchimpConfig, count: int = 50) -> tuple[list[dict], str]:
    """계정의 **아직 안 보낸** 캠페인 목록 + 오류메시지.

    반환 [{id, title, subject, list_id, list_name, created}] (최근 것부터).
    이미 보낸 캠페인은 다시 못 보내므로 status=save(초안) 만 준다.
    """
    try:
        r = requests.get(
            f"{_base(cfg)}/campaigns", auth=_auth(cfg),
            params={"count": count, "status": "save", "sort_field": "create_time",
                    "sort_dir": "DESC"},
            timeout=_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001
        return [], f"접속 오류: {e}"
    if r.status_code != 200:
        return [], _err(r)
    out = []
    for c in r.json().get("campaigns", []):
        rec = c.get("recipients") or {}
        st = c.get("settings") or {}
        out.append({
            "id": c.get("id", ""),
            "title": st.get("title", "") or st.get("subject_line", ""),
            "subject": st.get("subject_line", ""),
            "list_id": rec.get("list_id", ""),
            "list_name": rec.get("list_name", ""),
            "recipient_count": rec.get("recipient_count", 0),
            "created": (c.get("create_time") or "")[:16].replace("T", " "),
        })
    return out, ""


def get_campaign(cfg: MailchimpConfig, campaign_id: str) -> tuple[dict | None, str]:
    """캠페인 하나의 현재 상태 — 발송 대상·제목·상태 확인용."""
    if not campaign_id:
        return None, "캠페인 ID 가 없습니다."
    try:
        r = requests.get(f"{_base(cfg)}/campaigns/{campaign_id}",
                         auth=_auth(cfg), timeout=_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return None, f"접속 오류: {e}"
    if r.status_code != 200:
        return None, _err(r)
    c = r.json()
    rec = c.get("recipients") or {}
    st = c.get("settings") or {}
    return {
        "id": c.get("id", ""), "status": c.get("status", ""),
        "title": st.get("title", ""), "subject": st.get("subject_line", ""),
        "list_id": rec.get("list_id", ""), "list_name": rec.get("list_name", ""),
        "recipient_count": rec.get("recipient_count", 0),
    }, ""


def set_subject(cfg: MailchimpConfig, campaign_id: str, subject: str) -> tuple[bool, str]:
    """기존 캠페인의 제목만 바꾼다(발송 대상·설정은 그대로 둔다)."""
    if _shadow.blocked("mailchimp", "set_subject", campaign_id=campaign_id, subject=subject):
        return True, ""
    try:
        r = requests.patch(
            f"{_base(cfg)}/campaigns/{campaign_id}", auth=_auth(cfg),
            json={"settings": {"subject_line": subject, "title": subject}},
            timeout=_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001
        return False, f"접속 오류: {e}"
    return (True, "") if r.status_code == 200 else (False, _err(r))


def list_all_tags(cfg: MailchimpConfig) -> tuple[list[dict], str]:
    """계정의 **모든 오디언스**의 태그를 한 줄로 펴서 돌려준다.

    반환 [{list_id, list_name, id(=segment_id), name, member_count}] — 사람이 많은 순.
    발송 화면에서 '어느 그룹에 보낼지' 를 고르게 하려고 쓴다. 오디언스가 여럿이어도
    태그 이름만 보고 고를 수 있게 오디언스 이름을 함께 준다.
    """
    auds, err = list_audiences(cfg)
    if err:
        return [], err
    out: list[dict] = []
    for a in auds:
        tags, terr = list_tags(cfg, a["id"])
        if terr:
            continue
        for t in tags:
            out.append({
                "list_id": a["id"], "list_name": a.get("name", ""),
                "id": t.get("id"), "name": t.get("name", ""),
                "member_count": t.get("member_count", 0),
            })
    out.sort(key=lambda x: -int(x.get("member_count") or 0))
    return out, ""


def create_campaign(
    cfg: MailchimpConfig, subject: str, title: str,
    from_name: str = "", reply_to: str = "", list_id: str = "",
    segment_id: int | None = None, segment_ids: "list[int] | None" = None,
) -> tuple[str, str]:
    """regular 캠페인 생성 → (campaign_id, 오류메시지). 아직 발송 안 함.

    대상 좁히기:
      · segment_ids 가 둘 이상이면 **그 태그 중 하나라도 달린 사람**에게 보낸다
        (match=any 조건 세그먼트). 여러 태그에 겹쳐 있는 사람도 **한 통만** 받는다.
      · 하나면 예전처럼 saved_segment_id 로 건다.
      · 아무것도 없으면 오디언스 전체.
    태그는 모두 **같은 오디언스**의 것이어야 한다(캠페인은 오디언스 하나만 겨냥한다).
    """
    if _shadow.blocked("mailchimp", "create_campaign", subject=subject, title=title, list_id=list_id,
                       segment_ids=segment_ids or ([segment_id] if segment_id is not None else [])):
        return "shadow", ""
    lid = (list_id or cfg.list_id or "").strip()
    if not lid:
        return "", "오디언스(List) ID 가 설정되지 않았습니다."
    ids = [int(x) for x in (segment_ids or []) if x is not None]
    if not ids and segment_id is not None:
        ids = [int(segment_id)]
    recipients: dict = {"list_id": lid}
    if len(ids) == 1:
        recipients["segment_opts"] = {"saved_segment_id": ids[0]}
    elif len(ids) > 1:
        recipients["segment_opts"] = {
            "match": "any",
            "conditions": [
                {"condition_type": "StaticSegment", "field": "static_segment",
                 "op": "static_is", "value": i} for i in ids
            ],
        }
    payload = {
        "type": "regular",
        "recipients": recipients,
        "settings": {
            "subject_line": subject or cfg.subject_default or "PointMobile Newsletter",
            "title": title or subject or "Newsletter",
            # from_name 은 '표시 이름'이라 @ 등 불가 → 정리. 이메일은 reply_to 로 간다.
            "from_name": _clean_from_name(from_name or cfg.from_name),
            "reply_to": reply_to or cfg.reply_to or "",
        },
    }
    try:
        r = requests.post(f"{_base(cfg)}/campaigns", auth=_auth(cfg), json=payload, timeout=_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return "", f"접속 오류: {e}"
    if r.status_code in (200, 201):
        return r.json().get("id", ""), ""
    return "", _err(r)


def set_content(cfg: MailchimpConfig, campaign_id: str, html: str) -> tuple[bool, str]:
    """캠페인 본문 HTML 설정."""
    if _shadow.blocked("mailchimp", "set_content", campaign_id=campaign_id, html=html):
        return True, ""
    try:
        r = requests.put(
            f"{_base(cfg)}/campaigns/{campaign_id}/content",
            auth=_auth(cfg), json={"html": html}, timeout=_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001
        return False, f"접속 오류: {e}"
    if r.status_code == 200:
        return True, ""
    return False, _err(r)


def send_campaign(
    cfg: MailchimpConfig, campaign_id: str, allow_send: bool = False
) -> tuple[bool, str]:
    """캠페인을 오디언스에 **실제 발송**한다.

    allow_send=True 가 아니면 실제 호출하지 않고 dry(미발송) 로 리턴한다(안전 게이트).
    """
    if allow_send and _shadow.blocked("mailchimp", "send_campaign", campaign_id=campaign_id):
        return True, "[검증 모드] 발송하지 않고 기록만 했습니다."
    if not allow_send:
        return False, "DRY: 발송 게이트가 열리지 않았습니다(allow_send=False)."
    try:
        r = requests.post(
            f"{_base(cfg)}/campaigns/{campaign_id}/actions/send",
            auth=_auth(cfg), timeout=_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001
        return False, f"접속 오류: {e}"
    if r.status_code == 204:
        return True, "발송 요청 완료(Mailchimp 큐에 등록됨)."
    return False, _err(r)


def upload_image(cfg: MailchimpConfig, name: str, data: bytes) -> tuple[str, str]:
    """이미지를 Mailchimp File Manager 에 업로드 → (호스팅 URL, 오류메시지).

    이메일 본문의 이미지는 base64 임베드 대신 이 호스팅 URL 을 써야 대부분의 메일 클라이언트에서 표시된다.
    """
    if _shadow.blocked("mailchimp", "upload_image", name=name, size=len(data)):
        return f"https://shadow.invalid/mailchimp/{name}", ""
    import base64 as _b64

    payload = {"name": name, "file_data": _b64.b64encode(data).decode("ascii")}
    try:
        r = requests.post(f"{_base(cfg)}/file-manager/files", auth=_auth(cfg),
                          json=payload, timeout=_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return "", f"접속 오류: {e}"
    if r.status_code in (200, 201):
        return r.json().get("full_size_url", ""), ""
    return "", _err(r)
