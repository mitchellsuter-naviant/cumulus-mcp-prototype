"""REST surface for authored ABoxes.

Read-only, and materializing is a read: the fragment runs into a throwaway
in-memory world and nothing outside it is touched. That is why this is `user+`
rather than admin — looking at what a fixture asserts is not a privileged act,
and the isolation is what makes that true rather than a promise.

Materializing is not free (it runs the sync flows), so it is a separate call
from the listing: the catalog is cheap and the detail is on demand.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from . import deps, worlds
from .auth import Principal, get_principal, require_reader
from .worlds import AuthoredWorld, MaterializedWorld

log = logging.getLogger("cumulus.worlds.api")

router = APIRouter(prefix="/api/v1/worlds", tags=["worlds"])


@router.get("", response_model=list[AuthoredWorld])
def list_worlds(
    principal: Principal = Depends(get_principal),
) -> list[AuthoredWorld]:
    """The ABox fragments declared in code — eval fixtures and the demo seed."""
    require_reader(principal)
    return worlds.worlds()


@router.get("/{world_id:path}", response_model=MaterializedWorld)
def get_world(
    world_id: str, principal: Principal = Depends(get_principal)
) -> MaterializedWorld:
    """Run the fragment into a throwaway world and return what it asserts.

    Materialized rather than statically expanded, so what comes back includes
    the resolution the sync flow actually performed — which vendor it matched,
    at what score, by what method — and any Task it escalated. A prediction
    would show the payload; this shows the consequence.
    """
    require_reader(principal)
    result = worlds.materialize(world_id, knowledge=deps.get_service())
    if result is None:
        raise HTTPException(status_code=404, detail=f"No such world: {world_id}")
    return result
