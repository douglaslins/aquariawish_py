"""Log file: data/pulls_tracker.log. The program built with PyInstaller has no console, so errors go here."""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading

from . import paths

_MARK = "_pulls_tracker_handler"       # set on the handlers that setup() adds, so that a second call can remove them


def ensure_streams() -> None:
    """A windowed PyInstaller build has no stdout and no stderr: anything printed (the fetcher prints its progress, argparse prints its
    errors) goes nowhere instead of failing."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def setup(debug: bool = False) -> None:
    paths.ensure_dirs()
    ensure_streams()
    root = logging.getLogger()
    for old in [h for h in root.handlers if getattr(h, _MARK, False)]:      # a second call replaces the handlers of the first: no line is written twice
        root.removeHandler(old)
        old.close()
    handler = logging.handlers.RotatingFileHandler(paths.data_dir() / "pulls_tracker.log", maxBytes=512_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    setattr(handler, _MARK, True)
    root.addHandler(handler)
    if getattr(sys.stderr, "name", None) != os.devnull:         # no console (the stream was replaced above): the file is the log
        console = logging.StreamHandler()
        setattr(console, _MARK, True)
        root.addHandler(console)

    def hook(exc_type, exc, tb):
        logging.getLogger("pt").critical("Uncaught exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = hook
    threading.excepthook = lambda a: hook(a.exc_type, a.exc_value, a.exc_traceback)
