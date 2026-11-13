"""The settings of the Settings screen, kept in data/settings.json. The defaults are the ones of DEFAULT_SETTINGS in js/api.js."""
from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import threading
from pathlib import Path

from pt import paths

log = logging.getLogger("pt.settings")

DEFAULTS = {
    "language": "English", "times": "Local",
    "track": {"genshin": True, "hsr": True, "zzz": True},
    "folders": {"genshin": "", "hsr": "", "zzz": ""},
    "soft": {"genshin": {"character": 74, "weapon": 63}, "hsr": {"character": 74, "lightcone": 66}, "zzz": {"exclusive": 74, "wengine": 65}},
    "pityColors": {"green": 50, "gold": 75},
    "grade": {"ss": 50, "a": 60, "b": 73, "c": 76},
    "lossPool": ["Himeko", "Welt", "Bronya", "Gepard", "Clara", "Yanqing", "Bailu"],
    "maskUid": True, "backupFolder": "", "autoBackup": True,
    "beta": False, "hoyolab": False, "server": "",
}
TIMES = ("Local", "Server", "UTC")
HARD = {"genshin": {"character": 90, "weapon": 80}, "hsr": {"character": 90, "lightcone": 80}, "zzz": {"exclusive": 90, "wengine": 80}}
_lock = threading.Lock()


def _same_kind(base, value) -> bool:
    if isinstance(base, bool):
        return isinstance(value, bool)
    if isinstance(base, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, type(base))


def merge(base: dict, over) -> dict:
    """base with the values of `over` on top, key by key; keys that base does not have are dropped (an old or damaged file)."""
    out = copy.deepcopy(base)
    if not isinstance(over, dict):
        return out
    for k, v in base.items():
        if k not in over:
            continue
        if isinstance(v, dict):
            out[k] = merge(v, over[k])
        elif _same_kind(v, over[k]):
            out[k] = over[k]
    return out


def _int(v, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError, OverflowError):      # OverflowError: int(float("inf")); json.loads accepts Infinity
        return default


def clean(s: dict) -> dict:
    """Types and ranges: what the screen can send is not trusted."""
    out = merge(DEFAULTS, s)
    if out["times"] not in TIMES:
        out["times"] = DEFAULTS["times"]
    out["language"] = "English"
    for game, groups in out["soft"].items():
        for key in groups:
            groups[key] = _int(groups[key], 1, HARD[game][key], DEFAULTS["soft"][game][key])
    pc = out["pityColors"]
    pc["green"] = _int(pc["green"], 1, 90, 50)
    pc["gold"] = _int(pc["gold"], pc["green"], 90, max(pc["green"], 75))
    g = out["grade"]
    g["ss"] = _int(g["ss"], 1, 90, 50)
    g["a"] = _int(g["a"], g["ss"], 90, max(g["ss"], 60))
    g["b"] = _int(g["b"], g["a"], 90, max(g["a"], 73))
    g["c"] = _int(g["c"], g["b"], 90, max(g["b"], 76))
    pool = out["lossPool"]
    out["lossPool"] = [str(n).strip()[:40] for n in pool if str(n).strip()][:30] if isinstance(pool, list) else list(DEFAULTS["lossPool"])
    for k in ("folders",):
        out[k] = {game: str(v)[:500] for game, v in out[k].items()}
    backup = str(out["backupFolder"]).strip()[:500]
    out["backupFolder"] = backup if backup and Path(backup).is_absolute() else ""      # a relative path would lead to the folder the program was started from
    out["server"] = str(out["server"])[:100]
    return out


def path() -> Path:
    return paths.data_dir() / "settings.json"


def load() -> dict:
    f = path()
    try:
        raw = f.read_bytes()
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS)
    except OSError:                    # busy or not readable now: the file is left as it is, the next save replaces it
        log.warning("Could not read %s; using the defaults", f, exc_info=True)
        return copy.deepcopy(DEFAULTS)
    try:
        return clean(json.loads(raw.decode("utf-8-sig")))
    except Exception:
        bad = f.with_name(f.name + ".bad")
        log.warning("%s is not valid settings; it is kept as %s and the defaults are used", f, bad, exc_info=True)
        try:
            os.replace(f, bad)         # the next save would overwrite it: keep what the person had
        except OSError:
            log.debug("Could not keep the unreadable settings file", exc_info=True)
        return copy.deepcopy(DEFAULTS)


def save(s: dict) -> dict:
    """Validate, write (to a temporary file first, then a rename, so a crash never leaves half a file) and return what was kept."""
    out = clean(s)
    with _lock:
        f = path()
        f.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=f.parent, prefix="settings-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(out, fh, indent=2)
            os.replace(tmp, f)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return out
