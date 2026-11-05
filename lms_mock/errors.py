"""가상 LMS 서버 예외. 응답 형식: {"error": {"code", "message"}}"""


class LmsError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def not_found(what: str) -> LmsError:
    return LmsError(404, "not_found", f"{what}을(를) 찾을 수 없습니다.")
