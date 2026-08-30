"""Materializing a Fixture: what a declared world actually asserts.

The ontology is a TBox and the console already draws it. The *ABox* — the
instances — was visible in only one form: whatever the live graph happens to
hold. But a good deal of ABox is **authored**, in Python, and was invisible.
Every one of those fragments is a `Fixture` (`evals.py`): the world an eval
case runs in, and the demo seed's feed deliveries, which are the same kind of
thing and so live in one registry. Browsing and grading read the same
declaration, which is the only reason a preview is worth trusting.

**Materialize rather than predict.** A fixture is shown by running it into a
throwaway in-memory world and reading the graph back — not by statically
guessing what it would produce. That difference matters: a delivery goes
through the real sync flow, so what you see includes the resolution the flow
performed (which vendor it matched, at what score, by what method) and the
Tasks it escalated. A static expansion would show the payload; this shows the
consequence. It is also the fixture *editor*: authoring a world is worth
nothing without seeing what it asserts.

It also answers the question a natural key always raises: what id does this
resolve to. Every materialized object carries both.

Nothing here writes anywhere real — it establishes the world through the same
`seed_fixture` a graded run does, so a preview cannot show something an eval
never sees.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from .graph import TBoxViolation
from .eval_world import build_world
from .evals import Fixture, seed_fixture

log = logging.getLogger("cumulus.worlds")


class MaterializedClaim(BaseModel):
    field: str
    value: Any = None
    source: str
    confidence: float = 1.0
    resolves: bool = False


class MaterializedObject(BaseModel):
    id: str
    type: str
    # The pair that answers "what does this key resolve to".
    natural_key: str
    label: str
    status: str
    props: dict[str, Any] = Field(default_factory=dict)
    claims: list[MaterializedClaim] = Field(default_factory=list)
    ref: str = ""
    conflict_fields: list[str] = Field(default_factory=list)


class MaterializedLink(BaseModel):
    id: str
    type: str
    from_id: str
    to_id: str
    from_label: str = ""
    to_label: str = ""
    method: str = ""
    confidence: float = 1.0
    props: dict[str, Any] = Field(default_factory=dict)
    live: bool = True


class MaterializedRun(BaseModel):
    """A sync run the fragment drove on its way in."""

    id: str
    flow_id: str
    status: str
    steps: int = 0
    outputs: dict = Field(default_factory=dict)


class MaterializedTask(BaseModel):
    id: str
    kind: str
    title: str
    queue: str


class MaterializedWorld(BaseModel):
    # `world` rather than `fixture`: the fixture *is* the world, and this is
    # what it turned out to be.
    world: Fixture
    objects: list[MaterializedObject] = Field(default_factory=list)
    links: list[MaterializedLink] = Field(default_factory=list)
    runs: list[MaterializedRun] = Field(default_factory=list)
    tasks: list[MaterializedTask] = Field(default_factory=list)
    by_type: dict[str, int] = Field(default_factory=dict)
    by_link_type: dict[str, int] = Field(default_factory=dict)
    violations: list[TBoxViolation] = Field(default_factory=list)
    # True when a cap dropped instances. Said out loud, because a view that
    # silently shows part of a graph reads as showing all of it.
    truncated: bool = False
    error: str = ""


# ---------------------------------------------------------------------------
# Materializing
# ---------------------------------------------------------------------------
#
# The registry that used to live here is gone: the catalog of authored worlds
# is the FixtureStore, and a caller resolves an id through that before asking
# for a preview.


def materialize(fixture: Fixture, knowledge=None) -> MaterializedWorld:
    """Run a fixture into a throwaway world and read the graph back."""
    result = MaterializedWorld(world=fixture)
    try:
        sandbox = build_world(knowledge)
        seed_fixture(
            sandbox, fixture,
            actor="system:world-preview", provenance="authored fixture",
        )
        records = sandbox.graph.store.objects()
        by_id = {record.id: record for record in records}
        for record in sorted(records, key=lambda r: (r.type, r.label)):
            conflicts = record.conflicts()
            resolved = record.props
            result.objects.append(MaterializedObject(
                id=record.id, type=record.type, natural_key=record.natural_key,
                label=record.label, status=record.status, props=resolved,
                ref=record.ref(), conflict_fields=sorted(conflicts),
                claims=[
                    MaterializedClaim(
                        field=claim.field, value=claim.value, source=claim.source,
                        confidence=claim.confidence,
                        # Marked against the resolved value rather than
                        # recomputed, so the view cannot disagree with the
                        # precedence the store applied.
                        resolves=(
                            claim.field in resolved
                            and f"{resolved[claim.field]}" == f"{claim.value}"
                        ),
                    )
                    for claim in record.claims
                ],
            ))
            result.by_type[record.type] = result.by_type.get(record.type, 0) + 1
        for link in sandbox.graph.store.links():
            result.links.append(MaterializedLink(
                id=link.id, type=link.type, from_id=link.from_id,
                to_id=link.to_id,
                from_label=(by_id.get(link.from_id).label if by_id.get(link.from_id) else ""),
                to_label=(by_id.get(link.to_id).label if by_id.get(link.to_id) else ""),
                method=link.method, confidence=link.confidence,
                props=link.props, live=link.live,
            ))
            result.by_link_type[link.type] = result.by_link_type.get(link.type, 0) + 1
        result.runs = [
            MaterializedRun(
                id=run.id, flow_id=run.flow_id, status=run.status,
                steps=len(run.steps), outputs=run.outputs,
            )
            for run in sandbox.engine.list()
        ]
        result.tasks = [
            MaterializedTask(
                id=task.id, kind=task.kind, title=task.title, queue=task.queue
            )
            for task in sandbox.tasks.list()
        ]
        result.violations = sandbox.graph.check_invariants()
        log.info(
            "World materialized",
            extra={
                "fixtureId": fixture.id, "objects": len(result.objects),
                "links": len(result.links), "runs": len(result.runs),
            },
        )
    except Exception as exc:  # noqa: BLE001 — a broken fixture is a viewable fact
        log.exception(
            "Materializing a fixture failed", extra={"fixtureId": fixture.id}
        )
        result.error = f"{type(exc).__name__}: {exc}"
    return result


# ---------------------------------------------------------------------------
# The live ABox, in the same shape
# ---------------------------------------------------------------------------


def _project(records, links, by_id) -> tuple[list[MaterializedObject], list[MaterializedLink]]:
    objects: list[MaterializedObject] = []
    for record in sorted(records, key=lambda r: (r.type, r.label)):
        resolved = record.props
        objects.append(MaterializedObject(
            id=record.id, type=record.type, natural_key=record.natural_key,
            label=record.label, status=record.status, props=resolved,
            ref=record.ref(), conflict_fields=sorted(record.conflicts()),
            claims=[
                MaterializedClaim(
                    field=claim.field, value=claim.value, source=claim.source,
                    confidence=claim.confidence,
                    resolves=(
                        claim.field in resolved
                        and f"{resolved[claim.field]}" == f"{claim.value}"
                    ),
                )
                for claim in record.claims
            ],
        ))
    projected_links = [
        MaterializedLink(
            id=link.id, type=link.type, from_id=link.from_id, to_id=link.to_id,
            from_label=(by_id[link.from_id].label if link.from_id in by_id else ""),
            to_label=(by_id[link.to_id].label if link.to_id in by_id else ""),
            method=link.method, confidence=link.confidence, props=link.props,
            live=link.live,
        )
        for link in links
    ]
    return objects, projected_links


def project_live(graph, object_type: str | None = None, limit: int = 400) -> MaterializedWorld:
    """The running graph, in the same shape as an authored fragment.

    So that live and authored ABoxes are peers in the console rather than two
    views that happen to look similar: one renderer, one model, and the only
    difference is whether the instances were observed or declared.

    A type filter pulls in that type's one-hop neighbours too — a screen full
    of boxes with no edges is a worse answer than a smaller one that shows how
    they connect.
    """
    everything = graph.store.objects()
    by_id = {record.id: record for record in everything}
    if object_type:
        seeds = [r for r in everything if r.type == object_type][:limit]
        keep = {r.id for r in seeds}
        for record in seeds:
            for link in graph.store.links_from(record.id):
                keep.add(link.to_id)
            for link in graph.store.links_to(record.id):
                keep.add(link.from_id)
        records = [by_id[oid] for oid in keep if oid in by_id]
    else:
        records = everything[:limit]
        keep = {r.id for r in records}
    links = [
        link for link in graph.store.links()
        if link.from_id in keep and link.to_id in keep
    ]
    objects, projected_links = _project(records, links, by_id)
    result = MaterializedWorld(
        world=Fixture(
            id="live",
            name=object_type or "The running graph",
            summary=(
                f"Instances of {object_type} in the live graph, with what they "
                "connect to."
                if object_type else
                "Every instance the flows have written, as it stands now."
            ),
            derived_from="written by Flows, through Actions",
        ),
        objects=objects,
        links=projected_links,
    )
    for record in records:
        result.by_type[record.type] = result.by_type.get(record.type, 0) + 1
    for link in links:
        result.by_link_type[link.type] = result.by_link_type.get(link.type, 0) + 1
    result.truncated = len(everything) > len(records)
    return result
