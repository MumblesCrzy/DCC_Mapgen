# DCC Mapgen

A procedural floor/map generator in the style of the *Dungeon Crawler Carl* book series (Matt Dinniman). Generates Markdown write-ups and SVG/PNG maps for Floors 1–6 and 8–11, with random (but seed-reproducible) neighborhoods, bosses, mobs, loot, quests, and — for Floors 1 and 2 — a full room-by-room layout for every neighborhood.

> **Unofficial fan project.** This tool is not affiliated with, endorsed by, or reviewed by Matt Dinniman, his publishers, or any other rights holders. It's a GM/worldbuilding aid built from publicly available fan-wiki lore, meant for personal tabletop/solo use.

## Features

- **Floors 1 & 2** — neighborhood/borough/city grid, from small up through large (1 City). Beyond that, huge/giant/massive/continent tile multiple Cities together — County (4 Cities), State (9), Country (16), Continent (25) — each City keeping its own City Boss, with one top-tier boss (County/State/Country/Continent) overseeing all of them. Bosses (with a word-bank uniqueness guard so names/forms don't collide), loot boxes, achievements, an Elite, other crawler teams, the tutorial guild, and a full connected **room graph** per neighborhood (safe rooms, hidden passages, guild hall, filler rooms, corridor encounters). `--stair-chance` and `--borough-boss-rate` tune how often neighborhood stairwells and borough bosses show up.
- **Floor 3** (The Over City) — settlements on a wilderness grid, roaming bosses, locked stairwells, a quest board per town, one Elite.
- **Floor 4** (The Iron Tangle) — schematic rail/transit map, laid out the way the books lay it out, with its content from this project's own word banks rather than the books' plot:
  - **Stations:** every line has stairwells at 12, 24, 36, 48 and 72. A transfer has a safe room only if its number is prime, a Desperado Club if it ends in 1, and a Club Vanquisher if it ends in 9.
  - **Transfers and safe rooms:** most stations on the Iron Tangle are transfers, to far more lines than a floor details. So besides meeting each other, each line meets lines that aren't detailed, at a prime in each stretch a party uses: the yard to the stairwells, the stairwells to its spawn, and the forty stations past it. That gives every line a safe room in each stretch, and a floor 4–6 safe rooms on small, 8–12 on medium and 14–21 on large. A transfer to a line that isn't detailed is marked "not detailed here" on the line's list, says "change for the X Line" in its safe room's heading, and appears as a short stub in that line's color on the map.
  - **Withdrawal:** the drug is named, the Rev-Up Amazing Cure-All Vitamin Immunity Shot, and a supplier hands out the shots at every exit-only cavern. Three stages, each with its symptoms, its toll and what its dead come back as; a schedule for each line, fitted to the countdown; and two things that go wrong on the last day.
  - **Ghoul stations:** the five stairwell stations, each named, on the map too, with one kind of ghoul gathering there. Station 12 belongs to the janitors, three hold what each withdrawal stage raises, and one holds a ghoul of its own. Each says what adds to the crowd, how it grows over the countdown, what it does when the stairwell opens and what its control sigil does; at one of them, enough ghouls merge into a boss.
  - **Getting around:** which car carries whom, and how long the floor takes to cross. Each line says how long its crawlers have before their train reaches 435, how far it is to walk back to the stairwells, and how many stops ahead the nearest exit cavern is. The books give the anchors: a stop every 2 to 20 minutes, about three days from 11 to 435, stations a block or two apart up to 72 and 40 or 50 miles apart out past 300. The curves between them are ours, tunable in `FLOOR4_CFG`. Two special lines run outside the colored ones, one back from the stairwell stations and one for staff only.
  - **Safe rooms and hubs:** every safe room is somebody's business, with a proprietor, something on offer and a quest. One or two hubs per floor, where far more lines meet than the floor details, have shops, guilds and a noticeboard quest.
  - **Named trains:** every one starts a timed quest when boarded, with a reward and a failure.
  - **Station 75:** an interdiction depot, its repair carts at five times train speed, their times worked out from the same model as the trains. Derailments and boiler blowouts are possible, and bring its crews out.
  - **The tail and the yards:** how to fight the Station Mimic, 435's non-combatant staff, the Abyss's pull, and every yard the floor's lines run from. Station 435 has its own marker on the map.
  - **Achievements:** the ones most often earned on the Iron Tangle. Every quest and achievement box shows up in the floor's box suggestions. Nothing says what opens the portals at the Abyss.
- **Floor 5** (The Bubbles) — four quadrants, castles, factions. Each castle's ruler is its quadrant's boss: a borough boss, or a city boss in the castle meant to fall last, as the books field borough bosses at L52–55 and a city boss at L82. Harder castles keep borough-boss guardians as well, and killing one doesn't take the castle. A popped bubble lets its crawlers out, but they can't enter another until it has popped too.
  - **Crawlers and where they start:** the bubble's 130–170 crawlers are shared out unevenly between the quadrants, at least 15 to each, and each quadrant says how many it holds, since none can leave until its castle falls. Each quadrant has two to four starting sites by size (the books' Air quadrant had four), each with its share of crawlers: a faction's own settlement with its safe-room inn, the quadrant mob's ground ("the Kite City of the Razorfoxes", where a crawler starts in a fight), or a ruin with an abandoned safe room. Other Crawlers names the quadrant and the site each group is at.
  - **Quests:** each faction posts quests (one each on a small bubble, three on a large, the books' nine), shaped like the books' Fifth Floor ones — free a prisoner, find out who killed my son, kill someone, stay out of a building, get a loose creature back — and filled in with the bubble's own castles, rulers, rival factions, settlements and mobs.
  - **Achievements:** nine the books hand out on the Fifth Floor, leaving out the ones tied to its plot, then eight built on how a bubble works. Their boxes reach the floor's box suggestions and open in the roller.
  - Both sections say they are suggested ideas, not a definitive list.
- **Floor 6** (The Hunting Grounds) — Floor 3's grid reskinned as jungle, with hunter parties and a bounty board.
- **Floor 8** (Ghosts of Earth) — polar folklore map with totem cards and a deckmaster ladder.
- **Floor 9** (Faction Wars) — the nine-slice pie around Larracos, warlords, stance paths.
- **Floor 10** (Don't Come In Last) — seven-heat race season, garage/vehicle/upgrades.
- **Floor 11** (A Parade of Horribles) — parade route, judges, arena boss.
- **What's switched on** — every floor's write-up says which of the dungeon's systems are new on that floor, which have gone, which are still on from earlier floors, and what the next floor brings: sponsors from Floor 4 (a second on 5, a third on 6), the leaderboard and personal spaces from 4, races and classes on 3, guilds and the thirty-member party cap on 6, the Desperado Club's levels on 3, 6 and 9, and so on. The floors come from the DCC Database's Crawler, Safe Rooms, Sponsors, Guilds, Marketplace and Game Guide pages. Where two pages disagree, the System's own announcement wins: personal spaces go on sale on the Fourth Floor. There is no blood bar: an earlier version gave Floors 6 and 8 one that drained in safe rooms, but nothing in the Database has one, so it is gone.
- **Shops and prices** — from Floor 3, where marketplaces open, every write-up has a price list and the rules for buying and selling. From the books:
  - general stores are everywhere and buy anything, but sell at three times a specialist's price;
  - specialists are only in settlements;
  - merchants have only so much gold to buy with;
  - a shopkeeper exaggerates unless the crawler's Charisma beats the shopkeeper's level;
  - a personal space costs 50,000 gold.

  The books name no item prices, so those are this project's own. An item's list price is about the gold in a loot box of its tier on that floor (5 × the kind's factor × Floor ^ tier), so finding an item is worth about as much as finding the gold. Kind factors run from potions at ×0.5 to offworld goods at ×2. A general store pays a quarter of list. Charisma moves both buying and selling by the stat rules' 2% a point. An opened box says what a general store would pay for everything in it. The numbers live in `SHOP_RULES` in `data/common.json`.
- **Campaigns** — other crawler teams carry from floor to floor, keeping who they are and where they stand with the party, until their luck runs out. From Floor 3 they carry a party of named races and classes too, and from Floor 4 a Top Ten leaderboard with bounties tracks who is ranked, who fell off and who came back. See [Campaigns](#campaigns).
- A **desktop app** (`dcc_mapgen_gui.py`) wrapping all of the above: pick a floor and size, generate, and read the write-up and browse every map without leaving the window. See [Desktop app](#desktop-app).
- Every floor supports `--seed` for reproducible maps — byte-for-byte, and [tested](#tests) as such — and `--image`/`--save` for SVG or PNG output (PNG needs the optional `resvg-py` package). The seed is per floor: one seed carried across a whole campaign gives a different dungeon on each floor.
- `--rooms-for` can (re)generate just the room-level detail for one neighborhood from a floor you already generated and are playing, without touching the rest of the data.

Word banks (names, mobs, bosses, loot, room flavor, etc.) live in `data/*.json` — edit those to add or reskin content without touching the code.

Mob vocabulary is shared rather than kept per floor. `data/common.json` holds `MOB_NOUN`
and `MOB_ADJ` grouped by where a word can show up — `anywhere`, `urban`, `rail`,
`jungle` — and `FLOOR_MOB_TAGS` says which of those each floor draws from, and how
heavily:

```json
"FLOOR_MOB_TAGS": { "4": { "anywhere": 2, "rail": 3 } }
```

The tag is picked first by weight, then a word from it, so the Iron Tangle leads with
porters and signalmen but still turns up the occasional Hazard-Pay Shark. To add a mob,
drop it under the tag it belongs to; to give a floor a new habitat, add the tag to both
banks and list it for that floor.

`BOSS_MECHANICS` is split the same way, into `any` and `grand`. Neighborhood bosses
draw only from `any`; borough, city and top-tier bosses take one `grand` mechanic —
the arena-scale ones that reshape a room — plus one ordinary one. A floor deals them
from a shuffled deck, so it runs through every mechanic before repeating any, and a
boss run is not the same fight over and over.

Boss *names* are built the same way. `BOSS_NAME_FORM` holds the shapes a name can
take, with a weight each, and a floor deals them from a deck so it works through all
of them before reusing one:

```json
["{first} {title}", 6],
["The {adj} {noun} {title}", 4],
["{first} \"{nick}\" {surname}", 2],
["{first} and {second}, {title}", 1]
```

The placeholders are filled from `BOSS_FIRST` (137), `BOSS_TITLE` (152),
`BOSS_SURNAME` (70) and `BOSS_NICKNAME` (36), plus the floor's own adjective bank
and the mob the boss leads. A first name or a title spent on one boss is not reused
on that floor, so no two bosses read as the same person, and a name longer than 50
characters is rerolled — stacking a title onto an already multi-word mob produced
*The Burning Goblin Bomb Bard the Non-Disclosure Agreement*. Floor 11's single
Marshal has its own shapes in `PARADE_BOSS_NAME`.

Quests on Floors 3 and 6 work the same way. A town posts three, six or nine of them
by size, so a large floor carries about thirty across seven towns — far more than
`rng.choice` on a ten-entry bank can fill without repeating. Objectives, items,
rewards, flavor lines, giver trades and title forms are each dealt from their own
per-floor deck, and the banks are sized to outlast a large floor:

| bank | entries | | bank | entries |
|---|---|---|---|---|
| `QUEST_OBJECTIVE` | 52 | | `QUEST_REWARD` | 40 |
| `QUEST_ITEM` | 50 | | `QUEST_FLAVOR` | 40 |
| `QUEST_GIVER_ROLE` | 42 | | `QUEST_RELATIVE` | 24 |
| `QUEST_TITLE` | 22 | | | |

`QUEST_TITLE` is new — the five title forms used to be inlined in the code and all
keyed off the giver's trade or the town, which a nine-quest town repeats at once.
Titles can now name the thing the quest is about (`{item}`, `{mob}`, `{region}`),
and a form is only used if the objective actually mentioned that thing, so you never
get *The Price of the Town Charter* over a boundary dispute with no charter in it.
Note that `{settlement}` in an objective means *somewhere else* while `{town}` in a
title means the place posting the quest.

### Loot boxes

Every floor's write-up has a **Loot Boxes** section, and every boss names the box it
drops. The rules are canon: boxes open only in a safe room or a guildhall, opening
is all or nothing, the type says what is inside and the tier says how good it is,
and a Benefactor box outranks every other type tier for tier — a Bronze Benefactor
box beats a Gold Adventurer box.

`LOOT_BOXES` in `data/common.json` holds eleven box types, each with the floor it
first appears on: Benefactor and Bounty boxes arrive with sponsors and the
leaderboard on Floor 4, Venison a floor later. Boss drops climb with both the boss's
tier and the floor's depth — Floor 1's neighborhood bosses drop Bronze, as the books'
first one did; Floor 11's Marshal drops Legendary — and no boss drops Celestial.

A floor **suggests** boxes and never rolls them. Its write-up ends the loot section
with a table of every box it names — boss drops, quest rewards, achievement
rewards — grouped, so a DM can see the floor's loot at a glance. What goes *in* a
box is rolled separately, after the DM has decided what the box is: hand out the
Silver Boss Box as suggested, or make it Gold, or make it a Weapon box, then open it.

```
python dcc_mapgen.py --open-box "a Silver Boss Box" --floor 3   # pasted straight off a write-up
python dcc_mapgen.py --open-box "Gold Weapon" --count 3 --floor 6 --seed 7
```

```
- **Celestial Weapon Box** — Celestial: **Marjorie's Mistake**, a meat cleaver (pauses
  the floor's countdown for its owner once; shows its owner every crawler on the floor;
  turns one of its owner's achievements into its Platinum version). Platinum: a scroll
  of short-range teleport; a potion of water breathing; a vial of liquid luck.
  **279,936 gold**.
```

In the desktop app the **Loot Boxes** tab does the same: it lists the current floor's
boxes and fills in the floor, picking one fills in the tier and type, and any of them
can be changed before **Open**. Because nothing about a box touches the floor,
rolling one can never move a map.

**What a box holds.** One item is at the box's own tier and of the box's type. Every
other item is lesser, and rolls two things: its tier, evenly, from the box's range —
so a Celestial box never holds a second Celestial — and what kind of thing it is.
That second roll is what keeps a box from being one great item and a pile to pawn:

| A lesser item is… | Chance |
|---|---:|
| the same kind as the main item (a Weapon box: another weapon) | 15% |
| another piece of equipment — armor, accessories, weapons — other than that kind | 35% |
| a consumable: a potion, a scroll | 50% |

Equipment leaves out the main item's own kind, so a Weapon box reaches another weapon
only through the 15% rather than about 27% of the time. For a Boss box, which mixes
weapons, apparel and magic, "the same kind" is whatever its main item turned out to
be. The line groups items under the tier each rolled, best first, since a potion or a
tool says nothing about its own tier.

| Tier | At the box's tier | Lesser items | Each rolled from | Gold, from Floor 2 |
|---|---:|---:|---|---|
| Bronze | 1 | 2 | Bronze | (1–10) × Floor |
| Silver | 1 | 3 | Bronze | (1–10) × Floor² |
| Gold | 1 | 4 | Bronze, Silver | (1–10) × Floor³ |
| Platinum | 1 | 3 | Bronze to Gold | (1–10) × Floor⁴ |
| Legendary | 1 | 2 | Silver to Platinum | (1–10) × Floor⁵ |
| Celestial | 1 | 3 | Gold to Legendary | (1–10) × Floor⁶ |

Floor 1 boxes hold no gold. A Benefactor box pays the gold of the tier it opens at,
and a Bounty or Venison box holds its bounty rather than a roll. Each floor's
write-up prints the gold range for its own floor beside the tier table — on Floor 6
a Bronze box holds 6–60 gold and a Celestial one 46,656–466,560. The counts, the
lesser tier ranges, the 15/35/50 split and the gold formula are all in `LOOT_BOXES` in
`data/common.json`.

Boxes are the most generated thing in the project — a large Floor 1 names about 37,
a huge one about 100 — so the banks are sized for volume: 460 items in ten pools and
154 enchantments. Consumables carry the most, being half of every lesser item, so
they have the biggest bank: 136, most of them in the low band that early floors'
Bronze and Silver items draw from. A roller keeps its decks for as long as it runs, so boxes opened
together with `--count`, or in one app session, work through a whole pool before
repeating anything; every box a large Floor 1 names comes out of one session with no
item twice.

Enchantments are tagged by what they can sit on (`weapon`, `worn` or `any`), so a
jacket never "deals extra damage on a hit" and a scroll never "silences its owner's
footsteps". Items that take no enchantment — potions, tools, explosives, oddities —
come in low, mid and high bands: a plain headline item sits in its box's band, so a
Gold Sapper's box leads with mid-band kit, while the Bronze filler draws from the low
band. Achievement boxes the banks already award (Shoe,
Sapper's, Goblin, Lucky Bastard…) open from pools that match their names, and any
name the bank does not know still opens, from the Adventurer pools.

**Benefactor boxes.** A sponsor can see what their crawler is about to face, and a
DM handing over a Benefactor box is usually setting up something specific. Prepare
the box for it:

```
python dcc_mapgen.py --open-box "Silver Benefactor" --for "the undead" --floor 6
```

```
- **Silver Benefactor Box** *(opened at Legendary; prepared for the undead)* —
  Legendary: **Lettice's Punchline**, a bundle of incense (the undead cannot touch its
  owner; +10 to Charisma while visible, which sponsors notice). Platinum: a scroll of
  turn undead. Silver: a vial of holy water. **15,552 gold**. From the Society for
  Earthling Appreciation: "They don't stay down."
```

The main item takes a counter as one of its enchantments, every lesser item is a
counter to the same threat at the tier it rolled, and the sponsor signs a note that
hints without telling — they are not allowed to just say. There are twelve threats:
fire, cold, poison, undead, water, darkness, falls, traps, swarms, giants, flyers and
casters, each with counters at three power levels in `LOOT_BOXES.benefactor`.
Unprepared, a Benefactor box is the ordinary one, with a sponsor's compliments.

From Floor 4, where sponsors start, each floor's write-up suggests what to prepare
for. It reads the floor's own bosses, mobs, hazards and features for words that
point at a threat and lists the strongest, with the line that shows it best:

```
| Prepare for | Signs | For example |
|---|---:|---|
| `--for poison` (poison and gas) | 9 | Flora: Spore clouds at dusk: a slow disease debuff unless yo… |
| `--for water` (deep water) | 9 | River: runs through here; crossing is no crossing:… |
| `--for giants` (something huge) | 4 | Dr. Havel-Sung's party (Half-Giant, 3 hunters, Level 50) |
```

It suggests; the DM picks. Floor 8, Ghosts of Earth, comes out undead every time;
Floor 11's parade, with nothing in particular to prepare for, suggests nothing. The
sections every floor shares — the crawler roster, the loot rules that name all twelve
threats, the stat rules — are left out of the reading. So are names (a warlord called
Peregrine Ember is not a fire hazard), single stray words, and a handful of words
measured as false signals: "pit" is Floor 10's pit lane, "after dark" is about night,
"Blind, but hunt by sound" is a mob quirk. The signal words for each threat are in
`LOOT_BOXES.benefactor.threats.<threat>.signs`. In the app, the threat suggestions
head the Loot Boxes tab's list; picking one sets up a prepared Benefactor box.

**Fan boxes.** A Fan box is the audience's call. Set how much they liked whatever
earned it, and they vote twice: two likes is good, two dislikes bad, a split neutral.

| Liked | Good | Neutral | Bad |
|---:|---:|---:|---:|
| 90% | 81% | 18% | 1% |
| 70% | 49% | 42% | 9% |
| 50% | 25% | 50% | 25% |
| 30% | 9% | 42% | 49% |
| 10% | 1% | 18% | 81% |

Good is one useful item — equipment or a consumable — at the box's tier. Neutral is
one harmless thing, probably at the crawler's expense: *a framed photo of your ex,
signed "thinking of you"*. Bad costs them, and costs more the bigger the box — a
Bronze Fan box glitter-bombs the crawler until the next safe room; a Legendary one
might reanimate their ex, armed and still upset about the thing. Good and neutral
boxes carry the floor's gold; a bad one does not.

```
python dcc_mapgen.py --open-box "Gold Fan" --liked 70 --floor 6
```

In the app, both are on the **Loot Boxes** tab: a "Benefactor prepared for" list and a
"Fan box liked %" box that shows the odds as you type.

Floors 3 and 6 give every region a day mob and a `NIGHT_THREATS` line saying what
changes after dark. Most of those lines are the same mob behaving differently, but
some mean it is only there for half the cycle — and those need a second creature.
An entry tagged with a shift gets one rolled:

```json
["Dormant by day; at night they hunt in packs led by a Level +5 alpha.", "day"],
["At night they retreat to nests and the region's real predator comes out.", "night"],
"Night doubles their level and halves their loot."
```

The tag names the half the listed mob *doesn't* cover, so the first line gets the
region a daylight mob and the second gets it an after-dark one. Plain strings get
nothing extra. About three regions on a large floor come out with two mobs on the
same level band. The second of those entries used to end with the parenthetical
"(roll a second mob)", which is a generator asking the table to finish its job;
[a test](#tests) now fails on any line that asks for a roll, and on any new line
that says a region empties out without carrying a tag.

Loot themes (`LOOT_THEMES`, 58) and room features (`NOTABLE_FEATURES` for Floors 1–2,
`RUIN_FEATURES` for Floors 3 and 6) are dealt from a deck for the same reason. They
used to be fifteen entries sampled per cell, so a large Floor 3 covered 18 regions
with 11 loot themes and used one of them three times. A floor now works through the
bank before repeating anything, and past that — a continent is 400 neighborhoods —
the repeats are spread evenly instead of clumping.

## Examples

| Main map | Room detail (per neighborhood) |
|---|---|
| ![main map example](sample/floor1_large_seed914613.png) | ![room detail example](sample/floor1_large_seed914613_room_A.png) |

(`sample/floor1_large_seed914613.md` in this repo is a full sample write-up for a large dungeon if you want to see the Markdown output.)

## Installation

### Option A: run from source (any OS)

Requires Python 3.9+.

```
git clone https://github.com/MumblesCrzy/DCC_Mapgen.git
cd DCC_Mapgen
pip install -r requirements.txt   # optional, only needed for PNG output
python dcc_mapgen.py --floor 1 --size medium
python dcc_mapgen_gui.py          # ...or the desktop app
```

### Option B: standalone executable (Windows or Linux)

Check the [Releases](../../releases) page for bundled builds — no Python install required. Each platform ships two: `dcc_mapgen` (the command line) and `dcc_mapgen_gui` (the [desktop app](#desktop-app)). All are built automatically from this repo via GitHub Actions.

On Linux the desktop app needs your distribution's Tk package if it isn't already present (`sudo apt install python3-tk` on Debian/Ubuntu); the command-line build needs nothing.

## Desktop app

```
python dcc_mapgen_gui.py
```

![the desktop app showing a large Floor 1 map](sample/gui.png)

Floor, size and seed are always on the left; the options below them swap to match the
floor you picked, so you only ever see settings that affect the map in front of you.
**Generate** fills the *Maps* tab and the *Text* tab, and **Save output...** writes the
same `floorN_size_seedS.md` plus map files that `--save` produces on the command line.

- The map dropdown lists every map the floor produces — the overview plus one room map
  per neighborhood on floors 1–2, or the seven heats on floor 10.
- **Fit**, **1:1** and **+**/**−** zoom; each step re-renders from the SVG rather than
  scaling pixels, so text stays sharp however far you zoom in. Drag to pan, scroll to
  scroll, ctrl+scroll to zoom.
- The seed box is filled in with the seed that was used, so a map you like can be
  re-rolled or tweaked without hunting for it.
- On floors 1–2, the **stairwell chance** and **borough boss rate** sliders run 0–100%,
  in whole percents: a click on the track or an arrow key moves one percent. They are
  `--stair-chance` and `--borough-boss-rate`, which take the same thing as a fraction
  (50% is `0.5`).
- **File → Re-roll rooms from a saved floor** is `--rooms-for` in the GUI: point it at a
  floor 1–2 Markdown file this app wrote and re-roll one neighborhood's rooms.
- The **Loot Boxes** tab lists every box the current floor names; pick one, change its
  tier or type if you like, and open it. See [Loot boxes](#loot-boxes).
- The **Campaign** tab carries crawler teams between floors. It is where you change
  where a team stands with the party, add a group met at the table, and rewrite a
  group's crawler count, latest report or fate — alive or dead. See
  [Campaigns](#campaigns).
- The **Leaderboard** tab is the campaign's Top Ten from Floor 4, and everyone who
  has ever been on it. Rank anyone, take them off, set their multiplier, kill them, or
  add a crawler of your own. See [The leaderboard](#the-leaderboard).

The inline preview uses `resvg-py` (`pip install -r requirements.txt`). Without it the app
still generates and saves everything — maps just save as SVG, and **Open in browser**
shows the map instead of the built-in preview.

`python dcc_mapgen_gui.py --self-test` (or `python gui_smoke_test.py`) drives the app
through a generate and checks the write-up, the map list and the rendered preview all
arrive — this is what CI runs against the packaged builds.

## Campaigns

Every floor reports the other crawler teams sharing it. A campaign follows those
teams from floor to floor, so the group the party traded with on Floor 1 is still
out there on Floor 6 — or died on Floor 4 and is remembered for it.

```
python dcc_mapgen.py --floor 1 --size medium --campaign mycrawl.json
python dcc_mapgen.py --floor 2 --size medium --campaign mycrawl.json
```

The file is created on first use, so the same flag starts a campaign and continues
one. Every team still alive comes onto the next floor keeping its name, who they
were before the Collapse, and where it stands with the party; only its fate is
dealt again. A team whose fate comes up fatal leaves the floor but stays on the
roster, with the floor it died on.

The party also meets new groups on every floor, not only when deaths have left
room — 8.1 million crawlers enter Floor 1 and no party can know a fraction of them.
So a roster turns over rather than dwindling: it settles at about four teams on a
medium floor and eight on a large one, whatever the dice do.

Teams that have already lived through a floor or two are harder to kill than ones
the party has just met — around three times harder, once a team is four floors in.
Harder, not safe: a veteran still dies, and roughly half of full Floor 1-to-11 runs
end with one of the original groups still out there.

### Races and classes

Floor 3 is where the System makes every surviving crawler choose a race and a
class, so from Floor 3 down each team is listed with the crawlers in it:

```
- **Day 1 — Team Grayson Park** (*territorial*) — formed from a laundromat and the people waiting on the dryers; holed up out at the edge of the map, waiting out something they have no intention of fighting.
  - Four crawlers: a Harpy Rogue Trapmaster, a Changeling Former Child Actor, a Satyr Explosives Engineer, an Ashkin Hired Mercenary.
```

The race is a permanent transformation and is never rerolled — a Gnoll met on Floor
3 is still a Gnoll on Floor 11. The class can move, but only the way the System
moves it: `CRAWLER_CLASS_UPGRADE` in `data/common.json` maps the classes with a
documented level 6 upgrade onto what they become, and a team standing on Floor 6 or
deeper has taken it. A Pit Fighter met on Floor 4 is a Pit Champion by Floor 6.
About two teams in five field someone who upgraded.

The party itself grows and shrinks with what the floor's report says happened to
the group, so the two never contradict each other. A report can say so explicitly:
a status in `TEAM_STATUS` written as `[text, delta]` moves the headcount by that
much, and `[text, delta, "upgrade"]` marks one that claims a class upgrade and
makes sure somebody in the group can show it. Anything left as a plain string
falls back to what its outcome usually costs — a `costly` floor takes one crawler,
the other three outcomes leave the group as it was. A group is never thinned past
its last survivor; that is what `fatal` is for.

```json
"costly": [
  "loses their healer to a trapped loot box {where}",
  ["reduced to one survivor, still moving {where}", -6],
  ["burns every consumable they own getting clear {where}", 0]
]
```

The race bank (`CRAWLER_RACE`, 46 entries) and the class bank (`CRAWLER_CLASS`, 102)
are both in `data/common.json`. The classes are taken from the series' own list;
the races are anchored on the ones the books name and filled out with picks the
System would plausibly offer. Both are ordinary word banks — edit them.

### Crawler stats

Every floor's write-up ends with a **Crawler Stats** section: a one-page rules
reference for running the party and the teams the campaign is tracking. Five stats,
every effect given per point, no hidden modifier tables.

| | per point |
|---|---|
| **STR** | comfortable lift `STR x 60 lb`, max lift `STR x 75 lb`, +1 melee damage per 3 |
| **CON** | max health `CON x 15`, out-of-combat regeneration `CON x 2` per hour |
| **DEX** | +1 ranged/thrown per 3, +1 dodge per 4, initiative is the raw score |
| **INT** | mana *equals* INT, on a regeneration curve from 1/hour to effectively instant |
| **CHA** | -2% shop prices per point to a floor of -60%, one pet per 10 |

What comes from the books: the five stats, 3–5 as a typical starting range, 9–10 as
the natural human ceiling, mana being equal to Intelligence rather than a multiple
of it, and both ends of the mana regeneration curve (Intelligence 3 recovers one
point an hour, Intelligence 17 one a minute). Everything else is this project's own
arithmetic, sanity-checked against the anchors the books do give rather than derived
from them — Strength 30 works out at seven and a half average humans' worth of lift
here, against the books' "as strong as six ordinary humans", which is the right
neighbourhood and not a match.

All of it is tunable. `CRAWLER_STATS.knobs` in `data/common.json` holds every number
the section prints, the per-stat prose is formatted from that same dict, and the
at-a-glance table is computed from it — so changing `lift_comfortable` from 60 to 55
moves the rules and the table together, and [a test](#tests) fails if it ever moves
only one of them.

Crawlers gain **3 stat points per level**, as in the official RPG and the books,
banked until the race and class on Floor 3 and spent in a safe room after that.

The section also closes with a line on statting up the teams from Other Crawlers,
which is the point of putting it in the campaign material rather than in a separate
reference file: 4s across the board, plus 3 points per level, weighted toward
whatever the crawler's class is for. Put an ordinary group a little above the
floor's ordinary mobs, and the leaderboard ahead of that, where the books put
Carl: level 27 to 41 across the Fourth Floor, 41 to 47 across the Fifth.

### Bestiary

Every floor with mobs ends its own material with a **Bestiary**: each mob and boss
the floor names, statted from its level on the crawler rules, so a DM has numbers
as well as a level. Every stat starts at 4, then the points per level depend on what
the thing is:

| | points per level |
|---|---|
| a crawler | 3 |
| an ordinary mob | 1 |
| a neighborhood boss | 2 |
| a borough boss | 3 |
| a city boss | 4 |
| a county, state, country or continent boss | 5, 6, 7, 8 |
| Floor 11's floor boss | 9 |

So an ordinary mob at a crawler's level has a third of the crawler's points, and
it takes a city boss to out-point a crawler of its own level. What makes a mob
dangerous is its trick, which the generator already writes. The numbers are
there so the trick is what decides the fight, not the DM's guesswork.

- **The spread** follows the mob's type, worked out from its noun: brute,
  skirmisher, swarm, caster, undead, charmer, worker or beast. In a name the head
  noun decides, so Goblin Shamankas are casters and Tunnel Trolls brutes. Its
  adjective and gimmick lean the spread further: Bureaucratic and trap-setting
  mobs run to Intelligence, Corpulent ones to Constitution, ones that steal and
  flee to Dexterity.
- **About one mob in eight is poorly built**, as some in the books are: all
  Strength and no sense, a glass jaw, all Charisma for a fight that isn't this one,
  brilliant and helpless, or spread so thin it is good at nothing.
- **HP is CON × 15 and MP is INT**, as for a crawler. Few mobs cast spells, so MP is
  fuel for the mob's trick: 3 a use, coming back at a crawler's rate for the same
  Intelligence. What a mob simply is, such as exploding or regenerating, costs
  nothing.
- ***Even for*** is the crawler level with as many points to spend.

The Bestiary ends with **Bosses against a party**, a table of how many crawlers each
boss's stat points are worth at three levels:
- an ordinary crawler on that floor, at the median of the floor's ordinary mob band
  tops, so one late outlier like Floor 4's mimic spawn doesn't set it;
- a leaderboard crawler, from `MOB_BUILDS.party_levels`: Carl's level at the end of
  the floor's book, where Book 1 is Floors 1 and 2 and Book N is Floor N+1 after
  that (47 on Floor 5, 63 on Floor 6, 81 on Floor 8). Floors 9–11 have no book yet,
  so they carry on at his pace of about nine levels a floor;
- a crawler of the boss's own level.

Stat points don't care how a crawler fights: a caster's Intelligence counts the
same as a brawler's Strength. The table leaves out gear, class and the boss's
mechanic, and says so: deeper down, gear and abilities mean a party needs fewer
people, and the mechanic can mean it needs more. Where the boss kills a crawler of
that level in one hit, the cell says **one-shot** instead of a number. Then no
number of crawlers is enough: each one goes down before doing anything.

Last comes **You're going to die**, listing every mob and boss, hardest hit first,
with the level a crawler needs to live through one hit:
- **A full-strength hit** is the mob's STR × 5, or INT × 5 for a trick if that is
  bigger and it has the MP. That is a third of the 15 HP a point of Constitution
  gives, so a hit kills outright when the stat behind it is at least three times
  the crawler's CON. The multiplier is `MOB_BUILDS.hit_per_point`.
- **Two crawlers are measured:** one with their points spread evenly, and a
  veteran with half of them in Constitution.
- **The floor's cutoff** is the level its hardest-hitting ordinary mob stops
  one-shotting the evenly spread crawler. Below that, a crawler shouldn't be on the
  floor at all.

Ordinary mobs gain a point a level to a crawler's three, so an ordinary crawler
always clears their own floor's mobs. The cutoff is about being underleveled: 17 on
Floor 4 at medium seed 4242. Bosses are a different matter. That same Floor 4's
Station Mimic one-shots both an ordinary crawler and a leaderboard one. Floor 11's
level-134 floor boss hits for 2,710: it one-shots an evenly spread crawler below
level 295, and a veteran with half their points in Constitution below level 118.

Each build is seeded by the mob itself, so the same mob at the same level always
has the same stats, and adding the Bestiary moved nothing else on any floor. The
numbers live in `MOB_BUILDS` in `data/common.json`.

Standing is the part the party changes in play. Befriend the group that attacked
them on Floor 2, or fall out with an ally:

```
python dcc_mapgen.py --floor 3 --campaign mycrawl.json --standing "Thistle=allied"
```

A unique partial name is enough. The five standings that come with reaction text are
`hostile`, `territorial`, `wary`, `friendly` and `allied`, but any wording is
accepted — a table that decides a group is "owed a favour" can write that.

In the desktop app this is the **Campaign** tab: open or start a roster, and every
floor generated afterwards carries it. The table lists everyone met so far, the dead
greyed out with the floor that killed them, and standing is editable there.

Not every group the party meets comes out of the generator. **Add group...** adds one
met at the table — a name, who they were before the Collapse, how many crawlers are
in it, where they stand with the party, the floor they were met on, and their latest
report. From the next floor generated on, an added group is carried like any other:
dealt a fresh report each floor, picking races and classes on Floor 3, able to
thrive or die. What the generator never does is rename it or re-deal its standing,
which it does to every group it has just met itself. Added groups are marked
"(added)" in the table.

Any group's **crawler count**, **latest report** and **fate** — steady, thriving,
costly or fatal — can be rewritten from the same panel as its standing. Fatal kills
the group on the floor it was last seen, and it stops carrying; any other fate brings
a dead group back, which is how you undo a mistake or run a resurrection. The edit
corrects that floor's entry in the group's history rather than adding a second one,
and marks it as yours. Either way, the next floor generated deals every living group
a fresh report.

The crawler count works differently either side of Floor 3:

- **Floors 1–2.** Nobody has a race or class yet, so the count is only a number.
  The floor prints it under the group ("Five crawlers."), each report moves it the
  way it would move a party — a costly floor costs one — and Floor 3 deals exactly
  that many crawlers instead of rolling the group's size. A group the generator met
  has no number at all until Floor 3, unless you give it one.
- **Floor 3 and deeper.** The count is the crawlers. Raising it deals newcomers who
  share no race or class with anyone already there, as a merge does on a generated
  floor; lowering it drops the most recent to join first. A group added on Floor 3
  or deeper is dealt its races and classes as soon as you add it — with the level 6
  upgrades already taken past Floor 6 — and one added with the count left blank is
  sized by the generator on the next floor, like any other.

![the Campaign tab, five floors into a run](sample/gui_campaign.png)

Carrying a campaign never changes the floor itself. Teams are dealt from their own
random stream, so the same seed gives the same map and the same everything else
whether or not a campaign is open.

### The leaderboard

From Floor 4, the Other Crawlers section ends with the Dungeon Leaderboard: the Top
Ten by ratings, each with a bounty on their head. The bounty is the figure for the
rank times the crawler's multiplier:

| Rank | 1 | 2 | 3 | 4 | 5 | 6–10 |
| --- | --- | --- | --- | --- | --- | --- |
| Bounty | 1,000,000 | 500,000 | 400,000 | 300,000 | 200,000 | 100,000 |

The multiplier counts the floors a crawler has spent on the list: x1 on their first,
x2 on their second, and so on. It is remembered when they drop off. A crawler ranked
on Floors 4 and 5 who falls off on 6 and comes back on 7 is x3, not x1.

The first floor of a campaign from 4 down deals the list. A few places go to crawlers
from groups the party has met, a group having a good floor being likelier to put
someone there; the rest go to strangers with names from a new bank. A list first
dealt deeper than Floor 4 has been running since then, so its crawlers arrive with
multipliers to match.

After that the list is the DM's. A generated floor does only three things to it:

- everyone still on the list gets one more on their multiplier;
- anyone whose group the floor wiped out, or who is no longer in it, dies, and their
  bounty is claimed;
- the empty places at the bottom go to newcomers.

Nobody is moved up or down by the generator.

The campaign file keeps everyone who has ever been ranked: when they came onto the
list, fell off, came back or died, with the rank and multiplier they had at each.
The write-up for each floor after the first says what changed since the last one,
your own changes included.

The rules for killing a ranked crawler are printed under the table: the Bounty Box
for the killer, and the Venison Box for a crawler who reaches the next floor still
ranked. The leaderboard is what gives a Bounty Box its figure.

In the desktop app this is the **Leaderboard** tab. It lists everyone ever ranked:
the list first, then former crawlers, then the dead. For the selected crawler:

- **Rank:** give a former crawler a rank to bring them back with their multiplier,
  or clear it to take a crawler off. Everyone below a new rank moves down one, and
  tenth is pushed off.
- **Multiplier:** set it outright. Later floors still add one to it.
- **Killed...:** kills them and claims the bounty. A crawler from a group takes their
  place in that group with them; if they were its last crawler, the group dies too.
  **Bring back** undoes it.
- **Add crawler...:** puts someone new on the list, a stranger or one of the party's
  own. A crawler from a group has to be one of its crawlers, so you pick their race
  from the group and their class comes with them.

## Usage

### One example per floor

```
python dcc_mapgen.py --floor 1  --size medium --seed 42                                    # The Collapse: neighborhoods/boroughs/city, full room graphs
python dcc_mapgen.py --floor 2  --size large  --seed 42                                    # Same shape as Floor 1, tougher mob band, bosses roam
python dcc_mapgen.py --floor 3  --size medium --seed 42                                    # The Over City: settlements, quests, roaming bosses
python dcc_mapgen.py --floor 4  --size large  --seed 42                                    # The Iron Tangle: schematic rail/transit map
python dcc_mapgen.py --floor 5  --size medium --seed 42                                    # The Bubbles: four quadrants, castles, factions
python dcc_mapgen.py --floor 6  --size large  --seed 42                                    # The Hunting Grounds: jungle, hunter parties, bounty board
python dcc_mapgen.py --floor 8  --size medium --region "East Asia" --seed 42               # Ghosts of Earth: folklore polar map, totem cards
python dcc_mapgen.py --floor 9  --size large  --warlords generated --wildcard --seed 42    # Faction Wars: nine-slice pie around Larracos
python dcc_mapgen.py --floor 10 --size medium --vehicle biological --party 3 --seed 42     # Don't Come In Last: seven-heat race season
python dcc_mapgen.py --floor 11 --size large  --seed 42                                    # A Parade of Horribles: route, judges, arena boss
```

(Floor 7 doesn't exist, per canon.)

### More options

```
python dcc_mapgen.py --floor 1 --size small --days 5 --out floor1.md
python dcc_mapgen.py --floor 2 --size large --seed 9 --out f2.md --image f2.svg
python dcc_mapgen.py --floor 2 --size large --save maps/     # writes maps/floor2_large_seedNNN.md + .png + room maps
python dcc_mapgen.py --floor 1 --size massive --seed 1        # Country: 16 Cities, 1 Country Boss
python dcc_mapgen.py --floor 1 --size continent --seed 1       # Continent: 25 Cities, 1 Continent Boss
python dcc_mapgen.py --floor 1 --size large --stair-chance 0.5 --borough-boss-rate 0.5

# Add room-level detail to a neighborhood you already generated and are playing:
python dcc_mapgen.py --rooms-for my_floor1.md --rooms-for-label C --seed 42 --image roomC.png
```

Run `python dcc_mapgen.py --help` for the full flag list (per-floor options like `--region`, `--warlords`, `--vehicle`, `--party`, etc.).

## Tests

```
python -m unittest discover -s tests -v
```

Stdlib `unittest`, so nothing needs installing, and it finishes in a few seconds.
GitHub Actions runs it on every push across Linux and Windows.

`tests/golden.json` pins a hash of the Markdown and maps for every floor at every
size, plus the multi-city tiers and each floor's own options. Two things are checked:

- **Output hasn't drifted.** Any change to what the generator emits fails the golden
  test. When the change is intended, `python tests/update_golden.py` refreshes the
  hashes and prints which cases moved — commit that diff with the change, and it
  doubles as a record of what the change actually affected.
- **Seeds are genuinely reproducible.** The corpus is regenerated in separate
  interpreters under a spread of `PYTHONHASHSEED` values. Python salts string
  hashes per process, so anything that lets the iteration order of a set or dict
  reach the output produces a different map each run — the same seed, a different
  dungeon. Three such bugs had shipped in this project (corridor encounters, the
  Floor 9 stairwell angles, and the Floor 4 transfer connectors); within a single
  process all three were invisible, because that order is stable until the
  interpreter restarts.

A third group covers the floor 1–2 crawler roster, which is dealt rather than rolled
so a floor gets a spread of fates and dispositions: no roster repeats a name, origin
or status; a floor of more than one team is never all fatalities; the dispositions
span the hostile–allied scale; and a name like "What's Left of X" only lands on a
team that actually lost something.

A fourth covers the parties those teams field from Floor 3: nobody has a race or a
class before the floor that offers them, a race is never rerolled afterwards, the
level 6 upgrade is taken on Floor 6 and never walked back, and no party doubles up
on a race or a class. That last one goes wrong about twice in four thousand team
reports, so it is checked against two hundred campaigns dealt without their maps
rather than against the pinned corpus.

A fifth is about bosses not sounding alike, which the pinned hashes cannot see at
all: no floor repeats a boss name or recombines a first name or a title it has
already used, no one name shape accounts for more than half the bosses in a
montage, the banks are wide enough that no single first name or title lands on more
than 4% of them, and two floors generated from the same seed field no boss in
common. That last was a live bug: every generator started from `random.Random(seed)`,
so Floors 1 and 2 — which share a generator — described the same dungeon at the same
seed, down to all 21 boss names, and Floors 3 and 6 came back with a third of their
bosses in common. Each floor derives its own stream from the seed now. That check runs
against a control, because two floors overlapping on a 15-entry bank is arithmetic,
not a bug: the yardstick is how much a floor overlaps with *itself* at another seed.

A sixth is the same idea applied to what fills a floor: no floor repeats a loot theme,
a room feature or a quest while its bank still has unused entries, past that the
repeats are even rather than clumped, and the banks are checked against the cell and
quest counts of a large floor so the first guard cannot quietly stop meaning anything.
A quest title is also checked against its own objective, and Floor 6's Zockau is
pinned at zero quests, that being deliberate rather than an oversight.

The night-threat bank was 8 entries against 18 regions, which put the same line on
four of them, so it grew to 47 and is dealt from a deck like the rest.

An eighth covers loot boxes. On the floor side: every boss names its drop, box types
never turn up before the floor that introduces them, boss drops climb with tier and
depth and never reach Celestial, the floor rolls no box contents, every box it names
parses straight back into the roller, and opening a hundred boxes leaves a floor
byte-identical. On the box side: no effect sits on an item that cannot carry it, no
box holds anything but its tier's count — one item at its tier, the rest rolled
across exactly the lesser range the house rule gives, every tier in it coming up and
none outside it — lesser items split 15/35/50 between the main item's own kind, other
equipment and consumables, a Weapon box reaches another weapon only through the 15%,
each item carries its own tier's power, plain items follow the
band of the tier they rolled, gold is (1–10) × Floor ^ Tier
from Floor 2 and none on Floor 1, a Bronze Benefactor box beats a Gold Adventurer
box, Weapon boxes opened back to back repeat no weapon until the pool is spent, and
each pool has a floor stated in the test —
not read from the bank — so the banks cannot quietly shrink back. And every box
named anywhere, in any word bank or any floor's output, uses one of the six canon
tiers.

Benefactor and Fan boxes have their own groups. A prepared Benefactor box spends every
lesser item on a counter to its threat at the tier it rolled, its main item carries
the counter in place of an enchantment rather than beside one, and its sponsor's note
hints at the threat; no other box type takes a threat. A Fan box's odds are the
double vote, stated in the test and checked over two thousand rolls; 100% liked is
always good and 0% always bad; good is one useful item at the box's tier, neutral one
harmless thing, bad one cost from the box's band, and a bad box pays no gold. The
threat suggestions are checked against Floor 8 always reading undead, the shared
sections never counting, names and single stray words never counting, words of more
than one word still counting, every measured false signal staying out, and the table
in fifty-six written floors matching a fresh reading of each floor's own text.

Floor 4's station layout has its own group, with the rules written into the test:
- every line has stairwells at exactly 12, 24, 36, 48 and 72;
- a station has a safe room, Desperado Club or Club Vanquisher exactly when its
  number says so, both in the floor data and in the write-up;
- every floor has at least one safe room;
- every line has a safe room in each stretch a party uses, wherever that stretch
  has a prime, and a large floor has at least ten;
- a transfer to a line that isn't detailed names a real line color, says so in the
  write-up and is drawn on the map, and never shares its number with another
  line's own platform;
- a station two lines share reads the same from either;
- each line's withdrawal stages fall inside the countdown, down to a three-day floor;
- each ghoul station has a different ghoul, with 12 the janitors' and every
  withdrawal stage's dead at one of the others, and exactly one merged boss, never
  the janitors';
- every day a ghoul station names falls inside the countdown;
- 435 is on the map and in the text for every line;
- travel times meet the books' anchors, written into the test, and each line's
  printed ride, walk and exit cavern match the model;
- every prime transfer is a business with its own proprietor, named the same from
  each line it is on;
- each floor has the right number of hubs, never on a safe room while another
  transfer is free;
- every safe room and hub posts a quest, and every named train starts one, none of
  them the same on a floor;
- every quest and achievement box reaches the floor's box suggestions;
- at volume, a station number two lines share by chance is still two stations;
- the drug is named and supplied, 435's staff are non-combatants, the depot's cart
  times are the travel model's divided by five, every yard a line runs from is
  described, and no two ghoul stations share a sigil;
- none of the books' own Floor 4 creatures or events turns up in the write-up, and
  nothing says what opens the portals at the Abyss.

Floor 5's bosses have their own group, with the books' tiers written into the test:
- every castle ruler is a borough boss above its quadrant's mobs, except the one in
  the castle meant to fall last, a city boss at L78–84, so one to a bubble; no boss
  is on a tier of our own any more;
- guardians come none at the easiest castle and up to two at the hardest, each a
  borough boss between its quadrant's mobs and its ruler, written up as not taking
  the castle, in the Bestiary, dropping a box, and never sharing a ruler's first name;
- the rules say a popped bubble lets crawlers out but not into another bubble, and
  that its stairwells have no time limit;
- the quadrants' crawlers add up to the bubble's, unevenly, never under 15 to a
  quadrant or 2 to a site, even with the shares forced far wider than the bubble's;
- each quadrant has its number of sites, adding up to its crawlers: a faction's own
  settlement, named as it is, the quadrant mob's ground, or a ruin with a safe room,
  and a quadrant with no faction always has a ruin;
- every group in Other Crawlers is at a real quadrant and site, no two at one site
  while there are sites to spare;
- each faction posts its quota of quests, nine on a large bubble, no two from the
  same template, each in the write-up and the box suggestions;
- every name a quest fills in is really on the floor, checked by reading each quest
  back against its template: the castle's own ruler and quadrant, a rival that is
  never the giver's own faction, that rival's settlement, the quadrant's mob;
- the books' Fifth Floor achievements are listed with the books' rewards, and every
  boxed reward is a box the roller knows;
- both sections say they are suggested ideas, not a definitive list.

The leaderboard has its own group. The canon bounties are written into the test
rather than read from the bank, and checked against the table every floor prints:
nothing before Floor 4, a full Top Ten at x1 on Floor 4, and every bounty its rank's
figure times the multiplier. The multiplier is followed floor by floor — up one for
each floor on the list, not for a floor off it, and kept through a return. Also
checked:

- a ranked crawler from a group really is one of that group's crawlers, through the
  Floor 6 upgrades;
- nobody on the list shares a first name or surname, over two hundred lists;
- whoever a group loses — wiped out, cut down, or killed from the leaderboard —
  leaves the list dead, and the next floor refills it to ten;
- someone who fell off is never dealt back on under a new name;
- each refused edit leaves the campaign exactly as it was;
- the whole leaderboard survives a save and load.

The bestiary has its own group, with the points per level written into the test:
3 for a crawler, 1 for an ordinary mob, 2, 3 and 4 for neighborhood, borough and
city bosses. It checks:
- every build adds up exactly, and HP, MP, melee and *even for* follow from it;
- each type leads with the stats it should;
- a description leans the build;
- about one in eight is poorly built, with its dumped stats at 1;
- the head noun decides the type;
- a build never touches anything else's random;
- every floor with mobs lists every one of them, with the table matching the builds;
- every boss's party equivalents are its points over a crawler's (20 plus 3 a level),
  with the ordinary level set by the median band top, not one outlier, and
  "one-shot" exactly where its hit is at least that crawler's HP;
- the leaderboard crawler is Carl at the end of the floor's book, with the books'
  levels written into the test;
- a hit is the bigger of STR × 5 and a trick's INT × 5, and kills when it is at
  least the crawler's HP, a hit equal to their HP included;
- every floor lists every mob and boss hardest hit first, with the right level to
  live through each, and the floor's cutoff comes from its ordinary mobs, not its
  bosses;
- an ordinary crawler lives through every ordinary mob on their floor, and the
  Floor 11 boss kills an ordinary crawler there in one hit.

What's switched on and shops have their own groups, with the books' facts written into the tests rather than read back from the banks:
- each system arrives on the DCC Database's floor, and each floor's list of new, gone and still-on systems is checked against those floors;
- shops open on Floor 3, and a general store charges three times a specialist's price;
- a personal space costs 50,000 gold, and is not on sale until Floor 4;
- every list price is within reach of the gold in a box of its tier, and rises with tier and floor;
- every kind of loot has a price and a shop;
- Charisma reads the same as the stat rules;
- an opened box's sale value adds up its items, is nothing before Floor 3, and is nothing for a Fan box's joke.

A seventh group covers the stat rules, where the risk is drift rather than variety:
the section renders identically on every floor and seed, it is the last section of
every write-up, no `{placeholder}` survives into it, the prose and the at-a-glance
table are checked against each other value by value, and the handful of figures that
come from the books are pinned separately from the ones that are ours to retune.

If you add a floor, the suite fails until `tests/corpus.py` covers it.

## License

GPL-3.0 — see [LICENSE](LICENSE). The code is free to use, modify, and redistribute under those terms; the *Dungeon Crawler Carl* setting, names, and lore referenced by the word banks belong to their respective rights holders.
