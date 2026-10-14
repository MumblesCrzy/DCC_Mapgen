#!/usr/bin/env python3
"""
dcc_mapgen.py - Dungeon Crawler Carl style floor generator (Floors 1 - 11).

No dependencies beyond the standard library. Output is Markdown.

Usage:
    python dcc_mapgen.py --floor 1 --size medium
    python dcc_mapgen.py --floor 2 --size large --scaling distance --seed 42
    python dcc_mapgen.py --floor 1 --size small --days 5 --out floor1.md
    python dcc_mapgen.py --floor 2 --size large --seed 9 --out f2.md --image f2.svg
    python dcc_mapgen.py --floor 2 --size large --save maps/     # writes maps/floor2_large_seedNNN.md + .png

Sizes:
    small    1 neighborhood, 1 neighborhood boss,  +1 hidden stairwell
    medium   1 borough  (2x2 neighborhoods), 1 borough boss, 4 nbhd bosses, +2 hidden
    large    1 city     (4x4 neighborhoods, 4 boroughs), 1 city boss, 4 borough
             bosses, 16 nbhd bosses, +4 hidden
    huge/giant/massive/continent (floors 1-2 only): tiles "large" c x c times, each tile
             its own City (4x4 neighborhoods, 4 boroughs, 1 City Boss), plus one top-tier
             boss overseeing every City Boss on the floor:
               huge      = County    2x2 = 4  Cities,  64 nbhds
               giant     = State     3x3 = 9  Cities, 144 nbhds
               massive   = Country   4x4 = 16 Cities, 256 nbhds
               continent = Continent 5x5 = 25 Cities, 400 nbhds
             Use --stair-chance and --borough-boss-rate to thin out neighborhood
             stairwells / borough bosses on any size.

Floor 3 (the Over City) uses a different layout: settlements on a grid of
wilderness regions (small 3x3, medium 4x4, large 5x5), roaming bosses, locked
stairwells, quests, and one Elite.

Floor 4 (the Iron Tangle) is a rail map: size sets how many colored lines are
detailed (2/4/7) and how many named trains ride over them (1/2/3). Output is a
schematic transit map.

Floor 5 (the Bubbles) is one bubble with its four quadrants and castles; size
sets how deeply each castle is detailed (small: castle + boss; medium: adds
approach and interior obstacle; large: adds a chamber-by-chamber list).

Floor 6 (the Hunting Grounds) is Floor 3's grid in jungle, with Zockau pinned
north, a river, hunter parties (3/6/10 detailed), a thorn wall, and a bounty
board.

Floor 7 is skipped (per canon). Floor 8 (the Ghosts of Earth) is one region as
a polar map: 3 rings x 4/6/8 wedges of folklore mobs with T'Ghee totem cards,
a Phase 2 deckmaster ladder, rival squads, and card loot. --region picks the
folklore archetype.

Floor 9 (Faction Wars) is the fixed nine-slice pie around Larracos; the
generator fills warlords (--warlords canon|generated), an optional --wildcard
crawler team, a projected outcome (--winner/--runner-up, DM material), events,
recruitment offers, and three stance paths.

Floor 10 (Don't Come In Last) is a seven-heat race season: garage, vehicle
(--vehicle, --party), upgrade catalog with mechanical track requirements,
rival fields, and a DM projection. Images are one file per heat.

Floor 11 (A Parade of Horribles): a random one-word theme with float ideas,
judges, a 3 km route with events, and an arena with a generated floor boss
and 2/4/6 former crawlers by size.

Word banks live in ./data/*.json — edit those to add content.

Every neighborhood boss chamber contains a stairwell by default (canon); --stair-chance turns
that into a probability instead of a guarantee. Borough and city boss chambers also contain
one (canon) unless --no-boss-stairs is passed. Borough bosses are likewise one per borough by
default; --borough-boss-rate turns that into a probability too.
"""

import argparse
import math
import random
import re
import sys
import zlib
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple


class MapgenError(Exception):
    """A bad option combination or a missing optional dependency.

    Carries a message meant to be shown to a person as-is. The CLI turns it into a
    sys.exit; a GUI shows it in a dialog. Generation code raises this instead of
    calling sys.exit directly so it stays usable outside a terminal."""

# ---------------------------------------------------------------------------
# Floor tuning
# ---------------------------------------------------------------------------

FLOOR_CFG = {
    1: {
        "mob_band": (1, 5),          # standard mob levels
        "days": (4, 6),              # random day count range
        "boss_offset": {"neighborhood": 2, "borough": 4, "city": 6, "top": 9},
        "janitor_level": (1, 3),
        "safe_rooms": (1, 3),
        "guild_chance": 0.45,
        "bosses_roam": False,
        "pack_bonus": 0,
    },
    2: {
        "mob_band": (2, 8),
        "days": (5, 7),
        "boss_offset": {"neighborhood": 3, "borough": 5, "city": 8, "top": 12},
        "janitor_level": (2, 5),
        "safe_rooms": (1, 3),
        "guild_chance": 0.35,
        "bosses_roam": True,
        "pack_bonus": 1,             # packs run one size larger
    },
}

SIZE_CFG = {
    "small":  {"grid": 1, "hidden": 1},
    "medium": {"grid": 2, "hidden": 2},
    "large":  {"grid": 4, "hidden": 4},   # 1 city (2x2 boroughs), 1 City Boss
    # Floors 1-2 only (see --size validation in main()): each step is "large" (one City,
    # 2x2 boroughs) tiled c x c times, plus one top-tier boss overseeing all the City Bosses.
    # Cities-per-side grows by +1 a step rather than doubling, to keep neighborhood counts
    # from exploding the way a literal county-of-cities/state-of-counties nesting would.
    "huge":      {"grid": 8,  "hidden": 6},   # County:    2x2 = 4  cities,  64 nbhds
    "giant":     {"grid": 12, "hidden": 8},   # State:     3x3 = 9  cities, 144 nbhds
    "massive":   {"grid": 16, "hidden": 10},  # Country:   4x4 = 16 cities, 256 nbhds
    "continent": {"grid": 20, "hidden": 12},  # Continent: 5x5 = 25 cities, 400 nbhds
}
BIG_SIZES = ("huge", "giant", "massive", "continent")   # floors 1-2 only
TOP_TIER_NAME = {"huge": "County", "giant": "State", "massive": "Country", "continent": "Continent"}

# Extra filler rooms added per neighborhood on top of its mandatory rooms (entrance,
# boss chamber, safe rooms, guild hall, hidden passages) — never eats into that budget.
# Per-neighborhood detail doesn't grow with the overall city size, so huge+ sizes reuse
# "large"'s range.
ROOM_FILLER_COUNT_BY_SIZE = {
    "small":  (2, 3),
    "medium": (4, 5),
    "large":  (5, 6),
    "huge":   (5, 6),
    "giant":  (5, 6),
    "massive": (5, 6),
    "continent": (5, 6),
}


# ---------------------------------------------------------------------------
# Word banks live in data/*.json next to this script. Edit those to add content.
# ---------------------------------------------------------------------------

import json
import os

# When frozen by PyInstaller, data files are unpacked under sys._MEIPASS instead of
# living next to this .py file.
_BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_BASE_DIR, "data")


def _load_bank(name: str) -> dict:
    path = os.path.join(DATA_DIR, name + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        sys.exit(f"missing word bank: {path}")


for _bank in ("common", "floor1_2", "floor3", "floor4", "floor5", "floor6", "floor8", "floor9", "floor10", "floor11"):
    globals().update(_load_bank(_bank))
MOB_QUIRKS = [tuple(x) for x in MOB_QUIRKS]   # (text, danger modifier)

# Behavioral quirk: (text, danger modifier in [-1, 0, +1])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TITLE_SMALL = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in",
                "nor", "of", "on", "or", "the", "to", "with", "who"}


def cap_words(text: str) -> str:
    """Title-case for display. Not str.title(), which capitalizes after an
    apostrophe ("Magistrate'S Seal") and does not know to leave the small words
    alone ("Set Of Certified Scales")."""
    out = []
    for i, word in enumerate(text.split()):
        keep = i and word.lower() in _TITLE_SMALL
        out.append(word.lower() if keep else word[:1].upper() + word[1:])
    return " ".join(out)


def possessive(name: str) -> str:
    """"the Brine Knights'", not "the Brine Knights's"; "the Camel-folk's"."""
    return name + ("'" if name.endswith("s") else "'s")


def mid_phrase(name: str) -> str:
    """A proper name used inside a phrase: "The Sunken Market" reads better as
    "Business in the Sunken Market" than as "Business in The Sunken Market"."""
    return name[0].lower() + name[1:] if name.startswith("The ") else name


def singular(noun: str) -> str:
    if noun.endswith("ies"):
        return noun[:-3] + "y"
    if noun.endswith("es") and noun[:-2].endswith(("ch", "sh", "x", "o")):
        return noun[:-2]
    if noun.endswith("s"):
        return noun[:-1]
    return noun

def article(word: str) -> str:
    return "an" if word[0].lower() in "aeiou" else "a"

class Deck:
    """Deals from a pool without repeating until the pool runs out, then reshuffles.

    Boss mechanics were drawn with an independent sample per boss, which dedupes
    the two a single boss gets and nothing else. A large floor 1 fields 21 bosses
    across 33 mechanic slots, so the same trick came up five times on one floor and
    twelve times on a huge one -- a boss run turned into the same fight repeatedly.
    Dealing from a shuffled deck means a floor sees every mechanic once before it
    sees any of them twice."""

    def __init__(self, rng, items):
        self.rng = rng
        self.items = list(items)
        self._pile = []

    def draw(self) -> str:
        if not self._pile:
            self._pile = list(self.items)
            self.rng.shuffle(self._pile)
        return self._pile.pop()

    def draw_many(self, k: int) -> List[str]:
        return [self.draw() for _ in range(k)]


def boss_mechanics(decks, tier: str) -> str:
    """Mechanics for one boss: grander bosses get an arena-scale trick as well.

    `decks` is the (any, grand) pair a generator keeps for the whole floor. Tiers
    above neighborhood take one from each, so a city boss reshapes the room while a
    level-7 neighborhood boss keeps to things that fit a room."""
    any_deck, grand_deck = decks
    if tier == "neighborhood":
        return any_deck.draw()
    return f"{grand_deck.draw()} {any_deck.draw()}"


def make_mechanic_decks(rng) -> Tuple["Deck", "Deck"]:
    return Deck(rng, BOSS_MECHANICS["any"]), Deck(rng, BOSS_MECHANICS["grand"])


class BossNamer:
    """Names every boss on one floor, keeping them distinct in shape as well as in
    wording.

    Two shapes used to split the whole game between them -- "{first} {title}" or
    "The {adj} {noun} {title}", on a coin flip -- so a boss montage fell into a
    rhythm even on the floors where no two bosses shared a name. Shapes are dealt
    from a deck now, so a floor runs through all of them before reusing one.

    The old per-floor guards are kept: a first name or a title claimed by one boss
    is never recombined into another on the same floor, which is what stops
    "Vexnia the Auditor" standing two rooms from "Vexnia the Landlord"."""

    MAX_LEN = 50     # past this a name stops fitting a map legend, or a sentence

    def __init__(self, rng, adjs):
        self.rng = rng
        self.adjs = adjs
        self.shapes = Deck(rng, [shape for shape, weight in BOSS_NAME_FORM
                                 for _ in range(weight)])
        self.names = set()
        self.firsts = set()
        self.titles = set()

    def name(self, mob_noun: str) -> str:
        """A fresh name for a boss of a given mob. Falls back to the last candidate
        rather than looping forever once a small floor has drained the banks."""
        for _ in range(8):
            shape = self.shapes.draw()
            for _ in range(25):
                parts = self._parts(mob_noun)
                cand = shape.format(**parts)
                if not self._clashes(shape, parts, cand):
                    self._claim(shape, parts, cand)
                    return cand
        self._claim(shape, parts, cand)
        return cand

    def _parts(self, mob_noun: str) -> dict:
        first, second = self.rng.sample(BOSS_FIRST, 2)
        return {"first": first, "second": second,
                "surname": self.rng.choice(BOSS_SURNAME),
                "nick": self.rng.choice(BOSS_NICKNAME),
                "title": self.rng.choice(BOSS_TITLE),
                "adj": self.rng.choice(self.adjs),
                "noun": singular(mob_noun)}

    def _clashes(self, shape: str, parts: dict, cand: str) -> bool:
        # Length is checked because a shape that stacks a title onto an already
        # multi-word mob produces things like "The Burning Goblin Bomb Bard the
        # Non-Disclosure Agreement", which is nobody's idea of a name.
        if cand in self.names or len(cand) > self.MAX_LEN:
            return True
        if "{first}" in shape and parts["first"] in self.firsts:
            return True
        if "{second}" in shape and parts["second"] in self.firsts:
            return True
        return "{title}" in shape and parts["title"] in self.titles

    def _claim(self, shape: str, parts: dict, cand: str) -> None:
        self.names.add(cand)
        for key in ("first", "second"):
            if "{%s}" % key in shape:
                self.firsts.add(parts[key])
        if "{title}" in shape:
            self.titles.add(parts["title"])


def mob_words(rng, floor: int) -> Tuple[str, str]:
    """An (adjective, noun) pair drawn from the pools a floor can host.

    Mob vocabulary lives in one place, tagged by where it can show up, rather than
    a private list per floor: a rail map had 16 adjectives against 20 nouns to its
    name, which is 320 possible mobs before a repeat. Drawing from the shared pool
    instead gives every floor the whole dungeon's fauna.

    The tag is picked first, by weight, and the word uniformly from it. Picking a
    word uniformly across the merged pools would drown each floor's own character,
    since "anywhere" is several times the size of any habitat.
    """
    weights = FLOOR_MOB_TAGS.get(str(floor), {"anywhere": 1})

    def pick(bank):
        tags = [t for t in weights if bank.get(t)]
        if not tags:
            tags = [t for t in bank if bank[t]]
        tag = rng.choices(tags, weights=[weights.get(t, 1) for t in tags])[0]
        return rng.choice(bank[tag])

    return pick(MOB_ADJ), pick(MOB_NOUN)


def describe_mob(rng, adj: str, noun: str) -> Mob:
    """The System's write-up of one mob. Shared by every floor that has mobs."""
    quirk, mod = rng.choice(MOB_QUIRKS)
    desc = rng.choice(SYSTEM_DESC).format(
        a=article(adj), b=f"{adj.lower()} {singular(noun).lower()}",
        noun=noun, noun_l=noun.lower(), adj_l=adj.lower(),
        noun_sing=singular(noun).lower())
    return Mob(adj, noun, quirk, mod, desc)


def grid_label(i: int) -> str:
    """Spreadsheet-column-style label: 0->A, 25->Z, 26->AA, ... — plain A-Z runs out past
    26 neighborhoods, which huge/giant/massive floors need."""
    i += 1
    s = ""
    while i > 0:
        i, r = divmod(i - 1, 26)
        s = chr(ord("A") + r) + s
    return s

def block_label(br: int, bc: int, k: int, solo: str) -> str:
    """Label for the block at (br, bc) in a k x k grid of same-size blocks (used for both
    boroughs, k x k blocks of 2x2 neighborhoods, and cities, k x k blocks of 2x2 boroughs).
    `solo` is the label to use when there's only one block (k == 1): "Borough" or "City"."""
    if k == 1:
        return solo
    if k == 2:
        return ("N" if br == 0 else "S") + ("W" if bc == 0 else "E")
    return f"Row {br + 1} Col {bc + 1}"

def compass(r: int, c: int, n: int) -> str:
    """Compass label for a cell in an n x n grid."""
    if n == 1:
        return "Center"
    ns = "N" if r < n / 2 else "S"
    ew = "W" if c < n / 2 else "E"
    if n <= 2:
        return ns + ew
    inner_r = 1 <= r <= n - 2
    inner_c = 1 <= c <= n - 2
    if inner_r and inner_c:
        return f"Inner {ns}{ew}"
    return f"Outer {ns}{ew}"

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Mob:
    adj: str
    noun: str
    quirk: str
    danger_mod: int
    description: str

    @property
    def name(self) -> str:
        return f"{self.adj} {self.noun}"

@dataclass
class Boss:
    name: str
    tier: str
    level: int
    form: str
    mechanic: str
    chamber: str
    has_stairwell: bool

@dataclass
class Elite:
    name: str
    show: str
    form: str
    plot_armor: str
    settlement: str
    region: str

@dataclass
class CrawlerTeam:
    """A team of other survivors, carried across the floors of one campaign.

    Identity -- the name, who they were before the Collapse, and where they stand
    with the party -- persists. The report (status, outcome, day, place) is rerolled
    on every floor the team lives to see.

    The reaction text is deliberately *not* stored: it is looked up from the
    disposition when rendered, so editing a team's standing in a roster file, or in
    the app, changes what they do as well as what they are called."""
    name: str
    origin: str
    surname: str = ""              # the bare place name, kept so it is never reused
    disposition: str = "wary"      # hostile..allied; how they take the party
    # Who is actually in the group: one {"race", "class"} per crawler, empty until
    # the floor 3 selection. Race never changes after that; the class can upgrade.
    members: List[dict] = field(default_factory=list)
    # How many crawlers a group has before it has any members to count -- set only
    # by the DM, on floors 1-2. Floor 3 deals exactly this many, and clears it.
    headcount: Optional[int] = None
    # -- this floor's report, rerolled each floor --
    status: str = ""
    outcome: str = "steady"        # steady/thriving/costly/fatal
    day: Optional[int] = None      # None on floors with no day countdown
    floor: int = 1
    # -- campaign record --
    first_seen: int = 1
    died_on: Optional[int] = None
    history: List[dict] = field(default_factory=list)
    # Met at the table rather than dealt by the generator. Such a team carries
    # from floor to floor like any other, but is never renamed and never has its
    # standing re-dealt: the DM gave it both.
    added: bool = False

    @property
    def alive(self) -> bool:
        return self.died_on is None

    @property
    def crawler_count(self) -> Optional[int]:
        """How many crawlers are in the group, or None if nobody has said: a group
        the generator met on floor 1 or 2 has no number until floor 3 deals one."""
        return len(self.members) if self.members else self.headcount

    @property
    def reaction(self) -> str:
        return disposition_reaction(self.disposition)


def disposition_reaction(label: str) -> str:
    """What a standing means in play. Unknown labels are the user's own wording,
    so they are passed through rather than corrected."""
    for d in TEAM_DISPOSITION:
        if d["label"] == label:
            return d["reaction"]
    return f"treats the party as {label}"


def disposition_labels() -> List[str]:
    return [d["label"] for d in TEAM_DISPOSITION]


@dataclass
class RankedCrawler:
    """Someone who has been on the Dungeon Leaderboard: the Top Ten by ratings,
    each with a bounty on their head, from Floor 4.

    Kept once they fall off, as a dead team is kept on the roster. A crawler who
    comes back to the list keeps the multiplier they had, and the campaign keeps a
    record of who was ranked when."""
    name: str
    race: str = ""
    klass: str = ""                # their class; "class" in the roster file
    team: str = ""                 # the roster group they belong to; "" for a stranger
    rank: Optional[int] = None     # 1-10 while on the list, None once off it
    # Every floor they have spent on the list. The multiplier counts these, which
    # is what lets it remember: ranked on floors 4 and 5, off on 6 and back on 7
    # is x3, not x1.
    floors: List[int] = field(default_factory=list)
    # The DM's correction to that count. Setting a multiplier stores the
    # difference, so each later floor still adds one to whatever the DM set.
    bonus: int = 0
    died_on: Optional[int] = None
    added: bool = False            # put on the list by the DM rather than dealt
    # When they came onto the list and went off it: {"floor", "event", "rank"},
    # the event one of entered, fell off, returned, died or revived. "by_dm"
    # marks a change the DM made.
    history: List[dict] = field(default_factory=list)

    @property
    def alive(self) -> bool:
        return self.died_on is None

    @property
    def multiplier(self) -> int:
        return max(1, len(self.floors) + self.bonus)

    @property
    def bounty(self) -> int:
        """Gold on their head: their rank's bounty times their multiplier. Nothing
        while they are off the list."""
        return bounty_for(self.rank, self.multiplier) if self.rank else 0


def bounty_for(rank: int, multiplier: int = 1) -> int:
    """The bounty on whoever holds a rank, in gold."""
    return LEADERBOARD["bounties"][rank - 1] * multiplier


_RANKED_FIELDS = ("name", "race", "klass", "team", "rank", "floors", "bonus",
                  "died_on", "added", "history")


def _ranked_to_dict(c: RankedCrawler) -> dict:
    raw = {f: getattr(c, f) for f in _RANKED_FIELDS}
    raw["class"] = raw.pop("klass")
    return raw


def _ranked_from_dict(raw: dict) -> RankedCrawler:
    if not raw.get("name"):
        raise MapgenError("a crawler on the campaign's leaderboard has no name")
    known = {k: v for k, v in raw.items() if k in _RANKED_FIELDS}
    known["klass"] = raw.get("class", raw.get("klass", ""))
    return RankedCrawler(**known)


@dataclass
class CampaignRoster:
    """Teams followed across a run of floors, including the ones that did not make it.

    The dead are kept: a campaign wants to remember who died on floor 4, and floor
    11 parades crawlers who already died. So is everyone who was ever on the
    leaderboard."""
    teams: List[CrawlerTeam] = field(default_factory=list)
    leaderboard: List[RankedCrawler] = field(default_factory=list)

    def active(self) -> List["CrawlerTeam"]:
        return [t for t in self.teams if t.alive]

    def to_dict(self) -> dict:
        return {"version": ROSTER_VERSION,
                "teams": [_team_to_dict(t) for t in self.teams],
                "leaderboard": [_ranked_to_dict(c) for c in self.leaderboard]}

    @classmethod
    def from_dict(cls, raw: dict) -> "CampaignRoster":
        version = raw.get("version")
        if version not in (None, ROSTER_VERSION):
            raise MapgenError(
                f"campaign roster is version {version}, this build reads "
                f"version {ROSTER_VERSION}")
        return cls(teams=[_team_from_dict(t) for t in raw.get("teams", [])],
                   leaderboard=[_ranked_from_dict(c) for c in raw.get("leaderboard", [])])


# Still 1 with `members`, `headcount`, `added` and the leaderboard added: a roster
# written before parties existed loads fine (the field defaults to empty and fills
# on the next floor 3+), and an older build reading a newer file drops the key it
# does not know. Bumping this would reject both, for no incompatibility.
ROSTER_VERSION = 1
_TEAM_FIELDS = ("name", "origin", "surname", "disposition", "members", "headcount",
                "status", "outcome", "day", "floor", "first_seen", "died_on", "history",
                "added")


def _team_to_dict(t: CrawlerTeam) -> dict:
    return {f: getattr(t, f) for f in _TEAM_FIELDS}


def _team_from_dict(raw: dict) -> CrawlerTeam:
    if not raw.get("name"):
        raise MapgenError("a team in the campaign roster has no name")
    known = {k: v for k, v in raw.items() if k in _TEAM_FIELDS}
    known.setdefault("origin", "an unrecorded group")
    return CrawlerTeam(**known)


def load_roster(path: str) -> CampaignRoster:
    """Read a campaign roster, or hand back an empty one if it isn't there yet --
    the first floor of a campaign has nothing to carry."""
    if not os.path.exists(path):
        return CampaignRoster()
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        raise MapgenError(f"campaign roster {path} is not valid JSON: {e}")
    except OSError as e:
        raise MapgenError(f"could not read campaign roster {path}: {e}")
    return CampaignRoster.from_dict(raw)


def save_roster(roster: CampaignRoster, path: str) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(roster.to_dict(), f, indent=2, ensure_ascii=False)
            f.write("\n")
    except OSError as e:
        raise MapgenError(f"could not write campaign roster {path}: {e}")

@dataclass
class Room:
    id: str              # neighborhood label + index, e.g. "A1"
    kind: str             # Entrance/Den/Junction/Cache/Overlook/Shrine/Safe Room/Guild Hall/Hidden Passage/Boss Chamber
    description: str
    connects_to: List[str] = field(default_factory=list)

@dataclass
class Neighborhood:
    idx: int
    label: str          # A, B, C...
    name: str
    row: int
    col: int
    borough: Optional[str]
    city: Optional[str]
    mob: Mob
    level_range: Tuple[int, int]
    boss: Boss
    loot: str
    safe_rooms: int
    tutorial_guild: bool
    feature: str
    hidden_stairs: List[str] = field(default_factory=list)
    distance: int = 0
    is_entry: bool = False
    loot_tier: str = ""
    rooms: List[Room] = field(default_factory=list)
    corridor_encounters: List[Tuple[str, str, str]] = field(default_factory=list)

@dataclass
class Floor:
    number: int
    size: str
    name: str
    days: int
    janitor: Mob
    janitor_level: int
    storyline: str
    grid: int
    neighborhoods: List[Neighborhood]
    borough_bosses: dict           # borough label -> Boss
    city_bosses: dict              # city label -> Boss (single entry {"City": ...} for small/medium/large)
    top_boss: Optional[Boss]       # oversees multiple cities (County/State/Country/Continent); None below huge
    top_tier_name: str             # "County"/"State"/"Country"/"Continent"; "" when top_boss is None
    scaling: str
    seed: int
    elite: Optional[Elite] = None
    other_crawlers: List[CrawlerTeam] = field(default_factory=list)
    collapse_note: str = ""
    guildmaster_form: str = ""
    guildmaster_blurb: str = ""

# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

class Generator:
    def __init__(self, floor: int, size: str, scaling: str, seed: int,
                 days: Optional[int], boss_stairs: bool,
                 stair_chance: float = 1.0, borough_boss_rate: float = 1.0):
        # Floors 1 and 2 share this generator, and sharing a seed with it meant
        # sharing a dungeon: --floor 1 --seed 4242 and --floor 2 --seed 4242 came
        # back with the same neighborhoods, the same mobs and -- the way this was
        # noticed -- the same 21 bosses, name for name. Every generator mixes its
        # floor into the stream for the same reason; see Generator3.
        self.rng = random.Random(f"floor{floor}|{seed}")
        self.seed = seed
        self.floor = floor
        self.size = size
        self.scaling = scaling
        self.cfg = FLOOR_CFG[floor]
        self.grid = SIZE_CFG[size]["grid"]
        self.hidden = SIZE_CFG[size]["hidden"]
        self.days_override = days
        self.boss_stairs = boss_stairs
        self.stair_chance = stair_chance
        self.borough_boss_rate = borough_boss_rate
        self.used_mobs = set()
        self.mech_decks = make_mechanic_decks(self.rng)
        self.namer = BossNamer(self.rng, NBHD_ADJ)
        # Loot and features are dealt, not rolled. Both banks used to be fifteen
        # entries sampled independently per cell, so a large floor 3 covered 18
        # regions with 11 loot themes and one of them three times over, and a huge
        # floor 1 put the same loot theme on eight of its 64 neighborhoods. Dealing
        # spreads them: repeats only start once the bank is exhausted, and then
        # evenly, instead of clumping wherever the dice fell.
        self.loot_deck = Deck(self.rng, LOOT_THEMES)
        self.feature_deck = Deck(self.rng, NOTABLE_FEATURES)
        self.used_names = set()
        self.used_forms = set()

    # -- pieces --------------------------------------------------------------

    def _distinct(self, bank, k: int) -> list:
        """k picks from `bank` with no repeats, unless the bank is smaller than k.

        A bank too small to fill the request is a content problem, not a reason to
        fail a generation, so the shortfall is topped up with repeats rather than
        raising -- someone trimming a word bank still gets a floor out of it."""
        if k <= len(bank):
            return self.rng.sample(bank, k)
        out = self.rng.sample(bank, len(bank))
        out += [self.rng.choice(bank) for _ in range(k - len(bank))]
        return out

    def make_mob(self) -> Mob:
        for _ in range(100):
            adj, noun = mob_words(self.rng, self.floor)
            if (adj, noun) not in self.used_mobs:
                break
        self.used_mobs.add((adj, noun))
        return describe_mob(self.rng, adj, noun)

    def make_boss(self, tier: str, mob: Mob, base_level: int) -> Boss:
        name = self.namer.name(mob.noun)
        self.used_names.add(name)        # shared with the neighborhood names
        size = {"neighborhood": (8, 15), "borough": (15, 30), "city": (30, 60), "top": (60, 100)}[tier]
        for _ in range(200):
            sz = self.rng.randint(*size)
            form = self.rng.choice(BOSS_FORMS).format(
                size=sz, a_size=("an" if sz in (8, 11, 18) or 80 <= sz < 90 else "a"),
                noun_l=mob.noun.lower(), noun_sing=singular(mob.noun).lower())
            if form not in self.used_forms:
                break
        self.used_forms.add(form)
        level = base_level + self.cfg["boss_offset"][tier]
        mechanics = boss_mechanics(self.mech_decks, tier)
        stair = (self.rng.random() < self.stair_chance) if tier == "neighborhood" else self.boss_stairs
        return Boss(name, tier, level, form, mechanics,
                    self.rng.choice(CHAMBER_STYLE), stair)

    def nbhd_name(self) -> str:
        for _ in range(100):
            name = f"The {self.rng.choice(NBHD_ADJ)} {self.rng.choice(NBHD_PLACE)}"
            if name not in self.used_names:
                break
        self.used_names.add(name)
        return name

    def level_range(self, distance: int, max_distance: int) -> Tuple[int, int]:
        lo, hi = self.cfg["mob_band"]
        if self.scaling == "distance" and max_distance > 0:
            frac = distance / max_distance
            center = lo + frac * (hi - lo)
            a = max(lo, int(round(center - 1)))
            b = min(hi, int(round(center + 1)))
            return (a, max(a, b))
        a = self.rng.randint(lo, hi - 1)
        b = min(hi, a + self.rng.randint(1, 2))
        return (a, b)

    def make_rooms(self, nb: "Neighborhood", guildmaster_form: str) -> Tuple[List[Room], List[Tuple[str, str, str]]]:
        """Build a connected room graph for one neighborhood: mandatory rooms (entrance,
        safe rooms, guild hall, hidden passages, boss chamber) plus size-scaled filler rooms."""
        from collections import deque
        counter = 1
        rooms: List[Room] = []

        def add_room(kind: str, desc: str) -> Room:
            nonlocal counter
            r = Room(id=f"{nb.label}{counter}", kind=kind, description=desc)
            counter += 1
            rooms.append(r)
            return r

        entrance = add_room("Entrance", self.rng.choice(ROOM_ENTRANCE_DESC))

        pending = []
        for _ in range(nb.safe_rooms):
            pending.append(("Safe Room", self.rng.choice(SAFE_ROOM_DESC)))
        if nb.tutorial_guild:
            pending.append(("Guild Hall", f"Mordecai's guild hall (local form: {guildmaster_form}). "
                                           f"{self.rng.choice(TUTORIAL_GUILD_BLURB)}"))
        for hs in nb.hidden_stairs:
            pending.append(("Hidden Passage", hs))

        filler_kinds = list(ROOM_KINDS)
        for _ in range(self.rng.randint(*ROOM_FILLER_COUNT_BY_SIZE[self.size])):
            kind = self.rng.choice(filler_kinds)
            pending.append((kind, self.rng.choice(ROOM_KINDS[kind]).format(mob=nb.mob.noun.lower())))

        self.rng.shuffle(pending)
        for kind, desc in pending:
            r = add_room(kind, desc)
            parent = self.rng.choice(rooms[:-1])
            r.connects_to.append(parent.id)
            parent.connects_to.append(r.id)

        # Fold the neighborhood's notable feature into a random filler room (else the entrance).
        fillers = [r for r in rooms if r.kind in filler_kinds]
        feat_room = self.rng.choice(fillers) if fillers else entrance
        feat_room.description += " " + nb.feature

        # Boss chamber attaches to whichever room is deepest from the entrance.
        graph = {r.id: r.connects_to for r in rooms}
        depth = {entrance.id: 0}
        queue = deque([entrance.id])
        while queue:
            cur = queue.popleft()
            for nxt in graph[cur]:
                if nxt not in depth:
                    depth[nxt] = depth[cur] + 1
                    queue.append(nxt)
        max_depth = max(depth.values())
        deepest_id = self.rng.choice([rid for rid, d in depth.items() if d == max_depth])
        boss_room = add_room("Boss Chamber", nb.boss.chamber)
        boss_room.connects_to.append(deepest_id)
        next(r for r in rooms if r.id == deepest_id).connects_to.append(boss_room.id)

        # A handful of random encounters happen in transit, not inside a fixed room.
        edges = {tuple(sorted((r.id, other))) for r in rooms for other in r.connects_to}
        encounter_ct = min(1 if self.size == "small" else 2, len(edges))
        # sorted(), not list(): a set of string tuples iterates in hash order, which varies
        # per process, so sampling the raw set made --seed give a different map every run.
        corridor_encounters = [(a, b, self.rng.choice(RANDOM_ENCOUNTERS))
                                for a, b in self.rng.sample(sorted(edges), encounter_ct)]

        return rooms, corridor_encounters

    # -- assembly ------------------------------------------------------------

    def build(self) -> Floor:
        n = self.grid
        cells = [(r, c) for r in range(n) for c in range(n)]
        guildmaster_form = MORDECAI_FORMS.get(str(self.floor), "a local guildmaster")

        # Entry point: random edge cell (or the only cell).
        edges = [(r, c) for r, c in cells if r in (0, n - 1) or c in (0, n - 1)]
        entry = self.rng.choice(edges)
        max_dist = max(abs(r - entry[0]) + abs(c - entry[1]) for r, c in cells)

        k = n // 2       # boroughs are 2x2 blocks of neighborhoods, arranged k x k
        c_side = n // 4  # cities are 2x2 blocks of boroughs, arranged c_side x c_side
        nbhds: List[Neighborhood] = []
        for i, (r, c) in enumerate(cells):
            dist = abs(r - entry[0]) + abs(c - entry[1])
            borough = block_label(r // 2, c // 2, k, "Borough") if k >= 1 else None
            city = block_label(r // 4, c // 4, c_side, "City") if c_side >= 1 else None
            mob = self.make_mob()
            lr = self.level_range(dist, max_dist)
            boss = self.make_boss("neighborhood", mob, lr[1])
            tier_idx = min(len(LOOT_BOX_TIERS) - 1, dist + self.rng.randint(0, 1))
            nbhds.append(Neighborhood(
                idx=i, label=grid_label(i), name=self.nbhd_name(),
                row=r, col=c, borough=borough, city=city, mob=mob, level_range=lr,
                boss=boss, loot=self.loot_deck.draw(),
                safe_rooms=self.rng.randint(*self.cfg["safe_rooms"]),
                tutorial_guild=self.rng.random() < self.cfg["guild_chance"],
                feature=self.feature_deck.draw(),
                distance=dist, is_entry=(r, c) == entry,
                loot_tier=LOOT_BOX_TIERS[tier_idx],
            ))

        # Hidden stairwells, spread across distinct neighborhoods when possible.
        pool = nbhds[:]
        self.rng.shuffle(pool)
        spots = self.rng.sample(HIDDEN_STAIR_SPOTS, self.hidden)
        for hs_i in range(self.hidden):
            pool[hs_i % len(pool)].hidden_stairs.append(spots[hs_i])

        # Borough bosses. Not every borough necessarily gets one — see --borough-boss-rate.
        borough_bosses = {}
        if n >= 2:
            names = sorted({nb.borough for nb in nbhds})
            for bname in names:
                if self.rng.random() >= self.borough_boss_rate:
                    continue
                members = [nb for nb in nbhds if nb.borough == bname]
                top = max(nb.level_range[1] for nb in members)
                mob = self.rng.choice(members).mob
                borough_bosses[bname] = self.make_boss("borough", mob, top)

        # City bosses: one per city, always (bosses cap at city tier within a city — huge+
        # sizes add more cities, not a bigger city boss).
        city_bosses = {}
        if n >= 4:
            for cname in sorted({nb.city for nb in nbhds}):
                members = [nb for nb in nbhds if nb.city == cname]
                top = max(nb.level_range[1] for nb in members)
                mob = self.rng.choice(members).mob
                city_bosses[cname] = self.make_boss("city", mob, top)

        # Top-tier boss (County/State/Country/Continent): one overseeing every City Boss,
        # only when there's more than one city to oversee.
        top_boss = None
        top_tier_name = TOP_TIER_NAME.get(self.size, "")
        if c_side >= 2:
            top = max(nb.level_range[1] for nb in nbhds)
            top_boss = self.make_boss("top", self.rng.choice(nbhds).mob, top)

        janitor = self.make_mob()
        janitor.quirk = self.rng.choice(JANITOR_MECHANICS)
        janitor.description = "Janitor mob. " + janitor.description

        for nb in nbhds:
            nb.rooms, nb.corridor_encounters = self.make_rooms(nb, guildmaster_form)

        days = self.days_override or self.rng.randint(*self.cfg["days"])
        title = {"small": "Neighborhood", "medium": "Borough", "large": "City"}.get(
            self.size, TOP_TIER_NAME.get(self.size, "City"))
        floor_name = f"The {self.rng.choice(NBHD_ADJ)} {title}"

        elite_host = self.rng.choice(nbhds)
        elite = Elite(
            name=f"{self.rng.choice(BOSS_FIRST)} {self.rng.choice(ELITE_TITLE)}",
            show=self.rng.choice(ELITE_SHOW).format(
                noun=singular(elite_host.mob.noun), adj=self.rng.choice(NBHD_ADJ)),
            form=self.rng.choice(ELITE_FORM).format(race=singular(elite_host.mob.noun).lower()),
            plot_armor=self.rng.choice(PLOT_ARMOR),
            settlement=elite_host.name, region=floor_name)


        return Floor(
            number=self.floor, size=self.size, name=floor_name, days=days,
            janitor=janitor, janitor_level=self.rng.randint(*self.cfg["janitor_level"]),
            storyline=self.rng.choice(STORYLINES), grid=n, neighborhoods=nbhds,
            borough_bosses=borough_bosses, city_bosses=city_bosses,
            top_boss=top_boss, top_tier_name=top_tier_name,
            scaling=self.scaling, seed=self.seed,
            elite=elite,
            collapse_note=self.rng.choice(COLLAPSE_NOTES),
            guildmaster_form=guildmaster_form,
            guildmaster_blurb=self.rng.choice(TUTORIAL_GUILD_BLURB),
        )

# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def night_threat(entry) -> Tuple[str, str]:
    """A night-threat line, and the shift its region still needs a mob for.

    Plain strings are the common case: one mob holds the region around the clock
    and behaves differently after dark. An entry written as [text, "day"] or
    [text, "night"] means the listed mob only holds half the cycle, and the tag
    names the half that is left over. "Dormant by day" needs a daylight mob; a
    region whose real predator comes out at night needs that predator. One of
    these used to end with "(roll a second mob)", which is a generator telling
    the table to do its job for it."""
    if isinstance(entry, (list, tuple)):
        return entry[0], entry[1]
    return entry, ""


def boss_box(tier: str, floor: int) -> str:
    """The Boss Box a boss drops: a base by boss tier, a step for how deep the floor
    is, capped short of Celestial. Worked out at render time from what the boss
    already is, so it draws nothing from any rng and moves no map."""
    bb = LOOT_BOXES["boss_box"]
    idx = min(bb["base"].get(tier, 0) + bb["depth"].get(str(floor), 0), bb["cap"])
    return f"{LOOT_BOX_TIERS[idx]} Boss Box"


def box_types_for(floor: int) -> List[dict]:
    """Box types that exist by this floor. Sponsors arrive on Floor 4, and with
    them Benefactor boxes; the leaderboard arrives there too, and Bounty boxes."""
    return [t for t in LOOT_BOXES["types"] if t["from_floor"] <= floor]


def _fit_kind(pool: str) -> str:
    """Which enchantment deck an item draws from. A weapon takes "on a hit"
    effects, worn gear takes equipment effects, and a scroll or a wand takes only
    the ones that read on anything. A leather jacket that "deals extra damage to
    undead" is what happens without this."""
    return "+".join(LOOT_BOXES["effect_kinds"].get(pool, ["any"]))


# Plain pools (potions, tools, explosives, oddities) take no enchantment, so they
# scale by band instead: a Celestial box does not hold a ten-foot pole.
LOOT_BANDS = {"Bronze": "low", "Silver": "low", "Gold": "mid", "Platinum": "mid",
              "Legendary": "high", "Celestial": "high"}

_BOX_RE = re.compile(r"\b(" + "|".join(LOOT_BOX_TIERS) +
                     r")\s+((?:[A-Z][\w']*\s+)*?[A-Z][\w']*)\s+Box\b")


def find_box_type(name: str) -> dict:
    """A box type by name, case-insensitively.

    A name the bank does not know is still a box: achievements name their own
    after whatever earned them, so "Podiatrist" is a perfectly good box type. It
    opens from the Adventurer pools and keeps the name it was given."""
    wanted = name.strip().lower()
    for t in LOOT_BOXES["types"] + LOOT_BOXES["themed_types"]:
        if t["name"].lower() == wanted:
            return t
    base = next(t for t in LOOT_BOXES["types"] if t["name"] == "Adventurer")
    return {"name": cap_words(name.strip()), "pools": base["pools"],
            "earned": "an achievement of its own", "unknown": True}


def parse_box(text: str) -> Tuple[str, dict]:
    """Read a box the way a DM would write it, or paste it off a write-up:
    "a Silver Boss Box", "Silver Boss", "gold weapon box". Returns (tier, type).
    Raises MapgenError when there is no tier to read."""
    words = text.strip().rstrip(".").split()
    if words and words[0].lower() in ("a", "an", "the"):
        words = words[1:]
    if words and words[-1].lower() == "box":
        words = words[:-1]
    tiers = {t.lower(): t for t in LOOT_BOX_TIERS}
    if len(words) < 2 or words[0].lower() not in tiers:
        raise MapgenError(f"cannot read a box from {text!r}: expected a tier "
                          f"({', '.join(LOOT_BOX_TIERS)}) then a type, "
                          f"as in \"Silver Boss\" or \"a Gold Weapon Box\"")
    return tiers[words[0].lower()], find_box_type(" ".join(words[1:]))


@dataclass
class OpenedBox:
    tier: str                  # what the label says
    box_type: str
    opened_at: str             # what the contents were rolled at
    items: List[str]
    note: str = ""
    gold: int = 0              # none on Floor 1
    floor: int = 1
    item_tiers: List[str] = field(default_factory=list)   # one per item, headline first
    outcome: str = ""          # a Fan box's vote: good, neutral or bad
    threat: str = ""           # what a Benefactor box was prepared for
    sponsor_note: str = ""     # who sent a Benefactor box, and what they said
    # The kind of each item, for pricing; "" for something no shop would buy.
    item_pools: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.tier} {self.box_type} Box"

    def sale_value(self) -> int:
        """What a general store would pay for everything in the box, before
        Charisma. Nothing before the Third Floor, where shops open."""
        if self.floor < SHOP_RULES["from_floor"]:
            return 0
        total = sum(store_pays(t, p, self.floor)
                    for t, p in zip(self.item_tiers, self.item_pools) if p)
        # Rounded like the prices it adds up: "about 14,342" is false precision.
        return _two_figures(total) if total else 0

    def markdown(self) -> str:
        """The box on one line, its items grouped under the tier each rolled at:
        a plain potion or tool says nothing about its own tier, and a DM handing
        it out needs to know. A Fan box groups under the vote instead."""
        note = f" *({self.note})*" if self.note else ""
        if self.outcome:
            head = self.outcome.title()
            if self.outcome == "good":
                head += f", {self.opened_at}"
            body = f"{head}: " + "; ".join(self.items)
        elif self.item_tiers:
            groups = {}
            for tier, item in zip(self.item_tiers, self.items):
                groups.setdefault(tier, []).append(item)
            body = ". ".join(f"{tier}: " + "; ".join(items) for tier, items in groups.items())
        else:
            body = "; ".join(self.items)           # a bounty, not a set of items
        gold = f". **{self.gold:,} gold**" if self.gold else ""
        worth = self.sale_value()
        sale = f" A general store would pay about {worth:,} gold for the lot." if worth else ""
        # The sponsor's note goes last: it ends in a quote with its own stop.
        sponsor = f" {self.sponsor_note}" if self.sponsor_note else ""
        return f"- **{self.label}**{note} — {body}{gold}.{sale}{sponsor}"


def benefactor_threats() -> List[str]:
    """The threats a sponsor can prepare a Benefactor box for, in bank order."""
    return list(LOOT_BOXES["benefactor"]["threats"])


@dataclass
class ThreatSign:
    threat: str            # a key find_threat accepts
    label: str
    count: int             # how many lines of the floor point at it
    example: str           # one of them, trimmed around the word that matched


# Sections every floor carries whatever is on it. Scanning them would find the same
# threats everywhere -- the loot section lists all twelve by name.
_SHARED_SECTIONS = ("## Other Crawlers", "## Achievements", "## Bestiary",
                    "## What's Switched On", "## Shops and Prices", "## Loot Boxes",
                    "## Crawler Stats")


def floor_body(markdown: str) -> str:
    """A write-up cut off before the first section every floor shares."""
    cut = len(markdown)
    for head in _SHARED_SECTIONS:
        at = markdown.find("\n" + head)
        if at != -1:
            cut = min(cut, at)
    return markdown[:cut]


def _threat_patterns() -> dict:
    """One pattern per threat. Kept for callers that want to look at a single one."""
    return {key: re.compile(r"\b(?:" + "|".join(re.escape(w) for w in t["signs"]) + r")\b",
                            re.IGNORECASE)
            for key, t in LOOT_BOXES["benefactor"]["threats"].items()}


_WORD = re.compile(r"\w+")


def _sign_index():
    """Which threats each sign belongs to, and a pattern for the few signs that
    are more than one word.

    Single words are looked up in a dict, one token at a time. A regex of all
    250-odd signs tries every one of them at every character, and running one
    pattern per threat over every line made the scan as slow as building the
    floor. Tokens split on \\w+, which is exactly where the old \\b boundaries
    fell, so "Rot-Touched" still yields "rot"."""
    owners = {}
    for key, t in LOOT_BOXES["benefactor"]["threats"].items():
        for word in t["signs"]:
            owners.setdefault(word.lower(), []).append(key)
    phrases = sorted((w for w in owners if not _WORD.fullmatch(w)), key=len, reverse=True)
    phrase_re = (re.compile(r"\b(?:" + "|".join(re.escape(p) for p in phrases) + r")\b",
                            re.IGNORECASE) if phrases else None)
    return owners, phrase_re


def threat_suggestions(text: str, top: int = 3) -> List[ThreatSign]:
    """The threats a floor's own text points to, strongest first.

    Counts lines rather than words, so a boss whose mechanic says "fire" three
    times is one sign, and a floor with fire in four different places is four.
    Pass a floor's body (floor_body) rather than its whole write-up. Nothing here
    is rolled: the same floor always suggests the same threats."""
    threats = LOOT_BOXES["benefactor"]["threats"]
    owners, phrase_re = _sign_index()
    hits = {key: [] for key in threats}
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line.strip():
            continue
        found = {}                                  # threat -> its matches on this line
        matches = [m for m in _WORD.finditer(line) if m.group(0).lower() in owners]
        if phrase_re is not None:
            matches += list(phrase_re.finditer(line))
        matches.sort(key=lambda m: m.start())
        for m in matches:
            if _inside_a_name(line, m):
                continue
            for key in owners[m.group(0).lower()]:
                found.setdefault(key, []).append(m)
        for key, ms in found.items():
            hits[key].append((len(ms), line, ms[0].start(), ms[0].end()))
    # One stray word is not a theme; it takes a few to mean the floor is about it.
    least = LOOT_BOXES["benefactor"]["min_signs"]
    ranked = sorted((k for k in threats if len(hits[k]) >= least),
                    key=lambda k: (-len(hits[k]), k))
    out = []
    for key in ranked[:top]:
        # The example is the line that says it most, which tends to be the most
        # specific one: a hazard spelled out rather than a map key's "~ = river".
        _, line, start, end = max(hits[key], key=lambda h: h[0])
        out.append(ThreatSign(key, threats[key]["label"], len(hits[key]),
                              _excerpt(line, start, end)))
    return out


_NAME_WORD = re.compile(r"[A-Z][\w'’-]*")


def _inside_a_name(line: str, m) -> bool:
    """A capitalised match straight after another capitalised word is part of a
    name: the warlord Peregrine Ember, the boss The Burning Goblin, the Ossuary
    Water Tower. Those say nothing about the floor. One after a label or a comma
    -- "Mob: Fire Changelings", "Air, Land, Sea" -- still counts.

    Only the one word before the match is looked at. Searching the whole start
    of the line for it, as a regex anchored at the end does, made the scan as
    slow as building the floor."""
    if not m.group(0)[0].isupper():
        return False
    before = line[:m.start()]
    if not before[-1:].isspace():
        return False
    words = before.split()
    return bool(words) and bool(_NAME_WORD.fullmatch(words[-1]))


def _excerpt(line: str, start: int, end: int, width: int = 48) -> str:
    """The words around a match, without the line's markdown."""
    lo, hi = max(0, start - width), min(len(line), end + width)
    snippet = line[lo:hi]
    # Headings and list markers are layout, not evidence -- and a "###" inside a
    # table cell reads as a stray heading.
    snippet = re.sub(r"[*_`]", "", snippet).lstrip("# ").strip(" -|")
    snippet = re.sub(r"\s+", " ", snippet)
    return ("…" if lo > 0 else "") + snippet + ("…" if hi < len(line) else "")


def find_threat(name: str) -> str:
    """A threat key from what a DM typed: "fire", "Fire", or its label, "the
    undead". Raises MapgenError naming the choices when nothing matches."""
    wanted = name.strip().lower()
    for key, t in LOOT_BOXES["benefactor"]["threats"].items():
        if wanted in (key, t["label"].lower()):
            return key
    raise MapgenError(f"no threat called {name!r}; a sponsor can prepare for: "
                      + ", ".join(benefactor_threats()))


def fan_odds(liked: float) -> dict:
    """The audience votes twice. Two likes is good, two dislikes bad, a split
    neutral, so at 70% liked the odds are 49% good, 42% neutral and 9% bad, and
    only a crowd that loved it (100%) or hated it (0%) is ever certain."""
    if not 0 <= liked <= 100:
        raise MapgenError(f"how much the audience liked it is 0 to 100 (got {liked})")
    p = liked / 100
    return {"good": p * p, "neutral": 2 * p * (1 - p), "bad": (1 - p) * (1 - p)}


class LootRoller:
    """Opens loot boxes on demand, with no floor involved.

    Boxes are generated apart from the map so that what a floor suggests -- "a
    Silver Boss Box" -- is a suggestion: a DM can hand it out as it stands, or
    make it Gold, or make it a Weapon box, and only then roll it. The decks live
    as long as the roller does, so a session of rolls works through each bank
    before it repeats anything. Seeded, a roller reproduces its whole sequence."""

    def __init__(self, seed: Optional[int] = None):
        self.seed = seed if seed is not None else random.randrange(1_000_000)
        self.rng = random.Random(f"boxes|{self.seed}")
        self._decks = {}

    def _deck(self, key: str, items) -> Deck:
        # Made on first use. The order decks are made in depends only on the order
        # of the rolls, so a seeded roller replays exactly.
        if key not in self._decks:
            self._decks[key] = Deck(self.rng, items)
        return self._decks[key]

    def _item(self, pool: str, quality: str) -> str:
        items = LOOT_BOXES["items"][pool]
        if isinstance(items, dict):
            band = LOOT_BANDS[quality]
            return self._deck(f"{pool}:{band}", items[band]).draw()
        return self._deck(pool, items).draw()

    def _effects(self, quality: str, pool: str, count: int) -> List[str]:
        kind = _fit_kind(pool)
        deck = self._deck(f"fx:{quality}:{kind}",
                          [text for text, fits in LOOT_BOXES["effects"][quality]
                           if fits in kind.split("+")])
        out = []
        while len(out) < count:                   # distinct even across a reshuffle
            effect = deck.draw()
            if effect not in out:
                out.append(effect)
        return out

    def _make(self, pool: str, quality: str, headline: bool,
              counter: Optional[str] = None) -> str:
        """One item from a pool, at a quality: enchanted if the pool takes it and
        the quality has any to give, named if it is a Legendary or Celestial
        headline. Plain pools draw from the quality's band instead. `counter` is
        a Benefactor box's threat counter, and takes one of the enchantment
        slots rather than adding one."""
        lb = LOOT_BOXES
        item = self._item(pool, quality)
        fx_count = lb["effects_per_item"][quality]
        if fx_count and pool in lb["enchantable"]:
            effects = self._effects(quality, pool, fx_count - (1 if counter else 0))
            if counter:
                effects = [counter] + effects
            item = f"{item} ({'; '.join(effects)})"
            if headline and quality in ("Legendary", "Celestial"):
                name = self._deck("first", BOSS_FIRST).draw()
                epithet = self._deck("epithet", lb["epithets"]).draw()
                item = f"**{name}'s {epithet}**, {item}"
        return item

    def _weighted_pool(self, pools: List[str]) -> str:
        """A pool picked in proportion to its size, so every item is equally likely."""
        sizes = []
        for p in pools:
            bank = LOOT_BOXES["items"][p]
            sizes.append(sum(map(len, bank.values())) if isinstance(bank, dict) else len(bank))
        return self.rng.choices(pools, weights=sizes)[0]

    def _lesser_pool(self, main_pool: str) -> str:
        """Which pool a lesser item comes from, by LOOT_BOXES["secondary"]: the
        main item's own pool 15% of the time, other equipment 35%, consumables 50%.

        Equipment leaves out the main item's pool, so a Weapon box reaches another
        weapon only through the 15% -- counted twice, it would be closer to 27%.
        Within a category a pool is picked in proportion to its size, so every
        item in it is equally likely and the 16 pairs of shoes do not turn up as
        often as the 50 weapons."""
        sec = LOOT_BOXES["secondary"]
        kinds = list(sec["weights"])
        kind = self.rng.choices(kinds, weights=[sec["weights"][k] for k in kinds])[0]
        if kind == "same":
            return main_pool
        pools = (sec["equip_pools"] if kind == "equip" else sec["consumable_pools"])
        pools = [p for p in pools if p != main_pool] or pools
        return self._weighted_pool(pools)

    def _gold(self, tier: str, floor: int) -> int:
        lb = LOOT_BOXES
        if floor < lb["gold"]["from_floor"]:
            return 0
        lo, hi = lb["gold"]["roll"]
        return self.rng.randint(lo, hi) * floor ** lb["gold"]["tier_power"][tier]

    def open(self, tier: str, box_type, floor: int = 1, *,
             threat: Optional[str] = None, liked: Optional[float] = None) -> OpenedBox:
        """Open one box. `box_type` is a type dict or a name; unknown names are
        achievement boxes and open from the Adventurer pools. `floor` sets the
        gold: none on Floor 1, then (1-10) x floor ^ tier.

        Exactly one item is at the box's tier, and it is of the box's type: a
        Celestial Weapon box is one god-tier weapon, not four. Every other item
        is lesser, and rolls its own tier from LOOT_BOXES["contents"] -- a
        Celestial box's three from Gold to Legendary, a Legendary box's two from
        Silver to Platinum, a Gold box's four from Bronze or Silver. A Benefactor
        box opens three tiers above its label, which is how the books put a
        Bronze one ahead of a Gold Adventurer box -- but the promotion stops at
        Legendary, and never demotes a box already above it.

        `threat` prepares a Benefactor box for what its sponsor can see coming;
        `liked` is how much the audience liked whatever earned a Fan box, 0-100.
        Each is ignored by every other type."""
        lb = LOOT_BOXES
        if isinstance(box_type, str):
            box_type = find_box_type(box_type)
        if tier not in LOOT_BOX_TIERS:
            raise MapgenError(f"{tier!r} is not a tier: {', '.join(LOOT_BOX_TIERS)}")
        if floor < 1:
            raise MapgenError(f"floor must be 1 or deeper (got {floor})")
        if box_type.get("special"):
            # A Bounty or Venison box is gold already: the bounty, not a roll.
            return OpenedBox(tier, box_type["name"], tier, [box_type["holds"]],
                             box_type.get("note", ""), floor=floor)
        if box_type["name"] == "Fan":
            return self._open_fan(tier, box_type, floor, 50 if liked is None else liked)
        idx = LOOT_BOX_TIERS.index(tier)
        benefactor = box_type["name"] == "Benefactor"
        if benefactor:
            cap = LOOT_BOX_TIERS.index(lb["benefactor_cap"])
            idx = max(idx, min(idx + lb["benefactor_bonus"], cap))
        top = LOOT_BOX_TIERS[idx]
        pools = box_type["pools"]
        enchantable = [p for p in pools if p in lb["enchantable"]]
        contents = lb["contents"][top]
        prepared = find_threat(threat) if (benefactor and threat) else None
        counters = lb["benefactor"]["threats"][prepared] if prepared else None

        # The headline comes from a pool that can carry the tier, so a Gold box
        # always shows it is Gold instead of turning out to be all rope.
        lead = enchantable if enchantable and lb["effects_per_item"][top] else pools
        main_pool = self.rng.choice(lead)
        counter = None
        if counters and main_pool in lb["enchantable"]:
            band = LOOT_BANDS[top]
            counter = self._deck(f"counterfx:{prepared}:{band}",
                                 counters["effects"][band]).draw()
        items = [self._make(main_pool, top, headline=True, counter=counter)]
        tiers = [top]
        kinds = [main_pool]
        # Each lesser item rolls its own tier, evenly, from the box's list, and
        # its own kind: mostly support for the main item rather than more of it.
        # A prepared Benefactor box spends every lesser item on the threat: the
        # sponsor has seen what is coming and is not paying for anything else.
        lesser = []
        for _ in range(contents["extras"]):
            t = self.rng.choice(contents["extra_tiers"])
            if counters:
                band = LOOT_BANDS[t]
                item = self._deck(f"counter:{prepared}:{band}",
                                  counters["items"][band]).draw()
                # A counter is bought for one threat, so a shop prices it as an
                # oddity rather than as whatever it happens to look like.
                pool = "oddities"
            else:
                pool = self._lesser_pool(main_pool)
                item = self._make(pool, t, headline=False)
            lesser.append((t, item, pool))
        # Best first, so the line reads down the tiers; sorting after the rolls
        # changes nothing about what was rolled.
        lesser.sort(key=lambda entry: -LOOT_BOX_TIERS.index(entry[0]))
        tiers += [t for t, _, _ in lesser]
        items += [item for _, item, _ in lesser]
        kinds += [pool for _, _, pool in lesser]

        sponsor_note = ""
        if benefactor:
            sponsor = self._deck("sponsor", lb["benefactor"]["sponsors"]).draw()
            line = (self._deck(f"note:{prepared}", counters["notes"]).draw() if counters
                    else self._deck("note:any", lb["benefactor"]["untargeted_notes"]).draw())
            sponsor_note = f"From {sponsor}: \"{line}\""
        gold = self._gold(top, floor)
        note = box_type.get("note", "")
        if box_type.get("unknown"):
            note = "an achievement box; opened from the Adventurer pools"
        if prepared:
            note = (f"opened at {top}; prepared for {counters['label']}")
        return OpenedBox(tier, box_type["name"], top, items, note, gold=gold, floor=floor,
                         item_tiers=tiers, threat=prepared or "", sponsor_note=sponsor_note,
                         item_pools=kinds)

    def _open_fan(self, tier: str, box_type: dict, floor: int, liked: float) -> OpenedBox:
        """A Fan box is whatever the audience decided. Good is one useful thing at
        the box's tier; neutral is something harmless and probably at the
        crawler's expense; bad costs them, and costs more the bigger the box.
        Good and neutral boxes still carry the floor's gold; a bad one does not."""
        fan = LOOT_BOXES["fan"]
        odds = fan_odds(liked)
        outcome = self.rng.choices(list(odds), weights=list(odds.values()))[0]
        pool = ""                 # a joke or a punishment: nothing a shop would buy
        if outcome == "good":
            pool = self._weighted_pool(fan["good_pools"])
            item = self._make(pool, tier, headline=True)
        elif outcome == "neutral":
            item = self._deck("fan:neutral", fan["neutral"]).draw()
        else:
            band = LOOT_BANDS[tier]
            item = self._deck(f"fan:bad:{band}", fan["bad"][band]).draw()
        gold = 0 if outcome == "bad" else self._gold(tier, floor)
        note = (f"{liked:g}% liked it: {odds['good']:.0%} good, {odds['neutral']:.0%} "
                f"neutral, {odds['bad']:.0%} bad")
        return OpenedBox(tier, box_type["name"], tier, [item], note, gold=gold, floor=floor,
                         item_tiers=[tier], outcome=outcome, item_pools=[pool])

    def open_text(self, text: str, floor: int = 1, *, threat: Optional[str] = None,
                  liked: Optional[float] = None) -> OpenedBox:
        """Open a box written the way a write-up names it: "a Silver Boss Box"."""
        tier, box_type = parse_box(text)
        return self.open(tier, box_type, floor, threat=threat, liked=liked)


@dataclass
class BoxSuggestion:
    tier: str
    box_type: str
    kind: str          # what hands it out: "neighborhood boss", "quest", "achievement"
    source: str        # which one

    @property
    def label(self) -> str:
        return f"{self.tier} {self.box_type} Box"


def floor_bosses(fl) -> List[Boss]:
    """Every boss on a floor, wherever that floor keeps it."""
    out = []
    for attr in ("neighborhoods", "cells"):
        out += [c.boss for c in getattr(fl, attr, None) or [] if getattr(c, "boss", None)]
    for group in ("borough_bosses", "city_bosses"):
        out += list((getattr(fl, group, None) or {}).values())
    for q in getattr(fl, "quadrants", None) or []:
        if getattr(q, "castle", None):
            out += [q.castle.boss] + q.castle.guardians
    for attr in ("top_boss", "boss"):
        if getattr(fl, attr, None):
            out.append(getattr(fl, attr))
    return out


def floor_box_suggestions(fl) -> List[BoxSuggestion]:
    """The boxes a floor itself names: what each boss drops, what each quest pays,
    what each achievement listed on it awards. These are what the write-up hands
    a DM as starting points; nothing about them is rolled."""
    out = []
    for b in floor_bosses(fl):
        tier = boss_box(b.tier, fl.number).split()[0]
        out.append(BoxSuggestion(tier, "Boss", f"{b.tier} boss", b.name))
    for cell in getattr(fl, "cells", None) or []:
        for q in getattr(cell, "quests", None) or []:
            m = _BOX_RE.search(q.reward)
            if m:
                out.append(BoxSuggestion(m.group(1), m.group(2), "quest",
                                         f"{q.title}, {cell.name}"))
    # Floor 4 keeps its station quests on the floor, and a quest on every named
    # train.
    for q in getattr(fl, "quests", None) or []:
        m = _BOX_RE.search(q.reward)
        if m:
            out.append(BoxSuggestion(m.group(1), m.group(2), "quest", q.title))
    for t in getattr(fl, "named", None) or []:
        quest = getattr(t, "quest", None)
        m = quest and _BOX_RE.search(quest["reward"])
        if m:
            out.append(BoxSuggestion(m.group(1), m.group(2), "quest",
                                     f"{quest['title']}, {t.name}"))
    if getattr(fl, "neighborhoods", None):           # floors 1 and 2 list them
        achievements = ACHIEVEMENTS
    else:                                            # and floor 4 its own
        achievements = getattr(fl, "achievements", None) or []
    for name, _, reward in achievements:
        m = _BOX_RE.search(reward)
        if m:
            out.append(BoxSuggestion(m.group(1), m.group(2), "achievement", name))
    return out


def loot_box_section(fl, body: str = "") -> List[str]:
    """Loot boxes on this floor: the rules, the tiers, the types that exist yet,
    and the boxes the floor itself names as suggestions. Nothing here is rolled:
    a box is opened apart from the floor, after a DM has decided what it is."""
    floor = fl.number
    lb = LOOT_BOXES
    o = ["## Loot Boxes", ""]
    o += [f"- {rule}" for rule in lb["rules"]]
    o.append("")
    if getattr(fl, "neighborhoods", None):
        o.append("Neighborhoods farther from the entry skew toward higher tiers; each one "
                 "lists the tier its own boxes run at.")
        o.append("")
    gold = lb["gold"]
    with_gold = floor >= gold["from_floor"]
    if with_gold:
        o.append(f"| Tier | Items | Gold on Floor {floor} | What it holds |")
        o.append("|---|---:|---:|---|")
    else:
        o.append("| Tier | Items | What it holds |")
        o.append("|---|---:|---|")
    lo, hi = gold["roll"]
    for tier, desc in lb["tier_guide"]:
        n = 1 + lb["contents"][tier]["extras"]
        if with_gold:
            scale = floor ** gold["tier_power"][tier]
            o.append(f"| {tier} | {n} | {lo * scale:,}–{hi * scale:,} | {desc} |")
        else:
            o.append(f"| {tier} | {n} | {desc} |")
    o.append("")
    if not with_gold:
        o.append(f"Boxes hold no gold until Floor {gold['from_floor']}.")
        o.append("")
    o.append("| Box | Earned for |")
    o.append("|---|---|")
    for t in box_types_for(floor):
        note = f" ({t['note']})" if t.get("note") else ""
        o.append(f"| {t['name']} | {t['earned']}{note} |")
    o.append("")
    later = [t for t in lb["types"] if t["from_floor"] > floor]
    if later:
        o.append("Not on this floor yet: " + ", ".join(
            f"{t['name']} (from Floor {t['from_floor']})" for t in later) + ".")
        o.append("")

    suggestions = floor_box_suggestions(fl)
    if suggestions:
        o.append("### What this floor hands out")
        o.append("")
        o.append("Every box this write-up names, grouped. Treat them as suggestions: "
                 "hand one out as it stands, or change its tier or type first.")
        o.append("")
        o.append("| Box | How many | From |")
        o.append("|---|---:|---|")
        groups = {}
        for sg in suggestions:
            key = (LOOT_BOX_TIERS.index(sg.tier), sg.label, sg.kind)
            groups.setdefault(key, []).append(sg)
        for (_, label, kind), members in sorted(groups.items()):
            src = (f"{kind}: {members[0].source}" if len(members) == 1
                   else f"{len(members)} {kind}{'es' if kind.endswith('s') else 's'}")
            o.append(f"| {label} | {len(members)} | {src} |")
        o.append("")

    benefactor_from = next(t for t in lb["types"] if t["name"] == "Benefactor")["from_floor"]
    threats = threat_suggestions(body) if floor >= benefactor_from else []
    if threats:
        o.append("### What a sponsor would see coming")
        o.append("")
        o.append("The threats this floor's own bosses, mobs and hazards point to, "
                 "strongest first. A sponsor watching would prepare a Benefactor box for "
                 "one of them; pick whichever fits what the party is about to walk into.")
        o.append("")
        o.append("| Prepare for | Signs | For example |")
        o.append("|---|---:|---|")
        for t in threats:
            o.append(f"| `--for {t.threat}` ({t.label}) | {t.count} | {t.example.replace('|', '/')} |")
        o.append("")

    o.append("### Opening a box")
    o.append("")
    o.append("Boxes are rolled on their own, not with the floor, so a DM decides what "
             "a box is before anything is in it, and rolling one never changes the map. "
             "Paste any box from this write-up, as written or edited:")
    o.append("")
    o.append("```")
    o.append(f'python dcc_mapgen.py --open-box "a Silver Boss Box" --floor {floor}')
    o.append(f'python dcc_mapgen.py --open-box "Gold Weapon" --count 3 --floor {floor}')
    if floor >= next(t for t in lb["types"] if t["name"] == "Benefactor")["from_floor"]:
        lead = threats[0].threat if threats else "fire"
        o.append(f'python dcc_mapgen.py --open-box "Gold Benefactor" --for {lead} --floor {floor}')
    o.append(f'python dcc_mapgen.py --open-box "Silver Fan" --liked 70 --floor {floor}')
    o.append("```")
    o.append("")
    o.append("A Fan box is the audience's call: `--liked` is how much they liked whatever "
             "earned it, and sets the odds of a good, neutral or bad box. A Benefactor box "
             "can be prepared with `--for` for what its sponsor can see coming: "
             + ", ".join(benefactor_threats()) + ".")
    o.append("")
    o.append("`--floor` sets the gold, so keep it matching the floor the box was earned on. "
             "In the desktop app, the **Loot Boxes** tab lists this floor's boxes to "
             "start from and fills in the floor. Boxes opened together, or in one app "
             "session, work through the whole bank before repeating an item.")
    o.append("")
    return o


def _mana_regen_for(score: int) -> str:
    """The regeneration row that applies at a score, which is the last row at or
    below it -- the table is sparse on purpose."""
    best = CRAWLER_STATS["mana_regen"][0][1]
    for at, text in CRAWLER_STATS["mana_regen"]:
        if score >= at:
            best = text
    return best


def systems_on(floor: int) -> List[dict]:
    """Every system on for this floor: switched on here or earlier, not yet
    switched off, and not something that only happened on an earlier floor."""
    return [s for s in SYSTEMS if s["from"] <= floor
            and floor <= s.get("until", 99) and not (s.get("once") and s["from"] < floor)]


def systems_section(floor: int) -> List[str]:
    """What's switched on: the dungeon's systems arrive a floor at a time, and a
    DM running Floor 6 should not have to remember which floor brought guilds."""
    new = [s for s in SYSTEMS if s["from"] == floor]
    off = [s for s in SYSTEMS if s.get("until") == floor - 1]
    still = [s for s in systems_on(floor) if s["from"] < floor]
    upcoming = [s for s in SYSTEMS if s["from"] == floor + 1]
    # Names are kept in sentence case for the lists ("the HUD, gold, Game
    # Guides"), and capitalized where one starts a bullet.
    def head(s):
        return s["name"][0].upper() + s["name"][1:]

    o = ["## What's Switched On", "",
         "The dungeon's systems arrive a floor at a time, and a few go away again.", ""]
    if new:
        o += [f"**New on Floor {floor}:**", ""]
        o += [f"- **{head(s)}:** {s['text']}." for s in new]
        o.append("")
    if off:
        o += ["**Switched off:**", ""]
        o += [f"- **{head(s)}:** {s['off']}." for s in off]
        o.append("")
    if still:
        o += ["**Still on from earlier floors:**", ""]
        for f in sorted({s["from"] for s in still}):
            names = [s["name"] for s in still if s["from"] == f]
            o.append(f"- Floor {f}: {', '.join(names)}.")
        o.append("")
    if upcoming and floor + 1 in FLOORS:
        o.append(f"**Next floor:** {', '.join(s['name'] for s in upcoming)}.")
        o.append("")
    return o


# -- shops and prices -------------------------------------------------------------

def _two_figures(x: float) -> int:
    """A price as a shop would mark it: two significant figures, never under 1."""
    return max(1, int(round(float(f"{x:.2g}"))))


def _kind(pool: str) -> dict:
    return next(k for k in SHOP_RULES["kinds"] if k["pool"] == pool)


def list_price(tier: str, pool: str, floor: int) -> int:
    """What a specialist charges for an item of this tier and kind on this floor:
    about the gold in a box of the same tier, so an item found is worth about the
    gold it could have been."""
    power = LOOT_BOXES["gold"]["tier_power"][tier]
    return _two_figures(SHOP_RULES["base"] * _kind(pool)["mult"] * floor ** power)


def store_pays(tier: str, pool: str, floor: int) -> int:
    """What a general store pays a crawler for one, before Charisma."""
    return _two_figures(list_price(tier, pool, floor) * SHOP_RULES["general_pays"])


def shop_purse(floor: int) -> int:
    p = SHOP_RULES["purse"]
    return _two_figures(p["per"] * floor ** p["power"])


def bed_price(floor: int) -> int:
    b = SHOP_RULES["bed"]
    return _two_figures(b["per"] * floor ** b["power"])


def shopkeeper_levels(floor: int) -> Tuple[int, int]:
    k = SHOP_RULES["shopkeeper_level"]
    return k["per_floor"] * floor, k["per_floor"] * floor + k["spread"]


def shops_section(floor: int) -> List[str]:
    """Where to buy, where to sell, and what things cost on this floor. Nothing
    before the Third Floor, where marketplaces open."""
    s = SHOP_RULES
    if floor < s["from_floor"]:
        return []
    knobs = CRAWLER_STATS["knobs"]
    lo, hi = shopkeeper_levels(floor)
    specialists = list(dict.fromkeys(k["shop"] for k in s["kinds"]))
    tiers = LOOT_BOX_TIERS
    o = ["## Shops and Prices", "",
         f"Marketplaces open on the Third Floor. These are Floor {floor}'s prices; "
         f"everything costs more a floor down, the same way the gold in a loot box "
         f"grows.", "",
         f"- **General stores** are everywhere and will buy anything, but they sell at "
         f"{_COUNT_WORD.get(s['general_markup'], s['general_markup'])} times a "
         f"specialist's price. Sell to them; buy elsewhere.",
         f"- **Specialists** ({', '.join(specialists)}) are only in settlements. They "
         f"sell at list price and do not buy from crawlers.",
         f"- **Selling:** a general store pays {s['general_pays']:.0%} of list, out of a "
         f"purse of {shop_purse(floor):,} gold on this floor that refills overnight. An "
         f"empty purse buys nothing.",
         f"- **Charisma:** each point takes {knobs['discount_per']}% off what a crawler "
         f"pays and adds {knobs['discount_per']}% to what a store pays them, up to "
         f"{knobs['discount_cap']}%.",
         f"- **Shopkeepers** here are level {lo}–{hi}. They cannot lie about an item, "
         f"but they exaggerate; a crawler whose Charisma is higher than the shopkeeper's "
         f"level hears it straight.", "",
         f"**List prices on Floor {floor}**, in gold, as a specialist charges them. An "
         f"item's tier is the one it rolled in its box.", "",
         "| Kind | Sold by | " + " | ".join(tiers) + " |",
         "| --- | --- | " + " | ".join("---:" for _ in tiers) + " |"]
    for k in s["kinds"]:
        o.append(f"| {k['name']} | {k['shop']} | "
                 + " | ".join(f"{list_price(t, k['pool'], floor):,}" for t in tiers) + " |")
    o += ["", "**Services:**", "",
          f"- A bed in a safe room: {bed_price(floor):,} gold a night (free on the first "
          f"two floors)."]
    o += [f"- {sv['what']}: {sv['price']}." for sv in s["services"] if sv["from"] <= floor]
    o.append("")
    return o


# -- the bestiary: mobs statted from their level ----------------------------------

STAT_ORDER = ("STR", "DEX", "CON", "INT", "CHA")


@dataclass
class MobBuild:
    """One mob or boss, statted the way a crawler is but on fewer points a level."""
    name: str
    where: str
    level: int
    band: Tuple[int, int]
    tier: str                  # "mob", or the boss tier
    kind: str                  # brute, skirmisher, ...
    stats: Dict[str, int]
    poor: str = ""             # how it is badly built, if it is

    @property
    def hp(self) -> int:
        return self.stats["CON"] * CRAWLER_STATS["knobs"]["hp_per_con"]

    @property
    def mp(self) -> int:
        return self.stats["INT"]                    # mana is INT, as for a crawler

    @property
    def melee(self) -> int:
        return self.stats["STR"] // CRAWLER_STATS["knobs"]["attack_per"]

    @property
    def tricks(self) -> int:
        """How many times it can use its trick on purpose before it runs dry."""
        return self.mp // MOB_BUILDS["trick_cost"]

    @property
    def hit(self) -> Tuple[int, str]:
        """Its biggest single hit, and what is behind it: a full-strength blow off
        its STR, or its trick off its INT if that is the bigger and it has the MP
        for one use. Ties go to the blow."""
        per = MOB_BUILDS["hit_per_point"]
        blow = self.stats["STR"]
        trick = self.stats["INT"] if self.tricks else 0
        return (trick * per, "trick") if trick > blow else (blow * per, "STR")

    @property
    def points(self) -> int:
        return sum(self.stats.values())

    def crawlers_at(self, level: int) -> float:
        """How many crawlers of this level its stat points are worth."""
        return self.points / crawler_points(level)

    def one_shots(self, level: int, half: bool = False) -> bool:
        """Whether its hit kills a crawler of this level outright."""
        return self.hit[0] >= crawler_hp(level, half)

    @property
    def even_for(self) -> int:
        """The crawler level with as many stat points: what it matches point for
        point, before a crawler's gear and class and before its own trick."""
        spent = sum(self.stats.values()) - MOB_BUILDS["base"] * len(STAT_ORDER)
        return max(1, round(spent / CRAWLER_STATS["knobs"]["points_per_level"]))


def crawler_points(level: int) -> int:
    """A crawler's stat points at a level: 4 in each stat, plus their points a level."""
    return (MOB_BUILDS["base"] * len(STAT_ORDER)
            + CRAWLER_STATS["knobs"]["points_per_level"] * level)


def crawler_hp(level: int, half: bool = False) -> int:
    """A crawler's health at a level: CON x hp_per_con, with CON at 4 plus a fifth
    of their points (spread evenly) or, with half, half of them -- the build of a
    veteran who has learned where the points go."""
    k = CRAWLER_STATS["knobs"]
    spent = k["points_per_level"] * level
    con = MOB_BUILDS["base"] + (spent // 2 if half else spent // len(STAT_ORDER))
    return con * k["hp_per_con"]


def lives_through(hit: int, half: bool = False) -> int:
    """The lowest crawler level that lives through a hit, with HP to spare."""
    level = 1
    while crawler_hp(level, half) <= hit:
        level += 1
    return level


def party_levels(floor: int, beasts: List["MobBuild"]) -> Tuple[int, int, bool]:
    """The two crawler levels a boss is measured against on a floor, and whether
    the first was read from the floor: an ordinary crawler, where the floor's
    ordinary mobs top out, and one on the leaderboard.

    "Top out" is the median of the mobs' band tops, not the highest: Floor 4's
    mimic spawn at the very end of the line would otherwise set the level for
    every boss on the floor. A floor with no ordinary mobs takes it from the bank."""
    ordinary, leader = MOB_BUILDS["party_levels"][str(floor)]
    tops = sorted(b.band[1] for b in beasts if b.tier == "mob")
    if tops:
        ordinary = tops[len(tops) // 2]
    return ordinary, max(leader, ordinary + 1), bool(tops)


def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z]+", text.lower())


def mob_kind(name: str, default: Optional[str] = None,
             sentence: bool = False) -> Tuple[str, List[float]]:
    """A mob's type from its words. In a name the head noun comes last and decides
    first -- "Goblin Shamankas" are shamans, "Tunnel Trolls" trolls -- then the
    rest; in a sentence describing a boss, the first word that names a type wins,
    so "a stone effigy animated by the spite of the librarians" is an effigy. A
    word matches a type if it starts with one of that type's words, types tried
    in MOB_BUILDS order. Anything unmatched is the default."""
    words = _tokens(name)
    if not sentence and words:
        words = [words[-1]] + words[:-1]
    for w in words:
        for t in MOB_BUILDS["types"]:
            if any(w.startswith(k) for k in t["words"]):
                return t["type"], list(t["weights"])
    kind = default or MOB_BUILDS["default_type"]
    match = next((t for t in MOB_BUILDS["types"] if t["type"] == kind), None)
    return kind, list(match["weights"] if match else MOB_BUILDS["default_weights"])


def build_mob(name: str, level: int, tier: str = "mob", *, kind_from: str = "",
              description: str = "", where: str = "", band: Optional[Tuple[int, int]] = None,
              default_kind: Optional[str] = None, sentence: bool = False) -> MobBuild:
    """Stat a mob or boss from its level.

    Every stat starts at 4, as an average adult human's does; then the mob gets
    MOB_BUILDS["tier_points"][tier] points a level -- 1 for an ordinary mob, 2 for
    a neighborhood boss and so on up, against a crawler's 3 -- spread by its type
    (from `kind_from`, its noun) and leant by the words of `description`. About
    one in eight is poorly built instead. Seeded by the mob itself, so the same
    mob at the same level is always the same build, and statting a floor never
    moves anything else on it."""
    mb = MOB_BUILDS
    rng = random.Random(f"build|{name}|{level}|{tier}")
    kind, weights = mob_kind(kind_from or name, default_kind, sentence)
    base = {s: mb["base"] for s in STAT_ORDER}
    poor = None
    if rng.randrange(mb["poorly_built_in"]) == 0:
        poor = rng.choice(mb["poor_builds"])
        weights = list(poor["weights"])
        for s in poor["dump"]:
            moved = base[s] - 1
            base[s] = 1
            weights[STAT_ORDER.index(s)] = 0       # a dumped stat stays dumped
            if poor["main"]:
                base[poor["main"]] += moved
    else:
        words = set(_tokens(description))
        for key, lean in mb["leanings"].items():
            if key in words or key + "s" in words:
                for s, factor in lean.items():
                    weights[STAT_ORDER.index(s)] *= factor
    weights = [w * rng.uniform(0.85, 1.15) for w in weights]
    points = mb["tier_points"].get(tier, mb["tier_points"]["neighborhood"]) * max(1, level)
    # Largest remainder, so the points add up exactly.
    total_w = sum(weights)
    shares = [points * w / total_w for w in weights]
    whole = [int(s) for s in shares]
    for i in sorted(range(len(shares)), key=lambda i: shares[i] - whole[i],
                    reverse=True)[:points - sum(whole)]:
        whole[i] += 1
    stats = {s: base[s] + whole[i] for i, s in enumerate(STAT_ORDER)}
    return MobBuild(name, where, level, band or (level, level), tier, kind, stats,
                    poor["name"] if poor else "")


def _middle(band) -> int:
    lo, hi = band
    return (lo + hi) // 2


def floor_bestiary(fl) -> List[MobBuild]:
    """Every mob and boss a floor names with a level, statted, in the order the
    write-up meets them. A mob with a band is statted at the middle of it."""
    out = []

    def mob(m, band, where):
        if m is None:
            return
        if isinstance(m, str):
            out.append(build_mob(m, _middle(band), where=where, band=tuple(band),
                                 description=m))
        else:
            out.append(build_mob(m.name, _middle(band), kind_from=m.noun,
                                 description=f"{m.adj} {m.quirk} {m.description}",
                                 where=where, band=tuple(band)))

    def boss(b, where, noun=""):
        if b is None:
            return
        out.append(build_mob(b.name, b.level, b.tier, kind_from=noun or b.form,
                             description=f"{b.form} {b.mechanic}", where=where,
                             default_kind="brute", sentence=not noun))

    for nb in getattr(fl, "neighborhoods", None) or []:             # floors 1-2
        mob(nb.mob, nb.level_range, f"neighborhood {nb.label}")
        boss(nb.boss, f"neighborhood {nb.label}", nb.mob.noun)
    for name, b in (getattr(fl, "borough_bosses", None) or {}).items():
        boss(b, f"borough {name}")
    for name, b in (getattr(fl, "city_bosses", None) or {}).items():
        boss(b, f"city {name}")
    if getattr(fl, "top_boss", None):
        boss(fl.top_boss, getattr(fl, "top_tier_name", "") or "the top")
    for c in getattr(fl, "cells", None) or []:                     # floors 3 and 6
        if getattr(c, "mob", None):
            mob(c.mob, c.level_range, f"region {c.label}")
            if getattr(c, "second_mob", None):
                mob(c.second_mob, c.level_range, f"region {c.label}")
            boss(getattr(c, "boss", None), f"region {c.label}", c.mob.noun)
    for q in getattr(fl, "quadrants", None) or []:                 # floor 5
        mob(q.mob, q.level_range, f"the {q.name} quadrant")
        for om in getattr(q, "other_mobs", None) or []:
            mob(om.mob, om.level_range, f"the {q.name} quadrant")
        if getattr(q, "white", None):
            w = q.white
            out.append(build_mob(w.name, _middle(w.level_range), description=w.hazard,
                                 where=f"the {q.name} quadrant, white-tagged",
                                 band=tuple(w.level_range)))
        castle = getattr(q, "castle", None)
        boss(castle.boss if castle else None, f"the {q.name} castle", q.mob.noun)
        for g in castle.guardians if castle else []:
            boss(g, f"the {q.name} quadrant", q.mob.noun)
    for z in getattr(fl, "zones", None) or []:                     # floor 8
        mob(z.mob, z.level_range, f"zone {z.label}")
    for name, band in getattr(fl, "forest_mobs", None) or []:      # floor 9
        mob(name, band, "the forest")
    if getattr(fl, "lines", None):                                 # floor 4
        for ln in fl.lines:
            mob(ln.line_mob, ln.mob_levels, f"the {ln.color} Line")
        for gs in fl.ghoul_stations[1:]:       # 12 is the janitors', statted below
            g = gs["ghoul"]
            out.append(build_mob(g["name"], _middle(g["level"]), where=f"station {gs['number']}",
                                 band=tuple(g["level"]), description=g["does"]))
            if gs["merged"]:
                m = gs["merged"]
                out.append(build_mob(m["name"], _middle(m["level"]), m["tier"].split()[0],
                                     where=f"station {gs['number']}", band=tuple(m["level"]),
                                     description=m["does"], default_kind="brute"))
        sup, spawn = fl.supplier, fl.mimic_spawn
        out.append(build_mob(sup["name"], _middle(sup["level"]), where="every exit cavern",
                             band=tuple(sup["level"]), description=sup["does"]))
        c4 = FLOOR4_CFG
        out.append(build_mob("Station Mimic", c4["mimic_level"], "city",
                             where=f"station {c4['mimic_station']}",
                             description="a station-sized mouth", default_kind="brute"))
        out.append(build_mob(spawn["name"].capitalize(), _middle(spawn["level"]),
                             where=f"station {c4['mimic_station']}", band=tuple(spawn["level"]),
                             description=spawn["does"]))
    if getattr(fl, "janitor", None):
        mob(fl.janitor, (fl.janitor_level, fl.janitor_level), "the janitors")
    if getattr(fl, "boss", None) and isinstance(fl.boss, Boss):    # floor 11
        boss(fl.boss, "the arena")
    return out


def bestiary_section(fl) -> List[str]:
    """The floor's mobs and bosses with stat lines, for a DM who wants numbers."""
    beasts = floor_bestiary(fl)
    if not beasts:
        return []
    mb, k = MOB_BUILDS, CRAWLER_STATS["knobs"]
    tp = mb["tier_points"]
    o = ["## Bestiary", "",
         f"Every mob and boss on this floor, statted the way a crawler is but on fewer "
         f"points: each stat starts at {mb['base']}, then a crawler gains "
         f"{k['points_per_level']} points a level, an ordinary mob {tp['mob']}, a "
         f"neighborhood boss {tp['neighborhood']}, a borough boss {tp['borough']}, a city "
         f"boss {tp['city']}, and so on up. A mob with a level band is shown at the middle "
         f"of it; each level either way moves its points.", "",
         f"HP is CON x {k['hp_per_con']} and MP is INT, as for a crawler. Melee adds 1 per "
         f"{k['attack_per']} STR. A mob's MP is fuel for its trick: each use on purpose costs "
         f"{mb['trick_cost']}, and the MP comes back at a crawler's rate for the same INT. "
         f"What it simply is — exploding, regenerating, hunting by sound — costs nothing. "
         f"*Even for* is the crawler level with as many points to spend: a fair fight on "
         f"paper, before a crawler's gear and class, and before the trick, which is what "
         f"usually decides it.", "",
         "| Mob | Where | Level | Build | STR | DEX | CON | INT | CHA | HP | MP | Melee | Even for |",
         "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for b in beasts:
        lo, hi = b.band
        level = f"{b.level}" if lo == hi else f"{b.level} ({lo}–{hi})"
        kind = b.kind + (", poorly built" if b.poor else "")
        if b.tier != "mob":
            kind = f"{b.tier} boss, {kind}"
        s = b.stats
        o.append(f"| {b.name} | {b.where} | {level} | {kind} | " +
                 " | ".join(str(s[x]) for x in STAT_ORDER) +
                 f" | {b.hp:,} | {b.mp} | +{b.melee} | {b.even_for} |")
    poor = [b for b in beasts if b.poor]
    if poor:
        o += ["", "**Poorly built**, and it shows:", ""]
        o += [f"- **{b.name}:** {b.poor}." for b in poor]
    bosses = [b for b in beasts if b.tier != "mob"]
    if bosses:
        ordinary, leader, read = party_levels(fl.number, beasts)
        why = ("about where its ordinary mobs top out" if read
               else "a typical crawler this deep")
        o += ["", "### Bosses against a party", "",
              f"How many crawlers each boss's stat points are worth, at three levels: an "
              f"ordinary crawler on this floor (level {ordinary}, {why}), one on the "
              f"leaderboard (level {leader}), and one of the boss's own level. A crawler "
              f"has {crawler_points(1) - k['points_per_level']} points plus "
              f"{k['points_per_level']} a level.", "",
              "Stat points only, and stat points do not care how a crawler fights: a "
              "caster's Intelligence counts the same as a brawler's Strength. What the "
              "table leaves out is gear and class, and both count for more the deeper the "
              "floor: by the middle floors a crawler's enchanted kit and abilities can be "
              "worth more than their own points, so a well-equipped party needs fewer "
              "people than this says. It leaves out the boss's own mechanic too, and that "
              "can need more.", "",
              "*One-shot* means the boss's hit kills a crawler of that level outright, with "
              "their points spread evenly, and then no number of them is enough. **You're "
              "going to die**, below, gives the level each boss stops doing it.", "",
              f"| Boss | Level | Points | Crawlers at L{ordinary} | Crawlers at L{leader} "
              f"| Crawlers of its level |",
              "| --- | ---: | ---: | ---: | ---: | ---: |"]

        def against(b, level):
            return "one-shot" if b.one_shots(level) else f"{b.crawlers_at(level):.1f}"
        for b in bosses:
            o.append(f"| {b.name} | {b.level} | {b.points:,} | {against(b, ordinary)} | "
                     f"{against(b, leader)} | {against(b, b.level)} |")
    o += _going_to_die(beasts)
    o.append("")
    return o


def _going_to_die(beasts: List[MobBuild]) -> List[str]:
    """The level below which each mob and boss kills a crawler in one hit, and the
    floor's own cutoff: the level its hardest-hitting ordinary mob stops doing it.
    A party of any size loses to a one-hit kill, one crawler at a time."""
    per, k = MOB_BUILDS["hit_per_point"], CRAWLER_STATS["knobs"]
    o = ["", "### You're going to die", "",
         "Numbers don't help against something that kills in one hit: each crawler who "
         "steps up goes down before they can do anything, and the next goes the same way. "
         f"A mob's full-strength hit is its STR x {per}, or its INT x {per} if its trick "
         f"hits harder and it has the MP for one use. That kills a crawler outright when it "
         f"is at least their HP, so when the stat behind it is at least "
         f"{k['hp_per_con'] / per:g} times their CON.", "",
         "Below, the level a crawler needs to live through it: with their points spread "
         "evenly, and with half of them in Constitution, as veterans learn to do. A trick "
         "is counted as a hit; if this one's trick is something else, a charm or a theft, "
         f"use its STR x {per} instead.", ""]
    mobs = [b for b in beasts if b.tier == "mob"]
    if mobs:
        worst = max(mobs, key=lambda b: b.hit[0])
        hit, behind = worst.hit
        cutoff = lives_through(hit)
        how = "off STR" if behind == "STR" else "with a trick"
        if cutoff == 1:
            o += [f"**Nothing ordinary on this floor kills a crawler in one hit**, not even "
                  f"at level 1: the hardest hit among its ordinary mobs is {hit:,}, "
                  f"{how}, from {worst.name}.", ""]
        else:
            o += [f"**This floor's cutoff is level {cutoff}.** Below it, a crawler with their "
                  f"points spread evenly dies in one hit to the floor's hardest-hitting "
                  f"ordinary mob: {worst.name}, at {hit:,} {how}. With half their points in "
                  f"Constitution, a crawler lives through it from level "
                  f"{lives_through(hit, half=True)}.", ""]

    def below(hit, half=False):
        level = lives_through(hit, half)
        return "—" if level == 1 else f"below L{level}"
    o += ["| Mob or boss | Level | Hit | With | One-shots, spread evenly "
          "| One-shots, half in CON |",
          "| --- | ---: | ---: | --- | ---: | ---: |"]
    for b in sorted(beasts, key=lambda b: -b.hit[0]):
        hit, behind = b.hit
        o.append(f"| {b.name} | {b.level} | {hit:,} | {behind} | {below(hit)} "
                 f"| {below(hit, half=True)} |")
    return o


def stat_rules_section() -> List[str]:
    """The crawler stat rules, identical on every floor.

    Reference rather than generated content, so it reads the same every time and
    sits at the end of the write-up with the campaign material. Every number comes
    out of CRAWLER_STATS["knobs"] -- the prose is formatted with it and the table
    is computed from it, so retuning a dial moves both and they cannot disagree.

    The five stats, the 3-5 starting range, the 9-10 natural ceiling, mana being
    equal to Intelligence rather than a multiple of it, and the two ends of the
    regeneration curve are all out of the books. Everything else is this project's
    own arithmetic, sanity-checked against the anchors the books do give rather
    than derived from them: Strength 30 comes out at seven and a half average
    humans' worth of lift here, against the books' "as strong as six ordinary
    humans", which is the right neighbourhood and not a match."""
    cs = CRAWLER_STATS
    k = cs["knobs"]
    o = ["## Crawler Stats", ""]
    o.append("Five stats, and the System sets them from what a crawler actually was "
             "before the Collapse. Everything below is per point, with no hidden "
             "modifier table — if a number is not here, it is not in the maths.")
    o.append("")
    o.append("| Score | What it means |")
    o.append("|---:|---|")
    for score, meaning in cs["benchmarks"]:
        o.append(f"| {score} | {meaning} |")
    o.append("")

    for st in cs["stats"]:
        o.append(f"**{st['name']} ({st['abbr']})** — {st['governs']}.")
        o.append("")
        for rule in st["rules"]:
            o.append(f"- {rule.format(**k)}")
        o.append("")

    o.append("### Mana regeneration")
    o.append("")
    o.append("| INT | Recovers |")
    o.append("|---:|---|")
    for at, text in cs["mana_regen"]:
        o.append(f"| {at} | {text} |")
    o.append("")

    o.append("### At a glance")
    o.append("")
    o.append("| Score | Comfortable lift | Max lift | Melee / ranged | Max health | "
             "Mana | Regeneration | Shop |")
    o.append("|---:|---:|---:|---:|---:|---:|---|---:|")
    for s in cs["example_scores"]:
        o.append(
            f"| {s} | {s * k['lift_comfortable']:,} lb | {s * k['lift_max']:,} lb "
            f"| +{s // k['attack_per']} | {s * k['hp_per_con']:,} | {s} "
            f"| {_mana_regen_for(s)} "
            f"| -{min(s * k['discount_per'], k['discount_cap'])}% |")
    o.append("")

    o.append("### Growing them")
    o.append("")
    for line in cs["growth"]:
        o.append(f"- {line.format(**k)}")
    o.append("")
    o.append(cs["npc_note"].format(**k))
    o.append("")
    return o


def second_mob_lines(reg) -> List[str]:
    """The mob that holds a region for the half of the cycle its listed mob does
    not. Same level band -- it is the same region, at a different hour."""
    if not reg.second_mob:
        return []
    label = ("Holds the region by day" if reg.second_shift == "day"
             else "Takes the region after dark")
    return [f"- **{label}:** {reg.second_mob.name}, same level band",
            f"  - {reg.second_mob.quirk}",
            f"  - *{reg.second_mob.description}*"]


def crawler_section(fl) -> List[str]:
    """The Other Crawlers section, identical on every floor that has a roster.

    Returns no lines at all when there are no teams, so a floor generated outside a
    campaign reads exactly as it did before there was one."""
    teams = getattr(fl, "other_crawlers", None)
    if not teams:
        return []
    days = getattr(fl, "days", None)
    o = ["## Other Crawlers", ""]
    if days:
        o.append(f"Other survivors sharing this floor. Each report is dated to a day of "
                 f"the {days}-day countdown — it is what the death feed and the rumor "
                 f"mill carry on that day, not something already true when the party "
                 f"arrives.")
    else:
        o.append("Other survivors sharing this floor, as the death feed and the rumor "
                 "mill have them. This floor runs too short to date them to a day.")
    if any(t.members for t in teams):
        o.append("")
        o.append("Everyone still breathing chose a race and a class on floor 3. The race "
                 "is permanent; classes with a level 6 upgrade show it from floor 6 down.")
    o.append("")
    for ct in teams:
        lead = f"Day {ct.day} — {ct.name}" if ct.day else ct.name
        carried = "" if ct.first_seen == ct.floor else f" — met on floor {ct.first_seen}"
        o.append(f"- **{lead}** (*{ct.disposition}*{carried}) — formed from "
                 f"{ct.origin}; {ct.status}.")
        if ct.crawler_count:
            o.append(f"  - {party_line(ct)}")
    o.append("")
    # The reactions are listed once rather than per team: with more teams than
    # points on the scale they repeat, and the same two sentences under every
    # entry buries the part that differs.
    o.append("How they take the party on contact:")
    o.append("")
    seen = []
    for ct in teams:
        if ct.disposition not in seen:
            seen.append(ct.disposition)
    for label in disposition_labels():
        if label in seen:
            o.append(f"- ***{label}*** — {disposition_reaction(label)}.")
    for label in seen:                       # standings the user invented themselves
        if label not in disposition_labels():
            o.append(f"- ***{label}*** — {disposition_reaction(label)}.")
    o.append("")
    o.extend(leaderboard_section(fl))
    return o


def render_grid(fl: Floor) -> str:
    n = fl.grid
    by_pos = {(nb.row, nb.col): nb for nb in fl.neighborhoods}
    lines = []
    for r in range(n):
        row = []
        for c in range(n):
            nb = by_pos[(r, c)]
            tag = nb.label
            if nb.is_entry:
                tag += "*"
            if nb.hidden_stairs:
                tag += "+" * len(nb.hidden_stairs)
            row.append(f"[{tag:<4}]")
        lines.append(" ".join(row))
    return "\n".join(lines)

def boss_block(b: Boss, floor: int, label: Optional[str] = None) -> List[str]:
    out = [
        f"- **{label or b.tier.title()} Boss:** {b.name} (Level {b.level})",
        f"  - Form: {b.form}.",
        f"  - Chamber: {b.chamber}.",
        f"  - Mechanic: {b.mechanic}",
        f"  - Drops: a {boss_box(b.tier, floor)}",
        f"  - Stairwell in chamber: {'yes' if b.has_stairwell else 'no'}",
    ]
    return out

def render(fl: Floor) -> str:
    cfg = FLOOR_CFG[fl.number]
    n_nb = len(fl.neighborhoods)
    n_bor = len(fl.borough_bosses)
    n_city = len(fl.city_bosses)
    n_top = 1 if fl.top_boss else 0
    # Count actual has_stairwell=True bosses rather than assuming every boss has one —
    # --stair-chance and --borough-boss-rate can both leave a boss without a stairwell.
    boss_stairs_ct = (sum(1 for nb in fl.neighborhoods if nb.boss.has_stairwell)
                      + sum(1 for b in fl.borough_bosses.values() if b.has_stairwell)
                      + sum(1 for b in fl.city_bosses.values() if b.has_stairwell)
                      + (1 if fl.top_boss and fl.top_boss.has_stairwell else 0))
    hidden_ct = sum(len(nb.hidden_stairs) for nb in fl.neighborhoods)
    entry = next(nb for nb in fl.neighborhoods if nb.is_entry)

    o = []
    o.append(f"# Floor {fl.number} — {fl.name} ({fl.size})")
    o.append("")
    o.append("## Summary")
    o.append("")
    o.append(f"- **Countdown:** {fl.days} days")
    zones = f"- **Zones:** {n_nb} neighborhood{'s' if n_nb != 1 else ''}"
    if n_bor > 1:
        zones += f", {n_bor} boroughs"
    elif n_bor == 1:
        zones += ", 1 borough"
    if n_city > 1:
        zones += f", {n_city} cities"
    elif n_city == 1:
        zones += ", 1 city"
    if n_top:
        zones += f", 1 {fl.top_tier_name.lower()}"
    o.append(zones)
    bosses = f"- **Bosses:** {n_nb} neighborhood, {n_bor} borough, {n_city} city"
    if n_top:
        bosses += f", 1 {fl.top_tier_name.lower()}"
    o.append(bosses)
    o.append(f"- **Stairwells:** {boss_stairs_ct} in boss chambers, {hidden_ct} hidden")
    lo, hi = cfg["mob_band"]
    o.append(f"- **Standard mob levels:** {lo}–{hi} ({fl.scaling} scaling)")
    o.append(f"- **Bosses roam:** {'yes' if cfg['bosses_roam'] else 'no — confined to chambers'}")
    o.append(f"- **Janitor mob:** {fl.janitor.name} (Level {fl.janitor_level}). {fl.janitor.quirk}")
    o.append(f"  - *{fl.janitor.description}*")
    o.append(f"- **Floor storyline:** {fl.storyline}")
    o.append(f"- **Entry point:** {entry.label} — {entry.name} ({compass(entry.row, entry.col, fl.grid)})")
    o.append(f"- **Seed:** {fl.seed}")
    o.append("")

    o.append("## The Collapse")
    o.append("")
    o.append(fl.collapse_note)
    o.append("")

    o.append("## Layout")
    o.append("")
    o.append("Grid, north at top. `*` = entry, `+` = one hidden stairwell.")
    o.append("")
    o.append("```")
    o.append(render_grid(fl))
    o.append("```")
    o.append("")
    if fl.grid >= 4:
        c_side = fl.grid // 4
        if c_side <= 1:
            o.append("Boroughs are the four 2x2 quadrants (NW, NE, SW, SE). Each borough boss "
                     "chamber sits at the center of its quadrant, reachable from all four of "
                     "its neighborhoods. The city boss chamber sits at the seam where the four "
                     "boroughs meet.")
        else:
            o.append(f"The {fl.top_tier_name} is {c_side * c_side} Cities arranged in a "
                     f"{c_side}x{c_side} grid. Each City is its own 2x2-borough block (4x4 "
                     "neighborhoods, same shape as a large floor on its own) with its own City "
                     "Boss at its center — every City gets one, though not every borough within "
                     "it necessarily has a borough boss (see Borough Bosses below). One "
                     f"{fl.top_tier_name} Boss oversees every City Boss on the floor; bosses "
                     f"cap out at {fl.top_tier_name} tier regardless of how many Cities make up "
                     "the map.")
    elif fl.grid == 2:
        o.append("The four neighborhoods form one borough. The borough boss chamber sits at "
                 "the center, reachable from all four neighborhoods.")
    else:
        o.append("A single neighborhood. The boss chamber is at the far end from the entry.")
    o.append("")

    if fl.top_boss:
        o.append(f"## {fl.top_tier_name} Boss")
        o.append("")
        o.extend(boss_block(fl.top_boss, fl.number, label=fl.top_tier_name))
        o.append("")

    if len(fl.city_bosses) == 1:
        o.append("## City Boss")
        o.append("")
        o.extend(boss_block(next(iter(fl.city_bosses.values())), fl.number))
        o.append("")
    elif fl.city_bosses:
        o.append("## City Bosses")
        o.append("")
        for cname, b in fl.city_bosses.items():
            members = [nb.label for nb in fl.neighborhoods if nb.city == cname]
            city_boroughs = sorted({nb.borough for nb in fl.neighborhoods if nb.city == cname})
            o.append(f"### City {cname} ({len(members)} neighborhoods, "
                     f"boroughs {', '.join(city_boroughs)})")
            o.append("")
            o.extend(boss_block(b, fl.number))
            o.append("")

    if fl.borough_bosses:
        o.append("## Borough Bosses")
        o.append("")
        for bname, b in fl.borough_bosses.items():
            members = [nb.label for nb in fl.neighborhoods if nb.borough == bname]
            head = "The Borough" if fl.grid == 2 else f"Borough {bname}"
            o.append(f"### {head} (neighborhoods {', '.join(members)})")
            o.append("")
            o.extend(boss_block(b, fl.number))
            o.append("")

    o.append("## Neighborhoods")
    o.append("")
    for nb in fl.neighborhoods:
        pos = compass(nb.row, nb.col, fl.grid)
        adj = []
        for other in fl.neighborhoods:
            if abs(other.row - nb.row) + abs(other.col - nb.col) == 1:
                adj.append(other.label)
        o.append(f"### {nb.label}. {nb.name}")
        o.append("")
        loc = f"- **Location:** {pos}"
        if nb.city and fl.grid // 4 >= 2:
            loc += f", City {nb.city}"
        if nb.borough and fl.grid >= 4:
            loc += f", Borough {nb.borough}"
        if adj:
            loc += f". Adjacent to {', '.join(adj)}"
        loc += f". Distance from entry: {nb.distance}"
        if nb.is_entry:
            loc += " (ENTRY)"
        o.append(loc + ".")
        o.append(f"- **Mob:** {nb.mob.name}, Level {nb.level_range[0]}–{nb.level_range[1]}")
        o.append(f"  - {nb.mob.quirk}")
        o.append(f"  - *{nb.mob.description}*")
        o.extend(boss_block(nb.boss, fl.number))
        o.append(f"- **Loot theme:** {nb.loot} ({nb.loot_tier}-tier loot box)")
        o.append(f"- **Rooms:** ({len(nb.rooms)})")
        for r in nb.rooms:
            conn = ", ".join(r.connects_to) if r.connects_to else "none"
            o.append(f"  - **{r.id} {r.kind}:** {r.description} → connects to {conn}")
        if nb.corridor_encounters:
            o.append("- **Corridor encounters:**")
            for a_id, b_id, text in nb.corridor_encounters:
                o.append(f"  - **{a_id} ↔ {b_id}:** {text}")
        o.append("")

    guild_nbs = [nb for nb in fl.neighborhoods if nb.tutorial_guild]
    if guild_nbs:
        o.append("## Tutorial Guild")
        o.append("")
        o.append(f"Guildmaster's local form: **{fl.guildmaster_form}** (a former crawler bound to a "
                 "non-combatant contract; cannot attack or be attacked).")
        o.append(f"- {fl.guildmaster_blurb}")
        o.append(f"- Present in: {', '.join(nb.label + ' (' + nb.name + ')' for nb in guild_nbs)}")
        o.append("")

    o.append("## Elite")
    o.append("")
    o.append(f"- **{fl.elite.name}** — star of *{fl.elite.show}*. "
             f"{fl.elite.form[0].upper() + fl.elite.form[1:]}. Based in {fl.elite.settlement}; "
             f"storyline plays out across {fl.elite.region}. {fl.elite.plot_armor}")
    o.append("")

    o.extend(crawler_section(fl))

    o.append("## Achievements")
    o.append("")
    o.append("System-wide behaviors, not unique to this floor, but most commonly earned here:")
    o.append("")
    for name, trigger, reward in ACHIEVEMENTS:
        o.append(f"- **{name}:** {trigger} → {reward}")
    o.append("")

    return "\n".join(o)

# ---------------------------------------------------------------------------
# Image rendering (SVG, no dependencies; PNG via optional resvg-py)
# ---------------------------------------------------------------------------

def _svg_to_png(svg: str, path: str) -> None:
    try:
        import resvg_py  # type: ignore
    except ImportError:
        raise MapgenError("PNG output needs resvg-py (pip install resvg-py); or use a .svg path.")
    png_bytes = bytes(resvg_py.svg_to_bytes(svg_string=svg))
    with open(path, "wb") as f:
        f.write(png_bytes)

CELL = 230
PAD = 40
HEADER = 70
LEGEND = 110

MOB_PALETTE = [
    "#e8c4a0", "#b8d8b8", "#c4c8e8", "#e8d8a0", "#d8b8c8", "#a8d8d8",
    "#e0b8b8", "#c8e0a8", "#d0c0e0", "#f0d0b0", "#b0d0e8", "#d8d0a8",
    "#c0d8c8", "#e8c8d8", "#d0d8b8", "#c8c0d8",
]

def _esc(t: str) -> str:
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def _text(x, y, t, size=12, weight="normal", anchor="start", fill="#222", style=""):
    return (f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" '
            f'text-anchor="{anchor}" fill="{fill}" style="{style}">{_esc(t)}</text>')

def _fit(t: str, max_chars: int) -> str:
    return t if len(t) <= max_chars else t[: max_chars - 1] + "…"

def _stair_icon(x, y, hidden=False):
    """Small stair glyph; dashed outline if hidden."""
    dash = ' stroke-dasharray="3,2"' if hidden else ""
    fill = "#fff" if hidden else "#333"
    pts = f"{x},{y+14} {x},{y+9} {x+5},{y+9} {x+5},{y+4} {x+10},{y+4} {x+10},{y} {x+15},{y} {x+15},{y+14}"
    return f'<polygon points="{pts}" fill="{fill}" stroke="#333" stroke-width="1.5"{dash}/>'

LEGEND_GAP = 28        # horizontal space between legend entries
LEGEND_ROW_GAP = 12    # vertical space between legend rows
BOSS_R = {"neighborhood": 13, "borough": 22, "city": 30, "top": 38}


def _legend_entries(fl) -> List[Tuple[str, object, str]]:
    """(kind, detail, label) for each marker this map actually uses.

    Only listing what is on the map keeps a small floor from advertising the city
    and top-tier bosses it does not have -- every size below huge used to show all
    four boss tiers in the key regardless."""
    out = [("boss", "neighborhood", "Neighborhood boss")]
    if fl.borough_bosses:
        out.append(("boss", "borough", "Borough boss"))
    if fl.city_bosses:
        out.append(("boss", "city", "City boss"))
    if fl.top_boss:
        out.append(("boss", "top", f"{fl.top_tier_name or 'Top-tier'} boss"))
    out.append(("stair", False, "Stairwell"))
    if any(nb.hidden_stairs for nb in fl.neighborhoods):
        out.append(("stair", True, "Hidden stairwell"))
    out.append(("entry", None, "Entry"))
    return out


def _legend_half(kind, detail) -> float:
    """Half the height (and width) of one legend marker."""
    if kind == "boss":
        return BOSS_R[detail]
    return 8.0 if kind == "stair" else 9.0


def _legend_width(entry) -> float:
    kind, detail, label = entry
    # 6.2 px/char approximates 11px Helvetica closely enough to flow rows with.
    return 2 * _legend_half(kind, detail) + 8 + len(label) * 6.2 + LEGEND_GAP


def _legend_layout(entries, avail: float):
    """Flow entries into rows no wider than `avail`, as (rows, total height)."""
    rows, cur, cur_w = [], [], 0.0
    for e in entries:
        w = _legend_width(e)
        if cur and cur_w + w > avail:
            rows.append(cur)
            cur, cur_w = [], 0.0
        cur.append(e)
        cur_w += w
    if cur:
        rows.append(cur)
    height = sum(max(_legend_half(k, d) for k, d, _ in row) * 2 for row in rows)
    height += LEGEND_ROW_GAP * (len(rows) - 1)
    return rows, height


def _boss_marker(cx, cy, tier, label):
    r = BOSS_R[tier]
    color = {"neighborhood": "#cd7f32", "borough": "#a8a9ad", "city": "#d4af37", "top": "#7a1f7a"}[tier]
    out = [f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{color}" stroke="#222" stroke-width="2"/>']
    glyph = {"neighborhood": "N", "borough": "B", "city": "C", "top": "★"}[tier]
    out.append(_text(cx, cy + 5, glyph, size=r, weight="bold", anchor="middle"))
    return out

def render_svg(fl: Floor, boss_stairs: bool) -> str:
    n = fl.grid
    W = max(PAD * 2 + CELL * n, 800)
    key_lines = len(fl.borough_bosses) + len(fl.city_bosses) + (1 if fl.top_boss else 0)
    # The legend is laid out before anything is emitted, because how many rows it
    # flows into decides the image height.
    legend_rows, legend_h = _legend_layout(_legend_entries(fl), W - PAD * 2)
    H = HEADER + PAD + CELL * n + 30 + legend_h + 28 + 18 * key_lines + PAD
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'font-family="Helvetica, Arial, sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#f6f2ea"/>']

    # Header
    o.append(_text(PAD, 32, f"Floor {fl.number} — {fl.name}", size=22, weight="bold"))
    lo, hi = FLOOR_CFG[fl.number]["mob_band"]
    o.append(_text(PAD, 54,
                   f"{fl.days} days  •  mobs L{lo}–{hi} ({fl.scaling})  •  janitor: "
                   f"{fl.janitor.name} L{fl.janitor_level}  •  seed {fl.seed}", size=12, fill="#555"))

    ox, oy = (W - CELL * n) // 2, HEADER + PAD

    # Neighborhood cells
    for nb in fl.neighborhoods:
        x = ox + nb.col * CELL
        y = oy + nb.row * CELL
        color = MOB_PALETTE[nb.idx % len(MOB_PALETTE)]
        o.append(f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" fill="{color}" '
                 f'stroke="#555" stroke-width="1.5"/>')
        o.append(_text(x + 10, y + 26, nb.label, size=24, weight="bold"))
        o.append(_text(x + 42, y + 24, _fit(nb.name, 22), size=13, weight="bold"))
        o.append(_text(x + 10, y + 48, _fit(nb.mob.name, 30), size=12))
        o.append(_text(x + 10, y + 64, f"Level {nb.level_range[0]}–{nb.level_range[1]}", size=12))
        o.append(_text(x + 10, y + 84, "Boss: " + _fit(nb.boss.name, 24), size=11, fill="#444"))
        o.append(_text(x + 10, y + 99, f"L{nb.boss.level}  •  {nb.safe_rooms} safe room"
                       + ("s" if nb.safe_rooms != 1 else "")
                       + ("  •  guild" if nb.tutorial_guild else ""), size=11, fill="#444"))
        # neighborhood boss chamber, bottom-right corner
        bx, by = x + CELL - 30, y + CELL - 30
        o.extend(_boss_marker(bx, by, "neighborhood", ""))
        if nb.boss.has_stairwell:
            o.append(_stair_icon(bx - 32, by - 7))
        # hidden stairs, bottom-left
        for hs_i, _ in enumerate(nb.hidden_stairs):
            o.append(_stair_icon(x + 12 + hs_i * 22, y + CELL - 24, hidden=True))
        # entry
        if nb.is_entry:
            o.append(f'<rect x="{x+3}" y="{y+3}" width="{CELL-6}" height="{CELL-6}" fill="none" '
                     f'stroke="#c0392b" stroke-width="4"/>')
            o.append(_text(x + CELL // 2, y + CELL - 12, "ENTRY", size=14, weight="bold",
                           anchor="middle", fill="#c0392b"))

    # Borough outlines + bosses. Every block gets an outline; only blocks that actually
    # rolled a borough boss (see --borough-boss-rate) get a marker and stairwell icon.
    k = n // 2
    if k >= 1:
        blocks = [(0, 0, "Borough")] if k == 1 else \
                 [(br, bc, block_label(br, bc, k, "Borough")) for br in range(k) for bc in range(k)]
        for br, bc, bname in blocks:
            x, y = ox + bc * 2 * CELL, oy + br * 2 * CELL
            o.append(f'<rect x="{x}" y="{y}" width="{CELL*2}" height="{CELL*2}" fill="none" '
                     f'stroke="#222" stroke-width="4"/>')
            b = fl.borough_bosses.get(bname)
            if b:
                o.extend(_boss_marker(x + CELL, y + CELL, "borough", ""))
                if boss_stairs and b.has_stairwell:
                    o.append(_stair_icon(x + CELL + 26, y + CELL - 28))

    # City outlines + bosses. Every City gets a boss (unlike boroughs, no --city-boss-rate).
    c_side = n // 4
    if c_side >= 1:
        cities = [(0, 0, "City")] if c_side == 1 else \
                 [(cr, cc, block_label(cr, cc, c_side, "City")) for cr in range(c_side) for cc in range(c_side)]
        for cr, cc, cname in cities:
            x, y = ox + cc * 4 * CELL, oy + cr * 4 * CELL
            o.append(f'<rect x="{x}" y="{y}" width="{CELL*4}" height="{CELL*4}" fill="none" '
                     f'stroke="#000" stroke-width="7"/>')
            b = fl.city_bosses.get(cname)
            if b:
                o.extend(_boss_marker(x + CELL * 2, y + CELL * 2, "city", ""))
                if boss_stairs and b.has_stairwell:
                    o.append(_stair_icon(x + CELL * 2 + 40, y + CELL * 2 - 42))

    # Top-tier boss (County/State/Country/Continent): sits at the seam where all Cities
    # meet — only present once there's more than one City to oversee.
    if fl.top_boss:
        cx, cy = ox + n * CELL / 2, oy + n * CELL / 2
        o.extend(_boss_marker(cx, cy, "top", ""))
        if boss_stairs and fl.top_boss.has_stairwell:
            o.append(_stair_icon(cx + 44, cy - 46))

    # Legend. Entries were previously pinned to hardcoded offsets on two fixed rows,
    # which put the top-tier circle (r=38, centred just 30px below a row whose city
    # circle already reaches 30px down) on top of the first row's marker and label.
    # Flowing them instead keeps every marker clear of its neighbours at any size.
    ly = oy + CELL * n + 30
    y = prev_half = 0.0
    for i, row in enumerate(legend_rows):
        half = max(_legend_half(k, d) for k, d, _ in row)
        y = ly + half if i == 0 else y + prev_half + LEGEND_ROW_GAP + half
        x = float(PAD)
        for kind, detail, label in row:
            h = _legend_half(kind, detail)
            if kind == "boss":
                o.extend(_boss_marker(x + h, y, detail, ""))
            elif kind == "stair":
                o.append(_stair_icon(x + h - 7.5, y - 7, hidden=detail))
            else:
                o.append(f'<rect x="{x}" y="{y-9}" width="18" height="18" fill="none" '
                         f'stroke="#c0392b" stroke-width="3"/>')
            o.append(_text(x + 2 * h + 8, y + 4, label, size=11))
            x += _legend_width((kind, detail, label))
        prev_half = half

    # Boss key
    lx = PAD
    ky = y + prev_half + 28
    if fl.top_boss:
        o.append(_text(lx, ky, f"{fl.top_tier_name} boss: {fl.top_boss.name} (L{fl.top_boss.level})",
                       size=12, weight="bold"))
        ky += 18
    for cname, b in fl.city_bosses.items():
        tag = "City" if n == 4 else f"City {cname}"
        o.append(_text(lx, ky, f"{tag} boss: {b.name} (L{b.level})", size=12, weight="bold"))
        ky += 18
    for bname, b in fl.borough_bosses.items():
        tag = "Borough" if n == 2 else f"Borough {bname}"
        o.append(_text(lx, ky, f"{tag} boss: {b.name} (L{b.level})", size=12))
        ky += 18

    o.append("</svg>")
    return "\n".join(o)

def render_room_svg(nb: Neighborhood, fl: Floor) -> str:
    """Standalone section map: this neighborhood's room graph, laid out in BFS-depth columns
    with the Entrance at the left and the Boss Chamber at the deepest (rightmost) column."""
    from collections import deque
    W = 1000
    graph = {r.id: r.connects_to for r in nb.rooms}
    entrance_id = nb.rooms[0].id
    depth = {entrance_id: 0}
    queue = deque([entrance_id])
    while queue:
        cur = queue.popleft()
        for nxt in graph[cur]:
            if nxt not in depth:
                depth[nxt] = depth[cur] + 1
                queue.append(nxt)
    cols = {}
    for rid, d in depth.items():
        cols.setdefault(d, []).append(rid)
    max_depth = max(depth.values())

    graph_top, graph_bottom = 130, 340
    col_w = (W - 160) / max_depth
    pos = {}
    for d in sorted(cols):
        ids = cols[d]
        x = 80 + d * col_w
        for i, rid in enumerate(ids):
            y = graph_top + (i + 1) * (graph_bottom - graph_top) / (len(ids) + 1)
            pos[rid] = (x, y)

    list_top = 360
    H = list_top + 80 + 18 * (len(nb.rooms) + len(nb.corridor_encounters) + 3)

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'font-family="Helvetica, Arial, sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#f6f2ea"/>']
    o.append(_text(30, 32, f"Floor {fl.number} \u2014 {nb.label}. {nb.name}", size=20, weight="bold"))
    o.append(_text(30, 54, f"{nb.mob.name} L{nb.level_range[0]}\u2013{nb.level_range[1]}  \u2022  "
                   f"boss: {nb.boss.name} L{nb.boss.level}  \u2022  {len(nb.rooms)} rooms  \u2022  "
                   f"seed {fl.seed}", size=12, fill="#555"))

    # Legend
    lx, ly_leg = 30, 76
    o.append(f'<circle cx="{lx}" cy="{ly_leg}" r="8" fill="#2e8b57" stroke="#222" stroke-width="1.5"/>')
    o.append(_text(lx + 14, ly_leg + 4, "Entrance", size=10))
    lx += 90
    o.append(f'<rect x="{lx-7}" y="{ly_leg-7}" width="14" height="14" fill="#3a6ea5" stroke="#222" stroke-width="1.5"/>')
    o.append(_text(lx + 14, ly_leg + 4, "Safe Room", size=10))
    lx += 100
    o.append(f'<circle cx="{lx}" cy="{ly_leg}" r="8" fill="#7a1f7a" stroke="#222" stroke-width="1.5"/>')
    o.append(_text(lx + 14, ly_leg + 4, "Guild Hall", size=10))
    lx += 100
    o.append(_stair_icon(lx - 7, ly_leg - 7, hidden=True))
    o.append(_text(lx + 14, ly_leg + 4, "Hidden Passage", size=10))
    lx += 130
    o.extend(_boss_marker(lx, ly_leg, "neighborhood", ""))
    o.append(_text(lx + 18, ly_leg + 4, "Boss Chamber", size=10))
    lx += 130
    o.append(f'<circle cx="{lx}" cy="{ly_leg}" r="8" fill="#cfcac0" stroke="#666" stroke-width="1.5"/>')
    o.append(_text(lx + 14, ly_leg + 4, "Other room (Den/Junction/Cache/Overlook/Shrine)", size=10))

    encounter_edges = {tuple(sorted((a, b))): text for a, b, text in nb.corridor_encounters}
    drawn = set()
    for r in nb.rooms:
        for other in r.connects_to:
            key = tuple(sorted((r.id, other)))
            if key in drawn:
                continue
            drawn.add(key)
            x1, y1 = pos[r.id]
            x2, y2 = pos[other]
            hot = key in encounter_edges
            o.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                     f'stroke="{"#b8860b" if hot else "#999"}" stroke-width="{2.5 if hot else 1.5}"/>')
            if hot:
                mx, my = (x1 + x2) / 2, (y1 + y2) / 2
                o.append(f'<circle cx="{mx}" cy="{my}" r="9" fill="#fff" stroke="#b8860b" stroke-width="2"/>')
                o.append(_text(mx, my + 4, "!", size=12, weight="bold", anchor="middle", fill="#b8860b"))

    for r in nb.rooms:
        x, y = pos[r.id]
        if r.kind == "Boss Chamber":
            o.extend(_boss_marker(x, y, "neighborhood", ""))
        elif r.kind == "Hidden Passage":
            o.append(_stair_icon(x - 7, y - 7, hidden=True))
        elif r.kind == "Safe Room":
            o.append(f'<rect x="{x-10}" y="{y-10}" width="20" height="20" fill="#3a6ea5" stroke="#222" stroke-width="1.5"/>')
        elif r.kind == "Guild Hall":
            o.append(f'<circle cx="{x}" cy="{y}" r="11" fill="#7a1f7a" stroke="#222" stroke-width="1.5"/>')
        elif r.kind == "Entrance":
            o.append(f'<circle cx="{x}" cy="{y}" r="11" fill="#2e8b57" stroke="#222" stroke-width="1.5"/>')
        else:
            o.append(f'<circle cx="{x}" cy="{y}" r="9" fill="#cfcac0" stroke="#666" stroke-width="1.5"/>')
            o.append(_text(x, y + 4, r.kind[0], size=10, weight="bold", anchor="middle"))
        o.append(_text(x, y - 16, r.id, size=11, weight="bold", anchor="middle"))

    ly = list_top
    o.append(_text(30, ly, "Rooms:", size=13, weight="bold"))
    ly += 20
    for r in nb.rooms:
        conn = ", ".join(r.connects_to) if r.connects_to else "none"
        o.append(_text(30, ly, f"{r.id} {r.kind}: {_fit(r.description, 90)} (\u2192 {conn})", size=11))
        ly += 18
    if nb.corridor_encounters:
        ly += 8
        o.append(_text(30, ly, "Corridor encounters:", size=13, weight="bold"))
        ly += 20
        for a_id, b_id, text in nb.corridor_encounters:
            o.append(_text(30, ly, f"{a_id} \u2194 {b_id}: {_fit(text, 100)}", size=11))
            ly += 18

    o.append("</svg>")
    return "\n".join(o)

def floor_svg(fl, boss_stairs: bool) -> str:
    """The main map for any floor, as SVG source."""
    if isinstance(fl, Floor11):
        return render11_svg(fl)
    if isinstance(fl, Floor9):
        return render9_svg(fl)
    if isinstance(fl, Floor8):
        return render8_svg(fl)
    if isinstance(fl, Floor6):
        return render6_svg(fl)
    if isinstance(fl, Floor5):
        return render5_svg(fl)
    if isinstance(fl, Floor4):
        return render4_svg(fl)
    if isinstance(fl, Floor3):
        return render3_svg(fl)
    return render_svg(fl, boss_stairs)

def floor_svgs(fl, boss_stairs: bool = True) -> List[Tuple[str, str]]:
    """Every map a floor produces, as (filename suffix, SVG source) pairs, in the
    order they should be shown. The main map's suffix is "" and it comes first;
    floors 1-2 add one room map per neighborhood, floor 10 is heat maps only
    (it has no overview map)."""
    if isinstance(fl, Floor10):
        return [(f"_heat{h.number}", render10_heat_svg(fl, h)) for h in fl.heats]
    out = [("", floor_svg(fl, boss_stairs))]
    if isinstance(fl, Floor):
        out += [(f"_room_{nb.label}", render_room_svg(nb, fl)) for nb in fl.neighborhoods]
    return out

def _write_svg(svg: str, path: str) -> None:
    if path.lower().endswith(".png"):
        _svg_to_png(svg, path)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)

def write_image(fl, path: str, boss_stairs: bool) -> None:
    _write_svg(floor_svg(fl, boss_stairs), path)

def save_svgs(svgs: List[Tuple[str, str]], path: str) -> List[str]:
    """Write already-rendered (suffix, SVG) pairs, named off `path`, and return the
    paths written. A .png path converts each one; anything else is saved as SVG."""
    import os
    root, ext = os.path.splitext(path)
    out = []
    for suffix, svg in svgs:
        p = path if not suffix else f"{root}{suffix}{ext}"
        _write_svg(svg, p)
        out.append(p)
    return out

def write_images(fl, path: str, boss_stairs: bool = True) -> List[str]:
    """Render and write every map for `fl`, named off `path`."""
    return save_svgs(floor_svgs(fl, boss_stairs), path)

# ===========================================================================
# FLOOR 3 — THE OVER CITY
# Settlements (sanctuaries) on a grid of ruined wilderness regions.
# ===========================================================================

FLOOR3_CFG = {
    "mob_band": (8, 20),            # wilderness mobs
    "days": (8, 12),                # canon: 20 traditionally, AI forced 8
    "boss_offset": {"neighborhood": 8, "borough": 25},
    "city_boss_level": 85,          # canon Grimaldi
    "janitor_level": (8, 12),
    "guard_level": {"tiny": 60, "small": 65, "medium": 75, "large": 80, "xl": 90},
}

# grid, main settlement size, satellite sizes, boss counts, stairwells
SIZE3_CFG = {
    "small":  {"grid": 3, "main": "medium", "satellites": [],
               "neighborhood": 1, "borough": 0, "city": 0, "stairs": 1},
    "medium": {"grid": 4, "main": "large",  "satellites": ["small", "tiny", "tiny"],
               "neighborhood": 3, "borough": 1, "city": 0, "stairs": 2},
    "large":  {"grid": 5, "main": "xl",     "satellites": ["medium", "small", "small", "tiny", "tiny", "tiny"],
               "neighborhood": 6, "borough": 2, "city": 1, "stairs": 4},
}

LAW_TEXT = ("Don't mess with the NPCs. Attacking, robbing, or killing a citizen in view "
            "of a guard is a death sentence; there is no jail, and survivors are barred from "
            "the settlement for good. If no guard saw it and the NPC is dead, well. "
            "You might just get off scot-free.")

@dataclass
class Quest:
    title: str
    giver: str
    objective: str
    flavor: str
    reward: str
    unlocks: Optional[str] = None      # stairwell label

@dataclass
class Settlement:
    idx: int
    label: str
    name: str
    size: str
    row: int
    col: int
    race: str
    guard_level: int
    tutorial_guild: bool
    shops: List[str]
    race_guilds: List[str]
    class_guilds: List[str]
    club: bool
    quests: List[Quest]
    distance: int = 0
    hostile: bool = False
    on_river: bool = False

@dataclass
class Stairwell:
    label: str
    region_label: str
    condition: str

@dataclass
class Region:
    idx: int
    label: str
    name: str
    row: int
    col: int
    mob: Mob
    night: str
    level_range: Tuple[int, int]
    loot: str
    feature: str
    # Some night threats mean the listed mob only holds the region for half the
    # cycle. The other half gets its own mob, on the same level band, and
    # second_shift says which half that is -- "day" or "night".
    second_mob: Optional[Mob] = None
    second_shift: str = ""
    boss: Optional[Boss] = None
    roams_to: Optional[str] = None
    stairwell: Optional[Stairwell] = None
    distance: int = 0
    is_entry: bool = False
    river: Optional[str] = None
    flora: Optional[str] = None
    camp: Optional[str] = None

@dataclass
class Floor3:
    size: str
    name: str
    days: int
    janitor: Mob
    janitor_level: int
    elite: Elite
    grid: int
    cells: list                       # Settlement | Region, in row-major order
    stairs: List[Stairwell]
    scaling: str
    seed: int
    number: int = 3
    other_crawlers: List[CrawlerTeam] = field(default_factory=list)

class Generator3:
    FLOOR = 3

    def __init__(self, size, scaling, seed, days):
        # Each floor gets its own stream off the shared seed. Without this every
        # generator started from random.Random(seed), so two floors built from one
        # campaign seed drew the same cards in the same order out of the banks they
        # share: floors 3 and 6, which share this generator, came back at the same
        # seed with 85% of their loot, 83% of their features and a third of their
        # boss names in common. FLOOR is a class attribute so that a subclass can
        # say which floor it is without reaching into its parent's constructor.
        self.rng = random.Random(f"floor{self.FLOOR}|{seed}")
        self.seed = seed
        self.size = size
        self.scaling = scaling
        self.cfg = FLOOR3_CFG
        self.sc = SIZE3_CFG[size]
        self.days_override = days
        self.used = set()
        self.mech_decks = make_mechanic_decks(self.rng)
        self.namer = BossNamer(self.rng, RUIN_ADJ)
        # Dealt rather than rolled, for the reasons in Generator.
        self.loot_deck = Deck(self.rng, LOOT_THEMES)
        self.feature_deck = Deck(self.rng, RUIN_FEATURES)
        # Quests too, and for the same reason with more force: a town posts three
        # times as many as it used to, so a large floor carries about thirty and
        # every component of them would otherwise be drawn from a bank of ten.
        self.quest_decks = {name: Deck(self.rng, bank) for name, bank in (
            ("objective", QUEST_OBJECTIVE), ("item", QUEST_ITEM),
            ("role", QUEST_GIVER_ROLE), ("reward", QUEST_REWARD),
            ("flavor", QUEST_FLAVOR), ("title", QUEST_TITLE))}
        self.night_deck = Deck(self.rng, NIGHT_THREATS)

    def _unique(self, fn):
        for _ in range(100):
            v = fn()
            if v not in self.used:
                break
        self.used.add(v)
        return v

    def settlement_name(self):
        return self._unique(lambda: self.rng.choice(SETTLEMENT_PREFIX) + self.rng.choice(SETTLEMENT_SUFFIX))

    def region_name(self):
        return self._unique(lambda: f"The {self.rng.choice(RUIN_ADJ)} {self.rng.choice(RUIN_PLACE)}")

    def make_mob(self) -> Mob:
        adj, noun = self._unique(lambda: mob_words(self.rng, 3))
        return describe_mob(self.rng, adj, noun)

    def make_boss(self, tier: str, mob: Mob, base: int) -> Boss:
        name = self.namer.name(mob.noun)
        self.used.add(name)              # shared with the settlement and region names
        sizes = {"neighborhood": (10, 18), "borough": (18, 35), "city": (35, 70)}[tier]
        sz = self.rng.randint(*sizes)
        form = self.rng.choice(BOSS_FORMS).format(
            size=sz, a_size=("an" if sz in (8, 11, 18) else "a"),
            noun_l=mob.noun.lower(), noun_sing=singular(mob.noun).lower())
        level = self.cfg["city_boss_level"] if tier == "city" else base + self.cfg["boss_offset"][tier]
        mech = boss_mechanics(self.mech_decks, tier)
        chamber = "roams the ruins; no fixed chamber"
        return Boss(name, tier, level, form, mech, chamber, False)

    def level_range(self, dist, max_dist):
        lo, hi = self.cfg["mob_band"]
        if self.scaling == "distance" and max_dist > 0:
            c = lo + (dist / max_dist) * (hi - lo)
            a = max(lo, int(round(c - 2)))
            b = min(hi, int(round(c + 2)))
            return (a, max(a, b))
        a = self.rng.randint(lo, hi - 2)
        return (a, min(hi, a + self.rng.randint(2, 4)))

    def make_quest(self, s_name, region_names, settlement_names, mob_names, unlocks=None) -> Quest:
        d = self.quest_decks
        role = d["role"].draw()
        giver = f"{self.rng.choice(BOSS_FIRST)} the {role}"
        others = [n for n in settlement_names if n != s_name] or [s_name]
        # The subject is drawn before the objective rather than inline in the
        # format call, so the title can name the thing the quest is about. The old
        # five title forms all keyed off the giver's trade or the town, which a
        # town posting nine quests repeats immediately.
        item, region = d["item"].draw(), self.rng.choice(region_names)
        mob, elsewhere = self.rng.choice(mob_names), self.rng.choice(others)
        form = d["objective"].draw()
        obj = form.format(
            item=item, region=mid_phrase(region), mob=mob, settlement=elsewhere,
            giver=giver.split()[0], relative=self.rng.choice(QUEST_RELATIVE),
            role=d["role"].draw())
        # A title may only name a subject the objective actually used, or it
        # announces "The Price of the Town Charter" over a boundary dispute that
        # has no charter in it. Forms built from the giver's trade or the town
        # always fit, so there is always something to fall back on.
        subjects = {k for k in ("item", "region", "mob") if "{%s}" % k in form}
        for _ in range(8):
            title_form = d["title"].draw()
            if {k for k in ("item", "region", "mob") if "{%s}" % k in title_form} <= subjects:
                break
        # {settlement} in an objective is somewhere else; {town} in a title is the
        # place posting the quest. Two names because they are two things.
        title = title_form.format(
            role=cap_words(role.split(" (")[0].split(" who")[0]), town=s_name,
            item=cap_words(item), mob=mob, region=mid_phrase(region),
            giver=giver.split()[0])
        reward = d["reward"].draw()
        if unlocks:
            reward += f"; unlocks stairwell {unlocks}"
        return Quest(title, giver, obj, d["flavor"].draw(), reward, unlocks)

    def build(self) -> Floor3:
        n = self.sc["grid"]
        center = (n // 2, n // 2)
        cells = [(r, c) for r in range(n) for c in range(n)]

        # Settlement placement: main at center, satellites spread out.
        settle_pos = {center: self.sc["main"]}
        far = [p for p in cells if abs(p[0] - center[0]) + abs(p[1] - center[1]) >= 2]
        self.rng.shuffle(far)
        for sz in self.sc["satellites"]:
            for pos in far:
                if pos in settle_pos:
                    continue
                if all(abs(pos[0] - q[0]) + abs(pos[1] - q[1]) >= 2 for q in settle_pos):
                    settle_pos[pos] = sz
                    break
            else:  # fallback: allow adjacency rather than drop a settlement
                for pos in far:
                    if pos not in settle_pos:
                        settle_pos[pos] = sz
                        break

        # Entry: random edge cell that is not a settlement.
        edges = [p for p in cells if (p[0] in (0, n - 1) or p[1] in (0, n - 1)) and p not in settle_pos]
        entry = self.rng.choice(edges)
        max_dist = max(abs(r - center[0]) + abs(c - center[1]) for r, c in cells) or 1

        grid = {}
        for i, (r, c) in enumerate(cells):
            label = chr(ord("A") + i)
            dist = abs(r - center[0]) + abs(c - center[1])   # distance from the hub
            if (r, c) in settle_pos:
                sz = settle_pos[(r, c)]
                counts = {"tiny": (1, 0, 1, 0), "small": (2, 1, 2, 0), "medium": (3, 2, 3, 0),
                          "large": (4, 3, 4, 0), "xl": (6, 5, 6, 1)}[sz]
                n_shop, n_rg, n_cg, club = counts
                grid[(r, c)] = Settlement(
                    idx=i, label=label, name=self.settlement_name(), size=sz, row=r, col=c,
                    race=self.rng.choice(NPC_RACES), guard_level=self.cfg["guard_level"][sz],
                    tutorial_guild=sz in ("medium", "large", "xl"),
                    shops=self.rng.sample(SHOPS, n_shop),
                    race_guilds=sorted(self.rng.sample(RACE_GUILDS, n_rg)),
                    class_guilds=sorted(self.rng.sample(CLASS_GUILDS, n_cg)),
                    club=bool(club), quests=[], distance=dist)
            else:
                after_dark, shift = night_threat(self.night_deck.draw())
                grid[(r, c)] = Region(
                    idx=i, label=label, name=self.region_name(), row=r, col=c,
                    mob=self.make_mob(), night=after_dark,
                    # A threat that takes the listed mob off the board for half the
                    # day leaves the region to something else, so that something
                    # gets rolled here rather than left to the DM.
                    second_mob=self.make_mob() if shift else None, second_shift=shift,
                    level_range=self.level_range(dist, max_dist),
                    loot=self.loot_deck.draw(), feature=self.feature_deck.draw(),
                    distance=dist, is_entry=(r, c) == entry)

        regions = [v for v in grid.values() if isinstance(v, Region)]
        settlements = [v for v in grid.values() if isinstance(v, Settlement)]

        def neighbors(cell):
            return [grid[(cell.row + dr, cell.col + dc)] for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1))
                    if (cell.row + dr, cell.col + dc) in grid]

        # Bosses: city boss farthest from hub, others random distinct regions.
        pool = regions[:]
        self.rng.shuffle(pool)
        if self.sc["city"]:
            far_reg = max(regions, key=lambda x: x.distance)
            far_reg.boss = self.make_boss("city", far_reg.mob, far_reg.level_range[1])
            pool.remove(far_reg)
        for tier, count in (("borough", self.sc["borough"]), ("neighborhood", self.sc["neighborhood"])):
            for _ in range(count):
                if not pool:
                    break
                reg = pool.pop()
                reg.boss = self.make_boss(tier, reg.mob, reg.level_range[1])
        for reg in regions:
            if reg.boss:
                adj = [x for x in neighbors(reg) if isinstance(x, Region)]
                reg.roams_to = self.rng.choice(adj).label if adj else None

        # Stairwells: distinct regions; condition depends on what's there.
        stairs = []
        cand = regions[:]
        self.rng.shuffle(cand)
        chosen = cand[: self.sc["stairs"]]
        region_names = [r.name for r in regions]
        mob_names = [r.mob.name for r in regions]
        settlement_names = [s.name for s in settlements]
        for k, reg in enumerate(chosen):
            slabel = f"S{k + 1}"
            roll = self.rng.random()
            if reg.boss and roll < 0.75:
                cond = f"guarded by {reg.boss.name} ({reg.boss.tier} boss, L{reg.boss.level}); opens on its death"
            elif roll < 0.8:
                st = min(settlements, key=lambda s_: abs(s_.row - reg.row) + abs(s_.col - reg.col))
                q = self.make_quest(st.name, region_names, settlement_names, mob_names, unlocks=slabel)
                st.quests.append(q)
                cond = f"locked; keyed to the quest \"{q.title}\" in {st.name}"
            else:
                cond = (f"locked; opens for a crawler holding the {self.rng.choice(BENEFIT_NAMES)} "
                        f"benefit (gamble — the benefit doesn't say which stairwell it fits)")
            sw = Stairwell(slabel, reg.label, cond)
            reg.stairwell = sw
            stairs.append(sw)

        # Ordinary quests.
        # Three times what this used to post. A large floor carries about thirty
        # quests across seven towns, which is why every component above is dealt
        # from a deck instead of rolled.
        qcount = {"tiny": 3, "small": 3, "medium": 6, "large": 6, "xl": 9}
        for st in settlements:
            while len(st.quests) < qcount[st.size]:
                st.quests.append(self.make_quest(st.name, region_names, settlement_names, mob_names))

        # Janitor.
        jan = self.make_mob()
        jan.quirk = self.rng.choice(JANITOR_MECHANICS) + " Emerge only at night."
        jan.description = "Janitor mob. " + jan.description

        # Elite.
        host = self.rng.choice(settlements)
        reg = self.rng.choice(regions)
        elite = Elite(
            name=f"{self.rng.choice(BOSS_FIRST)} {self.rng.choice(ELITE_TITLE)}",
            show=self.rng.choice(ELITE_SHOW).format(noun=singular(reg.mob.noun), adj=self.rng.choice(RUIN_ADJ)),
            form=self.rng.choice(ELITE_FORM).format(race=singular(host.race).lower()),
            plot_armor=self.rng.choice(PLOT_ARMOR), settlement=host.name, region=reg.name)

        days = self.days_override or self.rng.randint(*self.cfg["days"])
        name = f"The {self.rng.choice(RUIN_ADJ)} Over City"
        return Floor3(size=self.size, name=name, days=days, janitor=jan,
                      janitor_level=self.rng.randint(*self.cfg["janitor_level"]), elite=elite,
                      grid=n, cells=[grid[p] for p in cells], stairs=stairs,
                      scaling=self.scaling, seed=self.seed)

def render3(fl: Floor3) -> str:
    settlements = [c for c in fl.cells if isinstance(c, Settlement)]
    regions = [c for c in fl.cells if isinstance(c, Region)]
    entry = next(r for r in regions if r.is_entry)
    bosses = [r.boss for r in regions if r.boss]
    lo, hi = FLOOR3_CFG["mob_band"]
    o = []
    o.append(f"# Floor 3 — {fl.name} ({fl.size})")
    o.append("")
    o.append("## Summary")
    o.append("")
    o.append(f"- **Countdown:** {fl.days} days")
    o.append(f"- **Zones:** {len(settlements)} settlements, {len(regions)} wilderness regions")
    o.append(f"- **Bosses:** {sum(b.tier=='neighborhood' for b in bosses)} neighborhood, "
             f"{sum(b.tier=='borough' for b in bosses)} borough, {sum(b.tier=='city' for b in bosses)} city (all roam)")
    o.append(f"- **Stairwells:** {len(fl.stairs)}, all locked")
    for sw in fl.stairs:
        reg = next(r for r in regions if r.label == sw.region_label)
        o.append(f"  - {sw.label} in {reg.label} ({reg.name}): {sw.condition}")
    o.append(f"- **Wilderness mob levels:** {lo}–{hi} ({fl.scaling} scaling from the main settlement)")
    o.append("- **Day/night:** mobs roam at all hours but are far more dangerous at night; see each region's night line.")
    o.append(f"- **Janitor mob:** {fl.janitor.name} (Level {fl.janitor_level}). {fl.janitor.quirk}")
    o.append(f"  - *{fl.janitor.description}*")
    o.append(f"- **Law:** {LAW_TEXT}")
    o.append(f"- **Elite:** {fl.elite.name} — star of *{fl.elite.show}*. {fl.elite.form[0].upper() + fl.elite.form[1:]}. "
             f"Based in {fl.elite.settlement}; storyline plays out in {fl.elite.region}. {fl.elite.plot_armor}")
    o.append(f"- **Entry point:** {entry.label} — {entry.name} ({compass(entry.row, entry.col, fl.grid)})")
    o.append(f"- **Seed:** {fl.seed}")
    o.append("")
    o.append("## Layout")
    o.append("")
    o.append("Grid, north at top. `#` = settlement, `*` = entry, `S` = stairwell, `!` = boss.")
    o.append("")
    o.append("```")
    for r in range(fl.grid):
        row = []
        for c in fl.cells[r * fl.grid:(r + 1) * fl.grid]:
            tag = c.label
            if isinstance(c, Settlement):
                tag += "#"
            else:
                if c.is_entry: tag += "*"
                if c.stairwell: tag += "S"
                if c.boss: tag += "!"
            row.append(f"[{tag:<4}]")
        o.append(" ".join(row))
    o.append("```")
    o.append("")

    o.append("## Settlements")
    o.append("")
    for st in settlements:
        o.append(f"### {st.label}. {st.name} ({st.size} {st.race.lower()} settlement)")
        o.append("")
        o.append(f"- **Location:** {compass(st.row, st.col, fl.grid)}. Guards: Level {st.guard_level}. "
                 f"Safe-room inn present{'; tutorial guild hall' if st.tutorial_guild else ''}"
                 f"{'; nightclub access (Desperado-style)' if st.club else ''}.")
        o.append(f"- **Shops:** {', '.join(st.shops)}")
        o.append(f"- **Race guildhalls:** {', '.join(st.race_guilds) if st.race_guilds else 'none'}")
        o.append(f"- **Class guildhalls:** {', '.join(st.class_guilds)}")
        o.append("- **Quests:**")
        for q in st.quests:
            o.append(f"  - **{q.title}** — from {q.giver}. {q.objective} Reward: {q.reward}.")
            o.append(f"    - *{q.flavor}*")
        o.append("")

    o.append("## Wilderness Regions")
    o.append("")
    for reg in regions:
        adj = [c.label for c in fl.cells if abs(c.row - reg.row) + abs(c.col - reg.col) == 1]
        o.append(f"### {reg.label}. {reg.name}")
        o.append("")
        o.append(f"- **Location:** {compass(reg.row, reg.col, fl.grid)}. Adjacent to {', '.join(adj)}. "
                 f"Distance from hub: {reg.distance}{' (ENTRY)' if reg.is_entry else ''}.")
        o.append(f"- **Mob:** {reg.mob.name}, Level {reg.level_range[0]}–{reg.level_range[1]}")
        o.append(f"  - {reg.mob.quirk}")
        o.append(f"  - Night: {reg.night}")
        o.append(f"  - *{reg.mob.description}*")
        o += second_mob_lines(reg)
        if reg.boss:
            b = reg.boss
            o.append(f"- **{b.tier.title()} Boss:** {b.name} (Level {b.level})")
            o.append(f"  - Form: {b.form}.")
            o.append(f"  - Roams between here and {reg.roams_to}." if reg.roams_to else "  - Stays in this region.")
            o.append(f"  - Mechanic: {b.mechanic}")
            o.append(f"  - Drops: a {boss_box(b.tier, fl.number)}")
        if reg.stairwell:
            o.append(f"- **Stairwell {reg.stairwell.label}:** {reg.stairwell.condition}.")
        o.append(f"- **Loot theme:** {reg.loot}")
        o.append(f"- **Notable feature:** {reg.feature}")
        o.append("")
    o.extend(crawler_section(fl))
    return "\n".join(o)

def render3_svg(fl: Floor3) -> str:
    n = fl.grid
    W = max(PAD * 2 + CELL * n, 800)
    stairs_lines = len(fl.stairs)
    H = HEADER + PAD + CELL * n + LEGEND + 18 * (stairs_lines + 1) + PAD
    ox, oy = (W - CELL * n) // 2, HEADER + PAD
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="Helvetica, Arial, sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#f6f2ea"/>',
         _text(PAD, 32, f"Floor 3 — {fl.name}", size=22, weight="bold")]
    lo, hi = FLOOR3_CFG["mob_band"]
    o.append(_text(PAD, 54, f"{fl.days} days  •  wilderness mobs L{lo}–{hi} ({fl.scaling})  •  janitor: "
                   f"{fl.janitor.name} L{fl.janitor_level}  •  seed {fl.seed}", size=12, fill="#555"))
    for c in fl.cells:
        x, y = ox + c.col * CELL, oy + c.row * CELL
        if isinstance(c, Settlement):
            o.append(f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" fill="#f2dfa8" stroke="#8a5a1a" stroke-width="5"/>')
            o.append(_text(x + 10, y + 26, c.label, size=24, weight="bold"))
            o.append(_text(x + 42, y + 24, _fit(c.name, 22), size=13, weight="bold"))
            o.append(_text(x + 10, y + 48, f"{c.size.upper()} settlement — {c.race}", size=12))
            o.append(_text(x + 10, y + 64, f"Guards L{c.guard_level}  •  inn" + ("  •  guild" if c.tutorial_guild else "")
                           + ("  •  club" if c.club else ""), size=11, fill="#444"))
            o.append(_text(x + 10, y + 82, "Shops: " + _fit(", ".join(c.shops), 32), size=11, fill="#444"))
            o.append(_text(x + 10, y + 98, f"Quests: {len(c.quests)}", size=11, fill="#444"))
            if any(q.unlocks for q in c.quests):
                o.append(_text(x + 10, y + 114, "Unlocks: " + ", ".join(q.unlocks for q in c.quests if q.unlocks),
                               size=11, weight="bold", fill="#8a5a1a"))
            if c.name == fl.elite.settlement:
                o.append(_text(x + CELL - 10, y + CELL - 12, "ELITE", size=13, weight="bold", anchor="end", fill="#7a1f7a"))
        else:
            o.append(f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" fill="#d9d6cf" stroke="#555" stroke-width="1.5"/>')
            o.append(_text(x + 10, y + 26, c.label, size=24, weight="bold"))
            o.append(_text(x + 42, y + 24, _fit(c.name, 22), size=13, weight="bold"))
            o.append(_text(x + 10, y + 48, _fit(c.mob.name, 30), size=12))
            o.append(_text(x + 10, y + 64, f"Level {c.level_range[0]}–{c.level_range[1]}", size=12))
            if c.boss:
                o.append(_text(x + 10, y + 84, f"{c.boss.tier.title()} boss: " + _fit(c.boss.name, 18), size=11, fill="#444"))
                roam = f"roams → {c.roams_to}" if c.roams_to else "stays here"
                o.append(_text(x + 10, y + 99, f"L{c.boss.level}, {roam}", size=11, fill="#444"))
                o.extend(_boss_marker(x + CELL - 30, y + CELL - 30, c.boss.tier, ""))
            if c.stairwell:
                o.append(_stair_icon(x + 12, y + CELL - 26))
                o.append(_text(x + 32, y + CELL - 12, f"{c.stairwell.label} (locked)", size=11, weight="bold"))
            if c.is_entry:
                o.append(f'<rect x="{x+3}" y="{y+3}" width="{CELL-6}" height="{CELL-6}" fill="none" stroke="#c0392b" stroke-width="4"/>')
                o.append(_text(x + CELL // 2, y + CELL - 12, "ENTRY", size=14, weight="bold", anchor="middle", fill="#c0392b"))
    ly = oy + CELL * n + 30
    lx = PAD
    o.extend(_boss_marker(lx + 13, ly, "neighborhood", "")); o.append(_text(lx + 32, ly + 4, "Neighborhood boss", size=11))
    o.extend(_boss_marker(lx + 170, ly, "borough", "")); o.append(_text(lx + 198, ly + 4, "Borough boss", size=11))
    o.extend(_boss_marker(lx + 320, ly, "city", "")); o.append(_text(lx + 356, ly + 4, "City boss", size=11))
    o.append(_stair_icon(lx + 440, ly - 8)); o.append(_text(lx + 462, ly + 4, "Locked stairwell", size=11))
    o.append(f'<rect x="{lx+580}" y="{ly-9}" width="18" height="18" fill="#f2dfa8" stroke="#8a5a1a" stroke-width="3"/>')
    o.append(_text(lx + 604, ly + 4, "Settlement", size=11))
    o.append(f'<rect x="{lx+690}" y="{ly-9}" width="18" height="18" fill="none" stroke="#c0392b" stroke-width="3"/>')
    o.append(_text(lx + 714, ly + 4, "Entry", size=11))
    ky = ly + 52
    for sw in fl.stairs:
        o.append(_text(lx, ky, f"{sw.label} ({sw.region_label}): {_fit(sw.condition, 110)}", size=12)); ky += 18
    o.append(_text(lx, ky, f"Elite: {fl.elite.name} — {fl.elite.show} (based in {fl.elite.settlement})", size=12, fill="#7a1f7a"))
    o.append("</svg>")
    return "\n".join(o)

# ===========================================================================
# FLOOR 4 — THE IRON TANGLE
# Colored rail lines from trainyards (ring 10) toward the Abyss (436).
# ===========================================================================

FLOOR4_CFG = {
    "days": 10,                    # this season; the 'normal' allotment is 15
    "normal_days": 15,
    "mob_band": (15, 30),
    "janitor_level": (16, 20),
    "stairwell_numbers": [12, 24, 36, 48, 72],
    "spawn_range": (73, 140),
    "mimic_station": 433,
    "employee_portal": 435,
    "abyss": 436,
    "yard_ring": 10,
    "mimic_level": 80,
    # What every mob and crew member on the floor is hooked on, and what aboard
    # call it.
    "drug": "Rev-Up Amazing Cure-All Vitamin Immunity Shot",
    "drug_short": "Rev-Up",
    # The interdiction depot, where the repair carts wait for a derailment; the
    # books put it at 75 and its carts at five times a train's speed.
    "depot": 75,
    "cart_speed": 5,
    # Getting around. The books give the anchors: a stop every 2 to 20 minutes,
    # about three days to ride from 11 to 435, stations a block or two apart up
    # to 72, and 40 or 50 miles apart out past 300. The curves between them are
    # ours: minutes per stop climb evenly from the first figure to the second,
    # and the spacing holds at block_miles up to 72, then grows by the same
    # factor each station until it reaches far_miles at far_station.
    "ride_minutes": (2, 18),
    "block_miles": 0.15,
    "far_station": 300,
    "far_miles": 45,
    "walk_mph": 3,
}

SIZE4_CFG = {
    "small":  {"lines": 2, "named": 1, "hubs": 1},
    "medium": {"lines": 4, "named": 2, "hubs": 1},
    "large":  {"lines": 7, "named": 3, "hubs": 2},
}

F4_RULES = [
    "No early descent. Stairwells open six hours before collapse and not before.",
    "Every colored line has a stairwell at stations 12, 24, 36, 48 and 72, and nowhere else. Crawlers spawn past 72, so the game is getting backwards.",
    "A transfer station has a safe room only if its number is prime (11, 13, 17 and so on). Transfers ending in 1 also have a Desperado Club; those ending in 9, a Club Vanquisher.",
    "Exit-only caverns sit at every fifth station. Mobs disembark there for their Rev-Up shot from the supplier; a rollercoaster track at the back runs to the trainyard.",
    "A train can be derailed, and a steam engine with nobody tending it builds pressure until it blows. Either one brings the interdiction crews out of station 75.",
    "Control sigils are glowing circles that stop one kind of mob from passing. Only a crawler with a knack for finding the way out can see them. The showrunners switch them on and off, not the dungeon, and they break easily.",
    "Mobs get off every five stops. A mob that misses its stop panics; it cannot stay aboard and live. They break into cars 10 and 15, never car 5.",
    "Trains reset to the trainyard (ring 10) after every run. Employees remember 435, then blink, then they are boarding again.",
]


def _is_prime(n: int) -> bool:
    return n > 1 and all(n % d for d in range(2, int(n ** 0.5) + 1))


# -- getting around the Iron Tangle ------------------------------------------------
# Station n's "gap" is the stretch from n to n + 1. Trains run one way only, from
# the yard toward the Abyss, so a ride always goes up the numbers.

def f4_gap_minutes(n: int) -> float:
    """Minutes a colored-line train takes from station n to n + 1, stop included."""
    c = FLOOR4_CFG
    lo, hi = c["ride_minutes"]
    first, last = c["yard_ring"] + 1, c["employee_portal"] - 1
    return lo + (hi - lo) * (min(max(n, first), last) - first) / (last - first)


def f4_gap_miles(n: int) -> float:
    """How far apart stations n and n + 1 are."""
    c = FLOOR4_CFG
    edge = c["stairwell_numbers"][-1]
    if n < edge:
        return c["block_miles"]
    growth = (c["far_miles"] / c["block_miles"]) ** (1 / (c["far_station"] - edge))
    # Held at far_miles past far_station: left to grow, the last gap before the
    # Abyss would be over a thousand miles.
    return c["block_miles"] * growth ** (min(n, c["far_station"]) - edge)


def f4_ride_minutes(a: int, b: int) -> float:
    """Riding a colored line from station a to station b, a before b."""
    return sum(f4_gap_minutes(n) for n in range(a, b))


def f4_walk_miles(a: int, b: int) -> float:
    """The tunnel between two stations, either way round."""
    lo, hi = sorted((a, b))
    return sum(f4_gap_miles(n) for n in range(lo, hi))


def f4_walk_minutes(a: int, b: int) -> float:
    return f4_walk_miles(a, b) / FLOOR4_CFG["walk_mph"] * 60


def duration_text(minutes: float) -> str:
    """"about 25 minutes", "about 7 hours", "about 2 days and 18 hours"."""
    m = round(minutes)
    if m < 60:
        n = max(5, int(5 * round(m / 5)))
        return f"about {n} minutes"
    hours = round(m / 60)
    if hours < 24:
        return f"about {hours} hour{'s' if hours != 1 else ''}"
    days, hours = divmod(hours, 24)
    text = f"about {days} day{'s' if days != 1 else ''}"
    return text + (f" and {hours} hour{'s' if hours != 1 else ''}" if hours else "")


@dataclass
class Station:
    number: int
    kind: str                     # spawn | stairwell | transfer | exit_cavern
    note: str
    transfer_to: List[str] = field(default_factory=list)
    has_stairwell: bool = False
    safe_room: bool = False
    hub_lines: int = 0            # a hub: how many lines meet here, most not detailed

    # What a transfer station has follows from its number: a safe room if it is
    # prime, a Desperado Club if it ends in 1, a Club Vanquisher if it ends in 9.
    # Only transfers have any of them. A hub is dealt, not numbered.
    @property
    def desperado(self) -> bool:
        return bool(self.transfer_to) and self.number % 10 == 1

    @property
    def vanquisher(self) -> bool:
        return bool(self.transfer_to) and self.number % 10 == 9

    def amenities(self) -> List[str]:
        return ([name for name, has in (("safe room", self.safe_room),
                                        ("Desperado Club", self.desperado),
                                        ("Club Vanquisher", self.vanquisher)) if has]
                + ([f"hub of {self.hub_lines} lines"] if self.hub_lines else []))


@dataclass
class RailLine:
    color: str
    hex: str
    yard: str
    engineer: str
    engineer_species: str
    line_mob: Mob
    roster: List[str]
    withdrawal: str               # how this line's crew and mobs take it
    spawn: int
    stations: List[Station]
    stage2_day: int = 0           # the day withdrawal reaches stage 2 on this line
    stage3_day: int = 0
    mob_levels: Tuple[int, int] = (0, 0)   # the line mob's band, as its roster line gives it


@dataclass
class NamedTrain:
    name: str
    stock: str
    interval: int
    cars: int
    stops: List[Tuple[str, int]]   # (line color, station number)
    entry: str
    gimmick: str
    crew: Mob
    # The quest boarding starts: title, objective, minutes, reward, failure.
    quest: Optional[dict] = None


@dataclass
class Floor4:
    size: str
    name: str
    days: int
    janitor: Mob
    janitor_level: int
    lines: List[RailLine]
    named: List[NamedTrain]
    mimic_tell: str
    story: List[str]
    seed: int
    number: int = 4
    other_crawlers: List[CrawlerTeam] = field(default_factory=list)
    # One pick per withdrawal stage: {"symptom", "toll", "risen"}, from WITHDRAWAL.
    withdrawal: List[dict] = field(default_factory=list)
    last_day: List[str] = field(default_factory=list)
    stair_notes: Dict[int, str] = field(default_factory=dict)   # by station number
    # The five stairwell stations, each where one kind of ghoul gathers: number,
    # name, ghoul {name, level, does, from}, feed, tide, opening, and at one of
    # them a merged boss.
    ghoul_stations: List[dict] = field(default_factory=list)
    # Safe rooms are businesses: number, lines, business {name, what, offers},
    # proprietor, quest. Hubs: number, lines, n_lines, shops, guilds, sight, quest.
    safe_rooms: List[dict] = field(default_factory=list)
    hubs: List[dict] = field(default_factory=list)
    quests: List[Quest] = field(default_factory=list)      # every station quest
    achievements: List[list] = field(default_factory=list)
    supplier: dict = field(default_factory=dict)       # who hands out the shots
    hr: dict = field(default_factory=dict)             # the non-combatants at 435
    depot: dict = field(default_factory=dict)          # station 75's repair carts
    mimic_spawn: dict = field(default_factory=dict)    # what the Station Mimic belches up
    yards: dict = field(default_factory=dict)          # supervisor, and a feature per yard
    special_lines: dict = field(default_factory=dict)  # the backward line and the staff line


class Generator4:
    FLOOR = 4

    def __init__(self, size, seed, days):
        self.rng = random.Random(f"floor{self.FLOOR}|{seed}")
        self.seed = seed
        self.size = size
        self.cfg = FLOOR4_CFG
        self.sc = SIZE4_CFG[size]
        self.days_override = days
        self.used = set()
        self.mech_decks = make_mechanic_decks(self.rng)

    def _unique(self, fn):
        for _ in range(100):
            v = fn()
            if v not in self.used:
                break
        self.used.add(v)
        return v

    def make_mob(self) -> Mob:
        adj, noun = self._unique(lambda: mob_words(self.rng, 4))
        return describe_mob(self.rng, adj, noun)

    def build(self) -> Floor4:
        n_lines = self.sc["lines"]
        colors = self.rng.sample(list(LINE_COLORS), n_lines)
        lo, hi = self.cfg["mob_band"]
        c = self.cfg
        days = self.days_override or c["days"]
        # Each line declines at its own pace, as a fraction of the countdown, so
        # a five-day floor gets a five-day decline rather than stages dated past
        # the collapse.
        paces = _pick_distinct(self.rng, WITHDRAWAL["pace"], n_lines)

        lines: List[RailLine] = []
        for color, pace in zip(colors, paces):
            mob = self.make_mob()
            spawn = self.rng.randint(*c["spawn_range"])
            lvl_a = self.rng.randint(lo, hi - 5)
            roster = [
                f"Porters and conductors (L{lvl_a}–{lvl_a + 4}), board at {c['yard_ring']}",
                f"{mob.name} (L{lvl_a + 2}–{lvl_a + 7}), board at {self.rng.choice([15, 20, 25, 30])}",
                "Janitor ghouls, board at 12",
                "Engineer, aboard from the yard; only leaves the cab for a derailment",
            ]
            # Stage 3 by the last day at the latest, and stage 2 at least a day
            # before it, however short the countdown.
            stage3 = min(days, max(3, math.ceil(days * pace["stage3"])))
            stage2 = max(1, min(stage3 - 1, max(2, math.ceil(days * pace["stage2"]))))
            lines.append(RailLine(
                color=color, hex=LINE_COLORS[color], yard=self.rng.choice("ABCDEFGHIJKLMNOPQ"),
                engineer=f"{self.rng.choice(BOSS_FIRST)} the {self.rng.choice(['7th', 'Elder', 'Unblinking', 'Punctual', 'Second-Shift', 'Late'])}",
                engineer_species=self.rng.choice(ENGINEER_SPECIES),
                line_mob=mob, roster=roster, withdrawal=pace["text"],
                spawn=spawn, stations=[], stage2_day=stage2, stage3_day=stage3,
                mob_levels=(lvl_a + 2, lvl_a + 7)))

        # The five stairwell stations are the same five numbers on every line, so
        # each gets one note for the floor rather than one per line.
        stairs = c["stairwell_numbers"]
        stair_notes = dict(zip(stairs, _pick_distinct(self.rng, STATION_FLAVOR["stairwell"],
                                                      len(stairs))))

        stmap = {ln.color: {} for ln in lines}
        # A transfer is one station on two lines, so one note: the safe room at
        # 103 is the same safe room whichever line the party arrives on. Dealt
        # from decks, so two transfers on a floor differ while the bank lasts.
        transfer_notes = {}
        transfer_deck = Deck(self.rng, STATION_FLAVOR["transfer"])
        # A safe room is a business: the station's note names it, and the Safe
        # Rooms and Hubs section has its proprietor, what it offers, and a quest.
        business_deck = Deck(self.rng, SAFE_ROOMS4["businesses"])
        businesses = {}

        def transfer_note(number):
            if number not in transfer_notes:
                if _is_prime(number):
                    b = businesses[number] = business_deck.draw()
                    transfer_notes[number] = f"Safe room: {b['name']}, {b['what']}."
                else:
                    transfer_notes[number] = transfer_deck.draw()
            return transfer_notes[number]

        def add(ln, number, kind, **kw):
            d = stmap[ln.color]
            if number in d:
                st = d[number]
                if kind == "transfer":
                    st.transfer_to.extend(kw.get("transfer_to", []))
                    st.safe_room = _is_prime(number)
                    # A transfer landing on a line's spawn platform: if it makes
                    # a safe room, the business is what that line should say too.
                    if st.safe_room:
                        st.note = transfer_note(number)
                return st
            if kind == "stairwell":
                note = stair_notes[number]
            elif kind == "transfer":
                note = transfer_note(number)
            else:
                note = self.rng.choice(STATION_FLAVOR[kind]) if kind in STATION_FLAVOR else ""
            st = Station(number, kind, note, list(kw.get("transfer_to", [])),
                         has_stairwell=(kind == "stairwell"),
                         safe_room=(kind == "transfer" and _is_prime(number)))
            d[number] = st
            return st

        for ln in lines:
            add(ln, ln.spawn, "spawn")
            for num in stairs:
                add(ln, num, "stairwell")
            cav = ((ln.spawn // 5) + 1) * 5
            add(ln, cav, "exit_cavern")
            add(ln, self.rng.choice([15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70]), "exit_cavern")

        # Transfers: spanning path + extras so the network is connected. Never on
        # a multiple of five (the exit caverns) or a stairwell station. The first
        # is always prime: a safe room is only ever at a prime transfer, and the
        # lines a floor details should share at least one.
        order = lines[:]
        self.rng.shuffle(order)
        pairs = [(order[i], order[i + 1]) for i in range(len(order) - 1)]
        extra = max(0, n_lines - 2)
        for _ in range(extra):
            a, b = self.rng.sample(lines, 2)
            pairs.append((a, b))
        for i, (a, b) in enumerate(pairs):
            top = min(max(a.spawn, b.spawn) + 40, 420)
            choices = [n for n in range(11, top + 1)
                       if n % 5 and n not in stairs and (i or _is_prime(n))]
            num = self.rng.choice(choices)
            add(a, num, "transfer", transfer_to=[b.color])
            add(b, num, "transfer", transfer_to=[a.color])

        # Most stations on the Iron Tangle are transfers, to far more lines than a
        # floor details, and a prime one is a safe room. So each line also meets
        # lines the floor does not detail: at a prime in each stretch a party uses
        # -- the yard to the stairwells, the stairwells to its spawn, and ahead of
        # that -- unless it already has a safe room there. At a number no other
        # line here uses, where there is one: two lines at the same number read as
        # one station. Where there is not, it meets the line that has it.
        undetailed = Deck(self.rng, [col for col in LINE_COLORS if col not in colors])
        edge = stairs[-1]
        for ln in lines:
            mine = stmap[ln.color]
            top = min(ln.spawn + 40, 420)
            for first, last in ((11, edge - 1), (edge + 1, ln.spawn - 1), (ln.spawn + 1, top)):
                primes = [n for n in range(first, last + 1) if _is_prime(n)]
                if not primes or any(n in mine for n in primes):
                    continue
                taken = {n for d in stmap.values() for n in d}
                free = [n for n in primes if n not in taken]
                if free:
                    add(ln, self.rng.choice(free), "transfer", transfer_to=[undetailed.draw()])
                    continue
                num = self.rng.choice(primes)
                other = next(o for o in lines if num in stmap[o.color])
                add(ln, num, "transfer", transfer_to=[other.color])
                add(other, num, "transfer", transfer_to=[ln.color])
        for ln in lines:
            ln.stations = sorted(stmap[ln.color].values(), key=lambda s_: s_.number)

        # Hubs: transfers where many lines meet -- far more than the floor details --
        # with shops and guilds. Not a safe room where there is a choice: a hub
        # is a concourse, and a safe room is somebody's business.
        transfers = sorted({st.number for ln in lines for st in ln.stations if st.transfer_to})
        plain = [n for n in transfers if not _is_prime(n)]
        hub_numbers = sorted(self.rng.sample(plain, min(self.sc["hubs"], len(plain)))
                             if plain else transfers[:1])
        hubs = []
        lo_lines, hi_lines = HUBS4["lines"]
        for num in hub_numbers:
            through = [ln.color for ln in lines
                       if any(st.number == num and st.transfer_to for st in ln.stations)]
            n_lines = self.rng.randint(max(lo_lines, len(through)), hi_lines)
            for ln in lines:
                for st in ln.stations:
                    if st.number == num and st.transfer_to:
                        st.hub_lines = n_lines
            hubs.append({"number": num, "lines": through, "n_lines": n_lines,
                         "shops": self.rng.sample(HUBS4["shops"], 2),
                         "guilds": self.rng.sample(HUBS4["guilds"], self.rng.randint(1, 2)),
                         "sight": self.rng.choice(HUBS4["sights"])})

        # Named trains: loops over 3-4 detailed stations on 2+ lines. Not the
        # stairwell stations: every line has all five, and a loop of them would
        # crowd out everything else.
        named = []
        # Every named train starts a quest when boarded, none the same on a floor.
        train_quests = Deck(self.rng, TRAIN_QUESTS4["quests"])
        for _ in range(self.sc["named"]):
            name = self._unique(lambda: f"{self.rng.choice(NAMED_PREFIX)} {self.rng.choice(NAMED_SUFFIX)}")
            k = self.rng.randint(3, 4)
            stops = []
            pool = [(ln.color, st.number) for ln in lines for st in ln.stations
                    if st.kind != "stairwell"]
            self.rng.shuffle(pool)
            seen_lines = set()
            for color, num in pool:
                if len(stops) >= k:
                    break
                if color in seen_lines and len(seen_lines) < min(2, n_lines):
                    continue
                stops.append((color, num))
                seen_lines.add(color)
            if self.rng.random() < 0.5:
                stops.append(("Abyss", c["abyss"]))
            crew = self.make_mob()
            tq = train_quests.draw()
            minutes = self.rng.randint(*TRAIN_QUESTS4["minutes"])
            quest = {"title": tq["title"], "minutes": minutes,
                     "objective": tq["objective"].format(minutes=minutes, crew=crew.name),
                     "reward": tq["reward"],
                     "failure": tq["failure"].format(minutes=minutes, crew=crew.name)}
            named.append(NamedTrain(
                name=name, stock=self.rng.choice(NAMED_STOCK),
                interval=self.rng.choice([30, 45, 48, 60, 75, 90, 120]),
                cars=self.rng.choice([2, 3, 6, 12, 24, 38, 40]),
                stops=stops, entry=self.rng.choice(NAMED_ENTRY),
                gimmick=self.rng.choice(NAMED_GIMMICK), crew=crew, quest=quest))

        jan = self.make_mob()
        jan.quirk = self.rng.choice(JANITOR_MECHANICS) + " Board at station 12 and ride to 435."
        jan.description = "Janitor mob. " + jan.description
        story = self.rng.sample(F4_STORY, 2)
        # Withdrawal for the whole floor: one symptom, toll and risen form per
        # stage, and two things that happen on the last day.
        withdrawal = [{"symptom": self.rng.choice(st["symptoms"]),
                       "toll": self.rng.choice(st["toll"]),
                       "risen": self.rng.choice(st["risen"])}
                      for st in WITHDRAWAL["stages"]]
        last_day = self.rng.sample(WITHDRAWAL["last_day"], 2)
        janitor_level = self.rng.randint(*c["janitor_level"])
        ghoul_stations = self._ghoul_stations(stairs, stair_notes, days, jan, janitor_level,
                                              withdrawal)

        # Each safe room's proprietor has a quest, and so does each hub's
        # noticeboard. Built last, since a quest can send the party to a ghoul
        # station.
        decks = self._quest_decks()
        owners = Deck(self.rng, SAFE_ROOMS4["proprietors"])
        safe_rooms, quests = [], []
        for num in sorted(businesses):
            owner = f"{owners.draw()}, {self.rng.choice(SAFE_ROOMS4['species'])}"
            q = self._station_quest(num, owner.split(",")[0], lines, ghoul_stations, decks)
            through = [ln.color for ln in lines
                       if any(st.number == num and st.transfer_to for st in ln.stations)]
            others = sorted({col for ln in lines for st in ln.stations if st.number == num
                             for col in st.transfer_to} - set(colors))
            safe_rooms.append({"number": num, "lines": through, "others": others,
                               "business": businesses[num], "proprietor": owner, "quest": q})
            quests.append(q)
        for hub in hubs:
            hub["quest"] = self._station_quest(hub["number"], "the station noticeboard", lines,
                                               ghoul_stations, decks)
            quests.append(hub["quest"])

        # The floor's systems: who hands out the shots, who meets the crew at 435,
        # the repair depot at 75, the mimic's spawn, the yards, the special lines.
        depot = {"carts": self.rng.randint(*DEPOT4["carts"]),
                 "repair": self.rng.choice(DEPOT4["repair_crews"]),
                 "security": self.rng.choice(DEPOT4["security"]),
                 "controls": self.rng.choice(DEPOT4["controls"]),
                 "state": self.rng.choice(DEPOT4["states"])}
        yard_letters = sorted({ln.yard for ln in lines})
        yards = {"supervisor": dict(self.rng.choice(YARDS4["supervisors"])),
                 "features": dict(zip(yard_letters, self.rng.sample(YARDS4["features"],
                                                                    len(yard_letters))))}
        special = {k: self.rng.choice(SPECIAL_LINES4[k])
                   for k in ("backward", "backward_how", "staff", "staff_how")}

        return Floor4(size=self.size, name=f"The {self.rng.choice(NAMED_PREFIX)} Tangle", days=days,
                      janitor=jan, janitor_level=janitor_level,
                      lines=lines, named=named, mimic_tell=self.rng.choice(MIMIC_TELL),
                      story=story, seed=self.seed, withdrawal=withdrawal,
                      last_day=last_day, stair_notes=stair_notes,
                      ghoul_stations=ghoul_stations, safe_rooms=safe_rooms, hubs=hubs,
                      quests=quests, achievements=[list(a) for a in ACHIEVEMENTS4],
                      supplier=dict(self.rng.choice(SUPPLIERS4)), hr=dict(self.rng.choice(HR4)),
                      depot=depot, mimic_spawn=dict(self.rng.choice(MIMIC_MINIONS4)),
                      yards=yards, special_lines=special)

    def _quest_decks(self) -> dict:
        """What every station quest on a floor is dealt from, and the titles the
        floor has used."""
        sq = STATION_QUESTS4
        return {"objective": Deck(self.rng, sq["objectives"]), "item": Deck(self.rng, sq["items"]),
                "title": Deck(self.rng, sq["titles"]), "reward": Deck(self.rng, sq["rewards"]),
                "flavor": Deck(self.rng, sq["flavor"]), "titled": set()}

    def _station_quest(self, number, giver, lines, ghoul_stations, decks) -> Quest:
        """A quest given at a station: by a safe room's proprietor, or posted on a
        hub's noticeboard. It sends the party along a line that stops there."""
        ln = self.rng.choice([ln for ln in lines
                              if any(st.number == number and st.transfer_to
                                     for st in ln.stations)])
        elsewhere = [st.number for st in ln.stations
                     if st.number != number and st.kind != "stairwell"]
        caverns = [st.number for st in ln.stations if st.kind == "exit_cavern"]
        gs = self.rng.choice(ghoul_stations)
        item = decks["item"].draw()
        form = decks["objective"].draw()
        objective = form.format(item=item, line=ln.color,
                                station=f"station {self.rng.choice(elsewhere)}",
                                mob=ln.line_mob.name, cavern=self.rng.choice(caverns),
                                ghoul_station=gs["name"], ghoul_number=gs["number"])
        # A title names the item only if the objective has one, and a noticeboard
        # is not somebody a favor can be for. No two quests on a floor share one: a
        # title skipped for one quest is still in the deck when it reshuffles,
        # behind titles already used. Two decks' worth of draws always takes in
        # one whole pass, so a title is reused only when every one that fits is.
        bare = re.sub(r"^(?:a|an|the)\s+", "", item)
        title = None
        for _ in range(2 * len(decks["title"].items)):
            title_form = decks["title"].draw()
            if "{Item}" in title_form and "{item}" not in form:
                continue
            if "{giver}" in title_form and giver.startswith("the "):
                continue
            title = title_form.format(giver=giver, line=ln.color, Item=cap_words(bare))
            if title not in decks["titled"]:
                break
        decks["titled"].add(title)
        return Quest(title, giver, objective, decks["flavor"].draw(), decks["reward"].draw())

    def _ghoul_stations(self, stairs, stair_notes, days, jan, janitor_level, withdrawal):
        """Who gathers at each of the five stairwell stations, and what it means.

        Station 12 is the janitors': they board there already. The other four
        hold what each stage of withdrawal raises, and one ghoul from the bank, in
        an order dealt per floor. At one of those four, enough of them together
        merge into a boss."""
        gs = GHOUL_STATIONS
        others = [dict(w["risen"], source=f"what stage {n} of withdrawal raises")
                  for n, w in enumerate(withdrawal, start=1)]
        others.append(dict(self.rng.choice(gs["ghouls"]), source="made here, not by withdrawal"))
        self.rng.shuffle(others)
        ghouls = [{"name": jan.name, "level": [janitor_level, janitor_level],
                   "does": "they board here, ride to 435, and clean up whatever the floor "
                           "leaves lying about",
                   "source": "the floor's janitors"}] + others
        early = max(1, round(days * 0.3))
        mid = max(early, round(days * 0.5))
        late = max(mid, days - 1)
        names = self.rng.sample(gs["names"], len(stairs))
        feeds = self.rng.sample(gs["feeds"], len(stairs))
        tides = self.rng.sample(gs["tides"], len(stairs))
        openings = self.rng.sample(gs["openings"], len(stairs))
        sigils = self.rng.sample(SIGILS4, len(stairs))
        merge_at = self.rng.choice(stairs[1:])
        merged = self.rng.choice(gs["merged"])
        stations = []
        for i, num in enumerate(stairs):
            stations.append({
                "number": num, "name": names[i], "stair": stair_notes[num],
                "ghoul": ghouls[i], "feed": feeds[i],
                "tide": tides[i].format(early=early, mid=mid, late=late),
                "opening": openings[i],
                "sigil": sigils[i].format(ghoul=ghouls[i]["name"]),
                "merged": merged if num == merge_at else None})
        return stations


def _miles_text(miles: float) -> str:
    if miles < 1:
        return "under a mile"
    n = round(miles)
    return f"{n} mile{'s' if n != 1 else ''}"


def _lines_text(colors: List[str]) -> str:
    if len(colors) == 1:
        return f"{colors[0]} Line"
    return f"{', '.join(colors[:-1])} and {colors[-1]} Lines"


def _quest_lines(q: Quest, label: str) -> List[str]:
    return [f"- **{label} — {q.title}.** {q.objective} Reward: {q.reward}.",
            f"  - *{q.flavor}*"]


def _safe_rooms_and_hubs4(fl: Floor4) -> List[str]:
    """The safe rooms the detailed lines pass, each somebody's business, and the
    hubs where far more lines meet than the floor details."""
    o = ["## Safe Rooms and Hubs", "",
         "A safe room on the Iron Tangle is somebody's business, and its proprietor always "
         "has something that needs doing. Only a prime-numbered transfer has one, and most "
         "stations are transfers, so every line here has at least one between the yard and "
         "the stairwells, one between the stairwells and where its crawlers start, and one "
         "ahead of that. A hub is where far more lines meet than this floor details: a "
         "concourse with shops and guilds, and a noticeboard.", ""]
    for sr in fl.safe_rooms:
        b = sr["business"]
        st = next(s for ln in fl.lines for s in ln.stations
                  if s.number == sr["number"] and s.transfer_to)
        also = [a for a in st.amenities() if a != "safe room"]
        change = f"; change for the {_lines_text(sr['others'])}" if sr["others"] else ""
        o.append(f"### {sr['number']} — {b['name']} ({_lines_text(sr['lines'])}{change})")
        o.append("")
        o.append(f"- **What it is:** {b['what']}."
                 + (f" Also here: {', '.join(also)}." if also else ""))
        o.append(f"- **Proprietor:** {sr['proprietor']}.")
        o.append(f"- **On offer:** {b['offers']}.")
        o += _quest_lines(sr["quest"], "Proprietor's quest")
        o.append("")
    for h in fl.hubs:
        o.append(f"### {h['number']} — Hub, {h['n_lines']} lines ({_lines_text(h['lines'])} "
                 f"among them)")
        o.append("")
        o.append(f"- **Shops:** {'; '.join(h['shops'])}.")
        o.append(f"- **Guilds:** {'; '.join(h['guilds'])}.")
        o.append(f"- **Also:** {h['sight']}.")
        o += _quest_lines(h["quest"], "On the noticeboard")
        o.append("")
    return o


def _getting_around4(fl: Floor4) -> List[str]:
    """How a colored-line train is made up, and how long anything takes."""
    c = FLOOR4_CFG
    first, end, edge = c["yard_ring"] + 1, c["employee_portal"], c["stairwell_numbers"][-1]
    lo, hi = c["ride_minutes"]
    o = ["## Getting Around", "",
         "Every colored-line train is made up the same way. Crawlers start the floor aboard "
         "one, with their party, in the passenger cars.", "",
         "| Car | Who rides in it |", "| --- | --- |",
         "| 1 | The engineer, in the cab. |",
         "| 5 | The employee break room, and the conductor. No mob ever comes in. |",
         "| 10 | The porters. Mobs board here. |",
         f"| 15 | The janitors: {fl.janitor.name}. Mobs board here too. |",
         "| The rest | Passengers. This is where crawlers ride. |", ""]
    o.append(f"The trains run one way only, from the yard toward the Abyss. A full run from "
             f"{first} to {end} takes {duration_text(f4_ride_minutes(first, end))}: near the "
             f"yard a stop comes every {lo} minutes, and by the far end every {hi} or so.")
    o.append("")
    o.append(f"Up to {edge} the stations are a block or two apart, and the whole stretch from "
             f"{first} to {edge} can be walked in {duration_text(f4_walk_minutes(first, edge))} "
             f"({_miles_text(f4_walk_miles(first, edge))}) if nothing is in the tunnel. Past "
             f"{edge} they spread out, until by station {c['far_station']} they are "
             f"{c['far_miles']} miles apart. Each line below says how long its crawlers have "
             f"before their train reaches {end}, and how far they are from the stairwells on foot.")
    o.append("")
    sp = fl.special_lines
    o.append("Two services run outside the colored lines:")
    o.append("")
    o.append(f"- **{sp['backward'][0].upper() + sp['backward'][1:]}** is the only train that "
             f"runs the other way, from the stairwell stations back toward the yard: "
             f"{sp['backward_how']}.")
    o.append(f"- **{sp['staff'][0].upper() + sp['staff'][1:]}** is for the staff only: "
             f"{sp['staff_how']}.")
    o.append("")
    return o


def _depot4(fl: Floor4) -> List[str]:
    """Station 75, where the repair carts wait for something to come off the rails."""
    c, d = FLOOR4_CFG, fl.depot
    at, speed = c["depot"], c["cart_speed"]
    first, end = c["yard_ring"] + 1, c["employee_portal"]
    return [
        f"## Station {at}: the Interdiction Depot", "",
        f"Where the floor's repair carts wait. Every line's tracks run past its sidings, so "
        f"a cart can reach any of them.", "",
        f"- **The carts:** {d['carts']} of them, flat-topped, twelve-wheeled, and {speed} "
        f"times as fast as a train in either direction: from {at} out to {end} in "
        f"{duration_text(f4_ride_minutes(at, end) / speed)}, or back to the yard in "
        f"{duration_text(f4_ride_minutes(first, at) / speed)}.",
        f"- **The crews:** {d['repair']} do the repairs; {d['security']} keep an eye on "
        f"them, because the company does not trust the {d['repair']}.",
        f"- **Making one go:** {d['controls']}.",
        "- **The scoop:** the front of every cart is a scoop that clears the line by sending "
        "whatever it picks up somewhere else: to a train yard, or into the Abyss. Switching "
        "between them takes ten seconds.",
        "- **When they go out:** whenever a train comes off its rails or a boiler blows, "
        "anywhere on the floor.",
        f"- **Right now:** {d['state']}", ""]


def _tail4(fl: Floor4) -> List[str]:
    """The last three stations on every line."""
    c, hr, sp = FLOOR4_CFG, fl.hr, fl.mimic_spawn
    return [
        "## The Tail", "",
        "The last three stations, the same on every line.", "",
        f"### {c['mimic_station']} — The Station Mimic", "",
        f"- **What it is:** advertised as a transit hub, and a city boss "
        f"(L{c['mimic_level']}): a station-sized mouth waiting for a train to pull in. The "
        f"tell is that {fl.mimic_tell}.",
        "- **Fighting it:** blunt weapons and spells do nothing. Only cutting works, and it "
        "does not die until more than half of it has been cut away.",
        f"- **What it belches up:** {sp['name']} (L{sp['level'][0]}–{sp['level'][1]}): "
        f"{sp['does']}.", "",
        f"### {c['employee_portal']} — End of the Line", "",
        "- **What it is:** the last stop for passengers on every line. The crew get off "
        f"here and portal home to the yard; the cars go on to {c['abyss']} to be dumped in "
        "the pit, with anyone still aboard.",
        f"- **Who meets the crew:** the **{hr['name']}**, {hr['what']}. {hr['does'][0].upper()}"
        f"{hr['does'][1:]}. Non-combatants: they do not fight.", "",
        f"### {c['abyss']} — The Abyss", "",
        "- **What it is:** a bare platform, one steep stair, and an hour's climb to a catwalk "
        "over the pit. The pit is a mile across and thousands of feet deep, and the wreckage "
        "of every train that ever reached it burns at the bottom while the janitors pick "
        "through it.",
        "- **Flying:** stray too far from the catwalk and the pit pulls you down.",
        "- **The walls:** lined with portals.", ""]


def _yards4(fl: Floor4) -> List[str]:
    """Station 10, where every train starts and every run resets."""
    c, y = FLOOR4_CFG, fl.yards
    sup = y["supervisor"]
    o = ["## The Train Yards", "",
         f"Station {c['yard_ring']}: the yards every colored line runs from, lettered A to Q, "
         f"where the trains reset after every run. A turntable the size of a ball field turns "
         f"them into their service bays, and the yards' substation is run by {sup['name']}, "
         f"{sup['what']}. The fenced sections are full of ghouls. A crawler gets here from the "
         f"Abyss, or on an interdiction cart.", ""]
    for letter, feature in sorted(y["features"].items()):
        users = [ln.color for ln in fl.lines if ln.yard == letter]
        o.append(f"- **Yard {letter}** ({_lines_text(users)}): {feature}.")
    o.append("")
    return o


def render4(fl: Floor4) -> str:
    c = FLOOR4_CFG
    o = [f"# Floor 4 — {fl.name} ({fl.size})", "", "## Summary", ""]
    o.append(f"- **Countdown:** {fl.days} days (the normal allotment is {c['normal_days']})")
    o.append(f"- **Lines detailed:** {len(fl.lines)} colored lines, {len(fl.named)} named "
             f"train{'s' if len(fl.named) != 1 else ''}")
    stairs = c["stairwell_numbers"]
    o.append(f"- **Stairwells:** stations {', '.join(map(str, stairs[:-1]))} and {stairs[-1]} "
             f"on every line, sealed until six hours before collapse. "
             f"See [Ghoul Stations](#ghoul-stations).")
    # Where the detailed lines meet, and what each meeting point has. Stations
    # shared by two lines are listed once.
    transfers = {}
    for ln in fl.lines:
        for st in ln.stations:
            if st.transfer_to:
                transfers.setdefault(st.number, st)
    for label, has in (("Safe rooms", lambda s_: s_.safe_room),
                       ("Desperado Club", lambda s_: s_.desperado),
                       ("Club Vanquisher", lambda s_: s_.vanquisher)):
        nums = sorted(n for n, st in transfers.items() if has(st))
        if nums:
            names = {sr["number"]: sr["business"]["name"] for sr in fl.safe_rooms}
            where = ", ".join(f"{n} ({names[n]})" if label == "Safe rooms" and n in names
                              else str(n) for n in nums)
            o.append(f"- **{label} on the detailed lines:** "
                     f"station{'s' if len(nums) != 1 else ''} {where}")
    if fl.hubs:
        o.append("- **Hubs:** " + ", ".join(f"station {h['number']} ({h['n_lines']} lines)"
                                             for h in fl.hubs)
                 + ". See [Safe Rooms and Hubs](#safe-rooms-and-hubs).")
    o.append(f"- **Train mob levels:** {c['mob_band'][0]}–{c['mob_band'][1]}, rising with withdrawal stage")
    o.append(f"- **Janitor mob:** {fl.janitor.name} (Level {fl.janitor_level}). {fl.janitor.quirk}")
    o.append(f"  - *{fl.janitor.description}*")
    o.append(f"- **The tail (every line):** {c['mimic_station']} is a Station Mimic (city boss, "
             f"L{c['mimic_level']}), {c['employee_portal']} is the end of the line, and "
             f"{c['abyss']} is the Abyss. See [The Tail](#the-tail).")
    o.append(f"- **Station {c['depot']}:** the interdiction depot, {fl.depot['carts']} repair "
             f"carts. See [Station {c['depot']}](#station-{c['depot']}-the-interdiction-depot).")
    o.append(f"- **The drug:** the {c['drug']}, \"{c['drug_short']}\" to everyone aboard, "
             f"handed out by the {fl.supplier['name']} at every exit-only cavern.")
    o.append(f"- **Withdrawal:** three stages on a schedule of each line's own, and a last "
             f"day (day {fl.days}) worse than any of them. See [Withdrawal](#withdrawal).")
    o.append("- **Story notes:**")
    for st in fl.story:
        o.append(f"  - {st}")
    o.append("  - Every track tube has an inverted underside, also in service, with mirrored stations. Nobody tells the crawlers.")
    o.append(f"- **Seed:** {fl.seed}")
    o.append("")
    o.append("## Floor Rules")
    o.append("")
    for r in F4_RULES:
        o.append(f"- {r}")
    o.append("")
    o.extend(_getting_around4(fl))
    o.extend(_depot4(fl))
    sup = fl.supplier
    o.append("## Withdrawal")
    o.append("")
    o.append(f"Every mob and crew member on the floor is hooked on the {c['drug']}, "
             f"\"{c['drug_short']}\" to everyone aboard. Miss a shot and withdrawal sets in, "
             f"in three stages, and nobody comes back from it. Each line goes through the "
             f"stages on its own schedule, given under the line.")
    o.append("")
    o.append(f"**The supply:** every fifth station is an exit-only cavern, and at every one "
             f"of them the **{sup['name']}** (L{sup['level'][0]}–{sup['level'][1]}) hand "
             f"out the shots: {sup['does']}. One shot per mob, enough to ride the next five "
             f"stops. If attacked, {sup['if_attacked']}.")
    o.append("")
    o.append("| Stage | What it does | Who dies of it | What the dead come back as |")
    o.append("| --- | --- | --- | --- |")
    for n, st in enumerate(fl.withdrawal, start=1):
        r = st["risen"]
        lo, hi = r["level"]
        o.append(f"| {n} | {st['symptom'].capitalize()} | {st['toll'].capitalize()} | "
                 f"**{r['name']}** (L{lo}–{hi}): {r['does']} |")
    o.append("")
    o.append(f"**The last day (day {fl.days}):**")
    o.append("")
    for event in fl.last_day:
        o.append(f"- {event}")
    o.append("")
    o.append("## Ghoul Stations")
    o.append("")
    o.append("The five stairwell stations, the same five on every line. Each stairwell is "
             "sealed until six hours before collapse, and each station is where one kind of "
             "ghoul gathers while the floor dies around it. The stairs open into them.")
    o.append("")
    for gs in fl.ghoul_stations:
        g = gs["ghoul"]
        lo, hi = g["level"]
        level = f"L{lo}" if lo == hi else f"L{lo}–{hi}"
        o.append(f"### {gs['number']} — {gs['name']}")
        o.append("")
        o.append(f"- **Stairwell:** {gs['stair']}")
        o.append(f"- **Who gathers:** **{g['name']}** ({level}), {g['source']}: {g['does']}.")
        o.append(f"- **What adds to them:** {gs['feed']}")
        o.append(f"- **Through the countdown:** {gs['tide']}")
        o.append(f"- **When the stairwell opens:** {gs['opening']}")
        o.append(f"- **Sigil:** {gs['sigil']}")
        if gs["merged"]:
            m = gs["merged"]
            o.append(f"- **If enough of them gather:** they merge into **{m['name']}** "
                     f"({m['tier']}, L{m['level'][0]}–{m['level'][1]}): {m['does']}.")
        o.append("")
    o.extend(_safe_rooms_and_hubs4(fl))
    o.extend(_tail4(fl))
    o.extend(_yards4(fl))
    o.append("## Colored Lines")
    o.append("")
    for ln in fl.lines:
        o.append(f"### {ln.color} Line (from Trainyard {ln.yard})")
        o.append("")
        o.append(f"- **Engineer:** {ln.engineer}, {ln.engineer_species}. Holds the {ln.color} engineer key.")
        o.append(f"- **Crew and passengers:**")
        for r in ln.roster:
            o.append(f"  - {r}")
        o.append(f"  - {ln.line_mob.name}: {ln.line_mob.quirk}")
        o.append(f"    - *{ln.line_mob.description}*")
        o.append(f"- **Withdrawal:** stage 2 from day {ln.stage2_day}, stage 3 from day "
                 f"{ln.stage3_day}. {ln.withdrawal}")
        end, edge = c["employee_portal"], c["stairwell_numbers"][-1]
        cavern = min(st.number for st in ln.stations
                     if st.kind == "exit_cavern" and st.number > ln.spawn)
        ahead = cavern - ln.spawn
        o.append(f"- **Crawlers start:** aboard this line's train with their party, pulling "
                 f"into station {ln.spawn}.")
        o.append(f"- **Staying aboard:** the train reaches {end} in "
                 f"{duration_text(f4_ride_minutes(ln.spawn, end))}, and the cars go on to the pit.")
        o.append(f"- **Getting back:** the stairwells are behind them. Back to {edge} on foot is "
                 f"{ln.spawn - edge} station{'s' if ln.spawn - edge != 1 else ''}, "
                 f"{_miles_text(f4_walk_miles(edge, ln.spawn))}: "
                 f"{duration_text(f4_walk_minutes(edge, ln.spawn))} if the tunnel is empty. "
                 f"The exit cavern at {cavern} is {ahead} stop{'s' if ahead != 1 else ''} ahead "
                 f"({duration_text(f4_ride_minutes(ln.spawn, cavern))}); its coaster track runs "
                 f"back to the yard.")
        o.append("- **Stations of note** (all others are routine platforms):")
        o.append(f"  - **{' / '.join(map(str, c['stairwell_numbers']))}** — stairwells. "
                 f"See [Ghoul Stations](#ghoul-stations).")
        o.append(f"  - **{c['depot']}** — INTERDICTION DEPOT. See "
                 f"[Station {c['depot']}](#station-{c['depot']}-the-interdiction-depot).")
        detailed = {ln_.color for ln_ in fl.lines}
        for st in ln.stations:
            if st.kind == "stairwell":
                continue
            kind = {"spawn": "SPAWN", "transfer": "TRANSFER", "exit_cavern": "EXIT CAVERN"}[st.kind]
            extra = ""
            if st.transfer_to:
                here = sorted(set(st.transfer_to) & detailed)
                away = sorted(set(st.transfer_to) - detailed)
                parts = ([f"{', '.join(here)} Line" + ("s" if len(here) > 1 else "")] if here else [])
                parts += ([f"{', '.join(away)} Line" + ("s" if len(away) > 1 else "")
                           + ", not detailed here"] if away else [])
                extra = " ↔ " + "; ".join(parts)
            has = st.amenities()
            sr = f" ({', '.join(has)})" if has else ""
            o.append(f"  - **{st.number}** — {kind}{extra}{sr}. {st.note}")
        o.append(f"  - **{c['mimic_station']}** — STATION MIMIC. Not a station. See [The Tail](#the-tail).")
        o.append(f"  - **{c['employee_portal']}** — END OF THE LINE. Last stop for passengers; "
                 f"the crew portal home from here, and the cars go on to the pit.")
        o.append(f"  - **{c['abyss']}** — THE ABYSS. See [The Tail](#the-tail).")
        o.append("")
    o.append("## Named Trains")
    o.append("")
    for t in fl.named:
        o.append(f"### {t.name}")
        o.append("")
        o.append(f"- **Stock:** {t.stock}; {t.cars} cars; every {t.interval} minutes.")
        route = " → ".join(f"{col} {num}" if col != "Abyss" else "Abyss 436" for col, num in t.stops)
        o.append(f"- **Loop:** {route} → back to start.")
        o.append(f"- **Boarding:** {t.entry}")
        o.append(f"- **Gimmick:** {t.gimmick}")
        o.append(f"- **Crew:** {t.crew.name}. {t.crew.quirk}")
        o.append(f"  - *{t.crew.description}*")
        if t.quest:
            q = t.quest
            o.append(f"- **Quest on boarding — {q['title']}:** {q['objective']} "
                     f"Reward: {q['reward']}. Failure: {q['failure']}")
        o.append("")
    o.extend(crawler_section(fl))
    # Like floors 1 and 2, the achievements this floor is likeliest to hand out.
    o.append("## Achievements")
    o.append("")
    o.append("System-wide behaviors, not unique to this floor, but most commonly earned "
             "on the Iron Tangle:")
    o.append("")
    for name, trigger, reward in fl.achievements:
        o.append(f"- **{name}:** {trigger} → {reward}")
    o.append("")
    return "\n".join(o)


def render4_svg(fl: Floor4) -> str:
    c = FLOOR4_CFG
    # Ordinal x positions for every station number that appears anywhere, plus fixed ends.
    nums = {c["yard_ring"], c["depot"], c["mimic_station"], c["employee_portal"], c["abyss"]}
    for ln in fl.lines:
        nums.update(st.number for st in ln.stations)
    for t in fl.named:
        nums.update(n for _, n in t.stops)
    ordered = sorted(nums)
    SPACING = 74
    LEFT = 150
    xs = {n: LEFT + i * SPACING for i, n in enumerate(ordered)}
    ROW = 110
    TOP = 140                 # room above the first line for the ghoul stations' names
    W = LEFT + len(ordered) * SPACING + 60
    n_named = len(fl.named)
    legend_h = 60 + 20 * n_named + 20
    H = TOP + ROW * len(fl.lines) + legend_h + 40
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="Helvetica, Arial, sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#f6f2ea"/>',
         _text(30, 36, f"Floor 4 — {fl.name}", size=22, weight="bold"),
         _text(30, 58, f"{fl.days} days  •  {len(fl.lines)} colored lines, {len(fl.named)} named "
                       f"train{'s' if len(fl.named) != 1 else ''}  •  "
                       f"janitor: {fl.janitor.name} L{fl.janitor_level}  •  seed {fl.seed}", size=12, fill="#555")]
    # Column labels for the constants
    for n, lab in ((c["yard_ring"], "Trainyard"), (c["depot"], f"DEPOT {c['depot']}"),
                   (c["mimic_station"], "MIMIC"),
                   (c["employee_portal"], "END OF LINE"), (c["abyss"], "ABYSS")):
        o.append(_text(xs[n], TOP - 30, lab, size=11, weight="bold", anchor="middle", fill="#333"))
    # Each ghoul station's name over its column, split over two lines so that
    # stairwell stations with nothing between them on the map still fit.
    for gs in fl.ghoul_stations:
        words = gs["name"].split()
        half = (len(words) + 1) // 2
        for k, part in enumerate((" ".join(words[:half]), " ".join(words[half:]))):
            if part:
                o.append(_text(xs[gs["number"]], TOP - 66 + 11 * k, part, size=9,
                               weight="bold", anchor="middle", fill="#7a2e2e"))
    # Vertical guide lines for the shared tail, and for the depot every line passes
    for n in (c["depot"], c["mimic_station"], c["employee_portal"], c["abyss"]):
        o.append(f'<line x1="{xs[n]}" y1="{TOP - 18}" x2="{xs[n]}" y2="{TOP + ROW * (len(fl.lines) - 1) + 30}" '
                 f'stroke="#bbb" stroke-width="1" stroke-dasharray="4,4"/>')

    ys = {}
    for i, ln in enumerate(fl.lines):
        y = TOP + i * ROW
        ys[ln.color] = y
        x0, x1 = xs[c["yard_ring"]], xs[c["abyss"]]
        o.append(f'<line x1="{x0}" y1="{y}" x2="{x1}" y2="{y}" stroke="{ln.hex}" stroke-width="10" stroke-linecap="round"/>')
        o.append(_text(x0 - 12, y + 5, f"{ln.color}", size=13, weight="bold", anchor="end", fill=ln.hex))
        o.append(_text(x0 - 12, y + 20, f"Yard {ln.yard}", size=10, anchor="end", fill="#555"))
        # yard + tail markers
        o.append(f'<rect x="{x0-7}" y="{y-7}" width="14" height="14" fill="#333"/>')
        o.append(f'<circle cx="{xs[c["mimic_station"]]}" cy="{y}" r="5" fill="#f6f2ea" stroke="#333" stroke-width="2"/>')
        # 435, the end of the line: the last stop anyone gets off at, so it is a
        # station of its own on every line rather than a dot on the way to the pit.
        # The depot's sidings, unless the line has a station of its own at 75.
        if not any(st.number == c["depot"] for st in ln.stations):
            xd = xs[c["depot"]]
            o.append(f'<rect x="{xd-6}" y="{y-6}" width="12" height="12" fill="#888" '
                     f'stroke="#111" stroke-width="2"/>')
        x435 = xs[c["employee_portal"]]
        o.append(f'<circle cx="{x435}" cy="{y}" r="9" fill="#fff" stroke="#111" stroke-width="3"/>')
        o.append(f'<circle cx="{x435}" cy="{y}" r="3.5" fill="#111"/>')
        o.append(f'<circle cx="{x1}" cy="{y}" r="9" fill="#111" stroke="#c0392b" stroke-width="3"/>')
        for n in (c["mimic_station"], c["employee_portal"], c["abyss"]):
            o.append(_text(xs[n], y + 24, str(n), size=11, weight="bold", anchor="middle"))
    # Transfer connectors
    drawn = set()
    for ln in fl.lines:
        for st in ln.stations:
            # sorted(), not set(): a set of colour names iterates in per-process hash
            # order, which reordered these connectors in the SVG from run to run.
            away = sorted(c_ for c_ in set(st.transfer_to) if c_ not in ys)
            # A line the floor does not detail has no row: a stub in its color
            # above the station, outlined so a pale one still shows, and its name
            # in ink, since some line colors are too pale to read as text.
            for k, other in enumerate(away):
                x, y = xs[st.number], ys[ln.color]
                o.append(f'<line x1="{x}" y1="{y - 9}" x2="{x}" y2="{y - 25}" '
                         f'stroke="#111" stroke-width="8"/>')
                o.append(f'<line x1="{x}" y1="{y - 10}" x2="{x}" y2="{y - 24}" '
                         f'stroke="{LINE_COLORS[other]}" stroke-width="5"/>')
                o.append(_text(x, y - 29 - 10 * k, other, size=9, weight="bold",
                               anchor="middle", fill="#333"))
            for other in sorted(set(st.transfer_to) - set(away)):
                key = (min(ln.color, other), max(ln.color, other), st.number)
                if key in drawn:
                    continue
                drawn.add(key)
                x = xs[st.number]
                y1, y2 = ys[ln.color], ys[other]
                o.append(f'<line x1="{x}" y1="{y1}" x2="{x}" y2="{y2}" stroke="#111" stroke-width="3"/>')
                o.append(f'<line x1="{x}" y1="{y1}" x2="{x}" y2="{y2}" stroke="#fff" stroke-width="1"/>')
    for ln in fl.lines:
        y = ys[ln.color]
        for st in ln.stations:
            x = xs[st.number]
            if st.kind == "transfer":
                o.append(f'<circle cx="{x}" cy="{y}" r="9" fill="#fff" stroke="#111" stroke-width="3"/>')
            elif st.kind == "stairwell":
                o.append(f'<rect x="{x-8}" y="{y-8}" width="16" height="16" fill="#fff" stroke="#111" stroke-width="3" transform="rotate(45 {x} {y})"/>')
                o.append(_stair_icon(x - 7, y - 32))
            elif st.kind == "spawn":
                o.append(f'<circle cx="{x}" cy="{y}" r="9" fill="#c0392b" stroke="#111" stroke-width="2"/>')
                o.append(_text(x, y - 26, "SPAWN", size=10, weight="bold", anchor="middle", fill="#c0392b"))
            else:  # exit cavern
                o.append(f'<polygon points="{x},{y-10} {x+9},{y+6} {x-9},{y+6}" fill="#fff" stroke="#111" stroke-width="2.5"/>')
            o.append(_text(x, y + 24, str(st.number), size=11, weight="bold", anchor="middle"))
            for k, what in enumerate(st.amenities()):
                o.append(_text(x, y + 36 + 10 * k, what, size=9, anchor="middle", fill="#555"))
    # Named trains as dashed loops
    dash_styles = ["8,5", "2,4", "12,4,2,4"]
    y_base = TOP + ROW * (len(fl.lines) - 1) + 60
    for k, t in enumerate(fl.named):
        pts = []
        prev_y = ys[fl.lines[0].color]
        for col, num in t.stops:
            y = ys.get(col, prev_y)      # Abyss stop rides the previous line's row
            prev_y = y
            pts.append((xs[num], y))
        if len(pts) >= 2:
            pts.append(pts[0])
            path = f'M {pts[0][0]},{pts[0][1]} '
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                cx_ = (x0 + x1) / 2
                bow = -40 - 18 * k
                path += f'Q {cx_},{min(y0, y1) + bow} {x1},{y1} '
            o.append(f'<path d="{path}" fill="none" stroke="#222" stroke-width="2.5" stroke-dasharray="{dash_styles[k % 3]}"/>')
        for x, y in pts[:-1]:
            o.append(f'<circle cx="{x}" cy="{y}" r="4" fill="#222"/>')
    # Legend
    ly = y_base + 30
    lx = 30
    items = [
        (f'<circle cx="{lx+8}" cy="{ly}" r="8" fill="#fff" stroke="#111" stroke-width="3"/>', "Transfer"),
        (f'<rect x="{lx+152}" y="{ly-7}" width="14" height="14" fill="#fff" stroke="#111" stroke-width="3" transform="rotate(45 {lx+159} {ly})"/>', "Stairwell station"),
        (f'<circle cx="{lx+300}" cy="{ly}" r="8" fill="#c0392b" stroke="#111" stroke-width="2"/>', "Crawler spawn"),
        (f'<polygon points="{lx+430},{ly-9} {lx+439},{ly+6} {lx+421},{ly+6}" fill="#fff" stroke="#111" stroke-width="2.5"/>', "Exit-only cavern"),
        (f'<rect x="{lx+560}" y="{ly-7}" width="14" height="14" fill="#333"/>', "Trainyard"),
        (f'<circle cx="{lx+670}" cy="{ly}" r="8" fill="#111" stroke="#c0392b" stroke-width="3"/>', "Abyss 436"),
        (f'<circle cx="{lx+780}" cy="{ly}" r="8" fill="#fff" stroke="#111" stroke-width="3"/>'
         f'<circle cx="{lx+780}" cy="{ly}" r="3" fill="#111"/>', "End of the line 435"),
        (f'<rect x="{lx+914}" y="{ly-6}" width="12" height="12" fill="#888" stroke="#111" '
         f'stroke-width="2"/>', "Interdiction depot 75"),
        (f'<line x1="{lx+1090}" y1="{ly+8}" x2="{lx+1090}" y2="{ly-8}" stroke="#111" '
         f'stroke-width="8"/><line x1="{lx+1090}" y1="{ly+7}" x2="{lx+1090}" y2="{ly-7}" '
         f'stroke="#bbb" stroke-width="5"/>', "Transfer to a line not detailed here"),
    ]
    offs = [22, 170, 314, 446, 582, 686, 796, 932, 1102]
    for (glyph, label), off in zip(items, offs):
        o.append(glyph)
        o.append(_text(lx + off, ly + 4, label, size=11))
    ky = ly + 28
    for k, t in enumerate(fl.named):
        o.append(f'<line x1="{lx}" y1="{ky-4}" x2="{lx+40}" y2="{ky-4}" stroke="#222" stroke-width="2.5" stroke-dasharray="{dash_styles[k % 3]}"/>')
        route = " → ".join(f"{col} {num}" if col != "Abyss" else "Abyss" for col, num in t.stops)
        o.append(_text(lx + 50, ky, f"{t.name}: {route} (every {t.interval} min, {t.cars} cars)", size=11))
        ky += 20
    o.append("</svg>")
    return "\n".join(o)


# ===========================================================================
# FLOOR 5 — THE BUBBLES
# One bubble, four quadrants, four castles. Size = depth of castle detail.
# ===========================================================================

FLOOR5_CFG = {
    "days": 15,
    "mob_band": (25, 45),
    "crawlers": (130, 170),
    "bubbles": 1172,
    # The quadrant's boss is the castle's ruler: a borough boss this far above the
    # quadrant's top mob level, or a city boss in the castle meant to fall last,
    # as the books field borough bosses at L52-55 and a city boss at L82.
    "ruler_bump": {"Easy": 10, "Moderate": 15, "Hard": 20},
    "city_boss_level": (78, 84),
    # A quadrant can hold more bosses than its ruler: borough bosses on the way in,
    # more of them the harder the castle. Killing one does not take the castle.
    "guardians": {"Easy": (0, 0), "Moderate": (0, 1), "Hard": (1, 1), "Brutal": (1, 2)},
    "guardian_bump": (8, 12),
    # Quests each faction posts; a large bubble's nine match the books' count.
    "quests_per_faction": {"small": 1, "medium": 2, "large": 3},
    # Where crawlers start: the books' Air quadrant alone had four places. The
    # bubble's crawlers are split unevenly between the quadrants, then between
    # each quadrant's sites, by weights drawn from this range.
    "sites": {"small": 2, "medium": 3, "large": 4},
    # Kinds of hostile mob in a quadrant, the castle's garrison among them: the
    # books field three to five. Each quadrant also has one white-tagged mob.
    "mobs": {"small": 2, "medium": 3, "large": 4},
    "share_weight": (0.5, 1.5),
    "quadrant_min": 15,
    "site_min": 2,
}

QUADRANT_ORDER = ["Air", "Land", "Sea", "Subterranean"]
F5_RULES = [
    "Every crawler is locked inside their starting quadrant until that quadrant's castle is conquered.",
    "A castle is conquered when the throne room is occupied or the quadrant's boss, its ruler, is killed. Either counts. A quadrant can hold other bosses; killing them takes nothing.",
    "The stairwell is in the throne room. It will not open until all four castles in the bubble have fallen.",
    "Once your castle falls, you may cross into the other quadrants to help take theirs.",
    "When the fourth castle falls the bubble pops and its four stairwells open, with no time limit on them from then on. Crawlers may leave a popped bubble, but may not enter another until it has popped too.",
    "Fifteen days. If the bubble hasn't popped, the floor collapses around everyone in it.",
]


@dataclass
class Faction:
    name: str
    home: str            # quadrant name
    want: str


@dataclass
class Castle:
    name: str
    form: str
    ruler: str
    boss: Boss
    throne: str
    difficulty: str
    difficulty_note: str
    approach: Optional[str] = None
    obstacle: Optional[str] = None
    chambers: List[Tuple[str, str]] = field(default_factory=list)
    # Borough bosses elsewhere in the quadrant; each one's chamber says where.
    guardians: List[Boss] = field(default_factory=list)


@dataclass
class Site:
    """Somewhere in a quadrant that crawlers start."""
    name: str            # "Grayridge", "the Kite City of the Razorfoxes"
    kind: str            # settlement | mob | ruin
    note: str
    crawlers: int
    mob: str = ""        # whose ground it is, for a mob site


@dataclass
class QuadrantMob:
    """A hostile mob a quadrant has besides its castle's garrison."""
    mob: Mob
    level_range: Tuple[int, int]


@dataclass
class WhiteMob:
    """A white-tagged mob: it won't attack unless provoked. A hazard, not an enemy."""
    name: str
    level_range: Tuple[int, int]
    hazard: str


@dataclass
class Quadrant:
    name: str
    color: str
    biome: str
    faction: Optional[Faction]
    settlement: str
    mob: Mob
    level_range: Tuple[int, int]
    traversal: str
    castle: Castle
    crawlers: int = 0                     # how many start here, locked in
    sites: List[Site] = field(default_factory=list)
    other_mobs: List[QuadrantMob] = field(default_factory=list)
    white: Optional[WhiteMob] = None

    @property
    def hostile(self) -> List[Tuple[Mob, Tuple[int, int]]]:
        """Every hostile mob here, the castle's garrison first."""
        return [(self.mob, self.level_range)] + [(m.mob, m.level_range) for m in self.other_mobs]


@dataclass
class Floor5:
    size: str
    bubble: int
    days: int
    crawlers: int
    bubble_state: str
    rival: str
    factions: List[Faction]
    tension: str
    storyline: str
    quadrants: List[Quadrant]
    suggested_start: str
    seed: int
    number: int = 5
    other_crawlers: List[CrawlerTeam] = field(default_factory=list)
    quests: List[Quest] = field(default_factory=list)           # by faction, in order
    achievements: List[list] = field(default_factory=list)      # the books' first


class Generator5:
    FLOOR = 5

    def __init__(self, size, seed, days):
        self.rng = random.Random(f"floor{self.FLOOR}|{seed}")
        self.seed = seed
        self.size = size
        self.cfg = FLOOR5_CFG
        self.days_override = days
        self.used = set()
        self.mech_decks = make_mechanic_decks(self.rng)
        self.namer = BossNamer(self.rng, CASTLE_ADJ)

    def _unique(self, fn):
        for _ in range(100):
            v = fn()
            if v not in self.used:
                break
        self.used.add(v)
        return v

    def make_mob(self, noun) -> Mob:
        adj, _ = mob_words(self.rng, 5)       # the quadrant supplies the noun
        return describe_mob(self.rng, adj, noun)

    def _form(self, mob: Mob) -> str:
        return self.rng.choice(BOSS_FORMS).format(
            size=self.rng.randint(20, 60), a_size="a", noun_l=mob.noun.lower(),
            noun_sing=singular(mob.noun).lower())

    def make_castle(self, q: str, mob: Mob, top: int, diff) -> Castle:
        qd, c5 = QUADRANT_DATA[q], self.cfg
        # A ruler's first name is claimed with the namer, so no guardian on the
        # floor is "Vexnia the Auditor" beside "Vexnia the Mad".
        first = self.rng.choice([f for f in BOSS_FIRST if f not in self.namer.firsts]
                                or BOSS_FIRST)
        self.namer.firsts.add(first)
        name = self._unique(lambda: self.rng.choice(CASTLE_NAME).format(
            adj=self.rng.choice(CASTLE_ADJ), thing=self.rng.choice(CASTLE_THING),
            noun=self.rng.choice(CASTLE_NOUN), name=self.rng.choice(BOSS_FIRST)))
        ruler = f"{first} {self.rng.choice(RULER_TITLE)}"
        # The quadrant's boss: a borough boss, or a city boss in the castle meant to
        # fall last.
        if diff[0] in c5["ruler_bump"]:
            tier, lvl = "borough", top + c5["ruler_bump"][diff[0]] + self.rng.randint(-2, 2)
        else:
            tier, lvl = "city", self.rng.randint(*c5["city_boss_level"])
        boss = Boss(f"{ruler}, ruler of {name}", tier, lvl, self._form(mob),
                    boss_mechanics(self.mech_decks, tier), "the throne room", True)
        c = Castle(name=name, form=self.rng.choice(qd["castle_forms"]), ruler=ruler, boss=boss,
                   throne=self.rng.choice(THRONE_NOTE), difficulty=diff[0], difficulty_note=diff[1])
        # Borough bosses elsewhere in the quadrant, below the ruler, none of them
        # the quadrant's boss.
        posts = self.rng.sample(GUARDIAN_POSTS, len(GUARDIAN_POSTS))
        for post in posts[:self.rng.randint(*c5["guardians"][diff[0]])]:
            g_lvl = min(top + self.rng.randint(*c5["guardian_bump"]), lvl - 1)
            c.guardians.append(Boss(self.namer.name(mob.noun), "borough", g_lvl, self._form(mob),
                                    boss_mechanics(self.mech_decks, "borough"), post, False))
        if self.size in ("medium", "large"):
            c.approach = self.rng.choice(APPROACH)
            c.obstacle = self.rng.choice(OBSTACLE)
        if self.size == "large":
            for room in ("Gate", "Courtyard", "Keep"):
                c.chambers.append((room, self.rng.choice(CHAMBERS[room]).format(mob=mob.noun.lower())))
        return c

    def build(self) -> Floor5:
        lo, hi = self.cfg["mob_band"]
        # Three factions, three home quadrants; the fourth quadrant is factionless.
        homes = self.rng.sample(QUADRANT_ORDER, 3)
        factions = []
        for h in homes:
            nm = self._unique(lambda: f"{self.rng.choice(FACTION_ADJ)} {self.rng.choice(FACTION_NOUN)}")
            factions.append(Faction(nm, h, self.rng.choice(FACTION_WANT)))
        A, B, C = factions
        tension = self.rng.choice(TENSION).format(A=A.name, B=B.name, C=C.name, W=self.rng.choice(FACTION_WANT))

        diffs = DIFFICULTY[:]
        self.rng.shuffle(diffs)
        quadrants = []
        for q, diff in zip(QUADRANT_ORDER, diffs):
            qd = QUADRANT_DATA[q]
            mob = self.make_mob(self.rng.choice(qd["mobs"]))
            a = self.rng.randint(lo, hi - 8)
            lr = (a, a + self.rng.randint(5, 8))
            fac = next((f for f in factions if f.home == q), None)
            if fac:
                settlement = (f"{self.rng.choice(SETTLEMENT_PREFIX)}{self.rng.choice(SETTLEMENT_SUFFIX)}, "
                              f"a {fac.name} settlement with a safe-room inn")
            else:
                settlement = "No living settlement; one abandoned safe room near the entry point"
            quadrants.append(Quadrant(
                name=q, color=qd["color"], biome=self.rng.choice(qd["biomes"]), faction=fac,
                settlement=settlement, mob=mob, level_range=lr, traversal=self.rng.choice(qd["traversal"]),
                castle=self.make_castle(q, mob, lr[1], diff)))

        start = next(qq.name for qq in quadrants if qq.castle.difficulty == "Easy")
        fl = Floor5(
            size=self.size, bubble=self.rng.randint(1, self.cfg["bubbles"]),
            days=self.days_override or self.cfg["days"], crawlers=self.rng.randint(*self.cfg["crawlers"]),
            bubble_state=self.rng.choice(BUBBLE_STATE), rival=self.rng.choice(RIVALS),
            factions=factions, tension=tension, storyline=self.rng.choice(STORYLINE),
            quadrants=quadrants, suggested_start=start, seed=self.seed)
        # Last, so the bubble above is the one it was before quests existed.
        fl.quests = self._quests(factions, quadrants)
        fl.achievements = [list(a) for a in ACHIEVEMENTS5["books"] + ACHIEVEMENTS5["ours"]]
        # And after those, so the castles and quests stay what they were.
        self._more_mobs(fl)
        self._populate(fl)
        return fl

    def _band(self) -> Tuple[int, int]:
        lo, hi = self.cfg["mob_band"]
        a = self.rng.randint(lo, hi - 8)
        return a, a + self.rng.randint(5, 8)

    def _more_mobs(self, fl: Floor5) -> None:
        """The rest of each quadrant's mobs: more hostile kinds from its own bank,
        none sharing a noun, an adjective or a habit with another here, and one
        white-tagged mob that won't attack unless provoked."""
        for q in fl.quadrants:
            qd = QUADRANT_DATA[q.name]
            nouns = [x for x in qd["mobs"] if x != q.mob.noun]
            adjs, quirks = {q.mob.adj}, {q.mob.quirk}
            for noun in self.rng.sample(nouns, self.cfg["mobs"][fl.size] - 1):
                m = self.make_mob(noun)
                for _ in range(20):
                    if m.adj not in adjs and m.quirk not in quirks:
                        break
                    m = self.make_mob(noun)
                adjs.add(m.adj)
                quirks.add(m.quirk)
                q.other_mobs.append(QuadrantMob(m, self._band()))
            name, hazard = self.rng.choice(qd["white"])
            q.white = WhiteMob(name, self._band(), hazard)

    def _split(self, total: int, n: int, least: int) -> List[int]:
        """total shared unevenly between n, none under least, adding up exactly."""
        w = [self.rng.uniform(*self.cfg["share_weight"]) for _ in range(n)]
        counts = [max(least, int(total * x / sum(w))) for x in w]
        counts[counts.index(max(counts))] += total - sum(counts)
        return counts

    def _populate(self, fl: Floor5) -> None:
        """Where the bubble's crawlers start. Each quadrant gets an uneven share,
        and a few sites to start at: a faction's own settlement, the quadrant
        mob's ground, or a ruin with an abandoned safe room. A quadrant with no
        faction always has a ruin, so it still has a safe room."""
        c5, notes = self.cfg, SITE_NOTES5
        # Dealt across the bubble, so two ruins are seldom alike.
        deck = {k: Deck(self.rng, notes[k]) for k in ("mob", "ruin", "ruin_adj")}
        shares = self._split(fl.crawlers, len(fl.quadrants), c5["quadrant_min"])
        n = c5["sites"][fl.size]
        for q, share in zip(fl.quadrants, shares):
            q.crawlers = share
            places = self.rng.sample(QUADRANT_DATA[q.name]["sites"],
                                     len(QUADRANT_DATA[q.name]["sites"]))
            kinds = ["settlement" if q.faction else "ruin"]
            kinds += [self.rng.choice(["mob", "ruin"]) for _ in range(n - 1)]
            # Each mob ground is somebody different's while there are mobs to go
            # round, as each of the books' Air sites was a different race's home.
            holders = Deck(self.rng, [m for m, _ in q.hostile])
            for kind, count in zip(kinds, self._split(share, n, c5["site_min"])):
                holder = holders.draw() if kind == "mob" else None
                # A place that shares a word with its holder reads as a stutter:
                # "the Storm Shelf of the Storm Crows".
                clash = set(holder.noun.lower().split()) if holder else set()
                place = next(p for p in places if not set(p.lower().split()) & clash)
                places.remove(place)
                if kind == "settlement":
                    name, note = (q.settlement.split(",")[0],
                                  f"a {q.faction.name} settlement with a safe-room inn")
                elif kind == "mob":
                    name = f"the {place} of the {holder.noun}"
                    note = deck["mob"].draw().format(mob=holder.name,
                                                     mob_s=possessive(holder.name))
                else:
                    name = f"the {deck['ruin_adj'].draw()} {place}"
                    note = deck["ruin"].draw()
                q.sites.append(Site(name, kind, note, count, holder.name if holder else ""))

    def _quests(self, factions: List[Faction], quadrants: List[Quadrant]) -> List[Quest]:
        """What each faction asks of the crawlers. Templates are dealt from one deck
        for the whole bubble, so no two quests on it share one, and each fills in
        with what is really on the floor: its castles and rulers, the other
        factions and their settlements, the quadrants' mobs."""
        qb = QUESTS5
        deck = {k: Deck(self.rng, qb[k]) for k in
                ("templates", "npcs", "items", "places", "creatures", "rewards", "flavor")}
        home = {q.name: q for q in quadrants}
        out = []
        for f in factions:
            for _ in range(self.cfg["quests_per_faction"][self.size]):
                t = deck["templates"].draw()
                rival = self.rng.choice([r for r in factions if r is not f])
                q = self.rng.choice(quadrants)
                giver, npc = deck["npcs"].draw(), deck["npcs"].draw()
                item, (creature, what) = deck["items"].draw(), deck["creatures"].draw()
                vals = dict(giver=giver, npc=npc, faction=f.name, rival=rival.name,
                            giver_s=possessive(giver), faction_s=possessive(f.name),
                            rival_home=home[rival.home].settlement.split(",")[0],
                            castle=q.castle.name, ruler=q.castle.ruler, quadrant=q.name,
                            mob=q.mob.name, item=item,
                            Item=cap_words(re.sub(r"^(?:a|an|the)\s+", "", item)),
                            place=deck["places"].draw(), creature=creature, creature_what=what)
                out.append(Quest(t["title"].format(**vals), f"{giver}, of the {f.name}",
                                 t["objective"].format(**vals),
                                 deck["flavor"].draw().format(**vals),
                                 deck["rewards"].draw().format(**vals)))
        return out


def render5(fl: Floor5) -> str:
    lo, hi = FLOOR5_CFG["mob_band"]
    o = [f"# Floor 5 — Bubble {fl.bubble} ({fl.size})", "", "## Summary", ""]
    o.append(f"- **Countdown:** {fl.days} days")
    split = ", ".join(f"{q.name} {q.crawlers}" for q in fl.quadrants)
    o.append(f"- **Crawlers in the bubble:** about {fl.crawlers} ({split}). {fl.bubble_state} "
             f"Of note: {fl.rival}.")
    o.append("- **Quadrants:** Air, Land, Sea, Subterranean — one castle each, stairwell in every throne room.")
    o.append(f"- **Quadrant mob levels:** {lo}–{hi}. Each castle's ruler is the quadrant's "
             f"boss: a borough boss, or a city boss in the castle meant to fall last. Harder "
             f"castles have borough-boss guardians too.")
    o.append("- **Factions:**")
    for f in fl.factions:
        o.append(f"  - {f.name} ({f.home} quadrant) — want {f.want}.")
    o.append(f"- **Tension:** {fl.tension}")
    o.append(f"- **Storyline:** {fl.storyline}")
    o.append(f"- **Seed:** {fl.seed}")
    o.append("")
    o.append("## Floor Rules")
    o.append("")
    for r in F5_RULES:
        o.append(f"- {r}")
    o.append("")
    o.append("## Quadrants")
    o.append("")
    for q in fl.quadrants:
        c = q.castle
        o.append(f"### {q.name} Quadrant")
        o.append("")
        o.append(f"- **Biome:** {q.biome}.")
        o.append(f"- **Crawlers:** about {q.crawlers} start in this quadrant. None of them can "
                 f"leave it until its castle falls.")
        o.append("- **Starting sites:**")
        for s in q.sites:
            o.append(f"  - **{s.name[:1].upper() + s.name[1:]}** (about {s.crawlers} "
                     f"crawlers): {s.note}.")
        if q.faction:
            o.append(f"- **Faction:** {q.faction.name}; want {q.faction.want}.")
        o.append(f"- **Mob:** {q.mob.name}, Level {q.level_range[0]}–{q.level_range[1]}")
        o.append(f"  - {q.mob.quirk}")
        o.append(f"  - *{q.mob.description}*")
        if q.other_mobs:
            o.append("- **Also in the quadrant:**")
            for om in q.other_mobs:
                lo_, hi_ = om.level_range
                o.append(f"  - **{om.mob.name}**, Level {lo_}–{hi_}. {om.mob.quirk}")
                o.append(f"    - *{om.mob.description}*")
        if q.white:
            w = q.white
            o.append(f"- **White-tagged:** {w.name}, Level {w.level_range[0]}–"
                     f"{w.level_range[1]}. They won't attack unless provoked. {w.hazard}")
        o.append(f"- **Getting to the castle:** {q.traversal}")
        o.append(f"- **Castle:** {c.name} — {c.form}, ruled by {c.ruler}.")
        if c.approach:
            o.append(f"  - Approach: {c.approach}")
        if c.obstacle:
            o.append(f"  - Inside: {c.obstacle}")
        for room, desc in c.chambers:
            o.append(f"  - {room}: {desc}.")
        o.append(f"  - Throne room: {c.throne}")
        o.append(f"- **Quadrant Boss:** {c.boss.name} ({c.boss.tier} boss, Level {c.boss.level})")
        o.append(f"  - Form: {c.boss.form}.")
        o.append(f"  - Mechanic: {c.boss.mechanic}")
        o.append(f"  - Drops: a {boss_box(c.boss.tier, fl.number)}")
        for g in c.guardians:
            o.append(f"- **Guardian:** {g.name} ({g.tier} boss, Level {g.level}). {g.chamber} "
                     f"Killing it doesn't take the castle.")
            o.append(f"  - Form: {g.form}.")
            o.append(f"  - Mechanic: {g.mechanic}")
            o.append(f"  - Drops: a {boss_box(g.tier, fl.number)}")
        o.append("")
    if fl.quests:
        o.append("## Quests")
        o.append("")
        o.append("Suggested ideas, not a definitive list: the bubble's people will ask for "
                 "whatever the story needs. A quest in another quadrant can't be reached until "
                 "the crawler's own castle has fallen.")
        o.append("")
        for f in fl.factions:
            o.append(f"### {f.name} ({f.home} quadrant)")
            o.append("")
            for q in fl.quests:
                if q.giver.endswith(f", of the {f.name}"):
                    o += _quest_lines(q, q.giver.split(",")[0])
            o.append("")
    o.append("## DM Material")
    o.append("")
    o.append("Crawlers are meant to work this out on their own (or not).")
    o.append("")
    for q in fl.quadrants:
        o.append(f"- **{q.name}:** {q.castle.difficulty}. {q.castle.difficulty_note}")
    first = next(q for q in fl.quadrants if q.name == fl.suggested_start)
    gentle = next(s for s in first.sites if s.kind != "mob")
    o.append(f"- **Suggested starting quadrant:** {fl.suggested_start} (the easiest castle), "
             f"at {gentle.name}. Your call; the books dealt crawlers out at random.")
    o.extend(crawler_section(fl))
    if fl.achievements:
        books = len(ACHIEVEMENTS5["books"])
        o.append("## Achievements")
        o.append("")
        o.append(f"Suggested ideas, not a definitive list: the System hands achievements out for "
                 f"whatever amuses it. The first {books} are ones the books hand out on the "
                 f"Fifth Floor; the rest are built on how a bubble works.")
        o.append("")
        for name, trigger, reward in fl.achievements:
            o.append(f"- **{name}:** {trigger} → {reward}")
        o.append("")
    return "\n".join(o)


def render5_svg(fl: Floor5) -> str:
    import math
    W, H = 900, 900
    cx, cy, R = 450, 470, 330
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="Helvetica, Arial, sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#f6f2ea"/>',
         _text(30, 36, f"Floor 5 — Bubble {fl.bubble}", size=22, weight="bold"),
         _text(30, 58, f"{fl.days} days  •  ~{fl.crawlers} crawlers  •  seed {fl.seed}", size=12, fill="#555")]
    # wedges: Air NW, Land NE, Sea SE, Subterranean SW (quadrant order rotates from top-left clockwise)
    angles = {"Air": (180, 270), "Land": (270, 360), "Sea": (0, 90), "Subterranean": (90, 180)}
    for q in fl.quadrants:
        a0, a1 = angles[q.name]
        x0 = cx + R * math.cos(math.radians(a0)); y0 = cy + R * math.sin(math.radians(a0))
        x1 = cx + R * math.cos(math.radians(a1)); y1 = cy + R * math.sin(math.radians(a1))
        o.append(f'<path d="M {cx},{cy} L {x0:.1f},{y0:.1f} A {R},{R} 0 0 1 {x1:.1f},{y1:.1f} Z" '
                 f'fill="{q.color}" stroke="#333" stroke-width="3"/>')
        mid = math.radians((a0 + a1) / 2)
        tx = cx + 0.58 * R * math.cos(mid); ty = cy + 0.58 * R * math.sin(mid)
        lines = [
            (f"{q.name.upper()}", 16, "bold", "#111"),
            (_fit(q.biome, 34), 11, "normal", "#222"),
            (_fit(q.faction.name if q.faction else "no faction", 30), 11, "normal", "#222"),
            (f"{q.mob.name} L{q.level_range[0]}–{q.level_range[1]}", 11, "normal", "#222"),
            (f"~{q.crawlers} crawlers, {len(q.sites)} sites, {len(q.hostile)} mobs", 11,
             "normal", "#222"),
            (_fit(q.castle.name, 30), 12, "bold", "#111"),
            (f"{q.castle.boss.tier.capitalize()} boss L{q.castle.boss.level}"
             + (f" + {len(q.castle.guardians)} guardian{'s' if len(q.castle.guardians) != 1 else ''}"
                if q.castle.guardians else ""), 11, "normal", "#222"),
        ]
        for i, (t, sz, wt, col) in enumerate(lines):
            o.append(_text(tx, ty - 34 + i * 16, t, size=sz, weight=wt, anchor="middle", fill=col))
        # castle marker
        off = mid + math.radians(28)
        kx = cx + 0.80 * R * math.cos(off); ky = cy + 0.80 * R * math.sin(off)
        o.append(f'<rect x="{kx-9}" y="{ky-9}" width="18" height="18" fill="#222"/>')
        o.append(_stair_icon(kx - 7, ky - 32))
        if q.name == fl.suggested_start:
            o.append(_text(tx, ty + 66, "suggested start (DM)", size=10, weight="bold", anchor="middle", fill="#c0392b"))
    o.append(f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="none" stroke="#111" stroke-width="5"/>')
    o.append(f'<circle cx="{cx}" cy="{cy}" r="34" fill="#f6f2ea" stroke="#111" stroke-width="3"/>')
    o.append(_text(cx, cy + 5, str(fl.bubble), size=16, weight="bold", anchor="middle"))
    ly = cy + R + 40
    o.append(f'<rect x="30" y="{ly-9}" width="18" height="18" fill="#222"/>')
    o.append(_text(56, ly + 4, "Castle (throne room holds the stairwell)", size=11))
    o.append(_text(30, ly + 26, f"Factions: {'; '.join(f.name + ' (' + f.home + ')' for f in fl.factions)}", size=11))
    o.append(_text(30, ly + 44, _fit(f"Tension: {fl.tension}", 130), size=11))
    o.append("</svg>")
    return "\n".join(o)


# ===========================================================================
# FLOOR 6 — THE HUNTING GROUNDS
# Floor 3's grid, jungle-flavored, plus Zockau, the river, hunter parties,
# the thorn wall, and the bounty board.
# ===========================================================================

FLOOR6_CFG = {
    "mob_band": (35, 60),
    "days": (17, 17),
    "boss_offset": {"neighborhood": 10, "borough": 25},
    "city_boss_level": 85,
    "janitor_level": (30, 40),
    "guard_level": {"tiny": 95, "small": 95, "medium": 95, "large": 95, "xl": 95},
    "hunter_level": 50,
    "hunters_total": 360,
    "grace_hours": 30,
}

SIZE6_CFG = {"small": {"hunters": 3, "hidden": 1}, "medium": {"hunters": 6, "hidden": 2}, "large": {"hunters": 10, "hidden": 3}}

F6_RULES = [
    "Subclass selection on entry. Permanent.",
    "Grace period: hunters are locked in Zockau for the first 30 hours, then released.",
    "Parties cap at 30. Larger groups are split automatically. Guilds are available.",
    "Stairwells have no timing restriction. One is unguarded in Zockau's park; others are hidden in the valley or sit near city-tier mobs.",
    "Hunters start at Level 50, can die for real, and drop a Hunter Hand trophy. They earn a scalp for every crawler they kill.",
    "Settlement guards are Level 95 Funeral Bells. Same law as Floor 3: don't touch the NPCs where a guard can see.",
    "A wall of thorns advances across the floor as the timer runs down. Nothing behind it survives.",
]


@dataclass
class HunterParty:
    leader: str
    species: str
    size: int
    weapons: List[str]
    armor: str
    tech: List[str]
    sponsor: str
    style: str
    camp_type: str
    camp_region: str
    patrol: List[str]


@dataclass
class Floor6(Floor3):
    hunters: List[HunterParty] = field(default_factory=list)
    zockau_label: str = ""
    zockau_notes: List[str] = field(default_factory=list)
    thorn_side: str = ""
    thorn_schedule: str = ""
    bounty: list = field(default_factory=list)
    number: int = 6


class Generator6(Generator3):
    FLOOR = 6

    def __init__(self, size, scaling, seed, days):
        super().__init__(size, scaling, seed, days)
        self.cfg = FLOOR6_CFG

    def settlement_name(self):
        return self._unique(lambda: self.rng.choice(SETTLEMENT_PREFIX) + self.rng.choice(SETTLEMENT_SUFFIX))

    def region_name(self):
        return self._unique(lambda: f"The {self.rng.choice(JUNGLE_ADJ)} {self.rng.choice(JUNGLE_PLACE)}")

    def make_mob(self) -> Mob:
        adj, noun = self._unique(lambda: mob_words(self.rng, 6))
        return describe_mob(self.rng, adj, noun)

    def build(self) -> Floor6:
        base = super().build()
        n = base.grid
        grid = {(c.row, c.col): c for c in base.cells}

        # Jungle races and Funeral Bell guards.
        for c in base.cells:
            if isinstance(c, Settlement):
                c.race = self.rng.choice(JUNGLE_RACES)
                c.guard_level = 95

        # Zockau: pinned to the top row, centered.
        zpos = (0, n // 2)
        old = grid[zpos]
        z = Settlement(idx=old.idx, label=old.label, name="Zockau", size="xl", row=0, col=zpos[1],
                       race="Ursine", guard_level=95, tutorial_guild=False,
                       shops=["outfitter (hunters only)", "trophy broker", "pub strip"],
                       race_guilds=[], class_guilds=[], club=True, quests=[], distance=old.distance, hostile=True)
        grid[zpos] = z
        base.cells[old.idx] = z

        # The river: west edge to east edge across the middle rows, avoiding Zockau.
        r = self.rng.randint(max(1, n // 2 - 1), min(n - 2, n // 2 + 1))
        path = []
        for col in range(n):
            path.append((r, col))
            step = self.rng.choice([-1, 0, 0, 1])
            nr = min(n - 2, max(1, r + step))
            if nr != r and col < n - 1:
                path.append((nr, col))      # keep the bend connected
            r = nr
        for pos in path:
            c = grid[pos]
            if isinstance(c, Region):
                c.river = self.rng.choice(RIVER_CROSSINGS)
            elif isinstance(c, Settlement) and not c.hostile:
                c.on_river = True

        regions = [c for c in base.cells if isinstance(c, Region)]
        settlements = [c for c in base.cells if isinstance(c, Settlement)]
        for reg in regions:
            reg.flora = self.rng.choice(FLORA_HAZARDS)

        # Entry: keep a random jungle cell, but never Zockau's row.
        for reg in regions:
            reg.is_entry = False
        entry = self.rng.choice([x for x in regions if x.row > 0])
        entry.is_entry = True

        # Stairwells: Zockau park (unguarded), hidden valley ones, one near each city/borough boss.
        stairs = []
        for reg in regions:
            reg.stairwell = None
        stairs.append(Stairwell("S1", z.label, "unguarded, in Zockau's park; every hunter on the floor knows it"))
        k = 2
        pool = [x for x in regions if not x.boss]
        self.rng.shuffle(pool)
        for reg in pool[: SIZE6_CFG[self.size]["hidden"]]:
            sw = Stairwell(f"S{k}", reg.label, f"hidden in the valley: {self.rng.choice(HIDDEN_STAIR_SPOTS)}")
            reg.stairwell = sw
            stairs.append(sw)
            k += 1
        for reg in regions:
            if reg.boss and reg.boss.tier in ("city", "borough"):
                sw = Stairwell(f"S{k}", reg.label, f"near {reg.boss.name} ({reg.boss.tier} boss, L{reg.boss.level}); not locked, just guarded")
                reg.stairwell = sw
                stairs.append(sw)
                k += 1

        # Hunter parties with camps and patrols.
        hunters = []
        camp_pool = [x for x in regions if not x.is_entry]
        self.rng.shuffle(camp_pool)
        for i in range(SIZE6_CFG[self.size]["hunters"]):
            camp = camp_pool[i % len(camp_pool)]
            adj = [c.label for c in base.cells if abs(c.row - camp.row) + abs(c.col - camp.col) == 1]
            hp = HunterParty(
                leader=self._unique(lambda: self.rng.choice(HUNTER_LEADER)),
                species=self.rng.choice(HUNTER_SPECIES), size=self.rng.choice([2, 3, 4, 4, 5, 6, 8]),
                weapons=self.rng.sample(HUNTER_WEAPONS, 2), armor=self.rng.choice(HUNTER_ARMOR),
                tech=self.rng.sample(HUNTER_TECH, 2), sponsor=self.rng.choice(SPONSOR_PERKS),
                style=self.rng.choice(HUNT_STYLES), camp_type=self.rng.choice(CAMP_TYPES),
                camp_region=camp.label, patrol=self.rng.sample(adj, min(2, len(adj))))
            camp.camp = hp.leader
            hunters.append(hp)

        # Thorn wall.
        side = self.rng.choice(["north", "south", "east", "west"])
        days = self.days_override or self.cfg["days"][0]
        per = days / n
        sched = f"advances one {'row' if side in ('north', 'south') else 'column'} roughly every {per:.1f} days, reaching the far edge at collapse"

        jan = self.make_mob()
        jan.quirk = self.rng.choice(JANITOR_MECHANICS) + " Follow the thorn wall and clean what it leaves."
        jan.description = "Janitor mob. " + jan.description

        return Floor6(size=self.size, name=f"The {self.rng.choice(JUNGLE_ADJ)} Hunting Grounds", days=days,
                      janitor=jan, janitor_level=self.rng.randint(*self.cfg["janitor_level"]),
                      elite=base.elite, grid=n, cells=base.cells, stairs=stairs, scaling=self.scaling, seed=self.seed,
                      hunters=hunters, zockau_label=z.label, zockau_notes=list(ZOCKAU_NOTES),
                      thorn_side=side, thorn_schedule=sched, bounty=list(BOUNTY_BOARD))


def render6(fl: Floor6) -> str:
    settlements = [c for c in fl.cells if isinstance(c, Settlement)]
    regions = [c for c in fl.cells if isinstance(c, Region)]
    entry = next(r for r in regions if r.is_entry)
    bosses = [r.boss for r in regions if r.boss]
    lo, hi = FLOOR6_CFG["mob_band"]
    c6 = FLOOR6_CFG
    o = [f"# Floor 6 — {fl.name} ({fl.size})", "", "## Summary", ""]
    o.append(f"- **Countdown:** {fl.days} days. Hunters released at hour {c6['grace_hours']}.")
    o.append(f"- **Hunters:** {c6['hunters_total']} on the floor, all Level {c6['hunter_level']}; {len(fl.hunters)} parties detailed below.")
    o.append(f"- **Zones:** Zockau plus {len(settlements) - 1} settlements, {len(regions)} jungle regions")
    o.append(f"- **Bosses:** {sum(b.tier=='neighborhood' for b in bosses)} neighborhood, "
             f"{sum(b.tier=='borough' for b in bosses)} borough, {sum(b.tier=='city' for b in bosses)} city (all roam)")
    o.append(f"- **Stairwells:** {len(fl.stairs)}")
    for sw in fl.stairs:
        cell = next(c for c in fl.cells if c.label == sw.region_label)
        o.append(f"  - {sw.label} in {cell.label} ({cell.name}): {sw.condition}")
    o.append(f"- **Jungle mob levels:** {lo}–{hi} ({fl.scaling} scaling from the main settlement)")
    o.append(f"- **Thorn wall:** starts from the {fl.thorn_side} edge; {fl.thorn_schedule}.")
    o.append(f"- **Janitor mob:** {fl.janitor.name} (Level {fl.janitor_level}). {fl.janitor.quirk}")
    o.append(f"  - *{fl.janitor.description}*")
    o.append(f"- **Elite:** {fl.elite.name} — star of *{fl.elite.show}*. {fl.elite.form[0].upper() + fl.elite.form[1:]}. "
             f"Based in {fl.elite.settlement}; storyline plays out in {fl.elite.region}. {fl.elite.plot_armor}")
    o.append(f"- **Entry point:** {entry.label} — {entry.name} ({compass(entry.row, entry.col, fl.grid)}); "
             f"Zockau is {abs(entry.row) + abs(entry.col - fl.grid // 2)} cells away.")
    o.append(f"- **Seed:** {fl.seed}")
    o.append("")
    o.append("## Floor Rules")
    o.append("")
    for r in F6_RULES:
        o.append(f"- {r}")
    o.append("")
    o.append("## Layout")
    o.append("")
    o.append("Grid, north at top. `#` = settlement, `Z` = Zockau, `~` = river, `*` = entry, `S` = stairwell, `!` = boss, `X` = hunter camp.")
    o.append("")
    o.append("```")
    for r in range(fl.grid):
        row = []
        for c in fl.cells[r * fl.grid:(r + 1) * fl.grid]:
            tag = c.label
            if isinstance(c, Settlement):
                tag += "Z" if c.hostile else "#"
                if c.on_river: tag += "~"
            else:
                if c.river: tag += "~"
                if c.is_entry: tag += "*"
                if c.stairwell: tag += "S"
                if c.boss: tag += "!"
                if c.camp: tag += "X"
            row.append(f"[{tag:<5}]")
        o.append(" ".join(row))
    o.append("```")
    o.append("")
    o.append("## Zockau")
    o.append("")
    for note in fl.zockau_notes:
        o.append(f"- {note}")
    o.append("")
    o.append("## Hunter Parties")
    o.append("")
    for h in fl.hunters:
        o.append(f"### {h.leader}'s party ({h.species}, {h.size} hunters, Level {c6['hunter_level']})")
        o.append("")
        o.append(f"- **Camp:** {h.camp_type} in region {h.camp_region}. Patrols {', '.join(h.patrol)}.")
        o.append(f"- **Style:** {h.style}")
        o.append(f"- **Loadout:** {', '.join(h.weapons)}; {h.armor}; {', '.join(h.tech)}.")
        o.append(f"- **Sponsor perk:** {h.sponsor}.")
        o.append("")
    o.append("## Bounty Board")
    o.append("")
    for item, prize in fl.bounty:
        o.append(f"- **{item}:** {prize}.")
    o.append("")
    o.append("## Settlements")
    o.append("")
    for st in settlements:
        if st.hostile:
            continue
        o.append(f"### {st.label}. {st.name} ({st.size} {st.race.lower()} settlement{', on the river' if st.on_river else ''})")
        o.append("")
        o.append(f"- **Location:** {compass(st.row, st.col, fl.grid)}. Guards: Level {st.guard_level} Funeral Bells. "
                 f"Safe-room inn present{'; tutorial guild hall' if st.tutorial_guild else ''}"
                 f"{'; nightclub access' if st.club else ''}.")
        o.append(f"- **Shops:** {', '.join(st.shops)}")
        o.append(f"- **Race guildhalls:** {', '.join(st.race_guilds) if st.race_guilds else 'none'}")
        o.append(f"- **Class guildhalls:** {', '.join(st.class_guilds)}")
        o.append("- **Quests:**")
        for q in st.quests:
            o.append(f"  - **{q.title}** — from {q.giver}. {q.objective} Reward: {q.reward}.")
            o.append(f"    - *{q.flavor}*")
        o.append("")
    o.append("## Jungle Regions")
    o.append("")
    for reg in regions:
        adj = [c.label for c in fl.cells if abs(c.row - reg.row) + abs(c.col - reg.col) == 1]
        o.append(f"### {reg.label}. {reg.name}")
        o.append("")
        o.append(f"- **Location:** {compass(reg.row, reg.col, fl.grid)}. Adjacent to {', '.join(adj)}. "
                 f"Distance from hub: {reg.distance}{' (ENTRY)' if reg.is_entry else ''}.")
        if reg.river:
            o.append(f"- **River:** runs through here; crossing is {reg.river}. Naiads in the water.")
        o.append(f"- **Flora:** {reg.flora}")
        o.append(f"- **Mob:** {reg.mob.name}, Level {reg.level_range[0]}–{reg.level_range[1]}")
        o.append(f"  - {reg.mob.quirk}")
        o.append(f"  - Night: {reg.night}")
        o.append(f"  - *{reg.mob.description}*")
        o += second_mob_lines(reg)
        if reg.boss:
            b = reg.boss
            o.append(f"- **{b.tier.title()} Boss:** {b.name} (Level {b.level})")
            o.append(f"  - Form: {b.form}.")
            o.append(f"  - Roams between here and {reg.roams_to}." if reg.roams_to else "  - Stays in this region.")
            o.append(f"  - Mechanic: {b.mechanic}")
            o.append(f"  - Drops: a {boss_box(b.tier, fl.number)}")
        if reg.camp:
            o.append(f"- **Hunter camp:** {reg.camp}'s party (see Hunter Parties).")
        if reg.stairwell:
            o.append(f"- **Stairwell {reg.stairwell.label}:** {reg.stairwell.condition}.")
        o.append(f"- **Loot theme:** {reg.loot}")
        o.append(f"- **Notable feature:** {reg.feature}")
        o.append("")
    o.extend(crawler_section(fl))
    return "\n".join(o)


def render6_svg(fl: Floor6) -> str:
    n = fl.grid
    W = max(PAD * 2 + CELL * n, 800)
    key_lines = len(fl.stairs) + 2
    H = HEADER + PAD + CELL * n + LEGEND + 18 * key_lines + PAD
    ox, oy = (W - CELL * n) // 2, HEADER + PAD
    lo, hi = FLOOR6_CFG["mob_band"]
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="Helvetica, Arial, sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#f6f2ea"/>',
         _text(PAD, 32, f"Floor 6 — {fl.name}", size=22, weight="bold"),
         _text(PAD, 54, f"{fl.days} days  •  hunters released at hour {FLOOR6_CFG['grace_hours']}  •  jungle mobs L{lo}–{hi}  •  "
                        f"janitor: {fl.janitor.name} L{fl.janitor_level}  •  seed {fl.seed}", size=12, fill="#555")]
    by_pos = {(c.row, c.col): c for c in fl.cells}
    def _is_river(c):
        return (isinstance(c, Region) and bool(c.river)) or (isinstance(c, Settlement) and c.on_river)
    def _vert(c, x, y):
        below = by_pos.get((c.row + 1, c.col))
        if below and _is_river(c) and _is_river(below):
            o.append(f'<rect x="{x + CELL//2 - 14}" y="{y + CELL//2}" width="28" height="{CELL}" fill="#7fb3d5" opacity="0.5"/>')
    for c in fl.cells:
        x, y = ox + c.col * CELL, oy + c.row * CELL
        if isinstance(c, Settlement):
            fill = "#e8b4a0" if c.hostile else "#f2dfa8"
            stroke = "#8a1a1a" if c.hostile else "#8a5a1a"
            o.append(f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" fill="{fill}" stroke="{stroke}" stroke-width="5"/>')
            if c.on_river:
                o.append(f'<rect x="{x}" y="{y + CELL//2 - 14}" width="{CELL}" height="28" fill="#7fb3d5" opacity="0.6"/>')
            _vert(c, x, y)
            o.append(_text(x + 10, y + 26, c.label, size=24, weight="bold"))
            o.append(_text(x + 42, y + 24, _fit(c.name, 22), size=13, weight="bold"))
            if c.hostile:
                o.append(_text(x + 10, y + 48, "HUNTERS' CITY — guards hostile", size=12, weight="bold", fill="#8a1a1a"))
                o.append(_text(x + 10, y + 64, "Unguarded stairwell in the park", size=11, fill="#444"))
                o.append(_text(x + 10, y + 80, "Desperado Club  •  pub strip", size=11, fill="#444"))
            else:
                o.append(_text(x + 10, y + 48, f"{c.size.upper()} settlement — {c.race}", size=12))
                o.append(_text(x + 10, y + 64, "Funeral Bells L95  •  inn" + ("  •  guild" if c.tutorial_guild else "") + ("  •  club" if c.club else ""), size=11, fill="#444"))
                o.append(_text(x + 10, y + 82, "Shops: " + _fit(", ".join(c.shops), 32), size=11, fill="#444"))
                o.append(_text(x + 10, y + 98, f"Quests: {len(c.quests)}", size=11, fill="#444"))
            if c.name == fl.elite.settlement:
                o.append(_text(x + CELL - 10, y + CELL - 12, "ELITE", size=13, weight="bold", anchor="end", fill="#7a1f7a"))
        else:
            o.append(f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" fill="#cfd9b8" stroke="#555" stroke-width="1.5"/>')
            if c.river:
                o.append(f'<rect x="{x}" y="{y + CELL//2 - 14}" width="{CELL}" height="28" fill="#7fb3d5" opacity="0.7"/>')
                o.append(_text(x + CELL // 2, y + CELL // 2 + 4, "river", size=10, anchor="middle", fill="#1a4a6a"))
            _vert(c, x, y)
            o.append(_text(x + 10, y + 26, c.label, size=24, weight="bold"))
            o.append(_text(x + 42, y + 24, _fit(c.name, 22), size=13, weight="bold"))
            o.append(_text(x + 10, y + 48, _fit(c.mob.name, 30), size=12))
            o.append(_text(x + 10, y + 64, f"Level {c.level_range[0]}–{c.level_range[1]}", size=12))
            if c.boss:
                roam = f"roams → {c.roams_to}" if c.roams_to else "stays here"
                o.append(_text(x + 10, y + 84, f"{c.boss.tier.title()} boss: " + _fit(c.boss.name, 18), size=11, fill="#444"))
                o.append(_text(x + 10, y + 99, f"L{c.boss.level}, {roam}", size=11, fill="#444"))
                o.extend(_boss_marker(x + CELL - 30, y + CELL - 30, c.boss.tier, ""))
            if c.camp:
                cx_, cy_ = x + CELL - 50, y + 40
                o.append(f'<circle cx="{cx_}" cy="{cy_}" r="12" fill="none" stroke="#8a1a1a" stroke-width="3"/>')
                o.append(f'<line x1="{cx_-16}" y1="{cy_}" x2="{cx_+16}" y2="{cy_}" stroke="#8a1a1a" stroke-width="3"/>')
                o.append(f'<line x1="{cx_}" y1="{cy_-16}" x2="{cx_}" y2="{cy_+16}" stroke="#8a1a1a" stroke-width="3"/>')
                o.append(_text(cx_, cy_ + 30, _fit(c.camp, 14), size=10, weight="bold", anchor="middle", fill="#8a1a1a"))
            if c.stairwell:
                o.append(_stair_icon(x + 12, y + CELL - 26))
                o.append(_text(x + 32, y + CELL - 12, c.stairwell.label, size=11, weight="bold"))
            if c.is_entry:
                o.append(f'<rect x="{x+3}" y="{y+3}" width="{CELL-6}" height="{CELL-6}" fill="none" stroke="#c0392b" stroke-width="4"/>')
                o.append(_text(x + CELL // 2, y + CELL - 12, "ENTRY", size=14, weight="bold", anchor="middle", fill="#c0392b"))
    # Thorn wall: hatched band on the starting edge
    gx0, gy0, gx1, gy1 = ox, oy, ox + CELL * n, oy + CELL * n
    T = 16
    band = {"north": (gx0, gy0 - T, CELL * n, T), "south": (gx0, gy1, CELL * n, T),
            "west": (gx0 - T, gy0, T, CELL * n), "east": (gx1, gy0, T, CELL * n)}[fl.thorn_side]
    o.append('<defs><pattern id="thorn" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
             '<rect width="8" height="8" fill="#3a5a2a"/><line x1="0" y1="0" x2="0" y2="8" stroke="#9fd07a" stroke-width="3"/></pattern></defs>')
    o.append(f'<rect x="{band[0]}" y="{band[1]}" width="{band[2]}" height="{band[3]}" fill="url(#thorn)" stroke="#1a2a10" stroke-width="2"/>')
    o.append(f'<rect x="{gx0}" y="{gy0}" width="{CELL*n}" height="{CELL*n}" fill="none" stroke="#111" stroke-width="3"/>')
    lx, ly = PAD, oy + CELL * n + 36
    o.extend(_boss_marker(lx + 13, ly, "neighborhood", "")); o.append(_text(lx + 32, ly + 4, "Nbhd boss", size=11))
    o.extend(_boss_marker(lx + 130, ly, "borough", "")); o.append(_text(lx + 158, ly + 4, "Borough boss", size=11))
    o.extend(_boss_marker(lx + 270, ly, "city", "")); o.append(_text(lx + 306, ly + 4, "City boss", size=11))
    o.append(_stair_icon(lx + 380, ly - 8)); o.append(_text(lx + 402, ly + 4, "Stairwell", size=11))
    o.append(f'<circle cx="{lx+490}" cy="{ly}" r="8" fill="none" stroke="#8a1a1a" stroke-width="3"/>')
    o.append(f'<line x1="{lx+479}" y1="{ly}" x2="{lx+501}" y2="{ly}" stroke="#8a1a1a" stroke-width="3"/>')
    o.append(_text(lx + 506, ly + 4, "Hunter camp", size=11))
    o.append(f'<rect x="{lx+600}" y="{ly-9}" width="18" height="18" fill="url(#thorn)"/>')
    o.append(_text(lx + 624, ly + 4, f"Thorn wall start ({fl.thorn_side})", size=11))
    ky = ly + 52
    for sw in fl.stairs:
        o.append(_text(lx, ky, f"{sw.label} ({sw.region_label}): {_fit(sw.condition, 110)}", size=12)); ky += 18
    o.append(_text(lx, ky, f"Elite: {fl.elite.name} — {fl.elite.show} (based in {fl.elite.settlement})", size=12, fill="#7a1f7a"))
    o.append("</svg>")
    return "\n".join(o)


# ===========================================================================
# FLOOR 8 — THE GHOSTS OF EARTH
# One region: a polar map of three rings x N wedges around a starting
# structure, folklore mobs as T'Ghee totems, a Phase 2 deckmaster ladder.
# ===========================================================================

FLOOR8_CFG = {
    "days": 21,
    "phase1_days": 14,
    "radius_km": 99,
    "ring_bands": {1: (40, 50), 2: (50, 62), 3: (62, 75)},
    "deckmaster_levels": [40, 45, 50, 55, 60, 65, 70, 75, 80],
    "keymaster_level": 85,
    "key_km": (10, 15),
    "key_hours": 48,
    "squads_in_region": (60, 300),
}

SIZE8_CFG = {"small": {"wedges": 4, "rivals": 2}, "medium": {"wedges": 6, "rivals": 4}, "large": {"wedges": 8, "rivals": 6}}

F8_RULES = [
    "Squads are unbreakable, five crawlers at most. Each squad has six empty totem slots and must fill them before it can advance.",
    "The squad leader holds twenty flags. Drop any creature (ghosts included) to 5% health or below and plant a flag: it becomes a T'Ghee totem card.",
    "Totems summon for the duration printed on the card and are reusable even if the summoned monster dies.",
    "Utility cards target your own summoned totems. Snares only play while an enemy totem is out. Mystic cards act on squads, leaders, or decks.",
    "Consumable cards are gone the first time they're played, even in a practice arena. Cooldowns don't reset after practice.",
    "Totems can be stolen from other squads. At the start of Phase 2 every unplaced totem dissipates and a squad holds exactly six.",
    "Phase 1 (14 days): explore, capture, build. Difficulty rises with distance from the starting structure.",
    "Phase 2: ten numbered deckmasters seed the region, Level 40 to 80. From #4 on they have bodyguards. The first nine drop tokens; feed all nine to the vending machine to open the keymaster fight. The key opens only the stairwell next to your starting area and only for your squad.",
    "Phase 3 starts at midnight on December 26th. Key-holders are dropped at a random point in their region and race home. Everyone else is placed at a key-holder's stairwell door to block it.",
    "Every surviving squad keeps one T'Ghee card when the floor collapses.",
]


@dataclass
class TotemCard:
    name: str
    rarity: str
    duration: str
    ability: str


@dataclass
class Zone8:
    ring: int
    wedge: int
    label: str
    environment: str
    mob: str
    level_range: Tuple[int, int]
    hazard: str
    totem: TotemCard
    legendary: Optional[str] = None
    legendary_level: Optional[int] = None
    stairwell: Optional[str] = None      # "near" | "far"
    deckmasters: List[int] = field(default_factory=list)
    vending: bool = False
    key: bool = False
    key_km: Optional[int] = None


@dataclass
class Deckmaster:
    number: int
    level: int
    zone: str
    bodyguards: int
    quirk: str
    token: bool


@dataclass
class RivalSquad:
    name: str
    logo: str
    size: int
    totems: List[str]
    behavior: str


@dataclass
class Floor8:
    size: str
    archetype: str
    place: str
    structure: str
    coastal: bool
    days: int
    squads: int
    wedges: int
    zones: List[Zone8]
    deckmasters: List[Deckmaster]
    keymaster_level: int
    rivals: List[RivalSquad]
    loot_cards: list
    seed: int
    number: int = 8
    other_crawlers: List[CrawlerTeam] = field(default_factory=list)


class Generator8:
    FLOOR = 8

    def __init__(self, size, seed, days, region=None):
        self.rng = random.Random(f"floor{self.FLOOR}|{seed}")
        self.seed = seed
        self.size = size
        self.cfg = FLOOR8_CFG
        self.sc = SIZE8_CFG[size]
        self.days_override = days
        self.region = region

    def build(self) -> Floor8:
        c = self.cfg
        archetypes = list(FOLKLORE)
        arch = self.region if self.region in FOLKLORE else self.rng.choice(archetypes)
        fk = FOLKLORE[arch]
        place = self.rng.choice(fk["places"])
        coastal = self.rng.random() < 0.5
        W = self.sc["wedges"]
        commons = fk["common"][:]
        self.rng.shuffle(commons)
        legends = fk["legendary"][:]
        self.rng.shuffle(legends)

        zones = []
        ci = 0
        for ring in (1, 2, 3):
            lo, hi = c["ring_bands"][ring]
            for w in range(W):
                mob = commons[ci % len(commons)]
                ci += 1
                a = self.rng.randint(lo, hi - 4)
                rarity = {1: "Common", 2: "Uncommon", 3: "Rare"}[ring]
                if self.rng.random() < 0.2:
                    rarity = {1: "Uncommon", 2: "Rare", 3: "Epic"}[ring]
                dur = self.rng.choice(["30 s", "45 s", "60 s", "90 s", "2 min", "3 min"])
                totem = TotemCard(mob, rarity, dur, self.rng.choice(TOTEM_ABILITIES))
                zones.append(Zone8(ring, w, f"R{ring}W{w + 1}", self.rng.choice(ENVIRONMENTS[str(ring)]),
                                   mob, (a, a + self.rng.randint(4, 6)), self.rng.choice(GHOST_HAZARDS), totem))
        # Legendary creatures in the outer ring (about half the wedges) and one in ring 2.
        outer = [z for z in zones if z.ring == 3]
        self.rng.shuffle(outer)
        for z, leg in zip(outer[: max(1, W // 2)], legends):
            z.legendary = leg
            z.legendary_level = self.rng.randint(78, 90)
        mid = self.rng.choice([z for z in zones if z.ring == 2])
        mid.legendary = legends[-1]
        mid.legendary_level = self.rng.randint(66, 72)

        # Stairwells: near (ring 1) and far (ring 3, opposite side).
        near_w = self.rng.randrange(W)
        far_w = (near_w + W // 2) % W
        next(z for z in zones if z.ring == 1 and z.wedge == near_w).stairwell = "near"
        next(z for z in zones if z.ring == 3 and z.wedge == far_w).stairwell = "far"

        # Deckmasters 1-9 rise outward; keymaster fight at the vending machine.
        dms = []
        for i, lvl in enumerate(c["deckmaster_levels"], start=1):
            ring = 1 if i <= 3 else (2 if i <= 6 else 3)
            z = self.rng.choice([zz for zz in zones if zz.ring == ring])
            z.deckmasters.append(i)
            dms.append(Deckmaster(i, lvl, z.label, 0 if i < 4 else self.rng.randint(2, 6), self.rng.choice(DECK_QUIRKS), True))
        vend = self.rng.choice([z for z in zones if z.ring == 2 and not z.deckmasters] or [z for z in zones if z.ring == 2])
        vend.vending = True
        keyz = self.rng.choice([z for z in zones if z.ring == 2 and z is not vend])
        keyz.key = True
        keyz.key_km = self.rng.randint(*c["key_km"])

        rivals = []
        names = self.rng.sample(SQUAD_NAMES, self.sc["rivals"])
        for nm in names:
            rivals.append(RivalSquad(nm, self.rng.choice(SQUAD_LOGOS), self.rng.randint(2, 5),
                                     self.rng.sample(commons, self.rng.randint(1, 4)), self.rng.choice(SQUAD_BEHAVIOR)))

        loot = self.rng.sample(UTILITY_CARDS, 2) + self.rng.sample(SNARE_CARDS, 2) + self.rng.sample(MYSTIC_CARDS, 2)
        return Floor8(size=self.size, archetype=arch, place=place, structure=self.rng.choice(STRUCTURES), coastal=coastal,
                      days=self.days_override or c["days"], squads=self.rng.randint(*c["squads_in_region"]), wedges=W,
                      zones=zones, deckmasters=dms, keymaster_level=c["keymaster_level"], rivals=rivals,
                      loot_cards=loot, seed=self.seed)


def _wedge_dir(w, W):
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int(round(w * 8 / W)) % 8]


def render8(fl: Floor8) -> str:
    c = FLOOR8_CFG
    o = [f"# Floor 8 — {fl.place} ({fl.size})", "", "## Summary", ""]
    o.append(f"- **Countdown:** {fl.days} days; Phase 1 ends on day {c['phase1_days']}.")
    o.append(f"- **Region:** {fl.place} ({fl.archetype} folklore). Starting structure: {fl.structure}. "
             f"Explore up to {c['radius_km']} km{'; coastal, so no more than 6 km offshore' if fl.coastal else ''}.")
    o.append(f"- **Squads in the region:** roughly {fl.squads}. {len(fl.rivals)} rival squads detailed below.")
    o.append(f"- **Map:** 3 rings x {fl.wedges} wedges. Ring 1 mobs L{c['ring_bands'][1][0]}–{c['ring_bands'][1][1]}, "
             f"ring 2 L{c['ring_bands'][2][0]}–{c['ring_bands'][2][1]}, ring 3 L{c['ring_bands'][3][0]}–{c['ring_bands'][3][1]}.")
    near = next(z for z in fl.zones if z.stairwell == "near")
    far = next(z for z in fl.zones if z.stairwell == "far")
    o.append(f"- **Stairwells:** near — {near.label} ({_wedge_dir(near.wedge, fl.wedges)}, {near.environment}); "
             f"far — {far.label} ({_wedge_dir(far.wedge, fl.wedges)}, {far.environment}). Your key opens the near one only.")
    vend = next(z for z in fl.zones if z.vending)
    keyz = next(z for z in fl.zones if z.key)
    o.append(f"- **Phase 2 landmarks:** token vending machine in {vend.label} ({vend.environment}); "
             f"the key is marked in {keyz.label}, {keyz.key_km} km out, {c['key_hours']} hours to reach it once it appears.")
    o.append(f"- **Seed:** {fl.seed}")
    o.append("")
    o.append("## Floor Rules")
    o.append("")
    for r in F8_RULES:
        o.append(f"- {r}")
    o.append("")
    o.append("## Phase 1 — Zones")
    o.append("")
    o.append("Wedge 1 is north; wedges run clockwise. Rings run outward from the starting structure.")
    o.append("")
    for ring in (1, 2, 3):
        o.append(f"### Ring {ring}")
        o.append("")
        for z in [zz for zz in fl.zones if zz.ring == ring]:
            tags = []
            if z.stairwell: tags.append(f"{z.stairwell.upper()} STAIRWELL")
            if z.deckmasters: tags.append("deckmaster " + ", ".join(f"#{d}" for d in z.deckmasters))
            if z.vending: tags.append("VENDING MACHINE")
            if z.key: tags.append(f"KEY ({z.key_km} km)")
            tagtxt = f" — {'; '.join(tags)}" if tags else ""
            o.append(f"#### {z.label} ({_wedge_dir(z.wedge, fl.wedges)}) — {z.environment}{tagtxt}")
            o.append("")
            o.append(f"- **Mob:** {z.mob}, Level {z.level_range[0]}–{z.level_range[1]}")
            o.append(f"  - Totem card: **{z.totem.name}** [{z.totem.rarity}] — summon {z.totem.duration}. {z.totem.ability}")
            if z.legendary:
                o.append(f"- **Legendary:** {z.legendary} (Level {z.legendary_level}). Flaggable at 5% like anything else, if you can get it there.")
            o.append(f"- **Ghost hazard:** {z.hazard}")
            o.append("")
    o.append("## Phase 2 — Deckmasters")
    o.append("")
    o.append("| # | Level | Zone | Bodyguards | Deck | Token |")
    o.append("|---|-------|------|-----------|------|-------|")
    for d in fl.deckmasters:
        o.append(f"| {d.number} | {d.level} | {d.zone} | {d.bodyguards or '—'} | {d.quirk} | yes |")
    o.append(f"| KM | {fl.keymaster_level} | {vend.label} (vending machine) | 6 | "
             f"Opens when all nine tokens are fed in. Drops the key and a Champion Pack. | — |")
    o.append("")
    o.append("## Rival Squads")
    o.append("")
    for r in fl.rivals:
        o.append(f"- **{r.name}** (logo: {r.logo}; {r.size} crawlers). Known totems: {', '.join(r.totems)}. {r.behavior}")
    o.append("")
    o.append("## Card Loot (found in the region)")
    o.append("")
    for name, kind, effect in fl.loot_cards:
        o.append(f"- **{name}** [{kind}] — {effect}")
    o.extend(crawler_section(fl))
    return "\n".join(o)


def render8_svg(fl: Floor8) -> str:
    import math
    W, H = 1000, 1060
    cx, cy, R = 500, 500, 400
    rings = {1: R * 0.42, 2: R * 0.72, 3: R}
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="Helvetica, Arial, sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#f6f2ea"/>',
         _text(30, 36, f"Floor 8 — {fl.place}", size=22, weight="bold"),
         _text(30, 58, f"{fl.days} days  •  {fl.archetype} folklore  •  start: {fl.structure}  •  seed {fl.seed}", size=12, fill="#555")]
    ring_fill = {1: "#e9e2d0", 2: "#d9cfb8", 3: "#c8b99c"}
    n = fl.wedges
    for z in fl.zones:
        r0 = 0 if z.ring == 1 else rings[z.ring - 1]
        r1 = rings[z.ring]
        a0 = math.radians(-90 + z.wedge * 360 / n)
        a1 = math.radians(-90 + (z.wedge + 1) * 360 / n)
        big = 1 if (a1 - a0) > math.pi else 0
        p = (f'M {cx + r0*math.cos(a0):.1f},{cy + r0*math.sin(a0):.1f} '
             f'L {cx + r1*math.cos(a0):.1f},{cy + r1*math.sin(a0):.1f} '
             f'A {r1},{r1} 0 {big} 1 {cx + r1*math.cos(a1):.1f},{cy + r1*math.sin(a1):.1f} '
             f'L {cx + r0*math.cos(a1):.1f},{cy + r0*math.sin(a1):.1f} '
             f'A {r0},{r0} 0 {big} 0 {cx + r0*math.cos(a0):.1f},{cy + r0*math.sin(a0):.1f} Z')
        o.append(f'<path d="{p}" fill="{ring_fill[z.ring]}" stroke="#444" stroke-width="1.5"/>')
        mid = (a0 + a1) / 2
        rm = (r0 + r1) / 2 if z.ring > 1 else r1 * 0.62
        tx, ty = cx + rm * math.cos(mid), cy + rm * math.sin(mid)
        o.append(_text(tx, ty - 8, z.label, size=11, weight="bold", anchor="middle"))
        o.append(_text(tx, ty + 5, _fit(z.mob, 22), size=10, anchor="middle"))
        o.append(_text(tx, ty + 17, f"L{z.level_range[0]}–{z.level_range[1]}", size=9, anchor="middle", fill="#444"))
        yy = ty + 29
        if z.legendary:
            o.append(_text(tx, yy, "LEGEND: " + _fit(z.legendary, 20), size=9, weight="bold", anchor="middle", fill="#7a1f1f")); yy += 12
        if z.deckmasters:
            o.append(_text(tx, yy, "DM " + ",".join(f"#{d}" for d in z.deckmasters), size=9, weight="bold", anchor="middle", fill="#1f4a7a")); yy += 12
        if z.vending:
            o.append(_text(tx, yy, "VENDING", size=9, weight="bold", anchor="middle", fill="#1f4a7a")); yy += 12
        if z.key:
            o.append(_text(tx, yy, f"KEY {z.key_km} km", size=9, weight="bold", anchor="middle", fill="#8a5a1a")); yy += 12
        if z.stairwell:
            o.append(_text(tx, yy, f"{z.stairwell.upper()} STAIRWELL", size=9, weight="bold", anchor="middle", fill="#111"))
            o.append(_stair_icon(tx - 7, yy + 4))
    for rr in rings.values():
        o.append(f'<circle cx="{cx}" cy="{cy}" r="{rr}" fill="none" stroke="#222" stroke-width="2"/>')
    o.append(f'<rect x="{cx-16}" y="{cy-16}" width="32" height="32" fill="#222"/>')
    o.append(_text(cx, cy + 34, "START", size=10, weight="bold", anchor="middle"))
    ly = cy + R + 40
    o.append(_text(30, ly, f"Rings: 1 = L{FLOOR8_CFG['ring_bands'][1][0]}–{FLOOR8_CFG['ring_bands'][1][1]}, "
                            f"2 = L{FLOOR8_CFG['ring_bands'][2][0]}–{FLOOR8_CFG['ring_bands'][2][1]}, "
                            f"3 = L{FLOOR8_CFG['ring_bands'][3][0]}–{FLOOR8_CFG['ring_bands'][3][1]}   •   LEGEND = legendary creature   •   DM = deckmaster (Phase 2)", size=11))
    o.append(_text(30, ly + 20, "Wedge 1 is north, clockwise. Radius is roughly 99 km. Your key opens the near stairwell only.", size=11, fill="#555"))
    o.append("</svg>")
    return "\n".join(o)


# ===========================================================================
# FLOOR 9 — FACTION WARS
# Fixed pie of nine attacking slices around Larracos. The generator fills in
# warlords, politics, a projected outcome (DM), events, offers, and stances.
# ===========================================================================

FLOOR9_CFG = {
    "days": 30,
    "ceasefire_h": 60,
    "rampup_h": 180,
    "stairwells_total": 293,
    "forest_band": (60, 85),      # mob levels in the outer forest
}

SIZE9_CFG = {"small": {"events": 1, "mobs": 3, "stairs": 4}, "medium": {"events": 2, "mobs": 5, "stairs": 8}, "large": {"events": 3, "mobs": 8, "stairs": 12}}

F9_RULES = [
    "Nine attacking teams around Larracos like slices of a pie; the NPC defenders hold the city. Capture the castle at the center.",
    "Ceasefire: 60 hours from floor open. Ramp-Up: the next 180 hours. Then Open Hostilities. The Peeling of Larracos begins when four attackers remain; the city stays sealed until then.",
    "A team is eliminated when its warlord(s) die or its primary throne room is held for six hours. Its soldiers may join the victor or be slaughtered.",
    "Crawlers not sworn onto a team are subject to Conscription. Recruits are sworn in by an officer; officers are made by touch. Truces are formal declarations.",
    "Warlords can't use the crawler marketplace; they outfit armies in Larracos before it seals.",
    "293 stairwell chambers are seeded across the floor. They open when the war is won or 30 hours before collapse, whichever is first.",
    "A televised Warlord Council convenes a couple of days in.",
]


@dataclass
class Team:
    name: str
    sponsor: str
    color: str
    doctrine: str
    army: str
    reputation: List[str]
    warlord: str
    fortress: str
    slot: int
    wrinkle: str
    kings_point: bool = False
    is_party_team: bool = False
    wildcard: bool = False


@dataclass
class Offer:
    team: str
    terms: str
    hook: str


@dataclass
class Floor9:
    size: str
    days: int
    teams: List[Team]
    winner: str
    runner_up: str
    elimination: List[str]
    blocs: List[Tuple[str, List[str]]]
    events: dict
    forest_mobs: List[Tuple[str, Tuple[int, int]]]
    stairs: List[Tuple[int, str]]       # (slot, spot)
    offers: List[Offer]
    stances: dict
    criteria: List[str]
    warlord_mode: str
    seed: int
    number: int = 9
    other_crawlers: List[CrawlerTeam] = field(default_factory=list)


class Generator9:
    FLOOR = 9

    def __init__(self, size, seed, days, warlords="canon", wildcard=False, winner=None, runner_up=None):
        self.rng = random.Random(f"floor{self.FLOOR}|{seed}")
        self.seed = seed
        self.size = size
        self.sc = SIZE9_CFG[size]
        self.cfg = FLOOR9_CFG
        self.days_override = days
        self.warlord_mode = warlords
        self.wildcard = wildcard
        self.winner_arg = winner
        self.runner_arg = runner_up

    def gen_warlord(self):
        if not hasattr(self, "_used_wl"):
            self._used_wl = set()
        for _ in range(100):
            nm = f"{self.rng.choice(WARLORD_FIRST)} {self.rng.choice(WARLORD_NAME)}"
            if nm.split()[-1] not in self._used_wl:
                break
        self._used_wl.add(nm.split()[-1])
        return nm

    def build(self) -> Floor9:
        base = [dict(t) for t in TEAMS]
        slots = list(range(1, 10))
        self.rng.shuffle(slots)
        forts = FORTRESS[:]
        self.rng.shuffle(forts)
        teams = []
        for t, slot, fort in zip(base, slots, forts):
            is_carl = t.get("carl", False)
            if is_carl and self.wildcard:
                name = f"The {self.rng.choice(WILDCARD_ADJ)} {self.rng.choice(WILDCARD_NOUN)}"
                teams.append(Team(name, self.rng.choice(WILDCARD_SPONSOR), t["color"], self.rng.choice(WILDCARD_DOCTRINE),
                                  "whatever the sponsor could ship in time", ["No history. No reputation. No allies yet."],
                                  self.gen_warlord(), fort, slot, "", is_party_team=True, wildcard=True))
                continue
            if self.warlord_mode == "canon" and t.get("canon_warlord"):
                wl = t["canon_warlord"]
            else:
                wl = self.gen_warlord() + ("" if not t.get("canon_note") or self.warlord_mode != "canon" else f" ({t['canon_note']})")
            teams.append(Team(t["name"], t["sponsor"], t["color"], t["doctrine"], t["army"], list(t["reputation"]),
                              wl, fort, slot, "", kings_point=(t["name"] == "Bone Clan"), is_party_team=is_carl))
        wrinkles = ["Sponsor is behind on payments; the mercenaries know.", "Warlord is a figurehead; the adjutant runs the war.",
                    "Brought a Sixth-Floor artifact and has told nobody.", "Half its army is conscripted crawlers, and they talk.",
                    "Has already bought two truces it intends to break.", "Its superweapon is real and its supply line is not.",
                    "Running a side bet on its own elimination date.", "The warlord's heir is on the field and wants the job.",
                    "Its adjutant is the previous season's runner-up warlord."]
        self.rng.shuffle(wrinkles)
        for t, w in zip(teams, wrinkles):
            t.wrinkle = w
        names = [t.name for t in teams]
        party_team = next(t.name for t in teams if t.is_party_team)

        # Outcome: winner and runner-up fixed; elimination order from reputation.
        winner = self.winner_arg if self.winner_arg in names else party_team
        pool = [n for n in names if n != winner]
        if self.runner_arg in pool:
            runner = self.runner_arg
        else:
            strong = [n for n in ("Bone Clan", "The Reavers", "Prism Kingdom") if n in pool]
            runner = self.rng.choice(strong or pool)
        rest = [n for n in pool if n != runner]
        weight = {"Blood Sultanate": 0, "The Madness": 1, "Operatic Collective": 2, "Lemig Sortion": 2, "The Dream": 3,
                  "Prism Kingdom": 4, "The Reavers": 5, "Bone Clan": 5}
        rest.sort(key=lambda n: (weight.get(n, 2) + self.rng.random() * 1.5))
        elimination = rest + [runner, winner]

        # Blocs: one big bloc against the winner, one counter-bloc.
        others = [n for n in names if n != winner]
        self.rng.shuffle(others)
        k = self.rng.randint(4, 6)
        blocs = [(f"The Bloc (against {winner})", others[:k]), ("The Understanding", others[k:k + 2] or others[:2])]

        # Events by phase.
        ev = {}
        for phase, keyname in (("Ceasefire (0–60 h)", "ceasefire"), ("Ramp-Up (60–240 h)", "rampup"),
                               ("Open Hostilities (day 10 on)", "hostilities"), ("The Peeling (four teams left)", "peeling")):
            lines = []
            picks = self.rng.sample(PHASE_EVENTS[keyname], self.sc["events"])
            for tmpl in picks:
                a, b = self.rng.sample(names, 2)
                if keyname == "hostilities" and "primary base falls" in tmpl:
                    a = elimination[0]
                if keyname == "peeling" and "castle falls" in tmpl:
                    a, b = winner, runner
                lines.append(tmpl.format(A=a, B=b))
            ev[phase] = lines

        lo, hi = self.cfg["forest_band"]
        mobs = []
        for m in self.rng.sample(FOREST_MOBS, self.sc["mobs"]):
            a = self.rng.randint(lo, hi - 6)
            mobs.append((m, (a, a + self.rng.randint(4, 8))))
        stairs = [(self.rng.randint(1, 9), sp) for sp in self.rng.sample(STAIR_SPOTS, self.sc["stairs"])]

        # Competing offers: winner, runner-up, and one more.
        offer_teams = [winner, runner, self.rng.choice([n for n in names if n not in (winner, runner)])]
        offers = [Offer(tn, self.rng.choice(OFFER_TERMS), self.rng.choice(OFFER_HOOKS)) for tn in offer_teams]

        stances = {
            f"Help {winner}": self.rng.sample(HELP_MISSIONS, 3),
            f"Hinder {winner} (for {runner})": self.rng.sample(HINDER_MISSIONS, 3),
            "Stay out of it": self.rng.sample(NEUTRAL_ADVICE, 3),
        }
        return Floor9(size=self.size, days=self.days_override or self.cfg["days"], teams=sorted(teams, key=lambda t: t.slot),
                      winner=winner, runner_up=runner, elimination=elimination, blocs=blocs, events=ev,
                      forest_mobs=mobs, stairs=stairs, offers=offers, stances=stances, criteria=list(OUTCOME_CRITERIA),
                      warlord_mode=self.warlord_mode, seed=self.seed)


def render9(fl: Floor9) -> str:
    c = FLOOR9_CFG
    o = [f"# Floor 9 — Faction Wars ({fl.size})", "", "## Summary", ""]
    o.append(f"- **Countdown:** {fl.days} days. Ceasefire ends at hour {c['ceasefire_h']}; Ramp-Up ends at hour {c['ceasefire_h'] + c['rampup_h']} (day 10).")
    o.append(f"- **Teams:** nine attackers in slots 1–9 clockwise from north; {RETRIBUTION['name']} defends Larracos. {RETRIBUTION['note']}")
    kp = next(t for t in fl.teams if t.kings_point)
    o.append(f"- **King's Point:** slot {kp.slot} ({kp.name}), previous winner.")
    party = next(t for t in fl.teams if t.is_party_team)
    o.append(f"- **Crawler-friendly team:** {party.name} (slot {party.slot}){' — wildcard, replacing the canon team' if party.wildcard else ''}.")
    o.append(f"- **Warlords:** {fl.warlord_mode}.")
    o.append(f"- **Forest band mobs:** L{c['forest_band'][0]}–{c['forest_band'][1]}; {len(fl.stairs)} of {c['stairwells_total']} stairwell chambers placed below.")
    o.append(f"- **Seed:** {fl.seed}")
    o.append("")
    o.append("## Floor Rules")
    o.append("")
    for r in F9_RULES:
        o.append(f"- {r}")
    o.append("")
    o.append("## Teams")
    o.append("")
    for t in fl.teams:
        kp_tag = " (King's Point)" if t.kings_point else ""
        o.append(f"### Slot {t.slot}. {t.name}{kp_tag}")
        o.append("")
        o.append(f"- **Sponsor:** {t.sponsor}")
        o.append(f"- **Warlord:** {t.warlord}")
        o.append(f"- **Base:** {t.fortress}")
        o.append(f"- **Doctrine:** {t.doctrine}")
        o.append(f"- **Army:** {t.army}")
        o.append(f"- **Reputation:** {' '.join(t.reputation)}")
        o.append(f"- **This season:** {t.wrinkle}")
        o.append("")
    o.append("## Blocs")
    o.append("")
    for name, members in fl.blocs:
        o.append(f"- **{name}:** {', '.join(members)}")
    o.append("")
    o.append("## Timeline & Events")
    o.append("")
    for phase, lines in fl.events.items():
        o.append(f"### {phase}")
        o.append("")
        for ln in lines:
            o.append(f"- {ln}")
        o.append("")
    o.append("## Forest Band")
    o.append("")
    for m, (a, b) in fl.forest_mobs:
        o.append(f"- **{m}**, Level {a}–{b}")
    o.append("")
    o.append("## Stairwell Chambers")
    o.append("")
    for slot, sp in sorted(fl.stairs):
        tn = next(t.name for t in fl.teams if t.slot == slot)
        o.append(f"- Slot {slot} ({tn}'s slice): {sp}")
    o.append("")
    o.append("## Recruitment Offers (for the party)")
    o.append("")
    for of in fl.offers:
        o.append(f"- **{of.team}:** {of.terms} *{of.hook}*")
    o.append("")
    o.append("## Three Paths")
    o.append("")
    for name, lines in fl.stances.items():
        o.append(f"### {name}")
        o.append("")
        for ln in lines:
            o.append(f"- {ln}")
        o.append("")
    o.append("## DM Material")
    o.append("")
    o.append(f"- **Projected winner:** {fl.winner}. **Runner-up:** {fl.runner_up}.")
    o.append(f"- **Projected elimination order:** {' → '.join(fl.elimination)}")
    o.append("- **How the party can change it:**")
    for cr in fl.criteria:
        o.append(f"  - {cr}")
    o.extend(crawler_section(fl))
    return "\n".join(o)


def render9_svg(fl: Floor9) -> str:
    import math
    W, H = 1000, 1120
    cx, cy, R = 500, 520, 420
    r_city, r_forest = R * 0.22, R * 0.82
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="Helvetica, Arial, sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#f6f2ea"/>',
         _text(30, 36, "Floor 9 — Faction Wars", size=22, weight="bold"),
         _text(30, 58, f"{fl.days} days  •  nine attackers around Larracos  •  warlords: {fl.warlord_mode}  •  seed {fl.seed}", size=12, fill="#555")]
    n = 9
    for t in fl.teams:
        i = t.slot - 1
        a0 = math.radians(-90 + i * 360 / n); a1 = math.radians(-90 + (i + 1) * 360 / n)
        p = (f'M {cx + r_city*math.cos(a0):.1f},{cy + r_city*math.sin(a0):.1f} L {cx + R*math.cos(a0):.1f},{cy + R*math.sin(a0):.1f} '
             f'A {R},{R} 0 0 1 {cx + R*math.cos(a1):.1f},{cy + R*math.sin(a1):.1f} L {cx + r_city*math.cos(a1):.1f},{cy + r_city*math.sin(a1):.1f} '
             f'A {r_city},{r_city} 0 0 0 {cx + r_city*math.cos(a0):.1f},{cy + r_city*math.sin(a0):.1f} Z')
        o.append(f'<path d="{p}" fill="{t.color}" fill-opacity="0.55" stroke="#333" stroke-width="2"/>')
        mid = (a0 + a1) / 2
        tx, ty = cx + 0.62 * R * math.cos(mid), cy + 0.62 * R * math.sin(mid)
        o.append(_text(tx, ty - 10, f"{t.slot}. {t.name}", size=12, weight="bold", anchor="middle"))
        o.append(_text(tx, ty + 4, _fit(t.warlord.split(" (")[0], 26), size=10, anchor="middle", fill="#333"))
        o.append(_text(tx, ty + 17, _fit(t.fortress, 24), size=9, anchor="middle", fill="#444"))
        if t.kings_point:
            o.append(_text(tx, ty + 30, "KING'S POINT", size=9, weight="bold", anchor="middle", fill="#8a5a1a"))
        if t.is_party_team:
            o.append(_text(tx, ty + 43, "CRAWLER TEAM", size=9, weight="bold", anchor="middle", fill="#7a1f7a"))
        fx, fy = cx + 0.40 * R * math.cos(mid), cy + 0.40 * R * math.sin(mid)
        o.append(f'<rect x="{fx-8}" y="{fy-8}" width="16" height="16" fill="#222"/>')
    # forest band ring
    o.append(f'<circle cx="{cx}" cy="{cy}" r="{r_forest}" fill="none" stroke="#2f5a2f" stroke-width="2" stroke-dasharray="6,4"/>')
    o.append(_text(cx, cy - r_forest - 6 + 0, "", size=1))
    # stair chambers in the forest band
    for slot, sp in fl.stairs:
        i = slot - 1
        # crc32, not hash(): string hashing is salted per process, so hash() moved these
        # stair icons to new angles on every run of the same seed.
        ang = math.radians(-90 + (i + 0.2 + 0.6 * ((zlib.crc32(sp.encode("utf-8")) % 100) / 100)) * 360 / n)
        rr = r_forest + (R - r_forest) * 0.5
        sx, sy = cx + rr * math.cos(ang), cy + rr * math.sin(ang)
        o.append(_stair_icon(sx - 7, sy - 7))
    # Larracos
    for rr in (r_city, r_city * 0.7, r_city * 0.4):
        o.append(f'<circle cx="{cx}" cy="{cy}" r="{rr:.1f}" fill="#e8dcc0" stroke="#111" stroke-width="2"/>')
    o.append(f'<rect x="{cx-10}" y="{cy-10}" width="20" height="20" fill="#111"/>')
    o.append(_text(cx, cy + 4, "", size=1))
    o.append(_text(cx, cy - r_city - 8, "LARRACOS", size=10, weight="bold", anchor="middle"))
    o.append(f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="none" stroke="#111" stroke-width="4"/>')
    ly = cy + R + 40
    o.append(f'<rect x="30" y="{ly-8}" width="16" height="16" fill="#222"/>'); o.append(_text(52, ly + 4, "Team base (throne room)", size=11))
    o.append(_stair_icon(220, ly - 8)); o.append(_text(242, ly + 4, "Stairwell chamber (forest band)", size=11))
    o.append(f'<line x1="470" y1="{ly}" x2="500" y2="{ly}" stroke="#2f5a2f" stroke-width="2" stroke-dasharray="6,4"/>')
    o.append(_text(508, ly + 4, f"Forest band, mobs L{FLOOR9_CFG['forest_band'][0]}–{FLOOR9_CFG['forest_band'][1]}", size=11))
    o.append(_text(30, ly + 26, "Slots run clockwise from north. Larracos is sealed until four attackers remain. Projected outcome is in the Markdown's DM section, not on this map.", size=11, fill="#555"))
    o.append("</svg>")
    return "\n".join(o)


# ===========================================================================
# FLOOR 10 — DON'T COME IN LAST
# Seven AI-designed heats, a garage, a vehicle, an upgrade catalog with
# mechanical track requirements, rival fields, and a DM projection.
# ===========================================================================

SIZE10_CFG = {"small": {"rivals": 3}, "medium": {"rivals": 5}, "large": {"rivals": 8}}
TIER_RANK = {"none": 0, "Bronze": 1, "Silver": 2, "Gold": 3}


@dataclass
class Segment:
    number: int
    hazard: str
    note: str


@dataclass
class Rival:
    name: str
    vehicle: str
    style: str
    style_note: str
    threat: int
    targets_party: bool
    recurring: bool = False


@dataclass
class Heat:
    number: int
    environment: str
    req_tag: str
    req_tier: str
    danger: int
    env_note: str
    hint: str
    length_km: int
    segments: List[Segment]
    twist: str
    rivals: List[Rival]
    golden: bool = False
    projected_last: str = ""
    projected_party: int = 0
    offers: dict = field(default_factory=dict)


@dataclass
class Floor10:
    size: str
    party: int
    vehicle_kind: str
    chassis: str
    stats: dict
    quirk: str
    attendants: List[Tuple[str, str, str, str]]
    drivers: List[str]
    mercs: List[list]
    heats: List[Heat]
    catalog: dict
    audience: List[str]
    seed: int
    number: int = 10
    other_crawlers: List[CrawlerTeam] = field(default_factory=list)


class Generator10:
    FLOOR = 10

    def __init__(self, size, seed, vehicle=None, party=2):
        self.rng = random.Random(f"floor{self.FLOOR}|{seed}")
        self.seed = seed
        self.size = size
        self.sc = SIZE10_CFG[size]
        self.vehicle = vehicle
        self.party = max(2, min(4, party))

    def make_rival(self, name, recurring=False) -> Rival:
        style, note, threat = self.rng.choice(RIVAL_STYLES)
        kind = self.rng.choice(CHASSIS_MECH + CHASSIS_BIO)[0]
        return Rival(name, kind, style, note, threat, self.rng.random() < 0.4, recurring)

    def build(self) -> Floor10:
        kind = self.vehicle or self.rng.choice(["mechanical", "biological"])
        chassis, stats, quirk = self.rng.choice(CHASSIS_MECH if kind == "mechanical" else CHASSIS_BIO)
        att = []
        for role in ("mechanic", "medic/vet" if kind == "biological" else "assistant mechanic"):
            att.append((self.rng.choice(ATTENDANT_NAMES), self.rng.choice(ATTENDANT_SPECIES), role, self.rng.choice(ATTENDANT_QUIRKS)))
        drivers = [f"Member {(i % self.party) + 1}" for i in range(7)]
        mercs = self.rng.sample(MERCS, 3)

        envs = self.rng.sample(TRACK_ENVS, 7)
        envs.sort(key=lambda e: e[3] + self.rng.random() * 0.8)      # escalate, loosely
        recurring = [self.make_rival(n, True) for n in self.rng.sample(RIVAL_NAMES, 3)]
        rec_alive = recurring[:]
        used_names = {r.name for r in recurring}
        heats = []
        for i, (env, tag, tier, danger, note) in enumerate(envs, start=1):
            n_seg = self.rng.randint(3, 5)
            segs = []
            for k, hz in enumerate(self.rng.sample(HAZARDS, n_seg), start=1):
                segs.append(Segment(k, hz, f"{env} hazard: {hz}."))
            n_riv = self.sc["rivals"]
            field_ = rec_alive[:n_riv]
            while len(field_) < n_riv:
                nm = self.rng.choice([x for x in RIVAL_NAMES if x not in used_names] or RIVAL_NAMES)
                used_names.add(nm)
                field_.append(self.make_rival(nm))
            # projection: weakest by threat is last (never the party); party lands mid-pack shaded by danger
            weakest = min(field_, key=lambda r: r.threat + self.rng.random())
            if weakest in rec_alive:
                rec_alive.remove(weakest)
            party_pos = max(2, min(7, 3 + danger // 2 + self.rng.randint(-1, 1)))
            next_tag = envs[i][1] if i < len(envs) else None
            hint = HINTS[next_tag] if next_tag else "No hint. That was the last one."
            heats.append(Heat(i, env, tag, tier, danger, note, hint, self.rng.randint(12, 90), segs,
                              self.rng.choice(TWISTS), field_, golden=(i == 4), projected_last=weakest.name,
                              projected_party=party_pos))
        # Per-heat offers by finishing position.
        lanes = list(UPGRADES)
        for h in heats:
            offers = {}
            for pos, (n, top) in (("1st", (3, "Gold")), ("2nd–4th", (2, "Silver")), ("5th–7th", (1, "Bronze"))):
                picks = []
                pool = [(lane, u) for lane in lanes for u in UPGRADES[lane] if TIER_RANK[u[1]] <= TIER_RANK[top]]
                for lane, u in self.rng.sample(pool, min(n + 1, len(pool))):
                    picks.append(f"{u[0]} [{u[1]}, {lane}]")
                offers[pos] = picks
            offers["8th"] = ["audience vote (roll on the table)"]
            h.offers = offers
        return Floor10(size=self.size, party=self.party, vehicle_kind=kind, chassis=chassis, stats=stats, quirk=quirk,
                       attendants=att, drivers=drivers, mercs=mercs, heats=heats, catalog=UPGRADES,
                       audience=list(AUDIENCE_VOTES), seed=self.seed)


def render10(fl: Floor10) -> str:
    o = [f"# Floor 10 — Don't Come In Last ({fl.size})", "", "## Summary", ""]
    o.append("- **Format:** seven heats, nine teams per heat, level timer suspended.")
    o.append(f"- **Party:** {fl.party} crawlers. Driver rotation: " + ", ".join(f"H{i+1}: {d}" for i, d in enumerate(fl.drivers)) + ".")
    o.append(f"- **Vehicle:** {fl.chassis} ({fl.vehicle_kind}). Speed {fl.stats['speed']}, Toughness {fl.stats['toughness']}, "
             f"Handling {fl.stats['handling']}, Capacity {fl.stats['capacity']}. {fl.quirk}")
    o.append("- **Garage attendants:**")
    for nm, sp, role, q in fl.attendants:
        o.append(f"  - {nm}, {sp}, {role}. {q}")
    o.append("- **Mercenary board:**")
    for nm, role, does, catch in fl.mercs:
        o.append(f"  - {nm} ({role}): {does} *{catch}*")
    o.append(f"- **Pre-floor hint (heat 1):** \"{HINTS[fl.heats[0].req_tag]}\"")
    o.append(f"- **Seed:** {fl.seed}")
    o.append("")
    o.append("## Floor Rules")
    o.append("")
    for r in RACE_RULES:
        o.append(f"- {r}")
    o.append("")
    o.append("## Heats")
    o.append("")
    for h in fl.heats:
        o.append(f"### Heat {h.number} — {h.environment} (danger {h.danger}/5){'  •  GOLDEN UPGRADE AFTER THIS HEAT' if h.golden else ''}")
        o.append("")
        req = "none" if h.req_tier == "none" else f"{h.req_tag} at {h.req_tier}+"
        o.append(f"- **Requirement:** {req}. {h.env_note}")
        o.append(f"- **Length:** {h.length_km} km in {len(h.segments)} segments.")
        for sg in h.segments:
            o.append(f"  - Segment {sg.number}: {sg.hazard}")
        o.append(f"- **Twist:** {h.twist}")
        o.append(f"- **Hint for the next heat:** \"{h.hint}\"")
        o.append("- **Field:**")
        for r in h.rivals:
            o.append(f"  - {r.name}{' (recurring)' if r.recurring else ''} — {r.vehicle}; {r.style}, threat {r.threat}/5. {r.style_note}"
                     + (" They'll come for you." if r.targets_party else ""))
        o.append("- **Upgrade offers after this heat:**")
        for pos, picks in h.offers.items():
            o.append(f"  - {pos}: {'; '.join(picks)}")
        o.append("")
    o.append("## Upgrade Catalog")
    o.append("")
    o.append("| Lane | Upgrade | Tier | Tags | Effect |")
    o.append("|------|---------|------|------|--------|")
    for lane, ups in fl.catalog.items():
        for nm, tier, tags, eff in ups:
            o.append(f"| {lane} | {nm} | {tier} | {', '.join(tags)} | {eff} |")
    o.append("")
    o.append("Requirements are checked by tag and tier: any owned upgrade carrying the heat's tag at the listed tier or better satisfies it.")
    o.append("")
    o.append("## Audience Vote (second-to-last finish, roll d6)")
    o.append("")
    for i, a in enumerate(fl.audience, start=1):
        o.append(f"{i}. {a}")
    o.append("")
    o.append("## DM Projection")
    o.append("")
    o.append("Assumes the party races average and takes the offered upgrades. Ignore freely.")
    o.append("")
    o.append("| Heat | Party finishes | Eliminated (last) | Requirement met by |")
    o.append("|------|----------------|-------------------|--------------------|")
    for h in fl.heats:
        o.append(f"| {h.number} | {h.projected_party}{'st' if h.projected_party == 1 else 'nd' if h.projected_party == 2 else 'rd' if h.projected_party == 3 else 'th'} "
                 f"| {h.projected_last} | {h.req_tag} @ {h.req_tier} |")
    o.extend(crawler_section(fl))
    return "\n".join(o)


def render10_heat_svg(fl: Floor10, h: Heat) -> str:
    import math
    W, H = 1000, 620
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="Helvetica, Arial, sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#f6f2ea"/>',
         _text(30, 36, f"Heat {h.number} — {h.environment}", size=22, weight="bold"),
         _text(30, 58, f"danger {h.danger}/5  •  {h.length_km} km  •  {len(h.segments)} segments  •  requirement: {h.req_tag} @ {h.req_tier}  •  seed {fl.seed}", size=12, fill="#555"),
         _text(30, 78, f"Hint for the next heat: \"{h.hint}\"", size=12, fill="#8a5a1a")]
    # winding path through segment points
    n = len(h.segments)
    pts = [(80, 320)]
    for k in range(1, n + 1):
        x = 80 + k * (860 / (n + 1))
        y = 320 + (-110 if k % 2 else 110) * (0.6 + 0.4 * ((h.number * 7 + k * 13) % 5) / 4)
        pts.append((x, y))
    pts.append((940, 320))
    d = f"M {pts[0][0]},{pts[0][1]} "
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        cx_ = (x0 + x1) / 2
        d += f"C {cx_},{y0} {cx_},{y1} {x1},{y1} "
    o.append(f'<path d="{d}" fill="none" stroke="#333" stroke-width="22" stroke-linecap="round"/>')
    o.append(f'<path d="{d}" fill="none" stroke="#f6f2ea" stroke-width="3" stroke-dasharray="14,10"/>')
    o.append(f'<rect x="{pts[0][0]-14}" y="{pts[0][1]-30}" width="28" height="60" fill="#2e8b57"/>')
    o.append(_text(pts[0][0], pts[0][1] - 40, "START", size=11, weight="bold", anchor="middle"))
    o.append(f'<rect x="{pts[-1][0]-14}" y="{pts[-1][1]-30}" width="28" height="60" fill="#111"/>')
    o.append(_text(pts[-1][0], pts[-1][1] - 40, "FINISH", size=11, weight="bold", anchor="middle"))
    for k, sg in enumerate(h.segments, start=1):
        x, y = pts[k]
        o.append(f'<circle cx="{x}" cy="{y}" r="16" fill="#c0392b" stroke="#111" stroke-width="2"/>')
        o.append(_text(x, y + 5, str(k), size=13, weight="bold", anchor="middle", fill="#fff"))
        ly = y - 30 if k % 2 else y + 34
        o.append(_text(x, ly, _fit(sg.hazard, 26), size=11, weight="bold", anchor="middle"))
    o.append(_text(30, 500, "Twist: " + _fit(h.twist, 120), size=12, fill="#7a1f7a"))
    o.append(_text(30, 522, "Requirement: " + _fit(h.env_note, 130), size=11, fill="#333"))
    o.append(_text(30, 550, "Field: " + _fit(", ".join(r.name for r in h.rivals), 130), size=11, fill="#333"))
    if h.golden:
        o.append(_text(30, 578, "GOLDEN UPGRADE for everyone after this heat.", size=12, weight="bold", fill="#b8860b"))
    o.append("</svg>")
    return "\n".join(o)



# ===========================================================================
# FLOOR 11 — A PARADE OF HORRIBLES
# Build (90 min) → 3 km route → Judging Stands → arena → boss → stairwell.
# ===========================================================================

FLOOR11_CFG = {"build_min": 90, "route_km": 3, "judging_km": 2.0, "total": "3 h 15 min", "boss_level": (120, 150), "former_level": (60, 85)}
SIZE11_CFG = {"small": {"former": 2}, "medium": {"former": 4}, "large": {"former": 6}}


@dataclass
class FormerCrawler:
    name: str
    cls: str
    level: int
    how: str
    signature: str
    substitute: Optional[str] = None


@dataclass
class Floor11:
    size: str
    theme: str
    other_themes: List[str]
    ideas: List[str]
    grandmaster: str
    judges: List[str]
    criteria: List[str]
    route_events: List[Tuple[float, str]]
    boss: Boss
    formers: List[FormerCrawler]
    arena_mobs: List[str]
    seed: int
    number: int = 11
    other_crawlers: List[CrawlerTeam] = field(default_factory=list)


class Generator11:
    FLOOR = 11

    def __init__(self, size, seed):
        self.rng = random.Random(f"floor{self.FLOOR}|{seed}")
        self.seed = seed
        self.size = size

    def build(self) -> Floor11:
        themes = self.rng.sample(THEMES, 5)
        theme, others = themes[0], themes[1:]
        c = FLOOR11_CFG
        events = sorted((round(self.rng.uniform(0.2, 2.9), 1), ev) for ev in self.rng.sample(ROUTE_EVENTS, 4))
        lo, hi = c["boss_level"]
        # One boss, one name shape, eighteen themes: floor 11 had eighteen possible
        # boss names in total. The shapes give the Marshal somebody to be.
        first, second = self.rng.sample(BOSS_FIRST, 2)
        boss_name = self.rng.choice(PARADE_BOSS_NAME).format(
            theme=theme, first=first, second=second,
            surname=self.rng.choice(BOSS_SURNAME), nick=self.rng.choice(BOSS_NICKNAME))
        boss = Boss(boss_name, "floor", self.rng.randint(lo, hi), self.rng.choice(PARADE_BOSS_FORM),
                    " ".join(self.rng.sample(PARADE_BOSS_MECHANICS, 2)), "the arena", True)
        formers = []
        flo, fhi = c["former_level"]
        for nm in self.rng.sample(FORMER_FIRST, SIZE11_CFG[self.size]["former"]):
            sub = self.rng.choice(SUBSTITUTES) if self.rng.random() < 0.25 else None
            formers.append(FormerCrawler(nm, self.rng.choice(FORMER_CLASS), self.rng.randint(flo, fhi),
                                         self.rng.choice(FORMER_HOW), self.rng.choice(FORMER_SIG), sub))
        return Floor11(size=self.size, theme=theme, other_themes=others, ideas=list(FLOAT_IDEAS[theme]),
                       grandmaster=self.rng.choice(GRANDMASTER), judges=self.rng.sample(JUDGES, 3), criteria=list(CRITERIA),
                       route_events=events, boss=boss, formers=formers, arena_mobs=self.rng.sample(ARENA_MOBS, 2), seed=self.seed)


def render11(fl: Floor11) -> str:
    c = FLOOR11_CFG
    o = [f"# Floor 11 — A Parade of Horribles ({fl.size})", "", "## Summary", ""]
    o.append(f"- **Your group's theme:** **{fl.theme}**. Other groups: {', '.join(fl.other_themes)}.")
    o.append(f"- **Clock:** {c['build_min']} minutes to build, then a {c['route_km']} km route, judging at ~{c['judging_km']} km, arena at the end. Floor total {c['total']}.")
    o.append(f"- **Grand Master:** {fl.grandmaster}.")
    o.append(f"- **Arena:** {len(fl.formers)} former crawlers (or substitutes), tenth-floor mobs, and the floor boss. Stairwell opens on the boss's death.")
    o.append(f"- **Seed:** {fl.seed}")
    o.append("")
    o.append("## Floor Rules")
    o.append("")
    for r in PARADE_RULES:
        o.append(f"- {r}")
    o.append("")
    o.append(f"## Build Phase — theme: {fl.theme}")
    o.append("")
    o.append("Three starting points, in case the table stalls:")
    for i in fl.ideas:
        o.append(f"- {i}")
    o.append("")
    o.append("## Judging")
    o.append("")
    o.append("- **Judges:**")
    for j in fl.judges:
        o.append(f"  - {j}")
    o.append("- **Criteria:**")
    for cr in fl.criteria:
        o.append(f"  - {cr}")
    o.append("- Scores are cosmetic to the stairwell but not to the crowd: a group that scores highest enters the arena with the spectators' favor (one free re-roll, DM's call), lowest enters booed (the arena mobs target them first).")
    o.append("")
    o.append("## The Route")
    o.append("")
    for km, ev in fl.route_events:
        o.append(f"- **{km} km:** {ev}")
    o.append(f"- **{c['judging_km']} km:** the Judging Stands. Perform.")
    o.append(f"- **{c['route_km']} km:** the arena gates. They close behind the last float.")
    o.append("")
    o.append("## The Arena")
    o.append("")
    o.append(f"- **Floor Boss:** {fl.boss.name} (Level {fl.boss.level})")
    o.append(f"  - Form: {fl.boss.form}.")
    o.append(f"  - Mechanic: {fl.boss.mechanic}")
    o.append(f"  - Drops: a {boss_box(fl.boss.tier, fl.number)}")
    o.append("- **Former crawlers:**")
    for f_ in fl.formers:
        if f_.substitute:
            o.append(f"  - Substitute for {f_.name}: {f_.substitute} (Level {f_.level}). {f_.signature[0].upper() + f_.signature[1:]}.")
        else:
            o.append(f"  - {f_.name}, {f_.cls} (Level {f_.level}); {f_.how}. {f_.signature[0].upper() + f_.signature[1:]}.")
    o.append("- **Tenth-floor mobs:**")
    for m in fl.arena_mobs:
        o.append(f"  - {m}")
    o.append("- **Order of battle (suggested):** mobs first as the gates close; former crawlers when the boss is engaged; the boss's own waves per its mechanic.")
    o.extend(crawler_section(fl))
    return "\n".join(o)


def render11_svg(fl: Floor11) -> str:
    W, H = 1000, 560
    c = FLOOR11_CFG
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="Helvetica, Arial, sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#f6f2ea"/>',
         _text(30, 36, f"Floor 11 — A Parade of Horribles: {fl.theme}", size=22, weight="bold"),
         _text(30, 58, f"90 min build  •  3 km route  •  judging at ~2 km  •  arena  •  seed {fl.seed}", size=12, fill="#555")]
    x0, x1, y = 140, 820, 320
    o.append(f'<rect x="{x0}" y="{y-16}" width="{x1-x0}" height="32" fill="#e6c200" stroke="#8a5a1a" stroke-width="2"/>')
    for k in range(int((x1 - x0) / 24)):
        xx = x0 + k * 24
        o.append(f'<line x1="{xx}" y1="{y-16}" x2="{xx}" y2="{y+16}" stroke="#8a5a1a" stroke-width="1" opacity="0.5"/>')
    o.append(f'<rect x="{x0}" y="{y+16}" width="{x1-x0}" height="24" fill="#cfcfcf" opacity="0.6"/>')
    o.append(_text((x0 + x1) / 2, y + 110, "knee-high fog; spectators both sides; they cannot attack", size=10, anchor="middle", fill="#555"))
    # staging
    o.append(f'<rect x="{x0-105}" y="{y-40}" width="80" height="80" fill="#2e8b57" stroke="#111" stroke-width="2"/>')
    o.append(_text(x0 - 65, y - 50, "STAGING", size=11, weight="bold", anchor="middle"))
    o.append(_text(x0 - 65, y + 5, "safe room", size=10, anchor="middle", fill="#fff"))
    # km ticks
    for km in range(0, 4):
        xx = x0 + (x1 - x0) * km / 3
        o.append(f'<line x1="{xx}" y1="{y-24}" x2="{xx}" y2="{y-16}" stroke="#111" stroke-width="2"/>')
        o.append(_text(xx, y - 30, f"{km} km", size=10, anchor="middle"))
    # events
    for i, (km, ev) in enumerate(fl.route_events):
        xx = x0 + (x1 - x0) * km / 3
        yy = y - 60 - i * 34
        o.append(f'<circle cx="{xx}" cy="{y}" r="7" fill="#7a1f7a"/>')
        o.append(f'<line x1="{xx}" y1="{y-8}" x2="{xx}" y2="{yy+6}" stroke="#7a1f7a" stroke-width="1.5"/>')
        o.append(_text(xx, yy, _fit(ev, 44), size=10, anchor="middle", fill="#7a1f7a"))
    # judging stands
    jx = x0 + (x1 - x0) * c["judging_km"] / 3
    o.append(f'<rect x="{jx-40}" y="{y+45}" width="80" height="34" fill="#f2dfa8" stroke="#8a5a1a" stroke-width="2"/>')
    o.append(_text(jx, y + 67, "JUDGING STANDS", size=10, weight="bold", anchor="middle"))
    # arena
    o.append(f'<circle cx="{x1+80}" cy="{y}" r="70" fill="#d9d6cf" stroke="#111" stroke-width="3"/>')
    o.append(f'<circle cx="{x1+80}" cy="{y}" r="44" fill="#c0392b" opacity="0.35"/>')
    o.append(_text(x1 + 80, y - 6, "ARENA", size=12, weight="bold", anchor="middle"))
    o.append(_text(x1 + 80, y + 10, _fit(fl.boss.name, 20), size=10, anchor="middle"))
    o.append(_text(x1 + 80, y + 24, f"L{fl.boss.level}", size=10, anchor="middle", fill="#444"))
    o.append(_stair_icon(x1 + 73, y + 34))
    o.append(_text(30, 456, f"Floor boss: {fl.boss.name} (L{fl.boss.level}) — {_fit(fl.boss.form, 90)}", size=11))
    o.append(_text(30, 476, "Arena roster: " + _fit(", ".join((f.substitute or f"{f.name} ({f.cls})") for f in fl.formers), 130), size=11))
    o.append(_text(30, 496, f"Grand Master: {fl.grandmaster}", size=11, fill="#555"))
    o.append(_text(30, 516, "Other groups' themes: " + ", ".join(fl.other_themes), size=11, fill="#555"))
    o.append("</svg>")
    return "\n".join(o)


# ---------------------------------------------------------------------------
# Room detail for a pre-existing neighborhood (parsed from a text file)
# ---------------------------------------------------------------------------

def _extract_neighborhood_block(text: str, label: Optional[str]) -> str:
    """Isolate one '### X. Name' neighborhood section from a larger pasted file,
    so field regexes never accidentally read into a different neighborhood."""
    heads = list(re.finditer(r"^#{2,4}\s*([A-Za-z]+)\.\s*.+$", text, re.M))
    if not heads:
        raise SystemExit("--rooms-for: couldn't find any '### X. Name' neighborhood heading in the input file")
    if label:
        label = label.upper()
        chosen = next((h for h in heads if h.group(1).upper() == label), None)
        if chosen is None:
            raise SystemExit(f"--rooms-for: no neighborhood heading for label '{label}' found in the input file")
    else:
        chosen = heads[0]
    start = chosen.start()
    end = heads[heads.index(chosen) + 1].start() if chosen is not heads[-1] else len(text)
    return text[start:end]

def parse_neighborhood_block(block: str) -> Neighborhood:
    """Rebuild a Neighborhood from one pasted '### X. Name' section, as produced by this
    script (with or without the Rooms section) — enough to (re)generate room-level detail
    for pre-existing data without regenerating the whole floor."""
    heading = re.search(r"^#{2,4}\s*([A-Za-z]+)\.\s*(.+?)\s*$", block, re.M)
    if not heading:
        raise SystemExit("--rooms-for: couldn't find a '### X. Name' neighborhood heading in the input file")
    label, name = heading.group(1).upper(), heading.group(2)

    mob_m = re.search(
        r"\*\*Mob:\*\*\s*(?P<name>.+?),\s*Level\s*(?P<lo>\d+)[\u2013-](?P<hi>\d+)\s*\n"
        r"\s*-\s*(?P<quirk>.+?)\s*\n"
        r"\s*-\s*\*(?P<desc>.+?)\*", block)
    if not mob_m:
        raise SystemExit("--rooms-for: couldn't find a '- **Mob:** Name, Level X\u2013Y' block "
                          "(with its quirk and italic description lines) in the input file")
    mob_name = mob_m.group("name").strip()
    adj, _, noun = mob_name.partition(" ")
    mob = Mob(adj=adj, noun=noun or mob_name, quirk=mob_m.group("quirk").strip(),
              danger_mod=0, description=mob_m.group("desc").strip())
    level_range = (int(mob_m.group("lo")), int(mob_m.group("hi")))

    boss_m = re.search(
        r"\*\*(?:Neighborhood )?Boss:\*\*\s*(?P<name>.+?)\s*\(Level\s*(?P<level>\d+)\)\s*\n"
        r"\s*-\s*Form:\s*(?P<form>.+?)\s*\n"
        r"\s*-\s*Chamber:\s*(?P<chamber>.+?)\s*\n"
        r"\s*-\s*Mechanic:\s*(?P<mechanic>.+?)\s*\n"
        r"\s*-\s*Stairwell in chamber:\s*(?P<stair>yes|no)", block)
    if not boss_m:
        raise SystemExit("--rooms-for: couldn't find a '- **Neighborhood Boss:** ... (Level N)' block "
                          "(with Form/Chamber/Mechanic/Stairwell lines) in the input file")
    boss = Boss(name=boss_m.group("name").strip(), tier="neighborhood", level=int(boss_m.group("level")),
                form=boss_m.group("form").strip(), mechanic=boss_m.group("mechanic").strip(),
                chamber=boss_m.group("chamber").strip(), has_stairwell=boss_m.group("stair") == "yes")

    safe_m = re.search(r"\*\*Safe rooms:\*\*\s*(\d+)(.*)$", block, re.M)
    safe_rooms = int(safe_m.group(1)) if safe_m else 1
    tutorial_guild = bool(safe_m and "guild" in safe_m.group(2).lower())

    feature_m = re.search(r"\*\*Notable feature:\*\*\s*(.+)$", block, re.M)
    feature = feature_m.group(1).strip() if feature_m else ""

    hidden_stairs = [m.rstrip(".").strip() for m in re.findall(r"\*\*Hidden stairwell:\*\*\s*(.+)$", block, re.M)]

    loot_m = re.search(r"\*\*Loot theme:\*\*\s*(.+)$", block, re.M)
    loot = loot_m.group(1).strip() if loot_m else ""

    return Neighborhood(
        idx=0, label=label, name=name, row=0, col=0, borough=None, city=None, mob=mob,
        level_range=level_range, boss=boss, loot=loot, safe_rooms=safe_rooms,
        tutorial_guild=tutorial_guild, feature=feature, hidden_stairs=hidden_stairs,
    )

def render_rooms_only(nb: Neighborhood) -> str:
    o = [f"### {nb.label}. {nb.name} \u2014 Room Detail", "", f"- **Rooms:** ({len(nb.rooms)})"]
    for r in nb.rooms:
        conn = ", ".join(r.connects_to) if r.connects_to else "none"
        o.append(f"  - **{r.id} {r.kind}:** {r.description} \u2192 connects to {conn}")
    if nb.corridor_encounters:
        o.append("- **Corridor encounters:**")
        for a_id, b_id, text in nb.corridor_encounters:
            o.append(f"  - **{a_id} \u2194 {b_id}:** {text}")
    return "\n".join(o)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

# ===========================================================================
# GENERATION API
# The CLI and the GUI both go through generate(); it validates options, builds the
# floor, and returns the Markdown and map SVGs in memory without writing anything
# or printing. Callers decide what to do with the result.
# ===========================================================================

FLOORS = (1, 2, 3, 4, 5, 6, 8, 9, 10, 11)

# How many other teams a floor starts a campaign with, at its size.
TEAM_COUNT = {"small": 1, "medium": 2, "large": 3, "huge": 4,
              "giant": 5, "massive": 6, "continent": 7}

# How many groups the party newly runs into on each later floor. 8.1 million
# crawlers enter floor 1 and no party can know a fraction of them, so there are
# always new people to meet -- a roster is topped up every floor, not only when
# it has been thinned by deaths.
TEAM_NEW_PER_FLOOR = {"small": 1, "medium": 1, "large": 2, "huge": 2,
                      "giant": 3, "massive": 3, "continent": 4}

# Surviving a floor is evidence a team is good at surviving floors, so fatalities
# are weighted towards the least experienced rather than dealt blind. Weighting,
# not sorting: sorting funnelled every death onto the one or two newest teams,
# killing 59% of groups on the floor the party met them and leaving anyone four
# floors in effectively immortal at 1.6%. At this slope a veteran is around three
# times safer than a newcomer, which is a bias rather than a guarantee.
VETERAN_CAP = 4          # floors of experience past which no further credit accrues
VETERAN_SLOPE = 0.6      # how sharply that credit lowers the odds of drawing a death

# Floor 3 is where the System makes every surviving crawler choose a race and a
# class. The race is a permanent transformation and is never rerolled; the class
# has a documented upgrade for some picks, which lands at level 6 -- floor 6 in
# this generator, which is where the reference material puts the crawlers who
# take one. Groups the party meets on a later floor have already chosen, so they
# arrive with a party built and, past floor 6, already upgraded.
CLASS_FLOOR = 3
CLASS_UPGRADE_FLOOR = 6
_UPGRADED_CLASSES = frozenset(CRAWLER_CLASS_UPGRADE.values())

# How many crawlers are in a group the party runs into. Weighted towards three
# and four: a six-strong party that has held together this deep is a rarity, and
# should read as one.
PARTY_SIZES = (2, 3, 4, 5, 6)
PARTY_SIZE_WEIGHTS = (3, 5, 5, 3, 1)
PARTY_MAX = 8            # even a merged roster stops reading as a party past this

# What a report does to a group's numbers when it does not say otherwise. A team
# that had a costly floor lost someone; the other three outcomes leave the roster
# where it was. A status in the bank can override this by being written as
# [text, delta] -- or [text, delta, "upgrade"] for one that claims a class
# upgrade. A delta larger than any party just means "down to the last one",
# since a group is never thinned below its final survivor.
PARTY_DELTA = {"costly": -1}

_COUNT_WORD = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
               7: "seven", 8: "eight"}


def _render_floor(fl, floor: int) -> str:
    """Markdown for a built floor. Kept beside the build dispatch so the two lists
    of floors cannot drift apart."""
    renderers = {3: render3, 4: render4, 5: render5, 6: render6, 8: render8,
                 9: render9, 10: render10, 11: render11}
    text = renderers.get(floor, render)(fl)
    # What's switched on, and the loot boxes, exist on every floor, so their
    # sections are added here for all of them rather than taught to nine
    # renderers one at a time. The box section reads the floor's own body, so
    # it is worked out before the systems go on.
    body = floor_body(text)
    beasts = bestiary_section(fl)
    if beasts:
        text = text.rstrip("\n") + "\n\n" + "\n".join(beasts)
    text = text.rstrip("\n") + "\n\n" + "\n".join(systems_section(floor))
    shops = shops_section(floor)
    if shops:
        text = text.rstrip("\n") + "\n\n" + "\n".join(shops)
    text = text.rstrip("\n") + "\n\n" + "\n".join(loot_box_section(fl, body))
    # The stat rules go on here rather than inside crawler_section, so that they
    # land at the end of every floor instead of wherever that floor happens to
    # print its roster -- on floors 1 and 2 that is halfway up, ahead of the loot
    # boxes and the achievements, which is no place for a reference table.
    if getattr(fl, "other_crawlers", None):
        text = text.rstrip("\n") + "\n\n" + "\n".join(stat_rules_section())
    return text


def _team_rng(seed: int, floor: int) -> random.Random:
    """A stream of its own, so the roster is reproducible from the floor seed
    without team changes shifting every later roll on the floor itself."""
    return random.Random(f"crawlers|{seed}|{floor}")


def _weighted_sample(rng, weights: List[float], k: int) -> List[int]:
    """k distinct indices, each drawn with probability proportional to its weight."""
    pool = list(range(len(weights)))
    chosen = []
    for _ in range(min(k, len(pool))):
        cut = rng.random() * sum(weights[i] for i in pool)
        running = 0.0
        pick = pool[-1]
        for i in pool:
            running += weights[i]
            if running >= cut:
                pick = i
                break
        chosen.append(pick)
        pool.remove(pick)
    return chosen


def _pick_distinct(rng, bank, k: int) -> list:
    """k picks with no repeats, topping up with repeats only if the bank is short."""
    if k <= 0:
        return []
    if k <= len(bank):
        return rng.sample(bank, k)
    return rng.sample(bank, len(bank)) + [rng.choice(bank) for _ in range(k - len(bank))]


def _where_phrases(rng, fl, count: int) -> List[str]:
    """A place phrase per team, in whatever terms the floor has to offer.

    Floors 1-2 can name a neighborhood; the rest have no comparable grid yet, so
    they get deliberately vague phrasing until each floor learns to name its own
    ground."""
    nbhds = getattr(fl, "neighborhoods", None)
    if nbhds:
        return [f"in neighborhood {nb.label}" for nb in _pick_distinct(rng, nbhds, count)]
    # Floor 5: a quadrant and a starting site, where the crawlers are locked in
    # until their castle falls. A group lands at a site as often as crawlers
    # start there, and no two groups share one while there are sites to spare.
    sites = [(q, s) for q in getattr(fl, "quadrants", None) or [] for s in q.sites]
    if sites:
        out = []
        while len(out) < count:
            pool = list(sites)
            while pool and len(out) < count:
                q, s = rng.choices(pool, weights=[s_.crawlers for _, s_ in pool])[0]
                pool.remove((q, s))
                out.append(f"in the {q.name} quadrant, near {s.name}")
        return out
    return _pick_distinct(rng, TEAM_PLACE_GENERIC, count)


def _fresh_teams(rng, count: int, floor: int, taken_surnames, taken_origins) -> List[CrawlerTeam]:
    """New groups met on this floor, avoiding any name already on the roster."""
    origins = [o for o in TEAM_ORIGIN if o not in taken_origins] or list(TEAM_ORIGIN)
    # Surnames are excluded by the bare place name rather than the rendered one, or
    # "The Dry Gulch Holdouts" dying on floor 4 leaves "The Dry Gulch Crew" free to
    # turn up on floor 6 looking like the same people.
    free = [n for n in TEAM_SURNAME if n not in taken_surnames] or list(TEAM_SURNAME)
    out = []
    for surname, origin in zip(_pick_distinct(rng, free, count),
                              _pick_distinct(rng, origins, count)):
        # The form is chosen later, once outcomes are known, so that a diminished
        # name lands only on a team that lost something.
        out.append(CrawlerTeam(name=f"Team {surname}", surname=surname, origin=origin,
                               first_seen=floor, floor=floor))
        taken_surnames.add(surname)
    return out


def upgraded_class(name: str) -> str:
    """The level 6 form of a class, or the class itself if it has no documented one.

    Idempotent, so running a team through it again on floors 7+ is a no-op rather
    than a second promotion."""
    return CRAWLER_CLASS_UPGRADE.get(name, name)


def status_text(entry) -> str:
    """The report itself, whether the bank wrote it bare or with a party change."""
    return entry[0] if isinstance(entry, (list, tuple)) else entry


def status_change(entry, outcome: str) -> Tuple[int, Optional[str]]:
    """How many crawlers a report costs or gains the group, and any class upgrade
    it claims. Bare strings fall back to whatever the outcome usually does, so a
    bank edited by hand needs no new syntax."""
    if isinstance(entry, (list, tuple)):
        return entry[1], (entry[2] if len(entry) > 2 else None)
    return PARTY_DELTA.get(outcome, 0), None


def _new_members(rng, n: int, existing: List[dict]) -> List[dict]:
    """n crawlers who share neither a race nor a class with anyone already there.
    Duplicates are possible in life but read as a generator slip, and both banks
    are far larger than any party."""
    if n <= 0:
        return []
    taken = {m["race"] for m in existing}
    races = [r for r in CRAWLER_RACE if r not in taken]
    # A class is off the table if the party already has it *or* already has what it
    # upgrades into: a Rogue joining a group that has a Rogue Trapmaster becomes a
    # second Rogue Trapmaster the moment the floor deals an upgrade.
    taken = {m["class"] for m in existing}
    classes = [c for c in CRAWLER_CLASS
               if c not in taken and upgraded_class(c) not in taken]
    return [{"race": race, "class": klass} for race, klass
            in zip(_pick_distinct(rng, races, n), _pick_distinct(rng, classes, n))]


def _deal_party(rng, team: CrawlerTeam, floor: int,
                change: Tuple[int, Optional[str]] = (0, None)) -> None:
    """Bring a team's crawlers up to date for the floor they are reported on.

    In order: a group that has not chosen yet picks races and classes, the floor's
    report adds or takes away crawlers, and anyone owed a class upgrade takes it.
    Upgrades come last so that crawlers who joined a merged roster this floor are
    promoted alongside the ones who were already there."""
    delta, effect = change
    if floor < CLASS_FLOOR:
        # Nobody has chosen yet on floors 1-2, so a group only has a number if the
        # DM gave it one -- and then the report moves the number the way it would
        # move a party, or "lost one of their own" would leave the count where it was.
        if team.headcount:
            team.headcount = max(1, team.headcount
                                 + min(delta, max(0, PARTY_MAX - team.headcount)))
        return
    if not team.members:
        # A group the DM sized on floor 1 or 2 is dealt that many; anyone else gets
        # the usual roll.
        n = team.headcount or rng.choices(PARTY_SIZES, weights=PARTY_SIZE_WEIGHTS)[0]
        team.members = _new_members(rng, n, [])
        team.headcount = None
    if delta > 0:
        team.members += _new_members(rng, min(delta, PARTY_MAX - len(team.members)),
                                     team.members)
    elif delta < 0:
        # Never thinned below the last survivor: a group with nobody left is a
        # fatality, which is a separate outcome with its own bookkeeping.
        for _ in range(min(-delta, len(team.members) - 1)):
            team.members.pop(rng.randrange(len(team.members)))
    # Below floor 6 only a group whose report says it outlevelled the floor takes
    # its upgrade early; from floor 6 everyone owed one has it.
    if floor >= CLASS_UPGRADE_FLOOR or effect == "upgrade":
        for m in team.members:
            m["class"] = upgraded_class(m["class"])
    if effect == "upgrade" and not any(m["class"] in _UPGRADED_CLASSES
                                       for m in team.members):
        # A sixth of the classes in the bank have a documented upgrade, so about
        # half of all parties draw this report with nobody able to show it.
        # Rather than print a line with nothing under it, one crawler is re-dealt
        # a class that does have a path, and takes it.
        taken = {m["class"] for m in team.members}
        paths = [upgraded_class(c) for c in CRAWLER_CLASS_UPGRADE
                 if upgraded_class(c) not in taken]
        team.members[rng.randrange(len(team.members))]["class"] = rng.choice(paths)


def _article(word: str) -> str:
    return "an" if word[:1].upper() in "AEIOU" else "a"


def party_line(team: CrawlerTeam) -> str:
    """One line naming what the group is made of, for the Other Crawlers section.

    Before floor 3 nobody has a race or a class, so a group the DM gave a number
    to is only that number."""
    if not team.members:
        n = team.headcount or 0
        word = _COUNT_WORD.get(n, str(n))
        if not team.alive:
            return "There was one of them." if n == 1 else f"There were {word} of them."
        return "Down to one crawler." if n == 1 else f"{word.capitalize()} crawlers."
    who = ", ".join(f"{_article(m['race'])} {m['race']} {m['class']}"
                    for m in team.members)
    n = len(team.members)
    if not team.alive:
        # Past tense for a group that is already on the death feed, and kept rather
        # than dropped: floor 11 parades crawlers who died floors ago.
        return f"Who they were: {who}."
    if n == 1:
        return f"Down to one crawler: {who}."
    return f"{_COUNT_WORD.get(n, str(n)).capitalize()} crawlers: {who}."


def build_crawler_teams(fl, size: str, seed: int, floor: int,
                        roster: Optional[CampaignRoster] = None) -> CampaignRoster:
    """Deal this floor's roster of other survivors and return the updated campaign.

    Every team still alive carries over with its name, origin and standing intact;
    the floor tops the roster up to its size's target with groups newly met. Each
    active team then gets a fresh outcome, status, place and day -- a team that was
    thriving on floor 1 is dealt again on floor 2.
    """
    rng = _team_rng(seed, floor)
    roster = roster or CampaignRoster()
    days = getattr(fl, "days", None)

    active = roster.active()
    for t in active:
        t.floor = floor
    taken_surnames = {t.surname for t in roster.teams if t.surname}
    taken_origins = {t.origin for t in roster.teams}
    # A campaign meets new groups on every floor, not just when deaths have left
    # room: the party's roster grows and turns over rather than dwindling to
    # nobody by floor 5. An empty roster is a campaign's first floor, which starts
    # at the size's full count.
    need = TEAM_COUNT[size] if not active else TEAM_NEW_PER_FLOOR[size]
    fresh = _fresh_teams(rng, need, floor, taken_surnames, taken_origins)
    roster.teams.extend(fresh)
    active = active + fresh
    n = len(active)
    if not n:
        return roster

    # Deal outcomes by walking the kinds from a random start so a floor gets a
    # spread of fates rather than however many deaths independent rolls produce.
    kinds = list(TEAM_STATUS)
    start = rng.randrange(len(kinds))
    outcomes = [kinds[(start + i) % len(kinds)] for i in range(n)]
    rng.shuffle(outcomes)
    # Same mix of fates; who draws the fatal ones is weighted by experience.
    fatalities = outcomes.count("fatal")
    if fatalities and fatalities < n:
        risk = [1.0 / (1.0 + min(len(t.history), VETERAN_CAP) * VETERAN_SLOPE)
                for t in active]
        doomed = set(_weighted_sample(rng, risk, fatalities))
        spare = [o for o in outcomes if o != "fatal"]
        outcomes = ["fatal" if i in doomed else spare.pop() for i in range(n)]
    # Walk `kinds`, not set(outcomes): this draws from the rng, and a set of
    # strings iterates in per-process hash order.
    pools = {k: iter(_pick_distinct(rng, TEAM_STATUS[k], outcomes.count(k)))
             for k in kinds if k in outcomes}
    wheres = _where_phrases(rng, fl, n)

    # Standings are only dealt to teams that do not have one yet: a carried team
    # keeps where it stands with the party, including wherever the party moved it.
    # A team the DM added by hand is never a newcomer here, even on the floor it
    # was met: renaming it, or dealing it a standing, would overwrite what the DM
    # wrote down.
    newcomers = [i for i, t in enumerate(active) if t.first_seen == floor and not t.added]
    spread = _pick_distinct(rng, TEAM_DISPOSITION, len(newcomers))
    for i, disp in zip(newcomers, spread):
        active[i].disposition = disp["label"]

    # A name that says the team was cut down goes only to one that was, and only
    # to a team meeting the party for the first time -- renaming a group the party
    # already knows would lose the thread.
    hurt = [i for i in newcomers if outcomes[i] in ("costly", "fatal")]
    diminished = set(rng.sample(hurt, len(hurt) // 2)) if hurt else set()
    dim_forms = iter(_pick_distinct(rng, TEAM_NAME_FORM_DIMINISHED, len(diminished)))
    neutral = iter(_pick_distinct(rng, TEAM_NAME_FORM,
                                  len(newcomers) - len(diminished)))

    for i, team in enumerate(active):
        team.outcome = outcomes[i]
        entry = next(pools[outcomes[i]])
        team.status = status_text(entry).format(where=wheres[i])
        if days:
            # One team per slice of the countdown, jittered inside its slice:
            # rolling each freely piles half the roster onto the same day.
            lo = 1 + (i * days) // n
            team.day = rng.randint(lo, max(lo, ((i + 1) * days) // n))
        else:
            team.day = None                 # floors 10 and 11 have no day count
        if i in newcomers:
            form = next(dim_forms) if i in diminished else next(neutral)
            team.name = form.format(name=team.surname)
        # After the report is drawn, so that a status claiming the group lost half
        # its number, merged with another team or took a class upgrade is the same
        # thing the party line underneath it says.
        _deal_party(rng, team, floor, status_change(entry, team.outcome))
        if team.outcome == "fatal":
            team.died_on = floor
        entry = {"floor": floor, "day": team.day,
                 "outcome": team.outcome, "status": team.status}
        if team.crawler_count:
            entry["crawlers"] = team.crawler_count
        team.history.append(entry)

    return roster


def find_team(roster: CampaignRoster, name: str) -> CrawlerTeam:
    """A team on the roster by name, case-insensitively, allowing a unique partial
    so a DM does not have to retype "What's Left of Juniper Flats" exactly.
    Raises MapgenError if nothing matches or more than one team does."""
    if roster is None or not roster.teams:
        raise MapgenError(f"no teams on the roster yet, so {name!r} cannot be found")
    wanted = name.strip().lower()
    exact = [t for t in roster.teams if t.name.lower() == wanted]
    matches = exact or [t for t in roster.teams if wanted in t.name.lower()]
    if not matches:
        known = ", ".join(t.name for t in roster.teams)
        raise MapgenError(f"no team named {name!r} on the roster. Known: {known}")
    if len(matches) > 1:
        raise MapgenError(f"{name!r} matches more than one team: "
                          + ", ".join(t.name for t in matches))
    return matches[0]


def set_standing(roster: CampaignRoster, name: str, standing: str) -> CrawlerTeam:
    """Move a team's standing with the party. See find_team for the matching."""
    team = find_team(roster, name)
    team.disposition = standing
    return team


def team_outcomes() -> List[str]:
    """The fates a team's report can carry, mildest first: steady, thriving,
    costly, fatal. Fatal is the one that kills them."""
    return list(TEAM_STATUS)


def _check_outcome(outcome: str) -> None:
    if outcome not in TEAM_STATUS:
        raise MapgenError(f"a team's outcome is one of {', '.join(team_outcomes())} "
                          f"(got {outcome!r})")


def _check_crawlers(crawlers: Optional[int]) -> None:
    if crawlers is not None and (not isinstance(crawlers, int) or crawlers < 1):
        raise MapgenError(f"a group has at least one crawler (got {crawlers!r}); "
                          "one with nobody left is dead, which is its fate")


def _resize_party(team: CrawlerTeam, crawlers: int) -> None:
    """Make a group the size the DM says.

    Before floor 3 that is only a number, kept until floor 3 deals that many
    crawlers. From floor 3 the group has chosen, so the count is its members:
    raising it deals newcomers who share no race or class with anyone already
    there, as a merge on a generated floor does; lowering it removes the most
    recent to join. A group past floor 6 arrives with its upgrades taken."""
    if not team.members and team.floor < CLASS_FLOOR:
        team.headcount = crawlers
        return
    if crawlers > len(team.members):
        # Seeded from the group and its size, so the same edit deals the same
        # crawlers, and the floor's own stream is never touched.
        rng = random.Random(f"joined|{team.name}|{team.floor}|{len(team.members)}")
        team.members += _new_members(rng, crawlers - len(team.members), team.members)
    else:
        del team.members[crawlers:]
    if team.floor >= CLASS_UPGRADE_FLOOR:
        for m in team.members:
            m["class"] = upgraded_class(m["class"])
    team.headcount = None


def add_team(roster: CampaignRoster, name: str, *, origin: str = "",
             standing: str = "wary", floor: int = 1, status: str = "",
             outcome: str = "steady", crawlers: Optional[int] = None) -> CrawlerTeam:
    """Add a team the party met at the table rather than one the generator dealt.

    From the next floor generated on, it is carried like any other team: it is
    dealt a fresh report, picks races and classes on floor 3, can thrive or die.
    What the generator never does is rename it or re-deal its standing, both of
    which it does to a team it has just met itself.

    `crawlers` is how many are in it. Met on floor 3 or deeper, they have already
    chosen, so that many are dealt races and classes at once; met earlier, the
    number is kept and floor 3 deals that many. Left as None, the generator sizes
    the group when it deals the party, as it does any other. Raises MapgenError
    for an empty or duplicate name, an outcome that is not one of team_outcomes(),
    or fewer than one crawler."""
    clean = name.strip()
    if not clean:
        raise MapgenError("a team needs a name")
    if roster is None:
        raise MapgenError("there is no campaign to add a team to")
    taken = [t.name for t in roster.teams if t.name.lower() == clean.lower()]
    if taken:
        raise MapgenError(f"there is already a team called {taken[0]!r} on the roster")
    _check_outcome(outcome)
    _check_crawlers(crawlers)
    if floor < 1:
        raise MapgenError(f"a team is met on floor 1 or deeper (got {floor})")
    team = CrawlerTeam(
        name=clean, origin=origin.strip() or "an unrecorded group",
        # The full name stands in for the place name the generator reserves, so
        # it never deals a second team under the same name.
        surname=clean, disposition=standing.strip() or "wary",
        status=status.strip(), outcome=outcome, floor=floor, first_seen=floor,
        died_on=floor if outcome == "fatal" else None, added=True)
    if crawlers is not None:
        _resize_party(team, crawlers)
    entry = {"floor": floor, "day": None, "outcome": outcome,
             "status": team.status, "by_dm": True}
    if team.crawler_count:
        entry["crawlers"] = team.crawler_count
    team.history.append(entry)
    roster.teams.append(team)
    return team


def set_status(roster: CampaignRoster, name: str, *, status: Optional[str] = None,
               outcome: Optional[str] = None,
               crawlers: Optional[int] = None) -> CrawlerTeam:
    """Rewrite a team's latest report, its fate, how many crawlers it has, or any
    of them.

    A fatal outcome kills the team on the floor it was last seen, and it stops
    carrying to later floors; any other outcome brings a dead team back, which
    is how a DM undoes a mistake (or runs a resurrection). A new count is
    handled as add_team describes. The latest entry in the team's history is
    corrected to match rather than a second one added for the same floor, and is
    marked as the DM's. Nothing changes if any of the three is refused."""
    team = find_team(roster, name)
    if outcome is not None:
        _check_outcome(outcome)
    _check_crawlers(crawlers)
    if outcome is not None:
        team.outcome = outcome
        if outcome == "fatal":
            team.died_on = team.died_on or team.floor
        else:
            team.died_on = None
    if status is not None:
        team.status = status.strip()
    if crawlers is not None and crawlers != team.crawler_count:
        _resize_party(team, crawlers)
    fix = {"outcome": team.outcome, "status": team.status, "by_dm": True}
    if team.crawler_count:
        fix["crawlers"] = team.crawler_count
    if team.history and team.history[-1].get("floor") == team.floor:
        team.history[-1].update(fix)
    else:
        team.history.append({"floor": team.floor, "day": team.day, **fix})
    # A group killed, or cut down to fewer crawlers, may have taken someone off
    # the leaderboard with it.
    _settle_board(roster, team.floor, by_dm=True)
    return team


# -- the leaderboard ------------------------------------------------------------
#
# The Top Ten by ratings, from the first episode on Floor 4. Dealt once, on the
# first floor of a campaign deep enough to have one; after that it carries, and
# what changes it is the DM. A generated floor only adds one to everyone's
# multiplier, takes off whoever died with their group, and fills the empty places
# at the bottom with newcomers.

BOARD_SIZE = 10


def board(roster: Optional[CampaignRoster]) -> List[RankedCrawler]:
    """The Top Ten as it stands, rank 1 first."""
    if roster is None:
        return []
    return sorted((c for c in roster.leaderboard if c.rank), key=lambda c: c.rank)


def board_floor(roster: Optional[CampaignRoster]) -> Optional[int]:
    """The floor the leaderboard was last dealt on, which is the floor the DM's
    changes to it are recorded against. None before there is a leaderboard."""
    floors = [f for c in (roster.leaderboard if roster else []) for f in c.floors]
    return max(floors) if floors else None


def _log(c: RankedCrawler, floor: int, event: str, by_dm: bool = False) -> None:
    """Record a crawler coming onto the list or going off it, with the rank and
    multiplier they had at the time."""
    entry = {"floor": floor, "event": event, "rank": c.rank, "multiplier": c.multiplier}
    if by_dm:
        entry["by_dm"] = True
    c.history.append(entry)


def _on_floor(c: RankedCrawler, floor: int) -> None:
    """Count a floor towards the multiplier, once however often it is dealt."""
    if floor not in c.floors:
        c.floors.append(floor)
        c.floors.sort()


def _rerank(order: List[RankedCrawler], floor: int, by_dm: bool = False) -> None:
    """Hand out ranks 1-10 in this order; anyone past tenth falls off the list."""
    for i, c in enumerate(order):
        if i < BOARD_SIZE:
            c.rank = i + 1
        elif c.rank is not None:
            _log(c, floor, "fell off", by_dm)
            c.rank = None


def _taken_names(roster: CampaignRoster) -> set:
    # Group names too: a crawler called the same as a group is one lookup away
    # from editing the wrong thing.
    return ({c.name.lower() for c in roster.leaderboard}
            | {t.name.lower() for t in roster.teams})


def _crawler_name(rng, taken: set) -> str:
    """A name nobody in `taken` has, sharing no first name or surname with them
    either: two Wilhelminas on one Top Ten reads as the generator repeating
    itself, not as two people."""
    forms, weights = zip(*LEADERBOARD["name_forms"])
    # Only words from the name banks count: a handle such as "Fast Eddie Nwosu"
    # carries a surname, but two handles may both start with "The".
    bank = {w.lower() for w in CRAWLER_FIRST + CRAWLER_SURNAME}
    used = {w for n in taken for w in n.split() if w in bank}
    for _ in range(200):
        name = rng.choices(forms, weights=weights)[0].format(
            first=rng.choice(CRAWLER_FIRST), surname=rng.choice(CRAWLER_SURNAME),
            handle=rng.choice(CRAWLER_HANDLE))
        if name.lower() in taken or any(w in used for w in name.lower().split()):
            continue
        taken.add(name.lower())
        return name
    n = 2                                    # a bank long since spent; never in practice
    while f"{name} {n}".lower() in taken:
        n += 1
    taken.add(f"{name} {n}".lower())
    return f"{name} {n}"


def _stranger(rng, floor: int, taken: set) -> RankedCrawler:
    """A ranked crawler the party has never met: most of the Top Ten, in a
    dungeon this size, are people the party only ever sees on the feed."""
    klass = rng.choice(CRAWLER_CLASS)
    if floor >= CLASS_UPGRADE_FLOOR:
        klass = upgraded_class(klass)
    return RankedCrawler(name=_crawler_name(rng, taken), race=rng.choice(CRAWLER_RACE),
                         klass=klass)


def _group(roster: CampaignRoster, name: str) -> Optional[CrawlerTeam]:
    return next((t for t in roster.teams if t.name == name), None) if name else None


def _ranked_member(roster: CampaignRoster, c: RankedCrawler) -> Optional[dict]:
    """The crawler in their group that a ranked crawler is, or None if the group
    has lost them -- wiped out, or no longer counting anyone of their race. A race
    is permanent and no party holds two of one, so it is what identifies them."""
    team = _group(roster, c.team)
    if team is None or not team.alive:
        return None
    return next((m for m in team.members if m["race"] == c.race), None)


def _recruit(rng, roster: CampaignRoster, floor: int, taken: set,
             k: int) -> List[RankedCrawler]:
    """Up to k crawlers from groups reported on this floor, one per group, a group
    having a good floor likelier. Nobody who has been ranked before is picked
    again under a new name: coming back is the DM's call."""
    known = {(c.team, c.race) for c in roster.leaderboard if c.team}
    ranked_groups = {c.team for c in board(roster) if c.team}
    pool = []
    for t in roster.teams:
        if t.alive and t.floor == floor and t.name not in ranked_groups:
            free = [m for m in t.members if (t.name, m["race"]) not in known]
            if free:
                pool.append((t, free))
    weights = [LEADERBOARD["outcome_weight"].get(t.outcome, 1) for t, _ in pool]
    picked = []
    for i in _weighted_sample(rng, weights, k):
        team, free = pool[i]
        m = rng.choice(free)
        picked.append(RankedCrawler(name=_crawler_name(rng, taken), race=m["race"],
                                    klass=m["class"], team=team.name))
    return picked


def _settle_board(roster: Optional[CampaignRoster], floor: int,
                  by_dm: bool = False) -> List[RankedCrawler]:
    """Take off the list, dead, anyone whose group has lost them, and close up the
    ranks behind them. Returns who died."""
    if roster is None:
        return []
    lost = [c for c in board(roster) if c.team and _ranked_member(roster, c) is None]
    for c in lost:
        _log(c, floor, "died", by_dm)
        c.died_on, c.rank = floor, None
    if lost:
        _rerank(board(roster), floor, by_dm)
    return lost


def _fill(rng, roster: CampaignRoster, floor: int) -> None:
    """Fill the empty places at the bottom of the list with crawlers new to it."""
    ranked = board(roster)
    empty = BOARD_SIZE - len(ranked)
    if empty <= 0:
        return
    taken = _taken_names(roster)
    share = LEADERBOARD["from_roster"] / BOARD_SIZE
    new = _recruit(rng, roster, floor, taken, sum(rng.random() < share for _ in range(empty)))
    new += [_stranger(rng, floor, taken) for _ in range(empty - len(new))]
    for c in new:
        c.floors = [floor]
    roster.leaderboard.extend(new)
    _rerank(ranked + new, floor)
    for c in new:
        _log(c, floor, "entered")


def build_leaderboard(roster: CampaignRoster, seed: int, floor: int) -> None:
    """Deal the leaderboard, or carry it onto this floor.

    The first floor of a campaign from Floor 4 down deals a fresh Top Ten: a few
    crawlers from groups the party has met, the rest strangers. A list first dealt
    deeper than Floor 4 has been running since then, so its crawlers arrive with
    multipliers to match. On every later floor, each crawler still ranked has
    reached it, so their multiplier goes up by one -- and then anyone whose group
    the floor's report wiped out, or who is no longer in their group, dies with
    their bounty claimed. Empty places at the bottom go to newcomers. Ranks are
    otherwise left as they were: moving people up and down is the DM's."""
    if floor < LEADERBOARD["first_floor"]:
        return
    rng = random.Random(f"leaderboard|{seed}|{floor}")
    if roster.leaderboard:
        for c in board(roster):
            _on_floor(c, floor)
            member = _ranked_member(roster, c)
            if member:
                c.klass = member["class"]
            elif not c.team and floor >= CLASS_UPGRADE_FLOOR:
                c.klass = upgraded_class(c.klass)
        _settle_board(roster, floor)
        _fill(rng, roster, floor)
        return
    taken = _taken_names(roster)
    order = _recruit(rng, roster, floor, taken, LEADERBOARD["from_roster"])
    order += [_stranger(rng, floor, taken) for _ in range(BOARD_SIZE - len(order))]
    rng.shuffle(order)
    longest = floor - LEADERBOARD["first_floor"] + 1
    for c in order:
        c.floors = [floor]
        c.bonus = rng.randint(1, longest) - 1
    roster.leaderboard.extend(order)
    _rerank(order, floor)
    for c in order:
        _log(c, floor, "entered")


def floor_board(roster: CampaignRoster) -> List[RankedCrawler]:
    """The list as it stands, as copies: a result handed back for one floor should
    not change when the campaign moves on."""
    return [replace(c, floors=list(c.floors), history=[dict(e) for e in c.history])
            for c in board(roster)]


def board_news(roster: CampaignRoster, floor: int) -> List[Tuple[RankedCrawler, dict]]:
    """What changed on the list since the last floor's write-up: this floor's
    comings and goings, and whatever the DM changed after the last floor was
    generated. Nothing for the floor a list is first dealt on -- everyone on it
    is new."""
    floors = sorted({f for c in roster.leaderboard for f in c.floors})
    if not floors or floors[0] >= floor:
        return []
    before = max(f for f in floors if f < floor)
    order = {"died": 0, "fell off": 1, "revived": 2, "returned": 3, "entered": 4}
    news = [(c, e) for c in roster.leaderboard for e in c.history
            if e["floor"] == floor or (e.get("by_dm") and e["floor"] == before)]
    news.sort(key=lambda ce: (order.get(ce[1]["event"], 9), ce[1].get("rank") or 99))
    return news


def find_ranked(roster: CampaignRoster, name: str) -> RankedCrawler:
    """Anyone ever on the leaderboard, by name, matched as find_team matches."""
    if roster is None or not roster.leaderboard:
        raise MapgenError("nobody has been on the leaderboard yet")
    wanted = name.strip().lower()
    exact = [c for c in roster.leaderboard if c.name.lower() == wanted]
    matches = exact or [c for c in roster.leaderboard if wanted in c.name.lower()]
    if not matches:
        raise MapgenError(f"nobody called {name!r} has been on the leaderboard")
    if len(matches) > 1:
        raise MapgenError(f"{name!r} matches more than one crawler: "
                          + ", ".join(c.name for c in matches))
    return matches[0]


def _edit_floor(roster: CampaignRoster) -> int:
    floor = board_floor(roster)
    if floor is None:
        raise MapgenError(f"there is no leaderboard yet: it starts on Floor "
                          f"{LEADERBOARD['first_floor']}, so generate that floor or "
                          f"deeper in this campaign first")
    return floor


def _check_multiplier(multiplier) -> None:
    if not isinstance(multiplier, int) or isinstance(multiplier, bool) or multiplier < 1:
        raise MapgenError(f"a multiplier is a whole number, 1 or more (got {multiplier!r})")


def place_on_board(roster: CampaignRoster, name: str, rank: int, *, race: str = "",
                   klass: str = "", team: str = "",
                   multiplier: Optional[int] = None) -> RankedCrawler:
    """Put a crawler on the list at a rank.

    One already on it moves there. One who fell off comes back, keeping their
    multiplier, and this floor counts towards it. A name the leaderboard has
    never had is added -- the party's own crawlers included. Everyone from that
    rank down moves one place, and whoever is pushed past tenth falls off.

    A crawler from a group on the roster needs their race, and it has to be the
    race of someone in that group: that is how the generator tells, later, that
    the group has lost them. Their class is then taken from the group."""
    floor = _edit_floor(roster)
    if isinstance(rank, bool) or not isinstance(rank, int) or not 1 <= rank <= BOARD_SIZE:
        raise MapgenError(f"a rank is 1 to {BOARD_SIZE} (got {rank!r})")
    if multiplier is not None:
        _check_multiplier(multiplier)
    clean = name.strip()
    if not clean:
        raise MapgenError("a crawler needs a name")
    c = next((x for x in roster.leaderboard if x.name.lower() == clean.lower()), None)
    if c is not None and not c.alive:
        raise MapgenError(f"{c.name} died on floor {c.died_on}; bring them back first")
    event = None
    if c is None:
        if clean.lower() in {t.name.lower() for t in roster.teams}:
            raise MapgenError(f"{clean!r} is a group's name; a ranked crawler is one person")
        group = None
        if team.strip():
            group = next((t for t in roster.teams
                          if t.name.lower() == team.strip().lower()), None)
            if group is None:
                raise MapgenError(f"no group called {team!r} on the roster")
            if not group.alive:
                raise MapgenError(f"{group.name} died on floor {group.died_on}")
            member = next((m for m in group.members
                           if m["race"].lower() == race.strip().lower()), None)
            if member is None:
                there = ", ".join(f"{m['race']} {m['class']}" for m in group.members)
                raise MapgenError(f"{group.name} has nobody of the race {race!r}"
                                  + (f"; it has {there}" if there else
                                     "; it has no crawlers dealt yet"))
            race, klass = member["race"], member["class"]
        c = RankedCrawler(name=clean, race=race.strip(), klass=klass.strip(),
                          team=group.name if group else "", added=True)
        roster.leaderboard.append(c)
        event = "entered"
    elif c.rank is None:
        event = "returned"
    order = [x for x in board(roster) if x is not c]
    order.insert(rank - 1, c)
    _rerank(order, floor, by_dm=True)
    _on_floor(c, floor)
    if multiplier is not None:
        c.bonus = multiplier - len(c.floors)
    if event:
        _log(c, floor, event, by_dm=True)
    return c


def drop_from_board(roster: CampaignRoster, name: str, *,
                    died: bool = False) -> RankedCrawler:
    """Take a crawler off the list, and close up the ranks behind them.

    With died, they were killed -- the bounty is claimed -- and their group loses
    them too, dying with them if they were its last crawler."""
    floor = _edit_floor(roster)
    c = find_ranked(roster, name)
    if not c.alive:
        raise MapgenError(f"{c.name} already died, on floor {c.died_on}")
    if c.rank is None and not died:
        raise MapgenError(f"{c.name} is not on the list")
    _log(c, floor, "died" if died else "fell off", by_dm=True)
    c.rank = None
    if died:
        c.died_on = floor
        team, member = _group(roster, c.team), _ranked_member(roster, c)
        if member is not None:
            if len(team.members) > 1:
                team.members.remove(member)
                set_status(roster, team.name)
            else:
                set_status(roster, team.name, outcome="fatal")
    _rerank(board(roster), floor, by_dm=True)
    return c


def revive_ranked(roster: CampaignRoster, name: str) -> RankedCrawler:
    """Undo a death. They come back off the list -- put them back on with
    place_on_board -- and a living group that lost them has them again."""
    floor = _edit_floor(roster)
    c = find_ranked(roster, name)
    if c.alive:
        raise MapgenError(f"{c.name} is not dead")
    c.died_on = None
    _log(c, floor, "revived", by_dm=True)
    team = _group(roster, c.team)
    if team is not None and team.alive and _ranked_member(roster, c) is None:
        team.members.append({"race": c.race, "class": c.klass})
        set_status(roster, team.name)
    return c


def set_multiplier(roster: CampaignRoster, name: str, multiplier: int) -> RankedCrawler:
    """Set a crawler's multiplier. It still goes up by one for each later floor
    they spend on the list, and is kept if they are off it."""
    _check_multiplier(multiplier)
    c = find_ranked(roster, name)
    c.bonus = multiplier - len(c.floors)
    return c


def ranked_label(c: RankedCrawler) -> str:
    """Who a ranked crawler is, after their name: "a Gnome Necromancer, with
    Team Thistle"."""
    what = " ".join(x for x in (c.race, c.klass) if x)
    who = f"{_article(what)} {what}" if what else ""
    if c.team:
        who = f"{who}, with {c.team}" if who else f"with {c.team}"
    return who


_EVENT_WORDS = {"entered": "came onto the list", "fell off": "fell off",
                "returned": "came back", "died": "died", "revived": "was brought back"}


def ranked_story(c: RankedCrawler) -> str:
    """Their time on the list in a line: "came onto the list on floor 4 at rank 3;
    fell off on floor 6; came back on floor 7 at rank 9 (x3)"."""
    parts = []
    for e in c.history:
        bit = f"{_EVENT_WORDS.get(e['event'], e['event'])} on floor {e['floor']}"
        if e["event"] in ("entered", "returned") and e.get("rank"):
            bit += f" at rank {e['rank']}"
        if e["event"] in ("returned", "died") or (e["event"] == "entered"
                                                  and e.get("multiplier", 1) > 1):
            bit += f" (x{e.get('multiplier', 1)})"
        parts.append(bit)
    return "; ".join(parts)


def leaderboard_section(fl) -> List[str]:
    """The Top Ten for a floor from 4 down, with what changed since the last one."""
    ranked = getattr(fl, "leaderboard", None)
    if not ranked:
        return []
    o = ["### The Leaderboard", "",
         "The Top Ten by ratings, as this floor's first episode has them. A crawler's "
         "bounty is the figure for their rank times their multiplier, which goes up by "
         "one for every floor they spend on the list and is remembered if they drop "
         "off and come back.", "",
         "| Rank | Crawler | Multiplier | Bounty |", "| --- | --- | --- | --- |"]
    for c in ranked:
        who = ranked_label(c)
        o.append(f"| {c.rank} | **{c.name}**{' — ' + who if who else ''} | "
                 f"x{c.multiplier} | {c.bounty:,} gold |")
    o.append("")
    news = getattr(fl, "board_news", None)
    if news:
        o += ["Since the last floor:", ""]
        for c, e in news:
            if e["event"] == "died":
                gold = bounty_for(e["rank"], e["multiplier"]) if e.get("rank") else 0
                line = (f"**{c.name}** died" + (f" at rank {e['rank']}; a bounty of "
                                                f"{gold:,} gold was claimed" if gold else ""))
            elif e["event"] == "fell off":
                line = f"**{c.name}** fell off the list"
            elif e["event"] == "returned":
                line = (f"**{c.name}** is back, at rank {e['rank']}, keeping their "
                        f"multiplier (x{e['multiplier']})")
            elif e["event"] == "revived":
                line = f"**{c.name}** is alive after all"
            else:
                line = f"**{c.name}** is new to the list, at rank {e['rank']}"
            o.append(f"- {line}.")
        o.append("")
    o += [f"- {rule}" for rule in LEADERBOARD["rules"]]
    o.append("")
    return o


def _set_standing(roster: CampaignRoster, name: str, standing: str) -> str:
    team = set_standing(roster, name, standing)
    note = "" if standing in disposition_labels() else " (no reaction text for that one)"
    return f"{team.name} now stands {standing} with the party{note}"


def floor_teams(roster: CampaignRoster, floor: int) -> List[CrawlerTeam]:
    """The teams reported on one floor, in the order they should be read.

    Snapshots, not the roster's own objects: the roster goes on being dealt on
    later floors, and a result handed back for floor 1 should still describe floor
    1 after floor 2 has been generated from the same campaign."""
    teams = [t for t in roster.teams if t.floor == floor and t.history]
    teams.sort(key=lambda t: (t.day is None, t.day or 0, t.name))
    # The member dicts are copied too, not just the list: floor 6 upgrades classes
    # in place, and a floor 3 result should still show the floor 3 classes.
    return [replace(t, history=list(t.history),
                    members=[dict(m) for m in t.members]) for t in teams]


@dataclass
class GenResult:
    floor: object                   # the FloorN dataclass for the floor generated
    number: int                     # which floor was asked for
    size: str
    seed: int
    markdown: str
    svgs: List[Tuple[str, str]]     # (filename suffix, SVG source); "" is the main map
    roster: Optional["CampaignRoster"] = None   # the campaign, updated by this floor

    def basename(self) -> str:
        """The stem the CLI names saved files with."""
        return f"floor{self.number}_{self.size}_seed{self.seed}"


def generate(floor: int = 1, size: str = "medium", seed: Optional[int] = None, *,
             scaling: str = "distance", days: Optional[int] = None,
             boss_stairs: bool = True, stair_chance: float = 1.0,
             borough_boss_rate: float = 1.0, region: Optional[str] = None,
             warlords: str = "canon", wildcard: bool = False,
             winner: Optional[str] = None, runner_up: Optional[str] = None,
             vehicle: Optional[str] = None, party: int = 2,
             images: bool = True,
             roster: Optional[CampaignRoster] = None) -> GenResult:
    """Build one floor and render it. Raises MapgenError on a bad option combination.

    Pass images=False to skip SVG rendering when only the Markdown is wanted -- on the
    biggest floor 1-2 sizes that is hundreds of room maps and by far the slow part.

    Pass a roster to continue a campaign: teams still alive carry onto this floor with
    their name, origin and standing intact, and are dealt a fresh fate here. The roster
    that comes back on the result has been updated and is the one to save."""
    if floor not in FLOORS:
        raise MapgenError(f"floor must be one of {', '.join(map(str, FLOORS))} (got {floor})")
    if size not in SIZE_CFG:
        raise MapgenError(f"size must be one of {', '.join(SIZE_CFG)} (got {size!r})")
    if size in BIG_SIZES and floor not in (1, 2):
        raise MapgenError(f"size {size} is floor 1-2 only (got floor {floor})")
    if not 0.0 <= stair_chance <= 1.0:
        raise MapgenError("stair chance must be between 0 and 1")
    if not 0.0 <= borough_boss_rate <= 1.0:
        raise MapgenError("borough boss rate must be between 0 and 1")

    if seed is None:
        seed = random.randrange(1_000_000)

    if floor == 11:
        fl = Generator11(size, seed).build()
    elif floor == 10:
        fl = Generator10(size, seed, vehicle, party).build()
    elif floor == 9:
        fl = Generator9(size, seed, days, warlords, wildcard, winner, runner_up).build()
    elif floor == 8:
        fl = Generator8(size, seed, days, region).build()
    elif floor == 6:
        fl = Generator6(size, scaling, seed, days).build()
    elif floor == 5:
        fl = Generator5(size, seed, days).build()
    elif floor == 4:
        fl = Generator4(size, seed, days).build()
    elif floor == 3:
        fl = Generator3(size, scaling, seed, days).build()
    else:
        fl = Generator(floor, size, scaling, seed, days, boss_stairs,
                       stair_chance, borough_boss_rate).build()

    # Other crawlers are dealt on their own rng stream, after the floor is
    # built, so every floor can carry a campaign roster through one code path
    # and changing how teams are dealt cannot shift the floor's own rolls.
    roster = build_crawler_teams(fl, size, seed, floor, roster)
    # After the teams, which it reads: a ranked crawler whose group this floor
    # wiped out dies with it.
    build_leaderboard(roster, seed, floor)
    fl.other_crawlers = floor_teams(roster, floor)
    fl.leaderboard = floor_board(roster)
    fl.board_news = [(replace(c, history=[]), dict(e)) for c, e in board_news(roster, floor)]
    text = _render_floor(fl, floor)

    svgs = floor_svgs(fl, boss_stairs) if images else []
    return GenResult(floor=fl, number=floor, size=size, seed=seed, markdown=text,
                     svgs=svgs, roster=roster)


def regenerate_rooms(path: str, label: Optional[str] = None, floor: int = 1, *,
                     size: str = "medium", scaling: str = "distance",
                     seed: Optional[int] = None, days: Optional[int] = None,
                     boss_stairs: bool = True) -> Tuple[Neighborhood, GenResult]:
    """Re-roll just the room detail of one already-generated floor 1-2 neighborhood,
    parsed back out of a Markdown file this script wrote. Returns the neighborhood
    alongside a GenResult carrying its room text and map."""
    if floor not in (1, 2):
        raise MapgenError("room regeneration only supports floor 1 or 2 neighborhoods")
    if seed is None:
        seed = random.randrange(1_000_000)
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        raise MapgenError(f"could not read {path}: {e}")

    block = _extract_neighborhood_block(raw, label)
    nb = parse_neighborhood_block(block)
    gen = Generator(floor, size, scaling, seed, days, boss_stairs)
    nb.rooms, nb.corridor_encounters = gen.make_rooms(nb, MORDECAI_FORMS.get(str(floor), "a local guildmaster"))

    import types
    fake_fl = types.SimpleNamespace(number=floor, seed=seed)
    result = GenResult(floor=fake_fl, number=floor, size=size, seed=seed,
                       markdown=render_rooms_only(nb),
                       svgs=[(f"_room_{nb.label}", render_room_svg(nb, fake_fl))])
    return nb, result


def main(argv=None):
    # Some Windows consoles default to a legacy codepage (cp1252 etc.) that can't encode
    # the arrows/en-dashes used in the output; force UTF-8 on stdout/stderr so printing to
    # the console can't crash a run that would otherwise succeed fine when redirected to a file.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    p = argparse.ArgumentParser(description="Dungeon Crawler Carl floor generator (floors 1-2).")
    p.add_argument("--floor", type=int, choices=[1, 2, 3, 4, 5, 6, 8, 9, 10, 11], default=1)
    p.add_argument("--size", choices=list(SIZE_CFG), default="medium",
                   help="huge/giant/massive/continent are floors 1-2 only: multiple Cities "
                        "(County/State/Country/Continent), each its own large-sized City with "
                        "a City Boss, plus one top-tier boss over all of them")
    p.add_argument("--scaling", choices=["distance", "random"], default="distance",
                   help="distance: mob levels rise with distance from entry; random: independent per zone")
    p.add_argument("--seed", type=int, default=None, help="RNG seed for reproducible maps")
    p.add_argument("--days", type=int, default=None, help="override the random countdown length")
    p.add_argument("--no-boss-stairs", action="store_true",
                   help="omit stairwells from borough/city boss chambers (neighborhood chambers always have one)")
    p.add_argument("--stair-chance", type=float, default=1.0, metavar="0-1",
                   help="floors 1-2: probability each neighborhood boss chamber gets a stairwell "
                        "(default 1.0 = always, matching the books' 'every boss chamber' rule)")
    p.add_argument("--borough-boss-rate", type=float, default=1.0, metavar="0-1",
                   help="floors 1-2: probability each borough (2x2 block of neighborhoods) gets a "
                        "borough boss at all (default 1.0 = every borough; e.g. 0.5 for roughly "
                        "every other one)")
    p.add_argument("--region", type=str, default=None,
                   help="floor 8 only: folklore archetype (e.g. 'East Asia', 'Caribbean'); random if omitted")
    p.add_argument("--warlords", choices=["canon", "generated"], default="canon",
                   help="floor 9: use canon warlord names where recorded, or generate all of them")
    p.add_argument("--wildcard", action="store_true", help="floor 9: replace the crawler team with a generated wildcard faction")
    p.add_argument("--winner", type=str, default=None, help="floor 9: projected winner (default: the crawler team)")
    p.add_argument("--runner-up", type=str, default=None, help="floor 9: projected runner-up")
    p.add_argument("--vehicle", choices=["mechanical", "biological"], default=None, help="floor 10: vehicle type (random if omitted)")
    p.add_argument("--party", type=int, default=2, help="floor 10: party size 2–4 (drives the driver rotation)")
    p.add_argument("--out", type=str, default=None, help="write Markdown to this file instead of stdout")
    p.add_argument("--image", type=str, default=None,
                   help="also write a map image to this path (.svg needs nothing; .png needs resvg-py)")
    p.add_argument("--save", nargs="?", const=".", default=None, metavar="DIR",
                   help="write both floorN_size_seedS.md and .png (falls back to .svg without resvg-py) "
                        "into DIR (default: current directory)")
    p.add_argument("--campaign", type=str, default=None, metavar="FILE",
                   help="carry other crawler teams between floors: teams still alive in FILE "
                        "come onto this floor with their name and standing intact and are dealt "
                        "a fresh fate, then FILE is rewritten. Created if it does not exist, so "
                        "the same flag starts a campaign and continues one")
    p.add_argument("--standing", action="append", default=None, metavar='"NAME=STANDING"',
                   help="set where a team stands with the party before generating, e.g. "
                        '--standing "Team Thistle=allied" (repeatable). Needs --campaign. '
                        "Any wording is accepted; " + "/".join(disposition_labels()) +
                        " come with reaction text")
    p.add_argument("--rooms-for", type=str, default=None, metavar="FILE",
                   help="skip normal generation; parse one '### X. Name' neighborhood block from FILE "
                        "(as produced by this script, floor 1/2 only) and (re)generate just its room "
                        "detail -- useful for adding rooms to a floor you already generated and are playing")
    p.add_argument("--rooms-for-label", type=str, default=None, metavar="LABEL",
                   help="--rooms-for: which neighborhood letter to use if FILE has more than one (default: the first found)")
    p.add_argument("--open-box", action="append", default=None, metavar='"TIER TYPE"',
                   help='skip floor generation and open loot boxes instead, e.g. "Silver Boss" '
                        'or "a Gold Weapon Box" -- a Drops line pasted straight off a write-up '
                        'works. Repeatable. Unknown types are achievement boxes and open from '
                        'the Adventurer pools. --floor sets the gold in them (none on '
                        'Floor 1); --seed makes the rolls repeatable')
    p.add_argument("--count", type=int, default=1, metavar="N",
                   help="--open-box: how many of each box to open (default 1). Boxes opened "
                        "together work through each bank before repeating an item")
    p.add_argument("--for", dest="threat", default=None, metavar="THREAT",
                   help="--open-box: prepare Benefactor boxes for what the sponsor can see "
                        "coming -- fire, cold, poison, undead, water, darkness, falls, traps, "
                        "swarms, giants, flyers or casters. Other box types ignore it")
    p.add_argument("--liked", type=float, default=None, metavar="PERCENT",
                   help="--open-box: how much the audience liked whatever earned a Fan box, "
                        "0-100 (default 50). Sets the odds of a good, neutral or bad box. "
                        "Other box types ignore it")
    a = p.parse_args(argv)

    try:
        _run(a)
    except MapgenError as e:
        sys.exit(str(e))


def _open_boxes(a) -> None:
    """--open-box: roll boxes with no floor at all. One roller for the whole run,
    so asking for ten Gold Weapon boxes gets ten different weapons."""
    if a.count < 1:
        raise MapgenError("--count must be at least 1")
    if a.threat:
        find_threat(a.threat)                  # refuse a typo before rolling anything
    if a.liked is not None:
        fan_odds(a.liked)
    roller = LootRoller(a.seed)
    lines = [roller.open_text(spec, a.floor, threat=a.threat, liked=a.liked).markdown()
             for spec in a.open_box for _ in range(a.count)]
    text = "\n".join(lines)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"wrote {a.out}", file=sys.stderr)
    else:
        print(text)
    gold_from = LOOT_BOXES["gold"]["from_floor"]
    floor_note = (f"Floor {a.floor}" if a.floor >= gold_from else
                  f"Floor {a.floor}, so no gold -- add --floor N for a box earned deeper")
    print(f"opened {len(lines)} box(es) for {floor_note}; seed {roller.seed}, and "
          f"--seed {roller.seed} opens the same ones again", file=sys.stderr)


def _run(a) -> None:
    """The CLI body, split out so every MapgenError lands in one place."""
    import os

    if a.open_box:
        _open_boxes(a)
        return

    if a.rooms_for:
        seed = a.seed if a.seed is not None else random.randrange(1_000_000)
        nb, res = regenerate_rooms(a.rooms_for, a.rooms_for_label, a.floor, size=a.size,
                                   scaling=a.scaling, seed=seed, days=a.days,
                                   boss_stairs=not a.no_boss_stairs)
        print(f"parsed {nb.label}. {nb.name} -- mob {nb.mob.name} L{nb.level_range[0]}-{nb.level_range[1]}, "
              f"boss {nb.boss.name} L{nb.boss.level}, safe_rooms={nb.safe_rooms}, "
              f"hidden_stairwells={len(nb.hidden_stairs)}, tutorial_guild={nb.tutorial_guild}", file=sys.stderr)

        if a.out:
            with open(a.out, "w", encoding="utf-8") as f:
                f.write(res.markdown + "\n")
            print(f"wrote {a.out} (seed {res.seed})", file=sys.stderr)
        else:
            print(res.markdown)

        if a.image:
            _write_svg(res.svgs[0][1], a.image)
            print(f"wrote {a.image}", file=sys.stderr)
        return

    if a.standing and not a.campaign:
        raise MapgenError("--standing needs --campaign: there is nowhere to record a "
                          "standing without a campaign roster")
    roster = load_roster(a.campaign) if a.campaign else None
    for change in a.standing or []:
        name, _, standing = change.partition("=")
        if not standing:
            raise MapgenError(f'--standing wants "NAME=STANDING", got {change!r}')
        print(_set_standing(roster, name.strip(), standing.strip()), file=sys.stderr)

    res = generate(a.floor, a.size, a.seed, scaling=a.scaling, days=a.days,
                   boss_stairs=not a.no_boss_stairs, stair_chance=a.stair_chance,
                   borough_boss_rate=a.borough_boss_rate, region=a.region,
                   warlords=a.warlords, wildcard=a.wildcard, winner=a.winner,
                   runner_up=a.runner_up, vehicle=a.vehicle, party=a.party,
                   images=bool(a.image or a.save is not None), roster=roster)

    if a.campaign:
        save_roster(res.roster, a.campaign)
        alive = len(res.roster.active())
        carried = sum(1 for t in res.floor.other_crawlers if t.first_seen != t.floor)
        print(f"campaign {a.campaign}: {carried} team(s) carried in, "
              f"{alive} alive of {len(res.roster.teams)} met so far", file=sys.stderr)

    out, image = a.out, a.image
    if a.save is not None:
        os.makedirs(a.save, exist_ok=True)
        base = os.path.join(a.save, res.basename())
        try:
            import resvg_py  # type: ignore  # noqa: F401
            ext = ".png"
        except ImportError:
            ext = ".svg"
        out = out or base + ".md"
        image = image or base + ext

    if image:
        for pth in save_svgs(res.svgs, image):
            print(f"wrote {pth}", file=sys.stderr)

    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(res.markdown)
        print(f"wrote {out} (seed {res.seed})", file=sys.stderr)
    else:
        print(res.markdown)

def interactive_mode() -> None:
    """Friendly prompt-driven flow for the standalone .exe: double-clicking it from
    Explorer gives no chance to pass CLI flags and the console closes the instant the
    script ends, so ask a few quick questions, always save files, and pause at the end."""
    print("=" * 60)
    print("  DCC Mapgen -- Dungeon Crawler Carl floor generator")
    print("=" * 60)
    print("No command-line options were given, so here's the quick version.")
    print("Just press Enter to accept the default shown in [brackets].\n")

    floor_choices = [1, 2, 3, 4, 5, 6, 8, 9, 10, 11]
    raw = input("Floor number (1,2,3,4,5,6,8,9,10,11) [1]: ").strip()
    try:
        floor = int(raw) if raw else 1
    except ValueError:
        floor = 1
    if floor not in floor_choices:
        print(f"'{raw}' isn't a valid floor -- using 1 instead.")
        floor = 1

    size_prompt = ("Size (small/medium/large/huge/giant/massive/continent) [medium]: " if floor in (1, 2)
                   else "Size (small/medium/large) [medium]: ")
    raw = input(size_prompt).strip().lower()
    if floor not in (1, 2) and raw in BIG_SIZES:
        print(f"'{raw}' is floor 1-2 only -- using medium instead.")
        raw = "medium"
    size = raw if raw in SIZE_CFG else "medium"

    seed = input("Seed number (blank = random) []: ").strip()

    out_dir = input("Folder to save into [current folder]: ").strip() or "."

    argv = ["--floor", str(floor), "--size", size, "--save", out_dir]
    if seed:
        argv += ["--seed", seed]

    print("\nGenerating...\n")
    try:
        main(argv)
    except SystemExit as e:
        if e.code not in (0, None):
            print(f"\nSomething went wrong: {e}")
    except Exception as e:
        print(f"\nUnexpected error: {e}")
    input("\nDone. Press Enter to close this window...")

if __name__ == "__main__":
    if len(sys.argv) == 1 and getattr(sys, "frozen", False):
        interactive_mode()
    else:
        main()
