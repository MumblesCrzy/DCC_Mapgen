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

import collections
import glob
import json
import os
import random
import re
import string
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

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

    def test_new_teams_arrive_on_every_floor(self):
        """8.1 million crawlers enter floor 1, so there are always people the party
        has not met. A roster tops up every floor, not only after a death."""
        for size in ("small", "medium", "large"):
            with self.subTest(size=size):
                roster, per_floor = self._run_campaign(size=size, base_seed=300)
                for floor, teams in list(per_floor.items())[1:]:
                    newcomers = [t for t in teams if t.first_seen == floor]
                    self.assertTrue(newcomers, f"floor {floor} introduced nobody new")

    def test_the_roster_neither_dwindles_nor_runs_away(self):
        """The point of topping up is that a campaign cannot be emptied out; the
        point of a death rate is that it cannot grow without bound either."""
        sizes = {"small": 12, "medium": 12, "large": 20}
        for size, ceiling in sizes.items():
            with self.subTest(size=size):
                for run in range(6):
                    _, per_floor = self._run_campaign(size=size, base_seed=400 + run * 50)
                    counts = [len(t) for t in per_floor.values()]
                    self.assertTrue(all(counts), "a floor reported no teams at all")
                    self.assertLessEqual(max(counts), ceiling,
                                         f"{size} roster reached {max(counts)}")

    def test_experience_makes_a_team_harder_to_kill(self):
        """Veterans are meant to be safer than newcomers, but not safe.

        Measured over enough campaigns to be stable: the gap was around 2.8x when
        this was tuned, so 1.4x is a floor that a real regression breaks and noise
        does not. Both bounds matter -- an earlier version sorted deaths onto the
        newest team, which killed 59% of groups on sight and made anyone four
        floors in practically immortal."""
        dealt, died = collections.Counter(), collections.Counter()
        for c in range(60):
            roster = None
            for f in mg.FLOORS:
                before = ({t.name: len(t.history) for t in roster.active()}
                          if roster else {})
                res = mg.generate(f, "medium", c * 37 + f, images=False, roster=roster)
                roster = res.roster
                for t in res.floor.other_crawlers:
                    veteran = before.get(t.name, 0) >= 2
                    dealt[veteran] += 1
                    died[veteran] += t.outcome == "fatal"
        rookie = died[False] / dealt[False]
        veteran = died[True] / dealt[True]
        self.assertGreater(rookie, veteran * 1.4,
                           f"experience barely helped: {rookie:.0%} vs {veteran:.0%}")
        self.assertGreater(veteran, 0.04,
                           f"veterans are practically immortal at {veteran:.0%}")
        self.assertLess(rookie, 0.55,
                        f"newcomers are dying on sight at {rookie:.0%}")

    def test_the_mix_of_fates_is_unchanged_by_the_bias(self):
        """Weighting decides who draws a death, not how many are dealt."""
        outcomes = collections.Counter()
        for c in range(40):
            roster = None
            for f in mg.FLOORS:
                res = mg.generate(f, "medium", c * 13 + f, images=False, roster=roster)
                roster = res.roster
                outcomes.update(t.outcome for t in res.floor.other_crawlers)
        total = sum(outcomes.values())
        for kind in mg.TEAM_STATUS:
            share = outcomes[kind] / total
            self.assertTrue(0.18 < share < 0.32,
                            f"{kind} is {share:.0%} of fates, expected about a quarter")

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


class CampaignEditTest(unittest.TestCase):
    """Groups the party meets at the table, and reports a DM rewrites.

    An added group carries from floor to floor like any other -- a fresh report
    each floor, a party from floor 3 -- but the generator never renames it or
    re-deals its standing, which it does to every group it has just met itself."""

    @staticmethod
    def _campaign(floors=(1, 2, 3), seed=4242):
        roster = None
        for f in floors:
            roster = mg.generate(f, "small", seed, images=False, roster=roster).roster
        return roster

    def test_an_added_group_carries_like_any_other(self):
        roster = self._campaign()
        mg.add_team(roster, "The Sky Pirates", origin="a hen party on a zeppelin tour",
                    standing="friendly", floor=3, status="arguing over a map")
        roster = mg.generate(4, "small", 4242, images=False, roster=roster).roster
        team = next(t for t in mg.floor_teams(roster, 4) if t.name == "The Sky Pirates")
        self.assertEqual(team.origin, "a hen party on a zeppelin tour")
        self.assertEqual(team.disposition, "friendly")
        self.assertTrue(team.members, "an added group picks races and classes like any other")
        self.assertEqual(len(team.history), 2)
        self.assertNotEqual(team.status, "arguing over a map", "a new floor deals a new report")

    def test_an_added_group_is_never_renamed_or_redealt(self):
        """The case that matters: added on the very floor about to be generated,
        which is when the generator treats a group as newly met -- and newly met
        groups get a name form ("Team X", "The X Crew") and a dealt standing."""
        for seed in range(1, 13):
            roster = self._campaign(floors=(1, 2), seed=seed)
            mg.add_team(roster, "The Sky Pirates", standing="owed a favour", floor=3)
            roster = mg.generate(3, "small", seed, images=False, roster=roster).roster
            team = mg.find_team(roster, "Sky Pirates")
            self.assertEqual(team.name, "The Sky Pirates", f"seed {seed}: renamed")
            self.assertEqual(team.disposition, "owed a favour", f"seed {seed}: re-dealt")
            self.assertTrue(team.added)

    def test_a_bad_group_is_refused(self):
        roster = self._campaign()
        existing = roster.teams[0].name
        for name in (existing, existing.upper(), "  ", ""):
            with self.assertRaises(mg.MapgenError, msg=repr(name)):
                mg.add_team(roster, name)
        with self.assertRaises(mg.MapgenError):
            mg.add_team(roster, "The Sky Pirates", outcome="exploded")
        with self.assertRaises(mg.MapgenError):
            mg.add_team(roster, "The Sky Pirates", floor=0)
        with self.assertRaises(mg.MapgenError):
            mg.add_team(None, "The Sky Pirates")

    def test_an_added_group_can_arrive_dead(self):
        roster = self._campaign()
        team = mg.add_team(roster, "The Late Arrivals", floor=3, outcome="fatal",
                           status="found in pieces by the stairwell")
        self.assertFalse(team.alive)
        self.assertEqual(team.died_on, 3)
        roster = mg.generate(4, "small", 4242, images=False, roster=roster).roster
        self.assertNotIn("The Late Arrivals", [t.name for t in mg.floor_teams(roster, 4)])

    def test_fatal_kills_and_anything_else_brings_back(self):
        roster = self._campaign()
        alive = roster.active()[0]
        mg.set_status(roster, alive.name, outcome="fatal", status="killed by the party")
        self.assertFalse(alive.alive)
        self.assertEqual(alive.died_on, alive.floor)
        roster = mg.generate(4, "small", 4242, images=False, roster=roster).roster
        self.assertNotIn(alive.name, [t.name for t in mg.floor_teams(roster, 4)])
        mg.set_status(roster, alive.name, outcome="costly", status="dragged back somehow")
        self.assertTrue(alive.alive)
        roster = mg.generate(5, "small", 4242, images=False, roster=roster).roster
        self.assertIn(alive.name, [t.name for t in mg.floor_teams(roster, 5)],
                      "a team brought back carries onto the next floor")

    def test_editing_corrects_the_latest_entry_rather_than_adding_one(self):
        roster = self._campaign()
        team = roster.active()[0]
        before = len(team.history)
        mg.set_status(roster, team.name, status="holed up in a launderette")
        self.assertEqual(len(team.history), before, "a second entry for the same floor")
        self.assertEqual(team.history[-1]["status"], "holed up in a launderette")
        self.assertTrue(team.history[-1]["by_dm"])
        self.assertEqual(team.status, "holed up in a launderette")
        mg.set_status(roster, team.name, outcome="thriving")
        self.assertEqual(team.status, "holed up in a launderette", "the report survived")

    def test_a_status_edit_finds_teams_the_way_standing_does(self):
        roster = self._campaign()
        mg.add_team(roster, "The Sky Pirates", floor=3)
        self.assertEqual(mg.set_status(roster, "sky pir", status="x").name, "The Sky Pirates")
        with self.assertRaises(mg.MapgenError):
            mg.set_status(roster, "nobody at all", status="x")
        with self.assertRaises(mg.MapgenError):
            mg.set_status(roster, "The Sky Pirates", outcome="exploded")

    def test_added_survives_a_save_and_load_and_old_rosters_still_load(self):
        roster = self._campaign()
        mg.add_team(roster, "The Sky Pirates", floor=3)
        mg.add_team(roster, "The Early Birds", floor=2, crawlers=5)
        back = mg.CampaignRoster.from_dict(roster.to_dict())
        self.assertTrue(mg.find_team(back, "Sky Pirates").added)
        self.assertEqual(mg.find_team(back, "Early Birds").headcount, 5)
        old = roster.to_dict()
        for t in old["teams"]:
            t.pop("added", None)
            t.pop("headcount", None)
        loaded = mg.CampaignRoster.from_dict(old).teams
        self.assertFalse(any(t.added for t in loaded))
        self.assertTrue(all(t.headcount is None for t in loaded))

    # -- how many crawlers ----------------------------------------------------

    def test_a_count_before_floor_3_is_only_a_number(self):
        roster = self._campaign(floors=(1, 2))
        team = mg.add_team(roster, "The Early Birds", floor=2, crawlers=5)
        self.assertEqual((team.members, team.headcount, team.crawler_count), ([], 5, 5),
                         "nobody has a race or class before floor 3")
        self.assertEqual(team.history[-1]["crawlers"], 5)
        self.assertEqual(mg.party_line(team), "Five crawlers.")
        generated = roster.active()[0]
        mg.set_status(roster, generated.name, crawlers=1)
        self.assertEqual((generated.members, generated.crawler_count), ([], 1))
        self.assertEqual(mg.party_line(generated), "Down to one crawler.")
        mg.set_status(roster, generated.name, outcome="fatal")
        self.assertEqual(mg.party_line(generated), "There was one of them.")
        mg.set_status(roster, team.name, outcome="fatal")
        self.assertEqual(mg.party_line(team), "There were five of them.")

    def test_a_counted_group_shows_its_number_on_floors_1_and_2(self):
        roster = self._campaign(floors=(1,))
        mg.add_team(roster, "The Early Birds", floor=1, crawlers=5)
        lines = mg.generate(2, "small", 4242, images=False, roster=roster).markdown.splitlines()
        at = next(i for i, ln in enumerate(lines) if "The Early Birds" in ln)
        self.assertRegex(lines[at + 1], r"^  - (\w+ crawlers|Down to one crawler|"
                                        r"There (was|were) \w+ of them)\.$")
        uncounted = [i for i, ln in enumerate(lines)
                     if ln.startswith("- **") and "formed from" in ln and i != at]
        self.assertTrue(uncounted)
        for i in uncounted:
            self.assertFalse(lines[i + 1].startswith("  - "),
                             "a group nobody counted was given a number before floor 3")

    def test_the_report_moves_the_number_the_way_it_moves_a_party(self):
        # floor, the DM's number, the report's change -> number after, crawlers dealt
        cases = [(2, 5, (0, None), 5, 0), (2, 5, (-1, None), 4, 0), (2, 5, (-9, None), 1, 0),
                 (2, 5, (2, None), 7, 0), (1, 7, (3, None), mg.PARTY_MAX, 0),
                 (2, 10, (1, None), 10, 0),
                 (3, 5, (0, None), None, 5), (3, 5, (-1, None), None, 4)]
        for floor, start, change, headcount, members in cases:
            with self.subTest(floor=floor, start=start, change=change):
                team = mg.CrawlerTeam(name="Team Probe", origin="a probe", headcount=start)
                mg._deal_party(random.Random(1), team, floor, change)
                self.assertEqual(len(team.members), members)
                self.assertEqual(team.headcount, headcount)

    def test_floor_3_deals_the_number_the_dm_gave(self):
        """Seven, because the roll tops out at six: a group dealt seven was dealt
        the DM's number, not a roll that happened to agree with it."""
        self.assertLess(max(mg.PARTY_SIZES), 7)
        sevens = 0
        for seed in range(1, 13):
            roster = self._campaign(floors=(1, 2), seed=seed)
            mg.add_team(roster, "The Early Birds", floor=2, crawlers=7)
            roster = mg.generate(3, "small", seed, images=False, roster=roster).roster
            team = mg.find_team(roster, "Early Birds")
            self.assertIsNone(team.headcount, f"seed {seed}: the number outlived the party")
            self.assertEqual(team.history[-1]["crawlers"], len(team.members))
            sevens += len(team.members) == 7
        self.assertGreaterEqual(sevens, 4, "floor 3 ignored the DM's count")

    def test_a_count_from_floor_3_is_dealt_at_once(self):
        roster = self._campaign()
        upgradable = 0
        for i in range(20):
            for floor in (4, 7):
                team = mg.add_team(roster, f"Probe {floor}-{i}", floor=floor, crawlers=5)
                self.assertEqual(len(team.members), 5)
                self.assertIsNone(team.headcount)
                races = [m["race"] for m in team.members]
                classes = [m["class"] for m in team.members]
                self.assertEqual(len(set(races)), 5, races)
                self.assertEqual(len(set(classes)), 5, classes)
                held = [c for c in classes if c in mg.CRAWLER_CLASS_UPGRADE]
                if floor == 4:
                    upgradable += bool(held)
                else:
                    self.assertEqual(held, [], "met past floor 6 without the upgrade")
        self.assertTrue(upgradable, "nothing upgradable was dealt, so the floor 7 check "
                                    "proves nothing")
        uncounted = mg.add_team(roster, "The Unknowns", floor=4)
        self.assertEqual((uncounted.members, uncounted.crawler_count), ([], None),
                         "a group nobody counted is left for the generator to size")

    def test_editing_the_count_deals_or_drops_crawlers(self):
        roster = self._campaign()
        team = next(t for t in roster.active() if t.members)
        before = [dict(m) for m in team.members]
        entries = len(team.history)
        mg.set_status(roster, team.name, crawlers=len(before) + 2)
        self.assertEqual(team.members[:len(before)], before, "the crawlers already there")
        self.assertEqual(len({m["race"] for m in team.members}), len(before) + 2)
        self.assertEqual(len({m["class"] for m in team.members}), len(before) + 2)
        self.assertEqual(team.history[-1]["crawlers"], len(before) + 2)
        mg.set_status(roster, team.name, crawlers=1)
        self.assertEqual(team.members, before[:1], "the most recent to join leave first")
        self.assertEqual(team.history[-1]["crawlers"], 1)
        self.assertEqual(len(team.history), entries, "a second entry for the same floor")

    def test_crawlers_the_dm_adds_never_double_up(self):
        """At volume, and grown well past a party: two newcomers of 46 races
        colliding with each other or the group is rare at +2 and certain here."""
        grown = 0
        for seed in range(1, 9):
            roster = self._campaign(seed=seed)
            for team in [t for t in roster.teams if t.members]:
                mg.set_status(roster, team.name, crawlers=12)
                grown += 1
                races = [m["race"] for m in team.members]
                classes = [m["class"] for m in team.members]
                self.assertEqual(len(set(races)), 12, f"{team.name}: {races}")
                self.assertEqual(len(set(classes)), 12, f"{team.name}: {classes}")
        self.assertGreaterEqual(grown, 20)

    def test_a_bad_count_is_refused_and_changes_nothing(self):
        roster = self._campaign()
        team = roster.active()[0]
        status, members = team.status, [dict(m) for m in team.members]
        for bad in (0, -2, "3", 2.5):
            with self.assertRaises(mg.MapgenError, msg=repr(bad)):
                mg.add_team(roster, f"The Bad Count {bad}", crawlers=bad)
            with self.assertRaises(mg.MapgenError, msg=repr(bad)):
                mg.set_status(roster, team.name, status="rewritten", crawlers=bad)
        self.assertEqual((team.status, team.members), (status, members))
        self.assertFalse([t for t in roster.teams if t.name.startswith("The Bad Count")])


class Floor4LayoutTest(unittest.TestCase):
    """The Iron Tangle's stations, as the books lay them out, and its withdrawal.

    Stairwells are at 12, 24, 36, 48 and 72 on every line. A transfer has a safe
    room only if its number is prime, a Desperado Club if it ends in 1, a Club
    Vanquisher if it ends in 9. The rules are written out here, not read from the
    generator's config."""

    STAIRS = [12, 24, 36, 48, 72]
    PRIMES = {n for n in range(2, 500) if all(n % d for d in range(2, n))}
    # The books' own withdrawal and last-day plot: the bank is ours instead. Full
    # names, since "Blister" on its own is a word the mob bank uses for anything.
    CANON = ("krakaren", "festering ghoul", "wrath ghoul", "blister ghoul",
             "crotch dumpling", "ghoul generator")

    # Generated once and shared: these tests only read a floor, and two dozen of
    # them each building the same 36 floors cost the suite ten seconds.
    _built = {}

    @classmethod
    def _floors(cls, seeds=range(1, 13), sizes=("small", "medium", "large")):
        for size in sizes:
            for seed in seeds:
                if (size, seed) not in cls._built:
                    cls._built[size, seed] = mg.generate(4, size, seed, images=False)
                yield size, seed, cls._built[size, seed]

    def test_every_line_has_all_five_stairwells_and_no_others(self):
        for size, seed, res in self._floors():
            for ln in res.floor.lines:
                stairs = sorted(st.number for st in ln.stations if st.has_stairwell)
                self.assertEqual(stairs, self.STAIRS, f"{size} seed {seed}: {ln.color}")

    def test_no_transfer_hides_in_a_stairwell_station(self):
        """The write-up gives the five stairwell stations one line between them,
        so a transfer dealt onto one would vanish from every line's list."""
        transfers = 0
        for size, seed, res in self._floors(seeds=range(1, 41)):
            for ln in res.floor.lines:
                for st in ln.stations:
                    if st.transfer_to:
                        transfers += 1
                        self.assertNotIn(st.number, self.STAIRS,
                                         f"{size} seed {seed}: {ln.color} {st.number}")
        self.assertGreater(transfers, 500)

    def test_safe_rooms_and_clubs_follow_the_station_number(self):
        clubs = collections.Counter()
        for size, seed, res in self._floors():
            for ln in res.floor.lines:
                for st in ln.stations:
                    where = f"{size} seed {seed}: {ln.color} {st.number}"
                    transfer = bool(st.transfer_to)
                    self.assertEqual(st.safe_room, transfer and st.number in self.PRIMES, where)
                    self.assertEqual(st.desperado, transfer and st.number % 10 == 1, where)
                    self.assertEqual(st.vanquisher, transfer and st.number % 10 == 9, where)
                    clubs["safe room"] += st.safe_room
                    clubs["desperado"] += st.desperado
                    clubs["vanquisher"] += st.vanquisher
        self.assertTrue(all(clubs[k] for k in ("safe room", "desperado", "vanquisher")),
                        f"something never came up, so its rule went untested: {clubs}")

    def test_every_floor_has_a_safe_room_on_its_lines(self):
        for size, seed, res in self._floors(seeds=range(1, 41), sizes=("small",)):
            self.assertTrue(any(st.safe_room for ln in res.floor.lines for st in ln.stations),
                            f"seed {seed}")

    def test_every_line_has_a_safe_room_in_each_stretch(self):
        """Most stations on the Iron Tangle are transfers, so a line meets lines the
        floor does not detail too, and has a safe room in each stretch a party
        uses: 11 to 71, 73 to the station before its spawn, and the forty
        stations after it -- wherever the stretch has a prime at all."""
        for size, seed, res in self._floors():
            for ln in res.floor.lines:
                where = f"{size} seed {seed}: {ln.color} (spawn {ln.spawn})"
                safe = {st.number for st in ln.stations if st.safe_room}
                for first, last in ((11, 71), (73, ln.spawn - 1), (ln.spawn + 1, ln.spawn + 40)):
                    stretch = set(range(first, last + 1))
                    if stretch & self.PRIMES:
                        self.assertTrue(safe & stretch, f"{where}: nothing in {first}–{last}")
        counts = {size: [len(res.floor.safe_rooms) for _, _, res in self._floors(sizes=(size,))]
                  for size in ("small", "medium", "large")}
        self.assertGreaterEqual(min(counts["large"]), 10, counts)
        self.assertGreaterEqual(min(counts["small"]), 4, counts)

    def test_a_line_not_detailed_here_says_so(self):
        """A transfer to a line the floor doesn't detail names a real line color,
        says it is not detailed on the line's list and the safe room's heading,
        and is drawn as a stub on the map. Its number is no other detailed line's
        own platform: two lines at one number read as one station."""
        seen = 0
        for seed in range(1, 7):
            res = mg.generate(4, "medium", seed)
            fl, md, svg = res.floor, res.markdown, res.svgs[0][1]
            detailed = {ln.color for ln in fl.lines}
            for ln in fl.lines:
                for st in ln.stations:
                    away = sorted(set(st.transfer_to) - detailed)
                    if not away:
                        continue
                    seen += 1
                    where = f"seed {seed}: {ln.color} {st.number}"
                    for col in away:
                        self.assertIn(col, mg.LINE_COLORS, where)
                        self.assertIn(f">{col}</text>", svg, where)
                    self.assertIn(f"  - **{st.number}** — ", md)
                    self.assertIn(f"{', '.join(away)} Line, not detailed here", md, where)
                    for other in fl.lines:
                        if other is not ln:
                            for s in other.stations:
                                if s.number == st.number:
                                    self.assertTrue(s.transfer_to, where)
            for sr in fl.safe_rooms:
                if sr["others"]:
                    self.assertIn(f"; change for the {mg._lines_text(sr['others'])})", md)
                    self.assertFalse(set(sr["others"]) & detailed)
        self.assertGreater(seen, 30)

    def test_the_write_up_marks_what_each_transfer_has(self):
        row = re.compile(r"^  - \*\*(\d+)\*\* — (\w[\w ]*?)(?: ↔ [^(.]+)?(?: \(([^)]*)\))?\. ")
        checked = 0
        for size, seed, res in self._floors(seeds=range(1, 5)):
            md = res.markdown
            self.assertNotIn("Every transfer station has a safe room", md)
            for ln in md.splitlines():
                m = row.match(ln)
                if not m or "TRANSFER" not in ln and "↔" not in ln:
                    continue
                checked += 1
                n, has = int(m.group(1)), m.group(3) or ""
                self.assertEqual("safe room" in has, n in self.PRIMES, ln)
                self.assertEqual("Desperado Club" in has, n % 10 == 1, ln)
                self.assertEqual("Club Vanquisher" in has, n % 10 == 9, ln)
        self.assertGreater(checked, 20)

    def test_a_shared_transfer_reads_the_same_from_either_line(self):
        for size, seed, res in self._floors(seeds=range(1, 7)):
            notes = collections.defaultdict(set)
            for ln in res.floor.lines:
                for st in ln.stations:
                    if st.kind == "transfer":
                        notes[st.number].add(st.note)
            for n, seen in notes.items():
                self.assertEqual(len(seen), 1, f"{size} seed {seed}: station {n} {seen}")

    def test_withdrawal_stays_inside_the_countdown(self):
        for days in (3, 4, 6, 10, 15):
            for seed in range(1, 9):
                fl = mg.generate(4, "large", seed, days=days, images=False).floor
                for ln in fl.lines:
                    self.assertTrue(2 <= ln.stage2_day < ln.stage3_day <= days or
                                    (days < 3 and ln.stage3_day <= days),
                                    f"{days} days, seed {seed}: {ln.color} stage 2 day "
                                    f"{ln.stage2_day}, stage 3 day {ln.stage3_day}")

    def test_withdrawal_is_three_stages_and_a_last_day(self):
        for size, seed, res in self._floors(seeds=range(1, 5)):
            fl, md = res.floor, res.markdown
            self.assertEqual(len(fl.withdrawal), 3)
            self.assertEqual(len(set(fl.last_day)), 2)
            section = md[md.index("## Withdrawal"):md.index("## Ghoul Stations")]
            for n, st in enumerate(fl.withdrawal, start=1):
                self.assertIn(f"| {n} | ", section)
                self.assertIn(st["risen"]["name"], section)
            for event in fl.last_day:
                self.assertIn(event, section)
            self.assertNotIn("WITHDRAWAL_PROFILE", md)

    def test_none_of_the_books_floor_4_plot(self):
        for size, seed, res in self._floors():
            body = mg.floor_body(res.markdown).lower()
            for word in self.CANON:
                self.assertNotIn(word, body, f"{size} seed {seed}")

    def test_the_stairwell_stations_each_get_their_own_note(self):
        for size, seed, res in self._floors(seeds=range(1, 7)):
            notes = res.floor.stair_notes
            self.assertEqual(sorted(notes), self.STAIRS)
            self.assertEqual(len(set(notes.values())), 5, f"{size} seed {seed}")

    # -- the ghoul stations ---------------------------------------------------

    def test_each_stairwell_station_is_a_ghoul_station(self):
        """One kind of ghoul each, nothing repeated: twelve is the janitors',
        since they already board there; three hold what each withdrawal stage
        raises; one holds a ghoul of its own. One of the last four can merge."""
        for size, seed, res in self._floors():
            fl, where = res.floor, f"{size} seed {seed}"
            stations = fl.ghoul_stations
            self.assertEqual([gs["number"] for gs in stations], self.STAIRS, where)
            self.assertEqual(stations[0]["ghoul"]["name"], fl.janitor.name, where)
            names = [gs["ghoul"]["name"] for gs in stations]
            self.assertEqual(len(set(names)), 5, f"{where}: {names}")
            risen = {w["risen"]["name"] for w in fl.withdrawal}
            self.assertTrue(risen <= set(names[1:]),
                            f"{where}: withdrawal's dead {risen} not all at a station")
            for key in ("name", "feed", "tide", "opening", "stair"):
                self.assertEqual(len({gs[key] for gs in stations}), 5, f"{where}: {key}")
            merged = [gs["number"] for gs in stations if gs["merged"]]
            self.assertEqual(len(merged), 1, where)
            self.assertNotEqual(merged[0], 12, f"{where}: the janitors merged")

    def test_a_ghoul_stations_days_are_inside_the_countdown(self):
        for days in (3, 5, 10, 15):
            for seed in range(1, 13):
                fl = mg.generate(4, "small", seed, days=days, images=False).floor
                for gs in fl.ghoul_stations:
                    self.assertNotIn("{", gs["tide"])
                    for day in map(int, re.findall(r"day (\d+)", gs["tide"])):
                        self.assertTrue(1 <= day <= days, f"{days} days: {gs['tide']}")

    def test_the_write_up_gives_each_ghoul_station_its_own_entry(self):
        for size, seed, res in self._floors(seeds=range(1, 5)):
            md = res.markdown
            section = md[md.index("## Ghoul Stations"):md.index("## Colored Lines")]
            for gs in res.floor.ghoul_stations:
                self.assertIn(f"### {gs['number']} — {gs['name']}", section)
                self.assertIn(gs["ghoul"]["name"], section)
                self.assertIn(gs["opening"], section)
            self.assertEqual(section.count("**If enough of them gather:**"), 1)
            self.assertNotIn("[Stairwell Stations]", md)

    def test_the_map_names_the_ghoul_stations(self):
        for size in ("small", "large"):
            res = mg.generate(4, size, 4242)
            svg = res.svgs[0][1]
            for gs in res.floor.ghoul_stations:
                for word in gs["name"].split():
                    self.assertIn(word, svg, f"{size}: {gs['name']}")

    # -- getting around ---------------------------------------------------------

    def test_travel_times_meet_the_books_anchors(self):
        """A stop every 2 to 20 minutes; about three days from 11 to 435; a block
        or two apart up to 72; 40 or 50 miles apart out at 300."""
        for n in range(11, 435):
            self.assertTrue(2 <= mg.f4_gap_minutes(n) <= 20, f"gap {n}")
            self.assertLessEqual(mg.f4_gap_miles(n), 50, f"gap {n}")
        days = mg.f4_ride_minutes(11, 435) / 60 / 24
        self.assertTrue(2.5 <= days <= 3.5, f"11 to 435 is {days:.2f} days")
        for n in range(11, 72):
            self.assertTrue(0.05 <= mg.f4_gap_miles(n) <= 0.3, f"gap {n}")
        self.assertTrue(40 <= mg.f4_gap_miles(300) <= 50)
        hours = mg.f4_walk_minutes(11, 72) / 60
        self.assertTrue(1 <= hours <= 12, f"11 to 72 on foot is {hours:.1f} hours")

    def test_further_out_is_a_longer_walk_and_a_shorter_ride(self):
        walks = [mg.f4_walk_minutes(72, s) for s in range(73, 141)]
        rides = [mg.f4_ride_minutes(s, 435) for s in range(73, 141)]
        self.assertEqual(walks, sorted(walks))
        self.assertEqual(rides, sorted(rides, reverse=True))
        for s in (73, 100, 140):          # a tunnel is as long walked either way
            self.assertEqual(mg.f4_walk_miles(s, 72), mg.f4_walk_miles(72, s))
            self.assertGreater(mg.f4_walk_miles(s, 72), 0)

    def test_durations_read_as_a_person_would_say_them(self):
        cases = [(3, "about 5 minutes"), (25, "about 25 minutes"), (61, "about 1 hour"),
                 (7 * 60, "about 7 hours"), (24 * 60, "about 1 day"),
                 (64 * 60, "about 2 days and 16 hours")]
        for minutes, text in cases:
            self.assertEqual(mg.duration_text(minutes), text)

    def test_the_cars_are_laid_out(self):
        for size, seed, res in self._floors(seeds=range(1, 4)):
            md = res.markdown
            section = md[md.index("## Getting Around"):md.index("## Withdrawal")]
            for car in ("| 1 |", "| 5 |", "| 10 |", "| 15 |"):
                self.assertIn(car, section)
            self.assertIn(f"| 15 | The janitors: {res.floor.janitor.name}.", section)
            self.assertIn("No mob ever comes in", section)

    def test_each_line_says_how_long_its_crawlers_have(self):
        for size, seed, res in self._floors(seeds=range(1, 5)):
            md = res.markdown
            self.assertNotIn("**Crawler spawn:**", md)
            for ln in res.floor.lines:
                block = md[md.index(f"### {ln.color} Line"):]
                block = block[:block.index("- **Stations of note")]
                where = f"{size} seed {seed}: {ln.color}"
                self.assertIn(f"pulling into station {ln.spawn}.", block, where)
                ride = mg.duration_text(mg.f4_ride_minutes(ln.spawn, 435))
                self.assertIn(f"the train reaches 435 in {ride}", block, where)
                self.assertIn(f"Back to 72 on foot is {ln.spawn - 72} station", block, where)
                walk = mg.duration_text(mg.f4_walk_minutes(72, ln.spawn))
                self.assertIn(f"{walk} if the tunnel is empty", block, where)
                m = re.search(r"The exit cavern at (\d+) is (\d+) stops? ahead", block)
                self.assertTrue(m, where)
                cavern, ahead = int(m.group(1)), int(m.group(2))
                self.assertEqual(cavern - ln.spawn, ahead, where)
                self.assertTrue(1 <= ahead <= 5, where)
                self.assertIn(cavern, [st.number for st in ln.stations
                                       if st.kind == "exit_cavern"], where)

    # -- safe rooms, hubs, quests and achievements ---------------------------------

    HUBS = {"small": 1, "medium": 1, "large": 2}

    def test_every_safe_room_is_somebodys_business(self):
        for size, seed, res in self._floors():
            fl, where = res.floor, f"{size} seed {seed}"
            primes = sorted({st.number for ln in fl.lines for st in ln.stations
                             if st.transfer_to and st.number in self.PRIMES})
            self.assertEqual([sr["number"] for sr in fl.safe_rooms], primes, where)
            names = [sr["business"]["name"] for sr in fl.safe_rooms]
            self.assertEqual(len(set(names)), len(names), f"{where}: {names}")
            owners = [sr["proprietor"].split(",")[0] for sr in fl.safe_rooms]
            self.assertEqual(len(set(owners)), len(owners), f"{where}: {owners}")
            for sr in fl.safe_rooms:
                b = sr["business"]
                # Every line the transfer is on says so. The same number on a third
                # line is that line's own platform, and none of this.
                through = [ln.color for ln in fl.lines for st in ln.stations
                           if st.number == sr["number"] and st.transfer_to]
                self.assertEqual(sorted(sr["lines"]), sorted(through), where)
                for ln in fl.lines:
                    for st in ln.stations:
                        if st.number == sr["number"] and st.transfer_to:
                            self.assertEqual(st.note, f"Safe room: {b['name']}, {b['what']}.",
                                             where)
                        elif st.number == sr["number"]:
                            self.assertFalse(st.safe_room, where)
                self.assertIn(f"### {sr['number']} — {b['name']} (", res.markdown, where)

    def test_hubs_are_where_many_lines_meet(self):
        for size, seed, res in self._floors():
            fl, where = res.floor, f"{size} seed {seed}"
            transfers = {st.number for ln in fl.lines for st in ln.stations if st.transfer_to}
            plain = transfers - self.PRIMES
            self.assertEqual(len(fl.hubs), min(self.HUBS[size], len(plain) or 1), where)
            for h in fl.hubs:
                self.assertIn(h["number"], transfers, where)
                if plain:
                    self.assertNotIn(h["number"], self.PRIMES,
                                     f"{where}: a hub on a safe room with others free")
                self.assertTrue(max(8, len(h["lines"])) <= h["n_lines"] <= 30, where)
                self.assertEqual(len(set(h["shops"])), 2, where)
                self.assertTrue(1 <= len(set(h["guilds"])) <= 2, where)
                for ln in fl.lines:
                    for st in ln.stations:
                        if st.number == h["number"]:
                            self.assertEqual(st.hub_lines,
                                             h["n_lines"] if st.transfer_to else 0, where)
                self.assertIn(f"### {h['number']} — Hub, {h['n_lines']} lines", res.markdown)
                self.assertIn(f"station {h['number']} ({h['n_lines']} lines)", res.markdown)

    def test_a_station_number_shared_by_chance_is_still_two_stations(self):
        """At volume, the two coincidences a few dozen floors rarely produce: a
        safe-room transfer dealt onto one of its own lines' spawn platforms, which
        must then name the business; and a hub whose number is also a third line's
        plain platform, which is not part of the hub."""
        merged = elsewhere = 0
        for seed in range(1, 301):
            fl = mg.generate(4, "large", seed, images=False).floor
            hubs = {h["number"] for h in fl.hubs}
            names = {sr["number"]: sr["business"]["name"] for sr in fl.safe_rooms}
            for ln in fl.lines:
                for st in ln.stations:
                    if st.kind == "spawn" and st.safe_room:
                        merged += 1
                        self.assertTrue(st.note.startswith(f"Safe room: {names[st.number]},"),
                                        f"seed {seed}: {ln.color} {st.number}: {st.note}")
                    if st.number in hubs and not st.transfer_to:
                        elsewhere += 1
                        self.assertEqual(st.hub_lines, 0, f"seed {seed}: {ln.color} {st.number}")
        self.assertTrue(merged and elsewhere,
                        f"never met: spawn safe rooms {merged}, other platforms {elsewhere}")

    def test_floor_4_lists_its_achievements(self):
        res = mg.generate(4, "medium", 4242, images=False)
        md = res.markdown
        section = md[md.index("## Achievements"):md.index("## Loot Boxes")]
        boxed = {s.source for s in mg.floor_box_suggestions(res.floor) if s.kind == "achievement"}
        self.assertGreaterEqual(len(mg.ACHIEVEMENTS4), 12)
        for name, trigger, reward in mg.ACHIEVEMENTS4:
            self.assertIn(f"- **{name}:** {trigger} → {reward}", section)
            if reward != "flavor only":
                self.assertRegex(reward, r"^(Bronze|Silver|Gold|Platinum|Legendary|Celestial) ")
                self.assertIn(name, boxed)

    def test_each_safe_room_and_hub_posts_a_quest(self):
        items = [re.sub(r"^(?:a|an|the)\s+", "", i).lower()
                 for i in mg.STATION_QUESTS4["items"]]
        for size, seed, res in self._floors():
            fl, where = res.floor, f"{size} seed {seed}"
            posted = [sr["quest"] for sr in fl.safe_rooms] + [h["quest"] for h in fl.hubs]
            self.assertEqual(fl.quests, posted, where)
            titles = [q.title for q in posted]
            self.assertEqual(len(set(titles)), len(titles), f"{where}: {titles}")
            suggested = {s.source for s in mg.floor_box_suggestions(fl) if s.kind == "quest"}
            for q in posted:
                text = f"{q.title} {q.objective} {q.reward}"
                self.assertNotIn("{", text, where)
                self.assertNotIn("The The", q.title, where)
                self.assertIn("Quest Box", q.reward, where)
                self.assertIn(q.flavor, mg.STATION_QUESTS4["flavor"],
                              f"{where}: borrowed another floor's flavor: {q.flavor}")
                self.assertIn(q.title, suggested, where)
                if q.title.endswith(" Job"):
                    named = q.title[4:-4].lower()
                    self.assertIn(named, items, where)
                    self.assertIn(named, q.objective.lower(),
                                  f"{where}: {q.title!r} names something the quest doesn't")
            for h in fl.hubs:
                self.assertNotIn("noticeboard", h["quest"].title, where)

    def test_quest_titles_never_repeat_even_past_the_deck(self):
        """A title skipped for one quest -- it names an item the quest has none
        of -- is still in the deck when it reshuffles, behind titles already used.
        On a bank of two plain titles and one that names the item, with every
        quest fetching a different item, eight quests have eight titles to give:
        the deck alone would deal Alpha or Beta again on its second pass."""
        gen = mg.Generator4("medium", 5, None)
        fl = gen.build()
        hub = fl.hubs[0]["number"]
        bank = {"titles": ["Alpha", "Beta", "The {Item} Affair"],
                "objectives": ["Fetch {item} from {station}."]}
        with mock.patch.dict(mg.STATION_QUESTS4, bank):
            decks = gen._quest_decks()
            titles = [gen._station_quest(hub, "the station noticeboard", fl.lines,
                                         fl.ghoul_stations, decks).title for _ in range(8)]
        self.assertEqual(len(set(titles)), len(titles), titles)
        self.assertEqual(sum(t in ("Alpha", "Beta") for t in titles), 2, titles)

    def test_every_named_train_starts_a_quest_when_boarded(self):
        self.assertFalse([g for g in mg.NAMED_GIMMICK if "quest" in g.lower()],
                         "a gimmick still promises the quest every train now has")
        lo, hi = mg.TRAIN_QUESTS4["minutes"]
        for size, seed, res in self._floors():
            fl, where = res.floor, f"{size} seed {seed}"
            titles = [t.quest["title"] for t in fl.named]
            self.assertEqual(len(set(titles)), len(titles), f"{where}: {titles}")
            suggested = {s.source for s in mg.floor_box_suggestions(fl) if s.kind == "quest"}
            for t in fl.named:
                q = t.quest
                self.assertTrue(lo <= q["minutes"] <= hi, where)
                self.assertIn(f"{q['minutes']} minutes", q["objective"], where)
                self.assertIn(t.crew.name, f"{q['objective']} {q['failure']}",
                              f"{where}: the train's own crew play no part")
                for part in ("objective", "failure", "title"):
                    self.assertNotIn("{", q[part], where)
                self.assertIn("Quest Box", q["reward"], where)
                self.assertIn(f"- **Quest on boarding — {q['title']}:**", res.markdown, where)
                self.assertIn(f"{q['title']}, {t.name}", suggested, where)

    # -- the floor's systems ----------------------------------------------------

    @staticmethod
    def _section(md, head):
        start = md.index(head)
        nxt = md.find("\n## ", start + 1)
        return md[start:nxt if nxt != -1 else len(md)]

    def test_the_drug_has_a_name_and_a_supplier_every_five_stops(self):
        for size, seed, res in self._floors(seeds=range(1, 4)):
            fl, md = res.floor, res.markdown
            self.assertIn(fl.supplier, mg.SUPPLIERS4)
            self.assertIn("Rev-Up Amazing Cure-All Vitamin Immunity Shot", md)
            self.assertIn(f"handed out by the {fl.supplier['name']} at every exit-only cavern", md)
            supply = self._section(md, "## Withdrawal")
            self.assertIn("**The supply:** every fifth station is an exit-only cavern", supply)
            self.assertIn(f"**{fl.supplier['name']}** (L{fl.supplier['level'][0]}", supply)
            self.assertIn(fl.supplier["if_attacked"], supply)

    def test_435_has_its_non_combatants(self):
        for size, seed, res in self._floors(seeds=range(1, 4)):
            fl = res.floor
            self.assertIn(fl.hr, mg.HR4)
            tail = self._section(res.markdown, "## The Tail")
            entry = tail[tail.index("### 435"):tail.index("### 436")]
            self.assertIn(f"the **{fl.hr['name']}**", entry)
            self.assertIn("Non-combatants: they do not fight.", entry)

    def test_station_75_is_the_depot(self):
        """Carts at five times a train's speed, in the book's own figure, so their
        times are the travel model's divided by five."""
        for size, seed, res in self._floors(seeds=range(1, 4)):
            fl, md = res.floor, res.markdown
            lo, hi = mg.DEPOT4["carts"]
            self.assertTrue(lo <= fl.depot["carts"] <= hi)
            for key, bank in (("repair", "repair_crews"), ("security", "security"),
                              ("controls", "controls"), ("state", "states")):
                self.assertIn(fl.depot[key], mg.DEPOT4[bank])
            depot = self._section(md, "## Station 75: the Interdiction Depot")
            out = mg.duration_text(mg.f4_ride_minutes(75, 435) / 5)
            back = mg.duration_text(mg.f4_ride_minutes(11, 75) / 5)
            self.assertIn(f"from 75 out to 435 in {out}, or back to the yard in {back}", depot)
            self.assertIn(fl.depot["controls"], depot)
            self.assertIn("**The scoop:**", depot)
            self.assertEqual(md.count("**75** — INTERDICTION DEPOT"), len(fl.lines))
            self.assertIn(f"the interdiction depot, {fl.depot['carts']} repair carts", md)
        svg = mg.generate(4, "small", 7).svgs[0][1]
        self.assertIn("DEPOT 75", svg)
        self.assertIn("Interdiction depot 75", svg)

    def test_derailments_and_blowouts_are_possible(self):
        rules = self._section(mg.generate(4, "small", 1, images=False).markdown, "## Floor Rules")
        self.assertIn("A train can be derailed", rules)
        self.assertIn("builds pressure until it blows", rules)

    def test_the_tail_says_how_to_fight_the_mimic_and_fall_in_the_abyss(self):
        for size, seed, res in self._floors(seeds=range(1, 4)):
            fl = res.floor
            self.assertIn(fl.mimic_spawn, mg.MIMIC_MINIONS4)
            tail = self._section(res.markdown, "## The Tail")
            mimic = tail[tail.index("### 433"):tail.index("### 435")]
            self.assertIn("blunt weapons and spells do nothing", mimic)
            self.assertIn("more than half of it has been cut away", mimic)
            self.assertIn(fl.mimic_spawn["name"], mimic)
            self.assertIn(fl.mimic_tell, mimic)
            abyss = tail[tail.index("### 436"):]
            self.assertIn("stray too far from the catwalk and the pit pulls you down", abyss)

    def test_every_yard_a_line_runs_from_is_described(self):
        for size, seed, res in self._floors():
            fl, where = res.floor, f"{size} seed {seed}"
            letters = sorted({ln.yard for ln in fl.lines})
            self.assertEqual(sorted(fl.yards["features"]), letters, where)
            features = list(fl.yards["features"].values())
            self.assertEqual(len(set(features)), len(features), where)
            self.assertIn(fl.yards["supervisor"], mg.YARDS4["supervisors"])
            yards = self._section(res.markdown, "## The Train Yards")
            for letter in letters:
                users = [ln.color for ln in fl.lines if ln.yard == letter]
                self.assertIn(f"- **Yard {letter}** (", yards, where)
                for color in users:
                    line = next(ln for ln in yards.splitlines()
                                if ln.startswith(f"- **Yard {letter}** ("))
                    self.assertIn(color, line, where)

    def test_the_special_lines_and_a_sigil_at_every_ghoul_station(self):
        for size, seed, res in self._floors():
            fl, where = res.floor, f"{size} seed {seed}"
            around = self._section(res.markdown, "## Getting Around")
            for key in ("backward", "staff", "backward_how", "staff_how"):
                self.assertIn(fl.special_lines[key], mg.SPECIAL_LINES4[key])
            self.assertIn("the only train that runs the other way", around, where)
            self.assertIn("is for the staff only", around, where)
            # Compared as templates: the same sentence with five different ghouls
            # in it still reads as the same sigil five times.
            forms = {gs["sigil"].replace(gs["ghoul"]["name"], "{ghoul}")
                     for gs in fl.ghoul_stations}
            self.assertEqual(len(forms), 5, f"{where}: {forms}")
            self.assertTrue(forms <= set(mg.SIGILS4), where)
            for gs in fl.ghoul_stations:
                self.assertNotIn("{", gs["sigil"], where)
                self.assertIn(f"- **Sigil:** {gs['sigil']}", res.markdown, where)
            self.assertIn("Control sigils are glowing circles", res.markdown, where)

    # -- the tail ---------------------------------------------------------------

    def test_station_435_is_on_the_map_and_in_the_text(self):
        for size in ("small", "medium", "large"):
            res = mg.generate(4, size, 4242)
            md, svg = res.markdown, res.svgs[0][1]
            self.assertIn("### 435 — End of the Line", md)
            self.assertEqual(md.count("**435** — END OF THE LINE"), len(res.floor.lines),
                             f"{size}: 435 missing from a line's stations")
            self.assertIn("END OF LINE", svg)
            self.assertIn("End of the line 435", svg)
            self.assertEqual(svg.count(">435<"), len(res.floor.lines),
                             f"{size}: 435 not numbered on every line")

    def test_the_keys_and_the_hat_are_left_out(self):
        """Both spoil the books. Neither the rule nor the Abyss summary says what
        opens the portals there."""
        for size, seed, res in self._floors(seeds=range(1, 5)):
            md = mg.floor_body(res.markdown)
            for phrase in ("conductor's hat", "souvenir hat", "Engineer keys open"):
                self.assertNotIn(phrase, md, f"{size} seed {seed}")


class Floor5BossTest(unittest.TestCase):
    """Floor 5's bosses on the books' tiers. A quadrant's boss is its castle's
    ruler: a borough boss (the books field them at L52-55), or a city boss (L82) in
    the castle meant to fall last, so one to a bubble. Harder castles keep borough
    bosses besides, and killing one of those takes nothing. The rules say what a
    popped bubble opens: the way out, not the way into another."""

    GUARDIANS = {"Easy": (0, 0), "Moderate": (0, 1), "Hard": (1, 1), "Brutal": (1, 2)}

    @classmethod
    def setUpClass(cls):
        cls.floors = [mg.generate(5, size, seed, images=False)
                      for size in ("small", "medium", "large") for seed in range(1, 13)]

    def test_each_ruler_is_a_borough_or_a_city_boss(self):
        for res in self.floors:
            fl = res.floor
            tiers = []
            for q in fl.quadrants:
                c, where = q.castle, f"{fl.size} seed {fl.seed}: {q.name}"
                tiers.append(c.boss.tier)
                if c.difficulty == "Brutal":
                    self.assertEqual(c.boss.tier, "city", where)
                    self.assertTrue(78 <= c.boss.level <= 84, where)
                else:
                    self.assertEqual(c.boss.tier, "borough", where)
                    self.assertGreater(c.boss.level, q.level_range[1], where)
                self.assertIn(f"({c.boss.tier} boss, Level {c.boss.level})", res.markdown)
            self.assertEqual(tiers.count("city"), 1, f"{fl.size} seed {fl.seed}")
            self.assertNotIn("castle", {b.tier for b in mg.floor_bosses(fl)})
        self.assertNotIn("castle", mg.MOB_BUILDS["tier_points"])

    def test_harder_castles_keep_guardians(self):
        counts = collections.Counter()
        for res in self.floors:
            fl, md = res.floor, res.markdown
            where = f"{fl.size} seed {fl.seed}"
            rulers = {q.castle.ruler.split()[0] for q in fl.quadrants}
            names = []
            beasts = {b.name for b in mg.floor_bestiary(fl)}
            bosses = mg.floor_bosses(fl)
            for q in fl.quadrants:
                c = q.castle
                lo, hi = self.GUARDIANS[c.difficulty]
                self.assertTrue(lo <= len(c.guardians) <= hi, f"{where}: {c.difficulty}")
                counts[c.difficulty] += len(c.guardians)
                for g in c.guardians:
                    self.assertEqual(g.tier, "borough", where)
                    self.assertTrue(q.level_range[1] < g.level < c.boss.level, where)
                    self.assertIn(g.chamber, mg.GUARDIAN_POSTS)
                    self.assertIn(f"- **Guardian:** {g.name} (borough boss, Level {g.level}). "
                                  f"{g.chamber} Killing it doesn't take the castle.", md)
                    self.assertFalse(rulers & set(g.name.replace(",", "").split()),
                                     f"{where}: {g.name} shares a ruler's first name")
                    self.assertIn(g.name, beasts, where)
                    self.assertIn(g, bosses, where)
                names += [c.boss.name] + [g.name for g in c.guardians]
            self.assertEqual(len(set(names)), len(names), where)
        self.assertEqual(counts["Easy"], 0)
        self.assertTrue(counts["Moderate"] and counts["Hard"] and counts["Brutal"], counts)

    def test_a_popped_bubble_lets_crawlers_out_not_in(self):
        for res in self.floors[:3]:
            md = res.markdown
            rules = md[md.index("## Floor Rules"):md.index("## Quadrants")]
            self.assertIn("may not enter another until it has popped too", rules)
            self.assertIn("no time limit on them", rules)
            self.assertIn("killing them takes nothing", rules)
            self.assertNotIn("lacuna", md)


class Floor5QuestTest(unittest.TestCase):
    """What a bubble's factions ask of the crawlers, and the achievements a bubble
    invites. Both are labelled suggested ideas, not a definitive list. A large
    bubble posts nine quests, the books' count for the Fifth Floor, and every
    name a quest fills in is something really on that floor."""

    PER_FACTION = {"small": 1, "medium": 2, "large": 3}
    NOTE = "Suggested ideas, not a definitive list"
    # Achievements the books hand out on the Fifth Floor, with their rewards.
    BOOKS = {"Quarry Sees Another Spring": "Gold Venison Box",
             "Cannonball!": "Silver Skydiver's Box",
             "Milquetoast": "Silver Pacifist's Box",
             "I was in the Pool!": "Bronze I'm Wet Box",
             "Rock Bottom": "Gold I'm Wet Box",
             "Soft Vore": "Platinum Spicy Box",
             "Disarming Personality": "Silver Savage Box"}

    @classmethod
    def setUpClass(cls):
        cls.floors = [mg.generate(5, size, seed, images=False)
                      for size in ("small", "medium", "large") for seed in range(1, 9)]

    @staticmethod
    def _pattern(template):
        """A template as a regex: each {slot} a named group, repeats back-referenced."""
        seen, out = set(), []
        for literal, slot, _, _ in string.Formatter().parse(template):
            out.append(re.escape(literal))
            if slot:
                out.append(f"(?P={slot})" if slot in seen else f"(?P<{slot}>.+?)")
                seen.add(slot)
        return re.compile("^" + "".join(out) + "$")

    def _filled(self, q):
        """The template a quest came from, and what filled its slots."""
        for t in mg.QUESTS5["templates"]:
            mt = self._pattern(t["title"]).match(q.title)
            mo = self._pattern(t["objective"]).match(q.objective)
            if mt and mo:
                return t, {**mt.groupdict(), **mo.groupdict()}
        self.fail(f"no template makes {q.title!r}: {q.objective}")

    def test_each_faction_posts_its_quests(self):
        for res in self.floors:
            fl, md = res.floor, res.markdown
            where = f"{fl.size} seed {fl.seed}"
            for f in fl.factions:
                mine = [q for q in fl.quests if q.giver.endswith(f", of the {f.name}")]
                self.assertEqual(len(mine), self.PER_FACTION[fl.size], f"{where}: {f.name}")
            self.assertEqual(len(fl.quests), 3 * self.PER_FACTION[fl.size])
            titles = [q.title for q in fl.quests]
            self.assertEqual(len(set(titles)), len(titles), f"{where}: {titles}")
            section = md[md.index("## Quests"):md.index("## DM Material")]
            self.assertIn(self.NOTE, section)
            boxed = {s.source for s in mg.floor_box_suggestions(fl) if s.kind == "quest"}
            for q in fl.quests:
                text = f"{q.title} {q.objective} {q.reward} {q.flavor}"
                self.assertNotIn("{", text, where)
                for f in fl.factions:                    # "the Brine Knights' lands"
                    if f.name.endswith("s"):
                        self.assertNotIn(f"{f.name}'s", text, f"{where}: {text}")
                self.assertIn("Quest Box", q.reward, where)
                self.assertIn(f"- **{q.giver.split(',')[0]} — {q.title}.** {q.objective} "
                              f"Reward: {q.reward}.", section)
                self.assertIn(q.title, boxed, where)
        self.assertEqual(len(self.floors[-1].floor.quests), 9, "a large bubble posts the books' nine")

    def test_a_quest_names_what_is_really_on_the_floor(self):
        seen = set()
        for res in self.floors:
            fl = res.floor
            where = f"{fl.size} seed {fl.seed}"
            castles = {q.castle.name: q for q in fl.quadrants}
            factions = {f.name: f for f in fl.factions}
            homes = {q.name: q.settlement.split(",")[0] for q in fl.quadrants}
            for q in fl.quests:
                t, slots = self._filled(q)
                seen.add(t["title"])
                own = q.giver.split(", of the ")[1]
                giver = q.giver.split(",")[0]
                if "giver" in slots:
                    self.assertEqual(slots["giver"], giver, where)
                if "faction" in slots:
                    self.assertEqual(slots["faction"], own, where)
                if "rival" in slots:
                    self.assertIn(slots["rival"], factions, where)
                    self.assertNotEqual(slots["rival"], own, f"{where}: a faction is its own rival")
                if "rival_home" in slots:
                    self.assertEqual(slots["rival_home"],
                                     homes[factions[slots["rival"]].home], where)
                if "npc" in slots:
                    self.assertNotEqual(slots["npc"], giver, where)
                if "castle" in slots:
                    quad = castles[slots["castle"]]
                    if "ruler" in slots:
                        self.assertEqual(slots["ruler"], quad.castle.ruler, where)
                    if "quadrant" in slots:
                        self.assertEqual(slots["quadrant"], quad.name, where)
                if "mob" in slots:
                    self.assertEqual(slots["mob"],
                                     next(x.mob.name for x in fl.quadrants
                                          if x.name == slots["quadrant"]), where)
        self.assertEqual(seen, {t["title"] for t in mg.QUESTS5["templates"]},
                         "a quest template never came up, so its slots went unchecked")

    def test_the_achievements_a_bubble_invites(self):
        for res in self.floors[:3]:
            fl, md = res.floor, res.markdown
            section = md[md.index("## Achievements"):md.index("## Bestiary")]
            self.assertIn(self.NOTE, section)
            names = [a[0] for a in fl.achievements]
            self.assertEqual(len(set(names)), len(names))
            for name, reward in self.BOOKS.items():
                self.assertIn([name, reward], [[a[0], a[2]] for a in fl.achievements])
            boxed = {s.source for s in mg.floor_box_suggestions(fl) if s.kind == "achievement"}
            for name, trigger, reward in fl.achievements:
                self.assertIn(f"- **{name}:** {trigger} → {reward}", section)
                if reward.startswith("flavor only"):
                    self.assertNotIn(name, boxed)
                else:
                    self.assertRegex(reward, r"^(Bronze|Silver|Gold|Platinum|Legendary|Celestial) ")
                    self.assertIn(name, boxed)
            books = len(mg.ACHIEVEMENTS5["books"])
            self.assertIn(f"The first {books} are ones the books hand out", section)
            self.assertEqual(names[:books], [a[0] for a in mg.ACHIEVEMENTS5["books"]])


class Floor5SiteTest(unittest.TestCase):
    """Where a bubble's crawlers start. The books share them out at random, about
    150 to a bubble, and nobody leaves their quadrant until its castle falls, so
    each quadrant says how many it holds; the Air quadrant alone had four places
    a crawler could start, each somebody's home. Other Crawlers says which
    quadrant, and which site, each group is in."""

    SITES = {"small": 2, "medium": 3, "large": 4}

    @classmethod
    def setUpClass(cls):
        cls.floors = [mg.generate(5, size, seed, images=False)
                      for size in ("small", "medium", "large") for seed in range(1, 13)]

    def test_the_crawlers_are_shared_out_between_the_quadrants(self):
        spreads = []
        for res in self.floors:
            fl, md = res.floor, res.markdown
            where = f"{fl.size} seed {fl.seed}"
            counts = [q.crawlers for q in fl.quadrants]
            self.assertEqual(sum(counts), fl.crawlers, where)
            self.assertGreaterEqual(min(counts), 15, where)
            spreads.append(max(counts) - min(counts))
            split = ", ".join(f"{q.name} {q.crawlers}" for q in fl.quadrants)
            self.assertIn(f"about {fl.crawlers} ({split})", md)
            for q in fl.quadrants:
                self.assertIn(f"- **Crawlers:** about {q.crawlers} start in this quadrant. "
                              f"None of them can leave it until its castle falls.", md)
        self.assertGreater(max(spreads), 15, "every bubble shared out evenly")

    def test_no_quadrant_or_site_is_left_nearly_empty(self):
        """Fifteen crawlers at least to a quadrant and two to a site, however
        unevenly the shares fall: forced here with weights far wider than the
        bubble's own, which seldom push a share that low by themselves."""
        gen = mg.Generator5("large", 1, None)
        gen.cfg = dict(gen.cfg, share_weight=(0.05, 3.0))
        for total in (130, 150, 170):
            for _ in range(200):
                shares = gen._split(total, 4, gen.cfg["quadrant_min"])
                self.assertEqual(sum(shares), total)
                self.assertGreaterEqual(min(shares), 15, shares)
                sites = gen._split(min(shares), 4, gen.cfg["site_min"])
                self.assertEqual(sum(sites), min(shares))
                self.assertGreaterEqual(min(sites), 2, sites)

    def test_each_quadrant_has_its_starting_sites(self):
        for res in self.floors:
            fl, md = res.floor, res.markdown
            where = f"{fl.size} seed {fl.seed}"
            names = []
            for q in fl.quadrants:
                self.assertEqual(len(q.sites), self.SITES[fl.size], f"{where}: {q.name}")
                self.assertEqual(sum(s.crawlers for s in q.sites), q.crawlers, where)
                self.assertTrue(all(s.crawlers >= 2 for s in q.sites), where)
                kinds = [s.kind for s in q.sites]
                if q.faction:
                    home = next(s for s in q.sites if s.kind == "settlement")
                    self.assertEqual(kinds.count("settlement"), 1, where)
                    self.assertEqual(home.name, q.settlement.split(",")[0], where)
                    self.assertIn(f"{q.faction.name} settlement with a safe-room inn", home.note)
                else:
                    self.assertNotIn("settlement", kinds, where)
                    self.assertIn("ruin", kinds, f"{where}: a factionless quadrant with no safe room")
                for s in q.sites:
                    if s.kind == "mob":
                        holder = next(m for m, _ in q.hostile if m.name == s.mob)
                        self.assertTrue(s.name.endswith(f" of the {holder.noun}"), s.name)
                        self.assertIn(holder.name, s.note.replace("'", ""), where)
                    if s.kind == "ruin":
                        self.assertIn("safe room", s.note, where)
                    self.assertIn(f"  - **{s.name[:1].upper() + s.name[1:]}** (about "
                                  f"{s.crawlers} crawlers): {s.note}.", md)
                names += [s.name for s in q.sites]
            self.assertEqual(len(set(names)), len(names), where)
            start = next(q for q in fl.quadrants if q.name == fl.suggested_start)
            gentle = next(s for s in start.sites if s.kind != "mob")
            self.assertIn(f"(the easiest castle), at {gentle.name}.", md)
        kinds = collections.Counter(s.kind for res in self.floors
                                    for q in res.floor.quadrants for s in q.sites)
        self.assertTrue(kinds["mob"] and kinds["ruin"] and kinds["settlement"], kinds)

    def test_other_crawlers_say_where_each_group_is(self):
        placed = 0
        for res in self.floors:
            fl = res.floor
            places = {f"in the {q.name} quadrant, near {s.name}"
                      for q in fl.quadrants for s in q.sites}
            used = []
            for t in fl.other_crawlers:
                hits = [p for p in places if p in t.status]
                self.assertEqual(len(hits), 1, f"{fl.size} seed {fl.seed}: {t.status}")
                used += hits
                placed += 1
            if len(used) <= len(places):
                self.assertEqual(len(set(used)), len(used),
                                 f"{fl.size} seed {fl.seed}: two groups at one site")
        self.assertGreater(placed, 30)


class Floor5MobTest(unittest.TestCase):
    """A quadrant holds several kinds of mob -- the books field three to five, at
    L34-41 -- and one white-tagged mob that won't attack unless provoked, like the
    books' Big Boy Blues: a hazard, not an enemy. The castle's garrison stays the
    quadrant's first mob, and each mob ground belongs to somebody different while
    there are mobs to go round."""

    MOBS = {"small": 2, "medium": 3, "large": 4}

    @classmethod
    def setUpClass(cls):
        cls.floors = [mg.generate(5, size, seed, images=False)
                      for size in ("small", "medium", "large") for seed in range(1, 13)]

    def test_each_quadrant_has_several_mobs(self):
        lo, hi = mg.FLOOR5_CFG["mob_band"]
        for res in self.floors:
            fl, md = res.floor, res.markdown
            where = f"{fl.size} seed {fl.seed}"
            beasts = {(b.name, b.band) for b in mg.floor_bestiary(fl)}
            for q in fl.quadrants:
                mobs = q.hostile
                self.assertEqual(len(mobs), self.MOBS[fl.size], f"{where}: {q.name}")
                self.assertIs(mobs[0][0], q.mob, "the garrison is no longer the first mob")
                bank = mg.QUADRANT_DATA[q.name]["mobs"]
                for attr in ("noun", "adj", "quirk"):
                    values = [getattr(m, attr) for m, _ in mobs]
                    self.assertEqual(len(set(values)), len(values), f"{where}: {attr}s {values}")
                for m, (a, b) in mobs:
                    self.assertIn(m.noun, bank, where)
                    self.assertTrue(lo <= a < b <= hi, f"{where}: {m.name} {a}-{b}")
                    self.assertIn((m.name, (a, b)), beasts, where)
                for om in q.other_mobs:
                    a, b = om.level_range
                    self.assertIn(f"  - **{om.mob.name}**, Level {a}–{b}. {om.mob.quirk}", md)

    def test_each_quadrant_has_a_white_tagged_mob(self):
        lo, hi = mg.FLOOR5_CFG["mob_band"]
        seen = set()
        for res in self.floors:
            fl, md = res.floor, res.markdown
            where = f"{fl.size} seed {fl.seed}"
            beasts = {(b.name, b.where) for b in mg.floor_bestiary(fl)}
            for q in fl.quadrants:
                w = q.white
                self.assertIn([w.name, w.hazard], mg.QUADRANT_DATA[q.name]["white"], where)
                a, b = w.level_range
                self.assertTrue(lo <= a < b <= hi, where)
                self.assertIn(f"- **White-tagged:** {w.name}, Level {a}–{b}. They won't attack "
                              f"unless provoked. {w.hazard}", md)
                self.assertIn((w.name, f"the {q.name} quadrant, white-tagged"), beasts, where)
                self.assertNotIn(w.name, [m.name for m, _ in q.hostile], where)
                seen.add(w.name)
        self.assertGreater(len(seen), 8, "the white-tagged banks hardly come up")

    def test_mob_grounds_belong_to_different_mobs(self):
        shared = 0
        for res in self.floors:
            fl = res.floor
            for q in fl.quadrants:
                holders = [s.mob for s in q.sites if s.kind == "mob"]
                names = {m.name for m, _ in q.hostile}
                self.assertTrue(set(holders) <= names, f"{fl.size} seed {fl.seed}: {holders}")
                if len(holders) <= len(names):
                    self.assertEqual(len(set(holders)), len(holders),
                                     f"{fl.size} seed {fl.seed}: one mob holds two grounds")
                shared += len(set(holders) - {q.mob.name})
        self.assertGreater(shared, 10, "mob grounds never go to the quadrant's other mobs")


class SystemsTest(unittest.TestCase):
    """What's switched on: the dungeon's systems by the floor they arrive on.

    The floors are the DCC Database's, written out here rather than read back
    from the bank, so a bank edited to put guilds on Floor 2 fails."""

    ARRIVES = {"gold": 2, "ratings and the shows": 2, "safe rooms close early": 2,
               "races and classes": 3, "stat points": 3, "marketplaces": 3,
               "Desperado Club": 3, "Club Vanquisher": 3,
               "sponsors": 4, "the leaderboard": 4, "personal spaces": 4,
               "a second sponsor": 5, "a third sponsor": 6, "guilds": 6,
               "parties capped at thirty": 6, "Desperado Club, second level": 6,
               "class specialization": 6, "Desperado Club, third level": 9,
               "a second specialization": 9}
    LAST = {"free beds": 2, "Game Guides": 3}
    # Things that happen on their floor rather than staying on.
    ONCE = {"races and classes", "sponsor bidding", "a second sponsor", "a third sponsor",
            "Desperado Club, second level", "class specialization",
            "Desperado Club, third level", "a second specialization"}

    @classmethod
    def setUpClass(cls):
        cls.md = {f: mg.generate(f, "small", 4242, images=False).markdown for f in mg.FLOORS}

    def _section(self, f):
        md = self.md[f]
        return md[md.index("## What's Switched On"):md.index("## Loot Boxes")]

    @staticmethod
    def _head(name):
        return name[0].upper() + name[1:]

    def test_each_system_arrives_on_its_books_floor(self):
        by_name = {s["name"]: s for s in mg.SYSTEMS}
        for name, floor in self.ARRIVES.items():
            self.assertIn(name, by_name)
            self.assertEqual(by_name[name]["from"], floor, name)
        for name, floor in self.LAST.items():
            self.assertEqual(by_name[name].get("until"), floor, name)
        self.assertEqual({s["name"] for s in mg.SYSTEMS if s.get("once")}, self.ONCE)

    def test_every_floor_says_what_is_new_off_and_still_on(self):
        for f in mg.FLOORS:
            sec = self._section(f)
            new = [s for s in mg.SYSTEMS if s["from"] == f]
            for s in mg.SYSTEMS:
                bullet = f"- **{self._head(s['name'])}:** {s['text']}."
                self.assertEqual(bullet in sec, s in new, f"floor {f}: {s['name']}")
                off = f"- **{self._head(s['name'])}:** {s.get('off')}."
                self.assertEqual(off in sec, s.get("until") == f - 1, f"floor {f}: {s['name']}")
            still = sec[sec.index("**Still on"):] if "**Still on" in sec else ""
            for s in mg.SYSTEMS:
                ongoing = (s["from"] < f and f <= self.LAST.get(s["name"], 99)
                           and s["name"] not in self.ONCE)
                listed = re.search(rf"- Floor {s['from']}: .*\b{re.escape(s['name'])}[,.]", still)
                self.assertEqual(bool(listed), ongoing, f"floor {f}: {s['name']}")

    def test_the_next_floor_is_previewed_when_there_is_one(self):
        for f in mg.FLOORS:
            sec = self._section(f)
            coming = [s["name"] for s in mg.SYSTEMS if s["from"] == f + 1]
            if coming and f + 1 in mg.FLOORS:
                self.assertIn(f"**Next floor:** {', '.join(coming)}.", sec)
            else:
                self.assertNotIn("**Next floor:**", sec, f"floor {f}")

    def test_the_section_is_the_floors_not_the_seeds(self):
        for f in (1, 4, 9):
            other = mg.generate(f, "medium", 77, images=False).markdown
            here = other[other.index("## What's Switched On"):other.index("## Loot Boxes")]
            self.assertEqual(here, self._section(f))

    def test_it_is_shared_matter_not_the_floors_own(self):
        """Every floor's Other Crawlers section comes first and would cut the body
        off before this one anyway, so it is taken out: the systems section has to
        stop the body on its own."""
        for f in mg.FLOORS:
            md = self.md[f].replace("## Other Crawlers", "## Elsewhere")
            md = md.replace("## Achievements", "## Elsewhere Too")
            self.assertNotIn("What's Switched On", mg.floor_body(md), f"floor {f}")

    def test_there_is_no_blood_bar(self):
        """An earlier session gave Floors 6 and 8 a blood bar that drained in safe
        rooms. Nothing in the DCC Database has one and the Floor 6 story does not
        mention it, so it is gone: from every write-up and every word bank."""
        bar = re.compile(r"blood[- ]?bar", re.IGNORECASE)
        for f, md in self.md.items():
            self.assertFalse(bar.search(md), f"floor {f}")
        data = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        for path in glob.glob(os.path.join(data, "*.json")):
            with open(path, encoding="utf-8") as fh:
                self.assertFalse(bar.search(fh.read()), os.path.basename(path))


class BestiaryTest(unittest.TestCase):
    """Mobs statted from their level. The point budgets are the table's own
    decisions, written out here: a crawler gains 3 points a level, an ordinary
    mob 1, a neighborhood boss 2, a borough boss 3, a city boss 4."""

    CRAWLER = 3
    TIERS = {"mob": 1, "neighborhood": 2, "borough": 3, "city": 4}
    NAMES = ["Feral Goblins", "Tunnel Trolls", "Goblin Shamankas", "Fire Ants",
             "Freight Ghouls", "Ticket Inspectors", "Stilt Clowns", "Llamas"]

    @classmethod
    def setUpClass(cls):
        cls.res = {f: mg.generate(f, "small", 4242, images=False) for f in mg.FLOORS}

    def test_crawlers_gain_three_points_a_level(self):
        self.assertEqual(mg.CRAWLER_STATS["knobs"]["points_per_level"], self.CRAWLER)
        stats = self.res[3].markdown[self.res[3].markdown.index("## Crawler Stats"):]
        self.assertIn("**3 stat points per level**", stats)
        self.assertIn("then 3 points per level", stats)

    def test_points_by_tier(self):
        for tier, per in self.TIERS.items():
            for level in (1, 7, 20, 55):
                for name in self.NAMES:
                    b = mg.build_mob(name, level, tier)
                    self.assertEqual(sum(b.stats.values()), 5 * 4 + per * level,
                                     f"{name} {tier} L{level}")
                    self.assertTrue(all(v >= 1 for v in b.stats.values()))
                    if not b.poor:
                        self.assertTrue(all(v >= 4 for v in b.stats.values()), b.stats)

    def test_what_follows_from_the_stats(self):
        for name in self.NAMES:
            for tier, per in self.TIERS.items():
                b = mg.build_mob(name, 30, tier)
                self.assertEqual(b.hp, b.stats["CON"] * 15)
                self.assertEqual(b.mp, b.stats["INT"])
                self.assertEqual(b.melee, b.stats["STR"] // 3)
                self.assertEqual(b.tricks, b.stats["INT"] // mg.MOB_BUILDS["trick_cost"])
                self.assertEqual(b.even_for, max(1, round(per * 30 / self.CRAWLER)))

    def _average(self, name, stat, description="", n=60):
        """A stat's average share of the build over n levels, leaving out the
        poorly built: they ignore type and description by design."""
        shares = []
        for level in range(20, 20 + n):
            b = mg.build_mob(name, level, description=description)
            if not b.poor:
                shares.append(b.stats[stat] / sum(b.stats.values()))
        return sum(shares) / len(shares)

    def test_the_type_weights_the_build(self):
        """On average, over sixty levels: a brute leads with Strength and
        Constitution, a caster with Intelligence, a swarm and a skirmisher with
        Dexterity, a charmer with Charisma."""
        leads = {"Tunnel Trolls": ("STR", "CON"), "Goblin Shamankas": ("INT",),
                 "Fire Ants": ("DEX",), "Feral Goblins": ("DEX",), "Stilt Clowns": ("CHA",)}
        for name, top in leads.items():
            shares = {s: self._average(name, s) for s in mg.STAT_ORDER}
            best = sorted(shares, key=shares.get, reverse=True)[:len(top)]
            self.assertEqual(set(best), set(top), f"{name}: {shares}")

    def test_the_description_leans_it(self):
        plain = self._average("Llamas", "CON")
        fat = self._average("Llamas", "CON", "Corpulent")
        self.assertGreater(fat, plain * 1.2, "a corpulent mob is no sturdier")
        plain = self._average("Llamas", "INT")
        sly = self._average("Llamas", "INT", "Bureaucratic. Set crude tripwire traps.")
        self.assertGreater(sly, plain * 1.2, "a trap-setting bureaucrat is no cleverer")

    def test_some_mobs_are_poorly_built(self):
        poor = 0
        builds = [mg.build_mob(f"Probe {i}", 25) for i in range(2000)]
        for b in builds:
            if not b.poor:
                continue
            poor += 1
            pattern = next(p for p in mg.MOB_BUILDS["poor_builds"] if p["name"] == b.poor)
            for s in pattern["dump"]:
                self.assertEqual(b.stats[s], 1, f"{b.poor}: {b.stats}")
        self.assertTrue(0.07 <= poor / len(builds) <= 0.19, poor)

    def test_the_head_noun_decides_the_type(self):
        cases = {"Goblin Shamankas": "caster", "Tunnel Trolls": "brute", "Mole Rats": "swarm",
                 "Ratcatchers": "worker", "Sleeper-Car Wraiths": "undead",
                 "Goblin Bomb Bards": "charmer", "Llamas": "beast"}
        for name, kind in cases.items():
            self.assertEqual(mg.mob_kind(name)[0], kind, name)
        effigy = "an 18-foot stone effigy animated by the collective spite of the librarians"
        self.assertEqual(mg.mob_kind(effigy, "brute", sentence=True)[0], "brute")

    def test_a_build_is_the_mobs_own(self):
        a = mg.build_mob("Feral Goblins", 12)
        self.assertEqual(a, mg.build_mob("Feral Goblins", 12))
        roller = random.getstate()
        mg.build_mob("Feral Goblins", 13)
        self.assertEqual(random.getstate(), roller, "statting touched the global random")

    def test_every_floor_with_mobs_has_a_bestiary(self):
        for f, res in self.res.items():
            beasts = mg.floor_bestiary(res.floor)
            md = res.markdown
            self.assertEqual("## Bestiary" in md, bool(beasts), f"floor {f}")
            if not beasts:
                continue
            sec = md[md.index("## Bestiary"):md.index("## What's Switched On")]
            main = sec.split("\n### ")[0]
            rows = [ln for ln in main.splitlines() if ln.startswith("| ") and "---" not in ln][1:]
            self.assertEqual(len(rows), len(beasts), f"floor {f}")
            for row, b in zip(rows, beasts):
                cells = [c.strip() for c in row.strip("|").split("|")]
                self.assertEqual(cells[0], b.name)
                self.assertEqual([int(c) for c in cells[4:9]], [b.stats[s] for s in mg.STAT_ORDER])
                self.assertEqual(int(cells[9].replace(",", "")), b.hp)
                self.assertEqual(int(cells[12]), b.even_for)
            for b in beasts:
                if b.poor:
                    self.assertIn(f"- **{b.name}:** {b.poor}.", sec)
        self.assertNotIn("## Bestiary", self.res[10].markdown, "the race has no mobs")

    def test_the_bestiary_has_every_mob_the_floor_names(self):
        fl = self.res[1].floor
        names = {b.name for b in mg.floor_bestiary(fl)}
        for nb in fl.neighborhoods:
            self.assertIn(nb.mob.name, names)
            self.assertIn(nb.boss.name, names)
        self.assertIn(fl.janitor.name, names)
        fl4 = self.res[4].floor
        beasts4 = {b.name: b for b in mg.floor_bestiary(fl4)}
        for ln in fl4.lines:
            self.assertIn(ln.line_mob.name, beasts4)
        self.assertEqual((beasts4["Station Mimic"].tier, beasts4["Station Mimic"].level), ("city", 80))
        self.assertIn(fl4.supplier["name"], beasts4)
        for c in self.res[3].floor.cells:
            if getattr(c, "mob", None):
                self.assertIn(c.mob.name, {b.name for b in mg.floor_bestiary(self.res[3].floor)})

    def test_it_is_shared_matter_not_the_floors_own(self):
        for f, res in self.res.items():
            md = res.markdown
            for head in ("## Other Crawlers", "## Achievements"):
                md = md.replace(head, "## Elsewhere")
            self.assertNotIn("## Bestiary", mg.floor_body(md), f"floor {f}")

    # -- bosses against a party -----------------------------------------------

    def _boss_rows(self, md):
        start = md.index("### Bosses against a party")
        sec = md[start:md.index("### You're going to die")]
        head = next(ln for ln in sec.splitlines() if ln.startswith("| Boss |"))
        levels = [int(x) for x in re.findall(r"Crawlers at L(\d+)", head)]
        rows = [ln for ln in sec.splitlines()
                if ln.startswith("| ") and "---" not in ln and not ln.startswith("| Boss |")]
        return sec, levels, rows

    def test_every_boss_is_measured_against_a_party(self):
        """Points over a crawler's points -- 20, plus 3 a level -- at an ordinary
        crawler's level, a leaderboard crawler's, and the boss's own; or one-shot,
        where its hit kills that crawler outright and no number of them will do."""
        for f, res in self.res.items():
            beasts = mg.floor_bestiary(res.floor)
            bosses = [b for b in beasts if b.tier != "mob"]
            md = res.markdown
            self.assertEqual("### Bosses against a party" in md, bool(bosses), f"floor {f}")
            if not bosses:
                continue
            sec, levels, rows = self._boss_rows(md)
            self.assertEqual(len(rows), len(bosses), f"floor {f}")
            for row, b in zip(rows, bosses):
                cells = [c.strip() for c in row.strip("|").split("|")]
                self.assertEqual(cells[0], b.name)
                self.assertEqual(int(cells[2].replace(",", "")), sum(b.stats.values()))
                for cell, level in zip(cells[3:6], levels + [b.level]):
                    want = sum(b.stats.values()) / (20 + self.CRAWLER * level)
                    if self._hit(b)[0] >= self._hp(level):
                        want = "one-shot"
                    else:
                        want = f"{want:.1f}"
                    self.assertEqual(cell, want, f"floor {f} {b.name} at L{level}")
            self.assertIn("leaves out is gear and class", sec)
            self.assertIn("a caster's Intelligence counts the same as a brawler's Strength", sec)

    def test_the_ordinary_crawler_is_where_the_floors_mobs_top_out(self):
        """The median band top, so one outlier -- Floor 4's mimic spawn at the end
        of the line -- does not set the level for the whole floor."""
        for f, res in self.res.items():
            beasts = mg.floor_bestiary(res.floor)
            if not any(b.tier != "mob" for b in beasts):
                continue
            ordinary, leader, read = mg.party_levels(f, beasts)
            tops = sorted(b.band[1] for b in beasts if b.tier == "mob")
            if tops:
                self.assertTrue(read)
                self.assertEqual(ordinary, tops[len(tops) // 2], f"floor {f}")
                self.assertLess(ordinary, max(tops) + 1)
            else:
                self.assertFalse(read)
                self.assertIn("a typical crawler this deep", res.markdown)
            self.assertGreater(leader, ordinary, f"floor {f}")
        four = mg.floor_bestiary(self.res[4].floor)
        spawn_top = max(b.band[1] for b in four if b.tier == "mob")
        self.assertLess(mg.party_levels(4, four)[0], spawn_top,
                        "the mimic's spawn set Floor 4's ordinary crawler")

    def test_the_leaderboard_crawler_is_carl_at_the_end_of_the_floors_book(self):
        """Book 1 is Floors 1 and 2, and Book N is Floor N+1 from there. Carl, on
        the leaderboard, ends Book 1 at level 13, Book 2 at 27, Book 3 at 41, Book 4
        at 47, Book 5 at 63 and Book 7 at 81 (the DCC Database's Carl page)."""
        carl = {2: 13, 3: 27, 4: 41, 5: 47, 6: 63, 8: 81}
        for floor, level in carl.items():
            self.assertEqual(mg.MOB_BUILDS["party_levels"][str(floor)][1], level,
                             f"floor {floor}")
        self.assertIn("41 to 47 across the Fifth", mg.CRAWLER_STATS["npc_note"])
        self.assertNotIn("twice", mg.CRAWLER_STATS["npc_note"])

    # -- you're going to die --------------------------------------------------
    # A full-strength hit is 5 a point of the stat behind it, a third of the 15 HP
    # a point of CON gives: STR, or INT for a trick if that is bigger and the mob
    # has 3 MP for one use. A crawler's CON is 4 plus a fifth of their points
    # (spread evenly) or half of them (a veteran's build).

    def _hit(self, b):
        trick = b.stats["INT"] if b.stats["INT"] >= 3 else 0
        if trick > b.stats["STR"]:
            return trick * 5, "trick"
        return b.stats["STR"] * 5, "STR"

    @staticmethod
    def _hp(level, half=False):
        spent = 3 * level
        return (4 + (spent // 2 if half else spent // 5)) * 15

    def _lives_from(self, hit, half=False):
        return next(lv for lv in range(1, 100000) if self._hp(lv, half) > hit)

    def test_a_hit_is_three_times_a_crawlers_con_to_kill(self):
        for name in self.NAMES:
            for tier in self.TIERS:
                for level in (1, 9, 40, 130):
                    b = mg.build_mob(name, level, tier)
                    self.assertEqual(b.hit, self._hit(b), f"{name} {tier} L{level}")
        for level in (1, 4, 5, 25, 105, 300):
            for half in (False, True):
                self.assertEqual(mg.crawler_hp(level, half), self._hp(level, half))
                con = self._hp(level, half) // 15
                # one-shot exactly when the stat behind the hit is 3x their CON
                for stat in (3 * con - 1, 3 * con):
                    self.assertEqual(stat * 5 >= mg.crawler_hp(level, half), stat >= 3 * con)
        for hit in (0, 59, 60, 61, 200, 2710):
            for half in (False, True):
                self.assertEqual(mg.lives_through(hit, half), self._lives_from(hit, half))
        # STR 12 hits for 60, exactly a level-1 crawler's HP: that is a kill
        exact = mg.MobBuild("Probe", "here", 8, (8, 8), "mob", "brute",
                            {"STR": 12, "DEX": 4, "CON": 4, "INT": 4, "CHA": 4})
        self.assertEqual(exact.hit, (self._hp(1), "STR"))
        self.assertTrue(exact.one_shots(1), "a hit equal to their HP left them alive")
        self.assertFalse(exact.one_shots(2))

    def test_every_floor_says_where_youre_going_to_die(self):
        """Every mob and boss, hardest hit first, with the level a crawler needs to
        live through it; and the floor's own cutoff, from its ordinary mobs."""
        for f, res in self.res.items():
            beasts = mg.floor_bestiary(res.floor)
            md = res.markdown
            self.assertEqual("### You're going to die" in md, bool(beasts), f"floor {f}")
            if not beasts:
                continue
            sec = md[md.index("### You're going to die"):md.index("## What's Switched On")]
            rows = [ln for ln in sec.splitlines()
                    if ln.startswith("| ") and "---" not in ln][1:]
            self.assertEqual(len(rows), len(beasts), f"floor {f}")
            # hardest hit first; a tie keeps the Bestiary's order (Floor 8 has the
            # same mob in two regions, at two levels)
            hardest = sorted(beasts, key=lambda b: -self._hit(b)[0])
            for row, b in zip(rows, hardest):
                cells = [c.strip() for c in row.strip("|").split("|")]
                hit, behind = self._hit(b)
                self.assertEqual(cells[:4], [b.name, str(b.level), f"{hit:,}", behind])
                for cell, half in ((cells[4], False), (cells[5], True)):
                    lv = self._lives_from(hit, half)
                    self.assertEqual(cell, "—" if lv == 1 else f"below L{lv}", cells[0])
            mobs = [b for b in beasts if b.tier == "mob"]
            if not mobs:
                self.assertNotIn("This floor's cutoff", sec)
                continue
            top = max(self._hit(b)[0] for b in mobs)
            cutoff = self._lives_from(top)
            if cutoff == 1:
                self.assertIn("**Nothing ordinary on this floor kills a crawler in one hit**", sec)
            else:
                self.assertIn(f"**This floor's cutoff is level {cutoff}.**", sec, f"floor {f}")
                self.assertIn(f"from level {self._lives_from(top, True)}.", sec)

    def test_the_shape_of_it(self):
        """An ordinary crawler lives through every ordinary mob on their floor --
        mobs gain a point a level to their three -- and the Floor 11 boss, meant to
        be deadly, kills an ordinary crawler there in one hit."""
        for f, res in self.res.items():
            beasts = mg.floor_bestiary(res.floor)
            mobs = [b for b in beasts if b.tier == "mob"]
            if not mobs:
                continue
            ordinary = mg.party_levels(f, beasts)[0]
            for b in mobs:
                self.assertLess(self._hit(b)[0], self._hp(ordinary), f"floor {f}: {b.name}")
        eleven = mg.floor_bestiary(self.res[11].floor)
        boss = next(b for b in eleven if b.tier != "mob")
        self.assertGreaterEqual(self._hit(boss)[0], self._hp(mg.party_levels(11, eleven)[0]))


class ShopsTest(unittest.TestCase):
    """Shops and prices. The books' rules are written out here: marketplaces
    from the Third Floor; a general store charges three times a specialist's
    price; a personal space is 50,000 gold; Desperado Club security is 300 a
    crawler. The prices themselves are ours, and are checked for their shape:
    a box's worth of gold for an item of its tier, rising with the floor."""

    @classmethod
    def setUpClass(cls):
        cls.md = {f: mg.generate(f, "small", 4242, images=False).markdown for f in mg.FLOORS}

    def _section(self, f):
        md = self.md[f]
        return md[md.index("## Shops and Prices"):md.index("## Loot Boxes")]

    def test_shops_open_on_the_third_floor(self):
        for f in mg.FLOORS:
            self.assertEqual("## Shops and Prices" in self.md[f], f >= 3, f"floor {f}")

    def test_the_books_rules_are_stated(self):
        for f in (3, 6, 11):
            sec = self._section(f)
            self.assertIn("sell at three times a specialist's price", sec)
            self.assertIn("will buy anything", sec)
            self.assertIn("do not buy from crawlers", sec)
            self.assertIn("higher than the shopkeeper's level hears it straight", sec)
            self.assertIn("300 gold a crawler per visit, or 400 for a pair", sec)
        self.assertNotIn("A personal space", self._section(3), "not on sale until Floor 4")
        for f in (4, 9):
            self.assertIn("A personal space: 50,000 gold", self._section(f))

    def test_an_item_is_worth_about_a_box_of_its_tier(self):
        """List price is within a factor of the kind's multiplier of the average
        gold in a box of the same tier on the same floor."""
        lb = mg.LOOT_BOXES["gold"]
        lo, hi = lb["roll"]
        for f in (3, 4, 6, 9, 11):
            for tier, power in lb["tier_power"].items():
                box = (lo + hi) / 2 * f ** power
                for k in mg.SHOP_RULES["kinds"]:
                    price = mg.list_price(tier, k["pool"], f)
                    self.assertTrue(0.25 * box <= price <= 2.5 * box,
                                    f"floor {f} {tier} {k['pool']}: {price} vs a box's {box}")

    def test_prices_rise_with_tier_and_floor(self):
        tiers = mg.LOOT_BOX_TIERS
        for k in mg.SHOP_RULES["kinds"]:
            for f in (3, 6, 11):
                row = [mg.list_price(t, k["pool"], f) for t in tiers]
                self.assertEqual(row, sorted(row), f"{k['pool']} floor {f}")
                self.assertLess(row[0], row[-1])
            for t in tiers:
                col = [mg.list_price(t, k["pool"], f) for f in range(3, 12)]
                self.assertEqual(col, sorted(col), f"{k['pool']} {t}")

    def test_every_kind_of_loot_has_a_price_and_a_shop(self):
        self.assertEqual({k["pool"] for k in mg.SHOP_RULES["kinds"]},
                         set(mg.LOOT_BOXES["items"]))
        sec = self._section(4)
        for k in mg.SHOP_RULES["kinds"]:
            row = next(ln for ln in sec.splitlines() if ln.startswith(f"| {k['name']} |"))
            cells = [c.strip() for c in row.strip("|").split("|")]
            self.assertEqual(cells[1], k["shop"])
            self.assertEqual([int(c.replace(",", "")) for c in cells[2:]],
                             [mg.list_price(t, k["pool"], 4) for t in mg.LOOT_BOX_TIERS])

    def test_charisma_matches_the_stat_rules(self):
        knobs = mg.CRAWLER_STATS["knobs"]
        sec = self._section(5)
        self.assertIn(f"each point takes {knobs['discount_per']}% off", sec)
        self.assertIn(f"up to {knobs['discount_cap']}%", sec)

    def test_a_box_says_what_a_general_store_would_pay(self):
        roller = mg.LootRoller(seed=11)
        for text in ("Gold Weapon Box", "Silver Boss Box", "Platinum Benefactor Box",
                     "Legendary Adventurer Box"):
            for floor in (1, 2, 3, 6):
                box = roller.open_text(text, floor)
                self.assertEqual(len(box.item_pools), len(box.items))
                worth = box.sale_value()
                if floor < 3:
                    self.assertEqual(worth, 0)
                    self.assertNotIn("general store", box.markdown())
                    continue
                parts = sum(mg.store_pays(t, p, floor)
                            for t, p in zip(box.item_tiers, box.item_pools) if p)
                self.assertTrue(0.9 * parts <= worth <= 1.1 * parts, (worth, parts))
                self.assertIn(f"A general store would pay about {worth:,} gold for the lot.",
                              box.markdown())
                if box.sponsor_note:
                    self.assertTrue(box.markdown().endswith(box.sponsor_note))
        bounty = roller.open_text("Legendary Bounty Box", 6)
        self.assertEqual(bounty.sale_value(), 0)

    def test_a_fan_boxs_joke_is_worth_nothing(self):
        roller = mg.LootRoller(seed=11)
        for _ in range(40):
            box = roller.open_text("Gold Fan Box", 5, liked=50)
            if box.outcome == "good":
                self.assertGreater(box.sale_value(), 0)
            else:
                self.assertEqual(box.sale_value(), 0, box.outcome)

    def test_it_is_shared_matter_not_the_floors_own(self):
        for f in range(3, 12):
            if f not in mg.FLOORS:
                continue
            md = self.md[f]
            for head in ("## Other Crawlers", "## Achievements", "## What's Switched On"):
                md = md.replace(head, "## Elsewhere")
            self.assertNotIn("Shops and Prices", mg.floor_body(md), f"floor {f}")


class LeaderboardTest(unittest.TestCase):
    """The Top Ten by ratings, from Floor 4, each with a bounty on their head.

    The figures are the canon ones, written out here rather than read from the
    bank: a test that reads its expectations from the thing it tests passes
    whatever the bank says."""

    BOUNTIES = [1_000_000, 500_000, 400_000, 300_000, 200_000,
                100_000, 100_000, 100_000, 100_000, 100_000]
    ROW = re.compile(r"^\| (\d+) \| \*\*(.+?)\*\*.* \| x(\d+) \| ([\d,]+) gold \|$")

    @staticmethod
    def _campaign(floors, seed=4242, size="medium"):
        roster, results = None, {}
        for f in floors:
            res = mg.generate(f, size, seed, images=False, roster=roster)
            roster, results[f] = res.roster, res
        return roster, results

    def _assert_full(self, roster, where):
        ranks = [c.rank for c in mg.board(roster)]
        self.assertEqual(ranks, list(range(1, 11)), f"{where}: ranks {ranks}")

    def test_nothing_before_floor_4(self):
        roster, res = self._campaign((1, 2, 3))
        self.assertEqual(roster.leaderboard, [])
        for f in (1, 2, 3):
            self.assertNotIn("Leaderboard", res[f].markdown)
        with self.assertRaises(mg.MapgenError):
            mg.place_on_board(roster, "Anyone", 1)

    def test_floor_4_deals_a_top_ten_at_the_canon_bounties(self):
        for seed in (4242, 11, 77):
            roster, res = self._campaign((3, 4), seed=seed)
            self._assert_full(roster, f"seed {seed}")
            rows = [self.ROW.match(ln) for ln in res[4].markdown.splitlines()]
            rows = [m.groups() for m in rows if m]
            self.assertEqual(len(rows), 10, f"seed {seed}: {len(rows)} rows")
            for (rank, name, mult, gold), want in zip(rows, self.BOUNTIES):
                self.assertEqual(mult, "1", f"seed {seed}: {name} new on Floor 4 at x{mult}")
                self.assertEqual(int(gold.replace(",", "")), want, f"seed {seed}: rank {rank}")
            self.assertEqual(len({r[1] for r in rows}), 10)

    def test_some_of_the_top_ten_are_groups_the_party_has_met(self):
        """And each of those is one of that group's crawlers, race and class."""
        met = 0
        for seed in range(1, 9):
            # Through floor 6, where the group's classes upgrade under them.
            roster, _ = self._campaign((3, 4, 5, 6), seed=seed)
            for c in mg.board(roster):
                if not c.team:
                    continue
                met += 1
                team = mg.find_team(roster, c.team)
                self.assertIn({"race": c.race, "class": c.klass}, team.members,
                              f"seed {seed}: {c.name} is not in {team.name}")
        self.assertGreater(met, 8, "the list almost never draws on the roster")

    def test_names_on_the_list_never_share_a_first_name_or_surname(self):
        bank = {w.lower() for w in mg.CRAWLER_FIRST + mg.CRAWLER_SURNAME}
        stub = type("StubFloor", (), {"days": 8, "neighborhoods": None})
        for seed in range(200):
            roster = mg.build_crawler_teams(stub(), "large", seed, 4)
            mg.build_leaderboard(roster, seed, 4)
            words = [w.lower() for c in roster.leaderboard for w in c.name.split()
                     if w.lower() in bank]
            self.assertEqual(len(words), len(set(words)),
                             f"seed {seed}: {[c.name for c in roster.leaderboard]}")

    def test_the_multiplier_goes_up_one_for_each_floor_on_the_list(self):
        roster, res = self._campaign((3, 4, 5, 6, 8))
        seen = {}
        for f in (4, 5, 6, 8):
            for c in res[f].floor.leaderboard:
                if c.name in seen:
                    self.assertEqual(c.multiplier, seen[c.name] + 1,
                                     f"{c.name} on floor {f}")
                else:
                    self.assertEqual(c.multiplier, 1, f"{c.name} new on floor {f}")
                seen[c.name] = c.multiplier
        self.assertTrue(any(m == 4 for m in seen.values()), "nobody lasted four floors")

    def test_a_bounty_is_the_ranks_figure_times_the_multiplier(self):
        _, res = self._campaign((3, 4, 5, 6))
        rows = [self.ROW.match(ln) for ln in res[6].markdown.splitlines()]
        rows = [m.groups() for m in rows if m]
        self.assertEqual(len(rows), 10)
        for rank, name, mult, gold in rows:
            self.assertEqual(int(gold.replace(",", "")),
                             self.BOUNTIES[int(rank) - 1] * int(mult), name)
        self.assertTrue(any(m != "1" for _, _, m, _ in rows))

    def test_the_multiplier_is_remembered_off_the_list(self):
        roster, _ = self._campaign((3, 4, 5))
        c = mg.board(roster)[4]
        self.assertEqual(c.multiplier, 2)
        mg.drop_from_board(roster, c.name)
        self.assertIsNone(c.rank)
        roster = mg.generate(6, "medium", 4242, images=False, roster=roster).roster
        self.assertEqual(c.multiplier, 2, "a floor off the list does not count")
        mg.place_on_board(roster, c.name, 3)
        self.assertEqual((c.rank, c.multiplier), (3, 3), "back on: floors 4, 5 and 6")
        roster = mg.generate(8, "medium", 4242, images=False, roster=roster).roster
        self.assertEqual(c.multiplier, 4)
        self.assertEqual([(e["floor"], e["event"]) for e in c.history],
                         [(4, "entered"), (5, "fell off"), (6, "returned")])

    def test_a_set_multiplier_keeps_climbing(self):
        roster, _ = self._campaign((3, 4))
        c = mg.board(roster)[0]
        mg.set_multiplier(roster, c.name, 5)
        self.assertEqual(c.multiplier, 5)
        roster = mg.generate(5, "medium", 4242, images=False, roster=roster).roster
        self.assertEqual(c.multiplier, 6)

    def test_placing_someone_moves_everyone_below_down_one(self):
        roster, _ = self._campaign((3, 4))
        before = [c.name for c in mg.board(roster)]
        new = mg.place_on_board(roster, "Hero of the Party", 3, race="Human", klass="Bard")
        self.assertTrue(new.added)
        after = [c.name for c in mg.board(roster)]
        self.assertEqual(after, before[:2] + ["Hero of the Party"] + before[2:9])
        tenth = mg.find_ranked(roster, before[9])
        self.assertIsNone(tenth.rank)
        self.assertEqual(tenth.history[-1]["event"], "fell off")
        mg.place_on_board(roster, before[0], 5)          # moving, not adding
        self.assertEqual([c.name for c in mg.board(roster)][4], before[0])
        self._assert_full(roster, "after moves")

    def test_dropping_someone_closes_the_ranks_and_a_floor_fills_them(self):
        roster, _ = self._campaign((3, 4))
        mg.drop_from_board(roster, mg.board(roster)[2].name)
        self.assertEqual([c.rank for c in mg.board(roster)], list(range(1, 10)))
        roster = mg.generate(5, "medium", 4242, images=False, roster=roster).roster
        self._assert_full(roster, "the next floor")

    def test_a_ranked_crawler_dies_with_their_group(self):
        roster, _ = self._campaign((3, 4))
        c = next(c for c in mg.board(roster) if c.team)
        mg.set_status(roster, c.team, outcome="fatal")
        self.assertFalse(c.alive)
        self.assertIsNone(c.rank)
        self.assertEqual(c.history[-1]["event"], "died")
        self.assertEqual([x.rank for x in mg.board(roster)], list(range(1, 10)))
        roster = mg.generate(5, "medium", 4242, images=False, roster=roster).roster
        self.assertIsNone(c.rank, "the dead are never dealt back on")
        self._assert_full(roster, "the next floor")

    def test_a_generated_floor_buries_whoever_their_group_lost(self):
        died = 0
        for seed in range(1, 25):
            roster, _ = self._campaign((3, 4, 5, 6), seed=seed, size="small")
            for c in roster.leaderboard:
                if not c.team:
                    continue
                team = mg.find_team(roster, c.team)
                there = team.alive and any(m["race"] == c.race for m in team.members)
                if c.alive:
                    self.assertTrue(there, f"seed {seed}: {c.name} outlived {team.name}")
                else:
                    died += 1
            self._assert_full(roster, f"seed {seed}")
        self.assertTrue(died, "no ranked crawler died with their group, so nothing was checked")

    def test_killing_a_ranked_crawler_costs_their_group_them(self):
        roster, _ = self._campaign((3, 4))
        c = next(c for c in mg.board(roster) if c.team)
        team = mg.find_team(roster, c.team)
        before = len(team.members)
        mg.drop_from_board(roster, c.name, died=True)
        self.assertEqual(c.died_on, 4)
        if before > 1:
            self.assertEqual(len(team.members), before - 1)
            self.assertNotIn(c.race, [m["race"] for m in team.members])
        else:
            self.assertFalse(team.alive)
        with self.assertRaises(mg.MapgenError):
            mg.place_on_board(roster, c.name, 1)
        mg.revive_ranked(roster, c.name)
        self.assertTrue(c.alive)
        self.assertIn(c.race, [m["race"] for m in team.members], "the group has them back")

    def test_a_bad_edit_is_refused_and_changes_nothing(self):
        roster, _ = self._campaign((3, 4))
        before = roster.to_dict()
        group = next(t for t in roster.active() if t.members)
        name = mg.board(roster)[0].name
        bad = [lambda: mg.place_on_board(roster, "Someone", 0),
               lambda: mg.place_on_board(roster, "Someone", 11),
               lambda: mg.place_on_board(roster, "Someone", "3"),
               lambda: mg.place_on_board(roster, "  ", 3),
               lambda: mg.place_on_board(roster, "Someone", 3, multiplier=0),
               lambda: mg.place_on_board(roster, group.name, 3),
               lambda: mg.place_on_board(roster, "Someone", 3, team="No Such Group"),
               lambda: mg.place_on_board(roster, "Someone", 3, team=group.name,
                                         race="Nobody"),
               lambda: mg.set_multiplier(roster, name, 0),
               lambda: mg.drop_from_board(roster, "nobody at all"),
               lambda: mg.revive_ranked(roster, name)]
        for i, edit in enumerate(bad):
            with self.assertRaises(mg.MapgenError, msg=f"edit {i}"):
                edit()
        self.assertEqual(roster.to_dict(), before)

    def test_the_write_up_says_what_changed(self):
        roster, res = self._campaign((3, 4))
        self.assertNotIn("Since the last floor", res[4].markdown,
                         "everyone is new on the floor the list is dealt")
        gone = mg.board(roster)[3].name
        mg.drop_from_board(roster, gone)
        mg.place_on_board(roster, "Hero of the Party", 1)
        md = mg.generate(5, "medium", 4242, images=False, roster=roster).markdown
        news = md[md.index("Since the last floor"):]
        self.assertIn(f"**{gone}** fell off the list", news)
        self.assertIn("**Hero of the Party** is new to the list, at rank 1", news)

    def test_a_list_first_dealt_deeper_has_been_running_since_floor_4(self):
        deep = []
        for seed in range(1, 6):
            board = mg.generate(8, "small", seed, images=False).floor.leaderboard
            self.assertEqual(len(board), 10)
            mults = [c.multiplier for c in board]
            self.assertTrue(all(1 <= m <= 5 for m in mults), mults)   # floors 4-8
            deep += mults
        self.assertGreater(max(deep), 1)

    def test_strangers_take_their_upgrade_from_floor_6(self):
        for f, board in ((6, mg.generate(6, "small", 7, images=False).floor.leaderboard),
                         (9, mg.generate(9, "small", 7, images=False).floor.leaderboard)):
            for c in board:
                self.assertNotIn(c.klass, mg.CRAWLER_CLASS_UPGRADE,
                                 f"floor {f}: {c.name} is still a {c.klass}")
        # And one dealt on Floor 4 takes it on reaching 6, not only one dealt there.
        carried = 0
        for seed in range(1, 9):
            _, res = self._campaign((3, 4, 5, 6), seed=seed, size="small")
            due = {c.name for c in res[5].floor.leaderboard
                   if not c.team and c.klass in mg.CRAWLER_CLASS_UPGRADE}
            for c in res[6].floor.leaderboard:
                if c.name in due:
                    carried += 1
                    self.assertNotIn(c.klass, mg.CRAWLER_CLASS_UPGRADE, f"seed {seed}: {c.name}")
        self.assertTrue(carried, "no stranger reached floor 6 owed an upgrade")

    def test_nobody_is_ranked_twice_under_two_names(self):
        """A group's crawler who falls off is still that crawler: a later floor
        dealing a newcomer from the same group must not pick them again."""
        checked = 0
        for seed in range(1, 21):
            roster, _ = self._campaign((3, 4), seed=seed, size="small")
            # Everyone off, so the next floors deal ten newcomers each and the
            # groups of the crawlers who fell off are in the running.
            for c in mg.board(roster):
                mg.drop_from_board(roster, c.name)
            for f in (5, 6):
                for c in mg.board(roster):
                    mg.drop_from_board(roster, c.name)
                roster = mg.generate(f, "small", seed, images=False, roster=roster).roster
            alive = [(c.team, c.race) for c in roster.leaderboard if c.team and c.alive]
            checked += len(alive)
            self.assertEqual(len(alive), len(set(alive)), f"seed {seed}: {alive}")
        self.assertGreater(checked, 30)

    def test_the_leaderboard_survives_a_save_and_load(self):
        roster, _ = self._campaign((3, 4, 5))
        mg.place_on_board(roster, "Hero of the Party", 2, race="Human", klass="Bard")
        back = mg.CampaignRoster.from_dict(json.loads(json.dumps(roster.to_dict())))
        self.assertEqual(back.to_dict(), roster.to_dict())
        self.assertEqual([c.multiplier for c in mg.board(back)],
                         [c.multiplier for c in mg.board(roster)])
        old = roster.to_dict()
        old.pop("leaderboard")
        self.assertEqual(mg.CampaignRoster.from_dict(old).leaderboard, [])


class CrawlerPartyTest(unittest.TestCase):
    """Floor 3 is where every surviving crawler picks a race and a class.

    The race is a permanent transformation and must never be rerolled. The class
    can move, but only the way the System moves it: the documented level 6 upgrade,
    and only forwards. The party itself grows and shrinks with what the floor's
    report says happened to the group, which is the part that has to stay honest --
    a group reported as reduced to one survivor cannot be listed four strong."""

    PARTY_RE = re.compile(
        r"^  - (?:\w+ crawlers|Down to one crawler|Who they were): (.+)\.$")

    @classmethod
    def setUpClass(cls):
        # One campaign, shared: every floor of it is generated map-free, but that is
        # still ten builds and each test would otherwise pay for its own.
        cls.per_floor = {}
        roster = None
        for f in mg.FLOORS:
            res = mg.generate(f, "large", 700 + f, images=False, roster=roster)
            roster, cls.per_floor[f] = res.roster, res.floor.other_crawlers
        cls.roster = roster
        cls.md = {2: mg.generate(2, "medium", 808, images=False).markdown}

    def _teams(self, floors=mg.FLOORS):
        for f in floors:
            for team in self.per_floor[f]:
                yield f, team

    @staticmethod
    def _bulk(seeds, size="large"):
        """Campaigns dealt straight through build_crawler_teams against a stub floor.

        Some of what a party has to get right -- never doubling up on a class, say --
        goes wrong about twice in four thousand team reports, which is far more
        volume than generating real floors can afford. This skips the maps, the
        rooms and the rendering and deals nothing but rosters."""
        stub = type("StubFloor", (), {"days": 8, "neighborhoods": None})
        for seed in seeds:
            roster = mg.CampaignRoster()
            for f in mg.FLOORS:
                roster = mg.build_crawler_teams(stub(), size, seed, f, roster)
                for team in roster.teams:
                    if team.floor == f and team.history:
                        yield f, team

    # -- when a party exists at all -----------------------------------------

    def test_nobody_has_picked_before_floor_3(self):
        for f, team in self._teams(floors=(1, 2)):
            self.assertEqual(team.members, [],
                             f"{team.name} has a race and class on floor {f}, "
                             "before the System offers them")
        self.assertNotIn("crawlers:", self.md[2],
                         "floor 2 printed a party line")

    def test_every_team_has_a_party_from_floor_3_down(self):
        for f, team in self._teams(floors=(3, 4, 5, 6, 8, 9, 10, 11)):
            self.assertTrue(team.members,
                            f"{team.name} is reported on floor {f} with no crawlers")
            self.assertLessEqual(len(team.members), mg.PARTY_MAX)
            for m in team.members:
                self.assertIn(m["race"], mg.CRAWLER_RACE)

    def test_a_party_never_doubles_up_on_a_race_or_a_class(self):
        """Run at volume: the way this breaks is a Rogue joining a merged party that
        already holds the Rogue Trapmaster they are about to upgrade into, which
        needs a merge, a floor 6 and the wrong draw all at once."""
        reports = 0
        for f, team in self._bulk(range(200)):
            reports += 1
            races = [m["race"] for m in team.members]
            classes = [m["class"] for m in team.members]
            self.assertEqual(len(set(races)), len(races),
                             f"{team.name} on floor {f} has two of the same race")
            self.assertEqual(len(set(classes)), len(classes),
                             f"{team.name} on floor {f} has two of the same class: "
                             f"{classes}")
        self.assertGreater(reports, 3000, "not enough reports to find a rare clash")

    # -- what persists and what moves ---------------------------------------

    def test_a_race_is_permanent(self):
        """A party that did not grow can only have lost people, so its races this
        floor have to be a subset of last floor's. Rerolling shows up here."""
        seen = {}
        for f, team in self._teams():
            races = {m["race"] for m in team.members}
            was = seen.get(team.surname)
            if was is not None and len(races) <= len(was):
                self.assertTrue(races <= was,
                                f"{team.name} arrived on floor {f} with races nobody "
                                f"had before: {races - was}")
            seen[team.surname] = races

    def test_the_level_6_upgrade_is_taken_and_only_forwards(self):
        for f, team in self._teams(floors=(6, 8, 9, 10, 11)):
            for m in team.members:
                self.assertNotIn(m["class"], mg.CRAWLER_CLASS_UPGRADE,
                                 f"{team.name} on floor {f} still has a "
                                 f"{m['class']}, which upgrades at level 6")
        below = {m["class"] for f, t in self._teams(floors=(3, 4, 5)) for m in t.members}
        self.assertTrue(below & set(mg.CRAWLER_CLASS_UPGRADE),
                        "no team above floor 6 has an upgradable class, so the "
                        "upgrade check above proves nothing")

    def test_an_earlier_floors_result_keeps_its_own_classes(self):
        """Upgrading rewrites the member dicts in place, so a result handed back for
        floor 3 has to hold copies of them or floor 6 promotes it retroactively.

        Several seeds, and the assertion at the end: on any one seed the floor 3
        teams can all die before floor 6, or all hold classes with no upgrade path,
        and the comparison passes without having tested anything."""
        proved = 0
        for seed in (4242, 11, 77, 900, 1234):
            roster, three, snapshot = None, None, None
            for f in (3, 4, 5, 6):
                res = mg.generate(f, "medium", seed, images=False, roster=roster)
                roster = res.roster
                if f == 3:
                    three = res.floor.other_crawlers
                    snapshot = [[dict(m) for m in t.members] for t in three]
            self.assertEqual([[dict(m) for m in t.members] for t in three], snapshot,
                             f"seed {seed}: later floors rewrote the floor 3 result")
            live = {t.surname: t for t in roster.teams}
            proved += sum([dict(m) for m in live[t.surname].members] != snap
                          for t, snap in zip(three, snapshot))
        self.assertTrue(proved, "no floor 3 team was changed by a later floor, so "
                                "sharing the member dicts would have gone unnoticed")

    # -- the report and the roster agree ------------------------------------

    def test_the_party_line_names_exactly_the_roster(self):
        for f in (3, 6):
            res = mg.generate(f, "medium", 808, images=False)
            lines = [self.PARTY_RE.match(ln) for ln in res.markdown.splitlines()]
            found = [m.group(1) for m in lines if m]
            teams = [t for t in res.floor.other_crawlers if t.members]
            self.assertEqual(len(found), len(teams),
                             f"floor {f} printed {len(found)} party lines for "
                             f"{len(teams)} teams")
            for who, team in zip(found, teams):
                people = who.split(", ")
                self.assertEqual(len(people), len(team.members),
                                 f"{team.name} lists {len(people)} of "
                                 f"{len(team.members)} crawlers")
                self.assertTrue(all(p.startswith(("a ", "an ")) for p in people), who)
                self.assertEqual(len(set(people)), len(people),
                                 f"floor {f} listed the same crawler twice: {who}")
                for m in team.members:
                    self.assertIn(f"{m['race']} {m['class']}", who)

    def test_a_wiped_team_is_written_up_in_the_past_tense(self):
        dead = [t for _, t in self._teams() if not t.alive and t.members]
        self.assertTrue(dead, "no team died holding a party, so nothing was checked")
        for team in dead:
            self.assertTrue(mg.party_line(team).startswith("Who they were:"),
                            f"{team.name} died on floor {team.died_on} but is still "
                            f"described in the present: {mg.party_line(team)}")
        for _, team in self._teams():
            if team.alive:
                self.assertNotIn("Who they were", mg.party_line(team))

    def test_a_report_that_claims_an_upgrade_can_show_one(self):
        claims = 0
        for seed in range(40):
            roster = None
            for f in (3, 4, 5):
                res = mg.generate(f, "medium", 300 + seed * 7 + f, images=False,
                                  roster=roster)
                roster = res.roster
                for team in res.floor.other_crawlers:
                    if "class upgrade" not in team.status:
                        continue
                    claims += 1
                    self.assertTrue(
                        any(m["class"] in mg._UPGRADED_CLASSES for m in team.members),
                        f"{team.name} on floor {f} reports a class upgrade but "
                        f"nobody in it has one: {team.members}")
        self.assertGreater(claims, 5, "too few upgrade reports to prove anything")

    def test_an_untagged_report_falls_back_to_what_its_outcome_costs(self):
        """Most of the bank is still plain strings, and has to keep working: a
        costly floor takes someone whether or not the line spells it out, and the
        other three outcomes leave the headcount alone."""
        plain = collections.Counter()
        for kind, entries in mg.TEAM_STATUS.items():
            for entry in entries:
                if isinstance(entry, list):
                    self.assertEqual(mg.status_change(entry, kind),
                                     (entry[1], entry[2] if len(entry) > 2 else None))
                    continue
                plain[kind] += 1
                expected = -1 if kind == "costly" else 0
                self.assertEqual(mg.status_change(entry, kind), (expected, None),
                                 f"a plain {kind} report should be worth {expected}")
        self.assertTrue(plain["costly"], "no plain costly report left to check")

    def test_the_numbers_a_report_names_are_the_numbers_listed(self):
        """The bank tags the reports that say out loud what happened to the group's
        headcount. Those are the ones a party line can contradict."""
        cases = [(-1, 4, 3), (-2, 4, 2), (-6, 5, 1), (-6, 1, 1), (2, 3, 5),
                 (2, mg.PARTY_MAX, mg.PARTY_MAX), (0, 4, 4)]
        for delta, before, after in cases:
            with self.subTest(delta=delta, before=before):
                rng = random.Random(7)
                team = mg.CrawlerTeam(name="Team Probe", origin="a probe", outcome="costly")
                team.members = mg._new_members(rng, before, [])
                self.assertEqual(len(team.members), before)
                mg._deal_party(rng, team, 4, (delta, None))
                self.assertEqual(len(team.members), after)

    def test_a_merge_reads_as_a_merge(self):
        """Every report tagged as a merge should leave the group bigger, and the
        other outcomes should never quietly grow."""
        grew = 0
        for seed in range(30):
            roster = None
            prev = {}
            for f in (3, 4, 5, 6, 8):
                res = mg.generate(f, "medium", 500 + seed * 11 + f, images=False,
                                  roster=roster)
                roster = res.roster
                for team in res.floor.other_crawlers:
                    was = prev.get(team.surname)
                    now = len(team.members)
                    if was is not None and now > was:
                        grew += 1
                        self.assertIn("throws in with another team", team.status,
                                      f"{team.name} grew from {was} to {now} on floor "
                                      f"{f} with no report that says so: {team.status}")
                    prev[team.surname] = now
        self.assertGreater(grew, 5, "no team ever merged, so nothing was checked")

    # -- storage -------------------------------------------------------------

    def test_a_roster_written_before_parties_existed_still_loads(self):
        """`members` was added without bumping ROSTER_VERSION, on the grounds that
        an older file is readable and fills in on its next floor. That only holds
        while this passes."""
        roster = None
        for f in (1, 2, 3, 4, 5):
            roster = mg.generate(f, "medium", 4242, images=False, roster=roster).roster
        old = roster.to_dict()
        for team in old["teams"]:
            team.pop("members")                 # what the previous build would write
        back = mg.CampaignRoster.from_dict(old)
        self.assertTrue(back.active())
        self.assertTrue(all(t.members == [] for t in back.teams))
        res = mg.generate(6, "medium", 4242, images=False, roster=back)
        for team in res.floor.other_crawlers:
            self.assertTrue(team.members,
                            f"{team.name} carried in from an older roster and never "
                            "picked a race or class")

    def test_a_party_survives_a_save_and_load(self):
        round_tripped = mg.CampaignRoster.from_dict(self.roster.to_dict())
        self.assertEqual([_t(t) for t in round_tripped.teams],
                         [_t(t) for t in self.roster.teams])


class MobVocabularyTest(unittest.TestCase):
    """Mob words live in one tagged pool that every floor draws from.

    Floors used to keep private lists, which left a rail map picking from 16
    adjectives and 20 nouns. Sharing the pool is only safe while the tags keep each
    floor to words that make sense on it, and while the weighting keeps a floor's
    own habitat present rather than drowned by the much larger common pool."""

    MOB_FLOORS = (1, 2, 3, 4, 5, 6)

    def test_every_mob_floor_declares_its_tags(self):
        for floor in self.MOB_FLOORS:
            self.assertIn(str(floor), mg.FLOOR_MOB_TAGS,
                          f"floor {floor} makes mobs but declares no tags")

    def test_declared_tags_exist_in_both_banks(self):
        for floor, weights in mg.FLOOR_MOB_TAGS.items():
            for tag, weight in weights.items():
                with self.subTest(floor=floor, tag=tag):
                    self.assertIn(tag, mg.MOB_NOUN, "no nouns carry this tag")
                    self.assertIn(tag, mg.MOB_ADJ, "no adjectives carry this tag")
                    self.assertGreater(weight, 0)

    def test_tags_do_not_overlap(self):
        """A word in two tags would be drawn more often than its weight implies."""
        for name, bank in (("MOB_NOUN", mg.MOB_NOUN), ("MOB_ADJ", mg.MOB_ADJ)):
            seen = collections.Counter(w for words in bank.values() for w in words)
            dupes = sorted(w for w, n in seen.items() if n > 1)
            self.assertEqual(dupes, [], f"{name} lists these under several tags: {dupes}")

    def test_a_floor_only_draws_words_it_can_host(self):
        for floor in self.MOB_FLOORS:
            allowed = set(mg.FLOOR_MOB_TAGS[str(floor)])
            ok_nouns = {w for t in allowed for w in mg.MOB_NOUN[t]}
            ok_adj = {w for t in allowed for w in mg.MOB_ADJ[t]}
            rng = random.Random(floor)
            for _ in range(400):
                adj, noun = mg.mob_words(rng, floor)
                self.assertIn(noun, ok_nouns, f"floor {floor} drew an off-limits noun")
                self.assertIn(adj, ok_adj, f"floor {floor} drew an off-limits adjective")

    def test_every_floor_has_a_deep_pool(self):
        """The whole point of pooling. Floors 3, 4 and 6 had 216-320 combinations."""
        for floor in self.MOB_FLOORS:
            if floor == 5:
                continue                      # floor 5's quadrants supply the nouns
            tags = mg.FLOOR_MOB_TAGS[str(floor)]
            combos = (sum(len(mg.MOB_ADJ[t]) for t in tags)
                      * sum(len(mg.MOB_NOUN[t]) for t in tags))
            self.assertGreater(combos, 5000, f"floor {floor} can only make {combos} mobs")

    def test_a_floor_keeps_its_own_flavour(self):
        """The common pool dwarfs each habitat, so without weighting a rail floor
        would almost never field anything rail. These floors are meant to lead with
        their own habitat; floors 1-2 only season with theirs."""
        for floor, tag, low, high in ((3, "urban", 0.40, 0.80), (4, "rail", 0.40, 0.80),
                                      (6, "jungle", 0.40, 0.80), (1, "urban", 0.05, 0.40)):
            with self.subTest(floor=floor):
                rng = random.Random(99)
                habitat = sum(1 for _ in range(2000)
                              if mg.mob_words(rng, floor)[1] in mg.MOB_NOUN[tag])
                share = habitat / 2000
                self.assertTrue(low <= share <= high,
                                f"floor {floor} drew {share:.0%} {tag} nouns, "
                                f"wanted {low:.0%}-{high:.0%}")

    def test_unknown_floors_still_get_words(self):
        """A floor with no entry falls back rather than failing to build."""
        adj, noun = mg.mob_words(random.Random(1), 99)
        self.assertIn(noun, mg.MOB_NOUN["anywhere"])
        self.assertIn(adj, mg.MOB_ADJ["anywhere"])


class BossNameTest(unittest.TestCase):
    """A boss run should not keep introducing the same boss.

    Two name shapes used to split the whole game on a coin flip, drawing from 24
    first names and 44 titles, so a montage of 718 bosses fielded only 396 distinct
    names and heard "{first} the {title}" half the time. Worse, floors 1 and 2 share
    a generator and shared a seed with it, so the same seed handed back the same
    dungeon twice -- the same neighborhoods and the same 21 bosses, name for name."""

    @staticmethod
    def _bosses(fl):
        """Every boss on a floor, including the single floor 11 Marshal that hangs
        off the floor itself rather than off a cell."""
        out = []
        for attr in ("neighborhoods", "cells"):
            for item in getattr(fl, attr, []) or []:
                if getattr(item, "boss", None):
                    out.append(item.boss)
        for group in (getattr(fl, "borough_bosses", {}) or {},
                      getattr(fl, "city_bosses", {}) or {}):
            out += list(group.values())
        for attr in ("top_boss", "boss"):
            if getattr(fl, attr, None):
                out.append(getattr(fl, attr))
        return out

    @classmethod
    def setUpClass(cls):
        # A montage: every floor at a shared size, over several seeds. Markdown
        # only -- the maps are the slow part and no boss name is in them.
        cls.per_case = {}
        for seed in range(1, 9):
            for floor in mg.FLOORS:
                fl = mg.generate(floor, "large", seed, images=False).floor
                cls.per_case[(floor, seed)] = [b.name for b in cls._bosses(fl)]
        cls.all_names = [n for v in cls.per_case.values() for n in v]

    # -- within one floor ----------------------------------------------------

    def test_a_floor_never_repeats_a_boss_name(self):
        for (floor, seed), names in self.per_case.items():
            self.assertEqual(len(set(names)), len(names),
                             f"floor {floor} seed {seed} repeats a boss name")
        # The huge tier is where the old banks ran dry: 85 bosses against 24 first
        # names and 44 titles could not help but collide.
        for seed in (1, 77):
            names = [b.name for b in self._bosses(
                mg.generate(1, "huge", seed, images=False).floor)]
            self.assertGreater(len(names), 80)
            self.assertEqual(len(set(names)), len(names),
                             f"a huge floor 1 at seed {seed} repeats a boss name")

    def test_a_floor_never_recombines_a_first_name_or_a_title(self):
        """"Vexnia the Auditor" two rooms from "Vexnia the Landlord" reads as one
        boss with two entries, so a name or a title used once on a floor is spent."""
        firsts, titles = set(mg.BOSS_FIRST), sorted(mg.BOSS_TITLE, key=len, reverse=True)
        for (floor, seed), names in self.per_case.items():
            seen_first, seen_title = collections.Counter(), collections.Counter()
            for name in names:
                for word in name.replace(",", " ").replace('"', " ").split():
                    if word in firsts:
                        seen_first[word] += 1
                title = next((t for t in titles if name.endswith(t)), None)
                if title:
                    seen_title[title] += 1
            for bank, label in ((seen_first, "first name"), (seen_title, "title")):
                worst = bank.most_common(1)
                if worst and worst[0][1] > 1:
                    self.fail(f"floor {floor} seed {seed} uses the {label} "
                              f"{worst[0][0]!r} {worst[0][1]} times")

    # -- across floors and seeds ---------------------------------------------

    def test_two_floors_at_one_seed_are_not_the_same_dungeon(self):
        """Floors 1 and 2 are built by the same generator, floors 3 and 6 by
        another. The floor has to reach the rng stream, or one campaign seed
        describes both of a pair identically."""
        for a, b in ((1, 2), (3, 6)):
            for seed in (1, 77, 4242):
                one = mg.generate(a, "large", seed, images=False).floor
                two = mg.generate(b, "large", seed, images=False).floor
                # Bosses only, and strictly. The name space is in the millions, so
                # a single collision is evidence of a shared stream rather than
                # bad luck -- which is not true of the 225 settlement names, where
                # one floor collides with itself at an unrelated seed. Place names
                # are covered against a control in the next test.
                self.assertEqual({x.name for x in self._bosses(one)}
                                 & {x.name for x in self._bosses(two)}, set(),
                                 f"seed {seed}: floors {a} and {b} field the same bosses")

    @staticmethod
    def _places(fl) -> set:
        out = set()
        for attr in ("neighborhoods", "cells"):
            out |= {c.name for c in getattr(fl, attr, []) or [] if getattr(c, "name", None)}
        return out

    def test_no_floor_shares_a_stream_with_another(self):
        """Every generator used to start at random.Random(seed), so any two floors
        drawing on the same bank in the same order got the same values out of it.
        Checked against a control, because two floors overlapping on a 15-entry
        bank is arithmetic rather than a bug: a floor compared with *itself* at
        another seed is the most overlap a small bank can be blamed for."""
        for a in mg.FLOORS:
            for b in mg.FLOORS:
                if b <= a:
                    continue
                shared = control = 0
                for seed in (11, 4242):
                    fa = mg.generate(a, "medium", seed, images=False).floor
                    fb = mg.generate(b, "medium", seed, images=False).floor
                    other = mg.generate(a, "medium", seed + 9000, images=False).floor
                    names_a, names_b = self._names(fa), self._names(fb)
                    shared += len(names_a & names_b)
                    control += len(names_a & self._names(other))
                self.assertLessEqual(shared, control + 2,
                                     f"floors {a} and {b} at one seed share {shared} "
                                     f"generated names, against {control} for floor {a} "
                                     f"against itself at an unrelated seed")

    @classmethod
    def _names(cls, fl) -> set:
        """Generated proper nouns, which is where a shared stream shows. Loot and
        room features are deliberately left out: those banks are small enough that
        any two floors overlap on them whatever the seed."""
        out = cls._places(fl) | {b.name for b in cls._bosses(fl)}
        for attr in ("neighborhoods", "cells"):
            for c in getattr(fl, attr, []) or []:
                mob = getattr(c, "mob", None)
                if mob is not None:
                    out.add(getattr(mob, "name", str(mob)))
                for m in getattr(c, "mobs", []) or []:
                    out.add(getattr(m, "name", str(m)))
        return {n for n in out if n}

    def test_a_montage_does_not_run_out_of_names(self):
        counts = collections.Counter(self.all_names)
        distinct = len(counts) / len(self.all_names)
        self.assertGreater(distinct, 0.85,
                           f"only {distinct:.0%} of {len(self.all_names)} boss names "
                           f"across the montage are distinct")
        worst, hits = counts.most_common(1)[0]
        self.assertLessEqual(hits, 3, f"{worst!r} turns up {hits} times in one montage")

    def test_a_montage_draws_widely_from_the_banks(self):
        """Distinct full names are not the whole story. With 24 first names and 44
        titles every boss in a montage still came out of the same small hat --
        "Vexnia" 24 times and "the Understudy" 25 in 718 bosses -- and that is what
        a run of them sounds like, whether or not any two names matched."""
        firsts, titles = collections.Counter(), collections.Counter()
        known = set(mg.BOSS_FIRST)
        longest_first = sorted(mg.BOSS_TITLE, key=len, reverse=True)
        for name in self.all_names:
            for word in name.replace(",", " ").replace('"', " ").split():
                if word in known:
                    firsts[word] += 1
            title = next((t for t in longest_first if name.endswith(t)), None)
            if title:
                titles[title] += 1
        for bank, label, floor in ((firsts, "first names", 90), (titles, "titles", 90)):
            self.assertGreater(len(bank), floor,
                               f"a montage of {len(self.all_names)} bosses used only "
                               f"{len(bank)} different {label}")
            busiest, hits = bank.most_common(1)[0]
            share = hits / len(self.all_names)
            self.assertLess(share, 0.04,
                            f"{busiest!r} is on {share:.0%} of every boss in the montage")

    def test_no_one_shape_dominates(self):
        """The complaint was that the bosses sounded alike, not only that they
        shared names. Each shape leaves a mark that can be counted."""
        marks = {
            "nickname": lambda n: '"' in n,
            "a pair": lambda n: " and " in n,
            "The adj noun": lambda n: n.startswith("The "),
            "full name": lambda n: not n.startswith("The ") and '"' not in n
                                   and n.split()[-1] in set(mg.BOSS_SURNAME),
        }
        for label, hit in marks.items():
            share = sum(map(hit, self.all_names)) / len(self.all_names)
            self.assertGreater(share, 0.02, f"the {label} shape is effectively unused")
            self.assertLess(share, 0.5, f"the {label} shape is {share:.0%} of all names")

    def test_names_stay_short_enough_to_read(self):
        """Stacking a title onto an already multi-word mob produced things like
        "The Burning Goblin Bomb Bard the Non-Disclosure Agreement"."""
        longest = max(self.all_names, key=len)
        self.assertLessEqual(len(longest), mg.BossNamer.MAX_LEN,
                             f"{longest!r} is {len(longest)} characters")

    # -- the banks themselves ------------------------------------------------

    def test_every_shape_fills_without_a_missing_placeholder(self):
        """A typo in a template would otherwise surface as a KeyError mid-build, on
        whichever floor and seed happened to draw that shape."""
        parts = {"first": "A", "second": "B", "surname": "C", "nick": "D",
                 "title": "the E", "adj": "F", "noun": "G", "theme": "H"}
        for shape, weight in mg.BOSS_NAME_FORM:
            self.assertGreater(weight, 0, f"{shape!r} is weighted out of the deck")
            self.assertTrue(shape.format(**parts))
        for shape in mg.PARADE_BOSS_NAME:
            self.assertIn("{theme}", shape, f"{shape!r} drops the parade theme")
            self.assertTrue(shape.format(**parts))

    def test_the_parade_marshal_is_not_always_the_same(self):
        """Floor 11 fields one boss and used one shape, so the whole floor had as
        many possible boss names as it has themes."""
        names = {mg.generate(11, "medium", s, images=False).floor.boss.name
                 for s in range(25)}
        self.assertGreater(len(names), 20, f"25 seeds gave {len(names)} marshals")

    def test_a_pair_is_two_different_bosses(self):
        namer = mg.BossNamer(random.Random(3), mg.NBHD_ADJ)
        pairs = 0
        for _ in range(400):
            name = namer.name("Goblins")
            if " and " in name:
                pairs += 1
                left, right = name.split(" and ", 1)
                self.assertNotEqual(left.strip(), right.split(",")[0].strip())
        self.assertGreater(pairs, 5, "the pair shape never came up")


class LootBoxTest(unittest.TestCase):
    """Loot boxes: what a floor suggests, and what a box holds once opened.

    The two are separate on purpose. A floor names its boxes -- every boss's drop,
    every quest's and achievement's reward -- and rolls nothing; a LootRoller opens
    a box on demand, after a DM has decided what it is. Most checks here are about
    the parts that read wrong when they slip: effects on items that cannot carry
    them, power leaking up a tier, a low-tier item in a high-tier box, a box type
    turning up before its floor, and a tier that is not one of the six."""

    # Stated here rather than read from LOOT_BOXES["effect_kinds"]. Read from the
    # bank, the fit test approved whatever the bank said: a probe that let magic
    # items take worn effects again passed it. These are the design rules.
    NEVER = {
        "weapons": {"worn"},             # a sword does not silence footsteps
        "apparel": {"weapon"},           # a jacket does not deal damage on a hit
        "footwear": {"weapon"},
        "pet": {"weapon"},
        "magic": {"weapon", "worn"},     # a scroll is carried, not worn or swung
        "offworld": {"weapon"},
    }

    @classmethod
    def setUpClass(cls):
        cls.lb = mg.LOOT_BOXES
        cls.fx_kind = {e: k for tier in cls.lb["effects"].values() for e, k in tier}
        cls.fx_tier = {e: t for t, tier in cls.lb["effects"].items() for e, _ in tier}
        cls.item_pool, cls.item_band = {}, {}
        for pool, bank in cls.lb["items"].items():
            for band, items in (bank.items() if isinstance(bank, dict) else [(None, bank)]):
                for item in items:
                    cls.item_pool[item], cls.item_band[item] = pool, band
        # A spread of boxes: every rollable type at every tier, several times over.
        roller = mg.LootRoller(2024)
        # Fan boxes are the audience's call, one item and a vote, and have their
        # own tests below.
        types = [t for t in cls.lb["types"] + cls.lb["themed_types"]
                 if not t.get("special") and t["name"] != "Fan"]
        cls.opened = [roller.open(tier, t["name"])
                      for _ in range(6) for t in types for tier in mg.LOOT_BOX_TIERS]

    @staticmethod
    def _parse(line):
        """An item line back into (base item, [effects])."""
        body = re.sub(r"^\*\*.*?\*\*, ", "", line)
        base = body.split(" (")[0]
        effects = body[len(base) + 2:-1].split("; ") if " (" in body else []
        return base, effects

    # -- the floor suggests, and rolls nothing -----------------------------------

    def test_every_floor_has_the_section_once(self):
        for floor in mg.FLOORS:
            md = mg.generate(floor, "small", 11, images=False).markdown
            self.assertEqual(md.count("## Loot Boxes"), 1,
                             f"floor {floor} carries the loot section "
                             f"{md.count('## Loot Boxes')} times")
            self.assertIn("### Opening a box", md)
            self.assertIn("--open-box", md)
            self.assertNotIn("Box tiers, lowest to highest", md,
                             "the old one-line section came back")

    def test_a_floor_rolls_no_box_contents(self):
        """Decoupled means the write-up holds suggestions only. If an enchantment
        turns up in a floor's loot section, something is rolling boxes again."""
        effects = set(self.fx_tier)
        for floor in mg.FLOORS:
            md = mg.generate(floor, "medium", 4242, images=False).markdown
            section = md[md.index("## Loot Boxes"):md.index("## Crawler Stats")]
            self.assertNotIn("Ready to hand out", section)
            leaked = [e for e in effects if e in section]
            self.assertEqual(leaked, [], f"floor {floor} rolled contents: {leaked[:3]}")

    def test_rolling_boxes_never_touches_a_floor(self):
        """A roller has no floor to reach. Opening a hundred boxes between two
        generations of the same floor must leave every byte of it alone."""
        before = mg.generate(1, "medium", 4242)
        roller = mg.LootRoller(5)
        for _ in range(100):
            roller.open("Gold", "Boss")
        after = mg.generate(1, "medium", 4242)
        self.assertEqual(before.markdown, after.markdown)
        self.assertEqual([s for _, s in before.svgs], [s for _, s in after.svgs])

    def test_types_arrive_on_their_floor(self):
        """Sponsors start on Floor 4, so Benefactor boxes do; the leaderboard
        starts there too, so Bounty boxes do; Venison needs a ranked crawler to
        survive a floor, so it starts a floor later."""
        def table_types(md):
            sec = md[md.index("| Box | Earned for |"):md.index("### Opening a box")]
            sec = sec.split("### What this floor hands out")[0]
            return {ln.split("|")[1].strip() for ln in sec.splitlines()
                    if ln.startswith("| ") and "---" not in ln} - {"Box"}
        for floor in mg.FLOORS:
            md = mg.generate(floor, "small", 11, images=False).markdown
            listed = table_types(md)
            for t in self.lb["types"]:
                if t["from_floor"] <= floor:
                    self.assertIn(t["name"], listed, f"floor {floor} lost {t['name']}")
                else:
                    self.assertNotIn(t["name"], listed,
                                     f"{t['name']} boxes listed on floor {floor}, "
                                     f"before floor {t['from_floor']}")
        self.assertEqual(next(t for t in self.lb["types"]
                              if t["name"] == "Benefactor")["from_floor"], 4)

    def test_every_boss_names_its_drop(self):
        for floor in (1, 2, 3, 5, 6, 11):
            res = mg.generate(floor, "medium", 4242, images=False)
            md, fl = res.markdown, res.floor
            # Counted independently of mg.floor_bosses, which the suggestions use:
            # floor 5's bosses are the castle rulers, one per quadrant, and the
            # guardians some castles keep besides.
            bosses = BossNameTest._bosses(fl)
            for q in getattr(fl, "quadrants", []) or []:
                bosses += [q.castle.boss] + q.castle.guardians
            self.assertTrue(bosses, f"floor {floor} has no bosses to check")
            drops = [ln for ln in md.splitlines() if "  - Drops: a " in ln]
            self.assertEqual(len(drops), len(bosses),
                             f"floor {floor} has {len(bosses)} bosses and "
                             f"{len(drops)} drop lines")

    def test_the_suggestion_table_counts_every_named_box(self):
        """Every box the write-up names is in the table, and nothing else is."""
        for floor in (1, 3, 6, 11):
            md = mg.generate(floor, "large" if floor != 11 else "medium", 4242,
                             images=False).markdown
            start = md.index("### What this floor hands out")
            section = md[start:md.index("###", start + 3)]     # to the next heading
            rows = [ln for ln in section.splitlines()
                    if ln.startswith("| ") and "---" not in ln and "How many" not in ln]
            listed = sum(int(r.split("|")[2]) for r in rows)
            drops = sum(1 for ln in md.splitlines() if "  - Drops: a " in ln)
            self.assertGreaterEqual(listed, drops,
                                    f"floor {floor}: {drops} boss drops, {listed} in table")
            self.assertEqual(listed, len(mg.floor_box_suggestions(
                mg.generate(floor, "large" if floor != 11 else "medium", 4242,
                            images=False).floor)))

    def test_every_suggestion_can_be_pasted_back_in(self):
        """The point of the split: whatever a floor names, a DM can hand to the
        roller as written. Every suggestion's label must parse to itself."""
        for floor in mg.FLOORS:
            fl = mg.generate(floor, "medium", 4242, images=False).floor
            for sg in mg.floor_box_suggestions(fl):
                tier, box_type = mg.parse_box(f"a {sg.label}")
                self.assertEqual((tier, box_type["name"]), (sg.tier, sg.box_type),
                                 f"floor {floor}: {sg.label!r} parses as "
                                 f"{tier} {box_type['name']}")
                self.assertFalse(box_type.get("unknown"),
                                 f"floor {floor} suggests a {sg.label}, which the "
                                 f"roller does not know and would open as generic")

    def test_parse_reads_a_box_the_ways_a_dm_writes_one(self):
        for text in ("a Silver Boss Box", "Silver Boss", "silver boss box",
                     "  A SILVER BOSS BOX.  ", "the Silver Boss Box"):
            tier, t = mg.parse_box(text)
            self.assertEqual((tier, t["name"]), ("Silver", "Boss"), text)
        tier, t = mg.parse_box("Platinum Lucky Bastard")
        self.assertEqual((tier, t["name"]), ("Platinum", "Lucky Bastard"))
        for bad in ("Boss", "Shiny Boss", "Gold", ""):
            with self.assertRaises(mg.MapgenError, msg=bad):
                mg.parse_box(bad)

    # -- boss drops ---------------------------------------------------------------

    def test_boss_drops_climb_with_tier_and_depth(self):
        tiers = mg.LOOT_BOX_TIERS
        rank = lambda box: tiers.index(box.split()[0])
        for floor in mg.FLOORS:
            ladder = [rank(mg.boss_box(t, floor))
                      for t in ("neighborhood", "borough", "city", "top")]
            self.assertEqual(ladder, sorted(ladder),
                             f"floor {floor}: a bigger boss drops a smaller box")
        self.assertGreater(rank(mg.boss_box("neighborhood", 3)),
                           rank(mg.boss_box("neighborhood", 1)),
                           "a floor 3 neighborhood boss should out-drop floor 1's")
        self.assertEqual(mg.boss_box("neighborhood", 1), "Bronze Boss Box",
                         "the books' first neighborhood boss drops Bronze")
        for floor in mg.FLOORS:
            for tier in self.lb["boss_box"]["base"]:
                self.assertNotIn("Celestial", mg.boss_box(tier, floor),
                                 f"a {tier} boss on floor {floor} drops Celestial")

    # -- what is in a box ------------------------------------------------------

    def test_effects_fit_the_item_they_are_on(self):
        """A leather jacket that "deals extra damage to undead", or a spell scroll
        that "silences its owner's footsteps", was what the first draft produced."""
        checked = 0
        for box in self.opened:
            for line in box.items:
                base, effects = self._parse(line)
                barred = self.NEVER.get(self.item_pool[base], set())
                for e in effects:
                    checked += 1
                    self.assertNotIn(self.fx_kind[e], barred,
                                     f"{box.label}: {base} cannot carry {e!r}")
        # Only headline items are enchanted now, so there are fewer to check.
        self.assertGreater(checked, 500)

    def test_every_enchantable_pool_has_a_rule(self):
        """A pool added to the bank without a line in NEVER would be checked
        against nothing at all."""
        for pool in self.lb["enchantable"]:
            self.assertIn(pool, self.NEVER, f"no fit rule for the {pool!r} pool")

    def test_no_celestial_power_outside_a_celestial_box(self):
        """The first draft let a Gold Benefactor box open at Celestial, with every
        item carrying three world-breaking effects."""
        for box in self.opened:
            if box.opened_at == "Celestial":
                continue
            for line in box.items:
                for e in self._parse(line)[1]:
                    self.assertNotEqual(self.fx_tier[e], "Celestial",
                                        f"{box.label} carries {e!r}")

    # The house rule, stated here rather than read from LOOT_BOXES["contents"].
    # One item at the box's own tier; every other item lesser, its tier rolled:
    #   Celestial  3 more, each Gold to Legendary -- never a second Celestial
    #   Legendary  2 more, each Silver to Platinum -- never above or equal to it
    #   Gold       4 more, each Bronze or Silver
    #   Silver     3 more, Bronze
    #   Bronze     3 in all, Bronze
    # Platinum was left out of the rule: 3 more, each Bronze to Gold, between
    # Gold's four and Legendary's two and, like the rest, at most three tiers down.
    ITEMS = {"Bronze": 3, "Silver": 4, "Gold": 5, "Platinum": 4, "Legendary": 3,
             "Celestial": 4}
    LESSER = {"Bronze": {"Bronze"}, "Silver": {"Bronze"}, "Gold": {"Bronze", "Silver"},
              "Platinum": {"Bronze", "Silver", "Gold"},
              "Legendary": {"Silver", "Gold", "Platinum"},
              "Celestial": {"Gold", "Platinum", "Legendary"}}

    def test_a_box_holds_exactly_its_tiers_count(self):
        for box in self.opened:
            self.assertEqual(len(box.items), self.ITEMS[box.opened_at],
                             f"{box.label} (opened at {box.opened_at}) holds "
                             f"{len(box.items)} items")
            self.assertEqual(len(box.item_tiers), len(box.items))

    def test_one_item_at_the_boxs_tier_and_the_rest_rolled_lower(self):
        """A Celestial Weapon box used to hold four overpowered weapons: one at
        Celestial and three at Legendary. Now one item is at the box's tier and
        every other one rolls its tier from the box's lesser range."""
        for box in self.opened:
            self.assertEqual(box.item_tiers[0], box.opened_at,
                             f"{box.label}: the first item is {box.item_tiers[0]}")
            for t in box.item_tiers[1:]:
                self.assertIn(t, self.LESSER[box.opened_at],
                              f"{box.label}: a lesser item rolled {t}")
            named = [line for line in box.items if line.startswith("**")]
            self.assertLessEqual(len(named), 1, f"{box.label} names {len(named)} items")
            if named:
                self.assertEqual(named[0], box.items[0], "only the headline is named")

    def test_every_item_carries_its_own_tiers_power(self):
        """The tier each item says it rolled is the tier its enchantments are from:
        a Platinum lesser item in a Celestial box carries Platinum effects, two of
        them, and a Bronze one carries none."""
        per_item = {"Bronze": 0, "Silver": 1, "Gold": 1, "Platinum": 2, "Legendary": 2,
                    "Celestial": 3}
        checked = 0
        for box in self.opened:
            for tier, line in zip(box.item_tiers, box.items):
                base, effects = self._parse(line)
                if self.item_pool[base] not in self.lb["enchantable"]:
                    self.assertEqual(effects, [])
                    continue
                checked += 1
                self.assertEqual(len(effects), per_item[tier],
                                 f"{box.label}: a {tier} {base} carries {effects}")
                for e in effects:
                    self.assertEqual(self.fx_tier[e], tier,
                                     f"{box.label}: a {tier} {base} carries {e!r}")
        self.assertGreater(checked, 500)

    def test_lesser_tiers_roll_across_their_whole_range(self):
        """A roll, not a fixed step: across many boxes every tier a range allows
        comes up, and over-weighting any one of them would show here."""
        roller = mg.LootRoller(77)
        for tier, allowed in self.LESSER.items():
            seen = collections.Counter()
            for _ in range(150):
                seen.update(roller.open(tier, "Boss").item_tiers[1:])
            self.assertEqual(set(seen), allowed, f"{tier} lesser tiers: {dict(seen)}")
            if len(allowed) > 1:
                share = min(seen.values()) / sum(seen.values())
                self.assertGreater(share, 0.6 / len(allowed),
                                   f"{tier} lesser tiers are lopsided: {dict(seen)}")

    def test_a_celestial_weapon_box_is_one_god_tier_weapon(self):
        """The case the rule was written for: four items, one of them a Celestial
        weapon, the other three Gold to Legendary and mostly not weapons."""
        weapons = set(self.lb["items"]["weapons"])
        for seed in range(8):
            box = mg.LootRoller(seed).open("Celestial", "Weapon")
            self.assertEqual(len(box.items), 4)
            self.assertIn(self._parse(box.items[0])[0], weapons, "the main item is a weapon")
            self.assertEqual(box.item_tiers.count("Celestial"), 1, box.markdown())
            celestial = [l for l in box.items
                         if any(self.fx_tier[e] == "Celestial" for e in self._parse(l)[1])]
            self.assertEqual(len(celestial), 1, f"seed {seed}: {box.markdown()}")
            self.assertTrue(celestial[0].startswith("**"), "the god-tier one is named")
            self.assertTrue(set(box.item_tiers[1:]) <= {"Gold", "Platinum", "Legendary"})

    def test_the_line_reads_down_the_tiers(self):
        """Grouped under the tier each item rolled, best first, because a plain
        potion or tool says nothing about its own tier."""
        order = mg.LOOT_BOX_TIERS
        for box in self.opened[:200]:
            line = box.markdown()
            shown = [t for t in reversed(order) if f" {t}: " in line or f"— {t}: " in line]
            self.assertEqual(shown, sorted(set(box.item_tiers), key=order.index,
                                           reverse=True), line)
            ranks = [order.index(t) for t in box.item_tiers]
            self.assertEqual(ranks, sorted(ranks, reverse=True), line)

    def test_plain_items_follow_their_slot(self):
        """Potions, tools, explosives and oddities take no enchantment, so they come
        in bands, chosen by the tier the item rolled: a Gold Sapper's box leads with
        mid-band kit, a Legendary lesser item in a Celestial box is high-band."""
        bands = mg.LOOT_BANDS
        checked = 0
        for box in self.opened:
            for tier, line in zip(box.item_tiers, box.items):
                base, _ = self._parse(line)
                band = self.item_band[base]
                if band is None:
                    continue
                checked += 1
                self.assertEqual(band, bands[tier],
                                 f"{box.label}: {base!r} is a {band} item in a "
                                 f"{tier} slot")
        self.assertGreater(checked, 300)

    # -- gold ------------------------------------------------------------------

    # (1-10) x Floor ^ Tier, with Bronze counting as 1. Stated here, not read from
    # the bank, so a change to the formula has to be made in both places.
    POWER = {"Bronze": 1, "Silver": 2, "Gold": 3, "Platinum": 4, "Legendary": 5,
             "Celestial": 6}

    def test_floor_1_boxes_hold_no_gold(self):
        roller = mg.LootRoller(1)
        for tier in mg.LOOT_BOX_TIERS:
            box = roller.open(tier, "Boss", floor=1)
            self.assertEqual(box.gold, 0, f"a Floor 1 {box.label} holds gold")
            self.assertNotIn(" gold**", box.markdown())

    def test_gold_is_one_to_ten_times_floor_to_the_tier(self):
        roller = mg.LootRoller(2)
        for floor in (2, 3, 6, 9, 11):
            for tier in mg.LOOT_BOX_TIERS:
                scale = floor ** self.POWER[tier]
                seen = set()
                for _ in range(40):
                    box = roller.open(tier, "Adventurer", floor=floor)
                    self.assertEqual(box.gold % scale, 0,
                                     f"{box.gold} is not a multiple of {floor}^{self.POWER[tier]}")
                    self.assertTrue(1 <= box.gold // scale <= 10,
                                    f"Floor {floor} {tier}: {box.gold} is outside 1-10 x {scale}")
                    seen.add(box.gold // scale)
                self.assertGreater(len(seen), 5, "the 1-10 roll is not varying")
                self.assertIn(f"**{box.gold:,} gold**", box.markdown())

    def test_a_benefactor_box_pays_gold_at_the_tier_it_opens_at(self):
        box = mg.LootRoller(4).open("Bronze", "Benefactor", floor=5)
        self.assertEqual(box.opened_at, "Platinum")
        self.assertEqual(box.gold % 5 ** self.POWER["Platinum"], 0)
        self.assertGreaterEqual(box.gold, 5 ** self.POWER["Platinum"])

    def test_a_bounty_box_is_its_bounty_not_a_roll(self):
        for name in ("Bounty", "Venison"):
            box = mg.LootRoller(1).open("Legendary", name, floor=9)
            self.assertEqual(box.gold, 0, f"a {name} box added rolled gold to the bounty")

    def test_the_floor_table_shows_that_floors_gold(self):
        md = mg.generate(6, "small", 4242, images=False).markdown
        self.assertIn("| Gold on Floor 6 |", md)
        self.assertIn(f"| Celestial | 4 | {6 ** 6:,}–{10 * 6 ** 6:,} |", md)
        self.assertIn('--open-box "a Silver Boss Box" --floor 6', md)
        md1 = mg.generate(1, "small", 4242, images=False).markdown
        self.assertNotIn("Gold on Floor", md1)
        self.assertIn("Boxes hold no gold until Floor 2.", md1)

    def test_a_themed_box_leads_with_what_its_name_promises(self):
        """A Shoe box -- which a floor 1 achievement already awards -- leads with
        shoes. What sits beside them follows the same split as every other box."""
        roller = mg.LootRoller(8)
        footwear = set(self.lb["items"]["footwear"])
        for tier in mg.LOOT_BOX_TIERS:
            box = roller.open(tier, "Shoe")
            self.assertIn(self._parse(box.items[0])[0], footwear, box.markdown())

    # -- what the lesser items are ---------------------------------------------

    # The house rule, stated here rather than read from LOOT_BOXES["secondary"]:
    # each lesser item is the main item's own kind 15% of the time, another piece
    # of equipment (armor, accessories, weapons) 35%, a consumable 50%.
    SPLIT = {"same": 0.15, "equip": 0.35, "consumable": 0.50}
    EQUIP = {"weapons", "apparel", "footwear"}
    CONSUMABLE = {"consumables"}

    def _kind(self, main_pool, line):
        pool = self.item_pool[self._parse(line)[0]]
        if pool == main_pool:
            return "same"
        if pool in self.EQUIP:
            return "equip"
        if pool in self.CONSUMABLE:
            return "consumable"
        return f"other ({pool})"

    def test_lesser_items_split_fifteen_thirty_five_fifty(self):
        """Without this a box was one great thing and a pile to pawn. Checked for a
        box whose main item is equipment, one whose main item is not, and one whose
        main item is itself a consumable."""
        for tier, kind in (("Gold", "Weapon"), ("Legendary", "Boss"), ("Gold", "Quest"),
                           ("Platinum", "Survivor's")):
            roller = mg.LootRoller(31)
            seen = collections.Counter()
            for _ in range(1200):
                box = roller.open(tier, kind)
                main = self.item_pool[self._parse(box.items[0])[0]]
                seen.update(self._kind(main, l) for l in box.items[1:])
            total = sum(seen.values())
            others = [k for k in seen if k.startswith("other")]
            self.assertEqual(others, [], f"{tier} {kind}: lesser items from {others}")
            for k, want in self.SPLIT.items():
                got = seen[k] / total
                if kind == "Survivor's" and k != "equip":
                    continue        # its main item may itself be a consumable
                self.assertAlmostEqual(got, want, delta=0.04,
                                       msg=f"{tier} {kind}: {k} is {got:.0%}, want {want:.0%}")

    def test_a_weapon_box_reaches_another_weapon_only_through_the_fifteen(self):
        """Equipment leaves out the main item's own pool. Counted as equipment too,
        a Weapon box's lesser items would be weapons about 27% of the time."""
        roller = mg.LootRoller(5)
        weapons = set(self.lb["items"]["weapons"])
        lesser = [l for _ in range(1500) for l in roller.open("Gold", "Weapon").items[1:]]
        share = sum(self._parse(l)[0] in weapons for l in lesser) / len(lesser)
        self.assertAlmostEqual(share, 0.15, delta=0.03,
                               msg=f"{share:.0%} of a Weapon box's lesser items are weapons")

    def test_an_unknown_type_still_opens(self):
        """Achievements name their own boxes after whatever earned them. A name the
        bank does not know opens from the Adventurer pools and says so."""
        box = mg.LootRoller(1).open_text("Gold Podiatrist")
        self.assertEqual(box.label, "Gold Podiatrist Box")
        self.assertTrue(box.items)
        self.assertIn("Adventurer pools", box.note)

    def test_special_boxes_hold_what_they_hold(self):
        for name in ("Bounty", "Venison"):
            box = mg.LootRoller(1).open("Legendary", name)
            self.assertEqual(len(box.items), 1)
            self.assertIn("bounty", box.items[0])

    def test_a_bronze_benefactor_box_beats_a_gold_adventurer_box(self):
        """Straight from the books, and the reason Benefactor boxes open above
        their label."""
        tiers = mg.LOOT_BOX_TIERS
        for seed in range(5):
            roller = mg.LootRoller(seed)
            benefactor = roller.open("Bronze", "Benefactor")
            adventurer = roller.open("Gold", "Adventurer")
            self.assertGreater(tiers.index(benefactor.opened_at),
                               tiers.index(adventurer.opened_at))
            best = lambda box: max(tiers.index(self.fx_tier[e]) for line in box.items
                                   for e in self._parse(line)[1])
            self.assertGreater(best(benefactor), best(adventurer))

    def test_the_benefactor_cap_limits_promotion_not_the_box(self):
        """Capping the promotion at Legendary once demoted a Celestial Benefactor
        box to Legendary."""
        box = mg.LootRoller(9).open("Celestial", "Benefactor")
        self.assertEqual(box.opened_at, "Celestial")
        effects = self._parse(box.items[0])[1]
        self.assertTrue(effects and all(self.fx_tier[e] == "Celestial" for e in effects))
        self.assertEqual(mg.LootRoller(9).open("Gold", "Benefactor").opened_at, "Legendary")

    # -- volume: boxes are the most-generated thing in the project -------------

    # The floor under each bank, stated here rather than read from it -- a test
    # that took the bank's own size let a probe shrink weapons back to 16 and
    # pass. Sized against measured volume: a large Floor 1 names about 37 boxes,
    # a huge one about 100, and a DM opens them one after another.
    MIN_POOL = {"weapons": 45, "apparel": 45, "magic": 35, "offworld": 25, "pet": 25,
                "footwear": 12}
    MIN_BAND = 8
    MIN_EFFECTS = 20
    # Half of every lesser item is a consumable, and early floors' lesser items are
    # mostly Bronze or Silver, so the low band carries the most: at 20 it put the
    # same ration pack in six boxes on one huge floor.
    MIN_CONSUMABLES = {"low": 50, "mid": 35, "high": 25}

    def test_the_banks_stay_big(self):
        for pool, floor in self.MIN_POOL.items():
            self.assertGreaterEqual(len(self.lb["items"][pool]), floor,
                                    f"the {pool} pool shrank below {floor}")
        for band, floor in self.MIN_CONSUMABLES.items():
            self.assertGreaterEqual(len(self.lb["items"]["consumables"][band]), floor,
                                    f"consumables/{band} shrank below {floor}")
        for pool, bank in self.lb["items"].items():
            if isinstance(bank, dict):
                for band, items in bank.items():
                    self.assertGreaterEqual(len(items), self.MIN_BAND,
                                            f"{pool}/{band} shrank below {self.MIN_BAND}")
        for tier, effects in self.lb["effects"].items():
            self.assertGreaterEqual(len(effects), self.MIN_EFFECTS,
                                    f"{tier} enchantments shrank below {self.MIN_EFFECTS}")

    def test_weapon_boxes_in_a_row_repeat_nothing_until_the_pool_is_spent(self):
        """One roller holds its decks, so the CLI's --count and an app session do
        not repeat an item until the pool runs out. Before the banks grew, a
        Weapon box repeated its first item by the eighth box.

        Counted in weapons, not boxes: how many a box holds is a house rule that
        changes, and a Gold box went from two weapons to five."""
        floor = self.MIN_POOL["weapons"]
        weapons = set(self.lb["items"]["weapons"])
        for seed in (3, 4, 5):
            roller = mg.LootRoller(seed)
            seen = []
            while len(seen) < floor:
                seen += [b for b in (self._parse(l)[0] for l in
                                     roller.open("Gold", "Weapon").items) if b in weapons]
            seen = seen[:floor]
            self.assertEqual(len(seen), len(set(seen)),
                             f"seed {seed}: a weapon came round twice in the first "
                             f"{floor} drawn")

    def test_the_banks_are_big_enough_for_a_whole_floor(self):
        """Every box a large Floor 1 names, opened in one session, without a repeat.
        A huge floor names about a hundred boxes; the banks are not sized for
        that, and repeats are allowed there, but not clumped."""
        fl = mg.generate(1, "large", 4242, images=False).floor
        roller = mg.LootRoller(9)
        bases = [self._parse(l)[0] for sg in mg.floor_box_suggestions(fl)
                 for l in roller.open(sg.tier, sg.box_type).items]
        self.assertGreater(len(bases), 40)
        self.assertEqual(len(bases), len(set(bases)),
                         "a large floor's boxes repeat an item within one session")
        fl = mg.generate(1, "huge", 77, images=False).floor
        roller = mg.LootRoller(9)
        counts = collections.Counter(self._parse(l)[0] for sg in mg.floor_box_suggestions(fl)
                                     for l in roller.open(sg.tier, sg.box_type).items)
        self.assertLessEqual(max(counts.values()), 3,
                             f"a huge floor's boxes clump: {counts.most_common(2)}")

    def test_a_seeded_roller_replays_exactly(self):
        def run(seed):
            r = mg.LootRoller(seed)
            return [r.open(t, k).markdown() for t, k in
                    (("Gold", "Boss"), ("Bronze", "Pet"), ("Legendary", "Weapon"))]
        self.assertEqual(run(42), run(42))
        self.assertNotEqual(run(42), run(43))

    # -- canon -----------------------------------------------------------------

    def test_every_named_box_uses_a_canon_tier(self):
        """"Iron Quest Box" shipped in the quest rewards and is not one of the six.
        Checked across every word bank and every floor's output, so the next one
        is caught wherever it is written."""
        pattern = re.compile(r"\b([A-Z][a-z]+) [A-Z][\w']+(?: [A-Z][\w']+)? Box\b")
        texts = []
        for path in glob.glob(os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "data", "*.json")):
            with open(path, encoding="utf-8") as f:
                texts.append(f.read())
        texts += [mg.generate(f, "medium", 4242, images=False).markdown
                  for f in mg.FLOORS]
        found = 0
        for text in texts:
            for m in pattern.finditer(text):
                found += 1
                self.assertIn(m.group(1), mg.LOOT_BOX_TIERS,
                              f"{m.group(0)!r} names a tier that is not one of the six")
        self.assertGreater(found, 50, "the pattern stopped matching anything")


class BenefactorBoxTest(unittest.TestCase):
    """A sponsor can see what the crawler is about to face. A Benefactor box the
    DM prepares for a threat leads with a counter worked into its main item, spends
    every lesser item on the same threat, and carries a note from the sponsor
    hinting at it. Unprepared, it is the ordinary Benefactor box."""

    @classmethod
    def setUpClass(cls):
        cls.ben = mg.LOOT_BOXES["benefactor"]
        cls.bands = mg.LOOT_BANDS

    def _counter_band(self, threat, item):
        for band, items in self.ben["threats"][threat]["items"].items():
            if item in items:
                return band
        return None

    def test_every_lesser_item_counters_the_threat_at_its_own_tier(self):
        roller = mg.LootRoller(12)
        for threat in mg.benefactor_threats():
            for tier in mg.LOOT_BOX_TIERS:
                box = roller.open(tier, "Benefactor", 4, threat=threat)
                self.assertEqual(box.threat, threat)
                for t, item in zip(box.item_tiers[1:], box.items[1:]):
                    band = self._counter_band(threat, item)
                    self.assertIsNotNone(band, f"{box.label} for {threat}: {item!r} is "
                                               f"not a counter to it")
                    self.assertEqual(band, self.bands[t],
                                     f"{box.label} for {threat}: a {t} slot holds a {band} counter")

    def test_the_main_item_carries_the_threats_counter(self):
        roller = mg.LootRoller(13)
        for threat in mg.benefactor_threats():
            for tier in ("Bronze", "Gold", "Celestial"):
                box = roller.open(tier, "Benefactor", 4, threat=threat)
                band = self.bands[box.opened_at]
                counters = self.ben["threats"][threat]["effects"][band]
                self.assertTrue(any(c in box.items[0] for c in counters),
                                f"{box.label} for {threat}: {box.items[0]}")

    def test_the_counter_takes_a_slot_rather_than_adding_one(self):
        """A prepared main item carries exactly as many enchantments as any other
        item of its tier; the counter replaces one of them. Added on top, a
        Platinum main item would carry three."""
        per_item = {"Platinum": 2, "Legendary": 2, "Celestial": 3}
        roller = mg.LootRoller(16)
        for threat in mg.benefactor_threats():
            for tier in ("Bronze", "Silver", "Celestial"):
                box = roller.open(tier, "Benefactor", 4, threat=threat)
                head = re.sub(r"^\*\*.*?\*\*, ", "", box.items[0])
                if " (" not in head:
                    continue                       # a plain main item has no slots
                effects = head[head.index(" (") + 2:-1].split("; ")
                self.assertEqual(len(effects), per_item[box.opened_at],
                                 f"{box.label} for {threat}: {box.items[0]}")

    def test_the_sponsor_hints_and_signs_it(self):
        roller = mg.LootRoller(14)
        for threat in mg.benefactor_threats():
            box = roller.open("Silver", "Benefactor", 4, threat=threat)
            self.assertTrue(any(f'"{n}"' in box.sponsor_note
                                for n in self.ben["threats"][threat]["notes"]), box.sponsor_note)
            self.assertTrue(any(f"From {sp}:" in box.sponsor_note for sp in self.ben["sponsors"]))
            self.assertTrue(box.markdown().endswith(box.sponsor_note), box.markdown())
            self.assertIn(f"prepared for {self.ben['threats'][threat]['label']}", box.markdown())

    def test_unprepared_it_is_the_ordinary_benefactor_box(self):
        """No counters, the usual 15/35/50 lesser items, and a sponsor's compliments."""
        every_counter = {i for t in self.ben["threats"].values()
                         for band in t["items"].values() for i in band}
        roller = mg.LootRoller(15)
        for _ in range(60):
            box = roller.open("Gold", "Benefactor", 4)
            self.assertEqual(box.threat, "")
            self.assertFalse(set(box.items) & every_counter, box.markdown())
            self.assertTrue(any(f'"{n}"' in box.sponsor_note
                                for n in self.ben["untargeted_notes"]))

    def test_a_threat_can_be_named_by_key_or_label(self):
        self.assertEqual(mg.find_threat("Fire"), "fire")
        self.assertEqual(mg.find_threat("the undead"), "undead")
        self.assertEqual(mg.find_threat("  SPELLCASTERS "), "casters")
        with self.assertRaises(mg.MapgenError):
            mg.find_threat("lava")

    def test_only_a_benefactor_box_is_prepared(self):
        box = mg.LootRoller(1).open("Gold", "Boss", 4, threat="fire")
        self.assertEqual(box.threat, "")
        self.assertEqual(box.sponsor_note, "")

    # A floor under each threat's stock, stated here rather than read from it.
    def test_every_threat_is_stocked(self):
        self.assertGreaterEqual(len(mg.benefactor_threats()), 12)
        self.assertGreaterEqual(len(self.ben["sponsors"]), 20)
        for key, t in self.ben["threats"].items():
            for band in ("low", "mid", "high"):
                self.assertGreaterEqual(len(t["items"][band]), 4, f"{key}/{band} items")
                self.assertGreaterEqual(len(t["effects"][band]), 1, f"{key}/{band} effects")
            self.assertGreaterEqual(len(t["notes"]), 3, f"{key} notes")


class ThreatSuggestionTest(unittest.TestCase):
    """From Floor 4, where sponsors start, each floor suggests what a Benefactor
    box could be prepared for, read from the floor's own bosses, mobs and hazards.
    It suggests; the DM picks. The risks are a floor reporting threats that are
    not on it, and the shared sections drowning out the ones that are."""

    def test_the_ghost_floor_is_about_the_undead(self):
        """Floor 8 is Ghosts of Earth. If the scan cannot see that, it sees nothing."""
        for seed in range(1, 6):
            md = mg.generate(8, "medium", seed, images=False).markdown
            top = mg.threat_suggestions(mg.floor_body(md))
            self.assertTrue(top, f"floor 8 seed {seed} suggests nothing")
            self.assertEqual(top[0].threat, "undead", f"floor 8 seed {seed}: {top}")

    def test_only_the_floors_own_text_counts(self):
        """Every floor carries the loot rules, which name all twelve threats, the
        stat rules and the crawler roster. None of that is about this floor."""
        body = "- Mob: Spore Hounds\n- Flora: spore clouds and a slow disease debuff\n"
        shared = ("\n## Other Crawlers\n- a team of fire mages, burning everything\n"
                  "\n## Loot Boxes\nfire, cold, poison, undead, water, darkness\n"
                  "\n## Crawler Stats\nPoison, disease, cold, heat are all Constitution\n")
        found = {t.threat for t in mg.threat_suggestions(mg.floor_body(body + shared))}
        self.assertEqual(found, {"poison"})
        for floor in mg.FLOORS:
            md = mg.generate(floor, "small", 4242, images=False).markdown
            body = mg.floor_body(md)
            for head in ("## Other Crawlers", "## Loot Boxes", "## Crawler Stats",
                         "## Achievements"):
                self.assertNotIn(head, body, f"floor {floor} body still holds {head}")

    def test_one_stray_word_is_not_a_theme(self):
        self.assertEqual(mg.threat_suggestions("- A brazier gives off a little fire.\n"), [])
        two = "- A brazier gives off fire.\n- The boss breathes flames.\n"
        self.assertEqual([t.threat for t in mg.threat_suggestions(two)], ["fire"])

    def test_signs_of_more_than_one_word_still_count(self):
        """Words are looked up one at a time, so a sign with a space or a hyphen in
        it needs its own pass. Without it these read as nothing at all."""
        plates = "- A pressure plate by the door.\n- Another pressure plate under the rug.\n"
        self.assertEqual([t.threat for t in mg.threat_suggestions(plates)], ["traps"])
        black = "- The tunnel goes pitch-black.\n- Pitch-black again past the bend.\n"
        self.assertEqual([t.threat for t in mg.threat_suggestions(black)], ["darkness"])

    def test_a_hyphen_still_splits_words(self):
        """The old pattern split on \\b, so "Rot-Touched" yielded "rot". Splitting
        on whole words must keep doing that."""
        text = "- Janitor mob: Rot-Touched Ursines.\n- Their bite leaves rot behind.\n"
        self.assertEqual([t.threat for t in mg.threat_suggestions(text)], ["poison"])

    def test_names_do_not_count(self):
        """A warlord called Ember is not a fire hazard; a boss called The Burning
        Goblin is a name. A mob that is literally made of fire still is."""
        names = "- Warlord: Peregrine Ember\n- Neighborhood Boss: The Burning Goblin\n"
        self.assertEqual(mg.threat_suggestions(names), [])
        real = "- Mob: Fire Changelings\n- They burn whatever they touch.\n"
        self.assertEqual([t.threat for t in mg.threat_suggestions(real)], ["fire"])

    # Phrases measured as false signals, stated here so the words that caused them
    # cannot quietly go back into the sign lists.
    FALSE_SIGNALS = [
        "Segment 1: rival pit lane", "Segment 4: rival pit lane",
        "After dark they go quiet and stalk.", "At night they go quiet after dark.",
        "Blind, but hunt by sound.", "Blind, but they hunt by smell.",
        "No magical repair, no consumable refresh.", "No magical healing between heats.",
        "A dead end with a draft.", "Another dead end.",
        "436 is the Abyss: bare platform.", "The Abyss again.",
        "The racer falls behind.", "A flight of stairs, then another flight.",
    ]

    def test_measured_false_signals_stay_out(self):
        text = "\n".join(f"- {p}" for p in self.FALSE_SIGNALS)
        self.assertEqual(mg.threat_suggestions(text, top=12), [],
                         "a phrase that is not a hazard read as one")

    def test_floors_before_sponsors_suggest_nothing(self):
        for floor in mg.FLOORS:
            md = mg.generate(floor, "medium", 4242, images=False).markdown
            has = "### What a sponsor would see coming" in md
            if floor < 4:
                self.assertFalse(has, f"floor {floor} suggests threats before sponsors exist")

    def test_the_floor_table_and_command_use_the_suggestions(self):
        for floor in (6, 8, 10):
            md = mg.generate(floor, "medium", 4242, images=False).markdown
            top = mg.threat_suggestions(mg.floor_body(md))
            self.assertTrue(top)
            for t in top:
                self.assertEqual(mg.find_threat(t.threat), t.threat)
                self.assertIn(f"`--for {t.threat}` ({t.label}) | {t.count} |", md)
            self.assertIn(f'--open-box "Gold Benefactor" --for {top[0].threat} --floor {floor}', md)

    def test_the_written_table_reads_only_the_floor_body(self):
        """Checked across many floors, because the roster's text -- a team of Fire
        Mage Arcanists, a status about being caught in the open -- only tips the
        count now and then, and three floors can agree by chance."""
        for floor in (4, 5, 6, 8, 9, 10, 11):
            for seed in range(1, 9):
                md = mg.generate(floor, "medium", seed, images=False).markdown
                want = mg.threat_suggestions(mg.floor_body(md))
                section = md.split("### What a sponsor would see coming")
                rows = []
                if len(section) > 1:
                    table = section[1].split("###")[0]
                    rows = re.findall(r"\| `--for (\w+)` \([^)]*\) \| (\d+) \|", table)
                self.assertEqual([(r, int(n)) for r, n in rows],
                                 [(t.threat, t.count) for t in want],
                                 f"floor {floor} seed {seed}")

    def test_the_example_is_from_the_floor(self):
        md = mg.generate(6, "medium", 4242, images=False).markdown
        body = re.sub(r"[*_`]", "", mg.floor_body(md))
        for t in mg.threat_suggestions(mg.floor_body(md)):
            core = t.example.strip("…").strip()
            self.assertIn(core[:30], re.sub(r"\s+", " ", body), t.example)

    def test_every_threat_has_signs(self):
        for key, t in mg.LOOT_BOXES["benefactor"]["threats"].items():
            self.assertGreaterEqual(len(t["signs"]), 8, f"{key} has too few signs")


class FanBoxTest(unittest.TestCase):
    """A Fan box is whatever the audience decided. The DM sets how much they liked
    whatever earned it; the audience votes twice -- two likes good, two dislikes
    bad, a split neutral. Good is one useful item at the box's tier, neutral one
    harmless and probably humiliating thing, bad a cost that grows with the box."""

    # The vote, stated here rather than read from mg.fan_odds.
    @staticmethod
    def ODDS(liked):
        p = liked / 100
        return {"good": p * p, "neutral": 2 * p * (1 - p), "bad": (1 - p) ** 2}

    GOOD_POOLS = {"weapons", "apparel", "footwear", "consumables"}

    @classmethod
    def setUpClass(cls):
        cls.fan = mg.LOOT_BOXES["fan"]
        cls.pool_of = {}
        for pool, bank in mg.LOOT_BOXES["items"].items():
            for item in ([i for b in bank.values() for i in b] if isinstance(bank, dict)
                         else bank):
                cls.pool_of[item] = pool

    def test_the_odds_are_a_double_vote(self):
        for liked in (0, 10, 25, 50, 70, 90, 100):
            want = self.ODDS(liked)
            got = mg.fan_odds(liked)
            for k in want:
                self.assertAlmostEqual(got[k], want[k], places=9, msg=f"{liked}% {k}")
        for bad in (-1, 101):
            with self.assertRaises(mg.MapgenError):
                mg.fan_odds(bad)

    def test_the_rolls_follow_the_odds(self):
        roller = mg.LootRoller(21)
        for liked in (30, 70):
            seen = collections.Counter(roller.open("Silver", "Fan", 3, liked=liked).outcome
                                       for _ in range(2000))
            for k, want in self.ODDS(liked).items():
                self.assertAlmostEqual(seen[k] / 2000, want, delta=0.04,
                                       msg=f"{liked}% liked: {k} came up {seen[k] / 2000:.0%}")

    def test_a_crowd_that_loved_or_hated_it_is_certain(self):
        roller = mg.LootRoller(22)
        self.assertEqual({roller.open("Gold", "Fan", 3, liked=100).outcome
                          for _ in range(50)}, {"good"})
        self.assertEqual({roller.open("Gold", "Fan", 3, liked=0).outcome
                          for _ in range(50)}, {"bad"})

    def test_good_is_one_useful_item_at_the_boxs_tier(self):
        roller = mg.LootRoller(23)
        fx_tier = {e: t for t, tier in mg.LOOT_BOXES["effects"].items() for e, _ in tier}
        for tier in mg.LOOT_BOX_TIERS:
            for _ in range(20):
                box = roller.open(tier, "Fan", 3, liked=100)
                self.assertEqual(len(box.items), 1, box.markdown())
                base = re.sub(r"^\*\*.*?\*\*, ", "", box.items[0]).split(" (")[0]
                self.assertIn(self.pool_of.get(base), self.GOOD_POOLS, box.items[0])
                inner = box.items[0][len(re.sub(r" \(.*", "", box.items[0])) + 2:-1]
                for e in (inner.split("; ") if " (" in box.items[0] else []):
                    self.assertEqual(fx_tier[e], tier, f"{box.label}: {e!r}")

    def test_neutral_is_one_harmless_thing(self):
        roller = mg.LootRoller(24)
        for _ in range(60):
            box = roller.open("Gold", "Fan", 3, liked=50)
            if box.outcome == "neutral":
                self.assertEqual(len(box.items), 1)
                self.assertIn(box.items[0], self.fan["neutral"])

    def test_bad_gets_worse_with_the_box(self):
        roller = mg.LootRoller(25)
        for tier in mg.LOOT_BOX_TIERS:
            band = mg.LOOT_BANDS[tier]
            for _ in range(10):
                box = roller.open(tier, "Fan", 3, liked=0)
                self.assertIn(box.items[0], self.fan["bad"][band],
                              f"a {tier} Fan box drew a bad outcome from outside {band}")

    def test_a_bad_box_holds_no_gold(self):
        roller = mg.LootRoller(26)
        self.assertGreater(roller.open("Gold", "Fan", 6, liked=100).gold, 0)
        self.assertEqual(roller.open("Gold", "Fan", 6, liked=0).gold, 0)
        neutral = next(b for b in (roller.open("Gold", "Fan", 6, liked=50) for _ in range(40))
                       if b.outcome == "neutral")
        self.assertGreater(neutral.gold, 0)

    def test_the_line_shows_the_vote(self):
        box = mg.LootRoller(27).open("Gold", "Fan", 3, liked=70)
        line = box.markdown()
        self.assertIn("70% liked it: 49% good, 42% neutral, 9% bad", line)
        self.assertIn(f"— {box.outcome.title()}", line)

    def test_the_fan_banks_are_stocked(self):
        self.assertGreaterEqual(len(self.fan["neutral"]), 30)
        for band in ("low", "mid", "high"):
            self.assertGreaterEqual(len(self.fan["bad"][band]), 10, band)


class LootCommandLineTest(unittest.TestCase):
    """--open-box, run as a subprocess the way a DM would."""

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _run(self, *args):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        return subprocess.run([sys.executable, os.path.join(self.ROOT, "dcc_mapgen.py"),
                               *args], capture_output=True, text=True, env=env,
                              encoding="utf-8")

    def test_a_pasted_drops_line_opens(self):
        r = self._run("--open-box", "a Silver Boss Box", "--seed", "7")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("- **Silver Boss Box**"), r.stdout)
        self.assertIn("--seed 7", r.stderr)

    def test_count_and_several_boxes(self):
        r = self._run("--open-box", "Gold Weapon", "--open-box", "Bronze Pet",
                      "--count", "3", "--seed", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.strip().splitlines()
        self.assertEqual(sum("Gold Weapon Box" in l for l in lines), 3)
        self.assertEqual(sum("Bronze Pet Box" in l for l in lines), 3)

    def test_the_same_seed_opens_the_same_boxes(self):
        a = self._run("--open-box", "Platinum Boss", "--count", "2", "--seed", "11")
        b = self._run("--open-box", "Platinum Boss", "--count", "2", "--seed", "11")
        self.assertEqual(a.stdout, b.stdout)

    def test_the_floor_sets_the_gold(self):
        deep = self._run("--open-box", "Gold Boss", "--floor", "6", "--seed", "3")
        self.assertEqual(deep.returncode, 0, deep.stderr)
        self.assertRegex(deep.stdout, r"\*\*[\d,]+ gold\*\*")
        self.assertIn("Floor 6", deep.stderr)
        top = self._run("--open-box", "Gold Boss", "--seed", "3")
        self.assertNotIn(" gold**", top.stdout)
        self.assertIn("no gold", top.stderr)

    def test_for_and_liked(self):
        r = self._run("--open-box", "Gold Benefactor", "--for", "the undead",
                      "--open-box", "Gold Fan", "--liked", "0", "--floor", "5", "--seed", "4")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("prepared for the undead", r.stdout)
        self.assertIn("— Bad: ", r.stdout)
        for bad in (("--for", "lava"), ("--liked", "140")):
            r = self._run("--open-box", "Gold Benefactor", *bad)
            self.assertNotEqual(r.returncode, 0)
            self.assertNotIn("Traceback", r.stderr)

    def test_a_box_without_a_tier_is_refused_plainly(self):
        r = self._run("--open-box", "Shiny Boss")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("expected a tier", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class NightThreatTest(unittest.TestCase):
    """A night threat that takes the listed mob off the board has to leave another.

    Two entries in the bank describe a region whose mob only holds it for half the
    cycle -- "Dormant by day", and one whose real predator comes out after dark,
    which used to end with the parenthetical "(roll a second mob)". Neither rolled
    anything, so a region could be described as empty in daylight with nothing
    named to fill it. Tagged entries now get that second mob rolled for them."""

    @staticmethod
    def _regions(floor, size, seed):
        fl = mg.generate(floor, size, seed, images=False).floor
        return [c for c in fl.cells if hasattr(c, "night")]

    @classmethod
    def setUpClass(cls):
        cls.tags = {}
        for entry in mg.NIGHT_THREATS:
            text, shift = mg.night_threat(entry)
            cls.tags[text] = shift
        cls.regions = [r for floor in (3, 6) for size in ("small", "medium", "large")
                       for seed in range(1, 8)
                       for r in cls._regions(floor, size, seed)]

    def test_a_tagged_threat_always_gets_its_second_mob(self):
        tagged = 0
        for reg in self.regions:
            shift = self.tags[reg.night]
            if shift:
                tagged += 1
                self.assertIsNotNone(
                    reg.second_mob,
                    f"{reg.name} is told {reg.night!r} and has nobody holding the "
                    f"other half of the day")
                self.assertEqual(reg.second_shift, shift)
            else:
                self.assertIsNone(
                    reg.second_mob,
                    f"{reg.name} got a second mob from an untagged threat")
        self.assertGreater(tagged, 40, "too few tagged threats drawn to prove anything")

    def test_both_directions_of_the_split_are_used(self):
        shifts = collections.Counter(r.second_shift for r in self.regions if r.second_mob)
        self.assertGreater(shifts["day"], 5, "no region ever needed a daylight mob")
        self.assertGreater(shifts["night"], 5, "no region ever needed an after-dark mob")

    def test_the_second_mob_is_not_the_first(self):
        for reg in self.regions:
            if reg.second_mob:
                self.assertNotEqual(reg.second_mob.name, reg.mob.name,
                                    f"{reg.name} lists the same mob for both shifts")

    def test_the_write_up_names_the_second_mob(self):
        for floor in (3, 6):
            md = mg.generate(floor, "large", 4242, images=False).markdown
            regions = self._regions(floor, "large", 4242)
            split = [r for r in regions if r.second_mob]
            self.assertTrue(split, f"floor {floor} seed 4242 has no split region")
            for reg in split:
                label = ("Holds the region by day" if reg.second_shift == "day"
                         else "Takes the region after dark")
                self.assertIn(f"- **{label}:** {reg.second_mob.name}", md)
            # And the parenthetical that asked the table to do it is gone.
            self.assertNotIn("roll a second mob", md)

    # Phrases that say the listed mob is not in the region for part of the cycle.
    # A line like this without a tag is the original bug: the write-up says the
    # place empties out and names nothing that fills it.
    ABSENCE = ("dormant by day", "do not exist in daylight", "strictly nocturnal",
               "belongs to something else", "something else takes",
               "real predator comes out", "drives them underground",
               "dead by dawn", "moves in", "prey rather than predator",
               "surrender it")

    def test_the_two_original_offenders_stay_tagged(self):
        """Pinned by text, not derived from the bank. Every other test here builds
        its expectations out of NIGHT_THREATS, so untagging an entry moves the
        expectation with it and the regression passes -- which is exactly what
        happened when this was checked against a probe."""
        want = {"Dormant by day": "day", "real predator comes out": "night"}
        for phrase, shift in want.items():
            hits = [mg.night_threat(e) for e in mg.NIGHT_THREATS
                    if phrase in mg.night_threat(e)[0]]
            self.assertEqual(len(hits), 1,
                             f"expected exactly one threat saying {phrase!r}")
            self.assertEqual(hits[0][1], shift,
                             f"{hits[0][0]!r} needs a {shift!r} mob and is tagged "
                             f"{hits[0][1]!r}")

    def test_any_line_that_empties_a_region_is_tagged(self):
        """The bank is meant to be edited, so this has to hold for lines nobody has
        written yet, not only for the two that started it."""
        for entry in mg.NIGHT_THREATS:
            text, shift = mg.night_threat(entry)
            hit = next((p for p in self.ABSENCE if p in text.lower()), None)
            if hit and not shift:
                self.fail(f"{text!r} says {hit!r}, so the region has nobody for "
                          f'half the day. Tag it ["...", "day"] or ["...", "night"] '
                          f"and the generator will roll that mob.")

    def test_no_threat_still_asks_the_table_to_roll(self):
        """The bank is editable, so the instruction can come back. If a line tells
        the DM to roll a mob, the tag that makes the generator do it is missing."""
        for entry in mg.NIGHT_THREATS:
            text, shift = mg.night_threat(entry)
            if "roll a" in text.lower() and "mob" in text.lower():
                self.fail(f"{text!r} asks the table to roll; tag it instead")
            self.assertIn(shift, ("", "day", "night"),
                          f"{text!r} carries an unknown shift {shift!r}")

    def test_a_floor_does_not_repeat_a_night_threat(self):
        """Eight entries against eighteen regions put the same line on four of
        them. Dealt from a deck now, like the loot and the features."""
        for floor, size in ((3, "medium"), (3, "large"), (6, "large")):
            for seed in (1, 77, 4242):
                regions = self._regions(floor, size, seed)
                self.assertLessEqual(len(regions), len(mg.NIGHT_THREATS))
                lines = [r.night for r in regions]
                self.assertEqual(len(set(lines)), len(lines),
                                 f"floor {floor} {size} seed {seed} repeats a night "
                                 f"threat across {len(regions)} regions")


class StatRulesTest(unittest.TestCase):
    """The stat rules are reference text, so the risk is not variety but drift.

    Every number in the section is computed from CRAWLER_STATS["knobs"], and the
    point of that is that the prose and the at-a-glance table cannot disagree. If
    a dial is retuned and only one of them moves, this is what says so."""

    @classmethod
    def setUpClass(cls):
        cls.md = mg.generate(1, "small", 4242, images=False).markdown
        cls.section = cls.md[cls.md.index("## Crawler Stats"):]
        cls.k = mg.CRAWLER_STATS["knobs"]

    def test_every_floor_ends_with_the_rules(self):
        """Last section, on every floor. Appending them inside crawler_section put
        them wherever that floor prints its roster, which on floors 1 and 2 is
        halfway up, ahead of the loot boxes."""
        for floor in mg.FLOORS:
            md = mg.generate(floor, "small", 11, images=False).markdown
            self.assertEqual(md.count("## Crawler Stats"), 1,
                             f"floor {floor} does not carry the rules exactly once")
            headings = [l for l in md.splitlines() if l.startswith("## ")]
            self.assertEqual(headings[-1], "## Crawler Stats",
                             f"floor {floor} ends on {headings[-1]!r}, not the rules")

    def test_all_five_stats_are_covered(self):
        for st in mg.CRAWLER_STATS["stats"]:
            self.assertIn(f"**{st['name']} ({st['abbr']})**", self.section)
        self.assertEqual(len(mg.CRAWLER_STATS["stats"]), 5,
                         "the books have five adjustable stats")

    def test_no_placeholder_survives_into_the_document(self):
        """Every rule line is formatted with the knobs, so a knob renamed in the
        bank without being renamed in the prose would print a raw {placeholder}."""
        self.assertNotIn("{", self.section)
        self.assertNotIn("}", self.section)

    def test_the_prose_and_the_table_agree(self):
        """The whole reason the numbers live in one dict. Retune a dial and both
        the per-stat rules and the table have to move with it."""
        k = self.k
        for score in mg.CRAWLER_STATS["example_scores"]:
            # Inside the At a glance table only: the benchmark table higher up
            # also opens rows with "| 3 |", and matching that one proved nothing.
            table = self.section[self.section.index("### At a glance"):]
            row = next((ln for ln in table.splitlines()
                        if ln.startswith(f"| {score} |")), None)
            self.assertIsNotNone(row, f"no at-a-glance row for score {score}")
            for expected in (f"{score * k['lift_comfortable']:,} lb",
                             f"{score * k['lift_max']:,} lb",
                             f"+{score // k['attack_per']}",
                             f"{score * k['hp_per_con']:,}",
                             f"-{min(score * k['discount_per'], k['discount_cap'])}%"):
                self.assertIn(expected, row,
                              f"score {score} row is missing {expected!r}: {row}")
        self.assertIn(f"STR x {k['lift_comfortable']} lb", self.section)
        self.assertIn(f"STR x {k['lift_max']} lb", self.section)
        self.assertIn(f"CON x {k['hp_per_con']}", self.section)
        self.assertIn(f"+1 per {k['attack_per']} full points", self.section)

    def test_the_canon_anchors_are_not_quietly_edited(self):
        """Four things here come straight out of the books. They are the part that
        is not ours to retune, so they are pinned rather than left to a dial."""
        regen = dict(mg.CRAWLER_STATS["mana_regen"])
        self.assertEqual(regen[3], "1 per hour",
                         "the books put Intelligence 3 at one mana an hour")
        self.assertEqual(regen[17], "1 per minute",
                         "the books put Intelligence 17 at one mana a minute")
        self.assertIn("**Mana: equal to INT.**", self.section,
                      "mana equals Intelligence; it is not a multiple of it")
        marks = dict(mg.CRAWLER_STATS["benchmarks"])
        self.assertIn(4, marks, "4 is the books' average adult human")
        self.assertIn(10, marks, "10 is the books' natural human ceiling")

    def test_the_regen_lookup_takes_the_row_at_or_below_the_score(self):
        self.assertEqual(mg._mana_regen_for(3), "1 per hour")
        self.assertEqual(mg._mana_regen_for(4), "1 per hour")
        self.assertEqual(mg._mana_regen_for(17), "1 per minute")
        self.assertEqual(mg._mana_regen_for(18), "1 per minute")
        self.assertEqual(mg._mana_regen_for(1), "1 per hour")
        top = mg.CRAWLER_STATS["mana_regen"][-1]
        self.assertEqual(mg._mana_regen_for(top[0] + 50), top[1])

    def test_the_rules_are_the_same_on_every_floor_and_seed(self):
        """Reference text, so it must not drift with the dice."""
        reference = "\n".join(mg.stat_rules_section()).rstrip("\n")
        for floor, seed in ((1, 1), (3, 77), (9, 4242), (11, 5)):
            md = mg.generate(floor, "small", seed, images=False).markdown
            section = md[md.index("## Crawler Stats"):].rstrip("\n")
            self.assertEqual(section, reference,
                             f"floor {floor} seed {seed} renders different rules")


class QuestTest(unittest.TestCase):
    """Floors 3 and 6 post three times the quests they used to.

    A large floor carries about thirty across seven towns, against banks of ten
    objectives, ten rewards and eight flavor lines. Every component is dealt from
    a per-floor deck now, and the title has to name something the objective is
    actually about."""

    QUOTA = {"tiny": 3, "small": 3, "medium": 6, "large": 6, "xl": 9}

    @staticmethod
    def _towns(floor, size, seed):
        fl = mg.generate(floor, size, seed, images=False).floor
        return fl, [c for c in fl.cells if hasattr(c, "quests")]

    @classmethod
    def setUpClass(cls):
        cls.floors = {(f, s): cls._towns(f, s, 4242)
                      for f in (3, 6) for s in ("small", "medium", "large")}

    def test_a_town_posts_three_times_what_it_used_to(self):
        for (floor, size), (_, towns) in self.floors.items():
            for st in towns:
                if st.name == "Zockau":
                    continue                 # deliberately posts nothing; see below
                self.assertGreaterEqual(
                    len(st.quests), self.QUOTA[st.size],
                    f"{st.name} on floor {floor} {size} is {st.size} and posts "
                    f"{len(st.quests)} quests, under its quota")
                # A stairwell-unlock quest is assigned before the quota is topped
                # up, so one town on a floor can carry a single extra.
                self.assertLessEqual(len(st.quests), self.QUOTA[st.size] + 1)

    def test_zockau_posts_no_quests(self):
        """Floor 6 replaces a settlement with Zockau, hostile and hunters-only,
        and gives it no quest board. That is deliberate -- pinned here so the
        quota rules above cannot quietly start filling it."""
        found = 0
        for size in ("small", "medium", "large"):
            for seed in (1, 77, 4242):
                _, towns = self._towns(6, size, seed)
                zockau = [t for t in towns if t.name == "Zockau"]
                self.assertEqual(len(zockau), 1, f"floor 6 {size} has no Zockau")
                found += 1
                self.assertEqual(zockau[0].quests, [],
                                 "Zockau is supposed to post nothing")
                self.assertTrue(zockau[0].hostile)
        self.assertEqual(found, 9)

    def test_a_floor_does_not_repeat_a_quest(self):
        for (floor, size), (_, towns) in self.floors.items():
            quests = [q for st in towns for q in st.quests]
            for field, bank in (("objective", mg.QUEST_OBJECTIVE),
                                ("reward", mg.QUEST_REWARD),
                                ("flavor", mg.QUEST_FLAVOR)):
                values = [getattr(q, field) for q in quests]
                self.assertLessEqual(len(values), len(bank),
                                     f"floor {floor} {size} posts more quests than the "
                                     f"{field} bank holds, so this proves nothing")
                counts = collections.Counter(values)
                worst, hits = counts.most_common(1)[0]
                self.assertEqual(hits, 1,
                                 f"floor {floor} {size} uses the {field} {worst!r} "
                                 f"{hits} times across {len(values)} quests")

    def test_the_banks_outlast_a_large_floor(self):
        """The check above is only worth anything while the banks are bigger than
        the quest count. Raising the quota again means raising the banks."""
        for floor in (3, 6):
            _, towns = self.floors[(floor, "large")]
            posted = sum(len(st.quests) for st in towns)
            for name, bank in (("objective", mg.QUEST_OBJECTIVE),
                               ("reward", mg.QUEST_REWARD),
                               ("flavor", mg.QUEST_FLAVOR),
                               ("item", mg.QUEST_ITEM),
                               ("giver role", mg.QUEST_GIVER_ROLE)):
                self.assertGreaterEqual(
                    len(bank), posted,
                    f"a large floor {floor} posts {posted} quests against "
                    f"{len(bank)} {name}s, so it has to repeat itself")

    def test_a_title_only_names_what_the_quest_is_about(self):
        """Drawing the subject before the objective is what lets a title name it --
        and what let "The Price of the Town Charter" sit on top of a boundary
        dispute with no charter in it, because the objective never used the item."""
        for (floor, size), (fl, towns) in self.floors.items():
            regions = [c.name for c in fl.cells if not hasattr(c, "quests")]
            mobs = [c.mob.name for c in fl.cells if hasattr(c, "mob")]
            for st in towns:
                for q in st.quests:
                    with self.subTest(floor=floor, size=size, title=q.title):
                        for mob in mobs:
                            if mob in q.title:
                                self.assertIn(mob, q.objective)
                        for region in regions:
                            if mg.mid_phrase(region) in q.title:
                                self.assertIn(mg.mid_phrase(region), q.objective)
                        for item in mg.QUEST_ITEM:
                            if mg.cap_words(item) in q.title:
                                self.assertIn(item, q.objective)

    def test_a_proper_name_reads_inside_a_sentence(self):
        self.assertEqual(mg.mid_phrase("The Sunken Market"), "the Sunken Market")
        self.assertEqual(mg.mid_phrase("Grayhollow"), "Grayhollow")
        for (floor, size), (_, towns) in self.floors.items():
            for st in towns:
                for q in st.quests:
                    self.assertNotIn(" in The ", q.objective)
                    self.assertNotIn(" to The ", q.objective)
                    self.assertNotIn(" from The ", q.objective)

    def test_title_case_leaves_the_small_words_alone(self):
        """str.title() gives "Magistrate'S Seal" and "Set Of Certified Scales"."""
        self.assertEqual(mg.cap_words("magistrate's seal"), "Magistrate's Seal")
        self.assertEqual(mg.cap_words("set of certified scales"),
                         "Set of Certified Scales")
        self.assertEqual(mg.cap_words("the matter of the miller"),
                         "The Matter of the Miller")
        for (floor, size), (_, towns) in self.floors.items():
            for st in towns:
                for q in st.quests:
                    for small in (" Of ", " The ", " A ", " To ", " From ", " With "):
                        self.assertNotIn(small, q.title, f"{q.title!r}")


class LootAndFeatureTest(unittest.TestCase):
    """A floor should not keep handing out the same loot or the same room.

    Both banks were fifteen entries sampled independently per cell, which is the
    boss-mechanic bug again in a different place: a large floor 3 covered 18 regions
    with 11 loot themes and used one of them three times, and a huge floor 1 put the
    same loot theme on eight of its 64 neighborhoods. They are dealt from a deck
    now, so repeats start only once the bank is spent, and then evenly."""

    @staticmethod
    def _cells(floor, size, seed):
        fl = mg.generate(floor, size, seed, images=False).floor
        return [c for c in (getattr(fl, "cells", None)
                            or getattr(fl, "neighborhoods", None) or [])
                if hasattr(c, "loot")]

    def _banks(self, floor):
        """The feature bank a floor draws from; floors 3 and 6 share the ruins one."""
        return {"loot": mg.LOOT_THEMES,
                "feature": mg.NOTABLE_FEATURES if floor in (1, 2) else mg.RUIN_FEATURES}

    def test_a_floor_does_not_repeat_until_the_bank_runs_out(self):
        for floor, size in ((1, "large"), (2, "large"), (3, "medium"), (3, "large"),
                            (6, "large")):
            for seed in (1, 77, 4242):
                cells = self._cells(floor, size, seed)
                for field, bank in self._banks(floor).items():
                    self.assertLessEqual(len(cells), len(bank),
                                         f"this case no longer fits the {field} bank, "
                                         "so it cannot prove anything")
                    values = [getattr(c, field) for c in cells]
                    self.assertEqual(len(set(values)), len(values),
                                     f"floor {floor} {size} seed {seed} repeats a "
                                     f"{field} across {len(cells)} cells")

    def test_past_the_bank_the_repeats_are_even(self):
        """A continent is 400 neighborhoods against a 58-entry bank, so repetition
        is arithmetic there. Dealing is what keeps it from clumping: every entry is
        used n or n+1 times rather than one of them eight times."""
        for floor, size in ((1, "huge"), (1, "giant")):
            cells = self._cells(floor, size, 4242)
            for field, bank in self._banks(floor).items():
                counts = collections.Counter(getattr(c, field) for c in cells)
                self.assertEqual(len(counts), len(bank),
                                 f"floor {floor} {size} left part of the {field} "
                                 f"bank unused: {len(counts)} of {len(bank)}")
                spread = max(counts.values()) - min(counts.values())
                self.assertLessEqual(spread, 1,
                                     f"floor {floor} {size} uses its busiest {field} "
                                     f"{max(counts.values())} times and its quietest "
                                     f"{min(counts.values())}")

    def test_the_banks_cover_the_sizes_that_are_played(self):
        """The guard above only means something while the banks outlast a large
        floor, which is the biggest size every floor shares."""
        for floor in (1, 3, 6):
            cells = len(self._cells(floor, "large", 4242))
            for field, bank in self._banks(floor).items():
                self.assertGreaterEqual(
                    len(bank), cells,
                    f"the {field} bank ({len(bank)}) is smaller than a large floor "
                    f"{floor} ({cells} cells), so that floor must repeat itself")


class BossMechanicTest(unittest.TestCase):
    """A boss run should not be the same fight repeatedly.

    Mechanics used to be sampled per boss from one 14-entry list, which dedupes the
    pair a single boss gets and nothing across the floor: a large floor 1 repeated
    one mechanic five times over 26 slots, and a huge floor twelve times. They are
    dealt from a shuffled deck now, so a floor exhausts the pool before repeating."""

    @staticmethod
    def _bosses(fl):
        out = []
        for attr in ("neighborhoods", "cells"):
            for item in getattr(fl, attr, []) or []:
                if getattr(item, "boss", None):
                    out.append(item.boss)
        for group in (getattr(fl, "borough_bosses", {}) or {},
                      getattr(fl, "city_bosses", {}) or {}):
            out += list(group.values())
        if getattr(fl, "top_boss", None):
            out.append(fl.top_boss)
        return out

    @classmethod
    def setUpClass(cls):
        cls.known = set(mg.BOSS_MECHANICS["any"]) | set(mg.BOSS_MECHANICS["grand"])

    def _split(self, text):
        """Mechanics are joined with a space, so they are split back by matching."""
        found, rest = [], text
        while rest:
            hit = next((m for m in self.known if rest.startswith(m)), None)
            if hit is None:
                self.fail(f"unrecognised mechanic text: {rest[:60]!r}")
            found.append(hit)
            rest = rest[len(hit):].lstrip()
        return found

    def test_a_floor_does_not_repeat_until_the_pool_runs_out(self):
        pool = len(self.known)
        for floor, size in ((1, "large"), (2, "large"), (3, "large"), (6, "large")):
            with self.subTest(floor=floor, size=size):
                for seed in (1, 2, 3):
                    fl = mg.generate(floor, size, seed, images=False).floor
                    slots = [m for b in self._bosses(fl) for m in self._split(b.mechanic)]
                    counts = collections.Counter(slots)
                    # Repeats are only acceptable once every mechanic has been used.
                    allowed = max(1, -(-len(slots) // pool))
                    worst = max(counts.values()) if counts else 0
                    self.assertLessEqual(
                        worst, allowed,
                        f"floor {floor} {size} seed {seed}: a mechanic appears "
                        f"{worst} times across {len(slots)} slots")

    def test_even_the_biggest_floor_spreads_them(self):
        """A huge floor has more slots than mechanics, so some must repeat -- but
        only about as often as the arithmetic forces."""
        fl = mg.generate(1, "huge", 42, images=False).floor
        slots = [m for b in self._bosses(fl) for m in self._split(b.mechanic)]
        counts = collections.Counter(slots)
        self.assertGreater(len(slots), len(self.known), "expected more slots than mechanics")
        self.assertLessEqual(max(counts.values()), 3,
                             f"a mechanic appears {max(counts.values())} times")
        self.assertGreater(len(counts), len(self.known) * 0.8,
                           "most of the pool should be in play on a floor this size")

    def test_only_grand_bosses_get_arena_scale_mechanics(self):
        """A level-7 neighborhood boss should not be reshaping the arena."""
        grand = set(mg.BOSS_MECHANICS["grand"])
        for seed in range(6):
            fl = mg.generate(1, "large", seed, images=False).floor
            for nb in fl.neighborhoods:
                for mech in self._split(nb.boss.mechanic):
                    self.assertNotIn(mech, grand,
                                     f"neighborhood boss {nb.boss.name} got a grand mechanic")
            for boss in list(fl.borough_bosses.values()) + list(fl.city_bosses.values()):
                mechs = self._split(boss.mechanic)
                self.assertTrue(any(m in grand for m in mechs),
                                f"{boss.name} ({boss.tier}) got no grand mechanic")

    def test_the_pools_are_deep_enough_to_matter(self):
        self.assertGreater(len(mg.BOSS_MECHANICS["any"]), 40)
        self.assertGreater(len(mg.BOSS_MECHANICS["grand"]), 10)
        self.assertGreater(len(mg.PARADE_BOSS_MECHANICS), 12)

    def test_mechanics_are_not_listed_twice(self):
        seen = collections.Counter(m for group in mg.BOSS_MECHANICS.values() for m in group)
        dupes = sorted(m for m, n in seen.items() if n > 1)
        self.assertEqual(dupes, [], f"duplicated mechanics: {dupes}")

    def test_a_deck_deals_everything_before_repeating(self):
        deck = mg.Deck(random.Random(1), ["a", "b", "c"])
        self.assertEqual(sorted(deck.draw_many(3)), ["a", "b", "c"])
        self.assertEqual(sorted(deck.draw_many(3)), ["a", "b", "c"])


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
