"""Hold a query-only named-job handle until the controller is gone and all stage members exit."""

import json
import sys
import time
from pathlib import Path


def main() -> None:
    # -I excludes CWD/PYTHONPATH. Pair this fixed helper with its own checkout or installed package,
    # even when the selected application interpreter has another checkout installed editably.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from musubi_gui.processes import identity_status
    from musubi_gui.windows_job import WAIT_OBJECT_0, WAIT_TIMEOUT, ProcessJob

    name, ready_name, stop_name, identity = sys.argv[1:]
    controller = json.loads(identity)
    job = ProcessJob(name, create=False, observe_only=True)
    ready = stop = None
    try:
        if not job.handle:
            return  # Controller disappeared before establishing a job; no stage could have started.
        ready = job.api.OpenEventW(0x0002, False, ready_name)  # EVENT_MODIFY_STATE
        stop = job.api.OpenEventW(0x00100000, False, stop_name)  # SYNCHRONIZE
        if not ready or not stop:
            return
        if not job.api.SetEvent(ready):
            return
        while True:
            result = job.api.WaitForSingleObject(stop, 100)
            if result not in {WAIT_OBJECT_0, WAIT_TIMEOUT}:
                return
            finished = result == WAIT_OBJECT_0 or identity_status(controller) == "dead"
            try:
                if finished and not job.pids():
                    return
            except (OSError, ValueError):
                pass  # Retain discoverability when membership cannot be safely observed.
            if result == WAIT_OBJECT_0:
                time.sleep(0.1)  # A signalled manual-reset stop event otherwise returns immediately.
    finally:
        for event in [ready, stop]:
            if event:
                job.api.CloseHandle(event)
        job.close()


if __name__ == "__main__":
    main()
