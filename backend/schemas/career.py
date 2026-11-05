"""커리어 나비게이터 (졸업 요건 분석 + 직무 트랙 추천) 모델."""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

from backend.schemas.models import RequestModel

ProgressKey = Literal["general", "major_required", "major_elective", "free_elective"]
CourseState = Literal["completed", "in_progress", "failed", "not_taken"]
ReasonCode = Literal["retake_required", "required_missing", "track_core", "track_recommended", "elective_fill"]


class CareerTrackItem(BaseModel):
    job_role: str
    title: str
    description: str | None = None


class CareerTargetRequest(RequestModel):
    job_role: str = Field(pattern=r"^[a-z_]{2,30}$")


class CategoryProgress(BaseModel):
    key: ProgressKey
    label: str
    required: int
    earned: int
    in_progress: int
    remaining: int            # 이번 학기 수강 중 과목 제외
    remaining_after_current: int  # 수강 중 과목을 모두 이수한다고 가정
    percent: int


class CourseRef(BaseModel):
    code: str
    name: str
    credits: int
    category: str
    year_level: int
    state: CourseState


class GraduationAnalysis(BaseModel):
    student_name: str
    department: str
    total_required: int
    completed_credits: int
    in_progress_credits: int
    remaining_credits: int
    remaining_after_current: int
    estimated_semesters_left: int
    graduation_ready: bool
    categories: list[CategoryProgress]
    required_missing: list[CourseRef]   # 아직 이수하지 않은 필수 과목 (수강 중 포함)
    retake_needed: list[CourseRef]      # 낙제 후 아직 재수강하지 않은 과목


class TrackProgress(BaseModel):
    job_role: str
    title: str
    description: str | None = None
    core: list[CourseRef]
    recommended: list[CourseRef]
    core_completed: int
    core_total: int
    coverage_percent: int
    key_skills: list[str]
    matched_skills: list[str]
    missing_skills: list[str]


class RecommendedCourse(BaseModel):
    code: str
    name: str
    credits: int
    category: str
    year_level: int
    priority: int = Field(ge=1, le=5)
    reason_code: ReasonCode
    reason: str


class CareerPlanItemDraft(BaseModel):
    course_code: str = Field(description="반드시 후보 목록(candidates)에 있는 과목 코드")
    reason: str = Field(description="이 학생에게 이 과목이 필요한 이유 (1~2문장, 한국어)")


class CareerAdviceDraft(BaseModel):
    """LLM 구조화 출력: 커리어 조언."""

    headline: str = Field(description="한 줄 요약 (30자 내외)")
    summary: str = Field(description="졸업 요건과 직무 준비 상태를 쉬운 말로 3~4문장 요약")
    semester_plan: list[CareerPlanItemDraft] = Field(
        min_length=1, max_length=6, description="다음 학기 추천 과목 (총 18학점 이내, 후보 목록에서만 선택)"
    )
    skill_tips: list[str] = Field(min_length=1, max_length=4, description="수업 외에 준비하면 좋은 활동 (각 1문장)")
    encouragement: str = Field(description="짧은 응원 한마디")


class CareerPlanItem(BaseModel):
    course: RecommendedCourse
    ai_reason: str


class CareerAdvice(BaseModel):
    headline: str
    summary: str
    semester_plan: list[CareerPlanItem]
    plan_credits: int
    skill_tips: list[str]
    encouragement: str
    model_name: str


class CareerAnalysis(BaseModel):
    """AI 없이 계산한 분석 결과 (MCP 도구·API 공용)."""

    graduation: GraduationAnalysis
    track: TrackProgress | None = None
    candidates: list[RecommendedCourse]
    default_plan: list[RecommendedCourse]   # AI가 없을 때 쓰는 기본 추천 (18학점 이내)
    target_job: str | None = None


class CareerReport(BaseModel):
    analysis: CareerAnalysis
    advice: CareerAdvice | None = None
    advice_error: str | None = None
    tracks: list[CareerTrackItem]
    generated_at: dt.datetime
