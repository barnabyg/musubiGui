"""Same-origin run operations; clients select records rather than commands or paths."""

from contextlib import contextmanager
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from musubi_gui.runs import RunService


class PrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str


class QueueOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_ids: list[str]


class StopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force: bool = False
    confirmed: bool = False


class DeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: bool = False


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state_id: str
    additional_steps: int
    confirmed: bool = False


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

    @router.get("/queue")
    def queue() -> list[dict[str, Any]]:
        with run_errors():
            return service.queue()

    @router.post("/queue/order")
    def order(request: QueueOrder) -> list[dict[str, Any]]:
        with run_errors():
            service.reorder(request.run_ids)
            return service.queue()

    @router.get("/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        with run_errors():
            return service.get(run_id)

    @router.get("/{run_id}/logs")
    def logs(run_id: str) -> dict[str, Any]:
        with run_errors():
            return {"text": service.logs(run_id), "progress": service.progress(run_id)}

    @router.post("/{run_id}/retry")
    def retry(run_id: str) -> dict[str, Any]:
        with run_errors():
            return service.retry(run_id)

    @router.post("/{run_id}/start")
    def start(run_id: str) -> dict[str, Any]:
        with run_errors():
            return service.start(run_id)

    @router.post("/{run_id}/queue")
    def enqueue(run_id: str) -> dict[str, Any]:
        with run_errors():
            return service.enqueue(run_id)

    @router.post("/{run_id}/stop")
    def stop(run_id: str, request: StopRequest) -> dict[str, Any]:
        with run_errors():
            return service.stop(run_id, force=request.force, confirmed=request.confirmed)

    @router.get("/{run_id}/states")
    def states(run_id: str) -> list[dict[str, Any]]:
        with run_errors():
            return service.saved_states(run_id)

    @router.post("/{run_id}/restore-state")
    def restore(run_id: str, request: RestoreRequest) -> dict[str, Any]:
        with run_errors():
            return service.restore_state(
                run_id, request.state_id, request.additional_steps, confirmed=request.confirmed
            )

    @router.post("/{run_id}/reconcile")
    def reconcile(run_id: str) -> dict[str, Any]:
        with run_errors():
            return service.reconcile(run_id)

    @router.post("/{run_id}/terminate-orphan")
    def terminate_orphan(run_id: str, request: StopRequest) -> dict[str, Any]:
        with run_errors():
            return service.terminate_orphan(run_id, confirmed=request.confirmed)

    @router.post("/{run_id}/cancel")
    def cancel(run_id: str) -> dict[str, Any]:
        with run_errors():
            return service.cancel(run_id)

    @router.get("/{run_id}/delete-preview")
    def delete_preview(run_id: str) -> dict[str, Any]:
        with run_errors():
            return service.deletion_preview(run_id)

    @router.post("/{run_id}/delete")
    def delete(run_id: str, request: DeleteRequest) -> dict[str, Any]:
        with run_errors():
            return service.delete(run_id, confirmed=request.confirmed)

    return router
