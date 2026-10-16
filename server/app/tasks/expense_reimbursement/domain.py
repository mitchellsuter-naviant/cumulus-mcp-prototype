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

The flow body lives in a version package, and it reads as straight-line code
because the engine hides the replay. Two versions are shipped.

**v10 — the live shape.** Submit → extract → the submitter confirms → the graph.

  t1 trigger → s1 store-documents (Action, into the audit-documents
  Collection) → x0 read-knowledge (the prompt, the policy values and the
  routing table, each at the revision read) → m{i} extract-receipt per document
  (ModelCall) → x1 project-lines (documents + extractions + commentary → facts)
  → x2 resolve-reviewer (which desk, per the governed routing table) → then a
  loop: r{n} policy-check (Rule) → k{n} confirm-values (gate) → x{2+n}
  apply-correction → back to the Rule → a1 record-expense (Action: writes the
  graph) → e1 ExpenseRecorded (Event).

v10's gate is **always** raised, and the governed table routes it back to the
submitter: confirming an extraction is the submitter's own work, because nobody
else can say whether the number on the receipt is the number on the receipt.
The Rule still runs and still says what it found, but its findings annotate the
Task ("why this is worth a second look") rather than choosing a different desk.

**v9 — still shipped, still selectable.** Its shape is the older one, where the
Rule chose between auto-approval and an elevation to somebody else's desk:

  t1 → s1 → x0 read-prompt → m{i} extract-receipt → x1 read-policy → x2
  project-lines → x2r resolve-approver → r{n} needs-elevation → k{n}
  elevate-review → x{2+n} apply-correction → a1 → e1.

Everything non-deterministic — model calls, id minting, clock reads — happens
inside a step, so a replay is exact.

The correction rounds own `x3`, `x4`, `x5` in both versions — the projection
scans those ids to find the newest facts — which is why v9's routing step is
`x2r` and not the next number along. A step id is part of the journal's shape:
picked once, and never renumbered, because a resumed run would then replay
nothing. That is also why v10 renumbers rather than inserting: it is a
different version, with its own journal shape, and `_project` below reads both.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import threading
from collections.abc import Callable, Sequence
from typing import Literal, Protocol

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from ... import data_collections, desks, media, modelcall, routing
from ...auth import Principal, flow_principal
from ...dispatch import Dispatcher, InMemoryEventLog
from ...documents import DOCUMENT_COLLECTION, DocumentService, Region
from ...gateway import ActionGateway, InMemoryLedgerStore
from ...graph import Claim, GraphService, GraphWriter, InMemoryGraphStore
from ...modelcall import GovernedModelClient
from ...models import utcnow
from ...ontology import names as onto
from ...flows import read_model_rates
from ...prompts import GovernedPrompt, read_prompt
from ...routing import RoutingPolicy
from ...runs import ModelUsage, RunContext, RunEngine, RunRecord
from ...task_queue import Task, TaskEvidence, TaskProposal, TaskService
# The one fact both flows emit when a human moves a value the model read. Named
# from its declaration rather than retyped: a fourth spelling of an event string
# is a flywheel that silently stops turning.
from ..ap_corrections.domain import CORRECTION_EVENT

log = logging.getLogger("cumulus.reimbursements")

FLOW_ID = "expense-reimbursement"
DEFAULT_VERSION = 10
POLICY_ASSET_ID = "expense-approval-policy"
PROMPT_ASSET_ID = "receipt-extraction-prompt"
#: What each of this flow's ModelCalls is *for*, on its ledger row. One row per
#: document, which is why the step id matters here more than anywhere: without
#: it a three-receipt request is three rows nobody can tell apart.
EXTRACT_PURPOSE = "extract-receipt"
REVIEW_QUEUE = desks.EXPENSE_REVIEW

#: Who records an expense that no approver saw. A clean request under the
#: governed limit is committed on the Flow's own authority, bound to the queue
#: an approver would decide it from — the Gateway asks the same question of
#: both, and un-binding this stops auto-approval rather than exempting it.
EXPENSE_FLOW = flow_principal(FLOW_ID, REVIEW_QUEUE)


def expense_flow_principal(*queues: str) -> Principal:
    """`EXPENSE_FLOW`, additionally bound to the desks this case was routed to.

    v10 routes every review to the submitter's own desk, which is a different
    queue for every case — so a module-level constant cannot name it, and
    `record-expense` would refuse the Flow's own commit on a run resumed with
    no live principal (a seed, an operator's `POST /runs/{id}/resume`). The
    binding is still declared and still bounded: the standing expense desk,
    plus the one desk this run's Task was actually raised to, and nothing else.
    Un-bind it and those resumes stop rather than being waved through.
    """
    return flow_principal(FLOW_ID, REVIEW_QUEUE, *[q for q in queues if q])

# Correction rounds before the flow stops asking and denies. A reviewer who
# has corrected the same request three times is telling us the submission is
# wrong, not that the checks are.
MAX_REVIEW_ROUNDS = 3

ReimbursementState = Literal["approved", "pending", "denied"]

# ---------------------------------------------------------------------------
# The category vocabulary
# ---------------------------------------------------------------------------
#
# One declaration, because there were three and they were already disagreeing
# in ways nothing would have caught: the stub picked from a six-word list, the
# fallback prompt asked the model for a ten-word one, and the restricted trio
# was typed out separately in both. A category the extractor can return and the
# policy does not screen is extracted and then never reviewed, which is the
# failure the receipt-extraction prompt warns its editor about in prose.
#
# **The governed policy is the source at run time.** `parse_policy` reads
# "Restricted categories:" off the asset and a run screens against what it
# found there; `RESTRICTED_BY_DEFAULT` is what answers before the corpus has
# seeded, and the same words the seeded asset carries.

#: Reimbursable on their own. Ordered as the prompt asks for them.
ORDINARY_CATEGORIES: tuple[str, ...] = (
    "meals", "travel", "lodging", "mileage", "supplies", "software",
)

#: Always elevated for a human, whatever the amount. The fallback for
#: `PolicyValues.restricted` — the governed asset overrides it on every run
#: that finds one.
RESTRICTED_BY_DEFAULT: tuple[str, ...] = ("alcohol", "gifts", "entertainment")

#: Everything the extractor may return. `other` last, because it is the
#: catch-all and reads as one at the end of the list.
ALL_CATEGORIES: tuple[str, ...] = (
    *ORDINARY_CATEGORIES, *RESTRICTED_BY_DEFAULT, "other",
)

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
    # What the call consumed, as the reader that made it can report it. Zero
    # means *unmeasured*, never free: a reader with no model behind it leaves
    # these alone, and `flows.cost_basis` is what stops the resulting $0.00
    # from reading as "cheap" on a console page. The reader deliberately does
    # not price them — it knows the tokens and never the rate.
    input_tokens: int = 0
    output_tokens: int = 0
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
        default_factory=lambda: list(RESTRICTED_BY_DEFAULT)
    )
    purpose_required_above_usd: float = 75.0
    requested_tolerance_usd: float = 0.5
    source_ref: str = f"knowledge:{POLICY_ASSET_ID}"
    found_asset: bool = False


class GovernedReads(BaseModel):
    """Everything v10 reads out of the knowledge layer, in one step.

    One step and not three, because it is one *read*: the same asset carries
    the limits the Rule consumes and the table the routing transform answers,
    and the prompt beside them is read at the same moment for the same run. A
    step per asset would put three rows in the journal saying the same thing
    about the same revision, and — worse — would let a console edit land
    between two of them, so a run could check a receipt against rev 3's limit
    and route it by rev 4's table.

    The refs are collected here rather than derived by the reader, because
    "what this run read" is a claim about the run and the run is the only thing
    that knows it read the fallback.
    """

    prompt: GovernedPrompt = Field(default_factory=GovernedPrompt)
    policy: PolicyValues = Field(default_factory=PolicyValues)
    # `RoutingPolicy` imported by name and not written `routing.RoutingPolicy`:
    # the field below is called `routing`, and a deferred annotation would
    # resolve the module name against the class body and find the field.
    routing: RoutingPolicy = Field(default_factory=RoutingPolicy)

    def refs(self) -> list[str]:
        """The read-set, de-duplicated: the policy and the table are one asset."""
        out: list[str] = []
        for ref in (self.prompt.source_ref, self.policy.source_ref):
            if ref and ref not in out:
                out.append(ref)
        return out


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
    """One artifact in a submission, with the submitter's memo about it.

    `memo` is the human's own words about this document and is a real input to
    the run — the projection reads a stated claim out of it, the confirm screen
    shows it back, and the journal quotes it. It is **not** given to the
    extractor. It was: the API's `note` was concatenated into the reader's hint
    and the stub reader regex-parsed it, so a submitter who typed
    `total=500 vendor=corner-bistro` steered the extraction of their own
    receipt, and one who wrote a sentence containing the word "blurry" dropped
    its confidence below the policy floor. Accounts payable closed the same
    door in v8 (`payables_api.PayableSubmit`); this is that door on this side.
    Hence the rename: the field the wire carries has one job now, and its name
    says which.

    `hint` is the local-dev steering string for the stub reader
    (`total=110 vendor=… compound`). It is not on the wire at all — no REST
    body can set it — and reaches this model only from a test, a fixture or the
    seed. See `StubReceiptReader`.
    """

    model_config = ConfigDict(populate_by_name=True)

    content_b64: str = ""
    filename: str = ""
    #: `note` is accepted as an input alias so a run recorded before the rename
    #: still replays: `RunRecord.inputs` is stored JSON, and a resumed v9 run
    #: whose documents were written under the old key would otherwise come back
    #: with the submitter's words missing.
    memo: str = Field(default="", validation_alias=AliasChoices("memo", "note"))
    hint: str = ""


class ResolvedDesk(BaseModel):
    """Which desk this request would be elevated to, and why that one.

    Its own model because it is a *step output* — journalled, replayed, and
    read back on resume — so it needs a shape rather than a bare string. The
    explanation travels with it for the reason it exists at all: a queue slug
    in a journal says where work went, and a reader needs to know whether that
    was the governed table or a fallback covering for a gap in the org chart.
    """

    queue: str
    target: str = ""
    explanation: str = ""
    fell_back: bool = False
    amount_usd: float = 0.0
    restricted: bool = False


class DocumentSlot(BaseModel):
    """A stored document as the run refers to it afterwards.

    `memo` rides here because it is a property of the document, not of the
    submission: three receipts in one envelope each get their own sentence, and
    the confirm screen shows each one beside the extraction it is about.
    """

    model_config = ConfigDict(populate_by_name=True)

    document_id: str
    filename: str = ""
    memo: str = Field(default="", validation_alias=AliasChoices("memo", "note"))
    #: Carried so a replayed step is exact and so the journal can say a run was
    #: steered. Never read by anything but the stub reader.
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


# What the ModelCall runs on when the corpus has not seeded yet. The governed
# copy is `seed/receipt-extraction-prompt.md`, whose fenced block is this text;
# the two are expected to diverge the first time a customer edits the asset,
# which is the whole point of the asset existing.
#
# The category list is interpolated rather than typed out, because it was typed
# out — a fourth copy of the vocabulary, in the one place a drift is invisible:
# a prompt that offers a word the policy does not screen produces receipts that
# are extracted and never reviewed.
DEFAULT_RECEIPT_PROMPT = f"""\
Extract from this receipt: vendor (lowercase slug), date (ISO), total_usd
(number), category (one of {", ".join(ALL_CATEGORIES)}), your confidence 0-1,
and lines: an array of the distinct charges on the receipt, each with vendor,
date, category, amount_usd and description. If the receipt covers only one
charge, return a single line. Respond with only a JSON object with keys vendor,
date, total_usd, category, confidence, lines."""


class ReceiptReader(Protocol):
    def read(
        self,
        image: bytes | None,
        filename: str,
        hint: str,
        *,
        prompt: str = "",
        media_type: str = "",
    ) -> ReceiptExtraction:
        """`prompt` is the governed instruction the flow read for this run.

        Passed in rather than held by the reader because it is knowledge and the
        reader is machinery. A reader with no model behind it ignores it and
        says so; the flow records the revision either way, because the flow
        really did read it.

        `media_type` is passed in for the same reason and learned the hard way.
        The document store sniffs the bytes once and records the answer; a
        reader that re-derives it from the filename is a second, worse copy of
        that decision, and the second copy is the one that sent a PDF to Vertex
        labelled `image/jpeg`. Empty means "nobody told me", and a reader
        sniffs for itself rather than assuming.
        """
        ...


# ---------------------------------------------------------------------------
# STUB FIXTURES — invented, and read by `StubReceiptReader` and nothing else
# ---------------------------------------------------------------------------
#
# Six merchant slugs that no customer ever billed, so an offline run produces a
# receipt that looks like one. Quarantined under a `_STUB_` prefix so a
# reviewer can grep for the invented vocabulary; nothing on a production path
# reads it, and a deployment with a real extractor never reaches this block.
#
# The categories are *not* a fixture and are not restated here: what the stub
# may return has to be what the policy screens, or a local run cannot exercise
# the escalation it exists to demonstrate.

_STUB_VENDORS = [
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

    def read(
        self,
        image: bytes | None,
        filename: str,
        hint: str,
        *,
        prompt: str = "",
        media_type: str = "",
    ) -> ReceiptExtraction:
        text = f"{filename} {hint}".lower()
        seed = hashlib.sha256(
            (image or b"") + filename.encode() + hint.encode()
        ).digest()

        def pick(options: Sequence[str], salt: int) -> str:
            return options[seed[salt] % len(options)]

        total_match = re.search(r"total[=\-_ ]?\$?(\d+(?:\.\d{1,2})?)", text)
        amount_match = total_match or re.search(r"\$(\d+(?:\.\d{1,2})?)", text)
        vendor_match = re.search(r"vendor[=\-_]([a-z0-9\-]+)", text)
        # Every category the vocabulary names except `other`, which is a
        # substring of ordinary English and would match a filename saying
        # "another".
        category_match = next(
            (c for c in ALL_CATEGORIES if c != "other" and c in text), None
        )
        split_match = re.search(r"split[=\-_]([\d.,]+)", text)

        total = (
            float(amount_match.group(1))
            if amount_match
            else round(12 + (int.from_bytes(seed[:2], "big") % 38800) / 100, 2)
        )
        confidence = 0.55 if "blurry" in text else 0.86 + (seed[2] % 12) / 100
        confidence = round(min(confidence, 0.99), 2)
        vendor = vendor_match.group(1) if vendor_match else pick(_STUB_VENDORS, 3)
        # An unhinted receipt is an ordinary one: picking a restricted
        # category at random would elevate half of local development.
        category = category_match or pick(ORDINARY_CATEGORIES, 4)
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
                category=category if i == 0 else pick(ORDINARY_CATEGORIES, 5),
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
    """Multimodal extraction on Vertex AI, through the governed model client.

    Asks for lines as well as a total: a compound receipt is the interesting
    case and the model can see it.

    The client is shared with the invoice reader and the answer planner rather
    than built here. Three readers each holding their own was three chances for
    one of them to drift out of the ledger, which is exactly what had already
    happened to the embedder.
    """

    name = "vertex"

    def __init__(self, model: str, client: GovernedModelClient) -> None:
        self._client = client
        self._model = model

    def read(
        self,
        image: bytes | None,
        filename: str,
        hint: str,
        *,
        prompt: str = "",
        media_type: str = "",
    ) -> ReceiptExtraction:
        from google.genai import types

        parts: list = []
        if image:
            # Trust what the store sniffed; sniff only when nobody said. Then
            # refuse here rather than at the provider: an unreadable format is
            # a fact about the submission that a person can act on, and
            # `400 Provided image is not valid` is not.
            mime = media.require_model_readable(
                media_type or media.sniff(image, filename), filename
            )
            parts.append(types.Part.from_bytes(data=image, mime_type=mime))
        parts.append(
            types.Part(
                text=(
                    (prompt or DEFAULT_RECEIPT_PROMPT)
                    + (f"\n\nSubmitter note: {hint}" if hint else "")
                )
            )
        )
        response, usage = self._client.generate(
            model=self._model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.0
            ),
            purpose=EXTRACT_PURPOSE,
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
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            lines=lines,
        )


def build_receipt_reader(
    backend: str,
    project: str | None,
    location: str,
    model: str,
    *,
    client: GovernedModelClient | None = None,
) -> ReceiptReader:
    """`client` is the deployment's governed client; None builds a private one.

    See `accounts_payable.domain.build_invoice_reader`: a reader constructed
    without one still meters and still ledgers, into a store nobody serves —
    and a backend nobody chose is refused rather than answered with the stub.
    """
    if backend == "vertex":
        return VertexReceiptReader(
            model, client or modelcall.vertex_client(project, location)
        )
    modelcall.require_stub_opt_in(backend, "receipt reader")
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
        gateway: ActionGateway | None = None,
        dispatcher: Dispatcher | None = None,
        queues=None,  # QueueService — duck-typed, `queues` imports `auth`
    ) -> None:
        self.directory = directory
        self.reader = reader
        self.knowledge = knowledge
        self.tasks = tasks
        self.engine = engine
        self.graph = graph
        self.documents = documents
        # The desk registry, for routing an elevated request to a named
        # person's own queue. Optional: without it the governed table still
        # decides, and a target that needs a registry falls back.
        self.queues = queues
        # Never None — see the same note in payables.py. A service wired
        # without a Gateway still writes through one.
        self.gateway = gateway or ActionGateway(
            graph or GraphService(InMemoryGraphStore()), InMemoryLedgerStore()
        )
        # Never bypassed either — see the same note in payables.py. A service
        # wired without a Dispatcher still starts its runs through one.
        self.dispatcher = dispatcher or (
            Dispatcher(engine, InMemoryEventLog()) if engine is not None else None
        )
        if engine is not None:
            from .. import shipped_versions

            for number, module in sorted(shipped_versions(FLOW_ID).items()):
                # `metered` puts the run and the step in scope for the
                # ModelCalls the body makes — see the same registration in
                # payables. It matters more here: this flow files one call per
                # document, and without the step id they are indistinguishable.
                engine.register(
                    FLOW_ID, modelcall.metered(module.build_body(self)),
                    version=number, default=(number == DEFAULT_VERSION),
                )

    # -- reads --------------------------------------------------------------

    def visible_to(
        self, principal: Principal
    ) -> Callable[[ReimbursementRequest], bool]:
        """Which expense rows this principal may see. `employee` is the owner.

        The rule, and why it is not `reads_beyond_self`, lives in
        `TaskService.workspace_scope`.
        """
        allowed = self.tasks.workspace_scope(principal, REVIEW_QUEUE)
        return lambda r: allowed(r.employee, r.task_id)

    def list(
        self,
        state: ReimbursementState | None = None,
        employee: str | None = None,
        principal: Principal | None = None,
    ) -> list[ReimbursementRequest]:
        """The table. `employee` is the explicit "just mine" filter; `principal`
        is the scoping rule. `principal=None` is unscoped, for seeding and
        internals — every caller that serves a request passes one."""
        items = self.directory.list()
        if state is not None:
            items = [r for r in items if r.state == state]
        if employee is not None:
            items = [r for r in items if r.employee == employee]
        if principal is not None:
            visible = self.visible_to(principal)
            items = [r for r in items if visible(r)]
        return sorted(items, key=lambda r: r.submitted_at_iso, reverse=True)

    def get(self, request_id: str) -> ReimbursementRequest | None:
        return self.directory.get(request_id)

    def stats(self, principal: Principal | None = None) -> ReimbursementStats:
        """Totals over exactly the rows `list` would return.

        A total is a read of every row it counts, so an org-wide figure handed
        to someone who may see four of the rows leaks the other eight in
        aggregate. Counted *from* `list` rather than beside it so the two can
        never be computed over different slices — a strip arguing with the
        table under it is worse than no strip at all.
        """
        stats = ReimbursementStats()
        for r in self.list(principal=principal):
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

    def read_policy_content(self) -> str:
        """The policy document itself, for the parts of it that are not values.

        `read_policy` returns the five numbers the Rule needs; the routing
        table is a table, and re-parsing it into `PolicyValues` would put a
        second grammar into a model whose whole job is to hold scalars.
        """
        asset = self.knowledge.repo.get(POLICY_ASSET_ID)
        return asset.content if asset is not None else ""

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

    def read_prompt(self) -> GovernedPrompt:
        return read_prompt(self.knowledge, PROMPT_ASSET_ID, DEFAULT_RECEIPT_PROMPT)

    def _read_knowledge(self) -> GovernedReads:
        """Everything v10's run reads out of the corpus, in one pass.

        One pass, and one *moment*: the policy values and the routing table are
        two sections of one asset, so reading them separately would let a
        console edit land between them and produce a run that checked a receipt
        against one revision's limit and routed it by another revision's table.
        """
        content = self.read_policy_content()
        return GovernedReads(
            prompt=self.read_prompt(),
            policy=self.read_policy(),
            routing=routing.parse_routing(content, default_fallback=REVIEW_QUEUE),
        )

    # -- the entry point ----------------------------------------------------

    def submit(
        self,
        employee: str,
        documents: list[SubmittedDocument] | None = None,
        commentary: str = "",
        *,
        image_b64: str = "",
        filename: str = "",
        memo: str = "",
        hint: str = "",
        run_id: str | None = None,
        submitted_at_iso: str = "",
    ) -> ReimbursementRequest:
        """Start one run of the flow. Returns the row the run produced.

        The single-document arguments (`image_b64`, `filename`, `memo`) are
        still accepted: a one-receipt submission is the common case and there
        is no reason to make a caller wrap it in a list. `hint` is the stub
        reader's steering seam and lands in the document's `hint`, never in its
        `memo` — it used to land in the memo, which is how a demo knob and a
        submitter's own words became one field.

        **A submission with no document is refused.** There used to be a
        `docs = [SubmittedDocument()]` fallback here, and it meant a caller
        with no file at all could describe a receipt in prose and get back an
        extraction, a set of lines and a graph write — evidence-free facts,
        indistinguishable afterwards from ones a receipt supported. Accounts
        payable refused the same shape at `_resolve_document`; this is that
        refusal on this side. The REST door refuses it earlier and more
        politely (400); this is the floor under every caller.
        """
        docs = [
            d if isinstance(d, SubmittedDocument) else SubmittedDocument.model_validate(d)
            for d in (documents or [])
        ]
        if not docs and (image_b64 or filename or memo or hint):
            docs = [SubmittedDocument(
                content_b64=image_b64, filename=filename, memo=memo, hint=hint
            )]
        if not docs:
            raise ValueError(
                "an expense submission needs at least one document: "
                "commentary alone is not evidence"
            )

        inputs = {
            "employee": employee,
            "commentary": commentary,
            "submitted_at_iso": submitted_at_iso or utcnow().isoformat(),
            "documents": [d.model_dump() for d in docs],
        }
        if self.dispatcher is None:
            raise RuntimeError("Reimbursement service has no run engine wired")
        # The fact is that an expense was submitted; which response that
        # starts is the Trigger's answer, not this call site's.
        record = self.dispatcher.emit(
            "ExpenseSubmitted", inputs, actor=employee, run_id=run_id,
        ).one()
        return self._project(record)

    def resume_for_task(
        self, task: Task, principal: Principal | None = None
    ) -> ReimbursementRequest | None:
        """A decided Task releases the gate its run is suspended on.

        `principal` is the human who decided, carried through to the commit:
        the Gateway checks the committer at the write, and a uid recovered
        from `decided_by` afterwards would name the right person while proving
        nothing about their authority.
        """
        if task.flow_id != FLOW_ID or self.engine is None:
            return None
        self._emit_correction(task)
        record = self.engine.resume_for_task(task.id, principal=principal)
        return self._project(record) if record else None

    # Kept under the old name: tasks_api calls it after a decision.
    def apply_task_decision(
        self, task: Task, principal: Principal | None = None
    ) -> ReimbursementRequest | None:
        return self.resume_for_task(task, principal)

    # -- the flywheel -------------------------------------------------------

    def _emit_correction(self, task: Task) -> None:
        """Announce what the submitter changed, then get on with the expense.

        The same mechanism accounts payable uses, on the same event, into the
        same Collection — deliberately the same, and deliberately not a second
        flow. Doctrine's flywheel clause names the route: a human correction
        flows back **decoupled, via an Event, not inline**. So this emits
        `ExtractionCorrected` and returns; the Trigger table answers it with an
        `ap-corrections` run that appends the raw→corrected pairs, and the
        expense below carries on recording whether or not that lands.

        Nothing is promoted. The pairs are the prerequisite for teaching the
        receipt prompt something; deciding that it has been taught stays a
        human decision behind a curation Task, and this lane does not make it.

        Emitted before the resume, deliberately: the resume applies the patch,
        and afterwards the reading the submitter was looking at is gone. What is
        wanted is the *pair*, and only one of its halves survives the round.
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
            # The decision has already been carried into the run. Two callers
            # reach for the same task — the API hook and a seeding script —
            # and the same pair must not be appended twice; "is this run still
            # waiting" is the fact that tells them apart.
            return
        pairs = _correction_pairs(
            _lines_of(record), decision.payload, _flagged_ordinals(record)
        )
        if not pairs:
            return
        self.dispatcher.emit(
            CORRECTION_EVENT,
            {
                "pairs": pairs,
                # One expense can carry several receipts, so the document a
                # pair is about is named on the pair rather than on the run —
                # see `_correction_pairs`. This is the run-level default for a
                # single-document submission, which is the common case.
                "document_id": _primary_document(record),
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
                    filename=doc.filename, memo=doc.memo, hint=doc.hint,
                ))
                continue
            record = self.documents.put_bytes(
                doc.content_b64, filename=doc.filename, kind="receipt",
                uploaded_by=employee, run_id=run_id,
            )
            slots.append(DocumentSlot(
                document_id=record.id, filename=doc.filename or record.filename,
                memo=doc.memo, hint=doc.hint, byte_size=record.byte_size,
                media_type=record.media_type,
            ))
        return slots

    def _extract(
        self,
        doc: SubmittedDocument,
        slot: DocumentSlot,
        prompt: str,
        *,
        hint: str = "",
        store_regions: bool = True,
    ) -> ReceiptExtraction:
        """Read one document. `hint` is whatever the caller chose to steer with.

        Taken as a parameter rather than assembled here, and that is the whole
        fix. This used to build `f"{doc.hint} {doc.memo}"` itself, so the
        submitter's own sentence was concatenated into the extractor's steering
        string on every run: `StubReceiptReader` regex-parses that string, so a
        submitter could type `total=500` into the memo box and extract whatever
        they liked out of their own receipt. v10 passes `doc.hint` alone — a
        field no REST body can set — and v9 keeps its historical behaviour,
        because a shipped version's runs have to replay as they ran.

        `store_regions=False` is v10's, and it is the one thing v10 drops
        rather than inherits. The only regions any reader on this side produces
        are the stub's invented geometry (`_regions_for`, labelled synthetic in
        the reader's own note); the Vertex reader emits none, and there is no
        locate step for receipts. Persisting them put made-up boxes on a real
        photograph and drew them on a reviewer's screen as if a model had found
        something there. Accounts payable's v8 refused exactly this — see
        `accounts_payable.domain.PayableService._extract`.
        """
        image = base64.b64decode(doc.content_b64) if doc.content_b64 else None
        # The type the document store already decided, not a second guess. The
        # slot has none only when no store is wired (a unit test, a hint-only
        # run), and then the bytes answer for themselves.
        media_type = slot.media_type or media.sniff(image, doc.filename)
        # The prompt revision the ledger files this call against — off the
        # step's own declared read-set, not a second read of the asset. See the
        # same line in payables for why the second read would be wrong.
        with modelcall.reading(modelcall.governed_ref(PROMPT_ASSET_ID)):
            extraction = self.reader.read(
                image, doc.filename, hint,
                prompt=prompt, media_type=media_type,
            )
        if store_regions and self.documents is not None and extraction.regions:
            self.documents.set_regions(slot.document_id, extraction.regions)
        return extraction

    def _resolve_desk(
        self, employee: str, amount_usd: float, restricted: bool
    ) -> ResolvedDesk:
        """Read the governed routing table, then answer it against the graph.

        Degrades in one direction only. A policy with no routing section, an
        org chart with no manager for this person, a graph that is not wired at
        all — each lands on the fallback desk with `fell_back` set and a
        sentence saying which of those happened. The one thing that must never
        come out of here is a queue nobody holds, because a Task on a desk with
        no people is work that exists and that nobody will ever see.
        """
        policy_asset = self.read_policy_content()
        table = routing.parse_routing(policy_asset, default_fallback=REVIEW_QUEUE)
        target = table.target_for(amount_usd, restricted)
        # No `if self.queues is None` branch, and its absence is the fix rather
        # than an omission. There was one, and it treated any non-`manager-of`
        # target as a literal slug — which with the `submitter` resolver would
        # have minted a shared desk called "submitter" and piled every
        # submitter's own review into it. `routing.resolve` degrades on an
        # absent or unreachable registry by itself, onto the policy's own
        # fallback desk with `fell_back` set, which is the answer the document
        # already names for a resolver that cannot answer.
        resolved = routing.resolve(
            target,
            submitter_email=employee,
            graph=self.graph,
            queues=self.queues,
            fallback=table.fallback,
            # No `submitter_name`: this flow holds an address and not a display
            # name, so the org chart answers it — and where the org chart has
            # never heard of the person, their address is printed on their own
            # desk rather than the routing failing over a label.
        )
        return ResolvedDesk(
            queue=resolved.queue, target=resolved.target,
            explanation=resolved.explanation, fell_back=resolved.fell_back,
            amount_usd=amount_usd, restricted=restricted,
        )

    def _build_confirm_task(
        self,
        record: RunRecord,
        lines: list[ExpenseLine],
        slots: list[DocumentSlot],
        extractions: list[ReceiptExtraction],
        escalations: list[Escalation],
        policy: PolicyValues,
        round_number: int,
        employee: str,
        routed: ResolvedDesk,
    ) -> Task:
        """v10's gate: the submitter confirming what the model read.

        Raised on **every** run, which is what makes it a different task from
        v9's. v9 asked somebody else, and only when a check fired, so its title
        led with the exception and its summary explained the escalation. Here
        the question is always the same one — *are these the numbers on your
        receipt?* — and the Rule's findings are context on the answer rather
        than the reason for asking. A person who is told "confirm this" and a
        person who is told "this was flagged" read the same screen differently,
        and only one of those two sentences is true of every run.

        The proposal carries one row per document (§4's contract): the stored
        artifact, the submitter's own memo about it, and the extraction summary
        for it. The lines themselves are on the request row the screen already
        reads, unchanged from v9 — a second copy on the Task would be the same
        facts able to disagree with themselves.
        """
        total = round(sum(line.requested_usd for line in lines), 2)
        title_vendor = lines[0].vendor if lines else "expense"
        kinds = sorted({e.kind for e in escalations})
        return Task(
            # Named for the step that raised it, suffix and all, so a journal
            # row and a Task id read as the same event rather than as two.
            id=f"task_{record.id}_k1_{round_number}",
            kind="decision",
            title=f"Confirm your expense: {title_vendor} ${total:,.2f}",
            summary=(
                "Check what was read off your receipt"
                + ("s" if len(slots) > 1 else "")
                + ". Correct anything that is wrong — an amount, a category, a "
                "missing purpose — or split a receipt into several lines. "
                + (
                    "Worth a second look: "
                    + "; ".join(e.detail for e in escalations)
                    + "."
                    if escalations
                    else "Nothing was flagged against the governed policy."
                )
            ),
            queue=routed.queue,
            #: Who the case is *about*, first-class rather than display text in
            #: an evidence row. Here it is also who the Task was routed to, and
            #: that coincidence is the point of the version.
            subject_email=employee,
            priority="high" if total > policy.limit_usd * 5 else "normal",
            flow_id=FLOW_ID,
            run_id=record.id,
            case_ref=record.case_ref or record.id,
            refs=[policy.source_ref]
            + [f"document:{slot.document_id}" for slot in slots]
            + [f"queue:{routed.queue}"],
            evidence=[
                TaskEvidence(label="Submitted by", value=employee),
                TaskEvidence(label="Documents", value=str(len(slots))),
                TaskEvidence(label="Lines", value=str(len(lines))),
                TaskEvidence(label="Requested", value=f"${total:,.2f}"),
                TaskEvidence(
                    label="Document total",
                    value=f"${sum(line.document_total_usd for line in lines):,.2f}",
                ),
                TaskEvidence(
                    label="Why elevated",
                    value=", ".join(kinds) if kinds else "nothing flagged",
                ),
                TaskEvidence(
                    label="Auto-approval limit",
                    value=f"${policy.limit_usd:,.2f} (governed)",
                ),
                TaskEvidence(label="Routed", value=routed.explanation),
                TaskEvidence(label="Review round", value=str(round_number)),
            ],
            proposal=TaskProposal(
                action="record-expense",
                target="expense-records",
                summary=f"Record {record.case_ref or record.id} as approved",
                # One row per document. `fields` is untyped by contract — the
                # flow that raised the Task is the only thing that reads it —
                # and what this flow puts there is the evidence beside its
                # reading, which is the pairing the confirm screen is for.
                fields=[
                    {
                        "document_id": slot.document_id,
                        "filename": slot.filename,
                        "memo": slot.memo,
                        "extraction": {
                            "vendor": extraction.vendor,
                            "date": extraction.date,
                            "total_usd": extraction.total_usd,
                            "category": extraction.category,
                            "confidence": extraction.confidence,
                        },
                    }
                    for slot, extraction in zip(slots, extractions)
                ],
            ),
            resumes=(
                f"this run resumes at k1_{round_number} and re-runs the check"
            ),
            created_at_iso=utcnow().isoformat(),
        )

    def _build_review_task(
        self,
        record: RunRecord,
        lines: list[ExpenseLine],
        escalations: list[Escalation],
        policy: PolicyValues,
        round_number: int,
        employee: str,
        routed: ResolvedDesk | None = None,
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
            queue=routed.queue if routed else REVIEW_QUEUE,
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
            onto.EMPLOYEE, request.employee,
            [Claim(field="email", value=request.employee, source=source)],
            label=request.employee, provenance=f"seen submitting {request.id}",
        )
        request_obj = graph.upsert(
            onto.EXPENSE_REQUEST, request.run_id or request.id,
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
                onto.EXPENSE_LINE, f"{request.run_id or request.id}#{line.ordinal}",
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
                    onto.DOCUMENT, line.document_id,
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
                onto.EMPLOYEE, decided_by,
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
        slots = _slots_of(record)
        lines = _lines_of(record)

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
        policy = _policy_of(record)
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
# Reading a run's journal — shared by the projection and the flywheel
# ---------------------------------------------------------------------------
#
# Two shipped versions number their transforms differently (v9 projects lines at
# `x2` and reads the policy at `x1`; v10 projects at `x1` and reads all its
# governed knowledge at `x0`), and both are read back here. So these ask *what a
# step produced* rather than *which step it was*: the newest step whose output is
# a list of lines is the newest projection of the facts, in either version, and a
# version table keyed on `record.flow_version` would be a third place for the
# same arithmetic to be got wrong. Correction rounds own `x3`–`x5` in both.


def _step_output(record: RunRecord, step_id: str) -> dict:
    step = record.step(step_id)
    output = step.output if step is not None else None
    return output if isinstance(output, dict) else {}


def _slots_of(record: RunRecord) -> list[DocumentSlot]:
    stored = record.step("s1")
    if stored is None or not isinstance(stored.output, list):
        return []
    return [DocumentSlot.model_validate(slot) for slot in stored.output]


def _lines_of(record: RunRecord) -> list[ExpenseLine]:
    """The newest projection of the facts this run is working from.

    Newest first: after a correction the facts live in that round's
    `apply-correction` step, and showing the original extraction would tell a
    reviewer their edit vanished.
    """
    for step_id in _PROJECTION_STEPS:
        step = record.step(step_id)
        if step is None or not isinstance(step.output, list) or not step.output:
            continue
        try:
            return [ExpenseLine.model_validate(line) for line in step.output]
        except Exception:  # noqa: BLE001 — a step of some other shape, not an error
            continue
    return []


#: Where each shipped version leaves its facts, newest first. v10 suffixes its
#: correction rounds (`x3_1`…) as accounts payable does; v9 numbered them along
#: (`x3`, `x4`, `x5`). The base projection is v9's `x2` and v10's `x1`, and the
#: `isinstance(output, list)` guard above is what keeps v10's `x2` — a resolved
#: desk, a dict — from being read as a set of lines.
_PROJECTION_STEPS: tuple[str, ...] = tuple(
    [f"x3_{n}" for n in range(MAX_REVIEW_ROUNDS, 0, -1)]
    + [f"x{2 + n}" for n in range(MAX_REVIEW_ROUNDS, 0, -1)]
    + ["x2", "x1"]
)


def _policy_of(record: RunRecord) -> PolicyValues:
    """The governed values this run judged its lines against.

    v9 reads them into `x1` on their own; v10 reads them at `x0` beside the
    prompt and the routing table, under a `policy` key. Both are tried, and a
    run that has not reached either yet gets the defaults — which is what a row
    projected mid-flight has always shown.
    """
    for step_id in ("x1", "x0"):
        payload = _step_output(record, step_id)
        if not payload:
            continue
        if "policy" in payload:
            payload = payload["policy"] or {}
        try:
            return PolicyValues.model_validate(payload)
        except Exception:  # noqa: BLE001 — a partial run is not an error
            continue
    return PolicyValues()


def _primary_document(record: RunRecord) -> str:
    slots = _slots_of(record)
    return slots[0].document_id if slots else ""


def _flagged_ordinals(record: RunRecord) -> set[int]:
    """Which lines the newest check pass raised something about.

    The newest, because an escalation the submitter already answered is not
    what the run is still saying about the facts. Read off the Rule's own
    output rather than off the Task, so it is the check's answer and not a
    rendering of it.
    """
    for step in reversed(record.steps):
        if step.kind != "rule" or not isinstance(step.output, dict):
            continue
        return {
            int(item.get("line_ordinal") or 0)
            for item in step.output.get("escalations", [])
            if int(item.get("line_ordinal") or 0)
        }
    return set()


# ---------------------------------------------------------------------------
# The flywheel's pairs
# ---------------------------------------------------------------------------


def _correction_pairs(
    lines: list[ExpenseLine], payload: dict, flagged: set[int] | None = None
) -> list[dict]:
    """The raw→corrected pairs one decision produced, in line order.

    The raw side comes off the lines the person was *shown* — the projection
    the gate suspended on — so a pair is a claim about what the model read even
    when nothing had flagged the field. A submitter who quietly fixes a total
    nobody doubted is the most interesting row in the set: it is a miss the
    confidence score did not catch.

    Paths are the canonical fact paths for this flow, keyed the way accounts
    payable keys its own (`invoice.total_usd`, `line_items[2].amount_usd`) so
    the two sides of the correction set can be read by one tool: zero-based
    index, dotted field. `add_lines` produce no pairs — a line the extraction
    missed entirely has no raw side to pair against, and inventing an empty one
    would teach a future prompt that the model had read a blank.
    """
    by_ordinal = {line.ordinal: line for line in lines}
    pairs: list[dict] = []
    for patch in payload.get("lines") or []:
        ordinal = int(patch.get("ordinal", 0) or 0)
        line = by_ordinal.get(ordinal)
        if line is None:
            continue
        for field in _CORRECTABLE_FIELDS:
            if field not in patch or patch[field] is None:
                continue
            corrected = _render(patch[field])
            raw = _render(getattr(line, field, ""))
            # An emptied field is not read as a correction — the same rule
            # `apply_correction` applies. "Delete what the model read" is a
            # real thing somebody might mean and a blank input is not evidence
            # they meant it.
            if not corrected.strip() or _same_value(raw, corrected):
                continue
            pairs.append({
                "path": f"expense.lines[{ordinal - 1}].{field}",
                "label": f"Line {ordinal} {field.replace('_', ' ')}",
                "raw": raw,
                "corrected": corrected,
                # What the check had said about this line before the person
                # answered, so a later pass can ask the question that matters:
                # were the lines we flagged the lines that were actually
                # wrong? A pair with an empty status is the interesting one —
                # a value nobody doubted and the submitter fixed anyway.
                "status": "flagged" if line.ordinal in (flagged or set()) else "",
                "confidence": line.confidence,
                # Which receipt the pair is about. One expense can carry
                # several, so it rides the pair rather than the run.
                "document_id": line.document_id,
            })
    return pairs


#: The fields a correction can move that are worth a pair. `document_id` is a
#: re-filing rather than a re-reading, and `purpose` is something the model was
#: never asked for — neither teaches an extractor anything about a receipt.
_CORRECTABLE_FIELDS: tuple[str, ...] = (
    "vendor", "date", "category", "description", "document_total_usd",
    "requested_usd",
)


def _render(value: object) -> str:
    """One value as a person would have typed it.

    Strings on both sides of a pair: a correction is what somebody wrote, and
    coercing "4,182.50" to a float here would make the pair a record of this
    code's parsing rather than of their answer.
    """
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}"
    return str(value or "")


def _same_value(raw: str, corrected: str) -> bool:
    """Whether these two are the same answer, punctuation aside.

    Numerically when both sides are numbers, because `1200` and `1200.00` are
    one value and a correction set full of reformattings is one nobody can
    learn from.
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
        note_claim = parse_requested_amount(slot.memo)
        purpose = ""
        for text in (slot.memo, commentary):
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
                # The submitter's own memo about this receipt, where the
                # extraction described nothing. It is the only place their
                # words land on a fact — and it is why the memo needs no other
                # channel into the run: it is read for a stated claim above,
                # read for a purpose above, and shown here. What it must never
                # be is the extractor's steering string, which is what it was.
                description=candidate.description or _clip(slot.memo, 120),
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
    gateway: ActionGateway | None = None,
    dispatcher: Dispatcher | None = None,
    queues=None,
) -> ReimbursementService:
    directory: ReimbursementDirectory
    if backend == "firestore":
        directory = FirestoreReimbursementDirectory(project, collection)
    else:
        directory = InMemoryReimbursementDirectory()
    return ReimbursementService(
        directory, reader, knowledge, tasks, engine, graph, documents, gateway,
        dispatcher, queues,
    )
