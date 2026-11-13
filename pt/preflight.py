"""Checks made at the start of the program built for Windows, before the window opens, and the messages they show.

The window needs the Microsoft Edge WebView2 runtime. Without it pywebview falls back to an old engine and the window stays blank, and the
program has no console to say why, so the missing runtime is reported here in a message box with the download address.
The message boxes are Windows only; each function takes its message box and its browser opener as parameters so the logic can be tested.
"""
from __future__ import annotations

import logging
import sys
from typing import Callable, Optional

log = logging.getLogger("pt.preflight")

WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"
TITLE = "Pulls Tracker"
MB_YESNO, MB_ICONERROR, MB_ICONWARNING, MB_ICONINFORMATION, IDYES = 0x4, 0x10, 0x30, 0x40, 6


def message_box(text: str, title: str = TITLE, flags: int = MB_ICONINFORMATION) -> int:
    import ctypes
    return ctypes.windll.user32.MessageBoxW(None, text, title, flags)


def webview2_ok(*, platform: Optional[str] = None, version: Optional[Callable[[], Optional[str]]] = None,
                ask: Callable[..., int] = message_box, open_url: Optional[Callable[[str], object]] = None) -> bool:
    """True when the program should go on. Without the runtime a Yes/No box offers the download page (Yes opens it and ends the program;
    No starts anyway, in case the runtime is installed in a place this check does not look at)."""
    if (platform or sys.platform) != "win32":
        return True
    try:
        if version is None:
            from pt.selfcheck import _webview2_version as version
        found = version()
        if found:
            log.info("WebView2 runtime %s", found)
            return True
        log.warning("The WebView2 runtime was not found in the registry")
        answer = ask("Pulls Tracker needs the Microsoft Edge WebView2 runtime, and it was not found on this computer.\n\n"
                     f"Download page:\n{WEBVIEW2_URL}\n\nOpen the download page now? (Yes opens the page and closes Pulls Tracker. "
                     "No tries to start anyway.)", TITLE, MB_YESNO | MB_ICONWARNING)
        if answer == IDYES:
            if open_url is None:
                import webbrowser
                open_url = webbrowser.open
            open_url(WEBVIEW2_URL)
            return False
        return True
    except Exception:      # noqa: BLE001 - a failed check must not keep the program from starting
        log.warning("The WebView2 check failed; starting anyway", exc_info=True)
        return True


def _tell(text: str, flags: int, ask: Callable[..., int], platform: Optional[str]) -> None:
    """Show a message to the person: a message box on Windows, a line on stderr elsewhere (and when the box cannot be shown)."""
    if (platform or sys.platform) == "win32":
        try:
            ask(text, TITLE, flags)
            return
        except Exception:      # noqa: BLE001
            log.debug("The message box failed", exc_info=True)
    print(text, file=sys.stderr)


def already_running(*, ask: Callable[..., int] = message_box, platform: Optional[str] = None) -> None:
    """Tell the person that another copy is open."""
    _tell("Pulls Tracker is already running (look for its window on the taskbar). Only one copy can use the same data folder.",
          MB_ICONINFORMATION, ask, platform)


def database_ok(found: Optional[int], supported: int, folder, *, ask: Callable[..., int] = message_box, platform: Optional[str] = None) -> bool:
    """False when the pull database in `folder` has a newer format than this version reads (`found` is the number it records, None when
    there is no database yet). The program does not open it, so nothing in it changes."""
    if found is None or found <= supported:
        return True
    log.error("The pull database has format %s; this version reads up to format %s", found, supported)
    _tell("The pull database in this data folder was written by a newer version of Pulls Tracker "
          f"(database format {found}; this version reads up to format {supported}).\n\nUse the newer version of the program. "
          f"Nothing was changed.\n\nData folder:\n{folder}", MB_ICONERROR, ask, platform)
    return False


def fatal(exc: BaseException, log_file=None, *, ask: Callable[..., int] = message_box, platform: Optional[str] = None) -> None:
    """Tell the person that the program could not start (the windowed program has no console, so without this it just disappears)."""
    text = f"Pulls Tracker could not start.\n\n{type(exc).__name__}: {exc}"
    if log_file:
        text += f"\n\nMore detail is in:\n{log_file}"
    _tell(text, MB_ICONERROR, ask, platform)
