"""Same-origin loopback API and bundled browser application."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from musubi_gui.datasets_api import dataset_router
from musubi_gui.diagnostics import diagnostics
from musubi_gui.jobs import jobs_router
from musubi_gui.runs import RunService
from musubi_gui.runs_api import runs_router
from musubi_gui.settings import Settings, load_settings, save_settings, state_root, validate_paths


def create_app(
    state: Path | None = None,
    assets: Path | None = None,
    *,
    fixture_mode: bool = False,
    device_path: Path | None = None,
) -> FastAPI:
    root = state or state_root()
    service = RunService(root, fixture_mode=fixture_mode, device_path=device_path, autostart=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        service.start_worker()
        try:
            yield
        finally:
            service.close()

    app = FastAPI(title="Musubi GUI", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_only(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method not in {"GET", "HEAD"}:
            origin = request.headers.get("origin", "")
            if (origin and urlsplit(origin).netloc != request.headers.get("host")) or request.headers.get(
                "sec-fetch-site"
            ) == "cross-site":
                return JSONResponse({"detail": "Use the local application tab for this action."}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "Expected application/json."}, status_code=415)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; media-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    def current() -> Settings:
        try:
            return load_settings(root)
        except (ValueError, OSError) as error:
            raise HTTPException(
                409, "Cannot read saved settings. Preserve settings.json and repair it or restore a compatible version."
            ) from error

    @app.get("/api/settings")
    def get_settings() -> Settings:
        return current()

    @app.put("/api/settings")
    def put_settings(settings: Settings) -> Settings:
        current()  # Refuse to overwrite corrupt data or an unknown future format.
        try:
            validate_paths(settings)
            save_settings(root, settings)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except OSError as error:
            raise HTTPException(
                500,
                "Settings could not be saved; check state-directory permissions and free disk. Your edits remain in this tab.",
            ) from error
        return settings

    @app.post("/api/diagnostics")
    def post_diagnostics() -> dict[str, Any]:
        return diagnostics(current())

    app.include_router(jobs_router(root, fixture_mode=fixture_mode))
    app.include_router(runs_router(service))
    app.include_router(dataset_router(root))
    app.mount("/", StaticFiles(directory=assets or Path(__file__).parent / "web", html=True), name="ui")
    return app
