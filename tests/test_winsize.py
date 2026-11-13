"""Window preferences (keep on top), the edge resize and the WebView2 patch. Windows-only calls are replaced by fakes."""
import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.pop("PULLS_TRACKER_DATA", None)      # a test must never touch a real data folder: each test that needs one sets its own

from pt import paths, window, winpatch, winsize      # noqa: E402


class NewRectTest(unittest.TestCase):
    R = (100, 100, 1300, 1000)      # 1200 x 900

    def test_each_handle_moves_only_its_edges(self):
        n = lambda e, dx, dy: winsize.new_rect(e, self.R, dx, dy, (960, 720))      # noqa: E731
        self.assertEqual(n("e", 50, 999), (100, 100, 1350, 1000))
        self.assertEqual(n("w", -50, 999), (50, 100, 1300, 1000))
        self.assertEqual(n("s", 999, 40), (100, 100, 1300, 1040))
        self.assertEqual(n("n", 999, -40), (100, 60, 1300, 1000))
        self.assertEqual(n("se", 10, 20), (100, 100, 1310, 1020))
        self.assertEqual(n("nw", -10, -20), (90, 80, 1300, 1000))
        self.assertEqual(n("ne", 10, -20), (100, 80, 1310, 1000))
        self.assertEqual(n("sw", -10, 20), (90, 100, 1300, 1020))

    def test_the_minimum_size_stops_the_edge_and_the_opposite_edge_stays(self):
        n = lambda e, dx, dy: winsize.new_rect(e, self.R, dx, dy, (960, 720))      # noqa: E731
        self.assertEqual(n("e", -900, 0), (100, 100, 1060, 1000))       # width stops at 960
        self.assertEqual(n("w", 900, 0), (340, 100, 1300, 1000))
        self.assertEqual(n("s", 0, -900), (100, 100, 1300, 820))        # height stops at 720
        self.assertEqual(n("n", 0, 900), (100, 280, 1300, 1000))
        self.assertEqual(n("nw", 900, 900), (340, 280, 1300, 1000))

    def test_a_window_smaller_than_the_minimum_is_not_forced_to_grow(self):
        small = (0, 0, 800, 600)
        self.assertEqual(winsize.new_rect("e", small, -100, 0, (960, 720)), (0, 0, 800, 600))      # cannot shrink, does not jump to 960
        self.assertEqual(winsize.new_rect("e", small, 100, 0, (960, 720)), (0, 0, 900, 600))       # can grow


class FakeNative:
    """A cursor that moves along a script; the button goes up after the script ends."""

    def __init__(self, path, scale=1.0, rect=(100, 100, 1300, 1000)):
        self.path, self.i, self._scale, self._rect, self.placed = list(path), 0, scale, rect, []

    def cursor(self):
        return self.path[min(self.i, len(self.path) - 1)]

    def rect(self, hwnd):
        return self._rect

    def scale(self, hwnd):
        return self._scale

    def button_down(self):
        self.i += 1
        return self.i <= len(self.path)

    def place(self, hwnd, rect):
        self.placed.append(rect)


class FollowTest(unittest.TestCase):
    def test_follows_the_cursor_and_skips_unchanged_positions(self):
        native = FakeNative([(1300, 1000), (1310, 1000), (1310, 1000), (1330, 1020), (1330, 1020)])
        n = winsize.follow(native, 1, "se", (960, 720), sleep=lambda s: None)
        self.assertEqual(native.placed, [(100, 100, 1310, 1000), (100, 100, 1330, 1020)])
        self.assertEqual(n, 2)

    def test_the_minimum_size_is_scaled_with_the_screen_scale(self):
        native = FakeNative([(1900, 1000), (0, 1000)], scale=1.5, rect=(100, 100, 1900, 1400))       # drag the right edge far to the left
        winsize.follow(native, 1, "e", (960, 720), sleep=lambda s: None)
        self.assertEqual(native.placed[-1], (100, 100, 100 + 1440, 1400))        # 960 * 1.5

    def test_a_held_button_that_never_ends_is_stopped(self):
        class Stuck(FakeNative):
            polls = 0

            def button_down(self):
                self.polls += 1
                if self.polls > 100:                      # without the time limit the loop would never end: fail instead of hanging
                    raise AssertionError("the loop did not stop at max_seconds")
                return True
        t = [0.0]
        def clock():
            t[0] += 1.0
            return t[0]
        native = Stuck([(0, 0)])
        placed = winsize.follow(native, 1, "e", (960, 720), max_seconds=5, sleep=lambda s: None, clock=clock)
        self.assertEqual(placed, 0)                       # the mouse never moved
        self.assertTrue(0 < native.polls <= 6, native.polls)      # it polled, and stopped at the limit (5 clock ticks)

    def test_a_cursor_that_cannot_be_read_ends_the_resize_without_moving_the_window(self):
        class Locked(FakeNative):
            def cursor(self):
                if self.i >= 2:
                    raise OSError("GetCursorPos failed")        # a locked desktop: the real call fails and gives no position
                return super().cursor()
        native = Locked([(1300, 1000), (1310, 1000), (1320, 1000), (1330, 1000)])
        self.assertEqual(winsize.follow(native, 1, "se", (960, 720), sleep=lambda s: None), 1)
        self.assertEqual(native.placed, [(100, 100, 1310, 1000)])


class Win32Test(unittest.TestCase):
    """The ctypes wrapper with user32 replaced: what it does with a failed call and with a system that lacks GetDpiForWindow."""

    def make(self, names=("GetCursorPos", "GetWindowRect", "SetWindowPos", "GetAsyncKeyState", "GetSystemMetrics", "GetDpiForWindow")):
        import ctypes
        user32 = mock.MagicMock(spec=list(names))
        with mock.patch.object(ctypes, "WinDLL", return_value=user32, create=True):
            return winsize.Win32(), user32

    def test_the_shared_user32_of_the_process_is_left_alone(self):
        """pywebview moves the window with ctypes.windll.user32.SetWindowPos(hwnd, None, x, y, None, None, flags). Integer argtypes on that
        shared function make the call raise TypeError, which stopped the window from being dragged after the first resize."""
        import ctypes
        shared = types.SimpleNamespace(**{n: types.SimpleNamespace() for n in ("GetCursorPos", "GetWindowRect", "SetWindowPos", "GetAsyncKeyState", "GetDpiForWindow")})
        own = mock.MagicMock()
        with mock.patch.object(ctypes, "windll", types.SimpleNamespace(user32=shared), create=True), \
                mock.patch.object(ctypes, "WinDLL", return_value=own, create=True) as win_dll:
            winsize.Win32()
        win_dll.assert_called_once_with("user32")
        for name, function in vars(shared).items():
            self.assertFalse(hasattr(function, "argtypes"), f"argtypes were set on the shared {name}")
        self.assertIsInstance(own.SetWindowPos.argtypes, list)              # they went to the library of its own

    def test_a_system_without_GetDpiForWindow_still_resizes_at_scale_one(self):
        native, _ = self.make(names=("GetCursorPos", "GetWindowRect", "SetWindowPos", "GetAsyncKeyState", "GetSystemMetrics"))
        self.assertEqual(native.scale(1), 1.0)

    def test_the_scale_is_the_dpi_over_96(self):
        native, user32 = self.make()
        user32.GetDpiForWindow.return_value = 144
        self.assertEqual(native.scale(1), 1.5)
        user32.GetDpiForWindow.return_value = 0
        self.assertEqual(native.scale(1), 1.0)

    def test_failed_reads_are_errors_not_zeros(self):
        native, user32 = self.make()
        user32.GetCursorPos.return_value = 0
        user32.GetWindowRect.return_value = 0
        with self.assertRaises(OSError):
            native.cursor()
        with self.assertRaises(OSError):
            native.rect(1)
        user32.GetCursorPos.return_value = 1
        self.assertEqual(native.cursor(), (0, 0))            # a read that worked (the fake wrote nothing: the structure starts at zero)


class StartTest(unittest.TestCase):
    def test_a_thread_that_cannot_start_does_not_lock_the_later_resizes(self):
        class NoThread:
            def __init__(self, *a, **kw):
                pass

            def start(self):
                raise RuntimeError("can't start new thread")
        with mock.patch.object(winsize.threading, "Thread", NoThread), self.assertLogs("pt.winsize", level="ERROR"):
            self.assertFalse(winsize.start(7, "e", native=FakeNative([(0, 0)])))
        self.assertTrue(winsize._busy.acquire(blocking=False))        # not left taken
        winsize._busy.release()
        native = FakeNative([(1300, 1000), (1400, 1000)])
        self.assertTrue(winsize.start(7, "e", native=native))
        time.sleep(0.1)

    def test_unknown_edge_or_no_handle(self):
        native = FakeNative([(0, 0)])
        self.assertFalse(winsize.start(1, "x", native=native))
        self.assertFalse(winsize.start(0, "e", native=native))

    def test_runs_in_a_thread_and_a_second_start_does_not_run_twice(self):
        native = FakeNative([(1300, 1000), (1400, 1000)])
        self.assertTrue(winsize.start(7, "e", native=native))
        for _ in range(100):
            if native.placed:
                break
            time.sleep(0.01)
        self.assertEqual(native.placed, [(100, 100, 1400, 1000)])
        time.sleep(0.05)
        self.assertTrue(winsize._busy.acquire(blocking=False))        # the thread released it
        winsize._busy.release()

    @unittest.skipIf(sys.platform == "win32", "checks the other systems")
    def test_other_systems_answer_false_so_the_page_resizes_itself(self):
        self.assertFalse(winsize.start(7, "e"))


class FakeWin:
    def __init__(self):
        self.on_top, self.x, self.y, self.width, self.height, self.calls = False, 10, 20, 1191, 930, []

    def resize(self, w, h):
        self.calls.append(("resize", w, h))

    def move(self, x, y):
        self.calls.append(("move", x, y))


class PrefsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = mock.patch.object(paths, "app_dir", return_value=Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_default_is_off_and_nothing_is_written(self):
        api = window.WindowApi()
        self.assertEqual(api.windowGetOnTop(), {"onTop": False})
        self.assertFalse((Path(self.tmp.name) / "data" / "window.json").exists())

    def test_turning_it_on_sets_the_window_and_is_kept_for_the_next_start(self):
        api, win = window.WindowApi(), FakeWin()
        api._attach(win)
        self.assertEqual(api.windowSetOnTop(True), {"ok": True, "onTop": True})
        self.assertIs(win.on_top, True)
        self.assertEqual(json.loads((Path(self.tmp.name) / "data" / "window.json").read_text()), {"onTop": True})
        self.assertEqual(window.WindowApi().windowGetOnTop(), {"onTop": True})       # the next start
        self.assertEqual(api.windowSetOnTop(False), {"ok": True, "onTop": False})
        self.assertIs(win.on_top, False)
        self.assertEqual(window.WindowApi().windowGetOnTop(), {"onTop": False})

    def test_without_a_window_the_state_does_not_change(self):
        api = window.WindowApi()
        self.assertEqual(api.windowSetOnTop(True), {"ok": False, "onTop": False})

    def test_a_damaged_file_gives_the_defaults(self):
        f = Path(self.tmp.name) / "data" / "window.json"
        f.parent.mkdir(parents=True)
        for text in ("{not json", '{"onTop": "yes"}', "[1]"):
            f.write_text(text)
            self.assertEqual(window.load_prefs(), {"onTop": False})

    def test_resize_answers_for_systems_without_the_native_route(self):
        api, win = window.WindowApi(), FakeWin()
        api._attach(win)
        self.assertEqual(api.windowStartResize("e"), {"ok": True, "native": False})
        self.assertEqual(api.windowGetRect(), {"x": 10, "y": 20, "width": 1191, "height": 930, "minWidth": 960, "minHeight": 720})
        api.windowSetRect(5, 6, 500, 400)       # below the minimum: held at it
        self.assertEqual(win.calls, [("resize", 960, 720), ("move", 5, 6)])

    def test_perf_report_goes_to_the_log(self):
        with self.assertLogs("pt.window", "INFO") as cm:
            window.WindowApi().perfReport({"longTasks": 1})
        self.assertIn('perf {"longTasks":1}', cm.output[0])


class WebViewPatchTest(unittest.TestCase):
    def make(self):
        calls = []

        class EdgeChrome:
            def on_web_resource_request(self, s, a): calls.append("request")
            def on_web_resource_response(self, s, a): calls.append("response")
            def on_webview_ready(self, sender, args): calls.append("ready")

        class Ev:
            def __init__(self, name): self.name = name
            def __isub__(self, h): calls.append(f"-= {self.name}"); return self

        core = types.SimpleNamespace(RemoveWebResourceRequestedFilter=lambda uri, ctx: calls.append(f"filter off {uri} {ctx}"),
                                     WebResourceRequested=Ev("request"), WebResourceResponseReceived=Ev("response"))
        module = types.SimpleNamespace(EdgeChrome=EdgeChrome, CoreWebView2WebResourceContext=types.SimpleNamespace(All="All"))
        return module, core, calls

    def test_the_per_resource_handlers_do_nothing_and_the_filter_is_removed(self):
        module, core, calls = self.make()
        self.assertTrue(winpatch.patch_edge_chromium(module))
        obj = module.EdgeChrome()
        obj.on_web_resource_request(1, 2)
        obj.on_web_resource_response(1, 2)
        self.assertEqual(calls, [])
        obj.on_webview_ready(types.SimpleNamespace(CoreWebView2=core), types.SimpleNamespace(IsSuccess=True))
        self.assertEqual(calls, ["ready", "filter off * All", "-= request", "-= response"])

    def test_patched_once_and_a_failed_start_removes_nothing(self):
        module, core, calls = self.make()
        self.assertTrue(winpatch.patch_edge_chromium(module))
        self.assertFalse(winpatch.patch_edge_chromium(module))
        module.EdgeChrome().on_webview_ready(types.SimpleNamespace(CoreWebView2=core), types.SimpleNamespace(IsSuccess=False))
        self.assertEqual(calls, ["ready"])

    def test_a_failing_removal_step_does_not_stop_the_others(self):
        module, core, calls = self.make()
        core.RemoveWebResourceRequestedFilter = mock.Mock(side_effect=RuntimeError("x"))
        winpatch.patch_edge_chromium(module)
        module.EdgeChrome().on_webview_ready(types.SimpleNamespace(CoreWebView2=core), types.SimpleNamespace(IsSuccess=True))
        self.assertEqual(calls, ["ready", "-= request", "-= response"])

    def test_apply_does_nothing_outside_windows(self):
        with mock.patch.object(sys, "platform", "linux"):
            self.assertFalse(winpatch.apply())

    def test_apply_survives_a_pywebview_that_is_built_differently(self):
        class EdgeChrome:           # no on_webview_ready: patch_edge_chromium raises AttributeError
            pass
        edge = types.ModuleType("webview.platforms.edgechromium")
        edge.EdgeChrome = EdgeChrome
        plat, pkg = types.ModuleType("webview.platforms"), types.ModuleType("webview")
        plat.edgechromium, pkg.platforms = edge, plat
        fakes = {"webview": pkg, "webview.platforms": plat, "webview.platforms.edgechromium": edge}
        with mock.patch.object(sys, "platform", "win32"), mock.patch.dict(sys.modules, fakes), mock.patch.dict(os.environ):
            os.environ.pop("PULLS_TRACKER_NO_PATCH", None)
            with self.assertLogs("pt.winpatch", "WARNING"):
                self.assertFalse(winpatch.apply())


if __name__ == "__main__":
    unittest.main()
