"""The document library and the employment ledger: two feeds that hold back.

The properties worth pinning here are all negative ones, which is unusual and
is the point. The DMS feed must land a tree and a set of pointers and must
**not** fetch a byte; the ledger feed must keep every row and must **not**
touch the people those rows are about. A feed that quietly did more would pass
a test written around what it produced, so these are written around what it
refuses.

Fixtures are recorded from the live `naviant` estate — including the scratch
folders somebody made by hand before the namespace was seeded, which carry no
`updatedAt` at all, and the pair of invoices filed twice under one filename.
"""

import pytest

from app.graph import object_id
from app.connectors.sync import DMS_FLOW_ID, EMPLOYMENT_EVENT_FLOW_ID, SyncRequest
from tests import estate_fixtures as fx
from tests import world as test_world


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


class _Access:
    """The connector's estate handle, with the network taken out.

    `url` is the half the DMS build needs and it is pure string work, so the
    fixture can answer it exactly as the connector would. `__call__` raises:
    this feed declares no dereference, and a build that grew one would fail
    here rather than silently costing a GET per document.
    """

    base = "https://cumulus-demo-mitchell-suter-dms.web.app/api/naviant/v1"

    def __init__(self) -> None:
        self.fetched: list[tuple[str, str]] = []

    def __call__(self, resource: str, record_id: str) -> dict:
        self.fetched.append((resource, record_id))
        raise AssertionError(
            f"the dms feed dereferenced {resource}/{record_id}; it declares none"
        )

    def url(self, resource: str, record_id: str, suffix: str = "") -> str:
        return f"{self.base}/{resource}/{record_id}{suffix}"


def _dms_request(**overrides) -> SyncRequest:
    """The library, through the connector's own build function.

    Not a hand-written SyncRequest: the renames the flow depends on
    (`parentId` → `parent_external_id`, `latestVersion` → the version pinned
    into `storage_ref`) are exercised rather than assumed.
    """
    from app.estate import _build_dms_library

    rows = {"folders": fx.FOLDERS, "documents": fx.DMS_DOCUMENTS}
    rows.update(overrides)
    return _build_dms_library(rows, _Access())


def _events_request(**overrides) -> SyncRequest:
    from app.estate import _build_hris_employment_events

    rows = {"employmentEvents": fx.EMPLOYMENT_EVENTS}
    rows.update(overrides)
    return _build_hris_employment_events(rows, _Access())


class _HrisAccess(_Access):
    """The HRIS handle, which does still answer — for the two references no
    column carries by id: a department's parent and its head."""

    base = "https://cumulus-demo-mitchell-suter-hris.web.app/api/naviant/v1"

    def __call__(self, resource: str, record_id: str) -> dict:
        self.fetched.append((resource, record_id))
        for row in fx.RESOURCES.get(("hris", resource), []):
            if row.get("id") == record_id:
                return row
        off = fx.OFF_PAGE.get(("hris", resource))
        return off if off is not None and off.get("id") == record_id else {}


def _workforce_request(access: _Access | None = None, **overrides):
    from app.estate import _build_hris_workforce

    rows = {
        "departments": fx.DEPARTMENTS,
        "jobs": fx.JOBS,
        "employees": fx.EMPLOYEES,
    }
    rows.update(overrides)
    return _build_hris_workforce(rows, access or _HrisAccess())


def _deliver(world, request: SyncRequest, key: str = "") -> dict:
    outcomes = world.sync.deliver(
        request.model_copy(update={"idempotency_key": key}),
        actor="system:test",
    )
    assert len(outcomes) == 1
    return outcomes[0]


def _folder(world, path: str):
    return world.graph.get(object_id("Folder", f"dms-library:{path}"))


def _document(world, external_id: str):
    return world.graph.get(object_id("Document", f"dms-library:{external_id}"))


@pytest.fixture()
def filed(world):
    out = _deliver(world, _dms_request())
    assert out["status"] == "succeeded", out
    return out


# ---------------------------------------------------------------------------
# The tree
# ---------------------------------------------------------------------------


def test_the_tree_lands_as_folders_keyed_by_their_path(world, filed):
    """A path and not a name, because a name is not unique in a tree.

    `Customers/Northwind Traders/Proposals` and a hypothetical
    `Internal/Proposals` are two places, and a Folder keyed by `Proposals`
    would have made them one — which is the failure the type exists to avoid.
    """
    assert filed["folders"] == len(fx.FOLDERS)
    assert filed["unplaced"] == 0

    proposals = _folder(world, "Customers/Northwind Traders/Proposals")
    assert proposals is not None
    assert proposals.props["name"] == "Proposals"
    assert proposals.props["path"] == "Customers/Northwind Traders/Proposals"
    assert proposals.props["depth"] == 2
    assert proposals.props["source"] == "dms-library"
    assert proposals.props["external_id"] == "e3463e01a5d7"
    # The document system owns the tree, so the claim carries its authority.
    assert {c.source for c in proposals.claims} == {"dms:dms-library"}


def test_nesting_is_an_edge_and_the_root_has_none(world, filed):
    assert filed["subfolder_of"] == sum(
        1 for f in fx.FOLDERS if f["parentId"]
    )

    proposals = _folder(world, "Customers/Northwind Traders/Proposals")
    parents = [
        o.label for _, o in world.graph.neighbors(proposals.id, "subfolder_of", "out")
    ]
    assert parents == ["Northwind Traders"]

    customers = _folder(world, "Customers")
    assert world.graph.neighbors(customers.id, "subfolder_of", "out") == []
    children = [
        o.label for _, o in world.graph.neighbors(customers.id, "subfolder_of", "in")
    ]
    assert children == ["Northwind Traders"]
    assert world.graph.check_invariants() == []


def test_a_folder_whose_parent_nobody_holds_is_counted_not_rooted(world):
    """The honest failure. Filing an orphan at the root would put a document
    six levels down at the top of the tree, and nothing downstream could tell
    that from a document that really is at the top."""
    out = _deliver(world, _dms_request(folders=[
        {"id": "orphan-1", "name": "Executed", "parentId": "never-pulled",
         "createdAt": "2026-08-20T00:00:00+00:00",
         "updatedAt": "2026-08-20T00:00:00+00:00"},
    ]))
    assert out["folders"] == 0
    assert out["unplaced"] == 1
    assert out["subfolder_of"] == 0
    assert _folder(world, "Executed") is None


def test_a_delta_places_a_folder_against_the_tree_already_in_the_graph(
    world, filed
):
    """The case the whole path-walk exists for: one renamed subfolder arrives
    alone, and its parent is in the graph rather than in the batch."""
    out = _deliver(world, _dms_request(folders=[
        {"id": "e3463e01a5d7", "name": "Proposals (2026)",
         "parentId": "b6a714547ead",
         "createdAt": "2026-08-17T01:27:04.646644+00:00",
         "updatedAt": "2026-08-31T09:00:00+00:00"},
    ], documents=[]), key="delta-1")
    assert out["folders"] == 1 and out["unplaced"] == 0

    renamed = _folder(world, "Customers/Northwind Traders/Proposals (2026)")
    assert renamed is not None
    assert renamed.props["depth"] == 2
    # A rename is a new place in the tree, so the old key still resolves to
    # the old row — nothing was silently moved, and B5 gets to decide whether
    # those two folders are one.
    assert _folder(world, "Customers/Northwind Traders/Proposals") is not None


# ---------------------------------------------------------------------------
# The documents
# ---------------------------------------------------------------------------


def test_documents_are_referenced_in_place_and_never_read(world, filed):
    """Custody, spelled out. The row says where the file is and which revision
    that address resolves to, and says nothing about what is in it."""
    assert filed["documents"] == len(fx.DMS_DOCUMENTS)

    offer = _document(world, "82e6b9be140a")
    assert offer is not None
    assert offer.props["filename"] == "Andre Mbeki offer letter 2026-09-05.pdf"
    assert offer.props["media_type"] == "text/plain"
    assert offer.props["byte_size"] == 195
    assert offer.props["collection_id"] == "document-library"
    assert offer.props["latest_version"] == 2
    # Verbatim, and version-pinned: `/content` alone would answer with
    # whatever is latest at read time, which is a different fact.
    assert offer.props["storage_ref"] == (
        "https://cumulus-demo-mitchell-suter-dms.web.app/api/naviant/v1"
        "/documents/82e6b9be140a/content?version=2"
    )
    # The absence that matters: a hash is what a file somebody read has.
    assert "sha256" not in offer.props


def test_a_file_is_filed_where_the_customer_filed_it(world, filed):
    assert filed["filed_under"] == len(fx.DMS_DOCUMENTS)
    assert filed["folder_unresolved"] == 0

    invoice = _document(world, "8467902a9c82")
    folders = [
        o.props["path"]
        for _, o in world.graph.neighbors(invoice.id, "filed_under", "out")
    ]
    assert folders == ["Accounts Payable/GSF/2026"]

    library = _folder(world, "Accounts Payable/GSF/2026")
    filed_here = {
        o.props["filename"]
        for _, o in world.graph.neighbors(library.id, "filed_under", "in")
    }
    # Two of them share a filename and differ by id. The mirror keeps both:
    # the customer filed the same invoice twice and that is a fact about the
    # customer, not a duplicate for us to collapse.
    assert filed_here == {
        "GSF invoice 2026-09-02.pdf", "GSF invoice 2026-09-12.pdf",
    }
    ids = {
        o.natural_key
        for _, o in world.graph.neighbors(library.id, "filed_under", "in")
    }
    assert len(ids) == 3


def test_a_document_in_a_folder_nobody_holds_still_lands(world):
    """The reference fails; the file does not stop existing."""
    out = _deliver(world, _dms_request(folders=[], documents=[
        {"id": "stray-1", "name": "Scanned batch 44.pdf",
         "folderId": "never-pulled", "contentType": "application/pdf",
         "size": 1024, "latestVersion": 1,
         "createdAt": "2026-08-20T00:00:00+00:00",
         "updatedAt": "2026-08-20T00:00:00+00:00", "versions": []},
    ]))
    assert out["documents"] == 1
    assert out["folder_unresolved"] == 1
    assert out["filed_under"] == 0
    assert _document(world, "stray-1") is not None


def test_a_file_at_the_root_is_counted_rather_than_left_as_arithmetic(world):
    """The live library holds nine of these — hand-uploaded outside anyone's
    tree. The root is a real place, and it has no Folder object, so the row
    has no `filed_under` edge and says so on its own line. The difference
    between `documents` and `filed_under` reading as a silent shortfall is how
    a fact about the customer's filing becomes a suspected bug in the mirror.
    """
    out = _deliver(world, _dms_request(folders=[], documents=[
        {"id": "loose-1", "name": "beautiful_image.jpg", "folderId": None,
         "contentType": "image/jpeg", "size": 40518, "latestVersion": 1,
         "createdAt": "2026-08-15T16:14:20.000000+00:00",
         "updatedAt": "2026-08-15T16:14:20.000000+00:00", "versions": []},
    ]))
    assert out["documents"] == 1
    assert out["at_root"] == 1
    assert out["filed_under"] == 0
    assert out["folder_unresolved"] == 0
    loose = _document(world, "loose-1")
    assert loose is not None
    assert world.graph.neighbors(loose.id, "filed_under", "out") == []


def test_the_connector_never_fetches_a_byte(world):
    """The custody rule where it could most easily have been broken.

    `_Access.__call__` raises, so a build that dereferenced anything — or, far
    worse, pulled `/content` to hash it — fails here. What the build may do is
    ask for an address, which is string work.
    """
    access = _Access()
    from app.estate import _build_dms_library

    request = _build_dms_library(
        {"folders": fx.FOLDERS, "documents": fx.DMS_DOCUMENTS}, access
    )
    assert access.fetched == []
    assert all(d.storage_ref.endswith("/content?version=1")
               or "/content?version=" in d.storage_ref
               for d in request.documents)


def test_a_redelivery_changes_nothing(world, filed):
    before = world.graph.stats()
    again = _deliver(world, _dms_request(), key="repeat-1")
    assert again["status"] == "succeeded"
    assert world.graph.stats() == before


def test_the_feed_reports_what_it_put_in_the_graph(world, filed):
    """`landed` is the Sources lens's word, and a bind feed has to say it.

    There is no landing step here — the rows become Folders and Documents
    directly — so nothing counts them unless the body does, and the operator's
    row read a permanent zero beside three sibling feeds that report honestly.
    """
    rows = {(s.source, s.kind): s for s in world.sync.sources()}
    row = rows[("dms-library", "documents")]
    assert row.flow_id == DMS_FLOW_ID
    assert row.last_status == "succeeded"
    assert row.records_landed == len(fx.FOLDERS) + len(fx.DMS_DOCUMENTS)


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------


def test_the_ledger_lands_as_source_records_and_nothing_else(world):
    """Landing-only, asserted as an absence: the flow that receives a worker's
    whole employment history writes not one claim about the worker."""
    people_before = world.graph.find("Employee")
    out = _deliver(world, _events_request())

    assert out["status"] == "succeeded"
    assert out["kind"] == "employment-events"
    assert out["landed"] == len(fx.EMPLOYMENT_EVENTS)
    assert world.graph.find("Employee") == people_before

    landed = world.graph.find(
        "SourceRecord", prop_equals={"record_kind": "employment-event"}
    )
    assert len(landed) == len(fx.EMPLOYMENT_EVENTS)
    assert {c.source for row in landed for c in row.claims} == {
        "hris:hris-employment-events"
    }


def test_the_row_is_kept_whole_including_what_it_used_to_be(world):
    """The reason this feed reifies at all. A promotion's previous salary is
    not on any type, and a landing that named four columns would have decided
    nobody may ever ask about the other thirteen."""
    import json

    _deliver(world, _events_request())
    promotion = world.graph.get(
        object_id("SourceRecord", "hris-employment-events:EVT-1017")
    )
    assert promotion is not None
    payload = json.loads(promotion.props["payload"])["payload"]
    assert payload["eventType"] == "Promotion"
    assert payload["annualSalary"] == 152000.0
    assert payload["previousAnnualSalary"] == 128000
    assert payload["previousJobId"] == "JOB-1011"


def test_the_ledger_is_its_own_flow_and_its_own_run(world):
    """One delivery, one run, on the ledger's flow — not folded into the
    workforce sync, whose cursor moves on a different clock."""
    out = _deliver(world, _events_request(), key="ledger-1")
    assert out["flow_id"] == EMPLOYMENT_EVENT_FLOW_ID
    run = world.engine.get(out["run_id"])
    assert [s.step_id for s in run.steps] == ["t1", "s1", "e1"]

    replay = _deliver(world, _events_request(), key="ledger-1")
    assert replay["replayed"] is True


def test_a_redelivered_ledger_row_is_unchanged_not_relanded(world):
    _deliver(world, _events_request())
    second = _deliver(world, _events_request(), key="ledger-2")
    assert second["landed"] == 0
    assert second["unchanged"] == len(fx.EMPLOYMENT_EVENTS)


# ---------------------------------------------------------------------------
# Workforce hardening
# ---------------------------------------------------------------------------


def test_a_terminated_worker_lands_saying_so(world):
    """The column whose absence was a lie rather than a gap.

    Alan Whitcomb resigned in July and the HRIS says `Terminated`. Before this
    column the feed delivered him indistinguishable from everyone still here.
    """
    request = _workforce_request()
    alan = next(e for e in request.employees
                if e.email == "alan.whitcomb@naviant.com")
    assert alan.status == "Terminated"
    assert alan.employment_type == "Full-time"

    out = _deliver(world, request)
    assert out["status"] == "succeeded", out

    person = world.graph.get(
        object_id("Employee", "alan.whitcomb@naviant.com")
    )
    assert person.props["status"] == "terminated"
    assert person.props["employment_type"] == "full-time"
    assert person.props["external_id"] == "EMP-1016"

    # And the ones still here say that, rather than saying nothing.
    nina = world.graph.get(object_id("Employee", "nina.alvarez@naviant.com"))
    assert nina.props["status"] == "active"


def test_a_source_that_carries_no_status_claims_none(world):
    """An empty claim is not the same fact as a missing one — and `hris`
    outranks most things, so a blank asserted here would win against a system
    that knows."""
    out = _deliver(world, SyncRequest(
        source="hris-workforce",
        employees=[{
            "external_id": "WD-1", "email": "quiet@naviant.com",
            "name": "Quiet Feed",
        }],
    ))
    assert out["status"] == "succeeded"
    person = world.graph.get(object_id("Employee", "quiet@naviant.com"))
    assert "status" not in person.props
    assert "employment_type" not in person.props


def test_a_manager_off_the_page_resolves_by_the_id_that_travelled(world):
    """What retired the connector's dereference.

    A delta of one promoted employee carries a `managerId` and no way to
    translate it — the manager's row is not in the batch. The flow resolves it
    against the person already in the graph instead of the connector spending
    a GET to re-read a key the row had already given it.
    """
    _deliver(world, _workforce_request())

    access = _HrisAccess()
    delta = _workforce_request(access, employees=[fx._worker(
        id="EMP-1010", legalFirstName="Nina", legalLastName="Alvarez",
        workEmail="nina.alvarez@naviant.com", fullName="Nina Alvarez",
        hireDate="2024-09-15", jobId="JOB-1006", departmentId="DEPT-1004",
        managerId="EMP-1003", annualSalary=99000,
        createdAt="2026-08-16T22:16:23.179831+00:00",
        updatedAt="2026-08-31T09:00:00+00:00",
    )], departments=[], jobs=[])
    person = delta.employees[0]
    # The batch could not translate either reference, so both travel raw.
    assert person.manager_email == "" and person.team_slug == ""
    assert person.manager_external_id == "EMP-1003"
    assert person.department_external_id == "DEPT-1004"
    # And it cost nothing to say so. The only round trip this delta makes is
    # the one for `jobId`, which no column carries — the manager and the
    # department are not fetched at all.
    assert access.fetched == [("jobs", "JOB-1006")]

    out = _deliver(world, delta, key="workforce-delta-1")
    assert out["status"] == "succeeded", out
    assert out["unresolved"] == 0
    assert out["reports_to"] == 1 and out["member_of"] == 1

    nina = world.graph.get(object_id("Employee", "nina.alvarez@naviant.com"))
    managers = [
        o.label for _, o in world.graph.neighbors(nina.id, "reports_to", "out")
    ]
    assert managers == ["Gregory Lindholm"]
    teams = [
        o.label for _, o in world.graph.neighbors(nina.id, "member_of", "out")
    ]
    assert teams == ["Customer Support"]


def test_a_reference_nobody_can_place_is_still_counted(world):
    """The fallback widens what resolves; it does not invent anything."""
    out = _deliver(world, SyncRequest(
        source="hris-workforce",
        employees=[{
            "external_id": "WD-9999", "email": "ghost@naviant.com",
            "name": "Ghost Contractor",
            "manager_external_id": "EMP-nobody",
            "department_external_id": "DEPT-nobody",
        }],
    ))
    assert out["unresolved"] == 2
    assert out["employees"] == 1


# ---------------------------------------------------------------------------
# The contracts
# ---------------------------------------------------------------------------


def test_both_feeds_declare_what_they_do_and_do_not_do():
    from app.mappings import MAPPINGS, validate_mappings

    assert validate_mappings() == []

    dms = next(m for m in MAPPINGS if m.source == "dms-library")
    assert dms.strategy == "bind" and dms.lands_as == ""
    assert dms.custody == "reference"
    assert dms.collection == "document-library"
    assert dms.precedence_source == "dms"
    assert "never fetched" in dms.notes

    ledger = next(m for m in MAPPINGS if m.source == "hris-employment-events")
    assert ledger.strategy == "reify" and ledger.lands_as == "SourceRecord"
    # The claim its landing-only flow makes, in the contract: it resolves
    # nothing, so it binds nothing.
    assert ledger.resolves_to == () and ledger.identity == ()
    assert ledger.precedence_source == "hris"


def test_the_dms_authority_is_its_own_bucket():
    """Without it the DMS falls through to `flow`, and a customer's own filing
    tree would have been recorded as something Cumulus guessed at."""
    from app.ontology import ONTOLOGY
    from app.connectors.sync import _authority

    assert _authority("dms-library") == "dms"
    order = ONTOLOGY.precedence
    assert order.index("office") < order.index("dms") < order.index("procurement")


def test_both_flows_have_a_map_whose_steps_are_the_ones_they_journal(world):
    """An export naming a step nobody runs is the lie this pays off."""
    from app.flows import InMemoryFlowDirectory, FlowService, seed_flows_if_empty

    flows = FlowService(InMemoryFlowDirectory())
    seed_flows_if_empty(flows)

    dms_run = world.engine.get(_deliver(world, _dms_request())["run_id"])
    ledger_run = world.engine.get(_deliver(world, _events_request())["run_id"])

    for flow_id, run in ((DMS_FLOW_ID, dms_run),
                         (EMPLOYMENT_EVENT_FLOW_ID, ledger_run)):
        declared = flows.get(flow_id)
        assert declared is not None, flow_id
        assert [s.id for s in declared.steps] == [
            s.step_id for s in run.steps
        ], flow_id
