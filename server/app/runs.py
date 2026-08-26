"""Durable Flow runs: a replayed journal, not a Python call stack.

What was here before was honest but thin: a flow ran synchronously inside the
POST that triggered it, "suspending" meant writing a Task and returning, and
resuming meant a second function that hand-appended the remaining journal
entries. That works for exactly one round of human judgment and cannot survive
a restart, which rules out everything the two flows now need — correct an
extraction, re-run the checks, escalate again, drive to finished.

The model here is the standard durable one, kept as small as it can be:

- A **Run** is stored state. Its journal of completed steps *is* the run; the
  Python frame is disposable.
- A **step** is memoized by id. Re-entering the flow body replays completed
  steps from the journal instead of re-executing them, so re-running a body is
  cheap and side-effect-free up to the point it last stopped. This is why
  object ids in `graph.py` are derived from natural keys: a replayed step must
  mint the same entity, not a second one.
- A **gate** is a step that needs a person. First pass raises the Task and
  suspends; once the Task is decided, the same gate returns the decision and
  execution continues past it. Several gates in one flow work by construction,
  which is the whole point.
- **Resume** re-invokes the registered body with the stored inputs. Nothing
  outside a step may be non-deterministic, and steps are where all the
  non-determinism (model calls, clock reads, id minting) is required to live.

A suspended run holds no compute — it is a document with a cursor. Restarting
the process loses nothing, because nothing was in memory that mattered.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, Field

from .models import utcnow
from .tasks import Task, TaskDecision, TaskService

log = logging.getLogger("cumulus.runs")

RunStatus = Literal["running", "suspended", "succeeded", "failed"]
StepStatus = Literal["done", "suspended", "failed"]

# Step kinds, matching the vocabulary the Tier-2 export and the canvas use.
StepKind = Literal[
    "trigger", "modelcall", "transform", "rule", "task", "action", "event",
    "sync", "check", "resolve",
]


class RunSuspended(Exception):
    """Raised by a gate with no decision yet. Not an error — a pause."""

    def __init__(self, task_id: str, step_id: str) -> None:
        super().__init__(f"run suspended at {step_id} on task {task_id}")
        self.task_id = task_id
        self.step_id = step_id


class StepFailed(Exception):
    """A step exhausted its retries. The run fails or escalates."""

    def __init__(self, step_id: str, message: str) -> None:
        super().__init__(f"{step_id}: {message}")
        self.step_id = step_id
        self.message = message


class StepRecord(BaseModel):
    step_id: str
    name: str
    kind: str
    status: StepStatus = "done"
    summary: str = ""
    # Whatever the step returned, JSON-shaped. Replay hands this back instead
    # of calling the step again, so it has to survive a round trip through
    # storage — which is a useful constraint: a step that cannot serialize its
    # result is a step doing too much.
    output: Any = None
    # Knowledge and graph records this step read, at the revision it read them.
    refs: list[str] = Field(default_factory=list)
    # What this step *wrote*: object, link, document and knowledge refs. Runs
    # have always recorded what they read; without this they cannot say what
    # they changed, which is the half an auditor actually asks about — and it
    # is what lets a view replay the graph assembling itself step by step.
    writes: list[str] = Field(default_factory=list)
    error: str = ""
    attempts: int = 1
    at_iso: str = Field(default_factory=lambda: utcnow().isoformat())
    duration_ms: int = 0
    # Set on gate steps: the Task raised, and the decision that released it.
    task_id: str = ""
    decision: dict | None = None
    # Model accounting, on modelcall steps — the governed operation.
    model: str = ""
    cost_usd: float = 0.0


class RunRecord(BaseModel):
    id: str
    flow_id: str
    status: RunStatus = "running"
    case_ref: str = ""
    actor: str = ""
    inputs: dict = Field(default_factory=dict)
    outputs: dict = Field(default_factory=dict)
    steps: list[StepRecord] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list)
    open_task_id: str = ""
    suspended_at_step: str = ""
    resume_count: int = 0
    error: str = ""
    model_calls: int = 0
    cost_usd: float = 0.0
    started_at_iso: str = Field(default_factory=lambda: utcnow().isoformat())
    updated_at_iso: str = Field(default_factory=lambda: utcnow().isoformat())
    finished_at_iso: str = ""

    def step(self, step_id: str) -> StepRecord | None:
        return next((s for s in self.steps if s.step_id == step_id), None)

    def journal(self) -> list[dict]:
        """The shape the console's run-trace reads.

        Deliberately a projection rather than a second stored copy: there is
        one record of what happened, and the journal is a view of it.
        """
        return [
            {
                "step_id": s.step_id,
                "name": s.name,
                "kind": s.kind,
                "summary": s.summary,
                "at_iso": s.at_iso,
            }
            for s in self.steps
        ]

    def duration_ms(self) -> int:
        return sum(s.duration_ms for s in self.steps)

    def writes(self) -> list[str]:
        """Everything this run wrote, in the order the steps wrote it."""
        out: list[str] = []
        for step in self.steps:
            for ref in step.writes:
                if ref not in out:
                    out.append(ref)
        return out


class RunStore(Protocol):
    def list(self) -> list[RunRecord]: ...
    def get(self, run_id: str) -> RunRecord | None: ...
    def put(self, record: RunRecord) -> RunRecord: ...


class InMemoryRunStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, RunRecord] = {}

    def list(self) -> list[RunRecord]:
        with self._lock:
            return list(self._items.values())

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._items.get(run_id)

    def put(self, record: RunRecord) -> RunRecord:
        with self._lock:
            self._items[record.id] = record
            return record


class FirestoreRunStore:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore  # deferred: not needed for local dev

        self._client = firestore.Client(project=project)
        self._col = self._client.collection(collection)

    def list(self) -> list[RunRecord]:
        out: list[RunRecord] = []
        for doc in self._col.stream():
            try:
                out.append(RunRecord.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping unparseable run", extra={"docId": doc.id})
        return out

    def get(self, run_id: str) -> RunRecord | None:
        snap = self._col.document(run_id).get()
        if not snap.exists:
            return None
        try:
            return RunRecord.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable run", extra={"runId": run_id})
            return None

    def put(self, record: RunRecord) -> RunRecord:
        self._col.document(record.id).set(record.model_dump(mode="json"))
        return record


# ---------------------------------------------------------------------------
# The context handed to a flow body
# ---------------------------------------------------------------------------


class RunContext:
    """What a flow body is allowed to do.

    Every method that can have an effect goes through a step, because a step is
    the unit of replay. A body that computes outside a step is fine as long as
    it is deterministic; a body that *acts* outside one will act twice.
    """

    def __init__(
        self,
        record: RunRecord,
        store: RunStore,
        tasks: TaskService,
        resume_input: dict | None = None,
    ) -> None:
        self.record = record
        self._store = store
        self._tasks = tasks
        self.resume_input = resume_input or {}
        # Steps replayed on this pass, for the log line at the end.
        self.replayed = 0

    # -- steps --------------------------------------------------------------

    def step(
        self,
        step_id: str,
        name: str,
        kind: StepKind,
        fn: Callable[[], Any] | None = None,
        *,
        summary: str | Callable[[Any], str] = "",
        refs: list[str] | None = None,
        model: str = "",
        cost_usd: float = 0.0,
        retries: int = 0,
        escalate_queue: str = "",
        writes: Callable[[Any], list[str]] | None = None,
    ) -> Any:
        """Run a step once, ever. On replay, hand back what it returned.

        `retries` is the doctrine's retry-then-escalate: a transient failure is
        retried in place, and a persistent one either fails the run or (with
        `escalate_queue`) raises an exception Task carrying the error, which is
        the honest way to hand a broken step to a person.

        `writes` is called with the step's output *after* a successful `fn`
        and returns the refs the step wrote. A callable rather than a value
        because the write-set does not exist until the work is done, and it
        takes the output rather than closing over it because the assignment
        the caller is in the middle of has not happened yet — typically it
        reads the accumulated refs off a `graph.GraphWriter` the step wrote
        through.
        """
        existing = self.record.step(step_id)
        if existing is not None and existing.status == "done":
            self.replayed += 1
            return existing.output

        started = time.monotonic()
        attempts = 0
        last_error = ""
        output: Any = None
        succeeded = False
        while attempts <= retries and not succeeded:
            attempts += 1
            try:
                output = fn() if fn is not None else None
                succeeded = True
            except Exception as exc:  # noqa: BLE001 — recorded, then re-raised
                last_error = f"{type(exc).__name__}: {exc}"
                log.warning(
                    "Step attempt failed",
                    extra={
                        "runId": self.record.id, "stepId": step_id,
                        "attempt": attempts, "error": last_error,
                    },
                )

        if not succeeded:
            self._record_step(StepRecord(
                step_id=step_id, name=name, kind=kind, status="failed",
                summary=f"failed after {attempts} attempt(s): {last_error}",
                error=last_error, attempts=attempts,
                duration_ms=int((time.monotonic() - started) * 1000),
                refs=refs or [],
            ))
            if escalate_queue:
                task = self._tasks.create(Task(
                    id=f"task_{self.record.id}_{step_id}",
                    kind="exception",
                    title=f"{self.record.flow_id}: {name} failed",
                    summary=(
                        f"The step failed {attempts} time(s) and escalated "
                        f"rather than retrying further. Error: {last_error}"
                    ),
                    queue=escalate_queue,
                    priority="high",
                    flow_id=self.record.flow_id,
                    run_id=self.record.id,
                    case_ref=self.record.case_ref,
                    resumes=f"this run resumes at {step_id}",
                    created_at_iso=utcnow().isoformat(),
                ))
                raise RunSuspended(task.id, step_id)
            raise StepFailed(step_id, last_error)

        text = summary(output) if callable(summary) else summary
        self._record_step(StepRecord(
            step_id=step_id, name=name, kind=kind, status="done",
            summary=text, output=output, refs=refs or [], attempts=attempts,
            duration_ms=int((time.monotonic() - started) * 1000),
            model=model, cost_usd=cost_usd,
            writes=list(writes(output) or []) if writes is not None else [],
        ))
        return output

    def event(self, step_id: str, name: str, summary: str, kind: StepKind = "event") -> None:
        """A journal entry with no output — an emitted Event, a trigger."""
        if self.record.step(step_id) is not None:
            self.replayed += 1
            return
        self._record_step(StepRecord(
            step_id=step_id, name=name, kind=kind, status="done", summary=summary,
        ))

    # -- gates --------------------------------------------------------------

    def gate(
        self,
        step_id: str,
        name: str,
        build_task: Callable[[], Task],
    ) -> TaskDecision:
        """Suspend for human judgment; return the decision once it exists.

        Three states, one call site: no Task yet (raise one and suspend), Task
        open (suspend again — a resume that arrives early is harmless), Task
        decided (record it and fall through). The flow body reads as straight
        line code either way, which is the property that makes several gates in
        one flow tractable.
        """
        existing = self.record.step(step_id)
        if existing is not None and existing.status == "done" and existing.decision:
            self.replayed += 1
            return TaskDecision.model_validate(existing.decision)

        if existing is not None and existing.task_id:
            task = self._tasks.get(existing.task_id)
            if task is not None and task.decision is not None:
                existing.status = "done"
                existing.decision = task.decision.model_dump(mode="json")
                existing.summary = (
                    f"resumed — {task.decision.verdict} by "
                    f"{task.decision.decided_by}"
                )
                existing.at_iso = utcnow().isoformat()
                self._persist()
                return task.decision
            raise RunSuspended(existing.task_id, step_id)

        task = build_task()
        task.run_id = self.record.id
        # A gate has to be able to find its own Task again on the next pass, so
        # the id cannot be optional. Flows normally set a meaningful one; when
        # one is missing the engine derives it from the run and the step, which
        # is unique by construction and keeps a forgetful flow author from
        # producing a run that can never be resumed.
        if not task.id:
            task.id = f"task_{self.record.id}_{step_id}"
        created = self._tasks.create(task)
        self._record_step(StepRecord(
            step_id=step_id, name=name, kind="task", status="suspended",
            summary=f"suspended — task {created.id} raised to {created.queue}",
            task_id=created.id,
        ))
        raise RunSuspended(created.id, step_id)

    # -- read-set -----------------------------------------------------------

    def note_refs(self, *refs: str) -> None:
        for ref in refs:
            if ref and ref not in self.record.refs:
                self.record.refs.append(ref)

    # -- internals ----------------------------------------------------------

    def _record_step(self, step: StepRecord) -> None:
        previous = self.record.step(step.step_id)
        if previous is not None:
            self.record.steps[self.record.steps.index(previous)] = step
        else:
            self.record.steps.append(step)
        if step.kind == "modelcall" and step.status == "done":
            self.record.model_calls += 1
            self.record.cost_usd = round(self.record.cost_usd + step.cost_usd, 6)
        self.note_refs(*step.refs)
        self._persist()

    def _persist(self) -> None:
        self.record.updated_at_iso = utcnow().isoformat()
        self._store.put(self.record)


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

FlowBody = Callable[[RunContext, dict], dict]


class RunEngine:
    """Starts runs, resumes them, and owns nothing else.

    Bodies register by flow id so a resume needs no caller knowledge: given a
    stored run, the engine can always find the code that produced it. That is
    also the constraint that keeps a flow body a pure function of its stored
    inputs.
    """

    def __init__(self, store: RunStore, tasks: TaskService) -> None:
        self.store = store
        self.tasks = tasks
        self._bodies: dict[str, FlowBody] = {}

    def register(self, flow_id: str, body: FlowBody) -> None:
        self._bodies[flow_id] = body

    # -- reads --------------------------------------------------------------

    def list(
        self,
        flow_id: str | None = None,
        status: RunStatus | None = None,
        limit: int | None = None,
    ) -> list[RunRecord]:
        runs = self.store.list()
        if flow_id is not None:
            runs = [r for r in runs if r.flow_id == flow_id]
        if status is not None:
            runs = [r for r in runs if r.status == status]
        runs.sort(key=lambda r: r.started_at_iso, reverse=True)
        return runs[:limit] if limit else runs

    def get(self, run_id: str) -> RunRecord | None:
        return self.store.get(run_id)

    def find_by_case(self, case_ref: str) -> RunRecord | None:
        matches = [r for r in self.store.list() if r.case_ref == case_ref]
        matches.sort(key=lambda r: r.started_at_iso, reverse=True)
        return matches[0] if matches else None

    def find_by_task(self, task_id: str) -> RunRecord | None:
        for run in self.store.list():
            if run.open_task_id == task_id:
                return run
            if any(s.task_id == task_id for s in run.steps):
                return run
        return None

    # -- writes -------------------------------------------------------------

    def start(
        self,
        flow_id: str,
        inputs: dict,
        *,
        case_ref: str = "",
        actor: str = "",
        run_id: str | None = None,
    ) -> RunRecord:
        body = self._bodies.get(flow_id)
        if body is None:
            raise ValueError(f"No registered body for flow {flow_id}")
        rid = run_id or self.mint_run_id(flow_id, inputs, actor)
        record = RunRecord(
            id=rid, flow_id=flow_id, case_ref=case_ref or rid,
            actor=actor, inputs=inputs,
        )
        self.store.put(record)
        return self._drive(record, body, resume_input=None)

    def resume(self, run_id: str, resume_input: dict | None = None) -> RunRecord | None:
        record = self.store.get(run_id)
        if record is None:
            return None
        if record.status in ("succeeded", "failed"):
            return record
        body = self._bodies.get(record.flow_id)
        if body is None:
            log.warning(
                "Cannot resume: no registered body",
                extra={"runId": run_id, "flowId": record.flow_id},
            )
            return record
        record.status = "running"
        record.resume_count += 1
        record.open_task_id = ""
        record.suspended_at_step = ""
        self.store.put(record)
        return self._drive(record, body, resume_input=resume_input)

    def resume_for_task(
        self, task_id: str, resume_input: dict | None = None
    ) -> RunRecord | None:
        run = self.find_by_task(task_id)
        return self.resume(run.id, resume_input) if run else None

    # -- the loop -----------------------------------------------------------

    def _drive(
        self, record: RunRecord, body: FlowBody, resume_input: dict | None
    ) -> RunRecord:
        ctx = RunContext(record, self.store, self.tasks, resume_input)
        try:
            outputs = body(ctx, record.inputs) or {}
            record.status = "succeeded"
            record.outputs = outputs
            record.finished_at_iso = utcnow().isoformat()
        except RunSuspended as suspended:
            record.status = "suspended"
            record.open_task_id = suspended.task_id
            record.suspended_at_step = suspended.step_id
        except StepFailed as failed:
            record.status = "failed"
            record.error = failed.message
            record.suspended_at_step = failed.step_id
            record.finished_at_iso = utcnow().isoformat()
        except Exception as exc:  # noqa: BLE001 — a body bug must not 500 silently
            log.exception("Flow body raised", extra={"runId": record.id})
            record.status = "failed"
            record.error = f"{type(exc).__name__}: {exc}"
            record.finished_at_iso = utcnow().isoformat()
        record.updated_at_iso = utcnow().isoformat()
        self.store.put(record)
        log.info(
            "Run driven",
            extra={
                "runId": record.id, "flowId": record.flow_id,
                "status": record.status, "steps": len(record.steps),
                "replayed": ctx.replayed, "resumeCount": record.resume_count,
            },
        )
        return record

    @staticmethod
    def mint_run_id(flow_id: str, inputs: dict, actor: str) -> str:
        prefix = "".join(part[:1] for part in flow_id.split("-"))[:3] or "run"
        digest = hashlib.sha256(
            f"{flow_id}:{actor}:{sorted(inputs.keys())}:{utcnow().isoformat()}".encode()
        ).hexdigest()[:10]
        return f"run_{prefix}_{digest}"


def build_run_engine(
    backend: str, project: str | None, collection: str, tasks: TaskService
) -> RunEngine:
    store: RunStore
    if backend == "firestore":
        store = FirestoreRunStore(project, collection)
    else:
        store = InMemoryRunStore()
    return RunEngine(store, tasks)
