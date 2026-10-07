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
"""

import dataclasses
import decimal

from etrace import presets

GROUPS = presets.G_GROUPS
CODES = tuple(code for codes in GROUPS.values() for code in codes)
PROVENANCES = ("self_report", "model_inference")
CONFIDENCES = ("low", "medium", "high")
MIN_VALUE, MAX_VALUE = 0.0, 10.0

_FIELDS = ("code", "value", "provenance", "source", "confidence", "self_value", "reason")
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
