#!/usr/bin/env python3
"""Regression tests for generator output.

Written on unittest so they run with no install at all:

    python -m unittest discover -s tests -v

pytest discovers them too, if you prefer it.

Two things are checked. The golden test pins a hash of every case's Markdown and
maps, so any change to generated output has to be an intentional one that comes
with a golden update. The determinism test re-runs the whole corpus in a second
interpreter under a different PYTHONHASHSEED, which is what catches output that
depends on the iteration order of a set or dict keyed by strings -- three such
bugs have shipped in this project, and none of them were visible from a single
run, because within one process that order is perfectly stable.
"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import corpus  # noqa: E402
import dcc_mapgen as mg  # noqa: E402

FIELDS = ("seed", "markdown", "map_count", "maps")


def _compare(left: dict, right: dict, left_name: str, right_name: str) -> list:
    """Human-readable differences between two fingerprint sets."""
    problems = []
    for case in sorted(set(left) | set(right)):
        if case not in left:
            problems.append(f"{case}: missing from {left_name}")
            continue
        if case not in right:
            problems.append(f"{case}: missing from {right_name}")
            continue
        for field in FIELDS:
            a, b = left[case].get(field), right[case].get(field)
            if a != b:
                problems.append(f"{case}: {field} {left_name}={a} {right_name}={b}")
    return problems


class GoldenOutputTest(unittest.TestCase):
    def test_output_matches_golden(self):
        """Generated output is unchanged from the pinned hashes.

        A failure here is not automatically a bug -- if the change was intended,
        run `python tests/update_golden.py` and commit the new hashes alongside
        whatever produced them."""
        problems = _compare(corpus.fingerprint_all(), corpus.load_golden(),
                            "now", "golden")
        if problems:
            self.fail(
                f"{len(problems)} difference(s) from the golden output:\n  "
                + "\n  ".join(problems)
                + "\n\nIf this change was intended, refresh the hashes with:"
                  "\n    python tests/update_golden.py")

    def test_golden_covers_every_floor(self):
        """Adding a floor should mean adding it to the corpus."""
        covered = {c["floor"] for c in corpus.CASES}
        self.assertEqual(covered, set(mg.FLOORS),
                         "tests/corpus.py CASES does not cover every floor in mg.FLOORS")

    def test_case_ids_are_unique(self):
        ids = [corpus.case_id(c) for c in corpus.CASES]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        self.assertEqual(dupes, [], f"duplicate case ids would mask each other: {dupes}")


class DeterminismTest(unittest.TestCase):
    """The same seed must produce the same bytes, in any interpreter."""

    # Several, not two: a set of n strings has only n! orders, so a small one can
    # easily hash the same way under two given seeds. Floor 4's transfer
    # connectors iterate a two-element set, and seeds 1 and 2 agree on it -- that
    # bug shipped past a two-seed version of this test. Eight costs about two
    # seconds and catches every case this project has produced so far.
    HASH_SEEDS = ("1", "2", "3", "4", "5", "6", "7", "8")

    @staticmethod
    def _fingerprints_with_hashseed(seed: str) -> dict:
        env = dict(os.environ, PYTHONHASHSEED=seed)
        proc = subprocess.run(
            [sys.executable, corpus.__file__],
            capture_output=True, text=True, env=env, check=False)
        if proc.returncode != 0:
            raise AssertionError(
                f"corpus.py failed under PYTHONHASHSEED={seed}:\n{proc.stderr}")
        return json.loads(proc.stdout)

    def test_output_survives_hash_randomization(self):
        """Output must not depend on string hash order.

        Python salts string hashes per process, so a set or dict keyed by strings
        iterates in a different order each run and a bug here is a coin flip to
        reproduce. Pinning PYTHONHASHSEED to a fixed spread of values makes that
        difference repeatable, so a failure is a failure every time rather than
        one run in n.
        """
        baseline_seed = self.HASH_SEEDS[0]
        baseline = self._fingerprints_with_hashseed(baseline_seed)
        problems = []
        for seed in self.HASH_SEEDS[1:]:
            problems += _compare(baseline, self._fingerprints_with_hashseed(seed),
                                 f"hashseed={baseline_seed}", f"hashseed={seed}")
        if problems:
            self.fail(
                f"{len(problems)} difference(s) caused by string hash order:\n  "
                + "\n  ".join(problems[:20])
                + (f"\n  ... and {len(problems) - 20} more" if len(problems) > 20 else "")
                + "\n\nSomething iterates a set or dict keyed by strings and lets"
                  " that order reach the output. Sort it before use.")

    def test_same_seed_repeats_in_process(self):
        for case in (corpus.CASES[0], {"floor": 9, "size": "large", "seed": 5}):
            with self.subTest(case=corpus.case_id(case)):
                self.assertEqual(corpus.fingerprint(case), corpus.fingerprint(case))

    def test_different_seeds_differ(self):
        """Guards against a seed that is accepted and then ignored."""
        one = corpus.fingerprint({"floor": 1, "size": "medium", "seed": 1})
        two = corpus.fingerprint({"floor": 1, "size": "medium", "seed": 2})
        self.assertNotEqual(one["markdown"], two["markdown"])
        self.assertNotEqual(one["maps"], two["maps"])

    def test_omitted_seed_is_random_and_reported(self):
        seeds = {mg.generate(floor=1, size="small", images=False).seed for _ in range(5)}
        self.assertGreater(len(seeds), 1, "an omitted seed should be rolled per run")


class CrawlerTeamTest(unittest.TestCase):
    """The floor 1-2 roster is dealt rather than rolled, so that a floor gets a
    spread of fates and dispositions instead of whatever independent rolls produce.
    These are the properties that spread is for; they regress silently otherwise."""

    # Seeds per size, tapering as floors get dearer to build: a continent is 400
    # neighborhoods of rooms at ~70ms, against ~0.1ms for a small one, and all
    # that matters here is how many teams the size fields. Built once in
    # setUpClass and shared -- rebuilding them per test method took 100 seconds.
    SIZES = {"small": 60, "medium": 60, "large": 60, "huge": 30,
             "giant": 20, "massive": 15, "continent": 15}

    @classmethod
    def setUpClass(cls):
        cls.rosters = {
            size: [mg.Generator(1, size, "distance", seed, None, True).build()
                   for seed in range(count)]
            for size, count in cls.SIZES.items()}

    @staticmethod
    def _diminished(name: str) -> bool:
        return name.startswith(("What's Left of ", "The Last of ")) or name.endswith(" Remnant")

    def _rosters(self, size):
        return self.rosters[size]

    def test_roster_entries_are_distinct(self):
        """No floor fields two groups under one name, origin or status."""
        for size in self.SIZES:
            with self.subTest(size=size):
                for fl in self._rosters(size):
                    teams = fl.other_crawlers
                    for field in ("name", "origin", "status"):
                        values = [getattr(t, field) for t in teams]
                        self.assertEqual(len(set(values)), len(values),
                                         f"seed {fl.seed}: repeated {field} in {values}")

    def test_not_every_team_dies(self):
        """A roster of more than one must not be all fatalities."""
        for size in list(self.SIZES)[1:]:   # a one-team floor can legitimately be a death
            with self.subTest(size=size):
                for fl in self._rosters(size):
                    fates = [t.outcome for t in fl.other_crawlers]
                    self.assertNotEqual(set(fates), {"fatal"},
                                        f"seed {fl.seed}: every team on the floor dies")

    def test_dispositions_span_the_scale(self):
        """Enough teams to cover the scale should cover it, not cluster."""
        scale = {d["label"] for d in mg.TEAM_DISPOSITION}
        for fl in self._rosters("continent"):  # 7 teams against a 5-point scale
            self.assertEqual({t.disposition for t in fl.other_crawlers}, scale,
                             f"seed {fl.seed}: dispositions did not span the scale")

    def test_diminished_names_only_where_earned(self):
        """"What's Left of X" must not land on a team that lost nothing."""
        for size in self.SIZES:
            with self.subTest(size=size):
                for fl in self._rosters(size):
                    for t in fl.other_crawlers:
                        if self._diminished(t.name):
                            self.assertIn(t.outcome, ("costly", "fatal"),
                                          f"seed {fl.seed}: {t.name!r} is {t.outcome}")

    def test_reports_land_inside_the_countdown(self):
        for size in self.SIZES:
            with self.subTest(size=size):
                for fl in self._rosters(size):
                    for t in fl.other_crawlers:
                        self.assertTrue(1 <= t.day <= fl.days,
                                        f"seed {fl.seed}: day {t.day} outside 1..{fl.days}")

    def test_reports_are_chronological(self):
        for fl in self._rosters("continent"):
            days = [t.day for t in fl.other_crawlers]
            self.assertEqual(days, sorted(days), f"seed {fl.seed}: out of order")

    def test_no_status_leaves_an_unfilled_placeholder(self):
        for size in self.SIZES:
            with self.subTest(size=size):
                for fl in self._rosters(size):
                    for t in fl.other_crawlers:
                        self.assertNotIn("{", t.status, f"seed {fl.seed}: {t.status!r}")


class OptionValidationTest(unittest.TestCase):
    """Bad options raise MapgenError rather than exiting or crashing."""

    def test_rejects_bad_combinations(self):
        for kwargs, expected in [
            ({"floor": 3, "size": "continent"}, "floor 1-2 only"),
            ({"floor": 7, "size": "small"}, "floor must be one of"),
            ({"floor": 1, "size": "enormous"}, "size must be one of"),
            ({"floor": 1, "size": "small", "stair_chance": 1.5}, "stair chance"),
            ({"floor": 1, "size": "small", "borough_boss_rate": -1}, "borough boss rate"),
        ]:
            with self.subTest(**kwargs):
                with self.assertRaises(mg.MapgenError) as caught:
                    mg.generate(**kwargs)
                self.assertIn(expected, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
