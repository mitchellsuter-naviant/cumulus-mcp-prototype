"""The expense-reimbursement Flow: durable, multi-document, graph-writing.

What changed, and why. The first version of this flow read one receipt, ran a
rule over it, and either approved it or wrote a Task; resuming meant a second
function that hand-appended the rest of the journal. That shape cannot express
what an expense submission actually is:

- A submission is **N documents plus the submitter's own commentary** ("for the
  dinner receipt I'm only claiming $75 of the $110 — the rest was mine"). The
  commentary is a governed input to the run, read by the projection and quoted
  in the journal, not a decoration.
- A document is **not** a line. A compound receipt yields two lines; a reviewer
  may add a line the extraction missed. So documents are evidence and lines are
  facts, and they are separate objects with an edge between them.
- Review is **not one round**. A reviewer corrects an amount, the checks re-run
  against the corrected lines, and the run either finishes or asks again. That
  needs a real gate, several of them, and a journal to replay from — which is
  what `runs.RunEngine` provides.

The flow body below is the whole thing, and it reads as straight-line code
because the engine hides the replay. Steps:

  t1 trigger → s1 store-documents (Action, into the audit-documents
  Collection) → m{i} extract-receipt per document (ModelCall) → x1 read-policy
  (governed values, at the revision read) → x2 project-lines (documents +
  extractions + commentary → facts) → then a loop: r{n} needs-elevation (Rule)
  → k{n} elevate-review (gate) → x{n} apply-correction → back to the Rule →
  a1 record-expense (Action: writes the graph) → e1 ExpenseRecorded (Event).

Everything non-deterministic — model calls, id minting, clock reads — happens
inside a step, so a replay is exact.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import threading
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from . import data_collections
from .documents import DOCUMENT_COLLECTION, DocumentService, Region
from .graph import Claim, GraphService, GraphWriter
from .models import utcnow
from .runs import RunContext, RunEngine, RunRecord
from .tasks import Task, TaskEvidence, TaskProposal, TaskService

log = logging.getLogger("cumulus.reimbursements")

FLOW_ID = "expense-reimbursement"
POLICY_ASSET_ID = "expense-approval-policy"
REVIEW_QUEUE = "expense-review"
# Correction rounds before the flow stops asking and denies. A reviewer who
# has corrected the same request three times is telling us the submission is
# wrong, not that the checks are.
MAX_REVIEW_ROUNDS = 3

ReimbursementState = Literal["approved", "pending", "denied"]

# Why a request was elevated. Typed rather than prose because the three
# escalations need three different review screens: fixing an extraction is not
# the same job as supplying a missing purpose, which is not the same job as
# ruling on something out of policy.
EscalationKind = Literal[
    "extraction-uncertain",
    "over-limit",
    "restricted-category",
    "purpose-missing",
    "amount-mismatch",
]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ExtractedLine(BaseModel):
    """One candidate line a ModelCall read out of one document."""

    vendor: str = ""
    date: str = ""
    category: str = "other"
    amount_usd: float = 0.0
    description: str = ""
    confidence: float = 0.0


class ReceiptExtraction(BaseModel):
    """The ModelCall's output for one document.

    `lines` is plural because a compound receipt is normal, not exceptional —
    a dinner plus a taxi on one folio, a hotel bill with room and parking.
    `total_usd` stays as the document-level total so a single-line receipt
    reads the way it always did.
    """

    vendor: str = ""
    date: str = ""
    total_usd: float = 0.0
    category: str = "other"
    confidence: float = 0.0
    model: str = ""
    note: str = ""
    lines: list[ExtractedLine] = Field(default_factory=list)
    regions: list[Region] = Field(default_factory=list)


class ExpenseLine(BaseModel):
    """One reimbursable fact, with where it came from and who last touched it."""

    ordinal: int
    document_id: str = ""
    vendor: str = ""
    date: str = ""
    category: str = "other"
    description: str = ""
    # What the paper says, and what the employee is actually claiming. The
    # difference is the submitter's own split and is legitimate; the reverse
    # (claiming more than the paper supports) is always elevated.
    document_total_usd: float = 0.0
    requested_usd: float = 0.0
    purpose: str = ""
    confidence: float = 0.0
    origin: Literal["model", "human"] = "model"
    # Set when a reviewer changed or added this line.
    corrected_by: str = ""
    graph_ref: str = ""


class PolicyValues(BaseModel):
    """The governed values the Rule consumes — parsed from the policy asset.

    Parsed at run time, so a console edit takes effect on the next submission,
    and stamped with the revision actually read so a decision stays explainable
    against the document as it was.
    """

    limit_usd: float = 200.0
    min_confidence: float = 0.8
    restricted: list[str] = Field(
        default_factory=lambda: ["alcohol", "gifts", "entertainment"]
    )
    purpose_required_above_usd: float = 75.0
    requested_tolerance_usd: float = 0.5
    source_ref: str = f"knowledge:{POLICY_ASSET_ID}"
    found_asset: bool = False


class JournalEvent(BaseModel):
    """One executed step — same ids as the Tier-2 export's steps.

    Still here, and still the shape the console's run-trace reads, but now a
    *projection* of the run's steps rather than a second stored copy.
    """

    step_id: str
    name: str
    kind: str
    summary: str
    at_iso: str = ""


class SubmittedDocument(BaseModel):
    """One artifact in a submission, with the submitter's note about it.

    `note` is the human's own words about this document and is a real input to
    the run — the projection reads a stated claim out of it and the journal
    quotes it. `hint` is a local-dev steering string for the stub reader
    (`total=110 vendor=… compound`) and carries no meaning in a deployment with
    a real extractor; keeping them apart stops a demo knob from being mistaken
    for evidence.
    """

    content_b64: str = ""
    filename: str = ""
    note: str = ""
    hint: str = ""


class DocumentSlot(BaseModel):
    """A stored document as the run refers to it afterwards."""

    document_id: str
    filename: str = ""
    note: str = ""
    hint: str = ""
    byte_size: int = 0
    media_type: str = ""


class Escalation(BaseModel):
    kind: EscalationKind
    line_ordinal: int = 0
    detail: str = ""


class ReimbursementRequest(BaseModel):
    """The row. A projection of the run, kept for cheap reads and history."""

    id: str
    run_id: str = ""
    employee: str
    submitted_at_iso: str
    commentary: str = ""
    documents: list[DocumentSlot] = Field(default_factory=list)
    lines: list[ExpenseLine] = Field(default_factory=list)
    requested_usd: float = 0.0
    approved_usd: float = 0.0
    state: ReimbursementState = "pending"
    verdict_reason: str = ""
    escalations: list[Escalation] = Field(default_factory=list)
    review_rounds: int = 0
    human_override: bool = False
    policy_ref: str = f"knowledge:{POLICY_ASSET_ID}"
    policy_limit_usd: float = 0.0
    model: str = ""
    task_id: str = ""
    decided_by: str = ""
    decision_note: str = ""
    graph_ref: str = ""
    journal: list[JournalEvent] = Field(default_factory=list)

    # -- convenience projections, so a one-line request still reads simply ---

    @property
    def primary(self) -> ExpenseLine | None:
        return self.lines[0] if self.lines else None

    # Kept as fields (not properties) so they serialize into the REST payload
    # the console already reads.
    filename: str = ""
    vendor: str = ""
    date: str = ""
    total_usd: float = 0.0
    category: str = "other"
    confidence: float = 0.0

    def refresh_projections(self) -> "ReimbursementRequest":
        """Recompute the flat summary fields from the lines."""
        self.requested_usd = round(sum(line.requested_usd for line in self.lines), 2)
        primary = self.primary
        if primary is not None:
            self.vendor = primary.vendor
            self.date = primary.date
            self.category = primary.category
            self.confidence = min(line.confidence for line in self.lines)
        self.total_usd = self.requested_usd
        if self.documents and not self.filename:
            self.filename = self.documents[0].filename
        if self.state == "approved":
            self.approved_usd = self.requested_usd
        return self


class ReimbursementStats(BaseModel):
    total: int = 0
    approved: int = 0
    pending: int = 0
    denied: int = 0
    approved_usd: float = 0.0
    auto_approved: int = 0
    lines: int = 0
    human_corrected: int = 0


# ---------------------------------------------------------------------------
# Receipt readers (the ModelCall's engine)
# ---------------------------------------------------------------------------


class ReceiptReader(Protocol):
    def read(
        self, image: bytes | None, filename: str, hint: str
    ) -> ReceiptExtraction: ...


_CATEGORIES = ["meals", "travel", "lodging", "mileage", "supplies", "software"]
_VENDORS = [
    "corner-bistro",
    "transit-authority",
    "harbor-hotel",
    "office-depot",
    "cloudline-software",
    "airport-taxi",
]


def _regions_for(lines: list[ExtractedLine], total: float) -> list[Region]:
    """Plausible boxes for a stub extraction.

    Synthetic, and labelled as such by the reader's `note`. They exist so the
    review screen's box-drawing path is exercised end to end locally; a real
    reader returns real geometry.
    """
    regions = [
        Region(field="receipt.total_usd", page=1, x=0.58, y=0.82, width=0.30,
               height=0.06, text=f"{total:.2f}", confidence=0.9),
        Region(field="receipt.vendor", page=1, x=0.08, y=0.06, width=0.44,
               height=0.07, text=lines[0].vendor if lines else "", confidence=0.9),
    ]
    for i, line in enumerate(lines):
        regions.append(Region(
            field=f"receipt.lines[{i}].amount_usd", page=1, x=0.60,
            y=0.34 + 0.09 * i, width=0.26, height=0.06,
            text=f"{line.amount_usd:.2f}", confidence=line.confidence,
        ))
    return regions


class StubReceiptReader:
    """Deterministic local-dev extractor: no credential, no network.

    Steerable for demos. Honored hints, in the filename or the note:
    `total=64.20`, `vendor=corner-bistro`, `category=meals`, `blurry` (drops
    confidence below the policy floor), and `split=45.00,65.00` or the word
    `compound` (returns two lines from one document, which is the case that
    forces documents and facts apart). Anything else derives stable
    pseudo-values from a hash, so the same file always extracts the same way —
    which is also what makes a replayed step exact.
    """

    name = "stub"

    def read(self, image: bytes | None, filename: str, hint: str) -> ReceiptExtraction:
        text = f"{filename} {hint}".lower()
        seed = hashlib.sha256(
            (image or b"") + filename.encode() + hint.encode()
        ).digest()

        def pick(options: list[str], salt: int) -> str:
            return options[seed[salt] % len(options)]

        total_match = re.search(r"total[=\-_ ]?\$?(\d+(?:\.\d{1,2})?)", text)
        amount_match = total_match or re.search(r"\$(\d+(?:\.\d{1,2})?)", text)
        vendor_match = re.search(r"vendor[=\-_]([a-z0-9\-]+)", text)
        category_match = next(
            (c for c in _CATEGORIES + ["alcohol", "gifts", "entertainment"] if c in text),
            None,
        )
        split_match = re.search(r"split[=\-_]([\d.,]+)", text)

        total = (
            float(amount_match.group(1))
            if amount_match
            else round(12 + (int.from_bytes(seed[:2], "big") % 38800) / 100, 2)
        )
        confidence = 0.55 if "blurry" in text else 0.86 + (seed[2] % 12) / 100
        confidence = round(min(confidence, 0.99), 2)
        vendor = vendor_match.group(1) if vendor_match else pick(_VENDORS, 3)
        category = category_match or pick(_CATEGORIES, 4)
        date = utcnow().date().isoformat()

        amounts: list[float] = []
        if split_match:
            amounts = [
                float(part) for part in re.split(r"[,]", split_match.group(1)) if part
            ]
        elif "compound" in text:
            first = round(total * 0.6, 2)
            amounts = [first, round(total - first, 2)]
        if amounts:
            total = round(sum(amounts), 2)
        else:
            amounts = [total]

        lines = [
            ExtractedLine(
                vendor=vendor, date=date,
                category=category if i == 0 else pick(_CATEGORIES, 5),
                amount_usd=amount,
                description=f"line {i + 1} of {len(amounts)}" if len(amounts) > 1 else "",
                confidence=confidence,
            )
            for i, amount in enumerate(amounts)
        ]
        return ReceiptExtraction(
            vendor=vendor, date=date, total_usd=round(total, 2), category=category,
            confidence=confidence, model="stub-extractor",
            note="deterministic local extraction — no model configured; "
                 "regions are synthetic",
            lines=lines, regions=_regions_for(lines, total),
        )


class VertexReceiptReader:
    """Multimodal extraction on Vertex AI via google-genai.

    Same client and Application Default Credentials as the embedder and the
    answer backend, so enabling this adds no new secret. Asks for lines as well
    as a total: a compound receipt is the interesting case and the model can
    see it.
    """

    name = "vertex"

    def __init__(self, project: str | None, location: str, model: str) -> None:
        from google import genai

        self._client = genai.Client(vertexai=True, project=project, location=location)
        self._model = model

    def read(self, image: bytes | None, filename: str, hint: str) -> ReceiptExtraction:
        from google.genai import types

        parts: list = []
        if image:
            mime = "image/png" if filename.lower().endswith(".png") else "image/jpeg"
            parts.append(types.Part.from_bytes(data=image, mime_type=mime))
        parts.append(
            types.Part(
                text=(
                    "Extract from this receipt: vendor (lowercase slug), date "
                    "(ISO), total_usd (number), category (one of meals, travel, "
                    "lodging, mileage, supplies, software, alcohol, gifts, "
                    "entertainment, other), your confidence 0-1, and lines: an "
                    "array of the distinct charges on the receipt, each with "
                    "vendor, date, category, amount_usd and description. If the "
                    "receipt covers only one charge, return a single line. "
                    "Respond with only a JSON object with keys vendor, date, "
                    "total_usd, category, confidence, lines."
                    + (f" Submitter note: {hint}" if hint else "")
                )
            )
        )
        response = self._client.models.generate_content(
            model=self._model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.0
            ),
        )
        data = json.loads(response.text or "{}")
        total = float(data.get("total_usd", 0) or 0)
        confidence = float(data.get("confidence", 0) or 0)
        raw_lines = data.get("lines") or []
        lines = [
            ExtractedLine(
                vendor=str(item.get("vendor", data.get("vendor", ""))),
                date=str(item.get("date", data.get("date", ""))),
                category=str(item.get("category", data.get("category", "other"))),
                amount_usd=float(item.get("amount_usd", 0) or 0),
                description=str(item.get("description", "")),
                confidence=confidence,
            )
            for item in raw_lines
            if float(item.get("amount_usd", 0) or 0) > 0
        ]
        if not lines:
            lines = [ExtractedLine(
                vendor=str(data.get("vendor", "")), date=str(data.get("date", "")),
                category=str(data.get("category", "other")), amount_usd=total,
                confidence=confidence,
            )]
        return ReceiptExtraction(
            vendor=str(data.get("vendor", "")),
            date=str(data.get("date", "")),
            total_usd=total or round(sum(line.amount_usd for line in lines), 2),
            category=str(data.get("category", "other")),
            confidence=confidence,
            model=self._model,
            lines=lines,
        )


def build_receipt_reader(
    backend: str, project: str | None, location: str, model: str
) -> ReceiptReader:
    if backend == "vertex":
        return VertexReceiptReader(project, location, model)
    return StubReceiptReader()


# ---------------------------------------------------------------------------
# Policy parsing (the governed knowledge read)
# ---------------------------------------------------------------------------


def parse_policy(content: str) -> PolicyValues:
    values = PolicyValues(found_asset=True)
    limit = re.search(
        r"auto-approval limit:?\s*\$([\d,]+(?:\.\d{1,2})?)", content, re.IGNORECASE
    )
    if limit:
        values.limit_usd = float(limit.group(1).replace(",", ""))
    confidence = re.search(
        r"minimum extraction confidence:?\s*(\d+(?:\.\d+)?)", content, re.IGNORECASE
    )
    if confidence:
        values.min_confidence = float(confidence.group(1))
    restricted = re.search(
        r"restricted categories:?\s*([a-z, ]+)", content, re.IGNORECASE
    )
    if restricted:
        values.restricted = [
            c.strip() for c in restricted.group(1).lower().split(",") if c.strip()
        ]
    purpose = re.search(
        r"business purpose required above:?\s*\$([\d,]+(?:\.\d{1,2})?)",
        content, re.IGNORECASE,
    )
    if purpose:
        values.purpose_required_above_usd = float(purpose.group(1).replace(",", ""))
    tolerance = re.search(
        r"requested amount tolerance:?\s*\$([\d,]+(?:\.\d{1,2})?)",
        content, re.IGNORECASE,
    )
    if tolerance:
        values.requested_tolerance_usd = float(tolerance.group(1).replace(",", ""))
    return values


# ---------------------------------------------------------------------------
# Commentary parsing — the submitter's own words as a governed input
# ---------------------------------------------------------------------------

_CLAIM_PATTERNS = (
    r"(?:only\s+)?(?:request|requesting|claim|claiming|reimburse|reimbursement of)"
    r"\s*(?:for\s*)?\$?(\d+(?:\.\d{1,2})?)",
    r"\$?(\d+(?:\.\d{1,2})?)\s*(?:of|out of)\s*(?:the\s*)?\$?\d+(?:\.\d{1,2})?",
)


def parse_requested_amount(note: str) -> float | None:
    """Pull an explicit claim out of the submitter's note.

    Deliberately narrow. "I'm only requesting $75 of the $110" is a specific,
    common, checkable sentence; anything vaguer is left alone and the document
    total stands. A wrong guess here would silently reduce someone's
    reimbursement, so the bar for reading one is high and the journal always
    records that the number came from the note.
    """
    text = (note or "").lower()
    for pattern in _CLAIM_PATTERNS:
        match = re.search(pattern, text)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return None


_PURPOSE_HINTS = (
    "for ", "purpose", "client", "customer", "meeting", "conference", "onsite",
    "interview", "team ", "offsite", "training", "vendor", "recruiting",
)


def looks_like_purpose(text: str) -> bool:
    """Whether a note plausibly states *why* the expense happened.

    A keyword test, and it says so. It is enough to tell "dinner with the Acme
    team after the Phase 2 review" from "receipt attached", which is the
    distinction the purpose check needs; it is not enough to judge the quality
    of a stated purpose, and does not try.
    """
    lowered = (text or "").strip().lower()
    if len(lowered) < 12:
        return False
    return any(hint in lowered for hint in _PURPOSE_HINTS)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class ReimbursementDirectory(Protocol):
    def list(self) -> list[ReimbursementRequest]: ...
    def get(self, request_id: str) -> ReimbursementRequest | None: ...
    def put(self, request: ReimbursementRequest) -> ReimbursementRequest: ...


class InMemoryReimbursementDirectory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, ReimbursementRequest] = {}

    def list(self) -> list[ReimbursementRequest]:
        with self._lock:
            return list(self._items.values())

    def get(self, request_id: str) -> ReimbursementRequest | None:
        with self._lock:
            return self._items.get(request_id)

    def put(self, request: ReimbursementRequest) -> ReimbursementRequest:
        with self._lock:
            self._items[request.id] = request
            return request


class FirestoreReimbursementDirectory:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore

        self._client = firestore.Client(project=project)
        self._col = self._client.collection(collection)

    def list(self) -> list[ReimbursementRequest]:
        out = []
        for doc in self._col.stream():
            try:
                out.append(ReimbursementRequest.model_validate(doc.to_dict()))
            except Exception:
                log.exception(
                    "Skipping unparseable reimbursement", extra={"docId": doc.id}
                )
        return out

    def get(self, request_id: str) -> ReimbursementRequest | None:
        snap = self._col.document(request_id).get()
        if not snap.exists:
            return None
        try:
            return ReimbursementRequest.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable reimbursement", extra={"id": request_id})
            return None

    def put(self, request: ReimbursementRequest) -> ReimbursementRequest:
        self._col.document(request.id).set(request.model_dump(mode="json"))
        return request


def _event(step_id: str, name: str, kind: str, summary: str) -> JournalEvent:
    """Kept for callers that build journal entries outside a run."""
    return JournalEvent(
        step_id=step_id, name=name, kind=kind, summary=summary,
        at_iso=utcnow().isoformat(),
    )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ReimbursementService:
    """Owns the flow body, the row projection, and the graph writes."""

    def __init__(
        self,
        directory: ReimbursementDirectory,
        reader: ReceiptReader,
        knowledge,  # KnowledgeService — duck-typed to avoid an import cycle
        tasks: TaskService,
        engine: RunEngine | None = None,
        graph: GraphService | None = None,
        documents: DocumentService | None = None,
    ) -> None:
        self.directory = directory
        self.reader = reader
        self.knowledge = knowledge
        self.tasks = tasks
        self.engine = engine
        self.graph = graph
        self.documents = documents
        if engine is not None:
            engine.register(FLOW_ID, self._body)

    # -- reads --------------------------------------------------------------

    def list(
        self, state: ReimbursementState | None = None, employee: str | None = None
    ) -> list[ReimbursementRequest]:
        items = self.directory.list()
        if state is not None:
            items = [r for r in items if r.state == state]
        if employee is not None:
            items = [r for r in items if r.employee == employee]
        return sorted(items, key=lambda r: r.submitted_at_iso, reverse=True)

    def get(self, request_id: str) -> ReimbursementRequest | None:
        return self.directory.get(request_id)

    def stats(self) -> ReimbursementStats:
        stats = ReimbursementStats()
        for r in self.directory.list():
            stats.total += 1
            stats.lines += len(r.lines)
            stats.human_corrected += sum(1 for line in r.lines if line.corrected_by)
            if r.state == "approved":
                stats.approved += 1
                stats.approved_usd = round(stats.approved_usd + r.requested_usd, 2)
                if not r.task_id:
                    stats.auto_approved += 1
            elif r.state == "pending":
                stats.pending += 1
            else:
                stats.denied += 1
        return stats

    def read_policy(self) -> PolicyValues:
        asset = self.knowledge.repo.get(POLICY_ASSET_ID)
        if asset is None:
            log.warning("Expense policy asset missing; using defaults")
            return PolicyValues(found_asset=False)
        values = parse_policy(asset.content)
        values.source_ref = (
            f"knowledge:{POLICY_ASSET_ID}@rev{getattr(asset, 'revision', 1)}"
        )
        return values

    # -- the entry point ----------------------------------------------------

    def submit(
        self,
        employee: str,
        documents: list[SubmittedDocument] | None = None,
        commentary: str = "",
        *,
        image_b64: str = "",
        filename: str = "",
        hint: str = "",
        run_id: str | None = None,
        submitted_at_iso: str = "",
    ) -> ReimbursementRequest:
        """Start one run of the flow. Returns the row the run produced.

        The single-document arguments (`image_b64`, `filename`, `hint`) are
        still accepted: a one-receipt submission is the common case and there
        is no reason to make a caller wrap it in a list.
        """
        docs = [
            d if isinstance(d, SubmittedDocument) else SubmittedDocument.model_validate(d)
            for d in (documents or [])
        ]
        if not docs and (image_b64 or filename or hint):
            docs = [SubmittedDocument(
                content_b64=image_b64, filename=filename, note=hint
            )]
        if not docs:
            docs = [SubmittedDocument()]

        inputs = {
            "employee": employee,
            "commentary": commentary,
            "submitted_at_iso": submitted_at_iso or utcnow().isoformat(),
            "documents": [d.model_dump() for d in docs],
        }
        if self.engine is None:
            raise RuntimeError("Reimbursement service has no run engine wired")
        record = self.engine.start(
            FLOW_ID, inputs, actor=employee, run_id=run_id
        )
        return self._project(record)

    def resume_for_task(self, task: Task) -> ReimbursementRequest | None:
        """A decided Task releases the gate its run is suspended on."""
        if task.flow_id != FLOW_ID or self.engine is None:
            return None
        record = self.engine.resume_for_task(task.id)
        return self._project(record) if record else None

    # Kept under the old name: tasks_api calls it after a decision.
    def apply_task_decision(self, task: Task) -> ReimbursementRequest | None:
        return self.resume_for_task(task)

    # -- the flow body ------------------------------------------------------

    def _body(self, ctx: RunContext, inputs: dict) -> dict:
        employee = inputs["employee"]
        commentary = inputs.get("commentary", "")
        submitted_at = inputs.get("submitted_at_iso") or utcnow().isoformat()
        submitted = [SubmittedDocument.model_validate(d) for d in inputs["documents"]]

        ctx.event(
            "t1", "ExpenseSubmitted",
            f"{len(submitted)} document(s) submitted by {employee}"
            + (f" · commentary: \"{_clip(commentary)}\"" if commentary else ""),
            kind="trigger",
        )

        # s1 · Action — the artifacts land in the audit-documents Collection,
        # which is what stamps their two-year retention.
        slots = ctx.step(
            "s1", "store-documents", "action",
            fn=lambda: [
                slot.model_dump()
                for slot in self._store_documents(submitted, employee, ctx.record.id)
            ],
            writes=lambda out: [
                f"document:{slot['document_id']}" for slot in (out or [])
            ],
            summary=lambda out: (
                f"{len(out)} document(s) stored in "
                f"`{DOCUMENT_COLLECTION}` · retention "
                f"{data_collections.get(DOCUMENT_COLLECTION).retention}"
                if out else "no documents attached (hint-driven run)"
            ),
        )
        slot_models = [DocumentSlot.model_validate(s) for s in slots]

        # m{i} · ModelCall — one step per document, so a retry, a correction or
        # a cost lands on the document it belongs to.
        extractions: list[ReceiptExtraction] = []
        for index, (slot, doc) in enumerate(zip(slot_models, submitted), start=1):
            raw = ctx.step(
                f"m{index}", f"extract-receipt[{index}]", "modelcall",
                fn=lambda d=doc, s=slot: self._extract(d, s).model_dump(),
                summary=lambda out: (
                    f"{out['model']}: {out['vendor']} · ${out['total_usd']:,.2f} · "
                    f"{out['category']} · {len(out['lines'])} line(s) · "
                    f"confidence {out['confidence']:.2f}"
                ),
                model=getattr(self.reader, "name", ""),
                refs=[f"document:{slot.document_id}"],
                retries=1,
                escalate_queue=REVIEW_QUEUE,
            )
            extractions.append(ReceiptExtraction.model_validate(raw))

        # x1 · transform — the governed read. The Rule never fetches.
        policy_raw = ctx.step(
            "x1", "read-policy", "transform",
            fn=lambda: self.read_policy().model_dump(),
            summary=lambda out: (
                f"{out['source_ref']}: limit ${out['limit_usd']:,.2f} · "
                f"min confidence {out['min_confidence']:.2f} · "
                f"purpose above ${out['purpose_required_above_usd']:,.2f} · "
                f"restricted {', '.join(out['restricted'])}"
            ),
        )
        policy = PolicyValues.model_validate(policy_raw)
        ctx.note_refs(policy.source_ref)

        # x2 · transform — documents + extractions + commentary become facts.
        lines_raw = ctx.step(
            "x2", "project-lines", "transform",
            fn=lambda: [
                line.model_dump()
                for line in project_lines(slot_models, extractions, commentary)
            ],
            summary=lambda out: (
                f"{len(out)} line(s) projected · requested "
                f"${sum(line['requested_usd'] for line in out):,.2f}"
                + (
                    " · submitter's own split honored"
                    if any(
                        line["requested_usd"] < line["document_total_usd"] - 0.001
                        for line in out
                    )
                    else ""
                )
            ),
        )
        lines = [ExpenseLine.model_validate(line) for line in lines_raw]

        # The review loop. Each round is its own Rule step and its own gate, so
        # the journal shows exactly how many times a person was asked.
        state: ReimbursementState = "approved"
        reason = "auto-approved within governed policy"
        escalations: list[Escalation] = []
        override = False
        decided_by = ""
        decision_note = ""
        rounds = 0

        for round_number in range(1, MAX_REVIEW_ROUNDS + 1):
            checked = ctx.step(
                f"r{round_number}", "needs-elevation", "rule",
                fn=lambda ln=lines: _check(ln, policy),
                summary=lambda out: (
                    "elevate: " + "; ".join(e["detail"] for e in out["escalations"])
                    if out["escalations"]
                    else "approve: within limit, allowed category, confident "
                         "extraction, purpose stated"
                ),
                refs=[policy.source_ref],
            )
            escalations = [Escalation.model_validate(e) for e in checked["escalations"]]
            rounds = round_number
            if not escalations:
                if round_number > 1:
                    reason = (
                        f"approved after {round_number - 1} review round(s) — "
                        f"corrected by {decided_by}"
                    )
                break

            decision = ctx.gate(
                f"k{round_number}", "elevate-review",
                build_task=lambda rn=round_number, esc=escalations, ln=lines: (
                    self._build_review_task(ctx.record, ln, esc, policy, rn, employee)
                ),
            )
            decided_by = decision.decided_by
            decision_note = decision.note or decision.correction

            if decision.verdict == "rejected":
                state = "denied"
                reason = decision.note or "denied on review"
                break
            if decision.verdict == "approved":
                # The reviewer looked at the flags and accepted them. Re-running
                # the Rule would raise the same flags forever, so the override
                # is recorded and the loop ends.
                override = True
                state = "approved"
                reason = (
                    "approved on review despite: "
                    + "; ".join(e.detail for e in escalations)
                )
                break

            patched = ctx.step(
                f"x{2 + round_number}", "apply-correction", "transform",
                fn=lambda ln=lines, d=decision: [
                    line.model_dump()
                    for line in apply_correction(ln, d.payload, d.decided_by)
                ],
                summary=lambda out: (
                    f"reviewer supplied {len(out)} line(s) — re-checking against "
                    "the same governed values"
                ),
            )
            lines = [ExpenseLine.model_validate(line) for line in patched]
        else:
            # Fell out of the loop still flagged: stop asking.
            state = "denied"
            reason = (
                f"unresolved after {MAX_REVIEW_ROUNDS} review rounds: "
                + "; ".join(e.detail for e in escalations)
            )

        # a1 · Action — the graph write, through the Gateway.
        request = ReimbursementRequest(
            id=ctx.record.case_ref or ctx.record.id,
            run_id=ctx.record.id,
            employee=employee,
            submitted_at_iso=submitted_at,
            commentary=commentary,
            documents=slot_models,
            lines=lines,
            state=state,
            verdict_reason=reason,
            escalations=escalations if state != "approved" or override else [],
            review_rounds=rounds - 1 if rounds else 0,
            human_override=override,
            policy_ref=policy.source_ref,
            policy_limit_usd=policy.limit_usd,
            model=extractions[0].model if extractions else "",
            task_id=next(
                (s.task_id for s in ctx.record.steps if s.task_id), ""
            ),
            decided_by=decided_by,
            decision_note=decision_note,
        ).refresh_projections()

        writer = self.graph.writer(ctx.record.id, "a1") if self.graph else None
        written = ctx.step(
            "a1", "record-expense", "action",
            fn=lambda: self._write_graph(request, policy, decided_by, writer),
            writes=lambda _out: writer.refs if writer is not None else [],
            summary=lambda out: (
                f"{request.state} · ${request.requested_usd:,.2f} · "
                f"{out['objects']} object(s) and {out['links']} link(s) written "
                f"· ledger led_{request.id[-6:]}"
            ),
        )
        request.graph_ref = written.get("request_ref", "")
        for line, ref in zip(request.lines, written.get("line_refs", [])):
            line.graph_ref = ref
        request.journal = [JournalEvent.model_validate(e) for e in ctx.record.journal()]
        self.directory.put(request)

        ctx.event("e1", "ExpenseRecorded", f"emitted on commit · {request.state}")
        request.journal = [JournalEvent.model_validate(e) for e in ctx.record.journal()]
        self.directory.put(request)

        log.info(
            "Expense request recorded",
            extra={
                "reimbursementId": request.id, "runId": ctx.record.id,
                "state": request.state, "amount": request.requested_usd,
                "lines": len(request.lines), "actor": employee,
                "reviewRounds": request.review_rounds,
            },
        )
        return {
            "request_id": request.id,
            "state": request.state,
            "requested_usd": request.requested_usd,
            "lines": len(request.lines),
        }

    # -- steps' internals ---------------------------------------------------

    def _store_documents(
        self, submitted: list[SubmittedDocument], employee: str, run_id: str
    ) -> list[DocumentSlot]:
        slots: list[DocumentSlot] = []
        for index, doc in enumerate(submitted, start=1):
            if self.documents is None:
                # No document store wired (a unit test, or a hint-only run):
                # the slot still exists so the rest of the flow is unchanged.
                slots.append(DocumentSlot(
                    document_id=f"doc_inline_{index}",
                    filename=doc.filename, note=doc.note, hint=doc.hint,
                ))
                continue
            record = self.documents.put_bytes(
                doc.content_b64, filename=doc.filename, kind="receipt",
                uploaded_by=employee, run_id=run_id,
            )
            slots.append(DocumentSlot(
                document_id=record.id, filename=doc.filename or record.filename,
                note=doc.note, hint=doc.hint, byte_size=record.byte_size,
                media_type=record.media_type,
            ))
        return slots

    def _extract(self, doc: SubmittedDocument, slot: DocumentSlot) -> ReceiptExtraction:
        image = base64.b64decode(doc.content_b64) if doc.content_b64 else None
        # The reader sees the submitter's note as well as the hint: with a real
        # multimodal extractor the note is genuinely useful context ("the wine
        # was mine"), and with the stub it is the hint that steers.
        extraction = self.reader.read(
            image, doc.filename, f"{doc.hint} {doc.note}".strip()
        )
        if self.documents is not None and extraction.regions:
            self.documents.set_regions(slot.document_id, extraction.regions)
        return extraction

    def _build_review_task(
        self,
        record: RunRecord,
        lines: list[ExpenseLine],
        escalations: list[Escalation],
        policy: PolicyValues,
        round_number: int,
        employee: str,
    ) -> Task:
        kinds = sorted({e.kind for e in escalations})
        total = round(sum(line.requested_usd for line in lines), 2)
        title_vendor = lines[0].vendor if lines else "expense"
        return Task(
            id=f"task_{record.id}_k{round_number}",
            kind="decision",
            title=f"Elevated reimbursement: {title_vendor} ${total:,.2f}",
            summary=(
                "The needs-elevation rule routed this submission for review: "
                + "; ".join(e.detail for e in escalations)
                + ". The reviewer may correct amounts, split a document into "
                "several lines, add a missing line, or supply a purpose."
            ),
            queue=REVIEW_QUEUE,
            priority="high" if total > policy.limit_usd * 5 else "normal",
            flow_id=FLOW_ID,
            run_id=record.id,
            case_ref=record.case_ref or record.id,
            refs=[policy.source_ref]
            + [f"document:{line.document_id}" for line in lines if line.document_id],
            evidence=[
                TaskEvidence(label="Submitted by", value=employee),
                TaskEvidence(label="Lines", value=str(len(lines))),
                TaskEvidence(label="Requested", value=f"${total:,.2f}"),
                TaskEvidence(
                    label="Document total",
                    value=f"${sum(line.document_total_usd for line in lines):,.2f}",
                ),
                TaskEvidence(label="Why elevated", value=", ".join(kinds)),
                TaskEvidence(
                    label="Auto-approval limit",
                    value=f"${policy.limit_usd:,.2f} (governed)",
                ),
                TaskEvidence(label="Review round", value=str(round_number)),
            ],
            proposal=TaskProposal(
                action="record-expense",
                target="expense-records",
                summary=f"Record {record.case_ref or record.id} as approved",
            ),
            resumes=f"this run resumes at k{round_number} and re-runs the rule",
            created_at_iso=utcnow().isoformat(),
        )

    def _write_graph(
        self,
        request: ReimbursementRequest,
        policy: PolicyValues,
        decided_by: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        """Write the entities. Idempotent: natural keys resolve to one uid.

        This is the step that makes "how much has this employee submitted in
        six months" answerable — not by scanning a table, but by walking one
        edge from the Employee object.
        """
        if self.graph is None:
            return {"objects": 0, "links": 0, "request_ref": "", "line_refs": []}
        # Every write goes through the step-bound writer, so the claims and
        # links it makes name the run and step that made them.
        graph = writer if writer is not None else self.graph

        source = f"flow:{FLOW_ID}"
        employee_obj = graph.upsert(
            "Employee", request.employee,
            [Claim(field="email", value=request.employee, source=source)],
            label=request.employee, provenance=f"seen submitting {request.id}",
        )
        request_obj = graph.upsert(
            "ExpenseRequest", request.run_id or request.id,
            [
                Claim(field="submitted_at", value=request.submitted_at_iso, source=source),
                Claim(field="commentary", value=request.commentary, source=source),
                Claim(field="requested_usd", value=request.requested_usd, source=source),
                Claim(
                    field="approved_usd",
                    value=request.requested_usd if request.state == "approved" else 0.0,
                    source=source,
                ),
                Claim(field="state", value=request.state, source=source),
                Claim(field="employee_email", value=request.employee, source=source),
            ],
            label=f"{request.vendor} ${request.requested_usd:,.2f}",
            provenance=f"run {request.run_id}",
        )
        objects = 2
        links = 0
        graph.link(
            "submitted", employee_obj.id, request_obj.id,
            props={"at": request.submitted_at_iso}, provenance=request.run_id,
        )
        links += 1

        line_refs: list[str] = []
        for line in request.lines:
            # A human-corrected value is a claim from `human:…`, which outranks
            # the model's claim under the ontology's precedence — the model's
            # original reading is still on the record.
            line_source = (
                f"human:{line.corrected_by}" if line.corrected_by
                else f"model:{request.model or 'extractor'}"
            )
            line_obj = graph.upsert(
                # Keyed off the run rather than the request object's generated
                # id, so a person — or an eval expectation — can write it.
                "ExpenseLine", f"{request.run_id or request.id}#{line.ordinal}",
                [
                    Claim(field="vendor", value=line.vendor, source=line_source),
                    Claim(field="date", value=line.date, source=line_source),
                    Claim(field="category", value=line.category, source=line_source),
                    Claim(
                        field="document_total_usd", value=line.document_total_usd,
                        source=line_source, confidence=line.confidence,
                    ),
                    Claim(
                        field="requested_usd", value=line.requested_usd,
                        source=line_source, confidence=line.confidence,
                    ),
                    Claim(field="purpose", value=line.purpose, source=line_source),
                    Claim(field="origin", value=line.origin, source=line_source),
                ],
                label=f"{line.vendor} ${line.requested_usd:,.2f}",
                provenance=f"run {request.run_id} line {line.ordinal}",
            )
            objects += 1
            line_refs.append(line_obj.ref())
            graph.link(
                "has_expense_line", request_obj.id, line_obj.id,
                props={"ordinal": line.ordinal}, provenance=request.run_id,
            )
            links += 1
            if line.document_id:
                document_obj = graph.upsert(
                    "Document", line.document_id,
                    [
                        Claim(field="kind", value="receipt", source=source),
                        Claim(
                            field="filename",
                            value=next(
                                (
                                    d.filename for d in request.documents
                                    if d.document_id == line.document_id
                                ),
                                "",
                            ),
                            source=source,
                        ),
                    ],
                    label=line.document_id,
                    provenance=f"submitted with {request.id}",
                )
                objects += 1
                graph.link(
                    "evidenced_by", line_obj.id, document_obj.id,
                    method="derived" if line.origin == "model" else "human",
                    confidence=line.confidence or 1.0,
                    provenance=request.run_id,
                )
                links += 1

        if decided_by:
            decider = graph.upsert(
                "Employee", decided_by,
                [Claim(field="email", value=decided_by, source=source)],
                label=decided_by, provenance=f"decided {request.id}",
            )
            objects += 1
            graph.link(
                "decided_by", request_obj.id, decider.id,
                props={"verdict": request.state}, method="human",
                provenance=request.run_id,
            )
            links += 1

        return {
            "objects": objects,
            "links": links,
            "request_ref": request_obj.ref(),
            "line_refs": line_refs,
        }

    # -- projection ---------------------------------------------------------

    def _project(self, record: RunRecord) -> ReimbursementRequest:
        """The row for a run, whatever state the run is in.

        A suspended run has no `a1` step yet, so the row is assembled from what
        the journal already holds. That is the honest reading: the row is a
        view of the run, not a separate truth.
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
        stored = record.step("s1")
        slots = [
            DocumentSlot.model_validate(s) for s in (stored.output or [])
        ] if stored else []

        # The newest projection of the lines wins: after a correction the facts
        # live in the round's `apply-correction` step, and showing the original
        # extraction would tell a reviewer their edit vanished.
        lines: list[ExpenseLine] = []
        for step_id in [
            f"x{2 + n}" for n in range(MAX_REVIEW_ROUNDS, 0, -1)
        ] + ["x2"]:
            step = record.step(step_id)
            if step is not None and step.output:
                lines = [ExpenseLine.model_validate(line) for line in step.output]
                break

        # Likewise the newest Rule pass: an escalation the reviewer already
        # resolved is not why the run is still waiting.
        escalations: list[Escalation] = []
        for step in reversed(record.steps):
            if step.kind == "rule" and isinstance(step.output, dict):
                escalations = [
                    Escalation.model_validate(e)
                    for e in step.output.get("escalations", [])
                ]
                break
        decided_gates = [
            s for s in record.steps if s.kind == "task" and s.decision
        ]
        policy_step = record.step("x1")
        policy = (
            PolicyValues.model_validate(policy_step.output)
            if policy_step and policy_step.output else PolicyValues()
        )
        state: ReimbursementState = (
            "pending" if record.status in ("running", "suspended")
            else ("denied" if record.status == "failed" else "approved")
        )
        request = ReimbursementRequest(
            id=record.case_ref or record.id,
            run_id=record.id,
            employee=inputs.get("employee", ""),
            submitted_at_iso=inputs.get("submitted_at_iso", record.started_at_iso),
            commentary=inputs.get("commentary", ""),
            documents=slots,
            lines=lines,
            state=state,
            verdict_reason=(
                "; ".join(e.detail for e in escalations)
                if escalations else (record.error or "in flight")
            ),
            escalations=escalations,
            review_rounds=len(decided_gates),
            decided_by=(
                decided_gates[-1].decision or {}
            ).get("decided_by", "") if decided_gates else "",
            decision_note=(
                decided_gates[-1].decision or {}
            ).get("note", "") if decided_gates else "",
            policy_ref=policy.source_ref,
            policy_limit_usd=policy.limit_usd,
            task_id=record.open_task_id
            or next(
                (s.task_id for s in reversed(record.steps) if s.task_id), ""
            ),
            journal=[JournalEvent.model_validate(e) for e in record.journal()],
        ).refresh_projections()
        return self.directory.put(request)


def _clip(text: str, limit: int = 90) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# Pure transforms — no I/O, so they are testable and replay-safe
# ---------------------------------------------------------------------------


def project_lines(
    slots: list[DocumentSlot],
    extractions: list[ReceiptExtraction],
    commentary: str,
) -> list[ExpenseLine]:
    """Documents + extractions + the submitter's words become facts.

    The two interesting moves:

    - One document with two charges becomes two lines. That is why this is a
      projection and not a rename of the extraction.
    - The submitter's note can lower what a line claims ("only requesting $75
      of the $110"), and the note or the overall commentary can supply the
      purpose. Neither can raise a claim above what the document says — that
      is a check, and it belongs to the Rule.
    """
    lines: list[ExpenseLine] = []
    ordinal = 0
    for slot, extraction in zip(slots, extractions):
        candidates = extraction.lines or [ExtractedLine(
            vendor=extraction.vendor, date=extraction.date,
            category=extraction.category, amount_usd=extraction.total_usd,
            confidence=extraction.confidence,
        )]
        note_claim = parse_requested_amount(slot.note)
        purpose = ""
        for text in (slot.note, commentary):
            if looks_like_purpose(text):
                purpose = text.strip()
                break
        for index, candidate in enumerate(candidates):
            ordinal += 1
            document_total = round(candidate.amount_usd, 2)
            requested = document_total
            # A stated claim applies to a single-line document. On a compound
            # receipt the note cannot say which charge it means, so the split
            # is left to the reviewer rather than guessed at.
            if note_claim is not None and len(candidates) == 1:
                requested = round(note_claim, 2)
            lines.append(ExpenseLine(
                ordinal=ordinal,
                document_id=slot.document_id,
                vendor=candidate.vendor or extraction.vendor,
                date=candidate.date or extraction.date,
                category=candidate.category,
                description=candidate.description,
                document_total_usd=document_total,
                requested_usd=requested,
                purpose=purpose,
                confidence=candidate.confidence or extraction.confidence,
                origin="model",
            ))
    return lines


def _check(lines: list[ExpenseLine], policy: PolicyValues) -> dict:
    """The Rule: a pure check over what the flow already gathered.

    Returns a dict rather than models because a step's output is persisted and
    replayed as JSON.
    """
    escalations: list[dict] = []
    total = round(sum(line.requested_usd for line in lines), 2)
    if total > policy.limit_usd:
        escalations.append(Escalation(
            kind="over-limit",
            detail=(
                f"${total:,.2f} exceeds the ${policy.limit_usd:,.2f} "
                "auto-approval limit"
            ),
        ).model_dump())
    for line in lines:
        if line.confidence and line.confidence < policy.min_confidence:
            escalations.append(Escalation(
                kind="extraction-uncertain", line_ordinal=line.ordinal,
                detail=(
                    f"line {line.ordinal} extraction confidence "
                    f"{line.confidence:.2f} is below {policy.min_confidence:.2f}"
                ),
            ).model_dump())
        if line.category in policy.restricted:
            escalations.append(Escalation(
                kind="restricted-category", line_ordinal=line.ordinal,
                detail=(
                    f"line {line.ordinal} category '{line.category}' is always "
                    "reviewed"
                ),
            ).model_dump())
        if (
            line.requested_usd >= policy.purpose_required_above_usd
            and not line.purpose.strip()
        ):
            escalations.append(Escalation(
                kind="purpose-missing", line_ordinal=line.ordinal,
                detail=(
                    f"line {line.ordinal} at ${line.requested_usd:,.2f} needs a "
                    f"stated business purpose (required above "
                    f"${policy.purpose_required_above_usd:,.2f})"
                ),
            ).model_dump())
        if line.requested_usd > line.document_total_usd + policy.requested_tolerance_usd:
            escalations.append(Escalation(
                kind="amount-mismatch", line_ordinal=line.ordinal,
                detail=(
                    f"line {line.ordinal} requests ${line.requested_usd:,.2f} "
                    f"against a document total of "
                    f"${line.document_total_usd:,.2f}"
                ),
            ).model_dump())
    return {"escalations": escalations, "requested_usd": total}


def apply_correction(
    lines: list[ExpenseLine], payload: dict, decided_by: str
) -> list[ExpenseLine]:
    """Apply a reviewer's structured patch to the lines.

    The payload shape (all keys optional):

        {"lines": [{"ordinal": 1, "requested_usd": 75.0, "purpose": "...",
                    "category": "meals", "vendor": "...", "date": "...",
                    "document_id": "doc_…"}],
         "add_lines": [{...same fields, no ordinal...}],
         "remove_ordinals": [3]}

    A patched or added line is `origin="human"` and carries `corrected_by`,
    which is what makes the graph write record the value as a `human:` claim
    outranking the model's. Ordinals are renumbered at the end so the set stays
    dense — a line the reviewer removed does not leave a hole for the next
    round's Rule to reason about.
    """
    by_ordinal = {line.ordinal: line.model_copy(deep=True) for line in lines}

    for patch in payload.get("lines") or []:
        ordinal = int(patch.get("ordinal", 0) or 0)
        line = by_ordinal.get(ordinal)
        if line is None:
            continue
        for field in (
            "vendor", "date", "category", "description", "purpose", "document_id",
        ):
            if field in patch and patch[field] is not None:
                setattr(line, field, str(patch[field]))
        for field in ("requested_usd", "document_total_usd", "confidence"):
            if field in patch and patch[field] is not None:
                setattr(line, field, round(float(patch[field]), 2))
        # A reviewer who has looked at the line has resolved the model's
        # uncertainty about it, so the confidence check stops applying — without
        # this the extraction-uncertain escalation would raise itself again on
        # the next round forever. An explicit confidence in the patch wins, for
        # the reviewer who wants to say "I am also unsure".
        if "confidence" not in patch:
            line.confidence = 1.0
        line.origin = "human"
        line.corrected_by = decided_by

    for ordinal in payload.get("remove_ordinals") or []:
        by_ordinal.pop(int(ordinal), None)

    next_ordinal = (max(by_ordinal) if by_ordinal else 0) + 1
    for addition in payload.get("add_lines") or []:
        line = ExpenseLine(
            ordinal=next_ordinal,
            document_id=str(addition.get("document_id", "")),
            vendor=str(addition.get("vendor", "")),
            date=str(addition.get("date", "")),
            category=str(addition.get("category", "other")),
            description=str(addition.get("description", "")),
            document_total_usd=round(float(addition.get("document_total_usd", 0) or 0), 2),
            requested_usd=round(float(addition.get("requested_usd", 0) or 0), 2),
            purpose=str(addition.get("purpose", "")),
            # A human-entered line is certain by definition: the uncertainty a
            # confidence expresses is the model's, and there is no model here.
            confidence=1.0,
            origin="human",
            corrected_by=decided_by,
        )
        by_ordinal[next_ordinal] = line
        next_ordinal += 1

    ordered = [by_ordinal[key] for key in sorted(by_ordinal)]
    for index, line in enumerate(ordered, start=1):
        line.ordinal = index
    return ordered


def build_reimbursement_service(
    backend: str,
    project: str | None,
    collection: str,
    reader: ReceiptReader,
    knowledge,
    tasks: TaskService,
    engine: RunEngine | None = None,
    graph: GraphService | None = None,
    documents: DocumentService | None = None,
) -> ReimbursementService:
    directory: ReimbursementDirectory
    if backend == "firestore":
        directory = FirestoreReimbursementDirectory(project, collection)
    else:
        directory = InMemoryReimbursementDirectory()
    return ReimbursementService(
        directory, reader, knowledge, tasks, engine, graph, documents
    )


# ---------------------------------------------------------------------------
# Seed history — driven through the real engine
# ---------------------------------------------------------------------------
#
# The seed does not hand-write rows any more: it *runs the flow*, and for the
# decided cases it decides the Task through the same path a person would. So
# the demo history has real runs with real journals, real documents in the
# audit Collection, real graph objects, and real Task decisions — and any bug
# in the flow shows up at startup rather than on the first live submission.
#
# Two deliberate compromises, both marked: the stub reader is forced regardless
# of the configured backend (seeding must not spend model calls, and must be
# deterministic), and timestamps are back-dated after the fact so the history
# spreads across a month instead of piling up at boot.


class SeedCase(BaseModel):
    run_id: str
    employee: str
    days_ago: int
    documents: list[SubmittedDocument]
    commentary: str = ""
    # None leaves whatever the flow decided (approved, or pending on a Task).
    decision: dict | None = None


def _seed_cases() -> list[SeedCase]:
    def doc(hint: str, filename: str = "", note: str = "") -> SubmittedDocument:
        return SubmittedDocument(
            filename=filename or f"{hint}.jpg", note=note, hint=hint
        )

    return [
        SeedCase(
            run_id="run_er_seed01", employee="pat@customer.example", days_ago=21,
            documents=[doc("total=64.20 vendor=corner-bistro category=meals")],
            commentary="Team lunch after the Acme Phase 1 review.",
        ),
        SeedCase(
            run_id="run_er_seed02", employee="sam@customer.example", days_ago=19,
            documents=[doc("total=32.50 vendor=transit-authority category=travel")],
            commentary="Rail fare to the customer site.",
        ),
        SeedCase(
            run_id="run_er_seed03", employee="pat@customer.example", days_ago=16,
            documents=[doc("total=612.00 vendor=harbor-hotel category=lodging")],
            commentary="Conference block rate for the finance summit, three nights.",
            decision={
                "verdict": "approved", "decided_by": "dana@customer.example",
                "note": "Block rate confirmed against the event contract.",
            },
        ),
        SeedCase(
            run_id="run_er_seed04", employee="kim@customer.example", days_ago=14,
            documents=[doc("total=141.13 vendor=office-depot category=supplies")],
            commentary="Supplies for the onsite customer workshop.",
        ),
        SeedCase(
            run_id="run_er_seed05", employee="sam@customer.example", days_ago=12,
            documents=[doc("total=96.40 vendor=vintners-cellar category=alcohol")],
            commentary="Client gift wine for the Acme close.",
            decision={
                "verdict": "rejected", "decided_by": "dana@customer.example",
                "note": "Gift wine is out of policy §4.2 — use the gifting desk.",
            },
        ),
        SeedCase(
            run_id="run_er_seed06", employee="kim@customer.example", days_ago=8,
            documents=[doc("total=49.00 vendor=cloudline-software category=software")],
            commentary="Monthly seat for the design tool.",
        ),
        # The submitter's own split: one receipt, part of it personal. This is
        # the case the commentary parser exists for, and it auto-approves.
        SeedCase(
            run_id="run_er_seed07", employee="pat@customer.example", days_ago=6,
            documents=[doc(
                "total=110.00 vendor=corner-bistro category=meals",
                filename="dinner-acme-phase2.jpg",
                note=(
                    "Dinner with the Acme team after the Phase 2 review. I am "
                    "only requesting $75 of the $110 — I ordered wine on my own "
                    "tab."
                ),
            )],
            commentary="Client dinner, Acme Phase 2.",
        ),
        # A blurry receipt: the extraction is uncertain, a reviewer confirms the
        # amount, and the run finishes on the second round.
        SeedCase(
            run_id="run_er_seed08", employee="pat@customer.example", days_ago=5,
            documents=[doc(
                "blurry total=58.75 vendor=airport-taxi category=travel",
                filename="taxi-crumpled.jpg",
            )],
            commentary="Airport transfer for the customer visit.",
            decision={
                "verdict": "corrected", "decided_by": "dana@customer.example",
                "note": "Amount matches the card feed; vendor spelling fixed.",
                "payload": {"lines": [{
                    "ordinal": 1, "requested_usd": 58.75,
                    "vendor": "airport-taxi",
                    "purpose": "Airport transfer for the Acme customer visit.",
                }]},
            },
        ),
        # A compound receipt over the limit: two lines out of one document, the
        # reviewer supplies purposes and trims one line, and the re-check passes.
        SeedCase(
            run_id="run_er_seed09", employee="kim@customer.example", days_ago=4,
            documents=[doc(
                "compound total=210.00 vendor=harbor-hotel category=lodging",
                filename="hotel-folio.pdf",
                note="Room and the parking charge are on one folio.",
            )],
            commentary="Two nights at the Acme onsite.",
            decision={
                "verdict": "corrected", "decided_by": "dana@customer.example",
                "note": (
                    "Split the folio: room stands, parking trimmed to the "
                    "reimbursable rate."
                ),
                "payload": {"lines": [
                    {"ordinal": 1, "purpose": "Two nights at the Acme onsite.",
                     "category": "lodging"},
                    {"ordinal": 2, "requested_usd": 60.00, "category": "travel",
                     "purpose": "Parking at the Acme onsite, capped rate."},
                ]},
            },
        ),
        # A multi-document submission that needs nobody.
        SeedCase(
            run_id="run_er_seed10", employee="sam@customer.example", days_ago=3,
            documents=[
                doc("total=42.00 vendor=airport-taxi category=travel"),
                doc("total=38.50 vendor=corner-bistro category=meals"),
            ],
            commentary="Client visit to Acme — cab and lunch.",
        ),
        # Two left open on purpose, so the Task Queue has live work and the
        # workspace has suspended runs to show.
        SeedCase(
            run_id="run_er_seed11", employee="sam@customer.example", days_ago=2,
            documents=[doc(
                "total=487.90 vendor=harbor-hotel category=lodging",
                filename="hotel-invoice.pdf",
            )],
        ),
        SeedCase(
            run_id="run_er_seed12", employee="kim@customer.example", days_ago=0,
            documents=[doc(
                "blurry total=182.40 vendor=corner-bistro category=meals",
                filename="team-dinner.jpg",
            )],
        ),
    ]


def _shift_iso(value: str, delta) -> str:
    from datetime import datetime

    if not value:
        return value
    try:
        return (datetime.fromisoformat(value) + delta).isoformat()
    except ValueError:
        return value


def _backdate(engine: RunEngine, tasks: TaskService, run_id: str, days_ago: int) -> None:
    """Seed-only: move a run (and its Tasks) into the past.

    Nothing in the engine reads these timestamps to make decisions, so shifting
    them is a display concern, not a rewrite of history. Live runs never take
    this path.
    """
    from datetime import timedelta

    record = engine.get(run_id)
    if record is None:
        return
    delta = timedelta(days=-days_ago, minutes=-7)
    record.started_at_iso = _shift_iso(record.started_at_iso, delta)
    record.updated_at_iso = _shift_iso(record.updated_at_iso, delta)
    record.finished_at_iso = _shift_iso(record.finished_at_iso, delta)
    for step in record.steps:
        step.at_iso = _shift_iso(step.at_iso, delta)
        if step.task_id:
            task = tasks.get(step.task_id)
            if task is not None:
                task.created_at_iso = _shift_iso(task.created_at_iso, delta)
                if task.decision is not None:
                    task.decision.decided_at_iso = _shift_iso(
                        task.decision.decided_at_iso, delta
                    )
                tasks.directory.put(task)
    engine.store.put(record)


def seed_reimbursements_if_empty(service: ReimbursementService) -> int:
    if service.directory.list():
        return 0
    if service.engine is None:
        log.warning("No run engine wired; skipping reimbursement seed")
        return 0

    # Seeding must be deterministic and must not spend model calls, so the stub
    # reader is forced here even when the deployment has a real one configured.
    configured = service.reader
    service.reader = StubReceiptReader()
    seeded = 0
    try:
        for case in _seed_cases():
            from datetime import timedelta

            submitted_at = (utcnow() - timedelta(days=case.days_ago, minutes=7))
            request = service.submit(
                case.employee,
                documents=case.documents,
                commentary=case.commentary,
                run_id=case.run_id,
                submitted_at_iso=submitted_at.isoformat(),
            )
            if case.decision and request.task_id:
                decided = service.tasks.decide(
                    request.task_id,
                    verdict=case.decision["verdict"],
                    decided_by=case.decision["decided_by"],
                    note=case.decision.get("note", ""),
                    payload=case.decision.get("payload") or {},
                )
                service.resume_for_task(decided)
            _backdate(service.engine, service.tasks, case.run_id, case.days_ago)
            seeded += 1
    finally:
        service.reader = configured

    log.info(
        "Seeded expense history through the engine",
        extra={"runs": seeded, "rows": len(service.directory.list())},
    )
    return seeded
