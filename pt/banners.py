"""Banner and asset engine of the load tasks. It uses banner_tracker_test.py (the fetcher) for the network work and keeps its results in
the data folder, which mirrors the interface folder:

    data/js/banners.js                the banner data the start screen reads (window.PT_BANNERS), written by the fetcher's writer
    data/assets/art/<game>/<file>     the art and icons that data points to

The interface folder (inside the PyInstaller bundle in the .exe) is read only; its own js/banners.js and assets/art are the first-run
snapshot that is shown when the data folder has nothing yet. When data/js/banners.js exists, the main page is opened with #data=<data folder>
and reads banners and art from there (js/banners-base.js).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from . import paths

log = logging.getLogger("pt.banners")

RETRY_FAILED_AFTER = 6 * 3600       # seconds: art that could not be downloaded is tried again at a start this long after the last try
CALENDAR_PAUSE = 0.5                # seconds between the calendar requests (the API allows 2 per second)


def fetcher():
    """The banner fetcher module (imported when first needed, so tools that do not fetch never load it)."""
    import banner_tracker_test
    return banner_tracker_test


def stored_path() -> Path:
    return paths.data_dir() / "js" / "banners.js"


def _well_formed(data) -> bool:
    """The shape every reader of the stored data relies on: {"slides": {ui: [slide, ...]}, "games": {ui: {...}}}, each slide a dict (its
    rate-up list, when there is one, a list of dicts). A file that is valid JSON but not of this shape (edited by hand, or written by
    another version) would otherwise raise in the load tasks at every start and never be replaced."""
    if not isinstance(data, dict) or not isinstance(data.get("slides"), dict):
        return False
    if data.get("games") is not None and not isinstance(data["games"], dict):     # store_game writes into both
        return False
    for slides in data["slides"].values():
        if not isinstance(slides, list):
            return False
        for s in slides:
            if not isinstance(s, dict):
                return False
            rate_up = s.get("rateUp")
            if rate_up is not None and not (isinstance(rate_up, list) and all(isinstance(r, dict) for r in rate_up)):
                return False
    return True


def load_stored() -> dict | None:
    """The stored banner data of the data folder, or None when there is none, it cannot be read or it is not of the expected shape
    (then it counts as "nothing stored": the load tasks write it again, and the page uses the bundled snapshot)."""
    p = stored_path()
    data = fetcher().load_previous(str(p)) if p.is_file() else None
    if data is not None and not _well_formed(data):
        log.warning("%s is not of the expected shape; it is ignored and will be replaced", p)
        return None
    return data


def data_param() -> str:
    """Address parameter that makes the main page read banners and art from the data folder; empty when there is nothing stored."""
    if load_stored() is None:
        return ""
    return "&data=" + quote(paths.data_dir().as_uri() + "/", safe="")


# ---- task 1: the active banners ---------------------------------------------------------------------------------------------

def check_active(now: int | None = None, base: str | None = None, lang: str = "en-us", pause: float = CALENDAR_PAUSE):
    """Current banners of each game from the calendar. Returns (active, errors), both keyed by the game's UI key (genshin, hsr, zzz):
    active[ui] is the list of current banners, or None when the calendar could not be read (then errors[ui] says why).
    After a network error the other games are not tried (the computer is offline)."""
    f = fetcher()
    now = int(time.time()) if now is None else now
    base = base or f.API_BASE
    active, errors, offline = {}, {}, False
    for n, g in enumerate(f.GAMES):
        ui = g["ui"]
        if offline:
            active[ui], errors[ui] = None, "no network"
            continue
        if n:
            time.sleep(pause)
        try:
            active[ui] = f.fetch_current_banners(g["key"], base, lang, now)
            log.info("%s: %d current banner(s)", ui, len(active[ui]))
        except f.FetchError as e:
            active[ui], errors[ui] = None, str(e)
            offline = str(e).startswith("network error")
            log.warning("%s: calendar not available: %s", ui, e)
    return active, errors


def test_source(base: str | None = None) -> dict:
    """Ask a calendar server (the default one, or the address given) for the current banners of the first game. {ok, detail} or {ok: False, error}."""
    f = fetcher()
    try:
        items = f.fetch_current_banners(f.GAMES[0]["key"], (base or f.API_BASE).rstrip("/"), "en-us", int(time.time()))
        return {"ok": True, "detail": f"{len(items)} banner(s) for {f.GAMES[0]['title'].title()}"}
    except f.FetchError as e:
        return {"ok": False, "error": str(e)}


# ---- task 3: is the stored data up to date? ---------------------------------------------------------------------------------

def signature_active(current: list[dict]) -> set:
    """What the start screen shows for these banners: one entry per character or weapon banner, with its window and featured items."""
    f = fetcher()
    sig = set()
    for b in current:
        if b["kind"] == "both":         # no slide is made for these
            continue
        top, _ = f.split_featured(b["chars"] if b["kind"] == "character" else b["other"])
        sig.add((b["name"], f.iso_utc(b["start"]), f.iso_utc(b["end"]), tuple(sorted(i["id"] or i["name"] for i in top))))
    return sig


def signature_stored(slides: list[dict]) -> set:
    groups: dict[tuple, list] = {}
    for s in slides:
        groups.setdefault((s.get("bannerName"), s.get("start"), s.get("end")), []).append(s.get("itemId") or s.get("name"))
    return {(name, start, end, tuple(sorted(ids))) for (name, start, end), ids in groups.items()}


def asset_problems(slides: list[dict], root: Path) -> tuple[list[str], int]:
    """(files the data names but are missing on disk, number of images that were never downloaded)."""
    missing, never = [], 0

    def check(rel, remote):
        nonlocal never
        if rel:
            if not (root / rel).is_file():
                missing.append(rel)
        elif remote:
            never += 1

    for s in slides:
        check(s.get("art"), s.get("artRemote"))
        check(s.get("icon"), s.get("iconRemote"))
        for r in s.get("rateUp") or []:
            check(r.get("icon"), r.get("iconRemote"))
    return missing, never


def _age(stored: dict | None, now: float) -> float:
    try:
        return now - datetime.fromisoformat(stored["generatedAt"].replace("Z", "+00:00")).timestamp()
    except (TypeError, KeyError, ValueError, AttributeError):
        return float("inf")


def compare(stored: dict | None, active: dict, errors: dict, now: float | None = None, root: Path | None = None):
    """Per game: is the stored data out of date? Returns (outdated, why), both keyed by UI key.
    A game whose calendar could not be read is not out of date: what is stored (or the bundled snapshot) stays in use."""
    now = time.time() if now is None else now
    root = root or paths.data_dir()
    outdated, why = {}, {}
    for g in fetcher().GAMES:
        ui = g["ui"]
        current = active.get(ui)
        slides = ((stored or {}).get("slides") or {}).get(ui)
        if current is None:
            outdated[ui], why[ui] = False, f"calendar not available ({errors.get(ui, 'unknown')}); the stored data stays in use"
        elif slides is None:
            outdated[ui], why[ui] = True, "nothing stored for this game"
        elif signature_active(current) != signature_stored(slides):
            outdated[ui], why[ui] = True, "the active banners changed"
        else:
            missing, never = asset_problems(slides, root)
            if missing:
                outdated[ui], why[ui] = True, f"{len(missing)} stored file(s) are missing"
            elif never and _age(stored, now) > RETRY_FAILED_AFTER:
                outdated[ui], why[ui] = True, f"{never} image(s) were not downloaded last time; trying again"
            else:
                outdated[ui], why[ui] = False, "up to date"
    return outdated, why


# ---- tasks 5 to 7: download the assets of a game ----------------------------------------------------------------------------

def fetch_manifest():
    """The nanoka manifest (character lists and descriptions come from it). Returns (manifest or None, error text)."""
    f = fetcher()
    try:
        manifest = f.get_json(f"{f.NANOKA}/manifest.json")
        if not isinstance(manifest, dict):
            raise f.FetchError("manifest is not a JSON object")
        return manifest, ""
    except f.FetchError as e:
        log.warning("nanoka manifest not available: %s", e)
        return None, f"manifest: {e}"


def download_game(ui: str, current: list[dict], manifest, manifest_err: str, progress=lambda fraction: None):
    """Steps 2 and 3 of the fetcher for one game: its characters and weapons, their art, icons and descriptions, saved under the data
    folder. Returns (slides, notes, problems)."""
    f = fetcher()
    g = next(x for x in f.GAMES if x["ui"] == ui)
    store = f.AssetStore(str(paths.data_dir()))
    problems: list[str] = []
    char_list, version, list_err = None, "", manifest_err
    if manifest is not None:
        try:
            version, char_list = f.nanoka_character_list(g["nanoka"], manifest)
            list_err = ""
        except f.FetchError as e:
            list_err = str(e)
    if list_err:
        problems.append(f"nanoka character list: {list_err}")
    f.label_items(g["key"], current, char_list)
    wlist = None
    if g["key"] == "genshin" and manifest is not None and not list_err:
        try:
            wlist = f.nanoka_list(g["nanoka"], manifest, "weapon")[1]
        except f.FetchError as e:
            problems.append(f"nanoka weapon list: {e}")

    chars, weapons = f.build_catalog(current)
    total, done = max(1, len(chars) + len(weapons)), 0
    char_res, weapon_res = {}, {}
    for c in chars:
        res = f.fetch_character(g, c, char_list, version, store)
        char_res[c["id"] or c["name"]] = res
        problems += [f"{c['name']}: {p}" for p in res["problems"]]
        done += 1
        progress(done / total)
    for w in weapons:
        res = f.fetch_weapon(g, w, store, wlist, version)
        weapon_res[w["id"] or w["name"]] = res
        problems += [f"{w['name']}: {p}" for p in res["problems"]]
        done += 1
        progress(done / total)
    slides, notes = f.build_slides(g, current, char_res, weapon_res)
    return slides, notes, problems


def store_game(stored: dict | None, ui: str, slides: list[dict], info: dict, now: float | None = None, base: str | None = None) -> dict:
    """Put one game's slides into the stored data (the other games keep theirs) and write data/js/banners.js. Returns the new data."""
    f = fetcher()
    payload = stored or {"generatedAt": None, "source": base or f.API_BASE, "slides": {}, "games": {}}
    payload.setdefault("slides", {})[ui] = slides
    payload.setdefault("games", {})[ui] = info
    payload["generatedAt"] = f.iso_utc(int(time.time() if now is None else now))
    payload["source"] = base or payload.get("source") or f.API_BASE
    f.write_banners_js(str(paths.data_dir()), payload)
    return payload
