"""Desks: who holds them, and the lease that keeps two people off one case.

Two failures drive this file, both seen in the console rather than deduced.

1. A signed-in owner was shown 24 tasks under a heading reading "My Tasks ·
   YOURS" and refused on every one of them, because `reach_for` grants no
   queues to any role and nothing mapped a real account to the roster entry
   naming that exact person.
2. Doctrine broadcasts a Task to every Principal bound to its queue — which is
   the point — and said nothing about what happens when two of them start
   working it. The only protection was a 409 on the second *decision*, by
   which time both had done the work.
"""

import pytest

import app.deps as deps
from app import auth, personas
from app.auth import Principal
from app.config import settings
from app.queues import (
    InMemoryQueueDirectory,
    QueueService,
    bootstrap_from_roster,
    personal_slug,
)
from app.task_queue import (
    InMemoryTaskDirectory,
    Task,
    TaskAlreadyDecidedError,
    TaskClaimConflict,
    TaskService,
)

OPERATOR = "masuter@naviant.com"
CONTROLLER = "raymond.fitzgerald@naviant.com"


@pytest.fixture()
def queues():
    service = QueueService(InMemoryQueueDirectory())
    bootstrap_from_roster(service)
    deps._queue_service = service
    return service


@pytest.fixture()
def tasks():
    service = TaskService(InMemoryTaskDirectory())
    deps._task_service = service
    return service


def _steward(uid: str, email: str = "") -> Principal:
    return Principal(
        uid=uid, email=email or f"{uid}@naviant.com", role="user",
        scope="own", queues=("data-stewardship",),
    )


# ---- the registry ----------------------------------------------------------


def test_the_roster_becomes_a_registry_the_customer_can_edit(queues):
    """Bootstrapping is what lets the admin screen open onto the desks that
    already exist rather than onto nothing."""
    assert {q.slug for q in queues.list()} >= set(personas.ALL_QUEUES)
    assert queues.get("expense-review").description


def test_the_reverse_map_exists_at_all(queues):
    """`Persona.queues` is forward-only. Nothing could answer "who holds the
    AP desk" except one function in `ui_api` that scanned the roster inline."""
    assert CONTROLLER in queues.holders_of("expense-review")
    assert "ap-clerk" in queues.queues_for("connie.alberts@naviant.com")


def test_a_roster_binding_cannot_be_deleted_in_the_console(queues):
    """It would come back at the next restart. A change that does not stick is
    worse than one that is refused."""
    with pytest.raises(ValueError, match="persona roster"):
        queues.remove_member("expense-review", CONTROLLER)


def test_a_desk_added_in_the_console_binds_a_real_account(queues):
    queues.add_member("expense-review", "new.approver@naviant.com", "New Approver")
    assert "expense-review" in queues.queues_for("new.approver@naviant.com")
    queues.remove_member("expense-review", "new.approver@naviant.com")
    assert queues.queues_for("new.approver@naviant.com") == []


def test_a_personal_desk_is_stable_and_minted_once(queues):
    first = queues.ensure_personal(CONTROLLER, "Raymond Fitzgerald")
    again = queues.ensure_personal(CONTROLLER)
    assert first.slug == again.slug == personal_slug(CONTROLLER)
    assert first.personal and first.person_email == CONTROLLER
    # A replayed Flow step that resolves an approver must not mint a second
    # inbox for the same person.
    assert len([q for q in queues.list() if q.personal]) == 1


# ---- the account that held nothing ------------------------------------------


def test_a_signed_in_account_holds_the_desks_the_roster_names(queues, monkeypatch):
    """The 403 in the console, stated as a test.

    `masuter@naviant.com` signing in for real was bound to no queue at all,
    while `naviant-engineer` — the same human, the same email, one row of the
    roster away — held every one of them.
    """
    monkeypatch.setattr(settings, "auth_backend", "firebase")
    held = auth.queues_for(OPERATOR, "owner")
    assert "expense-review" in held
    assert set(held) >= set(personas.ALL_QUEUES)


def test_being_on_the_roster_adds_work_and_never_power(queues):
    """Access comes from the credential, assignment from the org. DOCTRINE
    leaves the divergence open at point 230; here it plainly diverges."""
    assert personas.reach_for("user") == {"scope": "own", "queues": ()}
    assert auth.queues_for(CONTROLLER, "user")  # bound to desks
    assert personas.reach_for("user")["scope"] == "own"  # still own-scoped


def test_an_unknown_email_holds_nothing(queues):
    assert auth.queues_for("stranger@example.com", "owner") == ()


# ---- the lease ---------------------------------------------------------------


def test_two_stewards_cannot_both_work_one_case(tasks, queues):
    tasks.create(Task(id="t1", kind="match_review", title="Northwind",
                      queue="data-stewardship"))
    tasks.claim("t1", "karen", holder_label="Karen Boothe")
    with pytest.raises(TaskClaimConflict, match="Karen Boothe"):
        tasks.claim("t1", "raymond", holder_label="Raymond Fitzgerald")


def test_the_holder_renews_rather_than_fighting_itself(tasks, queues):
    tasks.create(Task(id="t2", kind="curation", title="Promote",
                      queue="knowledge-curation"))
    first = tasks.claim("t2", "karen").claim
    again = tasks.claim("t2", "karen").claim
    assert again.claimed_at_iso == first.claimed_at_iso  # same sitting
    assert again.expires_at_iso >= first.expires_at_iso  # heartbeat extended
    assert again.steal_count == 0


def test_a_lapsed_lease_returns_to_the_pool_with_no_sweeper(tasks, queues):
    """Expiry is computed, not swept: there is no background pass whose
    failure could leave a stale lease standing."""
    from datetime import timedelta

    from app.models import utcnow

    tasks.create(Task(id="t3", kind="match_review", title="Blue Harbor",
                      queue="data-stewardship"))
    tasks.claim("t3", "karen")
    stale = tasks.get("t3")
    stale.claim.expires_at_iso = (utcnow() - timedelta(minutes=1)).isoformat()
    tasks.directory.put(stale)

    assert tasks.claim_holder(tasks.get("t3")) is None
    taken = tasks.claim("t3", "raymond")
    assert taken.claim.holder_uid == "raymond"
    assert taken.claim.steal_count == 0  # lapsing is not a steal


def test_a_takeover_is_allowed_and_counted(tasks, queues):
    """The alternative to "they went home, I am doing this" is an admin ticket,
    and a coordination mechanism that needs an admin gets routed around."""
    tasks.create(Task(id="t4", kind="match_review", title="Summit Health",
                      queue="data-stewardship"))
    tasks.claim("t4", "karen", holder_label="Karen Boothe")
    stolen = tasks.claim("t4", "raymond", steal=True)
    assert stolen.claim.holder_uid == "raymond"
    assert stolen.claim.steal_count == 1


def test_deciding_a_case_somebody_else_holds_is_refused(tasks, queues):
    tasks.create(Task(id="t5", kind="match_review", title="Four Lakes",
                      queue="data-stewardship"))
    tasks.claim("t5", "karen", holder_label="Karen Boothe")
    with pytest.raises(TaskClaimConflict):
        tasks.decide("t5", "approved", decided_by="raymond")


def test_an_uncontended_desk_never_asks_for_a_claim(tasks, queues):
    """The claim must cost nothing until it is actually contended, or it is a
    second click in front of every decision on every single-holder desk."""
    tasks.create(Task(id="t6", kind="decision", title="Invoice", queue="ap-clerk"))
    decided = tasks.decide("t6", "approved", decided_by="connie")
    assert decided.status == "done"
    assert decided.claim is None


def test_the_lease_is_cleared_when_the_task_is_settled(tasks, queues):
    tasks.create(Task(id="t7", kind="curation", title="Promote",
                      queue="knowledge-curation"))
    tasks.claim("t7", "karen")
    assert tasks.decide("t7", "approved", decided_by="karen").claim is None


def test_a_decided_task_cannot_be_claimed(tasks, queues):
    tasks.create(Task(id="t8", kind="decision", title="Done", queue="ap-clerk"))
    tasks.decide("t8", "approved", decided_by="connie")
    with pytest.raises(TaskAlreadyDecidedError):
        tasks.claim("t8", "karen")


def test_releasing_returns_the_case_without_deciding_it(tasks, queues):
    tasks.create(Task(id="t9", kind="match_review", title="Northwind",
                      queue="data-stewardship"))
    tasks.claim("t9", "karen")
    released = tasks.release("t9", "karen")
    assert released.claim is None and released.status == "open"
    assert tasks.claim("t9", "raymond").claim.holder_uid == "raymond"


def test_the_desk_decides_how_long_a_lease_runs(queues):
    """An AP clerk holds an invoice for minutes; a steward may sit with a merge
    for half an hour. The duration is a property of the work."""
    queues.update("data-stewardship", claim_ttl_seconds=1800)
    assert queues.claim_ttl("data-stewardship") == 1800
    assert queues.claim_ttl("ap-clerk") != 1800


# ---- the console no longer offers work it will refuse ------------------------


@pytest.fixture()
def api(_client, queues, tasks):
    tasks.create(Task(id="api1", kind="match_review", title="Northwind",
                      queue="data-stewardship"))
    tasks.create(Task(id="api2", kind="decision", title="Invoice",
                      queue="ap-clerk"))
    return _client


def _rows(api, **headers) -> dict[str, dict]:
    resp = api.get("/api/v1/tasks", headers=headers)
    assert resp.status_code == 200
    return {t["id"]: t for t in resp.json()}


#: Reads every queue in the deployment to diagnose it, and is bound to none —
#: which is exactly the shape the console broke on. An own-scoped clerk never
#: had the bug, because it never saw another desk's work in the first place.
SEES_ALL_HOLDS_NONE = {"X-Cumulus-Persona": "naviant-support"}
HOLDS_EVERYTHING = {"X-Cumulus-Persona": "naviant-engineer"}


def test_a_row_says_whether_this_principal_may_decide_it(api):
    """The heart of the console bug: 24 live Approve buttons over 24 refusals."""
    reader = _rows(api, **SEES_ALL_HOLDS_NONE)
    assert set(reader) == {"api1", "api2"}          # sees the whole deployment
    assert not any(r["decidable"] for r in reader.values())

    holder = _rows(api, **HOLDS_EVERYTHING)
    assert all(r["decidable"] for r in holder.values())


def test_a_refusal_names_whose_desk_it_is(api):
    """Telling somebody a desk is not theirs without naming whose leaves them
    nothing to do next."""
    row = _rows(api, **SEES_ALL_HOLDS_NONE)["api1"]
    assert "data-stewardship" in row["blocked_reason"]
    assert row["queue_holders"]
    assert any("@" in holder for holder in row["queue_holders"])
    assert all(holder in row["blocked_reason"] for holder in row["queue_holders"])


@pytest.mark.parametrize(
    "headers", [SEES_ALL_HOLDS_NONE, HOLDS_EVERYTHING], ids=["unbound", "bound"]
)
def test_the_greyed_button_and_the_403_cannot_disagree(api, headers):
    """`decidable` is computed from the same checks `decide` runs, in the same
    order, so a row the console offers is a row the API accepts — and a row it
    greys out is one the API would have refused."""
    for task_id, row in _rows(api, **headers).items():
        resp = api.post(
            f"/api/v1/tasks/{task_id}/decide",
            json={"verdict": "approved"}, headers=headers,
        )
        assert (resp.status_code < 400) == row["decidable"], (task_id, resp.text)


def test_claiming_a_desk_you_do_not_hold_is_refused(api):
    resp = api.post(
        "/api/v1/tasks/api1/claim", json={},
        headers={"X-Cumulus-Persona": "customer-user:ap"},
    )
    assert resp.status_code == 403


def test_a_contended_claim_answers_409_and_says_how_to_take_it(api):
    controller = {"X-Cumulus-Persona": "customer-user:controller"}
    engineer = {"X-Cumulus-Persona": "naviant-engineer"}
    assert api.post("/api/v1/tasks/api1/claim", json={}, headers=controller).status_code == 200

    contended = api.post("/api/v1/tasks/api1/claim", json={}, headers=engineer)
    assert contended.status_code == 409
    assert "steal=true" in contended.json()["detail"]

    stolen = api.post(
        "/api/v1/tasks/api1/claim", json={"steal": True}, headers=engineer
    )
    assert stolen.status_code == 200
    assert stolen.json()["claim"]["steal_count"] == 1


def test_a_held_task_reads_as_undecidable_to_everyone_else(api):
    controller = {"X-Cumulus-Persona": "customer-user:controller"}
    api.post("/api/v1/tasks/api1/claim", json={}, headers=controller)

    mine = _rows(api, **controller)["api1"]
    assert mine["claim_state"] == "yours" and mine["decidable"] is True

    theirs = _rows(api, **{"X-Cumulus-Persona": "naviant-engineer"})["api1"]
    assert theirs["claim_state"] == "held" and theirs["decidable"] is False
    assert "working it now" in theirs["blocked_reason"]


def test_my_queues_answers_why_i_cannot_decide(api):
    resp = api.get(
        "/api/v1/queues/mine", headers={"X-Cumulus-Persona": "customer-user:ae"}
    )
    assert resp.status_code == 200 and resp.json() == []


def test_a_desk_with_work_and_nobody_on_it_is_visible(api):
    resp = api.get(
        "/api/v1/queues/data-stewardship",
        headers={"X-Cumulus-Persona": "naviant-engineer"},
    )
    body = resp.json()
    assert body["open_tasks"] == 1
    assert body["holders"]


def test_retiring_a_desk_with_work_on_it_is_refused(api):
    """Deleting it would leave the tasks with no holder and no screen."""
    resp = api.delete(
        "/api/v1/queues/data-stewardship",
        headers={"X-Cumulus-Persona": "naviant-engineer"},
    )
    assert resp.status_code == 409
    assert "reroute" in resp.json()["detail"]
