"""What the web page calls, running inside Pyodide in the visitor's browser.

A thin layer over dcc_mapgen: the page hands it JSON, it calls the same
generate() and LootRoller the desktop app does, and hands JSON back. Nothing is
generated anywhere but here, so a seed gives the same floor on a phone as in
the app -- web/check_pyodide.mjs holds that against tests/golden.json.
"""

import copy
import json
import os
import re
import tempfile

import dcc_mapgen as mg

# One roller for the visit, as the desktop app keeps one for a session: boxes
# opened one after another work through each bank before repeating an item.
_roller = mg.LootRoller()

# The options the page offers, by the names FLOOR_OPTIONS groups them under:
# all of the desktop app's.
WEB_OPTIONS = ("days", "scaling", "region", "stairs", "floor9", "floor10")


def meta() -> str:
    """Everything the page needs to draw its controls."""
    floors = []
    for f in mg.FLOORS:
        sizes = (list(mg.SIZE_CFG) if f in (1, 2)
                 else [s for s in mg.SIZE_CFG if s not in mg.BIG_SIZES])
        floors.append({"number": f, "label": mg.FLOOR_LABELS[f], "sizes": sizes,
                       "options": sorted(set(WEB_OPTIONS) & mg.FLOOR_OPTIONS[f])})
    types = [t["name"] for t in mg.LOOT_BOXES["types"] + mg.LOOT_BOXES["themed_types"]]
    return json.dumps({"floors": floors, "regions": list(mg.FOLKLORE),
                       "tiers": list(mg.LOOT_BOX_TIERS), "types": types,
                       "threats": mg.benefactor_threats(), "roller_seed": _roller.seed,
                       # Floor 9's factions besides the crawler team: a projected
                       # winner or runner-up counts only if it names one.
                       "factions9": [t["name"] for t in mg.TEAMS if not t.get("carl")],
                       # The Campaign and Leaderboard tabs' lists.
                       "standings": mg.standing_labels(), "outcomes": mg.team_outcomes(),
                       "races": list(mg.CRAWLER_RACE), "classes": list(mg.CRAWLER_CLASS),
                       "board_size": mg.BOARD_SIZE})


def _whole(raw, name: str, lo=None, hi=None):
    """A whole number as typed, or None left blank; ValueError says what's wrong."""
    raw = str(raw if raw is not None else "").strip()
    if not raw:
        return None
    try:
        n = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number (got {raw!r}).") from None
    if (lo is not None and n < lo) or (hi is not None and n > hi):
        raise ValueError(f"{name} must be from {lo} to {hi} (got {n}).")
    return n


def _kwargs(o: dict) -> dict:
    """The page's controls as generate() arguments, as the desktop app reads its
    panel: only the options the floor has are passed."""
    floor = int(o["floor"])
    kw = {"floor": floor, "size": o.get("size", "medium")}
    seed = _whole(o.get("seed"), "Seed")
    if seed is not None:
        kw["seed"] = seed
    allowed = mg.FLOOR_OPTIONS[floor]
    if "days" in allowed:
        days = _whole(o.get("days"), "Days")
        if days is not None:
            kw["days"] = days
    if "scaling" in allowed and o.get("scaling"):
        kw["scaling"] = o["scaling"]
    if "region" in allowed and o.get("region"):
        kw["region"] = o["region"]
    if "stairs" in allowed:
        # Held as whole percents, as the desktop app's sliders are; generate()
        # takes the fraction.
        chance = _whole(o.get("stair_chance", 100), "Neighborhood stairwell chance", 0, 100)
        rate = _whole(o.get("borough_rate", 100), "Borough boss rate", 0, 100)
        kw.update(boss_stairs=bool(o.get("boss_stairs", True)),
                  stair_chance=(100 if chance is None else chance) / 100,
                  borough_boss_rate=(100 if rate is None else rate) / 100)
    if "floor9" in allowed:
        kw.update(warlords=o.get("warlords") or "canon", wildcard=bool(o.get("wildcard")),
                  winner=str(o.get("winner") or "").strip() or None,
                  runner_up=str(o.get("runner_up") or "").strip() or None)
    if "floor10" in allowed:
        party = _whole(o.get("party"), "Party size", 2, 4)
        kw["party"] = 2 if party is None else party
        if o.get("vehicle"):
            kw["vehicle"] = o["vehicle"]
    return kw


def generate(options: str) -> str:
    """Build a floor from the page's controls. Returns the write-up, every map
    with its title, and the boxes the floor suggests; or an error to show. With a
    campaign open, its groups carry onto the floor, and the campaign comes back
    as the floor left it."""
    global _roster
    try:
        kw = _kwargs(json.loads(options))
    except ValueError as e:
        return json.dumps({"error": str(e)})
    if _roster is not None:
        # A copy: a floor that can't be made leaves the campaign as it was.
        kw["roster"] = copy.deepcopy(_roster)
    try:
        res = mg.generate(**kw)
    except mg.MapgenError as e:
        return json.dumps({"error": str(e)})
    _undo.clear()                 # a new floor: nothing of the last one's to undo
    out = _show(res)
    if _roster is not None:
        _roster = res.roster      # this floor's outcome, carried to the next
        out["campaign"] = _campaign()
        out["carried"] = sum(1 for t in res.floor.other_crawlers if t.first_seen != t.floor)
    return json.dumps(out)


# The floor on show, for Save floor file. Rooms re-rolled on their own are not
# a floor, and leave it empty.
_shown = None


def _show(res, **more) -> dict:
    """What the page shows of a floor: its write-up, every map, the boxes it
    names. Kept as the floor on show."""
    global _shown
    _shown = res
    return {
        "kind": "floor", "seed": res.seed, "floor": res.number, "basename": res.basename(),
        "markdown": res.markdown,
        "size": res.size,
        # Each map with the write-up section that tells what it shows in words.
        "maps": [{"suffix": s, "title": mg.map_title(s), "svg": svg,
                  "section": mg.map_section(res.markdown, res.number, s)}
                 for s, svg in res.svgs],
        "suggestions": _suggestions(res),
        **more,
    }


def save_floor(_: str = "") -> str:
    """The floor on show as a floor file: everything its write-up and maps are
    drawn from, to edit by hand and open again. The same file the desktop app
    and --save write."""
    if _shown is None:
        return json.dumps({"error": "Generate or open a floor to save it as a floor file."})
    return json.dumps({"name": _shown.basename() + mg.FLOOR_FILE_SUFFIX,
                       "text": mg._floor_json(mg.floor_file_data(_shown)) + "\n"})


def open_floor(text: str) -> str:
    """Draw a floor file: the write-up and every map, edits and all."""
    try:
        data = json.loads(text)
    except ValueError as e:
        return json.dumps({"error": f"That file isn't valid JSON ({e}). A hand edit may have left "
                                    "a comma or a quote out."})
    try:
        res = mg.render_floor_data(data, "That file")
    except mg.MapgenError as e:
        return json.dumps({"error": str(e)})
    _undo.clear()                 # a new floor: nothing of the last one's to undo
    return json.dumps(_show(res))


def reroll(options: str) -> str:
    """New rooms for one neighborhood of a Floor 1 or 2 write-up this app
    saved, as File > Re-roll rooms does in the desktop app: they keep its
    crossings and its ways into boss chambers. The floor and its size come
    from the write-up's title."""
    global _shown
    o = json.loads(options)
    text = str(o.get("text") or "")
    head = re.search(r"^# Floor (\d+) — .*\((\w+)\)\s*$", text, re.M)
    floor, size = (int(head.group(1)), head.group(2)) if head else (1, "medium")
    if floor not in (1, 2):
        return json.dumps({"error": f"Rooms are re-rolled on Floors 1 and 2; that write-up is "
                                    f"Floor {floor}."})
    if size not in mg.SIZE_CFG:
        size = "medium"
    raw = str(o.get("seed") or "").strip()
    try:
        seed = int(raw) if raw else None
    except ValueError:
        return json.dumps({"error": f"Seed must be a whole number (got {raw!r})."})
    # Read back as the desktop app reads a file: from a file, here in the
    # browser's own temporary folder.
    path = os.path.join(tempfile.gettempdir(), "dcc_mapgen_reroll.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        nb, res = mg.regenerate_rooms(path, str(o.get("label") or "").strip() or None, floor,
                                      size=size, seed=seed)
    except (mg.MapgenError, SystemExit) as e:
        # Reading a write-up back reports what it couldn't find by exiting.
        return json.dumps({"error": str(e).replace("--rooms-for: ", "").replace("the input file", "that write-up").replace("in the input file", "in that write-up")})
    _shown = None
    _undo.clear()
    moved = res.markdown.count("\n  - The ")
    return json.dumps({
        "kind": "rooms", "seed": res.seed, "floor": floor, "size": size,
        "label": nb.label, "name": nb.name, "moved": moved,
        "ways": len(nb.crossings) + len(nb.chamber_ways),
        "basename": f"floor{floor}_rooms_{nb.label}_seed{res.seed}",
        "markdown": res.markdown,
        "maps": [{"suffix": s, "title": mg.map_title(s), "svg": svg,
                  "section": mg.map_section(res.markdown, floor, s)} for s, svg in res.svgs],
        "suggestions": [],
    })


def _suggestions(res) -> list:
    """What the floor names, as the desktop app's Loot Boxes tab lists it: first
    the threats a sponsor would prepare a Benefactor box for, then every box."""
    benefactor_from = next(t for t in mg.LOOT_BOXES["types"]
                           if t["name"] == "Benefactor")["from_floor"]
    out = []
    if res.number >= benefactor_from:
        for t in mg.threat_suggestions(mg.floor_body(res.markdown)):
            out.append({"kind": "threat", "threat": t.threat, "type": "Benefactor",
                        "text": f"Benefactor prepared for {t.label}",
                        "note": f"{t.count} signs on this floor"})
    for sg in mg.floor_box_suggestions(res.floor):
        out.append({"kind": "box", "tier": sg.tier, "type": sg.box_type,
                    "text": sg.label, "note": f"{sg.kind}: {sg.source}"})
    return out


# -- editing ------------------------------------------------------------------
# The page's Edit tab, as the desktop app's edit panel: the floor's parts, read
# from its data, and their values, changed and drawn again. A value that holds
# the floor together is shown but locked; renaming something offers to change
# it wherever else it is written. Undo steps back through every change.

_undo = []                # snapshots of the floor on show, before each change
UNDO_LIMIT = 50


def _editable():
    return _shown.floor if _shown is not None and mg.is_dataclass(_shown.floor) else None


def _group(path):
    """The list a row is in, which says which of its cells are links."""
    return path[-2] if len(path) >= 2 and isinstance(path[-1], int) else None


def edit_part(path_json: str) -> str:
    """One part of the floor on show: the way back to the floor from it, the
    parts it holds, and its values."""
    fl = _editable()
    if fl is None:
        return json.dumps({"error": "Generate or open a floor to edit it. Re-rolled rooms "
                                    "aren't a whole floor."})
    path = json.loads(path_json or "[]")
    try:
        node = mg.edit_get(fl, path)
    except (KeyError, IndexError, AttributeError, TypeError):
        path, node = [], fl          # gone, after an undo: back to the floor
    trail = [{"path": [], "label": _shown.markdown.splitlines()[0].lstrip("# ")}]
    for i in range(1, len(path) + 1):
        sub = mg.edit_get(fl, path[:i])
        trail.append({"path": path[:i], "label": mg._human(path[i - 1]) if mg._scalar(sub)
                      else mg.edit_label(sub, path[i - 1])})
    return json.dumps({
        "path": path, "trail": trail, "undo": len(_undo),
        "children": [{"path": path + [k], "label": label} for k, label in mg.edit_children(node)],
        "fields": [{"key": f.key, "label": f.label, "kind": f.kind, "locked": f.locked,
                    "optional": f.optional,
                    "value": list(f.value) if isinstance(f.value, tuple) else f.value}
                   for f in mg.edit_fields(node, _group(path))],
    })


def edit_apply(payload: str) -> str:
    """Change a part's values: {"path", "values": [[key, as typed], ...],
    "rename": null or [yes/no, ...]}. When a changed name is written elsewhere
    on the floor and "rename" is null, nothing changes yet: the answer is
    "ask", with where, for the page to ask whether to change it there too."""
    global _shown
    fl = _editable()
    if fl is None:
        return json.dumps({"error": "There's no floor to edit."})
    o = json.loads(payload)
    path = o.get("path") or []
    typed = {json.dumps(k): v for k, v in o.get("values") or []}
    try:
        node = mg.edit_get(fl, path)
        changes = []
        for f in mg.edit_fields(node, _group(path)):
            if f.locked or json.dumps(f.key) not in typed:
                continue
            new = mg.parse_edit(f, typed[json.dumps(f.key)])
            if new != f.value:
                changes.append((f, new))
    except mg.MapgenError as e:
        return json.dumps({"error": str(e)})
    except (KeyError, IndexError, AttributeError, TypeError):
        return json.dumps({"error": "That part of the floor isn't there any more."})
    if not changes:
        return json.dumps({"nothing": True})
    also = mg.edit_mentions(fl, path, changes)
    answers = o.get("rename")
    if also and answers is None:
        return json.dumps({"ask": [{"old": old, "new": new, "count": len(where),
                                    "where": [mg.describe_path(fl, w) for w in where[:6]]}
                                   for old, new, where in also]})
    snapshot = mg.snapshot_floor(fl)
    for f, new in changes:
        mg.edit_set(fl, path + [f.key], new)
    extra = sum(mg.replace_text(fl, old, new, where)
                for (old, new, where), yes in zip(also, answers or []) if yes)
    try:
        res = mg.redraw_floor(_shown)
    except mg.MapgenError as e:
        _shown = mg.replace(_shown, floor=mg.restore_floor(snapshot))
        return json.dumps({"error": f"That change can't be drawn: {e}"})
    _undo.append(snapshot)
    del _undo[:-UNDO_LIMIT]
    names = ", ".join(f.label for f, _ in changes)
    status = (f"Changed {names}" + (f", and {extra} other mention{'s' if extra != 1 else ''}"
                                    if extra else "") + ".")
    return json.dumps(_show(res, status=status, undo=len(_undo)))


def edit_undo(_: str = "") -> str:
    """Take back the last change."""
    global _shown
    if not _undo or _editable() is None:
        return json.dumps({"error": "Nothing to undo."})
    _shown = mg.replace(_shown, floor=mg.restore_floor(_undo.pop()))
    return json.dumps(_show(mg.redraw_floor(_shown), status="Undone.", undo=len(_undo)))


def map_parts(suffix: str) -> str:
    """Where one map of the floor on show draws each of its parts, in the order
    drawn, for a tap to find the one on top: its path, shape and box, what the
    editor calls it, and the map that shows it up close (a neighborhood's rooms).
    None for re-rolled rooms, which aren't a floor to edit."""
    fl = _editable()
    if fl is None:
        return json.dumps([])
    names, out = {}, []
    for h in mg.map_hits(fl, suffix, _shown.boss_stairs):
        if h.path not in names:
            words = mg.describe_path(fl, list(h.path)).split(" › ")
            # A row says little on its own ("1. A1 · A4"): say what it is a row of.
            row = isinstance(mg.edit_get(fl, list(h.path)), (list, tuple))
            names[h.path] = (" › ".join(words[-2:]) if row else words[-1], mg.part_map(fl, h.path))
        name, closer = names[h.path]
        out.append({"path": list(h.path), "shape": h.shape, "box": list(h.box),
                    "name": name, "closer": closer})
    return json.dumps(out)


def open_boxes(options: str) -> str:
    """Open boxes on the visit's roller. Returns their write-up in Markdown."""
    o = json.loads(options)
    kind = str(o.get("type") or "").strip()
    if not kind:
        return json.dumps({"error": "Give the box a type, such as Boss or Weapon."})
    try:
        floor = int(o.get("floor") or 1)
        count = max(1, min(20, int(o.get("count") or 1)))
        liked = float(o.get("liked", 50))
    except ValueError:
        return json.dumps({"error": "Floor, how many and % liked need to be numbers."})
    threat = o.get("threat") or None
    try:
        boxes = [_roller.open(o.get("tier", "Silver"), kind, floor, threat=threat, liked=liked)
                 for _ in range(count)]
    except mg.MapgenError as e:
        return json.dumps({"error": str(e)})
    return json.dumps({"markdown": "\n".join(b.markdown() for b in boxes),
                       "label": boxes[0].label, "count": count, "floor": floor})


def fan_odds(liked: str) -> str:
    """What a Fan box's % liked works out to."""
    try:
        odds = mg.fan_odds(float(liked))
    except (ValueError, mg.MapgenError):
        return json.dumps({"error": "0 to 100"})
    return json.dumps({k: odds[k] for k in ("good", "neutral", "bad")})


# -- the campaign -------------------------------------------------------------
# The desktop app's Campaign and Leaderboard tabs. There the campaign is a file
# saved after every change; here the page keeps its text in the browser, and
# Save campaign file downloads the same file the desktop app reads and writes.

_roster = None                # the campaign open, if any
_campaign_name = ""           # the name of its file, to save it under again


def _roster_text() -> str:
    """The campaign file, exactly as the desktop app's save_roster writes it."""
    return json.dumps(_roster.to_dict(), indent=2, ensure_ascii=False) + "\n"


def _team_row(t) -> dict:
    fate = t.status or ("your own group" if mg.is_us(t) else "not yet reported")
    note = "" if t.alive else f" (died on floor {t.died_on})"
    added = " (added by the DM)" if t.added else ""
    about = f"{t.name} — formed from {t.origin}{note}{added}"
    return {"name": t.name, "shown": f"{t.name} (added)" if t.added else t.name,
            "standing": t.disposition, "crawlers": t.crawler_count or None,
            "met": t.first_seen, "seen": t.floor if t.history else None,
            "fate": fate if t.alive else f"died on floor {t.died_on} — {fate}",
            "alive": t.alive, "outcome": t.outcome, "report": t.status,
            "about": about, "party": mg.party_line(t) if t.crawler_count else ""}


def _ranked_row(c) -> dict:
    claimed = f"Their bounty of {c.bounty:,} gold is claimed" if c.rank else "Nobody claims a bounty"
    group = f", and {c.team} loses them" if c.team else ""
    status = (f"rank {c.rank}, bounty {c.bounty:,} gold" if c.rank else
              "off the list" if c.alive else f"died on floor {c.died_on}")
    return {"name": c.name, "shown": c.name + (" (added)" if c.added else ""), "rank": c.rank,
            "alive": c.alive, "multiplier": c.multiplier,
            "bounty": f"{c.bounty:,}" if c.rank else None,
            "label": mg.ranked_label(c), "story": mg.ranked_story(c), "status": status,
            "kill": f"Kill {c.name}? {claimed}{group}."}


def _campaign(**more) -> dict:
    """The campaign as the page shows it, and its file's text for the page to keep."""
    if _roster is None:
        return {"open": False,
                "board_info": "Open or start a campaign on the Campaign tab: the leaderboard "
                              "is kept in it.", **more}
    floor = mg.board_floor(_roster)
    if floor is None:
        info = (f"No leaderboard yet. It starts on Floor {mg.LEADERBOARD['first_floor']}; "
                f"generate that floor or deeper in this campaign.")
    else:
        info = f"As of floor {floor}. Changes here count towards floor {floor}."
    # The list first, then former crawlers, then the dead.
    ranked = sorted(_roster.leaderboard,
                    key=lambda c: (0 if c.rank else 1 if c.alive else 2, c.rank or 0, c.name))
    # Who a new leaderboard crawler can come from: a group with crawlers to be
    # one of, or the players' own, who pick their own races and classes.
    groups = [{"name": t.name, "us": mg.is_us(t),
               "members": [{"race": m["race"], "class": m["class"]} for m in t.members]}
              for t in _roster.teams if t.alive and (t.members or mg.is_us(t))]
    deepest = max((t.floor for t in _roster.teams), default=None)
    return {"open": True, "name": _campaign_name, "text": _roster_text(),
            "teams": [_team_row(t) for t in _roster.teams],
            "alive": len(_roster.active()), "met": len(_roster.teams),
            "board": [_ranked_row(c) for c in ranked], "board_floor": floor, "board_info": info,
            "groups": groups, "met_floor": deepest or (_shown.number if _shown else 1), **more}


def _answer(**more) -> str:
    return json.dumps(_campaign(**more))


def campaign_new(name: str = "") -> str:
    """Start an empty campaign: the next floor generated deals its first groups."""
    global _roster, _campaign_name
    _roster, _campaign_name = mg.CampaignRoster(), (name or "campaign.json")
    return _answer(status=f"Started {_campaign_name}. Floors you generate now carry its groups "
                          f"from one to the next.")


def campaign_open(payload: str) -> str:
    """Open a campaign file: {"text", "name"}. The same file the desktop app keeps."""
    global _roster, _campaign_name
    o = json.loads(payload)
    try:
        raw = json.loads(o.get("text") or "")
    except ValueError as e:
        return json.dumps({"error": f"That file isn't valid JSON ({e})."})
    if not isinstance(raw, dict) or not isinstance(raw.get("teams", []), list):
        return json.dumps({"error": "That isn't a DCC Mapgen campaign file."})
    try:
        roster = mg.CampaignRoster.from_dict(raw)
    except (mg.MapgenError, TypeError, AttributeError) as e:
        return json.dumps({"error": f"That campaign can't be read: {e}"})
    _roster, _campaign_name = roster, (o.get("name") or "campaign.json")
    return _answer(status=f"Campaign {_campaign_name}: {len(roster.active())} group(s) alive "
                          f"of {len(roster.teams)} met.")


def campaign_close(_: str = "") -> str:
    """Stop carrying groups forward."""
    global _roster, _campaign_name
    _roster, _campaign_name = None, ""
    return _answer(status="Campaign closed; floors generate standalone again.")


def campaign_state(_: str = "") -> str:
    return _answer()


def _needs_campaign():
    if _roster is None:
        return json.dumps({"error": "Open or start a campaign first; that is where it's kept."})
    return None


def team_save(payload: str) -> str:
    """Change a group: {"name", "standing", "crawlers", "outcome", "report"}, as
    the desktop app's Save under Selected group does."""
    if (no := _needs_campaign()):
        return no
    o = json.loads(payload)
    team = next((t for t in _roster.teams if t.name == o.get("name")), None)
    standing = str(o.get("standing") or "").strip()
    if team is None:
        return json.dumps({"error": "That group isn't in the campaign any more."})
    if not standing:
        return json.dumps({"error": "Give the group a standing, such as wary or friendly."})
    try:
        crawlers = _whole(o.get("crawlers"), "Crawlers", 1)
    except ValueError:
        return json.dumps({"error": "Crawlers needs to be a whole number, or blank if nobody "
                                    "has counted them."})
    was_alive, count_was = team.alive, team.crawler_count
    try:
        mg.set_status(_roster, team.name, status=str(o.get("report") or ""),
                      outcome=o.get("outcome") or team.outcome, crawlers=crawlers)
    except mg.MapgenError as e:
        return json.dumps({"error": str(e)})
    team.disposition = standing
    if was_alive and not team.alive:
        msg = f"{team.name} is dead, as of floor {team.died_on}"
    elif not was_alive and team.alive:
        msg = f"{team.name} is back, and carries onto the next floor"
    else:
        msg = mg.standing_note(team)
    n = team.crawler_count
    if n != count_was:
        msg += f"; {n} crawler{'' if n == 1 else 's'}"
    return _answer(status=msg + ".", selected=team.name)


def team_add(payload: str) -> str:
    """Add a group met at the table: {"name", "origin", "crawlers", "standing",
    "floor", "status", "outcome"}, as the desktop app's Add group does."""
    if (no := _needs_campaign()):
        return no
    o = json.loads(payload)
    try:
        floor = _whole(o.get("floor"), "Met on floor", 1) or _campaign()["met_floor"]
        crawlers = _whole(o.get("crawlers"), "Crawlers", 1)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    try:
        team = mg.add_team(_roster, str(o.get("name") or ""), origin=str(o.get("origin") or ""),
                           standing=str(o.get("standing") or "").strip() or "wary", floor=floor,
                           status=str(o.get("status") or ""),
                           outcome=o.get("outcome") or "steady", crawlers=crawlers)
    except mg.MapgenError as e:
        return json.dumps({"error": str(e)})
    return _answer(status=f"Added {team.name}; it carries onto the next floor you generate.",
                   selected=team.name)


def _ranked(name):
    return next((c for c in _roster.leaderboard if c.name == name), None)


def board_save(payload: str) -> str:
    """A leaderboard crawler's rank (blank: off the list) and multiplier."""
    if (no := _needs_campaign()):
        return no
    o = json.loads(payload)
    c = _ranked(o.get("name"))
    if c is None:
        return json.dumps({"error": "That crawler isn't in the campaign any more."})
    try:
        rank = _whole(o.get("rank"), "Rank")
        mult = _whole(o.get("multiplier"), "Multiplier")
    except ValueError:
        return json.dumps({"error": "Rank and multiplier are whole numbers; leave the rank "
                                    "blank for off the list."})
    mult = c.multiplier if mult is None else mult
    try:
        if rank is None and c.rank is not None:
            mg.drop_from_board(_roster, c.name)
        elif rank is not None and rank != c.rank:
            mg.place_on_board(_roster, c.name, rank)
        if mult != c.multiplier:
            mg.set_multiplier(_roster, c.name, mult)
    except mg.MapgenError as e:
        return _answer(error=str(e))
    msg = (f"{c.name}: rank {c.rank}, x{c.multiplier}, bounty {c.bounty:,} gold" if c.rank
           else f"{c.name}: off the list, x{c.multiplier} remembered")
    return _answer(status=msg + ".", selected=c.name)


def board_kill(name: str) -> str:
    if (no := _needs_campaign()):
        return no
    c = _ranked(name)
    if c is None:
        return json.dumps({"error": "That crawler isn't in the campaign any more."})
    try:
        mg.drop_from_board(_roster, c.name, died=True)
    except mg.MapgenError as e:
        return json.dumps({"error": str(e)})
    return _answer(status=f"{c.name} is dead, on floor {c.died_on}.", selected=c.name)


def board_revive(name: str) -> str:
    if (no := _needs_campaign()):
        return no
    c = _ranked(name)
    if c is None:
        return json.dumps({"error": "That crawler isn't in the campaign any more."})
    try:
        mg.revive_ranked(_roster, c.name)
    except mg.MapgenError as e:
        return json.dumps({"error": str(e)})
    return _answer(status=f"{c.name} is alive again, off the list until you rank them.",
                   selected=c.name)


def board_add(payload: str) -> str:
    """Put a crawler on the list: {"name", "team", "race", "class", "rank",
    "multiplier"}, as the desktop app's Add crawler does."""
    if (no := _needs_campaign()):
        return no
    if mg.board_floor(_roster) is None:
        return json.dumps({"error": "There's no leaderboard yet."})
    o = json.loads(payload)
    try:
        rank = _whole(o.get("rank"), "Rank") or mg.BOARD_SIZE
        mult = _whole(o.get("multiplier"), "Multiplier") or 1
    except ValueError:
        return json.dumps({"error": "Rank and multiplier are whole numbers."})
    try:
        c = mg.place_on_board(_roster, str(o.get("name") or ""), rank,
                              race=str(o.get("race") or ""), klass=str(o.get("class") or ""),
                              team=str(o.get("team") or ""), multiplier=mult)
    except mg.MapgenError as e:
        return json.dumps({"error": str(e)})
    return _answer(status=f"{c.name} is on the list at rank {c.rank}, bounty {c.bounty:,} gold.",
                   selected=c.name)
