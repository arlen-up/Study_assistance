"""📧 메일 알림 화면 조각 (/ui/notify/*). 알림은 메일로만 보낸다 (웹 푸시·카카오톡은 쓰지 않는다).

- 설정: 메일 알림 받기 켜기·끄기, 받을 주소 (비우면 가입 메일)
- 테스트 메일: 오늘 저녁에 갈 메일을 지금 한 번 보내 보기 (하루 횟수 제한)
- 미리보기: 보내지 않고 오늘 메일 모습만 보기
"""

import re
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from supabase import AsyncClient

from backend.core.config import Settings
from backend.core.errors import AppError
from backend.core.supabase_client import DbDep, SettingsDep
from backend.integrations.lms_client import LmsDep
from backend.services import email_service, notification_service
from backend.web.session import WebUser, public_base_url
from backend.web.templating import templates

router = APIRouter(include_in_schema=False)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(raw: str) -> str | None:
    """빈칸이면 None(가입 메일 사용). 형식이 틀리면 AppError."""
    value = raw.strip()
    if not value:
        return None
    if len(value) > 254 or not _EMAIL.match(value):
        raise AppError("메일 주소 형식을 확인해 주세요.", code="email_bad_format", status_code=400)
    return value


async def email_card_context(db: AsyncClient, settings: Settings, user_id: UUID) -> dict[str, Any]:
    return {
        "email_prefs": await notification_service.get_email_settings(db, user_id),
        "email_ready": settings.email_enabled,
        "email_test_limit": settings.email_test_daily_limit,
    }


def _card(request: Request, context: dict[str, Any], notice: str | None = None, error: bool = False) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "partials/email_card.html", {**context, "email_notice": notice, "email_error": error}
    )


@router.post("/ui/notify/email-settings", response_class=HTMLResponse)
async def ui_email_settings(
    request: Request,
    user: WebUser,
    db: DbDep,
    settings: SettingsDep,
    enabled: Annotated[str | None, Form()] = None,
    alert_email: Annotated[str, Form(max_length=300)] = "",
) -> HTMLResponse:
    try:
        address = normalize_email(alert_email)
        await notification_service.save_email_settings(db, user.id, enabled=enabled is not None, alert_email=address)
    except AppError as exc:
        return _card(request, await email_card_context(db, settings, user.id), exc.message, error=True)
    context = await email_card_context(db, settings, user.id)
    prefs = context["email_prefs"]
    notice = (f"저장했어요. 매일 저녁 8시에 {prefs.recipient}(으)로 보내요." if prefs.enabled
              else "저장했어요. 메일 알림은 꺼져 있어요.")
    return _card(request, context, notice)


@router.post("/ui/notify/email-test", response_class=HTMLResponse)
async def ui_email_test(request: Request, user: WebUser, db: DbDep, settings: SettingsDep, lms: LmsDep) -> HTMLResponse:
    try:
        to = await notification_service.send_test_email(db, settings, lms, user.id, base_url=public_base_url(request))
    except AppError as exc:
        return _card(request, await email_card_context(db, settings, user.id), exc.message, error=True)
    return _card(request, await email_card_context(db, settings, user.id),
                 f"{to}(으)로 테스트 메일을 보냈어요. 메일함(스팸함 포함)을 확인해 주세요.")


@router.get("/ui/notify/email-preview", response_class=HTMLResponse)
async def ui_email_preview(
    request: Request, user: WebUser, db: DbDep, settings: SettingsDep, lms: LmsDep
) -> HTMLResponse:
    """로그인한 학생의 '오늘 메일'을 매일 저녁 메일과 같은 규칙으로 만들어 보여준다 (보내지는 않음)."""
    prefs = await notification_service.get_email_settings(db, user.id)
    inputs = await notification_service.gather_reminder_inputs(db, lms, user.id)
    email = email_service.build_daily_email(inputs, base_url=public_base_url(request), to=prefs.recipient or user.email)
    return templates.TemplateResponse(request, "partials/email_preview.html", {"email": email})
