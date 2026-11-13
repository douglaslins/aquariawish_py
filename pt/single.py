"""One running copy per data folder.

Two copies of the program on the same data folder would share the browser storage of the window, the settings file and the pull database
(and could both start a sync). A second start finds the first one and ends with a short message instead.

Windows: a named mutex (its name is made from the data folder, so a copy of the program with another data folder can run beside it).
Elsewhere: an exclusive lock on data/.instance.lock. Both are released by the system when the process ends, however it ends.
Any failure of the guard itself lets the program start: a broken guard must not keep the program from running.
"""
from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path

log = logging.getLogger("pt.single")

ERROR_ALREADY_EXISTS = 183


class Guard:
    """`acquired` is False only when another copy is running on the same data folder."""

    def __init__(self, acquired: bool, handle=None, library=None) -> None:
        self.acquired = acquired
        self._handle = handle          # kept so the mutex or the file stays open for the life of the process
        self._library = library        # Windows: the kernel32 object whose argument types are set (the one that created the mutex)

    def release(self) -> None:
        h, self._handle = self._handle, None
        if h is None:
            return
        try:
            if self._library is not None:
                self._library.CloseHandle(h)
            else:
                h.close()
        except Exception:      # noqa: BLE001
            log.debug("Could not release the instance guard", exc_info=True)


def mutex_name(data_dir: Path) -> str:
    # sha1 only makes a short name out of a path: it protects nothing (and keeps the name of earlier versions).
    digest = hashlib.sha1(str(Path(data_dir).resolve()).lower().encode("utf-8", "replace"), usedforsecurity=False).hexdigest()
    return "Local\\PullsTracker-" + digest[:16]


def acquire(data_dir: Path) -> Guard:
    try:
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            handle = kernel32.CreateMutexW(None, False, mutex_name(data_dir))
            if not handle:
                log.warning("The instance mutex could not be created (error %s); starting anyway", ctypes.get_last_error())
                return Guard(True)
            if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(handle)
                return Guard(False)
            return Guard(True, handle, kernel32)
        import fcntl
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        f = open(Path(data_dir) / ".instance.lock", "a+")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return Guard(False)
        return Guard(True, f)
    except Exception:      # noqa: BLE001
        log.warning("The instance guard failed; starting anyway", exc_info=True)
        return Guard(True)
