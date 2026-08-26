"""REST surface for the Task Queue — the console's HITL spine.

Reads are open to any authenticated role: seeing the queue is how work
reaches people. Deciding is different in kind from every other write in the
API: it *is* the human judgment the doctrine routes through Tasks, so a
minted API key is refused outright — a key deciding a Task would be an agent
approving its own proposal. Role-wise `user` suffices: deciding an item in
your queue is clerk work, not registry administration.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import Principal, get_principal, require_reader
from .deps import (
    get_employee_service,
    get_payable_service,
    get_reimbursement_service,
    get_task_service,
)
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


def _require_human(principal: Principal) -> None:
    """Judgment is the one thing that cannot be delegated to a credential."""
    if principal.key_id is not None:
        log.warning(
            "API key attempted to decide a task", extra={"keyId": principal.key_id}
        )
        raise HTTPException(
            status_code=403,
            detail="API keys cannot decide tasks; a task is a human judgment.",
        )


@router.get("", response_model=list[Task])
def list_tasks(
    status: TaskStatus | None = Query(default=None),
    queue: str | None = Query(default=None),
    kind: TaskKind | None = Query(default=None),
    principal: Principal = Depends(get_principal),
) -> list[Task]:
    require_reader(principal)
    return get_task_service().list(status=status, queue=queue, kind=kind)


@router.get("/stats", response_model=TaskStats)
def task_stats(principal: Principal = Depends(get_principal)) -> TaskStats:
    require_reader(principal)
    return get_task_service().stats()


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
    _require_human(principal)
    try:
        decided = get_task_service().decide(
            task_id,
            verdict=body.verdict,
            decided_by=principal.uid,
            note=body.note,
            correction=body.correction,
        )
        # The resume half of suspend/resume: a decision on a task raised by a
        # live runner resumes that run. Each hook no-ops on unrelated tasks,
        # and a hook failure never un-decides the task — the judgment stands
        # and the stuck resume surfaces in logs.
        for resume in (
            get_reimbursement_service().apply_task_decision,
            get_payable_service().apply_task_decision,
            get_employee_service().apply_task_decision,
        ):
            try:
                resume(decided)
            except Exception:
                log.exception(
                    "Task decision hook failed", extra={"taskId": task_id}
                )
        return decided
    except TaskNotFoundError:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    except TaskAlreadyDecidedError:
        # 409, not idempotent-200: a second decider must learn the judgment
        # already happened, not be told their verdict was recorded.
        raise HTTPException(status_code=409, detail="Task already decided")
