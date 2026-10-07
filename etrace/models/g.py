"""G score: B (security), E (hope) and G = (B + E) / 2.

A GItem is one item score (B1..B4, E1..E4) on the preset's 0-10 scale with its
evidence: provenance (self_report | model_inference), source and confidence
are always required. self_value holds the user's own rating next to an AI
estimate; it is stored unchanged and, when it differs, a reason must say why
the AI disagrees (principle 2: AI estimates never pose as self report).

compute(items, preset) weighs the items with the preset's g.json weights.
Nothing is rounded during computation; display() rounds to one decimal for
people. A group is computed only when all four of its items exist and G only
when both groups exist; anything missing is None, never 0.

Continuity gap fill (principle 3): gap_fill(date, history) fills only the
missing items, per item in a fixed order: historical_bridge (linear
interpolation between the nearest real values before and after), else
continuity_carry_forward (nearest real value before), else scale_prior (5.0).
Filled items have provenance gap_fill, confidence low, the method and the
neighbor dates/values; such a day has explicit_gap_fill and is never a
training label (training_eligible). record()/refill() keep day revisions in
g.jsonl: a fill that changes (e.g. a later real value turns carry-forward into
a bridge) is appended as a new revision and old revisions stay.
"""

import dataclasses
import datetime
import decimal

from etrace import presets
from etrace.store import Store

GROUPS = presets.G_GROUPS
CODES = tuple(code for codes in GROUPS.values() for code in codes)
PROVENANCES = ("self_report", "model_inference", "gap_fill")
GAP_METHODS = ("historical_bridge", "continuity_carry_forward", "scale_prior")
SCALE_PRIOR = 5.0
CONFIDENCES = ("low", "medium", "high")
MIN_VALUE, MAX_VALUE = 0.0, 10.0

_FIELDS = ("code", "value", "provenance", "source", "confidence", "self_value", "reason",
           "method", "neighbors")
_MISSING = {"zh": "缺", "en": "n/a"}


class GError(ValueError):
    """Invalid G input. .errors lists 'field: message' strings."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_value(value, field, errors):
    if not _is_number(value):
        errors.append(f"{field}: must be a number")
    elif not MIN_VALUE <= value <= MAX_VALUE:
        errors.append(f"{field}: must be within 0-10, got {value!r}")


def _check_text(value, field, errors):
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field}: must be a non-empty string")


@dataclasses.dataclass(frozen=True)
class GItem:
    code: str
    value: float
    provenance: str
    source: str
    confidence: str
    self_value: float = None
    reason: str = None
    method: str = None      # gap_fill only: one of GAP_METHODS
    neighbors: list = None  # gap_fill only: [{'date', 'value'}] the fill was derived from

    def __post_init__(self):
        errors = []
        if self.code not in CODES:
            errors.append(f"code: must be one of {', '.join(CODES)}, got {self.code!r}")
        _check_value(self.value, "value", errors)
        if self.provenance not in PROVENANCES:
            errors.append(f"provenance: must be one of {', '.join(PROVENANCES)}, got {self.provenance!r}")
        _check_text(self.source, "source", errors)
        if self.confidence not in CONFIDENCES:
            errors.append(f"confidence: must be one of {', '.join(CONFIDENCES)}, got {self.confidence!r}")
        if self.reason is not None:
            _check_text(self.reason, "reason", errors)
        if self.self_value is not None:
            value_errors = []
            _check_value(self.self_value, "self_value", value_errors)
            errors.extend(value_errors)
            if not value_errors and self.self_value != self.value and self.reason is None:
                errors.append("reason: required when value differs from self_value")
        if self.provenance == "gap_fill":
            if self.confidence != "low":
                errors.append("confidence: gap_fill items must have confidence low")
            if self.method not in GAP_METHODS:
                errors.append(f"method: must be one of {', '.join(GAP_METHODS)}, got {self.method!r}")
            if not isinstance(self.neighbors, list) or not all(
                    isinstance(n, dict) and set(n) == {"date", "value"} for n in self.neighbors):
                errors.append("neighbors: must be a list of {'date', 'value'} objects")
        else:
            for field in ("method", "neighbors"):
                if getattr(self, field) is not None:
                    errors.append(f"{field}: only allowed for gap_fill items")
        if errors:
            raise GError(errors)

    @property
    def group(self):
        return self.code[0]

    @classmethod
    def from_dict(cls, data):
        """Build from agent JSON; unknown or missing fields raise GError."""
        if not isinstance(data, dict):
            raise GError(["item: must be an object"])
        errors = [f"{k}: unknown field" for k in data if k not in _FIELDS]
        errors += [f"{k}: required" for k in ("code", "value", "provenance", "source", "confidence")
                   if k not in data]
        if errors:
            raise GError(errors)
        return cls(**data)

    def to_dict(self):
        return {k: v for k, v in dataclasses.asdict(self).items() if v is not None}


@dataclasses.dataclass(frozen=True)
class GResult:
    B: float
    E: float
    G: float
    items: dict  # code -> GItem, only the items given

    @property
    def missing(self):
        return [code for code in CODES if code not in self.items]

    def to_dict(self):
        return {"B": self.B, "E": self.E, "G": self.G,
                "items": [self.items[c].to_dict() for c in CODES if c in self.items]}


def _items(items):
    out, errors = {}, []
    for i, item in enumerate(items):
        if isinstance(item, dict):
            try:
                item = GItem.from_dict(item)
            except GError as exc:
                errors += [f"items[{i}].{e}" for e in exc.errors]
                continue
        elif not isinstance(item, GItem):
            errors.append(f"items[{i}]: must be a GItem or an object")
            continue
        if item.code in out:
            errors.append(f"items[{i}].code: duplicate {item.code}")
        out[item.code] = item
    if errors:
        raise GError(errors)
    return out


def compute(items, preset=None):
    """Weighted B and E and G = (B + E) / 2, unrounded; missing parts are None."""
    if preset is None:
        preset = presets.load("default")
    by_code = _items(items)
    groups = {}
    for group, codes in GROUPS.items():
        weights = preset["g"]["groups"][group]["items"]
        if all(code in by_code for code in codes):
            groups[group] = sum(weights[code]["weight"] * by_code[code].value for code in codes)
        else:
            groups[group] = None
    b, e = groups["B"], groups["E"]
    g = (b + e) / 2 if b is not None and e is not None else None
    return GResult(b, e, g, by_code)


def display(value, language="zh"):
    """VALUE rounded half up to one decimal ('6.3'); None shows as missing."""
    if value is None:
        return _MISSING.get(language, _MISSING["en"])
    rounded = decimal.Decimal(repr(float(value))).quantize(
        decimal.Decimal("0.1"), rounding=decimal.ROUND_HALF_UP)
    return str(rounded)


def legend(preset=None, language="zh"):
    """Symbol explanation: formulas, item names and weights, anchors, notice."""
    if preset is None:
        preset = presets.load("default")
    g = preset["g"]
    lab = lambda obj: presets.label(obj, language)
    zh = language == "zh"
    lines = [("公式：" if zh else "Formula: ") + lab(g["formula"])]
    for group, codes in GROUPS.items():
        spec = g["groups"][group]
        lines.append(lab(spec["label"]) + ("：" if zh else ":"))
        for code in codes:
            item = spec["items"][code]
            lines.append(f"- {code} {lab(item['label'])} ({item['weight']:g})")
    scale = g.get("scale", {"min": 0, "max": 10})
    lines.append((f"刻度 {scale['min']}–{scale['max']}，锚点：" if zh
                  else f"Scale {scale['min']}-{scale['max']}, anchors: ")
                 + ("；" if zh else "; ").join(
                     f"{key} {lab(g['anchors'][key])}" for key in sorted(g["anchors"], key=int)))
    lines.append(("缺项不计 0，显示为“缺”。" if zh
                  else "Missing parts are never counted as 0; they show as n/a."))
    lines.append(lab(g["notice"]))
    return "\n".join(lines)


# --- continuity gap fill -------------------------------------------------

STORE_NAME = "g"


@dataclasses.dataclass(frozen=True)
class GDay:
    date: str
    items: dict  # code -> GItem, real and filled
    explicit_gap_fill: bool

    @property
    def filled(self):
        return [code for code in CODES if self.items[code].provenance == "gap_fill"]

    def item_dicts(self):
        return [self.items[c].to_dict() for c in CODES if c in self.items]


def _date(value):
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise GError([f"date: must be YYYY-MM-DD, got {value!r}"]) from None


def _real_values(history, target):
    """{code: [(date, value)]} of real (not gap_fill) values on days other than TARGET."""
    out = {code: [] for code in CODES}
    for day, items in history.items():
        day = _date(day)
        if day == target:
            continue
        for item in _items(items).values():
            if item.provenance != "gap_fill":
                out[item.code].append((day, item.value))
    return out


def _fill_item(code, target, known):
    before = [(d, v) for d, v in known if d < target]
    after = [(d, v) for d, v in known if d > target]
    left = max(before, key=lambda dv: dv[0]) if before else None
    right = min(after, key=lambda dv: dv[0]) if after else None
    if left and right:
        frac = (target - left[0]).days / (right[0] - left[0]).days
        value = left[1] + (right[1] - left[1]) * frac
        method, neighbors = "historical_bridge", [left, right]
    elif left:
        value, method, neighbors = left[1], "continuity_carry_forward", [left]
    else:
        value, method, neighbors = SCALE_PRIOR, "scale_prior", []
    return GItem(code=code, value=value, provenance="gap_fill", source=f"gap_fill:{method}",
                 confidence="low", method=method,
                 neighbors=[{"date": d.isoformat(), "value": v} for d, v in neighbors])


def gap_fill(date, items, history):
    """GDay for DATE: real ITEMS kept as given, missing codes filled from HISTORY.

    HISTORY maps date -> items for other days; only their real values count.
    """
    target = _date(date)
    real = _items(items)
    bad = [code for code, item in real.items() if item.provenance == "gap_fill"]
    if bad:
        raise GError([f"items.{code}: gap_fill items are made by the engine, not given" for code in bad])
    known = _real_values(history, target)
    out = {}
    for code in CODES:
        out[code] = real[code] if code in real else _fill_item(code, target, known[code])
    return GDay(target.isoformat(), out, explicit_gap_fill=len(real) < len(CODES))


def training_eligible(day):
    """False for any day with gap-filled items (GDay or a g.jsonl record)."""
    if isinstance(day, GDay):
        return not day.explicit_gap_fill and not day.filled
    if day.get("explicit_gap_fill"):
        return False
    return not any(i.get("provenance") == "gap_fill" for i in day.get("items", []))


def _store(data_dir):
    return data_dir if isinstance(data_dir, Store) else Store(data_dir)


def _now(now):
    if now is None:
        return datetime.datetime.now(datetime.timezone.utc)
    if not isinstance(now, datetime.datetime) or now.utcoffset() is None:
        raise GError(["now: must be a timezone-aware datetime"])
    return now


def _latest_by_date(records):
    latest = {}
    for rec in records:
        if rec["date"] not in latest or rec["revision"] > latest[rec["date"]]["revision"]:
            latest[rec["date"]] = rec
    return latest


def _real_items(record):
    return [i for i in record["items"] if i.get("provenance") != "gap_fill"]


def _append_revision(store, records, day, now):
    """Append DAY as a new revision unless its items equal the latest one. Caller holds the lock."""
    prev = _latest_by_date(records).get(day.date)
    if prev is not None and prev["items"] == day.item_dicts():
        return None
    revision = prev["revision"] + 1 if prev else 1
    record = {"id": f"G-{day.date.replace('-', '')}-r{revision}", "date": day.date,
              "revision": revision, "items": day.item_dicts(),
              "explicit_gap_fill": day.explicit_gap_fill,
              "filled": day.filled, "created_at": now.isoformat(),
              "supersedes": prev["id"] if prev else None}
    store.append_locked(STORE_NAME, record)
    records.append(record)
    return record


def _history(records):
    return {d: _real_items(r) for d, r in _latest_by_date(records).items()}


def record(data_dir, date, items, now=None):
    """Store DATE's real ITEMS gap-filled as a new revision, then refill other days.

    Returns the list of appended records (empty if nothing changed).
    """
    store, now = _store(data_dir), _now(now)
    with store.lock():
        records = store.read_all(STORE_NAME)
        day = gap_fill(date, items, _history(records))
        added = [r for r in [_append_revision(store, records, day, now)] if r]
        added += _refill_locked(store, records, now)
    return added


def _refill_locked(store, records, now):
    added = []
    for date, rec in sorted(_latest_by_date(records).items()):
        if not rec.get("filled"):
            continue
        day = gap_fill(date, _real_items(rec), _history(records))
        new = _append_revision(store, records, day, now)
        if new:
            added.append(new)
    return added


def refill(data_dir, now=None):
    """Re-run gap fill on every stored day with filled items; changed fills get new revisions."""
    store, now = _store(data_dir), _now(now)
    with store.lock():
        return _refill_locked(store, store.read_all(STORE_NAME), now)


def latest(data_dir, date):
    """The newest revision record for DATE, or None."""
    return _latest_by_date(_store(data_dir).read_all(STORE_NAME)).get(_date(date).isoformat())


def revisions(data_dir, date):
    """Every revision record for DATE, oldest first."""
    date = _date(date).isoformat()
    recs = [r for r in _store(data_dir).read_all(STORE_NAME) if r["date"] == date]
    return sorted(recs, key=lambda r: r["revision"])
