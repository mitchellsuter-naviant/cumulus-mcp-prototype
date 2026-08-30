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

**Milestones** are the other half. A spec evaluates the world at points *in*
the run — once the vendor has been settled it should be resolved and linked;
once the invoice exists the MatchSet should say `matched`. That is possible
without snapshots because claims and links carry the step that wrote them: the
state as of a step is the resolution of claims whose step came no later.
Point-in-time reconstruction for free, and the direct payoff of recording
provenance.

A milestone names that point by a **predicate over the journal**, never by a
step id. This is the property the whole eval layer exists to have: flow-level
expectations are affixed to the data that seeds a flow, so ModelCalls and
other transactions can be added, split, renamed or reordered without the
golden set having to be rebuilt. `after_step="z1"` could not survive any of
those; `last_of_kind: resolve` survives all of them.

**The fixture is named.** A case says which world it runs in and carries none,
so three AP cases over one Acme world reference one declaration rather than
holding three copies that drift. That is also what lets a golden set survive a
re-architecture: rewrite the world, leave the cases alone.

**Isolation.** A case runs in its own in-memory world. Object ids are derived
from natural keys, so an eval fixture named "Acme Industrial Inc." *is* the
production vendor id — running against the live graph would append claims to a
real vendor and, worse, feed eval invoices into the anomaly baseline that real
invoices are judged against. The corpus is copied in (frozen for the run,
writable, so nothing a graded run writes touches the real shelf) and the
revisions read are recorded, so two results are only compared when they
measured the same knowledge.

**The gate.** A result is graded against the previous one for the same spec:
no case that passed may now fail, and the pass count may not drop. It runs
only when the two runs measured the same thing — same rules, same cases —
because a pass rate compared across a spec edit is two different questions
wearing one number.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from .eval_world import FLOW_ENTRYPOINTS, EvalWorld, build_world
from .graph import (
    Claim,
    GraphService,
    LinkRecord,
    ObjectRecord,
    TBoxViolation,
    object_id,
    parse_ref,
    resolve_props,
)
from .models import utcnow
from .resolution import name_similarity
from .runs import RunRecord
from .sync import SyncRequest

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


MilestoneAnchor = Literal["first_of_kind", "last_of_kind", "wrote", "terminal"]


class Milestone(BaseModel):
    """What must be true at a point in the run, located by what *happened*.

    This was `after_step="z1"` — a literal step id — which made every golden
    set a hostage of one flow's internal naming. Rename `z1`, split the model
    call in two, insert a step: every case that mentioned the id failed
    without a single fact about the world having changed. A milestone says
    what the point *is*, and is resolved against the journal at grading time:

        last_of_kind   selector = a step kind    "once the resolver has run"
        first_of_kind  selector = a step kind    "the first time it stopped"
        wrote          selector = a ref prefix   "once the invoice exists"
        terminal       —                         "however it ended, if it did"

    Four anchors, and the shortlist was argued down to what cases actually
    need. `wrote` names a commit by its effect and would cover most points —
    but a resolver that links an *already known* vendor writes nothing at all,
    so the point after it can only be named by kind. And a suspended run has
    no terminal to anchor on, so a case asserting what is **not** committed
    before the first gate needs `first_of_kind`. Each of the four is the only
    way to name a milestone some declared case depends on.

    Everything downstream is untouched: this resolves to a step id and
    `steps_upto` / `snapshot_as_of` take it from there. A resolver, not a
    second grading path — which is what keeps it small.
    """

    id: str
    label: str = ""
    anchor: MilestoneAnchor = "terminal"
    # A step kind for the two `*_of_kind` anchors, a ref prefix for `wrote`
    # (keep the trailing slash — `graph:Invoice/` names one type, while
    # `graph:Invoice` would also catch `graph:InvoiceLine/`), and unused by
    # `terminal`.
    selector: str = ""
    note: str = ""
    objects: list[ObjectRule] = Field(default_factory=list)
    trajectory: list[TrajectoryRule] = Field(default_factory=list)

    def rule_for(self, object_type: str) -> ObjectRule | None:
        return next((r for r in self.objects if r.type == object_type), None)

    def describe(self) -> str:
        """The point this names, in words.

        One phrasing, so the failure a reader sees and the published spec they
        review cannot describe the same anchor two different ways.
        """
        if self.anchor == "terminal":
            return "the end of the run, which must have finished"
        if self.anchor == "wrote":
            return f"a step that writes `{self.selector}`"
        which = "last" if self.anchor == "last_of_kind" else "first"
        return f"the {which} step of kind `{self.selector}`"


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
    milestones: list[Milestone] = Field(default_factory=list)

    def milestone(self, milestone_id: str) -> Milestone | None:
        return next((m for m in self.milestones if m.id == milestone_id), None)


# ---------------------------------------------------------------------------
# The fixture: the world a case runs in, named and shared
# ---------------------------------------------------------------------------

FixtureOrigin = Literal["declared", "authored", "promoted", "synthesized"]


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


class Fixture(BaseModel):
    """The world as it must be before a trigger fires — named, and shared.

    This used to be an anonymous literal inside the one case that needed it,
    which made two cases over the same world two copies of it and tied a golden
    set to the file its worlds happened to be written in. Naming the world is
    what lets a case say only *which* one it runs in: reshape the flow, rewrite
    the fixture, and the cases still stand.

    Preferred form is `deliveries`: the same push payloads the real feeds send,
    so the precondition is established the way production established it — and
    the sync flows get exercised on the way in. `objects` is the escape hatch
    for entities no feed provides yet, an Employee being the standing example.
    """

    id: str
    # The flow whose cases run here. Empty for a world that belongs to no one
    # flow — the demo seed being the standing example.
    flow_id: str = ""
    name: str = ""
    summary: str = ""
    deliveries: list[dict] = Field(default_factory=list)
    objects: list[FixtureObject] = Field(default_factory=list)
    # The governed knowledge this world was declared against, at the revisions
    # it was declared at. A claim rather than an enforcement: what a graded run
    # actually read comes back on the result, and the two disagreeing is the
    # signal that a policy moved under a case rather than the case regressing.
    read_set: list[str] = Field(default_factory=list)
    # `declared` is written in code and code owns it; `authored` is built in a
    # console, `promoted` lifted off a real run, `synthesized` generated from
    # another. Only the first is in use — the rest are here because the seeder
    # has to know which fixtures a redeploy may replace and which it must not.
    origin: FixtureOrigin = "declared"
    # Where it came from: the module that declares it, the run it was promoted
    # out of, or the fixture it was generated against. Points a reader at the
    # source rather than leaving them to grep for the id.
    derived_from: str = ""
    authored_by: str = ""
    # When it entered the store, not when the world it describes happened.
    created_at: datetime = Field(default_factory=utcnow)


# The payload a fixture declares, kept off the catalog and off the preview. A
# catalog is a catalog; and the preview answers "what does this world assert"
# by *running* it, so handing back the declaration alongside the consequence
# would answer the same question twice, in the weaker form. Exactly one route
# serves it — the fixture itself — because that is the authoring surface.
FIXTURE_PAYLOAD = {"deliveries", "objects"}


def seed_fixture(
    world: EvalWorld, fixture: Fixture, *, actor: str, provenance: str
) -> None:
    """Establish a fixture's world the way production would have.

    Deliveries go through the real sync flows, so a fixture is not a privileged
    back door: if the sync path would have rejected or escalated a row, whoever
    is looking at this world sees that too.

    Shared with the preview in `worlds.py` deliberately. A preview that
    established the world by a second route could show something no graded run
    ever sees, and a fixture editor you cannot trust is worse than none.
    """
    for delivery in fixture.deliveries:
        world.sync.deliver(SyncRequest.model_validate(delivery), actor=actor)
    writer = world.graph.writer("fixture", "f0")
    for entity in fixture.objects:
        writer.upsert(
            entity.type, entity.key,
            [
                Claim(field=field, value=value, source="fixture")
                for field, value in entity.props.items()
            ],
            label=entity.label or entity.key,
            provenance=provenance,
        )


# ---------------------------------------------------------------------------
# The case: which world, the trigger, and the expected values
# ---------------------------------------------------------------------------


class ExpectedObject(BaseModel):
    """What one entity should look like at a milestone.

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
    # True when this object must *not* exist at this milestone.
    absent: bool = False


class MilestoneExpectation(BaseModel):
    milestone: str
    objects: list[ExpectedObject] = Field(default_factory=list)
    trajectory: dict[str, str] = Field(default_factory=dict)


class ScriptedDecision(BaseModel):
    """A gate answer, applied in order as the run suspends.

    A case that reaches a gate with no decision left simply ends suspended,
    which is a legitimate expected outcome and one the trajectory can assert.
    """

    verdict: Literal["approved", "rejected", "corrected"]
    decided_by: str = "eval@naviant.com"
    note: str = ""
    payload: dict = Field(default_factory=dict)


class EvalCase(BaseModel):
    id: str
    spec_id: str
    name: str
    summary: str = ""
    # The world this case runs in, by name. A case carries expectations and
    # never a world: that is what lets two cases share one, and what lets a
    # fixture be re-authored without a single case being touched.
    fixture_id: str = ""
    trigger: dict = Field(default_factory=dict)
    decisions: list[ScriptedDecision] = Field(default_factory=list)
    expectations: list[MilestoneExpectation] = Field(default_factory=list)
    source: str = "authored"
    added_by: str = ""


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


class AssertionResult(BaseModel):
    milestone: str
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


class GateResult(BaseModel):
    """The promotion gate, stated rather than implied: no regressions, and at
    least as many cases passing as the run this one is measured against.

    `comparable` is false when the baseline measured something else — a spec
    someone edited, or a different set of cases. The gate then reports rather
    than judges: refusing to promote is right, but calling it a regression
    would be naming the wrong problem.
    """

    passed: bool
    comparable: bool = True
    baseline_at_iso: str = ""
    reasons: list[str] = Field(default_factory=list)
    # Cases the baseline passed and this run did not, by id — "3 regressions"
    # is a number, and these are the three things somebody can go and look at.
    regressed: list[str] = Field(default_factory=list)


class EvalResult(BaseModel):
    spec_id: str
    flow_id: str
    spec_version: int
    at_iso: str = Field(default_factory=lambda: utcnow().isoformat())
    launched_by: str = ""
    cases: list[CaseResult] = Field(default_factory=list)
    # What this run measured: the spec's graded content plus the ids of the
    # cases it actually graded. `spec_version` says the same thing when whoever
    # edited the spec remembered to bump it; this says it either way, which is
    # the property the gate needs and the reason it is a hash and not a number.
    measured: str = ""
    # Set when there was a previous result for this spec to measure against.
    # None is the honest answer for a first run: with nothing to compare, a
    # gate would be asserting a judgment it has no evidence for.
    gate: GateResult | None = None

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def failed(self) -> int:
        return len(self.cases) - self.passed

    def summary(self) -> str:
        return f"{self.passed}/{len(self.cases)} case(s) passed"


def measurement(spec: EvalSpec, case_ids: Iterable[str]) -> str:
    """A fingerprint of what a run measured: the rules, and the cases graded.

    Two pass rates are comparable exactly when this matches. A declared
    version says so when whoever edited the spec remembered to bump it; a hash
    over the spec's own content says so whether they did or not — and folding
    the case ids in means a run over three of the cases is not silently
    compared against a run over all of them.
    """
    payload = spec.model_dump_json() + "\n" + "\n".join(sorted(case_ids))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def gate(candidate: EvalResult, baseline: EvalResult | None) -> GateResult | None:
    """Grade a result against the previous one for the same spec.

    A regression is a case the baseline passed and this run did not — named,
    because "3 regressions" is a number and "these three cases" is something
    someone can go and look at.
    """
    if baseline is None:
        return None
    if baseline.measured != candidate.measured:
        return GateResult(
            passed=False,
            comparable=False,
            baseline_at_iso=baseline.at_iso,
            reasons=[
                "the previous run measured something else — the spec or the "
                "set of cases changed, so the two pass rates are answers to "
                "different questions. Re-run the baseline to compare."
            ],
        )
    before = {c.case_id: c.passed for c in baseline.cases}
    regressed = [
        c.case_id for c in candidate.cases if before.get(c.case_id) and not c.passed
    ]
    reasons: list[str] = []
    if regressed:
        reasons.append(
            f"{len(regressed)} regression{'s' if len(regressed) != 1 else ''} "
            f"against the run at {baseline.at_iso}: {', '.join(regressed)}"
        )
    if candidate.passed < baseline.passed:
        reasons.append(
            f"candidate passes {candidate.passed}/{len(candidate.cases)}, "
            f"below the baseline's {baseline.passed}/{len(baseline.cases)}"
        )
    return GateResult(
        passed=not reasons,
        baseline_at_iso=baseline.at_iso,
        reasons=reasons,
        regressed=regressed,
    )


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
    milestone cannot end up judging state as of one point and the path as of
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
# Resolving a milestone against a journal
# ---------------------------------------------------------------------------


def ref_family(raw: str) -> str:
    """A written ref reduced to what it addresses, minus the minted id.

    `graph:Invoice/invoice_1d5ad4a21f28@rev2` becomes `graph:Invoice/`. That is
    the form a `wrote` milestone selects on and the form a failure lists back,
    because a reader comparing "what I asked for" against "what the run wrote"
    cannot do it against forty hashes.

    Parsing rather than string surgery, through the same `parse_ref` the flow
    projection uses: the revision suffix is optional in fact and not just in
    the grammar, and a second parser here would drift from that one.
    """
    ref = parse_ref(raw)
    if not ref.namespace:
        return raw
    if ref.type:
        return f"{ref.namespace}:{ref.type}/"
    # Flat schemes: a `document:` id addresses one artifact and is noise in a
    # list of what a run touched, but a `knowledge:` id *is* the thing named.
    if ref.namespace == "document":
        return f"{ref.namespace}:"
    return f"{ref.namespace}:{ref.id}"


def _listed(values: Iterable[str], limit: int = 120) -> str:
    """Distinct values in the order the run produced them, elided if long."""
    text = ", ".join(dict.fromkeys(v for v in values if v))
    if not text:
        return "nothing"
    return f"{text[:limit]} …" if len(text) > limit else text


def resolve_milestone(
    milestone: Milestone, run: RunRecord
) -> tuple[str, AssertionResult | None]:
    """Where in this run the milestone falls, or the check saying it never did.

    Returns `(step_id, None)` when it resolves — `final` for the whole run,
    which is the sentinel `steps_upto` and `snapshot_as_of` already speak.

    The second element is the *failing check*, not a reason string, and that
    is the whole guarantee: declaring a milestone is itself the claim that the
    run reaches it, so there is no path through the grader on which a
    milestone contributes nothing. A milestone with no expectations at all
    still fails loudly when it is unreachable, and the failure names the
    alternatives the run actually offered rather than saying "not found".
    """

    def unreachable(actual: str, detail: str) -> tuple[str, AssertionResult]:
        return "", AssertionResult(
            milestone=milestone.id, target="milestone", rule="reached",
            passed=False, expected=milestone.describe(), actual=actual,
            detail=detail,
        )

    if milestone.anchor == "terminal":
        if run.status in ("running", "suspended"):
            return unreachable(
                _listed(s.step_id for s in run.steps),
                f"the run never finished — it ended {run.status}"
                + (f" at {run.suspended_at_step}" if run.suspended_at_step else ""),
            )
        return "final", None

    if milestone.anchor == "wrote":
        wrote = [
            s.step_id for s in run.steps
            if any(ref_family(w).startswith(milestone.selector) for w in s.writes)
        ]
        if not wrote:
            return unreachable(
                _listed(ref_family(w) for s in run.steps for w in s.writes),
                f"no step wrote `{milestone.selector}` — the run ended "
                f"{run.status}",
            )
        # The *first* writer. The milestone is the moment the thing came to
        # exist; a later step touching it again has not moved that point.
        return wrote[0], None

    of_kind = [s.step_id for s in run.steps if s.kind == milestone.selector]
    if not of_kind:
        kinds = _listed(s.kind for s in run.steps)
        return unreachable(
            kinds,
            f"this flow ran no step of kind '{milestone.selector}' — it ran: "
            f"{kinds}.",
        )
    return (of_kind[-1] if milestone.anchor == "last_of_kind" else of_kind[0]), None


# ---------------------------------------------------------------------------
# The stores
# ---------------------------------------------------------------------------


class SpecStore(Protocol):
    def specs(self) -> list[EvalSpec]: ...
    def cases(self, spec_id: str) -> list[EvalCase]: ...


class FixtureStore(Protocol):
    """Where declared worlds live. `put` is here for the console that will
    curate them; nothing deletes one yet, because a case may still name it."""

    def list(self) -> list[Fixture]: ...
    def get(self, fixture_id: str) -> Fixture | None: ...
    def put(self, fixture: Fixture) -> Fixture: ...


class InMemoryFixtureStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Fixture] = {}

    def list(self) -> list[Fixture]:
        with self._lock:
            return sorted(self._items.values(), key=lambda f: f.id)

    def get(self, fixture_id: str) -> Fixture | None:
        with self._lock:
            return self._items.get(fixture_id)

    def put(self, fixture: Fixture) -> Fixture:
        with self._lock:
            self._items[fixture.id] = fixture
            return fixture


class FirestoreFixtureStore:
    """A fixture id reads as a path — `ap/acme-po-7741-clean` — and a Firestore
    document id cannot hold a slash, so it is swapped on the way in. The id
    inside the document is the real one and is never reconstructed from the
    document name, so the encoding can change without stranding what is stored.
    """

    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore  # deferred: not needed for local dev

        self._client = firestore.Client(project=project)
        self._col = self._client.collection(collection)

    @staticmethod
    def _doc_id(fixture_id: str) -> str:
        return fixture_id.replace("/", "__")

    def list(self) -> list[Fixture]:
        out: list[Fixture] = []
        for doc in self._col.stream():
            try:
                out.append(Fixture.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping unparseable fixture", extra={"docId": doc.id})
        return sorted(out, key=lambda f: f.id)

    def get(self, fixture_id: str) -> Fixture | None:
        snap = self._col.document(self._doc_id(fixture_id)).get()
        if not snap.exists:
            return None
        try:
            return Fixture.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable fixture", extra={"fixtureId": fixture_id})
            return None

    def put(self, fixture: Fixture) -> Fixture:
        self._col.document(self._doc_id(fixture.id)).set(
            fixture.model_dump(mode="json")
        )
        return fixture


def build_fixture_store(
    backend: str, project: str | None, collection: str
) -> FixtureStore:
    if backend == "firestore":
        return FirestoreFixtureStore(project, collection)
    return InMemoryFixtureStore()


def declared_fixture_store() -> FixtureStore:
    """The fixtures declared in code, in memory. What a standalone test wants."""
    from .eval_specs import seed_fixtures_if_empty

    store = InMemoryFixtureStore()
    seed_fixtures_if_empty(store)
    return store


class EvalService:
    """Runs cases against a spec, in a world nothing else can see."""

    def __init__(
        self,
        store: SpecStore,
        knowledge=None,
        fixtures: FixtureStore | None = None,
    ) -> None:
        self.store = store
        # The corpus to copy into each world. None means "seed from disk",
        # which is what a standalone test wants.
        self.knowledge = knowledge
        # The worlds cases name. Same posture as `knowledge`: None means the
        # ones declared in code, so a test can run the declared cases without
        # standing up a store first.
        self.fixtures = fixtures if fixtures is not None else declared_fixture_store()
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
            measured=measurement(spec, [c.id for c in cases]),
        )
        for case in cases:
            result.cases.append(self.run_case(spec, case))
        # Read the baseline before overwriting it: the gate is the whole point
        # of keeping the last result, and a run that has nothing to compare
        # against says so rather than passing itself.
        result.gate = gate(result, self._results.get(spec.id))
        self._results[spec.id] = result
        log.info(
            "Eval completed",
            extra={
                "specId": spec.id, "flowId": spec.flow_id,
                "cases": len(result.cases), "passed": result.passed,
                "failed": result.failed, "actor": launched_by,
                "gate": None if result.gate is None else result.gate.passed,
            },
        )
        return result

    def run_case(self, spec: EvalSpec, case: EvalCase) -> CaseResult:
        outcome = CaseResult(case_id=case.id, name=case.name)
        try:
            fixture = self.fixture_for(case)
            if fixture is None:
                outcome.error = (
                    f"the case runs in fixture '{case.fixture_id}', which is "
                    "not registered"
                )
                return outcome
            world = build_world(self.knowledge)
            outcome.knowledge_revisions = world.knowledge_revisions
            seed_fixture(
                world, fixture,
                actor="system:eval-fixture", provenance="eval fixture",
            )
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

    def fixture_for(self, case: EvalCase) -> Fixture | None:
        """The world a case names, or None when nobody declared it.

        A case naming nothing is legitimate — the flow's own trigger is the
        whole precondition — and gets an empty world rather than a failure.
        A case naming a fixture that is gone is a broken case, and says so.
        """
        if not case.fixture_id:
            return Fixture(id="", name="an empty world")
        return self.fixtures.get(case.fixture_id)

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
            milestone = spec.milestone(expectation.milestone)
            if milestone is None:
                results.append(AssertionResult(
                    milestone=expectation.milestone, target="spec",
                    passed=False,
                    detail=(
                        f"the case expects milestone "
                        f"'{expectation.milestone}' which the spec does not "
                        "declare"
                    ),
                ))
                continue
            step_id, unreachable = resolve_milestone(milestone, run)
            if unreachable is not None:
                results.append(unreachable)
                continue
            # Recorded even when it resolves. Naming a milestone is a claim
            # that the run reaches it, so the resolution is an assertion in
            # its own right — and `actual` holding the step it landed on is
            # what lets a reader (or a test) see that reshaping the flow did
            # not move the point being judged.
            results.append(AssertionResult(
                milestone=milestone.id, target="milestone", rule="reached",
                passed=True, expected=milestone.describe(), actual=step_id,
            ))
            prefix, _reached = steps_upto(run, step_id)
            snapshot = snapshot_as_of(world.graph, run, step_id)
            for expected in expectation.objects:
                results.extend(
                    self._grade_object(spec, milestone, snapshot, expected, run)
                )
            results.extend(
                self._grade_trajectory(milestone, expectation, run, world, prefix)
            )
        return results

    def _grade_object(
        self,
        spec: EvalSpec,
        milestone: Milestone,
        snapshot: GraphSnapshot,
        expected: ExpectedObject,
        run: RunRecord,
    ) -> list[AssertionResult]:
        target = f"{expected.type}[{expected.key or 'the only one'}]"
        record, addressing = self._resolve_expected(snapshot, expected, run)
        if addressing:
            return [AssertionResult(
                milestone=milestone.id, target=target, rule="addressing",
                passed=False, detail=addressing,
            )]
        if expected.absent:
            return [AssertionResult(
                milestone=milestone.id, target=target, rule="absent",
                passed=record is None,
                actual="present" if record is not None else "absent",
                detail=(
                    "must not exist at this milestone"
                    if record is not None else "correctly absent"
                ),
            )]
        if record is None:
            return [AssertionResult(
                milestone=milestone.id, target=target, rule="exists",
                passed=False, expected="exists", actual="missing",
                detail=(
                    f"no {expected.type} with natural key '{expected.key}' "
                    f"as of {snapshot.after_step}"
                ),
            )]

        object_rule = milestone.rule_for(expected.type) or ObjectRule(
            type=expected.type
        )
        results: list[AssertionResult] = [AssertionResult(
            milestone=milestone.id, target=target, rule="exists",
            passed=True, expected="exists", actual=record.ref(),
        )]

        for field, value in expected.fields.items():
            if field in spec.ignore_fields:
                continue
            rule = object_rule.rule_for(field) or FieldRule(field=field)
            passed, why = compare(rule, value, record.props.get(field))
            results.append(AssertionResult(
                milestone=milestone.id, target=target, field=field,
                rule=rule.match, expected=f"{value}",
                actual=f"{record.props.get(field)}", passed=passed, detail=why,
            ))

        if object_rule.unlisted_fields == "require":
            named = {r.field for r in object_rule.fields} | set(spec.ignore_fields)
            drifted = sorted(set(record.props) - named)
            if drifted:
                results.append(AssertionResult(
                    milestone=milestone.id, target=target, rule="no-drift",
                    passed=False, actual=", ".join(drifted),
                    detail=(
                        "the flow claimed fields this spec does not name — "
                        "either grade them or say they are ignored"
                    ),
                ))

        results.extend(
            self._grade_links(milestone, object_rule, snapshot, record, expected, target)
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
        milestone: Milestone,
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
                    milestone=milestone.id, target=label, rule="absent",
                    passed=not edges, expected="0", actual=f"{len(edges)}",
                    detail="no such edge" if not edges else "edge present",
                ))
                continue
            if link_rule.expect == "count":
                wanted = expected.link_counts.get(link_rule.type)
                if wanted is None:
                    continue
                results.append(AssertionResult(
                    milestone=milestone.id, target=label, rule="count",
                    passed=len(edges) == wanted, expected=f"{wanted}",
                    actual=f"{len(edges)}",
                    detail=f"{len(edges)} edge(s) of {link_rule.type}",
                ))
                continue
            results.append(AssertionResult(
                milestone=milestone.id, target=label, rule="exists",
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
                    milestone=milestone.id, target=label, field=prop_rule.field,
                    rule=prop_rule.match, expected=f"{expected.link_props[key]}",
                    actual=f"{actual}", passed=passed, detail=why,
                ))
        return results

    def _grade_trajectory(
        self,
        milestone: Milestone,
        expectation: MilestoneExpectation,
        run: RunRecord,
        world: EvalWorld,
        prefix: list[str],
    ) -> list[AssertionResult]:
        results: list[AssertionResult] = []
        # Scoped to the milestone, so "no second model call before the
        # vendor is settled" means what it says.
        in_scope = [s for s in run.steps if s.step_id in set(prefix)]
        step_ids = [s.step_id for s in in_scope]
        gates = [s for s in in_scope if s.kind == "task"]
        for rule in milestone.trajectory:
            expected = expectation.trajectory.get(rule.kind, rule.expected)
            if not expected and rule.kind not in ("step_absent",):
                continue
            passed, actual = self._check_trajectory(
                rule.kind, expected, run, step_ids, gates, world, in_scope
            )
            results.append(AssertionResult(
                milestone=milestone.id, target="trajectory", field=rule.kind,
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
