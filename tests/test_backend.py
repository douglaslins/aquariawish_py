"""Tests of the data side: normalizing pulls, UIGF and CSV, the SQLite store, pity and guarantee, and the answers of the API methods.
The pulls are made up (no account, no network)."""
from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.pop("PULLS_TRACKER_DATA", None)      # a test must never touch a real data folder: each test that needs one sets its own

from pt import engine, paths, pulls, settings as settings_mod                       # noqa: E402
from pt.api import PullsApi                                                          # noqa: E402
from pt.games import GENSHIN, HSR, ZZZ, region_of                             # noqa: E402
from pt.store import Store, _is_damaged, full_key                                    # noqa: E402
from pt.tracker import Tracker                                                       # noqa: E402

T0 = 1788220800          # 2026-09-01 00:00:00 UTC
UI = Path(__file__).resolve().parent.parent / "pulls_tracker_ui"


def seq(game, code, rarities, names=None, uid="800000001", start_id=1_700_000_000_000_000_000, t0=T0, codes=None):
    """Records of one group, oldest first. rarities: the API's rank_type of each pull; names: {index: name} for the non-default names."""
    out = []
    for i, rank in enumerate(rarities):
        name = (names or {}).get(i) or f"Item {rank}-{i}"
        out.append({"id": str(start_id + i * 10), "gacha_type": (codes[i % len(codes)] if codes else code), "item_id": str(1000 + i), "name": name,
                    "item_type": "Character", "rank_raw": rank, "rarity": game.rarity(rank), "time": t0 + i * 60, "raw": {}})
    return out


def pity_plan(top, plan, trail):
    """Rarity list: for each (pity, ...) in plan a run of lower pulls then a top-rarity pull; then `trail` pulls without a top-rarity one."""
    r = []
    for pity in plan:
        r += [3] * (pity - 1) + [top]
    return r + [3] * trail


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = mock.patch.dict(os.environ, {"PULLS_TRACKER_DATA": str(self.root / "data")})
        env.start()
        self.addCleanup(env.stop)
        self.ui = self.root / "ui"
        (self.ui / "js").mkdir(parents=True)
        self.now = T0 + 90 * 86400
        self.store = Store(self.root / "data" / "pulls.sqlite")
        self.addCleanup(self.store.close)

    def banners(self, slides, generated="2026-10-25T14:17:00Z", path=None):
        f = path or (self.ui / "js" / "banners.js")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("/* test */\nwindow.PT_BANNERS = " + json.dumps({"generatedAt": generated, "source": "https://api.example", "slides": slides, "games": {}}) + ";\n", encoding="utf-8")

    def tracker(self):
        return Tracker(self.store, data_dir=self.root / "data", ui_dir=self.ui, clock=lambda: self.now)

    def add(self, game, records, uid="800000001"):
        label, off = region_of(game.key, uid)
        self.store.ensure_account(game.key, uid, label, off)
        return self.store.add_pulls(game.key, uid, records)


class PullsTest(unittest.TestCase):
    def test_time_is_converted_from_the_server_zone_to_utc(self):
        self.assertEqual(pulls.parse_time("2026-10-22 17:20:00", 8), pulls.parse_time("2026-10-22 09:20:00", 0))
        self.assertEqual(pulls.server_time(pulls.parse_time("2026-10-22 17:20:00", 8), 8), "2026-10-22 17:20:00")
        self.assertEqual(pulls.parse_time("2026-01-01 00:00:00", -5) - pulls.parse_time("2026-01-01 00:00:00", 1), 6 * 3600)

    def test_normalize_keeps_the_id_text_and_maps_the_rarity(self):
        item = {"uid": "1", "gacha_type": "301", "item_id": "", "count": "1", "time": "2026-10-22 17:20:00", "name": "Mona", "lang": "en-us",
                "item_type": "Character", "rank_type": "5", "id": "1761124800000123456"}
        n = pulls.normalize(GENSHIN, item, 8)
        self.assertEqual((n["id"], n["rarity"], n["gacha_type"]), ("1761124800000123456", 5, "301"))
        self.assertEqual(n["raw"], {"count": "1", "lang": "en-us", "uid": "1"})
        z = pulls.normalize(ZZZ, {**item, "gacha_type": "2", "rank_type": "4"}, 8)
        self.assertEqual(z["rarity"], 5)                                                    # ZZZ calls its top rank "4"
        self.assertEqual(pulls.normalize(ZZZ, {**item, "gacha_type": "2", "rank_type": "3"}, 8)["rarity"], 4)
        self.assertEqual(pulls.normalize(ZZZ, {**item, "gacha_type": "2", "rank_type": "2"}, 8)["rarity"], 3)

    def test_unusable_items_are_not_normalized(self):
        good = {"id": "1", "gacha_type": "301", "time": "2026-10-22 17:20:00", "rank_type": "3", "name": "x"}
        self.assertIsNotNone(pulls.normalize(GENSHIN, good, 8))
        for bad in ({**good, "id": "abc"}, {**good, "time": "yesterday"}, {**good, "rank_type": ""}, {**good, "gacha_type": ""}, {k: v for k, v in good.items() if k != "id"}):
            self.assertIsNone(pulls.normalize(GENSHIN, bad, 8), bad)

    def test_unknown_gacha_type_is_kept(self):
        n = pulls.normalize(GENSHIN, {"id": "5", "gacha_type": "500", "time": "2026-10-22 17:20:00", "rank_type": "4", "name": "x"}, 8)
        self.assertEqual(n["gacha_type"], "500")

    def test_uigf_round_trip(self):
        recs = seq(GENSHIN, "301", [3, 4, 5, 3], codes=["301", "400"])
        rows = [{**r, "raw": json.dumps(r["raw"])} for r in recs]
        doc = pulls.export_uigf(GENSHIN, "800000001", rows, 8, now=1)
        self.assertEqual(doc["info"]["version"], "v4.0")
        item = doc["hk4e"][0]["list"]
        self.assertEqual([i["uigf_gacha_type"] for i in item], ["301", "301", "301", "301"])         # 400 is written as 301 there
        self.assertEqual([i["gacha_type"] for i in item], ["301", "400", "301", "400"])
        parsed = pulls.read_uigf(json.dumps(doc))
        e = parsed["entries"][0]
        self.assertEqual((e["game"], e["uid"], e["offset"]), ("genshin", "800000001", 8))
        self.assertEqual([(i["id"], i["time"], i["rank_raw"], i["gacha_type"], i["name"]) for i in e["items"]],
                         [(r["id"], r["time"], r["rank_raw"], r["gacha_type"], r["name"]) for r in recs])

    def test_uigf_of_the_other_games(self):
        for game, code, top in ((HSR, "11", 5), (ZZZ, "2", 4)):
            recs = seq(game, code, [top, top - 2])
            doc = pulls.export_uigf(game, "6000001", [{**r, "raw": "{}"} for r in recs], -5, now=1)
            self.assertIn(game.uigf, doc)
            self.assertEqual(doc[game.uigf][0]["timezone"], -5)
            self.assertIn("gacha_id", doc[game.uigf][0]["list"][0])
            back = pulls.read_uigf(json.dumps(doc))["entries"][0]
            self.assertEqual([i["time"] for i in back["items"]], [r["time"] for r in recs])
            self.assertEqual([i["rarity"] for i in back["items"]], [5, 3])

    def test_ids_and_ranks_that_the_store_could_not_hold_are_skipped(self):
        ok = {"id": "1700000000000000001", "gacha_type": "301", "rank_type": "3", "time": "2024-01-01 00:00:00", "name": "x"}
        self.assertIsNotNone(pulls.normalize(GENSHIN, ok, 8))
        for change in ({"id": "1_000"}, {"id": "١٢٣"}, {"id": str(2 ** 63)}, {"id": "-5"}, {"id": "1.5"}, {"rank_type": "1000"}, {"rank_type": "-1"}):
            self.assertIsNone(pulls.normalize(GENSHIN, {**ok, **change}, 8), change)

    def test_a_pull_time_outside_1970_to_3000_is_skipped(self):
        ok = {"id": "1700000000000000001", "gacha_type": "301", "rank_type": "5", "time": "2024-01-01 00:00:00", "name": "x"}
        self.assertIsNotNone(pulls.normalize(GENSHIN, ok, 8))
        for when, offset in (("0001-01-01 00:00:00", 8), ("9999-12-31 23:59:59", -8), ("1969-12-31 00:00:00", 0), ("1970-01-01 00:00:00", 8),
                             ("3001-01-01 00:00:00", 0)):
            self.assertIsNone(pulls.normalize(GENSHIN, {**ok, "time": when}, offset), when)
        self.assertIsNotNone(pulls.normalize(GENSHIN, {**ok, "time": "1970-01-01 08:00:00"}, 8))            # exactly 1970-01-01 00:00:00 UTC
        self.assertIsNotNone(pulls.normalize(GENSHIN, {**ok, "time": "3000-12-31 23:59:59"}, 0))

    def test_json_that_nests_too_deep_and_a_csv_field_that_is_too_long_are_refused_with_the_message_of_the_screen(self):
        with self.assertRaisesRegex(pulls.ImportError_, "not a UIGF v4"):
            pulls.read_uigf("[" * 200_000)
        with self.assertRaisesRegex(pulls.ImportError_, "could not be read"):
            pulls.read_csv("id,name,gacha_type,server_time,rarity\n1," + "x" * 200_000 + ",301,2026-01-01 00:00:00,5\n", GENSHIN, "800000001", 8)

    def test_a_uigf_file_with_a_bad_account_time_zone_or_list_is_refused(self):
        item = {"id": "1700000000000000001", "gacha_type": "301", "uigf_gacha_type": "301", "rank_type": "3", "time": "2024-01-01 00:00:00",
                "name": "x", "item_id": "1", "item_type": "Weapon"}
        good = {"uid": "800000001", "timezone": 8, "list": [item]}

        def doc(accounts):
            return json.dumps({"info": {"version": "v4.0"}, "hk4e": accounts})
        self.assertEqual(len(pulls.read_uigf(doc([good]))["entries"][0]["items"]), 1)            # the control: this one is read
        bad = [[{**good, "timezone": float("inf")}], [{**good, "timezone": float("nan")}], [{**good, "timezone": 99}], [{**good, "uid": "../x"}],
               [{**good, "uid": ""}], ["not an account"], {"uid": "1"}, [{**good, "list": "nope"}]]
        for accounts in bad:
            with self.assertRaises(pulls.ImportError_, msg=str(accounts)):
                pulls.read_uigf(doc(accounts))

    def test_csv_names_that_look_like_formulas_are_written_as_text_and_read_back(self):
        recs = seq(GENSHIN, "302", [3, 4, 5], names={0: "=1+1", 1: "@SUM(A1)", 2: "-2+3"})
        text = pulls.export_csv(GENSHIN, [{**r, "raw": "{}"} for r in recs], 8)
        self.assertEqual([r["name"] for r in csv.DictReader(io.StringIO(text))], ["'=1+1", "'@SUM(A1)", "'-2+3"])
        back = pulls.read_csv(text, GENSHIN, "800000001", 8)["entries"][0]["items"]
        self.assertEqual([i["name"] for i in back], ["=1+1", "@SUM(A1)", "-2+3"])

    def test_files_that_are_not_uigf_v4(self):
        for text in ("not json", "{}", json.dumps({"info": {"version": "v3.0"}, "hk4e": []}), json.dumps({"info": {"version": "v4.0"}, "hk4e": []}),
                     json.dumps({"info": {"version": "v4.0"}, "hk4e": [{"uid": "1", "list": []}]})):
            with self.assertRaises(pulls.ImportError_, msg=text):
                pulls.read_uigf(text)

    def test_csv_round_trip(self):
        recs = seq(GENSHIN, "302", [3, 4, 5], names={1: 'Name, with "quotes"'})
        rows = [{**r, "raw": "{}"} for r in recs]
        text = pulls.export_csv(GENSHIN, rows, 8)
        back = pulls.read_csv(text, GENSHIN, "800000001", 8)["entries"][0]["items"]
        self.assertEqual([(i["id"], i["time"], i["name"], i["rarity"]) for i in back], [(r["id"], r["time"], r["name"], r["rarity"]) for r in recs])
        with self.assertRaises(pulls.ImportError_):
            pulls.read_csv("a,b\n1,2\n", GENSHIN, "1", 8)


class StoreTest(Base):
    def test_a_pull_is_stored_once(self):
        recs = seq(GENSHIN, "301", [3, 4, 5])
        self.store.ensure_account("genshin", "800000001", "Asia", 8)
        self.assertEqual(self.store.add_pulls("genshin", "800000001", recs), {"301": 3})
        self.assertEqual(self.store.add_pulls("genshin", "800000001", recs), {})
        self.assertEqual(self.store.count("genshin", "800000001"), 3)

    def test_the_same_id_in_two_games_or_accounts_is_two_rows(self):
        a = seq(GENSHIN, "301", [3])
        self.store.add_pulls("genshin", "800000001", a)
        self.store.add_pulls("genshin", "800000002", a)
        self.store.add_pulls("hsr", "800000001", [{**a[0], "gacha_type": "11"}])
        self.assertEqual([self.store.count("genshin", "800000001"), self.store.count("genshin", "800000002"), self.store.count("hsr", "800000001")], [1, 1, 1])

    def test_ids_are_ordered_as_integers_and_not_rounded(self):
        ids = ["1700000000000000009", "1700000000000000100", "900000000000000001"]
        recs = [{**seq(GENSHIN, "301", [3])[0], "id": i} for i in ids]
        self.store.add_pulls("genshin", "u", recs)
        got = [r["id"] for r in self.store.pulls("genshin", "u")]
        self.assertEqual(got, ["900000000000000001", "1700000000000000009", "1700000000000000100"])
        self.assertEqual(len(set(got)), 3)                              # a float would have merged the two big ones

    def test_replace_existing_and_remove(self):
        recs = seq(GENSHIN, "301", [3, 5])
        self.store.ensure_account("genshin", "u", "Asia", 8)
        self.store.add_pulls("genshin", "u", recs)
        ex = self.store.existing("genshin", "u", [r["id"] for r in recs] + ["999"])
        self.assertEqual(set(ex), {r["id"] for r in recs})
        self.assertEqual(self.store.replace_pulls("genshin", "u", [{**recs[0], "time": recs[0]["time"] + 5}]), 1)
        self.assertEqual(self.store.existing("genshin", "u", [recs[0]["id"]])[recs[0]["id"]][0], recs[0]["time"] + 5)
        self.assertEqual(self.store.remove_account("genshin", "u"), 2)
        self.assertEqual((self.store.count("genshin", "u"), self.store.accounts()), (0, []))

    def test_a_failed_batch_is_not_half_stored(self):
        recs = seq(GENSHIN, "301", [3, 3])
        recs[1] = {**recs[1], "id": "not a number"}
        with self.assertRaises(ValueError):
            self.store.add_pulls("genshin", "u", recs)
        self.assertEqual(self.store.count("genshin", "u"), 0)

    def test_active_account_is_the_one_touched_last(self):
        self.store.ensure_account("genshin", "a", "Asia", 8)
        self.store.ensure_account("genshin", "b", "Asia", 8)
        self.store.touch("genshin", "a", synced=True, now=100)
        self.assertEqual(self.store.active("genshin")["uid"], "a")
        self.store.touch("genshin", "b", now=200)
        self.assertEqual(self.store.active("genshin")["uid"], "b")
        self.assertEqual(self.store.account("genshin", "a")["last_sync"], 100)
        self.assertIsNone(self.store.account("genshin", "b")["last_sync"])

    def test_backup_is_a_database_with_the_rows(self):
        self.store.add_pulls("genshin", "u", seq(GENSHIN, "301", [3, 4]))
        out = self.store.backup_to(self.root / "backup" / "b.sqlite")
        con = sqlite3.connect(out)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM pulls").fetchone()[0], 2)
        con.close()

    def test_a_damaged_file_is_kept_aside_and_a_new_database_is_started(self):
        path = self.root / "bad" / "pulls.sqlite"
        path.parent.mkdir()
        path.write_bytes(b"this is not a database" * 100)
        s = Store(path)
        self.addCleanup(s.close)
        self.assertIsNotNone(s.recovered)
        self.assertTrue(s.recovered.exists())
        self.assertEqual(s.accounts(), [])

    def test_removing_an_account_also_removes_its_completion_marks(self):
        s = Store(self.root / "rm" / "pulls.sqlite")
        self.addCleanup(s.close)
        for uid in ("80000000", "800000001"):                           # the first UID is the start of the second one
            s.ensure_account("genshin", uid, "Asia", 8)
            s.set_meta(full_key("genshin", uid, "character"), "1")
            s.set_meta(full_key("genshin", uid, "weapon"), "1")
        s.set_meta("other", "kept")
        s.remove_account("genshin", "80000000")
        self.assertEqual([s.meta(full_key("genshin", "80000000", g)) for g in ("character", "weapon")], [None, None])
        self.assertEqual(s.meta(full_key("genshin", "800000001", "character")), "1")       # another account keeps its marks
        self.assertEqual((s.meta("other"), s.meta("schema")), ("kept", "1"))

    def test_a_database_that_is_only_busy_or_unreadable_for_now_is_not_moved_aside(self):
        path = self.root / "busy" / "pulls.sqlite"
        Store(path).close()
        for text in ("database is locked", "unable to open database file", "disk I/O error", "attempt to write a readonly database"):
            with mock.patch.object(Store, "_connect", side_effect=sqlite3.OperationalError(text)):
                with self.assertRaises(sqlite3.OperationalError):
                    Store(path)
        self.assertTrue(path.exists())
        self.assertEqual([p.name for p in path.parent.iterdir() if "damaged" in p.name], [])
        self.assertTrue(_is_damaged(sqlite3.DatabaseError("file is not a database")))
        self.assertTrue(_is_damaged(sqlite3.OperationalError("malformed database schema (x)")))
        self.assertFalse(_is_damaged(sqlite3.OperationalError("database is locked")))

    def test_a_failed_backup_leaves_no_half_file(self):
        s = Store(self.root / "bk" / "pulls.sqlite")
        self.addCleanup(s.close)
        dest = self.root / "bk" / "out" / "pulls-x.sqlite"
        with mock.patch.object(Path, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                s.backup_to(dest)
        self.assertEqual(list(dest.parent.iterdir()), [])
        self.assertEqual(s.backup_to(dest), dest)                            # and the next backup works
        self.assertTrue(dest.is_file())


class EngineTest(unittest.TestCase):
    def derive(self, game, group_key, ranks, names=None, classify=None, codes=None):
        recs = seq(game, "", ranks, names, codes=codes or [next(g for g in game.groups if g.key == group_key).codes[0]])
        g = next(x for x in game.groups if x.key == group_key)
        return engine.derive(game, g, recs, classify)

    def test_pity_counts_since_the_last_top_rarity(self):
        d = self.derive(GENSHIN, "standard", pity_plan(5, [77, 62], 17))
        fives = [r.pity for r in d.rows if r.rarity == 5]
        self.assertEqual(fives, [77, 62])
        self.assertEqual((d.pity, d.fives, len(d.rows)), (17, 2, 77 + 62 + 17))
        self.assertIsNone(d.guarantee)                                              # no guarantee on the standard banner

    def test_first_segment_is_a_lower_bound(self):
        d = self.derive(GENSHIN, "standard", pity_plan(5, [10, 20], 3))
        self.assertTrue(all(r.lb for r in d.rows[:10]))
        self.assertFalse(any(r.lb for r in d.rows[10:]))

    def test_guarantee_state_machine_genshin_character(self):
        names = {}
        plan = [77, 62, 31, 88, 80]
        ranks = pity_plan(5, plan, 62)
        at, i = [], 0
        for p in plan:
            i += p
            at.append(i - 1)
        for k, nm in zip(at, ["Mona", "Featured A", "Featured B", "Diluc", "Featured C"]):
            names[k] = nm
        d = self.derive(GENSHIN, "character", ranks, names, classify=lambda n, t: n not in ("Mona", "Diluc"), codes=["301", "400"])
        fives = [r for r in d.rows if r.rarity == 5]
        self.assertEqual([r.result["kind"] for r in fives], ["lost", "wonG", "won", "lost", "wonG"])
        self.assertEqual([r.result["assumed"] for r in fives], [False, False, False, False, False])
        self.assertEqual([r.result["flip"] for r in fives], [True, False, True, True, False])
        self.assertEqual((d.pity, d.guarantee), (62, False))
        self.assertEqual({r.sub for r in d.rows}, {1, 2})                              # Wish 1 and Wish 2 are one counter
        self.assertEqual(d.rows[1].pity, 2)                                           # the counter does not restart at the other Wish

    def test_a_first_win_is_assumed_a_5050_and_a_loss_is_not(self):
        d = self.derive(GENSHIN, "character", pity_plan(5, [40], 5), {39: "Featured"}, classify=lambda n, t: True, codes=["301"])
        r = [x for x in d.rows if x.rarity == 5][0]
        self.assertEqual((r.result["kind"], r.result["assumed"]), ("won", True))      # the guarantee before the first stored 5-star is unknown
        d = self.derive(GENSHIN, "character", pity_plan(5, [40], 5), {39: "Mona"}, classify=lambda n, t: False, codes=["301"])
        r = [x for x in d.rows if x.rarity == 5][0]
        self.assertEqual((r.result["kind"], r.result["assumed"]), ("lost", False))
        self.assertTrue(d.guarantee)

    def test_unknown_classification_leaves_the_result_and_guarantee_unknown(self):
        d = self.derive(GENSHIN, "weapon", pity_plan(5, [30], 5), classify=lambda n, t: None, codes=["302"])
        self.assertIsNone([x for x in d.rows if x.rarity == 5][0].result)
        self.assertIsNone(d.guarantee)

    def test_group_without_guarantee_has_no_results(self):
        d = self.derive(ZZZ, "bangboo", pity_plan(4, [30], 2), codes=["5"])
        self.assertIsNone([x for x in d.rows if x.rarity == 5][0].result)
        self.assertIsNone(d.guarantee)

    def test_classifier_uses_banner_windows_then_the_standard_list(self):
        c = engine.Classifier([{"start": 1000, "end": 2000, "names": {"skirk"}}], ["Diluc"])
        self.assertTrue(c("Skirk", 1500))
        self.assertFalse(c("Diluc", 1500))                      # a window that has featured names decides, not the list
        self.assertTrue(c("Skirk", 2000 + 100))                 # a little after the end (the slack)
        self.assertTrue(c("Mona", 99999) is False or c("Mona", 99999) is True)
        self.assertFalse(c("Diluc", 99999))
        self.assertTrue(c("Skirk", 99999))
        self.assertIsNone(engine.Classifier([], None)("Skirk", 1))

    def test_classifier_compares_names_without_case_extra_spaces_or_the_kind_of_apostrophe(self):
        c = engine.Classifier([], ["But the Battle Isn't Over"])
        for spelling in ("But the Battle Isn't Over", "but the battle isn’t over", "But  the Battle Isn‘t Over ", "BUT THE BATTLE ISN'T OVER"):
            self.assertFalse(c(spelling, 1), spelling)          # in the list: a 5-star that is not the featured one
        self.assertTrue(c("Brilliant Fixation", 1))
        w = engine.Classifier([{"start": 0, "end": 10, "names": {"Isn’t Featured"}}], None)
        self.assertTrue(w("isn't featured", 5))               # the names of a banner window are compared the same way

    def test_grade_and_stats(self):
        s = settings_mod.DEFAULTS
        self.assertEqual([engine.grade(a, s["grade"]) for a in (None, 40, 55, 70, 75, 80)], [None, "SS", "A", "B", "C", "F"])
        d = {"standard": self.derive(GENSHIN, "standard", pity_plan(5, [10, 80, 55], 4))}
        st = engine.stats(GENSHIN, d, ["standard"], 0, s, None)
        self.assertEqual((st["pulls"], st["fives"], st["best"], st["worst"], st["spent"]), (10 + 80 + 55 + 4, 3, 10, 80, (10 + 80 + 55 + 4) * 160))
        self.assertAlmostEqual(st["avg"], (10 + 80 + 55) / 3)
        self.assertEqual([b["n"] for b in st["bins"]][:2], [1, 0])
        self.assertEqual(sum(b["n"] for b in st["bins"]), 3)
        self.assertEqual(engine.stats(GENSHIN, d, ["standard"], 10 ** 12, s, None)["fives"], 0)

    def test_stats_without_a_known_group_and_with_pity_above_the_hard_cap(self):
        s = settings_mod.DEFAULTS
        d = {"standard": self.derive(GENSHIN, "standard", pity_plan(5, [10, 95], 4))}
        for keys in ([], ["nope"]):
            self.assertEqual(engine.stats(GENSHIN, d, keys, 0, s, None)["bins"], [], keys)         # used to raise ValueError (max of nothing)
        st = engine.stats(GENSHIN, d, ["standard"], 0, s, None)
        self.assertEqual(sum(b["n"] for b in st["bins"]), st["fives"])                         # the 5-star with pity 95 is in a bin


class TrackerTest(Base):
    """A made-up Genshin account: character banner 5-stars at pity 77 (Mona), 62, 31, 88 (Diluc), 80, then 62 pulls without one."""

    def setUp(self):
        super().setUp()
        self.plan = [77, 62, 31, 88, 80]
        ranks = pity_plan(5, self.plan, 62)
        names, i = {}, 0
        for p, nm in zip(self.plan, ["Mona", "Feat A", "Feat B", "Diluc", "Feat C"]):
            i += p
            names[i - 1] = nm
        self.char = seq(GENSHIN, "", ranks, names, codes=["301", "400"], t0=T0)
        self.weapon = seq(GENSHIN, "302", pity_plan(5, [62, 47], 31), start_id=1_700_000_100_000_000_000, t0=T0 + 10)
        self.add(GENSHIN, self.char + self.weapon)
        self.store.touch("genshin", "800000001", synced=True, now=self.now - 600)
        self.t = self.tracker()

    def test_state(self):
        st = self.t.state()
        self.assertTrue(st["synced"])
        self.assertEqual(st["accounts"], [{"game": "genshin", "uid": "800000001", "region": "Asia", "nickname": ""}])
        self.assertEqual(st["lastSync"], {"genshin": engine.iso(self.now - 600)})
        self.assertEqual(self.tracker().state()["accounts"][0]["game"], "genshin")

    def test_dashboard(self):
        d = self.t.dashboard("genshin")
        self.assertFalse(d["empty"])
        by = {g["key"]: g for g in d["groups"]}
        self.assertEqual(list(by), ["character", "weapon", "standard"])                    # the Beginner banner is not a group of the screen
        self.assertEqual((by["character"]["pity"], by["character"]["guarantee"], by["character"]["pulls"]), (62, False, sum(self.plan) + 62))
        self.assertEqual(by["character"]["extra"], {"radiance": 1})            # the last 50/50 was lost; the guaranteed 5-star after it is not a 50/50
        self.assertEqual((by["weapon"]["pity"], by["weapon"]["guarantee"], by["weapon"]["fives"]), (31, None, 2))       # no banner data and no list for weapons: unknown
        self.assertEqual((by["standard"]["pulls"], by["standard"]["lowerBound"]), (0, False))
        self.assertTrue(by["character"]["lowerBound"] is False)
        s = d["summary"]
        self.assertEqual((s["fives"], s["pulls"], s["currency"]), (7, sum(self.plan) + 62 + 62 + 47 + 31, "Primogems"))
        self.assertEqual(s["spent"], s["pulls"] * 160)
        self.assertEqual(d["oldest"], engine.iso(T0))
        self.assertEqual(d["bannerSource"]["label"], "bundled snapshot")
        self.assertEqual(self.t.dashboard("hsr"), {"game": "hsr", "empty": True})

    def test_a_group_with_pulls_and_no_5_star_shows_a_lower_bound(self):
        self.add(HSR, seq(HSR, "1", [3] * 12), uid="800000009")
        by = {g["key"]: g for g in self.t.dashboard("hsr")["groups"]}
        self.assertEqual((by["standard"]["pity"], by["standard"]["lowerBound"]), (12, True))

    def test_beginner_banner_is_not_counted_but_is_reported_done(self):
        self.add(GENSHIN, seq(GENSHIN, "100", [3] * 20, start_id=1_600_000_000_000_000_000))
        d = self.t.dashboard("genshin")
        by = {g["key"]: g for g in d["groups"]}
        self.assertEqual(by["standard"]["extra"], {"done": "Beginner Wish: completed"})
        self.assertEqual(d["summary"]["pulls"], sum(self.plan) + 62 + 62 + 47 + 31)
        self.assertEqual(d["oldest"], engine.iso(T0))

    def test_history_filters_sorts_and_pages(self):
        h = self.t.history("genshin", {"group": "character", "rarity": "5", "sort": {"col": "time", "dir": "asc"}})
        self.assertEqual([r["name"] for r in h["rows"]], ["Mona", "Feat A", "Feat B", "Diluc", "Feat C"])
        self.assertEqual([r["pity"] for r in h["rows"]], self.plan)
        self.assertEqual([r["result"]["text"] for r in h["rows"]], ["LOST 50/50", "WON guaranteed", "WON 50/50", "LOST 50/50", "WON guaranteed"])
        self.assertEqual([r["n"] for r in h["rows"]][:2], [77, 139])
        self.assertTrue(h["hasLowerBound"])
        self.assertFalse(h["hasAssumed"])
        self.assertEqual(h["rows"][0]["lowerBound"], True)
        self.assertEqual(h["rows"][0]["time"], engine.iso(T0 + 76 * 60))
        h = self.t.history("genshin", {"rarity": "5", "sort": {"col": "pity", "dir": "desc"}, "pageSize": 3, "page": 2})
        self.assertEqual((h["total"], h["pages"], h["page"]), (7, 3, 2))
        pit = [r["pity"] for r in self.t.history("genshin", {"rarity": "5", "sort": {"col": "pity", "dir": "desc"}, "pageSize": 50})["rows"]]
        self.assertEqual(pit, sorted(pit, reverse=True))
        self.assertEqual(self.t.history("genshin", {"page": 99, "pageSize": 3})["page"], 3)
        self.assertEqual(self.t.history("genshin", {"name": "feat a", "rarity": "5"})["total"], 1)
        self.assertEqual(self.t.history("genshin", {"rarity": "4", "group": "weapon"})["total"], 0)
        everything = self.t.history("genshin", {"rarity": "45", "include3": True, "pageSize": 1000})
        self.assertEqual(everything["total"], sum(self.plan) + 62 + 62 + 47 + 31)
        self.assertEqual(self.t.history("hsr")["empty"], True)

    def test_history_date_filter_uses_the_time_mode(self):
        day = "2026-09-01"            # all pulls start 2026-09-01 00:00:00 UTC and run for about 5 hours
        s = self.t.settings()
        s["times"] = "UTC"
        self.t.save_settings(s)
        self.assertEqual(self.t.history("genshin", {"rarity": "5", "from": day, "to": day})["total"], 7)
        self.assertEqual(self.t.history("genshin", {"rarity": "5", "from": "2026-09-02"})["total"], 0)
        s["times"] = "Server"                    # UTC+8: 2026-09-01 00:00 UTC is 08:00 on the same day, the end of the run is the same day too
        self.t.save_settings(s)
        self.assertEqual(self.t.history("genshin", {"rarity": "5", "from": day, "to": day})["total"], 7)
        self.assertEqual(self.t.history("genshin", {"rarity": "5", "to": "2026-08-31"})["total"], 0)

    def test_equal_values_sort_newer_first(self):
        recs = seq(GENSHIN, "200", [4, 4, 4], uid="x", t0=T0)
        self.add(GENSHIN, [{**r, "time": T0} for r in recs], uid="800000007")
        self.store.touch("genshin", "800000007", now=self.now)
        names = [r["n"] for r in self.t.history("genshin", {"group": "standard", "rarity": "4", "sort": {"col": "rarity", "dir": "asc"}})["rows"]]
        self.assertEqual(names, [3, 2, 1])

    def test_history_export_is_csv_with_every_filtered_row(self):
        res = self.t.history_csv("genshin", {"group": "character", "rarity": "5", "sort": {"col": "time", "dir": "asc"}})
        rows = list(csv.reader(io.StringIO(res["text"])))
        self.assertEqual(rows[0], ["number", "time", "name", "rarity", "banner", "pity", "result"])
        self.assertEqual([r[2] for r in rows[1:]], ["Mona", "Feat A", "Feat B", "Diluc", "Feat C"])
        self.assertEqual(res["count"], 5)
        self.assertTrue(res["name"].startswith("history_genshin_") and res["name"].endswith(".csv"))

    def test_stats(self):
        s = self.t.stats("genshin", {"group": "character", "dates": "all"})
        self.assertFalse(s["empty"])
        self.assertEqual((s["pulls"], s["fives"], s["best"], s["worst"]), (sum(self.plan) + 62, 5, 31, 88))
        self.assertAlmostEqual(s["avg"], sum(self.plan) / 5)
        self.assertTrue(s["avgLowerBound"])
        self.assertEqual(s["grade"], "B")                                                  # the average pity is 67.6
        self.assertEqual([(f["label"], f["won"], f["lost"]) for f in s["flips"]], [("Character Event", 1, 2)])           # the guaranteed ones are not flips
        self.assertEqual(s["official"][0]["kind"], "character")
        self.assertEqual(s["pullCost"], 160)
        self.assertEqual(self.t.stats("genshin", {"dates": "30d"})["pulls"], 0)          # the pulls are 90 days old
        self.assertEqual(self.t.stats("genshin", {"dates": "12m"})["pulls"], self.t.stats("genshin")["pulls"])
        self.assertEqual(self.t.stats("genshin", {"group": "bogus"}), {"empty": True})
        self.assertEqual(self.t.stats("zzz"), {"empty": True})

    def test_the_banner_history_decides_a_5050_and_gives_past_banners(self):
        slides = {"genshin": [
            {"id": "a", "group": "character", "kind": "character", "bannerName": "Character Event Wish", "version": "7.0", "start": "2026-08-30T10:00:00Z",
             "end": "2026-09-20T06:59:00Z", "name": "Mona", "rank": 5, "icon": "assets/art/genshin/m.png", "iconRemote": "https://x/m.png",
             "rateUp": [{"name": "Bennett", "icon": None, "iconRemote": "https://x/b.png"}]},
            {"id": "b", "group": "weapon", "kind": "weapon", "bannerName": "Weapon Event Wish", "version": "7.0", "start": "2026-08-30T10:00:00Z",
             "end": "2026-09-20T06:59:00Z", "name": "Some Sword", "rank": 5, "rateUp": []},
            {"id": "c", "group": "character", "kind": "character", "bannerName": "Character Event Wish", "version": "7.5", "start": "2026-12-01T10:00:00Z",
             "end": "2026-12-20T06:59:00Z", "name": "Later", "rank": 5, "rateUp": []}]}
        self.banners(slides)
        t = self.tracker()
        h = t.history("genshin", {"group": "character", "rarity": "5", "sort": {"col": "time", "dir": "asc"}})
        # Mona is featured in the window of that banner, so the first 5-star is a won 50/50 (assumed) and the rest follows from it
        self.assertEqual([r["result"]["kind"] for r in h["rows"]][:2], ["won", "lost"])
        self.assertTrue(h["rows"][0]["result"]["assumed"])
        self.assertTrue(h["hasAssumed"])
        past = t.past_banners("genshin")
        self.assertEqual([(p["title"], p["version"], p["group"]) for p in past], [("Character Event Wish", "7.0", "character"), ("Weapon Event Wish", "7.0", "weapon")])
        self.assertEqual(past[0]["items"], [{"name": "Mona", "rank": 5, "icon": "assets/art/genshin/m.png", "iconRemote": "https://x/m.png"}])
        self.assertEqual(past[0]["rateUp"][0]["name"], "Bennett")
        self.assertEqual(past[0]["start"], "2026-08-30T10:00:00.000Z")
        self.assertEqual(t.dashboard("genshin")["bannerSource"], {"label": "public calendar API", "at": "2026-10-25T14:17:00Z"})

    def test_banner_history_keeps_banners_that_a_newer_file_no_longer_has(self):
        self.banners({"genshin": [{"id": "a", "group": "character", "kind": "character", "bannerName": "Old", "version": "6.9", "start": "2026-08-01T00:00:00Z",
                                   "end": "2026-08-20T00:00:00Z", "name": "Mona", "rank": 5, "rateUp": []}]})
        t = self.tracker()
        self.assertEqual(len(t.past_banners("genshin")), 1)
        self.banners({"genshin": []}, generated="2026-10-30T00:00:00Z", path=self.root / "data" / "js" / "banners.js")
        self.assertEqual(t.refresh_banners(), 0)
        self.assertEqual(len(t.past_banners("genshin")), 1)

    def test_window_stats_and_pulls(self):
        a, b = engine.iso(T0), engine.iso(T0 + 199 * 60)             # the window ends 60 s later than its end (WINDOW_SLACK): pull 200 is inside
        st = self.t.window_stats("genshin", [{"id": "w1", "group": "character", "start": a, "end": b}, {"id": "w2", "group": "bogus", "start": a, "end": b}])
        self.assertEqual(st["w2"], None)
        self.assertEqual(st["w1"]["pulls"], 201)
        self.assertEqual(st["w1"]["fives"], 3)                                              # 5-stars at pulls 77, 139 and 170
        self.assertEqual(st["w1"]["sub"][1] + st["w1"]["sub"][2], 201)
        self.assertGreater(st["w1"]["sub"][1], 0)
        self.assertGreater(st["w1"]["sub"][2], 0)
        w = self.t.window_pulls("genshin", "character", a, b, 3)
        self.assertEqual(w["total"], sum(1 for r in self.t.history("genshin", {"group": "character", "rarity": "45", "pageSize": 1000})["rows"] if r["time"] <= b))
        self.assertEqual(len(w["rows"]), 3)
        self.assertEqual([r["time"] for r in w["rows"]], sorted((r["time"] for r in w["rows"]), reverse=True))
        self.assertEqual(self.t.window_stats("genshin", [{"id": "w", "group": "weapon", "start": a, "end": None}])["w"]["sub"], None)
        self.assertEqual(self.t.window_stats("hsr", [{"id": "w", "group": "character", "start": a, "end": b}]), {"w": None})

    def test_the_hsr_loss_pool_setting_changes_the_results(self):
        recs = seq(HSR, "11", pity_plan(5, [40, 30], 3), names={39: "Himeko", 69: "Someone"})
        self.add(HSR, recs, uid="600000001")
        t = self.tracker()
        q = {"rarity": "5", "sort": {"col": "time", "dir": "asc"}}
        h = t.history("hsr", q)
        self.assertEqual([r["result"]["text"] for r in h["rows"]], ["LOST 50/50", "WON guaranteed"])         # Himeko is in the default loss pool
        s = t.settings()
        s["lossPool"] = ["Welt"]
        t.save_settings(s)
        h = t.history("hsr", q)
        self.assertEqual([r["result"]["text"] for r in h["rows"]], ["WON 50/50", "WON 50/50"])             # nothing is derived from stored results
        self.assertEqual([r["result"]["assumed"] for r in h["rows"]], [True, False])
        self.assertEqual(t.dashboard("hsr")["groups"][0]["guarantee"], False)

    def test_the_hsr_light_cone_banner_uses_the_standard_list_when_no_banner_data_covers_the_pull(self):
        """Before, every Light Cone 5-star older than the banner data had no result and the guarantee was unknown (the Character Warp has the loss pool)."""
        plan = pity_plan(5, [40, 30, 20, 10], 3)
        # a Light Cone that is not in the list is the featured one; one that is in the list (here with a curly apostrophe) was lost
        names = {39: "Brilliant Fixation", 69: "But the Battle Isn’t Over", 89: "Another Featured", 99: "Sleep Like the Dead"}
        self.add(HSR, seq(HSR, "12", plan, names=names), uid="600000001")
        t = self.tracker()
        h = t.history("hsr", {"group": "lightcone", "rarity": "5", "sort": {"col": "time", "dir": "asc"}})
        self.assertEqual([r["result"]["text"] if r["result"] else None for r in h["rows"]],
                         ["WON 75/25", "LOST 75/25", "WON guaranteed", "LOST 75/25"])
        group = next(g for g in t.dashboard("hsr")["groups"] if g["key"] == "lightcone")
        self.assertEqual((group["pity"], group["guarantee"]), (3, True))          # the last 5-star was lost: the next one is guaranteed

    def test_rules_json_corrects_the_hsr_light_cone_list(self):
        (self.root / "data").mkdir(exist_ok=True)
        (self.root / "data" / "rules.json").write_text(json.dumps({"standard5": {"hsr_lightcone": ["Brilliant Fixation"]}}), encoding="utf-8")
        with mock.patch.dict("pt.games.STANDARD_5", {}, clear=False):
            self.add(HSR, seq(HSR, "12", pity_plan(5, [40, 30], 3), names={39: "Night on the Milky Way", 69: "Brilliant Fixation"}), uid="600000001")
            t = self.tracker()
            h = t.history("hsr", {"group": "lightcone", "rarity": "5", "sort": {"col": "time", "dir": "asc"}})
            # the list now holds only Brilliant Fixation: Night on the Milky Way is "featured" (a won 50-50 assumed: the guarantee was unknown), then a loss
            self.assertEqual([r["result"]["kind"] for r in h["rows"]], ["won", "lost"])

    def test_rules_json_corrects_the_standard_5_star_list(self):
        (self.root / "data").mkdir(exist_ok=True)
        (self.root / "data" / "rules.json").write_text(json.dumps({"standard5": {"genshin": ["Feat A"]}}), encoding="utf-8")
        with mock.patch.dict("pt.games.STANDARD_5", {}, clear=False):
            t = self.tracker()
            h = t.history("genshin", {"group": "character", "rarity": "5", "sort": {"col": "time", "dir": "asc"}})
            # Mona and Diluc are now "featured", Feat A is the standard 5-star: lost, then guaranteed, ...
            self.assertEqual(h["rows"][0]["name"], "Mona")
            self.assertEqual(h["rows"][0]["result"]["kind"], "won")
            self.assertEqual(h["rows"][1]["result"]["kind"], "lost")           # Feat A, now in the list, on a 50/50
            self.assertEqual(h["rows"][2]["result"]["kind"], "wonG")

    def test_nickname_and_removal(self):
        self.assertTrue(self.t.save_nickname("genshin", "800000001", "  Main  "))
        self.assertEqual(self.t.state()["accounts"][0]["nickname"], "Main")
        self.assertFalse(self.t.remove_account("genshin", "nobody"))
        self.assertTrue(self.t.remove_account("genshin", "800000001"))
        self.assertEqual(self.t.dashboard("genshin"), {"game": "genshin", "empty": True})
        self.assertFalse(self.t.state()["synced"])


    def test_banner_data_of_the_wrong_shape_is_skipped_not_fatal(self):
        self.banners(["not", "a", "dict"], path=self.root / "data" / "js" / "banners.js")             # valid JSON, wrong shape
        with self.assertLogs("pt.tracker", level="WARNING"):
            t = self.tracker()
        self.assertEqual(t.refresh_banners(), 0)
        self.assertTrue(t.state()["synced"])

    def test_the_newest_banner_data_wins_even_when_it_is_the_shipped_one(self):
        def slides(name):
            return {"genshin": [{"group": "character", "bannerName": "B", "start": "2026-01-01T00:00:00Z", "end": "2026-01-20T00:00:00Z",
                                 "name": name, "rank": 5, "kind": "character"}]}
        self.banners(slides("NewName"), generated="2026-02-01T00:00:00Z")                                    # shipped, newer
        self.banners(slides("OldName"), generated="2026-01-05T00:00:00Z", path=self.root / "data" / "js" / "banners.js")    # stale data file
        store = Store(self.root / "b2" / "pulls.sqlite")
        self.addCleanup(store.close)
        Tracker(store, data_dir=self.root / "data", ui_dir=self.ui, clock=lambda: self.now)
        self.assertEqual([i["name"] for b in store.banners("genshin") for i in b["items"]], ["NewName"])

    def test_a_window_item_without_an_id_does_not_stop_the_other_items(self):
        out = self.tracker().window_stats("genshin", [{"group": "character"}, {"id": "a", "group": "nope"}])
        self.assertEqual(set(out), {None, "a"})                       # the item without an id is answered under None, the next one still is
        self.assertIsNone(out["a"])
        self.assertEqual(out[None]["pulls"], 400)


class ImportExportTest(Base):
    def setUp(self):
        super().setUp()
        self.recs = seq(GENSHIN, "301", pity_plan(5, [10, 20], 5), codes=["301", "400"])
        self.add(GENSHIN, self.recs[:20])
        self.t = self.tracker()

    def export_text(self, recs=None, uid="800000001", offset=8):
        rows = [{**r, "raw": "{}"} for r in (recs or self.recs)]
        return json.dumps(pulls.export_uigf(GENSHIN, uid, rows, offset, now=1))

    def test_preview_counts_new_and_stored(self):
        p = self.t.import_preview("x.json", self.export_text())
        self.assertTrue(p["ok"])
        self.assertEqual((p["game"], p["uid"], p["records"], p["newRecords"], p["stored"]), ("genshin", "800000001", 35, 15, 20))
        self.assertEqual((p["conflicts"], p["mismatch"], p["format"]), ([], None, "UIGF v4.0"))

    def test_apply_adds_only_the_new_pulls(self):
        p = self.t.import_preview("x.json", self.export_text())
        self.assertEqual(self.t.import_apply(p, "keep")["added"], 15)
        self.assertEqual(self.store.count("genshin", "800000001"), 35)
        self.assertEqual(self.t.import_apply(p, "keep")["error"].startswith("The file preview"), True)           # one preview, one apply
        again = self.t.import_preview("x.json", self.export_text())
        self.assertEqual((again["newRecords"], again["stored"]), (0, 35))

    def test_conflict_keep_or_use_file(self):
        changed = [{**r, "time": r["time"] + 3600} if i == 3 else r for i, r in enumerate(self.recs)]
        p = self.t.import_preview("x.json", self.export_text(changed))
        self.assertEqual(p["conflicts"], [{"id": self.recs[3]["id"], "reason": "same ID, time differs"}])
        self.assertEqual(p["stored"], 19)
        self.t.import_apply(p, "keep")
        self.assertEqual(self.store.existing("genshin", "800000001", [self.recs[3]["id"]])[self.recs[3]["id"]][0], self.recs[3]["time"])
        p = self.t.import_preview("x.json", self.export_text(changed))
        self.t.import_apply(p, "file")
        self.assertEqual(self.store.existing("genshin", "800000001", [self.recs[3]["id"]])[self.recs[3]["id"]][0], self.recs[3]["time"] + 3600)

    def test_a_file_with_a_placeholder_pull_time_does_not_break_the_screens(self):
        text = json.loads(self.export_text())
        text["hk4e"][0]["list"].append({**text["hk4e"][0]["list"][0], "id": "1700000000000000999", "time": "0001-01-01 00:00:00", "rank_type": "5"})
        p = self.t.import_preview("x.json", json.dumps(text))
        self.assertEqual((p["records"], p["newRecords"]), (35, 15))                      # the 36th record is skipped, not stored
        self.assertIn("1 records", p["note"])
        self.t.import_apply(p, "keep")
        self.assertEqual(self.store.count("genshin", "800000001"), 35)
        self.assertFalse(self.t.dashboard("genshin")["empty"])                           # engine.iso(oldest) would raise for the placeholder
        self.assertEqual(self.t.history("genshin", {"rarity": "45"})["total"], 2)         # the two 5-stars of the plan

    def test_the_mismatch_check_does_not_depend_on_the_preview_the_page_sends_back(self):
        p = self.t.import_preview("x.json", self.export_text(uid="800999999"))
        self.assertTrue(p["mismatch"])
        self.assertIn("error", self.t.import_apply({**p, "mismatch": None}, "keep"))            # a copy that says "no mismatch" changes nothing
        self.assertEqual(self.store.count("genshin", "800999999"), 0)

    def test_the_history_csv_writes_names_that_look_like_formulas_as_text(self):
        self.add(GENSHIN, seq(GENSHIN, "301", [5], names={0: "=HYPERLINK(1)"}, start_id=1_700_000_900_000_000_000))
        text = self.t.history_csv("genshin", {"rarity": "5"})["text"]
        names = [row[2] for row in csv.reader(io.StringIO(text))][1:]
        self.assertIn("'=HYPERLINK(1)", names)
        self.assertFalse([n for n in names if n.startswith(("=", "+", "-", "@"))])

    def test_another_uid_is_blocked_for_a_game_with_an_account(self):
        p = self.t.import_preview("x.json", self.export_text(uid="800999999"))
        self.assertEqual(p["mismatch"], {"found": "800999999", "active": "800000001"})
        self.assertIn("error", self.t.import_apply(p, "keep"))
        self.assertEqual(self.store.count("genshin", "800999999"), 0)

    def test_a_file_for_a_game_without_an_account_creates_it(self):
        recs = seq(ZZZ, "2", pity_plan(4, [12], 3))
        text = json.dumps(pulls.export_uigf(ZZZ, "1300000001", [{**r, "raw": "{}"} for r in recs], 8, now=1))
        p = self.t.import_preview("z.json", text)
        self.assertEqual((p["game"], p["mismatch"], p["newRecords"]), ("zzz", None, 15))
        self.t.import_apply(p, "keep")
        acct = self.store.active("zzz")
        self.assertEqual((acct["uid"], acct["region"], acct["utc_offset"]), ("1300000001", "Asia", 8))
        self.assertEqual(self.t.history("zzz", {"rarity": "5"})["rows"][0]["rarity"], 5)

    def test_a_file_that_is_not_readable(self):
        p = self.t.import_preview("x.json", "hello")
        self.assertFalse(p["ok"])
        self.assertIn("not a UIGF v4", p["error"])
        self.assertIn("error", self.t.import_preview("x.csv", "a,b\n"))              # a CSV with no game given

    def test_csv_import_goes_to_the_active_account(self):
        text = self.t.export("genshin", "csv", "all")["text"]
        self.store.remove_account("genshin", "800000001")
        self.add(GENSHIN, self.recs[:5])
        p = self.t.import_preview("e.csv", text, "genshin")
        self.assertTrue(p["ok"], p)
        self.assertEqual((p["format"], p["records"], p["stored"], p["newRecords"]), ("CSV", 20, 5, 15))

    def test_export_formats(self):
        e = self.t.export("genshin", "uigf", "all")
        doc = json.loads(e["text"])
        self.assertEqual((e["count"], len(doc["hk4e"][0]["list"]), doc["hk4e"][0]["uid"]), (20, 20, "800000001"))
        self.assertTrue(e["name"].startswith("uigf_genshin_") and e["name"].endswith(".json"))
        self.assertEqual(self.t.export("genshin", "uigf", "30d")["count"], 0)                      # the pulls are 90 days old
        self.assertEqual(self.t.export("hsr", "uigf", "all"), {"empty": True})
        csv_rows = list(csv.DictReader(io.StringIO(self.t.export("genshin", "csv", "all")["text"])))
        self.assertEqual(len(csv_rows), 20)
        self.assertEqual(csv_rows[0]["id"], self.recs[0]["id"])


class FakeWindow:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def create_file_dialog(self, kind, **kw):
        self.calls.append((kind, kw))
        return self.answers.pop(0) if self.answers else None


class ApiTest(Base):
    def setUp(self):
        super().setUp()
        self.add(GENSHIN, seq(GENSHIN, "301", pity_plan(5, [30], 4), names={29: "Feat"}))
        self.api = PullsApi(self.tracker())

    def test_methods_answer_like_the_tracker(self):
        self.assertTrue(self.api.state()["synced"])
        self.assertFalse(self.api.dashboard("genshin")["empty"])
        self.assertEqual(self.api.history("genshin", {"rarity": "5"})["total"], 1)
        self.assertEqual(self.api.getSettings()["times"], "Local")

    def test_an_exception_is_marked_as_failed_but_a_refusal_with_an_error_text_is_not(self):
        failed = self.api.dashboard("nope")                  # the page raises an answer with "failed": the error is shown, not an empty history
        self.assertIs(failed["failed"], True)
        self.assertTrue(failed["empty"] and "ValueError" in failed["error"])
        self.assertIs(self.api.exportRun({"game": "nope"})["failed"], True)
        refused = self.api.importPreview({"path": str(self.root / "nothing.json")})        # an answer of its own: the page shows its text
        self.assertIn("error", refused)
        self.assertNotIn("failed", refused)
        self.assertEqual(self.api.windowStats("nope", []), {})                              # a map the page indexes by id stays a map

    def test_a_failure_is_answered_not_raised(self):
        self.assertEqual(self.api.dashboard("nope")["empty"], True)
        self.assertIn("ValueError", self.api.dashboard("nope")["error"])
        self.assertEqual(self.api.pastBanners("nope"), [])
        self.assertEqual(self.api.windowStats("nope", []), {})
        self.assertFalse(self.api.removeAccount("nope", "1"))
        self.assertEqual(self.api.history("genshin", {"sort": 5, "rarity": "5"})["empty"], True)      # a wrong query shape is a logged error

    def test_save_settings_validates(self):
        s = self.api.getSettings()
        s["soft"]["genshin"]["character"] = 500
        s["times"] = "Mars"
        saved = self.api.saveSettings(s)
        self.assertEqual((saved["soft"]["genshin"]["character"], saved["times"]), (90, "Local"))
        self.assertEqual(json.loads((paths.data_dir() / "settings.json").read_text())["soft"]["genshin"]["character"], 90)
        self.assertEqual(Tracker(self.store, data_dir=self.root / "data", ui_dir=self.ui).settings()["soft"]["genshin"]["character"], 90)

    def test_non_finite_numbers_in_the_settings_file_give_the_defaults_for_those_values_only(self):
        # json.loads accepts Infinity and NaN, and int(float("inf")) raises OverflowError: that used to throw the whole file away
        raw = json.loads('{"soft": {"genshin": {"character": Infinity, "weapon": 70}}, "pityColors": {"green": NaN, "gold": 80}, "maskUid": false}')
        out = settings_mod.clean(raw)
        self.assertEqual(out["soft"]["genshin"], {"character": 74, "weapon": 70})
        self.assertEqual((out["pityColors"]["green"], out["pityColors"]["gold"]), (50, 80))
        self.assertFalse(out["maskUid"])

    def test_export_without_a_window_returns_the_text(self):
        r = self.api.exportRun({"game": "genshin", "format": "uigf", "scope": "account", "dates": "all"})
        self.assertEqual((r["count"], r["mime"]), (34, "application/json"))
        self.assertEqual(self.api.exportRun({"game": "hsr", "format": "csv"}), {"empty": True})
        self.assertEqual(self.api.pickImportFile(), {"browser": True})
        self.assertEqual(self.api.chooseFolder("genshin"), {"unavailable": True})

    def test_export_with_a_window_writes_the_chosen_file(self):
        target = self.root / "out" / "mine.json"
        target.parent.mkdir()
        self.api._window = FakeWindow([str(target)])
        r = self.api.exportRun({"game": "genshin", "format": "uigf", "dates": "all"})
        self.assertEqual((r["path"], r["name"], r["count"]), (str(target), "mine.json", 34))
        self.assertEqual(len(json.loads(target.read_text(encoding="utf-8"))["hk4e"][0]["list"]), 34)
        self.api._window = FakeWindow([None])
        self.assertEqual(self.api.exportRun({"game": "genshin", "format": "csv"}), {"cancelled": True})
        self.api._window = FakeWindow([str(self.root / "no" / "such" / "dir" / "x.csv")])
        self.assertIn("error", self.api.exportRun({"game": "genshin", "format": "csv"}))

    def test_import_by_path(self):
        f = self.root / "in.json"
        rows = [{**r, "raw": "{}"} for r in seq(GENSHIN, "301", [3, 3, 3], start_id=1_700_000_500_000_000_000)]
        f.write_text(json.dumps(pulls.export_uigf(GENSHIN, "800000001", rows, 8, now=1)), encoding="utf-8-sig")
        self.api._window = FakeWindow([[str(f)]])
        picked = self.api.pickImportFile()                      # only a path chosen in the dialog is read (the page cannot name a file)
        self.assertEqual(picked, {"path": str(f), "name": "in.json"})
        p = self.api.importPreview(picked)
        self.assertEqual((p["ok"], p["newRecords"]), (True, 3))
        self.assertEqual(self.api.importApply(p, "keep"), {"added": 3, "replaced": 0})

    def test_import_by_path_refuses_what_is_not_a_small_text_file(self):
        folder = self.root / "adir"
        folder.mkdir()
        binary = self.root / "bin.json"
        binary.write_bytes(b"\xff\xfe\x00\x80" * 10)
        big = self.root / "big.json"
        big.write_text("x" * 100, encoding="utf-8")
        def pick(path):
            self.api._window = FakeWindow([[str(path)]])
            return self.api.pickImportFile()

        for path in (folder, self.root / "nothing.json"):
            self.assertFalse(self.api.importPreview(pick(path))["ok"], path)
        self.assertEqual(self.api.importPreview(pick(binary))["error"], "The file is not text in UTF-8.")
        with mock.patch("pt.api.MAX_IMPORT_BYTES", 10):
            self.assertIn("larger", self.api.importPreview(pick(big))["error"])

    def test_an_export_that_fails_midway_leaves_the_chosen_file_as_it_was(self):
        target = self.root / "out" / "mine.json"
        target.parent.mkdir()
        target.write_text("the old file", encoding="utf-8")
        self.api._window = FakeWindow([str(target)])
        with mock.patch("pt.api.os.replace", side_effect=OSError("disk full")):
            r = self.api.exportRun({"game": "genshin", "format": "uigf", "dates": "all"})
        self.assertIn("error", r)
        self.assertEqual(target.read_text(encoding="utf-8"), "the old file")
        self.assertEqual([p.name for p in target.parent.iterdir()], ["mine.json"])              # no half-written temporary file either

    def test_clearing_the_image_cache_never_leaves_the_data_folder(self):
        outside = self.root / "elsewhere"
        outside.mkdir()
        (outside / "keep.png").write_bytes(b"x")
        art = paths.data_dir() / "assets" / "art"
        art.parent.mkdir(parents=True)
        try:
            art.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("no symbolic links here")
        self.assertIn("error", self.api.clearImageCache())
        self.assertTrue((outside / "keep.png").exists())

    def test_clearing_the_image_cache_never_deletes_the_data_folder_or_what_a_linked_assets_folder_leads_to(self):
        data = paths.data_dir()
        data.mkdir(parents=True, exist_ok=True)
        (data / "pulls.sqlite").write_bytes(b"the database")
        try:
            (data / "assets").mkdir()
            (data / "assets" / "art").symlink_to(data, target_is_directory=True)            # the image folder is the data folder itself
        except (OSError, NotImplementedError):
            self.skipTest("no symbolic links here")
        self.assertIn("error", self.api.clearImageCache())
        self.assertEqual((data / "pulls.sqlite").read_bytes(), b"the database")
        (data / "assets" / "art").unlink()
        (data / "assets").rmdir()
        elsewhere = self.root / "pictures"
        (elsewhere / "art").mkdir(parents=True)
        (elsewhere / "art" / "mine.png").write_bytes(b"x")
        (data / "assets").symlink_to(elsewhere, target_is_directory=True)                  # <data>/assets leads out of the data folder
        self.assertIn("error", self.api.clearImageCache())
        self.assertTrue((elsewhere / "art" / "mine.png").exists())

    def test_image_cache(self):
        art = paths.data_dir() / "assets" / "art" / "genshin"
        art.mkdir(parents=True)
        (art / "a.png").write_bytes(b"x" * 1_500_000)
        self.assertEqual(self.api.storage()["imageCacheMB"], 1.5)
        self.assertEqual(self.api.clearImageCache(), {"cleared": 1})
        self.assertEqual(self.api.storage()["imageCacheMB"], 0)

    def test_the_storage_figure_survives_an_image_file_that_is_gone_when_it_is_measured(self):
        art = paths.data_dir() / "assets" / "art" / "genshin"
        art.mkdir(parents=True)
        (art / "a.png").write_bytes(b"x" * 1_500_000)
        (art / "gone.png").write_bytes(b"x")
        real_stat, real_is_file = Path.stat, Path.is_file

        def stat(self, *a, **k):                    # the file is listed, then another program removes it
            if self.name == "gone.png":
                raise FileNotFoundError(self.name)
            return real_stat(self, *a, **k)

        def is_file(self):
            return True if self.name == "gone.png" else real_is_file(self)
        with mock.patch.object(Path, "stat", stat), mock.patch.object(Path, "is_file", is_file):
            info = self.api.storage()
        self.assertNotIn("failed", info)               # js/api.js raises a failed answer, and the Settings screen then does not open
        self.assertEqual(info["imageCacheMB"], 1.5)

    def test_the_database_opens_at_the_first_use(self):
        api = PullsApi()
        self.assertIsNone(api._tracker_obj)
        self.addCleanup(lambda: api._tracker_obj.store.close())
        self.assertEqual(api.state(), {"synced": True, "accounts": [{"game": "genshin", "uid": "800000001", "region": "Asia", "nickname": ""}], "lastSync": {}})


class RegionTest(unittest.TestCase):
    def test_regions(self):
        self.assertEqual(region_of("genshin", "800000001"), ("Asia", 8))
        self.assertEqual(region_of("genshin", "600000001"), ("America", -5))
        self.assertEqual(region_of("genshin", "700000001"), ("Europe", 1))
        self.assertEqual(region_of("zzz", "1300000001"), ("Asia", 8))
        self.assertEqual(region_of("zzz", "1000000001"), ("America", -5))
        self.assertEqual(region_of("hsr", "42"), ("Unknown", 8))


if __name__ == "__main__":
    unittest.main()
