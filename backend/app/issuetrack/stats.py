"""이슈 통계 — **전부 journal 에서 유도한다**.

Redmine 이 주는 closed_on 을 쓰지 않는 이유(실측):
  · Resolved 로 바뀐 이력이 있는 이슈 1,139건 중 closed_on 이 채워진 건 531건뿐.
    나머지 608건은 closed_on 만 보면 '아직 안 끝난 이슈'로 잡힌다.
  · 값이 있는 것 중에도 created_on 보다 과거인 것이 있어 소요일이 음수가 된다.
그래서 '언제 해결됐나'는 status_id → Resolved 로 바뀐 journal 시각에서 뽑는다.
이 값으로 계산하면 음수가 0건이다.

대푯값은 **평균**이다(사용자 확정 2026-08-11). 소요시간은 오래 끈 몇 건이 평균을 끌어
올리므로 화면에서 중앙값도 함께 보여 준다 — 대표 숫자만 평균으로 바꾼다.

소요시간은 **업무일**이다. 주말과 한국 공휴일을 뺀다(app/issuetrack/workdays.py).
설·추석이 낀 건과 안 낀 건을 나란히 놓으려면 달력일로는 안 된다.

계산 비용: 이슈 1,600여 건 + 이벤트 14,000여 건을 전부 훑어 1초 남짓. 캐시 없이 매번 돈다.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.issuetrack.db import issuetrack_session
from app.issuetrack.events import ACTION_KINDS, ACTION_ORDER, Naming, timings
from app.issuetrack.models import Issue
from app.issuetrack.workdays import business_days, calendar_days

#: 제목 앞머리의 [PM95] 같은 태그. 제품·구분 분포를 뽑는 데 쓴다.
_TAG = re.compile(r"\[([^\]]{1,24})\]")

#: 아직 안 끝난 것으로 보는 상태.
OPEN_STATUSES = ("Open", "ReOpen", "InProgress", "Monitoring", "Holding")

#: 소요시간 히스토그램 구간(업무일). 사내 대응 감각에 맞춰 끊는다.
DURATION_BINS = [
    (0, 1, "1일 이내"),
    (1, 3, "1~3일"),
    (3, 5, "3~5일"),
    (5, 10, "1~2주"),
    (10, 20, "2~4주"),
    (20, 60, "1~3개월"),
    (60, float("inf"), "3개월 이상"),
]

#: [Issue] 태그가 붙은 건을 묶는 기준.
GROUP_BASES = {"target": "대상(제목 두 번째 태그)", "category": "카테고리"}

#: 대상 태그를 묶을 때 자르는 구분자. '-' 는 자르지 않는다 — PMDM-ENT · L-POS 처럼
#: 그 자체가 이름인 것들이 있다.
_SPLIT = re.compile(r"[ _,+/]+")


@dataclass
class IssueStat:
    id: int
    subject: str
    status: str
    assignee: str
    category: str
    created_at: datetime | None
    resolved_at: datetime | None
    #: Lead time — 생성 → Resolved, **업무일**
    lead_days: float | None
    #: 같은 구간의 달력일. 업무일과 나란히 보여 주려고 남긴다.
    lead_cal_days: float | None
    #: 작성자 외 첫 이벤트까지, 업무일
    first_action_days: float | None
    first_action_kind: str
    first_action_by: str
    #: 작성자 외 첫 코멘트까지, 업무일
    first_comment_days: float | None
    reopened: int
    comments: int
    participants: int

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def tags(self) -> list[str]:
        return [t.strip() for t in _TAG.findall(self.subject) if t.strip()]

    @property
    def is_issue_tagged(self) -> bool:
        return any(t.lower() == "issue" for t in self.tags)

    @property
    def target(self) -> str:
        """제목의 두 번째 태그 = 무엇에 대한 건인가(PM95 · PMDM · Scan2Stage …).

        `[Issue][PM85_A9] …` 처럼 OS 버전까지 붙는 표기가 흔해 그대로 세면 PM85 가
        PM85 / PM85_A9 / PM85 PIE / PM85_O 로 흩어진다. 첫 토큰만 남겨 묶는다.
        """
        rest = [t for t in self.tags if t.lower() != "issue"]
        if not rest:
            return "(태그 없음)"
        head = _SPLIT.split(rest[0].strip())[0]
        return head.upper() if head else rest[0]


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def collect(*, include_excluded: bool = False) -> list[IssueStat]:
    """이슈별 지표 한 벌. 화면에서 여러 집계를 만들 때 이걸 한 번만 돌려 재사용한다.

    제목이 [Test] 로 시작하는 기능 검증용 이슈는 **항상 뺀다**. 수집·SB 생성·이력에는
    그대로 남지만, 테스트 몇 건이 소요시간 평균이나 월별 건수를 흔들면 통계를
    믿을 수 없게 된다.
    """
    naming = Naming()
    out: list[IssueStat] = []
    with issuetrack_session() as db:
        q = select(Issue).options(selectinload(Issue.journals)) \
            .where(Issue.is_test.is_(False))
        if not include_excluded:
            q = q.where(Issue.excluded.is_(False))
        for issue in db.execute(q).scalars().all():
            t = timings(issue, naming)
            out.append(IssueStat(
                id=issue.id,
                subject=issue.subject or "",
                status=issue.status or "",
                assignee=issue.assigned_to_name or "(미지정)",
                category=issue.category or "(없음)",
                created_at=issue.created_on,
                resolved_at=t.last_resolved_at,
                lead_days=business_days(issue.created_on, t.resolved_at),
                lead_cal_days=calendar_days(issue.created_on, t.resolved_at),
                first_action_days=t.first_action_workdays,
                first_action_kind=t.first_action_kind,
                first_action_by=t.first_action_by,
                first_comment_days=t.first_response_workdays,
                reopened=t.reopened,
                comments=t.comment_count,
                participants=len(t.participants),
            ))
    return out


def _vals(rows: list[IssueStat], field_name: str) -> list[float]:
    return [v for v in (getattr(r, field_name) for r in rows)
            if v is not None and v >= 0]


def summary(rows: list[IssueStat]) -> dict:
    now = datetime.utcnow()
    month_ago = now - timedelta(days=30)
    lead = _vals(rows, "lead_days")
    action = _vals(rows, "first_action_days")
    open_now = [r for r in rows if r.is_open]
    return {
        "total": len(rows),
        "open": len(open_now),
        "new_30d": sum(1 for r in rows if r.created_at and r.created_at >= month_ago),
        "resolved_30d": sum(1 for r in rows
                            if r.resolved_at and r.resolved_at >= month_ago),
        "mean_lead": _mean(lead),
        "median_lead": _median(lead),
        "mean_first": _mean(action),
        "median_first": _median(action),
        "reopen_rate": (sum(1 for r in rows if r.reopened) / len(rows) * 100)
        if rows else 0.0,
        "lead_measured": len(lead),
        "action_measured": len(action),
        # 미해결 이슈가 지금까지 열려 있던 기간(오래 방치된 것을 보려고)
        "aging": sorted(
            (((now - r.created_at).days, r) for r in open_now if r.created_at),
            key=lambda x: -x[0],
        )[:10],
    }


def monthly(rows: list[IssueStat], months: int = 24) -> dict:
    """월별 생성 / 해결 건수. x 축은 이슈가 없는 달도 빠뜨리지 않는다."""
    keys = month_keys(months)
    created = Counter(r.created_at.strftime("%Y-%m") for r in rows if r.created_at)
    resolved = Counter(r.resolved_at.strftime("%Y-%m") for r in rows if r.resolved_at)
    return {
        "labels": keys,
        "created": [created.get(k, 0) for k in keys],
        "resolved": [resolved.get(k, 0) for k in keys],
    }


def month_keys(months: int) -> list[str]:
    now = datetime.utcnow().replace(day=1)
    keys: list[str] = []
    cur = now
    for _ in range(months):
        keys.append(cur.strftime("%Y-%m"))
        cur = (cur - timedelta(days=1)).replace(day=1)
    keys.reverse()
    return keys


def duration_hist(rows: list[IssueStat], field_name: str = "lead_days") -> dict:
    vals = _vals(rows, field_name)
    counts = [sum(1 for v in vals if lo <= v < hi) for lo, hi, _ in DURATION_BINS]
    return {"labels": [b[2] for b in DURATION_BINS], "counts": counts, "n": len(vals)}


def by_assignee(rows: list[IssueStat], top: int = 15) -> list[dict]:
    agg: dict[str, dict] = {}
    for r in rows:
        a = agg.setdefault(r.assignee, {"name": r.assignee, "total": 0, "open": 0,
                                        "resolved": 0, "_lead": [], "_act": []})
        a["total"] += 1
        if r.is_open:
            a["open"] += 1
        if r.resolved_at:
            a["resolved"] += 1
        if r.lead_days is not None and r.lead_days >= 0:
            a["_lead"].append(r.lead_days)
        if r.first_action_days is not None and r.first_action_days >= 0:
            a["_act"].append(r.first_action_days)
    out = []
    for a in agg.values():
        a["mean_lead"] = _mean(a.pop("_lead"))
        a["mean_action"] = _mean(a.pop("_act"))
        out.append(a)
    return sorted(out, key=lambda x: -x["total"])[:top]


def by_status(rows: list[IssueStat]) -> list[dict]:
    c = Counter(r.status or "(없음)" for r in rows)
    return [{"name": k, "value": v} for k, v in c.most_common()]


def by_tag(rows: list[IssueStat], top: int = 20) -> list[dict]:
    """제목 앞머리 태그([PM95], [VoC] 등) 분포. 제품·유형 흐름을 보는 용도.

    작성자마다 표기가 갈려서(Issue/ISSUE, VoC/VOC) 대소문자를 무시하고 묶고,
    표시는 가장 많이 쓰인 표기를 쓴다. 안 그러면 같은 태그가 둘로 쪼개져 보인다.
    """
    total: Counter[str] = Counter()      # 소문자 키 기준 합계
    spelling: dict[str, Counter[str]] = {}
    for r in rows:
        for t in r.tags:
            key = t.lower()
            total[key] += 1
            spelling.setdefault(key, Counter())[t] += 1
    return [{"name": spelling[k].most_common(1)[0][0], "value": v}
            for k, v in total.most_common(top)]


# ------------------------------------------------------- [Issue] 월별 구성 ---
def issue_tagged(rows: list[IssueStat]) -> list[IssueStat]:
    return [r for r in rows if r.is_issue_tagged]


@dataclass
class Composition:
    labels: list[str] = field(default_factory=list)          # 월
    groups: list[str] = field(default_factory=list)          # 묶음 이름
    series: dict[str, list[int]] = field(default_factory=dict)
    totals: list[tuple[str, int]] = field(default_factory=list)
    n: int = 0
    #: 묶음 이름 → 그 묶음에 들어간 이슈(표·드릴다운용)
    members: dict[str, list[IssueStat]] = field(default_factory=dict)


def composition(rows: list[IssueStat], *, basis: str = "target",
                months: int = 24, top: int = 10) -> Composition:
    """[Issue] 태그가 붙은 건이 달마다 어떤 형태였는지.

    묶음이 136종이라 전부 쌓으면 못 읽는다. 많이 나온 순으로 top 개만 남기고 나머지는
    '기타'로 몬다. 어떤 것이 기타에 들어갔는지는 아래 표에서 볼 수 있게 members 에
    그대로 담아 둔다.
    """
    src = issue_tagged(rows)
    keys = month_keys(months)
    in_range = [r for r in src
                if r.created_at and r.created_at.strftime("%Y-%m") in set(keys)]

    def key_of(r: IssueStat) -> str:
        return r.category if basis == "category" else r.target

    counts = Counter(key_of(r) for r in in_range)
    head = [k for k, _ in counts.most_common(top)]
    head_set = set(head)
    groups = head + (["기타"] if len(counts) > len(head) else [])

    series = {g: [0] * len(keys) for g in groups}
    members: dict[str, list[IssueStat]] = {g: [] for g in groups}
    idx = {k: i for i, k in enumerate(keys)}
    for r in in_range:
        g = key_of(r)
        g = g if g in head_set else "기타"
        series[g][idx[r.created_at.strftime("%Y-%m")]] += 1
        members[g].append(r)

    return Composition(
        labels=keys, groups=groups, series=series,
        totals=[(g, sum(series[g])) for g in groups],
        n=len(in_range), members=members,
    )


# ------------------------------------------------------------ 첫 반응 유형 ---
def by_first_action(rows: list[IssueStat]) -> list[dict]:
    """첫 반응이 무엇이었는지, 그 유형마다 얼마나 걸렸는지.

    코멘트만 세던 것을 '작성자 외 첫 이벤트'로 넓혔다. 담당만 배정하고 넘어간 건이
    171건, 필드만 고친 건이 46건인데 예전 기준으로는 전부 '반응 없음'이었다.
    """
    agg: dict[str, dict] = {}
    for r in rows:
        if not r.first_action_kind:
            continue
        a = agg.setdefault(r.first_action_kind,
                           {"key": r.first_action_kind,
                            "name": ACTION_KINDS.get(r.first_action_kind,
                                                     r.first_action_kind),
                            "value": 0, "_d": []})
        a["value"] += 1
        if r.first_action_days is not None and r.first_action_days >= 0:
            a["_d"].append(r.first_action_days)
    out = []
    for k in ACTION_ORDER:
        a = agg.get(k)
        if not a:
            continue
        d = a.pop("_d")
        a["mean_days"] = _mean(d)
        a["median_days"] = _median(d)
        out.append(a)
    return out


def no_action(rows: list[IssueStat]) -> list[IssueStat]:
    """작성자 말고는 아무도 손대지 않은 이슈. 첫 반응 통계의 분모 밖이라 따로 센다."""
    return [r for r in rows if not r.first_action_kind]
