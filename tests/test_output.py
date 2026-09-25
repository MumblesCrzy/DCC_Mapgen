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
import tempfile
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
        # Through generate(), not Generator(...).build(): teams are dealt after the
        # floor is built, on their own stream, so a raw build has no roster on it.
        cls.rosters = {
            size: [mg.generate(1, size, seed, images=False).floor
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


class CampaignTest(unittest.TestCase):
    """Teams are followed across the floors of one run.

    A team that lives keeps who it is and where it stands with the party; only its
    fate is dealt again. A team that dies leaves the floor but stays on the record."""

    @staticmethod
    def _run_campaign(size="medium", floors=mg.FLOORS, base_seed=1000):
        roster = None
        per_floor = {}
        for f in floors:
            res = mg.generate(f, size, base_seed + f, images=False, roster=roster)
            roster, per_floor[f] = res.roster, res.floor.other_crawlers
        return roster, per_floor

    def test_survivors_carry_and_the_dead_do_not(self):
        roster, per_floor = self._run_campaign()
        floors = list(per_floor)
        for prev, nxt in zip(floors, floors[1:]):
            alive = {t.name for t in per_floor[prev] if t.outcome != "fatal"}
            present = {t.name for t in per_floor[nxt]}
            dead = {t.name for t in per_floor[prev] if t.outcome == "fatal"}
            self.assertTrue(alive <= present,
                            f"floor {prev} survivors missing from {nxt}: {alive - present}")
            self.assertFalse(dead & present,
                             f"floor {nxt} fields teams that died on {prev}: {dead & present}")

    def test_identity_survives_but_the_fate_is_redealt(self):
        roster, per_floor = self._run_campaign()
        redealt = 0
        for team in roster.teams:
            if len(team.history) < 2:
                continue
            statuses = [h["status"] for h in team.history]
            self.assertEqual(len(set(statuses)), len(statuses),
                             f"{team.name} repeated a status across floors")
            redealt += 1
        self.assertGreater(redealt, 0, "no team lived long enough to be redealt")

    def test_a_dead_team_stays_on_the_record(self):
        roster, _ = self._run_campaign()
        dead = [t for t in roster.teams if not t.alive]
        self.assertTrue(dead, "a full campaign should have lost somebody")
        for t in dead:
            self.assertIsNotNone(t.died_on)
            self.assertIn(t, roster.teams)
            self.assertNotIn(t, roster.active())

    def test_a_name_is_never_reused(self):
        """Including by a later team taking a dead one's place name."""
        roster, _ = self._run_campaign(size="large")
        names = [t.name for t in roster.teams]
        surnames = [t.surname for t in roster.teams if t.surname]
        self.assertEqual(len(set(names)), len(names), f"repeated name in {names}")
        self.assertEqual(len(set(surnames)), len(surnames),
                         f"a place name came back under a new form: {surnames}")

    def test_standing_persists_and_drives_the_reaction(self):
        roster, _ = self._run_campaign(floors=(1, 2))
        team = roster.active()[0]
        mg.set_standing(roster, team.name, "allied")
        res = mg.generate(3, "medium", 7, images=False, roster=roster)
        carried = next(t for t in res.floor.other_crawlers if t.name == team.name)
        self.assertEqual(carried.disposition, "allied")
        self.assertIn("fight alongside", carried.reaction)

    def test_standing_accepts_a_partial_name_but_not_an_ambiguous_one(self):
        roster, _ = self._run_campaign(floors=(1,), size="large")
        team = roster.teams[0]
        word = team.surname.split()[0]
        mg.set_standing(roster, word, "owed a favour")
        self.assertEqual(team.disposition, "owed a favour")
        # An invented standing is kept as written, with its own fallback reaction.
        self.assertIn("owed a favour", team.reaction)
        with self.assertRaises(mg.MapgenError):
            mg.set_standing(roster, "no such team", "allied")

    def test_roster_survives_a_round_trip_through_disk(self):
        roster, _ = self._run_campaign(floors=(1, 2, 3))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "camp.json")
            mg.save_roster(roster, path)
            again = mg.load_roster(path)
        self.assertEqual([_t(t) for t in again.teams], [_t(t) for t in roster.teams])
        # and it keeps working as a campaign afterwards
        res = mg.generate(4, "medium", 9, images=False, roster=again)
        self.assertTrue(res.floor.other_crawlers)

    def test_missing_roster_file_starts_an_empty_campaign(self):
        with tempfile.TemporaryDirectory() as d:
            roster = mg.load_roster(os.path.join(d, "not-here.json"))
        self.assertEqual(roster.teams, [])

    def test_unreadable_roster_is_reported_not_crashed_on(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bad.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{not json")
            with self.assertRaises(mg.MapgenError):
                mg.load_roster(path)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"version": 99, "teams": []}, f)
            with self.assertRaises(mg.MapgenError):
                mg.load_roster(path)

    def test_every_floor_reports_a_roster(self):
        """Teams reach all eleven floors, not just the two that always had them."""
        for f in mg.FLOORS:
            with self.subTest(floor=f):
                res = mg.generate(f, "medium", 21, images=False)
                self.assertTrue(res.floor.other_crawlers, f"floor {f} fielded no teams")
                self.assertIn("## Other Crawlers", res.markdown)

    def test_floors_without_a_countdown_leave_reports_undated(self):
        for f, dated in ((1, True), (9, True), (10, False), (11, False)):
            with self.subTest(floor=f):
                res = mg.generate(f, "medium", 22, images=False)
                days = [t.day for t in res.floor.other_crawlers]
                if dated:
                    self.assertTrue(all(d is not None for d in days))
                else:
                    self.assertTrue(all(d is None for d in days),
                                    f"floor {f} has no day count to date against")

    def test_a_campaign_cannot_change_the_floor_itself(self):
        """Teams are dealt on their own rng stream, so carrying a roster in gives
        the same map and the same everything-else as generating standalone. Without
        that, every change to the team logic would shift unrelated rolls."""
        roster, _ = self._run_campaign(size="large", floors=(1, 2, 3), base_seed=900)
        plain = mg.generate(4, "large", 555)
        in_campaign = mg.generate(4, "large", 555, roster=roster)
        self.assertEqual([s for _, s in plain.svgs], [s for _, s in in_campaign.svgs],
                         "carrying a campaign changed the floor's maps")
        names = {t.name for t in plain.floor.other_crawlers}
        carried = {t.name for t in in_campaign.floor.other_crawlers}
        self.assertNotEqual(names, carried, "the campaign brought no teams of its own")

    def test_generating_without_a_campaign_still_works(self):
        res = mg.generate(1, "medium", 4242, images=False)
        self.assertTrue(res.floor.other_crawlers)
        self.assertIsNotNone(res.roster)


def _t(team):
    """A team reduced to comparable data, for round-trip checks."""
    return {f: getattr(team, f) for f in mg._TEAM_FIELDS}


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
