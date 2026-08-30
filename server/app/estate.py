"""The estate connector: pull the customer's systems, deliver through the door.

Everything else in `server/app/` waits to be pushed to. This is the one module
that reaches out — and it is deliberately the dumbest one in the system. It
holds a cursor per feed, asks each source what changed since, renames the
fields the wire shape names, and POSTs the result to
`POST /api/v1/sources/deliver` like any other external system would. It does
not resolve, score, mint, or rank. A connector that started deciding which
record *is* which entity would be a second, undeclared copy of the resolution
policy, sitting below the Mapping where nobody could read it — so the import
boundary is a test (`test_estate.py`), not a promise: this module may not
reach `graph.py`, `resolution.py`, or `gateway.py`.

**Delivery goes through the HTTP door, never in-process.** Calling
`SyncService.deliver` directly would work and would be a lie: the door is where
authority is checked and where `Dispatcher.emit` appends the Event *before* the
run exists. Going around it would produce a run with no fact behind it, which
is the exact inversion `dispatch.py` was written to fix. So the connector is a
client of its own server, and "a completed pull is an Event" is satisfied at
the chokepoint rather than asserted here.

**Three things the estate's shape decides for us.** Its list routes filter
`updatedAt` strictly greater than `?updatedSince=` and order ascending, so the
next cursor is simply the largest `updatedAt` observed — no overlap window, and
a re-pull of the same window is safe anyway (the wire is idempotent and the
upserts are keyed). They are unpaged, so a delta arrives whole and *chunking*
is ours to do: a delivery is capped and split on `updatedAt` boundaries, so a
cursor that advances per acknowledged chunk never skips the tail of a
timestamp. And deletes are invisible to `updatedSince` — accepted for this
phase, and stated rather than papered over.

**Scheduling is a sourced capability** (DOCTRINE.md: no continuously-warm
component of our own). The deployed form is a managed scheduler POSTing
`/api/v1/estate/pull`; the endpoint is the port. The interval thread below is
the *dev adapter* for that port — off unless `ESTATE_PULL_INTERVAL_SECONDS`
is set, and never on in tests.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Protocol
from urllib.parse import quote, urlencode

import httpx
from pydantic import BaseModel, Field

from .models import utcnow
from .sync import EmployeeRecord, SyncRequest, TeamRecord

log = logging.getLogger("cumulus.estate")

#: `https://{project}-{app}.web.app` — the app is a *host*, not a path segment.
DEFAULT_URL_TEMPLATE = "https://{project}-{app}.web.app"
#: Discovery lives on the estate's admin site, under the same template.
DISCOVERY_APP = "admin"
DISCOVERY_PATH = "/admin/namespaces"

HRIS_WORKFORCE = "hris-workforce"

#: Records per delivery. The estate does not page, so this is the only bound on
#: how large one POST gets; a chunk is cut on an `updatedAt` boundary, never
#: mid-timestamp.
DEFAULT_CHUNK_SIZE = 500


class EstateError(RuntimeError):
    """The estate, or the door, said no.

    Raised rather than returned for `gateway.Refused`'s reason: a caller that
    forgot to look must not read a failed pull as an empty delta. A feed that
    raises has advanced no cursor and recorded why in its `ConnectorState`.
    """


# ---------------------------------------------------------------------------
# Cursor state
# ---------------------------------------------------------------------------


class ConnectorState(BaseModel):
    """Where one feed's cursor stands, and how the last pull went.

    Machinery, not a Dataset (DOCTRINE.md: "machinery/operational state — flow
    run journals, the trigger index, queues — is not a Dataset"), so it lives
    in its own collection and stays out of `data_collections.COLLECTIONS`.

    It cannot be derived from the runs the way `SyncService.sources()` is,
    which is the whole reason it exists: a pull returning an empty delta starts
    no run, and a pull that *failed* has no run at all. The cursor and the
    failure both have to survive the case where nothing ran.
    """

    feed: str
    # Per resource, because they move independently — an employee change must
    # not re-pull the jobs catalogue.
    watermarks: dict[str, str] = Field(default_factory=dict)
    last_pull_at: str = ""
    last_outcome: str = ""  # delivered · unchanged · failed
    last_error: str = ""
    next_pull_at: str = ""


class ConnectorStateStore(Protocol):
    def get(self, feed: str) -> ConnectorState | None: ...
    def put(self, state: ConnectorState) -> ConnectorState: ...
    def list(self) -> list[ConnectorState]: ...


class InMemoryConnectorStateStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, ConnectorState] = {}

    def get(self, feed: str) -> ConnectorState | None:
        with self._lock:
            found = self._items.get(feed)
        # A copy, so a caller holding a cursor cannot advance it by accident —
        # the Firestore store has no choice but to behave this way, and two
        # backends that disagree about aliasing is a bug that only shows up in
        # production.
        return found.model_copy(deep=True) if found is not None else None

    def put(self, state: ConnectorState) -> ConnectorState:
        with self._lock:
            self._items[state.feed] = state.model_copy(deep=True)
        return state

    def list(self) -> list[ConnectorState]:
        with self._lock:
            return [s.model_copy(deep=True) for s in self._items.values()]


class FirestoreConnectorStateStore:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore  # deferred: not needed in dev mode

        self._col = firestore.Client(project=project).collection(collection)

    def get(self, feed: str) -> ConnectorState | None:
        snap = self._col.document(feed).get()
        if not snap.exists:
            return None
        try:
            return ConnectorState.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable connector state", extra={"feed": feed})
            return None

    def put(self, state: ConnectorState) -> ConnectorState:
        self._col.document(state.feed).set(state.model_dump(mode="json"))
        return state

    def list(self) -> list[ConnectorState]:
        out: list[ConnectorState] = []
        for doc in self._col.stream():
            try:
                out.append(ConnectorState.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping state row", extra={"docId": doc.id})
        return out


def build_connector_state_store(
    backend: str, project: str | None, collection: str
) -> ConnectorStateStore:
    return (
        FirestoreConnectorStateStore(project, collection)
        if backend == "firestore"
        else InMemoryConnectorStateStore()
    )


# ---------------------------------------------------------------------------
# The feed registry
# ---------------------------------------------------------------------------

#: Resolve one referenced record to the row the estate holds for it:
#: `(resource, external_id) -> record`, empty when the estate has no such row.
Deref = Callable[[str, str], dict]


@dataclass(frozen=True)
class FeedSpec:
    """One (app, resource-family) pulled together and delivered as one shape.

    `build` receives the rows of *this chunk*, keyed by resource, plus the
    dereference callable — and does mechanical field renames only. The second
    argument is the one sanctioned exception (risk decision 2): when a delta
    row references a record the pulled page does not contain, the connector may
    fetch that one record to read the Mapping's *declared natural key* off it
    (an HRIS `managerId` is an internal id; the key an EmployeeRecord binds on
    is the work email). Every such dereference is named in the feed's Mapping
    notes. Anything beyond reading a declared key belongs in the flow.
    """

    feed: str  # the SyncRequest.source deliveries arrive under
    app: str  # estate app id from /admin/namespaces: crm|erp|office|hris|dms|psa…
    resources: tuple[str, ...]  # list routes pulled together, in delivery order
    build: Callable[[dict[str, list[dict]], Deref], SyncRequest]


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def team_slug(name: str) -> str:
    """A department name as the Team natural key the wire shape declares.

    Deterministic, and the only derivation the connector performs: `team_slug`
    and `parent_slug` are keys, so both ends of the org edge have to spell them
    the same way or the flow wires nothing.
    """
    return _SLUG_STRIP.sub("-", (name or "").lower()).strip("-")


def _text(row: dict, field: str) -> str:
    """One estate column as a string. It sends JSON nulls generously."""
    value = row.get(field)
    return "" if value is None else str(value)


def _build_hris_workforce(
    rows: dict[str, list[dict]], deref: Deref
) -> SyncRequest:
    """HRIS departments/jobs/employees → the existing TeamRecord/EmployeeRecord.

    Renames, and three id→key lookups that read the page first: `jobId` for the
    title, `departmentId` for the team slug, `managerId` and `headEmployeeId`
    for a work email. Nothing here decides anything — a department becomes a
    Team because the Mapping says departments are Teams, and a worker whose
    email another row already holds is delivered as it arrived. The duplicate
    is real HRIS drift and resolving it is B5's job, not a connector's.
    """
    departments = {d["id"]: d for d in rows.get("departments", [])}
    jobs = {j["id"]: j for j in rows.get("jobs", [])}
    workers = {e["id"]: e for e in rows.get("employees", [])}

    def off_page(cache: dict[str, dict], resource: str, record_id: str) -> dict:
        if not record_id:
            return {}
        if record_id not in cache:
            cache[record_id] = deref(resource, record_id)
        return cache[record_id]

    def slug_of(dept_id: str) -> str:
        return team_slug(
            _text(off_page(departments, "departments", dept_id), "name")
        )

    def email_of(worker_id: str) -> str:
        return _text(off_page(workers, "employees", worker_id), "workEmail")

    teams = [
        TeamRecord(
            external_id=_text(d, "id"),
            slug=team_slug(_text(d, "name")),
            name=_text(d, "name"),
            parent_slug=slug_of(_text(d, "parentDepartmentId")),
            lead_email=email_of(_text(d, "headEmployeeId")),
        )
        for d in rows.get("departments", [])
    ]

    people: list[EmployeeRecord] = []
    for e in rows.get("employees", []):
        email = _text(e, "workEmail").strip()
        if not email:
            # The natural key is the work email, so a row without one names no
            # entity. Skipped rather than landed under an empty key — which is
            # bookkeeping, not judgment: there is nothing to resolve.
            log.info("HRIS row has no work email", extra={"record": e.get("id")})
            continue
        people.append(EmployeeRecord(
            external_id=_text(e, "id"),
            email=email,
            name=_text(e, "fullName") or " ".join(filter(None, (
                _text(e, "legalFirstName"), _text(e, "legalLastName"),
            ))),
            title=_text(off_page(jobs, "jobs", _text(e, "jobId")), "title"),
            location=_text(e, "workLocation"),
            start_date=_text(e, "hireDate"),
            manager_email=email_of(_text(e, "managerId")),
            team_slug=slug_of(_text(e, "departmentId")),
        ))

    return SyncRequest(source=HRIS_WORKFORCE, teams=teams, employees=people)


#: The closed table of feeds, and the extension point every later app lands in
#: — same idiom as `dispatch.TRIGGERS` and `gateway.ACTIONS`. A sixth app is
#: one FeedSpec, one Mapping, and (if it needs one) one flow; the connector
#: does not change, and discovery decides which rows are live.
FEEDS: tuple[FeedSpec, ...] = (
    FeedSpec(
        feed=HRIS_WORKFORCE,
        app="hris",
        # Order matters on the wire, not in the pull: the flow needs teams to
        # exist before it can wire an employee into one.
        resources=("departments", "jobs", "employees"),
        build=_build_hris_workforce,
    ),
)


# ---------------------------------------------------------------------------
# The connector
# ---------------------------------------------------------------------------


class EstateConnector:
    """One registered estate, and a cursor per feed.

    `fetch` and `deliver` are injectable so the tests can drive a recorded
    estate without a network while still posting through the real HTTP door —
    the only thing worth faking here is the wire.
    """

    def __init__(
        self,
        store: ConnectorStateStore,
        *,
        project: str = "",
        url_template: str = DEFAULT_URL_TEMPLATE,
        namespace: str = "",
        deliver_url: str = "",
        api_key: str = "",
        feeds: tuple[FeedSpec, ...] = FEEDS,
        fetch: Callable[[str], dict] | None = None,
        deliver: Callable[[dict], dict] | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        interval_seconds: int = 0,
        timeout: float = 30.0,
    ) -> None:
        self.store = store
        self.project = project
        self.url_template = url_template or DEFAULT_URL_TEMPLATE
        self.namespace = namespace
        self.deliver_url = deliver_url
        self.api_key = api_key
        self.feeds = feeds
        self.chunk_size = max(1, chunk_size)
        self.interval_seconds = interval_seconds
        self.timeout = timeout
        self._fetch = fetch or self._http_fetch
        self._deliver = deliver or self._http_deliver
        self._discovery: dict | None = None
        self._loop: threading.Thread | None = None

    # -- registration -------------------------------------------------------

    @property
    def registered(self) -> bool:
        """No estate configured is the default outside a dev box, and it reads
        as "nothing to do" rather than as a misconfiguration."""
        return bool(self.project)

    def base_url(self, app: str) -> str:
        return self.url_template.format(project=self.project, app=app)

    def discovery_url(self) -> str:
        return f"{self.base_url(DISCOVERY_APP)}{DISCOVERY_PATH}"

    def resource_url(self, app: str, resource: str, since: str = "") -> str:
        url = f"{self.base_url(app)}/api/{self.namespace}/v1/{resource}"
        return f"{url}?{urlencode({'updatedSince': since})}" if since else url

    def record_url(self, app: str, resource: str, record_id: str) -> str:
        return (
            f"{self.base_url(app)}/api/{self.namespace}/v1/{resource}/"
            f"{quote(record_id, safe='')}"
        )

    def discover(self, *, refresh: bool = False) -> dict:
        """The namespace the estate says it runs, and the apps it runs it with.

        Nothing hardcodes the app list: the FEEDS registry proposes and this
        disposes, which is what makes a sixth app a data change.
        """
        if not self.registered:
            raise EstateError("no estate registered (set ESTATE_PROJECT)")
        if self._discovery is not None and not refresh:
            return self._discovery
        payload = self._get(self.discovery_url())
        spaces = payload.get("namespaces") or []
        wanted = self.namespace or str(payload.get("default") or "")
        found = next((n for n in spaces if n.get("id") == wanted), None)
        if found is None:
            raise EstateError(
                f"namespace `{wanted or '(none)'}` is not one of "
                f"{[n.get('id') for n in spaces]}"
            )
        self.namespace = str(found.get("id") or wanted)
        self._discovery = found
        return found

    def active_feeds(self) -> list[FeedSpec]:
        apps = set(self.discover().get("apps") or [])
        return [f for f in self.feeds if f.app in apps]

    def spec(self, feed: str) -> FeedSpec | None:
        return next((f for f in self.feeds if f.feed == feed), None)

    def state(self, feed: str) -> ConnectorState:
        return self.store.get(feed) or ConnectorState(feed=feed)

    # -- pulling ------------------------------------------------------------

    def pull(self, feed: str) -> dict:
        """One feed, start to finish: delta, chunks, deliveries, cursor.

        All-or-nothing across the family. A feed whose employees route failed
        while its departments route answered has learned nothing it can safely
        write down — advancing the departments cursor would strand the two
        halves of an org edge on opposite sides of a window.
        """
        spec = self.spec(feed)
        if spec is None:
            raise EstateError(f"no such feed: {feed}")
        if not self.registered:
            raise EstateError("no estate registered (set ESTATE_PROJECT)")
        if spec.feed not in {f.feed for f in self.active_feeds()}:
            raise EstateError(
                f"feed `{feed}` needs app `{spec.app}`, which namespace "
                f"`{self.namespace}` does not run"
            )

        state = self.state(feed)
        started = utcnow()
        try:
            rows = {
                resource: self._list(
                    spec.app, resource, state.watermarks.get(resource, "")
                )
                for resource in spec.resources
            }
        except EstateError as exc:
            self._stamp(state, started, "failed", str(exc))
            raise

        chunks = _chunks(rows, self.chunk_size)
        deref = self._deref(spec.app)
        delivered: list[dict] = []
        try:
            for ordinal, chunk in enumerate(chunks):
                delivered.append(self._deliver_chunk(spec, chunk, ordinal, deref))
                # Per acknowledged chunk, never per pull: a door that refuses
                # chunk three leaves chunks one and two banked.
                for resource, items in chunk.items():
                    if items:
                        state.watermarks[resource] = max(
                            _updated_at(r) for r in items
                        )
                self.store.put(state)
        except EstateError as exc:
            self._stamp(state, started, "failed", str(exc))
            raise

        outcome = "delivered" if chunks else "unchanged"
        self._stamp(state, started, outcome, "")
        log.info(
            "Estate feed pulled",
            extra={
                "feed": feed, "app": spec.app, "namespace": self.namespace,
                "records": sum(len(v) for v in rows.values()),
                "chunks": len(chunks), "outcome": outcome,
            },
        )
        return {
            "feed": feed,
            "app": spec.app,
            "namespace": self.namespace,
            "outcome": outcome,
            "pulled": {resource: len(items) for resource, items in rows.items()},
            "chunks": delivered,
            "watermarks": dict(state.watermarks),
            "error": "",
        }

    def pull_all(self) -> list[dict]:
        """Every active feed. The scheduler's target, and the loop's body.

        One red feed does not stop the rest: a failure is that feed's recorded
        state, and a dashboard fact (C3), not an exception that strands four
        healthy sources behind a fifth.
        """
        if not self.registered:
            return []
        outcomes: list[dict] = []
        for spec in self.active_feeds():
            try:
                outcomes.append(self.pull(spec.feed))
            except EstateError as exc:
                log.warning(
                    "Estate feed failed",
                    extra={"feed": spec.feed, "error": str(exc)},
                )
                outcomes.append({
                    "feed": spec.feed, "app": spec.app,
                    "namespace": self.namespace, "outcome": "failed",
                    "pulled": {}, "chunks": [], "watermarks": {},
                    "error": str(exc),
                })
        return outcomes

    def registration(self) -> dict:
        """What is registered, what the estate says, and where each feed stands.

        Never raises: an estate that is down is a thing the console has to be
        able to render, and a screen that 500s is a worse answer than a screen
        that says the discovery call failed.
        """
        detail: dict | None = None
        error = ""
        if self.registered:
            try:
                detail = self.discover()
            except EstateError as exc:
                error = str(exc)
        apps = set((detail or {}).get("apps") or ())
        return {
            "registered": self.registered,
            "project": self.project,
            "namespace": self.namespace,
            "url_template": self.url_template,
            "discovery_url": self.discovery_url() if self.registered else "",
            "deliver_url": self.deliver_url,
            # Whether, not what — a credential never leaves this process.
            "authenticated": bool(self.api_key),
            "pull_interval_seconds": self.interval_seconds,
            "namespace_detail": detail,
            "discovery_error": error,
            "feeds": [
                {
                    "feed": spec.feed,
                    "app": spec.app,
                    "resources": list(spec.resources),
                    "base_url": self.base_url(spec.app) if self.registered else "",
                    "active": spec.app in apps,
                    "state": self.state(spec.feed).model_dump(mode="json"),
                }
                for spec in self.feeds
            ],
        }

    # -- the dev scheduling adapter ----------------------------------------

    def start_interval_loop(self) -> bool:
        """The dev adapter for the Scheduling port. Off unless asked.

        Deployed, a managed scheduler POSTs `/api/v1/estate/pull` and nothing
        here runs — Cumulus keeps no warm component of its own. This exists so
        a laptop can watch the estate drift without a second terminal.
        """
        if self.interval_seconds <= 0 or not self.registered:
            return False
        if self._loop is not None and self._loop.is_alive():
            return True
        self._loop = threading.Thread(
            target=self._tick, name="estate-pull", daemon=True
        )
        self._loop.start()
        log.info(
            "Estate pull loop started (dev adapter)",
            extra={"intervalSeconds": self.interval_seconds},
        )
        return True

    def _tick(self) -> None:
        while True:
            time.sleep(self.interval_seconds)
            try:
                self.pull_all()
            except Exception:
                # A loop that dies on a transient outage is worse than no loop:
                # it stops silently and the estate looks quiet.
                log.exception("Estate pull tick failed")

    # -- internals ----------------------------------------------------------

    def _stamp(
        self, state: ConnectorState, started, outcome: str, error: str
    ) -> None:
        state.last_pull_at = started.isoformat()
        state.last_outcome = outcome
        state.last_error = error
        # Only the dev loop knows when the next tick is; under a managed
        # scheduler the schedule lives in the scheduler, so this stays empty
        # rather than claiming a cadence nothing here controls.
        state.next_pull_at = (
            (started + timedelta(seconds=self.interval_seconds)).isoformat()
            if self.interval_seconds > 0 else ""
        )
        self.store.put(state)

    def _list(self, app: str, resource: str, since: str) -> list[dict]:
        payload = self._get(self.resource_url(app, resource, since))
        rows = payload.get(resource)
        if not isinstance(rows, list):
            raise EstateError(
                f"{app}/{resource}: response has no `{resource}` list"
            )
        return rows

    def _deref(self, app: str) -> Deref:
        """One GET for a referenced record the pulled page does not hold.

        Memoized for the whole pull, not per chunk: a hundred people reporting
        to one manager is one GET, and so is that manager reappearing in the
        next chunk. A miss is empty rather than fatal — a manager who left
        between the pull and the dereference is a dangling reference, and the
        flow already counts those on the aggregate instead of failing the batch.
        """
        seen: dict[tuple[str, str], dict] = {}

        def fetch(resource: str, record_id: str) -> dict:
            if (resource, record_id) in seen:
                return seen[(resource, record_id)]
            try:
                found = self._get(self.record_url(app, resource, record_id))
            except EstateError as exc:
                log.info(
                    "Dereference missed",
                    extra={"resource": resource, "id": record_id,
                           "error": str(exc)},
                )
                found = {}
            seen[(resource, record_id)] = found if isinstance(found, dict) else {}
            return seen[(resource, record_id)]

        return fetch

    def _deliver_chunk(
        self, spec: FeedSpec, chunk: dict[str, list[dict]], ordinal: int,
        deref: Deref,
    ) -> dict:
        watermark = max(
            _updated_at(row) for items in chunk.values() for row in items
        )
        try:
            built = spec.build(chunk, deref)
        except Exception as exc:
            # A row the wire shape cannot hold is this feed's failure, recorded
            # against its state — not an unhandled 500 from a sync-now.
            raise EstateError(
                f"building `{spec.feed}` chunk {ordinal} failed: {exc}"
            ) from exc
        request = built.model_copy(update={
            "source": spec.feed,
            "watermark": watermark,
            # The same pull retried is the same delivery: `deliver` re-enters
            # the run this key already named and replays it.
            "idempotency_key": f"{spec.feed}:{watermark}:{ordinal}",
        })
        payload = request.model_dump(mode="json")
        try:
            response = self._deliver(payload)
        except Exception as exc:
            raise EstateError(
                f"delivery of `{spec.feed}` chunk {ordinal} failed: {exc}"
            ) from exc
        return {
            "idempotency_key": request.idempotency_key,
            "watermark": watermark,
            "records": sum(len(items) for items in chunk.values()),
            "runs": (response or {}).get("runs", []),
        }

    def _get(self, url: str) -> dict:
        try:
            payload = self._fetch(url)
        except EstateError:
            raise
        except Exception as exc:
            raise EstateError(f"GET {url} failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise EstateError(f"GET {url}: expected a JSON object")
        return payload

    def _http_fetch(self, url: str) -> dict:
        response = httpx.get(url, timeout=self.timeout)
        if response.status_code >= 400:
            raise EstateError(f"GET {url} → {response.status_code}")
        return response.json()

    def _http_deliver(self, payload: dict) -> dict:
        """The HTTP door, called the way any external feed would call it."""
        base = (self.deliver_url or "").rstrip("/")
        if not base:
            raise EstateError("no deliver URL configured")
        url = f"{base}/api/v1/sources/deliver"
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        response = httpx.post(
            url, json=payload, headers=headers, timeout=self.timeout
        )
        if response.status_code != 202:
            raise EstateError(
                f"POST {url} → {response.status_code}: {response.text[:200]}"
            )
        return response.json()


def _updated_at(row: dict) -> str:
    value = row.get("updatedAt")
    return "" if value is None else str(value)


def _chunks(
    rows: dict[str, list[dict]], size: int
) -> list[dict[str, list[dict]]]:
    """Split a delta into deliveries, cutting only where `updatedAt` changes.

    The rows are merged across resources and re-sorted ascending first, because
    the cut has to be a point in *time*: every row before it, in every
    resource, is delivered before every row after it, so a cursor advanced to
    the boundary skips nothing. Sorting also frees the connector from the
    estate's default ordering, which is by name until `?updatedSince=` is
    passed and by `updatedAt` after — a difference that would otherwise make
    the first pull behave unlike every pull after it.

    Rows sharing an `updatedAt` are never split, or the second one would fall
    behind a strictly-greater-than cursor and never be seen again.
    """
    merged = sorted(
        (
            (_updated_at(row), resource, index, row)
            for resource, items in rows.items()
            for index, row in enumerate(items)
        ),
        key=lambda item: (item[0], item[1], item[2]),
    )
    out: list[dict[str, list[dict]]] = []
    current: dict[str, list[dict]] = {}
    held = 0
    previous = None
    for stamp, resource, _index, row in merged:
        if held >= size and stamp != previous:
            out.append(current)
            current, held = {}, 0
        current.setdefault(resource, []).append(row)
        held += 1
        previous = stamp
    if current:
        out.append(current)
    return out


def build_estate_connector(
    backend: str,
    project: str | None,
    collection: str,
    *,
    estate_project: str,
    url_template: str,
    namespace: str,
    deliver_url: str,
    api_key: str,
    interval_seconds: int,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> EstateConnector:
    return EstateConnector(
        build_connector_state_store(backend, project, collection),
        project=estate_project,
        url_template=url_template,
        namespace=namespace,
        deliver_url=deliver_url,
        api_key=api_key,
        interval_seconds=interval_seconds,
        chunk_size=chunk_size,
    )
