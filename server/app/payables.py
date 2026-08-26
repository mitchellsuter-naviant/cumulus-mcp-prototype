"""The accounts-payable flow's real execution path, and its table.

This is the prototype's second *live* flow: `POST /api/v1/payables` runs
the same steps the Tier-2 export declares — trigger → classify-vendor
(ModelCall) → load-exemplar (transform) → exemplar-known (Rule), then either
a single one-shot extraction against the vendor's exemplar or an ensemble of
three independent extractions adjudicated field by field and verified by a
human — synchronously, and lands a row in the payables table with its state.

Doctrine mapping, honestly stated:

- The **ModelCalls** are real when `INVOICE_BACKEND=vertex` (multimodal
  Gemini reads the document); locally they degrade to a deterministic stub
  that parses hints, so the pipeline is exercisable without a credential.
- The **governed knowledge read** is real twice over: the vendor exemplar
  that turns three reads into one is a knowledge asset, and the posting
  rule's values (auto-post limit, minimum classification confidence) are
  parsed from the `invoice-posting-policy` asset at run time. Editing that
  document in the console changes what this flow does — no code change.
- The **flywheel** is real: verifying an ensemble's adjudicated fields
  (a curation Task) writes the vendor exemplar back into the knowledge
  layer, and the *next* invoice from that vendor takes the one-shot path.
- **three-way-match** and **fraud-screen** are placeholders and say so in
  the journal — the PO and receiving feeds are not connected yet.
- The **Tasks** are real: an ensemble run suspends at verify-exemplar and
  an over-limit invoice suspends at review-invoice, both in the ap-clerk
  queue; the human's decision resumes the run via `apply_task_decision`.
- The **journal** is the per-run record the canvas run-trace derives from,
  kept on the row so every invoice is inspectable end to end.

A suspended run costs nothing while it waits — the row simply sits at
state=pending with its journal ending at the Task step.
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

from .models import AssetCreate, AssetUpdate, utcnow
from .reimbursements import JournalEvent, _event
from .tasks import Task, TaskEvidence, TaskProposal, TaskService

log = logging.getLogger("cumulus.payables")

FLOW_ID = "accounts-payable"
POLICY_ASSET_ID = "invoice-posting-policy"
CLERK_QUEUE = "ap-clerk"

PayableState = Literal["posted", "pending", "denied"]
ExtractionPath = Literal["one-shot", "ensemble"]

PLACEHOLDER_MATCH = "placeholder — PO and receiving feeds not connected yet"
PLACEHOLDER_FRAUD = (
    "placeholder — will score bank-detail changes, duplicates, "
    "round-number anomalies"
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class InvoiceExtraction(BaseModel):
    vendor_name: str = ""
    vendor_slug: str = ""
    invoice_number: str = ""
    invoice_date: str = ""
    due_date: str = ""
    po_number: str = ""
    total_usd: float = 0.0
    line_summary: str = ""
    confidence: float = 0.0
    model: str = ""


class PostingPolicy(BaseModel):
    """The governed values the post-or-review rule consumes.

    Parsed from the policy asset at run time so a console edit to the
    document takes effect on the next submission. Defaults apply when a
    value is absent, and the journal records which values were used, so a
    run is explainable even against an older revision of the document.
    """

    limit_usd: float = 2500.0
    min_confidence: float = 0.75
    source_ref: str = f"knowledge:{POLICY_ASSET_ID}"
    found_asset: bool = False


class InvoiceRecord(BaseModel):
    id: str
    submitted_by: str
    submitted_at_iso: str
    filename: str = ""
    vendor_slug: str = ""
    vendor_name: str = ""
    vendor_confidence: float = 0.0
    # e.g. `knowledge:vendor-exemplar-acme-industrial@rev1`; "" when none.
    exemplar_ref: str = ""
    extraction_path: ExtractionPath = "one-shot"
    invoice_number: str = ""
    invoice_date: str = ""
    due_date: str = ""
    po_number: str = ""
    total_usd: float = 0.0
    line_summary: str = ""
    # Ensemble only: the three raw extractions, model names included.
    candidates: list[dict] = Field(default_factory=list)
    # Human-readable adjudication disagreements, e.g.
    # "invoice_date: 2026-08-20 vs 08/20/2026 vs 2026-08-20 — majority ISO".
    conflicts: list[str] = Field(default_factory=list)
    three_way_match: str = "placeholder"
    fraud_screen: str = "placeholder"
    state: PayableState = "pending"
    verdict_reason: str = ""
    policy_ref: str = f"knowledge:{POLICY_ASSET_ID}"
    post_limit_usd: float = 0.0
    task_id: str = ""
    decided_by: str = ""
    decision_note: str = ""
    journal: list[JournalEvent] = Field(default_factory=list)


class PayableStats(BaseModel):
    total: int = 0
    posted: int = 0
    pending: int = 0
    denied: int = 0
    posted_usd: float = 0.0
    one_shot: int = 0
    ensemble: int = 0


# ---------------------------------------------------------------------------
# Invoice readers (the ModelCalls' engine)
# ---------------------------------------------------------------------------


class InvoiceReader(Protocol):
    def read(
        self,
        image: bytes | None,
        filename: str,
        hint: str,
        persona: str,
        exemplar: str | None,
    ) -> InvoiceExtraction: ...


_AP_VENDORS = [
    "acme-industrial",
    "northwind-paper",
    "globex-logistics",
    "initech-supplies",
    "steelhaven-metals",
]
_LINE_SUMMARIES = [
    "net-30 service invoice",
    "monthly materials order",
    "freight and handling",
    "maintenance contract billing",
    "quarterly consumables restock",
]


def _slug_to_name(slug: str) -> str:
    return slug.replace("-", " ").title()


class StubInvoiceReader:
    """Deterministic local-dev extractor: no credential, no network.

    Steerable for demos — a filename or hint like `vendor=acme-industrial`,
    `total=1234.56`, `invoice=INV-2041`, `po=PO-7741`, `date=2026-08-20`, or
    the word `blurry` (drops confidence below the policy floor) is honored;
    anything else derives stable pseudo-values from a hash of the input so
    the same file always extracts the same way.

    The three ensemble personas introduce controlled disagreement so the
    adjudicator has work: extractor `b` renders the invoice date MM/DD/YYYY
    instead of ISO, and extractor `c` drops the PO number when a hash byte
    is even, or offsets the total by a cent when it is odd. Persona
    `oneshot` with an exemplar returns clean values at 0.97 — the whole
    point of having the exemplar. The word `newvendor` (or an unhinted
    unknown `vendor=` slug) simply names a vendor no exemplar asset exists
    for; classification confidence stays fine.
    """

    name = "stub"

    def read(
        self,
        image: bytes | None,
        filename: str,
        hint: str,
        persona: str = "a",
        exemplar: str | None = None,
    ) -> InvoiceExtraction:
        raw = f"{filename} {hint}"
        text = raw.lower()
        seed = hashlib.sha256(
            (image or b"") + filename.encode() + hint.encode()
        ).digest()

        def pick(options: list[str], salt: int) -> str:
            return options[seed[salt] % len(options)]

        # -- classification --
        vendor_match = re.search(r"vendor[=\-_]([a-z0-9\-]+)", text)
        if vendor_match:
            slug, vendor_confidence = vendor_match.group(1), 0.96
        elif "newvendor" in text:
            slug, vendor_confidence = f"new-vendor-{seed[:2].hex()}", 0.83
        else:
            slug, vendor_confidence = pick(_AP_VENDORS, 3), 0.83

        # -- extraction fields --
        total_match = re.search(r"total[=\-_ ]?\$?(\d+(?:\.\d{1,2})?)", text)
        total = (
            float(total_match.group(1))
            if total_match
            else round(120 + (int.from_bytes(seed[:2], "big") % 480000) / 100, 2)
        )
        inv_match = re.search(r"invoice[=\-_]([A-Za-z0-9\-]+)", raw, re.IGNORECASE)
        invoice_number = (
            inv_match.group(1).upper()
            if inv_match
            else f"INV-{2000 + int.from_bytes(seed[8:10], 'big') % 8000}"
        )
        po_match = re.search(r"\bpo[=\-_]([A-Za-z0-9\-]+)", raw, re.IGNORECASE)
        po_number = (
            po_match.group(1).upper()
            if po_match
            else f"PO-{7000 + seed[10] % 999}"
        )
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        invoice_date = (
            date_match.group(1)
            if date_match
            else f"2026-{1 + seed[4] % 12:02d}-{1 + seed[5] % 28:02d}"
        )
        try:
            due_date = (date.fromisoformat(invoice_date) + timedelta(days=30)).isoformat()
        except ValueError:
            due_date = invoice_date
        confidence = (
            0.55 if "blurry" in text
            else round(min(0.90 + (seed[2] % 8) / 100, 0.99), 2)
        )
        line_summary = pick(_LINE_SUMMARIES, 6)

        # -- persona behavior --
        if persona == "classify":
            confidence = 0.55 if "blurry" in text else vendor_confidence
        elif persona == "oneshot" and exemplar:
            confidence = 0.55 if "blurry" in text else 0.97
        elif persona == "b":
            y, m, d = invoice_date.split("-")
            invoice_date = f"{m}/{d}/{y}"
        elif persona == "c":
            if seed[6] % 2 == 0:
                po_number = ""
            else:
                total = total + (0.01 if seed[7] % 2 else -0.01)

        return InvoiceExtraction(
            vendor_name=_slug_to_name(slug),
            vendor_slug=slug,
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            due_date=due_date,
            po_number=po_number,
            total_usd=round(total, 2),
            line_summary=line_summary,
            confidence=confidence,
            model=f"stub-extractor-{persona}",
        )


class VertexInvoiceReader:
    """Multimodal extraction on Vertex AI via google-genai.

    Same client and Application Default Credentials as the embedder and the
    answer backend, so enabling this adds no new secret. One class serves
    every step of the flow: the persona feeds the prompt (the three ensemble
    extractors are told to read independently), and when an exemplar is
    given it is included as the one-shot template text.
    """

    name = "vertex"

    def __init__(self, project: str | None, location: str, model: str) -> None:
        from google import genai

        self._client = genai.Client(vertexai=True, project=project, location=location)
        self._model = model

    def read(
        self,
        image: bytes | None,
        filename: str,
        hint: str,
        persona: str = "a",
        exemplar: str | None = None,
    ) -> InvoiceExtraction:
        from google.genai import types

        if persona == "classify":
            role = (
                "Identify the vendor who issued this invoice before anything "
                "else is extracted."
            )
        elif persona == "oneshot":
            role = "Extract using the vendor exemplar below as the template."
        else:
            role = (
                f"You are extractor {persona.upper()} — read the document "
                "independently; do not assume any other reading exists."
            )

        parts: list = []
        if image:
            mime = "image/png" if filename.lower().endswith(".png") else "image/jpeg"
            parts.append(types.Part.from_bytes(data=image, mime_type=mime))
        prompt = (
            f"{role} Extract from this invoice: vendor_name, vendor_slug "
            "(lowercase slug), invoice_number, invoice_date (ISO), due_date "
            "(ISO), po_number, total_usd (number), line_summary (one short "
            "phrase), and your confidence 0-1 in the extraction overall. "
            "Respond with only a JSON object with keys vendor_name, "
            "vendor_slug, invoice_number, invoice_date, due_date, po_number, "
            "total_usd, line_summary, confidence."
        )
        if exemplar:
            prompt += f"\n\nVendor exemplar (one-shot template):\n{exemplar}"
        if hint:
            prompt += f"\n\nSubmitter note: {hint}"
        parts.append(types.Part(text=prompt))
        response = self._client.models.generate_content(
            model=self._model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.0
            ),
        )
        data = json.loads(response.text or "{}")
        return InvoiceExtraction(
            vendor_name=str(data.get("vendor_name", "")),
            vendor_slug=str(data.get("vendor_slug", "")),
            invoice_number=str(data.get("invoice_number", "")),
            invoice_date=str(data.get("invoice_date", "")),
            due_date=str(data.get("due_date", "")),
            po_number=str(data.get("po_number", "")),
            total_usd=float(data.get("total_usd", 0) or 0),
            line_summary=str(data.get("line_summary", "")),
            confidence=float(data.get("confidence", 0) or 0),
            model=self._model,
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
        content,
        re.IGNORECASE,
    )
    if confidence:
        values.min_confidence = float(confidence.group(1))
    return values


# ---------------------------------------------------------------------------
# Adjudication (x2) — field-by-field majority over the ensemble
# ---------------------------------------------------------------------------

_ADJUDICATED_TEXT_FIELDS = [
    "invoice_number",
    "invoice_date",
    "due_date",
    "po_number",
    "line_summary",
]


def adjudicate(candidates: list[InvoiceExtraction]) -> tuple[dict, list[str]]:
    """Field-by-field majority over the three independent extractions.

    Amounts within $0.01 of each other agree; otherwise the median wins and
    a conflict is recorded. Empty fields lose to non-empty ones. Every
    disagreement lands as a human-readable line so the verifier sees exactly
    where the extractors diverged.
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
            winner, note = non_empty[0], f"no majority — extractor A's {non_empty[0]} retained"
        if len(set(values)) > 1:
            conflicts.append(f"{field}: {' vs '.join(shown)} — {note}")
        adjudicated[field] = winner

    totals = [round(c.total_usd, 2) for c in candidates]
    total = round(statistics.median(totals), 2)
    if max(totals) - min(totals) > 0.01 + 1e-9:
        conflicts.append(
            "total_usd: "
            + " vs ".join(f"${t:,.2f}" for t in totals)
            + f" — median ${total:,.2f} wins"
        )
    adjudicated["total_usd"] = total
    adjudicated["confidence"] = round(
        statistics.median(c.confidence for c in candidates), 2
    )
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
                log.exception("Skipping unparseable invoice", extra={"docId": doc.id})
        return out

    def get(self, record_id: str) -> InvoiceRecord | None:
        snap = self._col.document(record_id).get()
        if not snap.exists:
            return None
        try:
            return InvoiceRecord.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable invoice", extra={"id": record_id})
            return None

    def put(self, record: InvoiceRecord) -> InvoiceRecord:
        self._col.document(record.id).set(record.model_dump(mode="json"))
        return record


# ---------------------------------------------------------------------------
# Service — the inline runner
# ---------------------------------------------------------------------------


def _canonicalize_slug(slug: str, known: list[str]) -> str:
    """Snap a classifier slug onto a known vendor when they clearly match.

    Exact match wins; otherwise a known slug whose tokens are a subset of the
    classified slug's tokens (acme-industrial ⊂ acme-industrial-inc) is the
    same vendor wearing a suffix. Anything else stays as classified — a new
    vendor deserves the ensemble, not a forced merge.
    """
    if slug in known:
        return slug
    tokens = set(slug.split("-"))
    for candidate in known:
        if set(candidate.split("-")) <= tokens:
            return candidate
    return slug


def _exemplar_asset_id(slug: str) -> str:
    return f"vendor-exemplar-{slug}"


def _knowledge_ref(asset) -> str:
    # `Asset.revision` is landing concurrently; refs work either way.
    return f"knowledge:{asset.id}@rev{getattr(asset, 'revision', 1)}"


class PayableService:
    def __init__(
        self,
        directory: PayableDirectory,
        reader: InvoiceReader,
        knowledge,  # KnowledgeService — duck-typed to avoid an import cycle
        tasks: TaskService,
    ) -> None:
        self.directory = directory
        self.reader = reader
        self.knowledge = knowledge
        self.tasks = tasks

    def _known_vendor_slugs(self) -> list[str]:
        """The exemplar shelf doubles as the canonical vendor list."""
        try:
            assets = self.knowledge.list_assets(prefix="finance/exemplars")
        except Exception:
            return []
        prefix = "vendor-exemplar-"
        return sorted(
            a.id[len(prefix):] for a in assets if a.id.startswith(prefix)
        )

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
            if r.state == "posted":
                stats.posted += 1
                stats.posted_usd = round(stats.posted_usd + r.total_usd, 2)
            elif r.state == "pending":
                stats.pending += 1
            else:
                stats.denied += 1
            if r.extraction_path == "one-shot":
                stats.one_shot += 1
            else:
                stats.ensemble += 1
        return stats

    # -- the run ------------------------------------------------------------

    def read_policy(self) -> PostingPolicy:
        asset = self.knowledge.repo.get(POLICY_ASSET_ID)
        if asset is None:
            log.warning("Invoice posting policy asset missing; using defaults")
            return PostingPolicy(found_asset=False)
        return parse_posting_policy(asset.content)

    def submit(
        self,
        submitted_by: str,
        image_b64: str = "",
        filename: str = "",
        hint: str = "",
    ) -> InvoiceRecord:
        """One run of the accounts-payable flow, synchronously."""
        image = base64.b64decode(image_b64) if image_b64 else None
        rid = "inv_" + hashlib.sha256(
            f"{submitted_by}:{filename}:{utcnow().isoformat()}".encode()
        ).hexdigest()[:10]
        journal = [
            _event("t1", "InvoiceReceived", "trigger",
                   f"invoice submitted by {submitted_by}"
                   + (f" ({filename})" if filename else "")),
        ]

        # m0 · ModelCall — name the vendor before anything else is extracted.
        # The classifier is shown the slugs the knowledge layer already knows,
        # so "Acme Industrial Inc." resolves to the existing acme-industrial
        # exemplar instead of minting a near-duplicate slug. Real entity
        # resolution replaces this once the graph lands; until then the
        # exemplar shelf is the canonical vendor list.
        known = self._known_vendor_slugs()
        classify_hint = hint
        if known:
            classify_hint = (
                f"{hint}\nKnown vendor slugs (use the exact slug when this "
                f"vendor matches one): {', '.join(known)}"
            )
        classified = self.reader.read(
            image, filename, classify_hint, persona="classify", exemplar=None
        )
        slug = _canonicalize_slug(classified.vendor_slug, known)
        journal.append(_event(
            "m0", "classify-vendor", "modelcall",
            f"{classified.model}: {classified.vendor_name} ({slug}) · "
            f"confidence {classified.confidence:.2f}",
        ))

        # x1 · transform — look the vendor's exemplar up in the knowledge layer.
        exemplar_asset = self.knowledge.repo.get(_exemplar_asset_id(slug))
        exemplar_ref = _knowledge_ref(exemplar_asset) if exemplar_asset else ""
        journal.append(_event(
            "x1", "load-exemplar", "transform",
            f"{exemplar_ref} loaded" if exemplar_asset
            else f"no exemplar at knowledge:{_exemplar_asset_id(slug)}",
        ))

        # r1 · Rule — a known exemplar buys a single clean read.
        journal.append(_event(
            "r1", "exemplar-known", "rule",
            "one-shot: exemplar on file" if exemplar_asset
            else "ensemble: no exemplar — three independent extractions",
        ))

        record = InvoiceRecord(
            id=rid,
            submitted_by=submitted_by,
            submitted_at_iso=utcnow().isoformat(),
            filename=filename,
            vendor_slug=slug,
            vendor_name=classified.vendor_name,
            vendor_confidence=classified.confidence,
            exemplar_ref=exemplar_ref,
            journal=journal,
        )

        if exemplar_asset:
            # m1 · ModelCall — one extraction, templated by the exemplar.
            ext = self.reader.read(
                image, filename, hint, persona="oneshot",
                exemplar=exemplar_asset.content,
            )
            record.extraction_path = "one-shot"
            record.invoice_number = ext.invoice_number
            record.invoice_date = ext.invoice_date
            record.due_date = ext.due_date
            record.po_number = ext.po_number
            record.total_usd = ext.total_usd
            record.line_summary = ext.line_summary
            record.journal.append(_event(
                "m1", "extract-one-shot", "modelcall",
                f"{ext.model}: {ext.invoice_number} · ${ext.total_usd:,.2f} · "
                f"due {ext.due_date} · confidence {ext.confidence:.2f}",
            ))
            self._match_screen_and_route(record)
        else:
            # m2a/m2b/m2c · ModelCalls — three independent reads, then x2.
            candidates = []
            for step_id, persona in (("m2a", "a"), ("m2b", "b"), ("m2c", "c")):
                ext = self.reader.read(
                    image, filename, hint, persona=persona, exemplar=None
                )
                candidates.append(ext)
                record.journal.append(_event(
                    step_id, f"extract-{persona}", "modelcall",
                    f"{ext.model}: {ext.invoice_number} · ${ext.total_usd:,.2f} "
                    f"· {ext.invoice_date} · po {ext.po_number or '(empty)'}",
                ))
            adjudicated, conflicts = adjudicate(candidates)
            record.extraction_path = "ensemble"
            record.candidates = [c.model_dump() for c in candidates]
            record.conflicts = conflicts
            record.invoice_number = adjudicated["invoice_number"]
            record.invoice_date = adjudicated["invoice_date"]
            record.due_date = adjudicated["due_date"]
            record.po_number = adjudicated["po_number"]
            record.line_summary = adjudicated["line_summary"]
            record.total_usd = adjudicated["total_usd"]
            record.journal.append(_event(
                "x2", "adjudicate", "transform",
                f"field-by-field majority over 3 extractions · "
                f"{len(conflicts)} conflict(s)"
                + (f" — {conflicts[0]}" if conflicts else ""),
            ))

            # k1 · Task — the run suspends; a human verifies the exemplar.
            task = self._raise_verification(record, candidates, conflicts)
            record.task_id = task.id
            record.state = "pending"
            record.verdict_reason = (
                f"no exemplar for {slug}; adjudicated fields await verification"
            )
            record.journal.append(_event(
                "k1", "verify-exemplar", "task",
                f"suspended — task {task.id} raised to {CLERK_QUEUE}",
            ))

        self.directory.put(record)
        log.info(
            "Invoice submitted",
            extra={
                "invoiceId": rid, "state": record.state,
                "path": record.extraction_path,
                "amount": record.total_usd, "actor": submitted_by,
            },
        )
        return record

    def _match_screen_and_route(self, record: InvoiceRecord) -> None:
        """x3 + x4 (placeholders, and honest about it), then r2."""
        record.three_way_match = "placeholder"
        record.fraud_screen = "placeholder"
        record.journal.append(_event(
            "x3", "three-way-match", "transform", PLACEHOLDER_MATCH,
        ))
        record.journal.append(_event(
            "x4", "fraud-screen", "transform", PLACEHOLDER_FRAUD,
        ))

        # r2 · Rule — governed values, read from the policy asset at run time.
        policy = self.read_policy()
        record.policy_ref = policy.source_ref
        record.post_limit_usd = policy.limit_usd
        reasons = []
        if record.total_usd > policy.limit_usd:
            reasons.append(
                f"${record.total_usd:,.2f} exceeds the "
                f"${policy.limit_usd:,.2f} auto-post limit"
            )
        if record.vendor_confidence < policy.min_confidence:
            reasons.append(
                f"classification confidence {record.vendor_confidence:.2f} is "
                f"below {policy.min_confidence:.2f}"
            )
        record.journal.append(_event(
            "r2", "post-or-review", "rule",
            ("review: " + "; ".join(reasons)) if reasons
            else "post: within limit, confident classification",
        ))

        if reasons:
            # k2 · Task — the run suspends into the clerk queue.
            task = self._raise_review(record, reasons)
            record.task_id = task.id
            record.state = "pending"
            record.verdict_reason = "; ".join(reasons)
            record.journal.append(_event(
                "k2", "review-invoice", "task",
                f"suspended — task {task.id} raised to {CLERK_QUEUE}",
            ))
        else:
            record.state = "posted"
            record.verdict_reason = "auto-posted within governed policy"
            self._post(record)

    def _post(self, record: InvoiceRecord) -> None:
        record.journal.append(_event(
            "a1", "post-to-ap", "action",
            f"posted · committed via Action Gateway (ap ledger ap_{record.id[-6:]})",
        ))
        record.journal.append(_event(
            "e1", "InvoicePosted", "event", "emitted on commit",
        ))

    def _raise_verification(
        self,
        record: InvoiceRecord,
        candidates: list[InvoiceExtraction],
        conflicts: list[str],
    ) -> Task:
        models = " / ".join(c.model for c in candidates)
        evidence = [
            TaskEvidence(label="Vendor",
                         value=f"{record.vendor_name} ({record.vendor_slug})"),
            TaskEvidence(label="Adjudicated total",
                         value=f"${record.total_usd:,.2f}"),
        ]
        for field in ("invoice_number", "invoice_date", "due_date", "po_number"):
            evidence.append(TaskEvidence(
                label=f"{field} ({models})",
                value=" / ".join(getattr(c, field) or "(empty)" for c in candidates),
            ))
        evidence.append(TaskEvidence(
            label=f"total_usd ({models})",
            value=" / ".join(f"${c.total_usd:,.2f}" for c in candidates),
        ))
        evidence.append(TaskEvidence(
            label="Conflicts", value="; ".join(conflicts) if conflicts else "none",
        ))
        return self.tasks.create(Task(
            id=f"task_{record.id}_verify",
            kind="curation",
            title=f"Verify vendor exemplar: {record.vendor_name} "
                  f"{record.invoice_number}",
            summary=(
                f"First invoice seen from {record.vendor_name} — no exemplar "
                "on file, so three extractors read it independently and the "
                "adjudicated fields need a human's verification before they "
                "become the vendor's one-shot template."
            ),
            queue=CLERK_QUEUE,
            priority="normal",
            flow_id=FLOW_ID,
            case_ref=record.id,
            refs=[record.policy_ref],
            evidence=evidence,
            proposal=TaskProposal(
                action="save-exemplar",
                target="knowledge-layer",
                summary=f"Publish {_exemplar_asset_id(record.vendor_slug)} "
                        "from the verified fields",
            ),
            resumes="this run resumes at save-exemplar, then post-or-review",
            created_at_iso=utcnow().isoformat(),
        ))

    def _raise_review(self, record: InvoiceRecord, reasons: list[str]) -> Task:
        return self.tasks.create(Task(
            id=f"task_{record.id}_review",
            kind="decision",
            title=f"Invoice review: {record.vendor_name} "
                  f"${record.total_usd:,.2f}",
            summary=(
                "The post-or-review rule routed this invoice for review: "
                + "; ".join(reasons) + "."
            ),
            queue=CLERK_QUEUE,
            priority="high" if record.total_usd > record.post_limit_usd * 5
                     else "normal",
            flow_id=FLOW_ID,
            case_ref=record.id,
            refs=[record.policy_ref]
                 + ([record.exemplar_ref] if record.exemplar_ref else []),
            evidence=[
                TaskEvidence(label="Vendor", value=record.vendor_name),
                TaskEvidence(label="Invoice", value=record.invoice_number),
                TaskEvidence(label="Amount", value=f"${record.total_usd:,.2f}"),
                TaskEvidence(label="PO", value=record.po_number or "(none)"),
                TaskEvidence(label="Auto-post limit",
                             value=f"${record.post_limit_usd:,.2f} (governed)"),
            ],
            proposal=TaskProposal(
                action="post-to-ap",
                target="ap-ledger",
                summary=f"Post {record.id} to accounts payable",
            ),
            resumes="this run resumes at post-to-ap",
            created_at_iso=utcnow().isoformat(),
        ))

    # -- the resume ----------------------------------------------------------

    def apply_task_decision(self, task: Task) -> InvoiceRecord | None:
        """The human decided; the suspended run resumes from the Task step."""
        if not task.case_ref.startswith("inv_") or task.decision is None:
            return None
        record = self.directory.get(task.case_ref)
        if record is None or record.state != "pending" or record.task_id != task.id:
            return None
        if task.kind == "curation":
            return self._resume_verification(record, task)
        return self._resume_review(record, task)

    def _resume_verification(self, record: InvoiceRecord, task: Task) -> InvoiceRecord:
        """k1 decided: write the exemplar for real, then post-or-review."""
        verdict = task.decision.verdict
        record.decided_by = task.decision.decided_by
        record.decision_note = task.decision.note or task.decision.correction
        record.journal.append(_event(
            "k1", "verify-exemplar", "task",
            f"resumed — {verdict} by {task.decision.decided_by}",
        ))

        if verdict == "rejected":
            record.state = "denied"
            record.verdict_reason = "document rejected; no exemplar written"
            self.directory.put(record)
            log.info(
                "Invoice verification rejected",
                extra={"invoiceId": record.id, "taskId": task.id,
                       "actor": task.decision.decided_by},
            )
            return record

        # a2 · Action — the flywheel's write: the verified fields become the
        # vendor's one-shot template in the knowledge layer, for real.
        asset_id = _exemplar_asset_id(record.vendor_slug)
        content = self._exemplar_content(record, task)
        existing = self.knowledge.repo.get(asset_id)
        if existing is not None:
            asset = self.knowledge.update(
                asset_id, AssetUpdate(content=content, status="published")
            )
        else:
            asset = self.knowledge.create(AssetCreate(
                id=asset_id,
                path=f"finance/exemplars/{record.vendor_slug}",
                title=f"Vendor exemplar: {record.vendor_name}",
                content=content,
                authority_level="how-to",
                owner="finance-ops",
                tags=["exemplar", "ap", record.vendor_slug],
                status="published",
            ))
        record.exemplar_ref = _knowledge_ref(asset)
        record.journal.append(_event(
            "a2", "save-exemplar", "action",
            f"exemplar {record.exemplar_ref} published to the knowledge layer",
        ))

        # r2 · Rule — the verified total, against the governed values.
        policy = self.read_policy()
        record.policy_ref = policy.source_ref
        record.post_limit_usd = policy.limit_usd
        if record.total_usd > policy.limit_usd:
            reason = (
                f"${record.total_usd:,.2f} exceeds the "
                f"${policy.limit_usd:,.2f} auto-post limit"
            )
            record.journal.append(_event(
                "r2", "post-or-review", "rule", f"review: {reason}",
            ))
            review = self._raise_review(record, [reason])
            record.task_id = review.id
            record.verdict_reason = reason
            record.journal.append(_event(
                "k2", "review-invoice", "task",
                f"suspended — task {review.id} raised to {CLERK_QUEUE}",
            ))
        else:
            record.journal.append(_event(
                "r2", "post-or-review", "rule",
                "post: verified fields within the auto-post limit",
            ))
            record.state = "posted"
            record.verdict_reason = "posted after human verification"
            self._post(record)

        self.directory.put(record)
        log.info(
            "Invoice verification decided",
            extra={"invoiceId": record.id, "state": record.state,
                   "actor": task.decision.decided_by, "taskId": task.id},
        )
        return record

    def _resume_review(self, record: InvoiceRecord, task: Task) -> InvoiceRecord:
        """k2 decided: the clerk's judgment commits the state."""
        verdict = task.decision.verdict
        approved = verdict in ("approved", "corrected")
        record.decided_by = task.decision.decided_by
        record.decision_note = task.decision.note or task.decision.correction
        record.journal.append(_event(
            "k2", "review-invoice", "task",
            f"resumed — {verdict} by {task.decision.decided_by}",
        ))
        if approved:
            record.state = "posted"
            record.verdict_reason = (
                f"posted after review by {task.decision.decided_by}"
            )
            self._post(record)
        else:
            record.state = "denied"
            record.verdict_reason = (
                task.decision.note or "rejected in invoice review"
            )
        self.directory.put(record)
        log.info(
            "Invoice review decided",
            extra={"invoiceId": record.id, "state": record.state,
                   "actor": task.decision.decided_by, "taskId": task.id},
        )
        return record

    def _exemplar_content(self, record: InvoiceRecord, task: Task) -> str:
        """Render the verified fields as a one-shot extraction template."""
        lines = [
            f"# Vendor exemplar: {record.vendor_name}",
            "",
            f"One-shot extraction template for `{record.vendor_slug}` "
            "invoices, verified by a human from an adjudicated ensemble read.",
            "",
            f"- Invoice number format: `{record.invoice_number}` — match this "
            "pattern exactly",
            f"- Invoice date format: {record.invoice_date} (ISO 8601, header "
            "block)",
            f"- PO pattern: "
            + (f"`{record.po_number}`" if record.po_number
               else "no PO on this vendor's invoices"),
            "- Where the total sits: bottom-right summary block "
            f"(verified total ${record.total_usd:,.2f})",
            f"- Line summary style: {record.line_summary or 'one short phrase'}",
            "",
            f"Provenance: verified via task {task.id} on run {record.id} by "
            f"{task.decision.decided_by}.",
        ]
        if task.decision.verdict == "corrected":
            lines.append(
                "Correction applied by the verifier: "
                + (task.decision.correction or task.decision.note)
            )
        return "\n".join(lines)


def build_payable_service(
    backend: str,
    project: str | None,
    collection: str,
    reader: InvoiceReader,
    knowledge,
    tasks: TaskService,
) -> PayableService:
    directory: PayableDirectory
    if backend == "firestore":
        directory = FirestorePayableDirectory(project, collection)
    else:
        directory = InMemoryPayableDirectory()
    return PayableService(directory, reader, knowledge, tasks)


# ---------------------------------------------------------------------------
# Seed history — decided rows only; live submissions create the pending ones.
# ---------------------------------------------------------------------------


def _seed_rows() -> list[InvoiceRecord]:
    def row(
        rid, submitted_by, days_ago, slug, invoice_number, invoice_date,
        po_number, total, path, state, reason, *,
        task_id="", decided_by="", note="", review=None,
        conflicts=None, candidates=None,
    ) -> InvoiceRecord:
        vendor_name = _slug_to_name(slug)
        exemplar_ref = f"knowledge:{_exemplar_asset_id(slug)}@rev1"
        submitted = utcnow() - timedelta(days=days_ago)
        due = (date.fromisoformat(invoice_date) + timedelta(days=30)).isoformat()
        journal = [
            _event("t1", "InvoiceReceived", "trigger",
                   f"invoice submitted by {submitted_by}"),
            _event("m0", "classify-vendor", "modelcall",
                   f"invoice-classifier: {vendor_name} ({slug}) · "
                   "confidence 0.94"),
        ]
        if path == "one-shot":
            journal.append(_event("x1", "load-exemplar", "transform",
                                  f"{exemplar_ref} loaded"))
            journal.append(_event("r1", "exemplar-known", "rule",
                                  "one-shot: exemplar on file"))
            journal.append(_event(
                "m1", "extract-one-shot", "modelcall",
                f"invoice-extraction: {invoice_number} · ${total:,.2f} · "
                f"due {due} · confidence 0.97"))
        else:
            journal.append(_event(
                "x1", "load-exemplar", "transform",
                f"no exemplar at knowledge:{_exemplar_asset_id(slug)}"))
            journal.append(_event(
                "r1", "exemplar-known", "rule",
                "ensemble: no exemplar — three independent extractions"))
            for step_id, persona in (("m2a", "a"), ("m2b", "b"), ("m2c", "c")):
                journal.append(_event(
                    step_id, f"extract-{persona}", "modelcall",
                    f"invoice-extraction-{persona}: {invoice_number} · "
                    f"${total:,.2f}"))
            journal.append(_event(
                "x2", "adjudicate", "transform",
                f"field-by-field majority over 3 extractions · "
                f"{len(conflicts or [])} conflict(s)"))
            journal.append(_event(
                "k1", "verify-exemplar", "task",
                f"resumed — approved by {decided_by}"))
            journal.append(_event(
                "a2", "save-exemplar", "action",
                f"exemplar {exemplar_ref} published to the knowledge layer"))
        if path == "one-shot":
            journal.append(_event("x3", "three-way-match", "transform",
                                  PLACEHOLDER_MATCH))
            journal.append(_event("x4", "fraud-screen", "transform",
                                  PLACEHOLDER_FRAUD))
        if review is None:
            journal.append(_event(
                "r2", "post-or-review", "rule",
                "post: within limit, confident classification"))
        else:
            journal.append(_event("r2", "post-or-review", "rule",
                                  f"review: {reason}"))
            journal.append(_event(
                "k2", "review-invoice", "task",
                f"resumed — {review} by {decided_by}"))
        if state == "posted":
            journal.append(_event(
                "a1", "post-to-ap", "action",
                f"posted · committed via Action Gateway "
                f"(ap ledger ap_{rid[-6:]})"))
            journal.append(_event("e1", "InvoicePosted", "event",
                                  "emitted on commit"))
        return InvoiceRecord(
            id=rid,
            submitted_by=submitted_by,
            submitted_at_iso=submitted.isoformat(),
            vendor_slug=slug,
            vendor_name=vendor_name,
            vendor_confidence=0.94,
            exemplar_ref=exemplar_ref,
            extraction_path=path,
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            due_date=due,
            po_number=po_number,
            total_usd=total,
            line_summary="net-30 service invoice",
            conflicts=conflicts or [],
            candidates=candidates or [],
            state=state,
            verdict_reason=reason,
            post_limit_usd=2500.0,
            task_id=task_id,
            decided_by=decided_by,
            decision_note=note,
            journal=journal,
        )

    def cands(invoice_number, iso_date, po, total):
        us_date = "{1}/{2}/{0}".format(*iso_date.split("-"))
        return [
            {"model": "invoice-extraction-a", "invoice_number": invoice_number,
             "invoice_date": iso_date, "po_number": po, "total_usd": total},
            {"model": "invoice-extraction-b", "invoice_number": invoice_number,
             "invoice_date": us_date, "po_number": po, "total_usd": total},
            {"model": "invoice-extraction-c", "invoice_number": invoice_number,
             "invoice_date": iso_date, "po_number": "", "total_usd": total},
        ]

    return [
        row("inv_seed_01", "pat@customer.example", 18, "acme-industrial",
            "INV-2214", "2026-08-05", "PO-7712", 1840.00, "one-shot",
            "posted", "auto-posted within governed policy"),
        row("inv_seed_02", "kim@customer.example", 15, "northwind-paper",
            "NWP-88410", "2026-08-08", "PO-7716", 624.10, "one-shot",
            "posted", "auto-posted within governed policy"),
        row("inv_seed_03", "sam@customer.example", 13, "globex-logistics",
            "GLX-40071", "2026-08-10", "PO-7719", 1212.75, "ensemble",
            "posted", "posted after human verification",
            task_id="task_inv_seed_03_verify",
            decided_by="dana@customer.example",
            note="Fields match the paper invoice; exemplar looks right.",
            conflicts=["invoice_date: 2026-08-10 vs 08/10/2026 vs 2026-08-10 "
                       "— majority 2026-08-10"],
            candidates=cands("GLX-40071", "2026-08-10", "PO-7719", 1212.75)),
        row("inv_seed_04", "pat@customer.example", 10, "initech-supplies",
            "IT-55302", "2026-08-13", "PO-7723", 486.00, "ensemble",
            "posted", "posted after human verification",
            task_id="task_inv_seed_04_verify",
            decided_by="dana@customer.example",
            note="PO recovered from the c extractor's dropped field.",
            conflicts=["po_number: PO-7723 vs PO-7723 vs (empty) "
                       "— majority PO-7723"],
            candidates=cands("IT-55302", "2026-08-13", "PO-7723", 486.00)),
        row("inv_seed_05", "kim@customer.example", 7, "steelhaven-metals",
            "SHM-1190", "2026-08-16", "PO-7727", 2105.33, "one-shot",
            "posted", "auto-posted within governed policy"),
        row("inv_seed_06", "sam@customer.example", 4, "northwind-paper",
            "NWP-88512", "2026-08-19", "PO-7731", 6420.00, "one-shot",
            "denied", "duplicate of a paid invoice; vendor double-billed",
            task_id="task_inv_seed_06_review",
            decided_by="dana@customer.example",
            note="Duplicate of NWP-88410's contract period; do not pay.",
            review="rejected"),
        row("inv_seed_07", "pat@customer.example", 2, "acme-industrial",
            "INV-2287", "2026-08-21", "PO-7738", 4890.00, "one-shot",
            "posted", "$4,890.00 exceeds the $2,500.00 auto-post limit",
            task_id="task_inv_seed_07_review",
            decided_by="dana@customer.example",
            note="Annual maintenance contract; amount matches the PO.",
            review="approved"),
    ]


def seed_payables_if_empty(service: PayableService) -> int:
    if service.directory.list():
        return 0
    rows = _seed_rows()
    for r in rows:
        service.directory.put(r)
    log.info("Seeded payables table", extra={"count": len(rows)})
    return len(rows)
