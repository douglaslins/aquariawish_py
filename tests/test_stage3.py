"""Tests of account switching, the backups around destructive actions, and the 5-star timeline of the History screen."""
from __future__ import annotations

import json
import os
import sys
import time
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.pop("PULLS_TRACKER_DATA", None)      # a test must never touch a real data folder: each test that needs one sets its own

from pt import pulls                                           # noqa: E402
from pt.api import PullsApi                                    # noqa: E402
from pt.games import GENSHIN, HSR                              # noqa: E402
from test_backend import Base, T0, pity_plan, seq              # noqa: E402


class TwoAccountsTest(Base):
    def setUp(self):
        super().setUp()
        self.a = seq(GENSHIN, "301", pity_plan(5, [10, 20], 5), uid="800000001")
        self.b = seq(GENSHIN, "301", pity_plan(5, [30], 7), uid="600000009", start_id=1_800_000_000_000_000_000)
        self.add(GENSHIN, self.a, uid="800000001")
        self.add(GENSHIN, self.b, uid="600000009")
        self.t = self.tracker()

    def test_the_state_lists_the_shown_account_first(self):
        self.assertEqual([a["uid"] for a in self.t.state()["accounts"]], [a["uid"] for a in self.store.accounts("genshin")])
        self.assertEqual(self.t.active("genshin")["uid"], self.t.state()["accounts"][0]["uid"])

    def test_switching_shows_the_other_accounts_numbers_and_the_state_order_follows(self):
        self.t.set_active("genshin", "800000001")
        self.assertEqual(self.t.state()["accounts"][0]["uid"], "800000001")
        self.assertEqual(self.t.dashboard("genshin")["summary"]["pulls"], len(self.a))
        self.t.set_active("genshin", "600000009")
        self.assertEqual(self.t.state()["accounts"][0]["uid"], "600000009")
        self.assertEqual(self.t.dashboard("genshin")["summary"]["pulls"], len(self.b))
        self.assertEqual([r["name"] for r in self.t.history("genshin", {"rarity": "5"})["rows"]][:1], [self.b[[i for i, r in enumerate(self.b) if r["rarity"] == 5][0]]["name"]])

    def test_switching_to_the_account_already_shown_changes_nothing_and_unknown_accounts_are_refused(self):
        shown = self.t.active("genshin")
        before = self.store.version
        self.assertTrue(self.t.set_active("genshin", shown["uid"]))
        self.assertEqual(self.store.version, before)
        self.assertFalse(self.t.set_active("genshin", "123"))
        self.assertFalse(self.t.set_active("nope", shown["uid"]))
        self.assertFalse(self.t.set_active("hsr", shown["uid"]))

    def test_switching_in_the_same_second_still_wins(self):
        other = [a["uid"] for a in self.store.accounts("genshin")][1]
        self.assertTrue(self.t.set_active("genshin", other))        # the clock of the test does not move
        self.assertEqual(self.t.active("genshin")["uid"], other)

    def test_through_the_api(self):
        api = PullsApi(self.t)
        other = [a["uid"] for a in self.store.accounts("genshin")][1]
        self.assertIs(api.setActiveAccount("genshin", other), True)
        self.assertIs(api.setActiveAccount("genshin", "nobody"), False)
        self.assertIs(api.setActiveAccount("not a game", other), False)
        self.assertEqual(api.state()["accounts"][0]["uid"], other)


class BackupTest(Base):
    def setUp(self):
        super().setUp()
        self.recs = seq(GENSHIN, "301", pity_plan(5, [10, 20], 5), codes=["301", "400"])
        self.add(GENSHIN, self.recs[:20])
        self.add(HSR, seq(HSR, "11", pity_plan(5, [10], 3), uid="700000002"), uid="700000002")
        self.t = self.tracker()
        self.folder = self.t.backup_folder()

    def files(self):
        return sorted(self.folder.glob("pulls-*.sqlite")) if self.folder.is_dir() else []

    def test_removing_an_account_saves_a_copy_first_that_still_has_the_account(self):
        self.assertTrue(self.t.remove_account("hsr", "700000002"))
        self.assertEqual(self.store.count("hsr", "700000002"), 0)
        files = self.files()
        self.assertEqual(len(files), 1)
        import sqlite3
        db = sqlite3.connect(str(files[0]))
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM pulls WHERE game='hsr'").fetchone()[0], 13)
        finally:
            db.close()

    def test_rotation_never_deletes_a_file_the_program_did_not_write(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        others = [self.folder / n for n in ("pulls-notes.sqlite", "pulls-1.sqlite", "pulls-2024-01-01.sqlite", "pulls-20240101-000000.sqlite.bak", "thesis.docx")]
        for o in others:
            o.write_bytes(b"mine")
        self.t.BACKUP_KEEP = 1
        for _ in range(3):
            self.t.backup(force=True)
            self.now += 5
        self.assertEqual([o.read_bytes() for o in others], [b"mine"] * len(others))
        self.assertEqual(len(self.t.backup_files()), 1)                              # only its own backups were counted and rotated
        self.assertEqual(len([p for p in self.folder.iterdir() if p not in others]), 1)

    def test_a_second_removal_right_after_is_saved_too(self):
        self.t.remove_account("hsr", "700000002")
        self.now += 2
        self.t.remove_account("genshin", "800000001")
        self.assertEqual(len(self.files()), 2)

    def test_no_copy_when_automatic_backup_is_off_or_the_account_is_unknown(self):
        s = self.t.settings()
        s["autoBackup"] = False
        self.t.save_settings(s)
        self.t.remove_account("hsr", "700000002")
        self.assertEqual(self.files(), [])
        s["autoBackup"] = True
        self.t.save_settings(s)
        self.assertFalse(self.t.remove_account("hsr", "999"))
        self.assertEqual(self.files(), [])

    def test_a_backup_that_cannot_be_written_does_not_stop_the_removal(self):
        blocked = self.root / "blocked"
        blocked.write_text("a file, not a folder")
        s = self.t.settings()
        s["backupFolder"] = str(blocked / "sub")
        self.t.save_settings(s)
        with self.assertLogs("pt.tracker", level="ERROR"):
            self.assertTrue(self.t.remove_account("hsr", "700000002"))
        self.assertEqual(self.store.count("hsr", "700000002"), 0)

    def test_an_import_that_replaces_pulls_saves_a_copy_but_a_plain_import_does_not(self):
        rows = [{**r, "raw": "{}"} for r in self.recs]
        text = json.dumps(pulls.export_uigf(GENSHIN, "800000001", rows, 8, now=1))
        p = self.t.import_preview("x.json", text)
        self.t.import_apply(p, "keep")
        self.assertEqual(self.files(), [])
        changed = [{**r, "time": r["time"] + 3600} if i == 3 else r for i, r in enumerate(self.recs)]
        text = json.dumps(pulls.export_uigf(GENSHIN, "800000001", [{**r, "raw": "{}"} for r in changed], 8, now=1))
        p = self.t.import_preview("x.json", text)
        self.assertTrue(p["conflicts"])
        self.t.import_apply(p, "keep")
        self.assertEqual(self.files(), [])                            # conflicts kept as stored: nothing is replaced
        p = self.t.import_preview("x.json", text)
        self.t.import_apply(p, "file")
        self.assertEqual(len(self.files()), 1)

    def test_info_and_backup_now(self):
        self.assertEqual(self.t.backup_info(), {"folder": str(self.folder), "count": 0, "mb": 0.0, "newest": None})
        api = PullsApi(self.t)
        res = api.backupNow()
        self.assertTrue(res["name"].startswith("pulls-") and res["name"].endswith(".sqlite"))
        info = api.storage()["backups"]
        self.assertEqual((info["count"], info["folder"]), (1, str(self.folder)))
        self.assertIsNotNone(info["newest"])
        api.backupNow()                                               # same second: the same file name, not a second file
        self.assertEqual(api.storage()["backups"]["count"], 1)

    def test_the_backup_figures_survive_a_backup_that_is_gone_when_it_is_measured(self):
        self.t.backup(force=True)
        self.now += 5
        gone = self.t.backup(force=True)
        real_stat = Path.stat

        def stat(self, *a, **k):                    # listed, then removed by a sync program or by the person
            if self == gone:
                raise FileNotFoundError(self.name)
            return real_stat(self, *a, **k)
        listed = self.t.backup_files()
        with mock.patch.object(self.t, "backup_files", return_value=listed), mock.patch.object(Path, "stat", stat):
            info = self.t.backup_info()
        self.assertEqual(info["count"], 2)
        self.assertIsNotNone(info["newest"])           # the older backup is still measured

    def test_backup_now_with_nothing_stored(self):
        for a in self.store.accounts():
            self.store.remove_account(a["game"], a["uid"])
        self.assertEqual(PullsApi(self.t).backupNow(), {"empty": True})

    def test_only_the_newest_ten_are_kept(self):
        for i in range(13):
            self.now += 1
            self.t.backup(force=True)
        self.assertEqual(len(self.files()), 10)
        self.assertEqual(self.t.backup_info()["count"], 10)


class TimelineTest(Base):
    def setUp(self):
        super().setUp()
        names = {9: "Mona", 29: "Skirk", 59: "Diluc"}
        self.recs = seq(GENSHIN, "", pity_plan(5, [10, 20, 30], 8), names, codes=["301", "400"])
        self.add(GENSHIN, self.recs)
        self.add(GENSHIN, seq(GENSHIN, "302", pity_plan(5, [15], 4), start_id=1_750_000_000_000_000_000, t0=T0 + 100000))
        self.t = self.tracker()

    def test_all_five_stars_of_the_filter_not_only_the_page_oldest_first(self):
        res = self.t.history("genshin", {"rarity": "5", "pageSize": 1})
        self.assertEqual(len(res["rows"]), 1)
        tl = res["timeline"]
        self.assertEqual(len(tl), 4)
        self.assertEqual([x["time"] for x in tl], sorted(x["time"] for x in tl))
        self.assertEqual([x["pity"] for x in tl if x["group"] == "character"], [10, 20, 30])
        self.assertEqual(set(tl[0]), {"n", "time", "name", "pity", "lowerBound", "group", "kind"})
        self.assertTrue(tl[0]["lowerBound"])

    def test_the_filters_apply_to_the_timeline_and_four_stars_never_enter_it(self):
        self.assertEqual(len(self.t.history("genshin", {"rarity": "5", "group": "weapon"})["timeline"]), 1)
        self.assertEqual(self.t.history("genshin", {"rarity": "4"})["timeline"], [])
        both = self.t.history("genshin", {"rarity": "45", "include3": True})
        self.assertEqual(len(both["timeline"]), 4)
        self.assertEqual(self.t.history("genshin", {"rarity": "5", "name": "mona"})["timeline"][0]["name"], "Mona")

    def test_results_come_through_and_the_sort_order_does_not_change_the_timeline(self):
        a = self.t.history("genshin", {"rarity": "5", "sort": {"col": "pity", "dir": "asc"}})["timeline"]
        b = self.t.history("genshin", {"rarity": "5", "sort": {"col": "time", "dir": "desc"}})["timeline"]
        self.assertEqual(a, b)
        self.assertTrue(all(x["kind"] in (None, "won", "wonG", "lost") for x in a))

    def test_an_account_without_pulls_has_no_timeline(self):
        self.assertTrue(PullsApi(self.t).history("hsr", {})["empty"])


@unittest.skipUnless(hasattr(time, "tzset"), "the time zone of the process can be changed only where time.tzset exists (not on Windows)")
class ClockChangeTest(Base):
    """History date filter in Local mode on the two days a year when the clock changes (23 and 25 hours long). New York, 2026: the clock
    went forward on 8 March and back on 1 November."""

    def setUp(self):
        super().setUp()
        old = os.environ.get("TZ")

        def restore():
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()
        self.addCleanup(restore)
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        self.when = {k: int(datetime.fromisoformat(v).replace(tzinfo=timezone.utc).timestamp()) for k, v in {
            "fall_late": "2026-11-02T04:30:00",       # 23:30 on 1 November (EST): the last hour of a 25-hour day
            "fall_next": "2026-11-02T05:30:00",       # 00:30 on 2 November
            "spring_day": "2026-03-08T06:30:00",      # 01:30 on 8 March (EST)
            "spring_next": "2026-03-09T04:30:00",     # 00:30 on 9 March (EDT): the day after a 23-hour day
        }.items()}
        recs = seq(GENSHIN, "301", [5, 5, 5, 5])
        names = list(self.when)
        self.add(GENSHIN, [{**r, "name": names[i], "time": self.when[names[i]]} for i, r in enumerate(recs)])
        self.t = self.tracker()
        s = self.t.settings()
        s["times"] = "Local"
        self.t.save_settings(s)

    def names(self, day):
        h = self.t.history("genshin", {"rarity": "5", "from": day, "to": day})
        return sorted(r["name"] for r in h["rows"])

    def test_the_last_hour_of_a_25_hour_day_is_in_the_day(self):
        self.assertEqual(self.names("2026-11-01"), ["fall_late"])
        self.assertEqual(self.names("2026-11-02"), ["fall_next"])

    def test_the_first_hour_after_a_23_hour_day_is_not_in_the_day(self):
        self.assertEqual(self.names("2026-03-08"), ["spring_day"])
        self.assertEqual(self.names("2026-03-09"), ["spring_next"])


if __name__ == "__main__":
    unittest.main()
