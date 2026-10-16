"""What a job costs each department, in hours — the shape, and not one number.

Modelled on `accounts_payable/v8/arithmetic.py`, which is this repository's
existing answer to the same question: the arithmetic is code, the constants are
governed knowledge, and the run records the revision it read them at. Two
documents are read here and nothing else is:

- **`labor-hour-formulas`** — every rate, divisor, threshold, band and add-on
  the plant uses, plus the rounding;
- **`truss-standards`** — the cut / fabrication / nailing hours for one truss,
  by span and member count, and the girts figure.

**There is no number in this module.** Not a rate, not a threshold, not a
default. Every constant arrives on `Parameters`, `TrussStandards` or
`Incentive`, each parsed from those pages, and a page that does not carry a
line leaves the field `None` — which the formula reports as a *missing governed
value* rather than computing around. `_ZERO` is where a total starts, `Decimal`
does the round-ups exactly, table columns are read by unpacking rather than by
index, and the only literals in the file are `0`, `1` and the English-numeral
table `_NUMERALS`, which exists because the document spells one of its divisors
as a word: *"concrete mix hours per **two** bags"*. That word is read, not
assumed — change it to `three` and the computed figure changes, which is the
whole property this module is for.

`server/tests/test_labor_formulas.py` proves it twice: by walking this file's
own syntax tree and refusing a numeric literal outside that allowance, and by
editing the governed page and watching a department's hours move.

**What is not here.** Nothing decides anything. Which department is scheduled,
on what day, up to what capacity and by whose approval is the scheduling
parameters' business and the supervisor's; whether a computed figure is
plausible is the expected-hours band's, which lives on the *other* page because
it is a question about a day rather than about arithmetic. What this module
answers is exactly one question per department: given what the shipping list
says, how many hours.

`incentive()` is the exception worth naming: its two governed values live in
`plant-scheduling-parameters` rather than in the formulas page, because the
bonus cell is a property of a *day* and not of a job. It takes its own parsed
`Incentive` for that reason — a single parameter bag spanning two documents
would put one revision stamp on two pages.
"""

from __future__ import annotations

import math
import re
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Iterable

from pydantic import BaseModel, Field

LABOR_ASSET_ID = "labor-hour-formulas"
TRUSS_ASSET_ID = "truss-standards"

#: Where every total starts. Written this way rather than as a literal so the
#: claim in the docstring is exactly true and the test can be exactly strict.
_ZERO: float = math.fsum([])

#: English numerals, because the governed page spells one divisor as a word.
#: Orthography rather than a plant parameter: what "two bags" *means* is
#: English, and what the rate is comes off the page.
_NUMERALS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


# ---------------------------------------------------------------------------
# The read: two governed pages, parsed
# ---------------------------------------------------------------------------


class Band(BaseModel):
    """One row of a range lookup: a ceiling, and what it costs.

    "The first row whose ceiling the count does not exceed wins" — read as *up
    to and including*, which is what both governed tables say of themselves.
    """

    ceiling: float
    hours: float


class TrussRow(BaseModel):
    """One row of the truss standards: two ceilings and three hour figures."""

    span_ft: float
    members: float
    cut_hours: float
    fab_hours: float
    nailing_hours: float


class TrussStandards(BaseModel):
    """The span/member lookup, as a run read it.

    `rows` in document order, because the selection rule is *first row whose
    ceilings the truss does not exceed* and re-sorting a governed table would
    quietly change which row a truss lands on.
    """

    rows: list[TrussRow] = Field(default_factory=list)
    girts_hours_per_truss: float | None = None
    source_ref: str = f"knowledge:{TRUSS_ASSET_ID}"
    found_asset: bool = False


class Incentive(BaseModel):
    """One department's bonus cell, as two governed numbers.

    A starting figure that is negative and a rate per hour. The plant's own
    phrase for filling a day is getting *out of the hole*, and the floor is
    where this crosses zero — arithmetic somebody can check rather than a
    spreadsheet cell nobody can.
    """

    department: str = ""
    start_usd: float | None = None
    rate_usd_per_hour: float | None = None
    source_ref: str = ""
    found_asset: bool = False


class Parameters(BaseModel):
    """Every constant `labor-hour-formulas` carries, and where it came from.

    `None` everywhere rather than a default, and that is the design: a default
    would be a number in this module wearing a governed value's clothes. A page
    that has not been seeded, or a line somebody removed, leaves the field
    `None`, and the formula that needed it names the line in
    `HoursResult.missing` instead of computing on an invention.
    """

    # -- global -------------------------------------------------------------
    rounding_decimals: int | None = None

    # -- components ---------------------------------------------------------
    components_bands: list[Band] = Field(default_factory=list)
    components_overflow_base_hours: float | None = None
    components_overflow_hours_per_item: float | None = None

    # -- laminates ----------------------------------------------------------
    #: "3-ply 2x6" → hours per foot per column, keyed exactly as printed and
    #: lower-cased, so a new lumber size is a table row and not a deploy.
    laminate_rates: dict[str, float] = Field(default_factory=dict)
    perma_base_deduction_ft: float | None = None
    perma_attach_hours: float | None = None
    precut_department: str = ""
    precut_cut_hours_per_item: float | None = None
    larger_than_2x6_sizes: list[str] = Field(default_factory=list)
    spliced_2x6_over_ft: float | None = None
    spliced_larger_over_ft: float | None = None

    # -- verticals ----------------------------------------------------------
    verticals_base_hours: float | None = None
    verticals_hours_per_block: float | None = None
    verticals_hours_per_plywood_sheet: float | None = None
    verticals_hours_per_ridge_cleat: float | None = None

    # -- stack out ----------------------------------------------------------
    stack_out_rounding_decimals: int | None = None

    # -- steel fab ----------------------------------------------------------
    steel_lf_rate: float | None = None
    steel_condenstop_lf_rate: float | None = None
    steel_setup_hours: float | None = None
    steel_colour_surcharge: float | None = None
    crib_hours: dict[str, float] = Field(default_factory=dict)

    # -- trim pack ----------------------------------------------------------
    trim_bundle_weight_lb: float | None = None
    trim_extra_bundles_for_fj_jj: float | None = None
    trim_hours_per_bundle: float | None = None
    trim_packaging_round_to: float | None = None
    trim_long_threshold_in: float | None = None
    #: The add-on table, keyed by the add-on as printed. The keys carry their
    #: own matching vocabulary: the acronyms in `long UCR trim` are what a trim
    #: row's description is tested against, so renaming the add-on on the page
    #: renames the keyword too and this module still holds neither.
    trim_addons: dict[str, float] = Field(default_factory=dict)
    trim_house_wrap_cap: float | None = None

    # -- slide door ---------------------------------------------------------
    slide_end_caps_per_door: float | None = None
    slide_door_ladder: list[Band] = Field(default_factory=list)
    slide_hours_per_additional_door: float | None = None
    slide_pull_plate_hours: float | None = None
    slide_non_wick_repackaging_hours: float | None = None
    slide_hours_added_to: str = ""

    # -- hardware pack ------------------------------------------------------
    hardware_wings_rate_per_foot: float | None = None
    hardware_setup_hours: float | None = None
    hardware_hours_per_line_item: float | None = None
    hardware_line_items: dict[str, float] = Field(default_factory=dict)
    concrete_bags_per_increment: float | None = None
    concrete_hours_per_increment: float | None = None
    concrete_cap_above_bags: float | None = None
    concrete_cap_hours: float | None = None
    l_bracket_flat_up_to: float | None = None
    l_bracket_flat_hours: float | None = None
    l_bracket_divisor: float | None = None
    l_bracket_hours_per_divisor: float | None = None

    # -- loading ------------------------------------------------------------
    loading_width_bands: list[Band] = Field(default_factory=list)
    loading_overflow_hours: float | None = None
    loading_roller_trailer_hours: float | None = None
    loading_hours_per_material_grid: float | None = None

    # -- the truss lookup, read from its own page ---------------------------
    truss: TrussStandards = Field(default_factory=TrussStandards)

    source_ref: str = f"knowledge:{LABOR_ASSET_ID}"
    found_asset: bool = False


# -- the parse ---------------------------------------------------------------
#
# Tolerant and **section-scoped**, the way `parse_posting_policy` and
# `parse_routing` are tolerant: this is a page a plant superintendent edits, so
# a re-wrapped paragraph or a re-ordered table has to degrade one value at a
# time rather than raise in the middle of somebody's schedule.
#
# Section-scoping is not tidiness. `plant-scheduling-parameters` carries a
# quoted `- goal max: —` under its `## Open` heading, and a parser matching
# document-wide would read a quotation as a capacity table. Every table below
# is read out of the section that owns it for that reason, and so is every key
# whose name is not already unique to its department.
#
# The `key` arguments are regular expressions rather than escaped literals, on
# purpose: two governed keys name a threshold inside themselves
# (`… divisor above 30`), and matching that with `\d+` is what keeps the value
# beside it readable when somebody changes the threshold.


def _section(content: str, heading: str) -> str:
    """The body under one heading, up to the next heading of any level."""
    match = re.search(
        rf"^#{{1,4}}\s*{re.escape(heading)}\s*$(?P<body>.*?)(?=^#{{1,4}}\s|\Z)",
        content or "", re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    return match.group("body") if match else ""


def _number(text: str, key: str) -> float | None:
    """The number on a `- key: value` line, or None if the page lacks it."""
    match = re.search(
        rf"^\s*[-*]\s*{key}:?\s*[^\d\-+]*(?P<value>[-+]?\d+(?:\.\d+)?)",
        text or "", re.IGNORECASE | re.MULTILINE,
    )
    return float(match.group("value")) if match else None


def _integer(text: str, key: str) -> int | None:
    value = _number(text, key)
    return int(value) if value is not None else None


def _text(text: str, key: str) -> str:
    match = re.search(
        rf"^\s*[-*]\s*{key}:?\s*(?P<value>.+?)\s*$",
        text or "", re.IGNORECASE | re.MULTILINE,
    )
    return match.group("value").strip().strip("`*_") if match else ""


def _list(text: str, key: str) -> list[str]:
    return [part.strip() for part in _text(text, key).split(",") if part.strip()]


def _table(text: str) -> list[list[str]]:
    """The data rows of the first markdown table in this text.

    The header is dropped by shape rather than by position: the row whose cells
    are all dashes is the rule, and everything before it was the header. A
    second table in the same section is a different table and is not read —
    which is why each caller passes the section that owns the one it wants.
    """
    rows: list[list[str]] = []
    ruled = False
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if rows:
                break
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if all(set(cell) <= set("-: ") for cell in cells):
            ruled = True
            rows = []
            continue
        rows.append(cells)
    return rows if ruled else []


def _cell(cell: str) -> float | None:
    """The first number in a table cell — `99 (any)` is 99, `+0.15 per job` 0.15."""
    match = re.search(r"[-+]?\d+(?:\.\d+)?", cell or "")
    return float(match.group(0)) if match else None


def _bands(text: str) -> list[Band]:
    """A ceiling→hours table, in document order."""
    out: list[Band] = []
    for cells in _table(text):
        if len(cells) < len(Band.model_fields):
            continue
        raw_ceiling, raw_hours, *_rest = cells
        ceiling, hours = _cell(raw_ceiling), _cell(raw_hours)
        if ceiling is not None and hours is not None:
            out.append(Band(ceiling=ceiling, hours=hours))
    return out


def _named_hours(text: str) -> dict[str, float]:
    """A `name → hours` table, keyed by the name exactly as printed.

    The figure is the **last** column, which is what the two- and three-column
    tables on this page have in common: the add-on table's middle column is the
    condition, written for a person to read and not for this to parse.
    """
    out: dict[str, float] = {}
    for cells in _table(text):
        if len(cells) < len(Band.model_fields):
            continue
        name, *_middle = cells
        hours = _cell(_last(cells))
        if name and hours is not None:
            out[name.strip()] = hours
    return out


def _counted(text: str, key: str) -> tuple[float | None, float | None]:
    """A `- … <count> …: <hours>` line, as (count, hours).

    The count may be a digit or an English numeral, because the governed page
    writes *"per two bags"* and *"cap above ten bags"*. Reading the word is
    what keeps the divisor governed; assuming it would put a `2` in this
    module.
    """
    match = re.search(
        rf"^\s*[-*]\s*{key}:?\s*(?P<hours>.+)$",
        text or "", re.IGNORECASE | re.MULTILINE,
    )
    if match is None:
        return None, None
    raw = match.group("count").strip().lower()
    count = (
        float(_NUMERALS[raw]) if raw in _NUMERALS
        else float(raw) if re.fullmatch(r"\d+(?:\.\d+)?", raw) else None
    )
    return count, _cell(match.group("hours"))


def parse_truss_standards(content: str, ref: str) -> TrussStandards:
    """The span/member lookup, off its own governed page."""
    standards = TrussStandards(
        source_ref=ref or f"knowledge:{TRUSS_ASSET_ID}", found_asset=bool(content)
    )
    standards.girts_hours_per_truss = _number(
        _section(content, "How the lookup works"),
        r"girts plated in, hours per truss",
    )
    for cells in _table(_section(content, "Cut, fab and nailing hours")):
        if len(cells) < len(TrussRow.model_fields):
            continue
        raw_span, raw_members, raw_cut, raw_fab, raw_nail, *_rest = cells
        values = [
            _cell(raw_span), _cell(raw_members), _cell(raw_cut),
            _cell(raw_fab), _cell(raw_nail),
        ]
        if any(value is None for value in values):
            continue
        span, members, cut, fab, nail = values
        standards.rows.append(TrussRow(
            span_ft=span, members=members, cut_hours=cut,
            fab_hours=fab, nailing_hours=nail,
        ))
    return standards


def parse_labor_parameters(
    content: str, ref: str, truss: TrussStandards | None = None
) -> Parameters:
    """Every constant the formulas below consume, section by section."""
    params = Parameters(
        source_ref=ref or f"knowledge:{LABOR_ASSET_ID}",
        found_asset=bool(content),
        truss=truss or TrussStandards(),
    )

    usage = _section(content, "How these formulas are used")
    params.rounding_decimals = _integer(usage, r"computed hours rounding")

    components = _section(content, "Components")
    params.components_bands = _bands(components)
    params.components_overflow_base_hours = _number(
        components, r"components beyond the table, base hours"
    )
    params.components_overflow_hours_per_item = _number(
        components, r"components beyond the table, hours per extra item"
    )

    laminates = _section(content, "Laminates")
    params.laminate_rates = {
        name.lower(): hours for name, hours in _named_hours(laminates).items()
    }
    params.perma_base_deduction_ft = _number(
        laminates, r"laminates perma column base deduction"
    )
    params.perma_attach_hours = _number(
        laminates, r"laminates perma column attach hours"
    )
    params.precut_department = _text(
        laminates, r"laminates precut items scheduled in"
    )
    params.precut_cut_hours_per_item = _number(
        laminates, r"laminates precut cut hours per item"
    )
    mix = _section(content, "Spliced, and larger than 2x6")
    params.larger_than_2x6_sizes = [
        size.lower() for size in _list(mix, r"laminates larger-than-2x6 sizes")
    ]
    params.spliced_2x6_over_ft = _number(
        mix, r"laminates spliced when 2x6 longer than"
    )
    params.spliced_larger_over_ft = _number(
        mix, r"laminates spliced when 2x8, 2x10 or 2x12 longer than"
    )

    verticals = _section(content, "Verticals")
    params.verticals_base_hours = _number(verticals, r"verticals base hours")
    params.verticals_hours_per_block = _number(
        verticals, r"verticals hours per block"
    )
    params.verticals_hours_per_plywood_sheet = _number(
        verticals, r"verticals hours per ripped plywood sheet"
    )
    params.verticals_hours_per_ridge_cleat = _number(
        verticals, r"verticals hours per ridge cleat"
    )

    params.stack_out_rounding_decimals = _integer(
        _section(content, "Stack Out"), r"stack out board hours rounding"
    )

    steel = _section(content, "Steel Fab")
    params.steel_lf_rate = _number(steel, r"steel fab linear foot rate")
    params.steel_condenstop_lf_rate = _number(
        steel, r"steel fab condenstop linear foot rate"
    )
    params.steel_setup_hours = _number(steel, r"steel fab setup hours")
    params.steel_colour_surcharge = _number(
        steel, r"steel fab colour surcharge per additional colour"
    )
    params.crib_hours = {
        size.lower(): hours for size, hours in _named_hours(steel).items()
    }

    trim = _section(content, "Trim Pack")
    params.trim_bundle_weight_lb = _number(trim, r"trim pack bundle weight")
    params.trim_extra_bundles_for_fj_jj = _number(
        trim, r"trim pack extra bundle for FJ or JJ trim"
    )
    params.trim_hours_per_bundle = _number(
        trim, r"trim pack packaging hours per bundle"
    )
    params.trim_packaging_round_to = _number(trim, r"trim pack packaging rounding")
    params.trim_long_threshold_in = _number(trim, r"trim pack long trim threshold")
    params.trim_house_wrap_cap = _number(trim, r"trim pack house wrap cap")
    params.trim_addons = _named_hours(trim)

    slide = _section(content, "Slide Door")
    params.slide_end_caps_per_door = _number(slide, r"slide door end caps per door")
    params.slide_hours_per_additional_door = _number(
        slide, r"slide door hours per additional door"
    )
    params.slide_pull_plate_hours = _number(slide, r"slide door pull plate hours")
    params.slide_non_wick_repackaging_hours = _number(
        slide, r"slide door non-wick trim repackaging hours"
    )
    params.slide_hours_added_to = _text(slide, r"slide door hours are added to")
    params.slide_door_ladder = _bands(slide)

    hardware = _section(content, "Hardware Pack")
    params.hardware_wings_rate_per_foot = _number(
        hardware, r"hardware pack wings rate per foot"
    )
    params.hardware_setup_hours = _number(hardware, r"hardware pack setup hours")
    params.hardware_hours_per_line_item = _number(
        hardware, r"hardware pack hours per line item"
    )
    params.hardware_line_items = _named_hours(hardware)
    (
        params.concrete_bags_per_increment,
        params.concrete_hours_per_increment,
    ) = _counted(
        hardware, r"hardware pack concrete mix hours per (?P<count>[^\s:]+) bags"
    )
    (
        params.concrete_cap_above_bags,
        params.concrete_cap_hours,
    ) = _counted(
        hardware, r"hardware pack concrete mix cap above (?P<count>[^\s:]+) bags"
    )
    (
        params.l_bracket_flat_up_to,
        params.l_bracket_flat_hours,
    ) = _counted(
        hardware, r"hardware pack 12x5 l bracket hours up to (?P<count>[^\s:]+)"
    )
    params.l_bracket_divisor = _number(
        hardware, r"hardware pack 12x5 l bracket divisor above \d+"
    )
    params.l_bracket_hours_per_divisor = _number(
        hardware, r"hardware pack 12x5 l bracket hours per divisor"
    )

    loading = _section(content, "Loading")
    params.loading_width_bands = _bands(loading)
    params.loading_overflow_hours = _number(
        loading, r"loading beyond the table, hours"
    )
    params.loading_roller_trailer_hours = _number(
        loading, r"loading roller trailer override hours"
    )
    params.loading_hours_per_material_grid = _number(
        loading, r"loading hours per material grid"
    )
    return params


# ---------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------
#
# Everything below is pure: it reads its two arguments and nothing else, holds
# no literal a plant could want to change, and is replayable against a fixture
# with no world behind it. `grid` is the extraction's own dictionary — the
# shape the governed extraction prompt asks for — so a formula names the same
# field paths the verdicts and the boxes on the page name.


class HoursResult(BaseModel):
    """One department's hours, and everything needed to argue with them.

    `basis` is the sentence a schedule row carries: which formula produced the
    figure, and at which revision of which page. That is the difference between
    *"why is this 27.4 hours"* answering with a clause and answering with a
    stack trace.
    """

    department: str = ""
    hours: float = _ZERO
    formula: str = ""
    basis: str = ""
    #: The arithmetic, term by term, in the order it was added — read in the
    #: journal and beside the grid on the review screen.
    terms: list[str] = Field(default_factory=list)
    #: Governed lines the page did not carry. A formula reports them rather
    #: than substituting a number of its own: a missing rate is a document to
    #: fix, and a plausible figure computed around it is a schedule nobody can
    #: dispute.
    missing: list[str] = Field(default_factory=list)
    #: What is on the row and deliberately not in the figure — an out-of-range
    #: truss, Stack Out's plywood and pallet items, the slide door's framing
    #: grid.
    flags: list[str] = Field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing


class LaminatesMix(BaseModel):
    """Which capacity row Laminates' day is on, and why.

    Booleans and reasons rather than the row's name: the row names live in the
    scheduling parameters' capacity table, and naming one here would be this
    module holding a value off a page it does not read.
    """

    has_larger_than_2x6: bool = False
    has_spliced: bool = False
    reasons: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)

    @property
    def spliced_or_over_2x6(self) -> bool:
        return self.has_larger_than_2x6 or self.has_spliced


# -- small helpers, all literal-free ----------------------------------------


def _rounded(value: float, decimals: int | None) -> float:
    """The governed rounding, or the raw figure when the page names none."""
    return value if decimals is None else round(value, decimals)


def _ceil_div(value: float, divisor: float) -> float:
    """How many whole `divisor`s cover `value` — a partial one counts.

    `Decimal` rather than float division because the callers round *up*, and
    `math.ceil(0.3 / 0.1)` is 4 on binary floats. A bundle count that depends
    on that is a bundle count nobody can reproduce.
    """
    quotient = Decimal(str(value)) / Decimal(str(divisor))
    return float(quotient.to_integral_value(rounding=ROUND_CEILING))


def _floor_div(value: float, divisor: float) -> float:
    """How many whole `divisor`s fit inside `value` — `Decimal` for the reason above."""
    quotient = Decimal(str(value)) / Decimal(str(divisor))
    return float(quotient.to_integral_value(rounding=ROUND_FLOOR))


def _up_product(count: float, per_unit: float, step: float) -> float:
    """`count × per_unit`, rounded up to the next whole `step`, exactly."""
    product = Decimal(str(count)) * Decimal(str(per_unit))
    stepped = Decimal(str(step))
    return float(
        (product / stepped).to_integral_value(rounding=ROUND_CEILING) * stepped
    )


def _need(values: dict[str, float | int | None]) -> list[str]:
    """The governed lines that are not on the page, named as the page names them."""
    return [name for name, value in values.items() if value is None]


def _band_hours(bands: list[Band], measure: float) -> tuple[float | None, bool]:
    """The first band whose ceiling `measure` does not exceed, and whether it ran off the end."""
    for band in bands:
        if measure <= band.ceiling:
            return band.hours, False
    return None, True


def _last(items: list):
    *_head, final = items
    return final


def _rows(grid: dict, family: str, key: str) -> list[dict]:
    page = grid.get(family) or {}
    return [row for row in (page.get(key) or []) if isinstance(row, dict)]


def _float(value) -> float:
    try:
        return float(value or _ZERO)
    except (TypeError, ValueError):
        return _ZERO


def _acronyms(name: str) -> list[str]:
    """The all-capital tokens in an add-on's printed name — `long UCR trim` → `UCR`.

    The keyword a trim row's description is tested against comes out of the
    governed add-on name itself, so renaming the add-on renames the keyword and
    this module keeps holding neither.
    """
    return [
        token for token in re.findall(r"[A-Za-z0-9]+", name or "")
        if token.isupper() and len(token) > 1
    ]


def _mentions(description: str, phrase: str) -> bool:
    """Whether a printed description names this governed item.

    Alternatives split on `/`, the way the governed table writes them
    (`LP2 / profile vent`), so either spelling on the ticket matches the one
    row that governs it.
    """
    text = (description or "").lower()
    return any(
        part.strip() and part.strip().lower() in text
        for part in (phrase or "").split("/")
    )


def page_present(grid: dict, family: str) -> bool:
    """Whether the extraction reported this page family as present at all.

    Absent is not zero, and the distinction is the whole design's: a department
    whose page family is absent is **skipped**, and a zero would read as "no
    work". A building with no trusses is a building with no trusses.
    """
    page = grid.get(family)
    return bool(isinstance(page, dict) and page.get("present"))


# -- Components --------------------------------------------------------------


def components_hours(grid: dict, params: Parameters) -> HoursResult:
    """A quantity-range lookup, and a slope past the end of the table.

    The setup dominates a small pick and the marginal item dominates a large
    one, which is why the governed page states this as steps rather than as a
    rate. Past the last band the two overflow figures take over.
    """
    result = HoursResult(
        department="components", formula="components: quantity-range lookup"
    )
    if not params.components_bands:
        result.missing.append("the components quantity-range table")
        return _finish(result, _ZERO, params)
    page = grid.get("b_components") or {}
    count = _float(page.get("total_quantity")) or math.fsum(
        [_float(row.get("quantity")) for row in _rows(grid, "b_components", "rows")]
    )
    hours, beyond = _band_hours(params.components_bands, count)
    if not beyond:
        result.terms.append(f"{count:g} item(s) → {hours:g} h from the band table")
        return _finish(result, hours, params)
    result.missing.extend(_need({
        "components beyond the table, base hours":
            params.components_overflow_base_hours,
        "components beyond the table, hours per extra item":
            params.components_overflow_hours_per_item,
    }))
    if result.missing:
        return _finish(result, _ZERO, params)
    ceiling = _last(params.components_bands).ceiling
    extra = count - ceiling
    result.terms.append(
        f"{count:g} item(s) is past the {ceiling:g} band: "
        f"{params.components_overflow_base_hours:g} h + {extra:g} × "
        f"{params.components_overflow_hours_per_item:g} h"
    )
    return _finish(result, math.fsum([
        params.components_overflow_base_hours,
        extra * params.components_overflow_hours_per_item,
    ]), params)


# -- Laminates ---------------------------------------------------------------


def laminates_hours(grid: dict, params: Parameters) -> HoursResult:
    """`quantity × length × rate`, with the Perma Column's two halves.

    A Perma Column is shorter than the column it replaces by a fixed base, so
    the base comes off each one's length before the rate applies and the attach
    hours go back on per column. Subtracting one and forgetting the other would
    under-count the job, which is why the governed page names both.

    **Precut items are not Laminates hours at all.** They are cut in Truss
    Cutting and charged there — `precut_cut_hours` below is that figure.
    """
    result = HoursResult(
        department="laminates", formula="laminates: quantity × length × rate"
    )
    if not params.laminate_rates:
        result.missing.append("the laminates ply-and-size rate table")
        return _finish(result, _ZERO, params)
    parts: list[float] = []
    for index, row in enumerate(_rows(grid, "b_laminates", "rows")):
        if row.get("precut"):
            continue
        key = _laminate_key(row)
        rate = params.laminate_rates.get(key)
        if rate is None:
            result.missing.append(
                f"a laminates rate for `{key}` (b_laminates.rows[{index}])"
            )
            continue
        quantity = _float(row.get("quantity"))
        length = _float(row.get("length_ft"))
        if row.get("perma_column"):
            if (
                params.perma_base_deduction_ft is None
                or params.perma_attach_hours is None
            ):
                result.missing.extend(_need({
                    "laminates perma column base deduction":
                        params.perma_base_deduction_ft,
                    "laminates perma column attach hours": params.perma_attach_hours,
                }))
                continue
            length = max(length - params.perma_base_deduction_ft, _ZERO)
            parts.append(quantity * params.perma_attach_hours)
            result.terms.append(
                f"b_laminates.rows[{index}]: Perma Column, less "
                f"{params.perma_base_deduction_ft:g} ft base, plus "
                f"{quantity:g} × {params.perma_attach_hours:g} h attach"
            )
        parts.append(quantity * length * rate)
        result.terms.append(
            f"b_laminates.rows[{index}]: {quantity:g} × {length:g} ft × "
            f"{rate:g} h/ft ({key})"
        )
    return _finish(result, math.fsum(parts), params)


def _laminate_key(row: dict) -> str:
    """`3-ply 2x6`, from the ply count and the size the description carries."""
    return f"{_float(row.get('ply')):g}-ply {str(row.get('lumber_size') or '').strip().lower()}"


def precut_cut_hours(grid: dict, params: Parameters) -> HoursResult:
    """The laminates precut items, charged where they are actually cut.

    Truss Cutting's row. The sequencing that follows from it — a precut must be
    cut before its laminates date — is the scheduling parameters' business.
    """
    result = HoursResult(
        department=params.precut_department,
        formula="laminates precut: items × cut hours per item, charged to truss cutting",
    )
    if params.precut_cut_hours_per_item is None:
        result.missing.append("laminates precut cut hours per item")
        return _finish(result, _ZERO, params)
    count = math.fsum([
        _float(row.get("quantity"))
        for row in _rows(grid, "b_laminates", "rows") if row.get("precut")
    ])
    if count:
        result.terms.append(
            f"{count:g} precut item(s) × {params.precut_cut_hours_per_item:g} h"
        )
    return _finish(result, count * params.precut_cut_hours_per_item, params)


def laminates_mix(grid: dict, params: Parameters) -> LaminatesMix:
    """Spliced, or larger than a 2x6 — the two conditions, identified.

    A job with any spliced column or any column above a 2x6 puts the day on the
    heavier capacity row. Which row that is has a name on the scheduling
    parameters' page; what is decided here is only whether the condition holds.
    """
    mix = LaminatesMix()
    if not params.larger_than_2x6_sizes:
        mix.missing.append("laminates larger-than-2x6 sizes")
    mix.missing.extend(_need({
        "laminates spliced when 2x6 longer than": params.spliced_2x6_over_ft,
        "laminates spliced when 2x8, 2x10 or 2x12 longer than":
            params.spliced_larger_over_ft,
    }))
    for index, row in enumerate(_rows(grid, "b_laminates", "rows")):
        size = str(row.get("lumber_size") or "").strip().lower()
        length = _float(row.get("length_ft"))
        larger = size in params.larger_than_2x6_sizes
        if larger:
            mix.has_larger_than_2x6 = True
            mix.reasons.append(
                f"b_laminates.rows[{index}] is a {size}, which the governed "
                "page lists as larger than a 2x6"
            )
        threshold = (
            params.spliced_larger_over_ft if larger else params.spliced_2x6_over_ft
        )
        if threshold is not None and length > threshold:
            mix.has_spliced = True
            mix.reasons.append(
                f"b_laminates.rows[{index}] is {length:g} ft, over the "
                f"{threshold:g} ft splice threshold for a {size or '2x6'}"
            )
    return mix


# -- Verticals ---------------------------------------------------------------


def verticals_hours(grid: dict, params: Parameters) -> HoursResult:
    """A fixed base plus per-item additions, all four figures governed."""
    result = HoursResult(
        department="verticals",
        formula="verticals: base + blocks + ripped plywood + ridge cleats",
    )
    result.missing.extend(_need({
        "verticals base hours": params.verticals_base_hours,
        "verticals hours per block": params.verticals_hours_per_block,
        "verticals hours per ripped plywood sheet":
            params.verticals_hours_per_plywood_sheet,
        "verticals hours per ridge cleat": params.verticals_hours_per_ridge_cleat,
    }))
    if result.missing:
        return _finish(result, _ZERO, params)
    page = grid.get("b_verticals") or {}
    blocks = _float(page.get("blocks_quantity"))
    sheets = _float(page.get("ripped_plywood_sheets"))
    cleats = _float(page.get("ridge_cleat_quantity"))
    result.terms.append(
        f"{params.verticals_base_hours:g} h base + {blocks:g} × "
        f"{params.verticals_hours_per_block:g} + {sheets:g} × "
        f"{params.verticals_hours_per_plywood_sheet:g} + {cleats:g} × "
        f"{params.verticals_hours_per_ridge_cleat:g}"
    )
    return _finish(result, math.fsum([
        params.verticals_base_hours,
        blocks * params.verticals_hours_per_block,
        sheets * params.verticals_hours_per_plywood_sheet,
        cleats * params.verticals_hours_per_ridge_cleat,
    ]), params)


# -- Stack Out ---------------------------------------------------------------


def stack_out_hours(grid: dict, params: Parameters) -> HoursResult:
    """The one department whose hours are read rather than computed.

    The loader list already carries pre-calculated board hours, and this reads
    that figure. Deriving a second one and quietly preferring it would give the
    plant two answers to one question. Plywood and pallet items stay manual and
    are named on the row instead of estimated.
    """
    result = HoursResult(
        department="stack-out",
        formula="stack out: the loader list's own board hours, as printed",
    )
    page = grid.get("ll_loader_list") or {}
    board_hours = _float(page.get("board_hours"))
    result.terms.append(f"{board_hours:g} board hour(s) off the LL page")
    if page.get("plywood_grid_present") or _float(page.get("pallet_items_quantity")):
        result.flags.append(
            "plywood and pallet items are on the loader list and are not in "
            "this figure — the governed page leaves them manual"
        )
    # The governed page states Stack Out's own rounding and says it overrides
    # the global rule; absent, the global rule stands rather than nothing.
    return _finish(result, board_hours, params,
                   decimals=params.stack_out_rounding_decimals)


# -- Steel Fab ---------------------------------------------------------------


def steel_fab_hours(grid: dict, params: Parameters) -> HoursResult:
    """`total linear feet × rate + setup + colour surcharge`, plus cribs.

    The rate is the CondenStop rate when the job's steel carries CondenStop and
    the standard rate otherwise — nearly three times as much per foot, which is
    why the flag on page C is worth a supervisor confirming and why it also
    moves the department's capacity row.

    The surcharge is charged per colour **beyond the first**, which is the
    shape of the rule; the figure is a line on the page.
    """
    result = HoursResult(
        department="steel-fab",
        formula="steel fab: linear feet × rate + setup + colour surcharge + cribs",
    )
    page = grid.get("c_steel_fab") or {}
    condenstop = bool(page.get("condenstop"))
    rate = params.steel_condenstop_lf_rate if condenstop else params.steel_lf_rate
    result.missing.extend(_need({
        ("steel fab condenstop linear foot rate" if condenstop
         else "steel fab linear foot rate"): rate,
        "steel fab setup hours": params.steel_setup_hours,
    }))
    if result.missing:
        return _finish(result, _ZERO, params)
    linear_feet = _float(page.get("total_linear_feet"))
    parts = [linear_feet * rate, params.steel_setup_hours]
    result.terms.append(
        f"{linear_feet:g} lf × {rate:g} h/ft"
        + (" (CondenStop rate)" if condenstop else "")
        + f" + {params.steel_setup_hours:g} h setup"
    )
    colours = [row for row in (page.get("colours") or []) if isinstance(row, dict)]
    if colours:
        _first, *extra = colours  # beyond the first: the rule's own shape
        if extra:
            if params.steel_colour_surcharge is None:
                result.missing.append(
                    "steel fab colour surcharge per additional colour"
                )
            else:
                parts.append(len(extra) * params.steel_colour_surcharge)
                result.terms.append(
                    f"{len(extra)} colour(s) beyond the first × "
                    f"{params.steel_colour_surcharge:g} h"
                )
    for index, crib in enumerate(page.get("cribs") or []):
        if not isinstance(crib, dict):
            continue
        size = str(crib.get("size") or "").strip().lower()
        hours = params.crib_hours.get(size)
        if hours is None:
            result.missing.append(
                f"a crib hour figure for size `{size}` (c_steel_fab.cribs[{index}])"
            )
            continue
        quantity = _float(crib.get("quantity"))
        parts.append(quantity * hours)
        result.terms.append(f"{quantity:g} × {size} crib at {hours:g} h")
    return _finish(result, math.fsum(parts) if not result.missing else _ZERO, params)


# -- Trim Pack ---------------------------------------------------------------


def trim_pack_hours(grid: dict, params: Parameters) -> HoursResult:
    """Bundles by weight, packaging by bundle, and a table of add-ons.

    A partial bundle is a bundle somebody still has to band, so the count
    rounds **up**; FJ and JJ trim cannot travel with the rest, so it adds a
    whole bundle. The packaging figure then rounds up to the governed step — a
    tenth of an hour on the page as it stands, which the page itself explains
    and flags as the one place it departs from a literal reading of the design.
    """
    result = HoursResult(
        department="trim-fab",
        formula="trim pack: bundles by weight × hours per bundle + add-ons",
    )
    result.missing.extend(_need({
        "trim pack bundle weight": params.trim_bundle_weight_lb,
        "trim pack packaging hours per bundle": params.trim_hours_per_bundle,
        "trim pack packaging rounding": params.trim_packaging_round_to,
    }))
    if result.missing:
        return _finish(result, _ZERO, params)
    page = grid.get("e_trim") or {}
    weight = _float(page.get("total_weight_lb")) or math.fsum([
        _float(row.get("weight_lb")) for row in _rows(grid, "e_trim", "rows")
    ])
    bundles = _ceil_div(weight, params.trim_bundle_weight_lb)
    result.terms.append(
        f"{weight:g} lb ÷ {params.trim_bundle_weight_lb:g} lb, rounded up = "
        f"{bundles:g} bundle(s)"
    )
    if page.get("fj_trim") or page.get("jj_trim"):
        if params.trim_extra_bundles_for_fj_jj is None:
            result.missing.append("trim pack extra bundle for FJ or JJ trim")
            return _finish(result, _ZERO, params)
        bundles = math.fsum([bundles, params.trim_extra_bundles_for_fj_jj])
        result.terms.append(
            f"FJ/JJ trim present: +{params.trim_extra_bundles_for_fj_jj:g} bundle"
        )
    packaging = _up_product(
        bundles, params.trim_hours_per_bundle, params.trim_packaging_round_to
    )
    result.terms.append(
        f"{bundles:g} × {params.trim_hours_per_bundle:g} h, rounded up to "
        f"{params.trim_packaging_round_to:g} h = {packaging:g} h"
    )
    parts = [packaging, *_trim_addons(grid, params, result)]
    return _finish(result, math.fsum(parts) if not result.missing else _ZERO, params)


def _trim_addons(grid: dict, params: Parameters, result: HoursResult) -> list[float]:
    """The governed add-on table, evaluated against the E page.

    Each add-on's *condition* is the shape of the rule and lives here; each
    add-on's *figure* is a row on the page. The keyword a description is tested
    against comes out of the add-on's own printed name — see `_acronyms`.
    """
    page = grid.get("e_trim") or {}
    rows = _rows(grid, "e_trim", "rows")
    parts: list[float] = []

    def figure(fragment: str) -> tuple[str, float | None]:
        for name, hours in params.trim_addons.items():
            if fragment.lower() in name.lower():
                return name, hours
        return fragment, None

    def missing_figure(name: str) -> None:
        result.missing.append(f"a trim pack add-on figure for `{name}`")

    for fragment in ("UCR", "OHFO"):
        name, hours = figure(fragment)
        threshold = params.trim_long_threshold_in
        keywords = _acronyms(name)
        if threshold is None or not keywords:
            continue
        hits = [
            index for index, row in enumerate(rows)
            if _float(row.get("length_in")) > threshold
            and any(word.lower() in str(row.get("description") or "").lower()
                    for word in keywords)
        ]
        if not hits:
            continue
        if hours is None:
            missing_figure(name)
            continue
        parts.append(hours)
        result.terms.append(
            f"{name}: e_trim.rows{hits} over {threshold:g} in → +{hours:g} h"
        )

    if page.get("ridge_light"):
        name, hours = figure("ridge light")
        if hours is None:
            missing_figure(name)
        else:
            parts.append(hours)
            result.terms.append(f"{name}: present → +{hours:g} h")

    vents = _float(page.get("ridge_vent_quantity"))
    if vents:
        name, hours = figure("ridge vent")
        if hours is None:
            missing_figure(name)
        else:
            parts.append(vents * hours)
            result.terms.append(f"{name}: {vents:g} × {hours:g} h")

    rolls = _float(page.get("house_wrap_rolls"))
    if rolls:
        name, hours = figure("house wrap")
        if hours is None:
            missing_figure(name)
        elif params.trim_house_wrap_cap is None:
            result.missing.append("trim pack house wrap cap")
        else:
            uncapped = rolls * hours
            capped = min(uncapped, params.trim_house_wrap_cap)
            parts.append(capped)
            result.terms.append(
                f"{name}: {rolls:g} × {hours:g} h = {uncapped:g} h, capped at "
                f"{params.trim_house_wrap_cap:g} h" if capped < uncapped
                else f"{name}: {rolls:g} × {hours:g} h"
            )

    if page.get("precut_house_wrap"):
        name, hours = figure("precut house wrap")
        if hours is None:
            missing_figure(name)
        else:
            parts.append(hours)
            result.terms.append(f"{name}: present → +{hours:g} h flat")
    return parts


# -- Slide Door --------------------------------------------------------------


def slide_door_hours(grid: dict, params: Parameters) -> HoursResult:
    """The door count is derived, not printed: end caps ÷ caps per door.

    Page F prints a track end caps quantity and the governed page says how many
    there are per door, so four caps is two doors. The base comes off a short
    ladder and each door past the ladder adds the per-door figure. The framing
    grid is excluded on purpose — it is on the page and it is not slide door
    hardware work, and counting it would double-charge whichever department
    frames the opening.
    """
    result = HoursResult(
        department=params.slide_hours_added_to,
        formula="slide door: end caps ÷ caps per door, then the door ladder",
    )
    page = grid.get("f_slide_door") or {}
    result.missing.extend(_need({
        "slide door end caps per door": params.slide_end_caps_per_door,
    }))
    if not params.slide_door_ladder:
        result.missing.append("the slide door doors→hours ladder")
    if result.missing:
        return _finish(result, _ZERO, params)
    caps = _float(page.get("track_end_caps_quantity"))
    doors = _floor_div(caps, params.slide_end_caps_per_door)
    if not doors:
        return _finish(result, _ZERO, params)
    hours, beyond = _band_hours(params.slide_door_ladder, doors)
    parts: list[float] = []
    if beyond:
        if params.slide_hours_per_additional_door is None:
            result.missing.append("slide door hours per additional door")
            return _finish(result, _ZERO, params)
        ladder = _last(params.slide_door_ladder)
        extra = doors - ladder.ceiling
        parts.append(math.fsum([
            ladder.hours, extra * params.slide_hours_per_additional_door,
        ]))
        result.terms.append(
            f"{caps:g} end cap(s) ÷ {params.slide_end_caps_per_door:g} = "
            f"{doors:g} door(s): {ladder.hours:g} h for {ladder.ceiling:g} plus "
            f"{extra:g} × {params.slide_hours_per_additional_door:g} h"
        )
    else:
        parts.append(hours)
        result.terms.append(
            f"{caps:g} end cap(s) ÷ {params.slide_end_caps_per_door:g} = "
            f"{doors:g} door(s) → {hours:g} h from the ladder"
        )
    if page.get("pull_plate"):
        if params.slide_pull_plate_hours is None:
            result.missing.append("slide door pull plate hours")
        else:
            parts.append(params.slide_pull_plate_hours)
            result.terms.append(f"pull plate: +{params.slide_pull_plate_hours:g} h")
    if page.get("non_wick_trim_grid"):
        if params.slide_non_wick_repackaging_hours is None:
            result.missing.append("slide door non-wick trim repackaging hours")
        else:
            parts.append(params.slide_non_wick_repackaging_hours)
            result.terms.append(
                "a non-Wick trim grid is on the ticket: "
                f"+{params.slide_non_wick_repackaging_hours:g} h repackaging"
            )
    if page.get("framing_grid"):
        result.flags.append(
            "the slide door framing grid is on the page and is excluded by the "
            "governed formula — it is not slide door hardware work"
        )
    return _finish(result, math.fsum(parts) if not result.missing else _ZERO, params)


# -- Hardware Pack -----------------------------------------------------------


def hardware_pack_hours(grid: dict, params: Parameters) -> HoursResult:
    """A base from the building's wings, then the line items on page D.

    Both wing dimensions come off the job record rather than off the shipping
    list — which is why a job with no wings still has a base of the setup
    figure alone — so the flow hands them in on the grid as `wings_total_ft`.
    """
    result = HoursResult(
        department="hardware-trim-pack",
        formula="hardware pack: wings × rate + setup + line items",
    )
    result.missing.extend(_need({
        "hardware pack wings rate per foot": params.hardware_wings_rate_per_foot,
        "hardware pack setup hours": params.hardware_setup_hours,
        "hardware pack hours per line item": params.hardware_hours_per_line_item,
    }))
    if result.missing:
        return _finish(result, _ZERO, params)
    wings = _float(grid.get("wings_total_ft"))
    parts = [
        wings * params.hardware_wings_rate_per_foot, params.hardware_setup_hours,
    ]
    result.terms.append(
        f"{wings:g} ft of wings × {params.hardware_wings_rate_per_foot:g} + "
        f"{params.hardware_setup_hours:g} h setup"
    )
    page = grid.get("d_hardware") or {}
    descriptions = [
        str(row.get("description") or "")
        for row in _rows(grid, "d_hardware", "rows")
    ]
    for item, hours in params.hardware_line_items.items():
        if any(_mentions(text, item) for text in descriptions):
            parts.append(hours)
            result.terms.append(f"{item}: present → +{hours:g} h")

    bags = _float(page.get("concrete_mix_bags"))
    if bags:
        result.missing.extend(_need({
            "hardware pack concrete mix hours per two bags":
                params.concrete_hours_per_increment,
            "the concrete mix bag increment": params.concrete_bags_per_increment,
            "hardware pack concrete mix cap above ten bags":
                params.concrete_cap_hours,
            "the concrete mix cap threshold": params.concrete_cap_above_bags,
        }))
        if not result.missing:
            if bags > params.concrete_cap_above_bags:
                parts.append(params.concrete_cap_hours)
                result.terms.append(
                    f"{bags:g} bag(s) of concrete mix is over "
                    f"{params.concrete_cap_above_bags:g}, so the governed cap of "
                    f"{params.concrete_cap_hours:g} h applies"
                )
            else:
                increments = bags / params.concrete_bags_per_increment
                parts.append(increments * params.concrete_hours_per_increment)
                result.terms.append(
                    f"{bags:g} bag(s) ÷ {params.concrete_bags_per_increment:g} × "
                    f"{params.concrete_hours_per_increment:g} h"
                )

    brackets = _float(page.get("l_bracket_12x5_quantity"))
    if brackets:
        result.missing.extend(_need({
            "hardware pack 12x5 l bracket hours up to 30":
                params.l_bracket_flat_hours,
            "the 12x5 L bracket flat threshold": params.l_bracket_flat_up_to,
            "hardware pack 12x5 l bracket divisor above 30": params.l_bracket_divisor,
            "hardware pack 12x5 l bracket hours per divisor":
                params.l_bracket_hours_per_divisor,
        }))
        if not result.missing:
            if brackets <= params.l_bracket_flat_up_to:
                parts.append(params.l_bracket_flat_hours)
                result.terms.append(
                    f"{brackets:g} 12x5 L bracket(s), within "
                    f"{params.l_bracket_flat_up_to:g} → "
                    f"{params.l_bracket_flat_hours:g} h flat"
                )
            else:
                divisions = brackets / params.l_bracket_divisor
                parts.append(divisions * params.l_bracket_hours_per_divisor)
                result.terms.append(
                    f"{brackets:g} 12x5 L bracket(s) ÷ "
                    f"{params.l_bracket_divisor:g} × "
                    f"{params.l_bracket_hours_per_divisor:g} h"
                )
    return _finish(result, math.fsum(parts) if not result.missing else _ZERO, params)


# -- Truss Cut, Truss Fab, Nailing and Girts --------------------------------


def truss_row_for(
    span_ft: float, members: float, standards: TrussStandards
) -> tuple[TrussRow | None, bool]:
    """The first row whose span and member ceilings the truss does not exceed.

    Past the end of the table the last row stands and the truss is **flagged**:
    the standards stop where they stop, and a truss beyond them is either a
    special or a misread span — both worth a supervisor's glance before a day
    is written on it.
    """
    for row in standards.rows:
        if span_ft <= row.span_ft and members <= row.members:
            return row, False
    return (_last(standards.rows), True) if standards.rows else (None, True)


def _truss_hours(
    grid: dict,
    params: Parameters,
    *,
    department: str,
    formula: str,
    column: str,
    multi_ply_only: bool = False,
) -> HoursResult:
    """One of the three truss columns, summed over the job's trusses."""
    result = HoursResult(department=department, formula=formula)
    standards = params.truss
    if not standards.rows:
        result.missing.append("the truss standards span/member table")
        return _finish(result, _ZERO, params, ref=standards.source_ref)
    parts: list[float] = []
    for index, row in enumerate(_rows(grid, "a1_trusses", "rows")):
        if multi_ply_only and not _multi_ply(row):
            continue
        span = _float(row.get("span_ft"))
        members = math.fsum([
            _float(row.get("top_chord_members")),
            _float(row.get("bottom_chord_members")),
            _float(row.get("web_members")),
        ])
        standard, beyond = truss_row_for(span, members, standards)
        if standard is None:
            continue
        if beyond:
            result.flags.append(
                f"a1_trusses.rows[{index}]: {span:g} ft at {members:g} member(s) "
                "is past the end of the truss standards — the last row was used "
                "and the truss is out of range"
            )
        quantity = _float(row.get("quantity"))
        hours = getattr(standard, column)
        parts.append(quantity * hours)
        result.terms.append(
            f"a1_trusses.rows[{index}]: {quantity:g} × {hours:g} h "
            f"(span ≤ {standard.span_ft:g} ft, members ≤ {standard.members:g})"
        )
    return _finish(result, math.fsum(parts), params, ref=standards.source_ref)


def _multi_ply(row: dict) -> bool:
    """More than one ply — which is what the governed pages mean by multi-ply."""
    return _float(row.get("ply")) > 1


def truss_cut_hours(grid: dict, params: Parameters) -> HoursResult:
    return _truss_hours(
        grid, params, department="truss-cut",
        formula="truss cut: the standards' cut hours, summed over the trusses",
        column="cut_hours",
    )


def truss_fab_hours(grid: dict, params: Parameters) -> HoursResult:
    return _truss_hours(
        grid, params, department="truss-fab",
        formula="truss fab: the standards' fabrication hours, summed over the trusses",
        column="fab_hours",
    )


def nailing_hours(grid: dict, params: Parameters) -> HoursResult:
    """Nailing, charged only where a truss is multi-ply, as its own row."""
    return _truss_hours(
        grid, params, department="truss-fab",
        formula="nailing: the standards' nailing hours over the multi-ply trusses",
        column="nailing_hours", multi_ply_only=True,
    )


def nailing_applies(grid: dict) -> bool:
    """The NAILING row predicate: any truss on the job is multi-ply."""
    return any(_multi_ply(row) for row in _rows(grid, "a1_trusses", "rows"))


def girts_applies(grid: dict) -> bool:
    """The GIRTS row predicate: the B page carries the plated-in note.

    A handwritten note on a printed page, which is why it is a field the
    extraction is asked about explicitly and a field a supervisor is likely to
    be asked to confirm.
    """
    return bool((grid.get("b_notes") or {}).get("girts_plated_in"))


def girts_hours(grid: dict, params: Parameters) -> HoursResult:
    """The girts row's hours: a per-truss figure off the standards page."""
    result = HoursResult(
        department="truss-fab",
        formula="girts plated in: the standards' per-truss girts figure",
    )
    standards = params.truss
    if standards.girts_hours_per_truss is None:
        result.missing.append("girts plated in, hours per truss")
        return _finish(result, _ZERO, params, ref=standards.source_ref)
    if not girts_applies(grid):
        return _finish(result, _ZERO, params, ref=standards.source_ref)
    trusses = math.fsum([
        _float(row.get("quantity")) for row in _rows(grid, "a1_trusses", "rows")
    ])
    result.terms.append(
        f"{trusses:g} truss(es) × {standards.girts_hours_per_truss:g} h girts"
    )
    return _finish(result, trusses * standards.girts_hours_per_truss, params,
                   ref=standards.source_ref)


# -- Loading -----------------------------------------------------------------


def loading_hours(grid: dict, params: Parameters) -> HoursResult:
    """The widest truss on the truck decides it — unless a roller trailer does.

    A roller trailer overrides the **table** entirely, whatever the truss
    width; the material grids still add on top, because the governed page adds
    them "on top" rather than into the ladder.
    """
    result = HoursResult(
        department="loading",
        formula="loading: the widest truss's band, or the roller trailer override",
    )
    page = grid.get("ll_loader_list") or {}
    parts: list[float] = []
    if page.get("roller_trailer"):
        if params.loading_roller_trailer_hours is None:
            result.missing.append("loading roller trailer override hours")
        else:
            parts.append(params.loading_roller_trailer_hours)
            result.terms.append(
                "a roller trailer overrides the width table: "
                f"{params.loading_roller_trailer_hours:g} h"
            )
    elif not params.loading_width_bands:
        result.missing.append("the loading truss-width band table")
    else:
        width = _float(page.get("largest_truss_width_ft"))
        hours, beyond = _band_hours(params.loading_width_bands, width)
        if not beyond:
            parts.append(hours)
            result.terms.append(f"the widest truss is {width:g} ft → {hours:g} h")
        elif params.loading_overflow_hours is None:
            result.missing.append("loading beyond the table, hours")
        else:
            parts.append(params.loading_overflow_hours)
            result.terms.append(
                f"the widest truss is {width:g} ft, past the table: "
                f"{params.loading_overflow_hours:g} h"
            )
    grids = len([
        flag for flag
        in (page.get("lumber_grid_present"), page.get("plywood_grid_present"))
        if flag
    ])
    if grids:
        if params.loading_hours_per_material_grid is None:
            result.missing.append("loading hours per material grid")
        else:
            parts.append(grids * params.loading_hours_per_material_grid)
            result.terms.append(
                f"{grids} material grid(s) × "
                f"{params.loading_hours_per_material_grid:g} h"
            )
    return _finish(result, math.fsum(parts) if not result.missing else _ZERO, params)


def loading_row_groups(tickets: Iterable[dict]) -> list[dict]:
    """Tickets sharing a truck become one row, keyed on the truck.

    Loading is the opposite shape to every other department: two tickets on one
    truck are one loading job, so they become one row keyed on the shipment
    rather than two rows keyed on the tickets. The trailer number is
    deliberately left out — which trailer goes out is the yard's call on the
    day, and writing a guess into that column would be this system making an
    operational decision.

    Pure and numberless: `combined_with` is read as an undirected relation, so
    `A combined_with B` and `B combined_with A` are one group whichever way the
    source spelled it, and a three-way consolidation is one group too.
    """
    by_number = {
        str(ticket.get("job_number") or ""): dict(ticket) for ticket in tickets
    }
    parent: dict[str, str] = {number: number for number in by_number}

    def root(number: str) -> str:
        while parent.get(number, number) != number:
            number = parent[number]
        return number

    for number, row in by_number.items():
        other = str(row.get("combined_with") or "")
        if other in by_number:
            left, right = root(number), root(other)
            if left != right:
                parent[left] = right

    grouped: dict[str, list[dict]] = {}
    for number in by_number:
        grouped.setdefault(root(number), []).append(by_number[number])
    return [
        {
            "truck_key": key,
            "job_numbers": [
                str(row.get("job_number") or "")
                for row in sorted(grouped[key],
                                  key=lambda row: str(row.get("job_number") or ""))
            ],
            "consolidated": len(grouped[key]) > 1,
            "hours": math.fsum([_float(row.get("hours")) for row in grouped[key]]),
        }
        for key in sorted(grouped)
    ]


# -- the incentive -----------------------------------------------------------


def incentive(hours: float, params: Incentive) -> float:
    """`start + rate × hours` — the bonus cell, as arithmetic.

    Zero when the page carries neither figure, which is the honest answer: an
    invented curve would put a number this module holds in front of a
    supervisor as though the plant had set it.
    """
    if params.start_usd is None or params.rate_usd_per_hour is None:
        return _ZERO
    return math.fsum([params.start_usd, params.rate_usd_per_hour * hours])


def incentive_sentence(hours: float, params: Incentive) -> str:
    """The figure stated as its own arithmetic, for the approval Task.

    `$-180.00 + $6.20/h × 29.0 h = $+0.80` — which is what makes the
    out-of-the-hole indicator auditable instead of a cell nobody can check.
    """
    if params.start_usd is None or params.rate_usd_per_hour is None:
        return (
            "no incentive figures are on the governed page for "
            f"`{params.department or 'this department'}`"
        )
    return (
        f"${params.start_usd:,.2f} + ${params.rate_usd_per_hour:,.2f}/h × "
        f"{hours:,.1f} h = ${incentive(hours, params):+,.2f}"
    )


def crosses_zero_at(params: Incentive) -> float | None:
    """Where the figure crosses zero — the plant's "out of the hole" floor.

    `−start ÷ rate`, computed rather than read: the governed table prints it in
    a fourth column so the pair can be checked at a glance, and reading that
    column would give the flow two answers to one question.
    """
    if params.start_usd is None or not params.rate_usd_per_hour:
        return None
    return -params.start_usd / params.rate_usd_per_hour


# -- the finish --------------------------------------------------------------


def _finish(
    result: HoursResult,
    total: float,
    params: Parameters,
    *,
    decimals: int | None = None,
    ref: str = "",
) -> HoursResult:
    """Round by the governed rule, and write the basis sentence.

    The basis is the formula's name plus the revision of the page it was read
    from, which is what a schedule row carries and what makes "why 27.4 hours"
    answerable with a clause.
    """
    chosen = params.rounding_decimals if decimals is None else decimals
    result.hours = _rounded(total, chosen)
    result.basis = f"{result.formula} · {ref or params.source_ref}"
    if not params.found_asset:
        result.basis += " — the page is not in the corpus"
    return result


def summarize(results: Iterable[HoursResult]) -> str:
    """The `check-labor-hours` step's journal line."""
    rows = [row for row in results if row.department]
    if not rows:
        return "no page family a governed formula reads is present"
    missing = sorted({name for row in rows for name in row.missing})
    return " · ".join(
        f"{row.department} {row.hours:g} h" for row in rows
    ) + (
        f" — governed value(s) missing: {'; '.join(missing)}" if missing else ""
    )
