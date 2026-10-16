"""A department's day: filled from the graph, signed by a person, then a file.

**Windowed, one run per (department, business day).** The eligibility sweep
opens a day by emitting `DepartmentDayOpened`; this is the response. The unit
of work is a *day* and not a job, because capacity, headcount and the incentive
are all properties of the day, "sort by load date and add until capacity" is a
batch decision, and the approval the original design insists on is per
department-day. Ten UiPath queues collapse into one run, one aggregate Task and
one governed commit.

**Everything it reads about the day it reads out of our own graph.** There is
no spreadsheet, because the schedule *is* `ScheduleDay` and `ScheduleEntry`,
declared `authoritative` — Cumulus is the system of record for a plant
schedule, since nothing else in the estate is. So the headcount is the count of
active `Employee`s `member_of` the department's `Team` rather than a number
somebody typed into a workbook every morning, and the previous day's rows are
rows rather than cells.

**The one thing this flow may never do is overwrite a supervisor.** A row a
person placed or edited carries `placed_by: human`, claimed under a `human:`
source — which outranks `flow:` in the ontology's precedence — so the
protection is structural rather than an `if` somebody could delete. A re-run
keeps those rows, counts their hours against the day's capacity, and fills what
is left.

**Nothing leaves the graph before approval.** `a1` writes the day at
`pending_approval`; `k1` is the permanent control point (the original design's
2.5.10) and the run suspends there; `a2` moves it to `approved`, and the export
answers at that instant and not before. That ordering is the flow's central
claim and the export's 409 is what enforces it.

**A dependency that is not met is `deferred`, never a failure.** The run
records the reason and finishes; nothing sits warm waiting for a saw to
finish, because the next day's eligibility sweep re-raises the day.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import date, datetime, timezone

from pydantic import BaseModel, Field

from ... import desks
from ...auth import Principal, flow_principal
from ...dispatch import Dispatcher, InMemoryEventLog
from ...estate import HRIS_WORKFORCE
from ...gateway import ActionGateway
from ...graph import Claim, GraphService, GraphWriter, object_id
from ...models import utcnow
from ...ontology import names as onto
from ...runs import RunContext, RunEngine
from ...task_queue import Task, TaskEvidence, TaskProposal, TaskService
from ..plant_eligibility.domain import (
    DEPARTMENT_DAY_EVENT,
    Candidate as EligibilityCandidate,
    judge as judge_eligibility,
)
from . import DEFAULT_VERSION, FLOW_ID
from . import formulas
from .parameters import (
    POLICY_ASSET_ID,
    CapacityRow,
    PlantParameters,
    read_plant_parameters,
)

log = logging.getLogger("cumulus.department_scheduling")

#: The fact this flow emits once a day is signed. Nothing is bound to it: what
#: reads an approved day is the export, on request, and a Trigger here would be
#: a response looking for a question.
DAY_SCHEDULED_EVENT = "DepartmentDayScheduled"

SUPERVISOR_QUEUE = desks.PLANT_SUPERVISOR

#: Who this flow acts as when it writes. Bound to the same desk a supervisor
#: approves from, so the Gateway asks the same question either way and
#: un-binding this stops the unreviewed commit rather than exempting it.
PLANT_FLOW = flow_principal(FLOW_ID, SUPERVISOR_QUEUE)

#: How many times one day may come back to a supervisor before the flow stops
#: asking. `accounts_payable`'s bound and the extraction's, for their reason: a
#: correction that does not settle is a conversation, and a flow that keeps
#: re-proposing is a loop nobody closes. Past the cap the day is left at
#: `pending_approval` — unapproved, and therefore unexportable, which is the
#: safe end of the two.
MAX_REVIEW_ROUNDS = 2

#: Zero, without a literal — `formulas` holds the same convention and the same
#: reason: this module adds up governed numbers and should not be able to
#: contribute one of its own.
_ZERO: float = math.fsum([])

#: The two extra row kinds, recovered from `formulas` rather than spelled here.
#: Calling each function with an empty grid and empty parameters returns a
#: `HoursResult` carrying only its own `formula` name, which is the string the
#: extraction wrote into `JobTask.demand_summary`. Naming them by asking the
#: module that writes them is what stops the two from drifting: rename the
#: formula and this follows, where a literal would silently stop matching and a
#: plant's nailing rows would quietly disappear.


def _marker(result) -> str:
    """A governed formula's name, up to and including its colon.

    `nailing: the standards' nailing hours over the multi-ply trusses` becomes
    `nailing:`, which is what a `demand_summary` segment starts with.
    """
    head, sep, _rest = result.formula.partition(":")
    return head + sep


_NAILING_MARK = _marker(formulas.nailing_hours({}, formulas.Parameters()))
_GIRTS_MARK = _marker(formulas.girts_hours({}, formulas.Parameters()))

#: `12 h from truss fab: …` — the shape `checks._summary` writes, one segment
#: per governed formula that contributed. It is parsed rather than re-derived
#: because the grid it was computed from is the extraction's and is not in the
#: graph; see `split_rows`.
_SEGMENT = re.compile(
    r"(?P<hours>[-+]?\d+(?:\.\d+)?)\s*h\s+from\s+(?P<formula>[^;]+)"
)

#: The worker statuses that mean "no longer working here". The governed page
#: says `count of active employees member_of the department team` and stops
#: there: it names no status vocabulary, so this is the platform's reading of
#: its word `active`, kept in one place and reported as a gap rather than
#: buried at a comparison.
_NOT_WORKING = frozenset({"terminated", "inactive", "separated", "leaver"})

#: What a row without a governed suffix is called, and the kinds themselves.
#: `standard` is the page's own first word under `- row kinds:`; the others are
#: read off the same line, so nothing here invents a kind the plant did not.
STANDARD = "standard"
NAILING = "nailing"
GIRTS = "girts"
CONSOLIDATED = "consolidated"


def plant_flow_principal(queue: str = "") -> Principal:
    """The Flow's own identity, bound to the desk it is committing under."""
    return flow_principal(FLOW_ID, queue or SUPERVISOR_QUEUE)


# ---------------------------------------------------------------------------
# What the day is made of
# ---------------------------------------------------------------------------


class Row(BaseModel):
    """One row of a department's day, before or after it is written.

    The shape `a1` commits as a `ScheduleEntry` and the shape the approval Task
    shows, computed once so the fill, the question and the write are looking at
    one object rather than three derivations of it.
    """

    key: str = ""
    job_key: str = ""
    job_id: str = ""
    job_number: str = ""
    job_label: str = ""
    labor_hours: float = _ZERO
    basis: str = ""
    sequence: float = _ZERO
    row_kind: str = STANDARD
    vehicle_ref: str = ""
    #: `flow` or `human`. Only ever `human` from a supervisor's correction, and
    #: the one value this flow must never write over.
    placed_by: str = "flow"


class Candidate(BaseModel):
    """One department's share of one job, as the graph holds it today.

    `x2`'s output, so it is also what a reader of the journal sees before any
    clause has run — which is why the job's own facts travel beside the hours
    rather than being looked up again inside the Rule.

    `upstream_done` is read **live** off the job at the moment the department
    is considered rather than off a snapshot taken when the job became
    eligible: a job whose truss cutting finished this morning is fabricable
    this afternoon, and the governed sequencing section says so in as many
    words.
    """

    job_key: str = ""
    job_id: str = ""
    job_ref: str = ""
    job_number: str = ""
    label: str = ""
    packet_label: str = ""
    load_date: str = ""
    week_position: float | None = None
    release_state: str = ""
    hold_reason: str = ""
    combined_with: str = ""
    task_key: str = ""
    task_ref: str = ""
    labor_hours: float = _ZERO
    hours_basis: str = ""
    demand_summary: str = ""
    source_page: str = ""
    out_of_band: bool = False
    upstream_done: list[str] = Field(default_factory=list)


class Verdict(BaseModel):
    """One candidate, judged — and the clause that judged it.

    `clause` is the machine-readable name of the governed line that deferred
    the share, so a journal can count deferrals by reason; `reason` is the
    sentence a supervisor reads. Both, because "why is 260118 not on Tuesday"
    has to answer as a clause and as English.
    """

    job_key: str = ""
    job_number: str = ""
    load_date: str = ""
    labor_hours: float = _ZERO
    placeable: bool = False
    clause: str = ""
    reason: str = ""


class Limits(BaseModel):
    """The governed ceiling this day was filled against, and its source.

    Kept whole rather than as four loose numbers because **which capacity row
    was chosen is the first number a supervisor will dispute** — the governed
    page says so — and a row name with no crew size beside it is not an answer.
    """

    department: str = ""
    row: str = ""
    row_note: str = ""
    goal_max: float | None = None
    absolute_max: float | None = None
    fudge: float = _ZERO
    min_rule: str = ""
    #: The hours at which the governed incentive crosses zero, for the two
    #: departments the page says fill by that figure rather than by a ceiling.
    crossing: float | None = None
    fills_by_incentive: bool = False

    @property
    def ceiling(self) -> float | None:
        """The most this day may hold: the goal or the hard stop, plus fudge.

        `- fudge applies to: goal max and absolute max`, and
        `- exceeding the absolute max within the fudge: accepted, not an
        exception` — the page's own two lines. The smaller of the two bounds
        wins, which in practice is the goal: the fudge is the latitude for the
        row that crosses the target, not a second target above it.
        """
        bounds = [
            value + self.fudge
            for value in (self.goal_max, self.absolute_max)
            if value is not None
        ]
        return min(bounds) if bounds else None

    @property
    def target(self) -> float | None:
        """Where the fill stops adding: the crossing, or the goal max."""
        if self.fills_by_incentive and self.crossing is not None:
            return self.crossing
        return self.goal_max


class Fill(BaseModel):
    """What `r2` decided: the rows, the total, and everything it left out."""

    rows: list[Row] = Field(default_factory=list)
    total_hours: float = _ZERO
    human_hours: float = _ZERO
    deferred: list[Verdict] = Field(default_factory=list)
    limits: Limits = Field(default_factory=Limits)
    #: The governed clause the fill stopped against, named so the journal can
    #: say which of the page's three limits was doing the work.
    stopped_at: str = ""
    incentive_per_hour: float = _ZERO
    incentive_sentence: str = ""
    out_of_the_hole: bool = False
    below_capacity: bool = False


class DayState(BaseModel):
    """`x1`: the day as our own graph already holds it, and who works it.

    Nothing here comes from a spreadsheet and nothing comes from the estate.
    The previous rows are rows in this graph, and the headcount is a count over
    `member_of` — the design had a supervisor typing it in every morning, which
    is a counting problem the org chart already solves.
    """

    department: str = ""
    schedule_date: str = ""
    day_key: str = ""
    day_id: str = ""
    day_ref: str = ""
    status: str = ""
    exists: bool = False
    rows: list[Row] = Field(default_factory=list)
    human_rows: list[Row] = Field(default_factory=list)
    #: An approval Task this flow already raised for this day and nobody has
    #: answered. A second one would be two people signing one day.
    open_task_id: str = ""
    team_slug: str = ""
    team_id: str = ""
    team_name: str = ""
    headcount: float = _ZERO
    headcount_as_of: str = ""
    headcount_stale: bool = False
    headcount_note: str = ""


# ---------------------------------------------------------------------------
# r1 — the Rule. Pure: it reads its arguments and nothing else.
# ---------------------------------------------------------------------------


def judge(
    candidates: list[Candidate],
    params: PlantParameters,
    *,
    department: str,
    schedule_date: date,
    as_of: date,
) -> list[Verdict]:
    """Every candidate against the governed clauses, in the page's own order.

    Four questions, and a share must clear all of them: is the job still
    eligible at all, is its upstream department done, is this day inside the
    department's lead-time and farthest-out windows, and is the day one the
    plant is open on. A share that fails one is **deferred for that named
    reason** and stops there — reporting four reasons for one job would make
    "how many were held for the saw" unanswerable.

    Pure in the doctrine's sense: the clauses arrive parsed, the words arrive
    already translated by `job-sync` against the governed vocabulary, and both
    dates arrive from the caller. Nothing here fetches, so it replays against a
    fixture with no world behind it.
    """
    day_shut = (
        ""
        if params.calendar.is_business_day(schedule_date)
        else params.calendar.why_not_a_business_day(schedule_date)
        or f"{schedule_date.isoformat()} is not a plant business day"
    )
    eligibility = {
        verdict.job_key: verdict
        for verdict in judge_eligibility(
            [_as_eligibility_candidate(row) for row in candidates],
            params, as_of,
        )
    }
    upstream = params.sequencing.upstream_for(department)
    out: list[Verdict] = []
    for candidate in candidates:
        verdict = Verdict(
            job_key=candidate.job_key, job_number=candidate.job_number,
            load_date=candidate.load_date,
            labor_hours=candidate.labor_hours,
        )
        clause, reason = _first_failure(
            candidate, params,
            department=department, schedule_date=schedule_date,
            day_shut=day_shut, upstream=upstream,
            eligible=eligibility.get(candidate.job_key),
        )
        verdict.placeable = not clause
        verdict.clause = clause
        verdict.reason = reason or (
            "clears every governed clause for this department-day"
        )
        out.append(verdict)
    return out


def _first_failure(
    candidate: Candidate,
    params: PlantParameters,
    *,
    department: str,
    schedule_date: date,
    day_shut: str,
    upstream: str,
    eligible,
) -> tuple[str, str]:
    """The first governed clause this share fails, named, or `("", "")`."""
    # 1 — the day itself. Checked per candidate rather than once so that a day
    # nobody should be working reads as a deferral with a reason on every row,
    # which is what a supervisor opening the day sees.
    if day_shut:
        return "not-a-business-day", day_shut

    # 2 — is the job still in play? Re-asked here and not taken on trust from
    # the sweep that opened the day, because the interesting case is exactly
    # the one where something changed in between: a job somebody put a hold on
    # after it queued must not be built this afternoon. The clauses are the
    # sweep's own, run through the sweep's own Rule, so there is one
    # implementation of "eligible" in this system.
    if eligible is not None and not eligible.eligible:
        return f"not-eligible:{eligible.clause}", (
            f"the job is no longer eligible: {eligible.reason}"
        )

    # 3 — `- unmet upstream dependency: deferred to the next window`.
    if upstream and upstream not in candidate.upstream_done:
        return "upstream-not-done", (
            f"the governed sequencing table puts `{upstream}` before "
            f"`{department}`, and the job system does not have it done yet — "
            + (params.sequencing.unmet_upstream_dependency
               or "deferred to the next window")
        )

    # 4 — the two windows, both counted in business days back from the load
    # date. The lead time is where the work is normally placed; the
    # farthest-out window is the earliest it may be placed at all.
    load_date = _iso_date(candidate.load_date)
    if load_date is None:
        return "no-load-date", (
            "the job system holds no load date, and every plant window is "
            "counted back from one"
        )
    if schedule_date > load_date:
        return "load-date-past", (
            f"this day is {schedule_date.isoformat()} and the job loads "
            f"{candidate.load_date} — the work would be done after the truck"
        )
    lead = params.lead_time_for(department)
    assumed = ""
    if lead is None:
        lead = params.nearest_lead_time_for(department)
        assumed = lead.condition if lead is not None else ""
    if lead is None:
        return "no-lead-time", (
            "the governed lead-time table has no row for "
            f"`{department}`, so there is no window to place this in"
        )
    if lead.farthest_out_days is not None:
        earliest = params.calendar.business_days_before(
            load_date, lead.farthest_out_days
        )
        if schedule_date < earliest:
            return "too-far-out", (
                f"the governed farthest-out window for `{department}` is "
                f"{lead.farthest_out_days} business day(s) before the load "
                f"date, which is {earliest.isoformat()}, and this day is "
                f"{schedule_date.isoformat()}"
                + (
                    f" — read off the `{assumed}` row, because the page "
                    "conditions this department's window and the demand row "
                    "does not say which" if assumed else ""
                )
            )
    return "", ""


def _as_eligibility_candidate(row: Candidate) -> EligibilityCandidate:
    """This flow's candidate in the shape the sweep's own Rule judges.

    A projection and not a second reading: every field here was read off the
    same `Job` record the sweep reads, and handing the sweep's `judge` its own
    model is what lets the eligibility clauses have exactly one implementation
    in this system.
    """
    return EligibilityCandidate(
        job_key=row.job_key, job_id=row.job_id, job_ref=row.job_ref,
        job_number=row.job_number, label=row.label,
        load_date=row.load_date, week_position=row.week_position,
        release_state=row.release_state, hold_reason=row.hold_reason,
    )


def _iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat((value or "").strip()[:10])
    except ValueError:
        return None


def deferrals(verdicts: list[Verdict]) -> dict[str, int]:
    """Deferred shares counted by the clause that deferred them."""
    counts: dict[str, int] = {}
    for verdict in verdicts:
        if not verdict.placeable:
            counts[verdict.clause] = counts.get(verdict.clause, 0) + 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# r2 — the fill. Also pure.
# ---------------------------------------------------------------------------


def choose_limits(
    params: PlantParameters, department: str, headcount: float
) -> Limits:
    """The governed capacity row for this department at this crew size.

    Three ways to land on a row, tried in the page's own order, and the one
    that answered is recorded because it is the first number a supervisor will
    dispute:

    1. the **headcount ladder** — `- Employees present | Capacity row` — which
       is what the page says turns a crew count into a row;
    2. the department's own rows read as crew sizes, when the ladder names a
       row this department does not declare. The ladder is written for the
       truss tables (`table-3-man` …) and the saw declares `saw-1-man` and
       `saw-2-man`, so a plant with a two-man saw would otherwise fall through
       to a department with no `any` row and no ceiling at all;
    3. the `any` row, which is what most departments carry.

    A department the page has no row for at all is not an error and not a
    ceiling of zero: the fill runs to the demand and the journal says the page
    carries no row, which is the honest degrade every reader here uses.
    """
    incentive = params.incentive_for(department)
    limits = Limits(
        department=department,
        crossing=formulas.crosses_zero_at(incentive),
        fills_by_incentive=department in params.incentive_decides_the_fill,
    )
    rung = params.headcount.rung_for(headcount)
    ladder_row = rung.row if rung is not None else ""
    chosen: CapacityRow | None = None
    note = ""
    if ladder_row:
        chosen = next(
            (row for row in params.capacity.rows
             if row.department == department and row.row == ladder_row),
            None,
        )
        if chosen is not None:
            note = (
                f"the governed headcount ladder puts {headcount:g} "
                f"employee(s) on `{ladder_row}`"
            )
    if chosen is None:
        chosen, sized = _row_by_crew_size(params, department, headcount)
        if chosen is not None:
            note = (
                f"the ladder's `{ladder_row}` is not a row `{department}` "
                f"declares, so {headcount:g} employee(s) chose `{chosen.row}` "
                f"among this department's own rows"
                if ladder_row and sized else
                f"the department's `{chosen.row}` row"
            )
    if chosen is None:
        chosen = params.capacity.row_for(department)
        if chosen is not None:
            note = f"the department's `{chosen.row}` row"
    if chosen is None:
        # Every row this department has is conditioned on something the day
        # does not know — CondenStop for steel, how many specials for trim,
        # spliced or solid for laminates — and those are properties of a
        # ticket rather than of a crew. The **most restrictive** row stands
        # and the day says so, which is `nearest_lead_time_for`'s reasoning
        # inverted for exactly the right reason: a lead time is a permission
        # and the nearest is what no row forbids, where a capacity is a
        # ceiling and the lowest is what no row exceeds.
        chosen = _most_restrictive(params, department)
        if chosen is not None:
            note = (
                f"every `{department}` row on the page is conditioned on "
                f"something this day does not know, so the most restrictive "
                f"row (`{chosen.row}`) stands"
            )
    if chosen is None:
        limits.row_note = (
            f"the governed capacity table carries no row for `{department}`, "
            "so this day filled to the demand and no limit stopped it"
        )
        return limits
    limits.row = chosen.row
    limits.row_note = note or f"the department's `{chosen.row}` row"
    limits.goal_max = chosen.goal_max
    limits.absolute_max = chosen.absolute_max
    limits.fudge = chosen.fudge if chosen.fudge is not None else _ZERO
    limits.min_rule = chosen.min_rule
    return limits


def _most_restrictive(
    params: PlantParameters, department: str
) -> CapacityRow | None:
    """The department's tightest row: lowest absolute max, then lowest goal.

    A row with no ceiling at all is the least restrictive there is and sorts
    last, because "fill to the demand" is not a limit a day should inherit
    from a condition nobody checked.
    """
    rows = [
        row for row in params.capacity.rows if row.department == department
    ]
    if not rows:
        return None
    return min(rows, key=lambda row: (
        row.absolute_max if row.absolute_max is not None else float("inf"),
        row.goal_max if row.goal_max is not None else float("inf"),
    ))


def _row_by_crew_size(
    params: PlantParameters, department: str, headcount: float
) -> tuple[CapacityRow | None, bool]:
    """The department's own row whose crew size the headcount reaches.

    `saw-1-man` and `saw-2-man` are the page's words for one and two people at
    the saw, and the number in the row name is the plant's own statement of the
    crew it describes. The largest row the headcount reaches wins; below the
    smallest, the smallest stands, because a department with one person still
    has a capacity and refusing to name one would schedule nothing.
    """
    sized: list[tuple[float, CapacityRow]] = []
    for row in params.capacity.rows:
        if row.department != department or row.row in ("any", ""):
            continue
        found = re.search(r"\d+(?:\.\d+)?", row.row)
        if found:
            sized.append((float(found.group(0)), row))
    if not sized:
        return None, False
    sized.sort(key=lambda pair: pair[0])
    reached = [row for size, row in sized if size <= headcount]
    return (reached[-1] if reached else sized[0][1]), True


def fill(
    placeable: list[Verdict],
    candidates: dict[str, Candidate],
    params: PlantParameters,
    *,
    department: str,
    limits: Limits,
    human_rows: list[Row],
) -> Fill:
    """Add shares in load-date order until the governed limit says stop. Pure.

    Two stopping rules, and the page names which departments use which:

    - most departments fill to the **goal max**, with the fudge as the latitude
      for the row that crosses it and the absolute max plus the same fudge as
      the hard stop — `going past the absolute max by no more than the fudge is
      explicitly not an exception` is the design's own sentence;
    - the two departments under `- incentive decides the fill:` fill until the
      **computed** incentive crosses zero instead, because for them the floor
      is stated as money rather than hours and the plant's phrase for a full
      day is getting *out of the hole*.

    Rows a supervisor placed by hand are already on the day and their hours are
    counted first: they occupy capacity, and a fill that ignored them would
    hand a department a day and a half of work.

    **The first share that does not fit stops the fill**, and everything behind
    it is deferred for the same limit. Skipping a large job to fit a smaller
    one behind it would quietly reorder a queue whose order — load date
    ascending — is the plant's own priority.

    **Below capacity is reported and never forced.** The governed page's own
    line: who assigns keep-home, scrap or indirect work is a person's decision.
    """
    rows = [row.model_copy(deep=True) for row in human_rows]
    human_hours = math.fsum([row.labor_hours for row in rows])
    total = human_hours
    placed_jobs = {row.job_key for row in rows if row.job_key}
    deferred: list[Verdict] = []
    stopped_at = ""
    ceiling = limits.ceiling
    target = limits.target
    incentive = params.incentive_for(department)

    for verdict in placeable:
        candidate = candidates.get(verdict.job_key)
        if candidate is None:
            continue
        if candidate.job_key in placed_jobs:
            deferred.append(verdict.model_copy(update={
                "placeable": False, "clause": "already-placed",
                "reason": (
                    "a supervisor already placed this job on this day, and "
                    "the governed page says supervisor-placed rows are never "
                    "overwritten"
                ),
            }))
            continue
        if stopped_at:
            deferred.append(verdict.model_copy(update={
                "placeable": False, "clause": "day-full",
                "reason": (
                    f"the day was already full when this share came up in "
                    f"load-date order — {stopped_at}"
                ),
            }))
            continue
        if target is not None and total >= target:
            stopped_at = _target_sentence(limits, total, incentive)
            deferred.append(verdict.model_copy(update={
                "placeable": False, "clause": "day-full",
                "reason": stopped_at,
            }))
            continue
        prospective = math.fsum([total, candidate.labor_hours])
        if ceiling is not None and prospective > ceiling:
            stopped_at = (
                f"adding {candidate.job_number} would take the day to "
                f"{prospective:,.1f} h, past the governed ceiling of "
                f"{ceiling:,.1f} h "
                f"({_ceiling_sentence(limits)})"
            )
            deferred.append(verdict.model_copy(update={
                "placeable": False, "clause": "over-capacity",
                "reason": stopped_at,
            }))
            continue
        rows.extend(_rows_for(candidate, params, department))
        total = prospective

    for index, row in enumerate(_sequenced(rows), start=1):
        row.sequence = float(index)
    figure = formulas.incentive(total, incentive)
    minimum = _minimum_hours(limits, params, department)
    return Fill(
        rows=rows, total_hours=total, human_hours=human_hours,
        deferred=deferred, limits=limits,
        stopped_at=stopped_at or _unstopped(limits, total, target),
        incentive_per_hour=figure,
        incentive_sentence=formulas.incentive_sentence(total, incentive),
        out_of_the_hole=figure >= _ZERO and bool(incentive.found_asset),
        below_capacity=minimum is not None and total < minimum,
    )


def _minimum_hours(
    limits: Limits, params: PlantParameters, department: str
) -> float | None:
    """The day's floor in hours, whether the page states it in hours or money.

    `- minimum out of the hole: the incentive crossing zero` is the page's own
    resolution of a minimum written as `out of the hole` in the capacity table,
    so a department whose minimum is money has one all the same and a day below
    it is reported.
    """
    row = params.capacity.row_for(department, limits.row)
    if row is not None and row.min_hours is not None:
        return row.min_hours
    return limits.crossing


def _target_sentence(
    limits: Limits, total: float, incentive: formulas.Incentive
) -> str:
    if limits.fills_by_incentive and limits.crossing is not None:
        return (
            f"the day is at {total:,.1f} h and the governed incentive for "
            f"`{limits.department}` crosses zero at {limits.crossing:,.2f} h "
            f"— {formulas.incentive_sentence(total, incentive)}"
        )
    return (
        f"the day is at {total:,.1f} h and the governed goal max for "
        f"`{limits.row or limits.department}` is {limits.goal_max:,.1f} h"
    )


def _ceiling_sentence(limits: Limits) -> str:
    parts = []
    if limits.goal_max is not None:
        parts.append(f"goal max {limits.goal_max:,.1f} h")
    if limits.absolute_max is not None:
        parts.append(f"absolute max {limits.absolute_max:,.1f} h")
    parts.append(f"fudge {limits.fudge:,.1f} h")
    return f"{limits.row or limits.department}: " + " · ".join(parts)


def _unstopped(limits: Limits, total: float, target: float | None) -> str:
    """What stopped the fill when no limit did — which clause the page used."""
    if target is None and limits.absolute_max is None:
        return (
            f"the demand ran out at {total:,.1f} h; the governed row "
            f"`{limits.row or limits.department}` states no goal and no "
            "absolute max, so the demand was the limit"
        )
    if target is None:
        return (
            f"the demand ran out at {total:,.1f} h; the governed row "
            f"`{limits.row}` states no goal max, so the absolute max of "
            f"{limits.absolute_max:,.1f} h was the only ceiling"
        )
    return f"the demand ran out at {total:,.1f} h, below the governed target"


def _sequenced(rows: list[Row]) -> list[Row]:
    """The day's rows in the order the floor works them.

    A supervisor's own rows keep the top of the day: they were placed
    deliberately, and re-sorting them under a computed row would be this flow
    editing a decision it is not allowed to touch. Everything else follows in
    the order the fill placed it, which is load-date ascending.
    """
    human = [row for row in rows if row.placed_by == "human"]
    return human + [row for row in rows if row.placed_by != "human"]


# ---------------------------------------------------------------------------
# Row shaping — a job is not always one row
# ---------------------------------------------------------------------------


def split_rows(candidate: Candidate) -> list[tuple[str, float, str]]:
    """`(row_kind, hours, formula)` per row this share contributes.

    The governed page raises a NAILING row where any truss on the job is
    multi-ply and a GIRTS row where the B page carries the handwritten note.
    `formulas.nailing_applies` and `formulas.girts_applies` answer both — from
    the *grid*, which is the extraction's ModelCall output and is not in the
    graph. What **is** in the graph is `JobTask.demand_summary`, the sentence
    the extraction wrote: `page A1: 12 h from truss fab: …; 3 h from nailing:
    …`, one segment per governed formula that contributed hours.

    So the split is read off that sentence, and the total is preserved exactly:
    the standard row carries whatever the extra rows do not. A summary this
    cannot parse yields one standard row for the whole share, which is the
    honest degrade — a day is still correct, it simply does not separate the
    nailing out.

    **This is a seam that should be a claim.** `DepartmentDemand.row_kinds` is
    computed by the extraction and dropped at the write, and `JobTask` declares
    no field for the split. Claiming it there would make this function a read.
    """
    segments = [
        (float(match.group("hours")), match.group("formula").strip())
        for match in _SEGMENT.finditer(candidate.demand_summary or "")
    ]
    extras: list[tuple[str, float, str]] = []
    for hours, formula in segments:
        if formula.startswith(_NAILING_MARK):
            extras.append((NAILING, hours, formula))
        elif formula.startswith(_GIRTS_MARK):
            extras.append((GIRTS, hours, formula))
    if not extras:
        return [(STANDARD, candidate.labor_hours, candidate.hours_basis)]
    standard = math.fsum(
        [candidate.labor_hours] + [-hours for _kind, hours, _f in extras]
    )
    return [(STANDARD, standard, candidate.hours_basis)] + extras


def label_for(
    candidate: Candidate,
    params: PlantParameters,
    department: str,
    row_kind: str,
) -> str:
    """Column B: the packet label, this department's suffix, and its extras.

    The base is the label a crew already calls the job by — `Job.packet_label`,
    written by the field-documents flow off the same governed convention — so a
    schedule row and a crew's packet name the job the same thing. A job no
    packet pass has reached yet falls back to its number, which is legible and
    is not a second convention.
    """
    base = candidate.packet_label or candidate.job_number
    rule = next(
        (row for row in params.labels.rules if row.department == department),
        None,
    )
    suffix = rule.suffix if rule is not None else ""
    if rule is not None and row_kind != STANDARD:
        suffix = _extra_suffix(rule.extra, row_kind) or suffix
    label = f"{base} {suffix}".strip()
    if params.labels.max_length:
        return label[: params.labels.max_length]
    return label


def _extra_suffix(extra: str, row_kind: str) -> str:
    """The governed `Extra` column's token for one row kind.

    The column is written for a person — ``nailing rows: `TF-NAIL`; girts
    rows: `TF-GIRTS` `` — so the segment that mentions the kind is the segment
    that answers, and the backticked token in it is the label. A kind the
    column does not mention has no override and keeps the department's suffix.
    """
    for segment in (extra or "").split(";"):
        if row_kind.lower() not in segment.lower():
            continue
        found = re.search(r"`([^`]+)`", segment)
        if found:
            return found.group(1).strip()
    return ""


def _rows_for(
    candidate: Candidate, params: PlantParameters, department: str
) -> list[Row]:
    """One share as the rows it actually contributes to the day."""
    out: list[Row] = []
    for kind, hours, basis in split_rows(candidate):
        key = candidate.job_number if kind == STANDARD else (
            f"{candidate.job_number}:{kind}"
        )
        out.append(Row(
            key=key,
            job_key=candidate.job_key,
            job_id=candidate.job_id,
            job_number=candidate.job_number,
            job_label=label_for(candidate, params, department, kind),
            labor_hours=hours,
            basis=basis,
            row_kind=kind,
            placed_by="flow",
        ))
    return out


def consolidate_loading(
    rows: list[Row],
    candidates: dict[str, Candidate],
    params: PlantParameters,
    department: str,
) -> list[Row]:
    """Loading's own shape: tickets sharing a truck become one row.

    `- loading consolidates: tickets sharing a truck become one row`, and
    `formulas.loading_row_groups` is the pure grouping — `combined_with` read
    as an undirected relation, so a three-way consolidation is one group and
    the source's spelling of the pair does not matter.

    The trailer number is deliberately left blank: `- loading trailer number:
    left blank for the supervisor`, because which trailer goes out is the
    yard's call on the day and writing a guess into that column would be this
    system making an operational decision.
    """
    by_number = {row.job_number: row for row in rows}
    groups = formulas.loading_row_groups([
        {
            "job_number": row.job_number,
            "combined_with": (
                candidates[row.job_key].combined_with
                if row.job_key in candidates else ""
            ),
            "hours": row.labor_hours,
        }
        for row in rows
    ])
    out: list[Row] = []
    for group in groups:
        members = [
            by_number[number] for number in group["job_numbers"]
            if number in by_number
        ]
        if not members:
            continue
        first = members[0]
        if not group["consolidated"]:
            out.append(first)
            continue
        out.append(first.model_copy(update={
            "key": f"{group['truck_key']}:{CONSOLIDATED}",
            "job_number": group["truck_key"],
            "job_label": (
                f"{group['truck_key']} "
                f"{params.labels.suffix_for(department)}".strip()
            ),
            "labor_hours": group["hours"],
            "row_kind": CONSOLIDATED,
            "vehicle_ref": "",
            "basis": (
                f"{first.basis} · consolidated with "
                f"{', '.join(group['job_numbers'][1:])}: "
                + (params.row_shaping.loading_consolidates
                   or "tickets sharing a truck become one row")
            ),
        }))
    return out


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


class DepartmentSchedulingService:
    """Reads the day out of our own graph, fills it, asks, and commits.

    Holds no directory of its own: the schedule *is* `ScheduleDay` and its
    parts, and a second table here would be a second account of the record this
    platform is authoritative for.
    """

    def __init__(
        self,
        graph: GraphService,
        knowledge,  # KnowledgeService — duck-typed to avoid an import cycle
        tasks: TaskService,
        engine: RunEngine | None = None,
        gateway: ActionGateway | None = None,
        dispatcher: Dispatcher | None = None,
    ) -> None:
        self.graph = graph
        self.knowledge = knowledge
        self.tasks = tasks
        self.engine = engine
        self.gateway = gateway
        self.dispatcher = dispatcher or (
            Dispatcher(engine, InMemoryEventLog()) if engine is not None
            else None
        )
        if engine is not None:
            from .. import shipped_versions

            for number, module in sorted(shipped_versions(FLOW_ID).items()):
                engine.register(
                    FLOW_ID, module.build_body(self),
                    version=number, default=(number == DEFAULT_VERSION),
                )

    # -- the door -----------------------------------------------------------

    def open_day(
        self, department: str, schedule_date: str,
        actor: str = "system:scheduler",
    ) -> object:
        """Open one department-day. The Trigger table is what answers.

        Through the Dispatcher rather than by calling the body, so the
        causation is logged before the run exists — and so a day opened by hand
        reaches the flow by exactly the path the eligibility sweep's own
        emission takes.
        """
        if self.dispatcher is None:
            raise RuntimeError("The department scheduling flow has no "
                               "dispatcher wired")
        return self.dispatcher.emit(
            DEPARTMENT_DAY_EVENT,
            {"department": department, "schedule_date": schedule_date},
            actor=actor, case_ref=f"{department}:{schedule_date}",
        )

    # -- governed reads -----------------------------------------------------

    def read_parameters(self) -> PlantParameters:
        return read_plant_parameters(self.knowledge)

    # -- x1: the day, as our own graph holds it ----------------------------

    def day_state(
        self, department: str, schedule_date: str, params: PlantParameters
    ) -> DayState:
        """The `ScheduleDay` a previous window opened, and who works it.

        Reads three things and no spreadsheet: the day itself with its rows —
        specifically the `placed_by: human` rows this flow may never overwrite
        — the count of active `Employee`s `member_of` the department's `Team`,
        and how old the workforce feed's last landing is.
        """
        state = DayState(
            department=department, schedule_date=schedule_date,
            day_key=day_key(department, schedule_date),
        )
        record = self.graph.get(
            object_id(onto.SCHEDULE_DAY, state.day_key), follow=False
        )
        if record is not None:
            state.exists = True
            state.day_id = record.id
            state.day_ref = record.ref()
            state.status = str(record.props.get("status") or "")
            state.rows = self._entries(record.id, state.day_key)
            state.human_rows = [
                row for row in state.rows if row.placed_by == "human"
            ]
        state.open_task_id = self.open_approval_task_id(state.day_key)
        self._headcount(state, params)
        return state

    def _entries(self, day_id: str, key: str) -> list[Row]:
        """The day's live rows, in sequence.

        The row key is the entry's natural key with the day's own key taken
        off the front — `truss-fab:2026-09-08:250553:nailing` is the nailing
        row for job 250553, and splitting on the last colon would have called
        it `nailing`.
        """
        rows: list[Row] = []
        prefix = f"{key}:"
        for _link, entry in self.graph.neighbors(
            day_id, "has_schedule_entry", "out"
        ):
            props = entry.props
            rows.append(Row(
                key=entry.natural_key[len(prefix):]
                if entry.natural_key.startswith(prefix)
                else entry.natural_key,
                job_number=str(props.get("job_number") or ""),
                job_label=str(props.get("job_label") or ""),
                labor_hours=float(props.get("labor_hours") or 0.0),
                basis=str(props.get("basis") or ""),
                sequence=float(props.get("sequence") or 0.0),
                row_kind=str(props.get("row_kind") or STANDARD),
                vehicle_ref=str(props.get("vehicle_ref") or ""),
                placed_by=str(props.get("placed_by") or "flow"),
                job_key=_job_key_of(self.graph, entry.id),
                job_id=_job_id_of(self.graph, entry.id),
            ))
        rows.sort(key=lambda row: (row.sequence, row.key))
        return rows

    def open_approval_task_id(self, key: str) -> str:
        """An approval this flow already raised for this day, unanswered.

        Public because the export asks the same question: a day it refuses has
        to name the Task somebody has to answer before it will, and deriving
        that a second way would give two answers to one question.
        """
        for task in self.tasks.list():
            if (
                task.flow_id == FLOW_ID
                and task.case_ref == key
                and task.status == "open"
            ):
                return task.id
        return ""

    def _headcount(self, state: DayState, params: PlantParameters) -> None:
        """The crew, counted off the org chart rather than typed in.

        The department's `Team` is found through the governed `## Departments`
        table's workforce-system name, which is the only place the three
        spellings of a department are tied together; a plant that renames one
        renames it there. Failing that, the slug itself, because a deployment
        whose workforce feed already uses the platform's slug is not wrong.
        """
        row = next(
            (item for item in params.departments
             if item.slug == state.department), None,
        )
        state.team_name = row.hris_name if row is not None else ""
        team = _find_team(self.graph, state.team_name, state.department)
        if team is None:
            state.headcount_note = (
                "no `Team` in the graph answers to "
                f"`{state.team_name or state.department}`, "
                "so this day has no crew count — the workforce feed has not "
                "landed this department"
            )
            state.headcount_stale = True
            return
        state.team_id = team.id
        state.team_slug = str(team.props.get("slug") or team.natural_key)
        members = [
            employee for _link, employee in self.graph.neighbors(
                team.id, "member_of", "in"
            )
            if employee.type == onto.EMPLOYEE and _working(employee)
        ]
        state.headcount = float(len(members))
        state.headcount_as_of = self._workforce_landed_at()
        window = params.headcount.freshness_hours
        age = _hours_since(state.headcount_as_of)
        state.headcount_stale = (
            age is None or (window is not None and age > window)
        )
        state.headcount_note = (
            f"{len(members)} active employee(s) `member_of` "
            f"{state.team_slug or state.team_name}"
            + (
                f", counted from a workforce feed that last landed "
                f"{state.headcount_as_of} ({age:,.1f} h ago)"
                if age is not None else
                ", and the workforce feed has landed nothing in this "
                "deployment, so the count's age is unknown"
            )
        )

    def _workforce_landed_at(self) -> str:
        """When the workforce feed last landed, off its own source rows.

        The count is only as current as the feed behind it, and the feed's own
        landing time is a fact the sync flow already records on every
        `SourceRecord` it writes. Read here rather than claimed on the day by
        somebody else, because a second copy of a timestamp is a second answer
        to when the crew list was last true.
        """
        newest = ""
        for record in self.graph.find(onto.SOURCE_RECORD):
            if str(record.props.get("source") or "") != HRIS_WORKFORCE:
                continue
            landed = str(record.props.get("landed_at") or "")
            if landed > newest:
                newest = landed
        return newest

    # -- x2: this department's share of the work ---------------------------

    def candidates(
        self, department: str, params: PlantParameters
    ) -> list[Candidate]:
        """Every `JobTask` this department owes, with its job read live.

        The graph and not a delivery: a work list scoped to one delivery skews
        and a standing question does not. Sorted by load date ascending because
        the governed page says so — `- sort candidates by: load date ascending`
        — with a share whose job carries no load date sorting last, since a
        date nobody wrote cannot be earlier than one somebody did.
        """
        found: list[Candidate] = []
        for task in self.graph.find(
            onto.JOB_TASK, prop_equals={"department": department}
        ):
            jobs = [
                job for _link, job in self.graph.neighbors(
                    task.id, "has_job_task", "in"
                )
                if job.type == onto.JOB
            ]
            if not jobs:
                continue
            found.append(_candidate_of(jobs[0], task))
        ascending = "desc" not in (params.eligibility.sort_by or "").lower()
        found.sort(
            key=lambda row: (
                not row.load_date, row.load_date, row.job_number
            ),
            reverse=not ascending,
        )
        return found

    # -- a1: the write. There is no second one. ----------------------------

    def apply_schedule_day(
        self,
        state: DayState,
        filled: Fill,
        params: PlantParameters,
        run_id: str,
        writer: GraphWriter | None = None,
        *,
        decided_by: str = "",
    ) -> dict:
        """The `ScheduleDay` at `pending_approval`, and its rows.

        Claimed as `flow:` so that a supervisor's later `human:` claim on the
        same field outranks it forever — which is how `placed_by: human` and an
        approval survive every re-run without a guard anybody could delete.

        Rows a previous fill placed and this one did not are **withdrawn, not
        deleted**: the `has_schedule_entry` edge ends its validity and the row
        keeps its record, exactly the way a job moving between crews is
        handled. What a department was told to run last Tuesday stays
        answerable.
        """
        graph = writer if writer is not None else self.graph
        source = f"flow:{FLOW_ID}"
        day = graph.upsert(
            onto.SCHEDULE_DAY, state.day_key,
            [
                Claim(field="department", value=state.department,
                      source=source),
                Claim(field="schedule_date", value=state.schedule_date,
                      source=source),
                Claim(field="status", value="pending_approval", source=source),
                Claim(field="total_hours", value=filled.total_hours,
                      source=source),
                Claim(field="goal_max_hours", value=filled.limits.goal_max,
                      source=source),
                Claim(field="abs_max_hours",
                      value=filled.limits.absolute_max, source=source),
                Claim(field="fudge_hours", value=filled.limits.fudge,
                      source=source),
                Claim(field="headcount", value=state.headcount, source=source),
                Claim(field="headcount_as_of", value=state.headcount_as_of,
                      source=source),
                Claim(field="incentive_per_hour",
                      value=filled.incentive_per_hour, source=source),
                Claim(field="deferred_count",
                      value=float(len(filled.deferred)), source=source),
            ],
            label=f"{state.department} {state.schedule_date}",
            provenance=f"filled by {run_id}",
        )
        if state.team_id:
            graph.link(
                "scheduled_by", day.id, state.team_id,
                method="declared", provenance=f"filled by {run_id}",
            )
        written: list[dict] = []
        keys: set[str] = set()
        for row in filled.rows:
            key = f"{state.day_key}:{row.key}"
            keys.add(key)
            row_source = (
                f"human:{decided_by}"
                if row.placed_by == "human" and decided_by
                else "human:supervisor" if row.placed_by == "human"
                else source
            )
            entry = graph.upsert(
                onto.SCHEDULE_ENTRY, key,
                [
                    Claim(field="job_number", value=row.job_number,
                          source=row_source),
                    Claim(field="job_label", value=row.job_label,
                          source=row_source),
                    Claim(field="labor_hours", value=row.labor_hours,
                          source=row_source),
                    Claim(field="sequence", value=row.sequence,
                          source=row_source),
                    Claim(field="basis", value=row.basis, source=row_source),
                    Claim(field="row_kind", value=row.row_kind,
                          source=row_source),
                    Claim(field="vehicle_ref", value=row.vehicle_ref,
                          source=row_source),
                    Claim(field="placed_by", value=row.placed_by,
                          source=row_source),
                ],
                label=row.job_label or row.job_number,
                provenance=f"placed by {run_id}",
            )
            graph.link(
                "has_schedule_entry", day.id, entry.id,
                method="declared", provenance=f"placed by {run_id}",
            )
            if row.job_id:
                graph.link(
                    "scheduled_for", entry.id, row.job_id,
                    method="declared", provenance=f"placed by {run_id}",
                )
            written.append({
                "key": key, "job_number": row.job_number,
                "labor_hours": row.labor_hours, "row_kind": row.row_kind,
                "placed_by": row.placed_by, "ref": entry.ref(),
            })
        withdrawn = self._withdraw_dropped(graph, day.id, keys, run_id)
        return {
            "day_ref": day.ref(),
            "day_key": state.day_key,
            "status": "pending_approval",
            "rows": written,
            "withdrawn": withdrawn,
            "total_hours": filled.total_hours,
            "parameters_ref": params.source_ref,
        }

    def _withdraw_dropped(
        self, graph, day_id: str, keys: set[str], run_id: str
    ) -> list[str]:
        """Rows a previous fill placed and this one did not."""
        dropped: list[str] = []
        for link, entry in self.graph.neighbors(
            day_id, "has_schedule_entry", "out"
        ):
            if entry.natural_key in keys:
                continue
            graph.reverse_link(
                link.id, f"flow:{FLOW_ID}",
                f"not placed by the fill in {run_id}",
            )
            dropped.append(entry.natural_key)
        return dropped

    # -- k1: the permanent control point -----------------------------------

    def build_approval_task(
        self,
        ctx: RunContext,
        state: DayState,
        filled: Fill,
        params: PlantParameters,
        *,
        round_number: int,
        queue: str,
        routing_note: str,
    ) -> Task:
        """One Task per department-day, carrying everything a signature needs.

        The rows with their hours and basis; the total against the goal and the
        absolute max with the fudge band; the incentive **stated as its own
        arithmetic**, so the out-of-the-hole indicator is something a
        supervisor can dispute rather than a cell nobody can audit; what was
        deferred and why; the headcount with its age, warned about rather than
        failed on when the feed is behind; and what approving unlocks.
        """
        limits = filled.limits
        stale = state.headcount_stale
        return Task(
            id=f"task_{ctx.record.id}_k1_{round_number}",
            kind="decision",
            title=(
                f"Approve {state.department} for {state.schedule_date} — "
                f"{len(filled.rows)} row(s), {filled.total_hours:,.1f} h"
            ),
            summary=(
                f"The fill placed {len(filled.rows)} row(s) totalling "
                f"{filled.total_hours:,.1f} h against "
                f"{_limit_sentence(limits)}. {filled.stopped_at}. "
                f"Incentive: {filled.incentive_sentence} — "
                + (
                    "out of the hole." if filled.out_of_the_hole
                    else "still in the hole."
                )
                + (
                    f" {len(filled.deferred)} share(s) were deferred and are "
                    "listed below with their reasons; the next window "
                    "re-raises them."
                    if filled.deferred else ""
                )
                + (
                    " This day is below its governed minimum. The page says "
                    "below capacity is reported and never forced — who "
                    "assigns keep-home, scrap or indirect work is your call."
                    if filled.below_capacity else ""
                )
                + (
                    f" **Headcount warning:** {state.headcount_note}. The "
                    "governed freshness window has passed, so this count is "
                    "shown rather than trusted — the page says warn and "
                    "proceed."
                    if stale else ""
                )
                + "\n\nNothing has left this platform. The day is written at "
                "`pending_approval` and the schedule export refuses to answer "
                "for it until you approve; approving is what makes it "
                "exportable."
            ),
            queue=queue,
            priority="high" if (stale or filled.below_capacity) else "normal",
            flow_id=FLOW_ID,
            run_id=ctx.record.id,
            case_ref=ctx.record.case_ref or state.day_key,
            refs=[params.source_ref] + (
                [state.day_ref] if state.day_ref else []
            ),
            evidence=[
                TaskEvidence(label="Department", value=state.department),
                TaskEvidence(label="Day", value=state.schedule_date),
                TaskEvidence(
                    label="Rows",
                    value="; ".join(
                        f"{row.sequence:g}. {row.job_label or row.job_number} "
                        f"{row.labor_hours:g} h ({row.row_kind}, "
                        f"{row.placed_by})"
                        for row in filled.rows
                    ) or "none",
                ),
                TaskEvidence(
                    label="Basis",
                    value="; ".join(
                        f"{row.job_number}: {row.basis}"
                        for row in filled.rows if row.basis
                    ) or "—",
                ),
                TaskEvidence(
                    label="Total against capacity",
                    value=(
                        f"{filled.total_hours:,.1f} h · "
                        f"{_limit_sentence(limits)}"
                    ),
                ),
                TaskEvidence(
                    label="Capacity row chosen", value=limits.row_note,
                ),
                TaskEvidence(
                    label="Incentive", value=filled.incentive_sentence,
                ),
                TaskEvidence(
                    label="Out of the hole",
                    value=(
                        ("yes" if filled.out_of_the_hole else "no")
                        + (
                            f" — the governed figures cross zero at "
                            f"{limits.crossing:,.2f} h"
                            if limits.crossing is not None else
                            " — the page carries no incentive figures for "
                            "this department"
                        )
                    ),
                ),
                TaskEvidence(
                    label="Headcount",
                    value=(
                        f"{state.headcount:g}"
                        + (" (stale)" if stale else "")
                        + f" — {state.headcount_note}"
                    ),
                ),
                TaskEvidence(
                    label="Deferred",
                    value="; ".join(
                        f"{row.job_number}: {row.reason}"
                        for row in filled.deferred
                    ) or "nothing",
                ),
                TaskEvidence(label="Review round", value=str(round_number)),
                TaskEvidence(label="Routed to this desk because",
                             value=routing_note),
            ],
            proposal=TaskProposal(
                action="mark-day-approved",
                target="production-records",
                summary=(
                    f"Approve {state.department}'s day for "
                    f"{state.schedule_date}: {len(filled.rows)} row(s), "
                    f"{filled.total_hours:,.1f} h. Approving moves the day to "
                    "`approved` and unlocks "
                    f"`GET /api/v1/schedule/export?week="
                    f"{_iso_week(state.schedule_date)}&department="
                    f"{state.department}`."
                ),
                fields=[
                    {
                        "path": f"rows.{row.key}",
                        "job_number": row.job_number,
                        "job_label": row.job_label,
                        "labor_hours": row.labor_hours,
                        "row_kind": row.row_kind,
                        "placed_by": row.placed_by,
                        "basis": row.basis,
                    }
                    for row in filled.rows
                ],
            ),
            resumes=(
                "approving marks the day approved and makes it exportable; "
                "correcting re-fills what is left around the rows you touched "
                "and asks again; rejecting leaves the day unapproved and "
                "unexportable"
            ),
            created_at_iso=utcnow().isoformat(),
        )

    def resume_for_task(self, task: Task, principal: Principal | None = None):
        """`principal` is the human who decided, carried to the commit.

        The Gateway checks the committer at the write, so the deciding human
        has to reach it — a uid recovered from `decided_by` afterwards would
        name the right person and prove nothing about their authority.
        """
        if task.flow_id != FLOW_ID or self.engine is None:
            return None
        return self.engine.resume_for_task(task.id, principal=principal)

    def apply_task_decision(
        self, task: Task, principal: Principal | None = None,
    ):
        """The console's door, shaped like every other flow's."""
        return self.resume_for_task(task, principal)

    # -- a2: the instant a day becomes exportable --------------------------

    def mark_day_approved(
        self,
        state: DayState,
        decided_by: str,
        run_id: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        """`status → approved`, with who signed it and when.

        Claimed as `human:` and not as `flow:`, which is the whole point: a
        person's approval outranks every later `flow:` claim on the same field
        in the ontology's precedence, so no re-fill can quietly un-approve a
        day somebody signed, and no re-fill can quietly approve one nobody did.
        """
        graph = writer if writer is not None else self.graph
        source = f"human:{decided_by}" if decided_by else f"flow:{FLOW_ID}"
        at = utcnow().isoformat()
        day = graph.upsert(
            onto.SCHEDULE_DAY, state.day_key,
            [
                Claim(field="status", value="approved", source=source),
                Claim(field="approved_by", value=decided_by, source=source),
                Claim(field="approved_at", value=at, source=source),
            ],
            label=f"{state.department} {state.schedule_date}",
            provenance=f"approved by {decided_by or 'nobody'} via {run_id}",
        )
        log.info(
            "Department day approved",
            extra={
                "runId": run_id, "department": state.department,
                "day": state.schedule_date, "decidedBy": decided_by,
            },
        )
        return {
            "day_ref": day.ref(), "day_key": state.day_key,
            "status": "approved", "approved_by": decided_by,
            "approved_at": at,
        }


# ---------------------------------------------------------------------------
# Small readers
# ---------------------------------------------------------------------------


def day_key(department: str, schedule_date: str) -> str:
    """`<department slug>:<date>` — the natural key the ontology declares."""
    return f"{department}:{schedule_date}"


def _candidate_of(job, task) -> Candidate:
    """One `JobTask` and its `Job`, as the fill's input."""
    props = job.props
    demand = task.props
    return Candidate(
        job_key=job.natural_key,
        job_id=job.id,
        job_ref=job.ref(),
        job_number=str(props.get("job_number") or ""),
        label=job.label or job.natural_key,
        packet_label=str(props.get("packet_label") or ""),
        load_date=str(props.get("load_date") or ""),
        week_position=_optional_number(props.get("week_position")),
        release_state=str(props.get("release_state") or ""),
        hold_reason=str(props.get("hold_reason") or ""),
        combined_with=str(props.get("combined_with") or ""),
        upstream_done=[
            str(slug) for slug in (props.get("upstream_done") or [])
        ],
        task_key=task.natural_key,
        task_ref=task.ref(),
        labor_hours=float(demand.get("labor_hours") or 0.0),
        hours_basis=str(demand.get("hours_basis") or ""),
        demand_summary=str(demand.get("demand_summary") or ""),
        source_page=str(demand.get("source_page") or ""),
        out_of_band=str(demand.get("out_of_band") or "") == "yes",
    )


def _optional_number(value) -> float | None:
    """A claimed number, or `None` when nobody claimed one."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _working(employee) -> bool:
    """Whether this row is somebody the department actually has today."""
    if employee.status != "active":
        return False
    return str(employee.props.get("status") or "").strip().lower() \
        not in _NOT_WORKING


def _find_team(graph: GraphService, hris_name: str, slug: str):
    """The department's `Team`, by the workforce system's name or the slug."""
    teams = graph.find(onto.TEAM)
    wanted = (hris_name or "").strip().lower()
    if wanted:
        for team in teams:
            if str(team.props.get("name") or "").strip().lower() == wanted:
                return team
    for team in teams:
        if str(team.props.get("slug") or team.natural_key) == slug:
            return team
    return None


def _hours_since(at_iso: str) -> float | None:
    """How old a timestamp is, in hours, or None when there is not one."""
    if not at_iso:
        return None
    try:
        moment = datetime.fromisoformat(at_iso.replace("Z", "+00:00"))
    except ValueError:
        stamp = _iso_date(at_iso)
        if stamp is None:
            return None
        moment = datetime.combine(
            stamp, datetime.min.time(), tzinfo=timezone.utc
        )
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (utcnow() - moment).total_seconds() / 3600.0


def _job_key_of(graph: GraphService, entry_id: str) -> str:
    for _link, job in graph.neighbors(entry_id, "scheduled_for", "out"):
        return job.natural_key
    return ""


def _job_id_of(graph: GraphService, entry_id: str) -> str:
    for _link, job in graph.neighbors(entry_id, "scheduled_for", "out"):
        return job.id
    return ""


def _limit_sentence(limits: Limits) -> str:
    """The three governed numbers, in the page's own words."""
    parts = []
    if limits.fills_by_incentive and limits.crossing is not None:
        parts.append(
            f"an incentive that crosses zero at {limits.crossing:,.2f} h"
        )
    if limits.goal_max is not None:
        parts.append(f"a goal max of {limits.goal_max:,.1f} h")
    else:
        parts.append("no goal max on the page")
    if limits.absolute_max is not None:
        parts.append(f"an absolute max of {limits.absolute_max:,.1f} h")
    else:
        parts.append("no absolute max")
    parts.append(f"a fudge of {limits.fudge:,.1f} h")
    return f"`{limits.row or limits.department}`: " + ", ".join(parts)


def _iso_week(schedule_date: str) -> str:
    """`YYYY-Www` for a date — the export's own week parameter."""
    parsed = _iso_date(schedule_date)
    if parsed is None:
        return ""
    year, week, _day = parsed.isocalendar()
    return f"{year}-W{week:02d}"


def build_department_scheduling_service(
    graph: GraphService,
    knowledge,
    tasks: TaskService,
    engine: RunEngine | None = None,
    gateway: ActionGateway | None = None,
    dispatcher: Dispatcher | None = None,
) -> DepartmentSchedulingService:
    return DepartmentSchedulingService(
        graph, knowledge, tasks, engine, gateway, dispatcher
    )


__all__ = [
    "CONSOLIDATED",
    "DAY_SCHEDULED_EVENT",
    "DEPARTMENT_DAY_EVENT",
    "GIRTS",
    "MAX_REVIEW_ROUNDS",
    "NAILING",
    "POLICY_ASSET_ID",
    "STANDARD",
    "SUPERVISOR_QUEUE",
    "Candidate",
    "DayState",
    "DepartmentSchedulingService",
    "Fill",
    "Limits",
    "Row",
    "Verdict",
    "build_department_scheduling_service",
    "choose_limits",
    "consolidate_loading",
    "day_key",
    "deferrals",
    "fill",
    "judge",
    "label_for",
    "plant_flow_principal",
    "split_rows",
]
