"""Minted API keys for machine clients (MCP configurations, integrations).

Why these exist alongside ADMIN_API_KEY: that single env-var key grants owner,
is shared by every client, cannot be revoked without a redeploy, and gives no
attribution in logs. Minted keys are per-client, individually revocable, capped
below owner, and carry a key id into every log line they produce.

Token format: `cumk_<key_id>_<secret>`.
The key id is public (safe to log and display); only a SHA-256 of the whole
token is stored, so a leaked datastore does not yield usable credentials. The
embedded id makes verification a single document read instead of a scan over
every key.
"""

# Annotations are deferred: these classes define a `list()` method, which would
# otherwise shadow the builtin in later annotations like `list[UsageDay]`.
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
from datetime import datetime, timedelta
from typing import Protocol

from pydantic import BaseModel, Field

from .models import utcnow

log = logging.getLogger("cumulus.apikeys")

TOKEN_PREFIX = "cumk"

# Usage is buffered in memory and flushed on these thresholds, so a hot MCP
# client does not cost one Firestore write per request.
_FLUSH_AFTER_SECONDS = 30.0
_FLUSH_AFTER_EVENTS = 50


class ApiKeyRecord(BaseModel):
    """Stored metadata for one minted key. Never contains the secret.

    `identity_uid` is optional only so that keys minted before identities
    existed still parse; such a key is dead on arrival (see
    `revoke_unattributed` and the authentication path in auth.py).
    """

    id: str
    name: str
    identity_uid: str | None = None
    display: str = Field(description="Masked token shown in the UI")
    token_hash: str = Field(description="SHA-256 of the full token, hex")
    created_at: datetime = Field(default_factory=utcnow)
    created_by: str = ""
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    revoked_by: str | None = None
    last_used_at: datetime | None = None
    request_count: int = 0

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= utcnow()

    @property
    def active(self) -> bool:
        return not self.revoked and not self.expired

    def public(
        self,
        identity_name: str = "",
        identity_kind: str = "",
        role: str = "",
    ) -> "ApiKeyPublic":
        """Authority is resolved from the holding identity, never stored here."""
        return ApiKeyPublic(
            id=self.id,
            name=self.name,
            identity_uid=self.identity_uid,
            identity_name=identity_name,
            identity_kind=identity_kind,
            role=role,
            display=self.display,
            created_at=self.created_at,
            created_by=self.created_by,
            expires_at=self.expires_at,
            revoked_at=self.revoked_at,
            revoked_by=self.revoked_by,
            last_used_at=self.last_used_at,
            request_count=self.request_count,
            status=(
                "revoked"
                if self.revoked
                else "expired"
                if self.expired
                else "unattributed"
                if self.identity_uid is None
                else "active"
            ),
        )


class ApiKeyPublic(BaseModel):
    """What the API returns: everything except the hash."""

    id: str
    name: str
    identity_uid: str | None
    identity_name: str = ""
    identity_kind: str = ""
    role: str = Field(description="Inherited from the holding identity")
    display: str
    created_at: datetime
    created_by: str
    expires_at: datetime | None
    revoked_at: datetime | None
    revoked_by: str | None
    last_used_at: datetime | None
    request_count: int
    status: str


class UsageDay(BaseModel):
    """One day of usage for one key."""

    key_id: str
    day: str = Field(description="UTC date, YYYY-MM-DD")
    count: int = 0
    endpoints: dict[str, int] = Field(default_factory=dict)
    last_used_at: datetime | None = None


class ApiKeyExistsError(Exception):
    pass


class ApiKeyNotFoundError(Exception):
    def __init__(self, key_id: str) -> None:
        super().__init__(f"API key not found: {key_id}")
        self.key_id = key_id


def mint_token() -> tuple[str, str, str, str]:
    """Return (token, key_id, token_hash, display) for a fresh credential."""
    key_id = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    token = f"{TOKEN_PREFIX}_{key_id}_{secret}"
    return token, key_id, hash_token(token), f"{TOKEN_PREFIX}_{key_id}_{secret[:4]}…"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_key_id(token: str) -> str | None:
    """Pull the key id out of a token, or None if it isn't one of ours."""
    parts = token.split("_", 2)
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX or not parts[1] or not parts[2]:
        return None
    return parts[1]


class _UsageBuffer:
    """Accumulates per-key/per-day counts, flushed in batches.

    Cloud Run can freeze an instance between requests, so flushing is driven
    opportunistically by the request path (and once at shutdown) rather than by
    a background timer that may never be scheduled.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[tuple[str, str], UsageDay] = {}
        self._events = 0
        self._last_flush = utcnow()

    def add(self, key_id: str, path: str) -> None:
        day = utcnow().date().isoformat()
        with self._lock:
            entry = self._pending.get((key_id, day))
            if entry is None:
                entry = UsageDay(key_id=key_id, day=day)
                self._pending[(key_id, day)] = entry
            entry.count += 1
            entry.endpoints[path] = entry.endpoints.get(path, 0) + 1
            entry.last_used_at = utcnow()
            self._events += 1

    def due(self) -> bool:
        with self._lock:
            if not self._pending:
                return False
            return (
                self._events >= _FLUSH_AFTER_EVENTS
                or (utcnow() - self._last_flush).total_seconds() >= _FLUSH_AFTER_SECONDS
            )

    def drain(self) -> list[UsageDay]:
        with self._lock:
            drained = list(self._pending.values())
            self._pending.clear()
            self._events = 0
            self._last_flush = utcnow()
            return drained


class ApiKeyDirectory(Protocol):
    def list(self) -> list[ApiKeyRecord]: ...
    def get(self, key_id: str) -> ApiKeyRecord | None: ...
    def create(
        self,
        name: str,
        identity_uid: str,
        created_by: str,
        expires_at: datetime | None,
    ) -> tuple[ApiKeyRecord, str]: ...
    def revoke(self, key_id: str, revoked_by: str) -> ApiKeyRecord: ...
    def revoke_for_identity(self, identity_uid: str, revoked_by: str) -> int: ...
    def revoke_unattributed(self, revoked_by: str) -> int: ...
    def verify(self, token: str) -> ApiKeyRecord | None: ...
    def record_usage(self, key_id: str, path: str) -> None: ...
    def usage(self, key_id: str, days: int) -> list[UsageDay]: ...
    def flush(self) -> None: ...


class _BaseDirectory:
    """Shared verify/record logic; storage specifics live in the subclasses."""

    def __init__(self) -> None:
        self._buffer = _UsageBuffer()

    def get(self, key_id: str) -> ApiKeyRecord | None:  # pragma: no cover - overridden
        raise NotImplementedError

    def verify(self, token: str) -> ApiKeyRecord | None:
        key_id = parse_key_id(token)
        if key_id is None:
            return None
        record = self.get(key_id)
        if record is None:
            return None
        # Constant-time: a timing signal here would leak the stored hash.
        if not hmac.compare_digest(record.token_hash, hash_token(token)):
            log.warning("API key hash mismatch", extra={"keyId": key_id})
            return None
        if record.revoked:
            log.warning("Revoked API key presented", extra={"keyId": key_id})
            return None
        if record.expired:
            log.warning("Expired API key presented", extra={"keyId": key_id})
            return None
        return record

    def record_usage(self, key_id: str, path: str) -> None:
        self._buffer.add(key_id, path)
        if self._buffer.due():
            try:
                self.flush()
            except Exception:
                # Usage accounting must never break the request it belongs to.
                log.exception("Usage flush failed", extra={"keyId": key_id})


class InMemoryApiKeyDirectory(_BaseDirectory):
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._keys: dict[str, ApiKeyRecord] = {}
        self._usage: dict[tuple[str, str], UsageDay] = {}

    def list(self) -> list[ApiKeyRecord]:
        with self._lock:
            return sorted(self._keys.values(), key=lambda k: k.created_at, reverse=True)

    def get(self, key_id: str) -> ApiKeyRecord | None:
        with self._lock:
            return self._keys.get(key_id)

    def create(
        self,
        name: str,
        identity_uid: str,
        created_by: str,
        expires_at: datetime | None,
    ) -> tuple[ApiKeyRecord, str]:
        token, key_id, token_hash, display = mint_token()
        record = ApiKeyRecord(
            id=key_id,
            name=name,
            identity_uid=identity_uid,
            display=display,
            token_hash=token_hash,
            created_by=created_by,
            expires_at=expires_at,
        )
        with self._lock:
            if key_id in self._keys:
                raise ApiKeyExistsError(key_id)
            self._keys[key_id] = record
        return record, token

    def revoke(self, key_id: str, revoked_by: str) -> ApiKeyRecord:
        with self._lock:
            record = self._keys.get(key_id)
            if record is None:
                raise ApiKeyNotFoundError(key_id)
            if record.revoked:
                return record
            record.revoked_at = utcnow()
            record.revoked_by = revoked_by
            return record

    def revoke_for_identity(self, identity_uid: str, revoked_by: str) -> int:
        return self._revoke_matching(
            lambda r: r.identity_uid == identity_uid, revoked_by
        )

    def revoke_unattributed(self, revoked_by: str) -> int:
        return self._revoke_matching(lambda r: r.identity_uid is None, revoked_by)

    def _revoke_matching(self, predicate, revoked_by: str) -> int:
        revoked = 0
        with self._lock:
            for record in self._keys.values():
                if record.revoked or not predicate(record):
                    continue
                record.revoked_at = utcnow()
                record.revoked_by = revoked_by
                revoked += 1
        return revoked

    def usage(self, key_id: str, days: int) -> list[UsageDay]:
        self.flush()
        cutoff = (utcnow().date() - timedelta(days=days - 1)).isoformat()
        with self._lock:
            return sorted(
                (u for (k, d), u in self._usage.items() if k == key_id and d >= cutoff),
                key=lambda u: u.day,
            )

    def flush(self) -> None:
        for entry in self._buffer.drain():
            with self._lock:
                existing = self._usage.get((entry.key_id, entry.day))
                if existing is None:
                    self._usage[(entry.key_id, entry.day)] = entry.model_copy(deep=True)
                else:
                    existing.count += entry.count
                    for path, n in entry.endpoints.items():
                        existing.endpoints[path] = existing.endpoints.get(path, 0) + n
                    existing.last_used_at = entry.last_used_at
                record = self._keys.get(entry.key_id)
                if record is not None:
                    record.request_count += entry.count
                    record.last_used_at = entry.last_used_at


class FirestoreApiKeyDirectory(_BaseDirectory):
    """Keys in one collection, usage rolled up per key per day in another.

    Daily rollups (rather than one document per request) keep write volume
    proportional to active keys instead of to traffic.
    """

    def __init__(self, project: str | None, keys_collection: str, usage_collection: str) -> None:
        super().__init__()
        from google.cloud import firestore  # deferred: not needed for local dev

        self._firestore = firestore
        self._client = firestore.Client(project=project)
        self._keys = self._client.collection(keys_collection)
        self._usage = self._client.collection(usage_collection)

    def list(self) -> list[ApiKeyRecord]:
        records = []
        for doc in self._keys.stream():
            try:
                records.append(ApiKeyRecord.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping unparseable API key", extra={"docId": doc.id})
        return sorted(records, key=lambda k: k.created_at, reverse=True)

    def get(self, key_id: str) -> ApiKeyRecord | None:
        snap = self._keys.document(key_id).get()
        if not snap.exists:
            return None
        try:
            return ApiKeyRecord.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable API key document", extra={"keyId": key_id})
            return None

    def create(
        self,
        name: str,
        identity_uid: str,
        created_by: str,
        expires_at: datetime | None,
    ) -> tuple[ApiKeyRecord, str]:
        token, key_id, token_hash, display = mint_token()
        record = ApiKeyRecord(
            id=key_id,
            name=name,
            identity_uid=identity_uid,
            display=display,
            token_hash=token_hash,
            created_by=created_by,
            expires_at=expires_at,
        )
        ref = self._keys.document(key_id)
        if ref.get().exists:
            raise ApiKeyExistsError(key_id)
        ref.set(record.model_dump(mode="json"))
        return record, token

    def revoke(self, key_id: str, revoked_by: str) -> ApiKeyRecord:
        ref = self._keys.document(key_id)
        snap = ref.get()
        if not snap.exists:
            raise ApiKeyNotFoundError(key_id)
        record = ApiKeyRecord.model_validate(snap.to_dict())
        if record.revoked:
            return record
        record.revoked_at = utcnow()
        record.revoked_by = revoked_by
        ref.update(
            {
                "revoked_at": record.revoked_at.isoformat(),
                "revoked_by": revoked_by,
            }
        )
        return record

    def revoke_for_identity(self, identity_uid: str, revoked_by: str) -> int:
        return self._revoke_matching(
            lambda r: r.identity_uid == identity_uid, revoked_by
        )

    def revoke_unattributed(self, revoked_by: str) -> int:
        return self._revoke_matching(lambda r: r.identity_uid is None, revoked_by)

    def _revoke_matching(self, predicate, revoked_by: str) -> int:
        revoked = 0
        now = utcnow()
        for record in self.list():
            if record.revoked or not predicate(record):
                continue
            self._keys.document(record.id).update(
                {"revoked_at": now.isoformat(), "revoked_by": revoked_by}
            )
            revoked += 1
        return revoked

    def usage(self, key_id: str, days: int) -> list[UsageDay]:
        self.flush()
        cutoff = (utcnow().date() - timedelta(days=days - 1)).isoformat()
        out = []
        for doc in self._usage.where(field_path="key_id", op_string="==", value=key_id).stream():
            try:
                entry = UsageDay.model_validate(doc.to_dict())
            except Exception:
                log.exception("Skipping unparseable usage doc", extra={"docId": doc.id})
                continue
            if entry.day >= cutoff:
                out.append(entry)
        return sorted(out, key=lambda u: u.day)

    def flush(self) -> None:
        Increment = self._firestore.Increment
        for entry in self._buffer.drain():
            doc_id = f"{entry.key_id}_{entry.day}"
            payload: dict = {
                "key_id": entry.key_id,
                "day": entry.day,
                "count": Increment(entry.count),
                "last_used_at": entry.last_used_at.isoformat() if entry.last_used_at else None,
            }
            # A nested dict, not `endpoints.<path>` keys: `set(merge=True)`
            # treats dotted keys as literal field names (only `update()` reads
            # them as paths), which left the endpoints map permanently empty.
            # Merge still applies per leaf, so sibling endpoints survive.
            if entry.endpoints:
                payload["endpoints"] = {
                    path.replace(".", "_"): Increment(n)
                    for path, n in entry.endpoints.items()
                }
            try:
                self._usage.document(doc_id).set(payload, merge=True)
                self._keys.document(entry.key_id).update(
                    {
                        "request_count": Increment(entry.count),
                        "last_used_at": entry.last_used_at.isoformat()
                        if entry.last_used_at
                        else None,
                    }
                )
            except Exception:
                log.exception("Failed to persist usage", extra={"keyId": entry.key_id})


def build_api_key_directory(
    backend: str, project: str | None, keys_collection: str, usage_collection: str
) -> ApiKeyDirectory:
    if backend == "firestore":
        return FirestoreApiKeyDirectory(project, keys_collection, usage_collection)
    return InMemoryApiKeyDirectory()
