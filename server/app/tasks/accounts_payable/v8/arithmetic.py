"""`check-arithmetic` — the invoice checked against itself, naming names.

A Rule in the doctrine's sense: it evaluates what the flow already gathered and
fetches nothing. Everything it needs arrives as arguments — the extraction, and
the tolerances a transform step read out of the governed posting policy — so it
is pure, replayable, and gradeable without a world.

What it is *for* is the part worth being careful about. An arithmetic check that
answered "this invoice does not add up" would be no better than the coarse
lines-vs-total clause the posting rule already had: true, unhelpful, and
un-actionable. Every failure here names the **field path** it implicates,
because those paths are what become the boxes on the page and the focused
inputs beside them. "Line 3's amount is not 4 × $92.40" is a question a clerk
answers in four seconds; "the invoice does not add up" is a document they have
to re-read.

Five checks, and each one names a different field:

1. per line, `quantity × unit_price ≈ amount` → the line's amount;
2. the goods lines sum to the subtotal → the subtotal;
3. the total agrees with one of the document's two accounts of it —
   `subtotal + freight + tax`, or the billed lines → the total;
4. the required fields are there at all → the missing field;
5. the dates are sane (due not before issued) → the due date.

The tolerances are governed knowledge, never literals: what counts as "close
enough" on a total is a finance decision, and a customer who wants a cent of
slack should be able to edit a page rather than wait for a deploy.
"""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel

from ..domain import InvoiceExtraction
from ..paths import (
    LINE_ITEMS,
    REQUIRED_FIELDS,
    invoice_path,
    is_empty,
    label_for,
    line_path,
)
from .verdicts import FieldFinding


class ArithmeticTolerances(BaseModel):
    """What "close enough" means, read from the posting policy at run time.

    Defaults are what the check runs on when the asset is missing or says
    nothing — a fresh deployment still checks — and they are deliberately tight:
    a cent of rounding on a line, a nickel across a whole invoice. `source_ref`
    travels so the step that consumed them can put the revision in its read-set.
    """

    line_amount_usd: float = 0.01
    document_usd: float = 0.05
    min_field_confidence: float = 0.70
    source_ref: str = ""
    found_asset: bool = False


def parse_arithmetic_tolerances(content: str, ref: str) -> ArithmeticTolerances:
    """The governed values, parsed the way this flow parses its others.

    Prose with numbers in it rather than a config block, for the reason the
    posting policy is: the page is written for the finance owner who edits it,
    and a value they can read a sentence about is one they can change on
    purpose. A line the parser does not find leaves the default in place, so a
    partially-edited policy degrades one value at a time.
    """
    values = ArithmeticTolerances(source_ref=ref, found_asset=bool(content))
    line = re.search(
        r"line arithmetic tolerance:?\s*\$([\d,]+(?:\.\d{1,2})?)",
        content, re.IGNORECASE,
    )
    if line:
        values.line_amount_usd = float(line.group(1).replace(",", ""))
    document = re.search(
        r"document arithmetic tolerance:?\s*\$([\d,]+(?:\.\d{1,2})?)",
        content, re.IGNORECASE,
    )
    if document:
        values.document_usd = float(document.group(1).replace(",", ""))
    confidence = re.search(
        r"minimum field confidence:?\s*(\d+(?:\.\d+)?)", content, re.IGNORECASE
    )
    if confidence:
        values.min_field_confidence = float(confidence.group(1))
    return values


def _iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat((value or "").strip()[:10])
    except ValueError:
        return None


def check_arithmetic(
    extraction: InvoiceExtraction, tolerances: ArithmeticTolerances
) -> list[FieldFinding]:
    """The whole check. Pure: it reads its two arguments and nothing else."""
    findings: list[FieldFinding] = []

    # 1 — required fields. First, because a total that is not there cannot
    # disagree with anything, and reporting both would ask a clerk two
    # questions about one hole.
    missing: set[str] = set()
    for field in REQUIRED_FIELDS:
        if is_empty(getattr(extraction, field, None)):
            missing.add(field)
            findings.append(FieldFinding(
                path=invoice_path(field), status="missing",
                reason=(
                    f"{label_for(invoice_path(field))} is required to post an "
                    "invoice and the extractor did not find one on the page"
                ),
            ))
    if not extraction.lines:
        findings.append(FieldFinding(
            path=LINE_ITEMS, status="missing",
            reason=(
                "the extractor found no billed lines at all, so there is "
                "nothing to match against the purchase order"
            ),
        ))

    # 2 — per line: quantity × unit price is the amount.
    for index, line in enumerate(extraction.lines):
        expected = round(line.quantity * line.unit_price_usd, 2)
        if abs(expected - line.amount_usd) <= tolerances.line_amount_usd:
            continue
        # A line with no quantity or no unit price on the page is a line the
        # extractor read as a lump sum, which plenty of freight and tax lines
        # genuinely are. Nothing to disagree with, so nothing is claimed.
        if not line.quantity or not line.unit_price_usd:
            continue
        findings.append(FieldFinding(
            path=line_path(index, "amount_usd"), status="inconsistent",
            reason=(
                f"{line.quantity:g} × ${line.unit_price_usd:,.2f} is "
                f"${expected:,.2f}, but this line reads ${line.amount_usd:,.2f}"
            ),
        ))

    # 3 — the goods lines sum to the subtotal. Goods only: freight and tax are
    # counted once, in the total, and adding them here would make every
    # correctly-read invoice with a freight line fail.
    goods = round(
        sum(line.amount_usd for line in extraction.lines if line.kind == "goods"), 2
    )
    if (
        extraction.lines
        and not is_empty(extraction.subtotal_usd)
        and abs(goods - extraction.subtotal_usd) > tolerances.document_usd
    ):
        findings.append(FieldFinding(
            path=invoice_path("subtotal_usd"), status="inconsistent",
            reason=(
                f"the billed goods lines add up to ${goods:,.2f}, but the "
                f"subtotal reads ${extraction.subtotal_usd:,.2f}"
            ),
        ))

    # 4 — the total, against the document's own two accounts of it.
    #
    # Either identity is enough, and that is not laxity. Real invoices state
    # their freight and tax in two places — as summary fields, or as lines in
    # the table, and plenty of vendors do one and not the other. Demanding both
    # identities would flag every invoice that bills freight as a line with no
    # summary field, which is a large fraction of them: a check that fires on
    # correctly-read invoices is a check a desk stops reading within a week.
    # A total that agrees with *neither* account was misread.
    if "total_usd" not in missing:
        parts = round(
            extraction.subtotal_usd + extraction.freight_usd + extraction.tax_usd, 2
        )
        billed = round(sum(line.amount_usd for line in extraction.lines), 2)
        agrees = (
            abs(parts - extraction.total_usd) <= tolerances.document_usd
            or (
                bool(extraction.lines)
                and abs(billed - extraction.total_usd) <= tolerances.document_usd
            )
        )
        if not agrees:
            findings.append(FieldFinding(
                path=invoice_path("total_usd"), status="inconsistent",
                reason=(
                    f"the billed lines add up to ${billed:,.2f} and "
                    f"subtotal + freight + tax is ${parts:,.2f}, but the "
                    f"invoice total reads ${extraction.total_usd:,.2f}"
                ),
            ))

    # 5 — the dates. Only the one comparison that is always wrong when it
    # fails: an invoice due before it was issued was misread, whatever the
    # vendor's terms are. How *long* the terms are is the vendor's business and
    # not this check's.
    issued = _iso_date(extraction.invoice_date)
    due = _iso_date(extraction.due_date)
    if extraction.invoice_date and issued is None:
        findings.append(FieldFinding(
            path=invoice_path("invoice_date"), status="inconsistent",
            reason=(
                f"`{extraction.invoice_date}` is not a date this can read; "
                "the prompt asks for ISO 8601 (YYYY-MM-DD)"
            ),
        ))
    if extraction.due_date and due is None:
        findings.append(FieldFinding(
            path=invoice_path("due_date"), status="inconsistent",
            reason=(
                f"`{extraction.due_date}` is not a date this can read; "
                "the prompt asks for ISO 8601 (YYYY-MM-DD)"
            ),
        ))
    if issued is not None and due is not None and due < issued:
        findings.append(FieldFinding(
            path=invoice_path("due_date"), status="inconsistent",
            reason=(
                f"the invoice is due {due.isoformat()}, before it was issued "
                f"on {issued.isoformat()}"
            ),
        ))
    return findings


def summarize(findings: list[FieldFinding]) -> str:
    """The `check-arithmetic` step's journal line."""
    if not findings:
        return "the invoice agrees with itself: lines, subtotal, total and dates"
    return f"{len(findings)} disagreement(s): " + "; ".join(
        f"{finding.path} — {finding.reason}" for finding in findings[:4]
    ) + (" …" if len(findings) > 4 else "")
