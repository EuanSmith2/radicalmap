"""Local web UI for the v2 store.

Read-only over the corpus, with exactly one write path: the gold annotation
endpoint, which delegates to classify.annotate.record() so the "model output is
not ground truth" guard lives in one place rather than being re-implemented
here.

Binds to 127.0.0.1 by default. The corpus contains pseudonymised commenter
handles and material selected precisely because it is extremist; exposing that
on a LAN interface by accident is a governance incident, not a convenience, so
the bind address has to be passed explicitly to change it.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .routers import annotate as annotate_router
from .routers import data as data_router

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app() -> FastAPI:
    app = FastAPI(
        title="RadicalMap",
        description="Local analysis console for the RadicalMap v2 corpus.",
        version="2.0.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.include_router(data_router.router, prefix="/api")
    app.include_router(annotate_router.router, prefix="/api")

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        from ..db import engine
        return {"ok": True, "db": str(engine.db_path())}

    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

    return app


app = create_app()
