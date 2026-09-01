"""REST surface for the Task Queue — the console's HITL spine.

Reads are open to any authenticated role: seeing the queue is how work
reaches people. Deciding is different in kind from every other write in the
API: it *is* the human judgment the doctrine routes through Tasks, so a
minted API key is refused outright — a key deciding a Task would be an agent
approving its own proposal.

The write ladder is the wrong instrument for the rest of it. An AP clerk and
an expense approver sit on the same rung and own different work, so authority
alone cannot say who may decide what — the doctrine's Role confers
queue-bindings as well as permissions, and `require_queue` is that half. What
you may *see* is a separate question from what you may *decide*: a
platform-scoped principal reads every queue in order to diagnose it, and still
decides only its own.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import (
    Principal,
    get_principal,
    require_human,
    require_queue,
    require_reader,
)
from .deps import (
    get_employee_service,
    get_payable_service,
    get_queue_service,
    get_reimbursement_service,
    get_sync_service,
    get_task_service,
)
from .gateway import Refused
from .tasks import (
    Task,
    TaskAlreadyDecidedError,
    TaskClaimConflict,
    TaskKind,
    TaskNotFoundError,
    TaskStats,
    TaskStatus,
    TaskVerdict,
)

log = logging.getLogger("cumulus.tasks.api")

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


class TaskView(Task):
    """A Task plus the two answers the console needs before it draws a button.

    The queue said who may act and the claim said who is acting, and neither
    reached the client — so "My Tasks" rendered a live Approve/Correct/Reject
    row on every task in the deployment and discovered the refusal only after
    somebody had committed to a verdict. Offering work you have already decided
    to refuse is worse than not listing it: it spends the user's judgment
    before telling them it does not count.

    `decidable` is computed from exactly the checks `decide` will run, in the
    same order, so the greyed-out button and the 403 can never disagree.
    `blocked_reason` is written to be shown verbatim.
    """

    decidable: bool = False
    blocked_reason: str = ""
    #: "free" · "yours" · "held" — what the lease looks like from here.
    claim_state: str = "free"
    #: Who could act on this, for a reason a screen can actually use: telling
    #: somebody a desk is not theirs without naming whose it is leaves them
    #: with nothing to do next.
    queue_holders: list[str] = Field(default_factory=list)


def _view(task: Task, principal: Principal) -> TaskView:
    tasks = get_task_service()
    live = tasks.claim_holder(task)
    if live is None:
        claim_state = "free"
    elif live.holder_uid == principal.uid:
        claim_state = "yours"
    else:
        claim_state = "held"

    holders: list[str] = []
    reason = ""
    if task.status == "done":
        reason = "Already decided — the record stands."
    elif principal.key_id is not None:
        reason = "A minted key cannot decide a Task; sign in as yourself."
    elif not principal.owns_queue(task.queue):
        holders = get_queue_service().holders_of(task.queue)
        who = ", ".join(holders) if holders else "nobody yet"
        reason = (
            f"This sits on the '{task.queue}' desk, which you do not hold. "
            f"Held by: {who}."
        )
    elif claim_state == "held" and live is not None:
        who = live.holder_label or live.holder_email or live.holder_uid
        reason = (
            f"{who} claimed this and is working it now. You can take it over "
            "if they have stopped."
        )
    return TaskView(
        **task.model_dump(),
        decidable=not reason,
        blocked_reason=reason,
        claim_state=claim_state,
        queue_holders=holders,
    )


class ClaimRequest(BaseModel):
    #: Take a live claim from its holder. Recorded on the Task and logged —
    #: allowed because the alternative to "they went home, I am doing this" is
    #: an admin ticket, and a coordination mechanism that needs an admin to
    #: unstick it gets routed around instead of used.
    steal: bool = False


class TaskDecisionRequest(BaseModel):
    verdict: TaskVerdict
    note: str = Field(default="", max_length=2000)
    correction: str = Field(default="", max_length=2000)
    # The structured half of a `corrected` decision: edited field values, added
    # or removed lines, a chosen entity id. A prose correction cannot say
    # "split this receipt into two lines", and that is exactly what a compound
    # receipt needs. Interpreted only by the flow that raised the Task.
    payload: dict = Field(default_factory=dict)


#: Which resume hook answers which kind of Task.
#:
#: This was a tuple of six lambdas, every one of them run on every decision,
#: every one of them opening with a guard that returned None for kinds that
#: were not its own. Thirty no-ops and one real call, and the routing — the
#: only thing a reader actually wants from this — was not written down
#: anywhere; it was distributed across six other modules' first two lines.
#:
#: Keyed, the table *is* the routing, and it is exhaustive over `TaskKind` on
#: purpose: a new kind with no hook shows up here as an empty tuple somebody
#: chose, rather than as a silent fall-through nobody noticed. A test holds it
#: to the declared kinds.
#:
#: The two shapes in it are different and the difference is worth reading. The
#: **run gates** — payables and reimbursements — are keyed on the Task's
#: `flow_id` rather than its kind, because a Task that suspended a run can be
#: any kind that flow chose to raise; they appear under every kind those flows
#: use to gate. The **judgments** — a steward's on an identity, a controller's
#: on a disagreement — belong to exactly one kind each and commit straight
#: through the Gateway with no run to record them, which is why an exception
#: escaping one of them has to reach the caller (see below).
_RUN_GATES: tuple = (
    lambda t, p: get_reimbursement_service().apply_task_decision(t, p),
    lambda t, p: get_payable_service().apply_task_decision(t, p),
)

RESUME_HOOKS: dict[str, tuple] = {
    "decision": _RUN_GATES + (
        # PTO, which gates on `case_ref` rather than on a run of its own.
        lambda t, _p: get_employee_service().apply_task_decision(t),
    ),
    "curation": _RUN_GATES,
    "eval_review": _RUN_GATES,
    "exception": _RUN_GATES,
    # A steward's entity-resolution decision is the *first* write on that
    # record: a batch sync asserts nothing while the Task is open.
    "match_review": (lambda t, p: get_sync_service().apply_match_decision(t, p),),
    # And its sibling on the other kind: a proposed cross-source merge already
    # sits in the graph at `in_review`, so this decision flips it or withdraws
    # it rather than asserting anything new.
    "merge_review": (lambda t, p: get_sync_service().apply_merge_decision(t, p),),
    # And the controller's, on a disagreement between two of the customer's own
    # systems. Approving starts a run that writes to one of them; rejecting
    # settles the finding here and now.
    "state_disagreement": (
        lambda t, p: get_sync_service().apply_release_decision(t, p),
    ),
}


def _visible(principal: Principal, queue: str, mine: bool) -> bool:
    """Whether this principal should be shown work sitting in `queue`.

    Two rules, and the second is the one that matters: asking for "mine" always
    means your bindings, and a persona whose reach is its own work never sees
    past them even when it does not ask.
    """
    if mine or not principal.reads_beyond_self:
        return principal.owns_queue(queue)
    return True


@router.get("", response_model=list[TaskView])
def list_tasks(
    status: TaskStatus | None = Query(default=None),
    queue: str | None = Query(default=None),
    kind: TaskKind | None = Query(default=None),
    mine: bool = Query(
        default=False, description="Only queues this persona is bound to"
    ),
    principal: Principal = Depends(get_principal),
) -> list[TaskView]:
    """The queue. `mine=true` is "My Tasks"; without it, what your scope reaches.

    An own-scoped persona sees only its own queues either way — showing a
    clerk work nobody will ever route to them is noise wearing the costume of
    transparency.
    """
    require_reader(principal)
    tasks = get_task_service().list(status=status, queue=queue, kind=kind)
    return [_view(t, principal) for t in tasks if _visible(principal, t.queue, mine)]


@router.get("/stats", response_model=TaskStats)
def task_stats(
    mine: bool = Query(default=False),
    principal: Principal = Depends(get_principal),
) -> TaskStats:
    """Counts over the same slice `list_tasks` would return, so the rail badge
    and the list it opens can never disagree."""
    require_reader(principal)
    unrestricted = not mine and principal.reads_beyond_self
    return get_task_service().stats(
        queues=None if unrestricted else principal.queues
    )


@router.get("/{task_id}", response_model=TaskView)
def get_task(
    task_id: str, principal: Principal = Depends(get_principal)
) -> TaskView:
    require_reader(principal)
    task = get_task_service().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return _view(task, principal)


@router.post("/{task_id}/claim", response_model=TaskView)
def claim_task(
    task_id: str,
    body: ClaimRequest | None = None,
    principal: Principal = Depends(get_principal),
) -> TaskView:
    """Take the lease on a pooled Task, or learn who holds it.

    The lease runs for the desk's own `claim_ttl_seconds` and lapses on its
    own, so nothing here can wedge: the worst case is that a steward who walked
    away keeps a case out of the pool for twenty minutes.

    Re-claiming your own live lease is the heartbeat, not contention — a screen
    calls this while somebody reads the evidence, and it renews rather than
    conflicting.
    """
    require_reader(principal)
    require_human(principal, "claim tasks; a claim says a person is working it")
    tasks = get_task_service()
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    require_queue(principal, task.queue)
    try:
        claimed = tasks.claim(
            task_id,
            principal.uid,
            holder_email=principal.email,
            holder_label=principal.email or principal.uid,
            ttl_seconds=get_queue_service().claim_ttl(task.queue),
            steal=bool(body and body.steal),
        )
    except TaskClaimConflict as conflict:
        # 409 and not 403: nothing about this is a permission problem — the
        # work is simply already in somebody's hands, and the caller's next
        # move is to wait, pick another row, or take it over deliberately.
        raise HTTPException(
            status_code=409,
            detail=(
                f"{conflict}. Re-send with steal=true to take it over; the "
                "takeover is recorded on the task."
            ),
        ) from conflict
    except TaskAlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _view(claimed, principal)


@router.post("/{task_id}/release", response_model=TaskView)
def release_task(
    task_id: str, principal: Principal = Depends(get_principal)
) -> TaskView:
    """Put a claimed Task back in the pool without deciding it."""
    require_reader(principal)
    tasks = get_task_service()
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    require_queue(principal, task.queue)
    try:
        # An admin may free anyone's lease; everyone else frees their own.
        released = tasks.release(
            task_id, principal.uid, force=principal.outranks_or_is("admin")
        )
    except TaskClaimConflict as conflict:
        raise HTTPException(status_code=409, detail=str(conflict)) from conflict
    return _view(released, principal)


@router.post("/{task_id}/decide", response_model=Task)
def decide_task(
    task_id: str,
    body: TaskDecisionRequest,
    principal: Principal = Depends(get_principal),
) -> Task:
    require_reader(principal)
    require_human(principal, "decide tasks; a task is a human judgment")
    task = get_task_service().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    require_queue(principal, task.queue)
    try:
        decided = get_task_service().decide(
            task_id,
            verdict=body.verdict,
            decided_by=principal.uid,
            note=body.note,
            correction=body.correction,
            payload=body.payload,
        )
    except TaskNotFoundError:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    except TaskAlreadyDecidedError:
        # 409, not idempotent-200: a second decider must learn the judgment
        # already happened, not be told their verdict was recorded.
        raise HTTPException(status_code=409, detail="Task already decided")
    except TaskClaimConflict as conflict:
        # Deciding acquires a free lease, so reaching here means somebody else
        # holds a live one — the coordination the claim exists for, caught
        # before the work is duplicated rather than after.
        raise HTTPException(
            status_code=409,
            detail=(
                f"{conflict}. Claim it with steal=true first if they have "
                "stopped working it."
            ),
        ) from conflict

    # The resume half of suspend/resume: a decision on a task raised by a live
    # runner resumes that run. `RESUME_HOOKS` says which hooks answer for this
    # kind of Task, so the ones that could only have no-opped are not called at
    # all.
    #
    # The principal travels with the decision, because the Action Gateway
    # checks who is committing at the write and not at this route. A hook that
    # dropped it would land the commit as the Flow's own — accurate for an
    # auto-post, a lie for a judgment somebody made.
    #
    # **A hook that raises un-decides the Task.** This used to swallow the
    # exception and return 200, on the reasoning that the judgment stands and a
    # stuck resume surfaces in the logs. It does not stand: the Task read
    # `done` over a world where nothing had happened — the queue saying it was
    # answered, the graph saying it was not, the row gone from every worklist
    # that would have brought it back, and a log line nobody was looking at as
    # the only record. That is precisely how the pinned-queue refusal below
    # hid: a vendor steward's merge decision was refused at the Gateway and the
    # console showed it as decided. So the decision comes back off the Task and
    # the caller is told which hook failed and why.
    #
    # This bites where it should. A hook that resumes a *run* does not raise —
    # `RunEngine._drive` catches a failing body and records the run as failed,
    # which is itself a durable account of what happened — so what reaches here
    # is the resume that has no run to record it: a steward's entity decision,
    # committed straight through the Gateway, where an exception really is the
    # only trace there would ever be.
    for resume in RESUME_HOOKS.get(decided.kind, ()):
        try:
            resume(decided, principal)
        except Refused as refusal:
            log.warning(
                "Task decision refused at the Action Gateway",
                extra={"taskId": task_id, "action": refusal.action_id,
                       "uid": principal.uid, "refusal": refusal.reason},
            )
            get_task_service().withdraw_decision(task_id, str(refusal))
            # 403 and not 500: nothing broke. This principal may not commit
            # what answering the Task commits, which is an answer about
            # authority and reads as one.
            raise HTTPException(
                status_code=403,
                detail=(
                    f"The Action Gateway refused to commit this decision: "
                    f"{refusal.action_id} {refusal.reason}. The task is open "
                    "again — nothing was recorded and nothing changed."
                ),
            )
        except Exception as exc:
            log.exception(
                "Task decision hook failed", extra={"taskId": task_id}
            )
            get_task_service().withdraw_decision(task_id, str(exc))
            raise HTTPException(
                status_code=500,
                detail=(
                    f"The decision could not be applied: {exc}. The task is "
                    "open again — nothing was recorded and nothing changed."
                ),
            )
    return decided
