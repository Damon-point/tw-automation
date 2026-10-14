"""공통 테마 / 레이아웃 헬퍼.

Main Color(배경) : #EDEDED
Point Color(강조) : #4A7C67
모든 페이지가 이 모듈의 apply_theme() 와 page_header() 를 재사용한다.
"""
from contextlib import contextmanager

from nicegui import ui

# 팔레트 -------------------------------------------------------------------
MAIN_COLOR = "#EDEDED"   # 배경
POINT_COLOR = "#4A7C67"  # 강조 (버튼/헤더/링크)
POINT_DARK = "#3C6553"   # 강조 hover/진한 톤
TEXT_COLOR = "#2B2B2B"


def apply_theme() -> None:
    """Quasar 기본 색상과 페이지 배경을 프로젝트 팔레트로 맞춘다.

    각 @ui.page 함수 본문 첫 줄에서 호출한다.
    """
    ui.colors(
        primary=POINT_COLOR,
        secondary=POINT_DARK,
        accent=POINT_COLOR,
    )
    # 페이지 배경/기본 글자색
    ui.query("body").style(f"background-color: {MAIN_COLOR}; color: {TEXT_COLOR};")
    _autofit_script()


#: 내용 높이에 맞춰 스스로 늘어나는 입력칸에 붙이는 표식.
#: 이 클래스를 준 textarea 만 아래 스크립트가 손댄다(다른 화면은 그대로 둔다).
AUTOFIT_CLASS = "tw-autofit"

# Quasar 의 autogrow 는 **보이지 않는 곳에서 mount 되면 높이를 못 잰다** — 접힌
# expansion·닫힌 dialog 안이 그렇고, 나중에 펼쳐도 다시 재지 않아 값이 잘린 채 굳는다.
# 게다가 줄바꿈이 없어도 폭이 좁으면 글자가 **여러 줄로 접히는데**, 파이썬 쪽에서 미리
# 박아 두는 최소 높이(note_min_height)는 줄바꿈 문자 개수만 세므로 그런 값은 늘 모자란다
# (실측: 공개 선별표의 영문 한 줄 수정 내용이 3~5줄로 접혀 뒤가 가렸다).
# 그래서 화면에 **보이는 순간** 실제 내용 높이(scrollHeight)로 맞춘다.
_AUTOFIT_JS = """
<script>
(() => {
  if (window.__twAutofit) return;
  window.__twAutofit = true;
  const SEL = '.%s textarea';
  const fit = (ta) => {
    if (!ta.isConnected || ta.offsetHeight === 0) return;   // 아직 안 보인다
    ta.style.height = 'auto';
    ta.style.height = (ta.scrollHeight + 2) + 'px';
  };
  const io = new IntersectionObserver((es) => {
    for (const e of es) if (e.isIntersecting) fit(e.target);
  });
  const watch = (ta) => {
    if (ta.__twFit) return;
    ta.__twFit = true;
    ta.addEventListener('input', () => fit(ta));
    io.observe(ta);
    fit(ta);
  };
  const scan = () => document.querySelectorAll(SEL).forEach(watch);
  new MutationObserver(scan).observe(document.documentElement,
                                     {childList: true, subtree: true});
  scan();
})();
</script>
""" % AUTOFIT_CLASS


def _autofit_script() -> None:
    """페이지마다 한 번 심는다(스크립트 자체가 window 표식으로 중복 실행을 막는다)."""
    ui.add_body_html(_AUTOFIT_JS)


@contextmanager
def page_header(title: str, *, show_nav: bool = True, max_w: str = "max-w-3xl"):
    """상단 헤더 바(+ 네비게이션) + 본문 컨테이너.

    사용 예:
        with page_header("메일함"):
            ui.label("...")

    show_nav=False 이면 로그인 등 인증 전 페이지에서 네비/로그아웃을 숨긴다.
    """
    # 순환 import 방지를 위해 지연 import
    from app.auth import (
        can_mid_decide,
        current_username,
        is_final_reviewer,
        is_mid_reviewer,
        logout,
    )

    with ui.header().classes("items-center justify-between px-4").style(
        f"background-color: {POINT_COLOR};"
    ):
        ui.label(f"TW Automation · {title}").classes("text-h6 text-white")
        if show_nav and (is_mid_reviewer() or is_final_reviewer()):
            # 결재자 전용 네비 — 자기 결재 화면 + 본인 이메일 등록만.
            # (다른 페이지는 미들웨어가 차단한다.)
            _final = is_final_reviewer()
            with ui.row().classes("items-center gap-2"):
                ui.button(
                    "최종 검수" if _final else "RN 승인",
                    on_click=lambda f=_final: ui.navigate.to(
                        "/final-reviews" if f else "/reviews"),
                ).props("flat color=white")
                if _final:
                    # 최종 승인자는 License 발급 승인도 맡는다 — 메뉴가 없으면 갈 길이 없다.
                    ui.button("License",
                              on_click=lambda: ui.navigate.to("/licenses")) \
                        .props("flat color=white")
                ui.button(
                    "계정 관리", icon="manage_accounts",
                    on_click=lambda: ui.navigate.to("/accounts"),
                ).props("flat color=white").tooltip("내 발송 이메일 등록")

                def _logout():
                    logout()
                    ui.navigate.to("/login")

                ui.button("로그아웃", on_click=_logout).props("flat color=white")
                name = current_username()
                if name:
                    ui.label(name).classes("text-white text-caption")
        elif show_nav:
            with ui.row().classes("items-center gap-2"):
                # 서비스가 늘어나 메뉴바가 길어지므로 'Service' 하나로 묶는다.
                # 최상위 4개: OS Update · FOTA-DB 열람 · WhiteList · License.
                # OS Update 는 마우스를 올리면 우측으로 하위 항목이 펼쳐지는 캐스케이드.
                # (템플릿 관리는 관리자 전용이라 [설정] 안으로 옮겼다.)
                with ui.button("Service", icon="apps").props("flat color=white"):
                    with ui.menu():
                        # OS Update — hover 시 우측으로 하위 메뉴 펼침.
                        # (auto_close=False: 이 항목 자체는 클릭해도 메뉴가 닫히지 않아야
                        #  하위 항목으로 마우스를 옮길 수 있다.)
                        with ui.menu_item(auto_close=False).props(
                            "clickable"
                        ) as os_item:
                            with ui.row().classes(
                                "items-center justify-between w-full no-wrap gap-6"
                            ):
                                ui.label("OS Update")
                                ui.icon("chevron_right").classes("text-grey-6")
                            # 우측 펼침: anchor=부모의 오른쪽 위, self=하위메뉴의 왼쪽 위
                            with ui.menu().props(
                                'anchor="top end" self="top start"'
                            ) as os_submenu:
                                # 신규 작업 — 아직 시작하지 않은 회차만 평평하게 모아
                                # 건별로 처리하는 화면(제품군으로 접히지 않는다).
                                ui.menu_item(
                                    "신규 작업",
                                    on_click=lambda: ui.navigate.to("/newtasks"),
                                )
                                # 작업 History — 등록된 제품별로 전 회차를 모아 보는 목록
                                ui.menu_item(
                                    "작업 History",
                                    on_click=lambda: ui.navigate.to("/osupdate"),
                                )
                                # 제품 관리 — OS Update 작업의 기준 목록(제품+Android+Type)
                                ui.menu_item(
                                    "제품 관리",
                                    on_click=lambda: ui.navigate.to("/products"),
                                )
                                # 문서 History — 건별로 만들어진 문서(SP·KB·RN·Newsletter) 이력
                                ui.menu_item(
                                    "문서 History",
                                    on_click=lambda: ui.navigate.to("/portaledit"),
                                )
                                # RN 승인 — 전체 이력 RN(1차)을 보고 승인/반려하는 화면.
                                # 결재자는 위쪽 '결재자 전용 네비' 로 들어오지만, admin 은
                                # 이 메뉴가 없으면 갈 길이 없었다(2026-09-03).
                                # 권한이 없는 사람에게는 걸어도 /osupdate 로 되돌려지므로
                                # (auth 미들웨어) 메뉴를 아예 두지 않는다.
                                if can_mid_decide():
                                    ui.menu_item(
                                        "RN 승인",
                                        on_click=lambda: ui.navigate.to("/reviews"),
                                    )
                                # 최종 검수 — RN for Sales 를 보고 승인/반려만 하는 화면.
                                # (RN 승인자의 /reviews 와 같은 성격, 결재자만 다르다.)
                                ui.menu_item(
                                    "최종 검수",
                                    on_click=lambda: ui.navigate.to("/final-reviews"),
                                )
                        # Quasar 기본 하위 메뉴는 클릭으로 열려서, 마우스를 올리면(hover)
                        # 열리도록 mouseenter 에 열기를 붙인다.
                        os_item.on("mouseenter", lambda: os_submenu.open())
                        # FOTA-DB 열람 — 독립 메뉴로 분리
                        ui.menu_item(
                            "FOTA-DB 열람", on_click=lambda: ui.navigate.to("/excelrepo")
                        )
                        # WhiteList — FOTA 화이트리스트 등록(독립 모듈, DB 격리)
                        ui.menu_item(
                            "WhiteList", on_click=lambda: ui.navigate.to("/whitelist")
                        )
                        # License — OS Update 와 별개의 독립 모듈
                        ui.menu_item(
                            "License", on_click=lambda: ui.navigate.to("/licenses")
                        )
                        # Solution Buffer — Redmine salesissue 감시 → SB 문서화
                        # (독립 모듈, DB 격리)
                        ui.menu_item(
                            "Solution Buffer",
                            on_click=lambda: ui.navigate.to("/issues"),
                        )
                ui.button(
                    "사용량", on_click=lambda: ui.navigate.to("/usage"),
                ).props("flat color=white").tooltip("Claude 토큰 사용량·비용")
                ui.button(
                    "계정 관리", icon="manage_accounts",
                    on_click=lambda: ui.navigate.to("/accounts"),
                ).props("flat color=white")
                ui.button("설정", on_click=lambda: ui.navigate.to("/settings")).props(
                    "flat color=white"
                )
                name = current_username()
                if name:
                    ui.label(name).classes("text-white text-caption")

    # max_w: 넓은 화면이 필요한 페이지(예: 메일함의 Submission/Approval 2열)는 넓게 준다.
    with ui.column().classes(f"w-full {max_w} mx-auto p-6 gap-4"):
        yield
