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

DECLARED_IN = "server/app/eval_specs.py"

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
        {"source": "wms-receiving", "receiving_reports": [_receipt(short)]},
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

#: sku -> (description, unit price). Held here rather than read out of
#: `payables.CATALOG` so the fixture cannot drift when the stub reader's
#: catalogue is edited: a graded world is a frozen thing.
AP_CATALOG: dict[str, tuple[str, float]] = {
    "STL-0250": ("Hot-rolled steel plate, 1/4 in", 486.00),
    "FAS-M12": ("Grade 8 hex bolt, M12 x 60mm, box of 100", 128.75),
    "WLD-6013": ("Welding rod E6013, 5kg", 92.40),
    "PPE-GLV": ("Cut-resistant gloves, size L, 12 pair", 64.20),
    "CNS-HYD": ("Hydraulic fluid ISO 46, 20L drum", 214.90),
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
            "source": "wms-receiving",
            "watermark": "2026-08-12T00:00:00Z",
            "idempotency_key": "wms-001",
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
        name="Known vendor, bills its PO exactly, posts without a human",
        summary=(
            "The happy path, and the one that regresses silently: the vendor "
            "is in the master, the lines match the order and the receipt, and "
            "the total is under the governed limit."
        ),
        fixture_id="ap/acme-po-7741-clean",
        trigger={
            "submitted_by": "connie.alberts@naviant.com",
            "filename": "acme.pdf",
            "hint": "vendor=acme-industrial po=PO-7741 invoice=ACME-INV-1 "
                    "lines=STL-0250:2,FAS-M12:3",
        },
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
                trajectory={"terminal_status": "succeeded", "gate_count": "0"},
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
            "rule. If this ever passes with one gate and no second match, the "
            "correction path has quietly become an override."
        ),
        fixture_id="ap/acme-po-7741-clean",
        trigger={
            "submitted_by": "connie.alberts@naviant.com",
            "filename": "acme.pdf",
            "hint": "vendor=acme-industrial po=PO-7741 invoice=ACME-INV-2 "
                    "lines=STL-0250:2,FAS-M12:3 pricebump",
        },
        decisions=[ScriptedDecision(
            verdict="corrected",
            note="Vendor billed the old list price; corrected to the PO price.",
            payload={"lines": [{"ordinal": 1, "unit_price_usd": 486.00}]},
        )],
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
                trajectory={"terminal_status": "succeeded", "gate_count": "1"},
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
                "queue": "ap-clerk",
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
                    "queue": "ap-clerk",
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
                        # Counted, not asserted to exist: an auto-approved
                        # request has no decider, and that is correct.
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
        id="expense-in-policy-auto-approves",
        spec_id=EXPENSE_SPEC.id,
        name="An in-policy receipt with a stated purpose needs nobody",
        fixture_id="expense/felicia-on-the-roster",
        trigger={
            "employee": "felicia.marks@naviant.com",
            "commentary": "Team lunch after the Acme Phase 1 review.",
            "hint": "total=64.20 category=meals vendor=corner-bistro",
        },
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
                    link_counts={"has_expense_line": 1, "decided_by": 0},
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
            trajectory={"terminal_status": "succeeded", "gate_count": "0"},
        )],
    ),
    EvalCase(
        id="expense-submitter-claims-part-of-a-receipt",
        spec_id=EXPENSE_SPEC.id,
        name="The submitter claims $75 of a $110 receipt, and that stands",
        summary=(
            "The commentary is an input, not a note filed beside one. Claiming "
            "less than the paper is legitimate and must not need a human; the "
            "two amounts are kept apart so the difference stays legible."
        ),
        fixture_id="expense/felicia-on-the-roster",
        trigger={
            "employee": "felicia.marks@naviant.com",
            "commentary": "Client dinner, Acme Phase 2.",
            "documents": [{
                "filename": "dinner.jpg",
                "hint": "total=110.00 category=meals vendor=corner-bistro",
                "note": (
                    "Dinner with the Acme team after the Phase 2 review. I am "
                    "only requesting $75 of the $110 — I ordered wine on my "
                    "own tab."
                ),
            }],
        },
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
            trajectory={"terminal_status": "succeeded", "gate_count": "0"},
        )],
    ),
    EvalCase(
        id="expense-compound-receipt-corrected",
        spec_id=EXPENSE_SPEC.id,
        name="A compound receipt becomes two lines a reviewer can trim",
        summary=(
            "Two facts out of one document, then a correction that re-runs the "
            "Rule. `origin: human` on both lines is what proves the reviewer's "
            "values were applied rather than logged."
        ),
        fixture_id="expense/john-on-the-roster",
        trigger={
            "employee": "john.smith@naviant.com",
            "commentary": "Two nights at the Acme onsite.",
            "hint": "compound total=210.00 category=lodging vendor=harbor-hotel",
        },
        decisions=[ScriptedDecision(
            verdict="corrected",
            note="Trimmed the parking line to the reimbursable rate.",
            payload={"lines": [
                {"ordinal": 1, "purpose": "Two nights at the Acme onsite."},
                {"ordinal": 2, "requested_usd": 60.0, "category": "travel",
                 "purpose": "Parking at the onsite, capped rate."},
            ]},
        )],
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
            trajectory={"terminal_status": "succeeded", "gate_count": "1"},
        )],
    ),
    EvalCase(
        id="expense-restricted-category-denied",
        spec_id=EXPENSE_SPEC.id,
        name="A restricted category is always reviewed, and can be denied",
        fixture_id="expense/nathan-on-the-roster",
        trigger={
            "employee": "nathan.pryce@naviant.com",
            "commentary": "Client gift wine for the Acme close.",
            "hint": "total=96.40 category=alcohol vendor=vintners-cellar",
        },
        decisions=[ScriptedDecision(
            verdict="rejected",
            note="Out of policy §4.2 — use the gifting desk.",
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
                "queue": "expense-review",
            },
        )],
    ),
]


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

SPECS = [AP_SPEC, EXPENSE_SPEC]
CASES = {AP_SPEC.id: AP_CASES, EXPENSE_SPEC.id: EXPENSE_CASES}


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
        "off a page), `one_of`, `present`, `absent`, and `ignore`. `ignore` is "
        "used deliberately — a due date is the vendor's payment terms, so "
        "grading it would be grading the wrong system.",
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
        "scope": "org",
        "owner": "platform-eng",
        "tags": ["evals", "governed-knowledge", "testing"],
        "status": "published",
        "content": specs_markdown(),
    }
