"""Coordinate the launcher handoff and finish setup in the restarted backend."""

import os
import sqlite3
import threading
import uuid
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import psutil
from fastapi import APIRouter, HTTPException

from musubi_gui.diagnostics import diagnostics, latest_report, record_report
from musubi_gui.launcher import LAUNCHER_PID, UpdateStatus, check_checkout, read_update, write_update
from musubi_gui.managed_checkout import ManagedCheckout, checkout_gate
from musubi_gui.processes import identity_status, process_identity
from musubi_gui.redaction import redact
from musubi_gui.run_store import RunStore, is_active
from musubi_gui.settings import training_settings


class AppUpdate:
    def __init__(self, checkout: ManagedCheckout, restart: Callable[[], None] | None):
        self.checkout = checkout
        self.restart = restart
        self.server = uuid.uuid4().hex
        self.controller: dict[str, Any] = {}
        if restart and (launcher_pid := os.environ.get(LAUNCHER_PID, "")).isdecimal():
            try:
                launcher = next(
                    (parent for parent in psutil.Process().parents() if parent.pid == int(launcher_pid)), None
                )
                if launcher is not None:
                    self.controller = process_identity(launcher.pid)
            except (OSError, psutil.Error):
                pass

    def status(self) -> dict[str, Any]:
        status = read_update() if self.controller else UpdateStatus()
        if status.running and identity_status(status.controller) == "dead":
            status = replace(
                status, phase="failed", message="The application update was interrupted. Retry the update."
            )
        return {
            "available": bool(self.controller),
            "server": self.server,
            "phase": status.phase,
            "message": status.message,
            "running": status.running,
        }

    def start(self) -> dict[str, Any]:
        if not self.controller or self.restart is None:
            raise ValueError("Start Musubi GUI with launch.cmd to use the application updater.")
        with checkout_gate():
            if any(is_active(run) for run in RunStore(self.checkout.state).list()):
                raise ValueError("Finish or cancel queued and active Runs before updating the application.")
            self.checkout.release_stale_lease()
            if not self.checkout.device.acquire("managed-checkout", self.controller):
                raise ValueError(
                    "Training or another setup operation owns the GPU. Wait before updating the application."
                )
            try:
                check_checkout()
                write_update(UpdateStatus("restart", "Updating and restarting Musubi GUI…", self.controller))
                self.restart()
            except BaseException:
                self.checkout.device.release("managed-checkout")
                raise
        return self.status()

    def resume(self) -> None:
        self.checkout.release_stale_lease()
        if not self.controller:
            return
        status = read_update()
        if status.controller != self.controller:
            return
        owner = self.checkout.device.owner()
        if not owner or owner["run_id"] != "managed-checkout" or owner["controller"] != self.controller:
            return
        if status.phase == "rebuild":
            threading.Thread(target=self.finish, args=(status,), name="application-update", daemon=True).start()
        elif status.phase == "failed":
            self.checkout.device.release("managed-checkout", expected=owner)

    def finish(self, status: UpdateStatus) -> None:
        checkout = self.checkout
        setup_done = False
        try:
            write_update(replace(status, phase="setup", message="Installing the supported Musubi Tuner version…"))
            checkout.write_status(
                {
                    "state": "running",
                    "message": "Installing supported Musubi source and dependencies…",
                    "controller": process_identity(),
                }
            )
            checkout.configure()
            checkout.write_status({"state": "complete", "message": "Supported Musubi version installed."})
            setup_done = True
            write_update(replace(status, phase="diagnostics", message="Running diagnostics…"))
            settings = training_settings(checkout.state)
            report = record_report(checkout.state, settings, diagnostics(settings))
            saved = latest_report(checkout.state, settings)
            if saved is None or saved["ran_at"] != report["ran_at"]:
                raise ValueError(
                    "The diagnostics report could not be saved. Check state-directory permissions and try again."
                )
            if any(check["status"] == "error" for check in report["checks"]):
                message = "Update complete. Diagnostics found problems. Review the report below."
            elif any(check["status"] == "warning" for check in report["checks"]):
                message = "Update complete. Diagnostics finished with warnings. Review the report below."
            else:
                message = "Update complete. Diagnostics passed."
            result = replace(status, phase="complete", message=message)
        except (ValueError, OSError, psutil.Error, sqlite3.Error) as error:
            message = redact(str(error))
            if not setup_done:
                try:
                    checkout.write_status({"state": "failed", "message": message})
                except OSError:
                    message += " The trainer setup status could not be saved."
            result = replace(status, phase="failed", message=message)
        finally:
            checkout.device.release("managed-checkout")
        write_update(result)


def app_update_router(update: AppUpdate | None) -> APIRouter:
    router = APIRouter(prefix="/api/app-update")

    @router.get("")
    def get() -> dict[str, Any]:
        return update.status() if update else {"available": False}

    @router.post("")
    def start() -> dict[str, Any]:
        if not update:
            raise HTTPException(409, "The application updater is unavailable in the synthetic fixture.")
        try:
            return update.start()
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        except (OSError, psutil.Error, sqlite3.Error) as error:
            raise HTTPException(
                500, "Could not start the update. Check the launcher and project-directory permissions."
            ) from error

    return router
