"""A cold server with no estate configured: every screen renders, none errors.

This is the state the deployment now boots into. Until B4 a first start built
itself an invented world — a vendor master, an org chart, three weeks of
meetings, seven invoices — so the empty case was unreachable and, it turned
out, untested. It is the normal case now: install Cumulus, point it at
nothing, and every surface has to say *there is nothing here yet* rather than
500, or hand back a shape the console cannot draw.

The house rule these encode: **an access boundary or an empty world is a
statement, not a broken screen.** A 200 with an empty list and a sentence is
the statement; anything else is the console's problem to render around.
"""

import pytest

from app import personas
from tests import world as test_world

#: Every read surface the console opens on. Written out rather than pulled off
#: the router, because the point is to name the screens somebody would look at
#: on day one — a route that vanishes should fail this list loudly.
UI_ROUTES = (
    "/api/ui/v1/session",
    "/api/ui/v1/task-screens",
    "/api/ui/v1/ontology",
    "/api/ui/v1/sources",
    "/api/ui/v1/library",
    "/api/ui/v1/payables",
)

API_ROUTES = (
    "/api/v1/tasks",
    "/api/v1/tasks/stats",
    "/api/v1/payables",
    "/api/v1/payables/stats",
    "/api/v1/reimbursements",
    "/api/v1/graph/stats",
    "/api/v1/graph/objects",
    "/api/v1/graph/links",
    "/api/v1/graph/invariants",
    "/api/v1/graph/ontology",
    "/api/v1/graph/search?q=acme",
    "/api/v1/calendar/calendars",
    "/api/v1/calendar/me",
    "/api/v1/employee/me",
    "/api/v1/employee/pto",
    "/api/v1/mappings",
    "/api/v1/flows",
    "/api/v1/evals",
    "/api/v1/worlds",
)


@pytest.fixture()
def client(_client):
    """Nothing delivered, nothing pulled, nothing seeded."""
    test_world.fresh()
    return _client


@pytest.mark.parametrize("route", UI_ROUTES + API_ROUTES)
def test_every_read_surface_answers_on_an_empty_world(client, route):
    resp = client.get(route)
    assert resp.status_code == 200, f"{route} -> {resp.status_code} {resp.text}"


def test_the_graph_is_empty_and_says_so_rather_than_failing(client):
    stats = client.get("/api/v1/graph/stats").json()
    assert stats["objects"] == 0 and stats["links"] == 0
    assert client.get("/api/v1/graph/objects").json() == []
    assert client.get("/api/v1/graph/invariants").json()["ok"] is True
    # No mock world means no mock vendor. The search that used to find one is
    # the shortest proof the seed is gone.
    assert client.get("/api/v1/graph/search?q=acme").json() == []


def test_the_queues_are_empty_rather_than_absent(client):
    assert client.get("/api/v1/tasks").json() == []
    stats = client.get("/api/v1/tasks/stats").json()
    assert stats["open"] == 0
    assert sum(stats["by_queue"].values()) == 0


def test_the_sources_screen_reports_an_unregistered_estate(client):
    """Zero feeds is not the same fact as a connector that failed, and the
    card is where the difference is stated."""
    body = client.get("/api/ui/v1/sources").json()
    assert body["sources"] == []
    estate = body["estate"]
    assert estate["registered"] is False
    assert estate["discovery_error"] == ""
    assert estate["apps"] == [] and estate["feeds_active"] == 0
    # The map is still declared even with nothing landed against it: what is
    # empty is the world, not the contracts.
    assert body["problems"] == []
    assert len(client.get("/api/v1/mappings").json()["mappings"]) > 0


def test_the_ontology_screen_is_the_model_not_the_data(client):
    """The one screen an empty world should look *full*: types are declared in
    code, so they are there before a single row is."""
    body = client.get("/api/ui/v1/ontology").json()
    assert len(body["types"]) > 20
    assert all(t["instances"] == 0 for t in body["types"])


def test_the_session_resolves_and_admits_the_operator_is_not_an_employee(client):
    body = client.get("/api/ui/v1/session").json()
    assert body["persona_id"] == personas.DEFAULT_PERSONA.id
    assert body["in_workforce"] is False
    assert body["groups"]  # the switcher still offers everybody


@pytest.mark.parametrize(
    "persona", personas.ALL_PERSONAS, ids=[p.id for p in personas.ALL_PERSONAS]
)
def test_every_persona_gets_a_screen_rather_than_a_stack_trace(client, persona):
    """An empty world and a narrow scope are two different reasons to see
    nothing, and neither may be an error. 403 is a legitimate answer here —
    it is the access boundary speaking — but 5xx never is.
    """
    headers = {"X-Cumulus-Persona": persona.id}
    for route in UI_ROUTES + API_ROUTES:
        status = client.get(route, headers=headers).status_code
        assert status in (200, 403), f"{persona.id} {route} -> {status}"
