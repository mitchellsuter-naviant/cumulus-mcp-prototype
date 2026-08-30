"""Tasks: the unit of human judgment, routed through Queues.

Doctrine (Lens 4): an agent proposes, a person decides, the system resumes.
A Task is assigned to a Queue — a named work destination — never to a person,
so work survives absence and shares across a team. Three streams raise Tasks,
and all three are knowledge-layer streams:

- HITL proper: a Flow pauses for judgment (approve this invoice), or a
  persistent failure escalates with full context.
- Entity resolution: a low-confidence identity match needs a steward's
  confirmation before the merge is actionable.
- Governed knowledge: promoting a correction into canonical knowledge is a
  curation decision, never an inline side effect.

Prototype honesty: tasks are *real stored state* (like flow definitions and
eval specs): deciding one is a durable, attributed write — the prototype's
stand-in for the curation-ledger entry the doctrine requires every human
decision to land as. For tasks raised by the live expense-reimbursement
runner, deciding genuinely resumes the run (reimbursements.py picks the
decision up and commits the state); for the rest, the `resumes` field says
what would happen once a full engine lands.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Collection
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from . import personas
from .auth import Principal
from .graph import normalize_key
from .models import utcnow

log = logging.getLogger("cumulus.tasks")

TaskKind = Literal["decision", "match_review", "curation", "eval_review", "exception"]
TaskStatus = Literal["open", "done"]
TaskVerdict = Literal["approved", "rejected", "corrected"]
TaskPriority = Literal["normal", "high"]


class TaskNotFoundError(Exception):
    def __init__(self, task_id: str) -> None:
        super().__init__(f"Task not found: {task_id}")
        self.task_id = task_id


class TaskAlreadyDecidedError(Exception):
    def __init__(self, task_id: str) -> None:
        super().__init__(f"Task already decided: {task_id}")
        self.task_id = task_id


class TaskEvidence(BaseModel):
    """One labelled fact the decider needs — served with the task so the
    person decides in seconds with full context, not after a hunt."""

    label: str
    value: str


class TaskProposal(BaseModel):
    """What the agent proposes to do if approved. `action` is the guarded
    Action that would commit through the Gateway; `target` is where."""

    action: str
    target: str = ""
    summary: str = ""


class TaskDecision(BaseModel):
    verdict: TaskVerdict
    note: str = ""
    # For `corrected`: the value the human supplied in place of the proposal.
    correction: str = ""
    # For `corrected` on a structured review: the patch the reviewer applied —
    # edited field values, added or removed lines, a supplied purpose. A prose
    # `correction` string cannot carry "split this receipt into two lines", and
    # that is exactly what a compound receipt needs, so the structured payload
    # travels beside it. Shape is per flow; the flow that raised the Task is
    # the only thing that interprets it.
    payload: dict = Field(default_factory=dict)
    decided_by: str = ""
    decided_at_iso: str = ""


class Task(BaseModel):
    id: str
    kind: TaskKind
    title: str
    summary: str = ""
    # The Queue this task routed to. Principals bind to queues through their
    # persona, and `require_queue` gates deciding on the binding — so this
    # field decides who may act, not merely where the row is filed.
    queue: str
    priority: TaskPriority = "normal"
    status: TaskStatus = "open"
    flow_id: str = ""
    run_id: str = ""
    case_ref: str = ""
    # Knowledge/data-layer records this judgment rests on, in the read-set
    # namespaces (`knowledge:…@rev`, `vendor:…`, `graph:…`).
    refs: list[str] = Field(default_factory=list)
    evidence: list[TaskEvidence] = Field(default_factory=list)
    proposal: TaskProposal | None = None
    # What resuming means once a real engine exists — display-only today.
    resumes: str = ""
    created_at_iso: str = ""
    decision: TaskDecision | None = None


class TaskStats(BaseModel):
    open: int = 0
    high_priority: int = 0
    by_queue: dict[str, int] = Field(default_factory=dict)
    by_kind: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class TaskDirectory(Protocol):
    def list(self) -> list[Task]: ...
    def get(self, task_id: str) -> Task | None: ...
    def put(self, task: Task) -> Task: ...


class InMemoryTaskDirectory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Task] = {}

    def list(self) -> list[Task]:
        with self._lock:
            return list(self._items.values())

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._items.get(task_id)

    def put(self, task: Task) -> Task:
        with self._lock:
            self._items[task.id] = task
            return task


class FirestoreTaskDirectory:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore  # deferred: not needed for local dev

        self._client = firestore.Client(project=project)
        self._col = self._client.collection(collection)

    def list(self) -> list[Task]:
        out = []
        for doc in self._col.stream():
            try:
                out.append(Task.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping unparseable task", extra={"docId": doc.id})
        return out

    def get(self, task_id: str) -> Task | None:
        snap = self._col.document(task_id).get()
        if not snap.exists:
            return None
        try:
            return Task.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable task", extra={"taskId": task_id})
            return None

    def put(self, task: Task) -> Task:
        self._col.document(task.id).set(task.model_dump(mode="json"))
        return task


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def _sort_key(t: Task) -> tuple:
    # Open before done; high priority first among open; newest last within a
    # band so a queue reads oldest-first — the fair order for shared work.
    return (
        0 if t.status == "open" else 1,
        0 if t.priority == "high" else 1,
        t.created_at_iso,
    )


class TaskService:
    def __init__(self, directory: TaskDirectory) -> None:
        self.directory = directory

    def list(
        self,
        status: TaskStatus | None = None,
        queue: str | None = None,
        kind: TaskKind | None = None,
    ) -> list[Task]:
        items = self.directory.list()
        if status is not None:
            items = [t for t in items if t.status == status]
        if queue is not None:
            items = [t for t in items if t.queue == queue]
        if kind is not None:
            items = [t for t in items if t.kind == kind]
        return sorted(items, key=_sort_key)

    def get(self, task_id: str) -> Task | None:
        return self.directory.get(task_id)

    def create(self, task: Task) -> Task:
        """Raised by a running flow — the suspend half of suspend/resume."""
        if not task.created_at_iso:
            task = task.model_copy(update={"created_at_iso": utcnow().isoformat()})
        self.directory.put(task)
        log.info(
            "Task raised",
            extra={"taskId": task.id, "queue": task.queue, "kind": task.kind},
        )
        return task

    def require(self, task_id: str) -> Task:
        task = self.directory.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    def decide(
        self,
        task_id: str,
        verdict: TaskVerdict,
        decided_by: str,
        note: str = "",
        correction: str = "",
        payload: dict | None = None,
    ) -> Task:
        """The human's judgment, recorded durably and attributed.

        A decided task is never decided again — the record is the point. In a
        real deployment this write is also the curation-ledger append and the
        signal that resumes the suspended run.
        """
        task = self.require(task_id)
        if task.status == "done":
            raise TaskAlreadyDecidedError(task_id)
        decided = task.model_copy(
            update={
                "status": "done",
                "decision": TaskDecision(
                    verdict=verdict,
                    note=note,
                    correction=correction,
                    payload=payload or {},
                    decided_by=decided_by,
                    decided_at_iso=utcnow().isoformat(),
                ),
            }
        )
        self.directory.put(decided)
        log.info(
            "Task decided",
            extra={
                "taskId": task_id,
                "verdict": verdict,
                "actor": decided_by,
                "queue": task.queue,
                "kind": task.kind,
            },
        )
        return decided

    def stats(self, queues: Collection[str] | None = None) -> TaskStats:
        """Counts over the open queue, narrowed to `queues` when given.

        Every number is computed from the same slice. Filtering the breakdown
        while leaving the headline total alone is worse than not filtering at
        all: a badge reading 11 over a list showing none is a screen arguing
        with itself.
        """
        stats = TaskStats()
        for t in self.directory.list():
            if t.status != "open":
                continue
            if queues is not None and t.queue not in queues:
                continue
            stats.open += 1
            if t.priority == "high":
                stats.high_priority += 1
            stats.by_queue[t.queue] = stats.by_queue.get(t.queue, 0) + 1
            stats.by_kind[t.kind] = stats.by_kind.get(t.kind, 0) + 1
        return stats

    def workspace_scope(
        self, principal: Principal, queue: str = ""
    ) -> Callable[[str, str], bool]:
        """Whether a workspace row belongs to this principal, by (owner, task).

        `reads_beyond_self` is the right test for a **governance** surface —
        the knowledge layer, the graph browser — and the wrong one for a
        **workspace**. A workspace is where work gets done, and you can see the
        work you are accountable for: your own rows, plus the rows of any run
        whose Task landed in a queue you own. Narrowing an own-scoped principal
        to their own email alone shut the approver of a queue out of the very
        table the queue is about, which is the opposite of what a queue binding
        is for.

        `queue` is the queue this workspace's flow routes to. Owning it makes
        the whole table yours, task or no task — Lens 4 calls a Workspace a
        Seeing Space and says plainly that it is impersonal, that you can watch
        how a workstream runs without being assigned anything in it. Scoping to
        rows that raised a Task would have shown an AP clerk only the invoices
        that went wrong: `run_ap_seed01` auto-posted cleanly and so was
        invisible to the one person whose job it is, leaving him no baseline to
        judge the exceptions against.

        Returned as a predicate over the two fields rather than over rows,
        because the two workspaces name them differently — `employee` against
        `submitted_by` — and that naming is the only part of this rule that
        differs between them. Both spend one line on it in their own
        `visible_to`; the rule itself is written here once so it cannot drift
        into two rules again.
        """
        if principal.reads_beyond_self or (queue and principal.owns_queue(queue)):
            return lambda owner, task_id: True
        mine = normalize_key(principal.email)
        # Resolved once per call, not once per row: one set answers every row,
        # and a lookup per row is a directory read per row.
        owned = {t.id for t in self.directory.list() if t.queue in principal.queues}
        return lambda owner, task_id: (
            normalize_key(owner) == mine or task_id in owned
        )


def build_task_service(
    backend: str, project: str | None, collection: str
) -> TaskService:
    if backend == "firestore":
        return TaskService(FirestoreTaskDirectory(project, collection))
    return TaskService(InMemoryTaskDirectory())
