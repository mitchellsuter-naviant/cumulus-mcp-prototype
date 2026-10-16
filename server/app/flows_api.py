"""REST surface for the Flows section: the registry, its versions, telemetry.

Reads need any authenticated role — watching how a workstream runs is a
Workspace-grade capability, not an admin one. Registering, editing and pausing
are admin+, and — like teams — a minted key may read but never reshape the
registry it authenticates against.

Grading a flow is not here. `/api/v1/evals` owns that: it executes the flow in
an isolated world and grades what it wrote, which is a different kind of thing
from a registry entry and reads a different store.
"""

import logging

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import Principal, get_principal, require_human, require_reader, require_role
from .deps import get_flow_service
from .flows import (
    CostBasis,
    Recommendation,
    RoiBasis,
    StepDiff,
    UsageDay,
    FlowDefinition,
    FlowExistsError,
    FlowNotFoundError,
    FlowStatus,
    FlowStep,
    FlowVersion,
    cost_basis,
    eval_case_count,
    fleet_flow_ids,
    fleet_runs,
    flow_runs,
    open_tasks,
    open_tasks_by_flow,
    roi_basis,
    usage_series,
)
from .runs import RunRecord

log = logging.getLogger("cumulus.flows.api")

# What a *row* of the fleet can be in. `undeclared` is the absence of a
# definition to have a lifecycle state, so it is filterable and readable but
# deliberately absent from `FlowUpdate`: it is not a state a definition can be
# moved into.
FleetStatus = FlowStatus | Literal["undeclared"]

router = APIRouter(prefix="/api/v1/flows", tags=["flows"])


# ---- public shapes ---------------------------------------------------------


# What the Health column says about a flow. Five words, not the four a reader
# might expect, because a flow that has finished nothing is not healthy and
# there is no fourth word that covers it: claiming HEALTHY over an empty
# sample makes a flow nobody has run indistinguishable from one that is
# genuinely fine. The fifth is named for what it is — `no_runs` — rather than
# for a state of mind, which is what the earlier `idle` failed to say.
FlowHealth = Literal["healthy", "degraded", "failed", "draft", "no_runs"]

# The bar a flow has to clear to read as healthy, and the one below which it
# is not merely degraded. 0.95 is the same bar the attention list has always
# used; 0.5 is the point at which a flow is failing more often than a coin.
HEALTHY_AT = 0.95
FAILED_BELOW = 0.5


def _health(status: str, completed: int, failed: int) -> FlowHealth:
    """One word for how a flow is doing, over the window it was asked about.

    Lifecycle beats measurement: a draft has not been let out to run, so its
    success rate is not a finding about it. Everything else is judged on
    finished runs only — a run still waiting on a person is evidence of
    neither outcome, and counting it either way moves this badge without
    anything having happened.
    """
    if status == "draft":
        return "draft"
    finished = completed + failed
    if finished == 0:
        return "no_runs"
    rate = completed / finished
    if rate < FAILED_BELOW:
        return "failed"
    if rate < HEALTHY_AT:
        return "degraded"
    return "healthy"


class FlowSummaryPublic(BaseModel):
    """One row of the fleet, whether or not anyone authored a definition for it.

    Five of the seven flows that write the graph are inbound sync feeds with
    no `FlowDefinition`. They run, they write, and the fleet used to omit them
    entirely — so the console reported a two-flow world and a console that
    cannot see five of its seven flows is worse than one showing a row with
    empty columns. `has_definition` is how a row says which kind it is; the
    definition-only fields stay at their defaults on a feed.
    """

    id: str
    name: str
    status: FleetStatus
    has_definition: bool = True
    description: str
    team: str
    tags: list[str]
    version: int
    updated_at: str
    # Every figure below is over `window_days`, which the caller chooses. The
    # fields carry no number in their names on purpose: a `runs_30d` holding
    # ninety days of runs is the kind of quiet lie this codebase keeps
    # deleting, and the console now lets an operator change the window.
    window_days: int = 30
    runs_window: int = 0
    cost_window_usd: float = 0.0
    # Of finished runs. In-flight runs are evidence of neither outcome.
    success_window: float | None = None
    # Failed runs in the window, beside the rate rather than instead of it:
    # three failures out of four and three hundred out of four hundred are the
    # same percentage and not the same finding.
    errors_window: int = 0
    cost_basis_window: CostBasis = "no-model-calls"
    tasks_open: int = 0
    minutes_saved_window: float = 0.0
    # The verdict the console badges. See `_health`.
    health: FlowHealth = "no_runs"
    # What kind of fact starts this flow, and which one — read off the
    # export's own trigger step. Blank on a flow with no export, because
    # there is no step to read and a default would be an invention.
    trigger_kind: str = ""
    trigger_detail: str = ""
    # The last run this flow had, at any depth of history — not only inside
    # the window. "Last ran three months ago" is a finding; a blank because
    # the 30-day cut missed it is a hidden one.
    last_run_at: str = ""
    last_run_status: str = ""
    # Daily run counts for the last 14 days — the row sparkline.
    spark: list[int] = Field(default_factory=list)


class FlowDetailPublic(FlowSummaryPublic):
    steps: list[FlowStep] = Field(default_factory=list)
    queues: list[str] = Field(default_factory=list)
    workspace: str = ""
    roi_minutes_per_run: float = 0.0
    roi_hourly_rate_usd: float = 30.0
    model_budget_usd_per_run: float = 0.0
    authored_by: str = ""
    created_at: str = ""
    # Cases that grade this flow, counted off the eval registry. The wire name
    # predates the split and is kept: renaming a field the console reads is a
    # separate, coordinated change.
    golden_cases: int = 0
    # What the version selector needs without a second round trip.
    active_version_label: str = ""
    draft_version_label: str = ""
    version_count: int = 0


class FleetSeriesDay(BaseModel):
    """One day of the whole fleet.

    `completed` and `failed` ride along so a reader can derive that day's
    success rate without asking for every flow's series and summing them.
    In-flight runs are in `runs` and in neither of the other two: a run still
    waiting on a person is evidence of no outcome yet.
    """

    date: str
    runs: int
    cost_usd: float
    tasks_raised: int
    completed: int = 0
    failed: int = 0


# Why a flow is on the attention list. The `reason` sentence stays the thing a
# person reads; `kind` is what a client switches on, so a console rendering
# these as row tags does not have to pattern-match English.
AttentionKind = Literal["failure-rate", "no-eval-spec", "no-export"]


class FleetAttention(BaseModel):
    flow_id: str
    kind: AttentionKind
    reason: str


class FleetStats(BaseModel):
    """The fleet header, every figure read off stored runs.

    Three fields exist to keep a number from being read as more than it is:

    * `flows` vs `definitions` — the fleet is every flow that runs (seven);
      `definitions` is how many of those have a Tier-2 declarative export
      (two). The header used to show the second and call it the fleet.
    * `cost_basis` — whether the spend figure is a measurement or a hole. See
      `flows.cost_basis`; a $0.00 that means "nobody recorded it" must not
      read as "$0.00 spent".
    * `roi_basis` — the derivation of `hours_returned` and `roi_usd`, which
      are a real run count multiplied by a governed assumption. Nothing timed
      a person against these flows, and a dollar figure that does not say so
      will be read as though something did.
    """

    days: int
    # Every flow that runs: registered bodies, flows with stored runs, and
    # registered definitions.
    flows: int = 0
    definitions: int
    without_definition: int = 0
    active: int
    paused: int
    draft: int
    runs: int
    completed: int
    failed: int = 0
    in_flight: int = 0
    # Over finished runs only — a suspended run waiting on a person is not a
    # failure, and counting it as one was the easiest way to make this number
    # move without anything changing.
    success_rate: float | None = None
    model_spend_usd: float
    tokens: int
    model_calls: int
    priced_model_calls: int = 0
    cost_basis: CostBasis = "no-model-calls"
    hours_returned: float
    roi_usd: float
    roi_basis: RoiBasis = Field(default_factory=RoiBasis)
    tasks_open: int
    escalated: int
    series: list[FleetSeriesDay]
    attention: list[FleetAttention]


class FlowCreate(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]*$", max_length=60)
    name: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=600)
    team: str = Field(default="", max_length=60)
    tags: list[str] = Field(default_factory=list)
    steps: list[FlowStep] = Field(default_factory=list)
    queues: list[str] = Field(default_factory=list)
    workspace: str = ""
    roi_minutes_per_run: float = 0.0
    roi_hourly_rate_usd: float = 30.0
    model_budget_usd_per_run: float = 0.50


class FlowUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=600)
    status: FlowStatus | None = None
    team: str | None = Field(default=None, max_length=60)
    tags: list[str] | None = None
    roi_minutes_per_run: float | None = None
    roi_hourly_rate_usd: float | None = None
    model_budget_usd_per_run: float | None = None


class AppliedRecommendation(BaseModel):
    recommendation_id: str
    draft: FlowVersion
    summary: str


class FlowUsage(BaseModel):
    """One flow's window, summed from the same series it returns."""

    flow_id: str
    has_definition: bool = True
    days: int
    runs: int
    completed: int
    failed: int = 0
    in_flight: int = 0
    success_rate: float | None
    cost_usd: float
    tokens: int
    model_calls: int
    priced_model_calls: int = 0
    cost_basis: CostBasis = "no-model-calls"
    tasks_raised: int
    escalated: int
    minutes_saved: float
    roi_usd: float
    roi_basis: RoiBasis = Field(default_factory=RoiBasis)
    series: list[UsageDay]


# ---- helpers ---------------------------------------------------------------


def _label(flow_id: str) -> str:
    """A name for a flow nobody wrote a definition for."""
    return flow_id.replace("-", " ").capitalize()


def _task_scope(principal: Principal) -> tuple[str, ...] | None:
    """The queues whose open Tasks this principal is shown, or `None` for all.

    The same narrowing `tasks_api.task_stats` applies, deliberately: the Flows
    header and My Tasks are two readings of one queue, and an unscoped total
    here put "Awaiting judgment 21" on the same screen as an AE whose queue is
    clear. A principal that reads past its own work still sees the fleet total,
    because diagnosing the deployment is what that reach is for.
    """
    return None if principal.reads_beyond_self else principal.queues


def _trigger(defn: FlowDefinition | None) -> tuple[str, str]:
    """The kind of fact that starts this flow, and which one.

    Read off the declarative export's own trigger step rather than configured
    here: a flow woken by a schedule and one woken by an event are different
    machines, and the export is where that difference is written down. Every
    flow in this estate is currently event-driven; the schedule and webhook
    arms exist so an authored one renders as itself rather than as an event.
    """
    if defn is None:
        return "", ""
    step = next((s for s in defn.steps if s.type == "trigger"), None)
    if step is None:
        return "", ""
    for key, kind in (
        ("event", "event"),
        ("schedule", "schedule"),
        ("cron", "schedule"),
        ("webhook", "webhook"),
    ):
        if key in step.config:
            return kind, str(step.config[key])
    # A trigger step naming none of those still starts the flow. Its name is
    # the most specific true thing left to say about it.
    return "manual", step.name


def _last_run(runs: list[RunRecord]) -> tuple[str, str]:
    """When this flow last ran and how that run ended.

    Ordered on the run's own clock rather than on store order, and finished
    time preferred over start time so a long run reports when it landed. A
    run still going has no finish, so it reports when it began.
    """
    if not runs:
        return "", ""
    def when(run: RunRecord) -> str:
        return run.finished_at_iso or run.started_at_iso
    latest = max(runs, key=when)
    return when(latest), latest.status


def _rollup(
    flow_id: str,
    defn: FlowDefinition | None,
    runs: list[RunRecord],
    tasks_open: int,
    days: int = 30,
) -> FlowSummaryPublic:
    series = usage_series(runs, days, defn.roi_minutes_per_run if defn else 0.0)
    total = sum(d.runs for d in series)
    completed = sum(d.completed for d in series)
    errors = sum(d.failed for d in series)
    finished = completed + errors
    calls = sum(d.model_calls for d in series)
    priced = sum(d.priced_model_calls for d in series)
    trigger_kind, trigger_detail = _trigger(defn)
    last_run_at, last_run_status = _last_run(runs)
    return FlowSummaryPublic(
        id=flow_id,
        name=(defn.name or flow_id) if defn else _label(flow_id),
        status=defn.status if defn else "undeclared",
        has_definition=defn is not None,
        description=defn.description if defn else "",
        team=defn.team if defn else "",
        tags=defn.tags if defn else [],
        version=defn.version if defn else 0,
        updated_at=(
            defn.updated_at.isoformat()
            if defn
            else max((r.updated_at_iso for r in runs), default="")
        ),
        window_days=days,
        runs_window=total,
        cost_window_usd=round(sum(d.cost_usd for d in series), 2),
        success_window=round(completed / finished, 4) if finished else None,
        errors_window=errors,
        cost_basis_window=cost_basis(calls, priced),
        health=_health(
            defn.status if defn else "undeclared", completed, errors
        ),
        tasks_open=tasks_open,
        minutes_saved_window=round(sum(d.minutes_saved for d in series), 1),
        trigger_kind=trigger_kind,
        trigger_detail=trigger_detail,
        last_run_at=last_run_at,
        last_run_status=last_run_status,
        spark=[d.runs for d in series[-14:]],
    )


def _detail(
    flow_id: str,
    defn: FlowDefinition | None,
    eval_cases: int,
    queues: tuple[str, ...] | None = None,
) -> FlowDetailPublic:
    base = _rollup(flow_id, defn, flow_runs(flow_id), open_tasks(flow_id, queues))
    if defn is None:
        # A sync feed has runs and no map. Saying so beats a 404 on a row the
        # fleet list just showed, and beats inventing a definition for it.
        return FlowDetailPublic(**base.model_dump(), golden_cases=eval_cases)
    draft = defn.draft_version()
    return FlowDetailPublic(
        **base.model_dump(),
        steps=defn.steps,
        queues=defn.queues,
        workspace=defn.workspace,
        roi_minutes_per_run=defn.roi_minutes_per_run,
        roi_hourly_rate_usd=defn.roi_hourly_rate_usd,
        model_budget_usd_per_run=defn.model_budget_usd_per_run,
        authored_by=defn.authored_by,
        created_at=defn.created_at.isoformat(),
        golden_cases=eval_cases,
        active_version_label=defn.active_version().display(),
        draft_version_label=draft.display() if draft else "",
        version_count=len(defn.version_history()),
    )


def _get_or_404(flow_id: str) -> FlowDefinition:
    """For routes that act on a definition: editing, versioning, drafting."""
    defn = get_flow_service().get(flow_id)
    if defn is None:
        raise HTTPException(status_code=404, detail=f"Flow not found: {flow_id}")
    return defn


def _in_fleet_or_404(flow_id: str) -> FlowDefinition | None:
    """For read routes: a definition when there is one, else None if it runs.

    A flow the engine runs is a real flow. Refusing to report on it because
    nobody authored a Tier-2 export is how the console came to describe a
    seven-flow system as a two-flow one.
    """
    defn = get_flow_service().get(flow_id)
    if defn is None and flow_id not in fleet_flow_ids():
        raise HTTPException(status_code=404, detail=f"Flow not found: {flow_id}")
    return defn


# ---- fleet -----------------------------------------------------------------


@router.get("", response_model=list[FlowSummaryPublic])
def list_flows(
    q: str = "",
    status: FleetStatus | None = None,
    team: str | None = None,
    days: int = Query(default=30, ge=1, le=365),
    principal: Principal = Depends(get_principal),
) -> list[FlowSummaryPublic]:
    require_reader(principal)
    out = []
    needle = q.strip().lower()
    service = get_flow_service()
    runs_by_flow = fleet_runs()
    tasks_by_flow = open_tasks_by_flow(_task_scope(principal))
    for flow_id in fleet_flow_ids():
        defn = service.get(flow_id)
        row = _rollup(
            flow_id,
            defn,
            runs_by_flow.get(flow_id, []),
            tasks_by_flow.get(flow_id, 0),
            days,
        )
        if status is not None and row.status != status:
            continue
        if team is not None and row.team != team:
            continue
        if needle and needle not in " ".join(
            [row.id, row.name, row.description, row.team, *row.tags]
        ).lower():
            continue
        out.append(row)
    # Busiest first, then by spend, then by id. Spend led this list when it
    # was invented; with real journals most of it is zero, and a fleet ordered
    # by a column that reads zero everywhere has no order at all.
    out.sort(key=lambda w: (-w.runs_window, -w.cost_window_usd, w.id))
    return out


@router.get("/stats", response_model=FleetStats)
def fleet_stats(
    days: int = Query(default=30, ge=1, le=365),
    principal: Principal = Depends(get_principal),
) -> FleetStats:
    require_reader(principal)
    service = get_flow_service()
    defs = service.list()
    runs_by_flow = fleet_runs()
    tasks_by_flow = open_tasks_by_flow(_task_scope(principal))
    flow_ids = fleet_flow_ids()

    per_day: dict[str, FleetSeriesDay] = {}
    runs = completed = failed = tokens = calls = priced = escalated = 0
    tasks_open = no_assumption = 0
    cost = minutes = roi_usd = rate_weight = 0.0
    attention: list[FleetAttention] = []

    for flow_id in flow_ids:
        defn = service.get(flow_id)
        series = usage_series(
            runs_by_flow.get(flow_id, []),
            days,
            defn.roi_minutes_per_run if defn else 0.0,
        )
        d_runs = sum(d.runs for d in series)
        d_completed = sum(d.completed for d in series)
        d_failed = sum(d.failed for d in series)
        d_minutes = sum(d.minutes_saved for d in series)
        runs += d_runs
        completed += d_completed
        failed += d_failed
        cost += sum(d.cost_usd for d in series)
        tokens += sum(d.tokens for d in series)
        calls += sum(d.model_calls for d in series)
        priced += sum(d.priced_model_calls for d in series)
        escalated += sum(d.escalated for d in series)
        minutes += d_minutes
        tasks_open += tasks_by_flow.get(flow_id, 0)
        if defn and defn.roi_minutes_per_run:
            roi_usd += d_minutes / 60.0 * defn.roi_hourly_rate_usd
            rate_weight += d_completed * defn.roi_hourly_rate_usd
        else:
            # A flow with no ROI assumption returns no modelled hours. Its
            # runs are still real; they just do not belong in this figure.
            no_assumption += d_completed
        for day in series:
            key = day.date.isoformat()
            row = per_day.setdefault(
                key, FleetSeriesDay(date=key, runs=0, cost_usd=0.0, tasks_raised=0)
            )
            row.runs += day.runs
            row.cost_usd = round(row.cost_usd + day.cost_usd, 2)
            row.tasks_raised += day.tasks_raised
            row.completed += day.completed
            row.failed += day.failed
        d_finished = d_completed + d_failed
        if d_finished and (d_completed / d_finished) < 0.95:
            # The sample size travels with the rate. Three failures out of
            # four runs and three hundred out of four hundred are not the
            # same finding, and a bare percentage hides which one this is.
            attention.append(
                FleetAttention(
                    flow_id=flow_id,
                    kind="failure-rate",
                    reason=(
                        f"success {d_completed / d_finished:.1%} of {d_finished} "
                        f"finished run(s) in {days}d"
                    ),
                )
            )
        if defn and defn.status == "active" and not eval_case_count(flow_id):
            attention.append(
                FleetAttention(
                    flow_id=flow_id,
                    kind="no-eval-spec",
                    reason="active with no eval spec",
                )
            )
        if defn is None and d_runs:
            attention.append(
                FleetAttention(
                    flow_id=flow_id,
                    kind="no-export",
                    reason=f"{d_runs} run(s) with no declarative export to audit",
                )
            )

    finished = completed + failed
    weighted_rate = (
        rate_weight / (completed - no_assumption)
        if completed > no_assumption
        else 0.0
    )
    return FleetStats(
        days=days,
        flows=len(flow_ids),
        definitions=len(defs),
        without_definition=len(flow_ids) - len(defs),
        active=sum(1 for d in defs if d.status == "active"),
        paused=sum(1 for d in defs if d.status == "paused"),
        draft=sum(1 for d in defs if d.status == "draft"),
        runs=runs,
        completed=completed,
        failed=failed,
        in_flight=runs - finished,
        success_rate=round(completed / finished, 4) if finished else None,
        model_spend_usd=round(cost, 2),
        tokens=tokens,
        model_calls=calls,
        priced_model_calls=priced,
        cost_basis=cost_basis(calls, priced),
        hours_returned=round(minutes / 60.0, 1),
        roi_usd=round(roi_usd, 2),
        roi_basis=roi_basis(completed, minutes, weighted_rate, no_assumption),
        tasks_open=tasks_open,
        escalated=escalated,
        series=sorted(per_day.values(), key=lambda r: r.date),
        attention=attention,
    )


@router.post("", response_model=FlowDetailPublic, status_code=201)
def create_flow(
    payload: FlowCreate, principal: Principal = Depends(get_principal)
) -> FlowDetailPublic:
    require_role(principal, "admin")
    require_human(principal, "manage flows; sign in as an admin")
    defn = FlowDefinition(
        **payload.model_dump(),
        status="draft",
        version=1,
        authored_by=principal.email or principal.uid,
    )
    try:
        created = get_flow_service().create(defn)
    except FlowExistsError:
        raise HTTPException(
            status_code=409, detail=f"A flow named '{payload.id}' already exists"
        )
    log.info("Flow registered", extra={"flowId": created.id, "actor": principal.uid})
    return _detail(created.id, created, 0, _task_scope(principal))


@router.get("/{flow_id}", response_model=FlowDetailPublic)
def get_flow(
    flow_id: str, principal: Principal = Depends(get_principal)
) -> FlowDetailPublic:
    require_reader(principal)
    defn = _in_fleet_or_404(flow_id)
    return _detail(flow_id, defn, eval_case_count(flow_id), _task_scope(principal))


@router.patch("/{flow_id}", response_model=FlowDetailPublic)
def update_flow(
    flow_id: str,
    payload: FlowUpdate,
    principal: Principal = Depends(get_principal),
) -> FlowDetailPublic:
    require_role(principal, "admin")
    require_human(principal, "manage flows; sign in as an admin")
    defn = _get_or_404(flow_id)
    patch = {k: v for k, v in payload.model_dump().items() if v is not None}
    # A status change is a governed lifecycle act; log it distinctly so the
    # trail reads "who paused this and when".
    if "status" in patch and patch["status"] != defn.status:
        log.info(
            "Flow status changed",
            extra={
                "flowId": flow_id,
                "from": defn.status,
                "to": patch["status"],
                "actor": principal.uid,
            },
        )
    updated = get_flow_service().update(defn.model_copy(update=patch))
    log.info("Flow updated", extra={"flowId": flow_id, "actor": principal.uid})
    return _detail(flow_id, updated, eval_case_count(flow_id), _task_scope(principal))


@router.delete("/{flow_id}", status_code=204)
def delete_flow(
    flow_id: str, principal: Principal = Depends(get_principal)
) -> None:
    require_role(principal, "admin")
    require_human(principal, "manage flows; sign in as an admin")
    defn = _get_or_404(flow_id)
    if defn.status == "active":
        # Definitions are map assets; an active one is deprecated, not erased.
        raise HTTPException(
            status_code=409,
            detail="An active flow cannot be deleted. Pause or deprecate it first.",
        )
    try:
        get_flow_service().delete(flow_id)
    except FlowNotFoundError:
        raise HTTPException(status_code=404, detail=f"Flow not found: {flow_id}")
    log.info("Flow deleted", extra={"flowId": flow_id, "actor": principal.uid})


# ---- telemetry -------------------------------------------------------------


@router.get("/{flow_id}/usage", response_model=FlowUsage)
def flow_usage(
    flow_id: str,
    days: int = Query(default=30, ge=1, le=365),
    principal: Principal = Depends(get_principal),
) -> FlowUsage:
    require_reader(principal)
    defn = _in_fleet_or_404(flow_id)
    series = get_flow_service().usage(flow_id, days)
    runs = sum(d.runs for d in series)
    completed = sum(d.completed for d in series)
    failed = sum(d.failed for d in series)
    finished = completed + failed
    minutes = sum(d.minutes_saved for d in series)
    calls = sum(d.model_calls for d in series)
    priced = sum(d.priced_model_calls for d in series)
    rate = defn.roi_hourly_rate_usd if defn and defn.roi_minutes_per_run else 0.0
    return FlowUsage(
        flow_id=flow_id,
        has_definition=defn is not None,
        days=days,
        runs=runs,
        completed=completed,
        failed=failed,
        in_flight=runs - finished,
        success_rate=round(completed / finished, 4) if finished else None,
        cost_usd=round(sum(d.cost_usd for d in series), 2),
        tokens=sum(d.tokens for d in series),
        model_calls=calls,
        priced_model_calls=priced,
        cost_basis=cost_basis(calls, priced),
        tasks_raised=sum(d.tasks_raised for d in series),
        escalated=sum(d.escalated for d in series),
        minutes_saved=round(minutes, 1),
        roi_usd=round(minutes / 60.0 * rate, 2),
        roi_basis=roi_basis(
            completed, minutes, rate, 0 if rate else completed
        ),
        series=series,
    )


# ---- inventory -------------------------------------------------------------


class ModelInventory(BaseModel):
    """One model this flow calls, declared and observed.

    Both halves matter and neither implies the other. A model named in a
    step's export with no calls behind it is a declaration nothing has
    exercised; calls with no declaration are a model the map does not admit
    to. The console has to be able to say which, so the two counts travel
    together rather than being reconciled into one.
    """

    model: str
    # Steps whose exported config names this model.
    steps: list[str] = Field(default_factory=list)
    declared: bool = False
    calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    # Calls that carried a price. A cost of 0.00 over priced calls is free;
    # over unpriced calls it is a hole, and the console must not read the two
    # the same way.
    priced_calls: int = 0


FlowLinkVerb = Literal[
    "reads", "mutates", "establishes", "writes-to", "routes-to", "reaches"
]
FlowLinkKind = Literal[
    "entity", "knowledge", "collection", "link", "queue", "system"
]


class FlowLink(BaseModel):
    """One way this flow touches something governed.

    A single shape for every relationship, because the console shows them as
    one table and a reader comparing "reads Invoice" against "mutates
    SalesOrder" should not have to reconcile two different row layouts to do
    it. The verb carries the distinction that used to be carried by which list
    a ref appeared in.

    `ref` is the addressable form — `graph:Invoice`, `knowledge:ap-policy`,
    `collection:crm-companies`, `queue:order-desk`, `system:erp` — so a click
    routes on the address rather than on the label it happens to render.

    `declared` and `observed` are independent and both are reported. Declared
    with nothing observed is a path no run has taken yet, or a stale claim;
    observed with nothing declaring it is a governance gap. Collapsing them
    into one boolean would hide both.
    """

    verb: FlowLinkVerb
    ref: str
    kind: FlowLinkKind
    label: str
    declared: bool = False
    observed: bool = False


class FlowInventory(BaseModel):
    """What one flow is wired to: the models it calls, and every governed
    thing it reads, mutates, writes to, routes to or reaches.

    Composed rather than stored. The models come from the run journal — a
    `StepRecord` is the only record of what a model call cost — and the links
    come from `flow_projection`, which already diffs what runs were observed
    doing against what the Collection registry declares. This is the per-flow
    view of that; `/ui/ontology` is the estate-wide one, and both read the
    same projection.
    """

    flow_id: str
    state: str
    scanned_runs: int = 0
    total_runs: int = 0

    models: list[ModelInventory] = Field(default_factory=list)
    model_budget_usd_per_run: float = 0.0

    links: list[FlowLink] = Field(default_factory=list)

    # The governance signal, carried through from the projection: observed
    # with nothing declaring it, and declared with nothing observing it.
    undeclared: list[str] = Field(default_factory=list)
    unobserved: list[str] = Field(default_factory=list)


def _model_inventory(
    defn: FlowDefinition | None, runs: list[RunRecord]
) -> list[ModelInventory]:
    """Every model this flow calls, from its export and from its journal."""
    by_model: dict[str, ModelInventory] = {}

    def slot(model: str) -> ModelInventory:
        return by_model.setdefault(model, ModelInventory(model=model))

    # Declared: whatever a step's exported config names. A modelcall step that
    # names none inherits the server default at run time, and saying so beats
    # printing nothing for a step that demonstrably calls a model.
    for step in defn.steps if defn else []:
        if step.type != "modelcall":
            continue
        named = step.config.get("model", "")
        entry = slot(named or _DEFAULT_MODEL)
        entry.declared = True
        if step.id not in entry.steps:
            entry.steps.append(step.id)

    # Observed: the journal. `StepRecord.model` is filled from the meter on a
    # completed model call, so a step that failed before calling contributes
    # nothing rather than a zero-cost call.
    for run in runs:
        for step in run.steps:
            if not step.model:
                continue
            entry = slot(step.model)
            entry.calls += 1
            entry.tokens += step.tokens
            entry.cost_usd += step.cost_usd
            if step.cost_usd > 0:
                entry.priced_calls += 1

    for entry in by_model.values():
        entry.cost_usd = round(entry.cost_usd, 4)
    # Busiest first, then declared-but-idle, then by name — so the model doing
    # the work leads and a stale declaration is still reachable.
    return sorted(
        by_model.values(),
        key=lambda m: (-m.calls, not m.declared, m.model),
    )


# What a modelcall step resolves to when its export names no model. Stated
# here rather than imported from `config` because this is a claim about the
# *deployed* server's default, and printing a local default would be a lie on
# any other deployment.
_DEFAULT_MODEL = "server default"


# The order the console reads them in: what the flow takes in, what it
# changes, and what it reaches out to. Within a verb, observed outranks
# declared — what a flow actually did is worth more than what it says it does.
_VERB_ORDER: dict[str, int] = {
    "reads": 0,
    "mutates": 1,
    "establishes": 2,
    "writes-to": 3,
    "routes-to": 4,
    "reaches": 5,
}


def _links(projection, defn: FlowDefinition | None) -> list[FlowLink]:
    """Every governed thing this flow touches, as one uniform relation.

    Two sources, deliberately merged here rather than in the console: the
    projection (observed in run journals, plus what the Collection registry
    declares) and the export's own step config (desks and systems, which are
    routing decisions rather than traffic).
    """
    found: dict[tuple[str, str], FlowLink] = {}

    def put(
        verb: str,
        ref: str,
        kind: str,
        label: str,
        *,
        declared: bool = False,
        observed: bool = False,
    ) -> None:
        key = (verb, ref)
        entry = found.get(key)
        if entry is None:
            found[key] = FlowLink(
                verb=verb, ref=ref, kind=kind, label=label,
                declared=declared, observed=observed,
            )
            return
        entry.declared = entry.declared or declared
        entry.observed = entry.observed or observed

    # What it takes in.
    for t in projection.reads:
        put("reads", f"graph:{t}", "entity", t, observed=True)
    for a in projection.knowledge:
        put("reads", f"knowledge:{a}", "knowledge", a, observed=True)
    for t in projection.declared_reads:
        put("reads", f"graph:{t}", "entity", t, declared=True)
    for c in projection.read_collections:
        put("reads", f"collection:{c}", "collection", c, declared=True)

    # What it changes. `mutates` rather than `writes`: these are existing
    # entity types whose rows the flow creates or alters, which is a different
    # claim from the collection it lands them in.
    for t in projection.writes:
        put("mutates", f"graph:{t}", "entity", t, observed=True)
    for t in projection.declared_writes:
        put("mutates", f"graph:{t}", "entity", t, declared=True)
    for t in projection.establishes:
        put("establishes", f"link:{t}", "link", t, observed=True)
    for c in projection.written_collections:
        put("writes-to", f"collection:{c}", "collection", c, declared=True)

    # What it reaches out to. Declared only: a run that never suspended is not
    # evidence the desk does not exist, so scoping these to observed traffic
    # would empty the rows on every quiet flow.
    for queue in defn.queues if defn else []:
        put("routes-to", f"queue:{queue}", "queue", queue, declared=True)
    for step in defn.steps if defn else []:
        queue = step.config.get("queue", "")
        if queue:
            put("routes-to", f"queue:{queue}", "queue", queue, declared=True)
        # The export calls it a gateway; the reader calls it a system. Same
        # thing named from the two ends.
        gateway = step.config.get("gateway", "")
        if gateway:
            put("reaches", f"system:{gateway}", "system", gateway, declared=True)

    return sorted(
        found.values(),
        key=lambda link: (
            _VERB_ORDER.get(link.verb, 9),
            not link.observed,
            link.label,
        ),
    )


@router.get("/{flow_id}/inventory", response_model=FlowInventory)
def flow_inventory(
    flow_id: str, principal: Principal = Depends(get_principal)
) -> FlowInventory:
    """The models, ontology, collections, desks and gateways one flow touches.

    Not windowed. An inventory is what the flow is *wired to*, and a wiring
    that a 30-day window happens not to have exercised is still the wiring —
    scoping this to the page's timeframe would make a flow look unconnected on
    a quiet month.
    """
    require_reader(principal)
    defn = _in_fleet_or_404(flow_id)
    from .deps import get_run_engine
    from .flow_projection import project_flow

    engine = get_run_engine()
    projection = project_flow(engine, flow_id)
    runs = engine.list(flow_id=flow_id)
    return FlowInventory(
        flow_id=flow_id,
        state=projection.state,
        scanned_runs=projection.scanned_runs,
        total_runs=projection.total_runs,
        models=_model_inventory(defn, runs),
        model_budget_usd_per_run=defn.model_budget_usd_per_run if defn else 0.0,
        links=_links(projection, defn),
        undeclared=sorted(
            {*projection.undeclared_reads, *projection.undeclared_writes}
        ),
        unobserved=sorted(
            {*projection.unobserved_reads, *projection.unobserved_writes}
        ),
    )


# ---- versions --------------------------------------------------------------


class FlowVersionPublic(FlowVersion):
    #: Whether this version's executable package is present in the running
    #: deployment. Computed at read time from the shipped code, never stored:
    #: a redeploy is exactly what changes it. It is the precondition for the
    #: activate endpoint below.
    shipped: bool = False


def _shipped_numbers(flow_id: str) -> set[int]:
    from .tasks import shipped_version_numbers

    return shipped_version_numbers(flow_id)


def _public_version(version: FlowVersion, shipped: set[int]) -> FlowVersionPublic:
    return FlowVersionPublic(
        **version.model_dump(), shipped=version.version in shipped
    )


@router.get("/{flow_id}/versions", response_model=list[FlowVersionPublic])
def flow_versions(
    flow_id: str, principal: Principal = Depends(get_principal)
) -> list[FlowVersionPublic]:
    """Published history plus any open draft, newest first."""
    require_reader(principal)
    _get_or_404(flow_id)
    shipped = _shipped_numbers(flow_id)
    return [
        _public_version(v, shipped) for v in get_flow_service().versions(flow_id)
    ]


@router.get("/{flow_id}/versions/{version}", response_model=FlowVersionPublic)
def flow_version(
    flow_id: str, version: int, principal: Principal = Depends(get_principal)
) -> FlowVersionPublic:
    require_reader(principal)
    _get_or_404(flow_id)
    found = get_flow_service().version(flow_id, version)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Version not found: v{version}")
    return _public_version(found, _shipped_numbers(flow_id))


@router.post("/{flow_id}/versions/{version}/activate", response_model=FlowDetailPublic)
def activate_flow_version(
    flow_id: str, version: int, principal: Principal = Depends(get_principal)
) -> FlowDetailPublic:
    """Make one shipped version the live one.

    The console chooses; the deployed code bounds the choice. A version with
    no package in the running deployment is refused with a 409 — the stored
    pointer must never name code the engine cannot run. Takes effect on the
    next run started; suspended runs finish under the version that started
    them.
    """
    require_role(principal, "admin")
    require_human(principal, "manage flows; sign in as an admin")
    _get_or_404(flow_id)
    try:
        updated = get_flow_service().activate_version(flow_id, version)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    log.info(
        "Flow version activated",
        extra={"flowId": flow_id, "version": version, "actor": principal.uid},
    )
    return _detail(flow_id, updated, eval_case_count(flow_id), _task_scope(principal))


@router.get("/{flow_id}/versions/{version}/diff", response_model=list[StepDiff])
def flow_version_diff(
    flow_id: str,
    version: int,
    against: int | None = Query(
        default=None,
        description="Version to compare with. Defaults to the active version.",
    ),
    principal: Principal = Depends(get_principal),
) -> list[StepDiff]:
    """Steps of `version`, annotated added / removed / modified.

    Removed steps are returned last carrying the *baseline* step, so the
    canvas can show what a draft deletes rather than silently dropping it.
    """
    require_reader(principal)
    _get_or_404(flow_id)
    try:
        return get_flow_service().diff(flow_id, version, against)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ---- recommendations -------------------------------------------------------


@router.get("/{flow_id}/recommendations", response_model=list[Recommendation])
def flow_recommendations(
    flow_id: str,
    days: int = Query(default=30, ge=7, le=365),
    principal: Principal = Depends(get_principal),
) -> list[Recommendation]:
    """Cost and performance insights derived from this flow's own runs.

    Always at least one card: the recommender states the evidence it had,
    including — especially — when that evidence supports nothing.
    """
    require_reader(principal)
    _in_fleet_or_404(flow_id)
    return get_flow_service().recommendations(flow_id, days)


@router.post(
    "/{flow_id}/recommendations/{recommendation_id}/apply",
    response_model=AppliedRecommendation,
)
def apply_recommendation(
    flow_id: str,
    recommendation_id: str,
    principal: Principal = Depends(get_principal),
) -> AppliedRecommendation:
    """Stage a recommendation into the draft version — never into the live one.

    Applying edits governed knowledge, so it is admin-gated, human-only, and
    logged. The change lands in a draft precisely so it has to survive the
    flow's eval spec before it becomes what runs — which is a separate,
    deliberate act at `/api/v1/evals/{spec_id}/run`, not something applying a
    card does on the operator's behalf.
    """
    require_role(principal, "admin")
    require_human(principal, "manage flows; sign in as an admin")
    _get_or_404(flow_id)
    service = get_flow_service()
    rec = next(
        (r for r in service.recommendations(flow_id) if r.id == recommendation_id),
        None,
    )
    if rec is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Recommendation not found — insights are derived from live "
                "telemetry and may no longer apply."
            ),
        )
    try:
        _, draft = service.apply_recommendation(
            flow_id, rec, principal.email or principal.uid
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    change = rec.proposed_change or {}
    return AppliedRecommendation(
        recommendation_id=rec.id,
        draft=draft,
        summary=(
            f"{change.get('field', 'setting')} on {rec.step_name or change.get('step_id', '')} "
            f"set to {change.get('to', '')} in {draft.display()}"
        ),
    )
