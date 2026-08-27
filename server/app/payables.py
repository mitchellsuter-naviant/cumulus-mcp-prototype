"""The accounts-payable Flow: resolve, extract, match, screen, post.

What this flow proves, and the order it proves it in:

1. **A vendor is an entity, not a slug.** The first ModelCall does one narrow
   job — strip every candidate vendor name off the document — and the *resolver*
   turns those into a canonical Vendor id by fuzzy-matching the vendor master.
   The old code matched a slug against exemplar asset ids, which made the
   knowledge shelf the primary key; a vendor that appeared in two systems, or
   under two spellings, was two vendors. Now the exemplar shelf is keyed *by
   the canonical id*, which is the right way round.
2. **Extraction is data-model shaped.** One-shot against the vendor's exemplar
   when we have one, a three-model ensemble with a code adjudicator when we do
   not. Either way the output is invoice lines against the FlowContract's fact
   paths — the same vocabulary the golden sets assert on and the graph stores.
3. **The three-way match is retained.** Invoice lines are paired against the PO
   lines and receiving lines already in the graph (landed by their own sync
   Flows), and the pairings are written as a `MatchSet` object rather than
   computed and discarded. `no receiving report yet` is a *state*, not a
   failure.
4. **Anomaly screening replaces the fraud placeholder.** Scored against this
   vendor's own history from the derived `vendor_payment_profile`, with the
   thresholds read from the posting policy at run time.
5. **The flywheel still turns.** Verifying an ensemble writes the exemplar back
   into the corpus keyed by the vendor id, so the vendor's next invoice takes
   the one-shot path.

Steps: t1 trigger → s1 store-document → m1 read-vendor-candidates → z1
resolve-vendor → x1 load-exemplar → r1 exemplar-known → (m2 one-shot | m2a/b/c
ensemble + x2 adjudicate + k1 verify-exemplar) → x3 project-lines → x4
gather-po-and-receiving → c1 three-way-match → c2 anomaly-screen → r2
post-or-review → k2 review-invoice → a1 post-invoice → e1 InvoicePosted.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import statistics
import threading
from collections import Counter
from datetime import date, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from .anomaly import AnomalyService, parse_anomaly_thresholds
from .documents import DOCUMENT_COLLECTION, DocumentService, Region
from .graph import Claim, GraphService, GraphWriter, object_id
from .matching import MatchLine, MatchResult, match_invoice, parse_match_tolerances
from .models import AssetCreate, AssetUpdate, utcnow
from .reimbursements import JournalEvent, _event  # shared journal shape
from .resolution import ResolutionOutcome, ResolutionThresholds
from .resolution import read_resolution_thresholds, resolve
from .runs import RunContext, RunEngine, RunRecord
from .tasks import Task, TaskEvidence, TaskProposal, TaskService

log = logging.getLogger("cumulus.payables")

FLOW_ID = "accounts-payable"
POLICY_ASSET_ID = "invoice-posting-policy"
CLERK_QUEUE = "ap-clerk"

PayableState = Literal["posted", "pending", "denied"]
ExtractionPath = Literal["one-shot", "ensemble"]


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------
# Shared by the stub extractor and the seeded purchase orders so a local
# three-way match is a real match rather than a coincidence: the same SKUs, the
# same descriptions, the same unit prices on both sides. A deployment with a
# real extractor never reads this.

_CATALOG: tuple[tuple[str, str, float], ...] = (
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


class VendorCandidates(BaseModel):
    """The first ModelCall's whole job: names, nothing else.

    Narrow on purpose. Asking one call to both name the vendor and resolve it
    is what produced vendor-as-a-slug; the model reads text off a page, and
    deciding which entity that text refers to is the resolver's job against the
    master, not the model's against its own memory.
    """

    names: list[str] = Field(default_factory=list)
    tax_id: str = ""
    remit_to: str = ""
    confidence: float = 0.0
    model: str = ""


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
    vendor_candidates: list[str] = Field(default_factory=list)
    # e.g. `knowledge:vendor-exemplar-…@rev1`; "" when none.
    exemplar_ref: str = ""
    extraction_path: ExtractionPath = "one-shot"
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
    candidates: list[dict] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
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
    one_shot: int = 0
    ensemble: int = 0
    matched: int = 0
    match_variance: int = 0
    match_blocked: int = 0
    anomalies: int = 0


# ---------------------------------------------------------------------------
# Readers (the ModelCalls' engines)
# ---------------------------------------------------------------------------


class InvoiceReader(Protocol):
    def read_vendor_candidates(
        self, image: bytes | None, filename: str, hint: str
    ) -> VendorCandidates: ...

    def read(
        self,
        image: bytes | None,
        filename: str,
        hint: str,
        persona: str,
        exemplar: str | None,
    ) -> InvoiceExtraction: ...


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


class StubInvoiceReader:
    """Deterministic local-dev extractor: no credential, no network.

    Steerable for demos, and the hints matter because they are how a local run
    exercises each branch:

    - `vendor=acme-industrial` names a vendor; with an exemplar on the shelf the
      one-shot path runs.
    - `newvendor` (or an unrecognized `vendor=`) names one we do not know, so the
      ensemble path runs and a clerk verifies the proposed exemplar.
    - `po=PO-7741` bills against that purchase order, using SKUs from the shared
      catalog so the three-way match is real.
    - `pricebump` raises a unit price 8% (price variance), `overbill` bills more
      than was received (quantity variance), `nopo` omits the PO entirely
      (unmatched), `blurry` drops confidence below the policy floor.
    """

    name = "stub"

    # -- m1 ---------------------------------------------------------------
    def read_vendor_candidates(
        self, image: bytes | None, filename: str, hint: str
    ) -> VendorCandidates:
        text = f"{filename} {hint}".lower()
        seed = hashlib.sha256(
            (image or b"") + filename.encode() + hint.encode()
        ).digest()
        vendor_match = re.search(r"vendor[=\-_]([a-z0-9\-]+)", text)
        new_match = re.search(r"newvendor[=\-_]([a-z0-9\-]+)", text)
        if new_match:
            primary = f"{_slug_to_name(new_match.group(1))} Ltd."
            confidence = 0.88
        elif vendor_match:
            primary = _slug_to_name(vendor_match.group(1))
            confidence = 0.94
        elif "newvendor" in text:
            primary = f"Meridian Supply {seed[:2].hex().upper()} Ltd."
            confidence = 0.88
        else:
            primary = _AP_VENDORS[seed[3] % len(_AP_VENDORS)]
            confidence = 0.91
        # A real reader returns everything that looks like a party on the page:
        # the biller, the remit-to, sometimes a parent company. The resolver is
        # what decides which one is the vendor.
        names = [primary]
        if "remitto" in text or seed[4] % 3 == 0:
            names.append(f"{primary.split()[0]} Holdings")
        return VendorCandidates(
            names=names,
            tax_id=f"98-{int.from_bytes(seed[5:7], 'big') % 10000000:07d}"
            if "taxid" in text else "",
            remit_to=names[-1] if len(names) > 1 else primary,
            confidence=0.55 if "blurry" in text else confidence,
            model="stub-classifier",
        )

    # -- m2 / m2a-c -------------------------------------------------------
    def read(
        self,
        image: bytes | None,
        filename: str,
        hint: str,
        persona: str = "oneshot",
        exemplar: str | None = None,
    ) -> InvoiceExtraction:
        text = f"{filename} {hint}".lower()
        seed = hashlib.sha256(
            (image or b"") + filename.encode() + hint.encode() + persona.encode()
        ).digest()
        base = hashlib.sha256(
            (image or b"") + filename.encode() + hint.encode()
        ).digest()

        vendor_match = re.search(r"vendor[=\-_]([a-z0-9\-]+)", text)
        new_match = re.search(r"newvendor[=\-_]([a-z0-9\-]+)", text)
        if new_match:
            slug = f"{new_match.group(1)}-ltd"
        elif vendor_match:
            slug = vendor_match.group(1)
        elif "newvendor" in text:
            slug = f"meridian-supply-{base[:2].hex()}"
        else:
            slug = _name_to_slug(_AP_VENDORS[base[3] % len(_AP_VENDORS)])
        vendor_name = _slug_to_name(slug)

        po_match = re.search(r"po[=\-_](po-\d+)", text)
        po_number = "" if "nopo" in text else (
            po_match.group(1).upper() if po_match
            else f"PO-{7000 + base[6] % 900}"
        )
        invoice_match = re.search(r"invoice[=\-_]([a-z0-9\-]+)", text)
        invoice_number = (
            invoice_match.group(1).upper() if invoice_match
            else f"{slug[:4].upper()}-INV-{20000 + int.from_bytes(base[7:9], 'big') % 9000}"
        )
        issued = date.today() - timedelta(days=base[10] % 21)

        # Lines from the shared catalog, so a synced PO can actually match.
        # `lines=STL-0250:2,FAS-M12:3` pins them exactly, which is how a seeded
        # invoice bills the same SKUs its purchase order ordered.
        by_sku = {sku: (sku, description, price) for sku, description, price in _CATALOG}
        pinned = re.search(r"lines[=\-_]([a-z0-9:.,\-]+)", text, re.IGNORECASE)
        picks: list[tuple[str, str, float]] = []
        pinned_quantities: list[float] = []
        if pinned:
            for token in pinned.group(1).split(","):
                sku, _, quantity = token.partition(":")
                entry = by_sku.get(sku.strip().upper())
                if entry is None:
                    continue
                picks.append(entry)
                pinned_quantities.append(float(quantity or 1))
        if not picks:
            count = 2 + base[11] % 2
            picks = [
                _CATALOG[(base[12 + i] + i) % len(_CATALOG)] for i in range(count)
            ]
        seen: set[str] = set()
        lines: list[InvoiceLineExtract] = []
        for index, (sku, description, unit_price) in enumerate(picks):
            if sku in seen:
                continue
            seen.add(sku)
            quantity = (
                pinned_quantities[index] if index < len(pinned_quantities)
                else float(1 + (base[15 + index] % 4))
            )
            if "overbill" in text and index == 0:
                quantity += 2
            price = unit_price
            if "pricebump" in text and index == 0:
                price = round(unit_price * 1.08, 2)
            lines.append(InvoiceLineExtract(
                sku=sku, description=description, quantity=quantity,
                unit_price_usd=price,
                amount_usd=round(quantity * price, 2), kind="goods",
            ))

        total_match = re.search(r"total[=\-_]\$?(\d+(?:\.\d{1,2})?)", text)
        subtotal = round(sum(line.amount_usd for line in lines), 2)
        freight = round(48 + base[20] % 90, 2)
        if freight > 0:
            lines.append(InvoiceLineExtract(
                sku=_FREIGHT[0], description=_FREIGHT[1], quantity=1.0,
                unit_price_usd=freight, amount_usd=freight, kind="freight",
            ))
        total = round(subtotal + freight, 2)
        if total_match:
            # An explicit total wins and the freight line absorbs the
            # difference, so the demo's arithmetic still adds up.
            wanted = float(total_match.group(1))
            freight = round(max(wanted - subtotal, 0), 2)
            if lines and lines[-1].kind == "freight":
                lines[-1].unit_price_usd = freight
                lines[-1].amount_usd = freight
            total = round(subtotal + freight, 2)

        # Ensemble personas read the same document slightly differently — the
        # point of an ensemble is that they disagree in recoverable ways.
        confidence = 0.55 if "blurry" in text else 0.93
        invoice_date_text = issued.isoformat()
        due = (issued + timedelta(days=30)).isoformat()
        if persona == "b":
            invoice_date_text = issued.strftime("%m/%d/%Y")
            confidence = round(confidence - 0.06, 2)
        elif persona == "c":
            total = round(total + (0.02 if seed[0] % 2 else -0.03), 2)
            confidence = round(confidence - 0.03, 2)
            po_number = po_number if seed[1] % 4 else ""
        elif persona == "oneshot" and exemplar:
            confidence = 0.97

        return InvoiceExtraction(
            vendor_name=vendor_name, vendor_slug=slug,
            invoice_number=invoice_number, invoice_date=invoice_date_text,
            due_date=due, po_number=po_number,
            subtotal_usd=subtotal, freight_usd=freight, tax_usd=0.0,
            total_usd=total, currency="USD",
            line_summary=", ".join(
                f"{line.quantity:g}× {line.sku}" for line in lines if line.kind == "goods"
            ),
            confidence=confidence,
            model=f"stub-extractor-{persona}",
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

    Same client and credentials as the embedder and the answer backend. Two
    prompts, because the two calls have genuinely different jobs: one reads the
    parties off the page, the other reads the commercial content — with the
    vendor's exemplar in front of it when one exists.
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

    def read_vendor_candidates(
        self, image: bytes | None, filename: str, hint: str
    ) -> VendorCandidates:
        prompt = (
            "List every party named on this invoice that could be the vendor "
            "who issued it — the biller, the remit-to name, and any parent or "
            "trading name. Do not guess an identifier and do not normalize the "
            "names; return them as printed. Also return the vendor tax id if "
            "one is printed, and your confidence 0-1 that the first name is "
            "the issuing vendor. Respond with only a JSON object with keys "
            "names (array of strings), tax_id, remit_to, confidence."
            + (f" Context: {hint}" if hint else "")
        )
        data = self._generate(self._parts(image, filename, prompt), 0.0)
        names = [str(n) for n in (data.get("names") or []) if str(n).strip()]
        return VendorCandidates(
            names=names,
            tax_id=str(data.get("tax_id", "") or ""),
            remit_to=str(data.get("remit_to", "") or ""),
            confidence=float(data.get("confidence", 0) or 0),
            model=self._model,
        )

    def read(
        self,
        image: bytes | None,
        filename: str,
        hint: str,
        persona: str = "oneshot",
        exemplar: str | None = None,
    ) -> InvoiceExtraction:
        role = (
            "Extract using the vendor exemplar below as the template."
            if exemplar else
            "Extract independently; another extractor is reading the same "
            "document and you must not assume its answer."
        )
        prompt = (
            f"{role} Extract from this invoice: vendor_name, invoice_number, "
            "invoice_date (ISO 8601), due_date (ISO 8601), po_number, "
            "subtotal_usd, freight_usd, tax_usd, total_usd, currency, a short "
            "line_summary, your confidence 0-1, and lines: an array of the "
            "billed lines, each with sku, description, quantity, "
            "unit_price_usd, amount_usd and kind (goods, freight, tax or "
            "discount). Respond with only a JSON object with those keys."
            + (f"\n\nVendor exemplar (one-shot template):\n{exemplar}" if exemplar else "")
            + (f"\n\nContext: {hint}" if hint else "")
        )
        temperature = {"a": 0.0, "b": 0.2, "c": 0.4}.get(persona, 0.0)
        data = self._generate(self._parts(image, filename, prompt), temperature)
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
            model=f"{self._model}:{persona}",
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
    confidence = re.search(
        r"minimum classification confidence:?\s*(\d+(?:\.\d+)?)",
        content, re.IGNORECASE,
    )
    if confidence:
        values.min_confidence = float(confidence.group(1))
    return values


# ---------------------------------------------------------------------------
# Adjudication — field-by-field majority over the ensemble
# ---------------------------------------------------------------------------

_ADJUDICATED_TEXT_FIELDS = [
    "invoice_number", "invoice_date", "due_date", "po_number", "line_summary",
]


def adjudicate(candidates: list[InvoiceExtraction]) -> tuple[dict, list[str]]:
    """Field-by-field majority over the independent extractions.

    Amounts within a cent agree; otherwise the median wins and a conflict is
    recorded. Empty fields lose to non-empty ones. Lines are taken from the
    candidate whose line count is the median and whose subtotal is closest to
    the adjudicated total — merging line arrays across extractors produces
    plausible nonsense, and a clerk reviewing an ensemble needs one coherent
    reading to correct rather than a union of three.
    """
    conflicts: list[str] = []
    adjudicated: dict = {}

    for field in _ADJUDICATED_TEXT_FIELDS:
        values = [getattr(c, field) for c in candidates]
        shown = [v if v else "(empty)" for v in values]
        non_empty = [v for v in values if v]
        if not non_empty:
            adjudicated[field] = ""
            continue
        top, votes = Counter(non_empty).most_common(1)[0]
        if votes >= 2:
            winner, note = top, f"majority {top}"
        else:
            winner, note = (
                non_empty[0], f"no majority — extractor A's {non_empty[0]} retained"
            )
        if len(set(values)) > 1:
            conflicts.append(f"{field}: {' vs '.join(shown)} — {note}")
        adjudicated[field] = winner

    totals = [round(c.total_usd, 2) for c in candidates]
    total = round(statistics.median(totals), 2)
    if max(totals) - min(totals) > 0.01 + 1e-9:
        conflicts.append(
            "total_usd: " + " vs ".join(f"${t:,.2f}" for t in totals)
            + f" — median ${total:,.2f} wins"
        )
    adjudicated["total_usd"] = total
    adjudicated["confidence"] = round(
        statistics.median(c.confidence for c in candidates), 2
    )

    line_counts = [len(c.lines) for c in candidates]
    if len(set(line_counts)) > 1:
        conflicts.append(
            "lines: " + " vs ".join(f"{n} line(s)" for n in line_counts)
            + " — the closest coherent reading is kept whole"
        )
    target = statistics.median(line_counts)
    chosen = min(
        candidates,
        key=lambda c: (abs(len(c.lines) - target), abs(c.total_usd - total)),
    )
    adjudicated["lines"] = [line.model_dump() for line in chosen.lines]
    adjudicated["subtotal_usd"] = chosen.subtotal_usd
    adjudicated["freight_usd"] = chosen.freight_usd
    adjudicated["tax_usd"] = chosen.tax_usd
    adjudicated["currency"] = chosen.currency
    adjudicated["vendor_name"] = chosen.vendor_name
    adjudicated["vendor_slug"] = chosen.vendor_slug
    return adjudicated, conflicts


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


def _exemplar_asset_id(vendor_id: str, slug: str) -> str:
    """Exemplars are keyed by the canonical vendor id, with the slug for humans.

    The old scheme keyed them by slug alone, which quietly made the asset id
    the vendor's identity: two spellings meant two exemplars, and merging the
    vendors did not merge the shelves. The id is the key; the slug is a label.
    """
    suffix = vendor_id.split("_")[-1] if "_" in vendor_id else vendor_id
    return f"vendor-exemplar-{slug or 'unknown'}-{suffix}"[:120]


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
    ) -> None:
        self.directory = directory
        self.reader = reader
        self.knowledge = knowledge
        self.tasks = tasks
        self.engine = engine
        self.graph = graph
        self.documents = documents
        self.anomaly = anomaly
        if engine is not None:
            engine.register(FLOW_ID, self._body)

    # -- reads --------------------------------------------------------------

    def list(self, state: PayableState | None = None) -> list[InvoiceRecord]:
        items = self.directory.list()
        if state is not None:
            items = [r for r in items if r.state == state]
        return sorted(items, key=lambda r: r.submitted_at_iso, reverse=True)

    def get(self, record_id: str) -> InvoiceRecord | None:
        return self.directory.get(record_id)

    def stats(self) -> PayableStats:
        stats = PayableStats()
        for r in self.directory.list():
            stats.total += 1
            if r.extraction_path == "one-shot":
                stats.one_shot += 1
            else:
                stats.ensemble += 1
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
        if self.engine is None:
            raise RuntimeError("Payable service has no run engine wired")
        inputs = {
            "submitted_by": submitted_by,
            "image_b64": image_b64,
            "filename": filename,
            "hint": hint,
            "submitted_at_iso": submitted_at_iso or utcnow().isoformat(),
        }
        record = self.engine.start(
            FLOW_ID, inputs, actor=submitted_by, run_id=run_id
        )
        return self._project(record)

    def resume_for_task(self, task: Task) -> InvoiceRecord | None:
        if task.flow_id != FLOW_ID or self.engine is None:
            return None
        record = self.engine.resume_for_task(task.id)
        return self._project(record) if record else None

    def apply_task_decision(self, task: Task) -> InvoiceRecord | None:
        return self.resume_for_task(task)

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

        # -- m1: names off the page, nothing more -------------------------
        candidates = VendorCandidates.model_validate(ctx.step(
            "m1", "read-vendor-candidates", "modelcall",
            fn=lambda: self.reader.read_vendor_candidates(
                image, filename, hint
            ).model_dump(),
            summary=lambda out: (
                f"{out['model']}: {len(out['names'])} candidate name(s) — "
                + ", ".join(out["names"][:3])
                + f" · confidence {out['confidence']:.2f}"
            ),
            model=getattr(self.reader, "name", ""),
            refs=[f"document:{document_id}"],
            retries=1, escalate_queue=CLERK_QUEUE,
        ))

        # -- z1: names become one canonical vendor ------------------------
        vendor_writer = (
            self.graph.writer(ctx.record.id, "z1") if self.graph else None
        )
        resolution_raw = ctx.step(
            "z1", "resolve-vendor", "resolve",
            fn=lambda: self._resolve_vendor(
                candidates, ctx.record.id, vendor_writer
            ),
            writes=lambda _out: (
                vendor_writer.refs if vendor_writer is not None else []
            ),
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
                    ctx.record, candidates, resolution_raw, submitted_by
                ),
            )
            decision_writer = (
                self.graph.writer(ctx.record.id, "a0") if self.graph else None
            )
            chosen = ctx.step(
                "a0", "apply-vendor-decision", "action",
                fn=lambda: self._apply_vendor_decision(
                    candidates, resolution_raw, decision, ctx.record.id,
                    decision_writer,
                ),
                writes=lambda _out: (
                    decision_writer.refs if decision_writer is not None else []
                ),
                summary=lambda out: (
                    f"{out['action']} {out['label']} by {out['decided_by']}"
                ),
            )
            vendor_id = chosen["vendor_id"]
            vendor_label = chosen["label"]

        # -- x1/r1: the governed knowledge read that changes the plan ------
        exemplar = ctx.step(
            "x1", "load-exemplar", "transform",
            fn=lambda: self._load_exemplar(vendor_id, candidates),
            summary=lambda out: (
                f"{out['ref']} found — one-shot extraction available"
                if out["found"] else
                f"no exemplar for {vendor_label or 'this vendor'} — the "
                "ensemble path will propose one"
            ),
        )
        if exemplar["found"]:
            ctx.note_refs(exemplar["ref"])
        one_shot = bool(exemplar["found"])
        ctx.step(
            "r1", "exemplar-known", "rule",
            fn=lambda: {"one_shot": one_shot},
            summary=(
                "one-shot: a verified template exists for this vendor"
                if one_shot else
                "ensemble: no template — three independent reads, adjudicated, "
                "then verified by a clerk"
            ),
            refs=[exemplar["ref"]] if exemplar["found"] else [],
        )

        conflicts: list[str] = []
        candidate_dumps: list[dict] = []
        if one_shot:
            extraction = InvoiceExtraction.model_validate(ctx.step(
                "m2", "extract-oneshot", "modelcall",
                fn=lambda: self._extract(
                    image, filename, hint, "oneshot", exemplar["content"],
                    document_id,
                ).model_dump(),
                summary=lambda out: (
                    f"{out['model']}: {out['invoice_number']} · "
                    f"${out['total_usd']:,.2f} · {len(out['lines'])} line(s) · "
                    f"confidence {out['confidence']:.2f}"
                ),
                model=getattr(self.reader, "name", ""),
                refs=[exemplar["ref"], f"document:{document_id}"],
                retries=1, escalate_queue=CLERK_QUEUE,
            ))
            path: ExtractionPath = "one-shot"
        else:
            reads: list[InvoiceExtraction] = []
            for persona, step_id in (("a", "m2a"), ("b", "m2b"), ("c", "m2c")):
                reads.append(InvoiceExtraction.model_validate(ctx.step(
                    step_id, f"extract-ensemble[{persona}]", "modelcall",
                    fn=lambda p=persona: self._extract(
                        image, filename, hint, p, None, document_id
                    ).model_dump(),
                    summary=lambda out: (
                        f"{out['model']}: {out['invoice_number']} · "
                        f"${out['total_usd']:,.2f} · {len(out['lines'])} line(s)"
                    ),
                    model=getattr(self.reader, "name", ""),
                    refs=[f"document:{document_id}"],
                    retries=1, escalate_queue=CLERK_QUEUE,
                )))
            adjudicated = ctx.step(
                "x2", "adjudicate", "transform",
                fn=lambda: _adjudicate_output(reads),
                summary=lambda out: (
                    f"merged 3 reads · {len(out['conflicts'])} conflict(s)"
                    + (
                        ": " + "; ".join(c.split(":")[0] for c in out["conflicts"])
                        if out["conflicts"] else ""
                    )
                ),
            )
            conflicts = adjudicated["conflicts"]
            candidate_dumps = adjudicated["candidates"]
            extraction = InvoiceExtraction.model_validate(adjudicated["merged"])
            path = "ensemble"

            verification = ctx.gate(
                "k1", "verify-exemplar",
                build_task=lambda: self._build_verify_task(
                    ctx.record, extraction, conflicts, vendor_label,
                    resolution_raw, submitted_by,
                ),
            )
            exemplar_writer = (
                self.graph.writer(ctx.record.id, "a2") if self.graph else None
            )
            written = ctx.step(
                "a2", "write-exemplar", "action",
                fn=lambda: self._write_exemplar(
                    vendor_id, vendor_label, extraction, verification,
                    ctx.record.id, exemplar_writer,
                ),
                writes=lambda out: (
                    ([out["ref"]] if out.get("written") else [])
                    + (exemplar_writer.refs if exemplar_writer is not None else [])
                ),
                summary=lambda out: (
                    f"exemplar {out['ref']} written to the shelf — this "
                    "vendor's next invoice takes the one-shot path"
                    if out["written"] else
                    f"no exemplar written ({out['reason']})"
                ),
            )
            if written["written"]:
                ctx.note_refs(written["ref"])
            if verification.verdict == "rejected":
                return self._finish(
                    ctx, submitted_by, submitted_at, filename, document_id,
                    vendor_id, vendor_label, resolution_raw, candidates,
                    exemplar, path, extraction, conflicts, candidate_dumps,
                    match=None, screen=None, policy=self.read_policy(),
                    state="denied",
                    reason=verification.note or "rejected at exemplar verification",
                    decided_by=verification.decided_by,
                    decision_note=verification.note,
                )

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
            vendor_label, resolution_raw, candidates, exemplar, path,
            extraction, conflicts, candidate_dumps, match, screen, policy,
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
        persona: str,
        exemplar: str | None,
        document_id: str,
    ) -> InvoiceExtraction:
        extraction = self.reader.read(image, filename, hint, persona, exemplar)
        if self.documents is not None and extraction.regions:
            self.documents.set_regions(document_id, extraction.regions)
        return extraction

    def _resolve_vendor(
        self,
        candidates: VendorCandidates,
        run_id: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        thresholds = read_resolution_thresholds(self.knowledge)
        if self.graph is None:
            return {
                "disposition": "new", "vendor_id": "", "label": candidates.names[0]
                if candidates.names else "", "score": 0.0, "method": "none",
                "reason": "no graph wired", "candidates": [],
                "thresholds_ref": thresholds.source_ref,
            }
        outcome = resolve(
            self.graph, "Vendor", candidates.names, thresholds,
            tax_id=candidates.tax_id,
        )
        payload = outcome.model_dump()
        payload["vendor_id"] = outcome.object_id
        if outcome.disposition == "new" and candidates.names:
            # Mint it now, `onboarding`, so the rest of the run has an id to
            # hang facts off. The clerk's exemplar verification doubles as the
            # confirmation that this vendor is real — which is exactly the
            # unknown-vendor onboarding step the doctrine describes.
            vendor = (writer or self.graph).upsert(
                "Vendor", candidates.names[0],
                [
                    Claim(field="name", value=candidates.names[0],
                          source=f"model:{candidates.model}",
                          confidence=candidates.confidence),
                    Claim(field="slug", value=_name_to_slug(candidates.names[0]),
                          source=f"flow:{FLOW_ID}"),
                    Claim(field="status", value="onboarding",
                          source=f"flow:{FLOW_ID}"),
                ]
                + ([Claim(field="tax_id", value=candidates.tax_id,
                          source=f"model:{candidates.model}")]
                   if candidates.tax_id else []),
                label=candidates.names[0],
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
        candidates: VendorCandidates,
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
                        field="aliases", value=candidates.names,
                        source=f"human:{decision.decided_by}",
                    )],
                    label=vendor.label,
                )
                return {
                    "action": "linked", "vendor_id": vendor.id,
                    "label": vendor.label, "decided_by": decision.decided_by,
                }
        name = candidates.names[0] if candidates.names else "Unknown vendor"
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

    def _load_exemplar(self, vendor_id: str, candidates: VendorCandidates) -> dict:
        """Look for this vendor's one-shot template, keyed by canonical id."""
        if not vendor_id:
            return {"found": False, "ref": "", "content": "", "asset_id": ""}
        vendor = self.graph.get(vendor_id) if self.graph else None
        slug = str((vendor.props.get("slug") if vendor else "") or "")
        if not slug and vendor is not None:
            slug = _name_to_slug(vendor.label)
        asset_id = _exemplar_asset_id(vendor_id, slug)
        asset = self.knowledge.repo.get(asset_id)
        if asset is None:
            # Fall back to the pre-canonical naming so exemplars written before
            # vendors had ids are still found. One lookup, and it keeps the
            # seeded Acme exemplar working.
            legacy = self.knowledge.repo.get(f"vendor-exemplar-{slug}")
            asset = legacy
            asset_id = legacy.id if legacy else asset_id
        if asset is None:
            return {"found": False, "ref": "", "content": "", "asset_id": asset_id}
        return {
            "found": True, "ref": _knowledge_ref(asset),
            "content": asset.content, "asset_id": asset.id,
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
        candidates: VendorCandidates,
        resolution_raw: dict,
        submitted_by: str,
    ) -> Task:
        ranked = resolution_raw.get("candidates") or []
        return Task(
            id=f"task_{record.id}_k0",
            kind="match_review",
            title=f"Which vendor issued this invoice? {candidates.names[0] if candidates.names else '—'}",
            summary=(
                f"The document names {', '.join(candidates.names) or 'no clear party'}. "
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
                TaskEvidence(label="Names on the document",
                             value=", ".join(candidates.names) or "—"),
                TaskEvidence(label="Tax id", value=candidates.tax_id or "—"),
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
            resumes="this run resumes at k0 and extracts against the chosen vendor",
            created_at_iso=utcnow().isoformat(),
        )

    def _build_verify_task(
        self,
        record: RunRecord,
        extraction: InvoiceExtraction,
        conflicts: list[str],
        vendor_label: str,
        resolution_raw: dict,
        submitted_by: str,
    ) -> Task:
        return Task(
            id=f"task_{record.id}_k1",
            kind="curation",
            title=f"Verify extraction and exemplar: {vendor_label or extraction.vendor_name}",
            summary=(
                "No exemplar existed for this vendor, so three extractors read "
                "the document independently and an adjudicator merged them "
                f"({len(conflicts)} disagreement(s)). Approving verifies the "
                "adjudicated fields and writes the vendor's exemplar into the "
                "knowledge layer, so the next invoice from them is a single "
                "confident pass. This is also the confirmation that "
                f"{vendor_label or 'this vendor'} is a real vendor."
            ),
            queue=CLERK_QUEUE,
            priority="normal",
            flow_id=FLOW_ID,
            run_id=record.id,
            case_ref=record.case_ref or record.id,
            refs=[resolution_raw.get("thresholds_ref", "")],
            evidence=[
                TaskEvidence(label="Vendor", value=vendor_label or extraction.vendor_name),
                TaskEvidence(label="Invoice number", value=extraction.invoice_number),
                TaskEvidence(label="Invoice date", value=extraction.invoice_date),
                TaskEvidence(label="PO on document", value=extraction.po_number or "—"),
                TaskEvidence(label="Total", value=f"${extraction.total_usd:,.2f}"),
                TaskEvidence(label="Lines", value=str(len(extraction.lines))),
                TaskEvidence(label="Submitted by", value=submitted_by),
            ] + [
                TaskEvidence(label="Conflict", value=conflict)
                for conflict in conflicts[:5]
            ],
            proposal=TaskProposal(
                action="write-exemplar", target="ap-vendor-exemplars",
                summary=f"Write the one-shot template for {vendor_label}",
            ),
            resumes="this run resumes at k1, writes the exemplar, then matches "
                    "and screens the invoice",
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
                TaskEvidence(label="Three-way match",
                             value=f"{match.verdict} — {match.summary()}"),
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

    # -- the flywheel -------------------------------------------------------

    def _write_exemplar(
        self,
        vendor_id: str,
        vendor_label: str,
        extraction: InvoiceExtraction,
        decision,
        run_id: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        """The governed-knowledge write: a verified reading becomes a template.

        Only on approve or correct, and only ever through this step — promotion
        into canonical knowledge is a decision, not a side effect of a
        successful extraction.
        """
        if decision.verdict == "rejected":
            return {
                "written": False, "ref": "",
                "reason": "the verifier rejected the extraction",
            }
        vendor = self.graph.get(vendor_id) if self.graph else None
        slug = str((vendor.props.get("slug") if vendor else "") or "") or _name_to_slug(
            vendor_label or extraction.vendor_name
        )
        asset_id = _exemplar_asset_id(vendor_id, slug)
        content = _exemplar_content(
            vendor_label or extraction.vendor_name, slug, extraction, decision, run_id
        )
        existing = self.knowledge.repo.get(asset_id)
        if existing is None:
            asset = self.knowledge.create(AssetCreate(
                id=asset_id,
                path=f"finance/exemplars/{slug}",
                title=f"Vendor exemplar: {vendor_label or extraction.vendor_name}",
                content=content,
                kind="exemplar",
                origin="flywheel",
                scope="org",
                owner="finance-ops",
                tags=["exemplar", "ap", slug],
                status="published",
            ))
        else:
            asset = self.knowledge.update(asset_id, AssetUpdate(content=content))
        # The vendor graduates: a human has now vouched for it.
        if self.graph is not None and vendor is not None:
            (writer or self.graph).upsert(
                "Vendor", vendor.natural_key,
                [Claim(
                    field="status", value="active",
                    source=f"human:{decision.decided_by}",
                )],
                label=vendor.label,
            )
        log.info(
            "Vendor exemplar written",
            extra={
                "assetId": asset_id, "vendorId": vendor_id, "runId": run_id,
                "actor": decision.decided_by,
            },
        )
        return {"written": True, "ref": _knowledge_ref(asset), "reason": ""}

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
        candidates: VendorCandidates,
        exemplar: dict,
        path: ExtractionPath,
        extraction: InvoiceExtraction,
        conflicts: list[str],
        candidate_dumps: list[dict],
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
            vendor_confidence=candidates.confidence,
            vendor_resolution=str(resolution_raw.get("disposition", "")),
            vendor_method=str(resolution_raw.get("method", "")),
            vendor_candidates=list(candidates.names),
            exemplar_ref=exemplar.get("ref", ""),
            extraction_path=path,
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
            candidates=candidate_dumps,
            conflicts=conflicts,
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

        post_writer = self.graph.writer(ctx.record.id, "a1") if self.graph else None
        written = ctx.step(
            "a1", "post-invoice", "action",
            fn=lambda: self._write_graph(record, match, ctx.record.id, post_writer),
            writes=lambda _out: post_writer.refs if post_writer is not None else [],
            summary=lambda out: (
                f"{record.state} · ${record.total_usd:,.2f} · {out['objects']} "
                f"object(s) and {out['links']} link(s) written · ledger "
                f"led_{record.id[-6:]}"
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
                "path": record.extraction_path, "vendorId": vendor_id,
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
        model_source = f"model:{record.extraction_path}"
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
        exemplar = output("x1", {})
        stored = output("s1", {})
        one_shot = step("m2") is not None

        extraction = InvoiceExtraction()
        # The last correction wins, then the adjudicated merge, then the
        # one-shot read — newest reading of the document first.
        sources = (
            [f"x6_{n}" for n in range(MAX_REVIEW_ROUNDS, 0, -1)] + ["m2", "x2"]
        )
        for step_id in sources:
            payload = output(step_id)
            if not payload:
                continue
            if isinstance(payload, dict) and "extraction" in payload:
                payload = payload["extraction"]
            elif isinstance(payload, dict) and "merged" in payload:
                payload = payload["merged"]
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
        adjudication = output("x2", {}) or {}
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
            exemplar_ref=str(exemplar.get("ref", "")),
            extraction_path="one-shot" if one_shot else "ensemble",
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
            candidates=list(adjudication.get("candidates") or []),
            conflicts=list(adjudication.get("conflicts") or []),
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


def _adjudicate_output(reads: list[InvoiceExtraction]) -> dict:
    merged, conflicts = adjudicate(reads)
    base = reads[0].model_dump()
    base.update(merged)
    base["model"] = "adjudicator(" + ", ".join(r.model for r in reads) + ")"
    base["regions"] = reads[0].regions and [r.model_dump() for r in reads[0].regions] or []
    return {
        "merged": base,
        "conflicts": conflicts,
        "candidates": [r.model_dump() for r in reads],
    }


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


def _exemplar_content(
    vendor_name: str,
    slug: str,
    extraction: InvoiceExtraction,
    decision,
    run_id: str,
) -> str:
    """Render a verified reading as a one-shot extraction template."""
    lines = [
        f"# Vendor exemplar: {vendor_name}",
        "",
        f"One-shot extraction template for `{slug}` invoices, verified by a "
        "human from an adjudicated ensemble read. The accounts-payable flow "
        "hands this to the extraction ModelCall when the resolver names this "
        "vendor, so extraction is a single confident pass.",
        "",
        "## Layout",
        "",
        f"- Invoice number: format `{extraction.invoice_number}` — match this "
        "pattern exactly.",
        f"- Invoice date: {extraction.invoice_date} (ISO 8601, header block).",
        "- PO number: "
        + (
            f"`{extraction.po_number}` in the header block."
            if extraction.po_number
            else "this vendor's invoices carry no PO reference."
        ),
        f"- Total: bottom-right summary block (verified total "
        f"${extraction.total_usd:,.2f} {extraction.currency}).",
    ]
    if extraction.freight_usd:
        lines.append(
            f"- Freight appears as its own line (${extraction.freight_usd:,.2f} "
            "on the verified invoice) — do not fold it into materials."
        )
    if extraction.lines:
        lines += [
            "",
            "## Line items",
            "",
            "Columns run SKU · description · quantity · unit price · amount. "
            "The verified read had:",
            "",
        ]
        for line in extraction.lines:
            lines.append(
                f"- `{line.sku or '—'}` {line.description} — "
                f"{line.quantity:g} × ${line.unit_price_usd:,.2f} = "
                f"${line.amount_usd:,.2f} ({line.kind})"
            )
    lines += [
        "",
        "## Provenance",
        "",
        f"Verified on run `{run_id}` by {decision.decided_by}"
        + (f" — {decision.note}" if decision.note else "")
        + ".",
        "",
        "Corrections to this vendor's extractions accumulate in the correction "
        "set and re-propose this exemplar when they show a pattern.",
    ]
    if decision.verdict == "corrected":
        lines.append("")
        lines.append(
            "Correction applied by the verifier: "
            + (decision.correction or decision.note or "see the task record")
        )
    return "\n".join(lines)


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
) -> PayableService:
    directory: PayableDirectory
    if backend == "firestore":
        directory = FirestorePayableDirectory(project, collection)
    else:
        directory = InMemoryPayableDirectory()
    return PayableService(
        directory, reader, knowledge, tasks, engine, graph, documents, anomaly
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
        catalog = {sku: (description, price) for sku, description, price in _CATALOG}
        out = []
        for sku, quantity in _SEED_CATALOG_LINES[po_number]:
            description, price = catalog[sku]
            out.append({
                "sku": sku, "description": description,
                "quantity_ordered": quantity, "unit_price_usd": price,
            })
        return out

    return {
        "source": "erp-purchasing",
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
    catalog = {sku: (description, price) for sku, description, price in _CATALOG}

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
        # 1. Known vendor, exemplar on the shelf, bills its PO exactly.
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
        # 3. No exemplar for this vendor yet, so the ensemble runs and a clerk
        #    verifies it; then the invoice bills more than was received, so it
        #    stops again at the posting gate — left open, so the AP queue has a
        #    live exception with a real match set behind it.
        SeedInvoice(
            run_id="run_ap_seed03", submitted_by="ap-intake@customer.example",
            days_ago=9, filename="baystate-inv-88120.pdf",
            hint="vendor=bay-state-fasteners po=PO-7815 invoice=BSF-INV-88120 "
                 "lines=CNS-HYD:3,FAS-M12:2 overbill",
            decisions=[{
                "verdict": "approved", "decided_by": "dana@customer.example",
                "note": "Fields check out against the document.",
            }],
        ),
        # 4. An unknown vendor: ensemble, adjudication, a clerk verifies the
        #    exemplar (which also onboards the vendor), then the invoice has no
        #    PO so it goes back for a posting decision. Two gates, one run.
        SeedInvoice(
            run_id="run_ap_seed04", submitted_by="ap-intake@customer.example",
            days_ago=7, filename="meridian-first-invoice.pdf",
            hint="newvendor=meridian-supply nopo invoice=MER-0001 "
                 "lines=PPE-GLV:2 total=190.00",
            decisions=[
                {
                    "verdict": "approved", "decided_by": "dana@customer.example",
                    "note": "Fields verified against the document; exemplar looks right.",
                },
                {
                    "verdict": "approved", "decided_by": "dana@customer.example",
                    "note": "First invoice from this vendor, no PO expected at this size.",
                },
            ],
        ),
        # 5. The same vendor again — now on the one-shot path, because run 4
        #    wrote the exemplar. This is the flywheel closing.
        SeedInvoice(
            run_id="run_ap_seed05", submitted_by="ap-intake@customer.example",
            days_ago=5, filename="meridian-second-invoice.pdf",
            hint="newvendor=meridian-supply nopo invoice=MER-0002 "
                 "lines=PPE-GLV:1 total=112.00",
            decisions=[{
                "verdict": "approved", "decided_by": "dana@customer.example",
                "note": "One-shot read matches the document; no PO at this size.",
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
