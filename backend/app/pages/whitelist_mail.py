"""/whitelist/mail — WhiteList 전용 메일함.

메일함은 서비스마다 따로 둔다(OS Update = /mail, WhiteList = 여기).
동기화는 공용 수신함 하나가 이미 모든 메일을 저장하므로, 이 화면은 저장된 메일에
**1차 룰 필터**(mail_filter)와 **2차 Claude 판별**(mail_triage)을 걸어 보여준다.

단위는 메일이 아니라 **스레드**다 — 한 요청이 회신으로 여러 통이 되기 때문이다.
처리한 스레드는 mail_state 에 기록해 다시 작업하지 않는다.
읽기 전용 — 메일을 고치거나 지우지 않는다.
"""
from __future__ import annotations

import re

from nicegui import run, ui

from app.auth import current_username
from app.whitelist import mail_filter as F
from app.whitelist import mail_state as MS
from app.whitelist import service as S
from app.whitelist import mail_triage as T
from app.pages import whitelist_verify as WV
from app.pages.whitelist_bulk import DATE_COLS, UPLOAD_COLS, date_rows, upload_rows

# 신청 화면으로 넘길 단말 정보를 담아두는 세션 키(/whitelist 가 읽어서 비운다).
# 값: [{"sn", "model", "cur", "upg"}] — Claude 판정에서 모델·OS 를 같이 실어 보낸다.
STAGED_SN_KEY = "wl_staged_sns"

_PREVIEW_CHARS = 400
_STATUS_KO = {"pending": "대기 중", "confirmed": "승인됨", "rejected": "거절됨",
              "deleted": "삭제됨", "handled": "수동 처리(등록 아님)"}


def _when(received_at: str) -> str:
    """수신 시각을 한국 시간으로 통일해 표시.

    원문 Date 헤더는 발신지 시간대가 그대로 붙어 있어(+0000·+0300 등) 그대로 보여주면
    최신순으로 정렬해도 목록이 뒤죽박죽으로 읽힌다.
    """
    from datetime import timedelta, timezone
    from email.utils import parsedate_to_datetime

    try:
        dt = parsedate_to_datetime(received_at or "")
        if dt is not None:
            return dt.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        pass
    return (received_at or "")[:25]


def _short(text: str, n: int = _PREVIEW_CHARS) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[:n] + " …"


def _threads(state: dict) -> list[dict]:
    """후보 메일 → 스레드 묶음. 판정으로 걸러 정렬한다."""
    # 룰은 등급을 매기지 않는다 — SN 신호가 있으면 전부 후보고, 요청 여부는 Claude 판정이 정한다.
    # 자동발송(Redmine 알림·뉴스레터)만 뺀다.
    rows = F.list_candidates(limit=400)
    groups = T.group_threads(rows)
    verdicts = T.cached_verdicts()
    # 이미 신청된 SN 을 한 번에 조회(중복 등록 방지의 최후 방어선 — 팝업에서 표시용)
    dup = S.existing_sns({sn for g in groups for sn in g["serials"]})
    processed = S.processed_mail_ids()   # 이미 등록 처리한 원본 메일 id
    done_recs = MS.all_records()         # 수동 '처리 완료' 표시(파일은 한 번만 읽는다)
    show_done = bool(state.get("show_done"))
    show_notwl = bool(state.get("show_notwl"))
    out = []
    for g in groups:
        g["verdict"] = verdicts.get(T._cache_id(g)) or {}
        g["dups"] = {sn: dup[sn] for sn in g["serials"] if sn in dup}
        # 현재 최신 메일(rep)에서 등록을 만들었으면 처리된 것으로 본다.
        # (최신 메일 기준이라, 처리 뒤 새 회신이 오면 rep 이 바뀌어 다시 목록에 뜬다.)
        g["processed"] = str(g["rep"]["id"]) in processed
        # 수동 '처리 완료' 는 **표시한 시점까지**를 뜻한다. 그 뒤에 새 회신이 오면
        # 다시 열린 것으로 본다 — 같은 제목으로 새 요청이 계속 오는 대화가 있어서다
        # (실측: 완료 표시한 Carema DE 스레드에 3주 뒤 등록일 변경 요청이 왔는데 안 떴다).
        rec = done_recs.get(g["key"])
        # **작업 아님**은 완료와 달리 시각을 따지지 않는다 — 회신이 와도 여전히 아니다.
        g["notwl"] = MS.is_notwl_rec(rec)
        g["manual_done"] = MS.is_done_rec(rec, T.newest_ts(g))
        g["reopened"] = bool(rec) and not g["manual_done"] and not g["notwl"]
        out.append(g)
    # Claude 가 '요청 아님' 으로 판정한 스레드는 뺀다(판정 전인 것은 남긴다).
    out = [g for g in out if not (g["verdict"] and not g["verdict"].get("is_request"))]
    # 처리 여부 = 등록(WLRequest 생성) 또는 수동 처리 완료 표시.
    # (SN 등록 여부가 아니라 메일 처리 여부로 판단 — 같은 SN 후속 요청이 새 메일로 오면
    #  그 메일은 아직 처리 전이라 다시 뜬다. 처리 뒤 새 회신이 와도 rep 이 바뀌어 다시 뜬다.)
    for g in out:
        g["done"] = g["processed"] or g["manual_done"]
    # 처리 완료는 기본 숨김 — '처리 완료 포함' 을 켜면 '처리 후' 배지와 함께 보여준다.
    if not show_done:
        out = [g for g in out if not g["done"]]
    # WhiteList 작업이 아니라고 표시한 것은 목록에서 뺀다 — '작업 아님 포함' 으로 꺼낸다.
    if not show_notwl:
        out = [g for g in out if not g["notwl"]]
    # 가장 최근 메일이 맨 위(수신 시각 기준 — id 는 도착 순서가 아니다)
    out.sort(key=lambda g: T.received_key(g["rep"]), reverse=True)
    return out


def _set_done(g: dict, done: bool, me: str, refresh) -> None:
    """스레드를 수동으로 처리 완료 표시(또는 다시 열기). OS Update 의 수동 상태 관리와 같은 취지.

    처리 완료 시 /whitelist '등록 내역'에도 이력(등록 아님)으로 남긴다. 다시 열면 그 이력을 지운다.
    (이력 기록 실패가 처리 완료 자체를 막지 않도록 예외는 삼킨다.)
    """
    MS.set_status(g["key"], MS.DONE if done else MS.OPEN, by=me,
                  note="수동 처리 완료" if done else "", rep_ts=T.newest_ts(g))
    try:
        rep = g["rep"]
        if done:
            addr, name = _sender_addr_name(rep.get("sender", ""))
            S.record_manual_done(
                mail={"mail_id": rep["id"], "requester_addr": addr,
                      "requester_name": name, "message_id": rep.get("message_id", "")},
                serials=list(g.get("serials") or []),
                processed_by=me, verdict=g.get("verdict") or {})
        else:
            S.remove_manual_done(rep["id"])
    except Exception:  # noqa: BLE001
        pass
    ui.notify("이 메일을 처리 완료로 표시했습니다(등록 내역에 기록 · 목록에서 숨김)."
              if done else "다시 열었습니다(등록 내역의 수동 처리 이력 삭제).",
              color="positive" if done else "info")
    refresh()


def _set_notwl(g: dict, flag: bool, me: str, refresh) -> None:
    """이 스레드를 **WhiteList 작업이 아님**으로 내리거나 되돌린다.

    WhiteList 요청이 아닌 메일이 목록에 섞여 들어온다(사용자 지적 2026-09-14). 판별이
    요청으로 본 것이라 '요청 아님' 자동 제외에 걸리지 않는다. 사람이 직접 내린다.

    '처리 완료(수동)' 와 두 가지가 다르다.
      · 등록 내역에 남기지 않는다 — 처리한 일이 아니라 **우리 일이 아닌** 메일이다.
      · 새 회신이 와도 되살아나지 않는다 — 회신이 와도 여전히 WhiteList 일이 아니다.
    되돌리려면 [작업 아님 포함] 을 켜고 [되돌리기] 를 누른다.
    """
    MS.set_status(g["key"], MS.NOTWL if flag else MS.OPEN, by=me,
                  note="WhiteList 작업 아님" if flag else "",
                  rep_ts=T.newest_ts(g))
    ui.notify("WhiteList 작업이 아닌 것으로 내렸습니다(목록에서 숨김)."
              if flag else "다시 목록에 올렸습니다.",
              color="positive" if flag else "info")
    refresh()


def load_threads(show_done: bool = False) -> list[dict]:
    """메일함 목록 계산. **무겁다** — 메일 전량 분류 + FOTA 조회다.

    이벤트 루프에서 부르면 그동안 모든 사용자의 화면이 멈춘다. 반드시 run.io_bound
    같은 별도 스레드로 넘겨서 부를 것.
    """
    try:
        return _threads({"q": "", "show_done": show_done})
    except Exception:  # noqa: BLE001
        return []


# 탭 배지는 예전에 open_thread_count() 로 목록을 **따로 한 번 더** 셌다. 그래서 페이지
# 한 번 여는 데 같은 4초짜리 계산이 두 번 돌았다(실측 2026-08-14). 지금은 메일함 본문이
# 목록을 다 읽은 뒤 그 수를 on_count 로 알려 준다 — 세는 곳은 한 군데뿐이다.


def auto_triage_new() -> dict:
    """새로 들어온 후보만 Claude 로 판정한다(메일 동기화 직후 자동 호출).

    triage() 는 이미 판정한 스레드를 캐시로 건너뛰므로, 그냥 전체를 넘기면
    **새 스레드만** 실제 호출이 나간다.

    대상은 **SN 신호가 있는 사람 발신 메일 전부**다(등급 없음). 자동발송만 뺀다.
    """
    rows = F.list_candidates(limit=400)
    return T.triage(rows)


def render_whitelist_mailbox(on_count=None):
    """WhiteList 메일함 본문 — 통합 메일함(/mail)의 한 탭으로 그린다.

    목록 계산은 무겁다(메일 전량 분류 + FOTA 조회). 그래서 두 가지를 지킨다.
      · **화면을 먼저 띄우고** 목록은 그 뒤에 채운다. 페이지 응답을 붙잡으면 NiceGUI 가
        3초(response_timeout) 만에 오류 페이지를 내보낸다.
      · 계산은 run.io_bound 로 별도 스레드에서 한다. 이벤트 루프에서 하면 이 화면을 연
        사람뿐 아니라 접속한 **모든 사람의 화면**이 함께 멈춘다.

    on_count: 목록을 읽을 때마다 스레드 수를 알려 준다(탭 배지용). 배지가 같은 계산을
              따로 하지 않게 하려는 것이다 — 예전에는 배지와 본문이 각각 4초씩 썼다.

    반환: 이 패널을 다시 그리는 함수(동기화 후 갱신용).
    """
    me = current_username() or ""
    state = {"q": ""}

    with ui.row().classes("items-center gap-3 w-full q-mt-sm"):
        ui.input(
            placeholder="제목·발신자·일련번호 검색",
            on_change=lambda e: (state.update(q=e.value or ""), body.refresh()),
        ).props("dense outlined clearable").classes("flex-1 min-w-0")
        ui.checkbox(
            "처리 완료 포함",
            on_change=lambda e: (state.update(show_done=bool(e.value)), body.refresh()),
        ).props("dense").tooltip("수동으로 처리 완료한 메일도 함께 보기(다시 열 수 있음)")
        ui.checkbox(
            "작업 아님 포함",
            on_change=lambda e: (state.update(show_notwl=bool(e.value)), body.refresh()),
        ).props("dense").tooltip("WhiteList 작업이 아니라고 내린 메일도 함께 보기"
                                "(되돌릴 수 있음)")

    # body 는 **틀만** 만든다(빈 자리 + 기다림 표시). 목록은 아래 load 가 스레드에서
    # 읽어 채운다. body 자체를 async 로 두면 안 된다 — 화면을 짓는 자리에서 기다리는
    # 셈이라 NiceGUI 가 3초 만에 오류 페이지를 내보낸다(실측 2026-08-14).
    @ui.refreshable
    def body() -> None:
        holder = ui.column().classes("w-full")
        with holder:
            with ui.row().classes("items-center gap-2 q-mt-sm"):
                ui.spinner(size="sm")
                ui.label("메일함을 읽는 중…").classes("text-caption text-grey-7")

        async def load() -> None:
            snap = dict(state)          # 스레드로 넘기므로 그 사이 바뀌지 않게 복사한다
            try:
                groups = await run.io_bound(_threads, snap)
            except Exception as e:  # noqa: BLE001
                holder.clear()
                with holder:
                    ui.label(f"메일을 읽지 못했습니다: {e}").classes("text-negative")
                return
            holder.clear()
            # 배지는 검색어를 적용하기 **전** 수를 쓴다(검색해도 배지가 흔들리지 않게).
            if on_count is not None and not snap.get("show_done"):
                on_count(len(groups))

            q = (state["q"] or "").strip().lower()
            if q:
                groups = [
                    g for g in groups
                    if q in g["rep"]["subject"].lower()
                    or q in g["rep"]["sender"].lower()
                    or any(q in s.lower() for s in g["serials"])
                ]

            with holder:
                ui.label(f"메일 {len(groups)}건") \
                    .classes("text-caption text-grey-7 q-mt-sm")

                if not groups:
                    with ui.card().classes("w-full items-center q-pa-lg"):
                        ui.icon("inbox", size="42px").classes("text-grey-5")
                        ui.label("처리할 메일이 없습니다.").classes("text-grey-7")
                    return

                for g in groups:
                    _render_thread(g, me, body.refresh)

        # 화면이 브라우저에 뜬 **뒤** 채운다(ui.timer 는 접속이 붙은 다음 돈다).
        ui.timer(0.01, load, once=True)

    body()          # 틀을 지금 만들어 둔다 — 한 번도 부르지 않으면 refresh 가 안 먹는다
    return body.refresh


def _fota_status(sns: list[str]) -> dict:
    """각 SN 의 FOTA 등록 여부. {sn: True/False}. (조회 실패 SN 은 미등록으로 간주)

    SN 당 HTTP 한 번이라 순차로는 느리다(14개 ≈ 13초) → 스레드풀로 병렬 조회한다.
    """
    from concurrent.futures import ThreadPoolExecutor

    from app.whitelist import fota as WF

    uniq = list(dict.fromkeys(sns))
    if not uniq:
        return {}
    with ThreadPoolExecutor(max_workers=min(10, len(uniq))) as ex:
        results = list(ex.map(lambda s: (s, bool(WF.is_registered(s))), uniq))
    return dict(results)


def _sender_addr_name(sender: str) -> tuple[str, str]:
    """'이름 <a@b.com>' → (주소, 이름)."""
    from email.utils import parseaddr

    name, addr = parseaddr(sender or "")
    return addr, (name or "").strip()


def _open_process_dialog(g: dict, verdict: dict, sns: list[str], me: str, refresh) -> None:
    """처리 팝업 — ① 등록(FOTA) ② 메일 회신 두 단계를 한 창에서.

    OS Update 검수 팝업과 같은 방식(모달 안에서 전부 처리). 관리자 전용.
    """
    from nicegui import run

    from app.auth import current_user_id, is_admin
    from app.whitelist import mailer as WM
    from app.whitelist import service as S

    if not is_admin():
        ui.notify("등록 처리는 관리자만 가능합니다.", color="warning")
        return

    uid = current_user_id()
    rep = g["rep"]
    addr, name = _sender_addr_name(rep["sender"])
    lang = WM.detect_lang(f"{rep['subject']}\n{rep.get('body_text','')}")
    model = (verdict.get("model") or "").strip()
    cur0 = str(verdict.get("from_os") or "")
    upg0 = str(verdict.get("to_os") or "")

    from datetime import date

    # **등록일**의 기본값이다 — 지원 종료일이 아니다. 예전에는 '오늘+1년' 을 넣어
    # 두어서, 그대로 누르면 등록일이 2027 로 들어가고 종료일이 2028 이 됐다
    # (실측 2026-09-16 · SN 25253A0131). 종료일은 DB 가 등록일+1년으로 넣는다.
    default_date = date.today().isoformat()
    # 요청 구분 자동 판별 — 발신자가 DQA 팀이면 DQA, 아니면 GMS(일반). 화면에서 바꿀 수 있다.
    auto_cat = S.category_for_sender(rep["sender"])
    # req: 등록 결과 · reg_skipped: 처리 건너뜀 · status: {sn: FOTA 등록여부}
    # category: 이 요청을 어느 규칙 구분으로 등록할지 · ostype_override: 규칙 밖 수동 OS Type
    # verify_items: 방금 보낸 값(기대값). ② 확인 탭이 FOTA 에서 다시 읽어 대조한다.
    # verify_result: 그 대조 결과. 확인 전에는 None.
    state = {"req": None, "reg_skipped": False, "status": None,
             "category": auto_cat, "ostype_override": "",
             "verify_items": [], "verify_result": None, "verify_kind": ""}
    done_devices: list[dict] = []   # 회신 초안에 넣을, 실제 처리한 단말

    def _close_and_refresh():
        # 닫기는 즉시 반영하고, 무거운 목록 재조회(약 1.5초)는 다음 틱으로 미룬다.
        # 같은 핸들러에서 동기로 돌리면 닫힘이 늦어 "여러 번 눌러야" 하는 것처럼 보인다.
        dialog.close()
        ui.timer(0.01, refresh, once=True)

    def _mail_ctx() -> dict:
        return {"mail_id": rep["id"], "user_id": uid, "requester_addr": addr,
                "requester_name": name, "lang": lang,
                "message_id": rep.get("message_id", "")}

    with ui.dialog() as dialog, ui.card().classes("w-full").style(
        "max-width:820px; max-height:90vh"
    ):
        ui.label(f"처리 · {rep['subject']}").classes("text-subtitle1 text-weight-bold")
        ui.label(f"요청자: {name or '(이름 없음)'} <{addr or '주소 없음'}>  ·  "
                 f"언어: {'한국어' if lang == 'ko' else '영어'}").classes(
            "text-caption text-grey-7")

        with ui.tabs().classes("w-full") as tabs:
            t_reg = ui.tab("① 처리", icon="app_registration")
            t_check = ui.tab("② 확인", icon="fact_check")
            t_reply = ui.tab("③ 메일 회신", icon="reply")

        # 탭 본문만 스크롤(제목·탭·닫기는 고정). SN 많아도 버튼이 안 잘린다.
        with ui.tab_panels(tabs, value=t_reg).classes("w-full").style(
            "max-height:64vh; overflow-y:auto"
        ):
            # ── ① 처리(신규 등록 / 등록일 변경) ────────────────────
            with ui.tab_panel(t_reg):
                @ui.refreshable
                def proc_panel() -> None:
                    st = state["status"]
                    if st is None:
                        with ui.row().classes("items-center gap-2"):
                            ui.spinner()
                            ui.label("FOTA 등록 상태 확인 중…").classes("text-grey-7")
                        return

                    # 요청 구분 선택 — 자동(발신자) 값을 기본으로, 바꾸면 표가 즉시 갱신된다.
                    with ui.row().classes("items-center gap-2 q-mb-xs"):
                        cat_sel = ui.select(S.CATEGORIES, value=state["category"],
                                            label="요청 구분") \
                            .props("outlined dense").style("width:120px")
                        ui.badge(f"자동 판별: {auto_cat}",
                                 color="deep-orange" if auto_cat == "DQA" else "teal") \
                            .props("outline")
                    # 규칙에 없는 구분(KT/LG/기타)은 OS Type 을 직접 지정한다.
                    ov_in = ui.input("OS Type 직접 지정 (규칙 밖일 때만)",
                                     value=state["ostype_override"]) \
                        .props("outlined dense clearable").classes("w-full")

                    new_sns = [s for s in sns if not st.get(s)]
                    reg_sns = [s for s in sns if st.get(s)]

                    def _current_new_devs() -> list[dict]:
                        # 현재 선택한 구분 + 직접 지정 OS Type 으로 기기 목록을 다시 계산한다.
                        # 등록 요청 메일은 이미 등록된 SN 도 같은 엑셀에 실어 한 번에 올린다
                        # (갱신도 엑셀로 처리 — 사용자 확정 2026-08-07).
                        cat_now = cat_sel.value or S.DEFAULT_CATEGORY
                        ov_now = (ov_in.value or "").strip()
                        out = []
                        for s in sns:
                            r = S.resolve_os(model, cur0, upg0, cat_now)
                            _c = str(r["from"]) if r else cur0
                            _u = str(r["to"]) if r else upg0
                            out.append(S.build_device(s, _c, _u, model, cat_now,
                                                      ostype_override=ov_now))
                        return out


                    # 무엇을 하는 요청인지 먼저 말한다 — 전부 이미 등록돼 있으면 등록이 아니라
                    # **등록일 변경** 요청이다. 예전에는 등록 블록을 언제나 먼저 그려서 등록일
                    # 변경 요청도 '등록 요청' 처럼 읽혔다(사용자 지적 2026-09-16).
                    lead_change = bool(sns) and not new_sns
                    if sns:
                        if lead_change:
                            ui.badge(f"이미 전부 등록된 단말 {len(reg_sns)}대 — 등록일 변경 요청",
                                     color="indigo-7").props("outline").classes("q-mb-xs")
                        elif reg_sns:
                            ui.badge(f"신규 {len(new_sns)}대 · 이미 등록 {len(reg_sns)}대",
                                     color="teal-7").props("outline").classes("q-mb-xs")

                    def _blk_register() -> None:
                        """등록(신규 + 갱신) — 표의 값으로 엑셀을 만들어 FOTA 에 올린다."""
                        if sns:
                            _cnt_label = ui.label().classes("text-subtitle2 text-weight-bold")
                            ui.label("표의 값으로 엑셀을 만들어 FOTA 에 업로드합니다. "
                                     "‘상태 ⚠’ 행은 제외됩니다.").classes(
                                "text-caption text-grey-7")
                            _tbl = ui.table(columns=UPLOAD_COLS,
                                            rows=upload_rows(_current_new_devs(), st),
                                            row_key="sn").classes("w-full").props("dense flat bordered")
                            if len(sns) > 8:
                                _tbl.style("max-height:280px")

                            def _rebuild_new():
                                # 구분/OS Type 을 바꾸면 표·건수·버튼 상태를 즉시(포커스 유지) 갱신.
                                state["category"] = cat_sel.value or S.DEFAULT_CATEGORY
                                state["ostype_override"] = (ov_in.value or "").strip()
                                devs = _current_new_devs()
                                _tbl.rows = upload_rows(devs, st)
                                _tbl.update()
                                n_ok = sum(1 for d in devs if d["valid"])
                                _cnt_label.set_text(
                                    f"등록 · {len(devs)}대 (전송 가능 {n_ok}대"
                                    + (f" · 기존 등록 {len(reg_sns)}대" if reg_sns else "") + ")")
                                reg_btn.set_enabled(n_ok > 0)

                            cat_sel.on_value_change(lambda e: _rebuild_new())
                            ov_in.on_value_change(lambda e: _rebuild_new())
                            charge = {"type": "무상"}
                            with ui.row().classes("items-center gap-3 q-mt-xs"):
                                ui.radio({"무상": "무상", "유상": "유상"}, value="무상",
                                         on_change=lambda e: (charge.update(type=e.value),
                                                              po_row.set_visibility(e.value == "유상"),
                                                              free_in.set_visibility(e.value == "무상"))
                                         ).props("inline")
                            po_hold = {"name": "", "data": None}
                            with ui.column().classes("w-full gap-1") as po_row:
                                ui.label("PO 파일 등록 (유상)").classes("text-caption text-grey-7")

                                async def on_po_upload(e) -> None:
                                    po_hold["name"] = e.file.name
                                    po_hold["data"] = await e.file.read()
                                    ui.notify(f"PO 파일 첨부: {e.file.name}", color="positive")

                                ui.upload(on_upload=on_po_upload, auto_upload=True,
                                          max_files=1).props("flat").classes("w-full")
                            po_row.set_visibility(False)
                            free_in = ui.input("무상 사유").props("outlined dense").classes("w-full")
                            memo_in = ui.input("메모(선택)").props("outlined dense").classes("w-full")

                            async def do_register():
                                goods = [d for d in _current_new_devs() if d["valid"]]
                                if not goods:
                                    ui.notify("등록 가능한 단말이 없습니다.", color="warning"); return
                                paid = charge["type"] == "유상"
                                write = not bool(reg_dry.value)
                                # 중복 신청 방어는 **FOTA 미등록 SN 에만** 건다.
                                # 이미 FOTA 에 있는 SN 은 갱신이 목적이라 우리 이력에 있는 게 정상이다.
                                fresh = [d["sn"] for d in goods if not st.get(d["sn"])]
                                dup = await run.io_bound(
                                    lambda: S.existing_sns(fresh, fresh=True))
                                if dup:
                                    ui.notify("이미 신청된 단말: " + ", ".join(dup),
                                              color="negative", multi_line=True); return
                                reg_btn.props("loading")
                                try:
                                    res = await run.io_bound(lambda: S.register_from_mail(
                                        devices=goods, processed_by=me, charge_type=charge["type"],
                                        category=state["category"],
                                        free_reason="" if paid else (free_in.value or "").strip(),
                                        memo=(memo_in.value or "").strip(), mail=_mail_ctx(),
                                        allow_duplicates=True, allow_write=write))
                                finally:
                                    reg_btn.props(remove="loading")
                                if not res.get("ok"):
                                    ui.notify(res.get("msg", "등록 실패"), color="negative"); return
                                if res.get("dry"):
                                    f = res.get("fota") or {}
                                    ui.notify(f"dry-run · 전송 안 함 · 대상 {f.get('total', 0)}대",
                                              color="warning")
                                    return
                                state["req"] = res["request"]
                                # 유상 PO 파일 첨부 — 등록으로 생긴 req_id 로 저장한다.
                                if paid and po_hold["data"]:
                                    try:
                                        _p = await run.io_bound(
                                            S.save_po_file, res["request"]["id"],
                                            po_hold["name"], po_hold["data"])
                                        await run.io_bound(S.attach_po, res["request"]["id"], _p)
                                    except Exception as ex:  # noqa: BLE001
                                        ui.notify(f"PO 파일 저장 실패: {ex}", color="negative")
                                done_devices.extend(goods)
                                f = res.get("fota") or {}
                                ui.notify(f"등록 완료 · FOTA {f.get('success',0)}/{f.get('total',0)}",
                                          color="positive")
                                # 보낸 값을 기대값으로 넘긴다 — ② 확인이 FOTA 에서 다시 읽어 대조한다.
                                state["verify_kind"] = "등록"
                                state["verify_items"] = [
                                    {"sn": d["sn"], "endDate": d.get("endDate", ""),
                                     "ostype": d.get("ostype", ""), "api": str(d.get("api") or "")}
                                    for d in goods]
                                state["verify_result"] = None
                                check_panel.refresh()
                                reply_panel.refresh()
                                tabs.set_value(t_check)
                                # 여기서 목록(refresh)을 다시 그리면 **이 팝업이 통째로 사라진다** —
                                # 팝업이 목록 안에 그려져 있어 body 를 다시 그리면 같이 지워지고,
                                # 등록으로 '처리됨' 이 되어 그 줄 자체가 목록에서 빠진다.
                                # 그러면 ② 확인 · ③ 회신을 볼 수 없다(사용자 지적 2026-08-12).
                                # 목록 갱신은 팝업을 닫을 때 _close_and_refresh 가 한다.

                            with ui.row().classes("items-center gap-3"):
                                reg_btn = ui.button("등록 + FOTA 전송", icon="check",
                                                    on_click=do_register) \
                                    .props("unelevated color=primary")
                                reg_dry = ui.checkbox("dry-run").props("dense")
                            _rebuild_new()   # 초기 건수·버튼 상태 세팅(+ 이후 실시간 갱신)

                    def _blk_change() -> None:
                        """등록일 변경 — 이미 등록된 단말의 날짜만 FOTA DB 에서 바꾼다."""
                        if reg_sns:
                            if new_sns:
                                ui.separator().classes("q-my-sm")
                            ui.label(f"등록일 변경 · {len(reg_sns)}대 (이미 등록됨)").classes(
                                "text-subtitle2 text-weight-bold")
                            state.setdefault("change_date", default_date)
                            date_in = ui.input("변경할 등록일 (YYYY-MM-DD)",
                                               value=state["change_date"]) \
                                .props("outlined dense").classes("w-full")
                            # 넣은 날짜가 **무엇이 되는지** 한 줄로 보여준다 — 연도를 잘못 넣어도
                            # 표까지 안 내려가면 모른다(실측 2026-09-16 · 등록일 2027 · 종료일 2028).
                            _date_echo = ui.label().classes("text-caption text-weight-medium")

                            ui.label("표의 값 그대로 FOTA DB 에 반영합니다. "
                                     "androidApi·osType·partnumber 는 변경하지 않습니다.") \
                                .classes("text-caption text-grey-7")

                            # 표는 화면 입력값이 아니라 **DB 에 실제로 들어갈 값**을 보여준다.
                            _ctbl = ui.table(columns=DATE_COLS,
                                             rows=date_rows(reg_sns, state["change_date"]),
                                             row_key="sn") \
                                .classes("w-full").props("dense flat bordered")
                            if len(reg_sns) > 8:
                                _ctbl.style("max-height:280px")

                            def _echo() -> None:
                                r = (date_rows(["x"], state["change_date"]) or [{}])[0]
                                _date_echo.set_text(
                                    f"→ 등록일 {r.get('update_date_time', '')} · "
                                    f"지원 종료일 {r.get('support_end_date', '')}")

                            def _rebuild_change():
                                # 날짜를 입력하는 즉시(포커스 유지) 표를 갱신.
                                state["change_date"] = (date_in.value or "").strip()
                                _ctbl.rows = date_rows(reg_sns, state["change_date"])
                                _ctbl.update()
                                _echo()
                            date_in.on_value_change(lambda e: _rebuild_change())
                            _echo()

                            async def do_change():
                                nd = (date_in.value or "").strip()
                                if not re.match(r"^\d{4}-\d{2}-\d{2}$", nd):
                                    ui.notify("날짜는 YYYY-MM-DD 형식으로 입력하세요.",
                                              color="warning"); return
                                dry = bool(chg_dry.value)
                                chg_btn.props("loading")
                                try:
                                    # 웹 API 가 아니라 **FOTA DB 직접** — 한 트랜잭션이라
                                    # 하나라도 어긋나면 전부 되돌아간다(2026-08-07 확정).
                                    res = await run.io_bound(lambda: S.change_dates_db(
                                        sns=reg_sns, new_date=nd, processed_by=me,
                                        mail=_mail_ctx(), dry_run=dry))
                                finally:
                                    chg_btn.props(remove="loading")
                                _db_result.clear()
                                with _db_result:
                                    for ln in res.get("lines", []):
                                        ui.label(ln).classes("text-caption font-mono") \
                                            .style("color:#b3261e" if ln.lstrip().startswith("✘")
                                                   else "")
                                if not res.get("ok"):
                                    ui.notify(res["lines"][0] if res.get("lines") else "변경 실패",
                                              color="negative", multi_line=True)
                                    return
                                if res.get("dry"):
                                    ui.notify(f"dry-run · DB 미변경 · 대상 {len(reg_sns)}대",
                                              color="warning")
                                    return
                                state["req"] = res["request"]
                                done_devices.extend({"sn": s, "cur": "", "upg": "",
                                                     "model": "", "ostype": ""} for s in reg_sns)
                                ui.notify(f"등록일 변경 완료 · {len(reg_sns)}대 (DB 반영)",
                                          color="positive")
                                # 등록일 변경은 **날짜만** 대조한다 — osType·api 는 서버 값 보존이라
                                # 기대값이 없다(있는 값과 비교하면 거짓 불일치가 난다).
                                # DB 는 support_end_date 를 **등록일 + 1년** 으로 넣는다.
                                # 확인 단계에 등록일을 그대로 주면 거짓 불일치가 난다.
                                _y, _m, _d = nd.split("-")
                                _end = f"{int(_y) + 1}-{_m}-{_d}"
                                state["verify_kind"] = "등록일 변경"
                                state["verify_items"] = [{"sn": s, "endDate": _end} for s in reg_sns]
                                state["verify_result"] = None
                                check_panel.refresh()
                                reply_panel.refresh()
                                tabs.set_value(t_check)
                                # 등록과 같은 이유로 목록은 여기서 다시 그리지 않는다 —
                                # 팝업이 사라져 ② 확인으로 넘어갈 수 없다.

                            with ui.row().classes("items-center gap-3"):
                                chg_btn = ui.button("등록일 변경", icon="event",
                                                    on_click=do_change) \
                                    .props("unelevated color=primary")
                                chg_dry = ui.checkbox("dry-run").props("dense")
                            ui.label("dry-run 을 체크하지 않으면 프로덕션 FOTA DB 에 바로 반영됩니다.") \
                                .classes("text-caption").style("color:#b3261e")
                            _db_result = ui.column().classes("w-full gap-0 q-mt-xs")

                    if lead_change:
                        # 이 요청의 본론을 먼저. 다시 등록할 일은 드무니 접어 둔다.
                        _blk_change()
                        with ui.expansion("이미 등록된 단말을 다시 등록(엑셀 업로드)",
                                          icon="upload_file").classes("w-full q-mt-sm"):
                            _blk_register()
                    else:
                        _blk_register()
                        _blk_change()

                    # 처리 없이 회신만
                    def skip_proc():
                        state["reg_skipped"] = True
                        ui.notify("처리를 건너뜁니다. 회신 탭으로 이동합니다.", color="info")
                        tabs.set_value(t_reply)

                    ui.separator().classes("q-my-sm")
                    ui.button("처리 건너뛰고 회신만", icon="skip_next",
                              on_click=skip_proc).props("flat color=grey-8")

                proc_panel()

                async def _check_status():
                    st = await run.io_bound(_fota_status, sns)
                    state["status"] = st
                    proc_panel.refresh()

            async def _run_verify() -> None:
                items = state["verify_items"]
                if not items:
                    ui.notify("확인할 대상이 없습니다.", color="warning")
                    return
                res = await WV.run_verify(uid, items, state["verify_kind"])
                state["verify_result"] = res
                check_panel.refresh()
                if res.get("error"):
                    ui.notify(res["error"], color="negative")
                else:
                    ui.notify(f"일치 {res['matched']}/{res['total']}",
                              color="positive" if res["ok"] else "negative")

            # ── ② 확인 — FOTA 에서 다시 읽어(GET) 기대값과 대조 ─────
            # 보냈다는 응답만으로 회신하지 않는다. 실제로 그 값이 들어갔는지 본다.
            with ui.tab_panel(t_check):
                @ui.refreshable
                def check_panel() -> None:
                    items = state["verify_items"]
                    if not items:
                        ui.label("① 처리를 먼저 실행하세요.").classes(
                            "text-caption text-grey-7")
                        return
                    kind = state["verify_kind"] or "처리"
                    res = state["verify_result"]

                    with ui.row().classes("w-full items-center gap-3"):
                        ui.label(f"{kind} · 대상 {len(items)}대").classes("text-subtitle2")
                        ui.space()
                        ui.button("FOTA 에서 확인", icon="fact_check",
                                  on_click=_run_verify).props("unelevated color=primary dense")
                    if res is None:
                        ui.label("아직 확인하지 않았습니다.").classes(
                            "text-caption text-grey-7")
                        return
                    WV.verify_table(res)
                    if res.get("error"):
                        return
                    with ui.row().classes("w-full justify-end q-mt-sm"):
                        ui.button("회신 단계로", icon="arrow_forward",
                                  on_click=lambda: tabs.set_value(t_reply)) \
                            .props("unelevated dense "
                                   + ("color=primary" if res["ok"] else "color=grey-7"))

                check_panel()

            # ── ③ 메일 회신 ────────────────────────────────────
            with ui.tab_panel(t_reply):
                ui.label(f"받는 사람: {addr or '(주소 없음)'}").classes(
                    "text-caption text-grey-7")

                @ui.refreshable
                def reply_panel() -> None:
                    # 처리한 단말이 있으면 그걸로, 없으면 선택 SN 으로 초안을 만든다.
                    devs = done_devices or [{"sn": s} for s in sns]
                    draft = WM.build_reply_draft(
                        to_name=name, devices=devs, lang=lang,
                        kind=state["verify_kind"],
                        new_date=state.get("change_date", ""))
                    subj_in = ui.input("제목", value=draft["subject"]).props(
                        "outlined dense").classes("w-full")
                    body_in = ui.textarea("본문", value=draft["body"]).props(
                        "outlined").classes("w-full").style("min-height:220px")

                    def _reply_quote_html() -> str:
                        """회신에 실릴 원본 인용부(미리보기용) - 실제 발송과 같은 것을 쓴다."""
                        from app.user_mailer import reply_context

                        return (reply_context(rep.get("id")) or {}).get("quote_html", "")

                    async def do_send():
                        if state["req"] is None and not state["reg_skipped"]:
                            ui.notify("먼저 ① 처리를 완료하거나 건너뛰세요.", color="warning"); return
                        if not addr:
                            ui.notify("회신 받을 주소가 없습니다.", color="negative"); return
                        send_btn.props("loading")
                        try:
                            ok, msg = await run.io_bound(
                                WM.send_reply, uid, addr, subj_in.value, body_in.value,
                                orig_mail_id=rep.get("id"))
                        finally:
                            send_btn.props(remove="loading")
                        if state["req"] is not None:
                            S.set_reply_status(state["req"]["id"], "sent" if ok else "failed")
                        if ok:
                            ui.notify("회신을 보냈습니다.", color="positive")
                            _close_and_refresh()
                        else:
                            ui.notify(f"회신 발송 실패: {msg}", color="negative", multi_line=True)

                    with ui.row().classes("items-center gap-2 q-mt-sm"):
                        # 회신은 고객이 보낸 요청 스레드에 붙어 나간다 - 누르기 전에
                        # 인용부·서명·글꼴이 어떻게 실리는지 볼 수 있어야 한다.
                        from app.pages.mail_preview import preview_button

                        preview_button(uid, lambda: subj_in.value, lambda: body_in.value,
                                       to=addr, quote_html_fn=_reply_quote_html)
                        send_btn = ui.button("회신 보내기", icon="send",
                                             on_click=do_send).props("unelevated color=primary")
                        ui.button("회신 건너뛰기", icon="skip_next",
                                  on_click=_close_and_refresh).props("flat color=grey-8")

                reply_panel()

        with ui.row().classes("w-full justify-end q-mt-sm"):
            ui.button("닫기", on_click=_close_and_refresh).props("flat")

    dialog.open()
    ui.timer(0.05, _check_status, once=True)   # 팝업 뜨면 FOTA 등록 상태 조회


def _status_badge(g: dict) -> None:
    """목록에서 처리 전/후를 한눈에 — 처리 후는 등록완료/수동완료 사유를 툴팁으로."""
    if g.get("notwl"):
        ui.badge("작업 아님", color="blue-grey-6").props("outline") \
            .tooltip("WhiteList 작업이 아니라고 내린 메일입니다.")
    elif g.get("done"):
        reason = "등록 완료" if g.get("processed") else "수동 처리 완료"
        ui.badge("처리 후", color="green-7").tooltip(reason)
    elif g.get("reopened"):
        # 한 번 처리한 대화인데 그 뒤 새 메일이 왔다 — 후속 요청일 수 있으니 표시한다.
        ui.badge("새 회신", color="deep-orange-8")             .tooltip("이 대화는 처리 완료로 표시했지만, 그 뒤에 새 메일이 왔습니다.")
    else:
        ui.badge("처리 전", color="orange-8")


def _render_thread(g: dict, me: str, refresh) -> None:
    v = g["verdict"]
    rep = g["rep"]

    # 아직 판별 안 된 메일은 열지 못하게 한다(자동 판별이 끝나면 열린다).
    if not v:
        with ui.card().classes("w-full q-mb-sm"), \
                ui.row().classes("items-center gap-2 w-full no-wrap q-pa-sm"):
            ui.spinner(size="sm").classes("text-grey-5")
            _status_badge(g)
            ui.label(rep["subject"]).classes(
                "text-weight-medium flex-1 min-w-0 ellipsis text-grey-6")
            ui.badge("AI 판별 중", color="blue-grey-4").props("outline")
            if g["serials"]:
                ui.badge(f"SN {len(g['serials'])}", color="blue-7").props("outline")
            ui.label(_when(rep["received_at"])).classes("text-caption text-grey-6")
            # 판별 전이라도 사람이 손으로 처리 완료(숨김)할 수 있게 한다.
            if g.get("notwl"):
                ui.button("되돌리기", icon="undo",
                          on_click=lambda _=None, gg=g: _set_notwl(gg, False, me, refresh)) \
                    .props("flat dense size=sm color=grey-7")
            elif g.get("manual_done"):
                ui.button("다시 열기", icon="undo",
                          on_click=lambda _=None, gg=g: _set_done(gg, False, me, refresh)) \
                    .props("flat dense size=sm color=grey-7")
            else:
                ui.button("처리 완료", icon="done",
                          on_click=lambda _=None, gg=g: _set_done(gg, True, me, refresh)) \
                    .props("flat dense size=sm color=grey-7")
                ui.button("작업 아님", icon="block",
                          on_click=lambda _=None, gg=g: _set_notwl(gg, True, me, refresh)) \
                    .props("flat dense size=sm color=blue-grey-7") \
                    .tooltip("WhiteList 작업이 아닌 메일 — 목록에서 내린다")
        return

    with ui.card().classes("w-full q-mb-sm"), ui.expansion().classes("w-full") as exp:
        with exp.add_slot("header"), ui.row().classes("items-center gap-2 w-full no-wrap"):
            _status_badge(g)
            ui.label(rep["subject"]).classes(
                "text-weight-medium flex-1 min-w-0 ellipsis")
            if g["count"] > 1:
                ui.badge(f"메일 {g['count']}", color="blue-grey-5").props("outline")
            if g["serials"]:
                ui.badge(f"SN {len(g['serials'])}", color="blue-7").props("outline")
            _att = (rep.get("attachment_names") or "").strip()
            if _att:
                ui.badge("📎 첨부", color="deep-purple-5").props("outline").tooltip(_att)
            ui.label(_when(rep["received_at"])).classes("text-caption text-grey-6")

        ui.label(f"보낸사람: {rep['sender']}").classes("text-caption text-grey-7")
        if (rep.get("attachment_names") or "").strip():
            ui.label(f"첨부: {rep['attachment_names']}").classes("text-caption text-deep-purple-8")
        info = " · ".join(x for x in [
            f"모델 {v['model']}" if v.get("model") else "",
            f"OS {v.get('from_os') or '?'} → {v.get('to_os') or '?'}",
        ] if x)
        ui.label(f"Claude 판정: {info}").classes("text-caption text-grey-8")
        ui.label(v.get("reason", "")).classes("text-caption text-grey-7")

        dups = g.get("dups") or {}
        from app.pages.whitelist_bulk import BULK_THRESHOLD as _BULK

        if g["serials"] and len(g["serials"]) > _BULK:
            # 대량 — 목록을 나열하지 않고 개수만 표시(처리는 '대량 요약 처리'에서).
            picked = {}
            ui.label(f"일련번호 {len(g['serials']):,}개").classes(
                "text-caption text-weight-bold q-mt-sm")
            if dups:
                ui.label(f"※ 이미 신청된 SN {len(dups):,}건 포함").classes(
                    "text-caption text-grey-6")
        elif g["serials"]:
            ui.label("일련번호").classes("text-caption text-weight-bold q-mt-sm")
            # 이미 신청된 SN 은 기본 해제해 둔다 — 같은 단말을 두 번 올리지 않도록.
            picked: dict[str, bool] = {s: (s not in dups) for s in g["serials"]}
            with ui.column().classes("gap-0"):
                for s in g["serials"]:
                    d = dups.get(s)
                    with ui.row().classes("items-center gap-2 no-wrap"):
                        ui.checkbox(
                            s, value=(d is None),
                            on_change=lambda e, sn=s: picked.__setitem__(sn, bool(e.value)),
                        ).props("dense")
                        if d:
                            ui.label(
                                f"이미 신청됨 · {d['request_id']} "
                                f"({d['created_at']}, {_STATUS_KO.get(d['status'], d['status'])})"
                            ).classes("text-caption text-orange-9")
            if dups:
                ui.label(f"※ {len(dups)}건은 이미 신청되어 체크를 해제했습니다.")                     .classes("text-caption text-grey-6")
        else:
            picked = {}

        # DQA FOTA 테스트 요청은 전용 처리(FOTA OsInfo/DownloadPath 등록 + 빌드 업로드 + SN)로 보낸다.
        from app.pipeline import dqa_fota as _DQA

        is_fota_test = _DQA.is_fota_test_mail(
            rep.get("sender", ""), rep.get("subject", ""), rep.get("body_text", ""))

        from app.pages.whitelist_bulk import BULK_THRESHOLD

        _att = (rep.get("attachment_names") or "")
        _complex = len(g["serials"]) > BULK_THRESHOLD or "[첨부메일]" in _att \
            or any(x in _att.lower() for x in (".xlsx", ".xls", ".csv"))

        if is_fota_test:
            from app.pages.dqa_fota_dialog import open_dqa_fota_dialog

            with ui.row().classes("items-center gap-2 q-mt-sm"):
                ui.badge("DQA FOTA Test", color="deep-orange").props("outline")
                ui.button(
                    "DQA FOTA Test 처리", icon="system_update",
                    on_click=lambda _=None, gg=g: open_dqa_fota_dialog(gg, me, refresh),
                ).props("unelevated color=deep-orange dense")
        elif _complex:
            # 틀 없는/대량/첨부 포함 요청 — AI 해석 + 관리자 확인 계획으로 유기적 처리.
            from app.pages.whitelist_organic import open_organic_dialog

            with ui.row().classes("items-center gap-2 q-mt-sm"):
                if len(g["serials"]) > BULK_THRESHOLD:
                    ui.badge(f"대량 {len(g['serials']):,}건", color="deep-orange").props("outline")
                ui.button(
                    "요청 처리 (AI 해석)", icon="auto_awesome",
                    on_click=lambda _=None, gg=g: open_organic_dialog(gg, me, refresh),
                ).props("unelevated color=deep-orange dense")
        elif g["serials"]:
            # **체크한 것만** 넘긴다. 이미 등록된 것은 팝업이 '등록일 변경'으로 처리한다.
            #
            # 체크 상태는 **누르는 순간에** 읽는다. 예전에는 화면을 그릴 때 한 번
            # 세어 두고는 그것마저 쓰지 않은 채 g["serials"] 전부를 넘겼다 — 체크를
            # 풀어도 그대로 등록됐다(실측 2026-09-28).
            def _register(_=None, gg=g, vv=v, pk=picked):
                sel = [sn for sn in gg["serials"] if pk.get(sn)]
                if not sel:
                    ui.notify("체크된 일련번호가 없습니다.", color="warning")
                    return
                _open_process_dialog(gg, vv, sel, me, refresh)

            with ui.row().classes("items-center gap-2 q-mt-sm"):
                ui.button("등록하기", icon="task_alt", on_click=_register) \
                    .props("unelevated color=primary dense")

        # 수동 처리 완료 — 등록 없이 이 스레드를 목록에서 내린다(OS Update 수동 상태 관리와 같은 취지).
        with ui.row().classes("items-center gap-2 q-mt-xs"):
            if g.get("manual_done"):
                ui.badge("처리 완료(수동)", color="grey-6").props("outline")
                ui.button("다시 열기", icon="undo",
                          on_click=lambda _=None, gg=g: _set_done(gg, False, me, refresh)) \
                    .props("flat dense size=sm color=grey-8")
            else:
                ui.button("처리 완료(수동)", icon="check_circle",
                          on_click=lambda _=None, gg=g: _set_done(gg, True, me, refresh)) \
                    .props("flat dense size=sm color=grey-8") \
                    .tooltip("등록 없이 이 스레드를 처리 완료로 표시(목록에서 숨김)")
            # WhiteList 요청이 아닌 메일이 판별을 통과해 목록에 섞인다(사용자 지적
            # 2026-09-14). 완료로 덮으면 등록 내역에 '수동 처리' 로 남아 헷갈린다 —
            # 아예 우리 일이 아니라고 내린다.
            if g.get("notwl"):
                ui.badge("작업 아님", color="blue-grey-6").props("outline")
                ui.button("되돌리기", icon="undo",
                          on_click=lambda _=None, gg=g: _set_notwl(gg, False, me, refresh)) \
                    .props("flat dense size=sm color=blue-grey-8")
            else:
                ui.button("작업 아님", icon="block",
                          on_click=lambda _=None, gg=g: _set_notwl(gg, True, me, refresh)) \
                    .props("flat dense size=sm color=blue-grey-8") \
                    .tooltip("WhiteList 작업이 아닌 메일 — 등록 내역에 남기지 않고 "
                             "목록에서 내린다(되돌릴 수 있다)")

        ui.label("본문").classes("text-caption text-weight-bold q-mt-sm")
        ui.label(_short(rep["body_text"])).classes("text-caption text-grey-8")
