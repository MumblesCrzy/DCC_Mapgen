"""What the web page calls, running inside Pyodide in the visitor's browser.

A thin layer over dcc_mapgen: the page hands it JSON, it calls the same
generate() and LootRoller the desktop app does, and hands JSON back. Nothing is
generated anywhere but here, so a seed gives the same floor on a phone as in
the app -- web/check_pyodide.mjs holds that against tests/golden.json.
"""

import json

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
    return json.dumps({
        "seed": res.seed, "floor": res.number, "basename": res.basename(),
        "markdown": res.markdown,
        "size": res.size,
        # Each map with the write-up section that tells what it shows in words.
        "maps": [{"suffix": s, "title": mg.map_title(s), "svg": svg,
                  "section": mg.map_section(res.markdown, res.number, s)}
                 for s, svg in res.svgs],
        "suggestions": _suggestions(res),
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
