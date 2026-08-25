"""Per-asset usage accounting: what the corpus is actually being used for.

A governed knowledge layer accumulates documents faster than it retires them,
and nothing in the system otherwise says which of them earn their place. This
records two events per asset:

  - `read`   — the full text was fetched, at an API boundary. Something
               consumed the document rather than merely matching it.
  - `cited`  — the document backed a claim in an `/ask` answer. The strongest
               signal available: it answered a real question.
  - `retrieved` — it surfaced as a real match for a search. Weak on its own,
               but it is the denominator: without it, "surfaces constantly and
               nobody opens it" is indistinguishable from "nobody ever finds
               it", and those need opposite fixes.

Counts are keyed by event *name* rather than held in fixed columns, so a new
event needs no schema change and no migration of existing rollups.

The storage shape deliberately mirrors api_keys.py, which already solved the
same problem for API keys: per-day rollups, buffered in memory, flushed
opportunistically from the request path. Cloud Run freezes instances between
requests, so a background timer may simply never run — the flush has to be
driven by traffic and by shutdown. Accounting must also never break the request
it belongs to, so every failure here is logged and swallowed.
"""

import logging
import threading
from contextvars import ContextVar
from datetime import datetime, timedelta
from typing import Protocol

from pydantic import BaseModel, Field

from .models import utcnow

log = logging.getLogger("cumulus.usage")

# Same thresholds as api_keys: a hot MCP client must not cost one Firestore
# write per request.
_FLUSH_AFTER_SECONDS = 30.0
_FLUSH_AFTER_EVENTS = 50

READ = "read"
CITED = "cited"
# An impression: the document was returned as a plausible answer to a search.
# Only ever recorded for hits that stand out from the ranking — see
# search.mark_weak_hits. A dense retriever scores the whole corpus, so counting
# every returned hit would give every document an impression on every search
# and the number would measure traffic rather than relevance.
RETRIEVED = "retrieved"


class AssetUsageDay(BaseModel):
    """One day of usage for one asset."""

    asset_id: str
    day: str = Field(description="UTC date, YYYY-MM-DD")
    events: dict[str, int] = Field(default_factory=dict)
    # Which protocol drove it: rest, mcp, or a2a. The console is a REST client,
    # so it is not distinguishable here by design — `actors` is what tells a
    # person from an agent, and it does so by identity rather than by guessing
    # from a user agent.
    surfaces: dict[str, int] = Field(default_factory=dict)
    actors: dict[str, int] = Field(default_factory=dict)
    last_used_at: datetime | None = None

    @property
    def total(self) -> int:
        return sum(self.events.values())


class AssetUsageSummary(BaseModel):
    """A window of usage for one asset, flattened for display."""

    asset_id: str
    reads: int = 0
    citations: int = 0
    impressions: int = 0
    total: int = 0
    surfaces: dict[str, int] = Field(default_factory=dict)
    actors: dict[str, int] = Field(default_factory=dict)
    last_used_at: datetime | None = None
    # Oldest to newest, one entry per day that saw traffic. Sparse on purpose:
    # zero-filling a 90-day window for every asset is a lot of nothing.
    days: list[AssetUsageDay] = Field(default_factory=list)

    @property
    def unused(self) -> bool:
        """Nothing consumed it. Surfacing in search does not count as use."""
        return self.reads == 0 and self.citations == 0

    @property
    def ignored(self) -> bool:
        """Found repeatedly and opened by nobody.

        Distinct from `undiscovered`, and the distinction is the point: this is
        a relevance or quality problem — the document ranks for queries it does
        not answer, or answers them badly. Rewrite or retitle it.
        """
        return self.impressions > 0 and self.reads == 0 and self.citations == 0

    @property
    def undiscovered(self) -> bool:
        """Never even surfaced. A findability problem, not a quality one.

        Nothing can read what search never returns, so improving the prose
        would change nothing here — the fix is tags, title, or taxonomy.
        """
        return self.impressions == 0 and self.reads == 0 and self.citations == 0


# ---- Caller context ---------------------------------------------------------
#
# REST and A2A handlers already hold a Principal, so they pass the caller
# explicitly. MCP tools do not: the middleware authenticates the request and
# puts the principal in the ASGI scope, but a `@mcp.tool()` function receives
# only its own arguments. A context variable bridges that gap without
# threading a parameter through every tool signature — and unlike a global it
# is correct under concurrency, since each task gets its own copy.

_caller: ContextVar[tuple[str, str] | None] = ContextVar(
    "cumulus_usage_caller", default=None
)


def set_caller(surface: str, actor: str):
    """Record who is calling for the duration of this task. Returns the token."""
    return _caller.set((surface, actor))


def reset_caller(token) -> None:
    try:
        _caller.reset(token)
    except ValueError:
        # Set in a different context than the one being reset; nothing to undo.
        pass


def current_caller() -> tuple[str, str]:
    return _caller.get() or ("unknown", "unknown")


# ---- Buffer -----------------------------------------------------------------


class _UsageBuffer:
    """Accumulates per-asset/per-day counts, flushed in batches."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[tuple[str, str], AssetUsageDay] = {}
        self._events = 0
        self._last_flush = utcnow()

    def add(self, asset_id: str, event: str, surface: str, actor: str) -> None:
        day = utcnow().date().isoformat()
        with self._lock:
            entry = self._pending.get((asset_id, day))
            if entry is None:
                entry = AssetUsageDay(asset_id=asset_id, day=day)
                self._pending[(asset_id, day)] = entry
            entry.events[event] = entry.events.get(event, 0) + 1
            entry.surfaces[surface] = entry.surfaces.get(surface, 0) + 1
            entry.actors[actor] = entry.actors.get(actor, 0) + 1
            entry.last_used_at = utcnow()
            self._events += 1

    def due(self) -> bool:
        with self._lock:
            if not self._pending:
                return False
            return (
                self._events >= _FLUSH_AFTER_EVENTS
                or (utcnow() - self._last_flush).total_seconds()
                >= _FLUSH_AFTER_SECONDS
            )

    def drain(self) -> list[AssetUsageDay]:
        with self._lock:
            drained = list(self._pending.values())
            self._pending.clear()
            self._events = 0
            self._last_flush = utcnow()
            return drained


# ---- Stores -----------------------------------------------------------------


class AssetUsageStore(Protocol):
    def record(self, asset_id: str, event: str, surface: str, actor: str) -> None: ...
    def usage(self, asset_id: str, days: int) -> AssetUsageSummary: ...
    def usage_all(self, days: int) -> dict[str, AssetUsageSummary]: ...
    def forget(self, asset_id: str) -> None: ...
    def flush(self) -> None: ...


def _cutoff(days: int) -> str:
    return (utcnow() - timedelta(days=days)).date().isoformat()


def _summarize(asset_id: str, rows: list[AssetUsageDay]) -> AssetUsageSummary:
    summary = AssetUsageSummary(asset_id=asset_id)
    for row in sorted(rows, key=lambda r: r.day):
        summary.reads += row.events.get(READ, 0)
        summary.citations += row.events.get(CITED, 0)
        summary.impressions += row.events.get(RETRIEVED, 0)
        summary.total += row.total
        for key, count in row.surfaces.items():
            summary.surfaces[key] = summary.surfaces.get(key, 0) + count
        for key, count in row.actors.items():
            summary.actors[key] = summary.actors.get(key, 0) + count
        if row.last_used_at is not None:
            summary.last_used_at = row.last_used_at
        summary.days.append(row)
    return summary


class _BaseUsageStore:
    """Buffering and the never-break-the-request contract, shared by backends."""

    def __init__(self) -> None:
        self._buffer = _UsageBuffer()

    def record(self, asset_id: str, event: str, surface: str, actor: str) -> None:
        self._buffer.add(asset_id, event, surface, actor)
        if self._buffer.due():
            try:
                self.flush()
            except Exception:
                # Usage accounting must never break the request it belongs to.
                log.exception("Usage flush failed", extra={"assetId": asset_id})


class InMemoryAssetUsageStore(_BaseUsageStore):
    def __init__(self) -> None:
        super().__init__()
        self._rows: dict[tuple[str, str], AssetUsageDay] = {}

    def usage(self, asset_id: str, days: int) -> AssetUsageSummary:
        self.flush()
        cutoff = _cutoff(days)
        return _summarize(
            asset_id,
            [
                row
                for (aid, day), row in self._rows.items()
                if aid == asset_id and day >= cutoff
            ],
        )

    def usage_all(self, days: int) -> dict[str, AssetUsageSummary]:
        self.flush()
        cutoff = _cutoff(days)
        grouped: dict[str, list[AssetUsageDay]] = {}
        for (asset_id, day), row in self._rows.items():
            if day >= cutoff:
                grouped.setdefault(asset_id, []).append(row)
        return {aid: _summarize(aid, rows) for aid, rows in grouped.items()}

    def forget(self, asset_id: str) -> None:
        self.flush()
        for key in [k for k in self._rows if k[0] == asset_id]:
            del self._rows[key]

    def flush(self) -> None:
        for entry in self._buffer.drain():
            existing = self._rows.get((entry.asset_id, entry.day))
            if existing is None:
                self._rows[(entry.asset_id, entry.day)] = entry.model_copy(deep=True)
                continue
            for key, count in entry.events.items():
                existing.events[key] = existing.events.get(key, 0) + count
            for key, count in entry.surfaces.items():
                existing.surfaces[key] = existing.surfaces.get(key, 0) + count
            for key, count in entry.actors.items():
                existing.actors[key] = existing.actors.get(key, 0) + count
            existing.last_used_at = entry.last_used_at


class FirestoreAssetUsageStore(_BaseUsageStore):
    """One document per asset per day, merged on write."""

    def __init__(self, project: str | None, collection: str) -> None:
        super().__init__()
        from google.cloud import firestore

        self._firestore = firestore
        self._client = firestore.Client(project=project)
        self._collection = self._client.collection(collection)

    def _read(self, cutoff: str, asset_id: str | None) -> list[AssetUsageDay]:
        query = self._collection.where(
            field_path="day", op_string=">=", value=cutoff
        )
        if asset_id is not None:
            query = query.where(
                field_path="asset_id", op_string="==", value=asset_id
            )
        rows: list[AssetUsageDay] = []
        for doc in query.stream():
            try:
                rows.append(AssetUsageDay.model_validate(doc.to_dict()))
            except Exception:
                log.exception(
                    "Skipping unparseable usage doc", extra={"docId": doc.id}
                )
        return rows

    def usage(self, asset_id: str, days: int) -> AssetUsageSummary:
        self.flush()
        return _summarize(asset_id, self._read(_cutoff(days), asset_id))

    def usage_all(self, days: int) -> dict[str, AssetUsageSummary]:
        self.flush()
        grouped: dict[str, list[AssetUsageDay]] = {}
        for row in self._read(_cutoff(days), None):
            grouped.setdefault(row.asset_id, []).append(row)
        return {aid: _summarize(aid, rows) for aid, rows in grouped.items()}

    def forget(self, asset_id: str) -> None:
        self.flush()
        try:
            for doc in self._collection.where(
                field_path="asset_id", op_string="==", value=asset_id
            ).stream():
                doc.reference.delete()
        except Exception:
            log.exception("Could not drop usage", extra={"assetId": asset_id})

    def flush(self) -> None:
        for entry in self._buffer.drain():
            payload = entry.model_dump(mode="json")
            # Counters are incremented server-side so two instances flushing
            # the same asset on the same day add up instead of overwriting
            # each other. A plain merge would lose whichever write landed
            # first.
            increments = {
                f"events.{k}": self._firestore.Increment(v)
                for k, v in entry.events.items()
            }
            increments |= {
                f"surfaces.{k}": self._firestore.Increment(v)
                for k, v in entry.surfaces.items()
            }
            increments |= {
                f"actors.{k}": self._firestore.Increment(v)
                for k, v in entry.actors.items()
            }
            doc_id = f"{entry.asset_id}_{entry.day}"
            try:
                reference = self._collection.document(doc_id)
                reference.set(
                    {
                        "asset_id": entry.asset_id,
                        "day": entry.day,
                        "last_used_at": payload["last_used_at"],
                    },
                    merge=True,
                )
                if increments:
                    reference.update(increments)
            except Exception:
                log.exception(
                    "Failed to persist usage", extra={"assetId": entry.asset_id}
                )


def build_asset_usage_store(
    backend: str, project: str | None, collection: str
) -> AssetUsageStore:
    if backend == "firestore":
        return FirestoreAssetUsageStore(project, collection)
    return InMemoryAssetUsageStore()
