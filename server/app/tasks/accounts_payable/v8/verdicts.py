"""What a clerk is asked about — the seam between this flow and its screen.

Written as a contract rather than as whatever the screen happened to need:
`FieldVerdict` is the whole of what crosses. One rule holds the two sides
together — a field is named by its **canonical fact path** (`invoice.total_usd`,
`line_items[2].amount_usd`), the same string `Region.field` carries, the same
string a golden set asserts on, and the same string a correction comes back
keyed by. See `..paths`, where that vocabulary is written down once. A box on
the page, a doubt about a number and the clerk's answer to that doubt are then
provably about one field, with no lookup table anywhere between them.

Task-specific, deliberately. Which fields an invoice has, which of them are
required, and what to call them on a screen are facts about accounts payable —
not about documents in general — so this lives in the version package that
raises the verdicts rather than beside `Region`, which is the platform's.

The statuses, and the difference a clerk sees:

- `low_confidence` — the extractor read something and said it was unsure.
- `inconsistent` — the arithmetic disagrees with itself. The value may be
  perfectly legible and still wrong, which is the case a confidence score
  cannot reach.
- `missing` — required, and not on the page at all.
- `ok` — nothing to answer. Carried anyway, because the screen renders the
  whole extraction and needs to know which of it is context.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ....documents import Region
from ..domain import InvoiceExtraction
from ..paths import label_for, paths_of, render, value_at

FieldStatus = Literal["ok", "low_confidence", "inconsistent", "missing"]

#: Worst first. A field that is both unreadable and arithmetically wrong is one
#: question to a clerk rather than two, and this is the order that question is
#: asked in — an absent number is not "low confidence", it is absent.
_SEVERITY: dict[str, int] = {
    "missing": 3, "inconsistent": 2, "low_confidence": 1, "ok": 0,
}


class FieldVerdict(BaseModel):
    """One field of one invoice, as the review screen receives it."""

    #: The canonical fact path. See the module docstring: this is the join.
    path: str
    #: Screen copy — "Invoice total", "Line 3 — amount".
    label: str
    #: As read, rendered for display. A string because the screen renders it
    #: and a clerk edits it as text; the flow keeps the typed value.
    value: str = ""
    #: 0..1 from the extractor. 0.0 means it did not say, which is a different
    #: claim from "it was unsure" and must never be read as one.
    confidence: float = 0.0
    status: FieldStatus = "ok"
    #: One sentence, shown to the clerk verbatim. Written where the doubt was
    #: found, so the reason a box is on the page and the reason in the panel
    #: are one string rather than two that drift.
    reason: str = ""
    #: Where on the page. Filled by `locate-fields`, and only for fields in
    #: doubt: an `ok` field is never sent to the locator, so it never has one.
    region: Region | None = None


class FieldFinding(BaseModel):
    """One thing wrong with one field, from whatever noticed it.

    Deliberately not a `FieldVerdict`: a finding is what a check produces, a
    verdict is what a clerk is shown. The check has no opinion about labels,
    display values or boxes, and giving it one would put screen copy inside a
    pure Rule.
    """

    path: str
    status: FieldStatus
    reason: str


def verdicts_for(
    extraction: InvoiceExtraction,
    findings: list[FieldFinding],
    *,
    min_field_confidence: float,
    regions: dict[str, Region] | None = None,
) -> list[FieldVerdict]:
    """Every field of this extraction, with the doubts attached.

    The whole extraction rather than only the doubted fields, because the
    screen shows the whole invoice: the disputed fields are what it focuses and
    the rest is collapsed context. A screen that had to rebuild the context
    from a second source would be reading the invoice twice.

    Confidence is judged here and only here. A reported `0.0` is *silence* and
    is never read as doubt, and a field the arithmetic already objected to
    keeps the arithmetic's louder answer.
    """
    by_path: dict[str, FieldFinding] = {}
    for finding in findings:
        held = by_path.get(finding.path)
        if held is None or _SEVERITY[finding.status] > _SEVERITY[held.status]:
            by_path[finding.path] = finding

    boxes = regions or {}
    out: list[FieldVerdict] = []
    for path in paths_of(extraction):
        confidence = float(extraction.field_confidence.get(path, 0.0) or 0.0)
        finding = by_path.pop(path, None)
        status: FieldStatus = "ok"
        reason = ""
        if finding is not None:
            status, reason = finding.status, finding.reason
        elif 0.0 < confidence < min_field_confidence:
            status = "low_confidence"
            reason = (
                f"the extractor read this at {confidence:.2f} confidence, "
                f"below the governed floor of {min_field_confidence:.2f}"
            )
        out.append(FieldVerdict(
            path=path,
            label=label_for(path),
            value=render(path, value_at(extraction, path)),
            confidence=confidence,
            status=status,
            reason=reason,
            region=boxes.get(path) if status != "ok" else None,
        ))
    # A finding against a path the extraction holds no value for — the whole
    # line-item block missing, say — would otherwise be dropped here, and it is
    # exactly the doubt most worth keeping.
    for path, finding in by_path.items():
        out.append(FieldVerdict(
            path=path, label=label_for(path), value="",
            status=finding.status, reason=finding.reason,
            region=boxes.get(path),
        ))
    return out


def in_doubt(verdicts: list[FieldVerdict]) -> list[FieldVerdict]:
    """The union a clerk is asked about and the locate call is given."""
    return [verdict for verdict in verdicts if verdict.status != "ok"]


class VerdictSet(BaseModel):
    """The `field-verdicts` step's output: the whole invoice, and the doubts.

    Both halves in one journal row on purpose. `fields` is what the review Task
    carries and the screen renders; `in_doubt` is the list the locate call
    receives and the first thing a reader of the journal wants — and deriving
    one from the other at three call sites is three chances to derive it
    differently.
    """

    fields: list[FieldVerdict] = Field(default_factory=list)
    in_doubt: list[str] = Field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.in_doubt

    def summary(self) -> str:
        if self.clean:
            return (
                f"{len(self.fields)} field(s) read and nothing in doubt — "
                "no boxes needed, so no second call is made"
            )
        doubted = in_doubt(self.fields)
        kinds = sorted({verdict.status for verdict in doubted})
        return (
            f"{len(doubted)} of {len(self.fields)} field(s) in doubt "
            f"({', '.join(kinds)}): "
            + ", ".join(verdict.path for verdict in doubted[:6])
            + (" …" if len(doubted) > 6 else "")
        )
