"""The accounts-payable Flow, end to end.

The doctrinal assertions:

- a vendor is an *entity*: the first ModelCall reads names, the resolver decides
  identity against the vendor master, and two systems' ids for one company
  resolve to one Vendor;
- extraction is one-shot against the vendor's exemplar when we have one and a
  three-model adjudicated ensemble when we do not, and verifying the ensemble
  writes the exemplar — so the vendor's *next* invoice is one-shot;
- the three-way match is retained as a MatchSet, and "no receiving report yet"
  is a state rather than a failure;
- the anomaly screen is scored against the vendor's own history with governed
  thresholds;
- the posting rule's values come from the governed document;
- a clerk's correction re-runs the match rather than overriding it.
"""

import pytest

from app.graph import object_id
from app.matching import MatchLine, MatchTolerances, match_invoice
from app.models import AssetUpdate
from app.payables import seed_payables_if_empty
from app.sync import SyncRequest
from tests import world as test_world


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


def _vendors(world):
    world.sync.deliver(SyncRequest.model_validate({
        "source": "erp-vendors",
        "watermark": "2026-08-01T00:00:00Z",
        "vendors": [
            {
                "external_id": "V-10041", "name": "Acme Industrial Inc.",
                "tax_id": "94-2711883", "status": "active",
                "payment_terms": "NET30",
            },
            {
                "external_id": "V-10088", "name": "Northwind Traders LLC",
                "tax_id": "81-4410072", "status": "active",
            },
        ],
    }), actor="system:test")


def _orders(world):
    world.sync.deliver(SyncRequest.model_validate({
        "source": "erp-purchasing",
        "purchase_orders": [{
            "external_id": "PO-7741", "po_number": "PO-7741",
            "vendor_external_id": "V-10041", "vendor_name": "Acme Industrial Inc.",
            "ordered_date": "2026-08-05", "status": "open",
            "lines": [
                {"sku": "STL-0250", "description": "Hot-rolled steel plate, 1/4 in",
                 "quantity_ordered": 2.0, "unit_price_usd": 486.00},
                {"sku": "FAS-M12", "description": "Grade 8 hex bolt, M12 x 60mm, box of 100",
                 "quantity_ordered": 3.0, "unit_price_usd": 128.75},
            ],
        }],
    }), actor="system:test")


def _receipts(world, short: float = 0.0):
    world.sync.deliver(SyncRequest.model_validate({
        "source": "wms-receiving",
        "receiving_reports": [{
            "external_id": "RR-5501", "receipt_number": "RR-5501",
            "po_number": "PO-7741", "received_date": "2026-08-11",
            "status": "complete",
            "lines": [
                {"sku": "STL-0250", "description": "Hot-rolled steel plate, 1/4 in",
                 "quantity_received": 2.0 - short},
                {"sku": "FAS-M12", "description": "Grade 8 hex bolt, M12 x 60mm, box of 100",
                 "quantity_received": 3.0},
            ],
        }],
    }), actor="system:test")


# ---- the governed read -----------------------------------------------------


def test_posting_values_come_from_the_document(world):
    policy = world.payables.read_policy()
    assert policy.found_asset and policy.limit_usd == 2500.0
    assert policy.source_ref.endswith("@rev1")


def test_editing_the_posting_limit_changes_the_flow(world):
    _vendors(world)
    _orders(world)
    _receipts(world)
    content = world.knowledge.repo.get("invoice-posting-policy").content
    world.knowledge.update("invoice-posting-policy", AssetUpdate(
        content=content.replace("Auto-post limit: $2,500.00", "Auto-post limit: $500.00")
    ))
    record = world.payables.submit(
        "ap@customer.example", filename="acme.pdf",
        hint="vendor=acme-industrial po=PO-7741 invoice=ACME-1 lines=STL-0250:2,FAS-M12:3",
    )
    assert record.state == "pending"
    assert "$500.00 auto-post limit" in record.verdict_reason
    assert record.policy_ref.endswith("@rev2")


# ---- identity --------------------------------------------------------------


def test_two_systems_one_vendor(world):
    _vendors(world)
    world.sync.deliver(SyncRequest.model_validate({
        "source": "procurement-coupa",
        "vendors": [{
            "external_id": "C-5501", "name": "ACME INDUSTRIAL, INCORPORATED",
            "tax_id": "94-2711883", "status": "active",
        }],
    }), actor="system:test")
    acme = world.graph.get(object_id("Vendor", "Acme Industrial Inc."))
    assert acme is not None
    sources = {
        str(other.props.get("source"))
        for _, other in world.graph.neighbors(acme.id, "identified_as", "out")
    }
    # One canonical vendor, both systems' ids hanging off it.
    assert sources == {"erp-vendors", "procurement-coupa"}
    assert len(world.graph.find("Vendor")) == 2


def test_an_ambiguous_name_waits_for_a_steward_and_asserts_nothing(world):
    _vendors(world)
    before = len(world.graph.find("Vendor"))
    world.sync.deliver(SyncRequest.model_validate({
        "source": "legacy-ap-extract",
        "vendors": [{"external_id": "L-77", "name": "Acme Industial Inc"}],
    }), actor="system:test")
    # No new vendor, no new link: a batch feed escalates without asserting.
    assert len(world.graph.find("Vendor")) == before
    task = next(
        t for t in world.tasks.list(kind="match_review") if "Acme" in t.title
    )
    assert task.queue == "vendor-stewardship" and task.status == "open"

    decided = world.tasks.decide(
        task.id, "approved", "steward@customer.example",
        note="Same company, misspelled in the legacy extract.",
    )
    result = world.sync.apply_match_decision(decided)
    assert result["action"] == "linked"
    acme = world.graph.get(object_id("Vendor", "Acme Industrial Inc."))
    sources = {
        str(other.props.get("source"))
        for _, other in world.graph.neighbors(acme.id, "identified_as", "out")
    }
    assert "legacy-ap-extract" in sources


def test_the_invoice_path_resolves_against_the_master(world):
    _vendors(world)
    record = world.payables.submit(
        "ap@customer.example", filename="acme.pdf",
        hint="vendor=acme-industrial nopo invoice=ACME-9 lines=PPE-GLV:1 total=80.00",
    )
    assert record.vendor_resolution == "linked"
    assert record.vendor_id == object_id("Vendor", "Acme Industrial Inc.")
    assert record.vendor_method in ("normalized-name", "fuzzy-name", "tax-id")


def test_an_unknown_vendor_is_minted_onboarding(world):
    _vendors(world)
    record = world.payables.submit(
        "ap@customer.example", filename="new.pdf",
        hint="newvendor=meridian-supply nopo invoice=MER-1 lines=PPE-GLV:1 total=80.00",
    )
    assert record.vendor_resolution == "new"
    vendor = world.graph.get(record.vendor_id)
    assert vendor.props["status"] == "onboarding"


# ---- extraction paths and the flywheel ------------------------------------


def test_known_vendor_takes_the_one_shot_path(world):
    _vendors(world)
    _orders(world)
    _receipts(world)
    record = world.payables.submit(
        "ap@customer.example", filename="acme.pdf",
        hint="vendor=acme-industrial po=PO-7741 invoice=ACME-2 lines=STL-0250:2,FAS-M12:3",
    )
    assert record.extraction_path == "one-shot"
    assert record.exemplar_ref.startswith("knowledge:vendor-exemplar-acme-industrial")
    assert record.state == "posted"
    run = world.engine.get(record.run_id)
    assert {s.step_id for s in run.steps} >= {"m1", "z1", "x1", "r1", "m2", "c1", "c2"}
    assert not any(s.step_id.startswith("m2a") for s in run.steps)


def test_unknown_vendor_runs_the_ensemble_and_verifying_writes_the_exemplar(world):
    _vendors(world)
    record = world.payables.submit(
        "ap@customer.example", filename="mer.pdf",
        hint="newvendor=meridian-supply nopo invoice=MER-1 lines=PPE-GLV:2 total=190.00",
    )
    assert record.extraction_path == "ensemble"
    assert record.state == "pending"
    run = world.engine.get(record.run_id)
    assert {"m2a", "m2b", "m2c", "x2"} <= {s.step_id for s in run.steps}
    task = world.tasks.get(record.task_id)
    assert task.kind == "curation" and task.queue == "ap-clerk"

    decided = world.tasks.decide(
        task.id, "approved", "dana@customer.example", note="Fields verified.",
    )
    world.payables.resume_for_task(decided)
    # The exemplar is a real governed-knowledge write, keyed by the vendor id.
    shelf = [a for a in world.knowledge.repo.all() if "exemplar" in a.tags]
    assert any("meridian" in a.id for a in shelf)
    vendor = world.graph.get(record.vendor_id)
    # A human vouched for it, so the vendor graduates out of onboarding.
    assert vendor.props["status"] == "active"


def test_the_next_invoice_from_a_verified_vendor_is_one_shot(world):
    _vendors(world)
    first = world.payables.submit(
        "ap@customer.example", filename="mer1.pdf",
        hint="newvendor=meridian-supply nopo invoice=MER-1 lines=PPE-GLV:2 total=190.00",
    )
    decided = world.tasks.decide(first.task_id, "approved", "dana@customer.example")
    world.payables.resume_for_task(decided)
    # The posting gate for the no-PO invoice, then done with the first run.
    run = world.engine.get(first.run_id)
    if run.status == "suspended":
        world.payables.resume_for_task(
            world.tasks.decide(run.open_task_id, "approved", "dana@customer.example")
        )

    second = world.payables.submit(
        "ap@customer.example", filename="mer2.pdf",
        hint="newvendor=meridian-supply nopo invoice=MER-2 lines=PPE-GLV:1 total=112.00",
    )
    assert second.extraction_path == "one-shot"
    assert second.vendor_id == first.vendor_id
    assert "meridian" in second.exemplar_ref


def test_a_rejected_verification_denies_and_writes_no_exemplar(world):
    _vendors(world)
    record = world.payables.submit(
        "ap@customer.example", filename="mer.pdf",
        hint="newvendor=meridian-supply nopo invoice=MER-1 lines=PPE-GLV:2 total=190.00",
    )
    decided = world.tasks.decide(
        record.task_id, "rejected", "dana@customer.example",
        note="Not our invoice — wrong company entirely.",
    )
    resumed = world.payables.resume_for_task(decided)
    assert resumed.state == "denied"
    assert not any(
        "meridian" in a.id for a in world.knowledge.repo.all() if "exemplar" in a.tags
    )


# ---- the three-way match --------------------------------------------------


def test_a_clean_invoice_matches_its_po_and_receipt(world):
    _vendors(world)
    _orders(world)
    _receipts(world)
    record = world.payables.submit(
        "ap@customer.example", filename="acme.pdf",
        hint="vendor=acme-industrial po=PO-7741 invoice=ACME-3 lines=STL-0250:2,FAS-M12:3",
    )
    assert record.match_verdict == "matched"
    assert record.state == "posted"
    # The match is *retained*, not recomputed: there is a MatchSet object and
    # one link per pairing.
    match = world.graph.get(record.match_ref.split("/")[1].split("@")[0])
    assert match.props["verdict"] == "matched"
    assert match.props["matched_lines"] == len(record.lines)
    pairings = world.graph.neighbors(match.id, "matches_invoice_line", "out")
    assert len(pairings) == len(record.lines)
    assert all(link.props["verdict"] in ("matched", "allowed-no-po")
               for link, _ in pairings)


def test_a_price_variance_holds_the_invoice_and_a_correction_releases_it(world):
    _vendors(world)
    _orders(world)
    _receipts(world)
    record = world.payables.submit(
        "ap@customer.example", filename="acme.pdf",
        hint="vendor=acme-industrial po=PO-7741 invoice=ACME-4 "
             "lines=STL-0250:2,FAS-M12:3 pricebump",
    )
    assert record.match_verdict == "variance"
    assert record.state == "pending"
    assert "off the PO's" in record.verdict_reason

    decided = world.tasks.decide(
        record.task_id, "corrected", "dana@customer.example",
        note="Vendor billed the old list price.",
        payload={"lines": [{"ordinal": 1, "unit_price_usd": 486.00}]},
    )
    resumed = world.payables.resume_for_task(decided)
    # The correction re-runs the match rather than overriding it.
    assert resumed.match_verdict == "matched"
    assert resumed.state == "posted"
    run = world.engine.get(record.run_id)
    assert "c1_1" in {s.step_id for s in run.steps}


def test_billing_more_than_was_received_is_a_quantity_variance(world):
    _vendors(world)
    _orders(world)
    _receipts(world, short=1.0)
    record = world.payables.submit(
        "ap@customer.example", filename="acme.pdf",
        hint="vendor=acme-industrial po=PO-7741 invoice=ACME-5 lines=STL-0250:2,FAS-M12:3",
    )
    assert record.match_verdict == "variance"
    assert any(
        p["verdict"] == "quantity-variance" for p in record.match_pairings
    )


def test_an_invoice_with_no_purchase_order_is_unmatched_not_broken(world):
    _vendors(world)
    record = world.payables.submit(
        "ap@customer.example", filename="acme.pdf",
        hint="vendor=acme-industrial nopo invoice=ACME-6 lines=PPE-GLV:1 total=80.00",
    )
    assert record.match_verdict == "unmatched"
    assert record.state == "pending"
    assert "no purchase order" in record.verdict_reason


def test_freight_needs_no_po_line_within_the_allowance():
    tolerances = MatchTolerances(freight_allowance_usd=150.0)
    result = match_invoice(
        [
            MatchLine(ordinal=1, sku="STL-0250", description="steel plate",
                      quantity=2, unit_price_usd=100.0, amount_usd=200.0),
            MatchLine(ordinal=2, sku="FRT-STD", description="Freight and handling",
                      quantity=1, unit_price_usd=60.0, amount_usd=60.0,
                      kind="freight"),
        ],
        [MatchLine(ordinal=1, parent_number="PO-1", sku="STL-0250",
                   description="steel plate", quantity=2, unit_price_usd=100.0,
                   amount_usd=200.0)],
        [MatchLine(ordinal=1, parent_number="RR-1", sku="STL-0250",
                   description="steel plate", quantity=2)],
        tolerances,
    )
    assert result.verdict == "matched"
    assert result.pairings[1].verdict == "allowed-no-po"


def test_goods_not_booked_in_block_rather_than_vary():
    tolerances = MatchTolerances(receiving_required_above_usd=500.0)
    result = match_invoice(
        [MatchLine(ordinal=1, sku="STL-0250", description="steel plate",
                   quantity=2, unit_price_usd=486.0, amount_usd=972.0)],
        [MatchLine(ordinal=1, parent_number="PO-1", sku="STL-0250",
                   description="steel plate", quantity=2, unit_price_usd=486.0,
                   amount_usd=972.0)],
        [],  # nothing received yet
        tolerances,
    )
    # A sequencing state, and a different verdict from a variance.
    assert result.verdict == "blocked"
    assert result.pairings[0].verdict == "no-receipt"


# ---- the anomaly screen ---------------------------------------------------


def test_the_screen_says_so_when_there_is_no_history(world):
    _vendors(world)
    record = world.payables.submit(
        "ap@customer.example", filename="acme.pdf",
        hint="vendor=acme-industrial nopo invoice=ACME-7 lines=PPE-GLV:1 total=80.00",
    )
    assert record.anomaly_findings == []
    assert "no history" in record.anomaly_basis


def test_an_outlier_is_measured_against_this_vendor(world):
    _vendors(world)
    _orders(world)
    _receipts(world)
    for number in ("ACME-A", "ACME-B", "ACME-C"):
        world.payables.submit(
            "ap@customer.example", filename="acme.pdf",
            hint=f"vendor=acme-industrial po=PO-7741 invoice={number} "
                 "lines=STL-0250:2,FAS-M12:3",
        )
    world.anomaly.rebuild()
    profile = world.anomaly.profile_for(object_id("Vendor", "Acme Industrial Inc."))
    assert profile.invoices == 3 and profile.median_usd > 0

    big = world.payables.submit(
        "ap@customer.example", filename="acme.pdf",
        hint="vendor=acme-industrial po=PO-7741 invoice=ACME-BIG "
             "lines=STL-0375:12 total=9800.00",
    )
    kinds = {f["kind"] for f in big.anomaly_findings}
    assert "amount-outlier" in kinds
    assert str(profile.median_usd)[:4] in big.anomaly_basis.replace(",", "")


def test_a_duplicate_total_inside_the_window_is_flagged(world):
    _vendors(world)
    _orders(world)
    _receipts(world)
    first = world.payables.submit(
        "ap@customer.example", filename="acme.pdf",
        hint="vendor=acme-industrial po=PO-7741 invoice=ACME-D1 "
             "lines=STL-0250:2,FAS-M12:3",
    )
    world.anomaly.rebuild()
    second = world.payables.submit(
        "ap@customer.example", filename="acme.pdf",
        hint=f"vendor=acme-industrial po=PO-7741 invoice=ACME-D2 "
             f"lines=STL-0250:2,FAS-M12:3 total={first.total_usd:.2f}",
    )
    kinds = {f["kind"] for f in second.anomaly_findings}
    assert "duplicate-suspect" in kinds
    assert second.state == "pending"


def test_profiles_are_derived_and_rebuildable(world):
    _vendors(world)
    _orders(world)
    _receipts(world)
    world.payables.submit(
        "ap@customer.example", filename="acme.pdf",
        hint="vendor=acme-industrial po=PO-7741 invoice=ACME-E lines=STL-0250:2,FAS-M12:3",
    )
    world.anomaly.rebuild()
    before = world.anomaly.profile_for(object_id("Vendor", "Acme Industrial Inc."))
    world.anomaly.store._items.clear()  # nothing here is a source of truth
    assert world.anomaly.profile_for(before.vendor_id) is None
    world.anomaly.rebuild()
    after = world.anomaly.profile_for(before.vendor_id)
    assert after.invoices == before.invoices
    assert after.median_usd == before.median_usd


# ---- seeding and the API -------------------------------------------------


def test_seed_builds_a_matchable_world(world):
    seeded = seed_payables_if_empty(world.payables, sync=world.sync)
    assert seeded > 0
    assert seed_payables_if_empty(world.payables, sync=world.sync) == 0

    rows = world.payables.list()
    assert len(rows) == seeded
    assert all(row.run_id for row in rows)
    stats = world.payables.stats()
    assert stats.one_shot and stats.ensemble
    assert stats.matched  # the PO and receiving feeds landed, so matches happen
    assert stats.anomalies  # and the screen found something
    # Vendors, orders and receipts all arrived through their sync Flows.
    assert len(world.graph.find("Vendor")) >= 4
    assert world.graph.find("PurchaseOrder") and world.graph.find("ReceivingReport")
    sources = {s.source for s in world.sync.sources()}
    assert {"erp-vendors", "erp-purchasing", "wms-receiving"} <= sources


def test_a_redelivery_changes_nothing(world):
    _vendors(world)
    before = world.graph.stats()
    _vendors(world)
    after = world.graph.stats()
    assert after["objects"] == before["objects"]
    vendor_runs = world.engine.list(flow_id="vendor-sync")
    assert vendor_runs[0].outputs["unchanged"] == 2


def test_api_exposes_the_table_the_match_and_the_vendors(client, world):
    seed_payables_if_empty(world.payables, sync=world.sync)
    rows = client.get("/api/v1/payables").json()
    assert rows and any(r["match_pairings"] for r in rows)
    stats = client.get("/api/v1/payables/stats").json()
    assert stats["total"] == len(rows)

    vendors = client.get("/api/v1/graph/vendors").json()
    acme = next(v for v in vendors if "Acme" in v["label"])
    assert len(acme["source_records"]) >= 2  # ERP and procurement
    assert acme["has_exemplar"]
    assert acme["invoices"] >= 1

    sources = client.get("/api/v1/sources").json()
    assert {s["kind"] for s in sources} >= {
        "vendors", "purchase-orders", "receiving-reports"
    }
