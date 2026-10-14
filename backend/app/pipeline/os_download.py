"""단계 ① OS Download — 파일서버(.104 구조)에서 실제 OS PFU 를 받는다.

흐름:
  1) 제품 코드 + Android 파악(빌드지문 PM95:13 등).
  2) 정식 버전 판별(SP/RN 출시본 + FIH 증분) → 폴더 탐색·교차검증 기준.
  3) 파일서버에서 `[root]/[제품]/Software/[Android]/Formal Release/[버전]/sdcard/` 를 퍼지 탐색
     (대소문자·공백·P↔+·기본 Android 유동). sdcard 의 PFU 를 Full/Diff 로 분류
     (**Full=용량 큼/짧은 이름, Diff=용량 작음/긴 이름**).
  4) 파일명에서 버전·날짜·빌드등급 파싱 → 계산 버전과 교차검증, **비배포 등급(B/C…)이면 경고**.
  5) **7개 파일**을 로컬로 다운로드하고 ③ Dropbox 업로드용으로 상태에 기록:
       sdcard/ : Full PFU · Full .md5 · Diff PFU · Diff .md5 · Verify_Checksum*.exe   (5)
       flash/  : 플래싱 툴 .zip                                                        (1)
       EmKit   : {root}/Emkit/EmkitSDK/FormalRelease/{날짜}_v.{SDK}/sdk/v{SDK}.zip     (1)
     SDK 버전은 PFU 에서 자동 추출한 값(comp_auto.sdk)을 쓰므로 추출 뒤에 내려받는다.

파일서버는 '읽기만' 한다. 기준 최상위 폴더는 env `FOTA_SRC_ROOT`(기본 'project').
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from app import progress
from app.agent.runner import StageOutput
from app.integrations.fileserver import get_backend

_WORK_ROOT = Path(__file__).resolve().parent.parent.parent / "work" / "os_download"
# 제품 인식 — 타입(PM/TR/MF/RF/PR) + 클래스 + 변형문자(선택: PM84P의 P).
_PRODUCT_RE = re.compile(r"\b(PM|TR|MF|RF|PR)\s*-?\s*(\d{2,3})([A-Za-z]?)\b", re.IGNORECASE)
# 파일서버 기준 최상위(제품 폴더들이 있는 곳). 테스트 160='project', 실 104='Project'(퍼지 매칭).
_FS_ROOT = os.getenv("FOTA_SRC_ROOT", "project")


def _detect_product(input_params: str, subject: str) -> str:
    """제품 코드(PM95·TR54·PM84P 등) 파악: 입력값 우선, 없으면 제목에서 추정."""
    for src in (input_params or "", subject or ""):
        m = _PRODUCT_RE.search(src)
        if m:
            return f"{m.group(1).upper()}{m.group(2)}{(m.group(3) or '')}"
    return ""


# 버전 직접 지정: 'version: 95.07' 라벨형 우선, 없으면 'NN.NN' 토큰(제품코드 PM95 와 구분됨).
_VERSION_RE = re.compile(r"\b(\d{2,3}\.\d{1,2})\b")


def _override_version(input_params: str) -> str:
    """사용자가 input_params 에 버전을 명시했으면(예 'version: 95.07' 또는 '95.07') 그 값을 반환.

    없으면 "" — 이 경우 SP/RN 출시 이력으로 자동 판별한다.
    """
    text = input_params or ""
    m = re.search(r"version\s*[:=]\s*(\d{2,3}\.\d{1,2})", text, re.IGNORECASE)
    if m:
        return m.group(1)
    m = _VERSION_RE.search(text)
    return m.group(1) if m else ""


def _resolve_product_android(
    user_id: int, mail_id: int | None, subject: str, input_params: str
) -> tuple[str, str]:
    """제품 + Android 를 확정한다: 상태 → 입력/제목 → 트리거 메일 분석(빌드지문 PM95:13 등).

    Android 는 SKU(A13/A15) 구분에 필수라 반드시 확보하려 한다.
    """
    from app.pipeline.state import get_state

    st = get_state(mail_id) if mail_id is not None else {}
    product = st.get("product") or _detect_product(input_params, subject)
    android = str(st.get("android") or "")
    # 작업(job-N)에서 왔으면 제품·Android 는 상태에 이미 심겨 있다 — 메일 분석은 건너뛴다.
    if isinstance(mail_id, int) and (not product or not android):
        from app.pipeline.mail_analysis import analyze_mail

        a = analyze_mail(mail_id, user_id)
        if not a.get("error"):
            product = product or (a.get("product_family") or "")
            android = android or (a.get("android_version") or "")
    return product, android


def _fmt_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{int(n)}B" if unit == "B" else f"{f:.1f}{unit}"
        f /= 1024.0
    return f"{f:.1f}GB"


# DIFF PFU 이름 = {새 버전}_{출발 판}[_.]{빌드 날짜}.PFU — 실측 이름들:
#   95.08_07C1_20260929 · 95.08_07_20260929 · 86.07C8_07_20260824 · 95.52_51.20260718
#   TR54G15.04_03C1_20260822 · PM84PG15.03_02C1_20260727
_DIFF_FROM_RE = re.compile(
    r"^(?P<new>.+?)_(?P<from>\d{1,3})(?P<rev>[A-Z]{1,2}\d{0,2})?[._](?P<date>20\d{6})\.pfu$", re.I)


def diff_from(name: str) -> tuple[str, str] | None:
    """DIFF PFU 이름에서 출발 판 (base 자리, 리비전). '95.08_07C1_…' → ('07', 'C1'). 못 읽으면 None."""
    m = _DIFF_FROM_RE.match((name or "").strip())
    if not m:
        return None
    return m.group("from"), (m.group("rev") or "").upper()


def _rev_rank(rev: str) -> int:
    """리비전 순위 — 민짜 0, C1 1, C2 2 … 그 밖(B·RC 등 비배포)은 -1."""
    if not rev:
        return 0
    m = re.fullmatch(r"C(\d*)", rev)
    if m:
        return int(m.group(1) or 1)
    return -1


def official_diffs(names: list[str]) -> tuple[set[str], list[str]]:
    """DIFF PFU 이름들 중 **공식 판에서 출발하는 것만** 남긴다. (남길 이름, 뺀 이름).

    같은 출발 base(예 07) 안에서 C 리비전이 가장 높은 것만 공식이다 — C1 이 나왔으면 민짜 07 은
    공식이 아니다(사용자 2026-10-13: "07c1에서 08로 가는 diff만 있으면 됩니다. 애초에 c1 버전이
    나왔으니 07은 공식 버전이 아닙니다"). 실측: PM95 95.08 빌드 폴더에 95.08_07_… 과
    95.08_07C1_… 이 함께 있었다. 이름을 못 읽는 DIFF 는 그대로 둔다(빼지 않는다).
    """
    best: dict[str, tuple[int, str]] = {}
    for n in names:
        f = diff_from(n)
        if f is None:
            continue
        base, rev = f
        r = _rev_rank(rev)
        if base not in best or r > best[base][0]:
            best[base] = (r, n)
    keep, drop = set(), []
    for n in names:
        f = diff_from(n)
        if f is None or best.get(f[0], (None, n))[1] == n:
            keep.add(n)
        else:
            drop.append(n)
    return keep, drop


def _classify_pfus(entries) -> list[dict]:
    """sdcard FileEntry 목록 → PFU 분류. Full=최대 용량(짧은 이름), 나머지=Diff.

    반환 [{type, name, size, md5}] (type: FULL/DIFF). 각 PFU 의 md5 = '<pfu>.md5' 있으면 그 이름.
    """
    pfus = [e for e in entries if e.name.lower().endswith(".pfu")]
    md5_names = {e.name for e in entries if e.name.lower().endswith(".md5")}
    pfus.sort(key=lambda e: e.size, reverse=True)  # 큰 것(Full) 먼저
    out: list[dict] = []
    for i, e in enumerate(pfus):
        cand = e.name + ".md5"
        out.append({
            "type": "FULL" if i == 0 else "DIFF",
            "name": e.name, "size": e.size,
            "md5": cand if cand in md5_names else "",
        })
    return out


# flash(플래싱 툴) 폴더는 제품마다 이름이 다르다 — 'flash' 또는 'fastboot'.
_FLASH_NAME_RE = re.compile(r"flash|fastboot", re.I)


def _resolve_flash_dir(backend, sdcard: str, override: str = "") -> str:
    """sdcard 의 형제 폴더에서 플래싱 툴 폴더를 찾는다(flash/fastboot/유사명). 없으면 "".

    override 가 있으면 그대로 쓴다(수동 지정).
    """
    if override:
        return override.rstrip("/")
    parent = sdcard.rstrip("/").rsplit("/", 1)[0]
    # 우선 알려진 이름을 직접 시도(대소문자 무관, .zip 이 있어야 인정)
    for name in ("flash", "fastboot", "Flash", "Fastboot"):
        cand = f"{parent}/{name}"
        try:
            if any(e.name.lower().endswith(".zip") for e in backend.list_dir(cand)):
                return cand
        except Exception:  # noqa: BLE001
            continue
    # 그래도 못 찾으면 형제 폴더명을 훑어 flash/fastboot 가 든 폴더를 찾는다
    try:
        for e in backend.list_dir(parent):
            if _FLASH_NAME_RE.search(e.name):
                return f"{parent}/{e.name}"
    except Exception:  # noqa: BLE001
        pass
    return ""


def _same_round(a: str, b: str) -> bool:
    """두 버전 표기가 **같은 회차**인가 — 접미(C 리비전 등)만 다른 경우.

    '86.07' 과 '86.07C8' 은 같은 회차다. '86.07' 과 '86.08' 은 아니다.
    버전 문법은 dev_rn 한 벌만 쓴다(두 벌로 갈라지면 반드시 어긋난다).
    """
    from app.pipeline.dev_rn import parse_version

    pa, pb = parse_version(a or ""), parse_version(b or "")
    if not pa or not pb:
        return False
    return (pa.major.upper(), pa.minor) == (pb.major.upper(), pb.minor)


def ver_crosscheck(file_ver: str, tier: str, want_ver: str) -> tuple[str, str]:
    """파일명에서 읽은 버전과 단계가 계산한 회차 표기를 맞댄다. (판정, 할 말).

    판정은 넷이다 — "skip" · "ok" · "same_round" · "mismatch".

    **같은 기준끼리** 비교해야 한다. parse_os_build 의 version 은 접미를 뗀 표기이고
    want_ver 는 회차 전체 표기(C 리비전 포함)다. 그대로 맞대면 C 회차마다
    '버전 불일치' 가 뜬다(실측 2026-10-07 job-24: 파일 86.07 ≠ 계산 86.07C8).

    같은 회차의 다른 리비전은 **틀린 파일이 아니다** — 어느 판을 쓰는지만 알린다.
    """
    full = (file_ver + tier) if file_ver else ""
    norm = (lambda v: (v or "").replace(" ", "").upper())
    if not file_ver:
        return "skip", f"· 버전 교차검증 건너뜀: 파일명에서 버전을 못 읽음 (계산 {want_ver})"
    if norm(full) == norm(want_ver):
        return "ok", f"· 버전 교차검증 OK: 파일 {full} == 계산 {want_ver}"
    if _same_round(full, want_ver):
        return "same_round", (f"· 버전 교차검증: 파일 {full} · 계산 {want_ver} "
                              "— 같은 회차의 다른 리비전입니다")
    return "mismatch", f"⚠ 버전 불일치: 파일 {full} ≠ 계산 {want_ver} — 확인 필요"


# ── EmKit SDK 고르기 ───────────────────────────────────────────────────────────
# 공용 저장소 project/Emkit/EmkitSDK/FormalRelease 의 폴더 이름은 '2026.02.19_v.3.41.09'.
# 그 안 sdk 폴더에 **SDK zip 이 없는 회차가 많다** — 실측 2026-10-13:
#   3.41.18 → intent-api.zip 만, 3.41.14 → zip 없음, 3.41.13 → javadoc.zip 만,
#   3.41.09 → v3.41.09.zip. 예전 '폴더에 zip 이 하나면 그것' 규칙은 intent-api.zip 이나
#   javadoc.zip 을 SDK 로 집었다. 그래서 SDK zip 은 이름으로 가려낸다.
_SDK_DIR_VER_RE = re.compile(r"_v\.(\d+(?:\.\d+)+)\s*$", re.I)
_SDK_ZIP_VER_RE = re.compile(r"^v(\d+(?:\.\d+)+)\.zip$", re.I)          # v3.41.09.zip
_SDK_ZIP_PROD_RE = re.compile(r"_sdk_v?(\d+(?:\.\d+)+)\.zip$", re.I)    # PM45_SDK_V3.29.01.zip


def is_sdk_zip(name: str) -> bool:
    """SDK 본체 zip 인가. intent-api.zip · javadoc.zip 같은 부속물은 아니다."""
    n = name.strip()
    return bool(_SDK_ZIP_VER_RE.match(n) or _SDK_ZIP_PROD_RE.search(n))


def _vtuple(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except ValueError:
        return ()


def sdk_dir_version(name: str) -> str:
    """'2026.02.19_v.3.41.09' → '3.41.09'. 못 읽으면 ''."""
    m = _SDK_DIR_VER_RE.search(name or "")
    return m.group(1) if m else ""


def sdk_nearest_order(want: str, versions: list[str]) -> list[str]:
    """요청 버전이 없을 때 대신 받을 순서(요청 버전 자신은 뺀다).

    **윗 버전을 먼저**, 그중 가장 가까운 것부터(사용자 확정 2026-10-13: "3.41.16 이 없으면
    3.41.18 을 받아야"). 윗 버전이 하나도 없을 때만 아랫 버전을 가까운 것부터.
    요청 3.41.16, 저장소 3.41.09 · 3.41.14 · 3.41.18 · 3.42.00 → 3.41.18 → 3.42.00 → 3.41.14 → 3.41.09.
    """
    w = _vtuple(want)
    if not w:
        return []
    ups, downs = [], []
    for v in versions:
        t = _vtuple(v)
        if not t or t == w:
            continue
        (ups if t > w else downs).append((t, v))
    return [v for _, v in sorted(ups)] + [v for _, v in sorted(downs, reverse=True)]


def sdk_folder_packable(names: list[str]) -> bool:
    """zip 이 없어도 SDK 를 묶어 낼 수 있는 폴더인가 — 본체(lib)가 풀린 채 들어 있다.

    3.41.14 · 3.41.18 처럼 저장소 회차 다수가 zip 없이 lib · javadoc · samples 만 둔다.
    정식 zip(v3.41.09.zip)도 이 sdk 폴더 내용을 그대로 묶은 것이다(실측 2026-10-13).
    """
    return any(n.lower() == "lib" for n in names)


def pack_sdk_folder(backend, sdk_dir: str, dest_zip: str, *, step=None) -> int:
    """파일서버의 sdk 폴더를 zip 하나로 묶는다. 맨 위의 *.zip(부속 묶음)은 넣지 않는다.

    정식 zip 과 같은 모양: 폴더 안 내용이 zip 의 맨 위에 온다(javadoc/ · lib/ · samples/ · *.pdf).
    돌려주는 값은 묶은 파일 수.
    """
    import zipfile

    count = 0
    tmp = dest_zip + ".part"
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        def walk(rel_dir: str, arc_prefix: str, top: bool) -> None:
            nonlocal count
            for e in backend.list_dir(rel_dir):
                if top and not e.is_dir and e.name.lower().endswith(".zip"):
                    continue
                src = f"{rel_dir}/{e.name}"
                arc = f"{arc_prefix}{e.name}"
                if e.is_dir:
                    zf.writestr(arc + "/", b"")
                    walk(src, arc + "/", False)
                else:
                    with backend.open_read(src) as fh:
                        zf.writestr(arc, fh.read())
                    count += 1
                    if step is not None and count % 50 == 0:
                        step(f"SDK 묶는 중… {count}개")

        walk(sdk_dir.rstrip("/\\"), "", True)
    os.replace(tmp, dest_zip)
    return count


def run_os_download(
    user_id: int, subject: str, input_params: str = "", mail_id: int | None = None
) -> StageOutput:
    lines: list[str] = []

    # 1) 제품 + Android
    progress.step("제품·Android 파악 중…")
    product, android = _resolve_product_android(user_id, mail_id, subject, input_params)
    if not product:
        lines.append("✘ 제품을 식별하지 못했습니다 — 트리거 메일(빌드지문 등)에서 제품을 찾지 못했습니다.")
        return StageOutput(result_text="\n".join(lines))
    lines.append(f"✔ 제품: {product} / Android {android or '?'}")
    if not android:
        lines.append("  ⚠ Android 미확정(A13/A15 구분 필요) — 폴더 탐색이 실패할 수 있습니다.")

    backend = get_backend(user_id)
    if backend is None:
        lines.append("✘ 파일 서버가 설정되지 않았습니다. [설정 → 파일 서버]에서 등록하세요.")
        return StageOutput(result_text="\n".join(lines))
    # 다운로드 경로 수동 지정(자동 탐색 실패 시 사용자가 채운 값) + RN 컴포넌트(SDK 버전).
    _overrides = {}
    _st_full = {}
    if mail_id is not None:
        from app.pipeline.state import get_state as _gs_ov

        _st_full = _gs_ov(mail_id) or {}
        # 저장된 값에 탐색기 '경로로 복사'의 따옴표가 붙어 있을 수 있다. 여기서 벗겨야
        # 한다 — 앞뒤만 벗기는 처리는 뒤에 파일 이름을 이어붙이는 순간 따옴표가 경로
        # **가운데**로 들어가 [Errno 22] 로 죽는다(실측 2026-09-09 job-15:
        # '…\2026.07.19_45.81-approved\sdcard"\45.81_20260719.PFU').
        from app.integrations.fileserver import clean_unc as _cu

        _overrides = {k: (_cu(v) if isinstance(v, str) else v)
                      for k, v in (_st_full.get("os_path_overrides") or {}).items()}
    lines.append(f"· 파일 서버: {getattr(backend, 'location', backend.kind)} · 기준폴더 '{_FS_ROOT}'")

    # 2) 정식 버전(SP/RN 출시본 + FIH 증분) — 폴더 탐색·교차검증 기준
    from app.mail import search as _MS
    from app.pipeline.version_resolve import resolve_release_version

    progress.step("정식 버전 판별 중…")
    trig_date = ""
    # 작업(job-N)은 메일 id 가 아니다 — 승인 메일이 붙어 있을 때만 그 날짜를 쓴다.
    _trig = mail_id if isinstance(mail_id, int) else (_st_full.get("approval_mail_id"))
    if isinstance(_trig, int):
        trig_date = (_MS.read_mail(user_id, _trig) or {}).get("received_at", "") or ""
    rv = resolve_release_version(user_id, product, android, trigger_date=trig_date)
    # 사용자가 버전을 직접 지정하면 그 값으로 폴더를 찾는다(자동 판별은 base_version 확보용으로만 유지).
    # 이 단계 입력이 비어 있으면, Submission 단계에서 지정한 버전(version_override)을 이어받는다.
    override_ver = _override_version(input_params)
    if not override_ver and mail_id is not None:
        from app.pipeline.state import get_state as _gs0

        override_ver = str((_gs0(mail_id) or {}).get("version_override") or "").strip()
    if override_ver:
        want_ver = override_ver
        auto = rv.get("version", "")
        extra = f" · 자동판별값 {auto}" if auto and auto != override_ver else ""
        lines.append(f"✔ 대상 버전(사용자 지정): {want_ver}{extra}")
    else:
        want_ver = rv["version"]
        if not want_ver:
            lines.append(f"✘ 정식 버전을 확정하지 못해 폴더를 특정할 수 없습니다. {rv.get('note', '')}")
            return StageOutput(result_text="\n".join(lines))
        lines.append(f"✔ 대상 버전(폴더 탐색용): {want_ver} — {rv['source']}")

    # 3) sdcard 퍼지 탐색
    from app.integrations.fota_server import parse_os_build, resolve_sdcard

    def _ld(p: str) -> list[str]:
        return [e.name for e in backend.list_dir(p)]

    progress.step("파일서버에서 OS 폴더 탐색 중…")
    # 제품에 'OS file 경로' 를 등록해 뒀으면 **그 아래 {버전}/sdcard** 로 바로 간다.
    # 폴더 이름이 제품마다 달라(실측: PM95 'Software\Android15', PM45 'Software\Images9')
    # 퍼지 탐색이 자주 빗나가는데, 등록값은 확정이라 헤맬 일이 없다.
    from app import os_products as _OP

    _reg = _OP.paths_for(product, android)
    if _overrides.get("sdcard"):
        sdcard = _overrides["sdcard"].rstrip("/")
        lines.append(f"✔ sdcard(수동 지정): {sdcard}")
    elif _reg.get("fs_os"):
        sdcard = f"{_reg['fs_os'].rstrip(chr(92) + '/')}\\{want_ver}\\sdcard"
        lines.append(f"✔ sdcard(제품 등록 경로): {sdcard}")
        try:
            backend.list_dir(sdcard)
        except Exception:  # noqa: BLE001  (등록 경로가 안 맞으면 기존 탐색으로 넘어간다)
            lines.append("  ⚠ 등록 경로에서 못 찾아 자동 탐색으로 전환합니다.")
            r = resolve_sdcard(_ld, product, android, want_ver, root=_FS_ROOT)
            if not r["ok"]:
                lines.append(f"✘ OS 파일 폴더 탐색 실패: {r['error']}")
                lines.append("  → [제품 관리]의 'OS file 경로' 를 확인하거나 sdcard 경로를 수동 지정하세요.")
                return StageOutput(result_text="\n".join(lines))
            sdcard = r["path"]
            lines.append(f"✔ sdcard: {sdcard}")
    else:
        r = resolve_sdcard(_ld, product, android, want_ver, root=_FS_ROOT)
        if not r["ok"]:
            lines.append(f"✘ OS 파일 폴더 탐색 실패: {r['error']}")
            lines.append("  → [제품 관리]에 'OS file 경로' 를 등록하거나 sdcard 경로를 수동 지정하세요.")
            return StageOutput(result_text="\n".join(lines))
        sdcard = r["path"]
        lines.append(f"✔ sdcard: {sdcard}")

    try:
        entries = backend.list_dir(sdcard)
    except Exception as e:  # noqa: BLE001
        lines.append(f"✘ sdcard 폴더 접근 실패({sdcard}): {e}")
        lines.append("  → '다운로드 경로 수동 지정'에서 sdcard 경로를 확인하고 재실행하세요.")
        return StageOutput(result_text="\n".join(lines))
    pfus = _classify_pfus(entries)
    # 공식 판에서 출발하는 DIFF 만 받는다(07C1 이 있으면 07 에서 가는 DIFF 는 뺀다).
    _keep, _drop = official_diffs([p["name"] for p in pfus if p["type"] == "DIFF"])
    if _drop:
        pfus = [p for p in pfus if p["type"] != "DIFF" or p["name"] in _keep]
        lines.append("· DIFF 제외(공식 판이 아닌 출발): " + ", ".join(_drop))
    if not pfus:
        lines.append("✘ sdcard 에 PFU 파일이 없습니다.")
        return StageOutput(result_text="\n".join(lines))
    full = pfus[0]
    n_diff = sum(1 for p in pfus if p["type"] == "DIFF")
    lines.append(f"✔ FULL: {full['name']} ({_fmt_size(full['size'])}) · DIFF {n_diff}건")

    # sdcard 의 체크섬 검증 도구(Verify_Checksum_Android.exe 등)도 함께 받는다.
    checksum = None
    _exes = [e for e in entries if e.name.lower().endswith(".exe")]
    _cs = [e for e in _exes if "checksum" in e.name.lower()] or _exes
    if _cs:
        c = _cs[0]
        checksum = {"name": c.name, "size": c.size, "path": ""}
        lines.append(f"✔ Checksum 도구: {c.name} ({_fmt_size(c.size)})")
    else:
        lines.append("  ⚠ sdcard 에 Checksum 실행파일(.exe)이 없습니다.")

    # flash 폴더(= sdcard 의 형제): 플래싱 툴 .zip 1개. 폴더명이 flash/fastboot 로 다를 수 있다.
    # 수동 지정(os_path_overrides.flash)이 있으면 그 경로를 쓴다.
    flash_zip = None
    flash_dir = _resolve_flash_dir(backend, sdcard, _overrides.get("flash", ""))
    if flash_dir:
        try:
            zips = [e for e in backend.list_dir(flash_dir) if e.name.lower().endswith(".zip")]
            if zips:
                fz = max(zips, key=lambda e: e.size)  # 여러 개면 가장 큰 것
                flash_zip = {"name": fz.name, "size": fz.size, "dir": flash_dir, "path": ""}
                lines.append(f"✔ flash zip: {fz.name} ({_fmt_size(fz.size)}) — {flash_dir}")
            else:
                lines.append(f"  ⚠ flash 폴더에 .zip 없음: {flash_dir}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"  ⚠ flash 폴더 접근 실패({flash_dir}): {e}")
    else:
        lines.append("  ⚠ flash/fastboot 폴더를 찾지 못했습니다(수동 경로 지정 필요).")

    # 4) 버전·날짜·빌드등급 파싱 + 교차검증
    build = parse_os_build(full["name"])
    file_ver, tier, fdate = build["version"], build["tier"], build["date"]
    _kind, _say = ver_crosscheck(file_ver, tier, want_ver)
    lines.append("  " + _say)
    _grade = tier or "(접미 없음)"
    if tier and build["is_release"]:
        _grade = f"{tier} (정식 C 리비전)"
    lines.append(f"  · 빌드 날짜: {fdate or '?'} · 등급: {_grade}")
    if not build["is_release"]:
        # C 리비전은 정식이다 — 걸리는 것은 A/B/S/RC 뿐이다.
        lines.append(f"  ⚠ 배포용 아님(빌드등급 {tier}) — 정식 배포는 접미 없는 빌드나 "
                     "C 리비전이어야 합니다")

    # 4-a) EmKit SDK 폴더/zip 도 다운로드 전에 미리 찾는다. SDK 버전은 ② RN(RN 승인자 입력)에서
    #      확정돼 있다(OS Download 는 Approval 이라 RN 이 이미 완료됨 · 짝 단계가 상태 공유).
    sdk_pick = None       # {dir, name, size, version}
    sdk_reason = ""
    sdk_ver = str((_st_full.get("rn_components") or {}).get("sdk")
                  or _overrides.get("sdk_version") or "").strip()

    def _sdk_in(sdk_dir: str, ver: str, *, any_zip: bool = False):
        """그 폴더에서 이 버전의 SDK zip 을 고른다. 없으면 None.

        이름 규칙이 두 가지다 — 공용 저장소는 'v3.41.09.zip', OS 빌드에 동봉된 것은
        'PM45_SDK_V3.29.01.zip' 이다. 버전이 이름에 든 것을 먼저 보고, 없으면 SDK 본체
        zip(is_sdk_zip) 중 하나를 쓴다. intent-api.zip · javadoc.zip 은 SDK 가 아니다.
        any_zip=True(사람이 폴더를 직접 지정한 경우)면 마지막에 아무 zip 이나 받는다.
        """
        zips = [e for e in backend.list_dir(sdk_dir) if e.name.lower().endswith(".zip")]
        if not zips:
            return None
        low = ver.lower()
        exact = [e for e in zips if e.name.lower() == f"v{low}.zip"]
        named = [e for e in zips if low in e.name.lower() and is_sdk_zip(e.name)]
        sdk_like = [e for e in zips if is_sdk_zip(e.name)]
        pk = exact or named or sdk_like or (zips if any_zip else [])
        if not pk:
            return None
        got = (_SDK_ZIP_VER_RE.match(pk[0].name) or _SDK_ZIP_PROD_RE.search(pk[0].name))
        return {"dir": sdk_dir, "name": pk[0].name, "size": pk[0].size,
                "version": got.group(1) if got else ver}

    def _sdk_take(sdk_dir: str, ver: str):
        """저장소 회차 폴더용: SDK zip 이 있으면 그것, 없고 본체가 풀려 있으면 묶어서 받는다."""
        got = _sdk_in(sdk_dir, ver)
        if got:
            return got
        names = [e.name for e in backend.list_dir(sdk_dir)]
        if sdk_folder_packable(names):
            return {"dir": sdk_dir, "name": f"v{ver}.zip", "size": 0, "version": ver, "pack": True}
        return None

    if not sdk_ver:
        sdk_reason = "RN 컴포넌트에 SDK 버전이 없음(Submission 의 RN 승인 화면에서 SDK 입력)"
    else:
        _tried: list[str] = []
        _sdk_over = _overrides.get("sdk", "")
        try:
            if _sdk_over:
                sdk_pick = _sdk_in(_sdk_over.rstrip("/\\"), sdk_ver, any_zip=True)
                if sdk_pick:
                    lines.append(f"✔ EmKit SDK 폴더(수동 지정): {_sdk_over}")
                else:
                    sdk_reason = f"수동 지정한 폴더에 zip 이 없습니다: {_sdk_over}"
            else:
                # ① 공용 EmKit SDK 저장소
                base = f"{_FS_ROOT}/Emkit/EmkitSDK/FormalRelease"
                _tried.append(base)
                _repo = _ld(base)
                cands = ([n for n in _repo if n.strip().endswith(f"_v.{sdk_ver}")]
                         or [n for n in _repo if sdk_ver in n])
                if cands:
                    try:
                        sdk_pick = _sdk_take(f"{base}/{sorted(cands)[-1]}/sdk", sdk_ver)
                    except Exception:  # noqa: BLE001  (sdk 폴더가 없는 회차)
                        sdk_pick = None
                    if sdk_pick is None:
                        _tried.append(f"{sorted(cands)[-1]} (SDK 없음)")
                # ② 없으면 **이번 회차 OS 빌드 폴더의 sdk**. 제품 SDK 는 빌드에 동봉돼
                #    나오고 공용 저장소에 안 올라가는 대(帶)가 있다 — 실측 2026-09-09:
                #    저장소는 3.34~3.41 만 있는데 PM45 는 3.29.01, PM67 은 3.43.02 다.
                if sdk_pick is None:
                    _bdir = re.sub(r"[\\/][^\\/]+$", "", (sdcard or "").rstrip("/\\"))
                    if _bdir:
                        _cand = f"{_bdir}\\sdk"
                        _tried.append(_cand)
                        try:
                            sdk_pick = _sdk_in(_cand, sdk_ver)
                        except Exception:  # noqa: BLE001  (sdk 폴더가 없는 빌드도 있다)
                            sdk_pick = None
                        if sdk_pick:
                            lines.append(f"✔ EmKit SDK 폴더(OS 빌드 동봉): {_cand}")
                # ③ 그래도 없으면 공용 저장소에서 **윗 버전 중 가장 가까운 것**(사용자 확정
                #    2026-10-13 "3.41.16 이 없으면 3.41.18"). 윗 버전이 없을 때만 아랫 버전.
                #    zip 이 없는 회차는 sdk 폴더를 묶어서 받는다.
                if sdk_pick is None:
                    _by_ver: dict[str, str] = {}
                    for n in _repo:
                        v = sdk_dir_version(n)
                        if v and (v not in _by_ver or n > _by_ver[v]):
                            _by_ver[v] = n
                    for v in sdk_nearest_order(sdk_ver, list(_by_ver)):
                        try:
                            sdk_pick = _sdk_take(f"{base}/{_by_ver[v]}/sdk", v)
                        except Exception:  # noqa: BLE001
                            sdk_pick = None
                        if sdk_pick:
                            _dir = "윗" if _vtuple(v) > _vtuple(sdk_ver) else "아랫"
                            lines.append(f"✔ EmKit SDK: 요청 {sdk_ver} 이 저장소에 없어 "
                                         f"{_dir} 버전 {sdk_pick['version']} 을 씀 ({_by_ver[v]})")
                            break
                if sdk_pick is None:
                    sdk_reason = (f"SDK 버전 {sdk_ver} 를 찾지 못함 — 찾아본 곳: "
                                  + " · ".join(_tried))
        except Exception as e:  # noqa: BLE001
            sdk_reason = f"SDK 폴더 접근 실패(v{sdk_ver}): {e}"

    # 4-b) 사전 확인 — 다운로드 전에 7개 파일 경로가 모두 접근 가능한지 본다.
    #      하나라도 없으면 부분 다운로드하지 않고 중단해, 사용자가 경로를 수동 지정하게 한다.
    _sd_names = {en.name for en in entries}
    missing: list[str] = []
    for p in pfus:
        if p["md5"] and p["md5"] not in _sd_names:
            missing.append(f"sdcard/{p['md5']} (md5)")
    if checksum is None:
        missing.append("sdcard/Checksum(.exe)")
    if flash_zip is None:
        missing.append("flash/fastboot zip")
    if sdk_pick is None:
        missing.append(f"EmKit SDK zip — {sdk_reason}")
    if missing:
        lines.append("")
        lines.append("✘ 접근할 수 없는 파일이 있어 다운로드를 중단했습니다:")
        for m in missing:
            lines.append(f"   · {m}")
        lines.append("→ Release Note 제작 아래 '다운로드 경로 수동 지정'에서 해당 폴더 경로를 넣고 재실행하세요.")
        return StageOutput(result_text="\n".join(lines))

    # 5) Full + Diff PFU(+각 md5) 다운로드 → ③ 업로드용으로 상태 기록
    workdir = _WORK_ROOT / f"{user_id}_{datetime.now():%Y%m%d-%H%M%S}"
    downloaded: dict[str, str] = {}
    for p in pfus:
        for name in (p["name"], p["md5"]):
            if not name or name in downloaded:
                continue
            try:
                progress.step(f"다운로드 중… {name} ({_fmt_size(p['size'])})"
                              if name == p["name"] else f"다운로드 중… {name}")
                dst, _ = backend.download(f"{sdcard}/{name}", str(workdir))
                downloaded[name] = dst
            except Exception as e:  # noqa: BLE001
                lines.append(f"  ✘ 다운로드 실패 {name}: {e}")
    fota_source = [{
        "type": p["type"], "name": p["name"], "size": p["size"],
        "path": downloaded.get(p["name"], ""),
        "md5_name": p["md5"], "md5_path": downloaded.get(p["md5"], ""),
    } for p in pfus]
    # Checksum 실행파일도 sdcard 에서 함께 내려받는다.
    if checksum:
        try:
            progress.step(f"다운로드 중… {checksum['name']}")
            dst, _ = backend.download(f"{sdcard}/{checksum['name']}", str(workdir))
            checksum["path"] = dst
            downloaded[checksum["name"]] = dst
        except Exception as e:  # noqa: BLE001
            lines.append(f"  ✘ Checksum 다운로드 실패 {checksum['name']}: {e}")
            checksum = None

    os_image_path = downloaded.get(full["name"], "")
    lines.append(f"✔ 다운로드 {len(downloaded)}개 → {workdir}")

    # flash zip 다운로드(③ Dropbox SupportPotal 업로드용)
    if flash_zip:
        try:
            progress.step(f"다운로드 중… {flash_zip['name']} ({_fmt_size(flash_zip['size'])})")
            dst, _ = backend.download(f"{flash_dir}/{flash_zip['name']}", str(workdir))
            flash_zip["path"] = dst
        except Exception as e:  # noqa: BLE001
            lines.append(f"  ✘ flash zip 다운로드 실패: {e}")
            flash_zip = None

    # 6) 컴포넌트(보안패치·GMS·SDK 등)는 PFU 에서 추출하지 않는다.
    #    전부 ② RN 에서 RN 승인자가 입력·확인한다. SDK 버전만 EmKit zip 다운로드에 쓴다.
    comp_auto: dict = {}
    security_patch = ""

    # 7) EmKit SDK zip 다운로드 — 4-a 에서 미리 찾아둔 sdk_pick 을 받는다(preflight 통과 = 존재 보장).
    sdk_zip = None
    if sdk_pick:
        try:
            if sdk_pick.get("pack"):
                # zip 없는 회차 — sdk 폴더를 정식 zip 과 같은 모양으로 묶는다.
                progress.step(f"SDK 폴더 받아 묶는 중… {sdk_pick['name']}")
                dst = str(Path(workdir) / sdk_pick["name"])
                _n = pack_sdk_folder(backend, sdk_pick["dir"], dst, step=progress.step)
                sdk_pick["size"] = os.path.getsize(dst)
                lines.append(f"✔ EmKit SDK: 저장소에 zip 이 없어 sdk 폴더 {_n}개 파일을 "
                             f"{sdk_pick['name']} 로 묶음")
            else:
                progress.step(f"다운로드 중… {sdk_pick['name']} ({_fmt_size(sdk_pick['size'])})")
                dst, _ = backend.download(f"{sdk_pick['dir']}/{sdk_pick['name']}", str(workdir))
            sdk_zip = {"name": sdk_pick["name"], "size": sdk_pick["size"],
                       "dir": sdk_pick["dir"], "path": dst, "version": sdk_pick["version"],
                       "requested": sdk_ver}
            lines.append(f"✔ EmKit SDK: {sdk_pick['name']} ({_fmt_size(sdk_pick['size'])})")
        except Exception as e:  # noqa: BLE001
            lines.append(f"  ✘ EmKit SDK 다운로드 실패: {e}")

    version = file_ver or want_ver
    if mail_id is not None:
        from app.pipeline.state import update_state

        update_state(
            mail_id,
            product=product, android=android,
            os_version=version, release_version=version,
            prev_version=rv.get("base_version", ""),   # ⑤⑥ 편집이 문서에서 교체할 직전 버전
            # FIH Approval 승인일 = 이번 릴리스의 공식 날짜.
            # RN·Knowledge Base·Service Portal 표기와 배포 zip 파일명이 모두 이 날짜를 쓴다.
            approval_date=trig_date,
            os_image_path=os_image_path, os_image_name=full["name"],
            os_build_date=fdate, os_build_tier=tier, os_is_release=build["is_release"],
            security_patch=security_patch, comp_auto=comp_auto,
            fota_source=fota_source,
            flash_zip=flash_zip,   # ③ SupportPotal 업로드 + ⑤ 4C 편집용
            checksum_tool=checksum,   # sdcard 체크섬 검증 도구
            sdk_zip=sdk_zip,          # EmKit SDK zip (③ 업로드·⑤ 편집에서 사용)
        )

    # ── 간단 보고: 몇 개 · 어디서 · 무엇을 받았는지. (경고/오류만 아래에 따로) ──
    n_flash = 1 if (flash_zip and flash_zip.get("path")) else 0
    n_sdk = 1 if (sdk_zip and sdk_zip.get("path")) else 0
    total = len(downloaded) + n_flash + n_sdk      # downloaded 에 Checksum 포함
    file_list = [
        f"  · {p['type']}: {p['name']} ({_fmt_size(p['size'])})"
        + (f" + {p['md5']}" if p["md5"] else "")
        for p in pfus
    ]
    if checksum and checksum.get("path"):
        file_list.append(f"  · CHECKSUM: {checksum['name']} ({_fmt_size(checksum['size'])})")
    if flash_zip and flash_zip.get("path"):
        file_list.append(f"  · FLASH(zip): {flash_zip['name']} ({_fmt_size(flash_zip['size'])})")
    if sdk_zip and sdk_zip.get("path"):
        file_list.append(
            f"  · EMKIT SDK: {sdk_zip['name']} ({_fmt_size(sdk_zip['size'])})")

    location = getattr(backend, "location", None) or backend.kind
    lines_out = [
        f"[OS Download] {product} A{android or '?'} · {version}"
        + (f" ({tier} 비배포)" if tier and not build["is_release"] else ""),
        f"· 어디서 : {location} — {sdcard}",
        f"· 무엇을 : 파일 {total}개",
        *file_list,
        f"· 저장   : {workdir}",
    ]
    if security_patch or comp_auto:
        _auto = ", ".join(filter(None, [
            f"보안패치 {security_patch}" if security_patch else "",
            ", ".join(f"{k}={v}" for k, v in comp_auto.items()),
        ]))
        if _auto:
            lines_out.append(f"· 자동추출: {_auto}")

    # 흐름 로그(lines) 중 경고/오류만 추려 맨 아래에.
    notes = [ln.strip() for ln in lines if ("⚠" in ln or "✘" in ln)]
    if notes:
        lines_out.append("")
        lines_out.append("⚠ 확인 필요:")
        lines_out += [f"  {n}" for n in notes]

    return StageOutput(result_text="\n".join(lines_out),
                       artifact_type="file", artifact_path=os_image_path)
