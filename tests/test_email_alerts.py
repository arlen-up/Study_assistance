"""메일 알림 (웹 푸시·카카오톡 대신 메일로만) — 실제 메일 서버 없이 확인."""

import asyncio
import smtplib

import pytest
from pydantic import SecretStr

from backend.core.config import Settings
from backend.core.errors import AppError
from backend.routers.notify import normalize_email
from backend.services import email_service
from backend.services.notification_service import EmailSettings


def _settings(address: str | None = "lssa.alert@gmail.com", password: str | None = "abcd efgh ijkl mnop") -> Settings:
    return Settings.model_construct(
        gmail_address=address,
        gmail_app_password=SecretStr(password) if password else None,
        smtp_host="smtp.gmail.com",
        smtp_port=465,
    )


def _message() -> email_service.EmailMessage:
    return email_service.EmailMessage(
        sender="LSSA", to="student@example.com", subject="⏰ ‘ERD 설계’ 마감이 다가와요", headline="내일 마감이에요",
        cta_label="과제 확인하러 가기", cta_url="https://lms-study-helper.vercel.app/?tab=grades",
        assignments=[], reviews=[], pet_line="🐛 하루가 기다려요", html="<p>HTML 본문</p>", text="텍스트 본문",
        reminder_kind="assignment_due",
    )


class FakeSMTP:
    sent: list = []
    fail: Exception | None = None

    def __init__(self, host, port, context=None, timeout=None):  # noqa: ANN001
        self.host, self.port = host, port

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *exc):  # noqa: ANN002, ANN204
        return False

    def login(self, user, password):  # noqa: ANN001, ANN201
        if FakeSMTP.fail:
            raise FakeSMTP.fail
        assert " " not in password and len(password) == 16  # 앱 비밀번호의 공백은 빼고 로그인
        self.user = user

    def send_message(self, msg):  # noqa: ANN001, ANN201
        FakeSMTP.sent.append(msg)


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSMTP.sent, FakeSMTP.fail = [], None
    monkeypatch.setattr(email_service.smtplib, "SMTP_SSL", FakeSMTP)


def test_send_builds_text_and_html_message() -> None:
    asyncio.run(email_service.send(_settings(), _message(), "student@example.com"))
    msg = FakeSMTP.sent[0]
    assert msg["To"] == "student@example.com"
    assert "lssa.alert@gmail.com" in msg["From"] and "LSSA" in msg["From"]
    assert msg["Subject"].startswith("⏰")
    types = [part.get_content_type() for part in msg.walk()]
    assert "text/plain" in types and "text/html" in types


def test_test_mail_subject_is_marked() -> None:
    asyncio.run(email_service.send(_settings(), _message(), "student@example.com", test=True))
    assert FakeSMTP.sent[0]["Subject"].startswith("[테스트]")


def test_not_configured() -> None:
    with pytest.raises(email_service.EmailNotConfiguredError):
        asyncio.run(email_service.send(_settings(password=None), _message(), "a@b.co"))


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (smtplib.SMTPAuthenticationError(535, b"bad credentials"), "email_send_failed"),
        (smtplib.SMTPServerDisconnected("closed"), "email_send_failed"),
        (smtplib.SMTPRecipientsRefused({"x@y.z": (550, b"no")}), "email_bad_recipient"),
    ],
)
def test_send_errors_become_clear_messages(error: Exception, code: str) -> None:
    FakeSMTP.fail = error
    with pytest.raises(AppError) as info:
        asyncio.run(email_service.send(_settings(), _message(), "x@y.z"))
    assert info.value.code == code
    assert "abcd" not in info.value.message  # 비밀번호가 메시지에 섞이지 않음


def test_recipient_prefers_alert_email() -> None:
    assert EmailSettings(True, "me@gmail.com", "demo@example.com").recipient == "me@gmail.com"
    assert EmailSettings(True, None, "demo@example.com").recipient == "demo@example.com"
    assert EmailSettings(False, None, None).recipient is None


@pytest.mark.parametrize("raw", ["", "   "])
def test_blank_address_means_account_email(raw: str) -> None:
    assert normalize_email(raw) is None


@pytest.mark.parametrize("raw", ["not-an-email", "a@b", "a b@c.de", "x" * 250 + "@a.co"])
def test_bad_address_rejected(raw: str) -> None:
    with pytest.raises(AppError):
        normalize_email(raw)
