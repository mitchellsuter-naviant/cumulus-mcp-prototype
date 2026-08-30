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
    runs_30d: int = 0
    cost_30d_usd: float = 0.0
    # Of finished runs. In-flight runs are evidence of neither outcome.
    success_30d: float | None = None
    cost_basis_30d: CostBasis = "no-model-calls"
    tasks_open: int = 0
    minutes_saved_30d: float = 0.0
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
    date: str
    runs: int
    cost_usd: float
    tasks_raised: int


class FleetAttention(BaseModel):
    flow_id: str
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


def _rollup(
    flow_id: str,
    defn: FlowDefinition | None,
    runs: list[RunRecord],
    tasks_open: int,
) -> FlowSummaryPublic:
    series = usage_series(runs, 30, defn.roi_minutes_per_run if defn else 0.0)
    total = sum(d.runs for d in series)
    completed = sum(d.completed for d in series)
    finished = completed + sum(d.failed for d in series)
    calls = sum(d.model_calls for d in series)
    priced = sum(d.priced_model_calls for d in series)
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
        runs_30d=total,
        cost_30d_usd=round(sum(d.cost_usd for d in series), 2),
        success_30d=round(completed / finished, 4) if finished else None,
        cost_basis_30d=cost_basis(calls, priced),
        tasks_open=tasks_open,
        minutes_saved_30d=round(sum(d.minutes_saved for d in series), 1),
        spark=[d.runs for d in series[-14:]],
    )


def _detail(
    flow_id: str,
    defn: FlowDefinition | None,
    eval_cases: int,
) -> FlowDetailPublic:
    base = _rollup(flow_id, defn, flow_runs(flow_id), open_tasks(flow_id))
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
    principal: Principal = Depends(get_principal),
) -> list[FlowSummaryPublic]:
    require_reader(principal)
    out = []
    needle = q.strip().lower()
    service = get_flow_service()
    runs_by_flow = fleet_runs()
    tasks_by_flow = open_tasks_by_flow()
    for flow_id in fleet_flow_ids():
        defn = service.get(flow_id)
        row = _rollup(
            flow_id, defn, runs_by_flow.get(flow_id, []), tasks_by_flow.get(flow_id, 0)
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
    out.sort(key=lambda w: (-w.runs_30d, -w.cost_30d_usd, w.id))
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
    tasks_by_flow = open_tasks_by_flow()
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
        d_finished = d_completed + d_failed
        if d_finished and (d_completed / d_finished) < 0.95:
            # The sample size travels with the rate. Three failures out of
            # four runs and three hundred out of four hundred are not the
            # same finding, and a bare percentage hides which one this is.
            attention.append(
                FleetAttention(
                    flow_id=flow_id,
                    reason=(
                        f"success {d_completed / d_finished:.1%} of {d_finished} "
                        f"finished run(s) in {days}d"
                    ),
                )
            )
        if defn and defn.status == "active" and not eval_case_count(flow_id):
            attention.append(
                FleetAttention(flow_id=flow_id, reason="active with no eval spec")
            )
        if defn is None and d_runs:
            attention.append(
                FleetAttention(
                    flow_id=flow_id,
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
    return _detail(created.id, created, 0)


@router.get("/{flow_id}", response_model=FlowDetailPublic)
def get_flow(
    flow_id: str, principal: Principal = Depends(get_principal)
) -> FlowDetailPublic:
    require_reader(principal)
    defn = _in_fleet_or_404(flow_id)
    return _detail(flow_id, defn, eval_case_count(flow_id))


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
    return _detail(flow_id, updated, eval_case_count(flow_id))


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


# ---- versions --------------------------------------------------------------


@router.get("/{flow_id}/versions", response_model=list[FlowVersion])
def flow_versions(
    flow_id: str, principal: Principal = Depends(get_principal)
) -> list[FlowVersion]:
    """Published history plus any open draft, newest first."""
    require_reader(principal)
    _get_or_404(flow_id)
    return get_flow_service().versions(flow_id)


@router.get("/{flow_id}/versions/{version}", response_model=FlowVersion)
def flow_version(
    flow_id: str, version: int, principal: Principal = Depends(get_principal)
) -> FlowVersion:
    require_reader(principal)
    _get_or_404(flow_id)
    found = get_flow_service().version(flow_id, version)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Version not found: v{version}")
    return found


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
