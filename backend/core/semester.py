"""학기 주차 계산 — '저번주 자바 내용 알려줘' 같은 질문을 주차 번호로 바꾸기 위해 쓴다.

기준: 1주차 = 개강 주의 월요일(SEMESTER_START)부터 7일.
2026-2학기는 2026-08-31(월) 시작 (실제 e-class 자바 1주차 수업 9/2가 이 주에 들어감).
"""

import datetime as dt
import re
from dataclasses import dataclass

KST = dt.timezone(dt.timedelta(hours=9))
_WEEK_WORDS = (
    (re.compile(r"(저번|지난)\s*주"), -1),
    (re.compile(r"이번\s*주"), 0),
    (re.compile(r"다음\s*주"), 1),
)
_WEEK_NUMBER = re.compile(r"(\d{1,2})\s*주\s*차")
WEEKDAYS = "월화수목금토일"
SEMESTER_WEEKS = 16


@dataclass(frozen=True)
class SemesterWeek:
    number: int          # 1~16. 개강 전이면 0 이하, 종강 후면 17 이상
    start: dt.date
    end: dt.date

    @property
    def in_semester(self) -> bool:
        return 1 <= self.number <= SEMESTER_WEEKS

    def label(self) -> str:
        return f"{self.number}주차({self.start.month}/{self.start.day}~{self.end.month}/{self.end.day})"


def week_of(day: dt.date, semester_start: dt.date) -> SemesterWeek:
    monday = semester_start - dt.timedelta(days=semester_start.weekday())
    number = (day - monday).days // 7 + 1
    start = monday + dt.timedelta(weeks=number - 1)
    return SemesterWeek(number, start, start + dt.timedelta(days=6))


def today_kst() -> dt.date:
    return dt.datetime.now(KST).date()


def resolve_week(text: str, semester_start: dt.date, today: dt.date | None = None) -> int | None:
    """질문 속 '9주차' / '저번 주' / '이번 주' / '다음 주'를 수업 주차 번호로 바꾼다. 없으면 None."""
    if match := _WEEK_NUMBER.search(text):
        number = int(match.group(1))
        return number if 1 <= number <= SEMESTER_WEEKS else None
    today = today or today_kst()
    for pattern, offset in _WEEK_WORDS:
        if pattern.search(text):
            week = week_of(today + dt.timedelta(weeks=offset), semester_start)
            return week.number if week.in_semester else None
    return None


def describe(semester_label: str, semester_start: dt.date, today: dt.date | None = None) -> str:
    """AI에게 알려 줄 학기 달력 한 줄 요약."""
    today = today or today_kst()
    this = week_of(today, semester_start)
    date = f"{today:%Y-%m-%d}({WEEKDAYS[today.weekday()]})"
    if not this.in_semester:
        return f"오늘은 {date}이고 {semester_label} 수업 기간({SEMESTER_WEEKS}주)이 아니에요."
    last = week_of(today - dt.timedelta(days=7), semester_start)
    nxt = week_of(today + dt.timedelta(days=7), semester_start)
    parts = [f"오늘은 {date}, {semester_label} {this.label()}입니다."]
    if last.number >= 1:
        parts.append(f"'저번 주' = {last.label()}")
    if nxt.number <= SEMESTER_WEEKS:
        parts.append(f"'다음 주' = {nxt.label()}")
    return " ".join(parts)
