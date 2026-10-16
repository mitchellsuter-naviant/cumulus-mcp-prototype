"""REST surface for the reimbursements table and the live flow run.

Submitting is the trigger of the expense-reimbursement flow: the request runs
synchronously through store → extract → check and stops on its submitter's own
desk to be confirmed, landing as a `pending` row with a Task against it. It
becomes `approved` when that person answers — see the flow's `k1` gate.
Submission is a human act (a person is asking to be paid back), so minted API
keys are refused.

**This door carries bytes.** A submission with no document is answered 400. It
used to be accepted: a caller with a filename and a sentence, no file, got back
an extraction, a set of lines and a graph write — facts with nothing behind
them, indistinguishable afterwards from facts with a receipt. And the note the
submitter wrote is theirs, not the extractor's: it lands as the document's memo
and is never given to the reader.

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
from .tasks.expense_reimbursement.domain import (
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
    out of it ("only requesting $75 of the $110"), the confirm screen shows it
    back, and the journal quotes it — so it travels per document rather than
    per submission. It lands as the document's `memo`.

    There used to be a `hint` beside it, and the note was concatenated into it
    and handed to the extractor as its steering string; `StubReceiptReader`
    regex-parses that string, so `total=500 vendor=corner-bistro` typed into
    either box was an extraction the submitter had authored. Both are gone from
    the wire. Steering survives on `SubmittedDocument.hint`, which only a test,
    a fixture or the seed can reach — the same quarantine
    `payables_api.PayableSubmit` applies on the other side.
    """

    content_base64: str = Field(default="", max_length=8_000_000)
    filename: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=1000)


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
            content_b64=d.content_base64, filename=d.filename, memo=d.note,
        )
        for d in body.documents
    ]
    if not documents and body.image_base64:
        documents = [SubmittedDocument(
            content_b64=body.image_base64, filename=body.filename,
            memo=body.note,
        )]
    # Every door on this side carries bytes. A submission describing a receipt
    # in prose used to be accepted, extracted, projected into lines and written
    # to the graph — facts with no artifact behind them, indistinguishable
    # afterwards from facts with one. `payables_api.submit_payable` closed the
    # same door on the invoice side; this is it on the expense side.
    if not any(d.content_b64 for d in documents):
        raise HTTPException(
            status_code=400,
            detail="Attach a receipt: this door carries bytes, not a story.",
        )
    try:
        request = get_reimbursement_service().submit(
            employee=principal.email,
            documents=documents,
            commentary=body.commentary,
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
