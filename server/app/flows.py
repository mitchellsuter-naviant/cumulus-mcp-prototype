"""Flows: the registry and observability plane for authored logic.

Doctrine grounding (DOCTRINE.md, Lens 2 and the Governance spine):

  * There is deliberately **no "Agent" primitive** — the Flow *is* the agent.
  * There is one kind of authored logic: a **Flow**. The doctrine's original
    Workflow/DataFlow split has been retired — it was right about the physics
    (per-case work and windowed bulk work behave differently) and wrong about
    the level, putting a behavioural difference in the type system where it
    produced arguments about which noun a definition was. What actually
    differs is derived from a definition's own shape: how it is triggered and
    where it writes. See `is_windowed` and `writes_canonical`.
  * A **ModelCall** is a governed operation, not a primitive: every LLM
    invocation records which model ran, its tokens, and its cost. All spend
    numbers here roll up from ModelCall records — cost is an audit product,
    never a separate meter.
  * The definition shown to the customer is the **Tier-2 declarative export**:
    typed steps with plain-language summaries. Transparent, never opaque; it
    need not be runnable (the transparency bar, Lens 1).
  * The **golden set** is a knowledge-role Dataset seeded from the correction
    set — the flywheel's crown-jewel eval data. Test runs execute against it
    with writes suppressed: no Action reaches the gateway from a test.

Prototype honesty: this repo has no execution engine yet, so run telemetry
(runs, journals, spend, audit entries) is **synthesized deterministically**
from each definition's seed profile — stable across restarts and identical on
every backend, so the console and the API contract are real even though the
runs are not. Definitions, golden sets, and test runs are real stored state.
"""

from __future__ import annotations

import hashlib
import logging
import random
import threading
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from .models import utcnow

log = logging.getLogger("cumulus.flows")

FlowStatus = Literal["draft", "active", "paused", "deprecated"]
# The step vocabulary of the declarative export. `transform` is authored
# non-model logic (parse, reshape, entity-match) carried as a faithful
# plain-language description — implementation stays machinery.
StepType = Literal["trigger", "modelcall", "rule", "task", "action", "event", "transform"]
GoldenSource = Literal[
    "correction-set", "exemplar", "human-authored", "promoted-from-run"
]
# How a fact assertion is graded. `judge` is itself a governed ModelCall.
FactOp = Literal["equals", "approx", "one_of", "judge"]
# What kind of step establishes a contract fact — the derivation key the
# producer map uses, so labels never name a step id.
FactKind = Literal["extraction", "verdict", "commit", "synthesis"]
AssertionVerdict = Literal["pass", "fail", "review"]
Grader = Literal["deterministic", "judge", "human"]
# Pinned: the case's read-set is served back (reproducible regression run).
# Live: the flow reads today's knowledge layer (a different kind of question).
ReadMode = Literal["pinned", "live"]
RunStatus = Literal["completed", "waiting_on_human", "escalated", "failed"]
VersionStatus = Literal["active", "archived", "draft"]
# What changed about a step between two versions, for the canvas diff overlay.
ChangeKind = Literal["unchanged", "added", "removed", "modified"]

_REVIEWERS = ["j.alvarez", "p.okafor", "m.chen", "s.brandt", "d.silva"]


def _h(seed: str) -> random.Random:
    """A deterministic RNG keyed by a string — restart- and backend-stable."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _hex(seed: str, n: int) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:n]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class FlowStep(BaseModel):
    """One typed step of the declarative export.

    `summary` is the transparency bar in miniature: a plain-language statement
    of what the step does, present even when the implementation is reserved.
    `config` carries the structured-where-cheap half (model ids, thresholds,
    queues) as display strings.
    """

    id: str
    type: StepType
    name: str
    summary: str = ""
    config: dict[str, str] = Field(default_factory=dict)
    # Ids of the steps this one can hand off to. The first entry is the main
    # path; extras are branches, labelled via `branch_labels` in config-free
    # form: parallel list, same order as `next`.
    next: list[str] = Field(default_factory=list)
    branch_labels: list[str] = Field(default_factory=list)


class SeedProfile(BaseModel):
    """Shape parameters for the synthesized telemetry. Not exposed over REST."""

    base_runs: float = 20.0
    cost_per_run: float = 0.20
    tokens_per_run: int = 4200
    task_rate: float = 0.07
    escalate_rate: float = 0.004
    fail_rate: float = 0.004
    weekend_factor: float = 0.35
    growth_per_day: float = 0.004
    active_seconds: float = 40.0
    case_prefix: str = "case"


class FlowVersion(BaseModel):
    """One published or drafted revision of a definition.

    A version is a full snapshot of the declarative export, not a delta: the
    map has to stay readable on its own, and a chain of deltas is exactly the
    kind of opaque artifact the transparency bar rules out. Snapshots are
    small (a handful of typed steps), so the cost is nothing.
    """

    version: int
    label: str = ""
    status: VersionStatus = "archived"
    steps: list[FlowStep] = Field(default_factory=list)
    model_budget_usd_per_run: float = 0.50
    notes: str = ""
    authored_by: str = ""
    created_at: datetime = Field(default_factory=utcnow)

    def display(self) -> str:
        return self.label or f"v{self.version}"


class StepDiff(BaseModel):
    """One step of a version, annotated against the version compared with."""

    step: FlowStep
    change: ChangeKind = "unchanged"
    changed_fields: list[str] = Field(default_factory=list)


class FlowDefinition(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]*$")
    name: str = ""
    description: str = ""
    team: str = ""
    status: FlowStatus = "draft"
    version: int = 1
    tags: list[str] = Field(default_factory=list)
    steps: list[FlowStep] = Field(default_factory=list)
    # Where human judgment lands, and where the work is watched from.
    queues: list[str] = Field(default_factory=list)
    workspace: str = ""
    # Governed knowledge, not code: editable in the shell, versioned, ledgered.
    roi_minutes_per_run: float = 0.0
    roi_hourly_rate_usd: float = 30.0
    model_budget_usd_per_run: float = 0.50
    authored_by: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    profile: SeedProfile = Field(default_factory=SeedProfile)
    # Published history plus at most one open draft. `steps` above stays the
    # active version's steps, so every existing reader keeps working.
    versions: list[FlowVersion] = Field(default_factory=list)
    # The declared canonical facts this flow establishes — the stable spine
    # ground truth attaches to (defined below; resolved via model_rebuild).
    contract: "FlowContract" = Field(default_factory=lambda: FlowContract())

    def version_history(self) -> list[FlowVersion]:
        """Newest first, with the active version synthesized if absent."""
        if self.versions:
            return sorted(self.versions, key=lambda v: v.version, reverse=True)
        return [
            FlowVersion(
                version=self.version,
                label=f"v{self.version}",
                status="active",
                steps=self.steps,
                model_budget_usd_per_run=self.model_budget_usd_per_run,
                authored_by=self.authored_by,
                created_at=self.updated_at,
            )
        ]

    def find_version(self, version: int) -> FlowVersion | None:
        return next(
            (v for v in self.version_history() if v.version == version), None
        )

    def active_version(self) -> FlowVersion:
        history = self.version_history()
        return next(
            (v for v in history if v.status == "active"),
            next((v for v in history if v.status != "draft"), history[0]),
        )

    def draft_version(self) -> FlowVersion | None:
        return next(
            (v for v in self.version_history() if v.status == "draft"), None
        )


# ---- the flow contract: the stable spine labels attach to -----------------


class ContractFact(BaseModel):
    """One canonical fact a Flow establishes, expressed against the ontology.

    Ground truth keys to these paths, never to step ids — a document's
    line_items[1].amount is true regardless of which ModelCall happens to
    capture it, so relabelling on every topology change would be labelling
    the wrong thing. `kind` + `stage` are the derivation key the producer map
    uses to project a fact onto whatever steps the current version has.
    """

    path: str
    type: str = "string"  # money | string | ref | verdict | bool | int
    kind: FactKind = "extraction"
    # Which step of that kind produces it, in declaration order (0-based).
    stage: int = 0
    description: str = ""


class FlowContract(BaseModel):
    facts: list[ContractFact] = Field(default_factory=list)


def producer_map(contract: FlowContract, steps: list[FlowStep]) -> dict[str, str]:
    """{fact path -> step id} for one concrete version's steps.

    Derived from the version's topology (in the real engine, from journal
    lineage), never hand-maintained — which is what lets the flow's shape
    morph without orphaning a single label. A fact no step can produce maps
    to "", which grading surfaces as a structural gap rather than hiding.
    """
    kind_to_type = {
        "extraction": "modelcall",
        "synthesis": "modelcall",
        "verdict": "rule",
        "commit": "action",
    }
    by_type: dict[str, list[FlowStep]] = {}
    for step in steps:
        by_type.setdefault(step.type, []).append(step)
    out: dict[str, str] = {}
    for fact in contract.facts:
        candidates = by_type.get(kind_to_type[fact.kind], [])
        if not candidates:
            out[fact.path] = ""
        elif fact.kind == "synthesis":
            out[fact.path] = candidates[-1].id
        else:
            out[fact.path] = candidates[min(fact.stage, len(candidates) - 1)].id
    return out


FlowDefinition.model_rebuild()


# ---- golden cases: document + pinned world + assertions --------------------


class FactAssertion(BaseModel):
    """A claim about a canonical fact for this document — 'for exp-20441,
    receipt.total_usd is 64.20' — graded field-by-field."""

    path: str
    op: FactOp = "equals"
    expected: str = ""
    tolerance: str = ""  # for approx
    rubric: str = ""  # for judge — the rubric the judge ModelCall grades against


class TrajectoryAssertion(BaseModel):
    """A claim about the path, phrased semantically rather than by step id:
    which Actions commit, whether human judgment was needed, how it ends."""

    kind: Literal["action_committed", "task_raised", "terminal_status", "waypoint"]
    expected: str = ""


class PinnedRead(BaseModel):
    """One knowledge/data-layer record the run read, at its revision — captured
    from the run journal on promotion, or authored as a fixture. Serving these
    back is what makes a regression run reproducible when the vendor master
    moves underneath it."""

    ref: str  # e.g. "vendor:acme-industrial@rev12"
    summary: str = ""


class GoldenCase(BaseModel):
    id: str
    name: str
    input_ref: str = ""
    # Human-readable summary of what a passing run looks like; the structured
    # truth is `assertions` + `trajectory`.
    expected: str = ""
    source: GoldenSource = "correction-set"
    added_by: str = ""
    added_at: datetime = Field(default_factory=utcnow)
    # Set when the case was promoted out of a real run, so a failing eval can
    # be traced back to the execution that motivated it.
    run_id: str = ""
    # Free-form JSON payload when the case was added as one rather than as a
    # document reference. Stored as text: it is the customer's fixture, and
    # re-serializing it would quietly change what is being tested.
    payload: str = ""
    # The fixture artifact itself (an invoice PDF, an email) as a
    # knowledge-layer asset reference.
    document_ref: str = ""
    assertions: list[FactAssertion] = Field(default_factory=list)
    trajectory: list[TrajectoryAssertion] = Field(default_factory=list)
    read_set: list[PinnedRead] = Field(default_factory=list)
    # Set when a human confirmed/corrected the labels in the review screen.
    reviewed_by: str = ""
    reviewed_at: datetime | None = None


class GoldenSetRevision(BaseModel):
    version: int
    at: datetime
    by: str = ""
    note: str = ""
    case_count: int = 0


class GoldenSet(BaseModel):
    flow_id: str
    cases: list[GoldenCase] = Field(default_factory=list)
    # The dataset is a versioned knowledge asset: experiments pin the version
    # they ran against, so two pass rates are only ever compared when they
    # measured the same thing.
    version: int = 1
    revisions: list[GoldenSetRevision] = Field(default_factory=list)
    # Where the seeded half of the set comes from in the repository. The
    # golden set is governed knowledge that FDEs also author in code, so the
    # console states which file it was seeded from rather than pretending the
    # UI is the only source.
    synced_from: str = ""
    synced_at: datetime | None = None

    def bump(self, by: str, note: str) -> None:
        self.version += 1
        self.revisions.append(
            GoldenSetRevision(
                version=self.version,
                at=utcnow(),
                by=by,
                note=note,
                case_count=len(self.cases),
            )
        )


# ---- correction sets: the flywheel's eval data, per ModelCall ---------------


class CorrectionPair(BaseModel):
    field: str
    raw: str
    corrected: str
    by: str = ""
    at: datetime = Field(default_factory=utcnow)


class CorrectionSet(BaseModel):
    """The auto-accumulated raw→corrected pairs for one ModelCall step — the
    crown-jewel eval/tuning dataset the flywheel appends to on every human
    correction. Step evals grade a prompt/model change against this without
    running the whole flow."""

    step_id: str
    step_name: str
    model: str = ""
    pairs: int = 0
    last_appended: datetime | None = None
    sample: list[CorrectionPair] = Field(default_factory=list)


# ---- the case document, rendered for review/labelling -----------------------


class DocLine(BaseModel):
    """One text run on the synthesized document page. Coordinates are
    normalized 0..1 against a US-letter page."""

    text: str
    x: float
    y: float
    size: float = 12
    bold: bool = False
    mono: bool = False
    muted: bool = False


class DocRegion(BaseModel):
    """A grounded field: where on the page a contract fact's value sits.
    box = [x, y, w, h], normalized. In the real system these come off the
    extraction ModelCall's grounding output."""

    path: str
    box: list[float]
    value: str
    confidence: float = 1.0


class CaseDocument(BaseModel):
    case_id: str
    title: str
    kind: str = "generic"  # expense | generic
    lines: list[DocLine] = Field(default_factory=list)
    regions: list[DocRegion] = Field(default_factory=list)


class AssertionResult(BaseModel):
    """One assertion, graded for one (case, version) pair and localized to the
    step that owns the fact in this version's producer map."""

    kind: Literal["fact", "trajectory"]
    label: str  # the fact path, or the trajectory claim in words
    op: str = "equals"
    expected: str = ""
    actual: str = ""
    verdict: AssertionVerdict = "pass"
    grader: Grader = "deterministic"
    # "" when the claim has no single owning step (a trajectory terminal), or
    # when no step in this version can produce the fact — a structural gap.
    step_id: str = ""
    baseline_verdict: AssertionVerdict | None = None


class TestCaseResult(BaseModel):
    case_id: str
    name: str
    passed: bool
    expected: str = ""
    actual: str = ""
    cost_usd: float = 0.0
    duration_ms: int = 0
    # The same case run against the baseline version, so the comparison is
    # like-for-like rather than candidate-versus-remembered-numbers.
    baseline_passed: bool | None = None
    baseline_cost_usd: float = 0.0
    baseline_duration_ms: int = 0
    # Assertion-level grading: which facts diverged and at which step, so a
    # failure points at the thing to change rather than just counting.
    assertions: list[AssertionResult] = Field(default_factory=list)

    @property
    def regressed(self) -> bool:
        return self.baseline_passed is True and not self.passed


class StepStat(BaseModel):
    """Pass/fail of the assertions one step owns, across the whole run — the
    number the canvas heatmap paints on that node."""

    step_id: str
    step_name: str = ""
    step_type: str = ""
    passed: int = 0
    failed: int = 0
    baseline_passed: int = 0
    baseline_failed: int = 0


class GateResult(BaseModel):
    """The promotion gate, stated rather than implied: no regressions, and at
    least as many cases passing as the baseline."""

    passed: bool
    reasons: list[str] = Field(default_factory=list)


class TestRun(BaseModel):
    id: str
    flow_id: str
    candidate_version: str
    baseline_version: str
    status: Literal["running", "completed"] = "running"
    launched_by: str = ""
    launched_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    total_cases: int = 0
    results: list[TestCaseResult] = Field(default_factory=list)
    passed: int = 0
    cost_usd: float = 0.0
    baseline_passed: int = 0
    baseline_cost_usd: float = 0.0
    # Candidate minus baseline. Negative cost is a saving; negative latency is
    # faster. Kept as stored fields so a finished run reads the same forever.
    cost_delta_usd: float = 0.0
    latency_delta_ms: int = 0
    regressions: int = 0
    # Doctrinal constant: the harness never commits an Action. Kept on the
    # record so the audit trail states it rather than implying it.
    writes_suppressed: bool = True
    # Which revision of the golden set this experiment measured — pass rates
    # are only comparable when this matches.
    dataset_version: int = 0
    # Pinned read-sets by default; a live-reads run is a different question
    # and is labelled as one.
    reads: ReadMode = "pinned"
    # Per-step aggregates over assertion results — the canvas eval lens.
    step_stats: list[StepStat] = Field(default_factory=list)
    gate: GateResult | None = None
    # Assertions the grader could not decide; each raises a review Task.
    reviews_raised: int = 0
    # Set when the experiment was auto-launched by applying a recommendation,
    # so the card can show its measured (not estimated) delta.
    launched_for: str = ""


class Recommendation(BaseModel):
    """One cost or performance insight derived from execution telemetry.

    Every card carries its evidence and the exact change it proposes, because
    a recommendation you cannot audit is not usable for governance work — and
    applying one edits governed knowledge, which is a ledgered act.
    """

    id: str
    kind: Literal["model-downgrade", "prompt-cache", "budget", "coverage", "batching"]
    title: str
    detail: str
    evidence: str = ""
    step_id: str = ""
    step_name: str = ""
    estimated_monthly_savings_usd: float = 0.0
    confidence: float = 0.0
    # None when the insight is advisory (e.g. "add golden cases") rather than
    # a mechanical edit the console can stage into a draft.
    proposed_change: dict[str, str] | None = None


# ---- synthesized telemetry shapes (never stored) --------------------------


class UsageDay(BaseModel):
    date: date
    runs: int = 0
    completed: int = 0
    tasks_raised: int = 0
    escalated: int = 0
    failed: int = 0
    cost_usd: float = 0.0
    tokens: int = 0
    model_calls: int = 0
    minutes_saved: float = 0.0


class ModelCallRecord(BaseModel):
    step_id: str
    step_name: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_ms: int
    # The governance spine requires a ModelCall to record its inputs and its
    # captured output, not merely that it happened. These are previews — the
    # full payloads live with the run journal — but they are what makes an
    # answer traceable rather than asserted.
    prompt_preview: str = ""
    raw_response: str = ""
    prompt_version: str = ""
    temperature: float = 0.0


class RunStepEvent(BaseModel):
    step_id: str
    type: StepType
    name: str
    status: Literal["completed", "waiting", "failed", "skipped"]
    at: datetime
    duration_ms: int = 0
    detail: str = ""
    # Populated per type: a rule's verdict, a task's queue/decider, an
    # action's ledger reference.
    verdict: str = ""
    queue: str = ""
    decided_by: str = ""
    suspended_minutes: int = 0
    ledger_ref: str = ""
    # What went into this step and what came out, for the run drawer.
    input_preview: str = ""
    output_preview: str = ""


class RunSummary(BaseModel):
    id: str
    flow_id: str
    status: RunStatus
    case_ref: str
    started_at: datetime
    active_seconds: float
    suspended_minutes: int
    cost_usd: float
    model_calls: int
    tasks_raised: int
    actions_committed: int


class RunDetail(RunSummary):
    trigger_event: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    steps: list[RunStepEvent] = Field(default_factory=list)
    calls: list[ModelCallRecord] = Field(default_factory=list)


class AuditEntry(BaseModel):
    at: datetime
    kind: Literal["decision", "action", "resolution", "knowledge"]
    actor: str
    summary: str
    run_id: str = ""
    ledger_ref: str = ""


# ---------------------------------------------------------------------------
# Directories (stored state: definitions, golden sets, test runs)
# ---------------------------------------------------------------------------


class FlowExistsError(Exception):
    pass


class FlowNotFoundError(Exception):
    pass


class FlowDirectory(Protocol):
    def list(self) -> list[FlowDefinition]: ...
    def get(self, flow_id: str) -> FlowDefinition | None: ...
    def create(self, defn: FlowDefinition) -> FlowDefinition: ...
    def update(self, defn: FlowDefinition) -> FlowDefinition: ...
    def delete(self, flow_id: str) -> None: ...


class GoldenSetDirectory(Protocol):
    def get(self, flow_id: str) -> GoldenSet | None: ...
    def put(self, golden: GoldenSet) -> GoldenSet: ...
    def delete(self, flow_id: str) -> None: ...


class TestRunDirectory(Protocol):
    def list_for(self, flow_id: str) -> list[TestRun]: ...
    def get(self, test_id: str) -> TestRun | None: ...
    def put(self, run: TestRun) -> TestRun: ...


class InMemoryFlowDirectory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, FlowDefinition] = {}

    def list(self) -> list[FlowDefinition]:
        with self._lock:
            return sorted(self._items.values(), key=lambda w: w.id)

    def get(self, flow_id: str) -> FlowDefinition | None:
        with self._lock:
            return self._items.get(flow_id)

    def create(self, defn: FlowDefinition) -> FlowDefinition:
        with self._lock:
            if defn.id in self._items:
                raise FlowExistsError(defn.id)
            self._items[defn.id] = defn
            return defn

    def update(self, defn: FlowDefinition) -> FlowDefinition:
        with self._lock:
            if defn.id not in self._items:
                raise FlowNotFoundError(defn.id)
            self._items[defn.id] = defn
            return defn

    def delete(self, flow_id: str) -> None:
        with self._lock:
            if flow_id not in self._items:
                raise FlowNotFoundError(flow_id)
            del self._items[flow_id]


class InMemoryGoldenSetDirectory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, GoldenSet] = {}

    def get(self, flow_id: str) -> GoldenSet | None:
        with self._lock:
            return self._items.get(flow_id)

    def put(self, golden: GoldenSet) -> GoldenSet:
        with self._lock:
            self._items[golden.flow_id] = golden
            return golden

    def delete(self, flow_id: str) -> None:
        with self._lock:
            self._items.pop(flow_id, None)


class InMemoryTestRunDirectory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, TestRun] = {}

    def list_for(self, flow_id: str) -> list[TestRun]:
        with self._lock:
            runs = [r for r in self._items.values() if r.flow_id == flow_id]
        return sorted(runs, key=lambda r: r.launched_at, reverse=True)

    def get(self, test_id: str) -> TestRun | None:
        with self._lock:
            return self._items.get(test_id)

    def put(self, run: TestRun) -> TestRun:
        with self._lock:
            self._items[run.id] = run
            return run


class FirestoreFlowDirectory:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore  # deferred: not needed for local dev

        self._client = firestore.Client(project=project)
        self._col = self._client.collection(collection)

    def list(self) -> list[FlowDefinition]:
        out = []
        for doc in self._col.stream():
            try:
                out.append(FlowDefinition.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping unparseable flow", extra={"docId": doc.id})
        return sorted(out, key=lambda w: w.id)

    def get(self, flow_id: str) -> FlowDefinition | None:
        snap = self._col.document(flow_id).get()
        if not snap.exists:
            return None
        try:
            return FlowDefinition.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable flow", extra={"flowId": flow_id})
            return None

    def create(self, defn: FlowDefinition) -> FlowDefinition:
        ref = self._col.document(defn.id)
        if ref.get().exists:
            raise FlowExistsError(defn.id)
        ref.set(defn.model_dump(mode="json"))
        return defn

    def update(self, defn: FlowDefinition) -> FlowDefinition:
        ref = self._col.document(defn.id)
        if not ref.get().exists:
            raise FlowNotFoundError(defn.id)
        ref.set(defn.model_dump(mode="json"))
        return defn

    def delete(self, flow_id: str) -> None:
        ref = self._col.document(flow_id)
        if not ref.get().exists:
            raise FlowNotFoundError(flow_id)
        ref.delete()


class FirestoreGoldenSetDirectory:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore

        self._client = firestore.Client(project=project)
        self._col = self._client.collection(collection)

    def get(self, flow_id: str) -> GoldenSet | None:
        snap = self._col.document(flow_id).get()
        if not snap.exists:
            return None
        try:
            return GoldenSet.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable golden set", extra={"flowId": flow_id})
            return None

    def put(self, golden: GoldenSet) -> GoldenSet:
        self._col.document(golden.flow_id).set(golden.model_dump(mode="json"))
        return golden

    def delete(self, flow_id: str) -> None:
        self._col.document(flow_id).delete()


class FirestoreTestRunDirectory:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore

        self._client = firestore.Client(project=project)
        self._col = self._client.collection(collection)

    def list_for(self, flow_id: str) -> list[TestRun]:
        out = []
        for doc in self._col.where("flow_id", "==", flow_id).stream():
            try:
                out.append(TestRun.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping unparseable test run", extra={"docId": doc.id})
        return sorted(out, key=lambda r: r.launched_at, reverse=True)

    def get(self, test_id: str) -> TestRun | None:
        snap = self._col.document(test_id).get()
        if not snap.exists:
            return None
        try:
            return TestRun.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable test run", extra={"testId": test_id})
            return None

    def put(self, run: TestRun) -> TestRun:
        self._col.document(run.id).set(run.model_dump(mode="json"))
        return run


# ---------------------------------------------------------------------------
# Telemetry synthesis
# ---------------------------------------------------------------------------


def is_windowed(defn: "FlowDefinition") -> bool:
    """True when the flow is woken by a window rather than by a single case.

    This is the mechanical replacement for asking "is it a DataFlow?": a flow
    started by a timer is processing a batch, whatever anyone named it, and a
    flow started by a business event is processing one case.
    """
    trigger = next((s for s in defn.steps if s.type == "trigger"), None)
    if trigger is None:
        return False
    return trigger.config.get("event", "").startswith("timer:")


def writes_canonical(defn: "FlowDefinition") -> bool:
    """True when the flow commits into the canonical graph.

    Where competing claims meet, a Resolution picks a winner and records why —
    so this, not a type, is what says a flow's audit trail will contain them.
    """
    return any(
        s.type == "action" and s.config.get("target") == "graph"
        for s in defn.steps
    )


def _day_usage(defn: FlowDefinition, day: date) -> UsageDay:
    """Deterministic usage for one flow on one day.

    Keyed by (flow id, day) so every restart, instance, and backend agrees
    on history. Drafts and days before creation produce zeroes.
    """
    created = defn.created_at.date()
    if day < created:
        return UsageDay(date=day)
    rng = _h(f"{defn.id}:{day.isoformat()}")
    p = defn.profile
    age = (day - created).days
    base = p.base_runs * (1.0 + p.growth_per_day * age)
    if day.weekday() >= 5:
        base *= p.weekend_factor
    if defn.status == "draft":
        base = min(base, 1.2)
    elif defn.status in ("paused", "deprecated"):
        base = 0.0
    runs = max(0, int(round(base * rng.uniform(0.78, 1.22))))
    if runs == 0:
        return UsageDay(date=day)

    def count(rate: float, low: float = 0.6, high: float = 1.5) -> int:
        # Probabilistic rounding: a 0.3-per-day expectation must show up some
        # days rather than rounding to zero forever.
        expected = runs * rate * rng.uniform(low, high)
        whole = int(expected)
        return whole + (1 if rng.random() < (expected - whole) else 0)

    tasks = min(runs, count(p.task_rate))
    escalated = min(runs, count(p.escalate_rate, 0.0, 2.0))
    failed = min(runs - escalated, count(p.fail_rate, 0.0, 2.0))
    completed = runs - escalated - failed
    cost = runs * p.cost_per_run * rng.uniform(0.85, 1.15)
    tokens = int(runs * p.tokens_per_run * rng.uniform(0.85, 1.15))
    calls_per_run = max(1, sum(1 for s in defn.steps if s.type == "modelcall"))
    return UsageDay(
        date=day,
        runs=runs,
        completed=completed,
        tasks_raised=tasks,
        escalated=escalated,
        failed=failed,
        cost_usd=round(cost, 2),
        tokens=tokens,
        model_calls=runs * calls_per_run,
        minutes_saved=round(completed * defn.roi_minutes_per_run, 1),
    )


def usage_series(defn: FlowDefinition, days: int) -> list[UsageDay]:
    today = utcnow().date()
    return [_day_usage(defn, today - timedelta(days=d)) for d in range(days - 1, -1, -1)]


def open_tasks(defn: FlowDefinition) -> int:
    """Tasks currently sitting in this definition's queues, deterministic per day."""
    if defn.status != "active":
        return 0
    rng = _h(f"{defn.id}:open:{utcnow().date().isoformat()}")
    backlog = defn.profile.base_runs * defn.profile.task_rate * rng.uniform(0.3, 1.1)
    return int(round(backlog))


def _run_id(defn: FlowDefinition, day: date, idx: int) -> str:
    return "run_" + _hex(f"{defn.id}:{day.isoformat()}:{idx}", 6)


def _run_summary(defn: FlowDefinition, day: date, idx: int, runs_that_day: int) -> RunSummary:
    rid = _run_id(defn, day, idx)
    rng = _h(f"{rid}:shape")
    p = defn.profile
    raised = rng.random() < p.task_rate
    escalated = not raised and rng.random() < p.escalate_rate
    now = utcnow()
    # Spread the day's runs across working hours, newest last.
    frac = (idx + 0.5) / max(1, runs_that_day)
    started = datetime.combine(day, dtime(7, 0), tzinfo=timezone.utc) + timedelta(
        seconds=frac * 12 * 3600
    )
    if started > now:
        started = now - timedelta(minutes=rng.uniform(2, 30))
    suspended = int(rng.uniform(4, 90)) if raised else 0
    # A recent raised run may still be waiting on its human.
    waiting = raised and (now - started) < timedelta(minutes=suspended)
    status: RunStatus = (
        "waiting_on_human" if waiting else "escalated" if escalated else "completed"
    )
    calls = max(1, sum(1 for s in defn.steps if s.type == "modelcall"))
    actions = sum(1 for s in defn.steps if s.type == "action")
    return RunSummary(
        id=rid,
        flow_id=defn.id,
        status=status,
        case_ref=f"{p.case_prefix}-{20000 + int(_hex(rid, 4), 16) % 9000}",
        started_at=started,
        active_seconds=round(p.active_seconds * rng.uniform(0.6, 1.6), 1),
        suspended_minutes=suspended,
        cost_usd=round(p.cost_per_run * rng.uniform(0.7, 1.4), 2),
        model_calls=calls,
        tasks_raised=1 if raised else 0,
        actions_committed=0 if (waiting or escalated) else actions,
    )


def recent_runs(defn: FlowDefinition, limit: int) -> list[RunSummary]:
    out: list[RunSummary] = []
    today = utcnow().date()
    for back in range(0, 45):
        day = today - timedelta(days=back)
        usage = _day_usage(defn, day)
        day_runs = [_run_summary(defn, day, i, usage.runs) for i in range(usage.runs)]
        day_runs.sort(key=lambda r: r.started_at, reverse=True)
        out.extend(day_runs)
        if len(out) >= limit:
            break
    return out[:limit]


def find_run(defn: FlowDefinition, run_id: str) -> RunDetail | None:
    today = utcnow().date()
    for back in range(0, 45):
        day = today - timedelta(days=back)
        usage = _day_usage(defn, day)
        for i in range(usage.runs):
            if _run_id(defn, day, i) == run_id:
                return _run_detail(defn, _run_summary(defn, day, i, usage.runs))
    return None


_MODEL_RATES = {
    # $/M input, $/M output — display-plausible, not a price sheet.
    "claude-fable-5": (5.0, 25.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def _amount_for(case_ref: str) -> int:
    """A stable pretend amount for a case, used across payload previews."""
    return 500 + int(_hex(f"{case_ref}:amount", 5), 16) % 24000


def _run_detail(defn: FlowDefinition, summary: RunSummary) -> RunDetail:
    rng = _h(f"{summary.id}:detail")
    steps: list[RunStepEvent] = []
    calls: list[ModelCallRecord] = []
    at = summary.started_at
    tokens_in = tokens_out = 0
    modelcall_steps = [s for s in defn.steps if s.type == "modelcall"] or [None]
    cost_share = summary.cost_usd / len([s for s in modelcall_steps if s])
    raised = summary.tasks_raised > 0
    stopped = False
    amount = _amount_for(summary.case_ref)
    case = summary.case_ref

    for step in defn.steps:
        if stopped:
            steps.append(
                RunStepEvent(
                    step_id=step.id, type=step.type, name=step.name,
                    status="skipped", at=at, detail="not reached",
                )
            )
            continue
        dur = int(rng.uniform(5, 60))
        if step.type == "trigger":
            steps.append(
                RunStepEvent(
                    step_id=step.id, type=step.type, name=step.name,
                    status="completed", at=at, duration_ms=dur,
                    detail=f"{case} · {step.summary or 'event binding matched'}",
                    input_preview=(
                        '{\n'
                        f'  "event": "{step.config.get("event", step.name)}",\n'
                        f'  "case_ref": "{case}",\n'
                        f'  "source": "{step.config.get("source", "transport")}"\n'
                        '}'
                    ),
                    output_preview=f'{{ "run_id": "{summary.id}", "accepted": true }}',
                )
            )
        elif step.type == "modelcall":
            model = step.config.get("model", "claude-sonnet-5")
            rate_in, rate_out = _MODEL_RATES.get(model, (3.0, 15.0))
            # Back into a plausible token split from this call's cost share.
            cin = max(200, int((cost_share * 0.55 / rate_in) * 1_000_000))
            cout = max(60, int((cost_share * 0.45 / rate_out) * 1_000_000))
            dur = int(rng.uniform(900, 4200))
            tokens_in += cin
            tokens_out += cout
            prompt_version = step.config.get("prompt", "inline")
            temperature = float(step.config.get("temperature", "0.0") or 0.0)
            prompt = (
                f"[system] {step.summary}\n"
                f"[prompt {prompt_version}]\n"
                f"[user] Document for {case}. Return structured fields only."
            )
            response = (
                '{\n'
                f'  "case_ref": "{case}",\n'
                f'  "amount_usd": {amount},\n'
                f'  "confidence": {round(rng.uniform(0.72, 0.99), 2)},\n'
                '  "fields_extracted": '
                f'{int(rng.uniform(6, 18))}\n'
                '}'
            )
            calls.append(
                ModelCallRecord(
                    step_id=step.id, step_name=step.name, model=model,
                    input_tokens=cin, output_tokens=cout,
                    cost_usd=round(cost_share, 3), duration_ms=dur,
                    prompt_preview=prompt, raw_response=response,
                    prompt_version=prompt_version, temperature=temperature,
                )
            )
            steps.append(
                RunStepEvent(
                    step_id=step.id, type=step.type, name=step.name,
                    status="completed", at=at, duration_ms=dur,
                    detail=(
                        f"{model} · in {cin:,} tok · out {cout:,} tok · "
                        f"${cost_share:.3f} · output captured for audit"
                    ),
                    input_preview=prompt, output_preview=response,
                )
            )
        elif step.type == "rule":
            verdict = "review" if raised else "pass"
            threshold = step.config.get("threshold", "—")
            steps.append(
                RunStepEvent(
                    step_id=step.id, type=step.type, name=step.name,
                    status="completed", at=at, duration_ms=int(rng.uniform(1, 8)),
                    verdict=verdict,
                    detail=f"pure check · inputs recorded · {step.summary}",
                    input_preview=(
                        f'{{ "value": {amount}, "threshold": "{threshold}" }}'
                    ),
                    output_preview=(
                        f'{{ "verdict": "{verdict}", "rule_type": '
                        f'"{step.config.get("rule_type", "predicate")}" }}'
                    ),
                )
            )
        elif step.type == "task":
            if not raised:
                steps.append(
                    RunStepEvent(
                        step_id=step.id, type=step.type, name=step.name,
                        status="skipped", at=at,
                        detail="not raised — rule verdict allowed the automatic path",
                    )
                )
                continue
            queue = step.config.get("queue", defn.queues[0] if defn.queues else "")
            if summary.status == "waiting_on_human":
                steps.append(
                    RunStepEvent(
                        step_id=step.id, type=step.type, name=step.name,
                        status="waiting", at=at, queue=queue,
                        detail="run suspended in place — resumes when the Task resolves",
                        input_preview=(
                            f'{{ "queue": "{queue}", "case_ref": "{case}", '
                            f'"proposed": {amount} }}'
                        ),
                        output_preview="(awaiting human judgment)",
                    )
                )
                stopped = True
                continue
            decider = _REVIEWERS[int(_hex(summary.id, 3), 16) % len(_REVIEWERS)]
            steps.append(
                RunStepEvent(
                    step_id=step.id, type=step.type, name=step.name,
                    status="completed", at=at, queue=queue,
                    decided_by=decider, suspended_minutes=summary.suspended_minutes,
                    detail=(
                        "approved with corrections — decision and correction "
                        "appended to the curation ledger; correction set updated"
                    ),
                    input_preview=(
                        f'{{ "queue": "{queue}", "case_ref": "{case}", '
                        f'"proposed": {amount} }}'
                    ),
                    output_preview=(
                        '{\n'
                        '  "decision": "approved_with_corrections",\n'
                        f'  "decided_by": "{decider}",\n'
                        '  "corrections": ["po_number"]\n'
                        '}'
                    ),
                )
            )
            at = at + timedelta(minutes=summary.suspended_minutes)
        elif step.type == "action":
            if summary.status == "escalated":
                steps.append(
                    RunStepEvent(
                        step_id=step.id, type=step.type, name=step.name,
                        status="failed", at=at,
                        detail=(
                            "target system rejected the write after retries — "
                            "escalated to a human Task with full context; no rollback assumed"
                        ),
                        input_preview=(
                            f'{{ "target": "{step.config.get("target", "system")}", '
                            f'"case_ref": "{case}" }}'
                        ),
                        output_preview=(
                            '{ "error": "rejected_by_target", "retries": 3, '
                            '"escalated": true }'
                        ),
                    )
                )
                stopped = True
                continue
            ledger = str(80000 + int(_hex(f"{summary.id}:{step.id}", 4), 16) % 19999)
            steps.append(
                RunStepEvent(
                    step_id=step.id, type=step.type, name=step.name,
                    status="completed", at=at, duration_ms=int(rng.uniform(120, 900)),
                    ledger_ref=ledger,
                    detail=(
                        "committed through the Action Gateway — schema, permission "
                        "and status gates passed; stamped originated-by-Cumulus"
                    ),
                    input_preview=(
                        '{\n'
                        f'  "target": "{step.config.get("target", "system")}",\n'
                        f'  "case_ref": "{case}",\n'
                        f'  "amount_usd": {amount}\n'
                        '}'
                    ),
                    output_preview=(
                        '{\n'
                        f'  "committed": true,\n'
                        f'  "ledger_ref": "{ledger}",\n'
                        '  "originated_by": "cumulus"\n'
                        '}'
                    ),
                )
            )
        elif step.type in ("event", "transform"):
            steps.append(
                RunStepEvent(
                    step_id=step.id, type=step.type, name=step.name,
                    status="completed", at=at, duration_ms=dur,
                    detail=step.summary,
                    input_preview=f'{{ "case_ref": "{case}" }}',
                    output_preview=(
                        f'{{ "event": "{step.config.get("event", step.name)}" }}'
                        if step.type == "event"
                        else f'{{ "records": {int(rng.uniform(1, 40))} }}'
                    ),
                )
            )
        at = at + timedelta(milliseconds=steps[-1].duration_ms if steps else 50)

    trigger = next((s for s in defn.steps if s.type == "trigger"), None)
    return RunDetail(
        **summary.model_dump(),
        trigger_event=trigger.config.get("event", trigger.name) if trigger else "",
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        steps=steps,
        calls=calls,
    )


def audit_entries(defn: FlowDefinition, limit: int) -> list[AuditEntry]:
    """The ledger view: what this definition decided, committed, and learned."""
    out: list[AuditEntry] = []
    for run in recent_runs(defn, max(limit * 2, 40)):
        detail = _run_detail(defn, run)
        for ev in detail.steps:
            if ev.type == "task" and ev.status == "completed":
                out.append(
                    AuditEntry(
                        at=ev.at, kind="decision", actor=ev.decided_by,
                        summary=f"{ev.name}: approved with corrections ({run.case_ref})",
                        run_id=run.id,
                    )
                )
                out.append(
                    AuditEntry(
                        at=ev.at, kind="knowledge", actor="system:flywheel",
                        summary=f"correction appended to correction set ({run.case_ref})",
                        run_id=run.id,
                    )
                )
            elif ev.type == "action" and ev.status == "completed":
                out.append(
                    AuditEntry(
                        at=ev.at, kind="action", actor=f"flow:{defn.id}",
                        summary=f"{ev.name} committed via Action Gateway ({run.case_ref})",
                        run_id=run.id, ledger_ref=ev.ledger_ref,
                    )
                )
        if writes_canonical(defn) and run.status == "completed":
            rng = _h(f"{run.id}:resolution")
            if rng.random() < 0.35:
                out.append(
                    AuditEntry(
                        at=run.started_at, kind="resolution", actor="rule:precedence",
                        summary=(
                            f"claim conflict resolved by precedence rule ({run.case_ref}); "
                            "competing claims retained"
                        ),
                        run_id=run.id,
                    )
                )
        if len(out) >= limit * 2:
            break
    out.sort(key=lambda e: e.at, reverse=True)
    return out[:limit]


# ---------------------------------------------------------------------------
# Correction sets (synthesized from the flywheel's telemetry)
# ---------------------------------------------------------------------------


def correction_sets(defn: FlowDefinition) -> list[CorrectionSet]:
    """Per-ModelCall correction sets: every human correction the flywheel
    auto-appended over the last 30 days, with a sample of recent pairs.

    Synthesized deterministically like the rest of the telemetry — the
    contract (a per-step, auto-accumulated eval dataset) is the real part.
    """
    usage = usage_series(defn, 30)
    total_corrections = sum(d.tasks_raised for d in usage)
    model_steps = [s for s in defn.steps if s.type == "modelcall"]
    if not model_steps:
        return []
    fields_by_step: dict[str, list[str]] = {}
    pmap = producer_map(defn.contract, defn.steps)
    for fact in defn.contract.facts:
        step_id = pmap.get(fact.path, "")
        if step_id:
            fields_by_step.setdefault(step_id, []).append(fact.path)
    out: list[CorrectionSet] = []
    for i, step in enumerate(model_steps):
        rng = _h(f"{defn.id}:corrections:{step.id}")
        # The first extraction step absorbs most human corrections.
        share = 0.7 if i == 0 else 0.3 / max(1, len(model_steps) - 1)
        pairs = int(total_corrections * share)
        fields = fields_by_step.get(step.id) or [f"{defn.id}.output"]
        sample = []
        for n in range(min(3, max(1, pairs))):
            field = fields[n % len(fields)]
            base = 900 + int(_hex(f"{step.id}:{n}", 4), 16) % 9000
            sample.append(
                CorrectionPair(
                    field=field,
                    raw=str(base),
                    corrected=str(base + rng.randint(-40, 60)),
                    by=_REVIEWERS[(n + i) % len(_REVIEWERS)],
                    at=utcnow() - timedelta(hours=rng.uniform(2, 200)),
                )
            )
        out.append(
            CorrectionSet(
                step_id=step.id,
                step_name=step.name,
                model=step.config.get("model", ""),
                pairs=pairs,
                last_appended=(
                    utcnow() - timedelta(hours=rng.uniform(1, 30))
                    if pairs
                    else None
                ),
                sample=sample,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Case document synthesis (the review/labelling surface's raw material)
# ---------------------------------------------------------------------------


def _fact_value(case: GoldenCase, path: str, default: str = "") -> str:
    return next((a.expected for a in case.assertions if a.path == path), default)


def case_document(defn: FlowDefinition, case: GoldenCase) -> CaseDocument:
    """The fixture document behind a golden case, with grounded regions.

    In the real system the artifact is the stored asset and the regions come
    off the extraction ModelCall's grounding output; here both are synthesized
    coherently so the review screen is real even though the receipt is not.
    """
    rng = _h(f"{defn.id}:{case.id}:doc")
    lines: list[DocLine] = []
    regions: list[DocRegion] = []

    def put(text: str, x: float, y: float, *, size: float = 11, bold: bool = False,
            mono: bool = False, muted: bool = False) -> None:
        lines.append(DocLine(text=text, x=x, y=y, size=size, bold=bold,
                             mono=mono, muted=muted))

    def ground(path: str, x: float, y: float, value: str, *,
               w: float = 0.16, h: float = 0.028) -> None:
        if not value:
            return
        regions.append(
            DocRegion(
                path=path,
                # Lines are placed by their top edge; the box hugs the text
                # with a small halo on each side.
                box=[x - 0.008, y - 0.006, w, h],
                value=value,
                confidence=round(rng.uniform(0.82, 0.99), 2),
            )
        )

    if defn.id == "expense-reimbursement":
        vendor = _fact_value(case, "receipt.vendor", "corner-bistro")
        date_str = _fact_value(case, "receipt.date", "2026-08-14")
        total = _fact_value(case, "receipt.total_usd", "64.20")
        category = _fact_value(case, "receipt.category", "meals")
        vendor_display = vendor.replace("-", " ").title()
        put(vendor_display, 0.07, 0.08, size=17, bold=True)
        put("312 Lakeshore Ave · Madison WI 53703", 0.07, 0.115, size=9, muted=True)
        put("RECEIPT", 0.72, 0.08, size=13, bold=True, muted=True)
        put(date_str, 0.72, 0.11, size=10, mono=True)
        ground("receipt.vendor", 0.07, 0.08, vendor, w=0.30, h=0.034)
        ground("receipt.date", 0.72, 0.11, date_str, w=0.16)
        put("CATEGORY", 0.07, 0.19, size=8.5, bold=True, muted=True)
        put(category, 0.07, 0.215, size=10, mono=True)
        ground("receipt.category", 0.07, 0.215, category, w=0.18)
        item_names = [
            "House salad", "Grilled entrée", "Sparkling water", "Service charge",
        ]
        put("ITEM", 0.07, 0.30, size=8.5, bold=True, muted=True)
        put("AMOUNT", 0.80, 0.30, size=8.5, bold=True, muted=True)
        row_y = 0.335
        for idx in range(2 + int(_hex(case.id, 2), 16) % 3):
            put(item_names[idx % len(item_names)], 0.07, row_y, size=10)
            put(f"${rng.uniform(6, 40):.2f}", 0.80, row_y, size=10, mono=True)
            row_y += 0.035
        put("TOTAL", 0.62, row_y + 0.04, size=10, bold=True)
        put(f"${total}", 0.80, row_y + 0.04, size=12, bold=True, mono=True)
        ground("receipt.total_usd", 0.80, row_y + 0.04, total, w=0.13, h=0.032)
        put("Thank you — retain this receipt for reimbursement",
            0.07, 0.92, size=8.5, muted=True)
        return CaseDocument(
            case_id=case.id,
            title=f"{vendor_display} · {date_str}",
            kind="expense",
            lines=lines,
            regions=regions,
        )

    # Generic fixture: the document is a structured sheet of the case's facts.
    put(case.name, 0.07, 0.08, size=16, bold=True)
    put(f"fixture {case.input_ref or case.id} · {defn.name or defn.id}",
        0.07, 0.115, size=9, muted=True)
    y = 0.19
    for a in case.assertions:
        put(a.path, 0.07, y, size=9, muted=True, mono=True)
        put(a.expected, 0.48, y, size=10.5, mono=True)
        ground(a.path, 0.48, y, a.expected, w=0.34)
        y += 0.045
    if case.expected:
        put("EXPECTED OUTCOME", 0.07, y + 0.03, size=8.5, bold=True, muted=True)
        put(case.expected, 0.07, y + 0.06, size=10)
    return CaseDocument(
        case_id=case.id,
        title=case.name,
        kind="generic",
        lines=lines,
        regions=regions,
    )


# ---------------------------------------------------------------------------
# Version diffing
# ---------------------------------------------------------------------------

# Fields whose change is worth surfacing on the canvas. `id` is the identity,
# so it is deliberately absent: a changed id is an add plus a remove.
_DIFFABLE = ("type", "name", "summary", "config", "next", "branch_labels")


def diff_versions(
    candidate: FlowVersion, baseline: FlowVersion
) -> list[StepDiff]:
    """Annotate `candidate`'s steps against `baseline`.

    Removed steps are appended at the end carrying the *baseline* step, so the
    canvas can render a ghost node rather than silently dropping it — "what
    did this draft delete" is exactly the question a reviewer asks.
    """
    base_by_id = {s.id: s for s in baseline.steps}
    out: list[StepDiff] = []
    for step in candidate.steps:
        previous = base_by_id.get(step.id)
        if previous is None:
            out.append(StepDiff(step=step, change="added"))
            continue
        changed = [
            field
            for field in _DIFFABLE
            if getattr(step, field) != getattr(previous, field)
        ]
        out.append(
            StepDiff(
                step=step,
                change="modified" if changed else "unchanged",
                changed_fields=changed,
            )
        )
    candidate_ids = {s.id for s in candidate.steps}
    for step in baseline.steps:
        if step.id not in candidate_ids:
            out.append(StepDiff(step=step, change="removed"))
    return out


# ---------------------------------------------------------------------------
# Cost & performance recommender
# ---------------------------------------------------------------------------

# Cheaper substitutes, in descending capability. A downgrade is only ever
# proposed one rung at a time: two rungs is a different flow, not a saving.
_DOWNGRADE_PATH = {
    "claude-opus-5": "claude-sonnet-5",
    "claude-fable-5": "claude-sonnet-5",
    "claude-sonnet-5": "claude-haiku-4-5",
}


def _blended_rate(model: str) -> float:
    rate_in, rate_out = _MODEL_RATES.get(model, (3.0, 15.0))
    return rate_in * 0.6 + rate_out * 0.4


def recommendations(
    defn: FlowDefinition, usage: list[UsageDay], golden_cases: int
) -> list[Recommendation]:
    """Insight cards derived from this definition's own execution telemetry.

    Each card carries the evidence it was derived from, because an
    unauditable recommendation cannot be acted on safely — and applying one
    edits governed knowledge, which lands in the ledger.
    """
    out: list[Recommendation] = []
    runs = sum(d.runs for d in usage)
    completed = sum(d.completed for d in usage)
    spend = sum(d.cost_usd for d in usage)
    tasks = sum(d.tasks_raised for d in usage)
    if runs == 0:
        return out
    success = completed / runs
    model_steps = [s for s in defn.steps if s.type == "modelcall"]
    per_step_spend = spend / len(model_steps) if model_steps else 0.0
    days = max(1, len(usage))
    monthly = 30.0 / days

    for step in model_steps:
        model = step.config.get("model", "claude-sonnet-5")
        cheaper = _DOWNGRADE_PATH.get(model)
        # A downgrade is only defensible when the step is expensive AND the
        # flow is currently succeeding with room to spare. Below the bar,
        # the right move is to fix quality, not to spend less failing.
        if not cheaper or per_step_spend < 5 or success < 0.97:
            continue
        ratio = _blended_rate(cheaper) / _blended_rate(model)
        savings = per_step_spend * (1 - ratio) * monthly
        if savings < 1:
            continue
        out.append(
            Recommendation(
                id=f"{defn.id}:downgrade:{step.id}",
                kind="model-downgrade",
                title=f"Downgrade opportunity: {step.name}",
                detail=(
                    f"{step.name} runs on {model}. This flow is completing "
                    f"{success:.1%} of runs, which leaves headroom to try "
                    f"{cheaper} on the golden set before promoting it."
                ),
                evidence=(
                    f"{runs:,} runs / {days}d · ${per_step_spend:,.2f} attributed "
                    f"to this step · success {success:.1%}"
                ),
                step_id=step.id,
                step_name=step.name,
                estimated_monthly_savings_usd=round(savings, 2),
                confidence=round(min(0.95, success), 2),
                proposed_change={
                    "step_id": step.id,
                    "field": "model",
                    "from": model,
                    "to": cheaper,
                },
            )
        )

    # Prompt caching: a stable system prompt re-sent on every run is the
    # cheapest win available, and it is invisible unless someone looks at the
    # input/output token ratio.
    if model_steps and runs >= 200:
        step = model_steps[0]
        tokens = sum(d.tokens for d in usage)
        if tokens / max(1, runs) > 3000:
            savings = spend * 0.25 * monthly
            out.append(
                Recommendation(
                    id=f"{defn.id}:cache:{step.id}",
                    kind="prompt-cache",
                    title=f"Cache the standing prompt on {step.name}",
                    detail=(
                        "Input tokens dominate this definition's spend and the "
                        "system prompt is identical across runs, so the stable "
                        "prefix can be cached rather than re-sent."
                    ),
                    evidence=(
                        f"{tokens / max(1, runs):,.0f} tokens per run across "
                        f"{runs:,} runs · ${spend:,.2f} in {days}d"
                    ),
                    step_id=step.id,
                    step_name=step.name,
                    estimated_monthly_savings_usd=round(savings, 2),
                    confidence=0.6,
                    proposed_change={
                        "step_id": step.id,
                        "field": "cache",
                        "from": step.config.get("cache", "off"),
                        "to": "prefix",
                    },
                )
            )

    # Budget drift: the per-run cap is governed knowledge, so a systematic
    # breach is a decision someone owes, not a number to quietly raise.
    cost_per_run = spend / runs
    if defn.model_budget_usd_per_run and cost_per_run > defn.model_budget_usd_per_run:
        out.append(
            Recommendation(
                id=f"{defn.id}:budget",
                kind="budget",
                title="Runs are exceeding their model budget",
                detail=(
                    f"Average spend is ${cost_per_run:.2f} per run against a "
                    f"${defn.model_budget_usd_per_run:.2f} cap. Either the cap is "
                    "stale or the pipeline has grown — both are decisions, so "
                    "this one is not applied automatically."
                ),
                evidence=(
                    f"${spend:,.2f} over {runs:,} runs in {days}d "
                    f"= ${cost_per_run:.2f}/run"
                ),
                confidence=0.9,
            )
        )

    # Coverage: a thin golden set makes every other recommendation unsafe to
    # accept, so it is surfaced as an insight rather than left implicit.
    if golden_cases < 8:
        out.append(
            Recommendation(
                id=f"{defn.id}:coverage",
                kind="coverage",
                title="Golden set is thin for this much traffic",
                detail=(
                    f"{golden_cases} cases cover {runs:,} runs. Promote recent "
                    "runs — especially ones a human corrected — so a candidate "
                    "version can be graded on cases that actually occur."
                ),
                evidence=(
                    f"{golden_cases} golden cases · {tasks:,} human decisions "
                    f"available to promote in {days}d"
                ),
                confidence=0.75,
            )
        )

    # Batching: bulk work paying per-record model costs is the doctrine's
    # named anti-pattern. The tell is the trigger, not a type — a flow woken
    # by a window is doing bulk work whatever anyone called it.
    if is_windowed(defn) and model_steps and runs > 300:
        savings = spend * 0.35 * monthly
        step = model_steps[0]
        out.append(
            Recommendation(
                id=f"{defn.id}:batch:{step.id}",
                kind="batching",
                title=f"Widen the window on {step.name}",
                detail=(
                    "This DataFlow invokes a model on a narrow window. A wider "
                    "window amortizes the standing prompt across more records "
                    "and cuts the per-record share."
                ),
                evidence=f"{runs:,} windowed runs in {days}d · ${spend:,.2f}",
                step_id=step.id,
                step_name=step.name,
                estimated_monthly_savings_usd=round(savings, 2),
                confidence=0.55,
                proposed_change={
                    "step_id": step.id,
                    "field": "window",
                    "from": step.config.get("window", "2h"),
                    "to": "6h",
                },
            )
        )

    out.sort(key=lambda r: r.estimated_monthly_savings_usd, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class FlowService:
    def __init__(
        self,
        flows: FlowDirectory,
        golden_sets: GoldenSetDirectory,
        test_runs: TestRunDirectory,
        # Seconds the simulated harness spends per golden case; tests shrink it.
        test_seconds_per_case: float = 0.15,
    ) -> None:
        self.flows = flows
        self.golden_sets = golden_sets
        self.test_runs = test_runs
        self.test_seconds_per_case = test_seconds_per_case

    # ---- registry ----

    def list(self) -> list[FlowDefinition]:
        return self.flows.list()

    def get(self, flow_id: str) -> FlowDefinition | None:
        return self.flows.get(flow_id)

    def require(self, flow_id: str) -> FlowDefinition:
        defn = self.flows.get(flow_id)
        if defn is None:
            raise FlowNotFoundError(flow_id)
        return defn

    def create(self, defn: FlowDefinition) -> FlowDefinition:
        return self.flows.create(defn)

    def update(self, defn: FlowDefinition) -> FlowDefinition:
        return self.flows.update(
            defn.model_copy(update={"updated_at": utcnow()})
        )

    def delete(self, flow_id: str) -> None:
        self.flows.delete(flow_id)
        self.golden_sets.delete(flow_id)

    # ---- versions ----

    def versions(self, flow_id: str) -> list[FlowVersion]:
        return self.require(flow_id).version_history()

    def version(self, flow_id: str, version: int) -> FlowVersion | None:
        return self.require(flow_id).find_version(version)

    def version_by_label(self, flow_id: str, label: str) -> FlowVersion | None:
        return next(
            (v for v in self.versions(flow_id) if v.display() == label), None
        )

    def diff(
        self, flow_id: str, version: int, against: int | None
    ) -> list[StepDiff]:
        defn = self.require(flow_id)
        candidate = defn.find_version(version)
        if candidate is None:
            raise ValueError(f"Version not found: v{version}")
        baseline = (
            defn.find_version(against) if against is not None else defn.active_version()
        )
        if baseline is None:
            raise ValueError(f"Version not found: v{against}")
        return diff_versions(candidate, baseline)

    def _ensure_draft(
        self, defn: FlowDefinition, authored_by: str
    ) -> tuple[FlowDefinition, FlowVersion]:
        """Open a draft off the active version, or return the existing one.

        A change is never applied to the live version: the point of a draft is
        that a candidate is graded against the golden set before it becomes
        the thing running in production.
        """
        existing = defn.draft_version()
        if existing is not None:
            return defn, existing
        active = defn.active_version()
        draft = FlowVersion(
            version=active.version + 1,
            label=f"v{active.version + 1}-draft",
            status="draft",
            steps=[s.model_copy(deep=True) for s in active.steps],
            model_budget_usd_per_run=defn.model_budget_usd_per_run,
            notes="Opened from the console.",
            authored_by=authored_by,
        )
        history = defn.version_history()
        return defn.model_copy(update={"versions": history + [draft]}), draft

    def apply_recommendation(
        self, flow_id: str, rec: Recommendation, actor: str
    ) -> tuple[FlowDefinition, FlowVersion]:
        """Stage a recommendation's change into the draft version.

        Advisory cards (no `proposed_change`) are refused rather than
        silently no-oping: "applied" must mean something changed.
        """
        if not rec.proposed_change:
            raise ValueError(
                "This insight is advisory — there is no mechanical change to apply."
            )
        defn = self.require(flow_id)
        defn, draft = self._ensure_draft(defn, actor)
        change = rec.proposed_change
        step_id = change.get("step_id", "")
        field = change.get("field", "")
        target = change.get("to", "")
        steps = []
        touched = False
        for step in draft.steps:
            if step.id != step_id:
                steps.append(step)
                continue
            config = dict(step.config)
            config[field] = target
            steps.append(step.model_copy(update={"config": config}))
            touched = True
        if not touched:
            raise ValueError(f"Step not found in the draft: {step_id}")
        note = (
            f"{rec.title} — set {field} to {target} on {step_id} "
            f"(applied by {actor})."
        )
        updated_draft = draft.model_copy(
            update={
                "steps": steps,
                "notes": (draft.notes + " " + note).strip(),
                "authored_by": actor,
            }
        )
        versions = [
            updated_draft if v.version == draft.version else v
            for v in defn.version_history()
        ]
        saved = self.update(defn.model_copy(update={"versions": versions}))
        log.info(
            "Recommendation applied to draft",
            extra={
                "flowId": flow_id,
                "recommendationId": rec.id,
                "draft": updated_draft.display(),
                "actor": actor,
            },
        )
        return saved, updated_draft

    def recommendations(self, flow_id: str, days: int = 30) -> list[Recommendation]:
        defn = self.require(flow_id)
        return recommendations(
            defn, usage_series(defn, days), len(self.golden_set(flow_id).cases)
        )

    # ---- golden set ----

    def golden_set(self, flow_id: str) -> GoldenSet:
        return self.golden_sets.get(flow_id) or GoldenSet(flow_id=flow_id)

    def add_golden_case(self, flow_id: str, case: GoldenCase) -> GoldenSet:
        golden = self.golden_set(flow_id)
        if any(c.id == case.id for c in golden.cases):
            raise ValueError(f"Golden case already exists: {case.id}")
        golden.cases.append(case)
        golden.bump(case.added_by, f"added {case.id} · {case.source}")
        return self.golden_sets.put(golden)

    def import_golden_cases(
        self, flow_id: str, cases: list[GoldenCase], by: str
    ) -> GoldenSet:
        """Bulk upload — many cases, one dataset revision."""
        golden = self.golden_set(flow_id)
        existing = {c.id for c in golden.cases}
        clash = [c.id for c in cases if c.id in existing]
        if clash:
            raise ValueError(f"Golden case already exists: {', '.join(clash)}")
        golden.cases.extend(cases)
        golden.bump(by, f"imported {len(cases)} cases")
        return self.golden_sets.put(golden)

    def remove_golden_case(self, flow_id: str, case_id: str) -> GoldenSet:
        golden = self.golden_set(flow_id)
        remaining = [c for c in golden.cases if c.id != case_id]
        if len(remaining) == len(golden.cases):
            raise ValueError(f"Golden case not found: {case_id}")
        golden.cases = remaining
        golden.bump("", f"removed {case_id}")
        return self.golden_sets.put(golden)

    def review_golden_case(
        self, flow_id: str, case_id: str, corrections: dict[str, str], by: str
    ) -> GoldenSet:
        """A human confirmed/corrected the case's labels in the review screen.

        Corrections update the assertions' expected values; a confirmation
        with no corrections still stamps the reviewer. Either way the dataset
        version moves, because what a pass means changed (or was vouched for).
        """
        golden = self.golden_set(flow_id)
        case = next((c for c in golden.cases if c.id == case_id), None)
        if case is None:
            raise ValueError(f"Golden case not found: {case_id}")
        corrected: list[str] = []
        for path, value in corrections.items():
            assertion = next((a for a in case.assertions if a.path == path), None)
            if assertion is None:
                case.assertions.append(FactAssertion(path=path, expected=value))
                corrected.append(path)
            elif assertion.expected != value:
                assertion.expected = value
                corrected.append(path)
        case.reviewed_by = by
        case.reviewed_at = utcnow()
        note = (
            f"reviewed {case_id} · corrected {', '.join(corrected)}"
            if corrected
            else f"reviewed {case_id} · confirmed as labelled"
        )
        golden.bump(by, note)
        return self.golden_sets.put(golden)

    # ---- test harness ----

    def launch_test_run(
        self,
        flow_id: str,
        candidate_version: str,
        baseline_version: str,
        launched_by: str,
        reads: ReadMode = "pinned",
        launched_for: str = "",
    ) -> TestRun:
        defn = self.require(flow_id)
        golden = self.golden_set(flow_id)
        if not golden.cases:
            raise ValueError(
                "No golden set: add cases before launching a test run."
            )
        draft = defn.draft_version()
        run = TestRun(
            id="test_" + _hex(f"{flow_id}:{time.time_ns()}", 10),
            flow_id=flow_id,
            candidate_version=(
                candidate_version
                or (draft.display() if draft else f"v{defn.version + 1}-draft")
            ),
            baseline_version=baseline_version or defn.active_version().display(),
            launched_by=launched_by,
            total_cases=len(golden.cases),
            dataset_version=golden.version,
            reads=reads,
            launched_for=launched_for,
        )
        self.test_runs.put(run)
        thread = threading.Thread(
            target=self._execute_test_run,
            args=(run.id, defn, list(golden.cases)),
            daemon=True,
            name=f"test-run-{run.id}",
        )
        thread.start()
        return run

    def _version_cost_factor(self, defn: FlowDefinition, label: str) -> float:
        """How this version's model choices scale cost, relative to the active one.

        This is what makes the recommender loop honest end to end: apply a
        downgrade to the draft, run the suite, and the cost delta reflects the
        models the draft actually names rather than a hard-coded discount.
        """
        version = next(
            (v for v in defn.version_history() if v.display() == label), None
        )
        if version is None:
            return 1.0
        active = defn.active_version()

        def weight(steps: list[FlowStep]) -> float:
            rates = [
                _blended_rate(s.config.get("model", "claude-sonnet-5"))
                for s in steps
                if s.type == "modelcall"
            ]
            return sum(rates) if rates else 0.0

        base = weight(active.steps)
        if base <= 0:
            return 1.0
        return weight(version.steps) / base

    # Case-level target pass rate by provenance; individual assertions are
    # graded at rate**(1/n) so a many-assertion case is not punished for
    # being thoroughly labelled.
    _SOURCE_RATES = {
        "correction-set": 0.94,
        "exemplar": 0.84,
        "human-authored": 0.90,
        "promoted-from-run": 0.92,
    }

    # Verdict-ish expected values flip to their counterpart when they diverge,
    # so a failure reads like a wrong branch, not like noise.
    _FLIP = {
        "pass": "review", "review": "pass", "approve": "elevate",
        "elevate": "approve", "standard": "escalate", "escalate": "standard",
        "accept": "review", "true": "false", "false": "true",
        "completed": "escalated", "matched": "mismatched",
        "post": "review", "one-shot": "ensemble", "ensemble": "one-shot",
        # A committed Action that should not have committed flips to "none";
        # "none" itself flips to the flow's own terminal commit, derived from
        # its steps in `_diverged` rather than hard-coded here.
        "record-reimbursement": "none", "post-to-ap": "none",
    }

    @staticmethod
    def _traj_label(t: TrajectoryAssertion) -> str:
        return {
            "action_committed": "actions committed",
            "task_raised": "human task raised",
            "terminal_status": "terminal status",
            "waypoint": "waypoint",
        }[t.kind]

    @staticmethod
    def _flat_assertions(
        case: GoldenCase,
    ) -> list[tuple[str, str, str, str]]:
        """(kind, label, op, expected) for every claim this case makes.

        A legacy case with no structured assertions degrades to one waypoint
        derived from its prose expectation, so old datasets keep grading.
        """
        out: list[tuple[str, str, str, str]] = []
        for a in case.assertions:
            out.append(("fact", a.path, a.op, a.expected))
        for t in case.trajectory:
            out.append(
                ("trajectory", FlowService._traj_label(t), "equals", t.expected)
            )
        if not out:
            out.append(
                ("trajectory", "waypoint", "equals",
                 case.expected or "matches the recorded outcome")
            )
        return out

    def _diverged(self, defn: FlowDefinition, expected: str, key: str) -> str:
        """A plausible wrong answer for `expected`, deterministic per key."""
        rng = _h(key)
        lowered = expected.strip().lower()
        if lowered == "none":
            # An Action committed that should not have: name this flow's own
            # terminal commit, derived from its steps.
            action = next(
                (s for s in reversed(defn.steps) if s.type == "action"), None
            )
            return action.name if action else "(an Action committed)"
        flip = self._FLIP.get(lowered)
        if flip is not None:
            return flip
        cleaned = expected.replace(",", "").replace("$", "").replace("−", "-")
        try:
            value = float(cleaned)
        except ValueError:
            return "(diverged — output did not match the golden answer)"
        skew = rng.choice([0.9, 1.1, 0.88, 1.13])
        decimals = len(cleaned.split(".")[1]) if "." in cleaned else 0
        return f"{value * skew:.{decimals}f}"

    def _grade_case(
        self,
        defn: FlowDefinition,
        case: GoldenCase,
        version: str,
        factor: float,
        pmap: dict[str, str],
        traj_steps: dict[str, str],
        live: bool,
    ) -> tuple[list[AssertionResult], bool, float, int]:
        """Assertion-by-assertion verdicts for one (case, version) pair.

        Keyed by (flow, case, assertion, version) — the same candidate always
        reproduces the same result, a different candidate genuinely can
        diverge, and a live-reads run is honestly a different experiment.
        """
        flat = self._flat_assertions(case)
        rate = self._SOURCE_RATES.get(case.source, 0.9)
        per_assertion = rate ** (1.0 / len(flat))
        results: list[AssertionResult] = []
        for kind, label, op, expected in flat:
            key = f"{defn.id}:{case.id}:{version}:{label}" + (":live" if live else "")
            arng = _h(key)
            ok = arng.random() < per_assertion
            verdict: AssertionVerdict = "pass"
            grader: Grader = "judge" if op == "judge" else "deterministic"
            actual = expected
            if not ok:
                # An uncertain judge escalates to a human review Task instead
                # of guessing — ambiguity is judgment work, not noise.
                if op == "judge" and arng.random() < 0.5:
                    verdict, grader = "review", "human"
                    actual = "(judge uncertain — review task raised)"
                else:
                    verdict = "fail"
                    actual = self._diverged(defn, expected, key + ":actual")
            step_id = (
                pmap.get(label, "") if kind == "fact" else traj_steps.get(label, "")
            )
            results.append(
                AssertionResult(
                    kind=kind, label=label, op=op, expected=expected,
                    actual=actual, verdict=verdict, grader=grader,
                    step_id=step_id,
                )
            )
        passed = all(r.verdict == "pass" for r in results)
        crng = _h(f"{defn.id}:{case.id}:{version}:cost")
        cost = round(defn.profile.cost_per_run * crng.uniform(0.5, 1.1) * factor, 3)
        # A cheaper model is also a faster one, roughly; enough to make the
        # latency column move in the direction the cost column moves.
        duration = int(crng.uniform(1200, 6500) * (0.75 + 0.25 * factor))
        return results, passed, cost, duration

    def _execute_test_run(
        self, test_id: str, defn: FlowDefinition, cases: list[GoldenCase]
    ) -> None:
        """Simulated harness execution: case-by-case, writes suppressed.

        Each case is graded against BOTH the candidate and the baseline, so
        the comparison is like-for-like rather than candidate-versus-
        remembered-numbers, and a regression is visible per case. Fact
        failures are localized to the step that owns the fact in the
        candidate's producer map — the projection that keeps labels stable
        while the topology morphs.
        """
        run = self.test_runs.get(test_id)
        if run is None:
            return
        try:
            candidate_factor = self._version_cost_factor(defn, run.candidate_version)
            baseline_factor = self._version_cost_factor(defn, run.baseline_version)
            candidate = next(
                (v for v in defn.version_history()
                 if v.display() == run.candidate_version),
                None,
            )
            steps = candidate.steps if candidate else defn.steps
            pmap = producer_map(defn.contract, steps)
            traj_steps = {
                "actions committed": next(
                    (s.id for s in steps if s.type == "action"), ""
                ),
                "human task raised": next(
                    (s.id for s in steps if s.type == "task"), ""
                ),
            }
            step_index = {s.id: s for s in steps}
            live = run.reads == "live"
            for case in cases:
                time.sleep(self.test_seconds_per_case)
                results, passed, cost, duration = self._grade_case(
                    defn, case, run.candidate_version, candidate_factor,
                    pmap, traj_steps, live,
                )
                base_results, base_passed, base_cost, base_duration = (
                    self._grade_case(
                        defn, case, run.baseline_version, baseline_factor,
                        pmap, traj_steps, live,
                    )
                )
                for res, base in zip(results, base_results):
                    res.baseline_verdict = base.verdict
                first_fail = next(
                    (r for r in results if r.verdict != "pass"), None
                )
                run.results.append(
                    TestCaseResult(
                        case_id=case.id,
                        name=case.name,
                        passed=passed,
                        expected=case.expected,
                        actual=case.expected if passed else (
                            f"{first_fail.label}: expected {first_fail.expected}, "
                            f"got {first_fail.actual}"
                            if first_fail
                            else "diverged"
                        ),
                        cost_usd=cost,
                        duration_ms=duration,
                        baseline_passed=base_passed,
                        baseline_cost_usd=base_cost,
                        baseline_duration_ms=base_duration,
                        assertions=results,
                    )
                )
                run.passed = sum(1 for r in run.results if r.passed)
                run.baseline_passed = sum(1 for r in run.results if r.baseline_passed)
                run.cost_usd = round(sum(r.cost_usd for r in run.results), 3)
                run.baseline_cost_usd = round(
                    sum(r.baseline_cost_usd for r in run.results), 3
                )
                run.cost_delta_usd = round(run.cost_usd - run.baseline_cost_usd, 3)
                run.regressions = sum(1 for r in run.results if r.regressed)
                run.reviews_raised = sum(
                    1
                    for r in run.results
                    for a in r.assertions
                    if a.verdict == "review"
                )
                done = len(run.results)
                run.latency_delta_ms = int(
                    sum(r.duration_ms - r.baseline_duration_ms for r in run.results)
                    / max(1, done)
                )
                # Step aggregates, recomputed as results land so the heatmap
                # fills in while the run is still going.
                stats: dict[str, StepStat] = {}
                for result in run.results:
                    for a in result.assertions:
                        if not a.step_id:
                            continue
                        stat = stats.get(a.step_id)
                        if stat is None:
                            step = step_index.get(a.step_id)
                            stat = StepStat(
                                step_id=a.step_id,
                                step_name=step.name if step else a.step_id,
                                step_type=step.type if step else "",
                            )
                            stats[a.step_id] = stat
                        if a.verdict == "pass":
                            stat.passed += 1
                        else:
                            stat.failed += 1
                        if a.baseline_verdict == "pass":
                            stat.baseline_passed += 1
                        elif a.baseline_verdict is not None:
                            stat.baseline_failed += 1
                run.step_stats = list(stats.values())
                self.test_runs.put(run)
            reasons: list[str] = []
            if run.regressions:
                reasons.append(
                    f"{run.regressions} regression"
                    f"{'s' if run.regressions != 1 else ''} against "
                    f"{run.baseline_version}"
                )
            if run.passed < run.baseline_passed:
                reasons.append(
                    f"candidate passes {run.passed}/{run.total_cases}, "
                    f"below the baseline's {run.baseline_passed}"
                )
            run.gate = GateResult(passed=not reasons, reasons=reasons)
            run.status = "completed"
            run.finished_at = utcnow()
            self.test_runs.put(run)
            log.info(
                "Test run completed",
                extra={
                    "flowId": defn.id, "testId": run.id,
                    "passed": run.passed, "total": run.total_cases,
                    "baselinePassed": run.baseline_passed,
                    "regressions": run.regressions,
                    "costDelta": run.cost_delta_usd,
                    "gatePassed": run.gate.passed,
                },
            )
        except Exception:
            log.exception("Test run execution failed", extra={"testId": test_id})


def build_flow_service(
    backend: str,
    project: str | None,
    flows_collection: str,
    golden_sets_collection: str,
    test_runs_collection: str,
) -> FlowService:
    if backend == "firestore":
        return FlowService(
            FirestoreFlowDirectory(project, flows_collection),
            FirestoreGoldenSetDirectory(project, golden_sets_collection),
            FirestoreTestRunDirectory(project, test_runs_collection),
        )
    return FlowService(
        InMemoryFlowDirectory(),
        InMemoryGoldenSetDirectory(),
        InMemoryTestRunDirectory(),
    )


# ---------------------------------------------------------------------------
# Seed fleet
# ---------------------------------------------------------------------------


def _ago(days: int) -> datetime:
    return utcnow() - timedelta(days=days)


def _seed_definitions() -> list[FlowDefinition]:
    return [
        FlowDefinition(
            id="expense-reimbursement",
            name="Expense reimbursement",
            description=(
                "Reads a submitted receipt image with a ModelCall, checks it against "
                "the governed expense policy in the knowledge layer, elevates to human "
                "review when policy or confidence demands it, and records the request "
                "with its state in the reimbursements table."
            ),
            team="finance-ops",
            status="active",
            version=9,
            tags=["expenses", "reimbursement", "vision"],
            queues=["expense-review"],
            workspace="ap-workspace",
            roi_minutes_per_run=6,
            model_budget_usd_per_run=0.25,
            authored_by="fde:m.suter",
            created_at=_ago(140),
            updated_at=_ago(9),
            profile=SeedProfile(
                base_runs=48, cost_per_run=0.14, tokens_per_run=5200,
                task_rate=0.12, escalate_rate=0.004, fail_rate=0.002,
                active_seconds=20, case_prefix="exp",
            ),
            contract=FlowContract(facts=[
                ContractFact(path="receipt.vendor", type="string", kind="extraction"),
                ContractFact(path="receipt.date", type="string", kind="extraction"),
                ContractFact(path="receipt.total_usd", type="money", kind="extraction"),
                ContractFact(path="receipt.category", type="string", kind="extraction",
                             description="policy category the spend falls under"),
                ContractFact(path="reimbursement.verdict", type="verdict", kind="verdict",
                             description="approve automatically, or elevate to review"),
                ContractFact(path="reimbursement.recorded", type="bool", kind="commit",
                             description="row written to the reimbursements table with its state"),
            ]),
            steps=[
                FlowStep(id="t1", type="trigger", name="ReceiptSubmitted",
                         summary="one run per submitted receipt",
                         config={"event": "ReceiptSubmitted"}, next=["m1"]),
                FlowStep(id="m1", type="modelcall", name="extract-receipt",
                         summary="reads the receipt image: vendor, date, total, category — with a confidence per field",
                         config={"model": "claude-sonnet-5", "prompt": "receipt-extraction v9"},
                         next=["x1"]),
                FlowStep(id="x1", type="transform", name="read-policy",
                         summary="gathers the governed policy values (auto-approval limit, category caps) from the knowledge layer for the rule — rules never fetch",
                         config={"source": "knowledge:expense-approval-policy"}, next=["r1"]),
                FlowStep(id="r1", type="rule", name="needs-elevation",
                         summary="within the policy's auto-approval limit, an allowed category, and confidently extracted?",
                         config={"rule_type": "threshold", "source": "knowledge:expense-approval-policy@rev5"},
                         next=["a1", "k1"], branch_labels=["approve", "elevate"]),
                FlowStep(id="k1", type="task", name="elevate-review",
                         summary="a reviewer decides the elevated request with the extraction and the policy findings attached",
                         config={"queue": "expense-review"}, next=["a1"]),
                FlowStep(id="a1", type="action", name="record-reimbursement",
                         summary="guarded write of the request and its state into the reimbursements table",
                         config={"target": "reimbursements", "gateway": "action-gateway"}, next=["e1"]),
                FlowStep(id="e1", type="event", name="ReimbursementRecorded",
                         summary="emitted on commit", config={"event": "ReimbursementRecorded"}),
            ],
        ),
        FlowDefinition(
            id="accounts-payable",
            name="Accounts payable",
            description=(
                "Classifies the vendor on an inbound invoice, extracts one-shot from "
                "the vendor's exemplar in the knowledge layer — or, for an unknown "
                "vendor, runs three independent extractions, adjudicates them, and "
                "raises a Task to verify the new exemplar — then posts under the "
                "governed auto-post limit. Three-way match and fraud screening are "
                "declared placeholders until the PO and receiving feeds land."
            ),
            team="finance-ops",
            status="active",
            version=5,
            tags=["ap", "invoices", "vision", "ensemble"],
            queues=["ap-clerk"],
            workspace="ap-workspace",
            roi_minutes_per_run=9,
            model_budget_usd_per_run=0.60,
            authored_by="fde:m.suter",
            created_at=_ago(90),
            updated_at=_ago(4),
            profile=SeedProfile(
                base_runs=34, cost_per_run=0.31, tokens_per_run=9800,
                task_rate=0.16, escalate_rate=0.005, fail_rate=0.003,
                active_seconds=35, case_prefix="inv",
            ),
            contract=FlowContract(facts=[
                ContractFact(path="invoice.vendor_id", type="ref", kind="extraction",
                             description="canonical vendor the classifier resolves to"),
                ContractFact(path="invoice.invoice_number", type="string", kind="extraction"),
                ContractFact(path="invoice.invoice_date", type="string", kind="extraction"),
                ContractFact(path="invoice.po_number", type="string", kind="extraction"),
                ContractFact(path="invoice.total_usd", type="money", kind="extraction"),
                ContractFact(path="invoice.extraction_path", type="string", kind="verdict",
                             description="one-shot from the exemplar, or ensemble for an unknown vendor"),
                ContractFact(path="invoice.verdict", type="verdict", kind="verdict",
                             description="post automatically, or route to review"),
                ContractFact(path="invoice.posted", type="bool", kind="commit"),
            ]),
            steps=[
                FlowStep(id="t1", type="trigger", name="InvoiceReceived",
                         summary="one run per inbound invoice",
                         config={"event": "InvoiceReceived"}, next=["m0"]),
                FlowStep(id="m0", type="modelcall", name="classify-vendor",
                         summary="names the vendor and resolves it to a canonical slug, with confidence",
                         config={"model": "claude-haiku-4-5", "prompt": "vendor-classifier v4"},
                         next=["x1"]),
                FlowStep(id="x1", type="transform", name="load-exemplar",
                         summary="looks up the vendor's one-shot exemplar in the knowledge layer — the flow gathers; rules never fetch",
                         config={"source": "knowledge:vendor-exemplar-{vendor}"}, next=["r1"]),
                FlowStep(id="r1", type="rule", name="exemplar-known",
                         summary="does a verified exemplar exist for this vendor?",
                         config={"rule_type": "match-condition"},
                         next=["m1", "m2a"], branch_labels=["one-shot", "ensemble"]),
                FlowStep(id="m1", type="modelcall", name="extract-one-shot",
                         summary="extracts number, dates, PO and total using the vendor exemplar as the one-shot template",
                         config={"model": "claude-sonnet-5", "prompt": "invoice-extraction-oneshot v7"},
                         next=["x3"]),
                FlowStep(id="m2a", type="modelcall", name="extract-a",
                         summary="independent blind extraction — first of three, because no exemplar exists yet",
                         config={"model": "claude-sonnet-5", "prompt": "invoice-extraction v7"},
                         next=["m2b"]),
                FlowStep(id="m2b", type="modelcall", name="extract-b",
                         summary="independent blind extraction — different model, same document",
                         config={"model": "gemini-2.5-flash", "prompt": "invoice-extraction v7"},
                         next=["m2c"]),
                FlowStep(id="m2c", type="modelcall", name="extract-c",
                         summary="independent blind extraction — third opinion",
                         config={"model": "claude-haiku-4-5", "prompt": "invoice-extraction v7"},
                         next=["x2"]),
                FlowStep(id="x2", type="transform", name="adjudicate",
                         summary="merges the three candidates field-by-field: majority wins, near-equal amounts agree, and every disagreement is recorded as a conflict for the human",
                         config={"language": "python"}, next=["k1"]),
                FlowStep(id="k1", type="task", name="verify-exemplar",
                         summary="a clerk verifies the adjudicated fields and the proposed exemplar; corrections seed the correction set — the vendor graduates from unknown to known",
                         config={"queue": "ap-clerk"}, next=["a2"]),
                FlowStep(id="a2", type="action", name="save-exemplar",
                         summary="writes the verified exemplar into the knowledge layer — a governed knowledge write, ledgered like any other",
                         config={"target": "knowledge-layer", "gateway": "action-gateway"}, next=["x3"]),
                FlowStep(id="x3", type="transform", name="three-way-match",
                         summary="placeholder — will match invoice ↔ purchase order ↔ receiving record when the PO and receiving feeds land",
                         config={"status": "placeholder"}, next=["x4"]),
                FlowStep(id="x4", type="transform", name="fraud-screen",
                         summary="placeholder — will score bank-detail changes, duplicate invoices, and round-number anomalies",
                         config={"status": "placeholder"}, next=["r2"]),
                FlowStep(id="r2", type="rule", name="post-or-review",
                         summary="under the governed auto-post limit, with a confident classification?",
                         config={"rule_type": "threshold", "source": "knowledge:invoice-posting-policy"},
                         next=["a1", "k2"], branch_labels=["post", "review"]),
                FlowStep(id="k2", type="task", name="review-invoice",
                         summary="a clerk decides the flagged invoice with the extraction and policy findings attached",
                         config={"queue": "ap-clerk"}, next=["a1"]),
                FlowStep(id="a1", type="action", name="post-to-ap",
                         summary="guarded write of the posting into the AP ledger",
                         config={"target": "ap-ledger", "gateway": "action-gateway"}, next=["e1"]),
                FlowStep(id="e1", type="event", name="InvoicePosted",
                         summary="emitted on commit", config={"event": "InvoicePosted"}),
            ],
        ),
    ]


def case_from_run(defn: FlowDefinition, run: RunDetail) -> GoldenCase:
    """Build a golden case out of a real execution — the flywheel in miniature.

    The case captures the trigger payload, the reads the run made (off the
    journal, at their revisions — the pinned world that makes the regression
    run reproducible), and assertions derived from what the run actually did.
    A human refines them in the review screen; this is the starting point.
    """
    amount = _amount_for(run.case_ref)
    raised = run.tasks_raised > 0
    assertions: list[FactAssertion] = []
    for fact in defn.contract.facts:
        if fact.kind == "extraction" and fact.type == "money":
            assertions.append(
                FactAssertion(path=fact.path, expected=f"{amount}.00")
            )
        elif fact.kind == "verdict" and fact.type == "verdict":
            # Only verdict-typed facts take a pass/review label; a string-typed
            # verdict (e.g. which extraction path ran) is not knowable from the
            # summary alone, so it is left for the human to label in review.
            assertions.append(
                FactAssertion(
                    path=fact.path, expected="review" if raised else "pass"
                )
            )
    # The flow's terminal commit — the last Action — is the one the
    # trajectory claim is about.
    action = next((s for s in reversed(defn.steps) if s.type == "action"), None)
    trajectory = [
        TrajectoryAssertion(
            kind="task_raised", expected="true" if raised else "false"
        ),
        TrajectoryAssertion(
            kind="action_committed",
            expected=(
                action.name if action and run.actions_committed else "none"
            ),
        ),
    ]
    reads = [
        PinnedRead(
            ref=f"{defn.id}:context@rev{int(_hex(run.id, 3), 16) % 40 + 1}",
            summary="knowledge-layer records this run read, captured from "
                    "its journal",
        ),
    ]
    return GoldenCase(
        id="",  # assigned by the caller
        name=f"Promoted {run.case_ref}",
        input_ref=run.case_ref,
        expected="matches the recorded outcome",
        source="promoted-from-run",
        run_id=run.id,
        payload=run.steps[0].input_preview if run.steps else "",
        document_ref=f"asset:{run.case_ref}.pdf",
        assertions=assertions,
        trajectory=trajectory,
        read_set=reads,
    )


def _reads(*pairs: tuple[str, str]) -> list[PinnedRead]:
    return [PinnedRead(ref=r, summary=s) for r, s in pairs]


def _facts(*pairs: tuple[str, str]) -> list[FactAssertion]:
    return [FactAssertion(path=p, expected=e) for p, e in pairs]


def _traj(*pairs: tuple[str, str]) -> list[TrajectoryAssertion]:
    return [TrajectoryAssertion(kind=k, expected=e) for k, e in pairs]


# Structured labels for the seeded fixtures, keyed by input_ref. Assertions
# key to contract fact paths (never step ids); reads pin the world the case
# was captured in.
_SEED_CASE_EXTRAS: dict[str, dict] = {
    # ---- expense-reimbursement ----
    "exp-fixture-001": {
        "assertions": _facts(
            ("receipt.vendor", "corner-bistro"),
            ("receipt.date", "2026-08-14"),
            ("receipt.total_usd", "64.20"),
            ("receipt.category", "meals"),
            ("reimbursement.verdict", "approve"),
        ),
        "trajectory": _traj(
            ("task_raised", "false"),
            ("action_committed", "record-reimbursement"),
        ),
        "reads": _reads(
            ("knowledge:expense-approval-policy@rev5", "governed expense policy"),
        ),
    },
    "exp-fixture-004": {
        "assertions": _facts(
            ("receipt.vendor", "harborview-hotel"),
            ("receipt.total_usd", "612.00"),
            ("receipt.category", "lodging"),
            ("reimbursement.verdict", "elevate"),
        ),
        "trajectory": _traj(
            ("task_raised", "true"),
            ("action_committed", "record-reimbursement"),
        ),
        "reads": _reads(
            ("knowledge:expense-approval-policy@rev5", "governed expense policy"),
        ),
    },
    "exp-fixture-007": {
        "assertions": [
            FactAssertion(path="reimbursement.verdict", expected="elevate"),
            FactAssertion(
                path="receipt.total_usd", op="judge",
                expected="elevated rather than guessed",
                rubric="the thermal-paper total is barely legible; a "
                       "low-confidence extraction must elevate, not invent "
                       "a number",
            ),
        ],
        "trajectory": _traj(
            ("task_raised", "true"),
            ("waypoint", "low-confidence extraction elevated"),
        ),
        "reads": _reads(
            ("knowledge:expense-approval-policy@rev5", "governed expense policy"),
        ),
    },
    "exp-fixture-010": {
        "assertions": _facts(
            ("receipt.total_usd", "48.30"),
            ("receipt.category", "mileage"),
            ("reimbursement.verdict", "approve"),
        ),
        "trajectory": _traj(
            ("task_raised", "false"),
            ("action_committed", "record-reimbursement"),
        ),
        "reads": _reads(
            ("knowledge:expense-approval-policy@rev5", "governed expense policy"),
            ("knowledge:mileage-rate@2026", "governed mileage rate"),
        ),
    },
    "exp-fixture-013": {
        "assertions": _facts(
            ("receipt.category", "entertainment"),
            ("reimbursement.verdict", "elevate"),
        ),
        "trajectory": _traj(
            ("task_raised", "true"),
            ("waypoint", "policy §4.2 (alcohol cap) cited"),
        ),
        "reads": _reads(
            ("knowledge:expense-approval-policy@rev5", "governed expense policy"),
        ),
    },
    "exp-fixture-016": {
        "assertions": _facts(("reimbursement.verdict", "elevate")),
        "trajectory": _traj(
            ("task_raised", "true"),
            ("waypoint", "receipt requested"),
        ),
        "reads": _reads(
            ("knowledge:expense-approval-policy@rev5", "governed expense policy"),
        ),
    },
    # ---- accounts-payable ----
    "ap-fixture-001": {
        "assertions": _facts(
            ("invoice.vendor_id", "vendor:acme-industrial"),
            ("invoice.invoice_number", "INV-88412"),
            ("invoice.invoice_date", "2026-08-02"),
            ("invoice.po_number", "PO-4471"),
            ("invoice.total_usd", "1840.00"),
            ("invoice.extraction_path", "one-shot"),
            ("invoice.verdict", "post"),
        ),
        "trajectory": _traj(
            ("task_raised", "false"),
            ("action_committed", "post-to-ap"),
        ),
        "reads": _reads(
            ("knowledge:vendor-exemplar-acme-industrial@rev1",
             "vendor exemplar — one-shot template"),
            ("knowledge:invoice-posting-policy@rev1", "governed posting policy"),
        ),
    },
    "ap-fixture-004": {
        "assertions": _facts(
            ("invoice.vendor_id", "vendor:acme-industrial"),
            ("invoice.total_usd", "6120.00"),
            ("invoice.extraction_path", "one-shot"),
            ("invoice.verdict", "review"),
        ),
        "trajectory": _traj(
            ("task_raised", "true"),
            ("action_committed", "post-to-ap"),
        ),
        "reads": _reads(
            ("knowledge:vendor-exemplar-acme-industrial@rev1",
             "vendor exemplar — one-shot template"),
            ("knowledge:invoice-posting-policy@rev1", "governed posting policy"),
        ),
    },
    "ap-fixture-007": {
        # First contact: no exemplar exists yet, so the pinned world holds the
        # posting policy but deliberately no vendor exemplar.
        "assertions": _facts(
            ("invoice.extraction_path", "ensemble"),
            ("invoice.verdict", "post"),
        ),
        "trajectory": _traj(
            ("task_raised", "true"),
            ("waypoint", "exemplar verified by clerk and saved to the knowledge layer"),
            ("action_committed", "post-to-ap"),
        ),
        "reads": _reads(
            ("knowledge:invoice-posting-policy@rev1", "governed posting policy"),
        ),
    },
    "ap-fixture-010": {
        "assertions": _facts(
            ("invoice.invoice_date", "2026-07-30"),
            ("invoice.extraction_path", "ensemble"),
        ),
        "trajectory": _traj(
            ("task_raised", "true"),
            ("waypoint", "adjudicator picked the ISO date and recorded the conflict"),
        ),
        "reads": _reads(
            ("knowledge:invoice-posting-policy@rev1", "governed posting policy"),
        ),
    },
}


def _seed_golden_cases(flow_id: str) -> list[GoldenCase]:
    # Six cases — deliberately below the coverage recommendation's bar of 8,
    # so the "golden set is thin" insight has something real to point at.
    catalogs: dict[str, list[tuple[str, str, str, GoldenSource]]] = {
        "expense-reimbursement": [
            ("In-policy team lunch, clear receipt", "exp-fixture-001", "total $64.20 · approved automatically", "correction-set"),
            ("Hotel night above the city cap", "exp-fixture-004", "$612/night vs $290 cap · elevated", "correction-set"),
            ("Blurry thermal-paper receipt", "exp-fixture-007", "low extraction confidence · elevated", "exemplar"),
            ("Mileage at the current governed rate", "exp-fixture-010", "auto-approved · rate read from policy", "correction-set"),
            ("Alcohol line item over cap", "exp-fixture-013", "elevated · policy §4.2 cited", "correction-set"),
            ("Missing receipt over $75", "exp-fixture-016", "elevated · receipt requested", "human-authored"),
        ],
        "accounts-payable": [
            ("Known vendor · clean scan", "ap-fixture-001", "one-shot from exemplar · $1,840 posted", "correction-set"),
            ("Known vendor · over the auto-post limit", "ap-fixture-004", "$6,120 · review path", "correction-set"),
            ("Unknown vendor · first contact", "ap-fixture-007", "ensemble ×3 · exemplar verified by clerk", "exemplar"),
            ("Ensemble date-format conflict", "ap-fixture-010", "adjudicator picks ISO · conflict recorded", "correction-set"),
            ("Missing PO number", "ap-fixture-013", "review path · PO lookup suggested", "human-authored"),
            ("Duplicate of a posted invoice", "ap-fixture-016", "flagged · no Action committed", "human-authored"),
        ],
    }
    out: list[GoldenCase] = []
    for i, (name, ref, expected, source) in enumerate(catalogs.get(flow_id, [])):
        extras = _SEED_CASE_EXTRAS.get(ref, {})
        out.append(
            GoldenCase(
                id=f"gc-{i + 1:03d}",
                name=name,
                input_ref=ref,
                expected=expected,
                source=source,
                added_by="system:seed",
                document_ref=f"asset:{ref}.pdf",
                assertions=extras.get("assertions", []),
                trajectory=extras.get("trajectory", []),
                read_set=extras.get("reads", []),
            )
        )
    return out


# Definitions that seed an open draft, and how that draft differs from the
# active version — an added node plus a modified one, so the canvas diff
# overlay has real indicators to paint.
_SEED_DRAFTS: dict[str, str] = {
    "expense-reimbursement": "add-duplicate-check",
}


def _seed_version_history(defn: FlowDefinition) -> list[FlowVersion]:
    """Two archived revisions, the active one, and sometimes an open draft."""
    active_steps = [s.model_copy(deep=True) for s in defn.steps]
    versions: list[FlowVersion] = []

    # Archived revisions differ only in their governed knowledge (an older
    # prompt), which is the most common real edit and keeps the diff legible.
    for back in (2, 1):
        older = []
        for step in active_steps:
            copy = step.model_copy(deep=True)
            if copy.type == "modelcall" and "prompt" in copy.config:
                name, _, revision = copy.config["prompt"].rpartition(" v")
                if revision.isdigit():
                    config = dict(copy.config)
                    config["prompt"] = f"{name} v{max(1, int(revision) - back)}"
                    copy = copy.model_copy(update={"config": config})
            older.append(copy)
        versions.append(
            FlowVersion(
                version=defn.version - back,
                label=f"v{defn.version - back}",
                status="archived",
                steps=older,
                model_budget_usd_per_run=defn.model_budget_usd_per_run,
                notes="Superseded.",
                authored_by=defn.authored_by,
                created_at=defn.updated_at - timedelta(days=30 * back),
            )
        )

    versions.append(
        FlowVersion(
            version=defn.version,
            label=f"v{defn.version}",
            status="active",
            steps=active_steps,
            model_budget_usd_per_run=defn.model_budget_usd_per_run,
            notes="Live.",
            authored_by=defn.authored_by,
            created_at=defn.updated_at,
        )
    )

    recipe = _SEED_DRAFTS.get(defn.id)
    if recipe is None:
        return versions

    draft_steps = [s.model_copy(deep=True) for s in active_steps]
    # add-duplicate-check: a new deterministic guard in front of the model
    # call, so the trigger is modified (its `next` moves) and the guard
    # itself is added.
    draft_steps = [
        s.model_copy(update={"next": ["x0"]}) if s.id == "t1" else s
        for s in draft_steps
    ]
    guard = FlowStep(
        id="x0", type="transform", name="duplicate-check",
        summary="rejects a receipt already reimbursed, before any model runs",
        config={"language": "python"}, next=["m1"],
    )
    draft_steps.insert(1, guard)
    notes = "Adds a deterministic duplicate guard ahead of extraction."

    versions.append(
        FlowVersion(
            version=defn.version + 1,
            label=f"v{defn.version + 1}-draft",
            status="draft",
            steps=draft_steps,
            model_budget_usd_per_run=defn.model_budget_usd_per_run,
            notes=notes,
            authored_by=defn.authored_by,
            created_at=utcnow() - timedelta(days=2),
        )
    )
    return versions


def seed_flows_if_empty(service: FlowService) -> int:
    """Register the demo fleet when the registry is empty. Idempotent."""
    if service.list():
        return 0
    created = 0
    for defn in _seed_definitions():
        try:
            service.create(
                defn.model_copy(update={"versions": _seed_version_history(defn)})
            )
            cases = _seed_golden_cases(defn.id)
            if cases:
                service.golden_sets.put(
                    GoldenSet(
                        flow_id=defn.id,
                        cases=cases,
                        version=1,
                        revisions=[
                            GoldenSetRevision(
                                version=1,
                                at=utcnow() - timedelta(hours=2),
                                by="cumulus eval push",
                                note="seeded from the repository export",
                                case_count=len(cases),
                            )
                        ],
                        # FDEs author golden cases in code as well as in the
                        # console; naming the file keeps the console honest
                        # about not being the only source.
                        synced_from=f"server/tests/golden/{defn.id}.json",
                        synced_at=utcnow() - timedelta(hours=2),
                    )
                )
            created += 1
        except FlowExistsError:
            continue
    if created:
        log.info("Seeded flow definitions", extra={"count": created})
    return created
