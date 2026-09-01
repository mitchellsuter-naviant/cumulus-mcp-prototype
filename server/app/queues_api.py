"""REST surface for the queue registry — the desks and who holds them.

Reading is any authenticated principal's right: knowing that the AP desk
exists and that three people hold it is how you know where to send something,
and hiding it helps nobody. Writing is admin, because queue membership *is* an
authorization input — `require_queue` gates every Task decision on it — so
adding yourself to a desk would otherwise be a self-service grant of the
authority to approve invoices.

Personal desks (`person-…`) are minted by a Flow resolving an approver, not by
hand, so they are listable and editable but not creatable here: a second one
for the same person under a different slug would split their inbox in two with
nothing to say which half is real.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import deps
from .auth import Principal, get_principal, require_reader, require_role
from .queues import (
    DEFAULT_CLAIM_TTL_SECONDS,
    Queue,
    QueueExistsError,
    QueueNotFoundError,
)

log = logging.getLogger("cumulus.queues.api")

router = APIRouter(prefix="/api/v1/queues", tags=["queues"])


class QueueView(Queue):
    """A desk, with the two facts only the service can compute.

    `holders` unions the registry with the persona roster, because
    `require_queue` accepts either and a screen showing one of them would
    report an unheld desk that three people can in fact decide. `open_tasks`
    is here because "a queue with work and nobody holding it" is the single
    most useful thing this screen can tell an administrator, and it cannot be
    seen from either side alone.
    """

    holders: list[str] = Field(default_factory=list)
    open_tasks: int = 0
    high_priority: int = 0
    yours: bool = False


class QueueCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=1000)
    claim_ttl_seconds: int = Field(default=DEFAULT_CLAIM_TTL_SECONDS, ge=60, le=86400)


class QueuePatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    claim_ttl_seconds: int | None = Field(default=None, ge=60, le=86400)


class MemberAdd(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    label: str = Field(default="", max_length=120)


def _view(queue: Queue, principal: Principal) -> QueueView:
    service = deps.get_queue_service()
    tasks = deps.get_task_service().list(status="open", queue=queue.slug)
    return QueueView(
        **queue.model_dump(),
        holders=service.holders_of(queue.slug),
        open_tasks=len(tasks),
        high_priority=sum(1 for t in tasks if t.priority == "high"),
        yours=principal.owns_queue(queue.slug),
    )


@router.get("", response_model=list[QueueView])
def list_queues(
    include_personal: bool = Query(default=True),
    principal: Principal = Depends(get_principal),
) -> list[QueueView]:
    require_reader(principal)
    service = deps.get_queue_service()
    return [_view(q, principal) for q in service.list(include_personal)]


@router.get("/mine", response_model=list[str])
def my_queues(principal: Principal = Depends(get_principal)) -> list[str]:
    """The desks this credential holds — what every gate in the system reads.

    Its own endpoint because "why can I not decide this" is a question the
    console has to be able to answer without the user reading the source, and
    the answer is exactly this list.
    """
    require_reader(principal)
    return sorted(principal.queues)


@router.get("/{slug}", response_model=QueueView)
def get_queue(
    slug: str, principal: Principal = Depends(get_principal)
) -> QueueView:
    require_reader(principal)
    queue = deps.get_queue_service().get(slug)
    if queue is None:
        raise HTTPException(status_code=404, detail=f"Queue not found: {slug}")
    return _view(queue, principal)


@router.post("", response_model=QueueView, status_code=201)
def create_queue(
    body: QueueCreate, principal: Principal = Depends(get_principal)
) -> QueueView:
    require_role(principal, "admin")
    try:
        queue = deps.get_queue_service().create(
            body.slug, body.name, body.description,
            created_by=principal.uid,
            claim_ttl_seconds=body.claim_ttl_seconds,
        )
    except QueueExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _view(queue, principal)


@router.patch("/{slug}", response_model=QueueView)
def update_queue(
    slug: str, body: QueuePatch, principal: Principal = Depends(get_principal)
) -> QueueView:
    require_role(principal, "admin")
    try:
        queue = deps.get_queue_service().update(
            slug, name=body.name, description=body.description,
            claim_ttl_seconds=body.claim_ttl_seconds,
        )
    except QueueNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _view(queue, principal)


@router.post("/{slug}/members", response_model=QueueView, status_code=201)
def add_member(
    slug: str, body: MemberAdd, principal: Principal = Depends(get_principal)
) -> QueueView:
    """Bind a person to a desk.

    Admin-only and deliberately not self-service: this grants the authority to
    decide everything that routes here.
    """
    require_role(principal, "admin")
    try:
        queue = deps.get_queue_service().add_member(
            slug, body.email, body.label, added_by=principal.uid
        )
    except QueueNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _view(queue, principal)


@router.delete("/{slug}/members/{email}", response_model=QueueView)
def remove_member(
    slug: str, email: str, principal: Principal = Depends(get_principal)
) -> QueueView:
    require_role(principal, "admin")
    try:
        queue = deps.get_queue_service().remove_member(slug, email)
    except QueueNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        # A roster-declared binding: refused rather than silently reverted at
        # the next restart. See `QueueService.remove_member`.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _view(queue, principal)


@router.delete("/{slug}", status_code=204)
def delete_queue(slug: str, principal: Principal = Depends(get_principal)) -> None:
    """Retire a desk. Refused while work is still sitting on it.

    Deleting a queue with open Tasks would orphan them: they would keep a
    `queue` nothing resolves, so nobody would hold them and no screen would
    list them. Move the work first.
    """
    require_role(principal, "admin")
    open_here = deps.get_task_service().list(status="open", queue=slug)
    if open_here:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{slug}' still has {len(open_here)} open task(s). Decide or "
                "reroute them before retiring the desk — deleting it now "
                "would leave them with no holder and no screen."
            ),
        )
    if not deps.get_queue_service().delete(slug):
        raise HTTPException(status_code=404, detail=f"Queue not found: {slug}")
