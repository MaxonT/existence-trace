import datetime
import tempfile
import unittest

from etrace import presets
from etrace.models import xp
from etrace.store import Store

NOW = datetime.datetime(2026, 1, 10, 19, 30, tzinfo=datetime.timezone.utc)
ZERO = {"rhythm": 0, "learning": 0, "boundaries": 0, "action": 0, "reflection": 0, "connection": 0}


def levels(**overrides):
    return dict(ZERO, **overrides)


def event(event_id, occurred_on, points, **extra):
    return dict({"event_id": event_id, "occurred_on": occurred_on, "points": points}, **extra)


class ScoreDayTest(unittest.TestCase):
    def test_points_are_cap_times_level_over_four(self):
        day = xp.score_day("2026-01-05", levels(rhythm=4, learning=2, action=1), 0)
        self.assertEqual(day.dimensions["rhythm"], {"level": 4, "points": 18.0})
        self.assertEqual(day.dimensions["learning"]["points"], 8.0)
        self.assertEqual(day.dimensions["action"]["points"], 3.5)
        self.assertEqual(day.dimensions["connection"]["points"], 0.0)
        self.assertEqual(day.total, 30)  # 18 + 8 + 3.5 = 29.5 -> half up
        self.assertIsInstance(day.total, int)

    def test_max_dimensions_sum_to_ninety(self):
        day = xp.score_day("2026-01-05", {k: 4 for k in ZERO}, 0)
        self.assertEqual(day.total, 90)

    def test_both_presets_score(self):
        for name in presets.NAMES:
            day = xp.score_day("2026-01-05", levels(rhythm=2), 3, preset=presets.load(name))
            self.assertEqual(day.total, 12)

    def test_level_validation(self):
        for bad in (5, -1, 2.5, True, "2", None):
            with self.assertRaises(xp.XPError) as ctx:
                xp.score_day("2026-01-05", levels(rhythm=bad), 0)
            self.assertTrue(any(e.startswith("levels.rhythm:") for e in ctx.exception.errors))

    def test_missing_and_unknown_dimensions_rejected(self):
        partial = dict(ZERO)
        del partial["connection"]
        with self.assertRaises(xp.XPError) as ctx:
            xp.score_day("2026-01-05", dict(partial, mood=2), 0)
        self.assertIn("levels.mood: unknown dimension", ctx.exception.errors)
        self.assertTrue(any(e.startswith("levels.connection: required") for e in ctx.exception.errors))

    def test_ordinary_is_clamped(self):
        self.assertEqual(xp.score_day("2026-01-05", ZERO, 14).ordinary, 10)
        self.assertEqual(xp.score_day("2026-01-05", ZERO, -3).ordinary, 0)
        self.assertEqual(xp.score_day("2026-01-05", ZERO, 6.5).total, 7)
        with self.assertRaises(xp.XPError):
            xp.score_day("2026-01-05", ZERO, "5")

    def test_events_are_uncapped_and_no_daily_cap(self):
        day = xp.score_day("2026-01-05", {k: 4 for k in ZERO}, 10,
                           [event("E-first-talk", "2026-01-05", 120, title="first talk")])
        self.assertEqual(day.total, 220)

    def test_event_requires_id_and_occurred_on(self):
        with self.assertRaises(xp.XPError) as ctx:
            xp.score_day("2026-01-05", ZERO, 0, [{"points": 5}])
        errors = ctx.exception.errors
        self.assertIn("events[0].event_id: must be a non-empty string", errors)
        self.assertIn("events[0].occurred_on: required", errors)

    def test_event_ids_unique_within_day(self):
        with self.assertRaises(xp.XPError) as ctx:
            xp.score_day("2026-01-05", ZERO, 0,
                         [event("E-1", "2026-01-05", 5), event("E-1", "2026-01-05", 6)])
        self.assertIn("events[1].event_id: duplicate E-1", ctx.exception.errors)

    def test_event_booked_on_the_day_it_happened(self):
        with self.assertRaises(xp.XPError) as ctx:
            xp.score_day("2026-01-06", ZERO, 0, [event("E-1", "2026-01-05", 5)])
        self.assertTrue(ctx.exception.errors[0].startswith("events[0].occurred_on:"))

    def test_bad_event_points_and_fields(self):
        with self.assertRaises(xp.XPError) as ctx:
            xp.score_day("2026-01-05", ZERO, 0, [event("E-1", "2026-01-05", -1, mood="x")])
        self.assertIn("events[0].mood: unknown field", ctx.exception.errors)
        self.assertIn("events[0].points: must be a non-negative number", ctx.exception.errors)


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def test_record_and_revisions(self):
        first = xp.record(self.dir, xp.score_day("2026-01-05", levels(rhythm=2), 1), now=NOW)
        self.assertEqual(first["id"], "XP-20260105-r1")
        self.assertEqual(first["total"], 10)
        same = xp.record(self.dir, xp.score_day("2026-01-05", levels(rhythm=2), 1), now=NOW)
        self.assertEqual(same["id"], first["id"])
        second = xp.record(self.dir, xp.score_day("2026-01-05", levels(rhythm=3), 1), now=NOW)
        self.assertEqual((second["revision"], second["supersedes"]), (2, first["id"]))
        self.assertEqual(xp.latest(self.dir, "2026-01-05")["id"], second["id"])
        self.assertEqual(len(Store(self.dir).read_all("xp")), 2)

    def test_event_already_booked_on_another_day_is_rejected(self):
        xp.record(self.dir, xp.score_day("2026-01-05", ZERO, 0,
                                         [event("E-move", "2026-01-05", 40)]), now=NOW)
        later = xp.score_day("2026-01-06", ZERO, 0, [event("E-move", "2026-01-06", 40)])
        with self.assertRaises(xp.XPError) as ctx:
            xp.record(self.dir, later, now=NOW)
        self.assertEqual(ctx.exception.errors, ["events.E-move: already booked on 2026-01-05"])
        self.assertIsNone(xp.latest(self.dir, "2026-01-06"))
        self.assertEqual(xp.booked_events(self.dir), {"E-move": "2026-01-05"})

    def test_same_day_revision_may_keep_its_event(self):
        xp.record(self.dir, xp.score_day("2026-01-05", ZERO, 0,
                                         [event("E-move", "2026-01-05", 40)]), now=NOW)
        rec = xp.record(self.dir, xp.score_day("2026-01-05", ZERO, 2,
                                               [event("E-move", "2026-01-05", 40)]), now=NOW)
        self.assertEqual(rec["revision"], 2)

    def test_record_requires_xpday_and_aware_now(self):
        with self.assertRaises(xp.XPError):
            xp.record(self.dir, {"date": "2026-01-05"}, now=NOW)
        with self.assertRaises(xp.XPError):
            xp.record(self.dir, xp.score_day("2026-01-05", ZERO, 0),
                      now=datetime.datetime(2026, 1, 5))


class WeekTotalTest(unittest.TestCase):
    def test_sums_displayed_totals_without_bonus(self):
        # 3.5 points each: displayed 4 per day, so the week shows 8, not round(7.0) = 7
        days = [xp.score_day(d, levels(action=1), 0) for d in ("2026-01-05", "2026-01-06")]
        week = xp.week_total(days, "2026-01-05")
        self.assertEqual(week.days, {"2026-01-05": 4, "2026-01-06": 4})
        self.assertEqual(week.total, 8)

    def test_missing_days_are_reported_not_zero(self):
        days = [xp.score_day("2026-01-07", levels(rhythm=4), 0)]
        week = xp.week_total(days, "2026-01-05")
        self.assertEqual(week.total, 18)
        self.assertNotIn("2026-01-05", week.days)
        self.assertEqual(week.missing, ["2026-01-05", "2026-01-06", "2026-01-08",
                                        "2026-01-09", "2026-01-10", "2026-01-11"])

    def test_zero_score_day_is_not_missing(self):
        week = xp.week_total([xp.score_day("2026-01-05", ZERO, 0)], "2026-01-05")
        self.assertEqual(week.days, {"2026-01-05": 0})
        self.assertNotIn("2026-01-05", week.missing)

    def test_reads_latest_revisions_from_store_and_ignores_other_weeks(self):
        with tempfile.TemporaryDirectory() as tmp:
            xp.record(tmp, xp.score_day("2026-01-05", levels(rhythm=1), 0), now=NOW)
            xp.record(tmp, xp.score_day("2026-01-05", levels(rhythm=2), 0), now=NOW)
            xp.record(tmp, xp.score_day("2026-01-12", levels(rhythm=4), 0), now=NOW)
            week = xp.week_total(tmp, "2026-01-05")
        self.assertEqual(week.days, {"2026-01-05": 9})
        self.assertEqual(week.total, 9)

    def test_week_must_start_on_monday(self):
        with self.assertRaises(xp.XPError):
            xp.week_total([], "2026-01-06")


if __name__ == "__main__":
    unittest.main()
