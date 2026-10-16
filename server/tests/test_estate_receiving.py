"""The receiving feed: the third leg, from the ERP's dock to a matched invoice.

Everything downstream of `estate._build_erp_receiving` was built and unreachable
— `receiving-sync`, the `ReceivingRecord` wire shape, the
`ReceivingReport`/`ReceivingReportLine` types and the matcher's
`receives_against` traversal all existed and worked, and nothing had ever
delivered them. So these tests are deliberately end to end: the estate is faked
and the door is not, and the last one goes source row → feed → landed graph →
the matcher seeing three legs, because a feed that renames correctly into a
graph nobody can traverse would have passed a narrower test and left the match
two-way.

The rows below are the shapes the estate really serves, and they carry the three
conditions the seed and the live pulse both produce: a **split delivery** (two
receipts against one order), a **partial** (an order left short), and **service
orders billed with no receipt at all**, which is a genuine two-way match and is
asserted as such rather than papered over. They live here rather than in
`estate_fixtures` because a sibling lane holds that file; the additions belong
there once it is free.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from app.estate import (
    ERP_PURCHASE_ORDERS,
    ERP_RECEIVING,
    ERP_VENDORS,
    FEEDS,
    EstateConnector,
    EstateError,
    InMemoryConnectorStateStore,
)
from app.graph import object_id
from app.matching import MatchLine, MatchTolerances, match_invoice
from tests import estate_fixtures as fx
from tests import world as test_world

PROJECT = "cumulus-demo-mitchell-suter"


# ---------------------------------------------------------------------------
# The source, as the ERP serves it
# ---------------------------------------------------------------------------

#: Two orders beyond the three `estate_fixtures` records, so the receipts below
#: have somewhere real to book against. Same shape as the recorded rows: the
#: vendor and every item named by internal id only, which is what the feed's
#: declared dereference exists for. Their `updatedAt` sits *after* the receipts
#: written against them on purpose — the estate bumps a purchase order's
#: watermark when a receipt lands, so an order turning up in a `purchaseOrders`
#: delta on the strength of a receipt is the normal case, and this fixture makes
#: sure nothing here is quietly relying on the opposite.
EXTRA_ORDERS: list[dict] = [
    {"id": "6014", "tranId": "PO6014", "vendorId": "2000",
     "tranDate": "2026-08-26", "status": "Pending Billing", "total": 3840.0,
     "memo": "Archive box stock",
     "lines": [{"itemId": "3002", "quantity": 40, "rate": 96, "amount": 3840}],
     "createdAt": "2026-08-26T09:12:00.114233+00:00",
     "updatedAt": "2026-09-02T12:26:28.127580+00:00"},
    {"id": "6015", "tranId": "PO6015", "vendorId": "2001",
     "tranDate": "2026-08-27", "status": "Partially Received", "total": 14700.0,
     "memo": "Scanner and licence refresh",
     "lines": [
         {"itemId": "3001", "quantity": 6, "rate": 1250, "amount": 7500},
         {"itemId": "3003", "quantity": 3, "rate": 2400, "amount": 7200},
     ],
     "createdAt": "2026-08-27T14:03:11.882014+00:00",
     "updatedAt": "2026-09-01T18:40:02.551907+00:00"},
]

#: What the dock signed for. No status on a receipt and no money anywhere on
#: one — the ERP holds neither, and the absence is the point of the leg.
RECEIVING_REPORTS: list[dict] = [
    # The golden thread: one shipment, the whole order, nothing to argue about.
    {"id": "7000", "tranId": "RR7000", "purchaseOrderId": "6013",
     "vendorId": "2000", "tranDate": "2026-08-31", "receivedBy": "Curtis Boyd",
     "memo": "Crating stock — complete",
     "lines": [{"line": 1, "itemId": "3002", "quantityOrdered": 35,
                "quantityReceived": 35}],
     "createdAt": "2026-08-31T15:20:41.339020+00:00",
     "updatedAt": "2026-08-31T15:20:41.339020+00:00"},
    # A split delivery: half the pallet, then the balance a day later, both
    # against PO6014. Two receipts, one order.
    {"id": "7001", "tranId": "RR7001", "purchaseOrderId": "6014",
     "vendorId": "2000", "tranDate": "2026-09-01", "receivedBy": "Leon Tran",
     "memo": "Archive box stock — part shipment",
     "lines": [{"line": 1, "itemId": "3002", "quantityOrdered": 40,
                "quantityReceived": 25}],
     "createdAt": "2026-09-01T11:04:19.771503+00:00",
     "updatedAt": "2026-09-01T11:04:19.771503+00:00"},
    {"id": "7002", "tranId": "RR7002", "purchaseOrderId": "6014",
     "vendorId": "2000", "tranDate": "2026-09-02", "receivedBy": "Leon Tran",
     "memo": "Archive box stock — balance",
     "lines": [{"line": 1, "itemId": "3002", "quantityOrdered": 40,
                "quantityReceived": 15}],
     "createdAt": "2026-09-02T12:26:28.127580+00:00",
     "updatedAt": "2026-09-02T12:26:28.127580+00:00"},
    # A partial: four of the six scanners turned up with all three licences,
    # which is why the ERP left PO6015 at `Partially Received`.
    {"id": "7003", "tranId": "RR7003", "purchaseOrderId": "6015",
     "vendorId": "2001", "tranDate": "2026-09-01", "receivedBy": "Nathan Pryce",
     "memo": "Scanner and licence refresh — part shipment",
     "lines": [
         {"line": 1, "itemId": "3001", "quantityOrdered": 6,
          "quantityReceived": 4},
         {"line": 2, "itemId": "3003", "quantityOrdered": 3,
          "quantityReceived": 3},
     ],
     "createdAt": "2026-09-01T18:40:02.551907+00:00",
     "updatedAt": "2026-09-01T18:40:02.551907+00:00"},
    # And nothing at all against PO6011 or PO6012, the two `SVC-INSTALL`
    # orders. Nobody signs for a service call, so those bills will only ever
    # have two legs — the case `test_a_service_order_is_a_two_way_match` holds.
]


def estate(**over) -> fx.FakeEstate:
    """The recorded estate with the receiving resource served beside it."""
    resources = {
        **fx.RESOURCES,
        ("erp", "purchaseOrders"): [*fx.PURCHASE_ORDERS, *EXTRA_ORDERS],
        ("erp", "receivingReports"): RECEIVING_REPORTS,
    }
    return fx.FakeEstate(resources=resources, **over)


def build(source: fx.FakeEstate | None = None, **over) -> EstateConnector:
    source = source or estate()
    delivered: list[dict] = []
    connector = EstateConnector(
        InMemoryConnectorStateStore(),
        project=PROJECT,
        namespace="naviant",
        fetch=source.fetch,
        deliver=over.pop("deliver", None) or (
            lambda payload: (delivered.append(payload), {"runs": []})[1]
        ),
        **over,
    )
    connector.estate = source  # type: ignore[attr-defined]  — test handle
    connector.delivered = delivered  # type: ignore[attr-defined]
    return connector


def door(client):
    def deliver(payload: dict) -> dict:
        response = client.post("/api/v1/sources/deliver", json=payload)
        if response.status_code != 202:
            raise EstateError(f"door → {response.status_code}: {response.text}")
        return response.json()

    return deliver


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


def pull_commerce(client) -> EstateConnector:
    """Vendors, then orders, then receipts — the registry's own order."""
    connector = build(deliver=door(client))
    for feed in (ERP_VENDORS, ERP_PURCHASE_ORDERS, ERP_RECEIVING):
        assert connector.pull(feed)["outcome"] == "delivered", feed
    return connector


# -- the mechanical translation ----------------------------------------------


def test_the_feed_renames_and_dereferences_the_two_declared_keys():
    connector = build()
    out = connector.pull(ERP_RECEIVING)

    assert out["outcome"] == "delivered"
    assert out["pulled"] == {"receivingReports": len(RECEIVING_REPORTS)}
    reports = {
        r["receipt_number"]: r
        for payload in connector.delivered
        for r in payload["receiving_reports"]
    }
    assert set(reports) == {"RR7000", "RR7001", "RR7002", "RR7003"}

    golden = reports["RR7000"]
    assert golden["external_id"] == "7000"
    assert golden["received_date"] == "2026-08-31"
    # The dereference that carries the whole feed: the row says `6013` and a
    # PurchaseOrder's natural key is `PO6013`, so a report delivered with the
    # internal id would land with no edge to the order it books against.
    assert golden["po_number"] == "PO6013"
    assert [line["sku"] for line in golden["lines"]] == ["KIT-ROLLER"]
    assert golden["lines"][0]["description"] == "Scanner Roller Kit"
    assert golden["lines"][0]["quantity_received"] == 35

    # Line order is the source's, so the natural key `report#ordinal` means the
    # same physical line on every redelivery.
    partial = reports["RR7003"]
    assert [line["sku"] for line in partial["lines"]] == [
        "SCN-2100", "LIC-CAPTURE",
    ]
    assert [line["quantity_received"] for line in partial["lines"]] == [4.0, 3.0]


def test_nothing_the_source_does_not_carry_is_filled_in():
    """`_build_erp_vendors` refuses to invent a tax id; this is the same rule.

    A receipt has no status — completeness is the *order's* property, restated
    by the ERP from the sum of what has been received — and a line has no
    condition, because `good | damaged | short` is a judgment and a dock that
    counts has not made one. Defaulting either would put a claim in the graph
    that reads as the ERP's and is nobody's.
    """
    connector = build()
    connector.pull(ERP_RECEIVING)
    reports = [
        r for payload in connector.delivered
        for r in payload["receiving_reports"]
    ]

    assert {r["status"] for r in reports} == {""}
    assert {line["condition"] for r in reports for line in r["lines"]} == {""}
    # And no money anywhere on the wire, because there is none on the source.
    money = {"amount", "amount_usd", "total", "total_usd", "unit_price_usd"}
    assert all(money.isdisjoint(line) for r in reports for line in r["lines"])
    assert all(money.isdisjoint(r) for r in reports)


def test_a_split_delivery_costs_one_lookup_not_two():
    """`_deref` memoizes for the whole pull, which is what makes the
    order-row lookup affordable: RR7001 and RR7002 both name PO6014."""
    connector = build()
    connector.pull(ERP_RECEIVING)

    orders = [
        url for url in connector.estate.calls_for("purchaseOrders")
        if url.endswith("/purchaseOrders/6014")
    ]
    assert len(orders) == 1
    # Three distinct orders referenced, three GETs — never one per receipt.
    assert len(connector.estate.calls_for("purchaseOrders")) == 3


def test_the_watermark_is_updated_at_and_a_repull_finds_nothing():
    connector = build()
    connector.pull(ERP_RECEIVING)
    mark = connector.state(ERP_RECEIVING).watermarks["receivingReports"]
    assert mark == max(r["updatedAt"] for r in RECEIVING_REPORTS)

    second = connector.pull(ERP_RECEIVING)
    asked = urlparse(
        connector.estate.calls_for("receivingReports")[-1]
    ).query
    assert parse_qs(asked)["updatedSince"] == [mark]
    assert second["outcome"] == "unchanged"


def test_orders_are_pulled_before_receipts():
    """Registry order, and the one thing it decides.

    `sync._apply_receipts` can only draw `receives_against` if the
    PurchaseOrder is already in the graph, and counts `po_missing` when it is
    not. Receipts below orders makes the edge on the first pull; above them it
    would depend on whichever later window happened to touch the order again.
    """
    order = [f.feed for f in FEEDS]
    assert order.index(ERP_PURCHASE_ORDERS) < order.index(ERP_RECEIVING)


# -- through the real door ----------------------------------------------------


def test_the_reports_land_bound_to_the_orders_they_book_against(client, world):
    pull_commerce(client)

    report = world.graph.get(object_id("ReceivingReport", "RR7000"))
    assert report is not None
    assert report.props["received_date"] == "2026-08-31"
    orders = [
        o.props["po_number"]
        for _, o in world.graph.neighbors(report.id, "receives_against", "out")
    ]
    assert orders == ["PO6013"]

    lines = [
        line for _, line in
        world.graph.neighbors(report.id, "has_receiving_line", "out")
    ]
    assert [line.props["sku"] for line in lines] == ["KIT-ROLLER"]
    assert lines[0].props["quantity_received"] == 35.0
    assert lines[0].props["receipt_number"] == "RR7000"

    # Every report bound; nothing stranded.
    assert world.graph.stats()["by_type"]["ReceivingReport"] == 4
    assert world.graph.stats()["by_type"]["ReceivingReportLine"] == 5
    for number in ("RR7000", "RR7001", "RR7002", "RR7003"):
        found = world.graph.get(object_id("ReceivingReport", number))
        assert world.graph.neighbors(found.id, "receives_against", "out")
    assert world.graph.check_invariants() == []


def test_two_receipts_against_one_order_both_bind(client, world):
    """A split delivery, landed as two reports on one order rather than merged.

    Merging them would be the connector deciding what a shipment is; the graph
    keeps both and the order carries two `receives_against` edges, which is
    what the dock actually did.
    """
    pull_commerce(client)

    order = world.graph.get(object_id("PurchaseOrder", "PO6014"))
    reports = sorted(
        o.props["receipt_number"]
        for _, o in world.graph.neighbors(order.id, "receives_against", "in")
    )
    assert reports == ["RR7001", "RR7002"]

    received = sum(
        line.props["quantity_received"]
        for number in reports
        for _, line in world.graph.neighbors(
            world.graph.get(object_id("ReceivingReport", number)).id,
            "has_receiving_line", "out",
        )
    )
    assert received == 40.0  # 25 then 15, the whole order across two shipments


def test_a_repull_of_the_same_window_changes_nothing(client, world):
    connector = pull_commerce(client)
    before = world.graph.stats()

    state = connector.state(ERP_RECEIVING)
    state.watermarks = {}
    connector.store.put(state)
    assert connector.pull(ERP_RECEIVING)["outcome"] == "delivered"
    assert world.graph.stats() == before


# -- the gate: three legs -----------------------------------------------------


def _legs(world, po_number: str) -> dict:
    """The legs as the AP flow reads them — the real gather, not a stand-in."""
    return world.payables._gather_match_context("", po_number)


def test_an_invoice_matches_on_all_three_legs(client, world):
    """The gate. Ordered, received, billed — and the third leg is there.

    Before this feed existed the receiving list came back empty for every
    invoice in production, so a bill this clean was `blocked: no-receipt`. The
    only thing that changed is that something now delivers the reports.
    """
    pull_commerce(client)
    legs = _legs(world, "PO6013")

    assert legs["po_numbers"] == ["PO6013"]
    assert legs["receipt_numbers"] == ["RR7000"]
    assert [line["sku"] for line in legs["receipt_lines"]] == ["KIT-ROLLER"]

    result = match_invoice(
        [MatchLine(ordinal=1, sku="KIT-ROLLER",
                   description="Scanner Roller Kit", quantity=35,
                   unit_price_usd=96.0, amount_usd=3360.0)],
        [MatchLine.model_validate(line) for line in legs["po_lines"]],
        [MatchLine.model_validate(line) for line in legs["receipt_lines"]],
        MatchTolerances(),
        invoice_total_usd=3360.0,
    )
    assert result.verdict == "matched"
    pairing = result.pairings[0]
    assert pairing.po_number == "PO6013"
    assert pairing.receipt_number == "RR7000"
    assert pairing.received_quantity == 35.0
    assert pairing.quantity_variance == 0.0
    assert result.summary() == (
        "matched 1 line(s) against 1 PO(s) and 1 receipt(s)"
    )


def test_a_service_order_is_a_two_way_match_and_says_so(client, world):
    """Nobody signs for a service call, so `SVC-INSTALL` has no third leg.

    Roughly four orders in the seed are like this, and the honest outcome is a
    named `no-receipt` on a real threshold — not a receipt invented to make the
    match look complete.
    """
    pull_commerce(client)
    legs = _legs(world, "PO6011")

    assert legs["po_numbers"] == ["PO6011"]
    assert legs["receipt_lines"] == [] and legs["receipt_numbers"] == []

    result = match_invoice(
        [MatchLine(ordinal=1, sku="SVC-INSTALL",
                   description="On-site Installation", quantity=2,
                   unit_price_usd=640.0, amount_usd=1280.0)],
        [MatchLine.model_validate(line) for line in legs["po_lines"]],
        [],
        MatchTolerances(),
        invoice_total_usd=1280.0,
    )
    assert result.verdict == "blocked"
    assert result.pairings[0].verdict == "no-receipt"
    assert result.pairings[0].po_number == "PO6011"


def test_a_partial_receipt_bills_short_of_what_was_ordered(client, world):
    """Four of six scanners landed, all three licences did.

    Billing the four that arrived matches on three legs; billing all six is a
    quantity variance against the receipt, which is the sequencing question the
    receiving leg exists to answer.
    """
    pull_commerce(client)
    legs = _legs(world, "PO6015")
    assert legs["receipt_numbers"] == ["RR7003"]

    po_lines = [MatchLine.model_validate(line) for line in legs["po_lines"]]
    receipt_lines = [
        MatchLine.model_validate(line) for line in legs["receipt_lines"]
    ]
    honest = match_invoice(
        [MatchLine(ordinal=1, sku="SCN-2100", description="Desktop Scanner 2100",
                   quantity=4, unit_price_usd=1250.0, amount_usd=5000.0)],
        po_lines, receipt_lines, MatchTolerances(), invoice_total_usd=5000.0,
    )
    assert honest.verdict == "matched"
    assert honest.pairings[0].received_quantity == 4.0

    early = match_invoice(
        [MatchLine(ordinal=1, sku="SCN-2100", description="Desktop Scanner 2100",
                   quantity=6, unit_price_usd=1250.0, amount_usd=7500.0)],
        po_lines, receipt_lines, MatchTolerances(), invoice_total_usd=7500.0,
    )
    assert early.verdict == "variance"
    assert early.pairings[0].verdict == "quantity-variance"
    assert early.pairings[0].quantity_variance == 2.0


def test_a_split_delivery_reaches_the_matcher_as_two_receipt_lines(
    client, world
):
    """Both shipments are on the table, and the matcher pairs a line to one.

    Stated rather than asserted away: `match_invoice` pairs each invoice line
    with a single best receipt line and does not sum a split delivery, so an
    invoice for the whole forty reads as a quantity variance against the
    twenty-five that came first. That is the matcher's behaviour and this lane
    changed none of it — the feed's job is to put both receipts in front of it,
    which is what the graph now does.
    """
    pull_commerce(client)
    legs = _legs(world, "PO6014")

    assert legs["receipt_numbers"] == ["RR7001", "RR7002"]
    assert sorted(line["quantity"] for line in legs["receipt_lines"]) == [
        15.0, 25.0,
    ]

    result = match_invoice(
        [MatchLine(ordinal=1, sku="KIT-ROLLER",
                   description="Scanner Roller Kit", quantity=40,
                   unit_price_usd=96.0, amount_usd=3840.0)],
        [MatchLine.model_validate(line) for line in legs["po_lines"]],
        [MatchLine.model_validate(line) for line in legs["receipt_lines"]],
        MatchTolerances(),
        invoice_total_usd=3840.0,
    )
    assert result.pairings[0].receipt_number in ("RR7001", "RR7002")
    assert result.pairings[0].verdict == "quantity-variance"
