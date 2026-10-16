"""`plant-scheduling-parameters`, read once — the whole page, for every lane.

One governed page, several audiences, and until this module existed each of
them was growing a parser for it: the jobs feed needed `## Source vocabulary`,
the eligibility sweep needs the clauses and the calendar, the extraction needs
the page-family map and the confidence floors, and the scheduling flow needs
capacity, headcount, lead times and sequencing. Three copies of a markdown
reader over one document is three chances to disagree about what Saturday is.

So this is the reader, and `parse_plant_parameters(content, ref)` is the whole
public surface. What it returns:

| attribute | the page's section | who reads it |
|---|---|---|
| `eligibility` | `## Eligibility` (+ the vocabulary it resolves against) | the eligibility sweep |
| `calendar` | `## Business days and holidays` | everybody who counts a day |
| `departments` | `## Departments` | everybody |
| `vocabulary` | `## Source vocabulary`, via `connectors.production` | the feed, and the clauses |
| `documents` | `## Source documents` | the extraction |
| `page_families` | `## Page families` | the extraction, the scheduling fill |
| `thresholds` | `## Field verdicts` | the extraction |
| `default_band` / `bands` | `## Expected hours` | the extraction's out-of-band check |
| `capacity` | `## Capacity` | the scheduling fill |
| `headcount` | `## Headcount` | the scheduling fill |
| `lead_times` | `## Lead times and farthest out` | the sweep's target day, the fill |
| `sequencing` | `## Sequencing` | the fill's dependency rule |
| `row_shaping` | `## Row shaping` | the fill's row kinds |
| `labels` | `## Job label abbreviations` | the fill's column B |
| `incentives` | `## Incentive` | the fill, and the approval Task |
| `approval` | `## Approval routing`, via `routing.parse_routing` | every plant gate |

Three house properties, all deliberate:

- **Section-scoped, always.** The page carries, under its `## Open` heading,
  the interim rule for the four departments whose figures Chris Robson still
  owes — including a quoted `- goal max: —`. A parser matching document-wide
  would read that quotation as a capacity row. Every read below goes through
  `routing.section`, which is what makes that structural rather than careful.
- **Tolerant, one value at a time.** This is a page a plant superintendent
  edits in the console. A missing line leaves its field empty or `None` and
  the caller degrades on that one value; a re-wrapped paragraph cannot take a
  schedule down.
- **A fallback the run says it took.** `found_asset` is what a step reports, so
  a run using built-in behaviour instead of the plant's says so in the
  journal rather than looking like a run that read the page.

**Two things on this page are deliberately not parsed here.** The vocabulary
table is read by `connectors.production.parse_source_vocabulary` — the jobs
feed's own reader, kept because it is the *landing* direction of the same table
and because one reader is the point — and this module calls it rather than
copying it. And the incentive's arithmetic lives in `formulas.py`, next door:
the two numbers are read here, and `formulas.incentive` is what turns them into
a figure.
"""

from __future__ import annotations

import calendar
import logging
import re
from datetime import date, timedelta

from pydantic import BaseModel, Field

from ... import routing
from ...connectors.production import SourceVocabulary, parse_source_vocabulary
from ...desks import PLANT_SUPERVISOR
from .formulas import Incentive

log = logging.getLogger("cumulus.plant")

POLICY_ASSET_ID = "plant-scheduling-parameters"

#: Weekday name → `date.weekday()`, off the standard library rather than a
#: table of our own: the governed page writes `Monday, Tuesday, …`, and this is
#: the one place those words become an index.
_WEEKDAYS: dict[str, int] = {
    name.lower(): index for index, name in enumerate(calendar.day_name)
}

#: What the eligibility clause means when it names no date. The page as it
#: stands says `load date on or after: today`, and "today" is the run's own day
#: rather than a value this module holds.
TODAY = "today"

#: How a source spells "the flag is set". The `## Eligibility` clauses name a
#: source *field* in prose — `release required: out to yard` — and the
#: vocabulary table maps that field's two picklist words onto facts; this is
#: how the affirmative row is found without assuming the ERP says `Yes`. A
#: plant whose system says `Y`/`N` is already covered.
_AFFIRMATIVE: tuple[str, ...] = ("yes", "y", "true", "1", "on", "set")

#: How the page writes "there is no figure here". `—` in a goal-max column
#: means no target; `none` in an absolute-max column means no ceiling. Both
#: read as absent, and which absence it is depends on the column.
_ABSENT: frozenset[str] = frozenset(
    {"", "-", "--", "—", "–", "none", "n/a", "tbd"}
)


# ---------------------------------------------------------------------------
# The parsed shapes
# ---------------------------------------------------------------------------


class Calendar(BaseModel):
    """The plant's own working year: which weekdays, and which days off.

    Every "next business day", lead time and farthest-out window on the
    governed page counts business days using exactly these two lines. The
    original design names no calendar at all, so the page freezes one and says
    so — which means a wrong holiday shows up as a schedule written for a day
    nobody was in the building, and is fixed by editing a line.
    """

    business_weekdays: list[int] = Field(default_factory=list)
    holidays: list[str] = Field(default_factory=list)

    def is_business_day(self, day: date) -> bool:
        if not self.business_weekdays:
            # No governed calendar is not "every day is a working day": it is a
            # page nobody seeded, and the caller learns that from
            # `PlantParameters.found_asset` rather than from a silent yes.
            return True
        return (
            day.weekday() in self.business_weekdays
            and day.isoformat() not in set(self.holidays)
        )

    def why_not_a_business_day(self, day: date) -> str:
        """The named reason, so an exclusion reads as a clause and not a verdict."""
        if day.isoformat() in set(self.holidays):
            return f"{day.isoformat()} is a plant holiday on the governed calendar"
        if self.business_weekdays and day.weekday() not in self.business_weekdays:
            named = ", ".join(
                calendar.day_name[index] for index in sorted(self.business_weekdays)
            )
            return (
                f"{day.isoformat()} is a {calendar.day_name[day.weekday()]}, and "
                f"the plant's business days are {named}"
            )
        return ""

    def next_business_day(self, day: date) -> date:
        cursor = day + timedelta(days=1)
        for _ in range(len(calendar.day_name) * 2):
            if self.is_business_day(cursor):
                return cursor
            cursor += timedelta(days=1)
        return cursor

    def business_days_before(self, day: date, count: int) -> date:
        """`count` business days back from `day`, counting the days worked.

        A department's lead time and its farthest-out window are both stated on
        the page in business days back from a load date, so this is what turns
        one into a date. Bounded rather than `while True`: a calendar with no
        business weekdays would otherwise walk backwards forever, and a
        governed page can say anything.
        """
        cursor = day
        stepped = 0
        for _ in range(max(count, 0) * len(calendar.day_name) * 2):
            if stepped >= count:
                break
            cursor -= timedelta(days=1)
            if self.is_business_day(cursor):
                stepped += 1
        return cursor


class Eligibility(BaseModel):
    """The first-pass clauses, exactly as the page lists them.

    A job must clear **all** of them. A job that fails one is excluded for that
    named reason and the reason is recorded, so "why was 260118 not scheduled"
    has an answer that is a clause rather than an opinion.
    """

    load_date_on_or_after: str = ""
    week_positions: list[int] = Field(default_factory=list)
    #: The source field the page names as the release flag — `out to yard` —
    #: resolved against the vocabulary table into `released_state` below.
    release_required: str = ""
    hold_excludes: str = ""
    business_days_only: bool = True
    sort_by: str = ""
    #: `None` when the page carries no bound at all, which is a different fact
    #: from a bound of zero: zero is a plant saying "start nothing this
    #: window", and absent is a page that never mentioned the cost control.
    jobs_per_window: int | None = None
    empty_set_is: str = ""

    #: The `release_state` value that counts as released, and the `hold_reason`
    #: value the page excludes — both read off the governed vocabulary, so no
    #: flow holds the customer's words.
    released_state: str = ""
    excluding_hold: str = ""


class Department(BaseModel):
    """One department in the three spellings that exist.

    The workforce system's name, the job system's own Done checkbox, and the
    slug this platform uses everywhere. Neither system's spelling is written
    into any code: this table is the only place the three are tied together.
    """

    hris_name: str = ""
    erp_done_field: str = ""
    slug: str = ""


class PageFamily(BaseModel):
    """One page family of the shipping list, and the departments it feeds.

    "Absent" is judged per page family rather than per department, because
    Hardware & Trim Packaging is fed by three of them and Truss Fabrication by
    two: a ticket carrying a hardware grid on page D but no trim on page E
    still has hardware work to do.
    """

    page: str = ""
    carries: str = ""
    departments: list[str] = Field(default_factory=list)


class HourBand(BaseModel):
    """A department's expected-hours band — the out-of-range guard."""

    low: float
    high: float


class LeadTime(BaseModel):
    """Where a department's work is normally placed, and the earliest it may be.

    Both counted in business days back from the load date. `farthest_out_days`
    of `None` is the page's `none` — there is no earliest.
    """

    department: str = ""
    condition: str = ""
    lead_time_days: int = 0
    farthest_out_days: int | None = None
    note: str = ""


class CapacityRow(BaseModel):
    """How many hours a department will take in one day, on one condition.

    `goal_max` of `None` is the page's `—`: no target, so the fill runs to the
    absolute max or to the demand and the run journals which clause it used.
    `absolute_max` of `None` is the page's `none`: no ceiling at all.
    `min_hours` is `None` where the page states the minimum as money rather
    than hours — "out of the hole", which `min_rule` carries verbatim.
    """

    department: str = ""
    row: str = ""
    min_hours: float | None = None
    min_rule: str = ""
    goal_max: float | None = None
    absolute_max: float | None = None
    fudge: float | None = None
    note: str = ""


class Capacity(BaseModel):
    """The capacity table and the four clauses that say how to read it."""

    rows: list[CapacityRow] = Field(default_factory=list)
    fudge_applies_to: str = ""
    exceeding_absolute_max_within_fudge: str = ""
    minimum_out_of_the_hole: str = ""
    below_capacity: str = ""
    supervisor_placed_rows: str = ""

    def row_for(self, department: str, condition: str = "") -> CapacityRow | None:
        """This department's row for a condition, preferring an exact match.

        `any` is the row that answers when nothing more specific does, which is
        most departments. **Which row was chosen is recorded on every schedule
        day**, because it is the first number a supervisor will dispute.
        """
        rows = [row for row in self.rows if row.department == department]
        wanted = (condition or "").strip().lower()
        if wanted:
            for row in rows:
                if row.row == wanted:
                    return row
        return next((row for row in rows if row.row in ("any", "")), None)


class HeadcountRung(BaseModel):
    """One rung of the headcount ladder: a crew size, a row, and its ceiling.

    `employees_up_to` of `None` is the page's `5 or more` — the top rung, with
    no ceiling above it.
    """

    employees_up_to: float | None = None
    row: str = ""
    capacity_hours: float | None = None


class Headcount(BaseModel):
    """Where the crew count comes from, how fresh it has to be, and the ladder.

    The design had a supervisor typing today's crew into a spreadsheet. The
    workforce system is the crew list here, so the counting problem is gone and
    only feed lag remains — which is why `stale` is a warning on the approval
    Task and not a failure.
    """

    source: str = ""
    freshness_hours: float | None = None
    stale: str = ""
    ladder: list[HeadcountRung] = Field(default_factory=list)

    def rung_for(self, employees: float) -> HeadcountRung | None:
        for rung in self.ladder:
            if rung.employees_up_to is None or employees <= rung.employees_up_to:
                return rung
        return self.ladder[-1] if self.ladder else None


class SequencingRow(BaseModel):
    """One department's place in the fixed daily order, and what it waits for."""

    order: int = 0
    department: str = ""
    upstream_done_required: str = ""
    offset: str = ""
    note: str = ""


class Sequencing(BaseModel):
    """The fixed daily order, and the two clauses that qualify it."""

    rows: list[SequencingRow] = Field(default_factory=list)
    unmet_upstream_dependency: str = ""
    do_not_schedule_when: str = ""

    def upstream_for(self, department: str) -> str:
        row = next(
            (row for row in self.rows if row.department == department), None
        )
        return row.upstream_done_required if row else ""


class RowShaping(BaseModel):
    """A department-day is a list of rows, and a job is not always one of them."""

    row_kinds: list[str] = Field(default_factory=list)
    nailing_row_when: str = ""
    girts_row_when: str = ""
    #: The handwritten phrase the girts clause quotes, pulled out of the clause
    #: so the extraction can be asked about exactly that note.
    girts_note: str = ""
    loading_consolidates: str = ""
    loading_row_key: str = ""
    loading_trailer_number: str = ""


class LabelRule(BaseModel):
    """One department's suffix for column B of its day."""

    department: str = ""
    suffix: str = ""
    extra: str = ""


class Labels(BaseModel):
    """The short, legible name a schedule row calls a job."""

    rules: list[LabelRule] = Field(default_factory=list)
    max_length: int | None = None

    def suffix_for(self, department: str) -> str:
        row = next(
            (row for row in self.rules if row.department == department), None
        )
        return row.suffix if row else ""


class Thresholds(BaseModel):
    """What a supervisor is asked about, rather than handed a seven-page PDF."""

    min_extraction_confidence: float = 0.0
    min_field_confidence: float = 0.0
    grid_line_total_tolerance_pct: float = 0.0
    linear_footage_tolerance_pct: float = 0.0
    required_field_rule: str = ""


class SourceDocuments(BaseModel):
    """Where the floor's own paperwork is filed, and what it is called."""

    folder_root: str = ""
    plant_sites: list[str] = Field(default_factory=list)
    shipping_list_pattern: str = ""
    cut_sheet_pattern: str = ""
    missing_shipping_list: str = ""

    def folder_for(self, plant_site: str, job_number: str) -> str:
        """The governed root with this job's two values substituted.

        Placeholder substitution rather than an f-string of our own, because
        the shape of the tree is the customer's: a plant that files by year
        edits the root and nothing here changes.
        """
        return (
            self.folder_root
            .replace("<plant site>", plant_site)
            .replace("<job number>", job_number)
        )

    def matches(self, pattern: str, filename: str, job_number: str) -> bool:
        """Whether this filename is the file the governed pattern names.

        The page writes patterns the way a person writes them —
        `<job number> Shipping List*.pdf` — so the placeholder is filled and
        the `*` is the only wildcard honoured. Anything else in the pattern is
        matched literally, which is what stops a stray `.` or `(` in a
        customer's convention from becoming a regular expression nobody
        intended.
        """
        if not pattern:
            return False
        filled = pattern.replace("<job number>", job_number)
        expression = "".join(
            ".*" if part == "*" else re.escape(part)
            for part in re.split(r"(\*)", filled)
        )
        return bool(re.fullmatch(expression, (filename or "").strip(),
                                 re.IGNORECASE))


class PlantParameters(BaseModel):
    """Everything the page says, at one revision.

    One model and one step, for `accounts_payable/v9`'s reason: a run that read
    the same asset from five places could name two revisions of it if somebody
    republished mid-flight, and a reader of the map could not tell what the run
    consults.
    """

    eligibility: Eligibility = Field(default_factory=Eligibility)
    calendar: Calendar = Field(default_factory=Calendar)
    departments: list[Department] = Field(default_factory=list)
    vocabulary: SourceVocabulary = Field(default_factory=SourceVocabulary)
    page_families: list[PageFamily] = Field(default_factory=list)
    documents: SourceDocuments = Field(default_factory=SourceDocuments)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    default_band: HourBand | None = None
    bands: dict[str, HourBand] = Field(default_factory=dict)
    capacity: Capacity = Field(default_factory=Capacity)
    headcount: Headcount = Field(default_factory=Headcount)
    lead_times: list[LeadTime] = Field(default_factory=list)
    sequencing: Sequencing = Field(default_factory=Sequencing)
    row_shaping: RowShaping = Field(default_factory=RowShaping)
    labels: Labels = Field(default_factory=Labels)
    incentives: dict[str, Incentive] = Field(default_factory=dict)
    incentive_model: str = ""
    incentive_decides_the_fill: list[str] = Field(default_factory=list)
    approval: routing.RoutingPolicy = Field(default_factory=routing.RoutingPolicy)
    source_ref: str = f"knowledge:{POLICY_ASSET_ID}"
    found_asset: bool = False

    # -- the reads a flow actually makes -----------------------------------

    @property
    def slugs(self) -> list[str]:
        return [row.slug for row in self.departments if row.slug]

    def band_for(self, department: str) -> HourBand | None:
        """This department's band, or the page's default, or nothing.

        Nothing is a real answer and is reported as one: a band nobody set is
        not a band of zero to infinity, and a check that invented one would
        pass every figure.
        """
        return self.bands.get(department) or self.default_band

    def families_for(self, department: str) -> list[str]:
        return [
            family.page for family in self.page_families
            if department in family.departments
        ]

    def departments_for(self, page: str) -> list[str]:
        for family in self.page_families:
            if family.page.lower() == (page or "").lower():
                return list(family.departments)
        return []

    def lead_time_for(self, department: str, condition: str = "") -> LeadTime | None:
        """The row for this department, preferring one whose condition matches.

        `any` in the page's condition column is the row that answers when
        nothing more specific does — which is most departments, most days.
        `None` when the page conditions this department's lead time and the
        caller does not know the condition: Laminates is `treated` or
        `non-treated` and nothing else, and picking one of those silently would
        put treated stock on a two-day window.
        """
        rows = [row for row in self.lead_times if row.department == department]
        wanted = (condition or "").strip().lower()
        if wanted:
            for row in rows:
                if row.condition == wanted:
                    return row
        return next((row for row in rows if row.condition in ("any", "")), None)

    def nearest_lead_time_for(self, department: str) -> LeadTime | None:
        """The **shortest** lead time the page allows this department.

        For a caller that has to place a day before the condition is known.
        Shortest and not longest, deliberately: a lead time is where work is
        normally placed and the farthest-out window is the earliest it may be
        placed at all, so the nearest row is the one placement no row forbids.
        A caller using this instead of `lead_time_for` is expected to say so —
        the sweep puts it on the day it opens.
        """
        rows = [row for row in self.lead_times if row.department == department]
        return min(rows, key=lambda row: row.lead_time_days) if rows else None

    def incentive_for(self, department: str) -> Incentive:
        """This department's two figures, or an empty pair that says so.

        Never `None`: `formulas.incentive` answers zero for an unset pair and
        `formulas.incentive_sentence` says out loud that the page carries no
        figures, which is better than a caller having to guard.
        """
        return self.incentives.get(department) or Incentive(
            department=department, source_ref=self.source_ref
        )

    def desk_for(self, total_hours: float) -> routing.ResolvedQueue:
        """The desk a plant review lands on, off the governed approval table.

        Pure: the table was read at the flow's one knowledge step and this is a
        function over values already in hand, which is what lets it be called
        where a gate is raised without a Rule fetching anything.

        The one outcome that must never come out of here is an empty queue: a
        Task on no desk is work that exists and that nobody will ever see.
        """
        target = self.approval.target_for(total_hours)
        desk = target or self.approval.fallback or PLANT_SUPERVISOR
        return routing.ResolvedQueue(
            queue=desk,
            target=target,
            explanation=(
                f"routed to the `{desk}` desk by the governed approval table, "
                f"for a review of {total_hours:,.1f} h"
                if self.approval.found_asset else
                f"no approval table is on the governed page, so this review "
                f"fell back to the `{desk}` desk"
            ),
            fell_back=not self.approval.found_asset,
        )


# ---------------------------------------------------------------------------
# The parse
# ---------------------------------------------------------------------------


def _text(text: str, key: str) -> str:
    match = re.search(
        rf"^\s*[-*]\s*{re.escape(key)}:?\s*(?P<value>.+?)\s*$",
        text or "", re.IGNORECASE | re.MULTILINE,
    )
    return match.group("value").strip().strip("`*_") if match else ""


def _number(text: str, key: str) -> float | None:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", _text(text, key))
    return float(match.group(0)) if match else None


def _items(text: str, key: str) -> list[str]:
    return [
        part.strip().strip("`")
        for part in _text(text, key).split(",") if part.strip()
    ]


def _optional_int(text: str, key: str) -> int | None:
    """A whole number off a `- key: value` line, or None when the line is absent."""
    value = _number(text, key)
    return int(value) if value is not None else None


def _yes(text: str, key: str) -> bool:
    return _text(text, key).strip().lower().startswith("yes")


def _table(text: str) -> list[list[str]]:
    """The data rows of the first markdown table in this text.

    The header goes by shape rather than by position: the row whose cells are
    all dashes is the rule, and everything before it was the header. A second
    table in the same section is a different table and is not read — which is
    why each caller passes the section that owns the one it wants.
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


def _cell_number(cell: str) -> float | None:
    """The first number in a table cell, or None for `—`, `none` and prose."""
    if (cell or "").strip().lower() in _ABSENT:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", cell or "")
    return float(match.group(0)) if match else None


def _last_number(cell: str) -> float | None:
    """The **last** number in a cell — `1 to 3` is a ceiling of three."""
    if (cell or "").strip().lower() in _ABSENT:
        return None
    found = re.findall(r"[-+]?\d+(?:\.\d+)?", cell or "")
    return float(found[-1]) if found else None


def _slugs(cell: str) -> list[str]:
    return [part.strip().lower() for part in (cell or "").split(",") if part.strip()]


def parse_plant_parameters(content: str, ref: str) -> PlantParameters:
    """Read the page, section by section. The module's whole public surface."""
    params = PlantParameters(
        source_ref=ref or f"knowledge:{POLICY_ASSET_ID}",
        found_asset=bool(content),
    )
    if not content:
        log.warning(
            "Plant scheduling parameters missing; every caller degrades and "
            "the run says so",
            extra={"assetId": POLICY_ASSET_ID},
        )

    # -- the calendar every other section counts on ------------------------
    days = routing.section(content, "Business days and holidays")
    params.calendar = Calendar(
        business_weekdays=sorted({
            _WEEKDAYS[name.lower()] for name in _items(days, "business days")
            if name.lower() in _WEEKDAYS
        }),
        holidays=[
            value for value in _items(days, "holidays")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
        ],
    )

    # -- the source's own words becoming facts, through the feed's reader --
    params.vocabulary = parse_source_vocabulary(content)
    params.vocabulary.source_ref = params.source_ref

    # -- the first-pass clauses --------------------------------------------
    section = routing.section(content, "Eligibility")
    params.eligibility = Eligibility(
        load_date_on_or_after=_text(section, "load date on or after") or TODAY,
        week_positions=[
            int(value) for value in _items(section, "eligible week positions")
            if value.isdigit()
        ],
        release_required=_text(section, "release required"),
        hold_excludes=_text(section, "hold excludes"),
        business_days_only=_yes(section, "business days only"),
        sort_by=_text(section, "sort candidates by"),
        jobs_per_window=_optional_int(section, "jobs per eligibility window"),
        empty_set_is=_text(section, "empty eligible set"),
        # The clauses name a source *field* in prose and the vocabulary table
        # says what its affirmative word becomes. Resolving it here rather than
        # in the Rule is the same split `v9`'s routing has: the transform
        # fetches and reads, and what the Rule receives is a value it can
        # compare.
        released_state=_affirmative(params.vocabulary.release_state),
        excluding_hold=_affirmative(params.vocabulary.hold_reason),
    )

    # -- the three spellings of a department -------------------------------
    for cells in _table(routing.section(content, "Departments")):
        if len(cells) < len(Department.model_fields):
            continue
        hris, done, slug, *_rest = cells
        if slug:
            params.departments.append(Department(
                hris_name=hris, erp_done_field=done, slug=slug.lower(),
            ))

    # -- where the floor's paperwork lives ---------------------------------
    documents = routing.section(content, "Source documents")
    params.documents = SourceDocuments(
        folder_root=_text(documents, "cut sheet folder root"),
        plant_sites=_items(documents, "plant sites"),
        shipping_list_pattern=_text(documents, "shipping list filename pattern"),
        cut_sheet_pattern=_text(documents, "column cut sheet filename pattern"),
        missing_shipping_list=_text(documents, "missing shipping list"),
    )

    # -- which page feeds which department ---------------------------------
    for cells in _table(routing.section(content, "Page families")):
        if len(cells) < len(PageFamily.model_fields):
            continue
        page, carries, departments, *_rest = cells
        if page:
            params.page_families.append(PageFamily(
                page=page, carries=carries, departments=_slugs(departments),
            ))

    # -- the floors and the two arithmetic tolerances ----------------------
    verdicts = routing.section(content, "Field verdicts")
    params.thresholds = Thresholds(
        min_extraction_confidence=(
            _number(verdicts, "minimum extraction confidence") or 0.0),
        min_field_confidence=_number(verdicts, "minimum field confidence") or 0.0,
        grid_line_total_tolerance_pct=(
            _number(verdicts, "grid line total tolerance") or 0.0),
        linear_footage_tolerance_pct=(
            _number(verdicts, "linear footage tolerance") or 0.0),
        required_field_rule=_text(verdicts, "required field rule"),
    )

    # -- the out-of-range guard --------------------------------------------
    expected = routing.section(content, "Expected hours")
    default = re.search(
        r"default expected hours band:?\s*(?P<low>[\d.]+)\s*to\s*(?P<high>[\d.]+)",
        expected, re.IGNORECASE,
    )
    if default:
        params.default_band = HourBand(
            low=float(default.group("low")), high=float(default.group("high"))
        )
    for cells in _table(expected):
        if len(cells) < len(("department", "low", "high")):
            continue
        name, raw_low, raw_high, *_rest = cells
        low, high = _cell_number(raw_low), _cell_number(raw_high)
        if name and low is not None and high is not None:
            params.bands[name.lower()] = HourBand(low=low, high=high)

    # -- how many hours a department will take in a day --------------------
    section = routing.section(content, "Capacity")
    capacity = Capacity(
        fudge_applies_to=_text(section, "fudge applies to"),
        exceeding_absolute_max_within_fudge=_text(
            section, "exceeding the absolute max within the fudge"),
        minimum_out_of_the_hole=_text(section, "minimum out of the hole"),
        below_capacity=_text(section, "below capacity"),
        supervisor_placed_rows=_text(section, "supervisor-placed rows"),
    )
    for cells in _table(section):
        if len(cells) < len(CapacityRow.model_fields) - len(("min_rule",)):
            continue
        name, row, raw_min, raw_goal, raw_abs, raw_fudge, *rest = cells
        if not name:
            continue
        capacity.rows.append(CapacityRow(
            department=name.lower(), row=row.lower(),
            min_hours=_cell_number(raw_min), min_rule=raw_min,
            goal_max=_cell_number(raw_goal),
            absolute_max=_cell_number(raw_abs),
            fudge=_cell_number(raw_fudge),
            note=(rest[0] if rest else ""),
        ))
    params.capacity = capacity

    # -- the crew count, and the ladder it climbs --------------------------
    section = routing.section(content, "Headcount")
    headcount = Headcount(
        source=_text(section, "headcount source"),
        freshness_hours=_number(section, "headcount freshness window"),
        stale=_text(section, "stale headcount"),
    )
    for cells in _table(section):
        if len(cells) < len(HeadcountRung.model_fields):
            continue
        raw_present, row, raw_hours, *_rest = cells
        if not row:
            continue
        headcount.ladder.append(HeadcountRung(
            # `5 or more` is the top rung: a ceiling read off the last number
            # in the cell would say five, so an open-ended rung is None.
            employees_up_to=(
                None if re.search(r"or more|\+|and (up|above|over)",
                                  raw_present, re.IGNORECASE)
                else _last_number(raw_present)
            ),
            row=row.lower(),
            capacity_hours=_cell_number(raw_hours),
        ))
    params.headcount = headcount

    # -- how far ahead of a load date the work is placed -------------------
    for cells in _table(routing.section(content, "Lead times and farthest out")):
        if len(cells) < len(LeadTime.model_fields):
            continue
        name, condition, raw_lead, raw_farthest, *rest = cells
        lead = _cell_number(raw_lead)
        if not name or lead is None:
            continue
        farthest = _cell_number(raw_farthest)
        params.lead_times.append(LeadTime(
            department=name.lower(), condition=condition.lower(),
            lead_time_days=int(lead),
            farthest_out_days=int(farthest) if farthest is not None else None,
            note=(rest[0] if rest else ""),
        ))

    # -- the fixed daily order ---------------------------------------------
    section = routing.section(content, "Sequencing")
    sequencing = Sequencing(
        unmet_upstream_dependency=_text(section, "unmet upstream dependency"),
        do_not_schedule_when=_text(section, "do not schedule when"),
    )
    for cells in _table(section):
        if len(cells) < len(SequencingRow.model_fields):
            continue
        raw_order, name, upstream, offset, *rest = cells
        order = _cell_number(raw_order)
        if not name or order is None:
            continue
        sequencing.rows.append(SequencingRow(
            order=int(order), department=name.lower(),
            upstream_done_required=(
                "" if upstream.strip().lower() in _ABSENT else upstream.lower()
            ),
            offset=("" if offset.strip().lower() in _ABSENT else offset),
            note=(rest[0] if rest else ""),
        ))
    params.sequencing = sequencing

    # -- the four row kinds and the conditions that raise them -------------
    section = routing.section(content, "Row shaping")
    girts_when = _text(section, "girts row when")
    quoted = re.search(r"[\"“](?P<note>[^\"”]+)[\"”]", girts_when)
    params.row_shaping = RowShaping(
        row_kinds=_items(section, "row kinds"),
        nailing_row_when=_text(section, "nailing row when"),
        girts_row_when=girts_when,
        girts_note=quoted.group("note") if quoted else girts_when,
        loading_consolidates=_text(section, "loading consolidates"),
        loading_row_key=_text(section, "loading row key"),
        loading_trailer_number=_text(section, "loading trailer number"),
    )

    # -- what a schedule row calls a job -----------------------------------
    section = routing.section(content, "Job label abbreviations")
    labels = Labels(max_length=(
        int(_number(section, "job label max length"))
        if _number(section, "job label max length") is not None else None
    ))
    for cells in _table(section):
        if len(cells) < len(LabelRule.model_fields):
            continue
        name, suffix, extra, *_rest = cells
        if name and suffix:
            labels.rules.append(LabelRule(
                department=name.lower(), suffix=suffix, extra=extra,
            ))
    params.labels = labels

    # -- the bonus cell, as two governed numbers per department ------------
    section = routing.section(content, "Incentive")
    params.incentive_model = _text(section, "incentive model")
    params.incentive_decides_the_fill = _slugs(
        _text(section, "incentive decides the fill")
    )
    for cells in _table(section):
        if len(cells) < len(("department", "start", "rate")):
            continue
        name, raw_start, raw_rate, *_rest = cells
        start, rate = _cell_number(raw_start), _cell_number(raw_rate)
        if not name or start is None or rate is None:
            continue
        params.incentives[name.lower()] = Incentive(
            department=name.lower(), start_usd=start, rate_usd_per_hour=rate,
            source_ref=params.source_ref, found_asset=True,
        )

    # -- whose desk a plant review lands on --------------------------------
    params.approval = routing.parse_routing(
        content, default_fallback=PLANT_SUPERVISOR
    )
    params.approval.source_ref = params.source_ref
    return params


def read_plant_parameters(knowledge) -> PlantParameters:
    """The page at the revision this run read it, or the honest fallback.

    Shaped like `connectors.production.read_source_vocabulary` and
    `resolution.read_resolution_thresholds`, including the `@rev` on the ref: a
    decision derived from a governed value is only auditable if the run names
    the revision it read.
    """
    asset = knowledge.repo.get(POLICY_ASSET_ID) if knowledge else None
    if asset is None:
        return parse_plant_parameters("", f"knowledge:{POLICY_ASSET_ID}")
    return parse_plant_parameters(
        asset.content,
        f"knowledge:{POLICY_ASSET_ID}@rev{getattr(asset, 'revision', 1)}",
    )


def _affirmative(mapping: dict[str, str]) -> str:
    """The fact value the source's *affirmative* word produces.

    The eligibility clauses name a source field in prose — `release required:
    out to yard`, `hold excludes: do not fab` — and the vocabulary table maps
    that field's picklist words onto facts. What "required" and "excludes" mean
    is the state the flag is *set* to, so this is the row for the source's own
    word for yes. A plant whose system says `Y`/`N` is covered by `_AFFIRMATIVE`
    rather than by an edit here.
    """
    for word in _AFFIRMATIVE:
        value = mapping.get(word)
        if value:
            return value
    return ""
