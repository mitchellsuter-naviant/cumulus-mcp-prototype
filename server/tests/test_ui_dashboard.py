"""The console's system-state screen: `/api/ui/v1/dashboard`.

One route, four questions, and not one stored number of its own. Everything the
screen shows is composed from a surface that already owns it, which makes the
properties worth defending here properties about *agreement* rather than about
arithmetic:

- **Every figure is answerable by a list one rail stop away.** The map's counts
  are the graph's own; the feed rows are the Sources rows; the queue depths are
  the task service's; the proposals are the workbench's. Each of those is
  asserted against the service it was composed from, because the failure this
  screen invites is a plausible number that agrees with nothing.
- **The two `unchanged`s stay apart.** A pull outcome of `unchanged` is the
  estate reporting no delta — nothing fetched, no run started. `unchanged_rows`
  counts rows that arrived byte-identical on a pull that *did* deliver. They are
  different facts sharing a word, they live in different fields, and a feed can
  have either without the other.
- **Records landed and entities held are different numbers on purpose.** Ten
  worker rows landing eight Employees is the natural key collapsing the HRIS's
  own duplicates, not a dropped delivery — and the screen carries both halves so
  it can say so rather than looking like a bug.
- **An empty world states itself.** Zeroes here have to be legible as "nothing
  has happened" rather than as "something is broken", and the fields that would
  otherwise fill a hole with a confident zero — spend basis, the last sweep —
  say which it is.
- **The persona gate is the Knowledge layer's.** None of this is scoped to one
  person's work, so an own-scoped persona is refused outright rather than shown
  a screen of zeros it would read as a clean bill of health.
"""

from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

import app.deps as deps
from app import personas, ui_api
from app.auth import Principal, principal_for
from app.estate import (
    HRIS_WORKFORCE,
    EstateConnector,
    InMemoryConnectorStateStore,
)
from app.eval_specs import (
    _IDENTITY_CRM,
    _IDENTITY_ERP_SALES,
    _IDENTITY_ERP_VENDORS,
)
from app.gateway import Refused
from app.sync import SyncRequest
from app.ui_api import get_ui_principal
from tests import demo_world
from tests import estate_fixtures as fx
from tests import world as test_world

ROUTE = "/api/ui/v1/dashboard"
SOURCES_ROUTE = "/api/ui/v1/sources"
IDENTITY_ROUTE = "/api/ui/v1/identity"


def _as(persona_id: str) -> dict[str, str]:
    return {"X-Cumulus-Persona": persona_id}


@pytest.fixture(scope="module")
def populated():
    """A world with history on every one of the four questions.

    Payables and expenses put decisions in the ledger and Tasks on desks; the
    workforce and calendar feeds put rows in the book; the answer key's
    counterparty deliveries plus a sweep put standing proposals in front of a
    steward. A world missing any of those would let a panel pass by being
    empty.
    """
    world = test_world.build()
    demo_world.seed_payables(world.payables, sync=world.sync)
    demo_world.seed_workforce(world.sync, world.graph)
    demo_world.seed_calendars(world.sync, world.graph)
    demo_world.seed_expenses(world.expenses)
    demo_world.seed_tasks(world.tasks)
    for delivery in (_IDENTITY_CRM, _IDENTITY_ERP_SALES, _IDENTITY_ERP_VENDORS):
        world.sync.deliver(
            SyncRequest.model_validate(delivery), actor="system:test"
        )
    world.sync.sweep_identities(actor="system:test")
    return world


@pytest.fixture()
def client(_client, populated):
    """Re-asserted per test, so a test that swaps in its own world cannot
    leave the rest of the module reading it."""
    test_world.install(populated)
    return _client


@pytest.fixture()
def empty_client(_client):
    """A world where nothing has ever arrived, run, or been decided."""
    test_world.fresh()
    return _client


@pytest.fixture()
def estate_client(_client, monkeypatch):
    """A registered estate with one feed pulled through the real door.

    Fresh rather than `populated`: a pull delivers through
    `POST /api/v1/sources/deliver`, and landing employees into the module's
    shared world would make every other test depend on the order this one ran.
    """
    world = test_world.fresh()

    def door(payload: dict) -> dict:
        response = _client.post("/api/v1/sources/deliver", json=payload)
        assert response.status_code == 202, response.text
        return response.json()

    connector = EstateConnector(
        InMemoryConnectorStateStore(),
        project="cumulus-demo-mitchell-suter",
        namespace="naviant",
        fetch=fx.FakeEstate().fetch,
        deliver=door,
    )
    monkeypatch.setattr(deps, "_estate_connector", connector)
    _client.connector = connector  # type: ignore[attr-defined] — test handle
    _client.world = world  # type: ignore[attr-defined]
    return _client


def _body(client, persona: str = "naviant-engineer") -> dict:
    resp = client.get(ROUTE, headers=_as(persona))
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1 · What the map holds
# ---------------------------------------------------------------------------


def test_the_map_panel_counts_what_the_graph_counts(client, populated):
    """The same pass the Ontology screen counts from, so a type reading N here
    reads N there. Composed rather than recounted is the whole claim."""
    body = _body(client)
    stats = populated.graph.stats()

    assert body["map"]["objects"] == stats["objects"]
    assert body["map"]["links"] == stats["links"]
    assert body["map"]["conflicted"] == stats["conflicted_objects"]

    # Every type in the payload carries the store's own instance count.
    counted = {
        t["name"]: t["instances"]
        for c in body["map"]["collections"] for t in c["types"]
    }
    for type_name, instances in stats["by_type"].items():
        assert counted[type_name] == instances, type_name


def test_every_object_type_lands_in_exactly_one_collection(client):
    """The partition governance hangs off. A type claimed by none would vanish
    from a panel that says it is the whole book; a type claimed by two would be
    counted twice into the totals under it."""
    from app.ontology import ONTOLOGY

    body = _body(client)
    placed = [
        t["name"] for c in body["map"]["collections"] for t in c["types"]
    ]
    assert sorted(placed) == sorted(o.name for o in ONTOLOGY.objects)
    assert len(placed) == len(set(placed))
    assert ui_api.UNGOVERNED not in {c["id"] for c in body["map"]["collections"]}

    # And each Collection's rollup is the sum of the rows inside it, so no
    # header can disagree with its own list.
    for collection in body["map"]["collections"]:
        assert collection["objects"] == sum(
            t["instances"] for t in collection["types"]
        )
        assert collection["conflicted"] == sum(
            t["conflicted"] for t in collection["types"]
        )


def test_a_collection_that_governs_no_object_type_is_left_out(client):
    """The knowledge library, the flow definitions and the task ledger govern
    containers that are not the graph. A zero row for them would be a count
    about the wrong store, read as "the corpus is empty"."""
    ids = {c["id"] for c in _body(client)["map"]["collections"]}
    assert "workforce" in ids and "ap-records" in ids
    for container_only in (
        "knowledge-library", "flow-definitions", "task-ledger",
        "vendor-profiles",
    ):
        assert container_only not in ids, container_only


def test_outstanding_claim_conflicts_are_counted_where_they_stand(client, populated):
    """A conflict is a live disagreement between two sources about one field,
    not a piece of history. It is counted per type because a badge on the type
    is where precedence has somewhere to be noticed."""
    body = _body(client)
    conflicted = {
        t["name"]: t["conflicted"]
        for c in body["map"]["collections"] for t in c["types"]
    }
    expected: dict[str, int] = {}
    for obj in populated.graph.store.objects():
        if obj.conflicts():
            expected[obj.type] = expected.get(obj.type, 0) + 1

    assert {k: v for k, v in conflicted.items() if v} == expected
    assert body["map"]["conflicted"] == sum(expected.values())


def test_the_invariant_report_is_a_platform_reading(client):
    """A TBox violation is a Naviant bug. The tenant gets every count above it
    and `null` where this would be — deliberately not a reassuring zero, which
    would be a claim rather than a withholding."""
    platform = _body(client, "naviant-support")
    tenant = _body(client, "customer-admin")

    assert platform["map"]["invariants"] == 0
    assert platform["map"]["invariant_kinds"] == {}
    assert tenant["map"]["invariants"] is None
    assert tenant["map"]["invariant_kinds"] is None
    # Withheld, not a smaller screen: the book itself reads identically.
    assert tenant["map"]["objects"] == platform["map"]["objects"]
    assert len(tenant["map"]["collections"]) == len(platform["map"]["collections"])


# ---------------------------------------------------------------------------
# 2 · Is the outside world arriving
# ---------------------------------------------------------------------------


def test_the_feed_rows_are_the_sources_rows(client):
    """Composed from `_source_rows`, never from a second walk of the runs.
    There is one answer to what a feed has delivered and the Sources screen
    owns it; two answers would be one screen arguing with another."""
    body = _body(client)
    rows = {
        r["source"]: r
        for r in client.get(SOURCES_ROUTE, headers=_as("naviant-engineer")).json()["sources"]
    }
    feeds = {f["source"]: f for f in body["arrivals"]["feeds"]}

    assert set(feeds) == set(rows)
    for source, feed in feeds.items():
        assert feed["records_landed"] == rows[source]["records_landed"], source
        assert feed["deliveries"] == rows[source]["deliveries"], source
        assert feed["created"] == rows[source]["created"], source
        assert feed["linked"] == rows[source]["linked"], source
        assert feed["unchanged_rows"] == rows[source]["unchanged"], source
        assert feed["last_status"] == rows[source]["last_status"], source


def test_a_pushed_feed_carries_no_cursor_and_says_that_is_the_arrangement(client):
    """`pulls: false` is a fact about the feed rather than a gap in the
    payload. Every seeded feed pushes on its own cadence, so there is no
    watermark to age and nothing here may imply there should be one."""
    for feed in _body(client)["arrivals"]["feeds"]:
        assert feed["pulls"] is False, feed["source"]
        assert feed["active"] is None
        assert feed["watermark"] == ""
        assert feed["watermark_age_seconds"] is None
        assert feed["last_pull_at"] == "" and feed["last_pull_outcome"] == ""


def test_records_landed_and_entities_held_are_both_reported(estate_client):
    """The screen's most misreadable gap, carried as two numbers so it can be
    explained instead of looking like a dropped delivery.

    Ten worker rows land eight Employees because a work email is the natural
    key and the live HRIS holds two of them twice. `records_landed` counts what
    the door was handed; `entities` counts what the book holds now.
    """
    estate_client.connector.pull(HRIS_WORKFORCE)
    feed = next(
        f for f in _body(estate_client)["arrivals"]["feeds"]
        if f["source"] == HRIS_WORKFORCE
    )

    # 7 departments + 10 workers handed to the door.
    assert feed["records_landed"] == 17
    # A bind mapping: the rows become the types below and nothing of their own,
    # which is what makes the two numbers comparable at all. A reify feed lands
    # its own type first and its row count says nothing about the book.
    assert feed["lands_as"] == ""
    assert feed["transport"] == "pull"
    entities = {e["type"]: e for e in feed["entities"]}
    assert set(entities) == {"Employee", "Team"}
    assert entities["Employee"]["instances"] == 8
    assert entities["Employee"]["instances"] < feed["records_landed"]
    # And the count agrees with the book the Ontology screen draws from.
    assert (
        entities["Employee"]["instances"]
        == estate_client.world.graph.stats()["by_type"]["Employee"]
    )
    # The Employee book is shared: the mail and calendar feeds resolve against
    # it without ever minting one, so `instances` is not this feed's output and
    # the row is entitled to say so.
    assert entities["Employee"]["feeds"] == 3
    assert entities["Team"]["feeds"] == 1


def test_a_bind_feed_reports_no_disposition_rather_than_three_zeros(
    estate_client,
):
    """`None` is not `0`, and here the difference is the whole reading.

    A bind mapping has no resolve step to link or mint with and no payload
    hash to suppress a redelivery by, so it never reports the three
    dispositions at all. Coercing an absent key to zero printed "0 minted · 0
    linked · 0 redelivered byte-identical" under a feed that had just landed
    every row it was handed — a confident answer to a question this contract
    does not ask.
    """
    estate_client.connector.pull(HRIS_WORKFORCE)
    feed = next(
        f for f in _body(estate_client)["arrivals"]["feeds"]
        if f["source"] == HRIS_WORKFORCE
    )
    assert feed["records_landed"] == 17
    assert feed["created"] is None
    assert feed["linked"] is None
    assert feed["unchanged_rows"] is None

    # And a reify feed that *does* run a resolver reports real numbers, so the
    # absence above is a property of the contract rather than of the payload.
    estate_client.connector.pull("erp-vendors")
    vendors = next(
        f for f in _body(estate_client)["arrivals"]["feeds"]
        if f["source"] == "erp-vendors"
    )
    assert vendors["created"] is not None and vendors["created"] > 0
    assert vendors["linked"] == 0
    assert vendors["unchanged_rows"] == 0


def test_received_and_landed_are_two_numbers_because_they_are_two_facts(
    estate_client,
):
    """A reify feed keeps a fraction of what it is handed, and the screen has
    to be able to say both halves.

    `landed` means new landing records on a reify feed and every bound row on
    a bind feed, so a total summed from it alone under-reported the estate's
    traffic by a third. `records` is what the delivery carried.
    """
    estate_client.connector.pull("psa-book")
    feed = next(
        f for f in _body(estate_client)["arrivals"]["feeds"]
        if f["source"] == "psa-book"
    )
    assert feed["lands_as"] == "SourceRecord"
    assert feed["records"] > feed["records_landed"]

    # A bind feed keeps everything it was handed, which is why the two numbers
    # look interchangeable until a reify feed is beside them.
    estate_client.connector.pull(HRIS_WORKFORCE)
    workforce = next(
        f for f in _body(estate_client)["arrivals"]["feeds"]
        if f["source"] == HRIS_WORKFORCE
    )
    assert workforce["records"] == workforce["records_landed"] == 17


def test_a_reify_feed_names_the_type_its_rows_became(estate_client):
    """The destination line said "the book now holds Vendor 13" over a feed
    whose rows had become `SourceRecord`s — the resolution target printed under
    a heading about landing. Both halves are carried so the row can say "lands
    as X · resolves against Y", and a contract that resolves nothing still has
    a destination to name.
    """
    estate_client.connector.pull("hris-employment-events")
    ledger = next(
        f for f in _body(estate_client)["arrivals"]["feeds"]
        if f["source"] == "hris-employment-events"
    )
    # Resolves nothing by declaration — and still lands somewhere, which is
    # what kept this row's destination line from being suppressed entirely.
    assert ledger["entities"] == []
    assert ledger["landed_type"]["type"] == "SourceRecord"
    assert ledger["landed_type"]["instances"] > 0
    # Four contracts land as `SourceRecord`, so the book is shared and the row
    # is entitled to say so rather than claim the count as its own.
    assert ledger["landed_type"]["feeds"] > 1

    # A bind mapping lands nothing of its own, and says that with `None`.
    estate_client.connector.pull(HRIS_WORKFORCE)
    workforce = next(
        f for f in _body(estate_client)["arrivals"]["feeds"]
        if f["source"] == HRIS_WORKFORCE
    )
    assert workforce["lands_as"] == "" and workforce["landed_type"] is None


def test_a_type_fed_by_more_than_one_source_says_so(client):
    """Two feeds resolve onto the counterparty book. Reporting its size on each
    of their rows without saying it is shared would invite reading one feed's
    row as that feed's output."""
    from app.mappings import MAPPINGS

    shared = {
        t for m in MAPPINGS for t in m.resolves_to
        if sum(1 for other in MAPPINGS if t in other.resolves_to) > 1
    }
    assert "Company" in shared

    by_source = {m.source: m for m in MAPPINGS}
    for feed in _body(client)["arrivals"]["feeds"]:
        for entity in feed["entities"]:
            expected = sum(
                1 for m in MAPPINGS if entity["type"] in m.resolves_to
            )
            assert entity["feeds"] == expected, (feed["source"], entity["type"])
            assert (entity["feeds"] > 1) == (entity["type"] in shared)
        # And the reify/bind distinction rides the row, because it decides
        # whether `records_landed` is comparable to what the book holds.
        mapping = by_source.get(feed["source"])
        assert feed["lands_as"] == (mapping.lands_as if mapping else "")


def test_the_two_unchangeds_are_different_fields(estate_client):
    """A pull that found no delta and a row that arrived byte-identical share a
    word and are opposite kinds of event. The first fetches nothing and starts
    no run; the second only happens on a pull that delivered.

    Asserted by producing one without the other: the second pull of the same
    window is `unchanged` at the connector while the row counter it did not
    touch stays where the first pull left it.
    """
    estate_client.connector.pull(HRIS_WORKFORCE)
    delivered = next(
        f for f in _body(estate_client)["arrivals"]["feeds"]
        if f["source"] == HRIS_WORKFORCE
    )
    assert delivered["last_pull_outcome"] == "delivered"
    assert delivered["records_landed"] > 0

    estate_client.connector.pull(HRIS_WORKFORCE)
    quiet = next(
        f for f in _body(estate_client)["arrivals"]["feeds"]
        if f["source"] == HRIS_WORKFORCE
    )
    assert quiet["last_pull_outcome"] == "unchanged"
    assert quiet["last_pull_error"] == ""
    # Nothing was delivered, so nothing was redelivered: the row counter is
    # untouched by an outcome that shares its name.
    assert quiet["unchanged_rows"] == delivered["unchanged_rows"]
    assert quiet["records_landed"] == delivered["records_landed"]
    assert quiet["deliveries"] == delivered["deliveries"]


def test_a_watermark_has_an_age_and_never_pulled_has_none(estate_client):
    """`None` rather than a large number: "never" and "a long time ago" are
    opposite readings of a feed and only the second is about staleness."""
    before = {
        f["source"]: f for f in _body(estate_client)["arrivals"]["feeds"]
    }
    assert before[HRIS_WORKFORCE]["pulls"] is True
    assert before[HRIS_WORKFORCE]["watermark"] == ""
    assert before[HRIS_WORKFORCE]["watermark_age_seconds"] is None
    assert before[HRIS_WORKFORCE]["last_pull_age_seconds"] is None

    estate_client.connector.pull(HRIS_WORKFORCE)
    after = next(
        f for f in _body(estate_client)["arrivals"]["feeds"]
        if f["source"] == HRIS_WORKFORCE
    )
    assert after["watermark_age_seconds"] is not None
    assert after["last_pull_age_seconds"] is not None
    # The cursor is the furthest of the resources', which is what the next pull
    # asks from — the same one the Sources row shows.
    pull = next(
        r["pull"]
        for r in estate_client.get(SOURCES_ROUTE).json()["sources"]
        if r["source"] == HRIS_WORKFORCE
    )
    assert after["watermark"] == max(pull["watermarks"].values())


def test_a_failed_pull_is_named_where_a_run_derived_list_would_be_silent(
    estate_client,
):
    """A pull that never reached the door left no run behind. A panel built
    from runs alone would draw this feed as simply quiet."""
    from app.estate import EstateError

    estate_client.connector._fetch = fx.FakeEstate(
        fails={("hris", "employees")}
    ).fetch
    with pytest.raises(EstateError):
        estate_client.connector.pull(HRIS_WORKFORCE)

    arrivals = _body(estate_client)["arrivals"]
    feed = next(f for f in arrivals["feeds"] if f["source"] == HRIS_WORKFORCE)
    assert feed["last_pull_outcome"] == "failed"
    assert "employees" in feed["last_pull_error"]
    assert feed["last_status"] == ""  # no run to have a status
    assert arrivals["failing"] == [HRIS_WORKFORCE]


def test_feeds_under_no_contract_are_named_once_and_read_twice(client):
    """Rows arriving under no declared Mapping is a finding both panels need,
    and the governance panel is handed the arrivals panel's own list — so the
    two cannot report different numbers of the same thing."""
    body = _body(client)
    named = body["arrivals"]["without_contract"]
    feeds = {f["source"]: f for f in body["arrivals"]["feeds"]}

    assert named == sorted(s for s, f in feeds.items() if not f["contract_id"])
    assert body["governance"]["feeds_without_contract"] == named
    # The AP fixture's three deliveries: systems the estate does not run, so
    # the map lost their Mappings when the mock world died.
    assert set(named) == {
        "procurement-coupa", "legacy-ap-extract", "wms-receiving",
    }
    assert all(feeds[s]["records_landed"] > 0 for s in named)


# ---------------------------------------------------------------------------
# 3 · Is judgment flowing
# ---------------------------------------------------------------------------


def test_the_queue_depths_are_the_task_services_own(client, populated):
    """Every depth is answerable by the list My Tasks draws."""
    body = _body(client)
    stats = populated.tasks.stats()

    assert body["judgment"]["open_tasks"] == stats.open
    assert body["judgment"]["high_priority"] == stats.high_priority
    depths = {q["queue"]: q["open"] for q in body["judgment"]["queues"]}
    for queue, open_count in stats.by_queue.items():
        assert depths[queue] == open_count, queue
    assert sum(depths.values()) == stats.open
    # And the kind split within a queue sums to that queue's depth.
    for queue in body["judgment"]["queues"]:
        assert sum(queue["by_kind"].values()) == queue["open"], queue["queue"]


def test_a_declared_desk_with_nothing_on_it_still_gets_a_row(client):
    """A desk that has gone quiet and a desk nobody declared are different
    facts. Building the list from open Tasks alone would make them the same."""
    queues = {q["queue"]: q for q in _body(client)["judgment"]["queues"]}
    declared = {q for p in personas.ALL_PERSONAS for q in p.queues}
    assert declared <= set(queues)
    idle = [q for q in queues.values() if q["open"] == 0]
    assert idle, "no idle desk in this world to assert the empty state on"
    for queue in idle:
        assert queue["by_kind"] == {}
        assert queue["oldest_open_at_iso"] == ""
        assert queue["oldest_open_age_seconds"] is None


def test_each_queue_names_who_is_bound_to_it(client):
    """Read off the roster, so a rebinding moves the queue and this together."""
    queues = {q["queue"]: q for q in _body(client)["judgment"]["queues"]}
    for persona in personas.ALL_PERSONAS:
        for queue in persona.queues:
            assert persona.label in queues[queue]["bound"], (
                persona.id, queue
            )
    assert "Raymond Fitzgerald" in queues["data-stewardship"]["bound"]
    assert "Diane Vogel" in queues["expense-review"]["bound"]


def test_the_oldest_open_task_on_a_desk_is_aged(client, populated):
    """Depth alone cannot tell a busy desk from an abandoned one."""
    body = _body(client)
    for queue in body["judgment"]["queues"]:
        if queue["open"] == 0:
            continue
        oldest = min(
            t.created_at_iso
            for t in populated.tasks.list(status="open", queue=queue["queue"])
            if t.created_at_iso
        )
        assert queue["oldest_open_at_iso"] == oldest, queue["queue"]
        assert queue["oldest_open_age_seconds"] >= 0


def test_a_queue_holding_work_that_nobody_is_bound_to_is_a_finding(client):
    """Tasks route to a queue and never to a person so work survives absence —
    which also means an unbound desk accumulates where no personal inbox would
    ever surface it."""
    from app.tasks import Task

    deps.get_task_service().create(Task(
        id="task_orphan_desk", kind="curation", title="Nobody holds this",
        queue="nobody-is-bound-here", created_at_iso="2026-08-01T00:00:00+00:00",
    ))
    try:
        body = _body(client)
        assert body["judgment"]["unowned_with_work"] == ["nobody-is-bound-here"]
        row = next(
            q for q in body["judgment"]["queues"]
            if q["queue"] == "nobody-is-bound-here"
        )
        assert row["open"] == 1 and row["bound"] == []
    finally:
        deps.get_task_service().directory._items.pop("task_orphan_desk", None)


def test_the_identity_panel_is_the_workbenchs_own_composition(client):
    """Not a second walk of the links. The workbench is the list these numbers
    are answerable by, and re-deriving them would be a second way to be
    wrong."""
    body = _body(client)
    screen = client.get(
        IDENTITY_ROUTE, headers=_as("naviant-engineer")
    ).json()
    panel = body["judgment"]["identity"]

    assert panel["standing"] == len(screen["standing"])
    assert panel["awaiting_decision"] == sum(
        1 for p in screen["standing"] if p["gate"]["task_status"] == "open"
    )
    assert panel["without_task"] == sum(
        1 for p in screen["standing"] if not p["gate"]["task_id"]
    )
    assert panel["books"] == screen["books"]
    assert panel["last_sweep"] == screen["last_sweep"]
    # The sweep ran in this world, so there is something to be waiting on.
    assert panel["standing"] > 0
    assert panel["awaiting_decision"] == panel["standing"]
    assert panel["without_task"] == 0
    assert sum(b["standing"] for b in panel["books"]) == panel["standing"]


# ---- the ledger ------------------------------------------------------------


@pytest.fixture()
def ledgered(_client):
    """A world whose ledger holds exactly the entries this test wrote.

    Written through the Gateway rather than appended directly, because the
    Gateway is the only thing that may append and a test that bypassed it would
    be asserting against a shape nothing produces.
    """
    world = test_world.fresh()
    operator = principal_for(personas.get("naviant-engineer"))
    unbound = principal_for(personas.get("customer-user:ae"))

    def commit(action_id: str, principal: Principal = operator) -> None:
        world.gateway.action(action_id, principal).commit(lambda _writer: None)

    commit("land-source-records")
    commit("land-source-records")
    commit("apply-source-records")
    commit("post-invoice")
    commit("record-expense")
    commit("resolve-identities")
    # A refused write is the spine working, and it lands in the ledger too:
    # counting only commits would make a refusing deployment look idle.
    with pytest.raises(Refused):
        commit("post-invoice", unbound)

    _client.world = world  # type: ignore[attr-defined] — test handle
    return _client


def test_the_window_counts_governed_writes_by_the_regime_they_declared(
    ledgered,
):
    """Declared, never inferred. An observation, a decision and a resolution
    all end in an upsert and are indistinguishable by their effect, so the
    Action carries the regime and this counts what the Actions said."""
    ledger = _body(ledgered)["judgment"]["ledger"]
    by_regime = {r["regime"]: r for r in ledger["by_regime"]}

    assert ledger["in_window"] == 7
    assert ledger["scanned"] == 7
    assert ledger["truncated"] is False
    assert by_regime["observation"] == {
        "regime": "observation", "committed": 3, "refused": 0, "failed": 0,
    }
    assert by_regime["decision"] == {
        "regime": "decision", "committed": 2, "refused": 1, "failed": 0,
    }
    assert by_regime["resolution"] == {
        "regime": "resolution", "committed": 1, "refused": 0, "failed": 0,
    }
    assert ledger["refused"] == 1
    assert ledger["failed"] == 0
    assert ledger["newest_at_iso"]
    assert ledger["days"] == ui_api.DASHBOARD_WINDOW_DAYS


def test_a_refused_write_and_a_broken_one_are_counted_apart(ledgered):
    """Two endings, two buckets, and the reason they must not be one.

    A refusal is the spine working — a principal without the desk, a write the
    ontology rejects — and nothing happened. A failure is a write the Gateway
    allowed that then broke, which is the only ending where nobody can say
    what landed. Summing them would file an outage as governance.
    """
    gateway = deps.get_action_gateway()
    with pytest.raises(RuntimeError):
        gateway.action(
            "post-invoice", principal_for(personas.get("naviant-engineer")),
        ).commit(lambda _w: (_ for _ in ()).throw(RuntimeError("the ERP hung up")))

    ledger = _body(ledgered)["judgment"]["ledger"]
    by_regime = {r["regime"]: r for r in ledger["by_regime"]}
    assert by_regime["decision"] == {
        "regime": "decision", "committed": 2, "refused": 1, "failed": 1,
    }
    assert ledger["refused"] == 1 and ledger["failed"] == 1
    # And the row still adds up to the entries behind it, which is what the
    # console's bar draws.
    assert sum(
        r["committed"] + r["refused"] + r["failed"] for r in ledger["by_regime"]
    ) == ledger["in_window"] == 8


def test_a_regime_nothing_used_is_reported_as_zero_rather_than_omitted(
    empty_client,
):
    """The three regimes are a closed set. A missing row would read as a
    regime that does not exist rather than one nothing exercised."""
    ledger = _body(empty_client)["judgment"]["ledger"]
    assert [r["regime"] for r in ledger["by_regime"]] == [
        "observation", "decision", "resolution",
    ]
    assert all(
        r["committed"] == r["refused"] == r["failed"] == 0
        for r in ledger["by_regime"]
    )
    assert ledger["in_window"] == 0 and ledger["newest_at_iso"] == ""


def test_an_entry_older_than_the_window_is_outside_it(ledgered):
    """The window is a window. A count over 30 days that quietly included
    everything would be a different number wearing the same label."""
    entries = deps.get_action_gateway().ledger.list()
    stale = entries[-1].model_copy(update={
        "id": "led_stale", "at_iso": "2020-01-01T00:00:00+00:00",
    })
    deps.get_action_gateway().ledger.append(stale)

    ledger = _body(ledgered)["judgment"]["ledger"]
    assert ledger["scanned"] == 8
    assert ledger["in_window"] == 7


# ---------------------------------------------------------------------------
# 4 · Is governance holding
# ---------------------------------------------------------------------------


def test_the_gaps_are_the_flow_projections_own_diff(client, populated):
    """Both directions, unsubtracted anywhere else. `undeclared_*` is a flow
    touching what no Collection says it may; `unobserved_*` is a declaration no
    run has exercised. They are opposite problems."""
    from app.flow_projection import project_flows

    body = _body(client)
    projections = {p.flow_id: p for p in project_flows(populated.engine)}
    assert body["governance"]["flows_total"] == len(projections)

    gaps = {g["flow_id"]: g for g in body["governance"]["gaps"]}
    for flow_id, projection in projections.items():
        has_gap = bool(
            projection.undeclared_writes or projection.undeclared_reads
            or projection.unobserved_writes or projection.unobserved_reads
        )
        assert (flow_id in gaps) == has_gap, flow_id
        if not has_gap:
            continue
        assert gaps[flow_id]["undeclared_writes"] == projection.undeclared_writes
        assert gaps[flow_id]["unobserved_writes"] == projection.unobserved_writes
        assert gaps[flow_id]["state"] == projection.state
        assert gaps[flow_id]["total_runs"] == projection.total_runs

    # The AP flow's two live findings, kept as the worked example: it reads a
    # purchase order nothing declared, and declares a receiving report no run
    # of it has ever written.
    ap = gaps["accounts-payable"]
    assert "PurchaseOrder" in ap["undeclared_reads"]
    assert "ReceivingReport" in ap["unobserved_writes"]
    assert ap["label"] == "Accounts payable"


def test_a_flow_that_never_ran_carries_its_state_beside_its_gaps(client):
    """A flow with no history produces `unobserved_*` on every declaration it
    has, which is not the same finding as a flow that ran and skipped one."""
    for gap in _body(client)["governance"]["gaps"]:
        if gap["state"] == "never_ran":
            assert gap["total_runs"] == 0
            assert gap["undeclared_writes"] == []
            assert gap["undeclared_reads"] == []


def test_the_spend_tile_says_whether_zero_is_a_measurement(client):
    """A $0.00 has two readings pointing opposite ways — nothing called a
    model, or models were called and nobody priced them — and only the first is
    a fact about spending."""
    from app import flows

    body = _body(client)
    spend = body["governance"]["spend"]
    series = flows.usage_series(
        deps.get_run_engine().store.list(), ui_api.DASHBOARD_WINDOW_DAYS
    )
    assert spend["runs"] == sum(d.runs for d in series)
    assert spend["model_calls"] == sum(d.model_calls for d in series)
    assert spend["priced_model_calls"] == sum(d.priced_model_calls for d in series)
    assert spend["basis"] == flows.cost_basis(
        spend["model_calls"], spend["priced_model_calls"]
    )
    # The deployment's honest state today: the two real call sites journal the
    # call and not its price, so the figure is a hole that states itself.
    assert spend["model_calls"] > 0
    assert spend["priced_model_calls"] == 0
    assert spend["basis"] == "unrecorded"
    assert spend["cost_usd"] == 0.0


def test_the_registry_problems_are_a_platform_reading(client):
    """A Mapping naming a type that does not exist is a declaration bug on this
    side of the fence. Same withholding as the invariant report."""
    assert _body(client, "naviant-support")["governance"]["registry_problems"] == []
    assert _body(client, "customer-admin")["governance"]["registry_problems"] is None


# ---------------------------------------------------------------------------
# The empty world
# ---------------------------------------------------------------------------


def test_an_empty_world_states_itself_rather_than_looking_broken(empty_client):
    """Every zero here has to be legible as "nothing has happened yet". The two
    fields that would otherwise let a confident zero pass as good news — the
    spend basis and the last sweep — say which it is."""
    body = _body(empty_client)

    assert body["map"]["objects"] == 0
    assert body["map"]["links"] == 0
    assert body["map"]["conflicted"] == 0
    # The Collections are still listed: the shape of the book is declared, and
    # an empty map is a book with nothing in it rather than no book.
    assert body["map"]["collections"]
    assert all(c["objects"] == 0 for c in body["map"]["collections"])
    assert body["map"]["invariants"] == 0

    assert body["arrivals"]["feeds"] == []
    assert body["arrivals"]["without_contract"] == []
    assert body["arrivals"]["failing"] == []
    assert body["arrivals"]["estate"]["registered"] is False

    assert body["judgment"]["open_tasks"] == 0
    assert body["judgment"]["unowned_with_work"] == []
    # The desks exist even with nothing on them, and each says who holds it.
    assert {q["queue"] for q in body["judgment"]["queues"]}
    assert all(q["open"] == 0 for q in body["judgment"]["queues"])

    identity = body["judgment"]["identity"]
    assert identity["standing"] == 0 and identity["confirmed"] == 0
    # Never a zeroed row standing in for a sweep that never happened.
    assert identity["last_sweep"] is None
    assert identity["books"] and all(b["objects"] == 0 for b in identity["books"])

    # No run, so no model call — genuinely zero rather than unpriced.
    assert body["governance"]["spend"]["basis"] == "no-model-calls"
    assert body["governance"]["spend"]["cost_usd"] == 0.0
    assert body["governance"]["feeds_without_contract"] == []


def test_an_empty_world_still_reports_the_declared_gaps(empty_client):
    """Every flow projects as `never_ran` against a non-empty declared half,
    which is the state the screen must be able to draw: a governance promise
    nothing has yet kept."""
    gaps = _body(empty_client)["governance"]["gaps"]
    assert gaps
    assert all(g["state"] == "never_ran" for g in gaps)
    assert all(g["unobserved_writes"] or g["unobserved_reads"] for g in gaps)
    assert all(not g["undeclared_writes"] and not g["undeclared_reads"] for g in gaps)


# ---------------------------------------------------------------------------
# The door
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "persona", personas.ALL_PERSONAS, ids=[p.id for p in personas.ALL_PERSONAS]
)
def test_read_reach_decides_who_may_open_the_screen(client, persona):
    """The Knowledge layer's rule, because it is the same argument: none of
    this is scoped to one person's work. An own-scoped persona refused outright
    reads "this is not mine"; the same persona shown a screen of zeros would
    read "everything is fine"."""
    resp = client.get(ROUTE, headers=_as(persona.id))
    if persona.scope == "own":
        assert resp.status_code == 403, persona.id
        # This screen's own noun, not a shared generality — and addressed to
        # the reader rather than to the persona they are wearing.
        assert resp.json()["detail"] == (
            "System state reads every collection, feed, desk and ledger entry "
            "at once. Your access is to your own work."
        )
    else:
        assert resp.status_code == 200, persona.id


def test_the_screen_inherits_the_console_door():
    """One spelling of the no-keys rule and one of the read-reach rule. A
    machine has no screens, and a second dependency here would be a second,
    quieter copy of both."""
    depends = inspect.signature(ui_api.dashboard).parameters["principal"].default
    assert depends.dependency is ui_api.get_dashboard_principal
    door = inspect.signature(ui_api.get_dashboard_principal)
    assert door.parameters["principal"].default.dependency is get_ui_principal

    machine = Principal(
        uid="svc-1", email="svc@machine", role="owner", key_id="k_1",
        scope="platform",
    )
    with pytest.raises(HTTPException) as refused:
        get_ui_principal(machine)
    assert refused.value.status_code == 403


def test_one_visit_is_one_call(client):
    """The four questions are one question. Fetching them separately would put
    the join in the client, where each panel could show a different instant and
    disagree about a number they were both reading off the same store."""
    body = _body(client)
    assert set(body) == {
        "at_iso", "window_days", "map", "arrivals", "judgment", "governance",
    }
    assert body["at_iso"]
    assert body["window_days"] == ui_api.DASHBOARD_WINDOW_DAYS
