"""The Action Gateway: one chokepoint, three regimes, an append-only ledger.

The doctrinal assertions, and each of them is a claim the governance spine
makes that was previously unfalsifiable:

- a decision write is refused at the *write* when its principal may not make
  it, even when the HTTP boundary that normally checks was never involved;
- a write that violates the ontology is refused rather than landed;
- every commit appends exactly one ledger entry, and a replayed step appends
  none;
- an impersonated commit names both principals — the persona and the operator;
- the three write regimes are distinguishable in the ledger, because the
  Action declares which it is rather than the ledger guessing;
- and the paradigm case: a Task decision resuming an AP run lands a ledgered
  commit naming the human who decided it.
"""

import pytest

from app import personas
from app.auth import flow_principal, principal_for
from app.gateway import ACTIONS, Refused
from app.graph import Claim, object_id
from app.sync import SyncRequest
from tests import world as test_world

CLERK = personas.get("customer-user:ap")
#: The Controller owns both master-data desks: vendor stewardship is a
#: finance accountability, not an IT one.
STEWARD = personas.get("customer-user:controller")
NO_QUEUES = personas.get("customer-user:ae")


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


def _vendors(world):
    world.sync.deliver(SyncRequest.model_validate({
        "source": "erp-vendors",
        "vendors": [{
            "external_id": "V-10041", "name": "Acme Industrial Inc.",
            "tax_id": "94-2711883", "status": "active",
        }],
    }), actor="system:test")


def _gated_invoice(world):
    """An invoice with no PO on it: the posting rule holds it for a clerk."""
    return world.payables.submit(
        "connie.alberts@naviant.com", filename="mer.pdf",
        hint="newvendor=meridian-supply nopo invoice=MER-1 lines=PPE-GLV:2 "
             "total=190.00",
    )


def _entries(world, **match):
    return [
        e for e in world.gateway.ledger.list()
        if all(getattr(e, k) == v for k, v in match.items())
    ]


# ---- the declared set ------------------------------------------------------


def test_every_action_binds_to_a_queue_someone_can_hold():
    """A queue no persona binds to would refuse every principal forever, and
    the failure would look like a permission bug rather than a typo."""
    named = {a.queue for a in ACTIONS.values() if a.queue}
    assert named and named <= set(personas.ALL_QUEUES)


# ---- permission, at the write ---------------------------------------------


def test_a_decision_write_is_refused_when_the_principal_may_not_make_it(world):
    """The whole point of the Gateway, stated as a test.

    The Task is decided directly rather than through `POST /tasks/{id}/decide`,
    so `require_queue` at the boundary never runs. Kim owns no queue at all,
    and the write refuses anyway — which is the difference between a permission
    check and a permission *route*.
    """
    _vendors(world)
    record = _gated_invoice(world)
    assert record.state == "pending"

    decided = world.tasks.decide(record.task_id, "approved", NO_QUEUES.email)
    world.payables.resume_for_task(decided, principal_for(NO_QUEUES))

    run = world.engine.get(record.run_id)
    assert run.status == "failed" and "post-invoice" in run.error
    # Nothing posted, and the refusal is on the record with a reason.
    assert world.payables.get(record.id).state == "pending"
    [entry] = _entries(world, action="post-invoice")
    assert entry.outcome == "refused"
    assert "ap-clerk" in entry.refusal
    assert entry.principal_uid == NO_QUEUES.uid


def test_an_undeclared_action_never_reaches_the_graph(world):
    with pytest.raises(Refused):
        world.gateway.action("delete-everything", principal_for(CLERK))
    assert world.gateway.ledger.list() == []


# ---- validation ------------------------------------------------------------


def test_a_tbox_invalid_write_is_refused_rather_than_landed(world):
    """The ontology check already existed; refusing on it is what is new."""
    act = world.gateway.action("apply-source-records", flow_principal("test-flow"))
    with pytest.raises(Refused):
        act.commit(lambda w: w.upsert(
            "Vendor", "Phantom Supply",
            [Claim(field="not_a_declared_field", value=1, source="erp:test")],
        ))
    assert world.graph.get(object_id("Vendor", "Phantom Supply")) is None
    [entry] = world.gateway.ledger.list()
    assert entry.outcome == "refused" and "ontology" in entry.refusal
    # A refused entry still says what it managed to write — here, nothing.
    assert entry.writes == []


# ---- the ledger ------------------------------------------------------------


def test_a_decision_write_appends_exactly_one_ledger_entry(world):
    _vendors(world)
    record = _gated_invoice(world)
    decided = world.tasks.decide(record.task_id, "approved", CLERK.email)
    resumed = world.payables.resume_for_task(decided, principal_for(CLERK))
    assert resumed.state == "posted"

    [entry] = _entries(world, action="post-invoice", run_id=record.run_id)
    assert entry.outcome == "committed"
    assert entry.regime == "decision"
    assert entry.step_id == "a1" and entry.flow_id == "accounts-payable"
    assert entry.task_id == record.task_id
    assert any(ref.startswith("graph:Invoice/") for ref in entry.writes)
    # The step's summary names the entry, so a run trace dereferences into the
    # ledger instead of asserting one exists.
    step = world.engine.get(record.run_id).step("a1")
    assert entry.id in step.summary


def test_an_impersonated_write_names_both_principals(world):
    _vendors(world)
    record = _gated_invoice(world)
    decided = world.tasks.decide(record.task_id, "approved", CLERK.email)
    world.payables.resume_for_task(
        decided, principal_for(CLERK, by=personas.ENGINEER.uid)
    )

    [entry] = _entries(world, action="post-invoice")
    assert entry.principal_uid == CLERK.uid
    assert entry.impersonated_by == personas.ENGINEER.uid


def test_the_three_regimes_are_distinguishable(world):
    """Observation, decision and resolution all end in an `upsert`; only the
    call site knows which it made, and the ledger records what it said."""
    _vendors(world)
    record = _gated_invoice(world)
    world.payables.resume_for_task(
        world.tasks.decide(record.task_id, "approved", CLERK.email),
        principal_for(CLERK),
    )

    by_regime: dict[str, set[str]] = {}
    for entry in world.gateway.ledger.list():
        by_regime.setdefault(entry.regime, set()).add(entry.action)
    assert set(by_regime) == {"observation", "decision", "resolution"}
    assert by_regime["observation"] == {"land-source-records"}
    assert by_regime["resolution"] == {"resolve-identities"}
    assert by_regime["decision"] == {"post-invoice"}


def test_a_steward_decision_is_ledgered_as_a_decision(world):
    """The one commit with no run behind it: the Gateway is all that records
    it, which is why it goes through the Gateway."""
    _vendors(world)
    world.sync.deliver(SyncRequest.model_validate({
        "source": "legacy-ap-extract",
        "vendors": [{"external_id": "L-77", "name": "Acme Industial Inc"}],
    }), actor="system:test")
    task = next(t for t in world.tasks.list(kind="match_review"))
    decided = world.tasks.decide(task.id, "approved", STEWARD.email)

    world.sync.apply_match_decision(decided, principal_for(STEWARD))
    [entry] = _entries(world, action="link-source-record")
    assert entry.regime == "decision" and entry.outcome == "committed"
    assert entry.principal_uid == STEWARD.uid
    assert entry.decided_by == STEWARD.email
    assert entry.run_id == ""  # a batch feed does not suspend for one row


def test_a_steward_without_the_queue_is_refused(world):
    _vendors(world)
    world.sync.deliver(SyncRequest.model_validate({
        "source": "legacy-ap-extract",
        "vendors": [{"external_id": "L-77", "name": "Acme Industial Inc"}],
    }), actor="system:test")
    before = len(world.graph.find("Vendor"))
    task = next(t for t in world.tasks.list(kind="match_review"))
    decided = world.tasks.decide(task.id, "approved", CLERK.email)

    with pytest.raises(Refused):
        world.sync.apply_match_decision(decided, principal_for(CLERK))
    assert len(world.graph.find("Vendor")) == before
    assert _entries(world, action="link-source-record")[0].outcome == "refused"


# ---- end to end, through the front door ------------------------------------


def test_a_task_decision_lands_a_ledgered_commit_naming_the_human(client, world):
    """The paradigm decision write, taken the way a clerk takes it.

    Through the API, acting as the AP persona: the boundary check passes, the
    Gateway's check passes for the same reason, and the entry names the persona
    who decided *and* the engineer whose session it was.
    """
    _vendors(world)
    record = _gated_invoice(world)
    response = client.post(
        f"/api/v1/tasks/{record.task_id}/decide",
        json={"verdict": "approved", "note": "Small first invoice, no PO."},
        headers={"X-Cumulus-Persona": CLERK.id},
    )
    assert response.status_code == 200
    assert world.payables.get(record.id).state == "posted"

    [entry] = _entries(world, action="post-invoice")
    assert entry.outcome == "committed"
    assert entry.principal_uid == CLERK.uid
    assert entry.decided_by == CLERK.uid
    # Local dev signs in as the engineer, so this decision was made under a
    # persona switch — and the ledger keeps both parties.
    assert entry.impersonated_by == personas.ENGINEER.uid

    read = client.get(f"/api/v1/ledger/{entry.id}").json()
    assert read["id"] == entry.id and read["regime"] == "decision"


def test_the_ledger_route_scopes_reads_and_lists_the_closed_action_set(
    client, world
):
    _vendors(world)
    record = _gated_invoice(world)
    world.payables.resume_for_task(
        world.tasks.decide(record.task_id, "approved", CLERK.email),
        principal_for(CLERK),
    )

    everything = client.get("/api/v1/ledger").json()
    assert {e["regime"] for e in everything} == {
        "observation", "decision", "resolution"
    }
    assert client.get("/api/v1/ledger?regime=decision").json()[0]["action"] == (
        "post-invoice"
    )
    # Kim reads only her own work, and none of this was hers.
    assert client.get(
        "/api/v1/ledger", headers={"X-Cumulus-Persona": NO_QUEUES.id}
    ).json() == []

    actions = client.get("/api/v1/ledger/actions").json()
    assert {a["id"] for a in actions} == {
        "post-invoice", "record-expense", "link-source-record",
        "link-company-record", "confirm-vendor", "land-source-records",
        "apply-source-records", "resolve-identities",
    }
    # Every Action names one of the three regimes and nothing else.
    assert {a["regime"] for a in actions} == {
        "decision", "observation", "resolution"
    }


def test_an_expense_the_flow_approved_is_committed_by_the_flow(world):
    """Nobody decided it, so nobody is credited with deciding it.

    The alternative — crediting the submitter — would be the ledger's first
    lie, and the expensive kind: an auto-approval that reads as a judgment.
    """
    request = world.expenses.submit(
        "john.smith@naviant.com",
        documents=[{"filename": "taxi.jpg", "hint": "vendor=City Cab total=24.50"}],
    )
    assert request.state == "approved"
    [entry] = _entries(world, action="record-expense")
    assert entry.principal_uid == "flow:expense-reimbursement"
    assert entry.decided_by == "" and entry.regime == "decision"
