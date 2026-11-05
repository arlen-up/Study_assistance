"""Moodle Web Services REST 클라이언트 (코스모스 = Moodle 기반).

규격: Moodle 4.5 LTS
- POST {base}/webservice/rest/server.php  (wstoken, wsfunction, moodlewsrestformat=json)
- 배열 파라미터는 name[0]=..&name[1]=.. 로 인코딩
- 오류도 HTTP 200으로 오며 본문이 {"exception", "errorcode", "message"} 형태 → 본문으로 판단한다
응답 모델은 도우미가 실제로 읽는 필드만 정의하고 나머지는 무시한다 (Moodle 버전 차이에 강하게).
"""

import logging
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from backend.core.errors import AppError, NotFoundError, UpstreamError

logger = logging.getLogger(__name__)

REST_PATH = "/webservice/rest/server.php"
T = TypeVar("T")


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class MoodleSiteInfo(_M):
    sitename: str
    username: str
    fullname: str
    userid: int
    release: str | None = None


class MoodleCourse(_M):
    id: int
    shortname: str
    fullname: str
    idnumber: str | None = None
    completed: bool | None = None
    startdate: int | None = None
    enddate: int | None = None


class MoodleAssignment(_M):
    id: int
    cmid: int
    course: int
    name: str
    duedate: int = 0
    cutoffdate: int = 0
    allowsubmissionsfromdate: int = 0
    timemodified: int = 0
    intro: str | None = None


class MoodleAssignmentCourse(_M):
    id: int
    fullname: str
    shortname: str
    assignments: list[MoodleAssignment] = Field(default_factory=list)


class MoodleAssignments(_M):
    courses: list[MoodleAssignmentCourse] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class MoodleSubmission(_M):
    status: str = "new"
    timemodified: int = 0


class MoodleLastAttempt(_M):
    submission: MoodleSubmission | None = None
    cansubmit: bool = False


class MoodleSubmissionStatus(_M):
    lastattempt: MoodleLastAttempt | None = None


class MoodlePage(_M):
    id: int
    coursemodule: int
    course: int
    name: str
    section: int | None = None
    content: str = ""
    contentformat: int = 1
    timemodified: int = 0


class MoodlePages(_M):
    pages: list[MoodlePage] = Field(default_factory=list)


class MoodleModule(_M):
    id: int  # course module id (cmid)
    name: str
    modname: str
    instance: int | None = None


class MoodleSection(_M):
    id: int
    name: str
    section: int | None = None  # 과목 안에서의 섹션 번호 (주차)
    modules: list[MoodleModule] = Field(default_factory=list)


class MoodleClient:
    def __init__(self, http: httpx.AsyncClient, token: str) -> None:
        self._http = http
        self._token = token

    @staticmethod
    def encode(params: dict[str, Any]) -> dict[str, str]:
        """list 값을 name[0]=.., name[1]=.. 로 펼친다 (Moodle REST 규칙)."""
        out: dict[str, str] = {}
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, list | tuple):
                for i, item in enumerate(value):
                    out[f"{key}[{i}]"] = str(item)
            elif isinstance(value, bool):
                out[key] = "1" if value else "0"
            else:
                out[key] = str(value)
        return out

    async def call(self, function: str, **params: Any) -> Any:
        data = {"wstoken": self._token, "wsfunction": function, "moodlewsrestformat": "json", **self.encode(params)}
        try:
            response = await self._http.post(REST_PATH, data=data)
        except httpx.HTTPError as exc:
            logger.warning("코스모스 연결 실패: %s (%s)", function, type(exc).__name__)
            raise UpstreamError("코스모스(LMS)에 연결할 수 없습니다.", code="lms_unreachable") from None
        if not response.is_success:
            raise UpstreamError(f"코스모스 응답 오류 (HTTP {response.status_code})", code="lms_error")
        try:
            body = response.json()
        except ValueError:
            raise UpstreamError("코스모스 응답 형식이 올바르지 않습니다.", code="lms_bad_response") from None

        if isinstance(body, dict) and "exception" in body:
            code = str(body.get("errorcode", ""))
            logger.info("코스모스 오류: %s → %s", function, code)
            if code in ("invalidtoken", "accessexception"):
                raise UpstreamError("코스모스 인증에 실패했습니다. 연동 토큰을 확인해 주세요.", code="lms_auth_failed")
            if code in ("invalidrecord", "requireloginerror"):
                raise NotFoundError("코스모스에서 해당 항목을 찾을 수 없습니다.", code="lms_not_found")
            if code == "nopermissions":
                raise AppError("코스모스 권한이 없습니다.", code="lms_forbidden", status_code=403)
            raise UpstreamError(f"코스모스 요청 실패: {body.get('message', code)}", code="lms_error")
        return body

    async def _typed(self, adapter_type: Any, function: str, **params: Any) -> Any:
        body = await self.call(function, **params)
        try:
            return TypeAdapter(adapter_type).validate_python(body)
        except ValidationError:
            logger.exception("코스모스 응답 검증 실패: %s", function)
            raise UpstreamError("코스모스 응답 형식이 예상과 다릅니다.", code="lms_bad_response") from None

    # ---------------- Web service functions ----------------
    async def get_site_info(self) -> MoodleSiteInfo:
        return await self._typed(MoodleSiteInfo, "core_webservice_get_site_info")

    async def get_users_courses(self, userid: int) -> list[MoodleCourse]:
        return await self._typed(list[MoodleCourse], "core_enrol_get_users_courses", userid=userid)

    async def get_assignments(self, courseids: list[int]) -> MoodleAssignments:
        return await self._typed(MoodleAssignments, "mod_assign_get_assignments", courseids=courseids)

    async def get_submission_status(self, assignid: int) -> MoodleSubmissionStatus:
        return await self._typed(MoodleSubmissionStatus, "mod_assign_get_submission_status", assignid=assignid)

    async def submit_for_grading(self, assignmentid: int) -> list[dict[str, Any]]:
        return await self._typed(
            list[dict[str, Any]], "mod_assign_submit_for_grading",
            assignmentid=assignmentid, acceptsubmissionstatement=True,
        )

    async def get_pages_by_courses(self, courseids: list[int]) -> MoodlePages:
        return await self._typed(MoodlePages, "mod_page_get_pages_by_courses", courseids=courseids)

    async def get_course_contents(self, courseid: int) -> list[MoodleSection]:
        return await self._typed(list[MoodleSection], "core_course_get_contents", courseid=courseid)
