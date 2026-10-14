"""OS Update 작업(제품 SKU + 버전) — 생성·조회와 단계 상태 관리.

메일 짝에 매여 있던 작업을 제품 기준으로 바꾼 것이다. 단계 API 는 메일 쪽
(app/stages.py)과 **같은 모양**으로 맞춰, 화면·실행기가 두 경로를 거의 같은 코드로
다룰 수 있게 한다. 갈래(Submission ②⑥⑦ / Approval ①③④⑤⑥⑦⑧)는 그대로 유지한다.

파이프라인 공유 상태 키는 `job-{id}` 다. 파이프라인 함수들은 이 값을 불투명한 키로만
쓰므로(`get_state`/`update_state`), 메일 id 대신 넣어도 그대로 동작한다.
"""
from __future__ import annotations

import json
import re

from app.database import get_session
from app.models import OsProduct, OsUpdateJob, OsUpdateJobStage
from app.stages import DONE, PENDING, RUNNING, stage_keys_for


def state_key(job_id: int) -> str:
    """파이프라인 공유 상태 키. 메일 작업의 mail_id 자리에 들어간다."""
    return f"job-{job_id}"


# ── 작업 ───────────────────────────────────────────────────────────────────
def _load_basis(raw: str | None) -> list[dict]:
    """작업에 박아 둔 근거 메일 스냅샷. 비었거나 깨졌으면 빈 목록."""
    if not raw:
        return []
    try:
        got = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return got if isinstance(got, list) else []


def basis_from_update(update: dict | None) -> list[dict]:
    """감지 결과에서 근거 메일 목록을 뽑는다 — 배포 공지분과 [FIH] 분을 같은 모양으로.

    시작할 때 작업에 박아 두는 값이자, 아직 시작하지 않은 회차를 화면에 그릴 때 쓰는 값이다.
    한 곳에서 만들어야 시작 전후로 보이는 내용이 달라지지 않는다.
    """
    if not update:
        return []
    if update.get("mails"):
        return list(update["mails"])
    # [FIH] 감지분은 Submission/Approval 메일 자체가 근거다.
    return [{"mail_id": m["mail_id"], "subject": m.get("subject") or "",
             "version": "", "date": (m.get("received_at") or "")[:10], "not_re": True}
            for m in (update.get("submission"), update.get("approval")) if m]


def _row_to_dict(j: OsUpdateJob, p: OsProduct | None = None) -> dict:
    d = {
        "id": j.id, "product_id": j.product_id, "version": j.version,
        "note": j.note or "", "created_by": j.created_by or "", "created_at": j.created_at,
        "submission_mail_id": j.submission_mail_id, "approval_mail_id": j.approval_mail_id,
        "backfilled_at": j.backfilled_at, "state_key": state_key(j.id),
        "basis_mails": _load_basis(j.basis_mails),
    }
    if p is not None:
        d.update(product=p.product, android=p.android, os_type=p.os_type,
                 initial_os_version=p.initial_os_version or "")
    return d


def list_jobs(product_id: int | None = None) -> list[dict]:
    """작업 목록. product_id 를 주면 그 제품 것만. 최신 버전이 위로."""
    with get_session() as db:
        q = db.query(OsUpdateJob)
        if product_id is not None:
            q = q.filter_by(product_id=product_id)
        jobs = q.all()
        prods = {p.id: p for p in db.query(OsProduct).all()}
        out = [_row_to_dict(j, prods.get(j.product_id)) for j in jobs]
    out.sort(key=lambda d: (_ver_key(d["version"]), d["id"]), reverse=True)
    return out


def get(job_id: int) -> dict | None:
    with get_session() as db:
        j = db.query(OsUpdateJob).filter_by(id=job_id).first()
        if j is None:
            return None
        return _row_to_dict(j, db.query(OsProduct).filter_by(id=j.product_id).first())


def _ver_key(v: str) -> tuple:
    """정렬용 — 버전 비교는 한 곳(version_resolve)만 쓴다.

    형식 2('TR54G15.03')와 리비전 접미('95.07C1')를 그쪽이 이미 다룬다. 여기에 따로
    두면 두 벌이 어긋난다(예전 규칙은 형식 2 를 통째로 (0,0,…) 로 밀어냈다).
    """
    from app.pipeline.version_resolve import _ver_key as _k

    return _k(v)


def _ver_core(v: str) -> str:
    """버전 표기에서 제품 접두어를 뗀 숫자 부분. 'MF52G15.00' → '15.00' · '95.50' → '95.50'."""
    from app.os_products import split_version_prefix

    s = str(v or "").strip().lstrip("vV")
    pre = split_version_prefix(s)
    return s[len(pre):] if pre else s


def is_first_release(initial_os_version: str, version: str) -> bool:
    """이 버전이 제품의 **최초 OS 버전**인가.

    최초 버전이면 참고할 직전 RN 이 있을 수 없다 — 파일서버를 뒤져 보고 판단할 게 아니라
    제품 등록 때 받아 둔 '최초 OS 버전'으로 처음부터 알 수 있다.

    두 값은 **같은 제품**의 것이라 표기 접두어는 떼고 본다. 제품관리에는 'MF52G15.00'
    처럼 접두어까지 적는데 버전 자동 판별은 base('15.00') 를 돌려줄 때가 있어, 그대로
    비교하면 신규 OS 를 신규가 아니라고 조용히 잘못 판정한다.
    """
    from app.pipeline import dev_rn as RN

    a, b = (initial_os_version or "").strip(), (version or "").strip()
    if not a or not b:
        return False
    va, vb = RN.parse_version(_ver_core(a)), RN.parse_version(_ver_core(b))
    if va is None or vb is None:          # 형식이 낯설면 표기 그대로 비교
        return a.lower() == b.lower()
    return va.major.upper() == vb.major.upper() and va.minor == vb.minor


def create(product_id: int, version: str, actor: str = "", note: str = "") -> tuple[bool, str, int]:
    """작업 생성 + 파이프라인 상태 씨앗 심기. (ok, 메시지, job_id)."""
    v = re.sub(r"\s+", "", version or "")
    if not v:
        return False, "버전을 입력하세요.", 0
    with get_session() as db:
        p = db.query(OsProduct).filter_by(id=product_id).first()
        if p is None:
            return False, "제품을 찾을 수 없습니다.", 0
        if db.query(OsUpdateJob).filter_by(product_id=product_id, version=v).first():
            return False, f"이미 {p.product} {v} 작업이 있습니다.", 0
        job = OsUpdateJob(product_id=product_id, version=v, created_by=actor, note=note)
        db.add(job)
        db.commit()
        job_id, product, android, os_type = job.id, p.product, p.android, p.os_type
        first = is_first_release(p.initial_os_version or "", v)

    # 파이프라인이 쓰는 값을 **작업에서 주입**한다. 이게 있으면 ①②가 트리거 메일을
    # Claude 로 분석해 제품·Android 를 알아낼 필요가 없다(메일이 아예 없을 수도 있다).
    # 최초 OS 버전이면 참고할 직전 RN 이 없다는 것도 여기서 이미 정해진다 — ②가 파일서버를
    # 뒤져 보고 나서야 알 일이 아니다(제품 등록 때 '최초 OS 버전'을 받아 뒀다).
    from app.pipeline.state import update_state

    update_state(state_key(job_id),
                 job_id=job_id, product_id=product_id,
                 product=product, android=android, os_type=os_type,
                 version_override=v, release_version=v,
                 **({"rn_first_release": True} if first else {}))
    # 이 SKU 로 와 있던 트리거 메일을 조용히 붙인다 — 승인일이 ①② 의 버전 판별 근거다.
    try:
        auto_link()
    except Exception:  # noqa: BLE001
        pass
    return True, f"{product} A{android} {os_type} · {v} 작업을 만들었습니다.", job_id


def delete(job_id: int) -> tuple[bool, str]:
    with get_session() as db:
        j = db.query(OsUpdateJob).filter_by(id=job_id).first()
        if j is None:
            return False, "작업을 찾을 수 없습니다."
        db.query(OsUpdateJobStage).filter_by(job_id=job_id).delete(synchronize_session=False)
        db.delete(j)
        db.commit()
    return True, "작업을 삭제했습니다."


# ── 메일 연결 ──────────────────────────────────────────────────────────────
# 트리거 메일이 알려주는 건 SKU(제품+Android)와 시점까지다 — **OS 버전은 제목에도 본문에도
# 없다**(실측 19/19). 그래서 메일만으로 작업을 특정할 수 없고, 아래 규칙을 쓴다:
#   1) 같은 빌드지문의 Submission 이 이미 어떤 작업에 붙어 있으면 Approval 도 그 작업으로 확정.
#      (FIH 메일은 Submission/Approval 이 같은 빌드지문을 싣는다 — 19통 전부 확인)
#   2) 아니면 같은 SKU 이면서 그 갈래가 비어 있는 작업이 **정확히 하나일 때만** 자동 연결.
#   3) 여럿이면 자동 연결하지 않는다 — 화면에서 사람이 고른다. 잘못 붙이면 승인일·근거가
#      틀린 채로 RN 이 만들어지므로, 애매하면 붙이지 않는 쪽이 안전하다.
_SIDE_COL = {"submission": "submission_mail_id", "approval": "approval_mail_id"}


def _fingerprint_of(mail_id: int) -> str:
    from app.database import get_session as _gs
    from app.models import CachedMail
    from app.pipeline import fih_mail as F

    with _gs() as db:
        m = db.query(CachedMail).filter_by(id=mail_id).first()
        return F.parse_mail(m)["fingerprint"] if m else ""


def link_mail(job_id: int, side: str, mail_id: int) -> tuple[bool, str]:
    """메일을 작업의 한 갈래에 붙인다. 승인 메일이면 승인일도 상태에 심는다."""
    from app.models import CachedMail
    from app.pipeline import fih_mail as F
    from app.pipeline.state import update_state

    col = _SIDE_COL.get(side)
    if not col:
        return False, f"알 수 없는 갈래입니다: {side}"
    with get_session() as db:
        j = db.query(OsUpdateJob).filter_by(id=job_id).first()
        if j is None:
            return False, "작업을 찾을 수 없습니다."
        m = db.query(CachedMail).filter_by(id=mail_id).first()
        if m is None:
            return False, "메일을 찾을 수 없습니다."
        other = (db.query(OsUpdateJob)
                 .filter(getattr(OsUpdateJob, col) == mail_id, OsUpdateJob.id != job_id)
                 .first())
        if other is not None:
            return False, f"이 메일은 이미 다른 작업({other.version})에 연결돼 있습니다."
        parsed = F.parse_mail(m)
        setattr(j, col, mail_id)
        db.commit()
        version, product = j.version, parsed["product"]

    seed = {f"{side}_mail_id": mail_id,
            "build_fingerprint": parsed["fingerprint"] or ""}
    if side == "approval":
        # ①②가 버전 판별 기준으로 쓰는 FIH 승인 날짜. 본문에 승인일 항목이 없어 수신일을 쓴다.
        seed["approval_date"] = F.received_date(parsed["received_at"])
        seed["trigger_mail_id"] = mail_id
    else:
        seed["submission_date"] = parsed["submission_date"] or F.received_date(
            parsed["received_at"])
    update_state(state_key(job_id), **seed)
    return True, f"{product} {version} 작업에 {side} 메일을 연결했습니다."


def _linked_ids(side: str) -> set:
    """이미 어느 작업엔가 물려 있는 트리거 메일 id.

    작업의 `submission_mail_id`·`approval_mail_id` 뿐 아니라 **근거 메일(basis_mails)**
    까지 본다. C 리비전은 앞 C 판을 대체하므로 한 작업이 회차를 갈아타는 일이 있는데
    (실측 2026-09-21 job-24: 86.07C2 → 86.07C8), 그때 놓아준 옛 회차의 메일이 '신규'
    로 되살아나 **이미 끝낸 회차를 또 만들라고** 올라왔다. 근거로 적어 둔 메일은
    그 작업이 안고 있는 것으로 본다.
    """
    col = _SIDE_COL[side]
    with get_session() as db:
        rows = db.query(getattr(OsUpdateJob, col), OsUpdateJob.basis_mails).all()
    out = set()
    for v, basis in rows:
        if v:
            out.add(v)
        for m in _load_basis(basis):
            mid = m.get("mail_id")
            if mid:
                out.add(mid)
    return out



# 메일 조회 결과 캐시 — 화면을 그릴 때마다 메일 전문을 훑으면 서버가 멈춘다.
# 배포 공지 후보 한 번 뽑는 데 ~0.8초, 트리거 메일 파싱에 ~0.25초가 든다. 한 페이지에
# 제품마다 여러 번 필요하므로 그대로 두면 렌더 한 번이 수 초가 되고, 그동안 이벤트
# 루프가 막혀 **모든 사용자**의 화면이 멈춘다.
# 메일 판번호(건수, 최대 id)가 바뀌면 자동으로 버린다 — 새 메일이 오면 다시 읽는다.
_mail_cache: dict = {}


def _mail_ver() -> tuple:
    from app import background

    return background.data_version()


def _cached(key, build):
    ver = _mail_ver()
    hit = _mail_cache.get(key)
    if hit is not None and hit[0] == ver:
        return hit[1]
    val = build()
    _mail_cache[key] = (ver, val)
    return val


def _trigger_mails() -> list[dict]:
    from app.pipeline import fih_mail as F

    return _cached("trigger", F.trigger_mails)


def _request_versions() -> dict:
    """{빌드지문열쇠: 승인 요청 메일에서 읽은 공식 버전 …}.

    'Request to start the GMS * approval test' 는 **우리가 FIH 로 보내는** 메일이라
    누락이 없고, 배포 공지보다 먼저 온다(실측 MF52e: 8/28 요청 · 배포 공지 없음).
    지문이 [FIH] 트리거와 이 메일을 잇는 다리다 — 자세한 사정은 gms_request 참조.
    """
    from app.pipeline import gms_request as G

    return _cached("gms_request", G.request_versions)


def _deploy_for(product: str, android: str) -> list[dict]:
    from app.pipeline.rn_research import _deploy_candidates

    return _cached(("deploy", product.upper(), str(android)),
                   lambda: _deploy_candidates(0, product, android))


def os_version_like(ver: str, initial: str) -> bool:
    """그 제품의 **OS 버전 모양**인가 — 배포 공지의 버전을 회차로 볼지 가른다.

    배포 공지 제목에는 OS 가 아닌 것도 온다. 실측 2026-10-13: '[RFID] RF88 / MF52 RFID 통합
    SDK 1.0.1 배포 안내' 가 MF52 A15 의 '1.0' 회차로 신규 작업에 올라왔다(사용자: "MF52 A15
    GMS는 OS Update가 아닙니다").

    규칙: OS 버전 문법(dev_rn)으로 읽혀야 하고, 앞자리의 **끝 번호**가 제품 관리의
    '최초 OS 버전' 과 같아야 한다. 끝 번호로 견주는 까닭 — 같은 제품을 'TR54G15.04' 와
    '15.04' 로 섞어 쓴다(둘 다 15). 최초 OS 버전이 비어 있거나 읽히지 않으면 문법만 본다.
    """
    import re as _re

    from app.pipeline.rn_research import _parsed_ver

    pv = _parsed_ver(ver)
    if pv is None or getattr(pv, "major", None) is None:
        return False
    iv = _parsed_ver(initial or "")
    if iv is None or getattr(iv, "major", None) is None:
        return True

    def tail(major) -> str:
        m = _re.search(r"(\d+)$", str(major))
        if not m:
            return str(major)
        return m.group(1).lstrip("0") or "0"

    return tail(pv.major) == tail(iv.major)


def _deploy_rounds(product: dict) -> list[dict]:
    """배포 공지에서 뽑은 회차 — **트리거 메일이 없어도 배포를 인지**하기 위한 보완.

    지금 트리거는 [FIH] 메일이라 비GMS·인증 미대상·긴급 생산 투입·DQA 미경유 건이
    통째로 누락된다(회의 2026-08-04). 배포 공지는 그런 건에도 오므로, **공지는 있는데
    작업이 없는 회차**를 여기서 찾아 '신규'로 올린다.

    base 버전 단위로 묶는다 — B판·RC판이 각각 한 줄이 되면 같은 회차가 여러 번 뜬다.

    **B판(Beta)만 있는 묶음은 회차로 보지 않는다.** 정식 배포 공지가 아직 없다는 뜻이라
    작업을 시작할 대상이 아니다(사용자 확정 2026-08-19).
    """
    from app.pipeline.rn_research import _base_ver, _official_version, _parsed_ver

    def _is_release(ver: str) -> bool:
        """정식 배포로 볼 수 있는 표기인가 — 접미가 없거나 C 리비전.

        개발 RN 수집의 기준과 같다(dev_rn.Version.tracked). A/B/S/RC 는 비배포 빌드다.
        """
        pv = _parsed_ver(ver)
        sfx = (pv.suffix if pv else "").upper()
        return (not sfx) or sfx.startswith("C")

    prod, andr = product.get("product", ""), str(product.get("android", ""))
    if not prod:
        return []
    try:
        cands = _deploy_for(prod, andr)
    except Exception:  # noqa: BLE001  (감지 실패가 목록 전체를 막으면 안 된다)
        return []

    by_base: dict[str, list] = {}
    _init = str(product.get("initial_os_version") or "")
    for c in cands:
        # OS 버전 모양이 아니면(SDK · 앱 · 펌웨어 배포 공지) 회차가 아니다.
        if not os_version_like(c.get("version", ""), _init):
            continue
        b = _base_ver(c.get("version", ""))
        if b:
            by_base.setdefault(b, []).append(c)

    out: list[dict] = []
    for base, group in by_base.items():
        # 정식 배포 공지가 하나라도 있어야 회차다. 실측 2026-08-19:
        #   '[PM84][SecGate] 84.10 B2 (Jul 06 2026) 배포' 는 본문이 "고객 배포용 PM84
        #   이미지를 배포합니다" 인 Beta(이슈 #33679 대응) 인데, base 가 84.10 이라
        #   'PM84 A13 84.10' 이 신규 회차로 올라왔다. 정식 배포가 아니므로 감지하지 않는다.
        # 나중에 정식 공지가 오면 그때 이 묶음에 들어와 회차로 잡힌다.
        if not any(_is_release(c.get("version", "")) for c in group):
            continue
        official, _note = _official_version(base, group)
        # 원 공지(회신 아님) 중 가장 최근 것을 그 회차의 대표로 삼는다.
        origin = [c for c in group if c.get("not_re")] or group
        rep = max(origin, key=lambda c: c.get("mail_id", 0))
        # 근거 메일을 **전부** 실어 보낸다. 한 회차가 95.07 · C1 · C2 처럼 여러 공지로
        # 나뉘어 오므로, 대표 하나만 주면 '무엇을 보고 이 회차라고 했는지' 확인할 수 없다.
        # (최신 공지가 위 — 대표로 삼은 것이 맨 앞에 온다.)
        mails = sorted(group, key=lambda c: c.get("mail_id", 0), reverse=True)
        out.append({
            "build": "", "incremental": "",
            "submission": None, "approval": None,
            "approved": False,              # 배포 공지만으로는 Google 승인 여부를 모른다
            "source": "deploy",             # 화면이 [FIH] 감지분과 문구를 다르게 쓴다
            "version": official,
            "basis": (f"배포 공지 #{rep['mail_id']} — {rep['version']} "
                      f"({rep.get('date') or '날짜?'})"),
            "mail_id": rep["mail_id"],
            "mails": [{
                "mail_id": c["mail_id"],
                "subject": c.get("subject") or "",
                "version": c.get("version") or "",
                "date": c.get("date") or "",
                "not_re": bool(c.get("not_re")),
            } for c in mails],
        })
    return out


def _fp_of(r: dict) -> str:
    """회차의 빌드지문 열쇠 — 두 갈래 중 아무 메일에서나 얻는다."""
    from app.pipeline.gms_request import fp_key_of

    return fp_key_of(r.get("build", ""), r.get("incremental", ""))


def _nv0(v: str) -> str:
    """버전 비교용 정규화 — 공백 제거 + 대문자."""
    from app.pipeline.rn_research import _norm_ver

    return _norm_ver(v)


def detected_updates(product: dict) -> list[dict]:
    """이 제품(SKU)에 **감지된 업데이트** 목록 — 아직 작업으로 만들지 않은 회차.

    같은 회차의 Submission·Approval 메일은 **빌드지문이 같다**(실측 19/19). 그래서
    빌드지문으로 묶으면 '몇 번째 업데이트인지'가 메일에서 그대로 나온다.
    승인 메일이 있으면 Google 승인까지 끝난 회차다.

    버전은 **기존 판별기**(pipeline.version_resolve)가 정한다 — ①② 가 쓰던 그 규칙이다.
    반환 [{build, incremental, submission, approval, approved, version, basis}] (최신 먼저).

    **Others 는 감지하지 않는다**(사용자 확정 2026-08-05). 감지의 근거인 [FIH] 트리거
    메일과 GMS 배포 공지는 Android 회차에만 오므로, OS 가 없거나 다른 OS 인 제품에
    적용하면 남의 회차를 잘못 물어 온다. 여기 한 곳만 막으면 신규 알림·신규 작업
    목록·버전 줄이 함께 조용해진다.
    """
    from app import os_products as P
    from app.pipeline import fih_mail as F

    if P.is_others(product.get("category")):
        return []
    # 지금은 **등록된 우리 GMS 제품만** 다룬다(사용자 확정 2026-09-07).
    # OEM/ODM 공급분(HT2·Z7_JP 등)은 제품 관리에 없으므로 자연히 빠진다.
    if str(product.get("os_type") or "").strip().upper() != "GMS":
        return []

    _reqmap = _request_versions()
    taken = _linked_ids("submission") | _linked_ids("approval")
    rounds: dict[str, dict] = {}
    for d in _trigger_mails():                       # 최신순
        if d["mail_id"] in taken or not _same_sku(d, product):
            continue
        # 빌드지문이 없으면 회차를 묶을 수 없으니 메일 하나를 한 회차로 본다.
        key = d["fingerprint"] or f"mail-{d['mail_id']}"
        r = rounds.setdefault(key, {
            "build": d["build_id"], "incremental": d["incremental"],
            "submission": None, "approval": None, "type": d["submission_type"],
        })
        if r.get(d["side"]) is None:
            r[d["side"]] = d

    def when(r: dict) -> float:
        return F._ts((r["submission"] or r["approval"])["received_at"])

    with get_session() as db:
        have_job = {j.version for j in
                    db.query(OsUpdateJob).filter_by(product_id=product["id"]).all()}

    # 오래된 회차부터 — 먼저 나간 회차가 낮은 버전이어야 한다.
    out = sorted(rounds.values(), key=when)
    for r in out:
        r["approved"] = r["approval"] is not None
        ref = r["approval"] or r["submission"]
        # 이 회차의 **공식 버전**은 승인 요청 메일이 알고 있다. 지문이 같으면 같은 빌드다.
        # 배포 공지에서 뽑는 기존 판별은 그것을 못 찾았을 때만 쓴다 — 공지가 안 나가는
        # 회차가 실제로 있다(실측 MF52e C1).
        _req = _reqmap.get(_fp_of(r))
        if _req:
            r["version"] = _req["version"]
            r["basis"] = (f"승인 요청 메일 #{_req['mail_id']} — "
                          f"{_req['version']} (지문 {_req['fp_key']})")
            r["req_mail_id"] = _req["mail_id"]
        else:
            r["version"], r["basis"] = suggest_version(
                product, trigger_date=(ref["received_at"] if ref else ""))
        # 이 버전으로 만든 작업이 이미 있으면 '신규'가 아니다(소급 완료분 포함).
        # 비교는 base 로 한다 — 작업이 '95.07', 판별이 '95.07C1' 이면 같은 회차다.
        from app.pipeline.rn_research import _base_ver as _bv0

        r["job_exists"] = bool(r["version"]) and _bv0(r["version"]) in {
            _bv0(v) for v in have_job}
        # **지문으로 버전을 안 회차는 표기 그대로 본다.** base 로만 보면 같은 회차의
        # C 리비전이 새 빌드인데도 묻힌다 — 실측 MF52e: job 이 MF52EG15.00 인데
        # 8/28 요청은 MF52EG15.00C1(증분 R25, 기존 작업은 RA4)로 **다른 빌드**였다.
        if r.get("req_mail_id") and r["job_exists"]:
            r["job_exists"] = _nv0(r["version"]) in {_nv0(v) for v in have_job}
    out.reverse()                                    # 화면에는 최신 회차가 위로

    # 같은 버전으로 판별된 회차는 하나로 본다 — 재제출이면 최신 것만 남긴다.
    # (버전 목록이 버전 단위라, 중복을 두면 알림 수와 줄 수가 어긋난다.)
    # 같은 회차인지는 **base 버전**으로 본다 — 한쪽은 '95.07', 다른 쪽은 '95.07C1' 로
    # 나올 수 있어 문자열 그대로 비교하면 같은 회차가 두 줄이 된다.
    from app.pipeline.rn_research import _base_ver as _bv

    seen: set[str] = set()
    uniq = []
    for r in out:
        # **합치기는 base 로 한다.** 여기서 표기를 그대로 쓰면 같은 회차를 다른 근거가
        # 다르게 부른 것(배포 공지 '86.07' vs 승인 요청 '86.07C2')이 두 줄로 갈라져,
        # 작업이 있는데도 빈 줄 하나가 '신규' 로 뜬다(실측 PM86 A11).
        # 새 빌드인지 아닌지는 아래 job_exists 가 지문으로 가른다 — 역할이 다르다.
        key = _bv(r["version"]) if r["version"] else ""
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        uniq.append(r)

    # 배포 공지 기반 보완 — 트리거 메일이 없어 여기까지 못 온 회차를 마저 올린다.
    have_base = {_bv(v) for v in have_job}
    for d in _deploy_rounds(product):
        key = _bv(d["version"])
        if not key or key in seen:
            continue
        seen.add(key)
        d["job_exists"] = key in have_base
        uniq.append(d)
    return uniq


def suggest_version(product: dict, trigger_date: str = "") -> tuple[str, str]:
    """이번에 나갈 OS 버전 — **기존 판별기를 그대로 부른다**.

    1) 배포 공지 메일(pipeline.rn_research 의 판별기). ② 가 이슈 근거 메일을 찾을 때
       쓰는 그것이다. 제목에 버전이 그대로 적혀 온다:
           [PM45][A9] 45.81 (Jul 19 2026) 배포
       Beta(B1)·RC 를 거쳐도 base 는 그대로라 어느 공지를 잡아도 같은 값이 나온다.
       추정이 아니라 명시라 이게 가장 정확하다.
    2) 그런 메일이 없으면 pipeline.version_resolve (출시 이력 + FIH 승인일 증분).
       받아 둔 출시 이력 캐시를 넘겨 **외부 조회 없이** 돌린다.
    """
    from app import release_history as RH
    from app.pipeline.fih_mail import received_date
    from app.pipeline.rn_research import _base_ver, _official_version
    from app.pipeline.version_resolve import resolve_release_version

    prod, andr = product.get("product", ""), str(product.get("android", ""))
    cands = _deploy_for(prod, andr)
    for c in cands:
        v = _base_ver(c["version"])
        if v:
            # 공식 릴리즈 버전명에는 **가장 높은 C 리비전**이 붙는다(95.07 + C1 → 95.07C1).
            # ②와 같은 규칙을 써야 목록의 버전과 RN 의 버전이 어긋나지 않는다.
            official, note = _official_version(
                v, [x for x in cands if _base_ver(x["version"]) == v])
            return official, (f"배포 공지 {c['version']} ({c['date'] or '날짜?'})"
                              + (f" · {note}" if note else ""))

    cached = RH.get(product["id"])
    if not cached.get("versions"):
        return "", "배포 공지도 출시 이력도 없습니다 — [출시 이력 조회] 를 누르거나 직접 입력하세요"
    rel = resolve_release_version(
        0, prod, andr,
        trigger_date=received_date(trigger_date) if trigger_date else "",
        history={"versions": cached["versions"], "notes": []},
    )
    return rel.get("version", ""), rel.get("note", "")


def sync_job_version(job_id: int, version: str) -> tuple[bool, str]:
    """작업의 버전을 **확정된 공식 버전**으로 맞춘다(95.07 → 95.07C1). (바뀜, 사유).

    ②가 배포 공지에서 C 리비전을 확정하면 작업 목록의 버전도 그 표기여야 한다 —
    그렇지 않으면 같은 회차가 '95.07'(작업)과 '95.07C1'(감지) 두 줄로 갈라져 보인다.
    같은 제품에 그 버전 작업이 이미 있으면 건드리지 않는다(유일 키 충돌).
    """
    v = (version or "").strip()
    if not v:
        return False, "버전이 비어 있습니다."
    with get_session() as db:
        job = db.query(OsUpdateJob).filter_by(id=job_id).first()
        if job is None:
            return False, "작업을 찾지 못했습니다."
        if (job.version or "").strip() == v:
            return False, ""
        dup = db.query(OsUpdateJob).filter_by(product_id=job.product_id, version=v).first()
        if dup is not None:
            return False, f"{v} 작업(#{dup.id})이 이미 있어 버전을 바꾸지 않았습니다."
        old = job.version
        job.version = v
        db.commit()
        return True, f"작업 버전 {old} → {v}"


def _mail_side_progress(update: dict | None) -> tuple[int, int]:
    """이 회차를 **메일 짝으로** 진행한 흔적 — (완료 단계 수, 전체 단계 수).

    같은 회차를 /osupdate 의 작업(job)이 아니라 메일함 쪽에서 진행할 수 있다. 그 진행은
    mail_stages 에 남는데 작업 History 는 job 만 봐서, 실제로 RN 이 검수 대기인 회차가
    '출시됨' 으로 보였다(실측 PM67 67.09 · 메일 864 의 release_note=review).

    단계 줄은 화면을 열기만 해도 pending 으로 만들어지므로, **pending 이 아닌 줄이
    하나라도 있을 때만** 진행으로 본다. 아니면 (0, 0) 을 돌려 손대지 않은 것으로 둔다.
    """
    from app.models import MailStage

    ids = [m["mail_id"] for m in ((update or {}).get("submission"),
                                  (update or {}).get("approval")) if m]
    if not ids:
        return 0, 0
    with get_session() as db:
        rows = db.query(MailStage).filter(MailStage.mail_id.in_(ids)).all()
    if not any(r.status != PENDING for r in rows):
        return 0, 0
    return sum(1 for r in rows if r.status == DONE), len(rows)


def pending_updates(product: dict) -> list[dict]:
    """아직 시작하지 않은 감지 회차만 — 제품 카드의 신규 알림·시작 안내에 쓴다.

    판정을 version_rows 와 **같은 자리에서** 한다. 예전에는 여기서 job 유무만 보고
    화면의 줄은 version_rows 가 따로 정해, 배지는 '신규 1' 인데 그 줄은 '출시됨' 으로
    보이는 일이 있었다(실측 PM67 67.09 · PM68 68.04).
    """
    return [r["update"] for r in version_rows(product)
            if r["state"] == NEW and r.get("update")]


def basis_for_job(job: dict) -> list[dict]:
    """작업에 남은 근거 메일. 스냅샷이 없으면 연결된 트리거 메일에서 만든다.

    이 컬럼이 생기기 전에 시작한 작업은 스냅샷이 비어 있다 — 그런 작업도 근거를
    볼 수 있어야 하므로 submission/approval 연결에서 되살린다.
    """
    from app.models import CachedMail

    got = job.get("basis_mails") or []
    if got:
        return got
    ids = [job.get(c) for c in _SIDE_COL.values() if job.get(c)]
    if not ids:
        return []
    with get_session() as db:
        rows = db.query(CachedMail).filter(CachedMail.id.in_(ids)).all()
        return [{"mail_id": m.id, "subject": m.subject or "", "version": "",
                 "date": (m.received_at or "")[:16], "not_re": True} for m in rows]


def save_basis(job_id: int, mails: list[dict]) -> None:
    """근거 메일 스냅샷을 작업에 저장한다. 빈 목록이면 기존 값을 지우지 않는다."""
    if not mails:
        return
    with get_session() as db:
        j = db.query(OsUpdateJob).filter_by(id=job_id).first()
        if j is None:
            return
        j.basis_mails = json.dumps(mails, ensure_ascii=False)
        db.commit()


def start_from_update(product: dict, version: str, update: dict,
                      actor: str = "") -> tuple[bool, str, int]:
    """감지된 업데이트로 작업을 만들고, 그 회차의 메일을 바로 연결한다."""
    ok, msg, job_id = create(product["id"], version, actor=actor)
    if not ok:
        return ok, msg, 0
    # 근거를 **시작하는 순간** 박아 둔다. 배포 공지분은 붙일 Submission/Approval 이
    # 없어, 여기서 남기지 않으면 시작과 동시에 근거가 사라진다.
    save_basis(job_id, basis_from_update(update))
    notes = []
    for side in ("submission", "approval"):
        d = update.get(side)
        if d:
            ok2, m2 = link_mail(job_id, side, d["mail_id"])
            notes.append(side if ok2 else f"{side} 연결 실패: {m2}")
    if notes:
        msg += " (메일 연결: " + ", ".join(notes) + ")"
    return True, msg, job_id


# ── 버전 = 작업 하나 ───────────────────────────────────────────────────────
NEW = "new"              # 트리거 메일은 왔는데 아직 작업을 시작하지 않음
IDLE = "idle"            # 개발 RN 만 있고 메일도 작업도 없음
PUBLISHED = "published"  # 이미 출시됐지만 아직 완료로 닫지 않음(최근 출시분)
RUNNING_ = "running"     # 작업 진행 중
FINISHED = "done"        # 모든 단계 완료

VERSION_LABELS = {NEW: "신규", IDLE: "미진행", PUBLISHED: "출시됨",
                  RUNNING_: "진행 중", FINISHED: "완료"}
VERSION_COLORS = {NEW: "red-6", IDLE: "grey-6", PUBLISHED: "blue-grey-6",
                  RUNNING_: "orange-8", FINISHED: "green-8"}


def version_rows(product: dict) -> list[dict]:
    """제품 하나의 **버전 목록 = 작업 목록**. 최신 버전이 위로.

    한 줄에 모이는 것:
      · 개발 Release Note 수집분(문서)
      · 그 버전으로 만든 작업(단계 진행 상황)
      · 아직 작업이 없지만 트리거 메일로 감지된 회차(신규)
      · 출시 이력(Service Portal·KB)에 올라간 버전 — 이미 고객에게 나간 회차
    넷을 합집합으로 본다. 어느 하나만 보면 빠지는 회차가 생긴다 — 개발 RN 은 뒤늦게
    올라오고, 과거 회차는 출시 이력에만 남아 있다.

    **최초 OS 버전보다 뒷자리가 낮은 버전은 뺀다.** 그건 Android 가 더 낮은 다른
    SKU 의 것이다(PM95 A15 최초 95.51 → 95.03·95.02·95.00 은 A13 것).
    출시 이력의 Confluence 쪽은 Android 를 가리지 않고 제품 전체를 주기 때문에
    걸러 주지 않으면 남의 회차가 이 제품 칸에 섞인다.
    (작업은 거르지 않는다 — 사람이 만든 것은 잘못 만들었더라도 보여야 지울 수 있다.)
    """
    from app import release_history as RH
    from app.pipeline import dev_rn as RN

    floor = RN.parse_version(str(product.get("initial_os_version") or "").strip())

    def mine(version: str) -> bool:
        v = RN.parse_version(version)
        return v is None or RN._same_series(v, floor)

    rows: dict[str, dict] = {}
    _floor_raw = str(product.get("initial_os_version") or "").strip()

    def slot(version: str) -> dict:
        # 자료마다 표기가 갈린다(출시 이력 '15.01' vs 개발 RN 'PM84PG15.01'). 등록된
        # 최초 OS 버전을 기준으로 표준 표기에 맞춰 놓지 않으면 한 회차가 두 줄이 된다.
        version = RN.canonical_version(version, _floor_raw)
        return rows.setdefault(version, {
            "version": version, "release": None, "job": None, "update": None,
            "published_at": "",
        })

    for d in RN.list_releases(product["id"]):
        if mine(d["version"]):
            slot(d["version"])["release"] = d
    for v, date in RH.published(product["id"]).items():
        if mine(v):
            slot(v)["published_at"] = date
    for j in list_jobs(product["id"]):
        slot(j["version"])["job"] = j
    for u in detected_updates(product):
        # 버전을 못 정한 회차는 빌드 값으로 자리를 만든다 — 조용히 사라지면 안 된다.
        if u["version"] and not mine(u["version"]):
            continue
        slot(u["version"] or f"? ({u['incremental']})")["update"] = u

    # 출시 여부 판정용 — 여기도 같은 표준 표기로 맞춰 둔다.
    _pub_std = {RN.canonical_version(v, _floor_raw)
                for v in RH.published(product["id"])}
    out = []
    for r in rows.values():
        j = r["job"]
        if j is not None:
            done, total = job_progress(j["id"])
            r["done"], r["total"] = done, total
            r["state"] = FINISHED if total and done == total else RUNNING_
            r["backfilled"] = bool(j.get("backfilled_at"))
        else:
            r["backfilled"] = False
            # 작업(job)이 없어도 메일 짝으로 진행했을 수 있다 — 그걸 먼저 본다.
            # 이미 출시된 회차라도 손대던 일이 남아 있으면 그게 더 급한 정보다.
            m_done, m_total = _mail_side_progress(r["update"])
            r["done"], r["total"] = m_done, m_total
            if m_total:
                r["state"] = FINISHED if m_done == m_total else RUNNING_
            elif r["published_at"] or r["version"] in _pub_std:
                r["state"] = PUBLISHED
            elif r["update"] is not None:
                r["state"] = NEW          # 트리거 메일이 왔는데 아직 시작 안 함
            else:
                r["state"] = IDLE
        out.append(r)
    out.sort(key=lambda r: _ver_key(r["version"]), reverse=True)
    return out


#: 신규 작업 화면이 담는 상태 — 아직 끝나지 않은 회차.
#: 시작하면 NEW → RUNNING_ 로 바뀌지만 목록에서 빠지지는 않는다. 시작하자마자
#: 사라지면 같은 자리에서 이어 처리할 수가 없기 때문이다(사용자 확정 2026-08-05).
#: IDLE(개발 RN 만 있음)·PUBLISHED(이미 나감)·FINISHED 는 '할 일'이 아니라 뺀다.
OPEN_STATES = (NEW, RUNNING_)


def open_rounds(q: str = "") -> list[dict]:
    """등록된 모든 제품을 통틀어 **아직 끝나지 않은 회차**를 평평하게 모은다.

    작업 History(/osupdate)는 제품 → 버전 두 겹으로 접히지만, 여기서는 그 겹을 걷고
    회차 하나를 곧바로 한 줄로 놓는다. 각 줄은 version_rows() 가 주는 것과 같은 dict 에
    어느 제품 것인지(`product`)만 얹은 것이라, 화면 코드를 그대로 다시 쓸 수 있다.

    정렬: 신규가 먼저, 그 안에서 최신 회차부터. 진행 중인 것은 그 아래로 내린다 —
    손대지 않은 것이 위에 있어야 놓치지 않는다.
    """
    from app import os_products as P

    out: list[dict] = []
    for p in P.list_products(q):
        for d in version_rows(p):
            if d["state"] in OPEN_STATES:
                out.append({**d, "product": p})
    out.sort(key=lambda d: (d["state"] != NEW, _round_seen_at(d) * -1,
                            d["product"]["product"]))
    return out


def _round_seen_at(d: dict) -> float:
    """이 회차를 언제 알게 됐는지(정렬용). 모르면 0."""
    from app.pipeline import fih_mail as F

    u = d.get("update") or {}
    for side in ("approval", "submission"):
        m = u.get(side)
        if m and m.get("received_at"):
            return F._ts(m["received_at"])
    j = d.get("job") or {}
    return float(j.get("id") or 0)


def close_as_done(product_id: int, version: str, actor: str = "",
                  backfill: bool = True) -> tuple[bool, str]:
    """이 버전을 **실행 없이 완료로 기록**한다(소급).

    작업이 없으면 만들고, 모든 단계를 완료로 표시한다. backfill=True 면 '소급으로 닫은
    건'이라고 표시해 둔다 — 우리가 실제로 처리한 회차와 구분하기 위해서다.
    """
    from datetime import datetime as _dt

    from app.stages import DONE as _DONE
    from app.stages import SIDE_APPROVAL, SIDE_SUBMISSION

    with get_session() as db:
        j = (db.query(OsUpdateJob)
             .filter_by(product_id=product_id, version=version).first())
        job_id = j.id if j else 0
    if not job_id:
        ok, msg, job_id = create(product_id, version, actor=actor,
                                 note="출시 이력 기준 소급 완료" if backfill else "")
        if not ok:
            return False, msg

    for side in (SIDE_SUBMISSION, SIDE_APPROVAL):
        for s in get_or_create_stages(job_id, side):
            if s.status != _DONE:
                set_stage_status(s.id, _DONE, actor)
    if backfill:
        with get_session() as db:
            j = db.query(OsUpdateJob).filter_by(id=job_id).first()
            if j is not None:
                j.backfilled_at = _dt.now()
                db.commit()
    return True, f"{version} 을(를) 완료로 기록했습니다."


def reopen(job_id: int, actor: str = "") -> tuple[bool, str]:
    """완료로 닫은 작업을 **다시 연다** — 모든 단계를 대기로 되돌린다.

    소급 표시도 지운다. 잘못 닫았을 때 되살리는 길이다(작업 삭제는 통째로 없앤다).
    """
    from app.stages import PENDING as _PENDING
    from app.stages import SIDE_APPROVAL, SIDE_SUBMISSION

    with get_session() as db:
        j = db.query(OsUpdateJob).filter_by(id=job_id).first()
        if j is None:
            return False, "작업을 찾을 수 없습니다."
        version = j.version
    for side in (SIDE_SUBMISSION, SIDE_APPROVAL):
        for s in get_or_create_stages(job_id, side):
            if s.status != _PENDING:
                set_stage_status(s.id, _PENDING, actor)
    with get_session() as db:
        j = db.query(OsUpdateJob).filter_by(id=job_id).first()
        if j is not None:
            j.backfilled_at = None
            db.commit()
    return True, f"{version} 작업을 다시 열었습니다(모든 단계 대기)."


def job_progress(job_id: int) -> tuple[int, int]:
    """작업 전체(두 갈래 합)의 (완료 단계 수, 전체 단계 수)."""
    from app.stages import SIDE_APPROVAL, SIDE_SUBMISSION

    done = total = 0
    for side in (SIDE_SUBMISSION, SIDE_APPROVAL):
        d, t = side_progress(job_id, side)
        done += d
        total += t
    return done, total


# 결과물 모음에서 쓰는 상태 키 — 단계 결과 글에 경로가 안 남는 산출물을 여기서 건진다.
_STATE_ARTIFACTS: list[tuple[str, tuple[str, ...]]] = [
    # ②가 처음 만든 고정본(rn_initial_docx_path · rn_sales_initial_path)은 **넣지 않는다**
    # (사용자 확정 2026-08-26). 저장·재생성에 갱신되지 않아 선별 전 내용이 그대로 남는데,
    # 이름이 비슷한 채로 실제 RN 옆에 놓여 있어 그것을 열어 보고 "뺀 항목이 아직 있다"는
    # 오해가 났다(실측 job-21). 파일은 작업 폴더에 그대로 남는다 — 목록에만 안 올린다.
    ("공개 Release Note", ("rn_docx_path", "rn_pdf_path", "rn_final_pdf_path")),
    # 전체 버전 이력본 — 모든 판의 Revision History 를 담은 문서(파일서버 정식 RN 형태).
    # 이번 판의 내용은 공개 RN 과 같다(미반영 제외).
    ("전체 이력 Release Note", ("rn_full_docx_path", "rn_full_pdf_path")),
    # 이전 **버전**의 실제 RN — 참고 대상이 맞다.
    ("참고 · 이전 버전 RN", ("rn_existing_pdf_path",)),
]


def artifacts_for(row: dict) -> list[dict]:
    """이 버전에서 나온 **결과물 전부**. [{group, name, path}] (중복 제거, 실제 존재분만).

    개발 RN(수집 문서) + 파이프라인 상태에 남은 산출물 + 각 단계 실행 결과 파일.
    """
    import os as _os

    from app.stages import SIDE_APPROVAL, SIDE_SUBMISSION, stage_label
    from app.task_store import result_files

    out: list[dict] = []
    seen: set[str] = set()

    def add(group: str, path: str) -> None:
        if not path or path in seen or not _os.path.exists(path):
            return
        seen.add(path)
        out.append({"group": group, "name": _os.path.basename(path), "path": path})

    rel = row.get("release") or {}
    add("개발 Release Note", rel.get("docx_path", ""))
    add("개발 Release Note", rel.get("pdf_path", ""))

    job = row.get("job")
    if job is None:
        return out

    from app.pipeline.state import get_state

    st = get_state(state_key(job["id"])) or {}
    for group, keys in _STATE_ARTIFACTS:
        for k in keys:
            add(group, str(st.get(k) or ""))
    for p in (st.get("newsletter_paths") or {}).values() if isinstance(
            st.get("newsletter_paths"), dict) else (st.get("newsletter_paths") or []):
        add("Newsletter", str(p))

    for side in (SIDE_SUBMISSION, SIDE_APPROVAL):
        for s in get_or_create_stages(job["id"], side):
            label = stage_label(s.stage_key, side)
            for p in result_files(s.result_text or "", s.artifact_path or ""):
                add(label, p)
    return out


def jobs_by_mail() -> dict:
    """{메일 id: 작업} — 메일함에서 '이 메일은 이미 작업에 연결됨'을 알리는 데 쓴다.

    같은 일을 메일함과 /osupdate 양쪽에서 두 번 하지 않도록 하는 표시다.
    """
    out: dict = {}
    for j in list_jobs():
        for col in _SIDE_COL.values():
            if j.get(col):
                out[j[col]] = j
        # 감지 근거 메일도 작업에 붙은 메일이다 — 정리(prune)에서 지워지면
        # 미리보기가 빈 화면이 된다.
        for m in j.get("basis_mails") or []:
            if m.get("mail_id"):
                out.setdefault(m["mail_id"], j)
    return out


def _same_sku(parsed: dict, job: dict) -> bool:
    return (parsed["product"].upper() == str(job.get("product", "")).upper()
            and str(parsed["android"]) == str(job.get("android", "")))


def auto_link() -> dict:
    """캐시된 트리거 메일을 규칙대로 작업에 붙인다. 애매하면 건너뛴다.

    반환 {linked: [설명…], ambiguous: [설명…], no_job: [설명…]}.
    """
    from app.pipeline import fih_mail as F

    linked: list[str] = []
    ambiguous: list[str] = []
    no_job: list[str] = []

    # Submission 을 먼저 붙인다 — Approval 은 같은 빌드지문의 Submission 을 단서로 쓴다.
    mails = _trigger_mails()                   # 최신순
    for side in ("submission", "approval"):
        for d in mails:
            if d["side"] != side or not d["product"]:
                continue
            if d["mail_id"] in _linked_ids(side):
                continue
            who = f"{d['product']} A{d['android']} {side} ({F.received_date(d['received_at'])})"
            jobs = [j for j in list_jobs()
                    if _same_sku(d, j) and not j.get(_SIDE_COL[side])]
            if not jobs:
                no_job.append(who)
                continue

            pick = None
            if side == "approval" and d["fingerprint"]:
                # 같은 빌드지문의 Submission 이 붙어 있는 작업이 있으면 그것으로 확정.
                same = [j for j in jobs
                        if j.get("submission_mail_id")
                        and _fingerprint_of(j["submission_mail_id"]) == d["fingerprint"]]
                if len(same) == 1:
                    pick = same[0]
            if pick is None and len(jobs) == 1:
                pick = jobs[0]
            if pick is None:
                ambiguous.append(who + f" — 후보 {len(jobs)}개")
                continue

            # 같은 SKU 의 미연결 메일이 여럿이면 최신 것이 먼저 붙는다(위 목록이 최신순).
            # 회차가 밀려 있을 수 있으니 몇 건 중 고른 것인지 함께 알린다.
            rest = sum(1 for x in mails
                       if x["side"] == side and _same_sku(x, pick)
                       and x["mail_id"] not in _linked_ids(side))
            ok, msg = link_mail(pick["id"], side, d["mail_id"])
            if not ok:
                ambiguous.append(f"{who} — {msg}")
            else:
                note = f" (미연결 {rest}건 중 최신)" if rest > 1 else ""
                linked.append(f"{who} → {pick['version']}{note}")
    return {"linked": linked, "ambiguous": ambiguous, "no_job": no_job}


# ── 단계 ───────────────────────────────────────────────────────────────────
def get_or_create_stages(job_id: int, side: str) -> list[OsUpdateJobStage]:
    """해당 갈래의 단계 행을 (없으면 만들어) 순서대로 반환."""
    keys = stage_keys_for(side)
    with get_session() as db:
        existing = {
            s.stage_key: s
            for s in db.query(OsUpdateJobStage).filter_by(job_id=job_id, side=side).all()
        }
        created = False
        for key in keys:
            if key not in existing:
                db.add(OsUpdateJobStage(job_id=job_id, side=side, stage_key=key,
                                        status=PENDING))
                created = True
        if created:
            db.commit()
        rows = {
            s.stage_key: s
            for s in db.query(OsUpdateJobStage).filter_by(job_id=job_id, side=side).all()
        }
        return [rows[k] for k in keys if k in rows]


def get_stage(stage_id: int) -> OsUpdateJobStage | None:
    with get_session() as db:
        return db.query(OsUpdateJobStage).filter_by(id=stage_id).first()


def set_stage_status(stage_id: int, status: str, updated_by: str = "") -> None:
    with get_session() as db:
        row = db.query(OsUpdateJobStage).filter_by(id=stage_id).first()
        if row:
            row.status = status
            if updated_by:
                row.updated_by = updated_by
            db.commit()


def set_stage_input(stage_id: int, input_params: str) -> None:
    with get_session() as db:
        row = db.query(OsUpdateJobStage).filter_by(id=stage_id).first()
        if row:
            row.input_params = input_params
            db.commit()


def try_claim_stage(stage_id: int, updated_by: str = "") -> tuple[bool, str]:
    """단계를 '실행 중'으로 선점. 이미 누가 돌리고 있으면 (False, 사유).

    메일 쪽과 같은 방식 — 한 번의 UPDATE 로 선점해 두 사람이 같은 단계를 동시에
    돌리지 못하게 한다(③ 업로드·⑧ 삭제가 두 번 실행되면 곤란하다).
    """
    with get_session() as db:
        changed = (
            db.query(OsUpdateJobStage)
            .filter(OsUpdateJobStage.id == stage_id, OsUpdateJobStage.status != RUNNING)
            .update({"status": RUNNING, "updated_by": updated_by or ""},
                    synchronize_session=False)
        )
        db.commit()
        if changed:
            return True, ""
        row = db.query(OsUpdateJobStage).filter_by(id=stage_id).first()
        who = (row.updated_by if row else "") or "다른 사용자"
        return False, f"{who} 님이 이미 실행 중입니다."


def save_stage_result(stage_id: int, result_text: str, artifact_type: str = "",
                      artifact_path: str = "", updated_by: str = "") -> None:
    """실행 결과를 저장하고 '검수 대기'로 만든다."""
    from app.stages import REVIEW

    with get_session() as db:
        row = db.query(OsUpdateJobStage).filter_by(id=stage_id).first()
        if row:
            row.result_text = result_text
            row.artifact_type = artifact_type
            row.artifact_path = artifact_path
            row.status = REVIEW
            if updated_by:
                row.updated_by = updated_by
            db.commit()


def next_pending_stage(job_id: int, side: str) -> OsUpdateJobStage | None:
    """해당 갈래에서 아직 완료되지 않은 첫 단계(없으면 None)."""
    for s in get_or_create_stages(job_id, side):
        if s.status != DONE:
            return s
    return None


def side_progress(job_id: int, side: str) -> tuple[int, int]:
    """(완료 단계 수, 전체 단계 수) — 목록에서 진행률 표시용."""
    rows = get_or_create_stages(job_id, side)
    return sum(1 for r in rows if r.status == DONE), len(rows)
