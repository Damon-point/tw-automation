"""⑤ Service Portal 편집 · ⑥ OS/FW Release Notes 편집 — 메일 구동. **읽기 전용 + 로컬 검수.**

흐름:
  1) 현재 작업 제품 + Android 버전 식별(입력/파이프라인 상태/제목).
  2) 대상 문서를 자동 수집(Android 버전으로 정확히 특정 — 동명 제품 A13/A15 혼동 방지):
       - service_portal: Download 목록에서 제목에 '{제품} A{안드}' 포함 문서(들)
       - release_notes : Confluence 제품+Android Release Note 페이지
  3) 각 문서를 **읽기만** 하고, Claude 가 새 릴리스에 맞춰 바꿀 것을 판단해 적용(로컬).
  4) 검수 작업공간에 담아 /portaledit 에서 사람이 문서별 로컬 에디터로 검수.

targets 로 대상 분리: Service Portal 편집=("service_portal",), Knowledge Base 편집=("release_notes",).
프로덕션(Service Portal DB / Confluence)에는 어떤 것도 쓰지 않는다.
"""
from __future__ import annotations

import re

from app import progress
from app.agent.runner import StageOutput
from app.integrations import confluence_reader as RN
from app.integrations import service_portal as SP
from app.pipeline import review_workspace as WS
from app.pipeline.ai_edit import propose_edit, release_data
from app.pipeline.state import get_state

_MAX_SP_DOCS = 8


def _detect_product(subject: str, input_params: str, state: dict) -> str:
    p = (input_params or "").strip()
    if p and not p.isdigit():
        return p
    if state.get("product"):
        return state["product"]
    m = re.search(r"\b(?:PM|TR|MF|RF|PR)\s*\d{2,3}[A-Za-z]?\b", subject or "", re.I)
    return m.group(0).replace(" ", "").upper() if m else ""


def _detect_android(subject: str, state: dict) -> str:
    if state.get("android"):
        return str(state["android"])
    m = re.search(r"Android\s*(\d{1,2})", subject or "", re.I)
    if m:
        return m.group(1)
    m = re.search(r"\bA(\d{1,2})\b", subject or "")
    return m.group(1) if m else ""


def _title_matches(title: str, product: str, android: str) -> bool:
    t = (title or "").lower()
    if product.lower() not in t:
        return False
    if android:
        # 'A13' / 'Android 13' 형태로 Android 일치 요구(A13 vs A15 구분)
        return bool(re.search(rf"a\s*{android}\b|android\s*{android}\b", t))
    return True


def _apply_resource_links(html: str, st: dict, version: str) -> tuple[str, list, list, str]:
    """Resources 문서에 ③ Dropbox 산출물 링크를 규칙으로 반영.

    sdcard Full/Diff zip · Release Note · SDK + 상단 현재버전 마커.
    (flash zip 은 같은 ⑤ 안의 4C 문서에서 따로 처리한다.)
    """
    from app.pipeline.dropbox_release import release_date

    by_type = {(u.get("type") or "").lower(): u for u in (st.get("sdcard_uploads") or [])}
    sdk = st.get("sdk_upload") or {}
    vfull = st.get("version_full") or version or ""
    return SP.edit_resources_links(
        html,
        version_full=vfull,
        release_date=SP.fmt_portal_date(release_date(st)),
        full_url=(by_type.get("full") or {}).get("url", ""),
        diff_url=(by_type.get("diff") or {}).get("url", ""),
        rn_url=st.get("rn_link", ""),
        sdk_url=sdk.get("url", ""),
        sdk_version=sdk.get("version", ""),
    )


def rework_portal(
    user_id: int, mail_id: int | None, revision: str,
    targets: tuple[str, ...] = ("service_portal",),
) -> StageOutput:
    """⑤/⑥ 재작업 — **검수 작업공간의 편집본에 지시만 반영**(Claude). 원본(SP/Confluence) 재수집 없음.

    프로덕션 무접촉. ⑤ 4C 플래싱 행은 규칙 편집이라 재작업 대상에서 제외한다."""
    if not (revision or "").strip():
        return StageOutput(result_text="✘ 수정 지시가 비어 있습니다.")
    src = "service_portal" if "service_portal" in targets else "release_notes"
    label = "Service Portal 편집" if src == "service_portal" else "Knowledge Base 편집"
    items = [it for it in WS.list_items(mail_id) if it.get("source") == src]
    if not items:
        return StageOutput(result_text=f"✘ 재작업할 편집 문서가 없습니다. 먼저 {label}을 실행하세요.")

    data = release_data(mail_id)
    lines: list[str] = []
    n = 0
    for it in items:
        if it.get("doc_id") == "4C":
            lines.append("  · 4C 플래싱 행은 규칙 편집이라 지시 재작업에서 제외")
            continue
        base = it.get("edited_html") or it.get("original_html") or ""
        progress.step(f"추가 지시 반영 편집 중… ({str(it.get('title', ''))[:30]})")
        res = propose_edit(base, data, src, extra=revision)
        if not res.ok:
            lines.append(f"  ✘ {it.get('title', '')} — {res.error[:60]}")
            continue
        edited, applied = res.edited_html, list(res.applied)
        if it.get("is_resources"):
            # 지시 반영으로 링크가 흐트러질 수 있으니 규칙 편집을 한 번 더 덮어쓴다.
            st_r = get_state(mail_id) if mail_id is not None else {}
            edited, ap2, _fa2, _er2 = _apply_resource_links(
                edited, st_r, it.get("version", ""))
            applied += ap2
        new_item = dict(it)
        new_item.update(edited_html=edited, applied=applied,
                        failed=res.failed, error=res.error)
        if src == "release_notes":
            progress.step("Knowledge Base 미리보기 재렌더 중…")
            preview, _ = RN.storage_to_view(user_id, it.get("doc_id", ""), res.edited_html)
            new_item["preview_html"] = preview or it.get("preview_html", "")
        WS.put(new_item)
        n += 1
        lines.append(f"  ✔ {it.get('title', '')} — Claude {len(res.applied)}건 반영")

    summary = (f"[{label} · 재작업 · 완료 편집본에 지시 반영]\n지시: {revision}\n"
               f"{n}개 문서 편집.\n" + "\n".join(lines)
               + "\n\n※ 원본 재수집 없이 검수 작업공간의 편집본에만 지시를 반영했습니다. (프로덕션 무접촉)")
    return StageOutput(result_text=summary)


def run_portal_edit(
    user_id: int, subject: str, input_params: str = "", mail_id: int | None = None,
    revision: str = "", targets: tuple[str, ...] = ("service_portal", "release_notes"),
) -> StageOutput:
    st = get_state(mail_id) if mail_id is not None else {}
    product = _detect_product(subject, input_params, st)
    android = _detect_android(subject, st)
    if not product:
        return StageOutput(
            result_text="✘ 편집할 제품을 식별하지 못했습니다. 제품 코드(예: PM95)를 입력하거나 "
            "먼저 OS Download·Release Note 제작을 실행하세요."
        )
    # 회차 표기는 **한 군데서** 정한다(app/pipeline/round_version.py). 예전에는 여기서
    # os_version 을 맨 앞에 두었는데, 그 값은 회차를 다시 잡아도 바뀌지 않는 가장 낡은
    # 자리다. 그래서 86.07C8 회차인데 문서에 86.07 이 들어갔다(실측 2026-10-07 job-24).
    from app.pipeline.round_version import round_version

    version, _ver_date, _ver_note = round_version(st)
    data = release_data(mail_id)

    lines: list[str] = [f"제품 {product} · Android {android or '?'} · 새 버전 {version or '(미상)'}"]
    if _ver_note:
        lines.append(f"  · {_ver_note}")
    if not android:
        lines.append("⚠ Android 버전을 식별하지 못했습니다(제목/상태). 제품명만으로 매칭 — 동명 제품 주의.")
    n_docs = 0

    # Resources 문서의 Release Note 링크 = **이번 회차 RN for Sales 의 Dropbox 주소**.
    # 예전에는 ③ Dropbox 업로드 단계가 올려 둔 것을 주워 썼는데, 그 업로드를 단계에서
    # 떼어냈다(2026-09-22 — Newsletter 가 도는 Submission 갈래에 ③ 이 없어서 링크가
    # 생길 길이 없었다). 아직 안 올라갔으면 여기서 올린다. 두 번 올리지는 않는다.
    if mail_id is not None and not str(st.get("rn_link") or "").strip():
        from app.pipeline.rn_dropbox import ensure_rn_link

        _rn_link, _rn_name, _rn_note = ensure_rn_link(user_id, mail_id)
        if _rn_link:
            st["rn_link"] = _rn_link
        else:
            lines.append(f"⚠ Release Note 링크를 얻지 못했습니다 — {_rn_note}")

    # ── ⑤ Service Portal 문서(들) ─────────────────────────────────────
    # 제품에 등록한 Service Portal 문서 id · Knowledge Base 페이지가 있으면 그것으로 직행한다.
    # 제목 매칭은 동명 제품·표기 흔들림에 약하다(실측 id: 51 · 3Q · 5R — 영숫자다).
    from app import os_products as _OP

    _reg = _OP.paths_for(product, android)
    if _reg:
        lines.append(f"· 제품 등록 사용: {_reg['sku']}")

    if "service_portal" in targets:
        WS.clear_source("service_portal", mail_id)
        progress.step("Service Portal 로그인·문서 목록 조회 중…")
        if _reg.get("sp_doc_id"):
            # 등록된 문서 하나만 편집한다(찾지 못하면 아래 목록 매칭으로 넘어가지 않는다 —
            # 등록이 잘못된 것이므로 조용히 다른 문서를 고치면 안 된다).
            _d, _e = SP.get_detail(user_id, _reg["sp_doc_id"])
            if _d is None:
                matched, err = [], f"등록된 문서 #{_reg['sp_doc_id']} 를 읽지 못했습니다: {_e}"
            else:
                matched, err = [{"DownloadId": _reg["sp_doc_id"],
                                 "Title": _d.get("Title", "")}], ""
                lines.append(f"· Service Portal: 등록 문서 #{_reg['sp_doc_id']} "
                             f"{_d.get('Title', '')}")
        else:
            docs, err = SP.list_downloads(user_id)
            matched = ([d for d in docs if _title_matches(d.get("Title", ""), product, android)]
                       if not err else [])
            if not err:
                lines.append(f"· Service Portal: '{product} A{android}' 매칭 문서 {len(matched)}건")
        # 최초 출시 제품은 Download 문서 자체가 없다 — 가장 최근에 손댄 Resources 문서를
        # 복사해 새 문서 초안을 만든다(사용자 확정 2026-08-13). 실제 생성은 검수 완료 때.
        sp_new_title = ""
        if not err and not matched and not _reg.get("sp_doc_id"):
            progress.step("Service Portal 복사 원본(최근 Resources 문서) 조회 중…")
            _src, _serr = SP.latest_resources_doc(user_id)
            if _src is None:
                lines.append(f"✘ Service Portal: 문서가 없고 복사 원본도 못 찾았습니다 — {_serr}")
            else:
                sp_new_title = SP.resources_title(product, android)
                matched = [{"DownloadId": "", "Title": _src.get("Title", ""),
                            "_src_detail": _src}]
                lines.append(f"· Service Portal: 문서가 없어 **새로 만듭니다** — "
                             f"'{sp_new_title}' (복사 원본 '{_src.get('Title','')}')")
        if err:
            lines.append(f"✘ Service Portal 목록 조회 실패: {err}")
        else:
            for _i, d in enumerate(matched[:_MAX_SP_DOCS]):
                did = str(d.get("DownloadId") or "")
                progress.step(f"Service Portal 문서 편집 중… (#{did or '신규'}, "
                              f"{_i + 1}/{min(len(matched), _MAX_SP_DOCS)})")
                if d.get("_src_detail") is not None:
                    detail, e = d["_src_detail"], ""   # 복사 원본(신규 생성용)
                else:
                    detail, e = SP.get_detail(user_id, did)
                if detail is None:
                    lines.append(f"  ✘ #{did} 읽기 실패: {e}")
                    continue
                cont = detail.get("Cont", "") or ""
                res = propose_edit(cont, data, "service_portal", extra=revision)
                edited = res.edited_html if res.ok else cont
                applied, failed = list(res.applied), list(res.failed)

                # Resources 문서면 ③ 산출물 링크를 **규칙으로 덮어쓴다**(Claude 뒤에 적용).
                # 링크·버전 표기는 판단이 아니라 정답이 정해진 값이라 Claude 에 맡기지 않는다.
                # 신규는 원본이 **남의 제품** 문서라 제목으로 판정하면 False 가 된다 —
                # 그러면 ③ 링크 규칙이 안 돌아 빈 껍데기가 올라간다. 신규는 항상 Resources 다.
                is_resources = (bool(d.get("_src_detail") is not None and sp_new_title)
                                or SP.is_resources_title(detail.get("Title", ""), product))
                if is_resources:
                    edited, ap2, fa2, er2 = _apply_resource_links(edited, st, version)
                    applied += ap2
                    failed += fa2
                    if er2:
                        lines.append(f"    ⚠ 링크 편집: {er2}")

                _is_new = bool(d.get("_src_detail") is not None and sp_new_title)
                WS.put({
                    "key": f"{mail_id}:service_portal:{did or 'new'}", "mail_id": mail_id,
                    "source": "service_portal", "doc_id": did,
                    # 신규는 doc_id 가 비어 있다 — 업로드 화면이 이걸 보고 '생성'으로 간다.
                    "sp_new": ({"title": sp_new_title,
                                "from": detail.get("Title", ""),
                                "src_detail": detail} if _is_new else None),
                    "title": sp_new_title if _is_new else detail.get("Title", ""),
                    "editor": "se2",
                    "original_html": cont, "edited_html": edited,
                    "applied": applied, "failed": failed, "error": res.error,
                    "product": product, "version": version,
                    "is_resources": is_resources,   # 재작업 때 링크를 다시 덮어쓰기 위한 표시
                })
                n_docs += 1
                note = (f"Claude {len(res.applied)}건 적용/{len(res.failed)}건 미적용"
                        if res.ok else f"Claude 오류: {res.error[:60]}")
                if len(applied) > len(res.applied):
                    note += f" · 링크 규칙 {len(applied) - len(res.applied)}건"
                lines.append(f"  ✔ {('[신규] ' + sp_new_title) if _is_new else '#' + did + ' ' + detail.get('Title','')}"
                             f" — {note}")

        # 플래싱 툴(4C) 다운로드 편집 — ③에서 올린 flash zip 링크로 Model+Android 행 3곳 교체
        # (OS Version · 다운로드 링크 · 링크 텍스트). 결정적(규칙) 편집, Claude 미사용.
        fl = st.get("flash_upload") or None
        if fl and fl.get("url"):
            progress.step("Service Portal 4C(플래싱) 편집 중…")
            detail4c, e4 = SP.get_detail(user_id, "4C")
            if detail4c is None:
                lines.append(f"  ✘ 4C(플래싱) 읽기 실패: {e4}")
            else:
                cont4c = detail4c.get("Cont", "") or ""
                vfull = st.get("version_full") or version or ""
                new4c, ap4, fa4, er4 = SP.edit_flash_row(
                    cont4c, product, android, vfull, fl["url"], fl.get("size", 0))
                WS.put({
                    "key": f"{mail_id}:service_portal:4C", "mail_id": mail_id,
                    "source": "service_portal", "doc_id": "4C",
                    "title": detail4c.get("Title", "") or "OS Flashing (4C)", "editor": "se2",
                    "original_html": cont4c, "edited_html": new4c,
                    "applied": ap4, "failed": fa4, "error": er4,
                    "product": product, "version": version,
                })
                n_docs += 1
                lines.append("  ✔ 4C 플래싱: "
                             + (f"{product} A{android} 행 3곳 교체(OS Version·링크·텍스트)"
                                if ap4 else f"실패 — {er4}"))
        else:
            lines.append("  · 4C 플래싱 편집 스킵(Dropbox 업로드에서 flash zip 링크 없음)")

    # ── ⑥ OS/FW Release Notes(Confluence) 페이지 ──────────────────────
    if "release_notes" in targets:
        WS.clear_source("release_notes", mail_id)
        progress.step("Release Notes(Confluence) 로그인·페이지 읽는 중…")
        # 등록된 KB 페이지가 있으면 **제목으로 정확히** 그 페이지를 연다(추정하지 않는다).
        _kw = {}
        if _reg.get("kb_title"):
            _kw = {"page_title": _reg["kb_title"]}
            if _reg.get("kb_space"):
                _kw["space"] = _reg["kb_space"]
            lines.append(f"· Knowledge Base: 등록 페이지 '{_reg['kb_title']}'")
        rn = RN.read_release_note(user_id, product, android_version=android,
                                  headful=False, **_kw)
        # 최초 출시 제품은 KB 페이지 자체가 없다 — 못 읽었다고 끝내지 말고, 가장 최근에
        # 손댄 RN 페이지를 복사해 **새 페이지 뼈대**를 만들고 그 위에 첫 블록을 올린다.
        # 실제 생성은 여기서 하지 않는다(검수 완료 = 업로드 때 한다).
        new_page: dict = {}
        if not rn.found:
            from app.pipeline import kb_new_page as KNEW

            _src, _serr = KNEW.latest_rn_page(user_id)
            if _src is None:
                lines.append(f"✘ OS/FW Release Notes 읽기 실패: {rn.error}"
                             f" / 복사할 원본도 찾지 못했습니다: {_serr}")
            else:
                _skel, _info = KNEW.build_skeleton(
                    _src["body"]["storage"]["value"])
                new_page = {
                    "title": _reg.get("kb_title") or KNEW.page_title(product, android),
                    "space": _reg.get("kb_space") or KNEW.DEFAULT_SPACE,
                    "storage": _skel, "from_title": _src["title"],
                    "from_id": str(_src["id"]),
                }
                lines.append(
                    f"· Knowledge Base: 페이지가 없어 **새로 만듭니다** — "
                    f"'{new_page['title']}' (뼈대 원본 '{_src['title']}', {_info})")
        if rn.found or new_page:
            # ⑥ KB: 변경을 NEW/IMPROVED/FIXED 로 분류 → 확정 구조로 새 버전 엔트리 생성 → 최신 위에 삽입.
            #        업로드는 storage 로 나가고, 미리보기는 렌더된 view 로 보여준다.
            storage = new_page["storage"] if new_page else (rn.storage or rn.html or "")
            from app.pipeline import kb_rn

            # Rule A: 고객용 KB 는 ②에서 '선별된 공개 내용'만 반영한다(재작업·수작업 편집 포함).
            from app.pipeline.rn_research import current_public_changes

            changes = current_public_changes(st)
            sec = st.get("security_patch") or ""
            vfull = st.get("version_full") or version or ""
            sdk = (st.get("rn_components") or {}).get("sdk", "")
            progress.step("Knowledge Base 섹션·항목 분류(VoC/EmKit/OS… × NEW/IMPROVED/FIXED) 중…")
            sections = kb_rn.categorize_sections(product, version, changes, revision=revision)
            # 선별표에서 반영으로 둔 시스템 두 줄(보안패치 · 뭉침)도 엔트리에 싣는다 —
            # 실제 KB 관례다(kb_rn.with_system_lines 주석 참고).
            sections = kb_rn.with_system_lines(sections, st.get('rn_select_rows'))
            # OS Download Link = 이번에 올린 sdcard **Full** zip(③ 결과). 최신 블록에만
            # 들어가고, 이전 최신 블록의 링크는 insert_entry 가 걷어낸다.
            _dl = next((u.get("url", "") for u in (st.get("sdcard_uploads") or [])
                        if str(u.get("type", "")).upper().startswith("FULL")), "")
            entry = kb_rn.build_entry_html(vfull, sdk, sections, security_patch=sec,
                                           os_download_url=_dl)
            edited_storage, ins_ok = kb_rn.insert_entry(storage, entry)
            n_new = sum(len(s["new"]) for s in sections)
            n_imp = sum(len(s["improved"]) for s in sections)
            n_fix = sum(len(s["fixed"]) for s in sections)
            if ins_ok:
                applied = [{"why": f"새 버전 엔트리 삽입 · 섹션 {len(sections)}개 · "
                                   f"NEW {n_new} · IMPROVED {n_imp} · FIXED {n_fix}",
                            "old": "(최신 버전 섹션 앞에 삽입)", "new": f"{vfull} 엔트리"}]
                failed, err = [], ""
            else:
                edited_storage = storage
                applied = []
                failed = [{"why": "삽입 위치 못 찾음", "old": "", "new": "",
                           "reason": "최신 버전 섹션(<h2><strong>)을 찾지 못함"}]
                err = "최신 버전 섹션을 찾지 못해 엔트리를 삽입하지 못했습니다."
            progress.step("Knowledge Base 미리보기 렌더 중…")
            # 새 페이지는 아직 id 가 없다 — 렌더는 뼈대를 뜬 원본 페이지를 문맥으로 쓴다.
            _pid = new_page["from_id"] if new_page else rn.page_id
            _title = new_page["title"] if new_page else rn.title
            preview_html, _pverr = RN.storage_to_view(user_id, _pid, edited_storage)
            WS.put({
                "key": f"{mail_id}:release_notes:{_pid if not new_page else 'new'}",
                "mail_id": mail_id,
                "source": "release_notes",
                # 새 페이지는 doc_id 를 비워 둔다 — 업로드 화면이 이걸 보고 '교체'가 아니라
                # '생성'으로 간다(kb_new 에 만들 제목·스페이스가 들어 있다).
                "doc_id": "" if new_page else rn.page_id,
                "kb_new": ({"title": new_page["title"], "space": new_page["space"],
                            "from": new_page["from_title"]} if new_page else None),
                "title": _title, "editor": "storage",
                "original_html": storage, "edited_html": edited_storage,
                "preview_html": preview_html or "",
                "applied": applied, "failed": failed, "error": err,
                "product": product, "version": version,
                # 어느 KB 페이지를 고치는지 **검수 화면에서** 알아야 한다. 예전에는
                # 이 경고가 단계 결과 글에만 있어, 검수자는 제목만 보고 승인했다.
                "ambiguous": bool(rn.ambiguous) and not new_page,
                "ambiguous_why": (
                    f"작업은 Android {android} 인데 고른 페이지는 "
                    f"'{rn.title}' 입니다 — 제품 관리에 이 Android 의 KB 주소가 "
                    "등록되어 있지 않아 제품 이름만으로 골랐습니다."
                ) if (rn.ambiguous and not new_page) else "",
                # 항목 단위 편집(정식 에디터)용: 새 엔트리를 매크로 안전하게 재생성하기 위한 재료.
                "kb_sections": sections, "kb_vfull": vfull, "kb_sdk": sdk, "kb_security": sec,
            })
            n_docs += 1
            note = (f"엔트리 삽입 OK · 섹션 {len(sections)} · NEW {n_new}/IMPROVED {n_imp}/FIXED {n_fix}"
                    if ins_ok else f"삽입 실패: {err}")
            lines.append(f"  ✔ KB {rn.title}{' (애매·확인요)' if rn.ambiguous else ''} — {note}")

    # 이력: SP·KB 최초 생성 스냅샷(생성되는 순간의 내용). 포털 탭에 남긴다.
    from app.pipeline import history as _H
    _doc_of = {"service_portal": "sp", "release_notes": "kb"}
    for _src in targets:
        _doc = _doc_of.get(_src)
        if not _doc:
            continue
        for _it in WS.list_items(mail_id):
            if _it.get("source") != _src:
                continue
            _content = _it.get("edited_html", "") or _it.get("original_html", "")
            _H.record(mail_id, _doc, "생성",
                      summary="문서 생성",
                      text=_H.text_from_html(_content),
                      doc_key=str(_it.get("doc_id", "")),
                      title=_it.get("title", ""))

    label = {("service_portal",): "Service Portal 편집",
             ("release_notes",): "Knowledge Base 편집"}.get(tuple(targets), "포털 편집")
    lines.append("")
    lines.append(
        f"총 {n_docs}개 문서를 담았습니다. "
        + ("바로 아래에서 검수합니다. " if "release_notes" in targets
           else "메뉴 [테스트 → 포털 편집 검수](/portaledit) 에서 검수하세요. ")
        + "(프로덕션 무접촉)"
    )
    return StageOutput(result_text=f"[{label} · 메일 구동]\n" + "\n".join(lines))
