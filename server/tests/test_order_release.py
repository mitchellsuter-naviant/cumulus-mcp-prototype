"""The outbound write: one PATCH, and every way there is not one.

This is the only flow in the system that changes a record Cumulus does not own,
so the tests are written against the four things that would each, on their own,
make it unsafe to point at a customer's ERP:

1. **It writes once.** Not once per happy path — once, across a retried step, a
   replayed run, a second run over the same order, and a first run over an order
   somebody else already released. Four different mechanisms, because a
   double-PATCH has four different ways to happen and only the first one is
   obvious.
2. **The estate decides whether it took.** A 200 that changed nothing settles
   `unverified` and raises a Task; it never reports success and never closes the
   finding.
3. **It never claims authority it did not read back.** No `human:` claim ever
   reaches `SalesOrder.status` — that would outrank `erp:` forever and freeze
   the order — and the claim that does land carries the sales feed's own source,
   which is what makes the echo a no-op instead of a conflict.
4. **A refusal and a failure are different accusations.** A precondition that
   went stale is not the world breaking, and it must not be ledgered as if it
   were.

Nothing here touches a live estate. The wire is `estate_fixtures.FakeEstate`,
whose `patch` is `erp.py`'s own — picklist, unknown-field 400, and *no
transition validation*, which is the whole reason `WRITABLE` exists. The graded
suite at the bottom runs on `app.estate_fake.FakeErp`, which is the same route
reproduced on the app side because a console-triggered eval run executes where
`tests/` is not importable; a test here drives both and compares them, so the
two cannot drift apart in silence.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.auth import Principal
from app.disagreement_sweep import ORDER_DESK_QUEUE, SCAN, STANDING
from app.estate import EstateConnector, InMemoryConnectorStateStore
from app.estate_fake import sales_order_rows
from app.estate_writes import (
    WRITABLE,
    EstateWriter,
    InMemoryEstateWriteJournal,
    write_id,
)
from app.eval_specs import VOSS_DEAL, VOSS_ORDER, VOSS_RECORD, _state_world
from app.gateway import ACTIONS, Refused
from app.graph import object_id
from app.order_release import (
    ALREADY_APPLIED,
    ESCALATE,
    ORDER_RELEASE_EVENT,
    ORDER_RELEASE_FLOW_ID,
    OUTCOMES,
    PROCEED,
    RESOLVED_EXTERNALLY,
)
from app.sync import SyncRequest, SyncService
from app.tasks import TaskKind
from tests import estate_fixtures as fx
from tests import world as test_world

PROJECT = "cumulus-demo-mitchell-suter"
TARGET = "sales-order-status"
RECORD = VOSS_RECORD
PENDING = "Pending Approval"
RELEASED = "Pending Fulfillment"
PATCH_URL = (
    f"PATCH https://{PROJECT}-erp.web.app/api/naviant/v1/salesOrders/{RECORD}"
)

CONTROLLER = Principal(
    uid="persona_customer_user_controller",
    email="raymond.fitzgerald@naviant.com",
    role="user", scope="own", persona_id="controller",
    queues=("ap-clerk", "expense-review", "vendor-stewardship",
            "data-stewardship", ORDER_DESK_QUEUE),
)


# -- the world ----------------------------------------------------------------
#
# The same recording the sweep runs on and the graded suites are declared
# against: `eval_specs`' `state/naviant-disagreement`, which is the live
# estate's own rows — a Voss deal closed won on the 14th and an order raised on
# the 17th that the ERP still holds, beside the non-findings that keep the
# counts honest.

_CRM, _ERP, _PSA = _state_world().deliveries


def _row(status: str = PENDING) -> dict:
    """`salesOrders/5037` as the estate holds it.

    Derived from the fixture's own delivery rather than written out again: the
    ERP's shape and the feed's shape are two views of one record, and two
    hand-written copies is how the order a test PATCHes stops being the order
    the graph is holding.
    """
    row = next(
        r for r in sales_order_rows([_ERP]) if r["id"] == RECORD
    )
    return {**row, "status": status}


def _estate(status: str = PENDING, **over) -> fx.FakeEstate:
    return fx.FakeEstate(
        resources={("erp", "salesOrders"): [_row(status)]}, **over
    )


def _wire(world, estate: fx.FakeEstate, **over) -> EstateWriter:
    """A real writer over a fake wire, installed on the world's sync service.

    The table, the journal, the verification and the request shaping all run
    for real; only the socket is replaced. `ESTATE_WRITES_ENABLED` stays false
    throughout — an injected wire needs no flag, which is one of the three
    independent reasons this suite cannot reach a live estate.
    """
    writer = EstateWriter(
        EstateConnector(
            InMemoryConnectorStateStore(), project=PROJECT,
            namespace="naviant", fetch=estate.fetch,
        ),
        over.pop("journal", None) or InMemoryEstateWriteJournal(),
        fetch=over.pop("fetch", None) or estate.fetch,
        patch=over.pop("patch", None) or estate.patch,
        **over,
    )
    world.sync.estate_writer = writer
    writer.estate = estate  # type: ignore[attr-defined] — test handle
    return writer


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


@pytest.fixture()
def found(world):
    """The two feeds delivered and the sweep run: one standing finding, one
    Task in the order desk's queue, and an estate holding SO5037 unreleased."""
    for delivery in (_CRM, _ERP):
        world.sync.deliver(
            SyncRequest.model_validate(delivery), actor="system:test"
        )
    out = world.sync.sweep_disagreements(actor="system:test")
    assert out["found"] == 1, "the fixture must produce exactly one finding"
    return world


def _task(world):
    return next(t for t in world.tasks.list() if t.kind == "state_disagreement")


def _finding(world):
    return world.graph.store.get_link(_task(world).case_ref.split("/", 1)[1])


def _decide(world, verdict: str = "approved", note: str = "") -> dict | None:
    """The order desk's answer, through the real hook rather than around it."""
    task = _task(world)
    decided = world.tasks.decide(
        task.id, verdict=verdict, decided_by=CONTROLLER.uid, note=note,
    )
    return world.sync.apply_release_decision(decided, CONTROLLER)


def _run(world, out: dict):
    return world.engine.get(out["run_id"])


def _patches(estate: fx.FakeEstate) -> list[str]:
    return [c for c in estate.calls if c.startswith("PATCH ")]


def _ledger(world, action: str) -> list:
    return [e for e in world.gateway.ledger.list() if e.action == action]


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------


def test_the_action_takes_its_desk_from_the_task_and_targets_the_erp():
    action = ACTIONS["release-sales-order"]
    assert action.target == "sales-records"
    assert action.regime == "decision"
    # Per case, like `confirm-identity-link`: there is one desk today and the
    # queue that raised the work has to be the queue that may commit it.
    assert action.queue_from_task and not action.queue
    # And the write table and the Gateway agree about which Action this target
    # is written through, or a journal row and a ledger entry could not be
    # joined into one account of one event.
    assert [t.action for t in WRITABLE] == ["release-sales-order"]


def test_the_trigger_is_per_case_unlike_the_sweep_that_raised_it():
    from app.dispatch import TRIGGERS, is_windowed_flow

    trigger = TRIGGERS["order-release-approved"]
    assert trigger.event == ORDER_RELEASE_EVENT
    assert trigger.starts == ORDER_RELEASE_FLOW_ID
    assert trigger.wakes == "case"
    assert not is_windowed_flow(ORDER_RELEASE_FLOW_ID)


def test_every_echo_shape_names_a_target_the_write_table_declares():
    """A write nobody could ever confirm is worse than one nobody makes: the
    journal entry would sit open forever and no dashboard could say why."""
    declared = {t.id for t in WRITABLE}
    assert {shape[0] for shape in SyncService.ECHO_SHAPES} <= declared
    assert {shape[0] for shape in SyncService.ECHO_SHAPES} == declared


def test_the_resume_table_covers_every_declared_task_kind():
    """The tuple of lambdas this replaced ran every hook on every decision and
    wrote its routing down nowhere. Exhaustive on purpose: a new kind with no
    hook is an empty tuple somebody chose, not a silent fall-through."""
    from typing import get_args

    from app.tasks_api import RESUME_HOOKS

    assert set(RESUME_HOOKS) == set(get_args(TaskKind))
    assert len(RESUME_HOOKS["state_disagreement"]) == 1


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_one_patch_moves_the_order_and_closes_the_finding(found):
    """The whole flow, end to end, asserted at every surface it touches.

    One PATCH with the ERP's own field name and the table's own destination,
    addressed by the record id the object carries rather than by the
    transaction number every human uses — and two ledger entries, because
    deciding to release and observing what the ERP then held are two different
    claims about the world.
    """
    estate = _estate()
    _wire(found, estate)

    out = _decide(found)
    assert out["action"] == "released"
    assert out["status"] == "succeeded"
    assert out["outcome"] == PROCEED and out["patched"] is True

    # Exactly one PATCH, to the record id and not the tranId.
    assert _patches(estate) == [PATCH_URL]
    assert estate.record("erp", "salesOrders", RECORD)["status"] == RELEASED

    # The estate is what says the write took, so the journal only reads
    # `applied` because the re-read agreed.
    entry = found.sync.estate_writer.journal.get(out["write_id"])
    assert (entry.state, entry.observed, entry.value) == (
        "applied", PENDING, RELEASED,
    )
    assert entry.verified == RELEASED and entry.estate_updated_at
    assert entry.id == write_id(TARGET, RECORD, out["run_id"])

    # And the finding is over, with how it ended recorded beside the fact.
    link = _finding(found)
    assert link.props["status"] == "resolved"
    assert link.props["resolved_how"] == "by_cumulus"
    assert link.props["resolved_by"] == CONTROLLER.uid
    # The evidence that produced it survives the decision that closed it.
    assert link.props["correlation_confidence"] > 0.8
    assert link.props["signals"]


def test_the_patch_body_is_the_declared_field_and_nothing_else(found):
    """A partial PATCH carrying one field. The estate ignores what it does not
    own and 400s what it does not know, so an extra key here is either silently
    dropped or a refusal — and both are worse than not sending it."""
    estate = _estate()
    sent: list[tuple[str, dict]] = []

    def patch(url: str, body: dict) -> dict:
        sent.append((url, body))
        return estate.patch(url, body)

    _wire(found, estate, patch=patch)
    _decide(found)

    assert len(sent) == 1
    url, body = sent[0]
    assert url.endswith(f"/api/naviant/v1/salesOrders/{RECORD}")
    assert body == {"status": RELEASED}


def test_the_release_and_the_read_back_are_two_ledger_entries(found):
    """The two-regime split, as the ledger records it.

    `a1` is a decision a human authorized and it names them. `s1` is an
    observation nobody decided — a source reported and Cumulus recorded — and
    it commits on the Flow's own authority. An auditor asking "who released
    SO5037" and "where did the graph get that status" gets two different, both
    true, answers.
    """
    estate = _estate()
    _wire(found, estate)
    task_id = _task(found).id
    out = _decide(found)

    decision = _ledger(found, "release-sales-order")
    observation = [
        e for e in _ledger(found, "apply-source-records")
        if e.run_id == out["run_id"]
    ]
    assert len(decision) == 1 and len(observation) == 1

    a1, s1 = decision[0], observation[0]
    assert (a1.regime, a1.target, a1.outcome) == (
        "decision", "sales-records", "committed",
    )
    assert (a1.step_id, a1.flow_id) == ("a1", ORDER_RELEASE_FLOW_ID)
    # What authorized it, which is a different question from who ran it.
    assert (a1.task_id, a1.decided_by) == (task_id, CONTROLLER.uid)
    assert a1.principal_uid == CONTROLLER.uid
    # The case is the finding, so the PATCH joins back to the disagreement that
    # justified it without going through the run.
    assert a1.case_ref == f"link:{SCAN.link_type}/{_finding(found).id}"
    # It wrote the finding and nothing else: no object ref, because a claim on
    # the order is exactly what this Action must never make.
    assert a1.writes == [f"link:{SCAN.link_type}/{_finding(found).id}"]

    assert (s1.regime, s1.target, s1.outcome) == (
        "observation", "entity-layer", "committed",
    )
    assert s1.step_id == "s1"
    order = found.graph.get(object_id("SalesOrder", VOSS_ORDER))
    assert s1.writes == [order.ref()]
    assert s1.writes[0].startswith("graph:SalesOrder/")


def test_no_human_claim_ever_reaches_the_orders_status(found):
    """The failure the two-regime split exists to prevent.

    `human` outranks `erp` in `ontology.PRECEDENCE` and precedence is a
    read-time decision over retained claims, so a human claim here would win
    *forever*: the order would read `pending-fulfillment` in Cumulus whatever
    the ERP did with it afterwards, and every future disagreement about this
    order would be invisible. The status the graph ends up holding is the
    ERP's, at the ERP's own authority, under the source the feed itself uses.
    """
    estate = _estate()
    _wire(found, estate)
    _decide(found)

    order = found.graph.get(object_id("SalesOrder", VOSS_ORDER))
    status_claims = [c for c in order.claims if c.field == "status"]
    assert status_claims, "the order must still carry its status claims"
    assert not [c for c in status_claims if c.authority == "human"]
    assert {c.source for c in status_claims} == {"erp:erp-sales"}
    # And it did move — this is not passing because nothing was written.
    assert order.props["status"] == "pending-fulfillment"


def test_the_delta_that_carries_the_change_home_is_a_no_op_on_the_graph(found):
    """Echo suppression, on the graph side, achieved by not needing any.

    Because `s1` landed the ERP's answer under the feed's own claim source,
    the row the next delta delivers produces a claim byte-identical to one
    already held. `upsert` dedupes on (source, field, value), so the revision
    does not move and no steward is shown a conflict.
    """
    estate = _estate()
    _wire(found, estate)
    _decide(found)

    order_id = object_id("SalesOrder", VOSS_ORDER)
    before = found.graph.get(order_id).revision
    found.sync.deliver(
        SyncRequest.model_validate({
            **_ERP,
            "watermark": "2026-08-31T00:00:00Z",
            "sales_orders": [{**_ERP["sales_orders"][0], "status": RELEASED}],
        }),
        actor="system:test",
    )
    after = found.graph.get(order_id)
    assert after.revision == before
    assert after.props["status"] == "pending-fulfillment"


def test_the_run_journals_the_declared_steps_in_order(found):
    estate = _estate()
    _wire(found, estate)
    out = _decide(found)

    run = _run(found, out)
    assert [s.step_id for s in run.steps] == [
        "t1", "x1", "z1", "a1", "s1", "k1", "e1",
    ]
    assert run.caused_by
    assert run.case_ref == f"link:{SCAN.link_type}/{_finding(found).id}"
    # And no model call on any path: every decision here is a string
    # comparison against a table.
    assert not [s for s in run.steps if s.kind == "modelcall"]
    assert run.cost_usd == 0.0


def test_the_declared_export_matches_what_the_run_actually_journals(found):
    from app.flows import _seed_definitions

    estate = _estate()
    _wire(found, estate)
    run = _run(found, _decide(found))

    definition = next(
        d for d in _seed_definitions() if d.id == ORDER_RELEASE_FLOW_ID
    )
    assert [s.id for s in definition.steps] == [s.step_id for s in run.steps]
    assert [s.type for s in definition.steps] == [
        "trigger", "transform", "resolve", "action", "action", "task", "event",
    ]
    assert definition.queues == [ORDER_DESK_QUEUE]
    # The branch is declared as a branch: `z1` can go straight to the desk.
    assert definition.steps[2].next == ["a1", "k1"]


# ---------------------------------------------------------------------------
# The five outcomes
# ---------------------------------------------------------------------------


def test_the_five_outcomes_are_the_five_the_table_declares():
    assert OUTCOMES == (
        "proceed", "already_applied", "resolved_externally", "escalate",
        "refused",
    )


def test_a_second_run_over_our_own_write_sends_nothing(found):
    """The idempotency case, driven the way it would really happen: the order
    is already released and the journal remembers asking for that.

    A second decision on the same order is a second *run* and gets its own
    journal row by construction, which is exactly why the precondition cannot
    be "have I written before" and has to be "does the estate hold what I would
    write, and did anyone here ask for it".
    """
    journal = InMemoryEstateWriteJournal()
    estate = _estate()
    _wire(found, estate, journal=journal)
    first = _decide(found)
    assert first["patched"] is True

    # The same fact again — a second approval, or a redelivered one. The hook's
    # own guard is tested separately; what is under test here is the flow's,
    # which is the one that would reach the ERP.
    second = found.dispatcher.emit(
        ORDER_RELEASE_EVENT,
        {"finding": first["finding"], "task_id": _task(found).id,
         "decided_by": CONTROLLER.uid, "queue": ORDER_DESK_QUEUE},
        actor=CONTROLLER.uid, case_ref=first["finding"], principal=CONTROLLER,
    ).one().outputs

    assert second["outcome"] == ALREADY_APPLIED
    assert second["patched"] is False
    assert len(_patches(estate)) == 1, "the second run must send nothing"
    assert second["resolved"] and second["resolved_how"] == "by_cumulus"
    # Two runs, one journal row — the second never opened one, because the
    # precondition answered before the writer was asked.
    assert [e.id for e in journal.list()] == [first["write_id"]]


def test_an_order_released_by_somebody_else_is_resolved_externally(found):
    """The same row, the same value, an empty journal. The finding is over and
    nobody is interrupted about it: the controller wanted the order released
    and it is released."""
    estate = _estate(status=RELEASED)
    _wire(found, estate)

    out = _decide(found)
    assert out["outcome"] == RESOLVED_EXTERNALLY
    assert out["patched"] is False and not _patches(estate)
    assert out["resolved"] and out["resolved_how"] == "externally"
    assert _finding(found).props["status"] == "resolved"
    # No noise. This is the branch that must not raise a Task.
    assert out["desk_tasks"] == 0
    assert not [t for t in found.tasks.list() if t.kind == "exception"]


def test_an_order_the_erp_has_billed_escalates_and_writes_nothing(found):
    """The row moved under the approval. The table lists no move out of
    `Billed`, and the estate would happily accept one — `erp.py` validates the
    picklist and nothing else — which is the entire argument for `WRITABLE`."""
    estate = _estate(status="Billed")
    _wire(found, estate)

    out = _decide(found)
    assert out["outcome"] == ESCALATE
    assert not _patches(estate)
    assert not out["resolved"]
    # The finding is still true and still standing: nobody released anything.
    assert _finding(found).props["status"] == STANDING

    task = next(t for t in found.tasks.list() if t.kind == "exception")
    assert task.queue == ORDER_DESK_QUEUE and task.priority == "high"
    assert "not where you left it" in task.title
    assert "Billed" in " ".join(e.value for e in task.evidence)
    # No undo is offered, because there is none.
    assert "new decision" in task.resumes

    # And the graph learned what the ERP actually holds, from the read the
    # precondition already made — an escalation is not a reason to stay
    # ignorant of a row we just looked at.
    order = found.graph.get(object_id("SalesOrder", VOSS_ORDER))
    assert order.props["status"] == "billed"
    assert not [
        c for c in order.claims
        if c.field == "status" and c.authority == "human"
    ]


def test_a_transition_the_table_does_not_name_is_refused_before_any_request(
    found, monkeypatch
):
    """The fifth outcome, and the only one that is about Cumulus rather than
    about the customer's data. Nothing is read, nothing is sent, and the desk
    is told that this system refused its own write."""
    import app.order_release as order_release

    estate = _estate()
    _wire(found, estate)
    monkeypatch.setattr(order_release, "RELEASE_TARGET", "purchase-order-status")

    out = _decide(found)
    assert out["outcome"] == "refused"
    assert not _patches(estate)
    # Not even a GET: a write the table does not name costs the estate nothing.
    assert not estate.calls
    assert _finding(found).props["status"] == STANDING
    assert "refused its own write" in next(
        t.title for t in found.tasks.list() if t.kind == "exception"
    )


# ---------------------------------------------------------------------------
# When the world says no
# ---------------------------------------------------------------------------


def test_a_persistent_refusal_is_ledgered_failed_and_escalates(found):
    """The Gateway hole S2-0 closed, exercised on the write it was closed for.

    `failed` and `refused` are different accusations: the Gateway refusing is
    governance working, and the world refusing after it was allowed is a write
    whose effect nobody can be sure of. Only the second one is what happened
    here, and the entry has to exist at all — before the fix, a `write()`
    raising anything but a `TBoxError` appended nothing.
    """
    estate = _estate()

    def patch(url: str, body: dict) -> dict:
        raise fx.EstateRefused(400, "'status' must be one of: Pending Approval")

    _wire(found, estate, patch=patch)
    out = _decide(found)

    assert out["status"] == "failed" or out["status"] == "suspended"
    entries = _ledger(found, "release-sales-order")
    assert entries and all(e.outcome == "failed" for e in entries)
    assert all(e.refusal == "" for e in entries), "not a governance refusal"
    # The estate's own sentence, not a paraphrase of it.
    assert "must be one of" in entries[0].failure

    # The journal remembers asking, which is the point of writing ahead.
    journal = found.sync.estate_writer.journal.list()
    assert journal and all(e.state == "failed" for e in journal)
    assert all(e.observed == PENDING and e.value == RELEASED for e in journal)
    # Nothing was released and the finding still stands.
    assert estate.record("erp", "salesOrders", RECORD)["status"] == PENDING
    assert _finding(found).props["status"] == STANDING

    # Retry-then-escalate: the step retried, failed persistently, and handed
    # itself to the desk rather than failing the run into silence.
    escalation = next(
        t for t in found.tasks.list()
        if t.kind == "exception" and t.flow_id == ORDER_RELEASE_FLOW_ID
    )
    assert escalation.queue == ORDER_DESK_QUEUE
    assert "must be one of" in escalation.summary


def test_a_lying_two_hundred_settles_unverified_and_raises_a_task(found):
    """The estate decides whether the write took, never the response code.

    A door that answers cheerfully and changes nothing is the failure mode a
    `200`-trusting client cannot see, so the writer re-reads and compares. That
    is a Task and not a success, and emphatically not a resolved finding: the
    disagreement is still true.
    """
    estate = _estate()

    def patch(url: str, body: dict) -> dict:
        return {"id": RECORD, "status": RELEASED}  # says yes, changes nothing

    _wire(found, estate, patch=patch)
    out = _decide(found)

    assert out["status"] == "succeeded"
    assert out["patched"] is True and out["write_state"] == "unverified"
    assert not out["resolved"]
    assert _finding(found).props["status"] == STANDING
    assert estate.record("erp", "salesOrders", RECORD)["status"] == PENDING

    entry = found.sync.estate_writer.journal.get(out["write_id"])
    assert entry.state == "unverified" and entry.verified == PENDING

    task = next(t for t in found.tasks.list() if t.kind == "exception")
    assert "said yes and did not change" in task.title
    assert "nothing here will retry it" in task.summary
    # And the ledger says committed, because the Action *did* run and the
    # Gateway did allow it — what failed is the estate's honesty, which is a
    # fact about the journal and the Task rather than about the commit.
    assert _ledger(found, "release-sales-order")[0].outcome == "committed"


def test_a_retried_step_re_enters_the_body_and_sends_no_second_patch(found):
    """The case `retries=1` exists for, and the one it would break.

    The wire applies the change and *then* fails to answer — a connect error
    after the request landed, which is exactly the shape the write journal's
    `failed` state means by "may have landed". A step that simply retried would
    send a second PATCH. This one asks the estate what the first attempt did
    before it asks it to do anything else.
    """
    estate = _estate()
    attempts: list[str] = []

    def patch(url: str, body: dict) -> dict:
        attempts.append(url)
        answer = estate.patch(url, body)
        if len(attempts) == 1:
            raise fx.EstateDown(f"PATCH {url} → connection reset")
        return answer

    _wire(found, estate, patch=patch)
    out = _decide(found)

    assert out["status"] == "succeeded"
    assert len(attempts) == 1, "the second attempt must not reach the wire"
    assert len(_patches(estate)) == 1
    assert out["write_state"] == "applied" and out["patched"] is False
    assert estate.record("erp", "salesOrders", RECORD)["status"] == RELEASED
    # One row for one run, settled by the attempt that discovered the truth.
    journal = found.sync.estate_writer.journal.list()
    assert len(journal) == 1 and journal[0].state == "applied"
    assert _finding(found).props["status"] == "resolved"


def test_a_row_that_moves_between_the_check_and_the_write_escalates(found):
    """The honest bucket for the one window the design accepts.

    `z1` reads the row and `a1` writes it, and nothing in this estate offers an
    `If-Match` to close the gap between them. When a retry re-reads and finds
    the row somewhere the table cannot write from, that is not the world
    breaking (`failed`) and not governance stopping us (`refused`) — it is the
    same fact `z1` reports about a `Billed` order, noticed one step later. It
    gets the same name, the same desk, and no PATCH.
    """
    estate = _estate()
    attempts: list[str] = []

    def patch(url: str, body: dict) -> dict:
        attempts.append(url)
        # The request never lands; meanwhile somebody bills the order.
        estate.resources[("erp", "salesOrders")] = [_row("Billed")]
        raise fx.EstateDown(f"PATCH {url} → connection reset")

    _wire(found, estate, patch=patch)
    out = _decide(found)

    assert out["outcome"] == ESCALATE
    assert len(attempts) == 1, "no second attempt once the row has moved"
    assert out["status"] == "succeeded", "an escalation is not a failed run"
    assert not out["resolved"]
    assert _finding(found).props["status"] == STANDING
    # Ledgered as an Action that ran, was allowed, and correctly wrote nothing.
    entry = _ledger(found, "release-sales-order")[0]
    assert entry.outcome == "committed" and entry.writes == []
    assert next(
        t for t in found.tasks.list() if t.kind == "exception"
    ).queue == ORDER_DESK_QUEUE


# ---------------------------------------------------------------------------
# The desk's other answer
# ---------------------------------------------------------------------------


def test_rejecting_dismisses_the_finding_and_the_sweep_does_not_re_raise_it(
    found
):
    """"Leave it as it is", and the promise the button's copy makes.

    Through the real hook, not by setting the prop: the sweep's guard reads the
    link's status, so a test that wrote `dismissed` directly would prove the
    guard works and nothing about whether the reject path ever reaches it.
    """
    estate = _estate()
    _wire(found, estate)

    out = _decide(found, verdict="rejected", note="Contract terms still open.")
    assert out["action"] == "dismissed"
    assert not _patches(estate)
    assert not estate.calls, "rejecting must not touch the estate at all"

    link = _finding(found)
    assert link.props["status"] == "dismissed"
    assert link.props["dismissed_by"] == CONTROLLER.uid
    assert link.live, "the finding was true; dismissing is not withdrawing it"

    # And it stays dismissed. A scheduled pass that re-opened it would be a
    # machine overruling a person once a night, quietly, forever.
    before = len([t for t in found.tasks.list() if t.kind == "state_disagreement"])
    again = found.sync.sweep_disagreements(actor="system:test")
    assert again["desk_tasks"] == 0
    assert again["desk_tasks_withheld"] == 1
    assert again["standing"] == 0
    assert _finding(found).props["status"] == "dismissed"
    assert len([
        t for t in found.tasks.list() if t.kind == "state_disagreement"
    ]) == before


def test_the_dismissal_is_ledgered_and_is_not_the_release_action(found):
    """A ledger row saying `release-sales-order committed` for a decision that
    declined to release is a sentence an auditor would have to read the payload
    to disbelieve."""
    _wire(found, _estate())
    _decide(found, verdict="rejected")

    assert not _ledger(found, "release-sales-order")
    entry = next(
        e for e in _ledger(found, "record-disagreements")
        if e.step_id == "desk-decision"
    )
    assert entry.outcome == "committed"
    assert entry.decided_by == CONTROLLER.uid
    assert entry.writes == [f"link:{SCAN.link_type}/{_finding(found).id}"]


def test_a_refused_commit_re_opens_the_task_and_answers_403(
    found, _client, monkeypatch
):
    """A hook that cannot commit must not leave a Task reading `done` over a
    world where nothing happened — the failure that hid the pinned-desk bug on
    the identity link, restated for the write plane where it would matter
    more."""
    from app.auth import get_principal
    from app.main import app

    _wire(found, _estate())
    task = _task(found)

    def refuse(*_args, **_kwargs):
        raise Refused(
            "release-sales-order",
            "is the 'order-desk' queue's work, which this principal does not "
            "own",
        )

    monkeypatch.setattr(found.sync, "apply_release_decision", refuse)
    app.dependency_overrides[get_principal] = lambda: CONTROLLER
    try:
        resp = _client.post(
            f"/api/v1/tasks/{task.id}/decide",
            json={"verdict": "approved", "note": "Release it."},
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)

    assert resp.status_code == 403, resp.text
    assert "order-desk" in resp.json()["detail"]
    assert "open again" in resp.json()["detail"]

    after = found.tasks.get(task.id)
    assert after.status == "open" and after.decision is None
    assert _finding(found).props["status"] == STANDING


def test_a_decided_finding_is_not_released_twice(found):
    """The hook's own guard. A Task store 409 stops a second *decision*; this
    stops a second *run*, which is what would reach a customer's ERP."""
    estate = _estate()
    _wire(found, estate)
    _decide(found)

    task = found.tasks.get(_task(found).id)
    assert found.sync.apply_release_decision(task, CONTROLLER) is None
    assert len(_patches(estate)) == 1


def test_the_controller_alone_holds_the_desk_that_releases_revenue(client, found):
    """Scoping, stated as the persona that must not be able to do this. Connie
    works the AP queue; releasing revenue is Raymond's call and nobody else's."""
    from app.auth import get_principal
    from app.main import app

    _wire(found, _estate())
    clerk = Principal(
        uid="persona_customer_user_ap", email="connie.alberts@naviant.com",
        role="user", scope="own", persona_id="ap", queues=("ap-clerk",),
    )
    app.dependency_overrides[get_principal] = lambda: clerk
    try:
        resp = client.post(
            f"/api/v1/tasks/{_task(found).id}/decide",
            json={"verdict": "approved"},
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)
    assert resp.status_code == 403
    assert _finding(found).props["status"] == STANDING


# ---------------------------------------------------------------------------
# The echo
# ---------------------------------------------------------------------------


def _delta(status: str, external_id: str = RECORD) -> SyncRequest:
    return SyncRequest.model_validate({
        **_ERP,
        "watermark": "2026-08-31T00:00:00Z",
        "sales_orders": [{
            **_ERP["sales_orders"][0],
            "external_id": external_id,
            "order_number": VOSS_ORDER if external_id == RECORD else "SO9999",
            "status": status,
        }],
    })


def test_the_echo_pass_runs_from_deliver_with_no_flow_opting_in(found):
    """Recognizing our own change coming home is a property of the *delivery*.

    No feed declares it, none can, and the flow that made the write is long
    finished — a delivery days later still settles the journal, because the
    thing being recognized is a row on the wire and not a step in a run.
    """
    estate = _estate()
    _wire(found, estate)
    out = _decide(found)
    entry_id = out["write_id"]
    assert found.sync.estate_writer.journal.get(entry_id).state == "applied"

    outcomes = found.sync.deliver(_delta(RELEASED), actor="system:test")

    assert found.sync.estate_writer.journal.get(entry_id).state == "echoed"
    sales = next(o for o in outcomes if o["flow_id"] == "sales-sync")
    assert sales["echoes"] == 1
    assert "diverged" not in sales and "unechoed" not in sales


def test_the_row_still_lands_when_it_echoes(found):
    """An identical claim *is* the no-op. Suppressing the row itself would
    throw away the delivery that proves the write took."""
    estate = _estate()
    _wire(found, estate)
    _decide(found)

    outcomes = found.sync.deliver(_delta(RELEASED), actor="system:test")
    sales = next(o for o in outcomes if o["flow_id"] == "sales-sync")
    assert sales["status"] == "succeeded"
    assert sales["orders"] == 1
    order = found.graph.get(object_id("SalesOrder", VOSS_ORDER))
    assert order.props["status"] == "pending-fulfillment"


def test_a_third_value_coming_home_diverges_and_asks_a_person(found):
    """The re-elevating discrepancy: Cumulus set the order, the ERP confirmed
    it, and the feed now says something else. Nothing is undone — moving it
    back is a second decision — and what this needs is somebody who knows why
    it moved."""
    estate = _estate()
    _wire(found, estate)
    out = _decide(found)

    outcomes = found.sync.deliver(_delta("Billed"), actor="system:test")

    entry = found.sync.estate_writer.journal.get(out["write_id"])
    assert entry.state == "diverged" and entry.verified == "Billed"
    sales = next(o for o in outcomes if o["flow_id"] == "sales-sync")
    assert sales["diverged"] == 1 and "echoes" not in sales

    task = next(
        t for t in found.tasks.list()
        if t.kind == "exception" and "no longer holds" in t.title
    )
    assert task.queue == ORDER_DESK_QUEUE and task.priority == "high"
    assert "second decision" in task.summary
    assert "Billed" in " ".join(e.value for e in task.evidence)

    # Idempotent: the same divergent row delivered again asks once.
    found.sync.deliver(_delta("Billed"), actor="system:test")
    assert len([
        t for t in found.tasks.list() if "no longer holds" in t.title
    ]) == 1


def test_a_write_that_never_comes_home_goes_unechoed_and_asks_nobody(found):
    """"We cannot confirm this" is a number, not an interruption. Three
    deliveries of the row's own feed without it is the honest unit — the
    estate's deltas are watermarked, and one pull can legitimately race the
    write's own `updatedAt`."""
    estate = _estate()
    _wire(found, estate)
    out = _decide(found)

    for _ in range(2):
        found.sync.deliver(_delta(PENDING, external_id="9999"),
                           actor="system:test")
        assert found.sync.estate_writer.journal.get(out["write_id"]).state == (
            "applied"
        )
    outcomes = found.sync.deliver(_delta(PENDING, external_id="9999"),
                                  actor="system:test")

    assert found.sync.estate_writer.journal.get(out["write_id"]).state == (
        "unechoed"
    )
    sales = next(o for o in outcomes if o["flow_id"] == "sales-sync")
    assert sales["unechoed"] == 1
    assert not [t for t in found.tasks.list() if "no longer holds" in t.title]


def test_a_delivery_of_another_shape_does_not_age_anybody_s_write(found):
    """A vendor delta is not evidence about a sales order either way, and a
    patience counter that ticked on unrelated traffic would report a healthy
    write as unechoed the moment the estate got busy."""
    estate = _estate()
    _wire(found, estate)
    out = _decide(found)

    for _ in range(5):
        found.sync.deliver(
            SyncRequest.model_validate({
                "source": "erp-vendors",
                "vendors": [{"external_id": "V1", "name": "Acme Industrial"}],
            }),
            actor="system:test",
        )
    entry = found.sync.estate_writer.journal.get(out["write_id"])
    assert entry.state == "applied" and entry.echo_pulls == 0


def test_the_graded_world_can_never_reach_a_live_estate(world):
    """The first of the three independent reasons a graded run cannot write.

    `eval_world.build_world` hands every world a writer whose entire wire is a
    recorded ERP — the read the precondition makes, the PATCH, the verification
    re-read, and the connector's own fetch. There is no socket anywhere on the
    object, which is a stronger statement than "the flag is off": it holds on a
    box where somebody turned the flag on.

    The other two are `ESTATE_WRITES_ENABLED`, asserted below, and the import
    boundary on `estate_writes.py` in `test_estate_writes.py`.
    """
    from app.config import settings
    from app.eval_world import build_world

    graded = build_world()
    writer = graded.sync.estate_writer
    assert writer is not None
    assert writer.live is False
    # Every callable that could have been a network call is the fake's.
    assert writer._fetch.__self__ is graded.estate
    assert writer._patch.__self__ is graded.estate
    assert writer.connector._fetch.__self__ is graded.estate
    assert settings.estate_writes_enabled is False
    # And the writer it was handed is the writer the flow reads.
    assert world.sync.estate_writer is not None
    assert world.sync.estate_writer.live is False


def test_the_two_fakes_agree_about_the_sales_order_route():
    """The recorded estate in `app/` and the one in `tests/`, side by side.

    `tests/estate_fixtures.FakeEstate` serves eighteen resources, list routes,
    watermarks and outages, and stays where it is for the connector suite.
    `app/estate_fake.FakeErp` serves one route, because a console-triggered
    eval run executes where `tests/` is not importable. Two fakes for one ERP
    is a drift risk, and this is what stops it being a silent one: the shared
    surface is driven through both and compared, answer for answer.
    """
    from app import estate_fake

    url = f"https://p-erp.web.app/api/naviant/v1/salesOrders/{RECORD}"
    cases: list[tuple[str, dict]] = [
        ("the declared transition", {"status": RELEASED}),
        # The picklist is the only validation either one performs, and it is
        # the only validation the real route performs.
        ("a value off the picklist", {"status": "Shipped Maybe"}),
        ("a field the entity does not have", {"released_by": "cumulus"}),
        # Server-owned fields are ignored rather than rejected, so a caller can
        # round-trip a record it just read.
        ("a read-only field beside a real one",
         {"status": RELEASED, "updatedAt": "2020-01-01T00:00:00Z"}),
        ("nothing at all", {}),
        ("a non-string", {"memo": 7}),
        # The one that matters most: neither validates the transition, because
        # `erp.py` does not. That absence is the whole reason `WRITABLE` exists.
        ("a transition the write table forbids", {"status": "Closed"}),
    ]
    for label, body in cases:
        theirs = fx.FakeEstate(resources={("erp", "salesOrders"): [_row()]})
        ours = estate_fake.FakeErp([_row()])
        answers = []
        for fake in (theirs, ours):
            try:
                answers.append(("ok", fake.patch(url, dict(body))))
            except Exception as exc:  # noqa: BLE001 — the answer under test
                answers.append(
                    ("refused", getattr(exc, "status", 0),
                     getattr(exc, "message", str(exc)))
                )
        assert answers[0][0] == answers[1][0], f"{label}: {answers}"
        if answers[0][0] == "refused":
            assert answers[0][1:] == answers[1][1:], label
            continue
        left, right = answers[0][1], answers[1][1]
        assert left["status"] == right["status"], label
        # `updatedAt` is server time and moves; that it moved is the property.
        assert left["updatedAt"] > _row()["updatedAt"], label
        assert right["updatedAt"] > _row()["updatedAt"], label
        assert {k: v for k, v in left.items() if k != "updatedAt"} == {
            k: v for k, v in right.items() if k != "updatedAt"
        }, label


def test_a_deployment_with_no_writer_delivers_exactly_as_before(world):
    """The read-only estate is a working deployment, not a degraded one.

    Asked for explicitly, because `eval_world.build_world` now hands every
    world a writer over a recorded ERP — that is the guarantee a graded run
    cannot reach a live estate, and it means "no writer at all" is a
    deployment shape this test has to construct rather than inherit.
    """
    world.sync.estate_writer = None
    outcomes = world.sync.deliver(
        SyncRequest.model_validate(_ERP), actor="system:test"
    )
    sales = next(o for o in outcomes if o["flow_id"] == "sales-sync")
    assert sales["status"] == "succeeded"
    assert not {"echoes", "diverged", "unechoed"} & set(sales)


# ---------------------------------------------------------------------------
# The graded suite
# ---------------------------------------------------------------------------
#
# Eight cases over seven branches — the echo is two, because an echo and a
# divergence are two different things for the world to do — driven through the
# console's own door. Every one of them starts where a release really starts:
# the sweep raises the Task, a `ScriptedDecision` answers it as the controller
# would, and `apply_release_decision` emits the Event the run begins from.


@pytest.fixture()
def owner(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_backend", "firebase")
    monkeypatch.setattr(settings, "admin_api_key", "root-sekret")
    return {"X-API-Key": "root-sekret"}


def _graded(client, owner) -> dict:
    body = client.post("/api/v1/evals/order-release/run", headers=owner).json()
    assert "result" in body, body
    return body


def test_every_branch_runs_green_through_the_eval_route(client, owner, world):
    body = _graded(client, owner)
    cases = body["result"]["cases"]
    assert body["failed"] == 0, [
        (c["case_id"], [a for a in c["assertions"] if not a["passed"]], c["error"])
        for c in cases if not c["passed"]
    ]
    assert body["passed"] == 8
    assert {c["case_id"] for c in cases} == {
        "release/one-patch-moves-the-order",
        "release/a-second-approval-sends-nothing",
        "release/somebody-else-got-there-first",
        "release/a-billed-order-escalates",
        "release/a-door-that-refuses-is-ledgered-failed",
        "release/a-lying-two-hundred-settles-unverified",
        "release/the-change-comes-home",
        "release/a-third-value-comes-home-instead",
    }
    assert body["invariant_violations"] == 0
    assert body["failed_assertions"] == 0
    # Seven of the eight runs finish; the one whose door refuses hands itself
    # to a person and waits, which is the ending that branch is about.
    suspended = [c for c in cases if c["run_status"] == "suspended"]
    assert [c["case_id"] for c in suspended] == [
        "release/a-door-that-refuses-is-ledgered-failed"
    ]


def test_the_graded_branches_run_on_the_worlds_own_recording(client, owner):
    """Every release case names the fixture the sweep is graded against.

    Not a tidiness check. The finding a release closes is written by the sweep,
    over the same three books, so a release suite with a world of its own would
    be grading a decision about an order the sweep never saw.
    """
    cases = client.get(
        "/api/v1/evals/order-release/cases", headers=owner
    ).json()
    assert {c["fixture_id"] for c in cases} == {"state/naviant-disagreement"}
    assert all(c["decisions"] for c in cases), "a release is a decision"
    assert {d["verdict"] for c in cases for d in c["decisions"]} == {"approved"}


def test_widening_the_write_table_releases_an_order_it_must_not(
    client, owner, world, monkeypatch
):
    """The mutation proof: the refusal comes from `WRITABLE`, not from the ERP.

    The estate would not have stopped this — `erp.py` validates the picklist
    and nothing else, so `Billed` straight back to `Pending Fulfillment` is a
    legal PATCH there and the recorded estate reproduces that faithfully, which
    the first half of this test demonstrates rather than asserts on trust. What
    refuses it is one row in a table in Cumulus. Widen that row and the write
    goes out, and the case that says an order the ERP has billed is not ours to
    touch fails on exactly the assertions that carried the claim.
    """
    import app.estate_writes as estate_writes
    from app.estate_fake import FakeErp

    # The estate's own answer to the illegal transition, first.
    permissive = FakeErp([_row("Billed")])
    url = f"https://p-erp.web.app/api/naviant/v1/salesOrders/{RECORD}"
    assert permissive.patch(url, {"status": RELEASED})["status"] == RELEASED

    assert _graded(client, owner)["failed"] == 0

    widened = tuple(
        dataclasses.replace(
            target,
            transitions={**target.transitions, "Billed": (RELEASED,)},
        )
        if target.id == TARGET else target
        for target in estate_writes.WRITABLE
    )
    monkeypatch.setattr(estate_writes, "WRITABLE", widened)
    body = _graded(client, owner)

    billed = next(
        c for c in body["result"]["cases"]
        if c["case_id"] == "release/a-billed-order-escalates"
    )
    assert not billed["passed"]
    failures = {(a["target"], a["field"], a["expected"], a["actual"])
                for a in billed["assertions"] if not a["passed"]}
    # The order moved, in somebody else's system, because one row in a table
    # said it could.
    assert ("SalesOrder[SO5037]", "status", "billed", "pending-fulfillment") in (
        failures
    ), failures
    assert any(
        field == "reported" and "outcome=escalate" in expected
        for _target, field, expected, _actual in failures
    ), failures
