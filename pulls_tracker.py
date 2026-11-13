#!/usr/bin/env python3
"""Pulls Tracker: tracks pulls (wishes, warps, signals) for Genshin Impact, Honkai: Star Rail and Zenless Zone Zero.

Usage:  python pulls_tracker.py [--debug] [--perf] [--selfcheck]
  --debug   enable the browser developer tools of the window (F12, right click, Inspect)
  --perf    the page measures its own responsiveness and writes it to data/pulls_tracker.log
  --selfcheck  check the installation (folders, interface files, modules, WebView2), write data/selfcheck.txt and exit
"""
from __future__ import annotations

import argparse
import logging
import multiprocessing
import sys


def main(argv=None) -> int:
    multiprocessing.freeze_support()      # needed by a Windows build (PyInstaller) when anything starts a process; harmless otherwise
    from pt import log
    log.ensure_streams()                  # the windowed build has no console: argparse and print must not fail
    ap = argparse.ArgumentParser(description="Pulls Tracker")
    ap.add_argument("--debug", action="store_true", help="enable the developer tools of the window")
    ap.add_argument("--perf", action="store_true", help="write responsiveness measurements of the page to the log")
    ap.add_argument("--selfcheck", action="store_true", help="check the installation and write data/selfcheck.txt")
    args = ap.parse_args(argv)
    if args.selfcheck:
        from pt import selfcheck
        return selfcheck.main()
    try:
        return _start(args)
    except Exception as e:      # noqa: BLE001 - the windowed program has no console: tell the person, and write the details to the log
        return _failed(e)


def _start(args) -> int:
    from pt import log, paths, preflight, single, store, window
    log.setup(debug=args.debug)
    logging.getLogger("pt").info("Start: frozen=%s, data folder %s", paths.FROZEN, paths.data_dir_note())
    guard = single.acquire(paths.data_dir())
    if not guard.acquired:
        logging.getLogger("pt").warning("Another copy is running on the data folder %s; this one ends", paths.data_dir())
        preflight.already_running()
        return 0
    try:
        if paths.FROZEN and not preflight.webview2_ok():
            return 1
        database = paths.database_path()
        if not preflight.database_ok(store.stored_schema(database), int(store.SCHEMA_VERSION), paths.data_dir()):
            return 1
        window.run(debug=args.debug, perf=args.perf)
    finally:
        guard.release()
    return 0


def _failed(exc: Exception) -> int:
    logging.getLogger("pt").critical("The program could not start", exc_info=(type(exc), exc, exc.__traceback__))
    log_file = None
    try:
        from pt import paths
        candidate = paths.data_dir() / "pulls_tracker.log"
        log_file = candidate if candidate.is_file() else None
    except Exception:      # noqa: BLE001 - the folder itself may be the problem
        pass
    from pt import preflight
    preflight.fatal(exc, log_file)
    return 1


if __name__ == "__main__":
    sys.exit(main())
