"""App factory: wires config, database engine, and mailer into the FastAPI app."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from bokehbowl.admin import AdminRequired, LoginThrottle
from bokehbowl.admin import router as admin_router
from bokehbowl.auth import csrf_token
from bokehbowl.config import AppConfig
from bokehbowl.mailer import Mailer
from bokehbowl.web import LoginRequired
from bokehbowl.web import router as web_router

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_FAVICON = STATIC_DIR / "favicon.svg"
INSTANCE_DIR = Path("instance")
INSTANCE_TEMPLATES_DIR = INSTANCE_DIR / "templates"
INSTANCE_FAVICON = INSTANCE_DIR / "favicon.svg"

MAX_BODY_BYTES = 64 * 1024


def csrf_context(request: Request) -> dict[str, str]:
    """Expose the request's CSRF token to every rendered template."""
    return {"csrf": csrf_token(request)}


def create_app(config: AppConfig, engine: Engine, mailer: Mailer) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config = config
    app.state.engine = engine
    app.state.mailer = mailer
    app.state.admin_login_throttle = LoginThrottle()
    templates = Jinja2Templates(
        directory=[INSTANCE_TEMPLATES_DIR, TEMPLATES_DIR],
        context_processors=[csrf_context],
    )
    templates.env.globals.update(
        operator_name=config.operator_name,
        operator_email=config.operator_email,
        app_commit=config.commit,
    )
    app.state.templates = templates

    favicon = INSTANCE_FAVICON if INSTANCE_FAVICON.is_file() else DEFAULT_FAVICON

    @app.get("/favicon.ico")
    def favicon_file() -> FileResponse:
        return FileResponse(favicon)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    app.add_middleware(
        SessionMiddleware,
        secret_key=config.session_secret,
        same_site="lax",
        https_only=config.cookie_secure,
    )

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("multipart/"):
            return PlainTextResponse("Unsupported media type", status_code=415)
        if "transfer-encoding" in request.headers:
            return PlainTextResponse("Length required", status_code=411)
        content_length = request.headers.get("content-length", "")
        if content_length.isdigit() and int(content_length) > MAX_BODY_BYTES:
            return PlainTextResponse("Request body too large", status_code=413)
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        return response

    @app.exception_handler(LoginRequired)
    def redirect_to_login(request: Request, exc: LoginRequired) -> RedirectResponse:
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(AdminRequired)
    def redirect_to_admin_login(
        request: Request, exc: AdminRequired
    ) -> RedirectResponse:
        return RedirectResponse("/admin/login", status_code=303)

    app.include_router(web_router)
    app.include_router(admin_router)
    return app
