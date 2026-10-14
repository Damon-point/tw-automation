"""
tw-next API — 3080 의 업무 서버. 8080(tw-automation)과 따로 돈다.

- 업무 코드·DB·work 폴더는 backend/ 의 사본을 쓴다(8080 것은 건드리지 않는다).
- 단계 실행·검수·업로드·결재도 여기서 한다. 진행률이 이 프로세스 메모리에 있으므로
  단계와 주기 작업(runtime.py)은 이 프로세스 안에서만 돈다.
- 외부 시스템 쓰기는 검증 모드(app.shadow)가 막고 기록만 한다. 운영 전환은 관리자가 한다.
- 로그인은 users 표의 비밀번호로 확인하고, 서명된 쿠키 세션을 쓴다.

    python -m uvicorn main:app --host 127.0.0.1 --port 8180   (api 폴더에서, backend/.venv 로)
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parent / ".env")
TWA = Path(os.getenv("TWN_BACKEND_DIR", str(HERE.parent / "backend"))).resolve()   # 3080 전용 업무 코드·데이터
load_dotenv(TWA / ".env")          # 3080 백엔드 설정(FERNET_KEY 등)
sys.path.insert(0, str(TWA))

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from app import os_products as P  # noqa: E402
from app import os_update_jobs as J  # noqa: E402
from app import stages as S  # noqa: E402
from app.database import get_session  # noqa: E402
from app.models import CachedMail, OsUpdateJob, OsUpdateJobStage, User  # noqa: E402
from app.pipeline.state import get_state  # noqa: E402
from app.security import verify_password  # noqa: E402
from app import app_settings, shadow  # noqa: E402

import osu_actions as A  # noqa: E402
import osu_reviews as R  # noqa: E402
import runtime  # noqa: E402

LEGACY_URL = os.getenv("TWN_LEGACY_URL", "http://192.168.121.35:8080").rstrip("/")
SECRET = os.getenv("TWN_SECRET", "")
if not SECRET:
    raise SystemExit("TWN_SECRET 가 .env 에 없습니다 (tw-next/.env)")

from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def _lifespan(_app):
    runtime.start()
    yield


app = FastAPI(title="tw-next api", docs_url=None, redoc_url=None, lifespan=_lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET, session_cookie="twn_session",
                   max_age=60 * 60 * 12, same_site="lax", https_only=False)

STAGE_LABEL = dict(getattr(S, "STAGE_LABELS", {}))
WORK = (TWA / "work").resolve()


# ── 인증 ───────────────────────────────────────────────────────────────────────

class LoginIn(BaseModel):
    username: str
    password: str


def _user(request: Request) -> dict:
    u = request.session.get("user")
    if not u:
        raise HTTPException(401, "로그인이 필요합니다")
    return u


@app.post("/api/login")
def login(body: LoginIn, request: Request):
    with get_session() as db:
        u = db.query(User).filter_by(username=body.username.strip()).first()
        if u is None or not verify_password(body.password, u.password_hash):
            raise HTTPException(401, "아이디 또는 비밀번호가 맞지 않습니다")
        request.session["user"] = {"id": u.id, "username": u.username, "role": u.role}
        return {"ok": True, "username": u.username, "role": u.role}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.post("/api/dev-login")
def dev_login(request: Request):
    """개발용 — 환경변수 TWN_DEV_LOGIN_AS 가 있고 이 PC(127.0.0.1)에서 온 요청일 때만. 기본은 꺼짐."""
    who = os.getenv("TWN_DEV_LOGIN_AS", "").strip()
    host = request.client.host if request.client else ""
    # 화면 서버(Next.js)를 거친 요청은 API 에서 127.0.0.1 로 보인다 — 전달 머리가 있으면 거부한다.
    forwarded = any(h in request.headers for h in ("x-forwarded-for", "x-forwarded-host", "x-real-ip"))
    if not who or host not in ("127.0.0.1", "::1") or forwarded:
        raise HTTPException(404, "없음")
    with get_session() as db:
        u = db.query(User).filter_by(username=who).first()
        if u is None:
            raise HTTPException(404, "없음")
        request.session["user"] = {"id": u.id, "username": u.username, "role": u.role}
        return {"ok": True, "username": u.username}


@app.get("/api/me")
def me(request: Request):
    u = _user(request)
    return {**u, "legacy_url": LEGACY_URL, "live": shadow.live()}


# ── 공통 ───────────────────────────────────────────────────────────────────────

def _iso(v) -> str:
    if isinstance(v, datetime):
        return v.isoformat(timespec="seconds")
    return str(v or "")


def _label(key: str, side: str = "") -> str:
    try:
        return S.stage_label(key, side)
    except Exception:  # noqa: BLE001
        return STAGE_LABEL.get(key, key)


_MARK_RE = re.compile(r"^\s*([✔✘⚠·※→•])\s*(.*)$")


def summarize_result(text: str) -> dict:
    """단계 결과 글 → 요약. ✘ 줄은 오류, ⚠ 줄은 경고, ✔ 줄은 완료로 센다."""
    ok, warn, err = [], [], []
    for line in (text or "").splitlines():
        m = _MARK_RE.match(line)
        if not m:
            continue
        mark, rest = m.group(1), m.group(2).strip()
        if mark == "✘":
            err.append(rest)
        elif mark == "⚠":
            if rest and not rest.endswith(":"):   # '확인 필요:' 같은 머리줄은 빼고 내용 줄만
                warn.append(rest)
        elif mark == "✔":
            ok.append(rest)
    first = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
    tone = "error" if err else "warn" if warn else "ok" if ok else "none"
    return {"tone": tone, "headline": first[:200], "errors": err[:8], "warnings": warn[:8], "ok_count": len(ok)}


def _stage_rows(job_id: int) -> list[OsUpdateJobStage]:
    with get_session() as db:
        rows = db.query(OsUpdateJobStage).filter_by(job_id=job_id).all()
        db.expunge_all()
        return rows


def _order(side: str) -> list[str]:
    try:
        return list(S.stage_keys_for(side))
    except Exception:  # noqa: BLE001
        return []


def _blocked_reason(rows_by_key: dict, side: str, key: str) -> str:
    """이 단계를 지금 못 돌리는 이유. 8080 의 규칙 그대로: 그 갈래의 첫 미완료 단계만 돈다."""
    order = _order(side)
    if key not in order:
        return ""
    me_ = rows_by_key.get(key)
    if me_ is not None and me_.status == "done":
        return ""
    for k in order[: order.index(key)]:
        r = rows_by_key.get(k)
        if r is None or r.status != "done":
            return f"앞 단계 '{_label(k, side)}' 가 아직 완료되지 않음"
    return ""


# ── 홈: 오늘 할 일 ───────────────────────────────────────────────────────────────

@app.get("/api/home")
def home(request: Request):
    u = _user(request)
    with get_session() as db:
        jobs = {j.id: j for j in db.query(OsUpdateJob).all()}
        stages = db.query(OsUpdateJobStage).all()
        db.expunge_all()
    prods = {p["id"]: p for p in P.list_products("")}

    def jinfo(job_id: int) -> dict:
        j = jobs.get(job_id)
        p = prods.get(j.product_id, {}) if j else {}
        return {"job_id": job_id, "product": p.get("product", "?"), "android": p.get("android", ""),
                "os_type": p.get("os_type", ""), "version": j.version if j else ""}

    review, running, failed = [], [], []
    for s in stages:
        item = {**jinfo(s.job_id), "side": s.side, "stage_key": s.stage_key, "label": _label(s.stage_key, s.side),
                "updated_by": s.updated_by, "updated_at": _iso(s.updated_at)}
        if s.status == "running":
            running.append(item)
        elif s.status == "review":
            summ = summarize_result(s.result_text)
            item["summary"] = summ
            (failed if summ["tone"] == "error" else review).append(item)

    # RN 결재 — 상태 파일의 요청됨 표시
    approvals = []
    for jid in jobs:
        st = get_state(J.state_key(jid)) or {}
        for k, kind in (("rn_mid_review", "RN 승인(1차)"), ("rn_review", "최종 검수(2차)"), ("newsletter_review", "뉴스레터 결재")):
            v = st.get(k) or {}
            if isinstance(v, dict) and v.get("status") == "requested":
                approvals.append({**jinfo(jid), "kind": kind, "requested_by": v.get("requester_name", ""),
                                  "requested_at": v.get("requested_at", "")})

    rounds = J.open_rounds("")
    new_rounds = [r for r in rounds if r.get("state") == "new"]
    try:
        import license_inbox

        lic_new = license_inbox.open_count()
    except Exception:  # noqa: BLE001
        lic_new = 0
    key = lambda x: x.get("updated_at", "")  # noqa: E731
    return {
        "user": u,
        "counts": {"review": len(review), "failed": len(failed), "running": len(running),
                   "approvals": len(approvals), "new_rounds": len(new_rounds),
                   "license_mail": lic_new},
        "review": sorted(review, key=key, reverse=True)[:30],
        "failed": sorted(failed, key=key, reverse=True)[:30],
        "running": running,
        "approvals": approvals,
        "new_rounds": [_round_card(r) for r in new_rounds],
    }


# ── 회차 ───────────────────────────────────────────────────────────────────────

def _round_card(r: dict) -> dict:
    p = r.get("product") or {}
    up = r.get("update") or {}
    job = r.get("job") or {}
    return {
        "product": p.get("product", ""), "android": str(p.get("android", "")), "os_type": p.get("os_type", ""),
        "product_id": p.get("id"), "version": r.get("version", ""), "state": r.get("state", ""),
        "done": r.get("done", 0), "total": r.get("total", 0), "job_id": job.get("id"),
        "why": up.get("basis", "") if up else "",
        "approved": bool(up.get("approved")) if up else False,
        "mails": [{"mail_id": m.get("mail_id"), "subject": m.get("subject", ""), "date": m.get("date", "")}
                  for m in (up.get("mails") or [])][:6] if up else [],
    }


@app.get("/api/rounds")
def rounds(request: Request, q: str = ""):
    _user(request)
    open_ = [_round_card(r) for r in J.open_rounds(q)]
    recent = []
    for j in J.list_jobs()[:60]:
        done, total = J.job_progress(j["id"])
        if total and done == total:
            recent.append({"product": j.get("product", ""), "android": str(j.get("android", "")),
                           "os_type": j.get("os_type", ""), "version": j.get("version", ""), "job_id": j["id"],
                           "done": done, "total": total, "state": "finished",
                           "created_at": _iso(j.get("created_at"))})
    if q:
        ql = q.lower()
        recent = [r for r in recent if ql in f"{r['product']} {r['version']} A{r['android']}".lower()]
    return {"open": open_, "finished": recent[:40]}


# ── 작업 ───────────────────────────────────────────────────────────────────────

_STATE_KEYS = [
    ("release_version", "공식 버전"), ("version_full", "버전 표기"), ("os_build_date", "빌드 날짜"),
    ("security_patch", "보안패치"), ("prev_version", "이전 버전"), ("rn_link", "RN for Sales 링크"),
    ("rn_confluence_link", "RN 링크(직접 지정)"), ("approval_date", "승인일"),
]


def _fact_value(key: str, v) -> str:
    """승인일처럼 메일 Date 머리 원문인 값은 날짜로 줄인다."""
    if key == "approval_date" and isinstance(v, str) and "," in v:
        from email.utils import parsedate_to_datetime

        try:
            return parsedate_to_datetime(v).strftime("%Y-%m-%d %H:%M")
        except Exception:  # noqa: BLE001
            return v
    return str(v)


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: int, request: Request):
    _user(request)
    j = J.get(job_id)
    if not j:
        raise HTTPException(404, "작업이 없습니다")
    st = get_state(J.state_key(job_id)) or {}
    rows = _stage_rows(job_id)
    sides = []
    for side, title in ((S.SIDE_SUBMISSION, "Submission — 준비(외부에 쓰지 않음)"),
                        (S.SIDE_APPROVAL, "Approval — 실제 업로드·발송")):
        by_key = {r.stage_key: r for r in rows if r.side == side}
        items = []
        for k in _order(side):
            r = by_key.get(k)
            status = r.status if r else "pending"
            items.append({
                "id": r.id if r else None, "key": k, "label": _label(k, side), "status": status,
                "input": (r.input_params or "") if r else "",
                "updated_by": r.updated_by if r else "", "updated_at": _iso(r.updated_at) if r else "",
                "result": (r.result_text or "") if r else "",
                "summary": summarize_result(r.result_text if r else ""),
                "artifact": {"type": r.artifact_type, "path": r.artifact_path} if r and r.artifact_path else None,
                "blocked": _blocked_reason(by_key, side, k) if status == "pending" else "",
            })
        done = sum(1 for i in items if i["status"] == "done")
        nxt = next((i for i in items if i["status"] != "done"), None)
        sides.append({"side": side, "title": title, "done": done, "total": len(items), "stages": items,
                      "next": nxt["key"] if nxt else None})

    facts = [{"label": lab, "value": _fact_value(k, st.get(k))} for k, lab in _STATE_KEYS if st.get(k)]
    uploads = [{"type": u.get("type"), "name": u.get("name"), "url": u.get("url")} for u in st.get("sdcard_uploads") or []]
    rn = {
        "mid": (st.get("rn_mid_review") or {}).get("status", ""),
        "final": (st.get("rn_review") or {}).get("status", ""),
        "full_pdf": bool(st.get("rn_full_pdf_path")), "sales_pdf": bool(st.get("rn_pdf_path")),
        "sales_archived": bool(st.get("rn_sales_archived")),
    }
    artifacts = []
    try:
        for a in J.artifacts_for({"job": j, "version": j.get("version"), "release": None, "product": {}}):
            artifacts.append({**a, "url": f"/api/files?path={a['path']}" if _inside(a["path"]) else ""})
    except Exception:  # noqa: BLE001
        pass
    basis = []
    try:
        basis = [{"mail_id": m.get("mail_id"), "subject": m.get("subject", ""), "date": m.get("date", "")}
                 for m in J.basis_for_job(j)]
    except Exception:  # noqa: BLE001
        pass
    return {
        "job": {"id": j["id"], "product": j.get("product", ""), "android": str(j.get("android", "")),
                "os_type": j.get("os_type", ""), "version": j.get("version", ""), "created_by": j.get("created_by", ""),
                "created_at": _iso(j.get("created_at"))},
        "sides": sides, "facts": facts, "uploads": uploads, "rn": rn, "artifacts": artifacts, "basis": basis,
        "reviews": R.state_of(job_id), "progress": A.progress_of(job_id), "run_all": A.run_all_state(job_id),
        "can_decide": {k: R.can_decide(k, request.session.get("user") or {}) for k in R.KINDS},
        "legacy": {"osupdate": f"{LEGACY_URL}/osupdate", "newtasks": f"{LEGACY_URL}/newtasks",
                   "reviews": f"{LEGACY_URL}/reviews", "final_reviews": f"{LEGACY_URL}/final-reviews"},
    }


# ── 작업 쓰기: 실행 · 검수 · 업로드 · 결재 ─────────────────────────────────────────

class StageIn(BaseModel):
    stage_id: int


class SideIn(BaseModel):
    side: str


class TextIn(BaseModel):
    stage_id: int
    text: str = ""


class StateValueIn(BaseModel):
    key: str
    value: str = ""


class SelectionIn(BaseModel):
    rows: list[dict]
    inputs: list[dict] | None = None     # 입력 값 칸 [{key,label,value}] — 최초 릴리즈면 key 없는 새 항목도


class NoteIn(BaseModel):
    no: str
    value: str = ""


class DecideIn(BaseModel):
    approve: bool
    note: str = ""


def _act(fn, *a):
    try:
        return fn(*a)
    except (A.ActionError, R.ReviewError) as e:
        raise HTTPException(409, str(e))


@app.post("/api/jobs/{job_id}/run-next")
def act_run_next(job_id: int, body: SideIn, request: Request):
    return _act(A.run_next, job_id, body.side, _user(request))


class BulkIn(BaseModel):
    side: str
    status: str           # done | pending


@app.post("/api/jobs/{job_id}/run-all")
def act_run_all(job_id: int, body: SideIn, request: Request):
    return _act(A.run_all, job_id, body.side, _user(request))


@app.post("/api/jobs/{job_id}/bulk-status")
def act_bulk_status(job_id: int, body: BulkIn, request: Request):
    return _act(A.bulk_status, job_id, body.side, body.status, _user(request))


@app.post("/api/jobs/{job_id}/run")
def act_run(job_id: int, body: StageIn, request: Request):
    return _act(A.run_stage, job_id, body.stage_id, _user(request))


@app.post("/api/jobs/{job_id}/cancel")
def act_cancel(job_id: int, body: StageIn, request: Request):
    _user(request)
    return _act(A.cancel, job_id, body.stage_id)


@app.get("/api/jobs/{job_id}/progress")
def act_progress(job_id: int, request: Request):
    _user(request)
    return A.progress_of(job_id)


@app.get("/api/jobs/{job_id}/revert-preview")
def act_revert_preview(job_id: int, stage_id: int, request: Request):
    _user(request)
    return _act(A.revert_preview, job_id, stage_id)


@app.post("/api/jobs/{job_id}/revert")
def act_revert(job_id: int, body: StageIn, request: Request):
    return _act(A.revert, job_id, body.stage_id, _user(request))


@app.post("/api/jobs/{job_id}/review-done")
def act_review_done(job_id: int, body: StageIn, request: Request):
    return _act(A.review_done, job_id, body.stage_id, _user(request))


@app.post("/api/jobs/{job_id}/skip")
def act_skip(job_id: int, body: StageIn, request: Request):
    return _act(A.skip, job_id, body.stage_id, _user(request))


@app.post("/api/jobs/{job_id}/rework")
def act_rework(job_id: int, body: TextIn, request: Request):
    return _act(A.rework, job_id, body.stage_id, body.text, _user(request))


@app.post("/api/jobs/{job_id}/input")
def act_input(job_id: int, body: TextIn, request: Request):
    _user(request)
    return _act(A.set_input, job_id, body.stage_id, body.text)


@app.post("/api/jobs/{job_id}/state")
def act_state(job_id: int, body: StateValueIn, request: Request):
    _user(request)
    return _act(A.set_state_value, job_id, body.key, body.value)


@app.get("/api/jobs/{job_id}/upload-plan")
def act_upload_plan(job_id: int, stage_id: int, request: Request):
    _user(request)
    return _act(A.upload_plan, job_id, stage_id)


@app.post("/api/jobs/{job_id}/upload")
def act_upload(job_id: int, body: StageIn, request: Request):
    return _act(A.upload, job_id, body.stage_id, _user(request))


@app.get("/api/jobs/{job_id}/selection")
def act_selection(job_id: int, request: Request):
    _user(request)
    return _act(A.selection, job_id)


@app.post("/api/jobs/{job_id}/selection")
def act_save_selection(job_id: int, body: SelectionIn, request: Request):
    return _act(A.save_selection, job_id, body.rows, _user(request), body.inputs)


@app.post("/api/jobs/{job_id}/exclude-note")
def act_exclude_note(job_id: int, body: NoteIn, request: Request):
    _user(request)
    return _act(A.save_exclude_note, job_id, body.no, body.value)


@app.post("/api/jobs/{job_id}/reviews/{kind}/request")
def rv_request(job_id: int, kind: str, request: Request):
    return _act(R.request, job_id, kind, _user(request))


@app.post("/api/jobs/{job_id}/reviews/{kind}/decide")
def rv_decide(job_id: int, kind: str, body: DecideIn, request: Request):
    return _act(R.decide, job_id, kind, body.approve, body.note, _user(request))


# ── 검증 모드 · 운영 전환 ──────────────────────────────────────────────────────────

class LiveIn(BaseModel):
    live: bool


@app.get("/api/shadow")
def shadow_log(request: Request, limit: int = 200):
    _user(request)
    return {"live": shadow.live(), "writes": shadow.recent(min(limit, 1000)), "runtime": runtime.status}


@app.post("/api/shadow/live")
def shadow_set_live(body: LiveIn, request: Request):
    u = _user(request)
    if u.get("role") != "admin":
        raise HTTPException(403, "관리자만 바꿀 수 있습니다")
    app_settings.set("live_writes", "1" if body.live else "0", by=u["username"])
    shadow.record("system", "운영 전환" if body.live else "검증 모드로", by=u["username"])
    return {"live": shadow.live()}


# ── 제품 · 메일 ─────────────────────────────────────────────────────────────────

_PROD_FIELDS = [("os_file_path", "OS 파일"), ("dev_rn_path", "개발 RN"), ("sales_rn_path", "RN for Sales"),
                ("sp_rn_path", "Service Portal"), ("kb_rn_path", "Knowledge Base"),
                ("dropbox_os_sdcard_path", "Dropbox FOTA"), ("dropbox_rn_path", "Dropbox RN")]


@app.get("/api/products")
def products(request: Request, q: str = ""):
    _user(request)
    out = []
    for p in P.list_products(q):
        filled = [lab for k, lab in _PROD_FIELDS if p.get(k)]
        missing = [lab for k, lab in _PROD_FIELDS if not p.get(k)]
        out.append({"id": p.get("id"), "product": p.get("product"), "android": str(p.get("android") or ""),
                    "os_type": p.get("os_type", ""), "category": p.get("category", ""),
                    "initial_os_version": p.get("initial_os_version", ""), "filled": filled, "missing": missing})
    return {"products": sorted(out, key=lambda x: (x["product"] or "", x["android"]))}


@app.get("/api/mail")
def mail(request: Request, q: str = "", limit: int = 80):
    _user(request)
    with get_session() as db:
        qs = db.query(CachedMail)
        if q:
            like = f"%{q}%"
            qs = qs.filter((CachedMail.subject.like(like)) | (CachedMail.sender.like(like)))
        rows = qs.order_by(CachedMail.id.desc()).limit(min(limit, 200)).all()
        out = [{"id": m.id, "subject": m.subject, "sender": m.sender, "received_at": m.received_at,
                "attachments": [a for a in (m.attachment_names or "").splitlines() if a][:6],
                "snippet": re.sub(r"\s+", " ", (m.body_text or ""))[:180]} for m in rows]
    return {"mail": out}


# ── 파일 ───────────────────────────────────────────────────────────────────────

def _inside(path: str) -> bool:
    try:
        Path(path).resolve().relative_to(WORK)
        return True
    except (ValueError, OSError):
        return False


@app.get("/api/files")
def files(path: str, request: Request):
    _user(request)
    if not _inside(path) or not Path(path).is_file():
        raise HTTPException(404, "허용되지 않은 경로")
    return FileResponse(path)


# ── 영역별 라우터(api/routers/*.py) ─────────────────────────────────────────────────

def _include_routers() -> None:
    import importlib
    import pkgutil

    import routers

    for m in sorted(pkgutil.iter_modules(routers.__path__), key=lambda m: m.name):
        mod = importlib.import_module(f"routers.{m.name}")
        r = getattr(mod, "router", None)
        if r is not None:
            app.include_router(r)


_include_routers()


@app.exception_handler(HTTPException)
async def _http_err(_req, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
