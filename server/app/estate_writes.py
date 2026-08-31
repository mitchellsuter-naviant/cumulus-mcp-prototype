"""The outbound write plane: what Cumulus may change in a customer's estate.

`estate.py` pulls. This module is the only place that pushes, and it is a
sibling rather than a method on the connector for three reasons, each of which
is load-bearing rather than tidy:

1. `estate.py`'s import boundary is a test, and it earns its keep by being
   narrow. The writer wants its own.
2. A cursor and a write journal are different machinery with different
   lifetimes. A watermark is disposable — re-derive it and the worst case is
   a re-pull. A row saying "we told the ERP to release SO5037" is evidence,
   and it outlives the run that made it.
3. `pull_all` runs on a timer. It must never be one refactor away from a loop
   that writes.

**The table is the door.** `WRITABLE` names every write this system can make:
one row today, and a second is a decision somebody makes on purpose. It exists
because the estate does not check what we need checked — `erp.py` validates
that a status is in the picklist and stops there, so an ERP that will happily
move an order from `Pending Approval` straight to `Closed` means the legality
of a state change is written down *here* or nowhere. A generic `patch()` with
no table above it is the governance hole this module exists to close.

**The journal is write-ahead.** The intent is durable before the request
leaves, because a request that raises may still have landed. The states form
one arc, and it is the same arc the Gateway ledger tells beside it:

    intent ─┬─ applied ──── echoed     the ERP's own change came home
            │           └── diverged   it came home holding a third value
            │           └── unechoed   it never came home
            ├─ unverified              the PATCH did not raise; the re-read
            │                          did not agree. A Task, not a success.
            └─ failed                  the world said no. May have landed —
                                       which is why the intent went first.

`refused` has no row. A write the table turns down never left this process,
and the Gateway's ledger is already the record that somebody asked.

**The estate decides whether the write took**, never the 200. `apply()` re-reads
the row and compares the value; a door that answers cheerfully and changes
nothing settles `unverified`.

Nothing here is wired unless `ESTATE_WRITES_ENABLED` says so. Off — the
default, everywhere but a box where somebody meant it — means the writer holds
no callable that could reach a network at all, rather than a polite refusal in
front of one that could.
"""

from __future__ import annotations

import logging
import secrets
import threading
from dataclasses import dataclass, field as dataclass_field
from typing import Any, Callable, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, field_validator

from .estate import EstateConnector, EstateError
from .models import utcnow

log = logging.getLogger("cumulus.estate_writes")


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WritableTarget:
    """One permitted write, addressed the way the estate addresses it.

    `transitions` maps the value the estate currently holds to the values we
    may move it to. Keying on the *current* value rather than listing
    destinations is what makes the table a state machine instead of a
    permission: releasing an order is only legal from `Pending Approval`, and
    an order somebody already billed is not ours to touch.

    `action` is the `gateway.ACTIONS` id this target is written through, so a
    journal row and a ledger entry name the same event from two sides.
    """

    id: str
    app: str
    resource: str
    field: str
    action: str
    transitions: dict[str, tuple[str, ...]] = dataclass_field(
        default_factory=dict
    )

    def allows(self, observed: str, value: str) -> bool:
        return value in self.transitions.get(observed, ())

    def destinations(self, observed: str) -> tuple[str, ...]:
        return self.transitions.get(observed, ())


#: Every write Cumulus can make, in one screen. The demo's whole outbound
#: surface: a controller approves a sales order the CRM says is won and the
#: ERP still holds unreleased, and the order moves one step. Nothing else in
#: the estate is writable, and adding a row is a governance change with a
#: review attached — which is the point of it being a literal.
WRITABLE: tuple[WritableTarget, ...] = (
    WritableTarget(
        id="sales-order-status",
        app="erp",
        resource="salesOrders",
        field="status",
        action="release-sales-order",
        transitions={"Pending Approval": ("Pending Fulfillment",)},
    ),
)


def writable(target_id: str) -> WritableTarget | None:
    return next((t for t in WRITABLE if t.id == target_id), None)


# ---------------------------------------------------------------------------
# Refusals and rejections
# ---------------------------------------------------------------------------


class EstateWriteError(EstateError):
    """Anything that stopped an outbound write.

    An `EstateError` so a caller already handling the read side's failures
    handles these too — and split below, because the two halves mean opposite
    things to an operator: one of them may have changed their data.
    """


class EstateWriteRefused(EstateWriteError):
    """Cumulus said no. Nothing left this process; the estate is untouched."""


class EstateWritesDisabled(EstateWriteRefused):
    """`ESTATE_WRITES_ENABLED` is off, so there is no door to knock on."""


class EstateWriteRejected(EstateWriteError):
    """The estate said no — or could not be reached to say yes.

    Carries the estate's own status and message rather than a paraphrase: the
    Task an operator reads says "'status' must be one of: …" in the ERP's
    words, which is the sentence they can act on. `status` is 0 when the
    failure had no HTTP answer at all (a connect error, a timeout) — that is
    exactly the case where the write may have landed anyway.
    """

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"{status or 'no response'}: {message}")
        self.status = status
        self.message = message


# ---------------------------------------------------------------------------
# The journal
# ---------------------------------------------------------------------------

#: The arc, closed. Open to widening — the echo pass adds nothing new here
#: because its three endings were named before it was written — and closed to
#: typos, which is what the validator below is for: a misspelled state is a
#: row no dashboard will ever count and no operator will ever find.
WRITE_STATES: tuple[str, ...] = (
    "intent",
    "applied",
    "unverified",
    "failed",
    "echoed",
    "diverged",
    "unechoed",
)

#: States where the estate may hold our value and the graph does not know it
#: yet — what the delivery-side echo pass walks.
OPEN_STATES: frozenset[str] = frozenset({"applied", "unverified"})

DEFAULT_JOURNAL_COLLECTION = "estate_write_journal"


class EstateWriteRecord(BaseModel):
    """One thing we told a customer's system to do, and how it turned out.

    Machinery, not a Dataset (DOCTRINE.md: "machinery/operational state — flow
    run journals, the trigger index, queues — is not a Dataset"), so it lives
    in its own collection and stays out of `data_collections.COLLECTIONS`.
    The argument is `ConnectorState`'s, one step sharper: this cannot be
    derived from the runs, because the case it exists for is the case where
    the run *died* between the PATCH and its own bookkeeping. A row here with
    no run around it is not a leak — it is the only surviving statement that
    Cumulus changed somebody's data, and the first thing a human needs.

    `observed` and `value` are the two ends of the transition as the table
    approved it. `verified` is what the estate said afterwards, which is the
    same string on the happy path and the interesting one otherwise.
    """

    # Settling an entry is an assignment, so the closed vocabulary has to hold
    # on the way out as well as on the way in.
    model_config = ConfigDict(validate_assignment=True)

    id: str
    target: str  # WritableTarget.id
    app: str
    resource: str
    record_id: str
    field: str
    observed: str  # what the estate held when the decision was made
    value: str  # what we asked it to hold
    state: str = "intent"
    verified: str = ""  # what the re-read actually found
    #: The estate's `updatedAt` after the write — not on any sync wire shape
    #: today, and kept here because it is the cheapest evidence that the row
    #: we are looking at is the row we touched.
    estate_updated_at: str = ""
    action: str = ""  # the gateway Action, for the join to the ledger
    run_id: str = ""
    actor: str = ""
    intended_at: str = ""
    settled_at: str = ""
    error: str = ""

    @field_validator("state")
    @classmethod
    def _known_state(cls, value: str) -> str:
        if value not in WRITE_STATES:
            raise ValueError(
                f"`{value}` is not one of: {', '.join(WRITE_STATES)}"
            )
        return value


class EstateWriteJournal(Protocol):
    def put(self, record: EstateWriteRecord) -> EstateWriteRecord: ...
    def get(self, write_id: str) -> EstateWriteRecord | None: ...
    def list(self) -> list[EstateWriteRecord]: ...


class InMemoryEstateWriteJournal:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, EstateWriteRecord] = {}

    def put(self, record: EstateWriteRecord) -> EstateWriteRecord:
        with self._lock:
            self._items[record.id] = record.model_copy(deep=True)
        return record

    def get(self, write_id: str) -> EstateWriteRecord | None:
        with self._lock:
            found = self._items.get(write_id)
        # A copy, for `InMemoryConnectorStateStore`'s reason: a caller holding
        # an entry must not be able to settle it by assignment, because the
        # Firestore store has no way to behave that way and two backends that
        # disagree about aliasing is a bug that only shows up in production.
        return found.model_copy(deep=True) if found is not None else None

    def list(self) -> list[EstateWriteRecord]:
        with self._lock:
            return [r.model_copy(deep=True) for r in self._items.values()]


class FirestoreEstateWriteJournal:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore  # deferred: not needed in dev mode

        self._col = firestore.Client(project=project).collection(collection)

    def put(self, record: EstateWriteRecord) -> EstateWriteRecord:
        self._col.document(record.id).set(record.model_dump(mode="json"))
        return record

    def get(self, write_id: str) -> EstateWriteRecord | None:
        snap = self._col.document(write_id).get()
        if not snap.exists:
            return None
        try:
            return EstateWriteRecord.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable write journal entry", extra={"id": write_id})
            return None

    def list(self) -> list[EstateWriteRecord]:
        out: list[EstateWriteRecord] = []
        for doc in self._col.stream():
            try:
                out.append(EstateWriteRecord.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping write journal row", extra={"docId": doc.id})
        return out


def build_estate_write_journal(
    backend: str, project: str | None, collection: str
) -> EstateWriteJournal:
    return (
        FirestoreEstateWriteJournal(project, collection)
        if backend == "firestore"
        else InMemoryEstateWriteJournal()
    )


def entries_for(
    journal: EstateWriteJournal, target: str, record_id: str
) -> list[EstateWriteRecord]:
    """Everything this system has ever told the estate about one record.

    The idempotency question — "is that our change, or did somebody else move
    it?" — is answered from this list and the row's current value together.
    A scan rather than a query on purpose: the whole point of a closed table
    is that this collection stays small enough to read, and a composite index
    is a thing to add when there is something to index.
    """
    return [
        e for e in journal.list() if e.target == target and e.record_id == record_id
    ]


def awaiting_echo(journal: EstateWriteJournal) -> list[EstateWriteRecord]:
    """Writes the estate may be holding that the graph has not yet seen come
    home. What the delivery-side echo pass walks its rows against."""
    return [e for e in journal.list() if e.state in OPEN_STATES]


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------


def _write_id(target: str, record_id: str, run_id: str) -> str:
    """One row per run, not per attempt.

    A step with `retries=1` replays its whole body, and the second attempt is
    the same attempt — it must settle the row it opened rather than claim a
    second write. Two *runs* against one record are two decisions and get two
    rows, which is how a failure stays legible after somebody retries it an
    hour later.
    """
    return f"{target}:{record_id}:{run_id or secrets.token_hex(6)}"


class EstateWriter:
    """The one component allowed to change a customer's system of record.

    Borrows the connector's addresses — `record_url` knows the project and the
    namespace, and nothing here should learn them a second time — and takes
    its wire the way the connector does: `fetch` and `patch` are injectable,
    so the eval world drives a recorded estate with no network while the
    request-shaping, the table and the journal all run for real.

    An injected wire needs no `enabled`. The flag gates the *live* door, and a
    fake handed in at construction is not that door: `ESTATE_WRITES_ENABLED`
    stays false in every test and eval run, including the ones that write.
    """

    def __init__(
        self,
        connector: EstateConnector,
        journal: EstateWriteJournal,
        *,
        enabled: bool = False,
        fetch: Callable[[str], dict] | None = None,
        patch: Callable[[str, dict], dict] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.connector = connector
        self.journal = journal
        self.timeout = timeout
        # None rather than a refusing stub: "disabled" is the absence of a way
        # to reach the estate, and a reader of this object can see that.
        self._fetch = fetch or (self._http_get if enabled else None)
        self._patch = patch or (self._http_patch if enabled else None)
        if (self._patch is None) != (self._fetch is None):
            # The trap this closes: inject `patch` alone into an eval world
            # and every write settles `unverified` for a reason no test would
            # name out loud.
            raise ValueError(
                "an estate writer needs both halves of the wire — it verifies "
                "every write by re-reading the record, so one that cannot read "
                "can never say `applied`"
            )
        #: True when a PATCH from here would reach a real network. The eval
        #: suite asserts this is False, which is one of the three independent
        #: reasons a test run cannot write to a live estate.
        self.live = patch is None and enabled

    # -- the wire -----------------------------------------------------------

    def _url(self, app: str, resource: str, record_id: str) -> str:
        if not self.connector.registered:
            raise EstateWriteRefused("no estate registered (set ESTATE_PROJECT)")
        return self.connector.record_url(app, resource, record_id)

    def read(self, app: str, resource: str, record_id: str) -> dict:
        """One record, as the estate currently holds it.

        The write plane's own read, not the connector's: it is how a
        precondition is checked and how a write is verified, and both of those
        are this module's business. It delivers nothing and moves no cursor.
        """
        if self._fetch is None:
            raise EstateWritesDisabled(
                "estate writes are disabled (set ESTATE_WRITES_ENABLED)"
            )
        url = self._url(app, resource, record_id)
        try:
            payload = self._fetch(url)
        except Exception as exc:  # noqa: BLE001 — normalized just below
            raise _rejected(exc) from exc
        if not isinstance(payload, dict):
            raise EstateWriteRejected(0, f"GET {url}: expected a JSON object")
        return payload

    def patch(self, app: str, resource: str, record_id: str, body: dict) -> dict:
        """The wire, not the door.

        Nothing outside this module should call it: on its own it is exactly
        the generic `patch()` the table exists to prevent. `apply()` is the
        door, and it is the only caller that matters.
        """
        if self._patch is None:
            raise EstateWritesDisabled(
                "estate writes are disabled (set ESTATE_WRITES_ENABLED)"
            )
        url = self._url(app, resource, record_id)
        try:
            payload = self._patch(url, body)
        except Exception as exc:  # noqa: BLE001 — normalized just below
            raise _rejected(exc) from exc
        return payload if isinstance(payload, dict) else {}

    def _http_get(self, url: str) -> dict:
        response = httpx.get(url, timeout=self.timeout)
        if response.status_code >= 400:
            raise EstateWriteRejected(response.status_code, _message(response))
        return response.json()

    def _http_patch(self, url: str, body: dict) -> dict:
        """No `If-Match`, because the estate offers none.

        The concurrency guard is a value comparison instead — the precondition
        re-reads the row and the verification re-reads it again — which is
        weaker than an ETag and is what this estate actually supports. Writing
        a header it ignores would read like protection we do not have.
        """
        response = httpx.patch(url, json=body, timeout=self.timeout)
        if response.status_code >= 400:
            raise EstateWriteRejected(response.status_code, _message(response))
        return response.json()

    # -- the door -----------------------------------------------------------

    def apply(
        self,
        target: str,
        record_id: str,
        *,
        observed: str,
        value: str,
        run_id: str = "",
        actor: str = "",
    ) -> EstateWriteRecord:
        """Check the table, journal the intent, send it, then ask the estate.

        `observed` is what the caller's precondition read off the row a moment
        ago, and it is trusted here rather than re-fetched: deciding whether
        the record is in a state worth writing to is the flow's job (there are
        five answers and only one of them is "write"), and re-reading it here
        would be a second, quieter copy of that decision. What this method
        will not do is let an approved *decision* become an unapproved *move*
        — `observed → value` has to be a transition the table lists, whoever
        asked and however the row got that way.

        Returns the settled entry rather than raising on a bad verification:
        `unverified` is a Task for a human, and an exception escaping into a
        step that retries is a second PATCH.
        """
        spec = writable(target)
        if spec is None:
            raise EstateWriteRefused(
                f"`{target}` is not a writable target — "
                f"one of: {', '.join(t.id for t in WRITABLE)}"
            )
        if not spec.allows(observed, value):
            allowed = spec.destinations(observed)
            raise EstateWriteRefused(
                f"{spec.resource}.{spec.field} `{observed}` → `{value}` is not "
                f"a permitted transition — from `{observed}` this system may "
                + (f"only set: {', '.join(allowed)}" if allowed else "set nothing")
            )
        if self._patch is None:
            raise EstateWritesDisabled(
                "estate writes are disabled (set ESTATE_WRITES_ENABLED)"
            )
        # Before the journal, so a misconfigured deployment leaves no row
        # claiming an intent it never had the means to act on.
        self._url(spec.app, spec.resource, record_id)

        record = EstateWriteRecord(
            id=_write_id(target, record_id, run_id),
            target=target,
            app=spec.app,
            resource=spec.resource,
            record_id=record_id,
            field=spec.field,
            observed=observed,
            value=value,
            state="intent",
            action=spec.action,
            run_id=run_id,
            actor=actor,
            intended_at=utcnow().isoformat(),
        )
        self.journal.put(record)

        try:
            self.patch(spec.app, spec.resource, record_id, {spec.field: value})
        except Exception as exc:  # noqa: BLE001 — journalled, then re-raised
            self._settle(record, "failed", error=str(exc))
            raise

        # Only the declared field travels, so the response body is not read:
        # what the estate *says* it did and what it holds are the two things
        # this module refuses to conflate.
        try:
            after = self.read(spec.app, spec.resource, record_id)
        except EstateWriteError as exc:
            return self._settle(record, "unverified", error=str(exc))
        found = str(after.get(spec.field) or "")
        return self._settle(
            record,
            "applied" if found == value else "unverified",
            verified=found,
            estate_updated_at=str(after.get("updatedAt") or ""),
        )

    def _settle(self, record: EstateWriteRecord, state: str, **fields: Any):
        for key, value in fields.items():
            setattr(record, key, value)
        record.state = state
        record.settled_at = utcnow().isoformat()
        self.journal.put(record)
        if state != "applied":
            log.warning(
                "Estate write settled %s", state,
                extra={"writeId": record.id, "error": record.error},
            )
        return record


def _message(response: httpx.Response) -> str:
    """The estate's own sentence, if it sent one.

    `erp.py` answers `{"error": "…"}` on every refusal it means; anything else
    is a proxy or a crash, and the raw body truncated is more use than a
    guess at what it meant.
    """
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 — not JSON is itself the information
        return response.text[:200]
    if isinstance(payload, dict) and payload.get("error"):
        return str(payload["error"])
    return response.text[:200]


def _rejected(exc: Exception) -> EstateWriteRejected:
    """Normalize whatever the wire raised into one type with a status.

    The wire is injectable, so what comes back from it is somebody else's
    exception class — the fake estate's, `httpx`'s, or this module's own on
    the live path. All this needs from it is the status, and duck-typing that
    beats making every possible door import a base class from here.
    """
    if isinstance(exc, EstateWriteRejected):
        return exc
    status = getattr(exc, "status", 0)
    message = getattr(exc, "message", None) or str(exc)
    return EstateWriteRejected(status if isinstance(status, int) else 0, message)


def build_estate_writer(
    connector: EstateConnector,
    backend: str,
    project: str | None,
    collection: str,
    *,
    enabled: bool = False,
    timeout: float = 30.0,
) -> EstateWriter:
    return EstateWriter(
        connector,
        build_estate_write_journal(backend, project, collection),
        enabled=enabled,
        timeout=timeout,
    )
