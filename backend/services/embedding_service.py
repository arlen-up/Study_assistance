"""OpenAI 호환 임베딩 서비스."""

import logging

import openai
from langchain_openai import OpenAIEmbeddings

from backend.core.config import Settings
from backend.core.errors import UpstreamError

logger = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(self, settings: Settings) -> None:
        self._dimensions = settings.embedding_dimensions
        kwargs: dict[str, object] = {
            "model": settings.llm_embedding_model,
            "api_key": settings.llm_api_key,
            "base_url": settings.llm_base_url,
            # OpenAI 이외 서버는 토큰 ID 배열 입력을 지원하지 않는 경우가 많아 문자열 그대로 전송
            "check_embedding_ctx_length": False,
            "timeout": settings.llm_timeout_seconds,
            "max_retries": 2,
            "chunk_size": 64,
        }
        if settings.embedding_send_dimensions:
            kwargs["dimensions"] = settings.embedding_dimensions
        self._client = OpenAIEmbeddings(**kwargs)  # type: ignore[arg-type]

    def _validate(self, vectors: list[list[float]], expected: int) -> list[list[float]]:
        if len(vectors) != expected:
            raise UpstreamError("임베딩 응답 개수가 요청과 다릅니다.")
        for vec in vectors:
            if len(vec) != self._dimensions:
                raise UpstreamError(
                    f"임베딩 차원이 {len(vec)}입니다. DB(vector({self._dimensions}))와 일치하는 모델을 사용하세요.",
                    code="embedding_dimension_mismatch",
                )
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        text = text.strip()
        if not text:
            raise UpstreamError("빈 텍스트는 임베딩할 수 없습니다.", code="empty_text", status_code=400)
        try:
            vector = await self._client.aembed_query(text)
        except openai.OpenAIError as exc:
            logger.warning("임베딩 실패: %s", type(exc).__name__)
            raise UpstreamError("임베딩 API 호출에 실패했습니다.") from None
        return self._validate([vector], 1)[0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            vectors = await self._client.aembed_documents(texts)
        except openai.OpenAIError as exc:
            logger.warning("임베딩 실패: %s", type(exc).__name__)
            raise UpstreamError("임베딩 API 호출에 실패했습니다.") from None
        return self._validate(vectors, len(texts))
