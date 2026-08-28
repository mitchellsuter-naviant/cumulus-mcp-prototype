"""REST surface for the payables table and the live accounts-payable run.

Submitting is the trigger of the accounts-payable flow: the invoice runs
synchronously through one extraction pass → vendor resolution → the three-way
match → the anomaly screen → the governed posting rule, and lands as a row. Submission is a human act (a person is entering a
vendor's invoice), so minted API keys are refused; reads are any
authenticated role — the table is a data-role dataset the whole console
may see.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import Principal, get_principal, require_reader

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


def _require_human(principal: Principal) -> None:
    if principal.key_id is not None:
        raise HTTPException(
            status_code=403,
            detail="API keys cannot submit invoices; sign in as yourself.",
        )


@router.get("", response_model=list[InvoiceRecord])
def list_payables(
    state: PayableState | None = Query(default=None),
    principal: Principal = Depends(get_principal),
) -> list[InvoiceRecord]:
    require_reader(principal)
    return get_service().list(state=state)


@router.get("/stats", response_model=PayableStats)
def payable_stats(
    principal: Principal = Depends(get_principal),
) -> PayableStats:
    require_reader(principal)
    return get_service().stats()


@router.get("/{record_id}", response_model=InvoiceRecord)
def get_payable(
    record_id: str, principal: Principal = Depends(get_principal)
) -> InvoiceRecord:
    require_reader(principal)
    record = get_service().get(record_id)
    if record is None:
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
    _require_human(principal)
    record = get_service().submit(
        submitted_by=principal.uid,
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
