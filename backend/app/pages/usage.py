"""Claude 사용량 대시보드 (/usage) — 실시간·일별·월별 토큰/비용 + 작업별 내역.

비용은 토큰 × 공개 단가로 계산한 **추정치**이며, 이 앱이 한 Claude 호출만 집계한다.
"""
from nicegui import ui

from app import usage
from app.theme import apply_theme, page_header


def _usd(v) -> str:
    v = float(v or 0)
    return f"${v:,.2f}" if v >= 1 else f"${v:.4f}"


def _n(v) -> str:
    return f"{int(v or 0):,}"


def _kpi(title: str, d: dict, color: str) -> None:
    with ui.card().classes("items-start").style(f"min-width:180px; border-left:4px solid {color}"):
        ui.label(title).classes("text-caption text-grey-7")
        ui.label(_usd(d.get("cost"))).classes("text-h5")
        ui.label(f"{_n(d.get('tokens'))} tokens · {_n(d.get('calls'))}회").classes(
            "text-caption text-grey-6")


def _bar_chart(rows: list[dict], key: str, title: str) -> None:
    xs = [r[key] for r in rows]
    costs = [round(float(r["cost"] or 0), 6) for r in rows]
    ui.echart({
        "title": {"text": title, "textStyle": {"fontSize": 13}},
        "tooltip": {"trigger": "axis"},
        "grid": {"left": 55, "right": 20, "top": 40, "bottom": 60},
        "xAxis": {"type": "category", "data": xs,
                  "axisLabel": {"rotate": 45, "fontSize": 10}},
        "yAxis": {"type": "value", "axisLabel": {"formatter": "${value}"}},
        "series": [{"type": "bar", "data": costs, "itemStyle": {"color": "#4A7C67"},
                    "name": "비용($)"}],
    }).classes("w-full").style("height: 300px")


@ui.page("/usage")
def usage_page():
    apply_theme()
    with page_header("Claude 사용량"):
        _render()


def _render():
    @ui.refreshable
    def body():
        t = usage.totals()
        ui.label("Claude API 사용량 · 비용(추정)").classes("text-h6")
        ui.label(
            "토큰 수 × 공개 단가 기준 추정치입니다(실제 청구액과 다를 수 있음)."
        ).classes("text-caption text-grey-7")

        with ui.row().classes("gap-3 q-mt-sm").style("flex-wrap: wrap"):
            _kpi("오늘", t["today"], "#4A7C67")
            _kpi("이번 달", t["month"], "#3b6ea5")
            _kpi("전체", t["all"], "#8a8a8a")

        day_rows = usage.by_day(30)
        month_rows = usage.by_month(12)
        if not day_rows:
            ui.label("아직 기록된 사용량이 없습니다. Claude 작업(Release Note·Service Portal·Knowledge Base·Newsletter 등)을 실행하면 집계됩니다.") \
                .classes("text-caption text-grey-6 q-mt-md")
        else:
            with ui.card().classes("w-full"):
                _bar_chart(day_rows, "day", "일별 비용 (최근 30일)")
            with ui.card().classes("w-full"):
                _bar_chart(month_rows, "month", "월별 비용")

        # 작업별 내역
        ui.separator()
        with ui.row().classes("items-center gap-2"):
            ui.label("작업별 사용량").classes("text-subtitle1")
            scope = ui.toggle({"today": "오늘", "month": "이번 달", "all": "전체"},
                              value="month").props("dense")
        op_box = ui.column().classes("w-full")

        def _render_ops():
            op_box.clear()
            rows = usage.by_operation(scope.value)
            cols = [
                {"name": "operation", "label": "작업", "field": "operation", "align": "left", "sortable": True},
                {"name": "calls", "label": "호출수", "field": "calls", "align": "right", "sortable": True},
                {"name": "tokens", "label": "토큰", "field": "tokens", "align": "right", "sortable": True},
                {"name": "cost", "label": "비용", "field": "cost", "align": "right", "sortable": True},
            ]
            disp = [{"operation": r["operation"], "calls": _n(r["calls"]),
                     "tokens": _n(r["tokens"]), "cost": _usd(r["cost"])} for r in rows]
            with op_box:
                if not disp:
                    ui.label("데이터 없음").classes("text-caption text-grey-6")
                else:
                    ui.table(columns=cols, rows=disp, row_key="operation").props(
                        "dense flat bordered").classes("w-full")

        scope.on_value_change(lambda: _render_ops())
        _render_ops()

        # 최근 호출
        ui.separator()
        ui.label("최근 호출").classes("text-subtitle1")
        rc = usage.recent(50)
        rcols = [
            {"name": "ts", "label": "시각", "field": "ts", "align": "left"},
            {"name": "operation", "label": "작업", "field": "operation", "align": "left"},
            {"name": "model", "label": "모델", "field": "model", "align": "left"},
            {"name": "io", "label": "in/out", "field": "io", "align": "right"},
            {"name": "cost", "label": "비용", "field": "cost", "align": "right"},
        ]
        rdisp = [{"ts": r["ts"][:19].replace("T", " "), "operation": r["operation"],
                  "model": r["model"], "io": f"{_n(r['input_tokens'])}/{_n(r['output_tokens'])}",
                  "cost": _usd(r["cost_usd"])} for r in rc]
        ui.table(columns=rcols, rows=rdisp, row_key="ts", pagination=15).props(
            "dense flat bordered").classes("w-full")

    with ui.row().classes("w-full justify-end"):
        ui.button("새로고침", icon="refresh", on_click=lambda: body.refresh()).props("flat")
    body()
