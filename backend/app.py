"""FastAPI 애플리케이션 조립: 라우터, 예외 처리, 보안 미들웨어, 정적 파일."""

import logging
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint

from backend import __version__
from backend.core.errors import AppError
from backend.routers import api, notify, web
from backend.web.session import LoginRequiredError, clear_session_cookies, set_session_cookies
from backend.web.templating import templates
from lms_mock.app import app as lms_app

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
# HTTP 클라이언트의 요청 URL 로그(학생 ID 등 쿼리 값 포함)는 남기지 않는다
for _noisy in ("httpx", "httpcore", "hpack"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger("lms.app")

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "public" / "static"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def _is_api(request: Request) -> bool:
    return request.url.path.startswith("/api/")


def _same_origin(request: Request) -> bool:
    """브라우저가 보낸 Origin이 현재 호스트와 같은지 확인 (CSRF 방어)."""
    origin = request.headers.get("origin")
    if not origin:
        return True  # 서버 간 호출·curl 등 Origin 없는 요청 (쿠키 SameSite=Lax가 추가로 보호)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    return urlparse(origin).netloc == host


def _error_response(request: Request, status: int, code: str, message: str) -> Response:
    if _is_api(request):
        return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})
    if _is_htmx(request):
        if status == 401:
            # HTMX가 전체 페이지를 로그인 화면으로 이동시킨다
            return Response(status_code=200, headers={"HX-Redirect": "/login"})
        # HTMX는 4xx/5xx 응답을 화면에 넣지 않으므로 200으로 돌려주고,
        # 원래 대상(예: 풀던 퀴즈) 대신 화면 아래 알림 영역(#toast)에 표시한다
        response = templates.TemplateResponse(request, "partials/error.html", {"message": message})
        response.headers["HX-Retarget"] = "#toast"
        response.headers["HX-Reswap"] = "innerHTML"
        return response
    if status == 401:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "error.html", {"message": message}, status_code=status)


def create_app() -> FastAPI:
    app = FastAPI(
        title="LSSA (Study Overflow)",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    # ---------------- 예외 처리 ----------------
    @app.exception_handler(LoginRequiredError)
    async def handle_login_required(request: Request, exc: LoginRequiredError) -> Response:
        return _error_response(request, 401, exc.code, exc.message)

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> Response:
        if exc.status_code >= 500:
            logger.warning("AppError %s: %s", exc.code, exc.message)
        return _error_response(request, exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> Response:
        if _is_api(request):
            return JSONResponse(status_code=422, content={"detail": jsonable_errors(exc)})
        return _error_response(request, 422, "validation_error", "입력값을 확인해 주세요.")

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> Response:
        logger.exception("처리되지 않은 오류", exc_info=exc)
        return _error_response(request, 500, "internal_error", "서버 오류가 발생했습니다.")

    # ---------------- 미들웨어: CSRF Origin 검사 + 세션 쿠키 갱신 ----------------
    @app.middleware("http")
    async def security_and_session(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method in UNSAFE_METHODS and not _same_origin(request):
            return PlainTextResponse("다른 사이트에서 보낸 요청은 허용되지 않습니다.", status_code=403)

        response = await call_next(request)

        new_session = getattr(request.state, "new_session", None)
        if new_session is not None:
            set_session_cookies(response, request, new_session)
        elif getattr(request.state, "clear_session", False):
            clear_session_cookies(response)

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    # ---------------- 라우터 · 정적 파일 ----------------
    app.include_router(api.router)
    app.include_router(web.router)
    app.include_router(notify.router)

    # 🏫 가상 LMS 모의 서버: 독립 FastAPI 앱을 /lms 에 연결 (→ /lms/api/*, 문서 /lms/api/docs)
    app.mount("/lms", lms_app, name="lms")

    # 로컬 개발용. Vercel에서는 public/ 폴더를 CDN이 직접 제공한다.
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(status_code=204)

    # 서비스 워커는 사이트 루트(/sw.js)에 있어야 전체 경로를 관리할 수 있다. (Vercel에서는 CDN이 public/ 에서 제공)
    @app.get("/sw.js", include_in_schema=False)
    async def service_worker() -> Response:
        path = ROOT / "public" / "sw.js"
        if not path.is_file():
            return Response(status_code=404)
        return FileResponse(
            path,
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Service-Worker-Allowed": "/"},
        )

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def manifest() -> Response:
        path = ROOT / "public" / "manifest.webmanifest"
        if not path.is_file():
            return Response(status_code=404)
        return FileResponse(path, media_type="application/manifest+json")

    return app


def jsonable_errors(exc: RequestValidationError) -> list[dict[str, object]]:
    return [{"loc": list(err.get("loc", [])), "msg": str(err.get("msg", ""))} for err in exc.errors()]


app = create_app()
