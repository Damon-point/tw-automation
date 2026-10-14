"""DQA FOTA Test 처리 — DQA 팀의 FOTA 업데이트 테스트 요청을 FOTA-DB 에 등록한다.

일반 OS Update(정식 배포)와 달리, DQA 가 특정 단말(SN)로 임의의 From→To 조합을
FOTA 로 받아보고 확인하려는 **테스트용** 등록이다. 그래서:

  · OS_Type = WhiteDQA{모델숫자}{안드로이드 접미}
      - 기본 안드로이드(제품별)는 접미 없음(예 PM95 기본=13 → WhiteDQA95)
      - 그 외는 안드로이드 디저트명 첫 글자(A15 Vanilla Ice Cream → V → WhiteDQA95V)
  · OS_Version = 해당 OS_Type 의 FOTA 최신값 + 1 부터 순번 배정
      - full+diff 요청: full-from=N, diff-from=N+1, to=N+2
      - 단일 요청(라벨 없음): from=N, to=N+2 (가운데 한 칸 비움 = full 관례)
  · DownloadPath: from-OS 는 dummy(가짜 http URL), to-OS 는 실제 빌드 4파일을
      메일 경로(.104/.26)에서 받아 Dropbox `/{제품}/FOTA_TEST/` 에 올린 실 URL.
  · 마지막에 SN 을 화이트리스트(osType=WhiteDQA…, api=to안드 API, endDate=+1년)에 추가.

라이브 FOTA 목록 조회(2026-07)로 확정한 패턴을 따른다. 모든 쓰기(FOTA/whitelist)는
게이트(allow_write) 뒤에 두고, 관리자가 미리보기 확인 후에만 실행한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from app.integrations import fota_system
from app.whitelist import service as WLS

# ── 안드로이드 → 디저트명 첫 글자(OS_Type 접미) ─────────────────────────────
# 기본 안드로이드(제품별 최초)는 접미가 없다. 그 외는 아래 글자를 붙인다.
ANDROID_DESSERT = {
    6: "M",   # Marshmallow
    7: "N",   # Nougat
    8: "O",   # Oreo
    9: "P",   # Pie   (예: WhiteDQA45P)
    10: "Q",  # Quince Tart
    11: "R",  # Red Velvet Cake
    12: "S",  # Snow Cone
    13: "T",  # Tiramisu
    14: "U",  # Upside Down Cake
    15: "V",  # Vanilla Ice Cream
    16: "B",  # Baklava
}

# 더미(from-OS) DownloadPath 크기 — 라이브 목록 관례값.
_DUMMY_FULL_SIZE = "1234000"
_DUMMY_DIFF_SIZE = "1000"

# ── 판별(1차 룰) ────────────────────────────────────────────────────────────
# FOTA 테스트 요청 본문에 공통으로 나타나는 표지어. 발신자(DQA)와 함께 본다.
_KW_FROM = re.compile(r"업데이트\s*할\s*현재\s*버전|현재\s*버전\s*\(?\s*from", re.I)
_KW_TO = re.compile(r"확인\s*할\s*버전|버전\s*\(?\s*to", re.I)
_KW_FOTA = re.compile(r"FOTA", re.I)


def is_fota_test_mail(sender: str, subject: str, body: str) -> bool:
    """DQA FOTA 테스트 등록 요청인가 — 발신자(DQA) + 본문 표지어(From/To + FOTA).

    제목 규칙([PMxx Axx][GMS MR] …)은 신뢰하지 않는다(사용자 경고). 본문 구조로 판단한다.
    """
    if not WLS.is_dqa_sender(sender or ""):
        return False
    text = body or ""
    return bool(_KW_FROM.search(text) and _KW_TO.search(text) and _KW_FOTA.search(text))


# ── 파싱(본문 → 구조) ───────────────────────────────────────────────────────
# 버전 문자열: '95.52 B3 (Jul 02 2026)', '95.50NB140 (Jul 03 2026)', '95.50 (Mar 18 2026)'
_VER_RE = re.compile(
    r"(\d{2,3}\.\d{2}\s*[A-Za-z0-9 ]*\([A-Za-z]{3}\.?\s+\d{1,2}[, ]+\d{4}\))"
)
_PFU_RE = re.compile(r"([A-Za-z0-9._-]+\.PFU)", re.I)
# 제품 인식 — 타입(2글자: PM/TR/MF/RF/PR…) + 클래스(2~3자리) + 변형문자(선택: PM84P의 P).
# 신규 빌드명 포맷은 '참고 힌트'일 뿐, 여기선 제품을 '인식'만 넓힌다(기준으로 삼지 않음).
_PRODUCT_RE = re.compile(r"\b(PM|TR|MF|RF|PR)\s*-?\s*(\d{2,3})([A-Za-z]?)\b", re.I)
# 'Android-15' / 'Android15' / 'android 9' 우선, 없으면 제목의 '[A15]' / '[A9]'.
_ANDROID_RE = re.compile(r"Android[\s-]*?(\d{1,2})\b", re.I)
_ANDROID_BRACKET_RE = re.compile(r"\bA\s?(\d{1,2})\b")
_FULL_LABEL = re.compile(r"\bFull\b", re.I)
_DIFF_LABEL = re.compile(r"\bDiff\b", re.I)


@dataclass
class OsRef:
    role: str            # "from-full" | "from-diff" | "from" | "to"
    dtype: str           # "FULL" | "DIFF"
    version: str         # 메일 버전 문자열 그대로 (OsInfo Version 칸)
    path: str            # UNC sdcard 경로
    full_pfu: str = ""   # to-OS 일 때 메일에 명시된 Full PFU 파일명(없으면 폴더에서 탐색)
    diff_pfu: str = ""   # to-OS 일 때 메일에 명시된 Diff PFU 파일명


@dataclass
class Parsed:
    product: str = ""            # 전체 모델명 예: PM84P, TR54, MF52, PM95
    product_type: str = ""       # 타입 2글자 예: PM, TR, MF
    product_num: str = ""        # 클래스+변형 예: 84P, 54, 95
    android: str = ""
    sns: list[str] = field(default_factory=list)
    has_diff: bool = False
    froms: list[OsRef] = field(default_factory=list)   # 1개(단일) 또는 2개(full+diff)
    to: OsRef | None = None
    warnings: list[str] = field(default_factory=list)


def _android_from_newformat(version: str) -> str:
    """신규 빌드명 버전에서 Android 를 '힌트'로 추출. 예 'PM84PG15.03'·'TR54G15.03 C1' → '15'.

    포맷 {…Customer(A/G/…)}{Android 2자리}.{Minor} 을 참고만 한다(기준 아님). 구 포맷('95.52')엔
    앞에 글자가 없어 매칭되지 않으므로 영향 없다.
    """
    m = re.search(r"[A-Za-z](\d{2})\.\d", version or "")
    if m:
        try:
            n = int(m.group(1))
            if 6 <= n <= 20:      # 상식적 안드로이드 범위만 인정
                return str(n)
        except ValueError:
            pass
    return ""


def _clean_ver_for_file(version: str) -> str:
    """버전 문자열 → 파일/식별용 축약. '95.52 B3 (Jul 02 2026)' → '95.52B3'."""
    v = re.sub(r"\([^)]*\)", "", version or "").strip()   # (날짜) 제거
    return re.sub(r"\s+", "", v)


def _ver_date(version: str) -> str:
    """버전 문자열의 (MMM DD YYYY) → 'YYYYMMDD'. 실패 시 ''."""
    m = re.search(r"\(([A-Za-z]{3})\.?\s+(\d{1,2})[, ]+(\d{4})\)", version or "")
    if not m:
        return ""
    try:
        dt = datetime.strptime(f"{m.group(1)} {int(m.group(2))} {m.group(3)}", "%b %d %Y")
        return dt.strftime("%Y%m%d")
    except ValueError:
        return ""


def _section(body: str, start_re: re.Pattern, end_re: re.Pattern | None) -> str:
    m = start_re.search(body or "")
    if not m:
        return ""
    tail = body[m.end():]
    if end_re:
        e = end_re.search(tail)
        if e:
            return tail[:e.start()]
    return tail


def _first_ver(text: str) -> str:
    m = _VER_RE.search(text or "")
    return m.group(1).strip() if m else ""


# 회신(quoted) 메일은 긴 UNC 경로를 여러 줄로 강제 줄바꿈한다(경로 중간에서 끊기기도).
# 게다가 경로가 sdcard 로 끝나지 않고 버전 폴더에서 끝나기도 하며, 뒤에 '<file://…>' 하이퍼링크나
# 한글 프로즈('준비' 등)가 붙는다. 그래서 '\\' 부터 다음 <file / 빈 줄 전까지를 잘라
# 내부 공백(줄바꿈)을 제거해 하나의 경로로 복원한다.
_FILEURI_RE = re.compile(r"<file:/*([^>]+)>", re.I | re.S)


def _extract_path(text: str) -> str:
    """줄바꿈으로 끊긴 경로까지 복원한다(sdcard 유무 무관). UNC 우선, 실패 시 <file://…>."""
    t = text or ""
    i = t.find("\\\\")
    if i != -1:
        rest = t[i:]
        end = len(rest)
        for term in ("<file", "\r\n\r\n", "\n\n"):
            j = rest.find(term)
            if j != -1:
                end = min(end, j)
        cand = re.sub(r"\s+", "", rest[:end]).rstrip("\\")
        if len(cand) > 3:
            return cand
    m = _FILEURI_RE.search(t)
    if m:
        raw = re.sub(r"\s+", "", m.group(1)).replace("/", "\\").rstrip("\\")
        return "\\\\" + raw.lstrip("\\")
    return ""


def parse_fota_test(subject: str, body: str, serials: list[str] | None = None) -> Parsed:
    """FOTA 테스트 메일 본문 → 구조화. 정확 토큰(경로/버전/PFU/SN)은 정규식으로만 뽑는다.

    serials: 룰(mail_filter)이 원문에서 뽑은 SN 목록. 주면 그대로 쓴다(가장 신뢰).
    """
    body = body or ""
    p = Parsed()

    # 제품·안드로이드 — 경로/제목에서
    hay = subject + "\n" + body
    mm = _PRODUCT_RE.search(hay)
    if mm:
        _typ = mm.group(1).upper()
        _cls = mm.group(2)
        _var = (mm.group(3) or "")
        p.product_type = _typ
        p.product_num = f"{_cls}{_var}"          # 클래스+변형 (예 84P, 54, 95)
        p.product = f"{_typ}{_cls}{_var}"         # 전체 모델 (예 PM84P, TR54)
    ma = _ANDROID_RE.search(hay)
    if ma:
        p.android = str(int(ma.group(1)))
    else:
        # 경로에 'Android15' 가 없으면 제목의 '[A15]'/'[A9]' 를 본다.
        mb = _ANDROID_BRACKET_RE.search(subject or "")
        if mb:
            p.android = str(int(mb.group(1)))

    # SN — 룰이 준 값 우선, 없으면 본문에서 10자리 토큰
    if serials:
        p.sns = [s for s in serials if WLS.is_valid_sn(s)]
    if not p.sns:
        p.sns = [s for s in re.findall(r"(?<![A-Za-z0-9])([A-Za-z0-9]{10})(?![A-Za-z0-9])", body)
                 if WLS.is_valid_sn(s) and any(c.isdigit() for c in s)]
        # 흔한 오검(단어) 방지: 최소 4자리 이상 숫자 포함
        p.sns = [s for s in p.sns if sum(c.isdigit() for c in s) >= 4]
    # 중복 제거(순서 유지)
    seen: set[str] = set()
    p.sns = [s for s in p.sns if not (s in seen or seen.add(s))]

    # From / To 블록 분리
    to_block = _section(body, _KW_TO, None)
    from_block = _section(body, _KW_FROM, _KW_TO)

    # ── From 파싱 (경로 기준) ──
    # 형식이 제각각이다: 'Full : {ver} : {path}'(라벨→버전) 도 있고
    # '{ver} : Full Image Update \n {path}'(버전→라벨) 도 있다. 라벨 위치에 의존하지 않도록
    # **각 UNC 경로를 하나의 from-OS 로 보고**, 그 경로 '앞 문맥'에서 버전·타입(Full/Diff)을 읽는다.
    unc_pos = [mo.start() for mo in re.finditer(r"\\\\", from_block)]
    if len(unc_pos) >= 2:
        p.has_diff = True
        for i, pos in enumerate(unc_pos):
            ctx = from_block[(unc_pos[i - 1] if i > 0 else 0):pos]
            ver = _first_ver(ctx)
            path = _extract_path(from_block[pos:])
            has_f = bool(_FULL_LABEL.search(ctx))
            has_d = bool(_DIFF_LABEL.search(ctx))
            dtype = "DIFF" if (has_d and not has_f) else "FULL"
            role = "from-diff" if dtype == "DIFF" else "from-full"
            if ver or path:
                p.froms.append(OsRef(role, dtype, ver, path))
        # 라벨을 못 읽어 전부 같은 타입이면 순서로 배정(첫째=Full, 나머지=Diff).
        if p.froms and all(r.dtype == p.froms[0].dtype for r in p.froms):
            p.froms[0].role, p.froms[0].dtype = "from-full", "FULL"
            for r in p.froms[1:]:
                r.role, r.dtype = "from-diff", "DIFF"
    else:
        # 단일: 버전 1개 + 경로 1개(같은 줄 또는 다음 줄)
        ver = _first_ver(from_block)
        if ver:
            p.froms.append(OsRef("from", "FULL", ver, _extract_path(from_block)))

    # ── To 파싱 ──
    to_ver = _first_ver(to_block)
    to_path = _extract_path(to_block)
    full_pfu = diff_pfu = ""
    for line in to_block.splitlines():
        m = _PFU_RE.search(line)
        if not m:
            continue
        name = m.group(1)
        if _FULL_LABEL.search(line):
            full_pfu = name
        elif _DIFF_LABEL.search(line):
            diff_pfu = name
    if to_ver:
        p.to = OsRef("to", "FULL", to_ver, to_path, full_pfu=full_pfu, diff_pfu=diff_pfu)

    # Android 미확정이면 신규 빌드명 버전에서 '힌트'로 보조 추출(예 PM84PG15.03 → 15).
    if not p.android:
        _av = _android_from_newformat(p.to.version if p.to else "")
        if not _av and p.froms:
            _av = _android_from_newformat(p.froms[0].version)
        if _av:
            p.android = _av

    # 경고
    if not p.product_num:
        p.warnings.append("제품(PMxx)을 찾지 못함")
    if not p.android:
        p.warnings.append("Android 버전을 찾지 못함")
    if not p.froms:
        p.warnings.append("From(현재) 버전을 찾지 못함")
    if not p.to:
        p.warnings.append("To(확인) 버전을 찾지 못함")
    if not p.sns:
        p.warnings.append("SN 을 찾지 못함")
    for r in p.froms + ([p.to] if p.to else []):
        if not r.path:
            p.warnings.append(f"{r.role} 경로(UNC)를 찾지 못함 — 수동 입력 필요")
    return p


# ── OS_Type / OS_Version 산정 ───────────────────────────────────────────────
def resolve_os_type(user_id: int, product: str, android: str, product_type: str = "") -> dict:
    """WhiteDQA os_type 확정. **FOTA 실제 존재로 확인**(포맷을 기준으로 유도하지 않음).

    후보를 여러 형태로 만들어 FOTA 에 있는 걸 채택한다:
      · PM 계열: WhiteDQA{클래스+변형}(+안드letter)  예 WhiteDQA95, WhiteDQA84P, WhiteDQA95V
      · TR/MF 등: WhiteDQA{타입+클래스+변형}(+안드letter)  예 WhiteDQATR54, WhiteDQAMF52
    두 형태를 모두 시도하므로 구/신 제품 모두 커버된다.
    반환 {os_type, latest, tried, error, note}. os_type='' 이면 신규(수동 확인 필요).
    """
    m = re.match(r"([A-Za-z]{2})(\d{2,3})([A-Za-z]?)", str(product or ""))
    if not m:
        return {"os_type": "", "latest": None, "tried": [], "error": "",
                "note": "제품(모델) 미확정"}
    typ = (product_type or m.group(1)).upper()
    cls = m.group(2)
    var = (m.group(3) or "")
    cv = f"{cls}{var}"          # 클래스+변형 예 84P, 54, 95, 52e
    tcv = f"{typ}{cv}"          # 타입+클래스+변형 예 PM84P, MF52e
    tc = f"{typ}{cls}"          # 타입+클래스(변형 제외) 예 MF52, TR54
    try:
        an = int(str(android))
        letter = ANDROID_DESSERT.get(an, "")
    except (TypeError, ValueError):
        letter = ""
    # 관례가 제품마다 달라(PM 은 타입 생략, TR/MF 는 타입 포함; 변형문자는 포함/제외 혼재)
    # 여러 형태를 만들고 **FOTA 에 실제 있는 것**을 채택한다. 변형포함형을 앞에 둔다.
    if typ == "PM":
        bases = [cv, cls, tcv, tc]
    else:
        bases = [tcv, tc, cv, cls]
    # 중복(변형 없으면 cv==cls, tcv==tc) 제거
    _bseen: set[str] = set()
    bases = [b for b in bases if not (b in _bseen or _bseen.add(b))]
    forms: list[str] = []
    for b in bases:
        if letter:
            forms.append(f"WhiteDQA{b}{letter}")
        forms.append(f"WhiteDQA{b}")
    seen: set[str] = set()
    cands = [x for x in forms if not (x in seen or seen.add(x))]

    tried: list[tuple[str, int | None]] = []
    for c in cands:
        lv, err = fota_system.get_latest_os_version(user_id, c)
        if err:
            return {"os_type": "", "latest": None, "tried": tried, "error": err, "note": ""}
        tried.append((c, lv))
        if lv is not None:
            note = "" if (letter and c.endswith(letter)) else "FOTA 실존 형태로 매칭"
            return {"os_type": c, "latest": lv, "tried": tried, "error": "", "note": note}
    return {"os_type": "", "latest": None, "tried": tried, "error": "",
            "note": f"FOTA 에 없는 신규 OS_Type({', '.join(c for c, _ in tried)}) — 수동 확인 필요"}


def assign_os_versions(parsed: Parsed, latest: int) -> dict:
    """os_version 배정. 반환 {role→os_version, to_osv, from_osvs}.

    full+diff: full-from=N, diff-from=N+1, to=N+2. 단일: from=N, to=N+2.
    """
    n = int(latest) + 1
    mapping: dict[str, int] = {}
    if parsed.has_diff:
        for r in parsed.froms:
            if r.role == "from-full":
                mapping["from-full"] = n
            elif r.role == "from-diff":
                mapping["from-diff"] = n + 1
        mapping["to"] = n + 2
    else:
        mapping["from"] = n
        mapping["to"] = n + 2
    return mapping


# ── 행 빌더 + Plan ──────────────────────────────────────────────────────────
@dataclass
class OiRow:
    os_type: str
    version: str
    os_version: int
    description: str
    release_note: str
    release_url: str

    def to_list(self) -> list:
        return [self.os_type, self.version, self.os_version, self.description,
                self.release_note, self.release_url, 0, 0, 0]


@dataclass
class DpRow:
    os_type: str
    os_version: int
    dtype: str                 # FULL | DIFF
    download_url: str          # from=dummy, to=Dropbox 공유링크(업로드 후 채움)
    md5: str
    filename: str
    size: str
    is_dummy: bool             # True=from(dummy), False=to(실파일)
    role: str = ""             # from-full/from-diff/from/to
    src_path: str = ""         # 실파일일 때 다운로드할 sdcard UNC 경로

    def to_list(self) -> list:
        return [self.os_type, "Dropbox", self.os_version, self.download_url,
                self.md5, self.dtype, self.filename, self.size]


@dataclass
class Plan:
    parsed: Parsed
    os_type: str
    latest: int
    product: str
    android: str
    api: int | None
    end_date: str
    oi_rows: list[OiRow] = field(default_factory=list)
    dp_rows: list[DpRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    note: str = ""
    #: OsInfo Version 칸에 붙는 제품 접두어(TR54G…)와 그 출처. 화면에 근거로 보여준다.
    version_prefix: str = ""
    version_prefix_src: str = ""


def _dummy_dp(os_type: str, osv: int, verclean: str, date: str, dtype: str, role: str) -> DpRow:
    base = f"{verclean}_{date}_base"
    suffix = "full" if dtype == "FULL" else "diff"
    size = _DUMMY_FULL_SIZE if dtype == "FULL" else _DUMMY_DIFF_SIZE
    return DpRow(
        os_type=os_type, os_version=osv, dtype=dtype,
        download_url=f"http://{base}_{suffix}.PFU",
        md5=f"http://{base}.PFU_{suffix}.md5",
        filename=f"{base}.PFU", size=size, is_dummy=True, role=role,
    )


def build_plan(user_id: int, parsed: Parsed, os_type_override: str = "") -> Plan:
    """파싱 결과 → 등록 Plan(OsInfo/DownloadPath 행 · api · endDate). FOTA 조회 포함.

    os_type_override: 관리자가 OS_Type 을 직접 지정하면 자동 해석 대신 그 값을 쓰고
    최신 os_version 만 조회한다(자동 매칭이 애매한 신규 안드로이드 대비 안전장치).
    """
    if os_type_override.strip():
        ot = os_type_override.strip()
        lv, err = fota_system.get_latest_os_version(user_id, ot)
        rt = {"os_type": ot if not err else "", "latest": lv, "tried": [(ot, lv)],
              "error": err, "note": (f"OS_Type 수동 지정({ot})"
                                      + (" — FOTA 에 없음(신규)" if lv is None and not err else ""))}
        if lv is None and not err:
            rt["latest"] = -1  # 신규 os_type: 0 부터 시작(latest+1=0)
    else:
        rt = resolve_os_type(user_id, parsed.product, parsed.android,
                             product_type=parsed.product_type)
    an = int(parsed.android) if str(parsed.android).isdigit() else None
    plan = Plan(
        parsed=parsed, os_type=rt["os_type"], latest=rt["latest"] if rt["latest"] is not None else -1,
        product=parsed.product, android=parsed.android,
        api=WLS.get_api(an) if an else None, end_date=WLS.get_end_date(),
        note=rt.get("note", ""),
    )
    if rt["error"]:
        plan.errors.append(f"FOTA 조회 실패: {rt['error']}")
        return plan
    if not rt["os_type"]:
        plan.errors.append(rt.get("note") or "OS_Type 를 확정하지 못했습니다.")
        return plan
    if plan.api is None:
        plan.warnings.append(f"Android {parsed.android} → API 매핑 없음(화이트리스트 api 확인 필요)")

    osv = assign_os_versions(parsed, plan.latest)
    desc = f"{parsed.product} A{parsed.android} DQA FOTA test"
    os_type = plan.os_type

    # ── OsInfo Version 표기 접두어 ─────────────────────────────────────────
    # 제품마다 표기가 다르다: TR54 는 'TR54G15.04 (Aug 22 2026)', PM95 는
    # '95.07 (Jun 11 2026)'. 메일 본문에서 버전을 뽑는 정규식은 숫자부터 잡기 때문에
    # 접두어가 통째로 떨어져 나간다(실측 2026-08-24: '15.04' 로 등록돼 수동 정정).
    # 접두어는 **제품관리의 '최초 OS 버전'** 에서 가져온다 — TR54G15.00 → 'TR54G'.
    from app import os_products as OSP

    vpre, vsrc = OSP.version_prefix(parsed.product, parsed.android)
    if not vsrc:
        # 제품관리에 없으면 메일이 알려 준 PFU 파일명에서 접두어를 읽는다
        # (TR54G15.04_20260822.PFU → 'TR54G'). 그래도 없으면 접두어 없이 간다.
        _pfu = ((parsed.to.full_pfu or parsed.to.diff_pfu) if parsed.to else "")
        vpre = OSP.split_version_prefix(_pfu)
        vsrc = f"메일의 PFU 파일명({_pfu})" if vpre else ""
        plan.warnings.append(
            f"제품관리에 {parsed.product or '(제품 미확정)'} A{parsed.android or '?'} 가 "
            "없습니다 — OsInfo 버전 표기를 직접 확인하세요"
            + (f" (지금은 메일 파일명에서 '{vpre}' 로 추정)" if vpre else ""))
    plan.version_prefix, plan.version_prefix_src = vpre, vsrc

    # Release Note URL — 이 흐름은 FOTA 테스트 메일에서 시작해 **회차 상태가 없다**.
    # 이번 회차에 올린 RN 도 없으므로 제품 소개 페이지 주소를 조합해 넣는다.
    # (OS Update 쪽은 회차 RN 의 Dropbox 링크를 쓴다 — app/pipeline/rn_dropbox.py)
    url = f"https://pointmobile.com/en/products-mobilecomputers/{parsed.product}"

    # 각 from/to os_version 별 OsInfo + DownloadPath 행.
    refs: list[tuple[OsRef, int]] = []
    for r in parsed.froms:
        refs.append((r, osv.get(r.role)))
    if parsed.to:
        refs.append((parsed.to, osv.get("to")))

    dtypes = ["FULL", "DIFF"] if parsed.has_diff else ["FULL"]
    for ref, v in refs:
        if v is None:
            continue
        # 접두어를 붙인 표기를 OsInfo 와 더미 파일명 양쪽에 쓴다 — FOTA 의 기존 행도
        # 둘이 같다('TR54G15.03 C1 (May 15 2026)' / 'TR54G15.03C1_20260515.PFU').
        vtext = OSP.apply_version_prefix(vpre, ref.version)
        plan.oi_rows.append(OiRow(os_type, vtext, v, desc, desc, url))
        verclean = _clean_ver_for_file(vtext)
        date = _ver_date(vtext)
        if ref.role == "to":
            # 실파일: 다운로드/업로드 후 url·md5·size·filename 이 채워진다.
            names = {"FULL": ref.full_pfu, "DIFF": ref.diff_pfu}
            for dt in dtypes:
                plan.dp_rows.append(DpRow(
                    os_type=os_type, os_version=v, dtype=dt, download_url="", md5="",
                    filename=names.get(dt, ""), size="", is_dummy=False,
                    role="to", src_path=ref.path,
                ))
        else:
            for dt in dtypes:
                plan.dp_rows.append(_dummy_dp(os_type, v, verclean, date, dt, ref.role))

    plan.warnings.extend(parsed.warnings)
    return plan


def osinfo_lists(plan: Plan) -> list[list]:
    return [r.to_list() for r in plan.oi_rows]


def downloadpath_lists(plan: Plan) -> list[list]:
    return [r.to_list() for r in plan.dp_rows]


# ── 파일서버 백엔드 매핑(.104/.26/.160) ─────────────────────────────────────
def _backend_and_rel(user_id: int, unc: str):
    """UNC(\\\\host\\share\\...) → (backend, rel, error). host 로 fs target 을 고른다."""
    from app.integrations.fileserver import FS_TARGETS, get_backend

    m = re.match(r"^\\\\([^\\]+)\\(.*)$", (unc or "").replace("/", "\\"))
    if not m:
        return None, "", f"경로 형식 오류: {unc}"
    host, rel = m.group(1), m.group(2)
    target = next((t for t, info in FS_TARGETS.items() if info["host"] == host), None)
    if not target:
        return None, "", f"알 수 없는 파일서버({host}) — [설정 → 파일 서버]에 등록 필요"
    b = get_backend(user_id, target)
    if b is None:
        return None, "", f"{target}({host}) 자격증명 없음 — [설정 → 파일 서버]에서 등록"
    return b, rel, ""


def _resolve_sdcard(backend, rel: str) -> tuple[str, str]:
    """메일의 sdcard 경로가 정확하지 않을 수 있어(폴더명 접미 생략) 퍼지 보정한다.

    메일 예: '...\\FormalRelease\\2026.07.11_95.52\\sdcard'
    실제  : '...\\FormalRelease\\2026.07.11_95.52_rejected\\sdcard'
    → 버전 폴더명을 **접두 일치**로 상위 폴더에서 찾는다. 반환 (rel_ok, note).
    """
    parts = [p for p in (rel or "").replace("/", "\\").split("\\") if p]
    # 1) 있는 그대로 시도
    try:
        backend.list_dir(rel)
        return rel, ""
    except Exception:  # noqa: BLE001
        pass
    if len(parts) < 2:
        return rel, ""
    # 종단이 'sdcard' 면 그 앞(버전 폴더)을, 아니면 종단 자체(버전 폴더)를 퍼지 매칭한다.
    if parts[-1].lower() == "sdcard":
        leaf = parts[-1]
        verfolder = parts[-2]
        parent = "\\".join(parts[:-2])
    else:
        leaf = ""
        verfolder = parts[-1]
        parent = "\\".join(parts[:-1])
    try:
        entries = backend.list_dir(parent)
    except Exception:  # noqa: BLE001
        return rel, ""
    vf = verfolder.lower()
    cands = [e.name for e in entries if getattr(e, "is_dir", True)
             and e.name.lower().startswith(vf)]
    if not cands:
        # 접두가 안 맞으면 날짜+버전 토큰만이라도 포함하는 폴더를 찾는다
        cands = [e.name for e in entries if getattr(e, "is_dir", True) and vf in e.name.lower()]
    if not cands:
        return rel, ""
    # 여러 개면 approved/MR 우선, 그다음 이름 긴 것(더 구체적)
    cands.sort(key=lambda n: (("approved" in n.lower()) or ("mr" in n.lower()), len(n)),
               reverse=True)
    picked = cands[0]
    note = f"폴더 보정: '{verfolder}' → '{picked}'" if picked != verfolder else ""
    segs = [parent, picked] + ([leaf] if leaf else [])
    return "\\".join(segs), note


class DqaCancelled(BaseException):
    """사용자가 ① 다운로드+업로드를 중단했다.

    BaseException 을 상속한다 — 진행률 콜백은 파일 복사 루프 한가운데에서 불리는데,
    그 자리를 감싸는 `except Exception` 들이 이걸 '다운로드 실패'로 둔갑시키면
    중단인지 고장인지 구분할 수 없다(app.progress.StageCancelled 와 같은 이유).
    """


# ── to-OS 실파일 다운로드 ───────────────────────────────────────────────────
def download_to_files(user_id: int, plan: Plan, workdir: str, on_progress=None):
    """to-OS 의 Full/Diff PFU(+각 md5)를 메일 경로(.104/.26)에서 받는다.

    반환 (downloaded, logs). downloaded = {dtype: {pfu, md5, filename, size}}.

    on_progress(name, done, total, idx, cnt) 를 주면 파일마다 받은 바이트를 알린다
    (PFU 는 1MB 마다, md5 는 끝났을 때 한 번). 수 GB 를 받는 동안 화면이 아무 말도
    없으면 사람이 기다릴지 말지 정할 수가 없다. 콜백에서 예외를 던지면 그대로
    올라간다 — 중단 요청을 이 자리에서 받는다.
    """
    import os

    from app.pipeline.os_download import _classify_pfus

    logs: list[str] = []
    downloaded: dict[str, dict] = {}
    to_rows = [r for r in plan.dp_rows if not r.is_dummy]
    if not to_rows:
        return downloaded, ["to-OS 대상 행이 없습니다."]

    src = to_rows[0].src_path
    backend, rel, err = _backend_and_rel(user_id, src)
    if err:
        return downloaded, ["✘ " + err]
    rel, note = _resolve_sdcard(backend, rel)
    if note:
        logs.append("· " + note)
    try:
        entries = backend.list_dir(rel)
    except Exception as e:  # noqa: BLE001
        return downloaded, logs + [f"✘ 폴더 접근 실패({src}): {e}",
                                   "  → 메일 경로의 폴더명을 확인하세요(빌드가 이동/삭제됐을 수 있음)."]
    # 경로가 버전 폴더까지만이고(sdcard 미포함) PFU 가 하위 sdcard 에 있으면 그리로 내려간다.
    if not any(e.name.lower().endswith(".pfu") for e in entries):
        sub = next((e.name for e in entries
                    if getattr(e, "is_dir", False) and e.name.lower() == "sdcard"), "")
        if sub:
            rel = rel.rstrip("\\") + "\\" + sub
            logs.append(f"· sdcard 하위 폴더로 이동: {rel}")
            try:
                entries = backend.list_dir(rel)
            except Exception as e:  # noqa: BLE001
                return downloaded, logs + [f"✘ sdcard 하위 접근 실패: {e}"]
    names = {e.name for e in entries}
    pfus = _classify_pfus(entries)          # [{type,name,size,md5}]
    by_type = {p["type"]: p for p in pfus}
    logs.append(f"· sdcard: {rel} — PFU {len(pfus)}개")

    cnt = len(to_rows)
    for idx, r in enumerate(to_rows, 1):
        # 메일에 명시된 파일명이 폴더에 있으면 그걸, 없으면 폴더 분류(FULL/DIFF)로.
        pfu = None
        if r.filename and r.filename in names:
            pfu = next((p for p in pfus if p["name"] == r.filename), None)
        if pfu is None:
            pfu = by_type.get(r.dtype)
        if pfu is None:
            logs.append(f"✘ {r.dtype} PFU 를 폴더에서 찾지 못함")
            continue
        cb = (lambda done, total, _n=pfu["name"], _i=idx:
              on_progress(_n, done, total, _i, cnt)) if on_progress is not None else None
        if cb is not None:
            cb(0, int(pfu.get("size") or 0))
        try:
            dst, sz = backend.download(f"{rel}/{pfu['name']}", workdir, on_progress=cb)
        except Exception as e:  # noqa: BLE001
            if isinstance(e, DqaCancelled):
                raise
            logs.append(f"✘ {pfu['name']} 다운로드 실패: {e}")
            continue
        md5_local = ""
        md5_name = pfu.get("md5") or (f"{pfu['name']}.md5" if f"{pfu['name']}.md5" in names else "")
        if md5_name and md5_name in names:
            try:
                md5_local, _ = backend.download(f"{rel}/{md5_name}", workdir)
            except Exception as e:  # noqa: BLE001
                logs.append(f"  ⚠ md5 다운로드 실패 {md5_name}: {e}")
            if on_progress is not None:
                on_progress(md5_name, 1, 1, idx, cnt)
        downloaded[r.dtype] = {"pfu": dst, "md5": md5_local,
                               "filename": pfu["name"],
                               # FOTA DownloadPath 의 size 관례에 맞춘다(바이트 아님).
                               "size": fota_system.dp_size(pfu["size"])}
        logs.append(f"✔ {r.dtype}: {pfu['name']} ({pfu['size']:,}B)"
                    + (f" + {os.path.basename(md5_local)}" if md5_local else " (md5 없음)"))
    return downloaded, logs


# ── Dropbox 업로드(/{제품}/FOTA_TEST) → 실 DownloadPath 행 채움 ──────────────
def upload_to_dropbox(user_id: int, plan: Plan, downloaded: dict, on_progress=None):
    """다운로드한 to-OS 파일을 Dropbox `/{제품}/FOTA_TEST/` 에 올리고 실 URL 로 채운다.

    반환 (ok, logs). on_progress 는 upload_files 로 그대로 넘어간다
    (name, sent, total, idx, cnt).
    """
    import os

    from app.integrations import dropbox_client as DB
    from app.integrations.creds import credential_user_id, get_dropbox_auth

    logs: list[str] = []
    token = get_dropbox_auth(credential_user_id())
    if not token:
        return False, ["✘ Dropbox 자격증명 없음 — [설정 → Dropbox] 등록 필요"]

    dest = f"{plan.product}/FOTA_TEST"
    local_paths: list[str] = []
    for info in downloaded.values():
        local_paths.append(info["pfu"])
        if info["md5"]:
            local_paths.append(info["md5"])
    if not local_paths:
        return False, ["✘ 업로드할 파일 없음(다운로드 먼저 실행)"]

    results = DB.upload_files(token, local_paths, dest_folder=dest,
                              on_progress=on_progress)
    by_name = {os.path.basename(r.name): r for r in results}
    logs.append(f"· Dropbox 대상: /{dest.strip('/')}")

    ok = True
    for r in plan.dp_rows:
        if r.is_dummy:
            continue
        info = downloaded.get(r.dtype)
        if not info:
            ok = False
            logs.append(f"✘ {r.dtype} 다운로드 결과 없음")
            continue
        r.filename = info["filename"]
        r.size = info["size"]
        pr = by_name.get(info["filename"])
        r.download_url = pr.link if (pr and pr.ok) else ""
        md5_base = os.path.basename(info["md5"]) if info["md5"] else ""
        mr = by_name.get(md5_base) if md5_base else None
        r.md5 = mr.link if (mr and mr.ok) else ""
        if not r.download_url:
            ok = False
        logs.append(f"{'✔' if r.download_url else '✘'} 업로드 {r.dtype} {r.filename}"
                    + (f" → {r.download_url[:70]}" if r.download_url else " (실패)"))
    return ok, logs


# ── FOTA 등록 / 화이트리스트 ─────────────────────────────────────────────────
def register_fota(user_id: int, plan: Plan, allow_write: bool | None = None) -> dict:
    """OsInfo + DownloadPath 미리보기(dry-run)용 — 두 종류를 한 번에 보여준다.

    실제 등록은 register_osinfo → register_downloadpath 로 **나눠서** 한다.
    """
    return fota_system.register_rows(
        user_id, osinfo_lists(plan), downloadpath_lists(plan), allow_write)


def register_osinfo(user_id: int, plan: Plan, allow_write: bool | None = None) -> dict:
    """③-1 OsInfo 만 등록하고 **되읽어 확인**한다(DownloadPath 는 보내지 않는다)."""
    return fota_system.register_rows(
        user_id, osinfo_lists(plan), None, allow_write)


def register_downloadpath(user_id: int, plan: Plan,
                          allow_write: bool | None = None) -> dict:
    """③-2 DownloadPath 등록 — OsInfo 가 FOTA 에 있는지 확인한 뒤에만 보낸다."""
    return fota_system.register_downloadpath_only(
        user_id, osinfo_lists(plan), downloadpath_lists(plan), allow_write)


def whitelist_devices(plan: Plan) -> list[dict]:
    return [{"sn": sn, "api": str(plan.api or ""), "endDate": plan.end_date,
             "ostype": plan.os_type, "partnumber": ""} for sn in plan.parsed.sns]


def register_whitelist(user_id: int, plan: Plan, allow_write: bool | None = None) -> dict:
    """SN 을 화이트리스트에 등록(osType=WhiteDQA…, api=to안드 API, endDate=+1년, PN 빈값).

    다른 WhiteList 작업과 같은 경로를 쓴다 — 신규는 엑셀 업로드, **이미 등록된 SN 은
    덮어쓴다**(사용자 확정 2026-08-10). 엑셀만 올리면 기존 등록이 그대로 남는다.
    """
    from app.whitelist import service as WLS

    return WLS.register_devices(user_id, whitelist_devices(plan),
                                allow_write=allow_write)
