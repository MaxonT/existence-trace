"""Presets: the five JSON files that make the loop run on day one.

presets/NAME/ holds pad.json, g.json, xp.json, rules.json and
question_templates.json. load(name) reads and validates all five; any invalid
field raises ValueError naming the file and the field. Every user-facing label
is a {"zh": ..., "en": ...} object; use label(obj, language) to pick one.
"""

import json
import math
import os

NAMES = ("default", "tiger-method")
FILES = ("pad.json", "g.json", "xp.json", "rules.json", "question_templates.json")
LANGUAGES = ("zh", "en")
PRESETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preset_data")

PAD_AXES = ("P", "A", "D")
PAD_BANDS = ("low", "mid", "high")
G_GROUPS = {"B": ("B1", "B2", "B3", "B4"), "E": ("E1", "E2", "E3", "E4")}
G_ANCHORS = ("0", "2", "5", "8", "10")
G_ITEM_ANCHORS = ("0-2", "5", "8-10")
XP_LEVELS = ("0", "1", "2", "3", "4")


def _fail(field, message):
    raise ValueError(f"{field}: {message}")


def _obj(value, field):
    if not isinstance(value, dict):
        _fail(field, "must be an object")
    return value


def _label(value, field):
    _obj(value, field)
    for lang in LANGUAGES:
        text = value.get(lang)
        if not isinstance(text, str) or not text.strip():
            _fail(f"{field}.{lang}", "must be a non-empty string")
    return value


def _number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(field, "must be a number")
    return value


def label(obj, language):
    """Pick the zh or en text of a label object."""
    if language not in LANGUAGES:
        raise ValueError(f"language: expected one of {', '.join(LANGUAGES)}, got {language!r}")
    return obj[language]


# --- per-file validators ------------------------------------------------------

def validate_pad(pad, where="pad.json"):
    _obj(pad, where)
    if pad.get("scale") != {"min": 0, "max": 5}:
        _fail(f"{where}.scale", "must be min 0, max 5")
    axes = _obj(pad.get("axes"), f"{where}.axes")
    if sorted(axes) != sorted(PAD_AXES):
        _fail(f"{where}.axes", f"must be exactly {', '.join(PAD_AXES)}")
    for axis in PAD_AXES:
        field = f"{where}.axes.{axis}"
        _label(_obj(axes[axis], field).get("label"), f"{field}.label")
        levels = _obj(axes[axis].get("levels"), f"{field}.levels")
        if sorted(levels) != [str(i) for i in range(1, 6)]:
            _fail(f"{field}.levels", "must have exactly the five reference levels 1-5")
        for level, anchor in levels.items():
            _label(anchor, f"{field}.levels.{level}")
    projection = _obj(pad.get("projection"), f"{where}.projection")
    if projection != {"low": [0, 2], "mid": [3, 3], "high": [4, 5]}:
        _fail(f"{where}.projection", "must be low 0-2, mid 3, high 4-5")
    words = pad.get("words")
    if not isinstance(words, list) or len(words) != 27:
        _fail(f"{where}.words", "must have exactly 27 cells")
    seen = set()
    for i, cell in enumerate(words):
        field = f"{where}.words[{i}]"
        _label(cell, field)
        key = tuple(cell.get(axis) for axis in PAD_AXES)
        if any(band not in PAD_BANDS for band in key):
            _fail(field, f"P/A/D must each be one of {', '.join(PAD_BANDS)}")
        if key in seen:
            _fail(field, f"duplicate cell {key}")
        seen.add(key)
    return pad


def validate_g(g, where="g.json"):
    _obj(g, where)
    anchors = _obj(g.get("anchors"), f"{where}.anchors")
    if sorted(anchors) != sorted(G_ANCHORS):
        _fail(f"{where}.anchors", f"must be exactly {'/'.join(G_ANCHORS)}")
    for key, anchor in anchors.items():
        _label(anchor, f"{where}.anchors.{key}")
    _label(g.get("formula"), f"{where}.formula")
    _label(g.get("notice"), f"{where}.notice")
    groups = _obj(g.get("groups"), f"{where}.groups")
    if sorted(groups) != sorted(G_GROUPS):
        _fail(f"{where}.groups", "must be exactly B and E")
    for name, codes in G_GROUPS.items():
        field = f"{where}.groups.{name}"
        _label(_obj(groups[name], field).get("label"), f"{field}.label")
        items = _obj(groups[name].get("items"), f"{field}.items")
        if sorted(items) != list(codes):
            _fail(f"{field}.items", f"must be exactly {', '.join(codes)}")
        total = 0.0
        for code in codes:
            item = _obj(items[code], f"{field}.items.{code}")
            weight = _number(item.get("weight"), f"{field}.items.{code}.weight")
            if weight <= 0:
                _fail(f"{field}.items.{code}.weight", "must be positive")
            total += weight
            _label(item.get("label"), f"{field}.items.{code}.label")
            item_anchors = _obj(item.get("anchors"), f"{field}.items.{code}.anchors")
            if sorted(item_anchors) != sorted(G_ITEM_ANCHORS):
                _fail(f"{field}.items.{code}.anchors", f"must be exactly {' / '.join(G_ITEM_ANCHORS)}")
            for key in G_ITEM_ANCHORS:
                _label(item_anchors[key], f"{field}.items.{code}.anchors.{key}")
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            _fail(f"{field}.items", f"weights must sum to 1.0, got {total:g}")
    return g


def validate_xp(xp, where="xp.json"):
    _obj(xp, where)
    levels = _obj(xp.get("levels"), f"{where}.levels")
    if sorted(levels) != list(XP_LEVELS):
        _fail(f"{where}.levels", "must be exactly levels 0-4")
    for key, anchor in levels.items():
        _label(anchor, f"{where}.levels.{key}")
    dims = xp.get("dimensions")
    if not isinstance(dims, list) or len(dims) != 6:
        _fail(f"{where}.dimensions", "must have exactly six dimensions")
    ids = set()
    for i, dim in enumerate(dims):
        field = f"{where}.dimensions[{i}]"
        _obj(dim, field)
        if not isinstance(dim.get("id"), str) or not dim["id"] or dim["id"] in ids:
            _fail(f"{field}.id", "must be a unique non-empty string")
        ids.add(dim["id"])
        cap = _number(dim.get("cap"), f"{field}.cap")
        if cap <= 0:
            _fail(f"{field}.cap", "must be positive")
        _label(dim.get("label"), f"{field}.label")
    ordinary = _obj(xp.get("ordinary"), f"{where}.ordinary")
    if ordinary.get("min") != 0 or ordinary.get("max") != 10:
        _fail(f"{where}.ordinary", "must be min 0, max 10")
    events = _obj(xp.get("events"), f"{where}.events")
    if events.get("capped") is not False:
        _fail(f"{where}.events.capped", "events are uncapped (false)")
    if xp.get("booked_events") != []:
        _fail(f"{where}.booked_events", "presets must not ship any events")
    return xp


def validate_rules(rules, where="rules.json"):
    _obj(rules, where)
    items = rules.get("rules")
    if not isinstance(items, list) or not items:
        _fail(f"{where}.rules", "must be a non-empty list")
    ids = set()
    for i, rule in enumerate(items):
        field = f"{where}.rules[{i}]"
        _obj(rule, field)
        if not isinstance(rule.get("id"), str) or not rule["id"] or rule["id"] in ids:
            _fail(f"{field}.id", "must be a unique non-empty string")
        ids.add(rule["id"])
        if not isinstance(rule.get("scope"), str) or not rule["scope"].strip():
            _fail(f"{field}.scope", "must be a non-empty string")
        if "text" not in rule:
            _fail(f"{field}.text", "is required")
        _label(rule["text"], f"{field}.text")
    return rules


def validate_question_templates(qt, where="question_templates.json"):
    _obj(qt, where)
    items = qt.get("templates")
    if not isinstance(items, list) or not items:
        _fail(f"{where}.templates", "must be a non-empty list")
    ids = set()
    for i, tpl in enumerate(items):
        field = f"{where}.templates[{i}]"
        _obj(tpl, field)
        if not isinstance(tpl.get("id"), str) or not tpl["id"] or tpl["id"] in ids:
            _fail(f"{field}.id", "must be a unique non-empty string")
        ids.add(tpl["id"])
        dims = tpl.get("dimensions")
        if not isinstance(dims, list) or not dims or not all(isinstance(d, str) and d for d in dims):
            _fail(f"{field}.dimensions", "must be a non-empty list of strings")
        _label(tpl.get("label"), f"{field}.label")
        _label(tpl.get("prompt"), f"{field}.prompt")
    return qt


_VALIDATORS = {
    "pad.json": validate_pad,
    "g.json": validate_g,
    "xp.json": validate_xp,
    "rules.json": validate_rules,
    "question_templates.json": validate_question_templates,
}


# --- loading ------------------------------------------------------------------

def preset_dir(name, presets_dir=None):
    if presets_dir is None:
        if name not in NAMES:
            raise ValueError(f"preset: unknown preset {name!r}; expected one of {', '.join(NAMES)}")
        presets_dir = PRESETS_DIR
    return os.path.join(presets_dir, name)


def load(name, presets_dir=None):
    """Read and validate all five files of preset NAME.

    Returns {'name', 'pad', 'g', 'xp', 'rules', 'question_templates'}.
    presets_dir overrides the bundled presets/ directory (tests, custom presets).
    """
    base = preset_dir(name, presets_dir)
    result = {"name": name}
    for filename in FILES:
        path = os.path.join(base, filename)
        if not os.path.exists(path):
            _fail(f"{name}/{filename}", "file is missing")
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as exc:
            _fail(f"{name}/{filename}", f"invalid JSON ({exc})")
        result[filename[:-len(".json")]] = _VALIDATORS[filename](data, f"{name}/{filename}")
    return result


# --- lookups ------------------------------------------------------------------

def g_items(preset):
    """[(code, weight, label_obj)] in B1..B4, E1..E4 order."""
    out = []
    for group, codes in G_GROUPS.items():
        items = preset["g"]["groups"][group]["items"]
        out += [(code, items[code]["weight"], items[code]["label"]) for code in codes]
    return out


def band(level):
    """Project a continuous 0-5 PAD value onto low / mid / high (decimals round to the nearest level)."""
    _number(level, "level")
    if not 0 <= level <= 5:
        raise ValueError(f"level: must be within 0-5, got {level!r}")
    rounded = math.floor(level + 0.5)
    return "low" if rounded <= 2 else "mid" if rounded == 3 else "high"


def pad_word(preset, p, a, d, language="zh"):
    """The 27-table word for P/A/D values 0-5 (a coarse projection only)."""
    key = (band(p), band(a), band(d))
    for cell in preset["pad"]["words"]:
        if (cell["P"], cell["A"], cell["D"]) == key:
            return label(cell, language)
    raise ValueError(f"pad.words: no cell for {key}")  # unreachable for a validated preset
