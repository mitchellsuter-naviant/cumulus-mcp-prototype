"""REST surface for the estate connector.

Two routes, and the second one is the point of the module: `POST /pull` is
the **port** the Scheduling capability plugs into. Deployed, a managed
scheduler calls it on a cadence and Cumulus runs nothing warm of its own; in
dev an interval thread calls the same code path. Nothing about the schedule is
recorded here, because the schedule belongs to whatever is doing the calling.

Pulling *one* feed on demand is the operator's version of the same act, and it
has exactly one door: the console's `POST /api/ui/v1/sources/{source}/
sync-now`, which calls `pull_feed_now` below. There used to be a second
spelling of it here, gating the same act on the same role and restating the
same three refusals — two doors onto one act is two authorization policies
waiting to drift apart, and the machine door had no caller.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from . import deps
from .auth import Principal, get_principal, require_reader, require_role
from .estate import EstateError

log = logging.getLogger("cumulus.estate.api")

router = APIRouter(prefix="/api/v1/estate", tags=["estate"])


@router.get("")
def get_estate(principal: Principal = Depends(get_principal)) -> dict:
    """Registration, the discovery echo, and where every feed's cursor stands.

    A reader's route, and `registration()` is already the curated shape: it
    reports *whether* a credential is configured and not which, and it does not
    carry the address of our own delivery door. Everything here is about the
    customer's estate and how their feeds are running, which is what a reader
    is entitled to.
    """
    require_reader(principal)
    return deps.get_estate_connector().registration()


def pull_feed_now(feed: str, actor: str) -> dict:
    """Pull one feed, synchronously, with this act's three refusals.

    Called by the console's `/api/ui/v1/sources/{source}/sync-now`. That route
    checks authority (`admin`, because a pull writes through the door) and this
    answers what the act itself can refuse: a feed nobody registers is a 404,
    no estate at all is a 400, and an estate that is down is a **502** rather
    than our own 500 — the failure is upstream, and a 500 would send an
    operator to read the wrong logs.
    """
    connector = deps.get_estate_connector()
    if connector.spec(feed) is None:
        raise HTTPException(status_code=404, detail=f"No such feed: {feed}")
    if not connector.registered:
        raise HTTPException(
            status_code=400,
            detail="No estate is registered — set ESTATE_PROJECT",
        )
    try:
        outcome = connector.pull(feed)
    except EstateError as exc:
        log.warning(
            "Sync-now failed",
            extra={"feed": feed, "actor": actor, "error": str(exc)},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    log.info(
        "Sync-now completed",
        extra={"feed": feed, "actor": actor,
               "outcome": outcome["outcome"], "chunks": len(outcome["chunks"])},
    )
    return outcome


@router.post("/pull")
def pull_due(principal: Principal = Depends(get_principal)) -> dict:
    """Every active feed — the sourced scheduler's target.

    One red feed is reported, not raised: a scheduler that got a 502 because
    the fifth source was down would retry four healthy pulls it already did.
    """
    require_role(principal, "admin")
    feeds = deps.get_estate_connector().pull_all()
    return {"feeds": feeds}
