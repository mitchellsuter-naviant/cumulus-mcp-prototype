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
from .documents import DocumentTooLargeError
from .reimbursements import (
    ReimbursementRequest,
    ReimbursementState,
    ReimbursementStats,
    SubmittedDocument,
)

log = logging.getLogger("cumulus.reimbursements.api")

router = APIRouter(prefix="/api/v1/reimbursements", tags=["reimbursements"])


class SubmittedDocumentBody(BaseModel):
    """One artifact in a submission, with the submitter's note about it.

    The note is a real input to the run — the projection reads a stated claim
    out of it ("only requesting $75 of the $110") and the journal quotes it —
    so it travels per document rather than per submission.
    """

    content_base64: str = Field(default="", max_length=8_000_000)
    filename: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=1000)
    # Local-dev steering for the stub extractor (`total=64.20 compound`). No
    # meaning in a deployment with a real reader.
    hint: str = Field(default="", max_length=200)


class ReimbursementSubmit(BaseModel):
    """A submission: several documents, and the submitter's own framing.

    The single-document fields are still accepted, because one receipt is the
    common case and there is no reason to make a caller wrap it in a list.
    """

    documents: list[SubmittedDocumentBody] = Field(default_factory=list, max_length=20)
    commentary: str = Field(default="", max_length=4000)
    image_base64: str = Field(default="", max_length=8_000_000)
    filename: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=1000)


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
    documents = [
        SubmittedDocument(
            content_b64=d.content_base64, filename=d.filename, note=d.note,
            hint=d.hint,
        )
        for d in body.documents
    ]
    try:
        request = get_reimbursement_service().submit(
            employee=principal.uid,
            documents=documents,
            commentary=body.commentary,
            image_b64=body.image_base64,
            filename=body.filename,
            hint=body.note,
        )
    except DocumentTooLargeError as exc:
        # 413 rather than 500: the caller can act on this, and the limit is a
        # property of the deployment's document-store adapter, so say it.
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    log.info(
        "Reimbursement submitted via API",
        extra={
            "reimbursementId": request.id,
            "runId": request.run_id,
            "state": request.state,
            "documents": len(request.documents),
            "lines": len(request.lines),
            "actor": principal.uid,
        },
    )
    return request
