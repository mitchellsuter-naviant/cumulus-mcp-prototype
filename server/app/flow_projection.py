"""What a Flow touches in the ontology, read off its own runs.

A Flow's relationship to the ontology has been *declared* in one place and
*observed* in none. `data_collections.py` states which flows write and read
each Collection, which is a statement of intent that nothing checks. Meanwhile
every `StepRecord` has carried both halves of the answer all along: `refs` is
what the step read and `writes` is what it wrote, as namespaced refs collected
off the `GraphWriter` rather than typed in by hand. Aggregating those over a
flow's runs gives its true projection onto the ontology, and the reason to
compute both halves is the diff between them — *declared but never observed*
is a stale declaration or an untested path, *observed but not declared* is a
governance gap.

Like `RunRecord.journal()`, this is a projection and not a second stored copy.
There is one record of what a flow did — its runs — and everything here is a
view of it. Memoizing is safe for the same reason: the key is the run count
plus the newest `updated_at`, so an appended run or a resumed one invalidates
the entry in one comparison and there is no window in which a stale answer can
be served.

Two things the aggregation has to get right, and both are live in the seeded
data:

- **Aggregate over `step.refs`, never `run.refs`.** The run-level list is a
  roll-up `RunContext.note_refs` maintains as a convenience, and it is only
  ever as complete as the passes that happened to append to it. The steps are
  where a read is actually recorded, and they are what replay preserves.
- **The revision suffix is optional.** See `graph.parse_ref`. Insisting on
  `@rev` drops the AP flow's only graph read, because a purchase order looked
  up by the number printed on an invoice has no minted id to pin.

`document:` refs are counted rather than projected. A document ref addresses
one artifact, not a type, so it belongs to a run's trace rather than to a
flow's shape against the ontology — but dropping it silently would make the
projection look more complete than it is, so `unprojected_refs` says how many
there were.
"""

from __future__ import annotations

from typing import Literal
from weakref import WeakKeyDictionary

from pydantic import BaseModel, Field

from . import data_collections
from .graph import parse_ref
from .runs import RunEngine, RunRecord

# The most recent runs to read per flow. A flow with more history than this
# has a projection drawn from a window, which `scanned_runs` / `total_runs`
# make visible rather than leaving the caller to assume otherwise.
SCAN_LIMIT = 200

ProjectionState = Literal["never_ran", "ran_but_wrote_nothing", "observed"]


class FlowProjection(BaseModel):
    """One flow's footprint on the ontology, observed and declared.

    `state` is the distinction that matters most. A flow that has never run
    and a flow that ran and wrote nothing both produce an empty `writes`, and
    they are opposite facts: the first is unknown, the second is a read-only
    flow doing exactly what it should. Callers must be able to say which,
    which is why an empty set is never allowed to stand for both.

    The four diff fields carry their reading in their names. `undeclared_*`
    is observed with nothing declaring it — a governance gap. `unobserved_*`
    is declared and never seen — a stale declaration, or a path no run has
    yet exercised.
    """

    flow_id: str
    state: ProjectionState

    # Observed, from the steps of the scanned runs.
    reads: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)
    writes: list[str] = Field(default_factory=list)
    establishes: list[str] = Field(default_factory=list)
    unprojected_refs: int = 0

    # Declared, from the Collection registry.
    declared_reads: list[str] = Field(default_factory=list)
    declared_writes: list[str] = Field(default_factory=list)
    read_collections: list[str] = Field(default_factory=list)
    written_collections: list[str] = Field(default_factory=list)

    # The diff, which is the point of computing both.
    undeclared_reads: list[str] = Field(default_factory=list)
    undeclared_writes: list[str] = Field(default_factory=list)
    unobserved_reads: list[str] = Field(default_factory=list)
    unobserved_writes: list[str] = Field(default_factory=list)

    scanned_runs: int = 0
    total_runs: int = 0

    @property
    def truncated(self) -> bool:
        return self.total_runs > self.scanned_runs


# Keyed weakly on the engine, so the console's world and an eval's isolated
# copy can never be served each other's answers — they are different worlds
# that share flow ids by design. Keyed within it on the run count and the
# newest `updated_at`, which is what makes an appended or resumed run
# invalidate the entry with no staleness window.
_MemoEntry = tuple[tuple[int, str], FlowProjection]
_memo: WeakKeyDictionary[RunEngine, dict[str, _MemoEntry]] = WeakKeyDictionary()


def project_flow(engine: RunEngine, flow_id: str) -> FlowProjection:
    """The flow's projection onto the ontology, memoized on its run set."""
    return _project_with_memo(engine, flow_id, engine.list(flow_id=flow_id))


def _project_with_memo(
    engine: RunEngine, flow_id: str, runs: list[RunRecord]
) -> FlowProjection:
    """The memoized core, taking the runs so a caller that already fetched
    them all — `project_flows` fetches once and groups — does not fetch again.
    `runs` must arrive newest-first, which is the order `engine.list` answers
    in, because `_project` scans only the first SCAN_LIMIT of them."""
    key = (len(runs), max((r.updated_at_iso for r in runs), default=""))
    cache = _memo.setdefault(engine, {})
    cached = cache.get(flow_id)
    if cached is not None and cached[0] == key:
        return cached[1]
    projection = _project(flow_id, runs)
    cache[flow_id] = (key, projection)
    return projection


def known_flow_ids(engine: RunEngine) -> list[str]:
    """Every flow the engine can name, whether or not it has ever run.

    Registered bodies rather than the flow registry, because a sync feed *is*
    a flow: five of the seven that reach the graph are inbound syncs and none
    of them has a FlowDefinition, so a registry-only list would report the
    ontology as being written by two flows when it is written by seven. The
    union with the flow ids on stored runs covers a run whose body this
    process never registered, which is the honest read of a journal that
    outlives the code that wrote it.

    The fleet rollups in `flows.py` count the same set. There is one answer to
    "which flows are there", and a second enumeration beside it would be a
    list of the flows somebody remembered to add.
    """
    return sorted(set(engine.registered()) | {r.flow_id for r in engine.store.list()})


def project_flows(engine: RunEngine) -> list[FlowProjection]:
    """One projection per flow the engine can name.

    One fetch of the run store, grouped, rather than one per flow: the store
    answers a flow-filtered list by streaming everything and filtering, so
    projecting N flows through `project_flow` fetched every journal N+1 times
    — the ontology screen's second-largest cost after the conformance sweep,
    and one that grew with every estate pull. The grouping preserves the
    newest-first order `engine.list` answers in, which `_project`'s scan
    window depends on.
    """
    runs_by_flow: dict[str, list[RunRecord]] = {}
    for run in engine.list():
        runs_by_flow.setdefault(run.flow_id, []).append(run)
    ids = sorted(set(engine.registered()) | set(runs_by_flow))
    return [
        _project_with_memo(engine, flow_id, runs_by_flow.get(flow_id, []))
        for flow_id in ids
    ]


def _project(flow_id: str, runs: list[RunRecord]) -> FlowProjection:
    scanned = runs[:SCAN_LIMIT]
    reads: set[str] = set()
    knowledge: set[str] = set()
    writes: set[str] = set()
    establishes: set[str] = set()
    unprojected = 0
    wrote_anything = False

    for run in scanned:
        for step in run.steps:
            for raw in step.refs:
                ref = parse_ref(raw)
                if ref.namespace == "graph" and ref.type:
                    reads.add(ref.type)
                elif ref.namespace == "knowledge" and ref.id:
                    knowledge.add(ref.id)
                else:
                    unprojected += 1
            for raw in step.writes:
                wrote_anything = True
                ref = parse_ref(raw)
                if ref.namespace == "graph" and ref.type:
                    writes.add(ref.type)
                elif ref.namespace == "link" and ref.type:
                    establishes.add(ref.type)
                else:
                    unprojected += 1

    if not runs:
        state: ProjectionState = "never_ran"
    elif not wrote_anything:
        state = "ran_but_wrote_nothing"
    else:
        state = "observed"

    written = data_collections.written_by(flow_id)
    read = data_collections.read_by(flow_id)
    declared_writes = {t for c in written for t in c.object_types}
    declared_reads = {t for c in read for t in c.object_types}

    return FlowProjection(
        flow_id=flow_id,
        state=state,
        reads=sorted(reads),
        knowledge=sorted(knowledge),
        writes=sorted(writes),
        establishes=sorted(establishes),
        unprojected_refs=unprojected,
        declared_reads=sorted(declared_reads),
        declared_writes=sorted(declared_writes),
        read_collections=sorted(c.id for c in read),
        written_collections=sorted(c.id for c in written),
        undeclared_reads=sorted(reads - declared_reads),
        undeclared_writes=sorted(writes - declared_writes),
        unobserved_reads=sorted(declared_reads - reads),
        unobserved_writes=sorted(declared_writes - writes),
        scanned_runs=len(scanned),
        total_runs=len(runs),
    )
