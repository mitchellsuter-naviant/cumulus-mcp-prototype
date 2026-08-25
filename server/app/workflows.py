"""Workflows & DataFlows: the registry and observability plane for authored logic.

Doctrine grounding (DOCTRINE.md, Lens 2 and the Governance spine):

  * There is deliberately **no "Agent" primitive** — the Workflow or DataFlow
    *is* the agent. This module therefore manages the two kinds of authored
    logic side by side: a Workflow (business layer, one run per case, may
    suspend for a human) and a DataFlow (data/knowledge layer, stream/batch).
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

log = logging.getLogger("cumulus.workflows")

WorkflowKind = Literal["workflow", "dataflow"]
WorkflowStatus = Literal["draft", "active", "paused", "deprecated"]
# The step vocabulary of the declarative export. `transform` is authored
# non-model logic (parse, reshape, entity-match) carried as a faithful
# plain-language description — implementation stays machinery.
StepType = Literal["trigger", "modelcall", "rule", "task", "action", "event", "transform"]
GoldenSource = Literal["correction-set", "exemplar", "human-authored"]
RunStatus = Literal["completed", "waiting_on_human", "escalated", "failed"]

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


class WorkflowStep(BaseModel):
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


class WorkflowDefinition(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]*$")
    name: str = ""
    kind: WorkflowKind = "workflow"
    description: str = ""
    team: str = ""
    status: WorkflowStatus = "draft"
    version: int = 1
    tags: list[str] = Field(default_factory=list)
    steps: list[WorkflowStep] = Field(default_factory=list)
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


class GoldenCase(BaseModel):
    id: str
    name: str
    input_ref: str = ""
    expected: str = ""
    source: GoldenSource = "correction-set"
    added_by: str = ""
    added_at: datetime = Field(default_factory=utcnow)


class GoldenSet(BaseModel):
    workflow_id: str
    cases: list[GoldenCase] = Field(default_factory=list)


class TestCaseResult(BaseModel):
    case_id: str
    name: str
    passed: bool
    expected: str = ""
    actual: str = ""
    cost_usd: float = 0.0
    duration_ms: int = 0


class TestRun(BaseModel):
    id: str
    workflow_id: str
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
    # Doctrinal constant: the harness never commits an Action. Kept on the
    # record so the audit trail states it rather than implying it.
    writes_suppressed: bool = True


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


class RunSummary(BaseModel):
    id: str
    workflow_id: str
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


class WorkflowExistsError(Exception):
    pass


class WorkflowNotFoundError(Exception):
    pass


class WorkflowDirectory(Protocol):
    def list(self) -> list[WorkflowDefinition]: ...
    def get(self, workflow_id: str) -> WorkflowDefinition | None: ...
    def create(self, defn: WorkflowDefinition) -> WorkflowDefinition: ...
    def update(self, defn: WorkflowDefinition) -> WorkflowDefinition: ...
    def delete(self, workflow_id: str) -> None: ...


class GoldenSetDirectory(Protocol):
    def get(self, workflow_id: str) -> GoldenSet | None: ...
    def put(self, golden: GoldenSet) -> GoldenSet: ...
    def delete(self, workflow_id: str) -> None: ...


class TestRunDirectory(Protocol):
    def list_for(self, workflow_id: str) -> list[TestRun]: ...
    def get(self, test_id: str) -> TestRun | None: ...
    def put(self, run: TestRun) -> TestRun: ...


class InMemoryWorkflowDirectory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, WorkflowDefinition] = {}

    def list(self) -> list[WorkflowDefinition]:
        with self._lock:
            return sorted(self._items.values(), key=lambda w: w.id)

    def get(self, workflow_id: str) -> WorkflowDefinition | None:
        with self._lock:
            return self._items.get(workflow_id)

    def create(self, defn: WorkflowDefinition) -> WorkflowDefinition:
        with self._lock:
            if defn.id in self._items:
                raise WorkflowExistsError(defn.id)
            self._items[defn.id] = defn
            return defn

    def update(self, defn: WorkflowDefinition) -> WorkflowDefinition:
        with self._lock:
            if defn.id not in self._items:
                raise WorkflowNotFoundError(defn.id)
            self._items[defn.id] = defn
            return defn

    def delete(self, workflow_id: str) -> None:
        with self._lock:
            if workflow_id not in self._items:
                raise WorkflowNotFoundError(workflow_id)
            del self._items[workflow_id]


class InMemoryGoldenSetDirectory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, GoldenSet] = {}

    def get(self, workflow_id: str) -> GoldenSet | None:
        with self._lock:
            return self._items.get(workflow_id)

    def put(self, golden: GoldenSet) -> GoldenSet:
        with self._lock:
            self._items[golden.workflow_id] = golden
            return golden

    def delete(self, workflow_id: str) -> None:
        with self._lock:
            self._items.pop(workflow_id, None)


class InMemoryTestRunDirectory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, TestRun] = {}

    def list_for(self, workflow_id: str) -> list[TestRun]:
        with self._lock:
            runs = [r for r in self._items.values() if r.workflow_id == workflow_id]
        return sorted(runs, key=lambda r: r.launched_at, reverse=True)

    def get(self, test_id: str) -> TestRun | None:
        with self._lock:
            return self._items.get(test_id)

    def put(self, run: TestRun) -> TestRun:
        with self._lock:
            self._items[run.id] = run
            return run


class FirestoreWorkflowDirectory:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore  # deferred: not needed for local dev

        self._client = firestore.Client(project=project)
        self._col = self._client.collection(collection)

    def list(self) -> list[WorkflowDefinition]:
        out = []
        for doc in self._col.stream():
            try:
                out.append(WorkflowDefinition.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping unparseable workflow", extra={"docId": doc.id})
        return sorted(out, key=lambda w: w.id)

    def get(self, workflow_id: str) -> WorkflowDefinition | None:
        snap = self._col.document(workflow_id).get()
        if not snap.exists:
            return None
        try:
            return WorkflowDefinition.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable workflow", extra={"workflowId": workflow_id})
            return None

    def create(self, defn: WorkflowDefinition) -> WorkflowDefinition:
        ref = self._col.document(defn.id)
        if ref.get().exists:
            raise WorkflowExistsError(defn.id)
        ref.set(defn.model_dump(mode="json"))
        return defn

    def update(self, defn: WorkflowDefinition) -> WorkflowDefinition:
        ref = self._col.document(defn.id)
        if not ref.get().exists:
            raise WorkflowNotFoundError(defn.id)
        ref.set(defn.model_dump(mode="json"))
        return defn

    def delete(self, workflow_id: str) -> None:
        ref = self._col.document(workflow_id)
        if not ref.get().exists:
            raise WorkflowNotFoundError(workflow_id)
        ref.delete()


class FirestoreGoldenSetDirectory:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore

        self._client = firestore.Client(project=project)
        self._col = self._client.collection(collection)

    def get(self, workflow_id: str) -> GoldenSet | None:
        snap = self._col.document(workflow_id).get()
        if not snap.exists:
            return None
        try:
            return GoldenSet.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable golden set", extra={"workflowId": workflow_id})
            return None

    def put(self, golden: GoldenSet) -> GoldenSet:
        self._col.document(golden.workflow_id).set(golden.model_dump(mode="json"))
        return golden

    def delete(self, workflow_id: str) -> None:
        self._col.document(workflow_id).delete()


class FirestoreTestRunDirectory:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore

        self._client = firestore.Client(project=project)
        self._col = self._client.collection(collection)

    def list_for(self, workflow_id: str) -> list[TestRun]:
        out = []
        for doc in self._col.where("workflow_id", "==", workflow_id).stream():
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


def _day_usage(defn: WorkflowDefinition, day: date) -> UsageDay:
    """Deterministic usage for one workflow on one day.

    Keyed by (workflow id, day) so every restart, instance, and backend agrees
    on history. Drafts and days before creation produce zeroes.
    """
    created = defn.created_at.date()
    if day < created:
        return UsageDay(date=day)
    rng = _h(f"{defn.id}:{day.isoformat()}")
    p = defn.profile
    age = (day - created).days
    base = p.base_runs * (1.0 + p.growth_per_day * age)
    if day.weekday() >= 5 and defn.kind == "workflow":
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


def usage_series(defn: WorkflowDefinition, days: int) -> list[UsageDay]:
    today = utcnow().date()
    return [_day_usage(defn, today - timedelta(days=d)) for d in range(days - 1, -1, -1)]


def open_tasks(defn: WorkflowDefinition) -> int:
    """Tasks currently sitting in this definition's queues, deterministic per day."""
    if defn.status != "active":
        return 0
    rng = _h(f"{defn.id}:open:{utcnow().date().isoformat()}")
    backlog = defn.profile.base_runs * defn.profile.task_rate * rng.uniform(0.3, 1.1)
    return int(round(backlog))


def _run_id(defn: WorkflowDefinition, day: date, idx: int) -> str:
    return "run_" + _hex(f"{defn.id}:{day.isoformat()}:{idx}", 6)


def _run_summary(defn: WorkflowDefinition, day: date, idx: int, runs_that_day: int) -> RunSummary:
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
        workflow_id=defn.id,
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


def recent_runs(defn: WorkflowDefinition, limit: int) -> list[RunSummary]:
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


def find_run(defn: WorkflowDefinition, run_id: str) -> RunDetail | None:
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


def _run_detail(defn: WorkflowDefinition, summary: RunSummary) -> RunDetail:
    rng = _h(f"{summary.id}:detail")
    steps: list[RunStepEvent] = []
    calls: list[ModelCallRecord] = []
    at = summary.started_at
    tokens_in = tokens_out = 0
    modelcall_steps = [s for s in defn.steps if s.type == "modelcall"] or [None]
    cost_share = summary.cost_usd / len([s for s in modelcall_steps if s])
    raised = summary.tasks_raised > 0
    stopped = False

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
                    detail=f"{summary.case_ref} · {step.summary or 'event binding matched'}",
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
            calls.append(
                ModelCallRecord(
                    step_id=step.id, step_name=step.name, model=model,
                    input_tokens=cin, output_tokens=cout,
                    cost_usd=round(cost_share, 3), duration_ms=dur,
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
                )
            )
        elif step.type == "rule":
            verdict = "review" if raised else "pass"
            steps.append(
                RunStepEvent(
                    step_id=step.id, type=step.type, name=step.name,
                    status="completed", at=at, duration_ms=int(rng.uniform(1, 8)),
                    verdict=verdict,
                    detail=f"pure check · inputs recorded · {step.summary}",
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
                )
            )
        elif step.type in ("event", "transform"):
            steps.append(
                RunStepEvent(
                    step_id=step.id, type=step.type, name=step.name,
                    status="completed", at=at, duration_ms=dur,
                    detail=step.summary,
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


def audit_entries(defn: WorkflowDefinition, limit: int) -> list[AuditEntry]:
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
                        at=ev.at, kind="action", actor=f"workflow:{defn.id}",
                        summary=f"{ev.name} committed via Action Gateway ({run.case_ref})",
                        run_id=run.id, ledger_ref=ev.ledger_ref,
                    )
                )
        if defn.kind == "dataflow" and run.status == "completed":
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
# Service
# ---------------------------------------------------------------------------


class WorkflowService:
    def __init__(
        self,
        workflows: WorkflowDirectory,
        golden_sets: GoldenSetDirectory,
        test_runs: TestRunDirectory,
        # Seconds the simulated harness spends per golden case; tests shrink it.
        test_seconds_per_case: float = 0.15,
    ) -> None:
        self.workflows = workflows
        self.golden_sets = golden_sets
        self.test_runs = test_runs
        self.test_seconds_per_case = test_seconds_per_case

    # ---- registry ----

    def list(self) -> list[WorkflowDefinition]:
        return self.workflows.list()

    def get(self, workflow_id: str) -> WorkflowDefinition | None:
        return self.workflows.get(workflow_id)

    def require(self, workflow_id: str) -> WorkflowDefinition:
        defn = self.workflows.get(workflow_id)
        if defn is None:
            raise WorkflowNotFoundError(workflow_id)
        return defn

    def create(self, defn: WorkflowDefinition) -> WorkflowDefinition:
        return self.workflows.create(defn)

    def update(self, defn: WorkflowDefinition) -> WorkflowDefinition:
        return self.workflows.update(
            defn.model_copy(update={"updated_at": utcnow()})
        )

    def delete(self, workflow_id: str) -> None:
        self.workflows.delete(workflow_id)
        self.golden_sets.delete(workflow_id)

    # ---- golden set ----

    def golden_set(self, workflow_id: str) -> GoldenSet:
        return self.golden_sets.get(workflow_id) or GoldenSet(workflow_id=workflow_id)

    def add_golden_case(self, workflow_id: str, case: GoldenCase) -> GoldenSet:
        golden = self.golden_set(workflow_id)
        if any(c.id == case.id for c in golden.cases):
            raise ValueError(f"Golden case already exists: {case.id}")
        golden.cases.append(case)
        return self.golden_sets.put(golden)

    def remove_golden_case(self, workflow_id: str, case_id: str) -> GoldenSet:
        golden = self.golden_set(workflow_id)
        remaining = [c for c in golden.cases if c.id != case_id]
        if len(remaining) == len(golden.cases):
            raise ValueError(f"Golden case not found: {case_id}")
        golden.cases = remaining
        return self.golden_sets.put(golden)

    # ---- test harness ----

    def launch_test_run(
        self,
        workflow_id: str,
        candidate_version: str,
        baseline_version: str,
        launched_by: str,
    ) -> TestRun:
        defn = self.require(workflow_id)
        golden = self.golden_set(workflow_id)
        if not golden.cases:
            raise ValueError(
                "No golden set: add cases before launching a test run."
            )
        run = TestRun(
            id="test_" + _hex(f"{workflow_id}:{time.time_ns()}", 10),
            workflow_id=workflow_id,
            candidate_version=candidate_version or f"v{defn.version + 1}-draft",
            baseline_version=baseline_version or f"v{defn.version}",
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

    def _execute_test_run(
        self, test_id: str, defn: WorkflowDefinition, cases: list[GoldenCase]
    ) -> None:
        """Simulated harness execution: case-by-case, writes suppressed.

        Grading is deterministic per (case, candidate version) so re-running
        the same candidate reproduces the same result — which is also what a
        real eval against pinned inputs should do.
        """
        run = self.test_runs.get(test_id)
        if run is None:
            return
        try:
            for case in cases:
                time.sleep(self.test_seconds_per_case)
                rng = _h(f"{defn.id}:{case.id}:{run.candidate_version}")
                base = {"correction-set": 0.94, "exemplar": 0.84, "human-authored": 0.90}
                passed = rng.random() < base.get(case.source, 0.9)
                cost = round(defn.profile.cost_per_run * rng.uniform(0.5, 1.1), 3)
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
                        duration_ms=int(rng.uniform(1200, 6500)),
                    )
                )
                run.passed = sum(1 for r in run.results if r.passed)
                run.cost_usd = round(sum(r.cost_usd for r in run.results), 2)
                self.test_runs.put(run)
            run.status = "completed"
            run.finished_at = utcnow()
            self.test_runs.put(run)
            log.info(
                "Test run completed",
                extra={
                    "workflowId": defn.id, "testId": run.id,
                    "passed": run.passed, "total": run.total_cases,
                },
            )
        except Exception:
            log.exception("Test run execution failed", extra={"testId": test_id})


def build_workflow_service(
    backend: str,
    project: str | None,
    workflows_collection: str,
    golden_sets_collection: str,
    test_runs_collection: str,
) -> WorkflowService:
    if backend == "firestore":
        return WorkflowService(
            FirestoreWorkflowDirectory(project, workflows_collection),
            FirestoreGoldenSetDirectory(project, golden_sets_collection),
            FirestoreTestRunDirectory(project, test_runs_collection),
        )
    return WorkflowService(
        InMemoryWorkflowDirectory(),
        InMemoryGoldenSetDirectory(),
        InMemoryTestRunDirectory(),
    )


# ---------------------------------------------------------------------------
# Seed fleet
# ---------------------------------------------------------------------------


def _ago(days: int) -> datetime:
    return utcnow() - timedelta(days=days)


def _seed_definitions() -> list[WorkflowDefinition]:
    return [
        WorkflowDefinition(
            id="invoice-intake",
            name="Invoice intake",
            kind="workflow",
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
                WorkflowStep(
                    id="t1", type="trigger", name="InvoiceReceived",
                    summary="starts one run per invoice landed by the mail-intake DataFlow",
                    config={"event": "InvoiceReceived", "source": "mail-intake"},
                    next=["m1"],
                ),
                WorkflowStep(
                    id="m1", type="modelcall", name="extract-invoice",
                    summary="extracts amount, vendor, PO and line items from the document",
                    config={"model": "claude-sonnet-5", "prompt": "invoice-extraction v9"},
                    next=["r1"],
                ),
                WorkflowStep(
                    id="r1", type="rule", name="amount-threshold",
                    summary="is the amount over the $10,000 review threshold?",
                    config={"rule_type": "threshold", "threshold": "$10,000"},
                    next=["a1", "k1"], branch_labels=["pass", "review"],
                ),
                WorkflowStep(
                    id="k1", type="task", name="confirm-extraction",
                    summary="a person confirms or corrects the extraction; the run suspends in place",
                    config={"queue": "ap-clerk"},
                    next=["a1"],
                ),
                WorkflowStep(
                    id="a1", type="action", name="post-to-erp",
                    summary="guarded write of the approved invoice to the ERP",
                    config={"target": "erp", "gateway": "action-gateway"},
                    next=["e1"],
                ),
                WorkflowStep(
                    id="e1", type="event", name="InvoicePosted",
                    summary="emitted on commit; corrections flow back decoupled via ExtractionCorrected",
                    config={"event": "InvoicePosted"},
                ),
            ],
        ),
        WorkflowDefinition(
            id="expense-triage",
            name="Expense triage",
            kind="workflow",
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
                WorkflowStep(
                    id="t1", type="trigger", name="ExpenseSubmitted",
                    summary="one run per submitted report",
                    config={"event": "ExpenseSubmitted"}, next=["m1"],
                ),
                WorkflowStep(
                    id="m1", type="modelcall", name="policy-check",
                    summary="checks the report against the expense policy in the knowledge layer",
                    config={"model": "claude-haiku-4-5", "prompt": "expense-policy v5"},
                    next=["r1"],
                ),
                WorkflowStep(
                    id="r1", type="rule", name="policy-verdict",
                    summary="in policy, and under the manager-approval limit?",
                    config={"rule_type": "match-condition"},
                    next=["a1", "k1"], branch_labels=["approve", "review"],
                ),
                WorkflowStep(
                    id="k1", type="task", name="review-expense",
                    summary="a reviewer decides the flagged report with the model's findings attached",
                    config={"queue": "expense-review"}, next=["a1"],
                ),
                WorkflowStep(
                    id="a1", type="action", name="record-decision",
                    summary="guarded write of the approval or rejection",
                    config={"target": "expense-system"}, next=["e1"],
                ),
                WorkflowStep(
                    id="e1", type="event", name="ExpenseDecided",
                    summary="emitted on commit", config={"event": "ExpenseDecided"},
                ),
            ],
        ),
        WorkflowDefinition(
            id="contract-review",
            name="Contract review",
            kind="workflow",
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
                WorkflowStep(
                    id="t1", type="trigger", name="ContractUploaded",
                    summary="one run per contract landing in the deal room",
                    config={"event": "ContractUploaded"}, next=["m1"],
                ),
                WorkflowStep(
                    id="m1", type="modelcall", name="clause-extraction",
                    summary="extracts and classifies every clause against the playbook ontology",
                    config={"model": "claude-sonnet-5", "prompt": "clause-extraction v11"},
                    next=["x1"],
                ),
                WorkflowStep(
                    id="x1", type="transform", name="clause-normalizer",
                    summary="maps extracted clauses onto canonical clause Objects and Links",
                    config={"language": "python"}, next=["m2"],
                ),
                WorkflowStep(
                    id="m2", type="modelcall", name="risk-review",
                    summary="independent second model scores deviation from the standard terms",
                    config={"model": "claude-opus-5", "prompt": "risk-review v4"},
                    next=["r1"],
                ),
                WorkflowStep(
                    id="r1", type="rule", name="risk-threshold",
                    summary="is any clause scored above the escalation threshold?",
                    config={"rule_type": "threshold", "threshold": "risk ≥ 0.6"},
                    next=["a1", "k1"], branch_labels=["standard", "escalate"],
                ),
                WorkflowStep(
                    id="k1", type="task", name="legal-review",
                    summary="counsel reviews the flagged clauses with both models' reasoning attached",
                    config={"queue": "legal-review"}, next=["a1"],
                ),
                WorkflowStep(
                    id="a1", type="action", name="file-review-summary",
                    summary="guarded write of the review summary into the deal room",
                    config={"target": "deal-room"}, next=["e1"],
                ),
                WorkflowStep(
                    id="e1", type="event", name="ContractReviewed",
                    summary="emitted on commit", config={"event": "ContractReviewed"},
                ),
            ],
        ),
        WorkflowDefinition(
            id="deal-brief",
            name="Deal brief",
            kind="workflow",
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
                WorkflowStep(
                    id="t1", type="trigger", name="DealStageChanged",
                    summary="fires when a deal moves stage in the CRM",
                    config={"event": "DealStageChanged"}, next=["x1"],
                ),
                WorkflowStep(
                    id="x1", type="transform", name="gather-context",
                    summary="pulls the deal Object, its Links, and related knowledge assets",
                    config={"language": "python"}, next=["m1"],
                ),
                WorkflowStep(
                    id="m1", type="modelcall", name="synthesize-brief",
                    summary="writes the briefing with citations into the graph",
                    config={"model": "claude-opus-5", "prompt": "deal-brief v2"},
                    next=["a1"],
                ),
                WorkflowStep(
                    id="a1", type="action", name="post-to-workspace",
                    summary="guarded write of the brief into the Deal Room workspace",
                    config={"target": "deal-room"},
                ),
            ],
        ),
        WorkflowDefinition(
            id="vendor-master-sync",
            name="Vendor master sync",
            kind="dataflow",
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
                WorkflowStep(
                    id="t1", type="trigger", name="every-2h",
                    summary="windowing timer event through the standard Trigger mechanism",
                    config={"event": "timer:2h"}, next=["x1"],
                ),
                WorkflowStep(
                    id="x1", type="transform", name="land-erp-changes",
                    summary="lands the change feed as bulk observations with per-revision lineage",
                    config={"language": "python", "transport": "change-feed"},
                    next=["m1"],
                ),
                WorkflowStep(
                    id="m1", type="modelcall", name="match-assist",
                    summary="scores ambiguous vendor-name matches the deterministic keys cannot settle",
                    config={"model": "claude-haiku-4-5"}, next=["r1"],
                ),
                WorkflowStep(
                    id="r1", type="rule", name="match-confidence",
                    summary="accept the identity Link automatically above 0.85 confidence?",
                    config={"rule_type": "threshold", "threshold": "0.85"},
                    next=["a1", "k1"], branch_labels=["accept", "review"],
                ),
                WorkflowStep(
                    id="k1", type="task", name="confirm-match",
                    summary="a data steward confirms low-confidence merges; reversal info retained",
                    config={"queue": "data-steward"}, next=["a1"],
                ),
                WorkflowStep(
                    id="a1", type="action", name="upsert-canonical",
                    summary="resolution writes: winning claims recorded with why; losers retained",
                    config={"target": "graph"}, next=["e1"],
                ),
                WorkflowStep(
                    id="e1", type="event", name="VendorMasterReconciled",
                    summary="summarizing event per window — never per record",
                    config={"event": "VendorMasterReconciled"},
                ),
            ],
        ),
        WorkflowDefinition(
            id="document-embedder",
            name="Document embedder",
            kind="dataflow",
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
                WorkflowStep(
                    id="t1", type="trigger", name="DocumentLanded",
                    summary="event-triggered on each landed document",
                    config={"event": "DocumentLanded"}, next=["x1"],
                ),
                WorkflowStep(
                    id="x1", type="transform", name="parse-and-chunk",
                    summary="extracts text and splits it on the chunking policy in the Mapping",
                    config={"language": "python"}, next=["m1"],
                ),
                WorkflowStep(
                    id="m1", type="modelcall", name="summarize-for-index",
                    summary="writes the retrieval summary and tags for each chunk",
                    config={"model": "claude-haiku-4-5"}, next=["a1"],
                ),
                WorkflowStep(
                    id="a1", type="action", name="register-derived-dataset",
                    summary="records the derived vectors with {from, fromVersion, recipe}",
                    config={"target": "knowledge-layer"}, next=["e1"],
                ),
                WorkflowStep(
                    id="e1", type="event", name="DocumentIndexed",
                    summary="emitted per document", config={"event": "DocumentIndexed"},
                ),
            ],
        ),
    ]


def _seed_golden_cases(workflow_id: str) -> list[GoldenCase]:
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
        for i, (name, ref, expected, source) in enumerate(catalogs.get(workflow_id, []))
    ]


def seed_workflows_if_empty(service: WorkflowService) -> int:
    """Register the demo fleet when the registry is empty. Idempotent."""
    if service.list():
        return 0
    created = 0
    for defn in _seed_definitions():
        try:
            service.create(defn)
            cases = _seed_golden_cases(defn.id)
            if cases:
                service.golden_sets.put(GoldenSet(workflow_id=defn.id, cases=cases))
            created += 1
        except WorkflowExistsError:
            continue
    if created:
        log.info("Seeded workflow definitions", extra={"count": created})
    return created
