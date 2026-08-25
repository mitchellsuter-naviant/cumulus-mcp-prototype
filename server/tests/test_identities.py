"""Identities: the merged view of people and machine clients, and its rules."""

import pytest

import app.deps as deps
from app.api_keys import InMemoryApiKeyDirectory
from app.config import settings
from app.identities import IdentityService, InMemoryMachineIdentityDirectory
from app.service import KnowledgeService, seed_if_empty
from app.storage import InMemoryRepository
from app.users import InMemoryUserDirectory


@pytest.fixture()
def client(_client):
    deps._service = KnowledgeService(InMemoryRepository())
    seed_if_empty(deps._service, settings.seed_dir)
    deps._user_directory = InMemoryUserDirectory()
    deps._api_key_directory = InMemoryApiKeyDirectory()
    deps._identity_service = IdentityService(
        deps._user_directory, InMemoryMachineIdentityDirectory()
    )
    return _client


@pytest.fixture()
def owner(monkeypatch):
    monkeypatch.setattr(settings, "auth_backend", "firebase")
    monkeypatch.setattr(settings, "admin_api_key", "root-sekret")
    return {"X-API-Key": "root-sekret"}


def _agent(client, owner, **kw):
    payload = {"display_name": "Renewal Brief Agent", **kw}
    resp = client.post("/api/v1/identities", json=payload, headers=owner)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_list_merges_people_and_machines(client, owner):
    """People are not copied into the registry; they are projected from Auth."""
    client.post(
        "/api/v1/users",
        json={"email": "u@example.com", "password": "password123", "role": "admin"},
        headers=owner,
    )
    agent = _agent(client, owner, description="Assembles renewal briefs")

    identities = client.get("/api/v1/identities", headers=owner).json()
    by_kind = {}
    for identity in identities:
        by_kind.setdefault(identity["kind"], []).append(identity)

    assert {i["email"] for i in by_kind["person"]} == {"owner@localhost", "u@example.com"}
    assert [i["uid"] for i in by_kind["agent"]] == [agent["uid"]]
    assert agent["uid"].startswith("agt_")
    assert agent["description"] == "Assembles renewal briefs"
    # The person's role still comes from the user directory, not a second copy.
    assert next(i for i in by_kind["person"] if i["email"] == "u@example.com")["role"] == "admin"


def test_service_kind_gets_its_own_prefix(client, owner):
    service = _agent(client, owner, display_name="Nightly ETL", kind="service")
    assert service["uid"].startswith("svc_")
    assert service["kind"] == "service"


def test_duplicate_display_name_is_rejected(client, owner):
    _agent(client, owner)
    resp = client.post(
        "/api/v1/identities",
        json={"display_name": "Renewal Brief Agent"},
        headers=owner,
    )
    assert resp.status_code == 409


def test_admin_scoped_identities_are_owner_only(client, owner, monkeypatch):
    """Same rule as user management: granting admin authority needs an owner."""
    admin_identity = _agent(client, owner, display_name="Publisher", role="admin")

    # An admin (not owner) may not create, re-scope, or delete admin authority.
    from app.auth import Principal
    import app.identities_api as identities_api

    admin_principal = Principal(uid="admin-1", email="a@example.com", role="admin")
    _client_app = client.app
    _client_app.dependency_overrides[identities_api.get_principal] = lambda: admin_principal
    try:
        assert (
            client.post(
                "/api/v1/identities",
                json={"display_name": "Another publisher", "role": "admin"},
            ).status_code
            == 403
        )
        assert (
            client.put(
                f"/api/v1/identities/{admin_identity['uid']}/role", json={"role": "user"}
            ).status_code
            == 403
        )
        assert client.delete(f"/api/v1/identities/{admin_identity['uid']}").status_code == 403
    finally:
        _client_app.dependency_overrides.clear()


def test_reader_cannot_see_identities(client, monkeypatch):
    from app.auth import Principal
    import app.identities_api as identities_api

    reader = Principal(uid="reader", email="r@example.com", role="user")
    client.app.dependency_overrides[identities_api.get_principal] = lambda: reader
    try:
        assert client.get("/api/v1/identities").status_code == 403
    finally:
        client.app.dependency_overrides.clear()


def test_a_key_cannot_reshape_the_identity_graph(client, owner):
    """Keys hold authority; they must not be able to hand it to themselves."""
    agent = _agent(client, owner, display_name="Admin agent", role="admin")
    minted = client.post(
        "/api/v1/api-keys",
        json={"name": "client", "identity_uid": agent["uid"]},
        headers=owner,
    ).json()
    headers = {"X-API-Key": minted["token"]}

    assert client.get("/api/v1/identities", headers=headers).status_code == 200
    assert (
        client.post(
            "/api/v1/identities", json={"display_name": "Sock puppet"}, headers=headers
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/api/v1/identities/{agent['uid']}/role", json={"role": "user"}, headers=headers
        ).status_code
        == 403
    )
    assert client.delete(f"/api/v1/identities/{agent['uid']}", headers=headers).status_code == 403


def test_deleting_a_person_identity_removes_the_account(client, owner):
    person = client.post(
        "/api/v1/users",
        json={"email": "leaver@example.com", "password": "password123"},
        headers=owner,
    ).json()
    assert client.delete(f"/api/v1/identities/{person['uid']}", headers=owner).status_code == 204
    assert person["uid"] not in {u["uid"] for u in client.get("/api/v1/users", headers=owner).json()}


def test_unknown_identity_is_404(client, owner):
    assert client.delete("/api/v1/identities/agt_missing", headers=owner).status_code == 404
    assert (
        client.put(
            "/api/v1/identities/agt_missing/role", json={"role": "user"}, headers=owner
        ).status_code
        == 404
    )
