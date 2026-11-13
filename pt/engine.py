"""Pity, guarantee and statistics: pure functions over the stored pulls of one account (rebuild analysis section 5, defects D2 to D6, D12).

Pity is counted per pity group (Genshin Wish 1 and Wish 2 are one group), oldest pull first, from the stored history of that group only.
The first 5-star of a group has a pity that is only a lower bound, because the history may start in the middle of a pity count (the
servers keep a limited window, or the account was played before the first sync): rows of that first segment have lb = True.
The guarantee is a small state machine: None (unknown) until a 5-star shows what the last 50/50 gave.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from pt.games import Game, Group

SLACK = 120      # seconds around a banner's start and end in which a pull still counts as made on that banner


@dataclass
class Row:
    id: str
    n: int                    # number of the pull inside its group, from 1
    time: int                 # unix seconds, UTC
    name: str
    rarity: int               # 3, 4, 5 (the interface's numbering in every game)
    group: str
    pity: int
    lb: bool
    result: Optional[dict] = None
    sub: Optional[int] = None
    code: str = ""


@dataclass
class Derived:
    rows: list = field(default_factory=list)
    pity: int = 0                         # pulls since the last 5-star (the current counter)
    guarantee: Optional[bool] = None      # next 5-star is the featured one; None: unknown or no guarantee on this group
    fives: int = 0
    radiance: int = 0                     # consecutive lost 50/50s since the last won one (an estimate; Genshin character group only)


def _norm(name: str) -> str:
    """A name as compared: lower case, one space between words, and the curly apostrophes as straight ones (a list written with ' must match
    an item the service writes with a curly one, as in "But the Battle Isn't Over")."""
    return " ".join(str(name).replace("’", "'").replace("‘", "'").lower().split())


class Classifier:
    """Says whether a 5-star was the featured item of its banner: True, False, or None when nothing says so."""

    def __init__(self, banners: list[dict], standard: Optional[list[str]]):
        # banners: [{start: unix, end: unix, names: set of featured 5-star names}] of this game and group
        self.banners = banners
        self.standard = {_norm(n) for n in standard} if standard else None

    def __call__(self, name: str, t: int) -> Optional[bool]:
        key = _norm(name)
        names = set()
        for b in self.banners:
            if b["start"] - SLACK <= t <= b["end"] + SLACK:
                names |= {_norm(n) for n in b["names"]}
        if names:
            return key in names
        if self.standard is not None:
            return key not in self.standard
        return None


def derive(game: Game, group: Group, pulls: list[dict], classify: Optional[Callable[[str, int], Optional[bool]]] = None) -> Derived:
    """`pulls`: dicts with id, time, name, rarity, gacha_type, oldest first. Returns every pull with its pity and (5-stars) result."""
    d = Derived()
    pity, seg, guar, loss_streak = 0, 0, None, 0
    for i, p in enumerate(pulls, 1):
        pity += 1
        sub = (2 if p["gacha_type"] == "400" else 1) if game.key == "genshin" and group.key == "character" else None
        row = Row(id=p["id"], n=i, time=p["time"], name=p["name"], rarity=p["rarity"], group=group.key, pity=pity, lb=seg == 0, sub=sub, code=p["gacha_type"])
        if row.rarity == 5:
            if group.flip:
                if guar is True:
                    row.result = {"text": "WON guaranteed", "kind": "wonG", "flip": False, "assumed": False}
                    guar = False
                else:
                    featured = classify(row.name, row.time) if classify else None
                    if featured is None:
                        guar = None
                    else:
                        assumed = guar is None and featured       # a lost 50/50 can only have been a 50/50: nothing is assumed there
                        row.result = ({"text": f"WON {group.flip}", "kind": "won", "flip": True, "assumed": assumed} if featured
                                      else {"text": f"LOST {group.flip}", "kind": "lost", "flip": True, "assumed": False})
                        guar = not featured
                        loss_streak = 0 if featured else loss_streak + 1
            seg += 1
            pity = 0
        d.rows.append(row)
    d.pity, d.guarantee, d.fives = pity, (guar if group.flip else None), seg
    d.radiance = loss_streak if (game.key == "genshin" and group.key == "character") else 0
    return d


def grade(avg: Optional[float], g: dict) -> Optional[str]:
    """Luck grade from the average pity against the saved thresholds (analysis section 5)."""
    if avg is None:
        return None
    return "SS" if avg < g["ss"] else "A" if avg <= g["a"] else "B" if avg <= g["b"] else "C" if avg <= g["c"] else "F"


def stats(game: Game, derived: dict[str, Derived], keys: list[str], since: int, settings: dict, official: Optional[dict]) -> dict:
    """The figures of the Stats screen. `derived` is by group key; pulls older than `since` (unix seconds) are left out of every count
    (pity itself was counted over the whole history)."""
    rows = [r for k in keys if k in derived for r in derived[k].rows if r.time >= since]
    fives = [r for r in rows if r.rarity == 5]
    avg = (sum(r.pity for r in fives) / len(fives)) if fives else None      # D12: one definition, the mean pity of the 5-stars
    groups = {g.key: g for g in game.groups}
    # The bins also reach the largest pity there is: a history with gaps (a partial import) can show more than the hard pity, and a 5-star
    # outside every bin would make the bins add up to less than "fives". No group at all gives no bins instead of an error.
    hard_max = max([groups[k].hard for k in keys if k in groups] + [r.pity for r in fives], default=0)
    bins, lo = [], 1
    while lo <= hard_max:
        hi = lo + 9
        bins.append({"label": f"{lo} - {min(hi, hard_max)}", "from": lo, "to": hi, "n": sum(1 for r in fives if lo <= r.pity <= hi)})
        lo += 10
    flips = []
    for k in keys:
        g = groups.get(k)
        if g and g.flip:
            f = [r for r in fives if r.group == k and r.result and r.result["flip"]]
            flips.append({"group": k, "label": g.short, "odds": g.flip,
                          "won": sum(1 for r in f if r.result["kind"] == "won"), "lost": sum(1 for r in f if r.result["kind"] == "lost")})
    return {
        "pulls": len(rows), "fives": len(fives), "avg": avg, "avgLowerBound": any(r.lb for r in fives),
        "best": min((r.pity for r in fives), default=None), "worst": max((r.pity for r in fives), default=None),
        "grade": grade(avg, settings["grade"]), "bins": bins, "flips": flips,
        "official": [{"kind": k, **v} for k, v in official.items()] if official else None,
        "spent": len(rows) * game.pull_cost, "currency": game.currency, "pullCost": game.pull_cost,
    }


def iso(t: int) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
