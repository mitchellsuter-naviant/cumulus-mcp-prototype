"""REST surface for the reimbursements table and the live flow run.

Submitting is the trigger of the expense-reimbursement flow: the request
runs synchronously through extract → policy → rule and lands as a row.
Submission is a human act (a person is asking to be paid back), so minted
API keys are refused; reads are any authenticated role — the table is a
data-role dataset the whole console may see.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import Principal, get_principal, require_reader
from .deps import get_reimbursement_service
from .reimbursements import (
    ReimbursementRequest,
    ReimbursementState,
    ReimbursementStats,
)

log = logging.getLogger("cumulus.reimbursements.api")

router = APIRouter(prefix="/api/v1/reimbursements", tags=["reimbursements"])


class ReimbursementSubmit(BaseModel):
    # A receipt image, base64-encoded. Optional so text-hint demos work
    # without a file; the reader treats hint/filename as extraction input.
    image_base64: str = Field(default="", max_length=8_000_000)
    filename: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=500)


def _require_human(principal: Principal) -> None:
    if principal.key_id is not None:
        raise HTTPException(
            status_code=403,
            detail="API keys cannot submit reimbursements; sign in as yourself.",
        )


@router.get("", response_model=list[ReimbursementRequest])
def list_reimbursements(
    state: ReimbursementState | None = Query(default=None),
    mine: bool = Query(default=False),
    principal: Principal = Depends(get_principal),
) -> list[ReimbursementRequest]:
    require_reader(principal)
    return get_reimbursement_service().list(
        state=state, employee=principal.uid if mine else None
    )


@router.get("/stats", response_model=ReimbursementStats)
def reimbursement_stats(
    principal: Principal = Depends(get_principal),
) -> ReimbursementStats:
    require_reader(principal)
    return get_reimbursement_service().stats()


@router.get("/{request_id}", response_model=ReimbursementRequest)
def get_reimbursement(
    request_id: str, principal: Principal = Depends(get_principal)
) -> ReimbursementRequest:
    require_reader(principal)
    request = get_reimbursement_service().get(request_id)
    if request is None:
        raise HTTPException(
            status_code=404, detail=f"Reimbursement not found: {request_id}"
        )
    return request


@router.post("", response_model=ReimbursementRequest, status_code=201)
def submit_reimbursement(
    body: ReimbursementSubmit,
    principal: Principal = Depends(get_principal),
) -> ReimbursementRequest:
    require_reader(principal)
    _require_human(principal)
    request = get_reimbursement_service().submit(
        employee=principal.uid,
        image_b64=body.image_base64,
        filename=body.filename,
        hint=body.note,
    )
    log.info(
        "Reimbursement submitted via API",
        extra={
            "reimbursementId": request.id,
            "state": request.state,
            "actor": principal.uid,
        },
    )
    return request
