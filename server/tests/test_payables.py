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

import base64

import pytest
from pydantic import ValidationError

from app.graph import object_id
from app.matching import MatchLine, MatchTolerances, match_invoice
from app.models import AssetUpdate
from app.tasks.accounts_payable import domain as payables
from app.tasks.accounts_payable.scenarios import InvoiceScenario, ScenarioLine
from app.connectors.sync import SyncRequest
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
        "source": "erp-receiving",
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



def _posted(world, hint: str, filename: str = "acme.pdf", **kwargs):
    """Submit an invoice and take it through the review the policy now requires.

    The governed auto-post limit is **$0.00** — every invoice raises a review
    task, which is the whole of decision D6 — so a test that needs a *posted*
    invoice has to answer that task, exactly as the desk does. Approving is
    the honest gesture here: these cases are clean invoices whose only
    objection is the limit itself, which is what the clerk is being asked
    about.

    It fails loudly rather than approving twice. A case that comes back to the
    gate a second time is a case with a real objection in it, and quietly
    clicking through that would turn every assertion below into a test of the
    approve button.
    """
    record = world.payables.submit(
        kwargs.pop("submitted_by", "connie.alberts@naviant.com"),
        filename=filename, hint=hint, **kwargs,
    )
    if record.state != "pending" or not record.task_id:
        return record
    decided = world.tasks.decide(
        record.task_id, "approved", "dana.prentiss@naviant.com",
        note="Nothing to check but the governed limit.",
    )
    return world.payables.resume_for_task(decided) or record


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


def test_lines_carry_the_quantity_a_case_asked_for():
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
    """A twist that names a branch has to reach it.

    Each `ScenarioTwist` declares the branch it exercises and the governed
    clause that decides it, and the seeded world and the eval cases are written
    against those declarations. If a twist stopped reaching its branch the
    declaration would still read correctly and every case built on it would be
    grading the wrong path, which is the failure this catches.
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
    # $0.00 as shipped — decision D6, taken as a knowledge edit rather than a
    # code change, which is the point of the value being governed at all.
    assert policy.found_asset and policy.limit_usd == 0.0
    assert policy.min_confidence == 0.75
    assert policy.source_ref.endswith("@rev1")


def test_editing_the_posting_limit_changes_the_flow(world):
    """The dial, in both directions — including the one D6 turned.

    As shipped the limit is $0.00, so a clean invoice from a resolved vendor
    that matches its order line for line still stops at a clerk. Raising the
    limit is a knowledge edit and nothing else, and the very next run posts
    with nobody involved: the auto-post path is dormant, not deleted, which is
    the difference between a policy decision and a code change.
    """
    _vendors(world)
    _orders(world)
    _receipts(world)
    held = world.payables.submit(
        "connie.alberts@naviant.com", filename="acme.pdf",
        hint="vendor=acme-industrial po=PO-7741 invoice=ACME-1 lines=STL-0250:2,FAS-M12:3",
    )
    assert held.state == "pending"
    assert "$0.00 auto-post limit" in held.verdict_reason
    assert held.policy_ref.endswith("@rev1")

    content = world.knowledge.repo.get("invoice-posting-policy").content
    world.knowledge.update("invoice-posting-policy", AssetUpdate(
        content=content.replace("Auto-post limit: $0.00", "Auto-post limit: $5,000.00")
    ))
    posted = world.payables.submit(
        "connie.alberts@naviant.com", filename="acme.pdf",
        hint="vendor=acme-industrial po=PO-7741 invoice=ACME-1B lines=STL-0250:2,FAS-M12:3",
    )
    assert posted.state == "posted"
    assert posted.decided_by == ""  # nobody was asked
    assert posted.policy_ref.endswith("@rev2")


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
    record = _posted(
        world,
        "vendor=acme-industrial po=PO-7741 invoice=ACME-2 lines=STL-0250:2,FAS-M12:3",
    )
    assert record.state == "posted"
    run = world.engine.get(record.run_id)
    step_ids = {s.step_id for s in run.steps}
    assert step_ids >= {"s1", "x0", "m1", "z1", "x2", "c2"}
    # One call, and the second one is *absent* rather than empty: a clean
    # invoice puts nothing in doubt, so `m2` is never asked for boxes.
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
    # v9 routes the review to the person who submitted it, per the governed
    # approval table — their own desk, not the shared AP queue.
    assert task.kind == "decision"
    assert task.subject_email == "connie.alberts@naviant.com"
    assert task.queue == world.queues.ensure_personal(
        "connie.alberts@naviant.com"
    ).slug

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
    record = _posted(
        world,
        "vendor=acme-industrial po=PO-7741 invoice=ACME-3 lines=STL-0250:2,FAS-M12:3",
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
    run = world.engine.get(record.run_id)
    assert "c2_1" in {s.step_id for s in run.steps}

    # And it comes back to the desk once more, held now by the governed limit
    # alone — the variance the clerk answered is gone from the reason. That
    # second gate is D6's doing and is worth pinning: under a $0.00 limit a
    # correction can never *release* an invoice, it can only settle the
    # objection that is not the limit.
    assert resumed.state == "pending"
    assert "off the PO's" not in resumed.verdict_reason
    assert "$0.00 auto-post limit" in resumed.verdict_reason

    approved = world.payables.resume_for_task(world.tasks.decide(
        resumed.task_id, "approved", "dana.prentiss@naviant.com",
        note="Corrected price agrees with the order.",
    ))
    assert approved.state == "posted"


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
#
# A v8 capability, and these run against v8 for that reason. v9 cut the screen
# from the default path deliberately — scoring every invoice against its
# vendor's history before anybody sees it buys a finding on the handful of
# invoices that were going to a person anyway — and the capability is not gone:
# it is a version the console can make live, which is what a shipped candidate
# is for. So the tests that exercise it say which version they exercise.


@pytest.fixture()
def screened(world):
    """`world`, with the version that screens made live."""
    world.engine.live_version = lambda flow_id: (
        8 if flow_id == payables.FLOW_ID else 0
    )
    return world


def test_the_screen_says_so_when_there_is_no_history(screened):
    world = screened
    _vendors(world)
    record = world.payables.submit(
        "connie.alberts@naviant.com", filename="acme.pdf",
        hint="vendor=acme-industrial nopo invoice=ACME-7 lines=PPE-GLV:1 total=80.00",
    )
    assert record.anomaly_findings == []
    assert "no history" in record.anomaly_basis


def test_an_outlier_is_measured_against_this_vendor(screened):
    world = screened
    _vendors(world)
    _orders(world)
    _receipts(world)
    # Posted, not merely submitted: a vendor's payment history is what was
    # *paid*, and under the $0.00 limit that means three invoices a clerk
    # approved. An invoice still sitting at a gate is not history.
    for number in ("ACME-A", "ACME-B", "ACME-C"):
        _posted(
            world,
            f"vendor=acme-industrial po=PO-7741 invoice={number} "
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


def test_a_duplicate_total_inside_the_window_is_flagged(screened):
    world = screened
    _vendors(world)
    _orders(world)
    _receipts(world)
    first = _posted(
        world,
        "vendor=acme-industrial po=PO-7741 invoice=ACME-D1 "
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
    # Every seeded invoice went through a person, because the governed limit is
    # $0.00 — the seeded world's shape is now the *decisions*, not a screen's
    # findings: one corrected, one rejected as a duplicate by the reviewer who
    # recognised it, two still open.
    assert stats.match_variance
    # Vendors, orders and receipts all arrived through their sync Flows.
    assert len(world.graph.find("Vendor")) >= 4
    assert world.graph.find("PurchaseOrder") and world.graph.find("ReceivingReport")
    sources = {s.source for s in world.sync.sources()}
    assert {"erp-vendors", "erp-purchase-orders", "erp-receiving"} <= sources


def test_a_redelivery_changes_nothing(world):
    _vendors(world)
    before = world.graph.stats()
    _vendors(world)
    after = world.graph.stats()
    assert after["objects"] == before["objects"]
    vendor_runs = world.engine.list(flow_id="vendor-sync")
    assert vendor_runs[0].outputs["unchanged"] == 2


def test_api_exposes_the_table_and_the_match(client, world):
    demo_world.seed_payables(world.payables, sync=world.sync)
    rows = client.get("/api/v1/payables").json()
    assert rows and any(r["match_pairings"] for r in rows)
    stats = client.get("/api/v1/payables/stats").json()
    assert stats["total"] == len(rows)

    # The canonical vendor carries both source systems' rows and the invoice
    # it issued. Read off the graph rather than through `/graph/vendors`: that
    # view route went with the composer screen that was its only caller, and
    # the assembly it used to prove is the graph's, not the route's.
    acme = next(v for v in world.graph.find("Vendor") if "Acme" in v.label)
    identified = world.graph.neighbors(
        acme.id, "identified_as", "out", include_reversed=True
    )
    assert len(identified) >= 2  # ERP and procurement
    assert world.graph.neighbors(acme.id, "issued", "out")

    # The console door: the bare machine list was retired, and the assembly
    # under both spellings was always `SyncService.sources()`.
    sources = client.get("/api/ui/v1/sources").json()["sources"]
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
    assert everyone["posted_usd"] > 0 and everyone["matched"] > 0
    assert kim["posted_usd"] == 0.0 and kim["matched"] == 0
    # Tomas owns the queue this flow routes to, so the workspace is his and
    # his totals are the tenant's — that is what a Seeing Space is. The leak
    # this guards is Kim's, who owns no queue and entered no invoice.
    assert tomas["posted_usd"] == everyone["posted_usd"]
    assert tomas["matched"] == everyone["matched"]


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
# `/api/ui/v1/payables` used to serve a composer: the vendor master, its open
# orders and their lines, four "twist" checkboxes, and a preview of the hint
# string the choices serialized to. Every control was a real graph row and the
# whole thing was still a way to manufacture an extraction from a form — an
# invoice nobody wrote, indistinguishable afterwards from one a model read.
#
# What is defended now is the opposite property: the door takes bytes and
# nothing else, the screen says which extractor will read them, and the outcome
# names the clause the Rule cited without re-deriving the verdict.

UI_PAYABLES = "/api/ui/v1/payables"
UI_SUBMISSIONS = "/api/ui/v1/payables/submissions"


def _pdf(marker: str) -> bytes:
    """A tiny artifact with a real PDF signature.

    Enough for `media.sniff` to call it `application/pdf` and for the store to
    key it by its content — which is the point of the marker: two uploads in
    one test must be two documents, and a content-hash id makes identical bytes
    one.
    """
    return b"%PDF-1.4\n% " + marker.encode() + b"\n%%EOF\n"


def _uploaded(filename: str = "", **overrides) -> dict:
    """An upload, as the console posts one.

    The filename carries the case, and that is the stub extractor's documented
    behaviour rather than a back door: `MODEL_BACKEND=none` means *the reader
    reads the name, not the page*, the screen says so in `submission.extractor`,
    and a deployment with a real extractor ignores it entirely. There is no
    request field that carries a case — that is what this section is about.
    """
    # The `.pdf` goes after a token whose charset stops at a dot: the `lines=`
    # and `total=` patterns would otherwise swallow the extension and fail to
    # parse. A real filename has the same hazard, which is why the grammar's
    # patterns are the constraint rather than a convention.
    name = filename or (
        "vendor=acme-industrial po=PO-7741 lines=STL-0250:2,FAS-M12:3 "
        "invoice=ACME-INV-9001.pdf"
    )
    body = {"image_base64": base64.b64encode(_pdf(name)).decode(), "filename": name}
    body.update(overrides)
    return body


def test_the_submission_panel_offers_no_way_to_compose_an_invoice(client, world):
    """The screen's controls are gone, and their absence is the assertion.

    A vendor picker, an order picker, line steppers and the twist checkboxes
    were all served from here. Removing the *widgets* would have left the
    payload; this checks the payload, because a console rebuilt against a
    server that still offers `twists` is one commit from composing again.
    """
    demo_world.seed_payables(world.payables, sync=world.sync)
    screen = client.get(UI_PAYABLES).json()

    assert "composer" not in screen
    panel = screen["submission"]
    assert set(panel) == {"policy", "extractor"}

    # What a person is owed before they read an answer: which reader produced
    # it. `stub` reads the filename; `vertex` reads the page.
    assert panel["extractor"] == "stub"

    # The governed clauses the flow reads, at the revision it reads them.
    limit = next(c for c in panel["policy"] if c["id"] == "auto-post-limit")
    assert limit["value"] == "$0.00"
    assert limit["ref"] == "knowledge:invoice-posting-policy@rev1"

    # And no row of the table carries a composed string any more.
    assert all("hint" not in outcome for outcome in screen["invoices"])


def test_an_uploaded_document_runs_the_flow_over_bytes_it_kept(client, world):
    """The one door, and it carries evidence.

    The artifact is stored before anything reads it, keyed by its content, and
    the invoice row points at it — which is what makes "a model read this
    document" a claim somebody can check two years later.
    """
    demo_world.seed_payables(world.payables, sync=world.sync)
    outcome = client.post(UI_SUBMISSIONS, json=_uploaded()).json()

    # Held at the clerk's desk like every other invoice — the $0.00 limit
    # applies to the side door too, and the gate the screen is handed is the
    # evidence of it. What this test is about is the artifact underneath, and
    # that is stored before anything reads it either way.
    assert outcome["invoice"]["state"] == "pending"
    assert outcome["invoice"]["match_verdict"] == "matched"
    assert outcome["gate"] is not None

    stored = world.documents.get(outcome["invoice"]["document_id"])
    assert stored is not None
    assert stored.media_type == "application/pdf"
    assert stored.byte_size > 0
    assert outcome["invoice"]["run_id"] in stored.run_ids


def test_the_outcome_marks_the_clause_the_rule_cited(client, world):
    """Juxtaposition, not prediction.

    The screen shows $9,800.00 beside "auto-post ≤ $2,500.00" and marks the
    clause the Rule's own reason names. Marking is attribution of a sentence
    `_posting_check` already wrote; deciding here would be a second copy of the
    rule, and the two would eventually disagree.
    """
    demo_world.seed_payables(world.payables, sync=world.sync)
    outcome = client.post(UI_SUBMISSIONS, json=_uploaded(
        "vendor=acme-industrial po=PO-7741 lines=STL-0375:12 total=9800.00 "
        "invoice=ACME-INV-9002.pdf"
    )).json()
    assert outcome["invoice"]["state"] == "pending"
    deciding = {c["id"] for c in outcome["policy"] if c["deciding"]}
    assert "auto-post-limit" in deciding
    # A clause nothing cited stays unmarked, so the marks mean something.
    assert "freight-allowance" not in deciding


def test_may_decide_answers_require_queue_before_the_403(client, world):
    """Tomas is bound to `ap-clerk`; Naviant Support is bound to nothing.

    The gate is the same gate. What differs is who may release it, and that is
    a queue binding rather than a rung on the write ladder — so the screen has
    to say which queue, not merely "not allowed". Support is the sharp case
    now that the workspace itself is queue-gated: it reads every invoice in
    the deployment, which is what platform reach is for, and decides none of
    them, which is what a queue binding is for.
    """
    demo_world.seed_payables(world.payables, sync=world.sync)
    body = _uploaded(
        "vendor=acme-industrial po=PO-7741 total=9800.00 "
        "invoice=ACME-INV-9003.pdf"
    )

    tomas = client.post(
        UI_SUBMISSIONS, json=body, headers={"X-Cumulus-Persona": "customer-user:ap"}
    ).json()["gate"]
    # v9 routes the review back to the person who uploaded it, which here is
    # Tomas — so the desk is his own rather than the shared queue, and he may
    # decide it on the very response that minted the desk.
    desk = world.queues.ensure_personal("connie.alberts@naviant.com").slug
    assert tomas["queue"] == desk
    assert tomas["may_decide"] and tomas["why_not"] == ""
    # Which step stopped it, not merely that something did.
    assert tomas["step_id"].startswith("k1") and tomas["step_name"] == "review-invoice"

    screen = client.get(
        UI_PAYABLES, headers={"X-Cumulus-Persona": "naviant-support"}
    ).json()
    support = next(
        o["gate"] for o in screen["invoices"]
        if o["invoice"]["invoice_number"] == "ACME-INV-9003"
    )
    assert support["queue"] == desk
    assert not support["may_decide"]
    assert support["why_not"] == f"this is in {desk}; you are bound to no queue"


def test_the_ap_workspace_is_the_clerk_desk_not_a_public_screen(client, world):
    """Kim owns no queue, and the workspace is more than the rows she has none of.

    The table was already scoped to her — an empty one — but the screen around
    it is the posting policy and a control that runs the flow and can auto-post
    money. Neither is anyone's own work, so the door says which desk it is
    rather than handing over an empty table that reads as "the business is
    quiet".
    """
    demo_world.seed_payables(world.payables, sync=world.sync)
    kim = {"X-Cumulus-Persona": "customer-user:ae"}

    refused = client.get(UI_PAYABLES, headers=kim)
    assert refused.status_code == 403
    assert "ap-clerk" in refused.json()["detail"]
    assert "your own work" in refused.json()["detail"]

    submitted = client.post(UI_SUBMISSIONS, json=_uploaded(), headers=kim)
    assert submitted.status_code == 403

    # Platform reach opens the screen and still may not file an invoice:
    # reading a workstream and committing to its book are different acts.
    support = {"X-Cumulus-Persona": "naviant-support"}
    assert client.get(UI_PAYABLES, headers=support).status_code == 200
    blocked = client.post(UI_SUBMISSIONS, json=_uploaded(), headers=support)
    assert blocked.status_code == 403
    assert "ap-clerk" in blocked.json()["detail"]


def test_a_submission_with_no_document_is_refused_at_both_doors(client, world):
    """The shape that used to work, and must not.

    A filename and no bytes was a valid submission: the stub read the name and
    the run posted an invoice whose `document_id` pointed at a zero-byte
    artifact. Both doors now refuse it, and the console door refuses a minted
    key outright besides — a machine has no screens.
    """
    demo_world.seed_payables(world.payables, sync=world.sync)
    assert client.post(
        UI_SUBMISSIONS, json={"filename": "a-scan.pdf"}
    ).status_code == 400
    assert client.post(UI_SUBMISSIONS, json={}).status_code == 400
    assert client.get(UI_PAYABLES, headers={"X-API-Key": "k"}).status_code == 401

    rest = client.post("/api/v1/payables", json={"filename": "a-scan.pdf"})
    assert rest.status_code == 400
    # And there is no field left to describe a case with.
    described = client.post(
        "/api/v1/payables",
        json={"filename": "a.pdf", "note": "vendor=acme-industrial"},
    )
    assert described.status_code == 400


def test_the_screen_and_its_strip_count_the_same_slice(client, world):
    """One route, so the strip cannot be computed over a different read than
    the table under it — the failure the two-call version made possible.

    The two personas `AP_VISIBLE` gives zero rows are not on this list: they
    are refused the console screen outright (see
    `test_the_ap_workspace_is_the_clerk_desk_not_a_public_screen`). The REST
    table still answers them with an empty list, which is the honest shape for
    a row list; a *workspace* with a door that files invoices is not.
    """
    demo_world.seed_payables(world.payables, sync=world.sync)
    for persona, expected in sorted(AP_VISIBLE.items()):
        response = client.get(
            UI_PAYABLES, headers={"X-Cumulus-Persona": persona}
        )
        if expected == 0:
            assert response.status_code == 403, persona
            continue
        screen = response.json()
        assert len(screen["invoices"]) == screen["stats"]["total"] == expected


# ---- the quarantine ---------------------------------------------------------


def test_no_invented_business_vocabulary_reaches_a_production_path():
    """Four vendor names and six SKUs that no customer ever had.

    They were module-level vocabulary shared with the console's composer, the
    seeded orders and the stub extractor, so a deployed screen offered invented
    parts beside the ERP's real catalogue. They survive only as the stub
    reader's own fixtures, under a `_STUB_` prefix and below a banner that says
    so — the prefix exists precisely so this check, and a reviewer's grep, can
    be written at all.

    Asserted as an exact set rather than a bound: a third module reaching for
    the stub's vocabulary is the regression, and a bound would let it through.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    holders: dict[str, list[int]] = {}
    banners: dict[str, int] = {}
    for path in sorted(root.rglob("*.py")):
        lines = path.read_text().splitlines()
        hits = [n for n, line in enumerate(lines, 1) if "_STUB_" in line]
        if not hits:
            continue
        holders[str(path.relative_to(root))] = hits
        banners[str(path.relative_to(root))] = next(
            (n for n, line in enumerate(lines, 1) if "STUB FIXTURES" in line), 0
        )

    assert set(holders) == {
        "tasks/accounts_payable/domain.py",
        "tasks/expense_reimbursement/domain.py",
    }
    # Every reference sits under the banner that quarantines it.
    for module, hits in holders.items():
        assert banners[module], module
        assert min(hits) > banners[module], module
