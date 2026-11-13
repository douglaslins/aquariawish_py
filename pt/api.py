"""The methods of window.PT_API that the Python side answers (the contract is in pulls_tracker_ui/README.md and js/api.js).

PullsApi has the data methods (accounts, dashboard, history, stats, settings, import and export). WindowApi (pt/window.py) adds the window
methods. A method the page calls that is not here is answered by the page itself (js/api.js): read methods like an account with no pulls,
the others with { unavailable: true }. Names starting with an underscore are not visible to the page.

An error inside a method is written to the log and answered as { error: "..." } (plus the empty shape of the screen), never raised: a
rejected call would leave the screen waiting.
"""
from __future__ import annotations

import contextlib
import functools
import logging
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Optional

from pt import banners, paths, sync, vault
from pt.games import GAMES
from pt.store import Store
from pt.tracker import MAX_IMPORT_BYTES, Tracker

log = logging.getLogger("pt.api")


def safe(fallback, tag: bool = True):
    """Wrap an API method: log an exception and answer `fallback` instead of raising. A dict gets "error" (the text) and "failed": true
    (js/api.js raises that, so the page shows the error and not the fallback as if it were the answer; an answer that only carries an
    "error" text, such as a refused import, is not marked). tag=False is for a dict that is a map the page indexes by id: it stays as is."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            try:
                return fn(self, *args, **kwargs)
            except Exception as e:
                log.exception("%s failed", fn.__name__)
                if isinstance(fallback, dict) and tag:
                    return {**fallback, "error": f"{type(e).__name__}: {e}", "failed": True}
                return fallback
        return wrapper
    return deco


def _game(game) -> str:
    if game not in GAMES:
        raise ValueError(f"unknown game {game!r}")
    return game


def _dialog_kind(webview, name: str):
    enum = getattr(webview, "FileDialog", None)
    return getattr(enum, name, None) or getattr(webview, {"OPEN": "OPEN_DIALOG", "SAVE": "SAVE_DIALOG", "FOLDER": "FOLDER_DIALOG"}[name])


def _write_text_atomic(path: Path, text: str) -> None:
    """Write beside the target and rename it over: a failed write (disk full, a program that locks the file) never leaves the file that the
    person chose in the save dialog cut in half."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)             # mkstemp makes the file private to its owner; an export is an ordinary file
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def default_folder() -> Path:
    for name in ("Downloads", "Documents"):
        p = Path.home() / name
        if p.is_dir():
            return p
    return Path.home()


class PullsApi:
    def __init__(self, tracker: Optional[Tracker] = None) -> None:
        self._tracker_obj = tracker
        self._tracker_lock = threading.Lock()
        self._window = None
        self._picked_import: Optional[str] = None      # the file the person chose in the dialog of pickImportFile: the only path importPreview reads
        self._sync_lock = threading.Lock()
        self._sync_thread: Optional[threading.Thread] = None
        self._sync_cancel = threading.Event()
        self._sync_options: dict = {}          # extra arguments of Syncer (the tests give a fake service and no waiting)

    @property
    def _tracker(self) -> Tracker:
        """The service, created at the first use (the database file is opened then, not when the window object is made)."""
        with self._tracker_lock:
            if self._tracker_obj is None:
                self._tracker_obj = Tracker(Store(paths.database_path()))
                if self._tracker_obj.store.recovered:
                    log.error("The pull database was damaged; the damaged file is kept as %s", self._tracker_obj.store.recovered)
            return self._tracker_obj

    def _warm_up(self) -> None:
        """Open the database before the main page asks for data (so a problem with it is in the log at the start)."""
        try:
            self._tracker.state()
        except Exception:
            log.exception("The pull database could not be opened")

    def _emit(self, event: dict) -> None:
        """Send an event to the page (PT_EVENTS.emit). The window class does this; without a window the event is dropped."""

    # ---- accounts and settings -------------------------------------------------------------------------------------------------

    @safe({"synced": False, "accounts": [], "lastSync": {}})
    def state(self) -> dict:
        return self._tracker.state()

    @safe({"language": "English"})
    def getSettings(self) -> dict:
        return self._tracker.settings()

    @safe({"unavailable": True})
    def saveSettings(self, settings) -> dict:
        return self._tracker.save_settings(settings if isinstance(settings, dict) else {})

    @safe(False)
    def saveNickname(self, game, uid, nickname) -> bool:
        return self._tracker.save_nickname(_game(game), str(uid), str(nickname))

    @safe(False)
    def removeAccount(self, game, uid) -> bool:
        return self._tracker.remove_account(_game(game), str(uid))

    @safe(False)
    def setActiveAccount(self, game, uid) -> bool:
        """The account the screens show for a game (when it has more than one)."""
        return self._tracker.set_active(_game(game), str(uid))

    # ---- screens ---------------------------------------------------------------------------------------------------------------

    @safe({"empty": True})
    def dashboard(self, game) -> dict:
        return self._tracker.dashboard(_game(game))

    @safe({}, tag=False)
    def windowStats(self, game, items) -> dict:
        return self._tracker.window_stats(_game(game), items if isinstance(items, list) else [])

    @safe({"total": 0, "rows": []})
    def windowPulls(self, game, query=None) -> dict:
        q = query if isinstance(query, dict) else {}
        return self._tracker.window_pulls(_game(game), q.get("group"), q.get("start"), q.get("end"), q.get("limit", 8))

    @safe([])
    def pastBanners(self, game) -> list:
        return self._tracker.past_banners(_game(game))

    @safe({"empty": True, "total": 0, "rows": [], "page": 1, "pages": 1, "pageSize": 20})
    def history(self, game, query=None) -> dict:
        return self._tracker.history(_game(game), query if isinstance(query, dict) else {})

    @safe({"unavailable": True})
    def historyExport(self, game, query=None) -> dict:
        res = self._tracker.history_csv(_game(game), query if isinstance(query, dict) else {})
        return self._deliver(res) if res else {"empty": True}

    @safe({"empty": True})
    def stats(self, game, query=None) -> dict:
        return self._tracker.stats(_game(game), query if isinstance(query, dict) else {})

    # ---- storage ---------------------------------------------------------------------------------------------------------------

    @staticmethod
    def _art_dir() -> Path:
        return paths.data_dir() / "assets" / "art"

    @staticmethod
    def _dir_size(folder: Path) -> int:
        """Bytes of the files under `folder`. A file that is gone when it is measured (another program removed it, or the cache is being
        cleared) counts as nothing: the answer of this method is a figure on the Settings screen, which cannot open without it."""
        total = 0
        for f in folder.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                continue
        return total

    @safe({"imageCacheMB": 0})
    def storage(self) -> dict:
        size = self._dir_size(self._art_dir()) if self._art_dir().is_dir() else 0
        folder = paths.data_dir()
        sync_name = paths.cloud_folder(folder)
        warning = (f"This folder is inside {sync_name}. A file-sync program can lock or rewrite the database files while the program runs. "
                   "Keep the program in a folder outside it, or exclude this folder from syncing.") if sync_name else None
        return {"imageCacheMB": round(size / 1e6, 1), "note": None if size else "No images stored yet.", "backups": self._tracker.backup_info(),
                "dataFolder": str(folder), "warning": warning}

    @safe({"unavailable": True})
    def backupNow(self) -> dict:
        """Settings > Storage > Back up now: a copy of the database in the backup folder, whatever the age of the newest one."""
        path = self._tracker.backup(force=True)
        return {"path": str(path), "name": path.name} if path else {"empty": True}

    @safe({"unavailable": True})
    def clearImageCache(self) -> dict:
        """Delete the downloaded banner art. The next start finds the files missing and downloads them again (load task 3)."""
        d = self._art_dir()
        if not d.is_dir():
            return {"cleared": 0}
        real, root = d.resolve(), paths.data_dir().resolve()
        if real == root or not real.is_relative_to(root / "assets"):      # a link or junction that leads out of <data>/assets, or to the data folder itself
            return {"error": "The image folder is not inside the data folder; nothing was deleted."}
        n = sum(1 for f in real.rglob("*") if f.is_file())
        shutil.rmtree(real, ignore_errors=True)          # the folder that was checked above, not the path that may lead elsewhere
        left = sum(1 for f in real.rglob("*") if f.is_file()) if real.is_dir() else 0      # a file that is open in another program stays
        return {"cleared": n - left, **({"failed": left} if left else {})}

    # ---- files -----------------------------------------------------------------------------------------------------------------

    def _dialog(self, kind: str, **options):
        """A file dialog of the window. None when there is no window or the dialog was cancelled."""
        if self._window is None:
            return None
        import webview
        res = self._window.create_file_dialog(_dialog_kind(webview, kind), **options)
        if not res:
            return None
        return res if isinstance(res, str) else res[0]

    def _deliver(self, res: dict) -> dict:
        """Save an export: with a window the user picks the file and the answer is { path }; without one the page gets the text."""
        if self._window is None:
            return res
        ext = Path(res["name"]).suffix
        label = {".json": "UIGF JSON", ".csv": "CSV"}.get(ext, "File")
        path = self._dialog("SAVE", directory=str(default_folder()), save_filename=res["name"], file_types=(f"{label} (*{ext})",))
        if not path:
            return {"cancelled": True}
        _write_text_atomic(Path(path), res["text"])
        return {"path": str(path), "name": Path(path).name, "count": res.get("count")}

    @safe({"unavailable": True})
    def chooseFolder(self, game=None) -> dict:      # noqa: ARG002 - the page passes the game; the dialog is the same for every one
        if self._window is None:
            return {"unavailable": True}
        path = self._dialog("FOLDER", directory=str(default_folder()))
        return {"path": str(path)} if path else {"cancelled": True}

    @safe({"browser": True})
    def pickImportFile(self) -> dict:
        if self._window is None:
            return {"browser": True}
        path = self._dialog("OPEN", directory=str(default_folder()), file_types=("UIGF or CSV (*.json;*.csv)", "All files (*.*)"))
        if path:
            self._picked_import = str(path)
        return {"path": str(path), "name": Path(path).name} if path else {"cancelled": True}

    @safe({"ok": False})
    def importPreview(self, source) -> dict:
        source = source if isinstance(source, dict) else {}
        game = source.get("game")
        if source.get("path"):
            if str(source["path"]) != self._picked_import:      # the page cannot name a file: only the one chosen in the dialog is read
                return {"ok": False, "error": "Choose the file with the Choose file button."}
            f = Path(self._picked_import)
            if not f.is_file():                  # also keeps out devices and pipes, which have no size and no end
                return {"ok": False, "name": f.name, "error": "The file could not be found."}
            with f.open("rb") as fh:
                raw = fh.read(MAX_IMPORT_BYTES + 1)          # bounded whatever the file turns out to be
            if len(raw) > MAX_IMPORT_BYTES:
                return {"ok": False, "name": f.name, "error": f"The file is larger than {MAX_IMPORT_BYTES // 2 ** 20} MB."}
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                return {"ok": False, "name": f.name, "error": "The file is not text in UTF-8."}
            return self._tracker.import_preview(f.name, text, game)
        return self._tracker.import_preview(str(source.get("name") or ""), str(source.get("text") or ""), game)

    @safe({"unavailable": True})
    def importApply(self, preview, choice="keep") -> dict:
        return self._tracker.import_apply(preview if isinstance(preview, dict) else {}, "file" if choice == "file" else "keep")

    @safe({"unavailable": True})
    def exportRun(self, options) -> dict:
        o = options if isinstance(options, dict) else {}
        res = self._tracker.export(_game(o.get("game")), "csv" if o.get("format") == "csv" else "uigf", str(o.get("dates") or "all"))
        return self._deliver(res) if res.get("text") else res

    # ---- sync ------------------------------------------------------------------------------------------------------------------

    @safe({"games": []})
    def firstRun(self) -> dict:
        """What the first-run screen shows: per game the folder found and the age of the saved link."""
        return sync.first_run(self._tracker)

    @safe({"unavailable": True})
    def syncStart(self, game, opts=None) -> dict:
        """Start a sync of one game in a thread and return at once; the progress comes as events (see pt/sync.py). One sync runs at a time.
        opts.newAccount: accept a UID that is not stored yet as another account."""
        game = _game(game)
        o = opts if isinstance(opts, dict) else {}
        with self._sync_lock:
            if self._sync_thread is not None and self._sync_thread.is_alive():
                return {"started": False, "busy": True}
            cancel = threading.Event()
            syncer = sync.Syncer(self._tracker, game, self._emit, cancel, new_account=o.get("newAccount") is True, **self._sync_options)
            self._sync_cancel = cancel
            self._sync_thread = threading.Thread(target=self._run_sync, args=(syncer,), name="pt-sync", daemon=True)
            self._sync_thread.start()
        return {"started": True}

    def _run_sync(self, syncer: "sync.Syncer") -> None:
        try:
            syncer.run()
        except Exception:      # noqa: BLE001 - Syncer.run answers its own errors; this is the last guard of the thread
            log.exception("The sync thread failed")

    @safe(False)
    def syncCancel(self) -> bool:
        """Stop the running sync. Events after this call are dropped; the call returns when the thread has stopped or after 5 seconds."""
        return self._stop_sync(5.0)

    def _stop_sync(self, wait: float = 5.0) -> bool:
        self._sync_cancel.set()
        th = self._sync_thread
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(wait)
        return True

    # ---- sources ---------------------------------------------------------------------------------------------------------------

    @safe({"ok": False})
    def sourceTest(self, source) -> dict:
        """Settings > Data sources > Test. 'calendar' is the default banner server, 'own' the address saved in Settings."""
        if source == "own":
            base = self._tracker.settings()["server"].strip()
            if not base:
                return {"ok": False, "error": "No server address is saved."}
            return banners.test_source(base)
        return banners.test_source(None)

    @safe({"unavailable": True})
    def saveCookie(self, cookie) -> dict:
        """The HoYoLAB cookie goes to the system keyring (never to the settings file, never to the log). An empty value removes it."""
        if not vault.available():
            return {"unavailable": True, "reason": "No keyring is available on this system."}
        return {"ok": vault.save("hoyolab", str(cookie or "").strip())}
