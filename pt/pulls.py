"""Pull records: reading the API's items and UIGF files into one shape, and writing UIGF v4.0 and CSV.

Times in the API and in UIGF files are the server's local time; the store keeps unix seconds (UTC), using the UTC offset of the account's
region (pt/games.py region_of). IDs are kept as the numeric strings the API gives and compared as integers (analysis D5).

UIGF v4.0 (checked against the archived standard page, uigf.org/en/standards/uigf-legacy-v4.0.html; the current page names v4.2 and was not
readable here): info {export_timestamp, export_app, export_app_version, version}; hk4e / hkrpg / nap arrays of accounts
{uid, timezone, lang, list}; items {uigf_gacha_type (hk4e), gacha_type, gacha_id (hkrpg, nap), item_id, count, time, name, item_type, rank_type, id}.
[U] The official validator was not run, and v4.2 additions are not written.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Optional

from pt.games import GAMES, UIGF_KEYS, Game

log = logging.getLogger("pt.pulls")

APP_NAME, APP_VERSION = "Pulls Tracker", "0.33"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
# The pull times that are kept (unix seconds, UTC): 1970-01-01 to 3000-12-31, the range the date functions of Windows take. A time outside it
# is a placeholder of the program that wrote the file (0001-01-01 is .NET's DateTime.MinValue). Stored, it makes every screen that shows
# the pull fail (engine.iso), and that stays so until the account is removed.
MIN_TIME, MAX_TIME = 0, 32535215999


def parse_time(text: str, offset_hours: float) -> int:
    """'2026-10-22 17:20:00' in the server's time zone -> unix seconds (UTC)."""
    dt = datetime.strptime(str(text).strip()[:19], TIME_FORMAT).replace(tzinfo=timezone(timedelta(hours=offset_hours)))
    return int(dt.timestamp())


def server_time(t: int, offset_hours: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone(timedelta(hours=offset_hours))).strftime(TIME_FORMAT)


_LONE_SURROGATE = re.compile("[\\ud800-\\udfff]")


def clean_text(value) -> str:
    """Text for the store. A lone surrogate ("\\ud800" is valid in JSON) cannot be encoded as UTF-8: stored, it fails the whole batch at the
    insert, and again when the text is exported. It is replaced by U+FFFD."""
    return _LONE_SURROGATE.sub("�", str(value))


def normalize(game: Game, item: dict, offset_hours: float) -> Optional[dict]:
    """One item of the API or of a UIGF file -> a record for the store, or None when it cannot be used (no id, time, type or rarity)."""
    try:
        pid = str(item["id"]).strip()
        # The id is also stored as an integer (SQLite: 64 bit): int() alone takes "1_000" and non-ASCII digits, and a longer number would fail
        # the whole batch at the insert instead of skipping this item.
        if not (pid.isascii() and pid.isdigit()) or int(pid) >= 2 ** 63:
            return None
        code = str(item.get("gacha_type") or item.get("uigf_gacha_type") or "").strip()
        raw_rank = item.get("rank_type")
        if not code or raw_rank in (None, ""):
            return None
        rank = int(raw_rank)
        if not 0 <= rank <= 99:
            return None
        when = parse_time(item["time"], offset_hours)
        if not MIN_TIME <= when <= MAX_TIME:
            return None
        return {
            "id": pid, "gacha_type": clean_text(code), "item_id": clean_text(item.get("item_id") or ""), "name": clean_text(item.get("name") or ""),
            "item_type": clean_text(item.get("item_type") or ""), "rank_raw": rank, "rarity": game.rarity(rank),
            "time": when,
            "raw": {k: clean_text(item[k]) if isinstance(item[k], str) else item[k] for k in ("gacha_id", "count", "lang", "uid") if k in item},
        }
    except (KeyError, ValueError, TypeError, OverflowError):
        return None


def rows_to_items(game: Game, rows, offset_hours: float) -> list[dict]:
    """Stored rows back to API-shaped items (UIGF export)."""
    items = []
    for r in rows:
        raw = json.loads(r["raw"] or "{}")
        it = {"gacha_type": r["gacha_type"], "item_id": r["item_id"], "count": str(raw.get("count", "1")), "time": server_time(r["time"], offset_hours),
              "name": r["name"], "item_type": r["item_type"], "rank_type": str(r["rank_raw"]), "id": r["id"]}
        if game.key == "genshin":
            it["uigf_gacha_type"] = "301" if r["gacha_type"] == "400" else r["gacha_type"]
        else:
            it["gacha_id"] = str(raw.get("gacha_id", "0"))
        items.append(it)
    return items


def export_uigf(game: Game, uid: str, rows, offset_hours: float, lang: str = "en-us", now: Optional[int] = None) -> dict:
    return {
        "info": {"export_timestamp": int(now if now is not None else _time.time()), "export_app": APP_NAME, "export_app_version": APP_VERSION, "version": "v4.0"},
        game.uigf: [{"uid": str(uid), "timezone": int(offset_hours), "lang": lang, "list": rows_to_items(game, rows, offset_hours)}],
    }


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def cell(text) -> str:
    """A spreadsheet program runs a cell that starts with = + - @ as a formula, and a name can come from a file of another person: a leading
    ' makes the cell text. `uncell` takes it off again when the CSV is read."""
    s = str(text)
    return "'" + s if s.startswith(_FORMULA_START) else s


def uncell(text: str) -> str:
    return text[1:] if text.startswith("'") and text[1:].startswith(_FORMULA_START) else text


def export_csv(game: Game, rows, offset_hours: float) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["id", "time_utc", "server_time", "name", "rarity", "banner", "gacha_type", "item_id", "item_type"])
    for r in rows:
        group = game.group_of(r["gacha_type"])
        w.writerow([r["id"], datetime.fromtimestamp(r["time"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), server_time(r["time"], offset_hours),
                    cell(r["name"]), r["rarity"], group.short if group else cell(r["gacha_type"]), cell(r["gacha_type"]), cell(r["item_id"]),
                    cell(r["item_type"])])
    return out.getvalue()


# ---- reading files ---------------------------------------------------------------------------------------------------------

class ImportError_(ValueError):
    """The file is not something this program reads; the message is shown on the Import screen."""


def read_uigf(text: str) -> dict:
    """Parse a UIGF v4 document. Returns {format, entries: [{game, uid, offset, items: [normalized]}], skipped} or raises ImportError_."""
    try:
        data = json.loads(text)
    except (ValueError, RecursionError) as e:      # RecursionError: a file that nests arrays or objects thousands of levels deep
        raise ImportError_("The file is not a UIGF v4 JSON export or a CSV file.") from e
    info = data.get("info") if isinstance(data, dict) else None
    if not isinstance(info, dict) or not any(k in data for k in UIGF_KEYS):
        raise ImportError_("The file is not a UIGF v4 JSON export or a CSV file.")
    version = str(info.get("version") or "")
    if not version.startswith("v4"):
        raise ImportError_(f"UIGF {version or 'of an unknown version'} is not read; export UIGF v4.0 from the other program.")
    entries, skipped = [], 0
    for key, game_key in UIGF_KEYS.items():
        game = GAMES[game_key]
        accounts = data.get(key) or []
        if not isinstance(accounts, list):
            raise ImportError_(f"The {game.title} part of the file is not a list of accounts.")
        for acct in accounts:
            if not isinstance(acct, dict):
                raise ImportError_(f"An account of the file is not in the UIGF form ({game.title}).")
            uid, offset = str(acct.get("uid", "")).strip(), acct.get("timezone")
            # A UID is digits (it is shown in the screens and goes into file names); the time zone is hours, and the range check also
            # refuses Infinity and NaN, which JSON readers accept.
            if not (uid.isascii() and uid.isdigit() and len(uid) <= 20) or not isinstance(offset, (int, float)) or isinstance(offset, bool) \
                    or not -24 < offset < 24:
                raise ImportError_(f"An account of the file has no UID or no time zone ({game.title}).")
            listed = acct.get("list") or []
            if not isinstance(listed, list):
                raise ImportError_(f"The list of pulls of an account is not a list ({game.title}).")
            items = []
            for it in listed:
                n = normalize(game, it, offset)
                if n is None:
                    skipped += 1
                else:
                    items.append(n)
            entries.append({"game": game_key, "uid": uid, "offset": offset, "items": items})
    if not entries:
        raise ImportError_("The file has no pulls.")
    return {"format": f"UIGF {version}", "entries": entries, "skipped": skipped}


def read_csv(text: str, game: Game, uid: str, offset_hours: float) -> dict:
    """The CSV this program writes (export_csv): the game and UID are those of the active account, because the file has neither."""
    try:
        return _read_csv(text, game, uid, offset_hours)
    except csv.Error as e:      # a field above the reader's size limit (128 KB), or text it cannot split into rows: not a ValueError
        raise ImportError_("The CSV file could not be read: it is damaged, or a field in it is too long.") from e


def _read_csv(text: str, game: Game, uid: str, offset_hours: float) -> dict:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not {"id", "name", "gacha_type"} <= set(reader.fieldnames) or not ({"time_utc", "server_time"} & set(reader.fieldnames)):
        raise ImportError_("The CSV file is not one this program wrote (columns id, time_utc, name, rarity, gacha_type, item_id are needed).")
    items, skipped = [], 0
    for r in reader:
        try:
            rank_raw = int(r["rarity"]) - game.rarity_offset
            t = r["server_time"] if r.get("server_time") else datetime.strptime(r["time_utc"], "%Y-%m-%dT%H:%M:%SZ").strftime(TIME_FORMAT)
            n = normalize(game, {"id": r["id"], "gacha_type": uncell(r["gacha_type"]), "item_id": uncell(r.get("item_id") or ""), "name": uncell(r["name"]),
                                 "item_type": uncell(r.get("item_type") or ""), "rank_type": str(rank_raw), "time": t},
                           offset_hours if r.get("server_time") else 0)
        except (KeyError, ValueError, TypeError):
            n = None
        if n is None:
            skipped += 1
        else:
            items.append(n)
    if not items:
        raise ImportError_("The file has no pulls.")
    return {"format": "CSV", "entries": [{"game": game.key, "uid": uid, "offset": offset_hours, "items": items}], "skipped": skipped}
