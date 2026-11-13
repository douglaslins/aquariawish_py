"""Tests of the fixes made after the code audit. Each test fails on the code as it was before its fix.
Batch T7a: the data layer (pt/store.py, pt/tracker.py, pt/paths.py). Batch T7b: the bridge and the fetcher (pt/api.py, pt/pulls.py,\npt/locate.py, banner_tracker_test.py). Every database and file is made in a temp folder."""
from __future__ import annotations

import copy
import ctypes
import json
import logging
import logging.handlers
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import banner_tracker_test as fetcher                                    # noqa: E402
from test_backend import Base, FakeWindow, seq                           # noqa: E402
from pt import locate, log as log_mod, paths, pulls, selfcheck, settings as settings_mod, single, window     # noqa: E402
from pt import store as store_mod, tracker as tracker_mod                # noqa: E402
from pt.api import PullsApi                                              # noqa: E402
from pt.games import GENSHIN                                             # noqa: E402
from pt.store import DatabaseTooNew, Store, _is_damaged, full_key        # noqa: E402

UID = "800000001"
ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "pulls_tracker_ui"

try:
    from playwright.sync_api import sync_playwright
except ImportError:      # pragma: no cover
    sync_playwright = None


class Failing(sqlite3.Connection):
    """A connection whose statements that start with `fail_on` raise, to test a write that stops half way."""
    fail_on = None

    def execute(self, sql, *args):
        if Failing.fail_on and sql.lstrip().startswith(Failing.fail_on):
            raise sqlite3.OperationalError("disk I/O error")
        return super().execute(sql, *args)


class StoreAuditTest(Base):
    def failing_store(self, name):
        s = Store(self.root / name / "pulls.sqlite")
        s._db.close()
        s._db = sqlite3.connect(str(s.path), factory=Failing, check_same_thread=False)
        s._db.row_factory = sqlite3.Row
        self.addCleanup(s.close)
        self.addCleanup(setattr, Failing, "fail_on", None)
        s.ensure_account("genshin", UID, "Asia", 8)
        return s

    # T3-1
    def test_only_a_file_that_is_not_a_database_counts_as_damaged(self):
        for e in (sqlite3.IntegrityError("UNIQUE constraint failed"), sqlite3.ProgrammingError("Cannot operate on a closed database."),
                  sqlite3.OperationalError("disk I/O error"), sqlite3.OperationalError("database is locked")):
            self.assertFalse(_is_damaged(e), repr(e))
        self.assertTrue(_is_damaged(sqlite3.DatabaseError("file is not a database")))
        self.assertTrue(_is_damaged(sqlite3.DatabaseError("database disk image is malformed")))
        self.assertTrue(_is_damaged(sqlite3.OperationalError("malformed database schema (x)")))

    def test_an_error_of_the_program_while_opening_does_not_move_the_database_aside(self):
        path = self.root / "open" / "pulls.sqlite"
        Store(path).close()
        for exc in (sqlite3.IntegrityError("UNIQUE constraint failed"), sqlite3.ProgrammingError("bug")):
            with mock.patch.object(Store, "_connect", side_effect=exc):
                with self.assertRaises(type(exc)):
                    Store(path)
        self.assertTrue(path.exists())
        self.assertEqual([p.name for p in path.parent.iterdir() if "damaged" in p.name], [])

    # T3-2
    def test_a_remove_account_that_fails_half_way_changes_nothing(self):
        s = self.failing_store("rm")
        s.add_pulls("genshin", UID, seq(GENSHIN, "301", [3, 4]))
        flag = full_key("genshin", UID, "character")
        s.set_meta(flag, "1")
        Failing.fail_on = "DELETE FROM meta"
        with self.assertRaises(sqlite3.OperationalError):
            s.remove_account("genshin", UID)
        Failing.fail_on = None
        s.set_meta("unrelated", "1")                  # a later write commits whatever is still open
        self.assertEqual(s.count("genshin", UID), 2)
        self.assertIsNotNone(s.account("genshin", UID))
        self.assertEqual(s.meta(flag), "1")

    def test_an_update_account_that_fails_half_way_changes_nothing(self):
        s = self.failing_store("upd")
        Failing.fail_on = "UPDATE accounts SET region"
        with self.assertRaises(sqlite3.OperationalError):
            s.update_account("genshin", UID, utc_offset=1.0, lang="de", region="Europe")
        Failing.fail_on = None
        s.set_meta("unrelated", "1")
        a = s.account("genshin", UID)
        self.assertEqual((a["utc_offset"], a["lang"], a["region"]), (8, "", "Asia"))

    def test_remove_account_and_update_account_still_work(self):
        s = self.failing_store("ok")
        s.add_pulls("genshin", UID, seq(GENSHIN, "301", [3]))
        s.update_account("genshin", UID, utc_offset=1.0, lang="de", region="Europe")
        a = s.account("genshin", UID)
        self.assertEqual((a["utc_offset"], a["lang"], a["region"]), (1.0, "de", "Europe"))
        self.assertEqual(s.remove_account("genshin", UID), 1)
        self.assertIsNone(s.account("genshin", UID))

    # T3-3
    def test_two_damaged_databases_of_the_same_second_are_both_kept(self):
        path = self.root / "dmg" / "pulls.sqlite"
        path.parent.mkdir()
        with mock.patch.object(store_mod.time, "strftime", lambda *_: "20260101-000000"):
            path.write_bytes(b"first damaged file " * 50)
            Store(path).close()
            path.write_bytes(b"second damaged file " * 50)
            Store(path).close()
        kept = [p for p in path.parent.glob("pulls.sqlite.damaged-*") if not p.name.endswith(("-wal", "-shm"))]
        self.assertEqual(len(kept), 2)
        self.assertEqual(sorted(p.read_bytes()[:6] for p in kept), [b"first ", b"second"])

    # T3-8
    def test_a_database_of_a_newer_format_is_left_byte_for_byte(self):
        path = self.root / "new" / "pulls.sqlite"
        Store(path).close()
        db = sqlite3.connect(str(path))
        db.execute("UPDATE meta SET value='99' WHERE key='schema'")
        db.execute("DROP TABLE banner_archive")                # the program would recreate it from its own schema
        db.commit()
        db.close()
        before = path.read_bytes()
        with self.assertRaises(DatabaseTooNew):
            Store(path)
        self.assertEqual(path.read_bytes(), before)
        db = sqlite3.connect(str(path))
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        db.close()
        self.assertNotIn("banner_archive", tables)


class TrackerAuditTest(Base):
    def slide(self, name="Mona"):
        return {"genshin": [{"group": "character", "bannerName": "B", "start": "2026-01-01T00:00:00Z", "end": "2026-01-20T00:00:00Z",
                             "name": name, "rank": 5, "kind": "character"}]}

    # T3-4
    def test_refresh_banners_survives_a_file_that_cannot_be_examined(self):
        t = self.tracker()                                      # the constructor reads the banner files, so the file is written after it
        self.banners(self.slide())
        real_stat, real_is_file = Path.stat, Path.is_file

        def stat(p, *a, **k):
            if p.name == "banners.js":
                raise PermissionError(13, "Access is denied")
            return real_stat(p, *a, **k)

        def is_file(p, *a, **k):
            if p.name == "banners.js":
                raise PermissionError(13, "Access is denied")
            return real_is_file(p, *a, **k)

        with mock.patch.object(Path, "stat", stat), mock.patch.object(Path, "is_file", is_file):
            self.assertEqual(t.refresh_banners(), 0)            # no exception

    def test_banners_that_could_not_be_saved_are_tried_again_on_the_next_call(self):
        t = self.tracker()
        self.banners(self.slide())
        with mock.patch.object(Store, "archive_banner", side_effect=sqlite3.OperationalError("database is locked")):
            self.assertEqual(t.refresh_banners(), 0)
        self.assertEqual(t.refresh_banners(), 1)                # the files did not change, the banner is saved now
        self.assertEqual(t.refresh_banners(), 0)                # and is not read again

    def test_a_file_of_the_wrong_shape_is_not_read_again_on_every_call(self):
        t = self.tracker()
        self.banners({"genshin": "not a list of slides"})
        with mock.patch.object(t, "_archive", wraps=t._archive) as spy:
            t.refresh_banners()
            t.refresh_banners()
            t.refresh_banners()
        self.assertEqual(spy.call_count, 1)

    # T3-5
    def test_a_date_outside_the_kept_range_is_not_a_date(self):
        for text in ("0001-01-01T00:00:00Z", "9999-12-31T23:59:59Z", "1969-12-31T23:59:59Z", "", None, "garbage"):
            self.assertIsNone(tracker_mod.parse_iso(text), text)
        self.assertEqual(tracker_mod.parse_iso("2026-10-13T10:00:00Z"), 1791885600)
        self.assertEqual(tracker_mod.parse_iso("1970-01-01T00:00:00Z"), 0)

    def test_a_banner_with_an_impossible_start_is_not_archived(self):
        bad = {"genshin": [{"group": "character", "bannerName": "B", "start": "0001-01-01T00:00:00Z", "end": "2026-01-20T00:00:00Z",
                            "name": "Mona", "rank": 5, "kind": "character"}]}
        self.banners(bad)
        t = self.tracker()
        self.assertEqual(t.refresh_banners(), 0)
        self.assertEqual(self.store.banners("genshin"), [])

    # T3-6
    def test_a_backup_that_disappears_after_listing_does_not_stop_the_next_backup(self):
        self.store.ensure_account("genshin", UID, "Asia", 8)
        self.store.add_pulls("genshin", UID, seq(GENSHIN, "301", [3]))
        t = self.tracker()
        t.backup(force=True)
        newest = t.backup_files()[-1]
        real_stat = Path.stat
        listed = mock.patch.object(t, "backup_files", return_value=[newest])      # the listing still shows it; then it is gone

        def stat(p, *a, **k):
            if p == newest:
                raise FileNotFoundError(2, "removed")
            return real_stat(p, *a, **k)

        self.now += 3600                                         # younger than BACKUP_MIN_AGE, so only the stat decides
        with listed, mock.patch.object(Path, "stat", stat):
            made = t.backup()
        self.assertIsNotNone(made)                               # treated as "no recent backup": a new one is made

    # T3-7
    def test_the_database_path_is_named_in_one_place(self):
        self.assertEqual(paths.database_path(), paths.data_dir() / "pulls.sqlite")
        src = (Path(__file__).resolve().parent.parent / "pulls_tracker.py").read_text(encoding="utf-8")
        self.assertIn("paths.database_path()", src)
        self.assertNotIn('"pulls.sqlite"', src)


class ApiAuditTest(Base):
    def setUp(self):
        super().setUp()
        self.api = PullsApi(self.tracker())

    def pick(self, path):
        self.api._window = FakeWindow([[str(path)]])
        return self.api.pickImportFile()

    # T4-1
    def test_the_page_cannot_make_importpreview_read_a_file_it_names(self):
        existing = self.root / "secret.txt"
        existing.write_text("just a text file", encoding="utf-8")
        missing = self.root / "nope.json"
        a, b = (self.api.importPreview({"path": str(p)}) for p in (existing, missing))
        self.assertEqual(a, b)                                  # the answer does not tell which paths exist
        self.assertFalse(a["ok"])
        self.assertNotIn("not a UIGF", a["error"])

    def test_importpreview_reads_the_chosen_file_and_no_other(self):
        chosen, other = self.root / "chosen.txt", self.root / "other.txt"
        for f in (chosen, other):
            f.write_text("just a text file", encoding="utf-8")
        refusal = self.api.importPreview({"path": str(other)})["error"]
        picked = self.pick(chosen)
        self.assertNotEqual(self.api.importPreview(picked)["error"], refusal)         # read: it is not an export, and the answer says so
        self.assertEqual(self.api.importPreview({"path": str(other)})["error"], refusal)
        self.assertEqual(self.api.importPreview({"name": "x.json", "text": "{}"}).get("error", "").count("Choose the file"), 0)   # text from the page's file input

    # T4-2
    def test_clearimagecache_deletes_the_folder_it_checked(self):
        art = paths.data_dir() / "assets" / "art" / "genshin"
        art.mkdir(parents=True)
        (art / "x.png").write_bytes(b"x" * 2000)
        with mock.patch("pt.api.shutil.rmtree", wraps=shutil.rmtree) as rm:
            self.assertEqual(self.api.clearImageCache(), {"cleared": 1})
        self.assertEqual(rm.call_args[0][0], (paths.data_dir() / "assets" / "art").resolve())
        self.assertFalse((art / "x.png").exists())

    def test_clearimagecache_clears_the_target_of_a_link_that_stays_inside_the_assets_folder(self):
        real = paths.data_dir() / "assets" / "art_real"
        (real / "genshin").mkdir(parents=True)
        (real / "genshin" / "x.png").write_bytes(b"x" * 2000)
        try:
            os.symlink(real, paths.data_dir() / "assets" / "art", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("this system does not allow a symbolic link")
        self.assertEqual(self.api.clearImageCache(), {"cleared": 1})       # rmtree does not follow a link: it has to be given the target
        self.assertFalse((real / "genshin" / "x.png").exists())

    def test_clearimagecache_refuses_a_link_that_leads_out_of_the_assets_folder(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep", encoding="utf-8")
        link = paths.data_dir() / "assets" / "art"
        link.parent.mkdir(parents=True)
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("this system does not allow a symbolic link")
        self.assertIn("error", self.api.clearImageCache())
        self.assertTrue((outside / "keep.txt").exists())

    # T4-3
    def test_a_lone_surrogate_in_a_pull_is_replaced_and_the_pull_is_stored_and_exported(self):
        item = {"id": "1700000000000000001", "gacha_type": "301", "rank_type": "3", "time": "2026-01-01 00:00:00", "name": "bad\ud800name",
                "item_id": "1\udfff", "item_type": "x\ud800", "count": "1\ud800"}
        n = pulls.normalize(GENSHIN, item, 8)
        self.assertEqual((n["name"], n["item_id"], n["item_type"]), ("bad\ufffdname", "1\ufffd", "x\ufffd"))
        self.assertEqual(n["raw"]["count"], "1\ufffd")
        self.store.ensure_account("genshin", UID, "Asia", 8)
        self.assertEqual(self.store.add_pulls("genshin", UID, [n]), {"301": 1})
        rows = self.store.pulls("genshin", UID)
        json.dumps(pulls.export_uigf(GENSHIN, UID, rows, 8)).encode("utf-8")
        pulls.export_csv(GENSHIN, rows, 8).encode("utf-8")

    # T4-5
    def test_the_authkey_is_not_in_the_repr_of_a_link(self):
        link = locate.Link("https://h.hoyoverse.com/gacha_info/api/getGachaLog?authkey=SECRETKEY", "h.hoyoverse.com", "/p", ("authkey=SECRETKEY",))
        self.assertNotIn("SECRETKEY", repr(link))
        self.assertNotIn("SECRETKEY", str(link))
        self.assertEqual(link.value("authkey"), "SECRETKEY")           # the key itself is still there for the request


class FetcherAuditTest(Base):
    # T4-4
    def test_file_safe_limits_the_length_keeps_long_names_apart_and_avoids_device_names(self):
        a, b = fetcher.file_safe("x" * 300), fetcher.file_safe("x" * 299 + "y")
        self.assertLessEqual(len(a), fetcher.MAX_FILE_STEM)
        self.assertNotEqual(a, b)
        self.assertEqual(a, fetcher.file_safe("x" * 300))              # the same text gives the same name
        self.assertEqual(fetcher.file_safe("1001"), "1001")
        self.assertEqual(fetcher.file_safe("../../evil"), ".._.._evil")
        for name in ("CON", "nul", "COM1", "lpt9"):
            self.assertTrue(fetcher.file_safe(name).startswith("_"), name)

    def test_a_very_long_id_is_saved_inside_the_root(self):
        root = self.root / "assets_root"
        rel = fetcher.AssetStore(str(root)).save("genshin", fetcher.file_safe("9" * 300) + "-icon", {"mime": "image/png", "data": b"x" * 2000})
        target = (root / rel).resolve()
        self.assertTrue(target.is_file())
        self.assertTrue(str(target).startswith(str(root.resolve())))

    # T4-3
    def test_banners_js_with_a_lone_surrogate_is_written_as_utf8_and_leaves_no_tmp_file(self):
        ui = self.root / "ui2"
        payload = {"generatedAt": "x", "slides": {"genshin": [{"name": "bad\ud800"}]}, "games": {}}
        path = Path(fetcher.write_banners_js(str(ui), payload))
        text = path.read_text(encoding="utf-8")                       # strict UTF-8 decoding
        self.assertIn("bad\ufffd", text)
        self.assertFalse(path.with_name("banners.js.tmp").exists())
        self.assertEqual(tracker_mod.read_banners_js(path)["slides"]["genshin"][0]["name"], "bad\ufffd")

    def test_a_failed_write_of_banners_js_leaves_no_tmp_file(self):
        ui = self.root / "ui3"
        with mock.patch("banner_tracker_test.os.replace", side_effect=OSError("denied")):
            with self.assertRaises(OSError):
                fetcher.write_banners_js(str(ui), {"generatedAt": "x", "slides": {}, "games": {}})
        self.assertEqual([p.name for p in (ui / "js").iterdir()], [])


class EntryAuditTest(Base):
    """Batch T7c: settings, log, single-instance guard, self check, window start, build scripts."""

    # T2-2
    def test_a_relative_backup_folder_is_not_kept(self):
        base = copy.deepcopy(settings_mod.DEFAULTS)
        absolute = str(self.root / "backups")
        for value, kept in (("relative/dir", ""), ("..", ""), ("~/backups", ""), ("", ""), (absolute, absolute)):
            self.assertEqual(settings_mod.clean({**base, "backupFolder": value})["backupFolder"], kept, value)

    # T2-3
    def test_a_settings_file_that_cannot_be_read_is_kept_as_bad(self):
        f = settings_mod.path()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b'{"maskUid": false, "broken": ')
        self.assertEqual(settings_mod.load(), settings_mod.DEFAULTS)
        self.assertFalse(f.exists())
        self.assertEqual(f.with_name("settings.json.bad").read_bytes(), b'{"maskUid": false, "broken": ')
        settings_mod.save(settings_mod.load())                      # the next save does not touch the kept copy
        self.assertTrue(f.with_name("settings.json.bad").exists())

    def test_a_missing_settings_file_is_not_an_error_and_a_bom_is_accepted(self):
        f = settings_mod.path()
        self.assertEqual(settings_mod.load(), settings_mod.DEFAULTS)
        self.assertFalse(f.with_name("settings.json.bad").exists())
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"\xef\xbb\xbf" + json.dumps({"maskUid": False}).encode("utf-8"))           # a file saved by Notepad starts with a BOM
        self.assertIs(settings_mod.load()["maskUid"], False)
        self.assertTrue(f.exists())

    # T2-4
    def test_log_setup_called_twice_writes_each_line_once(self):
        root = logging.getLogger()
        before_handlers, before_level = list(root.handlers), root.level

        def restore():
            for h in list(root.handlers):
                if h not in before_handlers:
                    root.removeHandler(h)
                    h.close()
            root.setLevel(before_level)

        self.addCleanup(restore)
        with mock.patch.object(sys, "excepthook", sys.excepthook), mock.patch.object(threading, "excepthook", threading.excepthook):
            log_mod.setup()
            log_mod.setup()
        files = [h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler) and h not in before_handlers]
        self.assertEqual(len(files), 1)
        logging.getLogger("pt.audit").warning("line-written-once")
        for h in files:
            h.flush()
        self.assertEqual((paths.data_dir() / "pulls_tracker.log").read_text(encoding="utf-8").count("line-written-once"), 1)

    # T2-5
    def test_the_mutex_is_closed_with_the_library_object_that_has_its_argument_types(self):
        k32 = mock.MagicMock()
        k32.CreateMutexW.return_value = 1234
        with mock.patch.object(single.sys, "platform", "win32"), mock.patch.object(ctypes, "WinDLL", create=True, return_value=k32) as dll, \
                mock.patch.object(ctypes, "get_last_error", create=True, return_value=0):
            guard = single.acquire(self.root / "data")
            self.assertTrue(guard.acquired)
            guard.release()
            guard.release()                                              # a second call does nothing
        self.assertEqual(dll.call_count, 1)
        k32.CloseHandle.assert_called_once_with(1234)
        self.assertEqual(k32.CloseHandle.argtypes, [ctypes.c_void_p])

    def test_a_second_copy_closes_the_handle_it_made(self):
        k32 = mock.MagicMock()
        k32.CreateMutexW.return_value = 77
        with mock.patch.object(single.sys, "platform", "win32"), mock.patch.object(ctypes, "WinDLL", create=True, return_value=k32), \
                mock.patch.object(ctypes, "get_last_error", create=True, return_value=single.ERROR_ALREADY_EXISTS):
            self.assertFalse(single.acquire(self.root / "data").acquired)
        k32.CloseHandle.assert_called_once_with(77)

    # T2-6
    def test_the_self_check_message_says_whether_the_report_was_saved(self):
        target = self.root / "data" / "selfcheck.txt"
        self.assertIn(f"Saved in {target}", selfcheck.message_text("ok", True, target))
        failed = selfcheck.message_text("ok", False, target)
        self.assertNotIn("Saved in", failed)
        self.assertIn("could not be saved", failed)
        long = selfcheck.message_text("x" * 5000, True, target)
        self.assertTrue(long.startswith("x" * 1800 + "\n..."))
        self.assertFalse(long.startswith("x" * 1801))

    # T2-7
    def test_a_missing_pywebview_is_an_exception_the_start_handler_can_show(self):
        with mock.patch.dict(sys.modules, {"webview": None}):
            with self.assertRaises(RuntimeError) as cm:             # SystemExit is not an Exception: the handler of pulls_tracker.py would not see it
                window.run()
        self.assertIn("pywebview is not installed", str(cm.exception))

    # T2-1
    @unittest.skipUnless(shutil.which("sh"), "no sh on this system")
    def test_the_build_script_stops_before_it_can_delete_a_database(self):
        work = self.root / "build"
        db = work / "dist" / "AquariaWish - Pulls Tracker" / "data" / "pulls.sqlite"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"the person's pulls")
        shutil.copy(ROOT / "build_exe.sh", work / "build_exe.sh")
        r = subprocess.run(["sh", str(work / "build_exe.sh")], capture_output=True, text=True, timeout=60, cwd=str(self.root))
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("would delete it", r.stderr)
        self.assertEqual(db.read_bytes(), b"the person's pulls")
        self.assertFalse((work / ".venv-build").exists())            # nothing was installed either

    def test_the_windows_build_script_has_the_same_guard_before_the_build(self):
        text = (ROOT / "build_exe.bat").read_text(encoding="utf-8")
        guard, build = text.index('if exist "dist\\AquariaWish - Pulls Tracker\\data\\pulls.sqlite"'), text.index("-m PyInstaller")
        self.assertLess(guard, text.index("python -m venv"))
        self.assertLess(guard, build)
        self.assertIn("exit /b 3", text[guard:build])


@unittest.skipIf(sync_playwright is None, "Playwright is not installed")
class DataParameterTest(unittest.TestCase):
    """T2-8 and T5-1: the data= parameter names a local folder or a relative path, never another computer."""

    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch()
        except Exception as e:      # noqa: BLE001 - Chromium not installed for this Playwright
            cls.pw.stop()
            raise unittest.SkipTest(f"Chromium is not available: {type(e).__name__}") from e

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def load(self, page_name, suffix):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        seen = []
        page.on("request", lambda r: seen.append(r.url))
        page.route("http*://**", lambda r: r.abort())                 # nothing leaves the test, whatever the page tries
        page.goto((UI / page_name).as_uri() + suffix)
        page.wait_for_timeout(300)
        own = (UI / page_name).as_uri()
        return page, [u for u in seen if not u.startswith(own)]           # the address of the page itself holds the test value

    BAD = ("https://evil.example/x/", "HTTP://evil.example/x/", "//evil.example/x/", "javascript:alert(1)", "data:text/javascript,1", "C:/x/")

    def test_index_refuses_an_address_of_another_computer(self):
        for value in self.BAD:
            page, seen = self.load("index.html", "?data=" + value)
            self.assertEqual(page.evaluate("window.PT_DATA_BASE"), "", value)
            self.assertEqual([u for u in seen if "evil.example" in u], [], value)

    def test_index_accepts_a_local_folder_and_a_relative_path(self):
        for value, base in (("file:///tmp/zz/", "file:///tmp/zz/"), ("file://server/share/data", "file://server/share/data/"), ("../data/", "../data/"), ("", "")):
            page, _ = self.load("index.html", "?data=" + value)
            self.assertEqual(page.evaluate("window.PT_DATA_BASE"), base, value)

    def test_loading_page_refuses_an_address_of_another_computer(self):
        for value in self.BAD:
            _, seen = self.load("loading.html", "#data=" + value)
            self.assertEqual([u for u in seen if "evil.example" in u], [], value)
        _, seen = self.load("loading.html", "#data=file:///tmp/zz/")
        self.assertTrue(any(u.startswith("file:///tmp/zz/assets/loading/banner.") for u in seen), seen)


if __name__ == "__main__":
    unittest.main()
