"""XP: daily growth points.

score_day(date, levels, ordinary, events, preset) scores one day with the
preset's xp.json:
- every dimension gets a level 0-4 and points = cap * level / 4 (all six are
  required; level 0 is an explicit "none", a missing level is an error);
- ordinary achievements are clamped to 0..10;
- events are uncapped, need a unique event_id and occurred_on (the scored
  date: principle 4, an event is booked once, on the day it happened) and
  non-negative points;
- there is no daily cap; the displayed total is the sum rounded half up to an
  integer.

record() keeps day revisions in xp.jsonl and rejects an event_id that is
already booked on another day. week_total() sums the displayed daily totals
of a Monday-Sunday week with no weekly bonus; days without a score are listed
as missing, never counted as 0.
"""

import dataclasses
import datetime
import decimal

from etrace import presets
from etrace.store import Store

STORE_NAME = "xp"
MAX_LEVEL = 4
_EVENT_FIELDS = ("event_id", "occurred_on", "points", "title")


class XPError(ValueError):
    """Invalid XP input. .errors lists 'field: message' strings."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _date(value, field="date"):
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise XPError([f"{field}: must be YYYY-MM-DD, got {value!r}"]) from None


def _round(value):
    return int(decimal.Decimal(repr(float(value))).quantize(
        decimal.Decimal("1"), rounding=decimal.ROUND_HALF_UP))


@dataclasses.dataclass(frozen=True)
class XPDay:
    date: str
    dimensions: dict  # id -> {'level', 'points'}
    ordinary: float   # clamped 0..10
    events: list      # [{'event_id', 'occurred_on', 'points', 'title'?}]

    @property
    def raw_total(self):
        return (sum(d["points"] for d in self.dimensions.values()) + self.ordinary
                + sum(e["points"] for e in self.events))

    @property
    def total(self):
        """Displayed integer total (half up); no daily cap."""
        return _round(self.raw_total)

    def to_dict(self):
        return {"date": self.date, "dimensions": self.dimensions, "ordinary": self.ordinary,
                "events": self.events, "total": self.total}


def _events(events, date, errors):
    out, seen = [], set()
    if not isinstance(events, list):
        errors.append("events: must be a list")
        return out
    for i, event in enumerate(events):
        field = f"events[{i}]"
        if not isinstance(event, dict):
            errors.append(f"{field}: must be an object")
            continue
        errors += [f"{field}.{k}: unknown field" for k in event if k not in _EVENT_FIELDS]
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            errors.append(f"{field}.event_id: must be a non-empty string")
        elif event_id in seen:
            errors.append(f"{field}.event_id: duplicate {event_id}")
        else:
            seen.add(event_id)
        if "occurred_on" not in event:
            errors.append(f"{field}.occurred_on: required")
        else:
            try:
                occurred = _date(event["occurred_on"], f"{field}.occurred_on")
                if occurred != date:
                    errors.append(f"{field}.occurred_on: events are booked on the day they "
                                  f"happened ({occurred.isoformat()}), not {date.isoformat()}")
            except XPError as exc:
                errors += exc.errors
        points = event.get("points")
        if not _is_number(points) or points < 0:
            errors.append(f"{field}.points: must be a non-negative number")
        if "title" in event and (not isinstance(event["title"], str) or not event["title"].strip()):
            errors.append(f"{field}.title: must be a non-empty string")
        out.append(dict(event, occurred_on=str(event.get("occurred_on"))))
    return out


def score_day(date, levels, ordinary, events=(), preset=None):
    """XPDay for DATE. LEVELS maps every preset dimension id to an integer 0-4."""
    if preset is None:
        preset = presets.load("default")
    day = _date(date)
    errors, dims = [], {}
    if not isinstance(levels, dict):
        raise XPError(["levels: must be an object of dimension id -> level"])
    known = {d["id"]: d for d in preset["xp"]["dimensions"]}
    errors += [f"levels.{k}: unknown dimension" for k in levels if k not in known]
    for dim_id, spec in known.items():
        level = levels.get(dim_id)
        if dim_id not in levels:
            errors.append(f"levels.{dim_id}: required (use 0 for none)")
        elif not isinstance(level, int) or isinstance(level, bool) or not 0 <= level <= MAX_LEVEL:
            errors.append(f"levels.{dim_id}: must be an integer 0-{MAX_LEVEL}, got {level!r}")
        else:
            dims[dim_id] = {"level": level, "points": spec["cap"] * level / MAX_LEVEL}
    bounds = preset["xp"]["ordinary"]
    if not _is_number(ordinary):
        errors.append("ordinary: must be a number")
    else:
        ordinary = min(max(ordinary, bounds["min"]), bounds["max"])
    booked = _events(list(events), day, errors)
    if errors:
        raise XPError(errors)
    return XPDay(day.isoformat(), dims, ordinary, booked)


# --- persistence -----------------------------------------------------------

def _store(data_dir):
    return data_dir if isinstance(data_dir, Store) else Store(data_dir)


def _now(now):
    if now is None:
        return datetime.datetime.now(datetime.timezone.utc)
    if not isinstance(now, datetime.datetime) or now.utcoffset() is None:
        raise XPError(["now: must be a timezone-aware datetime"])
    return now


def _latest_by_date(records):
    latest = {}
    for rec in records:
        if rec["date"] not in latest or rec["revision"] > latest[rec["date"]]["revision"]:
            latest[rec["date"]] = rec
    return latest


def booked_events(data_dir):
    """{event_id: date} from the latest revision of every stored day."""
    return {e["event_id"]: date
            for date, rec in _latest_by_date(_store(data_dir).read_all(STORE_NAME)).items()
            for e in rec["events"]}


def record(data_dir, day, now=None):
    """Append XPDay DAY to xp.jsonl as a new revision; returns the record.

    An unchanged day returns the existing latest record. An event_id booked on
    another day raises XPError.
    """
    if not isinstance(day, XPDay):
        raise XPError(["day: must be an XPDay from score_day"])
    store, now = _store(data_dir), _now(now)
    body = day.to_dict()
    with store.lock():
        records = store.read_all(STORE_NAME)
        latest = _latest_by_date(records)
        clashes = [f"events.{e['event_id']}: already booked on {date}"
                   for date, rec in latest.items() if date != day.date
                   for e in rec["events"] for mine in day.events
                   if e["event_id"] == mine["event_id"]]
        if clashes:
            raise XPError(clashes)
        prev = latest.get(day.date)
        if prev and {k: prev[k] for k in body} == body:
            return prev
        revision = prev["revision"] + 1 if prev else 1
        rec = dict(body, id=f"XP-{day.date.replace('-', '')}-r{revision}", revision=revision,
                   supersedes=prev["id"] if prev else None, created_at=now.isoformat())
        store.append_locked(STORE_NAME, rec)
    return rec


def latest(data_dir, date):
    """The newest xp.jsonl record for DATE, or None."""
    return _latest_by_date(_store(data_dir).read_all(STORE_NAME)).get(_date(date).isoformat())


@dataclasses.dataclass(frozen=True)
class WeekTotal:
    week_start: str
    total: int        # sum of the displayed daily totals of scored days
    days: dict        # date -> displayed daily total, scored days only
    missing: list     # dates without a score (never counted as 0)


def week_total(days, week_start):
    """Sum the displayed daily totals of the 7 days from WEEK_START (a Monday).

    DAYS is a data dir/Store (latest xp.jsonl revisions) or an iterable of
    XPDay / xp.jsonl records. No weekly bonus is added.
    """
    start = _date(week_start, "week_start")
    if start.weekday() != 0:
        raise XPError([f"week_start: must be a Monday, got {start.isoformat()}"])
    if isinstance(days, (str, Store)) or hasattr(days, "__fspath__"):
        days = _latest_by_date(_store(days).read_all(STORE_NAME)).values()
    totals = {}
    for day in days:
        if isinstance(day, XPDay):
            totals[day.date] = day.total
        else:
            totals[day["date"]] = day["total"]
    dates = [(start + datetime.timedelta(days=i)).isoformat() for i in range(7)]
    scored = {d: totals[d] for d in dates if d in totals}
    return WeekTotal(start.isoformat(), sum(scored.values()), scored,
                     [d for d in dates if d not in totals])
