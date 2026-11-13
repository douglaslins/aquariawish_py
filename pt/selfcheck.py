"""`PullsTracker.exe --selfcheck` (or `python pulls_tracker.py --selfcheck`): checks the things a PyInstaller build can get wrong and writes
the report to data/selfcheck.txt. The program has no console, so on Windows the result is also shown in a message box.

Checks: where the program, the interface and the data folder are; that the data folder can be written; that the interface files and the
fonts the pages need are in the build; that the modules that are loaded by name (the fetcher banner_tracker_test, pywebview and, on
Windows, its WinForms/WebView2 parts and pythonnet) can be imported; that the WebView2 files of pywebview are in the build; and, on Windows,
that the WebView2 runtime is installed.
"""
from __future__ import annotations

import contextlib
import importlib
import sys
from pathlib import Path

from pt import paths

UI_FILES = (
    "index.html", "loading.html", "css/app.css", "css/loading.css",
    "js/params.js", "js/data.js", "js/api.js", "js/app.js", "js/views.js", "js/panels.js", "js/window.js", "js/loading.js", "js/banners-base.js", "js/banners.js",
    "fonts/zzz-mona-sans.woff2",
)
WEBVIEW2_CLIENT = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"      # the Evergreen WebView2 runtime in the Windows registry


def _webview2_version() -> str | None:
    try:
        import winreg
    except ImportError:
        return None
    for root, sub in ((winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT}"),
                      (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT}"),
                      (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT}")):
        try:
            with winreg.OpenKey(root, sub) as k:
                v = winreg.QueryValueEx(k, "pv")[0]
                if v and v != "0.0.0.0":
                    return str(v)
        except OSError:
            continue
    return None


def run() -> tuple[bool, list[str]]:
    """Returns (everything needed is there, report lines). Lines start with ok / FAIL / info."""
    lines: list[str] = []
    ok = True

    def check(name: str, good: bool, detail: str = "", fail: str | None = None) -> None:
        """detail is shown with the result; fail replaces it when the check failed."""
        nonlocal ok
        ok = ok and good
        text = detail if good or fail is None else fail
        lines.append(f"{'ok  ' if good else 'FAIL'} {name}{(': ' + text) if text else ''}")

    def info(text: str) -> None:
        lines.append(f"info {text}")

    info(f"python {sys.version.split()[0]} on {sys.platform}; frozen by PyInstaller: {paths.FROZEN}")
    info(f"program: {sys.executable}")
    info(f"interface (read only): {paths.ui_dir()}")
    info(f"data: {paths.data_dir_note()}")

    try:
        paths.ensure_dirs()
        probe = paths.data_dir() / ".selfcheck"
        probe.write_text("x")
        probe.unlink()
        check("data folder can be written", True)
    except OSError as e:
        check("data folder can be written", False, str(e))
    check("data folder is next to the program (a build keeps its settings and assets in its own folder)",
          (not paths.FROZEN) or paths.data_dir() == paths.app_dir() / "data", fail="using the fallback folder, see above")

    missing = [f for f in UI_FILES if not (paths.ui_dir() / f).is_file()]
    check(f"interface files present ({len(UI_FILES)} checked)", not missing, fail=", ".join(missing))
    art = paths.ui_dir() / "assets"
    check("interface assets folder present", art.is_dir(), str(art))
    from pt import appicon
    icon = appicon.icon_path()
    info(f"icon of the window: {icon}" if icon else f"icon of the window: {appicon.ICON_FILE} not found in {paths.bundle_dir()}; the default icons are used")

    for mod in ("banner_tracker_test", "webview", "sqlite3", "pt.window", "pt.loader", "pt.tasks", "pt.banners", "pt.winsize", "pt.winpatch", "pt.appicon",
                "pt.api", "pt.tracker", "pt.store", "pt.engine", "pt.games", "pt.pulls", "pt.settings", "pt.locate", "pt.client", "pt.sync", "pt.vault", "pt.single", "pt.preflight"):
        try:
            importlib.import_module(mod)
            check(f"import {mod}", True)
        except Exception as e:      # noqa: BLE001 - the report must say what failed, whatever it is
            check(f"import {mod}", False, f"{type(e).__name__}: {e}")

    try:        # the keyring is optional: it only stores the HoYoLAB cookie (a missing one is reported, not a failure)
        from pt import vault
        info("keyring for the HoYoLAB cookie: " + ("available" if vault.available() else "not available (the cookie option cannot store anything)"))
    except Exception as e:      # noqa: BLE001
        info(f"keyring check failed: {type(e).__name__}: {e}")

    try:        # the pull database: SQLite with the write-ahead log, in the data folder (a throw-away file, not the real database)
        import sqlite3
        probe = paths.data_dir() / "selfcheck.sqlite"
        con = sqlite3.connect(str(probe))
        try:
            mode = con.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            con.execute("CREATE TABLE IF NOT EXISTS t (a INTEGER)")
            con.execute("INSERT INTO t VALUES (9223372036854775807)")
            con.commit()
            big = con.execute("SELECT MAX(a) FROM t").fetchone()[0]
        finally:
            con.close()
        check(f"SQLite {sqlite3.sqlite_version} works in the data folder", mode == "wal" and big == 9223372036854775807, f"journal mode {mode}")
    except Exception as e:      # noqa: BLE001
        check("SQLite works in the data folder", False, f"{type(e).__name__}: {e}")
    finally:        # the throw-away files go in every case (a failed run used to leave them in the data folder)
        for suffix in ("", "-wal", "-shm"):
            with contextlib.suppress(OSError):
                (paths.data_dir() / f"selfcheck.sqlite{suffix}").unlink(missing_ok=True)

    if sys.platform == "win32":
        for mod in ("clr", "webview.platforms.winforms", "webview.platforms.edgechromium"):
            try:
                importlib.import_module(mod)
                check(f"import {mod}", True)
            except Exception as e:      # noqa: BLE001
                check(f"import {mod}", False, f"{type(e).__name__}: {e}")
        try:
            import webview
            lib = Path(webview.__file__).resolve().parent / "lib"
            need = ("WebView2Loader.dll", "Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll")
            absent = [n for n in need if not any(lib.rglob(n))]
            check("pywebview WebView2 files present", not absent, ", ".join(absent) or str(lib))
        except Exception as e:      # noqa: BLE001
            check("pywebview WebView2 files present", False, f"{type(e).__name__}: {e}")
        version = _webview2_version()
        check("WebView2 runtime installed", version is not None,
              version or "install the Evergreen runtime from https://developer.microsoft.com/microsoft-edge/webview2/")
    else:
        info("WebView2 checks skipped (not Windows)")
    return ok, lines


def message_text(report: str, saved: bool, target: Path) -> str:
    """The text of the message box of a build without a console: the report (cut when long) and where it was saved, or that it was not."""
    shown = report if len(report) < 1800 else report[:1800] + "\n..."
    return shown + (f"\nSaved in {target}" if saved else f"\nThe report could not be saved in {target.parent}.")


def main() -> int:
    ok, lines = run()
    report = "\n".join(lines) + f"\n{'ALL OK' if ok else 'PROBLEMS FOUND'}\n"
    saved = True
    try:
        (paths.data_dir() / "selfcheck.txt").write_text(report, encoding="utf-8")
    except OSError:
        saved = False
    print(report)
    if sys.platform == "win32" and paths.FROZEN:      # no console in the build: show the result
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message_text(report, saved, paths.data_dir() / "selfcheck.txt"), "Pulls Tracker self check", 0x10 if not ok else 0x40)
        except Exception:      # noqa: BLE001
            pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
