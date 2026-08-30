"""The commerce feeds: one counterparty book, spelled two ways.

These are the properties the CRM and ERP feeds exist to hold. The fixtures are
recorded from the live estate, which matters more here than anywhere else: the
whole point is that `Northwind Traders` and `Northwind Traders Inc.` are one
company, and a tidied fixture that spelled them alike would test a resolver
against a problem the estate does not have.
"""

import pytest

from app.graph import object_id
from app.sync import (
    CRM_FLOW_ID,
    DATA_STEWARD_QUEUE,
    SALES_FLOW_ID,
    SyncRequest,
)
from tests import estate_fixtures as fx
from tests import world as test_world


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


# ---------------------------------------------------------------------------
# The deliveries, built the way the connector builds them
# ---------------------------------------------------------------------------


def _crm_request(**overrides) -> SyncRequest:
    """The CRM book, through the connector's own build function.

    Not a hand-written SyncRequest: the point of going through `estate.py` is
    that the field renames the flow depends on (`companyName` → `name`,
    `itemId` → `sku`) are exercised rather than assumed.
    """
    from app.estate import _build_crm_book

    rows = {
        "accounts": fx.ACCOUNTS,
        "contacts": fx.CONTACTS,
        "opportunities": fx.OPPORTUNITIES,
    }
    rows.update(overrides)
    return _build_crm_book(rows, lambda resource, record_id: {})


def _sales_request(**overrides) -> SyncRequest:
    from app.estate import _build_erp_sales

    rows = {
        "customers": fx.CUSTOMERS,
        "items": fx.ITEMS,
        "salesOrders": fx.SALES_ORDERS,
    }
    rows.update(overrides)
    return _build_erp_sales(rows, lambda resource, record_id: {})


def _deliver(world, request: SyncRequest, key: str = "") -> dict:
    outcomes = world.sync.deliver(
        request.model_copy(update={"idempotency_key": key}),
        actor="system:test",
    )
    assert len(outcomes) == 1
    return outcomes[0]


@pytest.fixture()
def booked(world):
    """CRM first, then the ERP — the order the estate's own story runs in."""
    crm = _deliver(world, _crm_request())
    sales = _deliver(world, _sales_request())
    assert crm["status"] == "succeeded", crm
    assert sales["status"] == "succeeded", sales
    return crm, sales


def _company(world, name: str):
    from app.graph import normalize_key

    return world.graph.get(object_id("Company", normalize_key(name))) or next(
        (
            c for c in world.graph.find("Company")
            if normalize_key(str(c.props.get("name") or "")) == normalize_key(name)
        ),
        None,
    )


# ---------------------------------------------------------------------------
# One company, two spellings
# ---------------------------------------------------------------------------


def test_the_two_books_land_one_company_each(booked, world):
    """Five accounts and four customers make five companies, not nine.

    The four the estate holds twice are the demo's whole premise: the ERP
    spells them with a legal suffix and the CRM without, and `normalize_key`
    collapses exactly that difference — so the ERP delivery *links* rather
    than minting a parallel book.
    """
    _crm, sales = booked
    assert len(world.graph.find("Company")) == len(fx.ACCOUNTS) == 5
    # Every ERP customer found a company the CRM had already minted.
    assert sales["linked"] == len(fx.CUSTOMERS) == 4
    assert sales["created"] == 0
    # Fairview is the fifth: a CRM account the ERP has never billed.
    assert _company(world, "Fairview Insurance Group") is not None


@pytest.mark.parametrize("crm_name, erp_name", [
    ("Northwind Traders", "Northwind Traders Inc."),
    ("Apex Manufacturing", "Apex Manufacturing Co."),
    ("Blue Harbor Logistics", "Blue Harbor Logistics LLC"),
    ("Summit Health Partners", "Summit Health Partners"),
])
def test_both_claims_are_retained_and_precedence_resolves(
    booked, world, crm_name, erp_name
):
    """The disagreement is a *read* of retained claims, not a data loss.

    Both systems claim `name`, in their own spelling, and both claims survive.
    What resolves is decided by the precedence order at read time — which is
    why reordering it re-resolves history rather than rewriting it.
    """
    company = _company(world, crm_name)
    assert company is not None, crm_name
    names = {c.source: c.value for c in company.claims if c.field == "name"}
    assert names["crm:crm-book"] == crm_name
    assert names["erp:erp-sales"] == erp_name
    # `erp` outranks `crm`, so the legal spelling is what resolves.
    assert company.props["name"] == erp_name

    # And the fields only one system holds are unopposed, whichever ranks
    # higher: the CRM is the only voice on an industry, the ERP on terms.
    assert company.props["industry"]
    assert company.props["payment_terms"]
    assert {c.source for c in company.claims if c.field == "payment_terms"} == {
        "erp:erp-sales"
    }


def test_a_company_carries_a_reversible_link_into_each_system(booked, world):
    """Four systems, four ids, one company — the edge the identity story runs
    on. Reversible because it is a Link with a method and a confidence, not a
    column that overwrote whatever was there before."""
    northwind = _company(world, "Northwind Traders")
    rows = {
        str(row.props.get("source")): row
        for _link, row in world.graph.neighbors(
            northwind.id, "company_identified_as", "out"
        )
    }
    assert set(rows) == {"crm-book", "erp-sales"}
    assert rows["crm-book"].props["external_id"] == "0010e4954cbbedf"
    assert rows["erp-sales"].props["external_id"] == "1000"
    # Both landed rows say which book a steward's decision would write into.
    assert {r.props["record_kind"] for r in rows.values()} == {"company"}
    # The ERP's row matched by name normalization, which is an exact method:
    # the resolver never had to guess.
    link = next(
        link for link, row in world.graph.neighbors(
            northwind.id, "company_identified_as", "out"
        )
        if row.props.get("source") == "erp-sales"
    )
    assert link.method == "exact"
    assert link.confidence >= 0.92


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


def test_contacts_mint_people_keyed_by_address(booked, world):
    assert len(world.graph.find("Person")) == len(fx.CONTACTS) == 9
    tom = world.graph.get(
        object_id("Person", "tom.berg@northwindtraders.example.com")
    )
    assert tom.props["name"] == "Tom Berg"
    assert tom.props["title"] == "Purchasing Manager"
    # `works_at` is a traversal, not a string on the person — and the
    # denormalized company_name rides alongside it for cheap reads.
    employers = [
        c.label for _link, c in world.graph.neighbors(tom.id, "works_at", "out")
    ]
    assert employers == [_company(world, "Northwind Traders").label]
    # The denormalized copy is what the company was called *when the person
    # was written*, which is the CRM's spelling — the ERP had not delivered
    # yet. The link is the answer that stays current; this is the cheap read
    # beside it, and the difference between the two is exactly why the link
    # exists.
    assert tom.props["company_name"] == "Northwind Traders"


def test_a_person_is_not_an_employee(booked, world):
    """The whole reason Person exists. Nothing here landed in the org chart."""
    assert world.graph.find("Employee") == []


def test_two_people_at_one_domain_stay_two_people(booked, world):
    """The near-miss that would break the book if identity were a spelling.

    Tom Berg and Maria Delgado share everything but a local part; their keys
    score well below the review floor, so nothing proposes merging them.
    """
    northwind_people = [
        p for p in world.graph.find("Person")
        if "northwindtraders" in str(p.props.get("email") or "")
    ]
    assert len(northwind_people) == 2
    assert {p.props["name"] for p in northwind_people} == {
        "Tom Berg", "Maria Delgado",
    }


def test_a_contact_with_no_address_mints_under_the_source_key(world):
    """The declared fallback. The live CRM carries an address on all nine
    contacts, so this is the one delivery here that is constructed rather than
    recorded — and it is constructed as two rows, because the property worth
    holding is that two address-less strangers stay two people."""
    out = _deliver(world, SyncRequest.model_validate({
        "source": "crm-book",
        "contacts": [
            {"external_id": "003aaa", "first_name": "Tom", "last_name": "Berg"},
            {"external_id": "003bbb", "first_name": "Tom", "last_name": "Berg"},
        ],
    }))
    assert out["status"] == "succeeded"
    assert out["people"] == 2
    for external in ("003aaa", "003bbb"):
        person = world.graph.get(object_id("Person", f"crm-book:{external}"))
        assert person is not None
        assert person.label == "Tom Berg"
        # Nothing was matched, so the identity link says so.
        link, _row = world.graph.neighbors(
            person.id, "person_identified_as", "out"
        )[0]
        assert link.method == "declared"
    assert len(world.graph.find("Person")) == 2


# ---------------------------------------------------------------------------
# Pipeline and orders
# ---------------------------------------------------------------------------


def test_opportunities_bind_to_the_company_they_are_pursued_with(booked, world):
    crm, _sales = booked
    assert crm["opportunities"] == len(fx.OPPORTUNITIES) == 8
    assert crm["pursued_with"] == 8
    assert crm["company_unresolved"] == 0
    summit = _company(world, "Summit Health Partners")
    deals = {
        deal.label: deal.props["stage"]
        for _link, deal in world.graph.neighbors(summit.id, "pursued_with", "in")
    }
    # The CRM's title-case vocabulary, slugged onto the ontology's declared
    # enum — a mapping decision, so it happens in the flow and not in the
    # connector.
    assert deals == {
        "Summit — Patient Portal Migration": "closed-won",
        "Summit — Records Digitization": "proposal",
    }


def test_orders_land_with_their_lines_and_both_edges(booked, world):
    _crm, sales = booked
    assert sales["orders"] == len(fx.SALES_ORDERS) == 5
    assert sales["lines"] == 11
    assert sales["sold_to"] == 5
    assert sales["sells"] == 11
    assert sales["customer_unresolved"] == sales["item_unresolved"] == 0

    order = world.graph.get(object_id("SalesOrder", "SO5002"))
    assert order.props["status"] == "partially-fulfilled"
    assert order.props["total_usd"] == 50200.0
    buyer = [c.label for _l, c in world.graph.neighbors(order.id, "sold_to", "out")]
    assert buyer == [_company(world, "Apex Manufacturing").label]
    lines = sorted(
        (link.props["ordinal"], line)
        for link, line in world.graph.neighbors(order.id, "has_so_line", "out")
    )
    assert [ordinal for ordinal, _ in lines] == [1, 2, 3]
    assert [line.props["sku"] for _, line in lines] == [
        "SCN-4200", "SVC-INSTALL", "LIC-CAPTURE",
    ]


def test_a_line_resolves_its_item_through_the_id_the_erp_actually_sends(
    booked, world
):
    """The transposition the whole feed shape exists for: the line names
    `"3001"`, the catalogue is keyed `SCN-2100`, and only the item row knows
    they are the same thing."""
    item = world.graph.get(object_id("Item", "SCN-2100"))
    assert item.props["external_id"] == "3001"
    assert item.props["item_type"] == "inventory"
    assert item.props["base_price_usd"] == 1250.0
    orders = {
        line.props["order_number"]
        for _link, line in world.graph.neighbors(item.id, "sells", "in")
    }
    assert orders == {"SO5000", "SO5001", "SO5003"}
    # And what a line charged is a fact about the line, not about the item:
    # SO5003 sold it under list.
    sold = {
        line.props["order_number"]: line.props["unit_price_usd"]
        for _link, line in world.graph.neighbors(item.id, "sells", "in")
    }
    assert sold["SO5003"] == 1150.0
    assert sold["SO5000"] == item.props["base_price_usd"]


def test_an_item_nobody_has_seen_is_counted_and_never_minted(world):
    """A line naming an id the catalogue cannot translate. Minting an Item out
    of it would put a SKU-less row in the book that the real one, when it
    arrives, could never be reconciled with."""
    out = _deliver(world, _sales_request(items=[]))
    assert out["status"] == "succeeded"
    assert out["items"] == 0
    assert world.graph.find("Item") == []
    assert out["lines"] == 11
    assert out["sells"] == 0
    assert out["item_unresolved"] == 11


def test_an_order_for_a_customer_this_delta_did_not_carry(world):
    """Counted on the aggregate, and the order still lands. A batch feed never
    suspends, and a customer synced last week resolves through its landed row
    rather than through the batch — which this deliberately does not have."""
    out = _deliver(world, _sales_request(customers=[]))
    assert out["status"] == "succeeded"
    assert out["orders"] == 5
    assert out["sold_to"] == 0
    assert out["customer_unresolved"] == 5
    assert world.graph.find("Company") == []


def test_a_later_order_resolves_through_a_customer_landed_earlier(world):
    """The other half of the same rule: the second delivery carries no
    customers at all and still binds every order, because the identity is read
    back off the SourceRecord the first delivery left behind."""
    _deliver(world, _sales_request(salesOrders=[]), key="customers-only")
    out = _deliver(world, _sales_request(customers=[], items=[]), key="orders-only")
    assert out["sold_to"] == 5
    assert out["customer_unresolved"] == 0
    assert out["sells"] == 11  # items resolved off the graph, not the batch


# ---------------------------------------------------------------------------
# Redelivery
# ---------------------------------------------------------------------------


def test_redelivery_changes_nothing(booked, world):
    before = world.graph.stats()
    again = _deliver(world, _crm_request())
    assert again["status"] == "succeeded"
    assert again["landed"] == 0
    assert again["unchanged"] == len(fx.ACCOUNTS) + len(fx.CONTACTS)
    # The second sight of an account links to the company the first minted.
    assert again["created"] == 0
    assert again["linked"] == len(fx.ACCOUNTS)
    after = world.graph.stats()
    assert after["by_type"] == before["by_type"]
    assert world.graph.check_invariants() == []


def test_an_identical_delivery_re_enters_the_same_run(world):
    first = _deliver(world, _crm_request(), key="crm:2026-08-17:0")
    second = _deliver(world, _crm_request(), key="crm:2026-08-17:0")
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["run_id"] == first["run_id"]


# ---------------------------------------------------------------------------
# The steward path
# ---------------------------------------------------------------------------


def _ambiguous_customer() -> SyncRequest:
    """One ERP customer whose name sits in the review band against a company
    the CRM already minted. `Northwnd Traders` scores 0.75 — close enough that
    minting a second Northwind would be wrong, far enough that asserting the
    link would be a guess. That band is what the steward queue is for."""
    return SyncRequest.model_validate({
        "source": "erp-sales",
        "customers": [{
            "external_id": "9999", "name": "Northwnd Traders",
            "payment_terms": "Net 10",
        }],
    })


def test_an_ambiguous_company_raises_a_task_and_asserts_nothing(booked, world):
    before = len(world.graph.find("Company"))
    out = _deliver(world, _ambiguous_customer())
    assert out["awaiting_steward"] == 1
    assert out["linked"] == out["created"] == 0
    # Nothing minted, nothing linked — the landed row is all there is.
    assert len(world.graph.find("Company")) == before

    task = next(
        t for t in world.tasks.list()
        if t.kind == "match_review" and "Northwnd" in t.title
    )
    # The Task hangs off the landed row, which is the only thing that exists.
    assert world.graph.get(task.case_ref).type == "SourceRecord"
    assert task.queue == DATA_STEWARD_QUEUE
    assert task.flow_id == SALES_FLOW_ID
    assert task.proposal.target == "company-master"
    assert any(ref.startswith("graph:Company/") for ref in task.refs)


def test_the_queue_comes_from_the_mapping_not_the_flow(booked, world):
    """`Mapping.steward_queue` is the contract, and both commerce feeds
    declare the same desk while the vendor feeds keep theirs."""
    from app.mappings import steward_queue_for

    assert steward_queue_for("crm-book") == DATA_STEWARD_QUEUE
    assert steward_queue_for("erp-sales") == DATA_STEWARD_QUEUE
    assert steward_queue_for("erp-vendors") == "vendor-stewardship"
    # A feed with no declared Mapping falls back to where such rows have
    # always gone, rather than to nowhere.
    assert steward_queue_for("no-such-feed") == "vendor-stewardship"


def test_a_steward_approving_links_the_company_the_run_would_not(booked, world):
    """The routing this stage adds: the decision is applied against the
    Company book because the landed row says `company`, where the same code
    path used to be able to write a Vendor and nothing else."""
    from app.tasks import TaskDecision

    _deliver(world, _ambiguous_customer())
    task = next(
        t for t in world.tasks.list()
        if t.kind == "match_review" and "Northwnd" in t.title
    )
    northwind = _company(world, "Northwind Traders")
    decided = task.model_copy(update={
        "decision": TaskDecision(
            verdict="corrected", decided_by="steward@naviant.com",
            note="same company, the ERP row is a typo",
            payload={"object_id": northwind.id},
        ),
    })
    result = world.sync.apply_match_decision(decided)

    assert result["action"] == "linked"
    assert result["object_type"] == "Company"
    assert result["object_id"] == northwind.id
    # No second Northwind was minted, and the landed row now hangs off the one
    # that existed — under a human method, which is the point of asking.
    assert len(world.graph.find("Company")) == len(fx.ACCOUNTS)
    landed = world.graph.get(object_id("SourceRecord", "erp-sales:9999"))
    link = next(
        link for link, row in world.graph.neighbors(
            northwind.id, "company_identified_as", "out"
        )
        if row.id == landed.id
    )
    assert link.method == "human"
    assert link.props["decided_by"] == "steward@naviant.com"
    # The steward's spelling is kept as an alias, so the next delivery under
    # that name resolves outright.
    assert "Northwnd Traders" in northwind.props["aliases"]


def test_a_steward_rejecting_mints_a_company_not_a_vendor(booked, world):
    from app.tasks import TaskDecision

    _deliver(world, _ambiguous_customer())
    task = next(
        t for t in world.tasks.list()
        if t.kind == "match_review" and "Northwnd" in t.title
    )
    vendors_before = len(world.graph.find("Vendor"))
    result = world.sync.apply_match_decision(task.model_copy(update={
        "decision": TaskDecision(
            verdict="rejected", decided_by="steward@naviant.com",
            note="different firm entirely",
        ),
    }))
    assert result["action"] == "created"
    assert result["object_type"] == "Company"
    assert len(world.graph.find("Vendor")) == vendors_before
    minted = world.graph.get(result["object_id"])
    assert minted.type == "Company"
    assert minted.label == "Northwnd Traders"
    # A Company has no `onboarding` state to claim, so nothing invented one.
    assert minted.props.get("status") in (None, "")


def test_a_vendor_decision_still_writes_a_vendor(world):
    """The routing did not move the ground under the feed it came from."""
    from app.tasks import TaskDecision

    world.sync.deliver(SyncRequest.model_validate({
        "source": "erp-vendors",
        "vendors": [{"external_id": "2000", "name": "Lakeshore Paper & Packaging"}],
    }), actor="system:test")
    world.sync.deliver(SyncRequest.model_validate({
        "source": "erp-vendors",
        "vendors": [{"external_id": "2099", "name": "Lakeshore Paper & Packging"}],
    }), actor="system:test")
    task = next(
        t for t in world.tasks.list()
        if t.kind == "match_review" and "Lakeshore Paper & Packging" in t.title
    )
    assert task.queue == "vendor-stewardship"
    result = world.sync.apply_match_decision(task.model_copy(update={
        "decision": TaskDecision(
            verdict="rejected", decided_by="ap@naviant.com", note="new supplier",
        ),
    }))
    assert result["object_type"] == "Vendor"
    assert world.graph.get(result["object_id"]).props["status"] == "onboarding"


# ---------------------------------------------------------------------------
# The feed's own paperwork
# ---------------------------------------------------------------------------


def test_both_feeds_show_up_on_the_sources_lens(booked, world):
    """The registry refactor, from the outside: a feed registers once and the
    Sources screen can see it, instead of a dict a thousand lines down."""
    rows = {(s.source, s.kind): s for s in world.sync.sources()}
    assert rows[("crm-book", "crm-book")].flow_id == CRM_FLOW_ID
    assert rows[("erp-sales", "sales-records")].flow_id == SALES_FLOW_ID
    assert rows[("crm-book", "crm-book")].last_status == "succeeded"
    assert rows[("erp-sales", "sales-records")].records_landed == len(fx.CUSTOMERS)


def test_every_write_went_through_the_gateway(booked, world):
    """No claim in either book was written outside the ledger."""
    entries = [
        e for e in world.gateway.ledger.list()
        if e.flow_id in (CRM_FLOW_ID, SALES_FLOW_ID)
    ]
    assert entries
    assert {e.outcome for e in entries} == {"committed"}
    assert sum(e.write_count for e in entries) > 0


def test_the_exports_draw_the_steps_the_engine_actually_ran(booked, world, client):
    """The carried debt this stage pays off. Every step id in the declarative
    export is one the run journal contains — a map of a flow nobody can watch
    is the divergence the transparency bar exists to catch."""
    for flow_id in (CRM_FLOW_ID, SALES_FLOW_ID, "vendor-sync",
                    "purchase-order-sync"):
        defn = client.get(f"/api/v1/flows/{flow_id}").json()
        exported = [s["id"] for s in defn["steps"]]
        runs = world.engine.list(flow_id=flow_id)
        if not runs:
            continue
        journaled = [s.step_id for s in runs[0].steps]
        assert journaled == exported, flow_id
