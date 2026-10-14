"""단계 ⑦ Newsletter 제작 — 근거 기반 카드로 KR/EN 초안 HTML 생성(발송 X, 초안까지).

카드는 ②의 실제 공개 선별 항목(rn_public_changes)에서만 만든다(지어내지 않음).
  · 카드1 = Android 보안패치(PFU 자동값) — 항상
  · 공개 항목이 있으면 각 항목을 카드로(제목/설명 KR·EN + 토픽별 이미지, Claude 초안)
  · 없으면 '더 안정적인 동작'(안정성) 카드 1장
발송/Mailchimp 연동은 이후 단계. 지금은 검수용 초안 HTML(KR/EN) 산출까지.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from app import progress
from app.agent.runner import StageOutput
from app.atomic_io import atomic_write_text
from app.office import newsletter as NL
from app.pipeline.state import get_state, update_state
from app.integrations.claude_cli import have_auth  # noqa: E402

_OUT = Path(__file__).resolve().parent.parent.parent / "work" / "newsletter"


def _fmt_date(s: str) -> str:
    """다양한 날짜 문자열 → 'MMM DD YYYY'(예: 'Jul 16 2026'). 실패 시 원문."""
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%b %d, %Y", "%b %d %Y", "%B %d, %Y", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%b %d %Y")
        except ValueError:
            continue
    return s


def _resolve_date(st: dict) -> str:
    """출시 날짜를 견고하게 확보 — release_date 가 비어도 version_full/rn_doc_date/os_build_date 에서.

    version_full('68.05 (Jun 26 2026)')의 괄호 안 날짜가 원하는 형식 그대로라 우선 사용.
    """
    d = (st.get("release_date") or "").strip()
    if d:
        return _fmt_date(d)
    m = re.search(r"\(([^)]+)\)", st.get("version_full") or "")
    if m and m.group(1).strip():
        return m.group(1).strip()
    for k in ("rn_doc_date", "os_build_date"):
        v = (st.get(k) or "").strip()
        if v:
            return _fmt_date(v)
    return ""

_FEATURE_SYS = (
    "당신은 포인트모바일 제품 뉴스레터 카드 작성기입니다. 주어진 '고객 공개 변경 항목'들을 각각 "
    "이메일 카드로 만듭니다. 각 항목에 title_ko/title_en(짧은 제목), desc_ko/desc_en(1~2문장 설명), "
    "topic(security|network|pmdm|common 중 이미지 토픽; 네트워크/Wi-Fi=network, PMDM/Zero Touch/Enrollment=pmdm, "
    "그 외 일반=common) 을 JSON 으로. 주어진 항목 내용에만 근거하고 지어내지 마세요."
)


def _plain(items: list[str]) -> list[str]:
    """[Module] 태그를 뗀 문구. 뉴스레터 카드는 고객용 홍보 문구라 태그가 어울리지 않는다."""
    from app.pipeline.rn_select import strip_tag

    return [strip_tag(x) for x in items]


def _feature_cards(product: str, items: list[str], revision: str = "") -> list[dict]:
    """공개 항목 → 카드[{topic,title_ko,title_en,body_ko,body_en}]. Claude 실패 시 원문 폴백.

    revision: 검수 후 사람이 준 추가 수정 지시(있으면 Claude 가 최우선 반영해 재작업)."""
    if not have_auth():
        return [{"topic": "common", "title_ko": it[:40], "title_en": it[:60],
                 "body_ko": it, "body_en": it} for it in _plain(items)]
    import anthropic

    from app.pipeline.ai_edit import _extract_json

    blocks = "\n".join(f"- {it}" for it in items)
    extra = (f"\n[사람의 추가 수정 지시 — 최우선 반영]: {revision}\n"
             if (revision or "").strip() else "")
    prompt = (f"제품: {product}\n공개 변경 항목:\n{blocks}\n{extra}\n"
              '반드시 JSON: {"cards":[{"title_ko":"","title_en":"","desc_ko":"","desc_en":"","topic":""}]}')
    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=os.getenv("RN_RESEARCH_MODEL", "claude-sonnet-5"),
            max_tokens=1500, system=_FEATURE_SYS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        out = []
        for c in (_extract_json(text) or {}).get("cards") or []:
            out.append({
                "topic": (c.get("topic") or "common").strip().lower(),
                "title_ko": c.get("title_ko", "").strip(), "title_en": c.get("title_en", "").strip(),
                "body_ko": c.get("desc_ko", "").strip(), "body_en": c.get("desc_en", "").strip(),
            })
        if out:
            return out
    except Exception:  # noqa: BLE001
        pass
    return [{"topic": "common", "title_ko": it[:40], "title_en": it[:60],
             "body_ko": it, "body_en": it} for it in _plain(items)]


# ── 제목 ──────────────────────────────────────────────────────────────────
# 병합 태그(*|COMPANY|* · *|FNAME|*)는 **넣지 않는다** — 어느 자리에 어떤 값을 넣을지
# 정해지지 않아 걷어냈다(사용자 지시 2026-08-13). 넣을 때 참고할 실측값만 남긴다:
#   오디언스 'pointmobile' 의 병합 필드 = ADDRESS · BIRTHDAY · COMPANY · FNAME ·
#   LNAME · PHONE (한글 태그는 없다). 회사명 *|COMPANY|*(=*|MERGE6|*),
#   고객명 *|FNAME|*(=*|MERGE1|*). 둘 다 required=False·기본값 없음이라 빈 값이
#   실제로 나오므로, 넣을 때는 *|IF:…|* 조건 구문으로 감싸야 '— ' 가 남지 않는다.
SUBJECT_TMPL = "[PointMobile] {product} {version} OS Update"


#: Android 세대가 바뀐 출시는 '업데이트' 가 아니라 '새 세대 출시' 다 — 제목도 달리 간다.
SUBJECT_TMPL_NEWOS = "[PointMobile] {product} Android {android} is Now Available"


def subject_for(product: str, version: str, kind: str = "update",
                android: str = "") -> str:
    """뉴스레터 제목 초안. /newsletter 화면에서 사람이 고칠 수 있다."""
    if kind == "newos":
        return SUBJECT_TMPL_NEWOS.format(product=product, android=android)
    return SUBJECT_TMPL.format(product=product, version=version)


def newsletter_kind(product: str, android: str, version: str) -> tuple[str, str]:
    """이 회차에 어떤 뉴스레터를 낼지. (kind, 사유).

      'update'  — 평소의 OS 업데이트.
      'newos'   — 같은 모델의 **Android 세대가 바뀐** 첫 출시. 전용 판을 쓴다.
      'skip'    — **아예 신규 모델**. 신제품 출시 때 OS 뉴스레터는 내지 않는다
                  (사용자 확정 2026-08-13).

    첫 출시인지는 제품 등록의 '최초 OS 버전' 으로 본다(작업 만들 때 쓰는 판정과 같다).
    세대 변경과 신규 모델은 **그 제품에 다른 Android SKU 가 등록돼 있는지**로 가른다 —
    세대가 바뀐 것이라면 이전 세대 SKU 가 반드시 남아 있다.
    """
    from app.os_products import find_sku, list_products
    from app.os_update_jobs import is_first_release

    row = find_sku(product, android) or {}
    if not is_first_release(row.get("initial_os_version") or "", version):
        return "update", ""
    others = [p for p in list_products()
              if str(p.get("product", "")).strip().upper() == (product or "").strip().upper()
              and str(p.get("android", "")) != str(android)]
    if others:
        gens = ", ".join(sorted(str(p.get("android")) for p in others))
        return "newos", f"첫 출시 + 같은 제품의 다른 세대 등록됨(A{gens}) → 세대 변경"
    return "skip", "첫 출시 + 이 제품의 다른 세대 없음 → 신규 모델(뉴스레터 없음)"


def run_newsletter(
    user_id: int, subject: str, input_params: str = "", mail_id: int | None = None,
    revision: str = "",
) -> StageOutput:
    st = get_state(mail_id) if mail_id is not None else {}
    product = st.get("product") or ""
    android = str(st.get("android") or "")
    version = st.get("release_version") or st.get("os_version") or ""
    date = _resolve_date(st)
    patch = st.get("security_patch") or ""
    # ②에서 선별된 공개 내용(재작업·수작업 편집 반영본)만 카드로. ⑥ KB 와 동일 소스.
    from app.pipeline.rn_research import current_public_changes

    public_items = current_public_changes(st)
    sdk = (st.get("rn_components") or {}).get("sdk", "")
    # Release Note 링크 = **RN for Sales 를 Dropbox 에 올리고 받은 내려받기 주소**
    # (사용자 확정 2026-09-22). 받는 사람이 눌러야 하는 것은 그 회차의 RN 문서 자체다.
    # 예전에는 KB 문서 주소를 손으로 넣게 했는데, 그 업로드는 ③ Dropbox 단계 안에
    # 묻혀 있었고 Newsletter 제작이 도는 Submission 갈래에는 그 단계가 없어서 링크가
    # 생길 길이 자체가 없었다. 그래서 이 단계가 직접 올린다(아래, kind 를 가린 뒤).
    #
    # 링크는 **반드시 그 회차 문서**여야 한다. 비어 있을 때 KB 첫 화면으로 물러나던
    # 폴백은 두지 않는다 — 받는 사람이 이번 회차를 못 찾는다(사용자 확정 2026-08-05).
    # 손으로 넣어 둔 주소가 있으면 그것을 존중한다. 사람이 일부러 넣은 값이다.
    rn_link = (st.get("rn_confluence_link") or "").strip()

    if not product or not version:
        return StageOutput(result_text="✘ 제품/버전 미확정 — OS Download·Release Note 제작 먼저 완료하세요.")

    kind, why = newsletter_kind(product, android, version)
    if kind == "skip":
        # 신제품 출시 때는 OS 뉴스레터를 내지 않는다 — 알릴 '업데이트' 가 없다.
        return StageOutput(result_text=(
            f"[Newsletter · {product} {version}]\n"
            f"이번 회차는 뉴스레터를 만들지 않습니다 — {why}.\n"
            "신제품 출시 안내는 OS 뉴스레터와 별개입니다(사용자 확정 2026-08-13).\n"
            "필요하면 이 단계를 그대로 완료 처리하세요."
        ))
    # 뉴스레터를 내지 않는 회차(skip)까지 올릴 까닭은 없으므로 kind 를 가린 뒤에 한다.
    rn_note = ""
    if not rn_link and kind == "update":
        from app.pipeline.rn_dropbox import ensure_rn_link

        rn_link, _rn_name, rn_note = ensure_rn_link(user_id, mail_id)
    if not rn_link and kind == "update":
        return StageOutput(result_text=(
            "✘ Release Note 링크를 얻지 못했습니다 — Newsletter 를 만들 수 없습니다.\n"
            f"  {rn_note}\n"
            "  다른 주소를 쓰려면 [Release Note 링크 직접 지정] 에 넣고 다시 실행하세요."
        ))

    product_disp = f"{product} Android {android}" if android else product
    os_version_disp = f"{version} ({date})" if date else version

    # 근거 기반 카드
    cards: list[dict] = [{
        "topic": "security", "title_ko": "안드로이드 보안 패치", "title_en": "Android Security Patch",
        "body_ko": f'<a href="https://www.pointmobile.com/kr/service/puls.php" target="_blank">{patch or "-"}</a>',
        "body_en": patch or "-",
    }]
    used_claude = False
    if kind == "newos":
        # 세대 변경 판은 개선사항 카드가 없다(본문이 업그레이드 안내다) — Claude 를 부르지 않는다.
        cards = []
    elif public_items:
        progress.step(f"Claude가 뉴스레터 카드 작성 중… (공개 항목 {len(public_items)}건)")
        cards += _feature_cards(product, public_items, revision)
        used_claude = True
    else:
        cards.append({
            "topic": "common", "title_ko": "더 안정적인 동작", "title_en": "Improved Stability",
            "body_ko": "시스템 기능 전반적으로 안정성을 개선하여 더욱 원활하게 단말기를 이용할 수 있습니다.",
            "body_en": "Enhanced overall system stability for a smoother experience.",
        })

    progress.step("KR/EN 뉴스레터 HTML 생성 중…")
    _OUT.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for lang, key in (("kr", "ko"), ("en", "en")):   # 파일/템플릿은 kr, 카드 텍스트 키는 ko
        lc = [{"topic": c["topic"], "title": c[f"title_{key}"], "body": c[f"body_{key}"]} for c in cards]
        html = NL.render(lang, product_disp, product, os_version_disp, sdk, rn_link, lc,
                         kind=kind, android=android, security_patch=patch)
        fp = _OUT / f"[{product_disp}] {version} ({date}) ({lang.upper()}).html"
        atomic_write_text(fp, html)
        paths[lang] = str(fp)

    if mail_id is not None:
        update_state(mail_id, newsletter_paths=paths)
        # ⑦ 편집·발송 작업공간에 담는다(카드 필드 수정 → 재렌더 → Mailchimp 발송은 /newsletter 에서).
        from app.pipeline import newsletter_ws as NLWS

        _nl_draft = {
            "mail_id": mail_id,
            "subject": subject_for(product_disp, version, kind, android),
            # 재렌더(재작업)도 같은 판·같은 값으로 나가야 한다.
            "kind": kind, "android": android, "security_patch": patch,
            "product_disp": product_disp, "product_short": product,
            "os_version_disp": os_version_disp,
            "sdk": sdk, "rn_link": rn_link,
            "cards": cards,
            "html_path": paths.get("kr", ""),
        }
        NLWS.put(_nl_draft)
        # 이력: ⑦ Newsletter 최초 생성(또는 재실행) — initial 스냅샷.
        from app.pipeline import history as _H
        _H.record(mail_id, "newsletter", "생성", summary="Newsletter 초안 생성",
                  text=_H.text_from_newsletter(_nl_draft))

    card_lines = "\n".join(
        f"  · 카드 {i+1}: {c['title_ko']} / {c['title_en']} (이미지:{c['topic']})"
        for i, c in enumerate(cards)
    )
    summary = (
        f"[Newsletter 초안 · {product_disp} {version}]\n"
        f"KR/EN 초안 HTML 생성"
        + (" + Claude 초안" if used_claude else "(공개 기능 없음 → 보안패치+안정성)") + ".\n\n"
        + card_lines + "\n\n"
        f"KR: {paths['kr']}\nEN: {paths['en']}\n"
        f"SDK: {sdk or '(미입력)'} · 보안패치: {patch or '(미확보)'} · RN 링크: {rn_link}\n"
        + (f"{rn_note}\n" if rn_note else "")
    )
    return StageOutput(result_text=summary, artifact_type="html", artifact_path=paths["kr"])


_REWORK_CARD_SYS = (
    "당신은 이미 완성된 제품 뉴스레터 기능 카드들을 사람의 추가 지시대로 수정하는 편집기입니다. "
    "전체를 새로 쓰지 말고 지시가 요구하는 부분만 바꾸세요(카드 삭제/추가/문구 수정). "
    "각 카드는 title_ko/title_en(짧은 제목), desc_ko/desc_en(1~2문장), "
    "topic(security|network|pmdm|common) 을 유지합니다. 원문에 없는 내용은 지어내지 말 것.\n"
    '반드시 JSON: {"cards":[{"title_ko":"","title_en":"","desc_ko":"","desc_en":"","topic":""}]}'
)


def _rework_cards(product: str, cards: list[dict], revision: str) -> list[dict]:
    """기존 기능 카드에 지시(revision) 반영. 실패 시 원본 유지."""
    if not cards or not have_auth():
        return cards
    import anthropic

    from app.pipeline.ai_edit import _extract_json

    dump = [{"title_ko": c.get("title_ko", ""), "title_en": c.get("title_en", ""),
             "desc_ko": c.get("body_ko", ""), "desc_en": c.get("body_en", ""),
             "topic": c.get("topic", "common")} for c in cards]
    prompt = (f"제품: {product}\n[현재 카드]\n{json.dumps(dump, ensure_ascii=False, indent=2)}\n\n"
              f"[사람의 추가 수정 지시 — 반드시 반영]\n{revision}\n\n"
              "지시를 반영한 최종 카드 JSON 을 반환하세요.")
    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=os.getenv("RN_RESEARCH_MODEL", "claude-sonnet-5"),
            max_tokens=2000, system=_REWORK_CARD_SYS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        out = []
        for c in (_extract_json(text) or {}).get("cards") or []:
            out.append({
                "topic": (c.get("topic") or "common").strip().lower(),
                "title_ko": c.get("title_ko", "").strip(), "title_en": c.get("title_en", "").strip(),
                "body_ko": c.get("desc_ko", "").strip(), "body_en": c.get("desc_en", "").strip(),
            })
        if out:
            return out
    except Exception:  # noqa: BLE001
        pass
    return cards


def rework_newsletter(mail_id: int, revision: str) -> StageOutput:
    """⑦ 재작업 — **완료된 카드에 지시만 반영**해 KR/EN 재렌더. 근거 재수집 없음.

    1번째 보안 패치 카드는 보존하고 기능 카드만 편집한다."""
    from app.pipeline import newsletter_ws as NLWS

    if not (revision or "").strip():
        return StageOutput(result_text="✘ 수정 지시가 비어 있습니다.")
    draft = NLWS.get(mail_id)
    if not draft:
        return StageOutput(result_text="✘ 재작업할 뉴스레터 초안이 없습니다. Newsletter 제작을 먼저 실행하세요.")
    cards = draft.get("cards") or []
    if not cards:
        return StageOutput(result_text="✘ 카드가 없습니다.")

    product = draft.get("product_short", "")
    product_disp = draft.get("product_disp", product)
    os_version_disp = draft.get("os_version_disp", "")
    sdk = draft.get("sdk", "")
    rn_link = draft.get("rn_link", "")
    kind = draft.get("kind", "update")
    android = str(draft.get("android", "") or "")
    patch = draft.get("security_patch", "") or ""

    progress.step("추가 지시 반영(뉴스레터 카드 편집) 중…")
    new_cards = cards[:1] + _rework_cards(product, cards[1:], revision)   # 1번째=보안카드 보존

    st = get_state(mail_id) if mail_id is not None else {}
    version = st.get("release_version") or st.get("os_version") or ""
    date = _resolve_date(st)

    progress.step("KR/EN 뉴스레터 재렌더 중…")
    _OUT.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for lang, key in (("kr", "ko"), ("en", "en")):
        lc = [{"topic": c["topic"], "title": c[f"title_{key}"], "body": c[f"body_{key}"]}
              for c in new_cards]
        html = NL.render(lang, product_disp, product, os_version_disp, sdk, rn_link, lc,
                         kind=kind, android=android, security_patch=patch)
        fp = _OUT / f"[{product_disp}] {version} ({date}) ({lang.upper()}).html"
        atomic_write_text(fp, html)
        paths[lang] = str(fp)

    NLWS.update_fields(mail_id, cards=new_cards, html_path=paths.get("kr", ""))
    if mail_id is not None:
        update_state(mail_id, newsletter_paths=paths)

    card_lines = "\n".join(
        f"  · 카드 {i + 1}: {c['title_ko']} / {c['title_en']} (이미지:{c['topic']})"
        for i, c in enumerate(new_cards)
    )
    summary = (f"[Newsletter 재작업 · 완료 카드 편집]\n지시: {revision}\n\n" + card_lines + "\n\n"
               f"KR: {paths['kr']}\nEN: {paths['en']}\n"
               "※ 근거 재수집 없이 저장된 카드에 지시만 반영해 재렌더했습니다.")
    return StageOutput(result_text=summary, artifact_type="html", artifact_path=paths["kr"])
