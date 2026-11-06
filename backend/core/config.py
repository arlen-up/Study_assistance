"""환경변수 설정 (pydantic-settings).

로컬: 프로젝트 루트의 .env / .env.local 을 읽는다.
Vercel: 프로젝트 Environment Variables 를 읽는다.
"""

import datetime as dt
from functools import lru_cache

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.core.errors import ConfigError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Supabase ---
    supabase_url: str
    supabase_secret_key: SecretStr  # 서버 전용 (RLS 우회) — 절대 브라우저로 보내지 않음
    supabase_publishable_key: SecretStr | None = None  # 로그인/토큰 갱신용 (없으면 secret 키 사용)

    # --- LLM (OpenAI 호환 API: OpenAI, Azure OpenAI 호환 게이트웨이, 로컬 vLLM 등) ---
    llm_api_key: SecretStr
    llm_base_url: str = "https://api.openai.com/v1"
    llm_chat_model: str = "gpt-6-luna"
    llm_embedding_model: str = "text-embedding-3-small"  # 1536차원
    # 주 모델이 오류(장애·시간 초과·모델 없음 등)를 내면 차례로 바꿔 실행할 예비 모델 (쉼표로 구분, 비우면 끔)
    llm_fallback_models: str = "gpt-5.6-luna,gpt-5.4-mini"
    # 추론 모델 설정. gpt-6-luna는 Chat Completions에서 함수 호출을 쓰려면 "none"이어야 한다.
    # 추론 기능이 없는 다른 OpenAI 호환 모델을 쓸 때는 빈 값으로 두면 요청에서 빠진다.
    llm_reasoning_effort: str | None = "none"

    # --- 임베딩 ---
    embedding_dimensions: int = Field(default=1536, ge=1, le=4096)
    embedding_send_dimensions: bool = False

    # --- LLM / 에이전트 ---
    llm_temperature: float | None = Field(default=None, ge=0.0, le=2.0)  # None이면 모델 기본값 (추론 모델 호환)
    llm_timeout_seconds: float = Field(default=40.0, gt=0, le=55)
    agent_max_iterations: int = Field(default=5, ge=1, le=10)

    # --- 문서 ---
    lms_allowed_hosts: str = ""
    max_upload_bytes: int = Field(default=4_000_000, ge=1, le=4_400_000)  # Vercel 요청 본문 한도 4.5MB

    # --- 가상 LMS 연동 ---
    lms_api_key: SecretStr | None = None
    lms_api_base_url: str | None = None  # 학사정보 API. 없으면 같은 앱 안의 가상 시스템을 직접 호출
    moodle_base_url: str | None = None   # 코스모스(Moodle) 주소. 없으면 가상 코스모스
    # 학교 계정 연결 (OAuth 2.0). 실제 학교에서는 학교가 앱을 등록하고 client_id/secret 을 발급한다.
    lms_oauth_client_id: str = "lssa-study-overflow"
    lms_oauth_client_secret: SecretStr | None = None  # 없으면 가상 학교용 값을 LMS_API_KEY 에서 만든다
    lms_token_key: SecretStr | None = None  # 학교가 발급한 학생 토큰을 DB에 암호화해 저장하는 서버 키 (32자 이상)
    lms_timeout_seconds: float = Field(default=10.0, gt=0, le=30)

    # --- 메일 알림 (Gmail SMTP · 앱 비밀번호) ---
    gmail_address: str | None = None             # 보내는 Gmail 주소 (알림 전용 계정 권장)
    gmail_app_password: SecretStr | None = None  # Google 계정 '앱 비밀번호' 16자리 (공백은 무시)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465                         # SSL
    email_test_daily_limit: int = Field(default=3, ge=0, le=20)  # 학생 1명이 하루에 보낼 수 있는 테스트 메일 수

    # --- 매일 알림 (Vercel Cron) ---
    cron_secret: SecretStr | None = None

    app_timezone: str = "Asia/Seoul"

    # --- 학기 달력 ('저번주 자바 내용' 같은 질문의 주차 계산) ---
    semester_label: str = "2026-2학기"
    semester_start: dt.date = dt.date(2026, 8, 31)  # 1주차가 시작하는 월요일

    @field_validator(
        "lms_api_key", "lms_api_base_url", "moodle_base_url", "cron_secret",
        "lms_oauth_client_secret", "lms_token_key", "gmail_address", "gmail_app_password",
        mode="before",
    )
    @classmethod
    def _blank_optional(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("lms_api_base_url", "moodle_base_url")
    @classmethod
    def _validate_lms_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().rstrip("/")
        if not value.startswith(("https://", "http://")):
            raise ValueError("주소는 http:// 또는 https:// 로 시작해야 합니다")
        return value

    @field_validator("supabase_url", "llm_base_url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("https://", "http://")):
            raise ValueError("URL은 http:// 또는 https:// 로 시작해야 합니다")
        return value

    @field_validator("llm_chat_model", "llm_embedding_model")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("빈 값일 수 없습니다")
        return value

    @field_validator("llm_reasoning_effort", mode="before")
    @classmethod
    def _validate_reasoning_effort(cls, value: object) -> str | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        effort = str(value).strip().lower()
        if effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError("none, low, medium, high, xhigh, max 중 하나여야 합니다")
        return effort

    @field_validator("llm_temperature", mode="before")
    @classmethod
    def _blank_temperature(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return frozenset(h.strip().lower() for h in self.lms_allowed_hosts.split(",") if h.strip())

    @property
    def email_enabled(self) -> bool:
        return bool(self.gmail_address and self.gmail_app_password)

    @property
    def auth_key(self) -> str:
        key = self.supabase_publishable_key or self.supabase_secret_key
        return key.get_secret_value()


def fallback_model_names(settings: "Settings") -> list[str]:
    names = [n.strip() for n in settings.llm_fallback_models.split(",") if n.strip()]
    return [n for n in dict.fromkeys(names) if n != settings.llm_chat_model]  # 중복·주 모델 제외


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """설정을 한 번만 읽어 캐시한다. 누락 시 값이 아닌 '변수 이름'만 담은 ConfigError를 던진다."""
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        names = sorted({str(err["loc"][0]).upper() for err in exc.errors() if err.get("loc")})
        raise ConfigError(names) from None
