"""RN 승인 (/reviews) — ② Release Note 의 1차 승인.

흐름: 작성자가 ②를 실행해 RN **docx** 초안을 만들면 → RN 승인자에게 승인 요청 메일 →
RN 승인자가 이 페이지에서
  1) 공개 선별표의 포함 항목을 체크로 수정(포함 항목은 노란 하이라이트),
  2) 단말에서만 확인 가능한 값(Audio/Touch/Wi-Fi/Known Issues)을 입력,
  3) 고친 게 있으면 **[저장]** — 저장 + 문서 재생성 + 미리보기 갱신.
     저장 전에는 승인 버튼이 잠긴다(문서와 표가 어긋난 채 넘어가지 않게).
  4) **승인** 또는 **반려**. 승인은 **1차 결재**다(마지막이 아니다).
       · 승인 → History 관리용 RN(전체 이력본) PDF 확정 + 개발 RN 경로 저장.
         단계는 끝나지 않는다 — 작성자가 공개 항목을 선별·편집해 만든
         **RN for Sales** 가 최종 승인자 검수(/final-reviews)를 거쳐야 완료된다.
       · 반려 → 작성자에게 되돌린다(사유 필수). 작성자 화면의 잠금이 풀리고 다시
         승인을 요청할 수 있다. 반려한 건은 이 목록에서 빠진다.
     요청자에게 알림 메일이 나가고, 다른 RN 승인자는 참조로 함께 받는다.

이력본 PDF 는 승인 시점에 만든다(app.pipeline.rn_research.finalize_full_pdf).
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from functools import partial as _partial

from nicegui import run, ui

from app import user_mailer
from app.auth import can_mid_decide, current_user_id, current_username
from app.database import get_session
from app.models import CachedMail
from app.pairing import build_tasks
from app.pipeline import history as H
from app.pipeline.rn_select import normalize_rows as _norm_sel_rows
from app.pipeline.state import get_state, update_state
from app.theme import AUTOFIT_CLASS, apply_theme, page_header

MID_STATE_KEY = "rn_mid_review"      # RN 승인 상태(최종 검수 rn_review 와 분리)
                                     # 키 이름은 기존 저장분 호환을 위해 그대로 둔다.

# 공개 RN 선별표 열 — (머리글, 너비). 모든 행이 **같은 고정 너비**를 써야 열이 어긋나지 않는다.


# RN 승인자가 채워야 하는 값. 순서는 RN 템플릿의 섹션 순서와 같지만 **번호는 붙이지
# 않는다** — 값이 빈 섹션은 문서에서 통째로 빠지고 뒷 번호가 당겨지므로(2026-08-26),
# 화면에 박아 둔 번호는 문서와 어긋난다.
# (표시 레이블, 내부 키, 여러 줄 입력 여부, 필수 여부)
# Known Issues 는 없을 수 있으므로 선택 입력, 나머지는 필수.
INPUT_FIELDS = [
    ("Known Issues (선택)", "known_issues", True, False),
    ("GMS Package", "gms_package", False, True),
    ("SDK", "sdk", False, True),
    ("Audio parameter version information", "audio", False, True),
    ("Touch panel version information", "touch", False, True),
    ("Wi-Fi — Driver", "wifi_driver", False, True),
    ("Wi-Fi — Firmware", "wifi_firmware", False, True),
    ("Wi-Fi — Configuration", "wifi_config", False, True),
    ("Android security patch level", "security_patch", False, True),
]
# security_patch 는 상태 최상위에 저장된다(나머지는 rn_components 안).
_TOP_LEVEL_KEYS = {"security_patch"}


def note_min_height(value: str) -> str:
    """'수정 내용' 입력란의 최소 높이 — 값의 줄 수만큼.

    표가 **접힌 상태**(expansion/dialog)에서 mount 되면 Quasar 의 autogrow 가 높이를
    잘못 재고, 펼친 뒤에도 다시 계산하지 않아 여러 줄이 잘린다(실측: 4줄 값이 36px 로
    굳고 실제 내용 높이는 87px). 그래서 줄 수로 최소 높이를 미리 박아 둔다.
    화면에 보인 뒤부터는 autogrow 가 정상 동작하므로 더 늘어나는 것은 그대로 된다.
    """
    lines = max(1, str(value or "").count("\n") + 1)
    return f"min-height:{min(1.5 * lines + 1.0, 16.0):.1f}rem"


def input_fields_for(st: dict) -> list[tuple[str, str, bool, bool]]:
    """이 작업의 입력 항목 — **RN 에서 뽑은 목록이 있으면 그것을** 쓴다.

    섹션 구성은 제품마다 다르다(실측: MX450 은 Camera tuning·Scanner 까지 10번,
    PM45 는 TP Firmware, PM67 은 보안패치가 3번). 그래서 ②가 이번 버전 RN(또는 직전 버전
    RN)에서 읽어 둔 `rn_input_fields` 로 칸을 만들고, 없을 때만 아래 기본 목록으로 폴백한다.
    반환 형식은 기존과 같다: (표시 라벨, 내부 키, 여러 줄, 필수 여부).
    """
    dyn = (st or {}).get("rn_input_fields") or []
    out: list[tuple[str, str, bool, bool]] = []
    for d in dyn:
        key = str(d.get("key") or "").strip()
        if not key:
            continue
        out.append((strip_section_no(str(d.get("label") or key)), key,
                    bool(d.get("multiline")), False))
    return out or INPUT_FIELDS


class InputFieldBox:
    """입력 항목 칸 묶음 — 값 읽기·빈칸 확인·항목 목록 저장을 한 곳에서 다룬다.

    editable=True(최초 릴리즈)면 항목을 지우고 더할 수 있으므로, 저장할 때 값뿐 아니라
    **항목 목록 자체**(rn_input_fields)도 함께 써야 한다.
    """

    def __init__(self, fields: list[dict], editable: bool) -> None:
        self.fields = fields            # 화면에 살아 있는 항목(삭제·추가 반영)
        self.editable = editable
        self._base = {f["key"] for f in fields}

    def values(self) -> dict:
        return {f["key"]: (f["el"].value or "").strip() for f in self.fields}

    def missing(self) -> list[str]:
        return [f["label"] for f in self.fields
                if f.get("required") and not (f["el"].value or "").strip()]

    def dropped(self) -> set:
        """지운 항목의 키 — 저장 때 값도 같이 걷어내야 문서에 남지 않는다."""
        return self._base - {f["key"] for f in self.fields}

    def save_fields(self, mail_id) -> None:
        if not self.editable:
            return
        # multiline 은 **값에서 다시 판단해** 쓴다. 예전에는 항목을 만들 때 정한 표시를
        # 그대로 다시 저장해서, 값이 여러 줄로 바뀌어도 표시는 계속 False 로 남았다
        # (실측 job-15·job-25 의 touch·camera_tuning·scanner). 화면은 이제 이 표시와
        # 무관하게 모든 칸을 여러 줄로 그리지만, 저장분이 값과 어긋난 채 남는 것을 막는다.
        update_state(mail_id, rn_input_fields=[
            {"key": f["key"], "label": f["label"],
             "value": (f["el"].value or "").strip(),
             "multiline": "\n" in (f["el"].value or "")}
            for f in self.fields
        ])


def strip_section_no(label: str) -> str:
    """라벨 앞의 섹션 번호를 뗀다 — '8. Camera tuning …' → 'Camera tuning …'.

    번호는 문서에서 Word 가 매긴다(2026-08-26). 예전에 저장된 작업의 항목 목록에는
    번호가 박힌 라벨이 남아 있어, 읽는 자리에서 걷어낸다.
    """
    return re.sub(r"^\s*\d{1,2}\.\s*", "", str(label or "")).strip()


def render_input_fields(mail_id, st: dict, *, enabled: bool = True,
                        on_change=None) -> InputFieldBox:
    """입력 항목 칸들을 그린다. **최초 릴리즈면 항목을 지우고 더할 수 있다.**

    최초 버전은 참고할 이전 RN 이 없어 기본 항목으로 시작하는데, 섹션 구성은 제품마다
    다르다(실측: MX450 은 Camera tuning·Scanner 까지, PM45 는 TP Firmware). 그대로 두면
    첫 RN 이 그 제품에 없는 칸을 달고 나가므로, 이때만 편집을 연다.
    두 번째 버전부터는 직전 RN 에서 실제 섹션을 읽어 오므로 편집을 열지 않는다.
    """
    from app.pipeline import rn_lookup as RNL

    comps = st.get("rn_components", {}) or {}
    editable = bool(st.get("rn_first_release")) and enabled
    fields: list[dict] = []
    for disp, key, multiline, req in input_fields_for(st):
        cur = st.get(key) if key in _TOP_LEVEL_KEYS else comps.get(key)
        fields.append({"key": key, "label": disp, "multiline": multiline,
                       "required": req, "value": str(cur or "")})
    box = InputFieldBox(fields, editable)

    # 이 화면에서 값을 고치면 문서의 판(◼)이 어떻게 되는지 그 자리에서 보여준다.
    from app.pipeline.rn_research import component_version as _cver

    _vbase = (st or {}).get("rn_component_vers") or {}
    _vnow = str((st or {}).get("version_full")
                or (st or {}).get("release_version")
                or (st or {}).get("os_version") or "")

    def _ver_of(key: str, value) -> str:
        if not str(value or "").strip():
            return "—"
        return _cver(_vbase, key, value, _vnow).get("version") or _vnow

    body = ui.column().classes("w-full gap-1")

    def _row(f: dict) -> None:
        with body:
            with ui.row().classes("w-full items-start gap-2 no-wrap") as holder:
                # **모든 칸이 여러 줄을 받는다**(사용자 확정 2026-09-01). 예전에는 항목
                # 목록의 multiline 표시로 한 줄/여러 줄 칸을 갈랐는데, 그 표시는 값을
                # 보기 전에 STANDARD_SECTIONS 에 박아 둔 고정값이고 값은 rn_components
                # 에서 따로 온다. 둘이 어긋나면 여러 줄 값이 한 줄 칸에 들어가는데,
                # HTML 한 줄 입력칸은 줄바꿈을 **지워 버린다** — 실측 job-15 touch 가
                # 'Firmware: 45.T.0DFirmware: 45.T.28' 로 붙어 보였고, 그 상태로 저장하면
                # 줄바꿈이 영구히 사라졌다. 칸을 전부 여러 줄로 두면 이 어긋남 자체가
                # 없어지고, 값이 하나뿐인 항목에도 사람이 줄을 더할 수 있다.
                el = ui.textarea(f["label"], value=f["value"]) \
                    .classes(f"flex-grow {AUTOFIT_CLASS}")
                # autogrow 는 접힌 expansion 안에서 mount 되면 높이를 잘못 잰다
                # (이 화면의 입력칸이 바로 그 안에 있다) — 줄 수로 최소 높이를 박아 둔다.
                el.props("autogrow").style(note_min_height(f["value"]))
                el.set_enabled(enabled)
                if on_change:
                    el.on_value_change(on_change)
                f["el"] = el
                # 문서의 ◼ 줄에 찍힐 판. 값이 그대로면 옛 판, 고치면 이번 판으로
                # 바뀐다(사용자 확정 2026-09-03). 문서 생성과 **같은 함수**를 쓴다.
                _vl = ui.label("").classes("text-caption text-grey-7 q-mt-md")                     .style("min-width:9rem; white-space:nowrap")                     .tooltip("문서에서 이 항목 위에 찍히는 판(◼). "
                             "값을 고치면 이번 판으로 바뀝니다.")

                def _sync_ver(_e=None, _f=f, _lb=_vl) -> None:
                    _lb.set_text("◼ " + _ver_of(_f["key"], _f["el"].value))
                _sync_ver()
                el.on_value_change(_sync_ver)
                if editable:
                    def _drop(_e=None, _f=f, _h=holder) -> None:
                        box.fields.remove(_f)
                        _h.delete()
                        if on_change:
                            on_change()
                    ui.button(icon="close", on_click=_drop) \
                        .props("flat dense round size=sm color=grey-7").classes("q-mt-sm") \
                        .tooltip("이 항목을 뺍니다 (이 제품 RN 에 없는 섹션일 때)")

    for f in fields:
        _row(f)

    if editable:
        with ui.row().classes("w-full items-center gap-2 no-wrap q-mt-xs"):
            name_in = ui.input(label="항목 추가", placeholder="예: Scanner version information") \
                .classes("flex-grow").props("dense")
            # '여러 줄' 선택칸은 없앴다 — 이제 모든 칸이 여러 줄을 받는다(2026-09-01).

            def _add() -> None:
                # 번호는 붙이지 않는다 — 문서에서 Word 가 매긴다. 사람이 이름에 번호를
                # 적어 넣었으면 그것도 떼어 낸다(문서 번호와 겹쳐 '8. 8.' 이 된다).
                label = strip_section_no(name_in.value or "")
                if not label:
                    ui.notify("항목 이름을 입력하세요.", color="warning")
                    return
                key = RNL.section_key(label) or "extra"
                used = {f["key"] for f in box.fields}
                base, n = key, 2
                while key in used:
                    key, n = f"{base}_{n}", n + 1
                f = {"key": key, "label": label, "multiline": False,
                     "required": False, "value": ""}
                box.fields.append(f)
                _row(f)
                name_in.value = ""
                if on_change:
                    on_change()

            ui.button("추가", icon="add", on_click=_add).props("outline dense size=sm no-caps")
        ui.label(
            "최초 버전이라 참고할 이전 RN 이 없습니다 — 이 제품에 없는 항목은 빼고, "
            "빠진 항목은 더해 주세요. 저장하면 이 목록이 이 작업의 입력 항목이 됩니다."
        ).classes("text-caption text-grey-6")
    return box


@ui.page("/reviews")
def mid_review_page():
    apply_theme()
    with page_header("RN 승인", max_w="max-w-[92rem]"):
        _render()


class _JobTask:
    """제품 작업(/osupdate)을 메일 작업과 같은 모양으로 감싼 것.

    이 화면은 작업에서 `task_id`(파이프라인 상태 키)와 `model`(표시용 이름)만 쓴다.
    """

    def __init__(self, job: dict) -> None:
        self.task_id = job["state_key"]          # 'job-N'
        self.model = job.get("product") or ""


def _pending_items() -> list[dict]:
    """RN 승인이 요청된 작업 목록.

    두 갈래에서 온다 — 메일 짝(Submission↔Approval)과 /osupdate 의 제품 작업.
    승인 요청 상태는 둘 다 파이프라인 상태에 같은 키로 저장되므로 판정은 동일하다.
    """
    from app import os_update_jobs as JOBS

    out: list[dict] = []
    with get_session() as db:
        mails = db.query(CachedMail).all()
    tasks = list(build_tasks(mails)) + [_JobTask(j) for j in JOBS.list_jobs()]
    for t in tasks:
        st = get_state(t.task_id) or {}
        review = st.get(MID_STATE_KEY) or {}
        if review.get("status") in ("requested", "approved"):
            out.append({"task": t, "state": st, "review": review})
    return out


def _render() -> None:
    ui.label("RN 승인 — Release Note").classes("text-h6")
    ui.label(
        "내용을 확인하시고 승인을 부탁드립니다. 승인하시면 History 관리용 RN(전체 이력본)이 "
        "확정·저장됩니다. 고객 배포용 RN for Sales 는 이후 최종 승인자 검수를 따로 거칩니다."
    ).classes("text-caption text-grey-7")

    if not can_mid_decide():
        ui.label("RN 승인자 전용 페이지입니다.").classes("text-red-7")
        return

    @ui.refreshable
    def items():
        pending = _pending_items()
        if not pending:
            ui.label("승인 요청된 항목이 없습니다.").classes("text-grey-7")
            return
        for it in pending:
            _render_item(it, on_change=items.refresh)

    items()


def _render_item(it: dict, on_change) -> None:
    t, st, review = it["task"], it["state"], it["review"]
    mail_id = t.task_id
    product = st.get("product") or t.model
    version = st.get("version_full") or st.get("os_version") or ""
    status = review.get("status") or "none"
    _color = {"requested": "orange-8", "approved": "green-8"}.get(status, "grey-6")
    _label = {"requested": "승인 대기", "approved": "승인 완료"}.get(status, status)

    exp = ui.expansion().classes("w-full").props("expand-separator")
    with exp.add_slot("header"):
        with ui.row().classes("w-full items-center gap-3 no-wrap"):
            ui.label(f"{product} {version}".strip()).classes("text-subtitle1").style(
                "font-weight:700; min-width:14rem")
            ui.badge(_label, color=_color)
            ui.label(
                f"요청자 {review.get('requester_name', '')} · "
                f"{str(review.get('requested_at', ''))[:16].replace('T', ' ')}"
            ).classes("text-caption text-grey-7")

    with exp:
        _render_detail(mail_id, st, review, status, on_change)


def _render_readonly(mail_id, st, review, status) -> None:
    """승인 완료된 항목 — 파일과 메모만 열람(편집 불가)."""
    with ui.row().classes("items-center gap-2"):
        ui.badge("승인 완료", color="green-8")
        ui.label(f"처리자 {review.get('decided_by', '')} · "
                 f"{str(review.get('decided_at', ''))[:16].replace('T', ' ')}").classes(
            "text-caption text-grey-7")

    # 1) RN 문서 — 승인 뒤에도 화면에서 바로 확인할 수 있게 미리보기로 보여준다.
    #    **전체 이력본**이다 — 이 결재가 확정하는 문서가 그것이다(2026-08-27).
    ui.separator()
    render_rn_preview(mail_id, height="78vh", which="full")

    # 2) 메모
    ui.separator()
    ui.label("메모").classes("text-subtitle2")
    note = (review.get("note") or "").strip()
    ui.label(note or "(메모 없음)").classes(
        "text-body2" if note else "text-caption text-grey-6").style("white-space:pre-wrap")


def _preview_iframe(mail_id, height: str, version: str, which: str = "sales") -> str:
    # version 을 붙여 **브라우저 캐시를 우회**한다 — 문서를 다시 만들면 같은 주소라도
    # 새로 받아와야 미리보기가 갱신된다.
    return (f'<iframe src="/rn/preview/{mail_id}?doc={which}&v={version}" '
            'title="Release Note 미리보기" '
            f'style="width:100%; height:{height}; border:1px solid #d5d8d2; '
            'border-radius:8px; background:#fff"></iframe>')


def render_rn_preview(mail_id, *, height: str = "70vh", which: str = "sales"):
    """RN 문서를 **PDF 로 화면에 바로** 띄운다(검수용).

    작업 문서는 docx 라 브라우저가 못 연다. iframe 이 /rn/preview/{mail_id} 를 직접
    불러가고 변환은 그 라우트에서 한다. (예전에는 ui.timer 로 나중에 채웠는데,
    목록이 갱신되면 부모 슬롯이 먼저 사라져 'parent slot has been deleted' 로 깨졌다.)
    첫 요청은 Word 변환 때문에 몇 초 걸리고, 문서가 그대로면 이후는 즉시 뜬다.

    which="full" 이면 **전체 이력 RN** 을 띄운다 — 전체 이력 RN 제작 단계와 RN 승인
    화면이 쓴다. 1차 승인이 확정하는 문서가 이력본이라, 승인 대상 문서를 그대로 본다.
    """
    _st = get_state(mail_id) or {}
    _full = which == "full"
    _docx = _st.get("rn_full_docx_path" if _full else "rn_docx_path", "")
    _first = bool(_st.get("rn_first_release")) and not (_docx and os.path.exists(_docx))
    _title = "전체 이력 RN" if _full else "RN for Sales"
    with ui.row().classes("w-full items-center gap-2 no-wrap"):
        ui.label(f"{_title} (최초 버전)" if _first else _title).classes("text-subtitle2")
        ui.label("최초 버전이라 참고할 이전 문서가 없습니다 — 아래 항목을 채워 주세요."
                 if _first else "문서가 바뀐 뒤 처음 열 때는 몇 초 걸립니다.") \
            .classes("text-caption text-grey-6")
        ui.space()
        if _docx and os.path.exists(_docx):
            ui.button("docx 다운로드", icon="download",
                      on_click=lambda p=_docx: ui.download(p)) \
                .props("flat dense size=sm")
    # sanitize=False 필수 — 기본값(True)이면 DOMPurify 가 <iframe> 을 지워 아무것도 안 뜬다.
    # (내용은 우리가 만든 고정 문자열이고 mail_id 는 작업 키라 주입 위험이 없다.)
    # 처음 버전 값은 **문서의 수정 시각**으로 잡는다 — 화면을 닫았다 다시 열었을 때
    # 브라우저가 예전 미리보기를 그대로 보여주지 않도록.
    try:
        _v0 = str(int(os.path.getmtime(_docx))) if _docx and os.path.exists(_docx) else "0"
    except OSError:
        _v0 = "0"
    el = ui.html(_preview_iframe(mail_id, height, _v0, which), sanitize=False) \
        .classes("w-full")

    def reload() -> None:
        """문서를 다시 만든 뒤 호출 — 미리보기를 새로 받아온다."""
        el.set_content(_preview_iframe(mail_id, height, str(int(time.time())), which))

    return reload


def _finalize_after_approval(mail_id, user_id: int, actor: str) -> dict:
    """**1차(RN 승인)** 승인 시 — History 관리용 RN(전체 이력본)을 확정·저장한다.

    RN 승인은 '일반 History 관리용 RN' 을 만드는 결재다(사용자 확정 2026-08-04).
      1) 전체 이력본을 PDF 로 확정
      2) 제품의 개발 Release Note 경로(.104)에 저장
    **단계는 아직 끝내지 않는다** — 이 뒤에 작성자가 선별표에서 고르고 편집해
    RN for Sales 를 만들고, 최종 승인자 검수를 받아야 단계가 완료된다.

    **Word 변환·파일서버 접근이 섞이므로 반드시 스레드에서 부른다.**
    파일서버 저장이 실패해도 승인은 이미 끝난 것으로 둔다 — 결재가 파일서버 사정에
    묶이면 안 된다. 대신 사유를 화면에 알린다.
    """
    import os as _os

    from app.pipeline.rn_research import archive_full_rn, finalize_full_pdf

    notes: list[tuple[str, str]] = []

    pdf, err = finalize_full_pdf(mail_id)
    if pdf:
        notes.append((f"전체 이력 RN PDF 확정: {_os.path.basename(pdf)}", "positive"))
    else:
        notes.append((f"전체 이력 RN PDF 확정 실패 — {err}", "negative"))

    res = archive_full_rn(user_id, mail_id)
    if res.get("saved"):
        notes.append((f"전체 이력 RN 저장 ({len(res['saved'])}개) → {res.get('dir', '')}\n"
                      + "\n".join(_os.path.basename(p) for p in res["saved"]), "positive"))
    if res.get("skipped"):
        notes.append(("전체 이력 RN 미저장 — " + " / ".join(res["skipped"]), "warning"))

    notes.append(("다음: 선별표에서 공개할 항목을 고르고 편집해 RN for Sales 를 만든 뒤 "
                  "최종 승인자 검수를 요청하세요.", "info"))

    # 1차 승인이 곧 **전체 이력 RN 제작 단계의 끝**이다(2026-08-27 단계 분리).
    # 승인자 화면에는 아무 것도 더 띄우지 않는다 — 승인자가 하는 일은 그대로여야 한다.
    # 실패했을 때만 알린다(단계가 진행 중으로 남아 작성자가 다음 단계를 못 찾는다).
    from app.task_store import complete_stage

    if not complete_stage(mail_id, "release_note_full", actor):
        notes.append(("전체 이력 RN 제작 단계를 완료 표시하지 못했습니다 — "
                      "작업 화면에서 확인해 주세요.", "warning"))
    return {"notes": notes}


def _finalize_after_final_approval(mail_id, user_id: int, actor: str) -> dict:
    """**2차(최종 승인자 검수)** 승인 시 — RN for Sales 를 확정·저장하고 단계를 끝낸다.

      1) RN for Sales 를 PDF 로 확정
      2) 제품의 RN for Sales 경로(.160)에 저장 — 이력본과 **다른 폴더**다
      3) Release Note 제작 단계를 완료 처리

    스레드에서 부른다(Word 변환·파일서버 접근).
    """
    import os as _os

    from app.pipeline.rn_research import archive_sales_rn, finalize_rn_pdf
    from app.task_store import complete_stage

    notes: list[tuple[str, str]] = []

    pdf, err = finalize_rn_pdf(mail_id)
    if pdf:
        notes.append((f"RN for Sales PDF 확정: {_os.path.basename(pdf)}", "positive"))
    else:
        notes.append((f"RN for Sales PDF 확정 실패 — {err}", "negative"))

    sres = archive_sales_rn(user_id, mail_id)
    if sres.get("saved"):
        notes.append((f"RN for Sales 저장 ({len(sres['saved'])}개) → {sres.get('dir', '')}\n"
                      + "\n".join(_os.path.basename(p) for p in sres["saved"]), "positive"))
    if sres.get("skipped"):
        notes.append(("RN for Sales 미저장 — " + " / ".join(sres["skipped"]), "warning"))

    if complete_stage(mail_id, "release_note", actor):
        notes.append(("Release Note 제작 단계를 완료 처리했습니다.", "positive"))
    else:
        notes.append(("단계를 찾지 못해 완료 표시를 하지 못했습니다 — 작업 화면에서 확인해 주세요.",
                      "warning"))
    return {"notes": notes}


def _render_detail(mail_id, st, review, status, on_change) -> None:
    # 이미 승인된 항목은 RN 승인자에게 **파일과 메모만** 열람으로 보여준다.
    # (표·입력·재생성·업로드는 숨긴다 — 처리 후 다시 편집하지 못하도록.)
    if status == "approved":
        _render_readonly(mail_id, st, review, status)
        return

    # 1) RN 문서 — 화면에서 바로 보이는 PDF 미리보기.
    #    (예전에는 파일명만 적어 뒀는데, 그것만으로는 승인 여부를 판단할 수 없다.)
    #    승인 대상은 **전체 이력본**이므로 그 문서를 띄운다 — 예전에는 이번 판만 담은
    #    RN for Sales 를 보여 주고 이력본을 확정했다(어긋나 있었다, 2026-08-27).
    reload_preview = render_rn_preview(mail_id, which="full")

    # 선별표·입력값을 고치면 문서와 어긋난다. '저장' 으로 문서를 맞추기 전에는
    # 승인을 막는다(어긋난 문서가 그대로 확정되지 않도록).
    dirty = {"v": False}
    dirty_marks: list = []          # 변경 안내 라벨
    approve_btns: list = []         # 변경 중에는 잠글 버튼

    def _sync_dirty() -> None:
        for lb in dirty_marks:
            lb.set_visibility(dirty["v"])
        for btn in approve_btns:
            btn.set_enabled(not dirty["v"])

    def _mark_dirty(*_a) -> None:
        if not dirty["v"]:
            dirty["v"] = True
            _sync_dirty()

    # 2) 공개 선별표 — [반영/미반영] 토글 + [수정 내용]. 반영=노란 하이라이트(동적), 민감=주황 테두리+⚠.
    ui.separator()
    # 화면에 놓기 전에 불변식을 한 번 통과시킨다 — 기본값은 반영, '수정 내용'은 빈칸 금지.
    # (예전에 저장된 상태에도 같은 규칙이 걸리도록 읽는 자리에서 건다.)
    # edited 는 '사람이 이 표를 한 번이라도 저장했는가' — 사람이 정한 미반영은 건드리지
    # 않고, 아무도 안 건드린 표의 옛 기본값(미판정=미반영)만 되돌린다.
    rows = _norm_sel_rows(st.get("rn_select_rows"),
                          edited=bool(st.get("rn_select_edited_at")))
    # 보안패치 줄·뭉침 줄도 표에 올린다 — 예전에는 코드가 문서에 바로 끼워 넣어서 사람이
    # 뺄 수도 문구를 고칠 수도 없었다(사용자 확정 2026-08-26).
    from app.pipeline.rn_research import with_system_rows as _with_sys

    rows = _with_sys(st, rows)
    applies: dict = {}   # no -> 반영/미반영 토글
    notes: dict = {}     # no -> 수정 내용 입력
    # 열 순서: 번호·이슈 제목·[반영]·작성자·고객·redmine·[수정 내용]. 수정 내용은 남는 너비 전부.
    _HDR = (("번호", "3rem"), ("이슈 제목", "16rem"), ("반영", "9rem"),
            ("작성자", "5rem"), ("고객", "6rem"), ("redmine", "6rem"), ("수정 내용", ""),
            ("", "2.5rem"))   # 맨 끝 = 직접 추가한 행의 삭제 버튼 자리

    def _w(i: int) -> str:
        w = _HDR[i][1]
        if not w:   # 수정 내용 — 남는 너비 전부
            return "flex:1 1 26rem; min-width:26rem; white-space:normal; word-break:break-word"
        return f"width:{w}; flex:0 0 {w}; white-space:normal; word-break:break-word"

    def _row_style(active: bool) -> str:
        # 반영이면 노란 하이라이트, 미반영이면 없음. (민감 구분선 등 별도 표시는 넣지 않는다.)
        s = "border-top:1px solid #eceeea;"
        if active:
            s += "background:#fff7d6;"
        return s

    ui.label("공개 RN 선별표 — '반영'한 항목(노란색)이 RN 에 들어갑니다").classes("text-subtitle2")
    # live_rows = 화면에 실제로 놓인 행(저장 대상). '행 추가'로 늘어난다.
    live_rows: list[dict] = list(rows)
    count_lbl = ui.label("").classes("text-caption text-grey-7")

    def _sync_count() -> None:
        n_y = sum(1 for rr in live_rows if rr.get("include") == "Y")
        count_lbl.set_text(
            f"총 {len(live_rows)}건 · 반영 {n_y}건 / 미반영 {len(live_rows) - n_y}건")

    with ui.element("div").classes("w-full").style(
        "max-height:30rem; overflow:auto; border:1px solid #d5d8d2; border-radius:8px"
    ):
        with ui.row().classes("w-full items-center gap-2 no-wrap px-2 py-1").style(
            "background:#f0f1ef; position:sticky; top:0; z-index:1; font-weight:600"
        ):
            for i, (txt, _w0) in enumerate(_HDR):
                ui.label(txt).classes("text-caption").style(_w(i))
        tbody = ui.element("div").classes("w-full")

    def _add_row(r: dict, manual: bool = False) -> None:
        """행 하나를 표에 놓는다.

        · 자동 수집 행 = 반영/미반영 토글 + 읽기용 이슈 제목 + 수정 내용
        · 직접 추가한 행 = **수정 내용만** (항상 반영) + 삭제 버튼
        """
        no = str(r.get("no"))
        inc = (r.get("include") == "Y")
        with tbody:
            row_el = ui.row().classes("w-full items-start gap-2 no-wrap px-2 py-1").style(
                _row_style(inc))
        with row_el:
            ui.label(no).classes("text-caption q-pt-xs").style(_w(0))
            if manual:
                # 이슈가 없는 항목이라 제목·판정 칸을 두지 않는다(정렬만 맞춘다).
                ui.label("직접 추가").classes("text-caption text-grey-6 q-pt-xs").style(_w(1))
                for _i in (2, 3, 4, 5):
                    ui.label("").style(_w(_i))
            else:
                # 이슈 제목 열은 원본 문구만 그대로.
                ui.label(str(r.get("issue", ""))).classes("text-body2 q-pt-xs").style(_w(1))
                # 선택된 값만 진하게(파랑 채움), 나머지는 회색.
                tg = ui.toggle({"Y": "반영", "X": "미반영"}, value=("Y" if inc else "X")) \
                    .props("no-caps dense unelevated color=grey-5 toggle-color=primary") \
                    .style(_w(2))
                ui.label(str(r.get("team", ""))).classes("text-caption q-pt-xs").style(_w(3))
                ui.label(str(r.get("customer", ""))).classes(
                    "text-caption q-pt-xs").style(_w(4))
                _rm = str(r.get("redmine", "") or "")
                if _rm.startswith("http"):
                    ui.link("#" + _rm.rsplit("/", 1)[-1], _rm, new_tab=True) \
                        .classes("text-caption q-pt-xs").style(_w(5))
                else:
                    ui.label(_rm).classes("text-caption text-grey-7 q-pt-xs").style(_w(5))
            _v = str(r.get("apply_note", "") or "")
            ne = ui.textarea(value=_v, placeholder="수정 필요 시 입력") \
                .classes(AUTOFIT_CLASS) \
                .props('dense outlined autogrow '
                       f'input-style="{note_min_height(_v)}"').style(_w(6))
            ne.set_enabled(manual or inc)
            ne.on_value_change(_mark_dirty)

            if manual:
                def _remove(el=row_el, row=r, key=no):
                    live_rows[:] = [x for x in live_rows if x is not row]
                    notes.pop(key, None)
                    el.delete()
                    _sync_count()
                    _mark_dirty()

                ui.button(icon="delete", on_click=_remove) \
                    .props("flat dense size=sm color=negative").style(_w(7))
            else:
                ui.label("").style(_w(7))

                def _on_toggle(e, target=ne, el=row_el, row=r):
                    active = (e.value == "Y")
                    # 반영으로 바꾸면 '수정 내용'이 비어 있지 않게 공개 문구로 채운다
                    # (반영 항목은 RN 에 실릴 문구가 항상 보여야 한다).
                    if active and not (target.value or "").strip():
                        target.value = str(row.get("public", "") or "")
                    target.set_enabled(active)
                    el.style(replace=_row_style(active))
                    row["include"] = "Y" if active else "X"
                    _sync_count()
                    _mark_dirty()

                tg.on_value_change(_on_toggle)
                applies[no] = tg
        notes[no] = ne

    for _r in rows:
        _add_row(_r, manual=bool(_r.get("manual")))
    if not rows:
        ui.label(
            "선별 데이터가 없습니다. 작성자가 Release Note 제작을 실행하면 채워지고, "
            "아래 '행 추가'로 직접 넣을 수도 있습니다."
        ).classes("text-caption text-grey-6")
    _sync_count()

    def _new_row() -> None:
        """이슈에 없는 항목을 RN 에 넣을 때 — 빈 행을 하나 더 만든다."""
        nums = [int(x.get("no")) for x in live_rows
                if str(x.get("no", "")).isdigit()]
        r = {"no": (max(nums) + 1) if nums else 1, "issue": "", "team": "",
             "customer": "", "gubun": "", "include": "Y", "reason": "직접 추가",
             "public": "", "redmine": "", "sensitive": False, "apply_note": "",
             "manual": True}
        live_rows.append(r)
        _add_row(r, manual=True)
        _sync_count()
        _mark_dirty()

    ui.button("행 추가", icon="add", on_click=_new_row) \
        .props("outline dense size=sm no-caps").classes("q-mt-xs") \
        .tooltip("Redmine 이슈에 없는 항목을 RN 에 직접 넣습니다")
    # 3) 입력 요청 — RN 템플릿 섹션 번호를 붙여 1열로. 전부 필수.
    ui.separator()
    ui.label("입력 요청").classes("text-subtitle2")
    ui.label(
        "비워 두어도 저장·전송과 RN 재생성은 됩니다. 비운 칸은 RN 에 빈칸으로 나갑니다. "
        "직전 버전 RN 에서 가져온 값이 있으면 미리 채워져 있습니다."
    ).classes("text-caption text-grey-7")
    comps = st.get("rn_components", {}) or {}
    # 이 제품 RN 의 실제 섹션(없으면 기본 목록). 최초 릴리즈면 항목을 지우고 더할 수 있다.
    box = render_input_fields(mail_id, st, on_change=_mark_dirty)

    # 4) 반영 — docx 재생성 / docx 교체 업로드
    ui.separator()
    ui.label("문서에 반영").classes("text-subtitle2")

    def _collect() -> dict | None:
        """체크·입력값을 상태에 저장. 필수 항목이 비어 있으면 저장하지 않고 None 반환."""
        # 3~8 항목이 비어 있어도 **저장/전송과 RN 재생성을 막지 않는다** — 단말에서 확인해야
        # 알 수 있는 값이라 나중에 채우는 경우가 많다. 대신 무엇이 비었는지 알려 준다
        # (비운 채로 만들면 RN 의 해당 칸이 빈칸으로 나간다).
        missing = box.missing()
        if missing:
            ui.notify(
                "빈 항목이 있는 채로 저장합니다 — " + ", ".join(missing),
                color="warning", multi_line=True,
            )
        new_rows = []
        for r in live_rows:
            r2 = dict(r)
            no = str(r.get("no"))
            ne = notes.get(no)
            if ne is not None:
                # 사람이 친 문구는 **그대로** 저장한다 — [모듈] 태그를 붙이지 않는다
                # (사용자 확정 2026-08-27).
                r2["apply_note"] = (ne.value or "").strip()
            if r2.get("manual"):
                r2["include"] = "Y"                 # 직접 추가한 행은 항상 반영
                r2["issue"] = r2.get("apply_note", "")   # 표시용(제목 칸이 따로 없다)
                if not r2["apply_note"]:
                    continue                        # 빈 행은 저장하지 않는다
            else:
                tg = applies.get(no)
                r2["include"] = "Y" if (tg and tg.value == "Y") else "X"
            new_rows.append(r2)
        # 저장 직전에도 불변식을 건다 — 사람이 '수정 내용'을 지운 채 저장해도 빈칸으로
        # 남지 않는다(공개 문구 → 이슈 제목 순으로 되채운다).
        new_rows = _norm_sel_rows(new_rows)
        vals = box.values()
        comp_vals = {k: v for k, v in vals.items() if k not in _TOP_LEVEL_KEYS}
        top_vals = {k: v for k, v in vals.items() if k in _TOP_LEVEL_KEYS}
        # 기존 값을 지우지 않도록 **병합**해서 저장한다. 단, 화면에서 **뺀 항목**의 값은
        # 같이 걷어낸다 — 안 그러면 지운 섹션이 문서에 그대로 남는다.
        merged = {k: v for k, v in {**comps, **comp_vals}.items()
                  if k not in box.dropped()}
        # 사람이 **값을 바꾼** 항목만 수동으로 표시한다 — 표시가 붙으면 ②를 다시 돌려도
        # 그 값은 덮이지 않는다(자동으로 채운 값은 더 나은 출처가 생기면 갱신된다).
        from app.pipeline.rn_research import mark_manual_components

        mark_manual_components(mail_id, merged, top_vals)
        # 사람이 반영/미반영을 손댔다는 표시 — **이 화면에도 반드시 남긴다**.
        # 예전에는 검수 팝업(rn_edit_table)에서만 찍어서, RN 승인자가 여기서 미반영으로
        # 바꿔도 그 사실이 남지 않았다. 그러면 ②를 다시 돌릴 때 carry 가 비어(2153행)
        # 선별표가 기본값인 **전부 반영(Y)** 으로 다시 깔리고, 뺐던 이슈가 문서에 되돌아온다.
        update_state(mail_id, rn_select_rows=new_rows, rn_components=merged,
                     rn_select_edited_at=datetime.now().isoformat(),
                     rn_select_edited_by=current_username() or "", **top_vals)
        box.save_fields(mail_id)
        return {"rows": new_rows, "components": comp_vals}

    async def do_regen() -> None:
        """선별표·입력값을 저장하고 RN 을 다시 만든 뒤 미리보기를 새로 받아온다."""
        if _collect() is None:
            return
        n = ui.notification("저장 · RN 재생성 중…", spinner=True, timeout=None)
        _ko: list[str] = []
        try:
            from app.pipeline.rn_research import (
                korean_lines,
                rebuild_revision_from_selection,
                regenerate_rn,
            )

            new_lines, rnote = await run.io_bound(rebuild_revision_from_selection, mail_id)
            if rnote:   # 재구성 실패 시 기존 내용 유지
                new_lines = list((get_state(mail_id) or {}).get("rn_revision_lines") or [])
            # 이 화면이 확정하는 문서는 **전체 이력본** 하나다. RN for Sales 는 다음
            # 단계에서 만들어지므로 여기서 함께 만들지 않는다(2026-08-27).
            await run.io_bound(_partial(regenerate_rn, with_sales=False), mail_id,
                               "\n".join(f"- {ln}" for ln in new_lines))
            _ko = korean_lines(new_lines)
            H.record(mail_id, "rn", "RN 승인 재생성", actor=current_username(),
                     summary="선별표·입력값 수정 후 재생성",
                     text=H.text_from_rn(get_state(mail_id) or {}))
        finally:
            n.dismiss()
        dirty["v"] = False
        _sync_dirty()
        reload_preview()
        ui.notify("저장했습니다. RN 을 다시 만들었으니 위 미리보기에서 확인하세요.",
                  color="positive")
        if _ko:
            ui.notify(
                f"RN 본문에 한국어가 {len(_ko)}줄 있습니다 — 영어로 고쳐 주세요.\n"
                + "\n".join(f"· {ln[:70]}" for ln in _ko[:3]),
                color="warning", multi_line=True, timeout=12000,
            )

    with ui.row().classes("w-full items-center gap-2 no-wrap"):
        ui.button("저장", icon="save", on_click=do_regen).props("color=primary") \
            .tooltip("선별표·입력값을 저장하고 그 내용으로 RN 을 다시 만듭니다")
        _dm = ui.label("수정한 내용이 문서에 아직 반영되지 않았습니다 — 저장해야 승인할 수 있습니다.") \
            .classes("text-caption text-orange-9")
        _dm.set_visibility(False)
        dirty_marks.append(_dm)

    # 5) 승인 결과 — 승인/반려. 어느 쪽이든 선별·입력은 저장하고 RN 을 다시 만든다
    #    (승인자가 채운 값을 잃지 않게). 다른 점은 이후 흐름과 작성자에게 가는 메일이다.
    #      · 승인 → 문서 확정 · 파일서버 저장 · 단계 완료.
    #      · 반려 → 작성자에게 돌아간다(작성자가 고쳐 다시 승인 요청).
    ui.separator()
    ui.label("승인 결과").classes("text-subtitle2")
    if status != "requested":
        ui.label(
            f"이미 처리된 항목입니다 · 처리자 {review.get('decided_by', '')} "
            f"{str(review.get('decided_at', ''))[:16].replace('T', ' ')}"
        ).classes("text-caption text-grey-7")
        return
    if not can_mid_decide():
        return

    note_in = ui.textarea("메모 — 작성자에게 함께 전달 (반려 시 사유 필수)").classes("w-full")

    async def do_decide(approve: bool):
        # 고친 내용이 문서에 반영되기 전에는 승인할 수 없다(버튼도 잠겨 있지만 이중 확인).
        if approve and dirty["v"]:
            ui.notify("수정한 내용이 문서에 반영되지 않았습니다. '저장' 을 먼저 눌러 주세요.",
                      color="negative", multi_line=True)
            return
        # 업로드본이 정본이 아니면 필수 입력을 검증하고 선별·입력값을 상태에 저장한다.
        if _collect() is None:
            return
        req_email = (review.get("requester_email") or "").strip()
        product = st.get("product") or ""
        version = st.get("version_full") or st.get("os_version") or ""
        note = (note_in.value or "").strip()
        kind = "승인" if approve else "반려"
        if not approve and not note:
            ui.notify("반려 사유를 메모에 적어 주세요. 작성자가 무엇을 고쳐야 할지 알 수 있습니다.",
                      color="negative", multi_line=True)
            return
        # 세션에서 읽는 값은 **UI 스레드에서 미리** 확보한다(워커 스레드에선 접근 불가).
        uid = current_user_id()
        actor = current_username()
        cc_list = user_mailer.mid_reviewer_emails()
        n = ui.notification(f"{kind} 처리 중…", spinner=True, timeout=None)
        mail_err = ""
        try:
            # 문서는 '저장' 에서 이미 만들어 둔 것을 그대로 쓴다(여기서 또 만들지 않는다).
            # 승인은 dirty 일 때 막히므로, 이 시점의 문서는 화면의 선별표·입력값과 같다.
            H.record(mail_id, "rn", f"RN 승인 {kind}", actor=current_username(),
                     summary=note or "승인 결과 기록",
                     text=H.text_from_rn(get_state(mail_id) or {}))
            # ③ RN 승인 반영본을 **기록으로 별도 파일 고정**(누적 보관 · 최종본 아님).
            #    이 결재가 확정하는 문서는 **전체 이력본**이라 그 판을 남긴다(2026-08-27).
            #    승인본만 남긴다 — 반려는 아직 확정된 판이 아니다.
            if approve:
                try:
                    import os as _os2
                    import shutil as _sh
                    _stx = get_state(mail_id) or {}
                    _src = _stx.get("rn_full_docx_path") or _stx.get("rn_docx_path") or ""
                    if _src and _os2.path.exists(_src):
                        _dst = _os2.path.join(_os2.path.dirname(_src),
                                              f"midreviewed_{_os2.path.basename(_src)}")
                        _sh.copyfile(_src, _dst)
                        update_state(mail_id, rn_mid_reviewed_path=_dst,
                                     rn_mid_reviewed_name=_os2.path.basename(_dst))
                except Exception:  # noqa: BLE001
                    pass
            # 승인 → History 관리용 RN(전체 이력본)이 확정된다. **마지막 결재가 아니다** —
            # 그 뒤 작성자가 RN for Sales 를 만들어 최종 승인자 검수를 받아야 단계가 끝난다.
            # 반려 → 작성자에게 되돌린다(작성자 화면의 잠금이 풀리고
            # 'RN 승인자에게 승인 요청' 버튼이 다시 나온다).
            update_state(mail_id, **{MID_STATE_KEY: {
                **review, "status": "approved" if approve else "rejected",
                "decided_by": current_username(), "decided_at": datetime.now().isoformat(),
                "note": note,
            }})
            if approve:
                finish = await run.io_bound(_finalize_after_approval, mail_id, uid, actor)
            # 작성자에게 회신 알림 — **결정을 저장한 뒤** 보낸다. 다른 RN 승인자는 참조로
            # 넣어 누가 무엇을 처리했는지 공유되게 한다(겹치는 주소는 send_as_user 가 거른다).
            # 메일이 실패해도 결정은 남긴다 — 예전에는 발송이 먼저라, 실패하면 승인 자체가
            # 사라졌다(실측: 워커 스레드에서 세션 접근 오류로 매번 실패).
            if req_email:
                from app import mail_templates as MT

                subject, body = MT.render(
                    "mid_review_result", 제품=product, 버전=version, 결과=kind,
                    처리자=actor, 메모이름="메모" if approve else "반려 사유",
                    메모=note or "-",
                    다음안내=(
                        "▶ 확인한 내용으로 History 관리용 RN(전체 이력본)을 PDF 로 확정하고 "
                        "파일서버에 저장했습니다.\n"
                        "  이어서 공개할 항목을 선별·편집한 RN for Sales 가 최종 승인자 검수를 "
                        "거쳐 확정됩니다."
                        if approve else
                        "▶ 입력한 내용은 저장했습니다. 위 사유를 확인해 수정하신 뒤 "
                        "다시 RN 승인을 요청해 주시기 바랍니다."))
                subject = subject.strip()
                # uid·cc 는 **UI 스레드에서 미리** 구해 둔다 — run.io_bound 안에서
                # app.storage.user 에 접근하면 'can only be used within a UI context' 로 터진다.
                try:
                    # cc 는 키워드 전용 인자다. run.io_bound 는 kwargs 를 그대로 넘겨준다.
                    ok, err = await run.io_bound(
                        user_mailer.send_as_user, uid, req_email, subject, body,
                        cc=cc_list)
                    if not ok:
                        mail_err = err or "알 수 없는 오류"
                except Exception as e:  # noqa: BLE001
                    mail_err = f"{type(e).__name__}: {e}"
        finally:
            n.dismiss()
        ui.notify(f"{kind} 처리 완료", color="positive" if approve else "warning")
        if approve:
            for msg, color in (finish or {}).get("notes", []):
                ui.notify(msg, color=color, multi_line=True, timeout=12000)
        if mail_err:
            ui.notify(f"결정은 저장했지만 알림 메일 발송에 실패했습니다 — {mail_err}",
                      color="warning", multi_line=True, timeout=12000)
        on_change()

    with ui.row().classes("items-center gap-2"):
        _ap = ui.button("승인", icon="check", on_click=lambda: do_decide(True)) \
            .props("color=positive").tooltip("문서를 확정하고 파일서버에 저장합니다")
        approve_btns.append(_ap)
        ui.button("반려", icon="close", on_click=lambda: do_decide(False)) \
            .props("color=negative").tooltip("작성자에게 사유와 함께 되돌립니다")
        _dm2 = ui.label("← 먼저 '저장'").classes("text-caption text-orange-9")
        _dm2.set_visibility(False)
        dirty_marks.append(_dm2)
    _sync_dirty()
