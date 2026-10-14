"""/issues — Solution Buffer (Redmine salesissue 감시 → SB 문서화).

메일함(/mail)과 같은 감각으로 쓴다. 한 줄이 이슈 하나, 펼치면 그 이슈에서 일어난
**모든 이벤트**가 시간순 타임라인으로 나온다.

Redmine 웹에서 직접 보는 것과 다른 점:
  · 상태·담당자별로 한 화면에서 걸러 본다(프로젝트 1,600여 건을 한 번에 훑는다).
  · 첫 반응까지 걸린 시간, Resolved 까지 걸린 시간, 재오픈 횟수를 이슈마다 계산해 준다.
    Redmine 의 closed_on 은 결측이 많아 못 쓰고, journal 에서 유도한다(events.py 참고).

목록 질의는 이슈 테이블만 보고(타임라인은 펼칠 때 lazy 로드) 페이지당 50건씩 끊는다.
1,600여 건을 한 번에 그리면 브라우저가 버틴다 해도 사람이 못 읽는다.
"""
from __future__ import annotations

from datetime import datetime

from nicegui import run, ui
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.issuetrack import markup
from app.issuetrack import sync as it_sync
from app.issuetrack.client import issue_url
from app.issuetrack.events import Naming, Timings, timeline, timings
from app.issuetrack.db import issuetrack_session
from app.issuetrack.models import Issue
from app.theme import apply_theme, page_header

PAGE_SIZE = 50

# 화면이 서버 폴링(5분)을 따라잡는 간격. 검사는 count/max(id) 질의뿐이라 싸다.
_AUTO_REFRESH_SEC = 30.0

#: Redmine salesissue 에서 실제로 쓰이는 8개 상태의 색.
STATUS_COLOR = {
    "open": "red-6",
    "reopen": "deep-orange-7",
    "inprogress": "blue-7",
    "monitoring": "purple-5",
    "holding": "grey-7",
    "resolved": "green-7",
    "closed": "blue-grey-5",
    "rejected": "brown-5",
}
#: 아직 손이 필요한 상태 — 기본 필터이자 배지 계산 기준.

#: Textile 이 만든 표·목록·코드에는 브라우저 기본 스타일이 거의 없다.
#: Quasar 도 .q-* 밖의 요소는 건드리지 않으므로 여기서 최소한만 입힌다.
_NOTE_CSS = """
.rm-note { word-break: break-word; }
.rm-note p { margin: 0 0 .5em; }
.rm-note ul, .rm-note ol { margin: .25em 0 .5em; padding-left: 1.4em; }
.rm-note ul { list-style: disc; }
.rm-note ul ul { list-style: circle; }
.rm-note ul ul ul { list-style: square; }
.rm-note ol { list-style: decimal; }
.rm-note li { margin: .1em 0; }
.rm-note h1, .rm-note h2, .rm-note h3,
.rm-note h4, .rm-note h5, .rm-note h6 {
  font-size: 1.05em; font-weight: 600; margin: .7em 0 .3em; line-height: 1.3;
}
.rm-note table {
  border-collapse: collapse; margin: .4em 0; font-size: .95em;
  display: block; overflow-x: auto; max-width: 100%;
}
.rm-note th, .rm-note td { border: 1px solid #d0d0d0; padding: 3px 8px; }
.rm-note th { background: #f2f4f3; font-weight: 600; text-align: left; }
.rm-note pre {
  background: #f5f5f5; border: 1px solid #e0e0e0; border-radius: 3px;
  padding: 6px 8px; margin: .4em 0; overflow-x: auto;
  white-space: pre-wrap; word-break: break-all; font-size: .9em;
}
.rm-note code { background: #f0f0f0; padding: 0 3px; border-radius: 3px; }
.rm-note pre code { background: none; padding: 0; }
.rm-note blockquote {
  border-left: 3px solid #ccc; margin: .4em 0; padding-left: .8em; color: #555;
}
.rm-note a { color: #1976d2; }
"""

_EVENT_ICON = {
    "created": ("add_circle", "primary"),
    "status": ("flag", "green-7"),
    "comment": ("chat_bubble", "blue-grey-6"),
    "attachment": ("attach_file", "deep-purple-5"),
    "field": ("edit", "grey-6"),
}


def _color(status: str | None) -> str:
    return STATUS_COLOR.get((status or "").lower().replace(" ", ""), "grey-6")


def _when(dt: datetime | None) -> str:
    """Redmine 은 UTC 로 주므로 KST(+9)로 바꿔 보여준다."""
    if dt is None:
        return ""
    from datetime import timedelta

    return (dt + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")


def _ago(dt: datetime | None) -> str:
    if dt is None:
        return ""
    sec = (datetime.utcnow() - dt).total_seconds()
    if sec < 3600:
        return f"{int(sec // 60)}분 전"
    if sec < 86400:
        return f"{int(sec // 3600)}시간 전"
    d = int(sec // 86400)
    return f"{d}일 전" if d < 30 else f"{d // 30}개월 전"


def _dur(days: float | None) -> str:
    if days is None:
        return "—"
    if days < 1:
        return f"{days * 24:.1f}시간"
    return f"{days:.1f}일"


# ------------------------------------------------------------------ 질의 ---
def status_counts() -> dict[str, int]:
    with issuetrack_session() as db:
        return {
            s or "(없음)": n for s, n in db.execute(
                select(Issue.status, func.count())
                .where(Issue.excluded.is_(False))
                .group_by(Issue.status)).all()
        }


def _query_issues(state: dict) -> tuple[list[Issue], int]:
    """필터에 맞는 이슈 (한 페이지, 전체 건수)."""
    q = (state.get("q") or "").strip()

    # 이슈 번호를 그대로 친 경우엔 다른 필터를 전부 무시한다. 번호를 아는 사람은
    # 그 이슈를 보러 온 것이지, 상태 필터에 걸려 '0건'을 보려던 게 아니다.
    if q.lstrip("#").isdigit() and len(q.lstrip("#")) >= 4:
        with issuetrack_session() as db:
            row = db.get(Issue, int(q.lstrip("#")))
        return ([row], 1) if row else ([], 0)

    conds = [Issue.excluded.is_(False)]
    if state.get("statuses"):
        conds.append(Issue.status.in_(state["statuses"]))
    if q:
        like = f"%{q}%"
        conds.append(or_(Issue.subject.ilike(like),
                         Issue.assigned_to_name.ilike(like),
                         Issue.author_name.ilike(like)))

    with issuetrack_session() as db:
        total = db.scalar(select(func.count()).select_from(Issue).where(*conds)) or 0
        rows = db.execute(
            select(Issue).where(*conds)
            .order_by(Issue.updated_on.desc())
            .limit(state["limit"])
        ).scalars().all()
    return list(rows), total


def _load_timeline(issue_id: int):
    """펼칠 때만 호출 — 타임라인과 지표를 함께 계산한다."""
    with issuetrack_session() as db:
        issue = db.execute(
            select(Issue).options(selectinload(Issue.journals))
            .where(Issue.id == issue_id)
        ).scalars().first()
        if issue is None:
            return [], Timings()
        naming = Naming()
        return timeline(issue, naming), timings(issue, naming)


# ------------------------------------------------------------------ 그리기 ---
def _render_event(ev) -> None:
    icon, color = _EVENT_ICON.get(ev.kind, ("circle", "grey-6"))
    with ui.row().classes("w-full no-wrap items-start gap-2 q-py-xs"):
        ui.icon(icon).classes(f"text-{color} q-mt-xs").style("font-size:16px")
        with ui.column().classes("gap-0 flex-1 min-w-0"):
            with ui.row().classes("items-center gap-2 no-wrap"):
                ui.label(_when(ev.at)).classes("text-caption text-grey-6")
                ui.label(ev.who).classes("text-caption text-weight-medium")
                if ev.is_creation:
                    ui.badge("이슈 생성", color="primary").props("outline")
                elif ev.new_status:
                    ui.badge(ev.new_status, color=_color(ev.new_status))
            if ev.summary:
                ui.label(ev.summary).classes("text-caption text-grey-8")
            if ev.note:
                # Redmine 본문은 Textile 이다. 원문 그대로 두면 '*', '***',
                # '|_. 항목 |' 같은 기호가 날것으로 보여 읽을 수가 없다.
                # 아주 긴 글은 안에서 스크롤시킨다.
                ui.html(markup.render(ev.note)).classes("rm-note text-body2") \
                    .style("max-height:26rem; overflow-y:auto")


def _render_issue(row: Issue) -> None:
    # dense: 1,600여 건을 훑는 화면이라 한 줄이 낮아야 한 화면에 많이 들어온다.
    with ui.card().classes("w-full q-mb-xs q-pa-none"), \
            ui.expansion().props("dense dense-toggle").classes("w-full") as exp:
        with exp.add_slot("header"), \
                ui.row().classes("items-center gap-2 w-full no-wrap"):
            ui.badge(row.status or "?", color=_color(row.status))
            ui.link(f"#{row.id}", issue_url(row.id), new_tab=True) \
                .classes("text-caption text-primary").tooltip("Redmine 에서 열기")
            ui.label(row.subject or "(제목 없음)").classes(
                "text-weight-medium flex-1 min-w-0 ellipsis")
            if row.is_test:
                ui.badge("Test", color="amber-8").props("outline") \
                    .tooltip("기능 검증용 이슈 — 수집·SB 생성·이력은 그대로 하고 "
                             "통계에서만 뺍니다")
            if row.excluded:
                ui.badge("제외", color="grey-6").props("outline") \
                    .tooltip(row.excluded_reason or "")
            if row.assigned_to_name:
                ui.badge(row.assigned_to_name, color="blue-grey-4").props("outline")
            ui.label(_ago(row.updated_on)).classes("text-caption text-grey-6") \
                .tooltip(f"최종 갱신 {_when(row.updated_on)}")

        holder = ui.column().classes("w-full gap-0 q-px-md q-pb-md")
        loaded = {"v": False}

        async def _on_open(e, iid=row.id, holder=holder, loaded=loaded) -> None:
            # 이슈 하나에 이벤트가 100건 넘는 것도 있어, 펼칠 때만 읽는다.
            if not e.value or loaded["v"]:
                return
            loaded["v"] = True
            with holder:
                spin = ui.spinner(size="sm")
            events, t = await run.io_bound(_load_timeline, iid)
            spin.delete()
            with holder:
                _render_metrics(t)
                ui.separator().classes("q-my-sm")
                for ev in events:
                    _render_event(ev)

        exp.on_value_change(_on_open)


def _render_metrics(t: Timings) -> None:
    with ui.row().classes("items-center gap-4 wrap q-mt-sm"):
        # 통계 탭과 같은 자로 잰다 — 주말·한국 공휴일을 뺀 업무일.
        from app.issuetrack.events import ACTION_KINDS

        kind = ACTION_KINDS.get(t.first_action_kind, "")
        for label, value, tip in (
            ("첫 반응", _dur(t.first_action_workdays),
             f"작성자 외 사람이 처음 손댄 시점까지 (업무일)"
             + (f" — {kind}" if kind else "")),
            ("Lead time", _dur(t.lead_workdays),
             "생성 → Resolved 최초 시점, 주말·한국 공휴일을 뺀 업무일 "
             "(closed_on 이 아니라 이력에서 계산)"),
            ("재오픈", f"{t.reopened}회", "Resolved/Closed 이후 다시 열린 횟수"),
            ("참여자", f"{len(t.participants)}명", ", ".join(t.participants)),
            ("코멘트", f"{t.comment_count}건", ""),
        ):
            with ui.column().classes("gap-0"):
                ui.label(label).classes("text-caption text-grey-6")
                lb = ui.label(value).classes("text-body2 text-weight-medium")
                if tip:
                    lb.tooltip(tip)


def render_issue_list():
    """이슈 목록 탭 본문. 반환: 이 패널을 다시 그리는 함수."""
    # 이 탭은 이력을 들여다보는 곳이라 기본이 '전체'다. 상태·담당자를 고르는 칸은
    # 두지 않는다 — 담당자·작성자·제목은 검색으로 찾고, 상태는 아래 칩으로 좁힌다.
    # 제외 이슈(Janam 등)는 이 서비스에서 추적 대상이 아니라 아예 보여 주지 않는다.
    state: dict = {"q": "", "statuses": [], "limit": PAGE_SIZE}

    def _reset_and_refresh(**kw) -> None:
        state.update(kw)
        state["limit"] = PAGE_SIZE        # 필터가 바뀌면 첫 페이지부터
        body.refresh()

    ui.input(
        placeholder="제목·담당자·작성자·이슈번호 검색",
        on_change=lambda e: _reset_and_refresh(q=e.value or ""),
    ).props("dense outlined clearable debounce=300").classes("w-full q-mt-sm")

    # 상태별 건수 — 누르면 그 상태만 본다.
    chips = ui.row().classes("w-full items-center gap-1 wrap q-mt-xs")

    @ui.refreshable
    def body() -> None:
        chips.clear()
        with chips:
            counts = status_counts()
            for s, n in sorted(counts.items(), key=lambda x: -x[1]):
                ui.chip(
                    f"{s} {n}", color=_color(s),
                    on_click=lambda _=None, ss=s: _reset_and_refresh(statuses=[ss]),
                ).props("dense text-color=white clickable")
            ui.chip("전체", color="grey-7",
                    on_click=lambda: _reset_and_refresh(statuses=[])) \
                .props("dense text-color=white clickable")

        try:
            rows, total = _query_issues(state)
        except Exception as e:  # noqa: BLE001
            ui.label(f"이슈를 읽지 못했습니다: {e}").classes("text-negative")
            return

        st = it_sync.status()
        ui.label(f"{total:,}건 중 {len(rows):,}건 표시").classes(
            "text-caption text-grey-7 q-mt-sm")
        if st.get("last_error"):
            ui.label(f"수집 오류: {st['last_error']}").classes("text-caption text-negative")
        if st.get("backfill") != "done":
            ui.label("최초 전수 수집이 아직 진행 중입니다. 잠시 후 다시 확인하세요.") \
                .classes("text-caption text-warning")

        if not rows:
            with ui.card().classes("w-full items-center q-pa-lg"):
                ui.icon("inbox", size="42px").classes("text-grey-5")
                ui.label("조건에 맞는 이슈가 없습니다.").classes("text-grey-7")
            return

        # gap-0: 카드마다 q-mb-sm 을 이미 주므로 바깥 열의 기본 간격까지 겹치면
        # 한 화면에 몇 건 안 들어온다.
        with ui.column().classes("w-full gap-0"):
            for r in rows:
                _render_issue(r)

        if len(rows) < total:
            def _more() -> None:
                state["limit"] += PAGE_SIZE
                body.refresh()

            ui.button(f"더 보기 (남은 {total - len(rows):,}건)", on_click=_more) \
                .props("flat no-caps color=primary").classes("w-full")

    body()
    return body.refresh


# -------------------------------------------------------------------- 페이지 ---
def _tab_with_badge(label: str, icon: str, count: int):
    """탭 + 미처리 건수 배지. 배지는 항상 만들고 0 이면 숨긴다(나중에 갱신하려고)."""
    with ui.tab(label, icon=icon) as t:
        b = ui.badge(str(count), color="red-6").props("floating rounded")
        b.set_visibility(bool(count))
    return t, b


@ui.page("/issues")
def issues_page():
    from app.pages.issue_stats import render_issue_stats
    from app.pages.sb_docs import open_doc_count, render_sb_docs

    apply_theme()
    ui.add_css(_NOTE_CSS)
    panels: dict = {}

    with page_header("Solution Buffer", max_w="max-w-[88rem]"):
        with ui.row().classes("w-full items-center no-wrap gap-2"):
            # Solution Buffer 가 먼저다 — 실제로 손이 가는 일이 여기 있고,
            # 이슈 탭은 그 근거가 되는 이력을 들여다보는 곳이다.
            with ui.tabs().props("shrink no-caps") as tabs:
                t_sb, b_sb = _tab_with_badge(
                    "Solution Buffer", "description", open_doc_count())
                # 이슈 탭은 이력을 보는 곳이라 '처리할 건수' 개념이 없다 → 배지 없음.
                t_issue = ui.tab("이슈", icon="bug_report")
                t_stat = ui.tab("통계", icon="insights")
            ui.space()
            sync_lbl = ui.label("").classes("text-caption text-grey-6")
            sync_btn = ui.button("동기화", icon="sync").props("color=primary no-caps dense")
            sync_btn.tooltip("Redmine 에서 새 이벤트를 지금 가져온다 (평소 5분마다 자동)")

        with ui.tab_panels(tabs, value=t_sb).classes("w-full"):
            with ui.tab_panel(t_sb).classes("q-pa-none"):
                panels["sb"] = render_sb_docs()
            with ui.tab_panel(t_issue).classes("q-pa-none"):
                panels["issue"] = render_issue_list()
            with ui.tab_panel(t_stat).classes("q-pa-none"):
                panels["stats"] = render_issue_stats()

        def refresh_header() -> None:
            """탭과 무관한 머리말 — 배지와 마지막 수집 시각."""
            n = open_doc_count()
            b_sb.set_text(str(n))
            b_sb.set_visibility(bool(n))
            last = _iso(it_sync.status().get("last_poll"))
            sync_lbl.set_text(f"마지막 수집 {_ago(last)}" if last else "")

        def refresh_all() -> None:
            for fn in panels.values():
                if fn is not None:
                    fn()
            refresh_header()

        refresh_header()

        async def on_sync() -> None:
            sync_btn.disable()
            n = ui.notification("Redmine 에서 가져오는 중…", spinner=True, timeout=None)
            try:
                res = await run.io_bound(it_sync.poll)
            except Exception as e:  # noqa: BLE001
                n.dismiss()
                ui.notify(f"동기화 오류: {e}", color="negative")
                return
            finally:
                sync_btn.enable()
            n.dismiss()
            if res.get("error"):
                ui.notify(f"동기화 실패: {res['error']}", color="negative", multi_line=True)
            elif res.get("detail_done"):
                ui.notify(
                    f"이슈 {res['detail_done']}건 갱신 (이벤트 {res.get('journals', 0)}건)",
                    color="positive")
            else:
                ui.notify("새 이벤트가 없습니다.", color="info")
            refresh_all()

        sync_btn.on_click(on_sync)

        # 백그라운드 폴링(5분)이 새 이벤트를 넣으면 화면도 따라 갱신한다.
        # 매번 다시 그리면 검색어·펼친 이슈가 튀므로 **데이터가 바뀐 경우에만** 그린다.
        ver = {"v": it_sync.data_version()}

        async def auto_refresh() -> None:
            cur = await run.io_bound(it_sync.data_version)
            if cur == ver["v"] or cur == (-1, -1, -1):
                return
            ver["v"] = cur
            refresh_all()

        ui.timer(_AUTO_REFRESH_SEC, auto_refresh)


def _iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None
