"""서버 백그라운드 작업 — 브라우저가 하나도 안 열려 있어도 도는 주기 작업.

  · 60초마다   공용 수신함 동기화 → 새 메일이 있으면 WhiteList 판별(Claude)까지
  · 5분마다    WhiteList 메일함 판별 폴링 — 위에서 놓친 미판정 건을 훑는다
  · 1시간마다  오래된 메일 정리(prune)
  · 매일 11:00 / 16:00  WhiteList 미처리 작업이 있으면 담당자에게 알림 메일
  · 5분마다    Redmine salesissue 이슈/이벤트 증분 수집 (최초 1회는 전수 백필)

NiceGUI 의 `app.timer` 는 **서버 프로세스 레벨** 타이머다(클라이언트에 묶이는 `ui.timer`
와 다르다). 그래서 접속자가 없어도 동작한다.

폴링 비용(실측, 사내 mail.pointmobile.com 기준): 접속+UIDL 749줄 = 약 0.2초.
신규 7건을 본문까지 받아도 0.56초. 60초 주기는 서버에 사실상 부담이 없다.
"""
from __future__ import annotations

import json
import os
import threading
import traceback
from datetime import datetime
from pathlib import Path

from nicegui import app as ng_app, run

# ---- 주기 설정 (env 로 덮어쓸 수 있다) ----------------------------------
SYNC_INTERVAL_SEC = int(os.getenv("TW_SYNC_INTERVAL_SEC", "60"))
PRUNE_INTERVAL_SEC = int(os.getenv("TW_PRUNE_INTERVAL_SEC", "3600"))
# 알림을 보낼 시각(정시 기준). 그 시각을 지나면 그날 1회만 보낸다.
NOTIFY_HOURS = tuple(
    int(h) for h in (os.getenv("TW_NOTIFY_HOURS", "11,16").split(",")) if h.strip()
)
# 알림 수신 대상 역할. 쉼표 구분(User.role 값).
NOTIFY_ROLES = tuple(
    r.strip() for r in os.getenv("TW_NOTIFY_ROLES", "user").split(",") if r.strip()
)
# Redmine 이슈 폴링 주기. 메일과 달리 이슈는 분 단위로 급하지 않고, 한 틱이
# 이슈 목록 1콜 + 변경분만큼의 상세 콜이라 5분이면 충분하다.
REDMINE_POLL_SEC = int(os.getenv("TW_REDMINE_POLL_SEC", "300"))
REDMINE_ENABLED = os.getenv("TW_REDMINE_ENABLED", "1").strip().lower() not in (
    "0", "false", "no", "off", ""
)
# WhiteList 메일함 판별 폴링 주기.
# 메일 자체는 위 60초 동기화가 이미 받아 온다(같은 CachedMail 을 본다). 여기서 도는 건
# **판별**이다 — 동기화 직후 판별은 '새 메일이 들어왔을 때'만 돌아서, 그때 실패했거나
# 게이트가 닫혀 있었던 건은 사람이 화면을 열 때까지 미판정으로 남았다. 그 그물이다.
# 후보 목록을 매번 훑으므로 60초는 과하고, 판정이 급한 일도 아니라 5분으로 둔다.
WL_POLL_SEC = int(os.getenv("TW_WL_POLL_SEC", "300"))
WL_POLL_ENABLED = os.getenv("TW_WL_POLL_ENABLED", "1").strip().lower() not in (
    "0", "false", "no", "off", ""
)


# SB 관련 동작(자동 초안 생성 · 자동 검토요청 메일 · 틱당 건수)은 **화면에서 바꾼다**.
# app/issuetrack/settings.py 가 DB 값 > env > 코드 기본값 순으로 읽는다. 재시작 없이
# 켜고 끌 수 있어야 하는 항목이라 여기서 상수로 굳히지 않는다.
#   · 자동 초안 생성  기본 켜짐  — Resolved 전환이 주 2건 남짓이라 비용 부담이 작다
#   · 자동 검토 메일  기본 꺼짐  — 사내 담당자에게 실제로 나가는 메일이라 사람이 켠다

_WORK = Path(__file__).resolve().parent.parent / "work"
_STATE_FILE = _WORK / "background_state.json"

# 동기화가 주기보다 오래 걸릴 때 작업이 쌓이지 않게 한다.
# 수동 [동기화] 버튼과도 같은 락을 공유한다.
sync_lock = threading.Lock()

_fail_streak = 0        # 연속 실패 횟수 → 백오프에 사용
_last_error = ""


def _log(msg: str) -> None:
    """server.log 로 나가는 표준출력에 남긴다(start_server.bat 이 리다이렉트)."""
    print(f"[background {datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# ---- 상태 저장 (재부팅해도 그날 알림을 두 번 보내지 않게) -----------------
def _load_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_state(state: dict) -> None:
    try:
        _WORK.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    except Exception:  # noqa: BLE001
        _log("상태 저장 실패\n" + traceback.format_exc())


# ---- UI 변경 감지 --------------------------------------------------------
def data_version() -> tuple[int, int]:
    """메일 데이터의 '판번호'. 값이 바뀌었을 때만 화면을 다시 그리려고 쓴다.

    (총 건수, 최대 id) — 두 값 모두 인덱스만 보므로 매우 싸다.
    """
    from sqlalchemy import func

    from app.database import get_session
    from app.models import CachedMail

    try:
        with get_session() as db:
            n = db.query(func.count(CachedMail.id)).scalar() or 0
            mx = db.query(func.max(CachedMail.id)).scalar() or 0
        return int(n), int(mx)
    except Exception:  # noqa: BLE001
        return (-1, -1)


def _default_user_id() -> int:
    """CachedMail.user_id(최초 수신자 기록용)에 쓸 사용자. 관리자 우선."""
    from app.database import get_session
    from app.models import User

    try:
        with get_session() as db:
            u = (db.query(User).filter_by(role="admin").order_by(User.id).first()
                 or db.query(User).order_by(User.id).first())
            return u.id if u else 1
    except Exception:  # noqa: BLE001
        return 1


# ---- 동기화 -------------------------------------------------------------
def sync_once(*, run_triage: bool = True) -> dict:
    """동기화 1회(블로킹). 반환 {added, pruned, triaged, error}.

    락을 못 잡으면 이미 도는 중이므로 조용히 건너뛴다(skipped=True).
    """
    global _fail_streak, _last_error

    if not sync_lock.acquire(blocking=False):
        return {"skipped": True, "added": 0, "pruned": 0, "triaged": 0, "error": ""}
    try:
        from app.mail.service import NoCredentialError, sync_mails

        uid = _default_user_id()
        try:
            added = sync_mails(uid)
        except NoCredentialError as e:
            _fail_streak += 1
            _last_error = str(e)
            if _fail_streak in (1, 10, 60):     # 로그 폭주 방지
                _log(f"동기화 불가: {e}")
            return {"added": 0, "pruned": 0, "triaged": 0, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            _fail_streak += 1
            _last_error = str(e)
            if _fail_streak in (1, 5, 30) or _fail_streak % 60 == 0:
                _log(f"동기화 실패({_fail_streak}회 연속): {e}")
            return {"added": 0, "pruned": 0, "triaged": 0, "error": str(e)}

        if _fail_streak:
            _log(f"동기화 복구됨 (연속 실패 {_fail_streak}회 후)")
        _fail_streak = 0
        _last_error = ""

        state = _load_state()
        pruned = 0
        # prune 은 CachedMail·MailStage 전체를 훑으므로 매분 돌리지 않는다.
        last_prune = float(state.get("last_prune_ts") or 0)
        if datetime.now().timestamp() - last_prune >= PRUNE_INTERVAL_SEC:
            try:
                from app.mail.prune import prune_mails
                pruned = prune_mails(uid)
            except Exception:  # noqa: BLE001
                _log("prune 실패\n" + traceback.format_exc())
            state["last_prune_ts"] = datetime.now().timestamp()
            _save_state(state)

        triaged = 0
        if run_triage and added:
            # 이미 판정한 스레드는 캐시로 건너뛰므로, 새 후보가 없으면 API 호출도 없다.
            try:
                from app.pages.whitelist_mail import auto_triage_new
                res = auto_triage_new()
                triaged = int(res.get("asked") or 0)
                if res.get("error"):
                    _log(f"WhiteList 판별: {res['error']}")
            except Exception:  # noqa: BLE001
                _log("WhiteList 판별 실패\n" + traceback.format_exc())

        if added or pruned or triaged:
            _log(f"동기화: 신규 {added}건, 정리 {pruned}건, 판별 {triaged}건")
        return {"added": added, "pruned": pruned, "triaged": triaged, "error": ""}
    finally:
        sync_lock.release()


# ---- WhiteList 알림 메일 -------------------------------------------------
def locked_sync(user_id: int) -> int:
    """수동 [동기화] 버튼용 — 백그라운드 주기 동기화와 겹치지 않게 락을 잡고 실행한다.

    같은 메일함에 POP3 세션 두 개가 동시에 붙으면 서버가 잠금 오류를 낼 수 있고,
    같은 메일을 두 번 저장하려 시도하게 된다.
    """
    from app.mail.service import sync_mails

    with sync_lock:
        return sync_mails(user_id)


def _notify_recipients() -> list[str]:
    """알림 수신자 이메일 목록. NOTIFY_ROLES 역할의 사용자 — 주소는 아이디에서도 찾는다."""
    from app.user_mailer import _role_emails

    out: list[str] = []
    for role in NOTIFY_ROLES:
        for e in _role_emails(role):
            if e not in out:
                out.append(e)
    return out


def _sender_user_id() -> int | None:
    """알림을 돌릴 계정. 판단은 user_mailer 한 곳에서 한다."""
    from app.user_mailer import system_sender_user_id

    return system_sender_user_id()


def send_whitelist_notice() -> dict:
    """WhiteList 미처리 작업이 있으면 알림 메일을 보낸다. 없으면 보내지 않는다.

    반환 {sent, threads, to, error}.
    """
    from app.pages.whitelist_mail import _threads

    try:
        groups = _threads({"q": ""})
    except Exception as e:  # noqa: BLE001
        return {"sent": False, "threads": 0, "to": [], "error": f"목록 조회 실패: {e}"}

    if not groups:
        return {"sent": False, "threads": 0, "to": [], "error": ""}

    to = _notify_recipients()
    if not to:
        return {"sent": False, "threads": len(groups), "to": [],
                "error": f"수신 대상 없음 (role={','.join(NOTIFY_ROLES)} 사용자의 "
                         "메일 자격증명이 등록돼 있지 않습니다)"}

    uid = _sender_user_id()
    if uid is None:
        return {"sent": False, "threads": len(groups), "to": to,
                "error": "발송 계정 없음 — [계정 관리]에서 이메일 자격증명을 등록하세요."}

    lines: list[str] = []
    for i, g in enumerate(groups[:20], 1):
        rep = g.get("rep") or {}
        sns = g.get("serials") or []
        lines.append(f"{i}. {(rep.get('subject') or '(제목 없음)')[:70]}")
        lines.append(f"   발신: {(rep.get('sender') or '')[:60]}")
        if sns:
            shown = ", ".join(list(sns)[:5])
            more = f" 외 {len(sns) - 5}건" if len(sns) > 5 else ""
            lines.append(f"   S/N: {shown}{more}")
        lines.append("")
    if len(groups) > 20:
        lines.append(f"... 외 {len(groups) - 20}건")

    from app import mail_templates as MT
    from app.user_mailer import send_as_user

    subject, body = MT.render(
        "notice_whitelist", 건수=len(groups), 목록="\n".join(lines) + "\n",
        화면주소=f"{os.getenv('TW_APP_URL', 'http://192.168.121.35:8080')}/whitelist")
    ok, err = send_as_user(uid, to, subject, body, as_system=True)
    if not ok:
        return {"sent": False, "threads": len(groups), "to": to, "error": err}
    _log(f"WhiteList 알림 발송: {len(groups)}건 → {', '.join(to)}")
    return {"sent": True, "threads": len(groups), "to": to, "error": ""}


def send_new_round_notice(only_new: bool = True) -> dict:
    """**아직 시작하지 않은 OS Update 회차**가 있으면 알림 메일을 보낸다.

    회의(2026-08-04)에서 확인된 누락 리스크에 대한 대응이다. 배포 공지는 왔는데 아무도
    작업을 시작하지 않으면 그 회차는 조용히 지나간다 — 지금까지는 사람이 /osupdate 를
    열어봐야만 알 수 있었다.

    only_new=True 면 **지난번에 알린 뒤 새로 생긴 회차가 있을 때만** 보낸다. 같은 목록을
    매일 다시 보내면 사람이 읽지 않게 된다.

    반환 {sent, rounds, to, error}.
    """
    from app import os_products as P
    from app import os_update_jobs as J

    try:
        found: list[tuple[dict, dict]] = []
        for r in P.list_products(""):
            for u in J.pending_updates(r):
                found.append((r, u))
    except Exception as e:  # noqa: BLE001
        return {"sent": False, "rounds": 0, "to": [], "error": f"감지 실패: {e}"}

    if not found:
        return {"sent": False, "rounds": 0, "to": [], "error": ""}

    # 회차 식별키 — 제품+버전. 알린 것을 기억해 같은 목록을 반복 발송하지 않는다.
    keys = sorted(f"{r['product']}|{r['android']}|{u['version']}" for r, u in found)
    state = _load_state()
    if only_new and set(keys) <= set(state.get("notified_rounds") or []):
        return {"sent": False, "rounds": len(found), "to": [], "error": ""}

    to = _notify_recipients()
    if not to:
        return {"sent": False, "rounds": len(found), "to": [],
                "error": f"수신 대상 없음 (role={','.join(NOTIFY_ROLES)})"}
    uid = _sender_user_id()
    if uid is None:
        return {"sent": False, "rounds": len(found), "to": to,
                "error": "발송 계정 없음 — [계정 관리]에서 이메일 자격증명을 등록하세요."}

    base = os.getenv("TW_APP_URL", "http://192.168.121.35:8080")
    lines: list[str] = []
    for i, (r, u) in enumerate(found, 1):
        sku = " ".join(x for x in (r["product"], f"A{r['android']}" if r["android"] else "",
                                   r.get("os_type") or "") if x)
        src = "배포 공지만 있음(트리거 메일 없음)" if u.get("source") == "deploy" else (
            "Google 승인 완료" if u.get("approved") else "승인 대기")
        lines.append(f"{i}. {sku} — {u['version'] or '(버전 미정)'}  [{src}]")
        if u.get("basis"):
            lines.append(f"   근거: {u['basis']}")
        lines.append("")

    from app import mail_templates as MT
    from app.user_mailer import send_as_user

    subject, body = MT.render("notice_osupdate", 건수=len(found),
                              화면주소=f"{base}/osupdate",
                              목록="\n".join(lines) + "\n")
    ok, err = send_as_user(uid, to, subject, body, as_system=True)
    if not ok:
        return {"sent": False, "rounds": len(found), "to": to, "error": err}
    state["notified_rounds"] = keys
    _save_state(state)
    _log(f"OS Update 신규 회차 알림 발송: {len(found)}건 → {', '.join(to)}")
    return {"sent": True, "rounds": len(found), "to": to, "error": ""}


def _notify_if_due() -> None:
    """NOTIFY_HOURS 시각이 지났고 그날 아직 안 보냈으면 발송한다.

    '지나면 1회'라 서버가 그 시각에 꺼져 있었어도 켜진 뒤 곧바로 보낸다.
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    state = _load_state()
    changed = False

    for h in NOTIFY_HOURS:
        if now.hour < h:
            continue
        key = f"notified_{h:02d}"
        if state.get(key) == today:
            continue
        # OS Update 신규 회차 알림 — WhiteList 와 별개 목적이라 따로 보낸다.
        # 실패해도 WhiteList 알림을 막지 않는다(둘은 독립된 업무다).
        try:
            _r = send_new_round_notice()
            if _r["error"]:
                _log(f"OS Update 회차 알림 보류: {_r['error']}")
        except Exception:  # noqa: BLE001
            _log("OS Update 회차 알림 오류\n" + traceback.format_exc())

        res = send_whitelist_notice()
        if res["error"]:
            # 설정 문제(수신자·발송계정 미등록)는 고쳐질 때까지 계속 실패하므로
            # **표시하지 않고 다음 주기에 재시도**한다. 다만 60초마다 같은 줄이
            # 쌓이면 로그를 못 읽으니 경고는 하루 한 번만 남긴다.
            warn_key = f"warned_{h:02d}"
            if state.get(warn_key) != today:
                state[warn_key] = today
                changed = True
                _log(f"{h}시 알림 보류: {res['error']} (설정되면 자동 발송됩니다)")
        else:
            # 보냈든(작업 있음) 건너뛰었든(작업 없음) 그날은 처리 완료로 표시한다.
            state[key] = today
            changed = True
            if not res["sent"]:
                _log(f"{h}시 알림: 미처리 WhiteList 작업 없음 — 발송 생략")

    if changed:
        _save_state(state)


# ---- WhiteList 메일함 판별 폴링 ------------------------------------------
_wl_fail_streak = 0
_wl_blocked_streak = 0


def whitelist_poll_once() -> dict:
    """WhiteList 메일함 판별 1회(블로킹).

    새 후보가 없으면 캐시에서 끝나 API 호출이 나가지 않는다 — 매 주기 돌아도 비용이
    붙지 않는다. 게이트가 닫혀 있거나 키가 없으면 대기 건수만 돌아온다.
    """
    global _wl_fail_streak, _wl_blocked_streak

    from app.pages.whitelist_mail import auto_triage_new

    try:
        res = auto_triage_new()
    except Exception as e:  # noqa: BLE001
        _wl_fail_streak += 1
        if _wl_fail_streak in (1, 5, 30) or _wl_fail_streak % 60 == 0:
            _log(f"WhiteList 판별 실패({_wl_fail_streak}회 연속): {e}")
        return {"error": str(e), "asked": 0, "pending": 0}

    if _wl_fail_streak:
        _log(f"WhiteList 판별 복구됨 (연속 실패 {_wl_fail_streak}회 후)")
    _wl_fail_streak = 0

    if res.get("blocked"):
        # 키 미등록·게이트 차단은 고쳐질 때까지 매 주기 같은 결과다. 로그를 아껴 남긴다.
        _wl_blocked_streak += 1
        if _wl_blocked_streak in (1, 12) or _wl_blocked_streak % 144 == 0:
            _log(f"WhiteList 판별 보류: {res.get('error') or '판정이 차단돼 있습니다'}")
        return res
    _wl_blocked_streak = 0

    if res.get("asked"):
        _log(f"WhiteList 메일함 판별: {res['asked']}건")
    return res


async def _whitelist_tick() -> None:
    """WL_POLL_SEC 마다 호출. Claude 호출이 섞이므로 반드시 스레드에서 돈다."""
    try:
        await run.io_bound(whitelist_poll_once)
    except Exception:  # noqa: BLE001
        _log("WhiteList tick 오류\n" + traceback.format_exc())


# ---- Redmine 이슈 수집 ---------------------------------------------------
_redmine_fail_streak = 0


def redmine_poll_once() -> dict:
    """Redmine 증분 수집 1회(블로킹). 최초에는 자동으로 전수 백필로 넘어간다."""
    global _redmine_fail_streak

    from app.issuetrack import sync as it_sync

    try:
        res = it_sync.poll()
    except Exception as e:  # noqa: BLE001
        _redmine_fail_streak += 1
        if _redmine_fail_streak in (1, 5, 30) or _redmine_fail_streak % 60 == 0:
            _log(f"Redmine 수집 실패({_redmine_fail_streak}회 연속): {e}")
        return {"error": str(e)}

    if res.get("error"):
        _redmine_fail_streak += 1
        # 자격증명 미등록처럼 고쳐질 때까지 계속 실패하는 종류라 로그를 아껴 남긴다.
        if _redmine_fail_streak in (1, 5, 30) or _redmine_fail_streak % 60 == 0:
            _log(f"Redmine 수집 실패({_redmine_fail_streak}회 연속): {res['error']}")
        return res

    if _redmine_fail_streak:
        _log(f"Redmine 수집 복구됨 (연속 실패 {_redmine_fail_streak}회 후)")
    _redmine_fail_streak = 0

    if res.get("listed") is not None:                       # 백필 결과
        _log(
            f"Redmine 백필: 이슈 {res.get('listed')}건, 상세 {res.get('detail_done')}건"
            f"(실패 {res.get('detail_failed')}), 이벤트 {res.get('journals')}건"
        )
    elif res.get("detail_done"):                            # 변경분이 있었던 폴링
        msg = (f"Redmine 갱신: 이슈 {res.get('detail_done')}건, "
               f"이벤트 {res.get('journals')}건")
        if res.get("deferred"):
            msg += f" (다음 주기로 이월 {res['deferred']}건)"
        _log(msg)
    return res


def sb_tick_once() -> dict:
    """Resolved 감지 → SB 초안 생성 → 담당자 검토 요청 메일.

    한 틱에서 만드는 초안 수를 묶어 둔다(SB_PER_TICK). Claude 호출은 건당 수십 초·
    수십 원이라, 한꺼번에 몰아 치면 서버가 붙잡히고 비용도 한 번에 튄다.
    감시 시작 전에 이미 Resolved 였던 이슈는 backlog 로만 남고 여기서 건드리지 않는다.
    """
    from app.issuetrack import sb, settings

    cfg = settings.load()
    try:
        scanned = sb.scan()
    except Exception as e:  # noqa: BLE001
        _log(f"SB scan 실패: {e}")
        return {"error": str(e)}

    out: dict = dict(scanned)
    if scanned.get("first_run"):
        s = sb.summary()
        _log(f"SB 감시 시작 — 기존 Resolved {s['backlog']}건은 소급 대기(backlog)로 "
             f"남겨 둡니다. 자동 생성은 지금 이후 해결분부터입니다.")

    made: list[int] = []
    if cfg.sb_auto_generate:
        try:
            gen = sb.generate_pending(limit=cfg.sb_per_tick)
            out.update(gen)
            made = gen.get("generated_ids") or []
            if gen.get("generated"):
                _log(f"SB 초안 {gen['generated']}건 생성"
                     + (f" (실패 {gen['failed']}건)" if gen.get("failed") else ""))
        except Exception as e:  # noqa: BLE001
            _log(f"SB 초안 생성 실패: {e}")

    if cfg.sb_auto_notify:
        # 방금 만든 초안을 **곧바로** 담당자에게 알린다. 이게 이 기능의 본줄기다 —
        # 이슈가 Resolved 되면 담당자가 메일을 받고, 로그인해서 고치거나 올린다.
        try:
            res = sb.notify_many(made)
            # 앞선 틱에서 발송이 실패해 남아 있는 것도 함께 훑는다(재시도 그물).
            retry = sb.notify_pending(limit=cfg.sb_per_tick)
            sent = res.get("sent", 0) + retry.get("sent", 0)
            out["notified"] = sent
            if sent:
                _log(f"SB 검토 요청 메일 {sent}건 발송")
            for err in (res.get("errors") or []) + (retry.get("errors") or []):
                if err:
                    _log(f"SB 알림 발송 실패: {err}")
                    break
        except Exception as e:  # noqa: BLE001
            _log(f"SB 알림 발송 실패: {e}")
    elif made:
        _log(f"SB 초안 {len(made)}건 생성 — 담당자 알림은 꺼져 있습니다"
             " ([설정 → Solution Buffer]에서 켜세요)")
    return out


# ---- 타이머 -------------------------------------------------------------
async def _tick() -> None:
    """60초마다 호출. 블로킹 작업은 전부 스레드로 넘겨 이벤트 루프를 막지 않는다."""
    try:
        await run.io_bound(sync_once)
    except Exception:  # noqa: BLE001
        _log("동기화 tick 오류\n" + traceback.format_exc())
    try:
        await run.io_bound(_notify_if_due)
    except Exception:  # noqa: BLE001
        _log("알림 tick 오류\n" + traceback.format_exc())
    await _warm_os_update()


async def _warm_os_update() -> None:
    """/osupdate 가 쓰는 메일 조회 결과를 **미리 스레드에서** 계산해 둔다.

    이 계산(배포 공지 후보·트리거 메일 파싱)은 메일 전문을 훑어 수 초가 걸린다.
    화면을 그릴 때 처음 하면 그동안 이벤트 루프가 막혀 모든 사용자의 화면이 멈춘다.
    새 메일이 들어오면 캐시가 무효화되므로, 동기화 직후 여기서 다시 채워 둔다.
    """
    try:
        await run.io_bound(_warm_os_update_sync)
    except Exception:  # noqa: BLE001
        _log("OS Update 예열 오류\n" + traceback.format_exc())


def _warm_os_update_sync() -> None:
    from app import os_products as P
    from app import os_update_jobs as J

    for r in P.list_products(""):
        J.version_rows(r)


async def _warm_startup() -> None:
    """기동 직후 한 번 — 첫 사용자가 뒤집어쓰던 비용을 미리 치른다."""
    try:
        await run.io_bound(_warm_imports_sync)
    except Exception:  # noqa: BLE001
        _log("모듈 예열 오류\n" + traceback.format_exc())
    await _warm_os_update()
    try:
        await run.io_bound(_warm_whitelist_sync)
    except Exception:  # noqa: BLE001
        _log("WhiteList 예열 오류\n" + traceback.format_exc())


def _warm_imports_sync() -> None:
    """무거운 모듈을 미리 읽어 둔다.

    파이썬은 import 를 처음 실행할 때 읽는다. 이 모듈들은 화면을 그리는 함수 **안에서**
    늦게 import 되는데(순환 참조를 피하려고), 그러면 그 비용을 재시작 후 처음 그 화면을
    연 사람이 전부 뒤집어쓴다 — 실측 2.6초, 그중 anthropic 하나가 1.5초다(2026-08-14).
    """
    import importlib

    for name in ("app.pipeline.rn_research", "app.office.pdf_tool", "app.agent.runner",
                 "app.pipeline.excel_docs", "app.pipeline.version_resolve"):
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001
            pass                       # 예열은 거들 뿐이다 — 실패해도 화면은 그대로 돈다


def _warm_whitelist_sync() -> None:
    """WhiteList 메일함 목록을 한 번 계산해 캐시를 채워 둔다.

    두 가지가 한꺼번에 데워진다.
      · 메일 분류 — 메일 2,172통의 본문을 정규식으로 훑는다(첫 회 4초).
      · FOTA 등록 조회 — 일련번호 6,851개를 원격 DB 에 묻는다(첫 회 3초).
    둘 다 첫 회만 비싸다. 그 비용을 첫 사용자가 아니라 기동 직후 여기서 치른다.
    """
    from app.pages.whitelist_mail import load_threads

    load_threads()


def _clear_stale_running() -> None:
    """기동 시 남아 있는 '실행 중' 단계를 '대기' 로 되돌린다.

    단계는 워커 스레드에서 돈다 — 서버가 죽으면 그 스레드도 사라진다. 그런데 DB 의
    상태는 '실행 중' 인 채로 남아, 아무도 돌리고 있지 않은데 재실행이 막힌다.
    프로세스가 새로 뜬 시점에 '실행 중' 인 단계는 정의상 유령이므로 모두 되돌린다.
    """
    from sqlalchemy import update

    from app.database import get_session
    from app.models import MailStage, OsUpdateJobStage
    from app.stages import PENDING, RUNNING

    n = 0
    try:
        with get_session() as db:
            for model in (MailStage, OsUpdateJobStage):
                n += db.execute(
                    update(model).where(model.status == RUNNING)
                    .values(status=PENDING)
                ).rowcount or 0
            db.commit()
    except Exception:  # noqa: BLE001
        _log("실행 중 단계 정리 오류\n" + traceback.format_exc())
        return
    if n:
        _log(f"이전 실행에서 남은 '실행 중' 단계 {n}건을 '대기' 로 되돌렸습니다.")


async def _redmine_tick() -> None:
    """5분마다 호출. 최초 백필은 수 분 걸리므로 반드시 스레드에서 돈다."""
    try:
        res = await run.io_bound(redmine_poll_once)
    except Exception:  # noqa: BLE001
        _log("Redmine tick 오류\n" + traceback.format_exc())
        return
    # 수집이 성공했을 때만 SB 로 넘어간다. 수집이 실패한 상태에서 판정하면
    # '아직 안 들어온 이벤트'를 근거로 문서를 만들게 된다.
    if res.get("error"):
        return
    try:
        await run.io_bound(sb_tick_once)
    except Exception:  # noqa: BLE001
        _log("SB tick 오류\n" + traceback.format_exc())


def install() -> None:
    """main.py 에서 호출 — 서버 기동 시 백그라운드 타이머를 등록한다."""
    def _start() -> None:
        # 죽은 스레드가 남긴 '실행 중' 표시를 먼저 걷어낸다(아무도 안 돌리고 있다).
        _clear_stale_running()
        # immediate=False: 기동 직후는 DB 초기화 등과 겹치지 않게 한 주기 쉬고 시작.
        ng_app.timer(SYNC_INTERVAL_SEC, _tick, immediate=False)
        # 기동 직후 한 번 예열 — 첫 사용자가 화면을 열 때 몇 초씩 멈추지 않도록.
        ng_app.timer(3, _warm_startup, once=True)
        _log(
            f"백그라운드 시작 — 동기화 {SYNC_INTERVAL_SEC}초 / "
            f"정리 {PRUNE_INTERVAL_SEC}초 / "
            f"알림 {','.join(f'{h}시' for h in NOTIFY_HOURS)} "
            f"(수신 역할: {','.join(NOTIFY_ROLES)})"
        )
        if WL_POLL_ENABLED:
            ng_app.timer(WL_POLL_SEC, _whitelist_tick, immediate=False)
            # 기동 직후 한 번 — 서버가 꺼져 있는 동안 쌓인 미판정을 바로 훑는다.
            ng_app.timer(25, _whitelist_tick, once=True)
            _log(f"WhiteList 메일함 판별 폴링 시작 — {WL_POLL_SEC}초")
        if REDMINE_ENABLED:
            ng_app.timer(REDMINE_POLL_SEC, _redmine_tick, immediate=False)
            # 첫 회는 5분을 기다리지 않는다. 백필이 아직이면 이 한 방이 백필을 돌린다.
            ng_app.timer(20, _redmine_tick, once=True)
            from app.issuetrack import settings as it_settings

            cfg = it_settings.load()
            _log(f"Redmine 이슈 수집 시작 — 폴링 {REDMINE_POLL_SEC}초 / "
                 f"SB 초안 자동생성 {'ON' if cfg.sb_auto_generate else 'OFF'} · "
                 f"검토메일 {'ON' if cfg.sb_auto_notify else 'OFF'} "
                 f"(틱당 {cfg.sb_per_tick}건) — [설정 → Solution Buffer]에서 변경")

    ng_app.on_startup(_start)
