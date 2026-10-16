"""The Action Gateway: one chokepoint, three regimes, an append-only ledger.

The doctrinal assertions, and each of them is a claim the governance spine
makes that was previously unfalsifiable:

- a decision write is refused at the *write* when its principal may not make
  it, even when the HTTP boundary that normally checks was never involved;
- a write that violates the ontology is refused rather than landed;
- a write that *breaks* is ledgered before the exception leaves, and is
  `failed` rather than `refused` — the Gateway said yes and the world said no;
- every commit appends exactly one ledger entry, and a replayed step appends
  none;
- an impersonated commit names both principals — the persona and the operator;
- the three write regimes are distinguishable in the ledger, because the
  Action declares which it is rather than the ledger guessing;
- and the paradigm case: a Task decision resuming an AP run lands a ledgered
  commit naming the human who decided it.
"""

import httpx
import pytest

from app import personas
from app.auth import flow_principal, principal_for
from app.gateway import ACTIONS, Refused
from app.graph import Claim, object_id
from app.connectors.sync import SyncRequest
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
    # The desk named in the refusal is the one the *Task* was routed to — the
    # submitter's own, per the governed approval table — rather than the
    # Action's standing pin. Which is the sharper refusal: Kim was not merely
    # unbound from the AP desk, she was answering somebody else's case.
    assert world.queues.ensure_personal(
        "connie.alberts@naviant.com"
    ).slug in entry.refusal
    assert entry.principal_uid == NO_QUEUES.uid


def test_an_undeclared_action_never_reaches_the_graph(world):
    with pytest.raises(Refused):
        world.gateway.action("delete-everything", principal_for(CLERK))
    assert world.gateway.ledger.list() == []


# ---- the desk a case belongs to --------------------------------------------
#
# A governed approval table may route a review back to the person who
# submitted the case (`submitter` in routing.py), which resolves to that
# person's own desk. With `post-invoice` and `record-expense` pinned to a
# shared queue, the queue that raised the work and the queue that could commit
# it were different queues, and the submitter would answer their own Task and
# have the write refused at the Gateway.


def _own_desk(world, persona) -> str:
    return world.queues.ensure_personal(persona.email, persona.person).slug


def test_a_case_on_the_submitters_own_desk_commits_from_that_desk(world):
    """Kim owns no shared queue at all — she is the persona that proves scoping
    works — and she can still commit the review of her own submission."""
    desk = _own_desk(world, NO_QUEUES)
    kim = principal_for(NO_QUEUES)
    assert kim.owns_queue(desk) and not kim.owns_queue("ap-clerk")

    for action_id, target in (
        ("post-invoice", "ap-records"), ("record-expense", "expense-records")
    ):
        world.gateway.action(
            action_id, kim, queue=desk, task_id="task_kim_1",
            decided_by=NO_QUEUES.email,
        ).commit(lambda _w: None)
        # The entry is what it always was: same regime, same target, and the
        # human judgment that authorized it still named beside the principal.
        [entry] = _entries(world, action=action_id)
        assert entry.outcome == "committed" and entry.refusal == ""
        assert entry.regime == "decision" and entry.target == target
        assert entry.task_id == "task_kim_1"
        assert entry.decided_by == NO_QUEUES.email


def test_holding_the_standing_desk_does_not_buy_somebody_elses_case(world):
    """The refusal that has to survive the change. Deriving the desk from the
    Task must not widen the check to "either desk will do": the AP clerk holds
    the pinned queue and still may not commit a review routed to Kim."""
    desk = _own_desk(world, NO_QUEUES)
    clerk = principal_for(CLERK)
    assert clerk.owns_queue("ap-clerk") and not clerk.owns_queue(desk)

    with pytest.raises(Refused):
        world.gateway.action("post-invoice", clerk, queue=desk).commit(
            lambda _w: None
        )
    [entry] = _entries(world, action="post-invoice")
    assert entry.outcome == "refused" and desk in entry.refusal


def test_a_commit_no_task_authorized_still_answers_to_the_standing_desk(world):
    """The other half of carrying both: an invoice under the governed auto-post
    limit suspends for nobody, so there is no Task and no per-case desk. The
    pin is what the Flow commits under, and it still refuses whoever lacks it.
    """
    world.gateway.action(
        "post-invoice", flow_principal("accounts-payable", "ap-clerk")
    ).commit(lambda _w: None)
    assert _entries(world, action="post-invoice")[0].outcome == "committed"

    with pytest.raises(Refused) as refused:
        world.gateway.action("post-invoice", principal_for(NO_QUEUES)).commit(
            lambda _w: None
        )
    assert "ap-clerk" in refused.value.reason


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


def test_a_write_that_raises_is_ledgered_before_the_exception_leaves(world):
    """The hole this closes: a governed write with no ledger row at all.

    `commit` used to catch `TBoxError` and nothing else, with the append
    sitting *after* `write()` — so a write raising for any other reason left
    through the gap with the append still ahead of it and appended nothing.
    Survivable while every Action ended in a graph upsert; a governance hole
    the moment one ends in an HTTP call, because that is precisely the case
    where the write may have landed in somebody else's system and the only
    record of the attempt is the entry that was being skipped.

    So: the entry exists, it carries the refs that did land before the raise,
    it is `failed` rather than `refused` — the Gateway allowed this one — and
    the exception still travels, because a step that broke must not report
    success just because its failure was written down.
    """
    act = world.gateway.action("apply-source-records", flow_principal("test-flow"))

    def half_a_write(writer):
        writer.upsert(
            "Vendor", "Halfway Supply",
            [Claim(field="status", value="active", source="erp:test")],
        )
        raise httpx.ConnectError("the estate did not answer")

    with pytest.raises(httpx.ConnectError):
        act.commit(half_a_write)

    [entry] = world.gateway.ledger.list()
    assert entry.outcome == "failed"
    assert "ConnectError" in entry.failure
    # `refused` is the Gateway saying no and this was not that, so the field an
    # auditor filters refusals on stays empty.
    assert entry.refusal == ""
    # And what landed before the raise is on the entry, which is the whole
    # point: the graph moved and the ledger says by how much.
    assert entry.write_count == 1
    assert entry.writes[0].startswith("graph:Vendor/")
    assert world.graph.get(object_id("Vendor", "Halfway Supply")) is not None


def test_the_ledger_tells_a_refusal_and_a_failure_apart(world):
    """Two entries, same Action, two different accusations.

    Collapsing them into one "not committed" bucket would put the spine
    working and the spine's outages behind the same number — and the two ask
    for opposite responses.
    """
    with pytest.raises(Refused):
        world.gateway.action("post-invoice", principal_for(NO_QUEUES)).commit(
            lambda _w: None
        )
    with pytest.raises(RuntimeError):
        world.gateway.action("post-invoice", principal_for(CLERK)).commit(
            lambda _w: (_ for _ in ()).throw(RuntimeError("the ERP hung up"))
        )

    outcomes = {e.outcome for e in _entries(world, action="post-invoice")}
    assert outcomes == {"refused", "failed"}


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
        "apply-source-records", "record-document-read", "resolve-identities",
        "propose-identity-link", "confirm-identity-link",
        "record-disagreements", "record-compliance-alert",
        "link-job-documents", "apply-job-demand",
        "apply-schedule-day", "mark-day-approved",
        "release-sales-order", "append-correction",
    }
    # Seventeen Actions write into books Cumulus keeps. Exactly one leaves the
    # building, and it is worth being able to see that in one line: a bad
    # graph write is reversible by a steward and every claim it made is still
    # on the record, where this one ends in a PATCH to a customer's ERP.
    assert [a["id"] for a in actions if a["target"] == "sales-records"] == [
        "release-sales-order"
    ]
    # Five Actions carry no queue in the table, for two different reasons —
    # and the table says which. A queue says whose *work* an Action is:
    # proposing a merge, recording a disagreement, noticing that a
    # certification is close to expiring and linking a document nobody
    # disputed are nobody's work (a Flow does each unasked, on its own
    # authority), and confirming is somebody's
    # work but not always the same somebody, because the sweep proposes across
    # both counterparty books. So the confirmation takes its desk from the Task
    # it answers, which is the only place that knows.
    by_id = {a["id"]: a for a in actions}
    assert by_id["propose-identity-link"]["queue"] == ""
    assert by_id["propose-identity-link"]["queue_from_task"] is False
    assert by_id["record-disagreements"]["queue"] == ""
    assert by_id["record-disagreements"]["queue_from_task"] is False
    assert by_id["record-compliance-alert"]["queue"] == ""
    assert by_id["record-compliance-alert"]["queue_from_task"] is False
    assert by_id["link-job-documents"]["queue"] == ""
    assert by_id["link-job-documents"]["queue_from_task"] is False
    assert by_id["confirm-identity-link"]["queue"] == ""
    assert by_id["confirm-identity-link"]["queue_from_task"] is True
    assert by_id["release-sales-order"]["queue"] == ""
    assert by_id["release-sales-order"]["queue_from_task"] is True
    # And nothing else in the table derives its desk: an Action that declares
    # one and derives none must not accept one from a caller. Eight do, from
    # three directions — the identity link because one Action serves two
    # stewards' books, the release because there is one desk today and pinning
    # it would be right until the second class of disagreement brings a second
    # desk behind it, and the six writes a governed approval table can route
    # back to the desk the Task was answered at: the two flow commits, the
    # vendor a reviewer names inside the invoice review, the plant demand
    # committed under the desk that answered for it, and the plant day — both
    # the fill a correction re-runs and the signature that makes it
    # exportable, which must commit under the desk that signed it.
    assert sorted(a["id"] for a in actions if a["queue_from_task"]) == [
        "apply-job-demand", "apply-schedule-day", "confirm-identity-link",
        "confirm-vendor", "mark-day-approved", "post-invoice",
        "record-expense", "release-sales-order",
    ]
    # Those three are the only ones carrying both, and that is not a
    # contradiction: the pin is where a commit no human authorized lands (a
    # Flow posting under the governed auto-post limit), and the Task's desk
    # wins whenever a Task authorized it.
    assert by_id["post-invoice"]["queue"] == "ap-clerk"
    assert by_id["record-expense"]["queue"] == "expense-review"
    assert by_id["confirm-vendor"]["queue"] == "ap-clerk"
    # Every Action names one of the three regimes and nothing else.
    assert {a["regime"] for a in actions} == {
        "decision", "observation", "resolution"
    }


def test_an_expense_resumed_with_nobody_behind_it_is_committed_by_the_flow(world):
    """Nobody drove this pass, so nobody is credited with driving it.

    v9 had an auto-approval path and this was the ledger row it produced. v10
    asks its submitter every time, so the Flow's own authority shows up
    somewhere narrower: a resume with no live caller — a seed, a script, an
    operator's `POST /runs/{id}/resume`. The *decision* is still named, because
    a person really made it; the *committer* is the Flow, because crediting a
    human with an act they were not present for would be the ledger's first
    lie, and the expensive kind.
    """
    request = world.expenses.submit(
        "john.smith@naviant.com",
        documents=[{"filename": "taxi.jpg", "hint": "vendor=City Cab total=24.50"}],
    )
    decided = world.tasks.decide(
        request.task_id, "approved", "john.smith@naviant.com",
    )
    resumed = world.expenses.resume_for_task(decided)  # no principal
    assert resumed.state == "approved"
    [entry] = _entries(world, action="record-expense")
    assert entry.principal_uid == "flow:expense-reimbursement"
    assert entry.decided_by == "john.smith@naviant.com"
    assert entry.regime == "decision"
    # And it committed under the desk the Task was raised to, not under the
    # Action's standing pin: the queue that raised the work and the queue that
    # may commit it have to be the same queue, or a submitter answering their
    # own case is refused their own answer.
    assert entry.principal_email == "expense-reimbursement@flow"
