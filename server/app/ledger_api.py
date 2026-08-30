"""REST surface for the curation ledger — the governance claim, made checkable.

Read-only, and there is no way to make it otherwise: the ledger has an
`append` and nothing else, and the only thing that appends is
`gateway.ActionGateway`. A route that could write an entry would be a route
that could write a *false* entry, which is worse than no ledger at all.

Scoped like every other read here: a principal that reads beyond itself sees
the whole ledger, and one confined to its own work sees the entries it was a
party to — as the committer, or as the operator behind an impersonated one.
The doctrine asks for fine-grained permissions on reads too, and an audit log
that every user can read in full is a directory of who decided what about whom.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from . import deps
from .auth import Principal, get_principal, require_reader
from .gateway import ACTIONS, LedgerEntry

log = logging.getLogger("cumulus.ledger.api")

router = APIRouter(prefix="/api/v1/ledger", tags=["ledger"])


def _visible(entry: LedgerEntry, principal: Principal) -> bool:
    if principal.reads_beyond_self:
        return True
    return principal.uid in (
        entry.principal_uid, entry.impersonated_by
    ) or principal.email in (entry.principal_email, entry.decided_by)


@router.get("", response_model=list[LedgerEntry])
def list_entries(
    principal: Principal = Depends(get_principal),
    regime: str | None = Query(default=None),
    action: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    task_id: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[LedgerEntry]:
    """The ledger, newest first.

    Filtering runs in Python over the store's own ordering: the ledger is read
    by an auditor answering one question, not on a hot path, and one narrow
    index would not serve six filters.
    """
    require_reader(principal)
    entries = [
        e for e in deps.get_action_gateway().ledger.list()
        if _visible(e, principal)
        and (regime is None or e.regime == regime)
        and (action is None or e.action == action)
        and (run_id is None or e.run_id == run_id)
        and (task_id is None or e.task_id == task_id)
        and (outcome is None or e.outcome == outcome)
    ]
    return entries[:limit]


@router.get("/actions")
def list_actions(principal: Principal = Depends(get_principal)) -> list[dict]:
    """The closed set of guarded mutations, and the regime each one is.

    Served because "which writes are governed" is a question a CISO asks
    before any particular entry — and because a set that can be enumerated is
    a set that can be reviewed.
    """
    require_reader(principal)
    return [a.model_dump() for a in ACTIONS.values()]


@router.get("/{entry_id}", response_model=LedgerEntry)
def get_entry(
    entry_id: str, principal: Principal = Depends(get_principal)
) -> LedgerEntry:
    require_reader(principal)
    entry = deps.get_action_gateway().ledger.get(entry_id)
    if entry is None or not _visible(entry, principal):
        # The same 404 either way: telling a caller an entry exists but is not
        # theirs leaks the fact that a decision was made.
        raise HTTPException(status_code=404, detail=f"No ledger entry {entry_id}")
    return entry
