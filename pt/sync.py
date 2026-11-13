"""The sync: find the game's saved link, read the pull history from the game's service and merge the new pulls into the database.

Four steps, the ones the Sync screen lists:
  1 Find the game cache        the newest cache file of the game (pt/locate.py)
  2 Find a valid link          the addresses with a key in it are tested, newest first, with a one-item request
  3 Fetch the history          every banner type, page by page; each banner type is merged into the database when it is complete
  4 Merge into the local database   the account's sync time, the full-history marks, the summary

What reaches the page is a sequence of events (`emit`), in the shapes js/api.js documents:
  {type:'step', n, state: running|ok|failed, text}   {type:'fetch', group, label, page, pages, items, done}
  {type:'wait', sec, retry, of, stop}                {type:'done', result}     {type:'error', code, ...}
After a cancel nothing more is emitted. Errors have the codes the page knows (js/views.js ERRORS): cache_missing, link_expired, rate_limited,
uid_mismatch, network, server_error, cache_unreadable, internal.

Which pulls are fetched: a banner type whose complete history was fetched before (marked in the database) is fetched only until the first
page that holds a pull that is already stored; otherwise (the first sync, a banner type that was never completed, a sync that was cancelled
in it) it is fetched to the end. A banner type is only marked complete when the service said "no more pages", never after a stop at a stored pull.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from typing import Callable, Optional

from pt import client as client_mod, engine, locate, pulls
from pt.client import Cancelled, Client, LinkExpired, NetworkError, Problem, RateLimited
from pt.games import GAMES, region_of
from pt.store import full_key
from pt.tracker import Tracker

log = logging.getLogger("pt.sync")

MAX_CANDIDATES = locate.MAX_CANDIDATES
CANDIDATE_PAUSE = 0.3        # seconds between two tested addresses
STALE_HOURS = 24             # a saved link is expected to be unusable after about a day


class UidMismatch(Problem):
    code = "uid_mismatch"


class CacheMissing(Problem):
    code = "cache_missing"


class CacheUnreadable(Problem):
    code = "cache_unreadable"


def humanize(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} min"
    if seconds < 36 * 3600:
        return f"{round(seconds / 3600)} h"
    return f"{round(seconds / 86400)} days"


def _age(f, clock: Callable[[], float]) -> Optional[float]:
    """Seconds since a cache file changed; None when it is gone (the game can remove or replace a file after the search found it)."""
    try:
        return clock() - f.stat().st_mtime
    except OSError:
        return None


class Syncer:
    def __init__(self, tracker: Tracker, game_key: str, emit: Callable[[dict], None], cancel: Optional[threading.Event] = None, *,
                 http: Callable = client_mod.urllib_get, sleep: Optional[Callable[[float], None]] = None, log_root=None,
                 new_account: bool = False, pause: float = client_mod.PAUSE, rate_wait: float = client_mod.RATE_WAIT,
                 candidate_pause: float = CANDIDATE_PAUSE, clock: Callable[[], float] = time.time):
        self.t, self.game = tracker, GAMES[game_key]
        self._emit, self.cancel = emit, cancel or threading.Event()
        self.http, self.sleep, self.log_root = http, sleep, log_root
        self.new_account, self.pause, self.rate_wait, self.candidate_pause, self.clock = new_account, pause, rate_wait, candidate_pause, clock
        self.notes: list[str] = []

    # ---- events ----------------------------------------------------------------------------------------------------------------

    def emit(self, ev: dict) -> None:
        if not self.cancel.is_set():
            self._emit({"game": self.game.key, **ev})

    def step(self, n: int, state: str, text: str = "") -> None:
        self.emit({"type": "step", "n": n, "state": state, "text": text})

    def _pause(self, seconds: float) -> None:
        (self.sleep or self.cancel.wait)(seconds)
        if self.cancel.is_set():
            raise Cancelled()

    # ---- run ---------------------------------------------------------------------------------------------------------------------

    def run(self) -> Optional[dict]:
        """Do the sync. Returns the result (also sent as the 'done' event), or None when it was cancelled or ended with an error (the 'error'
        event says which)."""
        step = 1
        try:
            client = self._find_link()
            step = 3
            result = self._fetch_and_merge(client)
            self.emit({"type": "done", "result": result})
            return result
        except Cancelled:
            log.info("%s: sync cancelled", self.game.key)
            return None
        except Problem as p:
            log.warning("%s: sync stopped: %s (%s)", self.game.key, p.code, p.message)
            self._fail(step, p)
            return None
        except Exception as e:      # noqa: BLE001 - the screen must get an answer whatever went wrong
            log.exception("%s: sync failed", self.game.key)
            self._fail(step, Problem(f"{type(e).__name__}: {e}"), code="internal")
            return None

    def _fail(self, step: int, p: Problem, code: Optional[str] = None) -> None:
        if step == 3:
            self.step(3, "failed")
        self.emit({"type": "error", "code": code or p.code, "message": p.message, **p.params})

    # ---- steps 1 and 2 -----------------------------------------------------------------------------------------------------------

    def _find_link(self) -> Client:
        game = self.game
        chosen = self.t.settings()["folders"].get(game.key, "")
        self.step(1, "running")
        files = locate.cache_files(game.key, chosen, self.log_root)
        if not files:
            self.step(1, "failed")
            raise CacheMissing("no web cache of the game was found")
        age = _age(files[0], self.clock)
        self.step(1, "ok", f"cache file changed {humanize(age)} ago" if age is not None else "")      # a file that is gone is reported by step 2

        self.step(2, "running")
        links, unreadable = [], None
        for f in files[:3]:
            try:
                links = locate.candidate_links(locate.read_cache(f), game)
            except OSError as e:
                unreadable = e
                continue
            if links:
                break
        if not links:
            self.step(2, "failed")
            if unreadable is not None:
                raise CacheUnreadable(str(unreadable))
            raise CacheMissing("the cache has no saved link")
        used_age = _age(f, self.clock)          # `f` is the cache file the links come from: the newest that has any (not always the newest file)
        log.info("%s: %d saved links (testing %d) from cache file %d of %d, which changed %s ago", game.key, len(links), min(len(links), MAX_CANDIDATES),
                 files.index(f) + 1, len(files), humanize(used_age) if used_age is not None else "? (time unknown)")
        links = links[:MAX_CANDIDATES]
        seen_expired, seen_api, seen_net = False, None, None
        expired_n = net_n = 0
        for n, link in enumerate(links, 1):
            if self.cancel.is_set():
                raise Cancelled()
            net_hosts = hosts = 0
            for host in locate.hosts_for(game, link):
                hosts += 1
                c = Client(game, link, host, http=self.http, sleep=self.sleep, cancel=self.cancel, pause=self.pause, rate_wait=self.rate_wait)
                try:
                    c.check()
                except RateLimited:
                    pass                    # the key was accepted; the fetch waits as the service asks
                except LinkExpired:
                    seen_expired = True
                    expired_n += 1
                    break                   # the key is wrong on every host
                except NetworkError as e:
                    seen_net, net_hosts, net_n = e, net_hosts + 1, net_n + 1
                    log.info("%s: %s did not answer: %s", game.key, host, e.message)
                    continue
                except Problem as e:
                    seen_api = e
                    log.info("%s: %s%s answered: %s", game.key, host, game.api_path, e.message)      # the host and path are not secret, the key is not logged
                    continue
                log.info("%s: link accepted: %s, %d of %d tested", game.key, locate.describe(link), n, len(links))
                self.step(2, "ok", f"{n} of {len(links)} candidates tested")
                return c
            if n == 1 and hosts and net_hosts == hosts:
                self.step(2, "failed")
                raise seen_net                      # nothing answers: offline, do not test the other addresses
            self.step(2, "running", f"{n} of {len(links)} candidates tested")
            if n < len(links):
                self._pause(self.candidate_pause)
        self.step(2, "failed")
        log.info("%s: no saved link was accepted: %d tested, %d expired, %d without an answer", game.key, len(links), expired_n, net_n)
        if seen_expired:
            raise LinkExpired("none of the saved links is accepted")
        if seen_api is not None:
            raise seen_api
        raise seen_net or LinkExpired("none of the saved links is accepted")

    # ---- step 3 and 4 ---------------------------------------------------------------------------------------------------------------

    def _plan(self) -> list[tuple[str, str, str, bool]]:
        """(group key, label, request code, essential) of every banner type to fetch. A failure of a non-essential one is ignored."""
        plan = [(g.key, g.label, g.request, True) for g in self.game.groups]
        plan += [(f"x{c}", f"Banner type {c}", c, False) for c in self.game.extra_codes]
        return plan

    def _fetch_and_merge(self, client: Client) -> dict:
        game, store = self.game, self.t.store
        self.step(3, "running")
        uid: Optional[str] = None
        offset: Optional[float] = None
        account_check, lang = "", ""
        added_groups: list[dict] = []
        total_new = total_known = new5 = 0
        unknown: set[str] = set()
        skipped = 0
        backup_done = False
        for gkey, label, code, essential in self._plan():
            records: list[dict] = []
            known_ids: set[str] = set()
            complete_before = False
            reached_end = True
            last_page = 0
            try:
                for number, items, data in client.pages(code, lambda w: self.emit({"type": "wait", **w})):
                    if uid is None:
                        uid, offset, account_check = self._identify(items, data)
                        lang = str(items[0].get("lang") or "")
                    complete_before = store.meta(full_key(game.key, uid, gkey)) == "1"
                    for it in items:
                        n = pulls.normalize(game, it, offset)
                        if n is None:
                            skipped += 1
                        else:
                            records.append(n)
                            if game.group_of(n["gacha_type"]) is None and n["gacha_type"] not in game.extra_codes:
                                unknown.add(n["gacha_type"])
                    known_here = set(store.existing(game.key, uid, [str(i.get("id")) for i in items]))
                    known_ids |= known_here
                    last_page = number
                    if essential:
                        g0 = next(x for x in game.groups if x.key == gkey)
                        estimate = math.ceil(store.count_codes(game.key, uid, g0.codes) / client_mod.PAGE_SIZE) + 1 if complete_before else 0
                        self.emit({"type": "fetch", "group": gkey, "label": label, "page": number, "pages": max(number + 1, estimate), "items": len(records), "done": False})
                    if complete_before and known_here:
                        reached_end = False          # everything older is stored already
                        break
            except Problem as p:
                if essential or isinstance(p, (LinkExpired, RateLimited, NetworkError)):
                    raise
                log.info("%s: banner type %s was not fetched: %s", game.key, code, p.message)
                continue
            if essential:
                self.emit({"type": "fetch", "group": gkey, "label": label, "page": max(1, last_page), "pages": max(1, last_page), "items": len(records), "done": True})
            if not records:
                log.info("%s: %s: the service sent no pulls", game.key, label)
            if uid is None or not records:
                if uid is not None and reached_end:
                    store.set_meta(full_key(game.key, uid, gkey), "1")      # an empty banner type is a complete one
                continue
            if not backup_done:
                backup_done = True
                self._backup()
            region, default_offset = region_of(game.key, uid)
            store.ensure_account(game.key, uid, region, offset if offset is not None else default_offset, lang)
            fresh = [r for r in records if r["id"] not in known_ids]
            # what the service sent for this banner type, to tell a service that has no new pull from a program that missed it (no ids, no key)
            log.info("%s: %s: the service sent %d pulls in %d pages, the newest made %s ago; %d are new", game.key, label, len(records), last_page,
                     humanize(self.clock() - max(r["time"] for r in records)), len(fresh))
            added = store.add_pulls(game.key, uid, fresh)
            store.touch(game.key, uid)                  # the screens show this account even if a later banner type fails
            n_added = sum(added.values())
            total_new += n_added
            total_known += len(records) - len(fresh)
            new5 += sum(1 for r in fresh if r["rarity"] == 5)
            if reached_end:
                store.set_meta(full_key(game.key, uid, gkey), "1")
            g = next((x for x in game.groups if x.key == gkey), None)
            if g is not None and (g.visible or n_added):
                added_groups.append({"key": gkey, "label": g.short, "n": n_added})
            elif g is None and n_added:
                added_groups.append({"key": gkey, "label": label, "n": n_added})
        if uid is None:
            self.step(3, "ok")
            self.step(4, "ok")
            return self._empty_result()
        self.step(3, "ok")

        self.step(4, "running")
        region, default_offset = region_of(game.key, uid)
        store.ensure_account(game.key, uid, region, offset if offset is not None else default_offset, lang)
        store.update_account(game.key, uid, utc_offset=offset, lang=lang or None)
        store.touch(game.key, uid, synced=True, now=int(self.clock()))
        if unknown:
            self.notes.append(f"Pulls of banner type {', '.join(sorted(unknown))} were stored; this application has no screen for them.")
        if not client.names_english:
            self.notes.append("The names of the pulls are not in English, so the 50/50 results (which are matched to English banner data) may be wrong.")
        if skipped:
            self.notes.append(f"{skipped} items without a usable ID, time or rarity were skipped.")
        self.step(4, "ok")
        return {
            "at": engine.iso(int(self.clock())), "added": {"total": total_new, "groups": added_groups}, "stored": total_known, "new5": new5,
            "accountCheck": account_check, "notes": self.notes,
        }

    def _identify(self, items: list, data: dict) -> tuple[str, float, str]:
        """The account of a history (from its items) and the server's UTC offset; stops with uid_mismatch when it is not the stored account."""
        game, store = self.game, self.t.store
        uid = str(items[0].get("uid") or "").strip()
        if not uid:
            raise Problem("the answer has no UID")
        if not (uid.isascii() and uid.isdigit() and len(uid) <= 20):      # it becomes an account, a key of the database and text on the screens
            raise Problem("the answer has a UID that is not a number")
        tz = data.get("region_time_zone")
        offset = float(tz) if isinstance(tz, (int, float)) and not isinstance(tz, bool) and -12 <= tz <= 14 else float(region_of(game.key, uid)[1])
        stored = store.accounts(game.key)
        if any(a["uid"] == uid for a in stored):
            return uid, offset, "UID matches the stored account"
        if not stored:
            return uid, offset, "First account of this game, added"
        if not self.new_account:
            raise UidMismatch("the history belongs to another account", found=uid, active=stored[0]["uid"])
        return uid, offset, "Another account, added as a new one"

    def _backup(self) -> None:
        if not self.t.settings()["autoBackup"]:
            return
        try:
            path = self.t.backup()
            if path:
                log.info("Backup before the sync: %s", path)
        except Exception as e:      # noqa: BLE001 - a failed backup must not stop the sync
            log.exception("Backup failed")
            self.notes.append(f"The backup before the sync could not be written ({type(e).__name__}).")

    def _empty_result(self) -> dict:
        self.notes.append("The service returned no pulls for this link.")
        return {"at": engine.iso(int(self.clock())), "added": {"total": 0, "groups": []}, "stored": 0, "new5": 0,
                "accountCheck": "No pulls found, so the account could not be identified", "notes": self.notes}


# ---- sync at start ---------------------------------------------------------------------------------------------------------------

def can_auto_sync(tracker: Tracker, game_key: str, log_root=None, clock: Callable[[], float] = time.time) -> bool:
    """True when a short sync can run now: the game has an account whose full history was fetched before (so the sync stops at the first
    page it already has) and a saved link younger than STALE_HOURS exists. The first sync is always the user's."""
    game = GAMES[game_key]
    acct = tracker.store.active(game_key)
    if not acct or not tracker.settings()["track"].get(game_key, True):
        return False
    if any(tracker.store.meta(full_key(game_key, acct["uid"], g.key)) != "1" for g in game.ui_groups()):
        return False
    files = locate.cache_files(game_key, tracker.settings()["folders"].get(game_key, ""), log_root)
    age = _age(files[0], clock) if files else None
    return age is not None and age < STALE_HOURS * 3600


def _stop_when(stop: threading.Event, cancelled: Callable[[], bool], every: float = 0.2) -> Callable[[], None]:
    """A function for a thread: sets `stop` as soon as `cancelled()` is true, and ends when `stop` is set by anyone."""
    def watch() -> None:
        while not stop.wait(every):
            if cancelled():
                stop.set()
    return watch


def auto_sync(tracker: Tracker, cancelled: Callable[[], bool] = lambda: False, budget: float = 60.0, log_root=None, **kw) -> dict[str, str]:
    """Load task 7: the short syncs that can run now. Returns {game: "synced N new" | "skipped" | error code}. A failure is only logged."""
    out: dict[str, str] = {}
    deadline = time.time() + budget
    for game_key in GAMES:
        if cancelled() or time.time() > deadline:
            break
        if not can_auto_sync(tracker, game_key, log_root):
            out[game_key] = "skipped"
            continue
        stop = threading.Event()
        events: list[dict] = []
        timer = threading.Timer(max(1.0, deadline - time.time()), stop.set)
        timer.daemon = True
        timer.start()
        watcher = threading.Thread(target=_stop_when(stop, cancelled), name="pt-autosync-watch", daemon=True)
        watcher.start()
        try:
            res = Syncer(tracker, game_key, events.append, stop, log_root=log_root, **kw).run()
        finally:
            timer.cancel()
            stop.set()          # ends the watcher
        err = next((e for e in events if e.get("type") == "error"), None)
        out[game_key] = f"synced {res['added']['total']} new" if res else (err["code"] if err else "stopped")
        log.info("Sync at start, %s: %s", game_key, out[game_key])
    return out


# ---- the first-run check -----------------------------------------------------------------------------------------------------------

def first_run(tracker: Tracker, log_root=None, clock: Callable[[], float] = time.time) -> dict:
    """What the Sync screen shows before the first sync, per game: {game, folder: {state: found|missing, path}, link: {state: found|missing,
    ageHours}}. The age is the age of the newest cache file, which the game writes when the history page is opened."""
    settings = tracker.settings()
    games = []
    for key, game in GAMES.items():
        chosen = settings["folders"].get(key, "")
        folder = locate.game_folder(key, chosen, log_root)
        files = locate.cache_files(key, chosen, log_root)
        link: dict = {"state": "missing", "ageHours": None}
        for f in files[:3]:
            try:
                found = bool(locate.candidate_links(locate.read_cache(f), game))
            except OSError:
                continue
            age = _age(f, clock)
            if found and age is not None:
                link = {"state": "found", "ageHours": round(max(0.0, age) / 3600, 2)}
                break
        if folder is None and files:
            folder = files[0].parent
        games.append({"game": key, "folder": {"state": "found" if folder is not None else "missing", "path": str(folder) if folder is not None else ""}, "link": link})
    return {"games": games}
