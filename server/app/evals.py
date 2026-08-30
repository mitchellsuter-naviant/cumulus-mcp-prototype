"""Graph-shaped evaluation: a per-flow spec over the enterprise model.

The premise this is built on: there is one enterprise model — a global
`Invoice`, a global `Vendor` — and a flow's eval is an **overlay** on it saying
which parts that flow is responsible for and how tightly to judge them. So the
spec, not the case, carries the match policy:

    Invoice.invoice_number   exact
    Invoice.total_usd        approx ±0.01
    Invoice.due_date         ignore          (the vendor's terms, not ours)
    Vendor.name              fuzzy ≥ 0.90    (extraction reads it off a page)
    issued                   exists
    has_invoice_line         count

and the case only supplies the values. Three things fall out of that split:

- **Cases stop repeating themselves.** One decision about how strictly to
  compare a total, made once, applied everywhere.
- **Adding a field to the ontology cannot silently start failing cases.**
  Unlisted fields are ignored by default; a spec that wants the opposite says
  `unlisted_fields: require` and gets told about drift.
- **The spec is reviewable on its own.** "What does the AP flow promise about
  an Invoice" is a document, not an inference over fifty cases.

**Checkpoints** are the other half. A spec evaluates the world at points *in*
the run — after `z1` the vendor should be resolved and linked; after `x3` the
lines should exist; at the end the MatchSet should say `matched`. That is
possible without snapshots because claims and links carry the step that wrote
them: the state as of a step is the resolution of claims whose step came no
later. Point-in-time reconstruction for free, and the direct payoff of
recording provenance.

**Isolation.** A case runs in its own in-memory world. Object ids are derived
from natural keys, so an eval fixture named "Acme Industrial Inc." *is* the
production vendor id — running against the live graph would append claims to a
real vendor and, worse, feed eval invoices into the anomaly baseline that real
invoices are judged against. The corpus is copied in (frozen for the run,
writable, so nothing a graded run writes touches the real shelf) and the
revisions read are recorded, so two results are only compared when they
measured the same knowledge.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, Field

from .anomaly import AnomalyService, InMemoryProfileStore
from .calendars import CalendarService
from .documents import DocumentService, InMemoryDocumentStore
from .graph import (
    Claim,
    GraphService,
    InMemoryGraphStore,
    LinkRecord,
    ObjectRecord,
    TBoxViolation,
    object_id,
    resolve_props,
)
from .models import utcnow
from .resolution import name_similarity
from .runs import InMemoryRunStore, RunEngine, RunRecord
from .service import KnowledgeService
from .storage import InMemoryRepository
from .sync import SyncRequest, SyncService
from .tasks import InMemoryTaskDirectory, TaskService

log = logging.getLogger("cumulus.evals")


# ---------------------------------------------------------------------------
# The spec: how to judge, declared per flow over the shared model
# ---------------------------------------------------------------------------

FieldMatch = Literal[
    "exact", "approx", "fuzzy", "one_of", "present", "absent", "ignore"
]


class FieldRule(BaseModel):
    """How to compare one field of one object type, for this flow.

    `ignore` is as important as the rest and is used deliberately: a due date
    is the vendor's payment terms rather than something the flow establishes,
    so grading it would be grading the wrong system.
    """

    field: str
    match: FieldMatch = "exact"
    # approx: absolute tolerance. fuzzy: similarity floor, 0–1.
    tolerance: float = 0.0
    options: list[str] = Field(default_factory=list)
    note: str = ""


class LinkRule(BaseModel):
    """A subset of the edges this flow is answerable for."""

    type: str
    direction: Literal["out", "in"] = "out"
    to_type: str = ""
    expect: Literal["exists", "count", "absent"] = "exists"
    # Grade props on the edge itself — a pairing's variance lives there.
    props: list[FieldRule] = Field(default_factory=list)
    note: str = ""


class ObjectRule(BaseModel):
    type: str
    fields: list[FieldRule] = Field(default_factory=list)
    links: list[LinkRule] = Field(default_factory=list)
    # `require` reports any claimed field the spec does not name — drift
    # detection, at the cost of a spec that has to keep up.
    unlisted_fields: Literal["ignore", "require"] = "ignore"

    def rule_for(self, field: str) -> FieldRule | None:
        return next((r for r in self.fields if r.field == field), None)


TrajectoryKind = Literal[
    "step_ran",
    "step_absent",
    "gate_raised",
    "gate_count",
    "terminal_status",
    "queue",
    "model_calls_at_most",
    "wrote",
    "read",
]


class TrajectoryRule(BaseModel):
    """A claim about the path, phrased semantically rather than by position."""

    kind: TrajectoryKind
    expected: str = ""
    note: str = ""


class Checkpoint(BaseModel):
    """What must be true once a given step has completed.

    `after_step` is a step id, or `final` for the end of the run. Evaluating
    mid-run is what makes a spec able to say "the vendor is resolved before any
    extraction happens" — an ordering promise, not just an outcome.
    """

    id: str
    after_step: str = "final"
    label: str = ""
    objects: list[ObjectRule] = Field(default_factory=list)
    trajectory: list[TrajectoryRule] = Field(default_factory=list)
    note: str = ""

    def rule_for(self, object_type: str) -> ObjectRule | None:
        return next((r for r in self.objects if r.type == object_type), None)


class EvalSpec(BaseModel):
    """One flow's eval model: the overlay, versioned, reviewable on its own."""

    id: str
    flow_id: str
    version: int = 1
    summary: str = ""
    # Never graded, for any type: bookkeeping that moves on its own.
    ignore_fields: list[str] = Field(
        default_factory=lambda: ["revision", "updated_at", "created_at"]
    )
    checkpoints: list[Checkpoint] = Field(default_factory=list)

    def checkpoint(self, checkpoint_id: str) -> Checkpoint | None:
        return next((c for c in self.checkpoints if c.id == checkpoint_id), None)


# ---------------------------------------------------------------------------
# The case: the fixture, the trigger, and the expected values
# ---------------------------------------------------------------------------


class FixtureObject(BaseModel):
    """A precondition entity, in **natural keys** — never ids.

    Ids derive from the key, so a fixture written this way reads identically in
    the eval world and in production, and a case promoted from a real run needs
    no rewriting. Encoding raw ids here is the one thing that would be hard to
    undo later.
    """

    type: str
    key: str
    props: dict[str, Any] = Field(default_factory=dict)
    label: str = ""


class GraphFixture(BaseModel):
    """The world as it must be before the trigger fires.

    Preferred form is `deliveries`: the same push payloads the real feeds send,
    so the precondition is established the way production established it — and
    the sync flows get exercised on the way in. `objects` is the escape hatch
    for entities no feed provides yet, an Employee being the standing example.
    """

    deliveries: list[dict] = Field(default_factory=list)
    objects: list[FixtureObject] = Field(default_factory=list)


class ExpectedObject(BaseModel):
    """What one entity should look like at a checkpoint.

    `key` is a natural key, with two conveniences that exist because some
    natural keys are minted rather than authored:

    - `$run` is substituted with the run's id, so `$run` addresses the
      ExpenseRequest a run created and `$run#1` its first line.
    - an empty key means "the only object of this type", which is the common
      case in a world holding one run's output — and it fails loudly rather
      than guessing if there is more than one.
    """

    type: str
    key: str
    fields: dict[str, Any] = Field(default_factory=dict)
    # Edge counts by link type, for the `count` expectation.
    link_counts: dict[str, int] = Field(default_factory=dict)
    # Props on one edge, keyed `link_type.prop`.
    link_props: dict[str, Any] = Field(default_factory=dict)
    # True when this object must *not* exist at this checkpoint.
    absent: bool = False


class CheckpointExpectation(BaseModel):
    checkpoint: str
    objects: list[ExpectedObject] = Field(default_factory=list)
    trajectory: dict[str, str] = Field(default_factory=dict)


class ScriptedDecision(BaseModel):
    """A gate answer, applied in order as the run suspends.

    A case that reaches a gate with no decision left simply ends suspended,
    which is a legitimate expected outcome and one the trajectory can assert.
    """

    verdict: Literal["approved", "rejected", "corrected"]
    decided_by: str = "eval@customer.example"
    note: str = ""
    payload: dict = Field(default_factory=dict)


class EvalCase(BaseModel):
    id: str
    spec_id: str
    name: str
    summary: str = ""
    fixture: GraphFixture = Field(default_factory=GraphFixture)
    trigger: dict = Field(default_factory=dict)
    decisions: list[ScriptedDecision] = Field(default_factory=list)
    expectations: list[CheckpointExpectation] = Field(default_factory=list)
    source: str = "authored"
    added_by: str = ""


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


class AssertionResult(BaseModel):
    checkpoint: str
    target: str
    field: str = ""
    rule: str = ""
    expected: str = ""
    actual: str = ""
    passed: bool
    detail: str = ""


class CaseResult(BaseModel):
    case_id: str
    name: str
    passed: bool = False
    run_id: str = ""
    run_status: str = ""
    assertions: list[AssertionResult] = Field(default_factory=list)
    invariants: list[TBoxViolation] = Field(default_factory=list)
    error: str = ""
    knowledge_revisions: dict[str, int] = Field(default_factory=dict)

    @property
    def failures(self) -> list[AssertionResult]:
        return [a for a in self.assertions if not a.passed]


class EvalResult(BaseModel):
    spec_id: str
    flow_id: str
    spec_version: int
    at_iso: str = Field(default_factory=lambda: utcnow().isoformat())
    launched_by: str = ""
    cases: list[CaseResult] = Field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def failed(self) -> int:
        return len(self.cases) - self.passed

    def summary(self) -> str:
        return f"{self.passed}/{len(self.cases)} case(s) passed"


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _as_float(value: Any) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def compare(rule: FieldRule, expected: Any, actual: Any) -> tuple[bool, str]:
    """One field, one rule. Returns (passed, why)."""
    if rule.match == "ignore":
        return True, "ignored by the spec"
    if rule.match == "present":
        ok = actual not in (None, "", [], {})
        return ok, "present" if ok else "missing"
    if rule.match == "absent":
        ok = actual in (None, "", [], {})
        return ok, "absent" if ok else f"present as {actual!r}"
    if actual is None:
        return False, "not set"
    if rule.match == "one_of":
        options = rule.options or (
            [str(o) for o in expected] if isinstance(expected, list) else [str(expected)]
        )
        ok = str(actual) in options
        return ok, "one of the allowed values" if ok else f"not in {options}"
    if rule.match == "approx":
        left, right = _as_float(expected), _as_float(actual)
        if left is None or right is None:
            return False, "not numeric"
        tolerance = rule.tolerance or 0.01
        delta = abs(left - right)
        return delta <= tolerance + 1e-9, f"off by {delta:.4f} (tolerance {tolerance})"
    if rule.match == "fuzzy":
        floor = rule.tolerance or 0.85
        score = name_similarity(str(expected), str(actual))
        return score >= floor, f"similarity {score:.2f} (floor {floor:.2f})"
    # exact: numbers compare as numbers so 1425.25 and "1425.25" agree.
    left, right = _as_float(expected), _as_float(actual)
    if left is not None and right is not None:
        ok = round(left, 6) == round(right, 6)
        return ok, "equal" if ok else f"{right} != {left}"
    ok = str(expected) == str(actual)
    return ok, "equal" if ok else "different"


# ---------------------------------------------------------------------------
# Point-in-time view
# ---------------------------------------------------------------------------


class GraphSnapshot(BaseModel):
    """The graph as of a step, reconstructed from claim and link provenance.

    Not a stored snapshot: claims and links record the run and step that wrote
    them, so "as of `z1`" is the resolution of the claims whose step came no
    later, plus everything that predates this run. That is the whole reason to
    record the act as well as the actor.
    """

    objects: list[ObjectRecord] = Field(default_factory=list)
    links: list[LinkRecord] = Field(default_factory=list)
    after_step: str = "final"

    def get(self, object_type: str, natural_key: str) -> ObjectRecord | None:
        wanted = object_id(object_type, natural_key)
        # Ids are minted; the deterministic hash is a key *alias*, so a
        # snapshot lookup goes through the alias list like the live get().
        return next(
            (o for o in self.objects if wanted in o.key_aliases or o.id == wanted),
            None,
        )

    def by_id(self, oid: str) -> ObjectRecord | None:
        return next((o for o in self.objects if o.id == oid), None)

    def edges(
        self, oid: str, link_type: str, direction: str = "out"
    ) -> list[tuple[LinkRecord, ObjectRecord]]:
        out: list[tuple[LinkRecord, ObjectRecord]] = []
        for link in self.links:
            if link.type != link_type or not link.live:
                continue
            other_id = (
                link.to_id if direction == "out" and link.from_id == oid
                else link.from_id if direction == "in" and link.to_id == oid
                else None
            )
            if other_id is None:
                continue
            other = self.by_id(other_id)
            if other is not None:
                out.append((link, other))
        return out


def steps_upto(run: RunRecord, after_step: str) -> tuple[list[str], bool]:
    """The step ids up to and including `after_step`, and whether it was reached.

    One helper, used by both the graph snapshot and the trajectory checks, so a
    checkpoint cannot end up judging state as of one point and the path as of
    another.
    """
    if after_step in ("", "final"):
        return [s.step_id for s in run.steps], True
    prefix: list[str] = []
    for step in run.steps:
        prefix.append(step.step_id)
        if step.step_id == after_step:
            return prefix, True
    return [], False


def snapshot_as_of(
    graph: GraphService, run: RunRecord, after_step: str
) -> GraphSnapshot:
    """Reconstruct the world at a step boundary of one run."""
    if after_step in ("", "final"):
        allowed: set[str] | None = None
    else:
        prefix, _reached = steps_upto(run, after_step)
        allowed = set(prefix)

    def counts(run_id: str, step_id: str) -> bool:
        if allowed is None:
            return True
        if run_id != run.id:
            return True  # predates this run — the fixture, or an earlier run
        return step_id in allowed

    objects: list[ObjectRecord] = []
    for record in graph.store.objects():
        kept = [c for c in record.claims if counts(c.run_id, c.step_id)]
        if record.claims and not kept:
            continue  # minted by a later step: it did not exist yet
        projected = record.model_copy(deep=True)
        projected.claims = kept
        projected.props = resolve_props(kept, graph.ontology.precedence)
        objects.append(projected)
    links = [
        link for link in graph.store.links() if counts(link.run_id, link.step_id)
    ]
    return GraphSnapshot(objects=objects, links=links, after_step=after_step or "final")


# ---------------------------------------------------------------------------
# The isolated world
# ---------------------------------------------------------------------------


class EvalWorld(BaseModel):
    """Everything a flow needs, in memory, sharing nothing writable."""

    model_config = {"arbitrary_types_allowed": True}

    knowledge: Any
    tasks: Any
    engine: Any
    graph: Any
    documents: Any
    anomaly: Any
    sync: Any
    expenses: Any
    payables: Any
    calendars: Any = None
    knowledge_revisions: dict[str, int] = Field(default_factory=dict)


def build_world(source_knowledge=None, *, seed_dir: str | None = None) -> EvalWorld:
    """An isolated world, with the corpus copied in rather than shared.

    Copied rather than shared: a graded run reads governed knowledge and may
    write it, and neither should reach the corpus the console is showing. The
    revisions copied are recorded, so two results are only compared when they
    read the same knowledge.
    """
    # Deferred: the flow modules import this one for their seed helpers.
    from .payables import (
        InMemoryPayableDirectory,
        PayableService,
        StubInvoiceReader,
    )
    from .platform_assets import publish_generated_assets
    from .reimbursements import (
        InMemoryReimbursementDirectory,
        ReimbursementService,
        StubReceiptReader,
    )
    from .config import settings
    from .service import seed_if_empty

    repo = InMemoryRepository()
    revisions: dict[str, int] = {}
    if source_knowledge is not None:
        for asset in source_knowledge.repo.all():
            repo.put(asset.model_copy(deep=True))
            revisions[asset.id] = getattr(asset, "revision", 1)
    knowledge = KnowledgeService(repo)
    if source_knowledge is None:
        seed_if_empty(knowledge, seed_dir or settings.seed_dir)
        publish_generated_assets(knowledge)
        revisions = {a.id: getattr(a, "revision", 1) for a in repo.all()}

    tasks = TaskService(InMemoryTaskDirectory())
    engine = RunEngine(InMemoryRunStore(), tasks)
    graph = GraphService(InMemoryGraphStore())
    documents = DocumentService(InMemoryDocumentStore(), 700_000)
    anomaly = AnomalyService(InMemoryProfileStore(), graph)
    sync = SyncService(graph, knowledge, tasks, engine)
    expenses = ReimbursementService(
        InMemoryReimbursementDirectory(), StubReceiptReader(), knowledge, tasks,
        engine, graph, documents,
    )
    payables = PayableService(
        InMemoryPayableDirectory(), StubInvoiceReader(), knowledge, tasks,
        engine, graph, documents, anomaly,
    )
    calendars = CalendarService(graph)
    return EvalWorld(
        knowledge=knowledge, tasks=tasks, engine=engine, graph=graph,
        documents=documents, anomaly=anomaly, sync=sync, expenses=expenses,
        payables=payables, calendars=calendars, knowledge_revisions=revisions,
    )


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------

# How a case's trigger reaches its flow. Kept as an explicit table rather than
# reflection: a flow that wants to be evaluated has to say how it is started,
# and a typo in a case's `trigger` should be an error rather than a silent
# no-op.
FLOW_ENTRYPOINTS: dict[str, Callable[[EvalWorld, dict], Any]] = {
    "expense-reimbursement": lambda world, trigger: world.expenses.submit(**trigger),
    "accounts-payable": lambda world, trigger: world.payables.submit(**trigger),
}


class SpecStore(Protocol):
    def specs(self) -> list[EvalSpec]: ...
    def cases(self, spec_id: str) -> list[EvalCase]: ...


class EvalService:
    """Runs cases against a spec, in a world nothing else can see."""

    def __init__(self, store: SpecStore, knowledge=None) -> None:
        self.store = store
        # The corpus to copy into each world. None means "seed from disk",
        # which is what a standalone test wants.
        self.knowledge = knowledge
        # The most recent result per spec, in memory only. Not persisted: a
        # result is only comparable against the knowledge revisions it read,
        # and storing numbers nobody can compare is worse than storing none.
        self._results: dict[str, EvalResult] = {}

    # -- reads --------------------------------------------------------------

    def specs(self, flow_id: str | None = None) -> list[EvalSpec]:
        specs = self.store.specs()
        if flow_id is not None:
            specs = [s for s in specs if s.flow_id == flow_id]
        return specs

    def spec(self, spec_id: str) -> EvalSpec | None:
        return next((s for s in self.store.specs() if s.id == spec_id), None)

    def cases(self, spec_id: str) -> list[EvalCase]:
        return self.store.cases(spec_id)

    def last_result(self, spec_id: str) -> EvalResult | None:
        return self._results.get(spec_id)

    # -- the run ------------------------------------------------------------

    def run(
        self, spec_id: str, case_ids: list[str] | None = None, launched_by: str = ""
    ) -> EvalResult:
        spec = self.spec(spec_id)
        if spec is None:
            raise KeyError(f"No eval spec: {spec_id}")
        cases = self.cases(spec_id)
        if case_ids:
            wanted = set(case_ids)
            cases = [c for c in cases if c.id in wanted]
        result = EvalResult(
            spec_id=spec.id, flow_id=spec.flow_id, spec_version=spec.version,
            launched_by=launched_by,
        )
        for case in cases:
            result.cases.append(self.run_case(spec, case))
        self._results[spec.id] = result
        log.info(
            "Eval completed",
            extra={
                "specId": spec.id, "flowId": spec.flow_id,
                "cases": len(result.cases), "passed": result.passed,
                "failed": result.failed, "actor": launched_by,
            },
        )
        return result

    def run_case(self, spec: EvalSpec, case: EvalCase) -> CaseResult:
        outcome = CaseResult(case_id=case.id, name=case.name)
        try:
            world = build_world(self.knowledge)
            outcome.knowledge_revisions = world.knowledge_revisions
            self._seed_fixture(world, case.fixture)
            run = self._drive(world, spec, case)
            outcome.run_id = run.id if run else ""
            outcome.run_status = run.status if run else ""
            if run is None:
                outcome.error = f"No entrypoint registered for {spec.flow_id}"
                return outcome
            outcome.assertions = self._grade(world, spec, case, run)
            # The universal tier, every case, free: a structural bug no
            # expectation named still fails the case.
            outcome.invariants = world.graph.check_invariants()
            outcome.passed = (
                all(a.passed for a in outcome.assertions) and not outcome.invariants
            )
        except Exception as exc:  # noqa: BLE001 — a broken case is a failed case
            log.exception("Eval case raised", extra={"caseId": case.id})
            outcome.error = f"{type(exc).__name__}: {exc}"
            outcome.passed = False
        return outcome

    # -- fixture ------------------------------------------------------------

    def _seed_fixture(self, world: EvalWorld, fixture: GraphFixture) -> None:
        """Establish the precondition the way production would have.

        Deliveries go through the real sync flows, so the fixture is not a
        privileged back door: if the sync path would have rejected or escalated
        a row, the case sees that too.
        """
        for delivery in fixture.deliveries:
            world.sync.deliver(
                SyncRequest.model_validate(delivery), actor="system:eval-fixture"
            )
        writer = world.graph.writer("fixture", "f0")
        for entity in fixture.objects:
            writer.upsert(
                entity.type, entity.key,
                [
                    Claim(field=field, value=value, source="fixture")
                    for field, value in entity.props.items()
                ],
                label=entity.label or entity.key,
                provenance="eval fixture",
            )

    # -- the trigger and the gates -----------------------------------------

    def _drive(
        self, world: EvalWorld, spec: EvalSpec, case: EvalCase
    ) -> RunRecord | None:
        entrypoint = FLOW_ENTRYPOINTS.get(spec.flow_id)
        if entrypoint is None:
            return None
        row = entrypoint(world, dict(case.trigger))
        run_id = getattr(row, "run_id", "") or ""
        for decision in case.decisions:
            run = world.engine.get(run_id)
            if run is None or run.status != "suspended" or not run.open_task_id:
                break
            decided = world.tasks.decide(
                run.open_task_id,
                verdict=decision.verdict,
                decided_by=decision.decided_by,
                note=decision.note,
                payload=decision.payload,
            )
            # Resuming through the flow service is the same path a real
            # decision takes, hooks included.
            if spec.flow_id == "expense-reimbursement":
                world.expenses.resume_for_task(decided)
            else:
                world.payables.resume_for_task(decided)
        return world.engine.get(run_id)

    # -- grading ------------------------------------------------------------

    def _grade(
        self, world: EvalWorld, spec: EvalSpec, case: EvalCase, run: RunRecord
    ) -> list[AssertionResult]:
        results: list[AssertionResult] = []
        for expectation in case.expectations:
            checkpoint = spec.checkpoint(expectation.checkpoint)
            if checkpoint is None:
                results.append(AssertionResult(
                    checkpoint=expectation.checkpoint, target="spec",
                    passed=False,
                    detail=(
                        f"the case expects checkpoint "
                        f"'{expectation.checkpoint}' which the spec does not "
                        "declare"
                    ),
                ))
                continue
            prefix, reached = steps_upto(run, checkpoint.after_step)
            if not reached:
                results.append(AssertionResult(
                    checkpoint=checkpoint.id, target="checkpoint",
                    rule="reached", passed=False,
                    expected=f"step {checkpoint.after_step} runs",
                    actual=", ".join(s.step_id for s in run.steps),
                    detail=(
                        f"the run never reached {checkpoint.after_step} — it "
                        f"ended {run.status}"
                        + (
                            f" at {run.suspended_at_step}"
                            if run.suspended_at_step else ""
                        )
                    ),
                ))
                continue
            snapshot = snapshot_as_of(world.graph, run, checkpoint.after_step)
            for expected in expectation.objects:
                results.extend(
                    self._grade_object(spec, checkpoint, snapshot, expected, run)
                )
            results.extend(
                self._grade_trajectory(checkpoint, expectation, run, world, prefix)
            )
        return results

    def _grade_object(
        self,
        spec: EvalSpec,
        checkpoint: Checkpoint,
        snapshot: GraphSnapshot,
        expected: ExpectedObject,
        run: RunRecord,
    ) -> list[AssertionResult]:
        target = f"{expected.type}[{expected.key or 'the only one'}]"
        record, addressing = self._resolve_expected(snapshot, expected, run)
        if addressing:
            return [AssertionResult(
                checkpoint=checkpoint.id, target=target, rule="addressing",
                passed=False, detail=addressing,
            )]
        if expected.absent:
            return [AssertionResult(
                checkpoint=checkpoint.id, target=target, rule="absent",
                passed=record is None,
                actual="present" if record is not None else "absent",
                detail=(
                    "must not exist at this checkpoint"
                    if record is not None else "correctly absent"
                ),
            )]
        if record is None:
            return [AssertionResult(
                checkpoint=checkpoint.id, target=target, rule="exists",
                passed=False, expected="exists", actual="missing",
                detail=(
                    f"no {expected.type} with natural key '{expected.key}' "
                    f"as of {checkpoint.after_step}"
                ),
            )]

        object_rule = checkpoint.rule_for(expected.type) or ObjectRule(
            type=expected.type
        )
        results: list[AssertionResult] = [AssertionResult(
            checkpoint=checkpoint.id, target=target, rule="exists",
            passed=True, expected="exists", actual=record.ref(),
        )]

        for field, value in expected.fields.items():
            if field in spec.ignore_fields:
                continue
            rule = object_rule.rule_for(field) or FieldRule(field=field)
            passed, why = compare(rule, value, record.props.get(field))
            results.append(AssertionResult(
                checkpoint=checkpoint.id, target=target, field=field,
                rule=rule.match, expected=f"{value}",
                actual=f"{record.props.get(field)}", passed=passed, detail=why,
            ))

        if object_rule.unlisted_fields == "require":
            named = {r.field for r in object_rule.fields} | set(spec.ignore_fields)
            drifted = sorted(set(record.props) - named)
            if drifted:
                results.append(AssertionResult(
                    checkpoint=checkpoint.id, target=target, rule="no-drift",
                    passed=False, actual=", ".join(drifted),
                    detail=(
                        "the flow claimed fields this spec does not name — "
                        "either grade them or say they are ignored"
                    ),
                ))

        results.extend(
            self._grade_links(checkpoint, object_rule, snapshot, record, expected, target)
        )
        return results

    @staticmethod
    def _resolve_expected(
        snapshot: GraphSnapshot, expected: ExpectedObject, run: RunRecord
    ) -> tuple[ObjectRecord | None, str]:
        """The record the expectation is about, or why it could not be named."""
        if not expected.key:
            candidates = [o for o in snapshot.objects if o.type == expected.type]
            if len(candidates) > 1:
                return None, (
                    f"{len(candidates)} {expected.type} objects exist, so an "
                    "empty key is ambiguous — name one"
                )
            return (candidates[0] if candidates else None), ""
        return snapshot.get(expected.type, expected.key.replace("$run", run.id)), ""

    def _grade_links(
        self,
        checkpoint: Checkpoint,
        object_rule: ObjectRule,
        snapshot: GraphSnapshot,
        record: ObjectRecord,
        expected: ExpectedObject,
        target: str,
    ) -> list[AssertionResult]:
        results: list[AssertionResult] = []
        for link_rule in object_rule.links:
            edges = snapshot.edges(record.id, link_rule.type, link_rule.direction)
            label = f"{target} --{link_rule.type}-->"
            if link_rule.expect == "absent":
                results.append(AssertionResult(
                    checkpoint=checkpoint.id, target=label, rule="absent",
                    passed=not edges, expected="0", actual=f"{len(edges)}",
                    detail="no such edge" if not edges else "edge present",
                ))
                continue
            if link_rule.expect == "count":
                wanted = expected.link_counts.get(link_rule.type)
                if wanted is None:
                    continue
                results.append(AssertionResult(
                    checkpoint=checkpoint.id, target=label, rule="count",
                    passed=len(edges) == wanted, expected=f"{wanted}",
                    actual=f"{len(edges)}",
                    detail=f"{len(edges)} edge(s) of {link_rule.type}",
                ))
                continue
            results.append(AssertionResult(
                checkpoint=checkpoint.id, target=label, rule="exists",
                passed=bool(edges), expected="≥1", actual=f"{len(edges)}",
                detail=(
                    f"→ {edges[0][1].label}" if edges else "no such edge"
                ),
            ))
            for prop_rule in link_rule.props:
                key = f"{link_rule.type}.{prop_rule.field}"
                if key not in expected.link_props:
                    continue
                actual = edges[0][0].props.get(prop_rule.field) if edges else None
                passed, why = compare(prop_rule, expected.link_props[key], actual)
                results.append(AssertionResult(
                    checkpoint=checkpoint.id, target=label, field=prop_rule.field,
                    rule=prop_rule.match, expected=f"{expected.link_props[key]}",
                    actual=f"{actual}", passed=passed, detail=why,
                ))
        return results

    def _grade_trajectory(
        self,
        checkpoint: Checkpoint,
        expectation: CheckpointExpectation,
        run: RunRecord,
        world: EvalWorld,
        prefix: list[str],
    ) -> list[AssertionResult]:
        results: list[AssertionResult] = []
        # Scoped to the checkpoint, so "no second model call before the
        # vendor is resolved" means what it says.
        in_scope = [s for s in run.steps if s.step_id in set(prefix)]
        step_ids = [s.step_id for s in in_scope]
        gates = [s for s in in_scope if s.kind == "task"]
        for rule in checkpoint.trajectory:
            expected = expectation.trajectory.get(rule.kind, rule.expected)
            if not expected and rule.kind not in ("step_absent",):
                continue
            passed, actual = self._check_trajectory(
                rule.kind, expected, run, step_ids, gates, world, in_scope
            )
            results.append(AssertionResult(
                checkpoint=checkpoint.id, target="trajectory", field=rule.kind,
                rule=rule.kind, expected=expected, actual=actual, passed=passed,
                detail=rule.note,
            ))
        return results

    @staticmethod
    def _check_trajectory(
        kind: str,
        expected: str,
        run: RunRecord,
        step_ids: list[str],
        gates: list,
        world: EvalWorld,
        in_scope: list,
    ) -> tuple[bool, str]:
        if kind == "step_ran":
            return expected in step_ids, ", ".join(step_ids)
        if kind == "step_absent":
            return expected not in step_ids, ", ".join(step_ids)
        if kind == "gate_raised":
            return any(s.step_id == expected for s in gates), ", ".join(
                s.step_id for s in gates
            )
        if kind == "gate_count":
            return str(len(gates)) == expected, str(len(gates))
        if kind == "terminal_status":
            return run.status == expected, run.status
        if kind == "queue":
            queues = {
                task.queue
                for task in (world.tasks.get(s.task_id) for s in gates if s.task_id)
                if task is not None
            }
            return expected in queues, ", ".join(sorted(queues)) or "none"
        if kind == "model_calls_at_most":
            limit = int(expected or 0)
            calls = sum(
                1 for s in in_scope if s.kind == "modelcall" and s.status == "done"
            )
            return calls <= limit, str(calls)
        if kind == "wrote":
            writes = [ref for step in in_scope for ref in step.writes]
            return (
                any(ref.startswith(expected) for ref in writes),
                ", ".join(dict.fromkeys(writes))[:120] or "nothing",
            )
        if kind == "read":
            reads = [ref for step in in_scope for ref in step.refs]
            return (
                any(ref.startswith(expected) for ref in reads),
                ", ".join(dict.fromkeys(reads))[:120] or "nothing",
            )
        return False, f"unknown trajectory rule '{kind}'"
