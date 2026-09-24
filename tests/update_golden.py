#!/usr/bin/env python3
"""Refresh the pinned output hashes in tests/golden.json.

Run this when a change to the generator is *meant* to change its output, and
commit the result together with that change -- the diff is then a record of which
floors and sizes the change actually moved.

    python tests/update_golden.py

It prints what moved before writing, so an unexpected entry in that list is a
chance to stop and look rather than bless a regression.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import corpus  # noqa: E402


def main() -> int:
    fresh = corpus.fingerprint_all()
    try:
        old = corpus.load_golden()
    except FileNotFoundError:
        old = {}
        print(f"No golden file yet; creating {corpus.GOLDEN_PATH}")

    added = sorted(set(fresh) - set(old))
    removed = sorted(set(old) - set(fresh))
    changed = sorted(c for c in set(fresh) & set(old) if fresh[c] != old[c])

    for label, cases in (("added", added), ("removed", removed), ("changed", changed)):
        for case in cases:
            print(f"  {label:8s} {case}")

    if not (added or removed or changed):
        print("Golden file is already up to date; nothing written.")
        return 0

    corpus.save_golden(fresh)
    print(f"\nWrote {len(fresh)} cases to {corpus.GOLDEN_PATH} "
          f"({len(added)} added, {len(removed)} removed, {len(changed)} changed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
