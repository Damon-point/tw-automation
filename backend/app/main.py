"""TW Automation 웹앱 - 진입점.

    python -m app.main   또는   python app/main.py 로 실행 → http://localhost:8080

구성:
  - .env 로드 (NICEGUI_STORAGE_SECRET, FERNET_KEY)
  - DB 초기화
  - 인증 미들웨어 등록 (/login 외에는 로그인 필요)
  - 각 페이지 모듈 import 로 @ui.page 라우트 등록
"""
import os
from pathlib import Path

from dotenv import load_dotenv
# 주의: 프로젝트 패키지 이름이 'app' 이라, 아래 `import app.pages.*` 가 이름 'app' 을
# 프로젝트 패키지로 덮어쓴다. 충돌을 피하려고 NiceGUI 의 app 은 ng_app 으로 별칭 처리.
from nicegui import app as ng_app, ui

# .env 는 프로젝트 루트 기준으로 명시 로드
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.auth import AuthMiddleware  # noqa: E402
from app.database import init_db  # noqa: E402

# 페이지 라우트 등록 (import 만으로 @ui.page 데코레이터 실행)
import app.pages.login  # noqa: E402,F401
import app.pages.change_password  # noqa: E402,F401  (첫 로그인 비밀번호 변경)
import app.pages.settings  # noqa: E402,F401
import app.pages.task_board  # noqa: E402,F401  (작업 보드 렌더러 — /osupdate 가 쓴다)
import app.pages.mail_detail  # noqa: E402,F401
import app.pages.excel_files  # noqa: E402,F401
import app.pages.products  # noqa: E402,F401  (제품 관리 — OS Update 기준 목록)
import app.pages.os_update  # noqa: E402,F401  (작업 History — 제품별 전 회차)
import app.pages.new_tasks  # noqa: E402,F401  (신규 작업 — 안 끝난 회차만 건별로)
import app.pages.portal_edit  # noqa: E402,F401
import app.pages.templates_page  # noqa: E402,F401
import app.pages.mail_templates_page  # noqa: E402,F401  (메일 문구 — 관리자 전용)
import app.pages.credentials  # noqa: E402,F401
import app.pages.accounts  # noqa: E402,F401  (계정 관리 — 관리자 계정 생성 + 발송 이메일 등록)
import app.pages.usage  # noqa: E402,F401  (Claude 사용량 대시보드)
import app.pages.mid_review  # noqa: E402,F401  (RN 승인)
import app.pages.final_review  # noqa: E402,F401  (최종 검수 — RN for Sales)
import app.usage  # noqa: E402

app.usage.install()  # 모든 Claude 호출을 자동 계측(토큰·비용 기록)

# Claude 호출을 가로채는 자리. 구독(Claude Code)과 API 열쇠 **둘 다** 이리로 간다.
# 어느 쪽으로 부를지는 [설정 → Claude] 에서 고르고, 부를 때마다 그 값을 본다.
# 끼우는 자리가 anthropic.Anthropic 이라 호출부 열여섯 군데는 한 줄도 바뀌지 않는다.
# 계측(app.usage)보다 **뒤에** 끼워야 사용량이 그대로 기록된다.
from app.integrations import claude_cli as _CCLI  # noqa: E402

if _CCLI.install():
    _st = _CCLI.status()
    if _st["backend"] == _CCLI.SUBSCRIPTION:
        print(f"[startup] Claude 호출 → Claude Code 경유(구독) · "
              f"{_st['claude'] or '⚠ claude 명령을 찾지 못했습니다'}", flush=True)
    else:
        print("[startup] Claude 호출 → API 열쇠 · "
              + ("등록됨" if _st["has_key"] else "⚠ 열쇠가 없습니다"), flush=True)
import app.pages.license  # noqa: E402,F401  (PMDM License 발급 — 독립 모듈)
import app.pages.whitelist  # noqa: E402,F401  (FOTA WhiteList 등록 — 독립 모듈, DB 격리)
import app.pages.whitelist_mail  # noqa: E402,F401  (WhiteList 전용 메일함 — SN 룰 필터)
import app.pages.issues  # noqa: E402,F401  (Solution Buffer — Redmine 감시·SB 문서화, DB 격리)
import app.pages.demo  # noqa: E402,F401  (시연 화면 — 실제 데이터 없음)
import app.artifact_routes  # noqa: E402,F401
import app.background  # noqa: E402  (메일 자동 동기화·정리·알림 타이머)
from app.theme import apply_theme  # noqa: E402

app.background.install()  # 접속자가 없어도 도는 서버 레벨 주기 작업

# 로컬 에디터 정적 자산(TinyMCE, SmartEditor2)을 /static 으로 서빙 (로그인 세션에서만 접근)
_STATIC_DIR = Path(__file__).resolve().parent / "static"
if _STATIC_DIR.exists():
    ng_app.add_static_files("/static", str(_STATIC_DIR))


@ui.page("/")
def home():
    apply_theme()
    # 로그인된 사용자는 OS Update 작업 화면으로(메일함 페이지는 없앴다).
    ui.navigate.to("/osupdate")


def _startup() -> None:
    init_db()
    # Release Note 단계를 둘로 가른 뒤(2026-08-27), 예전 작업을 알맞은 단계에 앉힌다.
    # **화면이 열리기 전에** 끝내야 한다 — 보드를 먼저 열면 새 단계가 '대기' 로 만들어져
    # 다 끝난 작업이 미완료로 보인다. 한 번만 돌고, 두 번째부터는 스스로 건너뛴다.
    from app.pipeline import rn_stage_split

    _sp = rn_stage_split.migrate_once()
    if not _sp.get("skipped"):
        print(f"[startup] RN 단계 분리 이관 — 옮김 {_sp['moved']} · 규칙별 {_sp['rules']}",
              flush=True)
    # 사내 인원 명부가 비어 있으면 예전 목록(DQA json · 하드코딩 회신 제외)을 한 번만 옮긴다.
    from app import staff

    n = staff.seed_from_legacy()
    if n:
        print(f"[startup] 사내 인원 명부 이관 {n}명", flush=True)
    add, upd = staff.merge_team_roster()      # 팀 명단(구 rn_select._ROSTER_RAW) 합치기
    if add or upd:
        print(f"[startup] 팀 명단 합침 — 추가 {add}명 · 갱신 {upd}명", flush=True)


def run() -> None:
    ng_app.add_middleware(AuthMiddleware)
    ng_app.on_startup(_startup)
    ui.run(
        title="TW Automation",
        storage_secret=os.getenv("NICEGUI_STORAGE_SECRET", "dev-secret"),
        reload=False,
        host=os.getenv("TW_HOST", "0.0.0.0"),   # 모든 인터페이스 바인딩(사내망에서 접속)
        port=int(os.getenv("TW_PORT", "8080")),
        show=False,                              # 헤드리스 서버 — 브라우저 자동 오픈 안 함
        # 화면이 자꾸 끊겼다(사용자 지적 2026-08-12). 기본값 3초에서는 NiceGUI 가
        # ping_timeout 을 2초로 잡아, 서버가 2초만 밀려도 연결을 끊는다. 이 앱은 Claude
        # 호출·Redmine 폴링·FOTA 조회처럼 밖으로 나가는 일이 많고 그중 타임아웃이 10초짜리도
        # 있어 그 문턱을 쉽게 넘긴다. 30초면 ping_timeout 이 12초라 잠깐 밀려도 버틴다.
        reconnect_timeout=30.0,
    )


if __name__ in {"__main__", "__mp_main__"}:
    run()
