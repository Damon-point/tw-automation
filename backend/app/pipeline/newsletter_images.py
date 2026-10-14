"""⑦ 뉴스레터 표준 이미지를 Mailchimp File Manager 에 호스팅하고 URL 을 캐시한다.

이메일 본문 이미지는 base64 임베드하면 상당수 메일 클라이언트에서 표시되지 않는다.
발송 시 각 토픽 이미지를 Mailchimp 에 업로드해 호스팅 URL 로 치환한다. 같은 이미지는
내용 해시로 캐시해 재업로드하지 않는다.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.atomic_io import atomic_write_text
from app.integrations import mailchimp as MC
from app.office import newsletter as NL

_CACHE = Path(__file__).resolve().parent.parent.parent / "work" / "newsletter_image_urls.json"


def _load() -> dict:
    if _CACHE.exists():
        try:
            return json.loads(_CACHE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save(d: dict) -> None:
    atomic_write_text(_CACHE, json.dumps(d, ensure_ascii=False, indent=2))


def ensure_hosted_images(cfg) -> dict:
    """토픽 이미지들을 Mailchimp 에 업로드(캐시)하고 {topic: url} 반환.

    업로드 실패한 토픽은 결과에서 빠진다(→ 렌더 시 그 토픽만 data URI 폴백).
    """
    cache = _load()
    out: dict[str, str] = {}
    for topic, path in NL.topic_image_files().items():
        if not path or not path.exists():
            continue
        data = path.read_bytes()
        h = hashlib.sha1(data).hexdigest()[:12]
        key = f"{topic}:{h}"
        if cache.get(key):
            out[topic] = cache[key]
            continue
        url, err = MC.upload_image(cfg, f"pm_newsletter_{topic}_{h}.png", data)
        if url:
            cache[key] = url
            out[topic] = url
    _save(cache)
    return out
