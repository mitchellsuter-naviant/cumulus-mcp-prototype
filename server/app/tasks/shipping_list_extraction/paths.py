"""The fact-path vocabulary of a shipping list, written down once.

`accounts_payable/paths.py`'s argument, on a different document: a field is
named by its **canonical fact path**, and that one string is the join between
five things — the value the extraction reported, the box `locate-fields` drew
on the page, the verdict beside it, the correction a supervisor sends back, and
the assertion a golden set makes. With one vocabulary there is no lookup table
between them and no way for them to disagree about which field they are talking
about.

The paths are the governed extraction prompt's own key names, dotted, with
array indexes copied exactly — `c_steel_fab.total_linear_feet`,
`a1_trusses.rows[0].span_ft` — because the prompt asks the model for exactly
that spelling in `field_confidence`. Renaming a key on that page does not
rename it here; it makes that value arrive empty and the department it fed look
absent, which is what the prompt's own "editing this safely" section warns
about.

**One family of paths is not on the page at all**, and it is marked: a
computed figure lives under `hours.<department>` (see `HOURS`). An out-of-band
verdict is raised there rather than on a grid field, because the number a
supervisor is being asked about was produced by a governed formula and is not
printed anywhere — and because a locate call must never be handed a path there
is no ink for.
"""

from __future__ import annotations

import re

#: `rows[2]` — a name and an index, in the prompt's own spelling.
_INDEXED = re.compile(r"^(?P<name>[^\[\]]+)\[(?P<index>\d+)\]$")

#: The prefix for a figure a governed formula computed. Never sent to the
#: locator, never expected in `field_confidence`.
HOURS = "hours"

#: The seven page families, in the order the document prints them, with what to
#: call each one on a review screen. Keyed by the extraction's own attribute so
#: the page label and the field path cannot drift.
FAMILY_LABELS: dict[str, str] = {
    "a1_trusses": "A1 · truss drawings",
    "b_components": "B · components",
    "b_laminates": "B · laminates",
    "b_verticals": "B · verticals",
    "b_truss_grid": "B · truss grid",
    "b_notes": "B · handwritten notes",
    "c_steel_fab": "C · steel fab",
    "d_hardware": "D · hardware",
    "e_trim": "E · trim",
    "f_slide_door": "F · slide door",
    "ll_loader_list": "LL · loader list",
}

#: Which page family each governed page label names. The governed page-family
#: table keys on the printed label (`A1`, `B`, `C`, …); the extraction keys on
#: the block it filled in. One page prints several blocks, which is exactly why
#: "absent" is judged per family and why this is a one-to-many map.
FAMILIES_BY_PAGE: dict[str, tuple[str, ...]] = {
    "A1": ("a1_trusses",),
    "B": ("b_components", "b_laminates", "b_verticals", "b_truss_grid", "b_notes"),
    "C": ("c_steel_fab",),
    "D": ("d_hardware",),
    "E": ("e_trim",),
    "F": ("f_slide_door",),
    "LL": ("ll_loader_list",),
}

PAGE_BY_FAMILY: dict[str, str] = {
    family: page
    for page, families in FAMILIES_BY_PAGE.items()
    for family in families
}

#: The fields a governed formula **multiplies**, per family — the reading of
#: the page's `- required field rule: a field a governed formula reads is
#: required when its page family is present`.
#:
#: Read as "multiplies" rather than "reads", and the difference is worth being
#: exact about. A formula also reads optional counters — ridge vents, house
#: wrap rolls, concrete bags — where *absent* and *none present* are the same
#: fact and a required-field verdict would fire on every ordinary ticket. A
#: total, a span, a weight or a length is different: absent there is a grid
#: nobody read, and a zero would silently remove a department's work from the
#: plant's day. Those are the paths below, and the check names this rule when
#: it raises one.
REQUIRED: dict[str, tuple[str, ...]] = {
    "a1_trusses": (
        "rows[].quantity", "rows[].span_ft", "rows[].ply",
        "rows[].top_chord_members", "rows[].bottom_chord_members",
        "rows[].web_members",
    ),
    "b_components": ("total_quantity",),
    "b_laminates": (
        "rows[].quantity", "rows[].length_ft", "rows[].ply",
        "rows[].lumber_size",
    ),
    "b_verticals": (
        "blocks_quantity", "ripped_plywood_sheets", "ridge_cleat_quantity",
    ),
    "c_steel_fab": ("total_linear_feet",),
    "e_trim": ("total_weight_lb",),
    "f_slide_door": ("track_end_caps_quantity",),
    "ll_loader_list": ("board_hours", "largest_truss_width_ft"),
}

#: Structural flags the extraction carries and a supervisor is never asked to
#: confirm as a *value*. `present` is a question about the document, answered
#: by the page-family check rather than by a field verdict.
_STRUCTURAL: frozenset[str] = frozenset({"present"})


def hours_path(department: str) -> str:
    """`hours.steel-fab` — where a computed figure's verdict is raised."""
    return f"{HOURS}.{department}"


def is_hours(path: str) -> bool:
    return (path or "").startswith(f"{HOURS}.")


def value_at(data: dict, path: str):
    """The value at a dotted path, or `None` where the path does not resolve.

    Total rather than raising, because a path can arrive from three places
    that do not agree about what the document contains: a verdict raised on an
    earlier reading, a correction a supervisor typed, and a box a model
    returned.
    """
    cursor: object = data
    for part in (path or "").split("."):
        match = _INDEXED.match(part)
        key = match.group("name") if match else part
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(key)
        if match is not None:
            index = int(match.group("index"))
            if not isinstance(cursor, list) or index >= len(cursor):
                return None
            cursor = cursor[index]
    return cursor


def set_at(data: dict, path: str, value) -> bool:
    """Write a value at a dotted path. True when the path resolved.

    False rather than creating the path, and that is the point: a correction
    keyed on a field the extraction does not have is a correction about a
    different document, and inventing a row for it would put a value in the
    schedule that no page carries.
    """
    parts = (path or "").split(".")
    cursor: object = data
    for part in parts[:-1]:
        match = _INDEXED.match(part)
        key = match.group("name") if match else part
        if not isinstance(cursor, dict):
            return False
        cursor = cursor.get(key)
        if match is not None:
            index = int(match.group("index"))
            if not isinstance(cursor, list) or index >= len(cursor):
                return False
            cursor = cursor[index]
    final = parts[-1]
    match = _INDEXED.match(final)
    if match is not None:
        key, index = match.group("name"), int(match.group("index"))
        if not isinstance(cursor, dict):
            return False
        holder = cursor.get(key)
        if not isinstance(holder, list) or index >= len(holder):
            return False
        holder[index] = value
        return True
    if not isinstance(cursor, dict) or final not in cursor:
        return False
    cursor[final] = value
    return True


def paths_of(data: dict) -> list[str]:
    """Every field path this reading actually carries, in document order.

    Only the families the extraction reported as **present**: a path inside an
    absent family is not a field a supervisor read badly, it is a page the
    document does not have — and the honest response to that is to skip a
    department, which the page-family check does.
    """
    found: list[str] = []
    for family in FAMILY_LABELS:
        page = data.get(family)
        if not isinstance(page, dict):
            continue
        if "present" in page and not page.get("present"):
            continue
        for key, value in page.items():
            if key in _STRUCTURAL:
                continue
            found.extend(_leaf_paths(f"{family}.{key}", value))
    return found


def _leaf_paths(prefix: str, value) -> list[str]:
    if isinstance(value, list):
        out: list[str] = []
        for index, item in enumerate(value):
            out.extend(_leaf_paths(f"{prefix}[{index}]", item))
        return out
    if isinstance(value, dict):
        out = []
        for key, item in value.items():
            if key in _STRUCTURAL:
                continue
            out.extend(_leaf_paths(f"{prefix}.{key}", item))
        return out
    return [prefix]


def family_of(path: str) -> str:
    """Which page family a path belongs to — `""` for a computed figure."""
    head = (path or "").split(".")[0]
    return head if head in FAMILY_LABELS else ""


def expand(family: str, template: str, data: dict) -> list[str]:
    """`rows[].span_ft` against a real reading, as one path per row.

    The templates in `REQUIRED` carry a bare `[]` because the number of rows is
    a fact about the document rather than about the rule.
    """
    if "[]" not in template:
        return [f"{family}.{template}"]
    head, _, tail = template.partition("[]")
    rows = value_at(data, f"{family}.{head}")
    if not isinstance(rows, list):
        return []
    return [f"{family}.{head}[{index}]{tail}" for index in range(len(rows))]


def label_for(path: str) -> str:
    """Screen copy — "C · steel fab — total linear feet", "A1 · row 2 — span ft"."""
    if is_hours(path):
        department = path.split(".", maxsplit=1)[-1]
        return f"Computed hours — {department}"
    family = family_of(path)
    if not family:
        return _pretty(path)
    tail = path[len(family) + 1:] if len(path) > len(family) else ""
    return f"{FAMILY_LABELS[family]} — {_pretty(tail)}" if tail else FAMILY_LABELS[family]


def _pretty(text: str) -> str:
    parts: list[str] = []
    for part in (text or "").split("."):
        match = _INDEXED.match(part)
        if match is not None:
            # One-based, because a printed grid's second line is line 2 to
            # everybody who is not a programmer.
            parts.append(
                f"{match.group('name').rstrip('s')} {int(match.group('index')) + 1}"
            )
        else:
            parts.append(part)
    return " · ".join(part.replace("_", " ") for part in parts)


def render(value) -> str:
    """As read, rendered for display — a string, because a screen edits text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        return f"{int(value)}"
    return str(value)


def is_empty(value) -> bool:
    """Whether a required field is actually absent.

    `None` and `""` are absent. **Zero is not**, and that asymmetry is
    deliberate: a printed zero is a reading, and treating it as absence would
    put every genuinely empty grid line in front of a supervisor. What catches
    a misread zero is the expected-hours band, which is a question about the
    computed figure rather than about the field.
    """
    return value is None or (isinstance(value, str) and not value.strip())
