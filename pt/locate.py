"""Finding the game's saved link on this computer (rebuild analysis section 4).

When the pull history page of a game is opened, the game's embedded browser saves the page address, which holds a temporary key
(`authkey`), in its web cache. This module finds the game folder (the folder chosen in Settings, or the one named in the game's log
file), the newest cache file (`data_2` under `webCaches`) and the addresses with an authkey in it. It reads files only (a cache file that the
game has locked is read from a copy in a temporary folder, removed at once); the requests are in pt/client.py.

Differences from the reference program, as the analysis recommends: the cache file is read as bytes (it is binary), the whole address is
taken (not cut at `game_biz`), the key is never decoded and encoded again (its text is kept as it is in the file), and an address is only
used when its host belongs to the game's company (a cache can hold addresses of other sites).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlsplit

from pt.games import Game

log = logging.getLogger("pt.locate")

# log files under %USERPROFILE%\AppData\LocalLow
LOG_FILES = {
    "genshin": ("miHoYo/Genshin Impact/output_log.txt", "miHoYo/原神/output_log.txt", "Cognosphere/Genshin Impact/output_log.txt"),
    "hsr": ("Cognosphere/Star Rail/Player.log", "miHoYo/Star Rail/Player.log"),
    "zzz": ("miHoYo/ZenlessZoneZero/Player.log", "Cognosphere/ZenlessZoneZero/Player.log"),
}
LOG_READ_BYTES = 512 * 1024         # the folder is named near the start of each log
SEARCH_DEPTH = 4                    # levels below a folder chosen in Settings that are searched for "webCaches"
MAX_CANDIDATES = 40                 # addresses that are tested per sync (newest first)
ALLOWED_HOST_SUFFIXES = (".hoyoverse.com", ".mihoyo.com", ".hoyolab.com")

_URL = re.compile(rb"https://[\x21-\x7e]+")


def local_low() -> Path:
    home = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(home) / "AppData" / "LocalLow"


# ---- game folder -----------------------------------------------------------------------------------------------------------

def _read_head(path: Path) -> str:
    try:
        with open(path, "rb") as f:
            return f.read(LOG_READ_BYTES).decode("utf-8", "replace")
    except OSError:
        return ""


def data_dir_from_log(game: str, text: str) -> Optional[Path]:
    """The game's data folder named by its log (text = the start of the log file)."""
    if game == "genshin":
        m = re.search(r"([A-Za-z]:[/\\][^\r\n]*?(?:GenshinImpact_Data|YuanShen_Data))", text)
        return Path(m.group(1)) if m else None
    lines = text.splitlines()
    if game == "hsr":
        for line in lines[:80]:
            m = re.search(r"Loading player data from (.+?)data\.unity3d", line)
            if m:
                return Path(m.group(1).rstrip("/\\"))
    if game == "zzz":
        for line in lines[:60]:
            if line.startswith("[Subsystems] Discovering subsystems at path "):
                p = line.split("path ", 1)[1].strip()
                p = re.sub(r"[/\\]?UnitySubsystems[/\\]?$", "", p)
                return Path(p.rstrip("/\\"))
    return None


def folders_from_logs(game: str, log_root: Optional[Path] = None) -> list[Path]:
    """Data folders that the log files of the game name (every log file that exists is tried, a folder that is gone is dropped)."""
    root = log_root or local_low()
    found: list[Path] = []
    for rel in LOG_FILES.get(game, ()):
        f = root / rel
        if f.is_file():
            p = data_dir_from_log(game, _read_head(f))
            if p is not None and p not in found:
                found.append(p)
    return found


def _webcaches_under(folder: Path, depth: int = SEARCH_DEPTH) -> list[Path]:
    """Folders named webCaches in `folder` and up to `depth` levels below it."""
    out: list[Path] = []
    try:
        base = len(folder.parts)
        for dirpath, dirnames, _ in os.walk(folder):
            here = Path(dirpath)
            if here.name == "webCaches":
                out.append(here)
                dirnames[:] = []
                continue
            if len(here.parts) - base >= depth:
                dirnames[:] = []
    except OSError:
        log.debug("Could not search %s", folder, exc_info=True)
    return out


def _chosen_roots(chosen: str) -> list[Path]:
    """The web cache folders at or below the folder chosen in Settings (none when nothing is chosen or the folder holds none)."""
    if not chosen.strip():
        return []
    c = Path(chosen.strip())
    found = [c] if c.name == "webCaches" else _webcaches_under(c)
    return [p for p in found if p.is_dir()]


def cache_roots(game: str, chosen: str = "", log_root: Optional[Path] = None) -> list[Path]:
    """Folders that can hold the game's web cache: under the folder chosen in Settings, then under the ones the log files name."""
    roots: list[Path] = []
    seen = set()

    def add(p: Path) -> None:
        if p.is_dir() and p not in seen:
            seen.add(p)
            roots.append(p)

    for w in _chosen_roots(chosen):
        add(w)
    for d in folders_from_logs(game, log_root):
        add(d / "webCaches")
        add(d.parent / "webCaches")
    return roots


def cache_files(game: str, chosen: str = "", log_root: Optional[Path] = None) -> list[Path]:
    """Every `data_2` file in those folders, newest first."""
    files: list[Path] = []
    for root in cache_roots(game, chosen, log_root):
        try:
            files += [f for f in root.rglob("data_2") if f.is_file()]
        except OSError:
            log.debug("Could not search %s", root, exc_info=True)
    return sorted(set(files), key=_mtime, reverse=True)


def _mtime(f: Path) -> float:
    """The game can replace or remove a cache file between the search and this call: such a file sorts last instead of ending the sync."""
    try:
        return f.stat().st_mtime
    except OSError:
        return 0.0


def game_folder(game: str, chosen: str = "", log_root: Optional[Path] = None) -> Optional[Path]:
    """The folder to show as "the game folder": the one from Settings when it holds a web cache, else the one the log names."""
    if _chosen_roots(chosen):      # only the chosen folder counts here: a cache found through the logs must not make an empty choice look valid
        return Path(chosen.strip())
    named = folders_from_logs(game, log_root)
    return named[0] if named else None


# ---- reading the cache --------------------------------------------------------------------------------------------------------

def read_cache(path: Path) -> bytes:
    """The bytes of a cache file. While the game runs the file can be locked: then a copy is read."""
    try:
        return path.read_bytes()
    except OSError:
        tmp = Path(tempfile.mkdtemp(prefix="pt_cache_"))
        try:
            copy = tmp / path.name
            shutil.copyfile(path, copy)
            return copy.read_bytes()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def allowed_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host.endswith(s) for s in ALLOWED_HOST_SUFFIXES)


@dataclass(frozen=True)
class Link:
    """An address from the cache with an authkey. `pairs` are the query parameters exactly as they are in the file ("name=value"), so the
    key is sent back unchanged. Never write `raw` or `pairs` to a log: the key is a temporary password. They are left out of repr(), so a
    log line or a traceback that formats the object does not carry the key either."""
    raw: str = field(repr=False)
    host: str
    path: str
    pairs: tuple[str, ...] = field(repr=False)

    def value(self, name: str) -> Optional[str]:
        for p in self.pairs:
            k, _, v = p.partition("=")
            if k == name:
                return v
        return None

    def api_like(self) -> bool:
        return self.path.endswith("/getGachaLog")


def candidate_links(data: bytes, game: Optional[Game] = None) -> list[Link]:
    """Addresses with an authkey in a cache file, the one nearest the end of the file first. (The `timestamp` in an address is not the time the
    game made the key: on Zenless the newest one was 23 days old for a key made hours before, so it is not used to order the addresses or to
    tell their age.) Identical addresses are listed once (two that differ, for example by bytes of the next cache entry that follow the
    address, are both kept and both tested); an address of another game or of a host that is not the company's is skipped."""
    found: list[Link] = []
    seen = set()
    for m in reversed(list(_URL.finditer(data))):
        text = m.group(0).decode("ascii", "ignore")
        if "authkey=" not in text:
            continue
        text = re.split(r"[\"'<>\\]", text, maxsplit=1)[0].split("#", 1)[0]
        try:
            parts = urlsplit(text)
        except ValueError:
            continue
        if parts.scheme != "https" or not allowed_host(parts.hostname or ""):
            continue
        pairs = tuple(p for p in parts.query.split("&") if p)
        link = Link(text, (parts.hostname or "").lower(), parts.path, pairs)
        if not link.value("authkey") or link.raw in seen:
            continue
        biz = link.value("game_biz")
        if game is not None and biz and biz != game.biz:
            continue
        seen.add(link.raw)
        found.append(link)
    return found


def hosts_for(game: Game, link: Link) -> list[str]:
    """Hosts to try for the history requests: the cached address' own host when it is the API one, then the game's known hosts."""
    hosts = [link.host] if link.api_like() else []
    hosts += [h for h in game.hosts if h not in hosts]
    return hosts


def request_url(game: Game, link: Link, host: str, params: dict[str, str]) -> str:
    """The history request: the parameters of the cached address, with the ones in `params` set (and the game's `game_biz` when missing)."""
    drop = set(params) | {"page", "size", "end_id", "gacha_type", "real_gacha_type"}
    pairs = [p for p in link.pairs if p.partition("=")[0] not in drop]
    if link.value("game_biz") is None:
        pairs.append(f"game_biz={game.biz}")
    pairs += [f"{k}={quote(str(v), safe='')}" for k, v in params.items()]
    return f"https://{host}{game.api_path}?{'&'.join(pairs)}"


def describe(link: Link) -> str:
    """For the log: host and length of the key, never the key."""
    return f"{link.host}{link.path} (key of {len(link.value('authkey') or '')} characters)"
