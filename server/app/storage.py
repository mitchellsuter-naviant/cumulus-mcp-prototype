"""Asset repositories: in-memory (local dev) and Firestore (Cloud Run).

Embedding vectors live in their own repository rather than as a field on
`Asset`. Two reasons: a vector is ~6KB of float noise that would otherwise ride
along on every REST and MCP response, and it belongs to a *model*, not to the
document — swapping encoders should not rewrite the asset collection.
"""

import logging
import threading
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, Field

from .models import Asset, utcnow

log = logging.getLogger("cumulus.storage")


class AssetRepository(Protocol):
    def all(self) -> list[Asset]: ...
    def get(self, asset_id: str) -> Asset | None: ...
    def put(self, asset: Asset) -> None: ...
    def delete(self, asset_id: str) -> bool: ...


class InMemoryRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._assets: dict[str, Asset] = {}

    def all(self) -> list[Asset]:
        with self._lock:
            return list(self._assets.values())

    def get(self, asset_id: str) -> Asset | None:
        with self._lock:
            return self._assets.get(asset_id)

    def put(self, asset: Asset) -> None:
        with self._lock:
            self._assets[asset.id] = asset

    def delete(self, asset_id: str) -> bool:
        with self._lock:
            return self._assets.pop(asset_id, None) is not None


class FirestoreRepository:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore  # deferred: not needed for local dev

        self._client = firestore.Client(project=project)
        self._collection = self._client.collection(collection)

    def all(self) -> list[Asset]:
        assets = []
        for doc in self._collection.stream():
            try:
                assets.append(Asset.model_validate(doc.to_dict()))
            except Exception:
                # One malformed document should not blank the whole store.
                log.exception("Skipping unparseable document", extra={"docId": doc.id})
        return assets

    def get(self, asset_id: str) -> Asset | None:
        snap = self._collection.document(asset_id).get()
        if not snap.exists:
            return None
        return Asset.model_validate(snap.to_dict())

    def put(self, asset: Asset) -> None:
        self._collection.document(asset.id).set(asset.model_dump(mode="json"))

    def delete(self, asset_id: str) -> bool:
        ref = self._collection.document(asset_id)
        if not ref.get().exists:
            return False
        ref.delete()
        return True


def build_repository(
    backend: str, project: str | None, collection: str
) -> AssetRepository:
    if backend == "firestore":
        return FirestoreRepository(project, collection)
    if backend == "memory":
        return InMemoryRepository()
    raise ValueError(f"Unknown STORAGE_BACKEND: {backend}")


# ---- Embeddings -------------------------------------------------------------


class EmbeddingRecord(BaseModel):
    """One asset's vector, plus enough provenance to know when it is stale.

    `model` and `fingerprint` together decide whether a stored vector can still
    be used: a different encoder puts vectors in a different space, and edited
    text describes a different document. Either mismatch triggers a re-embed.
    """

    asset_id: str
    vector: list[float]
    model: str
    fingerprint: str
    updated_at: datetime = Field(default_factory=utcnow)


class EmbeddingRepository(Protocol):
    def all(self) -> list[EmbeddingRecord]: ...
    def put_many(self, records: list[EmbeddingRecord]) -> None: ...
    def delete(self, asset_id: str) -> None: ...


class InMemoryEmbeddingRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, EmbeddingRecord] = {}

    def all(self) -> list[EmbeddingRecord]:
        with self._lock:
            return list(self._records.values())

    def put_many(self, records: list[EmbeddingRecord]) -> None:
        with self._lock:
            for record in records:
                self._records[record.asset_id] = record

    def delete(self, asset_id: str) -> None:
        with self._lock:
            self._records.pop(asset_id, None)


class FirestoreEmbeddingRepository:
    # Firestore caps a batched write at 500 operations.
    _BATCH_LIMIT = 500

    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore  # deferred: not needed for local dev

        self._client = firestore.Client(project=project)
        self._collection = self._client.collection(collection)

    def all(self) -> list[EmbeddingRecord]:
        records = []
        for doc in self._collection.stream():
            try:
                records.append(EmbeddingRecord.model_validate(doc.to_dict()))
            except Exception:
                # A vector that will not parse is only a cache miss: the asset
                # gets re-embedded rather than the whole warm-up failing.
                log.warning("Discarding unparseable embedding", extra={"docId": doc.id})
        return records

    def put_many(self, records: list[EmbeddingRecord]) -> None:
        for start in range(0, len(records), self._BATCH_LIMIT):
            batch = self._client.batch()
            for record in records[start : start + self._BATCH_LIMIT]:
                batch.set(
                    self._collection.document(record.asset_id),
                    record.model_dump(mode="json"),
                )
            batch.commit()

    def delete(self, asset_id: str) -> None:
        self._collection.document(asset_id).delete()


def build_embedding_repository(
    backend: str, project: str | None, collection: str
) -> EmbeddingRepository:
    if backend == "firestore":
        return FirestoreEmbeddingRepository(project, collection)
    if backend == "memory":
        return InMemoryEmbeddingRepository()
    raise ValueError(f"Unknown STORAGE_BACKEND: {backend}")
