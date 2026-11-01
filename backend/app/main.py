"""
Application factory and ASGI entry point.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db.database import init_db
from app.api.health import router as health_router
from app.api.graphs import router as graphs_router
from app.api.approvals import router as approvals_router
from app.api.live import router as live_router
from app.api.trips import presets_router
from app.api.trips import router as trips_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    # Import DB models so SQLModel registers their metadata before create_all
    import app.db.models  # noqa: F401
    init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(health_router)
    app.include_router(graphs_router)
    app.include_router(trips_router)
    app.include_router(presets_router)
    app.include_router(approvals_router)
    app.include_router(live_router)

    return app


app = create_app()
