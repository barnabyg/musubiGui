"""Browser-independent preparation from frozen, provenance-bearing plans."""

import builtins
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from musubi_gui.checkpoint_retention import retained_states
from musubi_gui.job_models import Job
from musubi_gui.job_store import JobStore
from musubi_gui.job_validation import configuration_errors
from musubi_gui.managed_checkout import checkout_gate
from musubi_gui.processes import process_identity
from musubi_gui.recipe import Stage
from musubi_gui.redaction import redact
from musubi_gui.resume_compat import ResumeIncompatible, compatibility, refusal
from musubi_gui.resume_refresh import Refresh, inspect_refresh, refreshed_snapshot
from musubi_gui.run_cache import reuse_cache, seed_refreshed
from musubi_gui.run_chains import chain_fields, chain_of
from musubi_gui.run_control import request as request_control
from musubi_gui.run_control import unsaved_steps
from musubi_gui.run_deletion import failure_message, folder_bytes, output_index_file, plan_deletion, remove_folder
from musubi_gui.run_estimate import freeze_training_timing
from musubi_gui.run_execution import ExecutionContext, RunExecution
from musubi_gui.run_progress import estimated_remaining, parse_progress
from musubi_gui.run_recovery import RECONCILED_STATUSES, reconcile, terminate_orphan
from musubi_gui.run_restore import create_segment, restored_segment, resume_step
from musubi_gui.run_retention import RetentionScheduler
from musubi_gui.run_retry import retried_plan
from musubi_gui.run_store import (
    DeviceLease,
    RunStore,
    blocks_revision,
    is_active,
    preparation_transaction,
    utc_now,
)
from musubi_gui.saved_state import discover_states
from musubi_gui.settings import Settings, atomic_json, load_settings, managed_settings
from musubi_gui.snapshots import identify_snapshot, materialize_snapshot, verify_inputs
from musubi_gui.step_offset import numbers_continuously, segment_step_offset
from musubi_gui.wan_pair_runtime import is_paired, paired_saved_states, stage_log


@dataclass
class ExecutionPlan:
    job: dict[str, Any]
    settings: Settings
    manifest: dict[str, Any]
    directory: Path
    cache: Path
    stages: list[Stage]
    fingerprint: str
    stage_keys: dict[str, str] = field(default_factory=dict)
    input_files: dict[str, str] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)


class RunService:
    def __init__(
        self,
        root: Path,
        *,
        device_path: Path | None = None,
        fixture_mode: bool = False,
        autostart: bool = True,
        descendant_grace: float = 30.0,
    ):
        self.root = root
        self.fixture_mode = fixture_mode
        # Seconds that descendants may take to exit after their stage launcher before the run is orphaned.
        self.descendant_grace = descendant_grace
        self.store = RunStore(root)
        self.device = DeviceLease(root, device_path)
        self.identity = process_identity()
        self.identity["instance"] = uuid.uuid4().hex
        self.stopping = threading.Event()
        self.retention = RetentionScheduler(self.store, self.stopping)
        self.thread = threading.Thread(target=self._schedule, name="musubi-worker", daemon=True)
        if autostart:
            self.start_worker()

    def start_worker(self) -> None:
        for run in self.list():
            if run["status"] in RECONCILED_STATUSES:
                self.reconcile(run["id"])
        self.retention.start()
        self.thread.start()

    def list(self) -> list[dict[str, Any]]:
        return self.store.list()

    def summaries(self) -> builtins.list[dict[str, Any]]:
        return self.store.summaries()

    def get(self, run_id: str) -> dict[str, Any]:
        return self.store.get(run_id)

    def prepare(self, job_id: str, *, start: bool = False, reviewed_revision: int | None = None) -> dict[str, Any]:
        with nullcontext() if self.fixture_mode else checkout_gate(), preparation_transaction(self.root):
            inputs = identify_snapshot(
                self.root, job_id, fixture_mode=self.fixture_mode, reviewed_revision=reviewed_revision
            )
            for previous in self.list():
                if blocks_revision(previous, inputs.fingerprint):
                    return {**(self._start_when_prepared(previous["id"]) if start else previous), "existing": True}
            plan = ExecutionPlan(**materialize_snapshot(inputs))
            return {**self.submit(plan, start_after_prepare=start), "existing": False}

    def _start_when_prepared(self, run_id: str) -> dict[str, Any]:
        def change(record: dict[str, Any]) -> None:
            if record["status"] in {"pending_preparation", "preparing"}:
                record.update(start_after_prepare=True)
            elif record["status"] == "prepared" and any(stage["name"] == "train" for stage in record["stages"]):
                record.update(status="pending_training", phase="train", order=time.time_ns())

        return self.store.update(run_id, change)

    def start(self, run_id: str) -> dict[str, Any]:
        return self._request_training(run_id, allow_wait=False)

    def enqueue(self, run_id: str) -> dict[str, Any]:
        return self._request_training(run_id, allow_wait=True)

    def queue(self) -> builtins.list[dict[str, Any]]:
        return sorted(
            [run for run in self.list() if run["status"] in {"pending_preparation", "pending_training"}],
            key=lambda run: run["order"],
        )

    def reorder(self, run_ids: builtins.list[str]) -> None:
        self.store.reorder(run_ids)

    def _verify_inputs(self, run: dict[str, Any]) -> None:
        # API snapshots carry checkout provenance. Trusted CPU execution adapters may omit it.
        settings = Settings.model_validate(run["settings"])
        if (
            not self.fixture_mode
            and run.get("provenance", {}).get("checkout")
            and settings != managed_settings(settings)
        ):
            raise ValueError(
                "This Run uses an external training installation. Prepare a new Run with the managed checkout."
            )
        verify_inputs(run)

    def _request_training(self, run_id: str, *, allow_wait: bool) -> dict[str, Any]:
        run = self.get(run_id)
        if run["status"] in {"pending_training", "training", "generating", "completed"}:
            return run
        if run["status"] != "prepared":
            raise ValueError("Prepare this immutable run successfully before starting training.")
        owner = self.device.owner()
        if owner and owner["run_id"] != run_id and not allow_wait:
            raise ValueError("The GPU is owned by another operation. Wait for it to finish.")
        self._verify_inputs(run)
        if not any(stage["name"] == "train" for stage in run["stages"]):
            raise ValueError("This run has no supported training stage.")

        def change(record: dict[str, Any]) -> None:
            if record["status"] != "prepared":
                return
            record.update(status="pending_training", phase="train", order=time.time_ns())

        return self.store.update(run_id, change)

    def submit(self, plan: ExecutionPlan, *, start_after_prepare: bool = False) -> dict[str, Any]:
        """Trusted local adapter seam; API requests never supply command arrays."""
        data: dict[str, Any] = {
            "version": 1,
            "id": uuid.uuid4().hex,
            "created": utc_now(),
            "execution_started": None,
            "job": plan.job,
            "settings": plan.settings.model_dump(),
            "manifest": plan.manifest,
            "directory": str(plan.directory.resolve()),
            "cache": str(plan.cache.resolve()),
            "fingerprint": plan.fingerprint,
            "status": "pending_preparation",
            "phase": "prepare",
            "order": time.time_ns(),
            "stages": [
                freeze_training_timing({**stage.record(), "status": "pending"}, plan.job["family"], plan.directory)
                for stage in plan.stages
            ],
            "processes": [],
            "error": None,
            "fixture": self.fixture_mode,
            "stage_keys": plan.stage_keys,
            "input_files": plan.input_files,
            "provenance": plan.provenance,
            # Stored with the Run, so training still follows if the browser closes or the backend restarts.
            "start_after_prepare": start_after_prepare,
        }
        plan.directory.mkdir(parents=True, exist_ok=True)
        plan.cache.mkdir(parents=True, exist_ok=True)
        atomic_json(
            plan.directory / "inputs.json", {"job": plan.job, "settings": data["settings"], "manifest": plan.manifest}
        )
        return self.store.create(data)

    def logs(self, run_id: str) -> str:
        run = self.get(run_id)
        directory = Path(run["directory"])
        parts = []
        for stage in run["stages"]:
            file = directory / stage_log(stage)
            if file.is_file():
                with file.open("rb") as stream:
                    stream.seek(max(0, file.stat().st_size - 256_000))
                    label = stage_log(stage).removesuffix(".log")
                    parts.append(f"--- {label} ---\n" + redact(stream.read().decode("utf-8", errors="replace")))
        return "\n".join(parts)

    def progress(self, run_id: str) -> dict[str, Any] | None:
        run = self.get(run_id)
        training = [stage for stage in run["stages"] if stage["name"] == "train" and stage["status"] != "pending"]
        stage: dict[str, Any] = training[-1] if training else {"name": "train"}
        file = Path(run["directory"]) / stage_log(stage)
        if not file.is_file():
            return None
        with file.open("rb") as stream:
            stream.seek(max(0, file.stat().st_size - 64_000))
            progress = parse_progress(stream.read().decode("utf-8", errors="replace"))
        if progress:
            progress["unsaved_steps"] = unsaved_steps(run, stage, progress["step"])
        segment = run.get("segment", {})
        # Segments restored before continuous numbering report their own steps and requested additional steps.
        continuous = numbers_continuously(run)
        if progress and stage.get("training"):
            phase = stage["training"]
            from musubi_gui.wan_pair import phase_counts

            absolute = phase["start"] + progress["step"]
            progress.update(
                expert=phase["expert"],
                phase_step=progress["step"],
                phase_total=progress["total"],
                step=absolute - (0 if continuous else segment.get("schedule_start", 0)),
                total=(
                    (segment.get("schedule_start", 0) if continuous else 0)
                    + sum(item["training"]["steps"] for item in run["stages"] if item.get("training"))
                    if phase.get("per_expert")
                    else segment.get("target_steps", segment.get("additional_steps", run["job"]["config"]["steps"]))
                ),
                expert_steps=phase_counts(phase, progress["step"]),
                eta_scope="phase",
            )
        elif progress and (offset := segment_step_offset(run, stage)):
            # The trainer counts this segment from zero; show the global step and target total.
            progress.update(
                phase_step=progress["step"],
                phase_total=progress["total"],
                step=offset + progress["step"],
                total=offset + progress["total"],
            )
        if progress:
            progress["estimated_remaining_seconds"] = estimated_remaining(progress)
            if stage.get("training", {}).get("per_expert"):
                initial = {} if continuous else run.get("pair_sources", {})
                initial_steps = min(initial.get(expert, {}).get("optimizer_step", 0) for expert in ("low", "high"))
                last = next(item["training"] for item in reversed(run["stages"]) if item.get("training"))
                target = min(phase_counts(last, last["steps"]).values())
                progress["step"] = min(progress["expert_steps"].values()) - initial_steps
                progress["total"] = target - initial_steps
        return progress

    def close(self) -> None:
        self.stopping.set()
        self.retention.wake()
        if self.thread.is_alive():
            self.thread.join(timeout=20)
        self.retention.join()

    def retry(self, run_id: str) -> dict[str, Any]:
        previous = self.get(run_id)
        plan = ExecutionPlan(**retried_plan(previous, self._verify_inputs))
        retried = self.submit(plan)
        # Retrying a restored segment resumes the same state, so it keeps the segment's step numbering.
        segment = {"segment": previous["segment"]} if "segment" in previous else {}
        # A refreshed segment's retry copies the same unchanged items' caches from the same source Run.
        refresh = {"refresh": previous["refresh"]} if "refresh" in previous else {}
        return self.store.update(retried["id"], lambda r: r.update(retry_of=run_id, **segment, **refresh))

    def reconcile(self, run_id: str) -> dict[str, Any]:
        return reconcile(self.store, self.device, self.root, run_id)

    def terminate_orphan(self, run_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("Explicit confirmation is required to terminate recovered owned processes.")
        return terminate_orphan(self.store, self.device, self.root, run_id)

    def cancel(self, run_id: str) -> dict[str, Any]:
        run = self.get(run_id)
        if run["status"] in {"pending_preparation", "pending_training"}:
            return self.store.update(run_id, lambda r: r.update(status="cancelled", finished=utc_now()))
        if run["status"] != "preparing":
            raise ValueError("Only queued preparation, queued training or active preparation can be cancelled.")
        return self.store.update(run_id, lambda r: r.update(cancel_requested=True))

    def stop(self, run_id: str, *, force: bool = False, confirmed: bool = False) -> dict[str, Any]:
        if force and not confirmed:
            raise ValueError("Force termination requires explicit confirmation; unsaved checkpoints/state may be lost.")
        run = self.get(run_id)
        if run["status"] not in {"training", "generating", "stopping"}:
            raise ValueError("Only an active training or sampling process can be stopped.")
        return self.store.update(
            run_id, lambda r: r.update(status="stopping", stop_requested="force" if force else "graceful")
        )

    def control(self, run_id: str, action: str) -> dict[str, Any]:
        """Save now, Sample now or Save and stop at the training process's next step."""
        return request_control(self.store, run_id, action)

    def saved_states(self, run_id: str) -> builtins.list[dict[str, Any]]:
        run = self.get(run_id)
        if run["status"] not in {"completed", "stopped", "failed", "cancelled", "interrupted"}:
            return []
        if run.get("processes"):
            raise ValueError("Choose Check for leftover processes before inspecting saved state.")
        self.retain_checkpoints(run_id)
        run = self.get(run_id)
        states = paired_saved_states(run) if is_paired(run) else discover_states(run)
        states = retained_states(run, states)
        for state in states:
            if state["valid"]:
                state["resume_step"] = resume_step(run, state)
        self.store.update(run_id, lambda r: r.update(saved_states=states))
        return states

    def retain_checkpoints(self, run_id: str) -> None:
        """Apply the frozen Run's policy; failure preserves training and is visible for repair."""
        self.retention.retain(run_id)

    def request_retention(self, run_id: str) -> None:
        """Coalesce work without delaying process supervision or Stop signalling."""
        self.retention.request(run_id)

    def restore_state(
        self, run_id: str, state_id: str, target_steps: int, *, confirmed: bool = False
    ) -> dict[str, Any]:
        """Queue a new segment that trains `state` until global step `target_steps`."""
        if not confirmed:
            raise ValueError(
                "Please confirm restoration as a new segment: upstream restores optimizer state but restarts the data position."
            )
        original = self.get(run_id)
        self._verify_inputs(original)
        state = next((state for state in self.saved_states(run_id) if state["id"] == state_id), None)
        if not state or not state["valid"]:
            raise ValueError(
                "Saved state is incomplete or changed. A LoRA checkpoint alone is not resumable optimizer state."
            )
        if state.get("paired"):
            from musubi_gui.wan_pair_restore import restored_pair

            record = restored_pair(original, state, target_steps)
        else:
            record = restored_segment(original, state, target_steps)
        create_segment(self.store, record, run_id)
        return self.enqueue(record["id"])

    def resume_check(
        self,
        run_id: str,
        job_revision: int | None = None,
        target_steps: int | None = None,
        state_id: str | None = None,
    ) -> dict[str, Any]:
        """Whether a revision of the Run's Job (the latest by default) can resume a valid saved state.

        The state is `state_id`, or the latest valid one; the total is `target_steps`, or the revision's steps.
        """
        _, state, _, result = self._resume_plan(run_id, job_revision, target_steps, state_id)
        return {**result, "run_id": run_id, "state_id": state["id"], "state_name": state["name"]}

    def resume(
        self, run_id: str, job_revision: int, target_steps: int | None = None, state_id: str | None = None
    ) -> dict[str, Any]:
        """Queue a new segment that resumes a saved state with `job_revision` of the Run's Job.

        Its training and sampling commands come from that revision. The frozen dataset and caches are reused, unless
        the datasets changed: then the segment snapshots them again and prepares the caches whose inputs changed
        before it trains. A revision that changes what the state was trained with raises ResumeIncompatible naming
        the fields.
        """
        original, state, revision, result = self._resume_plan(run_id, job_revision, target_steps, state_id)
        target = result["target_steps"]
        if not result["compatible"]:
            raise ResumeIncompatible(refusal(result))
        changes = result["changes"]
        refresh = self._refresh(original, revision) if result["reprepare"] else None
        if state.get("paired"):
            from musubi_gui.wan_pair_restore import restored_pair

            record = restored_pair(original, state, target, revision=revision, changes=changes, refresh=refresh)
        else:
            record = restored_segment(original, state, target, revision=revision, changes=changes, refresh=refresh)
        create_segment(self.store, record, run_id)
        # A refreshed segment is queued to prepare, and trains after it.
        return self.get(record["id"]) if record.get("refresh") else self.enqueue(record["id"])

    def _refresh(self, original: dict[str, Any], revision: Job) -> Callable[[Path], Refresh]:
        """Snapshot `revision`'s datasets into a segment folder, as preparing a Run does."""

        def snapshot(directory: Path) -> Refresh:
            with nullcontext() if self.fixture_mode else checkout_gate(), preparation_transaction(self.root):
                return refreshed_snapshot(self.root, original, revision, directory, fixture_mode=self.fixture_mode)

        return snapshot

    def _resume_plan(
        self, run_id: str, job_revision: int | None, target_steps: int | None, state_id: str | None
    ) -> tuple[dict[str, Any], dict[str, Any], Job, dict[str, Any]]:
        """The Run, the chosen valid state, the revision and their compatibility."""
        original = self.get(run_id)
        jobs = JobStore(self.root)
        job_id = original["job"]["id"]
        if jobs.is_archived(job_id):
            raise ValueError("This job is archived. Restore it from Jobs before resuming with one of its revisions.")
        try:
            revision = jobs.get(job_id, job_revision)
        except FileNotFoundError as error:
            raise ValueError(
                f"Revision {job_revision} of this Run's Job doesn't exist."
                if job_revision
                else "This Run's Job is gone."
            ) from error
        videos = any(item["kind"] == "video" for item in original["manifest"]["items"])
        if errors := configuration_errors(revision, has_videos=videos):
            raise ValueError(f"Revision {revision.revision} can't train: " + " ".join(errors))
        self._verify_inputs(original)
        states = [state for state in self.saved_states(run_id) if state["valid"]]
        if not states:
            raise ValueError(
                "This Run has no valid saved state to resume. Resume after it stops or completes with a saved state."
            )
        if state_id is None:
            # The furthest-trained state; the last listed of equals is the latest save.
            state = max(reversed(states), key=lambda item: item["resume_step"])
        else:
            found = next((item for item in states if item["id"] == state_id), None)
            if found is None:
                raise ValueError(
                    "Saved state is incomplete, changed or missing. Inspect saved states and choose again."
                )
            state = found
        target = target_steps if target_steps is not None else revision.config.steps
        contents = inspect_refresh(self.root, original, revision, fixture_mode=self.fixture_mode)
        result = compatibility(
            original["job"],
            revision.model_dump(),
            saved_step=resume_step(original, state),
            target_steps=target,
            **contents,
        )
        return original, state, revision, result

    def deletion_preview(self, run_id: str) -> dict[str, Any]:
        run = self.reconcile(run_id)
        others = [other for other in self.list() if other["id"] != run_id]
        try:
            plan = plan_deletion(run, others, load_settings(self.root))
        except ValueError as refusal:
            return {"run_id": run_id, "refusal": str(refusal)}
        size = sum(folder_bytes(Path(folder)) for folder in plan["folders"])
        return {**plan, "run_id": run_id, "refusal": None, "bytes": size}

    def chain_deletion_preview(self, run_id: str) -> dict[str, Any]:
        """What deleting the whole training `run_id` belongs to removes: every segment, or why it can't be deleted."""
        for member in chain_of(self.list(), run_id).segments:
            self.reconcile(member["id"])
        runs = self.list()
        chain = chain_of(runs, run_id)
        fields = chain_fields(runs)
        segments = [
            {
                "id": member["id"],
                "status": member["status"],
                "job": {key: member["job"][key] for key in ("name", "revision")},
                "position": fields[member["id"]]["position"],
                "steps": fields[member["id"]]["steps"],
            }
            for member in chain.segments
        ]
        base = {"run_id": run_id, "chain": chain.id, "segments": segments}
        try:
            plan = self._chain_plan(chain.segments, runs)
        except ValueError as refusal:
            return {**base, "refusal": str(refusal)}
        size = sum(folder_bytes(Path(folder)) for folder in plan["folders"])
        return {**base, **plan, "refusal": None, "bytes": size}

    def _chain_plan(
        self, segments: builtins.list[dict[str, Any]], runs: builtins.list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Every segment's deletion plan together, or a ValueError naming the first segment that refuses it."""
        for member in segments:
            if is_active(member):
                if member.get("processes"):
                    raise ValueError(
                        f"Segment {member['id'][:8]} still records processes. "
                        "Choose Check for leftover processes on it before deleting this training."
                    )
                raise ValueError(
                    f"Segment {member['id'][:8]} is active ({member['status']}). "
                    "Stop or cancel it before deleting this training."
                )
        ids = {member["id"] for member in segments}
        outside = [run for run in runs if run["id"] not in ids]
        settings = load_settings(self.root)
        folders: builtins.list[str] = []
        shared: builtins.list[str] = []
        dependents: builtins.list[dict[str, Any]] = []
        for member in segments:
            plan = plan_deletion(member, outside, settings)
            folders += [folder for folder in plan["folders"] if folder not in folders]
            if plan["shared_cache"] and plan["shared_cache"] not in shared:
                shared.append(plan["shared_cache"])
            dependents += [item for item in plan["dependents"] if item not in dependents]
        return {
            "folders": [folder for folder in folders if folder not in shared],
            "shared_cache": shared[0] if shared else None,
            "dependents": dependents,
        }

    def delete_chain(self, run_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        """Delete every segment of the training `run_id` belongs to, latest first, so each goes as a leaf.

        Nothing is removed unless every segment can be deleted; a delete that fails partway stops there, leaving
        the earlier segments and the unfinished one listed to delete again.
        """
        if not confirmed:
            raise ValueError("Explicit confirmation is required to permanently delete a training.")
        preview = self.chain_deletion_preview(run_id)
        if preview["refusal"]:
            raise ValueError(preview["refusal"])
        deleted: builtins.list[str] = []
        freed = 0
        for member in reversed(preview["segments"]):
            result = self.delete(member["id"], confirmed=True)
            if not result["deleted"]:
                return {**result, "deleted_ids": deleted}
            deleted.append(member["id"])
            freed += result["freed_bytes"]
        return {"deleted": True, "id": preview["chain"], "deleted_ids": deleted, "freed_bytes": freed}

    def delete(self, run_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        """Mark the Run first, so anything short of a finished delete leaves it visible and deletable again."""
        if not confirmed:
            raise ValueError("Explicit confirmation is required to permanently delete a Run.")
        self.reconcile(run_id)
        settings = load_settings(self.root)
        folders: builtins.list[Path] = []

        def begin(record: dict[str, Any], others: builtins.list[dict[str, Any]]) -> None:
            folders.extend(Path(folder) for folder in plan_deletion(record, others, settings)["folders"])
            remaining = [{"path": str(folder)} for folder in folders]
            record.update(status="deleting", deletion={"error": None, "remaining": remaining})

        self.store.update_with_others(run_id, begin)
        freed = sum(folder_bytes(folder) for folder in folders)
        failed: builtins.list[str] = []
        for index, folder in enumerate(folders):
            failed += remove_folder(folder)
            if not failed:
                # Kept current so a restart partway through lists only what is really left.
                left = [{"path": str(later)} for later in folders[index + 1 :]]
                self.store.update(run_id, lambda record: record["deletion"].update(remaining=left))
        remaining = [{"path": str(folder), "bytes": folder_bytes(folder)} for folder in folders if folder.exists()]
        if failed or remaining:
            message = failure_message(failed or [str(item["path"]) for item in remaining])
            return {
                "deleted": False,
                "run": self.store.update(
                    run_id, lambda record: record["deletion"].update(error=message, remaining=remaining)
                ),
            }
        output_index_file(self.root, run_id).unlink(missing_ok=True)
        self.store.remove(run_id)
        return {"deleted": True, "id": run_id, "freed_bytes": freed}

    def _schedule(self) -> None:
        while not self.stopping.is_set():
            try:
                candidates = sorted(
                    [run for run in self.list() if run["status"] in {"pending_preparation", "pending_training"}],
                    key=lambda r: r["order"],
                )
                if candidates:
                    run = candidates[0]
                    if self.device.acquire(run["id"], self.identity):
                        self._execute(run)
            except (ValueError, OSError) as error:
                # Persist scheduler diagnostics without inventing a run outcome.
                self.root.mkdir(parents=True, exist_ok=True)
                atomic_json(self.root / "worker-error.json", {"error": redact(str(error)), "time": utc_now()})
            self.stopping.wait(0.1)

    def _execute(self, run: dict[str, Any]) -> None:
        context = ExecutionContext(
            root=self.root,
            store=self.store,
            device=self.device,
            identity=self.identity,
            stopping=self.stopping,
            fixture_mode=self.fixture_mode,
            descendant_grace=self.descendant_grace,
            verify_inputs=self._verify_inputs,
            reuse=self._reuse,
            request_retention=self.request_retention,
        )
        RunExecution(context, run).execute()

    def _reuse(self, run: dict[str, Any], stage_name: str, index: int) -> bool:
        return reuse_cache(self.store, run, stage_name, index) or seed_refreshed(self.store, run, stage_name, index)
