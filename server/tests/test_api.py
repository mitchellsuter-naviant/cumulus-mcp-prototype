import pytest

import app.deps as deps
from app.service import KnowledgeService, seed_if_empty
from app.storage import InMemoryRepository
from app.users import InMemoryUserDirectory
from app.config import settings
from app.service import parse_seed_file


@pytest.fixture()
def client(_client):
    deps._service = KnowledgeService(InMemoryRepository())
    seed_if_empty(deps._service, settings.seed_dir)
    deps._user_directory = InMemoryUserDirectory()
    return _client


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["assets"] == 11


def test_list_and_prefix_filter(client):
    assert len(client.get("/api/v1/assets").json()) == 11
    finance = client.get("/api/v1/assets", params={"prefix": "finance"}).json()
    assert {a["id"] for a in finance} == {
        "expense-approval-policy",
        "travel-booking-guideline",
        "invoice-posting-policy",
        "vendor-exemplar-acme-industrial",
        "vendor-exemplar-northwind-traders",
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
        "/api/v1/search", params={"q": "expense approval thresholds"}
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


def test_users_crud_and_role_rules(client):
    # Dev mode: caller acts as owner.
    me = client.get("/api/v1/users/me").json()
    assert me["role"] == "owner"

    users = client.get("/api/v1/users").json()
    assert users[0]["email"] == "owner@localhost"

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

    changed = client.put(f"/api/v1/users/{uid}/role", json={"role": "user"})
    assert changed.json()["role"] == "user"

    assert client.delete(f"/api/v1/users/{uid}").status_code == 204
    assert client.delete(f"/api/v1/users/{uid}").status_code == 404

    # Cannot change your own role or delete yourself (dev principal uid = "dev").
    assert (
        client.put("/api/v1/users/dev/role", json={"role": "user"}).status_code == 400
    )
    assert client.delete("/api/v1/users/dev").status_code == 400


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
