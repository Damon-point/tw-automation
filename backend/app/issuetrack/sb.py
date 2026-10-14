"""Resolved 이슈 → Solution Buffer 문서 초안 → 담당자 검토 요청 메일.

흐름
  1. scan()      상태가 Resolved 인 이슈를 훑어 SBDoc 행을 만든다.
                 감시 시작(워터마크) 이후 해결된 것 = queued(자동 생성 대상),
                 그 전에 이미 해결돼 있던 것 = backlog(수동 실행 대상).
  2. generate()  queued/backlog 행 하나를 Claude 로 채운다. 제목·공개등급·본문을
                 한 번에 받아 SBDoc 에 저장한다.
  3. notify()    **초안이 만들어진 그 자리에서** 담당자에게 메일을 보낸다.
                 이게 이 기능의 본줄기다 — 이슈가 Resolved 되면 담당자가 메일을
                 받고, 로그인해서 [Solution Buffer] 탭에서 고치거나 올린다.
                 여기서는 **아무것도 업로드하지 않는다** — Confluence 반영은
                 담당자가 화면에서 [업로드]를 눌러야 일어난다.

**과거분을 자동으로 돌리지 않는 이유**: 감시를 켠 시점에 이미 Resolved 인 이슈가
666건이다. 문서 1건에 약 $0.10 이므로 무심코 전부 돌리면 $60 이상이 한 번에 나간다.
소급은 사람이 건수를 보고 결정할 일이라 backlog 로 남겨 둔다.

읽기 전용 원칙: Redmine 에는 아무것도 쓰지 않는다. Confluence 도 이 모듈에서는
읽지도 쓰지도 않는다(템플릿 id 만 상수로 안다).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.issuetrack.client import issue_url
from app.issuetrack.db import issuetrack_session
from app.issuetrack.events import Naming, timings
from app.issuetrack.models import Issue, SBDoc, SyncState

log = logging.getLogger(__name__)

MODEL = os.getenv("TW_SB_MODEL", "claude-opus-5")
MAX_TOKENS = 16000

#: 공개 등급 → (Confluence 템플릿 id, 템플릿 이름, 붙일 라벨).
#: 라벨은 임의로 정한 게 아니라 각 템플릿에 실제로 달려 있는 값이다.
#: 'posted' 는 여기에 넣지 않는다 — 실제 업로드가 끝난 뒤에 붙일 라벨이다.
TEMPLATES: dict[str, dict] = {
    "public": {
        "template_id": "13402115",
        "name": "Confluence Article Template (전체 공개)",
        "labels": ["sb"],
        "ko": "전체 공개",
    },
    "partner": {
        "template_id": "65536001",
        "name": "Confluence Article (for Partner) Template",
        "labels": ["sb", "partner"],
        "ko": "파트너 공개",
    },
    "ascp": {
        "template_id": "95420419",
        "name": "Confluence Article (for ASCP) Template",
        "labels": ["sb", "ascp"],
        "ko": "ASC/ASP 공개",
    },
    "internal": {
        "template_id": "20676609",
        "name": "Solution Buffer(내부자료) Template",
        "labels": ["confidential", "sb"],
        "ko": "내부 공유",
    },
}

#: partner 템플릿의 체크박스 2종. 화면에서 담당자가 고른다.
PARTNER_CHOICES = {
    "pm_global_korea": "일반 파트너 (pm_global & pm_korea)",
    "pm_amazon": "Amazon (pm_amazon)",
}

#: 이슈 본문·이력이 아주 긴 것이 있어 프롬프트 상한을 둔다(입력 토큰 = 비용).
_MAX_DESC = 6000
_MAX_NOTE = 2000
_MAX_TOTAL = 60000

_gen_lock = threading.Lock()

K_WATERMARK = "sb_watermark"


class SBDraft(BaseModel):
    """Claude 에게 받을 구조화 출력."""

    title: str = Field(description=(
        "SB 문서 제목. 자연어 질문형 또는 설명형. 이슈 제목의 [Issue][PM95] 같은 "
        "대괄호 태그는 제거한다. Redmine 번호는 제목에 넣지 않는다."))
    is_troubleshooting: bool = Field(description=(
        "장애 해결(Troubleshooting) 성격이면 true. 제목 앞에 [TR] 이 붙는다."))
    disclosure: str = Field(description=(
        "공개 등급. internal | public | partner | ascp 중 하나."))
    disclosure_reason: str = Field(description="그 등급을 고른 이유 한 문장.")
    applies_to: str = Field(description=(
        "'1) Applies to' 값. 적용 모델/솔루션. 예: 'PM95 / Android 15, EmKit'"))
    description_html: str = Field(description=(
        "'2) Description' 본문. 평범한 HTML 로 쓴다 — "
        "<p>, <ul>, <ol>, <li>, <strong>, <em>, <table>, <tr>, <th>, <td>, <br>, "
        "<a href> 만 사용한다. 배경 → 증상 → 원인 → 해결 순서로 쓴다. "
        "Redmine 이슈 번호와 링크를 반드시 포함한다. "
        "XHTML 문법에 맞추려 애쓰지 않아도 된다(업로드 직전에 Confluence 가 정규화한다)."))


SYSTEM = """당신은 Point Mobile 기술지원팀의 Solution Buffer(SB) 문서 작성자입니다.
Redmine 이슈가 Resolved 되면 그 내용을 사내 Confluence 의 Solution Buffer 스페이스에
올릴 FAQ 문서로 정리합니다.

SB 문서 규칙:
- 제목은 자연어(질문형 또는 설명형). 이슈 제목의 [Issue][PM95] 같은 태그는 넣지 않는다.
  예: "PM68에서 지원하는 Ethernet chipset 정보", "TR54의 EID 교체가 가능한가요?"
- Troubleshooting(장애 해결) 성격이면 is_troubleshooting=true (제목 앞에 [TR] 이 붙는다).
- 본문은 두 부분:
    1) Applies to : 어떤 모델/솔루션에 적용되는지
    2) Description : 배경(왜 발생했는지) · 증상 · 원인 · 해결. Redmine 번호와 링크 포함.
- 첨부파일은 **파일명만** 언급한다. 업로드하지 않는다.
- 이슈 이력에 나오는 사내 구성원 실명·직급 호칭, 고객사 담당자 이름은 문서에 옮기지 않는다.
- 추측하지 않는다. 이슈에 근거가 없는 원인·수치·버전은 쓰지 않는다.

공개 등급 판단:
- internal : 내부에서만 봐야 할 것. 미출시 정보, 원인이 자사 결함으로 드러난 것,
             고객사명·계약 정보가 얽힌 것, 아직 확정되지 않은 내용
- public   : 제약 없이 공개 가능한 일반 사용법·스펙·설정 방법
- partner  : 파트너에게만 유용한 설정/운영 정보
- ascp     : ASC/ASP(서비스 협력사) 대상 수리·진단 정보
확신이 서지 않으면 더 보수적인 쪽(internal)을 고른다."""


# ------------------------------------------------------------------ 상태 ---
def _state(key: str, default: str | None = None) -> str | None:
    with issuetrack_session() as db:
        row = db.get(SyncState, key)
        return row.value if row and row.value is not None else default


def _set_state(key: str, value: str | None) -> None:
    with issuetrack_session() as db:
        row = db.get(SyncState, key)
        if row is None:
            row = SyncState(key=key)
            db.add(row)
        row.value = value
        row.updated_at = datetime.utcnow()
        db.commit()


def watermark() -> datetime | None:
    """감시 시작 시각. 이 시각 이후 Resolved 된 것만 자동 생성 대상."""
    v = _state(K_WATERMARK)
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


# ------------------------------------------------------------------ 대상 ---
def assignee_email(name: str | None) -> str:
    """Redmine 담당자 이름 → 사내 메일 주소.

    Redmine 계정명이 'samuel.seo', 'phill.kim' 처럼 메일 로컬파트와 같다.
    한글 이름이나 공백이 섞이면 규칙을 못 쓰므로 빈 문자열을 돌려주고,
    호출부가 관리자에게 대신 보내도록 한다.
    """
    n = (name or "").strip()
    if not n or not re.fullmatch(r"[A-Za-z0-9._-]+", n):
        return ""
    return f"{n.lower()}@pointmobile.com"


def _latest_doc(db, issue_id: int) -> SBDoc | None:
    return db.execute(
        select(SBDoc).where(SBDoc.issue_id == issue_id)
        .order_by(SBDoc.revision.desc()).limit(1)
    ).scalars().first()


def scan() -> dict:
    """Resolved 이슈를 훑어 SBDoc 행을 만든다. Claude 는 호출하지 않는다.

    최초 실행 때 워터마크를 '지금'으로 찍는다. 그래서 이미 쌓여 있던 Resolved 는
    전부 소급(backlog) 이 되고, 이후 새로 해결되는 것만 queued(자동 생성)가 된다.

    소급 기준일(설정 → Solution Buffer)보다 **전에** 해결된 건은 SB 에 이미 정리돼
    있다고 보고 not_needed 로 둔다. 아직 만들지 않은 행은 매번 기준일로 상태를
    다시 매기므로, 기준일을 당기면 backlog 로 되살아난다.
    """
    from app.issuetrack import settings

    first_run = watermark() is None
    if first_run:
        _set_state(K_WATERMARK, datetime.utcnow().isoformat())
    mark = watermark()
    cutoff = settings.cutoff_date()
    ta = settings.sb_assignee_set()

    naming = Naming()
    added_queued = added_backlog = added_skip = superseded = restated = 0

    def _is_target(assignee: str | None) -> bool:
        """Solution Buffer 는 TA 팀 전용이라 담당자를 가린다(빈 설정이면 전부 대상)."""
        return not ta or (assignee or "").strip().lower() in ta

    def _wanted(lr: datetime | None, assignee: str | None) -> str:
        """아직 만들지 않은 행의 상태."""
        if not _is_target(assignee):
            return "not_target"
        if cutoff is not None and lr is not None and lr < cutoff:
            return "not_needed"
        return "backlog"

    with issuetrack_session() as db:
        issues = db.execute(
            select(Issue).options(selectinload(Issue.journals))
            .where(Issue.excluded.is_(False), Issue.status == "Resolved")
        ).scalars().all()

        for issue in issues:
            lr = timings(issue, naming).last_resolved_at
            if lr is None:
                # 상태가 Resolved 인데 그렇게 바뀐 이력이 없다 — 이관·직접 생성 등.
                # 근거 시각이 없으면 재해결 판단을 못 하므로 생성 시각으로 대신한다.
                lr = issue.created_on
            prev = _latest_doc(db, issue.id)

            if prev is not None and prev.source_resolved_at == lr:
                # 이미 이 해결분으로 만든 행이 있다. 아직 사람 손을 타지 않았다면
                # 기준일·담당자 설정이 바뀌었을 수 있으니 상태를 다시 매긴다.
                # 알림을 보냈거나 올린 건은 건드리지 않는다 — 이미 벌어진 일이다.
                if prev.state in ("backlog", "not_needed", "not_target",
                                  "queued", "draft"):
                    want = _wanted(lr, prev.assignee_name)
                    # 이미 만들어 둔 초안은 대상이면 draft 로 살려 둔다.
                    if want == "backlog" and prev.generated_at:
                        want = "draft"
                    if prev.state != want:
                        prev.state = want
                        restated += 1
                continue

            if prev is not None:
                prev.superseded = True        # ReOpen 후 재해결 → 새 리비전
                superseded += 1

            target = _is_target(issue.assigned_to_name)
            queued = bool(target and mark and lr and lr >= mark)
            state = "queued" if queued else _wanted(lr, issue.assigned_to_name)
            doc = SBDoc(
                issue_id=issue.id,
                revision=(prev.revision + 1) if prev else 1,
                state=state,
                origin="auto" if queued else "backlog",
                source_resolved_at=lr,
                assignee_name=issue.assigned_to_name,
                assignee_email=assignee_email(issue.assigned_to_name),
                # 이전 문서를 이미 올렸다면 그 페이지를 물려받아 '갱신'이 가능하게 한다.
                confluence_page_id=prev.confluence_page_id if prev else None,
                confluence_url=prev.confluence_url if prev else None,
                created_at=datetime.utcnow(),
            )
            db.add(doc)
            if queued:
                added_queued += 1
            elif state == "backlog":
                added_backlog += 1
            else:
                added_skip += 1
        db.commit()

    if added_queued or added_backlog or superseded or restated:
        log.info("SB scan: queued=%s backlog=%s 소급불필요=%s 재분류=%s superseded=%s",
                 added_queued, added_backlog, added_skip, restated, superseded)
    return {"queued": added_queued, "backlog": added_backlog,
            "not_needed": added_skip, "restated": restated,
            "superseded": superseded, "first_run": first_run}


# ------------------------------------------------------------- 프롬프트 ---
def issue_text(issue: Issue, naming: Naming | None = None) -> str:
    """이슈 전문을 Claude 에게 넘길 평문으로. 저장된 데이터만 쓴다(재조회 없음)."""
    naming = naming or Naming()
    parts = [
        f"[Redmine #{issue.id}] {issue.subject}",
        f"링크: {issue_url(issue.id)}",
        f"상태: {issue.status} / 우선순위: {issue.priority} / 카테고리: {issue.category or '-'}",
        f"작성자: {issue.author_name} / 담당자: {issue.assigned_to_name}",
        f"생성: {issue.created_on} / 최종갱신: {issue.updated_on}",
    ]
    for f in json.loads(issue.custom_fields_json or "[]"):
        if f.get("value") and str(f["value"]).strip().upper() != "NA":
            parts.append(f"{str(f.get('name', '')).strip()}: {f['value']}")

    parts.append("\n[설명]\n" + ((issue.description or "(없음)")[:_MAX_DESC]))
    parts.append("\n[진행 이력]")
    from app.issuetrack.events import event_of

    for j in sorted(issue.journals, key=lambda x: (x.created_on or datetime.min, x.id)):
        ev = event_of(j, naming)
        line = f"- {ev.at} {ev.who}"
        if ev.summary:
            line += f" [{ev.summary}]"
        if ev.note:
            line += "\n    " + ev.note[:_MAX_NOTE]
        parts.append(line)

    text = "\n".join(parts)
    if len(text) > _MAX_TOTAL:
        # 앞(배경)과 뒤(해결)가 중요하므로 가운데를 자른다.
        head, tail = text[: _MAX_TOTAL // 2], text[-(_MAX_TOTAL // 2):]
        text = head + "\n\n…(중략)…\n\n" + tail
    return text


_ALLOWED_TAGS = re.compile(
    r"</?(?:p|br|ul|ol|li|strong|em|b|i|code|pre|table|thead|tbody|tr|th|td|"
    r"h[1-6]|a|span|div)\b[^>]*>", re.I)


def sanitize_html(html: str) -> str:
    """본문에서 위험하거나 뜻 없는 태그를 걷어낸다. **문법 교정은 하지 않는다.**

    XHTML 로 맞추는 일(홑따옴 & 이스케이프, <tbody> 삽입, 안 닫힌 태그)은
    Confluence 의 변환 API 가 훨씬 정확하게 해 준다 — 업로드 직전에
    SBConfluence.to_storage() 로 넘긴다. 여기서 흉내 내면 두 규칙이 어긋나
    오히려 깨진다. 이 함수의 책임은 script/iframe 같은 것을 막는 것뿐이다.
    """
    out = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", html or "")
    out = re.sub(r"(?is)<!--.*?-->", "", out)

    def _keep(m: re.Match) -> str:
        return m.group(0) if _ALLOWED_TAGS.fullmatch(m.group(0)) else ""

    return re.sub(r"<[^>]+>", _keep, out).strip()


# -------------------------------------------------------------- 초안 생성 ---
def generate(doc_id: int) -> dict:
    """SBDoc 한 건을 Claude 로 채운다. 반환 {ok, error, doc_id}."""
    import anthropic

    with issuetrack_session() as db:
        doc = db.get(SBDoc, doc_id)
        if doc is None:
            return {"ok": False, "error": "문서를 찾을 수 없습니다."}
        if doc.state not in ("backlog", "queued", "error"):
            return {"ok": False, "error": f"이미 처리된 문서입니다(state={doc.state})."}
        issue = db.execute(
            select(Issue).options(selectinload(Issue.journals))
            .where(Issue.id == doc.issue_id)
        ).scalars().first()
        if issue is None:
            return {"ok": False, "error": "이슈를 찾을 수 없습니다."}
        prompt = issue_text(issue)

    from app import usage

    # 사용량 대시보드에서 이 호출이 어디서 났는지 보이게 라벨을 붙인다.
    usage.set_operation("SB 문서 초안", None)
    try:
        resp = anthropic.Anthropic().messages.parse(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            messages=[{"role": "user",
                       "content": f"아래 Redmine 이슈를 SB 문서로 정리하세요.\n\n{prompt}"}],
            output_format=SBDraft,
        )
    except Exception as e:  # noqa: BLE001
        _mark_error(doc_id, f"Claude 호출 실패: {e}")
        return {"ok": False, "error": str(e), "doc_id": doc_id}

    if getattr(resp, "stop_reason", "") == "refusal":
        _mark_error(doc_id, f"Claude 가 응답을 거부했습니다: {resp.stop_details}")
        return {"ok": False, "error": "refusal", "doc_id": doc_id}

    d = resp.parsed_output
    disclosure = d.disclosure if d.disclosure in TEMPLATES else "internal"
    tpl = TEMPLATES[disclosure]

    with issuetrack_session() as db:
        doc = db.get(SBDoc, doc_id)
        doc.title = ("[TR] " if d.is_troubleshooting else "") + d.title.strip()
        doc.is_troubleshooting = bool(d.is_troubleshooting)
        doc.disclosure = disclosure
        doc.disclosure_reason = d.disclosure_reason
        doc.applies_to = d.applies_to
        doc.description_html = sanitize_html(d.description_html)
        doc.template_id = tpl["template_id"]
        doc.labels_json = json.dumps(tpl["labels"], ensure_ascii=False)
        doc.model = MODEL
        doc.input_tokens = int(getattr(resp.usage, "input_tokens", 0) or 0)
        doc.output_tokens = int(getattr(resp.usage, "output_tokens", 0) or 0)
        doc.error = None
        doc.state = "draft"
        doc.generated_at = datetime.utcnow()
        db.commit()
        title = doc.title
    log.info("SB 초안 생성: #%s rev%s %s", doc.issue_id, doc.revision, title[:60])
    return {"ok": True, "doc_id": doc_id, "title": title, "disclosure": disclosure}


def _mark_error(doc_id: int, msg: str) -> None:
    with issuetrack_session() as db:
        doc = db.get(SBDoc, doc_id)
        if doc is not None:
            doc.state = "error"
            doc.error = msg[:2000]
            db.commit()
    log.warning("SB 초안 생성 실패(doc %s): %s", doc_id, msg)


def generate_pending(*, limit: int = 5, states=("queued",),
                     assignee: str = "") -> dict:
    """생성 대기 문서를 순서대로 만든다. 락으로 중복 실행을 막는다.

    assignee 를 주면 그 담당자 문서만 — 화면에서 '내 담당만' 보고 있는 사람이
    [소급 생성]을 눌렀을 때 남의 문서까지 만들면 안 된다.
    """
    if not _gen_lock.acquire(blocking=False):
        return {"skipped": True, "reason": "이미 생성 중입니다."}
    try:
        conds = [SBDoc.state.in_(tuple(states))]
        if assignee:
            conds.append(SBDoc.assignee_name == assignee)
        with issuetrack_session() as db:
            ids = [
                i for (i,) in db.execute(
                    select(SBDoc.id).where(*conds)
                    .order_by(SBDoc.source_resolved_at.desc()).limit(limit)
                ).all()
            ]
        made, fail = [], 0
        for did in ids:
            res = generate(did)
            if res.get("ok"):
                made.append(did)
            else:
                fail += 1
        # 방금 만든 문서 id 를 돌려준다 — 호출부가 곧바로 담당자에게 알릴 수 있게.
        return {"generated": len(made), "failed": fail, "attempted": len(ids),
                "generated_ids": made}
    finally:
        _gen_lock.release()


# ---------------------------------------------------------------- 알림 메일 ---
def _app_url() -> str:
    from app.user_mailer import app_url

    return app_url()


def notify(doc_id: int) -> dict:
    """담당자에게 검토 요청 메일. 담당자 주소를 못 만들면 관리자에게 보낸다."""
    from app.background import _sender_user_id
    from app.user_mailer import admin_emails, send_as_user

    with issuetrack_session() as db:
        doc = db.get(SBDoc, doc_id)
        if doc is None:
            return {"sent": False, "error": "문서를 찾을 수 없습니다."}
        if doc.state != "draft":
            return {"sent": False, "error": f"초안 상태가 아닙니다(state={doc.state})."}
        issue = db.get(Issue, doc.issue_id)
        info = {
            "issue_id": doc.issue_id, "subject": issue.subject if issue else "",
            "title": doc.title, "disclosure": doc.disclosure,
            "reason": doc.disclosure_reason or "", "applies_to": doc.applies_to or "",
            "assignee": doc.assignee_name or "", "to": doc.assignee_email or "",
            "revision": doc.revision,
        }

    to = [info["to"]] if info["to"] else admin_emails()
    fallback = not info["to"]
    if not to:
        return {"sent": False, "error": "수신자를 정할 수 없습니다(담당자 주소·관리자 주소 모두 없음)."}

    uid = _sender_user_id()
    if uid is None:
        return {"sent": False, "error": "발송 계정 없음 — [계정 관리]에서 이메일 자격증명을 등록하세요."}

    from app import mail_templates as MT

    tpl = TEMPLATES[info["disclosure"]]
    rows = [
        f"■ 이슈       : #{info['issue_id']} {info['subject'][:70]}",
        f"                {issue_url(info['issue_id'])}",
        f"■ 문서 제목  : {info['title']}",
        f"■ 추천 등급  : {info['disclosure']} ({tpl['ko']}) — {info['reason']}",
        f"■ Applies to : {info['applies_to'][:120]}",
    ]
    if info["revision"] > 1:
        rows.append(f"■ 개정       : {info['revision']}판 "
                    "(이슈가 다시 열렸다가 해결되어 새로 작성했습니다)")
    subject, body = MT.render(
        "sb_review",
        담당자=info["assignee"] or "담당자",
        이슈번호=info["issue_id"],
        문서제목=info["title"][:50],
        정보표="\n".join(rows) + "\n",
        대신보냄=(f"\n※ 담당자({info['assignee']}) 메일 주소를 규칙으로 만들지 못해 "
                "관리자에게 대신 보냅니다.\n" if fallback else ""),
        화면주소=f"{_app_url()}/issues",
    )
    ok, err = send_as_user(uid, to, subject, body, as_system=True)
    if not ok:
        return {"sent": False, "error": err, "to": to}

    with issuetrack_session() as db:
        doc = db.get(SBDoc, doc_id)
        if doc is not None:
            doc.state = "notified"
            doc.notified_at = datetime.utcnow()
            db.commit()
    log.info("SB 검토 요청 발송: #%s → %s", info["issue_id"], ", ".join(to))
    return {"sent": True, "to": to}


def notify_many(doc_ids) -> dict:
    """방금 만든 초안들을 담당자에게 알린다. origin='auto' 인 것만 보낸다.

    소급분(backlog)은 사람이 화면에서 하나씩 보내야 한다. 몇 달 지난 이슈로
    담당자에게 갑자기 메일이 가면 안 된다.
    """
    if not doc_ids:
        return {"sent": 0, "failed": 0}
    with issuetrack_session() as db:
        auto = {i for (i,) in db.execute(
            select(SBDoc.id).where(SBDoc.id.in_(list(doc_ids)),
                                   SBDoc.origin == "auto")).all()}
    sent = failed = 0
    errs: list[str] = []
    for did in doc_ids:
        if did not in auto:
            continue
        res = notify(did)
        if res.get("sent"):
            sent += 1
        else:
            failed += 1
            errs.append(str(res.get("error", "")))
    return {"sent": sent, "failed": failed, "errors": errs[:3]}


def notify_pending(*, limit: int = 10) -> dict:
    """알림을 못 보낸 채 남아 있는 초안을 다시 시도한다(재시도 그물).

    보통은 generate 직후에 곧바로 보내므로 여기 걸리는 건 그때 발송이 실패한
    것들이다 — 메일 서버가 잠깐 죽었거나 발송 계정이 아직 없었던 경우.
    """
    with issuetrack_session() as db:
        ids = [i for (i,) in db.execute(
            select(SBDoc.id).where(SBDoc.state == "draft", SBDoc.origin == "auto")
            .order_by(SBDoc.generated_at).limit(limit)).all()]
    return notify_many(ids)


# -------------------------------------------------------------- 사람의 손 ---
EDITABLE = ("title", "disclosure", "applies_to", "description_html")


def update_draft(doc_id: int, **fields) -> dict:
    """화면에서 고친 내용을 반영. 등급을 바꾸면 템플릿·라벨도 따라 바뀐다."""
    from app.issuetrack.confluence import default_labels

    with issuetrack_session() as db:
        doc = db.get(SBDoc, doc_id)
        if doc is None:
            return {"ok": False, "error": "문서를 찾을 수 없습니다."}
        if doc.state == "uploaded":
            return {"ok": False, "error": "이미 업로드된 문서는 고칠 수 없습니다."}

        for k in EDITABLE:
            if k in fields and fields[k] is not None:
                setattr(doc, k, fields[k])
        if "partners" in fields:
            doc.partners_json = json.dumps(fields["partners"] or [], ensure_ascii=False)
        if doc.description_html:
            doc.description_html = sanitize_html(doc.description_html)
        doc.is_troubleshooting = (doc.title or "").startswith("[TR]")

        if doc.disclosure not in TEMPLATES:
            doc.disclosure = "internal"
        doc.template_id = TEMPLATES[doc.disclosure]["template_id"]
        if "labels" in fields and fields["labels"] is not None:
            doc.labels_json = json.dumps(fields["labels"], ensure_ascii=False)
        elif "disclosure" in fields:
            # 등급이 바뀌면 라벨을 그 등급의 기본값으로 되돌린다. 이전 등급의 라벨을
            # 남겨 두면 confidential 문서에 partner 라벨이 붙는 사고가 난다.
            doc.labels_json = json.dumps(default_labels(doc), ensure_ascii=False)
        db.commit()
    return {"ok": True}


def discard(doc_id: int, *, by: str = "", reason: str = "") -> dict:
    """올리지 않기로 결정. 초안은 이력으로 남긴다(지우지 않는다)."""
    with issuetrack_session() as db:
        doc = db.get(SBDoc, doc_id)
        if doc is None:
            return {"ok": False, "error": "문서를 찾을 수 없습니다."}
        if doc.state == "uploaded":
            return {"ok": False, "error": "이미 업로드된 문서입니다."}
        doc.state = "discarded"
        doc.decided_at = datetime.utcnow()
        doc.decided_by = by or None
        if reason:
            doc.error = f"폐기 사유: {reason}"[:2000]
        db.commit()
    return {"ok": True}


def reopen_draft(doc_id: int) -> dict:
    """폐기했던 초안을 다시 검토 대상으로 되돌린다."""
    with issuetrack_session() as db:
        doc = db.get(SBDoc, doc_id)
        if doc is None or doc.state != "discarded":
            return {"ok": False, "error": "폐기된 문서가 아닙니다."}
        doc.state = "draft" if doc.generated_at else "backlog"
        doc.decided_at = None
        doc.decided_by = None
        db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ 요약 ---
#: 비용 추정용 실측치(프로토타입 1건 + 초기 생성분 기준). 이슈마다 이력 길이가
#: 달라 편차가 크므로 '대략 이 정도'를 안내하는 용도로만 쓴다.
EST_IN_TOKENS = 12000
EST_OUT_TOKENS = 2500
PRICE_IN, PRICE_OUT = 5.0, 25.0     # claude-opus-5, $/1M


def estimate_cost(n: int) -> float:
    return n * (EST_IN_TOKENS / 1e6 * PRICE_IN + EST_OUT_TOKENS / 1e6 * PRICE_OUT)


def assignees_without_account() -> list[tuple[str, int]]:
    """검토를 맡을 담당자인데 앱 계정이 없는 사람 [(이름, 문서 수)].

    메일은 "로그인해서 확인하세요"라고 안내하는데 계정이 없으면 들어올 수가 없다.
    자동 발송을 켜기 전에 이걸 먼저 봐야 한다.
    """
    from app.database import get_session
    from app.models import User

    try:
        with get_session() as db:
            accounts = {u.username for u in db.query(User).all()}
        with issuetrack_session() as db:
            rows = db.execute(
                select(SBDoc.assignee_name, func.count())
                .where(SBDoc.superseded.is_(False),
                       SBDoc.state.in_(("queued", "draft", "notified")),
                       SBDoc.assignee_name.isnot(None))
                .group_by(SBDoc.assignee_name)
                .order_by(func.count().desc())).all()
    except Exception:  # noqa: BLE001
        return []
    return [(n, c) for n, c in rows if n not in accounts]


def summary(assignee: str = "") -> dict:
    """상태별 건수·비용. assignee 를 주면 그 담당자 문서만 센다(화면 범위와 맞춘다)."""
    conds = [SBDoc.superseded.is_(False)]
    if assignee:
        conds.append(SBDoc.assignee_name == assignee)
    with issuetrack_session() as db:
        counts = dict(db.execute(
            select(SBDoc.state, func.count()).where(*conds)
            .group_by(SBDoc.state)).all())
        tok = db.execute(select(
            func.coalesce(func.sum(SBDoc.input_tokens), 0),
            func.coalesce(func.sum(SBDoc.output_tokens), 0)).where(*conds)).one()
    spent = tok[0] / 1e6 * PRICE_IN + tok[1] / 1e6 * PRICE_OUT
    return {
        "counts": counts,
        "backlog": counts.get("backlog", 0),
        "queued": counts.get("queued", 0),
        "draft": counts.get("draft", 0),
        "notified": counts.get("notified", 0),
        "error": counts.get("error", 0),
        "spent_usd": round(spent, 3),
        "watermark": _state(K_WATERMARK),
        "running": _gen_lock.locked(),
    }
