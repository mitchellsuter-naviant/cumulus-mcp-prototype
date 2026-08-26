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
golden sets): deciding one is a durable, attributed write — the prototype's
stand-in for the curation-ledger entry the doctrine requires every human
decision to land as. For tasks raised by the live expense-reimbursement
runner, deciding genuinely resumes the run (reimbursements.py picks the
decision up and commits the state); for the rest, the `resumes` field says
what would happen once a full engine lands.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, Field

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
    # The Queue this task routed to. Principals bind to queues via roles;
    # the prototype shows every queue to every signed-in human.
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

    def stats(self) -> TaskStats:
        stats = TaskStats()
        for t in self.directory.list():
            if t.status != "open":
                continue
            stats.open += 1
            if t.priority == "high":
                stats.high_priority += 1
            stats.by_queue[t.queue] = stats.by_queue.get(t.queue, 0) + 1
            stats.by_kind[t.kind] = stats.by_kind.get(t.kind, 0) + 1
        return stats


def build_task_service(
    backend: str, project: str | None, collection: str
) -> TaskService:
    if backend == "firestore":
        return TaskService(FirestoreTaskDirectory(project, collection))
    return TaskService(InMemoryTaskDirectory())


# ---------------------------------------------------------------------------
# Seed queue
# ---------------------------------------------------------------------------


def _ago_iso(hours: float) -> str:
    return (utcnow() - timedelta(hours=hours)).isoformat()


def _seed_tasks() -> list[Task]:
    """A demo backlog across the three streams, tied to the seeded world.

    Ids are stable slugs (not random) so the seed is idempotent per backend
    and a demo script can name them.

    The elevated-reimbursement tasks that used to be written by hand here are
    gone: the seeded expense runs suspend for real, so the engine raises those
    Tasks itself and deciding one genuinely resumes its run. What is left is
    the work no live flow produces yet.
    """
    return [
        # ---- HITL proper: an exception, plus curation and resolution ----
        Task(
            id="task_payout_3307",
            kind="exception",
            title="Payout export rejected an approved reimbursement",
            summary=(
                "Transient retries exhausted pushing run_er_seed03 to the "
                "payroll export: duplicate employee cost-center row. Nothing "
                "is rolled back; the approval stands and the export needs a "
                "human call."
            ),
            queue="expense-review",
            priority="high",
            flow_id="expense-reimbursement",
            case_ref="run_er_seed03",
            refs=["knowledge:expense-approval-policy"],
            evidence=[
                TaskEvidence(label="Succeeded", value="reimbursements table, ledger"),
                TaskEvidence(
                    label="Rejected", value="payroll export: duplicate cost center"
                ),
                TaskEvidence(label="Attempts", value="4 over 18 minutes"),
            ],
            proposal=TaskProposal(
                action="repost-with-cost-center",
                target="payroll-export",
                summary="Re-export with the corrected cost-center row",
            ),
            resumes="export retries with the corrected row",
            created_at_iso=_ago_iso(1),
        ),
        # ---- Entity resolution: merchant identity from receipt extraction ----
        Task(
            id="task_mdm_0071",
            kind="match_review",
            title="Low-confidence merchant match: \u201cCorner Bistro #114\u201d",
            summary=(
                "Receipt extraction resolved the printed merchant name to the "
                "canonical vendor at 0.71, below the 0.85 auto-accept "
                "threshold. Confirming creates the identity Link with "
                "reversal info retained; rejecting creates a new canonical "
                "merchant."
            ),
            queue="data-steward",
            priority="normal",
            flow_id="expense-reimbursement",
            case_ref="run_er_seed01",
            refs=["vendor:corner-bistro@rev3"],
            evidence=[
                TaskEvidence(
                    label="Printed on receipt", value="Corner Bistro #114 \u00b7 5th St"
                ),
                TaskEvidence(
                    label="Candidate", value="corner-bistro \u00b7 Corner Bistro LLC"
                ),
                TaskEvidence(label="Method", value="fuzzy_name \u00b7 confidence 0.71"),
            ],
            proposal=TaskProposal(
                action="confirm-identity-link",
                target="graph",
                summary="Link merchant \u2192 vendor:corner-bistro (reversible)",
            ),
            resumes="future receipts from this merchant resolve one-shot",
            created_at_iso=_ago_iso(26),
        ),
        # ---- Governed knowledge: the flywheel's human half ----
        Task(
            id="task_cur_0210",
            kind="curation",
            title="Promote correction set entries into the receipt exemplar",
            summary=(
                "Five consistent human corrections on thermal-paper receipts. "
                "The promotion Rule proposes updating the one-shot exemplar; "
                "promotion into canonical knowledge is a curation decision, "
                "never automatic."
            ),
            queue="knowledge-curation",
            priority="normal",
            flow_id="expense-reimbursement",
            refs=[
                "knowledge:receipt-exemplar@rev3",
                "knowledge:receipt-extraction-corrections@rev41",
            ],
            evidence=[
                TaskEvidence(
                    label="Pattern", value="tip line mis-read as total, 5\u00d7"
                ),
                TaskEvidence(label="Corrections", value="5 consistent, 3 deciders"),
                TaskEvidence(label="Blast radius", value="~90 receipts/month"),
            ],
            proposal=TaskProposal(
                action="promote-exemplar",
                target="knowledge-layer",
                summary="Exemplar rev3 \u2192 rev4 with the tip-line template",
            ),
            resumes="exemplar revision published; change ledgered",
            created_at_iso=_ago_iso(50),
        ),
        Task(
            id="task_cur_0214",
            kind="curation",
            title="Two published policies disagree on the auto-approval limit",
            summary=(
                "expense-approval-policy says $200.00; the travel booking "
                "guideline still implies $150 for travel meals. Both are "
                "published, so retrieval cannot break the tie."
            ),
            queue="knowledge-curation",
            priority="high",
            refs=[
                "knowledge:expense-approval-policy",
                "knowledge:travel-booking-guideline",
            ],
            evidence=[
                TaskEvidence(label="Conflict", value="$200.00 vs $150.00"),
                TaskEvidence(
                    label="Signal", value="cited together in 14 answers (30d)"
                ),
            ],
            proposal=TaskProposal(
                action="deprecate-or-amend",
                target="knowledge-layer",
                summary="Supersede the guideline's meal-limit clause",
            ),
            resumes="losing clause marked superseded; ledger records why",
            created_at_iso=_ago_iso(76),
        ),
        # ---- Eval: a judge that cannot decide raises a review Task ----
        Task(
            id="task_eval_0032",
            kind="eval_review",
            title="Judge could not grade a golden-set assertion",
            summary=(
                "The rubric judge returned `cannot_decide` on one fact in the "
                "expense-reimbursement golden set rather than guessing."
            ),
            queue="eval-review",
            priority="normal",
            flow_id="expense-reimbursement",
            case_ref="golden:exp-fixture-007",
            refs=["knowledge:expense-approval-policy"],
            evidence=[
                TaskEvidence(label="Fact", value="reimbursement.verdict"),
                TaskEvidence(label="Expected", value="elevate"),
                TaskEvidence(
                    label="Candidate said",
                    value="approve at confidence 0.79 (rubric boundary)",
                ),
            ],
            proposal=TaskProposal(
                action="label-case",
                target="golden-set",
                summary="Set the expected value; dataset version moves",
            ),
            resumes="case relabelled; pending experiment re-grades it",
            created_at_iso=_ago_iso(96),
        ),
        # ---- HR: a PTO request over the auto-approval window ----
        Task(
            id="task_pto_seed_01",
            kind="decision",
            title="PTO request: 7 days in October",
            summary=(
                "Over the governed 3-day auto-approval window, so the "
                "request routes to HR. Balance covers it."
            ),
            queue="hr",
            priority="normal",
            case_ref="pto_seed_01",
            refs=["knowledge:pto-policy"],
            evidence=[
                TaskEvidence(label="Employee", value="dev"),
                TaskEvidence(label="Dates", value="Oct 12 \u2013 Oct 20 \u00b7 7 working days"),
                TaskEvidence(label="Balance", value="17.5 days"),
                TaskEvidence(
                    label="Auto-approval window", value="\u2264 3 days (governed)"
                ),
            ],
            proposal=TaskProposal(
                action="approve-pto",
                target="hr-system",
                summary="Approve and deduct 7 days from the balance",
            ),
            resumes="balance deducted; calendar hold confirmed",
            created_at_iso=_ago_iso(20),
        ),
        # ---- A little history so the panel's Done view isn't empty ----
        Task(
            id="task_hist_hotel",
            kind="decision",
            title="Elevated reimbursement: harbor-hotel $612.00",
            summary="Over the auto-approval limit; conference rate confirmed.",
            queue="expense-review",
            flow_id="expense-reimbursement",
            case_ref="run_er_seed03",
            status="done",
            refs=["knowledge:expense-approval-policy"],
            evidence=[TaskEvidence(label="Amount", value="$612.00")],
            proposal=TaskProposal(
                action="record-reimbursement",
                target="reimbursements",
                summary="Record as approved",
            ),
            created_at_iso=_ago_iso(390),
            decision=TaskDecision(
                verdict="approved",
                note="Conference block rate confirmed against the event contract.",
                decided_by="dana@customer.example",
                decided_at_iso=_ago_iso(388),
            ),
        ),
        Task(
            id="task_hist_taxi",
            kind="decision",
            title="Elevated reimbursement: airport-taxi $58.75",
            summary="Low extraction confidence; amount verified off the card feed.",
            queue="expense-review",
            flow_id="expense-reimbursement",
            case_ref="run_er_seed08",
            status="done",
            refs=["knowledge:expense-approval-policy"],
            evidence=[
                TaskEvidence(label="Extraction confidence", value="0.58"),
            ],
            proposal=TaskProposal(
                action="record-reimbursement",
                target="reimbursements",
                summary="Record as approved",
            ),
            created_at_iso=_ago_iso(148),
            decision=TaskDecision(
                verdict="corrected",
                note="Amount matches the card feed; corrected vendor spelling.",
                correction="vendor: airport-taxi (was: airporttaxi-114)",
                decided_by="dana@customer.example",
                decided_at_iso=_ago_iso(146),
            ),
        ),
    ]


def seed_tasks_if_empty(service: TaskService) -> int:
    """Idempotent: only an empty store is seeded, same posture as the fleet."""
    if service.directory.list():
        return 0
    tasks = _seed_tasks()
    for task in tasks:
        service.directory.put(task)
    log.info("Seeded task queue", extra={"count": len(tasks)})
    return len(tasks)
