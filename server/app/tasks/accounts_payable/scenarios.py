"""DEMO-AUTHORING APPARATUS — the hint grammar, and nothing a request may reach.

This is the language the *seeded world* and the *eval cases* are written in:
`vendor=acme-industrial po=PO-7741 lines=STL-0250:2,FAS-M12:3 pricebump` names a
case, and `StubInvoiceReader` turns it into an extraction. It used to live in
`domain.py` beside the flow it steers, and the console offered it as a screen —
which meant a deployed production path could compose an invoice out of a string
and get back an extraction nobody had read a page for.

So it moved here, and the module is the boundary: **exactly two kinds of caller
may import it** — the stub reader (opt-in, `MODEL_BACKEND=none` or `stub`, no
network, no credential) and the eval/seed fixtures that declare cases. No API
route, no service entry point, no console payload. That is a grep-checkable
claim and `test_payables.py` checks it.

`to_hint(parse(s)) == s` for every hint the repository ships, which is the
property that matters: a seed hint that serialized one character differently
would build a different world, and the eval suite would fail looking like a
Flow regression.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator


def _upper(value: str) -> str:
    return value.strip().upper() if isinstance(value, str) else value


def _lower(value: str) -> str:
    return value.strip().lower() if isinstance(value, str) else value


class ScenarioLine(BaseModel):
    sku: str = Field(default="", pattern=r"^[A-Z0-9.\-]*$")
    #: Bounded so `to_hint` never reaches `%g`'s exponent form, which the
    #: parser's charset cannot read back.
    quantity: float = Field(default=1.0, ge=0, le=99999)

    _norm = field_validator("sku", mode="before")(_upper)


class ScenarioTwist(BaseModel):
    """One deviation the grammar can ask for, and what it costs.

    A twist is a property of the Flow rather than of a screen, so all four
    facts are declared together: the token has to parse, the branch has to
    exist, and `clause_id` has to name a value the Flow actually reads. A case
    exercising a branch nothing reaches, or naming a clause nothing consults,
    would teach the wrong thing while looking right.
    """

    id: str
    token: str
    label: str
    branch: str
    clause_id: str


#: In the order `to_hint` writes them. Declared once because two things consume
#: them — the parser and the serializer — and a twist present in one and not
#: the other would be a branch you could reach and never author, or author and
#: never reach.
SCENARIO_TWISTS: tuple[ScenarioTwist, ...] = (
    ScenarioTwist(
        id="price_variance", token="pricebump",
        label="Bill the old price (+8% on the first line)",
        branch="price variance", clause_id="price-variance",
    ),
    ScenarioTwist(
        id="over_receipt", token="overbill",
        label="Bill 2 more of the first line than was received",
        branch="quantity variance", clause_id="quantity-variance",
    ),
    ScenarioTwist(
        id="low_confidence", token="blurry",
        label="A page the extractor can barely read (confidence 0.55)",
        branch="low extraction confidence", clause_id="min-confidence",
    ),
    ScenarioTwist(
        id="tax_id", token="taxid",
        label="Print a tax id on the page",
        branch="tax-id resolution", clause_id="resolution-auto-link",
    ),
    # The two WP-J branches. Both are about a *field* rather than about the
    # document: an invoice can be perfectly confident overall and still have
    # one number nobody should trust, and the whole point of the field
    # verdicts is that a clerk is asked about that one number.
    ScenarioTwist(
        id="bad_line_math", token="badmath",
        label="A line whose amount is not quantity × unit price",
        branch="arithmetic inconsistency", clause_id="line-arithmetic",
    ),
    ScenarioTwist(
        id="smudged_total", token="smudged",
        label="A total the extractor could barely read (field confidence 0.40)",
        branch="low field confidence", clause_id="min-field-confidence",
    ),
)


class InvoiceScenario(BaseModel):
    """The case-authoring language of this Flow, as a model rather than prose.

    `parse` is the only reader of a hint string and `to_hint` the only writer,
    so the seeded world, the nine eval cases and a local upload all speak one
    vocabulary that is written down in one place.

    Three fields say something an empty string cannot, so each is its own flag:
    `no_po` (billed against no order at all) differs from an absent `po_number`
    (the extractor invents one), and `unnamed_new_vendor` (a vendor we do not
    know, name unspecified) differs from a named one.
    """

    #: A vendor in the master, by slug. The resolver links it.
    vendor_slug: str = Field(default="", pattern=r"^[a-z0-9\-]*$")
    #: A vendor the master does not hold, named. The resolver mints it
    #: `onboarding` rather than matching it to anything.
    new_vendor: str = Field(default="", pattern=r"^[a-z0-9\-]*$")
    #: `newvendor` with no name: still unknown, but the extractor picks one.
    unnamed_new_vendor: bool = False
    #: The order this invoice bills, so the three-way match is a real match.
    po_number: str = Field(default="", pattern=r"^(PO-\d+)?$")
    #: Billed against no purchase order at all — the `unmatched` branch.
    no_po: bool = False
    invoice_number: str = Field(default="", pattern=r"^[A-Z0-9\-]*$")
    #: SKUs from the stub catalogue, so a seeded PO can actually pair with it.
    lines: list[ScenarioLine] = Field(default_factory=list)
    #: An explicit invoice total; 0 leaves the arithmetic to the lines.
    total_usd: float = Field(default=0.0, ge=0, le=99_999_999)

    # Every field is constrained to the charset its token is spelled in, so a
    # composed scenario round-trips by construction rather than by luck. The
    # patterns are exactly what `parse` can read back: a value this refuses is
    # one `to_hint` could write and `parse` could not recover, which would
    # quietly build a different world than the one that was declared.
    _upper_fields = field_validator("invoice_number", "po_number", mode="before")(_upper)
    _lower_fields = field_validator("vendor_slug", "new_vendor", mode="before")(_lower)

    @field_validator("total_usd")
    @classmethod
    def _to_cents(cls, value: float) -> float:
        """`to_hint` writes two decimal places, so the model holds two."""
        return round(value, 2)

    # -- twists: each reaches one branch of the flow ------------------------
    price_variance: bool = False
    over_receipt: bool = False
    low_confidence: bool = False
    tax_id: bool = False
    bad_line_math: bool = False
    smudged_total: bool = False

    @classmethod
    def parse(cls, text: str) -> "InvoiceScenario":
        """Read a hint string. `text` is filename *and* hint, because an upload
        with no hint still steers a stub extractor by its filename."""
        lowered = text.lower()
        named_new = re.search(r"newvendor[=\-_]([a-z0-9\-]+)", lowered)
        # `vendor[=-_]` also matches inside `newvendor=…`, so a named new
        # vendor wins outright rather than being read as both.
        vendor = re.search(r"vendor[=\-_]([a-z0-9\-]+)", lowered)
        po = re.search(r"po[=\-_](po-\d+)", lowered)
        invoice = re.search(r"invoice[=\-_]([a-z0-9\-]+)", lowered)
        pinned = re.search(r"lines[=\-_]([a-z0-9:.,\-]+)", lowered)
        total = re.search(r"total[=\-_]\$?(\d+(?:\.\d{1,2})?)", lowered)

        lines: list[ScenarioLine] = []
        if pinned:
            for token in pinned.group(1).split(","):
                sku, _, quantity = token.partition(":")
                if sku.strip():
                    lines.append(ScenarioLine(
                        sku=sku.strip().upper(), quantity=float(quantity or 1)
                    ))
        return cls(
            vendor_slug="" if named_new else (vendor.group(1) if vendor else ""),
            new_vendor=named_new.group(1) if named_new else "",
            unnamed_new_vendor=named_new is None and "newvendor" in lowered,
            po_number=po.group(1).upper() if po else "",
            no_po="nopo" in lowered,
            invoice_number=invoice.group(1).upper() if invoice else "",
            lines=lines,
            total_usd=float(total.group(1)) if total else 0.0,
            **{t.id: t.token in lowered for t in SCENARIO_TWISTS},
        )

    def to_hint(self) -> str:
        parts: list[str] = []
        if self.new_vendor:
            parts.append(f"newvendor={self.new_vendor}")
        elif self.unnamed_new_vendor:
            parts.append("newvendor")
        elif self.vendor_slug:
            parts.append(f"vendor={self.vendor_slug}")
        if self.no_po:
            parts.append("nopo")
        elif self.po_number:
            parts.append(f"po={self.po_number}")
        if self.invoice_number:
            parts.append(f"invoice={self.invoice_number}")
        if self.lines:
            parts.append("lines=" + ",".join(
                f"{line.sku}:{line.quantity:g}" for line in self.lines
            ))
        if self.total_usd:
            parts.append(f"total={self.total_usd:.2f}")
        parts.extend(t.token for t in SCENARIO_TWISTS if getattr(self, t.id))
        return " ".join(parts)


# ---------------------------------------------------------------------------
# A composed case as an artifact
# ---------------------------------------------------------------------------
#
# From v9 the flow refuses a run with no document: the extraction reads bytes
# out of the store, and a case with nothing behind it is the submission shape
# the whole lane exists to remove. That is a rule about *runs*, and the seeded
# world and the eval cases are runs like any other — so a composed case has to
# become an artifact rather than an exemption.
#
# It becomes one here. The hint is written **onto the page**, and the stub
# extractor reads it off the page, which is the same relationship a real
# extractor has to a real invoice: what steers the reading is the document. The
# hint stops being a channel that bypasses the artifact and becomes the way a
# fixture author writes the invoice, and every property v8 established still
# holds — the run has stored bytes, one metered pass reads them, and nothing on
# the request path can reach any of this.

#: What tells a stub extractor it is holding a composed case. Spelled in full
#: rather than derived, because the reader greps for it in bytes that a real
#: document could in principle also contain — a real invoice PDF carrying this
#: exact string would be a coincidence somebody engineered.
FIXTURE_MARKER = "cumulus-fixture:"


def fixture_document(hint: str) -> bytes:
    """A minimal PDF for a composed case, with the case printed on it.

    A real PDF header and trailer, so the document store sniffs
    `application/pdf` and the artifact is the same kind of thing an upload
    produces. There is no rendered invoice in it: what a fixture needs is an
    artifact with an identity and a content hash, and the case itself is the
    one line of text on it.
    """
    return (
        f"%PDF-1.4\n% {FIXTURE_MARKER} {hint.strip()}\n%%EOF\n".encode()
    )


def written_scenario(image: bytes | None) -> str:
    """The case written on this artifact, or "" — which every real page is.

    Deliberately total and deliberately narrow: the marker has to be in the
    bytes, on its own line, and what follows it to the end of that line is the
    hint. Anything else is a document, and a document steers a stub extractor
    only by its filename.
    """
    if not image:
        return ""
    try:
        text = image.decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001 — bytes that will not decode are a page
        return ""
    marker = text.find(FIXTURE_MARKER)
    if marker < 0:
        return ""
    line = text[marker + len(FIXTURE_MARKER):].splitlines()
    return line[0].strip() if line else ""
