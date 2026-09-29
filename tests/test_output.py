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
