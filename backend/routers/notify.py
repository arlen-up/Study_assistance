"""알림 채널 화면 조각 (/ui/notify/*).

- 📧 메일 미리보기: 오늘 저녁 알림이 메일로 간다면 어떤 모습인지 보여준다 (실제 발송은 추가 개발 예정).
- 💬 카카오톡 알림은 화면에 '추가 개발 예정'으로만 표시한다 (push_card.html).
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from backend.core.supabase_client import DbDep
from backend.integrations.lms_client import LmsDep
from backend.services import email_service, notification_service
from backend.web.session import WebUser
from backend.web.templating import templates

router = APIRouter(include_in_schema=False)


@router.get("/ui/notify/email-preview", response_class=HTMLResponse)
async def ui_email_preview(request: Request, user: WebUser, db: DbDep, lms: LmsDep) -> HTMLResponse:
    """로그인한 학생의 '오늘 메일'을 푸시와 같은 규칙으로 만들어 보여준다 (보내지는 않음)."""
    inputs = await notification_service.gather_reminder_inputs(db, lms, user.id)
    email = email_service.build_daily_email(inputs, base_url=str(request.base_url), to=user.email)
    return templates.TemplateResponse(request, "partials/email_preview.html", {"email": email})
