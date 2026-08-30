"""REST surface for the reimbursements table and the live flow run.

Submitting is the trigger of the expense-reimbursement flow: the request
runs synchronously through extract → policy → rule and lands as a row.
Submission is a human act (a person is asking to be paid back), so minted
API keys are refused.

A row belongs to an employee, and an employee is their email — the graph
keys them on it — so that is what "me" means here. Reads are any
authenticated role, narrowed to what the reader is accountable for: an
approver sees the queue they approve, and somebody who only submits sees what
they submitted.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import Principal, get_principal, require_human, require_reader
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


@router.get("", response_model=list[ReimbursementRequest])
def list_reimbursements(
    state: ReimbursementState | None = Query(default=None),
    mine: bool = Query(default=False),
    principal: Principal = Depends(get_principal),
) -> list[ReimbursementRequest]:
    """`mine=true` is "my expenses"; without it, the rows you are accountable
    for — your own, plus the queue you approve. See `visible_to`.

    `mine` stays a real filter rather than a synonym for the default, because
    an approver's own receipts and the queue they approve are two different
    questions and both get asked.
    """
    require_reader(principal)
    return get_reimbursement_service().list(
        state=state,
        employee=principal.email if mine else None,
        principal=principal,
    )


@router.get("/stats", response_model=ReimbursementStats)
def reimbursement_stats(
    principal: Principal = Depends(get_principal),
) -> ReimbursementStats:
    """Counts over the same slice `list_reimbursements` returns, so the strip
    and the table under it can never disagree."""
    require_reader(principal)
    return get_reimbursement_service().stats(principal=principal)


@router.get("/{request_id}", response_model=ReimbursementRequest)
def get_reimbursement(
    request_id: str, principal: Principal = Depends(get_principal)
) -> ReimbursementRequest:
    require_reader(principal)
    service = get_reimbursement_service()
    request = service.get(request_id)
    # A row outside your slice answers 404, not 403. A 403 would confirm the id
    # exists, which is the one bit an unauthorized caller should not get back.
    if request is None or not service.visible_to(principal)(request):
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
    require_human(principal, "submit reimbursements; sign in as yourself")
    documents = [
        SubmittedDocument(
            content_b64=d.content_base64, filename=d.filename, note=d.note,
            hint=d.hint,
        )
        for d in body.documents
    ]
    try:
        request = get_reimbursement_service().submit(
            employee=principal.email,
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
