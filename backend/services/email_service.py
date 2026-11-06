"""📧 메일 알림 — 지금은 '미리보기'만 (추가 개발 예정).

매일 저녁 푸시와 같은 재료(notification_service.gather_reminder_inputs)와 같은 선택 규칙(choose_reminder)으로
'오늘 보낼 메일'의 제목·HTML·텍스트를 만든다. 실제 발송(SMTP, 메일 발송 서비스)은 연결하지 않았다.
나중에 발송을 붙일 때는 EmailMessage를 그대로 보내면 된다. (카카오톡 알림톡도 같은 재료로 확장 가능)

🔒 보안
- HTML은 자동 이스케이프 템플릿으로 만든다 (과제·교안 제목에 태그가 있어도 글자로만 보임).
- 메일 속 링크는 우리 사이트의 경로('/...')만 붙인다 (다른 사이트로 보내는 링크 금지).
"""

import datetime as dt
from dataclasses import dataclass, replace
from urllib.parse import urljoin

from jinja2 import Environment

from backend.integrations.lms_client import LmsAssignment
from backend.services.notification_service import (
    Reminder,
    ReminderInputs,
    choose_reminder,
    urgent_assignments,
)

KST = dt.timezone(dt.timedelta(hours=9))
SENDER_NAME = "LSSA"
MAX_ITEMS = 3


@dataclass(frozen=True)
class EmailItem:
    title: str
    detail: str
    urgent: bool = False


@dataclass(frozen=True)
class EmailMessage:
    sender: str
    to: str | None
    subject: str
    headline: str
    cta_label: str
    cta_url: str
    assignments: list[EmailItem]
    reviews: list[EmailItem]
    pet_line: str
    html: str
    text: str
    reminder_kind: str | None   # 푸시와 같은 규칙으로 고른 알림 종류 (없으면 None)


# ---------------------------------------------------------------------
# 시간 표시
# ---------------------------------------------------------------------
def _kst(value: dt.datetime) -> str:
    local = value.astimezone(KST)
    return f"{local.month}월 {local.day}일 {local:%H:%M}"


def _left(value: dt.datetime, now: dt.datetime) -> str:
    seconds = (value - now).total_seconds()
    if seconds <= 0:
        return "마감 지남"
    hours = seconds / 3600
    if hours < 24:
        return f"{max(1, round(hours))}시간 남음"
    return f"{round(hours / 24)}일 남음"


def _safe_url(base_url: str, path: str) -> str:
    path = path if path.startswith("/") and not path.startswith("//") else "/"
    return urljoin(base_url if base_url.endswith("/") else base_url + "/", path.lstrip("/"))


# ---------------------------------------------------------------------
# 메일 HTML (메일 프로그램 호환을 위해 표 + 인라인 스타일)
# ---------------------------------------------------------------------
_env = Environment(autoescape=True, trim_blocks=True, lstrip_blocks=True)
_HTML = _env.from_string(
    """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{{ m.subject }}</title></head>
<body style="margin:0;padding:0;background:#f6f7f9;font-family:-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;color:#18181b;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f6f7f9;padding:20px 12px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#ffffff;border:1px solid #e4e4e7;border-radius:14px;">
  <tr><td style="padding:20px 24px 8px;font-size:13px;color:#71717a;">{{ m.sender }}</td></tr>
  <tr><td style="padding:0 24px 16px;">
    <h1 style="margin:0 0 10px;font-size:19px;line-height:1.4;">{{ title }}</h1>
    <p style="margin:0;font-size:15px;line-height:1.6;">{{ m.headline }}</p>
  </td></tr>
  <tr><td style="padding:0 24px 20px;">
    <a href="{{ m.cta_url }}" target="_blank" rel="noopener" style="display:inline-block;background:#1d4ed8;color:#ffffff;text-decoration:none;font-weight:700;font-size:14px;padding:10px 18px;border-radius:8px;">{{ m.cta_label }}</a>
  </td></tr>
  {% for section, items in [("📌 마감이 다가오는 과제", m.assignments), ("🔔 오늘 복습할 교안", m.reviews)] if items %}
  <tr><td style="padding:0 24px 16px;">
    <p style="margin:0 0 6px;font-size:13px;font-weight:700;color:#3f3f46;">{{ section }}</p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #e4e4e7;">
    {% for item in items %}
      <tr><td style="padding:8px 0;border-bottom:1px solid #e4e4e7;font-size:14px;">
        <strong>{{ item.title }}</strong><br>
        <span style="font-size:12px;color:{{ '#dc2626' if item.urgent else '#71717a' }};">{{ item.detail }}</span>
      </td></tr>
    {% endfor %}
    </table>
  </td></tr>
  {% endfor %}
  <tr><td style="padding:0 24px 20px;font-size:13px;color:#3f3f46;">{{ m.pet_line }}</td></tr>
  <tr><td style="padding:14px 24px;background:#fafafa;border-top:1px solid #e4e4e7;border-radius:0 0 14px 14px;font-size:11px;color:#a1a1aa;line-height:1.6;">
    알림은 하루에 한 번만 보내요. 대시보드에서 알림 방법을 바꿀 수 있어요.
  </td></tr>
</table>
</td></tr></table>
</body></html>"""
)


def _text_version(m: EmailMessage, title: str) -> str:
    lines = [title, "", m.headline, "", f"{m.cta_label}: {m.cta_url}"]
    for section, items in (("[마감이 다가오는 과제]", m.assignments), ("[오늘 복습할 교안]", m.reviews)):
        if items:
            lines += ["", section, *(f"- {i.title} ({i.detail})" for i in items)]
    lines += ["", m.pet_line, "", "알림은 하루에 한 번만 보내요."]
    return "\n".join(lines)


# ---------------------------------------------------------------------
# 오늘의 메일 만들기
# ---------------------------------------------------------------------
def _assignment_items(inputs: ReminderInputs, now: dt.datetime) -> list[EmailItem]:
    urgent_ids = {a.id for a in urgent_assignments(inputs, now)}
    upcoming: list[LmsAssignment] = [a for a in inputs.assignments if a.due_at and a.due_at > now]
    return [
        EmailItem(
            title=a.title,
            detail=f"{a.course_name} · {_kst(a.due_at)} 마감 ({_left(a.due_at, now)})",  # type: ignore[arg-type]
            urgent=a.id in urgent_ids,
        )
        for a in upcoming[:MAX_ITEMS]
    ]


def _headline(reminder: Reminder | None, inputs: ReminderInputs) -> str:
    if reminder is not None:
        return reminder.body
    if inputs.studied_today:
        return "오늘 공부는 이미 끝냈어요! 내일도 3분만 함께해요."
    return "오늘은 급한 일이 없어요. 3분만 복습해 볼까요?"


def _heading_and_subject(reminder: Reminder | None, inputs: ReminderInputs, now: dt.datetime) -> tuple[str, str]:
    """메일 제목(받은편지함 한 줄)과 본문 큰 제목. 푸시 제목(펫 문구)과 겹치지 않게 짧게 쓴다."""
    if reminder is None:
        heading = "🎉 오늘도 잘했어요" if inputs.studied_today else "📚 오늘의 3분 학습"
        return heading, heading
    if reminder.kind == "assignment_due":
        first = urgent_assignments(inputs, now)[0].title
        name = first if len(first) <= 24 else first[:23] + "…"
        return inputs.title, f"⏰ ‘{name}’ 마감이 다가와요"
    if reminder.kind == "review_due":
        return inputs.title, f"🔔 오늘 복습할 교안이 {len(inputs.reviews.due)}개 있어요"
    return inputs.title, f"{inputs.title} · 오늘 3분이면 {inputs.pet.current_streak + 1}일 연속이에요"


def build_daily_email(
    inputs: ReminderInputs, *, base_url: str, to: str | None, now: dt.datetime | None = None
) -> EmailMessage:
    now = now or dt.datetime.now(dt.UTC)
    reminder = choose_reminder(inputs, now)
    heading, subject = _heading_and_subject(reminder, inputs, now)
    reviews = [
        EmailItem(title=r.document_title, detail=f"{r.review_count}회 학습 · {r.stage_label}")
        for r in inputs.reviews.due[:MAX_ITEMS]
    ]
    pet = inputs.pet
    draft = EmailMessage(
        sender=SENDER_NAME,
        to=to,
        subject=subject,
        headline=_headline(reminder, inputs),
        cta_label="과제 확인하러 가기" if reminder and reminder.kind == "assignment_due" else "지금 3분 공부하러 가기",
        cta_url=_safe_url(base_url, reminder.url if reminder else "/"),
        assignments=_assignment_items(inputs, now) if inputs.linked else [],
        reviews=reviews,
        pet_line=f"{pet.emoji} {pet.message}",
        html="",
        text="",
        reminder_kind=reminder.kind if reminder else None,
    )
    return replace(draft, html=_HTML.render(m=draft, title=heading), text=_text_version(draft, heading))
