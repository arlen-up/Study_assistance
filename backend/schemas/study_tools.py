"""노트북LM 참고 학습 도구: 플래시카드 · 마인드맵 모델."""

from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.schemas.models import RequestModel, SourceChunk, ToolCallTrace

MAX_LABEL = 24


def _short(value: str, limit: int) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


# ---------------------------------------------------------------------
# 플래시카드
# ---------------------------------------------------------------------
class FlashcardDraft(BaseModel):
    """LLM 구조화 출력: 카드 1장."""

    front: str = Field(min_length=2, description="앞면: 짧은 질문이나 용어 (40자 이내)")
    back: str = Field(min_length=2, description="뒷면: 정답·설명 (1~2문장, 120자 이내)")
    hint: str | None = Field(default=None, description="선택: 떠올리기 힌트 (20자 이내)")
    source_chunk_id: str | None = Field(default=None, description="근거 chunk_id")

    @field_validator("front")
    @classmethod
    def _front(cls, v: str) -> str:
        return _short(v, 60)

    @field_validator("back")
    @classmethod
    def _back(cls, v: str) -> str:
        return _short(v, 200)


class FlashcardDeckDraft(BaseModel):
    """LLM 구조화 출력: 카드 묶음."""

    cards: list[FlashcardDraft] = Field(min_length=6, max_length=12, description="카드 6~12장, 서로 다른 개념")


class Flashcard(BaseModel):
    index: int
    front: str
    back: str
    hint: str | None = None
    source: SourceChunk | None = None


class FlashcardDeck(BaseModel):
    document_id: UUID
    document_title: str
    cards: list[Flashcard]
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)


class FlashcardRequest(RequestModel):
    document_id: UUID


class FlashcardResultRequest(RequestModel):
    document_id: UUID
    known: int = Field(ge=0, le=12)
    total: int = Field(ge=1, le=12)
    duration_seconds: int | None = Field(default=None, ge=0, le=86_400)

    @model_validator(mode="after")
    def _known_le_total(self) -> "FlashcardResultRequest":
        # 필드 검증이 모두 끝난 뒤 비교해야 total 값을 확실히 쓸 수 있다
        if self.known > self.total:
            raise ValueError("known은 total보다 클 수 없습니다")
        return self


class FlashcardResult(BaseModel):
    known: int
    total: int
    study_log_id: UUID


# ---------------------------------------------------------------------
# 마인드맵
# ---------------------------------------------------------------------
class MindLeafDraft(BaseModel):
    label: str = Field(min_length=1, description="세부 개념 (15자 이내 명사구)")

    @field_validator("label")
    @classmethod
    def _label(cls, v: str) -> str:
        return _short(v, MAX_LABEL)


class MindBranchDraft(BaseModel):
    label: str = Field(min_length=1, description="핵심 개념 (12자 이내 명사구)")
    children: list[MindLeafDraft] = Field(default_factory=list, max_length=5, description="세부 개념 0~5개")

    @field_validator("label")
    @classmethod
    def _label(cls, v: str) -> str:
        return _short(v, MAX_LABEL)


class MindMapDraft(BaseModel):
    """LLM 구조화 출력: 마인드맵."""

    root: str = Field(min_length=1, description="교안의 중심 주제 (12자 이내)")
    branches: list[MindBranchDraft] = Field(min_length=3, max_length=6, description="핵심 개념 3~6개")

    @field_validator("root")
    @classmethod
    def _root(cls, v: str) -> str:
        return _short(v, MAX_LABEL)


class MindNode(BaseModel):
    id: str
    label: str
    level: int          # 0=중심, 1=핵심 개념, 2=세부
    x: float
    y: float
    width: float
    height: float


class MindEdge(BaseModel):
    path: str           # SVG path d 속성
    level: int


class MindMap(BaseModel):
    document_id: UUID
    document_title: str
    root: str
    nodes: list[MindNode]
    edges: list[MindEdge]
    width: float
    height: float
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)


class MindMapRequest(RequestModel):
    document_id: UUID
