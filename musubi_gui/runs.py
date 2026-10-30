"""Browser-independent preparation from frozen, provenance-bearing plans."""

import builtins
import copy
import hashlib
import json
import os
import shutil
import subprocess  # nosec B404
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

from musubi_gui.checkpoint_dedup import remove_final_duplicates
from musubi_gui.media_probe import inspect as inspect_media
from musubi_gui.processes import identity_status, observed_tree, process_identity, terminate_owned
from musubi_gui.reference import Stage, redact, reference_environment
from musubi_gui.run_deletion import failure_message, folder_bytes, output_index_file, plan_deletion, remove_folder
from musubi_gui.run_store import DeviceLease, RunStore, blocks_revision, preparation_transaction
from musubi_gui.saved_state import discover_states
from musubi_gui.settings import Settings, atomic_json, load_settings
from musubi_gui.snapshots import (
    digest_file,
    identify_snapshot,
    materialize_snapshot,
    verify_inputs,
)
from musubi_gui.wan_tokenizer import publish_shared, seed_run, shared_directory
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


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


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
        self.thread = threading.Thread(target=self._schedule, name="musubi-worker", daemon=True)
        if autostart:
            self.start_worker()

    def start_worker(self) -> None:
        for run in self.list():
            if run["status"] in {
                "preparing",
                "training",
                "generating",
                "stopping",
                "interrupted",
                "orphaned",
                "uncertain",
            }:
                self.reconcile(run["id"])
        self.thread.start()

    def list(self) -> list[dict[str, Any]]:
        return self.store.list()

    def get(self, run_id: str) -> dict[str, Any]:
        return self.store.get(run_id)

    def prepare(self, job_id: str) -> dict[str, Any]:
        with preparation_transaction(self.root):
            inputs = identify_snapshot(self.root, job_id, fixture_mode=self.fixture_mode)
            for previous in self.list():
                if blocks_revision(previous, inputs.fingerprint):
                    return {**previous, "existing": True}
            return {**self.submit(ExecutionPlan(**materialize_snapshot(inputs))), "existing": False}

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

    def _request_training(self, run_id: str, *, allow_wait: bool) -> dict[str, Any]:
        run = self.get(run_id)
        if run["status"] in {"pending_training", "training", "generating", "completed"}:
            return run
        if run["status"] != "prepared":
            raise ValueError("Prepare this immutable run successfully before starting training.")
        owner = self.device.owner()
        if owner and owner["run_id"] != run_id and not allow_wait:
            raise ValueError("The GPU is owned by another operation. Wait for it to finish.")
        verify_inputs(run)
        if not any(stage["name"] == "train" for stage in run["stages"]):
            raise ValueError("This run has no supported training stage.")

        def change(record: dict[str, Any]) -> None:
            if record["status"] != "prepared":
                return
            record.update(status="pending_training", phase="train", order=time.time_ns())

        return self.store.update(run_id, change)

    def submit(self, plan: ExecutionPlan) -> dict[str, Any]:
        """Trusted local adapter seam; API requests never supply command arrays."""
        data: dict[str, Any] = {
            "version": 1,
            "id": uuid.uuid4().hex,
            "created": utc_now(),
            "job": plan.job,
            "settings": plan.settings.model_dump(),
            "manifest": plan.manifest,
            "directory": str(plan.directory.resolve()),
            "cache": str(plan.cache.resolve()),
            "fingerprint": plan.fingerprint,
            "status": "pending_preparation",
            "phase": "prepare",
            "order": time.time_ns(),
            "stages": [{"name": stage.name, "args": stage.args, "status": "pending"} for stage in plan.stages],
            "processes": [],
            "error": None,
            "fixture": self.fixture_mode,
            "stage_keys": plan.stage_keys,
            "input_files": plan.input_files,
            "provenance": plan.provenance,
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
            file = directory / f"{stage['name']}.log"
            if file.is_file():
                with file.open("rb") as stream:
                    stream.seek(max(0, file.stat().st_size - 256_000))
                    parts.append(f"--- {stage['name']} ---\n" + redact(stream.read().decode("utf-8", errors="replace")))
        return "\n".join(parts)

    def close(self) -> None:
        self.stopping.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)

    def retry(self, run_id: str) -> dict[str, Any]:
        previous = self.get(run_id)
        if previous["status"] not in {"failed", "cancelled", "interrupted"} or previous.get("processes"):
            raise ValueError("Only failed or cancelled preparation can be retried. Reconcile interrupted work first.")
        verify_inputs(previous)
        directory = Path(previous["directory"]).parent / ("retry-" + uuid.uuid4().hex)
        cache = Path(previous["cache"]).parent / ("retry-" + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        native_names = ["dataset.toml", "dataset.jsonl", "samples.txt"]
        old_native = {str(Path(previous["directory"]) / name) for name in native_names}
        input_files = {path: value for path, value in previous.get("input_files", {}).items() if path not in old_native}
        for name in native_names:
            source = Path(previous["directory"]) / name
            if source.is_file():
                text = source.read_text(encoding="utf-8")
                if name == "dataset.toml":
                    text = text.replace(previous["cache"].replace("\\", "/"), cache.as_posix())
                    text = text.replace(
                        json.dumps((Path(previous["directory"]) / "dataset.jsonl").as_posix(), ensure_ascii=False),
                        json.dumps((directory / "dataset.jsonl").as_posix(), ensure_ascii=False),
                    )
                target = directory / name
                target.write_text(text, encoding="utf-8")
                input_files[str(target)] = digest_file(target)
        verify_inputs(previous)
        stages = []
        for stage in previous["stages"]:
            args = [
                arg
                if arg.endswith(".py")
                else arg.replace(previous["directory"], str(directory)).replace(previous["cache"], str(cache))
                for arg in stage["args"]
            ]
            stages.append(Stage(stage["name"], args))
        plan = ExecutionPlan(
            copy.deepcopy(previous["job"]),
            Settings.model_validate(previous["settings"]),
            copy.deepcopy(previous["manifest"]),
            directory,
            cache,
            stages,
            previous["fingerprint"] + ":retry:" + uuid.uuid4().hex,
            stage_keys=previous.get("stage_keys", {}),
            input_files=input_files,
            provenance=previous.get("provenance", {}),
        )
        retried = self.submit(plan)
        return self.store.update(retried["id"], lambda r: r.update(retry_of=run_id))

    def reconcile(self, run_id: str) -> dict[str, Any]:
        run = self.get(run_id)
        if run["status"] not in {
            "preparing",
            "training",
            "generating",
            "stopping",
            "interrupted",
            "orphaned",
            "uncertain",
        }:
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
        owner = self.device.owner()
        if owner and owner["run_id"] == run_id and owner["state"] == str(self.root.resolve()):
            # Protocol 3 assigns the named Job Object atomically during process creation.
            # A known empty job proves that no process survived, even before PID persistence.
            # Protocol 2 remains readable, but its old unassigned suspended-child window is uncertain.
            observable_launch = owner.get("launch_protocol") == 3 and bool(run.get("job_objects"))
            uncertain = uncertain or (bool(owner.get("launch_pending")) and not observable_launch)
        if live or uncertain:
            status = "uncertain" if uncertain else "orphaned"
            self.device.update(run_id, identities)
            return self.store.update(
                run_id,
                lambda r: r.update(
                    status=status,
                    processes=identities,
                    error="Recorded work is still alive or cannot be safely identified. No replacement GPU work will start. Reconcile again or confirm termination of known owned processes.",
                ),
            )
        self.device.release(run_id)

        def finish(record: dict[str, Any]) -> None:
            # Every recorded process is gone, so a requested stop was observed rather than assumed.
            # An already interrupted run stays retryable, as it was before stops were reconciled.
            requested = record.get("stop_requested") if record["status"] != "interrupted" else None
            for stage in record["stages"]:
                if stage["status"] == "running":
                    stage.update(status="stopped" if requested else "interrupted", finished=utc_now())
            if requested:
                record.update(status="stopped", stop_kind=requested, processes=[], error=None, finished=utc_now())
                return
            record.update(
                status="interrupted",
                processes=[],
                error="The original processes are gone. Inspect outputs/state, then retry frozen preparation or restore validated optimizer state as a new segment.",
            )

        return self.store.update(run_id, finish)

    def terminate_orphan(self, run_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("Explicit confirmation is required to terminate recovered owned processes.")
        run = self.reconcile(run_id)
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
            self.store.update(run_id, lambda r: r.update(stop_requested="force"))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if all(identity_status(identity) == "dead" for identity in run.get("processes", [])):
                break
            time.sleep(0.05)
        return self.reconcile(run_id)

    def cancel(self, run_id: str) -> dict[str, Any]:
        run = self.get(run_id)
        if run["status"] in {"pending_preparation", "pending_training"}:
            return self.store.update(run_id, lambda r: r.update(status="cancelled"))
        if run["status"] != "preparing":
            raise ValueError("Only queued or active preparation can be cancelled.")
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
            raise ValueError("Reconcile remaining process ownership before inspecting saved state.")
        states = discover_states(run)
        self.store.update(run_id, lambda r: r.update(saved_states=states))
        return states

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
        verify_inputs(original)
        state = next((state for state in self.saved_states(run_id) if state["id"] == state_id), None)
        if not state or not state["valid"]:
            raise ValueError(
                "Saved state is incomplete or changed. A LoRA checkpoint alone is not resumable optimizer state."
            )
        directory = Path(original["directory"]).parent / ("restore-" + uuid.uuid4().hex)
        directory.mkdir()
        record = copy.deepcopy(original)
        record.update(
            id=uuid.uuid4().hex,
            directory=str(directory),
            status="prepared",
            phase="train",
            fingerprint=original["fingerprint"] + ":restore:" + uuid.uuid4().hex,
            created=utc_now(),
            processes=[],
            error=None,
            resume_of=run_id,
            saved_states=[],
            segment={
                "additional_steps": additional_steps,
                "optimizer_step_before": state["optimizer_step"],
                "counter_resets": True,
                "data_position_restored": False,
            },
            state_source={"path": state["path"], "files": state["files"]},
        )
        for key in ["stop_requested", "stop_kind", "finished", "exit_code", "checkpoints", "removed_duplicates"]:
            record.pop(key, None)
        for stage in record["stages"]:
            if stage["name"] not in {"train", "generate"}:
                continue
            stage.update(status="pending")
            args = stage["args"]
            for flag in ["--output_dir"] if stage["name"] == "train" else ["--save_path", "--lora_weight"]:
                if flag in args:
                    index = args.index(flag) + 1
                    args[index] = args[index].replace(original["directory"], str(directory))
            if stage["name"] == "train":
                args[args.index("--max_train_steps") + 1] = str(additional_steps)
                args.extend(["--resume", state["path"]])
        self.store.create(record)
        atomic_json(
            directory / "inputs.json",
            {"job": record["job"], "manifest": record["manifest"], "resume_of": run_id, "segment": record["segment"]},
        )
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
        preparing = run["phase"] == "prepare"

        def claim(record: dict[str, Any]) -> None:
            if record["status"] in {"pending_preparation", "pending_training"}:
                record.update(status="preparing" if preparing else "training", controller=self.identity)

        claimed = self.store.update(run_id, claim)
        if claimed["status"] not in {"preparing", "training"}:
            self.device.release(run_id)
            return
        try:
            verify_inputs(run)
            if run.get("state_source"):
                for name, expected in run["state_source"]["files"].items():
                    if digest_file(Path(run["state_source"]["path"]) / name) != expected:
                        raise ValueError("Saved state changed while queued. Restore a complete state again.")
            for index, stage in enumerate(run["stages"]):
                if stage["name"] not in ({"latent_cache", "text_cache"} if preparing else {"train", "generate"}):
                    continue
                verify_inputs(run)
                if preparing and self._reuse(run, stage["name"], index):
                    continue
                if stage["name"] == "generate":
                    self.store.update(run_id, lambda r: r.update(status="generating"))
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
                log_file = Path(run["directory"]) / f"{stage['name']}.log"
                with log_file.open("a", encoding="utf-8") as log:
                    environment = reference_environment(Path(run["cache"]))
                    if self._seed_wan_tokenizer(run):
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
                    identities = observed_tree(process.pid)
                    self.store.update(run_id, lambda r: r.update(processes=identities))
                    self.device.update(run_id, identities)
                    self.store.update(run_id, lambda r: r.update(processes=identities, launch_pending=False))
                    self.device.update(run_id, identities, launch_pending=False)
                    signalled = ""
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
                        if self.stopping.wait(0.1):
                            release = False
                            self.store.update(
                                run_id,
                                lambda r: r.update(
                                    status="interrupted",
                                    error="Service stopped; reconcile the recorded process before retrying.",
                                ),
                            )
                            if isinstance(process, WindowsProcess):
                                process.close()
                            else:
                                threading.Thread(target=process.wait, name="musubi-child-reaper", daemon=True).start()
                            return
                    code = process.wait()
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
                                    error="Service stopped; reconcile the recorded process before retrying.",
                                ),
                            )
                            return
                        self.store.update(
                            run_id,
                            lambda r: r.update(
                                status="orphaned",
                                processes=identities,
                                error="The launcher exited but a descendant remains active. Reconcile before starting another GPU operation.",
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
                        self.store.update(run_id, lambda r: r.update(status="cancelled", processes=[]))
                        return
                    raise ValueError(f"{stage['name']} exited {code}. Inspect logs and retry preparation.")
                self._share_wan_tokenizer(run)
                if preparing:
                    artifacts = self._cache_artifacts(run, stage["name"])
                    if run.get("stage_keys") and not artifacts:
                        raise ValueError(f"{stage['name']} returned success but produced no complete cache artifacts.")
                    self.store.update(run_id, lambda r: r["stages"][index].update(artifacts=artifacts))
                elif stage["name"] == "train":
                    removed = self._remove_final_duplicates(run, stage)
                    checkpoints = list((Path(run["directory"]) / "checkpoints").glob("*.safetensors"))
                    if not any(file.stat().st_size > 0 for file in checkpoints):
                        raise ValueError("Training exited successfully but no nonempty checkpoint was found.")
                    self.store.update(
                        run_id,
                        lambda r: r.update(checkpoints=[str(file) for file in checkpoints], removed_duplicates=removed),
                    )
                elif stage["name"] == "generate":
                    samples = list((Path(run["directory"]) / "samples").rglob("*"))
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
                        inspected.append({"path": str(file), "status": "playable", "metadata": metadata})
                    self.store.update(run_id, lambda r: r.update(samples=inspected))
            self.store.update(
                run_id,
                lambda r: r.update(
                    status="prepared" if preparing else "completed", processes=[], exit_code=0, finished=utc_now()
                ),
            )
        except Exception as error:
            failure = redact(str(error))
        finally:
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

    def _seed_wan_tokenizer(self, run: dict[str, Any]) -> bool:
        shared = wan_tokenizer_directory(run)
        if not shared:
            return False
        try:
            return seed_run(shared, Path(run["cache"]))
        except OSError:
            # The stage can still download the tokenizer itself.
            return False

    def _share_wan_tokenizer(self, run: dict[str, Any]) -> None:
        shared = wan_tokenizer_directory(run)
        if not shared:
            return
        try:
            publish_shared(Path(run["cache"]), shared)
        except OSError:
            # Sharing only saves a later download; it never fails a stage that succeeded.
            return

    def _remove_final_duplicates(self, run: dict[str, Any], stage: dict[str, Any]) -> builtins.list[str]:
        """Keep one copy of the final save when the step count is a multiple of the checkpoint interval."""
        args = stage["args"]
        if "--output_name" not in args or "--max_train_steps" not in args:
            return []
        log_file = Path(run["directory"]) / f"{stage['name']}.log"
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

    def _cache_artifacts(self, run: dict[str, Any], stage: str) -> builtins.list[dict[str, str]]:
        cache = Path(run["cache"])
        # dataset, dataset-images, and one dataset-rate-N per Wan source frame rate.
        candidates = list(cache.glob("dataset*/*.safetensors"))
        files = [file for file in candidates if file.name.endswith("_te.safetensors") == (stage == "text_cache")]
        if run["job"]["family"] == "h3" and stage == "text_cache":
            uncond = cache / "uncond.safetensors"
            if not uncond.is_file():
                raise ValueError("H3 text caching did not produce the unconditional guidance cache.")
            files.append(uncond)
        for item in run["manifest"]["items"]:
            if "snapshot_path" in item and not any(
                file.name.startswith(Path(item["snapshot_path"]).stem + "_") for file in files
            ):
                raise ValueError(
                    f"{item['name']}: {stage} did not prepare this included item. Inspect the stage log before retrying."
                )
        if any(file.stat().st_size == 0 for file in files):
            raise ValueError(f"{stage} contains empty/incomplete cache files.")
        return [{"path": str(file.relative_to(cache)), "sha256": digest_file(file)} for file in files]

    def _reuse(self, run: dict[str, Any], stage_name: str, index: int) -> bool:
        key = run.get("stage_keys", {}).get(stage_name)
        if not key:
            return False
        for previous in self.list():
            if previous["id"] == run["id"] or previous.get("stage_keys", {}).get(stage_name) != key:
                continue
            old: dict[str, Any] = next((stage for stage in previous["stages"] if stage["name"] == stage_name), {})
            artifacts = old.get("artifacts", [])
            if not artifacts or old.get("status") not in {"completed", "reused"} or previous["status"] == "deleting":
                continue
            try:
                if not all(
                    (Path(previous["cache"]) / file["path"]).is_file()
                    and digest_file(Path(previous["cache"]) / file["path"]) == file["sha256"]
                    for file in artifacts
                ):
                    continue
                for file in artifacts:
                    target = Path(run["cache"]) / file["path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(Path(previous["cache"]) / file["path"], target)
            except (FileNotFoundError, PermissionError):
                # That Run was deleted meanwhile; its cache simply isn't available to reuse.
                continue
            self.store.update(
                run["id"],
                lambda r: r["stages"][index].update(
                    status="reused", artifacts=artifacts, reused_from=previous["id"], exit_code=0
                ),
            )
            return True
        return False


def wan_tokenizer_directory(run: dict[str, Any]) -> Path | None:
    cache_root = run["settings"].get("cache_root")
    return shared_directory(cache_root) if run["job"]["family"] == "wan" and cache_root else None


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
