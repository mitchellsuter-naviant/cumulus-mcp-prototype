"""The expense-reimbursement flow's real execution path, and its table.

This is the prototype's first *live* flow: `POST /api/v1/reimbursements` runs
the same steps the Tier-2 export declares — trigger → extract-receipt
(ModelCall) → read-policy (transform) → needs-elevation (Rule) →
elevate-review (Task) → record-reimbursement (Action) — synchronously, and
lands a row in the reimbursements table with its state.

Doctrine mapping, honestly stated:

- The **ModelCall** is real when `RECEIPT_BACKEND=vertex` (a multimodal
  Gemini call reads the image); locally it degrades to a deterministic stub
  that parses hints, so the pipeline is exercisable without a credential.
- The **governed knowledge read** is real: the rule's values (auto-approval
  limit, minimum confidence, restricted categories) are parsed from the
  `expense-approval-policy` asset at run time. Editing that document in the
  console changes what this flow does — no code change.
- The **Rule** is a pure check over inputs the flow gathered; it never
  fetches.
- The **Task** is real: an elevated request lands in the expense-review
  queue, and the human's decision (via the Task Queue) resumes this run —
  `apply_task_decision` appends the resumed steps to the journal and commits
  the state.
- The **journal** is the per-run record the canvas run-trace derives from,
  kept on the row so every request is inspectable end to end.

A suspended run costs nothing while it waits — the row simply sits at
state=pending with its journal ending at the Task step.
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

from .models import utcnow
from .tasks import Task, TaskEvidence, TaskProposal, TaskService

log = logging.getLogger("cumulus.reimbursements")

FLOW_ID = "expense-reimbursement"
POLICY_ASSET_ID = "expense-approval-policy"
REVIEW_QUEUE = "expense-review"

ReimbursementState = Literal["approved", "pending", "denied"]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ReceiptExtraction(BaseModel):
    vendor: str = ""
    date: str = ""
    total_usd: float = 0.0
    category: str = "other"
    confidence: float = 0.0
    model: str = ""
    note: str = ""


class PolicyValues(BaseModel):
    """The governed values the rule consumes — parsed from the policy asset.

    Parsed at run time so a console edit to the document takes effect on the
    next submission. Defaults apply when a value is absent, and the journal
    records which values were used, so a run is explainable even against an
    older revision of the document.
    """

    limit_usd: float = 200.0
    min_confidence: float = 0.8
    restricted: list[str] = Field(
        default_factory=lambda: ["alcohol", "gifts", "entertainment"]
    )
    source_ref: str = f"knowledge:{POLICY_ASSET_ID}"
    found_asset: bool = False


class JournalEvent(BaseModel):
    """One executed step — same ids as the Tier-2 export's steps."""

    step_id: str
    name: str
    kind: str
    summary: str
    at_iso: str = ""


class ReimbursementRequest(BaseModel):
    id: str
    employee: str
    submitted_at_iso: str
    filename: str = ""
    vendor: str = ""
    date: str = ""
    total_usd: float = 0.0
    category: str = "other"
    confidence: float = 0.0
    state: ReimbursementState = "pending"
    verdict_reason: str = ""
    policy_ref: str = f"knowledge:{POLICY_ASSET_ID}"
    policy_limit_usd: float = 0.0
    model: str = ""
    task_id: str = ""
    decided_by: str = ""
    decision_note: str = ""
    journal: list[JournalEvent] = Field(default_factory=list)


class ReimbursementStats(BaseModel):
    total: int = 0
    approved: int = 0
    pending: int = 0
    denied: int = 0
    approved_usd: float = 0.0
    auto_approved: int = 0


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


class StubReceiptReader:
    """Deterministic local-dev extractor: no credential, no network.

    Steerable for demos — a filename or hint like `total=64.20`,
    `vendor=corner-bistro`, `category=meals`, or the word `blurry` (drops
    confidence below the policy floor) is honored; anything else derives
    stable pseudo-values from a hash of the input so the same file always
    extracts the same way.
    """

    name = "stub"

    def read(
        self, image: bytes | None, filename: str, hint: str
    ) -> ReceiptExtraction:
        text = f"{filename} {hint}".lower()
        seed = hashlib.sha256(
            (image or b"") + filename.encode() + hint.encode()
        ).digest()

        def pick(options: list[str], salt: int) -> str:
            return options[seed[salt] % len(options)]

        total_match = re.search(r"total[=\-_ ]?\$?(\d+(?:\.\d{1,2})?)", text)
        amount_match = total_match or re.search(r"\$(\d+(?:\.\d{1,2})?)", text)
        vendor_match = re.search(r"vendor[=\-_]([a-z0-9\-]+)", text)
        category_match = next((c for c in _CATEGORIES + ["alcohol", "gifts", "entertainment"] if c in text), None)

        total = (
            float(amount_match.group(1))
            if amount_match
            else round(12 + (int.from_bytes(seed[:2], "big") % 38800) / 100, 2)
        )
        confidence = 0.55 if "blurry" in text else 0.86 + (seed[2] % 12) / 100
        return ReceiptExtraction(
            vendor=vendor_match.group(1) if vendor_match else pick(_VENDORS, 3),
            date=utcnow().date().isoformat(),
            total_usd=round(total, 2),
            category=category_match or pick(_CATEGORIES, 4),
            confidence=round(min(confidence, 0.99), 2),
            model="stub-extractor",
            note="deterministic local extraction — no model configured",
        )


class VertexReceiptReader:
    """Multimodal extraction on Vertex AI via google-genai.

    Same client and Application Default Credentials as the embedder and the
    answer backend, so enabling this adds no new secret.
    """

    name = "vertex"

    def __init__(self, project: str | None, location: str, model: str) -> None:
        from google import genai

        self._client = genai.Client(vertexai=True, project=project, location=location)
        self._model = model

    def read(
        self, image: bytes | None, filename: str, hint: str
    ) -> ReceiptExtraction:
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
                    "entertainment, other), and your confidence 0-1 in the "
                    "extraction overall. Respond with only a JSON object with "
                    "keys vendor, date, total_usd, category, confidence."
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
        return ReceiptExtraction(
            vendor=str(data.get("vendor", "")),
            date=str(data.get("date", "")),
            total_usd=float(data.get("total_usd", 0) or 0),
            category=str(data.get("category", "other")),
            confidence=float(data.get("confidence", 0) or 0),
            model=self._model,
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
    return values


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


# ---------------------------------------------------------------------------
# Service — the inline runner
# ---------------------------------------------------------------------------


def _event(step_id: str, name: str, kind: str, summary: str) -> JournalEvent:
    return JournalEvent(
        step_id=step_id, name=name, kind=kind, summary=summary,
        at_iso=utcnow().isoformat(),
    )


class ReimbursementService:
    def __init__(
        self,
        directory: ReimbursementDirectory,
        reader: ReceiptReader,
        knowledge,  # KnowledgeService — duck-typed to avoid an import cycle
        tasks: TaskService,
    ) -> None:
        self.directory = directory
        self.reader = reader
        self.knowledge = knowledge
        self.tasks = tasks

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
            if r.state == "approved":
                stats.approved += 1
                stats.approved_usd = round(stats.approved_usd + r.total_usd, 2)
                if not r.task_id:
                    stats.auto_approved += 1
            elif r.state == "pending":
                stats.pending += 1
            else:
                stats.denied += 1
        return stats

    # -- the run ------------------------------------------------------------

    def read_policy(self) -> PolicyValues:
        asset = self.knowledge.repo.get(POLICY_ASSET_ID)
        if asset is None:
            log.warning("Expense policy asset missing; using defaults")
            return PolicyValues(found_asset=False)
        values = parse_policy(asset.content)
        # Stamp the revision actually read — the run is explainable against
        # the document as it was, and /api/v1/refs can dereference the pin.
        values.source_ref = (
            f"knowledge:{POLICY_ASSET_ID}@rev{getattr(asset, 'revision', 1)}"
        )
        return values

    def submit(
        self,
        employee: str,
        image_b64: str = "",
        filename: str = "",
        hint: str = "",
    ) -> ReimbursementRequest:
        """One run of the expense-reimbursement flow, synchronously."""
        image = base64.b64decode(image_b64) if image_b64 else None
        rid = "reimb_" + hashlib.sha256(
            f"{employee}:{filename}:{utcnow().isoformat()}".encode()
        ).hexdigest()[:10]
        journal = [
            _event("t1", "ReceiptSubmitted", "trigger",
                   f"receipt submitted by {employee}"
                   + (f" ({filename})" if filename else "")),
        ]

        # m1 · ModelCall — the governed operation: model, inputs, output.
        extraction = self.reader.read(image, filename, hint)
        journal.append(_event(
            "m1", "extract-receipt", "modelcall",
            f"{extraction.model}: {extraction.vendor} · "
            f"${extraction.total_usd:,.2f} · {extraction.category} · "
            f"confidence {extraction.confidence:.2f}",
        ))

        # x1 · transform — the flow gathers governed values; the rule never fetches.
        policy = self.read_policy()
        journal.append(_event(
            "x1", "read-policy", "transform",
            f"{policy.source_ref}: limit ${policy.limit_usd:,.2f} · "
            f"min confidence {policy.min_confidence:.2f} · "
            f"restricted {', '.join(policy.restricted)}",
        ))

        # r1 · Rule — a pure check over the gathered inputs.
        reasons = []
        if extraction.total_usd > policy.limit_usd:
            reasons.append(
                f"${extraction.total_usd:,.2f} exceeds the "
                f"${policy.limit_usd:,.2f} auto-approval limit"
            )
        if extraction.confidence < policy.min_confidence:
            reasons.append(
                f"extraction confidence {extraction.confidence:.2f} is below "
                f"{policy.min_confidence:.2f}"
            )
        if extraction.category in policy.restricted:
            reasons.append(f"category '{extraction.category}' is always reviewed")
        elevate = bool(reasons)
        journal.append(_event(
            "r1", "needs-elevation", "rule",
            ("elevate: " + "; ".join(reasons)) if elevate
            else "approve: within limit, allowed category, confident extraction",
        ))

        request = ReimbursementRequest(
            id=rid,
            employee=employee,
            submitted_at_iso=utcnow().isoformat(),
            filename=filename,
            vendor=extraction.vendor,
            date=extraction.date or utcnow().date().isoformat(),
            total_usd=extraction.total_usd,
            category=extraction.category,
            confidence=extraction.confidence,
            policy_ref=policy.source_ref,
            policy_limit_usd=policy.limit_usd,
            model=extraction.model,
            journal=journal,
        )

        if elevate:
            # k1 · Task — the run suspends; a stored record, not warm compute.
            task = self.tasks.create(Task(
                id=f"task_{rid}",
                kind="decision",
                title=f"Elevated reimbursement: {extraction.vendor} "
                      f"${extraction.total_usd:,.2f}",
                summary=(
                    "The needs-elevation rule routed this receipt for review: "
                    + "; ".join(reasons) + "."
                ),
                queue=REVIEW_QUEUE,
                priority="high" if extraction.total_usd > policy.limit_usd * 5
                         else "normal",
                flow_id=FLOW_ID,
                case_ref=rid,
                refs=[policy.source_ref],
                evidence=[
                    TaskEvidence(label="Vendor", value=extraction.vendor),
                    TaskEvidence(label="Amount",
                                 value=f"${extraction.total_usd:,.2f}"),
                    TaskEvidence(label="Category", value=extraction.category),
                    TaskEvidence(label="Extraction confidence",
                                 value=f"{extraction.confidence:.2f}"),
                    TaskEvidence(label="Auto-approval limit",
                                 value=f"${policy.limit_usd:,.2f} (governed)"),
                ],
                proposal=TaskProposal(
                    action="record-reimbursement",
                    target="reimbursements",
                    summary=f"Record {rid} as approved and schedule payout",
                ),
                resumes="this run resumes at record-reimbursement",
                created_at_iso=utcnow().isoformat(),
            ))
            request.task_id = task.id
            request.state = "pending"
            request.verdict_reason = "; ".join(reasons)
            request.journal.append(_event(
                "k1", "elevate-review", "task",
                f"suspended — task {task.id} raised to {REVIEW_QUEUE}",
            ))
        else:
            request.state = "approved"
            request.verdict_reason = "auto-approved within governed policy"
            request.journal.append(_event(
                "a1", "record-reimbursement", "action",
                f"approved · committed via Action Gateway (ledger led_{rid[-6:]})",
            ))
            request.journal.append(_event(
                "e1", "ReimbursementRecorded", "event", "emitted on commit",
            ))

        self.directory.put(request)
        log.info(
            "Reimbursement submitted",
            extra={
                "reimbursementId": rid, "state": request.state,
                "amount": extraction.total_usd, "actor": employee,
            },
        )
        return request

    # -- the resume ----------------------------------------------------------

    def apply_task_decision(self, task: Task) -> ReimbursementRequest | None:
        """The human decided; the suspended run resumes from the Task step."""
        if not task.case_ref.startswith("reimb_") or task.decision is None:
            return None
        request = self.directory.get(task.case_ref)
        if request is None or request.state != "pending":
            return None
        verdict = task.decision.verdict
        approved = verdict in ("approved", "corrected")
        request.state = "approved" if approved else "denied"
        request.decided_by = task.decision.decided_by
        request.decision_note = task.decision.note or task.decision.correction
        request.journal.append(_event(
            "k1", "elevate-review", "task",
            f"resumed — {verdict} by {task.decision.decided_by}",
        ))
        request.journal.append(_event(
            "a1", "record-reimbursement", "action",
            f"{request.state} · committed via Action Gateway "
            f"(ledger led_{request.id[-6:]})",
        ))
        request.journal.append(_event(
            "e1", "ReimbursementRecorded", "event", "emitted on commit",
        ))
        self.directory.put(request)
        log.info(
            "Reimbursement decided",
            extra={
                "reimbursementId": request.id, "state": request.state,
                "actor": task.decision.decided_by, "taskId": task.id,
            },
        )
        return request


def build_reimbursement_service(
    backend: str,
    project: str | None,
    collection: str,
    reader: ReceiptReader,
    knowledge,
    tasks: TaskService,
) -> ReimbursementService:
    directory: ReimbursementDirectory
    if backend == "firestore":
        directory = FirestoreReimbursementDirectory(project, collection)
    else:
        directory = InMemoryReimbursementDirectory()
    return ReimbursementService(directory, reader, knowledge, tasks)


# ---------------------------------------------------------------------------
# Seed history — decided rows only; live submissions create the pending ones
# (except two seeded pending rows paired with seeded tasks in tasks.py).
# ---------------------------------------------------------------------------


def _seed_rows() -> list[ReimbursementRequest]:
    def row(
        rid, employee, days_ago, vendor, date_offset, total, category,
        confidence, state, reason, task_id="", decided_by="", note="",
    ) -> ReimbursementRequest:
        from datetime import timedelta

        submitted = utcnow() - timedelta(days=days_ago)
        journal = [
            _event("t1", "ReceiptSubmitted", "trigger",
                   f"receipt submitted by {employee}"),
            _event("m1", "extract-receipt", "modelcall",
                   f"receipt-extraction: {vendor} · ${total:,.2f} · {category} "
                   f"· confidence {confidence:.2f}"),
            _event("x1", "read-policy", "transform",
                   f"knowledge:{POLICY_ASSET_ID}: limit $200.00 · "
                   "min confidence 0.80"),
        ]
        r = ReimbursementRequest(
            id=rid, employee=employee,
            submitted_at_iso=submitted.isoformat(),
            vendor=vendor,
            date=(submitted - timedelta(days=date_offset)).date().isoformat(),
            total_usd=total, category=category, confidence=confidence,
            state=state, verdict_reason=reason,
            policy_limit_usd=200.0, model="receipt-extraction",
            task_id=task_id, decided_by=decided_by, decision_note=note,
            journal=journal,
        )
        if state == "pending":
            r.journal.append(_event("r1", "needs-elevation", "rule",
                                    f"elevate: {reason}"))
            r.journal.append(_event("k1", "elevate-review", "task",
                                    f"suspended — task {task_id} raised"))
        else:
            auto = not task_id
            r.journal.append(_event(
                "r1", "needs-elevation", "rule",
                "approve: within limit" if auto else f"elevate: {reason}"))
            if task_id:
                r.journal.append(_event(
                    "k1", "elevate-review", "task",
                    f"resumed — decided by {decided_by}"))
            r.journal.append(_event(
                "a1", "record-reimbursement", "action",
                f"{state} · committed via Action Gateway (ledger led_{rid[-6:]})"))
            r.journal.append(_event("e1", "ReimbursementRecorded", "event",
                                    "emitted on commit"))
        return r

    return [
        row("reimb_seed_01", "pat@customer.example", 21, "corner-bistro", 1,
            64.20, "meals", 0.94, "approved",
            "auto-approved within governed policy"),
        row("reimb_seed_02", "sam@customer.example", 19, "transit-authority", 0,
            32.50, "travel", 0.97, "approved",
            "auto-approved within governed policy"),
        row("reimb_seed_03", "pat@customer.example", 16, "harbor-hotel", 2,
            612.00, "lodging", 0.91, "approved",
            "$612.00 exceeds the $200.00 auto-approval limit",
            task_id="task_hist_hotel", decided_by="dana@customer.example",
            note="Conference block rate confirmed against the event contract."),
        row("reimb_seed_04", "kim@customer.example", 14, "office-depot", 1,
            141.13, "supplies", 0.89, "approved",
            "auto-approved within governed policy"),
        row("reimb_seed_05", "sam@customer.example", 12, "vintners-cellar", 1,
            96.40, "alcohol", 0.92, "denied",
            "category 'alcohol' is always reviewed",
            task_id="task_hist_wine", decided_by="dana@customer.example",
            note="Client gift wine is out of policy §4.2; use the gifting desk."),
        row("reimb_seed_06", "kim@customer.example", 8, "cloudline-software", 0,
            49.00, "software", 0.96, "approved",
            "auto-approved within governed policy"),
        row("reimb_seed_07", "pat@customer.example", 6, "airport-taxi", 0,
            58.75, "travel", 0.58, "approved",
            "extraction confidence 0.58 is below 0.80",
            task_id="task_hist_taxi", decided_by="dana@customer.example",
            note="Amount matches the card feed; corrected vendor spelling."),
        # Two live pending rows, paired with seeded open tasks in tasks.py.
        row("reimb_seed_08", "sam@customer.example", 2, "harbor-hotel", 1,
            487.90, "lodging", 0.90, "pending",
            "$487.90 exceeds the $200.00 auto-approval limit",
            task_id="task_reimb_seed_08"),
        row("reimb_seed_09", "kim@customer.example", 0, "corner-bistro", 0,
            182.40, "meals", 0.61, "pending",
            "extraction confidence 0.61 is below 0.80",
            task_id="task_reimb_seed_09"),
    ]


def seed_reimbursements_if_empty(service: ReimbursementService) -> int:
    if service.directory.list():
        return 0
    rows = _seed_rows()
    for r in rows:
        service.directory.put(r)
    log.info("Seeded reimbursements table", extra={"count": len(rows)})
    return len(rows)
