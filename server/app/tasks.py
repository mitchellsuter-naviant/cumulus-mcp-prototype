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
from datetime import timedelta
from collections.abc import Callable, Collection
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from . import personas
from .auth import Principal
from .graph import normalize_key
from .models import utcnow

log = logging.getLogger("cumulus.tasks")

#: `match_review` is one arriving row against the book — approve and it links,
#: reject and it mints. `merge_review` is two entities the book already holds:
#: nothing arrived, nothing mints, and the answer flips a proposal that is
#: already in the graph at `in_review`. Two kinds rather than one, because the
#: screens differ (a landed row and its candidates, against two canonical
#: objects and the evidence joining them) and because a decision hook that had
#: to tell them apart from the payload would guess wrong the first time the two
#: shapes converged.
TaskKind = Literal[
    "decision", "match_review", "merge_review", "curation", "eval_review",
    "exception",
]
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
            queue="vendor-stewardship",
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
            resumes="future receipts from this merchant resolve without a Task",
            created_at_iso=_ago_iso(26),
        ),
        # ---- Governed knowledge: the flywheel's human half ----
        Task(
            id="task_cur_0210",
            kind="curation",
            title="Promote a repeated reviewer correction into the receipt prompt",
            summary=(
                "Five reviewers on the expense-review queue made the same "
                "correction on thermal-paper receipts: the tip line had been "
                "read as the total. The proposal amends the extraction prompt "
                "so the flow stops making it. The prompt is an asset in the "
                "corpus, so this amends knowledge and not code \u2014 which is "
                "exactly why promoting it is a curation decision and not an "
                "automatic side effect of five people agreeing."
            ),
            queue="knowledge-curation",
            priority="normal",
            flow_id="expense-reimbursement",
            refs=["knowledge:receipt-extraction-prompt@rev1"],
            evidence=[
                TaskEvidence(
                    label="Pattern", value="tip line mis-read as total, 5\u00d7"
                ),
                TaskEvidence(label="Corrections", value="5 consistent, 3 deciders"),
                TaskEvidence(label="Blast radius", value="~90 receipts/month"),
            ],
            proposal=TaskProposal(
                action="amend-prompt",
                target="knowledge-layer",
                summary="Prompt rev1 \u2192 rev2 with the tip-line instruction",
            ),
            resumes=(
                "prompt revision published; every run after it records the new "
                "rev in its ModelCall read-set"
            ),
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
        # ---- Eval: governed knowledge moved under a declared case ----
        Task(
            id="task_eval_0032",
            kind="eval_review",
            title="A graded case read knowledge its fixture never declared",
            summary=(
                "`expense/pat-on-the-roster` is declared against the expense "
                "approval policy alone, but the graded run also read the "
                "receipt extraction prompt \u2014 governed knowledge the flow "
                "reads at run time. A fixture's read-set is a claim about what "
                "a case was written against, so a run that read more than the "
                "claim is the signal that knowledge moved under the case rather "
                "than the flow regressing. Which of the two it is, only a "
                "person can say."
            ),
            queue="eval-review",
            priority="normal",
            flow_id="expense-reimbursement",
            case_ref="eval:expense-in-policy-auto-approves",
            refs=[
                "knowledge:expense-approval-policy@rev1",
                "knowledge:receipt-extraction-prompt@rev1",
            ],
            evidence=[
                TaskEvidence(
                    label="Declared", value="knowledge:expense-approval-policy@rev1"
                ),
                TaskEvidence(
                    label="Also read",
                    value="knowledge:receipt-extraction-prompt@rev1",
                ),
                TaskEvidence(
                    label="Consequence",
                    value="the case still passes, but against a wider read-set",
                ),
            ],
            proposal=TaskProposal(
                action="redeclare-read-set",
                target="eval-fixtures",
                summary=(
                    "Pin `expense/pat-on-the-roster` to the revisions the run "
                    "actually read"
                ),
            ),
            resumes="fixture re-declared; later runs compare against it",
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
                TaskEvidence(
                    label="Employee", value=personas.DEFAULT_PERSONA.person
                ),
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
