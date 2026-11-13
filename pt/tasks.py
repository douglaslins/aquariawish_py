"""The load tasks (see pt/loader.py for the order and the progress reporting). Each task is a function of the shared Context; results
that later tasks need go in ctx.state:

    active          {ui: list of current banners or None}      task 1
    active_errors   {ui: error text}                           task 1
    stored          the stored banner data or None             task 2
    outdated        {ui: True/False}                           task 3
    manifest        (manifest or None, error)                  first download task
"""
from __future__ import annotations

import logging
import time

from . import banners
from .loader import Context

log = logging.getLogger("pt.tasks")


def check_banners(ctx: Context) -> None:
    """1. Check the active banners."""
    server = ctx.services.get("server")
    base = (server() if server else "") or None
    active, errors = banners.check_active(base=base)
    ctx.state["active"], ctx.state["active_errors"] = active, errors
    if all(v is None for v in active.values()):
        log.warning("No calendar could be read (offline?). The stored data, or the bundled snapshot, stays in use.")


def look_for_stored(ctx: Context) -> None:
    """2. Check that locally stored information exists."""
    stored = banners.load_stored()
    ctx.state["stored"] = stored
    log.info("Stored banner data: %s", f"written {stored.get('generatedAt')}" if stored else "none")


def check_fresh(ctx: Context) -> None:
    """3. Check that the stored information is up to date with the active banners. (4. The asset tasks that follow run only for the
    games that are out of date; when none is, the next task is the account sync and then the interface opens.)"""
    outdated, why = banners.compare(ctx.state.get("stored"), ctx.state.get("active", {}), ctx.state.get("active_errors", {}))
    ctx.state["outdated"] = outdated
    for ui, text in why.items():
        log.info("%s: %s (%s)", ui, "out of date" if outdated[ui] else "ok", text)


def needs_assets(ui: str):
    def when(ctx: Context) -> bool:
        return ctx.state.get("outdated", {}).get(ui, True) is not False      # unknown counts as out of date
    return when


def download_assets(ui: str):
    """5 to 7. Download the assets of one game: art, icons, descriptions of its current characters and weapons."""
    def run(ctx: Context) -> None:
        current = ctx.state.get("active", {}).get(ui)
        if current is None:
            log.warning("%s: no calendar, nothing to download", ui)
            return
        if "manifest" not in ctx.state:
            ctx.state["manifest"] = banners.fetch_manifest()
        manifest, manifest_err = ctx.state["manifest"]
        slides, notes, problems = banners.download_game(ui, current, manifest, manifest_err, ctx.progress)
        info = {"banners": len(current), "notes": notes, "problems": problems, "status": "ok"}
        ctx.state["stored"] = banners.store_game(ctx.state.get("stored"), ui, slides, info, now=time.time())
        log.info("%s: %d slide(s) stored, %d problem(s)", ui, len(slides), len(problems))
        for p in problems:
            log.info("%s: %s", ui, p)
    return run


def sync_accounts(ctx: Context) -> None:
    """7. Account data syncs that can be done at this moment. The sync engine provides ctx.services["auto_sync"]."""
    auto_sync = ctx.services.get("auto_sync")
    if auto_sync is None:
        log.info("No account sync available yet")
        return
    auto_sync(ctx)
