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
import json
import os
import random
import re
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
