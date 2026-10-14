"""⑥ Knowledge Base(Confluence) Release Note — 새 버전 엔트리 생성·파싱.

실제 KB RN **42개 페이지 · 165개 버전 엔트리** 분석으로 확정한 구조를 그대로 만든다:

  <ac:layout-section type=single><ac:layout-cell>
    <h2><strong>{버전} ({날짜})</strong></h2>
    <ul><li><strong>SDK: v{sdk}</strong></li>
        <li><strong>Security Patch: <a href=PULS>{날짜}</a></strong></li></ul>   ← 둘 다 상단
    <ac:structured-macro ui-expand title="Release Notes">
      <h3>VoC</h3>              <table> (NEW)/(IMPROVED)/(FIXED) </table>
      <h3>EmKit</h3>            <table> … </table>
      <h3>Operating System</h3> <table> … </table>   ← **섹션이 여러 개**
    </...>

섹션 체계(실측 빈도·평균위치 기반, 아래 SECTIONS 순서가 실제 문서 순서):
  VoC(0.02) · EmKit(0.27) · Scanner / ScanSettings(0.54) · Enterprise Storage(0.60)
  · Wireless(0.67) · Device Settings(0.69) · Network(0.82) · Operating System(0.83) · Others
모든 섹션은 예외 없이 NEW/IMPROVED/FIXED 표를 갖는다(실측: 표없음 0건).
"""
from __future__ import annotations

import os
import re
import uuid
from app.integrations.claude_cli import have_auth  # noqa: E402

_COLOR = {"new": "255,102,0", "improved": "51,102,255", "fixed": "51,153,102"}
_LABEL = {"new": "NEW", "improved": "IMPROVED", "fixed": "FIXED"}
_BUCKETS = ("new", "improved", "fixed")
_MODEL = os.getenv("RN_RESEARCH_MODEL", "claude-sonnet-5")

# 실제 RN 에서 쓰이는 섹션(문서 등장 순서대로). 새 엔트리는 이 순서로 만든다.
SECTIONS = [
    "VoC",
    "EmKit",
    "Scanner / ScanSettings",
    "Enterprise Storage",
    "Wireless",
    "Device Settings",
    "Network",
    "Operating System",
    "Others",
]

# 실제 문서의 표기 흔들림 → 표준 섹션명으로 정규화(실측 44종 → 9종).
_SECTION_ALIASES = {
    "operation system": "Operating System",
    "settings": "Device Settings",
    "keypad": "Device Settings",
    "scanner settings": "Scanner / ScanSettings",
    "scansettings": "Scanner / ScanSettings",
    "scan settings": "Scanner / ScanSettings",
    "scanner": "Scanner / ScanSettings",
    "bluetooth": "Wireless",
    "beacon": "Wireless",
}


def normalize_section(name: str) -> str:
    """문서에 적힌 섹션명 → 표준 섹션명. ('PM90 VoC'·'Operation System' 등 흡수)"""
    s = re.sub(r"\s+", " ", (name or "")).strip()
    if not s:
        return "Others"
    if re.search(r"\bVoC\b", s, re.I):          # 'PM451 VoC', 'PM90 Android 9 VoC'
        return "VoC"
    if re.match(r"Initial Release", s, re.I):    # 첫 릴리즈 전용 섹션은 이름 유지
        return s
    low = s.lower()
    if low in _SECTION_ALIASES:
        return _SECTION_ALIASES[low]
    for canon in SECTIONS:
        if low == canon.lower():
            return canon
    if "emkit" in low:
        return "EmKit"
    if "scan" in low:
        return "Scanner / ScanSettings"
    if "operat" in low and "system" in low:
        return "Operating System"
    if "enterprise storage" in low:
        return "Enterprise Storage"
    return "Others"


def _order_key(title: str) -> int:
    t = normalize_section(title)
    return SECTIONS.index(t) if t in SECTIONS else len(SECTIONS)


# Rule A: 사내발견 버그·안정성 수정은 개별 항목화하지 않고 이 한 줄로 뭉친다.
#  실제 RN 에는 이 문구가 없어(실측 0건) 기본은 끈다. 켜더라도 **VoC 가 아니라**
#  Operating System(시스템 전반) 섹션에 넣는다 — VoC 는 고객 제보 건 전용이다.
_STABILITY_LINE = "Improved overall system stability and fixed minor bugs."
_STABILITY_SECTION = "Operating System"

_SYS = (
    "당신은 PointMobile Knowledge Base Release Note 편집기입니다. 고객용 변경 문구들을 "
    "**섹션**과 **범주**로 분류하고, 각 항목을 짧고 자연스러운 영문 한 줄로 다듬습니다.\n"
    "[섹션] 실제 RN 에서 쓰는 것만 사용하세요:\n"
    "  · VoC — 고객이 제보/요청해서 반영된 건(고객 이슈 대응). 사내 발견 건은 여기 넣지 마세요.\n"
    "  · EmKit — EmKit 계열 앱/툴(EmKit, EmLogTool, LogTrace, Update, SAM, HDMI, E-Label 등)\n"
    "  · Scanner / ScanSettings — 스캐너·스캔 설정·심볼로지\n"
    "  · Enterprise Storage — 엔터프라이즈 스토리지\n"
    "  · Wireless — Wi-Fi·Bluetooth·NFC·Beacon 등 무선\n"
    "  · Device Settings — 단말 설정·키패드·디스플레이·전원 등\n"
    "  · Network — 모바일 데이터·APN·이더넷 등 네트워크\n"
    "  · Operating System — OS/플랫폼/시스템 전반, 보안·안정성\n"
    "  · Others — 위 어디에도 안 맞는 것\n"
    "[범주] new(추가된 기능) / improved(기존 동작·성능 개선) / fixed(버그 수정)\n"
    "규칙: 결과 없는 조사·QA/검증·사용자 비가시 내부 항목은 제외. 보안패치 항목은 만들지 마세요"
    "(시스템이 엔트리 상단에 따로 넣습니다). 원문에 없는 내용은 지어내지 말 것.\n"
    '반드시 JSON 으로만: {"items": [{"section": "...", "bucket": "new|improved|fixed", "text": "..."}]}'
)


def categorize_sections(
    product: str, version: str, changes: list[str], revision: str = "",
    fold_stability: bool = False,
) -> list[dict]:
    """공개 변경 목록 → 섹션별 [{title, new, improved, fixed}] (실제 문서 순서).

    changes: rn_public_changes(공개 선별) 를 넣는다.
    revision: 검수 후 사람이 준 추가 수정 지시(있으면 Claude 가 최우선 반영).
    fold_stability: 사내발견 버그 뭉침 한 줄을 Operating System 에 추가할지(기본 끔).
    """
    buckets: dict[str, dict[str, list[str]]] = {}

    def _add(section: str, bucket: str, text: str) -> None:
        t = (text or "").strip()
        if not t:
            return
        sec = normalize_section(section)
        b = bucket if bucket in _BUCKETS else "improved"
        d = buckets.setdefault(sec, {k: [] for k in _BUCKETS})
        if t not in d[b]:
            d[b].append(t)

    if changes and have_auth():
        try:
            import anthropic

            from app.pipeline.ai_edit import _extract_json

            body = "\n".join(f"- {c}" for c in changes)
            extra = (f"\n[사람의 추가 수정 지시 — 최우선 반영]: {revision}\n"
                     if (revision or "").strip() else "")
            prompt = (f"제품 {product} · 버전 {version}\n변경 목록:\n{body}\n{extra}\n"
                      "위 규칙대로 섹션·범주로 분류한 JSON 을 반환하세요.")
            client = anthropic.Anthropic()
            resp = client.messages.create(
                model=_MODEL, max_tokens=4000, system=_SYS,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
            for it in (_extract_json(text) or {}).get("items") or []:
                _add(str(it.get("section", "")), str(it.get("bucket", "")).lower(),
                     str(it.get("text", "")))
        except Exception:  # noqa: BLE001
            for c in changes:            # 실패 시: 분류 없이 Others/improved 로 보존
                _add("Others", "improved", c)
    elif changes:
        for c in changes:
            _add("Others", "improved", c)

    if fold_stability:
        has = any(_STABILITY_LINE.lower()[:20] in i.lower()
                  for d in buckets.values() for i in d["improved"])
        if not has:
            _add(_STABILITY_SECTION, "improved", _STABILITY_LINE)

    out = [{"title": t, **{k: v[k] for k in _BUCKETS}} for t, v in buckets.items()]
    out = [s for s in out if any(s[k] for k in _BUCKETS)]
    out.sort(key=lambda s: _order_key(s["title"]))
    return out


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# 실제 KB RN 상단: SDK 와 Security Patch 를 굵게, 보안패치는 PULS 링크로 건다.
_SECPATCH_HREF = "https://pointmobile.com/en/products-business-puls"


def _section_table(sec: dict) -> str:
    """섹션 1개 → <h3>제목</h3><table> (NEW)/(IMPROVED)/(FIXED) 행 </table>."""
    rows = ""
    for k in _BUCKETS:
        items = [i for i in (sec.get(k) or []) if str(i).strip()]
        if not items:
            continue
        lis = "".join(f"<li>{_esc(str(i).strip())}</li>" for i in items)
        rows += (
            f'<tr><th><span style="color: rgb({_COLOR[k]});">({_LABEL[k]})</span></th>'
            f"<td><ul>{lis}</ul></td></tr>"
        )
    if not rows:
        return ""
    return (
        f"<h3>{_esc(sec.get('title') or 'Others')}</h3>"
        '<table class="fixed-width wrapped">'
        '<colgroup><col style="width: 111.0px;" /><col style="width: 701.0px;" /></colgroup>'
        f"<tbody>{rows}</tbody></table>"
    )


def secpatch_date(s: str) -> str:
    """보안패치 날짜 → KB 표기('2026-07-05' → 'Jul 05, 2026'). 못 읽으면 원문."""
    from datetime import datetime

    t = (s or "").strip()
    m = re.search(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", t)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return datetime(y, mo, d).strftime("%b %d, %Y")
        except ValueError:
            return t
    m = re.search(r"([A-Z][a-z]{2})\s+(\d{1,2}),?\s+(\d{4})", t)
    return f"{m.group(1)} {int(m.group(2)):02d}, {m.group(3)}" if m else t


def with_system_lines(sections: list[dict], rows) -> list[dict]:
    """선별표에서 **반영**으로 둔 시스템 두 줄(보안패치 · 뭉침)을 VoC 에 얹는다.

    실제 KB 관례다 — PM68 (Android 15) 68.04 (2026-06) 는 상단 <ul> 에 Security Bulletin
    을 두고도, Release Notes 안 VoC 표에 (IMPROVED) 로
      · Updated security patches to May 5, 2026.
      · Improved system stability and bug fixed.
    를 **또** 적었다. KB 145개 버전 블록 중 내용이 빈 블록은 14개뿐이고 그중 11개가 최초
    출시다(조사 2026-09-08). 즉 보통 회차는 비워 두지 않는다.

    반영 여부는 사람이 선별표에서 정한 그대로 따른다 — 여기서 판단하지 않는다.
    """
    from app.pipeline import rn_select

    sysrows = rn_select.system_rows(rows or [])
    lines = [
        str((sysrows.get(k) or {}).get("public") or "").strip()
        for k in ("security", "stability")
        if str((sysrows.get(k) or {}).get("include") or "").upper() == "Y"
    ]
    lines = [x for x in lines if x]
    if not lines:
        return list(sections or [])
    out = [dict(s) for s in (sections or [])]
    voc = next((s for s in out if s.get("title") == "VoC"), None)
    if voc is None:
        voc = {"title": "VoC", "new": [], "improved": [], "fixed": []}
        out.insert(0, voc)          # VoC 가 맨 위(실측 순서)
    voc["improved"] = list(voc.get("improved") or []) + lines
    return out


def build_entry_html(version_full: str, sdk: str, sections: list[dict],
                     security_patch: str = "", os_download_url: str = "") -> str:
    """확정된 KB 구조로 새 버전 엔트리(layout-section) HTML 생성.

    상단 <ul> = SDK + Security Patch(+ 최신 블록이면 OS Download Link),
    그 아래 Release Notes 펼침 안에 **섹션별 표**.
    sections: [{title, new, improved, fixed}] (categorize_sections 결과 또는 편집기 결과).

    문구는 2026-08-12 사용자가 KB 전체를 직접 손보며 정한 형태를 따른다:
      · Security Patch 는 **버전 블록마다** 둔다 — 'This build is compliant up to
        Android Security Bulletin of {날짜}.' (날짜는 'Jul 05, 2026' 표기)
      · OS Download Link 는 **문서당 하나**, 가장 최신 버전 블록에만 둔다.
        그래서 여기서는 url 을 받은 경우에만 넣고, 이전 최신 블록의 링크는
        insert_entry 가 걷어낸다.
    """
    body = "".join(_section_table(s) for s in (sections or []))
    sdk_li = (f'<li style="font-weight: bold;"><strong>SDK: v{_esc(sdk)}</strong></li>'
              if (sdk or "").strip() else '<li><strong>SDK: </strong></li>')
    sp = secpatch_date(security_patch)
    sp_li = (f'<li><span><em>This build is compliant up to Android Security Bulletin of '
             f'<a href="{_SECPATCH_HREF}">{_esc(sp)}</a>.</em></span></li>') if sp else ""
    dl = (os_download_url or "").strip()
    dl_li = f'<li><a href="{_esc(dl)}">OS Download Link</a></li>' if dl else ""
    mid = str(uuid.uuid4())
    return (
        '<ac:layout-section ac:type="single"><ac:layout-cell>'
        f"<h2><strong>{_esc(version_full)}</strong></h2>"
        f"<ul>{sdk_li}{sp_li}{dl_li}</ul>"
        f'<ac:structured-macro ac:name="ui-expand" ac:schema-version="1" ac:macro-id="{mid}">'
        '<ac:parameter ac:name="expanded">true</ac:parameter>'
        '<ac:parameter ac:name="title">Release Notes</ac:parameter>'
        f"<ac:rich-text-body>{body}</ac:rich-text-body></ac:structured-macro>"
        "</ac:layout-cell></ac:layout-section>"
    )


# ── 파싱(편집기용) ─────────────────────────────────────────────────────────
_TAG_RE = re.compile(r"<[^>]+>")


def _text(html: str) -> str:
    """태그 제거 + 엔티티 복원 + 공백 정리(편집기에 한 줄로 보여주기 위함)."""
    s = _TAG_RE.sub(" ", html or "")
    s = (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
         .replace("&nbsp;", " ").replace("&quot;", '"'))
    return re.sub(r"\s+", " ", s).strip()


# 버전 블록 = 'single' 레이아웃 섹션. **문서에서 처음 나오는 것이 최신 버전**이고,
# 새 버전은 그 앞에 들어간다.
#
# 예전에는 앵커를 '<h2><strong>' 으로 잡았는데, 머리글 표기가 문서마다 흔들린다:
#   PM84P  <h2><strong style="letter-spacing: -0.008em;">   (strong 에 속성)
#   TR54   <h2>TR54G15.03 (Apr 28 2026)</h2>                (strong 자체가 없음)
# 그래서 TR54 는 첫 블록을 건너뛰고 **두 번째 블록 앞**에 끼어들었다(실측 2026-08-13).
# 표기에 기대지 말고 섹션 자체를 앵커로 쓴다. 다만 <h2> 조차 없는 셀은 버전 블록이
# 아닐 수 있으므로 그때는 넣지 않는다.
_SINGLE_SEC_RE = re.compile(r'<ac:layout-section\s+ac:type="single">', re.I)
_HAS_H2_RE = re.compile(r"<h2\b", re.I)
#: 'OS Download Link' 항목 통째로 — 최신이 아니게 된 블록에서 걷어낼 때 쓴다.
_DL_LI_RE = re.compile(
    r"<li>(?:(?!</li>).)*?OS\s*Download\s*Link(?:(?!</li>).)*?</li>", re.I | re.S)
#: 사람이 페이지에서 손으로 고친 모양 — 링크가 목록을 감싼다.
#:   <a href="…"><ul><li>OS Download Link</li></ul></a>   (실측 2026-10-13 PM95 A13 95.07C1)
#: 안쪽 <li> 만 지우면 빈 <a><ul></ul></a> 껍데기가 남으므로 바깥까지 통째로 걷는다.
_DL_WRAP_RE = re.compile(
    r"<a\b[^>]*>\s*(?:<ul>\s*)?<li>(?:(?!</li>).)*?OS\s*Download\s*Link(?:(?!</li>).)*?</li>"
    r"\s*(?:</ul>\s*)?</a>", re.I | re.S)


def _strip_dl(block: str) -> str:
    """블록 안의 OS Download Link 를 모양 가리지 않고 걷어낸다."""
    return _DL_LI_RE.sub("", _DL_WRAP_RE.sub("", block))


def _count_dl(block: str) -> int:
    return len(_DL_WRAP_RE.findall(block)) + len(_DL_LI_RE.findall(_DL_WRAP_RE.sub("", block)))


def _first_version_block(s: str) -> tuple[int, int] | None:
    """문서에서 **버전 제목(<h2>)이 있는 첫 단일 섹션** 의 (시작, 끝). 없으면 None.

    맨 앞 섹션을 최신 버전으로 단정하면 안 된다 — 페이지 위에 안내 상자를 두는 일이 있다.
    실측 2026-10-13 PM95 (Android 13): 맨 앞이 'PM95 is available with Android 13 and
    Android 15 …' 안내 상자(단일 섹션, 제목 없음)라 95.08 엔트리 삽입이 실패했다.
    """
    m = _SINGLE_SEC_RE.search(s)
    while m:
        i = m.start()
        nxt = _SINGLE_SEC_RE.search(s, i + 1)
        end = nxt.start() if nxt else len(s)
        if _HAS_H2_RE.search(s[i:end]):
            return i, end
        m = nxt
    return None


def sdcard_full_url(st: dict) -> str:
    """이번 회차 sdcard **FULL** zip 의 Dropbox 링크(③ Dropbox 업로드 결과). 없으면 ''."""
    for u in (st or {}).get("sdcard_uploads") or []:
        if str(u.get("type", "")).upper().startswith("FULL") and u.get("url"):
            return str(u["url"]).strip()
    return ""


_UL_CLOSE_RE = re.compile(r"</ul>", re.I)
_H2_CLOSE_RE = re.compile(r"</h2>", re.I)


def set_os_download_link(storage: str, url: str) -> tuple[str, bool]:
    """**최신 버전 블록**(문서의 첫 버전 블록)의 OS Download Link 를 url 로 맞춘다.

    KB 편집본은 Submission 갈래에서 만들어진다 — ③ Dropbox 업로드보다 앞이라 그때는
    sdcard 링크가 없어 새 블록에 링크가 빠지고, 직전 최신 블록의 링크는 규칙대로
    걷힌다. 그래서 문서에 OS Download Link 가 하나도 없는 채로 올라갔다(실측 2026-10-13
    job-32 PM452 · job-33 PM352). Approval 에서 올리기 전에 이 함수로 채운다.
    사용자: "KB 업데이트할 때 가장 최신 OS 버전에 OS Download Link가 들어가는데 sdcard
    full zip 링크가 걸려야됩니다".

    블록 안의 기존 링크 항목은 지우고 새로 하나 넣는다(문서당 하나 규칙). 넣는 자리는
    블록의 첫 <ul> 끝(SDK · Security Patch 다음). <ul> 이 없으면 <h2> 뒤에 새로 만든다.
    url 이 비면 손대지 않는다. (new_storage, 바뀌었나)
    """
    s = storage or ""
    url = (url or "").strip()
    if not url:
        return s, False
    found = _first_version_block(s)
    if found is None:
        return s, False
    i, end = found
    block = s[i:end]
    li = f'<li><a href="{_esc(url)}">OS Download Link</a></li>'
    if li in block and _count_dl(block) == 1:
        return s, False
    block2 = _strip_dl(block)
    h2 = _H2_CLOSE_RE.search(block2)
    ul = _UL_CLOSE_RE.search(block2, h2.end() if h2 else 0)
    if ul:
        block2 = block2[:ul.start()] + li + block2[ul.start():]
    elif h2:
        block2 = block2[:h2.end()] + f"<ul>{li}</ul>" + block2[h2.end():]
    else:
        return s, False
    return s[:i] + block2 + s[end:], True


def apply_os_download_link(mail_id) -> tuple[int, str]:
    """검수 작업공간의 이 작업 KB 사본에 sdcard FULL 링크를 채운다. (바꾼 사본 수, 링크)."""
    from app.pipeline import review_workspace as WS
    from app.pipeline.state import get_state

    url = sdcard_full_url(get_state(mail_id) or {})
    if not url:
        return 0, ""
    n = 0
    for it in WS.list_items(mail_id):
        if it.get("source") != "release_notes":
            continue
        new, changed = set_os_download_link(it.get("edited_html", "") or "", url)
        if changed:
            it = dict(it)
            it["edited_html"] = new
            WS.put(it)
            n += 1
    return n, url


def insert_entry(storage: str, entry_html: str) -> tuple[str, bool]:
    """새 버전 엔트리를 최신 버전 섹션 바로 앞에 삽입. (new_storage, 삽입성공).

    버전 블록이 하나도 없는 페이지(최초 출시로 새로 만든 뼈대, 또는 아직 아무것도 안 올린
    페이지)에는 앵커가 있을 수 없다. 그때는 실패로 두지 말고 **레이아웃 맨 끝에 첫 블록으로**
    넣는다 — 예전에는 그대로 실패해서 최초 출시 제품은 ⑥이 아무 것도 못 했다.
    """
    s = storage or ""
    # 최신 버전 블록 = 버전 제목이 있는 **첫** 단일 섹션. 그 위의 안내 상자 · 목차 같은
    # 섹션은 그대로 맨 위에 둔다(실측 2026-10-13 PM95 A13).
    found = _first_version_block(s)
    if found is not None:
        i, end = found
        # OS Download Link 는 **문서당 하나**, 최신 블록에만 둔다(사용자 확정 2026-08-12).
        # 새 블록이 최신이 되므로 여태 최신이던 블록에서 링크를 걷어낸다. 범위를 그 블록
        # 하나로 자른 뒤 지운다 — 문서 전체에 걸면 아래 블록의 링크까지 지운다.
        # 손으로 고친 <a><ul><li>…</li></ul></a> 모양도 통째로 걷는다.
        prev_head = _strip_dl(s[i:end])
        return s[:i] + entry_html + prev_head + s[end:], True
    # 버전 블록이 하나도 없다 = 새로 만든 뼈대이거나 아직 아무 것도 안 올린 페이지.
    # 실패로 두지 말고 첫 블록으로 넣는다 — 예전에는 최초 출시 제품이 여기서 막혔다.
    j = s.rfind("</ac:layout>")
    if j >= 0:
        return s[:j] + entry_html + s[j:], True
    if not s.strip():
        return entry_html, True
    return s + entry_html, True
