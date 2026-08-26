"""A fully wired in-memory world, for tests that exercise a whole Flow.

The construction itself lives in `app.evals.build_world`, because the eval
harness needs exactly the same thing — an isolated copy of the machinery with
the corpus copied in rather than shared. Duplicating it here would guarantee
the two drift, and the test world is the one that would quietly stop matching
what an eval actually runs against.

`install(...)` swaps the world into `app.deps`, so tests reach it through the
same singletons the API does.
"""

from __future__ import annotations

import app.deps as deps
from app.config import settings
from app.evals import EvalWorld, build_world

World = EvalWorld


def build(*, seed_corpus: bool = True) -> EvalWorld:
    """A fresh world. `seed_corpus=False` leaves the knowledge layer empty."""
    world = build_world(seed_dir=settings.seed_dir if seed_corpus else None)
    if not seed_corpus:
        for asset in world.knowledge.repo.all():
            world.knowledge.repo.delete(asset.id)
    return world


def install(world: EvalWorld) -> EvalWorld:
    deps._service = world.knowledge
    deps._task_service = world.tasks
    deps._run_engine = world.engine
    deps._graph_service = world.graph
    deps._document_service = world.documents
    deps._anomaly_service = world.anomaly
    deps._sync_service = world.sync
    deps._reimbursement_service = world.expenses
    deps._payable_service = world.payables
    # The eval service copies *this* corpus into each case's world, so a test
    # that seeds the world also seeds what its evals read.
    deps._eval_service = None
    return world


def fresh(*, seed_corpus: bool = True) -> EvalWorld:
    return install(build(seed_corpus=seed_corpus))
