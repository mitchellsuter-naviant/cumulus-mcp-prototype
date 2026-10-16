"""The cross-source identity proposer, and the promise it must not break.

Three groups of property, and the middle one is the reason this module exists
rather than a handful of assertions bolted onto `test_sync_commerce.py`:

1. **It finds what the estate's answer key says is there** — the ERP's stale
   Northwind, the CRM's duplicate Verona account, the re-typed Thomas Berg.
2. **It does not find what the answer key says is not** — the two bait pairs,
   and the merely-similar neighbours the roster documents as *not* designed
   traps. A matcher is only as good as what it refuses, and every one of these
   is a pair that shares two words of three.
3. **It proposes and never merges.** Checked structurally rather than by
   inspection: the ledger entry for the write step names only `link:` refs, so
   a future change that started upserting an object fails here rather than in
   somebody's counterparty book.

The rows are recorded from the live estate, in `eval_specs`, and reused here —
one recording, two readers, so a fixture that drifts from the estate breaks the
unit tests and the graded suite together instead of quietly passing one.
"""

import pytest

from app.eval_specs import (
    _IDENTITY_CRM,
    _IDENTITY_ERP_SALES,
    _IDENTITY_ERP_VENDORS,
    _IDENTITY_PSA,
)
from app.auth import Principal
from app.gateway import Refused
from app.graph import object_id
from app.identity_sweep import (
    IDENTITY_SWEEP_FLOW_ID,
    SCANS,
    _anchor,
    _domain,
    _phone,
)
from app.mappings import steward_queue_for_type
from app.ontology import ONTOLOGY
from app.resolution import parse_sweep_policy, read_sweep_thresholds
from app.connectors.sync import SyncRequest
from tests import world as test_world

NORTHWIND = "Northwind Traders"
STALE_NORTHWIND = "Northwind Trading Co."
TOM = "tom.berg@northwindtraders.example.com"
THOMAS = "thomas.berg@northwindtraders.example.com"
MARIA = "maria.delgado@northwindtraders.example.com"


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


@pytest.fixture()
def owner(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_backend", "firebase")
    monkeypatch.setattr(settings, "admin_api_key", "root-sekret")
    return {"X-API-Key": "root-sekret"}


@pytest.fixture()
def booked(world):
    """The answer key's world, established the way production establishes it."""
    for delivery in (_IDENTITY_CRM, _IDENTITY_ERP_SALES, _IDENTITY_ERP_VENDORS,
                     _IDENTITY_PSA):
        world.sync.deliver(SyncRequest.model_validate(delivery), actor="system:test")
    return world


def _merge_task(world, label: str = STALE_NORTHWIND):
    """The `merge_review` Task for one pair, by a label in its title.

    Named rather than "the first one": every proposal raises a Task now, so a
    test that took whichever came back first would be asserting about the queue
    ordering as much as about the decision.
    """
    return next(
        t for t in world.tasks.list()
        if t.kind == "merge_review" and label in t.title
    )


def _proposals(world) -> dict[tuple[str, str], object]:
    """Every live proposal, keyed by the labels at its two ends."""
    out = {}
    for scan in SCANS:
        for link in world.graph.store.links():
            if link.type != scan.link_type or not link.live:
                continue
            left = world.graph.get(link.from_id)
            right = world.graph.get(link.to_id)
            out[(left.label, right.label)] = link
    return out


# ---------------------------------------------------------------------------
# The evidence readers
# ---------------------------------------------------------------------------


def test_a_domain_is_read_off_an_address_a_url_or_neither():
    assert _domain("billing@cedarlineoffice.example.com") == "cedarlineoffice.example.com"
    assert _domain("https://www.veronafoods.example.com/about") == "veronafoods.example.com"
    assert _domain("") == ""
    # A free mail host places nobody. Two contacts on gmail are not colleagues
    # and two companies reachable there share nothing, so it reads as absent
    # rather than as agreement.
    assert _domain("gschreiber@gmail.com") == ""


def test_a_phone_compares_as_digits_or_not_at_all():
    assert _phone("(608) 555-0117") == _phone("+1 608 555 0117") == "6085550117"
    # Too few digits is not a number to reason about, and a matcher that
    # treated `x4412` as evidence would join every extension in the building.
    assert _phone("x4412") == ""


def test_the_anchor_word_is_the_first_of_a_company_and_the_last_of_a_person():
    assert _anchor("Northwind Trading Co.", "organization") == "northwind"
    assert _anchor("Thomas Berg", "person") == "berg"
    # The signal the whole scorer leans on. English company names put the
    # distinctive word in front and the generic one behind, so two school
    # districts that share `school` and `district` share no anchor.
    assert _anchor("Sauk Prairie School District", "organization") != _anchor(
        "Sugar River School District", "organization"
    )


# ---------------------------------------------------------------------------
# The thresholds are governed knowledge
# ---------------------------------------------------------------------------


def test_the_thresholds_come_off_the_policy_asset_and_not_out_of_the_code(world):
    thresholds = read_sweep_thresholds(world.knowledge)
    assert thresholds.found_asset
    assert thresholds.source_ref.startswith("knowledge:entity-resolution-policy@rev")

    # And editing the document re-tunes the flow, which is the whole claim
    # `match_policy` makes on every Mapping that names this asset.
    tuned = parse_sweep_policy(
        "Propose a merge at or above: 0.42\n"
        "Ratify rather than ask at or above: 0.91\n"
        "Consider a pair on names alone at or above: 0.33\n"
        "Never propose above: 0.99\n"
    )
    assert (tuned.propose_at, tuned.ratify_at) == (0.42, 0.91)
    assert (tuned.name_floor, tuned.ceiling) == (0.33, 0.99)


def test_a_missing_policy_leaves_defaults_rather_than_proposing_everything(world):
    for asset in world.knowledge.repo.all():
        world.knowledge.repo.delete(asset.id)
    thresholds = read_sweep_thresholds(world.knowledge)
    assert not thresholds.found_asset
    # Conservative, not permissive: a lost policy must not turn the floor into
    # zero and fill a steward's queue with the whole cross product.
    assert thresholds.propose_at == 0.55


# ---------------------------------------------------------------------------
# What it finds
# ---------------------------------------------------------------------------


def test_the_resolver_mints_the_stale_northwind_and_the_sweep_catches_it(booked):
    """The division of labour, in one test.

    Per-source resolution is *right* to mint a second Company here: scored
    against the book, `Northwind Trading Co.` sits at 0.64, below the floor
    where a match would be asserted, and inventing a link there would be the
    resolver guessing. The pass over the book is what has the extra evidence —
    an anchor word and an area code — and it still only proposes.
    """
    assert booked.graph.get(object_id("Company", NORTHWIND)) is not None
    assert booked.graph.get(object_id("Company", STALE_NORTHWIND)) is not None

    booked.sync.sweep_identities(actor="system:test")
    link = _proposals(booked)[(NORTHWIND, STALE_NORTHWIND)]
    assert link.type == "company_same_as"
    assert link.props["status"] == "in_review"
    assert 0.70 <= link.confidence < 1.0
    assert link.method == "fuzzy"
    signals = {s["name"] for s in link.props["signals"]}
    assert signals == {"shared-area-code", "shared-anchor", "name-similarity"}
    # Every required property of a typed identity Link is on the record, in
    # the doctrine's terms: confidence, method, who decided, what an un-merge
    # restores, and a status nothing may act on.
    assert link.props["decided_by"] == f"flow:{IDENTITY_SWEEP_FLOW_ID}"
    assert "withdraw this company_same_as link" in link.props["reversible_with"]
    assert link.props["policy_ref"].startswith("knowledge:entity-resolution-policy")


def test_two_spellings_of_one_person_are_proposed_and_two_colleagues_are_not(booked):
    """The CRM feed refuses to merge people on a name, deliberately. This is
    where that refusal gets its second half — and Maria is the control that
    keeps `same employer` from being enough on its own."""
    booked.sync.sweep_identities(actor="system:test")
    found = _proposals(booked)

    link = found[("Thomas Berg", "Tom Berg")]
    assert link.type == "person_same_as"
    assert link.confidence >= 0.85
    assert {s["name"] for s in link.props["signals"]} == {
        "shared-employer", "shared-anchor", "name-similarity",
    }
    assert not [pair for pair in found if "Maria Delgado" in pair]


def test_the_crm_duplicate_account_is_proposed_though_one_row_holds_nothing(booked):
    """A badge-list import with no website and no phone. Everything the scorer
    would normally lean on is missing, so this is the case that proves the
    name signal is load-bearing rather than decoration."""
    booked.sync.sweep_identities(actor="system:test")
    link = _proposals(booked)[("Verona Foods", "Verona Foods Group")]
    assert link.confidence >= 0.80
    assert {s["name"] for s in link.props["signals"]} == {
        "shared-anchor", "name-similarity",
    }


# ---------------------------------------------------------------------------
# What it refuses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "left,right",
    [
        # The roster's two designed bait pairs.
        ("Summit Health Partners", "Summit Healthcare Staffing LLC"),
        ("Apex Manufacturing", "Apex Mfg. Services LLC"),
        # The third floor of the Apex trap: the delivery system's nickname for
        # the customer is the vendor's legal name, and it arrives from a system
        # that keeps no phone numbers — so the area codes that separate the
        # other two cannot be compared at all.
        ("Apex Mfg", "Apex Mfg. Services LLC"),
        # And the confusability the roster documents as *not* designed pairs.
        ("Sauk Prairie School District", "Sugar River School District"),
        ("Kestrel Credit Union", "Harbor District Credit Union"),
        ("Kestrel CU", "Harbor District Credit Union"),
        ("Prairie Records", "Sauk Prairie School District"),
    ],
)
def test_pairs_the_answer_key_says_are_two_entities_are_not_proposed(
    booked, left, right
):
    booked.sync.sweep_identities(actor="system:test")
    found = _proposals(booked)
    assert (left, right) not in found and (right, left) not in found


def test_the_vendor_duplicate_resolution_already_caught_is_not_re_proposed(booked):
    """`Cedarline Office Systems, Inc.` normalizes onto `Cedarline Office
    Systems`, so the resolver landed both ERP rows on one Vendor. The right
    answer for the sweep is silence — a proposal here would be an offer to
    merge something with itself."""
    vendor = booked.graph.get(object_id("Vendor", "Cedarline Office Systems"))
    assert vendor is not None
    assert len(booked.graph.neighbors(vendor.id, "identified_as", "out")) == 2

    booked.sync.sweep_identities(actor="system:test")
    assert not [
        link for link in booked.graph.store.links() if link.type == "vendor_same_as"
    ]


# ---------------------------------------------------------------------------
# Propose, never merge
# ---------------------------------------------------------------------------


def test_the_guarded_write_touches_edges_and_nothing_else(booked):
    """The module's central promise, checked structurally.

    Not "we read the code and it only calls `link`": the Gateway records every
    ref a step wrote, so a change that started upserting an object would show
    up here as a `graph:` ref. That is the difference between a promise and an
    invariant.
    """
    before = {o.id: o.revision for o in booked.graph.store.objects()}
    booked.sync.sweep_identities(actor="system:test")

    entry = next(
        e for e in booked.gateway.ledger.list()
        if e.action == "propose-identity-link"
    )
    assert entry.outcome == "committed"
    assert entry.writes
    assert all(ref.startswith("link:") for ref in entry.writes)
    # And nothing anywhere gained a claim.
    assert {o.id: o.revision for o in booked.graph.store.objects()} == before


def test_a_second_sweep_changes_nothing(booked):
    """Link ids derive from the endpoints, so a re-run addresses the same
    edges. It leaves them exactly as they are rather than restamping them: a
    proposal a steward has already ruled on must not be re-opened by a pass
    that happened to run again."""
    first = booked.sync.sweep_identities(actor="system:test")
    assert first["proposed"] == 5 and first["standing"] == 0
    assert first["steward_tasks"] == 5

    second = booked.sync.sweep_identities(actor="system:test")
    assert second["proposed"] == 0
    assert second["standing"] == 5
    assert len(_proposals(booked)) == 5
    # And no second Task for any of the five pairs.
    assert second["steward_tasks"] == 0
    assert second["steward_tasks_standing"] == 5
    assert len([t for t in booked.tasks.list() if t.kind == "merge_review"]) == 5


def test_a_later_sweep_does_not_re_open_a_decided_task(booked):
    """The quietest way a scheduled pass could undo a human.

    Task ids derive from the edge and `TaskService.create` is a put, so a
    sweep that raised its Tasks unconditionally would walk over a steward's
    verdict every time it ran — the Task back to `open`, the decision gone,
    and nothing anywhere saying it had happened.
    """
    booked.sync.sweep_identities(actor="system:test")
    task = _merge_task(booked)
    decided = booked.tasks.decide(
        task.id, verdict="rejected", decided_by="dana@naviant.com",
        note="Different customers.",
    )
    booked.sync.apply_merge_decision(decided)

    booked.sync.sweep_identities(actor="system:test")
    after = booked.tasks.get(task.id)
    assert after.status == "done"
    assert after.decision.verdict == "rejected"
    assert after.decision.decided_by == "dana@naviant.com"


def test_the_run_is_started_by_a_dispatched_event_like_every_other_flow(booked):
    booked.sync.sweep_identities(actor="system:test")
    event = next(
        e for e in booked.dispatcher.events.list()
        if e.name == "IdentityWindowClosed"
    )
    assert event.triggers == ["identity-window-closed"]
    run = booked.engine.get(event.starts[0])
    assert run.flow_id == IDENTITY_SWEEP_FLOW_ID
    assert run.caused_by == event.id
    # Windowed, read off the Trigger rather than declared on the flow.
    from app.dispatch import is_windowed_flow

    assert is_windowed_flow(IDENTITY_SWEEP_FLOW_ID)
    # And no ModelCall on any path: this is rules and evidence.
    assert not [s for s in run.steps if s.kind == "modelcall"]


# ---------------------------------------------------------------------------
# The steward's answer
# ---------------------------------------------------------------------------


def test_every_proposal_gets_a_decision_path_whatever_it_scored(booked):
    """The hole this closes, stated as the property that closes it.

    The sweep used to raise a Task only *under* the governed bar, which left
    its most confident proposals — the ones most likely to be right — standing
    at `in_review` with nobody paged and nowhere to decide them. Confidence is
    not authority: a merge is the doctrine's named reversible-with-provenance
    case, so nothing is auto-confirmed and everything is answerable.
    """
    out = booked.sync.sweep_identities(actor="system:test")
    tasks = [t for t in booked.tasks.list() if t.kind == "merge_review"]
    assert out["steward_tasks"] == len(tasks) == out["proposed"] == 5

    # Every standing proposal has a Task pointing at its edge, and every Task
    # is open. No proposal in the book is undecidable.
    standing = {
        f"link:{link.type}/{link.id}"
        for link in booked.graph.store.links()
        if link.live and str(link.props.get("status")) == "in_review"
    }
    assert {t.case_ref for t in tasks} == standing
    assert all(t.status == "open" for t in tasks)

    thresholds = read_sweep_thresholds(booked.knowledge)
    by_ref = {t.case_ref: t for t in tasks}
    for link_ref in standing:
        link = booked.graph.store.get_link(link_ref.split("/", 1)[1])
        task = by_ref[link_ref]
        ratifying = link.confidence >= thresholds.ratify_at
        # The bar governs the character of the ask and nothing else: urgency
        # and words, never whether anybody was asked.
        assert task.priority == ("low" if ratifying else "normal")
        assert ("ratification" in task.summary) is ratifying
        assert (task.title.startswith("Same party?")) is not ratifying

    # Northwind at 0.79 is the one genuine question in this book; Verona, the
    # Bergs and the two PSA nicknames clear the bar and are raised as
    # ratifications.
    ask = next(t for t in tasks if t.priority == "normal")
    assert STALE_NORTHWIND in ask.title
    assert out["steward_ratifications"] == 4
    assert ask.queue == steward_queue_for_type("Company") == "data-stewardship"
    assert ask.case_ref.startswith("link:company_same_as/")
    assert any("shared-anchor" == e.label for e in ask.evidence)


def test_confirming_flips_the_status_and_merges_nothing(booked):
    booked.sync.sweep_identities(actor="system:test")
    task = _merge_task(booked)
    before = {o.id: o.status for o in booked.graph.store.objects()}

    decided = booked.tasks.decide(
        task.id, verdict="approved", decided_by="dana@naviant.com",
        note="Same company; the 2019 row is the old trading name.",
    )
    outcome = booked.sync.apply_merge_decision(decided)
    assert outcome["action"] == "confirmed"

    link = _proposals(booked)[(NORTHWIND, STALE_NORTHWIND)]
    assert link.props["status"] == "confirmed"
    assert link.props["decided_by"] == "dana@naviant.com"
    # The machine's own score survives the human's answer. Overwriting it with
    # 1.0 would erase the only record of how good the proposal was before
    # anybody looked at it — which is the number a later tuning pass needs.
    assert link.props["machine_confidence"] == pytest.approx(link.confidence)
    # Confirmed is an assertion, not a collapse. Both rows stand.
    assert {o.id: o.status for o in booked.graph.store.objects()} == before
    assert booked.graph.get(object_id("Company", STALE_NORTHWIND)) is not None


def test_rejecting_withdraws_the_proposal_without_erasing_that_it_was_made(booked):
    booked.sync.sweep_identities(actor="system:test")
    task = _merge_task(booked)
    decided = booked.tasks.decide(
        task.id, verdict="rejected", decided_by="dana@naviant.com",
        note="Different customers.",
    )
    outcome = booked.sync.apply_merge_decision(decided)
    assert outcome["action"] == "withdrawn"

    assert not _proposals(booked).get((NORTHWIND, STALE_NORTHWIND))
    withdrawn = booked.graph.store.get_link(outcome["link_id"])
    assert not withdrawn.live
    assert withdrawn.reversed_by == "dana@naviant.com"
    assert "Different customers" in withdrawn.reversal_reason
    # Withdrawn, not deleted, and the other proposals are untouched — a
    # rejection is a judgment about one pair.
    assert withdrawn.props["status"] == "in_review"
    assert len(_proposals(booked)) == 4


def test_a_decision_on_a_ruled_proposal_is_refused_not_replayed(booked):
    booked.sync.sweep_identities(actor="system:test")
    task = _merge_task(booked)
    decided = booked.tasks.decide(
        task.id, verdict="approved", decided_by="dana@naviant.com",
    )
    assert booked.sync.apply_merge_decision(decided)["action"] == "confirmed"
    # The hook is wired into every task decision, so it sees other flows'
    # tasks too and must no-op on them — and on its own, once ruled.
    assert booked.sync.apply_merge_decision(decided) is None
    assert booked.sync.apply_match_decision(decided) is None


# ---------------------------------------------------------------------------
# Two desks, one Action — and what happens when the commit is refused
# ---------------------------------------------------------------------------
#
# The sweep proposes across both counterparty books, so a `vendor_same_as` pair
# is `vendor-stewardship`'s work where a `company_same_as` pair is
# `data-stewardship`'s. `confirm-identity-link` used to pin the second, which
# meant a vendor steward's own Task was refused at the write — and the decide
# route swallowed the refusal, so the Task read `decided` while the link never
# flipped. Two failures that hid each other: the one that made a legitimate
# decision impossible, and the one that made it look like it had happened.

#: A steward bound to the vendor book and nothing else. Not a persona — the
#: roster's only identity curators own *both* desks, which is exactly the shape
#: that hid this: the Controller could decide either kind, so nobody hit it.
VENDOR_STEWARD = Principal(
    uid="persona_vendor_steward", email="ingrid.voss@naviant.com",
    role="user", scope="own", queues=("vendor-stewardship",),
)

#: Two spellings of one carrier, delivered as the ERP has them. Unlike the
#: Cedarline pair they do not normalize together, so both mint and the sweep
#: has a genuine vendor-book proposal to raise — the pair kind the estate's
#: answer key has no example of, and the one the pinned desk broke.
_ERP_FREIGHT = {
    "source": "erp-vendors",
    "watermark": "2026-08-31T00:00:00Z",
    "vendors": [
        {"external_id": "2101", "name": "Granite State Freight",
         "email": "ap@gsfreight.example.com", "phone": "(603) 555-0180"},
        {"external_id": "2102", "name": "GSF Logistics",
         "email": "billing@gsfreight.example.com"},
    ],
}


@pytest.fixture()
def freight(booked):
    """The answer key's world plus a vendor-book duplicate, swept."""
    booked.sync.deliver(
        SyncRequest.model_validate(_ERP_FREIGHT), actor="system:test"
    )
    booked.sync.sweep_identities(actor="system:test")
    return booked


@pytest.fixture()
def as_vendor_steward(_client):
    """Decide as a persona bound only to the vendor desk."""
    from app.auth import get_principal
    from app.main import app

    app.dependency_overrides[get_principal] = lambda: VENDOR_STEWARD
    try:
        yield _client
    finally:
        app.dependency_overrides.pop(get_principal, None)


def test_a_vendor_pair_is_decided_by_the_steward_whose_queue_holds_it(
    freight, as_vendor_steward
):
    """The commit runs under the desk the Task sat in, not one pinned Action.

    Through the route, because both halves of the fix are on that path: the
    queue binding lets this persona answer the Task at all, and the Gateway
    check has to reach the same answer at the write — a decision authorized at
    the door and refused at the commit is the state that produced a Task
    reading `decided` over a link that never moved.
    """
    task = _merge_task(freight, "GSF Logistics")
    assert task.queue == steward_queue_for_type("Vendor") == "vendor-stewardship"
    assert task.case_ref.startswith("link:vendor_same_as/")

    resp = as_vendor_steward.post(
        f"/api/v1/tasks/{task.id}/decide",
        json={"verdict": "approved", "note": "One carrier, two ERP spellings."},
    )
    assert resp.status_code == 200, resp.text

    link = freight.graph.store.get_link(task.case_ref.split("/", 1)[1])
    assert link.props["status"] == "confirmed"
    assert link.props["decided_by"] == VENDOR_STEWARD.uid
    assert freight.tasks.get(task.id).status == "done"

    # And the ledger records it as this steward's commit, under their desk.
    entry = next(
        e for e in freight.gateway.ledger.list()
        if e.action == "confirm-identity-link"
    )
    assert entry.outcome == "committed" and entry.refusal == ""
    assert entry.principal_uid == VENDOR_STEWARD.uid
    assert entry.task_id == task.id


def test_the_pinned_desk_would_have_refused_this_steward(freight):
    """The mutation proof, on the check itself.

    The old declaration is one line, so the failure is asserted rather than
    described: with `confirm-identity-link` pinned to `data-stewardship`, the
    vendor steward holding this Task cannot commit it, and with the desk taken
    off the Task they can. Nothing about the principal changes between the two.
    """
    from app.gateway import ACTIONS, Action, _for_case, _refusal

    task = _merge_task(freight, "GSF Logistics")
    pinned = Action(id="confirm-identity-link", target="entity-layer",
                    queue="data-stewardship")
    assert "data-stewardship" in _refusal(pinned, VENDOR_STEWARD)

    derived = _for_case(ACTIONS["confirm-identity-link"], task.queue)
    assert derived.queue == "vendor-stewardship"
    assert _refusal(derived, VENDOR_STEWARD) == ""

    # The derivation is a declared property of the Action and not a parameter
    # any caller may set: an Action that names its own desk and derives no
    # other refuses one.
    with pytest.raises(Refused):
        _for_case(ACTIONS["link-source-record"], "vendor-stewardship")
    # And one that derives its desk refuses to commit with none derived, rather
    # than falling through to an empty queue that passes every check.
    with pytest.raises(Refused):
        _for_case(ACTIONS["confirm-identity-link"], "")


def test_a_refused_commit_leaves_the_task_open_and_tells_the_caller(
    freight, as_vendor_steward, monkeypatch
):
    """The second half: a resume hook that fails must not leave a decided Task.

    The refusal injected here is the one the pinned desk produced, verbatim.
    Before this fix the route caught it, logged it, and returned 200 with the
    Task marked `done` — the queue saying a steward had answered, the graph
    saying nobody had, and a log line as the only record of the disagreement.
    """
    task = _merge_task(freight, "GSF Logistics")
    link_id_ = task.case_ref.split("/", 1)[1]

    def refuse(*_args, **_kwargs):
        raise Refused(
            "confirm-identity-link",
            "is the 'data-stewardship' queue's work, which this principal "
            "does not own",
        )

    monkeypatch.setattr(freight.sync, "apply_merge_decision", refuse)
    resp = as_vendor_steward.post(
        f"/api/v1/tasks/{task.id}/decide",
        json={"verdict": "approved", "note": "One carrier."},
    )
    # 403 and not 500: nothing broke, this principal was not allowed to commit.
    assert resp.status_code == 403, resp.text
    assert "data-stewardship" in resp.json()["detail"]
    assert "open again" in resp.json()["detail"]

    after = freight.tasks.get(task.id)
    assert after.status == "open" and after.decision is None
    assert freight.graph.store.get_link(link_id_).props["status"] == "in_review"

    # The old behaviour, run rather than described: decide, swallow, carry on.
    # This is the state the route must never leave behind — and the assertion
    # that fails if the `except` clause ever goes back to passing.
    stranded = freight.tasks.decide(
        task.id, verdict="approved", decided_by=VENDOR_STEWARD.uid,
    )
    try:
        refuse(stranded)
    except Refused:
        pass
    assert stranded.status == "done"
    assert freight.graph.store.get_link(link_id_).props["status"] == "in_review"


def test_a_hook_that_breaks_is_a_500_and_the_task_survives_it(
    freight, as_vendor_steward, monkeypatch
):
    """Not every failure is an authorization one. A hook that raises anything
    else is a fault, says so, and still leaves the judgment un-recorded rather
    than recorded over a world that did not change."""
    task = _merge_task(freight, "GSF Logistics")

    def boom(*_args, **_kwargs):
        raise RuntimeError("the entity store is unreachable")

    monkeypatch.setattr(freight.sync, "apply_merge_decision", boom)
    resp = as_vendor_steward.post(
        f"/api/v1/tasks/{task.id}/decide",
        json={"verdict": "approved", "note": "One carrier."},
    )
    assert resp.status_code == 500, resp.text
    assert "entity store is unreachable" in resp.json()["detail"]

    after = freight.tasks.get(task.id)
    assert after.status == "open" and after.decision is None
    # Still decidable once whatever broke is fixed — the row is back exactly
    # as the sweep raised it.
    assert after.priority == "normal" and after.queue == "vendor-stewardship"


# ---------------------------------------------------------------------------
# The seams the rest of the platform reads
# ---------------------------------------------------------------------------


def test_the_identity_link_a_resolver_walks_is_read_off_the_ontology():
    """The generalization the source-link signal was waiting for.

    `score_candidates` hardcoded `identified_as`, so the strongest of its four
    signals — a source that has already told us its id — was dead for every
    book but the vendor master. The name is per type now, and derived from the
    declarations rather than kept in a table beside them.
    """
    assert ONTOLOGY.source_identity_link("Company").name == "company_identified_as"
    assert ONTOLOGY.source_identity_link("Person").name == "person_identified_as"
    assert ONTOLOGY.source_identity_link("Vendor").name == "identified_as"
    # A proposal is an identity link too, and must never be mistaken for the
    # edge that binds an entity to a landed row.
    assert ONTOLOGY.source_identity_link("Invoice") is None
    for scan in SCANS:
        declared = ONTOLOGY.link_type(scan.link_type)
        assert declared.identity and declared.to_type != "SourceRecord"


def test_the_source_link_signal_now_fires_for_a_company(booked):
    """What the hardcoded name cost, made concrete: a redelivery of the same
    CRM page re-resolves every account by the id it already carries rather
    than by re-scoring its spelling."""
    from app.resolution import read_resolution_thresholds, resolve

    outcome = resolve(
        booked.graph, "Company", ["something else entirely"],
        read_resolution_thresholds(booked.knowledge),
        source="crm-book", source_external_id="001e7ed1a4e7135",
    )
    assert outcome.disposition == "linked"
    assert outcome.method == "source-link"
    assert booked.graph.get(outcome.object_id).label == NORTHWIND


def test_the_desk_comes_from_the_mapping_for_a_book_as_well_as_a_feed():
    """`steward_queue_for` answers per feed; the sweep has no feed to ask
    with, and used to be answered by a constant. One rebind moves both now."""
    assert steward_queue_for_type("Company") == "data-stewardship"
    assert steward_queue_for_type("Person") == "data-stewardship"
    assert steward_queue_for_type("Vendor") == "vendor-stewardship"
    # A type no Mapping resolves into falls back to where such rows have
    # always gone rather than to nowhere.
    assert steward_queue_for_type("Invoice") == "vendor-stewardship"


def test_one_book_one_desk_is_enforced_at_declaration_time():
    """The check that makes the lookup above honest: it returns the first
    match, which is only defensible while every feed writing a book agrees."""
    import dataclasses

    from app.mappings import MAPPINGS, validate_mappings

    assert validate_mappings() == []
    split = tuple(
        dataclasses.replace(m, steward_queue="ap-clerk")
        if m.id == "erp-sales" else m
        for m in MAPPINGS
    )
    problems = validate_mappings(split)
    assert any("One book, one desk" in p for p in problems)


# ---------------------------------------------------------------------------
# The graded suite
# ---------------------------------------------------------------------------


def test_the_answer_key_runs_green_through_the_eval_route(client, owner, world):
    """The gate: the roster's ground truth as a promise something checks."""
    body = client.post("/api/v1/evals/identity-sweep/run", headers=owner).json()
    cases = body["result"]["cases"]
    assert body["failed"] == 0, [
        (c["case_id"], [a for a in c["assertions"] if not a["passed"]], c["error"])
        for c in cases if not c["passed"]
    ]
    assert body["passed"] == 5
    assert {c["case_id"] for c in cases} == {
        "identity/northwind-stale-duplicate",
        "identity/berg-duplicate-contact",
        "identity/cedarline-one-vendor",
        "identity/bait-pairs-are-not-proposed",
        "identity/psa-nickname-joins-the-book",
    }
    # Every case ran in a world the fixture's own deliveries built, and none
    # of them touched the live graph the rest of this module is using.
    assert all(c["run_status"] == "succeeded" for c in cases)
    assert body["invariant_violations"] == 0
    assert body["failed_assertions"] == 0


def test_loosening_the_governed_thresholds_fails_the_bait_case(client, owner, world):
    """The mutation proof, run rather than described.

    A suite that only ever passes is a suite nobody can trust, so this moves
    the two values that gate a bait pair — the floor a proposal must clear and
    the name similarity below which a pair is not considered without a hard
    identifier — and asserts the bait case notices. It edits the *policy*, not
    the code, which is the sharper demonstration: the thresholds really are
    governed knowledge, and a steward can loosen them into a false positive.
    """
    from app.models import AssetUpdate

    asset = world.knowledge.repo.get("entity-resolution-policy")
    world.knowledge.update(asset.id, AssetUpdate(
        content=asset.content
        .replace("Propose a merge at or above: 0.55",
                 "Propose a merge at or above: 0.30")
        .replace("Consider a pair on names alone at or above: 0.55",
                 "Consider a pair on names alone at or above: 0.40"),
    ))
    body = client.post("/api/v1/evals/identity-sweep/run", headers=owner).json()
    cases = body["result"]["cases"]

    failed = {c["case_id"] for c in cases if not c["passed"]}
    assert failed == {"identity/bait-pairs-are-not-proposed"}
    detail = [
        a for c in cases if not c["passed"]
        for a in c["assertions"] if not a["passed"]
    ]
    assert any(
        "company_same_as_vendor" in a["target"] and a["expected"] == "0"
        for a in detail
    ), detail
    # The must-link cases still pass, which is the half that makes this a
    # proof rather than a coincidence: loosening did not break the finder, it
    # broke the refusing — including the PSA case, whose two joins rest on a
    # shared domain and are untouched by a floor that moved.
    assert body["passed"] == 4
