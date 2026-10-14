"""Dropbox 클라이언트: 파일 업로드 후 직접 다운로드 링크를 만든다.

Dropbox API v2 (액세스 토큰 필요). 프로토타입은 단순 업로드(150MB 미만)만 지원한다.
"""
from __future__ import annotations
from app import shadow as _shadow

import time
from dataclasses import dataclass

import dropbox
from dropbox.exceptions import ApiError
from dropbox.files import WriteMode

# 단순 업로드 한계 (Dropbox 권장: 150MB 초과는 업로드 세션 사용)
SIMPLE_UPLOAD_LIMIT = 140 * 1024 * 1024

# ── 순단 재시도(지수 백오프) ──────────────────────────────────────────────
# 대용량 업로드 중 회선이 끊기면(WinError 10054 = ConnectionReset) requests 가
# ConnectionError 로 던지고, 순간 DNS 실패(getaddrinfo)도 같은 계열이다. 이런
# 일시적 네트워크 오류는 잠깐 쉬었다 다시 시도하면 대개 복구된다.
_RETRY_TRIES = 4          # 최초 1회 + 재시도 3회
_RETRY_BASE = 1.0         # 대기: 1s → 2s → 4s
_RETRY_CAP = 8.0


def _is_retryable(e: Exception) -> bool:
    """네트워크 일시 오류(순단·DNS 블립·5xx·속도제한)면 재시도 대상."""
    import requests

    if isinstance(e, (requests.exceptions.ConnectionError,
                      requests.exceptions.Timeout,
                      ConnectionResetError, ConnectionError, TimeoutError)):
        return True
    try:
        from dropbox.exceptions import InternalServerError, RateLimitError
        return isinstance(e, (InternalServerError, RateLimitError))
    except Exception:  # noqa: BLE001
        return False


def _retry(fn, *args, **kwargs):
    """fn(*args, **kwargs) 를 호출하되, 일시적 네트워크 오류면 지수 백오프로 재시도한다.

    재시도 불가능한 오류(인증·경로 없음 등)나 마지막 시도 실패는 그대로 예외를 올린다.
    호출당 개별 API(업로드 청크·복사·링크 생성)를 감싸 부분 실패만 다시 태운다.
    """
    last: Exception | None = None
    for i in range(_RETRY_TRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            if not _is_retryable(e) or i == _RETRY_TRIES - 1:
                raise
            last = e
            time.sleep(min(_RETRY_CAP, _RETRY_BASE * (2 ** i)))
    if last:
        raise last


@dataclass
class UploadResult:
    name: str
    dropbox_path: str = ""
    link: str = ""
    ok: bool = False
    error: str = ""
    skipped: bool = False        # 이미 같은 파일(이름+크기)이 있어 업로드를 생략함


# ── 접속 자격증명 → Dropbox 클라이언트 ────────────────────────────────
# auth 는 아래 셋 중 하나:
#   - str            : 레거시 단기 access token
#   - dict           : {"refresh_token","app_key","app_secret"} 또는 {"access_token"}
#   - DropboxAuth 유사 객체(creds.DropboxAuth): 같은 속성들
def _auth_fields(auth) -> tuple[str, str, str, str]:
    if isinstance(auth, str):
        return "", "", "", auth
    if isinstance(auth, dict):
        return (
            auth.get("refresh_token", "") or "",
            auth.get("app_key", "") or "",
            auth.get("app_secret", "") or "",
            auth.get("access_token", "") or "",
        )
    return (
        getattr(auth, "refresh_token", "") or "",
        getattr(auth, "app_key", "") or "",
        getattr(auth, "app_secret", "") or "",
        getattr(auth, "access_token", "") or "",
    )


def _make_dbx(auth) -> "dropbox.Dropbox":
    """auth 로부터 Dropbox 클라이언트를 만든다. refresh 방식이면 access token 자동 갱신."""
    rt, ak, asec, at = _auth_fields(auth)
    if rt and ak and asec:
        return dropbox.Dropbox(oauth2_refresh_token=rt, app_key=ak, app_secret=asec)
    if at:
        return dropbox.Dropbox(at)
    raise ValueError("Dropbox 자격증명이 없습니다.")


# ── OAuth (refresh token 발급) — 로컬 앱용 no-redirect 플로우 ──────────
def build_authorize_url(app_key: str, app_secret: str) -> str:
    """사용자가 방문해 권한을 허용할 인증 URL 을 만든다(offline=refresh token 발급)."""
    from dropbox import DropboxOAuth2FlowNoRedirect

    flow = DropboxOAuth2FlowNoRedirect(app_key, app_secret, token_access_type="offline")
    return flow.start()


def exchange_code(app_key: str, app_secret: str, code: str) -> tuple[bool, str]:
    """인증 코드를 refresh_token 으로 교환한다. (ok, refresh_token 또는 에러메시지)."""
    from dropbox import DropboxOAuth2FlowNoRedirect

    try:
        flow = DropboxOAuth2FlowNoRedirect(app_key, app_secret, token_access_type="offline")
        res = flow.finish(code.strip())
        rt = getattr(res, "refresh_token", "") or ""
        if not rt:
            return False, "refresh_token 이 반환되지 않았습니다(앱 권한/offline 설정 확인)."
        return True, rt
    except Exception as e:  # noqa: BLE001
        return False, str(e)


@dataclass
class DbxEntry:
    name: str
    path: str          # Dropbox 전체 경로 (예: /OSImage/automation-test/file.zip)
    is_dir: bool
    size: int = 0


def list_folder(token, folder: str) -> list[DbxEntry]:
    """Dropbox 폴더의 하위 파일/폴더 목록을 반환한다.

    folder: '/OSImage/automation-test' 형태(루트는 '' 또는 '/'). 폴더가 없으면 예외 발생.
    """
    from dropbox.files import FileMetadata, FolderMetadata

    # Dropbox API 는 루트를 빈 문자열로 표현한다.
    path = folder.strip()
    if path in ("", "/"):
        path = ""
    else:
        path = "/" + path.strip("/")

    dbx = _make_dbx(token)
    entries: list[DbxEntry] = []
    res = _retry(dbx.files_list_folder, path)
    while True:
        for md in res.entries:
            if isinstance(md, FolderMetadata):
                entries.append(DbxEntry(name=md.name, path=md.path_display, is_dir=True))
            elif isinstance(md, FileMetadata):
                entries.append(
                    DbxEntry(name=md.name, path=md.path_display, is_dir=False, size=md.size)
                )
        if not res.has_more:
            break
        res = _retry(dbx.files_list_folder_continue, res.cursor)
    # 폴더 먼저, 그다음 이름순
    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return entries


def delete_path(token, path: str) -> tuple[bool, str]:
    """Dropbox 경로(파일/폴더)를 삭제한다. (ok, error)."""
    if _shadow.blocked("dropbox", "delete", path=path):
        return True, ""
    dbx = _make_dbx(token)
    try:
        _retry(dbx.files_delete_v2, path)
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def shared_link_for(token, path: str) -> str:
    """이미 올라가 있는 Dropbox 파일 경로에 대한 다운로드 링크(dl=1)를 반환한다."""
    if _shadow.blocked("dropbox", "shared_link", path=path):
        return _shadow_link(path)
    dbx = _make_dbx(token)
    return _shared_link(dbx, path)


def _shadow_link(path: str) -> str:
    """검증 모드에서 올리지 않은 파일에 붙이는 가짜 링크(.invalid 라 열리지 않는다)."""
    return "https://shadow.invalid/dropbox" + path


def _direct_link(url: str) -> str:
    """공유 링크를 '들어가면 바로 다운로드'되는 형태로 변환한다.

    Dropbox 공유 링크는 기본이 미리보기(dl=0)다. dl=1 을 붙이면 다운로드가 강제된다.
    - 구형 링크: https://www.dropbox.com/s/xxx/file?dl=0
    - 신형 링크: https://www.dropbox.com/scl/fi/xxx/file?rlkey=yyy&dl=0
    두 형식 모두에서 dl 파라미터를 1 로 강제한다.
    """
    if "dl=0" in url:
        return url.replace("dl=0", "dl=1")
    if "dl=1" in url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}dl=1"


def test_token(token) -> tuple[bool, str]:
    """연결 테스트. token 은 access token 문자열 또는 refresh 자격증명(dict/DropboxAuth)."""
    try:
        dbx = _make_dbx(token)
        acct = dbx.users_get_current_account()
        return True, f"Dropbox 연결 성공: {acct.name.display_name}"
    except Exception as e:  # noqa: BLE001
        return False, f"Dropbox 연결 실패: {e}"


def _shared_link(dbx: dropbox.Dropbox, path: str) -> str:
    try:
        meta = _retry(dbx.sharing_create_shared_link_with_settings, path)
        return _direct_link(meta.url)
    except ApiError:
        # 이미 링크가 있으면 기존 것을 사용
        links = _retry(lambda: dbx.sharing_list_shared_links(path=path, direct_only=True).links)
        return _direct_link(links[0].url) if links else ""


def _remote_size(dbx: dropbox.Dropbox, path: str) -> int | None:
    """Dropbox 경로에 이미 있는 파일의 크기(byte). 없으면 None.

    resume 판단용 — 로컬 크기와 같으면 '이미 올라간 것'으로 보고 재업로드를 생략한다.
    """
    from dropbox.files import FileMetadata

    try:
        md = _retry(dbx.files_get_metadata, path)
    except Exception:  # noqa: BLE001 (경로 없음 등 → 그냥 '없음' 취급)
        return None
    return md.size if isinstance(md, FileMetadata) else None


# 업로드 세션 청크 크기(대용량: 150MB 초과 시 조각 전송)
_SESSION_CHUNK = 100 * 1024 * 1024  # 100MB


def _upload_stream(dbx, f, size: int, dbx_path: str, on_progress=None) -> None:
    """열린 파일/스트림 f 를 Dropbox 에 올린다. 150MB 초과면 업로드 세션으로 청크 전송.

    f 는 read(n)/tell() 을 지원해야 한다(파일 객체 또는 io.BytesIO). 메모리 절약을 위해
    한 번에 최대 _SESSION_CHUNK 만 읽는다(2GB 도 안전).

    on_progress(sent, total) 를 주면 청크를 하나 보낼 때마다 부른다. 청크가 100MB 라
    2.4GB 짜리면 스물몇 번 갱신된다 — 화면에 '멈춘 것처럼 보이는' 구간을 없애려는 것이다.
    """
    def _tick(sent: int) -> None:
        if on_progress is not None:
            on_progress(sent, size)

    if size <= SIMPLE_UPLOAD_LIMIT:
        # 청크를 먼저 메모리로 읽어두고 감싸면, 재시도 때 같은 바이트를 그대로 다시 보낸다.
        _retry(dbx.files_upload, f.read(), dbx_path, mode=WriteMode.overwrite)
        _tick(size)
        return
    from dropbox.files import CommitInfo, UploadSessionCursor

    chunk = f.read(_SESSION_CHUNK)
    sess = _retry(dbx.files_upload_session_start, chunk)
    cursor = UploadSessionCursor(session_id=sess.session_id, offset=f.tell())
    commit = CommitInfo(path=dbx_path, mode=WriteMode.overwrite)
    _tick(cursor.offset)
    while size - f.tell() > _SESSION_CHUNK:
        chunk = f.read(_SESSION_CHUNK)
        _retry(dbx.files_upload_session_append_v2, chunk, cursor)
        cursor.offset = f.tell()
        _tick(cursor.offset)
    _retry(dbx.files_upload_session_finish, f.read(_SESSION_CHUNK), cursor, commit)
    _tick(size)


def upload_bytes(
    token, name: str, data: bytes, dest_folder: str = "/tw-uploads",
    *, skip_if_same: bool = True,
) -> UploadResult:
    """메모리 상의 바이트를 Dropbox 에 올리고 다운로드 링크를 만든다(대용량 자동 세션).

    skip_if_same: 대상 경로에 같은 크기 파일이 이미 있으면 재업로드하지 않고 링크만 만든다(resume).
    """
    if _shadow.blocked("dropbox", "upload_bytes", name=name, dest_folder=dest_folder, size=len(data)):
        p = "/" + dest_folder.strip("/") + "/" + name
        return UploadResult(name=name, dropbox_path=p, link=_shadow_link(p), ok=True, skipped=True)
    import io

    res = UploadResult(name=name)
    try:
        dbx = _make_dbx(token)
        folder = "/" + dest_folder.strip("/")
        dbx_path = f"{folder}/{name}"
        if skip_if_same and _remote_size(dbx, dbx_path) == len(data):
            res.skipped = True
        else:
            _upload_stream(dbx, io.BytesIO(data), len(data), dbx_path)
        res.dropbox_path = dbx_path
        res.link = _shared_link(dbx, dbx_path)
        res.ok = True
    except Exception as e:  # noqa: BLE001
        res.error = str(e)
    return res


def _delete_if_exists(dbx, path: str) -> None:
    """overwrite 복사를 위해 대상 경로가 있으면 지운다(없으면 무시)."""
    try:
        dbx.files_delete_v2(path)
    except Exception:  # noqa: BLE001
        pass


def upload_and_copy(
    token, local_path: str, dest_paths: list[str], *, skip_if_same: bool = True,
) -> list[UploadResult]:
    """local_path 를 dest_paths[0] 에 **한 번만 업로드**하고, 나머지 경로엔 **서버 내 복사**한다.

    대용량 파일을 여러 폴더에 넣을 때 재업로드를 피해 빠르다. 각 목적지의 공유 링크까지 만든다.
    skip_if_same: 대상에 같은 크기 파일이 이미 있으면 업로드/복사를 건너뛴다(부분 실패 후 재실행 resume).
    반환: dest_paths 순서에 대응하는 UploadResult 리스트.
    """
    if _shadow.blocked("dropbox", "upload_and_copy", local_path=local_path, dest_paths=dest_paths):
        import os as _os
        return [UploadResult(name=_os.path.basename(local_path), dropbox_path=d, link=_shadow_link(d),
                             ok=True, skipped=True) for d in dest_paths]
    import os

    dbx = _make_dbx(token)
    name = os.path.basename(local_path)
    size = os.path.getsize(local_path)
    results: list[UploadResult] = []
    if not dest_paths:
        return results

    # 1) 첫 목적지: 이미 같은 크기면 생략, 아니면 실제 업로드(overwrite)
    first = dest_paths[0]
    r0 = UploadResult(name=name)
    try:
        if skip_if_same and _remote_size(dbx, first) == size:
            r0.skipped = True
        else:
            with open(local_path, "rb") as f:
                _upload_stream(dbx, f, size, first)
        r0.dropbox_path = first
        r0.link = _shared_link(dbx, first)
        r0.ok = True
    except Exception as e:  # noqa: BLE001
        r0.error = str(e)
    results.append(r0)

    # 2) 나머지 목적지: 이미 같은 크기면 생략, 아니면 서버 내 복사(기존 있으면 지우고 복사) + 링크
    for dp in dest_paths[1:]:
        r = UploadResult(name=name)
        if not r0.ok:
            r.error = "원본 업로드 실패로 복사 생략"
            results.append(r)
            continue
        try:
            if skip_if_same and _remote_size(dbx, dp) == size:
                r.skipped = True
            else:
                _delete_if_exists(dbx, dp)
                _retry(dbx.files_copy_v2, first, dp)
            r.dropbox_path = dp
            r.link = _shared_link(dbx, dp)
            r.ok = True
        except Exception as e:  # noqa: BLE001
            r.error = str(e)
        results.append(r)
    return results


def upload_files(
    token, local_paths: list[str], dest_folder: str = "/tw-uploads",
    *, skip_if_same: bool = True, on_progress=None,
) -> list[UploadResult]:
    """로컬 파일들을 Dropbox 에 올리고 각 파일의 다운로드 링크를 만든다(대용량 자동 세션).

    skip_if_same: 대상에 같은 크기 파일이 이미 있으면 재업로드를 건너뛴다(resume).
    on_progress(name, sent, total, idx, cnt) 를 주면 파일마다 진행 상황을 알린다
    (idx 는 1부터). 건너뛴 파일도 sent=total 로 한 번 부른다 — 화면에서 빠진 것처럼
    보이지 않게 하려는 것이다.
    """
    if _shadow.blocked("dropbox", "upload_files", local_paths=local_paths, dest_folder=dest_folder):
        import os as _os
        out = []
        for _i, _l in enumerate(local_paths, 1):
            _n = _os.path.basename(_l)
            _p = "/" + dest_folder.strip("/") + "/" + _n
            if on_progress is not None:
                _sz = _os.path.getsize(_l) if _os.path.exists(_l) else 0
                on_progress(_n, _sz, _sz, _i, len(local_paths))
            out.append(UploadResult(name=_n, dropbox_path=_p, link=_shadow_link(_p), ok=True, skipped=True))
        return out
    import os

    dbx = _make_dbx(token)
    results: list[UploadResult] = []
    folder = "/" + dest_folder.strip("/")
    cnt = len(local_paths)
    for idx, local in enumerate(local_paths, 1):
        name = os.path.basename(local)
        res = UploadResult(name=name)
        cb = (lambda sent, total, _n=name, _i=idx: on_progress(_n, sent, total, _i, cnt))             if on_progress is not None else None
        try:
            size = os.path.getsize(local)
            dbx_path = f"{folder}/{name}"
            if skip_if_same and _remote_size(dbx, dbx_path) == size:
                res.skipped = True
                if cb is not None:
                    cb(size, size)
            else:
                if cb is not None:
                    cb(0, size)
                with open(local, "rb") as f:
                    _upload_stream(dbx, f, size, dbx_path, on_progress=cb)
            res.dropbox_path = dbx_path
            res.link = _shared_link(dbx, dbx_path)
            res.ok = True
        except Exception as e:  # noqa: BLE001
            res.error = str(e)
        results.append(res)
    return results
