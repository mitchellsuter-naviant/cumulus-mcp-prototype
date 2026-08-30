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
    get_reimbursement_service,
    get_sync_service,
    get_task_service,
)
from .gateway import Refused
from .tasks import (
    Task,
    TaskAlreadyDecidedError,
    TaskKind,
    TaskNotFoundError,
    TaskStats,
    TaskStatus,
    TaskVerdict,
)

log = logging.getLogger("cumulus.tasks.api")

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


class TaskDecisionRequest(BaseModel):
    verdict: TaskVerdict
    note: str = Field(default="", max_length=2000)
    correction: str = Field(default="", max_length=2000)
    # The structured half of a `corrected` decision: edited field values, added
    # or removed lines, a chosen entity id. A prose correction cannot say
    # "split this receipt into two lines", and that is exactly what a compound
    # receipt needs. Interpreted only by the flow that raised the Task.
    payload: dict = Field(default_factory=dict)


def _visible(principal: Principal, queue: str, mine: bool) -> bool:
    """Whether this principal should be shown work sitting in `queue`.

    Two rules, and the second is the one that matters: asking for "mine" always
    means your bindings, and a persona whose reach is its own work never sees
    past them even when it does not ask.
    """
    if mine or not principal.reads_beyond_self:
        return principal.owns_queue(queue)
    return True


@router.get("", response_model=list[Task])
def list_tasks(
    status: TaskStatus | None = Query(default=None),
    queue: str | None = Query(default=None),
    kind: TaskKind | None = Query(default=None),
    mine: bool = Query(
        default=False, description="Only queues this persona is bound to"
    ),
    principal: Principal = Depends(get_principal),
) -> list[Task]:
    """The queue. `mine=true` is "My Tasks"; without it, what your scope reaches.

    An own-scoped persona sees only its own queues either way — showing a
    clerk work nobody will ever route to them is noise wearing the costume of
    transparency.
    """
    require_reader(principal)
    tasks = get_task_service().list(status=status, queue=queue, kind=kind)
    return [t for t in tasks if _visible(principal, t.queue, mine)]


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


@router.get("/{task_id}", response_model=Task)
def get_task(task_id: str, principal: Principal = Depends(get_principal)) -> Task:
    require_reader(principal)
    task = get_task_service().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return task


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

    # The resume half of suspend/resume: a decision on a task raised by a live
    # runner resumes that run. Each hook no-ops on tasks that are not its kind,
    # so at most one of them acts on any given Task.
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
    for resume in (
        lambda t: get_reimbursement_service().apply_task_decision(t, principal),
        lambda t: get_payable_service().apply_task_decision(t, principal),
        lambda t: get_employee_service().apply_task_decision(t),
        # A steward's entity-resolution decision is the *first* write on
        # that record: a batch sync asserts nothing while the Task is open.
        lambda t: get_sync_service().apply_match_decision(t, principal),
        # And its sibling on the other kind: a proposed cross-source merge
        # already sits in the graph at `in_review`, so this decision flips
        # it or withdraws it rather than asserting anything new.
        lambda t: get_sync_service().apply_merge_decision(t, principal),
    ):
        try:
            resume(decided)
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
