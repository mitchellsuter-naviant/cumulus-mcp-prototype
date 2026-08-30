"""REST surface for the payables table and the live accounts-payable run.

Submitting is the trigger of the accounts-payable flow: the invoice runs
synchronously through one extraction pass → vendor resolution → the three-way
match → the anomaly screen → the governed posting rule, and lands as a row. Submission is a human act (a person is entering a
vendor's invoice), so minted API keys are refused.

Reads are any authenticated role, narrowed to what the reader is accountable
for: a clerk bound to the AP queue works the whole table, and somebody who
merely entered an invoice sees the ones they entered. The table used to hand
every vendor, amount and posting decision in the tenant to anyone signed in.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import Principal, get_principal, require_human, require_reader

# Resolved through the module (deps.get_payable_service()) rather than a
# from-import, so the singleton installed at wiring/test time is the one
# every request sees.
from . import deps
from .payables import InvoiceRecord, PayableState, PayableStats

log = logging.getLogger("cumulus.payables.api")

router = APIRouter(prefix="/api/v1/payables", tags=["payables"])


def get_service():
    return deps.get_payable_service()


class PayableSubmit(BaseModel):
    # An invoice image, base64-encoded. Optional so text-hint demos work
    # without a file; the reader treats hint/filename as extraction input.
    image_base64: str = Field(default="", max_length=8_000_000)
    filename: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=500)


@router.get("", response_model=list[InvoiceRecord])
def list_payables(
    state: PayableState | None = Query(default=None),
    principal: Principal = Depends(get_principal),
) -> list[InvoiceRecord]:
    """The invoices this reader is accountable for — see `visible_to`."""
    require_reader(principal)
    return get_service().list(state=state, principal=principal)


@router.get("/stats", response_model=PayableStats)
def payable_stats(
    principal: Principal = Depends(get_principal),
) -> PayableStats:
    """Counts over the same slice `list_payables` returns, so the strip and the
    table under it can never disagree."""
    require_reader(principal)
    return get_service().stats(principal=principal)


@router.get("/{record_id}", response_model=InvoiceRecord)
def get_payable(
    record_id: str, principal: Principal = Depends(get_principal)
) -> InvoiceRecord:
    require_reader(principal)
    service = get_service()
    record = service.get(record_id)
    # A row outside your slice answers 404, not 403. A 403 would confirm the id
    # exists, which is the one bit an unauthorized caller should not get back.
    if record is None or not service.visible_to(principal)(record):
        raise HTTPException(
            status_code=404, detail=f"Invoice not found: {record_id}"
        )
    return record


@router.post("", response_model=InvoiceRecord, status_code=201)
def submit_payable(
    body: PayableSubmit,
    principal: Principal = Depends(get_principal),
) -> InvoiceRecord:
    require_reader(principal)
    require_human(principal, "submit invoices; sign in as yourself")
    record = get_service().submit(
        submitted_by=principal.email,
        image_b64=body.image_base64,
        filename=body.filename,
        hint=body.note,
    )
    log.info(
        "Invoice submitted via API",
        extra={
            "invoiceId": record.id,
            "state": record.state,
            "actor": principal.uid,
        },
    )
    return record
