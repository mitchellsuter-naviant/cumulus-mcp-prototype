import pytest

import app.deps as deps
from app.service import KnowledgeService, seed_if_empty
from app.storage import InMemoryRepository
from app.identities import IdentityService, InMemoryMachineIdentityDirectory
from app.users import InMemoryUserDirectory
from app.config import settings
from app.service import parse_seed_file


@pytest.fixture()
def client(_client):
    deps._service = KnowledgeService(InMemoryRepository())
    seed_if_empty(deps._service, settings.seed_dir)
    # Startup does this too: the generated exports are part of the corpus.
    from app.platform_assets import publish_generated_assets

    publish_generated_assets(deps._service)
    deps._user_directory = InMemoryUserDirectory()
    # The identity service *projects* people out of the user directory, so
    # swapping one without the other leaves it reading a directory this test
    # no longer writes to — and `/identities`, which is where managing a
    # person lives, would answer about the previous module's world.
    deps._identity_service = IdentityService(
        deps._user_directory, InMemoryMachineIdentityDirectory()
    )
    return _client


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    # 17 seed documents + 5 seeded prompts + 1 seeded config + 4 generated
    # schema-exports (ontology, collections, mappings, eval specs) — the
    # fixture publishes them like startup does. `model-pricing` is a document
    # because what a model call costs is governed knowledge now, so it sits in
    # the Library beside the policies rather than in a dict, and so is
    # `retrieval-authority`, which says what a document's authority is worth
    # when two of them answer the same question. The plant, field and
    # compliance policies Wick's flows read are documents on the same footing:
    # `field-documents-policy`, `certification-alert-policy`,
    # `plant-scheduling-parameters` and `labor-hour-formulas`. The one config
    # is `truss-standards` — a span/member lookup that used to be a spreadsheet
    # read fresh on every run, which is why it is governed but argues with
    # nothing.
    assert resp.json()["assets"] == 27


def test_list_and_prefix_filter(client):
    assert len(client.get("/api/v1/assets").json()) == 27
    # The kind filter carves the Library out of the corpus: documents only.
    docs = client.get("/api/v1/assets", params={"kind": "document"}).json()
    assert len(docs) == 17
    assert all(a["kind"] == "document" for a in docs)
    # And it carves out the governed prompts, which are knowledge without being
    # documents: they instruct a ModelCall rather than arguing with a policy.
    prompts = client.get("/api/v1/assets", params={"kind": "prompt"}).json()
    assert {a["id"] for a in prompts} == {
        "receipt-extraction-prompt",
        "invoice-extraction-prompt",
        "invoice-locate-prompt",
        "shipping-list-extract-prompt",
        "shipping-list-locate-prompt",
    }
    assert all(a["authority_level"] is None for a in prompts)
    finance = client.get("/api/v1/assets", params={"prefix": "finance"}).json()
    assert {a["id"] for a in finance} == {
        "expense-approval-policy",
        "travel-booking-guideline",
        "invoice-posting-policy",
        "ap-intake-policy",
        "receipt-extraction-prompt",
        "invoice-extraction-prompt",
        "invoice-locate-prompt",
    }


def test_get_asset_and_context(client):
    asset = client.get("/api/v1/assets/travel-booking-guideline").json()
    assert asset["authority_level"] == "guideline"
    ctx = client.get("/api/v1/assets/travel-booking-guideline/context").json()
    assert ctx["ancestors"][0]["id"] == "expense-approval-policy"
    assert ctx["related"][0]["id"] == "expense-approval-policy"


def test_crud_cycle(client):
    payload = {
        "id": "test-doc",
        "path": "engineering/notes/test-doc",
        "title": "Test Doc",
        "content": "A zebra-flavored unique token: quaggamatic.",
        "status": "published",
    }
    assert client.post("/api/v1/assets", json=payload).status_code == 201
    assert client.post("/api/v1/assets", json=payload).status_code == 409

    hits = client.get("/api/v1/search", params={"q": "quaggamatic"}).json()
    assert hits and hits[0]["asset"]["id"] == "test-doc"

    resp = client.put("/api/v1/assets/test-doc", json={"title": "Renamed"})
    assert resp.json()["title"] == "Renamed"

    assert client.delete("/api/v1/assets/test-doc").status_code == 204
    assert client.get("/api/v1/assets/test-doc").status_code == 404
    assert client.get("/api/v1/search", params={"q": "quaggamatic"}).json() == []


def test_search_ranks_relevant_doc(client):
    hits = client.get(
        "/api/v1/search", params={"q": "expense auto-approval limit"}
    ).json()
    assert hits[0]["asset"]["id"] == "expense-approval-policy"


def test_auth_enforced_when_firebase_backend(client, monkeypatch):
    # With firebase auth enabled every front door requires a credential; the
    # machine API key still acts as an owner credential.
    monkeypatch.setattr(settings, "auth_backend", "firebase")
    monkeypatch.setattr(settings, "admin_api_key", "sekret")
    key = {"X-API-Key": "sekret"}
    payload = {"id": "locked", "path": "x/locked", "title": "L", "content": "c"}

    assert client.post("/api/v1/assets", json=payload).status_code == 401
    assert client.post("/api/v1/assets", json=payload, headers=key).status_code == 201

    # Reads are closed too.
    for path in (
        "/api/v1/assets",
        "/api/v1/assets/locked",
        "/api/v1/assets/locked/context",
        "/api/v1/assets/tree",
        "/api/v1/search?q=locked",
    ):
        assert client.get(path).status_code == 401, path
        assert client.get(path, headers=key).status_code == 200, path


def test_a2a_requires_credential(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_backend", "firebase")
    monkeypatch.setattr(settings, "admin_api_key", "sekret")
    rpc = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {"message": {"parts": [{"kind": "text", "text": "expense"}]}},
    }
    assert client.post("/a2a", json=rpc).status_code == 401
    ok = client.post("/a2a", json=rpc, headers={"X-API-Key": "sekret"})
    assert ok.status_code == 200
    assert "Expense Approval Policy" in ok.json()["result"]["parts"][0]["text"]

    # The agent card stays public discovery metadata, and advertises the schemes.
    card = client.get("/.well-known/agent-card.json")
    assert card.status_code == 200
    assert "apiKey" in card.json()["securitySchemes"]


def test_mcp_requires_credential(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_backend", "firebase")
    monkeypatch.setattr(settings, "admin_api_key", "sekret")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

    denied = client.post("/mcp/", headers=headers, json=body)
    assert denied.status_code == 401
    assert denied.headers["www-authenticate"].startswith("Bearer")
    assert denied.json()["error"]["code"] == -32001

    allowed = client.post(
        "/mcp/", headers={**headers, "X-API-Key": "sekret"}, json=body
    )
    assert allowed.status_code == 200
    assert "search_docs" in {t["name"] for t in allowed.json()["result"]["tools"]}


def test_health_stays_public(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_backend", "firebase")
    monkeypatch.setattr(settings, "admin_api_key", "sekret")
    assert client.get("/health").status_code == 200


def test_search_does_not_let_drafts_crowd_out_published(client):
    # Regression: the index used to be truncated to limit*2 *before* the status
    # filter ran, so enough higher-scoring drafts hid published matches entirely.
    for i in range(6):
        client.post(
            "/api/v1/assets",
            json={
                "id": f"draft-{i}",
                "path": f"z/draft-{i}",
                "title": "zebra",
                "content": "zebra " * 30,
                "status": "draft",
            },
        )
    client.post(
        "/api/v1/assets",
        json={
            "id": "published-zebra",
            "path": "z/published",
            "title": "zebra",
            "content": "zebra " * 5,
            "status": "published",
        },
    )
    hits = client.get("/api/v1/search", params={"q": "zebra", "limit": 3}).json()
    assert [h["asset"]["id"] for h in hits] == ["published-zebra"]

    with_drafts = client.get(
        "/api/v1/search", params={"q": "zebra", "limit": 3, "include_drafts": "true"}
    ).json()
    assert len(with_drafts) == 3


def test_put_normalizes_path_like_post(client):
    # Regression: update() used model_copy(), which skips field validators, so
    # PUT could store a path that POST would have normalized.
    client.post(
        "/api/v1/assets",
        json={"id": "norm", "path": "a/b", "title": "T", "content": "c"},
    )
    resp = client.put("/api/v1/assets/norm", json={"path": "  /x/y/  "})
    assert resp.status_code == 200
    assert resp.json()["path"] == "x/y"

    assert client.put("/api/v1/assets/norm", json={"path": "  /  "}).status_code == 422


def test_index_updates_incrementally_on_write(client):
    payload = {
        "id": "inc",
        "path": "e/inc",
        "title": "Original",
        "content": "aardvark",
        "status": "published",
    }
    client.post("/api/v1/assets", json=payload)
    assert client.get("/api/v1/search", params={"q": "aardvark"}).json()

    client.put("/api/v1/assets/inc", json={"content": "wombat"})
    # Old terms are gone, new terms are searchable.
    assert client.get("/api/v1/search", params={"q": "aardvark"}).json() == []
    hits = client.get("/api/v1/search", params={"q": "wombat"}).json()
    assert [h["asset"]["id"] for h in hits] == ["inc"]

    client.delete("/api/v1/assets/inc")
    assert client.get("/api/v1/search", params={"q": "wombat"}).json() == []


def test_creating_a_person_and_the_role_rules_around_it(client):
    """`/users` mints the account; `/identities` does everything after that.

    The second spelling of list-change-delete that used to live on `/users`
    is gone — one directory behind two authorization policies is how the two
    come to disagree — so this walks the create half here and the manage half
    where it now lives.
    """
    # Dev mode: caller acts as owner.
    me = client.get("/api/v1/users/me").json()
    assert me["role"] == "owner"

    created = client.post(
        "/api/v1/users",
        json={"email": "ana@example.com", "password": "hunter2boat", "role": "admin"},
    )
    assert created.status_code == 201
    uid = created.json()["uid"]
    assert created.json()["role"] == "admin"

    dup = client.post(
        "/api/v1/users",
        json={"email": "ana@example.com", "password": "hunter2boat"},
    )
    assert dup.status_code == 409

    # The person is one identity among all of them, and is managed there.
    listed = client.get("/api/v1/identities").json()
    assert {"owner@localhost", "ana@example.com"} <= {i["email"] for i in listed}

    changed = client.put(f"/api/v1/identities/{uid}/role", json={"role": "user"})
    assert changed.json()["role"] == "user"

    assert client.delete(f"/api/v1/identities/{uid}").status_code == 204
    assert client.delete(f"/api/v1/identities/{uid}").status_code == 404

    # Cannot change your own role or delete yourself. Asked rather than
    # hardcoded: the signed-in uid is the acting persona's, not a literal.
    me_uid = client.get("/api/v1/users/me").json()["uid"]
    assert (
        client.put(f"/api/v1/identities/{me_uid}/role",
                   json={"role": "user"}).status_code == 400
    )
    assert client.delete(f"/api/v1/identities/{me_uid}").status_code == 400


def test_a2a_agent_card_and_message(client):
    card = client.get("/.well-known/agent-card.json").json()
    assert card["name"] == "Cumulus Knowledge Agent"
    assert card["url"].endswith("/a2a")

    rpc = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": "m1",
                "parts": [
                    {
                        "kind": "text",
                        "text": "What are the expense approval thresholds?",
                    }
                ],
            }
        },
    }
    result = client.post("/a2a", json=rpc).json()["result"]
    assert result["role"] == "agent"
    assert "Expense Approval Policy" in result["parts"][0]["text"]

    err = client.post(
        "/a2a", json={"jsonrpc": "2.0", "id": 2, "method": "tasks/nope"}
    ).json()
    assert err["error"]["code"] == -32601


def test_mcp_endpoint_tools(client):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    init = client.post(
        "/mcp/",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
    )
    assert init.status_code == 200
    assert init.json()["result"]["serverInfo"]["name"] == "cumulus-knowledge"

    tools = client.post(
        "/mcp/",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ).json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"list_docs", "get_doc", "search_docs", "get_context"} <= names

    call = client.post(
        "/mcp/",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "search_docs",
                "arguments": {"query": "incident severity"},
            },
        },
    ).json()["result"]
    assert not call.get("isError")
    assert "incident-response-standard" in str(call)


def test_parse_seed_file_frontmatter_and_defaults():
    asset = parse_seed_file(
        "expense-approval-policy.md",
        "---\npath: finance/policies/expense\nauthority_level: policy\n"
        "tags: finance, spend\n---\nBody text here.\n",
    )
    assert asset.id == "expense-approval-policy"
    assert asset.path == "finance/policies/expense"
    assert asset.authority_level == "policy"
    assert asset.tags == ["finance", "spend"]
    assert asset.status == "published"  # seed docs default to published
    assert asset.content == "Body text here."


def test_parse_seed_file_without_frontmatter():
    asset = parse_seed_file("vpn-setup-note.txt", "Just a note.")
    assert asset.id == "vpn-setup-note"
    assert asset.title == "Vpn Setup Note"
    assert asset.content_type == "text/plain"
    assert asset.content == "Just a note."


def test_revision_bumps_on_update_and_resolver_dereferences(client):
    before = client.get("/api/v1/assets/vpn-setup-note").json()
    assert before["revision"] == 1
    client.put(
        "/api/v1/assets/vpn-setup-note",
        json={"content": before["content"] + "\nrev bump"},
    )
    after = client.get("/api/v1/assets/vpn-setup-note").json()
    assert after["revision"] == 2

    # The resolver: current pin resolves cleanly...
    ok = client.get("/api/v1/refs/knowledge:vpn-setup-note@rev2").json()
    assert ok["resolved"] and ok["pinned_available"]
    # ...a stale pin is honest about missing history...
    stale = client.get("/api/v1/refs/knowledge:vpn-setup-note@rev1").json()
    assert stale["resolved"] and stale["pinned_available"] is False
    assert stale["current_revision"] == 2
    # ...namespaces without a store say so rather than pretending...
    graph = client.get("/api/v1/refs/graph:msa-master-7781@rev2").json()
    assert graph["resolved"] is False and "entity graph" in graph["note"]
    # ...and garbage is a 422, unknown knowledge a 404.
    assert client.get("/api/v1/refs/nonsense").status_code == 422
    assert (
        client.get("/api/v1/refs/knowledge:nope@rev1").status_code
        == 404
    )


# ---- the two dials: kind and origin -----------------------------------------


def test_a_generated_export_carries_no_authority_level(client):
    """Authority answers "which document wins an argument"; a generated schema
    export is not in that conversation. Carrying an authority level would be a
    category error, so the model drops it rather than defaulting it."""
    asset = client.get("/api/v1/assets/enterprise-ontology").json()
    assert asset["kind"] == "schema-export"
    assert asset["origin"] == "authored"
    assert asset["authority_level"] is None


def test_documents_default_the_dials(client):
    asset = client.get("/api/v1/assets/expense-approval-policy").json()
    assert asset["kind"] == "document"
    assert asset["origin"] == "authored"
    assert asset["authority_level"] == "policy"


def test_generated_exports_refuse_edits(client):
    """A schema-export is republished from Python on every startup; letting a
    PUT succeed would accept an edit the next boot silently discards."""
    for asset_id in ("enterprise-ontology", "source-mappings"):
        asset = client.get(f"/api/v1/assets/{asset_id}").json()
        assert asset["kind"] == "schema-export", asset_id
        r = client.put(f"/api/v1/assets/{asset_id}", json={"content": "x"})
        assert r.status_code == 409
        assert "server/app" in r.json()["detail"]
        assert client.delete(f"/api/v1/assets/{asset_id}").status_code == 409


def test_search_can_scope_by_kind(client):
    """Governed-context retrieval and generated-export listing must not cross."""
    hits = client.get(
        "/api/v1/search", params={"q": "vendor", "kind": "schema-export"}
    ).json()
    assert hits and all(h["asset"]["kind"] == "schema-export" for h in hits)
    hits = client.get("/api/v1/search", params={"q": "vendor", "kind": "document"}).json()
    assert all(h["asset"]["kind"] == "document" for h in hits)
    facets = client.get("/api/v1/search/facets", params={"q": ""}).json()
    kinds = {f["value"] for f in facets["kind"]}
    assert {"document", "schema-export"} <= kinds
