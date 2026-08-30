"""The Flow→Ontology projection: what a flow touches, read off its own runs.

The properties worth defending here are the ones that produce a plausible but
wrong answer when they break. Both of the traps are silent: a parser that
insists on `@rev` loses the AP flow's only graph read without failing, and an
aggregation over the run-level roll-up rather than the steps reports a flow as
reading nothing when it read a governed policy. Neither shows up as an error —
only as a projection that looks reasonable and is not true.
"""

from __future__ import annotations

import pytest

from app.estate import (
    _build_crm_book,
    _build_dms_library,
    _build_erp_sales,
    _build_hris_employment_events,
    _build_office_mail,
)
from app.flow_projection import SCAN_LIMIT, project_flow, project_flows
from app.graph import parse_ref
from app.payables import seed_payables_if_empty
from app.reimbursements import seed_reimbursements_if_empty
from app.runs import InMemoryRunStore, RunEngine, RunRecord, StepRecord
from app.tasks import InMemoryTaskDirectory, TaskService
from app.calendars import seed_calendars_if_empty
from app.workforce import seed_workforce_if_empty
from tests import estate_fixtures as fx
from tests import world as test_world


class _Access:
    """The connector's estate handle with the network removed.

    Only `url` is answered — the DMS build needs an address for a
    `storage_ref` and nothing here dereferences, which is the contract those
    two feeds declare.
    """

    def __call__(self, resource: str, record_id: str) -> dict:
        return {}

    def url(self, resource: str, record_id: str, suffix: str = "") -> str:
        return (
            "https://cumulus-demo-mitchell-suter-dms.web.app/api/naviant/v1/"
            f"{resource}/{record_id}{suffix}"
        )


_access = _Access()


# ---------------------------------------------------------------------------
# The ref parser
# ---------------------------------------------------------------------------


def test_a_knowledge_ref_parses_with_a_revision_and_without_one():
    # Both forms are real. The second is what a flow records when the governed
    # asset it went looking for is not there — the read most worth keeping.
    pinned = parse_ref("knowledge:invoice-posting-policy@rev1")
    bare = parse_ref("knowledge:invoice-posting-policy")

    assert pinned.namespace == bare.namespace == "knowledge"
    assert pinned.id == bare.id == "invoice-posting-policy"
    assert pinned.revision == 1
    assert bare.revision == 0


def test_a_graph_ref_parses_with_a_minted_id_and_with_a_natural_key():
    minted = parse_ref("graph:Invoice/invoice_0478a48f003b@rev2")
    assert (minted.namespace, minted.type, minted.id, minted.revision) == (
        "graph", "Invoice", "invoice_0478a48f003b", 2,
    )

    # No minted id and no revision: the AP flow reads a purchase order by the
    # number printed on the invoice, which is all it has.
    printed = parse_ref("graph:PurchaseOrder/PO-7741")
    assert (printed.namespace, printed.type, printed.id, printed.revision) == (
        "graph", "PurchaseOrder", "PO-7741", 0,
    )


def test_link_and_document_refs_parse():
    link = parse_ref("link:billed_against/link_7e9d6d172a8d")
    assert (link.namespace, link.type, link.id) == (
        "link", "billed_against", "link_7e9d6d172a8d",
    )

    doc = parse_ref("document:doc_05df949871fdcd4f")
    assert (doc.namespace, doc.type, doc.id) == (
        "document", "", "doc_05df949871fdcd4f",
    )


@pytest.mark.parametrize("ref", ["", "run_ap_seed01", "task_abc", "graph:", "::"])
def test_the_parser_is_total(ref):
    # These are read off stored journals that outlive the code that wrote
    # them, so nothing here may raise.
    assert parse_ref(ref).raw == ref


# ---------------------------------------------------------------------------
# Synthetic runs: the states, the cap, the memo, and the regression
# ---------------------------------------------------------------------------


def _engine() -> RunEngine:
    return RunEngine(InMemoryRunStore(), TaskService(InMemoryTaskDirectory()))


def _run(
    flow_id: str,
    run_id: str,
    *,
    refs: tuple[str, ...] = (),
    writes: tuple[str, ...] = (),
    run_refs: tuple[str, ...] = (),
    at: str = "2026-08-01T00:00:00+00:00",
) -> RunRecord:
    return RunRecord(
        id=run_id,
        flow_id=flow_id,
        refs=list(run_refs),
        started_at_iso=at,
        updated_at_iso=at,
        steps=[StepRecord(
            step_id="z1", name="resolve-records", kind="resolve",
            refs=list(refs), writes=list(writes),
        )],
    )


def test_a_step_read_counts_when_the_run_level_roll_up_is_empty():
    """The regression that matters most.

    `run.refs` is a convenience roll-up and is only ever as complete as the
    passes that appended to it; `step.refs` is where the read is recorded.
    Aggregating the former reports a sync flow as reading nothing, which is
    false — vendor-sync's `z1` reads the governed resolution policy.
    """
    engine = _engine()
    engine.store.put(_run(
        "vendor-sync", "run_sync_x",
        refs=("knowledge:entity-resolution-policy@rev1",),
        writes=("graph:Vendor/vendor_ab12@rev2",),
        run_refs=(),  # empty on purpose
    ))

    projection = project_flow(engine, "vendor-sync")
    assert projection.knowledge == ["entity-resolution-policy"]


def test_a_flow_with_no_runs_is_never_ran_rather_than_empty():
    projection = project_flow(_engine(), "vendor-profile-refresh")
    assert projection.state == "never_ran"
    assert projection.total_runs == 0
    assert projection.writes == []


def test_a_flow_that_only_reads_is_ran_but_wrote_nothing():
    engine = _engine()
    engine.store.put(_run(
        "read-only", "run_1", refs=("knowledge:invoice-posting-policy@rev1",),
    ))

    projection = project_flow(engine, "read-only")
    # Same empty `writes` as the state above, and the opposite fact: this one
    # is known to write nothing rather than unknown.
    assert projection.state == "ran_but_wrote_nothing"
    assert projection.writes == []
    assert projection.knowledge == ["invoice-posting-policy"]


def test_a_flow_that_wrote_is_observed():
    engine = _engine()
    engine.store.put(_run(
        "accounts-payable", "run_1",
        writes=("graph:Invoice/invoice_1@rev2", "link:issued/link_1"),
    ))

    projection = project_flow(engine, "accounts-payable")
    assert projection.state == "observed"
    assert projection.writes == ["Invoice"]
    assert projection.establishes == ["issued"]


def test_the_scan_cap_is_bounded_and_reported():
    engine = _engine()
    for i in range(SCAN_LIMIT + 5):
        # Newest first once sorted, so the lowest indices survive the window.
        engine.store.put(_run(
            "busy", f"run_{i:04d}",
            writes=("graph:Invoice/invoice_1@rev2",),
            at=f"2026-08-01T00:00:{SCAN_LIMIT + 5 - i:04d}+00:00",
        ))
    # One run outside the window writes a type nothing inside it writes.
    engine.store.put(_run(
        "busy", "run_ancient",
        writes=("graph:MatchSet/matchset_1@rev2",),
        at="2020-01-01T00:00:00+00:00",
    ))

    projection = project_flow(engine, "busy")
    assert projection.scanned_runs == SCAN_LIMIT
    assert projection.total_runs == SCAN_LIMIT + 6
    assert projection.truncated is True
    # Visibly truncated rather than quietly wrong: the cap really bounds the
    # scan, so the type only the oldest run wrote is genuinely absent.
    assert projection.writes == ["Invoice"]


def test_appending_a_run_invalidates_the_memo():
    engine = _engine()
    engine.store.put(_run("f", "run_1", writes=("graph:Invoice/invoice_1@rev2",)))

    first = project_flow(engine, "f")
    assert project_flow(engine, "f") is first  # unchanged run set, cached

    engine.store.put(_run(
        "f", "run_2", writes=("graph:MatchSet/matchset_1@rev2",),
        at="2026-08-02T00:00:00+00:00",
    ))
    second = project_flow(engine, "f")
    assert second is not first
    assert second.writes == ["Invoice", "MatchSet"]
    assert second.total_runs == 2


# ---------------------------------------------------------------------------
# The seeded world: seven flows, and the declared-vs-observed diff
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def seeded():
    """A world with every flow that reaches the graph having actually run.

    Built rather than installed: nothing here goes through the API, so there
    is no reason to swap the shared `deps` singletons out from under the other
    test modules.

    The commerce and mail feeds have no seed helper — nothing fictional stands
    in for them, by design — so they are driven from the recorded estate
    shapes, the same rows `test_sync_commerce.py` and `test_sync_office.py`
    use. Without that they would be registered flows with no runs, and this
    module's whole claim is about flows that have actually touched the graph.
    """
    world = test_world.build()
    seed_payables_if_empty(world.payables, sync=world.sync)
    seed_workforce_if_empty(world.sync, world.graph)
    seed_calendars_if_empty(world.sync, world.graph)
    seed_reimbursements_if_empty(world.expenses)
    for build, rows in (
        (_build_crm_book, {"accounts": fx.ACCOUNTS, "contacts": fx.CONTACTS,
                           "opportunities": fx.OPPORTUNITIES}),
        (_build_erp_sales, {"customers": fx.CUSTOMERS, "items": fx.ITEMS,
                            "salesOrders": fx.SALES_ORDERS}),
        (_build_office_mail, {"mailboxes": fx.MAILBOXES,
                              "messages": fx.MAIL_MESSAGES}),
        (_build_hris_employment_events,
         {"employmentEvents": fx.EMPLOYMENT_EVENTS}),
        (_build_dms_library,
         {"folders": fx.FOLDERS, "documents": fx.DMS_DOCUMENTS}),
    ):
        world.sync.deliver(
            build(rows, _access), actor="system:test"
        )
    # And the one flow no source pushes: it reads the book the deliveries
    # above wrote, so it goes last.
    world.sync.sweep_identities(actor="system:test")
    return world


def _by_id(world) -> dict:
    return {p.flow_id: p for p in project_flows(world.engine)}


def test_every_flow_that_reaches_the_graph_is_projected(seeded):
    projections = {p.flow_id: p for p in project_flows(seeded.engine)}
    assert set(projections) == {
        "accounts-payable",
        "expense-reimbursement",
        "vendor-sync",
        "purchase-order-sync",
        "receiving-sync",
        "workforce-sync",
        "calendar-sync",
        "crm-sync",
        "sales-sync",
        "mail-sync",
        "employment-event-sync",
        "dms-sync",
        "identity-sweep",
    }
    # Ten of the thirteen are inbound syncs. They all have FlowDefinitions now,
    # but the projection is still drawn from the engine and from stored runs:
    # a definition says what a flow is *meant* to touch, and this module is
    # about what it did.
    for projection in projections.values():
        assert projection.state == "observed", projection.flow_id
        if projection.flow_id == "employment-event-sync":
            # The one flow that establishes no link, and the projection is
            # right to say so: it lands the employment ledger and asserts
            # nothing about anybody. A projection that quietly excused an
            # empty `establishes` would stop being able to tell "this flow
            # draws no edges" from "this flow was never read".
            assert projection.writes
            assert projection.establishes == []
            continue
        if projection.flow_id == "identity-sweep":
            # And its exact mirror, which is the sweep's central promise made
            # visible in telemetry rather than only in prose: it draws edges
            # and touches no object. A projection here that listed a written
            # type would mean the proposer had merged something.
            assert projection.writes == []
            assert projection.establishes
            continue
        assert projection.writes, projection.flow_id
        assert projection.establishes, projection.flow_id


def test_the_ap_projection_matches_what_its_runs_did(seeded):
    ap = _by_id(seeded)["accounts-payable"]
    assert ap.writes == ["Document", "Invoice", "InvoiceLine", "MatchSet", "Vendor"]
    assert ap.establishes == [
        "billed_against", "evidenced_by_document", "has_invoice_line",
        "issued", "matched_by", "matches_invoice_line",
    ]
    # Both governed reads the flow makes, and the prompt is one of them: the
    # instruction the ModelCall ran on is knowledge like the posting policy is.
    assert ap.knowledge == ["invoice-extraction-prompt", "invoice-posting-policy"]
    # The bare `graph:PurchaseOrder/PO-7741` form, end to end: a parser that
    # required `@rev` would leave this list empty and nothing would fail.
    assert ap.reads == ["PurchaseOrder"]


def test_accounts_payable_diverges_from_its_declaration_in_both_directions(seeded):
    ap = _by_id(seeded)["accounts-payable"]

    # Observed but not declared — a governance gap. The flow reads purchase
    # orders out of `ap-records`, and no Collection names it as a reader.
    assert ap.undeclared_reads == ["PurchaseOrder"]

    # Declared but never observed — a stale declaration. `ap-records` lists
    # the AP flow as a writer of every type it holds, but the POs and the
    # receiving reports are written by the two sync flows, not by AP.
    assert set(ap.unobserved_writes) == {
        "PurchaseOrder", "PurchaseOrderLine", "ReceivingReport",
        "ReceivingReportLine",
    }


def test_the_expense_flow_writes_an_employee_that_nothing_declares(seeded):
    expenses = _by_id(seeded)["expense-reimbursement"]
    # The expense flow mints an Employee the first time somebody submits, but
    # the `workforce` Collection declares only the HRIS feed and people-ops as
    # its writers. A second governance gap, from a different flow.
    assert "Employee" in expenses.writes
    assert expenses.undeclared_writes == ["Employee"]
    # And the mirror: it is declared to *read* the workforce and never records
    # a graph read of one.
    assert expenses.unobserved_reads == ["Capability", "Employee", "Team"]

