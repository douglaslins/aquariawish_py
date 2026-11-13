"""Tests of the banner and asset engine and of the load tasks, with the network replaced by canned answers (no network needed)."""
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
import zlib
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.pop("PULLS_TRACKER_DATA", None)      # a test must never touch a real data folder: each test that needs one sets its own
import banner_tracker_test as f  # noqa: E402
from pt import banners, loader, paths, window  # noqa: E402
from pt.loader import Context, Loader  # noqa: E402

NOW = int(time.time())


def png(w=48, h=48):
    raw = b"".join(b"\x00" + os.urandom(w * 3) for _ in range(h))      # random pixels do not compress: the file is over the 1 KB minimum

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def item(i, name, rarity, **extra):
    return {"id": str(i), "name": name, "icon": f"https://icons.example/{i}.png", "rarity": rarity, **extra}


class FakeNet:
    """Answers the calendar, refuses every JSON file of the static hosts, and serves a PNG for every image address."""

    def __init__(self):
        self.end = NOW + 10_000
        self.fail = None          # FetchError text to raise for every request
        self.requests = []

    def calendar(self, game):
        def banner(name, kind_field, other_field, top, rest):
            return {"name": name, "version": "7.1", "state": "ACTIVE", "start_time": NOW - 1000, "end_time": self.end,
                    kind_field: top, other_field: rest}
        fields = f.ITEM_FIELDS[game]
        base = {"genshin": 10000114, "starrail": 1500, "zenless": 1640}[game]
        char = banner("" if game != "genshin" else "Character Event Wish", fields[0], fields[1],
                      [item(base, f"Hero {game}", 5, element="Cryo"), item(base + 1, "Side A", 4, element="Fire")], [])
        weap = banner("" if game != "genshin" else "Weapon Event Wish", fields[1], fields[0],
                      [item(base + 100, f"Blade {game}", 5), item(base + 101, "Spear B", 4)], [])
        return {"banners": [char, weap]}

    def http_get(self, url, limit, timeout=20):
        import json
        self.requests.append(url)
        if self.fail:
            raise f.FetchError(self.fail)
        if "/calendar" in url:
            game = url.split("/mihoyo/")[1].split("/")[0]
            return json.dumps(self.calendar(game)).encode(), "application/json"
        if url.endswith(".json"):
            raise f.FetchError("HTTP 404")
        return png(), "image/png"


class BannersCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.net = FakeNet()
        f.IMG_CACHE.clear()
        for p in (mock.patch.object(paths, "app_dir", return_value=Path(self.tmp.name)),
                  mock.patch.object(f, "http_get", self.net.http_get),
                  mock.patch.object(banners.time, "sleep")):
            p.start()
            self.addCleanup(p.stop)
        self.data = Path(self.tmp.name) / "data"

    def download_all(self, active):
        stored = None
        for ui in ("genshin", "hsr", "zzz"):
            slides, notes, problems = banners.download_game(ui, active[ui], None, "manifest: HTTP 404")
            stored = banners.store_game(stored, ui, slides, {"banners": len(active[ui]), "notes": notes, "problems": problems, "status": "ok"}, now=NOW)
        return stored


class FetcherSafetyTest(unittest.TestCase):
    def test_only_https_and_this_computer_are_fetched(self):
        for url in ("file:///etc/passwd", "ftp://example.com/x", "data:text/plain,hi", "example.com/x", "", "http://example.com/x", "http://[::1"):
            with self.assertRaises(f.FetchError, msg=url):
                f._check_address(url)
        for url in ("https://static.nanoka.cc/manifest.json", "http://127.0.0.1:8000/x", "http://localhost/x"):
            f._check_address(url)                                                      # no error
        with mock.patch.object(f._OPENER, "open") as opened:      # the opener http_get uses; a refused address must not reach it
            with self.assertRaises(f.FetchError):
                f.http_get("file:///etc/passwd", 100)
            opened.assert_not_called()

    def test_a_redirect_to_an_address_that_is_not_allowed_is_not_followed(self):
        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "ftp://127.0.0.1:1/x" if self.path == "/ftp" else "http://example.invalid/x")
                self.end_headers()

            def log_message(self, *args):
                pass

        srv = HTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with mock.patch.dict(os.environ, {"no_proxy": "127.0.0.1", "NO_PROXY": "127.0.0.1"}):
                for path in ("/ftp", "/http"):
                    with self.assertRaisesRegex(f.FetchError, "invalid address", msg=path):      # refused by the check, not a failed connection
                        f.http_get(f"http://127.0.0.1:{srv.server_port}{path}", 100, timeout=5)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_an_asset_is_written_whole_or_not_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = f.AssetStore(tmp)
            res = {"mime": "image/png", "data": b"x" * 100}
            rel = store.save("hsr", "a/../b", res)
            self.assertEqual(rel, "assets/art/hsr/a_.._b.png")                           # a name from remote data cannot leave the folder
            target = Path(tmp, *rel.split("/"))
            self.assertEqual(target.read_bytes(), b"x" * 100)
            with mock.patch("os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    store.save("hsr", "c", res)
            self.assertEqual([p.name for p in target.parent.iterdir()], ["a_.._b.png"])    # neither a half file nor a .tmp is left


class EngineTest(BannersCase):
    def test_check_active_reads_the_three_calendars(self):
        active, errors = banners.check_active(now=NOW, pause=0)
        self.assertEqual(errors, {})
        self.assertEqual({k: len(v) for k, v in active.items()}, {"genshin": 2, "hsr": 2, "zzz": 2})

    def test_offline_stops_after_the_first_game(self):
        self.net.fail = "network error: unreachable"
        active, errors = banners.check_active(now=NOW, pause=0)
        self.assertEqual(active, {"genshin": None, "hsr": None, "zzz": None})
        self.assertEqual(errors["hsr"], "no network")
        self.assertEqual(len(self.net.requests), 1)

    def test_download_stores_slides_and_files(self):
        active, _ = banners.check_active(now=NOW, pause=0)
        fractions = []
        slides, notes, problems = banners.download_game("genshin", active["genshin"], None, "manifest: HTTP 404", fractions.append)
        self.assertEqual([s["name"] for s in slides], ["Hero genshin", "Blade genshin"])
        self.assertEqual(fractions, sorted(fractions))
        self.assertEqual(fractions[-1], 1.0)
        for s in slides:
            self.assertTrue(s["icon"] and (self.data / s["icon"]).is_file(), s["icon"])
        stored = banners.store_game(None, "genshin", slides, {"status": "ok"}, now=NOW)
        self.assertTrue(banners.stored_path().is_file())
        self.assertEqual(banners.load_stored()["slides"]["genshin"][0]["id"], stored["slides"]["genshin"][0]["id"])
        self.assertIn("&data=file%3A%2F%2F%2F", banners.data_param())

    def test_nothing_stored_means_everything_is_out_of_date(self):
        active, errors = banners.check_active(now=NOW, pause=0)
        outdated, why = banners.compare(None, active, errors, now=NOW)
        self.assertEqual(outdated, {"genshin": True, "hsr": True, "zzz": True})
        self.assertEqual(banners.data_param(), "")

    def test_up_to_date_then_each_kind_of_change(self):
        active, errors = banners.check_active(now=NOW, pause=0)
        stored = self.download_all(active)
        outdated, why = banners.compare(stored, active, errors, now=NOW + 60)
        self.assertEqual(outdated, {"genshin": False, "hsr": False, "zzz": False}, why)

        gone = self.data / stored["slides"]["hsr"][0]["icon"]           # a stored file was deleted
        gone.unlink()
        outdated, why = banners.compare(stored, active, errors, now=NOW + 60)
        self.assertEqual(outdated, {"genshin": False, "hsr": True, "zzz": False})
        self.assertIn("missing", why["hsr"])

        self.net.end += 7 * 86400                                       # the banners were replaced by new ones
        active2, _ = banners.check_active(now=NOW, pause=0)
        outdated, why = banners.compare(stored, active2, errors, now=NOW + 60)
        self.assertEqual(outdated, {"genshin": True, "hsr": True, "zzz": True})
        self.assertIn("changed", why["zzz"])

    def test_calendar_not_available_keeps_the_stored_data(self):
        active, errors = banners.check_active(now=NOW, pause=0)
        stored = self.download_all(active)
        self.net.fail = "network error: x"
        active2, errors2 = banners.check_active(now=NOW, pause=0)
        outdated, why = banners.compare(stored, active2, errors2, now=NOW)
        self.assertEqual(outdated, {"genshin": False, "hsr": False, "zzz": False})
        self.assertIn("calendar not available", why["genshin"])

    def test_images_that_failed_are_retried_after_six_hours_only(self):
        active, errors = banners.check_active(now=NOW, pause=0)
        stored = self.download_all(active)
        for s in stored["slides"]["zzz"]:
            s["art"] = None                                             # this download failed
            s["artRemote"] = "https://art.example/x.webp"
        soon = banners.compare(stored, active, errors, now=NOW + 3600)[0]
        later = banners.compare(stored, active, errors, now=NOW + 7 * 3600)[0]
        self.assertFalse(soon["zzz"])
        self.assertTrue(later["zzz"])

    def test_main_page_url_reads_the_data_folder_once_there_is_data(self):
        self.assertTrue(window.page_url().endswith("/index.html#backend=1"))
        active, _ = banners.check_active(now=NOW, pause=0)
        self.download_all(active)
        url = window.page_url()
        self.assertIn("#backend=1&data=file%3A%2F%2F%2F", url)
        self.assertTrue(url.endswith("%2Fdata%2F"), url)
        self.assertNotIn("?", url)


class TasksTest(BannersCase):
    def run_tasks(self):
        quick = [replace(t, min_seconds=0) for t in loader.default_tasks() if t.id != "open"]
        ctx = Context()
        results = Loader(quick, lambda *a: None, ctx).run()
        return results, ctx

    def test_first_start_downloads_everything_second_start_downloads_nothing(self):
        results, ctx = self.run_tasks()
        self.assertEqual(results, {"banners": "ok", "stored": "ok", "fresh": "ok", "assets_genshin": "ok", "assets_hsr": "ok", "assets_zzz": "ok", "sync": "ok"})
        self.assertEqual(ctx.state["outdated"], {"genshin": True, "hsr": True, "zzz": True})
        self.assertEqual({k: len(v) for k, v in banners.load_stored()["slides"].items()}, {"genshin": 2, "hsr": 2, "zzz": 2})

        before = len(self.net.requests)
        results, ctx = self.run_tasks()
        self.assertEqual({k: v for k, v in results.items() if k.startswith("assets")}, {"assets_genshin": "skipped", "assets_hsr": "skipped", "assets_zzz": "skipped"})
        self.assertEqual(ctx.state["outdated"], {"genshin": False, "hsr": False, "zzz": False})
        self.assertEqual(len(self.net.requests) - before, 3)             # only the three calendars

    def test_one_game_changed_only_that_game_downloads(self):
        self.run_tasks()
        stored = banners.load_stored()
        stored["slides"]["hsr"][0]["end"] = "2026-01-01T00:00:00Z"        # the stored HSR banner is not the active one
        banners.store_game(stored, "hsr", stored["slides"]["hsr"], {"status": "ok"}, now=NOW)
        results, ctx = self.run_tasks()
        self.assertEqual({k: v for k, v in results.items() if k.startswith("assets")}, {"assets_genshin": "skipped", "assets_hsr": "ok", "assets_zzz": "skipped"})

    def test_offline_start_uses_what_is_stored_and_still_opens(self):
        self.run_tasks()
        self.net.fail = "network error: x"
        results, ctx = self.run_tasks()
        self.assertTrue(all(v in ("ok", "skipped") for v in results.values()), results)
        self.assertEqual({k: v for k, v in results.items() if k.startswith("assets")}, {"assets_genshin": "skipped", "assets_hsr": "skipped", "assets_zzz": "skipped"})

    def test_a_stored_file_that_is_valid_but_not_of_the_expected_shape_is_replaced_at_the_next_start(self):
        stored_file = banners.stored_path()
        stored_file.parent.mkdir(parents=True)
        for text in ('{"slides": 5}', '{"slides": {"genshin": 7}}', '{"slides": {"genshin": [1]}}', '{"slides": {"hsr": [{"rateUp": 3}]}}',
                     '{"slides": {}, "games": []}'):
            stored_file.write_text("window.PT_BANNERS = " + text + ";\n", encoding="utf-8")
            self.assertIsNone(banners.load_stored(), text)
            self.assertEqual(banners.data_param(), "", text)                  # the page uses the bundled snapshot meanwhile
            results, ctx = self.run_tasks()                                    # no task fails on it, and the data is written again
            self.assertEqual(set(results.values()), {"ok"}, (text, results))
            self.assertEqual({k: len(v) for k, v in banners.load_stored()["slides"].items()}, {"genshin": 2, "hsr": 2, "zzz": 2}, text)

    def test_first_start_offline_still_opens(self):
        self.net.fail = "network error: x"
        results, ctx = self.run_tasks()
        self.assertTrue(all(v in ("ok", "skipped") for v in results.values()), results)
        self.assertEqual(banners.data_param(), "")                         # nothing stored: the page uses the bundled snapshot


if __name__ == "__main__":
    unittest.main()
