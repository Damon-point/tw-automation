"""S/N → 모델 — Service Portal 단말 마스터에서 읽어온다. **읽기만 한다.**

첨부 서식은 파트너마다 제각각이다(실측: 같은 파일 안에서도 머리글이 'SERİNO' ·
'SERİ NUMARA' · 'SERİ NO' 로 다르고 모델 블록이 옆으로 세 벌 늘어서 있었다). 서식을
읽어 모델을 뽑으면 다음 파일에서 또 깨진다.

그래서 **S/N 만 뽑고 모델은 포털에 묻는다**(사용자 확정 2026-08-10).
실측: 메일 [1947] 의 75건이 전부 조회됐고 첨부의 모델과 100% 일치했다.

브라우저를 쓰지 않는다. 화면이 쓰는 것과 같은 API 를 HTTP 로 직접 부른다.
  POST /login/login.json                              {loginId, loginPw}
  POST /select/Warranty.getWarrantySeriNoList/action.json   {seriNoList:[...]}

응답 봉투는 {responseVO:{header:{status},body:{docs:[...]}}} 이고, **실패해도 HTTP 는
200 이라 header.status 를 봐야 한다.**
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

TARGET = "service_portal"
DEFAULT_BASE = "https://serviceportal.pointmobile.com"
LOOKUP_SQLID = "Warranty.getWarrantySeriNoList"
CHUNK = 1000          # IN 목록 한 번에 보낼 개수. 1만 건이 0.2초.
TIMEOUT = 60


@dataclass
class Lookup:
    """조회 결과. 못 찾은 것과 실패한 것을 나눠 담지 않고 **한데 모은다**.

    포털이 안 뜨든 그 S/N 이 마스터에 없든, 사람이 할 일은 같다 — 모델을 알 수 없으니
    따로 모아 두고 나머지만 진행한다(사용자 확정 2026-08-10).
    """
    found: dict = field(default_factory=dict)      # S/N(대문자) → {model, part_no, ...}
    failed: list = field(default_factory=list)     # 모델을 못 얻은 S/N (원래 표기)
    error: str = ""                                # 조회 자체가 실패한 사유

    @property
    def ok(self) -> bool:
        return not self.error

    def model_of(self, sn: str) -> str:
        return (self.found.get((sn or "").upper()) or {}).get("model", "")

    def by_model(self, serials: list[str]) -> dict:
        """{모델: [S/N …]} — 화면에서 모델별 표를 그리는 데 쓴다. 순서는 입력 순."""
        out: dict[str, list[str]] = {}
        for sn in serials:
            m = self.model_of(sn)
            if m:
                out.setdefault(m, []).append(sn)
        return out


def _base() -> str:
    from app.integrations.creds import get_web_config

    cfg = get_web_config(0, TARGET)
    base = (cfg.base_url if cfg else "") or DEFAULT_BASE
    return base.strip().split("#")[0].rstrip("/")


def _credentials() -> tuple[str, str]:
    from app.integrations.creds import get_web_config

    cfg = get_web_config(0, TARGET)
    if cfg is None or not cfg.username or not cfg.password:
        raise RuntimeError("Service Portal 계정이 없습니다 — "
                           "[설정 → 웹 편집 계정 → Service Portal]에서 등록하세요.")
    return cfg.username, cfg.password


class _Session:
    """로그인된 requests 세션 하나. 앱 전체가 돌려 쓴다."""

    def __init__(self) -> None:
        import requests

        self.base = _base()
        self.s = requests.Session()
        self.s.headers.update({"Content-Type": "application/json;charset=utf-8",
                               "Accept": "application/json, text/plain, */*"})
        self.user = ""
        self._pw = ""

    def login(self) -> None:
        self.user, self._pw = _credentials()
        docs = self._post("/login/login.json",
                          {"loginId": self.user, "loginPw": self._pw})
        if not docs:
            raise RuntimeError("Service Portal 로그인 실패 — 아이디/비밀번호를 확인하세요.")

    def select(self, sqlid: str, body: dict, *, retry: bool = True) -> list[dict]:
        try:
            return self._post(f"/select/{sqlid}/action.json", body)
        except _Unauthorized:
            if not retry:
                raise
            self.login()
            return self._post(f"/select/{sqlid}/action.json", body)

    def _post(self, path: str, body: dict) -> list[dict]:
        import requests

        try:
            r = self.s.post(self.base + path, json=body, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise RuntimeError(f"포털 연결 실패 — {type(e).__name__}: {e}") from e
        try:
            j = r.json()
        except ValueError as e:
            head = (r.text or "")[:100].replace("\n", " ")
            raise RuntimeError(f"응답이 JSON 이 아닙니다 — {head!r}") from e
        vo = j.get("responseVO") or {}
        status = (vo.get("header") or {}).get("status")
        if status == 401:
            raise _Unauthorized()
        if status != 200:
            raise RuntimeError(f"포털 오류 status={status}")
        docs = (vo.get("body") or {}).get("docs")
        if isinstance(docs, dict):
            docs = [docs]
        return docs if isinstance(docs, list) else []


class _Unauthorized(Exception):
    pass


_session: _Session | None = None
_lock = threading.Lock()


def _get_session(*, fresh: bool = False) -> _Session:
    global _session
    with _lock:
        if _session is None or fresh:
            s = _Session()
            s.login()
            _session = s
        return _session


def lookup(serials: list[str]) -> Lookup:
    """S/N 목록 → 모델. 못 찾은 것과 조회 실패는 failed 로 모은다.

    이 매퍼는 `#{item}` 을 그대로 비교한다(warranty.xml) — 등록 화면이 쓰는
    getSeriNoList 가 UPPER() 를 쓰는 것과 다르다. **대문자로 올려 보내야** 소문자
    S/N 을 '없음' 으로 잘못 세지 않는다.
    """
    res = Lookup()
    want = [s.strip() for s in serials if (s or "").strip()]
    if not want:
        return res

    try:
        sess = _get_session()
    except Exception as e:  # noqa: BLE001
        res.error = str(e)
        res.failed = list(want)
        return res

    up = sorted({s.upper() for s in want})
    try:
        for i in range(0, len(up), CHUNK):
            part = up[i:i + CHUNK]
            for d in sess.select(LOOKUP_SQLID, {"seriNoList": part}):
                sn = str(d.get("SeriNo") or "").upper()
                if not sn:
                    continue
                res.found[sn] = {
                    "model": str(d.get("ModelNm") or "").strip(),
                    "part_no": str(d.get("PartNo") or "").strip(),
                    "description": str(d.get("Description") or "").strip(),
                    "wrt_start": str(d.get("WrtStartDate") or "").strip(),
                    "wrt_end": str(d.get("WrtEndDate") or "").strip(),
                }
    except Exception as e:  # noqa: BLE001
        res.error = f"{type(e).__name__}: {e}"
        res.failed = list(want)
        return res

    # 모델을 못 얻은 것 — 마스터에 없거나 모델이 비어 있다
    res.failed = [s for s in want if not (res.found.get(s.upper()) or {}).get("model")]
    return res


def drop_session() -> None:
    """세션을 버린다. 계정을 바꿨을 때."""
    global _session
    with _lock:
        _session = None
