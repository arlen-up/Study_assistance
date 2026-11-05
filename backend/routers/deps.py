"""라우터 공통 의존성."""

from typing import Annotated

from fastapi import Depends

from backend.core.supabase_client import DbDep, SettingsDep
from backend.services.agent_service import AgentService


def get_agent(settings: SettingsDep, db: DbDep) -> AgentService:
    return AgentService(settings, db)


AgentDep = Annotated[AgentService, Depends(get_agent)]
