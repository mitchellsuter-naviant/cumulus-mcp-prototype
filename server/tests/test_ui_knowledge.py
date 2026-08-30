"""The console's Knowledge layer: Ontology, Library, Sources.

Three screens, three routes, and three properties worth defending — each one
the kind that fails plausibly rather than loudly.

- **The Collection partition.** The diagram clusters by Collection, tints by
  Collection, and renders the declared half of the overlay from Collection
  membership. All three assume every object type is claimed by exactly one.
  A type claimed twice is drawn twice; a type claimed by none disappears from
  a diagram that says it is the whole model. Neither raises anything at run
  time, so it is asserted here instead.
- **The overlay's three states.** `never_ran` and `ran_but_wrote_nothing` both
  produce an empty `writes` and are opposite facts. A payload that let the
  client collapse them would look correct on the flows that have run.
- **The persona gate.** The invariant report is a Naviant bug report, and
  routing it to a customer is an alarm with no action attached — so the tenant
  gets the model and the overlay and a `null` where the conformance count
  would be, which is deliberately not a reassuring zero.
"""

from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

import app.deps as deps
from app import data_collections, personas, ui_api
from app.auth import Principal
from app.calendars import seed_calendars_if_empty
from app.estate import (
    HRIS_WORKFORCE,
    EstateConnector,
    EstateError,
    InMemoryConnectorStateStore,
)
from app.ontology import ONTOLOGY
from app.payables import seed_payables_if_empty
from app.reimbursements import seed_reimbursements_if_empty
from app.runs import RunRecord, StepRecord
from app.ui_api import UNGOVERNED, get_ui_principal
from app.usage import READ, InMemoryAssetUsageStore
from app.workforce import seed_workforce_if_empty
from tests import estate_fixtures as fx
from tests import world as test_world

ONTOLOGY_ROUTE = "/api/ui/v1/ontology"
SOURCES_ROUTE = "/api/ui/v1/sources"
LIBRARY_ROUTE = "/api/ui/v1/library"
ROUTES = (ONTOLOGY_ROUTE, SOURCES_ROUTE, LIBRARY_ROUTE)

#: The flows that reach the graph. Written out rather than derived from the
#: engine, so the test states the expected answer: seven of them are inbound
#: syncs, three of those still have no FlowDefinition, and a selector built
#: from the flow registry would offer six and lose the rest.
GRAPH_FLOWS = {
    "accounts-payable",
    "calendar-sync",
    "crm-sync",
    "expense-reimbursement",
    "purchase-order-sync",
    "receiving-sync",
    "sales-sync",
    "vendor-sync",
    "workforce-sync",
}

_AT = "2026-08-01T00:00:00+00:00"


def _as(persona_id: str) -> dict[str, str]:
    return {"X-Cumulus-Persona": persona_id}


@pytest.fixture(scope="module")
def seeded():
    """A world where every flow that reaches the graph has actually run."""
    world = test_world.build()
    seed_payables_if_empty(world.payables, sync=world.sync)
    seed_workforce_if_empty(world.sync, world.graph)
    seed_calendars_if_empty(world.sync, world.graph)
    seed_reimbursements_if_empty(world.expenses)
    return world


@pytest.fixture()
def client(_client, seeded):
    """Re-asserted per test rather than installed once.

    `install` is a handful of assignments, and re-running it means a test that
    swaps in a world of its own cannot leave the rest of the module reading
    it. Usage is swapped too: the store is a process global, and the library
    join is only worth asserting against numbers this module put there.
    """
    test_world.install(seeded)
    deps._asset_usage = InMemoryAssetUsageStore()
    return _client


@pytest.fixture()
def probe_client(_client):
    """A world with no history, and one flow staged into each other state.

    Built rather than staged into `seeded`: adding probe runs to the shared
    world would make every other test in this module depend on the order it
    ran in.
    """
    world = test_world.fresh()
    world.engine.store.put(RunRecord(
        id="run_probe_read", flow_id="read-only-probe",
        started_at_iso=_AT, updated_at_iso=_AT,
        steps=[StepRecord(
            step_id="z1", name="read-policy", kind="resolve",
            refs=["knowledge:invoice-posting-policy@rev1"],
        )],
    ))
    world.engine.store.put(RunRecord(
        id="run_probe_write", flow_id="wrote-probe",
        started_at_iso=_AT, updated_at_iso=_AT,
        steps=[StepRecord(
            step_id="z1", name="post", kind="write",
            writes=["graph:Invoice/invoice_1@rev2"],
        )],
    ))
    return _client


# ---------------------------------------------------------------------------
# The invariant the diagram is built on
# ---------------------------------------------------------------------------


def test_every_object_type_is_governed_by_exactly_one_collection():
    """The partition, asserted at build time because nothing asserts it later.

    Governance hangs off the Collection — retention, custody, who may read it
    as canonical — so a type belonging to none of them is a type nothing
    governs, and a type belonging to two is governed by whichever declaration
    happened to be listed first.
    """
    claims: dict[str, list[str]] = {}
    for collection in data_collections.COLLECTIONS:
        for type_name in collection.object_types:
            claims.setdefault(type_name, []).append(collection.id)

    declared = {o.name for o in ONTOLOGY.objects}
    assert not declared - set(claims), "object types no Collection claims"
    assert not set(claims) - declared, "Collections claiming a type that is not declared"
    assert {t: ids for t, ids in claims.items() if len(ids) > 1} == {}


def test_the_collections_added_for_the_estate_claim_what_they_say_they_do():
    """The partition above is derived, so it stays true while saying nothing
    about *which* Collection took a type. These four were added together and
    the split between them is the argument: contacts sit with the companies
    they work at rather than in a book of their own, the whole sell-side
    lifecycle is one set, correspondence is capped at a mirror, and the
    document library governs the filing tree only — `Document` has exactly one
    home by type, and a DMS row is placed here per instance by `collection_id`.
    A future edit that quietly moves one of these would still pass the derived
    sweep.
    """
    claimed = {
        c.id: set(c.object_types)
        for c in data_collections.COLLECTIONS
        if c.id in {
            "company-master", "sales-records", "correspondence",
            "document-library",
        }
    }
    assert claimed == {
        "company-master": {"Company", "Person"},
        "sales-records": {"Opportunity", "SalesOrder", "SalesOrderLine", "Item"},
        "correspondence": {"Mailbox", "MailMessage"},
        "document-library": {"Folder"},
    }
    assert data_collections.for_object_type("Document").id == "audit-documents"

    # Custody is the governed property that separates them, and the DMS half
    # is the one that must never quietly become a copy.
    by_id = {c.id: c for c in data_collections.COLLECTIONS}
    assert by_id["document-library"].custody == "reference"
    assert by_id["correspondence"].custody == "copy"
    # Mirrors are not authoritative: a correction belongs in the source.
    assert by_id["company-master"].authoritative is True
    for mirrored in ("sales-records", "correspondence", "document-library"):
        assert by_id[mirrored].authoritative is False, mirrored


def test_the_estate_flows_are_declared_before_they_are_written():
    """Declared ahead of their writers, the way the org-chart links were.

    None of these flows is registered yet, so every one of them projects as
    `never_ran` with a non-empty declared half — which is exactly the
    governance gap the ontology overlay draws as an outline. A declaration
    that only appeared once its writer did could never show that state, and
    the screen's whole point is the difference between declared and observed.
    """
    for flow_id, expected in (
        ("crm-sync", {"company-master", "sales-records", "source-landing"}),
        ("sales-sync", {"company-master", "sales-records", "source-landing"}),
        ("mail-sync", {"correspondence"}),
        ("dms-sync", {"document-library"}),
        ("employment-event-sync", {"source-landing"}),
    ):
        writes = {c.id for c in data_collections.written_by(flow_id)}
        assert writes == expected, flow_id

    # mail-sync reads the counterparty book instead of writing it: a message
    # resolves its correspondents against the book and counts the ones it
    # cannot place, rather than minting a Person per sending domain.
    assert {c.id for c in data_collections.read_by("mail-sync")} == {
        "company-master"
    }
    assert "company-master" not in {
        c.id for c in data_collections.written_by("mail-sync")
    }


def test_the_tbox_and_the_registry_agree_on_who_governs_what():
    """The same fact is stated from both ends, and the route reads one of them.

    `ObjectType.collection` names the governing Collection; `object_types`
    names the governed types. Only the second decides the diagram's clusters,
    so a disagreement would show as a node tinted by one Collection and
    grouped under another.
    """
    for declared in ONTOLOGY.objects:
        governing = data_collections.for_object_type(declared.name)
        assert governing is not None, declared.name
        assert declared.collection == governing.id, declared.name


def test_an_unclaimed_type_degrades_into_a_named_group(client, monkeypatch):
    """A broken partition must not take the screen down with it.

    The test above fails the build when this happens; the route's job is to
    keep drawing, and to say out loud which types it could not place rather
    than dropping them from a diagram that claims to be the whole model.
    """
    monkeypatch.setattr(
        data_collections, "COLLECTIONS",
        tuple(c for c in data_collections.COLLECTIONS if c.id != "calendars"),
    )
    body = client.get(ONTOLOGY_ROUTE).json()

    assert len(body["types"]) == len(ONTOLOGY.objects)
    orphans = next(c for c in body["collections"] if c["id"] == UNGOVERNED)
    assert orphans["type_names"] == ["Calendar", "CalendarEvent"]
    assert "Ungoverned" in orphans["label"]
    calendar = next(t for t in body["types"] if t["name"] == "Calendar")
    assert calendar["collection"] == UNGOVERNED


# ---------------------------------------------------------------------------
# Ontology
# ---------------------------------------------------------------------------


def test_the_ontology_route_draws_the_whole_model_in_one_call(client):
    body = client.get(ONTOLOGY_ROUTE).json()

    assert len(body["types"]) == len(ONTOLOGY.objects)
    assert len(body["links"]) == len(ONTOLOGY.links)

    # The clusters partition the nodes: each type in exactly one, none empty,
    # and no orphan group in a healthy registry.
    clustered = [n for c in body["collections"] for n in c["type_names"]]
    assert sorted(clustered) == sorted(t["name"] for t in body["types"])
    assert all(c["type_names"] for c in body["collections"])
    assert UNGOVERNED not in {c["id"] for c in body["collections"]}

    # Declaration and reality in one payload, per node.
    employee = next(t for t in body["types"] if t["name"] == "Employee")
    assert employee["collection"] == "workforce"
    assert employee["instances"] > 0
    assert employee["competency_questions"]
    line = next(t for t in body["types"] if t["name"] == "InvoiceLine")
    assert line["owner_type"] == "Invoice"
    assert "Invoice" in [t["name"] for t in body["types"]]

    assert body["overlay"] is None


def test_the_selector_offers_every_flow_that_writes_the_graph(client):
    """Not the definitions in the flow registry. Three of the nine are inbound
    syncs with no FlowDefinition at all, and a picker built from the registry
    would leave part of the graph unattributable."""
    body = client.get(ONTOLOGY_ROUTE).json()
    assert {f["id"] for f in body["flows"]} == GRAPH_FLOWS
    # Named, including the three the registry cannot name.
    assert all(f["label"] and f["label"] != f["id"] for f in body["flows"])


def test_the_overlay_ships_both_halves_of_the_diff(client):
    """Observed and declared, unsubtracted. The client tints the diagram from
    the difference; shipping a third pre-diffed pair would be a second answer
    to the same question with its own way of being wrong."""
    overlay = client.get(f"{ONTOLOGY_ROUTE}?flow=accounts-payable").json()["overlay"]

    assert overlay["flow_id"] == "accounts-payable"
    assert overlay["state"] == "observed"
    assert overlay["writes"] == [
        "Document", "Invoice", "InvoiceLine", "MatchSet", "Vendor",
    ]
    assert overlay["knowledge"] == [
        "invoice-extraction-prompt", "invoice-posting-policy",
    ]
    assert overlay["scanned_runs"] == overlay["total_runs"] > 0

    # Observed with nothing declaring it — a governance gap the client can see
    # only because both sets are present.
    assert "PurchaseOrder" in overlay["reads"]
    assert "PurchaseOrder" not in overlay["declared_reads"]
    # And declared with nothing observing it — a stale declaration.
    assert "ReceivingReport" in overlay["declared_writes"]
    assert "ReceivingReport" not in overlay["writes"]


def test_the_overlay_tells_never_ran_from_wrote_nothing(probe_client):
    """The distinction the payload exists to preserve.

    Both produce an empty `writes` and they are opposite facts: the first is
    unknown, the second is a read-only flow doing exactly what it should.
    """
    def overlay(flow_id: str) -> dict:
        return probe_client.get(
            f"{ONTOLOGY_ROUTE}?flow={flow_id}"
        ).json()["overlay"]

    never = overlay("accounts-payable")
    quiet = overlay("read-only-probe")
    wrote = overlay("wrote-probe")

    assert never["writes"] == quiet["writes"] == []
    assert never["state"] == "never_ran"
    assert never["total_runs"] == 0
    assert quiet["state"] == "ran_but_wrote_nothing"
    assert quiet["total_runs"] == 1
    assert quiet["knowledge"] == ["invoice-posting-policy"]
    assert wrote["state"] == "observed"
    assert wrote["writes"] == ["Invoice"]

    # A flow with no history is still offered: the picker is the list of what
    # may be overlaid, not the list of what happens to have run.
    offered = {f["id"] for f in probe_client.get(ONTOLOGY_ROUTE).json()["flows"]}
    assert GRAPH_FLOWS <= offered


def test_a_flow_the_selector_never_offered_is_refused(client):
    resp = client.get(f"{ONTOLOGY_ROUTE}?flow=no-such-flow")
    assert resp.status_code == 404
    assert "no-such-flow" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def test_the_sources_route_puts_health_and_contract_on_one_row(client):
    body = client.get(SOURCES_ROUTE).json()
    rows = {r["source"]: r for r in body["sources"]}
    assert len(rows) == 7

    erp = rows["erp-vendors"]
    assert erp["flow_id"] == "vendor-sync"
    assert erp["last_status"] == "succeeded"
    assert erp["records_landed"] > 0
    assert erp["contract"]["lands_as"] == "SourceRecord"
    assert erp["contract"]["resolves_to"] == ["Vendor"]
    assert erp["contract"]["collection"] == "source-landing"

    # Three feeds ride one flow, which is why the artifact join goes through
    # the run and not the flow.
    assert sum(1 for r in rows.values() if r["flow_id"] == "vendor-sync") == 3
    assert all(r["contract"] is not None for r in rows.values())
    assert body["problems"] == []


def test_a_feed_with_no_mapping_is_reported_rather_than_dropped(client, monkeypatch):
    """Rows arriving under no declared contract is the finding this screen is
    for, so the row stays and says so."""
    monkeypatch.setattr(
        ui_api, "MAPPINGS",
        tuple(m for m in ui_api.MAPPINGS if m.source != "erp-vendors"),
    )
    rows = {r["source"]: r for r in client.get(SOURCES_ROUTE).json()["sources"]}
    assert rows["erp-vendors"]["contract"] is None
    assert rows["erp-vendors"]["records_landed"] > 0
    assert rows["procurement-coupa"]["contract"] is not None


def test_a_pushed_feed_carries_no_pull_state(client):
    """`pull is None` is a fact about the feed, not a gap in the payload.

    Every seeded feed pushes through the door on its own cadence. None of them
    has a cursor here, and a screen that drew a Sync now for them would be
    offering to pull a system nothing knows how to reach.
    """
    body = client.get(SOURCES_ROUTE).json()
    assert all(r["pull"] is None for r in body["sources"])
    assert body["estate"]["registered"] is False
    assert body["estate"]["feeds_registered"] == 0
    assert body["estate"]["apps"] == []


def test_artifacts_are_attributed_to_the_feed_whose_run_left_them(client):
    """Through the run, never the flow.

    Nothing a sync leaves today is a document — feeds land rows, and the paper
    belongs to the AP and expense flows — so every count here is honestly
    zero. The join is by run id because three feeds share `vendor-sync`, and
    the moment a feed does drop an artifact, a flow-keyed join would credit it
    to all three.
    """
    rows = client.get(SOURCES_ROUTE).json()["sources"]
    assert all(r["artifacts_total"] == len(r["artifacts"]) for r in rows)
    assert all(r["artifacts"] == [] for r in rows)
    # And the documents exist — the emptiness is attribution, not an empty
    # document store.
    assert deps.get_document_service().list()


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------


def test_the_library_route_joins_the_corpus_to_its_usage(client):
    deps.get_asset_usage().record("expense-approval-policy", READ, "console", "tester")

    body = client.get(LIBRARY_ROUTE).json()
    assert body["days"] == 30
    rows = {a["id"] for a in body["assets"]}
    assert rows == {a.id for a in deps.get_service().list_assets()}

    read = next(a for a in body["assets"] if a["id"] == "expense-approval-policy")
    assert read["reads"] == 1
    assert read["last_used_at_iso"]
    assert read["title"] and read["path"] and read["kind"]


def test_untouched_assets_are_zero_filled_rather_than_omitted(client):
    """The absence is the finding. An asset with no traffic has no rollup, so
    returning only what the usage store holds would drop exactly the documents
    a dead-weight report is looking for."""
    body = client.get(LIBRARY_ROUTE).json()
    unused = [a for a in body["assets"] if a["reads"] == a["citations"] == 0]
    assert unused
    assert all(a["last_used_at_iso"] == "" for a in unused)


# ---------------------------------------------------------------------------
# The doors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ROUTES, ids=lambda p: p.rsplit("/", 1)[-1])
@pytest.mark.parametrize(
    "persona", personas.ALL_PERSONAS, ids=[p.id for p in personas.ALL_PERSONAS]
)
def test_read_reach_decides_who_may_open_the_knowledge_layer(client, persona, path):
    """`own` is refused outright rather than served an empty screen, which it
    would read as "there is nothing here" instead of "this is not yours"."""
    resp = client.get(path, headers=_as(persona.id))
    if persona.scope == "own":
        assert resp.status_code == 403, persona.id
        assert "own" in resp.json()["detail"]
    else:
        assert resp.status_code == 200, persona.id


def test_only_platform_scope_sees_the_conformance_report(client):
    """A TBox violation is a Naviant bug. The tenant gets the whole model and
    the whole overlay, and `null` where the count would be — deliberately not
    a reassuring zero, which would be a claim rather than a withholding."""
    platform = client.get(ONTOLOGY_ROUTE, headers=_as("naviant-support")).json()
    tenant = client.get(ONTOLOGY_ROUTE, headers=_as("customer-admin")).json()

    assert platform["conformance"] == 0
    assert tenant["conformance"] is None

    assert len(tenant["types"]) == len(platform["types"])
    assert len(tenant["collections"]) == len(platform["collections"])
    overlaid = client.get(
        f"{ONTOLOGY_ROUTE}?flow=accounts-payable", headers=_as("customer-admin")
    ).json()["overlay"]
    assert overlaid["state"] == "observed"


# ---------------------------------------------------------------------------
# Sources — the estate connector's half
# ---------------------------------------------------------------------------


def _connector(estate: fx.FakeEstate | None = None, **over):
    """A connector over the recorded estate, delivering into a list.

    Built here rather than imported from `test_estate.py` so this module's
    fixtures do not depend on another test module's helpers; the fake is the
    shared thing, and it is a fixture file for exactly that reason.
    """
    estate = estate or fx.FakeEstate()
    return EstateConnector(
        InMemoryConnectorStateStore(),
        project="cumulus-demo-mitchell-suter",
        namespace=over.pop("namespace", "naviant"),
        fetch=estate.fetch,
        deliver=over.pop("deliver", None) or (lambda payload: {"runs": []}),
        **over,
    )


@pytest.fixture()
def estate_client(_client, monkeypatch):
    """A world with no history and a registered estate.

    Fresh rather than `seeded`, because a pull delivers through the real door:
    landing eight employees into the module's shared world would make every
    other test in it depend on the order this one ran in.
    """
    world = test_world.fresh()

    def door(payload: dict) -> dict:
        response = _client.post("/api/v1/sources/deliver", json=payload)
        assert response.status_code == 202, response.text
        return response.json()

    connector = _connector(deliver=door)
    monkeypatch.setattr(deps, "_estate_connector", connector)
    _client.connector = connector  # type: ignore[attr-defined]  — test handle
    _client.world = world  # type: ignore[attr-defined]
    return _client


def test_the_estate_card_says_so_when_no_estate_is_registered(client):
    """The default everywhere but a dev box, and it reads as "nothing to do".

    Registration is one fact for the whole connector, so it is answered once
    at screen level. The feed registry still holds its specs, but none of them
    names anything reachable — reporting them as rows would offer a Sync now
    with nowhere to pull from and make the card's counts describe a list of
    possibilities rather than of feeds.
    """
    estate = client.get(SOURCES_ROUTE).json()["estate"]
    assert estate == {
        "registered": False, "project": "", "namespace": "",
        "discovery_url": "", "authenticated": False,
        "pull_interval_seconds": 0, "discovery_error": "", "industry": "",
        "apps": [], "processes": [], "pulse_enabled": None,
        "pulse_intensity": "", "feeds_registered": 0, "feeds_active": 0,
    }


def test_the_estate_card_reports_discovery_and_per_feed_activation(
    estate_client,
):
    body = estate_client.get(SOURCES_ROUTE).json()
    estate = body["estate"]
    assert estate["registered"] is True
    assert estate["project"] == "cumulus-demo-mitchell-suter"
    assert estate["namespace"] == "naviant"
    assert estate["industry"] == "software-reseller"
    assert estate["apps"] == ["crm", "erp", "office", "hris", "dms"]
    assert estate["pulse_enabled"] is True
    assert estate["pulse_intensity"] == "low"
    assert estate["discovery_error"] == ""
    # Every count on the card is answered by rows in the list under it. The
    # expectation is read off the FEEDS registry rather than pinned, so a
    # sibling stage adding a feed moves both sides of this equation at once.
    from app.estate import FEEDS

    expected = {f.feed for f in FEEDS if f.app in estate["apps"]}
    assert estate["feeds_registered"] == len(FEEDS)
    assert estate["feeds_active"] == len(expected)
    pulled = [r for r in body["sources"] if r["pull"] is not None]
    assert {r["source"] for r in pulled} == {f.feed for f in FEEDS}
    assert {r["source"] for r in pulled if r["pull"]["active"]} == expected
    assert HRIS_WORKFORCE in expected


def test_a_registered_feed_that_has_never_pulled_still_gets_a_row(
    estate_client,
):
    """No run to be assembled from, and the operator still has to be able to
    start it. The row says nothing has happened rather than being absent."""
    rows = {r["source"]: r for r in estate_client.get(SOURCES_ROUTE).json()["sources"]}
    row = rows[HRIS_WORKFORCE]

    assert row["deliveries"] == 0 and row["records_landed"] == 0
    assert row["last_run_at_iso"] == "" and row["flow_id"] == ""
    assert row["contract"]["transport"] == "pull"
    assert row["pull"]["last_pull_at"] == ""
    assert row["pull"]["watermarks"] == {}
    assert row["pull"]["resources"] == ["departments", "jobs", "employees"]
    assert row["pull"]["sync_now_allowed"] is True
    assert row["pull"]["sync_now_refusal"] == ""


def test_the_cursor_is_joined_onto_the_row_the_pull_delivered(estate_client):
    """The half of a feed's state the runs cannot tell you.

    A watermark is not derivable from the runs the way the counts are, and the
    two facts belong on one row: `records_landed` says what arrived, the
    cursor says what the next pull will ask for.
    """
    estate_client.connector.pull(HRIS_WORKFORCE)

    rows = {r["source"]: r for r in estate_client.get(SOURCES_ROUTE).json()["sources"]}
    row = rows[HRIS_WORKFORCE]
    pull = row["pull"]

    assert pull["last_outcome"] == "delivered"
    assert pull["last_error"] == ""
    assert pull["last_pull_at"]
    # Per resource, because they move independently.
    assert set(pull["watermarks"]) == {"departments", "jobs", "employees"}
    assert pull["estate_app"] == "hris"
    # The run-derived half is there too, and counts what the door was handed:
    # 7 departments and 10 workers, the jobs having been read for titles only.
    assert row["flow_id"] == "workforce-sync"
    assert row["deliveries"] == 1
    assert row["records_landed"] == 17
    # Fewer Employees than worker rows, and that is the estate's real drift —
    # two of those emails are held by two worker records each, and the natural
    # key collapses them. The row counts deliveries, not entities.
    assert estate_client.world.graph.stats()["by_type"]["Employee"] == 8
    # The delivered watermark is the cursor the connector will ask with next.
    assert row["watermark"] == max(pull["watermarks"].values())


def test_a_failed_pull_is_on_the_row_even_though_no_run_exists(estate_client):
    """The state a run-derived list cannot hold. A pull that never reached the
    door left nothing behind but its own recorded failure, and a screen that
    read only the runs would show this feed as simply quiet."""
    estate_client.connector._fetch = fx.FakeEstate(
        fails={("hris", "employees")}
    ).fetch
    with pytest.raises(EstateError):
        estate_client.connector.pull(HRIS_WORKFORCE)

    rows = {r["source"]: r for r in estate_client.get(SOURCES_ROUTE).json()["sources"]}
    pull = rows[HRIS_WORKFORCE]["pull"]
    assert pull["last_outcome"] == "failed"
    assert "employees" in pull["last_error"]
    # No cursor advanced, so the next pull re-asks the same window.
    assert pull["watermarks"] == {}
    assert rows[HRIS_WORKFORCE]["deliveries"] == 0


def test_an_undeclared_pulled_feed_still_reports_both_halves(
    estate_client, monkeypatch
):
    """The screen's oldest finding survives the join: a feed can be pulling
    cleanly and landing under no declared Mapping at once, and the row has to
    say both."""
    monkeypatch.setattr(
        ui_api, "MAPPINGS",
        tuple(m for m in ui_api.MAPPINGS if m.source != HRIS_WORKFORCE),
    )
    estate_client.connector.pull(HRIS_WORKFORCE)

    rows = {r["source"]: r for r in estate_client.get(SOURCES_ROUTE).json()["sources"]}
    assert rows[HRIS_WORKFORCE]["contract"] is None
    assert rows[HRIS_WORKFORCE]["pull"]["last_outcome"] == "delivered"
    assert rows[HRIS_WORKFORCE]["records_landed"] > 0


def test_sync_now_pulls_the_feed_and_answers_with_its_row(estate_client):
    """One round trip: the outcome and the row it leaves behind.

    The row travels with the result so the screen redraws the feed from the
    same read that reports it — a count in a banner disagreeing with the count
    in the list under it is what this shape rules out.
    """
    body = estate_client.post(
        f"{SOURCES_ROUTE}/{HRIS_WORKFORCE}/sync-now"
    ).json()

    assert body["outcome"] == "delivered"
    assert body["error"] == ""
    assert body["pulled"] == {"departments": 7, "jobs": 5, "employees": 10}
    assert body["chunks"] == 1
    assert body["delivered"] == 22
    row = body["row"]
    assert row["source"] == HRIS_WORKFORCE
    assert row["records_landed"] > 0
    assert row["pull"]["last_outcome"] == "delivered"
    # And the row it answered with is the row the screen would read next.
    listed = {
        r["source"]: r
        for r in estate_client.get(SOURCES_ROUTE).json()["sources"]
    }
    assert listed[HRIS_WORKFORCE] == row


def test_a_second_sync_now_of_the_same_window_lands_nothing(estate_client):
    """The state the screen has to be able to say out loud: a pull that found
    nothing is not a pull that failed."""
    estate_client.post(f"{SOURCES_ROUTE}/{HRIS_WORKFORCE}/sync-now")
    again = estate_client.post(
        f"{SOURCES_ROUTE}/{HRIS_WORKFORCE}/sync-now"
    ).json()

    assert again["outcome"] == "unchanged"
    assert again["pulled"] == {"departments": 0, "jobs": 0, "employees": 0}
    assert again["chunks"] == 0 and again["delivered"] == 0
    assert again["row"]["pull"]["last_outcome"] == "unchanged"


def test_sync_now_reports_an_estate_outage_as_a_bad_gateway(estate_client):
    """The failure is upstream. Reporting it as our own 500 would send an
    operator to read the wrong logs."""
    estate_client.connector._fetch = fx.FakeEstate(
        fails={("hris", "employees")}
    ).fetch
    response = estate_client.post(f"{SOURCES_ROUTE}/{HRIS_WORKFORCE}/sync-now")

    assert response.status_code == 502
    assert "employees" in response.json()["detail"]


def test_sync_now_refuses_a_persona_with_no_write_authority(estate_client):
    """A pull writes through the door, so it gates exactly where the machine
    route gates — and the screen is told the answer in advance.

    Support reads every plane and decides nothing, which is the case a console
    proxy is most likely to get wrong: it can see the feed, so a button drawn
    from read reach alone would be there and would 403 when pressed.
    """
    refused = estate_client.post(
        f"{SOURCES_ROUTE}/{HRIS_WORKFORCE}/sync-now",
        headers=_as("naviant-support"),
    )
    assert refused.status_code == 403
    assert "admin" in refused.json()["detail"]

    pull = next(
        r["pull"]
        for r in estate_client.get(
            SOURCES_ROUTE, headers=_as("naviant-support")
        ).json()["sources"]
        if r["source"] == HRIS_WORKFORCE
    )
    assert pull["sync_now_allowed"] is False
    assert "admin authority" in pull["sync_now_refusal"]


def test_sync_now_refuses_a_persona_that_reads_only_its_own_work(estate_client):
    """A customer user is refused at the console door it shares with every
    other Knowledge route — one 403 with a sentence in it, not a stack trace
    from a route that assumed platform reach."""
    refused = estate_client.post(
        f"{SOURCES_ROUTE}/{HRIS_WORKFORCE}/sync-now",
        headers=_as("customer-user:kim"),
    )
    assert refused.status_code == 403
    assert "reads only its own" in refused.json()["detail"]


def test_sync_now_of_a_feed_the_registry_does_not_hold_is_a_404(estate_client):
    response = estate_client.post(f"{SOURCES_ROUTE}/no-such-feed/sync-now")
    assert response.status_code == 404
    assert "no-such-feed" in response.json()["detail"]


def test_sync_now_with_no_estate_registered_is_refused_before_the_network(
    client,
):
    response = client.post(f"{SOURCES_ROUTE}/{HRIS_WORKFORCE}/sync-now")
    assert response.status_code == 400
    assert "ESTATE_PROJECT" in response.json()["detail"]


def test_an_unreachable_estate_is_reported_as_itself(estate_client):
    """Discovery failing and discovery saying the app is absent are opposite
    findings, and only the second one is about the customer's estate."""
    estate_client.connector._discovery = None
    estate_client.connector._fetch = fx.FakeEstate(
        namespaces={"namespaces": [], "default": ""}
    ).fetch

    body = estate_client.get(SOURCES_ROUTE).json()
    assert body["estate"]["registered"] is True
    assert "naviant" in body["estate"]["discovery_error"]
    assert body["estate"]["apps"] == []
    assert body["estate"]["feeds_active"] == 0

    pull = next(
        r["pull"] for r in body["sources"] if r["source"] == HRIS_WORKFORCE
    )
    assert pull["sync_now_allowed"] is False
    assert pull["sync_now_refusal"].startswith("The estate could not be reached")


def test_the_console_sync_now_gates_where_the_machine_route_gates():
    """One spelling of the rule. A console door that let a weaker principal
    through would be a second, quieter authorization policy."""
    from app import estate_api

    console = inspect.signature(ui_api.sync_source_now)
    assert console.parameters["principal"].default.dependency is (
        ui_api.get_knowledge_principal
    )
    machine = inspect.getsource(estate_api.sync_now)
    assert 'require_role(principal, "admin")' in machine
    assert 'require_role(principal, "admin")' in inspect.getsource(
        ui_api.sync_source_now
    )


def test_only_platform_scope_sees_the_mapping_registry_problems(client):
    """Same reading as conformance: a mapping naming a type that does not
    exist is a declaration bug on this side of the fence."""
    platform = client.get(SOURCES_ROUTE, headers=_as("naviant-support")).json()
    tenant = client.get(SOURCES_ROUTE, headers=_as("customer-admin")).json()

    assert platform["problems"] == []
    assert tenant["problems"] is None
    assert len(tenant["sources"]) == len(platform["sources"])


def test_the_knowledge_routes_inherit_the_console_door():
    """The no-keys rule is inherited, never restated.

    `get_knowledge_principal` depends on `get_ui_principal` rather than
    re-deriving the check, so a machine credential is refused at the same door
    as every other console route and there is one place for that rule to live.
    """
    gate = inspect.signature(ui_api.get_knowledge_principal)
    assert gate.parameters["principal"].default.dependency is get_ui_principal

    for route in (ui_api.ontology, ui_api.sources, ui_api.library):
        depends = inspect.signature(route).parameters["principal"].default
        assert depends.dependency is ui_api.get_knowledge_principal, route.__name__

    machine = Principal(
        uid="svc-1", email="svc@machine", role="owner", key_id="k_1"
    )
    with pytest.raises(HTTPException) as refused:
        get_ui_principal(machine)
    assert refused.value.status_code == 403
