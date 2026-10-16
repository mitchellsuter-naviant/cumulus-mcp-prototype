"""The ticket checked against itself, and the hours checked against their band.

Two pure Rules in the doctrine's sense: they evaluate what the flow already
gathered and fetch nothing. Everything arrives as arguments — the reading, the
governed thresholds, the governed labor parameters — so both are replayable and
gradeable without a world, and both name the **field path** they implicate,
because those paths become the boxes on the page and the focused inputs beside
them.

`check_grids` is `accounts_payable/v8/arithmetic.py`'s job on a different
document, and it asks four questions:

1. **Is a field a formula multiplies actually there?** Only on a page family
   the extraction says is present, and only for the fields in `paths.REQUIRED`
   — the governed page's own `required field rule`.
2. **Does a grid add up to its own printed total?** Components' rows against
   `total_quantity`, trim's rows against `total_weight_lb`, within the governed
   grid-line tolerance. A grid that does not agree with its own total was
   misread somewhere, and finding out where costs somebody four seconds.
3. **Does the colour grid agree with the printed linear footage?** Its own
   tolerance, because it is its own question: the steel rate multiplies that
   one number and nearly triples on a flag beside it.
4. **Does the page map agree with itself?** A `pages` flag that says a family
   is there over a block that says it is not, and a family the governed
   page-family table has never heard of. "Present but empty" and "absent" are
   different answers, and a false absence silently removes a department's work
   from the plant's day.

`check_labor_hours` is the SDD's fourth business exception, and it is a **field
verdict rather than a second gate**: every department whose grid is present has
its governed formula run, and a figure outside that department's governed
expected band arrives in the same review as the low-confidence fields, beside
the grid it was computed from.
"""

from __future__ import annotations

import math
from typing import Callable

from pydantic import BaseModel, Field

from ..department_scheduling import formulas
from ..department_scheduling.parameters import PlantParameters
from . import paths as vocab
from .verdicts import FieldFinding

#: Zero, without a literal — `formulas` holds the same convention and the same
#: reason: this module does arithmetic over governed numbers and should not be
#: able to contribute one of its own.
_ZERO: float = math.fsum([])


class DepartmentDemand(BaseModel):
    """One department's share of one job, before anything is written.

    Exactly the row `apply-job-demand` commits as a `JobTask`, computed here so
    that the check, the review screen and the write are looking at one object
    rather than three derivations of it.
    """

    department: str = ""
    labor_hours: float = _ZERO
    #: Which governed formula produced the hours, and at which revision — the
    #: sentence a schedule row carries so "why 27.4 hours" answers with a
    #: clause.
    hours_basis: str = ""
    #: What the department is being asked for, in the source's own units.
    demand_summary: str = ""
    #: Which page families fed it, in the governed table's own labels.
    source_page: str = ""
    confidence: float = _ZERO
    out_of_band: bool = False
    band_low: float | None = None
    band_high: float | None = None
    terms: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    #: `standard`, plus `nailing` and `girts` where the governed conditions
    #: hold. The row shaping itself belongs to the flow that fills a day; what
    #: is decided here is only whether the condition is true of this ticket.
    row_kinds: list[str] = Field(default_factory=list)


#: What each department's hours are made of, and which blocks have to be
#: present for it to have a row at all.
#:
#: The **departments** come from the governed page-family table; this says what
#: each department's own formula reads, which is a fact about the formula. A
#: department the governed table names and this does not is reported rather
#: than silently skipped, so adding a department to the page is visible.
#:
#: `any_of` and not `all_of`: Hardware & Trim Packaging is fed by three page
#: families and a ticket carrying a hardware grid on page D but no trim on
#: page E still has hardware work to do — the governed page says exactly that,
#: which is why absence is judged per page family.
_Composer = Callable[[dict, formulas.Parameters], list[formulas.HoursResult]]

FORMULAS: dict[str, tuple[tuple[str, ...], _Composer]] = {
    "truss-cut": (
        ("a1_trusses",),
        lambda grid, params: [
            formulas.truss_cut_hours(grid, params),
            # The laminates precut items are cut here and charged here — the
            # governed line is `laminates precut items scheduled in: truss-cut`.
            formulas.precut_cut_hours(grid, params),
        ],
    ),
    "truss-fab": (
        ("a1_trusses",),
        lambda grid, params: [
            formulas.truss_fab_hours(grid, params),
            formulas.nailing_hours(grid, params),
            formulas.girts_hours(grid, params),
        ],
    ),
    "steel-fab": (
        ("c_steel_fab",),
        lambda grid, params: [formulas.steel_fab_hours(grid, params)],
    ),
    "trim-fab": (
        ("e_trim",),
        lambda grid, params: [formulas.trim_pack_hours(grid, params)],
    ),
    "hardware-trim-pack": (
        ("d_hardware", "e_trim", "f_slide_door"),
        lambda grid, params: [
            formulas.hardware_pack_hours(grid, params),
            formulas.slide_door_hours(grid, params),
        ],
    ),
    "verticals": (
        ("b_verticals",),
        lambda grid, params: [formulas.verticals_hours(grid, params)],
    ),
    "components": (
        ("b_components",),
        lambda grid, params: [formulas.components_hours(grid, params)],
    ),
    "laminates": (
        ("b_laminates",),
        lambda grid, params: [formulas.laminates_hours(grid, params)],
    ),
    "stack-out": (
        ("ll_loader_list",),
        lambda grid, params: [formulas.stack_out_hours(grid, params)],
    ),
    "loading": (
        ("ll_loader_list",),
        lambda grid, params: [formulas.loading_hours(grid, params)],
    ),
}


# ---------------------------------------------------------------------------
# c1 — the ticket against itself
# ---------------------------------------------------------------------------


def check_grids(reading: dict, plant: PlantParameters) -> list[FieldFinding]:
    """The whole check. Pure: it reads its two arguments and nothing else."""
    findings: list[FieldFinding] = []
    findings.extend(_page_map(reading, plant))
    findings.extend(_required(reading))
    findings.extend(_totals(reading, plant))
    findings.extend(_document_confidence(reading, plant))
    return findings


def _page_map(reading: dict, plant: PlantParameters) -> list[FieldFinding]:
    """The `pages` flags against the blocks under them, and the governed map."""
    findings: list[FieldFinding] = []
    flags = reading.get("pages") or {}
    for page, families in vocab.FAMILIES_BY_PAGE.items():
        if not plant.departments_for(page):
            # The governed page-family table does not name this page. Reported
            # on the flag rather than dropped: a plant that stops printing a
            # page family should see that the extraction is still reading one.
            if flags.get(page):
                findings.append(FieldFinding(
                    path=f"pages.{page}",
                    status="inconsistent",
                    reason=(
                        f"the extraction reports page family `{page}` as "
                        "present, and the governed page-family table names no "
                        "departments for it"
                    ),
                ))
            continue
        declared = bool(flags.get(page))
        blocks = [
            family for family in families
            if isinstance(reading.get(family), dict)
            and reading[family].get("present")
        ]
        if declared and not blocks:
            findings.append(FieldFinding(
                path=f"pages.{page}",
                status="inconsistent",
                reason=(
                    f"page family `{page}` is reported present and every block "
                    "on it is empty — 'present but empty' and 'absent' are "
                    "different answers, and only one of them skips a "
                    f"department ({', '.join(plant.departments_for(page))})"
                ),
            ))
        if blocks and not declared:
            findings.append(FieldFinding(
                path=f"pages.{page}",
                status="inconsistent",
                reason=(
                    f"page family `{page}` is reported absent and "
                    f"{', '.join(blocks)} carries a grid — a false absence "
                    "silently removes a department's work from the plant's day"
                ),
            ))
    return findings


def _required(reading: dict) -> list[FieldFinding]:
    """A field a governed formula multiplies, on a page family that is present."""
    findings: list[FieldFinding] = []
    for family, templates in vocab.REQUIRED.items():
        page = reading.get(family)
        if not isinstance(page, dict) or not page.get("present"):
            continue
        for template in templates:
            for path in vocab.expand(family, template, reading):
                if vocab.is_empty(vocab.value_at(reading, path)):
                    findings.append(FieldFinding(
                        path=path, status="missing",
                        reason=(
                            f"{vocab.label_for(path)} is a value a governed "
                            "formula multiplies and its page family is "
                            "present, so the governed required-field rule "
                            "asks for it — and the extraction did not find one"
                        ),
                    ))
    return findings


def _totals(reading: dict, plant: PlantParameters) -> list[FieldFinding]:
    """A grid against its own printed total, and the colour grid against the footage."""
    findings: list[FieldFinding] = []
    grid_tolerance = plant.thresholds.grid_line_total_tolerance_pct
    footage_tolerance = plant.thresholds.linear_footage_tolerance_pct

    for family, rows_key, line_field, total_field, unit in (
        ("b_components", "rows", "quantity", "total_quantity", "item(s)"),
        ("e_trim", "rows", "weight_lb", "total_weight_lb", "lb"),
    ):
        page = reading.get(family)
        if not isinstance(page, dict) or not page.get("present"):
            continue
        rows = page.get(rows_key) or []
        printed = page.get(total_field)
        if not rows or vocab.is_empty(printed):
            continue
        lines = math.fsum([
            _number(row.get(line_field)) for row in rows if isinstance(row, dict)
        ])
        if _within(lines, _number(printed), grid_tolerance):
            continue
        findings.append(FieldFinding(
            path=f"{family}.{total_field}", status="inconsistent",
            reason=(
                f"the grid's {len(rows)} line(s) add up to {lines:g} {unit} and "
                f"the printed total reads {_number(printed):g} {unit}, outside "
                f"the governed tolerance of {grid_tolerance:g}% — a grid that "
                "does not agree with its own total was misread somewhere"
            ),
        ))

    steel = reading.get("c_steel_fab")
    if isinstance(steel, dict) and steel.get("present"):
        colours = [row for row in (steel.get("colours") or []) if isinstance(row, dict)]
        printed = steel.get("total_linear_feet")
        if colours and not vocab.is_empty(printed):
            summed = math.fsum([_number(row.get("linear_feet")) for row in colours])
            if not _within(summed, _number(printed), footage_tolerance):
                findings.append(FieldFinding(
                    path="c_steel_fab.total_linear_feet", status="inconsistent",
                    reason=(
                        f"the colour grid's {len(colours)} colour(s) add up to "
                        f"{summed:g} linear feet and the printed total reads "
                        f"{_number(printed):g}, outside the governed tolerance "
                        f"of {footage_tolerance:g}% — and the steel rate "
                        "multiplies exactly that number"
                    ),
                ))
    return findings


def _document_confidence(
    reading: dict, plant: PlantParameters
) -> list[FieldFinding]:
    """The whole-document score against the governed extraction floor.

    Its own question, and a different one from the per-field floor: a page can
    read cleanly overall and still have one smudged quantity, and a page that
    reads badly overall goes to review whatever the individual fields say.
    """
    floor = plant.thresholds.min_extraction_confidence
    score = _number(reading.get("confidence"))
    if not floor or score >= floor:
        return []
    return [FieldFinding(
        path="confidence", status="low_confidence",
        reason=(
            f"the extraction read this document at {score:.2f} confidence "
            f"overall, below the governed floor of {floor:.2f} — a page that "
            "reads badly as a whole goes to review whatever the individual "
            "fields say"
        ),
    )]


def _within(actual: float, printed: float, tolerance_pct: float) -> bool:
    """Whether two figures agree, inside a tolerance stated as a percentage."""
    if not printed:
        # A printed total of zero cannot be compared as a percentage of
        # itself. Agreement is then exact agreement, which is the only honest
        # reading of "these two numbers should be the same".
        return actual == printed
    return abs(actual - printed) <= abs(printed) * (tolerance_pct / 100.0)


def _number(value) -> float:
    try:
        return float(value or _ZERO)
    except (TypeError, ValueError):
        return _ZERO


# ---------------------------------------------------------------------------
# c2 — the computed hours against their governed band
# ---------------------------------------------------------------------------


def compute_demand(
    reading: dict, plant: PlantParameters, labor: formulas.Parameters
) -> list[DepartmentDemand]:
    """One row per department whose page family is present. Pure.

    **A department whose page family is absent is skipped — that department
    only, never the whole job.** A building with no trusses is a building with
    no trusses, and a zero would read as "no work" where a skip reads as "we
    were not told".
    """
    out: list[DepartmentDemand] = []
    for department in plant.slugs:
        recipe = FORMULAS.get(department)
        if recipe is None:
            # The governed page names a department this build has no formula
            # for. Not a failure and not a silent skip: the flow reports it as
            # a count, because a department added to the page and nowhere else
            # would otherwise never appear on a plant's day.
            continue
        families, compose = recipe
        present = [family for family in families if _present(reading, family)]
        if not present:
            continue
        results = compose(reading, labor)
        hours = math.fsum([result.hours for result in results])
        band = plant.band_for(department)
        rounded = (
            round(hours, labor.rounding_decimals)
            if labor.rounding_decimals is not None else hours
        )
        out.append(DepartmentDemand(
            department=department,
            labor_hours=rounded,
            hours_basis=" + ".join(
                result.basis for result in results if result.terms or result.hours
            ) or results[0].basis,
            demand_summary=_summary(department, results, present),
            source_page=" · ".join(
                sorted({vocab.PAGE_BY_FAMILY.get(family, family)
                        for family in present})
            ),
            confidence=_family_confidence(reading, present),
            band_low=band.low if band else None,
            band_high=band.high if band else None,
            out_of_band=bool(band and not (band.low <= rounded <= band.high)),
            terms=[term for result in results for term in result.terms],
            flags=[flag for result in results for flag in result.flags],
            missing=[name for result in results for name in result.missing],
            row_kinds=_row_kinds(department, reading),
        ))
    return out


def check_labor_hours(
    demand: list[DepartmentDemand], plant: PlantParameters
) -> list[FieldFinding]:
    """The governed formula's hours against that department's governed band.

    The SDD's fourth business exception — *"labor calculation out of expected
    range → supervisor review before writing"* — as a field verdict rather than
    a second gate. Raised on `hours.<department>` and not on a grid field,
    because the number a supervisor is being asked about was computed and is
    printed nowhere: there is no ink for a box, and the locate call is never
    handed the path.

    A governed value the formulas page did not carry is its own finding. A
    plausible figure computed around a missing rate would be a schedule nobody
    can dispute.
    """
    findings: list[FieldFinding] = []
    for row in demand:
        if row.missing:
            findings.append(FieldFinding(
                path=vocab.hours_path(row.department), status="missing",
                reason=(
                    f"the governed formulas page does not carry "
                    f"{', '.join(sorted(set(row.missing)))}, so "
                    f"{row.department}'s hours could not be computed from it"
                ),
            ))
            continue
        if not row.out_of_band:
            continue
        band = plant.band_for(row.department)
        findings.append(FieldFinding(
            path=vocab.hours_path(row.department), status="out_of_band",
            reason=(
                f"the governed formula computes {row.labor_hours:g} h for "
                f"{row.department}, outside its governed expected band of "
                f"{band.low:g} to {band.high:g} h — which the plant's page "
                "puts in front of a supervisor before it is written"
                if band else
                f"{row.labor_hours:g} h for {row.department}, and the governed "
                "page carries no expected band to check it against"
            ),
        ))
    return findings


def _present(reading: dict, family: str) -> bool:
    page = reading.get(family)
    return bool(isinstance(page, dict) and page.get("present"))


def _row_kinds(department: str, reading: dict) -> list[str]:
    """`standard`, plus the two extra rows this ticket's own facts raise."""
    kinds = ["standard"]
    if department == "truss-fab":
        if formulas.nailing_applies(reading):
            kinds.append("nailing")
        if formulas.girts_applies(reading):
            kinds.append("girts")
    return kinds


def _summary(
    department: str, results: list[formulas.HoursResult], families: list[str]
) -> str:
    """The sentence a supervisor reads beside the hours."""
    pages = ", ".join(
        sorted({vocab.PAGE_BY_FAMILY.get(family, family) for family in families})
    )
    parts = [
        f"{result.hours:g} h from {result.formula}"
        for result in results if result.hours or result.terms
    ]
    return f"page {pages}: " + ("; ".join(parts) if parts else "nothing to do")


def _family_confidence(reading: dict, families: list[str]) -> float:
    """The **least** confident field on the grids that fed this share.

    Per share rather than per document, which is what `JobTask.confidence`
    declares: one unreadable grid should not put a whole job in doubt, and one
    confidently-read grid should not vouch for the smudged one beside it. The
    document's own score stands in when the extraction reported nothing per
    field.
    """
    reported = reading.get("field_confidence") or {}
    scores = [
        float(value)
        for path, value in reported.items()
        if vocab.family_of(path) in families and _is_number(value)
    ]
    return min(scores) if scores else _number(reading.get("confidence"))


def _is_number(value) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def summarize_demand(demand: list[DepartmentDemand]) -> str:
    """The `check-labor-hours` step's journal line."""
    if not demand:
        return "no page family a governed formula reads is present on this ticket"
    body = " · ".join(
        f"{row.department} {row.labor_hours:g} h"
        + (" (out of band)" if row.out_of_band else "")
        for row in demand
    )
    flagged = [row.department for row in demand if row.missing]
    return body + (
        f" — governed values missing for {', '.join(flagged)}" if flagged else ""
    )
