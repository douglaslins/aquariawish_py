"""The icon of the program (a design of your own), for the .exe and for the running window.

The file is pulls_tracker.ico. It is read from the folder that holds pulls_tracker.py (or .pyw) when the program runs from source, and from
PyInstaller's bundle folder (_internal) in the build: pulls_tracker.spec copies it there and also embeds it in the .exe. Without the file
nothing changes: the program runs with the icons it had before.

On Windows an icon is shown in three places, and each one is set in a different way:
1. The file in Explorer. Only an .exe can hold an icon (pulls_tracker.spec, icon=). A .py or .pyw file always shows the icon of Python
   files; to have your own icon on something you double click, make a shortcut to it and give the shortcut the icon (README).
2. The window and its taskbar button: window.set_window_icon sets it. Without it a program started from a .pyw (pythonw.exe) shows the
   Python icon.
3. How the taskbar groups the windows: a program started by pythonw.exe belongs to Python's own group, which also brings Python's icon.
   set_app_id() gives the program a group of its own. It is not done in the .exe build: an .exe already is its own group, and changing
   it would show a copy pinned to the taskbar from Explorer twice.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from pt import paths

log = logging.getLogger("pt.appicon")

ICON_FILE = "pulls_tracker.ico"
APP_ID = "PullsTracker.PullsTracker"      # the AppUserModelID: a short name without spaces, in the form Company.Product


def icon_path() -> Path | None:
    """The icon file, or None when the program has none."""
    f = paths.bundle_dir() / ICON_FILE
    return f if f.is_file() else None


def set_app_id() -> bool:
    """Give a program run from source (not the .exe) its own taskbar identity, so the taskbar shows the window's icon instead of Python's.
    Must be called before the first window is created. True when it was set."""
    if sys.platform != "win32" or paths.FROZEN:
        return False
    try:
        import ctypes
        fn = ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID      # a library of our own: argtypes on a function of the shared ctypes.windll would reach every other caller in the process
        fn.argtypes = [ctypes.c_wchar_p]
        fn.restype = ctypes.c_long          # an HRESULT: 0 is success
        return fn(APP_ID) == 0
    except Exception:
        log.debug("Could not set the taskbar identity", exc_info=True)
        return False
