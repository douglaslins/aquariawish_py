"""The application window.

One frameless pywebview window is used for the whole run:
1. It starts as the loading panel: 803 x 230 pixels (a 230 x 230 area with a donut and a 573 pixel wide banner area),
   showing pulls_tracker_ui/loading.html while the load tasks of pt/loader.py run.
2. The last load task ("open") hides it, sets it to 1191 x 930 around the same centre, loads index.html, and shows it again when the page
   reports that its first screen is drawn (WindowApi.uiReady).
Neither window is "always on top". Each is brought to the front once when it appears. Keeping the window above others is a setting
(Settings > Display), off by default, saved in data/window.json.

The main page draws its own minimize and close buttons, and the whole top bar (except its buttons) moves the window: a pywebview drag
region under the bar (see js/window.js and css/app.css in the interface). It talks to this side through WindowApi
(window.pywebview.api.<method>), the same names as window.PT_API in js/api.js.
The window and its taskbar button get the icon pulls_tracker.ico when that file exists (pt/appicon.py).
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path

if __package__ in (None, ""):        # started as a file (python pt/window.py, or the Run button of an editor): find the pt package
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pt import appicon, paths, preflight, sync, winpatch, winsize
from pt.api import PullsApi
from pt.loader import Context, Loader, default_tasks

log = logging.getLogger("pt.window")

APP_TITLE = "Pulls Tracker"
INITIAL_SIZE = (1191, 930)      # main screen, width x height at start (made smaller only when the screen is smaller)
LOADING_SIZE = (230 + 573, 230)     # loading panel: the 230 x 230 area plus the 573 pixel banner area
SCREEN_MARGIN = 40              # pixels kept free around the window on a small screen
BACKGROUND = "#0e1016"          # --bg of the interface, shown before a page has drawn (avoids a white flash)
UI_READY_TIMEOUT = 20.0         # seconds to wait for the main page before the window is shown anyway
LOADING_HOLD = 0.3              # seconds the panel stays at 100% before it changes to the main screen
PREFS_FILE = "window.json"      # in the data folder: {"onTop": false}


def prefs_path() -> Path:
    return paths.data_dir() / PREFS_FILE


def load_prefs() -> dict:
    prefs = {"onTop": False}
    try:
        data = json.loads(prefs_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("onTop"), bool):
            prefs["onTop"] = data["onTop"]
    except FileNotFoundError:
        pass
    except Exception:
        log.warning("Could not read %s; using the defaults", prefs_path(), exc_info=True)
    return prefs


def save_prefs(prefs: dict) -> None:
    try:
        prefs_path().parent.mkdir(parents=True, exist_ok=True)
        prefs_path().write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    except OSError:
        log.warning("Could not write %s", prefs_path(), exc_info=True)


def _form(window):
    """The WinForms window (Windows only), or None."""
    if sys.platform != "win32":
        return None
    try:
        from webview.platforms.winforms import BrowserView
        return BrowserView.instances.get(window.uid)
    except Exception:
        log.debug("No WinForms window", exc_info=True)
        return None


def set_on_top(window, flag: bool) -> bool:
    """Keep the window above the others (or not). On Windows the property is set on the thread that owns the window."""
    flag = bool(flag)
    form = _form(window)
    if form is not None:
        try:
            from System import Func, Type
            form.Invoke(Func[Type](lambda: setattr(form, "TopMost", flag)))
            return True
        except Exception:
            log.warning("Setting TopMost on the window thread failed; using pywebview", exc_info=True)
    try:
        window.on_top = flag
        return True
    except Exception:
        log.exception("Could not change the on-top state")
        return False


def raise_once(window, keep: bool = False) -> bool:
    """Bring the window in front of the others once, without leaving it on top (unless `keep`). False when this system has no such call."""
    form = _form(window)
    if form is None:
        return False
    try:
        from System import Func, Type

        def go():
            form.TopMost = True
            form.TopMost = bool(keep)
        form.Invoke(Func[Type](go))
        return True
    except Exception:
        log.debug("Could not bring the window to the front", exc_info=True)
        return False


def set_window_icon(window) -> bool:
    """Give the window and its taskbar button the icon of pulls_tracker.ico (pt/appicon.py), on the thread that owns the window.
    False when there is no icon file or no WinForms window (other systems); the window keeps the icon it has."""
    icon = appicon.icon_path()
    form = _form(window) if icon is not None else None
    if icon is None or form is None:
        return False
    try:
        from System import Func, Type
        from System.Drawing import Icon

        def go():
            form.Icon = Icon(str(icon))
        form.Invoke(Func[Type](go))
        return True
    except Exception:
        log.warning("Could not set the window icon from %s", icon, exc_info=True)
        return False


class WindowApi(PullsApi):
    """Methods of the main window the page can call, besides the data methods of PullsApi. Names starting with an underscore are not
    visible to the page."""

    def __init__(self, tracker=None) -> None:
        super().__init__(tracker)
        self._ready = threading.Event()
        self._prefs = load_prefs()

    def _attach(self, window) -> None:
        self._window = window

    def _emit(self, event: dict) -> None:
        """Events of a running sync reach the page through evaluate_js (the page has window.PT_EVENTS, js/api.js)."""
        w = self._window
        if w is None:
            return
        try:
            w.evaluate_js("window.PT_EVENTS && window.PT_EVENTS.emit(" + json.dumps(event, ensure_ascii=True) + ")")
        except Exception:      # the window was closed or the page is changing
            log.debug("An event could not be sent to the page", exc_info=True)

    def _reset_ready(self) -> None:
        self._ready.clear()

    def _wait_ready(self, timeout: float) -> bool:
        return self._ready.wait(timeout)

    def uiReady(self) -> dict:
        """The page calls this when its first screen is drawn."""
        self._ready.set()
        return {"ok": True}

    def windowMinimize(self) -> dict:
        if self._window is not None:
            self._window.minimize()
        return {"ok": True}

    def windowClose(self) -> dict:
        # A running sync stops first (a pull batch is written in one transaction, so nothing is left half written either way).
        self._stop_sync(3.0)
        # Destroy after this call has returned its answer to the page.
        if self._window is not None:
            threading.Timer(0.05, self._window.destroy).start()
        return {"ok": True}

    def windowGetOnTop(self) -> dict:
        return {"onTop": self._prefs["onTop"]}

    def windowSetOnTop(self, flag) -> dict:
        """Settings > Display > "Keep the window above other windows". Takes effect at once and is kept for the next start."""
        flag = bool(flag)
        if self._window is None or not set_on_top(self._window, flag):
            return {"ok": False, "onTop": self._prefs["onTop"]}
        self._prefs["onTop"] = flag
        save_prefs(self._prefs)
        return {"ok": True, "onTop": flag}

    def windowStartResize(self, edge) -> dict:
        """The page calls this when the button goes down on a resize handle. native true: this side follows the mouse until the
        button is released. native false: the page does the resize itself with windowGetRect / windowSetRect."""
        form = _form(self._window) if self._window is not None else None
        hwnd = None
        if form is not None:
            try:
                hwnd = int(form.Handle.ToInt64())
            except Exception:
                log.debug("No window handle", exc_info=True)
        return {"ok": True, "native": bool(hwnd) and winsize.start(hwnd, str(edge))}

    def windowGetRect(self) -> dict:
        w = self._window
        return {"x": w.x, "y": w.y, "width": w.width, "height": w.height, "minWidth": winsize.MIN_SIZE[0], "minHeight": winsize.MIN_SIZE[1]}

    def windowSetRect(self, x, y, width, height) -> dict:
        w = self._window
        w.resize(max(int(width), winsize.MIN_SIZE[0]), max(int(height), winsize.MIN_SIZE[1]))
        w.move(int(x), int(y))
        return {"ok": True}

    def perfReport(self, report) -> dict:
        """Measurements of the page (the --perf option), written to data/pulls_tracker.log."""
        log.info("perf %s", json.dumps(report, separators=(",", ":"))[:4000])
        return {"ok": True}


def _ui_url(name: str, query: str = "") -> str:
    f = paths.ui_dir() / name
    if not f.is_file():
        raise FileNotFoundError(f"The interface file was not found: {f}")
    return f.as_uri() + query


def page_url(perf: bool = False) -> str:
    # backend=1: use the Python side instead of the sample data (js/api.js).
    # perf=1 (the --perf option): the page measures its own responsiveness and reports it (js/perf.js).
    # data=<data folder>: read banners and art from there once the load tasks have stored some (js/banners-base.js).
    # These go in the fragment (#), not in a query string (?): the Windows WebView2 host takes the "?" of a file address as part of the
    # file name and shows "File not found" (js/params.js reads the fragment).
    from pt import banners
    return _ui_url("index.html", "#backend=1" + ("&perf=1" if perf else "") + banners.data_param())


def loading_url() -> str:
    # data=<data folder>: the loading page looks for the user's banner in <data>/assets/loading before the one shipped with the interface.
    from urllib.parse import quote
    return _ui_url("loading.html", "#data=" + quote(paths.data_dir().as_uri() + "/", safe=""))


def _set_progress(window, done: int, total: int, label: str, fraction: float) -> None:
    payload = json.dumps({"done": done, "total": total, "label": label, "fraction": fraction})
    try:
        window.evaluate_js(f"window.PT_LOAD && PT_LOAD.set({payload})")
    except Exception:       # the window was closed
        log.debug("Window gone", exc_info=True)


def _main_geometry(webview, window) -> tuple[int, int, int, int]:
    """x, y, width, height of the main screen: INITIAL_SIZE around the centre of the panel, kept inside the screen."""
    cx, cy = window.x + LOADING_SIZE[0] // 2, window.y + LOADING_SIZE[1] // 2
    width, height = INITIAL_SIZE
    screens = list(getattr(webview, "screens", None) or [])
    screen = next((s for s in screens if s.x <= cx < s.x + s.width and s.y <= cy < s.y + s.height), screens[0] if screens else None)
    if screen is None:
        return cx - width // 2, cy - height // 2, width, height
    width, height = min(width, screen.width - SCREEN_MARGIN), min(height, screen.height - SCREEN_MARGIN)
    x = min(max(cx - width // 2, screen.x), screen.x + screen.width - width)
    y = min(max(cy - height // 2, screen.y), screen.y + screen.height - height)
    return x, y, width, height


def _on_closed(api: WindowApi, state: dict) -> None:
    """The window was closed (Alt+F4, the close button) while the program was loading or running. Everything that would keep the process
    alive without a window is told to stop: the start-up tasks, a running sync, and the wait for the first screen (pywebview's start
    thread is not a daemon thread, so the process only ends when it does)."""
    state["cancelled"] = True
    api._sync_cancel.set()
    api._ready.set()


def _open_main(webview, window, api: WindowApi, state: dict, perf: bool = False) -> None:
    """Change the panel into the main window and wait until its first screen is drawn."""
    _set_progress(window, 1, 1, "Opening the application", 0.0)       # the donut at 100%
    time.sleep(LOADING_HOLD)
    if state["cancelled"]:
        return
    x, y, width, height = _main_geometry(webview, window)
    api._reset_ready()
    api._warm_up()
    window.hide()
    window.resize(width, height)
    window.move(x, y)
    state["main"] = True
    window.load_url(page_url(perf))
    if not api._wait_ready(UI_READY_TIMEOUT):
        log.warning("The main page did not report ready in %s s", UI_READY_TIMEOUT)
    if state["cancelled"]:      # the window was closed while the page loaded
        return
    window.show()
    if not raise_once(window, keep=api._prefs["onTop"]) and api._prefs["onTop"]:
        set_on_top(window, True)


def _auto_sync(api: WindowApi, ctx: Context) -> None:
    """Load task 7: the short syncs that can run now (a game whose full history is stored and whose saved link is fresh). Only logged."""
    sync.auto_sync(api._tracker, cancelled=ctx.cancelled)


def _server(api: WindowApi) -> str:
    """The banner server address saved in Settings (empty: the default one)."""
    try:
        return str(api._tracker.settings().get("server") or "").strip()
    except Exception:
        log.debug("Could not read the server setting", exc_info=True)
        return ""


def _bootstrap(webview, window, api: WindowApi, state: dict, perf: bool = False) -> None:
    """Runs in a thread once the GUI is running: the load tasks, the last of which opens the main screen."""
    error = None
    try:
        window.events.loaded.wait(15)
        raise_once(window)
        set_window_icon(window)
        ctx = Context(services={"open_main": lambda: _open_main(webview, window, api, state, perf), "auto_sync": lambda c: _auto_sync(api, c),
                                "server": lambda: _server(api)}, cancelled=lambda: state["cancelled"])
        results = Loader(default_tasks(), lambda d, t, lb, f: _set_progress(window, d, t, lb, f), ctx).run()
        ok = results.get("open") == "ok"
    except Exception as e:
        log.exception("Start-up failed")
        ok, error = False, e
    if not ok and not state["cancelled"]:      # no main screen to show: leave
        log.error("The main screen did not open")
        try:        # the windowed program has no console: without a message the window just disappears
            preflight.fatal(error or RuntimeError("The main screen did not open"), paths.data_dir() / "pulls_tracker.log")
        except Exception:
            log.debug("The start-up message could not be shown", exc_info=True)
        try:
            window.destroy()
        except Exception:
            log.debug("Window already closed", exc_info=True)


def run(debug: bool = False, perf: bool = False) -> None:
    try:
        import webview
    except ImportError as e:      # pragma: no cover
        raise RuntimeError("pywebview is not installed. Run: pip install -r requirements.txt") from e      # not SystemExit: the start-up handler shows the message

    paths.ensure_dirs()
    winpatch.apply()
    appicon.set_app_id()        # before the first window: the taskbar then shows the icon of the window, not the one of Python
    api = WindowApi()
    state = {"cancelled": False, "main": False}
    window = webview.create_window(
        APP_TITLE,
        url=loading_url(),
        js_api=api,
        width=LOADING_SIZE[0],
        height=LOADING_SIZE[1],
        frameless=True,
        easy_drag=False,                 # only the drag regions of the pages move the window (the loading panel is one all over)
        shadow=True,
        on_top=False,                    # never fixed on top at the start (Settings > Display can turn it on for the main window)
        background_color=BACKGROUND,
    )
    api._attach(window)

    window.events.closed += lambda: _on_closed(api, state)
    # private_mode=False and storage_path keep the browser storage of the pages in the data folder instead of a temporary folder.
    webview.start(_bootstrap, (webview, window, api, state, perf), debug=debug, private_mode=False, storage_path=str(paths.webview_storage_dir()))


if __name__ == "__main__":      # python pt/window.py is the same as python pulls_tracker.py
    import pulls_tracker
    sys.exit(pulls_tracker.main())
