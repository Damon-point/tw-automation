"""단계 ③ Dropbox 업로드 · Release Note — 결정적 실행(테스트 경로).

흐름:
  1) ① OS Download 산출물(OS Image)을 가져온다(공유 상태).
  2) Dropbox `OSImage/[제품]/[NEW(_)FOTA]/` 의 숫자 폴더(1~N) 각각에 대해
     **업로드만** 한다(기존 파일 삭제 X). 업로드 전 '기존(구버전) 파일'을 스냅샷해 두고,
     **⑦ Newsletter 발송이 끝난 뒤에** ⑧ 화면에서 사람이 경로별로 지운다(delete_group).
     ※ 테스트 규약: [제품] = automation-test 로 고정, 숫자 폴더는 `/OSImage/automation-test/[N]`.
     ※ 삭제(파괴적) 작업은 오직 automation-test 하위 + 게이트 통과 시에만 허용(가드).
  3) Release Note PDF(② 산출물)를 `.../Release Notes/` 에 업로드(overwrite).

프로덕션(실제 제품 폴더) 미접촉. 링크는 공유 상태에 저장해 이후 단계가 쓴다.
"""
from __future__ import annotations

import os
import re

from app import progress
from app.agent.runner import StageOutput
from app.integrations import dropbox_client as D
from app.integrations.creds import get_dropbox_auth
from app.pipeline.state import get_state, update_state

# ── 업로드 경로 ────────────────────────────────────────────────────────────
# production 형태를 템플릿으로 두고, 지금은 {product} 자리에 automation-test 를 넣어 검증한다.
# 실제 제품 폴더로 내보낼 때는 _PROD_MODE=True 로만 바꾸면 된다(경로 구조는 동일).
_PROD_MODE = False                     # ⚠ True 로 바꾸면 실제 제품 폴더로 업로드된다
_TEST_PRODUCT = "automation-test"

#  ① FOTA 배포용 — 개별 파일 4개(Full/Diff 의 .PFU + .md5). zip 아님.
#     OSImage/{제품}/NEW_FOTA/{숫자 폴더}
_FOTA_TPL = "/OSImage/{product}"
#  ② 지원 배포용 — PFU+md5+Checksum.exe 를 묶은 sdcard zip 2개(Full/Diff)
_SDCARD_ZIP_TPL = "/Support Team/SupportPotal/OSImage, FW/{product}"
#  ③ 플래싱 툴 zip 1개
_FLASH_TPL = "/Support Team/Support OS/Flash Package (Fastboot, etc.)/{product}"
#  ④ EmKit SDK zip 1개 — 같은 파일이 이미 있으면 올리지 않는다.
#     production 은 'Android_UnifiedSDK', 테스트는 'automation-test' 폴더를 쓴다.
#     ※ 루트에도 /SupportPotal 이 있지만 SDK 는 'Support Team' 아래에만 존재한다(실측 확인).
_SDK_BASE = "/Support Team/SupportPotal/SDK"
_SDK_PROD_FOLDER = "Android_UnifiedSDK"

# NEW_FOTA 표기 흔들림 대응 — 'NEW_FOTA' · 'NEW FOTA' · 'NEWFOTA' · 'New-Fota' 등을 모두 잡는다.
_NEWFOTA_RE = re.compile(r"^\s*new[\s_\-]*fota\s*$", re.IGNORECASE)

# 테스트 규약: 파괴적 작업(구버전 삭제)은 이 경로 하위로만 허용


def _fmt_mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f}MB" if n < 1024 ** 3 else f"{n / (1024 ** 3):.2f}GB"


def _mark(r) -> str:
    """업로드 결과 표시 — 재실행 때 이미 있어 건너뛴 건 '생략'으로 구분한다."""
    return "생략(이미 있음)" if getattr(r, "skipped", False) else "OK"


def _dest(tpl: str, product: str) -> str:
    """경로 템플릿 → 실제 경로. 테스트 모드면 {product} 자리에 automation-test 를 넣는다."""
    return tpl.format(product=product if _PROD_MODE else _TEST_PRODUCT)


def _sdk_dest() -> str:
    """EmKit SDK 업로드 폴더. production 은 Android_UnifiedSDK, 테스트는 automation-test."""
    return f"{_SDK_BASE}/{_SDK_PROD_FOLDER if _PROD_MODE else _TEST_PRODUCT}"


def _find_newfota(token, product_base: str) -> str:
    """제품 폴더 아래 'NEW_FOTA' 계층을 표기 변형까지 감안해 찾는다. 없으면 "".

    'NEW_FOTA' · 'NEW FOTA' · 'NEWFOTA' 처럼 폴더명이 제각각이라 정규식으로 맞춘다.
    """
    try:
        for it in D.list_folder(token, product_base):
            if it.is_dir and _NEWFOTA_RE.match(it.path.rsplit("/", 1)[-1]):
                return it.path
    except Exception:  # noqa: BLE001
        pass
    return ""


def release_date(st: dict):
    """이번 릴리스의 공식 날짜 = FIH Approval 승인일. 없으면 PFU 빌드일 → 오늘 순으로 폴백."""
    from datetime import date, datetime
    from email.utils import parsedate_to_datetime

    raw = (st.get("approval_date") or "").strip()
    if raw:
        try:
            return parsedate_to_datetime(raw).date()
        except Exception:  # noqa: BLE001
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d"):
                try:
                    return datetime.strptime(raw[:len(fmt) + 2].strip(), fmt).date()
                except ValueError:
                    continue
    bd = (st.get("os_build_date") or "").strip()      # 파일명에서 뽑은 빌드일 YYYYMMDD
    if len(bd) == 8 and bd.isdigit():
        try:
            return date(int(bd[:4]), int(bd[4:6]), int(bd[6:]))
        except ValueError:
            pass
    return date.today()


def _bundle_zip(items: list[tuple[str, str]], zip_path: str) -> tuple[str, str]:
    """(로컬경로, 저장이름) 목록을 zip 하나로 묶는다. (zip 경로, error).

    sdcard 업로드분은 PFU·md5·Checksum.exe 를 묶은 zip 으로 올린다.
    """
    import zipfile

    from app.atomic_io import atomic_write_bytes

    try:
        import io as _io

        buf = _io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for src, arcname in items:
                if src and os.path.exists(src):
                    zf.write(src, arcname)
        atomic_write_bytes(zip_path, buf.getvalue())
        return zip_path, ""
    except Exception as e:  # noqa: BLE001
        return "", str(e)


# 기존(구버전) 파일 실제 삭제 게이트 — ⑦ Newsletter 발송 완료 후에만 True.
_ALLOW_DROPBOX_DELETE = False


def deletable(path: str, base: str) -> tuple[bool, str]:
    """삭제해도 되는 경로인가. (허용여부, 거부사유).

    지금 규칙은 하나다 — **제품에 등록된 그 Dropbox 경로 하위여야 한다.** 삭제는
    ⑧ 화면에서 경로별 버튼으로만 일어나고, 목록은 등록 경로를 훑어 만든다. 그래도
    마지막에 한 번 더 본다(목록을 만든 곳과 지우는 곳이 떨어져 있으므로).

    예전에는 `{FOTA base}/{숫자}/파일` 만 허용했다. 그 규칙은 4개 경로 각각에 삭제
    버튼을 두기로 하면서 대체됐다(사용자 확정 2026-08-06) — sdcard zip·flash·
    Release Note 는 숫자 폴더 없이 경로 바로 아래에 있다.
    """
    b = (base or "").rstrip("/")
    if not b:
        return False, "등록 경로 없음"
    if not path.startswith(b + "/"):
        return False, "등록 경로 밖"
    if ".." in path.split("/"):
        return False, "상위 경로 참조"
    return True, ""


def run_dropbox_release(
    user_id: int, subject: str, input_params: str = "", mail_id: int | None = None
) -> StageOutput:
    lines: list[str] = []

    token = get_dropbox_auth(user_id)
    if not token:
        return StageOutput(result_text="✘ Dropbox 자격증명이 없습니다. [설정 → Dropbox]에서 등록하세요.")

    # 1) ① 산출물: Full/Diff PFU (+각 md5). 하위호환: 없으면 단일 os_image 를 FULL 로.
    st = get_state(mail_id) if mail_id is not None else {}
    fota_source = list(st.get("fota_source") or [])
    if not fota_source:
        os_image = st.get("os_image_path", "")
        if os_image and os.path.exists(os_image):
            fota_source = [{"type": "FULL", "name": os.path.basename(os_image),
                            "path": os_image, "size": os.path.getsize(os_image),
                            "md5_name": "", "md5_path": ""}]
    have = [s for s in fota_source if s.get("path") and os.path.exists(s["path"])]
    # 공식 판에서 출발하는 DIFF 만 올린다 — 이미 받아 둔 작업(OS Download 를 고치기 전)에도 듣게.
    from app.pipeline.os_download import official_diffs

    _keep, _drop_diffs = official_diffs([s["name"] for s in have if s.get("type") == "DIFF"])
    if _drop_diffs:
        have = [s for s in have if s.get("type") != "DIFF" or s["name"] in _keep]
    if not have:
        return StageOutput(
            result_text="✘ 업로드할 OS 파일(fota_source)이 없습니다. 먼저 'OS Download' 를 완료하세요."
        )
    product = st.get("product") or "(미상)"
    version = st.get("os_version") or "(미상)"
    lines.append("· 업로드 대상: " + ", ".join(f"{s['type']} {s['name']}" for s in have))

    # 제품에 등록한 Dropbox 경로가 있으면 그것을 쓴다. 폴더 이름 규칙이 제품마다 달라
    # (실측: PM95 A13 '/OSImage/PM95/NEW_FOTA', A15 '/OSImage/PM95_A15/NEWFOTA',
    #  PM45 '/OSImage/PM45_P/NEWFOTA') 제품명으로 경로를 조립하는 방식으로는 못 맞춘다.
    from app import os_products as _OP

    _reg = _OP.paths_for(product, st.get("android", ""))
    if _reg:
        lines.append(f"· 제품 등록 경로 사용: {_reg['sku']}")

    # 2) FOTA 배포 경로 = NEW_FOTA 아래 숫자 폴더 → **개별 파일 4개**(zip 아님)
    product_base = _dest(_FOTA_TPL, product)
    if _reg.get("dbx_fota"):
        base = _reg["dbx_fota"]
    else:
        newfota = _find_newfota(token, product_base)
        base = newfota or product_base   # NEW_FOTA 계층이 없으면 제품 폴더 바로 아래 숫자 폴더
        if not newfota:
            lines.append(f"  ⚠ {product_base} 아래 NEW_FOTA 폴더를 못 찾아 제품 폴더 바로 아래를 씁니다.")
    lines.append(
        f"· FOTA 대상: {base} 의 숫자 폴더"
        + ("" if _PROD_MODE else "  (테스트 모드 — 실제 제품 폴더 미접촉)")
    )
    try:
        entries = D.list_folder(token, base)
    except Exception as e:  # noqa: BLE001
        return StageOutput(result_text="\n".join(lines) + f"\n✘ 대상 폴더 조회 실패: {e}")

    num_folders = sorted(
        (e for e in entries if e.is_dir and e.name.strip().isdigit()),
        key=lambda e: int(e.name),
    )
    if not num_folders:
        return StageOutput(
            result_text="\n".join(lines) + f"\n✘ {base} 안에 숫자로 된 폴더(1~10)가 없습니다."
        )
    lines.append(f"· 숫자 폴더 {len(num_folders)}개 발견: {', '.join(e.name for e in num_folders)}")

    old_files: list[str] = []   # 업로드 전 기존(구버전) 파일 = ⑦ 후 삭제 대상 후보
    new_files: list[str] = []   # 이번에 올린 파일(삭제 대상에서 제외)
    fota_downloads: list[dict] = []   # ④ DownloadPath 행의 원천

    folder_names = [e.name for e in num_folders]   # 숫자 오름차순
    for fn in folder_names:
        try:
            existing = [it.path for it in D.list_folder(token, f"{base}/{fn}") if not it.is_dir]
        except Exception:  # noqa: BLE001
            existing = []
        old_files.extend(existing)

    # 같은 Full/Diff(+md5)를 폴더마다 넣되, 재업로드를 피하려고
    # '첫 폴더에 한 번 업로드 → 나머지 폴더엔 Dropbox 서버 내 복사(copy)'로 처리한다(대용량에 유리).
    dl_map: dict[tuple, dict] = {}
    for s_ in have:
        base_name = os.path.basename(s_["path"])
        dests = [f"{base}/{fn}/{base_name}" for fn in folder_names]
        progress.step(f"Dropbox 업로드+복사 중… {s_['type']} {s_['name']} → {len(folder_names)}개 폴더")
        results = D.upload_and_copy(token, s_["path"], dests)
        md5_results = None
        if s_.get("md5_path") and os.path.exists(s_["md5_path"]):
            md5_name = os.path.basename(s_["md5_path"])
            md5_dests = [f"{base}/{fn}/{md5_name}" for fn in folder_names]
            md5_results = D.upload_and_copy(token, s_["md5_path"], md5_dests)
        for i, fn in enumerate(folder_names):
            r = results[i]
            md5_ok = bool(md5_results and md5_results[i].ok)
            if r.ok:
                new_files.append(r.dropbox_path)
                if md5_ok:
                    new_files.append(md5_results[i].dropbox_path)
                # 파일 이름까지 열쇠로 — DIFF 가 둘이면 (폴더, 'DIFF') 하나에 겹쳐 두 번째가
                # 첫 번째를 덮어썼다(실측 2026-10-13 job-34: 07C1 DIFF 가 빠지고 07 이 두 번).
                dl_map[(fn, s_["type"], s_["name"])] = {
                    "type": s_["type"], "url": r.link,
                    "md5_url": md5_results[i].link if md5_ok else "",
                    "filename": s_["name"], "size": s_["size"],
                }
                lines.append(
                    f"  [{fn}] {s_['type']} {s_['name']} {_mark(r)}"
                    + ("" if md5_ok or not s_.get("md5_path") else " (md5 실패)")
                )
            else:
                lines.append(f"  [{fn}] {s_['type']} 실패: {r.error}")

    # DownloadPath 원천을 폴더 번호 순 → Full/Diff(have) 순으로 병합(결정적).
    for fn in folder_names:
        for s in have:
            d = dl_map.get((fn, s["type"], s["name"]))
            if d:
                fota_downloads.append(d)

    # 삭제 대상 = 기존 파일 중 이번에 올린 새 파일과 겹치지 않는 것(구버전만)
    new_lower = {p.lower() for p in new_files}
    cleanup_targets = [p for p in old_files if p.lower() not in new_lower]
    lines.append("")
    lines.append(
        f"✔ DownloadPath 원천 {len(fota_downloads)}건 생성 "
        f"(폴더 {len(num_folders)} × {len(have)}종 Full/Diff)"
    )
    lines.append(
        f"· 기존(구버전) 파일 {len(cleanup_targets)}개 보존 — 삭제는 Newsletter 발송 후"
    )

    # 3) Release Note for Sales 는 **이 단계에서 올리지 않는다**(2026-09-22).
    #    그 내려받기 링크가 곧 Newsletter 본문의 Release Note URL 인데, Newsletter
    #    제작은 이 단계가 없는 Submission 갈래에서 돈다. 그래서 링크가 생길 길이 없어
    #    Newsletter 가 계속 멈췄다. 업로드는 app/pipeline/rn_dropbox.ensure_rn_link 로
    #    옮겼다 — 링크가 필요한 단계가 필요할 때 부르고, 이미 올렸으면 그 링크를 준다.
    #    여기서는 이미 올라가 있으면 이름만 받아 ⑧ 정리 목록에 넣어 둔다.
    rn_link = str(st.get("rn_link") or "")
    rn_name = str(st.get("rn_name") or "")
    if rn_link:
        lines.append(f"· Release Note 는 이미 올라가 있습니다: {rn_name}")
        lines.append(f"    링크: {rn_link}")
    else:
        lines.append("· Release Note 는 Newsletter 제작 단계에서 올립니다.")

    # 3-1) sdcard zip → Support Team/SupportPotal/OSImage, FW/{제품}
    #      Full/Diff 각각 .PFU + .md5 + Checksum.exe 를 하나로 묶어 총 2개를 올린다.
    sdcard_base = _reg.get("dbx_sdcard_zip") or _dest(_SDCARD_ZIP_TPL, product)
    chk = st.get("checksum_tool") or {}
    chk_path = chk.get("path") if chk.get("path") and os.path.exists(chk["path"]) else ""
    if not chk_path:
        lines.append("  ⚠ Checksum.exe 가 없어 sdcard zip 에 포함하지 못합니다(OS Download 재실행 필요).")
    sdcard_uploads: list[dict] = []
    rdate = release_date(st)
    prev_v = st.get("prev_version") or ""
    for s_ in have:
        # 파일명은 포털 관행을 따른다(95.06_20260127.zip / 95.06_04_20260127.zip).
        # 버전을 모르면 관행 이름을 지어내지 말고 PFU 파일명을 그대로 쓴다.
        # zip 이름은 **PFU 파일 이름 그대로** 쓴다 — OS 빌드가 붙인 이름이 정본이다.
        #   TR54G15.04_20260822.PFU      → TR54G15.04_20260822.zip
        #   TR54G15.04_03C1_20260822.PFU → TR54G15.04_03C1_20260822.zip
        # 관행으로 지어내던 '{짧은버전}_{오늘}' 은 제품 접두사와 리비전을 잃고 날짜도
        # 빌드일이 아니라 올린 날이었다(실측 2026-09-09 TR54: '15.04_20260828.zip').
        # PM95·PM45 처럼 버전이 곧 파일 이름인 제품은 결과가 예전과 같다
        # (95.06_20260127.PFU → 95.06_20260127.zip).
        zname = os.path.splitext(os.path.basename(s_["path"]))[0] + ".zip"
        zpath = os.path.join(os.path.dirname(s_["path"]), zname)
        items = [(s_["path"], os.path.basename(s_["path"]))]
        if s_.get("md5_path") and os.path.exists(s_["md5_path"]):
            items.append((s_["md5_path"], os.path.basename(s_["md5_path"])))
        if chk_path:
            items.append((chk_path, os.path.basename(chk_path)))
        progress.step(f"sdcard zip 생성 중… {s_['type']} ({len(items)}개 파일)")
        zp, zerr = _bundle_zip(items, zpath)
        if not zp:
            lines.append(f"✘ sdcard zip 생성 실패({s_['type']}): {zerr}")
            continue
        progress.step(f"sdcard zip 업로드 중… {s_['type']} {_fmt_mb(os.path.getsize(zp))}")
        try:
            zres = D.upload_and_copy(token, zp, [f"{sdcard_base}/{zname}"])[0]
            if zres.ok:
                sdcard_uploads.append({
                    "type": s_["type"], "name": zname,
                    "size": os.path.getsize(zp), "url": zres.link,
                })
                lines.append(f"✔ sdcard zip {_mark(zres)}({s_['type']}): {sdcard_base}/{zname}")
                lines.append(f"    링크: {zres.link}")
            else:
                lines.append(f"✘ sdcard zip 업로드 실패({s_['type']}): {zres.error}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"✘ sdcard zip 업로드 오류({s_['type']}): {e}")
        finally:
            try:
                os.remove(zp)          # 업로드용 임시 zip 정리
            except OSError:
                pass

    # 3-2) 플래싱 툴(flash zip) → Flash Package 폴더('Support OS/Flash Package …')에 업로드.
    #      RN 은 여기(플래싱 폴더)에 올리지 않는다 — Release Note 정본은 위 Release Notes 한 곳뿐.
    #      zip 다운로드 링크·크기는 ⑤ SP download-detail/4C 편집에 사용된다.
    flash_base = _reg.get("dbx_flash") or _dest(_FLASH_TPL, product)
    flash_upload = None
    flash_zip = st.get("flash_zip") or None
    if flash_zip and flash_zip.get("path") and os.path.exists(flash_zip["path"]):
        progress.step("플래싱 툴(zip) 업로드 중…")
        try:
            fdest = f"{flash_base}/{os.path.basename(flash_zip['path'])}"
            fres = D.upload_and_copy(token, flash_zip["path"], [fdest])[0]
            if fres.ok:
                flash_upload = {"name": flash_zip["name"], "size": flash_zip["size"], "url": fres.link}
                lines.append(f"✔ flash zip {_mark(fres)}: {flash_base}/{flash_zip['name']}")
                lines.append(f"    링크: {fres.link}")
            else:
                lines.append(f"✘ flash zip 업로드 실패: {fres.error}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"✘ flash zip 업로드 오류: {e}")
    elif flash_zip:
        lines.append("  ⚠ flash zip 로컬 파일 없음 — OS Download 재실행 필요")

    # 3-3) EmKit SDK zip → SupportPotal/SDK/Android_UnifiedSDK
    #      SDK 는 제품별이 아니라 공용이라, 같은 이름의 zip 이 이미 있으면 올리지 않는다.
    sdk_base = _sdk_dest()
    sdk_zip = st.get("sdk_zip") or None
    sdk_upload = None
    if sdk_zip and sdk_zip.get("path") and os.path.exists(sdk_zip["path"]):
        zname = os.path.basename(sdk_zip["path"])
        existing = None
        try:
            existing = next(
                (it for it in D.list_folder(token, sdk_base)
                 if not it.is_dir and it.path.rsplit("/", 1)[-1].lower() == zname.lower()),
                None,
            )
        except Exception:  # noqa: BLE001
            pass                      # 조회 실패 시엔 그냥 올린다(덮어쓰기)
        if existing is not None:
            try:
                link = D.shared_link_for(token, existing.path)
            except Exception:  # noqa: BLE001
                link = ""
            sdk_upload = {"name": zname, "size": sdk_zip.get("size", 0),
                          "url": link, "version": sdk_zip.get("version", ""),
                          "skipped": True}
            lines.append(f"· EmKit SDK 이미 최신({zname}) — 업로드 생략")
            if link:
                lines.append(f"    링크: {link}")
            sdk_zip = None            # 아래 업로드 블록 건너뛰기
    if sdk_zip and sdk_zip.get("path") and os.path.exists(sdk_zip["path"]):
        progress.step("EmKit SDK zip 업로드 중…")
        try:
            sdest = f"{sdk_base}/{os.path.basename(sdk_zip['path'])}"
            sres = D.upload_and_copy(token, sdk_zip["path"], [sdest])[0]
            if sres.ok:
                sdk_upload = {"name": sdk_zip["name"], "size": sdk_zip["size"],
                              "url": sres.link, "version": sdk_zip.get("version", "")}
                lines.append(f"✔ EmKit SDK {_mark(sres)}: {sdk_base}/{sdk_zip['name']}")
                lines.append(f"    링크: {sres.link}")
            else:
                lines.append(f"✘ EmKit SDK 업로드 실패: {sres.error}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"✘ EmKit SDK 업로드 오류: {e}")
    elif sdk_upload is None:                     # 생략(이미 최신)이 아닌 진짜 누락만 경고
        if st.get("sdk_zip"):
            lines.append("  ⚠ SDK zip 로컬 파일 없음 — OS Download 재실행 필요")
        else:
            lines.append("  ⚠ SDK zip 정보가 없습니다 — OS Download 를 먼저 실행하세요.")

    # 공유 상태에 저장: ④ DownloadPath 원천(fota_downloads) + ⑦ 후 정리용 스냅샷
    if mail_id is not None:
        update_state(
            mail_id, fota_base=base, fota_downloads=fota_downloads,
            rn_link=rn_link, rn_name=rn_name,
            dropbox_cleanup={"base": base, "targets": cleanup_targets,
                             "registered": bool(_reg.get("dbx_fota"))},
            # ⑧ 정리 화면이 쓰는 것 — **이번 회차에 올린 파일 이름**. 정리할 때 각 폴더를
            # 다시 훑어 이 목록에 없는 것을 구버전으로 본다(스냅샷보다 현재 상태가 맞다).
            dropbox_new={
                "fota": new_files,
                "sdcard_zip": [u["name"] for u in sdcard_uploads],
                "flash": [flash_upload["name"]] if flash_upload else [],
                "rn": [rn_name] if rn_name else [],
            },
            sdcard_uploads=sdcard_uploads,   # Full/Diff sdcard zip 링크(⑤ SP 게시용)
            flash_upload=flash_upload,   # ⑤ SP download-detail/4C 편집용(url·size·name)
            sdk_upload=sdk_upload,       # EmKit SDK zip 업로드 결과(url·size·version)
        )

    summary = (
        f"[Dropbox 업로드 · 테스트 경로]\n"
        f"제품 {product} · 버전 {version} → {base} 숫자 폴더 {len(num_folders)}개에 "
        f"Full/Diff 업로드 · DownloadPath 원천 {len(fota_downloads)}건"
        + (f" · sdcard zip {len(sdcard_uploads)}개" if sdcard_uploads else "")
        + (f" · RN {rn_name}" if rn_link else "")
        + (" · flash zip→SupportPotal" if flash_upload else "")
        + (" · SDK " + ("생략(최신)" if (sdk_upload or {}).get("skipped") else "업로드")
           if sdk_upload else "")
        + "\n\n"
        + "\n".join(lines)
    )
    return StageOutput(result_text=summary)


# ⑧ 정리 화면이 다루는 Dropbox 경로 4종 — 제품 관리에 등록하는 그 네 칸이다.
#   depth=2 는 `{base}/{숫자}/{파일}`(NEW_FOTA), depth=1 은 `{base}/{파일}`.
CLEANUP_GROUPS = [
    ("fota", "OS Image(sdcard) · NEW_FOTA", "dbx_fota", 2),
    ("sdcard_zip", "OS Image(sdcard zip)", "dbx_sdcard_zip", 1),
    ("flash", "OS Image(flash)", "dbx_flash", 1),
    ("rn", "Release Note", "dbx_rn", 1),
]


def _list_group(token, base: str, depth: int) -> list[str]:
    """그 경로 아래 파일 전체 경로. depth=2 면 숫자 폴더 안까지 본다."""
    out: list[str] = []
    try:
        entries = D.list_folder(token, base)
    except Exception:  # noqa: BLE001
        return out
    if depth == 1:
        return [e.path for e in entries if not e.is_dir]
    for e in entries:
        if not (e.is_dir and e.name.strip().isdigit()):
            continue
        try:
            out.extend(it.path for it in D.list_folder(token, e.path) if not it.is_dir)
        except Exception:  # noqa: BLE001
            continue
    return out


def cleanup_groups(user_id: int, mail_id: int | None) -> tuple[list[dict], str]:
    """정리 화면용 — **등록된 Dropbox 경로 4개**를 각각 훑어 구버전 파일을 묶는다.

    구버전 = 그 폴더에 있는데 **이번 회차에 올린 것이 아닌** 파일.
    실제 삭제는 사람이 그룹별 버튼으로 한다(자동 삭제 없음).

    ③ Dropbox 업로드가 아직 안 돌았으면 '이번에 올린 것'을 모른다 — 그때는 폴더의 모든
    파일이 구버전처럼 보이므로 **이번 회차 파일까지 지울 수 있다**. 그래서 그 경우
    unsafe 를 세워 화면이 삭제를 막게 한다.
    반환 ([{key, label, base, files[], skipped, unsafe}], error)
    """
    st = get_state(mail_id) if mail_id is not None else {}
    from app import os_products as _OP

    _reg = _OP.paths_for(st.get("product", ""), st.get("android", ""))
    token = get_dropbox_auth(user_id)
    if not token:
        return [], "Dropbox 자격증명이 없습니다."
    new = st.get("dropbox_new") or {}
    unsafe = not any(new.get(k) for k, _l, _r, _d in CLEANUP_GROUPS)

    out = []
    for key, label, reg_key, depth in CLEANUP_GROUPS:
        base = (_reg.get(reg_key) or "").rstrip("/")
        g = {"key": key, "label": label, "base": base, "files": [],
             "skipped": "", "unsafe": unsafe}
        if not base:
            g["skipped"] = "제품 관리에 경로가 등록되지 않았습니다."
            out.append(g)
            continue
        mine = {str(x).lower() for x in (new.get(key) or [])}
        mine |= {os.path.basename(str(x)).lower() for x in (new.get(key) or [])}
        for p in _list_group(token, base, depth):
            if p.lower() in mine or os.path.basename(p).lower() in mine:
                continue                     # 이번에 올린 것
            g["files"].append(p)
        g["files"].sort()
        out.append(g)
    return out, ""


def delete_group(user_id: int, mail_id: int | None, key: str) -> str:
    """그룹 하나의 구버전 파일을 실제로 삭제한다 — 화면의 명시 버튼에서만 호출한다."""
    groups, err = cleanup_groups(user_id, mail_id)
    if err:
        return "✘ " + err
    g = next((x for x in groups if x["key"] == key), None)
    if g is None or not g["files"]:
        return "삭제할 파일이 없습니다."
    if g.get("unsafe"):
        return ("✘ Dropbox 업로드(③) 기록이 없어 무엇이 구버전인지 가릴 수 없습니다 — "
                "이번 회차 파일까지 지울 수 있어 중단합니다.")
    # 마지막 가드 — 등록 경로 하위가 아닌 것은 지우지 않는다.
    allowed, blocked = [], []
    for p in g["files"]:
        ok, why = deletable(p, g["base"])
        (allowed if ok else blocked).append(p if ok else f"{p} ({why})")
    if blocked:
        return ("✘ 등록 경로 밖의 파일이 섞여 있어 중단합니다 — "
                f"{len(blocked)}개\n✘ " + "\n✘ ".join(blocked[:5]))

    token = get_dropbox_auth(user_id)
    deleted, errs = 0, []
    for p in allowed:
        ok, e = D.delete_path(token, p)
        if ok:
            deleted += 1
        else:
            errs.append(f"{os.path.basename(p)}: {e}")
    return (f"✔ [{g['label']}] {deleted}/{len(allowed)}개 삭제"
            + (("\n✘ " + "\n✘ ".join(errs[:5])) if errs else ""))
