"""Where the application finds and keeps its files.

From source:        everything is in the project folder (the folder that holds pulls_tracker.py).
Built with PyInstaller: the files shipped with the program (the user interface) are read from PyInstaller's bundle folder
                    (sys._MEIPASS, the "_internal" folder next to the .exe), and everything the program writes goes in a "data" folder
                    next to the .exe, so the program folder can be moved or copied as a whole (see README, "Packaging").
                    When that folder cannot be written (for example C:\\Program Files) the data folder is %LOCALAPPDATA%\\PullsTracker\\data
                    instead, and the log and the self check say so.
The environment variable PULLS_TRACKER_DATA, when set, gives the data folder in any case.
"""
from __future__ import annotations

import functools
import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Files shipped with the program (read only)."""
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent


def app_dir() -> Path:
    """The folder the user sees: the folder of the .exe, or the project folder when run from source."""
    return Path(sys.executable).resolve().parent if FROZEN else bundle_dir()


def ui_dir() -> Path:
    return bundle_dir() / "pulls_tracker_ui"


@functools.lru_cache(maxsize=None)
def _writable(folder: str) -> bool:
    """True when files can be created in `folder` (it is created when it does not exist)."""
    try:
        d = Path(folder)
        d.mkdir(parents=True, exist_ok=True)
        probe = d / f".write_test.{os.getpid()}"      # one name per process: two copies starting together must not delete each other's probe
        probe.write_text("x")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def fallback_data_dir() -> Path:
    """Used only when the folder of the .exe cannot be written."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "PullsTracker" / "data"


def data_dir() -> Path:
    """Everything the program writes: settings, pull database, downloaded assets, browser storage."""
    forced = os.environ.get("PULLS_TRACKER_DATA")
    if forced:
        return Path(forced).expanduser().resolve()
    here = app_dir() / "data"
    if FROZEN and not _writable(str(here)):
        return fallback_data_dir()
    return here


def database_path() -> Path:
    """The pull database. The one place that names the file: the app and the startup check of pulls_tracker.py both use it."""
    return data_dir() / "pulls.sqlite"


CLOUD_FOLDERS = ("onedrive", "dropbox", "google drive", "googledrive", "icloud", "mega", "pcloud")


def cloud_folder(path) -> str:
    """The name of a file-sync service when `path` is inside its folder (OneDrive, Dropbox ...), else an empty string. The pull database
    is a SQLite file with a write-ahead log; a sync program that locks or rewrites those files while the program runs can damage it."""
    for part in Path(path).parts:
        low = part.lower()
        for name in CLOUD_FOLDERS:
            if low == name or low.startswith(name + " ") or low.startswith(name + "-"):
                return part
    return ""


def data_dir_note() -> str:
    """One line that says where the data is and why (for the log and the self check)."""
    d = data_dir()
    note = str(d)
    if os.environ.get("PULLS_TRACKER_DATA"):
        note = f"{d} (set by PULLS_TRACKER_DATA)"
    elif FROZEN and d != app_dir() / "data":
        note = f"{d} (the folder of the program, {app_dir()}, cannot be written)"
    sync = cloud_folder(d)
    if sync:
        note += f" WARNING: this folder is inside {sync}; a file-sync program can lock the database files while the program runs. A folder outside it is safer."
    return note


def loading_assets_dir() -> Path:
    """Where the user can put the banner of the loading panel (banner.png ...): it is read from here before the one shipped with the interface."""
    return data_dir() / "assets" / "loading"


def webview_storage_dir() -> Path:
    return data_dir() / "webview"


def ensure_dirs() -> None:
    for d in (data_dir(), webview_storage_dir(), loading_assets_dir()):
        d.mkdir(parents=True, exist_ok=True)
