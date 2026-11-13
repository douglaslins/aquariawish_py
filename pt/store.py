"""The pull database: data/pulls.sqlite (rebuild analysis section 4 and defects D1 to D4, D9).

One row per pull, keyed by (game, uid, id): the same ID of two games or two accounts is never one row (D1). The ID is kept as the text the
API sends and as an integer for ordering (D5: never a float). Rows are added with INSERT OR IGNORE, so pulls that are already stored are
never changed and never doubled (D2); what the API sent that the columns do not hold stays in `raw` (D9).

The store knows nothing about pity, banners or settings: it keeps rows and answers questions about them. Every write raises `version`;
the caches of pt/tracker.py are valid for one version.
"""
from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

log = logging.getLogger("pt.store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    game TEXT NOT NULL, uid TEXT NOT NULL, region TEXT NOT NULL DEFAULT '', utc_offset REAL NOT NULL DEFAULT 8,
    nickname TEXT NOT NULL DEFAULT '', last_sync INTEGER, active_at INTEGER NOT NULL DEFAULT 0, lang TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (game, uid));
CREATE TABLE IF NOT EXISTS pulls (
    game TEXT NOT NULL, uid TEXT NOT NULL, id TEXT NOT NULL, id_int INTEGER NOT NULL, gacha_type TEXT NOT NULL,
    item_id TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '', item_type TEXT NOT NULL DEFAULT '',
    rank_raw INTEGER NOT NULL, rarity INTEGER NOT NULL, time INTEGER NOT NULL, raw TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (game, uid, id));
CREATE INDEX IF NOT EXISTS pulls_order ON pulls (game, uid, gacha_type, id_int);
CREATE TABLE IF NOT EXISTS banner_archive (
    game TEXT NOT NULL, grp TEXT NOT NULL, title TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
    version TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT '', items TEXT NOT NULL DEFAULT '[]', rate_up TEXT NOT NULL DEFAULT '[]',
    seen INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (game, grp, title, start, end));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
SCHEMA_VERSION = "1"
CHUNK = 400         # ids per query (SQLite allows 999 variables per statement in older builds)

# A later version of the program that changes the tables adds an entry here: MIGRATIONS[n] changes a database of format n into format n+1
# (it receives the open connection; the program raises SCHEMA_VERSION to n+1). The database file is copied before the first step runs.
# The shared program is replaced by a newer .exe while data/pulls.sqlite stays, so a database of an older format has to be read.
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {}


def full_key(game: str, uid: str, group: str) -> str:
    """The meta key that says the complete history of a banner type of an account was fetched. pt/sync.py writes it; removing the account
    deletes it, because a later import of a partial history for the same UID must not be taken for a complete one."""
    return f"full:{game}:{uid}:{group}"


class DatabaseTooNew(RuntimeError):
    """The database was written by a newer version of the program than this one. It is left unchanged: older code does not know
    what the newer tables mean, and writing to them could damage them."""

    def __init__(self, found: int, supported: int):
        super().__init__(f"the pull database has format {found}; this version of the program reads up to format {supported}")
        self.found, self.supported = found, supported


def stored_schema(path) -> Optional[int]:
    """The format number a database file records (None when there is no file, no number, or the file cannot be read). Opens the file
    only to read; it never creates a database."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        db = sqlite3.connect(str(p), timeout=5)
        try:
            row = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        finally:
            db.close()
        return int(row[0]) if row else None
    except (sqlite3.Error, ValueError, TypeError):
        return None


def _is_damaged(e: sqlite3.DatabaseError) -> bool:
    """True for a file that SQLite cannot read as a database ("file is not a database", "malformed"). A file that is busy, locked,
    read only, on a full or unreadable disk, or cannot be opened (OperationalError) is not damaged: moving it aside for those would hide the
    person's pulls because of a condition that passes (an antivirus program or a sync tool holding the file, for example). The same holds
    for the other subclasses (IntegrityError, ProgrammingError, ...): they come from a condition or a bug of the program, not from the file.
    sqlite3 raises the plain DatabaseError for SQLITE_CORRUPT and SQLITE_NOTADB."""
    text = str(e).lower()
    return "not a database" in text or "malformed" in text or type(e) is sqlite3.DatabaseError


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.version = 0
        self.recovered: Optional[Path] = None       # set when a damaged database file had to be put aside
        self._lock = threading.RLock()
        self._db = self._open()

    # ---- opening -------------------------------------------------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            self._refuse_newer(db)              # before any statement that writes: a database of a newer format is left as it is
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.executescript(SCHEMA)
            db.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('schema', ?)", (SCHEMA_VERSION,))
            db.commit()
            self._check_format(db)
        except Exception:
            db.close()
            raise
        return db

    @staticmethod
    def _refuse_newer(db: sqlite3.Connection) -> None:
        """Raise DatabaseTooNew when an existing database records a newer format. Reads only: it creates and changes nothing."""
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
            return
        row = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        try:
            found = int(row[0]) if row else 0
        except (ValueError, TypeError):
            return                              # not a number: _check_format reports it after the tables exist
        if found > int(SCHEMA_VERSION):
            raise DatabaseTooNew(found, int(SCHEMA_VERSION))

    def _check_format(self, db: sqlite3.Connection) -> None:
        """Refuse a database of a newer format, bring one of an older format up to date."""
        target = int(SCHEMA_VERSION)
        stored = int(db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0])
        if stored > target:
            raise DatabaseTooNew(stored, target)
        if stored == target:
            return
        self._copy_before_migration(db, stored)
        for n in range(stored, target):
            step = MIGRATIONS.get(n)
            if step is None:
                raise RuntimeError(f"there is no migration from database format {n} to {n + 1}")
            try:
                db.execute("BEGIN")
                step(db)
                db.execute("UPDATE meta SET value=? WHERE key='schema'", (str(n + 1),))
                db.commit()
            except Exception:
                db.rollback()
                raise
            log.info("The pull database was changed from format %d to %d", n, n + 1)

    def _copy_before_migration(self, db: sqlite3.Connection, stored: int) -> None:
        aside = self.path.with_name(f"{self.path.name}.before-format-{stored}")
        if aside.exists():
            return
        copy = sqlite3.connect(str(aside))
        try:
            db.backup(copy)
        finally:
            copy.close()
        log.info("Copy of the pull database before the change of format: %s", aside)

    def _open(self) -> sqlite3.Connection:
        try:
            return self._connect()
        except sqlite3.DatabaseError as e:       # "file is not a database" / "malformed": keep the file for the user, start a new one
            if not _is_damaged(e):
                raise
            stamp = time.strftime("%Y%m%d-%H%M%S")
            aside = self.path.with_name(f"{self.path.name}.damaged-{stamp}")
            n = 1
            while any(Path(str(aside) + s).exists() for s in ("", "-wal", "-shm")):       # a copy from the same second is never overwritten
                n += 1
                aside = self.path.with_name(f"{self.path.name}.damaged-{stamp}-{n}")
            log.exception("The pull database cannot be read; it is moved to %s and a new one is started", aside)
            for suffix in ("", "-wal", "-shm"):
                p = Path(str(self.path) + suffix)
                if p.exists():
                    p.replace(Path(str(aside) + suffix))
            self.recovered = aside
            return self._connect()

    def close(self) -> None:
        with self._lock:
            try:
                self._db.close()
            except sqlite3.Error:
                log.debug("close", exc_info=True)

    def _changed(self) -> None:
        self.version += 1

    # ---- accounts ------------------------------------------------------------------------------------------------------------

    def accounts(self, game: Optional[str] = None) -> list[dict]:
        with self._lock:
            q = "SELECT * FROM accounts" + (" WHERE game=?" if game else "") + " ORDER BY game, active_at DESC, rowid DESC"
            return [dict(r) for r in self._db.execute(q, (game,) if game else ())]

    def account(self, game: str, uid: str) -> Optional[dict]:
        with self._lock:
            r = self._db.execute("SELECT * FROM accounts WHERE game=? AND uid=?", (game, uid)).fetchone()
            return dict(r) if r else None

    def active(self, game: str) -> Optional[dict]:
        """The account of a game the screens show: the one a sync or an import touched last."""
        found = self.accounts(game)
        return found[0] if found else None

    def ensure_account(self, game: str, uid: str, region: str, utc_offset: float, lang: str = "") -> dict:
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO accounts (game, uid, region, utc_offset, lang) VALUES (?,?,?,?,?)", (game, uid, region, utc_offset, lang))
            self._db.commit()
            self._changed()
            return self.account(game, uid)

    def touch(self, game: str, uid: str, synced: bool = False, now: Optional[int] = None) -> None:
        """Make this the account the screens show; with `synced`, also record the time of the sync."""
        t = int(now if now is not None else time.time())
        with self._lock:
            if synced:
                self._db.execute("UPDATE accounts SET active_at=?, last_sync=? WHERE game=? AND uid=?", (t, t, game, uid))
            else:
                self._db.execute("UPDATE accounts SET active_at=? WHERE game=? AND uid=?", (t, game, uid))
            self._db.commit()
            self._changed()

    def update_account(self, game: str, uid: str, utc_offset: Optional[float] = None, lang: Optional[str] = None, region: Optional[str] = None) -> None:
        """Change what a sync learned about an account (the server's UTC offset, the language of the names, the region name)."""
        with self._lock:
            with self._db:                      # all statements commit together, or none does (a failure rolls back)
                if utc_offset is not None:
                    self._db.execute("UPDATE accounts SET utc_offset=? WHERE game=? AND uid=?", (utc_offset, game, uid))
                if lang:
                    self._db.execute("UPDATE accounts SET lang=? WHERE game=? AND uid=?", (lang, game, uid))
                if region:
                    self._db.execute("UPDATE accounts SET region=? WHERE game=? AND uid=?", (region, game, uid))
            self._changed()

    def set_nickname(self, game: str, uid: str, nickname: str) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE accounts SET nickname=? WHERE game=? AND uid=?", (nickname, game, uid))
            self._db.commit()
            self._changed()
            return cur.rowcount > 0

    def remove_account(self, game: str, uid: str) -> int:
        """Delete an account and every pull of it. Returns the number of pulls deleted."""
        with self._lock:
            with self._db:                      # the three deletes commit together, or none does (a failure rolls back)
                n = self._db.execute("DELETE FROM pulls WHERE game=? AND uid=?", (game, uid)).rowcount
                self._db.execute("DELETE FROM accounts WHERE game=? AND uid=?", (game, uid))
                prefix = full_key(game, uid, "")
                self._db.execute("DELETE FROM meta WHERE substr(key, 1, ?) = ?", (len(prefix), prefix))      # not LIKE: a UID may hold _ or %
            self._changed()
            return n

    # ---- pulls ---------------------------------------------------------------------------------------------------------------

    def add_pulls(self, game: str, uid: str, records: Iterable[dict]) -> dict[str, int]:
        """Store the records that are new. Returns the number of new rows by gacha_type. Stored rows are never changed."""
        added: dict[str, int] = {}
        with self._lock:
            try:
                for r in records:
                    cur = self._db.execute(
                        "INSERT OR IGNORE INTO pulls (game, uid, id, id_int, gacha_type, item_id, name, item_type, rank_raw, rarity, time, raw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (game, uid, r["id"], int(r["id"]), r["gacha_type"], r["item_id"], r["name"], r["item_type"], r["rank_raw"], r["rarity"], r["time"],
                         json.dumps(r.get("raw") or {}, separators=(",", ":"))))
                    if cur.rowcount:
                        added[r["gacha_type"]] = added.get(r["gacha_type"], 0) + 1
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
            if added:
                self._changed()
        return added

    def replace_pulls(self, game: str, uid: str, records: Iterable[dict]) -> int:
        """Overwrite stored rows with the same ID (import with "use file" on a conflict). Returns the number of rows changed."""
        n = 0
        with self._lock:
            try:
                for r in records:
                    n += self._db.execute(
                        "UPDATE pulls SET gacha_type=?, item_id=?, name=?, item_type=?, rank_raw=?, rarity=?, time=?, raw=? WHERE game=? AND uid=? AND id=?",
                        (r["gacha_type"], r["item_id"], r["name"], r["item_type"], r["rank_raw"], r["rarity"], r["time"],
                         json.dumps(r.get("raw") or {}, separators=(",", ":")), game, uid, r["id"])).rowcount
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
            if n:
                self._changed()
        return n

    def existing(self, game: str, uid: str, ids: list[str]) -> dict[str, tuple]:
        """{id: (time, name, rank_raw, gacha_type)} of the given ids that are stored."""
        found: dict[str, tuple] = {}
        with self._lock:
            for i in range(0, len(ids), CHUNK):
                part = ids[i:i + CHUNK]
                q = f"SELECT id, time, name, rank_raw, gacha_type FROM pulls WHERE game=? AND uid=? AND id IN ({','.join('?' * len(part))})"
                for r in self._db.execute(q, (game, uid, *part)):
                    found[r["id"]] = (r["time"], r["name"], r["rank_raw"], r["gacha_type"])
        return found

    def pulls(self, game: str, uid: str, codes: Optional[Iterable[str]] = None) -> list[sqlite3.Row]:
        """Rows of an account, oldest first (by ID, which grows with time), limited to the given gacha_type codes when given."""
        with self._lock:
            if codes is None:
                return self._db.execute("SELECT * FROM pulls WHERE game=? AND uid=? ORDER BY id_int", (game, uid)).fetchall()
            codes = list(codes)
            q = f"SELECT * FROM pulls WHERE game=? AND uid=? AND gacha_type IN ({','.join('?' * len(codes))}) ORDER BY id_int"
            return self._db.execute(q, (game, uid, *codes)).fetchall()

    def count(self, game: str, uid: str) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM pulls WHERE game=? AND uid=?", (game, uid)).fetchone()[0]

    def count_codes(self, game: str, uid: str, codes: Iterable[str]) -> int:
        codes = list(codes)
        with self._lock:
            q = f"SELECT COUNT(*) FROM pulls WHERE game=? AND uid=? AND gacha_type IN ({','.join('?' * len(codes))})"
            return self._db.execute(q, (game, uid, *codes)).fetchone()[0]

    def window_counts(self, game: str, uid: str, codes: list[str], start: int, end: int) -> list[sqlite3.Row]:
        """Pull counts by rarity and gacha_type with a time between start and end (unix seconds)."""
        with self._lock:
            q = (f"SELECT rarity, gacha_type, COUNT(*) AS n FROM pulls WHERE game=? AND uid=? AND gacha_type IN ({','.join('?' * len(codes))}) "
                 "AND time BETWEEN ? AND ? GROUP BY rarity, gacha_type")
            return self._db.execute(q, (game, uid, *codes, start, end)).fetchall()

    # ---- banner archive ------------------------------------------------------------------------------------------------------

    def archive_banner(self, game: str, grp: str, title: str, start: int, end: int, version: str, kind: str, items: list, rate_up: list,
                       seen: Optional[int] = None) -> bool:
        """Remember a banner (inserted, or updated with the newest data when it is seen again). True when the row is new."""
        with self._lock:
            exists = self._db.execute("SELECT 1 FROM banner_archive WHERE game=? AND grp=? AND title=? AND start=? AND end=?", (game, grp, title, start, end)).fetchone()
            self._db.execute(
                "INSERT OR REPLACE INTO banner_archive (game, grp, title, start, end, version, kind, items, rate_up, seen) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (game, grp, title, start, end, version, kind, json.dumps(items, ensure_ascii=False), json.dumps(rate_up, ensure_ascii=False),
                 int(seen if seen is not None else time.time())))
            self._db.commit()
            self._changed()
            return not exists

    def banners(self, game: str) -> list[dict]:
        with self._lock:
            out = []
            for r in self._db.execute("SELECT * FROM banner_archive WHERE game=? ORDER BY start, grp, title", (game,)):
                d = dict(r)
                d["items"], d["rate_up"] = json.loads(d["items"]), json.loads(d["rate_up"])
                out.append(d)
            return out

    # ---- meta, backup --------------------------------------------------------------------------------------------------------

    def meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            r = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return r[0] if r else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, value))
            self._db.commit()

    def backup_to(self, dest: Path) -> Path:
        """A consistent copy of the database in one file (SQLite's online backup; safe while the program runs)."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        try:
            with self._lock:
                out = sqlite3.connect(str(tmp))
                try:
                    self._db.backup(out)
                finally:
                    out.close()
            tmp.replace(dest)
        except BaseException:
            with contextlib.suppress(OSError):      # a failed backup (disk full, folder gone) leaves no half file behind
                tmp.unlink(missing_ok=True)
            raise
        return dest
