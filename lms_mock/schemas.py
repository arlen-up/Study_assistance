"""가상 LMS API 응답/요청 형식."""

import datetime as dt
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

CourseCategory = Literal["major_required", "major_elective", "general_required", "general_elective"]
CourseStatus = Literal["completed", "in_progress", "failed", "dropped"]


class LmsModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Student(LmsModel):
    id: UUID
    student_no: str
    name: str
    department: str
    admission_year: int


class Course(LmsModel):
    code: str
    name: str
    credits: int
    category: CourseCategory
    year_level: int
    skills: list[str] = Field(default_factory=list)
    description: str | None = None


class CourseList(LmsModel):
    items: list[Course]


class CourseRecord(LmsModel):
    course_code: str
    course_name: str
    category: CourseCategory
    year_level: int
    skills: list[str] = Field(default_factory=list)
    semester: str
    status: CourseStatus
    grade: str | None = None
    credits: int


class CreditSummary(LmsModel):
    category: CourseCategory
    earned_credits: int
    course_count: int


class Transcript(LmsModel):
    student: Student
    records: list[CourseRecord]
    credit_summary: list[CreditSummary]
    completed_credits: int
    in_progress_credits: int
    generated_at: dt.datetime


class GraduationRequirement(LmsModel):
    department: str
    total_credits: int
    major_required_credits: int
    major_elective_credits: int
    general_credits: int
    required_course_codes: list[str] = Field(default_factory=list)
    note: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def free_elective_credits(self) -> int:
        assigned = self.major_required_credits + self.major_elective_credits + self.general_credits
        return max(0, self.total_credits - assigned)


class DemoStudentRequest(LmsModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    student_no: str = Field(pattern=r"^[0-9]{8,10}$")
    name: str = Field(min_length=1, max_length=50)


class DemoStudentResponse(LmsModel):
    student_id: UUID


class WsToken(LmsModel):
    """Moodle(가상 코스모스) Web Service 토큰. 진짜 코스모스에서는 학교가 발급한 학생별 토큰에 해당."""

    token: str
    service: str = "moodle_mobile_app"


class LmsHealth(LmsModel):
    status: Literal["ok", "degraded"]
    configured: bool
    version: str
