"""Child-process bookkeeping so ffmpeg never outlives the app.

Every ffmpeg we start is tracked and killed on shutdown. On Windows each child
is also placed in a Job Object with KILL_ON_JOB_CLOSE: when this Python process
ends for any reason (Ctrl+C, closing the console window, a crash), Windows
closes the job handle and terminates the children with it. ffmpeg runs with
CREATE_NO_WINDOW, so it would otherwise survive the console being closed.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import threading

NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_lock = threading.Lock()
_children: set[subprocess.Popen] = set()


# ------------------------------------------------------------------ Windows job object


class _BasicLimit(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimit),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_job = None


def _windows_job():
    """Create (once) a job object that kills its processes when we exit."""
    global _job
    if _job is not None or os.name != "nt":
        return _job
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        k32.CreateJobObjectW.restype = ctypes.c_void_p
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        k32.SetInformationJobObject.restype = ctypes.c_int
        k32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        k32.AssignProcessToJobObject.restype = ctypes.c_int
        k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        handle = k32.CreateJobObjectW(None, None)
        if not handle:
            _job = False
            return _job
        info = _ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                           ctypes.byref(info), ctypes.sizeof(info)):
            _job = False
            return _job
        _job = (k32, handle)
    except Exception:
        _job = False
    return _job


def _attach(proc: subprocess.Popen) -> None:
    job = _windows_job()
    if job:
        k32, handle = job
        try:
            k32.AssignProcessToJobObject(handle, ctypes.c_void_p(int(proc._handle)))  # type: ignore[attr-defined]
        except Exception:
            pass


# ------------------------------------------------------------------ public API


def popen(cmd: list[str], **kw) -> subprocess.Popen:
    kw.setdefault("creationflags", NO_WINDOW)
    proc = subprocess.Popen(cmd, **kw)
    _attach(proc)
    with _lock:
        _children.add(proc)
    return proc


def forget(proc: subprocess.Popen) -> None:
    with _lock:
        _children.discard(proc)


def run(cmd: list[str], timeout: float | None = None, **kw) -> subprocess.CompletedProcess:
    """subprocess.run() equivalent whose child is tracked (and killed on shutdown)."""
    proc = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        raise
    finally:
        forget(proc)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def kill_all() -> int:
    with _lock:
        procs = list(_children)
        _children.clear()
    for p in procs:
        try:
            if p.poll() is None:
                p.kill()
        except Exception:
            pass
    for p in procs:
        try:
            p.wait(timeout=3)
        except Exception:
            pass
    return len(procs)
