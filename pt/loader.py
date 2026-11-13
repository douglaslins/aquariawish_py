"""The load tasks that run while the loading panel is shown, and the runner that reports their progress to the panel.

Order (the numbers are the steps of the specification):
  1 banners          check the active banners
  2 stored           check that locally stored information exists
  3 fresh            check that it is up to date with the active banners
  4 (branch)         up to date: skip the asset downloads; not up to date: download the assets. Each of the three tasks below has a `when`
                     that is true only for a game whose stored data is out of date, so an up to date game is skipped on its own.
  5 assets_genshin   download the Genshin Impact assets
  6 assets_hsr       download the Honkai: Star Rail assets
  6 assets_zzz       download the Zenless Zone Zero assets
  7 sync             account data syncs that can be done at that moment
    open             initialize the interfaces and the initial screen (the main window; provided by pt/window.py)

The task functions are in pt/tasks.py; the banner and asset engine they use is pt/banners.py.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

log = logging.getLogger("pt.loader")

MIN_STEP_SECONDS = 0.3      # a task label stays visible at least this long, so a fast task does not flicker


@dataclass
class Context:
    """What the tasks share. `state` carries results from one task to later ones; `services` holds functions the window code provides."""
    state: dict = field(default_factory=dict)
    services: dict = field(default_factory=dict)
    cancelled: Callable[[], bool] = lambda: False
    _fraction: Callable[[float], None] = lambda f: None

    def progress(self, fraction: float) -> None:
        """Inside a long task (a download): how far it is, 0 to 1."""
        self._fraction(max(0.0, min(1.0, float(fraction))))


@dataclass
class Task:
    id: str
    label: str                                              # shown under the donut
    run: Callable[[Context], None]
    when: Optional[Callable[[Context], bool]] = None        # a task whose `when` gives False is skipped and leaves the total
    min_seconds: float = MIN_STEP_SECONDS


def _open_main(ctx: Context) -> None:
    open_main = ctx.services.get("open_main")
    if open_main:
        open_main()


def default_tasks() -> list[Task]:
    from . import tasks      # imported here: the task functions bring in the network code
    return [
        Task("banners", "Checking the active banners", tasks.check_banners),
        Task("stored", "Looking for stored data", tasks.look_for_stored),
        Task("fresh", "Checking that stored data is up to date", tasks.check_fresh),
        Task("assets_genshin", "Downloading Genshin Impact assets", tasks.download_assets("genshin"), when=tasks.needs_assets("genshin")),
        Task("assets_hsr", "Downloading Honkai: Star Rail assets", tasks.download_assets("hsr"), when=tasks.needs_assets("hsr")),
        Task("assets_zzz", "Downloading Zenless Zone Zero assets", tasks.download_assets("zzz"), when=tasks.needs_assets("zzz")),
        Task("sync", "Syncing accounts", tasks.sync_accounts),
        Task("open", "Opening the application", _open_main, min_seconds=0.0),
    ]


class Loader:
    """Runs the tasks in order and calls report(done, total, label, fraction) when a task starts, advances and ends.
    A task that raises is logged and counted as finished; the application still starts."""

    def __init__(self, tasks: list[Task], report: Callable[[int, int, str, float], None], ctx: Optional[Context] = None) -> None:
        self.tasks, self.report, self.ctx = tasks, report, ctx or Context()
        self.results: dict[str, str] = {}          # task id -> "ok" | "skipped" | "failed" | "cancelled"

    def _applies(self, task: Task, quiet: bool = False) -> bool:
        """Whether the task runs. A `when` that raises counts as False and is logged: an exception here must not end the runner before its
        last task, which opens the window."""
        if task.when is None:
            return True
        try:
            return bool(task.when(self.ctx))
        except Exception:
            if not quiet:
                log.exception("The condition of load task %s failed; the task is skipped", task.id)
            return False

    def _total(self, index: int, done: int) -> int:
        """Tasks finished plus the running one plus the later ones that will run."""
        return done + 1 + sum(1 for t in self.tasks[index + 1:] if self._applies(t, quiet=True))

    def run(self) -> dict[str, str]:
        done = 0
        for i, task in enumerate(self.tasks):
            if self.ctx.cancelled():
                self.results[task.id] = "cancelled"
                continue
            if not self._applies(task):
                self.results[task.id] = "skipped"
                continue
            total = self._total(i, done)
            self.ctx._fraction = lambda f, d=done, t=total, lb=task.label: self.report(d, t, lb, f)
            self.report(done, total, task.label, 0.0)
            started = time.monotonic()
            try:
                task.run(self.ctx)
                self.results[task.id] = "ok"
            except Exception:
                log.exception("Load task %s failed", task.id)
                self.results[task.id] = "failed"
            left = task.min_seconds - (time.monotonic() - started)
            if left > 0:
                time.sleep(left)
            done += 1
            self.report(done, total, task.label, 0.0)
        return self.results
