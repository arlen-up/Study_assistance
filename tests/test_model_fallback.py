"""주 AI 모델이 오류를 내면 예비 모델로 바꿔 실행한다 (실제 AI 없이 확인)."""

import asyncio

import httpx
import openai
import pytest
from langchain_core.runnables import RunnableLambda

from backend.core.config import Settings, fallback_model_names
from backend.services.agent_service import with_model_fallback


class FakeModel:
    def __init__(self, name: str, fail: Exception | None = None) -> None:
        self.model_name = name
        self.fail = fail
        self.calls = 0

    def runnable(self) -> RunnableLambda:
        async def run(x: str) -> str:
            self.calls += 1
            if self.fail:
                raise self.fail
            return f"{self.model_name}:{x}"
        return RunnableLambda(run)


def _api_error() -> openai.APIError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return openai.APIConnectionError(request=request)


def test_uses_primary_when_ok() -> None:
    primary, backup = FakeModel("main"), FakeModel("backup")
    used: list[str] = []
    chain = with_model_fallback(primary, [backup], lambda m: m.runnable(), on_fallback=used.append)
    assert asyncio.run(chain.ainvoke("q")) == "main:q"
    assert backup.calls == 0 and used == []


def test_switches_to_next_model_on_api_error() -> None:
    primary, first, second = FakeModel("main", _api_error()), FakeModel("b1", _api_error()), FakeModel("b2")
    used: list[str] = []
    chain = with_model_fallback(primary, [first, second], lambda m: m.runnable(), on_fallback=used.append)
    assert asyncio.run(chain.ainvoke("q")) == "b2:q"
    assert used == ["b1", "b2"]


def test_other_errors_are_not_hidden() -> None:
    primary, backup = FakeModel("main", ValueError("코드 버그")), FakeModel("backup")
    chain = with_model_fallback(primary, [backup], lambda m: m.runnable())
    with pytest.raises(ValueError):
        asyncio.run(chain.ainvoke("q"))  # API 오류가 아니면 예비 모델로 덮지 않는다
    assert backup.calls == 0


def test_fallback_names_skip_primary_and_duplicates() -> None:
    s = Settings.model_construct(llm_chat_model="gpt-6-luna", llm_fallback_models="gpt-5.6-luna, gpt-6-luna,gpt-5.6-luna,")
    assert fallback_model_names(s) == ["gpt-5.6-luna"]
    assert fallback_model_names(Settings.model_construct(llm_chat_model="x", llm_fallback_models="")) == []
