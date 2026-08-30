"""Collections, documents, sources, runs, profiles — the governance views.

What these assert is mostly about *honesty*: retention is computed and stamped
rather than implied, a count the console cannot get is null rather than zero, a
feed's history comes from the runs rather than a second bookkeeping store, and
the graph has no write route.
"""

import pytest

from app import data_collections
from app.reimbursements import SubmittedDocument
from tests import demo_world
from tests import world as test_world


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


# ---- collections -----------------------------------------------------------


def test_retention_is_parsed_and_stamped():
    assert data_collections.parse_duration("P2Y").days == 730
    assert data_collections.parse_duration("P90D").days == 90
    assert data_collections.parse_duration("indefinite") is None
    assert data_collections.parse_duration("") is None
    until = data_collections.retention_until("audit-documents")
    assert until  # two years out
    assert data_collections.retention_until("knowledge-library") == ""


def test_every_collection_declares_its_governed_properties():
    for collection in data_collections.COLLECTIONS:
        assert collection.role in ("knowledge", "data")
        assert collection.custody in ("copy", "reference")
        assert collection.container
        assert collection.summary
        # `indefinite` is a decision, not a default that slipped through.
        assert collection.retention == "indefinite" or data_collections.parse_duration(
            collection.retention
        )


def test_collections_endpoint_carries_live_counts(client, world):
    demo_world.seed_expenses(world.expenses)
    rows = client.get("/api/v1/collections").json()
    by_id = {r["id"]: r for r in rows}
    assert by_id["knowledge-library"]["rows"] >= 9
    assert by_id["expense-records"]["rows"] > 0
    assert by_id["audit-documents"]["retention"] == "P2Y"
    assert by_id["vendor-master"]["authoritative"] is True
    assert by_id["source-landing"]["authoritative"] is False
    # A Collection whose rows the console cannot count honestly says null.
    assert by_id["eval-datasets"]["rows"] is None


def test_collections_filter_by_role(client, world):
    knowledge = client.get("/api/v1/collections", params={"role": "knowledge"}).json()
    assert knowledge and all(r["role"] == "knowledge" for r in knowledge)


def test_the_exported_collections_document_matches_the_registry(world):
    asset = world.knowledge.repo.get("data-collections")
    assert asset is not None
    for collection in data_collections.COLLECTIONS:
        assert f"`{collection.id}`" in asset.content
    assert "P2Y" in asset.content


# ---- documents -------------------------------------------------------------


def test_documents_carry_regions_and_a_retention_date(client, world):
    request = world.expenses.submit(
        "felicia.marks@naviant.com",
        documents=[SubmittedDocument(filename="r.jpg", hint="total=64.20 category=meals")],
        commentary="Team lunch after the Acme review.",
    )
    document_id = request.documents[0].document_id
    body = client.get(f"/api/v1/documents/{document_id}").json()
    assert body["collection_id"] == "audit-documents"
    assert body["retention_until_iso"]
    assert body["content_b64"] == ""  # bytes are opt-in, not free
    regions = client.get(f"/api/v1/documents/{document_id}/regions").json()
    assert any(r["field"] == "receipt.total_usd" for r in regions)
    stats = client.get("/api/v1/documents/stats").json()
    assert stats["documents"] >= 1 and stats["with_regions"] >= 1


def test_a_document_with_no_bytes_says_so(client, world):
    request = world.expenses.submit(
        "felicia.marks@naviant.com",
        documents=[SubmittedDocument(filename="r.jpg", hint="total=10.00")],
    )
    resp = client.get(
        f"/api/v1/documents/{request.documents[0].document_id}/content"
    )
    assert resp.status_code == 404
    assert "hint-driven" in resp.json()["detail"]


def test_the_same_file_twice_is_one_document(world):
    import base64

    content = base64.b64encode(b"a receipt, of sorts").decode()
    first = world.documents.put_bytes(content, filename="a.jpg", run_id="run_1")
    second = world.documents.put_bytes(content, filename="a.jpg", run_id="run_2")
    assert first.id == second.id
    assert second.run_ids == ["run_1", "run_2"]
    assert len(world.documents.store.list()) == 1


def test_an_oversized_document_is_refused_with_the_limit(world):
    import base64

    from app.documents import DocumentTooLargeError

    world.documents.max_bytes = 32
    with pytest.raises(DocumentTooLargeError) as excinfo:
        world.documents.put_bytes(base64.b64encode(b"x" * 64).decode())
    assert "32" in str(excinfo.value)


def test_expiring_is_reported_not_swept(client, world):
    from app.models import utcnow

    world.expenses.submit(
        "felicia.marks@naviant.com",
        documents=[SubmittedDocument(filename="r.jpg", hint="total=10.00")],
    )
    record = world.documents.store.list()[0]
    record.retention_until_iso = (utcnow().replace(year=2020)).isoformat()
    world.documents.store.put(record)
    expiring = client.get("/api/v1/documents/expiring").json()
    assert [d["id"] for d in expiring] == [record.id]
    # Reported, and still there: nothing purges it.
    assert world.documents.get(record.id) is not None


# ---- sources ---------------------------------------------------------------


def test_sources_are_assembled_from_the_runs(client, world):
    demo_world.seed_payables(world.payables, sync=world.sync)
    rows = client.get("/api/v1/sources").json()
    by_key = {(r["source"], r["kind"]): r for r in rows}
    erp = by_key[("erp-vendors", "vendors")]
    assert erp["deliveries"] == 2  # the delivery and the re-delivery
    assert erp["records_landed"] == 4
    assert erp["unchanged"] == 1
    assert erp["created"] == 4
    assert erp["watermark"]
    assert erp["last_run_id"] and erp["last_status"] == "succeeded"
    legacy = by_key[("legacy-ap-extract", "vendors")]
    assert legacy["awaiting_steward"] == 1


def test_a_delivery_needs_admin_and_accepts_a_key(client, owner, world):
    body = {
        "source": "erp-vendors",
        "watermark": "2026-09-01T00:00:00Z",
        "vendors": [{"external_id": "V-1", "name": "Widget Works Inc."}],
    }
    # A feed is a machine act, so a credential is the right caller here — the
    # opposite of a Task decision.
    resp = client.post("/api/v1/sources/deliver", json=body, headers=owner)
    assert resp.status_code == 202
    assert resp.json()["runs"][0]["created"] == 1


def test_an_idempotent_redelivery_replays_the_same_run(client, owner, world):
    body = {
        "source": "erp-vendors",
        "idempotency_key": "batch-7",
        "vendors": [{"external_id": "V-2", "name": "Gadget Guild LLC"}],
    }
    first = client.post("/api/v1/sources/deliver", json=body, headers=owner).json()
    second = client.post("/api/v1/sources/deliver", json=body, headers=owner).json()
    assert first["runs"][0]["run_id"] == second["runs"][0]["run_id"]
    assert second["runs"][0]["replayed"] is True
    assert len(world.engine.list(flow_id="vendor-sync")) == 1


# ---- runs ------------------------------------------------------------------


def test_runs_list_and_detail(client, world):
    demo_world.seed_expenses(world.expenses)
    rows = client.get(
        "/api/v1/runs", params={"flow_id": "expense-reimbursement"}
    ).json()
    assert rows and all(r["flow_id"] == "expense-reimbursement" for r in rows)
    # Newest first, and each row is triage-shaped.
    assert [r["started_at_iso"] for r in rows] == sorted(
        [r["started_at_iso"] for r in rows], reverse=True
    )
    suspended = [r for r in rows if r["status"] == "suspended"]
    assert suspended and suspended[0]["open_task_id"]

    detail = client.get(f"/api/v1/runs/{rows[0]['id']}").json()
    assert detail["steps"] and detail["refs"]
    steps = client.get(f"/api/v1/runs/{rows[0]['id']}/steps").json()
    assert [s["step_id"] for s in steps] == [s["step_id"] for s in detail["steps"]]


def test_run_stats_counts_human_involvement(client, world):
    demo_world.seed_expenses(world.expenses)
    stats = client.get(
        "/api/v1/runs/stats", params={"flow_id": "expense-reimbursement"}
    ).json()
    assert stats["total"] > 0
    assert stats["with_human"] > 0
    assert stats["human_rounds"] >= stats["with_human"]
    assert stats["model_calls"] > 0


def test_resuming_a_run_is_admin_only(client, world, owner):
    demo_world.seed_expenses(world.expenses)
    # With the owner fixture active every request needs a credential, so the
    # reads carry one too.
    run_id = client.get("/api/v1/runs", headers=owner).json()[0]["id"]
    assert client.post(f"/api/v1/runs/{run_id}/resume", headers=owner).status_code == 200
    assert client.get("/api/v1/runs/nope", headers=owner).status_code == 404


# ---- the derived profile ---------------------------------------------------


def test_profiles_refresh_on_demand(client, owner, world):
    demo_world.seed_payables(world.payables, sync=world.sync)
    resp = client.post("/api/v1/vendor-profiles/refresh", headers=owner)
    assert resp.status_code == 200
    assert resp.json()["vendors_profiled"] > 0
    rows = client.get("/api/v1/vendor-profiles", headers=owner).json()
    assert rows and rows[0]["invoices"] >= rows[-1]["invoices"]
    assert any(r["median_usd"] > 0 for r in rows)


# ---- authored ABoxes ------------------------------------------------------
#
# A good deal of ABox is written by hand in Python — every world an eval case
# runs in, and the demo seed's feed deliveries. It was only ever observable
# through its effects. These assert it is now viewable, and that looking is a
# read. `/api/v1/worlds` is the fixture catalog under its older name.


def test_the_catalog_lists_the_worlds_declared_in_code(client, world):
    rows = client.get("/api/v1/worlds").json()
    by_id = {r["id"]: r for r in rows}
    # The demo seed is a fixture like any other — one registry, because it is
    # the same kind of thing as the world an eval case runs in.
    assert "seed:demo-world" not in by_id
    assert by_id["seed/demo-world"]["flow_id"] == ""
    # It is a declared fixture and nothing else now: the startup seed that
    # used to own these deliveries is gone, so the module that declares them
    # is the module the catalog names.
    assert by_id["seed/demo-world"]["derived_from"].endswith("eval_specs.py")
    assert len([r for r in rows if r["flow_id"]]) >= 6


def test_materializing_shows_the_consequence_not_the_payload(client, world):
    """Run into a throwaway world, not a static expansion of the JSON.

    The difference is the whole value: what comes back includes the resolution
    the sync flow performed and the Task it escalated, neither of which is in
    the delivery payload.
    """
    body = client.get("/api/v1/worlds/seed/demo-world").json()
    assert body["error"] == ""
    assert body["by_type"]["Vendor"] == 4
    assert body["by_type"]["SourceRecord"] == 12
    assert body["by_link_type"]["identified_as"] >= 5
    # The sync flows really ran.
    assert {r["flow_id"] for r in body["runs"]} == {
        "vendor-sync", "purchase-order-sync", "receiving-sync"
    }
    assert all(r["status"] == "succeeded" for r in body["runs"])
    # And the misspelled vendor escalated rather than being guessed at.
    assert [t["kind"] for t in body["tasks"]] == ["match_review"]
    assert body["violations"] == []


def test_a_materialized_object_answers_what_a_natural_key_resolves_to(client, world):
    body = client.get("/api/v1/worlds/seed/demo-world").json()
    acme = next(o for o in body["objects"] if o["label"] == "Acme Industrial Inc.")
    assert acme["natural_key"] == "Acme Industrial Inc."
    assert acme["id"].startswith("vendor_")
    assert acme["ref"].startswith(f"graph:Vendor/{acme['id']}")
    # Two systems asserted a name; one of them resolves and both are kept.
    names = [c for c in acme["claims"] if c["field"] == "name"]
    assert len(names) == 2
    assert sum(1 for c in names if c["resolves"]) == 1
    assert "name" in acme["conflict_fields"]


def test_materializing_touches_nothing_real(client, world):
    before = world.graph.stats()
    before_runs = len(world.engine.list())
    client.get("/api/v1/worlds/seed/demo-world")
    assert world.graph.stats() == before
    assert len(world.engine.list()) == before_runs


def test_an_expense_fixture_is_thin_and_says_so(client, world):
    body = client.get("/api/v1/worlds/expense/felicia-on-the-roster").json()
    # One Employee and no edges — correct, and worth being able to see rather
    # than inferring from a count.
    assert body["by_type"] == {"Employee": 1}
    assert body["links"] == []
    assert body["objects"][0]["claims"][0]["source"] == "fixture"


def test_a_world_is_shown_by_what_it_asserts_not_by_its_payload(client, world):
    """The preview answers the question by running it, so it answers it once.

    The declaration is a call away — `/api/v1/evals/fixtures/{id}` — and the
    catalog and the preview both leave it out rather than handing back the
    payload beside the consequence it produced.
    """
    listed = client.get("/api/v1/worlds").json()[0]
    assert "deliveries" not in listed and "objects" not in listed
    body = client.get("/api/v1/worlds/expense/felicia-on-the-roster").json()
    assert "deliveries" not in body["world"]


def test_the_live_graph_comes_back_in_the_same_shape(client, world):
    """One renderer for the running graph and a declared one."""
    demo_world.seed_payables(world.payables, sync=world.sync)
    body = client.get("/api/v1/graph/instances?limit=50").json()
    assert body["world"]["id"] == "live"
    assert "deliveries" not in body["world"]
    assert any(o["type"] == "Vendor" for o in body["objects"])


def test_an_unknown_world_is_a_404(client, world):
    assert client.get("/api/v1/worlds/expense/nope").status_code == 404
