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
from datetime import timedelta
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
#:
#: `state_disagreement` is the third of that family and the one that leaves the
#: graph. The first two are questions about what Cumulus *believes* — is this
#: row that entity, are these two entities one party — and both are answered by
#: moving a status. This one is a question about what Cumulus should *do* to
#: somebody else's system: approving it releases an order in the customer's
#: ERP. Same shape, categorically different blast radius, and a screen that
#: could not tell them apart would put an outbound write behind the same button
#: as a master-data tidy-up.
TaskKind = Literal[
    "decision", "match_review", "merge_review", "state_disagreement",
    "curation", "eval_review", "exception",
]
TaskStatus = Literal["open", "done"]
TaskVerdict = Literal["approved", "rejected", "corrected"]
#: `low` is for work that must have a decision path without competing for
#: attention with work that is waiting on one — the identity sweep's
#: ratifications are the case it exists for: a merge is never automatic
#: however strong the evidence, so a confident proposal still raises a Task,
#: and that Task must not sit level with the ones a machine is unsure about.
TaskPriority = Literal["low", "normal", "high"]


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


class TaskClaim(BaseModel):
    """Who is working this Task right now, and until when.

    A lease, not a lock, and the distinction is the whole design. Doctrine
    routes a Task to a Queue precisely so that *every* bound Principal sees it
    — which load-balances, and which also means two stewards can open the same
    entity-resolution case and both do the work. The 409 on a second decision
    catches that after both have spent the morning; a claim catches it before
    either does.

    It expires because the queue's own reason for existing is that work
    survives absence. A claim that held until explicitly released would put the
    absence problem back one level down: six cases locked at 09:02 by somebody
    who is then out sick, and an admin needed to free each one. So the holder
    keeps it by staying at the desk, and stops holding it by walking away.

    Expiry is computed, never swept. `expires_at_iso` in the past *is* the
    expired state — there is no background pass whose failure could leave a
    stale lease standing, and no write required for a lease to lapse.
    """

    holder_uid: str
    holder_email: str = ""
    holder_label: str = ""
    claimed_at_iso: str
    expires_at_iso: str
    #: Times this claim was taken from a live holder. Kept on the Task rather
    #: than only in the log because a case that three people have wrestled over
    #: is a fact about the case, visible to the fourth.
    steal_count: int = 0

    def live_at(self, now_iso: str) -> bool:
        return self.expires_at_iso > now_iso

    def held_by(self, uid: str, now_iso: str) -> bool:
        return self.holder_uid == uid and self.live_at(now_iso)


class TaskClaimConflict(Exception):
    """Somebody else holds a live lease on this Task."""

    def __init__(self, task_id: str, claim: TaskClaim) -> None:
        who = claim.holder_label or claim.holder_email or claim.holder_uid
        super().__init__(
            f"{who} is working this task; their claim runs until "
            f"{claim.expires_at_iso}"
        )
        self.task_id = task_id
        self.claim = claim


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
    #: The live lease, if anyone holds one. Absent is the normal state: most
    #: Tasks are decided by whoever opens them first, and the claim only earns
    #: its keep when two people reach for the same row.
    claim: TaskClaim | None = None


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

    def mutate(self, task_id: str, fn: "Callable[[Task], Task]") -> Task:
        """Read-modify-write the Task atomically.

        `put` is a blind overwrite, which is correct for everything that writes
        a Task from a position of authority — a Flow raising one, a hook
        withdrawing a decision. It is exactly wrong for a claim: two stewards
        pressing the same button a millisecond apart would both read no
        holder, both write themselves, and the lock would have handed the case
        to two people while reporting success to both. A lock that races is
        worse than no lock, because it is believed.
        """
        ...


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

    def mutate(self, task_id: str, fn: "Callable[[Task], Task]") -> Task:
        with self._lock:
            current = self._items.get(task_id)
            if current is None:
                raise TaskNotFoundError(task_id)
            updated = fn(current)
            self._items[task_id] = updated
            return updated


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

    def mutate(self, task_id: str, fn: "Callable[[Task], Task]") -> Task:
        from google.cloud import firestore

        ref = self._col.document(task_id)

        @firestore.transactional
        def _apply(transaction) -> Task:
            snap = ref.get(transaction=transaction)
            if not snap.exists:
                raise TaskNotFoundError(task_id)
            updated = fn(Task.model_validate(snap.to_dict()))
            transaction.set(ref, updated.model_dump(mode="json"))
            return updated

        # Firestore retries the whole function on a contended commit, which is
        # why `fn` has to be a pure decision over the Task it is handed rather
        # than a closure over one read earlier: on the second attempt it must
        # re-decide against what the winner just wrote.
        return _apply(self._client.transaction())


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


#: Three bands, so a ratification sorts under a genuine ask rather than beside
#: it. An unknown value sorts with `normal`: a task with a priority nobody
#: declared is still work, and hiding it at the bottom would be worse than
#: filing it in the middle.
_PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}

#: Fallback when no queue was consulted. The real value is per-desk and lives
#: on `queues.Queue`; this is what a caller that has not looked it up gets, and
#: it deliberately matches `queues.DEFAULT_CLAIM_TTL_SECONDS`.
_DEFAULT_CLAIM_TTL_SECONDS = 20 * 60


def _sort_key(t: Task) -> tuple:
    # Open before done; high priority first among open; newest last within a
    # band so a queue reads oldest-first — the fair order for shared work.
    return (
        0 if t.status == "open" else 1,
        _PRIORITY_ORDER.get(t.priority, 1),
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

    # -- claims -------------------------------------------------------------

    def claim(
        self,
        task_id: str,
        holder_uid: str,
        *,
        holder_email: str = "",
        holder_label: str = "",
        ttl_seconds: int = 0,
        steal: bool = False,
    ) -> Task:
        """Take the lease, or say who has it.

        Re-claiming your own live lease renews it rather than conflicting —
        that is the heartbeat a screen sends while somebody reads the evidence,
        and treating it as contention would mean a steward loses the case they
        are looking at to themselves.

        `steal` takes a live lease from someone else. Permitted on purpose: the
        alternative to a person being able to say "they went home, I am doing
        this" is an admin ticket, and a coordination mechanism that needs an
        admin to unstick it will be routed around. It is counted on the Task
        and logged, which is the accountability that makes it safe to allow.
        """
        ttl = ttl_seconds if ttl_seconds > 0 else _DEFAULT_CLAIM_TTL_SECONDS
        now = utcnow()
        now_iso = now.isoformat()
        expires_iso = (now + timedelta(seconds=ttl)).isoformat()

        def take(task: Task) -> Task:
            if task.status == "done":
                raise TaskAlreadyDecidedError(task.id)
            held = task.claim
            mine = held is not None and held.holder_uid == holder_uid
            if held is not None and held.live_at(now_iso) and not mine and not steal:
                raise TaskClaimConflict(task.id, held)
            stolen = (
                held.steal_count + 1
                if held is not None and held.live_at(now_iso) and not mine
                else (held.steal_count if mine and held is not None else 0)
            )
            return task.model_copy(update={"claim": TaskClaim(
                holder_uid=holder_uid,
                holder_email=holder_email,
                holder_label=holder_label,
                claimed_at_iso=(
                    held.claimed_at_iso if mine and held is not None else now_iso
                ),
                expires_at_iso=expires_iso,
                steal_count=stolen,
            )})

        claimed = self.directory.mutate(task_id, take)
        log.info(
            "Task claimed",
            extra={
                "taskId": task_id, "queue": claimed.queue, "holder": holder_uid,
                "expiresAt": expires_iso,
                "steals": claimed.claim.steal_count if claimed.claim else 0,
            },
        )
        return claimed

    def release(self, task_id: str, holder_uid: str, force: bool = False) -> Task:
        """Hand the lease back. A lease you no longer hold releases cleanly."""

        def let_go(task: Task) -> Task:
            held = task.claim
            if held is None:
                return task
            if not force and held.holder_uid != holder_uid:
                raise TaskClaimConflict(task.id, held)
            return task.model_copy(update={"claim": None})

        released = self.directory.mutate(task_id, let_go)
        log.info(
            "Task released",
            extra={"taskId": task_id, "holder": holder_uid, "forced": force},
        )
        return released

    def claim_holder(self, task: Task) -> TaskClaim | None:
        """The claim if it is still live, None once it has lapsed.

        Every reader goes through this rather than touching `task.claim`, so
        "expired" is one definition in one place. A screen that rendered the
        raw field would show a lock that the decide path would then ignore.
        """
        now_iso = utcnow().isoformat()
        if task.claim is not None and task.claim.live_at(now_iso):
            return task.claim
        return None

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
        now_iso = utcnow().isoformat()

        def settle(task: Task) -> Task:
            if task.status == "done":
                raise TaskAlreadyDecidedError(task.id)
            held = task.claim
            # Deciding *acquires* the lease when it is free. Requiring an
            # explicit claim first would put a second click in front of every
            # decision on every single-holder desk to solve a problem only
            # shared desks have — so the claim costs nothing until it is
            # actually contended, and then it costs exactly what it should.
            if (
                held is not None
                and held.live_at(now_iso)
                and held.holder_uid != decided_by
            ):
                raise TaskClaimConflict(task.id, held)
            return task.model_copy(
                update={
                    "status": "done",
                    "claim": None,
                    "decision": TaskDecision(
                        verdict=verdict,
                        note=note,
                        correction=correction,
                        payload=payload or {},
                        decided_by=decided_by,
                        decided_at_iso=now_iso,
                    ),
                }
            )

        # One transaction for the claim check and the decision together. Split
        # in two, a steward could pass the check and lose the race in the gap.
        decided = self.directory.mutate(task_id, settle)
        log.info(
            "Task decided",
            extra={
                "taskId": task_id,
                "verdict": verdict,
                "actor": decided_by,
                "queue": decided.queue,
                "kind": decided.kind,
            },
        )
        return decided

    def withdraw_decision(self, task_id: str, reason: str) -> Task:
        """Put a decided Task back to open, because the decision did not take.

        Deciding and applying are two writes and only the second can fail. A
        Task left reading `done` over a world that never changed is the worst
        of the two possible outcomes: the queue says somebody answered it, the
        graph says nobody did, the work has left every worklist that would have
        surfaced it, and nothing anywhere reconciles the two. So a resume hook
        that raises unwinds this half — the row goes back the way it was found
        and the caller is told — which keeps the invariant the queue is worth
        anything for: a Task reads `done` only when what it authorized
        actually happened.

        Not an undo of the *judgment*: nobody changed their mind. The judgment
        was never recorded, and the steward is still looking at the screen when
        they learn it, which is the only moment they can do anything about it.
        """
        task = self.require(task_id)
        reopened = task.model_copy(update={"status": "open", "decision": None})
        self.directory.put(reopened)
        log.warning(
            "Task decision withdrawn — it could not be applied",
            extra={
                "taskId": task_id,
                "queue": task.queue,
                "kind": task.kind,
                "actor": task.decision.decided_by if task.decision else "",
                "reason": reason,
            },
        )
        return reopened

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
