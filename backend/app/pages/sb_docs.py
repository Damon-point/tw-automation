"""SB 문서 검토/업로드 화면 — /issues 의 'SB 문서' 탭 본문.

Resolved 된 이슈에서 자동으로 만들어진 Solution Buffer 초안을 사람이 확인하고,
공개 등급을 조정한 뒤 Confluence 에 올릴지 결정하는 곳이다.

이 화면의 원칙
  · **Confluence 쓰기는 여기서 [업로드]를 눌렀을 때만 일어난다.** 주기 작업은 절대
    올리지 않는다. 사내 위키에 자동으로 글이 늘어나면 아무도 통제하지 못한다.
  · 올리기 전에 항상 Confluence 변환 API 로 문법을 검증하고 렌더 결과를 보여 준다.
    storage format 이 어긋나면 저장이 통째로 실패하는데, 눌러 본 뒤에 알면 늦다.
  · 소급(backlog·not_needed)은 감시를 켜던 시점에 한 번 정리하고 끝난 일이라
    화면에 안내나 버튼을 두지 않는다. 상태 값은 이력으로만 남는다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from nicegui import run, ui
from sqlalchemy import func, select

from app.auth import current_username, is_admin
from app.issuetrack import sb
from app.issuetrack.confluence import default_labels_for
from app.issuetrack.client import issue_url
from app.issuetrack.db import issuetrack_session
from app.issuetrack.models import Issue, SBDoc

PAGE_SIZE = 30

#: 상태 → (한글 이름, 색). 화면 배지에 쓴다.
STATE_INFO = {
    "backlog": ("소급 대기", "grey-7"),
    "not_needed": ("소급 불필요", "blue-grey-3"),
    "not_target": ("SB 대상 아님", "blue-grey-3"),
    "queued": ("생성 대기", "amber-8"),
    "draft": ("초안", "blue-7"),
    "notified": ("검토 요청됨", "indigo-6"),
    "approved": ("승인됨", "teal-7"),
    "uploaded": ("업로드 완료", "green-7"),
    "discarded": ("폐기", "blue-grey-4"),
    "error": ("생성 실패", "red-7"),
}
DISCLOSURE_COLOR = {
    "internal": "red-7", "public": "green-7",
    "partner": "blue-7", "ascp": "purple-6",
}
#: SB 스페이스에서 실제로 쓰이는 라벨(600건 조사 기준).
KNOWN_LABELS = ["sb", "confidential", "partner", "ascp", "posted"]

#: 기본으로 보여 줄 상태 — 사람이 할 일이 남은 것들.
ACTIVE_STATES = ["queued", "draft", "notified", "approved", "error"]
#: 상태 칩으로 만들지 않을 상태. 소급 관련은 감시 시작 때 한 번 정리하고 끝난 일이라
#: 걸러 볼 일이 없다(문서 배지에는 그대로 보인다).
_HIDDEN_CHIPS = ("backlog", "not_needed", "not_target")


def open_doc_count() -> int:
    """검토가 필요한 문서 수(탭 배지용). 화면과 같은 범위 — 내 담당 건만 센다."""
    try:
        conds = [SBDoc.superseded.is_(False),
                 SBDoc.state.in_(("draft", "notified", "error"))]
        if not is_admin():
            conds.append(SBDoc.assignee_name == (current_username() or ""))
        with issuetrack_session() as db:
            return db.scalar(select(func.count()).select_from(SBDoc).where(*conds)) or 0
    except Exception:  # noqa: BLE001
        return 0


def _when(dt: datetime | None) -> str:
    return (dt + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M") if dt else "—"


def _query(state: dict) -> tuple[list[dict], int]:
    # 대체된 판(ReOpen 재해결로 밀려난 이전 초안)은 늘 숨긴다 — 최신 판만 본다.
    conds = [SBDoc.superseded.is_(False)]
    # 본인 담당 문서만 — Redmine 담당자 이름이 앱 계정명과 같은 규칙(samuel.seo)이라
    # 그대로 맞춘다. 관리자만 전체를 볼 수 있다.
    # restrict 일 때는 이름이 비어 있어도 조건을 건다. 그래야 계정명을 못 얻는
    # 상황에서 남의 문서가 통째로 보이는 사고가 안 난다(빈 문자열은 아무것과도 안 맞는다).
    if state.get("restrict") or state.get("mine"):
        conds.append(SBDoc.assignee_name == (state.get("mine") or ""))
    if state.get("states"):
        conds.append(SBDoc.state.in_(state["states"]))
    q = (state.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        if q.lstrip("#").isdigit():
            conds.append(SBDoc.issue_id == int(q.lstrip("#")))
        else:
            conds.append(SBDoc.title.ilike(like))

    with issuetrack_session() as db:
        total = db.scalar(select(func.count()).select_from(SBDoc).where(*conds)) or 0
        rows = db.execute(
            select(SBDoc, Issue.subject, Issue.status)
            .join(Issue, Issue.id == SBDoc.issue_id)
            .where(*conds)
            .order_by(SBDoc.source_resolved_at.desc(), SBDoc.id.desc())
            .limit(state["limit"])
        ).all()
        # 세션 밖에서 쓰려고 필요한 값만 뽑아 둔다(지연 로딩 사고 방지).
        out = [{
            "id": d.id, "issue_id": d.issue_id, "revision": d.revision,
            "state": d.state, "origin": d.origin, "superseded": d.superseded,
            "title": d.title, "disclosure": d.disclosure,
            "reason": d.disclosure_reason, "applies_to": d.applies_to,
            "description_html": d.description_html,
            "partners": json.loads(d.partners_json or "[]"),
            "labels": json.loads(d.labels_json or "[]"),
            "assignee": d.assignee_name, "email": d.assignee_email,
            "error": d.error, "url": d.confluence_url,
            "page_id": d.confluence_page_id,
            "resolved_at": d.source_resolved_at, "generated_at": d.generated_at,
            "notified_at": d.notified_at, "uploaded_at": d.uploaded_at,
            "in_tok": d.input_tokens, "out_tok": d.output_tokens,
            "subject": s, "issue_status": st,
        } for d, s, st in rows]
    return out, total


# ------------------------------------------------------------------ 조각 ---
def _badges(d: dict) -> None:
    name, color = STATE_INFO.get(d["state"], (d["state"], "grey-6"))
    ui.badge(name, color=color)
    if d["state"] not in ("backlog", "queued"):
        ui.badge(d["disclosure"], color=DISCLOSURE_COLOR.get(d["disclosure"], "grey-6")) \
            .props("outline")
    if d["revision"] > 1:
        ui.badge(f"{d['revision']}판", color="orange-7").props("outline") \
            .tooltip("이슈가 다시 열렸다가 해결되어 새로 작성된 문서")
    if d["superseded"]:
        ui.badge("대체됨", color="blue-grey-4").props("outline")


def _render_doc(d: dict, refresh) -> None:
    me = current_username() or ""

    with ui.card().classes("w-full q-mb-xs q-pa-none"), \
            ui.expansion().props("dense dense-toggle").classes("w-full") as exp:
        with exp.add_slot("header"), \
                ui.row().classes("items-center gap-2 w-full no-wrap"):
            _badges(d)
            ui.link(f"#{d['issue_id']}", issue_url(d["issue_id"]), new_tab=True) \
                .classes("text-caption text-primary")
            ui.label(d["title"] or f"(초안 미생성) {d['subject']}") \
                .classes("text-weight-medium flex-1 min-w-0 ellipsis")
            if d["assignee"]:
                ui.badge(d["assignee"], color="blue-grey-4").props("outline")

        # 펼칠 때만 그린다. 초안 하나마다 서식 편집기가 하나씩 붙는데, 목록 30건을
        # 미리 만들면 열지도 않은 편집기 30개가 브라우저에 얹힌다.
        holder = ui.column().classes("w-full gap-2 q-px-md q-pb-md")
        loaded = {"v": False}

        def _on_open(e, d=d, holder=holder, loaded=loaded) -> None:
            if not e.value or loaded["v"]:
                return
            loaded["v"] = True
            with holder:
                _render_body(d, me, refresh)

        exp.on_value_change(_on_open)


def _render_body(d: dict, me: str, refresh) -> None:
    ui.label(f"이슈 #{d['issue_id']} [{d['issue_status']}] {d['subject']}") \
        .classes("text-caption text-grey-7")
    ui.label(
        f"해결 {_when(d['resolved_at'])} · 생성 {_when(d['generated_at'])}"
        + (f" · 검토요청 {_when(d['notified_at'])}" if d["notified_at"] else "")
        + (f" · 업로드 {_when(d['uploaded_at'])}" if d["uploaded_at"] else "")
        + (f" · 토큰 {d['in_tok']:,}/{d['out_tok']:,}" if d["in_tok"] else "")
    ).classes("text-caption text-grey-6")

    if d["state"] in ("backlog", "queued"):
        _render_ungenerated(d, refresh)
    elif d["state"] == "error":
        ui.label(d["error"] or "알 수 없는 오류").classes("text-negative text-body2")
        ui.button("다시 생성", icon="refresh",
                  on_click=lambda _=None: _generate(d["id"], refresh)) \
            .props("no-caps color=primary dense")
    elif d["state"] == "uploaded":
        _render_uploaded(d, refresh)
    elif d["state"] == "discarded":
        ui.label(d["error"] or "폐기된 초안입니다.").classes("text-grey-7 text-body2")
        ui.button("되돌리기", icon="undo",
                  on_click=lambda _=None: _reopen(d["id"], refresh)) \
            .props("flat no-caps dense color=grey-8")
    else:
        _render_editor(d, me, refresh)


def _render_ungenerated(d: dict, refresh) -> None:
    with ui.row().classes("items-center gap-2"):
        ui.label("초안이 아직 생성되지 않았습니다. 5분 주기 작업이 곧 만들며, "
                 "지금 만들려면 아래를 누르세요.") \
            .classes("text-caption text-grey-7")
        ui.button("초안 생성", icon="auto_awesome",
                  on_click=lambda _=None: _generate(d["id"], refresh)) \
            .props("no-caps color=primary dense")


def _render_uploaded(d: dict, refresh) -> None:
    with ui.row().classes("items-center gap-2"):
        ui.icon("check_circle").classes("text-green-7")
        ui.link("Confluence 에서 열기", d["url"] or "#", new_tab=True) \
            .classes("text-primary")
        for lb in d["labels"]:
            ui.badge(lb, color="blue-grey-5").props("outline")
    ui.label(f"Applies to: {d['applies_to'] or ''}").classes("text-caption text-grey-7")


def _render_editor(d: dict, me: str, refresh) -> None:
    """draft/notified 상태 — 사람이 고치고 결정하는 영역."""
    form = {
        "title": d["title"] or "",
        "disclosure": d["disclosure"],
        "applies_to": d["applies_to"] or "",
        "description_html": d["description_html"] or "",
        "partners": list(d["partners"]),
        "labels": list(d["labels"]) or [],
    }

    ui.label(f"Claude 판단 근거: {d['reason'] or ''}").classes("text-caption text-grey-7")

    ui.input("제목", value=form["title"],
             on_change=lambda e: form.update(title=e.value or "")) \
        .props("dense outlined").classes("w-full") \
        .tooltip("Troubleshooting 문서는 제목을 '[TR] ' 로 시작하세요")

    with ui.row().classes("w-full items-center gap-2 no-wrap"):
        partner_box = ui.row().classes("items-center gap-2")

        def _on_disclosure(e) -> None:
            form["disclosure"] = e.value
            # 등급이 바뀌면 라벨도 **화면에서 바로** 그 등급 기본값으로 바꾼다.
            # 저장할 때만 고치면, 사용자는 confidential 라벨이 그대로 있는 걸 보면서
            # partner 로 올리게 된다.
            form["labels"] = default_labels_for(e.value)
            label_sel.set_value(list(form["labels"]))
            _draw_partners()

        ui.select({k: f"{k} — {v['ko']}" for k, v in sb.TEMPLATES.items()},
                  value=form["disclosure"], label="공개 등급",
                  on_change=_on_disclosure) \
            .props("dense outlined").classes("min-w-[16rem]")

        def _draw_partners() -> None:
            partner_box.clear()
            if form["disclosure"] != "partner":
                return
            def _toggle(key: str, on: bool) -> None:
                if on and key not in form["partners"]:
                    form["partners"].append(key)
                elif not on and key in form["partners"]:
                    form["partners"].remove(key)

            with partner_box:
                ui.label("공개 파트너:").classes("text-caption text-grey-7")
                for key, label in sb.PARTNER_CHOICES.items():
                    ui.checkbox(
                        label, value=key in form["partners"],
                        on_change=lambda e, k=key: _toggle(k, bool(e.value)),
                    ).props("dense")

        _draw_partners()

    ui.input("Applies to", value=form["applies_to"],
             on_change=lambda e: form.update(applies_to=e.value or "")) \
        .props("dense outlined").classes("w-full")

    label_sel = ui.select(
        KNOWN_LABELS, multiple=True, label="라벨",
        value=form["labels"] or None,
        on_change=lambda e: form.update(labels=list(e.value or []))) \
        .props("dense outlined use-chips").classes("w-full") \
        .tooltip("공개 등급을 바꾸면 그 등급의 기본 라벨로 자동 교체됩니다. "
                 "'posted' 는 외부에 실제 게시한 문서에만 쓰는 라벨입니다.")

    # 본문은 서식 편집기로 쓴다. 사람이 storage format(엄격한 XHTML)을 손으로 맞출
    # 이유가 없다 — 업로드 직전에 Confluence 변환 API 가 정규화해 준다.
    ui.label("Description").classes("text-caption text-grey-7 q-mt-sm")

    editor = ui.editor(
        value=form["description_html"],
        placeholder="배경 → 증상 → 원인 → 해결 순서로 작성",
        on_change=lambda e: form.update(description_html=e.value or ""),
    ).classes("w-full").style("min-height:22rem")
    # QEditor 의 toolbar 는 중첩 배열이라 props 문자열로 못 준다. _props 로 직접 넣는다.
    # 맨 끝 viewsource 는 HTML 원문 보기/편집 — 표처럼 서식 버튼으로 다루기 어려운
    # 것을 손볼 탈출구이자, 붙여넣기가 이상할 때 확인하는 창이다.
    editor._props["toolbar"] = [
        ["bold", "italic", "underline", "strike"],
        ["h2", "h3"],
        ["unordered", "ordered"],
        ["link"],
        ["removeFormat"],
        ["undo", "redo"],
        ["viewsource"],
    ]
    ui.label("서식은 위 버튼으로 넣으세요. 맨 오른쪽 </> 을 누르면 HTML 원문을 "
             "직접 고칠 수 있습니다. XHTML 문법은 안 맞아도 됩니다 — "
             "업로드할 때 Confluence 가 정규화합니다.") \
        .classes("text-caption text-grey-6")

    preview_box = ui.column().classes("w-full")

    async def _save(*, notify_ok: bool = True) -> bool:
        res = await run.io_bound(
            lambda: sb.update_draft(
                d["id"], title=form["title"], disclosure=form["disclosure"],
                applies_to=form["applies_to"],
                description_html=form["description_html"],
                partners=form["partners"],
                labels=form["labels"] or None))
        if not res.get("ok"):
            ui.notify(res.get("error", "저장 실패"), color="negative")
            return False
        if notify_ok:
            ui.notify("저장했습니다.", color="positive")
        return True

    async def _preview() -> None:
        if not await _save(notify_ok=False):
            return
        from app.issuetrack import confluence as C

        n = ui.notification("Confluence 문법 검증 중…", spinner=True, timeout=None)
        res = await run.io_bound(C.preview, d["id"])
        n.dismiss()
        preview_box.clear()
        with preview_box:
            if not res.get("ok"):
                ui.label(f"검증 실패: {res.get('error')}").classes("text-negative")
                return
            with ui.row().classes("items-center gap-2"):
                ui.icon("check_circle").classes("text-green-7")
                ui.label(f"문법 정상 · 제목 '{res['title']}'").classes("text-caption")
                for lb in res["labels"]:
                    ui.badge(lb, color="blue-grey-5").props("outline")
            if res.get("exists"):
                ui.label(
                    f"⚠ 같은 제목의 페이지가 이미 있습니다 (id {res['exists']['id']}). "
                    "제목을 바꾸지 않으면 업로드가 거부됩니다."
                ).classes("text-warning text-caption")
            ui.label("아래는 Confluence 가 실제로 렌더한 결과입니다. "
                     "creation date 는 페이지가 만들어진 뒤에 채워지므로 "
                     "여기서는 매크로 원문($content.getCreationDate())으로 보입니다.") \
                .classes("text-caption text-grey-6")
            with ui.card().classes("w-full q-pa-sm bg-grey-1"):
                ui.html(res["html"])

    async def _do_notify() -> None:
        if not await _save(notify_ok=False):
            return
        to = d["email"] or "(관리자)"
        res = await run.io_bound(sb.notify, d["id"])
        if res.get("sent"):
            ui.notify(f"검토 요청 메일을 보냈습니다 → {', '.join(res['to'])}",
                      color="positive")
            refresh()
        else:
            ui.notify(f"발송 실패: {res.get('error')} (수신 예정: {to})",
                      color="negative", multi_line=True)

    async def _do_upload() -> None:
        if not await _save(notify_ok=False):
            return
        from app.issuetrack import confluence as C

        n = ui.notification("Confluence 에 올리는 중…", spinner=True, timeout=None)
        res = await run.io_bound(lambda: C.publish(d["id"], by=me))
        n.dismiss()
        if res.get("ok"):
            ui.notify(
                f"업로드 완료 ({'갱신' if res['action'] == 'updated' else '신규'}) — "
                f"{res['url']}", color="positive", multi_line=True)
            refresh()
        else:
            ui.notify(f"업로드 실패: {res.get('error')}",
                      color="negative", multi_line=True)

    def _confirm_upload() -> None:
        """사내 위키에 실제로 글이 올라가는 동작이라 한 번 더 묻는다."""
        with ui.dialog() as dlg, ui.card():
            ui.label("Confluence 에 업로드할까요?").classes("text-subtitle1")
            ui.label(f"스페이스 SB · 제목 '{form['title'][:70]}'") \
                .classes("text-caption text-grey-7")
            ui.label(f"공개 등급 {form['disclosure']} — 되돌리려면 Confluence 에서 "
                     "직접 지워야 합니다.").classes("text-caption text-warning")
            with ui.row().classes("justify-end w-full"):
                ui.button("취소", on_click=dlg.close).props("flat no-caps")
                ui.button("업로드", on_click=lambda: (dlg.close(), _do_upload())) \
                    .props("color=primary no-caps")
        dlg.open()

    with ui.row().classes("items-center gap-2 q-mt-sm wrap"):
        ui.button("저장", icon="save", on_click=_save).props("no-caps dense flat")
        ui.button("미리보기·검증", icon="preview", on_click=_preview) \
            .props("no-caps dense flat color=primary")
        ui.button("검토 요청 메일", icon="mail", on_click=_do_notify) \
            .props("no-caps dense flat color=indigo") \
            .tooltip(f"수신: {d['email'] or '담당자 주소를 못 만들어 관리자에게 발송'}")
        ui.button("업로드", icon="cloud_upload", on_click=_confirm_upload) \
            .props("no-caps dense color=primary")
        ui.space()
        ui.button("폐기", icon="delete_outline",
                  on_click=lambda: _discard(d["id"], me, refresh)) \
            .props("no-caps dense flat color=grey-7")


# ------------------------------------------------------------------ 동작 ---
async def _generate(doc_id: int, refresh) -> None:
    n = ui.notification("Claude 로 초안을 만드는 중… (30초 안팎)",
                        spinner=True, timeout=None)
    res = await run.io_bound(sb.generate, doc_id)
    n.dismiss()
    if res.get("ok"):
        ui.notify(f"초안 생성 완료 — {res['title'][:50]}", color="positive")
    else:
        ui.notify(f"생성 실패: {res.get('error')}", color="negative", multi_line=True)
    refresh()


async def _discard(doc_id: int, me: str, refresh) -> None:
    res = await run.io_bound(lambda: sb.discard(doc_id, by=me))
    ui.notify("폐기했습니다." if res.get("ok") else res.get("error", "실패"),
              color="info" if res.get("ok") else "negative")
    refresh()


async def _reopen(doc_id: int, refresh) -> None:
    res = await run.io_bound(sb.reopen_draft, doc_id)
    ui.notify("되돌렸습니다." if res.get("ok") else res.get("error", "실패"),
              color="info" if res.get("ok") else "negative")
    refresh()


# ------------------------------------------------------------------ 본문 ---
def render_sb_docs():
    """Solution Buffer 탭 본문. 반환: 이 패널을 다시 그리는 함수.

    **내가 담당인 문서만 보인다.** SB 문서는 Redmine 담당자가 검토·승인해야 하는
    것이라, 남의 담당 건까지 늘어놓으면 내 할 일이 묻힌다. 관리자는 전체를 볼 수 있다.
    """
    me = current_username() or ""
    admin = is_admin()
    state: dict = {"q": "", "states": list(ACTIVE_STATES), "limit": PAGE_SIZE,
                   "mine": "" if admin else me, "restrict": not admin}

    def _reset(**kw) -> None:
        state.update(kw)
        state["limit"] = PAGE_SIZE
        body.refresh()

    # 상태를 고르는 칸은 두지 않는다 — 아래 상태 칩으로 좁힌다.
    # 대체된 판(ReOpen 재해결로 밀려난 이전 초안)은 최신 판만 보면 되므로 늘 숨긴다.
    with ui.row().classes("w-full items-center no-wrap gap-2 q-mt-sm"):
        ui.input(placeholder="제목·이슈번호 검색",
                 on_change=lambda e: _reset(q=e.value or "")) \
            .props("dense outlined clearable debounce=300").classes("flex-1 min-w-0")
        if admin:
            # 관리자 계정은 Redmine 담당자가 아닌 경우가 많아, 본인 것만 걸면 늘 0건이다.
            ui.checkbox("내 담당만",
                        on_change=lambda e: _reset(mine=me if e.value else "")) \
                .props("dense").tooltip(f"담당자가 '{me}' 인 문서만 보기")

    if not admin and me:
        ui.label(f"담당자가 '{me}' 인 문서만 표시됩니다.") \
            .classes("text-caption text-grey-6")

    @ui.refreshable
    def body() -> None:
        s = sb.summary(state["mine"])
        # 상태 칩이 유일한 상태 필터다. 건수가 0 인 상태는 눌러 봐야 빈 화면이라 숨긴다.
        # 소급 관련 상태(backlog·not_needed)는 걸러 낼 일이 없어 칩으로 두지 않는다.
        with ui.row().classes("w-full items-center gap-1 wrap q-mt-xs"):
            for key, (label, color) in STATE_INFO.items():
                n = s["counts"].get(key, 0)
                if not n or key in _HIDDEN_CHIPS:
                    continue
                ui.chip(f"{label} {n}", color=color,
                        on_click=lambda _=None, k=key: _reset(states=[k])) \
                    .props("dense text-color=white clickable")
            ui.chip("전체", color="grey-8", on_click=lambda: _reset(states=[])) \
                .props("dense text-color=white clickable")
            ui.space()
            ui.label(f"누적 사용 ${s['spent_usd']:.2f}").classes("text-caption text-grey-6")

        rows, total = _query(state)
        ui.label(f"{total:,}건 중 {len(rows):,}건 표시") \
            .classes("text-caption text-grey-7 q-mt-sm")
        if not rows:
            with ui.card().classes("w-full items-center q-pa-lg"):
                ui.icon("description", size="42px").classes("text-grey-5")
                ui.label("조건에 맞는 문서가 없습니다.").classes("text-grey-7")
                if state["mine"]:
                    ui.label(f"담당자 '{state['mine']}' 기준입니다.") \
                        .classes("text-caption text-grey-6")
            return

        with ui.column().classes("w-full gap-0"):
            for d in rows:
                _render_doc(d, body.refresh)

        if len(rows) < total:
            def _more() -> None:
                state["limit"] += PAGE_SIZE
                body.refresh()

            ui.button(f"더 보기 (남은 {total - len(rows):,}건)", on_click=_more) \
                .props("flat no-caps color=primary").classes("w-full")

    body()
    return body.refresh
