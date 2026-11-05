"""애플리케이션 공통 예외. FastAPI 예외 핸들러에서 JSON 응답으로 변환된다."""


class AppError(Exception):
    """사용자에게 노출해도 안전한 메시지를 가진 예외."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"


class UpstreamError(AppError):
    """LLM·임베딩·DB 등 외부 서비스 오류."""

    status_code = 502
    code = "upstream_error"


class ConfigError(AppError):
    status_code = 500
    code = "server_misconfigured"

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(f"서버 환경변수가 설정되지 않았습니다: {', '.join(missing)}")
