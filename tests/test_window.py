"""Tests of the window code that need no screen: python -m unittest discover -s tests"""
import ctypes
import json
import os
import sys
import tempfile
import time
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.pop("PULLS_TRACKER_DATA", None)      # a test must never touch a real data folder: each test that needs one sets its own
from pt import appicon, loader, paths, window  # noqa: E402


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def wait(self, timeout=None):
        return True


class FakeWindow:
    """Stands for the pywebview window. It records what is done to it; loading the main page reports 'ready' at once, like the real page."""

    def __init__(self, kw, x=560, y=425):
        self.kw, self.x, self.y = kw, x, y
        self.calls, self.js = [], []
        self.on_top = kw.get("on_top", False)
        self.events = types.SimpleNamespace(loaded=FakeEvent(), closed=FakeEvent())
        self.fail_load = False

    def minimize(self):
        self.calls.append(("minimize",))

    def destroy(self):
        self.calls.append(("destroy",))
        for h in self.events.closed.handlers:
            h()

    def evaluate_js(self, js):
        self.js.append(js)

    def hide(self):
        self.calls.append(("hide",))

    def show(self):
        self.calls.append(("show", self.on_top))

    def resize(self, w, h):
        self.calls.append(("resize", w, h))

    def move(self, x, y):
        self.calls.append(("move", x, y))
        self.x, self.y = x, y

    def load_url(self, url):
        self.calls.append(("load_url", url))
        if self.fail_load:
            raise RuntimeError("x")
        self.kw["js_api"].uiReady()


class FakeWebview:
    def __init__(self, screens=((0, 0, 1920, 1080),)):
        self.windows, self.start_kw = [], {}
        self.screens = [types.SimpleNamespace(x=a, y=b, width=c, height=d) for a, b, c, d in screens]

    def create_window(self, title, **kw):
        w = FakeWindow(dict(title=title, **kw))
        self.windows.append(w)
        return w

    def start(self, func=None, args=(), **kw):
        self.start_kw = kw
        if func:
            func(*args)


class WindowApiTest(unittest.TestCase):
    def test_minimize_and_close(self):
        api, win = window.WindowApi(), FakeWindow({})
        api._attach(win)
        self.assertEqual(api.windowMinimize(), {"ok": True})
        self.assertEqual(win.calls, [("minimize",)])
        self.assertEqual(api.windowClose(), {"ok": True})
        time.sleep(0.3)
        self.assertEqual(win.calls, [("minimize",), ("destroy",)])

    def test_without_window(self):
        api = window.WindowApi()
        self.assertEqual(api.windowMinimize(), {"ok": True})
        self.assertEqual(api.windowClose(), {"ok": True})

    def test_ui_ready(self):
        api = window.WindowApi()
        self.assertFalse(api._wait_ready(0))
        api.uiReady()
        self.assertTrue(api._wait_ready(0))
        api._reset_ready()
        self.assertFalse(api._wait_ready(0))

    def test_events_of_a_sync_reach_the_page_through_evaluate_js(self):
        api = window.WindowApi()
        api._emit({"type": "step", "n": 1, "state": "ok", "text": "caf\u00e9 </script>"})        # no window yet: dropped without an error
        w = FakeWindow({})
        api._attach(w)
        api._emit({"type": "step", "n": 1, "state": "ok", "text": "caf\u00e9 </script>"})
        self.assertEqual(len(w.js), 1)
        js = w.js[0]
        self.assertTrue(js.startswith("window.PT_EVENTS && window.PT_EVENTS.emit({"))
        self.assertTrue(js.isascii())
        self.assertEqual(json.loads(js[js.index("emit(") + 5:-1])["text"], "caf\u00e9 </script>")
        w.evaluate_js = mock.Mock(side_effect=RuntimeError("closed"))
        api._emit({"type": "step"})                                                      # a closed window must not raise

    def test_closing_the_window_stops_what_would_keep_the_process_alive(self):
        api, state = window.WindowApi(), {"cancelled": False}
        self.assertFalse(api._wait_ready(0))
        window._on_closed(api, state)
        self.assertTrue(state["cancelled"])
        self.assertTrue(api._sync_cancel.is_set())
        self.assertTrue(api._wait_ready(0))              # a start that waits for the first screen goes on at once

    def test_page_sees_only_these_methods(self):
        public = [n for n in dir(window.WindowApi()) if not n.startswith("_")]
        data = ["clearImageCache", "chooseFolder", "dashboard", "exportRun", "getSettings", "history", "historyExport", "importApply", "importPreview",
                "pastBanners", "pickImportFile", "removeAccount", "saveCookie", "saveNickname", "saveSettings", "sourceTest", "state", "stats", "storage",
                "syncCancel", "syncStart", "firstRun", "setActiveAccount", "backupNow", "windowPulls", "windowStats"]
        win = ["perfReport", "uiReady", "windowClose", "windowGetOnTop", "windowGetRect", "windowMinimize", "windowSetOnTop", "windowSetRect", "windowStartResize"]
        self.assertEqual(sorted(public), sorted(data + win))


def run_app(fake, before_start=None):
    # the real tasks and labels, but only "open" does anything (the others would fetch from the network)
    quick = lambda: [replace(t, min_seconds=0, run=t.run if t.id == "open" else (lambda ctx: None)) for t in loader.default_tasks()]       # noqa: E731
    with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(sys.modules, {"webview": fake}), \
            mock.patch.object(paths, "app_dir", return_value=Path(tmp)), \
            mock.patch.object(window, "default_tasks", quick), mock.patch.object(window, "LOADING_HOLD", 0):
        if before_start:
            orig = fake.start
            fake.start = lambda func=None, args=(), **kw: (before_start(fake), orig(func, args, **kw))
        window.run()
        assert (Path(tmp) / "data" / "webview").is_dir()
        assert fake.start_kw["storage_path"] == str(Path(tmp) / "data" / "webview")
    return fake


class RunTest(unittest.TestCase):
    def test_one_window_starts_as_the_loading_panel_803_by_230(self):
        fake = run_app(FakeWebview())
        self.assertEqual(len(fake.windows), 1)
        kw = fake.windows[0].kw
        self.assertEqual((kw["width"], kw["height"]), (803, 230))
        self.assertIs(kw["frameless"], True)
        self.assertIs(kw["on_top"], False)      # never fixed on top unless the setting is on
        self.assertIs(kw["easy_drag"], False)
        self.assertIsInstance(kw["js_api"], window.WindowApi)
        self.assertTrue(kw["url"].startswith("file:///") and "/loading.html#data=file%3A%2F%2F%2F" in kw["url"] and kw["url"].endswith("%2Fdata%2F"), kw["url"])
        self.assertIs(fake.start_kw["private_mode"], False)

    def test_progress_reaches_the_panel(self):
        win = run_app(FakeWebview()).windows[0]
        payloads = [json.loads(js[js.index("(", js.index("PT_LOAD.set")) + 1:-1]) for js in win.js]
        self.assertEqual(payloads[0], {"done": 0, "total": 8, "label": "Checking the active banners", "fraction": 0.0})
        self.assertEqual(payloads[-3], {"done": 7, "total": 8, "label": "Opening the application", "fraction": 0.0})
        self.assertEqual(payloads[-2], {"done": 1, "total": 1, "label": "Opening the application", "fraction": 0.0})      # 100% before it opens
        self.assertEqual(payloads[-1], {"done": 8, "total": 8, "label": "Opening the application", "fraction": 0.0})      # after the page changed: no panel to draw it

    def test_the_panel_becomes_the_1191_by_930_main_screen(self):
        win = run_app(FakeWebview()).windows[0]
        calls = [c for c in win.calls if c[0] != "destroy"]
        self.assertEqual(calls[0:3], [("hide",), ("resize", 1191, 930), ("move", 366, 75)])      # same centre as the panel (560 + 401, 425 + 115)
        self.assertEqual(calls[3][0], "load_url")
        self.assertTrue(calls[3][1].startswith("file:///") and calls[3][1].endswith("/index.html#backend=1"), calls[3][1])
        self.assertNotIn("?", calls[3][1])      # a "?" in a file address gives "File not found" in the Windows WebView2 host
        self.assertEqual(calls[4], ("show", False))      # not on top when shown (the setting is off)
        self.assertEqual(len(calls), 5)
        self.assertNotIn(("destroy",), win.calls)

    def test_small_screen_makes_the_main_screen_smaller_and_keeps_it_inside(self):
        fake = FakeWebview(screens=((0, 0, 1366, 768),))
        win = run_app(fake, lambda f: f.windows[0].__dict__.update(x=280, y=269)).windows[0]       # panel centred on the screen
        resize = [c for c in win.calls if c[0] == "resize"][0]
        move = [c for c in win.calls if c[0] == "move"][0]
        self.assertEqual(resize[1:], (1191, 728))
        self.assertTrue(0 <= move[1] and move[1] + 1191 <= 1366 and 0 <= move[2] and move[2] + 728 <= 768, move)

    def test_second_screen_is_used_when_the_panel_is_on_it(self):
        fake = FakeWebview(screens=((0, 0, 1920, 1080), (1920, 0, 1920, 1080)))
        win = run_app(fake, lambda f: f.windows[0].__dict__.update(x=2400, y=400)).windows[0]
        move = [c for c in win.calls if c[0] == "move"][0]
        self.assertTrue(1920 <= move[1] and move[1] + 1191 <= 3840, move)

    def test_closing_the_panel_early_stops_the_start(self):
        win = run_app(FakeWebview(), lambda f: f.windows[0].destroy()).windows[0]       # the user closes it before the tasks run
        self.assertNotIn("load_url", [c[0] for c in win.calls])

    def test_closing_the_window_while_the_main_page_loads_ends_the_wait_and_shows_nothing(self):
        def close_on_load(fake):
            win = fake.windows[0]
            win.load_url = lambda url: (win.calls.append(("load_url", url)), win.destroy())      # closed instead of "ready"
        started = time.monotonic()
        with mock.patch.object(window, "UI_READY_TIMEOUT", 5.0):
            win = run_app(FakeWebview(), close_on_load).windows[0]
        self.assertLess(time.monotonic() - started, 3.0)             # the 5 s wait did not run out
        self.assertNotIn("show", [c[0] for c in win.calls])
        self.assertEqual([c for c in win.calls if c[0] == "destroy"], [("destroy",)])      # only the user's close, not a second one from the start

    def test_closing_the_window_while_the_panel_is_at_100_percent_stops_before_the_change(self):
        real = window._set_progress

        def close_at_100(win, done, total, label, fraction):
            real(win, done, total, label, fraction)
            if (done, total) == (1, 1):
                win.destroy()
        with mock.patch.object(window, "_set_progress", close_at_100):
            win = run_app(FakeWebview()).windows[0]
        self.assertEqual([c[0] for c in win.calls], ["destroy"])         # no hide, resize, move, load_url or show on a closed window

    def test_when_the_main_screen_cannot_open_the_application_leaves(self):
        with mock.patch.object(window.preflight, "fatal") as fatal:      # the real one is a message box on Windows: it would wait for a click
            win = run_app(FakeWebview(), lambda f: setattr(f.windows[0], "fail_load", True)).windows[0]
        self.assertEqual(win.calls[-1], ("destroy",))
        fatal.assert_called_once()                                        # and the person is told, not just left with a window that vanishes
        self.assertIsInstance(fatal.call_args.args[0], Exception)
        self.assertEqual(Path(fatal.call_args.args[1]).name, "pulls_tracker.log")

    def test_closing_the_panel_early_shows_no_start_up_message(self):
        with mock.patch.object(window.preflight, "fatal") as fatal:
            run_app(FakeWebview(), lambda f: f.windows[0].destroy())
        fatal.assert_not_called()

    def test_interface_files_exist(self):
        for name in ("index.html", "loading.html"):
            self.assertTrue((paths.ui_dir() / name).is_file(), name)


class StartedAsAFileTest(unittest.TestCase):
    def test_window_py_can_be_run_directly(self):
        """The Run button of an editor starts pt/window.py as a file: it must find its package (this once raised ImportError)."""
        import subprocess
        with tempfile.TemporaryDirectory() as cwd:
            r = subprocess.run([sys.executable, str(Path(window.__file__).resolve()), "--help"], cwd=cwd, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Pulls Tracker", r.stdout)

    def test_pulls_tracker_py_can_be_run_from_another_folder(self):
        import subprocess
        with tempfile.TemporaryDirectory() as cwd:
            r = subprocess.run([sys.executable, str(Path(window.__file__).resolve().parent.parent / "pulls_tracker.py"), "--help"], cwd=cwd,
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)


class PathsTest(unittest.TestCase):
    def test_from_source(self):
        self.assertEqual(paths.app_dir(), paths.bundle_dir())
        self.assertEqual(paths.data_dir(), paths.app_dir() / "data")

    def test_pyinstaller_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe_dir, bundle = Path(tmp) / "PullsTracker", Path(tmp) / "PullsTracker" / "_internal"
            with mock.patch.object(paths, "FROZEN", True), mock.patch.object(sys, "executable", str(exe_dir / "PullsTracker.exe")), \
                    mock.patch.object(sys, "_MEIPASS", str(bundle), create=True):
                self.assertEqual(paths.app_dir(), exe_dir.resolve())
                self.assertEqual(paths.ui_dir(), bundle / "pulls_tracker_ui")
                self.assertEqual(paths.data_dir(), exe_dir.resolve() / "data")

    def _frozen(self, tmp):
        exe_dir = Path(tmp) / "PullsTracker"
        exe_dir.mkdir()
        stack = [mock.patch.object(paths, "FROZEN", True), mock.patch.object(sys, "executable", str(exe_dir / "PullsTracker.exe")),
                 mock.patch.object(sys, "_MEIPASS", str(exe_dir / "_internal"), create=True), mock.patch.dict(os.environ, {}, clear=False)]
        for m in stack:
            m.start()
            self.addCleanup(m.stop)
        os.environ.pop("PULLS_TRACKER_DATA", None)
        paths._writable.cache_clear()
        self.addCleanup(paths._writable.cache_clear)
        return exe_dir.resolve()

    def test_frozen_data_folder_is_created_next_to_the_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe_dir = self._frozen(tmp)
            paths.ensure_dirs()
            self.assertEqual(paths.data_dir(), exe_dir / "data")
            for sub in ("webview", "assets/loading"):
                self.assertTrue((exe_dir / "data" / sub).is_dir(), sub)
            self.assertEqual(paths.data_dir_note(), str(exe_dir / "data"))

    def test_frozen_unwritable_program_folder_uses_local_app_data_and_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe_dir = self._frozen(tmp)
            local = Path(tmp) / "Local"
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local)}), mock.patch.object(paths, "_writable", lambda folder: False):
                self.assertEqual(paths.data_dir(), local / "PullsTracker" / "data")
                self.assertIn("cannot be written", paths.data_dir_note())
                self.assertIn(str(exe_dir), paths.data_dir_note())

    def test_data_folder_can_be_forced(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._frozen(tmp)
            with mock.patch.dict(os.environ, {"PULLS_TRACKER_DATA": str(Path(tmp) / "mine")}):
                self.assertEqual(paths.data_dir(), (Path(tmp) / "mine").resolve())
                self.assertIn("PULLS_TRACKER_DATA", paths.data_dir_note())


class SelfCheckTest(unittest.TestCase):
    def test_passes_from_source_and_names_what_it_checks(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"PULLS_TRACKER_DATA": tmp}):
            from pt import selfcheck
            ok, lines = selfcheck.run()
            text = "\n".join(lines)
            self.assertTrue(ok, text)
            for needle in ("data folder can be written", "interface files present (15 checked)", "import banner_tracker_test", "import webview", "import pt.window"):
                self.assertIn(needle, text)

    def test_a_missing_interface_file_fails_and_is_named(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"PULLS_TRACKER_DATA": tmp}), \
                mock.patch.object(paths, "ui_dir", return_value=Path(tmp) / "no_ui"):
            from pt import selfcheck
            ok, lines = selfcheck.run()
            self.assertFalse(ok)
            self.assertTrue(any(l.startswith("FAIL interface files present") and "index.html" in l for l in lines), lines)

    def test_main_writes_the_report_file(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"PULLS_TRACKER_DATA": tmp}):
            from pt import selfcheck
            with mock.patch("builtins.print"):
                self.assertEqual(selfcheck.main(), 0)
            self.assertIn("ALL OK", (Path(tmp) / "selfcheck.txt").read_text(encoding="utf-8"))


class FakeForm:
    """Stands for the WinForms window: Invoke runs the function at once (on the test thread), like the real one runs it on the window thread."""

    def __init__(self):
        self.Icon, self.invoked = None, 0

    def Invoke(self, func):
        self.invoked += 1
        return func()


def fake_dotnet(icon_factory=lambda path: ("icon", path)):
    """The two .NET modules set_window_icon imports (pythonnet is Windows only)."""
    class Func:
        def __class_getitem__(cls, item):
            return lambda fn: fn

    system, drawing = types.ModuleType("System"), types.ModuleType("System.Drawing")
    system.Func, system.Type, drawing.Icon = Func, object, icon_factory
    return {"System": system, "System.Drawing": drawing}


class AppIconTest(unittest.TestCase):
    def with_bundle(self, tmp, icon=True):
        folder = Path(tmp)
        if icon:
            (folder / appicon.ICON_FILE).write_bytes(b"not a real icon, the code only needs the path")
        return mock.patch.object(paths, "bundle_dir", return_value=folder)

    def test_the_icon_file_is_looked_up_in_the_bundle_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.with_bundle(tmp, icon=False):
                self.assertIsNone(appicon.icon_path())
            with self.with_bundle(tmp):
                self.assertEqual(appicon.icon_path(), Path(tmp) / "pulls_tracker.ico")

    def test_the_taskbar_identity_is_for_a_windows_program_run_from_source_only(self):
        shell32 = mock.MagicMock()
        set_id = shell32.SetCurrentProcessExplicitAppUserModelID
        set_id.return_value = 0
        with mock.patch.object(ctypes, "WinDLL", return_value=shell32, create=True) as win_dll:
            with mock.patch.object(sys, "platform", "linux"):
                self.assertFalse(appicon.set_app_id())
            with mock.patch.object(sys, "platform", "win32"), mock.patch.object(paths, "FROZEN", True):
                self.assertFalse(appicon.set_app_id())                                # the .exe is its own identity
            set_id.assert_not_called()
            with mock.patch.object(sys, "platform", "win32"), mock.patch.object(paths, "FROZEN", False):
                self.assertTrue(appicon.set_app_id())
            set_id.assert_called_once_with(appicon.APP_ID)
            win_dll.assert_called_once_with("shell32")                         # a library of its own, not the shared ctypes.windll
            set_id.return_value = 0x80070057                                           # an HRESULT that is not S_OK
            with mock.patch.object(sys, "platform", "win32"), mock.patch.object(paths, "FROZEN", False):
                self.assertFalse(appicon.set_app_id())

    def test_the_window_gets_the_icon(self):
        form = FakeForm()
        with tempfile.TemporaryDirectory() as tmp, self.with_bundle(tmp), mock.patch.object(window, "_form", return_value=form), \
                mock.patch.dict(sys.modules, fake_dotnet()):
            self.assertTrue(window.set_window_icon(object()))
        self.assertEqual(form.Icon, ("icon", str(Path(tmp) / "pulls_tracker.ico")))
        self.assertEqual(form.invoked, 1)

    def test_without_an_icon_file_or_a_winforms_window_nothing_changes(self):
        form = FakeForm()
        with tempfile.TemporaryDirectory() as tmp:
            with self.with_bundle(tmp, icon=False), mock.patch.object(window, "_form", return_value=form), mock.patch.dict(sys.modules, fake_dotnet()):
                self.assertFalse(window.set_window_icon(object()))
            with self.with_bundle(tmp), mock.patch.object(window, "_form", return_value=None):
                self.assertFalse(window.set_window_icon(object()))                    # other systems have no WinForms window
        self.assertEqual((form.Icon, form.invoked), (None, 0))

    def test_an_icon_file_that_cannot_be_read_is_logged_and_the_program_goes_on(self):
        def broken(path):
            raise ValueError("Argument 'picture' must be a picture that can be used as a Icon.")
        form = FakeForm()
        with tempfile.TemporaryDirectory() as tmp, self.with_bundle(tmp), mock.patch.object(window, "_form", return_value=form), \
                mock.patch.dict(sys.modules, fake_dotnet(broken)), self.assertLogs("pt.window", "WARNING"):
            self.assertFalse(window.set_window_icon(object()))
        self.assertIsNone(form.Icon)

    def test_start_up_sets_the_taskbar_identity_before_the_window_and_the_icon_after_it_exists(self):
        fake, seen = FakeWebview(), {}
        with mock.patch.object(window.appicon, "set_app_id", side_effect=lambda: seen.setdefault("windows_at_app_id", len(fake.windows))), \
                mock.patch.object(window, "set_window_icon") as set_icon:
            run_app(fake)
        self.assertEqual(seen, {"windows_at_app_id": 0})
        set_icon.assert_called_once_with(fake.windows[0])


class BuildFilesTest(unittest.TestCase):
    root = Path(__file__).resolve().parent.parent

    def test_spec_ships_the_interface_and_the_modules_loaded_by_name(self):
        spec = (self.root / "pulls_tracker.spec").read_text(encoding="utf-8")
        for needle in ('"pulls_tracker_ui"', '"banner_tracker_test"', "webview.platforms.winforms", "webview.platforms.edgechromium", "pythonnet",
                       "exclude_binaries=True", 'name="AquariaWish"', 'name="AquariaWish - Pulls Tracker"', "COLLECT("):
            self.assertIn(needle, spec)
        compile(spec, "pulls_tracker.spec", "exec")       # valid Python

    def test_spec_embeds_the_icon_in_the_exe_and_ships_it_for_the_window(self):
        spec = (self.root / "pulls_tracker.spec").read_text(encoding="utf-8")
        self.assertIn(f'HERE / "{appicon.ICON_FILE}"', spec)                          # the file name the program looks for (pt/appicon.py)
        self.assertIn("icon=str(ICON) if ICON.is_file() else None", spec)             # the icon of the .exe in Explorer
        self.assertIn('datas.append((str(ICON), "."))', spec)                          # the same file in the bundle folder: bundle_dir() / ICON_FILE
        self.assertIn('"pt.appicon"', spec)

    def test_build_script_runs_the_spec_and_the_selfcheck(self):
        bat = (self.root / "build_exe.bat").read_text(encoding="utf-8")
        self.assertIn("PyInstaller", bat)
        self.assertIn("pulls_tracker.spec", bat)
        self.assertIn("--selfcheck", bat)

    def test_the_fetcher_is_a_top_level_module_next_to_the_entry_point(self):
        self.assertTrue((self.root / "banner_tracker_test.py").is_file())
        self.assertTrue((self.root / "pulls_tracker.py").is_file())


if __name__ == "__main__":
    unittest.main()
