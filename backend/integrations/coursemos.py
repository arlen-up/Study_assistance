"""코스모스(Moodle) 어댑터: Moodle Web Services 응답 → 도우미 공통 형식.

가상 코스모스와 진짜 코스모스가 같은 Moodle 규격을 쓰므로, 이 어댑터는 그대로 재사용된다.
- 수강 중 과목: core_enrol_get_users_courses 에서 completed가 아니고 종료일이 지나지 않은 과목
- 과제: mod_assign_get_assignments + mod_assign_get_submission_status (제출한 과제는 제외)
- 강의 자료: mod_page_get_pages_by_courses + core_course_get_contents (섹션 번호 = 주차)
"""

import asyncio
import datetime as dt
import logging
import time

from backend.core.errors import AppError, NotFoundError
from backend.integrations.lms_models import LmsAssignment, LmsMaterial, LmsMaterialSummary, LmsSubmission
from backend.integrations.moodle_client import MoodleClient, MoodleCourse
from backend.services.document_service import html_to_text

logger = logging.getLogger(__name__)

OVERDUE_GRACE_SECONDS = 86_400  # 마감 후 하루까지는 '지남'으로 보여준다


def _from_epoch(value: int) -> dt.datetime | None:
    return dt.datetime.fromtimestamp(value, dt.UTC) if value else None


class CoursemosAdapter:
    def __init__(self, moodle: MoodleClient) -> None:
        self._moodle = moodle
        self._courses: list[MoodleCourse] | None = None

    async def courses(self) -> list[MoodleCourse]:
        if self._courses is None:
            site = await self._moodle.get_site_info()
            self._courses = await self._moodle.get_users_courses(site.userid)
        return self._courses

    async def current_courses(self) -> list[MoodleCourse]:
        now = time.time()
        return [c for c in await self.courses() if not c.completed and (not c.enddate or c.enddate >= now)]

    # ---------------- 과제 ----------------
    async def list_open_assignments(self) -> list[LmsAssignment]:
        current = await self.current_courses()
        if not current:
            return []
        data = await self._moodle.get_assignments([c.id for c in current])
        now = time.time()

        candidates = [
            (course, a)
            for course in data.courses
            for a in course.assignments
            if not (a.duedate or a.cutoffdate) or (a.duedate or a.cutoffdate) >= now - OVERDUE_GRACE_SECONDS
        ]
        statuses = await asyncio.gather(*(self._moodle.get_submission_status(a.id) for _, a in candidates))

        result: list[LmsAssignment] = []
        for (course, a), status in zip(candidates, statuses, strict=True):
            submission = status.lastattempt.submission if status.lastattempt else None
            if submission is not None and submission.status == "submitted":
                continue
            due = a.duedate or a.cutoffdate
            result.append(
                LmsAssignment(
                    id=str(a.id),
                    course_code=course.shortname,
                    course_name=course.fullname,
                    title=a.name,
                    description=html_to_text(a.intro or "") or None,
                    due_at=_from_epoch(due),
                    is_overdue=bool(due and due < now),
                )
            )
        far = dt.datetime.max.replace(tzinfo=dt.UTC)
        return sorted(result, key=lambda x: x.due_at or far)

    async def submit_assignment(self, assignment_id: str) -> LmsSubmission:
        if not assignment_id.isdigit():
            raise NotFoundError("과제를 찾을 수 없습니다.", code="lms_not_found")
        warnings = await self._moodle.submit_for_grading(int(assignment_id))
        if warnings:
            message = str(warnings[0].get("message") or "과제를 제출할 수 없습니다.")
            raise AppError(message, code="lms_submit_failed", status_code=409)
        return LmsSubmission(assignment_id=assignment_id, submitted_at=dt.datetime.now(dt.UTC))

    # ---------------- 강의 자료 ----------------
    async def _week_map(self, course_ids: list[int]) -> dict[int, int]:
        """{course module id: 섹션 번호(주차)}"""
        sections_per_course = await asyncio.gather(*(self._moodle.get_course_contents(cid) for cid in course_ids))
        week_of: dict[int, int] = {}
        for sections in sections_per_course:
            for section in sections:
                if section.section is None:
                    continue
                for module in section.modules:
                    week_of[module.id] = section.section
        return week_of

    async def _pages(self) -> list[tuple[MoodleCourse, LmsMaterialSummary, str]]:
        courses = {c.id: c for c in await self.courses()}
        if not courses:
            return []
        pages = (await self._moodle.get_pages_by_courses(list(courses))).pages
        week_of = await self._week_map(sorted({p.course for p in pages}))
        out: list[tuple[MoodleCourse, LmsMaterialSummary, str]] = []
        for page in pages:
            course = courses.get(page.course)
            if course is None:
                continue
            week = week_of.get(page.coursemodule)
            summary = LmsMaterialSummary(
                id=str(page.id),
                course_code=course.shortname,
                course_name=course.fullname,
                week_no=week if week and week > 0 else None,
                title=page.name,
                published_at=_from_epoch(page.timemodified),
            )
            out.append((course, summary, page.content))
        return out

    async def list_materials(self) -> list[LmsMaterialSummary]:
        items = [summary for _, summary, _ in await self._pages()]
        return sorted(items, key=lambda m: (m.course_code, m.week_no or 0, m.title))

    async def get_material(self, material_id: str) -> LmsMaterial:
        for _, summary, content in await self._pages():
            if summary.id == material_id:
                return LmsMaterial(**summary.model_dump(), content=html_to_text(content))
        raise NotFoundError("강의 자료를 찾을 수 없습니다.", code="lms_not_found")
