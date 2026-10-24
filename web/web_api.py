"""What the web page calls, running inside Pyodide in the visitor's browser.

A thin layer over dcc_mapgen: the page hands it JSON, it calls the same
generate() and LootRoller the desktop app does, and hands JSON back. Nothing is
generated anywhere but here, so a seed gives the same floor on a phone as in
the app -- web/check_pyodide.mjs holds that against tests/golden.json.
"""

import json
import os
import re
import tempfile

import dcc_mapgen as mg

# One roller for the visit, as the desktop app keeps one for a session: boxes
# opened one after another work through each bank before repeating an item.
_roller = mg.LootRoller()

# Options the mobile page offers. The desktop app has more (stairwell and boss
# sliders, the Floor 9 and 10 controls); the page leaves those at their defaults.
WEB_OPTIONS = ("days", "scaling", "region")


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
                       "threats": mg.benefactor_threats(), "roller_seed": _roller.seed})


def generate(options: str) -> str:
    """Build a floor from the page's controls. Returns the write-up, every map
    with its title, and the boxes the floor suggests; or an error to show."""
    o = json.loads(options)
    floor = int(o["floor"])
    kw = {"floor": floor, "size": o.get("size", "medium")}
    for key in ("seed", "days"):
        raw = str(o.get(key) or "").strip()
        if raw:
            try:
                kw[key] = int(raw)
            except ValueError:
                return json.dumps({"error": f"{key.capitalize()} must be a whole number "
                                            f"(got {raw!r})."})
    allowed = mg.FLOOR_OPTIONS[floor]
    if "days" not in allowed:
        kw.pop("days", None)
    if "scaling" in allowed and o.get("scaling"):
        kw["scaling"] = o["scaling"]
    if "region" in allowed and o.get("region"):
        kw["region"] = o["region"]
    try:
        res = mg.generate(**kw)
    except mg.MapgenError as e:
        return json.dumps({"error": str(e)})
    return json.dumps(_show(res))


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
