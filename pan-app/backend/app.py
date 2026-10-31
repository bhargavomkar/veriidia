"""Veriidia's local Protean registration application."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse

from backend.registration import (
    AUTOMATED_SUBMISSION_PERMITTED,
    router as registration_router,
    shutdown as shutdown_registration,
)

ROOT = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        yield
    finally:
        await shutdown_registration()


app = FastAPI(title="Veriidia PAN Registration", docs_url=None, redoc_url=None, lifespan=lifespan)
app.include_router(registration_router)


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


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "provider": "protean",
        "liveProviderAutomation": True,
        "automatedSubmissionPermitted": AUTOMATED_SUBMISSION_PERMITTED,
    }
