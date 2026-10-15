"""
검수 웹(tw-automation-confirm) 동기화 — 이 PC 에서 바깥으로만 나간다(웹이 이 PC 로 들어오지 않는다).

한 바퀴(sync_once):
  ① 올림  결재자 계정(bcrypt 해시) · '요청됨' 인 결재 · 3080 에서 먼저 닫힌 결재
  ② 문서  웹에 없는 문서만 하나씩 — RN 은 docx 를 PDF 로 바꿔서, 뉴스레터는 EN/KR HTML 그대로
  ③ 내림  웹에서 내려진 결정 → 3080 이 아직 같은 요청을 '요청됨' 으로 들고 있으면 osu_reviews.decide 로
          그대로 처리(PDF 확정 · 파일서버 저장 · 결과 메일) → 결과를 웹에 알린다

요청 하나는 source_key = "job-{id}:{kind}:{requested_at}" 로 구분한다. 반려 뒤 다시 요청하면 새 건이 된다.
TWN_CONFIRM_URL · TWN_CONFIRM_TOKEN 이 둘 다 있을 때만 돈다.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
from datetime import datetime
from pathlib import Path

import requests

from app import os_update_jobs as J
from app.database import get_session
from app.models import User
from app.pipeline.state import get_states
from app.work_paths import WORK

import osu_reviews as R

STATE_FILE = WORK / "confirm_sync.json"
PDF_CACHE = WORK / "confirm_sync"
REVIEWER_ROLES = ("admin", "mid_reviewer", "final_reviewer")
MAX_FILE = 3_300_000          # 웹 /api/sync/file 의 한도와 같다
MIME_PDF = "application/pdf"
MIME_HTML = "text/html; charset=utf-8"

_lock = threading.Lock()
last: dict = {"at": "", "pushed": 0, "uploaded": 0, "applied": 0, "closed": 0, "errors": []}


def base_url() -> str:
    return (os.getenv("TWN_CONFIRM_URL") or "").strip().rstrip("/")


def public_url() -> str:
    """결재 요청 메일에 넣는 주소 — 결재자가 여는 쪽. 없으면 메일에 넣지 않는다."""
    return (os.getenv("TWN_CONFIRM_PUBLIC_URL") or "").strip().rstrip("/")


def enabled() -> bool:
    return bool(base_url() and (os.getenv("TWN_CONFIRM_TOKEN") or "").strip())


def _call(method: str, path: str, body: dict | None = None) -> dict:
    r = requests.request(method, base_url() + path, json=body, timeout=60,
                         headers={"Authorization": "Bearer " + os.getenv("TWN_CONFIRM_TOKEN", "").strip()})
    if r.status_code >= 400:
        try:
            msg = r.json().get("error") or r.text
        except ValueError:
            msg = r.text
        raise RuntimeError(f"{method} {path} → {r.status_code} {str(msg)[:200]}")
    return r.json()


def _load() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"reviews": {}}


def _save(d: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def source_key(job_id: int, kind: str, requested_at: str) -> str:
    return f"job-{job_id}:{kind}:{requested_at}"


def _aware(v: str | None) -> str | None:
    """3080 상태의 시각은 시간대 없는 이 PC 시각 — 웹(Vercel 은 UTC)이 어긋나지 않게 시간대를 붙인다."""
    if not v:
        return None
    try:
        return datetime.fromisoformat(v).astimezone().isoformat()
    except ValueError:
        return None


def _parse_key(key: str) -> tuple[int, str, str]:
    job, kind, at = key.split(":", 2)
    return int(job.removeprefix("job-")), kind, at


# ── ① 올릴 것 ────────────────────────────────────────────────────────────────

def _users() -> list[dict]:
    """결재자 계정. 첫 로그인 비밀번호(공통값)를 아직 안 바꾼 계정은 올리지 않는다."""
    from app import staff, user_mailer

    names = {(s.get("email") or "").lower(): s.get("name") or "" for s in staff.list_staff()}
    with get_session() as db:
        rows = db.query(User).filter(User.role.in_(REVIEWER_ROLES)).order_by(User.id).all()
        users = [(u.id, u.username, u.role, u.password_hash, bool(u.must_change_password)) for u in rows]
    out = []
    for uid, name, role, pw, must in users:
        if must or not (pw or "").startswith("$2"):
            continue
        email = user_mailer.user_email(uid)
        out.append({"username": name, "role": role, "password_hash": pw, "email": email,
                    "display_name": names.get(email.lower(), "")})
    return out


def _open_requests() -> dict[str, dict]:
    """3080 에서 지금 '요청됨' 인 결재 — {source_key: 올릴 내용(+ 문서 목록)}."""
    jobs = J.list_jobs()
    states = get_states([J.state_key(j["id"]) for j in jobs])
    out: dict[str, dict] = {}
    for j in jobs:
        st = states.get(J.state_key(j["id"])) or {}
        for kind, spec in R.KINDS.items():
            rv = st.get(spec["state_key"]) or {}
            if rv.get("status") != "requested" or not rv.get("requested_at"):
                continue
            key = source_key(j["id"], kind, rv["requested_at"])
            out[key] = {
                "review": {"source_key": key, "job_id": j["id"], "kind": kind,
                           "product": st.get("product") or j.get("product") or "",
                           "version": st.get("version_full") or st.get("os_version") or j.get("version") or "",
                           "requested_by": rv.get("requester_name", ""), "requested_at": _aware(rv["requested_at"]),
                           "summary": _summary(st, kind)},
                "docs": _docs(st, kind),
            }
    return out


def _summary(st: dict, kind: str) -> dict:
    lines = [{"label": k, "value": str(v)} for k, v in (
        ("제품", st.get("product")), ("OS 버전", st.get("version_full") or st.get("os_version")),
        ("Android", st.get("android")), ("보안 패치", st.get("security_patch"))) if v]
    rev = [str(x) for x in (st.get("rn_revision_lines") or []) if str(x).strip()]
    return {"lines": lines, "revision": rev[:200]}


def _docs(st: dict, kind: str) -> list[dict]:
    """[{name, label, mime, path}] — 경로가 있고 파일이 있는 것만."""
    out = []
    if kind == "newsletter":
        paths = st.get("newsletter_paths") or {}
        for lang, label in (("en", "Newsletter (EN)"), ("kr", "Newsletter (KR)")):
            p = paths.get(lang) if isinstance(paths, dict) else None
            if p and os.path.exists(p):
                out.append({"name": Path(p).name, "label": label, "mime": MIME_HTML, "path": p})
        return out
    src = (st.get("rn_full_docx_path") or st.get("rn_docx_path")) if kind == "mid" else st.get("rn_docx_path")
    if src and os.path.exists(src):
        label = "Release Note (전체 이력)" if kind == "mid" else "Release Note for Sales"
        out.append({"name": Path(src).stem + ".pdf", "label": label, "mime": MIME_PDF, "path": src})
    return out


def _fingerprint(path: str) -> str:
    s = os.stat(path)
    return f"{s.st_size}:{int(s.st_mtime)}"


def _bytes_of(doc: dict) -> bytes:
    if doc["mime"] != MIME_PDF:
        return Path(doc["path"]).read_bytes()
    # docx → PDF 는 Word 로 돌려 느리다 — 같은 파일이면 한 번만
    PDF_CACHE.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(f"{doc['path']}|{_fingerprint(doc['path'])}".encode()).hexdigest()[:20]
    cached = PDF_CACHE / f"{h}.pdf"
    if cached.exists():
        return cached.read_bytes()
    from app.office.rn_render import docx_to_pdf_bytes

    pdf, err = docx_to_pdf_bytes(Path(doc["path"]).read_bytes())
    if not pdf:
        raise RuntimeError(f"PDF 변환 실패 — {err}")
    cached.write_bytes(pdf)
    return pdf


# ── ③ 웹 결정 처리 ───────────────────────────────────────────────────────────

def _user_dict(username: str) -> dict | None:
    with get_session() as db:
        u = db.query(User).filter_by(username=username).first()
        return {"id": u.id, "username": u.username, "role": u.role} if u else None


def _apply(d: dict) -> dict:
    """웹 결정 하나를 3080 에서 처리 → ack 본문."""
    if d["source_key"].startswith("test:"):
        # 웹 화면 점검용 시험 건 — 3080 작업이 없다. 결정만 받았다고 알리고 아무것도 바꾸지 않는다.
        return {"id": d["id"], "ok": True, "message": "시험 건 — 3080 에는 반영하지 않음"}
    job_id, kind, at = _parse_key(d["source_key"])
    spec = R.KINDS[kind]
    st = (get_states([J.state_key(job_id)]).get(J.state_key(job_id)) or {})
    rv = st.get(spec["state_key"]) or {}
    if rv.get("status") != "requested" or rv.get("requested_at") != at:
        who = rv.get("decided_by") or ""
        now = {"approved": "승인", "rejected": "반려"}.get(rv.get("status") or "", "요청 취소")
        return {"id": d["id"], "ok": False, "superseded": True,
                "message": f"3080 에서 먼저 {now}{' — ' + who if who else ''} · 웹 결정은 반영하지 않음"}
    user = _user_dict(d["decided_by"])
    if not user:
        return {"id": d["id"], "ok": False, "message": f"3080 에 {d['decided_by']} 계정이 없습니다"}
    try:
        res = R.decide(job_id, kind, d["status"] == "approved", d.get("note") or "", user)
    except R.ReviewError as e:
        return {"id": d["id"], "ok": False, "message": str(e)}
    msg = res.get("message", "")
    if res.get("notes"):
        msg += " · " + " · ".join(str(n) for n in res["notes"])
    return {"id": d["id"], "ok": True, "message": msg}


# ── 한 바퀴 ──────────────────────────────────────────────────────────────────

def sync_once() -> dict:
    if not enabled():
        return {"skipped": True}
    if not _lock.acquire(blocking=False):
        return {"busy": True}
    try:
        return _sync()
    finally:
        _lock.release()


def _sync() -> dict:
    mem = _load()
    known: dict = mem.setdefault("reviews", {})
    errors: list[str] = []
    stat = {"pushed": 0, "uploaded": 0, "applied": 0, "closed": 0}

    # ③ 먼저 — 결정이 이미 내려졌으면 문서를 다시 올릴 까닭이 없다
    for d in _call("GET", "/api/sync/decisions").get("decisions", []):
        try:
            ack = _apply(d)
        except Exception as e:  # noqa: BLE001 — 한 건 실패로 나머지를 막지 않는다
            ack = {"id": d["id"], "ok": False, "message": f"처리 중 오류 — {e}"[:500]}
        _call("POST", "/api/sync/ack", ack)
        stat["applied"] += 1
        if not ack["ok"] and not ack.get("superseded"):
            errors.append(f"{d['source_key']}: {ack['message']}")

    reqs = _open_requests()
    states = None
    closed = []
    for key in list(known):
        if key in reqs:
            continue
        job_id, kind, at = _parse_key(key)
        if states is None:
            states = get_states([J.state_key(j["id"]) for j in J.list_jobs()])
        rv = (states.get(J.state_key(job_id)) or {}).get(R.KINDS[kind]["state_key"]) or {}
        same = rv.get("requested_at") == at
        closed.append({"source_key": key, "status": rv.get("status") if same else "cancelled",
                       "decided_by": rv.get("decided_by", "") if same else "",
                       "decided_at": _aware(rv.get("decided_at")) if same else None,
                       "note": rv.get("note", "") if same else ""})

    res = _call("POST", "/api/sync/push", {"users": _users(), "reviews": [v["review"] for v in reqs.values()],
                                           "closed": closed})
    stat["closed"] = res.get("closed", 0)
    for c in closed:
        known.pop(c["source_key"], None)

    # ② 문서 — 웹에 없거나 3080 에서 바뀐 것만
    for key, info in reqs.items():
        web = (res.get("reviews") or {}).get(key)
        if not web:
            continue
        mine = known.setdefault(key, {"id": web["id"], "files": {}})
        mine["id"] = web["id"]
        stat["pushed"] += 1
        if web["status"] != "pending":
            continue
        for doc in info["docs"]:
            fp = _fingerprint(doc["path"])
            if doc["name"] in web["files"] and mine["files"].get(doc["name"]) == fp:
                continue
            try:
                data = _bytes_of(doc)
                if len(data) > MAX_FILE:
                    raise RuntimeError(f"{len(data) // 1024} KB — 웹 한도({MAX_FILE // 1024} KB)를 넘음")
                _call("POST", "/api/sync/file", {"review_id": web["id"], "name": doc["name"], "label": doc["label"],
                                                 "mime": doc["mime"], "b64": base64.b64encode(data).decode()})
                mine["files"][doc["name"]] = fp
                stat["uploaded"] += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"{key} {doc['name']}: {e}"[:300])

    _save(mem)
    last.update(stat, at=datetime.now().isoformat(timespec="seconds"), errors=errors[-20:])
    if errors:
        raise RuntimeError(" / ".join(errors)[:300])
    return dict(last)
