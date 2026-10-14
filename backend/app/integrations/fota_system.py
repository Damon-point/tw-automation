"""FOTA System(fota.solution-pointmobile.co.kr) 등록 연동.

OsInfo/DownloadPath 를 FOTA 시스템에 등록한다. 저장 API(recon 확정):
  · 로그인: POST /loginProcess (username/password, form) — 세션 쿠키 JSESSIONID
  · OsInfo 저장:      POST /view/osinfo/add       (form-urlencoded)
  · DownloadPath 저장: POST /view/downloadpath/add (form-urlencoded)
  · 카운터(조회): POST /view/osinfo/latestOsVersion {osType} → {latestVersion}

**안전장치**: `_ALLOW_FOTA_WRITES=False` 인 동안 저장 계열(/add·/update·/delete) POST 를
절대 실행하지 않는다(dry-run). dry-run 은 로그인 + latestOsVersion(둘 다 읽기전용)만 서버에
보내고, '무엇을 저장할지'를 미리보기로만 돌려준다. 실제 등록은 사용자 명시 승인 시에만.
"""
from __future__ import annotations
from app import shadow as _shadow

import re

import requests

from app.integrations.creds import get_web_config

# 실제 저장(/add·/update·/delete) 허용 게이트 — 승인 시에만 True.
_ALLOW_FOTA_WRITES = False

_LOGIN = "/loginProcess"
_OSINFO_ADD = "/view/osinfo/add"
_DP_ADD = "/view/downloadpath/add"
_LATEST = "/view/osinfo/latestOsVersion"

# Excel 행(고정 순서) → FOTA 폼 필드
_OSINFO_KEYS = [
    "osType", "version", "osVersion", "description", "releaseNote",
    "releaseNoteUrl", "updateCode", "osResetCode", "scannerResetCode",
]
_DP_KEYS = ["osType", "azure", "osVersion", "downloadUrl", "md5", "downloadType", "fileNm", "size"]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    s.verify = False
    try:
        requests.packages.urllib3.disable_warnings()  # noqa
    except Exception:  # noqa: BLE001
        pass
    return s


#: 제품 소개 페이지로 보이는가 — 내려받기 링크(Dropbox 등)를 가려내는 데 쓴다.
_SITE = "pointmobile"


def release_note_url(osinfo_rows, os_type: str, product: str) -> str:
    """OsInfo 의 Release_Note_URL — **그 제품의 소개 페이지**.

    단말에서 사용자가 누르는 링크다. 문서를 바로 내려받게 하는 자리가 아니다
    (사용자 지적 2026-10-07: "dropbox download url이 들어가면 안되는데 … 원래 기존처럼
    그냥 회사 홈페이지의 제품 페이지로 가게해야지").

    주소는 **제품마다 다르고 조합으로 맞출 수 없다**(실측 2026-10-07, 기존 OsInfo
    4597행):

        PM85 · PM95(A15) · TR54(A17)   /en/products-mobilecomputers/{제품}
        PM45                           /en/products-ruggedsmartphone/PM45
        PM67 · PM95(A13) · PM84P       /kr/product/view.php?idx=30
        PM68 · PM84E                   /en/product/view.php?idx=14

    그래서 **그 OS_Type 의 지난 회차가 쓰던 주소를 그대로 이어받는다.** 제품 소개
    페이지로 보이지 않는 값(내려받기 링크 등)은 건너뛴다 — 한 번 잘못 들어간 값이
    다음 회차로 번지지 않게. 이어받을 것이 없으면 가장 흔한 형태로 조합한다.
    """
    best, best_osv = "", -1
    for r in (osinfo_rows or []):
        if str(r.get("os_type") or "") != os_type:
            continue
        u = str(r.get("release_url") or "").strip()
        if _SITE not in u.lower():
            continue                            # 내려받기 링크 등 — 이어받지 않는다
        try:
            osv = int(r.get("os_version") or 0)
        except (TypeError, ValueError):
            osv = 0
        if osv >= best_osv:
            best, best_osv = u, osv
    if best:
        return best
    return f"https://pointmobile.com/en/products-mobilecomputers/{product}"


def dp_size(nbytes) -> str:
    """DownloadPath 의 size 칸에 넣을 값 — **바이트에서 뒤 세 자리를 뗀다**.

    FOTA 쪽 칸 이름은 그냥 'size' 로 단위 표기가 없다. 실제로 쌓여 있는 값이 관례다 —
    1.5GB 급 PFU 가 1529637, DIFF 가 105621 처럼 **일곱 자리**다(실측 2026-10-07:
    기존 4597행 중 7자리가 1046행으로 가장 많다). 우리가 쓰던 더미 값도
    FULL 1234000 · DIFF 1000 으로 같은 자리수다.

    그런데 실제 행만 ③ Dropbox 업로드가 준 **바이트 그대로** 나가고 있었다
    (86.07C8 FULL = 1616926982). 그래서 같은 칸에 단위가 뒤섞였다
    (사용자 지시 2026-10-07 "단위가 mb여서 뒤에 3자리 떼고 올라가야됩니다").

    읽을 수 없는 값은 빈 문자열로 둔다 — 0 을 넣어 '크기 0' 으로 보이게 하지 않는다.
    """
    s = str(nbytes if nbytes is not None else "").strip()
    if not s:
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return ""
    return str(int(digits) // 1000)


def build_osinfo_payload(row) -> dict:
    return {k: ("" if v is None else v) for k, v in zip(_OSINFO_KEYS, list(row))}


def build_downloadpath_payload(row) -> dict:
    return {k: ("" if v is None else v) for k, v in zip(_DP_KEYS, list(row))}


def _login(user_id: int):
    """(session, base_url, cfg, error). 실패 시 session=None."""
    cfg = get_web_config(user_id, "fota")
    if not cfg or not cfg.base_url:
        return None, "", None, "FOTA 자격증명 미등록 — /settings 에서 'FOTA System' 등록 필요."
    base = cfg.base_url.rstrip("/")
    s = _session()
    try:
        s.get(base + "/login", timeout=20)
        r = s.post(base + _LOGIN,
                   data={"username": cfg.username, "password": cfg.password},
                   timeout=20, allow_redirects=True)
    except Exception as e:  # noqa: BLE001
        return None, base, cfg, f"FOTA 접속 실패: {e}"
    if "/login" in r.url or "error" in r.url:
        return None, base, cfg, "FOTA 로그인 실패(자격증명 확인)."
    return s, base, cfg, ""


def latest_os_version(session, base: str, os_type: str) -> int | None:
    """읽기전용: 해당 osType 의 FOTA 최신 OS_Version 정수. 실패/미인식 시 None."""
    try:
        r = session.post(base + _LATEST, data={"osType": os_type}, timeout=20)
        if r.ok:
            v = (r.json() or {}).get("latestVersion")
            return int(v) if v is not None else None
    except Exception:  # noqa: BLE001
        return None
    return None


def get_latest_os_version(user_id: int, os_type: str) -> tuple[int | None, str]:
    """로그인 후 osType 의 FOTA 최신 OS_Version 을 조회한다. (version|None, error).

    - error != '' : 로그인/접속 실패.
    - version=None, error='' : 해당 osType 이 FOTA 에 없음(신규/미등록 → 상위에서 보류).
    - version=int : 존재. 다음 카운터 = version + 1.
    """
    session, base, cfg, err = _login(user_id)
    if err:
        return None, err
    return latest_os_version(session, base, os_type), ""


# ── FOTA 목록(HTML DataTable) 조회·파싱 — 누적 엑셀 대체(열람/대조용) ──────────
_TAG_RE = re.compile(r"<[^>]+>")
_TBODY_RE = re.compile(r"<tbody[^>]*>(.*?)</tbody>", re.S)
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)

# 서버렌더 <td> 위치 → 의미(2026-07-15 recon). checkbox=[0].
_OSINFO_COLS = [
    ("id", 1), ("os_type", 2), ("version", 3), ("os_version", 4),
    ("description", 5), ("release_note", 6), ("release_url", 7),
    ("update_code", 10), ("osreset_code", 11), ("scannerreset_code", 12),
]
_DP_COLS = [
    ("id", 1), ("os_type", 2), ("azure", 3), ("os_version", 4),
    ("download_url", 5), ("md5", 6), ("download_type", 7), ("filename", 8), ("size", 9),
]


def _cell_text(td: str) -> str:
    t = _TAG_RE.sub(" ", td)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&nbsp;", " ")):
        t = t.replace(a, b)
    return " ".join(t.split()).strip()


def _parse_list(html: str, colmap) -> list[dict]:
    tb = _TBODY_RE.search(html or "")
    if not tb:
        return []
    out: list[dict] = []
    for tr in _TR_RE.findall(tb.group(1)):
        cells = [_cell_text(td) for td in _TD_RE.findall(tr)]
        if not cells:
            continue
        out.append({key: (cells[idx] if idx < len(cells) else "") for key, idx in colmap})
    return out


def _fetch_rows(session, base: str, path: str, colmap, size: int) -> tuple[list[dict], str]:
    """로그인된 세션으로 목록 한 번 조회. 두 목록을 볼 때 로그인을 두 번 하지 않으려고 뺐다."""
    try:
        r = session.get(base + path, params={"size": size, "page": 1}, timeout=90)
    except Exception as e:  # noqa: BLE001
        return [], f"FOTA 조회 실패: {e}"
    if not r.ok:
        return [], f"FOTA 조회 HTTP {r.status_code}"
    return _parse_list(r.text, colmap), ""


def _fetch_list(user_id: int, path: str, colmap, size: int) -> tuple[list[dict], str]:
    session, base, cfg, err = _login(user_id)
    if err:
        return [], err
    return _fetch_rows(session, base, path, colmap, size)


def fetch_osinfo_list(user_id: int, size: int = 100000) -> tuple[list[dict], str]:
    """FOTA OsInfo 전체 목록(누적 엑셀 대체). (rows, error)."""
    return _fetch_list(user_id, "/view/osinfo/list", _OSINFO_COLS, size)


def fetch_downloadpath_list(user_id: int, size: int = 100000) -> tuple[list[dict], str]:
    """FOTA DownloadPath 전체 목록(누적 엑셀 대체). (rows, error)."""
    return _fetch_list(user_id, "/view/downloadpath/list", _DP_COLS, size)


def _vnum(s) -> str:
    """대조용 버전 키 — '95.07 C1 (Jul 10 2026)' → '95.07C1'.

    날짜는 떼고(같은 회차인데 날짜만 다른 경우가 있다), 공백도 없앤다
    (FOTA 는 '95.07 C1', 우리 예전 표기는 '95.07C1' 이라 그대로 비교하면 못 잡는다).
    """
    return str(s or "").split("(")[0].replace(" ", "").strip()


def plan_registration(user_id: int, osinfo_rows=None, dp_rows=None,
                      size: int = 100000) -> dict:
    """등록 전 대조 — **무엇이 새 것이고 무엇이 이미 있는지** 가른다.

    되돌릴 수 없는 쓰기라, 통째로 막거나 통째로 보내는 대신 행 단위로 나눈다.
    OsInfo 는 회차당 한 줄이어야 하므로 (OS_Type, Version 번호)로 본다.
    DownloadPath 는 같은 회차에 Dropbox 링크가 여럿인 게 정상이라(실측 PM95GMS15
    osv 102 FULL 3행) **Download_URL 까지** 키에 넣는다 — URL 을 빼면 정상적인 다른
    링크를 중복으로 오판해 빠뜨린다.

    반환 {ok, error, new_osinfo, dup_osinfo, new_dp, dup_dp}
      · new_osinfo/new_dp: 보낼 행(원본 형식 그대로)
      · dup_osinfo/dup_dp: [(보내려던 행, 이미 있는 행)]
    """
    out = {"ok": True, "error": "", "new_osinfo": [], "dup_osinfo": [],
           "new_dp": list(dp_rows or []), "dup_dp": []}
    session, base, cfg, err = _login(user_id)
    if err:
        out["ok"] = False
        out["error"] = err
        out["new_osinfo"] = list(osinfo_rows or [])
        return out

    if osinfo_rows:
        oi_all, e1 = _fetch_rows(session, base, "/view/osinfo/list", _OSINFO_COLS, size)
        if e1:
            out["ok"] = False
            out["error"] = e1
            out["new_osinfo"] = list(osinfo_rows)
            return out
        have = {}
        for r in oi_all:
            have.setdefault(
                (str(r.get("os_type", "")).strip(), _vnum(r.get("version"))), r)
        new_oi, dup_oi = [], []
        for row in osinfo_rows:
            m = have.get((str(row[0]).strip(), _vnum(row[1])))
            (dup_oi.append((list(row), m)) if m else new_oi.append(list(row)))
        out["new_osinfo"], out["dup_osinfo"] = new_oi, dup_oi

    if dp_rows:
        dp_all, e2 = _fetch_rows(session, base, "/view/downloadpath/list", _DP_COLS, size)
        if e2:
            out["ok"] = False
            out["error"] = e2
            return out
        have_dp = {}
        for r in dp_all:
            have_dp.setdefault(
                (str(r.get("os_type", "")).strip(), str(r.get("os_version", "")).strip(),
                 str(r.get("download_url", "")).strip()), r)
        new_dp, dup_dp = [], []
        for row in dp_rows:
            m = have_dp.get((str(row[0]).strip(), str(row[2]).strip(), str(row[3]).strip()))
            (dup_dp.append((list(row), m)) if m else new_dp.append(list(row)))
        out["new_dp"], out["dup_dp"] = new_dp, dup_dp
    return out


def _post_save(session, url: str, payload: dict) -> str:
    """실제 저장 POST(게이트 통과 시에만 호출). 결과 문자열."""
    try:
        r = session.post(url, data=payload, timeout=25)
        try:
            j = r.json()
        except Exception:  # noqa: BLE001
            return f"HTTP {r.status_code} (비 JSON 응답)"
        return f"{j.get('status', '?')} — {j.get('message', '')}".strip()
    except Exception as e:  # noqa: BLE001
        return f"요청 실패: {e}"


def register(user_id: int, osinfo_row=None, downloadpath_rows=None,
             allow_write: bool | None = None) -> dict:
    """FOTA 등록(기본 dry-run). allow_write=None 이면 모듈 게이트를 따른다.

    osinfo_row 를 비워 두면 DownloadPath 만 보낸다 — OsInfo 는 이미 올렸고 Dropbox
    업로드가 늦어 링크만 나중에 추가하는 경우가 있다.

    반환 report: {mode, ok, lines[], requests[(method_url, payload)]}
    dry-run 은 저장 POST 를 실행하지 않고 요청 미리보기만 담는다.
    """
    write = _ALLOW_FOTA_WRITES if allow_write is None else bool(allow_write)
    if write and _shadow.blocked("fota_web", "register", user_id=user_id, osinfo=str(locals().get("osinfo_row") or locals().get("osinfo_rows")),
                                 downloadpath=str(downloadpath_rows)):
        write = False
    report = {"mode": "WRITE" if write else "DRY-RUN", "ok": True, "lines": [], "requests": []}

    session, base, cfg, err = _login(user_id)
    if err:
        report["ok"] = False
        report["lines"].append("✘ " + err)
        return report
    report["lines"].append(f"✔ 로그인 OK ({cfg.username})")

    if osinfo_row:
        oi = build_osinfo_payload(osinfo_row)

        # 카운터 교차검증(읽기전용)
        lv = latest_os_version(session, base, str(oi.get("osType", "")))
        if lv is not None:
            report["lines"].append(
                f"✔ latestOsVersion({oi['osType']})={lv} → 등록 osVersion={lv + 1} "
                f"(Excel 계산={oi.get('osVersion')})"
            )
            if str(lv + 1) != str(oi.get("osVersion")):
                report["lines"].append(
                    f"  ⚠ Excel osVersion({oi.get('osVersion')}) ≠ FOTA+1({lv + 1}) — 확인 필요"
                )
        else:
            report["lines"].append(f"· latestOsVersion 조회 실패/미인식 osType({oi.get('osType')})")

        # OsInfo 저장 요청
        report["requests"].append(("POST " + _OSINFO_ADD, oi))
        if write:
            res = _post_save(session, base + _OSINFO_ADD, oi)
            report["lines"].append("→ OsInfo add: " + res)
            if "success" not in res.lower():
                report["ok"] = False

    # DownloadPath 저장 요청(있으면)
    for dr in (downloadpath_rows or []):
        dp = build_downloadpath_payload(dr)
        report["requests"].append(("POST " + _DP_ADD, dp))
        if write:
            res = _post_save(session, base + _DP_ADD, dp)
            report["lines"].append("→ DownloadPath add: " + res)
            if "success" not in res.lower():
                report["ok"] = False

    return report


def osinfo_index(session, base: str) -> tuple[dict, str]:
    """FOTA 의 OsInfo 를 (osType, osVersion) → version 으로 한 번에 읽는다."""
    rows, err = _fetch_rows(session, base, "/view/osinfo/list", _OSINFO_COLS, 100000)
    if err:
        return {}, err
    return {(str(r.get("os_type") or ""), str(r.get("os_version") or "")):
            str(r.get("version") or "") for r in rows}, ""


def verify_dp_targets(session, base: str, dp_rows) -> tuple[bool, list[str]]:
    """보낼 DownloadPath 행이 **매달릴 OsInfo 가 FOTA 에 있는지** 본다.

    DownloadPath 는 osType+osVersion 으로 OsInfo 에 매달리는 행이다. 그 판이 없으면
    FOTA 가 받아 주지 않는다 — '듀록 오루' 만 반복된다.

    예전에는 **보낼 OsInfo 가 있을 때만** 확인했다. 그런데 OsInfo 가 '이미 듀록됨' 으로
    전부 걸러지면 OsInfo 0건 · DownloadPath 8건을 보내게 되고, 그럴 넌 확인을
    건넌뛰었다(실제 2026-10-07 job-24: 회차 표기가 86.07 (Jun 19 2026) 이라 기존
    osv 107 과 거치어 OsInfo 가 전부 중복으로 걸러졌고, osv 108 은 만들어진 적이
    없는데 DownloadPath 만 날아갔다).
    """
    want = sorted({(str(p.get("osType") or ""), str(p.get("osVersion") or ""))
                   for p in (build_downloadpath_payload(r) for r in (dp_rows or []))})
    if not want:
        return True, []
    have, err = osinfo_index(session, base)
    if err:
        return False, ["✘ OsInfo 조회 실패(DownloadPath 대상 확인): " + err]
    ok, lines = True, []
    for ot, osv in want:
        if (ot, osv) in have:
            lines.append(f"✔ 대상 확인: {ot} osv {osv} — {have[(ot, osv)]}")
        else:
            ok = False
            lines.append(f"✘ 대상 없음: {ot} osv {osv} 에 OsInfo 가 없습니다 — "
                         "DownloadPath 는 그 판에 매달립니다")
    return ok, lines


def verify_osinfo_rows(session, base: str, osinfo_rows) -> tuple[bool, list[str]]:
    """예전 쓰임 그대로 — (모두일치, 로그줄). 행 단위 결과는 verify_osinfo_detail."""
    ok, lines, _c = verify_osinfo_detail(session, base, osinfo_rows)
    return ok, lines


def verify_osinfo_detail(session, base: str,
                         osinfo_rows) -> tuple[bool, list[str], list[tuple]]:
    """보낸 OsInfo 행이 **실제로 그 값으로 들어갔는지** 목록을 다시 읽어 대조한다.

    저장 응답이 HTML 이라 'success' 문자열만으로는 무엇이 들어갔는지 알 수 없다.
    DownloadPath 는 OsInfo 가 있어야 붙으므로, 여기서 막지 못하면 **없는 판에 파일
    경로만 등록되는 상태**가 만들어진다. 반환 (모두일치, 로그줄).
    """
    want = []
    for row in (osinfo_rows or []):
        p = build_osinfo_payload(row)
        want.append((str(p.get("osType") or ""), str(p.get("osVersion") or ""),
                     str(p.get("version") or "")))
    if not want:
        return True, [], []
    have, err = osinfo_index(session, base)
    if err:
        return False, ["✘ OsInfo 확인 조회 실패: " + err], []
    ok, lines, confirmed = True, [], []
    for ot, osv, ver in want:
        got = have.get((ot, osv))
        if got is None:
            ok = False
            lines.append(f"✘ 확인: {ot} osv {osv} 이 목록에 없습니다(등록 안 됨)")
        elif _vnum(got) != _vnum(ver):
            ok = False
            lines.append(f"✘ 확인: {ot} osv {osv} 버전 기대 {ver!r} · 실제 {got!r}")
        else:
            lines.append(f"✔ 확인: {ot} osv {osv} — {got}")
            confirmed.append((ot, osv, got))
    return ok, lines, confirmed


def register_rows(user_id: int, osinfo_rows, downloadpath_rows=None,
                  allow_write: bool | None = None, *, verify: bool = True) -> dict:
    """여러 OsInfo + DownloadPath 행을 한 세션으로 등록(기본 dry-run).

    DQA FOTA 테스트처럼 한 요청이 여러 os_version(from/to)을 만드는 경우에 쓴다.
    각 행이 자체 osType/osVersion 을 가지므로 카운터 교차검증 없이 순서대로 보낸다.

    **OsInfo 가 다 들어가야 DownloadPath 를 보낸다.** DownloadPath 는 OsInfo 의
    osType+osVersion 에 매달리는 행이라, OsInfo 가 없으면 붙을 데가 없다. 예전에는
    OsInfo 가 실패해도 계속 보내서 '판은 없는데 파일 경로만 있는' 상태가 될 수 있었다.
    write 모드에서는 보낸 뒤 목록을 **다시 읽어** 확인하고(verify=True), 확인까지
    통과해야 DownloadPath 로 넘어간다.

    반환 report: {mode, ok, lines[], requests[(method_url, payload)],
                  osinfo_ok, dp_sent, skipped,
                  osinfo_confirmed[(osType, osVersion, version)], dp_ok[(osType,
                  osVersion, downloadType)]}

    **osinfo_confirmed 와 dp_ok 가 '실제로 들어간 것'이다.** 보냈다는 응답만으로는
    무엇이 저장됐는지 알 수 없어서(저장 응답이 HTML 이다) 목록을 되읽어 맞춰 본 결과만
    담는다. 기록(FotaEntry)은 이 두 값으로 남긴다 — 계획을 적어 두면 등록이 막혀도
    '등록됨' 으로 보인다(실측 2026-10-07 job-24).
    """
    write = _ALLOW_FOTA_WRITES if allow_write is None else bool(allow_write)
    if write and _shadow.blocked("fota_web", "register", user_id=user_id, osinfo=str(locals().get("osinfo_row") or locals().get("osinfo_rows")),
                                 downloadpath=str(downloadpath_rows)):
        write = False
    report = {"mode": "WRITE" if write else "DRY-RUN", "ok": True, "lines": [],
              "requests": [], "osinfo_ok": True, "dp_sent": False, "skipped": "",
              "osinfo_confirmed": [], "dp_ok": []}

    session, base, cfg, err = _login(user_id)
    if err:
        report["ok"] = False
        report["osinfo_ok"] = False
        report["lines"].append("✘ " + err)
        return report
    report["lines"].append(f"✔ 로그인 OK ({cfg.username})")

    # ① OsInfo
    for row in (osinfo_rows or []):
        oi = build_osinfo_payload(row)
        report["requests"].append(("POST " + _OSINFO_ADD, oi))
        if write:
            res = _post_save(session, base + _OSINFO_ADD, oi)
            report["lines"].append(f"→ OsInfo add(osv {oi.get('osVersion')}): " + res)
            if "success" not in res.lower():
                report["ok"] = False
                report["osinfo_ok"] = False

    # ② OsInfo 되읽기 확인 — 보냈다는 응답만 믿지 않는다.
    if write and report["osinfo_ok"] and verify and osinfo_rows:
        v_ok, v_lines, v_conf = verify_osinfo_detail(session, base, osinfo_rows)
        report["lines"].extend(v_lines)
        report["osinfo_confirmed"] = v_conf
        if not v_ok:
            report["ok"] = False
            report["osinfo_ok"] = False

    # ③ DownloadPath — OsInfo 가 성립한 뒤에만.
    if write and not report["osinfo_ok"]:
        report["skipped"] = ("OsInfo 등록/확인이 끝나지 않아 DownloadPath 를 보내지 "
                             "않았습니다 — OsInfo 를 먼저 바로잡으세요.")
        report["lines"].append("■ " + report["skipped"])
        return report

    # **매달릴 판이 있는지 않미 본다.** 보낼 OsInfo 가 없어도 건너뛰지 않는다 —
    # OsInfo 가 '이미 듀록됨' 으로 전부 걸러진 경우가 바로 그 구먹이었다.
    if write and downloadpath_rows:
        t_ok, t_lines = verify_dp_targets(session, base, downloadpath_rows)
        report["lines"].extend(t_lines)
        if not t_ok:
            report["ok"] = False
            report["skipped"] = (
                "DownloadPath 가 매달릴 OsInfo 가 FOTA 에 없어 보내지 않았습니다 — "
                "그 판(osType·osVersion)을 먼저 듀록하세요. 회차 표기가 기존 판과 같아 "
                "'이미 듀록됨' 으로 걸러졌다면 표기를 바로잡아야 합니다.")
            report["lines"].append("■ " + report["skipped"])
            return report

    for row in (downloadpath_rows or []):
        dp = build_downloadpath_payload(row)
        report["requests"].append(("POST " + _DP_ADD, dp))
        if write:
            res = _post_save(session, base + _DP_ADD, dp)
            report["dp_sent"] = True
            report["lines"].append(
                f"→ DownloadPath add(osv {dp.get('osVersion')} {dp.get('downloadType')}): " + res)
            if "success" not in res.lower():
                report["ok"] = False
            else:
                report["dp_ok"].append((str(dp.get("osType") or ""),
                                        str(dp.get("osVersion") or ""),
                                        str(dp.get("downloadType") or "")))
    return report


def register_downloadpath_only(user_id: int, osinfo_rows, downloadpath_rows,
                               allow_write: bool | None = None) -> dict:
    """DownloadPath 만 보낸다 — **OsInfo 가 FOTA 에 실제로 있는지 먼저 확인하고**.

    OsInfo 등록과 DownloadPath 등록을 사람이 따로 누르는 화면을 위한 것이다. 팝업을
    닫았다 다시 열어도 '앞 단계가 끝났는지'를 기억에 기대지 않고 FOTA 에서 직접 읽어
    판단한다.
    """
    write = _ALLOW_FOTA_WRITES if allow_write is None else bool(allow_write)
    if write and _shadow.blocked("fota_web", "register", user_id=user_id, osinfo=str(locals().get("osinfo_row") or locals().get("osinfo_rows")),
                                 downloadpath=str(downloadpath_rows)):
        write = False
    report = {"mode": "WRITE" if write else "DRY-RUN", "ok": True, "lines": [],
              "requests": [], "osinfo_ok": True, "dp_sent": False, "skipped": "",
              "osinfo_confirmed": [], "dp_ok": []}

    session, base, cfg, err = _login(user_id)
    if err:
        report["ok"] = False
        report["osinfo_ok"] = False
        report["lines"].append("✘ " + err)
        return report
    report["lines"].append(f"✔ 로그인 OK ({cfg.username})")

    v_ok, v_lines, v_conf = verify_osinfo_detail(session, base, osinfo_rows)
    report["lines"].extend(v_lines)
    report["osinfo_confirmed"] = v_conf
    # OsInfo 행을 안 받았어도(또는 비어 있어도) **보낼 DownloadPath 의 대상**은 본다.
    t_ok, t_lines = verify_dp_targets(session, base, downloadpath_rows)
    report["lines"].extend(t_lines)
    v_ok = v_ok and t_ok
    report["osinfo_ok"] = v_ok
    if not v_ok:
        report["ok"] = False
        report["skipped"] = ("OsInfo 가 FOTA 에 확인되지 않아 DownloadPath 를 보내지 "
                             "않았습니다 — OsInfo 등록을 먼저 끝내세요.")
        report["lines"].append("■ " + report["skipped"])
        return report

    for row in (downloadpath_rows or []):
        dp = build_downloadpath_payload(row)
        report["requests"].append(("POST " + _DP_ADD, dp))
        if write:
            res = _post_save(session, base + _DP_ADD, dp)
            report["dp_sent"] = True
            report["lines"].append(
                f"→ DownloadPath add(osv {dp.get('osVersion')} {dp.get('downloadType')}): " + res)
            if "success" not in res.lower():
                report["ok"] = False
            else:
                report["dp_ok"].append((str(dp.get("osType") or ""),
                                        str(dp.get("osVersion") or ""),
                                        str(dp.get("downloadType") or "")))
    return report


def format_report(rep: dict) -> str:
    """report 를 검수용 텍스트로. dry-run 은 '보낼 요청'을 필드값까지 보여준다."""
    tag = " — 실제 전송 안 함" if rep["mode"] == "DRY-RUN" else ""
    out = [f"[FOTA 등록 · {rep['mode']}{tag}]"]
    out += rep["lines"]
    for method_url, payload in rep["requests"]:
        out.append(f"\n{method_url}   (form-urlencoded)")
        for k, v in payload.items():
            vs = str(v).replace("\n", " ⏎ ")
            if len(vs) > 90:
                vs = vs[:90] + "…"
            out.append(f"     {k}={vs}")
    if rep["mode"] == "DRY-RUN" and rep["ok"]:
        out.append("\n→ 실제 등록하려면 명시 승인 필요(_ALLOW_FOTA_WRITES 전환).")
    return "\n".join(out)
