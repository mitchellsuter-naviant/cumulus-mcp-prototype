"""The plant window closes, and the graph answers which jobs are in play.

**Windowed, and its per-case sibling is `shipping-list-extraction`.** The unit
of work is a pass over the job book rather than a row: nothing arrived, a
window closed, and the standing question is asked of the graph — *which jobs
does the job system hold that clear every governed eligibility clause?* Bulk
counts come out, not gates: a job the clauses exclude is a counter with a named
reason on it, never a Task somebody has to answer before the other eleven jobs
can move.

**What it emits is per record, and that is the seam rather than a
contradiction.** `JobEligible` carries one job, the Trigger table turns it into
one extraction run, and that run is where per-case commitment happens. The
batch stops being a batch exactly there — `ap_intake`'s shape, and its named
cost too: the window is not acknowledged until every extraction it raised has
run or suspended, which is what `- jobs per eligibility window: 15` bounds.
What the bound does not take on is still eligible, is reported as deferred, and
is picked up by the next window.

**Nothing eligible is not a fault.** The governed page says so in as many
words: an eligible set of zero is *informational* — a slow week — and the only
thing that raises an alarm is finding **no jobs at all**, which means the feed
stopped rather than that the plant is quiet. That is the one Task this flow can
raise, it is an aggregate, and it goes to the operations desk because a feed
that stopped answering is nobody's business decision.

**It writes nothing.** No `Action`, no Gateway, no claim: the eligibility
verdict is the run's own journal and the events it emits. `JobTask` rows are
created by the extraction and by nothing else, and a `release_state` claim is
`job-sync`'s — this flow reads the words that feed already translated against
the governed vocabulary table and never re-translates the estate's `"Yes"` for
itself.
"""

from __future__ import annotations

import logging
from datetime import date

from pydantic import BaseModel, Field

from ... import routing
from ...auth import flow_principal
from ...dispatch import Dispatcher, InMemoryEventLog
from ...graph import GraphService
from ...models import utcnow
from ...ontology import names as onto
from ...runs import RunContext, RunEngine
from ...task_queue import Task, TaskEvidence, TaskService
from ..department_scheduling.parameters import (
    POLICY_ASSET_ID,
    PlantParameters,
    read_plant_parameters,
)
from . import DEFAULT_VERSION, FLOW_ID

log = logging.getLogger("cumulus.plant_eligibility")

#: The fact that starts it: a window closed over the book the jobs feed wrote.
#: Not a delivery — nothing arrived — which is the same shape
#: `IdentityWindowClosed` has.
PLANT_WINDOW_EVENT = "PlantWindowClosed"

#: One per eligible job, bound to `shipping-list-extraction` in
#: `dispatch.TRIGGERS`, which is where the per-case response is declared.
JOB_ELIGIBLE_EVENT = "JobEligible"

#: One per (department, business day), bound to `department-scheduling` — which
#: lands with Lane 5. Until it does this emission binds nothing, and
#: `Dispatcher.emit` handles that by logging "Event bound no Trigger" and
#: returning no runs. It is still emitted: the fact is true whether or not
#: anything answers it yet, and the log is where a reader sees that nothing did.
DEPARTMENT_DAY_EVENT = "DepartmentDayOpened"

#: Who this flow acts as. **No queue**, and no Action either: this flow commits
#: nothing, so there is nothing for a queue binding to authorize. The one Task
#: it raises goes to the operations desk by name.
PLANT_FLOW = flow_principal(FLOW_ID)


class Candidate(BaseModel):
    """One job the window is going to judge, as the graph holds it.

    The selection step's output, so it is also what a person reading the
    journal sees before any clause ran — which is why the graph ref and the
    three translated words are on it and not only the number.

    `week_position` is `None` when the graph carries no claim at all, which is
    a different fact from the governed `0`: zero is the job system placing a
    job beyond Week 3, and `None` is nobody having said.
    """

    job_key: str = ""
    job_id: str = ""
    job_ref: str = ""
    job_number: str = ""
    label: str = ""
    source: str = ""
    plant_site: str = ""
    status: str = ""
    customer_name: str = ""
    load_date: str = ""
    week_position: float | None = None
    release_state: str = ""
    hold_reason: str = ""


class Verdict(BaseModel):
    """One job, judged — and the clause that judged it.

    `clause` is the machine-readable name of the governed line that excluded
    the job, so the journal can count exclusions by reason; `reason` is the
    sentence a supervisor reads. Both, because "why was 260118 not scheduled"
    has to be answerable as a clause and as English.
    """

    job_key: str = ""
    job_number: str = ""
    label: str = ""
    load_date: str = ""
    eligible: bool = False
    clause: str = ""
    reason: str = ""


class DepartmentDay(BaseModel):
    """One (department, business day) this window opened, and how it got there."""

    department: str = ""
    schedule_date: str = ""
    lead_time_days: int = 0
    job_keys: list[str] = Field(default_factory=list)
    #: True when the governed lead-time day was already past and the day opened
    #: on the first business day available instead. Reported rather than
    #: silently corrected: a department starting late is a fact about the
    #: plant's week.
    late: bool = False
    note: str = ""


# ---------------------------------------------------------------------------
# r1 — the Rule. Pure: it reads its arguments and nothing else.
# ---------------------------------------------------------------------------


def judge(
    candidates: list[Candidate], params: PlantParameters, as_of: date
) -> list[Verdict]:
    """Every candidate against every governed clause, in the page's own order.

    A job must clear **all** of the clauses; a job that fails one is excluded
    for **that** named reason and stops there, which is what the governed page
    says and what makes an exclusion count meaningful. Reporting five reasons
    for one job would make "how many were held" unanswerable.

    Pure in the doctrine's sense: the clauses arrive parsed, the words arrive
    already translated by `job-sync` against the governed vocabulary, and the
    date arrives from the window. Nothing here fetches, so this is replayable
    against a fixture with no world behind it.
    """
    eligibility = params.eligibility
    positions = set(eligibility.week_positions)
    out: list[Verdict] = []
    for candidate in candidates:
        verdict = Verdict(
            job_key=candidate.job_key, job_number=candidate.job_number,
            label=candidate.label, load_date=candidate.load_date,
        )
        clause, reason = _first_failure(candidate, params, positions, as_of)
        verdict.eligible = not clause
        verdict.clause = clause
        verdict.reason = reason or (
            "clears every governed eligibility clause"
        )
        out.append(verdict)
    return out


def _first_failure(
    candidate: Candidate,
    params: PlantParameters,
    positions: set[int],
    as_of: date,
) -> tuple[str, str]:
    """The first governed clause this job fails, named, or `("", "")`."""
    eligibility = params.eligibility

    # 1 — `load date on or after: today`.
    load_date = _iso_date(candidate.load_date)
    if load_date is None:
        return "no-load-date", (
            "the job system holds no load date, and every plant clause is "
            "counted from one"
        )
    if load_date < as_of:
        return "load-date-past", (
            f"the load date {candidate.load_date} is before {as_of.isoformat()}, "
            f"and the governed clause is `load date on or after: "
            f"{eligibility.load_date_on_or_after}`"
        )

    # 2 — `eligible week positions: 1, 2, 3`.
    if positions:
        position = candidate.week_position
        if position is None:
            return "no-week-position", (
                "the job system does not place this job in a production week, "
                f"and the governed set is {_named(positions)}"
            )
        if int(position) not in positions:
            return "week-position-out-of-set", (
                f"the job system puts this job in week position {position:g}, "
                f"and the governed set is {_named(positions)} — a 0 is a job "
                "the screen puts beyond the last week, which is not an error"
            )

    # 3 — `release required: out to yard`. The state, not the estate's `"Yes"`:
    # `job-sync` translated it against the governed vocabulary table and named
    # the revision it used on its own step.
    if eligibility.released_state:
        if candidate.release_state != eligibility.released_state:
            return "not-released", (
                f"the job system has this job as `{candidate.release_state or 'unset'}` "
                f"and the governed clause requires `{eligibility.released_state}` "
                f"— the plant's word for it is `{eligibility.release_required}`"
            )

    # 4 — `hold excludes: do not fab`. A separate clause from the release,
    # because the two are independent flags in the source and a job can carry
    # both: a released job somebody then put a hold on is held.
    if candidate.hold_reason:
        return "held", (
            f"the job is held: `{candidate.hold_reason}`, and the governed "
            f"clause is `hold excludes: {eligibility.hold_excludes}`"
            + (
                "" if candidate.hold_reason == eligibility.excluding_hold else
                " — this is a hold the page does not name, and a hold nobody "
                "recognizes is still a hold"
            )
        )

    # 5 — `business days only: yes`. The plant operations screen is a list of
    # load dates and does not know the plant is shut; the governed calendar is
    # what does.
    if eligibility.business_days_only and not params.calendar.is_business_day(load_date):
        return "not-a-business-day", (
            params.calendar.why_not_a_business_day(load_date)
            or f"{candidate.load_date} is not a plant business day"
        )
    return "", ""


def _named(positions: set[int]) -> str:
    return ", ".join(str(value) for value in sorted(positions))


def _iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat((value or "").strip()[:10])
    except ValueError:
        return None


def exclusions(verdicts: list[Verdict]) -> dict[str, int]:
    """Excluded jobs counted by the clause that excluded them."""
    counts: dict[str, int] = {}
    for verdict in verdicts:
        if not verdict.eligible:
            counts[verdict.clause] = counts.get(verdict.clause, 0) + 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


class PlantEligibilityService:
    """Reads the job book, judges it, and raises what the clauses let through.

    Holds no directory of its own: what it produces is Events on the log and a
    journal on the run. The record of a job is the job system's, mirrored by
    `job-sync`, and a second table here would be a second account of it.
    """

    def __init__(
        self,
        graph: GraphService,
        knowledge,  # KnowledgeService — duck-typed to avoid an import cycle
        tasks: TaskService,
        engine: RunEngine | None = None,
        dispatcher: Dispatcher | None = None,
    ) -> None:
        self.graph = graph
        self.knowledge = knowledge
        self.tasks = tasks
        self.engine = engine
        # Never bypassed, for the reason `ap_intake` gives at the same line: a
        # run nothing recorded a cause for is a governance problem rather than
        # a wiring one — and this flow's whole output is events.
        self.dispatcher = dispatcher or (
            Dispatcher(engine, InMemoryEventLog()) if engine is not None else None
        )
        # No Gateway, and the omission is the design: this flow commits
        # nothing. The eligibility verdict lives in the journal, and every
        # graph write in the plant spine belongs to a later flow.
        if engine is not None:
            from .. import shipped_versions
            from ... import modelcall

            for number, module in sorted(shipped_versions(FLOW_ID).items()):
                # `metered` even though this body makes no ModelCall: the
                # extraction runs it starts are driven from inside it, and a
                # context that stopped at this frame would leave their ledger
                # rows without run coordinates.
                engine.register(
                    FLOW_ID, modelcall.metered(module.build_body(self)),
                    version=number, default=(number == DEFAULT_VERSION),
                )

    # -- the door -----------------------------------------------------------

    def sweep(self, actor: str = "system:scheduler", as_of: str = "") -> object:
        """Close a plant window. The Trigger table is what answers.

        Through the Dispatcher rather than by calling the body, so the
        causation is logged before the run exists — the ordering the doctrine
        asks for, and the one every other windowed flow here already keeps.
        """
        if self.dispatcher is None:
            raise RuntimeError("The plant eligibility sweep has no dispatcher wired")
        return self.dispatcher.emit(
            PLANT_WINDOW_EVENT,
            {"window": utcnow().isoformat(), "as_of": as_of},
            actor=actor,
        )

    # -- governed reads -----------------------------------------------------

    def read_parameters(self) -> PlantParameters:
        return read_plant_parameters(self.knowledge)

    # -- step internals -----------------------------------------------------

    def candidates(self, params: PlantParameters) -> list[Candidate]:
        """Every `Job` the graph holds, in the governed order.

        The graph and not the delivery: a work list scoped to one delivery
        skews and a standing question does not — `ap_intake`'s argument, and
        the reason this flow is woken by a *window* rather than by a feed.

        Sorted by load date ascending because the page says so
        (`- sort candidates by: load date ascending`); a job with no load date
        sorts last, since a date nobody wrote cannot be earlier than one
        somebody did.
        """
        found = [
            _candidate_of(record) for record in self.graph.find(onto.JOB)
        ]
        ascending = "desc" not in (params.eligibility.sort_by or "").lower()
        found.sort(
            key=lambda item: (
                not item.load_date, item.load_date, item.job_number
            ),
            reverse=not ascending,
        )
        return found

    def raise_plant_fault(self, ctx: RunContext, params: PlantParameters) -> str:
        """The aggregate, and the only Task this flow can raise.

        **No `Job` rows at all.** Not "nothing eligible" — the governed page is
        explicit that an empty eligible set is informational and a slow week is
        not a fault. An empty *book* is a feed that stopped, which is an
        operations question and nobody's business decision.

        Idempotent on the run id, because a re-entered window must not open a
        second copy of the same fault.
        """
        task_id = f"task_{ctx.record.id}_plant"
        if self.tasks.get(task_id) is not None:
            return task_id
        self.tasks.create(Task(
            id=task_id,
            kind="exception",
            title="The plant window found no jobs at all",
            summary=(
                "This window asked the graph for the job book and it is "
                "empty. Nothing has been asserted and nothing has been lost — "
                "no job was excluded, because there was no job to exclude. "
                "What this needs is somebody who can say why the production "
                "system stopped delivering jobs. An empty *eligible* set "
                "would be a slow week and would raise nothing; an empty book "
                "is a feed."
            ),
            queue=routing.OPS_QUEUE,
            priority="high",
            flow_id=FLOW_ID,
            flow_version=ctx.record.flow_version,
            run_id=ctx.record.id,
            refs=[params.source_ref],
            evidence=[
                TaskEvidence(label="Jobs in the graph", value="0"),
                TaskEvidence(label="Governed parameters", value=params.source_ref),
                TaskEvidence(
                    label="Empty eligible set",
                    value=params.eligibility.empty_set_is or "informational",
                ),
            ],
            resumes="nothing resumes; the next window re-reads the book",
            created_at_iso=utcnow().isoformat(),
        ))
        log.error(
            "A plant window found no jobs at all", extra={"runId": ctx.record.id}
        )
        return task_id

    def raise_eligible(
        self, candidates: dict[str, Candidate], taken: list[Verdict]
    ) -> dict:
        """One `JobEligible` per eligible job, and one extraction run each.

        A response starting another response is fan-out, which `dispatch` says
        needs a queue rather than a call and does not have one — so this is a
        synchronous call, and the cost is named where it is paid: the window is
        not acknowledged until every case it raised has run or suspended.

        The payload is deliberately thin. The extraction reads the job out of
        the graph for itself, because by the time it runs the job may have been
        re-landed and a payload copied from here would be a snapshot competing
        with the record.
        """
        if self.dispatcher is None:
            raise RuntimeError("The plant eligibility sweep has no dispatcher wired")
        started: list[dict] = []
        failed: list[dict] = []
        suspended: list[dict] = []
        for verdict in taken:
            candidate = candidates.get(verdict.job_key)
            if candidate is None:
                continue
            dispatch = self.dispatcher.emit(
                JOB_ELIGIBLE_EVENT,
                {
                    "job_key": candidate.job_key,
                    "job_number": candidate.job_number,
                    "plant_site": candidate.plant_site,
                    "load_date": candidate.load_date,
                    "source": candidate.source,
                },
                actor=f"flow:{FLOW_ID}",
                case_ref=candidate.job_key,
            )
            for run in dispatch.runs:
                row = {
                    "run_id": run.id, "status": run.status,
                    "job_key": candidate.job_key,
                    "job_number": candidate.job_number,
                }
                # A case that failed is this window's report, not its
                # exception: `RunEngine._drive` never raises, so the only way a
                # broken job could stop the other eleven is if this loop
                # decided to let it.
                if run.status == "failed":
                    failed.append(row)
                elif run.status == "suspended":
                    suspended.append(row)
                else:
                    started.append(row)
        return {
            "raised": len(started) + len(failed) + len(suspended),
            "succeeded": started, "suspended": suspended, "failed": failed,
        }

    def department_days(
        self,
        candidates: dict[str, Candidate],
        taken: list[Verdict],
        params: PlantParameters,
        as_of: date,
    ) -> tuple[list[DepartmentDay], list[str]]:
        """The (department, day) pairs the extractions just made real.

        Read off the `JobTask` rows in the graph rather than off the department
        roster, and **after** `e1` for exactly that reason: a day is opened for
        a department that has work, and what says a department has work is a
        demand row the extraction wrote. A job whose extraction suspended has
        no rows yet, so no day is opened for it and the count says so.

        The date is the governed lead time counted back in business days from
        the load date. Where that day is already past the day opens on the
        first business day available and is marked `late`: the farthest-out
        window bounds how *early* work may be placed, not how late, and
        deferring a job whose lead time has run out would be this flow
        declining to schedule work the plant still has to do.
        """
        days: dict[tuple[str, str], DepartmentDay] = {}
        deferred: list[str] = []
        for verdict in taken:
            candidate = candidates.get(verdict.job_key)
            if candidate is None:
                continue
            record = self.graph.get(candidate.job_id)
            if record is None:
                continue
            load_date = _iso_date(candidate.load_date)
            for _link, task in self.graph.neighbors(
                record.id, "has_job_task", "out"
            ):
                department = str(task.props.get("department") or "")
                if not department or load_date is None:
                    continue
                lead = params.lead_time_for(department)
                assumed = ""
                if lead is None:
                    # The page conditions this department's lead time —
                    # Laminates is `treated` or `non-treated` — and a ticket
                    # does not say which. The nearest row is the placement no
                    # row forbids, and the day says out loud which condition it
                    # was dated by rather than quietly choosing one.
                    lead = params.nearest_lead_time_for(department)
                    assumed = lead.condition if lead is not None else ""
                if lead is None:
                    deferred.append(
                        f"{department}: the governed lead-time table has no "
                        f"row for this department, so no day was opened for "
                        f"{candidate.job_number}"
                    )
                    continue
                target = params.calendar.business_days_before(
                    load_date, lead.lead_time_days
                )
                late = target < as_of
                if late:
                    target = (
                        as_of if params.calendar.is_business_day(as_of)
                        else params.calendar.next_business_day(as_of)
                    )
                key = (department, target.isoformat())
                day = days.get(key)
                if day is None:
                    day = DepartmentDay(
                        department=department,
                        schedule_date=target.isoformat(),
                        lead_time_days=lead.lead_time_days,
                        late=late,
                        note=" · ".join(part for part in (
                            (
                                f"the governed lead time of "
                                f"{lead.lead_time_days} business day(s) put "
                                "this before the window's own day, so it opens "
                                "on the first business day available"
                            ) if late else "",
                            (
                                f"the page conditions this department's lead "
                                f"time and the ticket does not say which, so "
                                f"the nearest row (`{assumed}`) dated it"
                            ) if assumed else "",
                        ) if part),
                    )
                    days[key] = day
                if candidate.job_key not in day.job_keys:
                    day.job_keys.append(candidate.job_key)
        return [days[key] for key in sorted(days)], deferred

    def open_department_days(self, days: list[DepartmentDay], run_id: str) -> dict:
        """Emit one `DepartmentDayOpened` per (department, day).

        `.one()` is deliberately **not** called on these: until Lane 5 lands
        `department-scheduling` and its Trigger, nothing is bound to this fact
        and `Dispatcher.emit` returns a Dispatch with no runs (having logged
        "Event bound no Trigger"). Asking a Dispatch for its single run would
        raise `Undispatched` and fail a window whose only remaining job is to
        say what it opened.
        """
        if self.dispatcher is None:
            raise RuntimeError("The plant eligibility sweep has no dispatcher wired")
        started: list[str] = []
        for day in days:
            dispatch = self.dispatcher.emit(
                DEPARTMENT_DAY_EVENT,
                {
                    "department": day.department,
                    "schedule_date": day.schedule_date,
                    "job_keys": list(day.job_keys),
                    "lead_time_days": day.lead_time_days,
                    "late": day.late,
                    "opened_by": run_id,
                },
                actor=f"flow:{FLOW_ID}",
                case_ref=f"{day.department}:{day.schedule_date}",
            )
            started.extend(run.id for run in dispatch.runs)
        return {"opened": len(days), "runs": started}


def _candidate_of(record) -> Candidate:
    """One graph row as a candidate, reading the words `job-sync` translated."""
    props = record.props
    return Candidate(
        job_key=record.natural_key,
        job_id=record.id,
        job_ref=record.ref(),
        job_number=str(props.get("job_number") or ""),
        label=record.label or record.natural_key,
        source=str(props.get("source") or ""),
        plant_site=str(props.get("plant_site") or ""),
        status=str(props.get("status") or ""),
        customer_name=str(props.get("customer_name") or ""),
        load_date=str(props.get("load_date") or ""),
        week_position=_optional_number(props.get("week_position")),
        release_state=str(props.get("release_state") or ""),
        hold_reason=str(props.get("hold_reason") or ""),
    )


def _optional_number(value) -> float | None:
    """A claimed number, or `None` when nobody claimed one.

    `None` and `0.0` are different facts here and the Rule reads them
    differently: the governed page says a week position of 0 is a job the
    screen puts beyond the last week, which is not an error, where no claim at
    all is nobody having said.
    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_eligibility_service(
    graph: GraphService,
    knowledge,
    tasks: TaskService,
    engine: RunEngine | None = None,
    dispatcher: Dispatcher | None = None,
) -> PlantEligibilityService:
    return PlantEligibilityService(graph, knowledge, tasks, engine, dispatcher)


__all__ = [
    "Candidate",
    "DEPARTMENT_DAY_EVENT",
    "DepartmentDay",
    "JOB_ELIGIBLE_EVENT",
    "PLANT_WINDOW_EVENT",
    "POLICY_ASSET_ID",
    "PlantEligibilityService",
    "Verdict",
    "build_eligibility_service",
    "exclusions",
    "judge",
]
