"""④ FOTA-DB Excel 제작 — OsInfo · DownloadPath (실제 배포 스키마).

- OsInfo/DownloadPath.xlsx(FOTA 등록용) 2개만 생성. **누적 엑셀은 만들지 않는다.**
- 신규 항목은 서비스 DB(FotaEntry)에도 기록.
- OS_Type = {제품}GMS{안드로이드}. 단 세 자리 PM 제품은 P{숫자}(PM352 → P352GMS13).
  후보 중 FOTA 에 실제로 있는 것을 쓴다. FOTA 에 없는(신규/예외) OS_Type 은 자동 생성하지
  않고 **관리자 확인 보류**. Janam 등 우리가 관리 안 하는 제품은 제외.
- OS_Version = **FOTA 시스템에서 직접 조회**(latestOsVersion API) + 1. (누적 엑셀 대체)
- Release_Note = 공개용 축약 문구(보안패치 + 핵심 변경 + 고정 Wi-Fi 푸터). 정밀 선별 규칙은
  추후 확정(DQA/VoC/신규기능/기본값 규칙). 지금은 ②의 변경요약 기반 best-effort 초안.
- DownloadPath 실데이터(Full/Diff PFU URL·MD5·size)는 **.104 서버 대기** — ③가 채워주면 기록.
"""
from __future__ import annotations

import re
from datetime import datetime

from app.agent.runner import StageOutput
from app.pipeline import excel_repo
from app.pipeline.state import get_state, update_state


def _fmt_ver_date(s: str) -> str:
    """빌드/출시 날짜 → 'MMM DD YYYY'(예: 2026-06-26 → Jun 26 2026). 파싱 실패 시 원문."""
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%b %d %Y", "%b %d, %Y", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%b %d %Y")
        except ValueError:
            continue
    return s


_EXCLUDED = ("janam",)  # 우리가 관리하지 않는 제품(제외)

# ── C 리비전 회차 ─────────────────────────────────────────────────────────
# 한 회차가 95.07 · 95.07C1 · 95.07C2 로 나뉘어 나가면 FOTA 에는 **셋 다** 등록한다.
# 마지막(공식)에만 진짜 다운로드 경로를 달고, 앞의 것들에는 dummy 를 넣는다 — 그래야
# 앞 버전을 쓰던 단말도 순서대로 올라온다(실측 PM95GMS13 osv 105=dummy, 106=실제).
_DUMMY_SIZE = {"FULL": "1234000", "DIFF": "1000"}   # 실등록분과 같은 고정값
_ORD_RE = re.compile(r"(\d+)\s*(?:st|nd|rd|th)\b", re.I)


def _spaced(version: str) -> str:
    """'95.07C1' → '95.07 C1'. FOTA 표기는 리비전 앞에 공백이 있다."""
    from app.pipeline.rn_research import _base_ver, _c_revision

    rev = _c_revision(version)
    return f"{_base_ver(version)} {rev}" if rev else (version or "").strip()


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }".replace(" ", "")


def _next_round_no(user_id: int, os_type: str,
                   product: str = "", android: str = "") -> tuple[int | None, str]:
    """이번이 몇 번째 회차인지. (번호|None, 사유).

    ① **직전 등록 건의 Description 에서 읽어 +1** —
       'PM95 6th Official RC 95.07 C1 (Jul 10 2026)' → 6 → 이번 7.
       같은 회차의 C 리비전들은 같은 번호를 쓴다(실측 osv 105·106 이 둘 다 6th).
    ② 번호가 적힌 건이 하나도 없으면(PM90 은 'First release' 처럼 말로 적혀 있다)
       **최초 OS 버전부터 지금까지의 회차 수를 세어** +1 한다.
       C 리비전은 base 로 묶어 하나로 센다.
    """
    from app.integrations import fota_system
    from app.pipeline import rn_lookup as _RNL
    from app.pipeline.rn_research import _base_ver

    rows, err = fota_system.fetch_osinfo_list(user_id)
    if err:
        return None, err
    mine = [r for r in rows if str(r.get("os_type", "")).strip() == os_type]
    if not mine:
        return 1, ""                       # 이 OS_Type 의 첫 등록
    mine.sort(key=lambda r: int(r.get("os_version") or 0))

    for r in reversed(mine):               # ① 최신부터 거슬러 올라가며 번호를 찾는다
        m = _ORD_RE.search(r.get("description") or "")
        if m:
            return int(m.group(1)) + 1, ""

    if not product:
        return None, "직전 등록 건에서 회차 번호를 읽지 못했습니다."

    # ② 세어 본다 — 이 SKU 범위(최초 OS 버전 ~ 다음 세대) 안의 정식 회차만.
    window = _RNL.version_window(product, android)
    bases = set()
    for r in mine:
        raw = str(r.get("version", "")).split("(")[0].strip().replace(" ", "")
        v = _RN_parse(raw)
        if v is not None and not v.tracked:      # B판 등은 회차가 아니다
            continue
        if not _RNL._in_window(raw, window):
            continue
        b = _base_ver(raw)
        if b:
            bases.add(b)
    if not bases:
        return 1, ""
    return len(bases) + 1, ""


def _RN_parse(raw: str):
    from app.pipeline import dev_rn as _RN

    return _RN.parse_version(raw)


def _revisions(user_id: int, product: str, android: str, official: str) -> list[dict]:
    """이번 회차에 **실제로 배포된** 버전들 — base → C1 → C2 … 순서, 각자 배포일.

    사다리 자리는 공식 버전에서 만든다. 공식이 95.07C2 면 95.07 · C1 · C2 자리가 생긴다.
    그 다음 **근거가 있는 칸만 남긴다.**

        · base — 늘 남긴다. 회차는 base 에서 시작하고, C1 은 base 를 전제한다.
          오래된 base 공지가 정리되어 사라져도 빼지 않는다
          (실측 2026-08-05: PM95 A13 95.07 회차에 C1 공지만 남아 base 가 빠졌다)
        · 중간 C 판 — **배포일이 있을 때만**. 공지도 출시 이력도 없는 칸은 나간 적이
          없는 판이다
        · 공식(맨 윗 칸) — 늘 남긴다. 이번에 등록하려는 판이다

    왜 걸러야 하나. 자리를 다 채우면 나간 적 없는 판까지 FOTA 에 올라간다
    (사용자 지적 2026-10-07: "c1부터 c8까지 전부 다 downloadpath로 등록될 필요는
    없는데… 지금 approval된게 07이랑 07c8 이렇게라 이 2개만"). 실측으로 PM86 A11
    86.07C8 은 9건 → 2건(07 · 07C8)이 되고, PM95 A13 95.07C2(3건) · MF52E C1(2건) ·
    86.53(1건) 은 그대로다.

    날짜는 배포 공지 → 출시 이력 순으로 채운다. 둘 다 없으면 빈 값(칸에 날짜를 안 쓴다).
    """
    from app import os_products as _OP
    from app import release_history as _RH
    from app.pipeline.rn_research import _base_ver, _c_revision, _deploy_candidates

    base = _base_ver(official)
    top = _c_revision(official)
    ladder = [""] + [f"C{i}" for i in range(1, int(top[1:] or 0) + 1)] if top else [""]

    # ① 배포 공지 — 리비전별 날짜가 가장 정확하다(같은 리비전이 여럿이면 최초 배포일).
    dates: dict[str, str] = {}
    try:
        cands = _deploy_candidates(user_id, product, android)
    except Exception:  # noqa: BLE001
        cands = []
    for c in cands:
        v = c.get("version", "")
        if _base_ver(v) != base or c.get("is_beta"):
            continue
        rev, d = _c_revision(v), (c.get("date") or "").strip()
        if not d:
            continue
        if rev not in dates or _sort_date(d) < _sort_date(dates[rev]):
            dates[rev] = d

    # ② 출시 이력 — 공지가 사라진 옛 리비전은 여기서 날짜를 얻는다.
    pid = _OP.paths_for(product, android).get("product_id")
    if pid:
        pub = _RH.published(pid)
        norm = {str(k).replace(" ", "").upper(): v for k, v in pub.items()}
        for rev in ladder:
            if rev in dates:
                continue
            got = norm.get((base + rev).replace(" ", "").upper())
            if got:
                dates[rev] = got

    # 근거가 있는 칸만 — base 와 공식은 늘, 중간 C 판은 배포일이 있을 때만.
    top_rev = top or ""
    keep = [rev for rev in ladder
            if rev == "" or rev == top_rev or dates.get(rev)]
    return [{"version": base + rev, "rev": rev, "date": dates.get(rev, "")}
            for rev in keep]


# ── Whitelist OS_Type ─────────────────────────────────────────────────────
# 한 제품에 안드로이드가 여럿이면, 정식 OS_Type 말고 **업그레이드용 whitelist
# OS_Type** 에도 같은 회차를 등록한다(WhiteList 규칙표 WLRule 이 정한다).
# 그 안에서는 세대마다 번호 대역이 따로 있다 — from 세대가 아래, to 세대가 위여야
# 단말이 아래에서 위로 올라간다. 그래서 전역 latest+1 이 아니라 **그 세대 대역의
# 최댓값 +1** 을 쓴다(실측 White95TtoV: A13 1~63, A15 100~103).
def _whitelist_targets(product: str, android: str) -> list[dict]:
    """이 릴리즈가 들어가야 할 whitelist OS_Type 목록. [{ostype, side, other}]"""
    from app.whitelist import service as _W

    try:
        an = int(str(android).strip())
    except (TypeError, ValueError):
        return []
    out = []
    for r in _W.rules_for(product):
        if r["to"] == an:
            out.append({"ostype": r["ostype"], "side": "to", "other": r["from"]})
        elif r["from"] == an:
            out.append({"ostype": r["ostype"], "side": "from", "other": r["to"]})
    return out


def _band_next(user_id: int, ostype: str, product: str, android: str) -> tuple[int, str]:
    """그 whitelist OS_Type 안에서 이 세대가 쓸 다음 OS_Version. (번호, 사유).

    B판(beta)·NB판은 정식 회차가 아니라 대역 기준에서 뺀다.
    이 세대 행이 하나도 없으면 1 부터 시작한다(위 세대는 50·100 처럼 위에 있다).
    """
    from app.integrations import fota_system
    from app.pipeline import dev_rn as _RN
    from app.pipeline import rn_lookup as _RNL

    try:
        an = int(str(android).strip())
    except (TypeError, ValueError):
        return 0, "안드로이드 버전을 알 수 없습니다."
    rows, err = fota_system.fetch_osinfo_list(user_id)
    if err:
        return 0, err

    band: dict[int, list[int]] = {}
    for x in rows:
        if str(x.get("os_type", "")).strip() != ostype:
            continue
        raw = str(x.get("version", "")).split("(")[0].strip().replace(" ", "")
        v = _RN.parse_version(raw)
        if v is not None and not v.tracked:      # B판·NB판 — 대역 기준 아님
            continue
        for p in _OP_products(product):
            if _RNL._in_window(raw, _RNL.version_window(product, str(p))):
                band.setdefault(int(p), []).append(int(x.get("os_version") or 0))
                break

    mine = band.get(an) or []
    nxt = (max(mine) + 1) if mine else 1
    # 위 세대 대역을 침범하면 안 된다 — 넘으면 사람이 정해야 한다.
    above = [min(v) for g, v in band.items() if g > an and v]
    if above and nxt >= min(above):
        return 0, (f"{ostype}: A{an} 대역 다음 번호 {nxt} 가 위 세대 대역"
                   f"(최소 {min(above)})과 겹칩니다 — 번호를 직접 정해야 합니다.")
    return nxt, ""


def _OP_products(product: str) -> list[str]:
    """그 제품에 등록된 안드로이드 버전들(높은 것부터 — 좁은 범위를 먼저 본다)."""
    from app import os_products as _OP

    ans = [str(r["android"]) for r in _OP.list_products()
           if str(r["product"]).upper() == str(product).upper() and r["android"]]
    return sorted(set(ans), key=lambda x: -int(x))


def _dummy_dp(os_type: str, os_ver: int, version: str) -> list[list]:
    """dummy DownloadPath 2행(FULL/DIFF) — 실제 파일이 필요 없는 자리표시."""
    out = []
    for kind in ("FULL", "DIFF"):
        fn = f"{version.replace(' ', '')}_{kind.capitalize()}.PFU"
        out.append([os_type, "Dropbox", os_ver, f"http://{fn}",
                    f"http://{fn}.md5", kind, fn, _DUMMY_SIZE[kind]])
    return out


def _sort_date(s: str) -> str:
    """'Jul 10 2026' → '2026-07-10'(비교용). 못 읽으면 원문."""
    for fmt in ("%b %d %Y", "%b %d, %Y", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


_PM3_RE = re.compile(r"^PM(\d{3}[A-Za-z]?)$", re.I)


def os_type_candidates(product: str, android: str) -> list[str]:
    """FOTA OS_Type 이름 후보. 앞의 것이 관례상 맞는 이름이다.

    세 자리 PM 제품은 FOTA 에 'P' 하나로 줄여 등록돼 있다 — 실측 2026-10-13:
    P351GMS11 · P352GMS13 · P451GMS9/11/13 · P452GMS13 · P550GMS7 · P560GMS11.
    두 자리 PM(PM30 · PM86 · PM95 …)과 TR · MF 는 제품명 그대로다.
    사용자 지적 2026-10-13: "PM352GMS13을 찾네요. 실제로 등록되어있는건 P352GMS13".
    """
    p = (product or "").strip()
    a = str(android or "").strip()
    full = f"{p}GMS{a}"
    m = _PM3_RE.match(p)
    if m:
        return [f"P{m.group(1)}GMS{a}", full]
    return [full]


def _resolve_os_type(product: str, android: str) -> tuple[str | None, str]:
    """OS_Type 후보 결정(형식 검증만). (os_type, 사유). 존재 여부는 FOTA API 로 확인한다.
    os_type 이 None 이면 제외/보류. 돌려주는 것은 관례상 맞는 첫 후보."""
    p = (product or "").strip()
    a = str(android or "").strip()
    if not p or not a:
        return None, "제품 또는 Android 를 식별하지 못함(Release Note 제작 · OS Download 먼저 확인)."
    if any(b in p.lower() for b in _EXCLUDED):
        return None, f"'{p}' 는 우리가 관리하지 않는 제품(Janam 등) — 제외."
    return os_type_candidates(p, a)[0], ""


def _release_note(revision_lines: list[str]) -> str:
    """OsInfo 의 Release_Note = **RN for Sales 의 Revision History Description 그대로**
    (사용자 확정 2026-08-05).

    전에는 여기서 따로 조립했다 — 보안패치 줄을 새로 붙이고, 항목을 5개로 자르고,
    'Improved system stability and bug fixed' 를 항상 덧붙이고, Wi-Fi 안내를 꼬리에
    달았다. 그 결과 단말에 뜨는 문구가 RN 문서와 달라졌다. 이제는 검수를 거친 그
    목록을 그대로 쓴다 — 보안패치·안정성 줄도 이미 그 안에 들어 있다.
    """
    return "\n".join(f"- {t}" for t in
                     ((ln or "").strip().rstrip(".") for ln in (revision_lines or []))
                     if t)


def _record_fota(user_id, mail_id, os_type, version, os_ver, oi_added, dp_added) -> None:
    from app.database import get_session
    from app.models import FotaEntry

    with get_session() as db:
        row = db.query(FotaEntry).filter_by(os_type=os_type, version=version).first()
        if row:
            row.os_version = os_ver
            row.osinfo_added = oi_added
            row.downloadpath_added = dp_added
        else:
            db.add(FotaEntry(
                user_id=user_id, mail_id=None if mail_id is None else str(mail_id), os_type=os_type, version=version,
                os_version=os_ver, osinfo_added=oi_added, downloadpath_added=dp_added,
            ))
        db.commit()


def record_registered(user_id: int, mail_id, report: dict) -> int:
    """FOTA 등록 뒤 **실제로 들어간 것**을 기록한다. 적은 행 수를 돌려준다.

    report 는 fota_system.register_rows / register_downloadpath_only 의 반환값이다.
    'osinfo_confirmed' 는 보낸 뒤 목록을 **되읽어 맞춰 본** 행들이고 'dp_ok' 는 저장
    응답이 success 였던 DownloadPath 행들이다. 보냈다는 응답만으로는 무엇이 저장됐는지
    알 수 없어서(저장 응답이 HTML 이다) 이 둘만 믿는다.

    dry-run 이면 아무것도 적지 않는다 — 보낸 것이 없다.
    """
    if not report or report.get("mode") != "WRITE":
        return 0
    confirmed = list(report.get("osinfo_confirmed") or [])
    if not confirmed:
        return 0

    dp_ok = list(report.get("dp_ok") or [])
    n = 0
    for ot, osv, ver in confirmed:
        dp_n = sum(1 for (d_ot, d_osv, _t) in dp_ok if d_ot == ot and d_osv == osv)
        try:
            _record_fota(user_id, mail_id, ot, ver, int(osv or 0), 1, dp_n)
            n += 1
        except Exception:  # noqa: BLE001  (기록이 실패해도 등록 결과를 가리지 않는다)
            pass
    return n


def official_round(st: dict) -> tuple[str, str, str]:
    """이번 회차의 **공식 표기와 날짜**. (버전, 날짜, 알림글)

    보통 release_version 이 'MF52EG15.00 C1' 처럼 C 리비전을 품고, release_date 가
    그 판의 날짜다. 그런데 회차를 뒤늦게 다른 C 판으로 다시 잡으면 version_full 만
    바뀌고 release_version·release_date 는 **base 의 값으로 남을 수 있다**.

        실측 2026-10-07 job-24 — release_version '86.07' · release_date 'Jun 19 2026' ·
        version_full '86.07C8 (Aug 24 2026)'. 그 상태로 등록하니 사다리가 base 하나만
        만들어져(_revisions 는 공식 표기에서 C 사다리를 만든다) FOTA 에
        '86.07 (Jun 19 2026)' 로 올라갔다. 그 표기는 이미 osv 107 에 있어서 FOTA 가
        저장하지 않았고, 화면에는 등록된 것처럼 보였다.

    그래서 **version_full 이 더 높은 C 판을 가리키면 그쪽을 믿는다.** 빌드명에 붙은
    날짜는 그 판의 것이므로 날짜도 함께 가져온다(빌드 날짜는 임의로 바꾸지 않는다).
    """
    from app.pipeline.round_version import round_version

    return round_version(st)


def run_excel_docs(
    user_id: int, subject: str, input_params: str = "", mail_id: int | None = None
) -> StageOutput:
    st = get_state(mail_id) if mail_id is not None else {}
    product = st.get("product") or ""
    android = st.get("android") or ""
    # 회차 공식 표기 — C 리비전을 잃지 않는다(위 official_round 주석 참고).
    version, rel_date, _ver_note = official_round(st)
    # Release_Note = RN for Sales 의 Revision History Description(② 편집 반영본) 그대로.
    revision_lines = st.get("rn_revision_lines") or st.get("rn_public_changes") or []
    security_patch = st.get("security_patch") or ""      # 표시용(누락 경고에만 쓴다)
    downloads = st.get("fota_downloads") or []           # ③ 결과(.104 대기)
    # 공식 판에서 출발하는 DIFF 만 등록한다(07C1 이 있으면 07 에서 가는 DIFF 는 뺀다).
    # 같은 (폴더 URL) 이 두 번 든 옛 결과도 한 번만 쓴다.
    from app.pipeline.os_download import official_diffs as _official_diffs

    # 공식 여부는 OS Download 가 받은 목록(fota_source)까지 합쳐서 정한다 — Dropbox 결과에는
    # 공식 DIFF 가 빠져 있을 수 있다(옛 덮어쓰기 버그, job-34).
    _src_diffs = [str(s_.get("name", "")) for s_ in (st.get("fota_source") or [])
                  if str(s_.get("type", "")).upper() == "DIFF"]
    _dl_diffs = [str(d.get("filename", "")) for d in downloads
                 if str(d.get("type", "")).upper() == "DIFF"]
    _dk, _ = _official_diffs(sorted(set(_src_diffs + _dl_diffs)))
    _missing_diff = sorted(n for n in _dk if n in _src_diffs and n not in _dl_diffs)
    _seen_dl: set = set()
    _clean = []
    for d in downloads:
        if str(d.get("type", "")).upper() == "DIFF" and d.get("filename", "") not in _dk:
            continue
        k = (d.get("url", ""), d.get("filename", ""))
        if k in _seen_dl:
            continue
        _seen_dl.add(k)
        _clean.append(d)
    downloads = _clean

    header = f"[FOTA-DB Excel · {product or '?'} A{android or '?'}]\n"
    if _ver_note:
        header += f"※ {_ver_note}\n"
    if downloads and _missing_diff:
        # 공식 DIFF 가 Dropbox 결과에 없으면 빠진 채 등록하지 않는다(job-34: 07C1 이 빠짐).
        return StageOutput(result_text=(
            header + "✘ 공식 DIFF 가 Dropbox 업로드 결과에 없습니다: " + ", ".join(_missing_diff) + "\n"
            "  DownloadPath 를 빠진 채 등록하지 않습니다 — 'Dropbox 업로드' 를 되돌리고 다시 실행하세요."))

    if not version:
        return StageOutput(result_text=(
            header + "정식 버전이 없습니다(Release Note 제작을 먼저 실행해 버전을 확정하세요)."
        ))

    # OS_Type 형식 검증(제외 제품 거름)
    os_type, why = _resolve_os_type(product, android)
    if os_type is None:
        return StageOutput(result_text=(header + f"진행 보류: {why}\n버전 {version}"))

    # OS_Version 카운터 = FOTA 시스템에서 직접 조회(누적 엑셀 대체).
    #   존재하는 OS_Type → latest+1. 없으면 신규/예외 → 관리자 확인 보류.
    from app.integrations import fota_system

    # 후보 중 FOTA 에 실제로 있는 것을 쓴다(세 자리 PM 은 P352GMS13 이 먼저).
    lv, ferr = None, ""
    for _cand in os_type_candidates(product, android):
        lv, ferr = fota_system.get_latest_os_version(user_id, _cand)
        if ferr:
            return StageOutput(result_text=(header + f"✘ FOTA 조회 실패: {ferr}"))
        if lv is not None:
            os_type = _cand
            break
    baseline = False
    if lv is None:
        # 최초 출시는 OS_Type 이 FOTA 에 **아직 없는 게 정상**이다. 이 회차가 baseline
        # (OS_Version 1) 이 돼야 다음 업데이트 때 latest+1 이 성립한다.
        # 판단은 파이프라인 상태(rn_first_release)가 아니라 제품 등록의 '최초 OS 버전'으로
        # 한다 — 상태 값은 ②의 실행 이력에 따라 켜졌다 꺼졌다 한다.
        from app.os_products import find_sku as _find_sku
        from app.os_update_jobs import is_first_release as _is_first

        _row = _find_sku(product, android) or {}
        if _is_first(_row.get("initial_os_version") or "", version):
            lv, baseline = 0, True
        else:
            return StageOutput(result_text=(
                header + f"진행 보류: OS_Type '{os_type}' 가 FOTA 에 없습니다(신규/예외 제품). "
                f"자동 생성하지 않습니다 — 관리자 확인 후 진행 필요.\n버전 {version}\n"
                "(최초 출시라면 제품 관리의 '최초 OS 버전' 을 먼저 등록하세요 — "
                "그 값이 있어야 baseline 으로 진행합니다.)"
            ))
    # ── 이번 회차에 등록할 버전들 ────────────────────────────────────
    # 95.07 · 95.07C1 · 95.07C2 처럼 나뉘어 나간 회차는 **셋 다** 등록한다.
    revs = _revisions(user_id, product, android, version)
    round_no, rerr = _next_round_no(user_id, os_type, product, android)

    # Release_Note_URL = **그 제품의 소개 페이지**. 단말에서 사용자가 누르는 링크이고
    # 문서를 바로 내려받게 하는 자리가 아니다. 2026-09-22 에 내가 이 자리를 RN for
    # Sales 의 Dropbox 링크로 바꿨는데 그것이 틀렸다(사용자 지적 2026-10-07).
    # 주소는 제품마다 달라 조합으로 못 맞춘다 — 지난 회차가 쓰던 주소를 이어받는다.
    _oi_all, _oi_err = fota_system.fetch_osinfo_list(user_id)
    url = fota_system.release_note_url(_oi_all if not _oi_err else [], os_type, product)
    rn_text = _release_note(revision_lines)
    ord_txt = _ordinal(round_no) + " " if round_no else ""

    osinfo_rows: list[list] = []
    for i, r in enumerate(revs):
        # Version 칸: '95.07 C1 (Jul 10 2026)' — 리비전 앞 공백, 그 리비전의 배포일.
        vtxt = _spaced(r["version"])
        vdate = r["date"] or (rel_date if i == len(revs) - 1 else "")
        cell = f"{vtxt} ({_fmt_ver_date(vdate)})" if vdate else vtxt
        # 같은 회차의 C 리비전들은 같은 번호를 쓴다(실측 osv 105·106 이 둘 다 6th).
        desc = f"{product} {ord_txt}Official RC {cell}"
        # Release_Note 는 **마지막(공식) 행에만** 넣는다 — 중간 리비전은 비운다.
        note = rn_text if i == len(revs) - 1 else ""
        osinfo_rows.append([os_type, cell, lv + 1 + i, desc, note, url, 0, 0, 0])

    official_row = osinfo_rows[-1]
    os_ver = official_row[2]
    version_cell = official_row[1]

    # ── DownloadPath ────────────────────────────────────────────────
    # 마지막(공식) 행에만 실제 경로. 앞 리비전들은 dummy 2행(FULL·DIFF)씩.
    dp_rows: list[list] = []
    for i, r in enumerate(revs[:-1]):
        base_name = _spaced(r["version"]).replace(" ", "")
        for kind in ("FULL", "DIFF"):
            fn = f"{base_name}_{kind.capitalize()}.PFU"
            dp_rows.append([os_type, "Dropbox", lv + 1 + i, f"http://{fn}",
                            f"http://{fn}.md5", kind, fn, _DUMMY_SIZE[kind]])
    for d in downloads:                      # ③ 결과(.104 대기)
        # size 는 ③ 이 **바이트**로 준다 — FOTA 칸의 관례에 맞춰 변환한다.
        dp_rows.append([
            os_type, "Dropbox", os_ver, d.get("url", ""), d.get("md5_url", ""),
            str(d.get("type", "")).upper(), d.get("filename", ""),
            fota_system.dp_size(d.get("size", "")),
        ])

    # ── Whitelist OS_Type ───────────────────────────────────────────
    # 규칙표(WLRule)에 이 제품·이 안드로이드가 걸린 게 있으면 그쪽에도 같은 회차를 올린다.
    wl_notes: list[str] = []
    for tgt in _whitelist_targets(product, android):
        start, werr = _band_next(user_id, tgt["ostype"], product, android)
        if werr:
            wl_notes.append("✘ " + werr)
            continue
        for i, r in enumerate(revs):
            vtxt = _spaced(r["version"])
            vdate = r["date"] or (rel_date if i == len(revs) - 1 else "")
            cell = f"{vtxt} ({_fmt_ver_date(vdate)})" if vdate else vtxt
            wosv = start + i
            # 화이트리스트 OS_Type 은 **자기 이력**의 주소를 이어받는다 — 정식
            # OS_Type 과 다른 형태를 쓰는 경우가 있다(실측 White86RtoT).
            _w_url = fota_system.release_note_url(
                _oi_all if not _oi_err else [], tgt["ostype"], product)
            osinfo_rows.append([tgt["ostype"], cell, wosv,
                                f"{product} A{android}", "", _w_url, 0, 0, 0])
            last = i == len(revs) - 1
            if last and tgt["side"] == "to" and downloads:
                # to 쪽은 **실제로 이 버전을 받아 올라가는** 경로다 — 실제 링크가 필요하다.
                for d in downloads:
                    dp_rows.append([
                        tgt["ostype"], "Dropbox", wosv, d.get("url", ""),
                        d.get("md5_url", ""), str(d.get("type", "")).upper(),
                        d.get("filename", ""), fota_system.dp_size(d.get("size", "")),
                    ])
            else:
                # from 쪽(그리고 중간 리비전)은 '현재 상태' 표시일 뿐이라 dummy 로 둔다.
                dp_rows.extend(_dummy_dp(tgt["ostype"], wosv, vtxt))
        wl_notes.append(
            f"✔ {tgt['ostype']} [{'to' if tgt['side'] == 'to' else 'from'} 쪽 · "
            f"A{tgt['other']} 와의 업그레이드] OS_Version {start}~{start + len(revs) - 1}")

    # 신규만(repo) OsInfo/DownloadPath.xlsx 저장 — FOTA 등록용 산출물.
    excel_repo.save_new("OsInfo", osinfo_rows)
    excel_repo.save_new("DownloadPath", dp_rows)

    # 서비스 DB 기록 + 검수 팝업/실제 FOTA 등록이 쓸 수 있게 행을 상태에 저장
    if mail_id is not None:
        # **아직 등록 전이다.** 예전에는 여기서 True·len(dp_rows) 로 적어, 등록이
        # 막혀도 기록은 '등록됨' 으로 보였다(실측 2026-10-07 job-24: FOTA 에 osv 108 이
        # 없는데 osinfo_added=1 로 남아 있었다). 실제 수는 등록 뒤에
        # record_registered() 가 채운다.
        _record_fota(user_id, mail_id, os_type, version_cell, os_ver, 0, 0)
        update_state(
            mail_id, fota_os_type=os_type, fota_os_version=os_ver,
            fota_osinfo_rows=osinfo_rows, fota_dp_rows=dp_rows,
            fota_osinfo_row=official_row,        # 예전 키(하위 호환)
        )

    lines = [
        (f"✔ OS_Type: {os_type} · 등록할 회차 {len(revs)}건 "
         f"(OS_Version {lv + 1}~{os_ver}, **최초 출시 baseline** — FOTA 에 이 OS_Type 을 "
         f"새로 만드는 회차입니다)"
         if baseline else
         f"✔ OS_Type: {os_type} · 등록할 회차 {len(revs)}건 "
         f"(OS_Version {lv + 1}~{os_ver}, FOTA 최신 {lv} 다음)"),
    ]
    for i in range(len(revs)):
        tag = "공식" if i == len(revs) - 1 else "중간"
        lines.append(f"    · osv {osinfo_rows[i][2]} [{tag}] {osinfo_rows[i][1]}")
    lines.extend(wl_notes or ["· Whitelist: 이 제품·안드로이드에 해당하는 규칙 없음"])
    lines.append(f"✔ OsInfo/DownloadPath.xlsx 저장: {excel_repo.REPO_DIR}")
    n_dummy = sum(1 for r in dp_rows if str(r[3]).startswith("http://"))
    n_real = len(dp_rows) - n_dummy
    lines.append(
        f"✔ DownloadPath {len(dp_rows)}행 — 실제 {n_real}행 · dummy {n_dummy}행"
        if dp_rows else
        "· DownloadPath: 다운로드 파일 정보 없음 (Full/Diff PFU + Dropbox 업로드 결과 필요)"
    )
    lines.append(
        f"✔ Release_Note: Revision History Description {len(revision_lines)}줄 "
        f"— 공식 행에만 넣고 중간 회차는 비웁니다")
    lines.append(f"{'✔' if url else '·'} Release_Note_URL: "
                 f"{url or '제품 관리에 미등록 — 빈 값으로 등록됩니다'}")
    note = []
    if not url:
        note.append(f"Release_Note_URL 이 비었습니다 — {_rn_note}")
    if rerr:
        note.append(f"회차 번호 미확정({rerr}) — Description 확인 필요")
    if not security_patch:
        note.append("보안패치 날짜 미확보(PFU/.104 시 자동 채움)")
    if not revision_lines:
        note.append("Revision History Description 이 비어 Release_Note 도 빕니다 "
                    "(Release Note 제작·검수를 먼저 끝내세요)")
    if n_real == 0:
        note.append(f"공식 회차({version_cell})에 실제 DownloadPath 가 없습니다 "
                    "— Dropbox 업로드를 먼저 끝내세요")

    # FOTA System 등록 — dry-run(실제 저장 안 함, 로그인+카운터만 읽기전용).
    from app.integrations import fota_system

    # 여러 행이므로 register_rows 를 쓴다(DQA FOTA 와 같은 경로).
    fota_rep = fota_system.register_rows(user_id, osinfo_rows, dp_rows)  # 게이트(False)
    fota_block = fota_system.format_report(fota_rep)

    summary = (
        header
        + "※ OS_Version 은 FOTA 시스템에서 직접 조회합니다. "
        "OsInfo/DownloadPath.xlsx(FOTA 등록용) 2개만 생성합니다.\n"
        "※ 실제 등록은 '검수하기' 팝업에서 확인·실행합니다. "
        "등록된 데이터 열람은 [OS Update → FOTA-DB 열람].\n\n"
        + "\n".join(lines)
        + (("\n\n※ " + " / ".join(note)) if note else "")   # 없으면 '※ ' 만 남지 않게
        + "\n\n" + fota_block
    )
    return StageOutput(result_text=summary)  # 산출물(다운로드) 없음 — 팝업 열람만
