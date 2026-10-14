"""OS Update 제품 목록 — 조회·등록·수정·삭제.

한 행 = **제품명 + Android 버전 + Type** 조합 하나(SKU). 세 값이 유일 키다.
같은 PM95 라도 Android 13/15 가 다른 SKU 이고, 같은 Android 라도 GMS/AOSP/Russia 는
배포 계열이 달라 문서·폴더가 따로 있으므로 Type 까지 키에 넣는다.

화면(app/pages/products.py)이 그릴 입력 항목을 여기서 한 번만 정의한다
(FIELD 정의를 화면과 나눠 두면 항목이 추가될 때 두 곳을 고쳐야 하므로).
"""
from __future__ import annotations

import re
from urllib.parse import unquote

from app.database import get_session
from app.models import OsProduct

# ── 입력 항목 정의 ─────────────────────────────────────────────────────────
# (내부 키, 화면 라벨, 입력칸 placeholder)
KEY_FIELDS: list[tuple[str, str, str]] = [
    ("product", "제품명", "PM95"),
    ("android", "Android 버전", "15"),
    ("os_type", "Type", "GMS"),
]

# 제품군 — **보기 구분 전용**이다(파이프라인 판단에는 쓰지 않는다).
# 목록에서 이 순서대로 묶어 접었다 편다. 비어 있으면 마지막 칸(Others)으로 본다.
#
# Others 는 '분류가 애매한 것'이 아니라 **OS 축이 Android 하나가 아닌 제품**이다
# (사용자 확정 2026-08-05):
#   · OS 가 없는 장비            — RFID 리더기(RF300·RF750·RF851 …)
#   · Android 가 아닌 OS         — Windows CE · WEH · WM10
#   · 한 제품에 OS 가 여럿       — PM60(CE·WEH·Android), PM450(CE·WEH) …
# 지금은 **분류만** 한다. 등록 폼과 파이프라인은 Android 전제 그대로 두며, Others 전용
# 입력 항목·작업 흐름은 나중에 따로 정한다.
CATEGORIES: list[str] = ["EDA", "PDT & Keypad", "Tablet", "POS", "Others"]
CATEGORY_FALLBACK = "Others"
# 화면에서 제품군을 고를 때 보여줄 안내 — 무엇을 Others 로 둘지 사람이 판단할 근거.
CATEGORY_HINT = ("Others: OS 없음(RFID 리더기) · Android 아님(CE·WEH·WM) · "
                 "OS 여럿")

# OS 축 입력 — Android 를 전제로 한 두 값이라 Others 에서는 받지 않는다.
# (RFID 리더기처럼 OS 자체가 없는 제품이 있어 필수로 둘 수 없다.)
OS_KEYS: list[str] = ["android", "os_type"]


def is_others(category: str | None) -> bool:
    """Others(= OS 축이 Android 하나가 아닌) 제품인가."""
    return (category or CATEGORY_FALLBACK) == CATEGORY_FALLBACK


def category_of(row: dict) -> str:
    """그 제품이 속한 제품군. 미등록이거나 모르는 값이면 Others 로 묶는다."""
    c = str((row or {}).get("category") or "").strip()
    return c if c in CATEGORIES else CATEGORY_FALLBACK


def group_by_category(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    """[(제품군, 제품들)] — CATEGORIES 순서 고정. **비어 있는 제품군도 그대로 둔다**
    (칸이 사라졌다 생겼다 하면 늘 같은 자리에 있다는 기대가 깨진다)."""
    buckets: dict[str, list[dict]] = {c: [] for c in CATEGORIES}
    for r in rows:
        buckets[category_of(r)].append(r)
    return [(c, buckets[c]) for c in CATEGORIES]


# 기본 정보 — 유일 키에는 들어가지 않고, 필수도 아니다.
INFO_FIELDS: list[tuple[str, str, str]] = [
    ("initial_os_version", "최초 OS 버전", "95.01"),
    ("category", "제품군", "EDA"),
]

# 경로 항목 — (그룹 이름, [(키, 라벨, placeholder)]).
# placeholder 는 지금 파이프라인이 실제로 쓰는 경로 형태를 예로 넣는다.
PATH_GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("사내 경로", [
        ("dev_rn_path", "개발 Release Note 파일 경로",
         r"\\192.168.123.104\Project\SQA\Documents\ReleaseNotes\PM95\Android15"),
        # 전체 이력본은 위 경로, 영업 배포용 발췌본(RN for Sales)은 아래 경로에 쌓는다.
        ("sales_rn_path", "RN for Sales 저장 경로",
         r"\\192.168.123.104\Project\SQA\Documents\ReleaseNotes\PM95\Android15\for Sales"),
        ("os_file_path", "OS file 경로",
         r"Project\PM95\Software\Android15\Formal Release\95.52"),
    ]),
    ("게시 경로", [
        ("sp_rn_path", "Service Portal Release Note 경로",
         "PM95 A15 Resources (OS, SDK) & Document"),
        ("kb_rn_path", "Knowledge Base Release Note 경로",
         "PM95 (Android 15) Release Notes"),
    ]),
    ("Dropbox 경로", [
        ("dropbox_os_sdcard_path", "Dropbox OS Image(sdcard) 경로",
         "/OSImage/PM95/NEW_FOTA"),
        ("dropbox_os_sdcard_zip_path", "Dropbox OS Image(sdcard zip) 경로",
         "/Support Team/SupportPotal/OSImage, FW/PM95"),
        ("dropbox_os_flash_path", "Dropbox OS Image(flash) 경로",
         "/Support Team/Support OS/Flash Package (Fastboot, etc.)/PM95"),
        ("dropbox_rn_path", "Dropbox Release Note 경로",
         "/OSImage/PM95/Release Notes"),
    ]),
]

PATH_FIELDS: list[tuple[str, str, str]] = [f for _g, fs in PATH_GROUPS for f in fs]
PATH_KEYS: list[str] = [k for k, _l, _p in PATH_FIELDS]
INFO_KEYS: list[str] = [k for k, _l, _p in INFO_FIELDS]
ALL_KEYS: list[str] = (
    [k for k, _l, _p in KEY_FIELDS] + INFO_KEYS + PATH_KEYS
)

# Type 기본 선택지. 여기 없는 계열은 화면에서 직접 입력한다(입력값도 선택지로 누적된다).
DEFAULT_TYPES: list[str] = ["GMS", "AOSP", "Russia"]


def _norm(fields: dict) -> dict:
    """저장 전 정리 — 제품명은 대문자, Android 는 숫자만, 나머지는 앞뒤 공백 제거."""
    out = {k: str(fields.get(k, "") or "").strip() for k in ALL_KEYS}
    out["product"] = out["product"].upper().replace(" ", "")
    out["android"] = "".join(ch for ch in out["android"] if ch.isdigit())
    # Others 는 OS 입력을 받지 않는다 — 화면에서 감춘 값이 남아 저장되지 않도록 비운다.
    # (제품군을 Others 로 바꿔 저장한 기존 행도 여기서 정리된다.)
    if is_others(out.get("category")):
        for k in OS_KEYS:
            out[k] = ""
    # '95.01 ' · 'v95.01' 처럼 들어와도 '95.01' 로 맞춘다(비교·정렬에 쓰이므로).
    out["initial_os_version"] = out["initial_os_version"].lstrip("vV").replace(" ", "")
    return out


def _row_to_dict(r: OsProduct) -> dict:
    d = {k: getattr(r, k, "") or "" for k in ALL_KEYS}
    d.update(
        id=r.id,
        created_by=r.created_by or "", updated_by=r.updated_by or "",
        created_at=r.created_at, updated_at=r.updated_at,
    )
    return d


def _sku_key(d: dict) -> tuple:
    """정렬 키 — 제품명 → Android 내림차순(최신 먼저) → Type."""
    try:
        android = -int(d.get("android") or 0)
    except ValueError:
        android = 0
    return (d.get("product", ""), android, d.get("os_type", ""))


def list_products(q: str = "") -> list[dict]:
    """제품 목록. q 는 제품명·Android·Type·경로를 통틀어 부분 일치(대소문자 무시)."""
    with get_session() as db:
        rows = [_row_to_dict(r) for r in db.query(OsProduct).all()]
    needle = (q or "").strip().lower()
    if needle:
        rows = [
            r for r in rows
            if needle in " ".join(str(r.get(k, "")) for k in ALL_KEYS).lower()
        ]
    rows.sort(key=_sku_key)
    return rows


def get(product_id: int) -> dict | None:
    with get_session() as db:
        r = db.query(OsProduct).filter_by(id=product_id).first()
        return _row_to_dict(r) if r else None


def known_types() -> list[str]:
    """선택지로 보여줄 Type 목록 — 기본값 + 이미 등록된 값."""
    with get_session() as db:
        used = {(t or "").strip() for (t,) in db.query(OsProduct.os_type).all()}
    extra = sorted(t for t in used if t and t not in DEFAULT_TYPES)
    return DEFAULT_TYPES + extra


def _validate(f: dict) -> str:
    """필수값 검사. 문제가 없으면 빈 문자열.

    Others 는 OS 입력을 받지 않으므로 제품명만 요구한다.
    """
    need = [kf for kf in KEY_FIELDS
            if not (is_others(f.get("category")) and kf[0] in OS_KEYS)]
    missing = [label for key, label, _p in need if not f.get(key)]
    # 제품군이 무엇을 더 물을지 결정하므로, 고르지 않은 채로는 저장할 수 없다.
    if not f.get("category"):
        missing.insert(0, "제품군")
    if missing:
        return "다음 항목은 반드시 입력해야 합니다 — " + ", ".join(missing)
    return ""


def _duplicate_id(db, f: dict, exclude_id: int | None = None):
    """같은 (제품명, Android, Type) 행의 id. 없으면 None."""
    query = db.query(OsProduct).filter_by(
        product=f["product"], android=f["android"], os_type=f["os_type"]
    )
    if exclude_id is not None:
        query = query.filter(OsProduct.id != exclude_id)
    row = query.first()
    return row.id if row else None


def sku_label(f: dict) -> str:
    """'PM95 · A15 · GMS' 형태 표시 이름."""
    parts = [f.get("product", "")]
    if f.get("android"):
        parts.append(f"A{f['android']}")
    if f.get("os_type"):
        parts.append(f["os_type"])
    return " · ".join(p for p in parts if p)


def create(fields: dict, actor: str = "") -> tuple[bool, str]:
    f = _norm(fields)
    err = _validate(f)
    if err:
        return False, err
    with get_session() as db:
        if _duplicate_id(db, f) is not None:
            return False, f"이미 등록된 제품입니다 — {sku_label(f)}"
        db.add(OsProduct(**f, created_by=actor, updated_by=actor))
        db.commit()
    return True, f"{sku_label(f)} 를 등록했습니다."


def update(product_id: int, fields: dict, actor: str = "") -> tuple[bool, str]:
    f = _norm(fields)
    err = _validate(f)
    if err:
        return False, err
    with get_session() as db:
        row = db.query(OsProduct).filter_by(id=product_id).first()
        if row is None:
            return False, "제품을 찾을 수 없습니다(이미 삭제되었을 수 있습니다)."
        if _duplicate_id(db, f, exclude_id=product_id) is not None:
            return False, f"같은 제품이 이미 있습니다 — {sku_label(f)}"
        for k, v in f.items():
            setattr(row, k, v)
        row.updated_by = actor
        db.commit()
    return True, f"{sku_label(f)} 를 수정했습니다."


def delete(product_id: int) -> tuple[bool, str]:
    with get_session() as db:
        row = db.query(OsProduct).filter_by(id=product_id).first()
        if row is None:
            return False, "제품을 찾을 수 없습니다."
        label = sku_label(_row_to_dict(row))
        db.delete(row)
        db.commit()
    return True, f"{label} 를 삭제했습니다."


# 등록 상태를 세는 대상 = 기본 정보 + 경로(= 유일 키를 뺀 모든 입력 항목).
COUNTED_FIELDS: list[tuple[str, str, str]] = INFO_FIELDS + PATH_FIELDS
COUNTED_KEYS: list[str] = [k for k, _l, _p in COUNTED_FIELDS]


# ── 등록 링크 → 파이프라인이 쓰는 내부 식별자 ─────────────────────────────
# 사람은 브라우저에서 복사한 **웹 링크**를 넣는데, 파이프라인은 Dropbox API 경로·
# Service Portal 문서 id·Confluence 페이지 제목을 쓴다. 그 변환을 여기서 한다.
# (파일서버 UNC 경로는 백엔드가 그대로 받으므로 변환하지 않는다.)
_DROPBOX_HOME_RE = re.compile(r"^https?://(?:www\.)?dropbox\.com/(?:home|work)", re.I)
# Service Portal 문서 id 는 **영숫자**다(실측: 51 · 3Q · 5R, 코드에도 '4C' 사용).
# \d+ 로 잡으면 '3Q' 가 '3' 으로 조용히 잘린다.
_SP_DOC_RE = re.compile(r"download-detail/([A-Za-z0-9]+)")
_KB_PAGE_RE = re.compile(r"/display/([^/]+)/([^?#]+)")


def dropbox_path(value: str) -> str:
    """Dropbox 웹 링크 → API 경로. 이미 '/…' 형태면 그대로 둔다. 못 읽으면 ""."""
    v = (value or "").strip()
    if not v:
        return ""
    if v.startswith("/"):
        return v.rstrip("/")
    m = _DROPBOX_HOME_RE.match(v)
    if not m:
        return ""
    rest = v[m.end():].split("?", 1)[0].split("#", 1)[0]
    return unquote(rest).rstrip("/")


def sp_doc_id(value: str) -> str:
    """Service Portal 링크 → Download 문서 id. 못 읽으면 ""."""
    m = _SP_DOC_RE.search(value or "")
    return m.group(1) if m else ""


def kb_page(value: str) -> tuple[str, str]:
    """Confluence 링크 → (스페이스, 페이지 제목). 못 읽으면 ("", "")."""
    m = _KB_PAGE_RE.search(value or "")
    if not m:
        return "", ""
    return m.group(1), unquote(m.group(2).replace("+", " ")).strip()


# (등록 키, 표시 라벨, 변환 결과 설명, 변환 함수)


def find_sku(product: str, android: str, os_type: str = "") -> dict | None:
    """제품+Android(+Type)로 등록된 SKU 를 찾는다.

    Type 을 주지 않으면 GMS 를 먼저 본다 — [FIH] 트리거 메일은 전부 Google 승인 건이라
    메일에서는 Type 을 알 수 없다(실측: 트리거 19건 모두 'to Google').
    """
    p = (product or "").strip().upper().replace(" ", "")
    a = "".join(c for c in str(android or "") if c.isdigit())
    if not p:
        return None
    rows = [r for r in list_products()
            if r["product"] == p and (not a or r["android"] == a)]
    if os_type:
        rows = [r for r in rows if r["os_type"] == os_type]
    if not rows:
        return None
    rows.sort(key=lambda r: (r["os_type"] != "GMS", r["os_type"]))
    return rows[0]


def android_for_version(product: str, version: str, android: str = "") -> str:
    """이 버전이 속한 세대의 Android. **등록된 제품 정보에서 찾는다.**

    파이프라인 상태의 android 가 비어 있어도(메일에서 시작한 옛 작업 등) 제품 관리에
    등록해 둔 값이 있으므로 '모른다'로 두지 않는다.

    한 제품에 세대가 여럿이면 버전만으로 갈라야 한다. 각 SKU 의 '최초 OS 버전' 중
    **이번 버전을 넘지 않는 가장 큰 것**이 그 세대다(실측: PM95 A13 95.00 · A15 95.50 →
    95.52 는 A15, 95.07 은 A13). major 만 보면 두 세대가 같은 숫자라 갈리지 않는다.
    """
    a = "".join(c for c in str(android or "") if c.isdigit())
    if a:
        return a
    p = (product or "").strip().upper().replace(" ", "")
    rows = [r for r in list_products() if r["product"] == p]
    if not rows:
        return ""
    if len(rows) == 1:
        return str(rows[0].get("android") or "")

    from app.pipeline.version_resolve import _ver_key

    vk = _ver_key(version or "")
    best = None
    for r in rows:
        iv = (r.get("initial_os_version") or "").strip()
        if not iv:
            continue
        k = _ver_key(iv)
        if k <= vk and (best is None or k > best[0]):
            best = (k, r)
    if best:
        return str(best[1].get("android") or "")
    # 이번 버전보다 낮은 세대가 없다 = 가장 오래된 세대보다도 앞선 버전. 그 세대로 본다.
    rows.sort(key=lambda r: _ver_key(r.get("initial_os_version") or ""))
    return str(rows[0].get("android") or "")


def paths_for(product: str, android: str, os_type: str = "") -> dict:
    """파이프라인이 쓰는 **내부 식별자 묶음**. 등록이 없으면 빈 dict.

    반환 키:
      fs_dev_rn   개발 RN 폴더(UNC)          fs_sales_rn  RN for Sales 폴더(UNC)
      fs_os       OS 파일 폴더(UNC)
      sp_doc_id   Service Portal 문서 id      kb_space/kb_title  Confluence 스페이스·제목
      dbx_fota    NEW_FOTA(숫자 폴더 부모)     dbx_sdcard_zip     sdcard zip 폴더
      dbx_flash   flash zip 폴더              dbx_rn             Release Note 폴더
      sku         찾은 SKU 표시 이름
    각 값은 등록이 없거나 변환 실패면 "" 다 — 호출부가 있으면 쓰고 없으면 기존 방식으로 간다.
    """
    row = find_sku(product, android, os_type)
    if row is None:
        return {}
    space, title = kb_page(row.get("kb_rn_path", ""))
    return {
        "sku": sku_label(row),
        "product_id": row["id"],
        "fs_dev_rn": (row.get("dev_rn_path") or "").strip(),
        "fs_sales_rn": (row.get("sales_rn_path") or "").strip(),
        "fs_os": (row.get("os_file_path") or "").strip(),
        "sp_doc_id": sp_doc_id(row.get("sp_rn_path", "")),
        "kb_space": space,
        "kb_title": title,
        "dbx_fota": dropbox_path(row.get("dropbox_os_sdcard_path", "")),
        "dbx_sdcard_zip": dropbox_path(row.get("dropbox_os_sdcard_zip_path", "")),
        "dbx_flash": dropbox_path(row.get("dropbox_os_flash_path", "")),
        "dbx_rn": dropbox_path(row.get("dropbox_rn_path", "")),
    }


# ── 버전 표기 접두어 ────────────────────────────────────────────────────────
# FOTA OsInfo 의 Version 칸 표기는 **제품마다 다르다**(실측 2026-08-24, FOTA 1,365행):
#
#     TR54   → 'TR54G15.04 (Aug 22 2026)'      PM84P → 'PM84PG15.03 (Jul 27 2026)'
#     MF52E  → 'MF52EG15.00 NB166 (Apr 19 …)'  PM95  → '95.07 (Jun 11 2026)'
#     PM68   → '68.04 (Jun 26 2026)'           PM352 → '352.08 (Mar 16 2026)'
#
# 신형은 빌드명(제품 접두어 포함), 구형은 숫자만이다. 이 구분은 제품관리의
# **'최초 OS 버전'** 에 그대로 들어 있다 — TR54 는 'TR54G15.00', PM95 는 '95.50'.
# 그래서 접두어를 그 등록값에서 뽑는다(따로 표를 만들지 않는다 — 표가 둘이면 갈린다).
_VER_NUM_RE = re.compile(r"\d{2,3}\.\d{2}")


def split_version_prefix(version: str) -> str:
    """버전 문자열에서 숫자 앞 접두어만. 'TR54G15.00' → 'TR54G' · '95.50' → ''.

    앞의 'v' 는 접두어가 아니라 표기 습관이라 떼고 본다('v95.01' → '').
    """
    s = str(version or "").strip().lstrip("vV")
    m = _VER_NUM_RE.search(s)
    return s[:m.start()] if m else ""


def version_prefix(product: str, android: str = "", os_type: str = "") -> tuple[str, str]:
    """그 제품의 OS 버전 표기 접두어. 반환 (접두어, 출처설명).

    접두어가 없는 제품(구형)은 ('', 사유)를 돌려준다 — '못 찾음'과 구분해야 하므로
    출처 문구를 함께 준다. 등록이 아예 없으면 ('', '') 다.
    """
    sku = find_sku(product, android, os_type)
    if not sku:
        return "", ""
    iv = str(sku.get("initial_os_version") or "").strip()
    if not iv:
        return "", ""
    return split_version_prefix(iv), f"제품관리 {sku_label(sku)} · 최초 OS 버전 {iv}"


def apply_version_prefix(prefix: str, version: str) -> str:
    """'15.04 (Aug 22 2026)' + 'TR54G' → 'TR54G15.04 (Aug 22 2026)'.

    이미 접두어가 붙어 있으면 그대로 둔다(메일에 빌드명이 통째로 적혀 온 경우).
    """
    v = str(version or "").strip()
    p = str(prefix or "").strip()
    if not p or not v:
        return v
    return v if v.upper().startswith(p.upper()) else p + v


def filled_items(f: dict) -> int:
    """입력 항목(기본 정보 + 경로) 중 채워진 개수."""
    return sum(1 for k in COUNTED_KEYS if str(f.get(k, "") or "").strip())


def missing_labels(f: dict) -> list[str]:
    """아직 비어 있는 입력 항목의 라벨 — 무엇이 빠졌는지 화면에 알려주기 위함."""
    return [label for key, label, _p in COUNTED_FIELDS
            if not str(f.get(key, "") or "").strip()]
