#!/usr/bin/env python3
"""The set of generator outputs this project pins, and how to fingerprint them.

Both tests are built on this. test_output.py hashes the corpus in-process and
compares it against golden.json; it also runs this file as a subprocess twice
under different PYTHONHASHSEED values to catch output that depends on the
iteration order of a set or dict keyed by strings.

Run directly to print the fingerprints as JSON:

    python tests/corpus.py
"""

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dcc_mapgen as mg  # noqa: E402

# Every floor at each of its shared sizes, plus cases that reach code the plain
# sizes do not: the multi-city tiers with their top-tier boss, and each floor's
# own options. Keep this list cheap enough to run on every push -- the continent
# tier is deliberately left out, being 400 maps for no coverage the others miss.
CASES = [
    *[{"floor": f, "size": s, "seed": 4242}
      for f in mg.FLOORS for s in ("small", "medium", "large")],
    {"floor": 1, "size": "huge", "seed": 77},
    {"floor": 2, "size": "huge", "seed": 77},
    {"floor": 1, "size": "medium", "seed": 11, "scaling": "random"},
    {"floor": 1, "size": "large", "seed": 11, "boss_stairs": False,
     "stair_chance": 0.5, "borough_boss_rate": 0.5},
    # Floor 4 draws its transfer connectors by iterating a set of colour names, so
    # it only reveals hash-order bugs at a seed with stations on more than one
    # transfer. These two carry the most such stations found in the first 200
    # seeds; without them that whole class of bug is invisible on this floor.
    {"floor": 4, "size": "large", "seed": 31},
    {"floor": 4, "size": "medium", "seed": 17},
    {"floor": 8, "size": "medium", "seed": 11, "region": "East Asia"},
    {"floor": 9, "size": "large", "seed": 11, "warlords": "generated", "wildcard": True},
    {"floor": 10, "size": "medium", "seed": 11, "vehicle": "biological", "party": 4},
]

GOLDEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden.json")


def case_id(case: dict) -> str:
    """A stable name for a case, including whatever options make it distinct."""
    base = f"floor{case['floor']}_{case['size']}_seed{case['seed']}"
    extra = sorted(k for k in case if k not in ("floor", "size", "seed"))
    return base + "".join(f"_{k}={case[k]}" for k in extra)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fingerprint(case: dict) -> dict:
    """Hash one case's Markdown and maps.

    The maps hash covers the suffixes as well as the SVG source, so a map going
    missing, arriving in a different order or being renamed all show up.
    """
    res = mg.generate(**case)
    return {
        "seed": res.seed,
        "markdown": _sha(res.markdown),
        "map_count": len(res.svgs),
        "maps": _sha("\0".join(f"{suffix}\n{svg}" for suffix, svg in res.svgs)),
    }


def fingerprint_all() -> dict:
    return {case_id(c): fingerprint(c) for c in CASES}


def load_golden() -> dict:
    with open(GOLDEN_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_golden(data: dict) -> None:
    with open(GOLDEN_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    json.dump(fingerprint_all(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
