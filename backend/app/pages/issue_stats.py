"""이슈 통계 대시보드 — /issues 의 '통계' 탭 본문.

여기 숫자는 전부 **journal 이력에서 유도한 값**이다. Redmine 의 closed_on 은
Resolved 이슈 1,139건 중 531건에만 있고 일부는 생성일보다 과거라, 그걸로 계산하면
'해결까지 걸린 시간'이 절반 넘게 사라지거나 음수가 된다(app/issuetrack/stats.py 참고).

소요시간은 **업무일**이다 — 주말과 한국 공휴일을 뺀다. 대푯값은 **평균**이고, 평균만
보면 오래 끈 몇 건에 끌려가므로 작은 글씨로 중앙값을 함께 적는다.

집계는 이슈 1,600여 건 + 이벤트 14,000여 건을 매번 다 훑는데 1초라, 캐시 없이
탭을 열 때마다 새로 센다. 대신 UI 를 막지 않도록 스레드로 넘긴다.
"""
from __future__ import annotations

from nicegui import background_tasks, run, ui

from app.issuetrack import stats as S
from app.issuetrack.client import issue_url

#: 앱 테마의 초록 계열 + 대비색. echart 는 색을 직접 줘야 톤이 맞는다.
C_CREATED = "#c0705a"
C_RESOLVED = "#4A7C67"
C_BAR = "#3b6ea5"

STATUS_COLOR = {
    "Open": "#e05252", "ReOpen": "#e07a2c", "InProgress": "#3b6ea5",
    "Monitoring": "#8e5fbf", "Holding": "#8a8a8a", "Resolved": "#4A7C67",
    "Closed": "#6b7f8c", "Rejected": "#8a6b5a",
}

#: 첫 반응 유형 색. 무거운 행동일수록 진하게.
ACTION_COLOR = {
    "resolve": "#4A7C67", "analyze": "#3b6ea5", "status": "#8e5fbf",
    "comment": "#c0705a", "assign": "#d0a24c", "attach": "#7a9a8b",
    "field": "#8a8a8a", "empty": "#c4c4c4",
}

#: 쌓은 막대의 색 — 12개까지는 서로 구분된다. 넘으면 앞에서부터 다시 쓴다.
STACK_COLORS = [
    "#4A7C67", "#3b6ea5", "#c0705a", "#8e5fbf", "#d0a24c", "#6b9bd1",
    "#a4643f", "#5f9e86", "#b4577f", "#7a8b5a", "#9a6bbf", "#c98a5a",
]
ETC_COLOR = "#c4c4c4"


def _dur(days: float | None) -> str:
    """업무일을 사람이 읽는 형태로. 하루 미만은 시간으로."""
    if days is None:
        return "—"
    return f"{days * 24:.1f}시간" if days < 1 else f"{days:.1f}일"


def _kpi(title: str, value: str, sub: str, color: str, tip: str = "") -> None:
    with ui.card().classes("items-start").style(
            f"min-width:170px; border-left:4px solid {color}"):
        ui.label(title).classes("text-caption text-grey-7")
        ui.label(value).classes("text-h5")
        lb = ui.label(sub).classes("text-caption text-grey-6")
        if tip:
            lb.tooltip(tip)


def _monthly_chart(m: dict) -> None:
    ui.echart({
        "title": {"text": "월별 생성 · 해결", "textStyle": {"fontSize": 13}},
        "tooltip": {"trigger": "axis"},
        "legend": {"data": ["생성", "해결"], "right": 10, "top": 4},
        "grid": {"left": 45, "right": 20, "top": 45, "bottom": 60},
        "xAxis": {"type": "category", "data": m["labels"],
                  "axisLabel": {"rotate": 45, "fontSize": 10}},
        "yAxis": {"type": "value"},
        "series": [
            {"name": "생성", "type": "bar", "data": m["created"],
             "itemStyle": {"color": C_CREATED}},
            {"name": "해결", "type": "bar", "data": m["resolved"],
             "itemStyle": {"color": C_RESOLVED}},
        ],
    }).classes("w-full").style("height: 320px")


def _hist_chart(h: dict, title: str, color: str) -> None:
    ui.echart({
        "title": {"text": f"{title} (n={h['n']:,})", "textStyle": {"fontSize": 13}},
        "tooltip": {"trigger": "axis"},
        "grid": {"left": 45, "right": 20, "top": 45, "bottom": 55},
        "xAxis": {"type": "category", "data": h["labels"],
                  "axisLabel": {"rotate": 30, "fontSize": 10}},
        "yAxis": {"type": "value"},
        "series": [{"type": "bar", "data": h["counts"],
                    "itemStyle": {"color": color}}],
    }).classes("w-full").style("height: 280px")


def _status_chart(rows: list[dict]) -> None:
    ui.echart({
        # 제목을 왼쪽에 붙여야 도넛 바깥 라벨과 겹치지 않는다.
        "title": {"text": "상태 분포", "left": "left", "textStyle": {"fontSize": 13}},
        "tooltip": {"trigger": "item", "formatter": "{b}: {c}건 ({d}%)"},
        "legend": {"bottom": 0, "type": "scroll", "textStyle": {"fontSize": 10}},
        "series": [{
            "type": "pie", "radius": ["38%", "60%"], "center": ["50%", "54%"],
            "avoidLabelOverlap": True,
            "label": {"fontSize": 11},
            "data": [{"name": r["name"], "value": r["value"],
                      "itemStyle": {"color": STATUS_COLOR.get(r["name"], "#999")}}
                     for r in rows],
        }],
    }).classes("w-full").style("height: 320px")


def _tag_chart(rows: list[dict]) -> None:
    rows = list(reversed(rows))         # 가로 막대는 아래에서 위로 그려진다
    ui.echart({
        "title": {"text": "제목 태그 분포", "textStyle": {"fontSize": 13}},
        "tooltip": {"trigger": "axis"},
        "grid": {"left": 110, "right": 30, "top": 40, "bottom": 30},
        "xAxis": {"type": "value"},
        "yAxis": {"type": "category", "data": [r["name"] for r in rows],
                  "axisLabel": {"fontSize": 10}},
        "series": [{"type": "bar", "data": [r["value"] for r in rows],
                    "itemStyle": {"color": C_BAR}}],
    }).classes("w-full").style("height: 420px")


# ------------------------------------------------------- [Issue] 월별 구성 ---
def _composition_chart(comp: S.Composition, basis: str) -> None:
    series = []
    for i, g in enumerate(comp.groups):
        color = ETC_COLOR if g == "기타" else STACK_COLORS[i % len(STACK_COLORS)]
        series.append({
            "name": g, "type": "bar", "stack": "all",
            "data": comp.series[g],
            "itemStyle": {"color": color},
            "emphasis": {"focus": "series"},
        })
    ui.echart({
        "title": {"text": f"[Issue] 월별 구성 — {S.GROUP_BASES[basis]} "
                          f"(n={comp.n:,})", "textStyle": {"fontSize": 13}},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"},
                    "order": "valueDesc"},
        "legend": {"type": "scroll", "top": 24, "textStyle": {"fontSize": 10}},
        "grid": {"left": 45, "right": 20, "top": 66, "bottom": 60},
        "xAxis": {"type": "category", "data": comp.labels,
                  "axisLabel": {"rotate": 45, "fontSize": 10}},
        "yAxis": {"type": "value"},
        "series": series,
    }).classes("w-full").style("height: 400px")


def _composition_table(comp: S.Composition) -> None:
    """묶음별 건수와 Lead time. '기타'에 무엇이 들어갔는지도 여기서 편다."""
    rows = []
    for g, n in comp.totals:
        mem = comp.members.get(g, [])
        lead = [m.lead_days for m in mem if m.lead_days is not None and m.lead_days >= 0]
        open_n = sum(1 for m in mem if m.is_open)
        inside = ""
        if g == "기타":
            from collections import Counter

            c = Counter(m.target for m in mem)
            inside = ", ".join(f"{k}({v})" for k, v in c.most_common(12))
            if len(c) > 12:
                inside += f" 외 {len(c) - 12}종"
        rows.append({
            "name": g, "total": n, "open": open_n,
            "lead": round(sum(lead) / len(lead), 1) if lead else "—",
            "inside": inside,
        })
    ui.table(
        columns=[
            {"name": "name", "label": "묶음", "field": "name", "align": "left",
             "sortable": True},
            {"name": "total", "label": "건수", "field": "total", "sortable": True},
            {"name": "open", "label": "미해결", "field": "open", "sortable": True},
            {"name": "lead", "label": "Lead time 평균(업무일)", "field": "lead",
             "sortable": True},
            {"name": "inside", "label": "포함", "field": "inside", "align": "left"},
        ],
        rows=rows, row_key="name", pagination=15,
    ).props("dense flat wrap-cells").classes("w-full")


# ------------------------------------------------------------ 첫 반응 유형 ---
def _action_chart(rows: list[dict]) -> None:
    ui.echart({
        "title": {"text": "첫 반응 유형", "left": "left",
                  "textStyle": {"fontSize": 13}},
        "tooltip": {"trigger": "item", "formatter": "{b}: {c}건 ({d}%)"},
        "legend": {"bottom": 0, "type": "scroll", "textStyle": {"fontSize": 10}},
        "series": [{
            "type": "pie", "radius": ["38%", "60%"], "center": ["50%", "52%"],
            "avoidLabelOverlap": True, "label": {"fontSize": 11},
            "data": [{"name": r["name"], "value": r["value"],
                      "itemStyle": {"color": ACTION_COLOR.get(r["key"], "#999")}}
                     for r in rows],
        }],
    }).classes("w-full").style("height: 320px")


def _action_table(rows: list[dict], none_n: int) -> None:
    ui.table(
        columns=[
            {"name": "name", "label": "첫 반응", "field": "name", "align": "left"},
            {"name": "value", "label": "건수", "field": "value", "sortable": True},
            {"name": "mean", "label": "평균(업무일)", "field": "mean", "sortable": True},
            {"name": "median", "label": "중앙값", "field": "median", "sortable": True},
            {"name": "what", "label": "무엇을 했나", "field": "what", "align": "left"},
        ],
        rows=[{
            "name": r["name"], "value": r["value"],
            "mean": _dur(r["mean_days"]), "median": _dur(r["median_days"]),
            "what": ACTION_WHAT.get(r["key"], ""),
        } for r in rows],
        row_key="name",
    ).props("dense flat wrap-cells").classes("w-full")
    if none_n:
        ui.label(f"작성자 말고는 아무도 손대지 않은 이슈 {none_n:,}건은 위 분모에서 "
                 f"빠져 있습니다.").classes("text-caption text-grey-6")


#: 유형이 실제로 어떤 이벤트인지 — 화면에서 바로 읽히도록 풀어 쓴다.
ACTION_WHAT = {
    "resolve": "상태를 Resolved · Closed · Rejected 로 바꿨다 (첫 손길이 곧 종결)",
    "analyze": "상태를 InProgress 로 바꿨다 (분석 착수)",
    "status": "그 밖의 상태 변경 (ReOpen 등)",
    "comment": "코멘트를 남겼다",
    "assign": "담당자만 지정했다",
    "attach": "첨부만 올렸다",
    "field": "진행률·기한·우선순위·연관 이슈 등 필드만 고쳤다",
    "empty": "코멘트도 변경 내역도 없는 이벤트",
}


def render_issue_stats():
    """통계 탭 본문. 반환: 이 패널을 다시 그리는 함수."""
    state = {"include_excluded": False, "basis": "target", "months": 24, "top": 10}
    data: dict = {"rows": None}

    def _redraw() -> None:
        # 집계가 1초 걸려 이벤트 루프를 막으면 안 된다. draw 자체가 코루틴이라
        # 백그라운드 태스크로 띄운다(타이머를 새로 만들면 계속 쌓인다).
        background_tasks.create(draw(), name="issue-stats")

    def _recompose(**kw) -> None:
        """묶음 기준만 바꾼다 — 다시 세지 않고 이미 뽑아 둔 자료로 그린다."""
        state.update(kw)
        if data["rows"] is not None:
            comp_holder.refresh()

    with ui.row().classes("w-full items-center gap-2 q-mt-sm"):
        ui.checkbox("제외 이슈 포함",
                    on_change=lambda e: (state.update(include_excluded=bool(e.value)),
                                         _redraw())) \
            .props("dense").tooltip("Janam 등 감시 대상에서 뺀 이슈도 함께 집계")
        ui.space()
        ui.button("다시 계산", icon="refresh", on_click=_redraw) \
            .props("flat dense no-caps color=primary")

    holder = ui.column().classes("w-full gap-3")

    @ui.refreshable
    def comp_holder() -> None:
        rows = data["rows"]
        if rows is None:
            return
        comp = S.composition(rows, basis=state["basis"], months=state["months"],
                             top=state["top"])
        with ui.row().classes("w-full items-center gap-2"):
            ui.label("[Issue] 로 올라온 건이 달마다 어떤 형태였는지") \
                .classes("text-subtitle2")
            ui.space()
            ui.select(S.GROUP_BASES, value=state["basis"], label="묶는 기준",
                      on_change=lambda e: _recompose(basis=e.value)) \
                .props("outlined dense").classes("w-52")
            ui.select({12: "최근 1년", 24: "최근 2년", 36: "최근 3년", 120: "전체"},
                      value=state["months"], label="기간",
                      on_change=lambda e: _recompose(months=e.value)) \
                .props("outlined dense").classes("w-32")
            ui.select({8: "상위 8", 10: "상위 10", 15: "상위 15", 25: "상위 25"},
                      value=state["top"], label="묶음 수",
                      on_change=lambda e: _recompose(top=e.value)) \
                .props("outlined dense").classes("w-28")
        ui.label("제목이 [Issue][PM85_A9] 처럼 오면 두 번째 태그를 대상으로 봅니다. "
                 "PM85_A9 · PM85 PIE · PM85_O 는 한 묶음(PM85)으로 셉니다.") \
            .classes("text-caption text-grey-6")
        _composition_chart(comp, state["basis"])
        _composition_table(comp)

    async def draw() -> None:
        holder.clear()
        with holder:
            spin = ui.spinner(size="lg")
        rows = await run.io_bound(
            lambda: S.collect(include_excluded=state["include_excluded"]))
        data["rows"] = rows
        spin.delete()

        s = S.summary(rows)
        acts = S.by_first_action(rows)
        with holder:
            ui.label(
                "모든 소요시간은 이슈 이력(journal)에서 계산하고, 주말과 한국 공휴일을 "
                "뺀 업무일로 잽니다. Redmine 의 closed_on 은 Resolved 이슈 절반 이상이 "
                "비어 있고 일부는 생성일보다 과거라 쓰지 않습니다. "
                "제목이 [Test] 로 시작하는 기능 검증용 이슈는 집계에서 뺍니다."
            ).classes("text-caption text-grey-6")

            with ui.row().classes("gap-3 q-mt-xs").style("flex-wrap: wrap"):
                _kpi("전체 이슈", f"{s['total']:,}", f"미해결 {s['open']:,}건", "#6b7f8c")
                _kpi("최근 30일", f"신규 {s['new_30d']}", f"해결 {s['resolved_30d']}건",
                     C_CREATED)
                _kpi("Lead time (평균)", _dur(s["mean_lead"]),
                     f"중앙값 {_dur(s['median_lead'])} · 측정 {s['lead_measured']:,}건",
                     C_RESOLVED,
                     "생성 → Resolved. 주말·한국 공휴일을 뺀 업무일")
                _kpi("첫 반응 (평균)", _dur(s["mean_first"]),
                     f"중앙값 {_dur(s['median_first'])} · 측정 {s['action_measured']:,}건",
                     "#3b6ea5",
                     "작성자 외 사람이 처음 손댄 시점 — 코멘트뿐 아니라 상태·담당·첨부 변경도 포함")
                _kpi("재오픈율", f"{s['reopen_rate']:.1f}%",
                     "한 번이라도 다시 열린 비율", "#e07a2c",
                     "Resolved/Closed 이후 다시 열린 이력이 있는 이슈 비율")

            with ui.card().classes("w-full"):
                comp_holder()

            with ui.card().classes("w-full"):
                _monthly_chart(S.monthly(rows, 24))

            with ui.card().classes("w-full"):
                ui.label("첫 반응").classes("text-subtitle2")
                ui.label("작성자 말고 처음 손댄 사람이 무엇을 했는지로 나눕니다. "
                         "코멘트만 세면 담당만 배정하고 넘어간 건이 '반응 없음'이 됩니다.") \
                    .classes("text-caption text-grey-6")
                with ui.row().classes("w-full gap-3 no-wrap items-stretch"):
                    with ui.column().classes("flex-1 min-w-0"):
                        _action_chart(acts)
                    with ui.column().classes("flex-1 min-w-0"):
                        _hist_chart(S.duration_hist(rows, "first_action_days"),
                                    "첫 반응까지 (업무일)", "#3b6ea5")
                _action_table(acts, len(S.no_action(rows)))

            with ui.row().classes("w-full gap-3 no-wrap items-stretch"):
                with ui.card().classes("flex-1 min-w-0"):
                    _hist_chart(S.duration_hist(rows, "lead_days"),
                                "Lead time (업무일)", C_RESOLVED)
                with ui.card().classes("flex-1 min-w-0"):
                    _status_chart(S.by_status(rows))

            with ui.card().classes("w-full"):
                _tag_chart(S.by_tag(rows, 18))

            with ui.card().classes("w-full"):
                ui.label("담당자별").classes("text-subtitle2")
                ui.table(
                    columns=[
                        {"name": "name", "label": "담당자", "field": "name",
                         "align": "left", "sortable": True},
                        {"name": "total", "label": "전체", "field": "total",
                         "sortable": True},
                        {"name": "open", "label": "미해결", "field": "open",
                         "sortable": True},
                        {"name": "resolved", "label": "해결", "field": "resolved",
                         "sortable": True},
                        {"name": "lead", "label": "Lead time 평균(업무일)",
                         "field": "lead", "sortable": True},
                        {"name": "action", "label": "첫 반응 평균(업무일)",
                         "field": "action", "sortable": True},
                    ],
                    rows=[{
                        "name": a["name"], "total": a["total"], "open": a["open"],
                        "resolved": a["resolved"],
                        "lead": round(a["mean_lead"], 1)
                        if a["mean_lead"] is not None else "—",
                        "action": round(a["mean_action"], 1)
                        if a["mean_action"] is not None else "—",
                    } for a in S.by_assignee(rows, 20)],
                    row_key="name",
                ).props("dense flat").classes("w-full")

            with ui.card().classes("w-full"):
                ui.label("가장 오래 열려 있는 미해결 이슈").classes("text-subtitle2")
                ui.label("생성된 지 오래됐는데 아직 안 닫힌 것들입니다.") \
                    .classes("text-caption text-grey-6")
                for days, r in s["aging"]:
                    with ui.row().classes("items-center gap-2 no-wrap w-full"):
                        ui.badge(f"{days:,}일", color="red-7")
                        ui.badge(r.status, color="grey-7").props("outline")
                        ui.link(f"#{r.id}", issue_url(r.id), new_tab=True) \
                            .classes("text-caption text-primary")
                        ui.label(r.subject).classes(
                            "text-body2 flex-1 min-w-0 ellipsis")
                        ui.label(r.assignee).classes("text-caption text-grey-6")

    ui.timer(0.05, draw, once=True)     # 첫 렌더를 막지 않도록 그린 뒤 계산
    return _redraw
