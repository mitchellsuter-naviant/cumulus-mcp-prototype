"""Canonical fact paths for an invoice — the vocabulary four things share.

`invoice.total_usd` and `line_items[2].amount_usd` are the same strings in four
places, and that is the whole point of writing them down once: a `Region` says
where a value is, a `FieldVerdict` says what is doubtful about it, a golden set
asserts on it, and a clerk's correction comes back keyed by it. Four systems
naming one field the same way is what lets a box on a page, a highlighted input
and a raw→corrected pair be provably about the same number.

A leaf module on purpose: it imports nothing from this package, so the readers
in `domain.py` and the verdicts in `v8/` can both speak it without either one
importing the other. Everything here is duck-typed over "something shaped like
an `InvoiceExtraction`" for that reason.

Line indices are **0-based in a path and 1-based in a label**: a machine
indexes a list, a person counts rows on a page, and pretending either is the
other has to happen exactly once, here.
"""

from __future__ import annotations

import re
from typing import Any

#: The scalar fields of an invoice, in the order a page prints them — which is
#: the order verdicts come back in and the order a screen reads down.
INVOICE_FIELDS: tuple[str, ...] = (
    "vendor_name", "vendor_tax_id", "invoice_number", "invoice_date",
    "due_date", "po_number", "subtotal_usd", "freight_usd", "tax_usd",
    "total_usd",
)

#: The fields of one billed line.
LINE_FIELDS: tuple[str, ...] = (
    "sku", "description", "quantity", "unit_price_usd", "amount_usd",
)

#: An invoice missing one of these is not an invoice, and a run that posted it
#: would have invented the part that was absent. `po_number` is deliberately
#: not here: plenty of real invoices carry no order, and the three-way match
#: already has a legible answer for that one.
REQUIRED_FIELDS: tuple[str, ...] = (
    "vendor_name", "invoice_number", "invoice_date", "total_usd",
)

#: The path a whole missing line-item block is reported against. Not a field of
#: anything — an invoice with no lines has no line to name — so it is spelled
#: out rather than derived.
LINE_ITEMS = "line_items"

_LABELS: dict[str, str] = {
    "vendor_name": "Vendor", "vendor_tax_id": "Tax id",
    "invoice_number": "Invoice number", "invoice_date": "Invoice date",
    "due_date": "Due date", "po_number": "PO number",
    "subtotal_usd": "Subtotal", "freight_usd": "Freight", "tax_usd": "Tax",
    "total_usd": "Invoice total",
    "sku": "SKU", "description": "description", "quantity": "quantity",
    "unit_price_usd": "unit price", "amount_usd": "amount",
}

_MONEY = frozenset({
    "subtotal_usd", "freight_usd", "tax_usd", "total_usd", "unit_price_usd",
    "amount_usd",
})

_LINE_PATH = re.compile(r"^line_items\[(\d+)\]\.([a-z_]+)$")


def invoice_path(field: str) -> str:
    return f"invoice.{field}"


def line_path(index: int, field: str) -> str:
    """`index` is 0-based, as `Region.field` and the golden sets write it."""
    return f"line_items[{index}].{field}"


def parse_path(path: str) -> tuple[int, str]:
    """`(line index, field)`, with -1 for an invoice-level field.

    Total, like `graph.parse_ref`: a path this does not recognise comes back as
    `(-1, "")` rather than raising. These strings arrive from a model's answer
    and off stored Tasks that outlive the code that wrote them, and a screen
    that cannot render one field is a better outcome than a review page that
    will not open.
    """
    match = _LINE_PATH.match(path)
    if match is not None:
        return int(match.group(1)), match.group(2)
    head, sep, field = path.partition(".")
    if sep and head == "invoice" and field in INVOICE_FIELDS:
        return -1, field
    return -1, ""


def label_for(path: str) -> str:
    """Screen copy for one path — "Invoice total", "Line 3 — amount"."""
    if path == LINE_ITEMS:
        return "Billed lines"
    index, field = parse_path(path)
    if not field:
        return path
    if index < 0:
        return _LABELS.get(field, field)
    return f"Line {index + 1} — {_LABELS.get(field, field)}"


def render(path: str, value: Any) -> str:
    """One value as a screen shows it — money to the cent, quantities bare.

    No thousands separators, deliberately, and this is the one formatting
    decision here that is load-bearing. What this produces lands in an
    **editable input** on the review screen and comes back as the clerk left
    it, so `4,182.50` would round-trip a comma into a number — and would make
    the raw side of a raw→corrected pair differ from the corrected side by
    punctuation alone, which is a correction that never happened.
    """
    _, field = parse_path(path)
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        return f"{value:.2f}" if field in _MONEY else f"{value:g}"
    return str(value)


def value_at(extraction: Any, path: str) -> Any:
    """The typed value one path addresses, or `None` when it addresses nothing."""
    index, field = parse_path(path)
    if not field:
        return None
    if index < 0:
        return getattr(extraction, field, None)
    lines = getattr(extraction, "lines", [])
    if 0 <= index < len(lines):
        return getattr(lines[index], field, None)
    return None


def paths_of(extraction: Any) -> list[str]:
    """Every path this extraction holds a value for, in reading order."""
    paths = [invoice_path(field) for field in INVOICE_FIELDS]
    for index in range(len(getattr(extraction, "lines", []))):
        paths.extend(line_path(index, field) for field in LINE_FIELDS)
    return paths


def is_empty(value: Any) -> bool:
    """Whether a read produced nothing. `0.0` counts as nothing.

    A number the extractor never found comes back as the field's default, which
    for money is `0.0` — indistinguishable from a genuine zero. An invoice
    genuinely totalling nothing is not a case worth protecting at the cost of
    missing the far more common one, where the total simply was not read.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (int, float)):
        return float(value) == 0.0
    return False
