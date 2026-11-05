"""Supabase 비동기 클라이언트.

서버리스 환경은 요청마다 이벤트 루프가 바뀔 수 있으므로
전역 싱글턴 대신 요청 단위로 클라이언트를 만든다.

- create_supabase: secret 키 클라이언트. RLS를 우회하므로 사용자 데이터 조회 시
  반드시 검증된 user_id로 필터링해야 한다.
- create_auth_client: 로그인·토큰 갱신 전용. 로그인하면 클라이언트 내부 세션이
  사용자 토큰으로 바뀌므로, DB 작업용 클라이언트와 섞어 쓰지 않도록 분리한다.
"""

from typing import Annotated

from fastapi import Depends
from supabase import AsyncClient, AsyncClientOptions, acreate_client

from backend.core.config import Settings, get_settings

_OPTIONS = AsyncClientOptions(auto_refresh_token=False, persist_session=False)


async def create_supabase(settings: Settings) -> AsyncClient:
    secret = settings.supabase_secret_key.get_secret_value()
    return await acreate_client(settings.supabase_url, secret, options=_OPTIONS)


async def create_auth_client(settings: Settings) -> AsyncClient:
    return await acreate_client(settings.supabase_url, settings.auth_key, options=_OPTIONS)


def settings_dependency() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(settings_dependency)]


async def get_db(settings: SettingsDep) -> AsyncClient:
    return await create_supabase(settings)


DbDep = Annotated[AsyncClient, Depends(get_db)]
