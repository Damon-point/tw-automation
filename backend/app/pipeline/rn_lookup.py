"""② RN 제작 전 — 파일서버에 **이미 만들어진 RN**이 있는지 찾고, 있으면 내용을 뽑아온다.

경로: `\\\\192.168.123.104\\Project\\SQA\\Documents\\ReleaseNotes\\{제품}\\[Android{N}\\]{파일}.pdf`

실측(2026-07-23, 제품 폴더 54개)으로 확인한 구조:
  · 레이아웃 3종 — 제품폴더 직접형(29) / `Android{N}` 하위폴더형(21) / 혼합형(4)
  · 하위폴더 표기 흔들림 — `Android11/13/15/9` · `Android` · `Android_N/_O/_P` · `Android_O_GMS` · `CE`/`WEH`
  · 파일명 흔들림 — `PM95_Release_Note_95.04.pdf` · `PM95_Android15_Release_Note_95.51.pdf`
    · `PM67_Android_Release_Note_67.08_20250624.pdf`(날짜접미) · `PM67_A13_Release_Note_67.50.pdf`
  · 모든 문서는 `.doc` + `.pdf` 쌍

탐색 규칙(사용자 확정):
  1) `{제품}\\Android{N}` → 2) `{제품}` 루트 → 3) 그 외 `Android*` 폴더 순으로 훑는다.
  2) **변형 모델 파일은 제외** — 제품코드 뒤에 다른 모델코드가 붙은 것(`PM67_XM75P_…`)은 그 모델 전용.
읽기 전용 — 파일서버를 고치지 않는다.
"""
from __future__ import annotations

import os
import re

RN_BASE = r"Project\SQA\Documents\ReleaseNotes"
_TARGET = "fs104"

# 제품코드 뒤에 오는 토큰이 '다른 모델'이면 변형 모델 문서(PM67_XM75P_… → XM75P 전용).
_MODEL_TOKEN_RE = re.compile(r"^(?:PM|XM|MX|TR|PT|PS|RF|MF)\s?\d", re.I)


def _backend(user_id: int):
    from app.integrations.fileserver import get_backend

    return get_backend(user_id, _TARGET)


def backend_for_path(user_id: int, path: str):
    """경로가 가리키는 **호스트에 맞는** 백엔드. UNC 가 아니면 기본(fs104).

    제품마다 등록 경로의 서버가 다르다 — 개발 RN 은 .104, RN for Sales 는 .160 이다.
    백엔드를 .104 로 고정하면 .160 경로의 host 세그먼트가 공유 이름으로 오인돼
    'WNetAddConnection2: 네트워크 이름을 찾을 수 없습니다' 로 실패한다(실측 2026-08-04).
    """
    from app.integrations.fileserver import FS_TARGETS, clean_unc, get_backend

    # 따옴표가 붙은 채로 오면 head 가 '"' 가 되어 어떤 서버와도 안 맞고, 기본(.104)로
    # 떨어진다 — .160 경로였다면 엉뚱한 서버에 묻게 된다.
    s = clean_unc(path).replace("/", "\\").strip("\\")
    head = s.split("\\", 1)[0].lower() if s else ""
    for name, cfg in FS_TARGETS.items():
        if (cfg.get("host") or "").lower() == head:
            return get_backend(user_id, name)
    return _backend(user_id)


def is_variant_file(name: str, product: str) -> bool:
    """이 파일이 **다른(변형) 모델 전용** RN 인가. 제외 대상이면 True.

    'PM67_XM75P_Android_Release_Note_67.01…' → XM75P 전용 → True
    'PM67_Android_Release_Note_67.08…' · 'PM67_A13_Release_Note_67.50' → False
    """
    stem = os.path.splitext(name or "")[0]
    p = (product or "").strip()
    if not p or not stem.upper().startswith(p.upper()):
        return True                       # 이 제품 문서가 아님
    rest = stem[len(p):].lstrip("_- ")
    tok = re.split(r"[_\-\s]", rest)[0] if rest else ""
    return bool(_MODEL_TOKEN_RE.match(tok))


def version_in_name(name: str, version: str) -> bool:
    """파일명에 그 버전이 **경계 있게** 들어있는가('67.09' 가 '67.091' 에 걸리지 않게)."""
    v = (version or "").strip()
    if not v:
        return False
    return bool(re.search(rf"(?<![\d.]){re.escape(v)}(?![\d])", name or ""))


def _android_dirs(dirs: list[str], android: str) -> list[str]:
    """탐색 우선순위대로 하위폴더 이름 정렬: 정확한 Android{N} → 그 외 Android*."""
    a = str(android or "").strip()
    exact = [d for d in dirs if a and re.fullmatch(rf"Android\s*{re.escape(a)}", d, re.I)]
    other = [d for d in dirs if d not in exact and d.lower().startswith("android")]
    return exact + other


def _pick_from(files, where: str, product: str, version: str) -> dict | None:
    """폴더의 파일 목록에서 그 버전의 RN(pdf) 하나를 고른다. 없으면 None.

    변형 모델 문서(PM67_XM75P_…)는 제외하고, 같은 버전이 여럿이면 이름 마지막(대개 최신 날짜).

    **찾는 기준은 파일명 규칙이 아니라 '그 버전이 이름에 들어있는가' 다.** 2026-08-04 부터
    생성 규칙을 `{제품}_Android{N}_Release_Note_{버전}` 으로 고정했지만, 과거 문서는
    폴더마다 다른 관례로 쌓여 있다(`PM95_Release_Note_95.04` · `PM45(P)_…`). 버전으로
    찾으므로 신규 규칙분과 과거분이 **모두** 잡힌다.
    """
    cands = [f for f in files
             if f.name.lower().endswith(".pdf")
             and version_in_name(f.name, version)
             and not is_variant_file(f.name, product)]
    if not cands:
        return None
    cands.sort(key=lambda f: f.name)          # 결정적 선택
    f = cands[-1]
    return {"rel": f"{where}\\{f.name}", "name": f.name, "size": f.size, "where": where}


def find_existing_rn(user_id: int, product: str, android: str, version: str) -> dict | None:
    """이미 만들어진 RN(pdf)을 찾는다. 없으면 None.

    반환 {rel, name, size, where} — rel 은 파일서버 상대경로(다운로드용).

    제품에 '개발 Release Note 파일 경로' 를 등록해 뒀으면 **그 폴더만** 본다.
    폴더 이름이 제품마다 달라(Android15 · Android_P · CE …) 아래 탐색 규칙이
    빗나가는 경우가 있는데, 등록값은 확정이라 헤맬 일이 없다.
    """
    b = _backend(user_id)
    if b is None or not (product or "").strip() or not (version or "").strip():
        return None

    from app import os_products as _OP

    _reg_dir = (_OP.paths_for(product, android).get("fs_dev_rn") or "").strip()
    if _reg_dir:
        try:
            _rb = backend_for_path(user_id, _reg_dir) or b
            hit = _pick_from(_rb.list_dir(_reg_dir), _reg_dir, product, version)
        except Exception:  # noqa: BLE001  (등록 경로가 안 맞으면 아래 자동 탐색으로)
            hit = None
        if hit:
            return hit

    prod_dir = f"{RN_BASE}\\{product}"
    try:
        kids = b.list_dir(prod_dir)
    except Exception:  # noqa: BLE001  (제품 폴더 자체가 없음)
        return None

    subdirs = [k.name for k in kids if k.is_dir]
    root_files = [k for k in kids if not k.is_dir]

    def _pick(files, where: str) -> dict | None:
        return _pick_from(files, where, product, version)

    # 1) Android{N} → 2) 제품 루트 → 3) 그 외 Android* 폴더
    order = _android_dirs(subdirs, android)
    for d in order[:1]:                            # 정확한 Android{N} 먼저
        try:
            hit = _pick(b.list_dir(f"{prod_dir}\\{d}"), f"{prod_dir}\\{d}")
        except Exception:  # noqa: BLE001
            hit = None
        if hit:
            return hit
    hit = _pick(root_files, prod_dir)               # 제품 루트
    if hit:
        return hit
    for d in order[1:]:                             # 나머지 Android* 폴더
        try:
            hit = _pick(b.list_dir(f"{prod_dir}\\{d}"), f"{prod_dir}\\{d}")
        except Exception:  # noqa: BLE001
            continue
        if hit:
            return hit
    return None


# 뒷자리에 붙는 **C 리비전까지** 버전의 일부로 읽는다. `81.18` 과 `81.18C1` 은 서로
# 다른 문서이므로, 접미어를 떼면 개발 RN 쪽 표기(81.18C1)와 어긋난다.
_NAME_VER_RE = re.compile(r"\d+\.\d+(?:[A-Z]\d*)?")
_VER_PARTS_RE = re.compile(r"^(\d+)\.(\d+)([A-Z])?(\d*)$")
_TRAIL_DATE_RE = re.compile(r"_\d{6,8}$")


def standard_name(product: str, android: str, version: str,
                  suffix: str = "") -> str:
    """**표준 파일명**(확장자 없음) — `{제품}_A{N}_Release_Note_{버전}`.

    사용자 확정(2026-08-04): 앞으로 시스템이 만드는 문서는 폴더 관례를 따라가지 않고
    이 규칙으로 고정한다. 제목에 Android 를 넣는 이유는 대부분 모델이 OS 업그레이드를
    제공해 버전만으로는 세대가 구분되지 않기 때문이다. 표기는 'A13' 형태로 짧게 쓴다
    (파일서버에 이미 `PM67_A13_Release_Note_67.50` 관례가 있다).

    suffix 는 RN for Sales 처럼 뒤에 붙는 꼬리(' for Sales').
    Android 를 모르면 그 토막은 빼고 만든다(억지로 지어내지 않는다).
    """
    p = (product or "").strip()
    a = "".join(c for c in str(android or "") if c.isdigit())
    mid = f"_A{a}" if a else ""
    return f"{p}{mid}_Release_Note_{(version or '').strip()}{suffix}"


def name_for_version(existing: list[str], product: str, version: str,
                     default_suffix: str = "") -> str:
    """그 폴더의 **기존 파일명 규칙**으로 이번 버전의 이름(확장자 없음)을 만든다.

    관례가 폴더마다 다르다(실측):
      PM45\\Android_P   → 'PM45(P)_Release_Note_45.76'
      PM95\\Android15   → 'PM95_Android15_Release_Note_95.52'
      PM95\\Android13   → 'PM95_Release_Note_95.04'
    그래서 이름을 지어내지 않고, 가장 높은 버전 파일의 이름에서 버전만 갈아 끼운다.
    참고할 파일이 없으면 가장 흔한 형태(`{제품}_Release_Note_{버전}`)로 폴백한다.

    default_suffix 는 **그 폴백에만** 붙는다(예: ' for Sales'). 폴더가 비어 있을 때
    전체 이력본과 이름이 같아지지 않게 하려는 것이고, 기존 파일이 하나라도 있으면
    그 폴더의 관례가 이긴다.
    """
    from app.pipeline.dev_rn import _VER_RE, parse_version
    from app.pipeline.version_resolve import _ver_key

    def _swap(old_tok: str) -> str:
        """기존 파일명의 버전 토큰을 이번 버전으로 바꾼다.

        토큰 **전체**(major.minor+접미)를 갈아 끼운다. 예전에는 숫자 부분만 바꿔서
        'PM84PG15.02C1' → 'PM84PG15.03C1' 처럼 **옛 리비전 C1 이 그대로 남았고**,
        형식 2 버전을 그대로 넣으면 'PM84PGPM84PG15.03' 처럼 접두가 겹쳤다(실측).
        """
        o, n = parse_version(old_tok), parse_version(version)
        if o is None or n is None:
            return version
        # 형식 1 표기('15.03')를 형식 2 폴더에 쓸 때는 그 폴더의 major 를 살린다
        # ('PM84PG15' 가 '15' 로 끝나면 같은 계열 — dev_rn._same_series 와 같은 기준).
        major = n.major
        if n.major.isdigit() and not o.major.isdigit() and o.major.upper().endswith(n.major):
            major = o.major
        tail = n.text[len(n.major):]        # '.03C1' — 자릿수·접미 그대로
        return major + tail

    best: tuple[tuple, str] | None = None
    for name in existing or []:
        stem = os.path.splitext(name)[0]
        if is_variant_file(name, product):
            continue
        m = None
        for m in _VER_RE.finditer(stem):    # 마지막 매치 = 파일명 뒤쪽의 버전
            pass
        if m is None:
            continue
        key = _ver_key(m.group(0))
        if best is None or key > best[0]:
            best = (key, stem[:m.start()] + _swap(m.group(0)) + stem[m.end():])
    if best is None:
        return f"{product}_Release_Note_{version}{default_suffix}"
    # 'PM67_Android_Release_Note_67.08_20250624' 처럼 날짜 꼬리가 붙은 관례는 떼어낸다
    # (그 날짜는 그 판의 것이라 이번 판에 그대로 달면 틀린 정보가 된다).
    return _TRAIL_DATE_RE.sub("", best[1])


# 파일명 꼬리에 붙는 **날짜**를 버전으로 잘못 읽지 않기 위한 형태.
# `PM351_..._35.04_2024.09.12.pdf` 에서 2024.09 를, `..._90.13_2022.08.31` 에서
# 2022.08 을 집던 것을 막는다(점 없는 `20230825` 는 원래 걸리지 않았다).
_DATEISH_RE = re.compile(r"^(19|20)\d{2}$")


def version_window(product: str, android: str) -> tuple[object | None, object | None]:
    """이 SKU 가 가질 수 있는 버전 범위 (floor, ceiling). 제품 관리 등록값에서 만든다.

    floor 는 그 SKU 의 '최초 OS 버전'이고, ceiling 은 **같은 제품에서 바로 위 세대의
    최초 OS 버전**이다. PM86 이 A11=86.00 · A13=86.50 으로 등록돼 있으면 A11 의 범위는
    [86.00, 86.50) 이 된다 — 이 상한이 없으면 A13 문서(86.52)를 A11 것으로 집는다.

    등록값이 없거나 하나뿐이면 그만큼만 좁힌다(못 좁히면 거르지 않는다).
    """
    from app import os_products as _OP
    from app.pipeline import dev_rn as _RN
    from app.pipeline.version_resolve import _ver_key

    rows = [r for r in _OP.list_products()
            if str(r["product"]).upper() == str(product).upper()]
    mine = next((r for r in rows if str(r["android"]) == str(android)), None)
    if mine is None:
        return None, None
    raw = str(mine.get("initial_os_version") or "").strip()
    floor = _RN.parse_version(raw)
    if floor is None:
        return None, None
    # 같은 제품의 등록값 중 내 것보다 높은 것들의 최솟값이 상한이 된다.
    higher = sorted(
        (s for s in (str(r.get("initial_os_version") or "").strip() for r in rows)
         if s and _ver_key(s) > _ver_key(raw)),
        key=_ver_key)
    ceiling = _RN.parse_version(higher[0]) if higher else None
    return floor, ceiling


def _in_window(raw_ver: str, window) -> bool:
    """파일에서 읽은 버전이 그 SKU 의 범위 안인가. 판단할 수 없으면 거르지 않는다."""
    if not window:
        return True
    floor, ceiling = window
    if floor is None:
        return True
    from app.pipeline import dev_rn as _RN

    v = _RN.parse_version(raw_ver)
    if v is None:
        return True
    if not _RN._same_series(v, floor):          # 계열이 다르거나 floor 미만
        return False
    # ceiling 이상이면 위 세대 것이다(_same_series 는 '그 이상'을 뜻한다).
    return not (ceiling is not None and _RN._same_series(v, ceiling))


def in_sku_window(product: str, android: str, version: str) -> bool:
    """이 버전이 그 SKU 의 등록 범위 안이라고 **증명**되는가.

    `_in_window` 는 '판단할 수 없으면 거르지 않는다'라 기본이 True 다. 여기서는 반대로
    **확증이 필요할 때** 쓴다 — 전체 이력 RN 에 다른 Android 세대의 판을 섞으면 안 되므로,
    범위를 못 만들면(제품 미등록·최초 OS 버전 없음) False 를 돌려 잇지 않게 한다.
    """
    floor, ceiling = version_window(product, android)
    if floor is None:
        return False
    return _in_window(version, (floor, ceiling))


def _version_of(name: str, window=None) -> tuple[tuple, str] | None:
    """파일명에서 버전을 뽑아 (비교키, 원문표기). 못 뽑으면 None.

    원문표기를 함께 돌려주는 이유: 비교키로 되돌리면 '67.08' 이 '67.8' 이 된다.

    파일명에는 버전 말고 날짜도 붙는다(`35.04_2024.09.12`). 그래서 후보를 **왼쪽부터**
    보며 SKU 범위에 드는 첫 값을 쓰고, 범위를 모르면 연도로 보이는 것만 걸러낸 뒤
    마지막 값을 쓴다(예전 동작).
    """
    ms = [m.group(0) for m in _NAME_VER_RE.finditer(os.path.splitext(name or "")[0])]
    if not ms:
        return None

    def key(raw: str):
        """비교키 — 접미어는 (글자, 번호)로 뒤에 붙여 81.18 < 81.18C1 이 되게 한다."""
        m = _VER_PARTS_RE.match(raw)
        if m is None:
            return None
        return (int(m.group(1)), int(m.group(2)),
                ord(m.group(3)) if m.group(3) else 0,
                int(m.group(4)) if m.group(4) else 0)

    if window:
        for raw in ms:                       # 왼쪽부터 — 버전이 날짜보다 앞에 온다
            if _in_window(raw, window) and key(raw) is not None:
                return key(raw), raw
        return None
    for raw in reversed(ms):
        if _DATEISH_RE.match(raw.split(".")[0]):
            continue
        k = key(raw)
        if k is not None:
            return k, raw
    return None


def _pick_latest_from(files, where: str, product: str,
                      exclude_version: str = "", window=None) -> dict | None:
    """폴더의 파일 목록에서 **버전이 가장 높은** RN(pdf) 하나. 없으면 None.

    변형 모델 문서는 제외하고, 버전이 같으면 이름 마지막(대개 최신 날짜).
    window 를 주면 그 SKU 범위 밖 버전은 후보에서 뺀다.
    """
    best_key: tuple | None = None
    best_f = None
    best_ver = ""
    for f in files:
        if not f.name.lower().endswith(".pdf") or is_variant_file(f.name, product):
            continue
        if exclude_version and version_in_name(f.name, exclude_version):
            continue
        got = _version_of(f.name, window)
        if got is None:
            continue
        key, raw = got
        cand = (key, f.name)
        if best_key is None or cand > best_key:
            best_key, best_f, best_ver = cand, f, raw
    if best_f is None:
        return None
    return {"rel": f"{where}\\{best_f.name}", "name": best_f.name, "size": best_f.size,
            "where": where, "version": best_ver}


def find_latest_rn(user_id: int, product: str, android: str,
                   exclude_version: str = "") -> tuple[dict | None, str]:
    """그 제품·Android 가 **가지고 있는 가장 최신 버전** RN(pdf). 반환 (hit, error).

    직전 버전 RN 이 없을 때 입력 항목(GMS·SDK·Audio·Touch·Wi-Fi·보안패치)의 초기값을
    가져오기 위한 폴백이다. 폴더 우선순위는 find_existing_rn 과 같되, **후보가 나온 첫
    폴더 안에서** 최신을 고른다 — 폴더가 Android 세대로 갈려 있어(PM95\\Android13 은
    95.04, PM95\\Android15 는 95.52) 전체에서 최대를 잡으면 다른 세대 문서를 집는다.

    exclude_version 을 주면 그 버전 파일은 후보에서 뺀다(이번에 만들 버전).

    **(hit=None, error='') 은 '정말 참고할 RN 이 없다'는 뜻이고, error 가 차 있으면
    조회 자체가 실패한 것이다.** 이 둘을 뭉치면 파일서버가 잠깐 끊긴 것을 최초 출시로
    오해하게 된다. 그래서 제품 폴더 조회가 실패하면 상위 폴더를 한 번 더 열어
    '제품 폴더가 없다'와 '파일서버를 못 본다'를 갈라 준다.
    """
    b = _backend(user_id)
    if b is None:
        return None, "파일서버 자격증명이 없습니다."
    if not (product or "").strip():
        return None, "제품을 알 수 없습니다."

    from app import os_products as _OP

    # 제품 관리에 등록한 '최초 OS 버전'으로 이 SKU 의 버전 범위를 잡는다. 이게 없으면
    # 세대 폴더가 비었을 때 아래 폴백이 옆 세대 문서를 집어 온다(실측 2026-08-05:
    # PM86 A11 이 A13 의 86.52 를, PM90 A13 이 A11 의 90.56 을 집었다).
    window = version_window(product, android)
    # 파일명에서 읽은 버전은 숫자 꼬리만 남는다(`PM84PG15.02C1` → `15.02C1`).
    # 등록된 최초 OS 버전을 기준으로 표준 표기에 맞춰 돌려준다.
    _floor_raw = next(
        (str(r.get("initial_os_version") or "").strip() for r in _OP.list_products()
         if str(r["product"]).upper() == str(product).upper()
         and str(r["android"]) == str(android)), "")

    def _std(hit: dict | None) -> dict | None:
        if hit and hit.get("version") and _floor_raw:
            from app.pipeline import dev_rn as _RN

            hit["version"] = _RN.canonical_version(hit["version"], _floor_raw)
        return hit

    def _try(where: str) -> dict | None:
        try:
            _b = backend_for_path(user_id, where) or b
            return _std(_pick_latest_from(_b.list_dir(where), where, product,
                                          exclude_version, window))
        except Exception:  # noqa: BLE001  (하위 폴더가 없을 수 있다 — 제품 폴더는 이미 열렸다)
            return None

    _reg_dir = (_OP.paths_for(product, android).get("fs_dev_rn") or "").strip()
    if _reg_dir:
        hit = _try(_reg_dir)
        if hit:
            return hit, ""

    prod_dir = f"{RN_BASE}\\{product}"
    try:
        kids = b.list_dir(prod_dir)
    except Exception as e:  # noqa: BLE001
        # 제품 폴더가 없는 것인지, 파일서버 자체를 못 보는 것인지 가른다.
        try:
            base = b.list_dir(RN_BASE)
        except Exception as e2:  # noqa: BLE001
            return None, f"파일서버 조회 실패: {type(e2).__name__}: {e2}"
        if product.strip().lower() not in {k.name.strip().lower() for k in base if k.is_dir}:
            return None, f"파일서버에 '{product}' 제품 폴더가 없습니다."
        return None, f"제품 폴더 조회 실패: {type(e).__name__}: {e}"

    order = _android_dirs([k.name for k in kids if k.is_dir], android)
    for d in order[:1]:                            # 정확한 Android{N} 먼저
        hit = _try(f"{prod_dir}\\{d}")
        if hit:
            return hit, ""
    hit = _std(_pick_latest_from([k for k in kids if not k.is_dir], prod_dir,
                                 product, exclude_version, window))   # 제품 루트
    if hit:
        return hit, ""
    for d in order[1:]:                             # 나머지 Android* 폴더
        hit = _try(f"{prod_dir}\\{d}")
        if hit:
            return hit, ""
    return None, ""                 # 후보 0건 — 정말 참고할 RN 이 없다(최초 출시)


def dev_rn_dir(product: str, android: str) -> str:
    """제품에 등록된 '개발 Release Note 파일 경로'(없으면 '')."""
    from app import os_products as _OP

    return (_OP.paths_for(product, android).get("fs_dev_rn") or "").strip()


def sales_rn_dir(product: str, android: str) -> str:
    """제품에 등록된 'RN for Sales 저장 경로'(없으면 '').

    전체 이력본이 가는 dev_rn_dir 와 **다른 폴더**다 — 영업 배포용 발췌본은 따로 쌓는다.
    """
    from app import os_products as _OP

    return (_OP.paths_for(product, android).get("fs_sales_rn") or "").strip()


def download(user_id: int, rel: str, local_dir: str) -> tuple[str, str]:
    """찾은 RN 을 로컬로 내려받는다. (local_path, error)."""
    b = backend_for_path(user_id, rel)
    if b is None:
        return "", "파일서버 자격증명이 없습니다."
    try:
        path, _ = b.download(rel, local_dir)
        return path, ""
    except Exception as e:  # noqa: BLE001
        return "", f"RN 내려받기 실패: {e}"


# ── PDF 내용 추출 ──────────────────────────────────────────────────────────
# Revision History 표의 버전 줄. 형식 2('PM84PG15.03')는 major 가 문자로 시작해
# 숫자만 보던 규칙에서 통째로 빠졌다 — 그 제품은 이력이 0건으로 읽혔다(실측 2026-08-04).
_VER_HDR_RE = re.compile(
    r"^((?:[A-Za-z][A-Za-z0-9]*)?\d{2,3}\.\d{2}(?:\s*[A-Za-z]{1,3}\s*\d+)?)"
    r"\s*(?:\(([^)]*)\))?\s*$")
_REV_RE = re.compile(r"^V\d+$")

# 쪽 머리말 표(Doc.Type/Author/Page of Pages/Rev./Date/기밀 문구)는 **모든 쪽 맨 위**에 있고,
# PDF 텍스트로 뽑으면 본문 사이에 그대로 끼어든다. Revision History 표가 쪽을 넘어가면 그
# 머리말이 변경 항목으로 읽혀 'Rev. Date Pointmobile Co., Ltd. … Internal Use Only.' 같은
# 줄이 만들어진다(실측 PM84P PM84PG15.03, 2026-08-13).
# 문구는 제품·연도마다 조금씩 달라 정규식으로 다 잡기 어렵다. 대신 **여러 쪽에 똑같이
# 되풀이되는 앞머리 줄**이라는 성질로 잘라낸다 — 문구를 몰라도 된다.
_HDR_SCAN = 25                                    # 실측 머리말 최대 19줄
_PAGENO_RE = re.compile(r"^\d+\s*/\s*\d+$")       # 'Page of Pages' 의 '3 / 3'
_SECNO_RE = re.compile(r"^\d{1,2}\.\s+\S")        # '1. Revision History' — 본문 시작


def _strip_page_headers(pages: list[list[str]]) -> list[list[str]]:
    """쪽마다 되풀이되는 머리말을 각 쪽 앞에서 잘라낸다.

    맨 앞부터 (빈 줄 | 여러 쪽에 반복되는 줄 | 'N / M' 쪽번호) 가 연달아 이어지는 구간만
    후보로 보고, 그 안에서 **머리말임이 분명한 줄(_BOILER_RE)의 마지막 자리까지만** 자른다.
    반복된다는 이유만으로 끝까지 밀면 안 된다 — 머리말의 'Doc.No.' 값이 '-' 라서 본문
    글머리 '-' 와 같고, 그대로 밀면 다음 항목의 글머리를 먹어 **항목 둘이 한 줄로 붙는다**
    (실측 PM67 67.09: 'Removed GPS Demo…' 와 'Updated audio parameters…' 가 합쳐졌다).
    번호 섹션('2. Known Issues')을 만나면 즉시 멈춘다. 쪽이 하나면 비교 대상이 없어 그대로 둔다.
    """
    if len(pages) < 2:
        return pages
    from collections import Counter

    cnt: Counter = Counter()
    for pg in pages:
        for ln in pg[:_HDR_SCAN]:
            if ln:
                cnt[ln] += 1
    # 절반 이상의 쪽에 나오는 줄만 머리말로 본다(본문 문장이 우연히 겹쳐도 안전하게).
    need = max(2, (len(pages) + 1) // 2)
    common = {ln for ln, c in cnt.items() if c >= need}

    out: list[list[str]] = []
    for pg in pages:
        cut = 0
        for i, ln in enumerate(pg[:_HDR_SCAN]):
            if _SECNO_RE.match(ln):
                break
            if not (not ln or ln in common or _PAGENO_RE.match(ln)):
                break
            if _BOILER_RE.search(ln):
                cut = i + 1               # 머리말이 확실한 줄까지만 자른다
        out.append(pg[cut:])
    return out


def extract_revision_entries_full(pdf_path: str) -> dict:
    """RN PDF → {버전: {rev, date, header, lines}}. Revision History 표를 **행 단위로** 읽는다.

    `extract_revision_entries` 와 같은 파싱이지만 V번호(`V2`)와 Update 날짜(`Jul 27, 2026`)를
    버리지 않는다. 전체 이력 RN 은 그 표를 다시 그려야 하므로 셋 다 필요하다.
      · rev    — 'V2' (없으면 '')
      · date   — 'Jul 27, 2026' (Update 칸)
      · header — '95.52 (Jul 18 2026)' (Description 머리, 표기 그대로)
      · lines  — 그 버전의 변경 한 줄들
    반환 순서는 **문서에 실린 순서**(오래된 것이 먼저) 그대로다.

    PDF 텍스트는 표 셀이 줄 단위로 떨어져 나온다:
      V2 / 'Sep 02, 2021' / '67.00 B2 (Jul 26 2021)' / '-' / 본문… / '-' / 본문…
    '-' 를 불릿 시작으로 보고, 다음 '-' 나 다음 버전 헤더 전까지를 한 항목으로 합친다.
    V번호·날짜는 **버전 헤더보다 앞서 나오므로** 만나는 대로 들고 있다가 헤더에서 확정한다.
    """
    try:
        import fitz
    except Exception:  # noqa: BLE001
        return {}
    try:
        doc = fitz.open(pdf_path)
    except Exception:  # noqa: BLE001
        return {}

    raw_pages = [
        [x.strip() for x in _norm_markers(doc[pno].get_text()).splitlines()]
        for pno in range(doc.page_count)
    ]
    doc.close()
    # 표가 쪽을 넘어가면 다음 쪽 머리말이 변경 항목으로 읽힌다 — 먼저 걷어낸다.
    pages = _strip_page_headers(raw_pages)

    lines: list[str] = []
    started = False
    for pg in pages:
        t = "\n".join(pg)
        if not started:
            i = t.find("Revision History")
            if i < 0:
                continue
            started = True
            t = t[i + len("Revision History"):]
        # 다음 장(2. Known Issues 등)이 시작되면 멈춘다
        m = re.search(r"^\s*2\.\s+\S", t, re.M)
        if m:
            lines += [x.strip() for x in t[:m.start()].splitlines()]
            break
        lines += [x.strip() for x in t.splitlines()]

    out: dict[str, dict] = {}
    cur_ver, buf = "", []
    pend_rev, pend_date = "", ""      # 헤더보다 먼저 지나가는 V번호·Update 날짜

    # 읽어 온 문구의 앞 [모듈] 태그는 여기서 뗀다 — Revision History 에는 태그를 넣지
    # 않는다(사용자 확정 2026-08-31). 이 함수가 이력 문구가 들어오는 **유일한 입구**라
    # (전체 이력 RN·기존 RN 이어받기 모두 여기를 지난다) 한 자리에서 막는다.
    from app.pipeline.rn_select import strip_tag as _strip_tag

    def _flush():
        if cur_ver and buf:
            txt = _strip_tag(" ".join(x for x in buf if x).strip())
            if txt:
                out[cur_ver]["lines"].append(txt)

    skip = {"Revision", "Update", "Description", ""}
    for ln in lines:
        if ln in skip:
            continue
        if _BOILER_RE.search(ln):
            continue                  # 위에서 못 걷어낸 머리말 잔재 — 변경 항목이 아니다
        if _REV_RE.match(ln):
            pend_rev = ln                             # 'V2'
            continue
        if (re.match(r"^[A-Z][a-z]{2}\s+\d{1,2},\s*\d{4}$", ln)
                or re.match(r"^\d{2}/\d{2}/\d{4}$", ln)):
            pend_date = ln                            # Update 날짜 칸
            continue
        hv = _VER_HDR_RE.match(ln)
        if hv:                                        # 새 버전 블록 시작
            _flush()
            buf = []
            _vk = re.sub(r"\s+", "", hv.group(1))      # '67.00 B2' → '67.00B2'
            # 같은 버전 표기가 **두 판 이상** 실린 문서가 있다 — 날짜만 다르다
            # (실측 PM67 67.09: V6='67.00 (Aug 27 2021)', V7='67.00 (Sep 28 2021)').
            # 예전에는 버전을 그대로 키로 써서(setdefault) 뒤 판이 앞 판에 흡수됐다:
            # V7 의 번호·날짜가 사라지고 변경 4줄이 V6 줄에 합쳐져, 전체 이력 RN 에서
            # 한 판이 통째로 없어졌다. 키만 유일하게 만들어 판을 따로 남긴다
            # (문서에 찍히는 표기는 header 가 들고 있으므로 '#2' 는 드러나지 않는다).
            cur_ver, _dup = _vk, 2
            while cur_ver in out:
                cur_ver = f"{_vk}#{_dup}"
                _dup += 1
            out[cur_ver] = {"rev": pend_rev, "date": pend_date,
                            "header": ln.strip(), "lines": []}
            pend_rev, pend_date = "", ""
            continue
        if ln.startswith("-"):                        # 새 불릿
            _flush()
            buf = [ln.lstrip("-").strip()]
            continue
        if cur_ver:
            buf.append(ln)                            # 줄바꿈된 이어지는 본문
    _flush()
    return out


def extract_revision_entries(pdf_path: str) -> dict:
    """RN PDF → {버전: [변경 한 줄, …]}. 기존 호출부용 얇은 래퍼."""
    return {v: d["lines"] for v, d in extract_revision_entries_full(pdf_path).items()}


_COMP_SECTIONS = {
    "gms package": "gms_package", "sdk": "sdk",
    "audio parameter version information": "audio",
    "touch panel version information": "touch",
    "wi-fi version information": "wifi",
    "android security patch level": "security_patch",
    "known issues": "known_issues",
}

# 섹션 이름은 제품마다 다르다(실측: PM45 'Audio parameters'·'TP Firmware version information',
# MX450 'Camera tuning'·'Scanner version information'). 아래 규칙으로 **이미 쓰던 키**에 맞추고,
# 규칙에 없는 섹션은 제목을 슬러그로 만들어 새 키를 준다(값을 버리지 않는다).
_KEY_RULES: list[tuple[str, str]] = [
    ("known issue", "known_issues"),
    ("gms", "gms_package"),
    ("security patch", "security_patch"),
    ("wi-fi", "wifi"), ("wifi", "wifi"), ("wlan", "wifi"),
    ("audio", "audio"),
    ("touch panel", "touch"), ("tp firmware", "touch"), ("touch", "touch"),
    ("sdk", "sdk"),
    # 스캐너는 제품마다 이름이 다르다(Scanner / Scanner API / N6603 2D Scanner API).
    # 표준 항목 하나로 모은다 — 안 그러면 같은 값이 제품마다 다른 키로 흩어진다.
    ("scanner", "scanner"),
    ("camera tuning", "camera_tuning"), ("camera", "camera_tuning"),
    ("msr", "msr_firmware"),
    ("bluetooth", "bluetooth_profiles"),
]


# ── 표준 입력 항목(전 모델 공통) ───────────────────────────────────────────
# 사용자 확정(2026-08-04): 제품마다 다르던 항목을 **모든 항목의 합집합**으로 통일한다.
# 값이 없는 항목은 문서에서 **섹션째로 빠진다**(2026-08-26 — 예전에는 빈 섹션이 제목만
# 달고 남았다). 입력 칸은 그대로 남으니 다음 판에 값을 채우면 다시 실린다.
#
# 목록은 짐작이 아니라 **수집된 개발 RN 24개 제품 최신 판 실측**으로 뽑았다.
#   전 제품 공통(24/24) : Known Issues · GMS Package · Touch · Audio · Wi-Fi 3종 · 보안패치
#   일부만 있음         : SDK(21) · Camera tuning(14) · Scanner(5·이름 3종) ·
#                         Bluetooth profiles(5) · MSR firmware(1)
#   제외                : 'android.pool.ntp.org.' — 본문 문장이 번호 섹션으로 잘못 읽힌 오탐
# 스캐너는 제품마다 이름이 달라(Scanner / Scanner API / N6603 2D Scanner API) 한 항목으로
# 묶는다(section_key 의 'scanner' 규칙이 옛 표기를 이 키로 보낸다).
#
# 순서는 현행 템플릿(Audio→Touch)을 그대로 두고, 템플릿에 자리가 없던 항목만 Wi-Fi 뒤에
# 붙였다 — 기존 문서와의 차이를 최소화하기 위해서다.
#
# 라벨에 **번호를 붙이지 않는다**(사용자 확정 2026-08-26). 빈 섹션이 빠지면 뒷 번호가
# 당겨지므로, 화면에 박아 둔 번호는 문서와 어긋난다. 순서는 이 목록이 정하고 번호는
# Word 가 문서에서 매긴다(섹션 제목이 같은 자동 목록에 묶여 있다).
STANDARD_SECTIONS: list[dict] = [
    {"key": "known_issues", "label": "Known Issues", "value": "", "multiline": True},
    {"key": "gms_package", "label": "GMS Package", "value": "", "multiline": False},
    {"key": "sdk", "label": "SDK", "value": "", "multiline": False},
    {"key": "audio", "label": "Audio parameter version information", "value": "",
     "multiline": False},
    {"key": "touch", "label": "Touch panel version information", "value": "",
     "multiline": False},
    {"key": "wifi_driver", "label": "Wi-Fi version information — Driver", "value": "",
     "multiline": False},
    {"key": "wifi_firmware", "label": "Wi-Fi version information — Firmware",
     "value": "", "multiline": False},
    {"key": "wifi_config", "label": "Wi-Fi version information — Configuration",
     "value": "", "multiline": False},
    {"key": "camera_tuning", "label": "Camera tuning version information", "value": "",
     "multiline": False},
    {"key": "scanner", "label": "Scanner version information", "value": "",
     "multiline": False},
    {"key": "msr_firmware", "label": "MSR firmware version information", "value": "",
     "multiline": False},
    {"key": "bluetooth_profiles", "label": "Bluetooth profiles", "value": "",
     "multiline": True},
    {"key": "security_patch", "label": "Android security patch level", "value": "",
     "multiline": False},
]

# 표준 항목의 키 — 이 키들은 문서에서 **고정 자리**를 가진다(extra 로 밀려나면 안 된다).
STANDARD_KEYS: set[str] = {s["key"] for s in STANDARD_SECTIONS}

# 하위호환 — 예전 이름으로 부르던 곳이 있다.


def section_key(title: str) -> str:
    """섹션 제목 → 저장 키. 아는 항목은 기존 키로, 모르는 항목은 슬러그로."""
    low = re.sub(r"\s+", " ", (title or "")).strip().lower()
    if low in _COMP_SECTIONS:
        return _COMP_SECTIONS[low]
    for needle, key in _KEY_RULES:
        if needle in low:
            return key
    slug = re.sub(r"[^a-z0-9]+", "_", low).strip("_")
    # 'version information' 처럼 모든 섹션에 붙는 꼬리는 떼어 키를 짧게 만든다.
    slug = re.sub(r"_?version_information$|_?information$", "", slug) or "extra"
    return slug


# 번호 섹션 판정 — Revision History 표의 버전 줄('95. 51', '802. 11n/ac')이 섞이지 않게
# **번호는 1~20, 제목은 영문자로 시작**하는 것만 섹션으로 본다.
_SEC_HDR_RE = re.compile(r"^\s*(\d{1,2})\.\s*([A-Za-z][^\n]{0,58})\s*$", re.M)

# Wi-Fi 섹션 하나가 Driver·Firmware·Configuration 세 값을 담는다 — 입력 칸도 세 개로 나눈다.
_WIFI_SUBS = (("wifi_driver", "Driver"), ("wifi_firmware", "Firmware"),
              ("wifi_config", "Configuration"))


# 페이지 머리글/바닥글(표 셀·회사 표기) — 값 이어붙이기를 여기서 끊는다.
# 구세대 RN 은 같은 글머리를 **Wingdings 사설영역(PUA) 코드**로 찍는다. 글자만 다르고
# 역할은 같은데, 마커를 못 알아보면 그 섹션의 값을 하나도 못 뽑는다.
# 실측(2026-08-04, 레거시 203판): 신세대 121판은 ✓/◼/▪, 구세대 80판은 아래 세 코드.
_PUA_MARKERS = {"": "✓", "": "◼", "": "▪"}


def _norm_markers(text: str) -> str:
    """PDF 에서 읽은 글머리 문자를 표준 마커로 통일한다."""
    for pua, std in _PUA_MARKERS.items():
        if pua in text:
            text = text.replace(pua, std)
    return text


_BOILER_RE = re.compile(
    r"Doc\.\s*Type|Doc\.\s*No|Page of Pages|Company confidential|Pointmobile Co|"
    r"Internal Use Only|^Author$|^Model$|^Rev\.?$|^Date$|^SW Team$|^Release Note$",
    re.I,
)


def _section_entries(seg: str) -> list[tuple[str, str]]:
    """섹션 본문 → [(라벨, 값)]. 제품마다 표기가 두 가지라 둘 다 받는다.

      · PM95 형: '✓ 3.41.08'                        → ('', '3.41.08')
      · PM67 형: '✓ GMS Package' + '▪ Mar 2022'      → ('GMS Package', 'Mar 2022')
    """
    # 마커(✓/▪) 뒤 값이 **다음 줄에 오는** 경우가 있어(PM67: '▪ ' 다음 줄에 'Mar 2022'),
    # 마커를 만나면 그때까지 모은 줄을 확정하고 이어지는 줄은 현재 마커 값에 붙인다.
    items: list[tuple[str, str]] = []
    cur_mk: str | None = None
    cur_tx: list[str] = []

    def _flush_mk() -> None:
        if cur_mk:
            items.append((cur_mk, " ".join(x for x in cur_tx if x).strip()))

    for ln in seg.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith("✓") or s.startswith("▪"):
            _flush_mk()
            cur_mk = "✓" if s.startswith("✓") else "▪"
            cur_tx = [s.lstrip("✓▪ \t").strip()]
        elif s.startswith("◼"):          # 버전 마커 — 값 구간 종료
            _flush_mk()
            cur_mk, cur_tx = None, []
        elif cur_mk:
            # 페이지 머리글/바닥글이 값에 딸려 들어가지 않게 여기서 끊는다.
            if _BOILER_RE.search(s) or len(cur_tx) >= 3:
                _flush_mk()
                cur_mk, cur_tx = None, []
            else:
                cur_tx.append(s)         # 줄바꿈된 값
    _flush_mk()
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(items):
        mk, tx = items[i]
        if mk != "✓":
            i += 1
            continue
        sub = []
        j = i + 1
        while j < len(items) and items[j][0] == "▪":
            if items[j][1]:
                sub.append(items[j][1])
            j += 1
        if sub:                       # ✓ 는 라벨, ▪ 가 값
            out.append((tx, " / ".join(sub)))
            i = j
        else:                         # ✓ 자체가 값
            if tx:
                out.append(("", tx))
            i += 1
    return out


def _clean_val(v: str) -> str:
    return re.sub(r"[.\s]+$", "", (v or "").strip())


def _section_values(seg: str) -> list[str]:
    """섹션 본문 → **값 목록**(한 줄에 하나).

    한 섹션에 값이 여러 개인 경우가 있다(예: Scanner = API version / Decoder version /
    ScanDriver version). _section_entries 는 그것을 ' / ' 로 이어 한 문자열로 주므로,
    여기서 다시 펼쳐 목록으로 만든다. 문서에서도 줄마다 하나씩 찍혀야 한다.
    """
    out: list[str] = []
    for _lab, val in _section_entries(seg):
        for part in str(val or "").split(" / "):
            v = _clean_val(part)
            if v and v not in out:
                out.append(v)
    return out


def extract_components(pdf_path: str) -> dict:
    """RN PDF 의 번호 섹션(GMS Package·SDK·Audio·Touch·Wi-Fi·security patch)에서 값을 뽑는다.

    섹션 **번호는 제품마다 다르므로**(PM95 5.Audio / PM67 4.Audio) 제목 텍스트로 매칭한다.
    """
    try:
        import fitz
    except Exception:  # noqa: BLE001
        return {}
    try:
        doc = fitz.open(pdf_path)
        text = _norm_markers(
            "\n".join(doc[p].get_text() for p in range(doc.page_count)))
        doc.close()
    except Exception:  # noqa: BLE001
        return {}

    out: dict = {}
    for m in re.finditer(r"^\s*\d+\.\s*(.+?)\s*$", text, re.M):
        key = _COMP_SECTIONS.get(m.group(1).strip().lower())
        if not key:
            continue
        nxt = re.search(r"^\s*\d+\.\s+\S", text[m.end():], re.M)
        seg = text[m.end(): m.end() + (nxt.start() if nxt else 4000)]
        ents = _section_entries(seg)
        if not ents:
            continue
        if key == "wifi":                       # Driver/Firmware/Configuration 분리
            for lab, val in ents:
                t = f"{lab} {val}".lower()
                v = _clean_val(val.split(":", 1)[-1] if ":" in val else val)
                if "driver" in t:
                    out["wifi_driver"] = v
                elif "firmware" in t:
                    out["wifi_firmware"] = v
                elif "config" in t:
                    out["wifi_config"] = v
            continue
        if key == "gms_package":                # 'GMS Package' 라벨 우선(Play 업데이트 항목 제외)
            pick = next((v for lab, v in ents if "gms" in lab.lower()), None)
            out[key] = _clean_val(pick if pick is not None else ents[0][1])
            continue
        if out.get(key):                        # 이미 채웠으면 첫 섹션 값 유지
            continue
        out[key] = _clean_val(ents[0][1])
    return {k: v for k, v in out.items() if v}


def _iter_sections(pdf_path: str):
    """RN PDF → (번호, 제목, 본문) 차례로.

    값과 판을 서로 다른 구획으로 읽으면 어긋난다 — 섹션을 나누는 규칙을 여기 하나로 둔다.
    """
    try:
        import fitz
    except Exception:  # noqa: BLE001
        return
    try:
        doc = fitz.open(pdf_path)
        text = _norm_markers(
            "\n".join(doc[p].get_text() for p in range(doc.page_count)))
        doc.close()
    except Exception:  # noqa: BLE001
        return

    heads: list[tuple[int, str, int, int]] = []   # (번호, 제목, 본문시작, 헤더시작)
    for m in _SEC_HDR_RE.finditer(text):
        no, title = int(m.group(1)), m.group(2).strip()
        if not (1 <= no <= 20):
            continue
        heads.append((no, title, m.end(), m.start()))
    for i, (no, title, body_start, _hs) in enumerate(heads):
        end = heads[i + 1][3] if i + 1 < len(heads) else min(len(text), body_start + 4000)
        yield no, title, text[body_start:end]


def extract_sections(pdf_path: str) -> list[dict]:
    """RN PDF → **그 제품의 입력 항목 목록**(문서에 있는 순서·이름 그대로).

    섹션 구성은 제품마다 다르다(실측: PM95 8개까지 / MX450 10개까지, PM67 은 보안패치가 3번,
    PM45 는 'TP Firmware'·'Camera tuning' 이 따로 있음). 그래서 고정 목록을 쓰지 않고
    문서에서 번호 섹션을 그대로 읽어 입력 칸을 만든다.

    반환: [{"key", "label", "value", "multiline"}] — label 은 '3. GMS Package' 처럼 번호 포함.
      · 1. Revision History 는 우리가 만드는 것이라 입력 항목에서 제외한다.
      · Wi-Fi 섹션 하나는 Driver/Firmware/Configuration **세 칸**으로 펼친다.
    """
    fields: list[dict] = []
    seen: set[str] = set()
    for no, title, seg in _iter_sections(pdf_path):
        if no == 1:                                  # Revision History — 생성 대상
            continue
        key = section_key(title)
        ents = _section_entries(seg)

        if key == "wifi":
            vals = {k: "" for k, _l in _WIFI_SUBS}
            for lab, val in ents:
                t = f"{lab} {val}".lower()
                v = _clean_val(val.split(":", 1)[-1] if ":" in val else val)
                for sub, name in _WIFI_SUBS:
                    if name.lower()[:6] in t or (sub == "wifi_config" and "config" in t):
                        vals[sub] = vals[sub] or v
            for sub, name in _WIFI_SUBS:
                if sub in seen:
                    continue
                seen.add(sub)
                fields.append({"key": sub, "label": f"{no}. {title} — {name}",
                               "value": vals[sub], "multiline": False})
            continue

        if key in seen:
            continue
        seen.add(key)
        if key == "gms_package":
            pick = next((v for lab, v in ents if "gms" in lab.lower()), None)
            value = _clean_val(pick if pick is not None else (ents[0][1] if ents else ""))
        else:
            # 값이 여러 개인 섹션은 **줄바꿈으로 이어** 둔다(문서에서 줄마다 하나씩 찍힌다).
            vals = _section_values(seg)
            value = "\n".join(vals)
        fields.append({
            "key": key, "label": f"{no}. {title}", "value": value,
            # 값이 여러 줄이거나 Known Issues 면 입력칸도 여러 줄로.
            "multiline": key == "known_issues" or "\n" in value,
        })
    return fields


def _section_version(seg: str) -> str:
    """섹션 본문 → **그 값이 확정된 판**(◼ 줄의 텍스트).

    발행본은 섹션 안에 `◼ 판` + `✓ 값…` 블록을 쌓는다. 값이 바뀐 판마다 블록이 하나씩
    늘어나므로(실측 PM45 45.76 의 SDK 는 45.70·45.71·45.72·45.74·45.75 다섯 블록),
    **마지막 블록**이 그 값이 마지막으로 바뀐 판이다. 값을 물려받을 때 이 판도 함께
    물려받아야 문서가 '언제 정해진 값인지'를 바르게 말한다(사용자 확정 2026-09-03).

    Known Issues 처럼 ◼ 자리에 값이 오는 섹션에는 쓰지 않는다 — 입력 항목이 아니다.
    """
    out = ""
    for ln in seg.splitlines():
        s = ln.strip()
        if not s.startswith("◼"):
            continue
        v = s.lstrip("◼ \t").strip()
        # 판에는 숫자가 있다. 'None' 같은 값 줄을 판으로 오해하지 않는다.
        if v and any(c.isdigit() for c in v) and not _BOILER_RE.search(v):
            out = v
    return out


def extract_section_versions(pdf_path: str) -> dict:
    """RN PDF → {키: 그 값이 확정된 판}. extract_sections 와 **같은 구획**을 쓴다.

    값(extract_sections)과 판이 어긋나면 안 되므로 섹션을 나누는 코드를 공유한다.
    Wi-Fi 는 한 섹션이 세 칸으로 펼쳐지므로 세 키가 같은 판을 받는다.
    """
    out: dict = {}
    for no, title, seg in _iter_sections(pdf_path):
        if no == 1:
            continue
        ver = _section_version(seg)
        if not ver:
            continue
        key = section_key(title)
        if key == "wifi":
            for sub, _lab in _WIFI_SUBS:
                out.setdefault(sub, ver)
            continue
        out.setdefault(key, ver)
    return out
