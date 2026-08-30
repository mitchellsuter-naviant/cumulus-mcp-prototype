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
# The fixtures
# ---------------------------------------------------------------------------
#
# Built by a function rather than held as module constants because the demo
# world's deliveries come from `payables.py`, which imports its way back here
# through the world builder. Nothing needs them at import time.


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
            id="expense/pat-on-the-roster",
            name="Pat, on the roster",
            summary=(
                "One Employee and nothing else. Org membership is a member_of "
                "link rather than a field, so a fixture claiming `team` here "
                "would be re-smuggling the string the ontology just retired."
            ),
            objects=[FixtureObject(
                type="Employee", key="pat@customer.example",
                props={
                    "email": "pat@customer.example", "title": "Field Engineer",
                },
                label="pat@customer.example",
            )],
            **expense,
        ),
        Fixture(
            id="expense/kim-on-the-roster",
            name="Kim, on the roster",
            summary="One Employee, for the compound receipt a reviewer trims.",
            objects=[FixtureObject(
                type="Employee", key="kim@customer.example",
                props={"email": "kim@customer.example"},
                label="kim@customer.example",
            )],
            **expense,
        ),
        Fixture(
            id="expense/sam-on-the-roster",
            name="Sam, on the roster",
            summary="One Employee, for the restricted category that is denied.",
            objects=[FixtureObject(
                type="Employee", key="sam@customer.example",
                props={"email": "sam@customer.example"},
                label="sam@customer.example",
            )],
            **expense,
        ),
        _identity_world(),
        _demo_world(),
    ]


def _demo_world() -> Fixture:
    """The world every seeded run stands on, as one fragment.

    It belongs to no flow — it is what the whole prototype is set on — so its
    `flow_id` is empty rather than guessed at.
    """
    from .payables import (
        _seed_po_delivery,
        _seed_receiving_delivery,
        _seed_vendor_deliveries,
    )

    return Fixture(
        id="seed/demo-world",
        name="Demo world",
        summary=(
            "The vendors, purchase orders and receiving reports every seeded "
            "run stands on — including a byte-identical redelivery, a "
            "cross-system match that auto-links, and a misspelling that waits "
            "for a steward."
        ),
        deliveries=[
            *_seed_vendor_deliveries(),
            _seed_po_delivery(),
            _seed_receiving_delivery(),
        ],
        origin="declared",
        derived_from="server/app/payables.py",
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
            "submitted_by": "ap-intake@customer.example",
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
            "submitted_by": "ap-intake@customer.example",
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
            "submitted_by": "ap-intake@customer.example",
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
            "submitted_by": "ap-intake@customer.example",
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
            "submitted_by": "ap-intake@customer.example",
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
        fixture_id="expense/pat-on-the-roster",
        trigger={
            "employee": "pat@customer.example",
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
                        "employee_email": "pat@customer.example",
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
        fixture_id="expense/pat-on-the-roster",
        trigger={
            "employee": "pat@customer.example",
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
        fixture_id="expense/kim-on-the-roster",
        trigger={
            "employee": "kim@customer.example",
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
        fixture_id="expense/sam-on-the-roster",
        trigger={
            "employee": "sam@customer.example",
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


def _identity_world() -> Fixture:
    return Fixture(
        id="identity/naviant-answer-key",
        flow_id="identity-sweep",
        name="The estate's answer key, as its feeds delivered it",
        summary=(
            "Nine CRM accounts, three contacts, four ERP customers and five "
            "ERP vendors, recorded from the live estate. Holds every duplicate "
            "the namespace design plants across two books, both bait pairs, "
            "and two pairs of merely-similar neighbours — so a suite can fail "
            "for proposing too little and for proposing too much."
        ),
        deliveries=[_IDENTITY_CRM, _IDENTITY_ERP_SALES, _IDENTITY_ERP_VENDORS],
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
            "name it happens to be. Different domains, different area codes, "
            "opposite sides of the ledger. Verona Foods and the two school "
            "districts are in the same case on purpose: a must-not-link suite "
            "that a sweep proposing nothing at all would pass is not testing "
            "anything, so this case fails both ways round."
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
                    type="Company", key="Kestrel Credit Union",
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
]


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

SPECS = [AP_SPEC, EXPENSE_SPEC, IDENTITY_SPEC]
CASES = {
    AP_SPEC.id: AP_CASES,
    EXPENSE_SPEC.id: EXPENSE_CASES,
    IDENTITY_SPEC.id: IDENTITY_CASES,
}


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
