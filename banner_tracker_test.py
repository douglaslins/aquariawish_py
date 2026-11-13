#!/usr/bin/env python3
"""Banner fetcher, version 4.

For each game (Genshin Impact, Honkai: Star Rail, Zenless Zone Zero) it runs these steps in order:

  STEP 1  Identify the current banners.
          Source: the hoyoverse-api calendar. A banner is current when start_time <= now < end_time.
  STEP 2  Identify the characters and weapons in those banners.
          The top-rarity items of a banner are its featured items, the others are its rate-up items.
          "Weapons" means weapons, light cones and W-Engines.
  STEP 3  Fetch the art and the description of every character found in step 2.
          Description: the nanoka character list (fallback: the nanoka character detail file).
          Art: nanoka assets with Enka as second source; Zenless Zone Zero tries the official portrait first.
          The icons of the weapons are fetched too, and the full-size art of the featured weapons
          (Genshin gacha art, Star Rail light cone figure; the calendar icon is a small square crop).

Outputs
  banner_tracker_test.html   a report of the three steps. Images are embedded, so the file can be moved or sent.
  <ui>/js/banners.js         the banner data the start screen reads (window.PT_BANNERS)
  <ui>/assets/art/<game>/    the image files that data points to
<ui> is --ui-dir. Default: the folder "pulls_tracker_ui" next to this script if it exists, else "ui_export".

Nothing about specific banners or characters is hardcoded. Everything is read at run time.
Only the Python standard library is used (Python 3.9 or newer).

Usage:  python banner_tracker_test.py [--ui-dir DIR | --no-export] [--base-url URL] [--lang en-us]
                                      [--out FILE] [--embed-all-art]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import http.client
import json
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_BASE = "https://api.ennead.cc"          # hoyoverse-api public instance (override: --base-url)
NANOKA = "https://static.nanoka.cc"         # static game data and assets (successor of hakush.in)
ENKA = "https://enka.network/ui"            # second image host
YATTA = "https://gi.yatta.moe/assets/UI"       # third image host for Genshin weapon art
GENSHIN_DB = "https://raw.githubusercontent.com/theBowja/genshin-db/main/src/data/English/characters"   # character titles
USER_AGENT = "banner-tracker-test/0.4"
MAX_JSON_BYTES = 30 * 1024 * 1024
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MIN_IMAGE_BYTES = 1024
MIN_DESC_CHARS = 15                          # shorter texts are not taken as a character description

GAMES = [
    {"key": "genshin", "title": "GENSHIN IMPACT", "nanoka": "gi", "ui": "genshin", "has_title": True},
    {"key": "starrail", "title": "HONKAI STAR RAIL", "nanoka": "hsr", "ui": "hsr"},
    {"key": "zenless", "title": "ZENLESS ZONE ZERO", "nanoka": "zzz", "ui": "zzz"},
]
# calendar field names holding the two item lists of a banner, per game
ITEM_FIELDS = {
    "genshin": ("characters", "weapons"),
    "starrail": ("characters", "light_cones"),
    "zenless": ("agents", "w_engines"),
}
ITEM_NAMES = {
    "genshin": ("Characters", "Weapons"),
    "starrail": ("Characters", "Light Cones"),
    "zenless": ("Agents", "W-Engines"),
}
# The Star Rail calendar sends element and path as numbers. Known values are mapped here;
# unknown ones are learned at run time from nanoka's character list, else shown as raw numbers.
HSR_ELEMENTS = {"1": "Physical", "2": "Fire", "4": "Ice", "8": "Lightning", "16": "Wind",
                "32": "Quantum", "64": "Imaginary"}
HSR_PATHS = {"1": "Destruction", "2": "Hunt", "3": "Erudition", "4": "Harmony", "5": "Nihility",
             "6": "Preservation", "7": "Abundance", "8": "Remembrance"}
# (character banner, weapon banner, banner with both)
BANNER_LABELS = {
    "genshin": ("Character Event Wish", "Weapon Event Wish", "Chronicled Wish"),
    "starrail": ("Character Warp", "Light Cone Warp", "Warp"),
    "zenless": ("Exclusive Channel", "W-Engine Channel", "Channel"),
}
# pity group of a banner, as the start screen names it: (character banner, weapon banner)
PITY_GROUPS = {
    "genshin": ("character", "weapon"),
    "starrail": ("character", "lightcone"),
    "zenless": ("exclusive", "wengine"),
}
SLUG_PREFIX = {"genshin": "gi", "starrail": "hsr", "zenless": "zzz"}
# Star Rail names its light cone event warp "Brilliant Fixation" in every version (wiki, v1.x to 3.5). The calendar sends no name,
# so this constant is used for light cone banners and a name from the calendar wins if one is ever sent.
HSR_LC_BANNER_TITLE = "Brilliant Fixation"

# Zenless Zone Zero character art: variants tried for every agent. The first variant that downloads is used, in the order of
# --zzz-art (default prydwen,painting,select,mindscape1). --zzz-art-probe downloads all of them.
#   prydwen     Prydwen's full-body cutout, <name>_full.webp (a fan site's CDN, not an official source; the file name is guessed from the
#               agent's name, see PRYDWEN_STEMS for fixing a name that does not match)
#   painting    HoYoLAB's role_vertical_painting: the calendar icon URL with its folder swapped (a banner-shaped crop, shows the bust)
#   select      nanoka IconRoleSelect<n> (a small select icon)
#   mindscape1-3  nanoka Mindscape_<id>_<n> (cinema art with its own background)
ZZZ_ART_VARIANTS = ("prydwen", "painting", "select", "mindscape1", "mindscape2", "mindscape3")
ZZZ_ART_ORDER = ["prydwen", "painting", "select", "mindscape1"]
ZZZ_ART_PROBE = False
PRYDWEN = "https://cdn.prydwen.gg/images/zenless-zone-zero/characters"
# agent name (as the calendar gives it) -> Prydwen file stem, for names the guesses below do not hit: {"Zhu Yuan": "zhuyuan"}
PRYDWEN_STEMS: dict[str, str] = {}

LOG: list[str] = []                         # one line per HTTP request, shown at the end of the report
IMG_CACHE: dict[str, dict] = {}             # url -> download result, so shared icons download once
DETAIL_CACHE: dict[str, object] = {}        # nanoka character detail files (None = not available)


class FetchError(Exception):
    pass


# ----------------------------------------------------------------------------- HTTP

def _log(url, status, size, started, note=""):
    LOG.append(f"{status}\t{size} B\t{time.time() - started:.1f}s\t{url}" + (f"\t{note}" if note else ""))


def _check_address(url: str) -> None:
    """Only https addresses are fetched: an answer of the calendar, or the address typed in Settings > Data sources, could otherwise name
    a file:, ftp: or data: address. Plain http is accepted for this computer only (a test server)."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        raise FetchError("invalid address: " + url[:80]) from None
    if parts.scheme != "https" and not (parts.scheme == "http" and host in ("localhost", "127.0.0.1", "::1")):
        raise FetchError("invalid address (it must start with https://): " + url[:80])


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is followed only when its target passes _check_address too: the check of the first address alone would let a server
    send the request on to an http: (plain text) or ftp: address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_address(newurl)      # raises FetchError: http_get passes it on as it is
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_CheckedRedirect)


def http_get(url: str, limit: int, timeout: int = 20) -> tuple[bytes, str]:
    """GET with the retry rules from the usage plan: 429 -> wait 30 s once; 5xx and
    network errors -> up to 2 retries after 1 s and 3 s; other 4xx -> no retry."""
    _check_address(url)
    delays = [1, 3]
    attempt = 0
    waited_429 = False
    while True:
        started = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        try:
            with _OPENER.open(req, timeout=timeout) as resp:
                body = resp.read(limit + 1)
                ctype = resp.headers.get("Content-Type", "")
                status = resp.status
            if len(body) > limit:
                raise FetchError(f"response larger than {limit} bytes")
            _log(url, status, len(body), started)
            return body, ctype
        except FetchError:
            raise
        except urllib.error.HTTPError as e:
            _log(url, e.code, 0, started)
            if e.code == 429 and not waited_429:
                waited_429 = True
                time.sleep(30)
                continue
            if e.code >= 500 and attempt < len(delays):
                time.sleep(delays[attempt])
                attempt += 1
                continue
            raise FetchError(f"HTTP {e.code}")
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
            _log(url, "ERR", 0, started, str(e))
            if attempt < len(delays):
                time.sleep(delays[attempt])
                attempt += 1
                continue
            raise FetchError(f"network error: {getattr(e, 'reason', e)}")


def get_json(url: str):
    body, _ = http_get(url, MAX_JSON_BYTES)
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        raise FetchError("response is not valid JSON")


MIME_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}


def sniff_image(data: bytes):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def _download_image(url: str) -> dict:
    try:
        body, ctype = http_get(url, MAX_IMAGE_BYTES)
    except FetchError as e:
        return {"ok": False, "reason": str(e)}
    if len(body) < MIN_IMAGE_BYTES:
        return {"ok": False, "reason": f"too small ({len(body)} bytes)"}
    mime = sniff_image(body)
    if mime is None:
        return {"ok": False, "reason": f"not an image (content-type '{ctype}')"}
    return {"ok": True, "data": body, "mime": mime, "size": len(body)}


def fetch_first_image(candidates: list[str]) -> dict:
    """Try each URL in order; the first valid image wins."""
    tried = []
    for url in candidates:
        if url not in IMG_CACHE:
            IMG_CACHE[url] = _download_image(url)
        res = IMG_CACHE[url]
        if res["ok"]:
            return {"ok": True, "data": res["data"], "mime": res["mime"], "url": url, "size": res["size"], "tried": tried}
        tried.append((url, res["reason"]))
    return {"ok": False, "tried": tried}


def image_dims(data: bytes):
    """(width, height) read from the file header of a PNG, WebP or JPEG, or None."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", data[16:24])
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            kind = data[12:16]
            if kind == b"VP8X":
                return (int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1)
            if kind == b"VP8L":
                b = int.from_bytes(data[21:25], "little")
                return ((b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1)
            if kind == b"VP8 ":
                return (struct.unpack("<H", data[26:28])[0] & 0x3FFF, struct.unpack("<H", data[28:30])[0] & 0x3FFF)
        if data[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                m = data[i + 1]
                if m in (0xC0, 0xC1, 0xC2):
                    return (struct.unpack(">H", data[i + 7:i + 9])[0], struct.unpack(">H", data[i + 5:i + 7])[0])
                i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    except (struct.error, IndexError):
        pass
    return None


def data_uri(res: dict) -> str:
    return f"data:{res['mime']};base64," + base64.b64encode(res["data"]).decode("ascii")


# ----------------------------------------------------------------------------- formatting helpers

def esc(s) -> str:
    return html.escape(str(s), quote=True)


def to_seconds(v) -> int:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return 0
    return n // 1000 if n > 10 ** 11 else n


def fmt_ts(ts: int) -> str:
    if ts <= 0:
        return "not announced"
    utc = datetime.fromtimestamp(ts, tz=timezone.utc)
    loc = utc.astimezone()
    off = loc.strftime("%z")
    off = f"{off[:3]}:{off[3:]}" if off else ""
    return f"{loc:%Y-%m-%d %H:%M} local (UTC{off}) = {utc:%Y-%m-%d %H:%M} UTC"


def iso_utc(ts: int):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts > 0 else None


def fmt_delta(sec: int) -> str:
    sec = max(sec, 0)
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    return f"{d}d {h}h {rem // 60}m"


def rarity_rank(v) -> int:
    s = str(v).strip().upper()
    if s in ("S", "SS"):
        return 5
    if s == "A":
        return 4
    if s == "B":
        return 3
    try:
        return int(float(s))
    except ValueError:
        return 0


def rarity_text(game: str, item: dict) -> str:
    if game == "zenless":
        raw = str(item["rarity_raw"]).strip().upper()
        return f"{raw}-Rank" if raw in ("S", "A", "B") else f"rarity {raw}"
    return f"{item['rank']}★"


def clean_text(s: str) -> str:
    """Plain text from a game description: line breaks and paragraph ends become spaces, tags and
    entities are removed. The text is kept in full."""
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|\\n|[\r\n]+", " ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "item"


MAX_FILE_STEM = 100
_WINDOWS_DEVICES = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}


def file_safe(s: str) -> str:
    """A file name part made of remote text: only A-Z a-z 0-9 _ . -, at most MAX_FILE_STEM characters (a longer name is cut and ends in
    a short hash of the whole text, so two long names stay two files), and never a Windows device name such as CON or NUL."""
    text = str(s)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", text)
    if len(safe) > MAX_FILE_STEM:
        safe = safe[:MAX_FILE_STEM - 9] + "-" + hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:8]
    if safe.split(".")[0].upper() in _WINDOWS_DEVICES:
        safe = "_" + safe
    return safe


_LONE_SURROGATE = re.compile("[\\ud800-\\udfff]")


def scrub(obj):
    """The same data with every lone surrogate in a text replaced by U+FFFD (remote text can hold one; it cannot be written as UTF-8)."""
    if isinstance(obj, str):
        return _LONE_SURROGATE.sub("�", obj)
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    if isinstance(obj, dict):
        return {scrub(k): scrub(v) for k, v in obj.items()}
    return obj


# ----------------------------------------------------------------------------- STEP 1: current banners

def norm_item(raw: dict) -> dict:
    name = raw.get("name") or raw.get("full_name") or ""
    rid = str(raw.get("id", ""))
    extras = [str(raw[k]) for k in ("element", "path", "profession")
              if raw.get(k) and not str(raw[k]).isdigit()]   # numeric codes are labelled later (HSR)
    return {
        "id": rid,
        "name": name or f"Unannounced (id {rid})",
        "icon": raw.get("icon") or "",
        "rank": rarity_rank(raw.get("rarity")),
        "rarity_raw": raw.get("rarity", ""),
        "extras": extras,
        "raw_element": str(raw.get("element") or ""),
        "raw_path": str(raw.get("path") or ""),
        "raw_profession": str(raw.get("profession") or ""),
        "element": "",
        "role": "",
    }


def split_featured(items: list[dict]):
    """Top-rarity items are the featured ones; the rest are rate-up items."""
    if not items:
        return [], []
    top = max(i["rank"] for i in items)
    return [i for i in items if i["rank"] == top], [i for i in items if i["rank"] != top]


def norm_banner(game: str, raw: dict) -> dict:
    f_chars, f_other = ITEM_FIELDS[game]
    chars = [norm_item(x) for x in (raw.get(f_chars) or []) if isinstance(x, dict)]
    other = [norm_item(x) for x in (raw.get(f_other) or []) if isinstance(x, dict)]
    kind = "both" if chars and other else "character" if chars else "other"
    labels = BANNER_LABELS[game]
    generated = labels[2] if kind == "both" else labels[0] if kind == "character" else labels[1]
    name = raw.get("name")
    if not name:  # Star Rail sends empty names: make them distinguishable by the featured 5*
        top = split_featured(chars or other)[0]
        name = generated + (": " + ", ".join(i["name"] for i in top) if top else "")
    return {
        "name": name,
        "label": generated,
        "has_own_name": bool(raw.get("name")),
        "version": str(raw.get("version") or "?"),
        "state": str(raw.get("state") or ""),
        "start": to_seconds(raw.get("start_time")),
        "end": to_seconds(raw.get("end_time")),
        "kind": kind,
        "chars": chars,
        "other": other,
    }


def is_current(b: dict, now: int) -> bool:
    state = b["state"].upper()
    if "NOT_START" in state or "COMPLETED" in state:
        return False
    return 0 < b["start"] <= now and (b["end"] <= 0 or now < b["end"])


def fetch_current_banners(game: str, base: str, lang: str, now: int) -> list[dict]:
    data = get_json(f"{base.rstrip('/')}/mihoyo/{game}/calendar?lang={lang}")
    if not isinstance(data, dict) or not isinstance(data.get("banners"), list):
        detail = ""
        if isinstance(data, dict) and ("error" in data or "error_code" in data):
            detail = f" (error {data.get('error_code')}: {data.get('error')})"
        raise FetchError("calendar response has no 'banners' list" + detail)
    banners = [norm_banner(game, b) for b in data["banners"] if isinstance(b, dict)]
    current = [b for b in banners if is_current(b, now)]
    current.sort(key=lambda b: (b["kind"] != "character", b["start"], b["name"]))
    return current


# ----------------------------------------------------------------------------- STEP 2: characters and weapons

def nanoka_list(code: str, manifest, kind: str = "character"):
    """Returns (live_version, {id: entry}) of a nanoka list ('character' or 'weapon'). Raises FetchError."""
    try:
        version = str(manifest[code]["live"])
    except (TypeError, KeyError):
        raise FetchError(f"manifest has no live version for '{code}'")
    data = get_json(f"{NANOKA}/{code}/{version}/{kind}.json")
    if not isinstance(data, dict):
        raise FetchError(f"{kind} list is not a JSON object")
    return version, data


def nanoka_character_list(code: str, manifest):
    return nanoka_list(code, manifest, "character")


def entry_names(entry: dict) -> list[str]:
    out = []
    for key in ("en", "EN", "name", "Name"):
        v = entry.get(key)
        if isinstance(v, dict):
            v = v.get("name") or v.get("Name")
        if isinstance(v, str) and v.strip():
            out.append(v.strip().lower())
    return out


def find_entry(char_list, item: dict):
    """The nanoka list entry of a calendar item: by id first, then by English name."""
    if not char_list:
        return None
    e = char_list.get(item["id"])
    if isinstance(e, dict):
        return e
    want = item["name"].strip().lower()
    for e in char_list.values():
        if isinstance(e, dict) and want in entry_names(e):
            return e
    return None


def label_items(game: str, current, char_list):
    """Fill item['element'] and item['role'] on every item of every current banner.
    Star Rail: replace the numeric element and path codes by names."""
    learned_e, learned_p = {}, {}
    if game == "starrail":
        for b in current:
            for i in b["chars"]:
                entry = char_list.get(i["id"]) if char_list else None
                if isinstance(entry, dict):
                    for raw, val, learned in ((i["raw_element"], entry.get("damageType"), learned_e),
                                              (i["raw_path"], entry.get("baseType"), learned_p)):
                        if raw.isdigit() and isinstance(val, str) and val and not val.isdigit():
                            learned.setdefault(raw, val)

    def label(raw, static, learned, what):
        if not raw:
            return ""
        if not raw.isdigit():
            return raw
        return static.get(raw) or learned.get(raw) or f"{what} {raw}"

    for b in current:
        for i in b["chars"] + b["other"]:
            if game == "starrail":
                i["element"] = label(i["raw_element"], HSR_ELEMENTS, learned_e, "element")
                i["role"] = label(i["raw_path"], HSR_PATHS, learned_p, "path")
                i["extras"] = [x for x in (i["element"], i["role"]) if x]
            else:
                i["element"] = i["raw_element"]
                i["role"] = i["raw_profession"] or i["raw_path"]


def build_catalog(current: list[dict]):
    """STEP 2. The unique characters and the unique weapons of the current banners, in banner order.
    Each entry: the calendar item plus 'kind', 'featured' (top rarity in at least one banner) and 'banners'."""
    chars, weapons = {}, {}
    for b in current:
        for items, store, kind in ((b["chars"], chars, "character"), (b["other"], weapons, "weapon")):
            top, rest = split_featured(items)
            for it, featured in [(i, True) for i in top] + [(i, False) for i in rest]:
                key = it["id"] or it["name"]
                e = store.get(key)
                if e is None:
                    e = store[key] = {**it, "kind": kind, "featured": False, "banners": []}
                e["featured"] = e["featured"] or featured
                e["banners"].append({"name": b["name"], "version": b["version"], "end": b["end"], "featured": featured})
    return list(chars.values()), list(weapons.values())


# ----------------------------------------------------------------------------- STEP 3: art and description

def find_desc(obj):
    """Description text inside a nanoka detail file (shape not guaranteed: top level or one known sub-object)."""
    if not isinstance(obj, dict):
        return None
    for k in ("desc", "Desc", "description", "Description"):
        v = obj.get(k)
        if isinstance(v, str) and len(clean_text(v)) >= MIN_DESC_CHARS:
            return clean_text(v)
    for k, v in obj.items():
        if isinstance(v, dict) and k.lower() in ("charainfo", "info", "profile", "character", "base"):
            r = find_desc(v)
            if r:
                return r
    return None


def nanoka_detail(code: str, version: str, item_id: str, kind: str = "character", lang_dir: str = "en"):
    """The nanoka detail file of one character or weapon, or None. Cached, so an item costs one request."""
    if not (version and item_id):
        return None
    url = f"{NANOKA}/{code}/{version}/{lang_dir}/{kind}/{item_id}.json"
    if url not in DETAIL_CACHE:
        try:
            DETAIL_CACHE[url] = get_json(url)
        except FetchError:
            DETAIL_CACHE[url] = None
    return DETAIL_CACHE[url]


def get_description(code: str, version: str, item: dict, entry):
    """Returns (text or None, source text). Full text, nothing cut off."""
    if entry and isinstance(entry.get("desc"), str):
        text = clean_text(entry["desc"])
        if len(text) >= MIN_DESC_CHARS:
            return text, "nanoka character list"
    text = find_desc(nanoka_detail(code, version, item["id"]))
    if text:
        return text, "nanoka character detail file"
    return None, ""


def find_title(obj):
    """The character title ("Void Star") in a nanoka detail file: key 'title' at the top or inside 'chara_info'."""
    if not isinstance(obj, dict):
        return None
    for holder in (obj, *[v for k, v in obj.items() if isinstance(v, dict) and k.lower().replace("_", "") == "charainfo"]):
        for k, v in holder.items():
            if k.lower() == "title" and isinstance(v, str) and clean_text(v):
                return clean_text(v)
    return None


def get_title(code: str, version: str, item: dict):
    """Genshin only: the in-game character title. Returns (text or None, source text).
    Source 1: the genshin-db data file of the character (verified: it has the field). Source 2: the nanoka detail file."""
    file_name = re.sub(r"[^a-z0-9]", "", item["name"].lower())
    if file_name:
        try:
            data = get_json(f"{GENSHIN_DB}/{file_name}.json")
            title = clean_text(data.get("title", "")) if isinstance(data, dict) else ""
            if title and str(data.get("id")) == item["id"]:
                return title, "genshin-db"
        except FetchError:
            pass
    text = find_title(nanoka_detail(code, version, item["id"]))
    if text:
        return text, "nanoka character detail file"
    return None, ""


def prydwen_stems(names) -> list[str]:
    """File stems to try on Prydwen for an agent: an entry of PRYDWEN_STEMS first, then the name as 'zhu-yuan', 'zhu_yuan',
    'zhuyuan' and its first word, for every name given (calendar name, then the nanoka list names). No duplicates."""
    out = []
    def add(x):
        if x and x not in out:
            out.append(x)
    for n in names:
        add(PRYDWEN_STEMS.get(n))
    for n in names:
        sl = slug(n) if n else ""
        if sl and sl != "item":
            add(sl)
            add(sl.replace("-", "_"))
            add(sl.replace("-", ""))
            add(sl.split("-")[0])
    return out


def zzz_names(item: dict, entry) -> list[str]:
    """The names of an agent, calendar name first, then English names from the nanoka list entry."""
    names = [item["name"]]
    if isinstance(entry, dict):
        for key in ("en", "EN", "name", "Name"):
            v = entry.get(key)
            if isinstance(v, dict):
                v = v.get("name") or v.get("Name")
            if isinstance(v, str) and v.strip() and v.strip() not in names:
                names.append(v.strip())
    return names


def zzz_art_urls(cal_icon: str, icon_name: str, cid: str, names=()) -> dict:
    """Every known art URL of one Zenless Zone Zero agent, by variant name (see ZZZ_ART_VARIANTS): a list per variant."""
    urls = {}
    stems = prydwen_stems(names)
    if stems:
        urls["prydwen"] = [f"{PRYDWEN}/{st}_full.webp" for st in stems]
    marker = "role_square_avatar/role_square_avatar_"
    if marker in cal_icon:   # HoYoLAB's role_vertical_painting: the calendar icon URL with the folder swapped
        urls["painting"] = [cal_icon.replace(marker, "role_vertical_painting/role_vertical_painting_")]
    if icon_name.startswith("IconRole"):
        urls["select"] = [f"{NANOKA}/assets/zzz/{icon_name.replace('Role', 'RoleSelect', 1)}.webp"]
    for n in (1, 2, 3):
        urls[f"mindscape{n}"] = [f"{NANOKA}/assets/zzz/Mindscape_{cid}_{n}.webp"]
    return urls


def character_image_candidates(game: str, item: dict, entry):
    """Returns (art candidates, icon candidates); the first URL that downloads is used."""
    cid = item["id"]
    icon_name = entry.get("icon") if entry and isinstance(entry.get("icon"), str) else ""
    cal_icon = item["icon"]
    art, icon = [], []
    if game == "genshin":
        if icon_name.startswith("UI_AvatarIcon_"):
            base_name = icon_name[len("UI_AvatarIcon_"):]
        else:   # character not in the list yet: the English name without spaces is the usual file name
            base_name = re.sub(r"[^A-Za-z]", "", item["name"])
        if base_name:
            art += [f"{NANOKA}/assets/gi/UI_Gacha_AvatarImg_{base_name}.webp", f"{ENKA}/UI_Gacha_AvatarImg_{base_name}.png"]
            icon += [f"{NANOKA}/assets/gi/UI_AvatarIcon_{base_name}.webp", f"{ENKA}/UI_AvatarIcon_{base_name}.png"]
    elif game == "starrail":
        art += [f"{NANOKA}/assets/hsr/avatardrawcard/{cid}.webp",
                f"{ENKA}/hsr/SpriteOutput/AvatarDrawCard/{cid}.png"]
        icon += [f"{NANOKA}/assets/hsr/avatarshopicon/{cid}.webp",
                 f"{ENKA}/hsr/SpriteOutput/AvatarRoundIcon/{cid}.png"]
    elif game == "zenless":
        urls = zzz_art_urls(cal_icon, icon_name, cid, zzz_names(item, entry))
        art += [u for v in ZZZ_ART_ORDER for u in urls.get(v, [])]
        if icon_name:
            icon += [f"{NANOKA}/assets/zzz/{icon_name}.webp", f"{ENKA}/zzz/{icon_name}.png"]
    if cal_icon:
        icon.insert(0, cal_icon)
    return art, icon


class AssetStore:
    """Saves downloaded images as files under <ui>/assets/art/<game>/. root=None: nothing is written."""

    def __init__(self, root):
        self.root = root

    def save(self, ui_game: str, stem: str, res: dict):
        """Returns the path relative to the UI folder (forward slashes), or None when nothing is written."""
        rel = f"assets/art/{ui_game}/{file_safe(stem)}.{MIME_EXT.get(res['mime'], 'img')}"
        if self.root is None:
            return None
        path = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"          # written whole, then renamed: a crash must not leave a cut image that later checks take as present
        try:
            with open(tmp, "wb") as f:
                f.write(res["data"])
            os.replace(tmp, path)
        except OSError:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        return rel


def fetch_character(g: dict, item: dict, char_list, version: str, store: AssetStore) -> dict:
    """STEP 3 for one character: description, art, icon."""
    game, code, ui = g["key"], g["nanoka"], g["ui"]
    entry = find_entry(char_list, item)
    desc, desc_src = get_description(code, version, item, entry)
    title, title_src = get_title(code, version, item) if g.get("has_title") and item["featured"] else (None, "")
    art_urls, icon_urls = character_image_candidates(game, item, entry)
    art = fetch_first_image(art_urls) if art_urls else {"ok": False, "tried": []}
    icon = fetch_first_image(icon_urls) if icon_urls else {"ok": False, "tried": []}
    key = file_safe(item["id"] or slug(item["name"]))
    variants = []
    if game == "zenless" and ZZZ_ART_PROBE:   # --zzz-art-probe: download every variant, save it next to the used art, report its size
        icon_name = entry.get("icon") if entry and isinstance(entry.get("icon"), str) else ""
        for v, url_list in zzz_art_urls(item["icon"], icon_name, item["id"], zzz_names(item, entry)).items():
            r = fetch_first_image(url_list)
            variants.append({"variant": v, "url": r["url"] if r["ok"] else url_list[0], "res": r, "file": store.save(ui, f"{key}-art-{v}", r) if r["ok"] else None,
                             "dims": image_dims(r["data"]) if r["ok"] else None})
        print("[zenless] art variants of " + item["name"] + ": " + "; ".join(
            (f"{x['variant']} " + (f"{x['dims'][0]}x{x['dims'][1]}" if x["dims"] else "size unknown") + f" ({x['res']['size'] // 1024} KB)")
            if x["res"]["ok"] else f"{x['variant']} missing ({x['res']['tried'][0][1]}" + (f", {len(x['res']['tried'])} names tried" if len(x["res"]["tried"]) > 1 else "") + ")"
            for x in variants), flush=True)
    out = {
        "variants": variants,
        "entry": entry, "desc": desc, "desc_source": desc_src, "title": title, "title_source": title_src, "art_urls": art_urls,
        "art": art, "art_file": store.save(ui, f"{key}-art", art) if art["ok"] else None,
        "icon": icon, "icon_file": store.save(ui, f"{key}-icon", icon) if icon["ok"] else None,
        "problems": [],
    }
    if g.get("has_title") and item["featured"] and not title:
        out["problems"].append("no title found")
    if not desc:
        out["problems"].append("no description found" + ("" if entry else " (character not in the nanoka list)"))
    if not art["ok"]:
        out["problems"].append("art not fetched: " + ("; ".join(f"{u} ({r})" for u, r in art["tried"]) or "no URL available"))
    if not icon["ok"]:
        out["problems"].append("icon not fetched: " + ("; ".join(f"{u} ({r})" for u, r in icon["tried"]) or "no URL available"))
    return out


def weapon_art_candidates(g: dict, item: dict, wlist, version: str) -> list[str]:
    """URLs of the full-size art of a weapon, best first (the calendar icon is a small square crop).
    Genshin: the gacha art 'UI_Gacha_EquipIcon_<name>' (tall, uncropped); <name> comes from the nanoka weapon list
    (icon 'UI_EquipIcon_<name>'), else from the weapon detail file.
    Star Rail: nanoka 'lightconemaxfigures/<id>'. Zenless Zone Zero: no full-size source known."""
    game = g["key"]
    if game == "starrail" and item["id"]:
        return [f"{NANOKA}/assets/hsr/lightconemaxfigures/{item['id']}.webp"]
    if game == "genshin":
        entry = wlist.get(item["id"]) if wlist else None
        icon_name = entry.get("icon") if isinstance(entry, dict) else None
        if not isinstance(icon_name, str):
            detail = nanoka_detail(g["nanoka"], version, item["id"], "weapon")
            icon_name = detail.get("icon") if isinstance(detail, dict) else None
        if isinstance(icon_name, str) and icon_name.startswith("UI_EquipIcon_"):
            name = "UI_Gacha_EquipIcon_" + icon_name[len("UI_EquipIcon_"):]
            return [f"{NANOKA}/assets/gi/{name}.webp", f"{ENKA}/{name}.png", f"{YATTA}/{name}.png"]
    return []


def fetch_weapon(g: dict, item: dict, store: AssetStore, wlist=None, version: str = "") -> dict:
    """Weapons: the icon from the calendar, and for featured weapons the full-size art."""
    icon = fetch_first_image([item["icon"]]) if item["icon"] else {"ok": False, "tried": []}
    key = file_safe(item["id"] or slug(item["name"]))
    art_urls = weapon_art_candidates(g, item, wlist, version) if item["featured"] else []
    art = fetch_first_image(art_urls) if art_urls else {"ok": False, "tried": []}
    out = {"icon": icon, "icon_file": store.save(g["ui"], f"{key}-icon", icon) if icon["ok"] else None,
           "art": art, "art_file": store.save(g["ui"], f"{key}-art", art) if art["ok"] else None, "art_urls": art_urls,
           "problems": []}
    if not icon["ok"]:
        out["problems"].append("icon not fetched: " + ("; ".join(f"{u} ({r})" for u, r in icon["tried"]) or "no icon URL in the calendar"))
    if art_urls and not art["ok"]:
        out["problems"].append("full-size art not fetched (the icon is used): " + "; ".join(f"{u} ({r})" for u, r in art["tried"]))
    return out


# ----------------------------------------------------------------------------- data for the start screen

def build_slides(g: dict, current, char_res: dict, weapon_res: dict) -> tuple[list[dict], list[str]]:
    """One slide per featured top-rarity item of each character or weapon banner (banners with both kinds are skipped)."""
    game = g["key"]
    slides, notes = [], []
    seen = set()
    for b in current:
        if b["kind"] == "both":
            notes.append(f"banner '{b['name']}' has characters and weapons together; the start screen has no slide for it")
            continue
        is_char = b["kind"] == "character"
        items = b["chars"] if is_char else b["other"]
        top, rest = split_featured(items)
        rate_up = []
        for it in rest:
            res = (char_res if is_char else weapon_res).get(it["id"] or it["name"], {})
            rate_up.append({"name": it["name"], "rank": it["rank"], "element": it["element"] or None,
                            "icon": res.get("icon_file"), "iconRemote": (res.get("icon") or {}).get("url") or it["icon"] or None})
        for it in top:
            key = it["id"] or it["name"]
            res = (char_res if is_char else weapon_res).get(key, {})
            sid = f"{SLUG_PREFIX[game]}-{slug(it['name'])}"
            if (sid, b["name"]) in seen:
                continue
            seen.add((sid, b["name"]))
            icon_remote = (res.get("icon") or {}).get("url") or it["icon"] or None
            slide = {
                "id": sid,
                "group": PITY_GROUPS[game][0 if is_char else 1],
                "kind": "character" if is_char else "weapon",
                "banner": b["name"] if (game == "genshin" and b["has_own_name"]) else b["label"],
                "bannerName": b["name"],
                "bannerTitle": b["name"] if b["has_own_name"] else (HSR_LC_BANNER_TITLE if (game == "starrail" and not is_char) else None),
                "bannerTitleSource": "calendar" if b["has_own_name"] else ("constant" if (game == "starrail" and not is_char) else None),
                "version": b["version"],
                "start": iso_utc(b["start"]),
                "end": iso_utc(b["end"]),
                "itemId": it["id"],
                "name": it["name"],
                "rank": it["rank"],
                "icon": res.get("icon_file"),
                "iconRemote": icon_remote,
                "rateUp": rate_up,
            }
            if not is_char:
                art_res = res.get("art") or {}
                slide.update({"art": res.get("art_file"),
                              "artRemote": art_res.get("url") or (res.get("art_urls") or [None])[0]})
            if is_char:
                art_res = res.get("art") or {}
                slide.update({
                    "element": it["element"] or None,
                    "role": it["role"] or None,
                    "art": res.get("art_file"),
                    "artRemote": art_res.get("url") or (res.get("art_urls") or [None])[0],
                    "title": res.get("title"),
                    "titleSource": res.get("title_source") or None,
                    "desc": res.get("desc"),
                    "descSource": res.get("desc_source") or None,
                })
            slides.append(slide)
    if slides and any(sl.get("bannerTitleSource") == "constant" for sl in slides):
        notes.append(f'light cone banners use the constant bannerTitle "{HSR_LC_BANNER_TITLE}" (the calendar sends no banner names; taken from the wiki, not fetched)')
    if slides and not any(sl.get("bannerTitle") for sl in slides):
        notes.append("the calendar sends no banner names for this game, so slides have no bannerTitle (the start screen leaves that spot empty)")
    return slides, notes


def load_previous(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        m = re.search(r"window\.PT_BANNERS\s*=\s*(\{.*\})\s*;?\s*$", text, re.S)
        return json.loads(m.group(1)) if m else None
    except (OSError, ValueError):
        return None


def write_banners_js(ui_dir: str, payload: dict) -> str:
    path = os.path.join(ui_dir, "js", "banners.js")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = ("/* GENERATED by banner_tracker_test.py. Do not edit by hand: the next run replaces this file.\n"
            "   Slide = one featured 5-star item of a current banner. Times are UTC ISO strings.\n"
            "   art, icon: files under assets/art (null when the download failed); artRemote, iconRemote: the URL used. */\n"
            "window.PT_BANNERS = " + json.dumps(scrub(payload), indent=1, ensure_ascii=False) + ";\n")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)                  # no half-written file is left for the next run
        except OSError:
            pass
        raise
    return path


# ----------------------------------------------------------------------------- report (HTML)

def item_text(i: dict) -> str:
    return i["name"] + (f" ({', '.join(i['extras'])})" if i["extras"] else "")


def banner_text_html(game: str, b: dict, now: int) -> str:
    lines = [f"<b>{esc(b['name'])}</b> - version {esc(b['version'])}"
             + (f" - state {esc(b['state'])}" if b["state"] else "")]
    lines.append("Starts: " + esc(fmt_ts(b["start"])))
    ends = "Ends: " + esc(fmt_ts(b["end"]))
    if b["end"] > 0:
        ends += f" - in {fmt_delta(b['end'] - now)}"
    lines.append(ends)
    return "<p>" + "<br>\n".join(lines) + "</p>"


def banner_items_html(game: str, b: dict) -> str:
    lines = [f"<b>{esc(b['name'])}</b>"]
    for items, list_name in ((b["chars"], ITEM_NAMES[game][0]), (b["other"], ITEM_NAMES[game][1])):
        top, rest = split_featured(items)
        if top:
            lines.append(f"Featured {list_name} ({esc(rarity_text(game, top[0]))}): "
                         + esc("; ".join(item_text(i) + f" [id {i['id']}]" for i in top)))
        if rest:
            lines.append(f"Rate-up {list_name} ({esc(rarity_text(game, rest[0]))}): "
                         + esc("; ".join(item_text(i) + f" [id {i['id']}]" for i in rest)))
    return "<p>" + "<br>\n".join(lines) + "</p>\n"


def img_tag(res: dict, label: str, height: int, data_file=None) -> str:
    attr = f' data-file="{esc(data_file)}"' if data_file else ""
    return (f'<span style="display:inline-block;margin:4px;vertical-align:top">'
            f'<img src="{data_uri(res)}" height="{height}" alt="{esc(label)}"{attr}><br>'
            f'<small>{esc(label)}<br>{esc(res["url"])}</small></span>\n')


def image_block(res: dict, label: str, height: int, data_file, stats: dict, embed: bool = True) -> str:
    if res["ok"]:
        stats["ok"] += 1
        if embed:
            return img_tag(res, label, height, data_file)
        return (f"<p><small>{esc(label)}: fetched ({res['size'] // 1024} KB, {esc(res['mime'])}) from {esc(res['url'])}; "
                f"saved as {esc(data_file or 'nothing (no export)')}; not embedded in this report.</small></p>\n")
    stats["fail"] += 1
    tried = "; ".join(f"{u} ({r})" for u, r in res["tried"]) or "no URL available"
    return f"<p><small>[image not fetched] {esc(label)}. Tried: {esc(tried)}</small></p>\n"


def character_block(game: str, item: dict, res: dict, stats: dict, embed_art: bool) -> str:
    rows = [("Name", item["name"]), ("ID", item["id"]), ("Rarity", rarity_text(game, item))]
    if item["element"]:
        rows.append(("Element", item["element"]))
    if item["role"]:
        rows.append(("Path / role", item["role"]))
    rows.append(("In current banners", "; ".join(
        f"{b['name']} ({'featured' if b['featured'] else 'rate-up'}, ends {fmt_ts(b['end'])})" for b in item["banners"])))
    if item["featured"] and next(g for g in GAMES if g["key"] == game).get("has_title"):
        rows.append(("Title", res["title"] or "NOT FOUND"))
        if res["title"]:
            rows.append(("Title source", res["title_source"]))
    rows.append(("Description", res["desc"] if res["desc"] else "NOT FOUND"))
    if res["desc"]:
        rows.append(("Description source", res["desc_source"] + f" ({len(res['desc'])} characters, full text)"))
    rows.append(("Art file", res["art_file"] or ("not saved" if res["art"]["ok"] else "none (not fetched)")))
    rows.append(("Icon file", res["icon_file"] or ("not saved" if res["icon"]["ok"] else "none (not fetched)")))
    text = "<p>" + "<br>\n".join(f"<b>{esc(k)}:</b> {esc(v)}" for k, v in rows) + "</p>\n"
    if res.get("variants"):
        text += "<p><b>Art variants (--zzz-art-probe):</b> the start screen uses the first one that downloads, in the order of --zzz-art.</p>\n<div>\n"
        for x in res["variants"]:
            if x["res"]["ok"]:
                text += (f'<span style="display:inline-block;margin:4px;vertical-align:top"><img src="{data_uri(x["res"])}" height="300" '
                         f'alt="{esc(x["variant"])}" data-nat="1"><br><small>{esc(x["variant"])}: <span class="nat">'
                         f'{(str(x["dims"][0]) + " x " + str(x["dims"][1]) + " px") if x["dims"] else ""}</span>, {x["res"]["size"] // 1024} KB<br>'
                         f'{esc(x["url"])}<br>saved as {esc(x["file"] or "nothing (no export)")}</small></span>\n')
            else:
                text += (f'<p><small>{esc(x["variant"])}: not available. Tried: '
                         + esc("; ".join(f"{u} ({r})" for u, r in x["res"]["tried"])) + "</small></p>\n")
        text += "</div>\n"
    imgs = (image_block(res["art"], f"{item['name']} - art", 420, res["art_file"], stats, embed=embed_art)
            + image_block(res["icon"], f"{item['name']} - icon", 96, res["icon_file"], stats))
    return text + "<div>\n" + imgs + "</div>\n"


def weapon_block(game: str, item: dict, res: dict, stats: dict) -> str:
    label = f"{item['name']} ({rarity_text(game, item)}{', featured' if item['featured'] else ', rate-up'})"
    out = image_block(res["icon"], label + " - icon", 96, res["icon_file"], stats)
    if res["art"]["ok"]:
        out += image_block(res["art"], label + " - full-size art", 300, res["art_file"], stats)
    elif res["art_urls"]:
        out += image_block(res["art"], label + " - full-size art", 300, res["art_file"], stats)
    return out


# ----------------------------------------------------------------------------- one game

def process_game(g: dict, base: str, lang: str, now: int, manifest, manifest_err: str, store: AssetStore,
                 embed_all_art: bool):
    """Runs the three steps for one game. Returns (report html, stats, slides, notes, failed message)."""
    game, code = g["key"], g["nanoka"]
    stats = {"ok": 0, "fail": 0, "banners": 0, "characters": 0, "weapons": 0, "desc_missing": 0, "errors": []}
    out = [f"<h1>{esc(g['title'])}</h1>\n"]

    # ---- STEP 1
    print(f"[{game}] step 1: current banners ...", flush=True)
    out.append("<h2>Step 1: current banners</h2>\n")
    current, cal_err = [], ""
    try:
        current = fetch_current_banners(game, base, lang, now)
        stats["banners"] = len(current)
        print(f"[{game}] {len(current)} current banner(s)", flush=True)
    except FetchError as e:
        cal_err = str(e)
        stats["errors"].append(f"calendar: {cal_err}")
        print(f"[{game}] calendar FAILED: {cal_err}", flush=True)
    if cal_err:
        out.append(f"<p>Could not fetch the calendar: {esc(cal_err)}. Steps 2 and 3 need the calendar and were not run.</p>\n")
        return "".join(out), stats, [], [], cal_err
    if not current:
        out.append("<p>No banner is active right now according to the calendar (possible gap between versions).</p>\n")
        return "".join(out), stats, [], [], ""
    out.append("".join(banner_text_html(game, b, now) + "\n" for b in current))

    # nanoka character list (descriptions and art names); the calendar alone cannot supply them
    char_list, version, list_err = None, "", manifest_err
    if manifest is not None:
        try:
            version, char_list = nanoka_character_list(code, manifest)
            list_err = ""
        except FetchError as e:
            list_err = str(e)
    if list_err:
        stats["errors"].append(f"nanoka character list: {list_err}")
        print(f"[{game}] nanoka character list unavailable: {list_err}", flush=True)
    label_items(game, current, char_list)
    wlist = None                      # Genshin: the weapon list gives the art file names of the weapons
    if game == "genshin" and manifest is not None and not list_err:
        try:
            wlist = nanoka_list(code, manifest, "weapon")[1]
        except FetchError as e:
            stats["errors"].append(f"nanoka weapon list: {e}")

    # ---- STEP 2
    print(f"[{game}] step 2: characters and weapons ...", flush=True)
    chars, weapons = build_catalog(current)
    stats["characters"], stats["weapons"] = len(chars), len(weapons)
    out.append("<h2>Step 2: characters and weapons in these banners</h2>\n")
    out.append("".join(banner_items_html(game, b) for b in current))
    out.append(f"<p>Unique {esc(ITEM_NAMES[game][0].lower())}: {len(chars)} "
               f"({sum(1 for c in chars if c['featured'])} featured). "
               f"Unique {esc(ITEM_NAMES[game][1].lower())}: {len(weapons)} "
               f"({sum(1 for w in weapons if w['featured'])} featured).</p>\n")

    # ---- STEP 3
    print(f"[{game}] step 3: art and description of {len(chars)} character(s) ...", flush=True)
    out.append("<h2>Step 3: art and description of each character</h2>\n")
    if list_err:
        out.append(f"<p>The nanoka character list is not available ({esc(list_err)}). Descriptions can only come from the "
                   "detail files; art file names are guessed from the character names.</p>\n")
    char_res, weapon_res = {}, {}
    for c in chars:
        res = fetch_character(g, c, char_list, version, store)
        char_res[c["id"] or c["name"]] = res
        if not res["desc"]:
            stats["desc_missing"] += 1
        for p in res["problems"]:
            stats["errors"].append(f"{c['name']}: {p}")
        out.append(character_block(game, c, res, stats, embed_art=embed_all_art or c["featured"]))
        out.append("<hr>\n")
    out.append("<h3>Weapons: icons and full-size art</h3>\n<div>\n")
    for w in weapons:
        res = fetch_weapon(g, w, store, wlist, version)
        weapon_res[w["id"] or w["name"]] = res
        for p in res["problems"]:
            stats["errors"].append(f"{w['name']}: {p}")
        out.append(weapon_block(game, w, res, stats))
    out.append("</div>\n")

    slides, notes = build_slides(g, current, char_res, weapon_res)
    out.append("<h2>Slides for the start screen</h2>\n<p>"
               + "<br>\n".join(f"{esc(s['name'])} - {esc(s['banner'])} - group {esc(s['group'])} - "
                               + (f"art {esc(s.get('art') or s.get('artRemote') or 'none')}"
                                  + (" - description: yes" if s.get("desc") else " - description: no")
                                  if s["kind"] == "character" else f"icon {esc(s.get('icon') or s.get('iconRemote') or 'none')} - full-size art {esc(s.get('art') or 'none')}")
                               for s in slides) + "</p>\n")
    for n in notes:
        out.append(f"<p><small>Note: {esc(n)}.</small></p>\n")
    return "".join(out), stats, slides, notes, ""


# ----------------------------------------------------------------------------- main

def run(base: str, lang: str, out_path: str, ui_dir=None, now: int | None = None, embed_all_art: bool = False) -> int:
    now = int(time.time()) if now is None else int(now)
    store = AssetStore(ui_dir)
    manifest, manifest_err = None, ""
    try:
        manifest = get_json(f"{NANOKA}/manifest.json")
        if not isinstance(manifest, dict):
            raise FetchError("manifest is not a JSON object")
    except FetchError as e:
        manifest, manifest_err = None, f"manifest: {e}"
        print(f"nanoka manifest unavailable: {e}", flush=True)

    previous = load_previous(os.path.join(ui_dir, "js", "banners.js")) if ui_dir else None
    sections, all_stats = [], []
    payload = {"generatedAt": iso_utc(now), "source": base, "slides": {}, "games": {}}
    for n, g in enumerate(GAMES):
        if n:
            time.sleep(0.5)  # stay far below the API limit of 2 requests per second
        section, stats, slides, notes, failed = process_game(g, base, lang, now, manifest, manifest_err, store, embed_all_art)
        sections.append(section)
        all_stats.append((g["title"], stats))
        info = {"banners": stats["banners"], "notes": notes, "problems": stats["errors"]}
        if failed:
            old = ((previous or {}).get("slides") or {}).get(g["ui"])
            payload["slides"][g["ui"]] = old if old else []
            info.update({"status": "error", "message": failed, "keptPrevious": bool(old)})
        else:
            payload["slides"][g["ui"]] = slides
            info["status"] = "ok"
        payload["games"][g["ui"]] = info

    written = ""
    if ui_dir:
        written = write_banners_js(ui_dir, payload)

    summary = []
    for title, s in all_stats:
        summary.append(f"{title}: {s['banners']} current banner(s), {s['characters']} character(s), {s['weapons']} weapon(s), "
                       f"images ok {s['ok']}, failed {s['fail']}, descriptions missing {s['desc_missing']}"
                       + (f", problems: {' | '.join(s['errors'])}" if s["errors"] else ""))
    notes_txt = ("Permanent banners (standard, beginner, bangboo) are not in the calendar and are not listed. "
                 "The calendar has no banner-level artwork; the images are item icons and character art.")
    export_txt = (f"Start screen data written to {written} and image files under {os.path.join(ui_dir, 'assets', 'art')}."
                  if ui_dir else "Export to the start screen was switched off (--no-export).")
    data_json = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")       # no "</script>" or "<!--" from the server's text can end or confuse the block
    page = (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<title>Banner tracker test</title>\n</head>\n<body>\n"
        f"<p><small>Generated {esc(fmt_ts(now))}. Banner source: {esc(base)}. {esc(export_txt)}</small></p>\n"
        + "<hr>\n".join(sections)
        + "<hr>\n<details>\n<summary>Fetch log</summary>\n<pre>\n"
        + esc("\n".join(summary)) + "\n\n" + esc(notes_txt) + "\n\nstatus\tsize\ttime\turl\n"
        + esc("\n".join(LOG)) + "\n</pre>\n</details>\n"
        + f'<script type="application/json" id="ui-data">{data_json}</script>\n'
        + "<script>document.querySelectorAll('img[data-nat]').forEach(function (i) { function f() { var s = i.parentNode.querySelector('.nat'); "
          "if (s) s.textContent = i.naturalWidth + ' x ' + i.naturalHeight + ' px'; } if (i.complete) f(); else i.onload = f; });</script>\n"
        + "</body>\n</html>\n"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
    print("\n".join(summary))
    print(f"\nReport written: {out_path} ({os.path.getsize(out_path) // 1024} KB)")
    print(export_txt)
    return 0 if any(s["banners"] for _, s in all_stats) else 1


def main() -> int:
    global ZZZ_ART_ORDER, ZZZ_ART_PROBE
    here = os.path.dirname(os.path.abspath(__file__))
    default_out = os.path.join(here, "banner_tracker_test.html")
    ui_guess = os.path.join(here, "pulls_tracker_ui")
    default_ui = ui_guess if os.path.isdir(ui_guess) else os.path.join(here, "ui_export")
    ap = argparse.ArgumentParser(description="Banner fetcher: current banners -> their characters and weapons -> art and descriptions")
    ap.add_argument("--base-url", default=API_BASE, help="hoyoverse-api base URL (default %(default)s)")
    ap.add_argument("--lang", default="en-us", help="language code for the calendar (default %(default)s)")
    ap.add_argument("--out", default=default_out, help="report HTML file (default: next to this script)")
    ap.add_argument("--ui-dir", default=default_ui, help="start screen folder that receives js/banners.js and assets/art (default %(default)s)")
    ap.add_argument("--no-export", action="store_true", help="write the report only, no start screen files")
    ap.add_argument("--embed-all-art", action="store_true", help="embed the art of rate-up characters in the report too (larger file)")
    ap.add_argument("--zzz-art", default=",".join(ZZZ_ART_ORDER), metavar="LIST",
                    help="Zenless Zone Zero art sources, best first, from: " + ", ".join(ZZZ_ART_VARIANTS) + " (default %(default)s)")
    ap.add_argument("--zzz-art-probe", action="store_true",
                    help="download every Zenless Zone Zero art variant of the featured agents, save them, and list their sizes in the report")
    args = ap.parse_args()
    order = [v.strip() for v in args.zzz_art.split(",") if v.strip()]
    bad = [v for v in order if v not in ZZZ_ART_VARIANTS]
    if bad or not order:
        ap.error(f"--zzz-art: unknown variant {bad or ''}; choose from {', '.join(ZZZ_ART_VARIANTS)}")
    ZZZ_ART_ORDER, ZZZ_ART_PROBE = order, args.zzz_art_probe
    return run(args.base_url, args.lang, args.out, None if args.no_export else args.ui_dir, embed_all_art=args.embed_all_art)


if __name__ == "__main__":
    sys.exit(main())
