"""The cross-system disagreement detector, and the promises it must not break.

Four groups of property, and the second and third are why this module exists
rather than a few assertions bolted onto `test_sync_commerce.py`:

1. **It finds the disagreement the estate really holds** — Voss's deal closed
   won on the 14th against an order still at Pending Approval on the 17th, with
   the evidence a controller would need to disagree with it.
2. **It does not find what the answer key says is not there** — the same
   company's two older wins, which are outside the window and would be the
   easiest false positive in the book, and Blue Harbor's held order, which has
   no won deal behind it at all and is counted rather than raised.
3. **It refuses evidence that rests on an unconfirmed merge.** The same
   fixture, run both ways: with the Voss identity proposal at `in_review` a
   third system's engagement is inadmissible and counted; with a steward's
   confirmation on it, the same pass admits the same record. That is the whole
   argument for the identity work stated as a test rather than as a paragraph.
4. **It records and never releases.** Checked structurally: the ledger entry
   for the write step names only `link:` refs, so a change that started
   touching the order would fail here rather than in somebody's ERP.

The fixture is the live estate's own rows, recorded — the same Voss and Blue
Harbor numbers the workplan's ground-truth section verified against the running
book. **Flagged for S2-E:** these belong in `eval_specs` as the
`state/naviant-disagreement` fixture, so that one recording feeds the unit
tests and the graded suite the way `_IDENTITY_*` already does; they are
declared here because the eval spec is a later stage and the sweep needed a
world before it had one.
"""

import pytest

from app.auth import Principal
from app.disagreement_sweep import (
    DISAGREEMENT_SWEEP_FLOW_ID,
    FINDING_STATUSES,
    ORDER_DESK_QUEUE,
    SCAN,
    deal_subject,
    standing_findings,
)
from app.graph import link_id, object_id
from app.models import AssetUpdate
from app.ontology import ONTOLOGY
from app.resolution import parse_state_policy, read_state_policy
from app.sync import SyncRequest
from tests import world as test_world

VOSS_DEAL = "Voss — Title & Service Records Capture"
VOSS_ORDER = "SO5037"
BLUE_HARBOR_ORDER = "SO5003"
OLDER_WINS = ("Voss — Service Drive Scanner Rollout", "Voss — Deal Jacket Capture")


#: The CRM's half, as the live book holds it: three Voss wins, only one of them
#: near the order, and a Blue Harbor deal that never closed.
_CRM = {
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
        {"external_id": "O1", "name": VOSS_DEAL, "account_external_id": "A1",
         "stage": "closed-won", "amount": 128000.0,
         "close_date": "2026-08-14"},
        # Five months earlier, and its own order was billed long ago. The
        # nearest false positive in the estate: same counterparty, same state,
        # a name that shares two words with the order's memo.
        {"external_id": "O2", "name": OLDER_WINS[0],
         "account_external_id": "A1", "stage": "closed-won",
         "amount": 44000.0, "close_date": "2026-03-20"},
        {"external_id": "O3", "name": OLDER_WINS[1],
         "account_external_id": "A1", "stage": "closed-won",
         "amount": 61000.0, "close_date": "2025-11-14"},
        # Never won. The control that separates "no deal" from "no deal in the
        # window", which are different facts about an estate.
        {"external_id": "O4", "name": "Blue Harbor — Fleet Telematics Rollout",
         "account_external_id": "A2", "stage": "prospecting",
         "amount": 95000.0, "close_date": "2026-12-01"},
    ],
}

#: The ERP's half. Two held orders in the whole book, which is what the live
#: estate holds and what makes the counts here assertable rather than
#: approximate.
_ERP = {
    "source": "erp-sales",
    "watermark": "2026-08-30T00:00:00Z",
    "customers": [
        {"external_id": "1007", "name": "Voss Automotive Group",
         "email": "ap@vossauto.example.com", "phone": "(608) 555-0111"},
        {"external_id": "1011", "name": "Blue Harbor Logistics",
         "email": "ap@blueharbor.example.com", "phone": "(414) 555-0122"},
    ],
    "sales_orders": [
        {"external_id": "5037", "order_number": VOSS_ORDER,
         "customer_external_id": "1007", "ordered_date": "2026-08-17",
         "status": "Pending Approval", "total": 34960.0,
         "memo": "Title and service records capture — phase 1"},
        {"external_id": "5003", "order_number": BLUE_HARBOR_ORDER,
         "customer_external_id": "1011", "ordered_date": "2026-08-11",
         "status": "Pending Approval", "total": 6900.0,
         "memo": "Dock scanning refresh"},
        # Released, and therefore not a disagreement however well it matches.
        {"external_id": "5025", "order_number": "SO5025",
         "customer_external_id": "1007", "ordered_date": "2026-03-18",
         "status": "Billed", "total": 8850.0,
         "memo": "Service drive scanner rollout"},
    ],
}

#: The delivery system's half, under the nickname a coordinator typed. It mints
#: its own Company — `Voss Auto` is not `Voss Automotive Group` to any
#: resolver — and the identity sweep proposes the merge at `in_review`, which
#: is exactly the state this module has to refuse to read across.
_PSA = {
    "source": "psa-book",
    "watermark": "2026-08-30T00:00:00Z",
    "psa_clients": [
        {"external_id": "107", "name": "Voss Auto",
         "contact_name": "Dana Reyes",
         "contact_email": "dana.reyes@vossauto.example.com"},
    ],
    "projects": [
        # Started five days after the order was raised and immediately parked.
        # The third system's account of the same commitment, and the evidence
        # the unconfirmed merge is holding back.
        {"external_id": "105", "project_code": "ENG-0105",
         "client_external_id": "107",
         "name": "Voss Auto — title and service records capture",
         "status": "on-hold", "project_manager": "S. Hoffmann",
         "start_date": "2026-08-22", "target_end_date": "2026-11-18",
         "budget_hours": 260.0, "actual_hours": 12.0},
        # Long finished, and outside every window. Here so that "a project
        # exists for this customer" cannot be what makes the signal fire.
        {"external_id": "115", "project_code": "ENG-0115",
         "client_external_id": "107",
         "name": "Voss Auto — deal jacket capture install",
         "status": "completed", "project_manager": "Danielle G.",
         "start_date": "2025-11-23", "target_end_date": "2025-12-23",
         "budget_hours": 110.0},
    ],
}

#: A second counterparty, for the one-sided amount rule alone. Its order is
#: three times its deal, which no honest reading makes a phase of it.
_OVERSIZED = {
    "source": "erp-sales",
    "watermark": "2026-08-30T00:00:00Z",
    "customers": [
        {"external_id": "1020", "name": "Portage Plastics",
         "phone": "(920) 555-0170"},
    ],
    "sales_orders": [
        {"external_id": "5099", "order_number": "SO5099",
         "customer_external_id": "1020", "ordered_date": "2026-08-12",
         "status": "Pending Approval", "total": 30000.0,
         "memo": "Job ticket capture"},
    ],
}

_OVERSIZED_CRM = {
    "source": "crm-book",
    "watermark": "2026-08-30T00:00:00Z",
    "accounts": [
        {"external_id": "A9", "name": "Portage Plastics",
         "phone": "(920) 555-0170"},
    ],
    "opportunities": [
        {"external_id": "O9", "name": "Portage — Job Ticket Capture",
         "account_external_id": "A9", "stage": "closed-won",
         "amount": 10000.0, "close_date": "2026-08-10"},
    ],
}


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


@pytest.fixture()
def booked(world):
    """Three feeds delivered, and the identity sweep run over what they wrote.

    The identity pass runs here rather than in the tests that need it because
    it is a precondition of the estate rather than of any one assertion: in
    production it is what has already happened by the time a state window
    closes, and the proposals it leaves at `in_review` are the state this
    module is written against.
    """
    for delivery in (_CRM, _ERP, _PSA):
        world.sync.deliver(
            SyncRequest.model_validate(delivery), actor="system:test"
        )
    world.sync.sweep_identities(actor="system:test")
    return world


def _findings(world) -> dict[tuple[str, str], object]:
    """Every live finding, keyed by the labels at its two ends."""
    out = {}
    for link in world.graph.store.links():
        if link.type != SCAN.link_type or not link.live:
            continue
        left = world.graph.get(link.from_id)
        right = world.graph.get(link.to_id)
        out[(left.label, right.label)] = link
    return out


def _desk_task(world):
    return next(
        t for t in world.tasks.list() if t.kind == "state_disagreement"
    )


def _confirm_the_voss_merge(world) -> None:
    """Answer the identity sweep's proposal the way a steward would.

    Through the Task and the decision hook rather than by writing the link
    prop, because the demo beat this unlocks is a person clicking approve in
    the workbench, and a test that reached past that would not be exercising
    the path the beat runs on.
    """
    task = next(
        t for t in world.tasks.list()
        if t.kind == "merge_review" and "Voss Auto" in t.title
    )
    decided = world.tasks.decide(
        task.id, verdict="approved", decided_by="dana@naviant.com",
        note="The PSA's nickname for the same dealer group.",
    )
    assert world.sync.apply_merge_decision(decided)["action"] == "confirmed"


# ---------------------------------------------------------------------------
# The policy is governed knowledge
# ---------------------------------------------------------------------------


def test_the_windows_and_bands_come_off_the_asset_and_not_out_of_the_code(world):
    policy = read_state_policy(world.knowledge)
    assert policy.found_asset
    assert policy.source_ref.startswith("knowledge:state-agreement-policy@rev")
    assert (policy.lead_days, policy.lag_days) == (21, 30)
    assert (policy.amount_floor, policy.amount_ceiling) == (0.10, 1.15)
    # The class table is data too. A second class is a row in the document,
    # which is the property that makes "wave 1 ships one class" a scheduling
    # decision rather than an architectural one.
    assert policy.disagreements == (
        ("closed-won", "pending-approval", "won-not-released"),
    )
    assert policy.classify("closed-won", "pending-approval") == "won-not-released"
    assert policy.classify("closed-won", "billed") == ""


def test_a_missing_policy_leaves_defaults_rather_than_correlating_everything(world):
    for asset in world.knowledge.repo.all():
        world.knowledge.repo.delete(asset.id)
    policy = read_state_policy(world.knowledge)
    assert not policy.found_asset
    # Conservative, not permissive: a lost policy must not open the window to
    # infinity and correlate every won deal a customer ever had.
    assert (policy.lead_days, policy.lag_days) == (21, 30)
    assert policy.disagreements[0][2] == "won-not-released"


def test_the_table_header_does_not_parse_as_a_disagreement_class():
    """The pattern is anchored on backticks for this reason. A looser one reads
    the header row and the `|---|` rule as data, and an estate would acquire a
    class called `the-crm-says` that nothing could ever match."""
    parsed = parse_state_policy(
        "| The CRM says | The ERP says | The disagreement |\n"
        "|---|---|---|\n"
        "| `closed-won` | `pending-approval` | `won-not-released` |\n"
        "| `closed-won` | `pending-fulfillment` | `won-not-shipped` |\n"
    )
    assert parsed.disagreements == (
        ("closed-won", "pending-approval", "won-not-released"),
        ("closed-won", "pending-fulfillment", "won-not-shipped"),
    )


def test_a_truncated_policy_falls_back_to_the_declared_class_not_to_none():
    """A document somebody half-saved must not read as "nothing is a
    disagreement" — the failure that looks exactly like a clean estate."""
    assert parse_state_policy("# State Agreement Policy\n").disagreements == (
        ("closed-won", "pending-approval", "won-not-released"),
    )


# ---------------------------------------------------------------------------
# What it finds
# ---------------------------------------------------------------------------


def test_the_won_deal_and_the_unreleased_order_are_correlated(booked):
    """The one live instance of the class, with the evidence attached.

    The join is the load-bearing part: `pursued_with` and `sold_to` land on one
    Company because resolution already reconciled the CRM's `Voss Automotive
    Group` with the ERP's spelling. A name join would have failed here, which
    is why the sweep does not attempt one.
    """
    out = booked.sync.sweep_disagreements(actor="system:test")
    assert out["status"] == "succeeded"
    assert out["found"] == 1 and out["standing"] == 1

    link = _findings(booked)[(VOSS_DEAL, VOSS_ORDER)]
    assert link.type == "deal_disagrees_with_order"
    assert link.props["class"] == "won-not-released"
    # `standing` is the only status this flow writes; the other three are moved
    # by the order desk's decision and by a later pass, and the set is declared
    # whole so a reader does not have to discover it one stage at a time.
    assert link.props["status"] == "standing"
    assert FINDING_STATUSES == (
        "standing", "resolved", "dismissed", "superseded",
    )
    assert link.props["status"] in FINDING_STATUSES
    assert link.method == "derived"
    assert 0.85 <= link.confidence <= 0.95
    assert link.props["correlation_confidence"] == pytest.approx(link.confidence)
    assert link.props["policy_ref"].startswith("knowledge:state-agreement-policy")
    # What both rows said when it was found. Carried because the finding stops
    # being true the moment either end moves, and the release Action has to be
    # able to tell "still the case" from "was the case".
    assert link.props["state_when_found"] == {
        "opportunity_stage": "closed-won", "order_status": "pending-approval",
    }

    signals = {s["name"] for s in link.props["signals"]}
    # All three corroborators fire on this pair, which is what makes it the
    # class's demonstration case rather than merely its only one.
    assert signals == {
        "required-conditions", "name-proximity", "amount-containment",
        "sole-candidate",
    }


def test_the_evidence_reads_as_sentences_a_controller_could_argue_with(booked):
    booked.sync.sweep_disagreements(actor="system:test")
    link = _findings(booked)[(VOSS_DEAL, VOSS_ORDER)]
    detail = {s["name"]: s["detail"] for s in link.props["signals"]}

    assert "Voss Automotive Group" in detail["required-conditions"]
    assert "3 day(s) from the close" in detail["required-conditions"]
    # The account prefix comes off before the memo comparison, or every deal at
    # a company scores alike — the prefix is the half two rows are guaranteed
    # to share and therefore the half that says nothing about which deal it is.
    assert "Title & Service Records Capture" in detail["name-proximity"]
    assert "Voss —" not in detail["name-proximity"]
    assert "27% of the deal" in detail["amount-containment"]


def test_the_account_prefix_comes_off_a_deal_name_and_nothing_else_does():
    assert deal_subject(VOSS_DEAL) == "Title & Service Records Capture"
    assert deal_subject("Dane County — Plat Map Digitization") == "Plat Map Digitization"
    # A name with no prefix survives intact rather than losing its first word.
    assert deal_subject("Plat Map Digitization") == "Plat Map Digitization"


# ---------------------------------------------------------------------------
# What it refuses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("deal", OLDER_WINS)
def test_the_same_customers_older_wins_are_not_linked(booked, deal):
    """Same counterparty, same two states, names that share words with the
    memo. The window is the only thing separating them from the finding, and
    it is the rule the estate would embarrass first."""
    out = booked.sync.sweep_disagreements(actor="system:test")
    assert (deal, VOSS_ORDER) not in _findings(booked)
    # Counted, not silently skipped: "we looked and they were out of range" is
    # a different fact from "we never looked", and only the first is reportable.
    assert out["outside_window"] == 2


def test_a_held_order_with_no_won_deal_is_counted_and_never_raised(booked):
    """Blue Harbor's order is stuck for a reason that is not a disagreement:
    nobody ever won the deal. One-ended, so there is no edge to write, and the
    honest output is a number rather than a finding."""
    out = booked.sync.sweep_disagreements(actor="system:test")
    assert out["order_without_deal"] == 1
    assert out["orders_examined"] == 2
    assert not [
        pair for pair in _findings(booked) if BLUE_HARBOR_ORDER in pair
    ]
    # And nobody was paged about it. A count on a dashboard, not a Task.
    assert len([t for t in booked.tasks.list()
                if t.kind == "state_disagreement"]) == 1


def test_a_released_order_is_not_a_disagreement_however_well_it_matches(booked):
    """SO5025's memo is the older deal's name almost verbatim and it sits three
    days from that close date. It is `billed`, so the states do not disagree
    and no amount of correlation makes it a finding."""
    booked.sync.sweep_disagreements(actor="system:test")
    assert not [pair for pair in _findings(booked) if "SO5025" in pair]


def test_an_order_larger_than_its_deal_does_not_corroborate_on_amount(world):
    """The one-sided rule, stated as the case that would break a symmetric one.

    An order is routinely a *phase* of a deal and legitimately much smaller —
    Voss's is 27% — so the band's floor is generous. An order three times its
    deal is a different commitment, and a symmetric band around 1.0 would have
    scored it as the better match of the two.
    """
    for delivery in (_OVERSIZED_CRM, _OVERSIZED):
        world.sync.deliver(
            SyncRequest.model_validate(delivery), actor="system:test"
        )
    world.sync.sweep_disagreements(actor="system:test")

    link = _findings(world)[("Portage — Job Ticket Capture", "SO5099")]
    signals = {s["name"] for s in link.props["signals"]}
    assert "amount-containment" not in signals
    # Still raised, on the corroborator that does apply — the point is that the
    # amount contributed nothing, not that the pair vanished.
    assert "sole-candidate" in signals


# ---------------------------------------------------------------------------
# The identity gate — the same fixture, both ways
# ---------------------------------------------------------------------------


def test_an_unconfirmed_merge_withholds_a_third_systems_evidence(booked):
    """Status-gating in the doctrine's sense, and the number that sells it.

    The PSA's engagement for this exact commitment — started five days after
    the order and immediately parked — hangs off `Voss Auto`, joined to the
    counterparty by a `company_same_as` no steward has confirmed. The Gateway
    refuses to act on an unconfirmed proposal and so does this: the record is
    not read, the finding stands without it, and the cost of the unfinished
    identity work is reported rather than absorbed.
    """
    out = booked.sync.sweep_disagreements(actor="system:test")
    assert out["blocked_on_identity"] == 1
    assert out["found"] == 1

    link = _findings(booked)[(VOSS_DEAL, VOSS_ORDER)]
    assert "delivery-engagement" not in {
        s["name"] for s in link.props["signals"]
    }


def test_confirming_the_merge_admits_the_same_record_on_the_next_pass(booked):
    """The other half, over the same fixture and the same policy. Nothing about
    the sweep changes between the two — one link's status does."""
    _confirm_the_voss_merge(booked)
    out = booked.sync.sweep_disagreements(actor="system:test")

    assert out["blocked_on_identity"] == 0
    link = _findings(booked)[(VOSS_DEAL, VOSS_ORDER)]
    engagement = next(
        s for s in link.props["signals"] if s["name"] == "delivery-engagement"
    )
    assert "ENG-0105" in engagement["detail"]
    assert "on-hold" in engagement["detail"]
    # The finished engagement from last November is not evidence about this
    # commitment — the window applies to a third system's record too, or "the
    # customer has projects" would be the signal.
    assert "ENG-0115" not in engagement["detail"]


def test_a_merge_still_in_review_is_not_walked_however_confident_it_is(booked):
    """The gate is the *status*, not the score. This proposal sits at 0.95 —
    higher than the sweep's own findings ever go — and carries nothing."""
    proposal = next(
        link for link in booked.graph.store.links()
        if link.type == "company_same_as" and link.live
    )
    assert proposal.props["status"] == "in_review"
    assert proposal.confidence >= 0.90
    assert booked.sync.sweep_disagreements(
        actor="system:test"
    )["blocked_on_identity"] == 1


# ---------------------------------------------------------------------------
# Record, never release
# ---------------------------------------------------------------------------


def test_the_guarded_write_touches_edges_and_nothing_else(booked):
    """The module's central promise, checked structurally rather than read.

    The Gateway records every ref a step wrote, so a change that started
    stamping a status onto the order — or worse, releasing it — shows up here
    as a `graph:` ref. That is the difference between a promise and an
    invariant, and it matters more here than on the identity sweep: the object
    this flow declines to touch is one Cumulus can write to over HTTP.
    """
    before = {o.id: o.revision for o in booked.graph.store.objects()}
    booked.sync.sweep_disagreements(actor="system:test")

    entry = next(
        e for e in booked.gateway.ledger.list()
        if e.action == "record-disagreements"
    )
    assert entry.outcome == "committed"
    assert entry.writes
    assert all(ref.startswith("link:") for ref in entry.writes)
    assert {o.id: o.revision for o in booked.graph.store.objects()} == before


def test_the_order_keeps_the_status_the_erp_gave_it(booked):
    """Said again at the object, because this is the failure that would be
    invisible: a finding is only useful while the order still holds the state
    it was found in, and a sweep that helpfully moved it would destroy the
    fact it exists to report."""
    booked.sync.sweep_disagreements(actor="system:test")
    order = booked.graph.get(object_id("SalesOrder", VOSS_ORDER))
    assert order.props["status"] == "pending-approval"
    deal = booked.graph.get(_findings(booked)[(VOSS_DEAL, VOSS_ORDER)].from_id)
    assert deal.props["stage"] == "closed-won"


def test_no_model_call_on_any_path(booked):
    """Every signal is a date, a ratio, a state or a name similarity, and the
    blend is addition. The whole value of the number is that a controller can
    read the evidence beside it and disagree."""
    out = booked.sync.sweep_disagreements(actor="system:test")
    run = booked.engine.get(out["run_id"])
    assert not [s for s in run.steps if s.kind == "modelcall"]
    assert run.cost_usd == 0.0


def test_the_run_is_started_by_a_dispatched_event_like_every_other_flow(booked):
    booked.sync.sweep_disagreements(actor="system:test")
    event = next(
        e for e in booked.dispatcher.events.list()
        if e.name == "StateWindowClosed"
    )
    assert event.triggers == ["state-window-closed"]
    run = booked.engine.get(event.starts[0])
    assert run.flow_id == DISAGREEMENT_SWEEP_FLOW_ID
    assert run.caused_by == event.id

    from app.dispatch import is_windowed_flow

    assert is_windowed_flow(DISAGREEMENT_SWEEP_FLOW_ID)
    # And the journal is the six steps the Tier-2 export declares, in order.
    assert [s.step_id for s in run.steps] == ["t1", "x1", "z1", "a1", "k1", "e1"]


def test_the_declared_export_matches_what_the_run_actually_journals(booked):
    """The transparency bar's own check: a published flow definition that
    drifts from its runs is a map of a system nobody is operating."""
    from app.flows import _seed_definitions

    out = booked.sync.sweep_disagreements(actor="system:test")
    run = booked.engine.get(out["run_id"])
    definition = next(
        d for d in _seed_definitions() if d.id == DISAGREEMENT_SWEEP_FLOW_ID
    )
    assert [s.id for s in definition.steps] == [s.step_id for s in run.steps]
    assert [s.name for s in definition.steps] == [s.name for s in run.steps]
    assert definition.queues == [ORDER_DESK_QUEUE]


# ---------------------------------------------------------------------------
# Idempotence, and a decision that stays decided
# ---------------------------------------------------------------------------


def test_a_second_sweep_changes_nothing(booked):
    """Link ids derive from the endpoints, so a re-run addresses the same
    edges and leaves them as they are rather than restamping them."""
    first = booked.sync.sweep_disagreements(actor="system:test")
    assert (first["recorded"], first["already_recorded"]) == (1, 0)
    assert first["desk_tasks"] == 1

    second = booked.sync.sweep_disagreements(actor="system:test")
    assert (second["recorded"], second["already_recorded"]) == (0, 1)
    assert second["standing"] == 1
    assert second["desk_tasks"] == 0 and second["desk_tasks_standing"] == 1
    assert len(_findings(booked)) == 1
    assert len([t for t in booked.tasks.list()
                if t.kind == "state_disagreement"]) == 1


def test_a_dismissed_finding_is_never_re_raised(booked):
    """The promise the reject button makes out loud: "leave it as it is" holds
    until the two systems themselves change.

    A scheduled pass that re-raised a dismissal would be a machine overruling a
    person once a night, quietly, forever — and it is the quietest failure in
    the module, because every individual run looks correct.

    The status is moved directly here because the decision hook that will move
    it lands with the release flow in S2b-2. What is under test is the sweep's
    guard, which is this stage's to keep.
    """
    booked.sync.sweep_disagreements(actor="system:test")
    task = _desk_task(booked)
    edge = link_id(
        SCAN.link_type,
        *[(link.from_id, link.to_id) for link in booked.graph.store.links()
          if link.type == SCAN.link_type][0],
    )
    link = booked.graph.store.get_link(edge)
    link.props["status"] = "dismissed"
    booked.graph.store.put_link(link)
    booked.tasks.decide(
        task.id, verdict="rejected", decided_by="raymond.fitzgerald@naviant.com",
        note="Customer asked us to hold the order; the deal is real.",
    )

    out = booked.sync.sweep_disagreements(actor="system:test")
    assert out["recorded"] == 0 and out["standing"] == 0
    assert out["desk_tasks"] == 0 and out["desk_tasks_withheld"] == 1
    assert booked.graph.store.get_link(edge).props["status"] == "dismissed"
    # And the human's verdict survives the pass that ran after it.
    after = booked.tasks.get(task.id)
    assert after.status == "done"
    assert after.decision.verdict == "rejected"


def test_a_decided_task_is_left_alone_even_with_the_finding_still_standing(booked):
    """The second guard, on its own. `TaskService.create` is a put, so a sweep
    that raised its Tasks unconditionally would reset an answered one to
    `open` and drop the verdict with it."""
    booked.sync.sweep_disagreements(actor="system:test")
    task = _desk_task(booked)
    booked.tasks.decide(
        task.id, verdict="approved",
        decided_by="raymond.fitzgerald@naviant.com",
    )

    booked.sync.sweep_disagreements(actor="system:test")
    after = booked.tasks.get(task.id)
    assert after.status == "done" and after.decision.verdict == "approved"


# ---------------------------------------------------------------------------
# The mutation proof: the numbers really are in the document
# ---------------------------------------------------------------------------


def test_narrowing_the_window_in_the_document_loses_the_finding(booked, world):
    """The proof that the policy is load-bearing, run rather than described.

    It edits the *asset*, not the code, which is the sharper demonstration: a
    controller who thinks a month is too generous can tighten it and watch the
    finding go, with nothing rebuilt and nothing redeployed. Two days is absurd
    as policy and perfect as a test — Voss's order is three days after its
    close, so the finding survives every wider setting and only this one.
    """
    assert booked.sync.sweep_disagreements(actor="system:test")["found"] == 1

    asset = world.knowledge.repo.get("state-agreement-policy")
    world.knowledge.update(asset.id, AssetUpdate(
        content=asset.content.replace(
            "Close the window: 30 days after it.",
            "Close the window: 2 days after it.",
        ),
    ))
    assert read_state_policy(world.knowledge).lag_days == 2

    out = booked.sync.sweep_disagreements(actor="system:test")
    assert out["found"] == 0
    assert out["outside_window"] == 3
    # The finding already on the graph is not withdrawn by a tightened policy —
    # withdrawing an assertion is a decision, and re-tuning a threshold is not
    # a way to make one silently.
    assert out["standing"] == 1


def test_emptying_the_class_table_in_the_document_stops_the_sweep(world):
    """The class table is data. A row is what makes a pair of states a
    disagreement, and there is no second place in the code that also knows."""
    for delivery in (_CRM, _ERP):
        world.sync.deliver(
            SyncRequest.model_validate(delivery), actor="system:test"
        )
    asset = world.knowledge.repo.get("state-agreement-policy")
    world.knowledge.update(asset.id, AssetUpdate(
        content=asset.content.replace(
            "| `closed-won` | `pending-approval` | `won-not-released` |",
            "| `closed-won` | `pending-fulfillment` | `won-not-shipped` |",
        ),
    ))
    out = world.sync.sweep_disagreements(actor="system:test")
    assert out["found"] == 0 and out["standing"] == 0
    # It did not stop *looking* — it looked for a different disagreement and
    # this estate does not hold one.
    assert out["orders_examined"] == 0


def test_loosening_the_name_floor_does_not_by_itself_raise_an_older_win(booked, world):
    """A guard on the mutation proof above: the window is doing the work.

    Dropping the corroboration floor to nothing must not resurrect the older
    wins, because they fail a *required* condition rather than a corroborator —
    and a policy edit that could talk the sweep past a required condition would
    mean the three conditions were never required.
    """
    asset = world.knowledge.repo.get("state-agreement-policy")
    world.knowledge.update(asset.id, AssetUpdate(
        content=asset.content.replace(
            "Corroborate on names at or above: 0.60",
            "Corroborate on names at or above: 0.01",
        ),
    ))
    out = booked.sync.sweep_disagreements(actor="system:test")
    assert out["found"] == 1
    assert not [
        pair for pair in _findings(booked) if pair[0] in OLDER_WINS
    ]


# ---------------------------------------------------------------------------
# The desk: releasing revenue is not the AP clerk's call
# ---------------------------------------------------------------------------
#
# The queue is the governance. Connie sits on the same rung of the write ladder
# as Raymond and owns different work: she decides who Cumulus *pays*, and the
# authority to decide what it *bills* runs the other way. Nothing in the role
# ladder can tell them apart — only the binding can, which is why it is tested
# through the route that enforces it.

#: The AP clerk, as the roster binds her.
_AP_CLERK = Principal(
    uid="persona_customer_user_ap", email="connie.alberts@naviant.com",
    role="user", scope="own", queues=("ap-clerk", "expense-review"),
)

#: The controller, who owns the order desk and both master-data desks.
_CONTROLLER = Principal(
    uid="persona_customer_user_controller",
    email="raymond.fitzgerald@naviant.com", role="user", scope="own",
    queues=("ap-clerk", "expense-review", "vendor-stewardship",
            "data-stewardship", "order-desk"),
)


@pytest.fixture()
def as_persona(_client):
    from app.auth import get_principal
    from app.main import app

    def _use(principal):
        app.dependency_overrides[get_principal] = lambda: principal
        return _client

    yield _use
    app.dependency_overrides.pop(get_principal, None)


def test_the_finding_is_raised_to_the_order_desk_and_proposes_a_release(booked):
    booked.sync.sweep_disagreements(actor="system:test")
    task = _desk_task(booked)
    assert task.queue == ORDER_DESK_QUEUE == "order-desk"
    assert task.case_ref.startswith("link:deal_disagrees_with_order/")
    assert task.proposal.action == "release-sales-order"
    assert VOSS_ORDER in task.title
    # The two systems' own words, side by side, before any of the scoring.
    assert {e.label for e in task.evidence} >= {
        "Correlation", "The CRM says", "The ERP says",
    }
    assert any("state-agreement-policy" in ref for ref in task.refs)


def test_the_ap_clerk_may_not_decide_a_release(booked, as_persona):
    """Testable scoping, and the reason the queue is new rather than reused.
    Putting this in `ap-clerk` would have handed the authority that approves
    what Cumulus pays the power to decide what it bills."""
    booked.sync.sweep_disagreements(actor="system:test")
    task = _desk_task(booked)

    resp = as_persona(_AP_CLERK).post(
        f"/api/v1/tasks/{task.id}/decide", json={"verdict": "approved"},
    )
    assert resp.status_code == 403, resp.text
    assert "order-desk" in resp.json()["detail"]
    # And nothing moved: refused at the door, so there is no decision to undo.
    assert booked.tasks.get(task.id).status == "open"


def test_the_controller_may(booked, as_persona):
    booked.sync.sweep_disagreements(actor="system:test")
    task = _desk_task(booked)

    resp = as_persona(_CONTROLLER).post(
        f"/api/v1/tasks/{task.id}/decide",
        json={"verdict": "approved", "note": "Release it."},
    )
    assert resp.status_code == 200, resp.text
    after = booked.tasks.get(task.id)
    assert after.status == "done"
    assert after.decision.decided_by == _CONTROLLER.uid


def test_the_order_desk_is_declared_and_bound_to_exactly_one_persona():
    """The binding, read off the roster rather than off a fixture — a queue
    nothing enumerates is a queue a persona can be silently unbound from."""
    from app.personas import ALL_PERSONAS, ALL_QUEUES

    assert ORDER_DESK_QUEUE in ALL_QUEUES
    # Platform-scoped personas are excluded because the Naviant engineer binds
    # to `ALL_QUEUES` by construction — he owns every desk in the deployment,
    # which says nothing about how the customer's own authority is split.
    holders = {
        p.id for p in ALL_PERSONAS
        if ORDER_DESK_QUEUE in p.queues and p.scope != "platform"
    }
    assert holders == {"customer-user:controller"}


# ---------------------------------------------------------------------------
# The seams the rest of the platform reads
# ---------------------------------------------------------------------------


def test_the_finding_link_is_declared_and_is_not_an_identity_link():
    """It carries confidence and a status the way an identity link does, and it
    asserts the opposite kind of thing. A resolver walking this one looking for
    a party would find a deal and an order and be wrong about both."""
    declared = ONTOLOGY.link_type(SCAN.link_type)
    assert declared is not None
    assert (declared.from_type, declared.to_type) == ("Opportunity", "SalesOrder")
    assert declared.cardinality == "many-to-many"
    assert not declared.identity and not declared.composes


def test_recording_a_disagreement_is_nobody_s_work_and_carries_no_queue():
    """The `propose-identity-link` reasoning, on the second flow that needs it.

    A queue binding says whose *work* an Action is. Noticing that two systems
    disagree is not anybody's work — the Flow does it unasked — and a queue
    here would refuse the Flow outright, since a Flow principal owns only the
    queues its declaration binds it to. The desk appears on the Task instead.
    """
    from app.gateway import ACTIONS

    action = ACTIONS["record-disagreements"]
    assert action.queue == "" and not action.queue_from_task
    assert action.target == "entity-layer"
    # `decision` and not `observation`: no source reported this. It is a
    # determination Cumulus made by reading two sources against a policy.
    assert action.regime == "decision"


def test_standing_findings_reads_only_what_is_live_and_standing(booked):
    booked.sync.sweep_disagreements(actor="system:test")
    assert len(standing_findings(booked.graph)) == 1
    link = standing_findings(booked.graph)[0]
    link.props["status"] = "resolved"
    booked.graph.store.put_link(link)
    assert standing_findings(booked.graph) == []
