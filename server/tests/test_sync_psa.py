"""The delivery feed: what it mirrors, what it sums, and what it refuses.

Half of these are negative properties, which is the shape this feed has. It
must land engagements and phases and must **not** mirror the tasks it reads to
place an hour; it must sum time entries and must **not** keep one; it must put
the PSA's nickname for a company into the counterparty book as a claim and must
**not** let that nickname become a second Northwind on a spelling alone. A feed
that quietly did more would pass a test written around what it produced, so
these are written around what it holds back.

Fixtures are recorded from the live `naviant` PSA — including the two clients
nobody filled a contact address in for, the finished engagement whose time
entries all carry a null `taskId` because its task list was never written, and
`Apex Mfg`, the abbreviation that is also a vendor's legal name.
"""

import pytest

from app.graph import object_id
from app.mappings import steward_queue_for
from app.connectors.sync import PSA_FLOW_ID, SyncRequest
from tests import estate_fixtures as fx
from tests import world as test_world

APEX = "psa-book:ENG-0100"
VERONA_REFRESH = "psa-book:ENG-0112"


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


class _Access:
    """The connector's estate handle, with the network taken out.

    Both halves raise: this feed declares no dereference and builds no URL, so
    a build that grew either fails here rather than silently costing a GET per
    time entry.
    """

    def __init__(self) -> None:
        self.fetched: list[tuple[str, str]] = []

    def __call__(self, resource: str, record_id: str) -> dict:
        self.fetched.append((resource, record_id))
        raise AssertionError(
            f"the psa feed dereferenced {resource}/{record_id}; it declares none"
        )

    def url(self, resource: str, record_id: str, suffix: str = "") -> str:
        raise AssertionError("the psa feed builds no estate URL")


def _psa_request(access: _Access | None = None, **overrides) -> SyncRequest:
    """The delivery book, through the connector's own build function.

    Not a hand-written SyncRequest: the renames the flow depends on
    (`projectCode`, `clientId` → `client_external_id`, `taskId` → the phase an
    hour is attributed through) are exercised rather than assumed — and so is
    the pair of column sets the build drops on the floor.
    """
    from app.estate import _build_psa_book

    rows = {
        "clients": fx.PSA_CLIENTS,
        "projects": fx.PSA_PROJECTS,
        "phases": fx.PSA_PHASES,
        "tasks": fx.PSA_TASKS,
        "timeEntries": fx.PSA_TIME_ENTRIES,
    }
    rows.update(overrides)
    return _build_psa_book(rows, access or _Access())


def _crm_request(**overrides) -> SyncRequest:
    from app.estate import _build_crm_book

    rows = {
        "accounts": fx.ACCOUNTS,
        "contacts": fx.CONTACTS,
        "opportunities": fx.OPPORTUNITIES,
    }
    rows.update(overrides)
    return _build_crm_book(rows, lambda resource, record_id: {})


def _deliver(world, request: SyncRequest, key: str = "") -> dict:
    outcomes = world.sync.deliver(
        request.model_copy(update={"idempotency_key": key}),
        actor="system:test",
    )
    assert len(outcomes) == 1
    return outcomes[0]


def _project(world, key: str):
    return world.graph.get(object_id("Project", key))


def _phases(world, project):
    return sorted(
        (o for _link, o in world.graph.neighbors(project.id, "has_phase", "out")),
        key=lambda o: o.props.get("sequence", 0),
    )


def _company(world, key: str):
    return world.graph.get(object_id("Company", key))


@pytest.fixture()
def booked(world):
    """The CRM's book first, then Delivery's — the order the estate is in.

    Both, and not the PSA alone, because every interesting property of this
    feed is about a client list arriving at a counterparty book that already
    exists. A world where the PSA speaks first would have every nickname mint
    unopposed and would test nothing.
    """
    assert _deliver(world, _crm_request())["status"] == "succeeded"
    out = _deliver(world, _psa_request())
    assert out["status"] == "succeeded", out
    return out


# ---------------------------------------------------------------------------
# The connector's build
# ---------------------------------------------------------------------------


def test_a_task_arrives_as_three_ids_and_a_time_entry_without_its_notes():
    """Custody, at the earliest point it can be held.

    The estate serves a task's name, assignee, status, due date and estimate
    and a time entry's free-text note on every row. None of them reach the
    wire, so no flow written later can land them by accident — the same line
    `_build_office_mail` draws at `bodyPreview`.
    """
    request = _psa_request()
    assert len(request.project_tasks) == len(fx.PSA_TASKS)
    task = next(t for t in request.project_tasks if t.external_id == "100")
    assert task.project_external_id == "100" and task.phase_external_id == "100"
    assert set(task.model_dump()) == {
        "external_id", "project_external_id", "phase_external_id",
    }
    # The row it came from is full of everything this shape refuses.
    source = next(t for t in fx.PSA_TASKS if t["id"] == "100")
    assert source["assignee"] and source["name"] and source["status"]

    entry = request.time_entries[0]
    assert "notes" not in entry.model_dump()
    assert any(row.get("notes") for row in fx.PSA_TIME_ENTRIES)


def test_the_build_reads_no_record_it_was_not_handed():
    """No dereference and no URL. Every reference in this family is an id the
    flow resolves against a row the same feed landed, so the connector never
    leaves the page it pulled."""
    access = _Access()
    _psa_request(access)
    assert access.fetched == []


def test_the_project_code_and_the_internal_id_both_travel():
    """The key is the code a human says; the id is what four other resources
    reference. A shape carrying one of them could not do both jobs."""
    request = _psa_request()
    apex = next(p for p in request.projects if p.project_code == "ENG-0100")
    assert apex.external_id == "100"
    assert apex.client_external_id == "101"
    assert apex.project_manager == "S. Hoffmann"
    assert apex.budget_hours == 420.0


# ---------------------------------------------------------------------------
# Clients, onto the counterparty book
# ---------------------------------------------------------------------------


def test_a_nickname_the_resolver_cannot_place_mints_and_says_what_it_was_near(
    world, booked
):
    """`Apex Mfg` against a book holding `Apex Manufacturing` scores 0.54 —
    well below the floor where a match would be asserted — so it mints, and
    that is the resolver being right rather than being lazy. What makes the
    duplicate recoverable is the claim beside it: the domain out of the
    contact's address, which is the evidence the sweep joins the two on."""
    apex = _company(world, "Apex Mfg")
    assert apex is not None
    assert apex.props["name"] == "Apex Mfg"
    assert apex.props["domain"] == "apexmfg.example.com"
    # Under the delivery system's own authority, which ranks last among the
    # systems — so where anything else holds an opinion, this one loses.
    assert {c.source for c in apex.claims} == {"psa:psa-book"}
    # And the company it is a nickname for is untouched: two rows, and a
    # proposal is somebody else's job.
    known = _company(world, "Apex Manufacturing")
    assert known is not None and known.id != apex.id


def test_a_client_in_the_review_band_asserts_nothing_and_asks_a_steward(
    world, booked
):
    """`Northwind` sits at 0.73 against `Northwind Traders` — close enough to
    be a question and not close enough to be an answer. Nothing is minted and
    nothing is linked: the row lands, a Task is raised in the queue the
    Mapping names, and the projects that name that client stay unbound."""
    assert booked["awaiting_steward"] == 3
    assert _company(world, "Northwind") is None
    assert _company(world, "Summit Health") is None
    assert _company(world, "Blue Harbor") is None

    tasks = [t for t in world.tasks.list() if t.kind == "match_review"]
    assert {t.queue for t in tasks} == {steward_queue_for("psa-book")}
    titles = " ".join(t.title for t in tasks)
    for name in ("Northwind", "Summit Health", "Blue Harbor"):
        assert name in titles
    # The projects for a client nobody has ruled on are counted, not guessed
    # at — four of the eighteen, and they exist without a counterparty.
    assert booked["client_unresolved"] == 4
    assert booked["delivered_for"] == booked["projects"] - 4


def test_every_landed_client_carries_a_reversible_identity_link(world, booked):
    """The same edge the CRM and the ERP hang off a landed row, from a third
    system — which is the whole point of one counterparty book."""
    apex = _company(world, "Apex Mfg")
    rows = [o for _l, o in world.graph.neighbors(
        apex.id, "company_identified_as", "out"
    )]
    assert [o.natural_key for o in rows] == ["psa-book:101"]
    assert rows[0].props["record_kind"] == "company"
    link = next(
        link for link in world.graph.store.links()
        if link.type == "company_identified_as" and link.to_id == rows[0].id
    )
    assert link.method == "declared" and link.confidence == 1.0


def test_a_client_with_no_contact_address_carries_no_domain(world, booked):
    """Two of the thirteen have nobody's address on them. The claim is absent
    rather than empty: a domain that is "" would compare equal to another
    empty one, and the sweep would read two blanks as a shared identifier."""
    prairie = _company(world, "Prairie Records")
    assert prairie is not None
    assert "domain" not in prairie.props


def test_a_redelivery_re_resolves_by_the_id_the_source_already_gave_us(world):
    """The second pull of the same page links every client by `source-link`
    rather than re-scoring its spelling — and lands nothing, because the rows
    are byte-identical."""
    _deliver(world, _crm_request())
    first = _deliver(world, _psa_request(), key="one")
    assert (first["created"], first["linked"]) == (10, 0)

    second = _deliver(world, _psa_request(), key="two")
    assert second["unchanged"] == len(fx.PSA_CLIENTS)
    assert second["landed"] == 0
    assert (second["created"], second["linked"]) == (0, 10)
    assert second["awaiting_steward"] == 3
    assert len(world.graph.find("Company")) == len(
        [c for c in world.graph.find("Company")]
    )


def test_an_identical_delivery_replays_the_same_run(world, booked):
    replay = _deliver(world, _psa_request(), key="same")
    again = _deliver(world, _psa_request(), key="same")
    assert again["replayed"] is True
    assert again["run_id"] == replay["run_id"]


# ---------------------------------------------------------------------------
# The delivery book
# ---------------------------------------------------------------------------


def test_projects_land_keyed_by_the_code_the_customer_says(world, booked):
    assert booked["projects"] == len(fx.PSA_PROJECTS)
    apex = _project(world, APEX)
    assert apex is not None
    assert apex.props["project_code"] == "ENG-0100"
    assert apex.props["external_id"] == "100"
    assert apex.props["source"] == "psa-book"
    # The PSA's vocabulary as the declared enum — the mapping decision that
    # lives in the flow and not in the connector.
    assert apex.props["status"] == "active"
    assert _project(world, "psa-book:ENG-0105").props["status"] == "on-hold"
    assert apex.props["budget_hours"] == 420.0
    assert apex.props["project_manager"] == "S. Hoffmann"
    assert world.graph.check_invariants() == []


def test_a_project_is_bound_to_the_counterparty_it_is_delivered_for(
    world, booked
):
    apex = _project(world, APEX)
    companies = [
        o.label for _l, o in world.graph.neighbors(apex.id, "delivered_for", "out")
    ]
    assert companies == ["Apex Mfg"]
    assert apex.props["company_name"] == "Apex Mfg"


def test_phases_are_parts_of_their_project_and_keyed_through_it(world, booked):
    assert booked["phases"] == len(fx.PSA_PHASES)
    assert booked["phase_unplaced"] == 0

    apex = _project(world, APEX)
    stages = _phases(world, apex)
    assert [o.label for o in stages] == [
        "Discovery", "Configuration", "Install", "Training", "Go-live",
    ]
    # The weak-entity rule, and the reason it is enforced rather than
    # followed: `Discovery` means nothing outside the engagement that has one.
    assert stages[0].natural_key == f"{apex.id}#100"
    assert stages[0].props["project_code"] == "ENG-0100"
    assert stages[2].props["status"] == "in-progress"


def test_a_phase_whose_project_nobody_holds_is_counted_not_orphaned(world):
    """Landing it would produce a part with no owner, which the TBox sweep
    refuses anyway — so it is counted instead, and the batch survives."""
    out = _deliver(world, _psa_request(
        clients=[], projects=[], tasks=[], timeEntries=[],
        phases=[{"id": "900", "projectId": "never-pulled", "name": "Discovery",
                 "sequence": 1, "status": "Not Started",
                 "targetDate": "2026-09-01",
                 "createdAt": "2026-08-30T00:00:00+00:00",
                 "updatedAt": "2026-08-30T00:00:00+00:00"}],
    ))
    assert out["phases"] == 0 and out["phase_unplaced"] == 1
    assert world.graph.check_invariants() == []


def test_a_project_row_that_names_no_engagement_is_skipped(world):
    """The key is the code, or the id where a source issues none. Landing a
    row with neither would collide every such row into one ghost project."""
    out = _deliver(world, _psa_request(
        clients=[], phases=[], tasks=[], timeEntries=[],
        projects=[{"id": None, "projectCode": None, "clientId": "101",
                   "name": "A project from nowhere", "status": "Planned",
                   "projectManager": None, "startDate": None,
                   "targetEndDate": None, "budgetHours": None,
                   "actualHours": 0.0,
                   "createdAt": "2026-08-30T00:00:00+00:00",
                   "updatedAt": "2026-08-30T00:00:00+00:00"}],
    ))
    assert out["projects"] == 0 and out["project_unkeyed"] == 1
    assert world.graph.find("Project") == []


def test_a_delta_places_a_phase_against_a_project_already_in_the_graph(
    world, booked
):
    """One phase status flips and arrives alone. Its project is in the graph
    rather than in the batch, which is the case the source-scoped lookup
    exists for."""
    out = _deliver(world, _psa_request(
        clients=[], projects=[], tasks=[], timeEntries=[],
        phases=[{"id": "102", "projectId": "100", "name": "Install",
                 "sequence": 3, "status": "Complete",
                 "targetDate": "2026-09-03",
                 "createdAt": "2026-08-30T00:00:00+00:00",
                 "updatedAt": "2026-08-31T09:00:00+00:00"}],
    ), key="delta-1")
    assert out["phases"] == 1 and out["phase_unplaced"] == 0

    apex = _project(world, APEX)
    install = next(o for o in _phases(world, apex) if o.label == "Install")
    assert install.props["status"] == "complete"
    # And no second phase object: the key is the project's plus the PSA's id.
    assert len(_phases(world, apex)) == 5


# ---------------------------------------------------------------------------
# The hours
# ---------------------------------------------------------------------------


def test_hours_reach_a_phase_through_the_task_an_entry_names(world, booked):
    """The one thing task rows are carried for, and the proof they earn the
    ride: without them every hour would land on the project and no phase would
    know what it cost."""
    assert booked["time_entries"] == len(fx.PSA_TIME_ENTRIES)
    apex = _project(world, APEX)
    stages = _phases(world, apex)
    logged = {o.label: o.props["logged_hours"] for o in stages}
    assert sum(logged.values()) == pytest.approx(261.0)
    assert logged["Discovery"] == pytest.approx(55.5)
    # Billable is a strict subset — the PSA marks roughly one day in eight
    # non-billable and the flag is read rather than assumed.
    for stage in stages:
        assert stage.props["billable_hours"] <= stage.props["logged_hours"]
    assert apex.props["billable_hours"] == pytest.approx(207.0)


def test_the_source_owns_the_project_total_and_we_own_the_split(world, booked):
    """`actual_hours` is the PSA's own running sum, claimed as it arrived: it
    is whole even on a delta that carried one window of the entries behind it.
    Everything computed here is explicitly the window."""
    apex = _project(world, APEX)
    assert apex.props["actual_hours"] == pytest.approx(261.0)
    source = next(p for p in fx.PSA_PROJECTS if p["id"] == "100")
    assert apex.props["actual_hours"] == source["actualHours"]


def test_time_booked_past_every_phase_is_counted_rather_than_spread(
    world, booked
):
    """The finished Verona engagement ran before anybody wrote a task list, so
    all 21 of its entries carry a null `taskId`. Spreading them evenly over
    its phases would invent an attribution nobody made; the run says how many
    hours could not be placed and the phases stay silent."""
    assert booked["hours_unattributed"] == pytest.approx(141.0)
    verona = _project(world, VERONA_REFRESH)
    assert verona.props["actual_hours"] == pytest.approx(141.0)
    assert verona.props["billable_hours"] == pytest.approx(123.5)
    for stage in _phases(world, verona):
        assert "logged_hours" not in stage.props


def test_a_delivery_with_no_hours_for_a_project_leaves_its_totals_alone(world):
    """A delta about one renamed project must not overwrite a billable total
    with the zero it happens to have seen."""
    _deliver(world, _crm_request())
    _deliver(world, _psa_request(), key="full")
    before = _project(world, APEX).props["billable_hours"]

    _deliver(world, _psa_request(
        clients=[], phases=[], tasks=[], timeEntries=[],
        projects=[p for p in fx.PSA_PROJECTS if p["id"] == "100"],
    ), key="rename")
    assert _project(world, APEX).props["billable_hours"] == before


def test_people_land_as_the_strings_the_psa_holds_and_nothing_more(
    world, booked
):
    """`Curt Boyd` is Curtis Boyd in the HRIS and nothing in either system
    says so. The names land as observations awaiting promotion — no Employee
    link, no Person minted, no fuzzy join asserted by a batch feed."""
    apex = _project(world, APEX)
    assert apex.props["contributors"] == ["Curt Boyd", "Donna H.", "M. Ortega"]
    assert apex.props["project_manager"] == "S. Hoffmann"
    # The CRM's contacts are Persons — they arrived with addresses. Nobody
    # from the PSA is, because a display name is not an identity: no Person
    # carries a `psa` claim and no landed PSA row is a person's.
    assert not [
        person for person in world.graph.find("Person")
        if any(c.source.startswith("psa:") for c in person.claims)
    ]
    assert not [
        o for o in world.graph.find("SourceRecord")
        if o.props.get("source") == "psa-book"
        and o.props.get("record_kind") != "company"
    ]


def test_no_time_entry_becomes_an_object(world, booked):
    """Sixty-two rows in, and the graph holds the sums and not one of them.
    This is the `bind` side of the contract, and the reason it is drawn here:
    the questions asked of a timesheet are sums, and a million readings must
    not become a million objects."""
    landed = {o.type for o in world.graph.store.objects()}
    assert "TimeEntry" not in landed
    assert not [
        o for o in world.graph.find("SourceRecord")
        if o.props.get("record_kind") not in ("company", "person")
    ]
    # Only the clients reified. Thirteen rows, one per client, and nothing for
    # the 170-odd delivery rows beside them.
    psa_rows = [
        o for o in world.graph.find("SourceRecord")
        if o.props.get("source") == "psa-book"
    ]
    assert len(psa_rows) == len(fx.PSA_CLIENTS)


# ---------------------------------------------------------------------------
# The run, and the contract it is declared under
# ---------------------------------------------------------------------------


def test_the_run_is_journalled_as_the_definition_declares_it(world, booked):
    from app.flows import _seed_definitions

    run = world.engine.get(booked["run_id"])
    assert run.flow_id == PSA_FLOW_ID
    journalled = [step.step_id for step in run.steps]
    assert journalled == ["t1", "x1", "s1", "z1", "a1", "a2", "e1"]

    declared = next(d for d in _seed_definitions() if d.id == PSA_FLOW_ID)
    assert [step.id for step in declared.steps] == journalled
    assert [step.name for step in declared.steps][1:6] == [
        step.name for step in run.steps
    ][1:6]


def test_every_write_goes_through_the_gateway(world, booked):
    """Two guarded writes, one per apply step, and the landing under its own
    Action — the same regime every other feed commits under."""
    actions = [
        e.action for e in world.gateway.ledger.list()
        if e.run_id == booked["run_id"]
    ]
    assert actions.count("land-source-records") == 1
    assert actions.count("resolve-identities") == 1
    assert actions.count("apply-source-records") == 1
    assert all(
        e.outcome == "committed" for e in world.gateway.ledger.list()
        if e.run_id == booked["run_id"]
    )


def test_the_governed_thresholds_are_read_rather_than_assumed(world, booked):
    run = world.engine.get(booked["run_id"])
    policy = next(s for s in run.steps if s.step_id == "x1")
    assert policy.output["source_ref"].startswith(
        "knowledge:entity-resolution-policy"
    )
    assert "knowledge:entity-resolution-policy" in " ".join(run.refs)
