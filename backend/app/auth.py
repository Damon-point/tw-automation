"""인증: 세션 상태 + 미인증 요청 리다이렉트 미들웨어.

NiceGUI 의 app.storage.user (브라우저 세션 쿠키 기반)에 로그인 상태를 저장한다.
미인증 상태로 보호된 페이지에 접근하면 /login 으로 보낸다.
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse

from nicegui import app

from app.database import get_session
from app.models import User
from app.security import hash_password, verify_password

# 로그인 없이 접근 가능한 경로
# /demo 는 시연 화면이다 — DB·파일서버·외부 연동이 전부 끊겨 있고 진짜 데이터가
# 없으므로 로그인 없이 연다(발표 자리에서 계정 없이 바로 띄울 수 있어야 한다).
UNRESTRICTED_PAGES = {"/login", "/demo",
                      # 시연 화면이 iframe 으로 부르는 뉴스레터 미리보기(로컬 렌더).
                      "/demo/newsletter/kr", "/demo/newsletter/en"}

# 시스템 관리자 — 모든 페이지에 들어간다(결재자인 '최종 승인자'와 별개).
ADMIN_ROLE = "admin"

# RN 승인자(mid_reviewer)가 쓸 수 있는 페이지 — RN 승인 + 본인 이메일 등록뿐.
# (/accounts 는 관리자 기능이 is_admin() 으로 이미 가려져 있어 '내 발송 계정'만 보인다.)
#: 관리자가 계정을 만들 때 넣는 초기 비밀번호. 공통값이라 그대로 두면 누구나 아는
#: 비밀번호가 되므로, 이 값으로 만들어진 계정은 첫 로그인에서 반드시 바꾸게 한다.
INITIAL_PASSWORD = "1234"

#: 비밀번호 변경 화면 — 로그인한 사람이면 역할과 무관하게 언제나 들어갈 수 있다.
CHANGE_PW_PAGE = "/change-password"

MID_REVIEW_PAGE = "/reviews"
MID_REVIEWER_PAGES = {MID_REVIEW_PAGE, "/accounts", CHANGE_PW_PAGE, "/login"}

# 최종 승인자(final_reviewer)가 쓸 수 있는 페이지 — 최종 검수 + 본인 이메일 등록뿐.
# admin 과 **분리된 역할**이다(2026-08-04). admin 은 시스템 관리자라 모든 페이지에
# 들어갈 수 있고, 최종 승인자는 RN for Sales·Newsletter 결재만 한다.
FINAL_REVIEW_PAGE = "/final-reviews"
# License 발급 승인도 최종 승인자의 결재 업무라 /licenses 를 함께 연다(2026-08-05).
FINAL_REVIEWER_PAGES = {FINAL_REVIEW_PAGE, "/licenses", "/accounts",
                        CHANGE_PW_PAGE, "/login"}


def _is_infra_path(path: str) -> bool:
    """페이지가 아닌 프레임워크/자산 경로(차단하면 화면이 깨진다)."""
    return (
        path.startswith("/_nicegui")
        or path.startswith("/_nicegui_ws")
        # 로컬 에디터 라이브러리 자산(TinyMCE/SmartEditor2) — 민감정보 아님, iframe 로딩 보장
        or path.startswith("/static/")
    )


def _is_document_path(path: str) -> bool:
    """화면이 불러가는 **문서 파일** 경로 — 페이지가 아니라 리소스다.

    역할별 페이지 제한을 여기에 그대로 걸면, RN 승인자가 자기 승인 화면 안에서
    RN 미리보기(iframe)조차 못 불러온다(실측: /rn/preview 가 307 로 /reviews 에 튕김).
    로그인 검사는 위에서 이미 하므로, 인증된 사용자면 역할과 무관하게 통과시킨다.
    각 라우트가 자체적으로 경로·소유자를 다시 확인한다.
    """
    return path.startswith("/rn/preview/") or path.startswith("/artifact/")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not app.storage.user.get("authenticated", False):
            if path not in UNRESTRICTED_PAGES and not _is_infra_path(path):
                app.storage.user["referrer_path"] = path
                return RedirectResponse("/login")
        elif not _is_infra_path(path) and not _is_document_path(path):
            # 초기 비밀번호를 아직 안 바꿨으면 다른 화면으로 보내지 않는다.
            # (역할 판정보다 먼저 본다 — 결재자도 예외가 아니다.)
            if app.storage.user.get("must_change_password") and path != CHANGE_PW_PAGE:
                return RedirectResponse(CHANGE_PW_PAGE)
            role = app.storage.user.get("role")
            if role == MID_REVIEWER_ROLE:
                # RN 승인자는 승인 화면과 이메일 등록 외 다른 페이지에 들어갈 수 없다.
                if path not in MID_REVIEWER_PAGES:
                    return RedirectResponse("/reviews")
            elif role == FINAL_REVIEWER_ROLE:
                # 최종 승인자도 마찬가지 — 최종 검수와 이메일 등록만.
                if path not in FINAL_REVIEWER_PAGES:
                    return RedirectResponse(FINAL_REVIEW_PAGE)
            elif path == MID_REVIEW_PAGE and role != ADMIN_ROLE:
                # RN 승인은 RN 승인자 화면이다. 다만 **시스템 관리자는 예외** —
                # admin 은 모든 페이지에 들어갈 수 있어야 한다(사용자 확정 2026-08-05).
                return RedirectResponse("/osupdate")
        return await call_next(request)


def authenticate(username: str, password: str) -> bool:
    """자격 확인 후 성공하면 세션에 로그인 상태를 기록한다."""
    with get_session() as db:
        user = db.query(User).filter(User.username == username).first()
        if user and verify_password(password, user.password_hash):
            app.storage.user.update(
                {
                    "authenticated": True,
                    "user_id": user.id,
                    "username": user.username,
                    "role": user.role or "user",
                    "must_change_password": bool(
                        getattr(user, "must_change_password", False)),
                }
            )
            return True
    return False


def must_change_password() -> bool:
    """지금 로그인한 사람이 비밀번호를 바꿔야 하는 상태인가."""
    return bool(app.storage.user.get("must_change_password"))


def change_password(new_password: str) -> tuple[bool, str]:
    """현재 로그인한 계정의 비밀번호를 바꾼다. (성공 여부, 안내 문구).

    초기 비밀번호를 그대로 다시 쓰는 것은 막는다 — 바꾸게 한 이유가 사라진다.
    """
    pw = (new_password or "").strip()
    if len(pw) < 4:
        return False, "비밀번호는 4자 이상이어야 합니다."
    if pw == INITIAL_PASSWORD:
        return False, "초기 비밀번호와 다른 값으로 정해 주세요."
    uid = current_user_id()
    if uid is None:
        return False, "로그인 상태가 아닙니다."
    with get_session() as db:
        user = db.query(User).filter_by(id=uid).first()
        if user is None:
            return False, "계정을 찾을 수 없습니다."
        user.password_hash = hash_password(pw)
        user.must_change_password = False
        db.commit()
    app.storage.user["must_change_password"] = False
    return True, "비밀번호를 변경했습니다."


def logout() -> None:
    app.storage.user.clear()


def current_user_id() -> int | None:
    return app.storage.user.get("user_id")


def current_username() -> str | None:
    return app.storage.user.get("username")


MID_REVIEWER_ROLE = "mid_reviewer"   # RN 승인자 역할값(저장값은 호환 위해 유지)
FINAL_REVIEWER_ROLE = "final_reviewer"   # 최종 승인자 역할값(admin 과 분리)


def current_user_role() -> str:
    """현재 로그인 계정의 역할. 세션에 없으면 DB 에서 조회(구 세션 호환)."""
    role = app.storage.user.get("role")
    if role:
        return role
    uid = app.storage.user.get("user_id")
    if uid is not None:
        with get_session() as db:
            u = db.query(User).filter_by(id=uid).first()
            if u:
                app.storage.user["role"] = u.role or "user"
                return u.role or "user"
    return "user"


def is_admin() -> bool:
    """**시스템 관리자** — 모든 페이지에 접근한다.

    2026-08-04 부터 결재자인 '최종 승인자'와 분리됐다. 결재 권한만 보려면
    can_final_decide() 를 쓴다.
    """
    return current_user_role() == ADMIN_ROLE


def is_mid_reviewer() -> bool:
    """RN 승인자 — RN 초안을 먼저 확인·승인하고 단말 확인값을 입력하는 담당자."""
    return current_user_role() == MID_REVIEWER_ROLE


def is_final_reviewer() -> bool:
    """최종 승인자 — RN for Sales·Newsletter 결재만 하는 담당자(admin 과 별개 역할)."""
    return current_user_role() == FINAL_REVIEWER_ROLE


def can_mid_decide() -> bool:
    """RN 승인(1차) 권한 — RN 승인자, 그리고 모든 권한을 가진 admin.

    최종 검수의 can_final_decide 와 같은 규칙이다. 예전에는 /reviews 가
    is_mid_reviewer() 만 통과시켜 **admin 도 자기 화면에서 막혔다** — 미들웨어는
    admin 을 들여보내는데 화면이 '전용 페이지입니다' 만 띄웠다(2026-09-03).
    """
    return is_mid_reviewer() or is_admin()


def can_final_decide() -> bool:
    """최종 결재(승인/반려) 권한 — 최종 승인자, 그리고 모든 권한을 가진 admin.

    역할을 나눈 뒤에도 admin 은 막지 않는다. 최종 승인자가 자리를 비웠을 때
    시스템 관리자가 처리할 길이 없으면 업무가 멈춘다.
    """
    return is_final_reviewer() or is_admin()
