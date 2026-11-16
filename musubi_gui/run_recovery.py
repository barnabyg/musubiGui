"""Reconciling a Run's recorded processes after a restart or exit, and terminating confirmed orphans."""

import time
from pathlib import Path
from typing import Any

import psutil

from musubi_gui.processes import identity_status, process_identity, terminate_owned
from musubi_gui.run_store import DeviceLease, RunStore, utc_now
from musubi_gui.windows_job import ProcessJob

# Runs whose recorded work may still be alive, so they are reconciled before anything new starts.
RECONCILED_STATUSES = frozenset(
    {
        "preparing",
        "training",
        "generating",
        "stopping",
        "interrupted",
        "orphaned",
        "uncertain",
    }
)


def reconcile(store: RunStore, device: DeviceLease, root: Path, run_id: str) -> dict[str, Any]:
    run = store.get(run_id)
    if run["status"] not in RECONCILED_STATUSES:
        return run
    controller = run.get("controller")
    if (
        run["status"] in {"preparing", "training", "generating", "stopping"}
        and controller
        and identity_status(controller) != "dead"
    ):
        return run
    identities = list(run.get("processes", []))
    uncertain = False
    for name in run.get("job_objects", []):
        try:
            job = ProcessJob(name, create=False)
            try:
                for pid in job.pids():
                    try:
                        identities.append(process_identity(pid))
                    except psutil.NoSuchProcess:
                        pass
            finally:
                job.close()
        except (OSError, ValueError, psutil.AccessDenied):
            uncertain = True
    live = [identity for identity in identities if identity_status(identity) == "alive"]
    uncertain = uncertain or any(identity_status(identity) == "uncertain" for identity in identities)
    owner = device.owner()
    if owner and owner["run_id"] == run_id and owner["state"] == str(root.resolve()):
        # Protocol 3 assigns the named Job Object atomically during process creation.
        # A known empty job proves that no process survived, even before PID persistence.
        # Protocol 2 remains readable, but its old unassigned suspended-child window is uncertain.
        observable_launch = owner.get("launch_protocol") == 3 and bool(run.get("job_objects"))
        uncertain = uncertain or (bool(owner.get("launch_pending")) and not observable_launch)
    if live or uncertain:
        status = "uncertain" if uncertain else "orphaned"
        device.update(run_id, identities)
        return store.update(
            run_id,
            lambda r: r.update(
                status=status,
                processes=identities,
                error="Recorded work is still alive or cannot be safely identified. No replacement GPU work will start. Check again or confirm termination of known owned processes.",
            ),
        )
    device.release(run_id)
    return store.update(run_id, _finish)


def _finish(record: dict[str, Any]) -> None:
    # Every recorded process is gone, so a requested stop was observed rather than assumed.
    # An already interrupted run stays retryable, as it was before stops were reconciled.
    requested = record.get("stop_requested") if record["status"] != "interrupted" else None
    for stage in record["stages"]:
        if stage["status"] == "running":
            stage.update(status="stopped" if requested else "interrupted", finished=utc_now())
    if requested:
        record.update(
            status="stopped",
            stop_kind=requested,
            processes=[],
            error=None,
            finished=utc_now(),
            execution_end_unknown=True,
        )
        return
    record.update(
        status="interrupted",
        processes=[],
        error="The original processes are gone. Inspect outputs/state, then prepare a new Run from frozen inputs or restore validated optimizer state as a new segment.",
    )


def terminate_orphan(store: RunStore, device: DeviceLease, root: Path, run_id: str) -> dict[str, Any]:
    """Force-terminate a reconciled Run's recovered processes; the caller has the owner's confirmation."""
    run = reconcile(store, device, root, run_id)
    if run["status"] not in {"orphaned", "uncertain"}:
        raise ValueError("This run has no recovered orphan process to terminate.")
    for name in run.get("job_objects", []):
        job = ProcessJob(name, create=False)
        try:
            job.terminate()
        finally:
            job.close()
    terminate_owned(run.get("processes", []), force=True)
    if run.get("stop_requested"):
        # A requested stop is completed by this confirmed force termination, whatever kind was asked for.
        store.update(run_id, lambda r: r.update(stop_requested="force"))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if all(identity_status(identity) == "dead" for identity in run.get("processes", [])):
            break
        time.sleep(0.05)
    return reconcile(store, device, root, run_id)
