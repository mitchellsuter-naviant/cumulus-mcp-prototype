"""REST surface for the estate connector.

Three routes, and the last one is the point of the module: `POST /pull` is
the **port** the Scheduling capability plugs into. Deployed, a managed
scheduler calls it on a cadence and Cumulus runs nothing warm of its own; in
dev an interval thread calls the same code path. Nothing about the schedule is
recorded here, because the schedule belongs to whatever is doing the calling.

`sync-now` is the operator's version of the same act — one feed, synchronously,
with the delivery outcomes handed straight back so a console can show what
landed rather than "accepted". An estate that is down surfaces as a 502: the
failure is upstream, and reporting it as our own 500 would send an operator to
read the wrong logs. The console has a door onto the same act, and it calls
`pull_feed_now` below rather than restating those refusals — two spellings of
"no such feed" and "no estate registered" is two authorization policies waiting
to drift apart.
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

    The one implementation behind both doors — this router's route and the
    console's `/api/ui/v1/sources/{source}/sync-now`. The caller checks
    authority (both require `admin`, because a pull writes through the door)
    and this answers what the act itself can refuse: a feed nobody registers is
    a 404, no estate at all is a 400, and an estate that is down is a **502**
    rather than our own 500 — the failure is upstream, and a 500 would send an
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


@router.post("/feeds/{feed}/sync-now")
def sync_now(feed: str, principal: Principal = Depends(get_principal)) -> dict:
    """Pull one feed now. Admin, because a pull writes through the door."""
    require_role(principal, "admin")
    return pull_feed_now(feed, principal.uid)


@router.post("/pull")
def pull_due(principal: Principal = Depends(get_principal)) -> dict:
    """Every active feed — the sourced scheduler's target.

    One red feed is reported, not raised: a scheduler that got a 502 because
    the fifth source was down would retry four healthy pulls it already did.
    """
    require_role(principal, "admin")
    feeds = deps.get_estate_connector().pull_all()
    return {"feeds": feeds}
