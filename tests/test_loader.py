"""Tests of the load task runner: python -m unittest discover -s tests"""
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.pop("PULLS_TRACKER_DATA", None)      # a test must never touch a real data folder: each test that needs one sets its own
from pt import loader  # noqa: E402
from pt.loader import Context, Loader, Task  # noqa: E402


def quick(tasks):
    return [replace(t, min_seconds=0) for t in tasks]


def without_bodies(tasks):
    """The tasks of the real list with their bodies replaced by nothing (the real ones fetch from the network)."""
    return [replace(t, min_seconds=0, run=lambda ctx: None) for t in tasks]


class LoaderTest(unittest.TestCase):
    def test_default_tasks_run_in_order_and_reach_100_percent(self):
        reports = []
        results = Loader(without_bodies(loader.default_tasks()), lambda *a: reports.append(a)).run()
        ids = [t.id for t in loader.default_tasks()]
        self.assertEqual(ids, ["banners", "stored", "fresh", "assets_genshin", "assets_hsr", "assets_zzz", "sync", "open"])
        self.assertEqual(list(results), ids)
        self.assertTrue(all(v == "ok" for v in results.values()))
        self.assertEqual(reports[0], (0, 8, "Checking the active banners", 0.0))
        self.assertEqual(reports[-1], (8, 8, "Opening the application", 0.0))
        self.assertEqual(len(reports), 16)
        self.assertEqual([r[0] for r in reports], [0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8])

    def test_up_to_date_skips_the_asset_downloads(self):
        reports = []
        ran = []
        tasks = quick([
            Task("fresh", "fresh", lambda ctx: ctx.state.update(assets_outdated=False)),
            Task("a", "a", lambda ctx: ran.append("a"), when=lambda ctx: ctx.state.get('assets_outdated', True) is not False),
            Task("b", "b", lambda ctx: ran.append("b"), when=lambda ctx: ctx.state.get('assets_outdated', True) is not False),
            Task("sync", "sync", lambda ctx: ran.append("sync")),
        ])
        results = Loader(tasks, lambda *a: reports.append(a)).run()
        self.assertEqual(ran, ["sync"])
        self.assertEqual(results, {"fresh": "ok", "a": "skipped", "b": "skipped", "sync": "ok"})
        self.assertEqual(reports[-1], (2, 2, "sync", 0.0))       # the skipped tasks left the total
        self.assertEqual(reports[0][1], 4)                        # before task 1 finished all four were planned

    def test_failed_task_is_logged_and_the_rest_runs(self):
        def boom(ctx):
            raise RuntimeError("x")
        ran = []
        with self.assertLogs("pt.loader", level="ERROR"):
            results = Loader(quick([Task("a", "a", boom), Task("b", "b", lambda ctx: ran.append("b"))]), lambda *a: None).run()
        self.assertEqual(results, {"a": "failed", "b": "ok"})
        self.assertEqual(ran, ["b"])

    def test_a_condition_that_raises_skips_its_task_and_the_runner_still_reaches_the_last_one(self):
        def bad(ctx):
            raise KeyError("x")
        ran = []
        tasks = quick([Task("a", "a", lambda c: ran.append("a"), when=bad), Task("open", "open", lambda c: ran.append("open"))])
        with self.assertLogs("pt.loader", level="ERROR") as cm:
            results = Loader(tasks, lambda *a: None).run()
        self.assertEqual(results, {"a": "skipped", "open": "ok"})
        self.assertEqual(ran, ["open"])
        self.assertEqual(len(cm.records), 1)                   # logged once, not again by the progress total

    def test_cancel_stops_before_the_next_task(self):
        ctx = Context()
        ran = []
        tasks = quick([Task("a", "a", lambda c: (ran.append("a"), setattr(ctx, "cancelled", lambda: True))), Task("b", "b", lambda c: ran.append("b"))])
        results = Loader(tasks, lambda *a: None, ctx).run()
        self.assertEqual(ran, ["a"])
        self.assertEqual(results, {"a": "ok", "b": "cancelled"})

    def test_fraction_inside_a_task(self):
        reports = []
        Loader(quick([Task("a", "a", lambda c: None), Task("dl", "dl", lambda c: (c.progress(0.25), c.progress(2)))]), lambda *a: reports.append(a)).run()
        self.assertIn((1, 2, "dl", 0.25), reports)
        self.assertIn((1, 2, "dl", 1.0), reports)             # clamped to 1

    def test_a_fast_task_keeps_its_label_for_the_minimum_time(self):
        import time
        t0 = time.monotonic()
        Loader([Task("a", "a", lambda c: None, min_seconds=0.2)], lambda *a: None).run()
        self.assertGreaterEqual(time.monotonic() - t0, 0.19)


if __name__ == "__main__":
    unittest.main()
