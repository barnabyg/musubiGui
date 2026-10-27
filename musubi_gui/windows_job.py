"""Named Windows Job Objects retain process-tree identity across service restarts.

No kill-on-close limit is set: closing the browser/service must not kill training.
https://learn.microsoft.com/windows/win32/procthread/job-objects
"""

import ctypes
import json
import math
import os
import struct
import subprocess  # nosec B404
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, TextIO

PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_NEW_PROCESS_GROUP = 0x00000200
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
INFINITE = 0xFFFFFFFF


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", ctypes.c_void_p)]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class WindowsProcess:
    """Own only the process handle; closing it never terminates surviving work."""

    def __init__(self, api: Any, handle: Any, pid: int, args: list[str]):
        self.api = api
        self.handle = handle
        self.pid = pid
        self.args = args
        self.returncode: int | None = None

    def _wait(self, milliseconds: int) -> int | None:
        if self.returncode is not None:
            return self.returncode
        if not self.handle:
            raise ValueError("The process observation handle is closed.")
        result = self.api.WaitForSingleObject(self.handle, milliseconds)
        if result == WAIT_TIMEOUT:
            return None
        if result != WAIT_OBJECT_0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = wintypes.DWORD()
        if not self.api.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        # WaitForSingleObject established termination, so 259 is a valid real exit code here.
        self.returncode = int(code.value)
        return self.returncode

    def poll(self) -> int | None:
        return self._wait(0)

    def wait(self, timeout: float | None = None) -> int:
        milliseconds = INFINITE if timeout is None else min(INFINITE - 1, max(0, math.ceil(timeout * 1000)))
        code = self._wait(milliseconds)
        if code is None:
            raise subprocess.TimeoutExpired(self.args, 0 if timeout is None else timeout)
        return code

    def kill(self) -> None:
        if self.poll() is None and not self.api.TerminateProcess(self.handle, 1):
            error = ctypes.get_last_error()
            if self.poll() is None:
                raise ctypes.WinError(error)

    def close(self) -> None:
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class ProcessJob:
    def __init__(self, name: str, *, create: bool = True, observe_only: bool = False):
        self.name = name
        self.handle: Any = None
        self.api: Any = None
        self._ready: Any = None
        self._stop: Any = None
        self._keeper: subprocess.Popen[bytes] | None = None
        if os.name != "nt":
            return
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "OpenJobObjectW": ([wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            "GetCurrentProcess": ([], wintypes.HANDLE),
            "CreateEventW": ([ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            "OpenEventW": ([wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetEvent": ([wintypes.HANDLE], wintypes.BOOL),
            "DuplicateHandle": (
                [
                    wintypes.HANDLE,
                    wintypes.HANDLE,
                    wintypes.HANDLE,
                    ctypes.POINTER(wintypes.HANDLE),
                    wintypes.DWORD,
                    wintypes.BOOL,
                    wintypes.DWORD,
                ],
                wintypes.BOOL,
            ),
            "InitializeProcThreadAttributeList": (
                [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)],
                wintypes.BOOL,
            ),
            "UpdateProcThreadAttribute": (
                [
                    ctypes.c_void_p,
                    wintypes.DWORD,
                    ctypes.c_size_t,
                    ctypes.c_void_p,
                    ctypes.c_size_t,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                ],
                wintypes.BOOL,
            ),
            "DeleteProcThreadAttributeList": ([ctypes.c_void_p], None),
            "CreateProcessW": (
                [
                    wintypes.LPCWSTR,
                    wintypes.LPWSTR,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    wintypes.BOOL,
                    wintypes.DWORD,
                    ctypes.c_void_p,
                    wintypes.LPCWSTR,
                    ctypes.POINTER(_StartupInfoEx),
                    ctypes.POINTER(_ProcessInformation),
                ],
                wintypes.BOOL,
            ),
            "QueryInformationJobObject": (
                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p],
                wintypes.BOOL,
            ),
            "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            "GetExitCodeProcess": ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "TerminateProcess": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for function, (arguments, result) in signatures.items():
            call = getattr(api, function)
            call.argtypes, call.restype = arguments, result
        self.api = api
        self.handle = (
            api.CreateJobObjectW(None, name)
            if create
            else api.OpenJobObjectW(0x0004 if observe_only else 0x0004 | 0x0008, False, name)
        )
        if not self.handle and (create or ctypes.get_last_error() != 2):
            raise ctypes.WinError(ctypes.get_last_error())

    def _ensure_keeper(self) -> None:
        """Retain a query handle outside both the controller and the stage process tree."""
        if self._keeper is not None:
            if self._keeper.poll() is not None:
                raise ValueError("The Windows job observer stopped; create a new tracked launch before retrying.")
            return
        from musubi_gui.processes import process_identity

        ready_name, stop_name = self.name + "-keeper-ready", self.name + "-keeper-stop"
        self._ready = self.api.CreateEventW(None, True, False, ready_name)
        if not self._ready:
            raise ctypes.WinError(ctypes.get_last_error())
        self._stop = self.api.CreateEventW(None, True, False, stop_name)
        if not self._stop:
            raise ctypes.WinError(ctypes.get_last_error())
        args = [
            sys.executable,
            "-I",
            "-B",
            "-X",
            "utf8",
            str(Path(__file__).with_name("job_keeper.py")),
            self.name,
            ready_name,
            stop_name,
            json.dumps(process_identity()),
        ]
        # B603: fixed application helper, local named objects and observed identity; no shell or user commands.
        self._keeper = subprocess.Popen(  # nosec B603
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
        )
        deadline = time.monotonic() + 10
        while self._keeper.poll() is None and time.monotonic() < deadline:
            result = self.api.WaitForSingleObject(self._ready, 100)
            if result == WAIT_OBJECT_0:
                return
            if result != WAIT_TIMEOUT:
                raise ctypes.WinError(ctypes.get_last_error())
        raise ValueError("The Windows job observer could not acknowledge ownership; no training process was started.")

    def spawn(self, args: list[str], *, cwd: str, env: dict[str, str], log: TextIO) -> WindowsProcess:
        """Create a process atomically inside this job, with only its standard handles inherited.

        JOB_LIST and HANDLE_LIST values must remain alive until the attribute list is deleted:
        https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute
        """
        if not self.api or not self.handle:
            raise ValueError("A live Windows Job Object is required for atomic process creation.")
        if not args or not Path(args[0]).is_absolute() or any("\0" in argument for argument in args):
            raise ValueError("Select an absolute executable and valid local stage arguments.")
        if any(not key or "=" in key[1:] or "\0" in key or "\0" in value for key, value in env.items()):
            raise ValueError("The selected stage environment contains invalid entries.")
        command = subprocess.list2cmdline(args)
        if len(command.encode("utf-16-le")) // 2 + 1 > 32767:
            raise ValueError("The stage command exceeds the Windows process command-line limit.")
        self._ensure_keeper()  # Acknowledged query handle exists before any GPU-capable child can be created.
        command_line = ctypes.create_unicode_buffer(command)
        environment = ctypes.create_unicode_buffer(
            "".join(f"{key}={env[key]}\0" for key in sorted(env, key=str.upper)) + "\0"
        )
        size = ctypes.c_size_t()
        self.api.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
        if not size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        attributes = ctypes.create_string_buffer(size.value)
        if not self.api.InitializeProcThreadAttributeList(attributes, 2, 0, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        inherited: list[int] = []
        process = _ProcessInformation()
        try:
            import msvcrt

            log.flush()
            with open(os.devnull, "rb") as null:
                current = self.api.GetCurrentProcess()
                for descriptor in [null.fileno(), log.fileno()]:
                    duplicate = wintypes.HANDLE()
                    if not self.api.DuplicateHandle(
                        current, msvcrt.get_osfhandle(descriptor), current, ctypes.byref(duplicate), 0, True, 2
                    ):
                        raise ctypes.WinError(ctypes.get_last_error())
                    if duplicate.value is None:
                        raise OSError("Process standard-handle duplication returned no handle.")
                    inherited.append(duplicate.value)
                duplicate = wintypes.HANDLE()
                if not self.api.DuplicateHandle(
                    current, self.handle, current, ctypes.byref(duplicate), 0x0004, True, 0
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
                if duplicate.value is None:
                    raise OSError("Job observation handle duplication returned no handle.")
                inherited.append(duplicate.value)
                handles = (wintypes.HANDLE * 3)(*inherited)
                jobs = (wintypes.HANDLE * 1)(self.handle)
                for key, value in [
                    (PROC_THREAD_ATTRIBUTE_JOB_LIST, jobs),
                    (PROC_THREAD_ATTRIBUTE_HANDLE_LIST, handles),
                ]:
                    if not self.api.UpdateProcThreadAttribute(
                        attributes, 0, key, ctypes.byref(value), ctypes.sizeof(value), None, None
                    ):
                        raise ctypes.WinError(ctypes.get_last_error())
                startup = _StartupInfoEx()
                startup.StartupInfo.cb = ctypes.sizeof(startup)
                startup.StartupInfo.dwFlags = (
                    0x00000100 | 0x00000001
                )  # USESTDHANDLES | USESHOWWINDOW; hide new consoles.
                startup.StartupInfo.wShowWindow = 0
                startup.StartupInfo.hStdInput = handles[0]
                startup.StartupInfo.hStdOutput = startup.StartupInfo.hStdError = handles[1]
                startup.lpAttributeList = ctypes.addressof(attributes)
                flags = EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT | CREATE_NEW_PROCESS_GROUP
                # Fixed recipe arrays, explicit executable, no shell, restricted inherited handles.
                if not self.api.CreateProcessW(
                    args[0],
                    command_line,
                    None,
                    None,
                    True,
                    flags,
                    environment,
                    cwd,
                    ctypes.byref(startup),
                    ctypes.byref(process),
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
                child = WindowsProcess(self.api, process.hProcess, int(process.dwProcessId), args)
                process.hProcess = None  # Ownership transfers to the observation wrapper.
                return child
        finally:
            self.api.DeleteProcThreadAttributeList(attributes)
            for handle in inherited:
                self.api.CloseHandle(handle)
            if process.hThread:
                self.api.CloseHandle(process.hThread)
            if process.hProcess:
                self.api.CloseHandle(process.hProcess)

    def pids(self) -> list[int]:
        if not self.api or not self.handle:
            return []
        size = 65536
        while size <= 8 * 1024**2:
            buffer = ctypes.create_string_buffer(size)
            # JobObjectBasicProcessIdList = 3, includes nested descendants.
            if self.api.QueryInformationJobObject(self.handle, 3, buffer, size, None):
                assigned, count = struct.unpack_from("II", buffer.raw)
                if count == assigned:
                    return [int(pid) for pid in (ctypes.c_size_t * count).from_buffer(buffer, 8)]
            elif ctypes.get_last_error() != 234:
                raise ctypes.WinError(ctypes.get_last_error())
            size *= 2
        raise ValueError("Process tree exceeds the safe observation buffer; GPU ownership remains blocked.")

    def terminate(self) -> None:
        if self.api and self.handle and not self.api.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self.api and self._stop:
            self.api.SetEvent(self._stop)
        for event in [self._ready, self._stop]:
            if self.api and event:
                self.api.CloseHandle(event)
        self._ready = self._stop = None
        if self.api and self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None
        if self._keeper is not None:
            # The helper deliberately outlives this handle while stage descendants remain.
            # Retain Popen until reaped, without keeping the service alive or killing training.
            if self._keeper.poll() is None:
                threading.Thread(target=self._keeper.wait, name="musubi-job-observer-reaper", daemon=True).start()
            self._keeper = None
