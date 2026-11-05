"""Jinja2 템플릿 설정과 화면용 헬퍼 (날짜 포맷, 월별 잔디 달력 계산)."""

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from fastapi.templating import Jinja2Templates

from backend import __version__
from backend.integrations.lms_client import CATEGORY_LABELS
from backend.schemas.models import HeatmapDay
from backend.services.pet_service import with_ro

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
KST = dt.timezone(dt.timedelta(hours=9), name="KST")  # 한국은 서머타임이 없어 고정 오프셋으로 충분

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.autoescape = True  # 모든 템플릿에서 HTML 이스케이프 (XSS 방지)


def _to_datetime(value: dt.datetime | str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value) if isinstance(value, str) else value
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def kst_datetime(value: dt.datetime | str | None) -> str:
    if value is None:
        return ""
    local = _to_datetime(value).astimezone(KST)
    return f"{local.month}월 {local.day}일 {local:%H:%M}"


def relative_days(value: dt.datetime | str | None, now: dt.datetime | None = None) -> str:
    if value is None:
        return ""
    now = now or dt.datetime.now(dt.UTC)
    days = round((_to_datetime(value) - now).total_seconds() / 86_400)
    if days == 0:
        return "오늘"
    return f"{days}일 후" if days > 0 else f"{-days}일 지남"


# ---------------------------------------------------------------------
# 월별 잔디 달력: 한 달 = 일~토 7칸 × 주 단위 줄, ◀ ▶ 로 넘겨 본다
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class MonthCell:
    date: dt.date
    count: int
    level: int       # 0~4 → CSS 클래스 lv0~lv4
    outside: bool    # 기록 범위 밖 (아직 오지 않은 날 / 기록 시작 전)


@dataclass(frozen=True)
class MonthCalendar:
    key: str                              # 예: "2026-11"
    label: str                            # 예: "2026년 11월"
    weeks: list[list[MonthCell | None]]   # None = 다른 달 자리 (빈칸)
    active_days: int
    total_count: int


def build_month_calendars(days: list[HeatmapDay]) -> list[MonthCalendar]:
    """1년 치 학습 기록을 달별 달력으로 나눈다 (오래된 달 → 이번 달 순서)."""
    if not days:
        return []
    counts = {d.date: d.count for d in days}
    first, last = days[0].date, days[-1].date
    one_day = dt.timedelta(days=1)
    months: list[MonthCalendar] = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        start = dt.date(year, month, 1)
        end = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
        cells: list[MonthCell | None] = [None] * ((start.weekday() + 1) % 7)  # 일요일 시작
        active = total = 0
        day = start
        while day < end:
            if first <= day <= last:
                count = counts.get(day, 0)
                cells.append(MonthCell(day, count, min(count, 4), outside=False))
                active += count > 0
                total += count
            else:
                cells.append(MonthCell(day, 0, 0, outside=True))
            day += one_day
        cells += [None] * (-len(cells) % 7)
        weeks = [cells[i : i + 7] for i in range(0, len(cells), 7)]
        months.append(MonthCalendar(f"{year}-{month:02d}", f"{year}년 {month}월", weeks, active, total))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


templates.env.filters["kst"] = kst_datetime
templates.env.filters["with_ro"] = with_ro
templates.env.filters["relative_days"] = relative_days
templates.env.filters["category_label"] = lambda c: CATEGORY_LABELS.get(str(c), str(c))
templates.env.globals["app_version"] = __version__
templates.env.globals["build_month_calendars"] = build_month_calendars
