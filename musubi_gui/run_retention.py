"""Applying each Run's frozen checkpoint retention policy, inline or coalesced on a background thread."""

import threading
from pathlib import Path

from musubi_gui.checkpoint_retention import Save, apply_retention, retention_candidates
from musubi_gui.redaction import redact
from musubi_gui.run_store import RunStore


class RetentionScheduler:
    def __init__(self, store: RunStore, stopping: threading.Event):
        self.store = store
        # The owning service's shutdown signal, which also cancels an inspection in progress.
        self.stopping = stopping
        self.stamps: dict[str, tuple[tuple[str, int, int], ...]] = {}
        self.validation: dict[str, tuple[object, Save]] = {}
        self.lock = threading.RLock()
        self.condition = threading.Condition()
        self.requested: set[str] = set()
        self.thread = threading.Thread(target=self._retain_requested, name="musubi-retention", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def retain(self, run_id: str) -> None:
        """Apply the frozen Run's policy; failure preserves training and is visible for repair."""
        with self.lock:
            self._retain(run_id)

    def request(self, run_id: str) -> None:
        """Coalesce work without delaying process supervision or Stop signalling."""
        with self.condition:
            self.requested.add(run_id)
            self.condition.notify()

    def wake(self) -> None:
        """Let the background thread see that the service is stopping."""
        with self.condition:
            self.condition.notify_all()

    def join(self) -> None:
        if self.thread.is_alive():
            # Cancellation interrupts chunked hashes and kills/reaps an active state probe.
            self.thread.join(timeout=45)
            if self.thread.is_alive():
                raise RuntimeError("Background checkpoint inspection did not finish after cancellation.")

    def _retain_requested(self) -> None:
        while not self.stopping.is_set():
            with self.condition:
                self.condition.wait_for(lambda: self.requested or self.stopping.is_set())
                if self.stopping.is_set():
                    return
                run_id = self.requested.pop()
            try:
                self.retain(run_id)
            except (OSError, ValueError):
                # A terminal Run may have been deleted after its final request was queued.
                continue

    def _retain(self, run_id: str) -> None:
        run = self.store.get(run_id)
        if run["job"].get("config", {}).get("keep_checkpoints", 0) <= 0:
            return
        try:
            root = Path(run["directory"]) / "checkpoints"
            stamp = tuple(
                sorted(
                    (str(path), path.stat().st_mtime_ns, path.stat().st_size)
                    for path in root.rglob("*")
                    if path.is_file() and ".retention" not in path.parts
                )
            )
            if self.stamps.get(run_id) == stamp and not run.get("retention", {}).get("pending"):
                return
            candidates, protected = retention_candidates(run, self.validation, self.stopping)
            if self.stopping.is_set():
                return
            self.store.update_with_others(
                run_id, lambda record, others: apply_retention(record, others, candidates, protected)
            )
            self.stamps[run_id] = stamp
        except (OSError, ValueError) as error:
            message = redact(str(error))
            self.store.update(run_id, lambda record: record.setdefault("retention", {}).update(errors=[message]))
