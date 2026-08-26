"""REST surface for graph-shaped evals.

Reads are open to any authenticated role: a spec is a contract, and reading
what a flow promises is not privileged. Launching a run is `admin+` but **not**
human-gated — a CI key launching an eval is the point of having them, and it is
the one place in this API where a credential doing the work is correct rather
than suspicious. Deciding a Task is the opposite, and stays refused for keys.

Results are returned by the POST and the most recent one per spec is kept in
memory. They are deliberately not persisted yet: a result is only meaningful
against the knowledge revisions it read, and inventing a durable store before
that pinning is real would be storing numbers nobody can compare.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import deps
from .auth import Principal, get_principal, require_reader, require_role
from .evals import EvalCase, EvalResult, EvalSpec

log = logging.getLogger("cumulus.evals.api")

router = APIRouter(prefix="/api/v1/evals", tags=["evals"])


class SpecSummary(BaseModel):
    id: str
    flow_id: str
    version: int
    summary: str
    checkpoints: int
    cases: int
    graded_fields: int
    ignored_fields: int
    graded_links: int
    asset_id: str = "flow-eval-specs"


class EvalResultView(BaseModel):
    """A result, with the totals precomputed so a client need not derive them."""

    spec_id: str
    flow_id: str
    spec_version: int
    at_iso: str
    launched_by: str
    passed: int
    failed: int
    assertions: int
    failed_assertions: int
    invariant_violations: int
    result: EvalResult


def _summarize(spec: EvalSpec, cases: list[EvalCase]) -> SpecSummary:
    graded = ignored = links = 0
    for checkpoint in spec.checkpoints:
        for rule in checkpoint.objects:
            for field in rule.fields:
                if field.match == "ignore":
                    ignored += 1
                else:
                    graded += 1
            links += len(rule.links)
    return SpecSummary(
        id=spec.id, flow_id=spec.flow_id, version=spec.version,
        summary=spec.summary, checkpoints=len(spec.checkpoints),
        cases=len(cases), graded_fields=graded, ignored_fields=ignored,
        graded_links=links,
    )


def _view(result: EvalResult) -> EvalResultView:
    assertions = sum(len(c.assertions) for c in result.cases)
    failed = sum(len(c.failures) for c in result.cases)
    return EvalResultView(
        spec_id=result.spec_id, flow_id=result.flow_id,
        spec_version=result.spec_version, at_iso=result.at_iso,
        launched_by=result.launched_by, passed=result.passed,
        failed=result.failed, assertions=assertions, failed_assertions=failed,
        invariant_violations=sum(len(c.invariants) for c in result.cases),
        result=result,
    )


@router.get("", response_model=list[SpecSummary])
def list_specs(
    flow_id: str | None = Query(default=None),
    principal: Principal = Depends(get_principal),
) -> list[SpecSummary]:
    require_reader(principal)
    service = deps.get_eval_service()
    return [
        _summarize(spec, service.cases(spec.id))
        for spec in service.specs(flow_id=flow_id)
    ]


@router.get("/{spec_id}", response_model=EvalSpec)
def get_spec(
    spec_id: str, principal: Principal = Depends(get_principal)
) -> EvalSpec:
    require_reader(principal)
    spec = deps.get_eval_service().spec(spec_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"No eval spec: {spec_id}")
    return spec


@router.get("/{spec_id}/cases", response_model=list[EvalCase])
def list_cases(
    spec_id: str, principal: Principal = Depends(get_principal)
) -> list[EvalCase]:
    require_reader(principal)
    service = deps.get_eval_service()
    if service.spec(spec_id) is None:
        raise HTTPException(status_code=404, detail=f"No eval spec: {spec_id}")
    return service.cases(spec_id)


@router.get("/{spec_id}/result", response_model=EvalResultView)
def last_result(
    spec_id: str, principal: Principal = Depends(get_principal)
) -> EvalResultView:
    require_reader(principal)
    service = deps.get_eval_service()
    result = service.last_result(spec_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No result for {spec_id} in this process yet — results are not "
                "persisted; POST /run to produce one."
            ),
        )
    return _view(result)


@router.post("/{spec_id}/run", response_model=EvalResultView)
def run_spec(
    spec_id: str,
    case_id: list[str] | None = Query(default=None),
    principal: Principal = Depends(get_principal),
) -> EvalResultView:
    """Run the cases in an isolated world and grade them against the spec.

    Every case gets its own in-memory world with the corpus copied in, so a run
    cannot touch the live graph — which matters more than it sounds, because
    object ids derive from natural keys and eval invoices would otherwise land
    in the anomaly baseline that real invoices are judged against.
    """
    require_role(principal, "admin")
    service = deps.get_eval_service()
    if service.spec(spec_id) is None:
        raise HTTPException(status_code=404, detail=f"No eval spec: {spec_id}")
    result = service.run(spec_id, case_ids=case_id, launched_by=principal.uid)
    log.info(
        "Eval launched via API",
        extra={
            "specId": spec_id, "passed": result.passed, "failed": result.failed,
            "actor": principal.uid,
        },
    )
    return _view(result)
