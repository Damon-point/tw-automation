# -*- coding: utf-8 -*-
"""DQA FOTA ① 다운로드+Dropbox 업로드 — 팝업 수명과 분리된 백그라운드 작업.

**왜 따로 두는가:** 예전에는 팝업의 클릭 처리기가 `run.io_bound` 로 직접 돌렸다.
그러면 작업이 그 화면(클라이언트)에 매달린다. 화면을 새로 열거나 팝업을 닫으면
NiceGUI 가 그 클라이언트의 요소를 지우고, 돌아온 작업이 알림을 그리려다 죽는다
(실측 2026-08-24 10:57: 2.4GB 다 받아 놓고 `The parent element this slot belongs to
has been deleted` 로 Dropbox 업로드 직전에 통째로 날아갔다. 3GB 를 다시 받았다).

그래서 실제 일은 여기 데몬 스레드가 한다. 화면은 `snapshot()` 을 폴링해 그리기만
하므로, 팝업을 닫든 브라우저를 새로 열든 작업은 계속 돌고 다시 열면 이어서 보인다.

작업 키는 메일 id 다 — 같은 요청 메일에 대해 두 벌이 동시에 돌지 못하게 한다
(오늘 아침 같은 파일을 두 폴더에 3GB 씩 받은 일이 그래서 생겼다).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from app.atomic_io import atomic_write_text
from app.pipeline.dqa_fota import DqaCancelled

_lock = threading.Lock()
_jobs: dict[str, dict] = {}

#: 진행률을 이보다 자주 쓰지 않는다 — 1MB 마다 부르니 초당 수백 번이 된다.
_TICK = 0.25

#: 끝난 작업 기록. 서버를 재시작해도 '이미 받아서 올린 것'을 잃지 않으려고 파일에 남긴다
#: (이 앱의 작업 상태는 DB 가 아니라 work/ 아래 파일에 둔다는 기존 방식과 같다).
_STORE = Path(__file__).resolve().parent.parent.parent / "work" / "dqa_fota" / "_jobs.json"
_KEEP = 50
# 이 파일을 쓰는 것은 이 프로세스의 워커 스레드뿐이라 스레드 락이면 충분하다.
# atomic_io.file_lock(잠금 파일)은 쓰지 않는다 — 비정상 종료가 남긴 잠금 파일 하나에
# 워커가 10초씩 멈춰 서고(실측), 정작 막아야 할 동시 프로세스는 이 앱에 없다.
# 쓰기 자체는 atomic_write_text 라 파일이 깨지지는 않는다.
_store_lock = threading.Lock()

RUNNING, DONE, ERROR, CANCELLED = "running", "done", "error", "cancelled"


def key_for(mail_id) -> str:
    return str(mail_id or "")


# ── 끝난 작업 기록(파일) ─────────────────────────────────────────────────────
def _load_store() -> dict:
    try:
        return json.loads(_STORE.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _save_done(key: str, job: dict) -> None:
    """완료된 작업의 '되살릴 값'만 남긴다 — 진행률·로그는 남길 이유가 없다."""
    rec = {"key": key, "state": DONE, "uploaded": bool(job.get("uploaded")),
           "dp": job.get("dp") or {}, "downloaded": job.get("downloaded") or {},
           "workdir": job.get("workdir") or "", "label": job.get("label") or "",
           "finished_at": job.get("finished_at") or time.time(),
           "message": job.get("message") or "완료"}
    try:
        _STORE.parent.mkdir(parents=True, exist_ok=True)
        with _store_lock:
            data = _load_store()
            data[key] = rec
            if len(data) > _KEEP:
                old = sorted(data.items(), key=lambda kv: kv[1].get("finished_at") or 0)
                for k, _v in old[:len(data) - _KEEP]:
                    data.pop(k, None)
            atomic_write_text(_STORE, json.dumps(data, ensure_ascii=False, indent=1))
    except Exception:  # noqa: BLE001  (기록에 실패해도 작업 자체는 끝난 것이다)
        pass


def _drop_done(key: str) -> None:
    try:
        with _store_lock:
            data = _load_store()
            if data.pop(key, None) is not None:
                atomic_write_text(_STORE, json.dumps(data, ensure_ascii=False, indent=1))
    except Exception:  # noqa: BLE001
        pass


def snapshot(key: str) -> dict | None:
    """UI 가 읽을 사본. 원본을 그대로 주면 그리는 도중에 값이 바뀐다.

    메모리에 없으면 파일에 남은 완료 기록을 본다 — 서버를 재시작한 뒤에도 이미
    받아서 올려 둔 빌드를 다시 받지 않게 하려는 것이다.
    """
    with _lock:
        j = _jobs.get(key)
        out = dict(j) if j is not None else None
    if out is None:
        rec = _load_store().get(key)
        if not rec:
            return None
        out = dict(rec)
        out.setdefault("logs", ["(이전 실행 기록 — 서버 재시작 전에 끝난 작업입니다)"])
        out.setdefault("cur_done", 0)
        out.setdefault("cur_total", 0)
        out.setdefault("cur_idx", 0)
        out.setdefault("cur_cnt", 0)
        out.setdefault("cur_speed", 0.0)
        out.setdefault("phase", "")
        out.setdefault("error", "")
        out["started_at"] = out.get("finished_at") or time.time()
        out["restored"] = True
    out["logs"] = list(out.get("logs") or [])
    out["dp"] = {k: dict(v) for k, v in (out.get("dp") or {}).items()}
    out["downloaded"] = {k: dict(v) for k, v in (out.get("downloaded") or {}).items()}
    return out


def is_running(key: str) -> bool:
    with _lock:
        j = _jobs.get(key)
        return bool(j and j["state"] == RUNNING)


def cancel(key: str) -> bool:
    """중단 요청. 다음 진행률 지점에서 멈춘다(다운로드 1MB · 업로드 100MB 단위)."""
    with _lock:
        j = _jobs.get(key)
        if not j or j["state"] != RUNNING:
            return False
        j["cancel"] = True
        j["message"] = "중단하는 중… (지금 보내는 덩어리가 끝나면 멈춥니다)"
        return True


def clear(key: str) -> None:
    """끝난 작업 기록을 지운다(파일 기록도 함께). 도는 중이면 아무것도 하지 않는다."""
    with _lock:
        j = _jobs.get(key)
        if j and j["state"] == RUNNING:
            return
        _jobs.pop(key, None)
    _drop_done(key)


def start(key: str, user_id: int, plan, workdir: str, label: str = "") -> tuple[bool, str]:
    """작업 시작. (시작함, 사유). 이미 도는 중이면 (False, 사유)."""
    with _lock:
        j = _jobs.get(key)
        if j and j["state"] == RUNNING:
            return False, "이미 이 요청의 다운로드가 돌고 있습니다."
        now = time.time()
        _jobs[key] = {
            "key": key, "state": RUNNING, "cancel": False,
            "phase": "download", "message": "시작하는 중…", "label": label,
            "workdir": workdir,
            "cur_name": "", "cur_done": 0, "cur_total": 0,
            "cur_idx": 0, "cur_cnt": 0, "cur_started": now, "cur_speed": 0.0,
            "started_at": now, "updated_at": now, "finished_at": 0.0,
            "logs": [], "error": "",
            "downloaded": {}, "uploaded": False, "dp": {},
        }
    from app import shadow

    t = threading.Thread(target=shadow.carry(_run), args=(key, user_id, plan, workdir),
                         name=f"dqa-fota-{key}", daemon=True)
    t.start()
    return True, ""


# ── 워커 ────────────────────────────────────────────────────────────────────
def _set(key: str, **kw) -> None:
    with _lock:
        j = _jobs.get(key)
        if j is not None:
            j.update(kw)
            j["updated_at"] = time.time()


def _add_logs(key: str, lines) -> None:
    with _lock:
        j = _jobs.get(key)
        if j is not None:
            j["logs"].extend(lines)
            j["updated_at"] = time.time()


def _progress_cb(key: str, phase: str):
    """파이프라인에 넘길 콜백. 중단 요청이 있으면 여기서 DqaCancelled 를 던진다."""
    last = [0.0]

    def cb(name: str, done: int, total: int, idx: int, cnt: int) -> None:
        now = time.time()
        with _lock:
            j = _jobs.get(key)
            if j is None:
                raise DqaCancelled("작업 기록이 사라졌습니다")
            if j.get("cancel"):
                raise DqaCancelled("사용자 중단")
            new_file = (j["cur_name"] != name)
            # 파일이 바뀌거나 다 됐으면 무조건, 그 밖에는 _TICK 간격으로만 쓴다.
            if not new_file and done < total and (now - last[0]) < _TICK:
                return
            last[0] = now
            if new_file:
                j["cur_started"] = now
            elapsed = max(now - j["cur_started"], 0.001)
            j.update({
                "phase": phase, "cur_name": name, "cur_done": done,
                "cur_total": total, "cur_idx": idx, "cur_cnt": cnt,
                "cur_speed": done / elapsed, "updated_at": now,
                "message": ("내려받는 중" if phase == "download" else "Dropbox 로 올리는 중")
                           + f" — {name}",
            })

    return cb


def _run(key: str, user_id: int, plan, workdir: str) -> None:
    from app.pipeline import dqa_fota as D

    try:
        _set(key, phase="download", message="파일서버에서 빌드 찾는 중…")
        downloaded, logs = D.download_to_files(
            user_id, plan, workdir, on_progress=_progress_cb(key, "download"))
        _add_logs(key, logs)
        if not downloaded:
            _set(key, state=ERROR, phase="", finished_at=time.time(),
                 error="다운로드 실패 — 로그를 확인하세요.", message="다운로드 실패")
            return

        _set(key, downloaded=downloaded, phase="upload",
             message="Dropbox 업로드 시작…", cur_name="", cur_done=0, cur_total=0)
        ok, ulogs = D.upload_to_dropbox(
            user_id, plan, downloaded, on_progress=_progress_cb(key, "upload"))
        _add_logs(key, ["", "── Dropbox 업로드 ──"] + ulogs)

        # 팝업이 닫혀 있어도 결과를 되살릴 수 있게, plan 에 채워진 값을 따로 뽑아 둔다.
        dp = {r.dtype: {"filename": r.filename, "size": r.size,
                        "download_url": r.download_url, "md5": r.md5}
              for r in plan.dp_rows if not r.is_dummy}
        done_at = time.time()
        if ok:
            # 다 올라간 것만 남긴다 — 반쪽짜리 URL 을 되살리면 ③ 이 그걸 믿고 등록한다.
            # 상태를 done 으로 바꾸기 **전에** 쓴다. 화면은 done 을 보자마자 결과를
            # 읽으러 오는데, 그때 파일이 아직 없으면 되살릴 게 없는 것처럼 보인다.
            _save_done(key, {"uploaded": True, "dp": dp, "downloaded": downloaded,
                             "workdir": workdir, "finished_at": done_at,
                             "label": (snapshot(key) or {}).get("label", "")})
        _set(key, state=DONE, uploaded=bool(ok), dp=dp, phase="",
             finished_at=done_at,
             message="완료" if ok else "업로드 일부 실패 — 로그 확인")
    except DqaCancelled as e:
        _add_logs(key, [f"■ 중단됨: {e}"])
        _set(key, state=CANCELLED, phase="", finished_at=time.time(),
             message="사용자가 중단했습니다.")
    except BaseException as e:  # noqa: BLE001  (스레드라 여기서 막지 않으면 조용히 죽는다)
        _add_logs(key, [f"✘ {type(e).__name__}: {e}"])
        _set(key, state=ERROR, phase="", finished_at=time.time(),
             error=f"{type(e).__name__}: {e}", message="오류로 멈췄습니다.")


# ── 결과를 새 plan 에 되살리기 ───────────────────────────────────────────────
def apply_to_plan(job: dict, plan) -> bool:
    """끝난 작업의 결과를 (다시 만든) plan 의 DownloadPath 행에 채운다.

    팝업을 다시 열면 plan 을 새로 계산하므로, 업로드가 채워 놓은 파일명·크기·URL 이
    사라진다. 그러면 ③ FOTA 등록이 '먼저 ①을 하라'고 막는다 — 이미 다 해 놓고도.
    """
    dp = (job or {}).get("dp") or {}
    if not dp:
        return False
    hit = False
    for r in plan.dp_rows:
        if r.is_dummy:
            continue
        v = dp.get(r.dtype)
        if not v:
            continue
        r.filename = v.get("filename") or r.filename
        r.size = v.get("size") or r.size
        r.download_url = v.get("download_url") or r.download_url
        r.md5 = v.get("md5") or r.md5
        hit = True
    return hit


# ── 표시용 ──────────────────────────────────────────────────────────────────
def fmt_size(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.0f}{unit}" if unit == "B" else f"{n:,.1f}{unit}"
        n /= 1024
    return "-"


def fmt_secs(s) -> str:
    try:
        s = int(max(float(s), 0))
    except (TypeError, ValueError):
        return "-"
    if s < 60:
        return f"{s}초"
    if s < 3600:
        return f"{s // 60}분 {s % 60}초"
    return f"{s // 3600}시간 {(s % 3600) // 60}분"


def describe(job: dict) -> dict:
    """snapshot → 화면에 그대로 쓸 값들. {ratio, headline, detail, eta, elapsed}."""
    done, total = job.get("cur_done") or 0, job.get("cur_total") or 0
    idx, cnt = job.get("cur_idx") or 0, job.get("cur_cnt") or 0
    frac = (done / total) if total else 0.0
    # 전체 진행률은 '파일 개수' 기준이다 — 파일마다 크기를 미리 다 알 수는 없어서,
    # 바이트로 합치면 다음 파일이 나타날 때마다 막대가 뒤로 간다.
    ratio = ((idx - 1 + frac) / cnt) if cnt else frac
    speed = job.get("cur_speed") or 0.0
    eta = ((total - done) / speed) if (speed > 0 and total > done) else 0
    phase_ko = {"download": "① 빌드 다운로드", "upload": "② Dropbox 업로드"}.get(
        job.get("phase") or "", "")
    return {
        "ratio": max(0.0, min(1.0, ratio)),
        "frac": max(0.0, min(1.0, frac)),
        "headline": (phase_ko + (f" · 파일 {idx}/{cnt}" if cnt else "")).strip(),
        "detail": (f"{job.get('cur_name') or ''} — {fmt_size(done)} / {fmt_size(total)}"
                   f" ({frac * 100:.1f}%)" if total else (job.get("message") or "")),
        "speed": (f"{fmt_size(speed)}/s" if speed > 0 else "-"),
        "eta": (fmt_secs(eta) if eta else "-"),
        "elapsed": fmt_secs(time.time() - (job.get("started_at") or time.time())),
    }
