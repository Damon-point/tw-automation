"""WhiteList 비즈니스 로직 — Apps Script Code.gs 의 서버 함수들을 이식.

UI(page)와 분리해 테스트 가능하게 둔다. DB 는 whitelist.db(격리).
승인 시 FOTA 전송·메일 발송은 각각 fota.py·mailer.py 에 위임.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Iterable

from app.whitelist.db import whitelist_session
from app.whitelist.models import WLDevice, WLRequest, WLRule

# Android OS 버전 → API 레벨 (Apps Script OS_API)
OS_API = {6: 23, 7: 25, 8: 27, 9: 28, 10: 29, 11: 30, 12: 32, 13: 33, 14: 34, 15: 35, 16: 36}
_KST = timezone(timedelta(hours=9))
_SN_RE = re.compile(r"^[A-Za-z0-9]{10}$")


# ── 공통 헬퍼 ────────────────────────────────────────────────
def get_api(os_ver) -> int | None:
    try:
        return OS_API.get(int(os_ver))
    except (TypeError, ValueError):
        return None


def get_end_date() -> str:
    """KST 기준 오늘 + 1년 (YYYY-MM-DD). Support End Date.

    2/29 에 replace(year=+1) 하면 ValueError 라 그 해에만 2/28 로 내린다.
    """
    now = datetime.now(_KST)
    try:
        d = now.replace(year=now.year + 1)
    except ValueError:  # 윤년 2/29 → 평년
        d = now.replace(year=now.year + 1, day=28)
    return d.strftime("%Y-%m-%d")


def gen_req_id() -> str:
    import random
    import string
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=3))
    return "req-" + format(int(datetime.now().timestamp() * 1000), "x") + suffix


def is_valid_sn(sn: str) -> bool:
    return bool(_SN_RE.match((sn or "").strip()))


# ── 규칙(Rules) ─────────────────────────────────────────────
# 요청 구분(카테고리). DQA(DQA팀 발신자)만 자동으로 DQA, 그 외 일반 요청은 기본 '기타'.
# (기존 실제 규칙이 전부 '기타'라 일반 요청이 제대로 해석된다. GMS 는 발신자로 자동 잡지 않고
#  필요 시 처리 화면에서 수동 선택한다.)
CATEGORIES = ["GMS", "DQA", "KT", "LG", "기타"]
DEFAULT_CATEGORY = "기타"


def _norm_category(category: str) -> str:
    c = (category or "").strip() or DEFAULT_CATEGORY
    return c


def rules_for(model: str) -> list[dict]:
    """그 모델의 규칙 전부 — 구분(category)과 무관하게.

    FOTA 등록이 '이 릴리즈가 어느 whitelist OS_Type 에 들어가야 하는지'를 물을 때 쓴다.
    규칙을 화면에서 추가하면 코드 수정 없이 그대로 반영된다.
    """
    m = (model or "").strip()
    if not m:
        return []
    with whitelist_session() as db:
        rs = db.query(WLRule).filter_by(model=m).order_by(WLRule.from_os).all()
        return [{"id": r.id, "model": r.model, "from": r.from_os, "to": r.to_os,
                 "ostype": r.ostype, "category": r.category or DEFAULT_CATEGORY}
                for r in rs]


def list_rules(category: str = "") -> list[dict]:
    """규칙 목록. category 를 주면 그 구분만."""
    with whitelist_session() as db:
        q = db.query(WLRule)
        if category:
            q = q.filter(WLRule.category == category)
        rules = q.order_by(WLRule.model, WLRule.from_os, WLRule.category).all()
        return [
            {"id": r.id, "model": r.model, "from": r.from_os, "to": r.to_os,
             "ostype": r.ostype, "category": r.category or DEFAULT_CATEGORY}
            for r in rules
        ]


def add_rule(model: str, from_os: int, to_os: int, ostype: str,
             category: str = DEFAULT_CATEGORY) -> tuple[bool, str]:
    model = (model or "").strip()
    ostype = (ostype or "").strip()
    category = _norm_category(category)
    if not (model and ostype):
        return False, "Model 과 OS Type 을 입력하세요."
    try:
        from_os, to_os = int(from_os), int(to_os)
    except (TypeError, ValueError):
        return False, "OS 값이 올바르지 않습니다."
    with whitelist_session() as db:
        dup = db.query(WLRule).filter_by(model=model, from_os=from_os,
                                         to_os=to_os, category=category).first()
        if dup:
            return False, "이미 존재하는 규칙입니다(같은 구분)."
        db.add(WLRule(model=model, from_os=from_os, to_os=to_os,
                      ostype=ostype, category=category))
        db.commit()
    return True, ""


def bulk_import_rules(rows: list[dict]) -> dict:
    """규칙을 표(리스트)로 일괄 등록/갱신.

    rows 항목: {model, from, to, ostype, category}. 같은 (model,from,to,category)면 ostype 갱신.
    반환: {added, updated, skipped, errors:[...]}.
    """
    added = updated = skipped = 0
    errors: list[str] = []
    with whitelist_session() as db:
        for i, row in enumerate(rows, 1):
            model = str(row.get("model", "")).strip()
            ostype = str(row.get("ostype", "")).strip()
            category = _norm_category(row.get("category", ""))
            try:
                frm, to = int(row["from"]), int(row["to"])
            except (KeyError, TypeError, ValueError):
                errors.append(f"{i}행: from/to 숫자 오류 ({row})")
                continue
            if not (model and ostype):
                errors.append(f"{i}행: model/ostype 누락 ({row})")
                continue
            r = db.query(WLRule).filter_by(model=model, from_os=frm,
                                           to_os=to, category=category).first()
            if r is None:
                db.add(WLRule(model=model, from_os=frm, to_os=to,
                              ostype=ostype, category=category))
                added += 1
            elif r.ostype != ostype:
                r.ostype = ostype
                updated += 1
            else:
                skipped += 1
        db.commit()
    return {"added": added, "updated": updated, "skipped": skipped, "errors": errors}


def delete_rule(rule_id: int) -> bool:
    with whitelist_session() as db:
        r = db.get(WLRule, int(rule_id))
        if not r:
            return False
        db.delete(r)
        db.commit()
    return True


def get_ostype(model: str, from_os, to_os, category: str = DEFAULT_CATEGORY) -> str | None:
    try:
        from_os, to_os = int(from_os), int(to_os)
    except (TypeError, ValueError):
        return None
    with whitelist_session() as db:
        r = db.query(WLRule).filter_by(
            model=(model or "").strip(), from_os=from_os, to_os=to_os,
            category=_norm_category(category)).first()
        return r.ostype if r else None


def single_rule_for(model: str, category: str = DEFAULT_CATEGORY) -> dict | None:
    """모델(+구분)의 규칙이 정확히 1개면 그 규칙(자동 OS 채움용), 아니면 None."""
    with whitelist_session() as db:
        rs = db.query(WLRule).filter_by(model=(model or "").strip(),
                                        category=_norm_category(category)).all()
        if len(rs) == 1:
            return {"from": rs[0].from_os, "to": rs[0].to_os, "ostype": rs[0].ostype}
    return None


def resolve_os(model: str, cur_os="", upg_os="", category: str = DEFAULT_CATEGORY) -> dict | None:
    """모델 + (부분적인) OS 힌트를 **해당 구분(category)** 규칙과 대조해 from/to 를 확정한다.

    Claude 가 읽어낸 OS 는 틀리거나 한쪽만 있을 수 있다(예: 'PM451 ?→13').
    규칙표로 좁혀서 **후보가 하나로 특정되면** 그 규칙을 돌려준다.
      · 모델 규칙이 하나뿐 → 그 규칙(OS 힌트 무시)
      · to(또는 from)로 좁혀 하나만 남으면 → 그 규칙
    특정 못 하면 None(사람이 고른다).
    """
    m = (model or "").strip()
    if not m:
        return None
    with whitelist_session() as db:
        rs = db.query(WLRule).filter_by(model=m, category=_norm_category(category)).all()
    if not rs:
        return None
    if len(rs) == 1:
        r = rs[0]
        return {"from": r.from_os, "to": r.to_os, "ostype": r.ostype}
    cand = rs
    if str(upg_os).strip().isdigit():
        cand = [r for r in cand if r.to_os == int(upg_os)] or cand
    if str(cur_os).strip().isdigit():
        cand = [r for r in cand if r.from_os == int(cur_os)] or cand
    if len(cand) == 1:
        r = cand[0]
        return {"from": r.from_os, "to": r.to_os, "ostype": r.ostype}
    return None


# ── 등록: 신규는 엑셀, 이미 등록된 건 덮어쓰기 ────────────────
def register_devices(user_id: int, devices: list[dict], *,
                     allow_write: bool | None = None) -> dict:
    """FOTA 등록 — **이미 등록된 단말은 덮어쓴다**(사용자 확정 2026-08-10).

    엑셀 업로드는 white_list 행이 이미 있으면 아무것도 하지 않는다. 새 행을 만들지도,
    기존 행의 osType 을 바꾸지도 않는다(실측 2026-08-10: PM451 10대가 2023년
    White451PtoR 로 남아 11→13 요청이 통째로 무시됐다). 그래서 둘로 나눠 보낸다.

        등록 안 된 SN  →  엑셀 업로드          (지금까지와 같음)
        이미 등록된 SN →  FOTA DB 직접 갱신    (osType · android_api · 지원종료일)

    반환 형식은 upload_whitelist_excel 과 같다 — 화면의 _run_items 가 그대로 읽는다.
    """
    from datetime import date as _date

    from app.whitelist import fota as WLFOTA
    from app.whitelist import fota_db as FD

    devices = [d for d in (devices or []) if d.get("sn")]
    if not devices:
        return {"ok": False, "total": 0, "dryRun": True, "msg": "등록할 단말이 없습니다.",
                "success": 0, "details": []}
    dry = WLFOTA._dry_run() if allow_write is None else (not allow_write)

    # 이미 등록된 SN 가려내기 — inner join 이라 terminal 만 있는 건 '등록 안 됨'이다.
    try:
        rows, missing = FD.lookup([d["sn"] for d in devices])
    except Exception as e:  # noqa: BLE001
        # FOTA DB 를 못 보면 예전처럼 엑셀만 올린다 — 덮어쓰기는 못 하지만 신규는 들어간다.
        res = WLFOTA.upload_whitelist_excel(user_id, devices, allow_write=allow_write)
        res["msg"] = f"{res.get('msg', '')} · 기존 등록 확인 실패({type(e).__name__}) — 덮어쓰기 건너뜀"
        return res

    known = {r.sn for r in rows}
    fresh = [d for d in devices if d["sn"] not in known]
    already = [d for d in devices if d["sn"] in known]

    details: list[dict] = []
    msgs: list[str] = []
    ok_all = True

    if fresh:
        r = WLFOTA.upload_whitelist_excel(user_id, fresh, allow_write=allow_write)
        details += r.get("details", [])
        msgs.append(f"신규 {len(fresh)}대 엑셀 업로드 — {r.get('msg', '')}")
        ok_all = ok_all and bool(r.get("ok"))

    # 덮어쓰기는 osType·api·지원종료일이 같은 것끼리 묶어 한 번에 — 한 트랜잭션이라
    # 하나라도 어긋나면 그 묶음 전체가 되돌아간다.
    groups: dict[tuple, list[str]] = {}
    for d in already:
        key = (str(d.get("ostype") or ""), str(d.get("api") or ""),
               str(d.get("endDate") or ""))
        groups.setdefault(key, []).append(d["sn"])

    for (ostype, api, end_s), sns in groups.items():
        if not ostype:
            details += [{"sn": s, "ok": False, "msg": "osType 이 없어 덮어쓰지 못했습니다"}
                        for s in sns]
            ok_all = False
            continue
        try:
            end = _date.fromisoformat(end_s[:10]) if end_s else None
        except ValueError:
            end = None
        rep = FD.change_os_type(sns, ostype, android_api=api, end_date=end, dry_run=dry)
        good = bool(rep.ok)
        ok_all = ok_all and good
        msgs.append(f"기존 {len(sns)}대 덮어쓰기({ostype})"
                    + ("" if good else f" — {rep.error}"))
        details += [{"sn": s, "ok": good,
                     "msg": "덮어씀" if good else (rep.error or "덮어쓰기 실패")}
                    for s in sns]

    return {
        "ok": ok_all, "total": len(devices), "dryRun": dry,
        "msg": " · ".join(m for m in msgs if m) or "처리할 것이 없습니다",
        "success": sum(1 for d in details if d.get("ok")),
        "details": details,
        "fresh": len(fresh), "overwritten": len(already),
    }


# ── 단말 검증(신청/엑셀 공용) ───────────────────────────────
def build_device(sn: str, cur_os, upg_os, model: str,
                 category: str = DEFAULT_CATEGORY, ostype_override: str = "") -> dict:
    """입력 → 검증된 device dict. errors 비면 valid.

    category 로 규칙 구분(GMS/DQA/…)을 골라 ostype 을 정한다.
    ostype_override 가 있으면(수동 지정: KT·LG·기타) 규칙 대신 그 값을 그대로 쓴다.
    """
    sn = (sn or "").strip()
    model = (model or "").strip()
    cur_s, upg_s = str(cur_os or "").strip(), str(upg_os or "").strip()
    override = (ostype_override or "").strip()
    errors: list[str] = []

    # 단일 rule 모델은 OS 비면 자동 채움(해당 구분 안에서)
    if model and (not cur_s or not upg_s):
        sr = single_rule_for(model, category)
        if sr:
            cur_s, upg_s = str(sr["from"]), str(sr["to"])

    if not is_valid_sn(sn):
        errors.append("SN 10자리 아님")
    if not cur_s.isdigit():
        errors.append("Current OS 누락/오류")
    if not upg_s.isdigit():
        errors.append("Upgrade OS 누락/오류")
    if not model:
        errors.append("Model 누락")

    if override:
        ostype = override          # 수동 ostype(규칙에 없는 KT/LG/기타 등)
    else:
        ostype = None
        if model and cur_s.isdigit() and upg_s.isdigit():
            ostype = get_ostype(model, cur_s, upg_s, category)
            if not ostype:
                errors.append(f"규칙 없음 ({model} {cur_s}→{upg_s} · {category})")

    return {
        "sn": sn, "cur": cur_s, "upg": upg_s, "model": model,
        "api": get_api(cur_s), "ostype": ostype, "category": category,
        "endDate": get_end_date(),
        "errors": errors, "valid": not errors,
    }


# ── 요청 구분 자동 판별(발신자 기반) ─────────────────────────
# DQA 팀이 보낸 요청은 자동으로 DQA 구분을 쓴다. 그 외 사람의 요청은 GMS(일반).
# 발신자 이메일 주소(정확히 일치) 또는 도메인(@뒤)으로 매칭한다. work/ JSON 으로 관리.


def _dqa_conf_path():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent.parent / "work" / "whitelist_dqa_senders.json"


def dqa_senders() -> dict:
    """{addresses:[...], domains:[...]} — DQA 팀 발신자 목록."""
    import json
    p = _dqa_conf_path()
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return {"addresses": [a.lower() for a in d.get("addresses", [])],
                    "domains": [x.lower().lstrip("@") for x in d.get("domains", [])]}
        except Exception:  # noqa: BLE001
            pass
    return {"addresses": [], "domains": []}


def is_dqa_sender(sender: str) -> bool:
    """발신자가 DQA 팀인가.

    주소는 [설정 → 사내 인원]의 'DQA팀' 체크에서 온다(2026-08-06). 도메인 규칙은
    예전 json 에만 있어 그대로 함께 본다 — 도메인 전체를 DQA 로 두는 설정이 있으면
    화면에 없더라도 계속 동작해야 한다.
    """
    from app import staff

    s = (sender or "").lower()
    addrs = re.findall(r"[\w.+-]+@[\w.-]+\.\w+", s)
    if not addrs:
        return False
    if any(a in staff.dqa_addresses() for a in addrs):
        return True
    doms = {a.split("@", 1)[1] for a in addrs if "@" in a}
    return any(d in dqa_senders()["domains"] for d in doms)


def category_for_sender(sender: str) -> str:
    """발신자로 요청 구분 자동 결정 — DQA 팀이면 DQA, 아니면 GMS(기본)."""
    return "DQA" if is_dqa_sender(sender) else DEFAULT_CATEGORY


# ── 신청(Requests) ──────────────────────────────────────────
def _req_to_dict(r: WLRequest) -> dict:
    return {
        "id": r.id, "userId": r.requester_email, "userName": r.requester_name,
        "username": r.requester_username, "team": r.team,
        "mailMessageId": getattr(r, "mail_message_id", "") or "",
        "createdAt": r.created_at.isoformat() if r.created_at else "",
        "status": r.status, "memo": r.memo,
        "processedAt": r.processed_at.isoformat() if r.processed_at else "",
        "processedBy": r.processed_by, "rejectComment": r.reject_comment,
        "fotaStatus": r.fota_status, "chargeType": r.charge_type,
        "category": getattr(r, "category", "") or "",
        "amount": r.amount, "poNumber": r.po_number, "poFilePath": r.po_file_path,
        "freeReason": r.free_reason,
        "deletedAt": r.deleted_at.isoformat() if r.deleted_at else "",
        "deletedBy": r.deleted_by, "deleteReason": r.delete_reason,
        "devices": [
            {"sn": d.sn, "api": d.android_api, "endDate": d.support_end_date,
             "ostype": d.ostype, "model": d.model, "cur": d.cur_os, "upg": d.upg_os,
             "partnumber": d.partnumber}
            for d in r.devices
        ],
    }


def save_po_file(req_id: str, filename: str, data: bytes) -> str:
    """유상 PO 파일을 로컬에 원자적으로 저장하고 경로 반환(Google Drive 대체)."""
    import os

    from app.atomic_io import atomic_write_bytes
    from app.whitelist.db import WHITELIST_DB_PATH

    po_dir = WHITELIST_DB_PATH.parent / "whitelist_po"
    safe = os.path.basename(filename or "PO")
    dest = po_dir / f"{req_id}_{safe}"
    atomic_write_bytes(dest, data)
    return str(dest)


def attach_po(req_id: str, po_file_path: str) -> None:
    with whitelist_session() as db:
        r = db.get(WLRequest, req_id)
        if r:
            r.po_file_path = po_file_path
            db.commit()


class DuplicateSerialError(Exception):
    """이미 신청된 일련번호가 섞여 있다. .duplicates = {SN: {request_id, status, ...}}"""

    def __init__(self, duplicates: dict):
        self.duplicates = duplicates
        names = ", ".join(list(duplicates)[:5])
        super().__init__(f"이미 신청된 일련번호 {len(duplicates)}건: {names}")


# ── 등록 여부 조회 캐시 ──────────────────────────────────────────────────────
# 메일함 목록은 화면에 걸린 스레드의 SN 을 전부 모아 한 번에 조회한다 — 실측 6,851개에
# 3.4~4.7초다(2026-08-14). 목록을 그릴 때마다 이걸 다시 하면 그동안 서버가 멈춘다.
#
# 그래서 **SN 하나 단위로** 결과를 들고 있다가 재사용한다(집합 단위가 아니다 — 새 SN 이
# 몇 개 끼어도 그것만 물어보면 된다). 등록 안 된 SN 도 '없음' 으로 기억한다.
#
# 수명을 짧게 두는 이유: 우리 앱을 거치지 않고 FOTA 화면에서 직접 등록하는 경우가
# 실제로 있다(2026-08-10). 그래도 **실제 등록 직전 검사는 fresh=True 로 항상 새로 본다** —
# 캐시는 화면 표시용이고, 중복 등록을 막는 판단은 캐시를 믿지 않는다.
_SNS_TTL_SEC = 300.0
_sns_cache: dict[str, tuple[float, dict | None]] = {}
_sns_lock = threading.Lock()


def invalidate_sns_cache(sns: Iterable[str] | None = None) -> None:
    """등록/변경 직후 호출 — 방금 바꾼 SN 은 캐시가 아니라 FOTA 를 다시 보게 한다."""
    with _sns_lock:
        if sns is None:
            _sns_cache.clear()
            return
        for s in sns:
            _sns_cache.pop((s or "").strip().upper(), None)


def existing_sns(sns: Iterable[str], *, fresh: bool = False) -> dict[str, dict]:
    """이미 등록된 일련번호를 찾는다. {SN(대문자): {request_id, status, created_at, model, …}}

    **FOTA DB 를 직접 본다**(사용자 확정 2026-08-10). 예전에는 우리 앱의 등록 내역을
    봤는데, 앱을 거치지 않고 FOTA 화면에서 등록한 건이나 기록이 빠진 건을 못 잡았다
    (실측 2026-08-10: FOTA 에 65대가 들어갔는데 앱 기록이 없어 중복 검사가 몰랐다).

    FOTA 의 white_list 에 있으면 '등록된 것'이다 — terminal 에만 있는 건 아니다.
    조회에 실패하면 앱 기록으로 물러난다(중복 검사를 아예 못 하는 것보다 낫다).

    fresh: True 면 캐시를 건너뛰고 FOTA 를 새로 본다. **등록을 막을지 정하는 검사는
           반드시 True 로 부른다**(화면 표시는 캐시로 충분하다).
    """
    want = {(s or "").strip().upper() for s in sns if (s or "").strip()}
    if not want:
        return {}

    hits: dict[str, dict] = {}
    todo = set(want)
    if not fresh:
        now = time.monotonic()
        with _sns_lock:
            for sn in want:
                got = _sns_cache.get(sn)
                if got is None or now - got[0] > _SNS_TTL_SEC:
                    continue
                todo.discard(sn)
                if got[1] is not None:
                    hits[sn] = got[1]
        if not todo:
            return hits

    from app.whitelist import fota_db as FD

    try:
        rows, _missing = FD.lookup(sorted(todo))
    except Exception:  # noqa: BLE001
        # FOTA 를 못 보면 앱 기록으로 물러난다. 이 결과는 캐시에 넣지 않는다 —
        # 예비 경로의 답을 정답으로 굳히면 FOTA 가 살아난 뒤에도 계속 틀린다.
        return {**hits, **_existing_sns_local(todo)}

    found: dict[str, dict] = {}
    for r in rows:
        key = (r.sn or "").strip().upper()
        if key not in todo:
            continue
        when = r.update_date_time
        found[key] = {
            "request_id": r.os_type or "",          # 화면은 이 값을 '어디에 등록됐나'로 쓴다
            "status": "registered",
            "created_at": when.strftime("%Y-%m-%d") if when else "",
            "model": "",                            # FOTA 는 모델을 들고 있지 않다
            "ostype": r.os_type or "",
            "api": str(r.android_api or ""),
            "end_date": str(r.support_end_date or ""),
        }
    now = time.monotonic()
    with _sns_lock:
        for sn in todo:                             # 못 찾은 SN 은 '없음' 으로 기억한다
            _sns_cache[sn] = (now, found.get(sn))
    return {**hits, **found}


def _existing_sns_local(want: set[str]) -> dict[str, dict]:
    """FOTA 를 못 볼 때만 쓰는 예비 — 우리 앱의 등록 내역으로 본다."""
    out: dict[str, dict] = {}
    with whitelist_session() as db:
        rows = (
            db.query(WLDevice, WLRequest)
            .join(WLRequest, WLDevice.request_id == WLRequest.id)
            .filter(WLRequest.status.in_(("pending", "confirmed")))
            .all()
        )
        for dev, req in rows:
            key = (dev.sn or "").strip().upper()
            if key not in want:
                continue
            prev = out.get(key)
            # 같은 SN 이 여러 건이면 가장 최근 신청을 보여준다.
            if prev is None or req.created_at > prev["_at"]:
                out[key] = {
                    "request_id": req.id,
                    "status": req.status,
                    "created_at": req.created_at.strftime("%Y-%m-%d"),
                    "model": dev.model or "",
                    "_at": req.created_at,
                }
    for v in out.values():
        v.pop("_at", None)
    return out


def submit_request(*, requester: dict, devices: Iterable[dict], memo: str = "",
                   charge_type: str = "", po_number: str = "", amount: str = "",
                   free_reason: str = "", po_file_path: str = "",
                   category: str = "", allow_duplicates: bool = False) -> dict:
    """신청 1건 저장. requester={username,email,name,team}. devices=build_device 결과들.

    allow_duplicates=False 면 **이미 신청된 SN 이 있으면 저장하지 않고** 예외를 던진다.
    (메일함을 거치지 않고 직접 신청해도 중복이 막히도록 여기서도 검사한다.)
    """
    devices = list(devices)
    if not allow_duplicates:
        # 저장을 막을지 정하는 검사다 — 캐시를 믿지 않고 FOTA 를 새로 본다.
        dup = existing_sns((d["sn"] for d in devices), fresh=True)
        if dup:
            raise DuplicateSerialError(dup)
    rid = gen_req_id()
    with whitelist_session() as db:
        req = WLRequest(
            id=rid,
            requester_username=requester.get("username", ""),
            requester_email=requester.get("email", ""),
            requester_name=requester.get("name", ""),
            team=requester.get("team", ""),
            status="pending", memo=memo or "", charge_type=charge_type or "",
            category=category or (devices[0].get("category", "") if devices else ""),
            po_number=po_number or "", amount=amount or "",
            free_reason=free_reason or "", po_file_path=po_file_path or "",
        )
        for d in devices:
            req.devices.append(WLDevice(
                sn=d["sn"], android_api=str(d.get("api") or ""),
                support_end_date=d.get("endDate", ""), ostype=d.get("ostype") or "",
                model=d.get("model", ""), cur_os=str(d.get("cur") or ""),
                upg_os=str(d.get("upg") or ""), partnumber=d.get("partnumber", ""),
            ))
        db.add(req)
        db.commit()
        return _req_to_dict(req)


def register_from_mail(
    *, devices: Iterable[dict], mail: dict, processed_by: str = "",
    charge_type: str = "무상", po_number: str = "", amount: str = "",
    free_reason: str = "", po_file_path: str = "", memo: str = "",
    category: str = "", do_fota: bool = True, allow_duplicates: bool = False,
    allow_write: bool | None = None,
) -> dict:
    """메일함에서 **신청+승인을 한 번에** — WLRequest(confirmed) 저장 + FOTA 전송.

    관리자가 처리 팝업에서 판단하므로 pending 을 거치지 않는다. 회신 메일은 여기서
    보내지 않는다(2단계: 관리자가 초안을 다듬어 별도로 발송).

    allow_write: True 면 FOTA 에 실제 전송, False 면 dry-run. None 이면 env 게이트.
    dry-run 이면 **등록 이력도 남기지 않는다** — 보내지 않은 것을 보냈다고 기록하면
    그 메일이 처리됨으로 사라진다.
    반환: {ok, request, fota}. dry-run 은 request=None · dry=True.
    """
    from app.whitelist import fota as WLFOTA

    devices = list(devices)
    if not devices:
        return {"ok": False, "msg": "등록할 단말이 없습니다."}
    if not allow_duplicates:
        # 등록을 막을지 정하는 검사다 — 캐시를 믿지 않고 FOTA 를 새로 본다.
        dup = existing_sns((d["sn"] for d in devices), fresh=True)
        if dup:
            raise DuplicateSerialError(dup)

    # 신규는 엑셀 업로드, **이미 등록된 SN 은 덮어쓴다**(사용자 확정 2026-08-10).
    # 엑셀만 올리면 기존 등록이 그대로 남아 요청이 통째로 무시된다.
    fota = register_devices(
        int(mail.get("user_id") or 0),
        [{"sn": d["sn"], "api": d.get("api", ""), "endDate": d.get("endDate", ""),
          "ostype": d.get("ostype", ""), "partnumber": d.get("partnumber", "")}
         for d in devices],
        allow_write=allow_write,
    ) if do_fota else None
    if fota and fota.get("dryRun"):
        return {"ok": True, "dry": True, "request": None, "fota": fota}
    if fota and not fota.get("ok"):
        # 업로드가 통째로 실패했으면 아무것도 안 들어갔다 — 이력도 남기지 않고 대기시킨다.
        return {"ok": False, "msg": fota.get("msg", "FOTA 엑셀 업로드 실패"), "fota": fota}

    rid = gen_req_id()
    with whitelist_session() as db:
        req = WLRequest(
            id=rid,
            requester_username="", requester_email=mail.get("requester_addr", ""),
            requester_name=mail.get("requester_name", ""), team="",
            status="confirmed",
            processed_at=datetime.now(), processed_by=processed_by or "",
            memo=memo or "", charge_type=charge_type or "무상",
            category=category or (devices[0].get("category", "") if devices else ""),
            po_number=po_number or "", amount=amount or "",
            free_reason=free_reason or "", po_file_path=po_file_path or "",
            fota_status=("fota_sent" if (fota and fota["ok"])
                         else "fota_failed" if fota else ""),
            source_mail_id=str(mail.get("mail_id", "")),
            requester_addr=mail.get("requester_addr", ""),
            mail_lang=mail.get("lang", ""),
            mail_message_id=mail.get("message_id", ""),
        )
        for d in devices:
            req.devices.append(WLDevice(
                sn=d["sn"], android_api=str(d.get("api") or ""),
                support_end_date=d.get("endDate", ""), ostype=d.get("ostype") or "",
                model=d.get("model", ""), cur_os=str(d.get("cur") or ""),
                upg_os=str(d.get("upg") or ""), partnumber=d.get("partnumber", ""),
            ))
        db.add(req)
        db.commit()
        result = _req_to_dict(req)
    # 방금 FOTA 에 넣은 SN 은 캐시가 아니라 실제 값을 보게 한다(목록에 바로 반영).
    invalidate_sns_cache(d["sn"] for d in devices)
    return {"ok": True, "request": result, "fota": fota}


def record_registration(*, devices: Iterable[dict], mail: dict, processed_by: str = "",
                        memo: str = "", category: str = "",
                        processed_at: datetime | None = None) -> dict:
    """**이미 올린** 등록을 이력으로만 남긴다(FOTA 로는 아무것도 보내지 않는다).

    AI 해석 팝업과 DQA 팝업은 업로드를 각자 실행한 뒤 여기로 결과를 넘긴다.
    이력이 없으면 등록 내역에 안 뜨고 중복 등록 방어(existing_sns)도 이 SN 을 모른다.

    processed_at: 소급 기록용 — 실제 업로드 시각을 알면 그 값을 넣는다(기본은 지금).
    반환: {ok, request}.
    """
    devices = [d for d in devices if d.get("sn")]
    if not devices:
        return {"ok": False, "msg": "기록할 단말이 없습니다.", "request": None}

    rid = gen_req_id()
    with whitelist_session() as db:
        req = WLRequest(
            id=rid,
            requester_username="", requester_email=mail.get("requester_addr", ""),
            requester_name=mail.get("requester_name", ""), team="",
            status="confirmed",
            processed_at=processed_at or datetime.now(), processed_by=processed_by or "",
            memo=memo or "", charge_type="",
            category=category or "",
            fota_status="fota_sent",
            source_mail_id=str(mail.get("mail_id", "")),
            requester_addr=mail.get("requester_addr", ""),
            mail_lang=mail.get("lang", ""),
            mail_message_id=mail.get("message_id", ""),
        )
        for d in devices:
            req.devices.append(WLDevice(
                sn=d["sn"], android_api=str(d.get("api") or ""),
                support_end_date=d.get("endDate", ""), ostype=d.get("ostype") or "",
                model=d.get("model", ""), cur_os=str(d.get("cur") or ""),
                upg_os=str(d.get("upg") or ""), partnumber=d.get("partnumber", ""),
            ))
        db.add(req)
        db.commit()
        out = {"ok": True, "request": _req_to_dict(req)}
    # 여기 오는 건 이미 FOTA 에 올라간 단말이다 — 캐시에 '없음' 으로 남아 있으면 지운다.
    invalidate_sns_cache(d["sn"] for d in devices)
    return out


def processed_mail_ids() -> set[str]:
    """이미 등록 처리한(=WLRequest 를 만든) 원본 메일 id 집합.

    메일함 목록에서 **이 메일이 처리됐는지**를 판단하는 기준. SN 등록 여부가 아니라
    '이 메일로 등록을 만들었는가'로 봐야 후속 요청(날짜 변경 등)이 묻히지 않는다.
    거절·삭제된 건은 처리로 치지 않는다(다시 떠서 재처리할 수 있어야 한다).
    """
    with whitelist_session() as db:
        rows = (
            db.query(WLRequest.source_mail_id)
            .filter(WLRequest.source_mail_id != "",
                    WLRequest.status.in_(("pending", "confirmed")))
            .all()
        )
    return {r[0] for r in rows if r[0]}


def change_dates_db(
    *, sns: list[str], new_date: str, mail: dict, processed_by: str = "",
    memo: str = "", dry_run: bool = True,
) -> dict:
    """등록일 변경 — **FOTA DB 직접**(웹 API 대신). 2026-08-07 확정.

    white_list.update_date_time = 요청 등록일 01:01:01
    terminal.support_end_date   = 요청 등록일 + 1년
    한 트랜잭션이라 하나라도 어긋나면 전부 되돌아간다. 등록 안 된 SN 이 섞여 있으면
    **아무것도 하지 않는다**(건별 원자성).

    dry_run 이면 DB 도 이력도 건드리지 않는다.
    반환: {ok, dry, lines, request, missing}
    """
    from datetime import date as _date

    from app.whitelist import fota_db as DB

    sns = [s for s in sns if s]
    if not sns:
        return {"ok": False, "dry": dry_run, "lines": ["대상 SN 이 없습니다."],
                "request": None, "missing": []}
    try:
        d = _date.fromisoformat(new_date[:10])
    except ValueError:
        return {"ok": False, "dry": dry_run, "lines": [f"날짜 형식 오류: {new_date}"],
                "request": None, "missing": []}

    rep = DB.change_registration_date(sns, d, dry_run=dry_run)
    out = {"ok": rep.ok, "dry": rep.dry_run, "lines": rep.lines(),
           "request": None, "missing": rep.missing}
    if not rep.ok or rep.dry_run:
        return out

    # 실제로 반영됐을 때만 이력을 남긴다 — 처리한 메일이 목록에서 내려가도록.
    rid = gen_req_id()
    with whitelist_session() as db:
        req = WLRequest(
            id=rid, requester_email=mail.get("requester_addr", ""),
            requester_name=mail.get("requester_name", ""),
            status="confirmed", processed_at=datetime.now(),
            processed_by=processed_by or "",
            memo=(memo or "") + f" [등록일 변경(DB) → {d:%Y-%m-%d}]",
            charge_type="", fota_status="fota_sent",
            source_mail_id=str(mail.get("mail_id", "")),
            requester_addr=mail.get("requester_addr", ""),
            mail_lang=mail.get("lang", ""),
            mail_message_id=mail.get("message_id", ""),
        )
        for s in sns:
            req.devices.append(WLDevice(sn=s, support_end_date=f"{d.year + 1}-{d:%m-%d}"))
        db.add(req)
        db.commit()
        out["request"] = _req_to_dict(req)
    return out


# 수동 처리 완료(등록·FOTA 전송이 아니라 사람이 손으로 '처리 완료' 표시)한 이력의 상태값.
# confirmed(실등록)와 구분해, 등록 내역에서 '등록 아님'으로 표기하고 중복검사에서도 뺀다.
MANUAL_DONE_STATUS = "handled"


def record_manual_done(*, mail: dict, serials: Iterable[str] | None = None,
                       processed_by: str = "", verdict: dict | None = None,
                       memo: str = "") -> dict:
    """메일함에서 '처리 완료(수동)'한 스레드를 **등록 내역에 이력으로** 남긴다.

    실제 등록/FOTA 전송이 아니므로 status='handled'(등록 아님)로 저장한다.
    같은 원본 메일에는 중복 생성하지 않는다(이미 있으면 그대로 반환).
    """
    verdict = verdict or {}
    serials = [s for s in (serials or []) if s]
    mid = str(mail.get("mail_id", ""))
    with whitelist_session() as db:
        if mid:
            exist = (db.query(WLRequest)
                     .filter_by(source_mail_id=mid, status=MANUAL_DONE_STATUS).first())
            if exist:
                return _req_to_dict(exist)
        req = WLRequest(
            id=gen_req_id(),
            requester_email=mail.get("requester_addr", ""),
            requester_name=mail.get("requester_name", ""),
            status=MANUAL_DONE_STATUS,
            processed_at=datetime.now(), processed_by=processed_by or "",
            memo=memo or "수동 처리 완료(등록 아님)",
            source_mail_id=mid,
            requester_addr=mail.get("requester_addr", ""),
            mail_message_id=mail.get("message_id", ""),
        )
        model = (verdict.get("model") or "").strip()
        cur = str(verdict.get("from_os") or "")
        upg = str(verdict.get("to_os") or "")
        for s in serials:
            req.devices.append(WLDevice(sn=s, model=model, cur_os=cur, upg_os=upg))
        db.add(req)
        db.commit()
        return _req_to_dict(req)


def remove_manual_done(mail_id: str) -> int:
    """'다시 열기' 시 그 메일의 수동 처리 이력만 지운다(실등록 confirmed 는 건드리지 않음)."""
    mid = str(mail_id or "")
    if not mid:
        return 0
    with whitelist_session() as db:
        rows = (db.query(WLRequest)
                .filter_by(source_mail_id=mid, status=MANUAL_DONE_STATUS).all())
        for r in rows:
            db.delete(r)
        if rows:
            db.commit()
        return len(rows)


def set_reply_status(req_id: str, status: str) -> None:
    """회신 발송 결과 기록(sent/failed)."""
    with whitelist_session() as db:
        r = db.get(WLRequest, req_id)
        if r:
            r.reply_status = status
            db.commit()


def list_requests() -> list[dict]:
    with whitelist_session() as db:
        rows = db.query(WLRequest).order_by(WLRequest.created_at.desc()).all()
        return [_req_to_dict(r) for r in rows]


def delete_request(req_id: str, reason: str, admin_name: str) -> tuple[bool, str]:
    with whitelist_session() as db:
        r = db.get(WLRequest, req_id)
        if not r:
            return False, "Request 를 찾을 수 없습니다."
        r.status = "deleted"
        r.deleted_at = datetime.now()
        r.deleted_by = admin_name or ""
        r.delete_reason = reason or ""
        db.commit()
    return True, ""
