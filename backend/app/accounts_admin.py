"""계정 관리(권한 변경·삭제) 서비스 — 관리자 전용.

**삭제 시 데이터 보존이 핵심.** 메일함은 팀 공용 보드라 CachedMail.user_id 는 '처음 받은
사람'일 뿐이다. User 관계에 cascade="all, delete-orphan" 이 걸려 있어 ORM 으로 그냥 지우면
그 사람이 받은 **공용 메일과 단계 기록까지 함께 삭제**된다. 그래서 삭제 전에 공용 데이터를
다른 관리자에게 이관하고, 개인 자격증명만 지운다.
"""
from __future__ import annotations

from app.auth import FINAL_REVIEWER_ROLE, MID_REVIEWER_ROLE
from app.database import get_session
from app.models import (
    CachedMail,
    DropboxCredential,
    FotaEntry,
    MailchimpCredential,
    MailCredential,
    MailStage,
    SeenUidl,
    SftpCredential,
    User,
    WebCredential,
)

VALID_ROLES = ("user", MID_REVIEWER_ROLE, FINAL_REVIEWER_ROLE, "admin")
ROLE_LABEL = {"admin": "시스템 관리자", FINAL_REVIEWER_ROLE: "최종 승인자",
              MID_REVIEWER_ROLE: "RN 승인자", "user": "일반 사용자"}

# 계정과 함께 지우는 개인 자격증명(공용 데이터 아님)
_PERSONAL = (MailCredential, WebCredential, SftpCredential, DropboxCredential,
             MailchimpCredential, SeenUidl)
# 팀 공용이라 삭제하지 않고 다른 관리자에게 이관하는 데이터
_SHARED = (CachedMail, MailStage, FotaEntry)


def reset_password(user_id: int) -> tuple[bool, str, str]:
    """비밀번호를 초기값으로 되돌리고 다음 로그인에서 바꾸게 한다.

    반환 (성공, 안내, 아이디). 안내 메일 발송은 호출부가 한다 — 발신 계정이
    '초기화를 실행한 관리자' 여야 해서 여기서는 누구인지 알 수 없다.
    """
    from app.auth import INITIAL_PASSWORD
    from app.security import hash_password

    with get_session() as db:
        u = db.query(User).filter_by(id=user_id).first()
        if u is None:
            return False, "계정을 찾을 수 없습니다.", ""
        u.password_hash = hash_password(INITIAL_PASSWORD)
        u.must_change_password = True
        db.commit()
        return True, f"'{u.username}' 비밀번호를 초기화했습니다.", u.username


def _admin_count(db, exclude_id: int | None = None) -> int:
    q = db.query(User).filter(User.role == "admin")
    if exclude_id is not None:
        q = q.filter(User.id != exclude_id)
    return q.count()


def set_user_role(target_id: int, role: str, acting_id: int) -> tuple[bool, str]:
    """계정 권한 변경. 마지막 관리자를 강등하는 것은 막는다."""
    if role not in VALID_ROLES:
        return False, f"알 수 없는 역할입니다: {role}"
    with get_session() as db:
        u = db.query(User).filter_by(id=target_id).first()
        if u is None:
            return False, "계정을 찾을 수 없습니다."
        if u.role == role:
            return True, f"'{u.username}' 은(는) 이미 {ROLE_LABEL[role]}입니다."
        if u.role == "admin" and role != "admin" and _admin_count(db, exclude_id=u.id) == 0:
            return False, "마지막 관리자는 권한을 낮출 수 없습니다. 다른 관리자를 먼저 지정하세요."
        u.role = role
        db.commit()
        return True, f"'{u.username}' 을(를) {ROLE_LABEL[role]}(으)로 변경했습니다."


def delete_user(target_id: int, acting_id: int) -> tuple[bool, str]:
    """계정 삭제. 공용 데이터(메일·단계·FOTA)는 실행한 관리자에게 이관하고 개인 자격증명만 지운다."""
    if target_id == acting_id:
        return False, "본인 계정은 삭제할 수 없습니다."
    with get_session() as db:
        u = db.query(User).filter_by(id=target_id).first()
        if u is None:
            return False, "계정을 찾을 수 없습니다."
        if u.role == "admin" and _admin_count(db, exclude_id=u.id) == 0:
            return False, "마지막 관리자는 삭제할 수 없습니다."
        if db.query(User).filter_by(id=acting_id).first() is None:
            return False, "이관받을 관리자 계정을 찾을 수 없습니다."

        uname = u.username
        moved = 0
        for model in _SHARED:                      # 공용 데이터 → 실행 관리자에게 이관(보존)
            moved += (
                db.query(model).filter(model.user_id == target_id)
                .update({model.user_id: acting_id}, synchronize_session=False)
            )
        for model in _PERSONAL:                    # 개인 자격증명·수신이력 → 삭제
            db.query(model).filter(model.user_id == target_id).delete(
                synchronize_session=False)
        db.flush()
        db.delete(db.query(User).filter_by(id=target_id).first())
        db.commit()
        return True, f"'{uname}' 계정을 삭제했습니다. (공용 데이터 {moved}건은 보존·이관)"
