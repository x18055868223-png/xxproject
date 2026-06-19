from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import __version__
from .cache import MetricsCache
from .config import Settings
from .scraper import ScraplingScraper

bearer = HTTPBearer(auto_error=False)


class RefreshSectionParam(str, Enum):
    all = "all"
    gex_board = "gex_board"
    gamma_exposure = "gamma_exposure"
    volatility = "volatility"
    flow = "flow"


def create_app(settings: Settings | None = None, cache: Any | None = None) -> FastAPI:
    settings = settings or Settings()
    cache = cache or MetricsCache(
        ScraplingScraper(settings),
        cache_file=settings.cache_file,
        history_file=settings.history_file,
        rank_lookback_days=settings.rank_lookback_days,
        refresh_interval_seconds=settings.refresh_interval_seconds,
    )
    stop_event = asyncio.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        refresh_task: asyncio.Task | None = None
        if hasattr(cache, "load"):
            await cache.load()
        if settings.refresh_on_startup and hasattr(cache, "refresh"):
            await cache.refresh("all")
        if settings.enable_background_refresh and hasattr(cache, "refresh_periodically"):
            refresh_task = asyncio.create_task(cache.refresh_periodically(stop_event))
        try:
            yield
        finally:
            stop_event.set()
            if refresh_task:
                refresh_task.cancel()
                try:
                    await refresh_task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(title="GEX Monitor BTC Metric API", version=__version__, lifespan=lifespan)

    async def require_token(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> None:
        if not credentials or credentials.scheme.lower() != "bearer":
            raise _unauthorized()
        if not secrets.compare_digest(credentials.credentials, settings.api_token):
            raise _unauthorized()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/v1/info", dependencies=[Depends(require_token)])
    async def info() -> dict[str, Any]:
        return await cache.get_info()

    @app.post("/v1/refresh", dependencies=[Depends(require_token)])
    async def refresh(
        section: RefreshSectionParam = Query(default=RefreshSectionParam.all),
    ) -> dict[str, Any]:
        return await cache.refresh(section.value)

    return app


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API token",
        headers={"WWW-Authenticate": "Bearer"},
    )


app = create_app()
