"""도우미가 쓰는 LMS·학사정보 공통 형식.

학교 시스템마다 형식이 달라도 어댑터가 이 형식으로 바꿔 주므로, 도우미의 나머지 코드는 바뀌지 않는다.
ID는 시스템마다 숫자(Moodle)·UUID 등 다르므로 문자열로 다룬다.
"""

import datetime as dt
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CourseCategory = Literal["major_required", "major_elective", "general_required", "general_elective"]
CourseStatus = Literal["completed", "in_progress", "failed", "dropped"]

CATEGORY_LABELS: dict[str, str] = {
    "major_required": "전공필수",
    "major_elective": "전공선택",
    "general_required": "교양필수",
    "general_elective": "교양선택",
}


class _LmsModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ---------------- 🏛 학사정보 ----------------
class LmsStudent(_LmsModel):
    id: UUID
    student_no: str
    name: str
    department: str
    admission_year: int


class LmsCourse(_LmsModel):
    code: str
    name: str
    credits: int
    category: CourseCategory
    year_level: int
    skills: list[str] = Field(default_factory=list)
    description: str | None = None


class LmsCourseRecord(_LmsModel):
    course_code: str
    course_name: str
    category: CourseCategory
    year_level: int
    skills: list[str] = Field(default_factory=list)
    semester: str
    status: CourseStatus
    grade: str | None = None
    credits: int


class LmsCreditSummary(_LmsModel):
    category: CourseCategory
    earned_credits: int
    course_count: int


class LmsTranscript(_LmsModel):
    student: LmsStudent
    records: list[LmsCourseRecord]
    credit_summary: list[LmsCreditSummary]
    completed_credits: int
    in_progress_credits: int
    generated_at: dt.datetime


class LmsGraduationRequirement(_LmsModel):
    department: str
    total_credits: int
    major_required_credits: int
    major_elective_credits: int
    general_credits: int
    free_elective_credits: int
    required_course_codes: list[str] = Field(default_factory=list)
    note: str | None = None


# ---------------- 🏫 LMS (코스모스) ----------------
class LmsAssignment(_LmsModel):
    id: str
    course_code: str
    course_name: str
    material_id: str | None = None
    title: str
    description: str | None = None
    due_at: dt.datetime | None = None   # 마감일이 없는 과제도 있다
    is_overdue: bool = False


class LmsMaterialSummary(_LmsModel):
    id: str
    course_code: str
    course_name: str
    week_no: int | None = None          # 주차를 알 수 없으면 None
    title: str
    published_at: dt.datetime | None = None


class LmsMaterial(LmsMaterialSummary):
    content: str


class LmsSubmission(_LmsModel):
    assignment_id: str
    submitted_at: dt.datetime
