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

from pydantic import BaseModel, Field

from ... import desks, media, modelcall, routing
from ...anomaly import AnomalyService, parse_anomaly_thresholds
from ...auth import Principal, flow_principal
from ...dispatch import Dispatcher, InMemoryEventLog
from ...documents import DOCUMENT_COLLECTION, DocumentService, Region
from ...gateway import ActionGateway, InMemoryLedgerStore
from ...graph import Claim, GraphService, GraphWriter, InMemoryGraphStore, object_id
from ...flows import read_model_rates
from ...matching import MatchLine, MatchResult, match_invoice, parse_match_tolerances
from ...modelcall import GovernedModelClient
from ...models import utcnow
from ...ontology import names as onto
from ...prompts import GovernedPrompt, read_prompt
from . import paths
from ..expense_reimbursement.domain import JournalEvent, _event  # shared journal shape
from ...resolution import ResolutionOutcome, ResolutionThresholds
from ...resolution import read_resolution_thresholds, resolve
from ...runs import ModelUsage, RunContext, RunEngine, RunRecord
from ...task_queue import Task, TaskEvidence, TaskProposal, TaskService
# The hint grammar, imported for one caller: `StubInvoiceReader`, below the
# banner. It lives in its own module so the boundary is greppable — see the
# note there, and the test that holds it.
from .scenarios import InvoiceScenario, fixture_document, written_scenario

log = logging.getLogger("cumulus.payables")

FLOW_ID = "accounts-payable"
# The version fresh environments start live on. An environment that has
# chosen (through the console) keeps its choice across redeploys.
#
# v9 rather than v6, and that is the whole of decision C1: v6 asks a clerk
# twice, checks no arithmetic, computes no field verdicts, and — because
# `is_empty(0.0)` is a question it never asks — could post a $0.00 invoice as
# "matched". v9 is v8's real machinery with one human gate, routed to the
# person who submitted the invoice. v6, v7 and v8 stay shipped and selectable.
DEFAULT_VERSION = 9
POLICY_ASSET_ID = "invoice-posting-policy"
PROMPT_ASSET_ID = "invoice-extraction-prompt"
#: What this flow's one ModelCall is *for*, on its ledger row. The column that
#: makes a bill legible without joining anything: two flows sharing one model
#: differ here and nowhere else.
EXTRACT_PURPOSE = "extract-invoice"
CLERK_QUEUE = desks.AP_CLERK

#: Who posts an invoice that no person reviewed. An invoice under the governed
#: auto-post limit is committed on the Flow's own authority, bound to the same
#: queue a clerk would decide it from — so the Gateway asks the same question
#: either way, and un-binding this stops auto-posting rather than exempting it.
AP_FLOW = flow_principal(FLOW_ID, CLERK_QUEUE)


def ap_flow_principal(*queues: str) -> Principal:
    """`AP_FLOW`, additionally bound to the desks this case was routed to.

    The same shape `expense_flow_principal` takes, for the same reason. v9
    routes every review to the desk the governed approval table names, which
    for a submitted invoice is that person's own — a different queue for every
    case, so a module-level constant cannot name it, and `post-invoice` would
    refuse the Flow's own commit on a run resumed with no live principal (a
    seed, an eval case, an operator's `POST /runs/{id}/resume`).

    Still declared and still bounded: the standing AP desk, plus the one desk
    this run's Task was actually raised to, and nothing else. Un-bind it and
    those resumes stop rather than being waved through.
    """
    return flow_principal(FLOW_ID, CLERK_QUEUE, *[q for q in queues if q])


PayableState = Literal["posted", "pending", "denied"]


def submitter_email(submitted_by: str) -> str:
    """The person who handed this invoice over, or "" when nobody did.

    `submitted_by` is one field with two kinds of value in it, and the routing
    table needs them told apart. Both human doors — the console upload and the
    REST side door — put the authenticated principal's own address here, which
    is the address a review routes back to. `ap-intake` puts `flow:ap-intake`,
    because a file fetched out of a folder was handed over by nobody, and the
    governed table's fallback desk is the honest answer for it.

    A prefix check rather than a lookup: `flow:`/`system:` actors are how this
    system spells "not a person" everywhere, and asking the org chart would
    make a missing `Employee` row look like a machine-filed invoice.
    """
    text = (submitted_by or "").strip()
    if not text or ":" in text.split("@")[0]:
        return ""
    return text if "@" in text else ""


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
    #: How sure the extractor was **per field**, keyed by canonical fact path
    #: (`paths.py`). The whole-document `confidence` above answers "how did
    #: this page read", which is the question the posting rule asks; it cannot
    #: answer "which three numbers should a person check", which is the
    #: question a review screen asks. A path absent here means the extractor
    #: said nothing about that field, and 0.0 means the same — silence, never
    #: a claim that it was unsure.
    field_confidence: dict[str, float] = Field(default_factory=dict)
    model: str = ""
    # What the call consumed, reported by the reader that made it and priced
    # by the flow that declared the step. Zero means *unmeasured*, never free —
    # see the same pair on `ReceiptExtraction`.
    input_tokens: int = 0
    output_tokens: int = 0
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
freight, tax or discount).

Also return field_confidence: an object whose keys are the field names above
and whose values are your confidence 0-1 in that one field, and on each line a
field_confidence object over that line's own keys. Judge each field on its own
— how clearly that value is printed and how sure you are you read it correctly
— rather than repeating one number for the whole page. Omit a field you did not
find rather than reporting low confidence in a guess.

Respond with only a JSON object with those keys."""


class InvoiceReader(Protocol):
    def read(
        self,
        image: bytes | None,
        filename: str,
        hint: str,
        *,
        prompt: str = "",
        media_type: str = "",
    ) -> InvoiceExtraction:
        """`prompt` is the governed instruction the flow read for this run.

        Passed in rather than held by the reader for the reason given on
        `reimbursements.ReceiptReader`: the instruction is knowledge and the
        reader is machinery. `media_type` travels for the reason given there
        too — a reader that re-derives the type from the filename is a second
        copy of a decision the document store already made correctly.
        """
        ...


def _slug_to_name(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.split("-"))


def _name_to_slug(name: str) -> str:
    cleaned = re.sub(
        r"\b(inc|llc|ltd|co|corp|corporation|gmbh|plc)\b", " ", name.lower()
    )
    return re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")


# ---------------------------------------------------------------------------
# STUB FIXTURES — invented, and read by `StubInvoiceReader` and nothing else
# ---------------------------------------------------------------------------
#
# Four vendor names and six SKUs that no customer ever had. They used to be
# module-level vocabulary shared with the console's composer, which meant a
# deployed screen offered invented parts beside the ERP's real catalogue and a
# person picking one could not tell which was which. The vendor master and the
# item catalogue are in the graph, landed by `vendor-sync` and `sales-sync`;
# anything on a production path reads them from there.
#
# What is left is the stub extractor's own vocabulary, quarantined under a
# `_STUB_` prefix so a reviewer can grep for it. It exists so an offline run
# with `MODEL_BACKEND=stub` produces an invoice whose lines a seeded purchase
# order can actually match — the same SKUs, descriptions and unit prices on
# both sides, so a local three-way match is a real match and not a
# coincidence. A deployment with a real extractor never reaches this block.

_STUB_VENDORS: tuple[str, ...] = (
    "Acme Industrial Inc.",
    "Northwind Traders LLC",
    "Bay State Fasteners Co.",
    "Cascade Welding Supply",
)

_STUB_CATALOG: tuple[tuple[str, str, float], ...] = (
    ("STL-0250", "Hot-rolled steel plate, 1/4 in", 486.00),
    ("STL-0375", "Hot-rolled steel plate, 3/8 in", 712.50),
    ("FAS-M12", "Grade 8 hex bolt, M12 x 60mm, box of 100", 128.75),
    ("WLD-6013", "Welding rod E6013, 5kg", 92.40),
    ("PPE-GLV", "Cut-resistant gloves, size L, 12 pair", 64.20),
    ("CNS-HYD", "Hydraulic fluid ISO 46, 20L drum", 214.90),
)
_STUB_FREIGHT = ("FRT-STD", "Freight and handling", 0.0)


class StubInvoiceReader:
    """Deterministic local-dev extractor: no credential, no network.

    Steerable by an `InvoiceScenario` — which is what a hint string parses to,
    and where the grammar and the branch each token reaches are written down.
    What the scenario leaves unsaid this fills in from a hash of the input, so
    the same submission always reads the same way.

    Three places a case can be written, and only one of them is reachable from
    a request. The **filename**, which is what a stub extractor has always read
    when nobody passed a hint; the **hint**, which v6 hands over and no route
    carries; and the **page itself**, which is where a composed fixture writes
    its case (`scenarios.fixture_document`). The third is what keeps the seeded
    world and the eval cases running under v9, whose body passes no hint at
    all: the artifact exists, its bytes are stored, and the stub reads the case
    off the page the way a real extractor reads an invoice off one.
    """

    name = "stub"

    # -- m1 ---------------------------------------------------------------
    def read(
        self,
        image: bytes | None,
        filename: str,
        hint: str,
        *,
        prompt: str = "",
        media_type: str = "",
    ) -> InvoiceExtraction:
        written = written_scenario(image)
        steer = hint or written
        scenario = InvoiceScenario.parse(f"{filename} {steer}")
        # A fixture page is *the hint*, so it is hashed as one rather than as
        # bytes: the same composed case has to read the same way whichever
        # version ran it, and hashing the wrapper too would give v9 a different
        # freight figure and a different invoice date than v6 for one case.
        base = hashlib.sha256(
            (b"" if written else (image or b""))
            + filename.encode() + steer.encode()
        ).digest()

        if scenario.new_vendor:
            slug = f"{scenario.new_vendor}-ltd"
        elif scenario.vendor_slug:
            slug = scenario.vendor_slug
        elif scenario.unnamed_new_vendor:
            slug = f"meridian-supply-{base[:2].hex()}"
        else:
            slug = _name_to_slug(_STUB_VENDORS[base[3] % len(_STUB_VENDORS)])
        vendor_name = _slug_to_name(slug)

        po_number = "" if scenario.no_po else (
            scenario.po_number or f"PO-{7000 + base[6] % 900}"
        )
        invoice_number = scenario.invoice_number or (
            f"{slug[:4].upper()}-INV-"
            f"{20000 + int.from_bytes(base[7:9], 'big') % 9000}"
        )
        issued = date.today() - timedelta(days=base[10] % 21)

        # Lines from the stub catalogue, so a seeded PO can actually match.
        # A pinned SKU the catalogue does not hold is dropped rather than
        # invented: there would be no PO line for it to pair against.
        by_sku = {
            sku: (sku, description, price)
            for sku, description, price in _STUB_CATALOG
        }
        picks: list[tuple[tuple[str, str, float], float]] = [
            (by_sku[line.sku], line.quantity)
            for line in scenario.lines if line.sku in by_sku
        ]
        if not picks:
            picks = [
                (
                    _STUB_CATALOG[(base[12 + i] + i) % len(_STUB_CATALOG)],
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

        if scenario.bad_line_math and lines:
            # The amount stops agreeing with quantity × unit price, and nothing
            # else moves: the subtotal below is summed from the amounts, so the
            # document still adds up and the *only* thing wrong with this
            # invoice is one line. That is the case worth authoring — an
            # invoice that is wrong everywhere teaches nothing about a check
            # whose whole claim is that it names the field.
            lines[0].amount_usd = round(lines[0].amount_usd + 18.00, 2)

        subtotal = round(sum(line.amount_usd for line in lines), 2)
        freight = round(48 + base[20] % 90, 2)
        if freight > 0:
            lines.append(InvoiceLineExtract(
                sku=_STUB_FREIGHT[0], description=_STUB_FREIGHT[1], quantity=1.0,
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
            field_confidence=_stub_field_confidence(
                lines, confidence, smudged_total=scenario.smudged_total
            ),
            model="stub-extractor",
            lines=lines,
            regions=_invoice_regions(lines, total, invoice_number),
        )


def _stub_field_confidence(
    lines: list[InvoiceLineExtract],
    document_confidence: float,
    *,
    smudged_total: bool,
) -> dict[str, float]:
    """Per-field scores for the stub, so the verdict path runs offline.

    Every field carries the document's own score, because that is what a
    uniformly legible page looks like and the stub reads no page at all. The
    interesting shapes are authored: `blurry` drags the whole document down and
    `smudged` leaves it high while one number — the total, the one a clerk
    would most want to check — reads badly on its own.
    """
    scores = {
        paths.invoice_path(field): document_confidence
        for field in paths.INVOICE_FIELDS
    }
    for index in range(len(lines)):
        for field in paths.LINE_FIELDS:
            scores[paths.line_path(index, field)] = document_confidence
    if smudged_total:
        scores[paths.invoice_path("total_usd")] = 0.40
    return scores


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
    """Multimodal extraction on Vertex AI, through the governed model client.

    One prompt, one call: the vendor as printed plus the commercial content.
    That prompt arrives from the flow, which read it out of the corpus — so the
    instruction that forbids guessing an identifier is a governed value a
    customer can see and revise, not a literal buried here.

    It no longer holds a `genai.Client` of its own. Four readers each building
    one was four chances for a ModelCall to go unmetered, and the embedder had
    already taken it; the shared client is what makes "every model invocation
    is on the ledger" a property of the system rather than of four call sites
    remembering.
    """

    name = "vertex"

    def __init__(self, model: str, client: GovernedModelClient) -> None:
        self._client = client
        self._model = model

    def _parts(
        self, image: bytes | None, filename: str, prompt: str, media_type: str = ""
    ):
        from google.genai import types

        parts: list = []
        if image:
            # This branch used to guess from the extension and got PDFs right
            # by having remembered to add a case; its sibling in
            # `reimbursements` had not, which is the whole argument for the
            # type being carried rather than re-derived per reader.
            mime = media.require_model_readable(
                media_type or media.sniff(image, filename), filename
            )
            parts.append(types.Part.from_bytes(data=image, mime_type=mime))
        parts.append(types.Part(text=prompt))
        return parts

    def _generate(self, parts, temperature: float) -> tuple[dict, ModelUsage]:
        """The parsed JSON *and* what the call consumed.

        The token counts arrive on the same response as the answer and used to
        be dropped on the floor here, which is why every AP run's ModelCall
        step reported a cost of zero. They come back priced now, because the
        client that made the call has just read the governed rate sheet to
        price its own ledger row and two answers to one question is one too
        many — the journal and the ledger agree by construction.
        """
        from google.genai import types

        response, usage = self._client.generate(
            model=self._model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=temperature
            ),
            purpose=EXTRACT_PURPOSE,
        )
        return json.loads(response.text or "{}"), usage

    def read(
        self,
        image: bytes | None,
        filename: str,
        hint: str,
        *,
        prompt: str = "",
        media_type: str = "",
    ) -> InvoiceExtraction:
        text = (prompt or DEFAULT_INVOICE_PROMPT) + (
            f"\n\nContext: {hint}" if hint else ""
        )
        data, usage = self._generate(
            self._parts(image, filename, text, media_type), 0.0
        )
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
            field_confidence=_field_confidence(data),
            model=self._model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            lines=lines,
        )


def _field_confidence(data: dict) -> dict[str, float]:
    """A model's per-field scores, re-keyed to canonical fact paths.

    The model answers in its own vocabulary — the same key names it just used
    for the values, and a `field_confidence` object per line — because asking
    it to emit `line_items[2].amount_usd` would be asking it to do bracket
    arithmetic in the middle of reading a page, which is a reliable way to get
    the index wrong. The translation into paths happens here instead, where the
    line's position is known for certain.

    Out-of-range and unparseable scores are dropped rather than clamped. A
    confidence of 4 is not "very confident"; it is a field this model did not
    answer the question about, and 0.0-means-silence is the honest reading.
    """
    out: dict[str, float] = {}

    def record(path: str, raw: object) -> None:
        try:
            value = float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        if 0.0 <= value <= 1.0:
            out[path] = value

    for field, raw in (data.get("field_confidence") or {}).items():
        if field in paths.INVOICE_FIELDS:
            record(paths.invoice_path(str(field)), raw)
    for index, item in enumerate(data.get("lines") or []):
        if not isinstance(item, dict):
            continue
        scores = item.get("field_confidence")
        if isinstance(scores, dict):
            for field, raw in scores.items():
                if field in paths.LINE_FIELDS:
                    record(paths.line_path(index, str(field)), raw)
            continue
        # A single score for the whole line is the shape a model falls back to
        # when the per-field object is more structure than it wants to emit.
        # Spread across the line's fields rather than dropped: "this line was
        # hard to read" is a claim about every number on it.
        if "confidence" in item:
            for field in paths.LINE_FIELDS:
                record(paths.line_path(index, field), item.get("confidence"))
    return out


def build_invoice_reader(
    backend: str,
    project: str | None,
    location: str,
    model: str,
    *,
    client: GovernedModelClient | None = None,
) -> InvoiceReader:
    """`client` is the deployment's governed client; None builds a private one.

    A reader constructed without one is a test or a script, and it still meters
    and still ledgers — into a store nobody serves. The alternative, a reader
    that calls a model with no record anywhere, is the thing the ledger exists
    to make impossible.

    The last line used to be a bare `return StubInvoiceReader()`, which meant
    every backend name that was not exactly `vertex` — a typo, an unset
    variable, a value from a config nobody reviewed — produced a reader that
    fabricates an extraction from a hash of the filename. `require_stub_opt_in`
    is the difference between choosing that and falling into it.
    """
    if backend == "vertex":
        return VertexInvoiceReader(
            model, client or modelcall.vertex_client(project, location)
        )
    modelcall.require_stub_opt_in(backend, "invoice reader")
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


def _declared_step_names(version: int) -> set[str]:
    """The step names one shipped version declares, or empty for an unshipped one."""
    from .. import shipped_versions

    module = shipped_versions(FLOW_ID).get(version)
    return {step.name for step in getattr(module, "STEPS", [])} if module else set()


def read_policy_clauses(knowledge, version: int | None = None) -> list[PolicyClause]:
    """Every governed value the accounts-payable flow reads, at its revision.

    Two documents, because the flow reads two: the posting policy governs the
    limit, the match and the screen; the entity-resolution policy governs
    whether a printed name becomes a link or a new vendor.

    Only what a run actually consults is listed — and "the flow" means the
    version that runs, so the list is read off that version's declared steps:
    a version without the anomaly screen does not list outlier thresholds it
    never consults, and one that resolves review routing lists whose desk a
    review lands on. A screen that showed the whole document would be a
    document viewer — the Library is that — and one that showed a curated
    subset would drift from what the flow reads.
    """
    from .. import default_version

    names = _declared_step_names(
        version if version is not None else default_version(FLOW_ID)
    )
    # An unshipped or unknown version answers with everything: overstating what
    # an odd world consults beats hiding a clause a run is about to cite.
    show_all = not names
    asset = knowledge.repo.get(POLICY_ASSET_ID) if knowledge else None
    content = asset.content if asset is not None else ""
    ref = _knowledge_ref(asset) if asset is not None else f"knowledge:{POLICY_ASSET_ID}"
    posting = parse_posting_policy(content)
    match = parse_match_tolerances(content, ref)
    screen = parse_anomaly_thresholds(content, ref)
    resolution = read_resolution_thresholds(knowledge)
    # The field-verdict values live in the version package that reads them, and
    # are imported here rather than re-parsed: this list exists to show a person
    # what a run consults, and a second parser would eventually show them a
    # number no run had ever used.
    from .v8.arithmetic import parse_arithmetic_tolerances

    field = parse_arithmetic_tolerances(content, ref)
    clauses = [
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
            id="min-field-confidence", label="Minimum field confidence",
            value=f"{field.min_field_confidence:.2f}", ref=ref,
            cites="below the governed floor",
        ),
        PolicyClause(
            id="line-arithmetic", label="Line arithmetic tolerance",
            value=f"${field.line_amount_usd:,.2f}", ref=ref,
            cites="but this line reads",
        ),
        PolicyClause(
            id="document-arithmetic", label="Document arithmetic tolerance",
            value=f"${field.document_usd:,.2f}", ref=ref,
            cites="but the invoice total reads",
        ),
        PolicyClause(
            id="resolution-auto-link", label="Vendor auto-link at or above",
            value=f"{resolution.auto_link_at:.2f}", ref=resolution.source_ref,
        ),
    ]
    if show_all or "anomaly-screen" in names:
        clauses.extend([
            PolicyClause(
                id="amount-outlier", label="Amount outlier multiple",
                value=f"{screen.outlier_multiple:.1f}× the vendor's median",
                ref=ref, cites="anomaly (amount-outlier)",
            ),
            PolicyClause(
                id="duplicate-window", label="Duplicate window",
                value=f"{screen.duplicate_window_days} days", ref=ref,
                cites="anomaly (duplicate-suspect)",
            ),
        ])
    if show_all or "read-knowledge" in names:
        table = routing.parse_routing(content, default_fallback=CLERK_QUEUE)
        landing = table.rules[0].target if table.rules else table.fallback
        suffix = (
            f" (fallback {table.fallback})" if table.rules and table.fallback else ""
        )
        clauses.append(
            PolicyClause(
                id="review-routing", label="Reviews land on",
                value=f"{landing}{suffix}", ref=ref,
            )
        )
    return clauses


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
        locator=None,  # v8.locate.FieldLocator — duck-typed, see below
        queues=None,  # QueueService — duck-typed, for the same reason
    ) -> None:
        self.directory = directory
        self.reader = reader
        # The desk registry, so a review can be routed to the person who
        # submitted the invoice. `None` is a service wired without one (a unit
        # test), and then a `submitter` target falls back to the governed
        # fallback desk with `fell_back` set rather than raising: a routing
        # question the policy already answers must not fail a payment.
        self.queues = queues
        # The second reader, and it is deliberately typed as nothing. A
        # `FieldLocator` lives in the v8 package because locating disputed
        # fields is that version's idea; this service is shared by three
        # shipped versions and importing one version's module here would make
        # the package import itself. A version that has no use for it never
        # asks, and `None` is a deployment that shows no boxes rather than one
        # that shows invented ones.
        self.locator = locator
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
            # One registration per shipped version directory: the engine can
            # then run whichever one the stored definition says is live, and
            # resume old runs under the version that wrote their journals.
            from .. import shipped_versions

            for number, module in sorted(shipped_versions(FLOW_ID).items()):
                # `metered` is what puts this run's coordinates in scope for the
                # ModelCall the body makes: the extractor is called from a
                # closure that knows nothing about runs, and a ledger row with
                # no run and no step is a cost nobody can attribute to a case.
                engine.register(
                    FLOW_ID, modelcall.metered(module.build_body(self)),
                    version=number, default=(number == DEFAULT_VERSION),
                )

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

    def _resolve_submitter_desk(
        self, submitted_by: str, fallback: str
    ) -> routing.ResolvedQueue:
        """The submitter's own desk, answered against the org chart and the
        registry — or the fallback, saying why it fell back.

        The one fetching half of routing, and it runs inside a transform step
        for the reason `routing`'s own docstring gives: a Rule never fetches.
        What comes back out is a concrete queue and a sentence, and the band
        the amount falls in is decided afterwards from values already in hand.

        Deterministic, so a resumed run picks the same desk and mints no second
        inbox: `ensure_personal` keys the slug off the address.
        """
        return routing.resolve(
            routing.SUBMITTER,
            submitter_email=submitter_email(submitted_by),
            graph=self.graph,
            queues=self.queues,
            fallback=fallback,
        )

    # -- entry point --------------------------------------------------------

    def submit(
        self,
        submitted_by: str,
        image_b64: str = "",
        filename: str = "",
        hint: str = "",
        *,
        document_id: str = "",
        run_id: str | None = None,
        submitted_at_iso: str = "",
    ) -> InvoiceRecord:
        """Submit one invoice. `hint` is authoring apparatus, not a request field.

        No route passes it: both doors (`payables_api` and the console) carry
        bytes and a filename and nothing else, because a hint is a way to get
        an extraction without a document. What still passes one is the seeded
        world and the eval cases — see `scenarios.py` — where composing a case
        is the whole point and the extractor is explicitly the stub.

        `document_id` names an artifact the store already holds, which is how
        v8 is driven. `ap-intake` does not come through here — it emits the
        fact itself, per invoice, because a windowed flow raising forty cases
        has no single row to return — but the two put the same key on the same
        event, so a run is the same run whichever door opened it.

        A hint with no artifact behind it is turned into one. From v9 the flow
        refuses a run with no document, and that rule holds for fixtures too —
        so the composed case becomes a page with the case written on it
        (`scenarios.fixture_document`) and the run reads stored bytes like
        every other. Which is the honest arrangement: the exemption a fixture
        needs is "you may compose the document", not "you may skip it".
        """
        if self.dispatcher is None:
            raise RuntimeError("Payable service has no run engine wired")
        if hint and not image_b64 and not document_id:
            image_b64 = base64.b64encode(fixture_document(hint)).decode()
        inputs = {
            "submitted_by": submitted_by,
            "image_b64": image_b64,
            "filename": filename,
            "hint": hint,
            "document_id": document_id,
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
        self._emit_correction(task)
        record = self.engine.resume_for_task(task.id, principal=principal)
        return self._project(record) if record else None

    # -- the flywheel -------------------------------------------------------

    def _emit_correction(self, task: Task) -> None:
        """Announce what a clerk changed, and then get on with the invoice.

        Doctrine's flywheel clause is specific about the route: a human
        correction flows back **decoupled, via an Event, not inline**. So this
        emits `ExtractionCorrected` and returns — the Trigger table answers it
        with an `ap-corrections` run that appends the raw→corrected pairs, and
        the invoice below carries on posting whether or not that lands. The
        alternative, appending here, would put a knowledge write in the middle
        of a payment's critical path and give a correction-set outage the power
        to stop a payment.

        Emitted before the resume, deliberately: the resume applies the patch,
        and after it the extraction the clerk was looking at is gone. What is
        wanted is the pair — what the model read *and* what the human said —
        and only one of those two is still on the record afterwards.
        """
        decision = task.decision
        if (
            decision is None
            or decision.verdict != "corrected"
            or self.dispatcher is None
            or self.engine is None
        ):
            return
        record = self.engine.get(task.run_id)
        if record is None or record.status != "suspended":
            # The decision has already been carried into the run. A second call
            # here — the API hook and a seeding script both reaching for the
            # same task — must not append the same pair twice, and "is this run
            # still waiting" is the fact that distinguishes them.
            return
        extraction = _extraction_of(record)
        pairs = _correction_pairs(extraction, decision.payload, task)
        if not pairs:
            return
        self.dispatcher.emit(
            "ExtractionCorrected",
            {
                "pairs": pairs,
                "document_id": str(
                    _step_output(record, "s1").get("document_id", "")
                ),
                "invoice_number": extraction.invoice_number,
                "vendor_id": str(_step_output(record, "z1").get("vendor_id", "")),
                "flow_id": record.flow_id,
                "flow_version": record.flow_version,
                "run_id": record.id,
                "case_ref": record.case_ref or record.id,
                "task_id": task.id,
                "decided_by": decision.decided_by,
            },
            actor=decision.decided_by,
            case_ref=record.case_ref or record.id,
        )

    def apply_task_decision(
        self, task: Task, principal: Principal | None = None
    ) -> InvoiceRecord | None:
        return self.resume_for_task(task, principal)

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

    def _resolve_document(
        self,
        document_id: str,
        image_b64: str,
        filename: str,
        submitted_by: str,
        run_id: str,
    ) -> dict:
        """The artifact this run reads, however it got here. v8's s1.

        Two arrivals and one outcome. `ap-intake` has already fetched the
        vendor's file out of the customer's document system and stored it, so
        it hands over an **id**; a person with an invoice in their hand hands
        over **bytes**, and the store gives them the same kind of id. Either
        way what the extraction reads afterwards is the stored artifact, which
        is what makes "the model read this document" a checkable claim rather
        than a description of an input nobody kept.

        A run with neither is refused. It used to be possible — a filename and
        a hint and no file — and it is precisely the shape of submission that
        produced an extraction with nothing behind it.
        """
        if self.documents is None:
            raise RuntimeError("no document store wired")
        if document_id:
            record = self.documents.get(document_id)
            if record is None:
                raise KeyError(
                    f"{document_id} is not in the document store; this run was "
                    "started against an artifact nobody kept"
                )
            if run_id and run_id not in record.run_ids:
                # The store's own idempotency does this when bytes are put; an
                # id handed in skips that path, and a document that cannot name
                # the runs that read it is evidence with no chain.
                record.run_ids.append(run_id)
                self.documents.store.put(record)
            return {
                "document_id": record.id, "byte_size": record.byte_size,
                "arrived": "intake", "filename": record.filename or filename,
            }
        if not image_b64:
            raise ValueError(
                "an accounts-payable run needs a document: no stored id and "
                "no bytes"
            )
        record = self.documents.put_bytes(
            image_b64, filename=filename, kind="invoice",
            uploaded_by=submitted_by, run_id=run_id,
        )
        return {
            "document_id": record.id, "byte_size": record.byte_size,
            "arrived": "upload", "filename": record.filename or filename,
        }

    def _document_bytes(self, document_id: str) -> bytes | None:
        """The stored artifact, read back for the ModelCall to look at.

        Read from the store rather than carried down from the inputs: the
        stored copy is the one the region boxes, the review screen and an
        auditor two years from now will resolve, and an extraction made from
        some other copy of the bytes would be attributed to it anyway.
        """
        if self.documents is None:
            return None
        record = self.documents.get(document_id)
        if record is None or not record.content_b64:
            return None
        return base64.b64decode(record.content_b64)

    def _extract(
        self,
        image: bytes | None,
        filename: str,
        hint: str,
        document_id: str,
        prompt: str,
        *,
        store_regions: bool = True,
    ) -> InvoiceExtraction:
        # The prompt revision the ledger files this call against, taken off the
        # read-set the metered step declared rather than by reading the asset
        # again — the flow read it at x0, and a second read here could name a
        # different revision exactly in the run where that matters.
        with modelcall.reading(modelcall.governed_ref(PROMPT_ASSET_ID)):
            extraction = self.reader.read(
                image, filename, hint, prompt=prompt,
                media_type=media.sniff(image, filename),
            )
        # `store_regions=False` is v8's, and it is the one behaviour v8
        # deliberately drops rather than inherits. The only regions any reader
        # here produces are the stub's synthetic geometry — the Vertex readers
        # emit none — so persisting them would put invented boxes on a real
        # document and draw them on a reviewer's screen as if a model had
        # located something. v8's boxes come from WP-J's locate step, or the
        # document carries none and the screen says so.
        if store_regions and self.documents is not None and extraction.regions:
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
            self.graph, onto.VENDOR, [name] if name else [], thresholds,
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
                onto.VENDOR, name,
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
                    onto.VENDOR, vendor.natural_key,
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
            onto.VENDOR, name,
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
            order = self.graph.get(object_id(onto.PURCHASE_ORDER, po_number))
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
        fields: list[dict] | None = None,
        *,
        gate_id: str = "k2",
        queue: str = CLERK_QUEUE,
        routing_note: str = "",
        vendor: dict | None = None,
    ) -> Task:
        """`fields` is the version's field verdicts, carried and not read.

        Serialized `FieldVerdict`s, which this module deliberately does not
        import: the verdicts are v8's and this service is shared by four
        shipped versions. `None` travels through as `None` rather than being
        flattened to an empty list — a version that computes no verdicts and a
        version that computed them and found nothing wrong are opposite facts,
        and the review screen renders them differently on purpose.

        The four keyword arguments are v9's, and every one of them defaults to
        what v6, v7 and v8 already did:

        - `gate_id` names the step, because v9's single gate is `k1` where the
          older versions' posting gate is `k2` — a Task has to be findable
          from the step that raised it;
        - `queue` is the desk the governed approval table resolved for *this
          case*, which for a submitted invoice is the submitter's own;
        - `routing_note` is that resolution's sentence, so the desk on the row
          is a claim somebody can check;
        - `vendor` is the entity question folded into this review rather than
          asked at a gate of its own.
        """
        findings = screen.get("findings") or []
        subject = submitter_email(submitted_by)
        return Task(
            id=f"task_{record.id}_{gate_id}_{round_number}",
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
                + (
                    " The vendor is unsettled too: name it here and nothing "
                    "is asserted about the entity until you do."
                    if vendor else ""
                )
            ),
            queue=queue,
            subject_email=subject or None,
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
            ] + (
                [TaskEvidence(label="Routed to this desk because",
                              value=routing_note)]
                if routing_note else []
            ) + (
                [TaskEvidence(
                    label="Vendor as printed",
                    value=(
                        f"{vendor.get('printed', '')} — "
                        f"{len(vendor.get('candidates') or [])} candidate(s) "
                        "in the master, none confident enough to assert"
                    ),
                )] if vendor else []
            ) + [
                TaskEvidence(label=f"Anomaly: {f['kind']}", value=f["detail"])
                for f in findings[:4]
            ] + [
                TaskEvidence(label=f"Line {p.invoice_ordinal} ({p.verdict})",
                             value=p.reason)
                for p in match.pairings if not p.clean
            ][:4] + [
                # The same doubts the review screen focuses, spelled out for
                # the generic task pane — which is what a clerk sees on a
                # deployment whose console has not shipped the v8 screen yet.
                TaskEvidence(
                    label=f"{field.get('label', field.get('path', ''))} "
                          f"({field.get('status', '')})",
                    value=str(field.get("reason", "")),
                )
                for field in (fields or [])
                if field.get("status") not in ("ok", None)
            ][:4],
            proposal=TaskProposal(
                action="post-invoice", target="ap-records",
                summary=(
                    f"Post {extraction.invoice_number} for "
                    f"${extraction.total_usd:,.2f} to the AP ledger"
                ),
                fields=None if fields is None else list(fields),
                vendor=vendor,
            ),
            resumes=(
                f"this run resumes at {gate_id}_{round_number} and re-runs "
                "the rule"
            ),
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
        queue: str = "",
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
        #
        # `queue` is the desk of the Task that authorized this commit, and it
        # is passed only when there was one. Omitted, `post-invoice`'s standing
        # `ap-clerk` pin stands — which is the right desk for a write nobody
        # decided. Passed, the queue that raised the work and the queue that
        # commits it are the same queue, so a submitter answering a review on
        # their own desk is not refused their own answer.
        post = self.gateway.action(
            "post-invoice", ctx.principal or ap_flow_principal(queue),
            run=ctx.record, step_id="a1",
            task_id=record.task_id, decided_by=decided_by,
            queue=queue,
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
            onto.INVOICE, natural,
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
                    onto.VENDOR, vendor.natural_key,
                    [Claim(field="status", value="active",
                           source=f"human:{record.decided_by}")],
                    label=vendor.label,
                    provenance=f"vouched for by posting {record.invoice_number}",
                )
        if record.document_id:
            document = graph.upsert(
                onto.DOCUMENT, record.document_id,
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
                onto.INVOICE_LINE, f"{natural}#{ordinal}",
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
                onto.MATCH_SET, natural,
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
                order = self.graph.get(object_id(onto.PURCHASE_ORDER, number))
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

        Which step holds which fact is a **property of the version that ran**,
        so it is read off `flow_version` rather than guessed at. v9 renumbered
        the map — its `c2` is the three-way match where v6's `c2` is the
        anomaly screen — and a projection that tried both orders would read one
        version's screen as the other's match set and report a verdict no run
        ever produced.
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

        extraction = _extraction_of(record)
        journal = _journal_map(record.flow_version)

        match: MatchResult | None = None
        for step_id in journal["match"]:
            payload = output(step_id)
            if payload:
                try:
                    match = MatchResult.model_validate(payload)
                    break
                except Exception:  # noqa: BLE001
                    continue
        # v9 runs no anomaly screen: the capability stays selectable on v8, and
        # `{}` here is what "not evaluated" looks like downstream rather than a
        # screen that found nothing.
        screen = (output(journal["screen"], {}) or {}) if journal["screen"] else {}
        policy = PostingPolicy.model_validate(
            _nested(output(journal["policy"], {}) or {}, journal["policy_key"])
        )

        reasons: list[str] = []
        for step_id in journal["rule"]:
            checked = output(step_id, {})
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


def _step_output(record: RunRecord, step_id: str) -> dict:
    step = record.step(step_id)
    output = step.output if step is not None else None
    return output if isinstance(output, dict) else {}


def _nested(payload: dict, key: str) -> dict:
    """One key of a step output, or the output itself when no key is named.

    v9's `x0` is a single governed read whose output holds four things; the
    older versions read the posting policy into a step of its own. One
    accessor, so the projection reads "the policy this run ran on" rather than
    "the shape the version happened to store it in".
    """
    if not key:
        return payload
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def _journal_map(flow_version: int) -> dict:
    """Where each fact sits in the journal, for the version that wrote it.

    Read by the projection and by `_extraction_of`, which are the two things
    that reconstruct a case from steps rather than from a return value. Written
    as data rather than as branches at four call sites: the alternative was a
    projection that knew v9's numbering in one place and v6's in another.
    """
    if flow_version >= 9:
        return {
            "match": [f"c2_{n}" for n in range(MAX_REVIEW_ROUNDS, 0, -1)] + ["c2"],
            "screen": "",
            "policy": "x0",
            "policy_key": "posting",
            "rule": [f"r1_{n}" for n in range(MAX_REVIEW_ROUNDS, 0, -1)],
            "correction": [f"x3_{n}" for n in range(MAX_REVIEW_ROUNDS, 0, -1)],
        }
    return {
        "match": [f"c1_{n}" for n in range(MAX_REVIEW_ROUNDS, 0, -1)] + ["c1"],
        "screen": "c2",
        "policy": "x5",
        "policy_key": "",
        "rule": [f"r2_{n}" for n in range(MAX_REVIEW_ROUNDS, 0, -1)],
        "correction": [f"x6_{n}" for n in range(MAX_REVIEW_ROUNDS, 0, -1)],
    }


def _extraction_of(record: RunRecord) -> InvoiceExtraction:
    """The reading of the document this run is currently working from.

    The last correction wins, then the extraction itself — newest reading of
    the page first. Read off the journal rather than carried, because the two
    callers are in different processes' worth of time apart: the projection
    runs while the run is suspended, and the flywheel runs at the moment
    somebody answers.
    """
    sources = _journal_map(record.flow_version)["correction"] + ["m1"]
    for step_id in sources:
        payload: object = _step_output(record, step_id)
        if not payload:
            continue
        if isinstance(payload, dict) and "extraction" in payload:
            payload = payload["extraction"]
        try:
            return InvoiceExtraction.model_validate(payload)
        except Exception:  # noqa: BLE001 — a partial run is not an error
            continue
    return InvoiceExtraction()


def _corrected_by_path(payload: dict) -> dict[str, str]:
    """A clerk's correction, whatever shape it arrived in, keyed by fact path.

    Two shapes reach this. The review screen sends `fields`, a map from the
    canonical path to the value the clerk left — which is the contract, and the
    only shape that can name a field the v6-era form has no input for. The
    older shape is the patch itself (`total_usd`, `lines[{ordinal, …}]`), which
    the eval cases and the seeded world are written in and which stays valid.

    Read for the *pair*, not for the patch: what comes back is what the human
    said about a field, not what the applier will make of it. A subtotal the
    applier is going to re-derive from the lines still belongs in the
    correction set, because "the model misread the subtotal" is true whether or
    not that particular number survives the round trip.
    """
    out: dict[str, str] = {}

    def put(path: str, value: object) -> None:
        # An emptied field is not read as a correction, here or in the applier
        # below — the same rule both shapes have always had. "Delete what the
        # model read" is a real thing a clerk might mean and a blank input is
        # not evidence they meant it.
        if value is None:
            return
        text = paths.render(path, value) if isinstance(value, (int, float)) else str(value)
        if text.strip():
            out[path] = text.strip()

    for raw_path, value in (payload.get("fields") or {}).items():
        # Three spellings reach the wire — `Region.field` carries the
        # `invoice.` prefix on line paths and §4's examples do not — so they
        # are normalized once, here, rather than compared three ways later.
        path = str(raw_path)
        if path.startswith("invoice.line_items["):
            path = path[len("invoice."):]
        elif path.startswith("lines["):
            path = "line_items" + path[len("lines"):]
        index, field = paths.parse_path(path)
        if not field:
            continue
        put(path, value)

    for field in paths.INVOICE_FIELDS:
        if payload.get(field) is not None:
            put(paths.invoice_path(field), payload[field])
    for patch in payload.get("lines") or []:
        ordinal = int(patch.get("ordinal", 0) or 0)
        if ordinal < 1:
            continue
        for field in paths.LINE_FIELDS:
            if patch.get(field) is not None:
                put(paths.line_path(ordinal - 1, field), patch[field])
    return out


def _same_value(raw: str, corrected: str) -> bool:
    """Whether these two are the same answer, punctuation aside.

    Numerically when both sides are numbers, because `1200` and `1200.00` are
    one value and a correction set full of reformattings is a correction set
    nobody can learn from.
    """
    if raw.strip() == corrected.strip():
        return True
    try:
        return abs(
            float(raw.replace(",", "").replace("$", ""))
            - float(corrected.replace(",", "").replace("$", ""))
        ) < 0.005
    except ValueError:
        return False


def _correction_pairs(
    extraction: InvoiceExtraction, payload: dict, task: Task
) -> list[dict]:
    """The raw→corrected pairs one decision produced, in path order.

    The raw side comes off the extraction the clerk was shown rather than off
    the Task, so a pair is a claim about what the model read even when the
    field was never flagged — a clerk who fixes a number nobody doubted is the
    most interesting row in the set, because it is a miss the confidence score
    did not catch.
    """
    verdicts = {
        str(field.get("path", "")): field
        for field in ((task.proposal.fields if task.proposal else None) or [])
        if isinstance(field, dict)
    }
    pairs: list[dict] = []
    for path, corrected in _corrected_by_path(payload).items():
        raw = paths.render(path, paths.value_at(extraction, path))
        if _same_value(raw, corrected):
            continue
        verdict = verdicts.get(path, {})
        pairs.append({
            "path": path,
            "label": paths.label_for(path),
            "raw": raw,
            "corrected": corrected,
            "status": str(verdict.get("status", "") or ""),
            "confidence": float(verdict.get("confidence", 0) or 0),
        })
    return pairs


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
    *,
    check_line_sum: bool = True,
) -> dict:
    """The Rule: a pure check over what the flow gathered. It never fetches.

    `check_line_sum=False` is v8's, and it removes a duplicate rather than a
    check. The coarse "lines do not add up to the total" clause at the bottom
    of this function is the only arithmetic v6 has; v8 runs `check-arithmetic`
    before the vendor is even resolved, which asks the same question line by
    line and *names the field path* that fails. Leaving both on would put two
    sentences about one disagreement in front of the same clerk, and the vaguer
    one second.
    """
    reasons: list[str] = []
    # First, and unconditional: an invoice with no total cannot be auto-posted
    # by any limit, because arithmetic says nothing is *above* zero either. A
    # live estate posted a $0.00 invoice as "matched" — the limit comparison
    # passed, the match had nothing to disagree with, and a payment record was
    # written for a page nobody had read a number off. A zero total is what an
    # unread page looks like, so it is a reason to ask rather than a reason to
    # be quick, and the governed policy says so in as many words.
    if extraction.total_usd <= 0:
        reasons.append(
            "the invoice total reads $0.00 — an unread or unreadable total is "
            "never posted without a person, whatever the auto-post limit says"
        )
    elif extraction.total_usd > policy.limit_usd:
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
    if check_line_sum and extraction.lines and abs(line_total - extraction.total_usd) > 0.05:
        reasons.append(
            f"lines sum to ${line_total:,.2f} but the invoice total reads "
            f"${extraction.total_usd:,.2f}"
        )
    return {"reasons": reasons}


_NUMERIC_FIELDS = frozenset({
    "quantity", "unit_price_usd", "amount_usd", "subtotal_usd", "freight_usd",
    "tax_usd", "total_usd",
})


def _fold_field_corrections(payload: dict) -> dict:
    """Path-keyed corrections, rewritten into the patch shape above.

    Numbers are coerced here and a value that will not parse as one is
    *dropped*, not zeroed: `"twelve hundred"` in a total field is a typo, and
    the invoice keeping the number the model read is a better outcome than the
    invoice reading zero because somebody fat-fingered a form.

    A path the applier below has no case for — `vendor_name` is the one that
    matters — is **recorded as a correction and not applied**, and that is the
    right split rather than an omission: which entity issued an invoice is
    settled by the resolver at `z1` and, when it cannot settle it, by the
    `confirm-vendor` gate. Letting a posting review rename the vendor after the
    match has run against that vendor's orders would leave the invoice pointing
    at one company and its match set at another.
    """
    corrections = payload.get("fields")
    if not isinstance(corrections, dict) or not corrections:
        return payload

    folded = {key: value for key, value in payload.items() if key != "fields"}
    by_ordinal: dict[int, dict] = {
        int(patch.get("ordinal", 0) or 0): dict(patch)
        for patch in folded.get("lines") or []
    }
    for path, value in _corrected_by_path({"fields": corrections}).items():
        index, field = paths.parse_path(path)
        typed: object = value
        if field in _NUMERIC_FIELDS:
            try:
                typed = float(str(value).replace(",", "").replace("$", ""))
            except ValueError:
                log.warning(
                    "Dropping an uncorrectable value",
                    extra={"path": path, "value": value},
                )
                continue
        if index < 0:
            folded[field] = typed
        else:
            patch = by_ordinal.setdefault(index + 1, {"ordinal": index + 1})
            patch[field] = typed
    if by_ordinal:
        folded["lines"] = [by_ordinal[key] for key in sorted(by_ordinal)]
    return folded


def _apply_invoice_patch(extraction: InvoiceExtraction, payload: dict) -> dict:
    """Apply a clerk's structured correction to an extraction.

    Payload shape (all keys optional):

        {"invoice_number": "...", "invoice_date": "...", "po_number": "...",
         "total_usd": 1234.56,
         "lines": [{"ordinal": 1, "quantity": 3, "unit_price_usd": 486.00,
                    "amount_usd": 1458.00, "sku": "...", "description": "..."}],
         "remove_ordinals": [4],
         "fields": {"invoice.total_usd": "1234.56",
                    "line_items[0].unit_price_usd": "486.00"}}

    `fields` is the review screen's shape — corrections keyed by canonical fact
    path, which is the same key the verdict that raised the question carried.
    It is folded into the shape above before anything else happens, so there is
    one applier rather than two: a field arriving both ways (the screen sends
    the overlap deliberately, so a console built against v6's form keeps
    working) is one value either way, and the path wins if they ever disagree
    because the path is what the run named.

    Line amounts are recomputed from quantity × unit price when the clerk gives
    both and no explicit amount, because that is the arithmetic they would
    otherwise have to do by hand — and getting it wrong here would fail the
    lines-sum check on the next round for no reason.
    """
    answered = list(_corrected_by_path(payload))
    payload = _fold_field_corrections(payload)
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
    # Derived from the lines *unless the clerk said otherwise*, which is how
    # the total one line up has always behaved. Deriving over an explicit
    # correction would throw away an answer while reporting it as applied —
    # and the field-verdict screen can now put a subtotal in front of somebody
    # to correct, which v6's form could not.
    if payload.get("subtotal_usd") is None:
        patched.subtotal_usd = round(
            sum(line.amount_usd for line in patched.lines if line.kind == "goods"), 2
        )
    if payload.get("freight_usd") is None:
        patched.freight_usd = round(
            sum(line.amount_usd for line in patched.lines if line.kind == "freight"), 2
        )
    # A clerk who has read the document has resolved the model's uncertainty —
    # about the page as a whole, and about each field they actually answered.
    # Without the second half a field the extractor was unsure of stays in
    # doubt after the human has settled it, and the next round asks them the
    # same question about the number they just typed.
    patched.confidence = 1.0
    for path in answered:
        patched.field_confidence[path] = 1.0
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
    locator=None,
    queues=None,
) -> PayableService:
    directory: PayableDirectory
    if backend == "firestore":
        directory = FirestorePayableDirectory(project, collection)
    else:
        directory = InMemoryPayableDirectory()
    return PayableService(
        directory, reader, knowledge, tasks, engine, graph, documents, anomaly,
        gateway, dispatcher, locator, queues,
    )
