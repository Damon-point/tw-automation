"""저장된 journal 을 **사람이 읽는 타임라인**으로 바꾸고, 소요시간 지표를 유도한다.

Redmine 의 journal detail 은 값이 전부 문자열 id 다(`assigned_to_id: '97' → '207'`).
그대로 화면에 내면 아무도 못 읽으므로 Lookup 테이블로 이름을 붙인다.

**closed_on 을 쓰지 않는 이유**: salesissue 의 Resolved 이슈 중 10% 남짓만 closed_on
이 채워져 있고 일부는 created_on 보다 과거다. 그래서 '언제 Resolved 되었나'는
`status_id → Resolved` 로 바뀐 journal 시각에서 유도한다 — 이게 유일하게 믿을 수 있는 값.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.issuetrack.models import Issue, Journal
from app.issuetrack.sync import lookup_map

#: journal detail 의 name → 한글 라벨. 없는 것은 name 을 그대로 보여준다.
FIELD_LABELS = {
    "status_id": "상태",
    "assigned_to_id": "담당자",
    "priority_id": "우선순위",
    "category_id": "카테고리",
    "tracker_id": "트래커",
    "fixed_version_id": "목표 버전",
    "parent_id": "상위 이슈",
    "project_id": "프로젝트",
    "subject": "제목",
    "description": "설명",
    "done_ratio": "진행률",
    "start_date": "시작일",
    "due_date": "완료 기한",
    "estimated_hours": "예상 공수",
    "is_private": "비공개",
}

#: name 값이 id 라서 이름으로 바꿔야 하는 필드 → Lookup.kind
_ID_FIELDS = {
    "status_id": "status",
    "priority_id": "priority",
    "tracker_id": "tracker",
    "assigned_to_id": "user",
}

#: property='relation' 의 name → 한글 라벨(실제 salesissue 에 나타난 3종).
RELATION_LABELS = {
    "relates": "연관 이슈",
    "copied_to": "복사해 만든 이슈",
    "copied_from": "복사해 온 이슈",
    "duplicates": "중복 이슈",
    "duplicated": "중복된 이슈",
    "blocks": "차단하는 이슈",
    "blocked": "차단된 이슈",
    "precedes": "선행 이슈",
    "follows": "후행 이슈",
}

_EMPTY = "(없음)"


class Naming:
    """Lookup 테이블을 한 번만 읽어 재사용하는 이름 해석기."""

    def __init__(self) -> None:
        self._maps = {k: lookup_map(k) for k in ("status", "priority", "tracker", "user", "cf")}

    def value(self, name: str | None, raw: str | None) -> str:
        if raw in (None, ""):
            return _EMPTY
        kind = _ID_FIELDS.get(name or "")
        if kind:
            return self._maps[kind].get(str(raw), f"#{raw}")
        return str(raw)

    def field(self, name: str | None) -> str:
        return FIELD_LABELS.get(name or "", name or "")

    def custom_field(self, cf_id: str | None) -> str:
        return self._maps["cf"].get(str(cf_id), f"사용자 정의 필드 #{cf_id}")

    def status_ids(self, *names: str) -> set[str]:
        """'Resolved' 같은 상태 이름 → id 문자열 집합."""
        want = {n.lower() for n in names}
        return {k for k, v in self._maps["status"].items() if v.lower() in want}


@dataclass
class Change:
    label: str
    old: str
    new: str


@dataclass
class Event:
    """타임라인 1줄."""

    at: datetime | None
    who: str
    note: str = ""
    changes: list[Change] = field(default_factory=list)
    attachments_added: list[str] = field(default_factory=list)
    attachments_removed: list[str] = field(default_factory=list)
    is_creation: bool = False
    #: 상태 변경이 있었다면 바뀐 뒤 상태 이름(타임라인에서 굵게 표시하려고 따로 둔다).
    new_status: str = ""

    @property
    def kind(self) -> str:
        """화면 아이콘/색을 고르는 데 쓰는 대분류."""
        if self.is_creation:
            return "created"
        if self.new_status:
            return "status"
        if self.attachments_added or self.attachments_removed:
            return "attachment"
        if self.note:
            return "comment"
        return "field"

    @property
    def summary(self) -> str:
        parts = [f"{c.label} {c.old} → {c.new}" for c in self.changes]
        if self.attachments_added:
            parts.append("첨부 추가: " + ", ".join(self.attachments_added))
        if self.attachments_removed:
            parts.append("첨부 삭제: " + ", ".join(self.attachments_removed))
        return " · ".join(parts)


def event_of(j: Journal, naming: Naming) -> Event:
    ev = Event(at=j.created_on, who=j.user_name or _EMPTY, note=(j.notes or "").strip())
    for d in sorted(j.details, key=lambda x: x.seq):
        if d.property == "attachment":
            # name=첨부 id, 추가면 new_value 에, 삭제면 old_value 에 파일명이 온다.
            if d.new_value:
                ev.attachments_added.append(d.new_value)
            elif d.old_value:
                ev.attachments_removed.append(d.old_value)
            continue
        if d.property == "cf":
            ev.changes.append(Change(
                naming.custom_field(d.name),
                d.old_value or _EMPTY, d.new_value or _EMPTY))
            continue
        if d.property == "relation":
            label = RELATION_LABELS.get(d.name or "", d.name or "연결 이슈")
            # 연결은 추가/해제만 있어 old→new 로 쓰면 '(없음) → 32913' 처럼 읽기 나쁘다.
            if d.new_value:
                ev.changes.append(Change(label, "추가", f"#{d.new_value}"))
            else:
                ev.changes.append(Change(label, "해제", f"#{d.old_value}"))
            continue
        # 본문/설명 변경은 통짜 diff 가 실려 오는데 타임라인에 다 뿌리면 못 읽는다.
        if d.name in ("description", "subject"):
            ev.changes.append(Change(naming.field(d.name), "(변경 전)", "(변경 후)"))
            continue
        new = naming.value(d.name, d.new_value)
        if d.name == "status_id":
            ev.new_status = new
        ev.changes.append(Change(
            naming.field(d.name), naming.value(d.name, d.old_value), new))
    return ev


def timeline(issue: Issue, naming: Naming | None = None) -> list[Event]:
    """이슈 생성 + 이후 모든 journal 을 시간순 한 줄기로."""
    naming = naming or Naming()
    events = [Event(
        at=issue.created_on,
        who=issue.author_name or _EMPTY,
        note=(issue.description or "").strip(),
        is_creation=True,
    )]
    events += [event_of(j, naming) for j in
               sorted(issue.journals, key=lambda j: (j.created_on or datetime.min, j.id))]
    return events


# ---------------------------------------------------------- 첫 반응 유형 ---
#: 작성자 외 첫 이벤트를 무엇으로 볼 것인가. 값은 화면 라벨.
#:
#: 한 이벤트에 여러 가지가 섞여 있는 경우가 흔하다 — 운영 데이터에서 가장 많은 조합이
#: '코멘트 + 담당 배정'(464건), 그 다음이 '코멘트 + 상태변경 + 담당 배정'(224건)이다.
#: 그래서 아래 **위에서부터 먼저 걸리는 것 하나**로 대표를 정한다. 상태를 옮긴 것이
#: 코멘트보다 무거운 행동이고, 코멘트는 배정보다 실질적인 반응이라는 순서다.
ACTION_KINDS = {
    "resolve": "바로 해결",
    "analyze": "분석 착수",
    "status": "상태 변경",
    "comment": "코멘트",
    "assign": "담당 배정",
    "attach": "첨부",
    "field": "기타 변경",
    "empty": "내용 없음",
}
#: 표시 순서(분포 차트 축).
ACTION_ORDER = ["resolve", "analyze", "status", "comment", "assign", "attach",
                "field", "empty"]


def _action_kind(j: Journal, naming: Naming) -> str:
    """journal 하나를 첫 반응 유형 한 가지로 줄인다."""
    done = naming.status_ids("Resolved", "Closed", "Rejected")
    progress = naming.status_ids("InProgress")

    has_note = bool((j.notes or "").strip())
    new_status: list[str] = []
    names: set[str] = set()
    props: set[str] = set()
    for d in j.details:
        props.add(d.property or "")
        if d.property == "attachment":
            continue
        names.add(d.name or "")
        if d.name == "status_id" and d.new_value:
            new_status.append(str(d.new_value))

    if any(s in done for s in new_status):
        return "resolve"
    if any(s in progress for s in new_status):
        return "analyze"
    if new_status:
        return "status"
    if has_note:
        return "comment"
    if "assigned_to_id" in names:
        return "assign"
    if "attachment" in props:
        return "attach"
    if names or props:
        return "field"
    return "empty"


# ------------------------------------------------------------- 소요시간 ---
@dataclass
class Timings:
    created_at: datetime | None = None
    first_response_at: datetime | None = None   # 작성자가 아닌 사람의 첫 **코멘트**
    #: 작성자 외 사람의 **첫 이벤트**. 코멘트만 세면 담당만 배정하고 넘어간 건(171건)이나
    #: 필드만 고친 건(46건)이 '반응 없음'으로 잡혀 실제보다 느리게 보인다.
    first_action_at: datetime | None = None
    first_action_kind: str = ""                 # ACTION_KINDS 의 키
    first_action_by: str = ""
    resolved_at: datetime | None = None         # 상태가 Resolved 로 바뀐 **최초** 시각
    #: 상태가 Resolved 로 바뀐 **마지막** 시각. ReOpen 후 다시 해결된 경우 이 값이
    #: resolved_at 보다 뒤다 — SB 문서를 다시 만들어야 하는지 판단하는 기준.
    last_resolved_at: datetime | None = None
    last_event_at: datetime | None = None
    reopened: int = 0                           # Resolved/Closed 이후 다시 열린 횟수
    participants: list[str] = field(default_factory=list)
    comment_count: int = 0

    @staticmethod
    def _days(a: datetime | None, b: datetime | None) -> float | None:
        if not a or not b:
            return None
        return (b - a).total_seconds() / 86400

    @property
    def days_to_first_response(self) -> float | None:
        return self._days(self.created_at, self.first_response_at)

    @property
    def days_to_resolve(self) -> float | None:
        return self._days(self.created_at, self.resolved_at)

    # 업무일 기준 — 주말·한국 공휴일을 뺀다(사용자 확정 2026-08-11).
    @property
    def lead_workdays(self) -> float | None:
        from app.issuetrack.workdays import business_days

        return business_days(self.created_at, self.resolved_at)

    @property
    def first_action_workdays(self) -> float | None:
        from app.issuetrack.workdays import business_days

        return business_days(self.created_at, self.first_action_at)

    @property
    def first_response_workdays(self) -> float | None:
        from app.issuetrack.workdays import business_days

        return business_days(self.created_at, self.first_response_at)


def timings(issue: Issue, naming: Naming | None = None) -> Timings:
    """journal 만으로 계산한다 — closed_on 은 결측·역전이 많아 쓰지 않는다."""
    naming = naming or Naming()
    done_ids = naming.status_ids("Resolved", "Closed")
    resolved_ids = naming.status_ids("Resolved")

    t = Timings(created_at=issue.created_on)
    was_done = False
    seen: list[str] = []

    for j in sorted(issue.journals, key=lambda j: (j.created_on or datetime.min, j.id)):
        if j.created_on and (t.last_event_at is None or j.created_on > t.last_event_at):
            t.last_event_at = j.created_on
        if j.user_name and j.user_name not in seen:
            seen.append(j.user_name)
        outsider = j.user_name != issue.author_name
        if (j.notes or "").strip():
            t.comment_count += 1
            if t.first_response_at is None and j.created_on and outsider:
                t.first_response_at = j.created_on
        if t.first_action_at is None and j.created_on and outsider:
            t.first_action_at = j.created_on
            t.first_action_kind = _action_kind(j, naming)
            t.first_action_by = j.user_name or _EMPTY

        for d in j.details:
            if d.name != "status_id":
                continue
            if d.new_value in resolved_ids:
                if t.resolved_at is None:
                    t.resolved_at = j.created_on
                t.last_resolved_at = j.created_on
            if d.new_value in done_ids:
                was_done = True
            elif was_done:
                t.reopened += 1
                was_done = False

    t.participants = seen
    return t
