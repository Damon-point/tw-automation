# -*- coding: utf-8 -*-
"""전체 이력 RN 제작 — 이번 회차의 **최신 Beta OS** 에서 입력값 세 개를 읽는다.

RN 승인자가 손으로 채우던 값 중 **모델 불문 같은 규칙으로 뽑히는 것만** 가져온다
(사용자 확정 2026-09-01). 안드로이드 표준 프로퍼티라 제품·세대가 달라도 키가 같다.

    Android security patch level  ro.build.version.security_patch   28/28 제품
    SDK                           ro.vendor.build.version.emkit     23/28
    GMS Package                   ro.com.google.gmsversion          23/28

**뽑지 않는 것**: Audio·Camera·Wi-Fi·Scanner·Touch. 자리와 이름이 모델마다 달라
규칙이 서지 않는다(실측: acdb_version 은 28개 중 PM45 하나, 카메라 .ver 은 센서
이름이 곧 파일명 — s5k3l8/gc05a2/ov13b10/gc08a3/gc13a0/hi1332). 있는 척 뽑으면
엉뚱한 값이 문서로 나간다. Wi-Fi 는 파일이 있어도 내용이 갈린다(PM45 는 DRIVER·
FIRMWARE·CONFIGURATION 셋, PM86·PM84P 는 CONFIGURATION 하나뿐).

값이 없는 제품은 **그냥 빈 채로 둔다**. A9 세대는 PRODUCT 파티션이 없어 GMS 값이
이미지 안에 아예 없고(g.prop 도 없음), A7·A8 은 EmKit 프로퍼티가 생기기 전이다.

경로는 제품 관리에 등록된 `os_products.os_file_path` 를 쓴다 — 퍼지 탐색을 하지
않는다(등록된 33개가 정답이고, PM85 A9 처럼 `…/FormalRelease/MADA` 로 한 단계 더
들어가는 제품도 있어 추측하면 틀린다).

파일서버는 **읽기만** 한다. 내려받지 않는다 — open_read 로 zip 목록과 build.prop 만 읽는다.
"""
from __future__ import annotations

import re
import zipfile

#: 뽑을 값 — 내부 키 → build.prop 프로퍼티 이름.
PROP_KEYS: dict[str, str] = {
    "security_patch": "ro.build.version.security_patch",
    "sdk": "ro.vendor.build.version.emkit",
    "gms_package": "ro.com.google.gmsversion",
    # 입력 항목은 아니지만 버전·날짜 대조에 쓴다 — '45.81 B1 (Jul 07 2026)'.
    "display_id": "ro.build.display.id",
}

#: RN 입력 항목으로 채우는 것(display_id 는 참고용이라 뺀다).
FILL_KEYS = ("security_patch", "sdk", "gms_package")

# 버전 뒤에 붙는 판 표시. 86.07C8 → 86.07, TR54G15.04B2 → TR54G15.04.
_SUFFIX_RE = re.compile(r"\s*[BC]\d+\s*$", re.I)


def base_version(version: str) -> str:
    """리비전·베타 표시를 뗀 기준 버전. '86.07C8' → '86.07'."""
    return _SUFFIX_RE.sub("", str(version or "").strip())


#: 폴더 이름 앞의 날짜 — '2026.07.07_45.81B1-MR준비'.
_DATE_RE = re.compile(r"^\s*(20\d{2})[.\-_](\d{2})[.\-_](\d{2})")


def _folder_date(name: str) -> str:
    """정렬용 날짜 'YYYYMMDD'. 날짜가 없으면 '' (가장 뒤로 밀린다)."""
    m = _DATE_RE.match(name or "")
    return f"{m.group(1)}{m.group(2)}{m.group(3)}" if m else ""


def _matches_version(folder: str, base: str) -> bool:
    """이 회차의 폴더인가.

    앞뒤로 숫자가 이어지면 다른 버전이다 — '45.81' 이 '45.810'·'145.81' 을 잡으면 안 된다.
    뒤에 B7·C8·'-approved' 가 붙는 것은 같은 회차의 판이므로 모두 받는다.
    """
    return bool(re.search(r"(?<![\d.])" + re.escape(base) + r"(?![0-9])", folder, re.I))


#: 반려본 — 이름 어디에 있어도 걸러낸다('2026.07.03_67.09_rejected' 는 날짜로 시작한다).
_REJECT_RE = re.compile(r"reject", re.I)
#: 승인본 표시 — '45.81-approved' · '67.09_MR_approved' · 'PM84PG15.03-MR-approved'.
_APPROVED_RE = re.compile(r"approved", re.I)


def _matches_exact(folder: str, version: str) -> bool:
    """**딱 이 버전**의 폴더인가 — 뒤에 판 표시(B1·C1)가 붙으면 다른 회차다.

    '95.07' 이 '95.07C1-approved' 를 잡으면 안 된다. C1 은 별개 회차이고 날짜도 다르다.
    반대로 '95.07C1' 은 그 폴더를 잡아야 한다.
    """
    v = (version or "").strip()
    if not v:
        return False
    return bool(re.search(r"(?<![\d.])" + re.escape(v) + r"(?![0-9])(?![BbCc]\d)",
                          folder, re.I))


def pick_os_folder(names: list[str], version: str) -> str:
    """이 회차의 OS 폴더 하나를 고른다 — **승인본이 있으면 승인본**.

    이 폴더 이름이 곧 RN 머리의 날짜가 된다('67.09 (Jul 08 2026)'). 그래서 아무 판이나
    집으면 안 된다. 실측 2026-09-09:
      · PM67 67.09 — `2026.07.03_67.09_rejected` 와 `2026.07.08_67.09_MR_approved`
        가 함께 있다. 예전 걸러내기는 이름이 'reject' 로 **시작**할 때만 빼서 반려본이
        후보에 남았다.
      · PM95 95.07C1 — `2026.07.10_95.07C1-approved` 가 정본인데, 기준 버전 '95.07'
        로만 훑어 `2026.08.27_95.07B19_#33526_ramdump` 를 집었다. 날짜가 더 늦다는
        이유였다.

    그래서 순서를 둔다:
      ① 반려본은 이름 어디에 있든 뺀다.
      ② **버전 전체**(95.07C1)와 맞는 폴더가 있으면 그것들만 본다. 없으면 기준
         버전(95.07)으로 넓힌다.
      ③ 그중 **승인본**이 있으면 승인본 중 가장 늦은 것. 없으면(아직 제작 중인 회차)
         예전처럼 가장 늦은 판.
    """
    base = base_version(version)
    if not base:
        return ""
    live = [n for n in names if not _REJECT_RE.search(n or "")]
    full = (version or "").strip()
    pool = [n for n in live if _matches_exact(n, full)]
    if not pool:
        pool = [n for n in live if _matches_version(n, base)]
    if not pool:
        return ""
    latest = lambda xs: max(xs, key=lambda n: (_folder_date(n), n))  # noqa: E731
    approved = [n for n in pool if _APPROVED_RE.search(n)]
    return latest(approved) if approved else latest(pool)


#: 빌드 파일 이름 안의 날짜 — '67.09_20260708-target_files.zip'.
_BUILD_DATE_RE = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")


def build_date_of(folder: str, files: list[str] | None = None) -> str:
    """이 OS 판의 **빌드 날짜** 'YYYYMMDD'. 못 찾으면 ''.

    OS 빌드 이름에 적힌 날짜를 **그대로** 쓴다 — 고치지 않는다(사용자 확정 2026-09-09:
    "os명의 날짜를 수정하지말고 그대로 사용해야됩니다"). 빌드 파일 이름을 먼저 보고
    (`67.09_20260708-target_files.zip`), 거기 날짜가 없으면 폴더 이름 앞의 날짜를 쓴다
    (PM45 는 `pm45-target_files-vAQAJ-2.zip` 처럼 날짜가 없다).
    """
    for n in (files or []):
        m = _BUILD_DATE_RE.search(n or "")
        if m:
            return "".join(m.groups())
    return _folder_date(folder)


def _find_target_files(entries: list[str]) -> str:
    """etc 폴더의 target_files zip 이름. 제품마다 이름이 갈린다.

    '95.52_20260718-target_files.zip' · 'pm45-target_files-vAQAJ-2.zip'
    """
    for n in entries:
        low = n.lower()
        if "target_files" in low and low.endswith(".zip"):
            return n
    return ""


def read_props(be, tf_rel: str) -> dict:
    """target_files zip 안의 **모든 build.prop** 을 훑어 값을 모은다.

    자리가 세대마다 다르다 — GMS 는 A15·A13 이 PRODUCT/etc/build.prop, A11 은
    PRODUCT/build.prop, A7 은 SYSTEM/build.prop 이다. 그래서 파일을 고정하지 않고
    build.prop 을 전부 읽어 **먼저 찾은 값**을 쓴다.
    """
    out: dict = {}
    with be.open_read(tf_rel) as fh:
        z = zipfile.ZipFile(fh)
        props = [n for n in z.namelist() if n.lower().endswith("build.prop")]
        for name in props:
            try:
                text = z.read(name).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                continue
            for line in text.splitlines():
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip()
                for out_key, prop in PROP_KEYS.items():
                    if key == prop and out_key not in out and val:
                        out[out_key] = val
    return out


def os_root_for(product: str, android: str) -> str:
    """제품 관리에 등록된 OS 폴더 경로. 없으면 ""."""
    from app.database import get_session
    from app.models import OsProduct

    prod = str(product or "").strip()
    andr = str(android or "").strip().lstrip("Aa")
    if not prod:
        return ""
    with get_session() as db:
        q = db.query(OsProduct).filter(OsProduct.product == prod)
        row = q.filter(OsProduct.android == andr).first() if andr else None
        if row is None:
            row = q.first()
        return str(getattr(row, "os_file_path", "") or "") if row else ""


def props_for(user_id, product: str, android: str, version: str) -> dict:
    """이번 회차의 최신 Beta OS 에서 세 값을 읽는다.

    반환 {values:{...}, folder, source, error}. 못 읽어도 예외를 올리지 않는다 —
    값이 없으면 그 항목은 비워 두고, 나머지 제작은 그대로 진행한다.
    """
    res: dict = {"values": {}, "folder": "", "source": "", "error": "",
                 "build_date": ""}
    root = os_root_for(product, android)
    if not root:
        res["error"] = f"제품 관리에 {product} A{android} 의 OS 경로가 없습니다."
        return res

    from app.integrations.fileserver import get_backend

    be = get_backend(user_id, "fs104")
    if be is None:
        res["error"] = "파일서버 자격증명이 없습니다."
        return res
    try:
        names = [e.name for e in be.list_dir(root) if e.is_dir]
    except Exception as e:  # noqa: BLE001
        res["error"] = f"OS 폴더를 열지 못했습니다: {e}"
        return res

    folder = pick_os_folder(names, version)
    if not folder:
        res["error"] = f"{base_version(version)} 의 OS 폴더가 없습니다."
        return res
    res["folder"] = folder

    vdir = f"{root}\\{folder}"
    try:
        subs = {e.name.lower(): e.name for e in be.list_dir(vdir) if e.is_dir}
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{folder} 를 열지 못했습니다: {e}"
        return res
    if "etc" not in subs:
        res["error"] = f"{folder} 에 etc 폴더가 없습니다."
        return res
    try:
        files = [e.name for e in be.list_dir(f"{vdir}\\{subs['etc']}") if not e.is_dir]
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{folder}/etc 를 열지 못했습니다: {e}"
        return res
    # 빌드 날짜는 여기서 잡는다 — RN 머리의 '(Jul 08 2026)' 이 이 값이다.
    res["build_date"] = build_date_of(folder, files)
    tf = _find_target_files(files)
    if not tf:
        res["error"] = f"{folder}/etc 에 target_files zip 이 없습니다."
        return res

    try:
        res["values"] = read_props(be, f"{vdir}\\{subs['etc']}\\{tf}")
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{tf} 를 읽지 못했습니다: {e}"
        return res
    res["source"] = f"{folder}/{subs['etc']}/{tf}"
    return res
