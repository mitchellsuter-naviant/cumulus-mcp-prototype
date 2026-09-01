"""REST surface for Flow runs — the journal a workspace's run list reads.

Runs are read-only over HTTP. They are started by triggers (a submission, a
source delivery) and advanced by Task decisions; a POST that could drive a run
from outside would be a second, unaudited trigger.

The one exception is `POST /runs/{id}/resume`, which exists for a narrow and
honest reason: a run whose gate has been decided but which was interrupted
before it could be driven — the process died between the decision write and the
resume — needs a way forward. It replays completed steps, so calling it on a
run with nothing to do is a no-op rather than a duplicate.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import deps
from .auth import Principal, get_principal, require_reader, require_role
from .runs import RunRecord, RunStatus, StepRecord

log = logging.getLogger("cumulus.runs.api")

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


class RunSummary(BaseModel):
    """A run as a list row: enough to triage without loading the journal."""

    id: str
    flow_id: str
    status: RunStatus
    case_ref: str
    actor: str
    steps: int
    model_calls: int
    cost_usd: float
    duration_ms: int
    open_task_id: str
    suspended_at_step: str
    resume_count: int
    error: str
    started_at_iso: str
    finished_at_iso: str
    refs: list[str] = Field(default_factory=list)
    outputs: dict = Field(default_factory=dict)


class FlowRunStats(BaseModel):
    total: int = 0
    succeeded: int = 0
    suspended: int = 0
    failed: int = 0
    running: int = 0
    model_calls: int = 0
    cost_usd: float = 0.0
    # Runs that needed a person at least once, and how many rounds it took.
    with_human: int = 0
    human_rounds: int = 0
    by_flow: dict[str, int] = Field(default_factory=dict)


def _summary(record: RunRecord) -> RunSummary:
    return RunSummary(
        id=record.id, flow_id=record.flow_id, status=record.status,
        case_ref=record.case_ref, actor=record.actor, steps=len(record.steps),
        model_calls=record.model_calls, cost_usd=record.cost_usd,
        duration_ms=record.duration_ms(), open_task_id=record.open_task_id,
        suspended_at_step=record.suspended_at_step,
        resume_count=record.resume_count, error=record.error,
        started_at_iso=record.started_at_iso,
        finished_at_iso=record.finished_at_iso, refs=record.refs,
        outputs=record.outputs,
    )


@router.get("", response_model=list[RunSummary])
def list_runs(
    flow_id: str | None = Query(default=None),
    status: RunStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    principal: Principal = Depends(get_principal),
) -> list[RunSummary]:
    require_reader(principal)
    return [
        _summary(r)
        for r in deps.get_run_engine().list(flow_id=flow_id, status=status, limit=limit)
    ]


@router.get("/stats", response_model=FlowRunStats)
def run_stats(
    flow_id: str | None = Query(default=None),
    principal: Principal = Depends(get_principal),
) -> FlowRunStats:
    require_reader(principal)
    stats = FlowRunStats()
    for record in deps.get_run_engine().list(flow_id=flow_id):
        stats.total += 1
        setattr(stats, record.status, getattr(stats, record.status) + 1)
        stats.model_calls += record.model_calls
        stats.cost_usd = round(stats.cost_usd + record.cost_usd, 6)
        gates = [s for s in record.steps if s.kind == "task"]
        if gates:
            stats.with_human += 1
            stats.human_rounds += len(gates)
        stats.by_flow[record.flow_id] = stats.by_flow.get(record.flow_id, 0) + 1
    return stats


@router.get("/{run_id}", response_model=RunRecord)
def get_run(
    run_id: str, principal: Principal = Depends(get_principal)
) -> RunRecord:
    require_reader(principal)
    record = deps.get_run_engine().get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return record


@router.get("/{run_id}/steps", response_model=list[StepRecord])
def get_run_steps(
    run_id: str, principal: Principal = Depends(get_principal)
) -> list[StepRecord]:
    require_reader(principal)
    record = deps.get_run_engine().get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return record.steps


@router.post("/{run_id}/resume", response_model=RunRecord)
def resume_run(
    run_id: str,
    retry_failed: bool = Query(
        default=False,
        description="Re-enter a run that ended `failed` — after the bug its "
                    "step hit has been fixed and deployed.",
    ),
    principal: Principal = Depends(get_principal),
) -> RunRecord:
    """Drive a run forward from its journal.

    Two cases, and the second is why `retry_failed` exists.

    A **suspended** run whose gate was decided but which was interrupted before
    it could be driven — the process died between the decision write and the
    resume — needs a way forward. That is the default, and it is idempotent by
    construction: completed steps replay, so a run that is already finished or
    still genuinely waiting comes back unchanged.

    A **failed** run was unrecoverable. A step with no escalation queue raised,
    the record went terminal, and nothing could re-enter it — so fixing the bug
    and redeploying could not save the run, and resubmitting was the only path,
    which loses the case's history and mints a second one. `retry_failed=true`
    re-executes the failed step against the same journal. Deliberately explicit
    rather than automatic: retrying is a judgment that the cause has been
    addressed, and nobody but the operator can make it.

    Admin-only either way.
    """
    require_role(principal, "admin")
    engine = deps.get_run_engine()
    if engine.get(run_id) is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    record = engine.resume(run_id, retry_failed=retry_failed)
    log.info(
        "Run resumed via API",
        extra={"runId": run_id, "status": record.status, "actor": principal.uid},
    )
    return record
