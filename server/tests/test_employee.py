"""The Employee Hub: profile, governed PTO, and the grounded HR agent.

The hub is a personal surface, so the question underneath every test here is
"who is me". The answer is the signed-in email, because that is the natural
key the graph holds an `Employee` under; anything else resolves to nobody.
"""

import pytest

import app.deps as deps
from app import personas
from app.config import settings
from app.employee import EmployeeService, InMemoryEmployeeDirectory
from tests import demo_world
from tests import world as test_world


@pytest.fixture()
def client(_client):
    world = test_world.fresh()
    demo_world.seed_workforce(world.sync, world.graph)
    demo_world.seed_tasks(world.tasks)
    # The hub holds a Dispatcher rather than an answer backend now: the HR
    # agent emits `HrQuestionAsked` and the `answer-question` Flow owns the
    # call. This world's flow has no generative backend behind it, so chat
    # still degrades to facts-plus-retrieval — and now leaves a run saying so.
    deps._employee_service = EmployeeService(
        InMemoryEmployeeDirectory(),
        world.knowledge,
        world.tasks,
        world.graph,
        dispatcher=world.dispatcher,
    )
    demo_world.seed_pto(deps._employee_service)
    return _client


def _as(persona_id: str) -> dict[str, str]:
    return {"X-Cumulus-Persona": persona_id}


#: Whose hub these run in. The default persona is the Naviant engineer, who is
#: deliberately not on the customer's org chart, so a personal surface has to
#: be asked for as somebody who is.
ME = _as("customer-user:ae")
ME_EMAIL = "john.smith@naviant.com"


def _stored_balance(days: float = 17.5, accrual: float = 1.5) -> None:
    """Give this person a balance, the way an HRIS delivery would.

    The hub invents none — a balance nobody delivered used to be minted at
    17.5 days and then quoted back as their own accrued record — so every test
    of the balance *machinery* has to be handed the fact the machinery works
    on. This is that seam, written directly because no feed fills it yet.
    """
    from app.employee import EmployeeProfile

    deps._employee_service.directory.put_profile(EmployeeProfile(
        email=ME_EMAIL, pto_balance_days=days, pto_accrual_per_month=accrual,
    ))


def test_the_profile_is_the_person_the_org_chart_knows(client):
    """Title, team and manager are read through from the graph, not defaulted.

    The hub used to mint a blank profile the first time anybody read it, so it
    showed a synthesized person with a balance nobody had accrued.
    """
    me = client.get("/api/v1/employee/me", headers=ME).json()
    assert me["email"] == "john.smith@naviant.com"
    assert me["name"] == "John Smith"
    assert me["title"] == "Account Executive"
    assert me["team"] == "Sales"
    assert me["manager"] == "Marcus Reyes"
    assert me["location"] == "Madison, WI"
    assert me["in_workforce"] is True

    # The hub's own state is honestly empty. It used to be minted on the first
    # read at 17.5 days, 1.67/month, a named holiday and four benefit plans
    # with dollar figures on them — none of it delivered by anything, all of it
    # then shown to the employee as their own record.
    assert me["pto_balance_days"] is None
    assert me["pto_accrual_per_month"] is None
    assert me["next_company_holiday"] == ""
    assert me["benefits"] == []
    assert "on record" in me["records_note"]

    # And nothing was written down by reading the page.
    assert deps._employee_service.directory.get_profile(ME_EMAIL) is None

    # A delivered balance is shown as delivered, and the note goes.
    _stored_balance()
    with_balance = client.get("/api/v1/employee/me", headers=ME).json()
    assert with_balance["pto_balance_days"] == 17.5
    assert with_balance["records_note"] == ""


def test_the_persona_decides_whose_hub_it_is(client):
    me = client.get(
        "/api/v1/employee/me", headers=_as("customer-user:controller")
    ).json()
    assert me["email"] == "raymond.fitzgerald@naviant.com"
    assert me["name"] == "Raymond Fitzgerald"
    assert me["title"] == "Controller"
    assert me["team"] == "Finance & Administration"
    assert me["manager"] == "Diane Vogel"


@pytest.mark.parametrize(
    "persona", (personas.SUPPORT, personas.ENGINEER), ids=lambda p: p.id
)
def test_a_principal_outside_the_workforce_is_told_so(client, persona):
    """Neither Naviant persona is the customer's employee — not the support
    engineer diagnosing a ticket, and not the engineer operating the
    deployment. The hub renders and says so, rather than inventing a colleague
    to fill the screen."""
    me = client.get("/api/v1/employee/me", headers=_as(persona.id)).json()
    assert me["email"] == persona.email
    assert me["in_workforce"] is False
    assert "not in the workforce" in me["workforce_note"]
    assert me["name"] == "" and me["benefits"] == []
    # Not zero. "No balance is on record" and "your balance is zero days" are
    # different facts and the second one is a lie about this account.
    assert me["pto_balance_days"] is None


def test_the_hub_is_served_the_window_it_calls_governed(client):
    """The console printed "requests of 3 working days or fewer auto-approve"
    from a literal `3` in Dart, beside the claim that the number was governed.

    Editing the policy asset moved the rule and not the sentence — the exact
    drift the whole governed-knowledge argument exists to prevent — so the
    parsed value and the revision-stamped ref it came from travel with the
    profile, and the rule the screen states is the rule the flow applies.
    """
    me = client.get("/api/v1/employee/me", headers=ME).json()
    assert me["pto_auto_approve_days"] == 3.0
    assert me["pto_policy_ref"].startswith("knowledge:pto-policy@rev")

    published = client.get("/api/v1/assets/pto-policy").json()
    edited = client.put(
        "/api/v1/assets/pto-policy",
        json={
            "content": published["content"].replace(
                "3 working days or fewer", "5 working days or fewer"
            ),
        },
    )
    assert edited.status_code == 200

    moved = client.get("/api/v1/employee/me", headers=ME).json()
    assert moved["pto_auto_approve_days"] == 5.0
    assert moved["pto_policy_ref"] != me["pto_policy_ref"]

    # And the flow decides on the same value the hub now prints.
    request = client.post(
        "/api/v1/employee/pto",
        json={"start_date": "2026-09-14", "end_date": "2026-09-18", "days": 4,
              "note": "a week off"},
        headers=ME,
    ).json()
    assert request["state"] == "approved"
    assert "governed 5-day auto-approval window" in request["reason"]


def test_a_principal_outside_the_workforce_still_reads_the_governed_rule(
    client,
):
    """The window is a fact about the deployment, not about the reader — the
    hub says what the policy is even where it has nobody to apply it to."""
    me = client.get(
        "/api/v1/employee/me", headers=_as(personas.ENGINEER.id)
    ).json()
    assert me["in_workforce"] is False
    assert me["pto_auto_approve_days"] == 3.0
    assert me["pto_policy_ref"].startswith("knowledge:pto-policy@rev")


def test_short_pto_auto_approves_within_governed_window(client):
    """The window decides. With no balance on record there is nothing to
    deduct, and the reason says that rather than deducting from a default."""
    resp = client.post(
        "/api/v1/employee/pto",
        json={"start_date": "2026-09-14", "end_date": "2026-09-16", "days": 2,
              "note": "long weekend"},
        headers=ME,
    )
    assert resp.status_code == 201
    request = resp.json()
    assert request["state"] == "approved"
    assert "auto-approval window" in request["reason"]
    assert "no balance on record" in request["reason"]
    me = client.get("/api/v1/employee/me", headers=ME).json()
    assert me["pto_balance_days"] is None


def test_a_delivered_balance_is_deducted_on_auto_approval(client):
    """And when a balance *is* on record, the machinery is unchanged."""
    _stored_balance()
    request = client.post(
        "/api/v1/employee/pto",
        json={"start_date": "2026-09-14", "end_date": "2026-09-16", "days": 2},
        headers=ME,
    ).json()
    assert request["state"] == "approved"
    me = client.get("/api/v1/employee/me", headers=ME).json()
    assert me["pto_balance_days"] == 15.5  # deducted immediately


def test_long_pto_routes_to_hr_and_decision_resumes(client):
    _stored_balance()
    request = client.post(
        "/api/v1/employee/pto",
        json={"start_date": "2026-11-02", "end_date": "2026-11-10", "days": 6},
        headers=ME,
    ).json()
    assert request["state"] == "pending" and request["task_id"]
    task = client.get(f"/api/v1/tasks/{request['task_id']}").json()
    assert task["queue"] == "hr"
    assert any(r.startswith("knowledge:pto-policy@rev") for r in task["refs"])

    client.post(
        f"/api/v1/tasks/{task['id']}/decide",
        json={"verdict": "approved", "note": "Coverage confirmed."},
    )
    mine = client.get("/api/v1/employee/pto", headers=ME).json()
    decided = next(r for r in mine if r["id"] == request["id"])
    assert decided["state"] == "approved"
    me = client.get("/api/v1/employee/me", headers=ME).json()
    assert me["pto_balance_days"] == 11.5  # deducted on approval, not request


def test_a_request_with_no_balance_on_record_says_so_to_the_decider(client):
    """The Task is the honest one: HR is told coverage was not checked, rather
    than shown a number the hub made up to check it against."""
    request = client.post(
        "/api/v1/employee/pto",
        json={"start_date": "2026-11-02", "end_date": "2026-11-10", "days": 6},
        headers=ME,
    ).json()
    task = client.get(f"/api/v1/tasks/{request['task_id']}").json()
    assert "coverage was not checked" in task["summary"]
    balance = next(e for e in task["evidence"] if e["label"] == "Balance")
    assert balance["value"] == "not on record"

    client.post(
        f"/api/v1/tasks/{task['id']}/decide", json={"verdict": "approved"}
    )
    mine = client.get("/api/v1/employee/pto", headers=ME).json()
    assert next(r for r in mine if r["id"] == request["id"])["state"] == "approved"
    # The decision stands and no balance was invented to move.
    me = client.get("/api/v1/employee/me", headers=ME).json()
    assert me["pto_balance_days"] is None


def test_pto_over_a_recorded_balance_is_declined(client):
    """The balance gate needs a balance. It is exercised against a delivered
    one, because denying somebody's leave against a number this deployment
    invented is the failure the default was."""
    _stored_balance()
    request = client.post(
        "/api/v1/employee/pto",
        json={"start_date": "2026-12-01", "end_date": "2026-12-31", "days": 25},
        headers=ME,
    ).json()
    assert request["state"] == "denied"
    assert "balance" in request["reason"]


def test_pto_with_no_balance_on_record_is_not_declined_against_a_default(client):
    """The same 25 days, with nothing on record: the window still routes it to
    HR, and nothing denies it on a balance nobody delivered."""
    request = client.post(
        "/api/v1/employee/pto",
        json={"start_date": "2026-12-01", "end_date": "2026-12-31", "days": 25},
        headers=ME,
    ).json()
    assert request["state"] == "pending"
    assert "routed to HR" in request["reason"]


def test_a_pending_request_pairs_with_the_task_that_holds_it(client):
    """And belongs to whoever asked, so it is visible in their hub and in
    nobody else's."""
    mine = client.get("/api/v1/employee/pto", headers=ME).json()
    seeded = next(r for r in mine if r["id"] == "pto_seed_01")
    assert seeded["state"] == "pending"
    assert seeded["task_id"] == "task_pto_seed_01"
    assert seeded["email"] == "john.smith@naviant.com"
    assert client.get("/api/v1/tasks/task_pto_seed_01").json()["queue"] == "hr"
    assert client.get(
        "/api/v1/employee/pto", headers=_as("customer-user:controller")
    ).json() == []


def test_hr_chat_says_there_is_no_balance_rather_than_naming_one(client):
    """"How much PTO do I have" against no HRIS is a question this deployment
    cannot answer, and it used to answer it anyway — with the 17.5 days the
    hub minted, presented as the employee's own record."""
    reply = client.post(
        "/api/v1/employee/hr-chat",
        json={"message": "How much PTO do I have and how do I take time off?"},
        headers=ME,
    ).json()
    assert "no pto balance" in reply["reply"].lower()
    assert "17.5" not in reply["reply"]
    # The governed half still answers: the window is a fact about the
    # deployment rather than about the reader.
    assert any(c["id"] == "pto-policy" for c in reply["citations"])
    assert reply["backend"] == "retrieval"  # honest about the degraded mode


def test_hr_chat_reads_out_a_balance_that_was_delivered(client):
    _stored_balance()
    reply = client.post(
        "/api/v1/employee/hr-chat",
        json={"message": "How much PTO do I have and how do I take time off?"},
        headers=ME,
    ).json()
    assert "17.5 days" in reply["reply"]
    assert "PTO balance: 17.5 days (accruing 1.5/month)" in reply["grounded_on"]


def test_hr_chat_answers_benefits_from_the_guide_not_from_an_enrollment(client):
    """The plans are written down; who is enrolled in them is not.

    The hub used to mint four enrollments per person — "401(k) with match: 4%
    employer match, vested immediately" — and the agent read them out as this
    employee's own. The governed guide says the same thing and is a document a
    reader can check, so it answers and the enrollment claim is dropped.
    """
    reply = client.post(
        "/api/v1/employee/hr-chat", json={"message": "What is the 401k match?"},
        headers=ME,
    ).json()
    assert "No benefits enrollment is on record" in reply["reply"]
    assert any(c["id"] == "benefits-overview" for c in reply["citations"])
    assert "4% of salary" in reply["citations"][0]["snippet"]


def test_key_cannot_request_pto(client, monkeypatch):
    from app.api_keys import InMemoryApiKeyDirectory
    from app.identities import IdentityService, InMemoryMachineIdentityDirectory
    from app.users import InMemoryUserDirectory

    monkeypatch.setattr(settings, "auth_backend", "firebase")
    monkeypatch.setattr(settings, "admin_api_key", "root-sekret")
    owner = {"X-API-Key": "root-sekret"}
    deps._user_directory = InMemoryUserDirectory()
    deps._api_key_directory = InMemoryApiKeyDirectory()
    deps._identity_service = IdentityService(
        deps._user_directory, InMemoryMachineIdentityDirectory()
    )
    identity = client.post(
        "/api/v1/identities",
        json={"display_name": "HR bot", "role": "user"},
        headers=owner,
    ).json()
    minted = client.post(
        "/api/v1/api-keys",
        json={"name": "CI", "identity_uid": identity["uid"]},
        headers=owner,
    ).json()
    key = {"X-API-Key": minted["token"]}
    resp = client.post(
        "/api/v1/employee/pto",
        json={"start_date": "2026-09-14", "end_date": "2026-09-15", "days": 1},
        headers=key,
    )
    assert resp.status_code == 403
    # The chat read path is fine for an ambient agent.
    assert (
        client.post(
            "/api/v1/employee/hr-chat", json={"message": "pto?"}, headers=key
        ).status_code
        == 200
    )


# ---- The HR agent ----------------------------------------------------------
#
# Two things were wrong with it and they compounded: it could only see the
# `people/` taxonomy, and it named the top of whatever ranking came back "the
# governing document" unconditionally. So the questions it could answer it
# answered from the wrong half of the corpus, and the ones it could not it
# answered anyway.


def test_the_agent_reaches_outside_the_people_taxonomy(client):
    """"How do I get reimbursed for a flight" is an employee question filed
    under finance. The taxonomy scope made the two documents that answer it
    unreachable while letting the onboarding checklist answer "what is our
    expense approval policy", which it does not."""
    reply = client.post(
        "/api/v1/employee/hr-chat",
        json={"message": "how do I get reimbursed for a flight?"},
        headers=ME,
    ).json()
    cited = {c["id"] for c in reply["citations"]}
    assert "travel-booking-guideline" in cited

    expenses = client.post(
        "/api/v1/employee/hr-chat",
        json={"message": "what is our expense approval policy?"},
        headers=ME,
    ).json()
    # The checklist genuinely contains all three words, so it is retrieved and
    # is not suppressed — it is ranked under the document that governs and
    # named as lower authority, which is the honest place for it. What must
    # never happen again is the checklist *being* the answer.
    assert expenses["citations"][0]["id"] == "expense-approval-policy"
    assert "New Hire Onboarding Checklist" not in expenses["reply"].split(
        "at lower authority"
    )[0]


def test_nonsense_gets_no_governing_document(client):
    """The failure that made the hub read as broken: every ranking has a first
    row, and naming it the rule turns "I don't know" into a confident wrong
    answer an employee has no way to check."""
    reply = client.post(
        "/api/v1/employee/hr-chat",
        json={"message": "zzzz quarterly kombucha allowance"},
        headers=ME,
    ).json()
    assert reply["citations"] == []
    assert "could not find" in reply["reply"]
    assert "governing document" not in reply["reply"]
    # And it says what it discarded, so the claim can be checked.
    assert reply["grounding"]["below_floor"] >= 0
    assert reply["grounding"]["min_coverage"] > 0


def test_a_partial_match_is_offered_and_not_called_the_rule(client):
    """The middle band. No document covers vendor onboarding, so the closest
    one is handed over as exactly that — and the gap is named as a gap."""
    reply = client.post(
        "/api/v1/employee/hr-chat",
        json={"message": "what is the vendor onboarding process?"},
        headers=ME,
    ).json()
    assert reply["citations"]
    assert "closest thing in the corpus" in reply["reply"]
    assert "governing document is" not in reply["reply"]


def test_the_answer_says_which_precedence_it_ranked_under(client):
    """"The governing document is X" is a claim about precedence. A reader who
    cannot see the rule, at the revision it was read, cannot check it."""
    reply = client.post(
        "/api/v1/employee/hr-chat",
        json={"message": "what is our expense approval policy?"},
        headers=ME,
    ).json()
    grounding = reply["grounding"]
    assert grounding["policy_ref"].startswith("knowledge:retrieval-authority@rev")
    assert grounding["precedence"] == "policy > standard > guideline > how-to > note"
    assert "policy > standard" in reply["reply"]
    # The lower-authority document that also matched is named rather than
    # silently dropped: being told only the winning rule leaves no way to
    # discover that your team follows the other one.
    assert "at lower authority" in reply["reply"]
    assert "Corporate Travel Booking Guideline" in reply["reply"]


def test_machinery_is_never_cited_at_an_employee(client):
    """A prompt, a config and a generated schema export carry no authority by
    construction. The agent used to cite `source-mappings` to people."""
    for question in (
        "what is the 401k match?",
        "what is our expense approval policy?",
        "how do I get reimbursed for a flight?",
    ):
        reply = client.post(
            "/api/v1/employee/hr-chat", json={"message": question}, headers=ME
        ).json()
        for citation in reply["citations"]:
            assert citation["authority_level"] is not None, citation


def test_a_follow_up_carries_the_turn_before_it(client):
    """"What about dental?" is not a query. On its own it retrieves whatever
    else says "dental"; with the previous turn it retrieves the benefits
    guide, and the reply reports the query it actually ran."""
    reply = client.post(
        "/api/v1/employee/hr-chat",
        json={
            "message": "what about dental?",
            "history": [
                {"role": "employee", "text": "what does the medical plan cover?"},
                {"role": "agent", "text": "Health PPO 2500 covers 85%."},
            ],
        },
        headers=ME,
    ).json()
    assert "medical plan" in reply["grounding"]["query"]
    assert "dental" in reply["grounding"]["query"]

    # A question that stands on its own is left alone.
    standalone = client.post(
        "/api/v1/employee/hr-chat",
        json={
            "message": "what is our expense approval policy?",
            "history": [{"role": "employee", "text": "what about dental?"}],
        },
        headers=ME,
    ).json()
    assert standalone["grounding"]["query"] == "what is our expense approval policy?"


def test_the_agent_knows_who_it_is_talking_to(client):
    """Read through from the org chart, like the profile is. "Who do I ask" is
    the most common HR question there is and the graph has the answer."""
    reply = client.post(
        "/api/v1/employee/hr-chat",
        json={"message": "who is my manager and what team am I on?"},
        headers=ME,
    ).json()
    assert "Manager: Marcus Reyes" in reply["grounded_on"]
    assert "Team: Sales" in reply["grounded_on"]
    assert "Marcus Reyes" in reply["reply"]


def test_a_principal_outside_the_workforce_is_not_told_they_have_no_days(client):
    """The operator's default view. The agent used to open every reply with
    "PTO balance: 0 days (accruing 0/month)" and "Next company holiday: ." —
    two facts about nobody, and most of what made the hub look broken."""
    reply = client.post(
        "/api/v1/employee/hr-chat",
        json={"message": "how much PTO do I have?"},
        headers=_as(personas.ENGINEER.id),
    ).json()
    assert reply["grounded_on"] == []
    assert "0 days" not in reply["reply"]
    assert "not in the workforce" in reply["reply"]
    # And the governed half still answers: the policy is a fact about the
    # deployment, not about the reader.
    assert any(c["id"] == "pto-policy" for c in reply["citations"])


def test_the_agent_does_not_answer_from_the_deployments_own_internals(client):
    """The one plane an employee's agent does not read.

    Not a second cage — `finance/` and every other customer desk stay in
    reach, which was the point of removing the first one. `platform/` is the
    deployment describing itself: the ontology export, the source mappings,
    the price sheet, and the document that decides this agent's own ranking.
    Those share enough vocabulary with real questions — "coverage", "policy",
    "resolution" — that a governance memo about retrieval came within a tie of
    being named the governing document for somebody's dental cover.
    """
    reply = client.post(
        "/api/v1/employee/hr-chat",
        json={
            "message": "what about dental?",
            "history": [
                {"role": "employee", "text": "what does the medical plan cover?"},
            ],
        },
        headers=ME,
    ).json()
    cited = [c["id"] for c in reply["citations"]]
    assert cited and cited[0] == "benefits-overview"
    assert "retrieval-authority" not in cited
    assert not any(c["path"].startswith("platform/") for c in reply["citations"])
