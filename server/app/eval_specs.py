"""The eval specs, the worlds their cases run in, and the cases.

Declared in Python and exported to the map as governed knowledge, the same way
the ontology and the Collection registry are — a spec is a declarative config
that decides what "correct" means, so it belongs in the map rather than in
application code.

Read the specs as **contracts**: what the accounts-payable flow promises about
an Invoice, how tightly, and at which point in a run. The cases below are
deliberately few and each exists to pin one behaviour that would otherwise
regress silently — one model call reading the document and the resolver (not
the model) settling the entity, the match re-running after a correction rather
than being overridden, and the same vendor seen twice resolving to one object
rather than two.

The **fixtures** are declared here too, and named. Three AP cases run in the
same world; before it had a name each of them carried its own copy, so "make
the receipt short" was a change in one place and a silent divergence in three.
Code is the authoring source until a console curates them, which is why they
are seeded into a store rather than read out of this module at grading time.
"""

from __future__ import annotations

from .ontology import ONTOLOGY
from .evals import (
    EvalCase,
    EvalSpec,
    ExpectedObject,
    FieldRule,
    Fixture,
    FixtureObject,
    FixtureStore,
    LinkRule,
    Milestone,
    MilestoneExpectation,
    ObjectRule,
    ScriptedDecision,
    TrajectoryRule,
)
from .queues import personal_slug

DECLARED_IN = "server/app/eval_specs.py"

#: Who submits the invoices these cases are about. Named once because the
#: governed approval table routes the review back to them: the desk a held
#: invoice waits on is *this person's own*, so the expectation is derived from
#: the address rather than written out as a slug nobody would recognise.
AP_SUBMITTER = "connie.alberts@naviant.com"

# ---------------------------------------------------------------------------
# Shared fixture pieces
# ---------------------------------------------------------------------------
#
# The precondition is expressed as the deliveries the real feeds would have
# made, so a case is set up the way production got there — and the sync flows
# are exercised on the way in rather than bypassed.

_ACME = {
    "external_id": "V-10041", "name": "Acme Industrial Inc.",
    "tax_id": "94-2711883", "status": "active", "payment_terms": "NET30",
}
_NORTHWIND = {
    "external_id": "V-10088", "name": "Northwind Traders LLC",
    "tax_id": "81-4410072", "status": "active", "payment_terms": "NET45",
}

_PO_7741 = {
    "external_id": "PO-7741", "po_number": "PO-7741",
    "vendor_external_id": "V-10041", "vendor_name": "Acme Industrial Inc.",
    "ordered_date": "2026-08-05", "status": "open",
    "lines": [
        {"sku": "STL-0250", "description": "Hot-rolled steel plate, 1/4 in",
         "quantity_ordered": 2.0, "unit_price_usd": 486.00},
        {"sku": "FAS-M12",
         "description": "Grade 8 hex bolt, M12 x 60mm, box of 100",
         "quantity_ordered": 3.0, "unit_price_usd": 128.75},
    ],
}


def _receipt(short: float = 0.0) -> dict:
    return {
        "external_id": "RR-5501", "receipt_number": "RR-5501",
        "po_number": "PO-7741", "received_date": "2026-08-11",
        "status": "complete",
        "lines": [
            {"sku": "STL-0250", "description": "Hot-rolled steel plate, 1/4 in",
             "quantity_received": 2.0 - short},
            {"sku": "FAS-M12",
             "description": "Grade 8 hex bolt, M12 x 60mm, box of 100",
             "quantity_received": 3.0},
        ],
    }


def _ap_deliveries(short: float = 0.0) -> list[dict]:
    return [
        {"source": "erp-vendors", "watermark": "2026-08-01T00:00:00Z",
         "vendors": [_ACME, _NORTHWIND]},
        {"source": "erp-purchase-orders", "purchase_orders": [_PO_7741]},
        {"source": "erp-receiving", "receiving_reports": [_receipt(short)]},
    ]


# ---------------------------------------------------------------------------
# The AP demo world
# ---------------------------------------------------------------------------
#
# These deliveries used to live in `payables.py` as a startup seed, and this
# module imported them. The seed is gone — the estate is the only world a
# running server fills from — so the *data* moved here rather than dying with
# it: it is a declared eval fixture, which is the one place invented records
# still belong. It is copied verbatim, because the case worlds are graded and a
# world that quietly changed shape would move every pass rate with it.
#
# Four vendors from the ERP, one cross-system match, one byte-identical
# redelivery (echo suppression), and one misspelling that scores into the
# steward band; then the orders and the receipts they are matched against.

AP_CATALOG_LINES: dict[str, list[tuple[str, float]]] = {
    "PO-7741": [("STL-0250", 2.0), ("FAS-M12", 3.0)],
    "PO-7802": [("WLD-6013", 4.0), ("PPE-GLV", 2.0)],
    "PO-7815": [("CNS-HYD", 3.0), ("FAS-M12", 2.0)],
}

#: sku -> (description, unit price). Held here rather than read out of the stub
#: extractor's own catalogue so the fixture cannot drift when that is edited: a
#: graded world is a frozen thing.
AP_CATALOG: dict[str, tuple[str, float]] = {
    "STL-0250": ("Hot-rolled steel plate, 1/4 in", 486.00),
    "STL-0375": ("Hot-rolled steel plate, 3/8 in", 712.50),
    "FAS-M12": ("Grade 8 hex bolt, M12 x 60mm, box of 100", 128.75),
    "WLD-6013": ("Welding rod E6013, 5kg", 92.40),
    "PPE-GLV": ("Cut-resistant gloves, size L, 12 pair", 64.20),
    "CNS-HYD": ("Hydraulic fluid ISO 46, 20L drum", 214.90),
}


def ap_catalog_delivery() -> dict:
    """The AP fixture catalogue as `Item` rows the ERP would have sent.

    A fixture world that wants a catalogue has to *land* one, the way the
    estate does. It is what the seeded purchase orders bill against, so a
    fixture invoice can pair with a real PO line rather than with a list of
    SKUs living in a Python constant — which is the invented vocabulary that
    stopped being offered anywhere. Kept out of `ap_demo_deliveries` on
    purpose: the graded worlds are frozen, and a catalogue is only needed
    where something picks from one.
    """
    return {
        "source": "erp-sales",
        "watermark": "2026-08-01T00:00:00Z",
        "items": [
            {
                "external_id": f"AP-{index:04d}",
                "sku": sku,
                "display_name": description,
                "item_type": "Inventory Item",
                "base_price": price,
            }
            for index, (sku, (description, price)) in enumerate(
                sorted(AP_CATALOG.items()), start=1
            )
        ],
    }


def _catalog_lines(po_number: str) -> list[dict]:
    return [
        {
            "sku": sku,
            "description": AP_CATALOG[sku][0],
            "quantity_ordered": quantity,
            "unit_price_usd": AP_CATALOG[sku][1],
        }
        for sku, quantity in AP_CATALOG_LINES[po_number]
    ]


def _received_lines(po_number: str, shortfall: dict | None = None) -> list[dict]:
    out = []
    for sku, quantity in AP_CATALOG_LINES[po_number]:
        received = quantity - (shortfall or {}).get(sku, 0)
        out.append({
            "sku": sku,
            "description": AP_CATALOG[sku][0],
            "quantity_received": received,
            "condition": "short" if received < quantity else "good",
        })
    return out


def ap_demo_deliveries() -> list[dict]:
    """The vendor, order and receipt deliveries the AP demo world stands on."""
    return [
        {
            "source": "erp-vendors",
            "watermark": "2026-08-01T00:00:00Z",
            "idempotency_key": "erp-vendors-001",
            "vendors": [
                {
                    "external_id": "V-10041", "name": "Acme Industrial Inc.",
                    "tax_id": "94-2711883", "status": "active",
                    "payment_terms": "NET30", "remit_country": "US",
                },
                {
                    "external_id": "V-10088", "name": "Northwind Traders LLC",
                    "tax_id": "81-4410072", "status": "active",
                    "payment_terms": "NET45", "remit_country": "US",
                },
                {
                    "external_id": "V-10102", "name": "Bay State Fasteners Co.",
                    "tax_id": "04-3390118", "status": "active",
                    "payment_terms": "NET30", "remit_country": "US",
                },
                {
                    "external_id": "V-10190", "name": "Cascade Welding Supply",
                    "tax_id": "91-2038844", "status": "active",
                    "payment_terms": "NET15", "remit_country": "US",
                },
            ],
        },
        # A second system's id for a vendor we already hold: the legal-form
        # variant normalizes to the same key, so this auto-links and the vendor
        # ends up with two source records. This is the master-data payoff.
        {
            "source": "procurement-coupa",
            "watermark": "2026-08-02T00:00:00Z",
            "idempotency_key": "coupa-001",
            "vendors": [{
                "external_id": "C-5501", "name": "ACME INDUSTRIAL, INCORPORATED",
                "tax_id": "94-2711883", "status": "active",
                "payment_terms": "NET30",
            }],
        },
        # Byte-identical re-delivery: everything lands as `unchanged`.
        {
            "source": "erp-vendors",
            "watermark": "2026-08-03T00:00:00Z",
            "idempotency_key": "erp-vendors-002",
            "vendors": [{
                "external_id": "V-10041", "name": "Acme Industrial Inc.",
                "tax_id": "94-2711883", "status": "active",
                "payment_terms": "NET30", "remit_country": "US",
            }],
        },
        # A misspelling from a legacy feed: scores in the steward band, so
        # nothing is asserted and a match_review Task waits in the queue.
        {
            "source": "legacy-ap-extract",
            "watermark": "2026-08-04T00:00:00Z",
            "idempotency_key": "legacy-001",
            "vendors": [{
                "external_id": "L-77", "name": "Acme Industial Inc",
                "status": "active",
            }],
        },
        {
            "source": "erp-purchase-orders",
            "watermark": "2026-08-05T00:00:00Z",
            "idempotency_key": "erp-po-001",
            "purchase_orders": [
                {
                    "external_id": "PO-7741", "po_number": "PO-7741",
                    "vendor_external_id": "V-10041",
                    "vendor_name": "Acme Industrial Inc.",
                    "ordered_date": "2026-08-05", "status": "open",
                    "lines": _catalog_lines("PO-7741"),
                },
                {
                    "external_id": "PO-7802", "po_number": "PO-7802",
                    "vendor_external_id": "V-10088",
                    "vendor_name": "Northwind Traders LLC",
                    "ordered_date": "2026-08-07", "status": "open",
                    "lines": _catalog_lines("PO-7802"),
                },
                {
                    "external_id": "PO-7815", "po_number": "PO-7815",
                    "vendor_external_id": "V-10102",
                    "vendor_name": "Bay State Fasteners Co.",
                    "ordered_date": "2026-08-09", "status": "open",
                    "lines": _catalog_lines("PO-7815"),
                },
            ],
        },
        {
            "source": "erp-receiving",
            "watermark": "2026-08-12T00:00:00Z",
            "idempotency_key": "erp-receiving-001",
            "receiving_reports": [
                {
                    "external_id": "RR-5501", "receipt_number": "RR-5501",
                    "po_number": "PO-7741", "received_date": "2026-08-11",
                    "status": "complete", "lines": _received_lines("PO-7741"),
                },
                # One line short: the invoice that bills the full quantity will
                # land a quantity variance, which is the point.
                {
                    "external_id": "RR-5510", "receipt_number": "RR-5510",
                    "po_number": "PO-7802", "received_date": "2026-08-13",
                    "status": "partial", "lines": _received_lines("PO-7802"),
                },
                {
                    "external_id": "RR-5533", "receipt_number": "RR-5533",
                    "po_number": "PO-7815", "received_date": "2026-08-15",
                    "status": "complete", "lines": _received_lines("PO-7815"),
                },
            ],
        },
    ]


# ---------------------------------------------------------------------------
# The fixtures
# ---------------------------------------------------------------------------
#
# Built by a function rather than held as module constants: nothing needs a
# `Fixture` at import time, and the seeder is the only caller.


def declared_fixtures() -> list[Fixture]:
    """Every world declared in code, eval and demo alike.

    The demo seed is in this list because it is the same kind of thing: a
    fragment of ABox written by hand, only observable by running it. Giving it
    a different name would have been two registries for one concept.
    """
    common = {"origin": "declared", "authored_by": "platform-eng"}
    ap = {
        **common, "flow_id": "accounts-payable", "derived_from": DECLARED_IN,
        "read_set": ["knowledge:invoice-posting-policy@rev1"],
    }
    expense = {
        **common, "flow_id": "expense-reimbursement", "derived_from": DECLARED_IN,
        "read_set": ["knowledge:expense-approval-policy@rev1"],
    }
    return [
        Fixture(
            id="ap/acme-po-7741-clean",
            name="Acme, PO-7741, received in full",
            summary=(
                "A known vendor in the master, its order, and a receiving "
                "report that agrees with the order line for line. The world "
                "three cases need: the one that must post untouched, the one "
                "whose price a clerk corrects, and the unknown vendor that "
                "arrives beside it."
            ),
            deliveries=_ap_deliveries(),
            **ap,
        ),
        Fixture(
            id="ap/acme-po-7741-short-receipt",
            name="Acme, PO-7741, one plate short",
            summary=(
                "The same world with the receiving report one unit light. The "
                "receiving leg is the one most three-way matches skip, so it "
                "gets a world of its own rather than a flag on a case."
            ),
            deliveries=_ap_deliveries(short=1.0),
            **ap,
        ),
        Fixture(
            id="ap/acme-po-7741-meridian-onboarded",
            name="Acme's order, and Meridian already on the master",
            summary=(
                "A vendor an earlier invoice onboarded, standing in the graph "
                "as an entity rather than a delivery. The precondition for "
                "asking whether the second invoice links to it or mints a "
                "duplicate."
            ),
            deliveries=_ap_deliveries(),
            objects=[FixtureObject(
                type="Vendor", key="Meridian Supply Ltd.",
                props={
                    "name": "Meridian Supply Ltd.",
                    "slug": "meridian-supply",
                    "status": "active",
                },
                label="Meridian Supply Ltd.",
            )],
            **ap,
        ),
        Fixture(
            id="expense/felicia-on-the-roster",
            name="Felicia, on the roster",
            summary=(
                "One Employee and nothing else. Org membership is a member_of "
                "link rather than a field, so a fixture claiming `team` here "
                "would be re-smuggling the string the ontology just retired."
            ),
            objects=[FixtureObject(
                type="Employee", key="felicia.marks@naviant.com",
                props={
                    "email": "felicia.marks@naviant.com",
                    "title": "Account Executive",
                },
                label="felicia.marks@naviant.com",
            )],
            **expense,
        ),
        Fixture(
            id="expense/john-on-the-roster",
            name="John, on the roster",
            summary="One Employee, for the compound receipt a reviewer trims.",
            objects=[FixtureObject(
                type="Employee", key="john.smith@naviant.com",
                props={"email": "john.smith@naviant.com"},
                label="john.smith@naviant.com",
            )],
            **expense,
        ),
        Fixture(
            id="expense/nathan-on-the-roster",
            name="Nathan, on the roster",
            summary="One Employee, for the restricted category that is denied.",
            objects=[FixtureObject(
                type="Employee", key="nathan.pryce@naviant.com",
                props={"email": "nathan.pryce@naviant.com"},
                label="nathan.pryce@naviant.com",
            )],
            **expense,
        ),
        _identity_world(),
        _state_world(),
        *_wick_fixtures(),
        _demo_world(),
    ]


def _demo_world() -> Fixture:
    """The whole AP demo world as one fragment.

    It belongs to no flow — it is what the AP story as a whole is set on — so
    its `flow_id` is empty rather than guessed at. It was the world a startup
    seed built; now that the running server fills only from the estate, it is
    a fixture and nothing else, which is why `derived_from` names this module.
    """
    return Fixture(
        id="seed/demo-world",
        name="Demo world",
        summary=(
            "The vendors, purchase orders and receiving reports the AP demo "
            "history stands on — including a byte-identical redelivery, a "
            "cross-system match that auto-links, and a misspelling that waits "
            "for a steward."
        ),
        deliveries=ap_demo_deliveries(),
        origin="declared",
        derived_from=DECLARED_IN,
        authored_by="platform-eng",
    )


def seed_fixtures_if_empty(store: FixtureStore) -> int:
    """Register the worlds declared here, without stepping on curated ones.

    By origin rather than by emptiness, which is the difference that matters:
    a stored fixture still marked `declared` is code's to replace, so editing
    one in this file reaches an environment that already has it — the trap a
    seed-once pass falls into. Anything a person authored or a run was promoted
    into is left exactly as it stands, because a redeploy must never discard a
    world somebody built.
    """
    written = 0
    for fixture in declared_fixtures():
        existing = store.get(fixture.id)
        if existing is not None and existing.origin != "declared":
            continue
        store.put(fixture)
        written += 1
    return written


# ---------------------------------------------------------------------------
# Accounts payable
# ---------------------------------------------------------------------------

AP_SPEC = EvalSpec(
    id="accounts-payable-core",
    flow_id="accounts-payable",
    version=1,
    summary=(
        "What the accounts-payable flow is answerable for: naming the right "
        "vendor it read off the document, reading the commercial content of "
        "that document in a single pass, retaining a three-way match, and "
        "posting only what the governed policy allows. Fields the vendor controls rather than the "
        "flow — due dates, currency — are ignored on purpose: grading them "
        "would be grading the wrong system."
    ),
    milestones=[
        Milestone(
            id="vendor-settled",
            anchor="last_of_kind",
            selector="resolve",
            label="once the vendor has been settled",
            note=(
                "A division-of-labour promise, not just an outcome: the model "
                "contributes a name it read off a page and the *resolver* "
                "decides which entity that is, against the master and the "
                "governed thresholds. One model call gets to see the document."
                "\n\nAnchored on the kind rather than on an effect, because a "
                "resolver that recognises a vendor already on the master "
                "writes nothing at all — there is no ref that could name this "
                "point, only the act."
            ),
            objects=[
                ObjectRule(
                    type="Vendor",
                    fields=[
                        # Read off a page by a model, so judged on similarity
                        # rather than character equality.
                        FieldRule(field="name", match="fuzzy", tolerance=0.90),
                        FieldRule(field="slug", match="exact"),
                        FieldRule(
                            field="status", match="one_of",
                            options=["active", "onboarding", "inactive"],
                        ),
                        FieldRule(
                            field="tax_id", match="ignore",
                            note="printed on some invoices and not others",
                        ),
                        FieldRule(field="payment_terms", match="ignore",
                                  note="the ERP's to state, not this flow's"),
                    ],
                ),
            ],
            trajectory=[
                TrajectoryRule(
                    kind="read", expected="document:",
                    note="a pass over the document happened before this point",
                ),
                TrajectoryRule(
                    kind="model_calls_at_most", expected="1",
                    note=(
                        "…and only one: no second reading of the same page. "
                        "A count, not a named step, so splitting or renaming "
                        "the extraction cannot fake it either way"
                    ),
                ),
            ],
        ),
        Milestone(
            id="invoice-retained",
            anchor="wrote",
            selector="graph:Invoice/",
            label="once the invoice is retained",
            note=(
                "The flow writes the graph once, at commit, so the commit is "
                "named by its effect rather than by the step that happens to "
                "perform it. A run held at a gate never reaches this "
                "milestone, so declaring it in a case *is* the assertion that "
                "this invoice should have posted — and a case for a held "
                "invoice asserts the entities are absent at `held` instead, "
                "which pins the rule that nothing is committed until the gate "
                "is answered."
            ),
            objects=[
                ObjectRule(
                    type="Invoice",
                    fields=[
                        FieldRule(field="invoice_number", match="exact"),
                        FieldRule(field="po_number", match="exact"),
                        FieldRule(field="total_usd", match="approx", tolerance=0.01),
                        FieldRule(field="subtotal_usd", match="approx", tolerance=0.01),
                        FieldRule(
                            field="state", match="one_of",
                            options=["posted", "pending", "denied"],
                        ),
                        FieldRule(field="due_date", match="ignore",
                                  note="the vendor's payment terms"),
                        FieldRule(field="currency", match="ignore"),
                        FieldRule(field="freight_usd", match="approx",
                                  tolerance=0.01),
                        FieldRule(field="tax_usd", match="ignore"),
                        FieldRule(field="invoice_date", match="ignore",
                                  note="the document's, and it drifts with the stub"),
                    ],
                    links=[
                        LinkRule(
                            type="issued", direction="in", to_type="Vendor",
                            expect="exists",
                            note="exactly one issuing vendor — the TBox enforces "
                                 "the 'exactly', this checks the 'one'",
                        ),
                        LinkRule(type="has_invoice_line", expect="count"),
                        LinkRule(type="matched_by", expect="exists"),
                        LinkRule(type="evidenced_by_document", expect="exists"),
                    ],
                ),
                ObjectRule(
                    type="MatchSet",
                    fields=[
                        FieldRule(field="verdict", match="exact"),
                        FieldRule(field="matched_lines", match="exact"),
                        FieldRule(field="unmatched_lines", match="exact"),
                        FieldRule(field="price_variance_usd", match="approx",
                                  tolerance=0.01),
                        FieldRule(field="quantity_variance", match="approx",
                                  tolerance=0.001),
                        FieldRule(field="tolerance_ref", match="ignore"),
                        FieldRule(field="po_numbers", match="ignore"),
                        FieldRule(field="receipt_numbers", match="ignore"),
                    ],
                    links=[
                        LinkRule(
                            type="matches_invoice_line", expect="count",
                            props=[FieldRule(field="verdict", match="exact")],
                        ),
                    ],
                ),
            ],
            trajectory=[
                TrajectoryRule(
                    kind="wrote", expected="graph:Invoice",
                    note="the commit writes the entity, not just a row",
                ),
                TrajectoryRule(
                    kind="wrote", expected="link:matched_by",
                    note="…and the match set is linked to it",
                ),
                TrajectoryRule(
                    kind="read", expected="knowledge:invoice-posting-policy",
                    note="the governed values were actually read",
                ),
            ],
        ),
        Milestone(
            id="outcome",
            anchor="terminal",
            label="once the run has finished",
            note=(
                "Only a run that finished has an outcome, so this refuses to "
                "resolve for one still sitting at a gate rather than quietly "
                "grading a partial journal as if it were the end. A case about "
                "an invoice that is *held* asserts against `held` instead, and "
                "the two are not interchangeable."
            ),
            trajectory=[
                TrajectoryRule(kind="terminal_status"),
                TrajectoryRule(kind="gate_count"),
                TrajectoryRule(kind="queue"),
            ],
        ),
        Milestone(
            id="held",
            anchor="first_of_kind",
            selector="task",
            label="the first time the run stopped for a person",
            note=(
                "The counterpart to `outcome`, and the reason a `first_of_kind` "
                "anchor exists at all: a suspended run has no terminal to "
                "anchor on, and what a held invoice is judged on is precisely "
                "that nothing was committed *before* the gate was answered. "
                "First rather than last, because a second gate later in the "
                "run would move the point the absence is asserted at."
            ),
            trajectory=[
                TrajectoryRule(kind="terminal_status"),
                TrajectoryRule(kind="gate_count"),
                TrajectoryRule(kind="queue"),
            ],
        ),
    ],
)


AP_CASES = [
    EvalCase(
        id="ap-known-vendor-clean",
        spec_id=AP_SPEC.id,
        name="Known vendor, bills its PO exactly, approved at a glance",
        summary=(
            "The happy path, and the one that regresses silently: the vendor "
            "is in the master and the lines match the order and the receipt, "
            "so the clerk's only question is the governed auto-post limit — "
            "which is $0.00, so they are asked. What this grades is that "
            "nothing else was wrong: one gate, answered with a bare approval, "
            "and the invoice posts exactly as read."
        ),
        fixture_id="ap/acme-po-7741-clean",
        trigger={
            "submitted_by": "connie.alberts@naviant.com",
            "filename": "acme.pdf",
            "hint": "vendor=acme-industrial po=PO-7741 invoice=ACME-INV-1 "
                    "lines=STL-0250:2,FAS-M12:3",
        },
        # One decision, and it is deliberately a bare approval with nothing to
        # correct: the case is about a clean invoice, and the gate is the
        # governed limit rather than anything the flow found. A second
        # decision here would mean the run came back, which is what the
        # `gate_count` below refuses.
        decisions=[ScriptedDecision(
            verdict="approved",
            note="Matches the order line for line; nothing to check.",
        )],
        expectations=[
            MilestoneExpectation(
                milestone="vendor-settled",
                objects=[ExpectedObject(
                    type="Vendor", key="Acme Industrial Inc.",
                    fields={
                        "name": "Acme Industrial Inc.",
                        "slug": "acme-industrial",
                        "status": "active",
                    },
                )],
            ),
            MilestoneExpectation(
                milestone="invoice-retained",
                objects=[
                    ExpectedObject(
                        type="Invoice", key="acme-industrial:ACME-INV-1",
                        fields={
                            "invoice_number": "ACME-INV-1",
                            "po_number": "PO-7741",
                            "state": "posted",
                            "subtotal_usd": 1358.25,
                        },
                        link_counts={"has_invoice_line": 3},
                    ),
                    ExpectedObject(
                        type="MatchSet", key="acme-industrial:ACME-INV-1",
                        fields={
                            "verdict": "matched",
                            "matched_lines": 3,
                            "unmatched_lines": 0,
                            "price_variance_usd": 0.0,
                        },
                        link_counts={"matches_invoice_line": 3},
                    ),
                ],
            ),
            MilestoneExpectation(
                milestone="outcome",
                trajectory={"terminal_status": "succeeded", "gate_count": "1"},
            ),
        ],
    ),
    EvalCase(
        id="ap-price-variance-corrected",
        spec_id=AP_SPEC.id,
        name="Price variance holds the invoice; a correction re-runs the match",
        summary=(
            "The property worth protecting: a clerk's correction is applied "
            "and the checks run *again*, rather than the reviewer overriding a "
            "rule. If this ever passes without a second match, the correction "
            "path has quietly become an override. Two gates rather than one, "
            "and the second is the governed limit's: a correction settles the "
            "variance, and at a $0.00 auto-post limit somebody still has to "
            "say yes — so the case ends with the approval that follows."
        ),
        fixture_id="ap/acme-po-7741-clean",
        trigger={
            "submitted_by": "connie.alberts@naviant.com",
            "filename": "acme.pdf",
            "hint": "vendor=acme-industrial po=PO-7741 invoice=ACME-INV-2 "
                    "lines=STL-0250:2,FAS-M12:3 pricebump",
        },
        decisions=[
            ScriptedDecision(
                verdict="corrected",
                note="Vendor billed the old list price; corrected to the PO price.",
                payload={"lines": [{"ordinal": 1, "unit_price_usd": 486.00}]},
            ),
            ScriptedDecision(
                verdict="approved",
                note="The corrected price agrees with the order.",
            ),
        ],
        expectations=[
            MilestoneExpectation(
                milestone="invoice-retained",
                objects=[
                    ExpectedObject(
                        type="Invoice", key="acme-industrial:ACME-INV-2",
                        fields={"state": "posted"},
                    ),
                    ExpectedObject(
                        type="MatchSet", key="acme-industrial:ACME-INV-2",
                        fields={"verdict": "matched", "unmatched_lines": 0},
                    ),
                ],
            ),
            MilestoneExpectation(
                milestone="outcome",
                trajectory={"terminal_status": "succeeded", "gate_count": "2"},
            ),
        ],
    ),
    EvalCase(
        id="ap-quantity-variance-holds",
        spec_id=AP_SPEC.id,
        name="Billing more than was received holds the invoice",
        summary=(
            "The receiving leg is the one most three-way matches skip. A short "
            "receipt has to stop the invoice even though the PO agrees."
        ),
        fixture_id="ap/acme-po-7741-short-receipt",
        trigger={
            "submitted_by": "connie.alberts@naviant.com",
            "filename": "acme.pdf",
            "hint": "vendor=acme-industrial po=PO-7741 invoice=ACME-INV-3 "
                    "lines=STL-0250:2,FAS-M12:3",
        },
        expectations=[MilestoneExpectation(
            # `held`, not `outcome`: this run has not finished, and judging it
            # as though it had is exactly the vacuous pass a milestone must
            # refuse. Asserting the absence *at the gate* is the point.
            milestone="held",
            objects=[
                ExpectedObject(
                    type="Invoice", key="acme-industrial:ACME-INV-3",
                    absent=True,
                ),
                ExpectedObject(
                    type="MatchSet", key="acme-industrial:ACME-INV-3",
                    absent=True,
                ),
            ],
            trajectory={
                "terminal_status": "suspended",
                "gate_count": "1",
                # The submitter's own desk, per the governed approval table.
                "queue": personal_slug(AP_SUBMITTER),
            },
        )],
    ),
    EvalCase(
        id="ap-unknown-vendor-onboards",
        spec_id=AP_SPEC.id,
        name="An unknown vendor is minted onboarding, not matched",
        summary=(
            "The resolver's other branch. Nothing in the vendor master "
            "resembles this name, so it is minted rather than matched — and it "
            "stays `onboarding` until a human vouches for it."
        ),
        fixture_id="ap/acme-po-7741-clean",
        trigger={
            "submitted_by": "connie.alberts@naviant.com",
            "filename": "mer.pdf",
            "hint": "newvendor=meridian-supply nopo invoice=MER-1 "
                    "lines=PPE-GLV:2 total=190.00",
        },
        expectations=[
            MilestoneExpectation(
                milestone="vendor-settled",
                objects=[ExpectedObject(
                    type="Vendor", key="Meridian Supply Ltd.",
                    fields={"status": "onboarding", "slug": "meridian-supply"},
                )],
            ),
            MilestoneExpectation(
                milestone="held",
                objects=[ExpectedObject(
                    type="Invoice", key="meridian-supply:MER-1", absent=True,
                )],
                trajectory={
                    "terminal_status": "suspended",
                    "gate_count": "1",
                    # The submitter's own desk, per the governed approval table.
                    "queue": personal_slug(AP_SUBMITTER),
                },
            ),
        ],
    ),
    EvalCase(
        id="ap-second-invoice-links-not-mints",
        spec_id=AP_SPEC.id,
        name="A vendor seen before is linked, not minted a second time",
        summary=(
            "The property that makes a vendor an entity. This vendor was "
            "onboarded by an earlier invoice, so its second invoice must "
            "resolve to the *same* object — `active`, not a fresh "
            "`onboarding` duplicate — even though the name is only ever read "
            "off a page."
        ),
        fixture_id="ap/acme-po-7741-meridian-onboarded",
        trigger={
            "submitted_by": "connie.alberts@naviant.com",
            "filename": "mer2.pdf",
            "hint": "newvendor=meridian-supply nopo invoice=MER-2 "
                    "lines=PPE-GLV:1 total=112.00",
        },
        decisions=[
            ScriptedDecision(verdict="approved", note="No PO at this size."),
        ],
        expectations=[
            MilestoneExpectation(
                milestone="vendor-settled",
                objects=[ExpectedObject(
                    type="Vendor", key="Meridian Supply Ltd.",
                    fields={"status": "active", "slug": "meridian-supply"},
                )],
            ),
            MilestoneExpectation(
                milestone="invoice-retained",
                objects=[ExpectedObject(
                    type="Invoice", key="meridian-supply:MER-2",
                    fields={"state": "posted", "total_usd": 112.00},
                )],
            ),
            MilestoneExpectation(
                milestone="outcome",
                trajectory={"terminal_status": "succeeded", "gate_count": "1"},
            ),
        ],
    ),
]


# ---------------------------------------------------------------------------
# Expense reimbursement
# ---------------------------------------------------------------------------

EXPENSE_SPEC = EvalSpec(
    id="expense-reimbursement-core",
    flow_id="expense-reimbursement",
    version=1,
    summary=(
        "What the expense flow is answerable for: projecting the submitter's "
        "documents and words into lines, judging them against the governed "
        "policy, and re-judging them after a correction. The line — not the "
        "document — is the unit graded, because a compound receipt is two "
        "facts and a reviewer can add a third."
    ),
    milestones=[
        Milestone(
            id="recorded",
            anchor="terminal",
            label="once the run has finished",
            note=(
                "One milestone is enough here, and it is the honest one: the "
                "expense flow commits at the end and every declared case runs "
                "to completion, a denial included. A `resolve` or `check` "
                "milestone copied over from the AP spec would find no such "
                "step and say so rather than passing on an empty scope."
            ),
            objects=[
                ObjectRule(
                    type="ExpenseRequest",
                    fields=[
                        FieldRule(field="requested_usd", match="approx",
                                  tolerance=0.01),
                        FieldRule(field="approved_usd", match="approx",
                                  tolerance=0.01),
                        FieldRule(field="state", match="exact"),
                        FieldRule(field="employee_email", match="exact"),
                        FieldRule(field="commentary", match="ignore",
                                  note="the submitter's prose, quoted not graded"),
                        FieldRule(field="submitted_at", match="ignore"),
                    ],
                    links=[
                        LinkRule(type="submitted", direction="in",
                                 to_type="Employee", expect="exists"),
                        LinkRule(type="has_expense_line", expect="count"),
                        # Counted rather than asserted to exist. Every v10 run
                        # is confirmed by its submitter, so the edge is there
                        # on the recorded cases — but a case that ends
                        # suspended, or one graded under v9's auto-approval
                        # path, correctly has none, and `exists` would make
                        # this rule a claim about the version rather than about
                        # the request.
                        LinkRule(type="decided_by", expect="count"),
                    ],
                ),
                ObjectRule(
                    type="ExpenseLine",
                    fields=[
                        FieldRule(field="requested_usd", match="approx",
                                  tolerance=0.01),
                        FieldRule(field="document_total_usd", match="approx",
                                  tolerance=0.01),
                        FieldRule(field="category", match="exact"),
                        FieldRule(field="origin", match="exact"),
                        FieldRule(field="purpose", match="present"),
                        FieldRule(field="vendor", match="fuzzy", tolerance=0.85),
                        FieldRule(field="date", match="ignore"),
                    ],
                    links=[
                        LinkRule(type="evidenced_by", to_type="Document",
                                 expect="exists"),
                    ],
                ),
            ],
            trajectory=[
                TrajectoryRule(kind="terminal_status"),
                TrajectoryRule(kind="gate_count"),
                TrajectoryRule(kind="wrote", expected="graph:ExpenseLine"),
                TrajectoryRule(
                    kind="read", expected="knowledge:expense-approval-policy",
                    note="the policy was read at run time, not baked in",
                ),
            ],
        ),
    ],
)


EXPENSE_CASES = [
    EvalCase(
        id="expense-in-policy-confirmed",
        spec_id=EXPENSE_SPEC.id,
        name="A clean in-policy receipt, confirmed by the person who filed it",
        summary=(
            "Nothing is flagged and it is still somebody's to say. The reading "
            "the policy checks cleared is a model's reading of a photograph, "
            "and the only person who can vouch for the total is the one who "
            "was holding the paper — so one gate, on their own desk, and the "
            "lines stay `origin: model` because the confirmation agreed with "
            "them rather than replacing them."
        ),
        fixture_id="expense/felicia-on-the-roster",
        trigger={
            "employee": "felicia.marks@naviant.com",
            "commentary": "Team lunch after the Acme Phase 1 review.",
            "hint": "total=64.20 category=meals vendor=corner-bistro",
        },
        decisions=[ScriptedDecision(
            verdict="approved", decided_by="felicia.marks@naviant.com",
        )],
        expectations=[MilestoneExpectation(
            milestone="recorded",
            objects=[
                ExpectedObject(
                    type="ExpenseRequest", key="$run",
                    fields={
                        "requested_usd": 64.20,
                        "approved_usd": 64.20,
                        "state": "approved",
                        "employee_email": "felicia.marks@naviant.com",
                    },
                    link_counts={"has_expense_line": 1, "decided_by": 1},
                ),
                ExpectedObject(
                    type="ExpenseLine", key="",
                    fields={
                        "requested_usd": 64.20,
                        "category": "meals",
                        "origin": "model",
                        "purpose": "",
                    },
                ),
            ],
            trajectory={
                "terminal_status": "succeeded",
                "gate_count": "1",
                "queue": "person-felicia-marks-9d11",
            },
        )],
    ),
    EvalCase(
        id="expense-submitter-claims-part-of-a-receipt",
        spec_id=EXPENSE_SPEC.id,
        name="The submitter claims $75 of a $110 receipt, and that stands",
        summary=(
            "The memo is an input, not a note filed beside one — it lowers "
            "what the line asks for, and it is never given to the extractor. "
            "Claiming less than the paper raises no finding; the two amounts "
            "are kept apart so the difference stays legible."
        ),
        fixture_id="expense/felicia-on-the-roster",
        trigger={
            "employee": "felicia.marks@naviant.com",
            "commentary": "Client dinner, Acme Phase 2.",
            "documents": [{
                "filename": "dinner.jpg",
                "hint": "total=110.00 category=meals vendor=corner-bistro",
                "memo": (
                    "Dinner with the Acme team after the Phase 2 review. I am "
                    "only requesting $75 of the $110 — I ordered wine on my "
                    "own tab."
                ),
            }],
        },
        decisions=[ScriptedDecision(
            verdict="approved", decided_by="felicia.marks@naviant.com",
        )],
        expectations=[MilestoneExpectation(
            milestone="recorded",
            objects=[
                ExpectedObject(
                    type="ExpenseRequest", key="$run",
                    fields={"requested_usd": 75.00, "state": "approved"},
                ),
                ExpectedObject(
                    type="ExpenseLine", key="$run#1",
                    fields={
                        "requested_usd": 75.00,
                        "document_total_usd": 110.00,
                        "origin": "model",
                    },
                ),
            ],
            trajectory={"terminal_status": "succeeded", "gate_count": "1"},
        )],
    ),
    EvalCase(
        id="expense-compound-receipt-corrected",
        spec_id=EXPENSE_SPEC.id,
        name="A compound receipt becomes two lines the submitter can trim",
        summary=(
            "Two facts out of one document, then a correction that re-runs the "
            "checks and asks again — because a corrected set of facts is a new "
            "set of facts and nobody has vouched for it yet. `origin: human` "
            "on both lines is what proves the submitter's values were applied "
            "rather than logged."
        ),
        fixture_id="expense/john-on-the-roster",
        trigger={
            "employee": "john.smith@naviant.com",
            "commentary": "Two nights at the Acme onsite.",
            "hint": "compound total=210.00 category=lodging vendor=harbor-hotel",
        },
        decisions=[
            ScriptedDecision(
                verdict="corrected", decided_by="john.smith@naviant.com",
                note="Trimmed the parking line to the reimbursable rate.",
                payload={"lines": [
                    {"ordinal": 1, "purpose": "Two nights at the Acme onsite."},
                    {"ordinal": 2, "requested_usd": 60.0, "category": "travel",
                     "purpose": "Parking at the onsite, capped rate."},
                ]},
            ),
            ScriptedDecision(
                verdict="approved", decided_by="john.smith@naviant.com",
            ),
        ],
        expectations=[MilestoneExpectation(
            milestone="recorded",
            objects=[
                ExpectedObject(
                    type="ExpenseRequest", key="$run",
                    fields={"requested_usd": 186.00, "state": "approved"},
                    link_counts={"has_expense_line": 2, "decided_by": 1},
                ),
                ExpectedObject(
                    type="ExpenseLine", key="$run#2",
                    fields={
                        "requested_usd": 60.00,
                        "category": "travel",
                        "origin": "human",
                    },
                ),
            ],
            trajectory={"terminal_status": "succeeded", "gate_count": "2"},
        )],
    ),
    EvalCase(
        id="expense-restricted-category-denied",
        spec_id=EXPENSE_SPEC.id,
        name="A restricted category is always flagged, and can be withdrawn",
        summary=(
            "The finding annotates the confirmation rather than re-routing it: "
            "there is no amount and no category at which somebody other than "
            "the submitter can say what a receipt says. Nathan reads the flag "
            "and withdraws the request himself."
        ),
        fixture_id="expense/nathan-on-the-roster",
        trigger={
            "employee": "nathan.pryce@naviant.com",
            "commentary": "Client gift wine for the Acme close.",
            "hint": "total=96.40 category=alcohol vendor=vintners-cellar",
        },
        decisions=[ScriptedDecision(
            verdict="rejected", decided_by="nathan.pryce@naviant.com",
            note="Out of policy §4.2 — refiling with the gifting desk.",
        )],
        expectations=[MilestoneExpectation(
            milestone="recorded",
            objects=[ExpectedObject(
                type="ExpenseRequest", key="$run",
                fields={"state": "denied", "approved_usd": 0.0},
            )],
            trajectory={
                "terminal_status": "succeeded",
                "gate_count": "1",
                # His own desk, per the governed routing table's `submitter`.
                # The slug is a pure function of the address, which is what
                # makes it something a fixture can name.
                "queue": "person-nathan-pryce-82d6",
            },
        )],
    ),
]


# ---------------------------------------------------------------------------
# Identity sweep — the roster's answer key, graded
# ---------------------------------------------------------------------------
#
# The estate's namespace design ships an **answer key**: which records across
# the CRM, the ERP, Office and the DMS are the same real-world entity, and —
# the half that is easy to forget and expensive to get wrong — which pairs are
# designed to *look* like one and are not. The key is deliberately never stored
# in any app; it is ground truth held on the Cumulus side. This is where it
# stops being a design document and becomes a promise something checks.
#
# The rows below are recorded from the live estate, field for field, in the
# shape its feeds deliver. Not invented: `Northwind Trading Co.` really does
# carry `(312) 555-0100` and no email, `Apex Mfg. Services LLC` really has a
# phone in the 815 and no email at all, and those absences are what the
# scoring turns on. A fixture that tidied them up would grade a world the
# sweep will never meet.
#
# What each row is doing here:
#
# - **Northwind** — the ERP's 2019 stale duplicate under an old trading name.
#   The one the per-source resolver *correctly* mints as a second Company
#   (0.64 against the book, below the review floor) and only a pass over the
#   book can catch.
# - **Verona Foods / Verona Foods Group** — a CRM duplicate in one delivery.
#   Both mint, because a feed scores its rows against the book as it stood
#   before the batch; the sweep is the only thing that sees them together.
# - **Tom Berg / Thomas Berg** — the re-typed contact. Same account, same
#   surname, one has no phone. Two Persons by design, because people key on an
#   address.
# - **Cedarline ×2** — the duplicate normalization already catches. It is here
#   as a must-link that must *not* become a proposal: the answer is one Vendor
#   carrying both ERP rows, and a sweep proposing a merge on top of that would
#   be proposing to merge something with itself.
# - **Summit Healthcare Staffing / Apex Mfg. Services** — the two bait pairs.
#   A Rockford staffing agency that is not the Madison hospital system, and a
#   Rockford refurbisher that is not the Milwaukee manufacturer. The traps are
#   three deep on the Apex side: Naviant's own mail abbreviates the customer to
#   "Apex Mfg.", which is the vendor's legal name.
# - **Sauk Prairie / Sugar River school districts** and **Kestrel / Harbor
#   District credit unions** — ambient confusability the roster documents as
#   *not* designed pairs. They share two words of three and resolve instantly
#   on domain and area code. They are here because a suite that only tested
#   the designed traps would not notice a scorer that had started proposing
#   every pair of school districts in Wisconsin.

_IDENTITY_CRM = {
    "source": "crm-book",
    "watermark": "2026-08-30T00:00:00Z",
    "accounts": [
        {"external_id": "001e7ed1a4e7135", "name": "Northwind Traders",
         "industry": "Wholesale Distribution",
         "website": "https://northwindtraders.example.com",
         "phone": "(312) 555-0134"},
        {"external_id": "001351b23a6e8d3", "name": "Verona Foods Group",
         "industry": "Food Processing",
         "website": "https://veronafoods.example.com",
         "phone": "(608) 555-0184"},
        # The badge-list import: no website, no phone, no contacts.
        {"external_id": "001e3b0a58dac4f", "name": "Verona Foods"},
        {"external_id": "001b5cfd3d20648", "name": "Summit Health Partners",
         "industry": "Healthcare",
         "website": "https://summithealth.example.com",
         "phone": "(608) 555-0117"},
        {"external_id": "00166bfaec768cf", "name": "Apex Manufacturing",
         "industry": "Industrial Manufacturing",
         "website": "https://apexmfg.example.com", "phone": "(414) 555-0188"},
        {"external_id": "001f0046335b3b9", "name": "Sauk Prairie School District",
         "website": "https://saukprairieschools.example.com",
         "phone": "(608) 555-0125"},
        {"external_id": "001c3d9e0355efd", "name": "Sugar River School District",
         "website": "https://sugarriverschools.example.com",
         "phone": "(608) 555-0165"},
        {"external_id": "001756b5cf0c82f", "name": "Kestrel Credit Union",
         "website": "https://kestrelcu.example.com", "phone": "(608) 555-0192"},
        {"external_id": "001b64ade4e7b5f", "name": "Harbor District Credit Union",
         "website": "https://harbordistrictcu.example.com",
         "phone": "(414) 555-0162"},
    ],
    "contacts": [
        {"external_id": "003a1b2c3d4e5f6", "first_name": "Tom",
         "last_name": "Berg", "title": "Purchasing Manager",
         "email": "tom.berg@northwindtraders.example.com",
         "phone": "(312) 555-0135",
         "account_external_id": "001e7ed1a4e7135"},
        # The duplicate: re-entered from a trade-show list, no phone.
        {"external_id": "003b2c3d4e5f6a7", "first_name": "Thomas",
         "last_name": "Berg", "title": "Purchasing",
         "email": "thomas.berg@northwindtraders.example.com",
         "account_external_id": "001e7ed1a4e7135"},
        # A colleague, not a duplicate. The control that keeps "same company"
        # from being enough on its own.
        {"external_id": "003c3d4e5f6a7b8", "first_name": "Maria",
         "last_name": "Delgado", "title": "Operations Director",
         "email": "maria.delgado@northwindtraders.example.com",
         "phone": "(312) 555-0137",
         "account_external_id": "001e7ed1a4e7135"},
    ],
}

_IDENTITY_ERP_SALES = {
    "source": "erp-sales",
    "watermark": "2026-08-30T00:00:00Z",
    "customers": [
        {"external_id": "1000", "name": "Northwind Traders Inc.",
         "email": "ap@northwindtraders.example.com", "phone": "(312) 555-0134",
         "payment_terms": "Net 30"},
        {"external_id": "1016", "name": "Northwind Trading Co.",
         "phone": "(312) 555-0100", "payment_terms": "Net 60"},
        {"external_id": "1003", "name": "Summit Health Partners",
         "email": "ap@summithealth.example.com", "phone": "(608) 555-0117",
         "payment_terms": "Net 45"},
        {"external_id": "1001", "name": "Apex Manufacturing Co.",
         "email": "accounts.payable@apexmfg.example.com",
         "phone": "(414) 555-0188", "payment_terms": "Net 60"},
    ],
}

_IDENTITY_ERP_VENDORS = {
    "source": "erp-vendors",
    "watermark": "2026-08-30T00:00:00Z",
    "vendors": [
        {"external_id": "2003", "name": "Cedarline Office Systems",
         "email": "service@cedarlineoffice.example.com",
         "phone": "(608) 555-0163", "payment_terms": "Due on receipt"},
        {"external_id": "2011", "name": "Cedarline Office Systems, Inc.",
         "email": "billing@cedarlineoffice.example.com",
         "payment_terms": "Net 30"},
        {"external_id": "2012", "name": "Summit Healthcare Staffing LLC",
         "email": "billing@summitstaffing.example.com",
         "phone": "(815) 555-0144"},
        {"external_id": "2013", "name": "Apex Mfg. Services LLC",
         "phone": "(815) 555-0166", "payment_terms": "Net 30"},
        # The staffing vendor Naviant really uses, so that a staffing company
        # in the vendor master looks unremarkable — which is what keeps the
        # Summit bait honest.
        {"external_id": "2006", "name": "Badger Staffing Partners LLC",
         "email": "billing@badgerstaffing.example.com",
         "phone": "(608) 555-0154", "payment_terms": "Net 15"},
    ],
}


#: The delivery system's client list, and the fifth axis of drift the roster
#: designs: not a legal name, not a trade name, but whatever a coordinator
#: typed after a phone call. Five of the thirteen live clients — the ones with
#: a counterpart in the rows above, so the suite grades a join rather than an
#: absence.
#:
#: Clients only, with no projects and no hours: this fixture grades the
#: identity sweep, and what the sweep needs from the PSA is its *spellings*.
#: The delivery book those clients hang off is exercised in
#: `test_sync_psa.py`, against the same recording.
#:
#: What each row is doing here:
#:
#: - **`Apex Mfg`** — the third floor of the Apex trap. The abbreviation the
#:   coordinator typed for the Milwaukee customer *is* the Rockford vendor's
#:   legal name, and unlike the CRM's `Apex Manufacturing` this row carries no
#:   phone number, so the area codes that separate the two cannot be compared.
#:   What it does carry is its contact's address, and that domain is the
#:   evidence the join is made on and the vendor is refused on.
#: - **`Kestrel CU`** — the cross-spelling that must be *found*: a name a
#:   resolver will not join (0.51) on a domain it cannot argue with. Its
#:   neighbour `Harbor District Credit Union` is the control.
#: - **`Northwind`** and **`Summit Health`** — the two that never mint. Both
#:   land in the resolver's review band and wait for a steward, which is the
#:   other half of what an informal client list does and the reason the book
#:   does not simply grow a Company per nickname.
#: - **`Prairie Records`** — the one with no contact address at all. It mints
#:   on a name and holds nothing else, and it is here because Sauk Prairie
#:   School District is one word away from it.
_IDENTITY_PSA = {
    "source": "psa-book",
    "watermark": "2026-08-30T00:00:00Z",
    "psa_clients": [
        {"external_id": "101", "name": "Apex Mfg",
         "contact_name": "Susan Kowalski",
         "contact_email": "susan.kowalski@apexmfg.example.com"},
        {"external_id": "105", "name": "Kestrel CU",
         "contact_name": "Angela Marsh",
         "contact_email": "angela.marsh@kestrelcu.example.com"},
        {"external_id": "100", "name": "Northwind",
         "contact_name": "Maria Delgado",
         "contact_email": "maria.delgado@northwindtraders.example.com"},
        {"external_id": "103", "name": "Summit Health",
         "contact_name": "Evan Walsh",
         "contact_email": "evan.walsh@summithealth.example.com"},
        # Nobody filled the field in.
        {"external_id": "112", "name": "Prairie Records",
         "contact_name": "Vic Malloy"},
    ],
}


def _identity_world() -> Fixture:
    return Fixture(
        id="identity/naviant-answer-key",
        flow_id="identity-sweep",
        name="The estate's answer key, as its feeds delivered it",
        summary=(
            "Nine CRM accounts, three contacts, four ERP customers, five ERP "
            "vendors and five PSA clients, recorded from the live estate. "
            "Holds every duplicate the namespace design plants across three "
            "books, both bait pairs — the Apex one three deep, once the "
            "delivery system's abbreviation is in the world — and two pairs of "
            "merely-similar neighbours, so a suite can fail for proposing too "
            "little and for proposing too much."
        ),
        deliveries=[
            _IDENTITY_CRM, _IDENTITY_ERP_SALES, _IDENTITY_ERP_VENDORS,
            _IDENTITY_PSA,
        ],
        origin="declared",
        derived_from=DECLARED_IN,
        authored_by="platform-eng",
        read_set=["knowledge:entity-resolution-policy@rev1"],
    )


_PROPOSAL_PROPS = [
    FieldRule(
        field="confidence", match="at_least",
        note="a floor, not a value: the weights are governed knowledge and "
             "may be tuned, but a proposal below the floor is the wrong answer",
    ),
    FieldRule(
        field="status", match="exact",
        note="`in_review` on every path. A proposal the sweep wrote as "
             "anything else would be a merge nobody authorized",
    ),
    FieldRule(field="method", match="one_of", options=["exact", "fuzzy"]),
]

IDENTITY_SPEC = EvalSpec(
    id="identity-sweep",
    flow_id="identity-sweep",
    version=1,
    summary=(
        "What the cross-source identity sweep is answerable for: the merges "
        "the estate's answer key says exist, none of the ones it says do not, "
        "and nothing merged either way. Every assertion here is a link "
        "assertion — the sweep writes no claim on any object, so a case that "
        "graded an object's fields would be grading somebody else's flow."
    ),
    milestones=[
        Milestone(
            id="proposed",
            label="once the sweep has committed its proposals",
            anchor="terminal",
            note=(
                "Terminal because a windowed pass has no gate to anchor on: "
                "it raises Tasks without suspending, so the only point where "
                "its proposals are all on the graph is the end."
            ),
            objects=[
                ObjectRule(
                    type="Company",
                    links=[
                        LinkRule(
                            type="company_same_as", direction="out",
                            expect="count", props=_PROPOSAL_PROPS,
                            note="counted rather than asserted to exist, so "
                                 "one rule can say 'this one has a proposal' "
                                 "and 'this one must not'",
                        ),
                        LinkRule(
                            type="company_same_as_vendor", direction="out",
                            expect="count",
                            note="the bait pairs, from the sell side",
                        ),
                    ],
                ),
                ObjectRule(
                    type="Vendor",
                    links=[
                        LinkRule(
                            type="company_same_as_vendor", direction="in",
                            expect="count",
                            note="a bait vendor must carry no inbound "
                                 "proposal from the counterparty book",
                        ),
                        LinkRule(type="vendor_same_as", direction="out",
                                 expect="count"),
                        LinkRule(
                            type="identified_as", direction="out",
                            expect="count",
                            note="how many source rows resolved onto this one "
                                 "vendor — the shape a duplicate the resolver "
                                 "already caught takes",
                        ),
                    ],
                ),
                ObjectRule(
                    type="Person",
                    links=[
                        LinkRule(type="person_same_as", direction="out",
                                 expect="count", props=_PROPOSAL_PROPS),
                    ],
                ),
            ],
            trajectory=[
                TrajectoryRule(kind="terminal_status"),
                TrajectoryRule(
                    kind="read", expected="knowledge:entity-resolution-policy",
                    note="the thresholds are governed knowledge and the run "
                         "must show it read them, or the numbers below are a "
                         "constant wearing a policy's name",
                ),
                TrajectoryRule(
                    kind="model_calls_at_most", expected="0",
                    note="rules and evidence. A model call here would buy "
                         "non-determinism and an unexplainable number in "
                         "place of a sentence a steward can argue with",
                ),
                TrajectoryRule(kind="wrote"),
            ],
        ),
    ],
)

_IN_REVIEW = "in_review"

IDENTITY_CASES = [
    EvalCase(
        id="identity/northwind-stale-duplicate",
        spec_id=IDENTITY_SPEC.id,
        name="The ERP's 2019 Northwind is proposed against today's",
        summary=(
            "`Northwind Trading Co.` and `Northwind Traders` share no domain "
            "(the stale row has no email at all) and no phone number. What "
            "joins them is the anchor word, the 312 they are both in, and a "
            "name that is close without being close enough for the resolver — "
            "which is precisely why the per-source path minted a second "
            "company and was right to."
        ),
        fixture_id="identity/naviant-answer-key",
        expectations=[MilestoneExpectation(
            milestone="proposed",
            objects=[
                ExpectedObject(
                    type="Company", key="Northwind Traders",
                    link_counts={"company_same_as": 1,
                                 "company_same_as_vendor": 0},
                    link_props={
                        "company_same_as.confidence": 0.70,
                        "company_same_as.status": _IN_REVIEW,
                        "company_same_as.method": "fuzzy",
                    },
                ),
            ],
            trajectory={
                "terminal_status": "succeeded",
                "wrote": "link:company_same_as/",
            },
        )],
    ),
    EvalCase(
        id="identity/berg-duplicate-contact",
        spec_id=IDENTITY_SPEC.id,
        name="Tom and Thomas Berg are proposed; their colleague is not",
        summary=(
            "The pair the CRM feed deliberately refuses to merge, because it "
            "keys people on an address. Maria Delgado is in the case for the "
            "reason the whole suite is: a scorer that proposed everyone at "
            "Northwind to everyone else at Northwind would pass a case that "
            "only looked at the Bergs."
        ),
        fixture_id="identity/naviant-answer-key",
        expectations=[MilestoneExpectation(
            milestone="proposed",
            objects=[
                ExpectedObject(
                    type="Person", key="thomas.berg@northwindtraders.example.com",
                    link_counts={"person_same_as": 1},
                    link_props={
                        "person_same_as.confidence": 0.85,
                        "person_same_as.status": _IN_REVIEW,
                        "person_same_as.method": "fuzzy",
                    },
                ),
                ExpectedObject(
                    type="Person", key="maria.delgado@northwindtraders.example.com",
                    link_counts={"person_same_as": 0},
                ),
            ],
            trajectory={
                "terminal_status": "succeeded",
                "wrote": "link:person_same_as/",
            },
        )],
    ),
    EvalCase(
        id="identity/cedarline-one-vendor",
        spec_id=IDENTITY_SPEC.id,
        name="Cedarline is one vendor with two ERP rows, not a proposal",
        summary=(
            "A must-link the sweep must not touch. `Cedarline Office Systems` "
            "and `Cedarline Office Systems, Inc.` normalize to one key, so the "
            "resolver linked them at landing and the answer is a single Vendor "
            "carrying both source rows. A `vendor_same_as` proposal on top of "
            "that would be the sweep offering to merge something with itself, "
            "which is the failure mode a scorer that ignored what resolution "
            "already did would produce."
        ),
        fixture_id="identity/naviant-answer-key",
        expectations=[MilestoneExpectation(
            milestone="proposed",
            objects=[ExpectedObject(
                type="Vendor", key="Cedarline Office Systems",
                link_counts={
                    "identified_as": 2,
                    "vendor_same_as": 0,
                    "company_same_as_vendor": 0,
                },
            )],
            trajectory={"terminal_status": "succeeded"},
        )],
    ),
    EvalCase(
        id="identity/bait-pairs-are-not-proposed",
        spec_id=IDENTITY_SPEC.id,
        name="Neither bait pair is proposed — and the real duplicate still is",
        summary=(
            "Summit Healthcare Staffing is a Rockford temp agency, not the "
            "Madison hospital system; Apex Mfg. Services is a Rockford "
            "refurbisher, not the Milwaukee manufacturer whose own abbreviated "
            "name it happens to be — nor the delivery system's client `Apex "
            "Mfg`, which is that abbreviation typed out. Different domains, "
            "different area codes, opposite sides of the ledger, and in the "
            "PSA's case nothing comparable at all. Verona Foods and the two "
            "school districts are in the same case on purpose: a must-not-link "
            "suite that a sweep proposing nothing at all would pass is not "
            "testing anything, so this case fails both ways round."
        ),
        fixture_id="identity/naviant-answer-key",
        expectations=[MilestoneExpectation(
            milestone="proposed",
            objects=[
                ExpectedObject(
                    type="Vendor", key="Summit Healthcare Staffing LLC",
                    link_counts={"company_same_as_vendor": 0,
                                 "vendor_same_as": 0},
                ),
                ExpectedObject(
                    type="Vendor", key="Apex Mfg. Services LLC",
                    link_counts={"company_same_as_vendor": 0,
                                 "vendor_same_as": 0},
                ),
                ExpectedObject(
                    type="Company", key="Sauk Prairie School District",
                    link_counts={"company_same_as": 0},
                ),
                ExpectedObject(
                    type="Company", key="Harbor District Credit Union",
                    link_counts={"company_same_as": 0},
                ),
                # The third floor of the Apex trap, from the side that built
                # it. The delivery system's abbreviation for the Milwaukee
                # customer *is* the Rockford vendor's legal name, and unlike
                # the CRM's spelling this one carries no phone number — so the
                # area codes that separate the two cannot be compared and the
                # names agree at 0.78. What refuses it is that neither record
                # can be reached the way the other can.
                ExpectedObject(
                    type="Company", key="Apex Mfg",
                    link_counts={"company_same_as_vendor": 0},
                ),
                # A nickname that holds nothing but a name, one word from a
                # school district. Minted, and proposed against nothing.
                ExpectedObject(
                    type="Company", key="Prairie Records",
                    link_counts={"company_same_as": 0},
                ),
                # The discriminator: the sweep is not passing by silence.
                ExpectedObject(
                    type="Company", key="Verona Foods",
                    link_counts={"company_same_as": 1},
                    link_props={
                        "company_same_as.confidence": 0.80,
                        "company_same_as.status": _IN_REVIEW,
                    },
                ),
            ],
            trajectory={
                "terminal_status": "succeeded",
                "model_calls_at_most": "0",
                "read": "knowledge:entity-resolution-policy",
            },
        )],
    ),
    EvalCase(
        id="identity/psa-nickname-joins-the-book",
        spec_id=IDENTITY_SPEC.id,
        name="A coordinator's shorthand is joined to the company it means",
        summary=(
            "The delivery system contributes a fifth axis of drift and the "
            "weakest names in the estate: `Kestrel CU` scores 0.51 against "
            "`Kestrel Credit Union` and `Apex Mfg` 0.54 against `Apex "
            "Manufacturing`, both far below the floor where a resolver would "
            "assert anything, so both mint their own Company. What joins them "
            "is the one hard identifier an informal client list carries — the "
            "domain out of the contact's address — and the proposals come back "
            "`exact` because a shared domain is a fact rather than a "
            "resemblance. This is the half of the PSA feed that the bait case "
            "is the other half of: the same evidence that finds these two has "
            "to refuse the vendor."
        ),
        fixture_id="identity/naviant-answer-key",
        expectations=[MilestoneExpectation(
            milestone="proposed",
            objects=[
                ExpectedObject(
                    type="Company", key="Kestrel CU",
                    link_counts={"company_same_as": 1},
                    link_props={
                        "company_same_as.confidence": 0.90,
                        "company_same_as.status": _IN_REVIEW,
                        "company_same_as.method": "exact",
                    },
                ),
                # The same pair, read from the other end — a proposal between
                # two peers is directed by natural key and not by which row is
                # newer, because which one survives a merge is the steward's
                # call and encoding a guess about it in the edge would be the
                # sweep quietly making it. `Apex Manufacturing` sorts before
                # `Apex Mfg`, so the outbound edge is here.
                ExpectedObject(
                    type="Company", key="Apex Manufacturing",
                    link_counts={"company_same_as": 1},
                    link_props={
                        "company_same_as.confidence": 0.90,
                        "company_same_as.status": _IN_REVIEW,
                        "company_same_as.method": "exact",
                    },
                ),
            ],
            trajectory={
                "terminal_status": "succeeded",
                "wrote": "link:company_same_as/",
            },
        )],
    ),
]


# ---------------------------------------------------------------------------
# The state plane — one world, two flows, and the live estate's own rows
# ---------------------------------------------------------------------------
#
# The disagreement sweep and the release that answers it are graded against one
# recording, because they are two halves of one story: a deal the CRM says was
# won, an order the ERP has not released, a controller who decides, and a PATCH.
# Two fixtures would have been two accounts of the same three books, free to
# drift the moment somebody edited one.
#
# These rows are the live estate's, field for field — the same Voss and Blue
# Harbor numbers the workplan's ground-truth section verified against the
# running book. `server/tests/test_disagreement_sweep.py` reads them back from
# here rather than declaring its own, so the unit tests and the graded suite
# cannot disagree about the world they are describing.
#
# What each row is doing:
#
# - **Voss's August win against SO5037** — the one live instance of the class.
#   Closed won on the 14th, ordered on the 17th, still `Pending Approval`.
# - **Voss's two older wins** — March and last November, each with its own order
#   long since billed. The nearest false positive in the estate: same
#   counterparty, same two states, names that share words with the order's memo.
#   The window is the only thing separating them from the finding.
# - **SO5025** — released, and therefore not a disagreement however well it
#   matches: its memo is the March deal's name almost verbatim.
# - **Blue Harbor's SO5003** — a held order with no won deal behind it at all.
#   One-ended, so there is no edge to write, and the honest output is a count.
# - **The PSA's `Voss Auto`** — the delivery system's nickname for the same
#   dealer group, which mints its own Company and sits behind a `company_same_as`
#   no steward has confirmed. Its engagement `ENG-0105` is the third system's
#   account of this exact commitment, and it is inadmissible until somebody says
#   the two companies are one. `ENG-0115` is long finished and outside every
#   window, so that "the customer has projects" cannot be what fires the signal.

_STATE_CRM = {
    "source": "crm-book",
    "watermark": "2026-08-30T00:00:00Z",
    "accounts": [
        {"external_id": "A1", "name": "Voss Automotive Group",
         "website": "https://vossauto.example.com", "phone": "(608) 555-0111"},
        {"external_id": "A2", "name": "Blue Harbor Logistics",
         "website": "https://blueharbor.example.com",
         "phone": "(414) 555-0122"},
    ],
    "opportunities": [
        {"external_id": "O1", "name": "Voss — Title & Service Records Capture",
         "account_external_id": "A1", "stage": "closed-won", "amount": 128000.0,
         "close_date": "2026-08-14"},
        {"external_id": "O2", "name": "Voss — Service Drive Scanner Rollout",
         "account_external_id": "A1", "stage": "closed-won",
         "amount": 44000.0, "close_date": "2026-03-20"},
        {"external_id": "O3", "name": "Voss — Deal Jacket Capture",
         "account_external_id": "A1", "stage": "closed-won",
         "amount": 61000.0, "close_date": "2025-11-14"},
        # Never won. The control that separates "no deal" from "no deal in the
        # window", which are different facts about an estate.
        {"external_id": "O4", "name": "Blue Harbor — Fleet Telematics Rollout",
         "account_external_id": "A2", "stage": "prospecting",
         "amount": 95000.0, "close_date": "2026-12-01"},
    ],
}

_STATE_ERP = {
    "source": "erp-sales",
    "watermark": "2026-08-30T00:00:00Z",
    "customers": [
        {"external_id": "1007", "name": "Voss Automotive Group",
         "email": "ap@vossauto.example.com", "phone": "(608) 555-0111"},
        {"external_id": "1011", "name": "Blue Harbor Logistics",
         "email": "ap@blueharbor.example.com", "phone": "(414) 555-0122"},
    ],
    "sales_orders": [
        {"external_id": "5037", "order_number": "SO5037",
         "customer_external_id": "1007", "ordered_date": "2026-08-17",
         "status": "Pending Approval", "total": 34960.0,
         "memo": "Title and service records capture — phase 1"},
        {"external_id": "5003", "order_number": "SO5003",
         "customer_external_id": "1011", "ordered_date": "2026-08-11",
         "status": "Pending Approval", "total": 6900.0,
         "memo": "Dock scanning refresh"},
        {"external_id": "5025", "order_number": "SO5025",
         "customer_external_id": "1007", "ordered_date": "2026-03-18",
         "status": "Billed", "total": 8850.0,
         "memo": "Service drive scanner rollout"},
    ],
}

_STATE_PSA = {
    "source": "psa-book",
    "watermark": "2026-08-30T00:00:00Z",
    "psa_clients": [
        {"external_id": "107", "name": "Voss Auto",
         "contact_name": "Dana Reyes",
         "contact_email": "dana.reyes@vossauto.example.com"},
    ],
    "projects": [
        {"external_id": "105", "project_code": "ENG-0105",
         "client_external_id": "107",
         "name": "Voss Auto — title and service records capture",
         "status": "on-hold", "project_manager": "S. Hoffmann",
         "start_date": "2026-08-22", "target_end_date": "2026-11-18",
         "budget_hours": 260.0, "actual_hours": 12.0},
        {"external_id": "115", "project_code": "ENG-0115",
         "client_external_id": "107",
         "name": "Voss Auto — deal jacket capture install",
         "status": "completed", "project_manager": "Danielle G.",
         "start_date": "2025-11-23", "target_end_date": "2025-12-23",
         "budget_hours": 110.0},
    ],
}

#: The order the whole story is about, by the two names it has. `SO5037` is the
#: transaction number every human uses and `5037` is the id the PATCH URL keys
#: on; `SalesOrder.external_id` is the only thing that maps between them.
VOSS_ORDER = "SO5037"
VOSS_RECORD = "5037"
VOSS_DEAL = "Voss — Title & Service Records Capture"
#: An Opportunity's natural key is the CRM's own id namespaced by the book it
#: came out of — the *source*'s key for the row, which is what a natural key is
#: when the row has no human-readable one. The name every person uses is the
#: label, and it is on the ExpectedObject beside each key below so a reader is
#: never asked to remember which deal `O2` was.
VOSS_DEAL_KEY = "crm-book:O1"
OLDER_WIN_KEYS = ("crm-book:O2", "crm-book:O3")
OLDER_WINS = ("Voss — Service Drive Scanner Rollout", "Voss — Deal Jacket Capture")
BLUE_HARBOR_ORDER = "SO5003"
#: The counterparty as the PSA spells it, which is what a case names when it
#: says a steward has confirmed the merge.
VOSS_NICKNAME = "Voss Auto"

_STATE_FIXTURE = "state/naviant-disagreement"
_STATE_READ_SET = ["knowledge:state-agreement-policy@rev1"]


def _state_world() -> Fixture:
    return Fixture(
        id=_STATE_FIXTURE,
        # The world two flows run in, so it belongs to neither exclusively —
        # named for the sweep that reads it, because that is the flow whose
        # precondition it *is*.
        flow_id="disagreement-sweep",
        name="Voss, Blue Harbor, and one order nobody released",
        summary=(
            "Two CRM accounts and four opportunities, two ERP customers and "
            "three sales orders, and the delivery system's nickname for one of "
            "them — recorded from the live estate. Holds the class's one real "
            "instance, the two older wins that are the nearest false positive "
            "in the book, a held order with no won deal behind it, a released "
            "order whose memo matches better than the finding's, and a third "
            "system's evidence sitting behind an unconfirmed merge."
        ),
        deliveries=[_STATE_CRM, _STATE_ERP, _STATE_PSA],
        origin="declared",
        derived_from=DECLARED_IN,
        authored_by="platform-eng",
        read_set=_STATE_READ_SET,
    )


#: The finding, as a spec grades it. Confidence is a floor for
#: `_PROPOSAL_PROPS`' reason — the weights are governed and may be tuned — and
#: a case that wants to say the number went *down* pairs it with a ceiling.
_FINDING_PROPS = [
    FieldRule(
        field="correlation_confidence", match="at_least",
        note="a floor: the blend is governed knowledge, and a finding below "
             "the floor is the wrong answer however it was scored",
    ),
    FieldRule(
        field="status", match="exact",
        note="`standing` is the only status this flow writes. The other three "
             "are moved by the order desk's decision and by a later pass",
    ),
    FieldRule(field="class", match="exact"),
]


DISAGREEMENT_SPEC = EvalSpec(
    id="disagreement-sweep",
    flow_id="disagreement-sweep",
    version=1,
    summary=(
        "What the cross-system disagreement sweep is answerable for: the one "
        "disagreement the two books really hold, none of the pairs that only "
        "look like it, and nothing touched at either end. Every assertion is "
        "about an edge or about a number the pass reports — the sweep writes "
        "no claim on any object, and a case that graded one would be grading "
        "somebody else's flow. The counts are graded because half of what this "
        "pass is worth is what it *declined* to raise and why."
    ),
    milestones=[
        Milestone(
            id="found",
            label="once the pass has recorded its findings",
            anchor="terminal",
            note=(
                "Terminal because a windowed pass has no gate to anchor on: it "
                "raises Tasks without suspending, so the only point where its "
                "findings are all on the graph is the end."
            ),
            objects=[
                ObjectRule(
                    type="SalesOrder",
                    fields=[FieldRule(field="status", match="exact")],
                    links=[
                        LinkRule(
                            type="deal_disagrees_with_order", direction="in",
                            expect="count", props=_FINDING_PROPS,
                            note="graded from the stuck end, which is where "
                                 "the question starts — and counted rather "
                                 "than asserted to exist, so one rule says "
                                 "both 'this order has a finding' and 'this "
                                 "one must not'",
                        ),
                    ],
                ),
                ObjectRule(
                    type="Opportunity",
                    fields=[FieldRule(field="stage", match="exact")],
                    links=[
                        LinkRule(
                            type="deal_disagrees_with_order", direction="out",
                            expect="count",
                            props=[FieldRule(
                                field="correlation_confidence", match="at_most",
                                note="the ceiling, read from the deal's end; "
                                     "the floor is read from the order's. Two "
                                     "bounds on one number, because that is "
                                     "how a case says a piece of evidence was "
                                     "*not* admitted — the delivery "
                                     "engagement is worth 0.08, and a finding "
                                     "that reached the governed ceiling read "
                                     "something this one could not",
                            )],
                            note="the same edge from the deal, for the wins "
                                 "that must carry none",
                        ),
                    ],
                ),
                ObjectRule(
                    type="Company",
                    links=[
                        LinkRule(
                            type="company_same_as", direction="out",
                            expect="count",
                            props=[FieldRule(field="status", match="exact")],
                            note="the identity gate itself: the same world "
                                 "twice, differing by this one status",
                        ),
                    ],
                ),
            ],
            trajectory=[
                TrajectoryRule(kind="terminal_status"),
                TrajectoryRule(
                    kind="read", expected="knowledge:state-agreement-policy",
                    note="the window, the bands and the class table are "
                         "governed knowledge and the run must show it read "
                         "them, or the numbers are constants wearing a "
                         "policy's name",
                ),
                TrajectoryRule(
                    kind="model_calls_at_most", expected="0",
                    note="every signal is a date, a ratio, a state or a name "
                         "similarity, and the blend is addition. A model call "
                         "here would buy non-determinism and an unarguable "
                         "number in place of evidence a controller can read",
                ),
                TrajectoryRule(
                    kind="reported",
                    note="the counts the pass makes and does not write down — "
                         "what it declined to raise, and what an unconfirmed "
                         "merge cost it",
                ),
                TrajectoryRule(kind="task_raised"),
                TrajectoryRule(
                    kind="wrote", expected="link:deal_disagrees_with_order/",
                    note="edges, and only edges",
                ),
            ],
        ),
    ],
)

_STANDING = "standing"
_IN_REVIEW_STATUS = "in_review"
_CONFIRMED_STATUS = "confirmed"

DISAGREEMENT_CASES = [
    EvalCase(
        id="state/voss-won-not-released",
        spec_id=DISAGREEMENT_SPEC.id,
        name="The won deal and the unreleased order are correlated",
        summary=(
            "The class's one live instance, with the evidence a controller "
            "would need to disagree with it. The join is the load-bearing "
            "part: `pursued_with` and `sold_to` land on one Company because "
            "resolution already reconciled the CRM's spelling of Voss with the "
            "ERP's. A name join would have failed on Yahara, Dane County and "
            "Prairie Records, which is why the sweep does not attempt one."
        ),
        fixture_id=_STATE_FIXTURE,
        expectations=[MilestoneExpectation(
            milestone="found",
            objects=[
                ExpectedObject(
                    type="SalesOrder", key=VOSS_ORDER,
                    # Untouched at both ends: a finding is only useful while
                    # the order still holds the state it was found in, and a
                    # sweep that helpfully moved it would destroy the fact it
                    # exists to report.
                    fields={"status": "pending-approval"},
                    link_counts={"deal_disagrees_with_order": 1},
                    link_props={
                        "deal_disagrees_with_order.correlation_confidence": 0.90,
                        "deal_disagrees_with_order.status": _STANDING,
                        "deal_disagrees_with_order.class": "won-not-released",
                    },
                ),
                ExpectedObject(
                    type="Opportunity", key=VOSS_DEAL_KEY,
                    fields={"stage": "closed-won"},
                    link_counts={"deal_disagrees_with_order": 1},
                ),
            ],
            trajectory={
                "terminal_status": "succeeded",
                "wrote": "link:deal_disagrees_with_order/",
                "read": "knowledge:state-agreement-policy",
                "model_calls_at_most": "0",
                "reported": "found=1, recorded=1, standing=1, orders_examined=2",
                "task_raised": "order-desk/state_disagreement",
            },
        )],
    ),
    EvalCase(
        id="state/older-wins-and-blue-harbor-are-not-raised",
        spec_id=DISAGREEMENT_SPEC.id,
        name="The older wins are not linked, and Blue Harbor is only counted",
        summary=(
            "The two ways this pass is wrong if it is wrong. Voss's March and "
            "November wins are the same counterparty in the same two states "
            "with names that share words with the order's memo — the window is "
            "the only thing separating them from the finding, and it is the "
            "rule the estate would embarrass first. Blue Harbor's held order "
            "has no won deal behind it at all: one-ended, so there is no edge "
            "to write, and nobody is paged about it. One Task in the whole "
            "world, and it is the Voss one."
        ),
        fixture_id=_STATE_FIXTURE,
        expectations=[MilestoneExpectation(
            milestone="found",
            objects=[
                # `Voss — Service Drive Scanner Rollout`, closed won in March.
                ExpectedObject(
                    type="Opportunity", key=OLDER_WIN_KEYS[0],
                    fields={"stage": "closed-won"},
                    link_counts={"deal_disagrees_with_order": 0},
                ),
                # `Voss — Deal Jacket Capture`, closed won last November.
                ExpectedObject(
                    type="Opportunity", key=OLDER_WIN_KEYS[1],
                    fields={"stage": "closed-won"},
                    link_counts={"deal_disagrees_with_order": 0},
                ),
                ExpectedObject(
                    type="SalesOrder", key=BLUE_HARBOR_ORDER,
                    fields={"status": "pending-approval"},
                    link_counts={"deal_disagrees_with_order": 0},
                ),
                # Released, and therefore not a disagreement however well it
                # matches: its memo is the March deal's name almost verbatim.
                ExpectedObject(
                    type="SalesOrder", key="SO5025",
                    fields={"status": "billed"},
                    link_counts={"deal_disagrees_with_order": 0},
                ),
            ],
            trajectory={
                "terminal_status": "succeeded",
                # "We looked and they were out of range" and "nobody ever won
                # the deal" are different facts about an estate, and a screen
                # that conflated them would report perfect deal coverage as
                # none.
                "reported": (
                    "outside_window=2, order_without_deal=1, ambiguous=0, "
                    "desk_tasks=1"
                ),
                "model_calls_at_most": "0",
            },
        )],
    ),
    EvalCase(
        id="state/an-unconfirmed-merge-withholds-the-third-system",
        spec_id=DISAGREEMENT_SPEC.id,
        name="A merge nobody confirmed withholds a third system's evidence",
        summary=(
            "Status-gating in the doctrine's sense, and the number that sells "
            "it. The PSA's engagement for this exact commitment — started five "
            "days after the order and immediately parked — hangs off `Voss "
            "Auto`, joined to the counterparty by a `company_same_as` no "
            "steward has confirmed. The Gateway refuses to act on an "
            "unconfirmed proposal and so does this: the record is not read, "
            "the finding stands without it at a confidence below the ceiling, "
            "and the cost of the unfinished identity work is reported rather "
            "than absorbed."
        ),
        fixture_id=_STATE_FIXTURE,
        expectations=[MilestoneExpectation(
            milestone="found",
            objects=[
                ExpectedObject(
                    type="Company", key=VOSS_NICKNAME,
                    link_counts={"company_same_as": 1},
                    link_props={"company_same_as.status": _IN_REVIEW_STATUS},
                ),
                ExpectedObject(
                    type="SalesOrder", key=VOSS_ORDER,
                    link_counts={"deal_disagrees_with_order": 1},
                    link_props={
                        "deal_disagrees_with_order.correlation_confidence": 0.90,
                        "deal_disagrees_with_order.status": _STANDING,
                    },
                ),
                # And from the deal's end, the ceiling — which is where this
                # case says out loud that the engagement was not read: the
                # signal is worth 0.08, and the case below asserts the same
                # pair reaching 0.95 once a steward has confirmed the merge.
                ExpectedObject(
                    type="Opportunity", key=VOSS_DEAL_KEY,
                    fields={"stage": "closed-won"},
                    link_counts={"deal_disagrees_with_order": 1},
                    link_props={
                        "deal_disagrees_with_order.correlation_confidence": 0.94,
                    },
                ),
            ],
            trajectory={
                "terminal_status": "succeeded",
                "reported": "blocked_on_identity=1, found=1",
            },
        )],
    ),
    EvalCase(
        id="state/confirming-the-merge-admits-the-engagement",
        spec_id=DISAGREEMENT_SPEC.id,
        name="Confirming the merge admits the same record on the same pass",
        summary=(
            "The other half, over the same fixture and the same policy. "
            "Nothing about the sweep changes between the two cases — one "
            "link's status does, answered through the Task a steward would "
            "have answered. The engagement becomes admissible, the evidence "
            "appears on the finding, the confidence reaches the governed "
            "ceiling, and nothing is blocked. That is the whole argument for "
            "the identity work, stated as a pair of results rather than as a "
            "paragraph."
        ),
        fixture_id=_STATE_FIXTURE,
        trigger={"confirm": [VOSS_NICKNAME]},
        expectations=[MilestoneExpectation(
            milestone="found",
            objects=[
                ExpectedObject(
                    type="Company", key=VOSS_NICKNAME,
                    link_counts={"company_same_as": 1},
                    link_props={"company_same_as.status": _CONFIRMED_STATUS},
                ),
                ExpectedObject(
                    type="SalesOrder", key=VOSS_ORDER,
                    link_counts={"deal_disagrees_with_order": 1},
                    link_props={
                        "deal_disagrees_with_order.correlation_confidence": 0.95,
                        "deal_disagrees_with_order.status": _STANDING,
                    },
                ),
                # And the older wins are still refused. The identity work
                # widens what may be *read*; it does not loosen a required
                # condition, and a case that only checked the new signal would
                # not have noticed if it had.
                ExpectedObject(
                    type="Opportunity", key=OLDER_WIN_KEYS[0],
                    fields={"stage": "closed-won"},
                    link_counts={"deal_disagrees_with_order": 0},
                ),
            ],
            trajectory={
                "terminal_status": "succeeded",
                "reported": (
                    "blocked_on_identity=0, found=1, top~ENG-0105"
                ),
            },
        )],
    ),
]


# ---------------------------------------------------------------------------
# The release — the only flow that changes a record Cumulus does not own
# ---------------------------------------------------------------------------
#
# Seven branches, one case each (the echo is two, because an echo and a
# divergence are two different things for the world to do). Every one of them
# runs against `estate_fake.FakeErp` — `erp.py`'s picklist, its unknown-field
# 400, its ignored read-only fields, its bumped `updatedAt`, and **no transition
# validation**, because the real one has none. That last absence is the whole
# reason `estate_writes.WRITABLE` exists, and a tidier fake would have hidden it.
#
# What a case can say about the world it runs in is in `eval_world`'s
# `start_order_release`: what the ERP holds by the time the desk answers, how
# the door behaves, whether the approval arrives twice, and what the feed
# carries home afterwards.

RELEASE_SPEC = EvalSpec(
    id="order-release",
    flow_id="order-release",
    version=1,
    summary=(
        "What the outbound write promises. Four things, and each of them on "
        "its own would make it unsafe to point at a customer's ERP if it were "
        "not true: it writes at most once, across a retried step and a "
        "re-delivered approval; the *estate* decides whether the write took, "
        "never the response code; it never claims authority it did not read "
        "back, because a `human:` claim on an order's status would outrank the "
        "ERP forever and freeze the order; and a refusal, a failure and a "
        "stale precondition are three different accusations that reach three "
        "different places."
    ),
    milestones=[
        Milestone(
            id="checked",
            label="once the precondition has re-read the row",
            anchor="first_of_kind",
            selector="resolve",
            note=(
                "The point every case reaches, including the one whose write "
                "fails and suspends the run. Anchored on what the step "
                "*is* rather than on its id, so splitting or renaming the "
                "precondition moves no case."
            ),
            objects=[
                ObjectRule(
                    type="SalesOrder",
                    fields=[FieldRule(field="status", match="exact")],
                ),
            ],
            trajectory=[
                TrajectoryRule(kind="terminal_status"),
                TrajectoryRule(
                    kind="task_raised",
                    note="the desk a failure reaches. A run that suspends "
                         "reports nothing, so the Task is the only account of "
                         "it there is",
                ),
                TrajectoryRule(kind="model_calls_at_most", expected="0"),
            ],
        ),
        Milestone(
            id="settled",
            label="once the run has finished, however it finished",
            anchor="terminal",
            note=(
                "Terminal, and that includes the runs that decided not to "
                "write: withholding a release is an ending, not a failure."
            ),
            objects=[
                ObjectRule(
                    type="SalesOrder",
                    fields=[FieldRule(field="status", match="exact")],
                    links=[
                        LinkRule(
                            type="deal_disagrees_with_order", direction="in",
                            expect="count",
                            props=[
                                FieldRule(field="status", match="exact"),
                                FieldRule(field="resolved_how", match="exact"),
                            ],
                            note="the finding is the case, and its status is "
                                 "what the decision moved",
                        ),
                    ],
                ),
            ],
            trajectory=[
                TrajectoryRule(kind="terminal_status"),
                TrajectoryRule(
                    kind="reported",
                    note="the flow's own account of what it did to somebody "
                         "else's system: whether a PATCH was sent, what the "
                         "journal settled at, and how the finding ended",
                ),
                TrajectoryRule(
                    kind="claimed_by",
                    expected="SalesOrder.status=erp:erp-sales",
                    note="the promise this flow exists to keep. A `human:` "
                         "claim here would outrank `erp:` forever and leave "
                         "the value identical, so only the claims can see it",
                ),
                TrajectoryRule(kind="task_raised"),
            ],
        ),
    ],
)

_APPROVE = ScriptedDecision(
    verdict="approved",
    decided_by="raymond.fitzgerald@naviant.com",
    note="Release it — the deal closed and the paperwork is done.",
)
_RELEASED_STATUS = "pending-fulfillment"
_HELD_STATUS = "pending-approval"

RELEASE_CASES = [
    EvalCase(
        id="release/one-patch-moves-the-order",
        spec_id=RELEASE_SPEC.id,
        name="One PATCH moves the order and closes the finding",
        summary=(
            "The happy path, and the two-regime split that makes it safe. The "
            "release is the decision a controller authorized; the read-back "
            "that follows it is an observation of what the ERP then held, "
            "landed at the sales feed's own authority. "
            "They are two Action steps because they are two different claims "
            "about the world, and collapsing them would put a `human:` claim on "
            "the order's status — which would outrank the ERP forever and make "
            "every future disagreement about this order invisible."
        ),
        fixture_id=_STATE_FIXTURE,
        trigger={"record_id": VOSS_RECORD},
        decisions=[_APPROVE],
        expectations=[MilestoneExpectation(
            milestone="settled",
            objects=[ExpectedObject(
                type="SalesOrder", key=VOSS_ORDER,
                fields={"status": _RELEASED_STATUS},
                link_counts={"deal_disagrees_with_order": 1},
                link_props={
                    "deal_disagrees_with_order.status": "resolved",
                    "deal_disagrees_with_order.resolved_how": "by_cumulus",
                },
            )],
            trajectory={
                "terminal_status": "succeeded",
                "reported": (
                    "outcome=proceed, patched=True, write_state=applied, "
                    "resolved_how=by_cumulus, landed_status=pending-fulfillment, "
                    "desk_tasks=0"
                ),
                # `landed_status` is the observation's own output, so a
                # report carrying it is a report from a run that made both
                # commits: the decision and the observation, two ledger
                # entries, because they are two different claims about the
                # world.
                "claimed_by": "SalesOrder.status=erp:erp-sales",
            },
        )],
    ),
    EvalCase(
        id="release/a-second-approval-sends-nothing",
        spec_id=RELEASE_SPEC.id,
        name="The same approval twice sends one PATCH",
        summary=(
            "Idempotence, driven the way it would really happen: the order is "
            "already released and the journal remembers asking for that. A "
            "second decision is a second run and gets its own journal row by "
            "construction, which is exactly why the precondition cannot be "
            "'have I written before' and has to be 'does the estate hold what "
            "I would write, and did anyone here ask for it'."
        ),
        fixture_id=_STATE_FIXTURE,
        trigger={"record_id": VOSS_RECORD, "again": True},
        decisions=[_APPROVE],
        expectations=[MilestoneExpectation(
            milestone="settled",
            objects=[ExpectedObject(
                type="SalesOrder", key=VOSS_ORDER,
                fields={"status": _RELEASED_STATUS},
                link_counts={"deal_disagrees_with_order": 1},
                link_props={
                    "deal_disagrees_with_order.status": "resolved",
                    "deal_disagrees_with_order.resolved_how": "by_cumulus",
                },
            )],
            trajectory={
                "terminal_status": "succeeded",
                "reported": (
                    "outcome=already_applied, patched=False, resolved=True, "
                    "desk_tasks=0"
                ),
                "claimed_by": "SalesOrder.status=erp:erp-sales",
            },
        )],
    ),
    EvalCase(
        id="release/somebody-else-got-there-first",
        spec_id=RELEASE_SPEC.id,
        name="An order released elsewhere is resolved externally",
        summary=(
            "The same row, the same value, an empty journal. The controller "
            "wanted the order released and it is released, so the finding is "
            "over and nobody is interrupted about it — a number on a dashboard "
            "rather than a Task. This is the branch that must raise nothing."
        ),
        fixture_id=_STATE_FIXTURE,
        trigger={
            "record_id": VOSS_RECORD, "order_status": "Pending Fulfillment",
        },
        decisions=[_APPROVE],
        expectations=[MilestoneExpectation(
            milestone="settled",
            objects=[ExpectedObject(
                type="SalesOrder", key=VOSS_ORDER,
                fields={"status": _RELEASED_STATUS},
                link_counts={"deal_disagrees_with_order": 1},
                link_props={
                    "deal_disagrees_with_order.status": "resolved",
                    "deal_disagrees_with_order.resolved_how": "externally",
                },
            )],
            trajectory={
                "terminal_status": "succeeded",
                "reported": (
                    "outcome=resolved_externally, patched=False, "
                    "resolved_how=externally, desk_tasks=0"
                ),
                "claimed_by": "SalesOrder.status=erp:erp-sales",
            },
        )],
    ),
    EvalCase(
        id="release/a-billed-order-escalates",
        spec_id=RELEASE_SPEC.id,
        name="An order the ERP has billed escalates and writes nothing",
        summary=(
            "The row moved under the approval. The write table lists no move "
            "out of `Billed` and the estate would happily accept one — "
            "`erp.py` validates the picklist and nothing else — so this is the "
            "entire argument for `WRITABLE` in one case. The finding is still "
            "true and still standing, and the graph learns what the ERP "
            "actually holds from the read the precondition already made: an "
            "escalation is not a reason to stay ignorant of a row we just "
            "looked at."
        ),
        fixture_id=_STATE_FIXTURE,
        trigger={"record_id": VOSS_RECORD, "order_status": "Billed"},
        decisions=[_APPROVE],
        expectations=[MilestoneExpectation(
            milestone="settled",
            objects=[ExpectedObject(
                type="SalesOrder", key=VOSS_ORDER,
                fields={"status": "billed"},
                link_counts={"deal_disagrees_with_order": 1},
                link_props={"deal_disagrees_with_order.status": _STANDING},
            )],
            trajectory={
                "terminal_status": "succeeded",
                "reported": (
                    "outcome=escalate, patched=False, resolved=False, "
                    "desk_tasks=1"
                ),
                "claimed_by": "SalesOrder.status=erp:erp-sales",
                "task_raised": "order-desk/exception",
            },
        )],
    ),
    EvalCase(
        id="release/a-door-that-refuses-is-ledgered-failed",
        spec_id=RELEASE_SPEC.id,
        name="A door that refuses leaves a failed write and a person to tell",
        summary=(
            "`failed` and `refused` are different accusations: the Gateway "
            "refusing is governance working, and the world refusing after it "
            "was allowed is a write whose effect nobody can be sure of. The "
            "step retries, fails persistently and hands itself to the desk "
            "rather than failing into silence — so the run does not finish, "
            "and this case is graded at the precondition instead. Nothing was "
            "released and the finding still stands."
        ),
        fixture_id=_STATE_FIXTURE,
        trigger={"record_id": VOSS_RECORD, "door": "refuses"},
        decisions=[_APPROVE],
        expectations=[MilestoneExpectation(
            milestone="checked",
            objects=[ExpectedObject(
                type="SalesOrder", key=VOSS_ORDER,
                fields={"status": _HELD_STATUS},
            )],
            trajectory={
                # Suspended, not failed: the run is waiting on the person it
                # handed itself to.
                "terminal_status": "suspended",
                "task_raised": "order-desk/exception",
                "model_calls_at_most": "0",
            },
        )],
    ),
    EvalCase(
        id="release/a-lying-two-hundred-settles-unverified",
        spec_id=RELEASE_SPEC.id,
        name="A door that says yes and changes nothing is not a success",
        summary=(
            "The estate decides whether the write took, never the response "
            "code. A door that answers cheerfully and changes nothing is the "
            "failure a 200-trusting client cannot see, so the writer re-reads "
            "and compares. That is a Task and emphatically not a resolved "
            "finding: the disagreement is still true, and the order still "
            "reads as the ERP holds it."
        ),
        fixture_id=_STATE_FIXTURE,
        trigger={"record_id": VOSS_RECORD, "door": "lies"},
        decisions=[_APPROVE],
        expectations=[MilestoneExpectation(
            milestone="settled",
            objects=[ExpectedObject(
                type="SalesOrder", key=VOSS_ORDER,
                fields={"status": _HELD_STATUS},
                link_counts={"deal_disagrees_with_order": 1},
                link_props={"deal_disagrees_with_order.status": _STANDING},
            )],
            trajectory={
                "terminal_status": "succeeded",
                "reported": (
                    "patched=True, write_state=unverified, resolved=False, "
                    "desk_tasks=1"
                ),
                "claimed_by": "SalesOrder.status=erp:erp-sales",
                "task_raised": "order-desk/exception",
            },
        )],
    ),
    EvalCase(
        id="release/the-change-comes-home",
        spec_id=RELEASE_SPEC.id,
        name="The change comes home and lands as a no-op",
        summary=(
            "Recognizing our own write coming back is a property of the "
            "*delivery*: no feed declares it, none can, and the flow that made "
            "the write is long finished. The row still lands — an identical "
            "claim **is** the no-op, and suppressing the row would throw away "
            "the delivery that proves the write took — and nobody is "
            "interrupted."
        ),
        fixture_id=_STATE_FIXTURE,
        trigger={
            "record_id": VOSS_RECORD, "echo": "Pending Fulfillment",
        },
        decisions=[_APPROVE],
        expectations=[MilestoneExpectation(
            milestone="settled",
            objects=[ExpectedObject(
                type="SalesOrder", key=VOSS_ORDER,
                fields={"status": _RELEASED_STATUS},
                link_counts={"deal_disagrees_with_order": 1},
                link_props={
                    "deal_disagrees_with_order.status": "resolved",
                    "deal_disagrees_with_order.resolved_how": "by_cumulus",
                },
            )],
            trajectory={
                "terminal_status": "succeeded",
                "reported": "patched=True, write_state=applied, desk_tasks=0",
                # The point of the case: the feed re-delivered the order and
                # the claim it carries is byte-identical to the one the
                # read-back landed, so the graph gained nothing and no steward
                # was handed a conflict.
                "claimed_by": "SalesOrder.status=erp:erp-sales",
            },
        )],
    ),
    EvalCase(
        id="release/a-third-value-comes-home-instead",
        spec_id=RELEASE_SPEC.id,
        name="A third value coming home diverges and asks a person",
        summary=(
            "The re-elevating discrepancy: Cumulus set the order, the ERP "
            "confirmed it, and the feed now says something else. Nothing is "
            "undone — moving it back is a second decision — and what this "
            "needs is somebody who knows why it moved. The finding stays "
            "`resolved`, because it was: the release happened, and this is a "
            "new fact about the same order."
        ),
        fixture_id=_STATE_FIXTURE,
        trigger={"record_id": VOSS_RECORD, "echo": "Billed"},
        decisions=[_APPROVE],
        expectations=[MilestoneExpectation(
            milestone="settled",
            objects=[ExpectedObject(
                type="SalesOrder", key=VOSS_ORDER,
                fields={"status": "billed"},
                link_counts={"deal_disagrees_with_order": 1},
                link_props={"deal_disagrees_with_order.status": "resolved"},
            )],
            trajectory={
                "terminal_status": "succeeded",
                "reported": "patched=True, write_state=applied, desk_tasks=0",
                "claimed_by": "SalesOrder.status=erp:erp-sales",
                # Raised by the *delivery*, days after the run that wrote the
                # row had finished — which is why this is a fact about the
                # world rather than a step in a journal.
                "task_raised": "order-desk/exception",
            },
        )],
    ),
]


# ---------------------------------------------------------------------------
# Wick: the plant floor, the field packet and the compliance clock
# ---------------------------------------------------------------------------
#
# Five flows, six specs, and one property they all share that the naviant specs
# do not: **not one of them is submitted.** Each is woken by a fact — a feed
# delivering, a clock closing a window, an upstream flow finishing — so a case's
# `trigger` here is the fact, and `eval_world`'s entrypoints emit it.
#
# Every fixture is deliveries, including the crews and the plant departments:
# `TeamRecord` carries the `cost_center` the jobs feed binds `crew_id` against
# and `EmployeeRecord` carries the `team_slug` the headcount counts, so the
# workforce feed establishes both ends of the two joins the plant depends on.
# The unit tests write those two rows directly and say why — a department feed
# would put its whole surface between the test and what it is testing — but a
# fixture is not a unit test: what it has to be is the world production would
# have built, and `seed_fixture` runs objects *after* deliveries, so a Team
# written as an object would land after the job that needed it.
#
# The dates are literals rather than offsets from today. A fourteen-day
# document horizon, a sixty-day notice stage and a governed business-day
# calendar are all functions of *which* day it is, and a suite whose answers
# moved with the clock would be measuring the calendar.

WICK_SUPERVISOR = "chris.robson@wickbuildings.com"
WICK_SAFETY = "katy.tiller@wickbuildings.com"

#: The day every Wick window is answered as of. A Tuesday, and deliberately not
#: 2026-09-07 — that Monday is on the governed plant holiday list, and a plant
#: day dated on a holiday is a different test.
WICK_AS_OF = "2026-09-08"


# ---- the field packet ------------------------------------------------------
#
# The job numbers, the customers and the two label traps are the estate's
# authored roster (`docs/namespaces/wick/roster.md`): `The Willows Equine LLC`
# on 250661 and the two Evermores on 250553 / 250774.

def _wick_document(external_id: str, name: str, folder: str, version: int) -> dict:
    return {
        "external_id": external_id, "name": name,
        "folder_external_id": folder, "content_type": "application/pdf",
        "size": 24_000, "latest_version": version,
        "storage_ref": f"dms://{external_id}",
    }


def _wick_job(
    external_id: str,
    number: str,
    customer: str,
    crew: str,
    foreman: str,
    load: str,
    status: str = "Scheduled",
) -> dict:
    return {
        "external_id": external_id, "job_number": number,
        "name": f"{number} building", "customer_name": customer,
        "status": status, "load_date": load, "crew_id": crew,
        "foreman": foreman, "plant_site": "Mazomanie",
    }


#: The two crews the ERP's `crewId` binds to, through the cost centre the HRIS
#: writes beside the department. `SUB-KVB` matches none of them and never will:
#: the subcontractor is not on the payroll, which is the whole of the second
#: aggregate.
WICK_CREWS = {
    "source": "hris-workforce",
    "watermark": "2026-09-07T04:00:00Z",
    "teams": [
        {"external_id": "DEPT-2031", "slug": "crew-12", "name": "Crew 12",
         "cost_center": "12"},
        {"external_id": "DEPT-2032", "slug": "crew-14", "name": "Crew 14",
         "cost_center": "14"},
    ],
}

#: The filing tree, as the DMS holds it. 250661's packet is complete and one of
#: its four types is on a second revision; 250553 has one of the four; 250900's
#: folder holds nothing that matches a governed pattern; and **250774 has no
#: folder at all**, which is what the recurrence threshold is counted over.
WICK_FILING_TREE = {
    "source": "dms-library",
    "watermark": "2026-09-07T05:00:00Z",
    "folders": [
        {"external_id": "F-root", "name": "Job Documents"},
        {"external_id": "F-661", "name": "250661", "parent_external_id": "F-root"},
        {"external_id": "F-553", "name": "250553", "parent_external_id": "F-root"},
        {"external_id": "F-900", "name": "250900", "parent_external_id": "F-root"},
    ],
    "documents": [
        _wick_document("D-661-SL", "250661 Shipping List.pdf", "F-661", 1),
        _wick_document("D-661-EP1", "250661 Engineering Plans REV A.pdf",
                       "F-661", 1),
        _wick_document("D-661-EP2", "250661 Engineering Plans REV B.pdf",
                       "F-661", 2),
        _wick_document("D-661-MAP", "250661 Job Site Map.pdf", "F-661", 1),
        _wick_document("D-661-CH", "250661 Crew Hours.pdf", "F-661", 1),
        # Named to no governed pattern: counted, never guessed at.
        _wick_document("D-661-X", "site photos.pdf", "F-661", 1),
        _wick_document("D-553-SL", "250553 Shipping List.pdf", "F-553", 1),
        _wick_document("D-900-X", "notes.pdf", "F-900", 1),
    ],
}

WICK_SCHEDULED_JOBS = {
    "source": "erp-jobs",
    "watermark": "2026-09-07T05:00:00Z",
    "jobs": [
        # The stopword trap: `The Willows Equine LLC` → `250661 Willows`.
        _wick_job("J-661", "250661", "The Willows Equine LLC", "12",
                  "D. Prellwitz", "2026-09-12"),
        # The collision trap, both halves — the labels move together.
        _wick_job("J-553", "250553", "Evermore Ridge Estates", "14",
                  "M. Tessmann", "2026-09-14"),
        _wick_job("J-774", "250774", "Evermore Farms LLC", "12",
                  "D. Prellwitz", "2026-09-15"),
        # The subcontractor: a crew id no HRIS department carries.
        _wick_job("J-900", "250900", "Kickapoo Valley Builders", "SUB-KVB",
                  "KICKAPOO VALLEY BUILDERS", "2026-09-16"),
        # Off the governed status, and therefore never selected — which is how
        # "once the job moves to IN PROGRESS, stop checking" is kept.
        _wick_job("J-111", "250111", "Sauk Prairie Dairy LLC", "12",
                  "D. Prellwitz", "2026-09-10", status="In Progress"),
    ],
}

#: The same job, one crew later, as a one-row delta — which is how the estate
#: really reports a reassignment: the row's watermark moves and everything else
#: about it stays.
WICK_JOB_REASSIGNED = {
    "source": "erp-jobs",
    "watermark": "2026-09-08T05:00:00Z",
    "jobs": [
        _wick_job("J-661", "250661", "The Willows Equine LLC", "14",
                  "M. Tessmann", "2026-09-12"),
    ],
}


# ---- the compliance clock --------------------------------------------------
#
# The ladder is the point: one credential at each governed notice stage, one
# whose date will not read, one whose holder is on nobody's roster, one whose
# holder has left, one past the seven-year retention horizon — and two machines,
# one on an inspection cycle and one the source says carries none.

def _wick_credential(external_id: str, holder: str, name: str, category: str,
                     expires: str = "", **over) -> dict:
    row = {
        "external_id": external_id, "holder_email": holder,
        "credential_type": name, "category": category,
        "expiration_date": expires,
    }
    row.update(over)
    return row


WICK_ROSTER = {
    "source": "hris-workforce",
    "watermark": "2026-09-07T04:00:00Z",
    "teams": [
        {"external_id": "DEPT-2003", "slug": "plant-mazomanie",
         "name": "Plant — Mazomanie", "cost_center": "3000",
         "lead_email": "chris.robson@wickbuildings.com"},
    ],
    "employees": [
        {"external_id": "EMP-2003", "email": WICK_SUPERVISOR,
         "name": "Chris Robson", "title": "Plant Manager",
         "team_slug": "plant-mazomanie", "status": "Active"},
        {"external_id": "EMP-2005", "email": WICK_SAFETY,
         "name": "Katy Tiller", "title": "HR & Safety",
         "team_slug": "plant-mazomanie", "status": "Active"},
        {"external_id": "EMP-2020", "email": "terry.nachreiner@wickbuildings.com",
         "name": "Terry Nachreiner", "title": "Driver",
         "team_slug": "plant-mazomanie", "status": "Active",
         "manager_email": WICK_SUPERVISOR},
        # On the roster and gone: the holder of a live certification who has
        # left is one of the four exception classes.
        {"external_id": "EMP-2021", "email": "vern.oimoen@wickbuildings.com",
         "name": "Vern Oimoen", "title": "Driver",
         "team_slug": "plant-mazomanie", "status": "Terminated",
         "manager_email": WICK_SUPERVISOR},
    ],
}

#: The training file. `TR-3001` is 60 days out on the day the first window
#: closes, so the whole 60 → 30 → 15 cycle can be walked over one row.
WICK_TRAINING = {
    "source": "hris-training",
    "watermark": "2026-09-07T05:00:00Z",
    "credentials": [
        _wick_credential("TR-3001", "terry.nachreiner@wickbuildings.com",
                         "Fall Protection Refresher", "training",
                         "2026-11-07", completion_date="2025-11-07",
                         result="Pass", provider="Wisconsin Safety Council"),
        # No expiry anyone can read: the first exception class.
        _wick_credential("TR-3002", "terry.nachreiner@wickbuildings.com",
                         "Lift Operator", "training",
                         completion_date="2026-01-14", result="Pass"),
        # A holder no employee resolves to: the second.
        _wick_credential("TR-3003", "cody.statz@wickbuildings.com",
                         "Confined Space", "clearance", "2026-10-01"),
        # A holder who has left, and a live certification: the third.
        _wick_credential("TR-3004", "vern.oimoen@wickbuildings.com",
                         "Forklift Certification", "certification",
                         "2026-10-15"),
        # Older than the governed seven-year horizon: the fourth. Nothing is
        # deleted; it is reported for a person to rule on.
        _wick_credential("TR-3005", "terry.nachreiner@wickbuildings.com",
                         "Rigging Certification", "certification",
                         "2017-06-30"),
    ],
}

#: The driver file. One row, one category — and the claim it exists to grade is
#: that this becomes the same `Credential` type as a training row, differing
#: only in `category`.
WICK_DRIVER_FILES = {
    "source": "hris-driver-files",
    "watermark": "2026-09-07T05:00:00Z",
    "credentials": [
        _wick_credential("DQ-4001", "terry.nachreiner@wickbuildings.com",
                         "Commercial Driver Licence — Class A", "licence",
                         "2026-09-20"),
    ],
}

#: The fleet. A truck on a cycle, and a skid steer the source says carries no
#: tracked inspection — a fact about the machine, not a gap in the feed.
WICK_FLEET = {
    "source": "erp-assets",
    "watermark": "2026-09-07T05:00:00Z",
    "equipment": [
        {"external_id": "AST-7001", "equipment_id": "TRK-101",
         "name": "Truck 101", "category": "truck", "home_site": "Mazo",
         "last_inspection_date": "2025-10-08",
         "next_inspection_due_date": "2026-10-08"},
        {"external_id": "AST-7004", "equipment_id": "SKD-410",
         "name": "Skid Steer 410", "category": "skid-steer",
         "home_site": "Mazo", "last_inspection_date": "2025-04-02"},
    ],
}


# ---- the plant floor -------------------------------------------------------

#: The ten plant departments, spelled the HRIS's way — which is the string the
#: governed department table joins on — and the people whose `member_of` edges
#: are the headcount. Two rows per department is not decoration: the capacity
#: ladder turns a headcount into a capacity row, so a day filled against an
#: empty department would be filled against the wrong row.
WICK_PLANT_ROSTER = {
    "source": "hris-workforce",
    "watermark": "2026-09-07T04:00:00Z",
    "teams": [
        {"external_id": "DEPT-2004", "slug": "truss-cut",
         "name": "Truss Cutting", "cost_center": "3010"},
        {"external_id": "DEPT-2005", "slug": "truss-fab",
         "name": "Truss Fabrication", "cost_center": "3020"},
        {"external_id": "DEPT-2006", "slug": "steel-fab",
         "name": "Steel Fabrication", "cost_center": "3030"},
        {"external_id": "DEPT-2007", "slug": "trim-fab",
         "name": "Trim Fabrication", "cost_center": "3040"},
        {"external_id": "DEPT-2008", "slug": "hardware-trim-pack",
         "name": "Hardware & Trim Packaging", "cost_center": "3050"},
        {"external_id": "DEPT-2031", "slug": "crew-12", "name": "Crew 12",
         "cost_center": "12"},
    ],
    "employees": [
        {"external_id": f"EMP-31{index:02d}",
         "email": f"{slug}-{index}@wickbuildings.com",
         "name": f"{slug} {index}", "team_slug": slug, "status": "Active"}
        for index, slug in enumerate([
            "steel-fab", "steel-fab", "steel-fab",
            "truss-cut", "truss-fab", "truss-fab",
            "trim-fab", "hardware-trim-pack",
        ])
    ],
}

#: The four jobs a plant day is filled out of. `trim-fab` is done on all of
#: them and `truss-cut` on none, which is what makes one department's day a
#: fill and another's a deferral in the same window — the sequencing table is
#: read live off the job's own flags.
WICK_PLANT_LOADS = ("2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22")


def _wick_plant_job(external_id: str, number: str, load: str, **over) -> dict:
    row = {
        "external_id": external_id, "job_number": number,
        "name": f"{number} shed", "customer_name": "Evermore Ridge Estates",
        "builder_name": "Wick Buildings", "plant_site": "Mazo",
        "status": "Scheduled", "load_date": load, "shipment_date": load,
        "week_position": "Week 1", "out_to_yard": "Yes", "do_not_fab": "No",
        "foreman": "D. Prellwitz", "crew_id": "12",
        "width": 60.0, "length": 120.0, "height": 16.0,
        "secondary_dimensions": "12 x 24",
        "upstream_done": ["trim-fab"],
    }
    row.update(over)
    return row


#: Four buildable jobs and five that are not, each excluded by a different
#: governed clause and each for its own named reason.
WICK_PLANT_JOBS = {
    "source": "erp-jobs",
    "watermark": "2026-09-07T05:00:00Z",
    "jobs": [
        *(
            _wick_plant_job(f"JOB-70{index:02d}", number, load)
            for index, (number, load) in enumerate(zip(
                ("250553", "250774", "250661", "250900"), WICK_PLANT_LOADS,
            ))
        ),
        # A Saturday load: the governed calendar says the plant is shut.
        _wick_plant_job("JOB-7101", "260101", "2026-10-24"),
        # Held. `doNotFab: "Yes"` is `DNF` in the governed vocabulary.
        _wick_plant_job("JOB-7102", "260102", "2026-10-20", do_not_fab="Yes"),
        # A load date already behind the window.
        _wick_plant_job("JOB-7103", "260103", "2026-09-01"),
        # A production week outside the governed set — the screen's own way of
        # saying "beyond the last week", which is not an error.
        _wick_plant_job("JOB-7104", "260104", "2026-10-21",
                        week_position="Week 5"),
        # Not released to the yard.
        _wick_plant_job("JOB-7105", "260105", "2026-10-22", out_to_yard="No"),
    ],
}

#: Nothing eligible, and a job book that is not empty — which is the difference
#: the governed page calls informational: an empty *eligible* set is a slow
#: week, and an empty *book* is a broken feed.
WICK_PLANT_JOBS_ALL_HELD = {
    "source": "erp-jobs",
    "watermark": "2026-09-07T05:00:00Z",
    "jobs": [
        _wick_plant_job("JOB-7102", "260102", "2026-10-20", do_not_fab="Yes"),
        _wick_plant_job("JOB-7101", "260101", "2026-10-24"),
    ],
}


def _wick_cut_sheets(numbers: tuple[str, ...]) -> dict:
    """The plant floor's own copy of the paper, filed where the page says.

    `PlantCutSheets/<plant site>/<job number>` — not the office's
    `Job Documents/<job>`, which holds a shipping list matching the same
    governed pattern and is deliberately not the one read.
    """
    folders = [
        {"external_id": "F-cut", "name": "PlantCutSheets"},
        {"external_id": "F-mazo", "name": "Mazo", "parent_external_id": "F-cut"},
    ]
    documents = []
    for number in numbers:
        folders.append({
            "external_id": f"F-{number}", "name": number,
            "parent_external_id": "F-mazo",
        })
        documents.append({
            "external_id": f"WD-{number}",
            "name": f"{number} Shipping List A1-LL.pdf",
            "folder_external_id": f"F-{number}",
            "content_type": "application/pdf", "size": 4_096,
            "latest_version": 1,
            "storage_ref": f"https://dms.example/{number}/content",
        })
    return {
        "source": "dms-library", "watermark": "2026-09-07T05:00:00Z",
        "folders": folders, "documents": documents,
    }


#: What the plant's copy of each ticket says, in the stub reader's own case
#: grammar. A hardware grid big enough that four jobs carry a department past
#: the point its governed incentive crosses zero — which is the only way the
#: incentive clause is reachable at all.
WICK_PLANT_LIBRARY = {
    number: "bags=400 caps=40" for number in ("250553", "250774", "250661", "250900")
}


def _wick_fixtures() -> list[Fixture]:
    """The five worlds the Wick specs run in, all of them deliveries.

    `objects` is empty here and that is the interesting part: every row these
    flows stand on — the crews, the plant departments, the people whose
    membership *is* the headcount, the job book, the filing tree — arrives on a
    feed Wick really has, so each fixture is established the way production
    established it and the three sync flows are exercised on the way in.
    """
    common = {
        "origin": "declared", "authored_by": "platform-eng",
        "derived_from": DECLARED_IN,
    }
    plant_reads = [
        "knowledge:plant-scheduling-parameters@rev1",
        "knowledge:labor-hour-formulas@rev1",
        "knowledge:truss-standards@rev1",
    ]
    return [
        Fixture(
            id="wick/scheduled-jobs-week",
            flow_id="field-documents",
            name="A week of scheduled jobs, and the filing tree behind them",
            summary=(
                "Five jobs and four folders. Both packet-label traps are in "
                "it — an article-leading customer name and two customers "
                "sharing a first keyword — beside a complete packet on a "
                "second revision, a partial one, a job with no folder at all, "
                "a crew id no department carries, and a job that has moved off "
                "the governed status and must not be selected."
            ),
            deliveries=[WICK_CREWS, WICK_FILING_TREE, WICK_SCHEDULED_JOBS],
            read_set=["knowledge:field-documents-policy@rev1"],
            **common,
        ),
        Fixture(
            id="wick/job-moves-crew",
            flow_id="field-documents",
            name="The same week, and one job reassigned",
            summary=(
                "The world above with a second, one-row jobs delta moving "
                "250661 from Crew 12 to Crew 14 — which is how the estate "
                "really reports a reassignment. The precondition for the claim "
                "that retired the reconciliation Flow: the old edge is "
                "withdrawn rather than deleted, and no folder is touched."
            ),
            deliveries=[
                WICK_CREWS, WICK_FILING_TREE, WICK_SCHEDULED_JOBS,
                WICK_JOB_REASSIGNED,
            ],
            read_set=["knowledge:field-documents-policy@rev1"],
            **common,
        ),
        Fixture(
            id="wick/compliance-clock",
            flow_id="credential-tracking",
            name="The compliance clock, with one row at every stage",
            summary=(
                "A roster, a training file, a driver file and two machines. "
                "One credential is exactly sixty days out on the first window, "
                "so a single row can be walked through all three governed "
                "notice stages; the other four are one of each exception class; "
                "and one machine carries no tracked inspection at all."
            ),
            deliveries=[
                WICK_ROSTER, WICK_TRAINING, WICK_DRIVER_FILES, WICK_FLEET,
            ],
            read_set=["knowledge:certification-alert-policy@rev1"],
            **common,
        ),
        Fixture(
            id="wick/plant-day",
            flow_id="plant-eligibility-sweep",
            name="A plant week: four jobs to build and five that cannot be",
            summary=(
                "The whole plant spine's precondition. Four buildable jobs "
                "with their shipping lists filed under the plant's own cut "
                "sheet root, and five more excluded one apiece by a weekend "
                "load, a hold, a load date already past, a production week "
                "outside the governed set, and a job not released to the yard. "
                "`trim-fab` is done on every job and `truss-cut` on none, so "
                "one department's day is a fill and another's is a deferral in "
                "the same window."
            ),
            deliveries=[
                WICK_PLANT_ROSTER,
                _wick_cut_sheets(("250553", "250774", "250661", "250900")),
                WICK_PLANT_JOBS,
            ],
            read_set=plant_reads,
            **common,
        ),
        Fixture(
            id="wick/plant-day-nothing-eligible",
            flow_id="plant-eligibility-sweep",
            name="A plant week with nothing eligible and a book that is not empty",
            summary=(
                "Two jobs, both excluded. The distinction the governed page "
                "draws: an empty *eligible* set is a slow week and raises "
                "nothing, while an empty job *book* is a broken feed and is "
                "somebody's morning."
            ),
            deliveries=[WICK_PLANT_ROSTER, WICK_PLANT_JOBS_ALL_HELD],
            read_set=["knowledge:plant-scheduling-parameters@rev1"],
            **common,
        ),
        Fixture(
            id="wick/shipping-list-pages",
            flow_id="shipping-list-extraction",
            name="One job's shipping list, filed on the plant floor",
            summary=(
                "A single eligible job and the plant's own copy of its seven "
                "page families, under `PlantCutSheets/Mazo/250553`. What each "
                "page says is the case's to declare, so one world serves the "
                "clean read, the absent grid, the figure outside its band and "
                "the correction that re-runs both checks."
            ),
            deliveries=[
                WICK_PLANT_ROSTER,
                _wick_cut_sheets(("250553",)),
                {
                    "source": "erp-jobs", "watermark": "2026-09-07T05:00:00Z",
                    "jobs": [_wick_plant_job("JOB-7000", "250553",
                                             WICK_PLANT_LOADS[0])],
                },
            ],
            read_set=[
                *plant_reads,
                "knowledge:shipping-list-extract-prompt@rev1",
                "knowledge:shipping-list-locate-prompt@rev1",
            ],
            **common,
        ),
    ]




# ---- field documents -------------------------------------------------------

FIELD_DOCUMENTS_SPEC = EvalSpec(
    id="field-documents-core",
    flow_id="field-documents",
    version=1,
    summary=(
        "What the job packet promises. The label a crew calls a job by is a "
        "governed derivation and not a literal, and both traps the estate "
        "seeds deliberately have to hold: an article-leading customer name "
        "resolves through the stopword list, and two customers sharing a first "
        "keyword resolve through the collision rule — both of them, because a "
        "collision is a fact about the pair. Beside that: only the current "
        "revision is linked, a job off the governed status is never selected, "
        "a job with no filed documents is a count until the governed "
        "recurrence threshold and only then somebody's morning, and a crew id "
        "nobody can resolve is one identity question rather than N document "
        "questions."
    ),
    milestones=[
        Milestone(
            id="considered",
            label="once the window has decided what it is answering",
            anchor="first_of_kind",
            selector="action",
            note=(
                "The only step that writes, which makes it the point where a "
                "job that was **not** selected can be judged: nothing later "
                "can give it a label, so an absence here is an absence for "
                "good."
            ),
            objects=[
                ObjectRule(
                    type="Job",
                    fields=[FieldRule(
                        field="packet_label", match="absent",
                        note="a job off the governed status is not selected, "
                             "so no rule ever derives a label for it — which "
                             "is how *stop checking once it is In Progress* is "
                             "kept: there is nothing to switch off",
                    )],
                ),
            ],
            trajectory=[TrajectoryRule(
                kind="model_calls_at_most",
                note="none. Every decision here is a governed rule over rows "
                     "the feeds landed, and a model in this loop would be a "
                     "model nobody could argue with",
            )],
        ),
        Milestone(
            id="packets",
            label="once the packets have been written to the job book",
            anchor="wrote",
            selector="graph:Job/",
            note=(
                "Named by what it wrote rather than by a step id: the label, "
                "the completeness flag and the edges to the current revisions "
                "all land in one guarded write, and that write is the point."
            ),
            objects=[
                ObjectRule(
                    type="Job",
                    fields=[
                        FieldRule(field="packet_label", match="exact"),
                        FieldRule(field="documents_complete", match="exact"),
                    ],
                    links=[
                        LinkRule(
                            type="documented_by", to_type="Document",
                            expect="count",
                            note="the four governed types at their current "
                                 "revision — a superseded one is withdrawn "
                                 "and therefore not counted, which is what "
                                 "makes this number the whole claim",
                        ),
                        LinkRule(
                            type="job_folder", to_type="Folder",
                            expect="count",
                            note="the packet is a query over one edge rather "
                                 "than a directory somebody has to create",
                        ),
                        LinkRule(
                            type="assigned_to", to_type="Team", expect="count",
                            props=[FieldRule(field="crew_id", match="exact")],
                            note="the live crew. `job-sync` draws it; this "
                                 "flow reads it, and a job whose crew resolves "
                                 "to nobody has no edge at all",
                        ),
                    ],
                ),
            ],
        ),
        Milestone(
            id="settled",
            label="once the window has closed",
            anchor="terminal",
            objects=[],
            trajectory=[
                TrajectoryRule(kind="terminal_status"),
                TrajectoryRule(
                    kind="reported",
                    note="the window's own account: which governed rules fired, "
                         "what it could not find, and whether a gap has become "
                         "a question yet",
                ),
                TrajectoryRule(
                    kind="task_raised",
                    note="the desk the governed approval table routes an "
                         "aggregate to",
                ),
                TrajectoryRule(
                    kind="claimed_by",
                    note="the label is this flow's claim and nobody else's. A "
                         "second source on it would mean two derivations of "
                         "one name, which is exactly the confusion the "
                         "governed convention exists to end",
                ),
            ],
        ),
    ],
)

FIELD_DOCUMENTS_CASES = [
    EvalCase(
        id="wick/both-label-traps-hold",
        spec_id=FIELD_DOCUMENTS_SPEC.id,
        name="Both packet-label traps hold, and the out-of-scope job is untouched",
        summary=(
            "One window over the week. `The Willows Equine LLC` becomes "
            "`250661 Willows` through the governed stopword list; the two "
            "Evermores become `250553 Evermore Ridge` and `250774 Evermore "
            "Farms` through the collision rule, and **both** move. The "
            "complete packet carries four edges and not five — the superseded "
            "revision is withdrawn — the job with no folder carries none, the "
            "job off the governed status has no label at all, and the "
            "subcontractor's jobs are one identity question."
        ),
        fixture_id="wick/scheduled-jobs-week",
        trigger={"as_of": WICK_AS_OF, "windows": 1},
        expectations=[
            MilestoneExpectation(
                milestone="considered",
                objects=[
                    ExpectedObject(
                        type="Job", key="erp-jobs:250111",
                        fields={"packet_label": ""},
                    ),
                    # The subcontractor's job: no packet was even attempted,
                    # so no document question is asked about a job nobody can
                    # deliver to — one identity question instead of N.
                    ExpectedObject(
                        type="Job", key="erp-jobs:250900",
                        fields={"packet_label": ""},
                    ),
                ],
                trajectory={"model_calls_at_most": "0"},
            ),
            MilestoneExpectation(
                milestone="packets",
                objects=[
                    ExpectedObject(
                        type="Job", key="erp-jobs:250661",
                        fields={
                            "packet_label": "250661 Willows",
                            "documents_complete": "yes",
                        },
                        link_counts={
                            "documented_by": 4, "job_folder": 1,
                            "assigned_to": 1,
                        },
                        link_props={"assigned_to.crew_id": "12"},
                    ),
                    ExpectedObject(
                        type="Job", key="erp-jobs:250553",
                        fields={
                            "packet_label": "250553 Evermore Ridge",
                            "documents_complete": "no",
                        },
                        link_counts={
                            "documented_by": 1, "job_folder": 1,
                            "assigned_to": 1,
                        },
                        link_props={"assigned_to.crew_id": "14"},
                    ),
                    ExpectedObject(
                        type="Job", key="erp-jobs:250774",
                        fields={
                            "packet_label": "250774 Evermore Farms",
                            "documents_complete": "no",
                        },
                        link_counts={
                            "documented_by": 0, "job_folder": 0,
                            "assigned_to": 1,
                        },
                    ),
                    # The subcontractor's job, by its edges: no crew resolved,
                    # so no packet was resolved, so **no document question was
                    # asked** — which is the structural half of "in the second
                    # aggregate and only the second". Its fields are graded at
                    # `considered`, where the rule is an absence.
                    ExpectedObject(
                        type="Job", key="erp-jobs:250900",
                        link_counts={
                            "documented_by": 0, "assigned_to": 0,
                            "job_folder": 1,
                        },
                    ),
                ],
            ),
            MilestoneExpectation(
                milestone="settled",
                trajectory={
                    "terminal_status": "succeeded",
                    "reported": (
                        "stopwords_applied=1, collisions=2, folders_missing=1, "
                        "unclassified=1, crew_unstated=0, linked=5, "
                        "gap_tasks=0, "
                        "exclusions~'status-not-in-scope': 1, "
                        "screened~'crewless': 1, "
                        "screened~'past_threshold': 0"
                    ),
                    "task_raised": "field-operations/exception",
                    "claimed_by": "Job.packet_label=flow:field-documents",
                },
            ),
        ],
    ),
    EvalCase(
        id="wick/no-documents-becomes-a-question-at-the-threshold",
        spec_id=FIELD_DOCUMENTS_SPEC.id,
        name="No documents at all is a count until the threshold, then one Task",
        summary=(
            "The design's own answer, kept: *log as information and continue; "
            "notify if recurring*. 250774 has no folder and nothing filed. The "
            "window above counted it and asked nobody; the window here is the "
            "one that crosses the governed recurrence threshold, and the "
            "aggregate it raises carries the empty job and not the partial one "
            "— a filing failure is worth somebody's morning and a missing crew "
            "hours sheet is not."
        ),
        fixture_id="wick/scheduled-jobs-week",
        trigger={"as_of": WICK_AS_OF, "windows": 2},
        expectations=[MilestoneExpectation(
            milestone="settled",
            trajectory={
                "terminal_status": "succeeded",
                "reported": (
                    "gap_tasks=1, threshold=3, "
                    "screened~'past_threshold': 1, "
                    "screened~'partial': 1, "
                    "empty_jobs~erp-jobs:250774"
                ),
                "task_raised": "field-operations/exception",
            },
        )],
    ),
]


FIELD_MOVE_SPEC = EvalSpec(
    id="field-documents-crew-move",
    flow_id="field-documents",
    version=1,
    summary=(
        "The claim that retired a Flow. A weekly bot used to go looking for "
        "the difference between last week's crew schedule and this one, and "
        "to delete a folder out of one foreman's tree and create it in "
        "another's. The difference is a property of the record now: the old "
        "`assigned_to` edge is **withdrawn** — validity ended, never deleted — "
        "the new one is live, and both stay on the job, so *which crew has it "
        "now, and who held it before* is one traversal. There is no "
        "destructive act left to gate, and no folder is created, moved, copied "
        "or removed by any of it."
    ),
    milestones=[
        Milestone(
            id="packets",
            label="once the packets have been written to the job book",
            anchor="wrote",
            selector="graph:Job/",
            objects=[
                ObjectRule(
                    type="Job",
                    fields=[FieldRule(field="packet_label", match="exact")],
                    links=[
                        LinkRule(
                            type="assigned_to", to_type="Team", expect="count",
                            props=[FieldRule(field="crew_id", match="exact")],
                            note="**one** live edge, to the new crew. A "
                                 "withdrawn edge is still on the record and is "
                                 "deliberately not counted here — a count of "
                                 "two would mean the reassignment never took, "
                                 "and a count of one after a delete would look "
                                 "identical, which is why the run's own "
                                 "`reassigned` figure is graded beside it",
                        ),
                        LinkRule(
                            type="documented_by", to_type="Document",
                            expect="count",
                            note="unchanged by the move: a job's paperwork is "
                                 "not a fact about which crew is holding it",
                        ),
                        LinkRule(
                            type="job_folder", to_type="Folder",
                            expect="count",
                            note="the same folder, still. Nothing was created "
                                 "and nothing was removed",
                        ),
                    ],
                ),
            ],
        ),
        Milestone(
            id="settled",
            label="once the window has closed",
            anchor="terminal",
            trajectory=[
                TrajectoryRule(kind="terminal_status"),
                TrajectoryRule(
                    kind="reported",
                    note="`reassigned` is the window seeing a withdrawn edge "
                         "on a job it is answering — the fact that a delete "
                         "would have destroyed",
                ),
            ],
        ),
    ],
)

FIELD_MOVE_CASES = [
    EvalCase(
        id="wick/a-job-moves-crews-and-nothing-is-deleted",
        spec_id=FIELD_MOVE_SPEC.id,
        name="A job moves crews: the old edge is withdrawn, not deleted",
        summary=(
            "The reassignment arrives the way the estate really reports one — "
            "a one-row delta whose watermark moved and whose crew changed. "
            "After it, 250661 has exactly one live `assigned_to` edge and it "
            "names Crew 14; the edge to Crew 12 is withdrawn and still on the "
            "record; the packet is untouched; and the window reads the new "
            "crew while reporting that a move happened."
        ),
        fixture_id="wick/job-moves-crew",
        trigger={"as_of": WICK_AS_OF, "windows": 1},
        expectations=[
            MilestoneExpectation(
                milestone="packets",
                objects=[ExpectedObject(
                    type="Job", key="erp-jobs:250661",
                    fields={"packet_label": "250661 Willows"},
                    link_counts={
                        "assigned_to": 1, "documented_by": 4, "job_folder": 1,
                    },
                    link_props={"assigned_to.crew_id": "14"},
                )],
            ),
            MilestoneExpectation(
                milestone="settled",
                trajectory={
                    "terminal_status": "succeeded",
                    "reported": (
                        "reassigned=1, packets~'crew': 'Crew 14'"
                    ),
                },
            ),
        ],
    ),
]


# ---- the compliance clock --------------------------------------------------

CREDENTIAL_SPEC = EvalSpec(
    id="credential-tracking",
    flow_id="credential-tracking",
    version=1,
    summary=(
        "The Excel training matrix and the DOT tracker, as a governed rule "
        "over the graph. Three notice stages fire once each per cycle and "
        "never twice, because the stage that was sent is written on the row "
        "rather than kept in a spreadsheet cell somebody could edit. A course "
        "certificate and a commercial driving licence are the **same** kind of "
        "thing — a dated permission that expires and gets renewed — so they "
        "are one type differing only in category, and a second sweep that had "
        "to agree with the first was never built. What the rule cannot decide "
        "is sorted into four named classes and reaches a desk as four Tasks "
        "however many rows are behind them; and a machine the source says "
        "carries no tracked inspection is excluded and counted rather than "
        "reported overdue forever."
    ),
    milestones=[
        Milestone(
            id="staged",
            label="once every row has been measured against the notice stages",
            anchor="last_of_kind",
            selector="rule",
            trajectory=[
                TrajectoryRule(
                    kind="reported",
                    note="what the rule decided, by stage — and what it "
                         "deliberately did not re-send",
                ),
                TrajectoryRule(kind="model_calls_at_most"),
            ],
        ),
        Milestone(
            id="recorded",
            label="once the notices have been written onto the rows",
            anchor="wrote",
            selector="graph:Credential/",
            objects=[
                ObjectRule(
                    type="Credential",
                    fields=[
                        FieldRule(field="category", match="exact"),
                        FieldRule(field="credential_type", match="exact"),
                        FieldRule(field="last_notice_stage", match="exact"),
                        FieldRule(field="last_notified_on", match="exact"),
                        FieldRule(field="status", match="exact"),
                        FieldRule(
                            field="holder_email", match="exact",
                            note="filled by a join on the way in: both "
                                 "credential resources name a person by the "
                                 "HRIS's own id and nothing else",
                        ),
                    ],
                ),
                ObjectRule(
                    type="Equipment",
                    fields=[FieldRule(
                        field="inspection_tracked", match="exact",
                        note="derived once, on the way in, from the absence of "
                             "a next-due date. A fact about the machine, not a "
                             "gap in the feed — and the reason it never alerts",
                    )],
                ),
            ],
        ),
        Milestone(
            id="settled",
            label="once the window has closed",
            anchor="terminal",
            trajectory=[
                TrajectoryRule(kind="terminal_status"),
                TrajectoryRule(kind="reported"),
                TrajectoryRule(
                    kind="task_raised",
                    note="the desk the governed routing table names",
                ),
            ],
        ),
    ],
)

_WICK_TERRY = "terry.nachreiner@wickbuildings.com"

CREDENTIAL_CASES = [
    EvalCase(
        id="wick/one-window-fires-every-stage-once",
        spec_id=CREDENTIAL_SPEC.id,
        name="Each governed stage fires once, and two files are one type",
        summary=(
            "One window over the whole book. Six rows are due and they land "
            "two apiece on the 60, 30 and 15-day stages. A training record and "
            "a driver-qualification row both come out as `Credential`s "
            "distinguished only by `category` — and both are stamped by the "
            "same pass, each at the stage its own date earned. The skid steer "
            "the source gives no next-due date for is excluded and counted, "
            "and the four things the rule could not decide reach one desk as "
            "four Tasks and not five."
        ),
        fixture_id="wick/compliance-clock",
        trigger={"days": [WICK_AS_OF]},
        expectations=[
            MilestoneExpectation(
                milestone="staged",
                trajectory={
                    "reported": (
                        "due=6, already_sent=0, "
                        "by_stage~'60': 2, by_stage~'30': 2, by_stage~'15': 2"
                    ),
                    "model_calls_at_most": "0",
                },
            ),
            MilestoneExpectation(
                milestone="recorded",
                objects=[
                    ExpectedObject(
                        type="Credential", key="hris-training:TR-3001",
                        fields={
                            "category": "training",
                            "credential_type": "Fall Protection Refresher",
                            "last_notice_stage": "60",
                            "last_notified_on": WICK_AS_OF,
                            "status": "expiring",
                            "holder_email": _WICK_TERRY,
                        },
                    ),
                    ExpectedObject(
                        type="Credential", key="hris-driver-files:DQ-4001",
                        fields={
                            "category": "licence",
                            "credential_type":
                                "Commercial Driver Licence — Class A",
                            "last_notice_stage": "15",
                            "last_notified_on": WICK_AS_OF,
                            "status": "expiring",
                            "holder_email": _WICK_TERRY,
                        },
                    ),
                    ExpectedObject(
                        type="Equipment", key="erp-assets:SKD-410",
                        fields={"inspection_tracked": "no"},
                    ),
                ],
            ),
            MilestoneExpectation(
                milestone="settled",
                trajectory={
                    "terminal_status": "succeeded",
                    "reported": (
                        "alerted=6, considered=7, equipment=2, "
                        "untracked_assets=1, inspections=1, "
                        "holder_unresolved=1, exception_tasks=4"
                    ),
                    "task_raised": "hr-safety/exception",
                },
            ),
        ],
    ),
    EvalCase(
        id="wick/a-second-window-the-same-day-re-sends-nothing",
        spec_id=CREDENTIAL_SPEC.id,
        name="The stage on the row is what stops a second notice",
        summary=(
            "Two windows on the same day. The second finds every row already "
            "at the stage it is owed and sends nothing — the guard is the "
            "`last_notice_stage` claim on the row itself, which is the "
            "spreadsheet's *Last Notified* cell turned into something with a "
            "provenance. This is also what makes a daily cadence safe."
        ),
        fixture_id="wick/compliance-clock",
        trigger={"days": [WICK_AS_OF, WICK_AS_OF]},
        expectations=[
            MilestoneExpectation(
                milestone="staged",
                trajectory={"reported": "due=0, already_sent=6"},
            ),
            MilestoneExpectation(
                milestone="settled",
                trajectory={
                    "terminal_status": "succeeded",
                    "reported": "alerted=0, exception_tasks=0",
                },
            ),
        ],
    ),
    EvalCase(
        id="wick/the-cycle-walks-sixty-thirty-fifteen",
        spec_id=CREDENTIAL_SPEC.id,
        name="One row, three windows, three stages — and then nothing left",
        summary=(
            "The claim a table of thresholds cannot make on its own: the same "
            "certificate is notified at sixty days, again at thirty and again "
            "at fifteen, once each, as the date comes at it. The window graded "
            "here is the third, where the row is thirteen days out and the "
            "last stage fires."
        ),
        fixture_id="wick/compliance-clock",
        trigger={"days": [WICK_AS_OF, "2026-10-10", "2026-10-25"]},
        expectations=[
            MilestoneExpectation(
                milestone="staged",
                trajectory={"reported": "due=1, by_stage~'15': 1"},
            ),
            MilestoneExpectation(
                milestone="recorded",
                objects=[ExpectedObject(
                    type="Credential", key="hris-training:TR-3001",
                    fields={
                        "category": "training",
                        "credential_type": "Fall Protection Refresher",
                        "last_notice_stage": "15",
                        "last_notified_on": "2026-10-25",
                        "status": "expiring",
                        "holder_email": _WICK_TERRY,
                    },
                )],
            ),
            MilestoneExpectation(
                milestone="settled",
                trajectory={
                    "terminal_status": "succeeded",
                    "reported": "alerted=1, already_sent=5",
                },
            ),
        ],
    ),
]


# ---- the plant window ------------------------------------------------------

PLANT_SPEC = EvalSpec(
    id="plant-eligibility",
    flow_id="plant-eligibility-sweep",
    version=1,
    summary=(
        "The screen a scheduler used to read down by eye, as clauses with "
        "names. Every clause is a line on one governed page, they are read in "
        "the page's order, and a job that fails one is excluded **for that "
        "clause** and no other — so *why is this not on Thursday* has an "
        "answer that cites the sentence it came from. The sweep itself writes "
        "nothing at all: it decides what is in play and emits the facts the "
        "extraction and the day-fill answer, which is why an empty eligible "
        "set is a slow week rather than an alarm."
    ),
    milestones=[
        Milestone(
            id="screened",
            label="once every candidate has been put to the governed clauses",
            anchor="last_of_kind",
            selector="rule",
            note=(
                "There is one rule step and this is it. The clauses are "
                "evaluated in the page's order and stop at the first failure, "
                "so a job that breaks two rules is excluded for the first — "
                "which is what makes the counts below a partition rather than "
                "a tally."
            ),
            trajectory=[
                TrajectoryRule(
                    kind="reported",
                    note="the count per named clause. The English reason is on "
                         "the step's own verdicts, one sentence per job",
                ),
                TrajectoryRule(
                    kind="read",
                    note="one page decides all of it",
                ),
            ],
        ),
        Milestone(
            id="settled",
            label="once the window has closed and the spine has run",
            anchor="terminal",
            note=(
                "There is deliberately no `wrote` milestone on this spec. The "
                "sweep commits no claim and draws no link — it decides what is "
                "in play and emits the facts other flows answer — so a "
                "milestone anchored on a write would be a milestone this flow "
                "can never reach, and the `JobTask` rows in the world "
                "afterwards belong to the extractions it started."
            ),
            trajectory=[
                TrajectoryRule(kind="terminal_status"),
                TrajectoryRule(
                    kind="reported",
                    note="what the window put in play: the extractions it "
                         "raised and the department-days it opened. Both are "
                         "started from inside this run, which is what makes "
                         "this a spine rather than three flows",
                ),
            ],
        ),
    ],
)

PLANT_CASES = [
    EvalCase(
        id="wick/each-exclusion-names-its-own-clause",
        spec_id=PLANT_SPEC.id,
        name="Five jobs excluded, five different governed clauses",
        summary=(
            "A weekend load, a job on Do-Not-Fab, a load date already past, a "
            "production week outside the governed set and a job not released "
            "to the yard. Nine jobs considered, four eligible, and the five "
            "exclusions are one apiece — not a tally of rules broken, but a "
            "partition, because the clauses are read in the page's order and "
            "stop at the first failure."
        ),
        fixture_id="wick/plant-day",
        trigger={"as_of": WICK_AS_OF, "library": WICK_PLANT_LIBRARY},
        expectations=[
            MilestoneExpectation(
                milestone="screened",
                trajectory={
                    "reported": (
                        "considered=9, eligible=4, excluded=5, "
                        "exclusions~'not-a-business-day': 1, "
                        "exclusions~'held': 1, "
                        "exclusions~'load-date-past': 1, "
                        "exclusions~'week-position-out-of-set': 1, "
                        "exclusions~'not-released': 1"
                    ),
                    "read": "knowledge:plant-scheduling-parameters",
                },
            ),
            MilestoneExpectation(
                milestone="settled",
                trajectory={
                    "terminal_status": "succeeded",
                    "reported": (
                        "bound=15, taken=4, deferred=0, raised=4, "
                        "extractions_succeeded=4, extractions_failed=0, "
                        "department_days=40"
                    ),
                },
            ),
        ],
    ),
    EvalCase(
        id="wick/an-empty-eligible-set-raises-nothing",
        spec_id=PLANT_SPEC.id,
        name="Nothing eligible is informational, and raises nothing",
        summary=(
            "A book with two jobs in it and neither of them buildable. The "
            "governed page calls an empty eligible set informational, so the "
            "window says so and stops: no job is put in play, no department "
            "day is opened, and nobody is interrupted. The distinction that "
            "makes this safe is that an empty *book* — a feed that broke — is "
            "a different fact and does reach a desk."
        ),
        fixture_id="wick/plant-day-nothing-eligible",
        trigger={"as_of": WICK_AS_OF},
        expectations=[
            MilestoneExpectation(
                milestone="screened",
                trajectory={
                    "reported": (
                        "considered=2, eligible=0, excluded=2, "
                        "exclusions~'held': 1, "
                        "exclusions~'not-a-business-day': 1"
                    ),
                },
            ),
            MilestoneExpectation(
                milestone="settled",
                trajectory={
                    "terminal_status": "succeeded",
                    "reported": (
                        "raised=0, taken=0, department_days=0, "
                        "extractions_succeeded=0, extractions_failed=0"
                    ),
                },
            ),
        ],
    ),
]


# ---- the shipping list -----------------------------------------------------

EXTRACTION_SPEC = EvalSpec(
    id="shipping-list-extraction",
    flow_id="shipping-list-extraction",
    version=1,
    summary=(
        "Seven pages of a shipping list become ten departments' hours, and "
        "the model is only ever asked what is printed. The arithmetic that "
        "turns a grid into hours is a governed formula, so a figure outside "
        "its department's expected band is a **verdict on a field** — carried "
        "to the one review the run already stops at — rather than a second "
        "gate; a department whose page family is absent is skipped and "
        "counted while the job goes on; the locating call is not made at all "
        "when nothing is in doubt, because a second model call over a page "
        "nobody is arguing about is spend with no question behind it; and a "
        "correction re-runs **both** checks over the corrected reading rather "
        "than being written straight through."
    ),
    milestones=[
        Milestone(
            id="read",
            label="once the page has been read",
            anchor="last_of_kind",
            selector="modelcall",
            note=(
                "The last model call, which on a clean ticket is the only one: "
                "the locate call is conditional, and *that it did not happen* "
                "is the claim."
            ),
            trajectory=[
                TrajectoryRule(
                    kind="model_calls_at_most",
                    note="one for the reading. A second is made only when a "
                         "field a person will be asked about can be boxed on "
                         "the page — never for a computed figure, which has no "
                         "box because nobody printed it",
                ),
                TrajectoryRule(kind="step_absent"),
            ],
        ),
        Milestone(
            id="review",
            label="the first time it stopped for a supervisor",
            anchor="first_of_kind",
            selector="task",
            note=(
                "Declared, and therefore only nameable by a case that expects "
                "the run to stop. A clean ticket never reaches it, which is "
                "the point of the clean case."
            ),
            trajectory=[
                TrajectoryRule(
                    kind="gate_count",
                    note="one. An out-of-band figure and a low-confidence "
                         "field are two findings on one review, not two "
                         "interruptions",
                ),
                TrajectoryRule(kind="queue"),
            ],
        ),
        Milestone(
            id="demand",
            label="once the departments' hours are on the job",
            anchor="wrote",
            selector="graph:JobTask/",
            trajectory=[TrajectoryRule(
                kind="step_ran",
                note="the grid check, on its second pass. Anchored here rather "
                     "than at the end because a milestone carries one claim per "
                     "kind, and the hours check is the other half of the same "
                     "promise",
            )],
            objects=[
                ObjectRule(
                    type="JobTask",
                    fields=[
                        FieldRule(field="department", match="exact"),
                        FieldRule(field="labor_hours", match="approx",
                                  tolerance=0.01),
                        FieldRule(
                            field="out_of_band", match="exact",
                            note="the verdict, on the row. A supervisor "
                                 "approving an unusual figure does not make it "
                                 "an ordinary one",
                        ),
                        FieldRule(field="source_page", match="exact"),
                    ],
                ),
            ],
        ),
        Milestone(
            id="settled",
            label="once the ticket has been answered",
            anchor="terminal",
            trajectory=[
                TrajectoryRule(kind="terminal_status"),
                TrajectoryRule(kind="reported"),
                TrajectoryRule(
                    kind="step_ran",
                    note="a correction re-runs the grid check and the hours "
                         "check over the corrected reading — the whole point "
                         "of correcting rather than overriding",
                ),
            ],
        ),
    ],
)

_WICK_JOB_TICKET = {
    "job_key": "erp-jobs:250553", "job_number": "250553",
    "plant_site": "Mazo", "source": "erp-jobs",
}

EXTRACTION_CASES = [
    EvalCase(
        id="wick/a-clean-ticket-asks-nobody",
        spec_id=EXTRACTION_SPEC.id,
        name="A clean ticket is one model call, ten departments and no question",
        summary=(
            "Seven page families, every computed figure inside its governed "
            "band and every field read confidently. One model call is made, "
            "the locating call is not, nobody is interrupted, and ten "
            "departments' hours land on the job."
        ),
        fixture_id="wick/shipping-list-pages",
        trigger={**_WICK_JOB_TICKET, "library": {"250553": ""}},
        expectations=[
            MilestoneExpectation(
                milestone="read",
                trajectory={"model_calls_at_most": "1", "step_absent": "m2"},
            ),
            MilestoneExpectation(
                milestone="demand",
                objects=[
                    ExpectedObject(
                        type="JobTask", key="erp-jobs:250553:steel-fab",
                        fields={
                            "department": "steel-fab", "labor_hours": 10.76,
                            "out_of_band": "no", "source_page": "C",
                        },
                    ),
                    ExpectedObject(
                        type="JobTask", key="erp-jobs:250553:truss-cut",
                        fields={
                            "department": "truss-cut", "labor_hours": 5.7,
                            "out_of_band": "no", "source_page": "A1",
                        },
                    ),
                ],
            ),
            MilestoneExpectation(
                milestone="settled",
                trajectory={
                    "terminal_status": "succeeded",
                    "reported": (
                        "state=extracted, wrote=True, skipped=False, "
                        "review_rounds=0, out_of_band=[], "
                        "departments_skipped=[]"
                    ),
                },
            ),
        ],
    ),
    EvalCase(
        id="wick/a-department-with-no-grid-is-skipped-and-the-job-is-not",
        spec_id=EXTRACTION_SPEC.id,
        name="An absent page family skips its department, not the job",
        summary=(
            "The trim page is not in this ticket. `trim-fab` gets no hours and "
            "is named as skipped; `hardware-trim-pack`, which is fed by three "
            "pages and has two of them, still does; and the other nine "
            "departments are unaffected. A missing page is a fact about the "
            "paper, not a reason to drop a building."
        ),
        fixture_id="wick/shipping-list-pages",
        trigger={
            **_WICK_JOB_TICKET,
            "library": {"250553": "pages=A1,B,C,D,F,LL"},
        },
        expectations=[
            MilestoneExpectation(
                milestone="demand",
                objects=[
                    ExpectedObject(
                        type="JobTask", key="erp-jobs:250553:trim-fab",
                        absent=True,
                    ),
                    ExpectedObject(
                        type="JobTask",
                        key="erp-jobs:250553:hardware-trim-pack",
                        fields={
                            "department": "hardware-trim-pack",
                            "out_of_band": "no",
                        },
                    ),
                ],
            ),
            MilestoneExpectation(
                milestone="settled",
                trajectory={
                    "terminal_status": "succeeded",
                    "reported": (
                        "state=extracted, wrote=True, "
                        "departments_skipped=['trim-fab'], "
                        "departments_written~hardware-trim-pack"
                    ),
                },
            ),
        ],
    ),
    EvalCase(
        id="wick/out-of-band-hours-are-a-verdict-not-a-second-gate",
        spec_id=EXTRACTION_SPEC.id,
        name="A figure outside its band is one review, and stays out of band",
        summary=(
            "Sixty thousand linear feet of steel is five times an ordinary "
            "job, and the governed formula turns it into forty-five hours "
            "against a band that tops out at twenty-five. That is one finding "
            "on the one review the run stops at — not a second interruption — "
            "and the locating call is skipped, because a computed figure has "
            "no box on the page to draw around. The supervisor signs it off "
            "and the row is written **still marked out of band**: approving an "
            "unusual figure does not make it an ordinary one."
        ),
        fixture_id="wick/shipping-list-pages",
        trigger={**_WICK_JOB_TICKET, "library": {"250553": "steel_lf=60000"}},
        decisions=[ScriptedDecision(
            verdict="approved",
            decided_by=WICK_SUPERVISOR,
            note="Big roof, and the footage is right. Book it.",
        )],
        expectations=[
            MilestoneExpectation(
                milestone="read",
                trajectory={"model_calls_at_most": "1", "step_absent": "m2"},
            ),
            MilestoneExpectation(
                milestone="review",
                trajectory={
                    "gate_count": "1",
                    "queue": "plant-supervisor",
                },
            ),
            MilestoneExpectation(
                milestone="demand",
                objects=[ExpectedObject(
                    type="JobTask", key="erp-jobs:250553:steel-fab",
                    fields={
                        "department": "steel-fab", "labor_hours": 45.8,
                        "out_of_band": "yes", "source_page": "C",
                    },
                )],
            ),
            MilestoneExpectation(
                milestone="settled",
                trajectory={
                    "terminal_status": "succeeded",
                    "reported": (
                        "state=extracted, wrote=True, review_rounds=1, "
                        "out_of_band=['steel-fab'], "
                        f"decided_by={WICK_SUPERVISOR}"
                    ),
                },
            ),
        ],
    ),
    EvalCase(
        id="wick/a-correction-re-runs-both-checks",
        spec_id=EXTRACTION_SPEC.id,
        name="A correction re-runs both checks over the corrected reading",
        summary=(
            "The supervisor reads the footage off the page again and puts the "
            "real figure in. The grid check and the hours check both run again "
            "over the corrected reading — which is what makes a correction a "
            "correction rather than an override — and the department that was "
            "out of band comes back inside it. The row that lands is the one "
            "the arithmetic produced from the corrected page, not the number "
            "the supervisor typed."
        ),
        fixture_id="wick/shipping-list-pages",
        trigger={**_WICK_JOB_TICKET, "library": {"250553": "steel_lf=60000"}},
        decisions=[ScriptedDecision(
            verdict="corrected",
            decided_by=WICK_SUPERVISOR,
            note="Read the footage off the page again — it is twelve thousand.",
            payload={"fields": {
                "c_steel_fab.total_linear_feet": "12000",
                "c_steel_fab.colours[0].linear_feet": "6000",
                "c_steel_fab.colours[1].linear_feet": "6000",
            }},
        )],
        expectations=[
            MilestoneExpectation(
                milestone="demand",
                objects=[ExpectedObject(
                    type="JobTask", key="erp-jobs:250553:steel-fab",
                    fields={
                        "department": "steel-fab", "labor_hours": 10.76,
                        "out_of_band": "no", "source_page": "C",
                    },
                )],
                trajectory={"step_ran": "c1_1"},
            ),
            MilestoneExpectation(
                milestone="settled",
                trajectory={
                    "terminal_status": "succeeded",
                    "step_ran": "c2_1",
                    "reported": (
                        "state=extracted, wrote=True, review_rounds=1, "
                        "out_of_band=[], "
                        f"decided_by={WICK_SUPERVISOR}"
                    ),
                },
            ),
        ],
    ),
]


# ---- the day a department works --------------------------------------------

SCHEDULING_SPEC = EvalSpec(
    id="department-scheduling",
    flow_id="department-scheduling",
    version=1,
    summary=(
        "The one flow where Cumulus is the system of record, and therefore "
        "the one with a central claim rather than a list of properties: "
        "**nothing leaves the graph before a person signs it, and a day is "
        "exportable only once approved.** The fill writes the day at "
        "`pending_approval` and stops there; the export refuses a day in that "
        "state and names the Task standing in its way; only a decision moves "
        "it to `approved`, with the signer and the moment on the record and "
        "the claim carrying their address rather than the flow's. Around that: "
        "the fill stops where a governed number says to — a goal max, or for "
        "the two departments the page names, the point their incentive crosses "
        "zero — the latitude either side of it never reaches past the absolute "
        "max, an unmet upstream dependency ends the day deferred rather than "
        "failed, and the headcount is the count of who is `member_of` the "
        "department rather than a number somebody typed."
    ),
    milestones=[
        Milestone(
            id="written",
            label="once the day has been written",
            anchor="wrote",
            selector="graph:ScheduleDay/",
            note=(
                "The first write, which is the fill. Everything the fill "
                "decided is on the day at this point — and so is the status "
                "that makes it unexportable."
            ),
            objects=[
                ObjectRule(
                    type="ScheduleDay",
                    fields=[
                        FieldRule(field="department", match="exact"),
                        FieldRule(field="schedule_date", match="exact"),
                        FieldRule(field="status", match="exact"),
                        FieldRule(field="total_hours", match="approx",
                                  tolerance=0.01),
                        FieldRule(field="goal_max_hours", match="exact"),
                        FieldRule(field="abs_max_hours", match="exact"),
                        FieldRule(field="fudge_hours", match="exact"),
                        FieldRule(
                            field="headcount", match="exact",
                            note="the count of active people `member_of` the "
                                 "department's Team. The design had a "
                                 "supervisor typing it into a spreadsheet and "
                                 "a bot counting the names",
                        ),
                        FieldRule(
                            field="approved_by", match="absent",
                            note="nobody has signed it yet, and that is the "
                                 "whole claim",
                        ),
                    ],
                    links=[LinkRule(
                        type="has_schedule_entry", to_type="ScheduleEntry",
                        expect="count",
                        note="the rows the fill placed, in load-date order",
                    )],
                ),
            ],
        ),
        Milestone(
            id="proposed",
            label="the moment it stopped for a signature",
            anchor="first_of_kind",
            selector="task",
            note=(
                "The permanent control point. A day sits here, unexportable, "
                "for as long as it takes — and a case that grades this "
                "milestone and no other is a case about a run that has not "
                "finished, which is exactly the state being asserted."
            ),
            objects=[
                ObjectRule(
                    type="ScheduleDay",
                    fields=[
                        FieldRule(
                            field="status", match="exact",
                            note="`pending_approval`. The export answers 409 "
                                 "for a day in this state and names the Task",
                        ),
                        FieldRule(field="approved_by", match="absent"),
                        FieldRule(field="approved_at", match="absent"),
                    ],
                ),
            ],
            trajectory=[
                TrajectoryRule(kind="gate_count"),
                TrajectoryRule(kind="queue"),
                TrajectoryRule(kind="task_raised"),
            ],
        ),
        Milestone(
            id="settled",
            label="once the day has been answered",
            anchor="terminal",
            objects=[
                ObjectRule(
                    type="ScheduleDay",
                    fields=[
                        FieldRule(field="status", match="exact"),
                        FieldRule(field="approved_by", match="exact"),
                        FieldRule(field="approved_at", match="present"),
                        FieldRule(field="headcount", match="exact"),
                        FieldRule(field="total_hours", match="approx",
                                  tolerance=0.01),
                    ],
                ),
            ],
            trajectory=[
                TrajectoryRule(kind="terminal_status"),
                TrajectoryRule(
                    kind="reported",
                    note="where the fill stopped, and which governed number "
                         "stopped it — stated as its own arithmetic, so a "
                         "supervisor can argue with it",
                ),
                TrajectoryRule(
                    kind="claimed_by",
                    note="the signature is the *person's* claim, not the "
                         "flow's. A day approved under `flow:` would be a day "
                         "nobody signed",
                ),
            ],
        ),
    ],
)

#: The days the governed lead-time table dates for this fixture's loads. Not
#: arbitrary: `steel-fab` is fourteen business days back from the last load,
#: `truss-fab` twenty-one, and the sweep opens one day per load date. They are
#: written out because a case has to name the day it is about, and a natural
#: key is `<department slug>:<date>`.
_WICK_STEEL_DAY = "2026-10-02"
_WICK_PACK_DAY = "2026-10-02"
_WICK_TRUSS_FAB_DAY = "2026-09-23"

_WICK_SIGN = ScriptedDecision(
    verdict="approved",
    decided_by=WICK_SUPERVISOR,
    note="Signed — that is the day.",
)

SCHEDULING_CASES = [
    EvalCase(
        id="wick/nothing-leaves-the-graph-before-a-signature",
        spec_id=SCHEDULING_SPEC.id,
        name="The day is written, held at pending_approval, and waits",
        summary=(
            "The central claim, in the state it is actually made: the fill has "
            "run, the day and its rows are in the graph, and the run has "
            "stopped. `status` is `pending_approval`, nobody has signed it, "
            "and the schedule export refuses a day in this state and names the "
            "Task standing in its way. There is no decision in this case on "
            "purpose — a day nobody has answered is the thing being graded."
        ),
        fixture_id="wick/plant-day",
        trigger={
            "as_of": WICK_AS_OF, "department": "steel-fab",
            "schedule_date": _WICK_STEEL_DAY, "library": WICK_PLANT_LIBRARY,
        },
        expectations=[
            MilestoneExpectation(
                milestone="written",
                objects=[ExpectedObject(
                    type="ScheduleDay",
                    key=f"steel-fab:{_WICK_STEEL_DAY}",
                    fields={
                        "department": "steel-fab",
                        "schedule_date": _WICK_STEEL_DAY,
                        "status": "pending_approval",
                        "total_hours": 10.76,
                        "goal_max_hours": 20.0,
                        "abs_max_hours": 22.0,
                        "fudge_hours": 1.0,
                        "headcount": 3.0,
                        "approved_by": "",
                    },
                    link_counts={"has_schedule_entry": 1},
                )],
            ),
            MilestoneExpectation(
                milestone="proposed",
                objects=[ExpectedObject(
                    type="ScheduleDay",
                    key=f"steel-fab:{_WICK_STEEL_DAY}",
                    fields={
                        "status": "pending_approval",
                        "approved_by": "",
                        "approved_at": "",
                    },
                )],
                trajectory={
                    "gate_count": "1",
                    "queue": "plant-supervisor",
                    "task_raised": "plant-supervisor/decision",
                },
            ),
        ],
    ),
    EvalCase(
        id="wick/a-signed-day-carries-the-signature",
        spec_id=SCHEDULING_SPEC.id,
        name="A signed day is approved, and the goal max is what stopped it",
        summary=(
            "The same day, answered. `status` becomes `approved` with the "
            "signer's address and the moment on the record — and the claim "
            "carries *their* address, not the flow's, which is what makes it a "
            "signature. The fill stopped where the governed page said to: "
            "adding the next job would have taken the day past the ceiling the "
            "goal max and its latitude allow, and that ceiling is below the "
            "absolute max rather than beyond it."
        ),
        fixture_id="wick/plant-day",
        trigger={
            "as_of": WICK_AS_OF, "department": "steel-fab",
            "schedule_date": _WICK_STEEL_DAY, "library": WICK_PLANT_LIBRARY,
        },
        decisions=[_WICK_SIGN],
        expectations=[MilestoneExpectation(
            milestone="settled",
            objects=[ExpectedObject(
                type="ScheduleDay",
                key=f"steel-fab:{_WICK_STEEL_DAY}",
                fields={
                    "status": "approved",
                    "approved_by": WICK_SUPERVISOR,
                    "approved_at": "",
                    "headcount": 3.0,
                    "total_hours": 10.76,
                },
            )],
            trajectory={
                "terminal_status": "succeeded",
                "reported": (
                    "state=approved, status=approved, rows=1, "
                    f"approved_by={WICK_SUPERVISOR}, "
                    "capacity_row=no-condenstop, "
                    "goal_max_hours=20.0, abs_max_hours=22.0, "
                    "fudge_hours=1.0, "
                    "stopped_at~past the governed ceiling of 21.0 h"
                ),
                "claimed_by":
                    f"ScheduleDay.approved_by=human:{WICK_SUPERVISOR}",
            },
        )],
    ),
    EvalCase(
        id="wick/the-incentive-crossing-zero-stops-the-fill",
        spec_id=SCHEDULING_SPEC.id,
        name="Hardware & Trim Packaging fills until it is out of the hole",
        summary=(
            "One of the two departments the governed page says the incentive "
            "decides the fill for. Its capacity row has no goal max and no "
            "ceiling at all — as far out as needed — so what stops the day is "
            "the plant's own phrase for filling one: the bonus figure starts "
            "negative and rises with hours, and the fill runs until it crosses "
            "zero. The sentence on the run is the arithmetic itself, which is "
            "the whole reason this is two governed numbers rather than a "
            "formula in a spreadsheet cell."
        ),
        fixture_id="wick/plant-day",
        trigger={
            "as_of": WICK_AS_OF, "department": "hardware-trim-pack",
            "schedule_date": _WICK_PACK_DAY, "library": WICK_PLANT_LIBRARY,
        },
        decisions=[_WICK_SIGN],
        expectations=[MilestoneExpectation(
            milestone="settled",
            objects=[ExpectedObject(
                type="ScheduleDay",
                key=f"hardware-trim-pack:{_WICK_PACK_DAY}",
                fields={
                    "status": "approved",
                    "approved_by": WICK_SUPERVISOR,
                    "approved_at": "",
                    "headcount": 1.0,
                    "total_hours": 29.34,
                },
            )],
            trajectory={
                "terminal_status": "succeeded",
                "reported": (
                    "state=approved, rows=3, capacity_row=any, "
                    "out_of_the_hole=True, below_capacity=False, "
                    "stopped_at~crosses zero at 25.00 h, "
                    "incentive_sentence~$-150.00 + $6.00/h"
                ),
            },
        )],
    ),
    EvalCase(
        id="wick/an-unmet-dependency-ends-the-day-deferred",
        spec_id=SCHEDULING_SPEC.id,
        name="Truss Fabrication waits for the saw, and the day is deferred",
        summary=(
            "The governed sequencing table puts `truss-cut` before "
            "`truss-fab`, and it is read live off each job's own Done flags "
            "rather than off a snapshot. No job on this day has been cut yet, "
            "so nothing is placeable: no day is written, nobody is asked to "
            "approve one, and the run ends `deferred` rather than failed — an "
            "unmet dependency is a fact about Tuesday, not an error."
        ),
        fixture_id="wick/plant-day",
        trigger={
            "as_of": WICK_AS_OF, "department": "truss-fab",
            "schedule_date": _WICK_TRUSS_FAB_DAY,
            "library": WICK_PLANT_LIBRARY,
        },
        expectations=[MilestoneExpectation(
            milestone="settled",
            objects=[ExpectedObject(
                type="ScheduleDay",
                key=f"truss-fab:{_WICK_TRUSS_FAB_DAY}",
                absent=True,
            )],
            trajectory={
                "terminal_status": "succeeded",
                "reported": (
                    "state=deferred, wrote=False, rows=0, deferred=4, "
                    "capacity_row=table-3-man, "
                    "deferrals~'upstream-not-done': 4, "
                    "reason~no share of this department's work could be placed"
                ),
            },
        )],
    ),
]


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

SPECS = [
    AP_SPEC, EXPENSE_SPEC, IDENTITY_SPEC, DISAGREEMENT_SPEC, RELEASE_SPEC,
    # Wick. Six specs over five flows: `field-documents` has two because its
    # two claims are about two different worlds, and a fixture is a world.
    FIELD_DOCUMENTS_SPEC, FIELD_MOVE_SPEC, CREDENTIAL_SPEC, PLANT_SPEC,
    EXTRACTION_SPEC, SCHEDULING_SPEC,
]
CASES = {
    AP_SPEC.id: AP_CASES,
    EXPENSE_SPEC.id: EXPENSE_CASES,
    IDENTITY_SPEC.id: IDENTITY_CASES,
    DISAGREEMENT_SPEC.id: DISAGREEMENT_CASES,
    RELEASE_SPEC.id: RELEASE_CASES,
    FIELD_DOCUMENTS_SPEC.id: FIELD_DOCUMENTS_CASES,
    FIELD_MOVE_SPEC.id: FIELD_MOVE_CASES,
    CREDENTIAL_SPEC.id: CREDENTIAL_CASES,
    PLANT_SPEC.id: PLANT_CASES,
    EXTRACTION_SPEC.id: EXTRACTION_CASES,
    SCHEDULING_SPEC.id: SCHEDULING_CASES,
}


def validate_specs(
    specs: list[EvalSpec] | None = None,
    cases: dict[str, list[EvalCase]] | None = None,
    fixtures: list[Fixture] | None = None,
) -> list[str]:
    """Every type and link a spec, case or fixture names must exist in the TBox.

    The same conformance `validate_mappings` and `validate_collections` do, for
    the third registry that writes ontology names down as data. A misspelled
    type here does not raise — it grades. An `ObjectRule` for `Invoic` is a rule
    nothing can ever satisfy, so the case fails for a reason that reads as a
    flow regression; a `FixtureObject` of an undeclared type is a world that
    silently sets up nothing. Both are caught at declaration time instead.
    """
    problems: list[str] = []
    declared = {o.name for o in ONTOLOGY.objects}
    links = {link.name for link in ONTOLOGY.links}

    def check_type(where: str, name: str) -> None:
        if name and name not in declared:
            problems.append(f"{where}: unknown object type `{name}`")

    for spec in specs if specs is not None else SPECS:
        for milestone in spec.milestones:
            for rule in milestone.objects:
                where = f"spec `{spec.id}` milestone `{milestone.id}`"
                check_type(where, rule.type)
                for link in rule.links:
                    if link.type not in links:
                        problems.append(
                            f"{where}: unknown link type `{link.type}`"
                        )
                    check_type(where, link.to_type)
    for spec_id, spec_cases in (cases if cases is not None else CASES).items():
        for case in spec_cases:
            for expectation in case.expectations:
                for expected in expectation.objects:
                    check_type(
                        f"case `{spec_id}/{case.id}` at "
                        f"`{expectation.milestone}`",
                        expected.type,
                    )
    for fixture in fixtures if fixtures is not None else declared_fixtures():
        for obj in fixture.objects:
            check_type(f"fixture `{fixture.id}`", obj.type)
    return problems


class DeclaredSpecStore:
    """The specs as declared here. A JSON/SDK import path is the next step;
    keeping them in one reviewable file is what matters first."""

    def specs(self) -> list[EvalSpec]:
        return list(SPECS)

    def cases(self, spec_id: str) -> list[EvalCase]:
        return list(CASES.get(spec_id, []))


# ---------------------------------------------------------------------------
# The export
# ---------------------------------------------------------------------------

EVAL_SPEC_ASSET_ID = "flow-eval-specs"


def specs_markdown() -> str:
    fixtures = declared_fixtures()
    lines = [
        "# Flow eval specs",
        "",
        "What each flow is answerable for, and how tightly. A spec is an "
        "overlay on the enterprise ontology: the model is shared, and this "
        "says which parts of it a given flow establishes, how strictly to "
        "compare them, and at which point in a run to look. Authored in Python "
        "(`server/app/eval_specs.py`) and exported here.",
        "",
        "A **milestone** is where in a run to look, and it is a predicate over "
        "the journal rather than a step id: the last step of a given kind, the "
        "step that wrote a given kind of record, the first time the run stopped "
        "for a person, or the end of a finished run. That is deliberate and "
        "load-bearing — flow-level expectations are affixed to the data that "
        "seeds a flow, so LLM calls and other transactions can be added, "
        "removed or rearranged without every case having to be re-authored "
        "against the new step names.",
        "",
        "A milestone the run never reaches **fails**, and the failure names "
        "what the run did instead — the kinds of step it ran, or the records "
        "it wrote. Declaring one is itself the assertion that it is reachable, "
        "so a milestone can never be quietly skipped and a case can never pass "
        "by asserting nothing about a point the run never got to.",
        "",
        "Match rules: `exact` (numbers compare as numbers), `approx` with a "
        "tolerance, `fuzzy` with a similarity floor (for values a model reads "
        "off a page), `at_least` and `at_most` (a bound rather than a value, "
        "for scores whose weights are governed and may be tuned), `one_of`, "
        "`present`, `absent`, and `ignore`. `ignore` is used deliberately — a "
        "due date is the vendor's payment terms, so grading it would be "
        "grading the wrong system.",
        "",
    ]
    for spec in SPECS:
        lines += [
            f"## {spec.id} (`{spec.flow_id}`, v{spec.version})",
            "",
            spec.summary,
            "",
            f"Never graded: {', '.join(f'`{f}`' for f in spec.ignore_fields)}.",
            "",
        ]
        for milestone in spec.milestones:
            lines += [
                f"### Milestone `{milestone.id}` — {milestone.label}",
                "",
                f"Resolved at **{milestone.describe()}** — a predicate over "
                "the run's journal, never a step id.",
                "",
            ]
            if milestone.note:
                lines += [milestone.note, ""]
            for rule in milestone.objects:
                lines.append(f"**{rule.type}**")
                lines.append("")
                for field in rule.fields:
                    detail = f" — {field.note}" if field.note else ""
                    tolerance = (
                        f" ±{field.tolerance}" if field.match in ("approx", "fuzzy")
                        else ""
                    )
                    options = (
                        f" ({', '.join(field.options)})" if field.options else ""
                    )
                    lines.append(
                        f"- `{field.field}`: {field.match}{tolerance}{options}{detail}"
                    )
                for link in rule.links:
                    detail = f" — {link.note}" if link.note else ""
                    lines.append(
                        f"- edge `{link.type}` ({link.direction}): "
                        f"{link.expect}{detail}"
                    )
                lines.append("")
            if milestone.trajectory:
                lines.append("**Trajectory**")
                lines.append("")
                for rule in milestone.trajectory:
                    detail = f" — {rule.note}" if rule.note else ""
                    expected = f" `{rule.expected}`" if rule.expected else ""
                    lines.append(f"- `{rule.kind}`{expected}{detail}")
                lines.append("")
        cases = CASES.get(spec.id, [])
        lines += [f"### Cases ({len(cases)})", ""]
        for case in cases:
            lines.append(f"- **{case.name}** (`{case.id}`)")
            if case.summary:
                lines.append(f"  - {case.summary}")
            if case.fixture_id:
                lines.append(f"  - world: `{case.fixture_id}`")
        lines.append("")
        wanted = {c.fixture_id for c in cases}
        used = [f for f in fixtures if f.id in wanted]
        if used:
            lines += [
                "### Fixtures",
                "",
                "The worlds those cases run in. A world is named and shared, so "
                "two cases over the same one reference a single declaration "
                "rather than carrying a copy each — and the pinned reads say "
                "which governed values a result is comparable against.",
                "",
            ]
            for fixture in used:
                lines.append(f"- `{fixture.id}` — {fixture.name}")
                if fixture.summary:
                    lines.append(f"  - {fixture.summary}")
                if fixture.read_set:
                    pins = ", ".join(f"`{ref}`" for ref in fixture.read_set)
                    lines.append(f"  - pinned reads: {pins}")
            lines.append("")
    return "\n".join(lines)


def specs_asset_fields() -> dict:
    return {
        "id": EVAL_SPEC_ASSET_ID,
        "path": "platform/evals/flow-specs",
        "title": "Flow eval specs",
        "kind": "schema-export",
        "owner": "platform-eng",
        "tags": ["evals", "governed-knowledge", "testing"],
        "status": "published",
        "content": specs_markdown(),
    }
