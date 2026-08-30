"""`/api/v1/worlds`, aliased onto the fixture routes.

There is one concept here now — a Fixture is the world an eval case runs in
*and* the fragment the ABox explorer browses — so there is one implementation.
These are the same two endpoint functions from `evals_api`, mounted at the
path the console already calls, rather than a second copy that would drift.

The path survives because the explorer is not only about evals: it puts the
live graph and a declared world side by side, and "worlds" is the honest name
for that shelf. The fixture routes are the ones to build on.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from .evals import FIXTURE_PAYLOAD, Fixture
from .evals_api import list_fixtures, preview_fixture
from .worlds import MaterializedWorld

log = logging.getLogger("cumulus.worlds.api")

router = APIRouter(prefix="/api/v1/worlds", tags=["worlds"])

router.add_api_route(
    "",
    list_fixtures,
    methods=["GET"],
    response_model=list[Fixture],
    response_model_exclude={"__all__": FIXTURE_PAYLOAD},
    summary="The worlds declared in code — eval fixtures and the demo seed",
)
router.add_api_route(
    "/{fixture_id:path}",
    preview_fixture,
    methods=["GET"],
    response_model=MaterializedWorld,
    response_model_exclude={"world": FIXTURE_PAYLOAD},
    summary="Run one into a throwaway world and return what it asserts",
)
