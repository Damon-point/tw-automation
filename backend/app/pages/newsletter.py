"""⑦ Newsletter 편집·발송 UI 컴포넌트.

별도 페이지가 아니라 메일함 ⑦ 단계의 '검수하기' 팝업 안에서 렌더된다.
카드 필드(제목·본문 KR)를 수정 → 미리보기 재렌더 → Mailchimp 태그(세그먼트)로 실제 발송.
발송은 되돌릴 수 없는 외부 동작이라, 확인창에서 대상(태그)·수신자 수를 보고 승인해야 실행된다.
"""
from nicegui import run, ui

from app.auth import current_user_id, current_username
from app.office import newsletter as NL
from app.pipeline import history as H
from app.pipeline import newsletter_ws as NLWS


# 발송 화면에 보일 태그 — **여기 적은 순서 그대로** 보인다(사용자 지정 2026-09-11).
# 계정에는 태그가 130개쯤 있지만 대부분 전시회·초청장용이라 OS 업데이트 안내와 상관없다.
# 대소문자는 가리지 않는다 — 계정에 'pm_sales2 & customer' 처럼 소문자로 들어간 것이 있다.
SEND_TAGS: list[str] = [
    "OS1/2/7",
    "pm_sales1",
    "pm_sales2 & Customer",
    "PM85",
    "PM451",
    "PM500",
    "EmSolutions_customer",
    "cc",
    "cc_EmSolutions",
    "cc_PM5",
    "cc_RFID",
    "internal_news",
    "PM30_Gun_recall",
    "PM95/PM86/TR54/PM84",
    "PM_Support",
    "PMDM Enterprise",
    "PMDM Users",
]


def _send_link(mail_id) -> str:
    """발송될 메일의 Release Note 버튼 주소 = 지금 초안에 든 링크."""
    return str((NLWS.get(mail_id) or {}).get("rn_link") or "").strip()


def _pick_send_tags(tags: list[dict]) -> list[dict]:
    """계정 태그에서 SEND_TAGS 에 적힌 것만 **적힌 순서대로** 골라 준다."""
    order = {n.strip().lower(): i for i, n in enumerate(SEND_TAGS)}
    out = [t for t in tags if (t.get("name") or "").strip().lower() in order]
    out.sort(key=lambda t: order[(t.get("name") or "").strip().lower()])
    return out


def _render_kr(draft: dict, cards: list[dict], image_urls: dict | None = None) -> str:
    """편집한 카드(KR)로 KR 뉴스레터 HTML 을 렌더.

    image_urls 를 주면 카드 이미지를 호스팅 URL 로(이메일 발송용), 없으면 data URI(미리보기).
    """
    lc = [
        {"topic": c.get("topic", "common"),
         "title": c.get("title_ko", ""), "body": c.get("body_ko", "")}
        for c in cards
    ]
    return NL.render(
        "kr", draft.get("product_disp", ""), draft.get("product_short", ""),
        draft.get("os_version_disp", ""), draft.get("sdk", ""),
        draft.get("rn_link", ""), lc, image_urls=image_urls,
    )


def _render_en(draft: dict, cards: list[dict], image_urls: dict | None = None) -> str:
    """편집한 카드(EN)로 EN 뉴스레터 HTML 을 렌더."""
    lc = [
        {"topic": c.get("topic", "common"),
         "title": c.get("title_en", ""), "body": c.get("body_en", "")}
        for c in cards
    ]
    return NL.render(
        "en", draft.get("product_disp", ""), draft.get("product_short", ""),
        draft.get("os_version_disp", ""), draft.get("sdk", ""),
        draft.get("rn_link", ""), lc, image_urls=image_urls,
    )


def _en_path(kr_path: str) -> str:
    """KR html 경로 → EN html 경로('(KR).html' → '(EN).html')."""
    suffix = "(KR).html"
    return kr_path[: -len(suffix)] + "(EN).html" if kr_path.endswith(suffix) else ""


_TOPICS = {"security": "보안", "network": "네트워크", "pmdm": "PMDM/ZeroTouch", "common": "일반"}


_PREVIEW_SEQ = {"n": 0}


def _iframe_url(path: str):
    """미리보기 HTML 을 파일 URL(/artifact/view)로 iframe 에 띄운다.

    data: URI 는 큰 뉴스레터(이미지 base64 임베드로 1MB+)에서 URL 길이 한계로 못 띄우고,
    srcdoc 는 NiceGUI 가 <script> 포함 HTML 을 막는다. 파일을 work/ 아래에 쓰고 URL 로
    불러오면 크기·script 제한 없이 렌더된다(라우트는 로그인 필수 + work/ 하위만 허용).

    URL 이 매번 같으면 브라우저가 캐시된 예전 미리보기를 보여줘 '수정해도 반영 안 됨'
    (특히 EN 탭)이 생긴다. 매 렌더마다 증가하는 v= 파라미터로 캐시를 무력화한다.
    """
    from urllib.parse import quote

    _PREVIEW_SEQ["n"] += 1
    src = f"/artifact/view?path={quote(path)}&v={_PREVIEW_SEQ['n']}"
    ui.html(
        '<iframe sandbox '
        f'src="{src}" '
        'style="width:100%;min-height:52vh;border:1px solid #ddd;background:white;">'
        "</iframe>",
        sanitize=False,
    ).classes("w-full")


def render_newsletter_editor(mail_id, on_sent=None, *, allow_send: bool = True,
                             edit: bool = True) -> None:
    """⑦ 검수 팝업 안에 카드 편집 + 미리보기 + Mailchimp 발송 UI 를 렌더한다.

    on_sent: 발송 완료 시 호출할 콜백(선택) — 바깥 화면 갱신용.
    allow_send: False 면 실제 발송 버튼을 숨긴다(Submission = 제작만, 발송은 Approval).
    edit: False 면 편집 UI 없이 완성본 미리보기 + 발송만 보여준다(Approval = 발송만).
    """
    user_id = current_user_id()
    draft = NLWS.get(mail_id)
    if not draft:
        ui.label(
            "이 작업의 Newsletter 초안이 없습니다. 먼저 Newsletter 제작을 실행해 초안을 만들어주세요."
        ).classes("text-caption text-grey-7")
        return

    cards = [dict(c) for c in draft.get("cards", [])]
    ui.label(
        f'{draft.get("product_disp","")} · {draft.get("os_version_disp","")}'
        + (" · 발송완료 ✓" if draft.get("sent") else "")
    ).classes("text-subtitle2")

    inputs: list[dict] = []
    _send_opt: dict = {}          # 발송 줄(본문·태그) — 아래에서 채운다
    preview_box = ui.column().classes("w-full")

    def _sync_inputs() -> None:
        """화면 입력값을 cards 에 반영 — 카드 추가/삭제로 다시 그리기 전에 편집분을 보존한다."""
        for c, io in zip(cards, inputs):
            c["topic"] = io["topic"].value or "common"
            c["title_ko"] = (io["title_ko"].value or "").strip()
            c["body_ko"] = (io["body_ko"].value or "").strip()
            c["title_en"] = (io["title_en"].value or "").strip()
            c["body_en"] = (io["body_en"].value or "").strip()

    def _add_card() -> None:
        _sync_inputs()
        cards.append({"topic": "common", "title_ko": "", "body_ko": "",
                      "title_en": "", "body_en": ""})
        card_list.refresh()

    def _remove_card(idx: int) -> None:
        _sync_inputs()
        if 0 <= idx < len(cards):
            cards.pop(idx)
        card_list.refresh()

    def _move_card(idx: int, delta: int) -> None:
        """카드 순서 변경(위/아래). 편집 중인 내용을 먼저 반영한 뒤 자리를 맞바꾼다."""
        _sync_inputs()
        j = idx + delta
        if 0 <= idx < len(cards) and 0 <= j < len(cards):
            cards[idx], cards[j] = cards[j], cards[idx]
        card_list.refresh()

    subj_in = None
    osver_in = None
    # 제목·카드 편집 칸 — Submission(edit=True)에서만 보여준다.
    # Approval(edit=False)에서는 Submission 승인본을 그대로 발송만 하므로 편집 UI 를 아예 뺀다.
    if edit:
        with ui.expansion("제목·카드 편집", icon="edit", value=False).classes("w-full"):
            subj_in = ui.input("메일 제목", value=draft.get("subject", "")).classes("w-full")
            osver_in = ui.input("OS 버전 표기", value=draft.get("os_version_disp", "")) \
                .props("hint='예: 68.04 (Jul 16 2026) — 표에서 버전/(날짜) 두 줄로 표시됩니다'") \
                .classes("w-full")

            @ui.refreshable
            def card_list() -> None:
                inputs.clear()
                for i, c in enumerate(cards):
                    with ui.card().classes("w-full"):
                        with ui.row().classes("items-center gap-2 w-full no-wrap"):
                            ui.label(f"카드 {i+1}").classes("text-caption text-grey-7")
                            _tv = c.get("topic", "common")
                            topic_sel = ui.select(
                                _TOPICS, value=_tv if _tv in _TOPICS else "common",
                                label="이미지 토픽",
                            ).props("dense outlined").classes("w-48")
                            ui.space()
                            ui.button(icon="arrow_upward",
                                      on_click=lambda _=None, k=i: _move_card(k, -1)) \
                                .props("flat dense").tooltip("위로 이동") \
                                .set_enabled(i > 0)
                            ui.button(icon="arrow_downward",
                                      on_click=lambda _=None, k=i: _move_card(k, 1)) \
                                .props("flat dense").tooltip("아래로 이동") \
                                .set_enabled(i < len(cards) - 1)
                            ui.button(icon="delete", on_click=lambda _=None, k=i: _remove_card(k)) \
                                .props("flat dense color=negative").tooltip("이 카드 삭제")
                        tko = ui.input("제목 (KR)", value=c.get("title_ko", "")).classes("w-full")
                        bko = ui.textarea("본문 (KR)", value=c.get("body_ko", "")) \
                            .classes("w-full").props("autogrow")
                        ten = ui.input("제목 (EN)", value=c.get("title_en", "")).classes("w-full")
                        ben = ui.textarea("본문 (EN)", value=c.get("body_en", "")) \
                            .classes("w-full").props("autogrow")
                        inputs.append({"topic": topic_sel, "title_ko": tko, "body_ko": bko,
                                       "title_en": ten, "body_en": ben})
                if not cards:
                    ui.label("카드가 없습니다. '카드 추가'로 만들어 주세요.").classes(
                        "text-caption text-grey-6")
                ui.button("카드 추가", icon="add", on_click=_add_card).props("outline size=sm")

            card_list()
    else:
        ui.label("Submission 단계에서 완성한 Newsletter 입니다. 아래 미리보기를 확인하고 발송하세요.") \
            .classes("text-caption text-grey-7")
    preview_box

    def collect_cards() -> list[dict]:
        # 편집 UI 가 없으면(Approval) inputs 가 비어 있다 → 저장된 카드를 그대로 쓴다.
        if not inputs:
            return [dict(c) for c in cards]
        out = []
        for c, io in zip(cards, inputs):
            nc = dict(c)
            nc["topic"] = io["topic"].value or "common"
            nc["title_ko"] = (io["title_ko"].value or "").strip()
            nc["body_ko"] = (io["body_ko"].value or "").strip()
            nc["title_en"] = (io["title_en"].value or "").strip()
            nc["body_en"] = (io["body_en"].value or "").strip()
            out.append(nc)
        return out

    def _preview_paths() -> tuple[str, str]:
        """미리보기 HTML 을 저장할 KR/EN 경로. 초안 html_path 우선, 없으면 work/ 임시 경로.

        저장된 경로는 **work/ 안으로 당겨서** 쓴다. 그대로 믿고 쓰면 없어진 폴더를
        되살려 거기에 파일을 만들고, 열람은 403 으로 막힌다(실측 2026-10-07).
        """
        from pathlib import Path

        from app.work_paths import anchor

        kr = anchor(draft.get("html_path", "") or "")
        if not kr:
            base = Path(__file__).resolve().parent.parent.parent / "work" / "newsletter_preview"
            base.mkdir(parents=True, exist_ok=True)
            kr = str(base / f"nl_{mail_id}_KR.html")
        en = _en_path(kr) or (kr[:-5] + "_EN.html" if kr.endswith(".html") else kr + "_EN.html")
        return kr, en

    def _show_preview(kr_html: str, en_html: str = ""):
        from app.atomic_io import atomic_write_text

        kr_path, en_path = _preview_paths()
        try:
            atomic_write_text(kr_path, kr_html or "")
            if en_html:
                atomic_write_text(en_path, en_html)
        except Exception:  # noqa: BLE001
            pass
        preview_box.clear()
        with preview_box:
            with ui.tabs() as _t:
                ui.tab("KR")
                ui.tab("EN")
            with ui.tab_panels(_t, value="KR").classes("w-full"):
                with ui.tab_panel("KR"):
                    _iframe_url(kr_path)
                with ui.tab_panel("EN"):
                    if en_html:
                        _iframe_url(en_path)
                    else:
                        ui.label("‘미리보기 재렌더’를 누르면 EN 도 표시됩니다.").classes(
                            "text-caption text-grey-7")

    def do_preview():
        new_cards = collect_cards()
        draft["os_version_disp"] = (osver_in.value or "").strip()   # 편집한 OS 버전 표기 반영
        kr_html = _render_kr(draft, new_cards)
        en_html = _render_en(draft, new_cards)
        from app.work_paths import anchor

        path = anchor(draft.get("html_path", ""))
        if path:
            from app.atomic_io import atomic_write_text
            try:
                atomic_write_text(path, kr_html)
                enp = _en_path(path)
                if enp:
                    atomic_write_text(enp, en_html)
            except Exception:  # noqa: BLE001
                pass
        NLWS.update_fields(mail_id, subject=subj_in.value.strip(),
                           cards=new_cards, html_path=path,
                           os_version_disp=draft["os_version_disp"])
        _show_preview(kr_html, en_html)
        H.record(mail_id, "newsletter", "편집", actor=current_username(),
                 summary="카드·제목 편집 저장",
                 text=H.text_from_newsletter({
                     **draft,
                     "subject": (subj_in.value or "").strip() if subj_in else draft.get("subject", ""),
                     "cards": new_cards}))
        ui.notify("미리보기를 갱신하고 편집본(KR/EN)을 저장했습니다.", color="positive")

    async def do_send():
        """줄마다 캠페인을 하나씩 만들어 보낸다.

        줄 = (본문 언어, 보낼 그룹). 태그마다 보낼 언어가 달라서, 한 캠페인에 여러 태그를
        묶는 대신 **줄마다 따로** 만든다(사용자 확정 2026-09-11).
        """
        from app.integrations import mailchimp as MC
        from app.integrations.creds import get_mailchimp_config

        # 발송 직전에도 확인한다. 보는 것은 **초안에 실제로 들어간 링크** 다 — 메일 버튼이
        # 가리키는 주소가 바로 그것이기 때문이다. 링크는 제작 단계가 넣는다: 손으로 지정한
        # 주소가 있으면 그것, 없으면 RN for Sales 를 Dropbox 에 올린 링크(2026-09-22 확정).
        # 예전에는 손으로 넣는 주소(rn_confluence_link)만 봐서, Dropbox 링크로 만든 초안이
        # 발송에서 막혔다(job-24·30·31, 2026-10-12 발견).
        if not _send_link(mail_id):
            ui.notify("초안에 Release Note 링크가 없습니다 — Newsletter 제작을 다시 실행하세요.",
                      color="negative", multi_line=True)
            return

        cfg = get_mailchimp_config(user_id)
        if cfg is None or not cfg.usable:
            ui.notify(
                "Mailchimp 설정이 없습니다. [설정 → Mailchimp]에서 API 키를 등록하세요.",
                color="negative",
            )
            return

        tags = _send_opt.get("tags") or {}
        plan = []                       # [(언어, 태그정보)]
        for r in _send_opt.get("rows") or []:
            t = tags.get(r.get("tag") or "")
            if t:
                plan.append((r.get("lang") or "kr", t))
        if not plan:
            ui.notify(
                "보낼 줄이 없습니다. 줄마다 보낼 그룹(태그)을 고르세요. 목록이 비어 있으면 "
                "옆 새로고침을 눌러 계정에서 다시 읽어 오세요.",
                color="negative", multi_line=True,
            )
            return

        subject = ((subj_in.value if subj_in else "") or "").strip() or draft.get("subject", "")
        total = sum(int(t.get("member_count") or 0) for _lg, t in plan)

        with ui.dialog() as dlg, ui.card().classes("w-[36rem] max-w-full"):
            ui.label("실제 발송 확인").classes("text-h6")
            ui.label(
                f"줄 {len(plan)}개 · 모두 {total}명에게 실제로 발송합니다. "
                "줄마다 캠페인이 하나씩 만들어집니다."
            ).classes("text-negative")
            for lg, t in plan:
                ui.label(
                    f"· {'한글본 (KR)' if lg == 'kr' else '영문본 (EN)'} → "
                    f"{t.get('name', '')} · {t.get('member_count', 0)}명 "
                    f"({t.get('list_name', '')})"
                ).classes("text-caption")
            ui.label(f"제목: {subject}").classes("text-caption")
            ui.label("이 작업은 되돌릴 수 없습니다.").classes("text-caption text-grey-7")

            async def confirm_send():
                dlg.close()
                n2 = ui.notification("Mailchimp 캠페인 생성·발송 중...", spinner=True,
                                     timeout=None)
                # 이메일 본문 이미지는 한 번만 올려 모든 줄이 함께 쓴다.
                from app.pipeline import newsletter_images as NLI

                img_urls = await run.io_bound(NLI.ensure_hosted_images, cfg)
                if osver_in:
                    draft["os_version_disp"] = (osver_in.value or "").strip()
                cards = collect_cards()

                done, fail, ids, lines = 0, 0, [], []
                for lg, t in plan:
                    who = f"{t.get('name', '')}({'EN' if lg == 'en' else 'KR'})"
                    cid, err = await run.io_bound(
                        MC.create_campaign, cfg, subject, subject,
                        list_id=t.get("list_id", ""), segment_id=int(t["id"]),
                    )
                    if not cid:
                        fail += 1
                        lines.append(f"✘ {who} — 캠페인 생성 실패: {err}")
                        continue
                    _render = _render_en if lg == "en" else _render_kr
                    ok, err = await run.io_bound(
                        MC.set_content, cfg, cid, _render(draft, cards, image_urls=img_urls))
                    if not ok:
                        fail += 1
                        lines.append(f"✘ {who} — 콘텐츠 설정 실패: {err}")
                        continue
                    ok, msg = await run.io_bound(MC.send_campaign, cfg, cid, True)
                    if not ok:
                        fail += 1
                        lines.append(f"✘ {who} — 발송 실패: {msg}")
                        continue
                    done += 1
                    ids.append(cid)
                    real = ""
                    try:
                        info2, _ = await run.io_bound(MC.get_campaign, cfg, cid)
                        if info2 and info2.get("recipient_count"):
                            real = f" · 수신자 {info2['recipient_count']}명"
                    except Exception:  # noqa: BLE001  (숫자 확인 실패는 발송과 무관)
                        pass
                    lines.append(f"✔ {who}{real} · campaign {cid}")
                n2.dismiss()

                if done:
                    NLWS.mark_sent(mail_id, ", ".join(ids))
                    H.record(mail_id, "newsletter", "발송", actor=current_username(),
                             summary=(f"Mailchimp 발송 — 성공 {done}건"
                                      + (f" · 실패 {fail}건" if fail else "") + "\n"
                                      + "\n".join(lines)),
                             text=H.text_from_newsletter({
                                 **draft, "subject": subject, "cards": cards}))
                ui.notify(
                    (f"발송 완료 ✓ {done}건" if not fail
                     else f"발송 {done}건 성공 · {fail}건 실패") + "\n" + "\n".join(lines),
                    color="positive" if not fail else "warning",
                    multi_line=True, timeout=15000)
                if done and on_sent:
                    on_sent()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("취소", on_click=dlg.close).props("flat")
                ui.button(f"{len(plan)}개 줄 발송", icon="send",
                          on_click=confirm_send).props("color=negative")
        dlg.open()

    # 발송 설정 — 줄마다 '본문 + 보낼 그룹(태그)'. Approval 갈래에서만 보인다.
    if allow_send:
        ui.separator()
        _send_opt["rows"] = [{"lang": "kr", "tag": ""}]
        _send_opt["tags"] = {}
        _send_opt["opts"] = {}
        ui.label("보낼 줄 — 줄마다 캠페인이 하나씩 만들어집니다").classes("text-subtitle2")
        rows_box = ui.column().classes("w-full gap-2")
        send_note = ui.label("").classes("text-caption text-grey-7")

        @ui.refreshable
        def rows_ui() -> None:
            rows = _send_opt["rows"]
            for i, r in enumerate(rows):
                with ui.row().classes("w-full items-center gap-3 no-wrap"):
                    ui.select(
                        {"kr": "한글본 (KR)", "en": "영문본 (EN)"},
                        value=r.get("lang") or "kr", label="보낼 본문",
                        on_change=lambda e, k=i: rows[k].update(lang=e.value or "kr"),
                    ).props("dense outlined").classes("w-44")
                    ui.select(
                        _send_opt["opts"], value=r.get("tag") or None,
                        label="보낼 그룹(태그)",
                        on_change=lambda e, k=i: rows[k].update(tag=e.value or ""),
                    ).props("dense outlined").classes("flex-grow")
                    ui.button(icon="close",
                              on_click=lambda k=i: _del_row(k)) \
                        .props("flat dense color=negative").tooltip("이 줄 지우기") \
                        .set_enabled(len(rows) > 1)

        def _add_row() -> None:
            _send_opt["rows"].append({"lang": "kr", "tag": ""})
            rows_ui.refresh()

        def _del_row(i: int) -> None:
            rows = _send_opt["rows"]
            if 0 <= i < len(rows) and len(rows) > 1:
                rows.pop(i)
            rows_ui.refresh()

        def _load_targets() -> None:
            """계정의 태그(이메일 그룹)를 읽어 줄의 선택지로 쓴다."""
            from app.integrations import mailchimp as MC
            from app.integrations.creds import get_mailchimp_config

            cfg = get_mailchimp_config(user_id)
            if cfg is None or not cfg.usable:
                send_note.set_text("Mailchimp 설정이 없습니다. [설정 → Mailchimp] 를 먼저 채우세요.")
                return
            tags, terr = MC.list_all_tags(cfg)
            if terr:
                send_note.set_text(f"태그 목록을 읽지 못했습니다: {terr}")
                return
            tags = _pick_send_tags(tags)
            opts, tmap = {}, {}
            for t in tags:
                k = f"{t['list_id']}|{t['id']}"
                opts[k] = f"{t['name']} · {t['member_count']}명"
                tmap[k] = t
            _send_opt["opts"], _send_opt["tags"] = opts, tmap
            for r in _send_opt["rows"]:          # 없어진 태그를 고른 줄은 비운다
                if r.get("tag") not in opts:
                    r["tag"] = ""
            send_note.set_text(
                f"태그 {len(tags)}개. 줄을 추가해 본문과 그룹을 따로 고르세요 — "
                "줄마다 캠페인이 따로 만들어집니다.")
            rows_ui.refresh()

        with rows_box:
            rows_ui()
        with ui.row().classes("items-center gap-2"):
            ui.button("줄 추가", icon="add", on_click=_add_row).props("outline size=sm")
            ui.button(icon="refresh", on_click=lambda: _load_targets()) \
                .props("flat dense").tooltip("계정에서 태그 목록 다시 읽기")
        _load_targets()

    with ui.row().classes("gap-2 mt-2"):
        if edit:
            ui.button("미리보기 재렌더", icon="refresh", on_click=do_preview).props("outline")
        # Submission 단계에서는 제작(초안)만 하고 실제 발송은 Approval 에서 한다.
        if allow_send:
            ui.button("Mailchimp 발송", icon="send", on_click=do_send).props("color=negative")
        else:
            ui.label("발송은 Approval 단계에서 진행합니다.").classes("text-caption text-grey-6")

    # 처음 열 때: 현재 저장본으로 미리보기만(저장·알림 없이)
    _show_preview(_render_kr(draft, cards), _render_en(draft, cards))
