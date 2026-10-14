"""제품 관리 (/products) — OS Update 작업의 기준 목록.

메일 기준(새 배포 메일이 오면 한 줄)이 아니라 **제품 기준**으로 목록을 둔다.
한 행 = 제품명 + Android 버전 + Type(GMS/AOSP/Russia…) 조합 하나 = SKU.

행을 펼치면 그 SKU 의 작업 경로 8개가 보이고, [제품 등록] 으로 새 제품을 추가한다.
(제품별 OS Update 작업 실행은 다음 단계에서 이 목록 위에 붙인다.)
"""
from __future__ import annotations

from nicegui import run, ui

from app import os_products as P
from app.auth import current_user_id, current_username
from app.theme import apply_theme, page_header

_TYPE_COLOR = {"GMS": "teal-7", "AOSP": "indigo-6", "Russia": "deep-orange-7"}

# 열 너비 — 머리글과 각 행이 **같은 값**을 써야 열이 어긋나지 않는다.
_W_PRODUCT = "110px"
_W_ANDROID = "80px"
_W_TYPE = "90px"
_W_INITIAL = "96px"
# 최신 RN 은 버전 + 판 수 배지 + 문서 버튼 2개가 들어가 넓어야 한다 → **남는 폭을 가져간다**.
# 반대로 '등록 항목' 은 체크 + N/M 이 전부라 고정 폭으로 좁게 둔다(미등록 목록은 툴팁에 있다).
_S_LATEST = "flex:1 1 0; min-width:220px"
_W_FILLED = "190px"
_W_DATE = "96px"
_W_ACTION = "88px"
# 펼침 화살표 칸. ui.expansion 기본 화살표는 헤더 슬롯 **밖**에 붙어 열을 밀어내므로
# hide-expand-icon 으로 끄고, 이 폭의 칸을 머리글·행에 똑같이 둬서 직접 그린다.
_W_EXPAND = "28px"


def _cell(width: str) -> str:
    return f"width:{width}; flex:none"


def _type_color(t: str) -> str:
    return _TYPE_COLOR.get(t, "blue-grey-6")


def _short_date(dt) -> str:
    return dt.strftime("%Y-%m-%d") if dt else ""


@ui.page("/products")
def products_page():
    apply_theme()
    # 경로가 길어 한 줄에 다 보여주려면 넓은 폭이 필요하다.
    with page_header("제품 관리", max_w="max-w-[104rem]"):
        _render()


def _render() -> None:
    ui.label("제품 관리").classes("text-h6")
    ui.label(
        "OS Update 작업의 기준 목록입니다. 제품명 + Android 버전 + Type 조합 하나가 한 행이며, "
        "그 조합에 쓰이는 경로를 미리 등록해 두면 작업할 때 다시 찾지 않습니다."
    ).classes("text-caption text-grey-7")

    state = {"q": ""}

    @ui.refreshable
    def product_list() -> None:
        rows = P.list_products(state["q"])
        if not rows:
            with ui.card().classes("w-full"):
                if state["q"]:
                    ui.label("조건에 맞는 제품이 없습니다.").classes("text-grey-7")
                else:
                    ui.label("등록된 제품이 없습니다.").classes("text-grey-7")
                    ui.label(
                        "오른쪽 위 [제품 등록] 으로 첫 제품을 추가하세요."
                    ).classes("text-caption text-grey-6")
            return

        ui.label(f"총 {len(rows)}개").classes("text-caption text-grey-7")
        # 제품군으로 묶어 접었다 편다(보기 구분 전용). 검색 중에는 결과가 어느 칸에
        # 있는지 찾아 헤매지 않도록 해당 칸을 펼쳐 둔다.
        for cat, items in P.group_by_category(rows):
            with ui.expansion(value=bool(state["q"]) and bool(items)) \
                    .classes("w-full").props("dense expand-separator") as exp:
                with exp.add_slot("header"):
                    with ui.row().classes("w-full items-center gap-2 no-wrap"):
                        ui.label(cat).classes("text-subtitle2").style("font-weight:700")
                        ui.label(f"{len(items)}개").classes(
                            "text-caption " + ("text-grey-7" if items else "text-grey-5"))
                if not items:
                    ui.label("이 제품군에 등록된 제품이 없습니다.") \
                        .classes("text-caption text-grey-5")
                    continue
                # nicegui-card-tight : 기본 padding·gap 을 0 으로. (gap 이 남으면 머리글과
                # 첫 행 사이에 틈이 생긴다 — ui.card 기본값이 gap: var(--nicegui-default-gap))
                with ui.card().classes("w-full nicegui-card-tight").style(
                        "overflow:hidden"):
                    _list_header()
                    for r in items:
                        _product_row(r, on_change=product_list.refresh)

    # ── 검색 · 등록 ──────────────────────────────────────────────────────
    with ui.row().classes("w-full items-center no-wrap gap-2"):
        def _on_search(e) -> None:
            state["q"] = (e.value or "").strip()
            product_list.refresh()

        ui.input(placeholder="제품명 · Android · Type · 경로 검색", on_change=_on_search) \
            .props("dense outlined clearable").classes("w-80")

        ui.space()
        ui.button("제품 등록", icon="add",
                  on_click=lambda: _open_form(None, product_list.refresh)) \
            .props("color=primary no-caps")

    product_list()


def _list_header() -> None:
    # px-4 : 아래 행(q-item--dense)의 좌우 padding 16px 과 같은 값이어야 열이 맞는다.
    with ui.row().classes("w-full items-center gap-3 no-wrap px-4 py-2").style(
        "background:#f0f1ef; border-bottom:1px solid #d5d8d2; font-weight:600"
    ):
        ui.label("제품명").classes("text-caption").style(_cell(_W_PRODUCT))
        ui.label("Android").classes("text-caption").style(_cell(_W_ANDROID))
        ui.label("Type").classes("text-caption").style(_cell(_W_TYPE))
        ui.label("최초 OS").classes("text-caption").style(_cell(_W_INITIAL))
        ui.label("최신 RN").classes("text-caption").style(_S_LATEST)
        ui.label("등록 항목").classes("text-caption").style(_cell(_W_FILLED))
        ui.label("수정").classes("text-caption").style(_cell(_W_DATE))
        ui.label("").style(_cell(_W_ACTION))
        ui.label("").style(_cell(_W_EXPAND))


def _product_row(r: dict, on_change) -> None:
    n_filled = P.filled_items(r)
    n_total = len(P.COUNTED_KEYS)

    # hide-expand-icon : 기본 화살표를 끄고 아래에서 직접 그린다(열 정렬 유지).
    exp = ui.expansion().classes("w-full").props(
        "expand-separator dense hide-expand-icon")
    with exp.add_slot("header"):
        with ui.row().classes("w-full items-center gap-3 no-wrap"):
            ui.label(r["product"]).classes("text-body1").style(
                _cell(_W_PRODUCT) + "; font-weight:700")
            with ui.element("div").style(_cell(_W_ANDROID)):
                if r["android"]:
                    ui.badge(f"A{r['android']}", color="grey-7")
            with ui.element("div").style(_cell(_W_TYPE)):
                if r["os_type"]:
                    ui.badge(r["os_type"], color=_type_color(r["os_type"]))
            ui.label(r["initial_os_version"] or "—").classes(
                "text-body2 font-mono"
                + ("" if r["initial_os_version"] else " text-grey-5")
            ).style(_cell(_W_INITIAL))
            _latest_cell(r)
            with ui.row().classes("items-center gap-2 no-wrap").style(
                _cell(_W_FILLED)
            ):
                _done = n_filled == n_total
                ui.icon("check_circle" if _done else "radio_button_unchecked",
                        size="xs").classes("text-green-7" if _done else "text-grey-5")
                ui.label(f"{n_filled}/{n_total}").classes(
                    "text-caption " + ("text-green-9" if _done else "text-grey-7"))
                if not _done:
                    # 무엇이 비었는지 바로 보여준다(숫자만으론 알 수 없다).
                    missing = P.missing_labels(r)
                    ui.label("미등록: " + " · ".join(missing)).classes(
                        "text-caption text-grey-5 ellipsis").style(
                        "min-width:0").tooltip("\n".join(missing))
            ui.label(_short_date(r.get("updated_at"))).classes(
                "text-caption text-grey-6").style(_cell(_W_DATE))
            with ui.row().classes("gap-1 no-wrap items-center").style(_cell(_W_ACTION)):
                ui.button(icon="edit", on_click=lambda rr=r: _open_form(rr, on_change)) \
                    .props("flat dense size=sm").tooltip("수정")
                ui.button(icon="delete",
                          on_click=lambda rr=r: _confirm_delete(rr, on_change)) \
                    .props("flat dense size=sm color=negative").tooltip("삭제")
            chevron = ui.icon("expand_more", size="sm").classes("text-grey-6").style(
                _cell(_W_EXPAND))
            exp.on_value_change(
                lambda e, ic=chevron: ic.set_name(
                    "expand_less" if e.value else "expand_more"))

    with exp:
        with ui.column().classes("w-full gap-3 px-2 pb-2"):
            _dev_rn_block(r, on_change)
            ui.label("기본 정보").classes("text-subtitle2")
            for key, label, _ph in P.INFO_FIELDS:
                _path_line(label, r.get(key, ""), copy=False)
            for group, fields in P.PATH_GROUPS:
                ui.label(group).classes("text-subtitle2")
                for key, label, _ph in fields:
                    _path_line(label, r.get(key, ""))
            if r.get("updated_by") or r.get("created_by"):
                ui.label(
                    f"등록 {r.get('created_by') or '?'} "
                    f"{_short_date(r.get('created_at'))}"
                    + (f" · 최근 수정 {r['updated_by']} {_short_date(r.get('updated_at'))}"
                       if r.get("updated_by") else "")
                ).classes("text-caption text-grey-6")


def _releases_of(product_id: int) -> list[dict]:
    """수집된 개발 Release Note 목록(최신 먼저). 수집 전이면 빈 목록."""
    from app.pipeline import dev_rn as RN

    try:
        return RN.list_releases(product_id)
    except Exception:  # noqa: BLE001  (목록 그리기가 수집 실패로 막히면 안 된다)
        return []


def _doc_buttons(rel: dict) -> None:
    """그 판의 docx·pdf 내려받기 버튼(로컬에 받아 둔 파일만)."""
    from app.pipeline import dev_rn as RN

    for name, path, icon, tip in (
        (rel.get("docx_name"), rel.get("docx_path"), "description", "Word 내려받기"),
        (rel.get("pdf_name"), rel.get("pdf_path"), "picture_as_pdf", "PDF 내려받기"),
    ):
        if name and RN.exists(path):
            ui.button(icon=icon, on_click=lambda p=path: ui.download(p)) \
                .props("flat dense size=sm").tooltip(f"{tip} — {name}")


def _latest_cell(r: dict) -> None:
    """목록 행의 '최신 RN' 칸 — 가장 높은 버전 + 문서 내려받기.

    수집 전이면 '수집 안 됨' 으로 둔다. 등록만 하고 수집을 안 돌린 제품이 많아
    (경로를 나중에 수정으로 채우면 신규 등록 트리거가 걸리지 않는다) 그 상태가
    비어 있는 것과 구분되어야 한다.
    """
    rels = _releases_of(r["id"])
    with ui.row().classes("items-center gap-1 no-wrap").style(_S_LATEST):
        if not rels:
            ui.label("수집 안 됨").classes("text-caption text-grey-5")
            return
        ui.label(rels[0]["version"]).classes("text-body2 font-mono")
        if len(rels) > 1:
            ui.badge(f"+{len(rels) - 1}", color="grey-6").tooltip(
                "이 제품에서 수집된 판: " + ", ".join(x["version"] for x in rels[:12]))
        _doc_buttons(rels[0])


def _dev_rn_block(r: dict, on_change) -> None:
    """펼침 영역의 '개발 Release Note' — 수집된 판 목록 + 다시 가져오기.

    수집은 등록 경로를 **읽기만** 한다(파일서버에 쓰지 않는다). 받은 문서는
    work/dev_rn/{제품id}/{버전}/ 에 두고, 목록은 그 폴더의 현재 상태를 그대로 반영한다.
    """
    from app.pipeline import dev_rn as RN

    rels = _releases_of(r["id"])
    with ui.row().classes("w-full items-center gap-2 no-wrap"):
        ui.label("개발 Release Note").classes("text-subtitle2")
        ui.label(f"{len(rels)}판" if rels else "수집 안 됨").classes(
            "text-caption " + ("text-grey-7" if rels else "text-orange-9"))

        async def _rescan(pid=r["id"], path=r.get("dev_rn_path", "")) -> None:
            if not (path or "").strip():
                ui.notify("'개발 Release Note 파일 경로' 를 먼저 등록하세요.", color="warning")
                return
            n = ui.notification("등록 경로에서 개발 Release Note 를 가져오는 중…",
                                spinner=True, timeout=None)
            try:
                res = await run.io_bound(RN.scan_product, pid)
            except Exception as e:  # noqa: BLE001
                n.dismiss()
                ui.notify(f"수집 실패: {type(e).__name__}: {e}", color="negative")
                return
            n.dismiss()
            if not res.get("ok"):
                ui.notify(f"수집 실패: {res.get('error')}", color="negative")
                return
            msg = f"{res['total']}판 수집" + (f" (파일 {res['downloaded']}개 저장)"
                                           if res.get("downloaded") else "")
            ui.notify(msg, color="positive")
            if res.get("skipped"):
                # 무엇이 빠졌는지 알려야 '이게 전부'로 오해하지 않는다.
                ui.notify("제외 " + str(len(res["skipped"])) + "건: "
                          + " / ".join(res["skipped"][:3]), color="warning", timeout=8000)
            on_change()

        ui.button("다시 가져오기", icon="refresh", on_click=_rescan) \
            .props("flat dense size=sm").tooltip("등록한 경로를 다시 훑습니다(읽기 전용)")

    if not rels:
        ui.label("아직 수집하지 않았습니다. [다시 가져오기] 를 누르면 등록 경로에서 가져옵니다.") \
            .classes("text-caption text-grey-6")
        return
    for rel in rels[:12]:
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            ui.label(rel["version"]).classes("text-body2 font-mono").style(
                "width:140px; flex:none")
            ui.label(rel.get("pdf_name") or rel.get("docx_name") or "").classes(
                "text-caption text-grey-6 ellipsis").style("flex:1 1 0; min-width:0")
            _doc_buttons(rel)
    if len(rels) > 12:
        ui.label(f"… 외 {len(rels) - 12}판").classes("text-caption text-grey-6")


def _path_line(label: str, value: str, copy: bool = True) -> None:
    """한 줄 = 라벨 + 값(+경로면 복사 버튼). 비어 있으면 '미등록' 으로 표시한다."""
    with ui.row().classes("w-full items-start gap-2 no-wrap"):
        ui.label(label).classes("text-caption text-grey-7").style(
            "width:250px; flex:none")
        if value:
            ui.label(value).classes("text-body2 font-mono").style(
                "flex:1 1 0; min-width:0; word-break:break-all")
            if copy:
                ui.button(
                    icon="content_copy",
                    on_click=lambda v=value: (
                        ui.clipboard.write(v),
                        ui.notify("경로를 복사했습니다.", color="positive"),
                    ),
                ).props("flat dense size=sm").tooltip("경로 복사")
        else:
            ui.label("미등록").classes("text-caption text-grey-5").style(
                "flex:1 1 0; min-width:0")


def _open_form(row: dict | None, on_change) -> None:
    """제품 등록/수정 폼. row 가 None 이면 등록."""
    editing = row is not None
    inputs: dict = {}

    with ui.dialog() as dlg, ui.card().classes("w-[62rem] max-w-full").style(
        "max-height:92vh; overflow-y:auto"
    ):
        ui.label("제품 수정" if editing else "제품 등록").classes("text-h6")
        hint = ui.label().classes("text-caption text-grey-7")

        def _cur(key: str) -> str:
            return str((row or {}).get(key, "") or "")

        # ── 제품군 → 제품명 → OS 입력 → 기본 정보 ─────────────────────
        # 제품군을 **가장 먼저** 고르게 한다. OS 가 없는 제품(RFID 리더기)이 있어
        # 아래 OS 입력을 받을지 말지가 이 값으로 갈리기 때문이다.
        ui.separator()
        with ui.row().classes("w-full items-start gap-3 no-wrap"):
            inputs["category"] = ui.select(
                P.CATEGORIES, value=_cur("category") or None, label="제품군 *",
            ).props("dense outlined").classes("flex-1").tooltip(P.CATEGORY_HINT)

            inputs["product"] = ui.input(
                "제품명 *", value=_cur("product"), placeholder="PM95",
            ).props("dense outlined").classes("flex-1")

            # OS 축 입력 — Others 면 통째로 감춘다.
            with ui.row().classes("items-start gap-3 no-wrap").style(
                "flex:2 1 0; min-width:0"
            ) as os_box:
                inputs["android"] = ui.input(
                    "Android 버전 *", value=_cur("android"), placeholder="15",
                ).props("dense outlined").classes("flex-1")

                # 목록에 없는 계열은 직접 입력한다(입력값이 선택지로 추가된다).
                opts = P.known_types()
                if _cur("os_type") and _cur("os_type") not in opts:
                    opts = opts + [_cur("os_type")]
                inputs["os_type"] = ui.select(
                    opts, value=_cur("os_type") or None, label="Type *",
                    with_input=True, new_value_mode="add-unique",
                ).props("dense outlined").classes("flex-1").tooltip(
                    "목록에 없으면 직접 입력하세요")

            inputs["initial_os_version"] = ui.input(
                "최초 OS 버전", value=_cur("initial_os_version"), placeholder="95.01",
            ).props("dense outlined").classes("flex-1")

        def _sync_os() -> None:
            """제품군에 맞춰 OS 입력을 보이거나 감춘다."""
            others = P.is_others(inputs["category"].value)
            os_box.set_visibility(not others)
            # 무엇이 제품을 구분하는 키인지만 알린다(중복 저장이 막히는 기준이라 필요).
            hint.set_text("구분 키: 제품명" if others else
                          "구분 키: 제품명 · Android 버전 · Type")

        inputs["category"].on_value_change(lambda _: _sync_os())
        _sync_os()

        # ── 경로 ────────────────────────────────────────────────────
        for group, fields in P.PATH_GROUPS:
            ui.separator()
            ui.label(group).classes("text-subtitle2")
            for key, label, ph in fields:
                inputs[key] = ui.input(
                    label, value=str((row or {}).get(key, "") or ""), placeholder=ph,
                ).props("dense outlined").classes("w-full")

        async def _save() -> None:
            fields = {k: (el.value or "") for k, el in inputs.items()}
            actor = current_username() or ""
            if editing:
                ok, msg = P.update(row["id"], fields, actor)
            else:
                ok, msg = P.create(fields, actor)
            ui.notify(msg, color="positive" if ok else "negative", multi_line=True)
            if not ok:
                return
            dlg.close()
            on_change()
            # 신규 등록이면 등록된 경로에서 개발 Release Note 를 바로 가져온다(트리거).
            if not editing:
                await _fetch_dev_rn(fields)

        ui.separator()
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("취소", on_click=dlg.close).props("flat")
            ui.button("저장" if editing else "등록", icon="save", on_click=_save) \
                .props("color=primary")
    dlg.open()


async def _fetch_dev_rn(fields: dict) -> None:
    """신규 등록 트리거 — 방금 등록한 제품의 자료를 **한 번에** 모아 둔다.

    · 개발 Release Note 수집(등록한 파일 경로)
    · 출시 이력 조회(Service Portal · Knowledge Base)

    /osupdate 에는 다시 가져오는 버튼을 두지 않는다. 등록할 때 한 번 모아 두면
    그 뒤로는 트리거 메일이 버전을 확실히 알려주므로 다시 긁을 일이 없다.
    못 가져온 건 무시하고 넘어간다 — 있는 것만 쓴다.
    """
    from app import release_history as RH
    from app.pipeline import dev_rn as RN

    saved = next(
        (r for r in P.list_products()
         if (r["product"], r["android"], r["os_type"])
         == (fields.get("product", "").strip().upper().replace(" ", ""),
             "".join(c for c in fields.get("android", "") if c.isdigit()),
             fields.get("os_type", "").strip())),
        None,
    )
    if saved is None:
        return
    n = ui.notification("등록한 경로에서 자료를 모으는 중… (개발 RN · 출시 이력)",
                        spinner=True, timeout=None)
    done: list[str] = []

    if saved["dev_rn_path"]:
        try:
            res = await run.io_bound(RN.scan_product, saved["id"])
            if res.get("ok"):
                done.append(f"개발 Release Note {res['total']}개"
                            + (f" (파일 {res['downloaded']}개 저장)"
                               if res["downloaded"] else ""))
        except Exception:  # noqa: BLE001
            pass                       # 못 가져와도 등록은 그대로 둔다

    try:
        hist = await run.io_bound(RH.refresh, saved["id"], current_user_id())
        if hist.get("ok"):
            done.append(f"출시 이력 {len(hist['versions'])}개")
    except Exception:  # noqa: BLE001
        pass

    n.dismiss()
    if done:
        ui.notify("가져왔습니다 — " + " · ".join(done)
                  + ". [OS Update → 작업] 에서 확인하세요.",
                  color="positive", multi_line=True)
    else:
        ui.notify(
            "등록은 됐지만 자료를 가져오지 못했습니다. 트리거 메일이 오면 버전은 "
            "메일에서 확인합니다.",
            color="warning", multi_line=True)


def _confirm_delete(row: dict, on_change) -> None:
    with ui.dialog() as dlg, ui.card():
        ui.label(f"'{P.sku_label(row)}' 을(를) 삭제할까요?").classes("text-subtitle1")
        ui.label("등록한 경로도 함께 지워집니다.").classes(
            "text-caption text-grey-7").style("max-width:24rem")

        def _do() -> None:
            ok, msg = P.delete(row["id"])
            ui.notify(msg, color="positive" if ok else "negative")
            dlg.close()
            if ok:
                on_change()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("취소", on_click=dlg.close).props("flat")
            ui.button("삭제", on_click=_do).props("color=negative")
    dlg.open()
