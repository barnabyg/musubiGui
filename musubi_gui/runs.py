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

from musubi_gui.processes import observed_tree, process_identity, terminate_owned
from musubi_gui.reference import Stage, redact, reference_environment
from musubi_gui.run_store import DeviceLease, RunStore
from musubi_gui.saved_state import discover_states
from musubi_gui.settings import Settings, atomic_json
from musubi_gui.snapshots import digest_file, snapshot_plan, verify_inputs


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
        self, root: Path, *, device_path: Path | None = None, fixture_mode: bool = False, autostart: bool = True
    ):
        self.root = root
        self.fixture_mode = fixture_mode
        self.store = RunStore(root)
        self.device = DeviceLease(root, device_path)
        self.identity = process_identity()
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._schedule, name="musubi-worker", daemon=True)
        if autostart:
            self.thread.start()

    def start_worker(self) -> None:
        self.thread.start()

    def list(self) -> list[dict[str, Any]]:
        return self.store.list()

    def get(self, run_id: str) -> dict[str, Any]:
        return self.store.get(run_id)

    def prepare(self, job_id: str) -> dict[str, Any]:
        return self.submit(ExecutionPlan(**snapshot_plan(self.root, job_id, fixture_mode=self.fixture_mode)))

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
        if run["job"]["family"] != "wan":
            raise ValueError("H3 training is not yet available.")
        if self.device.owner() and not allow_wait:
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
        if previous["status"] not in {"failed", "cancelled"}:
            raise ValueError("Only failed or cancelled preparation can be retried. Reconcile interrupted work first.")
        directory = Path(previous["directory"]).parent / ("retry-" + uuid.uuid4().hex)
        cache = Path(previous["cache"]).parent / ("retry-" + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        for name in ["dataset.toml", "samples.txt"]:
            source = Path(previous["directory"]) / name
            if source.is_file():
                text = source.read_text(encoding="utf-8").replace(
                    previous["cache"].replace("\\", "/"), cache.as_posix()
                )
                (directory / name).write_text(text, encoding="utf-8")
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
            input_files=previous.get("input_files", {}),
            provenance=previous.get("provenance", {}),
        )
        retried = self.submit(plan)
        return self.store.update(retried["id"], lambda r: r.update(retry_of=run_id))

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
        for key in ["stop_requested", "stop_kind", "finished", "exit_code", "checkpoints"]:
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
                log_file = Path(run["directory"]) / f"{stage['name']}.log"
                with log_file.open("a", encoding="utf-8") as log:
                    # B603: trusted recipe adapter builds arrays; no API accepts executable commands.
                    process = subprocess.Popen(  # nosec B603
                        stage["args"],
                        cwd=settings.checkout,
                        env=reference_environment(Path(run["cache"])),
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        text=True,
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                        start_new_session=os.name != "nt",
                    )
                    identities = observed_tree(process.pid)
                    self.device.update(run_id, identities)
                    self.store.update(run_id, lambda r: r.update(processes=identities))
                    signalled = ""
                    while process.poll() is None:
                        identities = observed_tree(process.pid)
                        self.device.update(run_id, identities)
                        self.store.update(run_id, lambda r: r.update(processes=identities))
                        if self.get(run_id).get("cancel_requested"):
                            terminate_owned(identities, force=True)
                        stop = self.get(run_id).get("stop_requested", "")
                        if stop and stop != signalled:
                            try:
                                terminate_owned(identities, force=stop == "force")
                                signalled = stop
                            except OSError:
                                self.store.update(
                                    run_id,
                                    lambda r: r.update(
                                        error="Graceful console signalling is unavailable. Use confirmed force termination if the process remains active."
                                    ),
                                )
                                signalled = stop
                        if self.stopping.wait(0.1):
                            release = False
                            self.store.update(
                                run_id,
                                lambda r: r.update(
                                    status="interrupted",
                                    error="Service stopped; reconcile the recorded process before retrying.",
                                ),
                            )
                            threading.Thread(target=process.wait, name="musubi-child-reaper", daemon=True).start()
                            return
                    code = process.wait()
                self.store.update(
                    run_id,
                    lambda r: r["stages"][index].update(
                        status="completed" if code == 0 else "failed", exit_code=code, finished=utc_now()
                    ),
                )
                requested = self.get(run_id).get("stop_requested")
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
                if preparing:
                    artifacts = self._cache_artifacts(run, stage["name"])
                    if run.get("stage_keys") and not artifacts:
                        raise ValueError(f"{stage['name']} returned success but produced no complete cache artifacts.")
                    self.store.update(run_id, lambda r: r["stages"][index].update(artifacts=artifacts))
                elif stage["name"] == "train":
                    checkpoints = list((Path(run["directory"]) / "checkpoints").glob("*.safetensors"))
                    if not any(file.stat().st_size > 0 for file in checkpoints):
                        raise ValueError("Training exited successfully but no nonempty checkpoint was found.")
                    self.store.update(run_id, lambda r: r.update(checkpoints=[str(file) for file in checkpoints]))
                elif stage["name"] == "generate":
                    samples = list((Path(run["directory"]) / "samples").rglob("*"))
                    if not any(file.is_file() and file.stat().st_size > 0 for file in samples):
                        raise ValueError("Sampling exited successfully but no sample was found.")
            self.store.update(
                run_id,
                lambda r: r.update(
                    status="prepared" if preparing else "completed", processes=[], exit_code=0, finished=utc_now()
                ),
            )
        except (OSError, ValueError) as error:
            message = redact(str(error))
            self.store.update(run_id, lambda r: r.update(status="failed", error=message, finished=utc_now()))
        finally:
            if release:
                self.device.release(run_id)

    def _cache_artifacts(self, run: dict[str, Any], stage: str) -> builtins.list[dict[str, str]]:
        cache = Path(run["cache"])
        candidates = [
            *list((cache / "dataset").glob("*.safetensors")),
            *list((cache / "dataset-images").glob("*.safetensors")),
        ]
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
            if not artifacts or old.get("status") not in {"completed", "reused"}:
                continue
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
            self.store.update(
                run["id"],
                lambda r: r["stages"][index].update(
                    status="reused", artifacts=artifacts, reused_from=previous["id"], exit_code=0
                ),
            )
            return True
        return False


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
