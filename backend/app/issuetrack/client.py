"""Redmine REST(.json) 클라이언트 — **GET 전용**.

app/integrations/redmine.py 의 docstring 은 "REST 는 세션 쿠키로 401 이라 안 쓴다"고
적고 있는데, 절반만 맞다. 세션 쿠키로는 실제로 401 이지만 **Basic 인증(ID/PW)** 이나
API 키로는 정상 응답한다. 여기서는 이미 등록돼 있는 'redmine' 웹 편집 계정
(ID/PW)을 그대로 Basic 인증에 쓴다 — 새 자격증명 등록이 필요 없다.

안전장치: 이 모듈은 GET 외의 HTTP 메서드를 노출하지 않는다. Redmine 에 쓰기를
시도할 수 있는 코드가 실수로도 생기지 않게 하기 위함이다.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Iterator
from urllib.parse import urlparse

import requests

from app.integrations.creds import get_web_config

log = logging.getLogger(__name__)

TARGET = "redmine"
PROJECT = os.getenv("TW_REDMINE_PROJECT", "salesissue")

_TIMEOUT = (10, 60)          # (connect, read)
_PAGE_SIZE = 100             # Redmine 이 허용하는 limit 상한
_RETRY = 3
_UA = "TW-Automation/1.0 (issue monitor; read-only)"


class RedmineError(RuntimeError):
    pass


def _norm_base(raw: str) -> str:
    """등록된 base_url 이 경로/쿼리를 포함할 수 있어 scheme+host 까지만 남긴다."""
    u = urlparse((raw or "").strip())
    if u.scheme and u.netloc:
        return f"{u.scheme}://{u.netloc}"
    return (raw or "").strip().split("#")[0].split("?")[0].rstrip("/")


def base_url() -> str:
    cfg = get_web_config(0, TARGET)
    if cfg is None or not cfg.base_url:
        raise RedmineError(
            "'redmine' 웹 편집 계정이 없습니다. [설정 → 웹 편집 계정]에서 "
            "Redmine URL/아이디/비밀번호를 등록하세요."
        )
    return _norm_base(cfg.base_url)


def issue_url(issue_id: int) -> str:
    """이슈 웹 링크. 모델(LLM)이 호스트명을 지어내지 않도록 **코드가** 링크를 만든다."""
    try:
        return f"{base_url()}/issues/{issue_id}"
    except RedmineError:
        return f"/issues/{issue_id}"


class RedmineClient:
    """읽기 전용 Redmine REST 클라이언트."""

    def __init__(self) -> None:
        cfg = get_web_config(0, TARGET)
        if cfg is None or not cfg.base_url:
            raise RedmineError(
                "'redmine' 웹 편집 계정이 없습니다. [설정 → 웹 편집 계정]에서 "
                "Redmine URL/아이디/비밀번호를 등록하세요."
            )
        self.base = _norm_base(cfg.base_url)
        self._s = requests.Session()
        self._s.auth = (cfg.username or "", cfg.password or "")
        self._s.headers.update({"Accept": "application/json", "User-Agent": _UA})

    # ---- 저수준 -----------------------------------------------------------
    def _get(self, path: str, **params: Any) -> dict:
        url = self.base + path
        last: Exception | None = None
        for attempt in range(_RETRY):
            try:
                r = self._s.get(url, params=params or None, timeout=_TIMEOUT)
            except requests.RequestException as e:      # 네트워크 단절 등
                last = e
            else:
                if r.status_code == 200:
                    try:
                        return r.json()
                    except ValueError as e:
                        raise RedmineError(f"{path}: JSON 아님 ({r.text[:120]})") from e
                if r.status_code in (401, 403):
                    raise RedmineError(
                        f"{path}: 인증 실패({r.status_code}). Redmine 계정/비밀번호를 확인하세요."
                    )
                if r.status_code == 404:
                    raise RedmineError(f"{path}: 없음(404)")
                last = RedmineError(f"{path}: HTTP {r.status_code}")
            # 5xx·타임아웃만 재시도. 사내 서버라 짧게 백오프.
            if attempt < _RETRY - 1:
                time.sleep(1.5 * (attempt + 1))
        raise RedmineError(str(last))

    # ---- 조회 -------------------------------------------------------------
    def whoami(self) -> dict:
        return (self._get("/users/current.json") or {}).get("user") or {}

    def iter_issues(
        self,
        *,
        project: str = PROJECT,
        updated_since: datetime | None = None,
        limit_total: int | None = None,
    ) -> Iterator[dict]:
        """프로젝트 이슈를 페이지 단위로 흘려보낸다(열림/닫힘 모두).

        updated_since 를 주면 그 시각 이후 갱신분만. 정렬을 updated_on 이 아니라
        **id 오름차순**으로 고정하는 이유: 수집 도중 누가 이슈를 갱신하면 updated_on
        정렬은 페이지 경계가 밀려 건너뛰는 이슈가 생긴다. id 는 불변이라 안전하다.
        """
        params: dict[str, Any] = {
            "project_id": project,
            "status_id": "*",       # 기본값은 열린 이슈만이라 반드시 지정
            "sort": "id:asc",
            "limit": _PAGE_SIZE,
        }
        if updated_since is not None:
            params["updated_on"] = ">=" + _fmt_utc(updated_since)

        offset, seen = 0, 0
        while True:
            data = self._get("/issues.json", offset=offset, **params)
            rows = data.get("issues") or []
            if not rows:
                return
            for row in rows:
                yield row
                seen += 1
                if limit_total and seen >= limit_total:
                    return
            offset += len(rows)
            total = int(data.get("total_count") or 0)
            if offset >= total:
                return

    def issue_detail(self, issue_id: int) -> dict:
        """journals·attachments 를 포함한 이슈 상세.

        include 는 저장하는 것만 요청한다. relations/watchers 를 붙이면 Redmine 이
        추가 질의를 하느라 응답이 눈에 띄게 느려지는데, 지금은 쓰지 않는다.
        """
        data = self._get(f"/issues/{issue_id}.json", include="journals,attachments")
        return data.get("issue") or {}

    def enumerations(self) -> dict[str, list[dict]]:
        """상태/우선순위/트래커 id→이름 매핑용 원본."""
        out: dict[str, list[dict]] = {}
        for kind, path, key in (
            ("status", "/issue_statuses.json", "issue_statuses"),
            ("priority", "/enumerations/issue_priorities.json", "issue_priorities"),
            ("tracker", "/trackers.json", "trackers"),
        ):
            try:
                out[kind] = self._get(path).get(key) or []
            except RedmineError as e:
                # 권한에 따라 막힐 수 있다. 매핑이 없으면 화면에 id 가 그대로 보일 뿐
                # 수집 자체는 계속돼야 한다.
                log.warning("Redmine %s 목록 조회 실패: %s", kind, e)
                out[kind] = []
        return out

    def memberships(self, project: str = PROJECT) -> list[dict]:
        """프로젝트 참여자 — journal detail 의 담당자 id 를 이름으로 바꾸는 데 쓴다."""
        try:
            return self._get(
                f"/projects/{project}/memberships.json", limit=_PAGE_SIZE
            ).get("memberships") or []
        except RedmineError as e:
            log.warning("Redmine memberships 조회 실패: %s", e)
            return []


# ---- 값 파서 --------------------------------------------------------------
def _fmt_utc(dt: datetime) -> str:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_dt(s: str | None) -> datetime | None:
    """Redmine 의 '2026-07-21T02:13:44Z' → naive UTC datetime."""
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    return None
