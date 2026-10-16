"""What a supervisor is asked about — the seam between this flow and its screen.

`accounts_payable/v8/verdicts.py`'s contract, restated for a different
document, and restated rather than imported for the reason that module gives
about itself: which fields a ticket has, which of them are required and what to
call them on a screen are facts about a *shipping list*, not about documents in
general. What is shared is the shape — a path, a status, one sentence, and the
box on the page — and the vocabulary that makes them refer to one field, which
lives in `..paths`.

Five statuses, and the difference a supervisor sees:

- `low_confidence` — the extraction read something and said it was unsure.
- `inconsistent` — a grid disagrees with its own printed total, or a page flag
  disagrees with the block under it. The value may be perfectly legible and
  still wrong, which is the case a confidence score cannot reach.
- `missing` — a field a governed formula multiplies, on a page family that is
  present, that the extraction did not find.
- `out_of_band` — the *computed* figure is outside its department's governed
  expected-hours band. The SDD's fourth business exception, and a **field
  verdict rather than a second gate**: it arrives in the same review as the
  low-confidence fields, beside the grid it came from, so a supervisor confirms
  the reading and the hours in one sitting.
- `ok` — nothing to answer. Carried anyway, because the screen renders the
  whole ticket and needs to know which of it is context.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ...documents import Region
from . import paths as vocab

FieldStatus = Literal[
    "ok", "low_confidence", "inconsistent", "missing", "out_of_band"
]

#: Worst first. A field that is both unreadable and contradicted is one
#: question to a supervisor rather than two, and this is the order that
#: question is asked in — an absent number is not "low confidence", it is
#: absent, and a figure outside its band is a bigger claim than a smudge.
_SEVERITY: dict[str, int] = {
    "missing": 4, "out_of_band": 3, "inconsistent": 2, "low_confidence": 1,
    "ok": 0,
}


class FieldFinding(BaseModel):
    """One thing wrong with one field, from whatever noticed it.

    Deliberately not a `FieldVerdict`: a finding is what a check produces, a
    verdict is what a supervisor is shown. The checks have no opinion about
    labels, display values or boxes, and giving them one would put screen copy
    inside a pure Rule.
    """

    path: str
    status: FieldStatus
    reason: str


class FieldVerdict(BaseModel):
    """One field of one shipping list, as the review screen receives it."""

    #: The canonical fact path. See `..paths`: this is the join.
    path: str
    label: str = ""
    value: str = ""
    #: 0..1 from the extraction. 0.0 means it did not say, which is a different
    #: claim from "it was unsure" and must never be read as one.
    confidence: float = 0.0
    status: FieldStatus = "ok"
    #: One sentence, shown verbatim. Written where the doubt was found, so the
    #: reason a box is on the page and the reason in the panel are one string
    #: rather than two that drift.
    reason: str = ""
    #: Where on the page. Filled by `locate-fields`, and only for fields in
    #: doubt: an `ok` field is never sent to the locator, so it never has one.
    #: A computed figure never has one either — there is no ink for it.
    region: Region | None = None


def verdicts_for(
    reading: dict,
    findings: list[FieldFinding],
    *,
    min_field_confidence: float,
    regions: dict[str, Region] | None = None,
) -> list[FieldVerdict]:
    """Every field of this reading, with the doubts attached.

    The whole ticket rather than only the doubted fields, because the screen
    shows the whole ticket: the disputed fields are what it focuses and the
    rest is collapsed context. A screen that had to rebuild the context from a
    second source would be reading the document twice.

    Confidence is judged here and only here. A reported `0.0` is *silence* and
    is never read as doubt, and a field a check already objected to keeps the
    check's louder answer.
    """
    by_path: dict[str, FieldFinding] = {}
    for finding in findings:
        held = by_path.get(finding.path)
        if held is None or _SEVERITY[finding.status] > _SEVERITY[held.status]:
            by_path[finding.path] = finding

    boxes = regions or {}
    confidences = reading.get("field_confidence") or {}
    out: list[FieldVerdict] = []
    for path in vocab.paths_of(reading):
        confidence = _confidence(confidences, path)
        finding = by_path.pop(path, None)
        status: FieldStatus = "ok"
        reason = ""
        if finding is not None:
            status, reason = finding.status, finding.reason
        elif 0.0 < confidence < min_field_confidence:
            status = "low_confidence"
            reason = (
                f"the extraction read this at {confidence:.2f} confidence, "
                f"below the governed floor of {min_field_confidence:.2f}"
            )
        out.append(FieldVerdict(
            path=path,
            label=vocab.label_for(path),
            value=vocab.render(vocab.value_at(reading, path)),
            confidence=confidence,
            status=status,
            reason=reason,
            region=boxes.get(path) if status != "ok" else None,
        ))
    # A finding against a path the reading holds no value for — an absent
    # required field, a computed figure outside its band — would otherwise be
    # dropped here, and those are exactly the doubts most worth keeping.
    for path, finding in by_path.items():
        out.append(FieldVerdict(
            path=path,
            label=vocab.label_for(path),
            value=vocab.render(vocab.value_at(reading, path)),
            confidence=_confidence(confidences, path),
            status=finding.status,
            reason=finding.reason,
            region=boxes.get(path),
        ))
    return out


def _confidence(reported: dict, path: str) -> float:
    try:
        return float(reported.get(path, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def in_doubt(verdicts: list[FieldVerdict]) -> list[FieldVerdict]:
    """The union a supervisor is asked about."""
    return [verdict for verdict in verdicts if verdict.status != "ok"]


class VerdictSet(BaseModel):
    """The `field-verdicts` step's output: the whole ticket, and the doubts.

    Both halves in one journal row on purpose. `fields` is what the review Task
    carries and the screen renders; `in_doubt` is the first thing a reader of
    the journal wants — and deriving one from the other at three call sites is
    three chances to derive it differently.
    """

    fields: list[FieldVerdict] = Field(default_factory=list)
    in_doubt: list[str] = Field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.in_doubt

    @property
    def locatable(self) -> list[str]:
        """The disputed paths a locate call can actually be asked about.

        A computed figure is printed nowhere, so asking for a box around one
        would be asking a model to find ink that does not exist — and the
        governed locate prompt's own rule is to omit a path it cannot find. The
        field is still questioned; it simply has no highlight.
        """
        return [path for path in self.in_doubt if not vocab.is_hours(path)]

    def summary(self) -> str:
        if self.clean:
            return (
                f"{len(self.fields)} field(s) read and nothing in doubt — no "
                "boxes needed, so no second call is made"
            )
        doubted = in_doubt(self.fields)
        kinds = sorted({verdict.status for verdict in doubted})
        return (
            f"{len(doubted)} of {len(self.fields)} field(s) in doubt "
            f"({', '.join(kinds)}): "
            + ", ".join(verdict.path for verdict in doubted[:6])
            + (" …" if len(doubted) > 6 else "")
        )
