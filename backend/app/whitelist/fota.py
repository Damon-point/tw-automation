"""FOTA WhiteList 연동 — 등록(엑셀 업로드)과 조회.

  · POST /view/whitelist/uploadExcelFile  **신규 등록·갱신**(세션 필요). multipart, name="file"
  · POST /v1/whitelist/isExistWhiteList   SN 존재 조회(인증 불필요). ?sn=
  · GET  /view/whitelist/list             목록(세션 필요). HTML 렌더 → 행에서 값 파싱

인증: /view/* 는 폼 로그인 세션(JSESSIONID)이 필요. POST /loginProcess (username/password).

**등록일 변경은 여기 없다** — 웹 API 로는 건별 원자성이 안 나와 FOTA DB 직접 반영으로
옮겼다(fota_db.change_registration_date · 사용자 확정 2026-08-07).

안전장치: 프로덕션 쓰기라 **기본 dry-run**. 실제 전송은 env `WHITELIST_FOTA_DRYRUN=0`.
"""
from __future__ import annotations
from app import shadow as _shadow

import os
import re

import requests

FOTA_BASE = os.getenv(
    "WHITELIST_FOTA_BASE", "https://fota.solution-pointmobile.co.kr"
).rstrip("/")
_TIMEOUT = 25


def _dry_run() -> bool:
    return os.getenv("WHITELIST_FOTA_DRYRUN", "1").strip() not in ("0", "false", "False", "")


# ── 인증 세션 (/view/* 용) ────────────────────────────────────────────────
def login_session(username: str, password: str) -> requests.Session | None:
    """폼 로그인해서 세션(JSESSIONID)을 확보. 실패 시 None.

    /view/whitelist/{list,update,delete} 는 이 세션이 필요하다.
    """
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    try:
        s.get(f"{FOTA_BASE}/login", timeout=_TIMEOUT)
        s.post(f"{FOTA_BASE}/loginProcess",
               data={"username": username, "password": password}, timeout=_TIMEOUT)
    except Exception:  # noqa: BLE001
        return None
    if "JSESSIONID" not in s.cookies:
        return None
    return s


def _session_from_user(user_id: int) -> requests.Session | None:
    """앱에 저장된 FOTA 자격증명으로 로그인 세션을 만든다."""
    from app.integrations.creds import get_web_config

    cfg = get_web_config(user_id, "fota")
    if not cfg or not cfg.username:
        return None
    return login_session(cfg.username, cfg.password)


# ── 조회 ──────────────────────────────────────────────────────────────────
def is_registered(sn: str) -> bool | None:
    """SN 이 FOTA 화이트리스트에 있는가(인증 불필요). 조회 실패 시 None."""
    try:
        r = requests.post(f"{FOTA_BASE}/v1/whitelist/isExistWhiteList",
                          params={"sn": sn}, timeout=_TIMEOUT)
        return bool(r.json().get("data"))
    except Exception:  # noqa: BLE001
        return None


_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_ID_RE = re.compile(r"detail\?id=(\d+)")
_DATE_RE = re.compile(r"20\d\d-\d\d-\d\d")


_API_RE = re.compile(r"\b(\d{2})\b")
_OSTYPE_RE = re.compile(r"\b(White\w+)\b")


def find_records(session: requests.Session, sn: str) -> list[dict]:
    """세션으로 list 를 조회해 그 SN 의 레코드를 찾는다.

    반환: [{id, sn, date, androidApi, osType, partnumber}]. 날짜변경 시 기존 값을 보존하려고
    androidApi·osType 도 함께 파싱한다(행 예: id sn api YYYY-MM-DD osType).
    검색이 부분일치라 정확히 sn 이 든 행만 남긴다.
    """
    try:
        # 검색 필터는 flat(searchColumn/searchKeyword)이 아니라 searchOptions[0].* 형식이어야
        # 서버가 실제로 걸러준다(flat 은 무시되고 최근 목록만 나온다 — 실측 확인).
        html = session.get(
            f"{FOTA_BASE}/view/whitelist/list",
            params={"size": 50,
                    "searchOptions[0].searchColumn": "sn",
                    "searchOptions[0].searchKeyword": sn},
            timeout=_TIMEOUT,
        ).text
    except Exception:  # noqa: BLE001
        return []
    out = []
    for tr in _ROW_RE.findall(html):
        text = re.sub(r"<[^>]+>", " ", tr)
        if sn not in text:
            continue
        idm = _ID_RE.search(tr)
        if not idm:
            continue
        dm = _DATE_RE.search(text)
        apim = _API_RE.search(text)
        osm = _OSTYPE_RE.search(text)
        out.append({
            "id": int(idm.group(1)), "sn": sn,
            "date": dm.group(0) if dm else "",
            "androidApi": apim.group(1) if apim else "",
            "osType": osm.group(1) if osm else "",
            "partnumber": "",
        })
    return out


# ── 신규 등록: 엑셀 업로드 ─────────────────────────────────────────────────
# 화면의 [Excel Upload] 가 쓰는 경로. SN 개수와 무관하게 **항상 이 방식**으로 등록한다
# (사용자 확정 2026-08-07). 열 구성은 실제 사용 파일(04_WhiteList.xlsx)과 같다.
UPLOAD_PATH = "/view/whitelist/uploadExcelFile"
UPLOAD_HEADERS = ("SN", "Android API", "Support end date", "Partnumber", "ostype")


def build_upload_xlsx(devices: list[dict]) -> bytes:
    """등록용 엑셀을 만든다. devices=[{sn,api,endDate,ostype,partnumber}] → xlsx 바이트.

    Support end date 는 **등록일 + 1년** 이 이미 계산된 값(endDate)을 그대로 넣는다.
    Partnumber 는 비워 둔다(실제 사용 파일도 비어 있다).

    셀 타입을 실제 사용 파일(04_WhiteList.xlsx)과 똑같이 맞춘다 — SN·API 는 숫자,
    Support end date 는 날짜다. 서버 파서가 타입별로 값을 꺼내면 문자열 셀에서 깨진다.
    """
    import io as _io
    from datetime import datetime as _dt

    from openpyxl import Workbook

    def _num(v):
        s = str(v or "").strip()
        return int(s) if s.isdigit() else s

    def _date(v):
        s = str(v or "").strip()[:10]
        try:
            return _dt.strptime(s, "%Y-%m-%d")
        except ValueError:
            return s

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(list(UPLOAD_HEADERS))
    for d in devices:
        ws.append([_num(d.get("sn")), _num(d.get("api")), _date(d.get("endDate")),
                   (str(d.get("partnumber") or "") or None), str(d.get("ostype") or "")])
    for c in ws["C"][1:]:
        c.number_format = "yyyy-mm-dd hh:mm:ss"
    buf = _io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload_whitelist_excel(user_id: int, devices: list[dict],
                           allow_write: bool | None = None) -> dict:
    """엑셀을 만들어 화면과 같은 경로로 업로드한다. 반환 {ok,total,dryRun,msg,xlsx}.

    dry-run 이면 파일만 만들고 보내지 않는다 — 무엇이 올라갈지 먼저 확인할 수 있다.
    업로드 자체는 결과를 HTML 로 돌려주므로, **성공 여부는 등록 확인(verify)으로 판단**한다.
    """
    devices = [d for d in devices if d.get("sn")]

    def _out(ok, dry, msg, data=b""):
        # success/details 는 기존 일괄 처리 화면(_run_items)이 읽는 형식에 맞춘 것.
        # 파일 하나가 통째로 들어가거나 통째로 안 들어가므로 전건 동일 결과다.
        return {"ok": ok, "total": len(devices), "dryRun": dry, "msg": msg, "xlsx": data,
                "success": len(devices) if ok else 0,
                "details": [{"sn": d["sn"], "ok": ok, "msg": msg} for d in devices]}

    if not devices:
        return _out(False, True, "등록할 단말이 없습니다.")
    dry = _dry_run() if allow_write is None else (not allow_write)
    if not dry and _shadow.blocked("fota_whitelist", "upload_excel", devices=len(devices)):
        dry = True
    data = build_upload_xlsx(devices)
    if dry:
        return _out(True, True, f"미리보기 — 엑셀 {len(devices)}행 생성(업로드 안 함)", data)

    session = _session_from_user(user_id)
    if session is None:
        return _out(False, False, "FOTA 로그인 실패([설정]에서 FOTA 계정 확인)", data)
    try:
        r = session.post(
            f"{FOTA_BASE}{UPLOAD_PATH}",
            files={"file": ("whitelist.xlsx", data,
                            "application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet")},
            timeout=_TIMEOUT * 4,
        )
        ok = 200 <= r.status_code < 400
        return _out(ok, False,
                    f"업로드 {r.status_code}" if ok
                    else f"업로드 실패 {r.status_code}: {r.text[:150]}", data)
    except Exception as e:  # noqa: BLE001
        return _out(False, False, f"업로드 오류: {e}", data)


#: 웹 목록으로 한 대씩 확인할 때의 상한. 웹은 SN 하나에 조회 한 번이라 대량 건에서는
#: 사실상 끝나지 않는다 — 2,652대 등록일 변경 건에서 ② 확인이 스피너만 돌았다
#: (사용자 신고 2026-09-16). 평소에는 DB 한 번으로 읽으므로 이 길로 오는 일이 드물다.
WEB_VERIFY_MAX = 200


def _row_blank(sn: str) -> dict:
    return {"sn": sn, "found": False, "date": "", "osType": "",
            "androidApi": "", "diffs": [], "ok": False}


def _tally(rows: list[dict], error: str = "") -> dict:
    matched = sum(1 for r in rows if r["ok"])
    missing = sum(1 for r in rows if not r["found"])
    return {"ok": bool(rows) and matched == len(rows), "total": len(rows),
            "matched": matched, "missing": missing,
            "mismatched": len(rows) - matched - missing, "rows": rows, "error": error}


def _diff_row(row: dict, it: dict) -> dict:
    """기대값과 대조해 diffs 를 채운다. 기대값이 빈 항목은 보지 않는다."""
    for key, got, label in (("endDate", row["date"], "등록일"),
                            ("ostype", row["osType"], "osType"),
                            ("api", row["androidApi"], "androidApi")):
        want = str(it.get(key) or "").strip()
        if want and want != str(got or "").strip():
            row["diffs"].append(f"{label} 기대 {want} · 실제 {got or '(없음)'}")
    row["ok"] = row["found"] and not row["diffs"]
    return row


def verify_registration_db(items: list[dict]) -> dict:
    """**FOTA DB 를 한 번 읽어** 기대값과 대조한다(웹 목록을 SN 마다 긁지 않는다).

    웹 목록도 이 DB 를 그려 준다 — 보는 자리가 다를 뿐 값은 같다. 등록일 변경은 아예
    이 DB 에 직접 쓰므로 확인도 같은 자리를 읽는 것이 맞다. 2,652대가 4초면 끝난다.

    예외는 삼키지 않는다 — 부르는 쪽이 웹 경로로 물러설지 정한다.
    """
    from app.whitelist import fota_db as _DB

    rows, _missing = _DB.lookup([str(i["sn"]).strip() for i in items])
    by_sn = {r.sn: r for r in rows}
    out = []
    for it in items:
        sn = str(it["sn"]).strip()
        r = by_sn.get(sn)
        row = _row_blank(sn)
        if r is None:
            row["diffs"].append("등록 안 됨")
            out.append(row)
            continue
        row.update(found=True,
                   date=str(r.support_end_date or ""),
                   osType=str(r.os_type or ""),
                   androidApi=str(r.android_api or ""))
        out.append(_diff_row(row, it))
    return _tally(out)


def verify_registration(user_id: int, items: list[dict]) -> dict:
    """등록·변경 결과를 **다시 읽어** 기대값과 대조한다.

    보냈다는 응답만 믿지 않는다 — 실제로 그 값으로 들어갔는지 확인해야 회신을 보낼 수
    있다(사용자 확정 2026-08-06). 신규 등록과 등록일 변경 모두 같은 함수를 쓴다.

    읽는 자리는 **FOTA DB 를 먼저** 본다. 예전에는 웹 목록을 SN 하나에 한 번씩 긁어서,
    2,652대짜리 등록일 변경 건에서 ② 확인이 사실상 끝나지 않았다(사용자 신고
    2026-09-16). DB 는 같은 값을 한 번의 조회로 준다 — 2,652대가 4초다.
    DB 에 못 붙으면 예전 웹 경로로 물러서되, 대량이면 긁지 않고 사유를 돌려준다.

    items: [{sn, endDate?, ostype?, api?}] — 비워 둔 항목은 대조하지 않는다
           (등록일 변경은 endDate 만 확인하고 osType·api 는 서버 값 보존이라 안 본다).
    반환: {ok, total, matched, missing, mismatched, rows, error}
          rows: [{sn, found, date, osType, androidApi, diffs:[...], ok}]
    """
    items = [i for i in (items or []) if (i or {}).get("sn")]
    if not items:
        return _tally([], "확인할 SN 이 없습니다.")

    try:
        return verify_registration_db(items)
    except Exception as _e:  # noqa: BLE001  (DB 가 막히면 웹으로 물러선다)
        _why = f"{type(_e).__name__}: {_e}"
    if len(items) > WEB_VERIFY_MAX:
        # 웹은 SN 하나에 조회 한 번이라 여기서 긁기 시작하면 끝나지 않는다.
        return _tally(
            [_row_blank(str(i["sn"]).strip()) for i in items],
            f"FOTA DB 를 읽지 못했습니다({_why}). 대상이 {len(items):,}대라 웹 목록으로는 "
            f"확인하지 않습니다(상한 {WEB_VERIFY_MAX}대) — DB 접속을 확인하세요.")

    sess = _session_from_user(user_id)
    if sess is None:
        return _tally([_row_blank(str(i["sn"]).strip()) for i in items],
                      "FOTA 로그인 실패 — 확인할 수 없습니다.")

    from concurrent.futures import ThreadPoolExecutor

    def _one(it: dict) -> dict:
        sn = str(it["sn"]).strip()
        recs = find_records(sess, sn)
        row = _row_blank(sn)
        if not recs:
            row["diffs"].append("등록 안 됨")
            return row
        # 같은 SN 이 여러 행이면 가장 최근(id 큰 것)이 유효한 값이다.
        r = max(recs, key=lambda x: x.get("id") or 0)
        row.update(found=True, date=r.get("date", ""), osType=r.get("osType", ""),
                   androidApi=str(r.get("androidApi", "")))
        return _diff_row(row, it)

    with ThreadPoolExecutor(max_workers=min(10, len(items))) as ex:
        return _tally(list(ex.map(_one, items)))


def fetch_whitelist_list(user_id: int, size: int = 500) -> tuple[list[dict], str]:
    """FOTA-DB 열람용 — 화이트리스트 최근 목록을 표로. (rows, error).

    list HTML 의 각 행에서 id·sn·androidApi·supportEndDate·osType 을 파싱한다.
    excel_files._section 과 같은 시그니처(fetch_fn(user_id))로 맞춘다.
    """
    session = _session_from_user(user_id)
    if session is None:
        return [], "FOTA 로그인 실패([설정]에서 FOTA 계정을 확인하세요)."
    try:
        html = session.get(
            f"{FOTA_BASE}/view/whitelist/list",
            params={"size": size, "page": 1}, timeout=_TIMEOUT,
        ).text
    except Exception as e:  # noqa: BLE001
        return [], f"조회 오류: {e}"
    out = []
    for tr in _ROW_RE.findall(html):
        idm = _ID_RE.search(tr)
        if not idm:
            continue
        text = re.sub(r"<[^>]+>", " ", tr)
        cells = [c for c in re.split(r"\s{2,}|\n", text) if c.strip()]
        snm = re.search(r"(?<![A-Za-z0-9])([A-Za-z0-9]{10})(?![A-Za-z0-9])", text)
        dm = _DATE_RE.search(text)
        apim = _API_RE.search(text)
        osm = _OSTYPE_RE.search(text)
        out.append({
            "id": int(idm.group(1)),
            "sn": snm.group(1) if snm else "",
            "androidApi": apim.group(1) if apim else "",
            "supportEndDate": dm.group(0) if dm else "",
            "osType": osm.group(1) if osm else "",
        })
    return out, ""
