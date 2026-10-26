"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from sqlalchemy import text

from fastapi.middleware.cors import CORSMiddleware

from app.api.admin.router import router as admin_router
from app.api.connector.agent import router as connector_agent_router
from app.config import get_settings
from app.database import SessionLocal, init_db
from app.security.rate_limit import RateLimitMiddleware
from app.services.bootstrap import ensure_bootstrap
from app.workers.background import start_background_worker, stop_background_worker

ROOT = Path(__file__).resolve().parents[1]
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("brixigo.cloud")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.assert_secure_for_environment()
    init_db()
    db = SessionLocal()
    try:
        ensure_bootstrap(db, settings)
        db.commit()
    finally:
        db.close()
    start_background_worker()
    yield
    stop_background_worker()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    origins = settings.cors_origin_list()
    # Same-origin admin UI works without CORS. Wildcard is never used.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["http://127.0.0.1"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Connector-Id", "X-Organisation-Id", "X-Device-Token", "X-Connector-Version"],
    )
    app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.rate_limit_per_minute)
    if settings.is_production:
        logger.info("Cloud running in PRODUCTION mode")
    else:
        logger.info("Cloud running in DEVELOPMENT mode (CORS origins=%s)", origins)

    # Admin dashboard APIs
    app.include_router(admin_router, prefix=settings.admin_api_prefix)

    # On-premise connector agent contract (must match connector/api/cloud_client.py)
    app.include_router(connector_agent_router, prefix=settings.api_prefix)

    templates = Jinja2Templates(directory=str(ROOT / "templates"))
    static_dir = ROOT / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=HTMLResponse)
    @app.get("/admin", response_class=HTMLResponse)
    def admin_dashboard(request: Request):
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {"app_name": settings.app_name},
        )

    @app.get("/liveness")
    @app.get("/health")
    @app.get("/api/health")
    def liveness():
        """Process liveness — cheap, no database. Connector health checks use this."""
        return {
            "status": "ok",
            "service": "brixigo-cloud",
            "version": settings.app_version,
            "check": "liveness",
        }

    @app.get("/readiness")
    def readiness():
        """Dependency readiness. Connector must not poll this every 30s."""
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            return {
                "status": "ok",
                "service": "brixigo-cloud",
                "version": settings.app_version,
                "check": "readiness",
            }
        except Exception as exc:
            from fastapi.responses import JSONResponse

            logger.warning("Readiness check failed: %s", exc)
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unavailable",
                    "service": "brixigo-cloud",
                    "check": "readiness",
                },
            )
        finally:
            db.close()

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return HTMLResponse(status_code=204)

    return app


app = create_app()
