"""가상 코스모스 — Moodle Web Services REST 호환 엔드포인트.

규격 출처: Moodle MOODLE_405_STABLE(4.5 LTS) 소스의 *_parameters / *_returns 정의
  - webservice/rest/locallib.php : GET/POST 파라미터 병합, JSON 오류 {exception, errorcode, message}, HTTP 200
  - enrol/externallib.php        : core_enrol_get_users_courses
  - mod/assign/externallib.php   : mod_assign_get_assignments / get_submission_status / submit_for_grading
  - mod/page/classes/external.php: mod_page_get_pages_by_courses
  - course/externallib.php       : core_course_get_contents
  - grade/report/overview/...    : gradereport_overview_get_course_grades
  - webservice/externallib.php   : core_webservice_get_site_info

경로: /lms/webservice/rest/server.php  (진짜 코스모스로 바꿀 때는 도우미의 MOODLE_BASE_URL만 교체)
토큰: 학생마다 고정된 32자리 토큰 (가상 학사정보 API의 /api/students/{id}/ws-token 으로 발급).
"""

import datetime as dt
import hashlib
import hmac
import html
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from supabase import AsyncClient

from lms_mock import repository
from lms_mock.auth import LmsSettingsDep
from lms_mock.config import LmsSettings
from lms_mock.errors import LmsError
from lms_mock.repository import LmsDbDep
from lms_mock.schemas import CourseRecord, Transcript

logger = logging.getLogger("lms_mock.moodle")

SITE_NAME = "가상 코스모스 (Moodle 4.5 호환)"
SITE_URL = "https://coursemos.mock"
MOODLE_VERSION = "2024100700"  # Moodle 4.5.0
MOODLE_RELEASE = "4.5 (가상 코스모스)"
KST = dt.timezone(dt.timedelta(hours=9))
GRADE_POINTS = {
    "A+": "4.5",
    "A0": "4.0",
    "B+": "3.5",
    "B0": "3.0",
    "C+": "2.5",
    "C0": "2.0",
    "D+": "1.5",
    "D0": "1.0",
    "F": "0.0",
    "P": "",
    "NP": "",
}

router = APIRouter()


class MoodleError(Exception):
    """Moodle 형식 오류. REST 서버는 HTTP 200 + JSON 본문으로 돌려준다 (Moodle과 동일)."""

    def __init__(self, errorcode: str, message: str, exception: str = "moodle_exception") -> None:
        super().__init__(message)
        self.errorcode = errorcode
        self.message = message
        self.exception = exception


# ---------------------------------------------------------------------
# ID 변환 (Moodle은 정수 ID, 가상 LMS DB는 코드/UUID)
# ---------------------------------------------------------------------
def course_int(code: str) -> int:
    return int(hashlib.sha256(f"course:{code}".encode()).hexdigest()[:7], 16) + 1


def uuid_int(value: str | UUID, kind: str) -> int:
    digest = hashlib.sha256(f"{kind}:{value}".encode()).hexdigest()
    return int(digest[:8], 16) % 2_000_000_000 + 1


def ws_token(settings: LmsSettings, student_id: UUID) -> str:
    if settings.lms_api_key is None:
        raise LmsError(503, "lms_not_configured", "가상 LMS 연동 키(LMS_API_KEY)가 설정되지 않았습니다.")
    key = settings.lms_api_key.get_secret_value().encode()
    return hmac.new(key, f"moodle-ws:{student_id}".encode(), hashlib.sha256).hexdigest()[:32]


def _epoch(value: str | dt.datetime | None) -> int:
    if value is None:
        return 0
    parsed = dt.datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return int(parsed.timestamp())


def _semester_range(semester: str) -> tuple[int, int]:
    """'2026-2' → (2026-09-01, +16주) KST 기준 Unix 시간."""
    year, term = semester.split("-")
    month = {"1": 3, "2": 9, "S": 6, "W": 12}.get(term, 3)
    start = dt.datetime(int(year), month, 1, tzinfo=KST)
    weeks = 16 if term in ("1", "2") else 6
    return int(start.timestamp()), int((start + dt.timedelta(weeks=weeks)).timestamp())


def _html(text: str) -> str:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs) or "<p></p>"


# ---------------------------------------------------------------------
# 요청 컨텍스트
# ---------------------------------------------------------------------
class MoodleContext:
    def __init__(self, db: AsyncClient, student_id: UUID) -> None:
        self.db = db
        self.student_id = student_id
        self.userid = uuid_int(student_id, "user")
        self._transcript: Transcript | None = None

    async def transcript(self) -> Transcript:
        if self._transcript is None:
            self._transcript = await repository.get_transcript(self.db, self.student_id)
        return self._transcript

    async def enrolled(self) -> dict[int, CourseRecord]:
        """수강 중·이수한 과목 (같은 과목은 가장 최근 학기). {moodle course id: record}"""
        records: dict[str, CourseRecord] = {}
        for r in (await self.transcript()).records:
            if r.status in ("completed", "in_progress", "failed"):
                prev = records.get(r.course_code)
                if prev is None or r.semester >= prev.semester:
                    records[r.course_code] = r
        return {course_int(code): r for code, r in records.items()}

    async def course_ids_or_enrolled(self, args: dict[str, Any]) -> tuple[dict[int, CourseRecord], list[dict]]:
        enrolled = await self.enrolled()
        requested = [int(x) for x in args.get("courseids") or []]
        if not requested:
            return enrolled, []
        warnings = [
            {"item": "course", "itemid": cid, "warningcode": "1", "message": "No access rights in course context"}
            for cid in requested
            if cid not in enrolled
        ]
        return {cid: enrolled[cid] for cid in requested if cid in enrolled}, warnings


Handler = Callable[[MoodleContext, dict[str, Any]], Awaitable[Any]]
FUNCTIONS: dict[str, Handler] = {}


def ws_function(name: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        FUNCTIONS[name] = fn
        return fn

    return register


def _require_own_user(ctx: MoodleContext, args: dict[str, Any]) -> None:
    userid = args.get("userid")
    if userid not in (None, "", 0, "0") and int(userid) != ctx.userid:
        raise MoodleError(
            "nopermissions",
            "Sorry, but you do not currently have permissions to do that.",
            "required_capability_exception",
        )


# ---------------------------------------------------------------------
# Web service functions
# ---------------------------------------------------------------------
@ws_function("core_webservice_get_site_info")
async def get_site_info(ctx: MoodleContext, args: dict[str, Any]) -> dict[str, Any]:
    s = (await ctx.transcript()).student
    return {
        "sitename": SITE_NAME,
        "username": s.student_no,
        "firstname": s.name,
        "lastname": "",
        "fullname": s.name,
        "lang": "ko",
        "userid": ctx.userid,
        "siteurl": SITE_URL,
        "userpictureurl": "",
        "functions": [{"name": n, "version": MOODLE_VERSION} for n in sorted(FUNCTIONS)],
        "downloadfiles": 0,
        "uploadfiles": 0,
        "release": MOODLE_RELEASE,
        "version": MOODLE_VERSION,
        "mobilecssurl": "",
        "usercanmanageownfiles": False,
        "userquota": 0,
        "usermaxuploadfilesize": 0,
        "userhomepage": 1,
        "siteid": 1,
    }


@ws_function("core_enrol_get_users_courses")
async def get_users_courses(ctx: MoodleContext, args: dict[str, Any]) -> list[dict[str, Any]]:
    if args.get("userid") in (None, ""):
        raise MoodleError(
            "invalidparameter",
            "Invalid parameter value detected (Missing required key: userid)",
            "invalid_parameter_exception",
        )
    _require_own_user(ctx, args)
    courses = []
    for cid, r in sorted((await ctx.enrolled()).items(), key=lambda kv: kv[1].course_code):
        start, end = _semester_range(r.semester)
        courses.append(
            {
                "id": cid,
                "shortname": r.course_code,
                "fullname": r.course_name,
                "displayname": r.course_name,
                "idnumber": r.course_code,
                "visible": 1,
                "summary": "",
                "summaryformat": 1,
                "format": "weeks",
                "showgrades": True,
                "lang": "",
                "enablecompletion": False,
                "category": 1,
                "completed": r.status != "in_progress",
                "startdate": start,
                "enddate": end,
                "showactivitydates": True,
                "showcompletionconditions": False,
            }
        )
    return courses


async def _assignments(ctx: MoodleContext, courses: dict[int, CourseRecord]) -> list[tuple[int, dict[str, Any]]]:
    codes = {r.course_code: cid for cid, r in courses.items()}
    rows = await repository.list_assignments_for_courses(ctx.db, list(codes))
    return [(codes[row["course_code"]], row) for row in rows]


def _assignment_json(course_id: int, row: dict[str, Any]) -> dict[str, Any]:
    aid = uuid_int(row["id"], "assign")
    created = _epoch(row.get("created_at"))
    return {
        "id": aid,
        "cmid": uuid_int(row["id"], "cm"),
        "course": course_id,
        "name": row["title"],
        "nosubmissions": 0,
        "submissiondrafts": 0,
        "sendnotifications": 0,
        "sendlatenotifications": 0,
        "sendstudentnotifications": 1,
        "duedate": _epoch(row["due_at"]),
        "allowsubmissionsfromdate": created,
        "grade": 100,
        "timemodified": created,
        "completionsubmit": 0,
        "cutoffdate": 0,
        "gradingduedate": 0,
        "teamsubmission": 0,
        "requireallteammemberssubmit": 0,
        "teamsubmissiongroupingid": 0,
        "blindmarking": 0,
        "hidegrader": 0,
        "revealidentities": 0,
        "attemptreopenmethod": "untilpass",
        "maxattempts": -1,
        "markingworkflow": 0,
        "markingallocation": 0,
        "requiresubmissionstatement": 0,
        "preventsubmissionnotingroup": 0,
        "configs": [],
        "intro": _html(row.get("description") or ""),
        "introformat": 1,
        "introfiles": [],
        "introattachments": [],
    }


@ws_function("mod_assign_get_assignments")
async def get_assignments(ctx: MoodleContext, args: dict[str, Any]) -> dict[str, Any]:
    courses, warnings = await ctx.course_ids_or_enrolled(args)
    by_course: dict[int, list[dict[str, Any]]] = {cid: [] for cid in courses}
    for cid, row in await _assignments(ctx, courses):
        by_course[cid].append(_assignment_json(cid, row))
    return {
        "courses": [
            {
                "id": cid,
                "fullname": courses[cid].course_name,
                "shortname": courses[cid].course_code,
                "timemodified": 0,
                "assignments": items,
            }
            for cid, items in sorted(by_course.items(), key=lambda kv: courses[kv[0]].course_code)
        ],
        "warnings": warnings,
    }


async def _find_assignment(ctx: MoodleContext, assign_int: int) -> tuple[int, dict[str, Any], CourseRecord]:
    courses = await ctx.enrolled()
    for cid, row in await _assignments(ctx, courses):
        if uuid_int(row["id"], "assign") == assign_int:
            return cid, row, courses[cid]
    raise MoodleError(
        "invalidrecord", "Can't find data record in database table assign.", "dml_missing_record_exception"
    )


@ws_function("mod_assign_get_submission_status")
async def get_submission_status(ctx: MoodleContext, args: dict[str, Any]) -> dict[str, Any]:
    _require_own_user(ctx, args)
    _, row, record = await _find_assignment(ctx, int(args.get("assignid") or 0))
    submitted_at = (await repository.submissions_of(ctx.db, ctx.student_id)).get(str(row["id"]))
    can_submit = submitted_at is None and record.status == "in_progress"
    return {
        "lastattempt": {
            "submission": {
                "id": uuid_int(f"{row['id']}:{ctx.student_id}", "submission"),
                "userid": ctx.userid,
                "attemptnumber": 0,
                "timecreated": _epoch(submitted_at) if submitted_at else 0,
                "timemodified": _epoch(submitted_at) if submitted_at else 0,
                "status": "submitted" if submitted_at else "new",
                "groupid": 0,
                "plugins": [],
            },
            "submissiongroupmemberswhoneedtosubmit": [],
            "submissionsenabled": True,
            "locked": False,
            "graded": False,
            "canedit": can_submit,
            "caneditowner": can_submit,
            "cansubmit": can_submit,
            "extensionduedate": None,
            "blindmarking": False,
            "gradingstatus": "notgraded",
            "usergroups": [],
        },
        "warnings": [],
    }


@ws_function("mod_assign_submit_for_grading")
async def submit_for_grading(ctx: MoodleContext, args: dict[str, Any]) -> list[dict[str, Any]]:
    _, row, record = await _find_assignment(ctx, int(args.get("assignmentid") or 0))
    if record.status != "in_progress":
        return [
            {
                "item": "assignment",
                "itemid": uuid_int(row["id"], "assign"),
                "warningcode": "couldnotsubmitforgrading",
                "message": "Could not submit assignment for grading (course not in progress)",
            }
        ]
    await repository.mark_submitted(ctx.db, ctx.student_id, str(row["id"]))
    return []  # external_warnings: 빈 배열 = 성공


@ws_function("mod_page_get_pages_by_courses")
async def get_pages_by_courses(ctx: MoodleContext, args: dict[str, Any]) -> dict[str, Any]:
    courses, warnings = await ctx.course_ids_or_enrolled(args)
    codes = {r.course_code: cid for cid, r in courses.items()}
    pages = []
    for row in await repository.list_materials_for_courses(ctx.db, list(codes)):
        cid = codes[row["course_code"]]
        pages.append(
            {
                "id": uuid_int(row["id"], "page"),
                "coursemodule": uuid_int(row["id"], "cm"),
                "course": cid,
                "name": row["title"],
                "intro": "",
                "introformat": 1,
                "introfiles": [],
                "section": uuid_int(f"{row['course_code']}:{row['week_no']}", "section"),  # 섹션 ID (주차 번호 아님)
                "visible": True,
                "groupmode": 0,
                "groupingid": 0,
                "lang": "",
                "content": _html(row["content"]),
                "contentformat": 1,
                "contentfiles": [],
                "legacyfiles": 0,
                "legacyfileslast": None,
                "display": 5,
                "displayoptions": "",
                "revision": 1,
                "timemodified": _epoch(row.get("published_at")),
            }
        )
    return {"pages": pages, "warnings": warnings}


@ws_function("core_course_get_contents")
async def get_course_contents(ctx: MoodleContext, args: dict[str, Any]) -> list[dict[str, Any]]:
    courses = await ctx.enrolled()
    cid = int(args.get("courseid") or 0)
    record = courses.get(cid)
    if record is None:
        raise MoodleError("requireloginerror", "Course or activity not accessible.", "require_login_exception")
    materials = await repository.list_materials_for_courses(ctx.db, [record.course_code])
    assignments = await repository.list_assignments_for_courses(ctx.db, [record.course_code])
    weeks: dict[int, list[dict[str, Any]]] = {}
    for m in materials:
        weeks.setdefault(int(m["week_no"]), []).append(
            {
                "id": uuid_int(m["id"], "cm"),
                "name": m["title"],
                "instance": uuid_int(m["id"], "page"),
                "modname": "page",
                "modplural": "Pages",
                "visible": 1,
                "uservisible": True,
                "url": f"{SITE_URL}/mod/page/view.php?id={uuid_int(m['id'], 'cm')}",
            }
        )
    for a in assignments:
        week = next((int(m["week_no"]) for m in materials if m["id"] == a.get("material_id")), 0)
        weeks.setdefault(week, []).append(
            {
                "id": uuid_int(a["id"], "cm"),
                "name": a["title"],
                "instance": uuid_int(a["id"], "assign"),
                "modname": "assign",
                "modplural": "Assignments",
                "visible": 1,
                "uservisible": True,
                "url": f"{SITE_URL}/mod/assign/view.php?id={uuid_int(a['id'], 'cm')}",
            }
        )
    return [
        {
            "id": uuid_int(f"{record.course_code}:{week}", "section"),
            "name": "일반" if week == 0 else f"{week}주차",
            "visible": 1,
            "summary": "",
            "summaryformat": 1,
            "section": week,
            "hiddenbynumsections": 0,
            "uservisible": True,
            "modules": modules,
        }
        for week, modules in sorted(weeks.items())
    ]


@ws_function("gradereport_overview_get_course_grades")
async def get_course_grades(ctx: MoodleContext, args: dict[str, Any]) -> dict[str, Any]:
    _require_own_user(ctx, args)
    grades = [
        {"courseid": cid, "grade": r.grade or "-", "rawgrade": GRADE_POINTS.get(r.grade or "", "")}
        for cid, r in (await ctx.enrolled()).items()
        if r.grade
    ]
    return {"grades": grades, "warnings": []}


# ---------------------------------------------------------------------
# REST 서버
# ---------------------------------------------------------------------
_ARRAY_KEY = re.compile(r"^([a-z_]+)\[(\d+)\]$")


def parse_moodle_params(raw: dict[str, str]) -> dict[str, Any]:
    """courseids[0]=1&courseids[1]=2 → {"courseids": ["1", "2"]}"""
    out: dict[str, Any] = {}
    arrays: dict[str, dict[int, str]] = {}
    for key, value in raw.items():
        match = _ARRAY_KEY.match(key)
        if match:
            arrays.setdefault(match.group(1), {})[int(match.group(2))] = value
        else:
            out[key] = value
    for name, items in arrays.items():
        out[name] = [items[i] for i in sorted(items)]
    return out


def _error(exc: MoodleError) -> JSONResponse:
    return JSONResponse({"exception": exc.exception, "errorcode": exc.errorcode, "message": exc.message})


async def _resolve_token(db: AsyncClient, settings: LmsSettings, token: str | None) -> UUID:
    if not token or not re.fullmatch(r"[0-9a-f]{32}", token):
        raise MoodleError("invalidtoken", "Invalid token - token not found")
    for student_id in await repository.list_student_ids(db):
        if hmac.compare_digest(ws_token(settings, student_id), token):
            return student_id
    raise MoodleError("invalidtoken", "Invalid token - token not found")


@router.api_route("/webservice/rest/server.php", methods=["GET", "POST"], tags=["moodle"])
async def rest_server(request: Request, db: LmsDbDep, settings: LmsSettingsDep) -> JSONResponse:
    raw: dict[str, str] = dict(request.query_params)
    if request.method == "POST":
        form = await request.form()
        raw.update({k: str(v) for k, v in form.items()})

    if raw.pop("moodlewsrestformat", "xml") != "json":
        return _error(
            MoodleError(
                "invalidparameter",
                "이 가상 서버는 moodlewsrestformat=json 만 지원합니다.",
                "invalid_parameter_exception",
            )
        )
    token = raw.pop("wstoken", None)
    function = raw.pop("wsfunction", None)
    try:
        student_id = await _resolve_token(db, settings, token)
        handler = FUNCTIONS.get(function or "")
        if handler is None:
            raise MoodleError(
                "invalidrecord",
                "Can't find data record in database table external_functions.",
                "dml_missing_record_exception",
            )
        result = await handler(MoodleContext(db, student_id), parse_moodle_params(raw))
    except MoodleError as exc:
        return _error(exc)
    except LmsError as exc:
        logger.warning("Moodle 호환 계층 오류: %s", exc.code)
        return _error(MoodleError(exc.code, exc.message))
    except (ValueError, TypeError):
        return _error(
            MoodleError("invalidparameter", "Invalid parameter value detected", "invalid_parameter_exception")
        )
    return JSONResponse(result)
