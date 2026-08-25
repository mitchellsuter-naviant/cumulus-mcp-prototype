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


class GoldenCase(BaseModel):
    id: str
    name: str
    input_ref: str = ""
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


class GoldenSet(BaseModel):
    flow_id: str
    cases: list[GoldenCase] = Field(default_factory=list)
    # Where the seeded half of the set comes from in the repository. The
    # golden set is governed knowledge that FDEs also author in code, so the
    # console states which file it was seeded from rather than pretending the
    # UI is the only source.
    synced_from: str = ""
    synced_at: datetime | None = None


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

    @property
    def regressed(self) -> bool:
        return self.baseline_passed is True and not self.passed


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
        return self.golden_sets.put(golden)

    def remove_golden_case(self, flow_id: str, case_id: str) -> GoldenSet:
        golden = self.golden_set(flow_id)
        remaining = [c for c in golden.cases if c.id != case_id]
        if len(remaining) == len(golden.cases):
            raise ValueError(f"Golden case not found: {case_id}")
        golden.cases = remaining
        return self.golden_sets.put(golden)

    # ---- test harness ----

    def launch_test_run(
        self,
        flow_id: str,
        candidate_version: str,
        baseline_version: str,
        launched_by: str,
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

    def _grade(
        self, defn: FlowDefinition, case: GoldenCase, version: str, factor: float
    ) -> tuple[bool, float, int]:
        """Deterministic verdict for one (case, version) pair.

        Keyed by version, so the same candidate always reproduces the same
        result — the property any eval worth trusting has to have — while a
        different candidate genuinely can diverge.
        """
        rng = _h(f"{defn.id}:{case.id}:{version}")
        base = {
            "correction-set": 0.94,
            "exemplar": 0.84,
            "human-authored": 0.90,
            "promoted-from-run": 0.92,
        }
        passed = rng.random() < base.get(case.source, 0.9)
        cost = round(defn.profile.cost_per_run * rng.uniform(0.5, 1.1) * factor, 3)
        # A cheaper model is also a faster one, roughly; enough to make the
        # latency column move in the direction the cost column moves.
        duration = int(rng.uniform(1200, 6500) * (0.75 + 0.25 * factor))
        return passed, cost, duration

    def _execute_test_run(
        self, test_id: str, defn: FlowDefinition, cases: list[GoldenCase]
    ) -> None:
        """Simulated harness execution: case-by-case, writes suppressed.

        Each case is graded against BOTH the candidate and the baseline, so
        the comparison is like-for-like rather than candidate-versus-
        remembered-numbers, and a regression is visible per case.
        """
        run = self.test_runs.get(test_id)
        if run is None:
            return
        try:
            candidate_factor = self._version_cost_factor(defn, run.candidate_version)
            baseline_factor = self._version_cost_factor(defn, run.baseline_version)
            for case in cases:
                time.sleep(self.test_seconds_per_case)
                passed, cost, duration = self._grade(
                    defn, case, run.candidate_version, candidate_factor
                )
                base_passed, base_cost, base_duration = self._grade(
                    defn, case, run.baseline_version, baseline_factor
                )
                run.results.append(
                    TestCaseResult(
                        case_id=case.id,
                        name=case.name,
                        passed=passed,
                        expected=case.expected,
                        actual=case.expected if passed else (
                            "diverged: " + (
                                "took the automatic path where a Task was expected"
                                if "Task" in case.expected
                                else "extracted value did not match the golden answer"
                            )
                        ),
                        cost_usd=cost,
                        duration_ms=duration,
                        baseline_passed=base_passed,
                        baseline_cost_usd=base_cost,
                        baseline_duration_ms=base_duration,
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
                done = len(run.results)
                run.latency_delta_ms = int(
                    sum(r.duration_ms - r.baseline_duration_ms for r in run.results)
                    / max(1, done)
                )
                self.test_runs.put(run)
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
            id="invoice-intake",
            name="Invoice intake",
            description=(
                "Extracts, validates and routes vendor invoices; pauses for AP "
                "judgment when a Rule says the amount needs review."
            ),
            team="finance-ops",
            status="active",
            version=14,
            tags=["ap", "extraction", "erp"],
            queues=["ap-clerk"],
            workspace="ap-workspace",
            roi_minutes_per_run=8,
            model_budget_usd_per_run=0.35,
            authored_by="fde:m.suter",
            created_at=_ago(160),
            updated_at=_ago(12),
            profile=SeedProfile(
                base_runs=52, cost_per_run=0.22, tokens_per_run=5200,
                task_rate=0.071, escalate_rate=0.004, fail_rate=0.003,
                active_seconds=42, case_prefix="inv",
            ),
            steps=[
                FlowStep(
                    id="t1", type="trigger", name="InvoiceReceived",
                    summary="starts one run per invoice landed by the mail-intake flow",
                    config={"event": "InvoiceReceived", "source": "mail-intake"},
                    next=["m1"],
                ),
                FlowStep(
                    id="m1", type="modelcall", name="extract-invoice",
                    summary="extracts amount, vendor, PO and line items from the document",
                    config={"model": "claude-sonnet-5", "prompt": "invoice-extraction v9"},
                    next=["r1"],
                ),
                FlowStep(
                    id="r1", type="rule", name="amount-threshold",
                    summary="is the amount over the $10,000 review threshold?",
                    config={"rule_type": "threshold", "threshold": "$10,000"},
                    next=["a1", "k1"], branch_labels=["pass", "review"],
                ),
                FlowStep(
                    id="k1", type="task", name="confirm-extraction",
                    summary="a person confirms or corrects the extraction; the run suspends in place",
                    config={"queue": "ap-clerk"},
                    next=["a1"],
                ),
                FlowStep(
                    id="a1", type="action", name="post-to-erp",
                    summary="guarded write of the approved invoice to the ERP",
                    config={"target": "erp", "gateway": "action-gateway"},
                    next=["e1"],
                ),
                FlowStep(
                    id="e1", type="event", name="InvoicePosted",
                    summary="emitted on commit; corrections flow back decoupled via ExtractionCorrected",
                    config={"event": "InvoicePosted"},
                ),
            ],
        ),
        FlowDefinition(
            id="expense-triage",
            name="Expense triage",
            description=(
                "One ModelCall policy check on each expense report; approves "
                "in-policy spend and raises a Task for the rest."
            ),
            team="finance-ops",
            status="active",
            version=9,
            tags=["expenses", "policy"],
            queues=["expense-review"],
            workspace="ap-workspace",
            roi_minutes_per_run=5,
            model_budget_usd_per_run=0.20,
            authored_by="fde:m.suter",
            created_at=_ago(120),
            updated_at=_ago(30),
            profile=SeedProfile(
                base_runs=45, cost_per_run=0.12, tokens_per_run=2600,
                task_rate=0.11, escalate_rate=0.002, fail_rate=0.002,
                active_seconds=18, case_prefix="exp",
            ),
            steps=[
                FlowStep(
                    id="t1", type="trigger", name="ExpenseSubmitted",
                    summary="one run per submitted report",
                    config={"event": "ExpenseSubmitted"}, next=["m1"],
                ),
                FlowStep(
                    id="m1", type="modelcall", name="policy-check",
                    summary="checks the report against the expense policy in the knowledge layer",
                    config={"model": "claude-haiku-4-5", "prompt": "expense-policy v5"},
                    next=["r1"],
                ),
                FlowStep(
                    id="r1", type="rule", name="policy-verdict",
                    summary="in policy, and under the manager-approval limit?",
                    config={"rule_type": "match-condition"},
                    next=["a1", "k1"], branch_labels=["approve", "review"],
                ),
                FlowStep(
                    id="k1", type="task", name="review-expense",
                    summary="a reviewer decides the flagged report with the model's findings attached",
                    config={"queue": "expense-review"}, next=["a1"],
                ),
                FlowStep(
                    id="a1", type="action", name="record-decision",
                    summary="guarded write of the approval or rejection",
                    config={"target": "expense-system"}, next=["e1"],
                ),
                FlowStep(
                    id="e1", type="event", name="ExpenseDecided",
                    summary="emitted on commit", config={"event": "ExpenseDecided"},
                ),
            ],
        ),
        FlowDefinition(
            id="contract-review",
            name="Contract review",
            description=(
                "Multi-model pipeline on inbound contracts: clause extraction, "
                "then an independent risk review, then legal judgment on anything "
                "over the risk threshold."
            ),
            team="legal",
            status="active",
            version=6,
            tags=["contracts", "risk", "multi-model"],
            queues=["legal-review"],
            workspace="deal-room",
            roi_minutes_per_run=45,
            model_budget_usd_per_run=4.00,
            authored_by="fde:k.osei",
            created_at=_ago(95),
            updated_at=_ago(5),
            profile=SeedProfile(
                # Counterparty paper is genuinely hard: enough escalations and
                # hard failures to sit below the fleet's success bar.
                base_runs=7, cost_per_run=2.40, tokens_per_run=48000,
                task_rate=0.42, escalate_rate=0.045, fail_rate=0.03,
                active_seconds=210, case_prefix="ctr",
            ),
            steps=[
                FlowStep(
                    id="t1", type="trigger", name="ContractUploaded",
                    summary="one run per contract landing in the deal room",
                    config={"event": "ContractUploaded"}, next=["m1"],
                ),
                FlowStep(
                    id="m1", type="modelcall", name="clause-extraction",
                    summary="extracts and classifies every clause against the playbook ontology",
                    config={"model": "claude-sonnet-5", "prompt": "clause-extraction v11"},
                    next=["x1"],
                ),
                FlowStep(
                    id="x1", type="transform", name="clause-normalizer",
                    summary="maps extracted clauses onto canonical clause Objects and Links",
                    config={"language": "python"}, next=["m2"],
                ),
                FlowStep(
                    id="m2", type="modelcall", name="risk-review",
                    summary="independent second model scores deviation from the standard terms",
                    config={"model": "claude-opus-5", "prompt": "risk-review v4"},
                    next=["r1"],
                ),
                FlowStep(
                    id="r1", type="rule", name="risk-threshold",
                    summary="is any clause scored above the escalation threshold?",
                    config={"rule_type": "threshold", "threshold": "risk ≥ 0.6"},
                    next=["a1", "k1"], branch_labels=["standard", "escalate"],
                ),
                FlowStep(
                    id="k1", type="task", name="legal-review",
                    summary="counsel reviews the flagged clauses with both models' reasoning attached",
                    config={"queue": "legal-review"}, next=["a1"],
                ),
                FlowStep(
                    id="a1", type="action", name="file-review-summary",
                    summary="guarded write of the review summary into the deal room",
                    config={"target": "deal-room"}, next=["e1"],
                ),
                FlowStep(
                    id="e1", type="event", name="ContractReviewed",
                    summary="emitted on commit", config={"event": "ContractReviewed"},
                ),
            ],
        ),
        FlowDefinition(
            id="deal-brief",
            name="Deal brief",
            description=(
                "Synthesizes a deal-room briefing from CRM state, correspondence "
                "and the knowledge layer whenever a deal changes stage."
            ),
            team="sales-ops",
            status="draft",
            version=2,
            tags=["sales", "synthesis"],
            queues=[],
            workspace="deal-room",
            roi_minutes_per_run=25,
            model_budget_usd_per_run=1.50,
            authored_by="fde:k.osei",
            created_at=_ago(18),
            updated_at=_ago(2),
            profile=SeedProfile(
                base_runs=0.8, cost_per_run=1.10, tokens_per_run=26000,
                task_rate=0.0, escalate_rate=0.0, fail_rate=0.05,
                active_seconds=95, case_prefix="deal",
            ),
            steps=[
                FlowStep(
                    id="t1", type="trigger", name="DealStageChanged",
                    summary="fires when a deal moves stage in the CRM",
                    config={"event": "DealStageChanged"}, next=["x1"],
                ),
                FlowStep(
                    id="x1", type="transform", name="gather-context",
                    summary="pulls the deal Object, its Links, and related knowledge assets",
                    config={"language": "python"}, next=["m1"],
                ),
                FlowStep(
                    id="m1", type="modelcall", name="synthesize-brief",
                    summary="writes the briefing with citations into the graph",
                    config={"model": "claude-opus-5", "prompt": "deal-brief v2"},
                    next=["a1"],
                ),
                FlowStep(
                    id="a1", type="action", name="post-to-workspace",
                    summary="guarded write of the brief into the Deal Room workspace",
                    config={"target": "deal-room"},
                ),
            ],
        ),
        FlowDefinition(
            id="vendor-master-sync",
            name="Vendor master sync",
            description=(
                "Keeps the canonical vendor list in agreement with the ERP: lands "
                "changes, entity-matches them to canonical Objects, and resolves "
                "claim conflicts by precedence."
            ),
            team="data-platform",
            status="active",
            version=11,
            tags=["mdm", "sync", "erp"],
            queues=["data-steward"],
            workspace="",
            roi_minutes_per_run=2,
            model_budget_usd_per_run=0.25,
            authored_by="fde:r.nakamura",
            created_at=_ago(140),
            updated_at=_ago(21),
            profile=SeedProfile(
                base_runs=22, cost_per_run=0.11, tokens_per_run=2200,
                task_rate=0.05, escalate_rate=0.002, fail_rate=0.002,
                weekend_factor=1.0, active_seconds=25, case_prefix="batch",
            ),
            steps=[
                FlowStep(
                    id="t1", type="trigger", name="every-2h",
                    summary="windowing timer event through the standard Trigger mechanism",
                    config={"event": "timer:2h"}, next=["x1"],
                ),
                FlowStep(
                    id="x1", type="transform", name="land-erp-changes",
                    summary="lands the change feed as bulk observations with per-revision lineage",
                    config={"language": "python", "transport": "change-feed"},
                    next=["m1"],
                ),
                FlowStep(
                    id="m1", type="modelcall", name="match-assist",
                    summary="scores ambiguous vendor-name matches the deterministic keys cannot settle",
                    config={"model": "claude-haiku-4-5"}, next=["r1"],
                ),
                FlowStep(
                    id="r1", type="rule", name="match-confidence",
                    summary="accept the identity Link automatically above 0.85 confidence?",
                    config={"rule_type": "threshold", "threshold": "0.85"},
                    next=["a1", "k1"], branch_labels=["accept", "review"],
                ),
                FlowStep(
                    id="k1", type="task", name="confirm-match",
                    summary="a data steward confirms low-confidence merges; reversal info retained",
                    config={"queue": "data-steward"}, next=["a1"],
                ),
                FlowStep(
                    id="a1", type="action", name="upsert-canonical",
                    summary="resolution writes: winning claims recorded with why; losers retained",
                    config={"target": "graph"}, next=["e1"],
                ),
                FlowStep(
                    id="e1", type="event", name="VendorMasterReconciled",
                    summary="summarizing event per window — never per record",
                    config={"event": "VendorMasterReconciled"},
                ),
            ],
        ),
        FlowDefinition(
            id="document-embedder",
            name="Document embedder",
            description=(
                "Parses, chunks and embeds landed documents into the knowledge "
                "layer; derived vectors carry their source-plus-recipe derivation record."
            ),
            team="data-platform",
            status="active",
            version=8,
            tags=["knowledge", "embeddings"],
            queues=[],
            workspace="",
            roi_minutes_per_run=1,
            model_budget_usd_per_run=0.30,
            authored_by="fde:r.nakamura",
            created_at=_ago(130),
            updated_at=_ago(44),
            profile=SeedProfile(
                base_runs=10, cost_per_run=0.19, tokens_per_run=9500,
                task_rate=0.0, escalate_rate=0.0, fail_rate=0.006,
                weekend_factor=1.0, active_seconds=60, case_prefix="doc",
            ),
            steps=[
                FlowStep(
                    id="t1", type="trigger", name="DocumentLanded",
                    summary="event-triggered on each landed document",
                    config={"event": "DocumentLanded"}, next=["x1"],
                ),
                FlowStep(
                    id="x1", type="transform", name="parse-and-chunk",
                    summary="extracts text and splits it on the chunking policy in the Mapping",
                    config={"language": "python"}, next=["m1"],
                ),
                FlowStep(
                    id="m1", type="modelcall", name="summarize-for-index",
                    summary="writes the retrieval summary and tags for each chunk",
                    config={"model": "claude-haiku-4-5"}, next=["a1"],
                ),
                FlowStep(
                    id="a1", type="action", name="register-derived-dataset",
                    summary="records the derived vectors with {from, fromVersion, recipe}",
                    config={"target": "knowledge-layer"}, next=["e1"],
                ),
                FlowStep(
                    id="e1", type="event", name="DocumentIndexed",
                    summary="emitted per document", config={"event": "DocumentIndexed"},
                ),
            ],
        ),
    ]


def _seed_golden_cases(flow_id: str) -> list[GoldenCase]:
    catalogs: dict[str, list[tuple[str, str, str, GoldenSource]]] = {
        "invoice-intake": [
            ("Standard invoice · single PO", "inv-fixture-001", "amount $2,340 · vendor acme-industrial · automatic path", "correction-set"),
            ("Multi-page scan · rotated", "inv-fixture-007", "amount $18,905 · review path (over threshold)", "correction-set"),
            ("Unknown vendor · first contact", "inv-fixture-011", "Task raised · vendor profile seeded", "exemplar"),
            ("Credit memo (negative amount)", "inv-fixture-014", "amount −$1,120 · no ERP post", "correction-set"),
            ("Duplicate of a posted invoice", "inv-fixture-019", "flagged duplicate · no Action committed", "human-authored"),
            ("Handwritten total, printed lines", "inv-fixture-022", "amount $4,010 · automatic path", "correction-set"),
            ("Currency mismatch (EUR invoice)", "inv-fixture-025", "amount €3,300 converted · review path", "correction-set"),
            ("Missing PO number", "inv-fixture-028", "Task raised · PO lookup suggested", "exemplar"),
        ],
        "expense-triage": [
            ("In-policy meal, receipts attached", "exp-fixture-001", "approved automatically", "correction-set"),
            ("Alcohol line item over cap", "exp-fixture-004", "Task raised · policy §4.2 cited", "correction-set"),
            ("Missing receipt over $75", "exp-fixture-009", "Task raised · receipt requested", "human-authored"),
            ("Mileage at the current rate", "exp-fixture-012", "approved automatically", "correction-set"),
            ("Split personal/business hotel", "exp-fixture-015", "Task raised · split proposed", "exemplar"),
        ],
        "contract-review": [
            ("Standard MSA, no deviations", "ctr-fixture-001", "risk < 0.6 · standard path", "correction-set"),
            ("Uncapped liability clause", "ctr-fixture-003", "Task raised · clause 8 flagged ≥ 0.6", "correction-set"),
            ("Non-standard IP assignment", "ctr-fixture-006", "Task raised · clause 11 flagged", "human-authored"),
            ("Auto-renewal buried in annex", "ctr-fixture-009", "clause extracted · renewal Link created", "exemplar"),
            ("Redlined counterparty paper", "ctr-fixture-012", "Task raised · 3 clauses flagged", "correction-set"),
            ("Order form referencing master", "ctr-fixture-013", "risk < 0.6 · linked to master Object", "correction-set"),
        ],
        "vendor-master-sync": [
            ("Exact tax-id match", "vm-fixture-001", "identity Link confidence 1.0 · auto-accept", "correction-set"),
            ("Fuzzy name, same address", "vm-fixture-004", "confidence 0.78 · Task raised for steward", "correction-set"),
            ("Merged vendor un-merge", "vm-fixture-007", "reversal info applied · claims retained", "human-authored"),
            ("Conflicting credit limits", "vm-fixture-010", "precedence rule resolves to ERP claim", "correction-set"),
        ],
        "document-embedder": [
            ("Markdown policy document", "doc-fixture-001", "12 chunks · summary and tags written", "correction-set"),
            ("Scanned PDF, OCR path", "doc-fixture-003", "text extracted · 9 chunks", "correction-set"),
            ("Empty file", "doc-fixture-005", "no chunks · DocumentIndexed with count 0", "human-authored"),
        ],
    }
    return [
        GoldenCase(
            id=f"gc-{i + 1:03d}",
            name=name,
            input_ref=ref,
            expected=expected,
            source=source,
            added_by="system:seed",
        )
        for i, (name, ref, expected, source) in enumerate(catalogs.get(flow_id, []))
    ]


# Definitions that seed an open draft, and how that draft differs from the
# active version. Enough to exercise every diff indicator on the canvas:
# a modified node, an added node, and a removed one.
_SEED_DRAFTS: dict[str, str] = {
    "invoice-intake": "add-duplicate-check",
    "contract-review": "cheaper-risk-review",
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
    if recipe == "add-duplicate-check":
        # A new deterministic guard in front of the model call, so the trigger
        # is modified (its `next` moves) and the guard itself is added.
        draft_steps = [
            s.model_copy(update={"next": ["x0"]}) if s.id == "t1" else s
            for s in draft_steps
        ]
        guard = FlowStep(
            id="x0", type="transform", name="duplicate-check",
            summary="rejects an invoice already posted, before any model runs",
            config={"language": "python"}, next=["m1"],
        )
        draft_steps.insert(1, guard)
        notes = "Adds a deterministic duplicate guard ahead of extraction."
    else:
        # Swap the second-opinion model for a cheaper one and drop the
        # normalizer that the extraction step now does inline.
        draft_steps = [
            s.model_copy(update={"config": {**s.config, "model": "claude-sonnet-5"}})
            if s.id == "m2"
            else s
            for s in draft_steps
        ]
        draft_steps = [
            s.model_copy(update={"next": ["m2"]}) if s.id == "m1" else s
            for s in draft_steps
            if s.id != "x1"
        ]
        notes = "Cheaper second-opinion model; normalizer folded into extraction."

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
