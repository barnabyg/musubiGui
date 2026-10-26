"""Same-origin run operations; clients select records rather than commands or paths."""

from contextlib import contextmanager
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from musubi_gui.runs import RunService


class PrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str


@contextmanager
def run_errors() -> Iterator[None]:
    try:
        yield
    except (ValueError, KeyError) as error:
        raise HTTPException(409, str(error)) from error
    except OSError as error:
        raise HTTPException(
            500, "Run files could not be accessed. Check permissions/free disk; recorded inputs are preserved."
        ) from error


def runs_router(service: RunService) -> APIRouter:
    router = APIRouter(prefix="/api/runs")

    @router.get("")
    def get_runs() -> list[dict[str, Any]]:
        with run_errors():
            return service.list()

    @router.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        return {
            "fixture": service.fixture_mode,
            "gpu_owner": service.device.owner(),
            "resume": "Saved-state restoration needs recipe qualification; LoRA weights alone are not resumable state.",
        }

    @router.post("/prepare")
    def prepare(request: PrepareRequest) -> dict[str, Any]:
        with run_errors():
            return service.prepare(request.job_id)

    @router.get("/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        with run_errors():
            return service.get(run_id)

    @router.get("/{run_id}/logs")
    def logs(run_id: str) -> dict[str, str]:
        with run_errors():
            return {"text": service.logs(run_id)}

    @router.post("/{run_id}/retry")
    def retry(run_id: str) -> dict[str, Any]:
        with run_errors():
            return service.retry(run_id)

    @router.post("/{run_id}/start")
    def start(run_id: str) -> dict[str, Any]:
        with run_errors():
            return service.start(run_id)

    @router.post("/{run_id}/cancel")
    def cancel(run_id: str) -> dict[str, Any]:
        with run_errors():
            return service.cancel(run_id)

    return router
