"""Tests of what a shared .exe needs: one copy per data folder, the WebView2 check at start, the warning for a folder that a file-sync
program watches, a database of another format (a newer or an older version of the program), a message when the program cannot start,
and that nothing is written outside the data folder."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.pop("PULLS_TRACKER_DATA", None)      # a test must never touch a real data folder: each test that needs one sets its own

from pt import paths, preflight, single, store      # noqa: E402
from pt.api import PullsApi      # noqa: E402


class SingleInstanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a, self.b = Path(self.tmp.name) / "a" / "data", Path(self.tmp.name) / "b" / "data"

    @unittest.skipIf(sys.platform == "win32", "the Windows guard is a mutex; see test_mutex_name")
    def test_a_second_copy_on_the_same_data_folder_is_refused_until_the_first_ends(self):
        first = single.acquire(self.a)
        self.assertTrue(first.acquired)
        second = single.acquire(self.a)
        self.assertFalse(second.acquired)
        other = single.acquire(self.b)                      # another data folder: another program copy, allowed
        self.assertTrue(other.acquired)
        first.release()
        again = single.acquire(self.a)
        self.assertTrue(again.acquired)
        for g in (second, other, again):
            g.release()

    @unittest.skipIf(sys.platform == "win32", "uses a second process")
    def test_the_lock_goes_away_when_the_process_ends(self):
        code = "import sys; sys.path.insert(0, %r); from pt import single; g = single.acquire(%r); sys.exit(0 if g.acquired else 3)" % (
            str(Path(__file__).resolve().parent.parent), str(self.a))
        self.assertEqual(subprocess.run([sys.executable, "-c", code]).returncode, 0)      # takes the lock and ends
        mine = single.acquire(self.a)                                                     # so it is free again
        self.assertTrue(mine.acquired)
        self.assertFalse(single.acquire(self.a).acquired)                                 # and now it is held by this process
        mine.release()

    def test_a_guard_that_fails_lets_the_program_start(self):
        with mock.patch("builtins.open", side_effect=PermissionError("no")), mock.patch.object(sys, "platform", "linux"):
            self.assertTrue(single.acquire(self.a).acquired)

    def test_mutex_name_depends_on_the_data_folder_only(self):
        self.assertEqual(single.mutex_name(self.a), single.mutex_name(self.a))
        self.assertNotEqual(single.mutex_name(self.a), single.mutex_name(self.b))
        self.assertTrue(single.mutex_name(self.a).startswith("Local\\PullsTracker-"))
        self.assertLess(len(single.mutex_name(self.a)), 64)


class WebView2CheckTest(unittest.TestCase):
    def test_not_windows_or_runtime_present_goes_on_without_asking(self):
        ask = mock.Mock()
        self.assertTrue(preflight.webview2_ok(platform="linux", ask=ask))
        self.assertTrue(preflight.webview2_ok(platform="win32", version=lambda: "130.0.1", ask=ask))
        ask.assert_not_called()

    def test_missing_runtime_offers_the_download_and_yes_ends_the_program(self):
        opened = []
        ask = mock.Mock(return_value=preflight.IDYES)
        self.assertFalse(preflight.webview2_ok(platform="win32", version=lambda: None, ask=ask, open_url=opened.append))
        self.assertEqual(opened, [preflight.WEBVIEW2_URL])
        text = ask.call_args[0][0]
        self.assertIn(preflight.WEBVIEW2_URL, text)
        self.assertIn("WebView2", text)

    def test_no_starts_anyway_and_opens_nothing(self):
        opened = []
        self.assertTrue(preflight.webview2_ok(platform="win32", version=lambda: None, ask=lambda *a: 7, open_url=opened.append))
        self.assertEqual(opened, [])

    def test_a_failing_check_does_not_stop_the_program(self):
        def boom():
            raise OSError("registry")
        self.assertTrue(preflight.webview2_ok(platform="win32", version=boom, ask=mock.Mock()))
        self.assertTrue(preflight.webview2_ok(platform="win32", version=lambda: None, ask=mock.Mock(side_effect=OSError("no ui"))))

    def test_already_running_message(self):
        ask = mock.Mock()
        preflight.already_running(ask=ask, platform="win32")
        self.assertIn("already running", ask.call_args[0][0])


class CloudFolderTest(unittest.TestCase):
    def test_names(self):
        for p in ("C:/Users/ana/OneDrive/Games/PullsTracker/data", "C:/Users/ana/OneDrive - Company/x", "D:/Dropbox/pt", "/home/a/Google Drive/pt"):
            self.assertTrue(paths.cloud_folder(Path(p)), p)
        for p in ("C:/Users/ana/Documents/PullsTracker/data", "D:/Games/OneDriveTools/x", "/home/a/dropboxes/pt", "D:/Megabyte/pt"):
            self.assertEqual(paths.cloud_folder(Path(p)), "", p)

    def test_the_note_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "OneDrive" / "data"
            with mock.patch.dict(os.environ, {"PULLS_TRACKER_DATA": str(d)}):
                self.assertIn("WARNING", paths.data_dir_note())
                self.assertIn("OneDrive", paths.data_dir_note())
            with mock.patch.dict(os.environ, {"PULLS_TRACKER_DATA": str(Path(tmp) / "plain" / "data")}):
                self.assertNotIn("WARNING", paths.data_dir_note())


class DatabaseFormatTest(unittest.TestCase):
    """data/pulls.sqlite stays when a newer .exe replaces the old one, and an older .exe can meet a database that a newer one wrote."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "data" / "pulls.sqlite"

    def _make(self, fmt):
        s = store.Store(self.db)
        s.add_pulls("hsr", "800000001", [{"id": "1700000000000000001", "gacha_type": "11", "item_id": "1", "name": "A", "item_type": "Character",
                                          "rank_raw": 3, "rarity": 3, "time": 1700000000}])
        s._db.execute("UPDATE meta SET value=? WHERE key='schema'", (str(fmt),))
        s._db.commit()
        s.close()

    def test_a_new_database_records_the_format(self):
        store.Store(self.db).close()
        self.assertEqual(store.stored_schema(self.db), int(store.SCHEMA_VERSION))

    def test_no_file_means_no_format_and_none_is_created(self):
        self.assertIsNone(store.stored_schema(self.db))
        self.assertFalse(self.db.exists())

    def test_a_newer_database_is_refused_and_left_unchanged(self):
        self._make(int(store.SCHEMA_VERSION) + 1)
        before = self.db.read_bytes()
        with self.assertRaises(store.DatabaseTooNew) as cm:
            store.Store(self.db)
        self.assertEqual((cm.exception.found, cm.exception.supported), (int(store.SCHEMA_VERSION) + 1, int(store.SCHEMA_VERSION)))
        self.assertEqual(store.stored_schema(self.db), int(store.SCHEMA_VERSION) + 1)
        self.assertEqual(sorted(p.name for p in self.db.parent.iterdir() if "damaged" in p.name), [])      # not treated as a damaged file
        self.assertEqual(self.db.read_bytes()[:16], before[:16])

    def test_an_older_database_is_migrated_after_a_copy_is_made(self):
        self._make(1)
        calls = []

        def to_two(db):
            calls.append(1)
            db.execute("ALTER TABLE accounts ADD COLUMN note TEXT NOT NULL DEFAULT ''")

        with mock.patch.object(store, "SCHEMA_VERSION", "2"), mock.patch.dict(store.MIGRATIONS, {1: to_two}):
            s = store.Store(self.db)
            self.assertEqual(calls, [1])
            self.assertEqual(store.stored_schema(self.db), 2)
            self.assertEqual(s._db.execute("SELECT COUNT(*) FROM pulls").fetchone()[0], 1)      # the pulls are still there
            cols = [r[1] for r in s._db.execute("PRAGMA table_info(accounts)")]
            self.assertIn("note", cols)
            s.close()
        aside = self.db.with_name(self.db.name + ".before-format-1")
        self.assertTrue(aside.is_file())
        self.assertEqual(store.stored_schema(aside), 1)             # the copy is the old format, the pulls are in it
        import sqlite3
        c = sqlite3.connect(str(aside))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM pulls").fetchone()[0], 1)
        c.close()

    def test_a_failing_step_changes_nothing_and_stops_the_program(self):
        self._make(1)

        def broken(db):
            db.execute("ALTER TABLE accounts ADD COLUMN half TEXT")
            raise ValueError("step failed")

        with mock.patch.object(store, "SCHEMA_VERSION", "2"), mock.patch.dict(store.MIGRATIONS, {1: broken}):
            with self.assertRaises(ValueError):
                store.Store(self.db)
        self.assertEqual(store.stored_schema(self.db), 1)
        import sqlite3
        c = sqlite3.connect(str(self.db))
        self.assertNotIn("half", [r[1] for r in c.execute("PRAGMA table_info(accounts)")])
        c.close()

    def test_a_missing_step_is_an_error_not_a_silent_skip(self):
        self._make(1)
        with mock.patch.object(store, "SCHEMA_VERSION", "2"), mock.patch.dict(store.MIGRATIONS, {}, clear=True):
            with self.assertRaises(RuntimeError):
                store.Store(self.db)
        self.assertEqual(store.stored_schema(self.db), 1)

    def test_the_start_check(self):
        ask = mock.Mock()
        self.assertTrue(preflight.database_ok(None, 1, "x", ask=ask, platform="win32"))
        self.assertTrue(preflight.database_ok(1, 1, "x", ask=ask, platform="win32"))
        self.assertTrue(preflight.database_ok(0, 1, "x", ask=ask, platform="win32"))
        ask.assert_not_called()
        self.assertFalse(preflight.database_ok(3, 1, "D:/pt/data", ask=ask, platform="win32"))
        text = ask.call_args[0][0]
        self.assertIn("newer version", text)
        self.assertIn("D:/pt/data", text)
        self.assertIn("Nothing was changed", text)


class StartFailureTest(unittest.TestCase):
    def test_the_message_names_the_error_and_the_log(self):
        ask = mock.Mock()
        preflight.fatal(PermissionError("no access to the data folder"), "D:/pt/data/pulls_tracker.log", ask=ask, platform="win32")
        text, title, flags = ask.call_args[0]
        self.assertIn("could not start", text)
        self.assertIn("PermissionError: no access to the data folder", text)
        self.assertIn("D:/pt/data/pulls_tracker.log", text)
        self.assertEqual(flags, preflight.MB_ICONERROR)

    def test_without_a_message_box_the_text_goes_to_stderr(self):
        with mock.patch("sys.stderr") as err:
            preflight.fatal(OSError("x"), None, ask=mock.Mock(side_effect=OSError("no ui")), platform="win32")
        self.assertIn("could not start", "".join(c.args[0] for c in err.write.call_args_list))

    def test_the_program_reports_a_failure_to_start_and_returns_1(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("pulls_tracker_main", str(Path(__file__).resolve().parent.parent / "pulls_tracker.py"))
        main = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(main)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"PULLS_TRACKER_DATA": str(Path(tmp) / "d")}):
            with mock.patch.object(preflight, "fatal") as fatal, mock.patch("pt.window.run", side_effect=RuntimeError("window failed")), \
                    mock.patch.object(single, "acquire", return_value=single.Guard(True)):
                self.assertEqual(main.main([]), 1)
            exc, log_file = fatal.call_args[0]
            self.assertIsInstance(exc, RuntimeError)
            self.assertTrue(str(log_file).endswith("pulls_tracker.log"))
            self.assertIn("window failed", Path(log_file).read_text(encoding="utf-8"))
            import logging
            for h in list(logging.getLogger().handlers):          # the log file of the test run is closed so the folder can be removed
                if getattr(h, "baseFilename", "").startswith(tmp):
                    logging.getLogger().removeHandler(h)
                    h.close()

    def test_a_database_of_a_newer_format_ends_the_start_before_the_window(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("pulls_tracker_main2", str(Path(__file__).resolve().parent.parent / "pulls_tracker.py"))
        main = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(main)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"PULLS_TRACKER_DATA": str(Path(tmp) / "d")}):
            with mock.patch.object(store, "stored_schema", return_value=99), mock.patch.object(preflight, "database_ok", return_value=False) as ok, \
                    mock.patch("pt.window.run") as run, mock.patch.object(single, "acquire", return_value=single.Guard(True)):
                self.assertEqual(main.main([]), 1)
            ok.assert_called_once()
            self.assertEqual(ok.call_args[0][:2], (99, int(store.SCHEMA_VERSION)))
            run.assert_not_called()
            import logging
            for h in list(logging.getLogger().handlers):
                if getattr(h, "baseFilename", "").startswith(tmp):
                    logging.getLogger().removeHandler(h)
                    h.close()


class StorageInfoTest(unittest.TestCase):
    def test_the_data_folder_and_the_sync_warning_are_given_to_the_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain, synced = Path(tmp) / "plain" / "data", Path(tmp) / "OneDrive" / "Desktop" / "data"
            for d, warns in ((plain, False), (synced, True)):
                with mock.patch.dict(os.environ, {"PULLS_TRACKER_DATA": str(d)}):
                    api = PullsApi()
                    info = api.storage()
                    api._tracker.store.close()
                self.assertEqual(info["dataFolder"], str(d.resolve()))
                self.assertEqual(bool(info["warning"]), warns)
                if warns:
                    self.assertIn("OneDrive", info["warning"])


class NothingOutsideTheDataFolderTest(unittest.TestCase):
    """The program is started as a child process with its data folder, its home folder and its working folder all new and empty;
    afterwards the only things written are inside the data folder."""

    def test_selfcheck_writes_only_to_the_data_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data, home, cwd = tmp / "data folder ü", tmp / "home", tmp / "cwd"
            home.mkdir()
            cwd.mkdir()
            env = {**os.environ, "PULLS_TRACKER_DATA": str(data), "HOME": str(home), "USERPROFILE": str(home), "LOCALAPPDATA": str(home / "local")}
            root = Path(__file__).resolve().parent.parent
            r = subprocess.run([sys.executable, str(root / "pulls_tracker.py"), "--selfcheck"], cwd=str(cwd), env=env, capture_output=True, text=True, timeout=120)
            self.assertIn("data folder can be written", r.stdout)
            self.assertTrue((data / "selfcheck.txt").is_file())
            self.assertEqual(list(home.rglob("*")), [])
            self.assertEqual(list(cwd.rglob("*")), [])
            self.assertEqual(list((root / "data").rglob("*")) if (root / "data").is_dir() else [], [])


if __name__ == "__main__":
    unittest.main()
