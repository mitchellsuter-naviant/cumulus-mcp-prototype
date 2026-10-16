"""The correction set: every raw→corrected pair a clerk has ever produced.

Doctrine calls this a crown-jewel asset and, until this flow, nothing wrote
one. Every day the AP desk answered "no, that total is 4,182.50, not 4,132.50"
and every day the answer went into one invoice and nowhere else — the judgment
kept, in the curation ledger, and the *thing it taught* thrown away. This is
the set that keeps it: what the model read, what the human said instead, on
which document, decided by whom, when.

**Why it is its own Flow.** The doctrine's flywheel clause is explicit that a
correction flows back **decoupled, via an Event, not inline** — the business
path stays lean. So the accounts-payable run does not write this: it emits
`ExtractionCorrected` when a clerk's correction is accepted, and the Trigger
table answers that fact with a run of this flow. Three things follow from the
separation, and all three are the point. The invoice posts whether or not the
correction set is reachable. A run of this flow is a row in the fleet, so
"corrections are being captured" is something an operator can *see* rather than
infer. And the promotion question — should this teach the prompt? — has a place
to be asked later, by a Rule and a curation Task, instead of being smuggled
into the posting path as a side effect nobody voted for.

**What is deliberately not here: promotion.** Appending is automatic and
logged; nothing in this package edits the extraction prompt, the model config
or any other governed asset, and nothing here decides that N consistent
corrections mean anything. The doctrine reserves that for a declarative Rule
raising a curation Task, and a promotion path invented in the same lane as the
capture path would be a knowledge edit no human had governed. The set is the
prerequisite; the decision is somebody's, later.

**Role: knowledge.** These pairs make future runs smarter — as eval cases, as
tuning data, as the evidence behind a prompt revision. They live in the
`eval-datasets` Collection, which has declared them since before anything
wrote one.
"""

from __future__ import annotations

import logging
import secrets
import threading
from typing import Protocol

from pydantic import BaseModel

from ... import modelcall
from ...auth import flow_principal
from ...data_collections import retention_until
from ...gateway import ActionGateway
from ...models import utcnow

log = logging.getLogger("cumulus.corrections")

FLOW_ID = "ap-corrections"
DEFAULT_VERSION = 1

#: The fact this flow responds to. Emitted by `accounts-payable` when a clerk's
#: correction is accepted; bound here in `dispatch.TRIGGERS`.
CORRECTION_EVENT = "ExtractionCorrected"

#: The Collection these rows are governed by — declared long before anything
#: wrote to it, which is how the gap was visible in the first place.
CORRECTION_COLLECTION = "eval-datasets"

#: The Dataset within it. A ref in its own namespace: `dataset:` addresses map
#: content that is neither a graph Object nor a knowledge document, which is
#: exactly what an accumulating set of pairs is.
CORRECTION_DATASET = "ap-field-corrections"

APPEND_ACTION = "append-correction"

CORRECTIONS_FLOW = flow_principal(FLOW_ID)


class FieldCorrection(BaseModel):
    """One field, as the model read it and as a human left it.

    The pair is the asset; everything else on the row is what makes the pair
    usable later. `path` is the canonical fact path — the same string the
    verdict, the box on the page and the golden set use — so a set of these
    can be turned into eval cases without a translation layer, and
    `document_id` is what lets a tuning run fetch the page the pair is about.
    """

    id: str
    at_iso: str
    #: The canonical fact path (`invoice.total_usd`, `line_items[2].amount_usd`).
    path: str
    label: str = ""
    #: As read, and as left. Strings on both sides: a correction is what a
    #: person typed, and coercing "4,182.50" to a float here would make the
    #: pair a record of this code's parsing rather than of their answer.
    raw_value: str = ""
    corrected_value: str = ""
    #: What the flow had said about the field before the clerk answered, so a
    #: later pass can ask the question that matters: were the fields we flagged
    #: the fields that were actually wrong?
    status: str = ""
    confidence: float = 0.0
    #: The artifact the pair is about, and the run that read it.
    document_id: str = ""
    invoice_number: str = ""
    vendor_id: str = ""
    source_flow_id: str = ""
    source_flow_version: int = 0
    source_run_id: str = ""
    case_ref: str = ""
    task_id: str = ""
    decided_by: str = ""
    collection_id: str = CORRECTION_COLLECTION
    retention_until_iso: str = ""

    def ref(self) -> str:
        return f"dataset:{CORRECTION_DATASET}/{self.id}"


class CorrectionStore(Protocol):
    def append(self, record: FieldCorrection) -> FieldCorrection: ...
    def list(self, limit: int | None = None) -> list[FieldCorrection]: ...


class InMemoryCorrectionStore:
    """Append-only by omission: there is no update and no delete to call."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[FieldCorrection] = []

    def append(self, record: FieldCorrection) -> FieldCorrection:
        with self._lock:
            self._items.append(record)
        return record

    def list(self, limit: int | None = None) -> list[FieldCorrection]:
        with self._lock:
            items = list(reversed(self._items))  # newest first
        return items[:limit] if limit else items


class FirestoreCorrectionStore:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore  # deferred: not needed in dev mode

        self._col = firestore.Client(project=project).collection(collection)
        self._desc = firestore.Query.DESCENDING

    def append(self, record: FieldCorrection) -> FieldCorrection:
        self._col.document(record.id).set(record.model_dump(mode="json"))
        return record

    def list(self, limit: int | None = None) -> list[FieldCorrection]:
        query = self._col.order_by("at_iso", direction=self._desc)
        out: list[FieldCorrection] = []
        for doc in query.limit(limit or 500).stream():
            try:
                out.append(FieldCorrection.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping unparseable correction", extra={"docId": doc.id})
        return out


def build_correction_store(
    backend: str, project: str | None, collection: str
) -> CorrectionStore:
    if backend == "firestore":
        return FirestoreCorrectionStore(project, collection)
    return InMemoryCorrectionStore()


class CorrectionService:
    """The flow's service: the store, and the one write that reaches it."""

    def __init__(
        self,
        store: CorrectionStore,
        engine=None,
        gateway: ActionGateway | None = None,
    ) -> None:
        self.store = store
        self.engine = engine
        self.gateway = gateway
        if engine is not None:
            from .. import shipped_versions

            for number, module in sorted(shipped_versions(FLOW_ID).items()):
                engine.register(
                    FLOW_ID, modelcall.metered(module.build_body(self)),
                    version=number, default=(number == DEFAULT_VERSION),
                )

    # -- reads ---------------------------------------------------------------

    def list(self, limit: int | None = None) -> list[FieldCorrection]:
        return self.store.list(limit)

    def stats(self) -> dict:
        """What the set holds, for a console tile and for a person deciding
        whether there is enough here to promote anything."""
        rows = self.store.list()
        by_path: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for row in rows:
            by_path[row.path] = by_path.get(row.path, 0) + 1
            by_status[row.status or "unflagged"] = (
                by_status.get(row.status or "unflagged", 0) + 1
            )
        return {
            "corrections": len(rows),
            "documents": len({row.document_id for row in rows if row.document_id}),
            "by_path": by_path,
            "by_status": by_status,
        }

    # -- the write ------------------------------------------------------------

    def append(self, pairs: list[dict], context: dict, writer=None) -> dict:
        """Append one row per corrected field. The only write this flow makes.

        A pair whose two sides are equal is dropped rather than stored: the
        console sends the whole edited form back, so most of what arrives here
        is the clerk agreeing with the model, and a correction set full of
        `raw == corrected` would drown the pairs that carry the signal.
        """
        stamped = utcnow().isoformat()
        written: list[FieldCorrection] = []
        for pair in pairs:
            raw = str(pair.get("raw", "") or "")
            corrected = str(pair.get("corrected", "") or "")
            if raw == corrected:
                continue
            record = FieldCorrection(
                id=f"corr_{secrets.token_hex(6)}",
                at_iso=stamped,
                path=str(pair.get("path", "") or ""),
                label=str(pair.get("label", "") or ""),
                raw_value=raw,
                corrected_value=corrected,
                status=str(pair.get("status", "") or ""),
                confidence=float(pair.get("confidence", 0) or 0),
                # The artifact the pair is about. Usually the run's, because an
                # invoice is one document; an expense submission can be three
                # receipts in one envelope, so a pair may name its own and that
                # wins. Without this a correction to the taxi receipt would be
                # filed against the hotel folio, and a tuning run would fetch
                # the wrong page to learn from.
                document_id=str(
                    pair.get("document_id")
                    or context.get("document_id", "")
                    or ""
                ),
                invoice_number=str(context.get("invoice_number", "") or ""),
                vendor_id=str(context.get("vendor_id", "") or ""),
                source_flow_id=str(context.get("flow_id", "") or ""),
                source_flow_version=int(context.get("flow_version", 0) or 0),
                source_run_id=str(context.get("run_id", "") or ""),
                case_ref=str(context.get("case_ref", "") or ""),
                task_id=str(context.get("task_id", "") or ""),
                decided_by=str(context.get("decided_by", "") or ""),
                retention_until_iso=retention_until(CORRECTION_COLLECTION),
            )
            self.store.append(record)
            written.append(record)
            if writer is not None:
                # The Gateway builds its ledger entry from the writer's
                # ref-set, and this Action writes a Dataset rather than the
                # graph. Without this the entry would read "committed, nothing
                # written" — the one shape of governed write an auditor cannot
                # tell from a no-op.
                writer.note_write(record.ref())
            log.info(
                "Correction appended",
                extra={
                    "correctionId": record.id, "path": record.path,
                    "documentId": record.document_id,
                    "decidedBy": record.decided_by,
                    "runId": record.source_run_id,
                },
            )
        return {
            "appended": len(written),
            "paths": [record.path for record in written],
            "refs": [record.ref() for record in written],
        }


def build_correction_service(
    backend: str,
    project: str | None,
    collection: str,
    engine=None,
    gateway: ActionGateway | None = None,
) -> CorrectionService:
    return CorrectionService(
        build_correction_store(backend, project, collection), engine, gateway
    )
