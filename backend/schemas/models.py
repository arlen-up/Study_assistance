"""API 요청/응답 및 LLM 구조화 출력용 Pydantic 모델."""

import datetime as dt
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RequestModel(BaseModel):
    """클라이언트 입력: 알 수 없는 필드 거부, 앞뒤 공백 제거."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------
# 공통
# ---------------------------------------------------------------------
class AuthUser(BaseModel):
    id: UUID
    email: str | None = None


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    config_ok: bool
    missing_env: list[str] = Field(default_factory=list)
    version: str


class SourceChunk(BaseModel):
    """RAG(문서 검색 + 답변)의 출처 = 교안 조각(청크)."""

    chunk_id: UUID
    document_id: UUID
    document_title: str
    chunk_index: int
    page_number: int | None = None
    similarity: float
    content: str
    location: str | None = None  # 사람이 읽는 위치: "p.3" / "§ 2. 큐(Queue)" / "1번째 단락"
    ref: int | None = None       # 답변 속 인용 번호 [1], [2] …


class ToolCallTrace(BaseModel):
    """에이전트가 호출한 MCP 도구 기록 — 화면에서 눌러 입력/출력을 볼 수 있다."""

    name: str
    arguments: dict[str, Any]
    ok: bool
    output: str | None = None       # 도구 결과 (길면 잘라서 보관)
    duration_ms: int | None = None


# ---------------------------------------------------------------------
# 문서
# ---------------------------------------------------------------------
class DocumentItem(BaseModel):
    id: UUID
    title: str
    course_name: str | None = None
    week_no: int | None = None
    keywords: list[str] = Field(default_factory=list)
    created_at: dt.datetime


class DocumentListResponse(BaseModel):
    documents: list[DocumentItem]


class DocumentIngestResponse(BaseModel):
    document_id: UUID
    title: str
    chunk_count: int
    char_count: int
    already_imported: bool = False  # LMS 자료를 이미 가져온 경우 True (중복 저장 안 함)
    original_saved: bool = False    # PDF 원본을 뷰어용으로 보관했는지


# ---------------------------------------------------------------------
# 챗봇 (RAG)
# ---------------------------------------------------------------------
class ChatTurn(RequestModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


RouteType = Literal["chat", "study", "info"]
Evidence = Literal["high", "medium", "low"]


class ChatRequest(RequestModel):
    message: str = Field(min_length=1, max_length=2000)
    document_id: UUID | None = None
    history: list[ChatTurn] = Field(default_factory=list, max_length=10)
    include_summary: bool = False
    route: RouteType | None = Field(default=None, description="비우면 AI가 질문 종류를 판단 (chat/study/info)")


class RouteDecision(BaseModel):
    """LLM 구조화 출력: 질문 분류기(Router)."""

    type: RouteType = Field(description="chat(일상 대화) / study(학습 질문) / info(학적 정보 조회)")
    reason: str = Field(description="그렇게 분류한 이유 (한국어, 20자 이내)")
    course: str | None = Field(default=None, description="질문에 나온 과목 이름이나 별칭 (예: 자바). 없으면 null")
    week_no: int | None = Field(default=None, ge=1, le=16, description="질문이 가리키는 수업 주차 번호. 없으면 null")


class RouteInfo(BaseModel):
    """질문 분류기(Router) 결과 — chat(일상) / study(학습) / info(학적 정보)."""

    type: RouteType
    reason: str
    method: Literal["llm", "rule", "button"]   # AI가 분류 / 규칙으로 분류(AI 실패 시) / 단추로 지정
    duration_ms: int = 0
    plan: dict[str, Any] = Field(default_factory=dict)


class LmsSource(BaseModel):
    """학적 정보(info) 답변의 출처 — 어느 학교 시스템의 어떤 데이터를 봤는지."""

    system: str        # 예: "가상 코스모스", "가상 학사정보"
    label: str         # 예: "미제출 과제 목록"
    fetched_at: dt.datetime


class Citation(BaseModel):
    """답변 밑에 붙는 출처 한 줄 — 답변 속 [1], [2] 번호와 짝을 이룬다."""

    ref: int
    kind: Literal["chunk", "document"]   # 교안 조각 / 교안 전체(과목·주차로 찾은 자료)
    title: str
    location: str | None = None          # "p.3" / "§ 2. 큐(Queue)" / "본문 2번째 부분"
    snippet: str | None = None
    document_id: UUID | None = None
    chunk_id: UUID | None = None
    page_number: int | None = None
    similarity: float | None = None
    cited: bool = True                   # 답변 속에서 실제로 [번호]로 인용했는지


class ChatResponse(BaseModel):
    answer: str                                   # 마크다운
    three_minute_summary: str | None = None
    route: RouteInfo | None = None
    evidence: Evidence | None = None              # 학습 질문(study)에서만 '근거 충분/부족'을 표시
    confidence: Evidence = "high"                 # (이전 API 호환) evidence와 같은 값, 없으면 high
    citations: list[Citation] = Field(default_factory=list)
    sources: list[SourceChunk] = Field(default_factory=list)
    lms_sources: list[LmsSource] = Field(default_factory=list)
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)
    lms_data: list[str] = Field(default_factory=list)  # 답변에 사용한 LMS 데이터 (예: "성적표")
    context_document_id: UUID | None = None       # 다음 단추(요약·퀴즈·그림 정리)에 쓸 교안
    context_document_title: str | None = None
    answer_ms: int = 0                            # 답변 작성에 걸린 시간 (질문 분류 포함)


# ---------------------------------------------------------------------
# 3분 요약
# ---------------------------------------------------------------------
class SummaryRequest(RequestModel):
    document_id: UUID


class SummaryDraft(BaseModel):
    """LLM 구조화 출력: 3분 핵심 요약."""

    three_minute_summary: str = Field(description="3분 안에 읽을 수 있는 핵심 요약(600~900자, 한국어).")
    key_points: list[str] = Field(min_length=3, max_length=5, description="핵심 포인트 3~5개, 각각 한 문장.")
    keywords: list[str] = Field(min_length=1, max_length=8, description="교안 핵심 키워드.")
    cited_chunk_ids: list[str] = Field(default_factory=list, description="요약 근거로 사용한 chunk_id 목록.")


class SummaryResponse(BaseModel):
    document_id: UUID
    document_title: str
    three_minute_summary: str
    key_points: list[str]
    keywords: list[str]
    sources: list[SourceChunk]
    citations: list["Citation"] = Field(default_factory=list)
    tool_calls: list[ToolCallTrace]


# ---------------------------------------------------------------------
# 적응형 마이크로 퀴즈
# ---------------------------------------------------------------------
QuizType = Literal["multiple_choice", "ox", "code_blank"]
Difficulty = Literal["easy", "medium", "hard"]
CODE_BLANK_MARK = "____"


class QuizQuestionDraft(BaseModel):
    """LLM 구조화 출력: 퀴즈 1문항.

    여러 OpenAI 호환 서버에서 안정적으로 동작하도록 union 대신 평평한(flat) 스키마를 쓰고,
    유형별 규칙은 검증기로 강제한다.
    """

    type: QuizType = Field(description="multiple_choice | ox | code_blank")
    question: str = Field(min_length=5, description="문제 본문 (한국어).")
    options: list[str] = Field(
        default_factory=list, description="multiple_choice일 때만 보기 4개. 그 외 유형은 빈 배열."
    )
    code: str | None = Field(default=None, description=f"code_blank일 때만: 빈칸을 {CODE_BLANK_MARK} 로 표시한 코드.")
    language: str | None = Field(default=None, description="code_blank일 때 프로그래밍 언어 (예: python).")
    answer: str = Field(min_length=1, description="정답. multiple_choice는 보기 문자열 그대로, ox는 'O' 또는 'X'.")
    explanation: str = Field(min_length=5, description="정답 해설 (한국어, 2~3문장).")
    source_chunk_id: str | None = Field(default=None, description="문제 근거 chunk_id.")

    @model_validator(mode="after")
    def _validate_by_type(self) -> "QuizQuestionDraft":
        self.answer = self.answer.strip()
        if self.type == "multiple_choice":
            opts = [o.strip() for o in self.options if o.strip()]
            if len(opts) != 4 or len(set(opts)) != 4:
                raise ValueError("객관식은 서로 다른 보기 4개가 필요합니다")
            if self.answer not in opts:
                raise ValueError("객관식 정답은 보기 중 하나와 정확히 같아야 합니다")
            self.options = opts
            self.code = None
        elif self.type == "ox":
            normalized = self.answer.upper()
            if normalized not in {"O", "X"}:
                raise ValueError("O/X 문제의 정답은 'O' 또는 'X' 여야 합니다")
            self.answer = normalized
            self.options = ["O", "X"]
            self.code = None
        else:  # code_blank
            if not self.code or CODE_BLANK_MARK not in self.code:
                raise ValueError(f"코드 빈칸 문제는 {CODE_BLANK_MARK} 가 포함된 code가 필요합니다")
            self.options = []
        return self


class QuizDraft(BaseModel):
    """LLM 구조화 출력: 퀴즈 3문항 세트."""

    keywords: list[str] = Field(min_length=1, max_length=8, description="오늘 교안의 핵심 키워드.")
    questions: list[QuizQuestionDraft] = Field(min_length=3, max_length=3, description="정확히 3문항.")


class QuizGenerateRequest(RequestModel):
    document_id: UUID | None = Field(default=None, description="비우면 가장 최근 교안(오늘의 교안)을 사용")
    difficulty: Difficulty = "medium"


class PublicQuizQuestion(BaseModel):
    """클라이언트에 보내는 문항 (정답·해설 제외)."""

    index: int
    type: QuizType
    question: str
    options: list[str]
    code: str | None = None
    language: str | None = None


class QuizResponse(BaseModel):
    quiz_id: UUID
    document_id: UUID
    document_title: str
    difficulty: Difficulty
    keywords: list[str]
    questions: list[PublicQuizQuestion]
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)


class QuizSubmitRequest(RequestModel):
    answers: list[str] = Field(min_length=1, max_length=10)
    duration_seconds: int | None = Field(default=None, ge=0, le=86_400)

    @field_validator("answers")
    @classmethod
    def _limit_answer_length(cls, values: list[str]) -> list[str]:
        if any(len(v) > 500 for v in values):
            raise ValueError("답안은 500자 이하여야 합니다")
        return values


class QuestionResult(BaseModel):
    index: int
    correct: bool
    your_answer: str
    correct_answer: str
    explanation: str


# ---------------------------------------------------------------------
# 에빙하우스 복습
# ---------------------------------------------------------------------
class ReviewItem(BaseModel):
    document_id: UUID
    document_title: str
    last_studied_at: dt.datetime
    review_count: int
    review_1d_at: dt.datetime
    review_3d_at: dt.datetime
    review_7d_at: dt.datetime
    next_review_at: dt.datetime | None = None
    is_due: bool
    stage_label: str
    course_name: str | None = None  # 과목명 (documents.course_name)


class ReviewListResponse(BaseModel):
    due: list[ReviewItem]
    upcoming: list[ReviewItem]
    mastered: list[ReviewItem]


class QuizSubmitResponse(BaseModel):
    quiz_id: UUID
    score: int
    total: int
    results: list[QuestionResult]
    study_log_id: UUID
    review: ReviewItem | None = None


# ---------------------------------------------------------------------
# 잔디(스트릭)
# ---------------------------------------------------------------------
class HeatmapDay(BaseModel):
    date: dt.date
    completed: bool
    count: int


class StreakResponse(BaseModel):
    days: list[HeatmapDay]
    current_streak: int
    longest_streak: int
    active_days: int
    total_days: int


class DashboardResponse(BaseModel):
    streak: StreakResponse
    reviews: ReviewListResponse
    pet: "PetStatus | None" = None


# ---------------------------------------------------------------------
# 가상 LMS 연동
# ---------------------------------------------------------------------
class LmsLinkStatus(BaseModel):
    linked: bool
    student_id: UUID | None = None
    student_no: str | None = None
    name: str | None = None
    department: str | None = None
    completed_credits: int = 0
    in_progress_credits: int = 0
    synced_at: dt.datetime | None = None


# ---------------------------------------------------------------------
# 가상 펫 · 푸시 알림
# ---------------------------------------------------------------------
PetMood = Literal["new", "happy", "waiting", "sad"]


class PetStageInfo(BaseModel):
    key: str
    label: str
    emoji: str
    image_url: str | None = None
    min_streak: int
    reached: bool
    current: bool


class PetStatus(BaseModel):
    name: str
    theme: str
    stage_key: str
    stage_label: str
    emoji: str
    image_url: str | None = None
    stages: list[PetStageInfo] = []
    mood: PetMood
    message: str
    current_streak: int
    next_stage_label: str | None = None
    days_to_next: int | None = None
    progress_percent: int = 0


class PushKeys(RequestModel):
    p256dh: str = Field(min_length=40, max_length=200, pattern=r"^[A-Za-z0-9_\-=]+$")
    auth: str = Field(min_length=10, max_length=64, pattern=r"^[A-Za-z0-9_\-=]+$")


class PushSubscriptionIn(RequestModel):
    endpoint: str = Field(min_length=20, max_length=2048, pattern=r"^https://")
    keys: PushKeys
    expirationTime: int | None = None  # noqa: N815 (브라우저 PushSubscription.toJSON() 필드명)


class PushUnsubscribeIn(RequestModel):
    endpoint: str = Field(min_length=20, max_length=2048, pattern=r"^https://")


class PushResult(BaseModel):
    ok: bool
    delivered: int = 0
    message: str


class CronSummary(BaseModel):
    users: int
    synced: int
    imported_materials: int
    sent: int
    skipped: int
    failed: int
    elapsed_seconds: float
