"""라우터 공통 의존성.

AI 에이전트는 '현재 사용자의 LMS 학생 ID'에 묶여 만들어진다.
JSON API(Bearer/쿠키)와 웹 화면(쿠키 + 자동 갱신)은 사용자 확인 방식이 달라 의존성을 나눈다.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends

from backend.core.auth import CurrentUser
from backend.core.config import Settings
from backend.core.supabase_client import DbDep, SettingsDep
from backend.integrations.lms_client import LmsClient, LmsDep
from backend.services import lms_link_service
from backend.services.agent_service import AgentService
from backend.web.session import WebUser
from supabase import AsyncClient


async def _build_agent(settings: Settings, db: AsyncClient, lms: LmsClient, user_id: UUID) -> AgentService:
    student_id = await lms_link_service.get_student_id(db, user_id)
    return AgentService(settings, db, lms=lms, lms_student_id=student_id, user_id=user_id)


async def get_api_agent(settings: SettingsDep, db: DbDep, lms: LmsDep, user: CurrentUser) -> AgentService:
    return await _build_agent(settings, db, lms, user.id)


async def get_web_agent(settings: SettingsDep, db: DbDep, lms: LmsDep, user: WebUser) -> AgentService:
    return await _build_agent(settings, db, lms, user.id)


ApiAgentDep = Annotated[AgentService, Depends(get_api_agent)]
WebAgentDep = Annotated[AgentService, Depends(get_web_agent)]
