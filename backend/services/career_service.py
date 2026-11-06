"""커리어 나비게이터: 졸업 요건 분석 + 직무 트랙 추천.

원칙: "계산은 파이썬, 조언은 AI"
- 학점 계산·필수 과목·추천 후보는 이 모듈이 결정론적으로 계산한다 (AI 계산 실수·없는 과목 추천 방지).
- AI(CareerAdvisor)는 후보 목록 안에서만 과목을 골라 이유와 조언을 쓴다. 후보 밖 코드는 버린다.
- LMS 데이터는 LmsClient(HTTP API)로만 가져온다.
"""

import datetime as dt
import json
import logging
import math
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from supabase import AsyncClient

from backend.core.errors import AppError, BadRequestError, NotFoundError, UpstreamError
from backend.integrations.lms_client import (
    CATEGORY_LABELS,
    LmsClient,
    LmsCourse,
    LmsGraduationRequirement,
    LmsTranscript,
)
from backend.schemas.career import (
    CareerAdvice,
    CareerAdviceDraft,
    CareerAnalysis,
    CareerPlanItem,
    CareerReport,
    CareerTrackItem,
    CategoryProgress,
    CourseRef,
    CourseState,
    GraduationAnalysis,
    RecommendedCourse,
    TrackProgress,
)
from backend.services import lms_link_service

logger = logging.getLogger(__name__)

MAX_SEMESTER_CREDITS = 18
MAX_ELECTIVE_FILL = 4

REASON_TEXT: dict[str, str] = {
    "retake_required": "낙제한 필수 과목이에요. 재수강해야 졸업할 수 있어요.",
    "required_missing": "졸업에 꼭 필요한 필수 과목이에요.",
    "track_core": "관심 직무의 핵심 과목이에요.",
    "track_recommended": "관심 직무 준비에 도움이 되는 추천 과목이에요.",
    "elective_fill": "전공선택 학점을 채우면서 직무 기술도 익힐 수 있어요.",
}


# ---------------------------------------------------------------------
# 입력 데이터
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class CareerTrack:
    job_role: str
    title: str
    description: str | None
    core_course_codes: list[str]
    recommended_course_codes: list[str]
    key_skills: list[str]

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "CareerTrack":
        return cls(
            job_role=row["job_role"],
            title=row["title"],
            description=row.get("description"),
            core_course_codes=list(row.get("core_course_codes") or []),
            recommended_course_codes=list(row.get("recommended_course_codes") or []),
            key_skills=list(row.get("key_skills") or []),
        )


@dataclass(frozen=True)
class CareerInputs:
    transcript: LmsTranscript
    requirement: LmsGraduationRequirement
    catalog: list[LmsCourse]
    track: CareerTrack | None
    tracks: list[CareerTrack]
    target_job: str | None


async def list_tracks(db: AsyncClient) -> list[CareerTrack]:
    try:
        res = await db.table("career_tracks").select("*").order("job_role").execute()
    except Exception:
        logger.exception("career_tracks 조회 실패")
        raise UpstreamError("직무 트랙 정보를 불러오지 못했습니다.") from None
    return [CareerTrack.from_row(r) for r in res.data or []]


async def get_target_job(db: AsyncClient, user_id: UUID) -> str | None:
    try:
        res = await db.table("users").select("target_job").eq("id", str(user_id)).limit(1).execute()
    except Exception:
        logger.exception("target_job 조회 실패")
        raise UpstreamError("관심 직무를 불러오지 못했습니다.") from None
    return res.data[0].get("target_job") if res.data else None


async def set_target_job(db: AsyncClient, user_id: UUID, job_role: str) -> CareerTrack:
    tracks = {t.job_role: t for t in await list_tracks(db)}
    if job_role not in tracks:
        raise BadRequestError("알 수 없는 직무입니다.", code="unknown_job_role")
    try:
        await db.table("users").update({"target_job": job_role}).eq("id", str(user_id)).execute()
    except Exception:
        logger.exception("target_job 저장 실패")
        raise UpstreamError("관심 직무를 저장하지 못했습니다.") from None
    return tracks[job_role]


async def load_inputs(
    db: AsyncClient, lms: LmsClient, user_id: UUID, job_role: str | None = None
) -> CareerInputs:
    """LMS(성적표·졸업요건·과목)와 도우미(직무 트랙·관심 직무) 데이터를 모은다."""
    student_id = await lms_link_service.require_student_id(db, user_id, lms)
    transcript = await lms.get_transcript(student_id)
    requirement = await lms.get_graduation_requirement(transcript.student.department)
    catalog = await lms.list_courses()
    tracks = await list_tracks(db)
    target_job = await get_target_job(db, user_id)
    chosen = job_role or target_job
    track = next((t for t in tracks if t.job_role == chosen), None)
    if job_role and track is None:
        raise NotFoundError("알 수 없는 직무입니다.", code="unknown_job_role")
    return CareerInputs(transcript, requirement, catalog, track, tracks, target_job)


# ---------------------------------------------------------------------
# 분석 (순수 함수 — DB·AI 없이 테스트 가능)
# ---------------------------------------------------------------------
def _percent(earned: int, required: int) -> int:
    return 100 if required <= 0 else min(100, earned * 100 // required)


def analyze(inputs: CareerInputs) -> CareerAnalysis:
    t, req = inputs.transcript, inputs.requirement
    catalog = {c.code: c for c in inputs.catalog}
    for r in t.records:  # 카탈로그에 없는 과거 과목도 다룰 수 있게 보충
        catalog.setdefault(
            r.course_code,
            LmsCourse(code=r.course_code, name=r.course_name, credits=r.credits, category=r.category,
                      year_level=r.year_level, skills=r.skills),
        )

    completed = {r.course_code for r in t.records if r.status == "completed"}
    in_progress = {r.course_code for r in t.records if r.status == "in_progress"}
    failed = {r.course_code for r in t.records if r.status == "failed"} - completed
    taken = completed | in_progress

    def state(code: str) -> CourseState:
        if code in completed:
            return "completed"
        if code in in_progress:
            return "in_progress"
        if code in failed:
            return "failed"
        return "not_taken"

    def ref(code: str) -> CourseRef | None:
        course = catalog.get(code)
        if course is None:
            return None
        return CourseRef(code=code, name=course.name, credits=course.credits, category=course.category,
                         year_level=course.year_level, state=state(code))

    # ---- 이수 구분별 학점 ----
    def credits_of(status: str, cats: tuple[str, ...]) -> int:
        return sum(r.credits for r in t.records if r.status == status and r.category in cats)

    groups: list[tuple[str, tuple[str, ...], int]] = [
        ("general", ("general_required", "general_elective"), req.general_credits),
        ("major_required", ("major_required",), req.major_required_credits),
        ("major_elective", ("major_elective",), req.major_elective_credits),
    ]
    labels = {
        "general": "교양",
        "major_required": "전공필수",
        "major_elective": "전공선택",
        "free_elective": "자유선택",
    }

    categories: list[CategoryProgress] = []
    overflow = overflow_projected = 0
    for key, cats, required in groups:
        earned = credits_of("completed", cats)
        current = credits_of("in_progress", cats)
        overflow += max(0, earned - required)
        overflow_projected += max(0, earned + current - required)
        categories.append(CategoryProgress(
            key=key, label=labels[key], required=required, earned=earned, in_progress=current,  # type: ignore[arg-type]
            remaining=max(0, required - earned),
            remaining_after_current=max(0, required - earned - current),
            percent=_percent(earned, required),
        ))
    # 자유선택: 각 구분 요건을 넘긴 학점이 자유선택으로 인정된다
    free_req = req.free_elective_credits
    categories.append(CategoryProgress(
        key="free_elective", label=labels["free_elective"], required=free_req,
        earned=min(overflow, free_req), in_progress=max(0, min(overflow_projected, free_req) - min(overflow, free_req)),
        remaining=max(0, free_req - overflow), remaining_after_current=max(0, free_req - overflow_projected),
        percent=_percent(min(overflow, free_req), free_req),
    ))

    remaining_total = max(0, req.total_credits - t.completed_credits)
    remaining_after = max(0, req.total_credits - t.completed_credits - t.in_progress_credits)
    remaining_after = max(remaining_after, sum(c.remaining_after_current for c in categories))

    required_missing = [r for code in req.required_course_codes if code not in completed and (r := ref(code))]
    retake_needed = [r for code in sorted(failed) if code not in in_progress and (r := ref(code))]

    graduation = GraduationAnalysis(
        student_name=t.student.name,
        department=t.student.department,
        total_required=req.total_credits,
        completed_credits=t.completed_credits,
        in_progress_credits=t.in_progress_credits,
        remaining_credits=remaining_total,
        remaining_after_current=remaining_after,
        estimated_semesters_left=math.ceil(remaining_after / MAX_SEMESTER_CREDITS) if remaining_after else 0,
        graduation_ready=remaining_total == 0 and not required_missing,
        categories=categories,
        required_missing=required_missing,
        retake_needed=retake_needed,
    )

    # ---- 직무 트랙 ----
    track_progress: TrackProgress | None = None
    if inputs.track is not None:
        tr = inputs.track
        core = [r for c in tr.core_course_codes if (r := ref(c))]
        recommended = [r for c in tr.recommended_course_codes if (r := ref(c))]
        learned = {s.lower() for code in taken if code in catalog for s in catalog[code].skills}
        matched = [s for s in tr.key_skills if s.lower() in learned]
        core_done = sum(1 for r in core if r.state == "completed")
        track_progress = TrackProgress(
            job_role=tr.job_role, title=tr.title, description=tr.description,
            core=core, recommended=recommended, core_completed=core_done, core_total=len(core),
            coverage_percent=_percent(core_done, len(core)),
            key_skills=tr.key_skills, matched_skills=matched,
            missing_skills=[s for s in tr.key_skills if s not in matched],
        )

    # ---- 다음 학기 추천 후보 (우선순위) ----
    picked: dict[str, RecommendedCourse] = {}

    def add(code: str, priority: int, reason_code: str) -> None:
        course = catalog.get(code)
        if course is None or code in taken or code in picked:
            return
        picked[code] = RecommendedCourse(
            code=code, name=course.name, credits=course.credits, category=course.category,
            year_level=course.year_level, priority=priority, reason_code=reason_code,  # type: ignore[arg-type]
            reason=REASON_TEXT[reason_code],
        )

    for code in req.required_course_codes:
        if code in failed:
            add(code, 1, "retake_required")
    for code in sorted(req.required_course_codes, key=lambda c: catalog[c].year_level if c in catalog else 9):
        add(code, 2, "required_missing")
    if inputs.track is not None:
        for code in inputs.track.core_course_codes:
            add(code, 3, "track_core")
        for code in inputs.track.recommended_course_codes:
            add(code, 4, "track_recommended")

    elective_gap = next(c.remaining_after_current for c in categories if c.key == "major_elective")
    planned_elective = sum(p.credits for p in picked.values() if p.category == "major_elective")
    if elective_gap > planned_elective:
        wanted = {s.lower() for s in (inputs.track.key_skills if inputs.track else [])}
        electives = sorted(
            (c for c in catalog.values() if c.category == "major_elective" and c.code not in taken),
            key=lambda c: (-len(wanted & {s.lower() for s in c.skills}), c.year_level, c.code),
        )
        for course in electives[:MAX_ELECTIVE_FILL]:
            add(course.code, 5, "elective_fill")

    candidates = sorted(picked.values(), key=lambda c: (c.priority, c.year_level, c.code))
    default_plan: list[RecommendedCourse] = []
    plan_credits = 0
    for c in candidates:
        if plan_credits + c.credits <= MAX_SEMESTER_CREDITS:
            default_plan.append(c)
            plan_credits += c.credits

    return CareerAnalysis(
        graduation=graduation,
        track=track_progress,
        candidates=candidates,
        default_plan=default_plan,
        target_job=inputs.target_job,
    )


# ---------------------------------------------------------------------
# AI 조언 (후보 검증)
# ---------------------------------------------------------------------
class CareerAdvisor(Protocol):
    """AgentService가 구현한다 (순환 import를 피하려고 Protocol로 받음)."""

    @property
    def model_name(self) -> str: ...

    async def write_career_advice(self, analysis: CareerAnalysis) -> CareerAdviceDraft: ...


def finalize_advice(draft: CareerAdviceDraft, analysis: CareerAnalysis, model_name: str) -> CareerAdvice:
    """AI가 고른 과목을 후보 목록으로 검증하고, 18학점을 넘지 않게 자른다."""
    candidates = {c.code: c for c in analysis.candidates}
    plan: list[CareerPlanItem] = []
    credits = 0
    seen: set[str] = set()
    for item in draft.semester_plan:
        code = item.course_code.strip().upper()
        course = candidates.get(code)
        if course is None or code in seen:
            logger.info("AI가 후보 밖 과목을 추천해 제외: %s", code)
            continue
        if credits + course.credits > MAX_SEMESTER_CREDITS:
            break
        plan.append(CareerPlanItem(course=course, ai_reason=item.reason.strip()))
        credits += course.credits
        seen.add(code)

    if not plan:  # AI 추천이 모두 무효하면 기본 추천으로 대체
        plan = [CareerPlanItem(course=c, ai_reason=c.reason) for c in analysis.default_plan]
        credits = sum(p.course.credits for p in plan)

    return CareerAdvice(
        headline=draft.headline.strip(),
        summary=draft.summary.strip(),
        semester_plan=plan,
        plan_credits=credits,
        skill_tips=[t.strip() for t in draft.skill_tips if t.strip()],
        encouragement=draft.encouragement.strip(),
        model_name=model_name,
    )


async def build_report(
    db: AsyncClient,
    lms: LmsClient,
    user_id: UUID,
    advisor: CareerAdvisor | None,
    job_role: str | None = None,
) -> CareerReport:
    inputs = await load_inputs(db, lms, user_id, job_role)
    analysis = analyze(inputs)
    advice: CareerAdvice | None = None
    advice_error: str | None = None
    if advisor is not None:
        try:
            draft = await advisor.write_career_advice(analysis)
            advice = finalize_advice(draft, analysis, advisor.model_name)
        except AppError as exc:
            # AI가 실패해도 계산 결과와 기본 추천은 보여준다
            advice_error = f"AI 조언을 만들지 못했어요: {exc.message}"
    return CareerReport(
        analysis=analysis,
        advice=advice,
        advice_error=advice_error,
        tracks=[CareerTrackItem(job_role=t.job_role, title=t.title, description=t.description) for t in inputs.tracks],
        generated_at=dt.datetime.now(dt.UTC),
    )


def category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category)


def compact_json(analysis: CareerAnalysis) -> str:
    """AI 컨텍스트용으로 필요한 값만 추린 짧은 JSON."""
    g = analysis.graduation
    data: dict[str, Any] = {
        "graduation": {
            "total_required": g.total_required,
            "completed": g.completed_credits,
            "in_progress": g.in_progress_credits,
            "remaining_after_current": g.remaining_after_current,
            "estimated_semesters_left": g.estimated_semesters_left,
            "categories": [
                {"label": c.label, "required": c.required, "earned": c.earned,
                 "in_progress": c.in_progress, "remaining_after_current": c.remaining_after_current}
                for c in g.categories
            ],
            "required_missing": [f"{r.code} {r.name} ({r.state})" for r in g.required_missing],
            "retake_needed": [f"{r.code} {r.name}" for r in g.retake_needed],
        },
        "candidates": [
            {"course_code": c.code, "name": c.name, "credits": c.credits, "priority": c.priority, "why": c.reason}
            for c in analysis.candidates
        ],
    }
    if analysis.track:
        t = analysis.track
        data["track"] = {
            "title": t.title,
            "core_progress": f"{t.core_completed}/{t.core_total}",
            "core": [f"{r.code} {r.name} ({r.state})" for r in t.core],
            "matched_skills": t.matched_skills,
            "missing_skills": t.missing_skills,
        }
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
