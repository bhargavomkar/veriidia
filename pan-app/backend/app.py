"""Veriidia's local Protean registration application."""
from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)

from backend import database, registration as registration_service  # noqa: E402
from backend.registration import (
    AUTOMATED_SUBMISSION_PERMITTED,
    router as registration_router,
    shutdown as shutdown_registration,
)

CORS_ORIGINS = [origin.strip().rstrip("/") for origin in os.getenv("VERIIDIA_CORS_ORIGINS", "").split(",") if origin.strip()]
IDLE_TIMEOUT_SECONDS = max(60, int(os.getenv("REGISTRATION_SESSION_IDLE_TIMEOUT_SECONDS", "900")))


def validate_production_configuration() -> None:
    if os.getenv("APP_ENV", "development").casefold() != "production":
        return
    if not CORS_ORIGINS or "*" in CORS_ORIGINS:
        raise RuntimeError("Production requires exact HTTPS origins in VERIIDIA_CORS_ORIGINS.")
    if any(
        urlparse(origin).scheme != "https"
        or not urlparse(origin).netloc
        or urlparse(origin).path not in {"", "/"}
        or urlparse(origin).query
        or urlparse(origin).fragment
        or urlparse(origin).username
        or urlparse(origin).password
        for origin in CORS_ORIGINS
    ):
        raise RuntimeError("Every VERIIDIA_CORS_ORIGINS entry must be an HTTPS origin.")
    if not database.is_configured():
        raise RuntimeError("Production requires DATABASE_URL for registration session records.")


async def reap_idle_registration_session() -> None:
    while True:
        await asyncio.sleep(min(60, max(10, IDLE_TIMEOUT_SECONDS // 4)))
        state = registration_service.active
        if state and not state.closed and time.monotonic() - state.last_activity > IDLE_TIMEOUT_SECONDS:
            await shutdown_registration()


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_production_configuration()
    await database.initialize(required=os.getenv("APP_ENV", "development").casefold() == "production")
    cleanup_task = asyncio.create_task(reap_idle_registration_session())
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        await shutdown_registration()
        await database.close()


app = FastAPI(title="Veriidia PAN Registration", docs_url=None, redoc_url=None, lifespan=lifespan)
app.include_router(registration_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)


@app.middleware("http")
async def require_allowed_browser_origin(request: Request, call_next):
    production = os.getenv("APP_ENV", "development").casefold() == "production"
    protected_request = request.url.path.startswith("/api/registration/") and request.url.path != "/api/registration/viewer-authorized"
    if production and protected_request and request.method in {"POST", "DELETE"}:
        origin = request.headers.get("origin", "").rstrip("/")
        if origin not in CORS_ORIGINS:
            return JSONResponse(status_code=403, content={"detail": "This Veriidia origin is not authorized."})
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), payment=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self' data:; connect-src 'self' ws: wss:; frame-src 'self'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'"
    )
    return response


@app.get("/", include_in_schema=False)
async def home():
    return FileResponse(ROOT / "index.html")


@app.get("/registration", response_class=HTMLResponse, include_in_schema=False)
async def registration():
    return FileResponse(ROOT / "registration.html")


@app.get("/health")
@app.get("/api/health")
async def health():
    production = os.getenv("APP_ENV", "development").casefold() == "production"
    configured = database.is_configured()
    connected = await database.ping() if configured else False
    db_status = "connected" if connected else ("unavailable" if configured else "not_configured")
    status = "ok" if connected or not production else "degraded"
    return JSONResponse(status_code=200 if connected or not production else 503, content={
        "status": status,
        "provider": "protean",
        "liveProviderAutomation": True,
        "automatedSubmissionPermitted": AUTOMATED_SUBMISSION_PERMITTED,
        "databaseReady": connected,
        "databaseStatus": db_status,
    })


@app.get("/veriidia-config.js", include_in_schema=False)
async def local_frontend_config():
    return HTMLResponse(
        "window.VERIIDIA_CONFIG = { apiBaseUrl: '' };",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )
