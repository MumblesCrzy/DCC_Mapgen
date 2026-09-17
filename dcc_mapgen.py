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
    small   1 neighborhood, 1 neighborhood boss,  +1 hidden stairwell
    medium  1 borough  (2x2 neighborhoods), 1 borough boss, 4 nbhd bosses, +2 hidden
    large   1 city     (4x4 neighborhoods, 4 boroughs), 1 city boss, 4 borough
            bosses, 16 nbhd bosses, +4 hidden

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

Every neighborhood boss chamber contains a stairwell. Borough and city boss
chambers also contain one (canon) unless --no-boss-stairs is passed.
"""

import argparse
import random
import re
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Floor tuning
# ---------------------------------------------------------------------------

FLOOR_CFG = {
    1: {
        "mob_band": (1, 5),          # standard mob levels
        "days": (4, 6),              # random day count range
        "boss_offset": {"neighborhood": 2, "borough": 4, "city": 6},
        "janitor_level": (1, 3),
        "safe_rooms": (1, 3),
        "guild_chance": 0.45,
        "bosses_roam": False,
        "pack_bonus": 0,
    },
    2: {
        "mob_band": (2, 8),
        "days": (5, 7),
        "boss_offset": {"neighborhood": 3, "borough": 5, "city": 8},
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
    "large":  {"grid": 4, "hidden": 4},
}

# Extra filler rooms added per neighborhood on top of its mandatory rooms (entrance,
# boss chamber, safe rooms, guild hall, hidden passages) — never eats into that budget.
ROOM_FILLER_COUNT_BY_SIZE = {
    "small":  (2, 3),
    "medium": (4, 5),
    "large":  (5, 6),
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
    name: str
    origin: str
    status: str

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
    borough_bosses: dict           # borough name -> Boss
    city_boss: Optional[Boss]
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
                 days: Optional[int], boss_stairs: bool):
        self.rng = random.Random(seed)
        self.seed = seed
        self.floor = floor
        self.size = size
        self.scaling = scaling
        self.cfg = FLOOR_CFG[floor]
        self.grid = SIZE_CFG[size]["grid"]
        self.hidden = SIZE_CFG[size]["hidden"]
        self.days_override = days
        self.boss_stairs = boss_stairs
        self.used_mobs = set()
        self.used_names = set()
        self.used_firsts = set()
        self.used_titles = set()
        self.used_forms = set()

    # -- pieces --------------------------------------------------------------

    def make_mob(self) -> Mob:
        for _ in range(100):
            adj = self.rng.choice(MOB_ADJ)
            noun = self.rng.choice(MOB_NOUN)
            if (adj, noun) not in self.used_mobs:
                break
        self.used_mobs.add((adj, noun))
        quirk, mod = self.rng.choice(MOB_QUIRKS)
        tmpl = self.rng.choice(SYSTEM_DESC)
        desc = tmpl.format(
            a=article(adj), b=f"{adj.lower()} {singular(noun).lower()}",
            noun=noun, noun_l=noun.lower(), adj_l=adj.lower(),
            noun_sing=singular(noun).lower(),
        )
        return Mob(adj, noun, quirk, mod, desc)

    def make_boss(self, tier: str, mob: Mob, base_level: int) -> Boss:
        # Retry until the name is new AND doesn't just recombine a first name or
        # title already claimed by another boss on this floor (word-bank collision guard).
        name = first = title = None
        uses_first = False
        for _ in range(200):
            cand_first = self.rng.choice(BOSS_FIRST)
            cand_title = self.rng.choice(BOSS_TITLE)
            cand_uses_first = self.rng.random() < 0.5
            if cand_uses_first:
                cand_name = f"{cand_first} {cand_title}"
            else:
                cand_name = f"The {self.rng.choice(NBHD_ADJ)} {singular(mob.noun)} {cand_title}"
            if cand_name in self.used_names:
                continue
            if cand_uses_first and cand_first in self.used_firsts:
                continue
            if cand_title in self.used_titles:
                continue
            name, first, title, uses_first = cand_name, cand_first, cand_title, cand_uses_first
            break
        if name is None:
            # Word banks exhausted for this floor size; accept the last candidate.
            name, first, title, uses_first = cand_name, cand_first, cand_title, cand_uses_first
        self.used_names.add(name)
        if uses_first:
            self.used_firsts.add(first)
        self.used_titles.add(title)
        size = {"neighborhood": (8, 15), "borough": (15, 30), "city": (30, 60)}[tier]
        for _ in range(200):
            sz = self.rng.randint(*size)
            form = self.rng.choice(BOSS_FORMS).format(
                size=sz, a_size=("an" if sz in (8, 11, 18) or 80 <= sz < 90 else "a"),
                noun_l=mob.noun.lower(), noun_sing=singular(mob.noun).lower())
            if form not in self.used_forms:
                break
        self.used_forms.add(form)
        level = base_level + self.cfg["boss_offset"][tier]
        mechanics = self.rng.sample(BOSS_MECHANICS, 2 if tier != "neighborhood" else 1)
        stair = True if tier == "neighborhood" else self.boss_stairs
        return Boss(name, tier, level, form, " ".join(mechanics),
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
        corridor_encounters = [(a, b, self.rng.choice(RANDOM_ENCOUNTERS))
                                for a, b in self.rng.sample(list(edges), encounter_ct)]

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

        nbhds: List[Neighborhood] = []
        for i, (r, c) in enumerate(cells):
            dist = abs(r - entry[0]) + abs(c - entry[1])
            borough = None
            if n == 4:
                borough = ("N" if r < 2 else "S") + ("W" if c < 2 else "E")
            elif n == 2:
                borough = "Borough"
            mob = self.make_mob()
            lr = self.level_range(dist, max_dist)
            boss = self.make_boss("neighborhood", mob, lr[1])
            tier_idx = min(len(LOOT_BOX_TIERS) - 1, dist + self.rng.randint(0, 1))
            nbhds.append(Neighborhood(
                idx=i, label=chr(ord("A") + i), name=self.nbhd_name(),
                row=r, col=c, borough=borough, mob=mob, level_range=lr,
                boss=boss, loot=self.rng.choice(LOOT_THEMES),
                safe_rooms=self.rng.randint(*self.cfg["safe_rooms"]),
                tutorial_guild=self.rng.random() < self.cfg["guild_chance"],
                feature=self.rng.choice(NOTABLE_FEATURES),
                distance=dist, is_entry=(r, c) == entry,
                loot_tier=LOOT_BOX_TIERS[tier_idx],
            ))

        # Hidden stairwells, spread across distinct neighborhoods when possible.
        pool = nbhds[:]
        self.rng.shuffle(pool)
        spots = self.rng.sample(HIDDEN_STAIR_SPOTS, self.hidden)
        for k in range(self.hidden):
            pool[k % len(pool)].hidden_stairs.append(spots[k])

        # Borough bosses.
        borough_bosses = {}
        if n >= 2:
            names = sorted({nb.borough for nb in nbhds})
            for bname in names:
                members = [nb for nb in nbhds if nb.borough == bname]
                top = max(nb.level_range[1] for nb in members)
                mob = self.rng.choice(members).mob
                borough_bosses[bname] = self.make_boss("borough", mob, top)

        # City boss.
        city_boss = None
        if n == 4:
            top = max(nb.level_range[1] for nb in nbhds)
            city_boss = self.make_boss("city", self.rng.choice(nbhds).mob, top)

        janitor = self.make_mob()
        janitor.quirk = self.rng.choice(JANITOR_MECHANICS)
        janitor.description = "Janitor mob. " + janitor.description

        for nb in nbhds:
            nb.rooms, nb.corridor_encounters = self.make_rooms(nb, guildmaster_form)

        days = self.days_override or self.rng.randint(*self.cfg["days"])
        title = {"small": "Neighborhood", "medium": "Borough", "large": "City"}[self.size]
        floor_name = f"The {self.rng.choice(NBHD_ADJ)} {title}"

        elite_host = self.rng.choice(nbhds)
        elite = Elite(
            name=f"{self.rng.choice(BOSS_FIRST)} {self.rng.choice(ELITE_TITLE)}",
            show=self.rng.choice(ELITE_SHOW).format(
                noun=singular(elite_host.mob.noun), adj=self.rng.choice(NBHD_ADJ)),
            form=self.rng.choice(ELITE_FORM).format(race=singular(elite_host.mob.noun).lower()),
            plot_armor=self.rng.choice(PLOT_ARMOR),
            settlement=elite_host.name, region=floor_name)

        team_count = {"small": 1, "medium": 2, "large": 3}[self.size]
        other_crawlers = []
        for _ in range(team_count):
            label = self.rng.choice(nbhds).label
            other_crawlers.append(CrawlerTeam(
                name=f"Team {self.rng.choice(TEAM_SURNAME)}",
                origin=self.rng.choice(TEAM_ORIGIN),
                status=self.rng.choice(TEAM_STATUS).format(label=label)))

        return Floor(
            number=self.floor, size=self.size, name=floor_name, days=days,
            janitor=janitor, janitor_level=self.rng.randint(*self.cfg["janitor_level"]),
            storyline=self.rng.choice(STORYLINES), grid=n, neighborhoods=nbhds,
            borough_bosses=borough_bosses, city_boss=city_boss,
            scaling=self.scaling, seed=self.seed,
            elite=elite, other_crawlers=other_crawlers,
            collapse_note=self.rng.choice(COLLAPSE_NOTES),
            guildmaster_form=guildmaster_form,
            guildmaster_blurb=self.rng.choice(TUTORIAL_GUILD_BLURB),
        )

# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

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

def boss_block(b: Boss, mob_name: Optional[str] = None) -> List[str]:
    out = [
        f"- **{b.tier.title()} Boss:** {b.name} (Level {b.level})",
        f"  - Form: {b.form}.",
        f"  - Chamber: {b.chamber}.",
        f"  - Mechanic: {b.mechanic}",
        f"  - Stairwell in chamber: {'yes' if b.has_stairwell else 'no'}",
    ]
    return out

def render(fl: Floor, boss_stairs: bool) -> str:
    cfg = FLOOR_CFG[fl.number]
    n_nb = len(fl.neighborhoods)
    n_bor = len(fl.borough_bosses)
    n_city = 1 if fl.city_boss else 0
    boss_stairs_ct = n_nb + (n_bor + n_city if boss_stairs else 0)
    hidden_ct = sum(len(nb.hidden_stairs) for nb in fl.neighborhoods)
    entry = next(nb for nb in fl.neighborhoods if nb.is_entry)

    o = []
    o.append(f"# Floor {fl.number} — {fl.name} ({fl.size})")
    o.append("")
    o.append("## Summary")
    o.append("")
    o.append(f"- **Countdown:** {fl.days} days")
    o.append(f"- **Zones:** {n_nb} neighborhood{'s' if n_nb != 1 else ''}"
             + (f", {n_bor} boroughs" if n_bor > 1 else (", 1 borough" if n_bor == 1 else ""))
             + (", 1 city" if n_city else ""))
    o.append(f"- **Bosses:** {n_nb} neighborhood, {n_bor} borough, {n_city} city")
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
    if fl.grid == 4:
        o.append("Boroughs are the four 2x2 quadrants (NW, NE, SW, SE). Each borough boss "
                 "chamber sits at the center of its quadrant, reachable from all four of "
                 "its neighborhoods. The city boss chamber sits at the seam where the four "
                 "boroughs meet.")
    elif fl.grid == 2:
        o.append("The four neighborhoods form one borough. The borough boss chamber sits at "
                 "the center, reachable from all four neighborhoods.")
    else:
        o.append("A single neighborhood. The boss chamber is at the far end from the entry.")
    o.append("")

    if fl.city_boss:
        o.append("## City Boss")
        o.append("")
        o.extend(boss_block(fl.city_boss))
        o.append("")

    if fl.borough_bosses:
        o.append("## Borough Bosses")
        o.append("")
        for bname, b in fl.borough_bosses.items():
            members = [nb.label for nb in fl.neighborhoods if nb.borough == bname]
            head = f"Borough {bname}" if fl.grid == 4 else "The Borough"
            o.append(f"### {head} (neighborhoods {', '.join(members)})")
            o.append("")
            o.extend(boss_block(b))
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
        if nb.borough and fl.grid == 4:
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
        o.extend(boss_block(nb.boss))
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

    o.append("## Other Crawlers")
    o.append("")
    o.append("Other survivors sharing this floor, per the death feed and rumor:")
    o.append("")
    for ct in fl.other_crawlers:
        o.append(f"- **{ct.name}** — formed from {ct.origin}. Status: {ct.status}.")
    o.append("")

    o.append("## Loot Boxes")
    o.append("")
    o.append("Box tiers, lowest to highest: " + " → ".join(LOOT_BOX_TIERS) + 
             ". Neighborhoods farther from the entry skew toward higher tiers.")
    o.append("")

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
        sys.exit("PNG output needs resvg-py (pip install resvg-py); or use a .svg path.")
    png_bytes = bytes(resvg_py.svg_to_bytes(svg_string=svg))
    with open(path, "wb") as f:
        f.write(png_bytes)

CELL = 230
PAD = 40
HEADER = 70
LEGEND = 90

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

def _boss_marker(cx, cy, tier, label):
    r = {"neighborhood": 13, "borough": 22, "city": 30}[tier]
    color = {"neighborhood": "#cd7f32", "borough": "#a8a9ad", "city": "#d4af37"}[tier]
    out = [f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{color}" stroke="#222" stroke-width="2"/>']
    glyph = {"neighborhood": "N", "borough": "B", "city": "C"}[tier]
    out.append(_text(cx, cy + 5, glyph, size=r, weight="bold", anchor="middle"))
    return out

def render_svg(fl: Floor, boss_stairs: bool) -> str:
    n = fl.grid
    W = max(PAD * 2 + CELL * n, 800)
    key_lines = len(fl.borough_bosses) + (1 if fl.city_boss else 0)
    H = HEADER + PAD + CELL * n + LEGEND + 18 * key_lines + PAD
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
        o.append(_stair_icon(bx - 32, by - 7))
        # hidden stairs, bottom-left
        for k, _ in enumerate(nb.hidden_stairs):
            o.append(_stair_icon(x + 12 + k * 22, y + CELL - 24, hidden=True))
        # entry
        if nb.is_entry:
            o.append(f'<rect x="{x+3}" y="{y+3}" width="{CELL-6}" height="{CELL-6}" fill="none" '
                     f'stroke="#c0392b" stroke-width="4"/>')
            o.append(_text(x + CELL // 2, y + CELL - 12, "ENTRY", size=14, weight="bold",
                           anchor="middle", fill="#c0392b"))

    # Borough outlines + bosses
    if n == 4:
        for bname, b in fl.borough_bosses.items():
            r0 = 0 if bname[0] == "N" else 2
            c0 = 0 if bname[1] == "W" else 2
            x, y = ox + c0 * CELL, oy + r0 * CELL
            o.append(f'<rect x="{x}" y="{y}" width="{CELL*2}" height="{CELL*2}" fill="none" '
                     f'stroke="#222" stroke-width="4"/>')
            o.extend(_boss_marker(x + CELL, y + CELL, "borough", ""))
            if boss_stairs and b.has_stairwell:
                o.append(_stair_icon(x + CELL + 26, y + CELL - 28))
    elif n == 2:
        b = next(iter(fl.borough_bosses.values()))
        o.extend(_boss_marker(ox + CELL, oy + CELL, "borough", ""))
        if boss_stairs and b.has_stairwell:
            o.append(_stair_icon(ox + CELL + 26, oy + CELL - 28))
    if fl.city_boss:
        cx, cy = ox + CELL * 2, oy + CELL * 2
        o.extend(_boss_marker(cx, cy, "city", ""))
        if boss_stairs and fl.city_boss.has_stairwell:
            o.append(_stair_icon(cx + 34, cy - 36))

    # Legend
    ly = oy + CELL * n + 30
    lx = PAD
    o.extend(_boss_marker(lx + 13, ly, "neighborhood", ""))
    o.append(_text(lx + 32, ly + 4, "Neighborhood boss", size=11))
    o.extend(_boss_marker(lx + 170, ly, "borough", ""))
    o.append(_text(lx + 198, ly + 4, "Borough boss", size=11))
    o.extend(_boss_marker(lx + 320, ly, "city", ""))
    o.append(_text(lx + 356, ly + 4, "City boss", size=11))
    o.append(_stair_icon(lx + 440, ly - 8))
    o.append(_text(lx + 462, ly + 4, "Stairwell", size=11))
    o.append(_stair_icon(lx + 540, ly - 8, hidden=True))
    o.append(_text(lx + 562, ly + 4, "Hidden stairwell", size=11))
    o.append(f'<rect x="{lx+670}" y="{ly-9}" width="18" height="18" fill="none" stroke="#c0392b" stroke-width="3"/>')
    o.append(_text(lx + 694, ly + 4, "Entry", size=11))

    # Boss key
    ky = ly + 52
    if fl.city_boss:
        o.append(_text(lx, ky, f"City boss: {fl.city_boss.name} (L{fl.city_boss.level})",
                       size=12, weight="bold"))
        ky += 18
    for bname, b in fl.borough_bosses.items():
        tag = f"Borough {bname}" if n == 4 else "Borough"
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

def write_image(fl, path: str, boss_stairs: bool) -> None:
    if isinstance(fl, Floor11):
        svg = render11_svg(fl)
    elif isinstance(fl, Floor9):
        svg = render9_svg(fl)
    elif isinstance(fl, Floor8):
        svg = render8_svg(fl)
    elif isinstance(fl, Floor6):
        svg = render6_svg(fl)
    elif isinstance(fl, Floor5):
        svg = render5_svg(fl)
    elif isinstance(fl, Floor4):
        svg = render4_svg(fl)
    elif isinstance(fl, Floor3):
        svg = render3_svg(fl)
    else:
        svg = render_svg(fl, boss_stairs)
    if path.lower().endswith(".png"):
        _svg_to_png(svg, path)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)

def write_images(fl: Floor, path: str, boss_stairs: bool) -> List[str]:
    """Main map at `path`, plus one section map per neighborhood (its room graph)."""
    import os
    write_image(fl, path, boss_stairs)
    out = [path]
    root, ext = os.path.splitext(path)
    for nb in fl.neighborhoods:
        p = f"{root}_room_{nb.label}{ext}"
        svg = render_room_svg(nb, fl)
        if ext.lower() == ".png":
            _svg_to_png(svg, p)
        else:
            with open(p, "w", encoding="utf-8") as f:
                f.write(svg)
        out.append(p)
    return out

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

class Generator3:
    def __init__(self, size, scaling, seed, days):
        self.rng = random.Random(seed)
        self.seed = seed
        self.size = size
        self.scaling = scaling
        self.cfg = FLOOR3_CFG
        self.sc = SIZE3_CFG[size]
        self.days_override = days
        self.used = set()

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
        adj, noun = self._unique(lambda: (self.rng.choice(RUIN_MOB_ADJ), self.rng.choice(RUIN_MOB_NOUN)))
        quirk, mod = self.rng.choice(MOB_QUIRKS)
        desc = self.rng.choice(SYSTEM_DESC).format(
            a=article(adj), b=f"{adj.lower()} {singular(noun).lower()}",
            noun=noun, noun_l=noun.lower(), adj_l=adj.lower(), noun_sing=singular(noun).lower())
        return Mob(adj, noun, quirk, mod, desc)

    def make_boss(self, tier: str, mob: Mob, base: int) -> Boss:
        name = self._unique(lambda: (f"{self.rng.choice(BOSS_FIRST)} {self.rng.choice(BOSS_TITLE)}"
                                     if self.rng.random() < 0.5 else
                                     f"The {self.rng.choice(RUIN_ADJ)} {singular(mob.noun)} {self.rng.choice(BOSS_TITLE)}"))
        sizes = {"neighborhood": (10, 18), "borough": (18, 35), "city": (35, 70)}[tier]
        sz = self.rng.randint(*sizes)
        form = self.rng.choice(BOSS_FORMS).format(
            size=sz, a_size=("an" if sz in (8, 11, 18) else "a"),
            noun_l=mob.noun.lower(), noun_sing=singular(mob.noun).lower())
        level = self.cfg["city_boss_level"] if tier == "city" else base + self.cfg["boss_offset"][tier]
        mech = self.rng.sample(BOSS_MECHANICS, 1 if tier == "neighborhood" else 2)
        chamber = "roams the ruins; no fixed chamber"
        return Boss(name, tier, level, form, " ".join(mech), chamber, False)

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
        role = self.rng.choice(QUEST_GIVER_ROLE)
        giver = f"{self.rng.choice(BOSS_FIRST)} the {role}"
        others = [n for n in settlement_names if n != s_name] or [s_name]
        obj = self.rng.choice(QUEST_OBJECTIVE).format(
            item=self.rng.choice(QUEST_ITEM), region=self.rng.choice(region_names),
            mob=self.rng.choice(mob_names), settlement=self.rng.choice(others),
            giver=giver.split()[0], relative=self.rng.choice(QUEST_RELATIVE),
            role=self.rng.choice(QUEST_GIVER_ROLE))
        title = self.rng.choice(["The Matter of the {x}", "A Favor for the {x}", "What the {x} Wants",
                                 "{x}'s Errand", "Trouble in {s}"]).format(
            x=role.split(" (")[0].split(" who")[0].title(), s=s_name)
        reward = self.rng.choice(QUEST_REWARD)
        if unlocks:
            reward += f"; unlocks stairwell {unlocks}"
        return Quest(title, giver, obj, self.rng.choice(QUEST_FLAVOR), reward, unlocks)

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
                grid[(r, c)] = Region(
                    idx=i, label=label, name=self.region_name(), row=r, col=c,
                    mob=self.make_mob(), night=self.rng.choice(NIGHT_THREATS),
                    level_range=self.level_range(dist, max_dist),
                    loot=self.rng.choice(LOOT_THEMES), feature=self.rng.choice(RUIN_FEATURES),
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
        qcount = {"tiny": 1, "small": 1, "medium": 2, "large": 2, "xl": 3}
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
        if reg.boss:
            b = reg.boss
            o.append(f"- **{b.tier.title()} Boss:** {b.name} (Level {b.level})")
            o.append(f"  - Form: {b.form}.")
            o.append(f"  - Roams between here and {reg.roams_to}." if reg.roams_to else "  - Stays in this region.")
            o.append(f"  - Mechanic: {b.mechanic}")
        if reg.stairwell:
            o.append(f"- **Stairwell {reg.stairwell.label}:** {reg.stairwell.condition}.")
        o.append(f"- **Loot theme:** {reg.loot}")
        o.append(f"- **Notable feature:** {reg.feature}")
        o.append("")
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
}

SIZE4_CFG = {
    "small":  {"lines": 2, "named": 1},
    "medium": {"lines": 4, "named": 2},
    "large":  {"lines": 7, "named": 3},
}

F4_RULES = [
    "No early descent. Stairwells open six hours before collapse and not before.",
    "Stairwells exist only at stations 12, 24, 36, 48 and 72 on colored lines. Crawlers spawn past 72, so the game is getting backwards.",
    "Every transfer station has a safe room.",
    "Exit-only caverns sit at every fifth station. Mobs disembark there for their Rev-Up dose; a rollercoaster track at the back runs to the trainyard.",
    "Mobs get off every five stops. A mob that misses its stop panics; it cannot stay aboard and live. They break into cars 10 and 15, never car 5.",
    "Engineer keys open cars on their own line only. A conductor's souvenir hat opens the wall portals at the Abyss.",
    "Trains reset to the trainyard (ring 10) after every run. Employees remember 435, then blink, then they are boarding again.",
]


@dataclass
class Station:
    number: int
    kind: str                     # spawn | stairwell | transfer | exit_cavern
    note: str
    transfer_to: List[str] = field(default_factory=list)
    has_stairwell: bool = False
    safe_room: bool = False


@dataclass
class RailLine:
    color: str
    hex: str
    yard: str
    engineer: str
    engineer_species: str
    line_mob: Mob
    roster: List[str]
    withdrawal: str
    spawn: int
    stations: List[Station]


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


class Generator4:
    def __init__(self, size, seed, days):
        self.rng = random.Random(seed)
        self.seed = seed
        self.size = size
        self.cfg = FLOOR4_CFG
        self.sc = SIZE4_CFG[size]
        self.days_override = days
        self.used = set()

    def _unique(self, fn):
        for _ in range(100):
            v = fn()
            if v not in self.used:
                break
        self.used.add(v)
        return v

    def make_mob(self) -> Mob:
        adj, noun = self._unique(lambda: (self.rng.choice(TRAIN_MOB_ADJ), self.rng.choice(TRAIN_MOB_NOUN)))
        quirk, mod = self.rng.choice(MOB_QUIRKS)
        desc = self.rng.choice(SYSTEM_DESC).format(
            a=article(adj), b=f"{adj.lower()} {singular(noun).lower()}",
            noun=noun, noun_l=noun.lower(), adj_l=adj.lower(), noun_sing=singular(noun).lower())
        return Mob(adj, noun, quirk, mod, desc)

    def build(self) -> Floor4:
        n_lines = self.sc["lines"]
        colors = self.rng.sample(list(LINE_COLORS), n_lines)
        lo, hi = self.cfg["mob_band"]
        c = self.cfg

        lines: List[RailLine] = []
        for color in colors:
            mob = self.make_mob()
            spawn = self.rng.randint(*c["spawn_range"])
            lvl_a = self.rng.randint(lo, hi - 5)
            roster = [
                f"Porters and conductors (L{lvl_a}–{lvl_a + 4}), board at {c['yard_ring']}",
                f"{mob.name} (L{lvl_a + 2}–{lvl_a + 7}), board at {self.rng.choice([15, 20, 25, 30])}",
                "Janitor ghouls, board at 12",
                "Engineer, aboard from the yard; only leaves the cab for a derailment",
            ]
            lines.append(RailLine(
                color=color, hex=LINE_COLORS[color], yard=self.rng.choice("ABCDEFGHIJKLMNOPQ"),
                engineer=f"{self.rng.choice(BOSS_FIRST)} the {self.rng.choice(['7th', 'Elder', 'Unblinking', 'Punctual', 'Second-Shift', 'Late'])}",
                engineer_species=self.rng.choice(ENGINEER_SPECIES),
                line_mob=mob, roster=roster, withdrawal=self.rng.choice(WITHDRAWAL_PROFILE),
                spawn=spawn, stations=[]))

        # Stations per line: spawn, 1-3 stairwell numbers, exit caverns near spawn and near the stairs.
        stmap = {ln.color: {} for ln in lines}

        def add(ln, number, kind, **kw):
            d = stmap[ln.color]
            if number in d:
                st = d[number]
                if kind == "transfer":
                    st.transfer_to.extend(kw.get("transfer_to", []))
                    st.safe_room = True
                return st
            note = self.rng.choice(STATION_FLAVOR[kind]) if kind in STATION_FLAVOR else ""
            st = Station(number, kind, note, list(kw.get("transfer_to", [])),
                         has_stairwell=(kind == "stairwell"), safe_room=(kind == "transfer"))
            d[number] = st
            return st

        for ln in lines:
            add(ln, ln.spawn, "spawn")
            k = self.rng.randint(1, 3)
            for num in self.rng.sample(c["stairwell_numbers"], k):
                add(ln, num, "stairwell")
            cav = ((ln.spawn // 5) + 1) * 5
            add(ln, cav, "exit_cavern")
            add(ln, self.rng.choice([15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70]), "exit_cavern")

        # Transfers: spanning path + extras so the network is connected.
        order = lines[:]
        self.rng.shuffle(order)
        pairs = [(order[i], order[i + 1]) for i in range(len(order) - 1)]
        extra = max(0, n_lines - 2)
        for _ in range(extra):
            a, b = self.rng.sample(lines, 2)
            pairs.append((a, b))
        for a, b in pairs:
            lo_n = 11
            hi_n = max(a.spawn, b.spawn) + 40
            num = self.rng.randint(lo_n, min(hi_n, 420))
            if num % 5 == 0:
                num += 1
            add(a, num, "transfer", transfer_to=[b.color])
            add(b, num, "transfer", transfer_to=[a.color])
        for ln in lines:
            ln.stations = sorted(stmap[ln.color].values(), key=lambda s_: s_.number)

        # Named trains: loops over 3-4 detailed stations on 2+ lines.
        named = []
        for _ in range(self.sc["named"]):
            name = self._unique(lambda: f"{self.rng.choice(NAMED_PREFIX)} {self.rng.choice(NAMED_SUFFIX)}")
            k = self.rng.randint(3, 4)
            stops = []
            pool = [(ln.color, st.number) for ln in lines for st in ln.stations]
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
            named.append(NamedTrain(
                name=name, stock=self.rng.choice(NAMED_STOCK),
                interval=self.rng.choice([30, 45, 48, 60, 75, 90, 120]),
                cars=self.rng.choice([2, 3, 6, 12, 24, 38, 40]),
                stops=stops, entry=self.rng.choice(NAMED_ENTRY),
                gimmick=self.rng.choice(NAMED_GIMMICK), crew=crew))

        jan = self.make_mob()
        jan.quirk = self.rng.choice(JANITOR_MECHANICS) + " Board at station 12 and ride to 435."
        jan.description = "Janitor mob. " + jan.description
        days = self.days_override or c["days"]
        story = self.rng.sample(F4_STORY, 2)
        return Floor4(size=self.size, name=f"The {self.rng.choice(NAMED_PREFIX)} Tangle", days=days,
                      janitor=jan, janitor_level=self.rng.randint(*c["janitor_level"]),
                      lines=lines, named=named, mimic_tell=self.rng.choice(MIMIC_TELL),
                      story=story, seed=self.seed)


def render4(fl: Floor4) -> str:
    c = FLOOR4_CFG
    o = [f"# Floor 4 — {fl.name} ({fl.size})", "", "## Summary", ""]
    o.append(f"- **Countdown:** {fl.days} days (the normal allotment is {c['normal_days']})")
    o.append(f"- **Lines detailed:** {len(fl.lines)} colored lines, {len(fl.named)} named trains")
    n_stairs = sum(st.has_stairwell for ln in fl.lines for st in ln.stations)
    o.append(f"- **Stairwells on detailed lines:** {n_stairs}")
    for ln in fl.lines:
        nums = [str(st.number) for st in ln.stations if st.has_stairwell]
        o.append(f"  - {ln.color} Line: station{'s' if len(nums) != 1 else ''} {', '.join(nums)}")
    o.append(f"- **Train mob levels:** {c['mob_band'][0]}–{c['mob_band'][1]}, rising with withdrawal stage")
    o.append(f"- **Janitor mob:** {fl.janitor.name} (Level {fl.janitor_level}). {fl.janitor.quirk}")
    o.append(f"  - *{fl.janitor.description}*")
    o.append(f"- **The tail (every line):** {c['mimic_station']} is advertised as a transit hub and is a Station Mimic "
             f"(city boss, L{c['mimic_level']}); the tell is that {fl.mimic_tell}. "
             f"{c['employee_portal']} is where the crew portals back to the yard. "
             f"{c['abyss']} is the Abyss: bare platform, one steep stair, an hour's climb to a catwalk over the pit, "
             f"portals in the walls that want a conductor's hat.")
    o.append("- **Withdrawal climax:** in the final day the Rev-Up supply fails and the surviving crew hit stage 3; "
             "Krakaren-type bosses spawn across the network and the stairwells become a race.")
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
        o.append(f"- **Withdrawal profile:** {ln.withdrawal}")
        o.append(f"- **Crawler spawn:** station {ln.spawn}")
        o.append("- **Stations of note** (all others are routine platforms):")
        for st in ln.stations:
            kind = {"spawn": "SPAWN", "stairwell": "STAIRWELL", "transfer": "TRANSFER", "exit_cavern": "EXIT CAVERN"}[st.kind]
            extra = ""
            if st.transfer_to:
                extra = f" ↔ {', '.join(sorted(set(st.transfer_to)))} Line" + ("s" if len(set(st.transfer_to)) > 1 else "")
            sr = " (safe room)" if st.safe_room else ""
            o.append(f"  - **{st.number}** — {kind}{extra}{sr}. {st.note}")
        o.append(f"  - **{c['mimic_station']} / {c['employee_portal']} / {c['abyss']}** — mimic, crew portal, Abyss. See summary.")
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
        o.append("")
    return "\n".join(o)


def render4_svg(fl: Floor4) -> str:
    c = FLOOR4_CFG
    # Ordinal x positions for every station number that appears anywhere, plus fixed ends.
    nums = {c["yard_ring"], c["mimic_station"], c["employee_portal"], c["abyss"]}
    for ln in fl.lines:
        nums.update(st.number for st in ln.stations)
    for t in fl.named:
        nums.update(n for _, n in t.stops)
    ordered = sorted(nums)
    SPACING = 74
    LEFT = 150
    xs = {n: LEFT + i * SPACING for i, n in enumerate(ordered)}
    ROW = 110
    TOP = 120
    W = LEFT + len(ordered) * SPACING + 60
    n_named = len(fl.named)
    legend_h = 60 + 20 * n_named + 20
    H = TOP + ROW * len(fl.lines) + legend_h + 40
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="Helvetica, Arial, sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#f6f2ea"/>',
         _text(30, 36, f"Floor 4 — {fl.name}", size=22, weight="bold"),
         _text(30, 58, f"{fl.days} days  •  {len(fl.lines)} colored lines, {len(fl.named)} named trains  •  "
                       f"janitor: {fl.janitor.name} L{fl.janitor_level}  •  seed {fl.seed}", size=12, fill="#555")]
    # Column labels for the constants
    for n, lab in ((c["yard_ring"], "Trainyard"), (c["mimic_station"], "MIMIC"),
                   (c["employee_portal"], "crew portal"), (c["abyss"], "ABYSS")):
        o.append(_text(xs[n], TOP - 30, lab, size=11, weight="bold", anchor="middle", fill="#333"))
    # Vertical guide lines for the shared tail
    for n in (c["mimic_station"], c["employee_portal"], c["abyss"]):
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
        for n in (c["mimic_station"], c["employee_portal"]):
            o.append(f'<circle cx="{xs[n]}" cy="{y}" r="5" fill="#f6f2ea" stroke="#333" stroke-width="2"/>')
        o.append(f'<circle cx="{x1}" cy="{y}" r="9" fill="#111" stroke="#c0392b" stroke-width="3"/>')
    # Transfer connectors
    drawn = set()
    for ln in fl.lines:
        for st in ln.stations:
            for other in set(st.transfer_to):
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
            if st.safe_room:
                o.append(_text(x, y + 36, "safe room", size=9, anchor="middle", fill="#555"))
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
        (f'<circle cx="{lx+8}" cy="{ly}" r="8" fill="#fff" stroke="#111" stroke-width="3"/>', "Transfer (safe room)"),
        (f'<rect x="{lx+152}" y="{ly-7}" width="14" height="14" fill="#fff" stroke="#111" stroke-width="3" transform="rotate(45 {lx+159} {ly})"/>', "Stairwell station"),
        (f'<circle cx="{lx+300}" cy="{ly}" r="8" fill="#c0392b" stroke="#111" stroke-width="2"/>', "Crawler spawn"),
        (f'<polygon points="{lx+430},{ly-9} {lx+439},{ly+6} {lx+421},{ly+6}" fill="#fff" stroke="#111" stroke-width="2.5"/>', "Exit-only cavern"),
        (f'<rect x="{lx+560}" y="{ly-7}" width="14" height="14" fill="#333"/>', "Trainyard"),
        (f'<circle cx="{lx+670}" cy="{ly}" r="8" fill="#111" stroke="#c0392b" stroke-width="3"/>', "Abyss 436"),
    ]
    offs = [22, 170, 314, 446, 582, 686]
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
    "boss_offset": (15, 25),       # castle boss above the quadrant's top level
    "crawlers": (130, 170),
    "bubbles": 1172,
}

QUADRANT_ORDER = ["Air", "Land", "Sea", "Subterranean"]
F5_RULES = [
    "Every crawler is locked inside their starting quadrant until that quadrant's castle is conquered.",
    "A castle is conquered when the throne room is occupied or the quadrant boss is killed. Either counts.",
    "The stairwell is in the throne room. It will not open until all four castles in the bubble have fallen.",
    "Once your castle falls, you may cross into the other quadrants to help take theirs.",
    "When the fourth castle falls the top of the dome pops. The four stairwells open and the lacuna between bubbles becomes passable.",
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


class Generator5:
    def __init__(self, size, seed, days):
        self.rng = random.Random(seed)
        self.seed = seed
        self.size = size
        self.cfg = FLOOR5_CFG
        self.days_override = days
        self.used = set()

    def _unique(self, fn):
        for _ in range(100):
            v = fn()
            if v not in self.used:
                break
        self.used.add(v)
        return v

    def make_mob(self, noun) -> Mob:
        adj = self.rng.choice(RUIN_MOB_ADJ + MOB_ADJ)
        quirk, mod = self.rng.choice(MOB_QUIRKS)
        desc = self.rng.choice(SYSTEM_DESC).format(
            a=article(adj), b=f"{adj.lower()} {singular(noun).lower()}",
            noun=noun, noun_l=noun.lower(), adj_l=adj.lower(), noun_sing=singular(noun).lower())
        return Mob(adj, noun, quirk, mod, desc)

    def make_castle(self, q: str, mob: Mob, top: int, diff) -> Castle:
        qd = QUADRANT_DATA[q]
        first = self.rng.choice(BOSS_FIRST)
        name = self._unique(lambda: self.rng.choice(CASTLE_NAME).format(
            adj=self.rng.choice(CASTLE_ADJ), thing=self.rng.choice(CASTLE_THING),
            noun=self.rng.choice(CASTLE_NOUN), name=self.rng.choice(BOSS_FIRST)))
        ruler = f"{first} {self.rng.choice(RULER_TITLE)}"
        sz = self.rng.randint(20, 60)
        form = self.rng.choice(BOSS_FORMS).format(
            size=sz, a_size="a", noun_l=mob.noun.lower(), noun_sing=singular(mob.noun).lower())
        bump = {"Easy": 10, "Moderate": 15, "Hard": 20, "Brutal": 25}[diff[0]]
        lvl = top + bump + self.rng.randint(-2, 2)
        boss = Boss(f"{ruler}, ruler of {name}", "castle", lvl, form,
                    " ".join(self.rng.sample(BOSS_MECHANICS, 2)), "the throne room", True)
        c = Castle(name=name, form=self.rng.choice(qd["castle_forms"]), ruler=ruler, boss=boss,
                   throne=self.rng.choice(THRONE_NOTE), difficulty=diff[0], difficulty_note=diff[1])
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
        return Floor5(
            size=self.size, bubble=self.rng.randint(1, self.cfg["bubbles"]),
            days=self.days_override or self.cfg["days"], crawlers=self.rng.randint(*self.cfg["crawlers"]),
            bubble_state=self.rng.choice(BUBBLE_STATE), rival=self.rng.choice(RIVALS),
            factions=factions, tension=tension, storyline=self.rng.choice(STORYLINE),
            quadrants=quadrants, suggested_start=start, seed=self.seed)


def render5(fl: Floor5) -> str:
    lo, hi = FLOOR5_CFG["mob_band"]
    o = [f"# Floor 5 — Bubble {fl.bubble} ({fl.size})", "", "## Summary", ""]
    o.append(f"- **Countdown:** {fl.days} days")
    o.append(f"- **Crawlers in the bubble:** about {fl.crawlers}. {fl.bubble_state} Of note: {fl.rival}.")
    o.append("- **Quadrants:** Air, Land, Sea, Subterranean — one castle each, stairwell in every throne room.")
    o.append(f"- **Quadrant mob levels:** {lo}–{hi}; castle bosses well above their quadrant.")
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
        o.append(f"- **Settlement:** {q.settlement}.")
        if q.faction:
            o.append(f"- **Faction:** {q.faction.name}; want {q.faction.want}.")
        o.append(f"- **Mob:** {q.mob.name}, Level {q.level_range[0]}–{q.level_range[1]}")
        o.append(f"  - {q.mob.quirk}")
        o.append(f"  - *{q.mob.description}*")
        o.append(f"- **Getting to the castle:** {q.traversal}")
        o.append(f"- **Castle:** {c.name} — {c.form}, ruled by {c.ruler}.")
        if c.approach:
            o.append(f"  - Approach: {c.approach}")
        if c.obstacle:
            o.append(f"  - Inside: {c.obstacle}")
        for room, desc in c.chambers:
            o.append(f"  - {room}: {desc}.")
        o.append(f"  - Throne room: {c.throne}")
        o.append(f"- **Quadrant Boss:** {c.boss.name} (Level {c.boss.level})")
        o.append(f"  - Form: {c.boss.form}.")
        o.append(f"  - Mechanic: {c.boss.mechanic}")
        o.append("")
    o.append("## DM Material")
    o.append("")
    o.append("Crawlers are meant to work this out on their own (or not).")
    o.append("")
    for q in fl.quadrants:
        o.append(f"- **{q.name}:** {q.castle.difficulty}. {q.castle.difficulty_note}")
    o.append(f"- **Suggested starting quadrant:** {fl.suggested_start} (the easiest castle). Your call.")
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
            (_fit(q.castle.name, 30), 12, "bold", "#111"),
            (f"Boss L{q.castle.boss.level}", 11, "normal", "#222"),
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
    "Blood bar: safe-room time drains it; only kills refill it. Empty bar means you are teleported outside.",
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
    def __init__(self, size, scaling, seed, days):
        super().__init__(size, scaling, seed, days)
        self.cfg = FLOOR6_CFG

    def settlement_name(self):
        return self._unique(lambda: self.rng.choice(SETTLEMENT_PREFIX) + self.rng.choice(SETTLEMENT_SUFFIX))

    def region_name(self):
        return self._unique(lambda: f"The {self.rng.choice(JUNGLE_ADJ)} {self.rng.choice(JUNGLE_PLACE)}")

    def make_mob(self) -> Mob:
        adj, noun = self._unique(lambda: (self.rng.choice(JUNGLE_MOB_ADJ), self.rng.choice(JUNGLE_MOB_NOUN)))
        quirk, mod = self.rng.choice(MOB_QUIRKS)
        desc = self.rng.choice(SYSTEM_DESC).format(
            a=article(adj), b=f"{adj.lower()} {singular(noun).lower()}",
            noun=noun, noun_l=noun.lower(), adj_l=adj.lower(), noun_sing=singular(noun).lower())
        return Mob(adj, noun, quirk, mod, desc)

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
                 f"Safe-room inn present (blood bar applies){'; tutorial guild hall' if st.tutorial_guild else ''}"
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
        if reg.boss:
            b = reg.boss
            o.append(f"- **{b.tier.title()} Boss:** {b.name} (Level {b.level})")
            o.append(f"  - Form: {b.form}.")
            o.append(f"  - Roams between here and {reg.roams_to}." if reg.roams_to else "  - Stays in this region.")
            o.append(f"  - Mechanic: {b.mechanic}")
        if reg.camp:
            o.append(f"- **Hunter camp:** {reg.camp}'s party (see Hunter Parties).")
        if reg.stairwell:
            o.append(f"- **Stairwell {reg.stairwell.label}:** {reg.stairwell.condition}.")
        o.append(f"- **Loot theme:** {reg.loot}")
        o.append(f"- **Notable feature:** {reg.feature}")
        o.append("")
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
    "Every surviving squad keeps one T'Ghee card when the floor collapses. Blood bar applies to safe rooms.",
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


class Generator8:
    def __init__(self, size, seed, days, region=None):
        self.rng = random.Random(seed)
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


class Generator9:
    def __init__(self, size, seed, days, warlords="canon", wildcard=False, winner=None, runner_up=None):
        self.rng = random.Random(seed)
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
        ang = math.radians(-90 + (i + 0.2 + 0.6 * ((hash(sp) % 100) / 100)) * 360 / n)
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


class Generator10:
    def __init__(self, size, seed, vehicle=None, party=2):
        self.rng = random.Random(seed)
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


def write_images10(fl: Floor10, path: str) -> List[str]:
    import os
    root, ext = os.path.splitext(path)
    out = []
    for h in fl.heats:
        p = f"{root}_heat{h.number}{ext}"
        svg = render10_heat_svg(fl, h)
        if ext.lower() == ".png":
            _svg_to_png(svg, p)
        else:
            with open(p, "w", encoding="utf-8") as f:
                f.write(svg)
        out.append(p)
    return out


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


class Generator11:
    def __init__(self, size, seed):
        self.rng = random.Random(seed)
        self.seed = seed
        self.size = size

    def build(self) -> Floor11:
        themes = self.rng.sample(THEMES, 5)
        theme, others = themes[0], themes[1:]
        c = FLOOR11_CFG
        events = sorted((round(self.rng.uniform(0.2, 2.9), 1), ev) for ev in self.rng.sample(ROUTE_EVENTS, 4))
        lo, hi = c["boss_level"]
        boss = Boss(f"The {theme} Marshal", "floor", self.rng.randint(lo, hi), self.rng.choice(PARADE_BOSS_FORM),
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
    heads = list(re.finditer(r"^#{2,4}\s*([A-Za-z])\.\s*.+$", text, re.M))
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
    heading = re.search(r"^#{2,4}\s*([A-Za-z])\.\s*(.+?)\s*$", block, re.M)
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
        idx=0, label=label, name=name, row=0, col=0, borough=None, mob=mob,
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

def main(argv=None):
    p = argparse.ArgumentParser(description="Dungeon Crawler Carl floor generator (floors 1-2).")
    p.add_argument("--floor", type=int, choices=[1, 2, 3, 4, 5, 6, 8, 9, 10, 11], default=1)
    p.add_argument("--size", choices=list(SIZE_CFG), default="medium")
    p.add_argument("--scaling", choices=["distance", "random"], default="distance",
                   help="distance: mob levels rise with distance from entry; random: independent per zone")
    p.add_argument("--seed", type=int, default=None, help="RNG seed for reproducible maps")
    p.add_argument("--days", type=int, default=None, help="override the random countdown length")
    p.add_argument("--no-boss-stairs", action="store_true",
                   help="omit stairwells from borough/city boss chambers (neighborhood chambers always have one)")
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
    p.add_argument("--rooms-for", type=str, default=None, metavar="FILE",
                   help="skip normal generation; parse one '### X. Name' neighborhood block from FILE "
                        "(as produced by this script, floor 1/2 only) and (re)generate just its room "
                        "detail -- useful for adding rooms to a floor you already generated and are playing")
    p.add_argument("--rooms-for-label", type=str, default=None, metavar="LABEL",
                   help="--rooms-for: which neighborhood letter to use if FILE has more than one (default: the first found)")
    a = p.parse_args(argv)

    seed = a.seed if a.seed is not None else random.randrange(1_000_000)

    if a.rooms_for:
        if a.floor not in (1, 2):
            sys.exit("--rooms-for only supports floor 1 or 2 neighborhoods (use --floor 1 or --floor 2)")
        with open(a.rooms_for, encoding="utf-8") as f:
            raw = f.read()
        block = _extract_neighborhood_block(raw, a.rooms_for_label)
        nb = parse_neighborhood_block(block)
        gen = Generator(a.floor, a.size, a.scaling, seed, a.days, not a.no_boss_stairs)
        guildmaster_form = MORDECAI_FORMS.get(str(a.floor), "a local guildmaster")
        nb.rooms, nb.corridor_encounters = gen.make_rooms(nb, guildmaster_form)
        print(f"parsed {nb.label}. {nb.name} -- mob {nb.mob.name} L{nb.level_range[0]}-{nb.level_range[1]}, "
              f"boss {nb.boss.name} L{nb.boss.level}, safe_rooms={nb.safe_rooms}, "
              f"hidden_stairwells={len(nb.hidden_stairs)}, tutorial_guild={nb.tutorial_guild}", file=sys.stderr)

        rooms_text = render_rooms_only(nb)
        if a.out:
            with open(a.out, "w", encoding="utf-8") as f:
                f.write(rooms_text + "\n")
            print(f"wrote {a.out} (seed {seed})", file=sys.stderr)
        else:
            print(rooms_text)

        if a.image:
            import types
            fake_fl = types.SimpleNamespace(number=a.floor, seed=seed)
            svg = render_room_svg(nb, fake_fl)
            if a.image.lower().endswith(".png"):
                _svg_to_png(svg, a.image)
            else:
                with open(a.image, "w", encoding="utf-8") as f:
                    f.write(svg)
            print(f"wrote {a.image}", file=sys.stderr)
        return

    if a.floor == 11:
        fl = Generator11(a.size, seed).build()
        text = render11(fl)
    elif a.floor == 10:
        fl = Generator10(a.size, seed, a.vehicle, a.party).build()
        text = render10(fl)
    elif a.floor == 9:
        fl = Generator9(a.size, seed, a.days, a.warlords, a.wildcard, a.winner, a.runner_up).build()
        text = render9(fl)
    elif a.floor == 8:
        fl = Generator8(a.size, seed, a.days, a.region).build()
        text = render8(fl)
    elif a.floor == 6:
        fl = Generator6(a.size, a.scaling, seed, a.days).build()
        text = render6(fl)
    elif a.floor == 5:
        fl = Generator5(a.size, seed, a.days).build()
        text = render5(fl)
    elif a.floor == 4:
        fl = Generator4(a.size, seed, a.days).build()
        text = render4(fl)
    elif a.floor == 3:
        fl = Generator3(a.size, a.scaling, seed, a.days).build()
        text = render3(fl)
    else:
        gen = Generator(a.floor, a.size, a.scaling, seed, a.days, not a.no_boss_stairs)
        fl = gen.build()
        text = render(fl, not a.no_boss_stairs)

    if a.save is not None:
        import os
        os.makedirs(a.save, exist_ok=True)
        base = os.path.join(a.save, f"floor{a.floor}_{a.size}_seed{seed}")
        try:
            import resvg_py  # type: ignore  # noqa: F401
            img = base + ".png"
        except ImportError:
            img = base + ".svg"
        a.out = a.out or base + ".md"
        a.image = a.image or img

    if a.image:
        if isinstance(fl, Floor10):
            for pth in write_images10(fl, a.image):
                print(f"wrote {pth}", file=sys.stderr)
        elif isinstance(fl, Floor):
            for pth in write_images(fl, a.image, not a.no_boss_stairs):
                print(f"wrote {pth}", file=sys.stderr)
        else:
            write_image(fl, a.image, not a.no_boss_stairs)
            print(f"wrote {a.image}", file=sys.stderr)

    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {a.out} (seed {seed})", file=sys.stderr)
    else:
        print(text)

if __name__ == "__main__":
    main()
