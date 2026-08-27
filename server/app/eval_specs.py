"""The eval specs and cases, per flow.

Declared in Python and exported to the map as governed knowledge, the same way
the ontology and the Collection registry are — a spec is a declarative config
that decides what "correct" means, so it belongs in the map rather than in
application code.

Read the specs as **contracts**: what the accounts-payable flow promises about
an Invoice, how tightly, and at which point in a run. The cases below are
deliberately few and each exists to pin one behaviour that would otherwise
regress silently — the vendor resolving before any extraction happens, the
match re-running after a correction rather than being overridden, the flywheel
turning a second invoice into a one-shot read.
"""

from __future__ import annotations

from .evals import (
    Checkpoint,
    EvalCase,
    EvalSpec,
    ExpectedObject,
    CheckpointExpectation,
    FieldRule,
    FixtureObject,
    GraphFixture,
    LinkRule,
    ObjectRule,
    ScriptedDecision,
    TrajectoryRule,
)

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


def _ap_world(short: float = 0.0, vendors: list[dict] | None = None) -> GraphFixture:
    return GraphFixture(deliveries=[
        {"source": "erp-vendors", "watermark": "2026-08-01T00:00:00Z",
         "vendors": vendors if vendors is not None else [_ACME, _NORTHWIND]},
        {"source": "erp-purchasing", "purchase_orders": [_PO_7741]},
        {"source": "wms-receiving", "receiving_reports": [_receipt(short)]},
    ])


# ---------------------------------------------------------------------------
# Accounts payable
# ---------------------------------------------------------------------------

AP_SPEC = EvalSpec(
    id="accounts-payable-core",
    flow_id="accounts-payable",
    version=1,
    summary=(
        "What the accounts-payable flow is answerable for: naming the right "
        "vendor before it extracts anything, reading the commercial content of "
        "the document, retaining a three-way match, and posting only what the "
        "governed policy allows. Fields the vendor controls rather than the "
        "flow — due dates, currency — are ignored on purpose: grading them "
        "would be grading the wrong system."
    ),
    checkpoints=[
        Checkpoint(
            id="vendor-resolved",
            after_step="z1",
            label="after resolve-vendor",
            note=(
                "An ordering promise, not just an outcome: the vendor is "
                "settled before a single extraction runs, because extracting "
                "against the wrong exemplar is worse than not extracting."
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
                    kind="step_ran", expected="m1",
                    note="the classifier reads candidate names off the page",
                ),
                TrajectoryRule(
                    kind="step_absent", expected="m2",
                    note="no extraction before the vendor is known",
                ),
                TrajectoryRule(
                    kind="step_absent", expected="m2a",
                    note="…and no ensemble either",
                ),
                TrajectoryRule(kind="model_calls_at_most", expected="1"),
            ],
        ),
        Checkpoint(
            id="committed",
            after_step="a1",
            label="once the invoice is posted",
            note=(
                "The flow writes the graph once, at commit. A run held at a "
                "gate never reaches this checkpoint, so declaring it in a case "
                "*is* the assertion that this invoice should have posted — and "
                "a case for a held invoice asserts the entities are absent "
                "instead, which pins the rule that nothing is committed until "
                "the gate is answered."
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
        Checkpoint(
            id="outcome",
            after_step="final",
            label="however the run ended",
            note=(
                "Applies to every case, committed or held. The object rules "
                "are here too so a held case can assert the entities are "
                "*absent*."
            ),
            objects=[
                ObjectRule(type="Invoice"),
                ObjectRule(type="MatchSet"),
            ],
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
            "The happy path, and the one that regresses silently: an exemplar "
            "exists, the lines match the order and the receipt, the total is "
            "under the governed limit."
        ),
        fixture=_ap_world(),
        trigger={
            "submitted_by": "ap-intake@customer.example",
            "filename": "acme.pdf",
            "hint": "vendor=acme-industrial po=PO-7741 invoice=ACME-INV-1 "
                    "lines=STL-0250:2,FAS-M12:3",
        },
        expectations=[
            CheckpointExpectation(
                checkpoint="vendor-resolved",
                objects=[ExpectedObject(
                    type="Vendor", key="Acme Industrial Inc.",
                    fields={
                        "name": "Acme Industrial Inc.",
                        "slug": "acme-industrial",
                        "status": "active",
                    },
                )],
            ),
            CheckpointExpectation(
                checkpoint="committed",
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
            CheckpointExpectation(
                checkpoint="outcome",
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
        fixture=_ap_world(),
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
            CheckpointExpectation(
                checkpoint="committed",
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
            CheckpointExpectation(
                checkpoint="outcome",
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
        fixture=_ap_world(short=1.0),
        trigger={
            "submitted_by": "ap-intake@customer.example",
            "filename": "acme.pdf",
            "hint": "vendor=acme-industrial po=PO-7741 invoice=ACME-INV-3 "
                    "lines=STL-0250:2,FAS-M12:3",
        },
        expectations=[CheckpointExpectation(
            checkpoint="outcome",
            objects=[
                # Nothing is committed until the gate is answered. Asserting
                # the absence is the point.
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
        name="An unknown vendor runs the ensemble and is minted onboarding",
        summary=(
            "The resolver's other branch. Nothing in the vendor master "
            "resembles this name, so it is minted rather than matched — and it "
            "stays `onboarding` until a human vouches for it."
        ),
        fixture=_ap_world(),
        trigger={
            "submitted_by": "ap-intake@customer.example",
            "filename": "mer.pdf",
            "hint": "newvendor=meridian-supply nopo invoice=MER-1 "
                    "lines=PPE-GLV:2 total=190.00",
        },
        expectations=[
            CheckpointExpectation(
                checkpoint="vendor-resolved",
                objects=[ExpectedObject(
                    type="Vendor", key="Meridian Supply Ltd.",
                    fields={"status": "onboarding", "slug": "meridian-supply"},
                )],
            ),
            CheckpointExpectation(
                checkpoint="outcome",
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
        id="ap-flywheel-second-invoice-is-one-shot",
        spec_id=AP_SPEC.id,
        name="A verified exemplar turns the next invoice into one pass",
        summary=(
            "The flywheel, asserted rather than asserted-about. The clerk "
            "verifies the ensemble, the exemplar is written, and this run — the "
            "same vendor's second invoice — takes the one-shot path. `m2a` "
            "absent is the whole assertion."
        ),
        fixture=GraphFixture(
            deliveries=_ap_world().deliveries,
            objects=[FixtureObject(
                type="Vendor", key="Meridian Supply Ltd.",
                props={
                    "name": "Meridian Supply Ltd.",
                    "slug": "meridian-supply",
                    "status": "active",
                },
                label="Meridian Supply Ltd.",
            )],
        ),
        trigger={
            "submitted_by": "ap-intake@customer.example",
            "filename": "mer2.pdf",
            "hint": "newvendor=meridian-supply nopo invoice=MER-2 "
                    "lines=PPE-GLV:1 total=112.00",
        },
        decisions=[
            ScriptedDecision(verdict="approved", note="Fields verified."),
            ScriptedDecision(verdict="approved", note="No PO at this size."),
        ],
        expectations=[
            CheckpointExpectation(
                checkpoint="committed",
                objects=[ExpectedObject(
                    type="Invoice", key="meridian-supply:MER-2",
                    fields={"state": "posted", "total_usd": 112.00},
                )],
            ),
            CheckpointExpectation(
                checkpoint="outcome",
                trajectory={"terminal_status": "succeeded"},
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
    checkpoints=[
        Checkpoint(
            id="recorded",
            after_step="final",
            label="at the end of the run",
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


_PAT = FixtureObject(
    type="Employee", key="pat@customer.example",
    props={"email": "pat@customer.example", "team": "delivery"},
    label="pat@customer.example",
)


EXPENSE_CASES = [
    EvalCase(
        id="expense-in-policy-auto-approves",
        spec_id=EXPENSE_SPEC.id,
        name="An in-policy receipt with a stated purpose needs nobody",
        fixture=GraphFixture(objects=[_PAT]),
        trigger={
            "employee": "pat@customer.example",
            "commentary": "Team lunch after the Acme Phase 1 review.",
            "hint": "total=64.20 category=meals vendor=corner-bistro",
        },
        expectations=[CheckpointExpectation(
            checkpoint="recorded",
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
        fixture=GraphFixture(objects=[_PAT]),
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
        expectations=[CheckpointExpectation(
            checkpoint="recorded",
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
        fixture=GraphFixture(objects=[FixtureObject(
            type="Employee", key="kim@customer.example",
            props={"email": "kim@customer.example"},
            label="kim@customer.example",
        )]),
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
        expectations=[CheckpointExpectation(
            checkpoint="recorded",
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
        fixture=GraphFixture(objects=[FixtureObject(
            type="Employee", key="sam@customer.example",
            props={"email": "sam@customer.example"},
            label="sam@customer.example",
        )]),
        trigger={
            "employee": "sam@customer.example",
            "commentary": "Client gift wine for the Acme close.",
            "hint": "total=96.40 category=alcohol vendor=vintners-cellar",
        },
        decisions=[ScriptedDecision(
            verdict="rejected",
            note="Out of policy §4.2 — use the gifting desk.",
        )],
        expectations=[CheckpointExpectation(
            checkpoint="recorded",
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
    lines = [
        "# Flow eval specs",
        "",
        "What each flow is answerable for, and how tightly. A spec is an "
        "overlay on the enterprise ontology: the model is shared, and this "
        "says which parts of it a given flow establishes, how strictly to "
        "compare them, and at which point in a run to look. Authored in Python "
        "(`server/app/eval_specs.py`) and exported here.",
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
        for checkpoint in spec.checkpoints:
            lines += [
                f"### Checkpoint `{checkpoint.id}` — {checkpoint.label}",
                "",
                f"Evaluated once step `{checkpoint.after_step}` has completed.",
                "",
            ]
            if checkpoint.note:
                lines += [checkpoint.note, ""]
            for rule in checkpoint.objects:
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
            if checkpoint.trajectory:
                lines.append("**Trajectory**")
                lines.append("")
                for rule in checkpoint.trajectory:
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
