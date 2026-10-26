"""Named Windows Job Objects retain process-tree identity across service restarts.

No kill-on-close limit is set: closing the browser/service must not kill training.
https://learn.microsoft.com/windows/win32/procthread/job-objects
"""

import ctypes
import os
import struct
from ctypes import wintypes
from typing import Any

import psutil

CREATE_SUSPENDED = 0x00000004


class ProcessJob:
    def __init__(self, name: str, *, create: bool = True):
        self.name = name
        self.handle: Any = None
        self.api: Any = None
        if os.name != "nt":
            return
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "OpenJobObjectW": ([wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "OpenThread": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "QueryInformationJobObject": (
                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p],
                wintypes.BOOL,
            ),
            "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for function, (arguments, result) in signatures.items():
            call = getattr(api, function)
            call.argtypes, call.restype = arguments, result
        self.api = api
        self.handle = api.CreateJobObjectW(None, name) if create else api.OpenJobObjectW(0x0004 | 0x0008, False, name)
        if not self.handle and (create or ctypes.get_last_error() != 2):
            raise ctypes.WinError(ctypes.get_last_error())

    def assign_and_resume(self, pid: int) -> None:
        if not self.api:
            return
        process = self.api.OpenProcess(0x0100 | 0x0001 | 0x1000, False, pid)
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.api.AssignProcessToJobObject(self.handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
            threads = psutil.Process(pid).threads()
            if len(threads) != 1:
                raise ValueError("Expected one suspended initial thread before training starts.")
            thread = self.api.OpenThread(0x0002, False, threads[0].id)
            if not thread:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if self.api.ResumeThread(thread) == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                self.api.CloseHandle(thread)
        finally:
            self.api.CloseHandle(process)

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
        if self.api and self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None
