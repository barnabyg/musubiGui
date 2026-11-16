"""Running a claimed Run's stages: launch, supervision, stop and cancel handling, and each stage's outcome."""

import os
import subprocess  # nosec B404
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

import psutil

from musubi_gui.processes import identity_status, observed_tree, process_identity, terminate_owned
from musubi_gui.recipe import reference_environment
from musubi_gui.redaction import redact
from musubi_gui.run_cache import cache_artifacts, seed_wan_tokenizer, share_wan_tokenizer
from musubi_gui.run_performance import RunPerformance, start_performance
from musubi_gui.run_stage_outputs import record_checkpoints, record_samples
from musubi_gui.run_store import DeviceLease, RunStore, utc_now
from musubi_gui.settings import Settings
from musubi_gui.snapshots import digest, digest_file
from musubi_gui.wan_pair_runtime import is_paired, sampling_pair, stage_log, validate_phase_input
from musubi_gui.windows_job import ProcessJob, WindowsProcess

INTERRUPTED = "Service stopped; check for leftover processes before retrying."


@dataclass(frozen=True)
class ExecutionContext:
    """What running a Run's stages needs from the service that owns the queue and the GPU lease."""

    root: Path
    store: RunStore
    device: DeviceLease
    identity: dict[str, Any]
    stopping: threading.Event
    fixture_mode: bool
    # Seconds that descendants may take to exit after their stage launcher before the run is orphaned.
    descendant_grace: float
    verify_inputs: Callable[[dict[str, Any]], None]
    reuse: Callable[[dict[str, Any], str, int], bool]
    request_retention: Callable[[str], None]


class RunExecution:
    """One Run the scheduler holds the GPU lease for, from its claim until the lease is released.

    The lease is kept whenever owned processes may still be alive, so no replacement GPU work starts.
    """

    def __init__(self, context: ExecutionContext, run: dict[str, Any]):
        self.context = context
        self.store = context.store
        self.device = context.device
        self.run = run
        self.run_id: str = run["id"]
        self.preparing = run["phase"] == "prepare"
        self.release = True
        self.job_object: ProcessJob | None = None
        self.process: WindowsProcess | subprocess.Popen[str] | None = None
        self.performance: RunPerformance | None = None
        self.failure: str | None = None

    def execute(self) -> None:
        claimed = self.store.update(self.run_id, self._claim)
        if claimed["status"] not in {"preparing", "training"}:
            self.device.release(self.run_id)
            return
        try:
            self._run_stages()
        except Exception as error:
            self.failure = redact(str(error))
        finally:
            self._clean_up()

    def _claim(self, record: dict[str, Any]) -> None:
        if record["status"] in {"pending_preparation", "pending_training"}:
            record.update(status="preparing" if self.preparing else "training", controller=self.context.identity)
            if (not self.preparing or record.get("start_after_prepare")) and not record.get("execution_started"):
                record["execution_started"] = utc_now()
            record.pop("finished", None)

    def _run_stages(self) -> None:
        run = self.run
        self.context.verify_inputs(run)
        if run.get("state_source"):
            for name, expected in run["state_source"]["files"].items():
                if digest_file(Path(run["state_source"]["path"]) / name) != expected:
                    raise ValueError("Saved state changed while queued. Restore a complete state again.")
        # Inputs were just verified for the first stage; later stages verify again before launching.
        verified = True
        for index, stage in enumerate(run["stages"]):
            if stage["name"] not in ({"latent_cache", "text_cache"} if self.preparing else {"train", "generate"}):
                continue
            if not verified:
                self.context.verify_inputs(run)
            verified = False
            if not self._run_stage(index, stage):
                return
        self.store.update(self.run_id, self._finish)

    def _run_stage(self, index: int, stage: dict[str, Any]) -> bool:
        """Run one stage; returns False when the Run ended without the stage succeeding."""
        fresh_run = self.store.get(self.run_id)
        if fresh_run.get("stop_requested"):
            self.store.update(
                self.run_id,
                lambda r: r.update(status="stopped", stop_kind=r["stop_requested"], processes=[], finished=utc_now()),
            )
            return False
        if self.preparing and self.context.reuse(self.run, stage["name"], index):
            return True
        pair = self._mark_running(index, stage, fresh_run)
        code = self._launch(index, stage)
        if code is None:
            return False
        return self._record_outcome(index, stage, code, pair)

    def _mark_running(self, index: int, stage: dict[str, Any], fresh_run: dict[str, Any]) -> dict[str, Any] | None:
        """Record the stage and Run as running; returns the expert pair a paired sampling stage uses."""
        pair = None
        if stage.get("training"):
            validate_phase_input(fresh_run, stage)
        if stage["name"] == "generate":
            if is_paired(self.run):
                pair = sampling_pair(fresh_run, stage)
                self.store.update(self.run_id, lambda r: r.update(sample_pair=pair))
            self.store.update(self.run_id, lambda r: r.update(status="generating"))
        elif not self.preparing:
            self.store.update(self.run_id, lambda r: r.update(status="training"))
        self.store.update(self.run_id, lambda r: r["stages"][index].update(status="running", started=utc_now()))
        return pair

    def _launch(self, index: int, stage: dict[str, Any]) -> int | None:
        """Launch the stage and supervise it until every owned process exits.

        Returns the launcher's exit code, or None when the Run ended first.
        """
        settings = Settings.model_validate(self.run["settings"])
        job_object = self._open_job_object(index)
        log_file = Path(self.run["directory"]) / stage_log(stage)
        with log_file.open("a", encoding="utf-8") as log:
            self.process = process = self._spawn(stage, settings, job_object, log)
            self.performance = start_performance(
                self.run, stage, process.pid, log_file, fixture=self.context.fixture_mode
            )
            identities = self._record_launch(process.pid)
            supervised = self._supervise(stage, process, job_object, identities)
            if supervised is None:
                return None
            identities, signalled = supervised
            code = process.wait()
            if self.performance:
                self.performance.close(code)
                self.performance = None
            if isinstance(process, WindowsProcess):
                process.close()
            if not self._settle_descendants(job_object, identities, signalled):
                return None
        return code

    def _open_job_object(self, index: int) -> ProcessJob:
        """Create this stage's named Job Object, recorded before launch so a restart can always find it."""
        if self.job_object:
            self.job_object.close()
        root = digest(str(self.context.root.resolve()))[:12]
        name = f"Local\\MusubiGui-{root}-{self.run_id}-{index}"
        self.job_object = ProcessJob(name)
        names = list(self.store.get(self.run_id).get("job_objects", []))
        if name not in names:
            names.append(name)
        self.store.update(self.run_id, lambda r: r.update(job_objects=names, launch_pending=True, launch_protocol=3))
        self.device.update(self.run_id, [], job_objects=names, launch_pending=True, launch_protocol=3)
        return self.job_object

    def _spawn(
        self, stage: dict[str, Any], settings: Settings, job_object: ProcessJob, log: TextIO
    ) -> WindowsProcess | subprocess.Popen[str]:
        environment = reference_environment(Path(self.run["cache"]))
        environment.pop("MUSUBI_EXPERT_STEP_OFFSET", None)
        if (offset := stage.get("training", {}).get("checkpoint_step_offset")) is not None:
            environment["MUSUBI_EXPERT_STEP_OFFSET"] = str(offset)
        if seed_wan_tokenizer(self.run):
            environment["HF_HUB_OFFLINE"] = "1"
            log.write("Using the shared Wan tokenizer; Hugging Face Hub access is off for this stage.\n")
            log.flush()
        if os.name == "nt":
            return job_object.spawn(stage["args"], cwd=settings.checkout, env=environment, log=log)
        # B603: trusted recipe adapter builds arrays; no API accepts executable commands.
        return subprocess.Popen(  # nosec B603
            stage["args"],
            cwd=settings.checkout,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )

    def _record_launch(self, pid: int) -> list[dict[str, Any]]:
        identities = observed_tree(pid)
        self.store.update(self.run_id, lambda r: r.update(processes=identities))
        self.device.update(self.run_id, identities)
        self.store.update(self.run_id, lambda r: r.update(processes=identities, launch_pending=False))
        self.device.update(self.run_id, identities, launch_pending=False)
        return identities

    def _supervise(
        self,
        stage: dict[str, Any],
        process: WindowsProcess | subprocess.Popen[str],
        job_object: ProcessJob,
        identities: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], str] | None:
        """Track the owned tree and honour cancel and stop until the launcher exits.

        Returns every identity seen and the stop kind signalled, or None when the service stopped first.
        """
        signalled = ""
        retention_at = time.monotonic() + 5
        while process.poll() is None:
            fresh = observed_tree(process.pid)
            for pid in job_object.pids():
                try:
                    fresh.append(process_identity(pid))
                except psutil.NoSuchProcess:
                    pass
            identities = list({(item["pid"], item["created"]): item for item in [*identities, *fresh]}.values())
            self.device.update(self.run_id, identities)
            self.store.update(self.run_id, lambda r: r.update(processes=identities))
            if self.store.get(self.run_id).get("cancel_requested"):
                job_object.terminate()
                terminate_owned(identities, force=True)
            signalled = signal_stop(self.store, self.run_id, job_object, identities, signalled)
            if stage["name"] == "train" and time.monotonic() >= retention_at:
                self.context.request_retention(self.run_id)
                retention_at = time.monotonic() + 5
            if self.context.stopping.wait(0.1):
                self.release = False
                self.store.update(self.run_id, lambda r: r.update(status="interrupted", error=INTERRUPTED))
                if isinstance(process, WindowsProcess):
                    process.close()
                else:
                    threading.Thread(target=process.wait, name="musubi-child-reaper", daemon=True).start()
                return None
        return identities, signalled

    def _settle_descendants(self, job_object: ProcessJob, identities: list[dict[str, Any]], signalled: str) -> bool:
        """Wait out descendants after their launcher; a survivor keeps the lease and ends the Run."""
        if self._await_descendant_exit(job_object, identities, signalled):
            return True
        self.release = False
        self.device.update(self.run_id, identities)
        if self.context.stopping.is_set():
            self.store.update(
                self.run_id, lambda r: r.update(status="interrupted", processes=identities, error=INTERRUPTED)
            )
            return False
        self.store.update(
            self.run_id,
            lambda r: r.update(
                status="orphaned",
                processes=identities,
                error="The launcher exited but a descendant remains active. Check for leftover processes before starting another GPU operation.",
            ),
        )
        return False

    def _await_descendant_exit(self, job_object: ProcessJob, identities: list[dict[str, Any]], signalled: str) -> bool:
        """Allow a bounded grace period for descendants still exiting after their launcher, such as
        the Python child behind a venv launcher or a short-lived nvidia-smi. Records late arrivals
        and still honours stop requests; returns whether every owned process has exited."""
        deadline = time.monotonic() + self.context.descendant_grace
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
                self.device.update(self.run_id, identities)
                self.store.update(self.run_id, lambda r: r.update(processes=identities))
            if not remaining and all(identity_status(identity) == "dead" for identity in identities):
                return True
            signalled = signal_stop(self.store, self.run_id, job_object, identities, signalled)
            if time.monotonic() >= deadline or self.context.stopping.wait(0.1):
                return False

    def _record_outcome(self, index: int, stage: dict[str, Any], code: int, pair: dict[str, Any] | None) -> bool:
        """Record how the stage ended; returns False when the Run ended with it."""
        run_id = self.run_id
        requested = self.store.get(run_id).get("stop_requested")
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
            return False
        if code:
            if self.store.get(run_id).get("cancel_requested"):
                self.store.update(run_id, lambda r: r.update(status="cancelled", processes=[], finished=utc_now()))
                return False
            raise ValueError(f"{stage['name']} exited {code}. Inspect logs, then prepare a new Run from frozen inputs.")
        share_wan_tokenizer(self.run)
        if self.preparing:
            artifacts = cache_artifacts(self.run, stage["name"])
            if self.run.get("stage_keys") and not artifacts:
                raise ValueError(f"{stage['name']} returned success but produced no complete cache artifacts.")
            self.store.update(run_id, lambda r: r["stages"][index].update(artifacts=artifacts))
        elif stage["name"] == "train":
            record_checkpoints(self.store, self.run, stage)
            self.context.request_retention(run_id)
        elif stage["name"] == "generate":
            record_samples(self.store, self.run, stage, pair)
        return True

    def _finish(self, record: dict[str, Any]) -> None:
        record.update(
            status="prepared" if self.preparing else "completed", processes=[], exit_code=0, finished=utc_now()
        )
        if (
            self.preparing
            and record.get("start_after_prepare")
            and not record.get("cancel_requested")
            and any(stage["name"] == "train" for stage in record["stages"])
        ):
            # Atomic with the end of preparation: a crash can't leave it prepared but never started.
            record.update(status="pending_training", phase="train", order=time.time_ns())
            record.pop("finished", None)

    def _clean_up(self) -> None:
        if self.performance:
            self.performance.close()
        if isinstance(self.process, WindowsProcess):
            self.process.close()
        if self.job_object:
            try:
                if self.job_object.pids():
                    self.release = False
            except (OSError, ValueError):
                self.release = False
            self.job_object.close()
        failure, release = self.failure, self.release
        if failure is not None:
            # A retryable terminal failure and its cleared ownership records must become visible together.
            self.store.update(
                self.run_id,
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
                self.store.update(self.run_id, lambda r: r.update(processes=[], launch_pending=False))
            self.device.release(self.run_id)


def signal_stop(
    store: RunStore, run_id: str, job_object: ProcessJob, identities: list[dict[str, Any]], signalled: str
) -> str:
    """Signal a newly requested stop to the owned tree once; returns the stop kind now signalled."""
    stop = store.get(run_id).get("stop_requested", "")
    if not stop or stop == signalled:
        return signalled
    try:
        if stop == "force":
            job_object.terminate()
        terminate_owned(identities, force=stop == "force")
    except OSError:
        store.update(
            run_id,
            lambda r: r.update(
                error="Graceful console signalling is unavailable. Use confirmed force termination if the process remains active."
            ),
        )
    return str(stop)
