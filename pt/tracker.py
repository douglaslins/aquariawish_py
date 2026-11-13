"""The service behind the screens: stored pulls + settings + banner history -> the figures the interface shows.

Pity, guarantee and results are always derived from the stored pulls (pt/engine.py) and never stored, so a changed setting (the Star Rail
loss pool) or new banner data changes the results at once and nothing can get out of step with the rows. The derived rows of an account
are kept until the store, the banner history or a setting that matters changes.

All times the screens receive are ISO UTC strings; the screens format them in the time mode of the settings. Date filters ("from",
"to" of the History screen) are calendar days in that same mode: Local (this computer), Server (the account's region) or UTC.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from pt import engine, paths, pulls, settings as settings_mod
from pt.games import GAMES, OFFICIAL, STANDARD_5, Game, load_overrides, region_of
from pt.store import Store

log = logging.getLogger("pt.tracker")

WINDOW_SLACK = 60               # seconds added at the end of a banner window when pulls are counted in it
FAR = 2 ** 40                   # "no end announced"
DAY = 86400
DATE_RANGES = {"all": 0, "12m": 365 * DAY, "90d": 90 * DAY, "30d": 30 * DAY}
MAX_IMPORT_BYTES = 100 * 1024 * 1024      # the largest file the Import screen reads (pt/api.py reads files with it, import_preview checks the text)
_BACKUP_NAME = re.compile(r"pulls-\d{8}-\d{6}\.sqlite")      # what Tracker.backup writes; nothing else in a backup folder is ever deleted
_BANNERS_JS =re.compile(r"window\.PT_BANNERS\s*=\s*(\{.*\})\s*;?\s*$", re.S)


def read_banners_js(path: Path) -> Optional[dict]:
    """The data of a banners.js file (window.PT_BANNERS = {...}), or None when the file is missing or not readable."""
    try:
        m = _BANNERS_JS.search(path.read_text(encoding="utf-8"))
        return json.loads(m.group(1)) if m else None
    except (OSError, ValueError):
        return None


def _is_file(f: Path) -> bool:
    """Path.is_file, but False also when the file cannot be examined right now (before Python 3.13 it raises for some errors)."""
    try:
        return f.is_file()
    except OSError:
        return False


def _mtime_ns(f: Path) -> int:
    """The modification time of a file, 0 when it is missing or cannot be read right now (on Windows a file that is being replaced)."""
    try:
        return f.stat().st_mtime_ns if _is_file(f) else 0
    except OSError:
        return 0


def parse_iso(text) -> Optional[int]:
    """'2026-10-13T10:00:00Z' -> unix seconds; None for an empty value, an unreadable one, or one outside pulls.MIN_TIME..MAX_TIME
    (the range the program keeps: a date such as year 1 or 9999 would be stored for good and the system's date functions may fail on it)."""
    if not text:
        return None
    try:
        value = int(datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp())
    except (ValueError, OverflowError, OSError):      # the last two: a date the system's clock functions cannot take (year 1 on Windows)
        return None
    return value if pulls.MIN_TIME <= value <= pulls.MAX_TIME else None


class Tracker:
    def __init__(self, store: Store, data_dir: Optional[Path] = None, ui_dir: Optional[Path] = None, clock=time.time):
        self.store = store
        self.data_dir = Path(data_dir) if data_dir else paths.data_dir()
        self.ui_dir = Path(ui_dir) if ui_dir else paths.ui_dir()
        self.clock = clock
        self._settings = settings_mod.load()
        load_overrides(self.data_dir / "rules.json")
        self._lock = threading.RLock()
        self._derived: dict = {}
        self._derived_stamp = None
        self._stamp = None
        self._pending: dict = {}
        self.banner_info: dict = {"source": None, "generatedAt": None}
        self.refresh_banners()

    # ---- settings ------------------------------------------------------------------------------------------------------------

    def settings(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._settings))

    def save_settings(self, s: dict) -> dict:
        with self._lock:
            self._settings = settings_mod.save(s)
            return self.settings()

    # ---- banner history ------------------------------------------------------------------------------------------------------

    def _banner_files(self) -> list[Path]:
        return [self.ui_dir / "js" / "banners.js", self.data_dir / "js" / "banners.js"]       # oldest source first

    def refresh_banners(self) -> int:
        """Put the banners of the shipped snapshot and of data/js/banners.js into the banner history when the files changed. Returns the
        number of banners that were not known before."""
        files = self._banner_files()
        stamp = tuple(_mtime_ns(f) for f in files)
        with self._lock:
            if stamp == self._stamp:
                return 0
            loaded = [d for d in (read_banners_js(f) if _is_file(f) else None for f in files) if d]
            loaded.sort(key=lambda d: str(d.get("generatedAt") or ""))      # the newest data is applied last, so it wins for a banner both hold
            new = 0
            retry = False
            for data in loaded:
                try:
                    new += self._archive(data)
                except (OSError, sqlite3.Error):      # a condition that can pass (database busy, disk): the same files are tried again on the next call
                    retry = True
                    log.warning("The banner data generated at %s could not be saved", data.get("generatedAt"), exc_info=True)
                except Exception:      # a file of the wrong shape is skipped; it must not take every screen down
                    log.warning("The banner data generated at %s could not be used", data.get("generatedAt"), exc_info=True)
            if not retry:
                self._stamp = stamp
            if loaded:
                newest = loaded[-1]
                self.banner_info = {"source": newest.get("source"), "generatedAt": newest.get("generatedAt")}
            if new:
                log.info("Banner history: %d new banner(s)", new)
            return new

    def _archive(self, data: dict) -> int:
        """One archive row per banner: the slides of a game that share group, banner name and window are one banner."""
        new = 0
        seen = parse_iso(data.get("generatedAt")) or int(self.clock())
        for game, slides in (data.get("slides") or {}).items():
            if game not in GAMES:
                continue
            banners: dict[tuple, dict] = {}
            for s in slides or []:
                start, end = parse_iso(s.get("start")), parse_iso(s.get("end"))
                title = s.get("bannerName") or s.get("banner")
                if start is None or not title or not s.get("group"):
                    continue
                b = banners.setdefault((s["group"], title, start, end if end is not None else FAR),
                                       {"version": str(s.get("version") or ""), "kind": s.get("kind") or "", "items": [], "rate_up": []})
                if not any(i["name"] == s.get("name") for i in b["items"]):
                    b["items"].append({k: s.get(k) for k in ("name", "rank", "itemId", "icon", "iconRemote")})
                for r in s.get("rateUp") or []:
                    if isinstance(r, dict) and r.get("name") and not any(x["name"] == r["name"] for x in b["rate_up"]):
                        b["rate_up"].append({k: r.get(k) for k in ("name", "rank", "icon", "iconRemote")})
            for (grp, title, start, end), b in banners.items():
                if self.store.archive_banner(game, grp, title, start, end, b["version"], b["kind"], b["items"], b["rate_up"], seen):
                    new += 1
        return new

    def _classifier(self, game: Game, group) -> Optional[engine.Classifier]:
        if not group.flip:
            return None
        windows = [{"start": b["start"], "end": b["end"], "names": {i["name"].lower() for i in b["items"] if i.get("name") and i.get("rank") == 5}}
                   for b in self.store.banners(game.key) if b["grp"] == group.key]
        if game.key == "hsr" and group.key == "character":
            standard = self._settings["lossPool"]             # the user's loss pool (analysis D1)
        elif game.key == "hsr" and group.key == "lightcone":
            standard = STANDARD_5.get("hsr_lightcone")        # without it every Light Cone 5-star before the first banner of the snapshot had no result
        elif group.key in ("character", "exclusive"):
            standard = STANDARD_5.get(game.key)
        else:
            standard = None
        return engine.Classifier(windows, standard)

    # ---- accounts and derived rows -------------------------------------------------------------------------------------------

    def active(self, game: str) -> Optional[dict]:
        return self.store.active(game)

    def derived(self, game_key: str, uid: str) -> dict[str, engine.Derived]:
        """{group key: Derived} of an account, every group of the game (the Beginner banner too)."""
        self.refresh_banners()
        game = GAMES[game_key]
        with self._lock:
            stamp = (self.store.version, tuple(self._settings["lossPool"]), json.dumps(STANDARD_5, sort_keys=True))
            if stamp != self._derived_stamp:
                self._derived, self._derived_stamp = {}, stamp
            hit = self._derived.get((game_key, uid))
            if hit is None:
                hit = {g.key: engine.derive(game, g, self.store.pulls(game_key, uid, g.codes), self._classifier(game, g)) for g in game.groups}
                self._derived[(game_key, uid)] = hit
            return hit

    def _rows(self, game: Game, d: dict[str, engine.Derived], keys: Optional[list[str]] = None) -> list[engine.Row]:
        keys = keys or [g.key for g in game.ui_groups()]
        return [r for k in keys if k in d for r in d[k].rows]

    # ---- time helpers --------------------------------------------------------------------------------------------------------

    def _day_start(self, day: str, acct: dict, plus_days: int = 0) -> Optional[int]:
        """First second of the calendar day 'YYYY-MM-DD' (plus `plus_days` days) in the time mode of the settings. The end of a day is the start
        of the next minus 1, not start + 86400 - 1: in Local mode a day is 23 or 25 hours long when the clock changes."""
        try:
            d = datetime.strptime(day, "%Y-%m-%d") + timedelta(days=plus_days)
            mode = self._settings["times"]
            if mode == "Local":
                return int(d.timestamp())
            offset = 0 if mode == "UTC" else (acct.get("utc_offset") or 0)
            return int(d.replace(tzinfo=timezone(timedelta(hours=offset))).timestamp())
        except (TypeError, ValueError, OverflowError, OSError):      # the last two: a date the system's clock functions cannot take
            return None

    def _since(self, dates: str) -> int:
        span = DATE_RANGES.get(dates, 0)
        return int(self.clock()) - span if span else 0

    # ---- screens -------------------------------------------------------------------------------------------------------------

    def state(self) -> dict:
        accounts, last = [], {}
        for a in self.store.accounts():
            if not self.store.count(a["game"], a["uid"]) and not a["last_sync"]:
                continue
            accounts.append({"game": a["game"], "uid": a["uid"], "region": a["region"], "nickname": a["nickname"]})
            if a["game"] not in last and a["last_sync"]:
                last[a["game"]] = engine.iso(a["last_sync"])
        return {"synced": bool(accounts), "accounts": accounts, "lastSync": last}

    def dashboard(self, game_key: str) -> dict:
        game = GAMES[game_key]
        acct = self.active(game_key)
        if not acct or not self.store.count(game_key, acct["uid"]):
            return {"game": game_key, "empty": True}
        d = self.derived(game_key, acct["uid"])
        groups = []
        for g in game.ui_groups():
            x = d[g.key]
            groups.append({"key": g.key, "pity": x.pity, "guarantee": x.guarantee, "pulls": len(x.rows), "fives": x.fives, "lowerBound": x.fives == 0 and bool(x.rows),
                           "extra": self._extra(game, g, d)})
        st = engine.stats(game, d, [g.key for g in game.ui_groups()], 0, self._settings, OFFICIAL.get(game_key))
        rows = self._rows(game, d)
        info = self.banner_info
        return {
            "game": game_key, "empty": False, "groups": groups,
            "summary": {k: st[k] for k in ("pulls", "fives", "avg", "avgLowerBound", "grade", "flips", "spent", "currency")},
            "oldest": engine.iso(min(r.time for r in rows)) if rows else None,
            "bannerSource": {"label": "public calendar API" if info.get("source") else "bundled snapshot", "at": info.get("generatedAt")},
        }

    def _extra(self, game: Game, group, d: dict) -> Optional[dict]:
        if game.key == "genshin" and group.key == "character":
            return {"radiance": d[group.key].radiance}       # an estimate: consecutive lost 50/50s (the game's counter is not in the API)
        if group.key == "standard" and game.novice_pulls:
            novice = next(g for g in game.groups if not g.visible)
            if len(d[novice.key].rows) >= game.novice_pulls:
                return {"done": f"{novice.label}: completed"}
        return None

    def window_stats(self, game_key: str, items: list) -> dict:
        game = GAMES[game_key]
        acct = self.active(game_key)
        out = {}
        for it in items or []:
            key = it.get("id")
            g = next((x for x in game.groups if x.key == it.get("group")), None)
            if not acct or g is None or not self.store.count(game_key, acct["uid"]):
                out[key] = None
                continue
            start, end = parse_iso(it.get("start")) or 0, parse_iso(it.get("end"))
            counts = self.store.window_counts(game_key, acct["uid"], list(g.codes), start, (end + WINDOW_SLACK) if end is not None else FAR)
            out[key] = {
                "pulls": sum(r["n"] for r in counts),
                "fives": sum(r["n"] for r in counts if r["rarity"] == 5),
                "fours": sum(r["n"] for r in counts if r["rarity"] == 4),
                "sub": ({1: sum(r["n"] for r in counts if r["gacha_type"] != "400"), 2: sum(r["n"] for r in counts if r["gacha_type"] == "400")}
                        if game_key == "genshin" and g.key == "character" else None),
            }
        return out

    def window_pulls(self, game_key: str, group: str, start, end, limit: int = 8) -> dict:
        acct = self.active(game_key)
        if not acct or not self.store.count(game_key, acct["uid"]) or group not in {g.key for g in GAMES[game_key].groups}:
            return {"total": 0, "rows": []}
        a, b = parse_iso(start) or 0, parse_iso(end)
        b = (b + WINDOW_SLACK) if b is not None else FAR
        rows = [r for r in self.derived(game_key, acct["uid"])[group].rows if a <= r.time <= b and r.rarity >= 4]
        rows.sort(key=lambda r: (r.time, r.n), reverse=True)
        return {"total": len(rows), "rows": [self._row(r) for r in rows[:max(0, int(limit))]]}

    def past_banners(self, game_key: str) -> list:
        now, out = int(self.clock()), []
        for b in self.store.banners(game_key):
            if b["end"] >= now or b["kind"] not in ("character", "weapon"):
                continue
            out.append({
                "id": f"{game_key}:{b['grp']}:{b['start']}:{b['title']}", "version": b["version"], "title": b["title"], "group": b["grp"], "kind": b["kind"],
                "items": [{"name": i["name"], "rank": i.get("rank") or 5, "icon": i.get("icon"), "iconRemote": i.get("iconRemote")} for i in b["items"]],
                "rateUp": b["rate_up"], "start": engine.iso(b["start"]), "end": engine.iso(b["end"]),
            })
        return out

    @staticmethod
    def _row(r: engine.Row) -> dict:
        return {"id": r.id, "n": r.n, "time": engine.iso(r.time), "name": r.name, "rarity": r.rarity, "group": r.group, "pity": r.pity,
                "lowerBound": r.lb, "result": r.result, "sub": r.sub}

    def _history_rows(self, game_key: str, q: dict) -> tuple[Optional[dict], list]:
        game = GAMES[game_key]
        acct = self.active(game_key)
        if not acct or not self.store.count(game_key, acct["uid"]):
            return None, []
        d = self.derived(game_key, acct["uid"])
        group = q.get("group")
        keys = [group] if group in {g.key for g in game.ui_groups()} else None
        want = {"5": (5,), "4": (4,), "45": (4, 5)}.get(q.get("rarity") or "5", (5,))
        frm = self._day_start(q.get("from"), acct) if q.get("from") else 0
        to = self._day_start(q.get("to"), acct, 1) if q.get("to") else None      # the start of the day after "to"
        to = (to - 1) if to is not None else FAR
        name = (q.get("name") or "").strip().lower()
        rows = [r for r in self._rows(game, d, keys)
                if (r.rarity in want or (q.get("include3") and r.rarity == 3)) and (frm or 0) <= r.time <= to and (not name or name in r.name.lower())]
        col = (q.get("sort") or {}).get("col") or "time"
        desc = ((q.get("sort") or {}).get("dir") or "desc") != "asc"
        value = {"n": lambda r: (r.n, int(r.id)), "time": lambda r: (r.time, int(r.id)), "name": lambda r: r.name.lower(), "rarity": lambda r: r.rarity,
                 "group": lambda r: r.group, "pity": lambda r: r.pity, "result": lambda r: r.result["text"] if r.result else ""}.get(col)
        if value is None:
            value = lambda r: (r.time, int(r.id))        # noqa: E731
        rows.sort(key=lambda r: int(r.id), reverse=True)        # equal values: newer first
        rows.sort(key=value, reverse=desc)                       # (a stable sort keeps that order)
        return acct, rows

    def history(self, game_key: str, q: Optional[dict] = None) -> dict:
        q = q or {}
        acct, rows = self._history_rows(game_key, q)
        if acct is None:
            return {"empty": True, "total": 0, "rows": [], "page": 1, "pages": 1, "pageSize": 20}
        size = max(1, min(int(q.get("pageSize") or 20), 100000))
        pages = max(1, -(-len(rows) // size))
        page = min(max(1, int(q.get("page") or 1)), pages)
        return {
            "empty": False, "total": len(rows), "page": page, "pages": pages, "pageSize": size,
            "rows": [self._row(r) for r in rows[(page - 1) * size: page * size]],
            "hasLowerBound": any(r.lb and r.rarity == 5 for r in rows), "hasAssumed": any(r.result and r.result["assumed"] for r in rows),
            "timeline": self._timeline(rows),
        }

    TIMELINE_MAX = 1000

    @staticmethod
    def _timeline(rows: list) -> list[dict]:
        """The 5-stars of the filtered rows, oldest first, for the strip above the History table (all pages, not only the one shown)."""
        fives = sorted((r for r in rows if r.rarity == 5), key=lambda r: (r.time, int(r.id)))[-Tracker.TIMELINE_MAX:]
        return [{"n": r.n, "time": engine.iso(r.time), "name": r.name, "pity": r.pity, "lowerBound": r.lb, "group": r.group,
                 "kind": r.result["kind"] if r.result else None} for r in fives]

    def history_csv(self, game_key: str, q: Optional[dict] = None) -> Optional[dict]:
        """The filtered rows of the History screen as CSV text (all pages), or None when nothing is stored."""
        acct, rows = self._history_rows(game_key, {**(q or {}), "page": 1})
        if acct is None:
            return None
        game = GAMES[game_key]
        labels = {g.key: g.short for g in game.groups}
        top = {5: "S", 4: "A", 3: "B"} if game_key == "zzz" else {5: "5", 4: "4", 3: "3"}
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        w.writerow(["number", "time", "name", "rarity", "banner", "pity", "result"])
        for r in rows:
            w.writerow([r.n, engine.iso(r.time), pulls.cell(r.name), top[r.rarity], labels.get(r.group, r.group), r.pity, r.result["text"] if r.result else ""])
        return {"name": f"history_{game_key}_{time.strftime('%Y-%m-%d', time.gmtime(self.clock()))}.csv", "mime": "text/csv", "text": out.getvalue(), "count": len(rows)}

    def stats(self, game_key: str, q: Optional[dict] = None) -> dict:
        q = q or {}
        game = GAMES[game_key]
        acct = self.active(game_key)
        if not acct or not self.store.count(game_key, acct["uid"]):
            return {"empty": True}
        group = q.get("group") or "all"
        keys = [g.key for g in game.ui_groups()] if group == "all" else [group]
        if any(k not in {g.key for g in game.ui_groups()} for k in keys):
            return {"empty": True}
        dates = q.get("dates") if q.get("dates") in DATE_RANGES else "all"
        out = engine.stats(game, self.derived(game_key, acct["uid"]), keys, self._since(dates), self._settings, OFFICIAL.get(game_key))
        return {"empty": False, "group": group, "dates": dates, **out}

    # ---- accounts ------------------------------------------------------------------------------------------------------------

    def save_nickname(self, game: str, uid: str, nickname: str) -> bool:
        return self.store.set_nickname(game, str(uid), str(nickname).strip()[:40])

    def remove_account(self, game: str, uid: str) -> bool:
        if game not in GAMES or not self.store.account(game, str(uid)):
            return False
        self._safety_backup("removing an account")
        self.store.remove_account(game, str(uid))
        return True

    def set_active(self, game: str, uid: str) -> bool:
        """Make a stored account the one the screens show for its game (False: no such account)."""
        if game not in GAMES or not self.store.account(game, str(uid)):
            return False
        accounts = self.store.accounts(game)
        if accounts and accounts[0]["uid"] == str(uid):
            return True
        newest = max((a["active_at"] or 0) for a in accounts)
        self.store.touch(game, str(uid), now=max(int(self.clock()), int(newest) + 1))
        return True

    # ---- backup ----------------------------------------------------------------------------------------------------------------

    BACKUP_KEEP = 10             # backups kept in the folder (the oldest are deleted)
    BACKUP_MIN_AGE = 6 * 3600    # seconds: an automatic backup is skipped when the newest backup is younger than this

    def backup_folder(self) -> Path:
        chosen = self._settings["backupFolder"].strip()
        return Path(chosen) if chosen else self.data_dir / "backups"

    def backup(self, force: bool = False) -> Optional[Path]:
        """Copy the pull database to the backup folder (Settings > Storage). None when there is nothing to save, or (not `force`) when the
        newest backup is recent. The newest BACKUP_KEEP files are kept."""
        if not any(self.store.count(a["game"], a["uid"]) for a in self.store.accounts()):
            return None
        folder = self.backup_folder()
        old = self.backup_files()
        if old and not force and self._backup_is_recent(old[-1]):
            return None
        dest = self.store.backup_to(folder / f"pulls-{time.strftime('%Y%m%d-%H%M%S', time.localtime(self.clock()))}.sqlite")
        for f in ([f for f in old if f != dest] + [dest])[:-self.BACKUP_KEEP]:
            try:
                f.unlink()
            except OSError:
                log.debug("Could not delete the old backup %s", f, exc_info=True)
        return dest

    def _backup_is_recent(self, f: Path) -> bool:
        try:
            return self.clock() - f.stat().st_mtime < self.BACKUP_MIN_AGE
        except OSError:                 # removed after the listing: there is no recent backup
            return False

    def backup_files(self) -> list[Path]:
        """The backups this program wrote in the backup folder, oldest first. Only names of the form pulls-YYYYMMDD-HHMMSS.sqlite count: the
        folder can be one the person chose (Settings > Storage) and hold other files, and the deletion of old backups must never reach them."""
        folder = self.backup_folder()
        if not folder.is_dir():
            return []
        found = []
        for f in folder.iterdir():
            if _BACKUP_NAME.fullmatch(f.name) and f.is_file():
                try:
                    found.append((f.stat().st_mtime, f))
                except OSError:
                    continue            # removed meanwhile
        return [f for _, f in sorted(found)]

    def backup_info(self) -> dict:
        """What Settings > Storage shows about the backups: {folder, count, mb, newest: ISO time or None}."""
        sizes, newest = 0, None
        files = self.backup_files()
        for f in files:                  # a file can be removed after the list was made (the folder may be one that a sync program or the person uses)
            try:
                st = f.stat()
            except OSError:
                continue
            sizes += st.st_size
            newest = st.st_mtime         # the list is oldest first
        return {"folder": str(self.backup_folder()), "count": len(files), "mb": round(sizes / 1e6, 1),
                "newest": engine.iso(int(newest)) if newest is not None else None}

    def _safety_backup(self, why: str) -> None:
        """A copy of the database before something that deletes or replaces pulls (when Settings has automatic backup on). A failure is logged and
        does not stop the action: the person already confirmed it."""
        if not self._settings["autoBackup"]:
            return
        try:
            path = self.backup(force=True)
            if path:
                log.info("Backup before %s: %s", why, path)
        except Exception:      # noqa: BLE001
            log.exception("The backup before %s could not be written", why)

    # ---- export ----------------------------------------------------------------------------------------------------------------

    def export(self, game_key: str, fmt: str, dates: str) -> dict:
        """{name, mime, text, count}, or {empty: True}. Every stored pull of the account is exported, the Beginner banner included."""
        game = GAMES[game_key]
        acct = self.active(game_key)
        if not acct or not self.store.count(game_key, acct["uid"]):
            return {"empty": True}
        since = self._since(dates if dates in DATE_RANGES else "all")
        rows = [r for r in self.store.pulls(game_key, acct["uid"]) if r["time"] >= since]
        stamp = time.strftime("%Y%m%d", time.gmtime(self.clock()))
        if fmt == "csv":
            text = pulls.export_csv(game, rows, acct["utc_offset"])
            return {"name": f"export_{game_key}_{stamp}.csv", "mime": "text/csv", "text": text, "count": len(rows)}
        doc = pulls.export_uigf(game, acct["uid"], rows, acct["utc_offset"], acct["lang"] or "en-us", int(self.clock()))
        return {"name": f"uigf_{game_key}_{stamp}.json", "mime": "application/json", "text": json.dumps(doc, ensure_ascii=False, indent=1), "count": len(rows)}

    # ---- import ----------------------------------------------------------------------------------------------------------------

    def import_preview(self, name: str, text: str, game_hint: Optional[str] = None) -> dict:
        """Read a file and say what importing it would do; nothing is written. The parsed records are kept for import_apply (preview["token"])."""
        base = {"name": name, "game": None, "uid": None, "format": "Unknown", "records": 0, "newRecords": 0, "stored": 0, "conflicts": [], "mismatch": None, "ok": False}
        if len(text) > MAX_IMPORT_BYTES:
            return {**base, "error": f"The file is larger than {MAX_IMPORT_BYTES // 2 ** 20} MB."}
        try:
            text = text.lstrip("\ufeff")          # a byte order mark that the file starts with
            first = text.lstrip()[:200].split("\n", 1)[0]
            if (name or "").lower().endswith(".csv") or (not first.startswith("{") and "," in first and "id" in first):
                if game_hint not in GAMES:
                    raise pulls.ImportError_("Open Settings > Import / Export from the game the CSV file belongs to.")
                acct = self.active(game_hint)
                if not acct:
                    raise pulls.ImportError_("A CSV file has no UID. Sync or import a UIGF file for this game first, so the pulls have an account.")
                parsed = pulls.read_csv(text, GAMES[game_hint], acct["uid"], acct["utc_offset"])
            else:
                parsed = pulls.read_uigf(text)
        except pulls.ImportError_ as e:
            return {**base, "error": str(e)}
        stored = records = new = 0
        conflicts, mismatch = [], None
        for e in parsed["entries"]:
            records += len(e["items"])
            have = self.store.existing(e["game"], e["uid"], [i["id"] for i in e["items"]])
            e["conflicts"] = []
            for i in e["items"]:
                h = have.get(i["id"])
                if h is None:
                    new += 1
                    continue
                if abs(h[0] - i["time"]) > 1:
                    e["conflicts"].append({"id": i["id"], "reason": "same ID, time differs"})
                elif h[2] != i["rank_raw"] or h[3] != i["gacha_type"]:
                    e["conflicts"].append({"id": i["id"], "reason": "same ID, rarity or banner differs"})
                else:
                    stored += 1
            conflicts += e["conflicts"]
            known = self.store.accounts(e["game"])
            if known and not any(a["uid"] == e["uid"] for a in known) and mismatch is None:
                mismatch = {"found": e["uid"], "active": known[0]["uid"]}
        games = {e["game"] for e in parsed["entries"]}
        token = uuid.uuid4().hex
        parsed["mismatch"] = mismatch              # kept here: import_apply must not rely on the copy of the preview that the page sends back
        self._pending = {token: parsed}            # only the newest preview can be applied
        accounts = [f"{GAMES[e['game']].title} {e['uid']}" for e in parsed["entries"]]
        note = ("The file has several accounts: " + "; ".join(accounts) + ".") if len(parsed["entries"]) > 1 else ""
        if parsed["skipped"]:
            note = f"{note} {parsed['skipped']} records without a usable ID, time or rarity are skipped.".strip()
        return {**base, "ok": True, "token": token, "format": parsed["format"], "game": next(iter(games)) if len(games) == 1 else None,
                "uid": parsed["entries"][0]["uid"], "records": records, "newRecords": new, "stored": stored, "conflicts": conflicts, "mismatch": mismatch,
                **({"note": note} if note else {})}

    def import_apply(self, preview: dict, choice: str) -> dict:
        parsed = self._pending.get((preview or {}).get("token"))
        if parsed is None:
            return {"error": "The file preview is no longer available. Choose the file again."}
        if parsed.get("mismatch"):
            return {"error": "The file belongs to another account than the one stored for this game."}
        added = replaced = 0
        if choice == "file" and any(e["conflicts"] for e in parsed["entries"]):
            self._safety_backup("an import that replaces pulls")
        for e in parsed["entries"]:
            game = GAMES[e["game"]]
            label, offset = region_of(e["game"], e["uid"])
            self.store.ensure_account(e["game"], e["uid"], label, offset if label != "Unknown" else e["offset"])
            n_added = sum(self.store.add_pulls(e["game"], e["uid"], e["items"]).values())
            n_replaced = 0
            if choice == "file" and e["conflicts"]:
                ids = {c["id"] for c in e["conflicts"]}
                n_replaced = self.store.replace_pulls(e["game"], e["uid"], [i for i in e["items"] if i["id"] in ids])
            self.store.touch(e["game"], e["uid"])
            added, replaced = added + n_added, replaced + n_replaced
            log.info("Import: %s %s: %d new, %d replaced", game.title, e["uid"][:3] + "***", n_added, n_replaced)
        self._pending = {}
        return {"added": added, "replaced": replaced}
