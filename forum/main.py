from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException

from .config import Settings
from .content import render_body, render_quote
from .db import make_database
from .limits import RequestSizeLimit
from .routes import router
from .security import COOKIE_NAME, hash_password

PACKAGE = Path(__file__).resolve().parent


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    engine, factory = make_database(settings)

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()

    app = FastAPI(
        title="Commons", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.settings = settings
    app.state.session_factory = factory
    app.state.engine = engine
    app.state.dummy_password_hash = hash_password("unused-login-comparison")
    templates = Jinja2Templates(directory=PACKAGE / "templates")
    templates.env.filters["body"] = render_body
    templates.env.filters["quote"] = render_quote
    app.state.templates = templates
    app.add_middleware(RequestSizeLimit, max_bytes=settings.max_upload_bytes + 65536)

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        if hasattr(request.state, "session_cookie"):
            response.set_cookie(
                COOKIE_NAME,
                request.state.session_cookie,
                httponly=True,
                secure=settings.secure_cookies,
                samesite="lax",
                max_age=settings.session_hours * 3600,
                path="/",
            )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            status_code=exc.status_code,
            context={"status": exc.status_code, "message": exc.detail},
            headers=exc.headers,
        )

    app.mount("/static", StaticFiles(directory=PACKAGE / "static"), name="static")
    app.include_router(router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app
