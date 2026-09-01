"""The Document Store: submitted artifacts, with the regions an extraction read.

A Document is evidence, never a fact. It holds the bytes as submitted, a hash
that doubles as its identity, and the regions a model read values out of — so a
reviewer can be shown *where* a number came from, and a decision can be
re-checked against the paper it was made from two years later.

Two deliberate choices:

- **Identity is the content hash.** Submitting the same receipt twice lands one
  document, which is what makes a replayed Flow step idempotent and what makes
  duplicate-submission detection free.
- **Custody is `copy`, and the adapter is the document store.** The bytes live
  in the map under the `audit-documents` Collection's two-year retention. A
  Firestore document caps near 1MiB and base64 inflates by a third, so
  `documents_max_bytes` refuses anything larger rather than failing at write
  time. Past that size the same *capability* wants a blob-store adapter (GCS);
  the Collection, the retention and the regions do not change when it does,
  which is the point of naming the capability rather than the product.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import threading
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from . import data_collections, media
from .models import utcnow

log = logging.getLogger("cumulus.documents")

DOCUMENT_COLLECTION = "audit-documents"

DocumentKind = Literal[
    "receipt", "invoice", "purchase-order", "receiving-report", "other"
]


class Region(BaseModel):
    """Where on the page a value was read from.

    Normalized 0–1 coordinates so a viewer can draw the box at any render
    size without knowing the source resolution. `field` is a canonical fact
    path (`invoice.total_usd`, `line_items[0].amount`) — the same vocabulary
    the FlowContract and the golden sets use, so a bounding box, an assertion
    and a graph write all name the same thing.
    """

    field: str
    page: int = 1
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    text: str = ""
    confidence: float = 0.0


class DocumentRecord(BaseModel):
    id: str
    sha256: str
    filename: str = ""
    media_type: str = "application/octet-stream"
    byte_size: int = 0
    kind: DocumentKind = "other"
    pages: int = 1
    regions: list[Region] = Field(default_factory=list)
    # Base64 of the artifact. Absent when the submission carried no bytes
    # (a demo run driven by hints rather than a file).
    content_b64: str = ""
    collection_id: str = DOCUMENT_COLLECTION
    retention_until_iso: str = ""
    uploaded_by: str = ""
    uploaded_at_iso: str = Field(default_factory=lambda: utcnow().isoformat())
    # The runs that read this document. Appended to, never replaced: one
    # artifact can be evidence in several runs (a resubmission, an eval case).
    run_ids: list[str] = Field(default_factory=list)

    def ref(self) -> str:
        return f"document:{self.id}"

    def summary(self) -> "DocumentSummary":
        return DocumentSummary(
            id=self.id, sha256=self.sha256, filename=self.filename,
            media_type=self.media_type, byte_size=self.byte_size, kind=self.kind,
            pages=self.pages, region_count=len(self.regions),
            collection_id=self.collection_id,
            retention_until_iso=self.retention_until_iso,
            uploaded_by=self.uploaded_by, uploaded_at_iso=self.uploaded_at_iso,
            run_ids=self.run_ids, has_bytes=bool(self.content_b64),
        )


class DocumentSummary(BaseModel):
    """Everything but the bytes — what a list endpoint should ever return."""

    id: str
    sha256: str
    filename: str
    media_type: str
    byte_size: int
    kind: DocumentKind
    pages: int
    region_count: int
    collection_id: str
    retention_until_iso: str
    uploaded_by: str
    uploaded_at_iso: str
    run_ids: list[str]
    has_bytes: bool


class DocumentTooLargeError(Exception):
    def __init__(self, byte_size: int, limit: int) -> None:
        super().__init__(
            f"document is {byte_size} bytes; this deployment's document-store "
            f"adapter caps at {limit}"
        )
        self.byte_size = byte_size
        self.limit = limit


class DocumentStore(Protocol):
    def list(self) -> list[DocumentRecord]: ...
    def get(self, document_id: str) -> DocumentRecord | None: ...
    def put(self, record: DocumentRecord) -> DocumentRecord: ...
    def delete(self, document_id: str) -> bool: ...


class InMemoryDocumentStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, DocumentRecord] = {}

    def list(self) -> list[DocumentRecord]:
        with self._lock:
            return list(self._items.values())

    def get(self, document_id: str) -> DocumentRecord | None:
        with self._lock:
            return self._items.get(document_id)

    def put(self, record: DocumentRecord) -> DocumentRecord:
        with self._lock:
            self._items[record.id] = record
            return record

    def delete(self, document_id: str) -> bool:
        with self._lock:
            return self._items.pop(document_id, None) is not None


class FirestoreDocumentStore:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore  # deferred: not needed for local dev

        self._client = firestore.Client(project=project)
        self._col = self._client.collection(collection)

    def list(self) -> list[DocumentRecord]:
        out: list[DocumentRecord] = []
        for doc in self._col.stream():
            try:
                out.append(DocumentRecord.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping unparseable document", extra={"docId": doc.id})
        return out

    def get(self, document_id: str) -> DocumentRecord | None:
        snap = self._col.document(document_id).get()
        if not snap.exists:
            return None
        try:
            return DocumentRecord.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable document", extra={"documentId": document_id})
            return None

    def put(self, record: DocumentRecord) -> DocumentRecord:
        self._col.document(record.id).set(record.model_dump(mode="json"))
        return record

    def delete(self, document_id: str) -> bool:
        ref = self._col.document(document_id)
        if not ref.get().exists:
            return False
        ref.delete()
        return True


class DocumentService:
    def __init__(self, store: DocumentStore, max_bytes: int) -> None:
        self.store = store
        self.max_bytes = max_bytes

    def put_bytes(
        self,
        content_b64: str,
        *,
        filename: str = "",
        kind: DocumentKind = "other",
        uploaded_by: str = "",
        run_id: str = "",
        media_type: str = "",
        collection_id: str = DOCUMENT_COLLECTION,
    ) -> DocumentRecord:
        """Store an artifact, or re-recognize one already held.

        Idempotent on content: the id is the hash, so a resubmitted file adds a
        run reference rather than a second copy. That is what lets a Flow step
        that stores a document be replayed safely.
        """
        raw = base64.b64decode(content_b64) if content_b64 else b""
        if len(raw) > self.max_bytes:
            raise DocumentTooLargeError(len(raw), self.max_bytes)
        digest = hashlib.sha256(
            raw if raw else f"{filename}:{kind}:{uploaded_by}".encode()
        ).hexdigest()
        document_id = f"doc_{digest[:16]}"

        existing = self.store.get(document_id)
        if existing is not None:
            if run_id and run_id not in existing.run_ids:
                existing.run_ids.append(run_id)
                self.store.put(existing)
            return existing

        # Sniffed from the bytes, not read off the name: `filename` is
        # optional on a submission, and an extension is the submitter's claim
        # about the file rather than evidence of what it is.
        sniffed = media.sniff(raw, filename, media_type)
        record = DocumentRecord(
            id=document_id,
            sha256=digest,
            filename=filename,
            media_type=sniffed,
            byte_size=len(raw),
            kind=kind,
            pages=media.page_count(raw, sniffed),
            content_b64=content_b64,
            collection_id=collection_id,
            retention_until_iso=data_collections.retention_until(collection_id),
            uploaded_by=uploaded_by,
            run_ids=[run_id] if run_id else [],
        )
        log.info(
            "Document stored",
            extra={
                "documentId": document_id, "kind": kind,
                "bytes": len(raw), "collection": collection_id,
                "retentionUntil": record.retention_until_iso,
            },
        )
        return self.store.put(record)

    def set_regions(self, document_id: str, regions: list[Region]) -> DocumentRecord | None:
        """Attach what an extraction read, and where it read it.

        Replaces rather than appends: regions describe one extraction pass, and
        a re-extraction supersedes the previous one. The corrections a human
        makes are recorded on the *facts* (as claims with a human source), not
        by editing what the model claimed to see.
        """
        record = self.store.get(document_id)
        if record is None:
            return None
        record.regions = regions
        return self.store.put(record)

    def get(self, document_id: str) -> DocumentRecord | None:
        return self.store.get(document_id)

    def list(
        self, kind: DocumentKind | None = None, run_id: str | None = None
    ) -> list[DocumentSummary]:
        items = self.store.list()
        if kind is not None:
            items = [d for d in items if d.kind == kind]
        if run_id is not None:
            items = [d for d in items if run_id in d.run_ids]
        items.sort(key=lambda d: d.uploaded_at_iso, reverse=True)
        return [d.summary() for d in items]

    def expiring(self, before_iso: str = "") -> list[DocumentSummary]:
        """Documents whose retention horizon has passed.

        What a purge pass would read. Reporting it without acting on it is the
        honest position: the policy is declared and computed, the sweeper is
        not built, and pretending otherwise would be worse than saying so.
        """
        cutoff = before_iso or utcnow().isoformat()
        return [
            d.summary()
            for d in self.store.list()
            if d.retention_until_iso and d.retention_until_iso <= cutoff
        ]

    def stats(self) -> dict:
        items = self.store.list()
        by_kind: dict[str, int] = {}
        stored_bytes = 0
        for d in items:
            by_kind[d.kind] = by_kind.get(d.kind, 0) + 1
            stored_bytes += d.byte_size
        return {
            "documents": len(items),
            "stored_bytes": stored_bytes,
            "by_kind": by_kind,
            "expiring": len(self.expiring()),
            "with_regions": sum(1 for d in items if d.regions),
        }


def build_document_service(
    backend: str, project: str | None, collection: str, max_bytes: int
) -> DocumentService:
    store: DocumentStore
    if backend == "firestore":
        store = FirestoreDocumentStore(project, collection)
    else:
        store = InMemoryDocumentStore()
    return DocumentService(store, max_bytes)
