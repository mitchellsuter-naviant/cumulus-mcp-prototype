"""REST surface for the Flows section: registry, telemetry, audit, testing.

Reads need any authenticated role — watching how a workstream runs is a
Workspace-grade capability, not an admin one. Registering, editing, pausing,
and everything on the golden set or the harness is admin+, and — like teams —
a minted key may read but never reshape the registry it authenticates against.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import Principal, get_principal, require_reader, require_role
from .deps import get_flow_service
from .models import utcnow
from .flows import (
    AuditEntry,
    GoldenCase,
    GoldenSource,
    Recommendation,
    RunDetail,
    RunSummary,
    StepDiff,
    TestRun,
    UsageDay,
    FlowDefinition,
    FlowExistsError,
    FlowNotFoundError,
    FlowStatus,
    FlowStep,
    FlowVersion,
    audit_entries,
    find_run,
    open_tasks,
    recent_runs,
    usage_series,
)

log = logging.getLogger("cumulus.flows.api")

router = APIRouter(prefix="/api/v1/flows", tags=["flows"])


# ---- public shapes ---------------------------------------------------------


class FlowSummaryPublic(BaseModel):
    id: str
    name: str
    status: FlowStatus
    description: str
    team: str
    tags: list[str]
    version: int
    updated_at: str
    runs_30d: int = 0
    cost_30d_usd: float = 0.0
    success_30d: float | None = None
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
    days: int
    definitions: int
    active: int
    paused: int
    draft: int
    runs: int
    completed: int
    success_rate: float | None = None
    model_spend_usd: float
    tokens: int
    model_calls: int
    hours_returned: float
    roi_usd: float
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


class GoldenCaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    input_ref: str = Field(default="", max_length=200)
    expected: str = Field(default="", max_length=600)
    source: GoldenSource = "human-authored"
    # Set when promoting a real execution: the case keeps a pointer back to
    # the run that motivated it, so a failing eval is traceable.
    run_id: str = Field(default="", max_length=80)
    # A JSON fixture pasted or uploaded from the console. Held verbatim —
    # re-serializing would quietly change what is under test.
    payload: str = Field(default="", max_length=20000)


class GoldenSetPublic(BaseModel):
    flow_id: str
    cases: list[GoldenCase] = Field(default_factory=list)
    synced_from: str = ""
    synced_at: str = ""


class AppliedRecommendation(BaseModel):
    recommendation_id: str
    draft: FlowVersion
    summary: str


class TestRunLaunch(BaseModel):
    candidate_version: str = Field(default="", max_length=60)
    baseline_version: str = Field(default="", max_length=60)


class FlowUsage(BaseModel):
    flow_id: str
    days: int
    runs: int
    completed: int
    success_rate: float | None
    cost_usd: float
    tokens: int
    model_calls: int
    tasks_raised: int
    escalated: int
    minutes_saved: float
    roi_usd: float
    series: list[UsageDay]


# ---- helpers ---------------------------------------------------------------


def _require_human(principal: Principal) -> None:
    """A key may not reshape the registry it authenticates against."""
    if principal.key_id is not None:
        log.warning(
            "API key attempted flow management", extra={"keyId": principal.key_id}
        )
        raise HTTPException(
            status_code=403,
            detail="API keys cannot manage flows; sign in as an admin.",
        )


def _rollup(defn: FlowDefinition) -> FlowSummaryPublic:
    series = usage_series(defn, 30)
    runs = sum(d.runs for d in series)
    completed = sum(d.completed for d in series)
    return FlowSummaryPublic(
        id=defn.id,
        name=defn.name or defn.id,
        status=defn.status,
        description=defn.description,
        team=defn.team,
        tags=defn.tags,
        version=defn.version,
        updated_at=defn.updated_at.isoformat(),
        runs_30d=runs,
        cost_30d_usd=round(sum(d.cost_usd for d in series), 2),
        success_30d=round(completed / runs, 4) if runs else None,
        tasks_open=open_tasks(defn),
        minutes_saved_30d=round(sum(d.minutes_saved for d in series), 1),
        spark=[d.runs for d in series[-14:]],
    )


def _detail(defn: FlowDefinition, golden_cases: int) -> FlowDetailPublic:
    base = _rollup(defn)
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
        golden_cases=golden_cases,
        active_version_label=defn.active_version().display(),
        draft_version_label=draft.display() if draft else "",
        version_count=len(defn.version_history()),
    )


def _get_or_404(flow_id: str) -> FlowDefinition:
    defn = get_flow_service().get(flow_id)
    if defn is None:
        raise HTTPException(status_code=404, detail=f"Flow not found: {flow_id}")
    return defn


# ---- fleet -----------------------------------------------------------------


@router.get("", response_model=list[FlowSummaryPublic])
def list_flows(
    q: str = "",
    status: FlowStatus | None = None,
    team: str | None = None,
    principal: Principal = Depends(get_principal),
) -> list[FlowSummaryPublic]:
    require_reader(principal)
    out = []
    needle = q.strip().lower()
    for defn in get_flow_service().list():
        if status is not None and defn.status != status:
            continue
        if team is not None and defn.team != team:
            continue
        if needle and needle not in " ".join(
            [defn.id, defn.name, defn.description, defn.team, *defn.tags]
        ).lower():
            continue
        out.append(_rollup(defn))
    # Highest model spend first: the list doubles as the cost report.
    out.sort(key=lambda w: w.cost_30d_usd, reverse=True)
    return out


@router.get("/stats", response_model=FleetStats)
def fleet_stats(
    days: int = Query(default=30, ge=1, le=365),
    principal: Principal = Depends(get_principal),
) -> FleetStats:
    require_reader(principal)
    service = get_flow_service()
    defs = service.list()
    per_day: dict[str, FleetSeriesDay] = {}
    runs = completed = tokens = calls = escalated = tasks_open = 0
    cost = minutes = roi_usd = 0.0
    attention: list[FleetAttention] = []
    for defn in defs:
        series = usage_series(defn, days)
        d_runs = sum(d.runs for d in series)
        d_completed = sum(d.completed for d in series)
        runs += d_runs
        completed += d_completed
        cost += sum(d.cost_usd for d in series)
        tokens += sum(d.tokens for d in series)
        calls += sum(d.model_calls for d in series)
        escalated += sum(d.escalated for d in series)
        minutes += sum(d.minutes_saved for d in series)
        roi_usd += sum(d.minutes_saved for d in series) / 60.0 * defn.roi_hourly_rate_usd
        tasks_open += open_tasks(defn)
        for day in series:
            key = day.date.isoformat()
            row = per_day.setdefault(
                key, FleetSeriesDay(date=key, runs=0, cost_usd=0.0, tasks_raised=0)
            )
            row.runs += day.runs
            row.cost_usd = round(row.cost_usd + day.cost_usd, 2)
            row.tasks_raised += day.tasks_raised
        if d_runs and (d_completed / d_runs) < 0.95:
            attention.append(
                FleetAttention(
                    flow_id=defn.id,
                    reason=f"success {d_completed / d_runs:.1%} over the last {days}d",
                )
            )
        if defn.status == "active" and not service.golden_set(defn.id).cases:
            attention.append(
                FleetAttention(flow_id=defn.id, reason="active with no golden set")
            )
    return FleetStats(
        days=days,
        definitions=len(defs),
        active=sum(1 for d in defs if d.status == "active"),
        paused=sum(1 for d in defs if d.status == "paused"),
        draft=sum(1 for d in defs if d.status == "draft"),
        runs=runs,
        completed=completed,
        success_rate=round(completed / runs, 4) if runs else None,
        model_spend_usd=round(cost, 2),
        tokens=tokens,
        model_calls=calls,
        hours_returned=round(minutes / 60.0, 1),
        roi_usd=round(roi_usd, 2),
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
    _require_human(principal)
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
    return _detail(created, 0)


@router.get("/{flow_id}", response_model=FlowDetailPublic)
def get_flow(
    flow_id: str, principal: Principal = Depends(get_principal)
) -> FlowDetailPublic:
    require_reader(principal)
    defn = _get_or_404(flow_id)
    golden = get_flow_service().golden_set(flow_id)
    return _detail(defn, len(golden.cases))


@router.patch("/{flow_id}", response_model=FlowDetailPublic)
def update_flow(
    flow_id: str,
    payload: FlowUpdate,
    principal: Principal = Depends(get_principal),
) -> FlowDetailPublic:
    require_role(principal, "admin")
    _require_human(principal)
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
    golden = get_flow_service().golden_set(flow_id)
    return _detail(updated, len(golden.cases))


@router.delete("/{flow_id}", status_code=204)
def delete_flow(
    flow_id: str, principal: Principal = Depends(get_principal)
) -> None:
    require_role(principal, "admin")
    _require_human(principal)
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
    defn = _get_or_404(flow_id)
    series = usage_series(defn, days)
    runs = sum(d.runs for d in series)
    completed = sum(d.completed for d in series)
    minutes = sum(d.minutes_saved for d in series)
    return FlowUsage(
        flow_id=flow_id,
        days=days,
        runs=runs,
        completed=completed,
        success_rate=round(completed / runs, 4) if runs else None,
        cost_usd=round(sum(d.cost_usd for d in series), 2),
        tokens=sum(d.tokens for d in series),
        model_calls=sum(d.model_calls for d in series),
        tasks_raised=sum(d.tasks_raised for d in series),
        escalated=sum(d.escalated for d in series),
        minutes_saved=round(minutes, 1),
        roi_usd=round(minutes / 60.0 * defn.roi_hourly_rate_usd, 2),
        series=series,
    )


@router.get("/{flow_id}/runs", response_model=list[RunSummary])
def flow_runs(
    flow_id: str,
    limit: int = Query(default=25, ge=1, le=200),
    principal: Principal = Depends(get_principal),
) -> list[RunSummary]:
    require_reader(principal)
    return recent_runs(_get_or_404(flow_id), limit)


@router.get("/{flow_id}/runs/{run_id}", response_model=RunDetail)
def flow_run(
    flow_id: str, run_id: str, principal: Principal = Depends(get_principal)
) -> RunDetail:
    require_reader(principal)
    detail = find_run(_get_or_404(flow_id), run_id)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail=f"Run not found (journals are kept 45 days in this prototype): {run_id}",
        )
    return detail


@router.get("/{flow_id}/audit", response_model=list[AuditEntry])
def flow_audit(
    flow_id: str,
    limit: int = Query(default=40, ge=1, le=200),
    principal: Principal = Depends(get_principal),
) -> list[AuditEntry]:
    require_reader(principal)
    return audit_entries(_get_or_404(flow_id), limit)


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
    """Cost and performance insights derived from this definition's telemetry."""
    require_reader(principal)
    _get_or_404(flow_id)
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
    golden set before it becomes what runs.
    """
    require_role(principal, "admin")
    _require_human(principal)
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


# ---- golden set ------------------------------------------------------------


@router.get("/{flow_id}/golden-set", response_model=GoldenSetPublic)
def golden_set(
    flow_id: str, principal: Principal = Depends(get_principal)
) -> GoldenSetPublic:
    require_reader(principal)
    _get_or_404(flow_id)
    golden = get_flow_service().golden_set(flow_id)
    return GoldenSetPublic(
        flow_id=flow_id,
        cases=golden.cases,
        synced_from=golden.synced_from,
        synced_at=golden.synced_at.isoformat() if golden.synced_at else "",
    )


@router.post("/{flow_id}/golden-set", response_model=GoldenSetPublic, status_code=201)
def add_golden_case(
    flow_id: str,
    payload: GoldenCaseCreate,
    principal: Principal = Depends(get_principal),
) -> GoldenSetPublic:
    require_role(principal, "admin")
    _require_human(principal)
    _get_or_404(flow_id)
    service = get_flow_service()
    existing = service.golden_set(flow_id).cases
    # Promoting a run is the flywheel in miniature: the execution that needed
    # judgment becomes the case that guards against needing it again.
    source = payload.source
    if payload.run_id and source == "human-authored":
        source = "promoted-from-run"
    case = GoldenCase(
        id=f"gc-{len(existing) + 1:03d}-{utcnow().strftime('%H%M%S')}",
        name=payload.name,
        input_ref=payload.input_ref,
        expected=payload.expected,
        source=source,
        added_by=principal.email or principal.uid,
        run_id=payload.run_id,
        payload=payload.payload,
    )
    try:
        golden = service.add_golden_case(flow_id, case)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    log.info(
        "Golden case added",
        extra={
            "flowId": flow_id,
            "caseId": case.id,
            "source": source,
            "runId": payload.run_id,
            "actor": principal.uid,
        },
    )
    return GoldenSetPublic(
        flow_id=flow_id,
        cases=golden.cases,
        synced_from=golden.synced_from,
        synced_at=golden.synced_at.isoformat() if golden.synced_at else "",
    )


@router.delete("/{flow_id}/golden-set/{case_id}", response_model=GoldenSetPublic)
def remove_golden_case(
    flow_id: str, case_id: str, principal: Principal = Depends(get_principal)
) -> GoldenSetPublic:
    require_role(principal, "admin")
    _require_human(principal)
    _get_or_404(flow_id)
    try:
        golden = get_flow_service().remove_golden_case(flow_id, case_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    log.info(
        "Golden case removed",
        extra={"flowId": flow_id, "caseId": case_id, "actor": principal.uid},
    )
    return GoldenSetPublic(
        flow_id=flow_id,
        cases=golden.cases,
        synced_from=golden.synced_from,
        synced_at=golden.synced_at.isoformat() if golden.synced_at else "",
    )


# ---- test harness ----------------------------------------------------------


@router.get("/{flow_id}/test-runs", response_model=list[TestRun])
def list_test_runs(
    flow_id: str, principal: Principal = Depends(get_principal)
) -> list[TestRun]:
    require_reader(principal)
    _get_or_404(flow_id)
    return get_flow_service().test_runs.list_for(flow_id)


@router.post("/{flow_id}/test-runs", response_model=TestRun, status_code=202)
def launch_test_run(
    flow_id: str,
    payload: TestRunLaunch,
    principal: Principal = Depends(get_principal),
) -> TestRun:
    """Kick off a golden-set evaluation. Writes suppressed by construction.

    202: the harness executes case-by-case in the background; poll the test
    run for progress. Deliberately admin-gated but *not* human-gated — kicking
    off an eval from CI with a minted key is a legitimate machine act.
    """
    require_role(principal, "admin")
    defn = _get_or_404(flow_id)
    try:
        run = get_flow_service().launch_test_run(
            flow_id,
            payload.candidate_version,
            payload.baseline_version,
            launched_by=principal.email or principal.uid,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    log.info(
        "Test run launched",
        extra={
            "flowId": defn.id,
            "testId": run.id,
            "cases": run.total_cases,
            "actor": principal.uid,
        },
    )
    return run


@router.get("/{flow_id}/test-runs/{test_id}", response_model=TestRun)
def get_test_run(
    flow_id: str, test_id: str, principal: Principal = Depends(get_principal)
) -> TestRun:
    require_reader(principal)
    _get_or_404(flow_id)
    run = get_flow_service().test_runs.get(test_id)
    if run is None or run.flow_id != flow_id:
        raise HTTPException(status_code=404, detail=f"Test run not found: {test_id}")
    return run
