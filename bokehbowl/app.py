"""App factory: wires config, database engine, and mailer into the FastAPI app."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine
from starlette.middleware.sessions import SessionMiddleware

from bokehbowl.admin import AdminRequired
from bokehbowl.admin import router as admin_router
from bokehbowl.config import AppConfig
from bokehbowl.mailer import Mailer
from bokehbowl.web import LoginRequired
from bokehbowl.web import router as web_router

TEMPLATES_DIR = Path(__file__).parent / "templates"
INSTANCE_TEMPLATES_DIR = Path("instance/templates")


def create_app(config: AppConfig, engine: Engine, mailer: Mailer) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config = config
    app.state.engine = engine
    app.state.mailer = mailer
    templates = Jinja2Templates(directory=[INSTANCE_TEMPLATES_DIR, TEMPLATES_DIR])
    templates.env.globals.update(
        operator_name=config.operator_name,
        operator_contact=config.operator_contact,
    )
    app.state.templates = templates

    app.add_middleware(
        SessionMiddleware,
        secret_key=config.session_secret,
        same_site="lax",
        https_only=config.cookie_secure,
    )

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
