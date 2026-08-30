"""The estate connector: pull deltas, chunk them, post them through the door.

The connector is the one component in the system that talks to a network, and
these tests hold it to the two properties that make an at-least-once puller
safe: a watermark advances only for work the door acknowledged, and a re-pull
of the same window changes nothing. Everything semantic — scoring, precedence,
minting — is the flow's, and the last test here is the guard that keeps it
that way.

The estate is faked (`estate_fixtures`), the door is not: deliveries go through
`POST /api/v1/sources/deliver` against the session TestClient, so the wire
shapes, the Dispatcher and the workforce flow are all exercised for real.
"""

from __future__ import annotations

import ast
import pathlib
from urllib.parse import parse_qs, urlparse

import pytest

from app.estate import (
    FEEDS,
    HRIS_WORKFORCE,
    EstateConnector,
    EstateError,
    FeedSpec,
    InMemoryConnectorStateStore,
    team_slug,
)
from app.graph import object_id
from app.sync import SyncRequest
from tests import estate_fixtures as fx
from tests import world as test_world

PROJECT = "cumulus-demo-mitchell-suter"


def build(estate: fx.FakeEstate | None = None, **over) -> EstateConnector:
    """A connector over the fake estate, delivering into a list by default."""
    estate = estate or fx.FakeEstate()
    delivered: list[dict] = []
    connector = EstateConnector(
        InMemoryConnectorStateStore(),
        project=PROJECT,
        namespace=over.pop("namespace", "naviant"),
        fetch=estate.fetch,
        deliver=over.pop("deliver", None) or (
            lambda payload: (delivered.append(payload), {"runs": []})[1]
        ),
        **over,
    )
    connector.estate = estate  # type: ignore[attr-defined]  — test handle
    connector.delivered = delivered  # type: ignore[attr-defined]
    return connector


def door(client):
    """The real HTTP door as the connector's `deliver`."""

    def deliver(payload: dict) -> dict:
        response = client.post("/api/v1/sources/deliver", json=payload)
        if response.status_code != 202:
            raise EstateError(f"door → {response.status_code}: {response.text}")
        return response.json()

    return deliver


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


# -- discovery, and the extension point --------------------------------------


def test_discovery_activates_only_the_apps_the_namespace_runs():
    connector = build()
    assert connector.discover()["id"] == "naviant"
    assert [f.feed for f in connector.active_feeds()] == [HRIS_WORKFORCE]


def test_a_namespace_without_the_app_activates_nothing():
    """The filter is the namespace's `apps` list, not the FEEDS table."""
    payload = {
        "namespaces": [
            {**fx.NAMESPACES["namespaces"][0],
             "apps": ["crm", "erp", "office", "dms"]},
        ],
        "default": "naviant",
    }
    connector = build(fx.FakeEstate(namespaces=payload))
    assert connector.active_feeds() == []
    with pytest.raises(EstateError):
        connector.pull(HRIS_WORKFORCE)


def test_a_sixth_app_is_one_feedspec_and_no_connector_change():
    """The psa app, proved before it exists: one row in the registry."""
    psa = FeedSpec(
        feed="psa-projects", app="psa", resources=("projects",),
        build=lambda rows, deref: SyncRequest(source="psa-projects"),
    )
    payload = {
        "namespaces": [
            {**fx.NAMESPACES["namespaces"][0],
             "apps": [*fx.NAMESPACES["namespaces"][0]["apps"], "psa"]},
        ],
        "default": "naviant",
    }
    connector = build(fx.FakeEstate(namespaces=payload), feeds=(*FEEDS, psa))
    assert {f.feed for f in connector.active_feeds()} == {
        HRIS_WORKFORCE, "psa-projects"
    }
    # And with the app absent again, the same registry activates only the five.
    assert {f.feed for f in build(feeds=(*FEEDS, psa)).active_feeds()} == {
        HRIS_WORKFORCE
    }


def test_an_unknown_namespace_is_an_error_not_a_default():
    connector = build(namespace="nowhere")
    with pytest.raises(EstateError):
        connector.discover()


# -- watermarks ---------------------------------------------------------------


def test_a_watermark_advances_per_resource():
    connector = build()
    out = connector.pull(HRIS_WORKFORCE)

    assert out["outcome"] == "delivered"
    assert out["pulled"] == {"departments": 7, "jobs": 5, "employees": 10}
    marks = connector.state(HRIS_WORKFORCE).watermarks
    # Each resource carries its own max `updatedAt` — a shared cursor would
    # re-pull the jobs list every time an employee moved.
    assert marks["departments"] == max(
        d["updatedAt"] for d in fx.DEPARTMENTS
    )
    assert marks["jobs"] == max(j["updatedAt"] for j in fx.JOBS)
    assert marks["employees"] == max(e["updatedAt"] for e in fx.EMPLOYEES)
    assert marks["jobs"] != marks["employees"]


def test_a_repull_asks_strictly_past_the_watermark_and_finds_nothing():
    """The estate filters `updatedAt >`, so the cursor needs no overlap."""
    connector = build()
    connector.pull(HRIS_WORKFORCE)
    mark = connector.state(HRIS_WORKFORCE).watermarks["employees"]

    second = connector.pull(HRIS_WORKFORCE)
    asked = urlparse(connector.estate.calls_for("employees")[-1]).query
    assert parse_qs(asked)["updatedSince"] == [mark]
    assert second["outcome"] == "unchanged"
    assert second["pulled"] == {"departments": 0, "jobs": 0, "employees": 0}


def test_an_empty_delta_delivers_nothing_and_still_stamps_the_state():
    """A pull that found nothing is not a pull that did not happen."""
    connector = build()
    connector.pull(HRIS_WORKFORCE)
    before = connector.state(HRIS_WORKFORCE).model_copy(deep=True)
    connector.delivered.clear()

    out = connector.pull(HRIS_WORKFORCE)
    state = connector.state(HRIS_WORKFORCE)
    assert connector.delivered == []
    assert out["chunks"] == []
    assert state.watermarks == before.watermarks
    assert state.last_pull_at > before.last_pull_at
    assert state.last_outcome == "unchanged"
    assert state.last_error == ""


# -- chunking -----------------------------------------------------------------


def test_chunks_split_on_updated_at_boundaries_with_one_key_each():
    connector = build(chunk_size=4)
    out = connector.pull(HRIS_WORKFORCE)

    assert len(connector.delivered) == len(out["chunks"]) > 1
    keys = [c["idempotency_key"] for c in out["chunks"]]
    assert keys == [f"{HRIS_WORKFORCE}:{c['watermark']}:{i}"
                    for i, c in enumerate(out["chunks"])]
    assert len(set(keys)) == len(keys)
    # Chunk watermarks climb, and every row lands exactly once across them.
    assert [c["watermark"] for c in out["chunks"]] == sorted(
        c["watermark"] for c in out["chunks"]
    )
    assert sum(c["records"] for c in out["chunks"]) == 7 + 5 + 10
    # A row never straddles a boundary: the last row of one chunk and the
    # first of the next never share an `updatedAt`, or the strict-`>` re-pull
    # would silently drop the tail.
    edges = [c["watermark"] for c in out["chunks"]]
    assert len(set(edges)) == len(edges)
    # And the state ends where a single unchunked delivery would have.
    assert connector.state(HRIS_WORKFORCE).watermarks == build().pull(
        HRIS_WORKFORCE
    )["watermarks"]


def test_a_chunk_the_door_refuses_stops_the_feed_where_it_stood():
    """Watermarks advance per acknowledged chunk — never past a refusal."""
    accepted: list[dict] = []

    def flaky(payload: dict) -> dict:
        if len(accepted) >= 2:
            raise EstateError("door → 500")
        accepted.append(payload)
        return {"runs": []}

    estate = fx.FakeEstate()
    connector = build(estate, chunk_size=4, deliver=flaky)
    with pytest.raises(EstateError):
        connector.pull(HRIS_WORKFORCE)

    state = connector.state(HRIS_WORKFORCE)
    assert len(accepted) == 2
    assert state.last_outcome == "failed"
    assert "500" in state.last_error
    # Exactly the ground the two acknowledged chunks covered: a resource the
    # refusal came before never advanced at all, and no resource advanced past
    # where a whole pull would have put it.
    full = build().pull(HRIS_WORKFORCE)["watermarks"]
    # The employees route never advanced — the refusal came before its first
    # row — and nothing advanced past where a whole pull would have put it.
    assert set(state.watermarks) < set(full)
    assert "employees" not in state.watermarks
    assert all(state.watermarks[r] <= full[r] for r in state.watermarks)

    # And the next tick finishes the job from where it stood.
    connector._deliver = lambda payload: {"runs": []}
    assert connector.pull(HRIS_WORKFORCE)["outcome"] == "delivered"
    assert connector.state(HRIS_WORKFORCE).watermarks == full


# -- failure ------------------------------------------------------------------


def test_a_failed_resource_advances_no_watermark_at_all():
    """Multi-resource feeds are all-or-nothing: departments and jobs answered,
    employees did not, and a half-advanced cursor would lose the difference."""
    connector = build(fx.FakeEstate(fails={("hris", "employees")}))
    with pytest.raises(EstateError):
        connector.pull(HRIS_WORKFORCE)

    state = connector.state(HRIS_WORKFORCE)
    assert connector.delivered == []
    assert state.watermarks == {}
    assert state.last_outcome == "failed"
    assert "employees" in state.last_error
    assert state.last_pull_at


def test_a_failure_is_recoverable_on_the_next_tick():
    estate = fx.FakeEstate(fails={("hris", "employees")})
    connector = build(estate)
    with pytest.raises(EstateError):
        connector.pull(HRIS_WORKFORCE)
    estate.fails.clear()

    out = connector.pull(HRIS_WORKFORCE)
    assert out["outcome"] == "delivered"
    assert connector.state(HRIS_WORKFORCE).last_error == ""


def test_one_red_feed_does_not_stop_the_others():
    psa = FeedSpec(
        feed="psa-projects", app="psa", resources=("projects",),
        build=lambda rows, deref: SyncRequest(source="psa-projects"),
    )
    payload = {
        "namespaces": [
            {**fx.NAMESPACES["namespaces"][0],
             "apps": [*fx.NAMESPACES["namespaces"][0]["apps"], "psa"]},
        ],
        "default": "naviant",
    }
    connector = build(
        fx.FakeEstate(namespaces=payload, fails={("psa", "projects")}),
        feeds=(*FEEDS, psa),
    )
    outcomes = {o["feed"]: o for o in connector.pull_all()}
    assert outcomes[HRIS_WORKFORCE]["outcome"] == "delivered"
    assert outcomes["psa-projects"]["outcome"] == "failed"


# -- the mechanical translation ----------------------------------------------


def test_the_page_is_joined_and_only_the_gaps_are_dereferenced():
    """The declared-key dereference, and its bound: one GET per referenced
    record the page does not already contain, never one per row."""
    connector = build()
    connector.pull(HRIS_WORKFORCE)
    payload = connector.delivered[0]

    people = {e["external_id"]: e for e in payload["employees"]}
    # Joined in-batch: JOB-1006 and EMP-1003 both travelled in the page.
    assert people["EMP-1010"]["title"] == "Implementation Consultant"
    assert people["EMP-1010"]["manager_email"] == "gregory.lindholm@naviant.com"
    assert people["EMP-1010"]["team_slug"] == "customer-support"
    # Dereferenced: JOB-1003 is not in the pulled jobs list.
    assert people["EMP-1016"]["title"] == "Account Executive"
    assert connector.estate.calls_for("jobs")[-1].endswith("/jobs/JOB-1003")
    # A root with no manager binds nothing rather than inventing one.
    assert people["EMP-1001"]["manager_email"] == ""

    teams = {t["external_id"]: t for t in payload["teams"]}
    assert teams["DEPT-1004"]["slug"] == "customer-support"
    assert teams["DEPT-1004"]["parent_slug"] == "professional-services"
    assert teams["DEPT-1004"]["lead_email"] == "gregory.lindholm@naviant.com"
    assert teams["DEPT-1001"]["parent_slug"] == ""
    # DEPT-1007's head is off-page, so it cost exactly one GET.
    assert teams["DEPT-1007"]["lead_email"] == "karen.boothe@naviant.com"
    assert len([u for u in connector.estate.calls_for("employees")
                if u.endswith("/employees/EMP-1006")]) == 1


def test_team_slug_is_the_declared_natural_key():
    assert team_slug("Finance & Administration") == "finance-administration"
    assert team_slug("Professional Services") == "professional-services"
    assert team_slug("") == ""


def test_the_connector_does_nothing_semantic():
    """Risk decision 2, enforced rather than promised: no scoring, no
    precedence, no minting in `estate.py` — so it may not reach the entity
    layer or the resolver to do any of it."""
    source = pathlib.Path(__file__).parent.parent / "app" / "estate.py"
    tree = ast.parse(source.read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").lstrip("."))
        elif isinstance(node, ast.Import):
            imported.update(a.name.split(".")[-1] for a in node.names)
    assert "graph" not in imported
    assert "resolution" not in imported
    assert "gateway" not in imported


# -- through the real door ----------------------------------------------------


def test_the_feed_lands_the_firm_through_the_http_door(client, world):
    connector = build(deliver=door(client))
    out = connector.pull(HRIS_WORKFORCE)

    assert out["outcome"] == "delivered"
    counts = world.graph.stats()["by_type"]
    assert counts["Team"] == len(fx.DEPARTMENTS)
    # Ten rows, eight people: two of the sim's workers hold an email another
    # record already holds, and the natural key is the email. The connector
    # neither merged nor split them — the key did, and B5 gets to argue about
    # whether it was right.
    emails = {e["workEmail"] for e in fx.EMPLOYEES}
    assert len(fx.EMPLOYEES) == 10
    assert counts["Employee"] == len(emails) == 8

    nina = world.graph.get(object_id("Employee", "nina.alvarez@naviant.com"))
    assert nina.props["title"] == "Implementation Consultant"
    assert nina.props["location"] == "Madison, WI"
    assert [c.source for c in nina.claims if c.field == "email"] == [
        f"hris:{HRIS_WORKFORCE}"
    ]
    managers = [o.label for _, o in
                world.graph.neighbors(nina.id, "reports_to", "out")]
    assert managers == ["Gregory Lindholm"]
    teams = [o.label for _, o in
             world.graph.neighbors(nina.id, "member_of", "out")]
    assert teams == ["Customer Support"]

    support = world.graph.get(object_id("Team", "customer-support"))
    parents = [o.label for _, o in
               world.graph.neighbors(support.id, "subteam_of", "out")]
    assert parents == ["Professional Services"]
    assert world.graph.check_invariants() == []


def test_the_feed_shows_up_as_a_source_with_its_watermark(client, world):
    connector = build(deliver=door(client))
    connector.pull(HRIS_WORKFORCE)

    rows = {(s.source, s.kind): s for s in world.sync.sources()}
    status = rows[(HRIS_WORKFORCE, "workforce")]
    assert status.flow_id == "workforce-sync"
    assert status.last_status == "succeeded"
    assert status.records_landed == len(fx.DEPARTMENTS) + len(fx.EMPLOYEES)
    assert status.watermark == connector.state(HRIS_WORKFORCE).watermarks[
        "employees"
    ]


def test_a_repull_of_the_same_window_changes_nothing(client, world):
    """Re-delivery is safe, which is what lets the connector retry freely: the
    idempotency key re-enters the same run and the upserts are keyed."""
    connector = build(deliver=door(client))
    connector.pull(HRIS_WORKFORCE)
    before = world.graph.stats()

    # Rewind the cursor and pull the identical window again.
    state = connector.state(HRIS_WORKFORCE)
    state.watermarks = {}
    connector.store.put(state)
    second = connector.pull(HRIS_WORKFORCE)

    assert second["outcome"] == "delivered"
    assert all(r.get("replayed") for c in second["chunks"] for r in c["runs"])
    assert world.graph.stats() == before


def test_the_untouched_mock_world_is_still_there(client, world):
    """The estate lands *beside* the seeded world, not over it."""
    from app.payables import seed_payables_if_empty
    from app.workforce import seed_workforce_if_empty

    seed_payables_if_empty(world.payables, sync=world.sync)
    seed_workforce_if_empty(world.sync, world.graph)
    seeded = world.graph.stats()["by_type"]["Employee"]

    build(deliver=door(client)).pull(HRIS_WORKFORCE)
    after = world.graph.stats()["by_type"]["Employee"]
    assert after == seeded + 8
    assert world.graph.get(object_id("Employee", "rowan@customer.example"))
    assert world.graph.check_invariants() == []


# -- registration and the API -------------------------------------------------


def test_the_registry_holds_one_feed_at_stage_one():
    assert [f.feed for f in FEEDS] == [HRIS_WORKFORCE]
    assert [f.app for f in FEEDS] == ["hris"]
    assert FEEDS[0].resources == ("departments", "jobs", "employees")


def test_the_mapping_declares_the_pull_contract():
    from app.mappings import MAPPINGS, validate_mappings

    mapping = next(m for m in MAPPINGS if m.source == HRIS_WORKFORCE)
    assert mapping.transport == "pull"
    assert mapping.strategy == "bind" and mapping.lands_as == ""
    assert mapping.precedence_source == "hris"
    assert "watermark" in mapping.freshness
    # Risk decision 2: every dereference the FeedSpec makes is declared here.
    assert "managerId" in mapping.notes
    assert validate_mappings() == []


def test_registration_reports_state_without_a_pull():
    connector = build()
    view = connector.registration()
    assert view["registered"] is True
    assert view["namespace"] == "naviant"
    assert view["pull_interval_seconds"] == 0
    feed = next(f for f in view["feeds"] if f["feed"] == HRIS_WORKFORCE)
    assert feed["active"] is True
    assert feed["state"]["watermarks"] == {}

    connector.pull(HRIS_WORKFORCE)
    feed = next(f for f in connector.registration()["feeds"]
                if f["feed"] == HRIS_WORKFORCE)
    assert feed["state"]["watermarks"]["employees"]
    assert feed["state"]["last_outcome"] == "delivered"


def test_an_unregistered_connector_is_inert():
    """No ESTATE_PROJECT is the default everywhere but a dev box, so it has to
    read as "nothing to do" rather than as an error."""
    connector = EstateConnector(InMemoryConnectorStateStore(), project="")
    assert connector.registered is False
    assert connector.registration()["registered"] is False
    assert connector.start_interval_loop() is False
    with pytest.raises(EstateError):
        connector.pull(HRIS_WORKFORCE)


def test_the_interval_loop_is_off_unless_asked():
    assert build().start_interval_loop() is False
    assert build(interval_seconds=60).interval_seconds == 60


def test_the_api_reports_an_unregistered_estate(client):
    body = client.get("/api/v1/estate").json()
    assert body["registered"] is False
    assert client.post(
        f"/api/v1/estate/feeds/{HRIS_WORKFORCE}/sync-now"
    ).status_code == 400
    assert client.post("/api/v1/estate/pull").json()["feeds"] == []


def test_sync_now_pulls_one_feed_through_the_api(client, world, monkeypatch):
    import app.deps as deps

    connector = build(deliver=door(client))
    monkeypatch.setattr(deps, "_estate_connector", connector)

    body = client.post(
        f"/api/v1/estate/feeds/{HRIS_WORKFORCE}/sync-now"
    ).json()
    assert body["outcome"] == "delivered"
    assert body["pulled"]["employees"] == 10
    assert client.get("/api/v1/estate").json()["feeds"][0]["state"][
        "last_outcome"
    ] == "delivered"
    assert world.graph.stats()["by_type"]["Employee"] == 8


def test_sync_now_surfaces_an_estate_outage_as_a_bad_gateway(
    client, world, monkeypatch
):
    import app.deps as deps

    connector = build(fx.FakeEstate(fails={("hris", "employees")}))
    monkeypatch.setattr(deps, "_estate_connector", connector)
    response = client.post(f"/api/v1/estate/feeds/{HRIS_WORKFORCE}/sync-now")
    assert response.status_code == 502
    assert "employees" in response.json()["detail"]


def test_an_unknown_feed_is_a_404(client, monkeypatch):
    import app.deps as deps

    monkeypatch.setattr(deps, "_estate_connector", build())
    assert client.post(
        "/api/v1/estate/feeds/no-such-feed/sync-now"
    ).status_code == 404
