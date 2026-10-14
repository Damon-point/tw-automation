"""원자적 파일 저장 유틸.

**왜 필요한가:** 파일 경로에 직접 쓰는 도중(예: `wb.save(path)`, `path.write_text(...)`)
프로세스가 끊기면 원본이 잘린 채 손상된다(2026-07-14 160서버 03_DownloadPath.xlsx 사고).
그래서 모든 '실제 파일 경로' 저장은 **같은 디렉터리의 임시파일에 먼저 쓰고 → os.replace 로 교체**한다.
os.replace 는 같은 볼륨(로컬/동일 SMB 공유)에서 원자적이라, 중간에 끊겨도 원본은 손상되지 않는다
(버려지는 건 .tmp 뿐).

버퍼(BytesIO) 저장은 대상이 아니다 — 파일이 아니라 메모리라 손상 위험이 없다.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Union

PathLike = Union[str, "os.PathLike[str]"]


def _tmp_for(path: Path) -> Path:
    # 같은 디렉터리에 유니크 임시명(동시 저장 충돌 방지). os.replace 원자성 위해 같은 폴더 필수.
    return path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"


def _finish(tmp: Path, path: Path) -> None:
    """임시파일을 제자리로 옮긴다. 못 옮기면 **곧바로 덮어써서라도 남긴다.**

    파일서버 공유에는 '만들기·쓰기는 되는데 지우기는 안 되는' 권한이 흔하다. 이름
    바꾸기는 원래 이름을 지우는 일이라 DELETE 권한이 필요해서, 그런 공유에서는
    `os.replace` 만 WinError 5 로 튕긴다. 그러면 임시 파일만 남고 정작 문서는 올라가지
    않는다 — 실측(2026-09-08): RN for Sales 6건 12개 파일이 전부 그 꼴로 남아 있었다.

    원자성을 잃더라도 **문서가 올라가는 쪽**을 고른다. 옮기지 못했다고 아무것도 남기지
    않으면 사람이 손으로 올려야 한다. 덮어쓰기마저 실패하면 그때는 그대로 올린다.
    """
    try:
        os.replace(str(tmp), str(path))
        return
    except OSError as e:
        first = e
    # 물러서기 — 임시파일 내용을 최종 이름으로 그대로 쓴다(지우기 권한이 필요 없다).
    try:
        with open(str(tmp), "rb") as fin, open(str(path), "wb") as fout:
            shutil.copyfileobj(fin, fout, 1024 * 1024)
            fout.flush()
            os.fsync(fout.fileno())
    except BaseException:
        try:
            os.remove(str(tmp))
        except OSError:
            pass
        raise first
    # 덮어쓰기가 됐으면 임시파일은 치운다. 그것마저 막히면 남겨 둘 수밖에 없다
    # (지우기 권한이 없는 공유다 — 사람이 정리한다).
    try:
        os.remove(str(tmp))
    except OSError:
        pass


def atomic_write_bytes(path: PathLike, data: bytes) -> Path:
    """bytes 를 원자적으로 저장."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_for(path)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    _finish(tmp, path)
    return path


def atomic_write_text(path: PathLike, text: str, encoding: str = "utf-8") -> Path:
    """텍스트를 원자적으로 저장."""
    return atomic_write_bytes(path, text.encode(encoding))


def atomic_save_workbook(wb, path: PathLike) -> Path:
    """openpyxl Workbook 을 원자적으로 저장(임시파일에 save 후 교체)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_for(path)
    try:
        wb.save(str(tmp))
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    _finish(tmp, path)
    return path


def file_lock(path: PathLike, timeout: float = 10.0):
    """'읽고 → 고치고 → 쓰기' 를 직렬화하는 파일 락(컨텍스트 매니저).

    **왜 필요한가:** 원자적 쓰기는 파일이 깨지는 것만 막는다. 두 사람이 같은 JSON 을
    동시에 고치면 나중에 쓴 쪽이 앞사람의 변경을 통째로 덮어써서 **조용히 사라진다**.
    그래서 갱신 구간 전체를 잠근다.

    같은 프로세스 안의 스레드는 threading.Lock 으로, 프로세스 밖(혹시 모를 중복 실행)은
    잠금 파일을 O_EXCL 로 만들어 막는다. timeout 을 넘기면 그냥 진행한다
    (죽은 프로세스가 남긴 잠금 파일 때문에 앱이 멈추는 것이 더 나쁘다).
    """
    return _FileLock(Path(path), timeout)


_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def _thread_lock_for(key: str) -> threading.Lock:
    with _thread_locks_guard:
        return _thread_locks.setdefault(key, threading.Lock())


class _FileLock:
    def __init__(self, path: Path, timeout: float) -> None:
        self.path = path
        self.lock_path = path.parent / f".{path.name}.lock"
        self.timeout = timeout
        self._tlock = _thread_lock_for(str(path.resolve()).lower())
        self._fd: int | None = None

    def __enter__(self):
        self._tlock.acquire()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    # 오래된 잠금(비정상 종료 잔재)으로 보고 진행한다.
                    break
                time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        if self._fd is not None:
            try:
                os.close(self._fd)
                self.lock_path.unlink()
            except OSError:
                pass
            self._fd = None
        self._tlock.release()
        return False


def atomic_copyfile(src: PathLike, dst: PathLike, *, on_progress=None) -> Path:
    """src 파일 내용을 dst 로 원자적으로 복사(대용량 스트리밍 + 교체).

    on_progress(done_bytes, total_bytes) 를 주면 1MB 덩어리마다 부른다 — 수 GB 짜리
    OS 이미지를 받을 때 화면에 남은 양을 보여 주려고 뚫어 둔 구멍이다. 콜백이
    예외를 던지면 그대로 올라간다(중단 요청을 이 자리에서 받는다). 그때 tmp 는
    지워지고 원본·대상은 손대지 않은 상태로 남는다.
    """
    src = Path(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_for(dst)
    total = 0
    if on_progress is not None:
        try:
            total = src.stat().st_size
        except OSError:
            total = 0
    try:
        with open(src, "rb") as fin, open(tmp, "wb") as fout:
            done = 0
            while True:
                chunk = fin.read(1024 * 1024)
                if not chunk:
                    break
                fout.write(chunk)
                if on_progress is not None:
                    done += len(chunk)
                    on_progress(done, total)
            fout.flush()
            os.fsync(fout.fileno())
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    _finish(tmp, dst)
    return dst
