"""Jinja2 템플릿 설정과 화면용 헬퍼 (날짜 포맷, 잔디 격자 계산)."""

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from fastapi.templating import Jinja2Templates

from backend import __version__
from backend.schemas.models import HeatmapDay

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
# 잔디 격자: 열 = 주(일~토), 행 = 요일
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class HeatmapCell:
    date: dt.date
    count: int
    level: int  # 0~4 → CSS 클래스 lv0~lv4


@dataclass(frozen=True)
class HeatmapWeek:
    month_label: str
    cells: list[HeatmapCell | None]


def build_heatmap(days: list[HeatmapDay]) -> list[HeatmapWeek]:
    if not days:
        return []
    leading = (days[0].date.weekday() + 1) % 7  # Python 월=0 → 일요일 시작 기준으로 변환
    cells: list[HeatmapCell | None] = [None] * leading
    cells += [HeatmapCell(d.date, d.count, min(d.count, 4)) for d in days]

    weeks: list[HeatmapWeek] = []
    for start in range(0, len(cells), 7):
        chunk = cells[start : start + 7]
        first = next((c for c in chunk if c is not None), None)
        label = ""
        if first is not None and (first.date.day <= 7 or start == 0):
            label = f"{first.date.month}월"
        weeks.append(HeatmapWeek(month_label=label, cells=chunk))
    return weeks


templates.env.filters["kst"] = kst_datetime
templates.env.filters["relative_days"] = relative_days
templates.env.globals["app_version"] = __version__
templates.env.globals["build_heatmap"] = build_heatmap
