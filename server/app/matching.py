"""The three-way match, kept as a record instead of computed and discarded.

Invoice ↔ purchase order ↔ receiving report. The interesting decision here is
not the arithmetic — it is that the comparison is **retained**. A `MatchSet`
holds every pairing with its variance and the tolerance that judged it, so:

- the clerk reviewing an exception sees exactly which line failed and why,
  rather than a boolean;
- an auditor two years later can ask why this invoice posted and get the
  pairings, not a re-run;
- an eval case can assert on the pairing, which is impossible if the match only
  ever existed as a local variable.

Line matching is an assignment problem and is solved greedily on purpose: an
exact SKU match wins, then description similarity, each PO line consumed once.
Greedy is explainable — "this line matched that line at 0.81 on description" —
and explainable beats optimal when a person has to rule on the result.

Three shapes of miss are modelled distinctly, because they mean different
things to the business:

- **no-po-line** on a goods line: someone billed us for something nobody
  ordered. Always a variance.
- **allowed-no-po** on freight or tax under the governed allowance: normal, and
  not worth a clerk's time.
- **no-receipt**: the goods were ordered and billed but not booked in. Not an
  error — a *sequencing* state. Below the governed receiving threshold the
  invoice may still post; at or above it the match is `blocked`, which is a
  different verdict from `variance` and deserves to be.
"""

from __future__ import annotations

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from .resolution import name_similarity

log = logging.getLogger("cumulus.matching")

LineKind = Literal["goods", "freight", "tax", "discount"]
PairVerdict = Literal[
    "matched", "price-variance", "quantity-variance", "no-po-line",
    "allowed-no-po", "no-receipt",
]
MatchVerdict = Literal["matched", "variance", "unmatched", "blocked"]

# Below this the two descriptions are not the same line item.
_SIMILARITY_FLOOR = 0.45


class MatchTolerances(BaseModel):
    price_variance_pct: float = 2.0
    quantity_variance: float = 0.0
    freight_allowance_usd: float = 150.0
    receiving_required_above_usd: float = 500.0
    unmatched_line_tolerance: int = 0
    source_ref: str = ""
    found_asset: bool = False


def parse_match_tolerances(content: str, source_ref: str = "") -> MatchTolerances:
    values = MatchTolerances(found_asset=True, source_ref=source_ref)
    price = re.search(r"price variance tolerance:?\s*(\d+(?:\.\d+)?)\s*%", content, re.I)
    if price:
        values.price_variance_pct = float(price.group(1))
    quantity = re.search(
        r"quantity variance tolerance:?\s*(\d+(?:\.\d+)?)", content, re.I
    )
    if quantity:
        values.quantity_variance = float(quantity.group(1))
    freight = re.search(
        r"freight and tax allowance:?\s*\$([\d,]+(?:\.\d{1,2})?)", content, re.I
    )
    if freight:
        values.freight_allowance_usd = float(freight.group(1).replace(",", ""))
    receiving = re.search(
        r"receiving report required above:?\s*\$([\d,]+(?:\.\d{1,2})?)", content, re.I
    )
    if receiving:
        values.receiving_required_above_usd = float(
            receiving.group(1).replace(",", "")
        )
    unmatched = re.search(
        r"unmatched line tolerance:?\s*(\d+)\s*lines?", content, re.I
    )
    if unmatched:
        values.unmatched_line_tolerance = int(unmatched.group(1))
    return values


class MatchLine(BaseModel):
    """One line from any of the three documents, in one shape.

    Deliberately uniform: the matcher should not care which document a line
    came from, only what it says. `parent_ref` carries the document it belongs
    to so a pairing can name it.
    """

    ordinal: int
    parent_ref: str = ""
    parent_number: str = ""
    sku: str = ""
    description: str = ""
    quantity: float = 0.0
    unit_price_usd: float = 0.0
    amount_usd: float = 0.0
    kind: LineKind = "goods"


class LinePairing(BaseModel):
    invoice_ordinal: int
    description: str = ""
    sku: str = ""
    kind: LineKind = "goods"
    invoice_quantity: float = 0.0
    invoice_unit_price_usd: float = 0.0
    invoice_amount_usd: float = 0.0

    po_number: str = ""
    po_ordinal: int = 0
    po_quantity: float = 0.0
    po_unit_price_usd: float = 0.0
    po_match_score: float = 0.0

    receipt_number: str = ""
    receipt_ordinal: int = 0
    received_quantity: float = 0.0

    price_variance_usd: float = 0.0
    price_variance_pct: float = 0.0
    quantity_variance: float = 0.0
    verdict: PairVerdict = "matched"
    reason: str = ""

    @property
    def clean(self) -> bool:
        return self.verdict in ("matched", "allowed-no-po")


class MatchResult(BaseModel):
    verdict: MatchVerdict = "matched"
    pairings: list[LinePairing] = Field(default_factory=list)
    matched_lines: int = 0
    unmatched_lines: int = 0
    price_variance_usd: float = 0.0
    quantity_variance: float = 0.0
    po_numbers: list[str] = Field(default_factory=list)
    receipt_numbers: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    tolerance_ref: str = ""

    def summary(self) -> str:
        if self.verdict == "matched":
            return (
                f"matched {self.matched_lines} line(s) against "
                f"{len(self.po_numbers)} PO(s) and "
                f"{len(self.receipt_numbers)} receipt(s)"
            )
        return (
            f"{self.verdict}: {self.matched_lines} matched, "
            f"{self.unmatched_lines} not — "
            + "; ".join(self.notes[:3])
        )


def _pair_score(invoice_line: MatchLine, other: MatchLine) -> float:
    if invoice_line.sku and other.sku:
        if invoice_line.sku.strip().lower() == other.sku.strip().lower():
            return 1.0
    return name_similarity(invoice_line.description, other.description)


def match_invoice(
    invoice_lines: list[MatchLine],
    po_lines: list[MatchLine],
    receipt_lines: list[MatchLine],
    tolerances: MatchTolerances,
    *,
    invoice_total_usd: float = 0.0,
) -> MatchResult:
    """Pair each invoice line with a PO line and a receiving line, then judge."""
    result = MatchResult(tolerance_ref=tolerances.source_ref)
    available_po = list(po_lines)
    available_receipts = list(receipt_lines)
    result.po_numbers = sorted({line.parent_number for line in po_lines if line.parent_number})
    result.receipt_numbers = sorted(
        {line.parent_number for line in receipt_lines if line.parent_number}
    )

    for invoice_line in invoice_lines:
        pairing = LinePairing(
            invoice_ordinal=invoice_line.ordinal,
            description=invoice_line.description,
            sku=invoice_line.sku,
            kind=invoice_line.kind,
            invoice_quantity=invoice_line.quantity,
            invoice_unit_price_usd=invoice_line.unit_price_usd,
            invoice_amount_usd=invoice_line.amount_usd,
        )

        # -- the PO leg ----------------------------------------------------
        best_po, best_score = None, 0.0
        for candidate in available_po:
            score = _pair_score(invoice_line, candidate)
            if score > best_score:
                best_po, best_score = candidate, score
        if best_po is not None and best_score >= _SIMILARITY_FLOOR:
            available_po.remove(best_po)
            pairing.po_number = best_po.parent_number
            pairing.po_ordinal = best_po.ordinal
            pairing.po_quantity = best_po.quantity
            pairing.po_unit_price_usd = best_po.unit_price_usd
            pairing.po_match_score = round(best_score, 3)
            unit_delta = round(
                invoice_line.unit_price_usd - best_po.unit_price_usd, 4
            )
            pairing.price_variance_usd = round(
                unit_delta * (invoice_line.quantity or 1), 2
            )
            if best_po.unit_price_usd:
                pairing.price_variance_pct = round(
                    abs(unit_delta) / best_po.unit_price_usd * 100, 3
                )
            if pairing.price_variance_pct > tolerances.price_variance_pct:
                pairing.verdict = "price-variance"
                pairing.reason = (
                    f"unit price ${invoice_line.unit_price_usd:,.2f} is "
                    f"{pairing.price_variance_pct:.1f}% off the PO's "
                    f"${best_po.unit_price_usd:,.2f} (tolerance "
                    f"{tolerances.price_variance_pct:.1f}%)"
                )
        elif invoice_line.kind in ("freight", "tax") and (
            invoice_line.amount_usd <= tolerances.freight_allowance_usd
        ):
            pairing.verdict = "allowed-no-po"
            pairing.reason = (
                f"{invoice_line.kind} line at ${invoice_line.amount_usd:,.2f} is "
                f"within the ${tolerances.freight_allowance_usd:,.2f} allowance "
                "and carries no PO line by nature"
            )
        else:
            pairing.verdict = "no-po-line"
            pairing.reason = (
                "nothing on the purchase order resembles this line"
                + (
                    f" (closest {best_score:.2f})" if best_po is not None else
                    " (no purchase order linked)"
                )
            )

        # -- the receiving leg ---------------------------------------------
        if pairing.verdict in ("matched", "price-variance") and pairing.po_number:
            best_receipt, receipt_score = None, 0.0
            for candidate in available_receipts:
                score = _pair_score(invoice_line, candidate)
                if score > receipt_score:
                    best_receipt, receipt_score = candidate, score
            if best_receipt is not None and receipt_score >= _SIMILARITY_FLOOR:
                available_receipts.remove(best_receipt)
                pairing.receipt_number = best_receipt.parent_number
                pairing.receipt_ordinal = best_receipt.ordinal
                pairing.received_quantity = best_receipt.quantity
                pairing.quantity_variance = round(
                    invoice_line.quantity - best_receipt.quantity, 3
                )
                if pairing.quantity_variance > tolerances.quantity_variance:
                    pairing.verdict = "quantity-variance"
                    pairing.reason = (
                        f"billed {invoice_line.quantity:g} but "
                        f"{best_receipt.quantity:g} was received on "
                        f"{best_receipt.parent_number}"
                    )
            elif invoice_line.amount_usd >= tolerances.receiving_required_above_usd:
                pairing.verdict = "no-receipt"
                pairing.reason = (
                    f"no receiving line for this item, and ${invoice_line.amount_usd:,.2f} "
                    f"is at or above the "
                    f"${tolerances.receiving_required_above_usd:,.2f} threshold "
                    "that requires goods to be booked in first"
                )
            else:
                pairing.reason = (
                    pairing.reason
                    or (
                        "matched to the PO; under the "
                        f"${tolerances.receiving_required_above_usd:,.2f} "
                        "receiving threshold, so no receipt is required"
                    )
                )

        if not pairing.reason and pairing.verdict == "matched":
            pairing.reason = (
                f"PO {pairing.po_number} line {pairing.po_ordinal} at "
                f"{pairing.po_match_score:.2f}"
                + (
                    f", received on {pairing.receipt_number}"
                    if pairing.receipt_number else ""
                )
            )
        result.pairings.append(pairing)

    result.matched_lines = sum(1 for p in result.pairings if p.clean)
    result.unmatched_lines = len(result.pairings) - result.matched_lines
    result.price_variance_usd = round(
        sum(p.price_variance_usd for p in result.pairings if p.verdict == "price-variance"),
        2,
    )
    result.quantity_variance = round(
        sum(
            p.quantity_variance for p in result.pairings
            if p.verdict == "quantity-variance"
        ),
        3,
    )
    result.notes = [
        f"line {p.invoice_ordinal}: {p.reason}"
        for p in result.pairings if not p.clean
    ]

    if not po_lines:
        result.verdict = "unmatched"
        result.notes.insert(
            0,
            "no purchase order is linked to this invoice — nothing to match "
            "against",
        )
    elif any(p.verdict == "no-receipt" for p in result.pairings):
        result.verdict = "blocked"
    elif result.unmatched_lines > tolerances.unmatched_line_tolerance:
        result.verdict = "variance"
    else:
        result.verdict = "matched"

    # Consumed nothing on a leftover PO line is not an error — a partial
    # invoice against an open PO is the normal case — but it is worth saying.
    if available_po:
        result.notes.append(
            f"{len(available_po)} PO line(s) not billed on this invoice "
            "(partial invoicing against an open order)"
        )
    log.info(
        "Three-way match evaluated",
        extra={
            "verdict": result.verdict, "matched": result.matched_lines,
            "unmatched": result.unmatched_lines,
            "priceVarianceUsd": result.price_variance_usd,
        },
    )
    return result
