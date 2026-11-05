"""📊 대시보드 그래프용 숫자 계산 — "계산은 파이썬, 그리기는 템플릿(SVG)".

학습 기록(study_logs)으로 만든다.
- 주간 학습 횟수 (최근 12주, 이번 주 강조)
- 주간 정답률 (최근 8주, 꺾은선 — 퀴즈는 맞힌 문제, 카드는 '알아요'로 표시한 비율)
- 과목별 학습 비중 (최근 12주, 순위 막대)
- 복습 현황 (오늘 복습 / 예정 / 완료, 한 줄 막대)
그래프 좌표도 여기서 계산하고, 템플릿은 그리기만 한다.
"""

import datetime as dt
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from supabase import AsyncClient

from backend.schemas.models import DashboardResponse

logger = logging.getLogger(__name__)

KST = dt.timezone(dt.timedelta(hours=9))
WEEKS = 12
ACCURACY_WEEKS = 8
TOP_COURSES = 5

# SVG 그리기 영역 (viewBox 기준, 화면 폭에 맞춰 늘어난다)
CHART_W, CHART_H = 560, 190
PAD_L, PAD_R, PAD_T, PAD_B = 34, 10, 16, 26
BAR_MAX_W = 24
RADIUS = 4


@dataclass(frozen=True)
class Tick:
    value: int
    y: float
    label: str


@dataclass(frozen=True)
class WeekBar:
    label: str            # 예: "11/2"
    range_label: str      # 예: "11/2~11/8"
    count: int
    x: float
    width: float
    y: float
    height: float
    path: str             # 위쪽 모서리만 둥근 막대 (아래는 기준선에 붙음)
    hit_x: float
    hit_w: float
    current: bool


@dataclass(frozen=True)
class AccuracyPoint:
    label: str
    range_label: str
    pct: int | None
    quizzes: int
    x: float
    y: float | None


@dataclass(frozen=True)
class CourseShare:
    name: str
    count: int
    pct: int
    width_pct: float


@dataclass(frozen=True)
class ReviewPart:
    key: str
    label: str
    icon: str
    count: int
    pct: float


@dataclass
class Insights:
    weekly: list[WeekBar] = field(default_factory=list)
    weekly_ticks: list[Tick] = field(default_factory=list)
    this_week: int = 0
    last_week: int = 0
    accuracy: list[AccuracyPoint] = field(default_factory=list)
    accuracy_paths: list[str] = field(default_factory=list)
    accuracy_ticks: list[Tick] = field(default_factory=list)
    accuracy_recent: int | None = None     # 최근 4주 정답률
    accuracy_before: int | None = None     # 그 전 4주 정답률
    courses: list[CourseShare] = field(default_factory=list)
    course_total: int = 0
    reviews: list[ReviewPart] = field(default_factory=list)
    review_total: int = 0
    current_streak: int = 0
    longest_streak: int = 0
    streak_goal: int = 7
    streak_pct: int = 0
    chart_w: int = CHART_W
    chart_h: int = CHART_H
    baseline: float = CHART_H - PAD_B
    plot_left: float = PAD_L
    plot_right: float = CHART_W - PAD_R

    @property
    def week_delta(self) -> int:
        return self.this_week - self.last_week

    @property
    def accuracy_delta(self) -> int | None:
        if self.accuracy_recent is None or self.accuracy_before is None:
            return None
        return self.accuracy_recent - self.accuracy_before


def _nice_step(max_value: int, max_ticks: int = 4) -> int:
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500):
        if max_value / step <= max_ticks:
            return step
    return 1000


def _bar_path(x: float, y: float, w: float, base: float) -> str:
    """위쪽 두 모서리만 4px 둥글고, 아래는 기준선에 네모나게 붙은 막대."""
    h = base - y
    r = min(RADIUS, w / 2, h)
    if h <= 0:
        return ""
    return (
        f"M{x:.1f},{base:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
        f"H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} V{base:.1f} Z"
    )


def _week_start(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def _label(day: dt.date) -> str:
    return f"{day.month}/{day.day}"


def _range(start: dt.date) -> str:
    end = start + dt.timedelta(days=6)
    return f"{start.month}/{start.day}~{end.month}/{end.day}"


async def _load_logs(db: AsyncClient, user_id: UUID, since: dt.datetime) -> list[dict[str, Any]]:
    try:
        res = (
            await db.table("study_logs")
            .select("studied_at,score,total_questions,quiz_id,documents(course_name)")
            .eq("user_id", str(user_id))
            .gte("studied_at", since.isoformat())
            .order("studied_at")
            .limit(3000)
            .execute()
        )
    except Exception:
        logger.exception("대시보드 학습 기록 조회 실패 — 빈 그래프로 표시")
        return []
    return res.data or []


def build(logs: list[dict[str, Any]], dashboard: DashboardResponse, today: dt.date) -> Insights:
    ins = Insights()
    base = CHART_H - PAD_B
    plot_w = CHART_W - PAD_L - PAD_R
    plot_h = base - PAD_T
    this_monday = _week_start(today)
    weeks = [this_monday - dt.timedelta(weeks=WEEKS - 1 - i) for i in range(WEEKS)]

    counts: Counter[dt.date] = Counter()
    quiz_right: defaultdict[dt.date, int] = defaultdict(int)
    quiz_total: defaultdict[dt.date, int] = defaultdict(int)
    quiz_count: Counter[dt.date] = Counter()
    courses: Counter[str] = Counter()
    for row in logs:
        studied = dt.datetime.fromisoformat(str(row["studied_at"])).astimezone(KST).date()
        week = _week_start(studied)
        if week < weeks[0]:
            continue
        counts[week] += 1
        doc = row.get("documents") or {}
        courses[(doc.get("course_name") or "과목 미지정").strip() or "과목 미지정"] += 1
        if row.get("total_questions"):  # 퀴즈(맞힌 문제)·카드(안다고 한 카드) 모두 '맞힌 비율'로 본다
            quiz_right[week] += int(row.get("score") or 0)
            quiz_total[week] += int(row["total_questions"])
            quiz_count[week] += 1

    # 1) 주간 학습 횟수 (세로 막대)
    peak = max([counts[w] for w in weeks] + [1])
    step = _nice_step(peak)
    top = step * -(-peak // step)
    band = plot_w / WEEKS
    bar_w = min(BAR_MAX_W, band * 0.6)
    for i, week in enumerate(weeks):
        value = counts[week]
        x = PAD_L + band * i + (band - bar_w) / 2
        y = base - plot_h * value / top
        ins.weekly.append(
            WeekBar(
                label=_label(week), range_label=_range(week), count=value, x=round(x, 1), width=round(bar_w, 1),
                y=round(y, 1), height=round(base - y, 1), path=_bar_path(x, y, bar_w, base),
                hit_x=round(PAD_L + band * i, 1), hit_w=round(band, 1), current=week == this_monday,
            )
        )
    ins.weekly_ticks = [Tick(v, round(base - plot_h * v / top, 1), str(v)) for v in range(0, top + 1, step)]
    ins.this_week = counts[weeks[-1]]
    ins.last_week = counts[weeks[-2]]

    # 2) 주간 퀴즈 정답률 (꺾은선, 퀴즈가 없던 주는 선을 끊는다)
    acc_weeks = weeks[-ACCURACY_WEEKS:]
    a_band = plot_w / ACCURACY_WEEKS
    segment: list[str] = []
    for i, week in enumerate(acc_weeks):
        x = PAD_L + a_band * i + a_band / 2
        pct = round(100 * quiz_right[week] / quiz_total[week]) if quiz_total[week] else None
        y = round(base - plot_h * pct / 100, 1) if pct is not None else None
        ins.accuracy.append(AccuracyPoint(_label(week), _range(week), pct, quiz_count[week], round(x, 1), y))
        if y is None:
            if len(segment) > 1:
                ins.accuracy_paths.append(" ".join(segment))
            segment = []
        else:
            segment.append(f"{'M' if not segment else 'L'}{x:.1f},{y:.1f}")
    if len(segment) > 1:
        ins.accuracy_paths.append(" ".join(segment))
    ins.accuracy_ticks = [Tick(v, round(base - plot_h * v / 100, 1), f"{v}%") for v in (0, 50, 100)]

    def rate(span: list[dt.date]) -> int | None:
        total = sum(quiz_total[w] for w in span)
        return round(100 * sum(quiz_right[w] for w in span) / total) if total else None

    ins.accuracy_recent = rate(weeks[-4:])
    ins.accuracy_before = rate(weeks[-8:-4])

    # 3) 과목별 학습 비중 (순위 막대, 많은 순, 나머지는 '기타')
    ranked = courses.most_common()
    if len(ranked) > TOP_COURSES:
        rest = sum(c for _, c in ranked[TOP_COURSES - 1 :])
        ranked = [*ranked[: TOP_COURSES - 1], ("기타", rest)]
    total = sum(c for _, c in ranked)
    widest = max([c for _, c in ranked] + [1])
    ins.course_total = total
    ins.courses = [
        CourseShare(name, count, round(100 * count / total) if total else 0, round(100 * count / widest, 1))
        for name, count in ranked
    ]

    # 4) 복습 현황 (한 줄로 나눈 막대)
    r = dashboard.reviews
    parts = [("due", "오늘 복습", "🔔", len(r.due)), ("upcoming", "복습 예정", "⏰", len(r.upcoming)),
             ("mastered", "복습 완료", "✅", len(r.mastered))]
    ins.review_total = sum(p[3] for p in parts)
    ins.reviews = [
        ReviewPart(key, label, icon, count, round(100 * count / ins.review_total, 1) if ins.review_total else 0)
        for key, label, icon, count in parts
    ]

    # 연속 학습 미터: 최장 기록(최소 7일)을 목표로
    s = dashboard.streak
    ins.current_streak, ins.longest_streak = s.current_streak, s.longest_streak
    ins.streak_goal = max(7, s.longest_streak)
    ins.streak_pct = min(100, round(100 * s.current_streak / ins.streak_goal))
    return ins


async def get_insights(db: AsyncClient, user_id: UUID, dashboard: DashboardResponse) -> Insights:
    today = dt.datetime.now(KST).date()
    since = dt.datetime.combine(_week_start(today) - dt.timedelta(weeks=WEEKS - 1), dt.time(), KST)
    return build(await _load_logs(db, user_id, since), dashboard, today)
