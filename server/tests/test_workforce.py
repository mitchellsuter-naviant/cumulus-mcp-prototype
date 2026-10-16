"""The workforce: an org chart that traverses and skills you can search.

These are the competency questions the people-side types were declared to
answer. Each one is unanswerable with a string field on Employee, which is
why the fields became links.
"""

import pytest

from app.graph import object_id
from tests import demo_world
from tests import world as test_world
from tests.demo_world import ASSIGNMENTS, CAPABILITIES, EMPLOYEES, TEAMS


@pytest.fixture()
def world():
    w = test_world.fresh()
    # Order matters, and it is the point: the expense seed mints employees
    # from submissions, so the HRIS delivery lands onto people who already
    # exist rather than minting a parallel set.
    demo_world.seed_expenses(w.expenses)
    demo_world.seed_payables(w.payables, sync=w.sync)
    demo_world.seed_workforce(w.sync, w.graph)
    return w


@pytest.fixture()
def client(_client, world):
    return _client


def test_the_firm_lands_once(world):
    counts = world.graph.stats()["by_type"]
    assert counts["Team"] == len(TEAMS)
    assert counts["Capability"] == len(CAPABILITIES)
    # The roster exactly, not the roster plus four: the employees the expense
    # flow already minted resolved to the same entities the HRIS names.
    assert counts["Employee"] == len(EMPLOYEES)


def test_seeding_twice_changes_nothing(world):
    before = world.graph.stats()
    assert demo_world.seed_workforce(world.sync, world.graph) == 0
    assert world.graph.stats() == before


def test_the_org_chart_is_a_traversal(world):
    """"Who is in the CEO's line" — every level, and no string matching."""
    graph = world.graph
    ceo = graph.get(object_id("Employee", demo_world.CEO))
    assert ceo.props["title"] == "Chief Executive Officer"

    def line(person, depth=1):
        found = []
        for _, report in graph.neighbors(person.id, "reports_to", "in"):
            found.append((depth, report.label))
            found.extend(line(report, depth + 1))
        return found

    chart = line(ceo)
    assert len(chart) == len(EMPLOYEES) - 1  # everyone but the CEO
    assert max(depth for depth, _ in chart) == 3
    assert (1, "Raymond Fitzgerald") in chart
    assert (3, "Connie Alberts") in chart


def test_teams_nest_and_have_leads(world):
    graph = world.graph
    finance = graph.get(object_id("Team", "finance-administration"))
    parents = [o.label for _, o in graph.neighbors(finance.id, "subteam_of", "out")]
    subteams = [o.label for _, o in graph.neighbors(finance.id, "subteam_of", "in")]
    leads = [o.label for _, o in graph.neighbors(finance.id, "led_by", "out")]
    assert parents == ["Executive"]
    assert set(subteams) == {"Information Technology"}
    assert leads == ["Raymond Fitzgerald"]
    # Every team has exactly one lead — the cardinality is enforced, so this
    # is a check on the data rather than on the model.
    for team in TEAMS:
        unit = graph.get(object_id("Team", team["slug"]))
        assert len(graph.neighbors(unit.id, "led_by", "out")) == 1


def test_who_can_do_this_is_answerable(world):
    """The Capability competency question, with the proficiency on the link."""
    graph = world.graph
    payables = graph.get(object_id("Capability", "accounts-payable"))
    assert "Three-way matching" in payables.props["description"]
    holders = {
        person.label: link.props["proficiency"]
        for link, person in graph.neighbors(payables.id, "has_capability", "in")
    }
    assert holders == {
        "Raymond Fitzgerald": "expert", "Connie Alberts": "expert",
    }
    # Evidence travels with the holding, not with the person or the skill.
    for link, _ in graph.neighbors(payables.id, "has_capability", "in"):
        assert link.props["evidenced_by"]
        assert link.method == "human"


def test_every_assignment_landed(world):
    live = [
        link for link in world.graph.store.links()
        if link.type == "has_capability" and link.live
    ]
    assert len(live) == len(ASSIGNMENTS)


def test_a_skill_is_asserted_by_people_not_observed_by_a_system(world):
    """Two origins in one Collection, and the claims say which is which."""
    graph = world.graph
    capability = graph.get(object_id("Capability", "accounts-payable"))
    assert {c.source for c in capability.claims} == {"human:people-ops"}
    person = graph.get(object_id("Employee", "john.smith@naviant.com"))
    assert any(c.source == "hris:hris-workforce" for c in person.claims)


def test_the_hris_outranks_the_flow_that_minted_the_employee(world):
    """The AE's email is claimed twice — by the flow that first saw him
    submit, and by the HRIS. Both are retained; `hris` outranks `flow`, so the
    system of record for people wins without anything being overwritten."""
    person = world.graph.get(object_id("Employee", "john.smith@naviant.com"))
    sources = [c.source for c in person.claims if c.field == "email"]
    assert "flow:expense-reimbursement" in sources
    assert "hris:hris-workforce" in sources
    assert person.props["email"] == "john.smith@naviant.com"
    # Same value from both, so it is history rather than disagreement.
    assert "email" not in person.conflicts()


def test_the_org_chart_conforms(world):
    assert world.graph.check_invariants() == []


def test_the_feed_shows_up_as_a_source(world):
    rows = {(s.source, s.kind): s for s in world.sync.sources()}
    status = rows[("hris-workforce", "workforce")]
    assert status.flow_id == "workforce-sync"
    assert status.last_status == "succeeded"
    assert status.records_landed == len(TEAMS) + len(EMPLOYEES)


def test_the_hris_mapping_binds_rather_than_reifies():
    """An exact key needs no landing record.

    One HRIS contract, not two. The registry used to carry a fictional
    `hris-workday` row beside this one describing the same subject; both it and
    the seed that delivered under that id are gone, so the mock workforce and
    the estate's HRIS feed now arrive under the one source this Mapping names.
    """
    from app.mappings import MAPPINGS

    hris = next(m for m in MAPPINGS if m.id == "hris-workforce")
    assert hris.strategy == "bind"
    assert hris.lands_as == ""
    assert hris.precedence_source == "hris"
    bound = {(b.column, b.object_type) for b in hris.identity}
    assert ("manager_email", "Employee") in bound
    assert ("team_slug", "Team") in bound
    # Both references bind twice — by the derived key and by the source's own
    # id — which is what let the connector stop fetching them.
    assert ("manager_external_id", "Employee") in bound
    assert ("department_external_id", "Team") in bound


def test_an_unresolved_reference_escalates_as_one_aggregate(world):
    """A batch feed never suspends per row: a dangling manager email is
    counted and reported once for the delivery."""
    from app.connectors.sync import SyncRequest

    outcomes = world.sync.deliver(
        SyncRequest.model_validate({
            "source": "hris-workforce",
            "employees": [{
                "external_id": "WD-9999",
                "email": "ghost@naviant.com",
                "name": "Ghost Contractor",
                "manager_email": "nobody@naviant.com",
                "team_slug": "no-such-team",
            }],
        }),
        actor="system:test",
    )
    out = outcomes[0]
    assert out["status"] == "succeeded"
    assert out["unresolved"] == 2  # the manager and the team
    assert out["employees"] == 1  # the person still landed
