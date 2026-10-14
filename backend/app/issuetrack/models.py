"""Redmine 이슈/이벤트 테이블.

설계 요지
- **issue_id, journal_id 를 그대로 PK 로 쓴다.** Redmine 이 부여한 전역 고유 번호라
  증분 동기화에서 중복 판단이 곧 PK 존재 여부로 끝난다(별도 dedup 키 불필요).
- journal(=이벤트) 은 코멘트와 속성 변경을 함께 담는다. 변경 상세는 통계 질의를
  해야 하므로 JSON 이 아니라 별도 행(JournalDetail)으로 편다.
  예: "Resolved 로 바뀐 시각" = detail.name='status_id' and new_value='<resolved id>'
- **closed_on 은 신뢰할 수 없다.** salesissue 의 Resolved 702건 중 68건만 값이 있고
  일부는 created_on 보다 과거다. 소요시간류 지표는 전부 journal 에서 유도한다.
- id→이름 매핑(상태·우선순위·사용자)은 journal detail 이 숫자만 담기 때문에 필요하다.
  Lookup 테이블에 모아 둔다.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.issuetrack.db import IssuetrackBase


class Issue(IssuetrackBase):
    """Redmine 이슈 1건의 현재 스냅샷."""

    __tablename__ = "rm_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # Redmine issue id
    project_id: Mapped[int | None] = mapped_column(Integer)
    project_name: Mapped[str | None] = mapped_column(String(120))

    tracker: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str | None] = mapped_column(String(60), index=True)
    status_id: Mapped[int | None] = mapped_column(Integer)
    priority: Mapped[str | None] = mapped_column(String(60))
    priority_id: Mapped[int | None] = mapped_column(Integer)
    category: Mapped[str | None] = mapped_column(String(120))

    author_id: Mapped[int | None] = mapped_column(Integer)
    author_name: Mapped[str | None] = mapped_column(String(120))
    assigned_to_id: Mapped[int | None] = mapped_column(Integer)
    assigned_to_name: Mapped[str | None] = mapped_column(String(120), index=True)

    subject: Mapped[str] = mapped_column(String(500), default="")
    description: Mapped[str | None] = mapped_column(Text)
    done_ratio: Mapped[int | None] = mapped_column(Integer)

    start_date: Mapped[str | None] = mapped_column(String(20))
    due_date: Mapped[str | None] = mapped_column(String(20))
    created_on: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    updated_on: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    # Redmine 이 주는 값 그대로. 결측·역전이 많아 통계에는 쓰지 않는다(모듈 docstring 참고).
    closed_on: Mapped[datetime | None] = mapped_column(DateTime)

    # 커스텀 필드(SW/HW 버전, 재현여부 등)는 프로젝트마다 달라 JSON 문자열로 보관.
    custom_fields_json: Mapped[str | None] = mapped_column(Text)

    #: 감시 대상에서 뺄 이슈(현재 규칙: 제목에 Janam). 삭제하지 않고 플래그로 둬야
    #: 규칙이 바뀌었을 때 재수집 없이 되살릴 수 있다.
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    excluded_reason: Mapped[str | None] = mapped_column(String(120))

    #: 제목이 [Test] 로 시작하는 기능 검증용 이슈. 수집·SB 생성·이력은 그대로 하되
    #: **통계에서만 뺀다** — 테스트가 소요시간·건수 지표를 흔들면 안 된다.
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    #: journals 까지 받아온 이슈인지. 목록 API 만으로 만든 행은 False.
    detail_fetched: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    #: detail_fetched 시점의 updated_on. 이 값과 updated_on 이 다르면 재수집 대상.
    detail_synced_updated_on: Mapped[datetime | None] = mapped_column(DateTime)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime)

    # lazy="select": 목록 화면은 이슈 수천 건을 훑으므로 타임라인을 딸려 올리면 안 된다.
    # 타임라인이 필요한 화면만 selectinload(Issue.journals) 로 명시해서 가져간다.
    journals: Mapped[list["Journal"]] = relationship(
        back_populates="issue", cascade="all, delete-orphan", lazy="select",
        order_by="Journal.created_on",
    )

    @property
    def url(self) -> str:
        from app.issuetrack.client import issue_url

        return issue_url(self.id)


class Journal(IssuetrackBase):
    """이슈에서 일어난 이벤트 1건(코멘트 + 그때 함께 바뀐 속성들)."""

    __tablename__ = "rm_journals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # Redmine journal id
    issue_id: Mapped[int] = mapped_column(
        ForeignKey("rm_issues.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int | None] = mapped_column(Integer)
    user_name: Mapped[str | None] = mapped_column(String(120), index=True)
    created_on: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    private_notes: Mapped[bool] = mapped_column(Boolean, default=False)

    issue: Mapped["Issue"] = relationship(back_populates="journals")
    details: Mapped[list["JournalDetail"]] = relationship(
        back_populates="journal", cascade="all, delete-orphan", lazy="selectin"
    )


class JournalDetail(IssuetrackBase):
    """journal 안의 속성 변경 1건. (property, name) 예: ('attr','status_id')"""

    __tablename__ = "rm_journal_details"
    # (name, new_value) 묶음 색인은 뺐다 — new_value 에 설명 전문이 들어오면 PostgreSQL 색인 한도(2.7KB)를
    # 넘어 저장이 막힌다. SQL 에서 new_value 로 거르는 곳이 없고(비교는 파이썬에서), name 은 따로 색인이 있다.
    __table_args__ = (
        UniqueConstraint("journal_id", "seq", name="uq_rm_jd_journal_seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    journal_id: Mapped[int] = mapped_column(
        ForeignKey("rm_journals.id", ondelete="CASCADE"), index=True
    )
    #: journal 응답 내 순서. journal 을 다시 받아도 같은 행으로 수렴시키는 키.
    seq: Mapped[int] = mapped_column(Integer, default=0)
    property: Mapped[str | None] = mapped_column(String(40))
    name: Mapped[str | None] = mapped_column(String(80), index=True)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)

    journal: Mapped["Journal"] = relationship(back_populates="details")


class Attachment(IssuetrackBase):
    """이슈 첨부 메타데이터. 파일 내용은 받지 않는다(용량·권한 문제)."""

    __tablename__ = "rm_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # Redmine attachment id
    issue_id: Mapped[int] = mapped_column(
        ForeignKey("rm_issues.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str | None] = mapped_column(String(300))
    filesize: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(120))
    author_name: Mapped[str | None] = mapped_column(String(120))
    created_on: Mapped[datetime | None] = mapped_column(DateTime)


class Lookup(IssuetrackBase):
    """숫자 id → 표시 이름. journal detail 이 '97 → 207' 처럼 id 만 주기 때문에 필요."""

    __tablename__ = "rm_lookups"
    __table_args__ = (UniqueConstraint("kind", "ref_id", name="uq_rm_lookup"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: 'status' | 'priority' | 'tracker' | 'user' | 'category'
    kind: Mapped[str] = mapped_column(String(30), index=True)
    ref_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))


class SBDoc(IssuetrackBase):
    """이슈 하나에서 만들어진 Solution Buffer 문서 초안 1건.

    상태 흐름:
      backlog  — 소급 대상. 규칙상 만들어야 하지만 아직 Claude 를 부르지 않았다.
                 (감시 시작 시점보다 앞서 Resolved 된 이슈. 수동으로만 생성한다.)
      not_needed— 소급 기준일 이전에 해결돼 SB 에 이미 정리돼 있다고 보는 건.
                 기준일을 당기면 backlog 로 되살아난다(scan 이 매번 다시 매긴다).
      not_target— 담당자가 SB 대상(TA 팀)이 아니라 문서를 만들지 않는 건.
                 담당자 설정이 바뀌면 역시 되살아난다.
      queued   — 감시 시작 후 새로 Resolved 됨. 다음 주기에 자동 생성.
      draft    — 초안 생성 완료(담당자에게 아직 안 알림)
      notified — 담당자에게 검토 요청 메일을 보냈다
      approved — 담당자가 업로드하기로 했다
      uploaded — Confluence 에 실제로 올라갔다
      discarded— 올리지 않기로 했다
      error    — 생성 중 실패(사유는 error 컬럼)

    한 이슈가 ReOpen 후 다시 Resolved 되면 **새 행**을 만들고(revision +1) 이전 행은
    superseded 로 표시한다. 이력을 덮어쓰지 않아야 '언제 무엇을 올렸나'를 추적할 수 있다.
    """

    __tablename__ = "rm_sb_docs"
    __table_args__ = (
        UniqueConstraint("issue_id", "revision", name="uq_rm_sb_issue_rev"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issue_id: Mapped[int] = mapped_column(
        ForeignKey("rm_issues.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    superseded: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    state: Mapped[str] = mapped_column(String(20), default="backlog", index=True)
    #: 'auto'    감시 시작 후 새로 Resolved 되어 자동으로 잡힌 건 → 초안이 나오면
    #:           담당자에게 자동으로 검토 요청을 보낸다.
    #: 'backlog' 감시 이전에 이미 해결돼 있던 소급분 → 사람이 화면에서 생성하고,
    #:           메일도 사람이 눌러야 나간다. 과거 이슈로 담당자를 놀라게 하면 안 된다.
    origin: Mapped[str] = mapped_column(String(20), default="backlog", index=True)

    #: 이 초안이 근거로 삼은 'Resolved 로 바뀐 시각'. ReOpen 재해결 감지 기준.
    source_resolved_at: Mapped[datetime | None] = mapped_column(DateTime)

    title: Mapped[str] = mapped_column(String(400), default="")
    is_troubleshooting: Mapped[bool] = mapped_column(Boolean, default=False)
    #: internal | public | partner | ascp
    disclosure: Mapped[str] = mapped_column(String(20), default="internal", index=True)
    disclosure_reason: Mapped[str | None] = mapped_column(Text)
    applies_to: Mapped[str | None] = mapped_column(Text)
    description_html: Mapped[str | None] = mapped_column(Text)
    #: partner 등급일 때 공개 대상. JSON 배열 문자열.
    partners_json: Mapped[str | None] = mapped_column(Text)
    template_id: Mapped[str | None] = mapped_column(String(30))
    labels_json: Mapped[str | None] = mapped_column(Text)

    assignee_name: Mapped[str | None] = mapped_column(String(120))
    assignee_email: Mapped[str | None] = mapped_column(String(200))

    model: Mapped[str | None] = mapped_column(String(60))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)

    confluence_page_id: Mapped[str | None] = mapped_column(String(30))
    confluence_url: Mapped[str | None] = mapped_column(String(500))

    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    decided_by: Mapped[str | None] = mapped_column(String(120))
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime)

    issue: Mapped["Issue"] = relationship()


class SyncState(IssuetrackBase):
    """동기화 진행 상황. 단일 행 key/value."""

    __tablename__ = "rm_sync_state"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)
