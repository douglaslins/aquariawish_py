"""Tests of finding the saved link, the paged requests and the sync. A fake service answers like the game's history service (made-up
pulls, no network); the cache files are made up too."""
from __future__ import annotations

import http.client
import json
import logging
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.pop("PULLS_TRACKER_DATA", None)      # a test must never touch a real data folder: each test that needs one sets its own

from pt import banners as banners_mod, client as client_mod, locate, sync, vault                                   # noqa: E402
from pt.client import Cancelled, Client, LinkExpired, NetworkError, RateLimited      # noqa: E402
from pt.api import PullsApi                                                          # noqa: E402
from pt.games import GENSHIN, HSR, ZZZ                                        # noqa: E402
from pt.store import Store                                                           # noqa: E402
from pt.tracker import Tracker                                                       # noqa: E402
from test_backend import Base, pity_plan                                             # noqa: E402

_logs_patch = None


def setUpModule():
    """No test looks at the log files or caches of a game installed on the computer that runs the tests."""
    global _logs_patch
    _logs_patch = mock.patch.object(locate, "local_low", lambda: Path(tempfile.gettempdir()) / "pt_tests_no_such_logs")
    _logs_patch.start()


def tearDownModule():
    _logs_patch.stop()


KEY = "GOODKEY%2Babc%3D"                         # as it is in the file: percent-encoded, sent back unchanged
OLD_KEY = "OLDKEY%2Fxyz%3D%3D"
T0 = 1788220800


def api_url(game, key=KEY, host=None, extra="", lang="pt"):
    host = host or game.hosts[0]
    return (f"https://{host}{game.api_path}?authkey_ver=1&sign_type=2&auth_appid=webview_gacha&win_mode=fullscreen&lang={lang}&region=os_asia"
            f"&game_biz={game.biz}&authkey={key}&gacha_type={game.groups[0].request}&page=1&size=5&end_id=0{extra}")


def write_cache(root: Path, game, urls, noise=True) -> Path:
    """A cache file: binary noise, then each address as a cache key (`1/0/<address>` and a NUL), oldest address first."""
    folder = {"genshin": "GenshinImpact_Data", "hsr": "StarRail_Data", "zzz": "ZenlessZoneZero_Data"}[game.key]
    f = root / folder / "webCaches" / "2.1.0.0" / "Cache" / "Cache_Data" / "data_2"
    f.parent.mkdir(parents=True, exist_ok=True)
    body = b"\x00\x01\xff\xfe" * 50 if noise else b""
    for u in urls:
        body += b"1/0/" + u.encode() + b"\x00" + bytes(range(1, 40)) + b"\xc3\x28"
    f.write_bytes(body)
    return f


class FakeService:
    """The history service: items newest first per gacha_type, `valid` keys, a request log, and ways to fail."""

    def __init__(self, game, uid="800000001", tz=8):
        self.game, self.uid, self.tz = game, uid, tz
        self.valid = {KEY}
        self.history: dict[str, list[dict]] = {}
        self.requests: list[dict] = []
        self.next_id = 1_700_000_000_000_000_000
        self.fail = None                   # callable(request dict) -> answer or None
        self.lang_refused = False
        self.hosts = None                  # when set, the only hostnames that exist: any other one answers HTTP 404, like a host that is not the service

    def add(self, code, ranks, t0=T0, names=None, codes=None, new=False):
        """Add pulls (oldest first) to a banner type. `new`: they are newer than the ones there."""
        items = []
        for i, r in enumerate(ranks):
            self.next_id += 10
            t = t0 + i * 60
            items.append({"uid": self.uid, "gacha_type": (codes[i % len(codes)] if codes else code), "item_id": str(1000 + i), "count": "1",
                          "time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t + self.tz * 3600)), "name": (names or {}).get(i) or f"Item {r}-{i}",
                          "item_type": "Character", "rank_type": str(r), "id": str(self.next_id), "lang": "en-us", "_t": t})
        self.history[code] = list(reversed(items)) + (self.history.get(code, []) if new else [])
        if not new:
            self.history[code] = list(reversed(items))

    def http(self, url, timeout):
        parts = urlsplit(url)
        raw = dict(p.partition("=")[::2] for p in parts.query.split("&"))
        req = {"host": parts.hostname, "path": parts.path, "code": raw.get("gacha_type"), "page": int(raw.get("page", 0)), "size": int(raw.get("size", 20)),
               "end_id": raw.get("end_id"), "key": raw.get("authkey"), "lang": raw.get("lang"), "raw": raw}
        self.requests.append(req)
        if self.fail:
            answer = self.fail(req)
            if answer is not None:
                return answer
        if parts.path != self.game.api_path or (self.hosts is not None and parts.hostname not in self.hosts):
            return 404, b""
        if req["key"] not in self.valid:
            return 200, json.dumps({"retcode": -101, "message": "authkey timeout", "data": None}).encode()
        if self.lang_refused and req["lang"] == "en":
            return 200, json.dumps({"retcode": -100, "message": "authkey error", "data": None}).encode()
        for extra in self.game.request_extra:
            assert raw.get(extra) == req["code"], f"{extra} must repeat gacha_type"
        if req["code"] not in self.history and req["code"] in self.game.extra_codes:
            return 200, json.dumps({"retcode": -1, "message": "invalid gacha type", "data": None}).encode()
        pool = [dict(x) for x in self.history.get(req["code"], [])]
        end = int(req["end_id"] or 0)
        pool = [x for x in pool if not end or int(x["id"]) < end][: req["size"]]
        for x in pool:
            x.pop("_t")
        data = {"page": str(req["page"]), "size": str(req["size"]), "total": "0", "list": pool, "region": "os_asia", "region_time_zone": self.tz}
        return 200, json.dumps({"retcode": 0, "message": "OK", "data": data}).encode()


class LocateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_data_folder_from_each_log(self):
        g = "[Subsystems] Discovering subsystems at path D:/Games/ZenlessZoneZero Game/ZenlessZoneZero_Data/UnitySubsystems"
        self.assertEqual(locate.data_dir_from_log("zzz", "Unity 2022\n" + g + "\nmore"), Path("D:/Games/ZenlessZoneZero Game/ZenlessZoneZero_Data"))
        h = "Loading player data from D:/Games/Star Rail/Games/StarRail_Data/data.unity3d"
        self.assertEqual(locate.data_dir_from_log("hsr", "x\n" + h), Path("D:/Games/Star Rail/Games/StarRail_Data"))
        gi = "Warmup file C:/Program Files/Genshin Impact/Genshin Impact game/GenshinImpact_Data/StreamingAssets/x.ab\n"
        self.assertEqual(locate.data_dir_from_log("genshin", "[Log]\n" + gi), Path("C:/Program Files/Genshin Impact/Genshin Impact game/GenshinImpact_Data"))
        cn = "Warmup file E:\\Games\\Genshin\\YuanShen_Data\\Persistent\\a\n"
        self.assertEqual(locate.data_dir_from_log("genshin", cn), Path("E:\\Games\\Genshin\\YuanShen_Data"))
        self.assertIsNone(locate.data_dir_from_log("hsr", "nothing here"))

    def test_the_folder_chosen_in_settings_is_searched_below(self):
        f = write_cache(self.root / "Games" / "Genshin Impact game", GENSHIN, [api_url(GENSHIN)])
        self.assertEqual(locate.cache_files("genshin", str(self.root / "Games")), [f])
        self.assertEqual(locate.cache_files("genshin", str(self.root / "Games" / "Genshin Impact game" / "GenshinImpact_Data")), [f])
        self.assertEqual(locate.cache_files("genshin", str(self.root / "nowhere")), [])
        self.assertEqual(locate.game_folder("genshin", str(self.root / "Games")), self.root / "Games")

    def test_the_folder_from_the_log_is_used_and_the_newest_cache_file_comes_first(self):
        data_dir = self.root / "Star Rail" / "StarRail_Data"
        old = write_cache(self.root / "Star Rail", HSR, [api_url(HSR)])
        newer = data_dir / "webCaches" / "2.2.0.0" / "Cache" / "Cache_Data" / "data_2"
        newer.parent.mkdir(parents=True)
        newer.write_bytes(old.read_bytes())
        os.utime(old, (1000, 1000))
        logs = self.root / "LocalLow" / "Cognosphere" / "Star Rail"
        logs.mkdir(parents=True)
        (logs / "Player.log").write_text(f"Unity\nLoading player data from {data_dir.as_posix()}/data.unity3d\n", encoding="utf-8")
        self.assertEqual(locate.cache_files("hsr", "", self.root / "LocalLow"), [newer, old])
        self.assertEqual(locate.game_folder("hsr", "", self.root / "LocalLow"), data_dir)

    def test_a_settings_folder_without_a_web_cache_does_not_hide_the_folder_the_log_names(self):
        data_dir = self.root / "Star Rail" / "StarRail_Data"
        write_cache(self.root / "Star Rail", HSR, [api_url(HSR)])
        logs = self.root / "home" / "AppData" / "LocalLow" / "Cognosphere" / "Star Rail"
        logs.mkdir(parents=True)
        (logs / "Player.log").write_text(f"Unity\nLoading player data from {data_dir.as_posix()}/data.unity3d\n", encoding="utf-8")
        empty = self.root / "Empty"
        empty.mkdir()
        with mock.patch.object(locate, "local_low", lambda: self.root / "home" / "AppData" / "LocalLow"):      # the logs the program finds by itself
            self.assertEqual(locate.game_folder("hsr", str(empty)), data_dir)

    def test_links_are_read_from_bytes_newest_first_with_the_key_kept_as_it_is(self):
        other = "https://evil.example.com/gacha?authkey=EVIL&game_biz=hk4e_global"
        page = f"https://webstatic-sea.hoyoverse.com/genshin/event/e20190909gacha-v3/index.html?authkey_ver=1&lang=en&authkey={OLD_KEY}&game_biz=hk4e_global#/log"
        wrong_game = api_url(HSR, key="HSRKEY")
        f = write_cache(self.root, GENSHIN, [page, other, wrong_game, api_url(GENSHIN)])
        links = locate.candidate_links(f.read_bytes(), GENSHIN)
        self.assertEqual([lk.value("authkey") for lk in links], [KEY, OLD_KEY])           # the evil host and the other game are skipped
        self.assertEqual(links[1].path, "/genshin/event/e20190909gacha-v3/index.html")
        self.assertFalse(links[1].api_like())
        self.assertTrue(links[0].api_like())
        self.assertNotIn("#", links[1].raw)

    def test_request_url_changes_only_what_it_should(self):
        link = locate.candidate_links(api_url(GENSHIN).encode() + b"\x00", GENSHIN)[0]
        url = locate.request_url(GENSHIN, link, "h.hoyoverse.com", {"gacha_type": "302", "size": "20", "page": "3", "end_id": "99", "lang": "en"})
        q = url.split("?", 1)[1].split("&")
        self.assertTrue(url.startswith("https://h.hoyoverse.com/gacha_info/api/getGachaLog?"))
        self.assertIn(f"authkey={KEY}", q)                         # not decoded and encoded again
        self.assertIn("lang=en", q)
        self.assertNotIn("lang=pt", q)
        self.assertEqual([p for p in q if p.startswith(("gacha_type", "page", "size", "end_id"))], ["gacha_type=302", "size=20", "page=3", "end_id=99"])
        self.assertIn("auth_appid=webview_gacha", q)
        no_biz = locate.Link("x", "a.hoyoverse.com", "/x", ("authkey=K",))
        self.assertIn("game_biz=nap_global", locate.request_url(ZZZ, no_biz, "a.hoyoverse.com", {}))
        self.assertEqual(locate.hosts_for(GENSHIN, link)[0], GENSHIN.hosts[0])
        page_link = locate.Link("x", "webstatic-sea.hoyoverse.com", "/index.html", ("authkey=K",))
        self.assertEqual(locate.hosts_for(GENSHIN, page_link), list(GENSHIN.hosts))
        self.assertNotIn(KEY, locate.describe(link))

    def test_a_locked_cache_file_is_read_from_a_copy(self):
        f = write_cache(self.root, GENSHIN, [api_url(GENSHIN)])
        real = Path.read_bytes
        calls = []

        def locked(self):
            calls.append(self)
            if self == f:
                raise PermissionError("in use")
            return real(self)
        with mock.patch.object(Path, "read_bytes", locked), mock.patch("pt.locate.shutil.copyfile", lambda s, d: Path(d).write_bytes(b"copied")):
            self.assertEqual(locate.read_cache(f), b"copied")


class CacheOrderTest(unittest.TestCase):
    def test_a_cache_file_that_vanished_sorts_last_instead_of_raising(self):
        self.assertEqual(locate._mtime(Path("/no/such/folder/data_2")), 0.0)


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.svc = FakeService(GENSHIN)
        self.svc.add("301", [3] * 45 + [5] + [3] * 4)                 # 50 pulls = 3 pages
        self.link = locate.candidate_links(api_url(GENSHIN).encode() + b"\x00", GENSHIN)[0]
        self.sleeps: list[float] = []

    def client(self, **kw):
        return Client(GENSHIN, self.link, GENSHIN.hosts[0], http=self.svc.http, sleep=self.sleeps.append, **kw)

    def test_pages_are_read_newest_first_until_the_list_is_empty(self):
        got = [(n, [i["id"] for i in items]) for n, items, _ in self.client().pages("301")]
        self.assertEqual([n for n, _ in got], [1, 2, 3])
        self.assertEqual([len(x) for _, x in got], [20, 20, 10])
        ids = [i for _, x in got for i in x]
        self.assertEqual(ids, sorted(ids, reverse=True))
        self.assertEqual(len(set(ids)), 50)
        reqs = [r for r in self.svc.requests if r["code"] == "301"]
        self.assertEqual([r["page"] for r in reqs], [1, 2, 3, 4])        # the fourth one is the empty page
        self.assertEqual([r["end_id"] for r in reqs][:2], ["0", got[0][1][-1]])
        self.assertEqual(self.sleeps, [0.6, 0.6, 0.6])                  # between pages, not after the last one
        self.assertTrue(all(r["key"] == KEY for r in reqs))
        self.assertEqual({r["lang"] for r in reqs}, {"en"})              # the link's language is pt: English is asked for

    def check_with(self, answers, link=None):
        """A client whose http gives `answers` one after the other (an exception is raised, a (status, body) is returned); the last one is
        repeated. The addresses it requested are in self.http_calls."""
        calls = []

        def http(url, timeout):
            calls.append(url)
            a = answers[min(len(calls), len(answers)) - 1]
            if isinstance(a, Exception):
                raise a
            return a
        self.http_calls = calls
        return Client(GENSHIN, link or self.link, GENSHIN.hosts[0], http=http, sleep=self.sleeps.append)

    def test_check_tries_again_after_a_lost_request_or_a_server_fault(self):
        good = self.svc.http(locate.request_url(GENSHIN, self.link, GENSHIN.hosts[0], {"gacha_type": "301", "size": "1", "page": "1", "end_id": "0"}), 5)
        for first in (NetworkError("the server did not answer in time"), (503, b"")):
            with self.subTest(first=repr(first)):
                self.sleeps.clear()
                self.check_with([first, good]).check()
                self.assertEqual(len(self.http_calls), 2)
                self.assertEqual(self.sleeps, [client_mod.CHECK_RETRY_PAUSE])

    def test_check_gives_up_after_its_tries_and_does_not_repeat_a_final_answer(self):
        with self.assertRaises(NetworkError):
            self.check_with([NetworkError("no route")]).check()
        self.assertEqual(len(self.http_calls), client_mod.CHECK_TRIES)
        english = locate.candidate_links(api_url(GENSHIN, lang="en").encode() + b"\x00", GENSHIN)[0]      # (no language override, which would add a request of its own)
        for answer, problem in (((404, b""), client_mod.ApiError),
                                ((200, json.dumps({"retcode": -101, "message": "authkey timeout", "data": None}).encode()), LinkExpired),
                                ((200, json.dumps({"retcode": -110, "message": "visit too frequently", "data": None}).encode()), RateLimited)):
            with self.subTest(problem=problem.__name__):
                with self.assertRaises(problem):
                    self.check_with([answer], english).check()
                self.assertEqual(len(self.http_calls), 1)               # asking again would give the same answer

    def test_english_link_language_is_left_alone(self):
        link = locate.candidate_links(api_url(GENSHIN, lang="en").encode() + b"\x00", GENSHIN)[0]
        c = Client(GENSHIN, link, GENSHIN.hosts[0], http=self.svc.http, sleep=self.sleeps.append)
        list(c.pages("301"))
        self.assertEqual({r["lang"] for r in self.svc.requests}, {"en"})
        self.assertTrue(c.names_english)

    def test_a_refused_language_falls_back_to_the_link_language(self):
        self.svc.lang_refused = True
        c = self.client()
        self.assertEqual(sum(len(i) for _, i, _ in c.pages("301")), 50)
        self.assertFalse(c.names_english)
        self.assertEqual(self.svc.requests[-1]["lang"], "pt")

    def test_zzz_repeats_the_code(self):
        svc = FakeService(ZZZ)
        svc.add("2", [4, 3, 2])
        link = locate.candidate_links(api_url(ZZZ).encode() + b"\x00", ZZZ)[0]
        c = Client(ZZZ, link, ZZZ.hosts[0], http=svc.http, sleep=lambda s: None)
        self.assertEqual(sum(len(i) for _, i, _ in c.pages("2")), 3)          # the fake service asserts real_gacha_type

    def test_expired_key(self):
        self.svc.valid = set()
        with self.assertRaises(LinkExpired):
            self.client().check()

    def test_the_messages_that_say_the_key_has_expired(self):
        """Genshin says "authkey timeout", Zenless says "auth key time out" and does not have to use the codes -100 and -101 for it."""
        def answer(code, message):
            return 200, json.dumps({"retcode": code, "message": message, "data": None}).encode()
        for code, message in ((-101, "authkey timeout"), (-100, "authkey error"), (-1, "auth key time out"), (10001, "Auth Key Time Out"), (-1, "AuthKey invalid")):
            with self.subTest(message=message):
                with self.assertRaises(LinkExpired):
                    client_mod.interpret(*answer(code, message))
        for code, message in ((-1, "invalid gacha type"), (-1, "authorization failed"), (500, "")):          # other answers are not about the key
            with self.subTest(message=message):
                with self.assertRaises(client_mod.ApiError):
                    client_mod.interpret(*answer(code, message))

    def test_rate_limit_waits_then_goes_on(self):
        hits = {"n": 0}

        def fail(req):
            if req["page"] == 2 and hits["n"] < 2:
                hits["n"] += 1
                return 200, json.dumps({"retcode": -110, "message": "visit too frequently", "data": None}).encode()
        self.svc.fail = fail
        waits = []
        got = sum(len(i) for _, i, _ in self.client().pages("301", waits.append))
        self.assertEqual(got, 50)
        self.assertEqual(waits, [{"sec": 15, "retry": 1, "of": 5, "stop": False}, {"sec": 15, "retry": 2, "of": 5, "stop": False}])
        self.assertEqual([s for s in self.sleeps if s == 15.0], [15.0, 15.0])

    def test_rate_limit_that_does_not_end_stops_after_five_waits(self):
        self.svc.fail = lambda req: (200, json.dumps({"retcode": 5, "message": "Visit too frequently", "data": None}).encode())
        waits = []
        with self.assertRaises(RateLimited):
            list(self.client().pages("301", waits.append))
        self.assertEqual([w["retry"] for w in waits], [1, 2, 3, 4, 5])
        self.assertTrue(waits[-1]["stop"])

    def test_network_errors_are_retried_twice(self):
        n = {"c": 0}

        def flaky(url, timeout):
            n["c"] += 1
            if n["c"] <= 2:
                raise NetworkError("down")
            return self.svc.http(url, timeout)
        c = Client(GENSHIN, self.link, GENSHIN.hosts[0], http=flaky, sleep=self.sleeps.append)
        self.assertEqual(sum(len(i) for _, i, _ in c.pages("301")), 50)
        n["c"] = -10
        with self.assertRaises(NetworkError):
            list(Client(GENSHIN, self.link, GENSHIN.hosts[0], http=lambda u, t: (_ for _ in ()).throw(NetworkError("down")), sleep=lambda s: None).pages("301"))

    def test_other_answers(self):
        for status, body in ((500, b""), (200, b"not json"), (200, b"[1]"), (200, json.dumps({"retcode": 7, "message": "boom"}).encode())):
            with self.assertRaises(client_mod.ApiError):
                client_mod.interpret(status, body)

    def test_cancel_stops_between_pages(self):
        cancel = threading.Event()
        c = Client(GENSHIN, self.link, GENSHIN.hosts[0], http=self.svc.http, sleep=lambda s: None, cancel=cancel)
        it = c.pages("301")
        next(it)
        cancel.set()
        with self.assertRaises(Cancelled):
            next(it)

    def _answering(self, data):
        c = self.client()
        c.http = lambda url, timeout: (200, json.dumps({"retcode": 0, "message": "OK", "data": data}).encode())
        return c

    def test_a_service_that_sends_the_same_page_again_is_stopped(self):
        page = [{"id": "500", "gacha_type": "301", "item_type": "Character", "rank_type": "3", "name": "x", "time": "2024-01-01 00:00:00", "uid": "1"}]
        got = []
        with self.assertRaises(client_mod.ApiError):
            for n, _, _ in self._answering({"list": page}).pages("301"):
                got.append(n)
                self.assertLess(n, 10, "the loop did not stop")
        self.assertEqual(got, [1, 2])                                   # the repeat is noticed when the second page ends

    def test_a_list_that_is_not_a_list_of_items_is_an_api_error(self):
        for bad in ({"list": {"a": 1}}, {"list": [1, 2]}, {"list": ["x"]}):
            with self.assertRaises(client_mod.ApiError):
                list(self._answering(bad).pages("301"))

    def test_only_https_is_requested(self):
        for url in ("http://example.invalid/", "file:///etc/passwd", "ftp://example.invalid/"):
            with self.assertRaises(NetworkError):
                client_mod.urllib_get(url, 1)

    def test_an_answer_that_is_cut_off_is_a_network_error_that_is_tried_again(self):
        # http.client's own exceptions are not OSErrors: left as they are, the request would not be tried again and the screen would say "internal"
        class Answer:
            status, headers = 200, {"Content-Length": "100"}

            def __init__(self, read):
                self._read = read

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, n):
                return self._read()

        def cut():
            raise http.client.IncompleteRead(b"")
        for opened in (mock.Mock(side_effect=http.client.BadStatusLine("x")), mock.Mock(return_value=Answer(cut)),
                       mock.Mock(return_value=Answer(lambda: b'{"retcode":'))):         # the last one: 11 of the 100 bytes the header promised
            with mock.patch.object(client_mod._OPENER, "open", opened), self.assertRaises(NetworkError):
                client_mod.urllib_get("https://example.invalid/x", 1)
        with mock.patch.object(client_mod._OPENER, "open", mock.Mock(return_value=Answer(lambda: b"x" * 100))):
            self.assertEqual(client_mod.urllib_get("https://example.invalid/x", 1), (200, b"x" * 100))      # a whole answer still goes through

    def test_a_redirect_is_not_followed(self):
        import http.server
        hits = []

        class Redirecting(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
                self.end_headers()

            def log_message(self, *args):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), Redirecting)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with mock.patch.dict(os.environ, {"no_proxy": "127.0.0.1", "NO_PROXY": "127.0.0.1"}):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    client_mod._OPENER.open(urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/start"), timeout=5)
            self.assertEqual(caught.exception.code, 302)
            caught.exception.close()
            self.assertEqual(hits, ["/start"])                           # /elsewhere was never asked for
        finally:
            srv.shutdown()
            srv.server_close()


class SyncBase(Base):
    game = GENSHIN

    def setUp(self):
        super().setUp()
        self.games_root = self.root / "Games"
        self.svc = FakeService(self.game)
        self.cache = write_cache(self.games_root, self.game, [api_url(self.game, OLD_KEY), api_url(self.game)])
        self.t = self.tracker()
        s = self.t.settings()
        s["folders"][self.game.key] = str(self.games_root)
        self.t.save_settings(s)
        self.events: list[dict] = []
        self.logs: list[str] = []
        h = logging.Handler()
        h.emit = lambda r: self.logs.append(r.getMessage())
        logging.getLogger("pt").addHandler(h)
        logging.getLogger("pt").setLevel(logging.DEBUG)
        self.addCleanup(logging.getLogger("pt").removeHandler, h)

    def syncer(self, game=None, **kw):
        return sync.Syncer(self.t, (game or self.game).key, self.events.append, http=self.svc.http, sleep=lambda s: None,
                           clock=lambda: self.now, **kw)

    def run_sync(self, **kw):
        self.events.clear()
        return self.syncer(**kw).run()

    def steps(self):
        return [(e["n"], e["state"]) for e in self.events if e["type"] == "step"]

    def error(self):
        return next((e for e in self.events if e["type"] == "error"), None)


class GenshinSyncTest(SyncBase):
    def setUp(self):
        super().setUp()
        self.svc.add("301", pity_plan(5, [30, 50], 12), names={29: "Mona", 79: "Skirk"}, codes=["301", "400"])
        self.svc.add("302", pity_plan(5, [20], 4), t0=T0 + 5)
        self.svc.add("200", [3, 4, 3], t0=T0 + 9)
        self.svc.add("100", [3, 3, 4], t0=T0 + 11)

    def test_first_sync(self):
        res = self.run_sync()
        self.assertIsNone(self.error())
        types = [e["type"] for e in self.events]
        self.assertEqual(types[-1], "done")
        self.assertEqual(self.steps(), [(1, "running"), (1, "ok"), (2, "running"), (2, "ok"), (3, "running"), (3, "ok"), (4, "running"), (4, "ok")])
        self.assertEqual(res["added"]["total"], 92 + 24 + 3 + 3)
        self.assertEqual({g["key"]: g["n"] for g in res["added"]["groups"]}, {"character": 92, "weapon": 24, "standard": 3, "novice": 3})
        self.assertEqual((res["stored"], res["new5"]), (0, 3))       # two on the character banner, one on the weapon banner
        self.assertEqual(res["accountCheck"], "First account of this game, added")
        # the first address in the file (the newest) is the valid one: found after one test
        step2 = next(e for e in self.events if e["type"] == "step" and e["n"] == 2 and e["state"] == "ok")
        self.assertEqual(step2["text"], "1 of 2 candidates tested")
        self.assertIn("cache file changed", next(e for e in self.events if e["type"] == "step" and e["n"] == 1 and e["state"] == "ok")["text"])
        acct = self.t.store.active("genshin")
        self.assertEqual((acct["uid"], acct["region"], acct["utc_offset"], acct["lang"], acct["last_sync"]), ("800000001", "Asia", 8, "en-us", self.now))
        self.assertEqual(self.t.store.count("genshin", "800000001"), 92 + 24 + 3 + 3)
        fetch = [e for e in self.events if e["type"] == "fetch"]
        self.assertEqual([e["group"] for e in fetch if e["done"]], ["character", "weapon", "standard", "novice"])
        self.assertEqual(fetch[0]["page"], 1)
        self.assertIn("Item 5-", self.t.history("genshin", {"rarity": "5"})["rows"][0]["name"] + "Item 5-")

    def test_one_lost_request_at_the_first_address_does_not_stop_the_sync(self):
        real, lost = self.svc.http, []

        def flaky(url, timeout):
            if urlsplit(url).hostname != GENSHIN.hosts[0]:
                raise NetworkError("no route")                      # the other host (the one in China) does not answer from here
            if not lost:
                lost.append(url)
                raise NetworkError("the server did not answer in time")       # the first request to the right host is lost
            return real(url, timeout)
        self.svc.http = flaky
        res = self.run_sync()
        self.assertIsNone(self.error())
        self.assertEqual(res["added"]["total"], 92 + 24 + 3 + 3)
        self.assertIn((2, "ok"), self.steps())

    def test_a_failed_step_2_writes_what_happened_to_the_log_without_the_key(self):
        self.cache.write_bytes(self.cache.read_bytes().replace(KEY.encode(), OLD_KEY.encode()))        # every saved link is the expired one
        self.svc.fail = lambda req: (200, json.dumps({"retcode": -101, "message": "authkey timeout", "data": None}).encode())
        self.run_sync()
        self.assertEqual(self.error()["code"], "link_expired")
        self.assertTrue(any("no saved link was accepted: 1 tested, 1 expired, 0 without an answer" in line for line in self.logs), self.logs)
        self.assertTrue(any("saved links (testing 1)" in line for line in self.logs))
        self.assertFalse([line for line in self.logs if KEY in line or OLD_KEY in line])

    def test_an_expired_key_in_zenless_words_is_one_answer_per_address_and_a_clear_error(self):
        """The friend's log: "auth key time out" was not known as an expired key, so every address was asked on every host and the error was generic."""
        self.svc.fail = lambda req: (200, json.dumps({"retcode": 10001, "message": "auth key time out", "data": None}).encode())
        self.run_sync()
        self.assertEqual(self.error()["code"], "link_expired")
        self.assertEqual([(r["key"], r["lang"]) for r in self.svc.requests], [(KEY, "en"), (KEY, "pt"), (OLD_KEY, "en"), (OLD_KEY, "pt")])
        self.assertTrue(any("no saved link was accepted: 2 tested, 2 expired, 0 without an answer" in line for line in self.logs), self.logs)

    def test_the_log_tells_what_the_service_sent_for_each_banner_type(self):
        self.svc.history["200"] = []                       # a banner type without pulls
        self.run_sync()
        self.assertTrue(any("the service sent 92 pulls in 5 pages" in line and "92 are new" in line and "newest made" in line for line in self.logs), self.logs)
        self.assertTrue(any("the service sent no pulls" in line for line in self.logs), self.logs)
        self.run_sync()                                    # the second sync reads only up to the first page that holds a stored pull
        self.assertTrue(any("the service sent 20 pulls in 1 pages" in line and "0 are new" in line for line in self.logs), self.logs)
        self.assertFalse([line for line in self.logs if KEY in line or OLD_KEY in line])

    def test_times_are_stored_in_utc(self):
        self.run_sync()
        first = self.t.store.pulls("genshin", "800000001", ["301", "400"])[0]
        self.assertEqual(first["time"], T0)                 # the service gave server time (UTC+8); stored as UTC

    def test_pity_and_results_after_a_sync(self):
        self.run_sync()
        h = self.t.history("genshin", {"group": "character", "rarity": "5", "sort": {"col": "time", "dir": "asc"}})
        self.assertEqual([(r["name"], r["pity"]) for r in h["rows"]], [("Mona", 30), ("Skirk", 50)])
        self.assertEqual(self.t.dashboard("genshin")["groups"][0]["pity"], 12)

    def test_the_key_is_never_logged(self):
        self.run_sync()
        self.assertTrue(self.logs)
        for line in self.logs:
            self.assertNotIn("GOODKEY", line)
            self.assertNotIn("OLDKEY", line)

    def test_second_sync_reads_only_until_a_stored_pull(self):
        self.run_sync()
        self.svc.requests.clear()
        self.svc.add("301", [3, 3, 5], t0=T0 + 86400, new=True)
        res = self.run_sync()
        self.assertEqual(res["added"]["total"], 3)
        self.assertEqual(res["new5"], 1)
        self.assertGreater(res["stored"], 0)
        by_code: dict[str, int] = {}
        for r in self.svc.requests:
            if r["page"] > 1 or r["key"] == KEY:
                by_code[r["code"]] = by_code.get(r["code"], 0) + 1
        self.assertLessEqual(by_code["301"], 3)                # the check, then the one page that reaches a stored pull
        self.assertEqual(self.t.store.count("genshin", "800000001"), 122 + 3)
        again = self.run_sync()
        self.assertEqual(again["added"]["total"], 0)

    def test_a_banner_type_that_was_never_completed_is_read_to_the_end(self):
        self.run_sync()
        self.t.store.set_meta(sync.full_key("genshin", "800000001", "character"), "0")
        self.svc.requests.clear()
        self.run_sync()
        self.assertEqual(len([r for r in self.svc.requests if r["code"] == "301" and r["key"] == KEY and r["size"] == 20]), 6)    # 5 pages (92 pulls) and the empty one

    def test_a_uid_that_is_not_a_number_is_refused_before_anything_is_stored(self):
        self.svc.history.clear()
        self.svc.uid = "../evil"                                         # the UID is copied into each item when it is added
        self.svc.add("301", [3, 4, 5])
        self.run_sync()
        self.assertIn("not a number", self.error()["message"])
        self.assertEqual(self.t.store.accounts("genshin"), [])
        self.assertEqual(self.t.store.count("genshin", "../evil"), 0)

    def test_another_uid_is_stopped_before_anything_is_stored(self):
        self.add(GENSHIN, [], uid="600000009")
        self.t.store.ensure_account("genshin", "600000009", "America", -5)
        self.run_sync()
        e = self.error()
        self.assertEqual((e["code"], e["found"], e["active"]), ("uid_mismatch", "800000001", "600000009"))
        self.assertEqual(self.t.store.count("genshin", "800000001"), 0)
        self.assertIn((3, "failed"), self.steps())
        res = self.run_sync(new_account=True)
        self.assertEqual(res["accountCheck"], "Another account, added as a new one")
        self.assertEqual(self.t.store.active("genshin")["uid"], "800000001")
        self.assertEqual(sorted(a["uid"] for a in self.t.store.accounts("genshin")), ["600000009", "800000001"])

    def test_a_stored_account_other_than_the_active_one_is_accepted(self):
        self.run_sync()
        self.t.store.ensure_account("genshin", "600000009", "America", -5)
        self.now += 5
        self.t.store.touch("genshin", "600000009", now=self.now)
        self.assertEqual(self.t.store.active("genshin")["uid"], "600000009")
        self.now += 5
        res = self.run_sync()
        self.assertEqual(res["accountCheck"], "UID matches the stored account")
        self.assertEqual(self.t.store.active("genshin")["uid"], "800000001")

    def test_expired_link(self):
        self.svc.valid = set()
        self.run_sync()
        self.assertEqual(self.error()["code"], "link_expired")
        self.assertIn((2, "failed"), self.steps())
        # both addresses were tested, each one twice: the service gives the same answer for an expired key and for a refused language,
        # so the second try leaves the language override out. Nothing else was asked.
        self.assertEqual([(r["key"], r["lang"]) for r in self.svc.requests], [(KEY, "en"), (KEY, "pt"), (OLD_KEY, "en"), (OLD_KEY, "pt")])
        self.assertEqual(self.t.store.accounts(), [])

    def test_the_second_address_is_tried_when_the_first_is_expired(self):
        self.svc.valid = {OLD_KEY}
        res = self.run_sync()
        self.assertIsNotNone(res)
        self.assertEqual(next(e for e in self.events if e["type"] == "step" and e["n"] == 2 and e["state"] == "ok")["text"], "2 of 2 candidates tested")

    def test_no_cache(self):
        self.cache.unlink()
        self.run_sync()
        self.assertEqual(self.error()["code"], "cache_missing")
        self.assertEqual(self.steps()[-1], (1, "failed"))
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_bytes(b"\x00" * 100)
        self.run_sync()
        self.assertEqual(self.error()["code"], "cache_missing")
        self.assertEqual(self.steps()[-1], (2, "failed"))

    def test_offline_stops_after_the_first_address(self):
        def down(url, timeout):
            raise NetworkError("no route")
        self.svc.http = down
        self.run_sync()
        self.assertEqual(self.error()["code"], "network")
        self.assertEqual(self.steps()[-1], (2, "failed"))

    def test_rate_limit_in_the_middle_keeps_the_finished_banner_types(self):
        def fail(req):
            if req["code"] == "302" and req["page"] >= 1 and req["size"] == 20:
                return 200, json.dumps({"retcode": -110, "message": "visit too frequently", "data": None}).encode()
        self.svc.fail = fail
        self.run_sync()
        self.assertEqual(self.error()["code"], "rate_limited")
        self.assertEqual([e["retry"] for e in self.events if e["type"] == "wait"], [1, 2, 3, 4, 5])
        self.assertIn((3, "failed"), self.steps())
        self.assertEqual(self.t.store.count_codes("genshin", "800000001", ["301", "400"]), 92)       # the first banner type is stored
        self.assertEqual(self.t.store.count_codes("genshin", "800000001", ["302"]), 0)
        self.assertEqual(self.t.store.meta(sync.full_key("genshin", "800000001", "character")), "1")
        self.assertIsNone(self.t.store.meta(sync.full_key("genshin", "800000001", "weapon")))
        self.assertTrue(self.t.state()["synced"])

    def test_cancel_emits_nothing_more_and_keeps_the_finished_banner_types(self):
        cancel = threading.Event()
        seen = []

        def emit(ev):
            self.events.append(ev)
            if ev["type"] == "fetch" and ev["group"] == "weapon":
                cancel.set()
        res = sync.Syncer(self.t, "genshin", emit, cancel, http=self.svc.http, sleep=lambda s: None, clock=lambda: self.now).run()
        self.assertIsNone(res)
        self.assertEqual(self.events[-1]["type"], "fetch")
        self.assertIsNone(self.error())
        self.assertEqual(self.t.store.count_codes("genshin", "800000001", ["301", "400"]), 92)
        self.assertEqual(self.t.store.count_codes("genshin", "800000001", ["302"]), 0)
        self.assertEqual(seen, [])

    def test_unknown_banner_types_are_stored_and_reported(self):
        self.svc.add("200", [3, 3], t0=T0 + 20, codes=["200", "777"])
        res = self.run_sync()
        self.assertIn("777", " ".join(res["notes"]))
        self.assertEqual(self.t.store.count_codes("genshin", "800000001", ["777"]), 1)

    def test_chronicled_wish_is_stored_when_the_service_has_it_and_ignored_when_it_does_not(self):
        res = self.run_sync()                              # no code 500 in the service: "invalid gacha type" is ignored
        self.assertIsNone(self.error())
        self.assertEqual(res["notes"], [])
        self.svc.add("500", [3, 4], t0=T0 + 30)
        self.run_sync()
        self.assertEqual(self.t.store.count_codes("genshin", "800000001", ["500"]), 2)
        self.assertNotIn("500", " ".join(self.run_sync()["notes"]))

    def test_an_account_with_no_pulls_is_not_created(self):
        self.svc.history = {}
        res = self.run_sync()
        self.assertEqual(res["added"]["total"], 0)
        self.assertEqual(self.t.store.accounts(), [])

    def test_names_that_are_not_english_are_reported(self):
        self.svc.lang_refused = True
        res = self.run_sync()
        self.assertTrue(any("not in English" in n for n in res["notes"]))

    def test_backup_before_a_sync_only_when_there_is_something_to_save_and_not_twice_in_a_row(self):
        res = self.run_sync()
        folder = self.t.backup_folder()
        self.assertFalse(folder.exists())                         # nothing was stored before the first sync
        self.svc.add("301", [3, 5], t0=T0 + 86400, new=True)
        self.run_sync()
        self.assertEqual(len(list(folder.glob("pulls-*.sqlite"))), 1)
        self.svc.add("301", [3], t0=T0 + 2 * 86400, new=True)
        self.run_sync()
        self.assertEqual(len(list(folder.glob("pulls-*.sqlite"))), 1)            # the newest backup is younger than 6 hours
        s = self.t.settings()
        s["autoBackup"] = False
        self.t.save_settings(s)
        self.assertIsNotNone(res)

    def test_a_cache_file_that_is_gone_after_the_search_is_reported_not_crashed_on(self):
        gone = self.root / "gone" / "data_2"            # what cache_files found a moment ago and the game has removed since
        with mock.patch.object(sync.locate, "cache_files", return_value=[gone]):
            self.assertIsNone(self.syncer().run())
        self.assertEqual([e["code"] for e in self.events if e["type"] == "error"], ["cache_unreadable"])      # not "internal" (a FileNotFoundError)

    def test_the_start_up_check_survives_a_cache_file_that_is_gone(self):
        os.utime(self.cache, (self.now - 3600, self.now - 3600))
        self.run_sync()
        self.assertTrue(sync.can_auto_sync(self.t, "genshin", clock=lambda: self.now))
        with mock.patch.object(sync.locate, "cache_files", return_value=[self.root / "gone" / "data_2"]):
            self.assertFalse(sync.can_auto_sync(self.t, "genshin", clock=lambda: self.now))

    def test_auto_sync_runs_only_after_a_full_sync_and_with_a_fresh_link(self):
        os.utime(self.cache, (self.now - 3600, self.now - 3600))
        self.assertFalse(sync.can_auto_sync(self.t, "genshin", clock=lambda: self.now))        # no account yet
        self.run_sync()
        self.assertTrue(sync.can_auto_sync(self.t, "genshin", clock=lambda: self.now))
        self.assertFalse(sync.can_auto_sync(self.t, "genshin", clock=lambda: self.now + 30 * 3600))     # the link is older than a day
        self.assertFalse(sync.can_auto_sync(self.t, "hsr", clock=lambda: self.now))
        self.svc.add("301", [5], t0=T0 + 86400, new=True)
        with mock.patch.object(sync.time, "time", lambda: self.now):
            out = sync.auto_sync(self.t, http=self.svc.http, sleep=lambda s: None, clock=lambda: self.now)
        self.assertEqual(out, {"genshin": "synced 1 new", "hsr": "skipped", "zzz": "skipped"})

    def test_auto_sync_reports_an_error_code_and_goes_on(self):
        self.run_sync()
        os.utime(self.cache, (self.now - 60, self.now - 60))
        self.svc.valid = set()
        with mock.patch.object(sync.time, "time", lambda: self.now):
            out = sync.auto_sync(self.t, http=self.svc.http, sleep=lambda s: None, clock=lambda: self.now)
        self.assertEqual(out["genshin"], "link_expired")


    def test_auto_sync_stops_at_once_when_the_program_is_closing(self):
        self.run_sync()
        os.utime(self.cache, (self.now - 60, self.now - 60))
        self.svc.add("301", [3] * 400, t0=T0 + 86400, new=True)            # 20 pages of new pulls: it would run for a second or more
        self.svc.requests.clear()
        closing = threading.Event()
        base = self.svc.http

        def http(url, timeout):
            closing.set()                                                  # the window is closed during the first request
            return base(url, timeout)
        with mock.patch.object(sync.time, "time", lambda: self.now):
            out = sync.auto_sync(self.t, cancelled=closing.is_set, http=http, sleep=lambda s: time.sleep(0.05), clock=lambda: self.now)
        self.assertEqual(out["genshin"], "stopped")
        self.assertLess(len(self.svc.requests), 15)


class OtherGamesSyncTest(unittest.TestCase):
    def run_game(self, game, uid, plan, tz, check, urls=None, hosts=None):
        """Sync a made-up account and call `check(tracker, result, events, service)` while the database is open. `urls`: the addresses in the
        cache file (default: one history address); `hosts`: the hostnames the fake service answers on (default: any)."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"PULLS_TRACKER_DATA": str(Path(tmp) / "data")}):
            store = Store(Path(tmp) / "data" / "pulls.sqlite")
            try:
                ui = Path(tmp) / "ui" / "js"
                ui.mkdir(parents=True)
                t = Tracker(store, data_dir=Path(tmp) / "data", ui_dir=Path(tmp) / "ui", clock=lambda: T0 + 90 * 86400)
                s = t.settings()
                s["folders"][game.key] = tmp
                t.save_settings(s)
                svc = FakeService(game, uid=uid, tz=tz)
                svc.hosts = hosts
                for code, ranks in plan.items():
                    svc.add(code, ranks)
                write_cache(Path(tmp), game, urls or [api_url(game)])
                events = []
                res = sync.Syncer(t, game.key, events.append, http=svc.http, sleep=lambda s: None, clock=lambda: T0 + 90 * 86400).run()
                self.assertIsNotNone(res, events)
                check(t, res, events, svc)
            finally:
                store.close()

    def test_star_rail(self):
        def check(t, res, events, svc):
            self.assertEqual(res["added"]["total"], 45 + 2 + 1 + 2)
            self.assertEqual(sorted({r["path"] for r in svc.requests}), [HSR.api_path])
            self.assertEqual(t.store.account("hsr", "700000002")["utc_offset"], 1)
        self.run_game(HSR, "700000002", {"11": pity_plan(5, [40], 5), "12": [3, 4], "1": [3], "2": [3, 3]}, 1, check)

    def test_star_rail_page_link_is_sent_to_the_real_global_host(self):
        """The cache can hold only the history page address (a webstatic page, not the API one): the host then comes from games.HSR.hosts.
        The real service answers on public-operation-hkrpg-sg.hoyoverse.com (written out here, not read from games.py, so a wrong host in
        games.py fails this test); any other host is an HTTP 404."""
        page = ("https://webstatic.hoyoverse.com/hkrpg/event/e20211215gacha-v2/index.html?authkey_ver=1&sign_type=2&auth_appid=webview_gacha"
                f"&win_mode=fullscreen&gacha_id=abc&timestamp=1&region=prod_official_asia&default_gacha_type=11&lang=en&authkey={KEY}&game_biz=hkrpg_global#/log")

        def check(t, res, events, svc):
            self.assertEqual(res["added"]["total"], 45 + 2 + 1 + 2)
            self.assertEqual({r["host"] for r in svc.requests}, {"public-operation-hkrpg-sg.hoyoverse.com"})      # the first host tried is the right one
        self.run_game(HSR, "700000002", {"11": pity_plan(5, [40], 5), "12": [3, 4], "1": [3], "2": [3, 3]}, 1, check,
                      urls=[page], hosts={"public-operation-hkrpg-sg.hoyoverse.com"})

    def test_zenless_zone_zero_rarity_and_extra_parameter(self):
        def check(t, res, events, svc):
            self.assertEqual(res["new5"], 1)
            h = t.history("zzz", {"rarity": "5"})
            self.assertEqual((h["total"], h["rows"][0]["rarity"], h["rows"][0]["pity"]), (1, 5, 30))
            self.assertEqual(t.history("zzz", {"rarity": "4", "group": "wengine"})["total"], 1)
        self.run_game(ZZZ, "1300000003", {"2": pity_plan(4, [30], 3), "3": [2, 3], "5": [2], "1": [2]}, 8, check)


class FirstRunTest(SyncBase):
    def test_folder_and_link_state_per_game(self):
        mtime = self.cache.stat().st_mtime
        res = sync.first_run(self.t, clock=lambda: mtime + 3 * 3600)
        by = {g["game"]: g for g in res["games"]}
        self.assertEqual(list(by), ["genshin", "hsr", "zzz"])
        self.assertEqual(by["genshin"]["folder"], {"state": "found", "path": str(self.games_root)})
        self.assertEqual(by["genshin"]["link"], {"state": "found", "ageHours": 3.0})
        self.assertEqual(by["hsr"], {"game": "hsr", "folder": {"state": "missing", "path": ""}, "link": {"state": "missing", "ageHours": None}})

    def test_a_cache_without_a_link(self):
        write_cache(self.games_root, self.game, [], noise=True)
        res = sync.first_run(self.t)
        self.assertEqual(res["games"][0]["folder"]["state"], "found")
        self.assertEqual(res["games"][0]["link"]["state"], "missing")

    def test_through_the_api(self):
        res = PullsApi(self.t).firstRun()
        self.assertEqual([g["game"] for g in res["games"]], ["genshin", "hsr", "zzz"])
        self.assertLess(res["games"][0]["link"]["ageHours"], 0.1)


class ApiSyncTest(SyncBase):
    def setUp(self):
        super().setUp()
        self.svc.add("301", pity_plan(5, [30, 50], 12), codes=["301", "400"])
        self.svc.add("302", pity_plan(5, [20], 4), t0=T0 + 5)
        self.svc.add("200", [3, 4, 3], t0=T0 + 9)
        self.svc.add("100", [3, 3, 4], t0=T0 + 11)

    def make_api(self, gate=None):
        api = PullsApi(self.t)
        self.api_events: list[dict] = []
        lock = threading.Lock()

        def emit(ev):
            with lock:
                self.api_events.append(ev)
        api._emit = emit

        def http(url, timeout):
            if gate is not None:
                gate.wait(5)
            return self.svc.http(url, timeout)
        api._sync_options = {"http": http, "sleep": lambda s: None}
        return api

    def join(self, api):
        api._sync_thread.join(10)
        self.assertFalse(api._sync_thread.is_alive())

    def test_start_returns_at_once_and_the_events_arrive_from_the_thread(self):
        api = self.make_api()
        main = threading.current_thread()
        seen = []
        original = api._emit
        api._emit = lambda ev: (seen.append(threading.current_thread() is main), original(ev))
        self.assertEqual(api.syncStart("genshin"), {"started": True})
        self.join(api)
        self.assertEqual(self.api_events[-1]["type"], "done")
        self.assertEqual(self.api_events[-1]["game"], "genshin")
        self.assertFalse(any(seen))
        self.assertEqual(self.t.store.count("genshin", "800000001"), 122)

    def test_a_second_start_while_one_runs_is_refused(self):
        gate = threading.Event()
        api = self.make_api(gate)
        self.assertEqual(api.syncStart("genshin"), {"started": True})
        self.assertEqual(api.syncStart("hsr"), {"started": False, "busy": True})
        gate.set()
        self.join(api)
        self.assertEqual(api.syncStart("hsr"), {"started": True})            # after the end a new one can start
        self.join(api)
        self.assertEqual(self.api_events[-1]["game"], "hsr")

    def test_cancel_stops_the_thread_and_drops_later_events(self):
        gate = threading.Event()
        api = self.make_api(gate)
        api.syncStart("genshin")
        time.sleep(0.1)
        threading.Timer(0.2, gate.set).start()
        self.assertTrue(api.syncCancel())
        self.join(api)
        self.assertNotIn("done", [e["type"] for e in self.api_events])
        self.assertEqual(self.t.store.count("genshin", "800000001"), 0)
        self.assertEqual(api.syncStart("genshin"), {"started": True})        # a sync can start after a cancel
        gate.set()
        self.join(api)
        self.assertEqual(self.api_events[-1]["type"], "done")

    def test_new_account_option(self):
        self.add(GENSHIN, [], uid="600000009")
        self.t.store.ensure_account("genshin", "600000009", "America", -5)
        api = self.make_api()
        api.syncStart("genshin", {"newAccount": False})
        self.join(api)
        self.assertEqual(self.api_events[-1]["code"], "uid_mismatch")
        self.api_events.clear()
        api.syncStart("genshin", {"newAccount": True})
        self.join(api)
        self.assertEqual(self.api_events[-1]["type"], "done")
        self.assertEqual(api.syncStart("genshin", "yes"), {"started": True})   # anything but {newAccount: true} is not the option
        self.join(api)

    def test_bad_game_is_answered_not_raised(self):
        res = PullsApi(self.t).syncStart("nope")
        self.assertTrue(res.get("unavailable"))
        self.assertIn("error", res)

    def test_source_test(self):
        api = PullsApi(self.t)
        self.assertEqual(api.sourceTest("own")["ok"], False)                  # no address saved
        seen = []

        class F:
            API_BASE = "https://default.example"
            GAMES = [{"key": "genshin", "title": "GENSHIN IMPACT"}]

            class FetchError(Exception):
                pass

            @staticmethod
            def fetch_current_banners(game, base, lang, now):
                seen.append(base)
                if "down" in base:
                    raise F.FetchError("network error: refused")
                return [1, 2]
        with mock.patch.object(banners_mod, "fetcher", lambda: F):
            self.assertEqual(api.sourceTest("calendar"), {"ok": True, "detail": "2 banner(s) for Genshin Impact"})
            s = self.t.settings()
            s["server"] = "https://down.example/"
            self.t.save_settings(s)
            self.assertEqual(api.sourceTest("own"), {"ok": False, "error": "network error: refused"})
        self.assertEqual(seen, ["https://default.example", "https://down.example"])


class FakeKeyring:
    """Stands for the keyring module with a store that refuses values over 1280 characters (the Credential Manager's limit)."""
    class Backend:
        pass

    def __init__(self):
        self.items = {}
        self.backend = self.Backend()

    def get_keyring(self):
        return self.backend

    def set_password(self, service, name, value):
        assert len(value) <= 1280, "too long for the credential store"
        self.items[(service, name)] = value

    def get_password(self, service, name):
        return self.items.get((service, name))

    def delete_password(self, service, name):
        if (service, name) not in self.items:
            raise KeyError(name)
        del self.items[(service, name)]


class VaultTest(unittest.TestCase):
    def test_without_a_keyring_nothing_is_stored(self):
        with mock.patch.dict(sys.modules, {"keyring": None}):
            self.assertFalse(vault.available())
            self.assertFalse(vault.save("hoyolab", "x"))
            self.assertEqual(vault.load("hoyolab"), "")

    def test_a_long_value_is_stored_in_pieces_and_replaced_or_removed(self):
        k = FakeKeyring()
        cookie = "ltoken_v2=" + "abc123;" * 400
        with mock.patch.dict(sys.modules, {"keyring": k}):
            self.assertTrue(vault.available())
            self.assertTrue(vault.save("hoyolab", cookie))
            self.assertEqual(vault.load("hoyolab"), cookie)
            self.assertGreater(len(k.items), 3)
            self.assertTrue(vault.save("hoyolab", "short"))
            self.assertEqual(vault.load("hoyolab"), "short")
            self.assertEqual(len(k.items), 2)                                  # the old pieces are gone
            self.assertTrue(vault.save("hoyolab", ""))
            self.assertEqual((vault.load("hoyolab"), k.items), ("", {}))

    def test_a_removal_the_keyring_refuses_is_not_reported_as_done(self):
        for broken in ("get", "delete"):
            k = FakeKeyring()
            with mock.patch.dict(sys.modules, {"keyring": k}):
                self.assertTrue(vault.save("hoyolab", "x" * 1500))
                if broken == "get":
                    k.get_password = mock.Mock(side_effect=RuntimeError("locked"))
                else:
                    k.delete_password = mock.Mock(side_effect=RuntimeError("refused"))
                self.assertFalse(vault.save("hoyolab", ""), broken)           # it answered True while every piece stayed in the keyring
                self.assertFalse(vault.save("hoyolab", "new"), broken)        # and a new value would have been written beside the old pieces
            self.assertGreater(len(k.items), 0)

    def test_removing_what_is_already_partly_gone_still_succeeds(self):
        k = FakeKeyring()
        with mock.patch.dict(sys.modules, {"keyring": k}):
            self.assertTrue(vault.save("hoyolab", "x" * 1500))
            del k.items[("PullsTracker", "hoyolab:1")]                        # removed by hand
            self.assertTrue(vault.save("hoyolab", ""))
            self.assertEqual(k.items, {})
            k.items[("PullsTracker", "hoyolab:n")] = "not a number"           # a damaged count
            self.assertTrue(vault.delete("hoyolab"))
            self.assertEqual(k.items, {})

    def test_a_keyring_that_does_nothing_is_not_used(self):
        # keyring's real stand-ins are keyring.backends.fail.Keyring and keyring.backends.null.Keyring: both classes are called "Keyring"
        for module in ("keyring.backends.fail", "keyring.backends.null"):
            k = FakeKeyring()
            k.backend = type("Keyring", (), {"__module__": module})()
            with mock.patch.dict(sys.modules, {"keyring": k}):
                self.assertFalse(vault.available(), module)
                self.assertFalse(vault.save("hoyolab", "x"), module)
                self.assertEqual(k.items, {})

    def test_the_real_fail_backend_of_keyring_is_recognised(self):
        try:
            from keyring.backends import fail
        except ImportError:
            self.skipTest("keyring is not installed")
        k = FakeKeyring()
        k.backend = fail.Keyring()
        with mock.patch.dict(sys.modules, {"keyring": k}):
            self.assertFalse(vault.available())

    def test_a_cookie_with_a_missing_piece_is_not_returned_cut(self):
        k = FakeKeyring()
        with mock.patch.dict(sys.modules, {"keyring": k}):
            self.assertTrue(vault.save("hoyolab", "x" * 1500))
            del k.items[("PullsTracker", "hoyolab:1")]
            self.assertEqual(vault.load("hoyolab"), "")

    def test_the_cookie_never_goes_to_the_settings_or_the_log(self):
        k = FakeKeyring()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"PULLS_TRACKER_DATA": str(Path(tmp) / "data")}):
            store = Store(Path(tmp) / "data" / "pulls.sqlite")
            try:
                ui = Path(tmp) / "ui" / "js"
                ui.mkdir(parents=True)
                t = Tracker(store, data_dir=Path(tmp) / "data", ui_dir=Path(tmp) / "ui")
                api = PullsApi(t)
                logs = []
                h = logging.Handler()
                h.emit = lambda r: logs.append(r.getMessage())
                logging.getLogger("pt").addHandler(h)
                try:
                    with mock.patch.dict(sys.modules, {"keyring": k}):
                        self.assertEqual(api.saveCookie("  SECRETCOOKIE  "), {"ok": True})
                finally:
                    logging.getLogger("pt").removeHandler(h)
                self.assertEqual(vault.load.__name__, "load")
                self.assertNotIn("SECRET", json.dumps(t.settings()))
                self.assertFalse(any("SECRET" in line for line in logs))
                self.assertEqual(k.items[(vault.SERVICE, "hoyolab:0")], "SECRETCOOKIE")
            finally:
                store.close()
        with mock.patch.dict(sys.modules, {"keyring": None}):
            self.assertTrue(PullsApi(None).saveCookie("x")["unavailable"])


if __name__ == "__main__":
    unittest.main()
