"""⑦ Newsletter(Mailchimp HTML) 렌더러 — placeholder 템플릿(KR/EN)에 근거 기반 카드 채움.

카드 내용은 ②의 실제 공개 선별 항목에서만 나온다(지어내지 않음). 이미지는 표준 세트에서 토픽으로
선택해 data URI 로 임베드(초안 자체 렌더용). 발송/Mailchimp 연동은 별도(지금은 초안 HTML 생성까지).
"""
from __future__ import annotations

import base64
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent.parent / "data"
_TPL = _DATA / "templates"
_IMG = _DATA / "newsletter_images"

# 개선사항 토픽 → 표준 이미지 파일
_TOPIC_IMAGE = {
    "security": "OS_AndroidSecurityPatch.png",
    "network": "Network.png",
    "pmdm": "PMDM_ZeroTouchEnrollment.png",
    "common": "Improvements(common).png",
}

_IMG_CACHE: dict[str, str] = {}


def _img_data_uri(topic: str) -> str:
    name = _TOPIC_IMAGE.get(topic, _TOPIC_IMAGE["common"])
    if name in _IMG_CACHE:
        return _IMG_CACHE[name]
    p = _IMG / name
    uri = ""
    if p.exists():
        uri = "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
    _IMG_CACHE[name] = uri
    return uri


def topic_image_files() -> dict[str, Path]:
    """토픽 → 표준 이미지 파일 경로 (호스팅 업로드용)."""
    return {t: _IMG / name for t, name in _TOPIC_IMAGE.items()}


def _img_src(topic: str, image_urls: dict | None) -> str:
    """이미지 src — 호스팅 URL 이 있으면 그걸(이메일용), 없으면 data URI(미리보기용)."""
    if image_urls:
        url = image_urls.get(topic) or image_urls.get("common")
        if url:
            return url
    return _img_data_uri(topic)


# 카드(오른쪽 이미지 레이아웃) — Mailchimp mcnCaptionBlock 구조 근사
_CARD = """<table border="0" cellpadding="0" cellspacing="0" width="100%" class="mcnCaptionBlock">
 <tbody class="mcnCaptionBlockOuter"><tr>
  <td class="mcnCaptionBlockInner" valign="top" style="padding:9px;">
   <table border="0" cellpadding="0" cellspacing="0" class="mcnCaptionRightContentOuter" width="100%">
    <tbody><tr><td valign="top" class="mcnCaptionRightContentInner" style="padding:0 9px;">
     <table align="left" border="0" cellpadding="0" cellspacing="0" class="mcnCaptionRightImageContentContainer" width="176">
      <tbody><tr><td class="mcnCaptionRightImageContent" align="center" valign="top">
       <img alt="" src="__IMG__" width="176" style="max-width:315px; border-radius:0%;" class="mcnImage">
      </td></tr></tbody></table>
     <table class="mcnCaptionRightTextContentContainer" align="right" border="0" cellpadding="0" cellspacing="0" width="352">
      <tbody><tr><td valign="top" class="mcnTextContent">
       <h3>__TITLE__</h3><br>__BODY__
      </td></tr></tbody></table>
    </td></tr></tbody>
   </table>
  </td></tr></tbody>
</table>"""

_DIVIDER = """<table border="0" cellpadding="0" cellspacing="0" width="100%" class="mcnDividerBlock" style="min-width:100%;">
 <tbody class="mcnDividerBlockOuter"><tr>
  <td class="mcnDividerBlockInner" style="min-width:100%; padding:12px 18px;">
   <table class="mcnDividerContent" border="0" cellpadding="0" cellspacing="0" width="100%" style="min-width:100%;border-top:2px solid #EAEAEA;">
    <tbody><tr><td><span></span></td></tr></tbody></table>
  </td></tr></tbody>
</table>"""


def _card_html(topic: str, title: str, body: str, image_urls: dict | None = None) -> str:
    return (_CARD.replace("__IMG__", _img_src(topic, image_urls))
            .replace("__TITLE__", title).replace("__BODY__", body))


def render(lang: str, product: str, product_short: str, os_version: str,
           sdk: str, rn_link: str, cards: list[dict], image_urls: dict | None = None,
           kind: str = "update", android: str = "", security_patch: str = "") -> str:
    """cards: [{topic, title, body}] (해당 언어 텍스트). 완성 HTML 반환.

    image_urls({topic: url}) 를 주면 카드 이미지를 그 호스팅 URL 로(이메일 발송용).
    안 주면 data URI 로 임베드(로컬 미리보기용).

    kind 로 어떤 판을 쓸지 고른다:
      'update' — 평소의 OS 업데이트 안내(개선사항 카드).
      'newos'  — **같은 모델의 Android 세대가 바뀐** 출시 안내. 개선사항 목록이 아니라
                 '서비스센터에서 OS 플래싱으로만 업그레이드, PULS 인증 필요' 안내가 본문이라
                 문서 구성이 아예 다르다(사용자 제공 판, 2026-08-13). 카드가 없다.
    """
    name = "newsletter_newos" if kind == "newos" else "newsletter"
    tpl_path = _TPL / f"{name}_{lang}.html"
    html = tpl_path.read_text(encoding="utf-8")
    cards_html = "".join(
        _card_html(c["topic"], c["title"], c["body"], image_urls) + _DIVIDER for c in cards
    )
    html = (html.replace("{{CARDS}}", cards_html)
            .replace("{{PRODUCT}}", product)
            .replace("{{PRODUCT_SHORT}}", product_short)
            .replace("{{ANDROID}}", str(android or ""))
            .replace("{{SECURITY_PATCH}}", security_patch or "")
            # OS 버전 셀은 '버전' + '(날짜)' 두 줄로 표시(첫 ' (' 앞에서 줄바꿈).
            .replace("{{OS_VERSION}}", (os_version or "").replace(" (", "<br>(", 1))
            .replace("{{SDK}}", sdk or "")
            .replace("{{RN_LINK}}", rn_link or "#"))
    return html
