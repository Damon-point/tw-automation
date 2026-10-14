"""Redmine 본문(Textile) → 안전한 HTML.

Redmine 이슈 설명·코멘트는 **Textile** 마크업이다. 그대로 화면에 뿌리면
`*`, `***`, `|_. 항목 |` 같은 기호가 날것으로 보여 읽을 수가 없다.
(실제 이슈 본문의 절반 이상이 `**`·`***` 중첩 목록이다.)

textile 패키지로 변환하되, Redmine 고유 표기 세 가지는 변환 전에 손본다.
  · `!image.png!`      — Redmine 은 첨부를 가리키지만 우리는 파일을 안 받아 온다.
                         그대로 두면 깨진 <img> 가 뜨므로 파일명 표시로 바꾼다.
  · `attachment:파일명`  — 같은 이유로 파일명만 남긴다.
  · `#33907`           — Redmine 처럼 이슈 링크로 만든다.

변환 결과는 nh3(ammonia)로 걸러 낸다. 이슈 본문은 사외에서 들어온 텍스트가
섞이는 자리라 script/onclick 류가 통과하면 안 된다.
"""
from __future__ import annotations

import html as _html
import re

import nh3
import textile

#: 화면에 남길 태그. Textile 이 만들어 내는 것 + 링크·코드 정도면 충분하다.
_ALLOWED_TAGS = {
    "p", "br", "div", "span",
    "ul", "ol", "li", "dl", "dt", "dd",
    "strong", "b", "em", "i", "del", "ins", "sub", "sup",
    "code", "pre", "blockquote",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "tr", "th", "td", "caption", "colgroup", "col",
    "a", "hr",
}
#: 'rel' 은 넣지 않는다 — nh3 가 link_rel 로 직접 붙이며, 둘 다 지정하면 거부한다.
_ALLOWED_ATTRS = {"a": {"href", "title", "target"}, "*": {"class"}}

#: !파일명.png! — 앞뒤에 다른 글자가 붙지 않은 경우만(수식의 곱셈 등과 구분)
_IMG = re.compile(r"!([^!\s|]+\.(?:png|jpe?g|gif|bmp|webp|svg))!", re.I)
_ATTACHMENT = re.compile(r"\battachment:([^\s,)]+)")
#: 이슈 번호. 4자리 이상만 — '#1' 같은 건 이슈 참조가 아닐 때가 많다.
#: 앞에 &가 오면 HTML 엔티티(&#8220; 등)이므로 제외한다.
_ISSUE_REF = re.compile(r"(?<![\w#&])#(\d{4,6})\b")


def _pre_textile(text: str) -> str:
    """Textile 변환 전에 Redmine 고유 표기를 손본다."""
    out = _IMG.sub(lambda m: f"[첨부: {m.group(1)}]", text)
    out = _ATTACHMENT.sub(lambda m: f"[첨부: {m.group(1)}]", out)
    return out


def _link_issues(html_text: str, base: str) -> str:
    """#33907 → Redmine 링크. 이미 <a> 안에 있는 것은 건드리지 않는다."""
    if not base:
        return html_text
    parts = re.split(r"(<a\b[^>]*>.*?</a>|<[^>]+>)", html_text, flags=re.S)
    for i, part in enumerate(parts):
        if part.startswith("<"):
            continue
        parts[i] = _ISSUE_REF.sub(
            lambda m: f'<a href="{base}/issues/{m.group(1)}" '
                      f'target="_blank" rel="noopener">#{m.group(1)}</a>',
            part)
    return "".join(parts)


#: base_url() 은 DB 를 읽는다. 이슈 하나에 코멘트가 수십 개라 매번 부르면 안 된다.
_base_cache: list[str] = []


def _base() -> str:
    if not _base_cache:
        try:
            from app.issuetrack.client import base_url
            _base_cache.append(base_url())
        except Exception:  # noqa: BLE001
            _base_cache.append("")
    return _base_cache[0]


def render(text: str | None) -> str:
    """Textile 본문 → 안전한 HTML. 변환에 실패해도 원문은 반드시 보여 준다."""
    src = (text or "").strip()
    if not src:
        return ""

    try:
        out = textile.textile(_pre_textile(src))
    except Exception:  # noqa: BLE001
        # 마크업이 깨져 있어도 내용은 보여야 한다. 줄바꿈만 살려 그대로 낸다.
        return f'<pre style="white-space:pre-wrap;margin:0">{_html.escape(src)}</pre>'

    out = nh3.clean(out, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS,
                    link_rel="noopener noreferrer")

    # 이슈 링크는 **sanitize 뒤에** 만든다. 그 전에 하면 textile 이 남긴 HTML 엔티티
    # (&#8220; 등)의 숫자를 이슈 번호로 잡아 '&#8220;' 이 링크가 되는 사고가 난다.
    # 여기서 넣는 <a> 는 숫자만 뽑아 우리가 직접 만들므로 다시 걸러 낼 필요가 없다.
    return _link_issues(out, _base())
