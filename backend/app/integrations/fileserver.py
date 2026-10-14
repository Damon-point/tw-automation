"""내부 파일서버(SMB) 접근 — 서버별 계정으로 인증한다.

두 파일서버가 모두 SMB(Windows 공유)이고 계정이 서로 다르다. target 으로 구분:
  · fs104 = 192.168.123.104 (OS 이미지 파일)
  · fs160 = 192.168.123.160 (FOTA-DB 등)

인증은 win32wnet(WNetAddConnection2)로 아이디/비밀번호 기반 연결을 만든 뒤 UNC 경로를
os.scandir/open 으로 접근한다. 공유(share) 이름은 파이프라인 경로의 첫 세그먼트(예: 'project')
에서 오므로 자격증명에는 기준경로(base)를 두지 않는다.
백엔드 공통 메서드: test() · list_dir(rel) · read_head(rel,n) · download(rel,local_dir)
                 · exists(rel) · upload(local,rel)

**쓰기는 upload() 하나뿐이다.** 최종 승인된 Release Note 를 제품별 '개발 RN 경로'에 남기는
용도로만 쓰고, 기본값이 '덮어쓰지 않음'이다(사람이 만든 정본을 자동 처리가 지우지 않도록).
그 밖의 모든 접근은 읽기 전용이다.
"""
from __future__ import annotations
from app import shadow as _shadow

import os
from dataclasses import dataclass

from app.atomic_io import atomic_copyfile
from app.database import get_session
from app.models import SftpCredential
from app.security import decrypt_secret

# target 별 고정 호스트/라벨 — 사용자는 아이디/비밀번호만 등록한다.
FS_TARGETS: dict[str, dict] = {
    "fs104": {"host": "192.168.123.104", "label": ".104 (OS 이미지)"},
    "fs160": {"host": "192.168.123.160", "label": ".160 (FOTA-DB)"},
    "fs26": {"host": "192.168.123.26", "label": ".26 (NightlyBuild)"},
}
DEFAULT_TARGET = "fs104"


def clean_unc(path: str) -> str:
    r"""붙여넣은 경로를 다듬는다 — **큰따옴표는 어디에 있든** 걷어내고 앞뒤 공백을 턴다.

    Windows 탐색기의 '경로로 복사'는 경로를 큰따옴표로 감싼다. 그대로 저장되면
      · 첫 조각이 공유 이름으로 오인돼 (67, 'WNetAddConnection2', …) 로 죽고,
      · 앞뒤만 벗기면 뒤에 파일 이름을 이어붙이는 순간 따옴표가 경로 **가운데**로
        들어가 [Errno 22] Invalid argument 로 죽는다
        (실측 2026-09-09 job-15: '…\45.81-approved\sdcard"\45.81_20260719.PFU').

    큰따옴표는 Windows 파일·폴더 이름에 쓸 수 없는 글자라 어디에 있든 지워도 안전하다.
    작은따옴표는 이름에 쓸 수 있으므로 **앞뒤에 있을 때만** 벗긴다.
    """
    s = (path or "").strip()
    for q in '"“”':
        s = s.replace(q, "")
    s = s.strip()
    while s and s[0] in "'‘’":
        s = s[1:].lstrip()
    while s and s[-1] in "'‘’":
        s = s[:-1].rstrip()
    return s


@dataclass
class FileEntry:
    name: str
    is_dir: bool
    size: int


def write_direct(src: str, dst: str, *, overwrite: bool = False) -> None:
    """src 를 dst 에 바로 쓴다(임시 파일 없음). 쓴 뒤 크기가 다르면 OSError.

    overwrite=False 면 배타 생성 — 이미 있으면 FileExistsError 로 손대지 않는다.
    쓰다 실패하면 반쯤 쓴 dst 를 지워 보고, 지우기가 막혀 있으면 그대로 올린다.
    """
    import shutil

    mode = "wb" if overwrite else "xb"
    created = False
    try:
        with open(src, "rb") as fin, open(dst, mode) as fout:
            created = True
            shutil.copyfileobj(fin, fout, 1024 * 1024)
            fout.flush()
            os.fsync(fout.fileno())
        want, got = os.path.getsize(src), os.path.getsize(dst)
        if want != got:
            raise OSError(f"쓴 크기가 다릅니다({got} ≠ {want} bytes)")
    except BaseException:
        if created and not overwrite:
            try:
                os.remove(dst)
            except OSError:
                pass
        raise


def _sorted(entries: list[FileEntry]) -> list[FileEntry]:
    return sorted(entries, key=lambda x: (not x.is_dir, x.name.lower()))


def _wnet_connect(host: str, share: str, username: str, password: str) -> None:
    """win32wnet 으로 \\\\host\\share 에 계정 연결(deviceless). 이미 연결돼 있으면 통과."""
    import win32wnet

    nr = win32wnet.NETRESOURCE()
    nr.dwType = 0  # RESOURCETYPE_ANY (disk/IPC 모두)
    nr.lpRemoteName = f"\\\\{host}\\{share}"
    try:
        win32wnet.WNetAddConnection2(nr, password or None, username or None, 0)
    except Exception as e:  # noqa: BLE001
        we = getattr(e, "winerror", None)
        # 85=ALREADY_ASSIGNED, 1202=DEVICE_ALREADY_REMEMBERED, 1219=CREDENTIAL_CONFLICT
        if we in (85, 1202, 1219):
            return
        raise


class SmbBackend:
    kind = "smb"

    def __init__(self, host: str, username: str = "", password: str = ""):
        self.host = (host or "").strip("\\/")
        self.username = username or ""
        self.password = password or ""
        self.root = f"\\\\{self.host}"
        self.location = self.root
        self._shares: set[str] = set()

    def _rel_parts(self, rel: str) -> list[str]:
        # 따옴표부터 걷어낸다 — 그러지 않으면 첫 조각이 공유 이름으로 오인된다(clean_unc).
        parts = [p.strip() for p in
                 clean_unc(rel).replace("/", "\\").strip("\\").split("\\") if p.strip()]
        # 사용자가 전체 UNC 경로(\\host\share\...)를 붙여넣으면 맨 앞 host 세그먼트를 떼어낸다.
        # (백엔드는 host 기준 상대경로 = 'share\...' 를 기대한다. 첫 세그먼트가 곧 공유 이름.)
        if parts and parts[0].lower() == self.host.lower():
            parts = parts[1:]
        return parts

    def _ensure_share(self, share: str) -> None:
        if not share or share in self._shares:
            return
        _wnet_connect(self.host, share, self.username, self.password)
        self._shares.add(share)

    def _resolve(self, rel: str) -> str:
        parts = self._rel_parts(rel)
        target = os.path.normpath(os.path.join(self.root, *parts)) if parts else self.root
        if not (target == self.root or target.startswith(self.root + os.sep)):
            raise ValueError("허용된 서버 밖으로 접근할 수 없습니다.")
        return target

    def test(self):
        try:
            _wnet_connect(self.host, "IPC$", self.username, self.password)
            return True, f"SMB 인증 성공: \\\\{self.host} ({self.username or '현재 계정'})"
        except Exception as e:  # noqa: BLE001
            return False, f"SMB 인증 실패: {e}"

    def list_dir(self, rel: str = "") -> list[FileEntry]:
        parts = self._rel_parts(rel)
        if parts:
            self._ensure_share(parts[0])
        out: list[FileEntry] = []
        with os.scandir(self._resolve(rel)) as it:
            for e in it:
                try:
                    is_dir = e.is_dir()
                    size = 0 if is_dir else e.stat().st_size
                except OSError:
                    is_dir, size = False, 0
                out.append(FileEntry(e.name, is_dir, size))
        return _sorted(out)

    def read_head(self, rel: str, n_bytes: int = 2048) -> int:
        parts = self._rel_parts(rel)
        if parts:
            self._ensure_share(parts[0])
        with open(self._resolve(rel), "rb") as f:
            return len(f.read(n_bytes))

    def open_read(self, rel: str):
        """원격 파일을 **읽기 전용으로 연다**(내려받지 않는다).

        SMB 는 임의 위치 읽기가 되므로, 큰 zip 이라도 목록과 필요한 항목 하나만 읽으면
        된다. 실측: 3.3GB target_files zip 의 항목 7,667개를 0.2초에 읽고 그 안의
        build.prop(8.7KB)만 꺼냈다 — 통째로 받으면 수 분이 걸린다.
        호출한 쪽이 close() 해야 한다(with 문 권장).
        """
        parts = self._rel_parts(rel)
        if parts:
            self._ensure_share(parts[0])
        return open(self._resolve(rel), "rb")

    def download(self, rel: str, local_dir: str, *, on_progress=None) -> tuple[str, int]:
        """rel 을 local_dir 로 받는다. (저장경로, 바이트수).

        on_progress(done, total) 를 주면 복사 중 1MB 마다 부른다 — 수 GB 이미지의
        남은 양을 화면에 보여 주는 용도다(주지 않으면 예전과 똑같이 동작한다).
        """
        parts = self._rel_parts(rel)
        if parts:
            self._ensure_share(parts[0])
        src = self._resolve(rel)
        os.makedirs(local_dir, exist_ok=True)
        dst = os.path.join(local_dir, os.path.basename(src))
        atomic_copyfile(src, dst, on_progress=on_progress)
        return dst, os.path.getsize(dst)

    def exists(self, rel: str) -> bool:
        parts = self._rel_parts(rel)
        if parts:
            self._ensure_share(parts[0])
        try:
            return os.path.exists(self._resolve(rel))
        except OSError:
            return False

    def upload(self, local_path: str, rel: str, *, overwrite: bool = False) -> tuple[str, str]:
        """로컬 파일을 파일서버 rel(폴더+파일명) 로 올린다. (written_path, error).

        **파일서버에 쓰는 유일한 경로다.** 기본은 덮어쓰지 않는다 — 같은 이름이 있으면
        손대지 않고 사유를 돌려준다(사람이 만든 정본을 자동 처리가 지우면 되돌릴 수 없다).
        저장은 **최종 이름에 바로 쓴다**(임시 파일을 만들지 않는다). 예전에는 임시 파일에 쓴 뒤
        os.replace 로 옮겼는데, 우리 계정은 공유에 이름 바꾸기·삭제 권한이 없어 옮기기가 튕기고
        덮어쓰기로 물러선 뒤 임시 파일을 지우지도 못해 '.이름.<uuid>.tmp' 가 남았다(실측
        2026-09-08, 2026-10-13 .160 RN for Sales). 덮어쓰지 않는 쓰기는 배타 생성('xb')이라
        같은 이름이 생기는 경합에도 기존 파일을 건드리지 않는다. 쓴 뒤 크기를 되읽어 확인한다.
        """
        if _shadow.blocked("fileserver", "upload", local_path=local_path, rel=rel, overwrite=overwrite):
            return self._resolve(rel) if self._rel_parts(rel) else "", ""
        parts = self._rel_parts(rel)
        if not parts:
            return "", "저장 경로가 비어 있습니다."
        self._ensure_share(parts[0])
        dst = self._resolve(rel)
        if not overwrite and os.path.exists(dst):
            return "", "같은 이름의 파일이 이미 있습니다."
        try:
            write_direct(local_path, dst, overwrite=overwrite)
        except Exception as e:  # noqa: BLE001
            return "", f"{type(e).__name__}: {e}"
        return dst, ""


def make_smb_backend(target: str, username: str, password: str) -> SmbBackend:
    """설정 테스트용 — target 고정 호스트로 SMB 백엔드를 만든다."""
    host = FS_TARGETS.get(target, {}).get("host", "")
    return SmbBackend(host, username, password)


def get_backend(user_id: int, target: str = DEFAULT_TARGET):
    """해당 서버(target)의 SMB 백엔드 반환. 자격증명 없으면 None.

    파일서버 계정도 공용이라 **admin 계정** 자격증명을 쓴다(개인 메일 발송만 예외).
    """
    from app.integrations.creds import credential_user_id

    user_id = credential_user_id()
    with get_session() as db:
        c = db.query(SftpCredential).filter_by(user_id=user_id, target=target).first()
        if c is None:
            return None
        host = c.host or FS_TARGETS.get(target, {}).get("host", "")
        if not host:
            return None
        pw = decrypt_secret(c.encrypted_password) if c.encrypted_password else ""
        return SmbBackend(host, c.username, pw)
