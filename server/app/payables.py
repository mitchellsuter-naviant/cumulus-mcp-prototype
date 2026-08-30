"""The accounts-payable Flow: extract, resolve, match, screen, post.

What this flow proves, and the order it proves it in:

1. **One ModelCall reads the document.** A single extraction pass returns the
   vendor as printed *and* the commercial content. There is no separate
   classifier call and no exemplar shelf: an extra call to name the vendor and
   a per-vendor template to extract against were two mechanisms doing what one
   governed prompt does, and both had to be kept in step with each other.
2. **A vendor is an entity, not a string.** The model reads a name off a page;
   the *resolver* turns that into a canonical Vendor id by fuzzy-matching the
   vendor master. Deciding which entity a piece of text refers to is the
   resolver's job against the master, never the model's against its memory —
   and when the resolver cannot decide, the run stops and asks.
3. **Extraction is data-model shaped.** The output is invoice lines against the
   FlowContract's fact paths — the same vocabulary the golden sets assert on
   and the graph stores.
4. **The three-way match is retained.** Invoice lines are paired against the PO
   lines and receiving lines already in the graph (landed by their own sync
   Flows), and the pairings are written as a `MatchSet` object rather than
   computed and discarded. `no receiving report yet` is a *state*, not a
   failure.
5. **Anomaly screening replaces the fraud placeholder.** Scored against this
   vendor's own history from the derived `vendor_payment_profile`, with the
   thresholds read from the posting policy at run time.
6. **A vendor graduates through use.** One seen for the first time is minted
   `onboarding`; it becomes `active` when a human posts one of its invoices,
   which is the confirmation that it is real.

Steps: t1 trigger → s1 store-document → x0 read-prompt → m1 extract-invoice →
z1 resolve-vendor → (k0 confirm-vendor) → x3 project-lines → x4
gather-po-and-receiving → c1 three-way-match → c2 anomaly-screen → x5
read-posting-policy → r2 post-or-review → k2 review-invoice → a1 post-invoice →
e1 InvoicePosted.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import threading
from datetime import date, timedelta
from collections.abc import Callable
from typing import Literal, Protocol

from pydantic import BaseModel, Field, field_validator

from .anomaly import AnomalyService, parse_anomaly_thresholds
from .auth import Principal, flow_principal
from .dispatch import Dispatcher, InMemoryEventLog
from .documents import DOCUMENT_COLLECTION, DocumentService, Region
from .gateway import ActionGateway, InMemoryLedgerStore
from .graph import Claim, GraphService, GraphWriter, InMemoryGraphStore, object_id
from .matching import MatchLine, MatchResult, match_invoice, parse_match_tolerances
from .models import utcnow
from .prompts import GovernedPrompt, read_prompt
from .reimbursements import JournalEvent, _event  # shared journal shape
from .resolution import ResolutionOutcome, ResolutionThresholds
from .resolution import read_resolution_thresholds, resolve
from .runs import RunContext, RunEngine, RunRecord
from .tasks import Task, TaskEvidence, TaskProposal, TaskService

log = logging.getLogger("cumulus.payables")

FLOW_ID = "accounts-payable"
POLICY_ASSET_ID = "invoice-posting-policy"
PROMPT_ASSET_ID = "invoice-extraction-prompt"
CLERK_QUEUE = "ap-clerk"

#: Who posts an invoice that no person reviewed. An invoice under the governed
#: auto-post limit is committed on the Flow's own authority, bound to the same
#: queue a clerk would decide it from — so the Gateway asks the same question
#: either way, and un-binding this stops auto-posting rather than exempting it.
AP_FLOW = flow_principal(FLOW_ID, CLERK_QUEUE)

PayableState = Literal["posted", "pending", "denied"]


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------
# Shared by the stub extractor, the seeded purchase orders and the console's
# composer, so a local three-way match is a real match rather than a
# coincidence: the same SKUs, the same descriptions, the same unit prices on
# both sides. A deployment with a real extractor never reads this.

CATALOG: tuple[tuple[str, str, float], ...] = (
    ("STL-0250", "Hot-rolled steel plate, 1/4 in", 486.00),
    ("STL-0375", "Hot-rolled steel plate, 3/8 in", 712.50),
    ("FAS-M12", "Grade 8 hex bolt, M12 x 60mm, box of 100", 128.75),
    ("WLD-6013", "Welding rod E6013, 5kg", 92.40),
    ("PPE-GLV", "Cut-resistant gloves, size L, 12 pair", 64.20),
    ("CNS-HYD", "Hydraulic fluid ISO 46, 20L drum", 214.90),
)
_FREIGHT = ("FRT-STD", "Freight and handling", 0.0)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class InvoiceLineExtract(BaseModel):
    sku: str = ""
    description: str = ""
    quantity: float = 0.0
    unit_price_usd: float = 0.0
    amount_usd: float = 0.0
    kind: Literal["goods", "freight", "tax", "discount"] = "goods"


class InvoiceExtraction(BaseModel):
    vendor_name: str = ""
    vendor_slug: str = ""
    # Printed on some invoices and not others; when it is there it is the
    # strongest signal the resolver has, so the one extraction call reads it.
    vendor_tax_id: str = ""
    invoice_number: str = ""
    invoice_date: str = ""
    due_date: str = ""
    po_number: str = ""
    subtotal_usd: float = 0.0
    freight_usd: float = 0.0
    tax_usd: float = 0.0
    total_usd: float = 0.0
    currency: str = "USD"
    line_summary: str = ""
    confidence: float = 0.0
    model: str = ""
    lines: list[InvoiceLineExtract] = Field(default_factory=list)
    regions: list[Region] = Field(default_factory=list)


class PostingPolicy(BaseModel):
    """The governed values the post-or-review rule consumes."""

    limit_usd: float = 2500.0
    min_confidence: float = 0.75
    source_ref: str = f"knowledge:{POLICY_ASSET_ID}"
    found_asset: bool = False


class InvoiceRecord(BaseModel):
    id: str
    run_id: str = ""
    submitted_by: str
    submitted_at_iso: str
    filename: str = ""
    document_id: str = ""
    # The canonical id, and the label at the time of the run.
    vendor_id: str = ""
    vendor_slug: str = ""
    vendor_name: str = ""
    vendor_confidence: float = 0.0
    vendor_resolution: str = ""
    vendor_method: str = ""
    vendor_tax_id: str = ""
    invoice_number: str = ""
    invoice_date: str = ""
    due_date: str = ""
    po_number: str = ""
    subtotal_usd: float = 0.0
    freight_usd: float = 0.0
    tax_usd: float = 0.0
    total_usd: float = 0.0
    currency: str = "USD"
    line_summary: str = ""
    lines: list[InvoiceLineExtract] = Field(default_factory=list)
    # The retained three-way match, and the anomaly findings.
    match_verdict: str = ""
    match_summary: str = ""
    match_ref: str = ""
    match_pairings: list[dict] = Field(default_factory=list)
    anomaly_findings: list[dict] = Field(default_factory=list)
    anomaly_basis: str = ""
    state: PayableState = "pending"
    verdict_reason: str = ""
    policy_ref: str = f"knowledge:{POLICY_ASSET_ID}"
    post_limit_usd: float = 0.0
    task_id: str = ""
    decided_by: str = ""
    decision_note: str = ""
    graph_ref: str = ""
    journal: list[JournalEvent] = Field(default_factory=list)

    # Retained for the console's existing columns.
    three_way_match: str = ""
    fraud_screen: str = ""


class PayableStats(BaseModel):
    total: int = 0
    posted: int = 0
    pending: int = 0
    denied: int = 0
    posted_usd: float = 0.0
    matched: int = 0
    match_variance: int = 0
    match_blocked: int = 0
    anomalies: int = 0


# ---------------------------------------------------------------------------
# Readers (the ModelCalls' engines)
# ---------------------------------------------------------------------------


# What the ModelCall runs on when the corpus has not seeded yet. The governed
# copy is `seed/invoice-extraction-prompt.md`, whose fenced block is this text;
# the two are expected to diverge the first time a customer edits the asset,
# which is the whole point of the asset existing.
DEFAULT_INVOICE_PROMPT = """\
Extract from this invoice: vendor_name — the party that issued it, as printed,
not normalized and not guessed at from an identifier — vendor_tax_id if one is
printed, invoice_number, invoice_date (ISO 8601), due_date (ISO 8601),
po_number, subtotal_usd, freight_usd, tax_usd, total_usd, currency, a short
line_summary, your confidence 0-1, and lines: an array of the billed lines, each
with sku, description, quantity, unit_price_usd, amount_usd and kind (goods,
freight, tax or discount). Respond with only a JSON object with those keys."""


class InvoiceReader(Protocol):
    def read(
        self, image: bytes | None, filename: str, hint: str, *, prompt: str = ""
    ) -> InvoiceExtraction:
        """`prompt` is the governed instruction the flow read for this run.

        Passed in rather than held by the reader for the reason given on
        `reimbursements.ReceiptReader`: the instruction is knowledge and the
        reader is machinery.
        """
        ...


_AP_VENDORS = (
    "Acme Industrial Inc.",
    "Northwind Traders LLC",
    "Bay State Fasteners Co.",
    "Cascade Welding Supply",
)


def _slug_to_name(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.split("-"))


def _name_to_slug(name: str) -> str:
    cleaned = re.sub(
        r"\b(inc|llc|ltd|co|corp|corporation|gmbh|plc)\b", " ", name.lower()
    )
    return re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")


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
    exist, and `clause_id` has to name a value this Flow actually reads. A
    checkbox offering a branch nothing reaches, or naming a clause nothing
    consults, would teach the wrong thing while looking right.
    """

    id: str
    token: str
    label: str
    branch: str
    clause_id: str


#: In the order `to_hint` writes them. Declared once because three things
#: consume them — the parser, the serializer and the console's composer — and
#: a twist present in one and not the others would be a branch you could
#: reach and never author, or author and never reach.
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
)


class InvoiceScenario(BaseModel):
    """The case-authoring language of this Flow, as a model rather than prose.

    A local run is steered by a *hint string* — `vendor=acme-industrial
    po=PO-7741 lines=STL-0250:2,FAS-M12:3 pricebump` — and that string is not
    debug scaffolding. It is how the seeded world is written, how the nine eval
    cases state their triggers, and how a person submits an invoice by hand.
    Three consumers, one vocabulary, and until now it existed only as a
    docstring above a pile of regexes.

    So the grammar lives here and everything goes through it: `parse` is the
    only reader of the string, `to_hint` the only writer, and the console
    composes an instance from real graph rows and shows the string assembling.

    `to_hint(parse(s)) == s` for every hint the repository ships, which is the
    property that matters: a seed hint that serialized one character
    differently would build a different world, and the eval suite would fail
    looking like a Flow regression.

    Three fields say something an empty string cannot, so each is its own flag:
    `no_po` (billed against no order at all) differs from an absent
    `po_number` (the extractor invents one), and `unnamed_new_vendor` (a
    vendor we do not know, name unspecified) differs from a named one.
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
    #: SKUs from the shared catalog, so a synced PO can actually pair with it.
    lines: list[ScenarioLine] = Field(default_factory=list)
    #: An explicit invoice total; 0 leaves the arithmetic to the lines.
    total_usd: float = Field(default=0.0, ge=0, le=99_999_999)

    # Every field is constrained to the charset its token is spelled in, so a
    # composed scenario round-trips by construction rather than by luck. The
    # patterns are exactly what `parse` can read back: a value this refuses is
    # one `to_hint` could write and `parse` could not recover, which would
    # quietly submit a different invoice than the one on screen.
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


class StubInvoiceReader:
    """Deterministic local-dev extractor: no credential, no network.

    Steerable by an `InvoiceScenario` — which is what a hint string parses to,
    and where the grammar and the branch each token reaches are written down.
    What the scenario leaves unsaid this fills in from a hash of the input, so
    the same submission always reads the same way.
    """

    name = "stub"

    # -- m1 ---------------------------------------------------------------
    def read(
        self, image: bytes | None, filename: str, hint: str, *, prompt: str = ""
    ) -> InvoiceExtraction:
        scenario = InvoiceScenario.parse(f"{filename} {hint}")
        base = hashlib.sha256(
            (image or b"") + filename.encode() + hint.encode()
        ).digest()

        if scenario.new_vendor:
            slug = f"{scenario.new_vendor}-ltd"
        elif scenario.vendor_slug:
            slug = scenario.vendor_slug
        elif scenario.unnamed_new_vendor:
            slug = f"meridian-supply-{base[:2].hex()}"
        else:
            slug = _name_to_slug(_AP_VENDORS[base[3] % len(_AP_VENDORS)])
        vendor_name = _slug_to_name(slug)

        po_number = "" if scenario.no_po else (
            scenario.po_number or f"PO-{7000 + base[6] % 900}"
        )
        invoice_number = scenario.invoice_number or (
            f"{slug[:4].upper()}-INV-"
            f"{20000 + int.from_bytes(base[7:9], 'big') % 9000}"
        )
        issued = date.today() - timedelta(days=base[10] % 21)

        # Lines from the shared catalog, so a synced PO can actually match.
        # A pinned SKU the catalog does not hold is dropped rather than
        # invented: there would be no PO line for it to pair against.
        by_sku = {sku: (sku, description, price) for sku, description, price in CATALOG}
        picks: list[tuple[tuple[str, str, float], float]] = [
            (by_sku[line.sku], line.quantity)
            for line in scenario.lines if line.sku in by_sku
        ]
        if not picks:
            picks = [
                (
                    CATALOG[(base[12 + i] + i) % len(CATALOG)],
                    float(1 + (base[15 + i] % 4)),
                )
                for i in range(2 + base[11] % 2)
            ]
        seen: set[str] = set()
        lines: list[InvoiceLineExtract] = []
        for index, ((sku, description, unit_price), quantity) in enumerate(picks):
            if sku in seen:
                continue
            seen.add(sku)
            if scenario.over_receipt and index == 0:
                quantity += 2
            price = unit_price
            if scenario.price_variance and index == 0:
                price = round(unit_price * 1.08, 2)
            lines.append(InvoiceLineExtract(
                sku=sku, description=description, quantity=quantity,
                unit_price_usd=price,
                amount_usd=round(quantity * price, 2), kind="goods",
            ))

        subtotal = round(sum(line.amount_usd for line in lines), 2)
        freight = round(48 + base[20] % 90, 2)
        if freight > 0:
            lines.append(InvoiceLineExtract(
                sku=_FREIGHT[0], description=_FREIGHT[1], quantity=1.0,
                unit_price_usd=freight, amount_usd=freight, kind="freight",
            ))
        total = round(subtotal + freight, 2)
        if scenario.total_usd:
            # An explicit total wins and the freight line absorbs the
            # difference, so the demo's arithmetic still adds up.
            freight = round(max(scenario.total_usd - subtotal, 0), 2)
            if lines and lines[-1].kind == "freight":
                lines[-1].unit_price_usd = freight
                lines[-1].amount_usd = freight
            total = round(subtotal + freight, 2)

        confidence = 0.55 if scenario.low_confidence else 0.93
        invoice_date_text = issued.isoformat()
        due = (issued + timedelta(days=30)).isoformat()

        return InvoiceExtraction(
            vendor_name=vendor_name, vendor_slug=_name_to_slug(vendor_name),
            vendor_tax_id=(
                f"98-{int.from_bytes(base[5:7], 'big') % 10000000:07d}"
                if scenario.tax_id else ""
            ),
            invoice_number=invoice_number, invoice_date=invoice_date_text,
            due_date=due, po_number=po_number,
            subtotal_usd=subtotal, freight_usd=freight, tax_usd=0.0,
            total_usd=total, currency="USD",
            line_summary=", ".join(
                f"{line.quantity:g}× {line.sku}" for line in lines if line.kind == "goods"
            ),
            confidence=confidence,
            model="stub-extractor",
            lines=lines,
            regions=_invoice_regions(lines, total, invoice_number),
        )


def _invoice_regions(
    lines: list[InvoiceLineExtract], total: float, invoice_number: str
) -> list[Region]:
    """Synthetic geometry, so the review screen's box path runs locally."""
    regions = [
        Region(field="invoice.invoice_number", page=1, x=0.62, y=0.08,
               width=0.30, height=0.05, text=invoice_number, confidence=0.95),
        Region(field="invoice.total_usd", page=1, x=0.66, y=0.86, width=0.26,
               height=0.06, text=f"{total:,.2f}", confidence=0.94),
    ]
    for index, line in enumerate(lines):
        regions.append(Region(
            field=f"invoice.line_items[{index}].amount_usd", page=1, x=0.70,
            y=0.36 + 0.055 * index, width=0.22, height=0.045,
            text=f"{line.amount_usd:,.2f}", confidence=0.92,
        ))
    return regions


class VertexInvoiceReader:
    """Multimodal extraction on Vertex AI via google-genai.

    Same client and credentials as the embedder and the answer backend. One
    prompt, one call: the vendor as printed plus the commercial content. That
    prompt arrives from the flow, which read it out of the corpus — so the
    instruction that forbids guessing an identifier is a governed value a
    customer can see and revise, not a literal buried here.
    """

    name = "vertex"

    def __init__(self, project: str | None, location: str, model: str) -> None:
        from google import genai

        self._client = genai.Client(vertexai=True, project=project, location=location)
        self._model = model

    def _parts(self, image: bytes | None, filename: str, prompt: str):
        from google.genai import types

        parts: list = []
        if image:
            lowered = filename.lower()
            mime = (
                "application/pdf" if lowered.endswith(".pdf")
                else "image/png" if lowered.endswith(".png")
                else "image/jpeg"
            )
            parts.append(types.Part.from_bytes(data=image, mime_type=mime))
        parts.append(types.Part(text=prompt))
        return parts

    def _generate(self, parts, temperature: float) -> dict:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self._model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=temperature
            ),
        )
        return json.loads(response.text or "{}")

    def read(
        self, image: bytes | None, filename: str, hint: str, *, prompt: str = ""
    ) -> InvoiceExtraction:
        text = (prompt or DEFAULT_INVOICE_PROMPT) + (
            f"\n\nContext: {hint}" if hint else ""
        )
        data = self._generate(self._parts(image, filename, text), 0.0)
        lines = [
            InvoiceLineExtract(
                sku=str(item.get("sku", "") or ""),
                description=str(item.get("description", "") or ""),
                quantity=float(item.get("quantity", 0) or 0),
                unit_price_usd=float(item.get("unit_price_usd", 0) or 0),
                amount_usd=float(item.get("amount_usd", 0) or 0),
                kind=str(item.get("kind", "goods") or "goods"),
            )
            for item in (data.get("lines") or [])
        ]
        vendor_name = str(data.get("vendor_name", "") or "")
        return InvoiceExtraction(
            vendor_name=vendor_name,
            vendor_slug=_name_to_slug(vendor_name),
            vendor_tax_id=str(data.get("vendor_tax_id", "") or ""),
            invoice_number=str(data.get("invoice_number", "") or ""),
            invoice_date=str(data.get("invoice_date", "") or ""),
            due_date=str(data.get("due_date", "") or ""),
            po_number=str(data.get("po_number", "") or ""),
            subtotal_usd=float(data.get("subtotal_usd", 0) or 0),
            freight_usd=float(data.get("freight_usd", 0) or 0),
            tax_usd=float(data.get("tax_usd", 0) or 0),
            total_usd=float(data.get("total_usd", 0) or 0),
            currency=str(data.get("currency", "USD") or "USD"),
            line_summary=str(data.get("line_summary", "") or ""),
            confidence=float(data.get("confidence", 0) or 0),
            model=self._model,
            lines=lines,
        )


def build_invoice_reader(
    backend: str, project: str | None, location: str, model: str
) -> InvoiceReader:
    if backend == "vertex":
        return VertexInvoiceReader(project, location, model)
    return StubInvoiceReader()


# ---------------------------------------------------------------------------
# Policy parsing (the governed knowledge read)
# ---------------------------------------------------------------------------


def parse_posting_policy(content: str) -> PostingPolicy:
    values = PostingPolicy(found_asset=True)
    limit = re.search(
        r"auto-post limit:?\s*\$([\d,]+(?:\.\d{1,2})?)", content, re.IGNORECASE
    )
    if limit:
        values.limit_usd = float(limit.group(1).replace(",", ""))
    # Both spellings, because the governed asset said "classification" while
    # a separate classifier call existed and says "extraction" now — a policy
    # already published in a deployment must keep parsing.
    confidence = re.search(
        r"minimum (?:extraction|classification) confidence:?\s*(\d+(?:\.\d+)?)",
        content, re.IGNORECASE,
    )
    if confidence:
        values.min_confidence = float(confidence.group(1))
    return values


class PolicyClause(BaseModel):
    """One governed value this Flow reads, named where a person can see it.

    The console shows these beside what a person composed and stops there.
    "$9,800.00" next to "auto-post ≤ $2,500.00" lets the reader draw the
    conclusion; drawing it here would be a second implementation of
    `_posting_check`, and two implementations of one rule is one too many.
    """

    id: str
    label: str
    value: str
    #: The governed document, at the revision it was read.
    ref: str
    #: A fragment the posting rule's own reason contains when this clause is
    #: the one that held the invoice. Attribution of a sentence the Rule
    #: already wrote — never a re-derivation of it.
    cites: str = ""
    deciding: bool = False


def read_policy_clauses(knowledge) -> list[PolicyClause]:
    """Every governed value the accounts-payable flow reads, at its revision.

    Two documents, because the flow reads two: the posting policy governs the
    limit, the match and the screen; the entity-resolution policy governs
    whether a printed name becomes a link or a new vendor.

    Only what a run actually consults is listed. A screen that showed the
    whole document would be a document viewer — the Library is that — and one
    that showed a curated subset would drift from what the flow reads.
    """
    asset = knowledge.repo.get(POLICY_ASSET_ID) if knowledge else None
    content = asset.content if asset is not None else ""
    ref = _knowledge_ref(asset) if asset is not None else f"knowledge:{POLICY_ASSET_ID}"
    posting = parse_posting_policy(content)
    match = parse_match_tolerances(content, ref)
    screen = parse_anomaly_thresholds(content, ref)
    resolution = read_resolution_thresholds(knowledge)
    return [
        PolicyClause(
            id="auto-post-limit", label="Auto-post limit",
            value=f"${posting.limit_usd:,.2f}", ref=ref,
            cites="auto-post limit",
        ),
        PolicyClause(
            id="min-confidence", label="Minimum extraction confidence",
            value=f"{posting.min_confidence:.2f}", ref=ref,
            cites="extraction confidence",
        ),
        PolicyClause(
            id="price-variance", label="Price variance tolerance",
            value=f"{match.price_variance_pct:.1f}%", ref=ref,
            cites="off the PO's",
        ),
        PolicyClause(
            id="quantity-variance", label="Quantity variance tolerance",
            value=f"{match.quantity_variance:.1f} units", ref=ref,
            cites="was received on",
        ),
        PolicyClause(
            id="unmatched-lines", label="Unmatched line tolerance",
            value=f"{match.unmatched_line_tolerance} lines", ref=ref,
            cites="nothing on the purchase order resembles",
        ),
        PolicyClause(
            id="freight-allowance", label="Freight and tax allowance",
            value=f"${match.freight_allowance_usd:,.2f}", ref=ref,
        ),
        PolicyClause(
            id="receiving-required", label="Receiving report required above",
            value=f"${match.receiving_required_above_usd:,.2f}", ref=ref,
            cites="not booked in yet",
        ),
        PolicyClause(
            id="amount-outlier", label="Amount outlier multiple",
            value=f"{screen.outlier_multiple:.1f}× the vendor's median", ref=ref,
            cites="anomaly (amount-outlier)",
        ),
        PolicyClause(
            id="duplicate-window", label="Duplicate window",
            value=f"{screen.duplicate_window_days} days", ref=ref,
            cites="anomaly (duplicate-suspect)",
        ),
        PolicyClause(
            id="resolution-auto-link", label="Vendor auto-link at or above",
            value=f"{resolution.auto_link_at:.2f}", ref=resolution.source_ref,
        ),
    ]


def clauses_cited_by(clauses: list[PolicyClause], reason: str) -> list[PolicyClause]:
    """Mark the clauses this run's verdict reason names.

    A copy, so the registry stays a description of the corpus rather than of
    one invoice.
    """
    return [
        clause.model_copy(update={
            "deciding": bool(clause.cites) and clause.cites in reason
        })
        for clause in clauses
    ]


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class PayableDirectory(Protocol):
    def list(self) -> list[InvoiceRecord]: ...
    def get(self, record_id: str) -> InvoiceRecord | None: ...
    def put(self, record: InvoiceRecord) -> InvoiceRecord: ...


class InMemoryPayableDirectory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, InvoiceRecord] = {}

    def list(self) -> list[InvoiceRecord]:
        with self._lock:
            return list(self._items.values())

    def get(self, record_id: str) -> InvoiceRecord | None:
        with self._lock:
            return self._items.get(record_id)

    def put(self, record: InvoiceRecord) -> InvoiceRecord:
        with self._lock:
            self._items[record.id] = record
            return record


class FirestorePayableDirectory:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore

        self._client = firestore.Client(project=project)
        self._col = self._client.collection(collection)

    def list(self) -> list[InvoiceRecord]:
        out = []
        for doc in self._col.stream():
            try:
                out.append(InvoiceRecord.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping unparseable payable", extra={"docId": doc.id})
        return out

    def get(self, record_id: str) -> InvoiceRecord | None:
        snap = self._col.document(record_id).get()
        if not snap.exists:
            return None
        try:
            return InvoiceRecord.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable payable", extra={"id": record_id})
            return None

    def put(self, record: InvoiceRecord) -> InvoiceRecord:
        self._col.document(record.id).set(record.model_dump(mode="json"))
        return record


def _knowledge_ref(asset) -> str:
    return f"knowledge:{asset.id}@rev{getattr(asset, 'revision', 1)}"


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

MAX_REVIEW_ROUNDS = 2


class PayableService:
    def __init__(
        self,
        directory: PayableDirectory,
        reader: InvoiceReader,
        knowledge,  # KnowledgeService — duck-typed to avoid an import cycle
        tasks: TaskService,
        engine: RunEngine | None = None,
        graph: GraphService | None = None,
        documents: DocumentService | None = None,
        anomaly: AnomalyService | None = None,
        gateway: ActionGateway | None = None,
        dispatcher: Dispatcher | None = None,
    ) -> None:
        self.directory = directory
        self.reader = reader
        self.knowledge = knowledge
        self.tasks = tasks
        self.engine = engine
        self.graph = graph
        self.documents = documents
        self.anomaly = anomaly
        # Never None. A service wired without a Gateway still writes through
        # one: falling back to a bare GraphWriter would open a hole exactly
        # where the chokepoint belongs. A ledger nobody reads is a wiring bug;
        # an ungoverned write is a governance one.
        self.gateway = gateway or ActionGateway(
            graph or GraphService(InMemoryGraphStore()), InMemoryLedgerStore()
        )
        # Never bypassed, for the Gateway's reason one line up: a service
        # wired without a Dispatcher still starts its runs through one, into a
        # log nobody reads. A log nobody reads is a wiring bug; a run nothing
        # recorded a cause for is a governance one.
        self.dispatcher = dispatcher or (
            Dispatcher(engine, InMemoryEventLog()) if engine is not None else None
        )
        if engine is not None:
            engine.register(FLOW_ID, self._body)

    # -- reads --------------------------------------------------------------

    def visible_to(self, principal: Principal) -> Callable[[InvoiceRecord], bool]:
        """Which invoices this principal may see. `submitted_by` is the owner.

        The rule, and why it is not `reads_beyond_self`, lives in
        `TaskService.workspace_scope`.
        """
        allowed = self.tasks.workspace_scope(principal, CLERK_QUEUE)
        return lambda r: allowed(r.submitted_by, r.task_id)

    def list(
        self,
        state: PayableState | None = None,
        principal: Principal | None = None,
    ) -> list[InvoiceRecord]:
        """The table. `principal=None` is unscoped, for seeding and internals —
        every caller that serves a request passes one."""
        items = self.directory.list()
        if state is not None:
            items = [r for r in items if r.state == state]
        if principal is not None:
            visible = self.visible_to(principal)
            items = [r for r in items if visible(r)]
        return sorted(items, key=lambda r: r.submitted_at_iso, reverse=True)

    def get(self, record_id: str) -> InvoiceRecord | None:
        return self.directory.get(record_id)

    def stats(self, principal: Principal | None = None) -> PayableStats:
        """Totals over exactly the rows `list` would return.

        Counted *from* `list` rather than beside it, so the strip and the table
        under it cannot be computed over different slices. That bug was already
        caught once on the task badge, where the per-queue breakdown was
        filtered and the headline count was not; a stat strip arguing with the
        table below it is worse than no strip at all.
        """
        stats = PayableStats()
        for r in self.list(principal=principal):
            stats.total += 1
            if r.match_verdict == "matched":
                stats.matched += 1
            elif r.match_verdict in ("variance", "unmatched"):
                stats.match_variance += 1
            elif r.match_verdict == "blocked":
                stats.match_blocked += 1
            if r.anomaly_findings:
                stats.anomalies += 1
            if r.state == "posted":
                stats.posted += 1
                stats.posted_usd = round(stats.posted_usd + r.total_usd, 2)
            elif r.state == "pending":
                stats.pending += 1
            else:
                stats.denied += 1
        return stats

    def read_policy(self) -> PostingPolicy:
        asset = self.knowledge.repo.get(POLICY_ASSET_ID)
        if asset is None:
            log.warning("Invoice posting policy missing; using defaults")
            return PostingPolicy(found_asset=False)
        values = parse_posting_policy(asset.content)
        values.source_ref = _knowledge_ref(asset)
        return values

    def read_prompt(self) -> GovernedPrompt:
        return read_prompt(self.knowledge, PROMPT_ASSET_ID, DEFAULT_INVOICE_PROMPT)

    def _policy_asset_content(self) -> tuple[str, str]:
        asset = self.knowledge.repo.get(POLICY_ASSET_ID)
        if asset is None:
            return "", f"knowledge:{POLICY_ASSET_ID}"
        return asset.content, _knowledge_ref(asset)

    # -- entry point --------------------------------------------------------

    def submit(
        self,
        submitted_by: str,
        image_b64: str = "",
        filename: str = "",
        hint: str = "",
        *,
        run_id: str | None = None,
        submitted_at_iso: str = "",
    ) -> InvoiceRecord:
        if self.dispatcher is None:
            raise RuntimeError("Payable service has no run engine wired")
        inputs = {
            "submitted_by": submitted_by,
            "image_b64": image_b64,
            "filename": filename,
            "hint": hint,
            "submitted_at_iso": submitted_at_iso or utcnow().isoformat(),
        }
        # The fact, not the run: an invoice arriving is what happened, and
        # which response that starts is the Trigger's answer rather than this
        # call site's. `one()` is what makes the answer available to a caller
        # that has a row to return.
        record = self.dispatcher.emit(
            "InvoiceReceived", inputs, actor=submitted_by, run_id=run_id,
        ).one()
        return self._project(record)

    def resume_for_task(
        self, task: Task, principal: Principal | None = None
    ) -> InvoiceRecord | None:
        """`principal` is the human who decided, carried to the commit.

        The Gateway checks the committer at the write, so the deciding human
        has to reach it — a uid recovered from `decided_by` afterwards would
        name the right person and prove nothing about their authority.
        """
        if task.flow_id != FLOW_ID or self.engine is None:
            return None
        record = self.engine.resume_for_task(task.id, principal=principal)
        return self._project(record) if record else None

    def apply_task_decision(
        self, task: Task, principal: Principal | None = None
    ) -> InvoiceRecord | None:
        return self.resume_for_task(task, principal)

    # -- the flow body ------------------------------------------------------

    def _body(self, ctx: RunContext, inputs: dict) -> dict:
        submitted_by = inputs["submitted_by"]
        filename = inputs.get("filename", "")
        hint = inputs.get("hint", "")
        image_b64 = inputs.get("image_b64", "")
        submitted_at = inputs.get("submitted_at_iso") or utcnow().isoformat()
        image = base64.b64decode(image_b64) if image_b64 else None

        ctx.event(
            "t1", "InvoiceReceived",
            f"invoice received from {submitted_by}"
            + (f" ({filename})" if filename else ""),
            kind="trigger",
        )

        slot = ctx.step(
            "s1", "store-document", "action",
            fn=lambda: self._store(image_b64, filename, submitted_by, ctx.record.id),
            writes=lambda out: [f"document:{out['document_id']}"],
            summary=lambda out: (
                f"artifact {out['document_id']} stored in `{DOCUMENT_COLLECTION}`"
            ),
        )
        document_id = slot["document_id"]

        # -- x0: the governed read the ModelCall runs on -------------------
        # A prompt is knowledge, so it is read here at a revision rather than
        # compiled in, and the revision travels onto the extraction step.
        prompt = GovernedPrompt.model_validate(ctx.step(
            "x0", "read-prompt", "transform",
            fn=lambda: self.read_prompt().model_dump(),
            summary=lambda out: (
                f"{out['source_ref']}: {len(out['text'])} characters"
                + ("" if out["found_asset"]
                   else " — not in the corpus, running on the built-in default")
            ),
        ))
        ctx.note_refs(prompt.source_ref)

        # -- m1: one call reads the whole document ------------------------
        extraction = InvoiceExtraction.model_validate(ctx.step(
            "m1", "extract-invoice", "modelcall",
            fn=lambda: self._extract(
                image, filename, hint, document_id, prompt.text
            ).model_dump(),
            summary=lambda out: (
                f"{out['model']}: {out['vendor_name'] or 'unnamed vendor'} · "
                f"{out['invoice_number']} · ${out['total_usd']:,.2f} · "
                f"{len(out['lines'])} line(s) · "
                f"confidence {out['confidence']:.2f}"
            ),
            model=getattr(self.reader, "name", ""),
            refs=[f"document:{document_id}", prompt.source_ref],
            retries=1, escalate_queue=CLERK_QUEUE,
        ))

        # -- z1: the printed name becomes one canonical vendor ------------
        # A resolution, not a decision: the resolver picked a winner among
        # candidates by the governed thresholds and nobody was asked. Automatic
        # like an observation, ledgered like a decision — which is exactly what
        # the doctrine asks of the third regime.
        resolve_act = self.gateway.action(
            "resolve-identities", flow_principal(FLOW_ID),
            run=ctx.record, step_id="z1",
        )
        resolution_raw = ctx.step(
            "z1", "resolve-vendor", "resolve",
            fn=lambda: resolve_act.commit(
                lambda w: self._resolve_vendor(extraction, ctx.record.id, w)
            ),
            writes=lambda _out: resolve_act.writes,
            summary=lambda out: (
                f"{out['disposition']}: {out['label'] or '—'} "
                f"({out['method']} {out['score']:.2f}) — {out['reason']}"
            ),
        )
        vendor_id = resolution_raw.get("vendor_id", "")
        vendor_label = resolution_raw.get("label", "")

        if resolution_raw["disposition"] == "review":
            # A per-case flow *does* stop here: posting against the wrong
            # vendor is not a recoverable mistake, and the candidates are
            # already scored, so the question is cheap for a human to answer.
            decision = ctx.gate(
                "k0", "confirm-vendor",
                build_task=lambda: self._build_vendor_task(
                    ctx.record, extraction, resolution_raw, submitted_by
                ),
            )
            # The clerk named a vendor: a decision, and a consequential one —
            # posting against the wrong company is not a recoverable mistake.
            confirm = self.gateway.action(
                "confirm-vendor", ctx.principal or AP_FLOW,
                run=ctx.record, step_id="a0",
                task_id=f"task_{ctx.record.id}_k0",
                decided_by=decision.decided_by,
            )
            chosen = ctx.step(
                "a0", "apply-vendor-decision", "action",
                fn=lambda: confirm.commit(lambda w: self._apply_vendor_decision(
                    extraction, resolution_raw, decision, ctx.record.id, w,
                )),
                writes=lambda _out: confirm.writes,
                summary=lambda out: (
                    f"{out['action']} {out['label']} by {out['decided_by']}"
                ),
            )
            vendor_id = chosen["vendor_id"]
            vendor_label = chosen["label"]

        # -- x3: the lines become facts -----------------------------------
        lines = ctx.step(
            "x3", "project-lines", "transform",
            fn=lambda: [line.model_dump() for line in extraction.lines],
            summary=lambda out: (
                f"{len(out)} line(s) · "
                f"{sum(1 for line in out if line['kind'] == 'goods')} goods, "
                f"{sum(1 for line in out if line['kind'] != 'goods')} "
                "freight/tax"
            ),
        )

        # -- x4/c1: the three-way match, against the synced graph ----------
        context = ctx.step(
            "x4", "gather-po-and-receiving", "transform",
            fn=lambda: self._gather_match_context(vendor_id, extraction.po_number),
            summary=lambda out: (
                f"{len(out['po_numbers'])} PO(s) and "
                f"{len(out['receipt_numbers'])} receiving report(s) in the "
                "graph for this invoice"
                if out["po_numbers"] or out["receipt_numbers"] else
                "no purchase order or receiving report in the graph for this "
                "invoice"
            ),
        )
        policy_content, policy_ref = self._policy_asset_content()
        match_raw = ctx.step(
            "c1", "three-way-match", "check",
            fn=lambda: match_invoice(
                [
                    MatchLine.model_validate(line)
                    for line in _invoice_match_lines(extraction)
                ],
                [MatchLine.model_validate(line) for line in context["po_lines"]],
                [MatchLine.model_validate(line) for line in context["receipt_lines"]],
                parse_match_tolerances(policy_content, policy_ref),
                invoice_total_usd=extraction.total_usd,
            ).model_dump(),
            summary=lambda out: MatchResult.model_validate(out).summary(),
            refs=[policy_ref] + [
                f"graph:PurchaseOrder/{n}" for n in context["po_numbers"]
            ],
        )
        match = MatchResult.model_validate(match_raw)

        # -- c2: anomaly, against this vendor's own history ----------------
        screen = ctx.step(
            "c2", "anomaly-screen", "check",
            fn=lambda: self._screen(
                vendor_id, extraction, policy_content, policy_ref
            ),
            summary=lambda out: (
                f"{len(out['findings'])} finding(s): "
                + "; ".join(f["kind"] for f in out["findings"])
                + f" · basis: {out['basis']}"
                if out["findings"] else f"nothing unusual · basis: {out['basis']}"
            ),
            refs=[policy_ref],
        )

        # -- r2/k2: post or review ----------------------------------------
        policy_raw = ctx.step(
            "x5", "read-posting-policy", "transform",
            fn=lambda: self.read_policy().model_dump(),
            summary=lambda out: (
                f"{out['source_ref']}: auto-post ≤ ${out['limit_usd']:,.2f} · "
                f"min confidence {out['min_confidence']:.2f}"
            ),
        )
        policy = PostingPolicy.model_validate(policy_raw)
        ctx.note_refs(policy.source_ref)

        state: PayableState = "posted"
        reason = "auto-posted within governed policy"
        decided_by = ""
        decision_note = ""

        for round_number in range(1, MAX_REVIEW_ROUNDS + 1):
            checked = ctx.step(
                f"r2_{round_number}", "post-or-review", "rule",
                fn=lambda: _posting_check(extraction, match, screen, policy),
                summary=lambda out: (
                    "review: " + "; ".join(out["reasons"]) if out["reasons"]
                    else "post: within limit, matched, nothing unusual"
                ),
                refs=[policy.source_ref],
            )
            reasons = checked["reasons"]
            if not reasons:
                if round_number > 1:
                    reason = f"posted after review by {decided_by}"
                break
            decision = ctx.gate(
                f"k2_{round_number}", "review-invoice",
                build_task=lambda rs=reasons, rn=round_number: self._build_review_task(
                    ctx.record, extraction, match, screen, policy, rs, rn,
                    vendor_label, submitted_by,
                ),
            )
            decided_by = decision.decided_by
            decision_note = decision.note or decision.correction
            if decision.verdict == "rejected":
                state = "denied"
                reason = decision.note or "rejected on review"
                break
            if decision.verdict == "approved":
                state = "posted"
                reason = "posted on review despite: " + "; ".join(reasons)
                break
            # `corrected`: the clerk fixed the extraction, so re-run the checks
            # against the corrected values rather than trusting the override.
            patched = ctx.step(
                f"x6_{round_number}", "apply-correction", "transform",
                fn=lambda d=decision: _apply_invoice_patch(extraction, d.payload),
                summary=lambda out: (
                    f"clerk corrected {', '.join(out['changed']) or 'nothing'}"
                ),
            )
            extraction = InvoiceExtraction.model_validate(patched["extraction"])
            match_raw = ctx.step(
                f"c1_{round_number}", "three-way-match", "check",
                fn=lambda: match_invoice(
                    [
                        MatchLine.model_validate(line)
                        for line in _invoice_match_lines(extraction)
                    ],
                    [MatchLine.model_validate(line) for line in context["po_lines"]],
                    [MatchLine.model_validate(line) for line in context["receipt_lines"]],
                    parse_match_tolerances(policy_content, policy_ref),
                    invoice_total_usd=extraction.total_usd,
                ).model_dump(),
                summary=lambda out: MatchResult.model_validate(out).summary(),
            )
            match = MatchResult.model_validate(match_raw)
        else:
            state = "pending"
            reason = (
                f"still flagged after {MAX_REVIEW_ROUNDS} review round(s) — "
                "left for the AP lead"
            )

        return self._finish(
            ctx, submitted_by, submitted_at, filename, document_id, vendor_id,
            vendor_label, resolution_raw, extraction, match, screen, policy,
            state, reason, decided_by, decision_note,
        )

    # -- step internals -----------------------------------------------------

    def _store(
        self, image_b64: str, filename: str, submitted_by: str, run_id: str
    ) -> dict:
        if self.documents is None:
            return {"document_id": f"doc_inline_{run_id[-8:]}", "byte_size": 0}
        record = self.documents.put_bytes(
            image_b64, filename=filename, kind="invoice",
            uploaded_by=submitted_by, run_id=run_id,
        )
        return {"document_id": record.id, "byte_size": record.byte_size}

    def _extract(
        self,
        image: bytes | None,
        filename: str,
        hint: str,
        document_id: str,
        prompt: str,
    ) -> InvoiceExtraction:
        extraction = self.reader.read(image, filename, hint, prompt=prompt)
        if self.documents is not None and extraction.regions:
            self.documents.set_regions(document_id, extraction.regions)
        return extraction

    def _resolve_vendor(
        self,
        extraction: InvoiceExtraction,
        run_id: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        """The name the model read becomes a canonical Vendor id.

        The model contributes text; the resolver decides identity, against the
        vendor master and the governed thresholds. That division is the whole
        reason a vendor is an entity here rather than a string on a row.
        """
        thresholds = read_resolution_thresholds(self.knowledge)
        name = extraction.vendor_name.strip()
        if self.graph is None:
            return {
                "disposition": "new", "vendor_id": "", "label": name,
                "score": 0.0, "method": "none", "reason": "no graph wired",
                "candidates": [], "thresholds_ref": thresholds.source_ref,
            }
        outcome = resolve(
            self.graph, "Vendor", [name] if name else [], thresholds,
            tax_id=extraction.vendor_tax_id,
        )
        payload = outcome.model_dump()
        payload["vendor_id"] = outcome.object_id
        if outcome.disposition == "new" and name:
            # Mint it now, `onboarding`, so the rest of the run has an id to
            # hang facts off. It graduates to `active` when a human posts one
            # of its invoices — the unknown-vendor onboarding step the
            # doctrine describes.
            source = f"model:{extraction.model}"
            vendor = (writer or self.graph).upsert(
                "Vendor", name,
                [
                    Claim(field="name", value=name, source=source,
                          confidence=extraction.confidence),
                    Claim(field="slug", value=_name_to_slug(name),
                          source=f"flow:{FLOW_ID}"),
                    Claim(field="status", value="onboarding",
                          source=f"flow:{FLOW_ID}"),
                ]
                + ([Claim(field="tax_id", value=extraction.vendor_tax_id,
                          source=source)]
                   if extraction.vendor_tax_id else []),
                label=name,
                provenance=f"first seen on an invoice, run {run_id}",
            )
            payload["vendor_id"] = vendor.id
            payload["label"] = vendor.label
        elif outcome.disposition == "linked":
            existing = self.graph.get(outcome.object_id)
            payload["label"] = existing.label if existing else outcome.label
        return payload

    def _apply_vendor_decision(
        self,
        extraction: InvoiceExtraction,
        resolution_raw: dict,
        decision,
        run_id: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        """Assert the vendor the clerk named. Nothing was asserted before this."""
        target = str(decision.payload.get("object_id") or "")
        if decision.verdict in ("approved", "corrected") and not target:
            ranked = resolution_raw.get("candidates") or []
            target = ranked[0]["object_id"] if ranked else ""
        graph = writer if writer is not None else self.graph
        if target and self.graph is not None:
            vendor = self.graph.get(target)
            if vendor is not None:
                graph.upsert(
                    "Vendor", vendor.natural_key,
                    [Claim(
                        field="aliases", value=[extraction.vendor_name],
                        source=f"human:{decision.decided_by}",
                    )],
                    label=vendor.label,
                )
                return {
                    "action": "linked", "vendor_id": vendor.id,
                    "label": vendor.label, "decided_by": decision.decided_by,
                }
        name = extraction.vendor_name.strip() or "Unknown vendor"
        if self.graph is None:
            return {
                "action": "unresolved", "vendor_id": "", "label": name,
                "decided_by": decision.decided_by,
            }
        vendor = graph.upsert(
            "Vendor", name,
            [
                Claim(field="name", value=name,
                      source=f"human:{decision.decided_by}"),
                Claim(field="status", value="onboarding",
                      source=f"human:{decision.decided_by}"),
            ],
            label=name,
            provenance=f"clerk said this is a new vendor, run {run_id}",
        )
        return {
            "action": "minted", "vendor_id": vendor.id, "label": vendor.label,
            "decided_by": decision.decided_by,
        }

    def _gather_match_context(self, vendor_id: str, po_number: str) -> dict:
        """Read the PO and receiving legs out of the graph.

        A transform, not a check: the Rule and the matcher never fetch. What
        they get is whatever the sync Flows have landed, which is why "no
        receiving report yet" is a legible state rather than a mystery.
        """
        po_lines: list[dict] = []
        receipt_lines: list[dict] = []
        po_numbers: list[str] = []
        receipt_numbers: list[str] = []
        if self.graph is None:
            return {
                "po_lines": po_lines, "receipt_lines": receipt_lines,
                "po_numbers": po_numbers, "receipt_numbers": receipt_numbers,
            }

        orders = []
        if po_number:
            order = self.graph.get(object_id("PurchaseOrder", po_number))
            if order is not None:
                orders.append(order)
        if not orders and vendor_id:
            # No PO on the document: an invoice can still be matched against
            # this vendor's open orders, which is what a clerk would do.
            orders = [
                other
                for _, other in self.graph.neighbors(vendor_id, "ordered_from", "in")
                if other.props.get("status") != "cancelled"
            ]
        for order in orders:
            number = str(order.props.get("po_number") or order.label)
            po_numbers.append(number)
            for link, line in self.graph.neighbors(order.id, "has_po_line", "out"):
                po_lines.append(MatchLine(
                    ordinal=int(link.props.get("ordinal", 0) or 0),
                    parent_ref=order.ref(), parent_number=number,
                    sku=str(line.props.get("sku") or ""),
                    description=str(line.props.get("description") or ""),
                    quantity=float(line.props.get("quantity_ordered") or 0),
                    unit_price_usd=float(line.props.get("unit_price_usd") or 0),
                    amount_usd=float(line.props.get("amount_usd") or 0),
                ).model_dump())
            for _, report in self.graph.neighbors(order.id, "receives_against", "in"):
                number_rr = str(report.props.get("receipt_number") or report.label)
                receipt_numbers.append(number_rr)
                for link, line in self.graph.neighbors(
                    report.id, "has_receiving_line", "out"
                ):
                    receipt_lines.append(MatchLine(
                        ordinal=int(link.props.get("ordinal", 0) or 0),
                        parent_ref=report.ref(), parent_number=number_rr,
                        sku=str(line.props.get("sku") or ""),
                        description=str(line.props.get("description") or ""),
                        quantity=float(line.props.get("quantity_received") or 0),
                    ).model_dump())
        return {
            "po_lines": po_lines, "receipt_lines": receipt_lines,
            "po_numbers": sorted(set(po_numbers)),
            "receipt_numbers": sorted(set(receipt_numbers)),
        }

    def _screen(
        self,
        vendor_id: str,
        extraction: InvoiceExtraction,
        policy_content: str,
        policy_ref: str,
    ) -> dict:
        thresholds = parse_anomaly_thresholds(policy_content, policy_ref)
        if self.anomaly is None or not vendor_id:
            return {
                "findings": [], "basis": "no vendor profile available",
                "profile_invoices": 0, "thresholds_ref": policy_ref,
            }
        return self.anomaly.screen(
            vendor_id, extraction.invoice_number, extraction.invoice_date,
            extraction.total_usd, thresholds,
        )

    # -- tasks --------------------------------------------------------------

    def _build_vendor_task(
        self,
        record: RunRecord,
        extraction: InvoiceExtraction,
        resolution_raw: dict,
        submitted_by: str,
    ) -> Task:
        ranked = resolution_raw.get("candidates") or []
        printed = extraction.vendor_name or "no clear party"
        return Task(
            id=f"task_{record.id}_k0",
            kind="match_review",
            title=f"Which vendor issued this invoice? {printed}",
            summary=(
                f"The document is billed by {printed}. "
                f"{resolution_raw.get('reason', '')}. Nothing has been asserted: "
                "approve to accept the top candidate, correct with the id of "
                "the right vendor, or reject to onboard this as a new one."
            ),
            queue=CLERK_QUEUE,
            priority="high",
            flow_id=FLOW_ID,
            run_id=record.id,
            case_ref=record.case_ref or record.id,
            refs=[resolution_raw.get("thresholds_ref", "")]
            + [f"graph:Vendor/{c['object_id']}" for c in ranked[:3]],
            evidence=[
                TaskEvidence(label="Submitted by", value=submitted_by),
                TaskEvidence(label="Printed on the document", value=printed),
                TaskEvidence(label="Tax id", value=extraction.vendor_tax_id or "—"),
                TaskEvidence(label="Invoice number",
                             value=extraction.invoice_number or "—"),
                TaskEvidence(label="Total",
                             value=f"${extraction.total_usd:,.2f}"),
            ] + [
                TaskEvidence(
                    label=f"Candidate {i + 1} ({c['score']:.2f})",
                    value=f"{c['label']} — {c['reason']}",
                )
                for i, c in enumerate(ranked[:3])
            ],
            proposal=TaskProposal(
                action="link-identity", target="vendor-master",
                summary=(
                    f"Treat this invoice as issued by {ranked[0]['label']}"
                    if ranked else "Onboard a new vendor"
                ),
            ),
            resumes="this run resumes at k0 and matches against the chosen "
                    "vendor's orders",
            created_at_iso=utcnow().isoformat(),
        )

    def _build_review_task(
        self,
        record: RunRecord,
        extraction: InvoiceExtraction,
        match: MatchResult,
        screen: dict,
        policy: PostingPolicy,
        reasons: list[str],
        round_number: int,
        vendor_label: str,
        submitted_by: str,
    ) -> Task:
        findings = screen.get("findings") or []
        return Task(
            id=f"task_{record.id}_k2_{round_number}",
            kind="decision",
            title=(
                f"Review invoice {extraction.invoice_number} · "
                f"${extraction.total_usd:,.2f}"
            ),
            summary=(
                "The post-or-review rule held this invoice: "
                + "; ".join(reasons)
                + ". The reviewer may correct extracted values and line "
                "amounts, in which case the match and the screen re-run."
            ),
            queue=CLERK_QUEUE,
            priority="high" if findings or match.verdict != "matched" else "normal",
            flow_id=FLOW_ID,
            run_id=record.id,
            case_ref=record.case_ref or record.id,
            refs=[policy.source_ref, match.tolerance_ref]
            + [f"graph:PurchaseOrder/{n}" for n in match.po_numbers],
            evidence=[
                TaskEvidence(label="Vendor", value=vendor_label or extraction.vendor_name),
                TaskEvidence(label="Total", value=f"${extraction.total_usd:,.2f}"),
                TaskEvidence(label="Auto-post limit",
                             value=f"${policy.limit_usd:,.2f} (governed)"),
                TaskEvidence(label="Three-way match", value=match.summary()),
                TaskEvidence(label="Anomaly basis", value=str(screen.get("basis", ""))),
                TaskEvidence(label="Submitted by", value=submitted_by),
                TaskEvidence(label="Review round", value=str(round_number)),
            ] + [
                TaskEvidence(label=f"Anomaly: {f['kind']}", value=f["detail"])
                for f in findings[:4]
            ] + [
                TaskEvidence(label=f"Line {p.invoice_ordinal} ({p.verdict})",
                             value=p.reason)
                for p in match.pairings if not p.clean
            ][:4],
            proposal=TaskProposal(
                action="post-invoice", target="ap-records",
                summary=(
                    f"Post {extraction.invoice_number} for "
                    f"${extraction.total_usd:,.2f} to the AP ledger"
                ),
            ),
            resumes=f"this run resumes at k2_{round_number} and re-runs the rule",
            created_at_iso=utcnow().isoformat(),
        )

    # -- the commit ---------------------------------------------------------

    def _finish(
        self,
        ctx: RunContext,
        submitted_by: str,
        submitted_at: str,
        filename: str,
        document_id: str,
        vendor_id: str,
        vendor_label: str,
        resolution_raw: dict,
        extraction: InvoiceExtraction,
        match: MatchResult | None,
        screen: dict | None,
        policy: PostingPolicy,
        state: PayableState,
        reason: str,
        decided_by: str,
        decision_note: str,
    ) -> dict:
        record = InvoiceRecord(
            id=ctx.record.case_ref or ctx.record.id,
            run_id=ctx.record.id,
            submitted_by=submitted_by,
            submitted_at_iso=submitted_at,
            filename=filename,
            document_id=document_id,
            vendor_id=vendor_id,
            vendor_slug=extraction.vendor_slug or _name_to_slug(vendor_label),
            vendor_name=vendor_label or extraction.vendor_name,
            vendor_confidence=float(resolution_raw.get("score", 0) or 0),
            vendor_resolution=str(resolution_raw.get("disposition", "")),
            vendor_method=str(resolution_raw.get("method", "")),
            vendor_tax_id=extraction.vendor_tax_id,
            invoice_number=extraction.invoice_number,
            invoice_date=extraction.invoice_date,
            due_date=extraction.due_date,
            po_number=extraction.po_number,
            subtotal_usd=extraction.subtotal_usd,
            freight_usd=extraction.freight_usd,
            tax_usd=extraction.tax_usd,
            total_usd=extraction.total_usd,
            currency=extraction.currency,
            line_summary=extraction.line_summary,
            lines=extraction.lines,
            match_verdict=match.verdict if match else "",
            match_summary=match.summary() if match else "",
            match_pairings=[p.model_dump() for p in match.pairings] if match else [],
            anomaly_findings=list((screen or {}).get("findings") or []),
            anomaly_basis=str((screen or {}).get("basis") or ""),
            state=state,
            verdict_reason=reason,
            policy_ref=policy.source_ref,
            post_limit_usd=policy.limit_usd,
            task_id=next((s.task_id for s in ctx.record.steps if s.task_id), ""),
            decided_by=decided_by,
            decision_note=decision_note,
            three_way_match=match.verdict if match else "not evaluated",
            fraud_screen=(
                f"{len((screen or {}).get('findings') or [])} anomaly finding(s)"
                if screen else "not evaluated"
            ),
        )

        # The commit, through the Gateway. Who commits it is the human who
        # released the last gate; when the invoice cleared the governed
        # auto-post limit with no gate at all, nobody decided, so the Flow
        # commits on its own authority and the ledger says so rather than
        # crediting the submitter with a judgment they never made.
        post = self.gateway.action(
            "post-invoice", ctx.principal or AP_FLOW,
            run=ctx.record, step_id="a1",
            task_id=record.task_id, decided_by=decided_by,
        )
        written = ctx.step(
            "a1", "post-invoice", "action",
            fn=lambda: post.commit(
                lambda w: self._write_graph(record, match, ctx.record.id, w)
            ),
            writes=lambda _out: post.writes,
            summary=lambda out: (
                f"{record.state} · ${record.total_usd:,.2f} · {out['objects']} "
                f"object(s) and {out['links']} link(s) written · ledger "
                f"{post.entry.id}"
            ),
        )
        record.graph_ref = written.get("invoice_ref", "")
        record.match_ref = written.get("match_ref", "")
        record.journal = [
            JournalEvent.model_validate(e) for e in ctx.record.journal()
        ]
        self.directory.put(record)

        ctx.event("e1", "InvoicePosted", f"emitted on commit · {record.state}")
        record.journal = [
            JournalEvent.model_validate(e) for e in ctx.record.journal()
        ]
        self.directory.put(record)

        log.info(
            "Invoice recorded",
            extra={
                "invoiceId": record.id, "runId": ctx.record.id,
                "state": record.state, "amount": record.total_usd,
                "vendorId": vendor_id,
                "match": record.match_verdict,
                "anomalies": len(record.anomaly_findings),
            },
        )
        return {
            "invoice_id": record.id, "state": record.state,
            "total_usd": record.total_usd, "match": record.match_verdict,
            "anomalies": len(record.anomaly_findings),
        }

    def _write_graph(
        self,
        record: InvoiceRecord,
        match: MatchResult | None,
        run_id: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        if self.graph is None:
            return {"objects": 0, "links": 0, "invoice_ref": "", "match_ref": ""}
        graph = writer if writer is not None else self.graph
        source = f"flow:{FLOW_ID}"
        model_source = "model:invoice-extraction"
        # Keyed by the vendor's slug rather than its generated id: a natural
        # key has to be something a person — or an eval fixture — can write,
        # and the slug normalizes from the same name the id hashes from, so it
        # is exactly as unique.
        slug = record.vendor_slug or _name_to_slug(record.vendor_name)
        natural = f"{slug}:{record.invoice_number}"
        invoice = graph.upsert(
            "Invoice", natural,
            [
                Claim(field="invoice_number", value=record.invoice_number, source=model_source),
                Claim(field="invoice_date", value=record.invoice_date, source=model_source),
                Claim(field="due_date", value=record.due_date, source=model_source),
                Claim(field="po_number", value=record.po_number, source=model_source),
                Claim(field="subtotal_usd", value=record.subtotal_usd, source=model_source),
                Claim(field="freight_usd", value=record.freight_usd, source=model_source),
                Claim(field="tax_usd", value=record.tax_usd, source=model_source),
                Claim(field="total_usd", value=record.total_usd, source=model_source),
                Claim(field="currency", value=record.currency, source=model_source),
                Claim(field="state", value=record.state, source=source),
            ],
            label=f"{record.vendor_name} {record.invoice_number}",
            provenance=f"run {run_id}",
        )
        objects, links = 1, 0
        if record.vendor_id:
            graph.link(
                "issued", record.vendor_id, invoice.id,
                props={"invoice_date": record.invoice_date}, provenance=run_id,
            )
            links += 1
            # The vendor graduates. A vendor minted from an invoice is
            # `onboarding` until a person posts one of its invoices — that
            # decision, and nothing automatic, is the confirmation it is real.
            vendor = self.graph.get(record.vendor_id)
            if (
                vendor is not None
                and record.state == "posted"
                and record.decided_by
                and vendor.props.get("status") == "onboarding"
            ):
                graph.upsert(
                    "Vendor", vendor.natural_key,
                    [Claim(field="status", value="active",
                           source=f"human:{record.decided_by}")],
                    label=vendor.label,
                    provenance=f"vouched for by posting {record.invoice_number}",
                )
        if record.document_id:
            document = graph.upsert(
                "Document", record.document_id,
                [
                    Claim(field="kind", value="invoice", source=source),
                    Claim(field="filename", value=record.filename, source=source),
                ],
                label=record.document_id,
            )
            objects += 1
            graph.link(
                "evidenced_by_document", invoice.id, document.id, provenance=run_id
            )
            links += 1

        line_ids: list[str] = []
        for ordinal, line in enumerate(record.lines, start=1):
            line_obj = graph.upsert(
                "InvoiceLine", f"{natural}#{ordinal}",
                [
                    Claim(field="description", value=line.description, source=model_source),
                    Claim(field="sku", value=line.sku, source=model_source),
                    Claim(field="quantity", value=line.quantity, source=model_source),
                    Claim(field="unit_price_usd", value=line.unit_price_usd, source=model_source),
                    Claim(field="amount_usd", value=line.amount_usd, source=model_source),
                    Claim(field="kind", value=line.kind, source=model_source),
                ],
                label=f"{record.invoice_number} line {ordinal}",
            )
            objects += 1
            line_ids.append(line_obj.id)
            graph.link(
                "has_invoice_line", invoice.id, line_obj.id,
                props={"ordinal": ordinal}, provenance=run_id,
            )
            links += 1

        match_ref = ""
        if match is not None:
            match_obj = graph.upsert(
                "MatchSet", natural,
                [
                    Claim(field="verdict", value=match.verdict, source=source),
                    Claim(field="matched_lines", value=match.matched_lines, source=source),
                    Claim(field="unmatched_lines", value=match.unmatched_lines, source=source),
                    Claim(field="price_variance_usd", value=match.price_variance_usd, source=source),
                    Claim(field="quantity_variance", value=match.quantity_variance, source=source),
                    Claim(field="tolerance_ref", value=match.tolerance_ref, source=source),
                    Claim(field="po_numbers", value=match.po_numbers, source=source),
                    Claim(field="receipt_numbers", value=match.receipt_numbers, source=source),
                ],
                label=f"match for {record.invoice_number}: {match.verdict}",
                provenance=f"run {run_id}",
            )
            objects += 1
            match_ref = match_obj.ref()
            graph.link("matched_by", invoice.id, match_obj.id, provenance=run_id)
            links += 1
            for pairing in match.pairings:
                index = pairing.invoice_ordinal - 1
                if 0 <= index < len(line_ids):
                    graph.link(
                        "matches_invoice_line", match_obj.id, line_ids[index],
                        props={
                            "verdict": pairing.verdict,
                            "po_number": pairing.po_number,
                            "po_ordinal": pairing.po_ordinal,
                            "receipt_number": pairing.receipt_number,
                            "price_variance_usd": pairing.price_variance_usd,
                            "quantity_variance": pairing.quantity_variance,
                            "reason": pairing.reason,
                        },
                        method="derived",
                        confidence=pairing.po_match_score or 1.0,
                        provenance=run_id,
                    )
                    links += 1
            for number in match.po_numbers:
                order = self.graph.get(object_id("PurchaseOrder", number))
                if order is not None:
                    graph.link(
                        "billed_against", invoice.id, order.id, provenance=run_id
                    )
                    links += 1
        return {
            "objects": objects, "links": links,
            "invoice_ref": invoice.ref(), "match_ref": match_ref,
        }

    # -- projection ---------------------------------------------------------

    def _project(self, record: RunRecord) -> InvoiceRecord:
        """The row for a run, whatever state the run is in.

        A suspended run has no `a1` step yet, so this assembles the row out of
        the journal instead. That matters more than it sounds: an invoice
        waiting on a clerk is exactly when someone wants to see *why* — which
        lines failed the match, what the anomaly screen said, which rule held
        it. Reading those off the completed steps means the pending row is as
        informative as the posted one, without a second copy of the truth.
        """
        existing = self.directory.get(record.case_ref or record.id)
        # Only a *finished* run has an authoritative row: the commit step wrote
        # it. While the run is still moving — suspended between gates, say — the
        # row is re-derived, because the previous projection is a snapshot of a
        # state the run has already left.
        if existing is not None and record.status in ("succeeded", "failed"):
            existing.journal = [
                JournalEvent.model_validate(e) for e in record.journal()
            ]
            existing.run_id = record.id
            self.directory.put(existing)
            return existing

        inputs = record.inputs
        step = record.step

        def output(step_id: str, default=None):
            found = step(step_id)
            return (found.output if found is not None else None) or default

        resolution = output("z1", {})
        stored = output("s1", {})

        extraction = InvoiceExtraction()
        # The last correction wins, then the extraction itself — newest
        # reading of the document first.
        sources = [f"x6_{n}" for n in range(MAX_REVIEW_ROUNDS, 0, -1)] + ["m1"]
        for step_id in sources:
            payload = output(step_id)
            if not payload:
                continue
            if isinstance(payload, dict) and "extraction" in payload:
                payload = payload["extraction"]
            try:
                extraction = InvoiceExtraction.model_validate(payload)
                break
            except Exception:  # noqa: BLE001 — a partial run is not an error
                continue

        match: MatchResult | None = None
        for step_id in [f"c1_{n}" for n in range(MAX_REVIEW_ROUNDS, 0, -1)] + ["c1"]:
            payload = output(step_id)
            if payload:
                try:
                    match = MatchResult.model_validate(payload)
                    break
                except Exception:  # noqa: BLE001
                    continue
        screen = output("c2", {}) or {}
        policy = PostingPolicy.model_validate(output("x5", {}) or {})

        reasons: list[str] = []
        for n in range(MAX_REVIEW_ROUNDS, 0, -1):
            checked = output(f"r2_{n}", {})
            if checked and checked.get("reasons"):
                reasons = list(checked["reasons"])
                break

        gate = next(
            (s for s in reversed(record.steps) if s.kind == "task"), None
        )
        decision = gate.decision if gate is not None else None
        state: PayableState = (
            "pending" if record.status in ("running", "suspended")
            else ("denied" if record.status == "failed" else "posted")
        )
        if state == "pending" and reasons:
            reason = "; ".join(reasons)
        elif record.error:
            reason = record.error
        elif record.status == "suspended":
            reason = (
                f"suspended at {record.suspended_at_step} awaiting a decision"
            )
        else:
            reason = "in flight"

        projected = InvoiceRecord(
            id=record.case_ref or record.id,
            run_id=record.id,
            submitted_by=inputs.get("submitted_by", ""),
            submitted_at_iso=inputs.get("submitted_at_iso", record.started_at_iso),
            filename=inputs.get("filename", ""),
            document_id=str(stored.get("document_id", "")),
            vendor_id=str(resolution.get("vendor_id", "")),
            vendor_name=str(resolution.get("label", "") or extraction.vendor_name),
            vendor_slug=extraction.vendor_slug,
            vendor_confidence=float(resolution.get("score", 0) or 0),
            vendor_resolution=str(resolution.get("disposition", "")),
            vendor_method=str(resolution.get("method", "")),
            vendor_tax_id=extraction.vendor_tax_id,
            invoice_number=extraction.invoice_number,
            invoice_date=extraction.invoice_date,
            due_date=extraction.due_date,
            po_number=extraction.po_number,
            subtotal_usd=extraction.subtotal_usd,
            freight_usd=extraction.freight_usd,
            tax_usd=extraction.tax_usd,
            total_usd=extraction.total_usd,
            currency=extraction.currency,
            line_summary=extraction.line_summary,
            lines=extraction.lines,
            match_verdict=match.verdict if match else "",
            match_summary=match.summary() if match else "",
            match_pairings=[p.model_dump() for p in match.pairings] if match else [],
            anomaly_findings=list(screen.get("findings") or []),
            anomaly_basis=str(screen.get("basis") or ""),
            state=state,
            verdict_reason=reason,
            policy_ref=policy.source_ref,
            post_limit_usd=policy.limit_usd,
            task_id=record.open_task_id
            or next((s.task_id for s in record.steps if s.task_id), ""),
            decided_by=(decision or {}).get("decided_by", "") if decision else "",
            decision_note=(decision or {}).get("note", "") if decision else "",
            three_way_match=match.verdict if match else "not evaluated",
            fraud_screen=(
                f"{len(screen.get('findings') or [])} anomaly finding(s)"
                if screen else "not evaluated"
            ),
            journal=[JournalEvent.model_validate(e) for e in record.journal()],
        )
        return self.directory.put(projected)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _invoice_match_lines(extraction: InvoiceExtraction) -> list[dict]:
    return [
        MatchLine(
            ordinal=ordinal, parent_number=extraction.invoice_number,
            sku=line.sku, description=line.description, quantity=line.quantity,
            unit_price_usd=line.unit_price_usd, amount_usd=line.amount_usd,
            kind=line.kind,
        ).model_dump()
        for ordinal, line in enumerate(extraction.lines, start=1)
    ]


def _posting_check(
    extraction: InvoiceExtraction,
    match: MatchResult,
    screen: dict,
    policy: PostingPolicy,
) -> dict:
    """The Rule: a pure check over what the flow gathered. It never fetches."""
    reasons: list[str] = []
    if extraction.total_usd > policy.limit_usd:
        reasons.append(
            f"${extraction.total_usd:,.2f} exceeds the ${policy.limit_usd:,.2f} "
            "auto-post limit"
        )
    if extraction.confidence and extraction.confidence < policy.min_confidence:
        reasons.append(
            f"extraction confidence {extraction.confidence:.2f} is below "
            f"{policy.min_confidence:.2f}"
        )
    if match.verdict == "variance":
        reasons.append(
            f"three-way match found {match.unmatched_lines} line(s) out of "
            f"tolerance: {match.notes[0] if match.notes else 'see the match set'}"
        )
    elif match.verdict == "unmatched":
        reasons.append("no purchase order to match this invoice against")
    elif match.verdict == "blocked":
        reasons.append(
            "goods on this invoice are not booked in yet — a receiving report "
            "is required at this amount"
        )
    for finding in screen.get("findings") or []:
        reasons.append(f"anomaly ({finding['kind']}): {finding['detail']}")
    # A quiet arithmetic check the extractors cannot do for themselves: the
    # lines have to add up to the total, or one of the two is wrong.
    line_total = round(sum(line.amount_usd for line in extraction.lines), 2)
    if extraction.lines and abs(line_total - extraction.total_usd) > 0.05:
        reasons.append(
            f"lines sum to ${line_total:,.2f} but the invoice total reads "
            f"${extraction.total_usd:,.2f}"
        )
    return {"reasons": reasons}


def _apply_invoice_patch(extraction: InvoiceExtraction, payload: dict) -> dict:
    """Apply a clerk's structured correction to an extraction.

    Payload shape (all keys optional):

        {"invoice_number": "...", "invoice_date": "...", "po_number": "...",
         "total_usd": 1234.56,
         "lines": [{"ordinal": 1, "quantity": 3, "unit_price_usd": 486.00,
                    "amount_usd": 1458.00, "sku": "...", "description": "..."}],
         "remove_ordinals": [4]}

    Line amounts are recomputed from quantity × unit price when the clerk gives
    both and no explicit amount, because that is the arithmetic they would
    otherwise have to do by hand — and getting it wrong here would fail the
    lines-sum check on the next round for no reason.
    """
    changed: list[str] = []
    patched = extraction.model_copy(deep=True)
    for field in ("invoice_number", "invoice_date", "due_date", "po_number", "currency"):
        if payload.get(field):
            setattr(patched, field, str(payload[field]))
            changed.append(field)
    for field in ("subtotal_usd", "freight_usd", "tax_usd", "total_usd"):
        if payload.get(field) is not None:
            setattr(patched, field, round(float(payload[field]), 2))
            changed.append(field)

    by_ordinal = {index: line for index, line in enumerate(patched.lines, start=1)}
    for patch in payload.get("lines") or []:
        ordinal = int(patch.get("ordinal", 0) or 0)
        line = by_ordinal.get(ordinal)
        if line is None:
            continue
        for field in ("sku", "description", "kind"):
            if patch.get(field):
                setattr(line, field, str(patch[field]))
        for field in ("quantity", "unit_price_usd", "amount_usd"):
            if patch.get(field) is not None:
                setattr(line, field, round(float(patch[field]), 4))
        if patch.get("amount_usd") is None and (
            patch.get("quantity") is not None or patch.get("unit_price_usd") is not None
        ):
            line.amount_usd = round(line.quantity * line.unit_price_usd, 2)
        changed.append(f"line {ordinal}")
    for ordinal in payload.get("remove_ordinals") or []:
        by_ordinal.pop(int(ordinal), None)
        changed.append(f"line {ordinal} removed")
    patched.lines = [by_ordinal[key] for key in sorted(by_ordinal)]

    if payload.get("total_usd") is None and patched.lines:
        patched.total_usd = round(
            sum(line.amount_usd for line in patched.lines), 2
        )
    patched.subtotal_usd = round(
        sum(line.amount_usd for line in patched.lines if line.kind == "goods"), 2
    )
    patched.freight_usd = round(
        sum(line.amount_usd for line in patched.lines if line.kind == "freight"), 2
    )
    # A clerk who has read the document has resolved the model's uncertainty.
    patched.confidence = 1.0
    return {"extraction": patched.model_dump(), "changed": changed}


def build_payable_service(
    backend: str,
    project: str | None,
    collection: str,
    reader: InvoiceReader,
    knowledge,
    tasks: TaskService,
    engine: RunEngine | None = None,
    graph: GraphService | None = None,
    documents: DocumentService | None = None,
    anomaly: AnomalyService | None = None,
    gateway: ActionGateway | None = None,
    dispatcher: Dispatcher | None = None,
) -> PayableService:
    directory: PayableDirectory
    if backend == "firestore":
        directory = FirestorePayableDirectory(project, collection)
    else:
        directory = InMemoryPayableDirectory()
    return PayableService(
        directory, reader, knowledge, tasks, engine, graph, documents, anomaly,
        gateway, dispatcher,
    )


# ---------------------------------------------------------------------------
# Seed world — vendors, orders, receipts and invoices, all through the engine
# ---------------------------------------------------------------------------
#
# Nothing here writes a row by hand. The vendor master is populated by real
# `vendor-sync` deliveries (including a re-delivery, to exercise echo
# suppression, and a misspelling that lands in a steward's queue); the purchase
# orders and receiving reports arrive through their own sync Flows; and every
# invoice is an actual `accounts-payable` run whose Tasks are decided through
# the same path a clerk would use.
#
# The payoff is that the demo history and the live behaviour cannot diverge:
# a bug in the matcher, the resolver or the flywheel shows up at startup.


_SEED_CATALOG_LINES = {
    "PO-7741": [("STL-0250", 2.0), ("FAS-M12", 3.0)],
    "PO-7802": [("WLD-6013", 4.0), ("PPE-GLV", 2.0)],
    "PO-7815": [("CNS-HYD", 3.0), ("FAS-M12", 2.0)],
}


def _seed_vendor_deliveries() -> list[dict]:
    """Four vendors from the ERP, one cross-system match, one re-delivery, one typo."""
    return [
        {
            "source": "erp-vendors",
            "watermark": "2026-08-01T00:00:00Z",
            "idempotency_key": "erp-vendors-001",
            "vendors": [
                {
                    "external_id": "V-10041", "name": "Acme Industrial Inc.",
                    "tax_id": "94-2711883", "status": "active",
                    "payment_terms": "NET30", "remit_country": "US",
                },
                {
                    "external_id": "V-10088", "name": "Northwind Traders LLC",
                    "tax_id": "81-4410072", "status": "active",
                    "payment_terms": "NET45", "remit_country": "US",
                },
                {
                    "external_id": "V-10102", "name": "Bay State Fasteners Co.",
                    "tax_id": "04-3390118", "status": "active",
                    "payment_terms": "NET30", "remit_country": "US",
                },
                {
                    "external_id": "V-10190", "name": "Cascade Welding Supply",
                    "tax_id": "91-2038844", "status": "active",
                    "payment_terms": "NET15", "remit_country": "US",
                },
            ],
        },
        # A second system's id for a vendor we already hold: the legal-form
        # variant normalizes to the same key, so this auto-links and the vendor
        # ends up with two source records. This is the master-data payoff.
        {
            "source": "procurement-coupa",
            "watermark": "2026-08-02T00:00:00Z",
            "idempotency_key": "coupa-001",
            "vendors": [{
                "external_id": "C-5501", "name": "ACME INDUSTRIAL, INCORPORATED",
                "tax_id": "94-2711883", "status": "active",
                "payment_terms": "NET30",
            }],
        },
        # Byte-identical re-delivery: everything lands as `unchanged`.
        {
            "source": "erp-vendors",
            "watermark": "2026-08-03T00:00:00Z",
            "idempotency_key": "erp-vendors-002",
            "vendors": [{
                "external_id": "V-10041", "name": "Acme Industrial Inc.",
                "tax_id": "94-2711883", "status": "active",
                "payment_terms": "NET30", "remit_country": "US",
            }],
        },
        # A misspelling from a legacy feed: scores in the steward band, so
        # nothing is asserted and a match_review Task waits in the queue.
        {
            "source": "legacy-ap-extract",
            "watermark": "2026-08-04T00:00:00Z",
            "idempotency_key": "legacy-001",
            "vendors": [{
                "external_id": "L-77", "name": "Acme Industial Inc",
                "status": "active",
            }],
        },
    ]


def _seed_po_delivery() -> dict:
    def lines(po_number: str) -> list[dict]:
        catalog = {sku: (description, price) for sku, description, price in CATALOG}
        out = []
        for sku, quantity in _SEED_CATALOG_LINES[po_number]:
            description, price = catalog[sku]
            out.append({
                "sku": sku, "description": description,
                "quantity_ordered": quantity, "unit_price_usd": price,
            })
        return out

    return {
        "source": "erp-purchase-orders",
        "watermark": "2026-08-05T00:00:00Z",
        "idempotency_key": "erp-po-001",
        "purchase_orders": [
            {
                "external_id": "PO-7741", "po_number": "PO-7741",
                "vendor_external_id": "V-10041", "vendor_name": "Acme Industrial Inc.",
                "ordered_date": "2026-08-05", "status": "open",
                "lines": lines("PO-7741"),
            },
            {
                "external_id": "PO-7802", "po_number": "PO-7802",
                "vendor_external_id": "V-10088", "vendor_name": "Northwind Traders LLC",
                "ordered_date": "2026-08-07", "status": "open",
                "lines": lines("PO-7802"),
            },
            {
                "external_id": "PO-7815", "po_number": "PO-7815",
                "vendor_external_id": "V-10102",
                "vendor_name": "Bay State Fasteners Co.",
                "ordered_date": "2026-08-09", "status": "open",
                "lines": lines("PO-7815"),
            },
        ],
    }


def _seed_receiving_delivery() -> dict:
    catalog = {sku: (description, price) for sku, description, price in CATALOG}

    def lines(po_number: str, shortfall: dict | None = None) -> list[dict]:
        out = []
        for sku, quantity in _SEED_CATALOG_LINES[po_number]:
            description, _ = catalog[sku]
            received = quantity - (shortfall or {}).get(sku, 0)
            out.append({
                "sku": sku, "description": description,
                "quantity_received": received,
                "condition": "short" if received < quantity else "good",
            })
        return out

    return {
        "source": "wms-receiving",
        "watermark": "2026-08-12T00:00:00Z",
        "idempotency_key": "wms-001",
        "receiving_reports": [
            {
                "external_id": "RR-5501", "receipt_number": "RR-5501",
                "po_number": "PO-7741", "received_date": "2026-08-11",
                "status": "complete", "lines": lines("PO-7741"),
            },
            # One line short: the invoice that bills the full quantity will
            # land a quantity variance, which is the point.
            {
                "external_id": "RR-5510", "receipt_number": "RR-5510",
                "po_number": "PO-7802", "received_date": "2026-08-13",
                "status": "partial", "lines": lines("PO-7802"),
            },
            {
                "external_id": "RR-5533", "receipt_number": "RR-5533",
                "po_number": "PO-7815", "received_date": "2026-08-15",
                "status": "complete", "lines": lines("PO-7815"),
            },
        ],
    }


class SeedInvoice(BaseModel):
    run_id: str
    submitted_by: str
    days_ago: int
    filename: str
    hint: str
    # Decisions applied in order to whichever gate is open at the time. Each is
    # {verdict, decided_by, note, payload}.
    decisions: list[dict] = Field(default_factory=list)
    # Rebuild the vendor profiles before this invoice runs, so the anomaly
    # screen has history to compare against.
    refresh_profiles: bool = False


def _seed_invoices() -> list[SeedInvoice]:
    return [
        # 1. Known vendor, bills its PO exactly, posts with no human.
        SeedInvoice(
            run_id="run_ap_seed01", submitted_by="ap-intake@customer.example",
            days_ago=13, filename="acme-inv-20881.pdf",
            hint="vendor=acme-industrial po=PO-7741 invoice=ACME-INV-20881 "
                 "lines=STL-0250:2,FAS-M12:3",
        ),
        # 2. Price variance: the clerk corrects the unit price, the match
        #    re-runs, and the invoice posts on the second round.
        SeedInvoice(
            run_id="run_ap_seed02", submitted_by="ap-intake@customer.example",
            days_ago=11, filename="northwind-inv-4471.pdf",
            hint="vendor=northwind-traders po=PO-7802 invoice=NW-INV-4471 "
                 "lines=WLD-6013:4,PPE-GLV:2 pricebump",
            decisions=[{
                "verdict": "corrected", "decided_by": "dana@customer.example",
                "note": "Vendor billed the old list price; corrected to the PO price.",
                "payload": {"lines": [{"ordinal": 1, "unit_price_usd": 92.40}]},
            }],
        ),
        # 3. The invoice bills more than was received, so the match holds it
        #    at the posting gate — left open, so the AP queue has a live
        #    exception with a real match set behind it.
        SeedInvoice(
            run_id="run_ap_seed03", submitted_by="ap-intake@customer.example",
            days_ago=9, filename="baystate-inv-88120.pdf",
            hint="vendor=bay-state-fasteners po=PO-7815 invoice=BSF-INV-88120 "
                 "lines=CNS-HYD:3,FAS-M12:2 overbill",
        ),
        # 4. An unknown vendor: nothing in the master resembles the name, so
        #    it is minted `onboarding`. No PO on the document either, so the
        #    invoice stops at the posting gate; a clerk posts it, and that
        #    decision is what graduates the vendor to `active`.
        SeedInvoice(
            run_id="run_ap_seed04", submitted_by="ap-intake@customer.example",
            days_ago=7, filename="meridian-first-invoice.pdf",
            hint="newvendor=meridian-supply nopo invoice=MER-0001 "
                 "lines=PPE-GLV:2 total=190.00",
            decisions=[{
                "verdict": "approved", "decided_by": "dana@customer.example",
                "note": "First invoice from this vendor, no PO expected at this size.",
            }],
        ),
        # 5. The same vendor again — now `active`, and the resolver links to
        #    the object run 4 minted rather than minting a second one.
        SeedInvoice(
            run_id="run_ap_seed05", submitted_by="ap-intake@customer.example",
            days_ago=5, filename="meridian-second-invoice.pdf",
            hint="newvendor=meridian-supply nopo invoice=MER-0002 "
                 "lines=PPE-GLV:1 total=112.00",
            decisions=[{
                "verdict": "approved", "decided_by": "dana@customer.example",
                "note": "Read matches the document; no PO at this size.",
            }],
        ),
        # 6. A large invoice from a vendor whose history says otherwise: over
        #    the auto-post limit *and* an amount outlier. Left open.
        SeedInvoice(
            run_id="run_ap_seed06", submitted_by="ap-intake@customer.example",
            days_ago=3, filename="acme-inv-21044.pdf",
            hint="vendor=acme-industrial po=PO-7741 invoice=ACME-INV-21044 "
                 "lines=STL-0375:12 total=9800.00",
            refresh_profiles=True,
        ),
        # 7. A suspected duplicate: same vendor, same total as invoice 1, a
        #    different number, inside the governed window. Rejected.
        SeedInvoice(
            run_id="run_ap_seed07", submitted_by="ap-intake@customer.example",
            days_ago=1, filename="acme-inv-21077.pdf",
            hint="vendor=acme-industrial po=PO-7741 invoice=ACME-INV-21077 "
                 "lines=STL-0250:2,FAS-M12:3 total=1425.25",
            refresh_profiles=True,
            decisions=[{
                "verdict": "rejected", "decided_by": "dana@customer.example",
                "note": "Duplicate of ACME-INV-20881 — returned to the vendor.",
            }],
        ),
    ]


def seed_payables_if_empty(service: PayableService, sync=None) -> int:
    """Build the AP world: vendors, orders, receipts, then invoices.

    `sync` is the SyncService. Without it the vendor master stays empty, every
    invoice resolves to a new vendor, and nothing matches — so the seed says so
    rather than producing a world that looks fine and demonstrates nothing.
    """
    if service.directory.list():
        return 0
    if service.engine is None:
        log.warning("No run engine wired; skipping payables seed")
        return 0

    configured = service.reader
    service.reader = StubInvoiceReader()
    seeded = 0
    try:
        if sync is not None:
            from .sync import SyncRequest

            for delivery in _seed_vendor_deliveries():
                sync.deliver(SyncRequest.model_validate(delivery), actor="system:seed")
            sync.deliver(
                SyncRequest.model_validate(_seed_po_delivery()), actor="system:seed"
            )
            sync.deliver(
                SyncRequest.model_validate(_seed_receiving_delivery()),
                actor="system:seed",
            )
        else:
            log.warning(
                "Seeding payables with no sync service: the vendor master, the "
                "purchase orders and the receiving reports will all be absent"
            )

        for case in _seed_invoices():
            if case.refresh_profiles and service.anomaly is not None:
                service.anomaly.rebuild()
            submitted_at = utcnow() - timedelta(days=case.days_ago, minutes=11)
            record = service.submit(
                case.submitted_by, filename=case.filename, hint=case.hint,
                run_id=case.run_id, submitted_at_iso=submitted_at.isoformat(),
            )
            for decision in case.decisions:
                run = service.engine.get(case.run_id)
                if run is None or run.status != "suspended" or not run.open_task_id:
                    break
                decided = service.tasks.decide(
                    run.open_task_id,
                    verdict=decision["verdict"],
                    decided_by=decision["decided_by"],
                    note=decision.get("note", ""),
                    payload=decision.get("payload") or {},
                )
                service.resume_for_task(decided)
            _backdate_run(service.engine, service.tasks, case.run_id, case.days_ago)
            seeded += 1

        if service.anomaly is not None:
            service.anomaly.rebuild()
    finally:
        service.reader = configured

    log.info(
        "Seeded AP world through the engine",
        extra={"invoices": seeded, "rows": len(service.directory.list())},
    )
    return seeded


def _backdate_run(engine: RunEngine, tasks: TaskService, run_id: str, days_ago: int) -> None:
    """Seed-only: move a run and its Tasks into the past. See reimbursements."""
    from .reimbursements import _backdate

    _backdate(engine, tasks, run_id, days_ago)
