"""공용 수신함 동기화 버튼 — 여러 서비스 화면이 함께 쓴다.

동기화는 특정 서비스의 일이 아니라 **모든 서비스의 입구**다. 예전에는 통합 메일함
(/mail)이 이 버튼을 갖고 있었는데, 그 페이지를 없애면서 쓰는 쪽(WhiteList · OS Update)
각각에 두었다. 로직이 갈라지지 않도록 여기 한 벌만 둔다.
"""
from __future__ import annotations

from nicegui import run, ui

from app import background
from app.auth import current_user_id
from app.mail.prune import prune_mails
from app.mail.service import NoCredentialError


async def sync_mailbox(btn, on_done) -> None:
    """새 메일 받기 → 오래된 메일 정리 → 화면 갱신.

    btn 은 도는 동안 잠근다(두 번 눌러 동시에 돌지 않도록).
    """
    btn.disable()
    n = ui.notification("메일을 가져오는 중…", spinner=True, timeout=None)
    try:
        count = await run.io_bound(background.locked_sync, current_user_id())
        try:
            pruned = await run.io_bound(prune_mails, current_user_id())
        except Exception:  # noqa: BLE001
            pruned = 0
        n.dismiss()
        msg = f"새 메일 {count}통을 가져왔습니다." if count else "새 메일이 없습니다."
        if pruned:
            msg += f" (오래된 메일 {pruned}건 자동 정리)"
        ui.notify(msg, color="positive" if count else "info")
        on_done()
    except NoCredentialError as e:
        n.dismiss()
        ui.notify(str(e), color="negative")
        ui.navigate.to("/settings")
    except Exception as e:  # noqa: BLE001
        n.dismiss()
        ui.notify(f"동기화 오류: {e}", color="negative")
    finally:
        btn.enable()
