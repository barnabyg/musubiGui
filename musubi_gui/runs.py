"""Browser-independent preparation from frozen, provenance-bearing plans."""

import builtins
import hashlib
import json
import os
import subprocess  # nosec B404
import threading
import time
import uuid
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

from musubi_gui.checkpoint_dedup import remove_final_duplicates
from musubi_gui.checkpoint_retention import retained_states
from musubi_gui.managed_checkout import checkout_gate
from musubi_gui.media_probe import inspect as inspect_media
from musubi_gui.processes import identity_status, observed_tree, process_identity, terminate_owned
from musubi_gui.recipe import Stage, reference_environment
from musubi_gui.redaction import redact
from musubi_gui.run_cache import cache_artifacts, reuse_cache, seed_wan_tokenizer, share_wan_tokenizer
from musubi_gui.run_deletion import failure_message, folder_bytes, output_index_file, plan_deletion, remove_folder
from musubi_gui.run_estimate import freeze_training_timing
from musubi_gui.run_performance import RunPerformance, start_performance
from musubi_gui.run_progress import estimated_remaining, parse_progress
from musubi_gui.run_recovery import RECONCILED_STATUSES, reconcile, terminate_orphan
from musubi_gui.run_restore import create_segment, restored_segment
from musubi_gui.run_retention import RetentionScheduler
from musubi_gui.run_retry import retried_plan
from musubi_gui.run_store import DeviceLease, RunStore, blocks_revision, preparation_transaction, utc_now
from musubi_gui.saved_state import discover_states
from musubi_gui.settings import Settings, atomic_json, load_settings, managed_settings
from musubi_gui.snapshots import (
    digest_file,
    identify_snapshot,
    materialize_snapshot,
    verify_inputs,
)
from musubi_gui.wan_pair_runtime import (
    finish_phase,
    is_paired,
    paired_saved_states,
    sampling_pair,
    stage_log,
    validate_phase_input,
)
from musubi_gui.windows_job import ProcessJob, WindowsProcess


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
        if progress and stage.get("training"):
            phase = stage["training"]
            from musubi_gui.wan_pair import phase_counts

            absolute = phase["start"] + progress["step"]
            progress.update(
                expert=phase["expert"],
                phase_step=progress["step"],
                phase_total=progress["total"],
                step=absolute - run.get("segment", {}).get("schedule_start", 0),
                total=(
                    sum(item["training"]["steps"] for item in run["stages"] if item.get("training"))
                    if phase.get("per_expert")
                    else run.get("segment", {}).get("additional_steps", run["job"]["config"]["steps"])
                ),
                expert_steps=phase_counts(phase, progress["step"]),
                eta_scope="phase",
            )
        if progress:
            progress["estimated_remaining_seconds"] = estimated_remaining(progress)
            if stage.get("training", {}).get("per_expert"):
                initial = run.get("pair_sources", {})
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
        plan = ExecutionPlan(**retried_plan(self.get(run_id), self._verify_inputs))
        retried = self.submit(plan)
        return self.store.update(retried["id"], lambda r: r.update(retry_of=run_id))

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
        self.store.update(run_id, lambda r: r.update(saved_states=states))
        return states

    def retain_checkpoints(self, run_id: str) -> None:
        """Apply the frozen Run's policy; failure preserves training and is visible for repair."""
        self.retention.retain(run_id)

    def request_retention(self, run_id: str) -> None:
        """Coalesce work without delaying process supervision or Stop signalling."""
        self.retention.request(run_id)

    def restore_state(
        self, run_id: str, state_id: str, additional_steps: int, *, confirmed: bool = False
    ) -> dict[str, Any]:
        if not confirmed:
            raise ValueError(
                "Please confirm restoration as a new segment: upstream restores optimizer state but resets the displayed counter and data position."
            )
        if not 1 <= additional_steps <= 1_000_000:
            raise ValueError("Choose between 1 and 1000000 additional training steps.")
        original = self.get(run_id)
        self._verify_inputs(original)
        state = next((state for state in self.saved_states(run_id) if state["id"] == state_id), None)
        if not state or not state["valid"]:
            raise ValueError(
                "Saved state is incomplete or changed. A LoRA checkpoint alone is not resumable optimizer state."
            )
        if state.get("paired"):
            from musubi_gui.wan_pair_restore import restored_pair

            record = restored_pair(original, state, additional_steps)
        else:
            record = restored_segment(original, state, additional_steps)
        create_segment(self.store, record, run_id)
        return self.enqueue(record["id"])

    def deletion_preview(self, run_id: str) -> dict[str, Any]:
        run = self.reconcile(run_id)
        others = [other for other in self.list() if other["id"] != run_id]
        try:
            plan = plan_deletion(run, others, load_settings(self.root))
        except ValueError as refusal:
            return {"run_id": run_id, "refusal": str(refusal)}
        size = sum(folder_bytes(Path(folder)) for folder in plan["folders"])
        return {**plan, "run_id": run_id, "refusal": None, "bytes": size}

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
        run_id = run["id"]
        release = True
        job_object: ProcessJob | None = None
        process: WindowsProcess | subprocess.Popen[str] | None = None
        failure: str | None = None
        performance: RunPerformance | None = None
        preparing = run["phase"] == "prepare"

        def claim(record: dict[str, Any]) -> None:
            if record["status"] in {"pending_preparation", "pending_training"}:
                record.update(status="preparing" if preparing else "training", controller=self.identity)
                if (not preparing or record.get("start_after_prepare")) and not record.get("execution_started"):
                    record["execution_started"] = utc_now()
                record.pop("finished", None)

        claimed = self.store.update(run_id, claim)
        if claimed["status"] not in {"preparing", "training"}:
            self.device.release(run_id)
            return
        try:
            self._verify_inputs(run)
            if run.get("state_source"):
                for name, expected in run["state_source"]["files"].items():
                    if digest_file(Path(run["state_source"]["path"]) / name) != expected:
                        raise ValueError("Saved state changed while queued. Restore a complete state again.")
            # Inputs were just verified for the first stage; later stages verify again before launching.
            verified = True
            for index, stage in enumerate(run["stages"]):
                if stage["name"] not in ({"latent_cache", "text_cache"} if preparing else {"train", "generate"}):
                    continue
                if not verified:
                    self._verify_inputs(run)
                verified = False
                fresh_run = self.get(run_id)
                if fresh_run.get("stop_requested"):
                    self.store.update(
                        run_id,
                        lambda r: r.update(
                            status="stopped", stop_kind=r["stop_requested"], processes=[], finished=utc_now()
                        ),
                    )
                    return
                if preparing and self._reuse(run, stage["name"], index):
                    continue
                pair = None
                if stage.get("training"):
                    validate_phase_input(fresh_run, stage)
                if stage["name"] == "generate":
                    if is_paired(run):
                        pair = sampling_pair(fresh_run, stage)
                        self.store.update(run_id, lambda r: r.update(sample_pair=pair))
                    self.store.update(run_id, lambda r: r.update(status="generating"))
                elif not preparing:
                    self.store.update(run_id, lambda r: r.update(status="training"))
                self.store.update(run_id, lambda r: r["stages"][index].update(status="running", started=utc_now()))
                settings = Settings.model_validate(run["settings"])
                if job_object:
                    job_object.close()
                name = (
                    "Local\\MusubiGui-" + fingerprint(str(self.root.resolve()))[:12] + "-" + run_id + "-" + str(index)
                )
                job_object = ProcessJob(name)
                names = list(self.get(run_id).get("job_objects", []))
                if name not in names:
                    names.append(name)
                self.store.update(run_id, lambda r: r.update(job_objects=names, launch_pending=True, launch_protocol=3))
                self.device.update(run_id, [], job_objects=names, launch_pending=True, launch_protocol=3)
                log_file = Path(run["directory"]) / stage_log(stage)
                with log_file.open("a", encoding="utf-8") as log:
                    environment = reference_environment(Path(run["cache"]))
                    environment.pop("MUSUBI_EXPERT_STEP_OFFSET", None)
                    if (offset := stage.get("training", {}).get("checkpoint_step_offset")) is not None:
                        environment["MUSUBI_EXPERT_STEP_OFFSET"] = str(offset)
                    if seed_wan_tokenizer(run):
                        environment["HF_HUB_OFFLINE"] = "1"
                        log.write("Using the shared Wan tokenizer; Hugging Face Hub access is off for this stage.\n")
                        log.flush()
                    if os.name == "nt":
                        process = job_object.spawn(stage["args"], cwd=settings.checkout, env=environment, log=log)
                    else:
                        # B603: trusted recipe adapter builds arrays; no API accepts executable commands.
                        process = subprocess.Popen(  # nosec B603
                            stage["args"],
                            cwd=settings.checkout,
                            env=environment,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            text=True,
                            start_new_session=True,
                        )
                    performance = start_performance(run, stage, process.pid, log_file, fixture=self.fixture_mode)
                    identities = observed_tree(process.pid)
                    self.store.update(run_id, lambda r: r.update(processes=identities))
                    self.device.update(run_id, identities)
                    self.store.update(run_id, lambda r: r.update(processes=identities, launch_pending=False))
                    self.device.update(run_id, identities, launch_pending=False)
                    signalled = ""
                    retention_at = time.monotonic() + 5
                    while process.poll() is None:
                        fresh = observed_tree(process.pid)
                        for pid in job_object.pids():
                            try:
                                fresh.append(process_identity(pid))
                            except psutil.NoSuchProcess:
                                pass
                        identities = list(
                            {(item["pid"], item["created"]): item for item in [*identities, *fresh]}.values()
                        )
                        self.device.update(run_id, identities)
                        self.store.update(run_id, lambda r: r.update(processes=identities))
                        if self.get(run_id).get("cancel_requested"):
                            job_object.terminate()
                            terminate_owned(identities, force=True)
                        signalled = self._signal_stop(run_id, job_object, identities, signalled)
                        if stage["name"] == "train" and time.monotonic() >= retention_at:
                            self.request_retention(run_id)
                            retention_at = time.monotonic() + 5
                        if self.stopping.wait(0.1):
                            release = False
                            self.store.update(
                                run_id,
                                lambda r: r.update(
                                    status="interrupted",
                                    error="Service stopped; check for leftover processes before retrying.",
                                ),
                            )
                            if isinstance(process, WindowsProcess):
                                process.close()
                            else:
                                threading.Thread(target=process.wait, name="musubi-child-reaper", daemon=True).start()
                            return
                    code = process.wait()
                    if performance:
                        performance.close(code)
                        performance = None
                    if isinstance(process, WindowsProcess):
                        process.close()
                    if not self._await_descendant_exit(run_id, job_object, identities, signalled):
                        release = False
                        self.device.update(run_id, identities)
                        if self.stopping.is_set():
                            self.store.update(
                                run_id,
                                lambda r: r.update(
                                    status="interrupted",
                                    processes=identities,
                                    error="Service stopped; check for leftover processes before retrying.",
                                ),
                            )
                            return
                        self.store.update(
                            run_id,
                            lambda r: r.update(
                                status="orphaned",
                                processes=identities,
                                error="The launcher exited but a descendant remains active. Check for leftover processes before starting another GPU operation.",
                            ),
                        )
                        return
                requested = self.get(run_id).get("stop_requested")
                self.store.update(
                    run_id,
                    lambda r: r["stages"][index].update(
                        status="stopped" if requested else "completed" if code == 0 else "failed",
                        exit_code=code,
                        finished=utc_now(),
                    ),
                )
                if requested:
                    self.store.update(
                        run_id,
                        lambda r: r.update(
                            status="stopped", stop_kind=requested, processes=[], exit_code=code, finished=utc_now()
                        ),
                    )
                    return
                if code:
                    if self.get(run_id).get("cancel_requested"):
                        self.store.update(
                            run_id, lambda r: r.update(status="cancelled", processes=[], finished=utc_now())
                        )
                        return
                    raise ValueError(
                        f"{stage['name']} exited {code}. Inspect logs, then prepare a new Run from frozen inputs."
                    )
                share_wan_tokenizer(run)
                if preparing:
                    artifacts = cache_artifacts(run, stage["name"])
                    if run.get("stage_keys") and not artifacts:
                        raise ValueError(f"{stage['name']} returned success but produced no complete cache artifacts.")
                    self.store.update(run_id, lambda r: r["stages"][index].update(artifacts=artifacts))
                elif stage["name"] == "train":
                    from musubi_gui.preview import validate_previews

                    validate_previews(stage["args"], run["job"]["family"])
                    removed = self._remove_final_duplicates(run, stage)
                    checkpoints = list((Path(run["directory"]) / "checkpoints").glob("*.safetensors"))
                    if not any(file.stat().st_size > 0 for file in checkpoints):
                        raise ValueError("Training exited successfully but no nonempty checkpoint was found.")
                    self.store.update(
                        run_id,
                        lambda r: r.update(
                            checkpoints=[str(file) for file in checkpoints],
                            removed_duplicates=[*r.get("removed_duplicates", []), *removed],
                        ),
                    )
                    if stage.get("training"):
                        state = finish_phase(self.get(run_id), stage)

                        def save_expert(record: dict[str, Any]) -> None:
                            record.setdefault("pair_seals", {})[state["path"]] = state
                            record.setdefault("expert_states", {})[stage["training"]["expert"]] = state

                        self.store.update(run_id, save_expert)
                    self.request_retention(run_id)
                elif stage["name"] == "generate":
                    destination = Path(stage["args"][stage["args"].index("--save_path") + 1])
                    samples = list(destination.rglob("*")) if destination.is_dir() else [destination]
                    media_files = [
                        file
                        for file in samples
                        if file.is_file() and file.suffix.lower() in {".mp4", ".mov", ".webm", ".wav", ".png", ".jpg"}
                    ]
                    if not media_files:
                        raise ValueError("Sampling exited successfully but no sample was found.")
                    inspected = []
                    for file in media_files:
                        metadata = inspect_media(file, timeline=True)
                        if run["job"]["family"] == "h3" and (not metadata.get("frames") or not metadata.get("audio")):
                            raise ValueError("H3 sample is incomplete: decodable video and audio are both required.")
                        inspected.append(
                            {
                                "path": str(file),
                                "status": "playable",
                                "metadata": metadata,
                                "sha256": digest_file(file),
                                **({"lora_pair": pair} if pair else {}),
                                **(
                                    {"preview_step": stage["preview_step"]}
                                    if stage.get("preview_step") is not None
                                    else {}
                                ),
                            }
                        )
                    paths = {sample["path"] for sample in inspected}
                    self.store.update(
                        run_id,
                        lambda r: r.update(
                            samples=[sample for sample in r.get("samples", []) if sample["path"] not in paths]
                            + inspected
                        ),
                    )

            def finish(record: dict[str, Any]) -> None:
                record.update(
                    status="prepared" if preparing else "completed", processes=[], exit_code=0, finished=utc_now()
                )
                if (
                    preparing
                    and record.get("start_after_prepare")
                    and not record.get("cancel_requested")
                    and any(stage["name"] == "train" for stage in record["stages"])
                ):
                    # Atomic with the end of preparation: a crash can't leave it prepared but never started.
                    record.update(status="pending_training", phase="train", order=time.time_ns())
                    record.pop("finished", None)

            self.store.update(run_id, finish)
        except Exception as error:
            failure = redact(str(error))
        finally:
            if performance:
                performance.close()
            if isinstance(process, WindowsProcess):
                process.close()
            if job_object:
                try:
                    if job_object.pids():
                        release = False
                except (OSError, ValueError):
                    release = False
                job_object.close()
            if failure is not None:
                # A retryable terminal failure and its cleared ownership records must become visible together.
                self.store.update(
                    run_id,
                    lambda r: r.update(
                        status="failed" if release else "uncertain",
                        error=failure,
                        finished=utc_now(),
                        processes=[] if release else r["processes"],
                        launch_pending=False if release else r.get("launch_pending", False),
                    ),
                )
            if release:
                if failure is None:
                    self.store.update(run_id, lambda r: r.update(processes=[], launch_pending=False))
                self.device.release(run_id)

    def _remove_final_duplicates(self, run: dict[str, Any], stage: dict[str, Any]) -> builtins.list[str]:
        """Keep one copy of the final save when the step count is a multiple of the checkpoint interval."""
        args = stage["args"]
        if "--output_name" not in args or "--max_train_steps" not in args:
            return []
        log_file = Path(run["directory"]) / stage_log(stage)
        removed, problems = remove_final_duplicates(
            Path(run["directory"]) / "checkpoints",
            args[args.index("--output_name") + 1],
            int(args[args.index("--max_train_steps") + 1]),
        )
        lines = [f"Removed duplicate of the final save: {name}" for name in removed] + problems
        if lines:
            with log_file.open("a", encoding="utf-8") as log:
                log.writelines(f"{line}\n" for line in lines)
        return removed

    def _signal_stop(
        self, run_id: str, job_object: ProcessJob, identities: builtins.list[dict[str, Any]], signalled: str
    ) -> str:
        """Signal a newly requested stop to the owned tree once; returns the stop kind now signalled."""
        stop = self.get(run_id).get("stop_requested", "")
        if not stop or stop == signalled:
            return signalled
        try:
            if stop == "force":
                job_object.terminate()
            terminate_owned(identities, force=stop == "force")
        except OSError:
            self.store.update(
                run_id,
                lambda r: r.update(
                    error="Graceful console signalling is unavailable. Use confirmed force termination if the process remains active."
                ),
            )
        return str(stop)

    def _await_descendant_exit(
        self, run_id: str, job_object: ProcessJob, identities: builtins.list[dict[str, Any]], signalled: str
    ) -> bool:
        """Allow a bounded grace period for descendants still exiting after their launcher, such as
        the Python child behind a venv launcher or a short-lived nvidia-smi. Records late arrivals
        and still honours stop requests; returns whether every owned process has exited."""
        deadline = time.monotonic() + self.descendant_grace
        while True:
            remaining = job_object.pids()
            known = len(identities)
            for pid in remaining:
                try:
                    identity = process_identity(pid)
                    if identity not in identities:
                        identities.append(identity)
                except psutil.NoSuchProcess:
                    pass
            if len(identities) > known:
                self.device.update(run_id, identities)
                self.store.update(run_id, lambda r: r.update(processes=identities))
            if not remaining and all(identity_status(identity) == "dead" for identity in identities):
                return True
            signalled = self._signal_stop(run_id, job_object, identities, signalled)
            if time.monotonic() >= deadline or self.stopping.wait(0.1):
                return False

    def _reuse(self, run: dict[str, Any], stage_name: str, index: int) -> bool:
        return reuse_cache(self.store, run, stage_name, index)


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
