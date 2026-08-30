"""The accounts-payable Flow, end to end.

The doctrinal assertions:

- a vendor is an *entity*: one ModelCall reads the name printed on the page,
  the resolver decides identity against the vendor master, and two systems'
  ids for one company resolve to one Vendor;
- extraction is a single pass, and a vendor seen a second time resolves to the
  object the first invoice minted rather than to a duplicate;
- the three-way match is retained as a MatchSet, and "no receiving report yet"
  is a state rather than a failure;
- the anomaly screen is scored against the vendor's own history with governed
  thresholds;
- the posting rule's values come from the governed document;
- a clerk's correction re-runs the match rather than overriding it;
- the hint grammar is one model, and every hint the repository ships
  round-trips through it verbatim.
"""

import pytest
from pydantic import ValidationError

from app.graph import object_id
from app.matching import MatchLine, MatchTolerances, match_invoice
from app.models import AssetUpdate
from app.payables import InvoiceScenario, ScenarioLine
from app.sync import SyncRequest
from tests import demo_world
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
        "source": "erp-purchase-orders",
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



# ---- the case-authoring language ------------------------------------------
#
# The hint string is not debug scaffolding. It is what the seeded world is
# written in, what the eval cases state their triggers in, and what a person
# composing an invoice in the console sends. One grammar, three consumers.


def _shipped_hints() -> list[str]:
    """Every hint string this repository ships, from where it actually lives.

    Read out of the seeds and the eval specs rather than copied here, so a
    hint added to either is covered the moment it is added — a fixture list
    that has to be kept in step is the thing this test exists to prevent.
    """
    from app.eval_specs import AP_CASES

    return (
        [case.hint for case in demo_world.seed_invoices()]
        + [c.trigger["hint"] for c in AP_CASES if c.trigger.get("hint")]
    )


@pytest.mark.parametrize("hint", _shipped_hints())
def test_every_shipped_hint_round_trips_verbatim(hint):
    """The load-bearing property.

    Serialization drifting by one character does not fail loudly: the seed
    builds a *different* world and the eval suite goes red looking like a Flow
    regression. So the string is asserted byte-for-byte, both ways.
    """
    scenario = InvoiceScenario.parse(hint)
    assert scenario.to_hint() == hint
    assert InvoiceScenario.parse(scenario.to_hint()) == scenario


def test_the_grammar_distinguishes_what_an_empty_string_cannot():
    """`nopo` is not an absent `po=`, and a named new vendor is not a bare one.

    Both pairs serialize to different strings and reach different branches, so
    collapsing either into one empty field would silently retarget a case.
    """
    no_order = InvoiceScenario.parse("vendor=acme-industrial nopo")
    unstated = InvoiceScenario.parse("vendor=acme-industrial")
    assert no_order.no_po and not unstated.no_po
    assert no_order.to_hint() != unstated.to_hint()

    named = InvoiceScenario.parse("newvendor=meridian-supply")
    bare = InvoiceScenario.parse("newvendor po=PO-9001")
    assert named.new_vendor == "meridian-supply" and not named.unnamed_new_vendor
    assert bare.unnamed_new_vendor and not bare.new_vendor
    assert bare.to_hint() == "newvendor po=PO-9001"


def test_lines_carry_the_quantity_the_composer_stepped_to():
    scenario = InvoiceScenario.parse("lines=STL-0250:2,FAS-M12:3")
    assert [(line.sku, line.quantity) for line in scenario.lines] == [
        ("STL-0250", 2.0), ("FAS-M12", 3.0)
    ]
    scenario.lines[1].quantity = 5
    assert scenario.to_hint() == "lines=STL-0250:2,FAS-M12:5"


def test_the_grammar_refuses_what_it_cannot_spell():
    """Closure, not fussiness.

    `to_hint` and `parse` are one round trip, so a field holding a character
    the parser's charset cannot read back would let the console preview one
    invoice number and the run receive a different one. Refusing is the only
    answer that keeps "what this sends" true.
    """
    with pytest.raises(ValidationError):
        InvoiceScenario(invoice_number="ACME INV 1")
    with pytest.raises(ValidationError):
        InvoiceScenario(po_number="7741")
    # Case and surrounding space are normalized rather than refused: a person
    # typing an invoice number is not making a different case.
    assert InvoiceScenario(invoice_number=" acme-inv-1 ").to_hint() == (
        "invoice=ACME-INV-1"
    )


TWIST_BRANCHES = {
    "price_variance": "price variance",
    "over_receipt": "quantity variance",
    "low_confidence": "low extraction confidence",
    "tax_id": "tax-id resolution",
}


@pytest.mark.parametrize("twist", sorted(TWIST_BRANCHES))
def test_each_twist_reaches_the_branch_it_advertises(world, twist):
    """A checkbox that names a branch has to reach it.

    The composer labels each twist with the branch it exercises and the
    governed clause that decides it. If a twist stopped reaching its branch the
    label would still read correctly and the screen would be teaching the
    wrong thing, which is the failure this catches.
    """
    _vendors(world)
    _orders(world)
    _receipts(world)
    scenario = InvoiceScenario(
        vendor_slug="acme-industrial", po_number="PO-7741",
        invoice_number=f"ACME-{twist.upper().replace('_', '-')}",
        lines=[
            ScenarioLine(sku="STL-0250", quantity=2),
            ScenarioLine(sku="FAS-M12", quantity=3),
        ],
        **{twist: True},
    )
    record = world.payables.submit(
        "connie.alberts@naviant.com", filename="acme.pdf", hint=scenario.to_hint()
    )
    if twist == "price_variance":
        assert record.match_verdict == "variance"
        assert any(
            p["verdict"] == "price-variance" for p in record.match_pairings
        )
    elif twist == "over_receipt":
        assert record.match_verdict == "variance"
        assert any(
            p["verdict"] == "quantity-variance" for p in record.match_pairings
        )
    elif twist == "low_confidence":
        assert "confidence 0.55 is below" in record.verdict_reason
    else:
        # The twist prints a tax id, which is where the resolver's strongest
        # signal is read from. Whether it *settles* the vendor depends on the
        # master holding that id — the resolver's business, not the
        # extractor's, and exactly the division this flow exists to show.
        assert record.vendor_tax_id
        plain = world.payables.submit(
            "connie.alberts@naviant.com", filename="acme.pdf",
            hint=scenario.model_copy(update={"tax_id": False}).to_hint(),
        )
        assert not plain.vendor_tax_id


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
        "connie.alberts@naviant.com", filename="acme.pdf",
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
        task.id, "approved", "raymond.fitzgerald@naviant.com",
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
        "connie.alberts@naviant.com", filename="acme.pdf",
        hint="vendor=acme-industrial nopo invoice=ACME-9 lines=PPE-GLV:1 total=80.00",
    )
    assert record.vendor_resolution == "linked"
    # Ids are minted; the deterministic hash is an alias the graph resolves.
    assert record.vendor_id == world.graph.get(
        object_id("Vendor", "Acme Industrial Inc.")
    ).id
    assert record.vendor_method in ("normalized-name", "fuzzy-name", "tax-id")


def test_an_unknown_vendor_is_minted_onboarding(world):
    _vendors(world)
    record = world.payables.submit(
        "connie.alberts@naviant.com", filename="new.pdf",
        hint="newvendor=meridian-supply nopo invoice=MER-1 lines=PPE-GLV:1 total=80.00",
    )
    assert record.vendor_resolution == "new"
    vendor = world.graph.get(record.vendor_id)
    assert vendor.props["status"] == "onboarding"


# ---- one extraction pass, and the vendor's second invoice -----------------


def test_one_model_call_reads_the_document(world):
    """The whole extraction is one governed ModelCall — no separate classifier
    pass, no ensemble. The resolver, not a second model, settles the entity."""
    _vendors(world)
    _orders(world)
    _receipts(world)
    record = world.payables.submit(
        "connie.alberts@naviant.com", filename="acme.pdf",
        hint="vendor=acme-industrial po=PO-7741 invoice=ACME-2 lines=STL-0250:2,FAS-M12:3",
    )
    assert record.state == "posted"
    run = world.engine.get(record.run_id)
    step_ids = {s.step_id for s in run.steps}
    assert step_ids >= {"s1", "m1", "z1", "x3", "c1", "c2"}
    assert [s.step_id for s in run.steps if s.kind == "modelcall"] == ["m1"]


def test_the_extraction_prompt_is_an_asset_the_modelcall_names(world):
    """The one call is steered by governed knowledge, and says which revision.

    The AP flow already read its posting policy at a revision; the instruction
    that produced the extraction the policy is then applied to was a string
    literal. Now both are assets, and the ModelCall's read-set names the one it
    ran on — which is what makes a posting explainable end to end.
    """
    asset = world.knowledge.repo.get("invoice-extraction-prompt")
    assert asset is not None
    assert asset.kind == "prompt"

    prompt = world.payables.read_prompt()
    assert prompt.found_asset
    assert prompt.source_ref == "knowledge:invoice-extraction-prompt@rev1"
    # The instruction that keeps identity out of the model's hands is a value a
    # customer can read and revise, not a literal buried in the reader.
    assert "not normalized and not guessed at" in prompt.text

    _vendors(world)
    _orders(world)
    _receipts(world)
    record = world.payables.submit(
        "connie.alberts@naviant.com", filename="acme.pdf",
        hint="vendor=acme-industrial po=PO-7741 invoice=ACME-9 lines=STL-0250:2",
    )
    run = world.engine.get(record.run_id)
    [model_step] = [s for s in run.steps if s.kind == "modelcall"]
    assert "knowledge:invoice-extraction-prompt@rev1" in model_step.refs


def test_a_corpus_without_the_prompt_still_extracts():
    """A deployment that has not seeded yet still reads invoices.

    The built-in default carries the extraction and the unpinned ref says the
    run never got a revision — the same degradation as every other governed
    read here, because a missing asset is a corpus problem and not a reason to
    stop reading documents.
    """
    world = test_world.fresh(seed_corpus=False)
    prompt = world.payables.read_prompt()
    assert not prompt.found_asset
    assert prompt.source_ref == "knowledge:invoice-extraction-prompt"

    record = world.payables.submit(
        "connie.alberts@naviant.com", filename="mer.pdf",
        hint="newvendor po=PO-9001 invoice=MER-7 total=480.00",
    )
    run = world.engine.get(record.run_id)
    [model_step] = [s for s in run.steps if s.kind == "modelcall"]
    assert model_step.status == "done"
    assert "knowledge:invoice-extraction-prompt" in model_step.refs
    assert record.total_usd > 0


def test_an_unknown_vendor_is_minted_onboarding_and_graduates_on_posting(world):
    _vendors(world)
    record = world.payables.submit(
        "connie.alberts@naviant.com", filename="mer.pdf",
        hint="newvendor=meridian-supply nopo invoice=MER-1 lines=PPE-GLV:2 total=190.00",
    )
    # No PO on the document, so the posting rule holds it for a clerk.
    assert record.state == "pending"
    vendor = world.graph.get(record.vendor_id)
    assert vendor.props["status"] == "onboarding"
    task = world.tasks.get(record.task_id)
    assert task.kind == "decision" and task.queue == "ap-clerk"

    decided = world.tasks.decide(
        task.id, "approved", "dana.prentiss@naviant.com",
        note="First invoice from this vendor, no PO expected at this size.",
    )
    resumed = world.payables.resume_for_task(decided)
    assert resumed.state == "posted"
    # A human vouched for it by posting, so the vendor graduates.
    assert world.graph.get(record.vendor_id).props["status"] == "active"


def test_the_next_invoice_links_to_the_same_vendor(world):
    _vendors(world)
    first = world.payables.submit(
        "connie.alberts@naviant.com", filename="mer1.pdf",
        hint="newvendor=meridian-supply nopo invoice=MER-1 lines=PPE-GLV:2 total=190.00",
    )
    world.payables.resume_for_task(
        world.tasks.decide(first.task_id, "approved", "dana.prentiss@naviant.com")
    )

    second = world.payables.submit(
        "connie.alberts@naviant.com", filename="mer2.pdf",
        hint="newvendor=meridian-supply nopo invoice=MER-2 lines=PPE-GLV:1 total=112.00",
    )
    assert second.vendor_id == first.vendor_id
    assert second.vendor_resolution == "linked"
    meridian = [
        v for v in world.graph.find("Vendor") if "meridian" in v.label.lower()
    ]
    assert len(meridian) == 1


def test_a_rejected_review_denies_and_commits_nothing(world):
    _vendors(world)
    record = world.payables.submit(
        "connie.alberts@naviant.com", filename="mer.pdf",
        hint="newvendor=meridian-supply nopo invoice=MER-1 lines=PPE-GLV:2 total=190.00",
    )
    decided = world.tasks.decide(
        record.task_id, "rejected", "dana.prentiss@naviant.com",
        note="Not our invoice — wrong company entirely.",
    )
    resumed = world.payables.resume_for_task(decided)
    assert resumed.state == "denied"
    # The vendor never graduated: nobody vouched for it.
    assert world.graph.get(record.vendor_id).props["status"] == "onboarding"


# ---- the three-way match --------------------------------------------------


def test_a_clean_invoice_matches_its_po_and_receipt(world):
    _vendors(world)
    _orders(world)
    _receipts(world)
    record = world.payables.submit(
        "connie.alberts@naviant.com", filename="acme.pdf",
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
        "connie.alberts@naviant.com", filename="acme.pdf",
        hint="vendor=acme-industrial po=PO-7741 invoice=ACME-4 "
             "lines=STL-0250:2,FAS-M12:3 pricebump",
    )
    assert record.match_verdict == "variance"
    assert record.state == "pending"
    assert "off the PO's" in record.verdict_reason

    decided = world.tasks.decide(
        record.task_id, "corrected", "dana.prentiss@naviant.com",
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
        "connie.alberts@naviant.com", filename="acme.pdf",
        hint="vendor=acme-industrial po=PO-7741 invoice=ACME-5 lines=STL-0250:2,FAS-M12:3",
    )
    assert record.match_verdict == "variance"
    assert any(
        p["verdict"] == "quantity-variance" for p in record.match_pairings
    )


def test_an_invoice_with_no_purchase_order_is_unmatched_not_broken(world):
    _vendors(world)
    record = world.payables.submit(
        "connie.alberts@naviant.com", filename="acme.pdf",
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
        "connie.alberts@naviant.com", filename="acme.pdf",
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
            "connie.alberts@naviant.com", filename="acme.pdf",
            hint=f"vendor=acme-industrial po=PO-7741 invoice={number} "
                 "lines=STL-0250:2,FAS-M12:3",
        )
    world.anomaly.rebuild()
    profile = world.anomaly.profile_for(
        world.graph.get(object_id("Vendor", "Acme Industrial Inc.")).id
    )
    assert profile.invoices == 3 and profile.median_usd > 0

    big = world.payables.submit(
        "connie.alberts@naviant.com", filename="acme.pdf",
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
        "connie.alberts@naviant.com", filename="acme.pdf",
        hint="vendor=acme-industrial po=PO-7741 invoice=ACME-D1 "
             "lines=STL-0250:2,FAS-M12:3",
    )
    world.anomaly.rebuild()
    second = world.payables.submit(
        "connie.alberts@naviant.com", filename="acme.pdf",
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
        "connie.alberts@naviant.com", filename="acme.pdf",
        hint="vendor=acme-industrial po=PO-7741 invoice=ACME-E lines=STL-0250:2,FAS-M12:3",
    )
    world.anomaly.rebuild()
    before = world.anomaly.profile_for(
        world.graph.get(object_id("Vendor", "Acme Industrial Inc.")).id
    )
    world.anomaly.store._items.clear()  # nothing here is a source of truth
    assert world.anomaly.profile_for(before.vendor_id) is None
    world.anomaly.rebuild()
    after = world.anomaly.profile_for(before.vendor_id)
    assert after.invoices == before.invoices
    assert after.median_usd == before.median_usd


# ---- seeding and the API -------------------------------------------------


def test_seed_builds_a_matchable_world(world):
    seeded = demo_world.seed_payables(world.payables, sync=world.sync)
    assert seeded > 0
    assert demo_world.seed_payables(world.payables, sync=world.sync) == 0

    rows = world.payables.list()
    assert len(rows) == seeded
    assert all(row.run_id for row in rows)
    stats = world.payables.stats()
    assert stats.posted and stats.pending and stats.denied
    assert stats.matched  # the PO and receiving feeds landed, so matches happen
    assert stats.anomalies  # and the screen found something
    # Vendors, orders and receipts all arrived through their sync Flows.
    assert len(world.graph.find("Vendor")) >= 4
    assert world.graph.find("PurchaseOrder") and world.graph.find("ReceivingReport")
    sources = {s.source for s in world.sync.sources()}
    assert {"erp-vendors", "erp-purchase-orders", "wms-receiving"} <= sources


def test_a_redelivery_changes_nothing(world):
    _vendors(world)
    before = world.graph.stats()
    _vendors(world)
    after = world.graph.stats()
    assert after["objects"] == before["objects"]
    vendor_runs = world.engine.list(flow_id="vendor-sync")
    assert vendor_runs[0].outputs["unchanged"] == 2


def test_api_exposes_the_table_the_match_and_the_vendors(client, world):
    demo_world.seed_payables(world.payables, sync=world.sync)
    rows = client.get("/api/v1/payables").json()
    assert rows and any(r["match_pairings"] for r in rows)
    stats = client.get("/api/v1/payables/stats").json()
    assert stats["total"] == len(rows)

    vendors = client.get("/api/v1/graph/vendors").json()
    acme = next(v for v in vendors if "Acme" in v["label"])
    assert len(acme["source_records"]) >= 2  # ERP and procurement
    assert acme["invoices"] >= 1

    sources = client.get("/api/v1/sources").json()
    assert {s["kind"] for s in sources} >= {
        "vendors", "purchase-orders", "receiving-reports"
    }


# ---- who the AP table belongs to ------------------------------------------
#
# Kim is the whole point of this block. She owns no queue and has never entered
# an invoice, and until this rule landed her `/payables/stats` came back
# byte-identical to the engineer's — every vendor, every amount, every posting
# decision in the tenant, handed to a data engineer because she was signed in.
#
# The rule is one sentence: your own rows, plus the rows of any run whose Task
# sits in a queue you own. It reads off `InvoiceRecord.task_id` and `Task.queue`
# — both already stored — so an `ap-clerk` binding is what opens the table
# rather than a second notion of authority.

#: Rows each persona reaches over the seeded AP world, and why.
AP_VISIBLE = {
    # Platform scope: the deployment is theirs to diagnose.
    "naviant-engineer": 7,
    "naviant-support": 7,
    # Tenant scope: the customer's own map, whole, by design.
    "customer-admin": 7,
    # Bound to `ap-clerk`, which is the queue this flow routes to — so the
    # whole workspace is theirs, including `run_ap_seed01`, which auto-posted
    # and raised nothing. A clerk shown only the invoices that went wrong has
    # no baseline to judge the exceptions against.
    "customer-user:ap": 7,
    "customer-user:controller": 7,
    # Owns no queue and entered no invoice.
    "customer-user:ae": 0,
    # Approves expenses, which says nothing about invoices.
    "customer-user:ceo": 0,
}


@pytest.mark.parametrize("persona,expected", sorted(AP_VISIBLE.items()))
def test_the_ap_table_shows_the_work_you_are_accountable_for(
    client, world, persona, expected
):
    demo_world.seed_payables(world.payables, sync=world.sync)
    rows = client.get(
        "/api/v1/payables", headers={"X-Cumulus-Persona": persona}
    ).json()
    assert len(rows) == expected


@pytest.mark.parametrize("persona,expected", sorted(AP_VISIBLE.items()))
def test_the_ap_strip_counts_the_table_under_it(client, world, persona, expected):
    """A strip computed over a different slice than its table is a screen
    arguing with itself — the bug already caught once on the task badge, where
    the per-queue breakdown was filtered and the headline was not."""
    demo_world.seed_payables(world.payables, sync=world.sync)
    headers = {"X-Cumulus-Persona": persona}
    rows = client.get("/api/v1/payables", headers=headers).json()
    stats = client.get("/api/v1/payables/stats", headers=headers).json()
    assert stats["total"] == len(rows) == expected
    # Every band, not just the headline: a filtered total over an unfiltered
    # breakdown is the same bug wearing the other hat.
    assert stats["posted"] + stats["pending"] + stats["denied"] == len(rows)


def test_a_total_is_a_read_of_every_row_it_counts(client, world):
    """The money, not just the count. An org-wide figure handed to somebody who
    may see none of the rows leaks all of them in aggregate."""
    demo_world.seed_payables(world.payables, sync=world.sync)
    everyone = client.get("/api/v1/payables/stats").json()
    kim = client.get(
        "/api/v1/payables/stats", headers={"X-Cumulus-Persona": "customer-user:ae"}
    ).json()
    tomas = client.get(
        "/api/v1/payables/stats", headers={"X-Cumulus-Persona": "customer-user:ap"}
    ).json()
    assert everyone["posted_usd"] > 0 and everyone["anomalies"] > 0
    assert kim["posted_usd"] == 0.0 and kim["anomalies"] == 0
    # Tomas owns the queue this flow routes to, so the workspace is his and
    # his totals are the tenant's — that is what a Seeing Space is. The leak
    # this guards is Kim's, who owns no queue and entered no invoice.
    assert tomas["posted_usd"] == everyone["posted_usd"]
    assert tomas["anomalies"] == everyone["anomalies"]


def test_an_invoice_outside_your_slice_is_missing_not_forbidden(client, world):
    """404 rather than 403: a 403 confirms the id exists, which is the one bit
    an unauthorized caller should not get back."""
    demo_world.seed_payables(world.payables, sync=world.sync)
    record_id = "run_ap_seed03"  # raised an ap-clerk task, still pending
    assert client.get(
        f"/api/v1/payables/{record_id}",
        headers={"X-Cumulus-Persona": "customer-user:ap"},
    ).status_code == 200
    refused = client.get(
        f"/api/v1/payables/{record_id}",
        headers={"X-Cumulus-Persona": "customer-user:ae"},
    )
    assert refused.status_code == 404


# ---- the console's own door -----------------------------------------------
#
# `/api/ui/v1/payables` replaces a free-text hint box with a composer built out
# of the graph, and answers a submission with a screen rather than a sentence.
# Three things are worth defending: the composer's controls are real rows, the
# outcome names the clause the Rule cited without re-deriving the verdict, and
# `may_decide` answers `require_queue` before the 403 rather than after it.

UI_PAYABLES = "/api/ui/v1/payables"
UI_SUBMISSIONS = "/api/ui/v1/payables/submissions"


def _composed(**overrides) -> dict:
    scenario = {
        "vendor_slug": "acme-industrial",
        "po_number": "PO-7741",
        "invoice_number": "ACME-INV-9001",
        "lines": [{"sku": "STL-0250", "quantity": 2}, {"sku": "FAS-M12", "quantity": 3}],
    }
    scenario.update(overrides)
    return {"scenario": scenario}


def test_every_composer_control_is_a_row_from_the_graph(client, world):
    """The picker is the vendor master, its orders and their lines.

    Not a list of example strings: choosing Acme and then PO-7741 is what makes
    `lines=STL-0250:2,FAS-M12:3` appear, and nobody composing an invoice has to
    know that spelling exists.
    """
    demo_world.seed_payables(world.payables, sync=world.sync)
    composer = client.get(UI_PAYABLES).json()["composer"]

    acme = next(v for v in composer["vendors"] if "Acme" in v["label"])
    assert acme["status"] == "active"
    # The vendor master's whole point, at the moment you pick one.
    assert {s["source"] for s in acme["sources"]} >= {"erp-vendors", "procurement-coupa"}

    [order] = acme["orders"]
    assert order["po_number"] == "PO-7741" and order["receipts"] == ["RR-5501"]
    # In the order's own ordinal order: a twist that says "the first line"
    # has to mean line 1 of the purchase order, not whichever SKU sorts first.
    assert [(line["sku"], line["quantity_ordered"]) for line in order["lines"]] == [
        ("STL-0250", 2.0), ("FAS-M12", 3.0)
    ]
    assert [line["quantity_received"] for line in order["lines"]] == [2.0, 3.0]
    # The catalog is the line source for an invoice with no order behind it.
    assert {line["sku"] for line in composer["catalog"]} >= {"PPE-GLV", "STL-0375"}

    # Every twist names a clause the flow actually reads.
    clause_ids = {clause["id"] for clause in composer["policy"]}
    assert {t["clause_id"] for t in composer["twists"]} <= clause_ids
    limit = next(c for c in composer["policy"] if c["id"] == "auto-post-limit")
    assert limit["value"] == "$2,500.00"
    assert limit["ref"] == "knowledge:invoice-posting-policy@rev1"


def test_a_composed_submission_sends_the_string_it_showed(client, world):
    demo_world.seed_payables(world.payables, sync=world.sync)
    outcome = client.post(UI_SUBMISSIONS, json=_composed()).json()
    assert outcome["hint"] == (
        "vendor=acme-industrial po=PO-7741 invoice=ACME-INV-9001 "
        "lines=STL-0250:2,FAS-M12:3"
    )
    assert outcome["invoice"]["state"] == "posted"
    assert outcome["invoice"]["match_verdict"] == "matched"
    assert outcome["gate"] is None


def test_the_outcome_marks_the_clause_the_rule_cited(client, world):
    """Juxtaposition, not prediction.

    The screen shows $9,800.00 beside "auto-post ≤ $2,500.00" and marks the
    clause the Rule's own reason names. Marking is attribution of a sentence
    `_posting_check` already wrote; deciding here would be a second copy of the
    rule, and the two would eventually disagree.
    """
    demo_world.seed_payables(world.payables, sync=world.sync)
    outcome = client.post(UI_SUBMISSIONS, json=_composed(
        invoice_number="ACME-INV-9002",
        lines=[{"sku": "STL-0375", "quantity": 12}],
        total_usd=9800.0,
    )).json()
    assert outcome["invoice"]["state"] == "pending"
    deciding = {c["id"] for c in outcome["policy"] if c["deciding"]}
    assert "auto-post-limit" in deciding
    # A clause nothing cited stays unmarked, so the marks mean something.
    assert "freight-allowance" not in deciding


def test_may_decide_answers_require_queue_before_the_403(client, world):
    """Tomas is bound to `ap-clerk`; Kim is bound to nothing.

    The gate is the same gate. What differs is who may release it, and that is
    a queue binding rather than a rung on the write ladder — so the screen has
    to say which queue, not merely "not allowed".
    """
    demo_world.seed_payables(world.payables, sync=world.sync)
    body = _composed(invoice_number="ACME-INV-9003", total_usd=9800.0)

    tomas = client.post(
        UI_SUBMISSIONS, json=body, headers={"X-Cumulus-Persona": "customer-user:ap"}
    ).json()["gate"]
    assert tomas["queue"] == "ap-clerk"
    assert tomas["may_decide"] and tomas["why_not"] == ""
    # Which step stopped it, not merely that something did.
    assert tomas["step_id"].startswith("k2") and tomas["step_name"] == "review-invoice"

    body["scenario"]["invoice_number"] = "ACME-INV-9004"
    kim = client.post(
        UI_SUBMISSIONS, json=body, headers={"X-Cumulus-Persona": "customer-user:ae"}
    ).json()["gate"]
    assert not kim["may_decide"]
    assert kim["why_not"] == "this is in ap-clerk; you are bound to no queue"


def test_an_upload_carries_no_hints_and_a_key_gets_no_door(client, world):
    """The two honest halves.

    An upload is a document and says nothing about the case — with no model
    configured the extractor reads the filename, which the screen admits rather
    than dressing up. And the console door refuses a minted key outright: a
    machine has no screens.
    """
    demo_world.seed_payables(world.payables, sync=world.sync)
    outcome = client.post(UI_SUBMISSIONS, json={"filename": "a-scan.pdf"}).json()
    assert outcome["hint"] == "" and outcome["invoice"]["id"]
    assert client.post(UI_SUBMISSIONS, json={}).status_code == 400
    assert client.get(UI_PAYABLES, headers={"X-API-Key": "k"}).status_code == 401


def test_the_screen_and_its_strip_count_the_same_slice(client, world):
    """One route, so the strip cannot be computed over a different read than
    the table under it — the failure the two-call version made possible."""
    demo_world.seed_payables(world.payables, sync=world.sync)
    for persona, expected in sorted(AP_VISIBLE.items()):
        screen = client.get(
            UI_PAYABLES, headers={"X-Cumulus-Persona": persona}
        ).json()
        assert len(screen["invoices"]) == screen["stats"]["total"] == expected
