"""The rules of the three games as data (rebuild analysis section 2 and 7: a registry, not code).

Everything here can be wrong in a way only a live account shows; items marked [U] are unverified. The values the screens show
(pity caps, labels, flip types) are the same as window.PT_RULES in the interface (js/api.js); keep them in step.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("pt.games")


@dataclass(frozen=True)
class Group:
    """One pity counter. `codes` are the gacha_type values that share it (Genshin 301 and 400 are Wish 1 and Wish 2)."""
    key: str
    label: str
    short: str
    hard: int
    flip: str | None          # "50/50" or "75/25": the banner type has a featured-item guarantee; None: no guarantee
    codes: tuple[str, ...]
    request: str              # the gacha_type to ask the API for (it returns the aliases of `codes` with it)
    visible: bool = True      # False: stored and fetched, but not a screen group (Beginner banners)


@dataclass(frozen=True)
class Game:
    key: str                  # key of the interface: genshin, hsr, zzz
    title: str
    uigf: str                 # UIGF v4 key: hk4e, hkrpg, nap
    biz: str
    api_path: str
    hosts: tuple[str, ...]    # fallback only: the host of the cached link is used first
    groups: tuple[Group, ...]
    top_raw: int              # rank_type of the top rarity in the API (ZZZ calls its S-Rank "4")
    currency: str
    pull_cost: int = 160
    request_extra: tuple[str, ...] = ()       # parameters that repeat gacha_type (ZZZ: real_gacha_type)
    novice_pulls: int | None = None           # pulls in the Beginner banner (when the dashboard says "completed")
    extra_codes: tuple[str, ...] = ()         # gacha_type values that are fetched and stored but have no screen; a failure there is ignored

    @property
    def rarity_offset(self) -> int:
        """The interface calls the top rarity 5 and the next 4 in every game; the API does not (ZZZ)."""
        return 5 - self.top_raw

    def rarity(self, raw: int) -> int:
        return int(raw) + self.rarity_offset

    def group_of(self, code: str) -> Group | None:
        code = str(code)
        return next((g for g in self.groups if code in g.codes), None)

    def ui_groups(self) -> tuple[Group, ...]:
        return tuple(g for g in self.groups if g.visible)


# extra_codes of Genshin: 500 is Chronicled Wish (UIGF lists it; [U] not checked on a live account).
GENSHIN = Game(
    key="genshin", title="Genshin Impact", uigf="hk4e", biz="hk4e_global", api_path="/gacha_info/api/getGachaLog",
    hosts=("public-operation-hk4e-sg.hoyoverse.com", "public-operation-hk4e.mihoyo.com"), top_raw=5, currency="Primogems", novice_pulls=20, extra_codes=("500",),
    groups=(
        Group("character", "Character Event Wish", "Character Event", 90, "50/50", ("301", "400"), "301"),
        Group("weapon", "Weapon Event Wish", "Weapon Event", 80, "75/25", ("302",), "302"),
        Group("standard", "Standard Wish", "Standard", 90, None, ("200",), "200"),
        Group("novice", "Beginner Wish", "Beginner", 90, None, ("100",), "100", visible=False),
    ))
HSR = Game(
    key="hsr", title="Honkai: Star Rail", uigf="hkrpg", biz="hkrpg_global", api_path="/common/gacha_record/api/getGachaLog",
    hosts=("public-operation-hkrpg-sg.hoyoverse.com", "public-operation-hkrpg.mihoyo.com"), top_raw=5, currency="Stellar Jade", novice_pulls=50,
    groups=(
        Group("character", "Character Warp", "Character Warp", 90, "50/50", ("11",), "11"),
        Group("lightcone", "Light Cone Warp", "Light Cone Warp", 80, "75/25", ("12",), "12"),
        Group("standard", "Stellar Warp", "Stellar Warp", 90, None, ("1",), "1"),
        Group("novice", "Departure Warp", "Departure", 90, None, ("2",), "2", visible=False),
    ))
ZZZ = Game(
    key="zzz", title="Zenless Zone Zero", uigf="nap", biz="nap_global", api_path="/common/gacha_record/api/getGachaLog",
    hosts=("public-operation-nap-sg.hoyoverse.com", "public-operation-nap.mihoyo.com"), top_raw=4, currency="Polychrome", request_extra=("real_gacha_type",),
    groups=(
        Group("exclusive", "Exclusive Channel", "Exclusive", 90, "50/50", ("2",), "2"),
        Group("wengine", "W-Engine Channel", "W-Engine", 80, "75/25", ("3",), "3"),
        Group("bangboo", "Bangboo Channel", "Bangboo", 80, None, ("5",), "5"),
        Group("standard", "Stable Channel", "Stable", 90, None, ("1",), "1"),
    ))

GAMES: dict[str, Game] = {g.key: g for g in (GENSHIN, HSR, ZZZ)}
UIGF_KEYS = {g.uigf: g.key for g in GAMES.values()}

# The luck grade, soft pity and pity colours are settings (pt/settings.py). The official rates shown on the Stats screen:
OFFICIAL = {"genshin": {"character": {"pct": 1.6, "pulls": 62.5}, "weapon": {"pct": 1.85, "pulls": 54.1}}}

# 5-star items that cannot be the featured one: a 5-star of this list on a 50/50 banner is a LOST 50/50. Used only when no banner data
# covers the time of the pull (pt/banners.py keeps every banner the program has seen). [U] The lists change when a game moves a character
# into its standard pool; edit data/rules.json ({"standard5": {"genshin": [...]}}) to correct them. For the Star Rail Character Warp the list is
# the user's loss pool (Settings), not this one (analysis D1); for the Star Rail Light Cone Warp it is "hsr_lightcone" below.
STANDARD_5 = {
    "genshin": ["Diluc", "Jean", "Keqing", "Mona", "Qiqi", "Tighnari", "Dehya"],
    "zzz": ["Grace Howard", "Koleda Belobog", "Lycaon", "Rina", "Soldier 11"],
    # The permanent 5-star Light Cones of Star Rail (the 25% of the Light Cone Warp). [U] Taken from a guide page (prydwen.gg, November 2026),
    # not checked on a live account; a Light Cone the game adds to the permanent pool must be added here (data/rules.json).
    "hsr_lightcone": ["But the Battle Isn't Over", "In the Name of the World", "Moment of Victory", "Night on the Milky Way",
                      "Something Irreplaceable", "Sleep Like the Dead", "Time Waits for No One"],
}

# Server regions by the first digits of the UID: (label, UTC offset in hours). The pull times of the API are in the server's time zone.
_REGIONS = {
    "genshin": (("6", "America", -5), ("7", "Europe", 1), ("8", "Asia", 8), ("9", "TW/HK/MO", 8), ("5", "CN", 8), ("1", "CN", 8), ("2", "CN", 8), ("3", "CN", 8)),
    "hsr": (("6", "America", -5), ("7", "Europe", 1), ("8", "Asia", 8), ("9", "TW/HK/MO", 8), ("5", "CN", 8), ("1", "CN", 8), ("2", "CN", 8), ("3", "CN", 8)),
    "zzz": (("10", "America", -5), ("13", "Asia", 8), ("15", "Europe", 1), ("17", "TW/HK/MO", 8)),
}


def region_of(game: str, uid: str) -> tuple[str, int]:
    """(region label, UTC offset in hours) of an account; ("Unknown", 8) when the UID prefix is not known."""
    for prefix, label, offset in _REGIONS.get(game, ()):
        if str(uid).startswith(prefix):
            return label, offset
    return "Unknown", 8


def load_overrides(path: Path) -> None:
    """data/rules.json: {"standard5": {"genshin": [names], "zzz": [names], "hsr_lightcone": [names]}}. A missing or damaged file changes nothing."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for game, names in (data.get("standard5") or {}).items():
            if game in STANDARD_5 and isinstance(names, list) and all(isinstance(n, str) for n in names):
                STANDARD_5[game] = list(names)
    except FileNotFoundError:
        pass
    except Exception:
        log.warning("Could not read %s", path, exc_info=True)
