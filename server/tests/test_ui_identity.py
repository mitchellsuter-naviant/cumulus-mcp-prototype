"""The console's identity workbench: `/api/ui/v1/identity`.

Four properties, each one the kind that fails plausibly rather than loudly.

- **The gate is a queue binding, not read reach.** Every other console
  governance route refuses an own-scoped persona, and the one person who owns
  this work — the Controller — is own-scoped. A screen gated the usual way
  would refuse its only user and admit two personas who own neither desk.
- **A withdrawn proposal still says `in_review` in its props.** Rejecting
  reverses the edge; reversing does not rewrite what the assertion said. A
  route that read the prop alone would draw every rejected proposal as still
  standing, which is the worst possible way to be wrong here.
- **A standing proposal has no decider.** The sweep stamps itself as
  `decided_by` so the edge always names who stands behind it. Rendering that
  as a decision would put a machine's name where a person's belongs — history
  invented out of a field that means something else.
- **The counts are counted from the rows.** Each book's `standing` is derived
  from the same list the screen draws, so a badge cannot disagree with what is
  under it.

The world is the answer key's — the ERP's stale Northwind, the CRM's duplicate
Verona, the re-typed Thomas Berg — established the way production establishes
it, so what the screen composes is what the sweep actually found.
"""

from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

from app import personas, ui_api
from app.auth import Principal
from app.eval_specs import (
    _IDENTITY_CRM,
    _IDENTITY_ERP_SALES,
    _IDENTITY_ERP_VENDORS,
)
from app.sync import SyncRequest
from app.ui_api import get_identity_principal, get_ui_principal
from tests import world as test_world

ROUTE = "/api/ui/v1/identity"

STALE_NORTHWIND = "Northwind Trading Co."

#: Who gets in, and as what. The two axes are independent on purpose: a desk
#: makes you a curator, read reach makes you an observer, and neither makes
#: you the other.
CURATORS = {"naviant-engineer", "customer-user:controller"}
OBSERVERS = {"naviant-support", "customer-admin"}


def _as(persona_id: str) -> dict[str, str]:
    return {"X-Cumulus-Persona": persona_id}


@pytest.fixture()
def booked(_client):
    """The answer key's counterparty book, swept. Nothing decided yet."""
    world = test_world.fresh()
    for delivery in (_IDENTITY_CRM, _IDENTITY_ERP_SALES, _IDENTITY_ERP_VENDORS):
        world.sync.deliver(
            SyncRequest.model_validate(delivery), actor="system:test"
        )
    world.sync.sweep_identities(actor="system:test")
    _client.world = world  # type: ignore[attr-defined] — test handle
    return _client


@pytest.fixture()
def empty(_client):
    """A world where nothing has ever been delivered or swept."""
    test_world.fresh()
    return _client


def _screen(client, persona: str = "customer-user:controller") -> dict:
    resp = client.get(ROUTE, headers=_as(persona))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _northwind(body: dict) -> dict:
    return next(
        p for p in body["standing"] if STALE_NORTHWIND in
        (p["left"]["label"], p["right"]["label"])
    )


# ---------------------------------------------------------------------------
# What the screen composes
# ---------------------------------------------------------------------------


def test_the_route_puts_the_pair_the_evidence_and_the_records_in_one_payload(
    booked,
):
    """The three halves of the question, joined here rather than in the client.

    The proposal is an edge, the evidence is on the edge, and the records are
    the two objects it joins. A screen that fetched those separately would be
    assembling the argument where it could drift.
    """
    body = _screen(booked)
    assert len(body["standing"]) == 3
    proposal = _northwind(body)

    assert proposal["link_type"] == "company_same_as"
    assert proposal["case_ref"] == f"link:company_same_as/{proposal['link_id']}"
    assert proposal["pair_kind"] == "organization"
    assert proposal["status"] == "in_review"
    assert 0 < proposal["confidence"] <= body["policy"]["ceiling"]
    assert proposal["method"] in ("exact", "fuzzy")

    # Every signal carries what it was worth and why — the number is only
    # useful beside the sentence that produced it.
    names = {s["name"] for s in proposal["signals"]}
    assert "shared-anchor" in names and "name-similarity" in names
    assert all(s["detail"] for s in proposal["signals"])

    # The governed policy travels as a resolvable ref, so the console can open
    # the document a steward would have to edit to change the answer.
    assert proposal["policy_ref"].startswith(
        "knowledge:entity-resolution-policy@rev"
    )
    assert body["policy"]["found_asset"] is True
    assert proposal["reversible_with"]

    # And both records, as a person compares them.
    for side in (proposal["left"], proposal["right"]):
        assert side["type"] == "Company"
        assert side["label"] and side["natural_key"]
        assert side["ref"].startswith("graph:Company/")
        assert side["fields"]
        assert side["sources"], "a landed company knows which feed asserted it"


def test_agreement_is_computed_only_over_fields_both_records_hold(booked):
    """One side holding a phone the other has never had is not a disagreement.

    Counting it as one would make every pair read as conflicted, which is the
    same as marking none of them.
    """
    proposal = _northwind(_screen(booked))
    shared = set(proposal["left"]["fields"]) & set(proposal["right"]["fields"])

    assert set(proposal["agree"]) | set(proposal["conflict"]) == shared
    assert not set(proposal["agree"]) & set(proposal["conflict"])
    for field in proposal["agree"]:
        assert (
            proposal["left"]["fields"][field].lower()
            == proposal["right"]["fields"][field].lower()
        )
    for field in proposal["conflict"]:
        assert (
            proposal["left"]["fields"][field].lower()
            != proposal["right"]["fields"][field].lower()
        )


def test_a_standing_proposal_names_no_decider(booked):
    """The sweep records itself as `decided_by` so the edge always names who
    stands behind it. Reporting that as a decision would be history invented
    out of a field that means something else."""
    body = _screen(booked)
    assert body["standing"]
    for proposal in body["standing"]:
        assert proposal["decided_by"] == ""
        assert proposal["decided_at"] == ""
        assert proposal["withdrawn_because"] == ""
        assert proposal["proposed_at"], "when it was proposed is known"


def test_the_last_sweep_is_the_run_that_happened_and_never_a_zeroed_row(booked):
    body = _screen(booked)
    sweep = body["last_sweep"]
    assert sweep["status"] == "succeeded"
    assert sweep["run_id"].startswith("run_")
    assert sweep["compared"] > sweep["considered"] >= sweep["proposed"] == 3
    assert sweep["steward_tasks"] == 1


# ---------------------------------------------------------------------------
# The gate: where a decision lives, and whose it is
# ---------------------------------------------------------------------------


def test_a_proposal_under_the_bar_carries_its_task_and_one_above_it_does_not(
    booked,
):
    """The state a workbench normally gets wrong.

    The sweep raises a Task only *below* the governed confidence bar, so a
    high-scoring proposal stands at `in_review` with nobody paged and no
    decision path at all. That is the policy working as declared, and the row
    says it out loud rather than drawing a control with nothing to commit
    through.
    """
    body = _screen(booked)
    bar = body["policy"]["steward_task_below"]

    asked = [p for p in body["standing"] if p["gate"]["task_id"]]
    quiet = [p for p in body["standing"] if not p["gate"]["task_id"]]
    assert len(asked) == 1 and len(quiet) == 2

    gate = asked[0]["gate"]
    assert asked[0]["confidence"] < bar
    assert gate["queue"] == "data-stewardship"
    assert gate["task_status"] == "open"
    assert gate["may_decide"] is True and gate["why_not"] == ""

    for proposal in quiet:
        assert proposal["confidence"] >= bar
        assert proposal["gate"]["may_decide"] is False
        assert "Nobody was paged" in proposal["gate"]["why_not"]
        assert f"{bar:.2f}" in proposal["gate"]["why_not"]


def test_a_persona_that_does_not_own_the_desk_reads_a_sentence(booked):
    """An access boundary is a statement, not a disabled control with no
    reason attached. Support reads every plane and decides nothing — the case
    a screen drawing controls from read reach alone gets wrong."""
    body = _screen(booked, "naviant-support")

    assert body["curates"] is False
    assert body["desks"] == []
    gate = next(p["gate"] for p in body["standing"] if p["gate"]["task_id"])
    assert gate["may_decide"] is False
    assert "data-stewardship" in gate["why_not"]
    assert "no queue" in gate["why_not"]

    # And the observer sees exactly what the curator sees, minus the controls.
    assert len(body["standing"]) == len(_screen(booked)["standing"])


# ---------------------------------------------------------------------------
# What a decision leaves behind
# ---------------------------------------------------------------------------


def _decide(client, verdict: str, note: str) -> dict:
    task = next(
        t for t in client.world.tasks.list() if t.kind == "merge_review"
    )
    resp = client.post(
        f"/api/v1/tasks/{task.id}/decide",
        json={"verdict": verdict, "note": note},
        headers=_as("customer-user:controller"),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_confirming_moves_the_proposal_into_the_decided_record(booked):
    """Through the Task, which is the only path a merge decision has. The
    workbench composes; it does not commit."""
    _decide(booked, "approved", "Same company; the 2019 row is the old name.")
    body = _screen(booked)

    assert len(body["standing"]) == 2
    assert body["decided_total"] == len(body["decided"]) == 1
    ruled = body["decided"][0]
    assert ruled["status"] == "confirmed"
    assert ruled["decided_by"] == "persona_controller"
    assert ruled["decided_at"]
    assert ruled["withdrawn_because"] == ""
    # The machine's own score survives the human's answer: it is the number a
    # later tuning pass needs, and confirming is not a claim of certainty.
    assert ruled["confidence"] < body["policy"]["steward_task_below"]
    assert ruled["gate"]["task_status"] == "approved"
    assert ruled["gate"]["may_decide"] is False
    assert "Already decided" in ruled["gate"]["why_not"]

    # Nothing collapsed. Both records still stand, with their own keys.
    assert ruled["left"]["status"] != "superseded"
    assert ruled["right"]["status"] != "superseded"
    assert ruled["left"]["natural_key"] != ruled["right"]["natural_key"]


def test_rejecting_reads_as_withdrawn_even_though_the_prop_still_says_in_review(
    booked,
):
    """The failure this route's status derivation exists to prevent.

    `reverse_link` withdraws the assertion without rewriting it, so a rejected
    proposal's `status` prop still reads `in_review`. A screen trusting the
    prop would list every rejection as outstanding work.
    """
    _decide(booked, "rejected", "Different customers.")
    body = _screen(booked)

    ruled = body["decided"][0]
    assert ruled["status"] == "withdrawn"
    assert ruled["decided_by"] == "persona_controller"
    assert ruled["decided_at"]
    assert "Different customers" in ruled["withdrawn_because"]

    # The graph still says `in_review`; the screen does not.
    link = booked.world.graph.store.get_link(ruled["link_id"])
    assert link.props["status"] == "in_review" and not link.live
    assert ruled["link_id"] not in {p["link_id"] for p in body["standing"]}


# ---------------------------------------------------------------------------
# The counts
# ---------------------------------------------------------------------------


def test_every_book_count_is_answered_by_rows_in_the_lists(booked):
    _decide(booked, "approved", "Same company.")
    body = _screen(booked)

    books = {b["type"]: b for b in body["books"]}
    assert set(books) == {"Company", "Vendor", "Person"}
    assert sum(b["standing"] for b in books.values()) == len(body["standing"])
    assert sum(b["confirmed"] + b["withdrawn"] for b in books.values()) == (
        body["decided_total"]
    )
    assert books["Company"]["confirmed"] == 1
    # A proposal is booked to the type it starts from — the same rule the sweep
    # uses to pick a queue, so the count and the desk cannot disagree.
    assert books["Company"]["steward_queue"] == "data-stewardship"
    assert books["Vendor"]["steward_queue"] == "vendor-stewardship"
    assert all(b["objects"] > 0 for b in books.values())

    # Whose desk each book is, answered for this persona rather than in general.
    assert books["Company"]["yours"] is True
    assert {
        b["type"] for b in _screen(booked, "naviant-support")["books"]
        if b["yours"]
    } == set()


# ---------------------------------------------------------------------------
# The empty world
# ---------------------------------------------------------------------------


def test_an_empty_world_states_that_nothing_has_been_swept(empty):
    """`last_sweep: null` is the finding. A zeroed row would say a pass ran and
    found nothing, which is the opposite fact."""
    body = _screen(empty)

    assert body["standing"] == [] and body["decided"] == []
    assert body["decided_total"] == 0
    assert body["last_sweep"] is None
    # The books are still declared — the desks exist whether or not anything
    # has landed in them, and a screen with no books would read as broken.
    assert {b["type"] for b in body["books"]} == {"Company", "Vendor", "Person"}
    assert all(b["objects"] == b["standing"] == 0 for b in body["books"])
    assert body["policy"]["found_asset"] is True


# ---------------------------------------------------------------------------
# The doors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "persona", personas.ALL_PERSONAS, ids=[p.id for p in personas.ALL_PERSONAS]
)
def test_a_desk_gets_you_in_as_a_curator_and_read_reach_as_an_observer(
    booked, persona
):
    """The gate that is deliberately not the Knowledge layer's.

    Raymond owns both master-data desks and reads only his own work; Connie
    reads only her own and owns AP. The write ladder cannot tell them apart and
    read reach puts them on the same side of a line they belong on opposite
    sides of.
    """
    resp = booked.get(ROUTE, headers=_as(persona.id))
    if persona.id in CURATORS:
        assert resp.status_code == 200, persona.id
        assert resp.json()["curates"] is True
        assert resp.json()["desks"]
    elif persona.id in OBSERVERS:
        assert resp.status_code == 200, persona.id
        assert resp.json()["curates"] is False
        assert resp.json()["desks"] == []
    else:
        assert resp.status_code == 403, persona.id
        assert "neither stewardship queue" in resp.json()["detail"]


def test_the_roster_is_split_by_what_the_gate_actually_asks():
    """The parametrized expectation above, restated from the roster so a new
    persona cannot be added on one side of the line and tested on neither."""
    expected_curators = {
        p.id for p in personas.ALL_PERSONAS
        if any(q in p.queues for q in ui_api.identity_desks())
    }
    expected_observers = {
        p.id for p in personas.ALL_PERSONAS
        if p.scope != "own" and p.id not in expected_curators
    }
    assert expected_curators == CURATORS
    assert expected_observers == OBSERVERS
    assert ui_api.identity_desks() == {"data-stewardship", "vendor-stewardship"}


@pytest.mark.parametrize(
    "persona", personas.ALL_PERSONAS, ids=[p.id for p in personas.ALL_PERSONAS]
)
def test_the_session_tells_the_rail_exactly_what_the_door_will_answer(
    booked, persona
):
    """The other half of the access boundary.

    The console drops the rail stop for a persona the route would refuse, and
    the two answers have to be the same answer. Deriving it in Dart would mean
    a copy of the queue names in the client, drifting the moment a Mapping
    changed hands — so the session carries the door's own verdict.
    """
    session = booked.get(
        "/api/ui/v1/session", headers=_as(persona.id)
    ).json()
    opened = booked.get(ROUTE, headers=_as(persona.id)).status_code == 200
    assert session["may_open_identity"] is opened, persona.id


def test_the_workbench_inherits_the_console_door():
    """The no-keys rule is inherited, never restated: a machine credential is
    refused at the same door as every other console route."""
    gate = inspect.signature(get_identity_principal)
    assert gate.parameters["principal"].default.dependency is get_ui_principal

    depends = inspect.signature(ui_api.identity).parameters["principal"].default
    assert depends.dependency is get_identity_principal

    machine = Principal(
        uid="svc-1", email="svc@machine", role="owner", key_id="k_1"
    )
    with pytest.raises(HTTPException) as refused:
        get_ui_principal(machine)
    assert refused.value.status_code == 403


def test_the_workbench_has_no_write_route_of_its_own():
    """Confirming goes through the Task the sweep raised. A second write path
    here would be a quieter copy of `apply_merge_decision`'s authorization."""
    identity_routes = [
        route for route in ui_api.router.routes
        if getattr(route, "path", "").endswith("/identity")
        or "/identity/" in getattr(route, "path", "")
    ]
    assert identity_routes
    assert all(set(route.methods) == {"GET"} for route in identity_routes)
