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

from .auth import Principal
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


#: Status codes worth trying again. Everything else in the 4xx range is the
#: server telling us the *request* is wrong, and repeating it verbatim earns
#: the same answer — 408 and 429 are the two that mean "not now" rather than
#: "not ever".
_RETRYABLE_CLIENT_CODES = frozenset({408, 429})


def retryable(exc: Exception) -> bool:
    """Whether trying this again could plausibly produce a different answer.

    Retry-then-escalate is doctrine, but retrying a *deterministic* failure is
    only a slower way to fail: a PDF sent to Vertex labelled `image/jpeg`
    earned `400 Provided image is not valid` twice, at two calls' latency, and
    told the reviewer nothing the first attempt had not.

    Duck-typed on purpose. This module knows nothing about media types or
    Vertex and should not: an exception declares itself permanent by carrying
    `retryable = False`, and anything with an HTTP-shaped `code` is classified
    the way HTTP already says to. Everything else stays retryable, because the
    fail-safe direction here is to try again.
    """
    declared = getattr(exc, "retryable", None)
    if declared is not None:
        return bool(declared)
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(code, int) and 400 <= code < 500:
        return code in _RETRYABLE_CLIENT_CODES
    return True


class ModelUsage(BaseModel):
    """What one model call actually consumed, as the caller can report it.

    Its own small type, and it lives here because `StepRecord` is what it
    fills. Three facts travel together or none of them are trustworthy: a
    token count with no model id cannot be priced, and a model id with no
    tokens is a label. `cost_usd` is left to whoever knows the rate — the
    reader that made the call rarely does — and zero means *unpriced*, never
    cheap, which is the rule the fields below already state.
    """

    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def tokens(self) -> int:
        """Both directions, because that is the shape `StepRecord` keeps.

        The split is worth carrying this far — the two sides price
        differently — and worth collapsing at the journal, which is a record
        of what a run did rather than an invoice.
        """
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "ModelUsage") -> "ModelUsage":
        """Sum two turns of one call. The model id has to agree or it is lost.

        A planner answering one question takes several turns, and the fleet
        counts *questions*, not turns — so the turns are summed before they
        reach a step. Summing across two different models would produce a
        token count attributable to neither, so the id only survives when both
        sides agree; the empty left-hand side of an accumulator is the normal
        case and adopts whatever the first turn reported.
        """
        model = self.model or other.model
        if self.model and other.model and self.model != other.model:
            model = "mixed"
        return ModelUsage(
            model=model,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cost_usd=round(self.cost_usd + other.cost_usd, 6),
        )

    @classmethod
    def from_vertex(cls, model: str, response: Any) -> "ModelUsage":
        """Read `usage_metadata` off a google-genai response.

        Duck-typed rather than imported: every Vertex reader in the system
        discards the same two fields today, and one place that knows their
        names is better than three that each learn them separately. A response
        without the block reports zero, which is `cost_basis`'s `unrecorded`
        and not a claim that the call was free.
        """
        meta = getattr(response, "usage_metadata", None)
        return cls(
            model=model,
            input_tokens=int(getattr(meta, "prompt_token_count", 0) or 0),
            output_tokens=int(getattr(meta, "candidates_token_count", 0) or 0),
        )


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
    # Model accounting, on modelcall steps — the governed operation. These
    # are the *only* record of what a model call cost: the rollups the console
    # shows sum these fields and nothing else, so a call site that leaves them
    # at zero produces a zero on the page. That is the intended behaviour —
    # an unpriced call must read as unpriced, never as cheap — and it is why
    # `flows.cost_basis` counts how many calls carried a price rather than
    # only summing what they carried.
    model: str = ""
    cost_usd: float = 0.0
    tokens: int = 0


class RunRecord(BaseModel):
    id: str
    flow_id: str
    status: RunStatus = "running"
    case_ref: str = ""
    actor: str = ""
    # The Event whose Triggers started this run, named by the Dispatcher
    # before the run existed. Empty means nobody said why it began — a direct
    # `engine.start` — which is a thing to be able to see, not to hide.
    caused_by: str = ""
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
        principal: Principal | None = None,
        emit: Callable[[str, str, RunRecord], Any] | None = None,
    ) -> None:
        self.record = record
        self._store = store
        self._tasks = tasks
        self._emit = emit
        self.resume_input = resume_input or {}
        # Who drove *this pass* of the run, when a live caller drove it: the
        # human whose Task decision released the gate. It travels rather than
        # being looked up from `decided_by` afterwards, because re-deriving
        # authority from a name recorded earlier is a boundary check wearing a
        # write check's clothes — and it would lose `impersonated_by`, which
        # is the one thing an audit reader cannot reconstruct. None on a start
        # or a seeded resume; the Flow commits on its own authority then.
        self.principal = principal
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
        tokens: int = 0,
        retries: int = 0,
        escalate_queue: str = "",
        writes: Callable[[Any], list[str]] | None = None,
        meter: Callable[[Any], ModelUsage | None] | None = None,
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

        `meter` is `writes` for model accounting, and it exists for the same
        reason: the token counts do not exist until the call has been made,
        and the frame that made it is several below the one declaring the
        step. A `ModelUsage` it returns is authoritative for all three of
        `model`/`tokens`/`cost_usd` together — the alternative is a step whose
        model id came from one place and whose token count came from another,
        which is how a backend name ("vertex") ends up filed as a model. It
        returns None when there was nothing to meter, and then the literals
        below stand. A replayed step never calls it: replay returns above,
        which is what keeps a resumed run from counting one model call twice.
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
                permanent = not retryable(exc)
                log.warning(
                    "Step attempt failed",
                    extra={
                        "runId": self.record.id, "stepId": step_id,
                        "attempt": attempts, "error": last_error,
                        "permanent": permanent,
                    },
                )
                if permanent:
                    # Escalate now. The retry budget exists for a flaky
                    # network, not for a request the provider has already
                    # judged malformed.
                    break

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
                # The failed step has to name the Task it escalated into.
                # Without this the journal records the failure and loses the
                # handoff, so nothing can get from "this step broke" to "and
                # here is the person it was given to" — and a rollup counting
                # raised Tasks off the journal would miss every escalation.
                failed_step = self.record.step(step_id)
                if failed_step is not None:
                    failed_step.task_id = task.id
                    self._persist()
                raise RunSuspended(task.id, step_id)
            raise StepFailed(step_id, last_error)

        text = summary(output) if callable(summary) else summary
        usage = meter(output) if meter is not None else None
        self._record_step(StepRecord(
            step_id=step_id, name=name, kind=kind, status="done",
            summary=text, output=output, refs=refs or [], attempts=attempts,
            duration_ms=int((time.monotonic() - started) * 1000),
            model=usage.model if usage is not None else model,
            cost_usd=usage.cost_usd if usage is not None else cost_usd,
            tokens=usage.tokens if usage is not None else tokens,
            writes=list(writes(output) or []) if writes is not None else [],
        ))
        return output

    def event(self, step_id: str, name: str, summary: str, kind: StepKind = "event") -> None:
        """A journal entry with no output — an emitted Event, a trigger.

        An `event` row also *leaves* the run: it is appended to the event log
        (`dispatch.Dispatcher.record`), which is what makes an emitted fact
        readable by something other than this one journal. A `trigger` row is
        not — the Dispatcher logged that fact before the run existed, and
        appending it again from in here would record one causation twice, in
        the wrong order. The replay guard above covers both: a resumed run
        replays the row and appends nothing.
        """
        if self.record.step(step_id) is not None:
            self.replayed += 1
            return
        self._record_step(StepRecord(
            step_id=step_id, name=name, kind=kind, status="done", summary=summary,
        ))
        if kind == "event" and self._emit is not None:
            self._emit(name, summary, self.record)

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
        # Where an Event a running flow emits goes, beyond its own journal.
        # Set by `dispatch.Dispatcher`, which registers itself: a structural
        # attribute rather than an import, because `dispatch` imports this
        # module and the engine must not import it back. None leaves the
        # journal as the only record, which is what it was before.
        self.emit: Callable[[str, str, RunRecord], Any] | None = None

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

    def registered(self) -> list[str]:
        """Every flow id with a body in this process.

        The engine is where a flow announces itself, and an inbound sync feed
        announces itself exactly the way the two authored flows do. So this is
        the only list that has all of them without someone maintaining a
        second one alongside the registry — which would be a list of the
        flows somebody remembered to add.
        """
        return sorted(self._bodies)

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
        principal: Principal | None = None,
        caused_by: str = "",
    ) -> RunRecord:
        body = self._bodies.get(flow_id)
        if body is None:
            raise ValueError(f"No registered body for flow {flow_id}")
        rid = run_id or self.mint_run_id(flow_id, inputs, actor)
        record = RunRecord(
            id=rid, flow_id=flow_id, case_ref=case_ref or rid,
            actor=actor, inputs=inputs, caused_by=caused_by,
        )
        self.store.put(record)
        return self._drive(record, body, resume_input=None, principal=principal)

    def resume(
        self,
        run_id: str,
        resume_input: dict | None = None,
        principal: Principal | None = None,
        *,
        retry_failed: bool = False,
    ) -> RunRecord | None:
        """Drive a run forward from its journal.

        `retry_failed` re-enters a run that ended `failed`. Without it such a
        run was unrecoverable: a step with no escalation queue raised
        `StepFailed`, the record went terminal, and this returned it untouched
        — so the journal sat there, complete and correct, with no way to
        re-enter it. Fixing the bug and redeploying could not save the run, and
        a resubmission was the only path, which loses the case's history and
        mints a second one.

        Safe because the journal is what it always was: completed steps replay
        from their recorded output, and only the step that failed re-executes.
        Gated to an admin at the route rather than here, because "is this
        person allowed to retry" is an authorization question and this is the
        engine.
        """
        record = self.store.get(run_id)
        if record is None:
            return None
        if record.status == "succeeded":
            return record
        if record.status == "failed" and not retry_failed:
            return record
        body = self._bodies.get(record.flow_id)
        if body is None:
            log.warning(
                "Cannot resume: no registered body",
                extra={"runId": run_id, "flowId": record.flow_id},
            )
            return record
        # The Task this run suspended on is answered by the resume itself.
        # Left open it becomes a row in somebody's queue for work that is
        # already moving — and after a `POST /runs/{id}/resume` the console
        # showed exactly that: the record cleared its pointer to the Task and
        # nothing cleared the Task.
        settled = record.open_task_id
        record.status = "running"
        record.resume_count += 1
        record.open_task_id = ""
        record.suspended_at_step = ""
        record.error = ""
        record.finished_at_iso = ""
        self.store.put(record)
        if settled:
            self._close_open_task(settled, run_id, principal)
        return self._drive(
            record, body, resume_input=resume_input, principal=principal
        )

    def _close_open_task(
        self, task_id: str, run_id: str, principal: Principal | None
    ) -> None:
        """Mark the gate's Task decided, if the resume is what decided it.

        Only touches a Task still open: the ordinary path is a person deciding
        the Task and *that* triggering the resume, and re-deciding it here
        would overwrite their verdict with a machine's.
        """
        task = self.tasks.get(task_id)
        if task is None or task.status != "open":
            return
        try:
            self.tasks.decide(
                task_id,
                verdict="approved",
                decided_by=principal.uid if principal else f"flow:{run_id}",
                note="Resumed directly; the run was driven forward rather than "
                     "the task being answered on its merits.",
            )
        except Exception:  # noqa: BLE001 — a stale task must not block a resume
            log.exception(
                "Could not settle the task a resume answered",
                extra={"taskId": task_id, "runId": run_id},
            )

    def resume_for_task(
        self,
        task_id: str,
        resume_input: dict | None = None,
        principal: Principal | None = None,
    ) -> RunRecord | None:
        run = self.find_by_task(task_id)
        return self.resume(run.id, resume_input, principal) if run else None

    # -- the loop -----------------------------------------------------------

    def _drive(
        self,
        record: RunRecord,
        body: FlowBody,
        resume_input: dict | None,
        principal: Principal | None = None,
    ) -> RunRecord:
        ctx = RunContext(
            record, self.store, self.tasks, resume_input, principal, self.emit
        )
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
