"""The entity layer: identity, retained claims, reversible links, traversal.

These are the properties the two flows rest on. Deterministic ids are what make
a replayed step safe; retained claims are what make two disagreeing systems a
normal state; reversible links are what make an identity assertion something a
steward can withdraw without erasing that it was made.
"""

import pytest

from app.graph import (
    Claim,
    GraphService,
    InMemoryGraphStore,
    normalize_key,
    object_id,
    resolve_props,
)
from app.ontology import ONTOLOGY
from tests import world as test_world


@pytest.fixture()
def graph():
    return GraphService(InMemoryGraphStore())


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


# ---- identity --------------------------------------------------------------


@pytest.mark.parametrize(
    "left,right",
    [
        ("Acme Industrial Inc.", "ACME INDUSTRIAL, INCORPORATED"),
        ("Acme Industrial Inc.", "acme industrial"),
        ("Northwind Traders LLC", "Northwind Traders, L.L.C."),
    ],
)
def test_legal_form_and_punctuation_do_not_change_identity(left, right):
    assert normalize_key(left) == normalize_key(right)
    assert object_id("Vendor", left) == object_id("Vendor", right)


def test_word_order_does_change_identity():
    # Normalization is not matching: reordering words is a *match* question,
    # and matching needs a confidence, so it does not happen here.
    assert object_id("Vendor", "Acme Industrial") != object_id(
        "Vendor", "Industrial Acme"
    )


def test_upserting_the_same_entity_twice_mints_one_object(graph):
    first = graph.upsert(
        "Vendor", "Acme Industrial Inc.",
        [Claim(field="name", value="Acme Industrial Inc.", source="erp:vendors")],
    )
    second = graph.upsert(
        "Vendor", "ACME INDUSTRIAL, INCORPORATED",
        [Claim(field="name", value="Acme Industrial Inc.", source="erp:vendors")],
    )
    assert first.id == second.id
    assert len(graph.find("Vendor")) == 1
    # An identical claim is not appended twice, which is what makes a replayed
    # step and a re-delivered sync row both harmless.
    assert len(second.claims) == 1


# ---- claims and resolution -------------------------------------------------


def test_precedence_resolves_and_retains_both_claims(graph):
    vendor = graph.upsert(
        "Vendor", "Acme Industrial Inc.",
        [
            Claim(field="payment_terms", value="NET30", source="erp:vendors"),
            Claim(field="payment_terms", value="NET60", source="procurement-coupa"),
        ],
    )
    # ERP outranks procurement in the ontology's order.
    assert vendor.props["payment_terms"] == "NET30"
    conflicts = vendor.conflicts()
    assert set(conflicts) == {"payment_terms"}
    assert {c.value for c in conflicts["payment_terms"]} == {"NET30", "NET60"}


def test_a_human_claim_outranks_every_system():
    claims = [
        Claim(field="name", value="From the model", source="model:gemini"),
        Claim(field="name", value="From the ERP", source="erp:vendors"),
        Claim(field="name", value="From a person", source="human:dana@example.com"),
    ]
    assert resolve_props(claims)["name"] == "From a person"


def test_recency_breaks_a_tie_within_one_authority():
    claims = [
        Claim(field="status", value="active", source="erp:vendors",
              at_iso="2026-01-01T00:00:00+00:00"),
        Claim(field="status", value="inactive", source="erp:vendors",
              at_iso="2026-06-01T00:00:00+00:00"),
    ]
    assert resolve_props(claims)["status"] == "inactive"


def test_changing_precedence_re_resolves_rather_than_rewriting():
    claims = [
        Claim(field="terms", value="NET30", source="erp:vendors"),
        Claim(field="terms", value="NET60", source="procurement:coupa"),
    ]
    assert resolve_props(claims, ("erp", "procurement"))["terms"] == "NET30"
    assert resolve_props(claims, ("procurement", "erp"))["terms"] == "NET60"


# ---- links -----------------------------------------------------------------


def test_a_link_is_reversible_without_deleting_history(graph):
    vendor = graph.upsert("Vendor", "Acme", [])
    record = graph.upsert("SourceRecord", "erp:V-1", [])
    link = graph.link(
        "identified_as", vendor.id, record.id, confidence=0.94, method="fuzzy"
    )
    assert graph.neighbors(vendor.id, "identified_as", "out")

    graph.reverse_link(link.id, "steward@example.com", "wrong company")
    # Gone from live reads, still there when you ask for the history.
    assert not graph.neighbors(vendor.id, "identified_as", "out")
    withdrawn = graph.neighbors(
        vendor.id, "identified_as", "out", include_reversed=True
    )
    assert len(withdrawn) == 1
    assert withdrawn[0][0].reversed_by == "steward@example.com"
    assert withdrawn[0][0].reversal_reason == "wrong company"


def test_linking_twice_is_idempotent(graph):
    a = graph.upsert("Vendor", "Acme", [])
    b = graph.upsert("Invoice", "acme:INV-1", [])
    first = graph.link("issued", a.id, b.id)
    second = graph.link("issued", a.id, b.id)
    assert first.id == second.id
    assert len(graph.store.links()) == 1


def test_a_merge_keeps_the_loser_and_its_claims(graph):
    keep = graph.upsert(
        "Vendor", "Acme Industrial Inc.",
        [Claim(field="name", value="Acme Industrial Inc.", source="erp:vendors")],
    )
    drop = graph.upsert(
        "Vendor", "Acme Industial Inc",
        [Claim(field="tax_id", value="94-2711883", source="legacy:ap")],
    )
    graph.supersede(drop.id, keep.id, "steward@example.com", "misspelling")
    merged = graph.get(keep.id)
    assert merged.props["tax_id"] == "94-2711883"
    loser = graph.store.get_object(drop.id)
    assert loser.status == "superseded" and loser.superseded_by == keep.id
    # Reads through the loser follow the pointer.
    assert graph.get(drop.id).id == keep.id


def test_traversal_walks_a_declared_path(graph):
    employee = graph.upsert("Employee", "pat@example.com", [])
    request = graph.upsert("ExpenseRequest", "run_1", [])
    line_a = graph.upsert("ExpenseLine", "run_1#1", [])
    line_b = graph.upsert("ExpenseLine", "run_1#2", [])
    graph.link("submitted", employee.id, request.id)
    graph.link("has_expense_line", request.id, line_a.id)
    graph.link("has_expense_line", request.id, line_b.id)
    reached = graph.traverse(
        employee.id, [("submitted", "out"), ("has_expense_line", "out")]
    )
    assert {r.id for r in reached} == {line_a.id, line_b.id}
    assert graph.traverse(employee.id, [("issued", "out")]) == []


# ---- the ontology ----------------------------------------------------------


def test_every_link_type_names_declared_object_types():
    names = set(ONTOLOGY.object_names())
    for link in ONTOLOGY.links:
        assert link.from_type in names, link.name
        assert link.to_type in names, link.name


def test_every_object_type_belongs_to_a_declared_collection():
    from app import data_collections

    for object_type in ONTOLOGY.objects:
        assert object_type.collection, object_type.name
        assert data_collections.get(object_type.collection) is not None


def test_the_exported_ontology_document_matches_the_declaration(world):
    asset = world.knowledge.repo.get("enterprise-ontology")
    assert asset is not None and asset.status == "published"
    for object_type in ONTOLOGY.objects:
        assert f"`{object_type.name}`" in asset.content
    for source in ONTOLOGY.precedence:
        assert f"`{source}`" in asset.content


# ---- the API ---------------------------------------------------------------


def test_ontology_endpoint_reports_declaration_and_reality(client, world):
    from app.reimbursements import SubmittedDocument

    world.expenses.submit(
        "pat@customer.example",
        documents=[SubmittedDocument(hint="total=64.20 category=meals")],
        commentary="Team lunch after the Acme review.",
    )
    body = client.get("/api/v1/graph/ontology").json()
    by_name = {o["name"]: o for o in body["objects"]}
    assert by_name["Employee"]["instances"] == 1
    assert by_name["ExpenseLine"]["instances"] >= 1
    # A declared type with nothing in it is visible as such.
    assert by_name["ReceivingReport"]["instances"] == 0
    assert body["precedence"][0] == "human"


def test_the_analytical_question_is_a_traversal(client, world):
    from app.reimbursements import SubmittedDocument

    for hint in ("total=64.20 category=meals", "total=32.50 category=travel"):
        world.expenses.submit(
            "john.smith@customer.example",
            documents=[SubmittedDocument(hint=hint)],
            commentary="Client visit to Acme.",
        )
    body = client.get(
        "/api/v1/graph/employees/john.smith@customer.example/expenses",
        params={"days": 180},
    ).json()
    assert body["requests"] == 2
    assert body["lines"] == 2
    assert body["requested_usd"] == pytest.approx(96.70)
    assert set(body["by_category"]) == {"meals", "travel"}
    # The answer carries the path that produced it.
    assert body["traversal"][0].startswith("Employee")


def test_an_unknown_employee_is_an_empty_rollup_not_an_error(client, world):
    body = client.get(
        "/api/v1/graph/employees/nobody@customer.example/expenses"
    ).json()
    assert body["requests"] == 0 and body["requested_usd"] == 0
    assert body["employee_object_id"] == ""


def test_object_detail_exposes_claims_and_conflicts(client, world):
    from app.sync import SyncRequest

    world.sync.deliver(SyncRequest.model_validate({
        "source": "erp-vendors",
        "vendors": [{"external_id": "V-1", "name": "Acme Industrial Inc.",
                     "payment_terms": "NET30"}],
    }), actor="system:test")
    world.sync.deliver(SyncRequest.model_validate({
        "source": "procurement-coupa",
        "vendors": [{"external_id": "C-1", "name": "ACME INDUSTRIAL, INCORPORATED",
                     "payment_terms": "NET60"}],
    }), actor="system:test")
    vendor_id = object_id("Vendor", "Acme Industrial Inc.")
    body = client.get(f"/api/v1/graph/objects/{vendor_id}").json()
    assert body["object"]["type"] == "Vendor"
    assert "payment_terms" in body["conflicts"]
    assert body["object"]["props"]["payment_terms"] == "NET30"
    kinds = {n["type"] for n in body["neighbors"]}
    assert "identified_as" in kinds


def _api_routes(app):
    """Every route, following included routers.

    This FastAPI version keeps `include_router` results as wrapper objects
    rather than flattening them into `app.routes`, so walking `app.routes` and
    reading `.path` finds nothing — which would make the assertion below pass
    without testing anything.
    """
    seen = []

    def walk(routes):
        for route in routes:
            # `include_router` leaves a wrapper that holds the real router on
            # `original_router`; plain mounts expose `routes` directly.
            original = getattr(route, "original_router", None)
            nested = getattr(original, "routes", None) or getattr(route, "routes", None)
            if nested:
                walk(nested)
            elif getattr(route, "path", ""):
                seen.append(route)

    walk(app.routes)
    return seen


def test_the_graph_is_read_only_over_http(client):
    # No POST/PUT/DELETE on /graph: objects are written by Flows, through
    # Actions. A console that could invent a vendor would be a console that
    # could invent one with no run behind it.
    from app.main import app

    graph_routes = [
        r for r in _api_routes(app) if r.path.startswith("/api/v1/graph")
    ]
    assert graph_routes, "no /api/v1/graph routes found — the walk is broken"
    for route in graph_routes:
        assert set(getattr(route, "methods", set())) <= {"GET", "HEAD"}


def test_authored_worlds_are_read_only_too(client):
    """Materializing is a read: it runs into a throwaway world."""
    from app.main import app

    world_routes = [
        r for r in _api_routes(app) if r.path.startswith("/api/v1/worlds")
    ]
    assert world_routes
    for route in world_routes:
        assert set(getattr(route, "methods", set())) <= {"GET", "HEAD"}


# ---- TBox conformance ------------------------------------------------------
#
# The point of validating the ontology on write is that it turns the TBox from
# a description into a constraint, which gives testing a universal tier: these
# hold for every object and edge, so no case has to declare them.


def test_the_seeded_world_conforms_to_its_ontology(world):
    from app.payables import seed_payables_if_empty
    from app.reimbursements import seed_reimbursements_if_empty

    seed_reimbursements_if_empty(world.expenses)
    seed_payables_if_empty(world.payables, sync=world.sync)
    violations = world.graph.check_invariants()
    assert violations == [], "\n".join(str(v) for v in violations)


def test_an_undeclared_object_type_is_refused(graph):
    from app.graph import TBoxError

    with pytest.raises(TBoxError) as excinfo:
        graph.upsert("Sasquatch", "nope", [])
    assert excinfo.value.violations[0].kind == "unknown-object-type"


def test_an_undeclared_field_is_refused(graph):
    from app.graph import TBoxError

    with pytest.raises(TBoxError) as excinfo:
        graph.upsert(
            "Vendor", "Acme Industrial Inc.",
            [Claim(field="favourite_colour", value="blue", source="human:x")],
        )
    violation = excinfo.value.violations[0]
    assert violation.kind == "undeclared-field"
    assert violation.subject == "Vendor.favourite_colour"


def test_a_numeric_field_claimed_as_prose_is_refused(graph):
    from app.graph import TBoxError

    with pytest.raises(TBoxError):
        graph.upsert(
            "Invoice", "acme:INV-1",
            [Claim(field="total_usd", value="about nine hundred", source="model:x")],
        )


def test_a_link_between_the_wrong_types_is_refused(graph):
    from app.graph import TBoxError

    employee = graph.upsert("Employee", "pat@example.com", [])
    invoice = graph.upsert("Invoice", "acme:INV-1", [])
    with pytest.raises(TBoxError) as excinfo:
        graph.link("issued", employee.id, invoice.id)
    assert excinfo.value.violations[0].kind == "wrong-endpoint-type"


def test_a_second_parent_is_refused(graph):
    """The invariant that matters most here: one invoice, one issuing vendor.

    Two vendors claiming one invoice is exactly the corruption the old
    slug-as-identity scheme produced silently.
    """
    from app.graph import TBoxError

    first = graph.upsert("Vendor", "Acme Industrial Inc.", [])
    second = graph.upsert("Vendor", "Northwind Traders LLC", [])
    invoice = graph.upsert("Invoice", "acme:INV-1", [])
    graph.link("issued", first.id, invoice.id)
    with pytest.raises(TBoxError) as excinfo:
        graph.link("issued", second.id, invoice.id)
    assert excinfo.value.violations[0].kind == "cardinality"


def test_a_many_to_one_link_constrains_the_other_direction(graph):
    from app.graph import TBoxError

    order = graph.upsert("PurchaseOrder", "PO-1", [])
    first = graph.upsert("Vendor", "Acme Industrial Inc.", [])
    second = graph.upsert("Vendor", "Northwind Traders LLC", [])
    graph.link("ordered_from", order.id, first.id)
    with pytest.raises(TBoxError):
        graph.link("ordered_from", order.id, second.id)


def test_many_to_many_links_are_deliberately_unconstrained(graph):
    """A request reviewed twice can be decided by two people."""
    request = graph.upsert("ExpenseRequest", "run_1", [])
    for email in ("dana@example.com", "sam@example.com"):
        person = graph.upsert("Employee", email, [])
        graph.link("decided_by", request.id, person.id)
    assert len(graph.neighbors(request.id, "decided_by", "out")) == 2


def test_a_dangling_link_is_refused(graph):
    from app.graph import TBoxError

    vendor = graph.upsert("Vendor", "Acme Industrial Inc.", [])
    with pytest.raises(TBoxError) as excinfo:
        graph.link("issued", vendor.id, "invoice_does_not_exist")
    assert excinfo.value.violations[0].kind == "dangling-link"


def test_a_permissive_graph_reports_instead_of_refusing():
    """A deployment migrating older data reads the sweep rather than failing."""
    from app.graph import GraphService, InMemoryGraphStore

    graph = GraphService(InMemoryGraphStore(), strict=False)
    graph.upsert(
        "Vendor", "Acme Industrial Inc.",
        [Claim(field="favourite_colour", value="blue", source="human:x")],
    )
    violations = graph.check_invariants()
    assert [v.kind for v in violations] == ["undeclared-field"]


def test_the_invariants_endpoint_reports_conformance(client, world):
    from app.reimbursements import seed_reimbursements_if_empty

    seed_reimbursements_if_empty(world.expenses)
    body = client.get("/api/v1/graph/invariants").json()
    assert body["ok"] is True
    assert body["violations"] == []
    assert body["objects_checked"] > 0 and body["links_checked"] > 0
    assert body["strict_writes"] is True


# ---- provenance -----------------------------------------------------------


def test_claims_and_links_name_the_run_and_step_that_wrote_them(world):
    from app.reimbursements import SubmittedDocument

    request = world.expenses.submit(
        "pat@customer.example",
        documents=[SubmittedDocument(hint="total=64.20 category=meals")],
        commentary="Team lunch after the Acme review.",
    )
    line_id = request.lines[0].graph_ref.split("/")[1].split("@")[0]
    line = world.graph.get(line_id)
    # `source` is the actor; run_id/step_id are the act. Both, or "why does it
    # say this" only ever resolves to a category.
    assert all(c.run_id == request.run_id for c in line.claims)
    assert {c.step_id for c in line.claims} == {"a1"}
    link, _ = world.graph.neighbors(line_id, "evidenced_by", "out")[0]
    assert link.run_id == request.run_id and link.step_id == "a1"


def test_a_step_records_what_it_wrote(world):
    from app.reimbursements import SubmittedDocument

    request = world.expenses.submit(
        "pat@customer.example",
        documents=[SubmittedDocument(hint="total=64.20 category=meals")],
        commentary="Team lunch after the Acme review.",
    )
    run = world.engine.get(request.run_id)
    stored = run.step("s1")
    assert stored.writes == [f"document:{request.documents[0].document_id}"]

    committed = run.step("a1")
    assert any(ref.startswith("graph:ExpenseRequest/") for ref in committed.writes)
    assert any(ref.startswith("graph:ExpenseLine/") for ref in committed.writes)
    assert any(ref.startswith("link:submitted/") for ref in committed.writes)
    # Read-set and write-set are different things and both are recorded.
    assert any(ref.startswith("knowledge:") for ref in run.refs)
    assert run.writes()  # the run-level rollup, in the order steps wrote


def test_a_replayed_step_keeps_its_recorded_write_set(world):
    from app.reimbursements import SubmittedDocument

    request = world.expenses.submit(
        "sam@customer.example",
        documents=[SubmittedDocument(hint="total=612.00 category=lodging")],
        commentary="Conference block rate for the finance summit.",
    )
    before = world.engine.get(request.run_id).writes()
    decided = world.tasks.decide(
        request.task_id, "approved", "dana@customer.example"
    )
    world.expenses.resume_for_task(decided)
    after = world.engine.get(request.run_id)
    # The replayed steps kept their write-sets, and only the commit added to it.
    assert set(before) <= set(after.writes())
    assert after.step("a1").writes


def test_the_ontology_view_says_which_cardinalities_are_enforced(client, world):
    body = client.get("/api/v1/graph/ontology").json()
    by_name = {linked["name"]: linked for linked in body["links"]}
    assert by_name["issued"]["enforced"] is True
    assert by_name["ordered_from"]["cardinality"] == "many-to-one"
    # Declared unconstrained on purpose, not by omission.
    assert by_name["decided_by"]["enforced"] is False


def test_edge_lookups_go_by_endpoint_not_a_full_scan(graph):
    """Every traversal and every cardinality check does one of these.

    Streaming the whole edge table for each would make a single AP run read it
    dozens of times, so both stores answer by endpoint. Single-field equality
    on purpose: nothing to deploy alongside the code.
    """
    vendor = graph.upsert("Vendor", "Acme Industrial Inc.", [])
    invoice = graph.upsert("Invoice", "acme-industrial:INV-1", [])
    other = graph.upsert("Invoice", "acme-industrial:INV-2", [])
    graph.link("issued", vendor.id, invoice.id)
    graph.link("issued", vendor.id, other.id)

    assert len(graph.store.links_from(vendor.id)) == 2
    assert len(graph.store.links_to(invoice.id)) == 1
    assert graph.store.links_from(invoice.id) == []
    # And the traversal sees the same thing either way round.
    assert len(graph.neighbors(vendor.id, "issued", "out")) == 2
    assert len(graph.neighbors(invoice.id, "issued", "in")) == 1
    assert len(graph.neighbors(vendor.id, "issued", "both")) == 2


def test_one_source_restating_a_field_is_an_update_not_a_conflict(graph):
    """A feed re-landing a row stamps a fresh timestamp every time.

    Counting that as a conflict buries the real ones — a disagreement needs two
    parties, and this is the check that keeps the conflict count meaningful.
    """
    record = graph.upsert(
        "SourceRecord", "erp-vendors:V-1",
        [
            Claim(field="landed_at", value="2026-08-01T00:00:00+00:00",
                  source="erp:erp-vendors", at_iso="2026-08-01T00:00:00+00:00"),
            Claim(field="landed_at", value="2026-08-03T00:00:00+00:00",
                  source="erp:erp-vendors", at_iso="2026-08-03T00:00:00+00:00"),
        ],
    )
    assert record.conflicts() == {}
    # Both readings are still on the record; the newer one resolves.
    assert len([c for c in record.claims if c.field == "landed_at"]) == 2
    assert record.props["landed_at"] == "2026-08-03T00:00:00+00:00"


def test_two_sources_disagreeing_is_still_a_conflict(graph):
    record = graph.upsert(
        "Vendor", "Acme Industrial Inc.",
        [
            Claim(field="payment_terms", value="NET30", source="erp:vendors"),
            Claim(field="payment_terms", value="NET60", source="procurement:coupa"),
        ],
    )
    assert set(record.conflicts()) == {"payment_terms"}
    assert record.props["payment_terms"] == "NET30"


# ---- entity search and the link table ---------------------------------------


@pytest.fixture()
def seeded(client, world):
    """The demo world: vendors landed by the sync feeds, POs, invoices."""
    from app.payables import seed_payables_if_empty

    seed_payables_if_empty(world.payables, sync=world.sync)
    return client


def test_search_finds_an_entity_by_label(seeded):
    hits = seeded.get("/api/v1/graph/search", params={"q": "acme"}).json()
    assert hits
    top = hits[0]
    assert top["entity"]["type"] == "Vendor"
    assert "Acme" in top["entity"]["label"]


def test_an_external_id_resolves_to_the_canonical_entity(seeded):
    """Typing an ERP id must find the Vendor it means here, not just the
    landing record — the identity Link made searchable is MDM's payoff."""
    hits = seeded.get("/api/v1/graph/search", params={"q": "V-10041"}).json()
    types = {h["entity"]["type"]: h for h in hits}
    assert "SourceRecord" in types
    assert "Vendor" in types
    vendor_hit = types["Vendor"]
    assert vendor_hit["match"] == "external id"
    assert "V-10041" in vendor_hit["via"]


def test_search_never_duplicates_an_entity(seeded):
    hits = seeded.get("/api/v1/graph/search", params={"q": "a"}).json()
    ids = [h["entity"]["id"] for h in hits]
    assert len(ids) == len(set(ids))


def test_links_table_makes_identity_links_auditable(seeded):
    rows = seeded.get(
        "/api/v1/graph/links", params={"type": "identified_as"}
    ).json()
    assert rows
    for row in rows:
        assert row["type"] == "identified_as"
        # Declared Vendor -> SourceRecord: the canonical entity owns its ids.
        assert row["from_entity"]["type"] == "Vendor"
        assert row["to_entity"]["type"] == "SourceRecord"
        # The identity metadata is the point of the table.
        assert 0 < row["confidence"] <= 1
        assert row["method"]
        assert row["live"] is True


def test_ask_trace_steps_carry_walkable_refs(seeded):
    """The Walk screen replays the planner's steps; a step that landed on an
    entity must say which one, as a ref — not as prose to parse."""
    body = seeded.get(
        "/api/v1/ask", params={"q": "How much has Acme Industrial invoiced?"}
    ).json()
    graph_steps = [
        s for s in body.get("steps", [])
        if s["tool"] in ("resolve_entity", "describe_entity", "aggregate_edges")
    ]
    if not graph_steps:  # the planner may degrade to retrieval with no model
        import pytest

        pytest.skip("planner did not run graph tools in this environment")
    assert any(s.get("ref", "").startswith("graph:") for s in graph_steps)


# ---- composition: the derived hierarchy --------------------------------------


def test_part_ness_is_derived_never_declared():
    from app.ontology import ONTOLOGY

    assert ONTOLOGY.owner_of("InvoiceLine") == "Invoice"
    # The non-obvious case the key test decides: a retained computation is
    # still a dependent thing.
    assert ONTOLOGY.owner_of("MatchSet") == "Invoice"
    # "Belongs to" is not "is part of": a request has its own key and
    # lifecycle, so `submitted` stays an association.
    assert ONTOLOGY.owner_of("ExpenseRequest") is None
    assert set(ONTOLOGY.parts_of("Invoice")) == {"InvoiceLine", "MatchSet"}
    assert [o.name for o in ONTOLOGY.roots()] == [
        "Employee", "Team", "ExpenseRequest", "Document", "Vendor",
        "SourceRecord", "Invoice", "PurchaseOrder", "ReceivingReport",
    ]


def test_the_declared_ontology_conforms():
    from app.ontology import validate_ontology

    assert validate_ontology() == []


def test_composition_rules_are_refused_at_declaration():
    from app.ontology import LinkType, ObjectType, Ontology, validate_ontology

    objects = (
        ObjectType(name="A", label="A", summary="", natural_key="k"),
        ObjectType(name="B", label="B", summary="", natural_key="k"),
        ObjectType(name="C", label="C", summary="", natural_key="k"),
    )
    # A part with two possible owners by cardinality.
    bad_cardinality = Ontology(objects=objects, links=(
        LinkType("owns", "A", "B", "many-to-many", "", nature="composition"),
    ), precedence=("human",))
    assert any("two owners" in p for p in validate_ontology(bad_cardinality))

    # Two compositional links targeting one type: two owners.
    two_owners = Ontology(objects=objects, links=(
        LinkType("owns_a", "A", "C", "one-to-many", "", nature="composition"),
        LinkType("owns_b", "B", "C", "one-to-many", "", nature="composition"),
    ), precedence=("human",))
    assert any("exactly one" in p for p in validate_ontology(two_owners))

    # An ownership cycle.
    cycle = Ontology(objects=objects, links=(
        LinkType("owns_b", "A", "B", "one-to-many", "", nature="composition"),
        LinkType("owns_a", "B", "A", "one-to-many", "", nature="composition"),
    ), precedence=("human",))
    assert any("forest" in p for p in validate_ontology(cycle))


def test_an_orphan_part_is_an_invariant_violation(graph):
    graph.upsert(
        "InvoiceLine", "acme-industrial:INV-1#1",
        [Claim(field="amount_usd", value=10.0, source="flow:test")],
    )
    kinds = {v.kind for v in graph.check_invariants()}
    assert "orphan" in kinds


def test_a_part_key_must_embed_its_owner_key(graph):
    invoice = graph.upsert(
        "Invoice", "acme-industrial:INV-9",
        [Claim(field="total_usd", value=10.0, source="flow:test")],
    )
    line = graph.upsert(
        "InvoiceLine", "free-floating-key#1",
        [Claim(field="amount_usd", value=10.0, source="flow:test")],
    )
    graph.link("has_invoice_line", invoice.id, line.id)
    kinds = {v.kind for v in graph.check_invariants()}
    assert "weak-key" in kinds


def test_search_ranks_independent_entities_above_their_parts(seeded):
    hits = seeded.get(
        "/api/v1/graph/search", params={"q": "acme", "limit": 30}
    ).json()
    from app.ontology import ONTOLOGY

    subordinate = {o.name for o in ONTOLOGY.objects if ONTOLOGY.owner_of(o.name)}
    kinds = [h["entity"]["type"] in subordinate for h in hits]
    # Once the first part appears, no independent entity follows it.
    if True in kinds:
        assert all(kinds[kinds.index(True):])


def test_ontology_api_exposes_the_hierarchy(seeded):
    body = seeded.get("/api/v1/graph/ontology").json()
    by_name = {o["name"]: o for o in body["objects"]}
    assert by_name["InvoiceLine"]["owner_type"] == "Invoice"
    assert set(by_name["Invoice"]["part_types"]) == {"InvoiceLine", "MatchSet"}
    natures = {l["name"]: l["nature"] for l in body["links"]}
    assert natures["has_invoice_line"] == "composition"
    assert natures["identified_as"] == "identity"
    assert natures["issued"] == "association"


# ---- minted identity: keys find entities, they are not entities --------------


def test_identity_survives_a_key_change(graph):
    """The email-changes case. Ids are minted, keys are aliases: re-keying an
    employee changes how records find them, not what anything references."""
    kim = graph.upsert(
        "Employee", "kim@customer.example",
        [Claim(field="name", value="Kim Reyes", source="flow:test")],
    )
    request = graph.upsert(
        "ExpenseRequest", "run_x",
        [Claim(field="requested_usd", value=10.0, source="flow:test")],
    )
    graph.link("submitted", kim.id, request.id)

    rekeyed = graph.rekey(
        kim.id, "kim.reyes@customer.example", by="human:hr-admin",
    )
    assert rekeyed.id == kim.id  # identity unchanged
    # Both addresses resolve to the same entity — a journal that recorded the
    # old alias still dereferences.
    assert graph.get(object_id("Employee", "kim@customer.example")).id == kim.id
    assert (
        graph.get(object_id("Employee", "kim.reyes@customer.example")).id == kim.id
    )
    # The link never noticed.
    neighbors = graph.neighbors(kim.id, "submitted", "out")
    assert [o.id for _, o in neighbors] == [request.id]
    # And the change is a claim with an author, not a silent mutation.
    assert any(
        c.field == "natural_key" and c.source == "human:hr-admin"
        for c in rekeyed.claims
    )


def test_rekeying_onto_another_entity_is_refused(graph):
    graph.upsert("Employee", "kim@customer.example",
                 [Claim(field="name", value="Kim", source="flow:test")])
    dana = graph.upsert("Employee", "dana@customer.example",
                        [Claim(field="name", value="Dana", source="flow:test")])
    from app.graph import TBoxError

    with pytest.raises(TBoxError, match="supersede, not a rekey"):
        graph.rekey(dana.id, "kim@customer.example", by="human:hr-admin")


def test_same_key_still_lands_on_one_entity(graph):
    """Minting must not cost idempotency: two sightings of one key, one uid."""
    a = graph.upsert("Vendor", "Acme Industrial Inc.",
                     [Claim(field="status", value="active", source="erp:x")])
    b = graph.upsert("Vendor", "ACME INDUSTRIAL, INCORPORATED",
                     [Claim(field="status", value="active", source="erp:y")])
    assert a.id == b.id  # normalization still collapses spellings


# ---- domains and ref fields ---------------------------------------------------


def test_domains_are_declared_on_roots_and_inherited_by_parts():
    from app.ontology import ONTOLOGY

    assert ONTOLOGY.effective_domains("Employee") == ("people", "expenses")
    # A part lives where its owner lives — it declares nothing itself.
    assert ONTOLOGY.effective_domains("InvoiceLine") == ("payables",)
    assert ONTOLOGY.object_type("InvoiceLine").domains == ()
    # Multi-membership: evidence serves both flows.
    assert set(ONTOLOGY.effective_domains("Document")) == {"expenses", "payables"}


def test_domain_and_ref_rules_are_refused_at_declaration():
    from app.ontology import (
        FieldSpec, LinkType, ObjectType, Ontology, validate_ontology,
    )

    objects = (
        ObjectType(name="A", label="A", summary="", natural_key="k",
                   domains=("nowhere",),
                   fields=(FieldSpec("other", "ref:Ghost"),)),
        # A part that tries to declare its own domain: drift refused.
        ObjectType(name="B", label="B", summary="", natural_key="k",
                   domains=("people",)),
        # A root that declares none: unfiled entities clutter every view.
        ObjectType(name="C", label="C", summary="", natural_key="k"),
    )
    bad = Ontology(objects=objects, links=(
        LinkType("owns", "A", "B", "one-to-many", "", nature="composition"),
    ), precedence=("human",))
    problems = " ".join(validate_ontology(bad))
    assert "unknown domain `nowhere`" in problems
    assert "refs unknown type `Ghost`" in problems
    assert "part type `B` declares domains" in problems
    assert "root type `C` declares no domain" in problems
