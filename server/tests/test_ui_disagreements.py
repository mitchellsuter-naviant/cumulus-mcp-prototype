"""The order desk's case: `/api/ui/v1/disagreements`, and its tile on the dashboard.

The console surface over the one decision in this system that reaches out of
Cumulus into a customer's ERP, so the properties are about what a controller is
told *before* they press anything.

- **The two columns are two vocabularies.** A deal has a stage, an amount and a
  close date; an order has a status, a total and an order date. There is no
  shared key, so this is not a comparison table and a route that made one would
  draw a grid of empty cells.
- **The before/after is the write table's, not prose.** `CHANGES status Pending
  Approval → Pending Fulfillment` is read through `read_write_policy`, the
  release flow's own first step — so the screen cannot promise a transition the
  Action would refuse, and `UNCHANGED` is *derived* from the column above it
  rather than typed.
- **`blocked_on_identity` is read, never re-derived.** It counts evidence the
  sweep declined to admit because an unconfirmed merge was hiding it. That is a
  decision not to read; it leaves no trace on the graph to walk afterwards, and
  a panel that tried to recompute it would be inventing a number.
- **Every other count is answerable by a list on the screen.** The dashboard's
  standing, awaiting-decision and outcome splits are counted from the rows the
  route returned, so a tile cannot disagree with what opening it shows.
- **A settled finding says who settled it and how.** `by_cumulus` and
  `externally` are two numbers off one status, and folding them together would
  make a system that changed nothing read like one that changed a customer's
  order.

The world is `test_order_release`'s — the live estate's own Voss rows — because
the screen and the flow have to be describing one case.
"""

from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

from app import ui_api
from app.auth import Principal
from app.disagreement_sweep import ORDER_DESK_QUEUE, SCAN, STANDING
from app.estate import EstateConnector, InMemoryConnectorStateStore
from app.estate_writes import EstateWriter, InMemoryEstateWriteJournal
from app.tasks.order_release.domain import BY_CUMULUS, DISMISSED, EXTERNALLY, RESOLVED
from app.connectors.sync import SyncRequest
from app.ui_api import get_disagreement_principal, get_ui_principal
from tests import estate_fixtures as fx
from tests import world as test_world

ROUTE = "/api/ui/v1/disagreements"
DASHBOARD = "/api/ui/v1/dashboard"

PROJECT = "cumulus-demo-mitchell-suter"
VOSS_DEAL = "Voss — Title & Service Records Capture"
VOSS_ORDER = "SO5037"
RECORD = "5037"
PENDING = "Pending Approval"
RELEASED = "Pending Fulfillment"

#: Who reaches the screen, and as what. Two independent axes, exactly as the
#: identity workbench has: the desk makes you the decider, read reach makes you
#: an observer, and a persona with neither is told it is not theirs.
DESK = {"customer-user:controller"}
OBSERVERS = {"naviant-engineer", "naviant-support", "customer-admin"}
#: Reads every plane to diagnose a deployment and holds no queue at all — the
#: persona that separates "may see this" from "may answer this". The engineer
#: is deliberately not it: it owns every queue, so it would pass a gate test
#: that no binding was actually being checked by.
FOREIGN = "naviant-support"
#: Owns no queue and reads only his own work. The persona that proves scoping.
OUTSIDE = "customer-user:ae"

CONTROLLER = Principal(
    uid="persona_customer_user_controller",
    email="raymond.fitzgerald@naviant.com",
    role="user", scope="own", persona_id="controller",
    queues=("ap-clerk", "expense-review", "vendor-stewardship",
            "data-stewardship", ORDER_DESK_QUEUE),
)


def _as(persona_id: str) -> dict[str, str]:
    return {"X-Cumulus-Persona": persona_id}


# -- the world ---------------------------------------------------------------

_CRM = {
    "source": "crm-book",
    "watermark": "2026-08-30T00:00:00Z",
    "accounts": [
        {"external_id": "A1", "name": "Voss Automotive Group",
         "website": "https://vossauto.example.com", "phone": "(608) 555-0111"},
    ],
    "opportunities": [
        {"external_id": "O1", "name": VOSS_DEAL, "account_external_id": "A1",
         "stage": "closed-won", "amount": 128000.0,
         "close_date": "2026-08-14", "owner_email": "john.smith@naviant.com"},
    ],
}

_ERP = {
    "source": "erp-sales",
    "watermark": "2026-08-30T00:00:00Z",
    "customers": [
        {"external_id": "1007", "name": "Voss Automotive Group",
         "email": "ap@vossauto.example.com", "phone": "(608) 555-0111"},
    ],
    "sales_orders": [
        {"external_id": RECORD, "order_number": VOSS_ORDER,
         "customer_external_id": "1007", "ordered_date": "2026-08-17",
         "status": PENDING, "total": 34960.0,
         "memo": "Title and service records capture — phase 1",
         "lines": [
             {"item_external_id": "I1", "quantity": 40.0, "rate": 620.0,
              "amount": 24800.0},
             {"item_external_id": "I2", "quantity": 1.0, "rate": 10160.0,
              "amount": 10160.0},
         ]},
    ],
    "items": [
        {"external_id": "I1", "sku": "CAP-LIC", "display_name":
         "Capture licences", "item_type": "Service", "base_price": 620.0},
        {"external_id": "I2", "sku": "IMPL-SVC", "display_name":
         "Implementation", "item_type": "Service", "base_price": 10160.0},
    ],
}

#: The delivery system, under the nickname a coordinator typed. It mints its
#: own Company, the identity sweep proposes the merge at `in_review`, and the
#: disagreement sweep is not allowed to read across it — which is the whole
#: source of `blocked_on_identity`.
_PSA = {
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
    ],
}


def _row(status: str = PENDING) -> dict:
    return {
        "id": RECORD, "tranId": VOSS_ORDER, "customerId": "1007",
        "status": status, "total": 34960.0, "tranDate": "2026-08-17",
        "memo": "Title and service records capture — phase 1",
        "updatedAt": "2026-08-17T14:02:00Z",
    }


def _wire(world, status: str = PENDING) -> EstateWriter:
    """A real writer over a fake wire. Only the socket is replaced."""
    estate = fx.FakeEstate(resources={("erp", "salesOrders"): [_row(status)]})
    writer = EstateWriter(
        EstateConnector(
            InMemoryConnectorStateStore(), project=PROJECT,
            namespace="naviant", fetch=estate.fetch,
        ),
        InMemoryEstateWriteJournal(),
        fetch=estate.fetch, patch=estate.patch,
    )
    world.sync.estate_writer = writer
    return writer


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


@pytest.fixture()
def found(client, world):
    """Two feeds and a sweep: one standing finding, one Task on the order desk."""
    for delivery in (_CRM, _ERP):
        world.sync.deliver(
            SyncRequest.model_validate(delivery), actor="system:test"
        )
    out = world.sync.sweep_disagreements(actor="system:test")
    assert out["found"] == 1, "the fixture must produce exactly one finding"
    return client


@pytest.fixture()
def blocked(client, world):
    """The same estate with the PSA's nickname in it, and the merge unconfirmed.

    The identity pass runs first because in production it is what has already
    happened by the time a state window closes; the proposal it leaves at
    `in_review` is the state the disagreement sweep has to refuse to read
    across.
    """
    for delivery in (_CRM, _ERP, _PSA):
        world.sync.deliver(
            SyncRequest.model_validate(delivery), actor="system:test"
        )
    world.sync.sweep_identities(actor="system:test")
    out = world.sync.sweep_disagreements(actor="system:test")
    assert out["blocked_on_identity"] == 1, "the fixture must withhold evidence"
    return client


@pytest.fixture()
def empty(client, world):
    """A world where nothing has arrived and no window has ever closed."""
    return client


def _screen(client, persona: str = "customer-user:controller") -> dict:
    resp = client.get(ROUTE, headers=_as(persona))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _panel(client, persona: str = "naviant-engineer") -> dict:
    resp = client.get(DASHBOARD, headers=_as(persona))
    assert resp.status_code == 200, resp.text
    return resp.json()["judgment"]["disagreement"]


def _task(world):
    return next(t for t in world.tasks.list() if t.kind == "state_disagreement")


def _decide(world, verdict: str = "approved", note: str = "") -> dict | None:
    """The desk's answer through the real hook, never around it."""
    decided = world.tasks.decide(
        _task(world).id, verdict=verdict, decided_by=CONTROLLER.uid, note=note,
    )
    return world.sync.apply_release_decision(decided, CONTROLLER)


# ---------------------------------------------------------------------------
# The door
# ---------------------------------------------------------------------------


def test_the_desk_and_the_observers_get_in_and_nobody_else_does(found):
    """The identity workbench's rule over a different desk, and it has to be
    that rule: the only persona who may release an order is own-scoped, so a
    route gated on read reach alone would refuse its only user."""
    for persona in DESK | OBSERVERS:
        assert found.get(ROUTE, headers=_as(persona)).status_code == 200, persona

    refused = found.get(ROUTE, headers=_as(OUTSIDE))
    assert refused.status_code == 403
    # A sentence with the reason in it, naming the desk — not a bare 403.
    assert ORDER_DESK_QUEUE in refused.json()["detail"]
    assert "nothing here for you rather than something withheld" in (
        refused.json()["detail"]
    )


def test_the_desk_reads_as_its_own_and_an_observer_reads_over_its_shoulder(found):
    """`yours` is a queue binding and not read reach. Support reads everything
    in order to diagnose a customer's problem and decides nothing on their
    behalf, which is exactly the distinction this flag carries."""
    assert _screen(found, "customer-user:controller")["yours"] is True
    assert _screen(found, FOREIGN)["yours"] is False
    assert _screen(found)["desk"] == ORDER_DESK_QUEUE


def test_the_door_is_behind_the_console_door_and_no_key_may_reach_it():
    """`/api/ui/v1`'s whole contract, restated here because this is the payload
    behind the only button in the system that writes to somebody else's ERP: a
    machine credential has no screens, and one reaching this route would make a
    release something a key could read its way to."""
    gate = inspect.signature(get_disagreement_principal)
    assert gate.parameters["principal"].default.dependency is get_ui_principal

    depends = inspect.signature(ui_api.disagreements).parameters["principal"].default
    assert depends.dependency is get_disagreement_principal

    machine = Principal(
        uid="svc-1", email="svc@machine", role="owner", key_id="k_1"
    )
    with pytest.raises(HTTPException) as refused:
        get_ui_principal(machine)
    assert refused.value.status_code == 403


def test_the_screen_has_no_write_route_of_its_own():
    """Releasing goes through the Task the sweep raised, and nowhere else. A
    write route here would be a quieter copy of `apply_release_decision`'s
    authorization — the one place a controller's authority over an outbound
    write is actually checked."""
    routes = [
        route for route in ui_api.router.routes
        if "/disagreements" in getattr(route, "path", "")
    ]
    assert routes
    assert all(set(getattr(r, "methods", set())) <= {"GET", "HEAD"} for r in routes)


# ---------------------------------------------------------------------------
# The two columns
# ---------------------------------------------------------------------------


def test_each_column_is_its_own_books_vocabulary(found):
    """Not a comparison table. A deal has a stage and a close date; an order has
    a status and an order date; there is no shared key to compare on."""
    case = _screen(found)["standing"][0]

    assert case["crm"]["book"] == "the CRM"
    assert case["crm"]["label"] == VOSS_DEAL
    # The three the correlation turned on, and nothing else: the stage that
    # made it a disagreement, and the amount and close date the band and the
    # window were measured against.
    assert dict(
        (f["label"], f["value"]) for f in case["crm"]["facts"]
    ) == {
        "Stage": "closed-won",
        "Amount": "$128,000.00",
        "Close date": "2026-08-14",
    }

    assert case["erp"]["book"] == "the ERP"
    assert case["erp"]["label"] == VOSS_ORDER
    assert [f["label"] for f in case["erp"]["facts"]] == [
        "Status", "Total", "Lines", "Order date", "Customer", "Memo",
    ]
    erp = dict((f["label"], f["value"]) for f in case["erp"]["facts"])
    assert erp["Status"] == "pending-approval"
    assert erp["Total"] == "$34,960.00"
    # The count, not the lines: what the release does *not* touch is the
    # question this column answers, and two rows of SKUs would bury it.
    assert erp["Lines"] == "2"
    assert erp["Order date"] == "2026-08-17"


def test_a_transaction_names_the_feed_that_landed_it(found):
    """Read off the claims, because an order has no source-identity edge and
    never will — nothing proposes merges between sales orders. Reading only the
    edge left this blank and the console drew "no source record" beside a row
    that had plainly arrived from the ERP."""
    case = _screen(found)["standing"][0]
    assert case["crm"]["sources"] == ["crm-book"]
    assert case["erp"]["sources"] == ["erp-sales"]


def test_the_counterparty_is_the_edge_the_sweep_joined_on(found):
    """Not the order's denormalized `company_name`. The join that produced the
    finding ran on `sold_to`, and naming the party by anything else would let
    the screen and the sweep disagree about who this is about."""
    case = _screen(found)["standing"][0]
    assert case["counterparty"] == "Voss Automotive Group"
    assert case["finding_class"] == "won-not-released"


def test_a_standing_finding_names_no_decider(found):
    """The sweep stamps itself on the edge so it always says who stands behind
    it. Reporting that as a decision would put a machine's name where a
    person's belongs."""
    case = _screen(found)["standing"][0]
    assert case["status"] == STANDING
    assert case["decided_by"] == ""
    assert case["decided_at"] == ""
    assert case["resolved_how"] == ""
    assert case["resolution"] == ""
    # And it is still true, which is a different claim from "it was true".
    assert case["moved_since_found"] is False
    assert case["erp"]["state"] == case["erp"]["state_when_found"]


def test_the_evidence_is_the_sweeps_own_signals_with_their_weights(found):
    """Rules and not a model, which is the whole reason the desk can disagree
    with the number by disagreeing with a row."""
    case = _screen(found)["standing"][0]
    names = [s["name"] for s in case["signals"]]
    assert "required-conditions" in names
    assert all(s["detail"] for s in case["signals"])
    assert case["confidence"] > 0
    # The blend is addition over the rows above it.
    assert case["confidence"] == pytest.approx(
        round(sum(s["weight"] for s in case["signals"]), 4), abs=0.0001
    )
    assert case["policy_ref"].startswith("knowledge:state-agreement-policy@rev")


# ---------------------------------------------------------------------------
# The write, before it happens
# ---------------------------------------------------------------------------


def test_the_before_and_after_is_the_write_tables_own_words(found):
    """Read through `read_write_policy`, the release flow's first step — so the
    screen cannot promise a transition the Action would refuse. The graph holds
    `pending-approval`; the sentence is a promise about somebody else's system
    and uses that system's words."""
    change = _screen(found)["standing"][0]["change"]
    assert change["permitted"] is True
    assert change["why_not"] == ""
    assert change["field"] == "status"
    assert (change["from_value"], change["to_value"]) == (PENDING, RELEASED)
    assert (change["app"], change["resource"]) == ("erp", "salesOrders")
    assert change["target"] == "sales-order-status"


def test_the_record_id_is_the_erps_own_and_not_the_transaction_id(found):
    """`external_id` is the only thing that maps between `SO5037` and
    `salesOrders/5037`. String surgery on a transaction id is a guess that
    works until a customer numbers their orders differently."""
    change = _screen(found)["standing"][0]["change"]
    assert change["record_id"] == RECORD
    assert change["record_id"] != VOSS_ORDER


def test_what_does_not_change_is_derived_from_the_column_beside_it(found):
    """Not a typed list. The PATCH sets one field, so `unchanged` is every fact
    the column shows except that one — which means a second writable field
    would appear in both places at once or in neither."""
    case = _screen(found)["standing"][0]
    change = case["change"]
    shown = [f["label"].lower() for f in case["erp"]["facts"] if f["value"]]

    assert change["field"] not in change["unchanged"]
    assert change["unchanged"] == [
        label for label in shown if label != change["field"]
    ]
    # The four the plan names, in the order the column draws them.
    assert change["unchanged"][:4] == ["total", "lines", "order date", "customer"]


def test_an_order_that_has_moved_promises_nothing_and_says_why(client, world):
    """The finding was true when it was written and the book has since moved.
    Not a refusal — the release re-reads the ERP itself and escalates — but the
    screen must not draw an arrow it cannot stand behind."""
    for delivery in (_CRM, _ERP):
        world.sync.deliver(
            SyncRequest.model_validate(delivery), actor="system:test"
        )
    world.sync.sweep_disagreements(actor="system:test")

    billed = {
        **_ERP,
        "watermark": "2026-08-31T00:00:00Z",
        "sales_orders": [{**_ERP["sales_orders"][0], "status": "Billed"}],
    }
    world.sync.deliver(SyncRequest.model_validate(billed), actor="system:test")

    case = _screen(client)["standing"][0]
    assert case["moved_since_found"] is True
    assert case["erp"]["state"] == "billed"
    assert case["erp"]["state_when_found"] == "pending-approval"

    change = case["change"]
    assert change["permitted"] is False
    assert change["from_value"] == "" and change["to_value"] == ""
    assert "may not move an order out of" in change["why_not"]
    assert "re-read the ERP" in change["why_not"]


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_the_desk_may_decide_and_an_observer_reads_whose_work_it_is(found):
    mine = _screen(found, "customer-user:controller")["standing"][0]["gate"]
    assert mine["may_decide"] is True
    assert mine["why_not"] == ""
    assert mine["queue"] == ORDER_DESK_QUEUE
    assert mine["task_status"] == "open"
    assert mine["task_id"]

    theirs = _screen(found, FOREIGN)["standing"][0]["gate"]
    assert theirs["may_decide"] is False
    # A sentence naming the desk, never a disabled control with no reason.
    assert f"this is {ORDER_DESK_QUEUE}'s work" in theirs["why_not"]
    assert "you are bound to no queue" in theirs["why_not"]


def test_a_finding_with_no_task_names_both_readings(found, world):
    """A merge proposal with no Task is an anomaly the next sweep repairs. A
    finding with no Task may equally be one the desk already settled — the
    sweep never re-raises one — so telling a controller to sweep again would be
    telling them to re-raise a decision they made."""
    world.tasks.directory._items.pop(_task(world).id, None)
    gate = _screen(found)["standing"][0]["gate"]
    assert gate["may_decide"] is False
    assert gate["task_id"] == ""
    assert "never re-raises one that has been settled" in gate["why_not"]


# ---------------------------------------------------------------------------
# Settled findings, and how they ended
# ---------------------------------------------------------------------------


def test_leaving_it_as_it_is_settles_the_finding_without_a_resolution(found, world):
    """`dismissed` is the desk saying the two systems may disagree here. It
    ends by a judgment rather than by the ERP holding the released value, so it
    carries no `resolved_how` — and it must never be counted as one."""
    _decide(world, "rejected", note="Phase 1 is not invoiceable yet.")
    body = _screen(found)

    assert body["standing"] == []
    assert body["decided_total"] == 1
    case = body["decided"][0]
    assert case["status"] == DISMISSED
    assert case["resolved_how"] == ""
    assert case["decided_by"] == CONTROLLER.uid
    assert "Phase 1 is not invoiceable yet." in case["resolution"]
    assert case["gate"]["may_decide"] is False
    assert f"Already decided in {ORDER_DESK_QUEUE}" in case["gate"]["why_not"]


def test_a_release_this_system_made_is_counted_apart_from_one_it_found(found, world):
    """Two numbers off one status. `by_cumulus` is a write this system made and
    verified; `externally` is the ERP already holding the value with nothing in
    the journal asking for it. One number for both would make a system that
    changed nothing read like one that changed a customer's order."""
    _wire(world, PENDING)
    _decide(world)

    case = _screen(found)["decided"][0]
    assert case["status"] == RESOLVED
    assert case["resolved_how"] == BY_CUMULUS
    assert case["decided_by"] == CONTROLLER.uid
    assert case["decided_at"]

    panel = _panel(found)
    assert panel["standing"] == 0
    assert panel["outcomes"]["resolved"] == 1
    assert panel["outcomes"]["by_cumulus"] == 1
    assert panel["outcomes"]["externally"] == 0
    assert panel["outcomes"]["dismissed"] == 0


def test_a_release_somebody_else_made_is_named_as_theirs(found, world):
    """The ERP already holds the released value and the write journal never
    asked for it. Cumulus changed nothing, and the split says so."""
    _wire(world, RELEASED)
    _decide(world)

    case = _screen(found)["decided"][0]
    assert case["status"] == RESOLVED
    assert case["resolved_how"] == EXTERNALLY

    panel = _panel(found)
    assert panel["outcomes"]["resolved"] == 1
    assert panel["outcomes"]["externally"] == 1
    assert panel["outcomes"]["by_cumulus"] == 0


# ---------------------------------------------------------------------------
# The dashboard tile
# ---------------------------------------------------------------------------


def test_every_count_on_the_panel_is_answerable_by_a_row_on_the_screen(found):
    """The tile and the list it opens are one composition. A second derivation
    would be a second way to be wrong."""
    panel = _panel(found)
    screen = _screen(found, "naviant-engineer")

    assert panel["standing"] == len(screen["standing"])
    assert panel["awaiting_decision"] == sum(
        1 for c in screen["standing"] if c["gate"]["task_status"] == "open"
    )
    assert panel["last_sweep"] == screen["last_sweep"]
    # The sweep ran in this world, so there is something to be waiting on.
    assert panel["standing"] == 1
    assert panel["awaiting_decision"] == 1


def test_the_outcome_split_adds_up(found, world):
    """`resolved` is exactly `by_cumulus + externally`. An invariant rather
    than a coincidence: they are two readings of one status."""
    _wire(world, PENDING)
    _decide(world)
    outcomes = _panel(found)["outcomes"]
    assert outcomes["resolved"] == outcomes["by_cumulus"] + outcomes["externally"]
    assert outcomes["days"] == 30


def test_blocked_on_identity_is_the_sweeps_number_and_not_a_second_walk(blocked):
    """The number that says out loud what unfinished identity work is costing.

    It counts evidence the pass *declined to read* — a third system's
    engagement, hanging off a Company joined by a merge nobody has confirmed.
    That is a decision not to read; it leaves nothing on the graph to walk
    afterwards, so the panel reads the sweep's own arithmetic.
    """
    panel = _panel(blocked)
    assert panel["blocked_on_identity"] == 1
    assert panel["last_sweep"]["blocked_on_identity"] == 1

    # And the withheld evidence really is absent from the case: the delivery
    # engagement is inadmissible until a steward confirms the merge.
    case = _screen(blocked)["standing"][0]
    assert "delivery-engagement" not in [s["name"] for s in case["signals"]]


def test_the_panel_carries_the_sweeps_own_arithmetic_unchanged(found, world):
    """Not re-derived. Every field on `last_sweep` is a key the run published,
    and the panel is a reader of it."""
    out = world.sync.sweep_disagreements(actor="system:test")
    sweep = _panel(found)["last_sweep"]
    for key in (
        "orders_examined", "candidates", "found", "blocked_on_identity",
        "order_without_deal", "ambiguous",
    ):
        assert sweep[key] == out[key], key
    assert sweep["run_id"] == out["run_id"]
    assert sweep["status"] == out["status"]


# ---------------------------------------------------------------------------
# The empty world
# ---------------------------------------------------------------------------


def test_a_book_nothing_has_swept_says_so_rather_than_reporting_zero(empty):
    """A `blocked_on_identity` of 0 over a book nothing has swept is the most
    misleading number this panel could draw, so `last_sweep` is what tells the
    two apart — never a zeroed row standing in for a pass that did not happen.
    """
    body = _screen(empty)
    assert body["standing"] == []
    assert body["decided"] == []
    assert body["decided_total"] == 0
    assert body["last_sweep"] is None

    panel = _panel(empty)
    assert panel["last_sweep"] is None
    assert panel["standing"] == 0
    assert panel["blocked_on_identity"] == 0
    assert panel["outcomes"]["resolved"] == 0
    assert panel["outcomes"]["dismissed"] == 0


def test_a_finding_pointing_at_a_row_the_book_no_longer_holds_is_a_finding(
    found, world
):
    """Named rather than dropped: a silently shorter list is how a screen hides
    the one case somebody needs to look at."""
    order = next(
        o for o in world.graph.find(SCAN.to_type) if o.natural_key == VOSS_ORDER
    )
    world.graph.store._objects.pop(order.id, None)

    case = _screen(found)["standing"][0]
    assert case["erp"]["missing"] is True
    assert case["erp"]["facts"] == []
    assert case["erp"]["book"] == "the ERP"
    # And the write block refuses rather than describing a PATCH at an address
    # nothing answers.
    assert case["change"]["permitted"] is False
    assert case["change"]["why_not"]
