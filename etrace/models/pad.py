"""PAD points (pleasure / arousal / dominance) and the daily summary.

A PadPoint is one reading of the day: a date, a slot (one of SLOTS or an exact
'HH:MM' time), P/A/D floats 0-5, a provenance and its evidence:

    self_report      the user gave the values
    model_inference  the AI estimated them; source and confidence are required

self_value holds the user's own values ({'P', 'A', 'D'}) next to an AI
estimate. It is stored unchanged; when it differs from the AI values a reason
must say why the AI disagrees (principle 2: AI estimates never pose as self
report).

project(p, a, d) maps levels to the preset's 27-table word. daily_summary()
is the per-axis duration-weighted mean; points without duration_hours are
equal-weighted (each counts as the mean known duration, or 1 when none is
known) and the summary flags them. validate_day() requires morning, noon and
afternoon readings (exact times count for the slot they fall in).

record() keeps points in pad.jsonl ('PAD-YYYYMMDD-N'); a later point for the
same date and slot replaces the earlier one in points_for(), history stays.
"""

import dataclasses
import datetime
import re

from etrace import presets
from etrace.store import Store

AXES = ("P", "A", "D")
SLOTS = ("early_morning", "morning", "noon", "afternoon", "evening", "late_night")
REQUIRED_SLOTS = ("morning", "noon", "afternoon")
PROVENANCES = ("self_report", "model_inference")
CONFIDENCES = ("low", "medium", "high")
MIN_VALUE, MAX_VALUE = 0.0, 5.0

# [start, end) hour of each slot; late_night wraps past midnight.
SLOT_RANGES = {
    "early_morning": (5, 8),
    "morning": (8, 11),
    "noon": (11, 14),
    "afternoon": (14, 18),
    "evening": (18, 22),
    "late_night": (22, 29),
}
# Fixed chart position (hours) for slot-only points; not an exact clock time.
SLOT_HOURS = {"early_morning": 6.5, "morning": 9.5, "noon": 12.5,
              "afternoon": 16.0, "evening": 20.0, "late_night": 23.5}

_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_FIELDS = ("date", "slot", "P", "A", "D", "provenance", "source", "confidence",
           "duration_hours", "self_value", "reason")


class PadError(ValueError):
    """Invalid PAD input. .errors lists 'field: message' strings."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_value(value, field, errors):
    if not _is_number(value):
        errors.append(f"{field}: must be a number")
    elif not MIN_VALUE <= value <= MAX_VALUE:
        errors.append(f"{field}: must be within 0-5, got {value!r}")


def slot_of(slot):
    """The named slot for SLOT or an exact 'HH:MM' time."""
    if slot in SLOTS:
        return slot
    m = _TIME.match(slot) if isinstance(slot, str) else None
    if not m:
        raise PadError([f"slot: must be one of {', '.join(SLOTS)} or HH:MM, got {slot!r}"])
    hour = int(m.group(1))
    if hour < 5:
        hour += 24
    for name, (start, end) in SLOT_RANGES.items():
        if start <= hour < end:
            return name
    raise AssertionError("slot ranges cover the whole day")  # pragma: no cover


@dataclasses.dataclass(frozen=True)
class PadPoint:
    date: str
    slot: str
    P: float
    A: float
    D: float
    provenance: str
    source: str = None
    confidence: str = None
    duration_hours: float = None
    self_value: dict = None
    reason: str = None

    def __post_init__(self):
        errors = []
        try:
            datetime.date.fromisoformat(self.date)
        except (TypeError, ValueError):
            errors.append(f"date: must be YYYY-MM-DD, got {self.date!r}")
        try:
            slot_of(self.slot)
        except PadError as exc:
            errors.extend(exc.errors)
        for axis in AXES:
            _check_value(getattr(self, axis), axis, errors)
        if self.provenance not in PROVENANCES:
            errors.append(f"provenance: must be one of {', '.join(PROVENANCES)}, got {self.provenance!r}")
        inferred = self.provenance == "model_inference"
        if self.source is not None and (not isinstance(self.source, str) or not self.source.strip()):
            errors.append("source: must be a non-empty string")
        elif inferred and self.source is None:
            errors.append("source: required for model_inference")
        if self.confidence is not None and self.confidence not in CONFIDENCES:
            errors.append(f"confidence: must be one of {', '.join(CONFIDENCES)}, got {self.confidence!r}")
        elif inferred and self.confidence is None:
            errors.append("confidence: required for model_inference")
        if self.duration_hours is not None and (
                not _is_number(self.duration_hours) or not 0 < self.duration_hours <= 24):
            errors.append("duration_hours: must be a number in (0, 24]")
        if self.reason is not None and (not isinstance(self.reason, str) or not self.reason.strip()):
            errors.append("reason: must be a non-empty string")
        if self.self_value is not None:
            if not isinstance(self.self_value, dict) or set(self.self_value) != set(AXES):
                errors.append("self_value: must be an object with exactly P, A, D")
            else:
                axis_errors = []
                for axis in AXES:
                    _check_value(self.self_value[axis], f"self_value.{axis}", axis_errors)
                errors.extend(axis_errors)
                differs = any(self.self_value[a] != getattr(self, a) for a in AXES)
                if not axis_errors and differs and self.reason is None:
                    errors.append("reason: required when the values differ from self_value")
        if errors:
            raise PadError(errors)

    @property
    def named_slot(self):
        return slot_of(self.slot)

    @property
    def exact_time(self):
        return self.slot not in SLOTS

    @property
    def values(self):
        return (self.P, self.A, self.D)

    @classmethod
    def from_dict(cls, data):
        """Build from agent JSON; unknown or missing fields raise PadError."""
        if not isinstance(data, dict):
            raise PadError(["point: must be an object"])
        errors = [f"{k}: unknown field" for k in data if k not in _FIELDS]
        errors += [f"{k}: required" for k in ("date", "slot", "P", "A", "D", "provenance") if k not in data]
        if errors:
            raise PadError(errors)
        point = cls(**data)
        if point.self_value is not None:  # keep the user's values exactly as given
            object.__setattr__(point, "self_value", dict(point.self_value))
        return point

    def to_dict(self):
        return {k: v for k, v in dataclasses.asdict(self).items() if v is not None}


def project(p, a, d, preset=None, language="zh"):
    """The 27-table word for P/A/D (0-5; values below 1 count as level 1)."""
    if preset is None:
        preset = presets.load("default")
    values = []
    for axis, value in zip(AXES, (p, a, d)):
        errors = []
        _check_value(value, axis, errors)
        if errors:
            raise PadError(errors)
        values.append(max(1.0, value))
    return presets.pad_word(preset, *values, language=language)


@dataclasses.dataclass(frozen=True)
class DailySummary:
    date: str
    P: float
    A: float
    D: float
    points: int
    unknown_durations: int

    @property
    def equal_weighted(self):
        """True when some points had no duration and were equal-weighted."""
        return self.unknown_durations > 0


def daily_summary(points):
    """Per-axis duration-weighted mean of one day's points (None if no points)."""
    points = list(points)
    if not points:
        return None
    dates = {pt.date for pt in points}
    if len(dates) != 1:
        raise PadError([f"points: must share one date, got {', '.join(sorted(dates))}"])
    known = [pt.duration_hours for pt in points if pt.duration_hours is not None]
    fill = sum(known) / len(known) if known else 1.0
    weights = [pt.duration_hours if pt.duration_hours is not None else fill for pt in points]
    total = sum(weights)
    means = [sum(w * pt.values[i] for w, pt in zip(weights, points)) / total for i in range(3)]
    return DailySummary(points[0].date, *means, points=len(points),
                        unknown_durations=len(points) - len(known))


@dataclasses.dataclass
class DayValidation:
    problems: list

    @property
    def ok(self):
        return not self.problems


def validate_day(points):
    """Fail unless morning, noon and afternoon are all present on one date."""
    points = list(points)
    problems = []
    dates = {pt.date for pt in points}
    if len(dates) > 1:
        problems.append(f"points span several dates: {', '.join(sorted(dates))}")
    present = {pt.named_slot for pt in points}
    for slot in REQUIRED_SLOTS:
        if slot not in present:
            problems.append(f"missing {slot} reading")
    return DayValidation(problems)


STORE_NAME = "pad"


def _store(data_dir):
    return data_dir if isinstance(data_dir, Store) else Store(data_dir)


def _sort_key(pt):
    if pt.exact_time:
        h, m = map(int, pt.slot.split(":"))
        hours = h + m / 60
        return hours + 24 if hours < 5 else hours  # before 05:00 belongs to the previous evening
    return SLOT_HOURS[pt.slot]


def _latest(records, date=None):
    latest = {}
    for rec in records:
        if date is None or rec["date"] == date:
            latest[(rec["date"], rec["point"]["slot"])] = rec
    return latest


def record(data_dir, points, now=None):
    """Append PadPoints to pad.jsonl; returns the new records (unchanged points are skipped)."""
    points = list(points)
    if not all(isinstance(pt, PadPoint) for pt in points):
        raise PadError(["points: must be PadPoint instances"])
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise PadError(["now: must be timezone-aware"])
    store = _store(data_dir)
    added = []
    with store.lock():
        records = store.read_all(STORE_NAME)
        for pt in points:
            body = pt.to_dict()
            prev = _latest(records).get((pt.date, pt.slot))
            if prev and prev["point"] == body:
                continue
            stamp = pt.date.replace("-", "")
            n = sum(1 for r in records if r["id"].startswith(f"PAD-{stamp}-")) + 1
            rec = {"id": f"PAD-{stamp}-{n}", "date": pt.date, "point": body,
                   "supersedes": prev["id"] if prev else None, "recorded_at": now.isoformat()}
            store.append_locked(STORE_NAME, rec)
            records.append(rec)
            added.append(rec)
    return added


def points_for(data_dir, date):
    """DATE's current points (latest per slot), in time order."""
    date = date.isoformat() if isinstance(date, datetime.date) else date
    recs = _latest(_store(data_dir).read_all(STORE_NAME), date).values()
    return sorted((PadPoint.from_dict(r["point"]) for r in recs), key=_sort_key)
