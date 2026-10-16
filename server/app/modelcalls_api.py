"""REST surface for the ModelCall ledger — the spend claim, made checkable.

Read-only, and there is no way to make it otherwise: the store has an `append`
and nothing else, and the only thing that appends is `GovernedModelClient`. A
route that could write a row would be a route that could write a *false* row,
which is worse than no ledger at all — the whole value of this collection is
that a number on it was recorded by the code that made the call.

Filtering runs in Python over the store's own ordering, for `ledger_api`'s
reason: this is read by an operator answering one question about a bill, not on
a hot path, and one narrow index would not serve five filters.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from . import deps
from .auth import Principal, get_principal, require_reader
from .modelcall import ModelCallRecord, ModelCallStats, Outcome, summarize

log = logging.getLogger("cumulus.modelcalls.api")

router = APIRouter(prefix="/api/v1/modelcalls", tags=["modelcalls"])

#: How deep a filtered read goes before the filters run. The store returns
#: newest-first, so this is a window on recent history rather than a sample of
#: all of it — a filter that finds nothing inside it has found nothing recent,
#: which is the honest reading and the one an operator wants. Bounded because
#: on Firestore every row in the window is a document read, and a spend page
#: that costs a thousand reads to answer is its own small ongoing bill.
_SCAN = 1000


def _matching(
    run_id: str | None,
    flow_id: str | None,
    model: str | None,
    purpose: str | None,
    outcome: str | None,
    since: str | None,
) -> list[ModelCallRecord]:
    return [
        r for r in deps.get_modelcall_store().list(limit=_SCAN)
        if (run_id is None or r.run_id == run_id)
        and (flow_id is None or r.flow_id == flow_id)
        and (model is None or r.model == model)
        and (purpose is None or r.purpose == purpose)
        and (outcome is None or r.outcome == outcome)
        and (since is None or r.at_iso >= since)
    ]


@router.get("", response_model=list[ModelCallRecord])
def list_model_calls(
    run_id: str | None = Query(default=None),
    flow_id: str | None = Query(default=None),
    model: str | None = Query(default=None),
    purpose: str | None = Query(default=None),
    outcome: Outcome | None = Query(default=None),
    since: str | None = Query(
        default=None,
        description="ISO-8601 instant; rows at or after it. Compared as text, "
                    "which is exact for the ISO instants this ledger writes.",
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    principal: Principal = Depends(get_principal),
) -> list[ModelCallRecord]:
    """The ledger, newest first."""
    require_reader(principal)
    return _matching(run_id, flow_id, model, purpose, outcome, since)[:limit]


@router.get("/stats", response_model=ModelCallStats)
def model_call_stats(
    run_id: str | None = Query(default=None),
    flow_id: str | None = Query(default=None),
    model: str | None = Query(default=None),
    purpose: str | None = Query(default=None),
    outcome: Outcome | None = Query(default=None),
    since: str | None = Query(default=None),
    principal: Principal = Depends(get_principal),
) -> ModelCallStats:
    """The same slice, rolled up — and how much of the bill is actually known.

    Rolled up over exactly the rows the list route would return, so the strip
    and the table under it cannot be computed over two different slices. That
    bug has been caught here before, on the task badge.
    """
    require_reader(principal)
    return summarize(_matching(run_id, flow_id, model, purpose, outcome, since))


@router.get("/{call_id}", response_model=ModelCallRecord)
def get_model_call(
    call_id: str, principal: Principal = Depends(get_principal)
) -> ModelCallRecord:
    require_reader(principal)
    record = deps.get_modelcall_store().get(call_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No model call {call_id}")
    return record
